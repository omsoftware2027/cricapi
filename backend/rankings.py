"""30YCA-RATING-V1.

Lovable sends the stored 30YCA totals. This module ranks them.
A player has two ratings: one against every 30YCA player, and one against
the players in the same team. Teams are ranked on wins and win ratio.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from pathlib import Path

FORMULA = "30YCA-RATING-V1"
LISTS = ("batting", "bowling", "wicketkeeper", "overall")

ROOT_DIR = Path(__file__).parent
_DEFAULT_DB = ROOT_DIR / "data" / "rankings.sqlite"
_LOCK = threading.Lock()
_READY: set[str] = set()


class RankError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def db_path() -> Path:
    raw = (os.environ.get("RANKINGS_DB_PATH") or "").strip()
    return Path(raw) if raw else _DEFAULT_DB


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    key = str(path.resolve())
    if key not in _READY:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ranking_store (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        _READY.add(key)
    return conn


def _num(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number < 0:
        return 0.0
    return number


def _int(value) -> int:
    return int(_num(value))


def _is_keeper(row: dict) -> bool:
    if row.get("is_wicketkeeper") in (True, 1, "1", "true", "True"):
        return True
    text = f"{row.get('playing_role') or ''} {row.get('player_skill') or ''}".lower()
    return "wicket" in text or "keeper" in text or re.search(r"\bwk\b", text) is not None


def _blank(player_id: str, name: str, role: str, keeper: bool) -> dict:
    return {
        "player_id": player_id,
        "name": name,
        "playing_role": role,
        "is_wicketkeeper": keeper,
        "team_ids": [],
        "matches": 0,
        "innings": 0,
        "runs": 0,
        "balls": 0,
        "fours": 0,
        "sixes": 0,
        "not_outs": 0,
        "highest_score": 0,
        "fifties": 0,
        "hundreds": 0,
        "wickets": 0,
        "runs_conceded": 0,
        "balls_bowled": 0,
        "catches": 0,
        "stumpings": 0,
        "recent_innings": [],
    }


def _add_row(total: dict, row: dict) -> None:
    team_id = str(row.get("team_id") or "").strip()
    if team_id and team_id not in total["team_ids"]:
        total["team_ids"].append(team_id)
    if _is_keeper(row):
        total["is_wicketkeeper"] = True
    role = str(row.get("playing_role") or "").strip()
    if role and not total["playing_role"]:
        total["playing_role"] = role
    name = str(row.get("name") or "").strip()
    if name and len(name) > len(total["name"]):
        total["name"] = name
    for key in (
        "matches", "innings", "runs", "balls", "fours", "sixes", "not_outs",
        "fifties", "hundreds", "wickets", "runs_conceded", "balls_bowled",
        "catches", "stumpings",
    ):
        total[key] += _int(row.get(key))
    total["highest_score"] = max(total["highest_score"], _int(row.get("highest_score")))
    recent = [ _int(item) for item in (row.get("recent_innings") or []) ][-5:]
    if len(recent) > len(total["recent_innings"]):
        total["recent_innings"] = recent


def _finish(player: dict) -> dict:
    dismissals = max(player["innings"] - player["not_outs"], 0)
    player["dismissals"] = dismissals
    player["average"] = (player["runs"] / dismissals) if dismissals else float(player["runs"])
    player["strike_rate"] = (player["runs"] / player["balls"] * 100) if player["balls"] else 0.0
    player["runs_per_innings"] = (player["runs"] / player["innings"]) if player["innings"] else 0.0
    player["boundary_rate"] = ((player["fours"] + player["sixes"]) / player["balls"]) if player["balls"] else 0.0
    overs = player["balls_bowled"] / 6 if player["balls_bowled"] else 0.0
    player["overs"] = f"{player['balls_bowled'] // 6}.{player['balls_bowled'] % 6}"
    player["economy"] = (player["runs_conceded"] / overs) if overs else None
    player["bowling_average"] = (
        player["runs_conceded"] / player["wickets"] if player["wickets"] else None
    )
    player["keeping_dismissals"] = player["catches"] + player["stumpings"]
    return player


def _one(player_id: str, row: dict) -> dict:
    total = _blank(
        player_id,
        str(row.get("name") or "").strip(),
        str(row.get("playing_role") or "").strip(),
        _is_keeper(row),
    )
    _add_row(total, row)
    return _finish(total)


def _collapse(players: list) -> tuple[list, dict]:
    grouped: dict[str, list] = {}
    for raw in players or []:
        if not isinstance(raw, dict):
            continue
        player_id = str(raw.get("player_id") or "").strip()
        if not player_id:
            continue
        grouped.setdefault(player_id, []).append(raw)
    career = []
    by_team: dict[str, list] = {}
    for player_id, rows in grouped.items():
        team_rows = [row for row in rows if str(row.get("team_id") or "").strip()]
        chosen = team_rows or rows
        total = _blank(player_id, "", "", False)
        for row in chosen:
            _add_row(total, row)
        career.append(_finish(total))
        for row in team_rows:
            team_id = str(row.get("team_id") or "").strip()
            by_team.setdefault(team_id, []).append(_one(player_id, row))
    return career, by_team


def _percentile(value: float, values: list) -> float:
    count = len(values)
    if count <= 1:
        return 100.0
    if max(values) == min(values):
        return 50.0
    below = sum(1 for item in values if item < value)
    return below / (count - 1) * 100


def _shrink(percentile: float, sample: float, prior: float) -> float:
    factor = sample / (sample + prior) if sample + prior else 0
    return 50 + (percentile - 50) * factor


def _rating(percentile: float, sample: float, prior: float) -> int:
    score = round(_shrink(percentile, sample, prior) * 10)
    return max(0, min(1000, score))


def _batting_score(player: dict, pool: list) -> int:
    def values(key):
        return [item[key] for item in pool]

    mixed = (
        0.40 * _percentile(player["average"], values("average"))
        + 0.25 * _percentile(player["strike_rate"], values("strike_rate"))
        + 0.25 * _percentile(player["runs_per_innings"], values("runs_per_innings"))
        + 0.10 * _percentile(player["boundary_rate"], values("boundary_rate"))
    )
    return _rating(mixed, player["innings"], 8)


def _bowling_score(player: dict, pool: list) -> int:
    economies = [item["economy"] if item["economy"] is not None else 99 for item in pool]
    averages = [item["bowling_average"] if item["bowling_average"] is not None else 99 for item in pool]
    economy = player["economy"] if player["economy"] is not None else 99
    average = player["bowling_average"] if player["bowling_average"] is not None else 99
    mixed = (
        0.45 * _percentile(player["wickets"], [item["wickets"] for item in pool])
        + 0.30 * _percentile(-economy, [-item for item in economies])
        + 0.25 * _percentile(-average, [-item for item in averages])
    )
    return _rating(mixed, player["balls_bowled"], 90)


def _keeper_score(player: dict, pool: list) -> int:
    dismissals = [item["keeping_dismissals"] for item in pool]
    use_keeping = any(item > 0 for item in dismissals)
    batting = _batting_score(player, pool) / 10
    if use_keeping:
        keeping = _percentile(player["keeping_dismissals"], dismissals)
        mixed = 0.60 * batting + 0.40 * keeping
    else:
        mixed = batting
    return _rating(mixed, max(player["matches"], 1), 3)


def _component_map(players: list, predicate, scorer) -> dict:
    pool = [player for player in players if predicate(player)]
    return {player["player_id"]: scorer(player, pool) for player in pool}


def _impact(player: dict) -> float:
    return player["hundreds"] * 2 + player["fifties"] + (player["highest_score"] / 50)


def _form_rating(player: dict, pool: list) -> int:
    def recent(item):
        innings = item["recent_innings"]
        if not innings:
            return None
        return sum(innings) / len(innings)

    known = [recent(item) for item in pool if recent(item) is not None]
    mine = recent(player)
    if mine is None or not known:
        return 500
    return _rating(_percentile(mine, known), len(player["recent_innings"]), 3)


def _fielding_rating(player: dict, pool: list) -> int | None:
    if not any(item["keeping_dismissals"] for item in pool):
        return None
    percentile = _percentile(player["keeping_dismissals"], [item["keeping_dismissals"] for item in pool])
    return _rating(percentile, player["matches"], 5)


def _overall_rows(players: list) -> list:
    pool = [player for player in players if player["matches"] >= 5 and (player["innings"] >= 5 or player["balls_bowled"] >= 60)]
    if not pool:
        return []
    batting = _component_map(pool, lambda item: item["innings"] >= 5, _batting_score)
    bowling = _component_map(pool, lambda item: item["balls_bowled"] >= 60, _bowling_score)
    fielding = {player["player_id"]: _fielding_rating(player, pool) for player in pool}
    impacts = [_impact(player) for player in pool]
    ratings = {}
    for player in pool:
        parts = []
        if player["player_id"] in batting:
            parts.append((0.60, batting[player["player_id"]]))
        if player["player_id"] in bowling:
            parts.append((0.20, bowling[player["player_id"]]))
        field = fielding.get(player["player_id"])
        if field is not None:
            parts.append((0.10, field))
        elif player["player_id"] in batting:
            parts.append((0.10, batting[player["player_id"]]))
        parts.append((0.05, _form_rating(player, pool)))
        parts.append((0.05, _rating(_percentile(_impact(player), impacts), player["matches"], 5)))
        weight = sum(item[0] for item in parts)
        ratings[player["player_id"]] = int(round(sum(item[0] * item[1] for item in parts) / weight))
    ordered = sorted(pool, key=lambda player: (-ratings[player["player_id"]], -player["runs"], player["name"].lower()))
    rows = []
    last_rating = None
    last_rank = 0
    for index, player in enumerate(ordered, start=1):
        rating = ratings[player["player_id"]]
        rank = last_rank if rating == last_rating else index
        last_rating = rating
        last_rank = rank
        row = _public(player, rating, rank, "overall")
        row["batting_rating"] = batting.get(player["player_id"])
        row["bowling_rating"] = bowling.get(player["player_id"])
        row["overall_rating"] = rating
        rows.append(row)
    return rows


def _public(player: dict, rating: int, rank: int, list_name: str) -> dict:
    return {
        "rank": rank,
        "player_id": player["player_id"],
        "name": player["name"],
        "team_ids": list(player["team_ids"]),
        "playing_role": player["playing_role"],
        "is_wicketkeeper": player["is_wicketkeeper"],
        "matches": player["matches"],
        "innings": player["innings"],
        "runs": player["runs"],
        "balls": player["balls"],
        "average": round(player["average"], 2),
        "strike_rate": round(player["strike_rate"], 2),
        "fifties": player["fifties"],
        "hundreds": player["hundreds"],
        "highest_score": player["highest_score"],
        "wickets": player["wickets"],
        "runs_conceded": player["runs_conceded"],
        "balls_bowled": player["balls_bowled"],
        "overs": player["overs"],
        "economy": None if player["economy"] is None else round(player["economy"], 2),
        "bowling_average": None if player["bowling_average"] is None else round(player["bowling_average"], 2),
        "catches": player["catches"],
        "stumpings": player["stumpings"],
        "rating": rating,
        "list": list_name,
    }


def _lists_for(players: list) -> dict:
    return {
        "batting": _list_batting(players),
        "bowling": _list_bowling(players),
        "wicketkeeper": _list_keeper(players),
        "overall": _overall_rows(players),
    }


def _list_batting(players: list) -> list:
    pool = [player for player in players if player["innings"] >= 5]
    return _finish_list(pool, _batting_score, "batting")


def _list_bowling(players: list) -> list:
    pool = [player for player in players if player["balls_bowled"] >= 60]
    return _finish_list(pool, _bowling_score, "bowling")


def _list_keeper(players: list) -> list:
    pool = [player for player in players if player["is_wicketkeeper"] and player["matches"] >= 3]
    return _finish_list(pool, _keeper_score, "wicketkeeper")


def _finish_list(pool: list, scorer, list_name: str) -> list:
    if not pool:
        return []
    ratings = {player["player_id"]: scorer(player, pool) for player in pool}
    ordered = sorted(
        pool,
        key=lambda player: (-ratings[player["player_id"]], player["name"].lower(), player["player_id"]),
    )
    rows = []
    last_rating = None
    last_rank = 0
    for index, player in enumerate(ordered, start=1):
        rating = ratings[player["player_id"]]
        rank = last_rank if rating == last_rating else index
        last_rating = rating
        last_rank = rank
        rows.append(_public(player, rating, rank, list_name))
    return rows


def _team_rows(teams: list) -> list:
    cleaned = []
    for raw in teams or []:
        if not isinstance(raw, dict):
            continue
        team_id = str(raw.get("team_id") or "").strip()
        played = _int(raw.get("played"))
        won = _int(raw.get("won"))
        if not team_id or played < 3:
            continue
        won = min(won, played)
        cleaned.append({
            "team_id": team_id,
            "name": str(raw.get("name") or "").strip(),
            "played": played,
            "won": won,
            "lost": _int(raw.get("lost")),
            "win_ratio": won / played,
        })
    if not cleaned:
        return []
    ratings = {}
    wins = [item["won"] for item in cleaned]
    ratios = [item["win_ratio"] for item in cleaned]
    for team in cleaned:
        mixed = (
            0.60 * _percentile(team["won"], wins)
            + 0.40 * _percentile(team["win_ratio"], ratios)
        )
        ratings[team["team_id"]] = _rating(mixed, team["played"], 3)
    ordered = sorted(cleaned, key=lambda team: (-ratings[team["team_id"]], -team["won"], team["name"].lower()))
    rows = []
    last_rating = None
    last_rank = 0
    for index, team in enumerate(ordered, start=1):
        rating = ratings[team["team_id"]]
        rank = last_rank if rating == last_rating else index
        last_rating = rating
        last_rank = rank
        rows.append({
            "rank": rank,
            "team_id": team["team_id"],
            "name": team["name"],
            "played": team["played"],
            "won": team["won"],
            "lost": team["lost"],
            "win_ratio": round(team["win_ratio"], 3),
            "rating": rating,
        })
    return rows


def _stamp(rows: list, scope: str, team_id: str = "") -> list:
    stamped = []
    for row in rows:
        item = dict(row)
        item["scope"] = scope
        item["team_id"] = team_id
        stamped.append(item)
    return stamped


def build_rankings(players: list, teams: list) -> dict:
    """Rank the supplied 30YCA totals. Rows for the same player and team are summed."""
    career, by_team = _collapse(players)
    players_30yca = {name: _stamp(rows, "30yca") for name, rows in _lists_for(career).items()}
    players_by_team = {}
    for team_id, members in by_team.items():
        players_by_team[team_id] = {
            name: _stamp(rows, "team", team_id) for name, rows in _lists_for(members).items()
        }
    return {
        "formula": FORMULA,
        "players_30yca": players_30yca,
        "teams": _team_rows(teams),
        "players_by_team": players_by_team,
        "notes": {
            "batting": "At least 5 innings. Average 40, strike rate 25, runs per innings 25, boundary rate 10.",
            "bowling": "At least 60 balls bowled. Wickets 45, economy 30, bowling average 25.",
            "wicketkeeper": "Wicketkeepers with at least 3 matches. Batting 60, catches and stumpings 40.",
            "overall": "At least 5 matches. Batting 60, bowling 20, fielding 10, form 5, impact 5.",
            "team": "At least 3 completed matches. Wins 60, win ratio 40.",
            "scopes": "scope 30yca ranks the player against every 30YCA player. scope team ranks the player only against that team.",
        },
    }


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def save_rankings(players: list, teams: list) -> dict:
    if len(players or []) > 20000:
        raise RankError("Send at most 20000 player rows")
    if len(teams or []) > 5000:
        raise RankError("Send at most 5000 team rows")
    built = build_rankings(players, teams)
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO ranking_store (id, payload, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at
                """,
                (json.dumps(built), _now()),
            )
            conn.commit()
        finally:
            conn.close()
    return {
        "formula": FORMULA,
        "saved": True,
        "players": {
            name: len(built["players_30yca"][name]) for name in LISTS
        },
        "teams": len(built["teams"]),
        "team_player_lists": len(built["players_by_team"]),
    }


def _load() -> dict:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute("SELECT payload, updated_at FROM ranking_store WHERE id=1").fetchone()
        finally:
            conn.close()
    if row is None:
        raise RankError("Rankings have not been built. POST /api/rankings/rebuild first.", status_code=404)
    payload = json.loads(row["payload"])
    payload["updated_at"] = row["updated_at"]
    return payload


def _slice(rows: list, limit: int, offset: int) -> dict:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = 0
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    page = rows[offset:offset + limit]
    return {"total": len(rows), "offset": offset, "limit": limit, "rows": page}


def player_rankings(list_name: str, scope: str = "30yca", team_id: str = "", limit: int = 100, offset: int = 0) -> dict:
    list_name = (list_name or "overall").strip().lower()
    if list_name not in LISTS:
        raise RankError("list must be batting, bowling, wicketkeeper, or overall")
    scope = (scope or "30yca").strip().lower()
    stored = _load()
    if scope == "team":
        team_id = str(team_id or "").strip()
        if not team_id:
            raise RankError("team_id is required when scope is team")
        rows = (stored["players_by_team"].get(team_id) or {}).get(list_name) or []
    elif scope == "30yca":
        rows = stored["players_30yca"].get(list_name) or []
        team_id = ""
    else:
        raise RankError("scope must be 30yca or team")
    page = _slice(rows, limit, offset)
    return {
        "formula": FORMULA,
        "list": list_name,
        "scope": scope,
        "team_id": team_id,
        "updated_at": stored.get("updated_at") or "",
        "note": stored["notes"][list_name],
        **page,
    }


def team_rankings(limit: int = 100, offset: int = 0) -> dict:
    stored = _load()
    page = _slice(stored["teams"], limit, offset)
    return {
        "formula": FORMULA,
        "updated_at": stored.get("updated_at") or "",
        "note": stored["notes"]["team"],
        **page,
    }

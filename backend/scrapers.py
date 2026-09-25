"""
Cricket scorecard scrapers for multiple sites.
Supports: Cricbuzz, ESPN Cricinfo, Cricheroes (via Bright Data residential proxy)
"""
from __future__ import annotations
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from urllib.parse import urlparse

import requests as _requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cc_requests

from config import (
    brightdata_proxy_url,
    CRICHEROES_API_BASE,
    CRICHEROES_API_KEY,
    CRICHEROES_UDID,
    CRICHEROES_UA,
)


class ScrapeError(Exception):
    pass


class CloudflareBlocked(ScrapeError):
    pass


def _fetch_html(url: str) -> str:
    try:
        r = cc_requests.get(url, impersonate="chrome", timeout=25)
    except Exception as e:
        raise ScrapeError(f"Network error while fetching URL: {e}")

    if r.status_code == 403 or (
        "Just a moment" in r.text or "cf-error-details" in r.text or "Attention Required" in r.text
    ):
        raise CloudflareBlocked(
            "The target site is protected by Cloudflare and blocks server requests. "
            "Please try a Cricbuzz or ESPN Cricinfo scorecard URL instead."
        )
    if r.status_code >= 400:
        raise ScrapeError(f"HTTP {r.status_code} returned by target site.")
    return r.text


def detect_source(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "cricbuzz" in host:
        return "cricbuzz"
    if "cricinfo" in host or "espncricinfo" in host:
        return "cricinfo"
    if "cricheroes" in host:
        return "cricheroes"
    return "unknown"


def scrape(url: str) -> dict:
    src = detect_source(url)
    if src == "cricheroes":
        tournament_id = _cricheroes_tournament_id(url)
        if tournament_id:
            return scrape_tournament(tournament_id)
        # CricHeroes has its own API path; no HTML fetch needed
        return _scrape_cricheroes(url)
    html = _fetch_html(url)
    if src == "cricbuzz":
        return _scrape_cricbuzz(html, url)
    if src == "cricinfo":
        return _scrape_cricinfo(html, url)
    raise ScrapeError(
        "Unsupported site. Please paste a Cricbuzz, ESPN Cricinfo or CricHeroes scorecard URL."
    )


# --------------------------- CRICHEROES ---------------------------

_CRICHEROES_MATCH_RE = re.compile(r"/scorecard/(\d+)")
_CRICHEROES_TOURNAMENT_RE = re.compile(r"/tournament/(\d+)")

# Team match history is paged. Stop walking a team once the tournament is
# fully collected, or after this many pages, whichever comes first.
_TEAM_MATCH_PAGE_CAP = 40
_DEFAULT_SCORECARD_LIMIT = 100
_MAX_SCORECARD_LIMIT = 200


def _cricheroes_match_id(url: str) -> Optional[str]:
    m = _CRICHEROES_MATCH_RE.search(url)
    return m.group(1) if m else None


def _cricheroes_tournament_id(url: str) -> Optional[str]:
    m = _CRICHEROES_TOURNAMENT_RE.search(url or "")
    return m.group(1) if m else None


def _cricheroes_headers() -> dict:
    return {
        "User-Agent": CRICHEROES_UA,
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://cricheroes.com",
        "Referer": "https://cricheroes.com/",
        "api-key": CRICHEROES_API_KEY,
        "udid": CRICHEROES_UDID,
        "device-type": "Chrome: 128.0.0.0",
    }


def _cricheroes_api_get(path: str) -> dict:
    """GET a CricHeroes JSON route. Uses the Bright Data proxy when configured,
    then falls back to a direct request if the proxy is missing or rejects this host.
    """
    api_url = path if path.startswith("http") else f"{CRICHEROES_API_BASE}{path if path.startswith('/') else '/' + path}"
    headers = _cricheroes_headers()
    attempts = []
    proxy = brightdata_proxy_url()
    if proxy:
        attempts.append({"proxies": {"http": proxy, "https": proxy}, "verify": False})
    attempts.append({})

    last_error: Optional[Exception] = None
    for extra in attempts:
        try:
            r = _requests.get(api_url, headers=headers, timeout=90, **extra)
        except Exception as e:
            last_error = e
            continue
        if r.status_code != 200:
            last_error = ScrapeError(f"CricHeroes API returned HTTP {r.status_code}.")
            continue
        try:
            payload = r.json()
        except ValueError:
            last_error = ScrapeError("CricHeroes API returned invalid JSON.")
            continue
        if not isinstance(payload, dict):
            last_error = ScrapeError("CricHeroes API returned an unexpected payload.")
            continue
        return payload

    if isinstance(last_error, ScrapeError):
        raise last_error
    if not proxy:
        raise ScrapeError(
            "CricHeroes is Cloudflare-protected. A Bright Data residential proxy is required. "
            "Please configure BRIGHTDATA_PROXY_USER and BRIGHTDATA_PROXY_PASS on the server."
        )
    raise ScrapeError(f"Failed to reach CricHeroes via proxy: {last_error}")


def _scrape_cricheroes(url: str) -> dict:
    match_id = _cricheroes_match_id(url)
    if not match_id:
        raise ScrapeError(
            "Could not extract the match ID from that CricHeroes URL. "
            "It should look like https://cricheroes.com/scorecard/<match_id>/..."
        )

    payload = _cricheroes_api_get(f"/scorecard/get-scorecard/{match_id}")
    if not payload.get("status"):
        err = (payload.get("error") or {}).get("message") or "Unknown error"
        raise ScrapeError(f"CricHeroes API error: {err}")

    d = payload["data"]

    # Match meta
    team_a = d.get("team_a") or {}
    team_b = d.get("team_b") or {}
    match_title = f"{team_a.get('name','')} vs {team_b.get('name','')}"
    tour = d.get("tournament_name") or ""
    if tour:
        match_title = f"{match_title}, {tour}"
    venue_parts = [d.get("ground_name") or "", d.get("city_name") or ""]
    venue = ", ".join([p for p in venue_parts if p])
    toss = d.get("toss_details") or ""
    result = (d.get("match_summary") or {}).get("summary") or d.get("match_result") or ""

    # Innings — merge each team's scorecard entries and sort by inning number
    innings_list = []
    for team in (team_a, team_b):
        team_name = team.get("name") or ""
        for sc in team.get("scorecard") or []:
            inn_num = sc.get("inning") or 0
            # Match up header info from team.innings by inning number
            inn_meta = {}
            for inn in team.get("innings") or []:
                if inn.get("inning") == inn_num:
                    inn_meta = inn
                    break

            total_run = inn_meta.get("total_run", "")
            total_wicket = inn_meta.get("total_wicket", "")
            overs_played = inn_meta.get("overs_played", "")
            total = f"{total_run}/{total_wicket}" if total_run != "" else ""

            # Batting
            batting_rows = []
            for b in sc.get("batting") or []:
                batting_rows.append({
                    "player_id": str(b.get("player_id", "")),
                    "batter": (b.get("name") or "").strip(),
                    "dismissal": (b.get("how_to_out") or "").strip(),
                    "runs": str(b.get("runs", "")),
                    "balls": str(b.get("balls", "")),
                    "fours": str(b.get("4s", "")),
                    "sixes": str(b.get("6s", "")),
                    "sr": str(b.get("SR", "")),
                })

            # Bowling
            bowling_rows = []
            for bw in sc.get("bowling") or []:
                overs = bw.get("overs", "")
                balls = bw.get("balls", "")
                # cricheroes stores overs and balls separately; overs like 3, balls like 2 => "3.2"
                if balls:
                    overs_str = f"{overs}.{balls}"
                else:
                    overs_str = str(overs)
                bowling_rows.append({
                    "player_id": str(bw.get("player_id", "")),
                    "bowler": (bw.get("name") or "").strip(),
                    "overs": overs_str,
                    "maidens": str(bw.get("maidens", "")),
                    "runs": str(bw.get("runs", "")),
                    "wickets": str(bw.get("wickets", "")),
                    "no_balls": str(bw.get("noball", "")),
                    "wides": str(bw.get("wide", "")),
                    "econ": str(bw.get("economy_rate", "")),
                })

            # Extras summary text
            extras_obj = sc.get("extras") or {}
            extras_str = ""
            if extras_obj:
                extras_str = f"Extras {extras_obj.get('total','')} {extras_obj.get('summary','')}".strip()

            total_line = ""
            if total_run != "":
                total_line = f"Total {total_run}/{total_wicket} ({overs_played} Overs)"

            # Yet to bat — cricheroes returns list of {player_id, name}
            dnb = sc.get("to_be_bat") or []
            yet_to_bat_list = []
            dnb_str = ""
            if isinstance(dnb, list) and dnb:
                for x in dnb:
                    if not isinstance(x, dict):
                        continue
                    pid = str(x.get("player_id") or "")
                    nm = (x.get("name") or "").strip()
                    if nm or pid:
                        yet_to_bat_list.append({"player_id": pid, "name": nm})
                names_only = [it["name"] for it in yet_to_bat_list if it["name"]]
                if names_only:
                    dnb_str = "Yet to bat: " + ", ".join(names_only)

            # Fall of wickets - cricheroes has a summary string ready to use
            fow_obj = sc.get("fall_of_wicket") or {}
            fow_str = ""
            if isinstance(fow_obj, dict):
                summary = fow_obj.get("summary")
                if summary:
                    fow_str = f"Fall of Wickets: {summary}"

            innings_list.append({
                "innings_number": int(inn_num) if inn_num else len(innings_list) + 1,
                "team": team_name,
                "total": total,
                "overs": str(overs_played),
                "score_header": f"{team_name} {total} ({overs_played} Ov)".strip(),
                "batting": batting_rows,
                "bowling": bowling_rows,
                "extras": extras_str,
                "total_line": total_line,
                "did_not_bat": dnb_str,
                "yet_to_bat": yet_to_bat_list,
                "fall_of_wickets": fow_str,
            })

    innings_list.sort(key=lambda x: x["innings_number"])

    if not innings_list:
        raise ScrapeError("CricHeroes returned no innings data for this match.")

    return {
        "source": "cricheroes",
        "url": url,
        "match_title": match_title.strip(", "),
        "result": result,
        "venue": venue,
        "toss": toss,
        "innings": innings_list,
    }


# --------------------------- CRICHEROES TOURNAMENT ---------------------------

def _api_error_message(payload: dict, fallback: str) -> str:
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])
    return fallback


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _tournament_summary(detail: dict) -> dict:
    grounds = []
    for g in _as_list(detail.get("grounds")):
        if not isinstance(g, dict):
            continue
        grounds.append({
            "ground_id": str(g.get("ground_id") or ""),
            "ground_name": g.get("ground_name") or "",
            "city_name": g.get("city_name") or "",
        })
    return {
        "tournament_id": str(detail.get("tournament_id") or ""),
        "name": detail.get("name") or "",
        "city": detail.get("city_name") or "",
        "from_date": detail.get("from_date") or "",
        "to_date": detail.get("to_date") or "",
        "ball_type": detail.get("ball_type") or "",
        "category": detail.get("tournament_category") or detail.get("category") or "",
        "tournament_type": detail.get("tournament_type") or "",
        "match_count": int(detail.get("match_count") or 0),
        "live_matches_count": int(detail.get("live_matches_count") or 0),
        "past_matches_count": int(detail.get("past_matches_count") or 0),
        "upcoming_matches_count": int(detail.get("upcoming_matches_count") or 0),
        "logo": detail.get("tournament_logo") or detail.get("logo") or "",
        "share_url": detail.get("share_url") or "",
        "grounds": grounds,
    }


def _team_summary(team: dict) -> dict:
    return {
        "team_id": str(team.get("team_id") or ""),
        "team_name": team.get("team_name") or "",
        "city_name": team.get("city_name") or "",
        "logo": team.get("logo") or "",
    }


def _standing_rows(payload: dict) -> list:
    rows = []
    for group in _as_list(payload.get("data")):
        if not isinstance(group, dict):
            continue
        group_name = group.get("group") or ""
        for row in _as_list(group.get("standing")):
            if not isinstance(row, dict):
                continue
            rows.append({
                "group": row.get("group") or group_name,
                "round_name": row.get("round_name") or "",
                "team_id": str(row.get("team_id") or ""),
                "team_name": row.get("team_name") or "",
                "matches": row.get("matches", ""),
                "won": row.get("won", ""),
                "lost": row.get("lost", ""),
                "drawn": row.get("drawn", ""),
                "tied": row.get("tied", ""),
                "no_result": row.get("no_result", ""),
                "points": row.get("points", ""),
                "net_rr": row.get("net_rr", ""),
                "for": row.get("for") or "",
                "against": row.get("against") or "",
                "last_5": row.get("last_5") or "",
            })
    return rows


def _match_summary(raw: dict) -> dict:
    venue_parts = [raw.get("ground_name") or "", raw.get("city_name") or ""]
    scores = [s for s in (raw.get("team_a_summary"), raw.get("team_b_summary")) if s]
    return {
        "match_id": str(raw.get("match_id") or ""),
        "status": raw.get("status") or "",
        "match_type": raw.get("match_type") or "",
        "ball_type": raw.get("ball_type") or "",
        "overs": raw.get("overs") if raw.get("overs") is not None else "",
        "team_a_id": str(raw.get("team_a_id") or ""),
        "team_a": raw.get("team_a") or "",
        "team_b_id": str(raw.get("team_b_id") or ""),
        "team_b": raw.get("team_b") or "",
        "team_a_summary": raw.get("team_a_summary") or "",
        "team_b_summary": raw.get("team_b_summary") or "",
        "scores": scores,
        "result": raw.get("win_by") or raw.get("match_result") or "",
        "winning_team": raw.get("winning_team") or "",
        "round": raw.get("tournament_round_name") or "",
        "venue": ", ".join([p for p in venue_parts if p]),
        "start_time": raw.get("match_start_time") or "",
        "url": f"https://cricheroes.com/scorecard/{raw.get('match_id')}/individual/match/live",
    }


def _page_is_before_tournament(matches: list, from_date: str) -> bool:
    if not from_date:
        return False
    times = [m.get("match_start_time") or "" for m in matches if isinstance(m, dict)]
    times = [t for t in times if t]
    if not times:
        return False
    return max(times) < from_date


def _iter_team_matches(team_id: str, tournament_id: str, from_date: str, needed: int, found: dict):
    path = f"/team/get-team-match/{team_id}"
    for _ in range(_TEAM_MATCH_PAGE_CAP):
        if needed and len(found) >= needed:
            return
        payload = _cricheroes_api_get(path)
        if not payload.get("status"):
            return
        page_matches = [m for m in _as_list(payload.get("data")) if isinstance(m, dict)]
        for raw in page_matches:
            if str(raw.get("tournament_id") or "") != str(tournament_id):
                continue
            mid = str(raw.get("match_id") or "")
            if mid and mid not in found:
                found[mid] = _match_summary(raw)
        nxt = ""
        page = payload.get("page")
        if isinstance(page, dict):
            nxt = page.get("next") or ""
        if not nxt or not page_matches or _page_is_before_tournament(page_matches, from_date):
            return
        path = nxt if nxt.startswith("/") else "/" + nxt


def _collect_tournament_matches(tournament_id: str, teams: list, from_date: str, match_count: int) -> dict:
    found: dict = {}
    team_ids = []
    for team in teams:
        tid = str(team.get("team_id") or "")
        if tid and tid not in team_ids:
            team_ids.append(tid)

    def _one(team_id: str):
        local: dict = {}
        _iter_team_matches(team_id, tournament_id, from_date, match_count, local)
        return local

    # Walk teams together, then merge. A second pass is unnecessary: each
    # worker filters to this tournament and we dedupe by match id.
    if team_ids:
        workers = min(4, len(team_ids))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_one, tid) for tid in team_ids]
            for fut in as_completed(futures):
                for mid, summary in fut.result().items():
                    found.setdefault(mid, summary)
                    if match_count and len(found) >= match_count:
                        break
    return found


def scrape_tournament(
    tournament_id: str,
    *,
    include_scorecards: bool = True,
    scorecard_limit: int = _DEFAULT_SCORECARD_LIMIT,
) -> dict:
    """Scrape a whole CricHeroes tournament: profile, teams, points table, and every match."""
    tournament_id = str(tournament_id or "").strip()
    if not tournament_id.isdigit():
        raise ScrapeError("tournament_id must be numeric")

    try:
        limit = int(scorecard_limit)
    except (TypeError, ValueError):
        limit = _DEFAULT_SCORECARD_LIMIT
    limit = max(0, min(limit, _MAX_SCORECARD_LIMIT))

    detail_payload = _cricheroes_api_get(f"/tournament/get-tournament-detail/{tournament_id}")
    if not detail_payload.get("status") or not isinstance(detail_payload.get("data"), dict):
        raise ScrapeError(_api_error_message(detail_payload, "CricHeroes returned no tournament for that id."))
    detail = detail_payload["data"]
    tournament = _tournament_summary(detail)
    tournament["tournament_id"] = tournament["tournament_id"] or tournament_id

    teams_payload = _cricheroes_api_get(f"/tournament/get-tournament-teams/{tournament_id}")
    raw_teams = _as_list(teams_payload.get("data")) if teams_payload.get("status") else []
    if not raw_teams:
        raw_teams = _as_list(detail.get("teams"))
    teams = [_team_summary(t) for t in raw_teams if isinstance(t, dict) and t.get("team_id")]

    standing_payload = _cricheroes_api_get(f"/tournament/get-tournament-standing/{tournament_id}")
    standings = _standing_rows(standing_payload) if standing_payload.get("status") else []

    found = _collect_tournament_matches(
        tournament_id,
        teams,
        tournament.get("from_date") or "",
        tournament.get("match_count") or 0,
    )
    matches = sorted(found.values(), key=lambda m: (m.get("start_time") or "", int(m["match_id"] or 0)))

    scorecards_included = 0
    scorecard_failures = 0
    truncated = False
    if include_scorecards and limit > 0:
        targets = matches[:limit]
        truncated = len(matches) > len(targets)

        def _one_card(match: dict):
            try:
                card = _scrape_cricheroes(match["url"])
                return match["match_id"], card, None
            except Exception as e:
                return match["match_id"], None, str(e)

        cards = {}
        errors = {}
        if targets:
            workers = min(4, len(targets))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for mid, card, err in pool.map(_one_card, targets):
                    if card is not None:
                        cards[mid] = card
                    else:
                        errors[mid] = err
        for match in matches:
            if match["match_id"] in cards:
                match["scorecard"] = cards[match["match_id"]]
                match["ok"] = True
                scorecards_included += 1
            elif match["match_id"] in errors:
                match["ok"] = False
                match["error"] = errors[match["match_id"]]
                scorecard_failures += 1

    expected = tournament.get("match_count") or 0
    return {
        "source": "cricheroes",
        "kind": "tournament",
        "url": tournament.get("share_url") or f"https://cricheroes.com/tournament/{tournament_id}",
        "tournament_id": tournament_id,
        "tournament": tournament,
        "teams": teams,
        "standings": standings,
        "matches": matches,
        "total_matches": len(matches),
        "expected_matches": expected,
        "incomplete": bool(expected and len(matches) < expected),
        "scorecards_included": scorecards_included,
        "scorecard_failures": scorecard_failures,
        "scorecards_truncated": truncated,
    }


# --------------------------- CRICBUZZ ---------------------------

def _scrape_cricbuzz(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    title_el = soup.select_one("h1")
    match_title = title_el.get_text(" ", strip=True) if title_el else ""
    # Cricbuzz duplicates title; keep the second half after ' - Scorecard'
    if " - Scorecard" in match_title:
        match_title = match_title.split(" - Scorecard")[0]
    match_title = re.sub(r"\s+", " ", match_title).strip()

    # Match result / status
    result = ""
    status_el = soup.select_one('[class*="cb-scrcrd-status"], [class*="cbSuccess"], [class*="cb-text-complete"], [class*="cb-text-live"]')
    if status_el:
        result = status_el.get_text(" ", strip=True)
    if not result:
        # try headings containing "won" / "match"
        for h in soup.select("div, span"):
            t = h.get_text(" ", strip=True)
            if 5 < len(t) < 160 and re.search(r"\bwon by\b|match tied|match drawn|no result", t, re.I):
                result = t
                break

    # Innings sections: id="scard-team-{X}-innings-{Y}"
    innings_sections = soup.select('[id^="scard-team-"][id*="innings-"]')
    # header (score/overs) is in id="team-{X}-innings-{Y}"
    innings_list = []
    for sec in innings_sections:
        sid = sec.get("id", "")
        # e.g. scard-team-43-innings-1
        m = re.match(r"scard-team-(\d+)-innings-(\d+)", sid)
        if not m:
            continue
        team_id, inn_num = m.group(1), m.group(2)
        header = soup.select_one(f"#team-{team_id}-innings-{inn_num}")
        header_text = header.get_text(" ", strip=True) if header else f"Innings {inn_num}"

        # Extract team name and score from header like "SLA 1st Innings Sri Lanka A 1st Innings 366-10 (110 Ov)"
        team_name = ""
        total = ""
        overs = ""
        if header_text:
            # Match score-overs like "366-10 (110 Ov)" — require the digits followed by ( or Ov
            score_m = re.search(r"(\d+[-/]\d+)\s*\((\d+(?:\.\d+)?)\s*Ov", header_text)
            if score_m:
                total = score_m.group(1)
                overs = score_m.group(2)
            else:
                score_m2 = re.search(r"(\d+[-/]\d+)", header_text)
                if score_m2:
                    total = score_m2.group(1)
            # Team name is before "1st/2nd Innings" (the human name after abbreviation)
            name_m = re.search(r"\d(?:st|nd|rd|th)\s+Innings\s+(.+?)\s+\d(?:st|nd|rd|th)\s+Innings", header_text)
            if name_m:
                team_name = name_m.group(1).strip()
            else:
                team_name = header_text.split(" ")[0]

        batting_rows = []
        # rows: div class contains "scorecard-bat-grid" but not the header row (which has "Batter" first)
        bat_grid_rows = sec.select('div.grid.scorecard-bat-grid, div[class*="scorecard-bat-grid"]')
        for row in bat_grid_rows:
            children = row.find_all(recursive=False)
            if not children:
                continue
            first_text = children[0].get_text(" ", strip=True)
            if first_text.lower().startswith("batter"):
                continue
            # children[0] is a div containing name + dismissal
            name_block = children[0]
            name_el = name_block.find(["a", "span"])
            name = name_el.get_text(" ", strip=True) if name_el else name_block.get_text(" ", strip=True).split("\n")[0]
            # dismissal is remaining text
            full_text = name_block.get_text(" ", strip=True)
            dismissal = full_text.replace(name, "", 1).strip()
            # numeric columns are the remaining children (skip trailing empty)
            nums = [c.get_text(" ", strip=True) for c in children[1:] if c.get_text(" ", strip=True) != ""]
            padded = (nums + [""] * 5)[:5]
            batting_rows.append({
                "batter": name,
                "dismissal": dismissal,
                "runs": padded[0],
                "balls": padded[1],
                "fours": padded[2],
                "sixes": padded[3],
                "sr": padded[4],
            })

        # Extras / Total / Did not bat / FOW - find divs starting with these labels
        extras = ""
        total_line = ""
        did_not_bat = ""
        fall_of_wickets = ""

        for div in sec.select("div"):
            t = div.get_text(" ", strip=True)
            if not t or len(t) > 800:
                continue
            lo = t.lower()
            if lo.startswith("extras") and not extras and len(t) < 200:
                extras = t
            elif lo.startswith("total") and not total_line and len(t) < 200:
                total_line = t
            elif (lo.startswith("did not bat") or lo.startswith("yet to bat")) and not did_not_bat:
                did_not_bat = t
            elif lo.startswith("fall of wickets") and not fall_of_wickets:
                fall_of_wickets = t
            if extras and total_line and did_not_bat and fall_of_wickets:
                break

        # Bowling rows
        bowling_rows = []
        bowl_grid_rows = sec.select('div.grid.scorecard-bowl-grid, div[class*="scorecard-bowl-grid"]')
        for row in bowl_grid_rows:
            children = row.find_all(recursive=False)
            if not children:
                continue
            first_text = children[0].get_text(" ", strip=True)
            if first_text.lower().startswith("bowler"):
                continue
            # Layout: [<a>Name</a>, O, M, R, W, NB, WD, ECON, <a>optional trailing]
            name = first_text
            nums = [c.get_text(" ", strip=True) for c in children[1:] if c.get_text(" ", strip=True) != ""]
            padded = (nums + [""] * 7)[:7]
            bowling_rows.append({
                "bowler": name,
                "overs": padded[0],
                "maidens": padded[1],
                "runs": padded[2],
                "wickets": padded[3],
                "no_balls": padded[4],
                "wides": padded[5],
                "econ": padded[6],
            })

        innings_list.append({
            "innings_number": int(inn_num),
            "team": team_name or f"Team {team_id}",
            "total": total,
            "overs": overs,
            "score_header": header_text,
            "batting": batting_rows,
            "bowling": bowling_rows,
            "extras": extras,
            "total_line": total_line,
            "did_not_bat": did_not_bat,
            "fall_of_wickets": fall_of_wickets,
        })

    if not innings_list:
        raise ScrapeError("Could not parse the Cricbuzz scorecard structure.")

    # Venue / toss / format from other meta blocks
    venue = ""
    toss = ""
    for row in soup.select("div"):
        t = row.get_text(" ", strip=True)
        if t.startswith("Venue") and not venue and len(t) < 200:
            venue = t.replace("Venue", "").strip(" :")
        elif t.startswith("Toss") and not toss and len(t) < 200:
            toss = t.replace("Toss", "").strip(" :")
        if venue and toss:
            break

    return {
        "source": "cricbuzz",
        "url": url,
        "match_title": match_title,
        "result": result,
        "venue": venue,
        "toss": toss,
        "innings": innings_list,
    }


# --------------------------- CRICINFO ---------------------------

def _scrape_cricinfo(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    title = soup.select_one("h1")
    match_title = title.get_text(" ", strip=True) if title else ""

    # Result
    result = ""
    status_el = soup.select_one('[class*="ds-text-tight-l"], [class*="ds-text-title"], [class*="header-info"]')
    for el in soup.select('span, p, div'):
        t = el.get_text(" ", strip=True)
        if 5 < len(t) < 200 and re.search(r"\bwon by\b|match tied|match drawn|no result", t, re.I):
            result = t
            break

    # Innings blocks: usually inside <div class="ds-rounded-lg"> that contain tables
    innings_list = []
    # ESPN cricinfo uses tables with class "ds-w-full ds-table ds-table-md ds-table-auto"
    tables = soup.select("table")
    # Group tables in pairs (batting, bowling)
    # But we need innings headers. Try finding parent sections with headers
    inning_headers = soup.select('[class*="ds-text-title-xs"], span.ds-text-title-xs, .ci-team-scores')
    # Simpler: iterate all sections with data-testid
    sections = soup.select('[class*="ci-scorecard"], [class*="scorecard"]')
    # Fallback: parse tables in order

    # Find all "1st Innings", "2nd Innings" headings
    innings_titles = []
    for h in soup.select("span, h5, h3, div"):
        t = h.get_text(" ", strip=True)
        if re.match(r"^[A-Z].{1,40}\s+(1st|2nd|3rd|4th) Innings$", t) and t not in innings_titles:
            innings_titles.append(t)

    # For each innings title, find the next 2 tables (batting + bowling)
    idx = 0
    all_tables = soup.select("table")
    # separate by inspecting header cells
    for it_title in innings_titles:
        team_name = re.sub(r"\s+(1st|2nd|3rd|4th) Innings$", "", it_title)
        inn_num = 1
        m = re.search(r"(1st|2nd|3rd|4th) Innings", it_title)
        if m:
            inn_num = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4}[m.group(1)]

        batting_rows = []
        bowling_rows = []
        # take next 2 tables from all_tables (batting then bowling)
        chosen = all_tables[idx: idx + 2]
        idx += 2
        for tbl in chosen:
            headers = [th.get_text(" ", strip=True).lower() for th in tbl.select("thead th, thead td")]
            if not headers:
                # inspect first row
                first_tr = tbl.select_one("tr")
                headers = [c.get_text(" ", strip=True).lower() for c in first_tr.find_all(["th", "td"])] if first_tr else []
            is_bowling = any(h in ("o", "overs") for h in headers)
            for tr in tbl.select("tbody tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if not cells or len(cells) < 3:
                    continue
                if is_bowling:
                    # Bowler, O, M, R, W, ECON, 0s, 4s, 6s, WD, NB (varies)
                    padded = (cells + [""] * 11)[:11]
                    bowling_rows.append({
                        "bowler": padded[0],
                        "overs": padded[1],
                        "maidens": padded[2],
                        "runs": padded[3],
                        "wickets": padded[4],
                        "econ": padded[5],
                        "no_balls": padded[9] if len(cells) > 9 else "",
                        "wides": padded[10] if len(cells) > 10 else "",
                    })
                else:
                    # Batting: Batter, Dismissal, R, B, M, 4s, 6s, SR
                    padded = (cells + [""] * 8)[:8]
                    batting_rows.append({
                        "batter": padded[0],
                        "dismissal": padded[1],
                        "runs": padded[2],
                        "balls": padded[3],
                        "fours": padded[5],
                        "sixes": padded[6],
                        "sr": padded[7],
                    })

        innings_list.append({
            "innings_number": inn_num,
            "team": team_name,
            "total": "",
            "overs": "",
            "score_header": it_title,
            "batting": batting_rows,
            "bowling": bowling_rows,
            "extras": "",
            "total_line": "",
            "did_not_bat": "",
            "fall_of_wickets": "",
        })

    if not innings_list:
        raise ScrapeError("Could not parse the ESPN Cricinfo scorecard. The page structure may have changed.")

    return {
        "source": "cricinfo",
        "url": url,
        "match_title": match_title,
        "result": result,
        "venue": "",
        "toss": "",
        "innings": innings_list,
    }


# --------------------------- CSV export ---------------------------

def scorecard_to_csv(sc: dict) -> str:
    """Serialize scorecard to a single CSV string."""
    import csv, io

    buf = io.StringIO()
    w = csv.writer(buf)

    # Match info
    w.writerow(["MATCH INFO"])
    w.writerow(["Title", sc.get("match_title", "")])
    w.writerow(["Source", sc.get("source", "")])
    w.writerow(["URL", sc.get("url", "")])
    w.writerow(["Venue", sc.get("venue", "")])
    w.writerow(["Toss", sc.get("toss", "")])
    w.writerow(["Result", sc.get("result", "")])
    w.writerow([])

    for inn in sc.get("innings", []):
        w.writerow([f"INNINGS {inn.get('innings_number','')} - {inn.get('team','')}"])
        w.writerow(["Score", inn.get("total", ""), "Overs", inn.get("overs", "")])
        w.writerow([])

        # Batting
        w.writerow(["Batting"])
        w.writerow(["CricHeroes Player ID", "Batter", "Dismissal", "R", "B", "4s", "6s", "SR"])
        for b in inn.get("batting", []):
            w.writerow([
                b.get("player_id", ""), b.get("batter", ""), b.get("dismissal", ""), b.get("runs", ""),
                b.get("balls", ""), b.get("fours", ""), b.get("sixes", ""), b.get("sr", "")
            ])
        if inn.get("extras"):
            w.writerow([inn["extras"]])
        if inn.get("total_line"):
            w.writerow([inn["total_line"]])
        if inn.get("fall_of_wickets"):
            w.writerow([inn["fall_of_wickets"]])
        # Yet to bat — structured with Player ID (cricheroes) or fallback text (other sources)
        ytb = inn.get("yet_to_bat") or []
        if ytb:
            w.writerow([])
            w.writerow(["Yet to Bat"])
            w.writerow(["CricHeroes Player ID", "Name"])
            for p in ytb:
                w.writerow([p.get("player_id", ""), p.get("name", "")])
        elif inn.get("did_not_bat"):
            w.writerow([inn["did_not_bat"]])
        w.writerow([])

        # Bowling
        w.writerow(["Bowling"])
        w.writerow(["CricHeroes Player ID", "Bowler", "O", "M", "R", "W", "NB", "WD", "ECON"])
        for bw in inn.get("bowling", []):
            w.writerow([
                bw.get("player_id", ""), bw.get("bowler", ""), bw.get("overs", ""), bw.get("maidens", ""),
                bw.get("runs", ""), bw.get("wickets", ""), bw.get("no_balls", ""),
                bw.get("wides", ""), bw.get("econ", "")
            ])
        w.writerow([])
        w.writerow([])

    return buf.getvalue()


def tournament_to_csv(tournament_scrape: dict) -> str:
    """One CSV for a tournament: profile, teams, points table, then every match scorecard."""
    import csv, io

    buf = io.StringIO()
    w = csv.writer(buf)
    tour = tournament_scrape.get("tournament") or {}

    w.writerow(["TOURNAMENT"])
    w.writerow(["Tournament ID", tournament_scrape.get("tournament_id", "")])
    w.writerow(["Name", tour.get("name", "")])
    w.writerow(["City", tour.get("city", "")])
    w.writerow(["From", tour.get("from_date", "")])
    w.writerow(["To", tour.get("to_date", "")])
    w.writerow(["Ball", tour.get("ball_type", "")])
    w.writerow(["Matches found", tournament_scrape.get("total_matches", "")])
    w.writerow(["URL", tournament_scrape.get("url", "")])
    w.writerow([])

    w.writerow(["TEAMS"])
    w.writerow(["Team ID", "Team", "City"])
    for team in tournament_scrape.get("teams") or []:
        w.writerow([team.get("team_id", ""), team.get("team_name", ""), team.get("city_name", "")])
    w.writerow([])

    w.writerow(["POINTS TABLE"])
    w.writerow(["Group", "Round", "Team ID", "Team", "P", "W", "L", "D", "T", "NR", "Pts", "NRR", "For", "Against"])
    for row in tournament_scrape.get("standings") or []:
        w.writerow([
            row.get("group", ""), row.get("round_name", ""), row.get("team_id", ""), row.get("team_name", ""),
            row.get("matches", ""), row.get("won", ""), row.get("lost", ""), row.get("drawn", ""),
            row.get("tied", ""), row.get("no_result", ""), row.get("points", ""), row.get("net_rr", ""),
            row.get("for", ""), row.get("against", ""),
        ])
    w.writerow([])

    w.writerow(["MATCHES"])
    w.writerow(["Match ID", "Status", "Round", "Team A", "Team B", "Score A", "Score B", "Result", "Venue", "Start"])
    for match in tournament_scrape.get("matches") or []:
        w.writerow([
            match.get("match_id", ""), match.get("status", ""), match.get("round", ""),
            match.get("team_a", ""), match.get("team_b", ""),
            match.get("team_a_summary", ""), match.get("team_b_summary", ""),
            match.get("result", ""), match.get("venue", ""), match.get("start_time", ""),
        ])
    w.writerow([])

    for match in tournament_scrape.get("matches") or []:
        card = match.get("scorecard")
        if not card:
            if match.get("error"):
                w.writerow([f"MATCH {match.get('match_id', '')} SCORECARD ERROR", match.get("error")])
                w.writerow([])
            continue
        w.writerow([f"MATCH {match.get('match_id', '')}"])
        # Reuse the single-scorecard serializer, dropping its trailing blanks is unnecessary.
        text = scorecard_to_csv(card).strip("\n")
        for line in text.splitlines():
            buf.write(line + "\n")
        w.writerow([])

    return buf.getvalue()

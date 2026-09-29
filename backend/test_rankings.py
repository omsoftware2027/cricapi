"""30YCA ratings come from this API, for the whole pool and for one team."""
import pytest
from fastapi.testclient import TestClient

import rankings
from rankings import build_rankings
from server import API_AUTH_TOKEN, app


def _batter(**overrides):
    row = {
        "player_id": "1",
        "name": "Pankaj Chopade",
        "team_id": "10",
        "playing_role": "Top-order batter",
        "matches": 80,
        "innings": 80,
        "runs": 1862,
        "balls": 1100,
        "fours": 180,
        "sixes": 40,
        "not_outs": 5,
        "highest_score": 92,
        "fifties": 8,
        "hundreds": 0,
    }
    row.update(overrides)
    return row


def _bowler(**overrides):
    row = {
        "player_id": "2",
        "name": "Suraj Shinde",
        "team_id": "10",
        "playing_role": "Bowler",
        "matches": 101,
        "innings": 40,
        "runs": 200,
        "balls": 180,
        "wickets": 121,
        "runs_conceded": 2494,
        "balls_bowled": 2178,
    }
    row.update(overrides)
    return row


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("RANKINGS_DB_PATH", str(tmp_path / "rankings.sqlite"))
    rankings._READY.clear()
    test_client = TestClient(app)
    if API_AUTH_TOKEN:
        test_client.headers["Authorization"] = f"Bearer {API_AUTH_TOKEN}"
    return test_client


def test_short_careers_are_left_off_the_batting_list():
    built = build_rankings([
        _batter(),
        _batter(player_id="9", name="Mayank Tiwari", team_id="11", matches=2, innings=2, runs=8, balls=5, not_outs=1, fours=1, sixes=0, fifties=0, highest_score=6),
    ], [])
    batting = built["players_30yca"]["batting"]
    assert [row["name"] for row in batting] == ["Pankaj Chopade"]
    assert batting[0]["scope"] == "30yca"
    assert batting[0]["rating"] > 0


def test_bowling_and_wicketkeeper_lists_use_their_own_ratings():
    built = build_rankings([
        _bowler(),
        _bowler(player_id="3", name="Short Spell", team_id="11", matches=4, innings=2, runs=10, balls=8, wickets=3, runs_conceded=20, balls_bowled=18),
        _batter(player_id="4", name="Keeper One", team_id="10", playing_role="Wicketkeeper Batter", matches=12, innings=10, runs=280, balls=220, catches=8, stumpings=4),
        _batter(player_id="5", name="Keeper Two", team_id="11", playing_role="Wicketkeeper", matches=6, innings=5, runs=40, balls=50, catches=1, stumpings=0, fours=2, sixes=0, fifties=0, highest_score=15),
    ], [])
    bowling = built["players_30yca"]["bowling"]
    assert [row["name"] for row in bowling] == ["Suraj Shinde"]
    keepers = [row["name"] for row in built["players_30yca"]["wicketkeeper"]]
    assert keepers[0] == "Keeper One"
    assert "Suraj Shinde" not in keepers
    assert "Pankaj Chopade" not in keepers


def test_team_with_more_wins_ranks_above_a_short_perfect_record():
    built = build_rankings([], [
        {"team_id": "1", "name": "SJSF MEN", "played": 8, "won": 8},
        {"team_id": "2", "name": "Shankaracharya", "played": 61, "won": 48},
        {"team_id": "3", "name": "Vedic Fit", "played": 27, "won": 25},
        {"team_id": "4", "name": "One Tournament", "played": 2, "won": 2},
    ])
    names = [row["name"] for row in built["teams"]]
    assert "One Tournament" not in names
    assert names.index("Shankaracharya") < names.index("SJSF MEN")
    assert names.index("Vedic Fit") < names.index("SJSF MEN")


def test_team_scope_ranks_only_that_teams_players():
    built = build_rankings([
        _batter(player_id="1", name="Star", team_id="10", innings=20, matches=20, runs=600, balls=400),
        _batter(player_id="2", name="Support", team_id="10", innings=10, matches=10, runs=150, balls=180, fours=10, sixes=1, fifties=0, highest_score=30),
        _batter(player_id="3", name="Other Side", team_id="11", innings=30, matches=30, runs=900, balls=700),
    ], [])
    team = [row["name"] for row in built["players_by_team"]["10"]["batting"]]
    assert team[0] == "Star"
    assert "Other Side" not in team
    whole = [row["name"] for row in built["players_30yca"]["batting"]]
    assert "Other Side" in whole
    assert built["players_by_team"]["10"]["batting"][0]["scope"] == "team"


def test_last_five_innings_are_returned_as_form():
    built = build_rankings([
        _batter(recent_innings=[
            {"runs": 12, "balls": 9, "not_out": False, "opponent": "Lions", "match_id": "m1", "match_date": "2026-01-01"},
            {"runs": 4, "balls": 6, "not_out": True, "opponent": "Tigers", "match_id": "m2"},
            {"runs": 33, "balls": 21, "not_out": False, "match_id": "m3"},
        ]),
    ], [])
    form = built["players_30yca"]["batting"][0]["form"]
    assert form["text"] == "12, 4*, 33"
    assert form["scores"] == ["12", "4*", "33"]
    assert form["runs"] == 49
    assert form["innings"][1]["opponent"] == "Tigers"
    card = built["player_cards"]["1"]
    assert card["form"]["text"] == "12, 4*, 33"
    assert card["ratings_30yca"]["batting"]["rank"] == 1


def test_http_rebuild_then_get(client):
    missing = client.get("/api/rankings/players", params={"list": "batting"})
    assert missing.status_code == 404

    saved = client.post("/api/rankings/rebuild", json={
        "players": [
            _batter(),
            _batter(player_id="8", name="Tiny", team_id="10", matches=1, innings=1, runs=12, balls=8, not_outs=0, fours=1, sixes=0, fifties=0, highest_score=12),
        ],
        "teams": [
            {"team_id": "10", "name": "Lions", "played": 8, "won": 5},
        ],
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["players"]["batting"] == 1

    batting = client.get("/api/rankings/players", params={"list": "batting", "scope": "30yca"})
    assert batting.status_code == 200
    assert batting.json()["rows"][0]["name"] == "Pankaj Chopade"
    assert batting.json()["rows"][0]["form"]["text"] == ""

    card = client.get("/api/rankings/players/1")
    assert card.status_code == 200
    assert card.json()["form"]["scores"] == []
    assert card.json()["ratings_30yca"]["batting"]["rank"] == 1
    assert client.get("/api/rankings/players/missing").status_code == 404

    inside = client.get("/api/rankings/teams/10/players", params={"list": "batting"})
    assert inside.status_code == 200
    assert inside.json()["scope"] == "team"
    assert inside.json()["rows"][0]["player_id"] == "1"

    teams = client.get("/api/rankings/teams")
    assert teams.json()["rows"][0]["name"] == "Lions"
    assert teams.json()["rows"][0]["rating"] > 0

    with_form = client.post("/api/rankings/rebuild", json={
        "players": [_batter(recent_innings=[
            {"runs": 18, "balls": 14, "not_out": False, "match_id": "a"},
            {"runs": 7, "balls": 5, "not_out": True, "match_id": "b"},
        ])],
        "teams": [],
    })
    assert with_form.status_code == 200, with_form.text
    profile = client.get("/api/rankings/players/1")
    assert profile.status_code == 200
    assert profile.json()["form"]["text"] == "18, 7*"

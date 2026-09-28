"""Tournament scrape collects every match id, not just one scorecard."""
import pytest
from fastapi.testclient import TestClient

import scrapers
from scrapers import ScrapeError, scrape_tournament, tournament_to_csv


DETAIL = {
    "status": True,
    "data": {
        "tournament_id": 1,
        "name": "Digicorp Cricket League 2016",
        "city_name": "Ahmedabad",
        "from_date": "2016-10-30T18:30:00.000Z",
        "to_date": "2016-11-14T18:29:59.000Z",
        "ball_type": "TENNIS",
        "match_count": 2,
        "live_matches_count": 0,
        "past_matches_count": 2,
        "upcoming_matches_count": 0,
        "tournament_logo": "https://example.test/logo.png",
        "share_url": "https://cricheroes.in/tournament/1/Digicorp",
        "organizer_contact_number": "9999999999",
        "organizer_email": "secret@example.test",
        "grounds": [{"ground_id": 1, "ground_name": "Main Ground", "city_name": "Ahmedabad"}],
        "teams": [],
    },
}

TEAMS = {
    "status": True,
    "data": [
        {"team_id": 10, "team_name": "Lions", "city_name": "Ahmedabad", "logo": ""},
        {"team_id": 11, "team_name": "Tigers", "city_name": "Ahmedabad", "logo": ""},
    ],
}

STANDING = {
    "status": True,
    "data": [{
        "group": "Group A",
        "standing": [{
            "group": "Group A",
            "round_name": "Group Stage",
            "team_id": 10,
            "team_name": "Lions",
            "matches": 1,
            "won": 1,
            "lost": 0,
            "drawn": 0,
            "tied": 0,
            "no_result": 0,
            "points": 2,
            "net_rr": "1.200",
            "for": "100/1",
            "against": "80/2",
            "last_5": "W",
        }],
    }],
}


def _match(mid, team_id, tournament_id="1", start="2016-11-08T02:26:00.000Z"):
    other = "Tigers" if team_id == 10 else "Lions"
    return {
        "match_id": mid,
        "status": "past",
        "match_type": "Limited Overs",
        "ball_type": "TENNIS",
        "overs": 14,
        "team_a_id": 10,
        "team_a": "Lions",
        "team_b_id": 11,
        "team_b": other if False else "Tigers",
        "team_a_summary": "100/1",
        "team_b_summary": "80/2",
        "win_by": "20 runs",
        "winning_team": "Lions",
        "tournament_id": tournament_id,
        "tournament_round_name": "Final" if mid == 119 else "Group Stage",
        "ground_name": "Main Ground",
        "city_name": "Ahmedabad",
        "match_start_time": start,
    }


def _route(path):
    if path.startswith("/tournament/get-tournament-detail/"):
        return DETAIL
    if path.startswith("/tournament/get-tournament-teams/"):
        return TEAMS
    if path.startswith("/tournament/get-tournament-standing/"):
        return STANDING
    if path.startswith("/team/get-team-match/10"):
        if "pageno=2" in path:
            return {"status": True, "page": {}, "data": [_match(200, 10, tournament_id="", start="2016-09-01T00:00:00.000Z")]}
        return {
            "status": True,
            "page": {"next": "/team/get-team-match/10?pageno=2&datetime=1"},
            "data": [
                _match(119, 10),
                _match(500, 10, tournament_id="999", start="2016-12-01T00:00:00.000Z"),
            ],
        }
    if path.startswith("/team/get-team-match/11"):
        return {
            "status": True,
            "page": {},
            "data": [_match(119, 11), _match(112, 11, start="2016-11-01T00:00:00.000Z")],
        }
    if path.startswith("/player/get-player-profile-info/"):
        pid = path.rstrip("/").split("/")[-1]
        return {"status": True, "data": {
            "player_id": int(pid),
            "name": "Nitin Chouhan",
            "short_name": "",
            "profile_photo": "https://example.test/nitin.jpg",
            "city_name": "Pune",
            "batting_hand": "RHB",
            "bowling_style": "Right-arm fast",
            "playing_role": "Top-order batter",
            "player_skill": "",
            "batter_category": "Hard Hitter",
            "bowler_category": "Economist",
            "age": "34 years",
            "dob": "1992-01-06",
            "played_match_count": 307,
            "email": "hidden@example.test",
        }}
    if path.startswith("/scorecard/get-commentary/"):
        return {"status": True, "data": {"commentary": [{
            "inning": 1, "ball": "0.1", "run": 4, "extra_run": 0, "extra_type_code": "",
            "is_boundry": 1, "is_out": 0, "out_how": "", "dismiss_type": "",
            "dismiss_player_id": 0, "team_id": 10,
            "commentary": "A to B, 4 runs",
        }]}}
    if path.startswith("/match/get-match-official/"):
        return {"status": True, "data": [{
            "match_official_id": 9,
            "match_official_user_id": 8,
            "match_service_type_name": "Scorer",
            "name": "Official One",
            "profile_photo": "https://example.test/o.jpg",
            "city_name": "Ahmedabad",
            "is_certified": 1,
        }]}
    if path.startswith("/scorecard/get-scorecard/"):
        mid = path.rstrip("/").split("/")[-1]
        return {
            "status": True,
            "data": {
                "team_a": {"name": "Lions", "scorecard": [{
                    "inning": 1,
                    "batting": [{"player_id": 1, "name": "A", "how_to_out": "not out", "runs": 10, "balls": 8, "4s": 1, "6s": 0, "SR": "125"}],
                    "bowling": [],
                    "extras": {"total": 2, "summary": "(wd 2)"},
                    "to_be_bat": [],
                    "fall_of_wicket": {},
                }], "innings": [{"inning": 1, "total_run": 10, "total_wicket": 0, "overs_played": "2.0"}]},
                "team_b": {"name": "Tigers", "scorecard": [], "innings": []},
                "tournament_name": "Digicorp",
                "ground_name": "Main Ground",
                "city_name": "Ahmedabad",
                "toss_details": "Lions won the toss",
                "match_result": "Lions won",
                "match_summary": {"summary": "Lions won by 20 runs"},
            },
        }
    raise AssertionError(f"unexpected path {path}")


@pytest.fixture
def api(monkeypatch):
    def fake_get(path):
        return _route(path)
    monkeypatch.setattr(scrapers, "_cricheroes_api_get", fake_get)


def test_tournament_dedupes_matches_and_skips_other_tournaments(api):
    result = scrape_tournament("1", include_scorecards=False)
    ids = [m["match_id"] for m in result["matches"]]
    assert ids == ["112", "119"]
    assert result["total_matches"] == 2
    assert result["incomplete"] is False
    assert result["tournament"]["name"] == "Digicorp Cricket League 2016"
    assert "organizer_contact_number" not in result["tournament"]
    assert result["teams"][0]["team_name"] == "Lions"
    assert result["standings"][0]["points"] == 2
    assert result["matches"][1]["round"] == "Final"


def test_tournament_scorecards_attached(api):
    result = scrape_tournament("1", include_scorecards=True, scorecard_limit=10)
    assert result["scorecards_included"] == 2
    assert result["scorecard_failures"] == 0
    card = result["matches"][0]["scorecard"]
    assert card["source"] == "cricheroes"
    assert card["innings"][0]["batting"][0]["player_id"] == "1"
    assert card["innings"][0]["batting"][0]["batter"] == "A"
    assert result["matches"][0]["commentary"][0]["text"] == "A to B, 4 runs"
    assert result["matches"][0]["officials"][0]["role"] == "Scorer"
    assert result["players"][0]["player_id"] == "1"
    assert result["players"][0]["batting_hand"] == "RHB"
    assert result["players"][0]["profile_photo"].endswith("nitin.jpg")
    assert "email" not in result["players"][0]
    csv_text = tournament_to_csv(result)
    assert "TOURNAMENT" in csv_text
    assert "POINTS TABLE" in csv_text
    assert "112" in csv_text
    assert "9999999999" not in csv_text


def test_player_profile_url(api):
    profile = scrapers.scrape_player("9869683")
    assert profile["name"] == "Nitin Chouhan"
    assert profile["bowling_style"] == "Right-arm fast"
    assert profile["profile_url"] == "https://cricheroes.com/player-profile/9869683/Nitin-Chouhan"
    assert "email" not in profile


def test_bad_tournament_id():
    with pytest.raises(ScrapeError):
        scrape_tournament("abc")


def test_missing_tournament(api, monkeypatch):
    monkeypatch.setattr(scrapers, "_cricheroes_api_get", lambda path: {"status": False, "error": {"message": "Error in getting data."}})
    with pytest.raises(ScrapeError, match="Error in getting data"):
        scrape_tournament("404")


def test_tournament_url_dispatches(api):
    result = scrapers.scrape("https://cricheroes.com/tournament/1/Digicorp-Cricket-League-2016/matches")
    assert result["kind"] == "tournament"
    assert result["total_matches"] == 2


def test_http_tournament_endpoint(api):
    from server import app
    client = TestClient(app)
    res = client.get("/api/cricheroes/tournament/1?include_scorecards=false")
    assert res.status_code == 200
    body = res.json()
    assert body["total_matches"] == 2
    assert "scorecard" not in body["matches"][0]
    bad = client.get("/api/cricheroes/tournament/nope")
    assert bad.status_code == 400
    csv_res = client.get("/api/cricheroes/tournament/1/csv?include_scorecards=false")
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers["content-type"]
    assert "MATCHES" in csv_res.text

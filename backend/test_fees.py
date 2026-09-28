"""Admin match fees: team SPOC, per-match amount, and WhatsApp payment message."""
import pytest
from fastapi.testclient import TestClient

import fees
from server import API_AUTH_TOKEN, app


UPCOMING = {
    "match_id": "501",
    "status": "upcoming",
    "team_a_id": "10",
    "team_a": "Lions",
    "team_b_id": "11",
    "team_b": "Tigers",
    "start_time": "2026-10-18T09:00:00",
}

PAST = {
    "match_id": "502",
    "status": "past",
    "team_a_id": "10",
    "team_a": "Lions",
    "team_b_id": "11",
    "team_b": "Tigers",
    "start_time": "2026-09-01T09:00:00",
}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FEE_DB_PATH", str(tmp_path / "fees.sqlite"))
    fees._READY.clear()
    test_client = TestClient(app)
    if API_AUTH_TOKEN:
        test_client.headers["Authorization"] = f"Bearer {API_AUTH_TOKEN}"
    return test_client


def _spoc(client, team_id="10", name="Asha Rao", phone="9876543210", team_name="Lions"):
    response = client.put(f"/api/admin/teams/{team_id}/spoc", json={
        "spoc_name": name,
        "phone": phone,
        "team_name": team_name,
    })
    assert response.status_code == 200, response.text
    return response.json()


def _fee(client):
    response = client.put("/api/admin/tournaments/2189985/match-fee", json={
        "amount": 2500,
        "currency": "INR",
        "payment_details": "UPI: 30yca@upi",
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_spoc_phone_is_stored_with_country_code(client):
    saved = _spoc(client)
    assert saved["phone"] == "919876543210"
    assert saved["phone_display"] == "+919876543210"
    assert saved["spoc_name"] == "Asha Rao"
    listed = client.get("/api/admin/spocs", params={"tournament_id": "2189985"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


def test_tournament_spoc_overrides_the_team_default(client):
    _spoc(client, name="Default SPOC")
    response = client.put("/api/admin/teams/10/spoc", json={
        "spoc_name": "Cup SPOC",
        "phone": "9811111111",
        "team_name": "Lions",
        "tournament_id": "2189985",
    })
    assert response.status_code == 200
    resolved = client.get("/api/admin/teams/10/spoc", params={"tournament_id": "2189985"})
    assert resolved.json()["spoc_name"] == "Cup SPOC"
    default = client.get("/api/admin/teams/10/spoc")
    assert default.json()["spoc_name"] == "Default SPOC"


def test_upcoming_match_includes_the_tournament_fee(client):
    _spoc(client)
    _spoc(client, team_id="11", name="Ravi Shah", phone="9822222222", team_name="Tigers")
    _fee(client)
    preview = client.post("/api/admin/tournaments/2189985/fees/preview", json={"matches": [UPCOMING, PAST]})
    assert preview.status_code == 200
    body = preview.json()
    assert body["match_fee"]["amount"] == "2500.00"
    upcoming = body["matches"][0]
    assert upcoming["teams"][0]["status"] == "included"
    assert upcoming["teams"][0]["amount"] == "2500.00"
    assert upcoming["teams"][0]["spoc"]["spoc_name"] == "Asha Rao"
    assert upcoming["teams"][1]["spoc"]["phone"] == "919822222222"

    applied = client.post("/api/admin/tournaments/2189985/match-fee/apply", json={"matches": [UPCOMING, PAST]})
    assert applied.status_code == 200
    assert applied.json()["attached"] == 2
    saved = client.get("/api/admin/matches/501/fees")
    assert len(saved.json()["fees"]) == 2
    assert client.get("/api/admin/matches/502/fees").json()["fees"] == []


def test_notify_sends_whatsapp_with_amount_and_payment_details(client, monkeypatch):
    _spoc(client)
    _fee(client)
    client.post("/api/admin/tournaments/2189985/match-fee/apply", json={"matches": [UPCOMING]})
    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"messages": [{"id": "wamid.501"}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        return FakeResponse()

    monkeypatch.setenv("WHATSAPP_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "555")
    monkeypatch.setattr(fees.requests, "post", fake_post)

    sent = client.post("/api/admin/matches/501/fees/notify", json={
        "team_id": "10",
        "amount": 3000,
        "match_title": "Lions vs Tigers",
        "match_date": "18 Oct 2026",
        "match_status": "past",
    })
    assert sent.status_code == 200, sent.text
    result = sent.json()["results"][0]
    assert result["sent"] is True
    assert result["amount"] == "3000.00"
    assert "UPI: 30yca@upi" in result["message"]
    assert "Asha Rao" in result["message"]
    assert calls[0]["json"]["to"] == "919876543210"
    assert calls[0]["json"]["type"] == "text"
    assert calls[0]["headers"]["Authorization"] == "Bearer test-token"

    again = client.post("/api/admin/matches/501/fees/notify", json={"team_id": "10", "match_status": "past"})
    assert again.status_code == 200
    assert again.json()["results"][0]["already_notified"] is True
    assert len(calls) == 1

    paid = client.post("/api/admin/matches/501/fees/10/paid")
    assert paid.status_code == 200
    assert paid.json()["status"] == "paid"


def test_notify_uses_template_parameters_when_configured(client, monkeypatch):
    _spoc(client, team_id="11", name="Ravi Shah", phone="9822222222", team_name="Tigers")
    saved = client.put("/api/admin/matches/501/fees", json={
        "tournament_id": "2189985",
        "match_title": "Lions vs Tigers",
        "match_date": "18 Oct 2026",
        "payment_details": "UPI: 30yca@upi",
        "teams": [{
            "team_id": "11",
            "team_name": "Tigers",
            "opponent_name": "Lions",
            "amount": 2500,
        }],
    })
    assert saved.status_code == 200
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"messages": [{"id": "wamid.template"}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setenv("WHATSAPP_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "555")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_NAME", "match_fee_due")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_LANG", "en")
    monkeypatch.setattr(fees.requests, "post", fake_post)

    response = client.post("/api/admin/matches/501/fees/notify", json={"team_id": "11", "match_status": "completed"})
    assert response.status_code == 200, response.text
    payload = captured["json"]
    assert payload["type"] == "template"
    assert payload["template"]["name"] == "match_fee_due"
    texts = [item["text"] for item in payload["template"]["components"][0]["parameters"]]
    assert texts[0] == "Ravi Shah"
    assert texts[1] == "Tigers"
    assert "INR 2500.00" in texts[4]
    assert texts[5] == "UPI: 30yca@upi"


def test_notify_requires_spoc_amount_and_whatsapp_config(client, monkeypatch):
    missing_fee = client.post("/api/admin/matches/501/fees/notify", json={
        "team_id": "10",
        "match_status": "past",
    })
    assert missing_fee.status_code == 400

    early = client.post("/api/admin/matches/501/fees/notify", json={
        "team_id": "10",
        "match_status": "upcoming",
    })
    assert early.status_code == 400
    assert "completed" in early.json()["detail"]

    client.put("/api/admin/matches/501/fees", json={
        "tournament_id": "2189985",
        "teams": [{"team_id": "10", "team_name": "Lions", "amount": 2500}],
    })
    missing_spoc = client.post("/api/admin/matches/501/fees/notify", json={
        "team_id": "10",
        "match_status": "past",
    })
    assert missing_spoc.status_code == 400
    assert "SPOC" in missing_spoc.json()["detail"]

    _spoc(client)
    monkeypatch.delenv("WHATSAPP_TOKEN", raising=False)
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)
    unconfigured = client.post("/api/admin/matches/501/fees/notify", json={
        "team_id": "10",
        "match_status": "past",
    })
    assert unconfigured.status_code == 503
    stored = client.get("/api/admin/matches/501/fees").json()["fees"][0]
    assert stored["status"] == "pending"
    assert stored["whatsapp_status"] == "failed"

    bad_phone = client.put("/api/admin/teams/10/spoc", json={"spoc_name": "Asha", "phone": "123"})
    assert bad_phone.status_code == 400

"""Admin match fees: team SPOC, per-match amount, and WhatsApp click-to-chat link."""
from urllib.parse import parse_qs, urlparse

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


def test_team_primary_spoc_applies_across_tournaments(client):
    _spoc(client, name="Default SPOC")
    historical = client.put("/api/admin/teams/10/spoc", json={
        "spoc_name": "Cup SPOC",
        "phone": "9811111111",
        "team_name": "Lions",
        "tournament_id": "2189985",
    })
    assert historical.status_code == 200
    resolved = client.get("/api/admin/teams/10/spoc", params={"tournament_id": "2189985"})
    assert resolved.json()["spoc_name"] == "Default SPOC"
    assert resolved.json()["scope"] == "team"
    listed = client.get("/api/admin/spocs")
    assert listed.status_code == 200
    names = {row["spoc_name"] for row in listed.json()["spocs"]}
    assert names == {"Default SPOC", "Cup SPOC"}
    default = client.get("/api/admin/teams/10/spoc")
    assert default.json()["spoc_name"] == "Default SPOC"


def test_historical_tournament_spoc_is_used_until_a_team_primary_exists(client):
    response = client.put("/api/admin/teams/10/spoc", json={
        "spoc_name": "Cup SPOC",
        "phone": "9811111111",
        "team_name": "Lions",
        "tournament_id": "2189985",
    })
    assert response.status_code == 200
    resolved = client.get("/api/admin/teams/10/spoc", params={"tournament_id": "2189985"})
    assert resolved.json()["spoc_name"] == "Cup SPOC"
    assert resolved.json()["scope"] == "tournament"
    missing = client.get("/api/admin/teams/10/spoc")
    assert missing.status_code == 404


def test_inactive_team_primary_falls_back_to_history(client):
    saved = client.put("/api/admin/teams/10/spoc", json={
        "spoc_name": "Paused SPOC",
        "phone": "9876543210",
        "team_name": "Lions",
        "status": "inactive",
        "login_enabled": False,
    })
    assert saved.status_code == 200
    client.put("/api/admin/teams/10/spoc", json={
        "spoc_name": "Cup SPOC",
        "phone": "9811111111",
        "team_name": "Lions",
        "tournament_id": "2189985",
    })
    resolved = client.get("/api/admin/teams/10/spoc", params={"tournament_id": "2189985"})
    assert resolved.json()["spoc_name"] == "Cup SPOC"
    primary = client.get("/api/admin/teams/10/spoc")
    assert primary.json()["spoc_name"] == "Paused SPOC"
    assert primary.json()["status"] == "inactive"


def test_spoc_profile_stores_email_status_and_login(client):
    saved = client.put("/api/admin/teams/10/spoc", json={
        "spoc_name": "Asha Rao",
        "phone": "9876543210",
        "team_name": "Lions",
        "email": "asha@example.com",
        "status": "active",
        "login_enabled": True,
    })
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["email"] == "asha@example.com"
    assert body["status"] == "active"
    assert body["login_enabled"] is True
    assert body["scope"] == "team"
    assert body["tournament_id"] == ""


def test_collection_summary_reports_match_fees_not_entry_fees(client):
    _spoc(client)
    _fee(client)
    client.put("/api/admin/matches/501/fees", json={
        "tournament_id": "2189985",
        "match_title": "Lions vs Tigers",
        "teams": [
            {"team_id": "10", "team_name": "Lions", "amount": 6000},
            {"team_id": "11", "team_name": "Tigers", "amount": 6000},
        ],
    })
    client.put("/api/admin/matches/501/fees/10/payment", json={
        "amount_received": 6000,
        "payment_mode": "UPI",
        "reference": "UTR1",
        "received_on": "2026-10-18",
        "status": "paid",
    })
    summary = client.get("/api/admin/fees/summary")
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["tournament_entry_tracked"] is False
    assert body["match_collected"] == "6000.00"
    assert body["match_pending"] == "6000.00"
    assert body["match_collected_count"] == 1
    assert body["match_pending_count"] == 1
    assert body["tournament_fee_rates"][0]["amount"] == "2500.00"
    assert body["primary_spoc_team_ids"] == ["10"]
    assert "501" in body["fee_match_ids"]
    assert "phone" not in summary.text


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


def _whatsapp_text(url: str) -> str:
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "api.whatsapp.com"
    assert parsed.path == "/send/"
    query = parse_qs(parsed.query)
    assert query["type"] == ["phone_number"]
    assert query["app_absent"] == ["0"]
    return query["phone"][0], query["text"][0]


def test_notify_returns_whatsapp_link_with_amount_and_payment_details(client):
    _spoc(client)
    _fee(client)
    client.post("/api/admin/tournaments/2189985/match-fee/apply", json={"matches": [UPCOMING]})

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
    assert result["delivery"] == "whatsapp_link"
    assert result["amount"] == "3000.00"
    phone, text = _whatsapp_text(result["whatsapp_url"])
    assert phone == "919876543210"
    assert text.startswith("Hello Asha Rao, Good Evening!")
    assert "Lions match fees are due." in text
    assert "UPI: 30yca@upi" in text
    assert "Match Fee Amount - 3000 Rs" in text
    assert text.endswith("Thank you for your prompt attention!")

    again = client.post("/api/admin/matches/501/fees/notify", json={"team_id": "10", "match_status": "past"})
    assert again.status_code == 200
    repeat = again.json()["results"][0]
    assert repeat["already_notified"] is True
    assert repeat["whatsapp_url"].startswith("https://api.whatsapp.com/send/?")

    paid = client.post("/api/admin/matches/501/fees/10/paid")
    assert paid.status_code == 200
    assert paid.json()["status"] == "paid"


def test_notify_requires_spoc_and_a_completed_match(client):
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
    ready = client.post("/api/admin/matches/501/fees/notify", json={
        "team_id": "10",
        "match_status": "past",
    })
    assert ready.status_code == 200, ready.text
    stored = client.get("/api/admin/matches/501/fees").json()["fees"][0]
    assert stored["status"] == "notified"
    assert stored["whatsapp_status"] == "link_ready"

    bad_phone = client.put("/api/admin/teams/10/spoc", json={"spoc_name": "Asha", "phone": "123"})
    assert bad_phone.status_code == 400


def test_upi_comes_from_settings_and_receipt_can_be_updated(client):
    saved = client.put("/api/admin/settings/payment", json={
        "payee_name": "30YCA",
        "upi_id": "30yca@oksbi",
        "gpay_number": "9988776655",
        "payment_apps": "PhonePe / Google Pay",
    })
    assert saved.status_code == 200, saved.text
    settings = saved.json()
    assert settings["configured"] is True
    assert settings["gpay_number"] == "9988776655"
    assert client.get("/api/admin/settings/payment").json()["upi_id"] == "30yca@oksbi"

    _spoc(client)
    client.put("/api/admin/matches/501/fees", json={
        "tournament_id": "2189985",
        "match_title": "Lions vs Tigers",
        "teams": [{"team_id": "10", "team_name": "Lions", "opponent_name": "Tigers", "amount": 6000}],
    })
    sent = client.post("/api/admin/matches/501/fees/notify", json={
        "team_id": "10",
        "match_status": "past",
    })
    assert sent.status_code == 200, sent.text
    result = sent.json()["results"][0]
    _, text = _whatsapp_text(result["whatsapp_url"])
    assert "UPI ID: 30yca@oksbi" in text
    assert "Number: 9988776655" in text
    assert "Kindly submit the payment today to 30YCA via:" in text
    upi = result["upi"]
    assert upi["upi_uri"].startswith("upi://pay?")
    assert "pa=30yca%40oksbi" in upi["upi_uri"]
    assert "am=6000.00" in upi["upi_uri"]
    assert upi["google_pay_uri"].startswith("tez://upi/pay?")

    receipt = client.put("/api/admin/matches/501/fees/10/payment", json={
        "amount_received": 6000,
        "payment_mode": "Google Pay",
        "reference": "UTR123456",
        "received_on": "2026-10-18",
        "note": "Received from SPOC",
        "status": "paid",
    })
    assert receipt.status_code == 200, receipt.text
    body = receipt.json()
    assert body["status"] == "paid"
    assert body["amount_received"] == "6000.00"
    assert body["payment_mode"] == "Google Pay"
    assert body["payment_reference"] == "UTR123456"

    corrected = client.put("/api/admin/matches/501/fees/10/payment", json={
        "amount_received": 5500,
        "payment_mode": "PhonePe",
        "reference": "UTR999",
        "received_on": "2026-10-19",
        "note": "Short by 500",
        "status": "partial",
    })
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["status"] == "partial"
    assert corrected.json()["amount_received"] == "5500.00"
    assert corrected.json()["payment_reference"] == "UTR999"

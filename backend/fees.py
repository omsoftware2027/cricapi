"""Team SPOC contacts, per-match fees, and WhatsApp payment messages.

Scorecard scraping stays stateless. These admin records live in SQLite so
Lovable can save a team contact, confirm the amount after a match, and ask
this API to send the payment WhatsApp.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

ROOT_DIR = Path(__file__).parent
_DEFAULT_DB = ROOT_DIR / "data" / "fees.sqlite"
_LOCK = threading.Lock()
_READY: set[str] = set()

_UPCOMING = {"upcoming", "scheduled", "fixture", "notstarted", "not started", "yet to start"}


class FeeError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def db_path() -> Path:
    raw = (os.environ.get("FEE_DB_PATH") or "").strip()
    return Path(raw) if raw else _DEFAULT_DB


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    key = str(path.resolve())
    if key not in _READY:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS team_spocs (
                team_id TEXT NOT NULL,
                tournament_id TEXT NOT NULL DEFAULT '',
                team_name TEXT NOT NULL DEFAULT '',
                spoc_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (team_id, tournament_id)
            );
            CREATE TABLE IF NOT EXISTS tournament_fees (
                tournament_id TEXT PRIMARY KEY,
                amount TEXT NOT NULL,
                currency TEXT NOT NULL DEFAULT 'INR',
                payment_details TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS match_fees (
                match_id TEXT NOT NULL,
                team_id TEXT NOT NULL,
                tournament_id TEXT NOT NULL DEFAULT '',
                team_name TEXT NOT NULL DEFAULT '',
                opponent_name TEXT NOT NULL DEFAULT '',
                match_title TEXT NOT NULL DEFAULT '',
                match_date TEXT NOT NULL DEFAULT '',
                amount TEXT NOT NULL DEFAULT '',
                currency TEXT NOT NULL DEFAULT 'INR',
                payment_details TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                whatsapp_status TEXT NOT NULL DEFAULT '',
                whatsapp_message_id TEXT NOT NULL DEFAULT '',
                whatsapp_error TEXT NOT NULL DEFAULT '',
                notified_at TEXT NOT NULL DEFAULT '',
                paid_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (match_id, team_id)
            );
            """
        )
        conn.commit()
        _READY.add(key)
    return conn


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _require_id(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not text.isdigit():
        raise FeeError(f"{label} must be numeric")
    return text


def _optional_id(value: str, label: str) -> str:
    text = str(value or "").strip()
    if text and not text.isdigit():
        raise FeeError(f"{label} must be numeric")
    return text


def normalize_phone(phone: str, country_code: str = "91") -> str:
    """Store WhatsApp numbers as country code + subscriber digits, no plus."""
    cc = _digits(country_code) or "91"
    if not (1 <= len(cc) <= 3):
        raise FeeError("country_code must be 1 to 3 digits")
    raw = _digits(phone)
    if not raw:
        raise FeeError("phone is required")
    if len(raw) == 10:
        raw = cc + raw
    elif len(raw) == 11 and raw.startswith("0"):
        raw = cc + raw[1:]
    elif raw.startswith(cc) and len(raw) > len(cc) + 7:
        pass
    if not (11 <= len(raw) <= 15):
        raise FeeError("phone must be a WhatsApp number with country code")
    return raw


def _money(value) -> str:
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise FeeError("amount must be a number")
    if amount != amount.to_integral() and amount.as_tuple().exponent < -2:
        amount = amount.quantize(Decimal("0.01"))
    else:
        amount = amount.quantize(Decimal("0.01"))
    if amount <= 0:
        raise FeeError("amount must be greater than 0")
    if amount > Decimal("10000000"):
        raise FeeError("amount is too large")
    return f"{amount:.2f}"


def _currency(value: str) -> str:
    text = (value or "INR").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", text):
        raise FeeError("currency must be a 3-letter code")
    return text


def _clip(value: str, limit: int, label: str) -> str:
    text = (value or "").strip()
    if len(text) > limit:
        raise FeeError(f"{label} must be {limit} characters or fewer")
    return text


def _spoc_out(row: sqlite3.Row) -> dict:
    phone = row["phone"]
    return {
        "team_id": row["team_id"],
        "tournament_id": row["tournament_id"],
        "team_name": row["team_name"],
        "spoc_name": row["spoc_name"],
        "phone": phone,
        "phone_display": f"+{phone}",
        "updated_at": row["updated_at"],
    }


def _fee_config_out(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "tournament_id": row["tournament_id"],
        "amount": row["amount"],
        "currency": row["currency"],
        "payment_details": row["payment_details"],
        "updated_at": row["updated_at"],
    }


def _match_fee_out(row: sqlite3.Row) -> dict:
    return {
        "match_id": row["match_id"],
        "team_id": row["team_id"],
        "tournament_id": row["tournament_id"],
        "team_name": row["team_name"],
        "opponent_name": row["opponent_name"],
        "match_title": row["match_title"],
        "match_date": row["match_date"],
        "amount": row["amount"],
        "currency": row["currency"],
        "payment_details": row["payment_details"],
        "status": row["status"],
        "whatsapp_status": row["whatsapp_status"],
        "whatsapp_message_id": row["whatsapp_message_id"],
        "whatsapp_error": row["whatsapp_error"],
        "notified_at": row["notified_at"],
        "paid_at": row["paid_at"],
        "updated_at": row["updated_at"],
    }


def save_spoc(
    team_id: str,
    spoc_name: str,
    phone: str,
    *,
    country_code: str = "91",
    team_name: str = "",
    tournament_id: str = "",
) -> dict:
    team_id = _require_id(team_id, "team_id")
    tournament_id = _optional_id(tournament_id, "tournament_id")
    name = _clip(spoc_name, 80, "spoc_name")
    if not name:
        raise FeeError("spoc_name is required")
    stored_phone = normalize_phone(phone, country_code)
    team_name = _clip(team_name, 120, "team_name")
    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO team_spocs (team_id, tournament_id, team_name, spoc_name, phone, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(team_id, tournament_id) DO UPDATE SET
                    team_name=excluded.team_name,
                    spoc_name=excluded.spoc_name,
                    phone=excluded.phone,
                    updated_at=excluded.updated_at
                """,
                (team_id, tournament_id, team_name, name, stored_phone, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM team_spocs WHERE team_id=? AND tournament_id=?",
                (team_id, tournament_id),
            ).fetchone()
        finally:
            conn.close()
    return _spoc_out(row)


def get_spoc(team_id: str, tournament_id: str = "") -> dict | None:
    team_id = _require_id(team_id, "team_id")
    tournament_id = _optional_id(tournament_id, "tournament_id")
    with _LOCK:
        conn = _connect()
        try:
            row = _resolve_spoc(conn, team_id, tournament_id)
        finally:
            conn.close()
    return _spoc_out(row) if row else None


def _resolve_spoc(conn: sqlite3.Connection, team_id: str, tournament_id: str) -> sqlite3.Row | None:
    if tournament_id:
        row = conn.execute(
            "SELECT * FROM team_spocs WHERE team_id=? AND tournament_id=?",
            (team_id, tournament_id),
        ).fetchone()
        if row:
            return row
    return conn.execute(
        "SELECT * FROM team_spocs WHERE team_id=? AND tournament_id=''",
        (team_id,),
    ).fetchone()


def list_spocs(tournament_id: str = "") -> list:
    tournament_id = _optional_id(tournament_id, "tournament_id")
    with _LOCK:
        conn = _connect()
        try:
            if tournament_id:
                rows = conn.execute(
                    """
                    SELECT * FROM team_spocs
                    WHERE tournament_id=? OR tournament_id=''
                    ORDER BY team_name, team_id, tournament_id DESC
                    """,
                    (tournament_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM team_spocs ORDER BY team_name, team_id"
                ).fetchall()
        finally:
            conn.close()
    return [_spoc_out(row) for row in rows]


def delete_spoc(team_id: str, tournament_id: str = "") -> bool:
    team_id = _require_id(team_id, "team_id")
    tournament_id = _optional_id(tournament_id, "tournament_id")
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                "DELETE FROM team_spocs WHERE team_id=? AND tournament_id=?",
                (team_id, tournament_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def save_tournament_fee(tournament_id: str, amount, currency: str = "INR", payment_details: str = "") -> dict:
    tournament_id = _require_id(tournament_id, "tournament_id")
    stored_amount = _money(amount)
    stored_currency = _currency(currency)
    details = _clip(payment_details, 1000, "payment_details")
    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO tournament_fees (tournament_id, amount, currency, payment_details, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tournament_id) DO UPDATE SET
                    amount=excluded.amount,
                    currency=excluded.currency,
                    payment_details=excluded.payment_details,
                    updated_at=excluded.updated_at
                """,
                (tournament_id, stored_amount, stored_currency, details, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM tournament_fees WHERE tournament_id=?",
                (tournament_id,),
            ).fetchone()
        finally:
            conn.close()
    return _fee_config_out(row)


def get_tournament_fee(tournament_id: str) -> dict | None:
    tournament_id = _require_id(tournament_id, "tournament_id")
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM tournament_fees WHERE tournament_id=?",
                (tournament_id,),
            ).fetchone()
        finally:
            conn.close()
    return _fee_config_out(row)


def _status_text(status: str) -> str:
    return re.sub(r"[\s_-]+", " ", (status or "").strip().lower())


def _is_upcoming(status: str) -> bool:
    text = _status_text(status)
    return text in _UPCOMING or "upcoming" in text


def _require_completed(status: str) -> None:
    text = _status_text(status)
    if not text:
        raise FeeError("Pass match_status. The fee WhatsApp is sent after the match is completed.")
    if text in _UPCOMING or "upcoming" in text or text == "live" or text.startswith("live "):
        raise FeeError("The fee WhatsApp is sent after the match is completed.")


def _team_sides(match: dict) -> list:
    sides = []
    pairs = (
        ("team_a_id", "team_a", "team_b"),
        ("team_b_id", "team_b", "team_a"),
    )
    for id_key, name_key, opp_key in pairs:
        team_id = str(match.get(id_key) or "").strip()
        if not team_id.isdigit():
            continue
        sides.append({
            "team_id": team_id,
            "team_name": str(match.get(name_key) or "").strip(),
            "opponent_name": str(match.get(opp_key) or "").strip(),
        })
    return sides


def save_match_fees(
    match_id: str,
    teams: list,
    *,
    tournament_id: str = "",
    match_title: str = "",
    match_date: str = "",
    currency: str = "INR",
    payment_details: str = "",
) -> list:
    match_id = _require_id(match_id, "match_id")
    tournament_id = _optional_id(tournament_id, "tournament_id")
    match_title = _clip(match_title, 180, "match_title")
    match_date = _clip(match_date, 40, "match_date")
    stored_currency = _currency(currency)
    details = _clip(payment_details, 1000, "payment_details")
    if not teams:
        raise FeeError("teams is required")
    now = _now()
    saved = []
    with _LOCK:
        conn = _connect()
        try:
            config = None
            if tournament_id:
                config = conn.execute(
                    "SELECT * FROM tournament_fees WHERE tournament_id=?",
                    (tournament_id,),
                ).fetchone()
            for team in teams:
                team_id = _require_id(str(team.get("team_id") or ""), "team_id")
                team_name = _clip(str(team.get("team_name") or ""), 120, "team_name")
                opponent = _clip(str(team.get("opponent_name") or ""), 120, "opponent_name")
                raw_amount = team.get("amount")
                if raw_amount is None or str(raw_amount).strip() == "":
                    if config is None:
                        raise FeeError("Set the tournament match fee or pass an amount for each team")
                    stored_amount = config["amount"]
                    row_currency = config["currency"]
                    row_details = details or config["payment_details"]
                else:
                    stored_amount = _money(raw_amount)
                    row_currency = stored_currency
                    row_details = details or (config["payment_details"] if config else "")
                existing = conn.execute(
                    "SELECT status, notified_at, paid_at, whatsapp_status, whatsapp_message_id FROM match_fees WHERE match_id=? AND team_id=?",
                    (match_id, team_id),
                ).fetchone()
                status = existing["status"] if existing else "pending"
                if status == "paid":
                    status = "paid"
                conn.execute(
                    """
                    INSERT INTO match_fees (
                        match_id, team_id, tournament_id, team_name, opponent_name,
                        match_title, match_date, amount, currency, payment_details,
                        status, whatsapp_status, whatsapp_message_id, whatsapp_error,
                        notified_at, paid_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
                    ON CONFLICT(match_id, team_id) DO UPDATE SET
                        tournament_id=excluded.tournament_id,
                        team_name=excluded.team_name,
                        opponent_name=excluded.opponent_name,
                        match_title=excluded.match_title,
                        match_date=excluded.match_date,
                        amount=excluded.amount,
                        currency=excluded.currency,
                        payment_details=excluded.payment_details,
                        status=excluded.status,
                        updated_at=excluded.updated_at
                    """,
                    (
                        match_id, team_id, tournament_id, team_name, opponent,
                        match_title, match_date, stored_amount, row_currency, row_details,
                        status,
                        existing["whatsapp_status"] if existing else "",
                        existing["whatsapp_message_id"] if existing else "",
                        existing["notified_at"] if existing else "",
                        existing["paid_at"] if existing else "",
                        now,
                    ),
                )
            conn.commit()
            rows = conn.execute(
                "SELECT * FROM match_fees WHERE match_id=? ORDER BY team_name, team_id",
                (match_id,),
            ).fetchall()
            saved = [_match_fee_out(row) for row in rows]
        finally:
            conn.close()
    return saved


def list_match_fees(match_id: str) -> list:
    match_id = _require_id(match_id, "match_id")
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM match_fees WHERE match_id=? ORDER BY team_name, team_id",
                (match_id,),
            ).fetchall()
        finally:
            conn.close()
    return [_match_fee_out(row) for row in rows]


def list_tournament_match_fees(tournament_id: str) -> list:
    tournament_id = _require_id(tournament_id, "tournament_id")
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM match_fees WHERE tournament_id=? ORDER BY match_date, match_id, team_name",
                (tournament_id,),
            ).fetchall()
        finally:
            conn.close()
    return [_match_fee_out(row) for row in rows]


def apply_tournament_fee(tournament_id: str, matches: list, *, include_completed: bool = False) -> dict:
    """Copy the tournament per-match fee onto matches that do not have an amount yet."""
    tournament_id = _require_id(tournament_id, "tournament_id")
    created = 0
    skipped = 0
    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            config = conn.execute(
                "SELECT * FROM tournament_fees WHERE tournament_id=?",
                (tournament_id,),
            ).fetchone()
            if config is None:
                raise FeeError("Set the tournament per-match fee before attaching it to matches")
            for match in matches or []:
                match_id = str(match.get("match_id") or "").strip()
                if not match_id.isdigit():
                    skipped += 1
                    continue
                if not include_completed and not _is_upcoming(str(match.get("status") or "")):
                    skipped += 1
                    continue
                title = _clip(str(match.get("match_title") or ""), 180, "match_title")
                if not title:
                    names = [str(match.get("team_a") or "").strip(), str(match.get("team_b") or "").strip()]
                    title = " vs ".join([n for n in names if n])[:180]
                match_date = _clip(str(match.get("start_time") or match.get("match_date") or ""), 40, "match_date")
                for side in _team_sides(match):
                    existing = conn.execute(
                        "SELECT amount FROM match_fees WHERE match_id=? AND team_id=?",
                        (match_id, side["team_id"]),
                    ).fetchone()
                    if existing and existing["amount"]:
                        skipped += 1
                        continue
                    conn.execute(
                        """
                        INSERT INTO match_fees (
                            match_id, team_id, tournament_id, team_name, opponent_name,
                            match_title, match_date, amount, currency, payment_details,
                            status, whatsapp_status, whatsapp_message_id, whatsapp_error,
                            notified_at, paid_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', '', '', '', '', ?)
                        ON CONFLICT(match_id, team_id) DO UPDATE SET
                            tournament_id=excluded.tournament_id,
                            team_name=excluded.team_name,
                            opponent_name=excluded.opponent_name,
                            match_title=excluded.match_title,
                            match_date=excluded.match_date,
                            amount=excluded.amount,
                            currency=excluded.currency,
                            payment_details=excluded.payment_details,
                            updated_at=excluded.updated_at
                        """,
                        (
                            match_id, side["team_id"], tournament_id, side["team_name"], side["opponent_name"],
                            title, match_date, config["amount"], config["currency"], config["payment_details"],
                            now,
                        ),
                    )
                    created += 1
            conn.commit()
        finally:
            conn.close()
    return {
        "tournament_id": tournament_id,
        "attached": created,
        "skipped": skipped,
        "match_fee": get_tournament_fee(tournament_id),
    }


def preview_fees(tournament_id: str, matches: list) -> dict:
    """Annotate matches Lovable already scraped with the fee and the team SPOC."""
    tournament_id = _require_id(tournament_id, "tournament_id")
    config = get_tournament_fee(tournament_id)
    annotated = []
    with _LOCK:
        conn = _connect()
        try:
            for match in matches or []:
                match_id = str(match.get("match_id") or "").strip()
                teams = []
                for side in _team_sides(match):
                    saved = None
                    if match_id.isdigit():
                        saved = conn.execute(
                            "SELECT * FROM match_fees WHERE match_id=? AND team_id=?",
                            (match_id, side["team_id"]),
                        ).fetchone()
                    spoc = _resolve_spoc(conn, side["team_id"], tournament_id)
                    amount = ""
                    currency = (config or {}).get("currency") or "INR"
                    details = (config or {}).get("payment_details") or ""
                    status = "included" if config else "unset"
                    notified_at = ""
                    whatsapp_status = ""
                    if saved:
                        amount = saved["amount"]
                        currency = saved["currency"]
                        details = saved["payment_details"]
                        status = saved["status"]
                        notified_at = saved["notified_at"]
                        whatsapp_status = saved["whatsapp_status"]
                    elif config:
                        amount = config["amount"]
                    teams.append({
                        "team_id": side["team_id"],
                        "team_name": side["team_name"],
                        "opponent_name": side["opponent_name"],
                        "amount": amount,
                        "currency": currency,
                        "payment_details": details,
                        "status": status,
                        "spoc": _spoc_out(spoc) if spoc else None,
                        "notified_at": notified_at,
                        "whatsapp_status": whatsapp_status,
                    })
                annotated.append({
                    "match_id": match_id,
                    "status": match.get("status") or "",
                    "start_time": match.get("start_time") or "",
                    "team_a": match.get("team_a") or "",
                    "team_b": match.get("team_b") or "",
                    "teams": teams,
                })
        finally:
            conn.close()
    return {
        "tournament_id": tournament_id,
        "match_fee": config,
        "matches": annotated,
    }


def _template_param(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    if not text:
        return "-"
    return text[:900]


def render_fee_message(fee: dict, spoc: dict) -> str:
    title = fee.get("match_title") or f"Match {fee.get('match_id')}"
    when = fee.get("match_date") or "the completed match"
    opponent = fee.get("opponent_name") or "the opposition"
    details = (fee.get("payment_details") or "").strip() or "Use the payment details shared by 30YCA."
    amount = fee.get("amount") or ""
    currency = fee.get("currency") or "INR"
    return (
        f"30YCA match fee\n\n"
        f"Hello {spoc.get('spoc_name')},\n"
        f"{fee.get('team_name') or 'Your team'} has a match fee for {title} ({when}).\n"
        f"Opponent: {opponent}\n"
        f"Amount to pay: {currency} {amount}\n"
        f"Payment details:\n{details}\n\n"
        f"Reply once the payment is done."
    )


def _whatsapp_config() -> tuple[str, str, str, str]:
    token = (os.environ.get("WHATSAPP_TOKEN") or "").strip()
    phone_id = (os.environ.get("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
    template = (os.environ.get("WHATSAPP_TEMPLATE_NAME") or "").strip()
    lang = (os.environ.get("WHATSAPP_TEMPLATE_LANG") or "en").strip() or "en"
    return token, phone_id, template, lang


def _send_whatsapp(phone: str, message: str, fee: dict, spoc: dict) -> str:
    token, phone_id, template, lang = _whatsapp_config()
    if not token or not phone_id:
        raise FeeError(
            "WhatsApp is not configured. Set WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID on the API server.",
            status_code=503,
        )
    version = (os.environ.get("WHATSAPP_API_VERSION") or "v21.0").strip() or "v21.0"
    url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
    if template:
        params = [
            spoc.get("spoc_name") or "",
            fee.get("team_name") or "Your team",
            fee.get("match_title") or f"Match {fee.get('match_id')}",
            fee.get("match_date") or "the completed match",
            f"{fee.get('currency') or 'INR'} {fee.get('amount') or ''}".strip(),
            fee.get("payment_details") or "Contact 30YCA for payment details",
            fee.get("opponent_name") or "-",
        ]
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": lang},
                "components": [{
                    "type": "body",
                    "parameters": [{"type": "text", "text": _template_param(item)} for item in params],
                }],
            },
        }
    else:
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"body": message[:4096]},
        }
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
    except requests.RequestException as exc:
        raise FeeError(f"WhatsApp request failed: {exc}", status_code=502)
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400:
        err = ""
        if isinstance(body, dict):
            err = ((body.get("error") or {}) if isinstance(body.get("error"), dict) else {}).get("message") or ""
        raise FeeError(err or "WhatsApp rejected the message", status_code=502)
    messages = body.get("messages") if isinstance(body, dict) else None
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        return str(messages[0].get("id") or "")
    return ""


def notify_match_fee(
    match_id: str,
    *,
    team_id: str = "",
    amount=None,
    payment_details: str = "",
    match_title: str = "",
    match_date: str = "",
    match_status: str = "",
    tournament_id: str = "",
    force: bool = False,
) -> dict:
    """Send the fee WhatsApp to one team, or to every team saved on the match."""
    _require_completed(match_status)
    match_id = _require_id(match_id, "match_id")
    team_id = _optional_id(team_id, "team_id")
    tournament_id = _optional_id(tournament_id, "tournament_id")
    results = []
    with _LOCK:
        conn = _connect()
        try:
            if team_id:
                rows = conn.execute(
                    "SELECT * FROM match_fees WHERE match_id=? AND team_id=?",
                    (match_id, team_id),
                ).fetchall()
                if not rows:
                    raise FeeError("Save the match fee for this team before sending WhatsApp")
            else:
                rows = conn.execute(
                    "SELECT * FROM match_fees WHERE match_id=? ORDER BY team_name, team_id",
                    (match_id,),
                ).fetchall()
                if not rows:
                    raise FeeError("Save the match fee before sending WhatsApp")
            pending = []
            for row in rows:
                fee = dict(row)
                if tournament_id:
                    fee["tournament_id"] = tournament_id
                if match_title:
                    fee["match_title"] = _clip(match_title, 180, "match_title")
                if match_date:
                    fee["match_date"] = _clip(match_date, 40, "match_date")
                if payment_details:
                    fee["payment_details"] = _clip(payment_details, 1000, "payment_details")
                if amount is not None and str(amount).strip() != "":
                    fee["amount"] = _money(amount)
                elif not fee.get("amount"):
                    config = None
                    if fee.get("tournament_id"):
                        config = conn.execute(
                            "SELECT * FROM tournament_fees WHERE tournament_id=?",
                            (fee["tournament_id"],),
                        ).fetchone()
                    if config:
                        fee["amount"] = config["amount"]
                        fee["currency"] = fee["currency"] or config["currency"]
                        if not fee.get("payment_details"):
                            fee["payment_details"] = config["payment_details"]
                if not fee.get("amount"):
                    raise FeeError("Add the amount before sending the WhatsApp fee message")
                if fee.get("status") == "paid" and not force:
                    results.append({
                        "team_id": fee["team_id"],
                        "sent": False,
                        "already_paid": True,
                        "message": "",
                    })
                    continue
                if fee.get("status") == "notified" and not force:
                    results.append({
                        "team_id": fee["team_id"],
                        "sent": False,
                        "already_notified": True,
                        "notified_at": fee.get("notified_at") or "",
                        "message": "",
                    })
                    continue
                spoc_row = _resolve_spoc(conn, fee["team_id"], fee.get("tournament_id") or "")
                if spoc_row is None:
                    raise FeeError(
                        f"Add a team SPOC for team {fee['team_id']} before sending WhatsApp"
                    )
                spoc = _spoc_out(spoc_row)
                message = render_fee_message(fee, spoc)
                pending.append((fee, spoc, message))
        finally:
            conn.close()

    for fee, spoc, message in pending:
        message_id = ""
        error = ""
        status = "sent"
        try:
            message_id = _send_whatsapp(spoc["phone"], message, fee, spoc)
        except FeeError as exc:
            status = "failed"
            error = str(exc)
            _store_notify_result(fee, spoc, message, status, message_id, error, sent=False)
            raise
        _store_notify_result(fee, spoc, message, status, message_id, error, sent=True)
        results.append({
            "team_id": fee["team_id"],
            "team_name": fee.get("team_name") or "",
            "spoc_name": spoc["spoc_name"],
            "phone_display": spoc["phone_display"],
            "sent": True,
            "already_notified": False,
            "whatsapp_message_id": message_id,
            "amount": fee["amount"],
            "currency": fee.get("currency") or "INR",
            "message": message,
        })
    return {"match_id": match_id, "results": results}


def _store_notify_result(fee: dict, spoc: dict, message: str, status: str, message_id: str, error: str, sent: bool) -> None:
    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                UPDATE match_fees SET
                    tournament_id=?,
                    team_name=?,
                    opponent_name=?,
                    match_title=?,
                    match_date=?,
                    amount=?,
                    currency=?,
                    payment_details=?,
                    status=?,
                    whatsapp_status=?,
                    whatsapp_message_id=?,
                    whatsapp_error=?,
                    notified_at=?,
                    updated_at=?
                WHERE match_id=? AND team_id=?
                """,
                (
                    fee.get("tournament_id") or "",
                    fee.get("team_name") or "",
                    fee.get("opponent_name") or "",
                    fee.get("match_title") or "",
                    fee.get("match_date") or "",
                    fee.get("amount") or "",
                    fee.get("currency") or "INR",
                    fee.get("payment_details") or "",
                    "notified" if sent else (fee.get("status") or "pending"),
                    status,
                    message_id or fee.get("whatsapp_message_id") or "",
                    error[:500],
                    now if sent else (fee.get("notified_at") or ""),
                    now,
                    fee["match_id"],
                    fee["team_id"],
                ),
            )
            conn.commit()
        finally:
            conn.close()


def mark_fee_paid(match_id: str, team_id: str) -> dict:
    match_id = _require_id(match_id, "match_id")
    team_id = _require_id(team_id, "team_id")
    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                UPDATE match_fees
                SET status='paid', paid_at=?, updated_at=?
                WHERE match_id=? AND team_id=?
                """,
                (now, now, match_id, team_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                raise FeeError("No match fee exists for that team")
            row = conn.execute(
                "SELECT * FROM match_fees WHERE match_id=? AND team_id=?",
                (match_id, team_id),
            ).fetchone()
        finally:
            conn.close()
    return _match_fee_out(row)

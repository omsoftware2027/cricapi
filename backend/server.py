"""
CricHeroes Scorecard API.
Scorecard routes scrape on demand and do not store match data.
Team SPOC contacts, per-match fees, and WhatsApp payment messages are stored
in SQLite so Lovable can run the admin fee flow.
"""
from __future__ import annotations

from fastapi import FastAPI, APIRouter, HTTPException, Header, Depends
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
import os
import logging
import asyncio
from pathlib import Path
from pydantic import BaseModel
from typing import List, Optional

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from scrapers import (  # noqa: E402
    scrape,
    scrape_player,
    scrape_tournament,
    scrape_tournaments,
    scrape_organizer,
    list_organizer_tournaments,
    tournaments_for_player,
    scorecard_to_csv,
    tournament_to_csv,
    ScrapeError,
    CloudflareBlocked,
)
from fees import FeeError  # noqa: E402
import fees as fee_store  # noqa: E402


# ---------------- Auth ----------------

API_AUTH_TOKEN = os.environ.get('API_AUTH_TOKEN', '').strip()


def require_api_token(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    if not API_AUTH_TOKEN:
        return  # auth disabled
    supplied = None
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization.split(" ", 1)[1].strip()
    elif x_api_key:
        supplied = x_api_key.strip()
    if supplied != API_AUTH_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API token. Send 'Authorization: Bearer <token>' or 'X-API-Key: <token>'.",
        )


app = FastAPI(
    title="CricHeroes Scorecard API",
    description="Scrape CricHeroes, Cricbuzz and ESPN Cricinfo scorecards. JSON or CSV.",
    version="1.0.0",
)
api_router = APIRouter(prefix="/api")


# ---------------- Models ----------------

class ScrapeRequest(BaseModel):
    url: str


class BatchRequest(BaseModel):
    match_ids: List[str] = []
    urls: Optional[List[str]] = None


class TournamentQuery(BaseModel):
    tournament_id: str
    include_scorecards: bool = True
    scorecard_limit: int = 100


class TournamentsQuery(BaseModel):
    tournament_ids: List[str] = []
    include_scorecards: bool = True
    scorecard_limit: int = 100


class OrganizerImport(BaseModel):
    include_scorecards: bool = True
    scorecard_limit: int = 100
    offset: int = 0
    limit: int = 10


class TeamSpocBody(BaseModel):
    spoc_name: str
    phone: str
    country_code: str = "91"
    team_name: str = ""
    tournament_id: str = ""


class TournamentMatchFeeBody(BaseModel):
    amount: float
    currency: str = "INR"
    payment_details: str = ""


class MatchFeeTeamBody(BaseModel):
    team_id: str
    team_name: str = ""
    opponent_name: str = ""
    amount: Optional[float] = None


class MatchFeeBody(BaseModel):
    tournament_id: str = ""
    match_title: str = ""
    match_date: str = ""
    currency: str = "INR"
    payment_details: str = ""
    teams: List[MatchFeeTeamBody]


class FeeMatchRef(BaseModel):
    match_id: str
    status: str = ""
    team_a_id: str = ""
    team_a: str = ""
    team_b_id: str = ""
    team_b: str = ""
    start_time: str = ""
    match_title: str = ""


class FeeMatchesBody(BaseModel):
    matches: List[FeeMatchRef] = []
    include_completed: bool = False


class NotifyFeeBody(BaseModel):
    team_id: str = ""
    amount: Optional[float] = None
    payment_details: str = ""
    match_title: str = ""
    match_date: str = ""
    match_status: str = ""
    tournament_id: str = ""
    force: bool = False


# ---------------- Health ----------------

@api_router.get("/")
async def root():
    return {
        "service": "CricHeroes Scorecard API",
        "version": "1.0.0",
        "auth_required": bool(API_AUTH_TOKEN),
        "endpoints": {
            "single_json": "GET /api/cricheroes/{match_id}",
            "single_csv": "GET /api/cricheroes/{match_id}/csv",
            "any_url_json_get": "GET /api/json?url=...",
            "any_url_json_post": "POST /api/json  {url}",
            "any_url_csv_get": "GET /api/csv?url=...",
            "any_url_csv_post": "POST /api/csv  {url}",
            "batch": "POST /api/cricheroes/batch  {match_ids[] | urls[]}",
            "player_json": "GET /api/cricheroes/player/{player_id}",
            "player_tournaments": "GET /api/cricheroes/player/{player_id}/tournaments?name=30YCA",
            "career_json": "POST /api/cricheroes/tournaments {tournament_ids[]}",
            "organizer_json": "GET /api/cricheroes/organizer/{organizer_id}",
            "organizer_import": "POST /api/cricheroes/organizer/{organizer_id}/import",
            "tournament_json": "GET /api/cricheroes/tournament/{tournament_id}",
            "tournament_csv": "GET /api/cricheroes/tournament/{tournament_id}/csv",
            "team_spoc": "PUT /api/admin/teams/{team_id}/spoc",
            "tournament_match_fee": "PUT /api/admin/tournaments/{tournament_id}/match-fee",
            "match_fees": "PUT /api/admin/matches/{match_id}/fees",
            "fee_notify": "POST /api/admin/matches/{match_id}/fees/notify",
        },
    }


# ---------------- Helpers ----------------

def _scrape_or_400(url: str) -> dict:
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    try:
        return scrape(url)
    except CloudflareBlocked as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ScrapeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("scrape failed")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


def _csv_response(data: dict) -> PlainTextResponse:
    if data.get("kind") == "tournament":
        csv_text = tournament_to_csv(data)
        title_source = (data.get("tournament") or {}).get("name") or f"tournament-{data.get('tournament_id')}"
    else:
        csv_text = scorecard_to_csv(data)
        title_source = data.get("match_title") or "scorecard"
    safe_title = "".join(
        c if c.isalnum() or c in "-_ " else "_" for c in title_source
    )[:80].strip() or "scorecard"
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.csv"'},
    )


# ---------------- CSV endpoints ----------------

@api_router.get("/csv")
async def csv_get(url: str, _auth: None = Depends(require_api_token)):
    return _csv_response(_scrape_or_400(url))


@api_router.post("/csv")
async def csv_post(req: ScrapeRequest, _auth: None = Depends(require_api_token)):
    return _csv_response(_scrape_or_400((req.url or "").strip()))


# ---------------- JSON endpoints ----------------

@api_router.get("/json")
async def json_get(url: str, _auth: None = Depends(require_api_token)):
    return _scrape_or_400(url)


@api_router.post("/json")
async def json_post(req: ScrapeRequest, _auth: None = Depends(require_api_token)):
    return _scrape_or_400((req.url or "").strip())


# ---------------- CricHeroes shortcuts ----------------

def _cricheroes_url(match_id: str) -> str:
    return f"https://cricheroes.com/scorecard/{match_id}/individual/match/live"


@api_router.get("/cricheroes/{match_id}")
async def cricheroes_json(match_id: str, _auth: None = Depends(require_api_token)):
    if not match_id.isdigit():
        raise HTTPException(status_code=400, detail="match_id must be numeric")
    return _scrape_or_400(_cricheroes_url(match_id))


@api_router.get("/cricheroes/{match_id}/csv")
async def cricheroes_csv(match_id: str, _auth: None = Depends(require_api_token)):
    if not match_id.isdigit():
        raise HTTPException(status_code=400, detail="match_id must be numeric")
    return _csv_response(_scrape_or_400(_cricheroes_url(match_id)))


def _tournament_or_400(tournament_id: str, include_scorecards: bool, scorecard_limit: int) -> dict:
    if not str(tournament_id).isdigit():
        raise HTTPException(status_code=400, detail="tournament_id must be numeric")
    try:
        return scrape_tournament(
            tournament_id,
            include_scorecards=include_scorecards,
            scorecard_limit=scorecard_limit,
        )
    except ScrapeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("tournament scrape failed")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


@api_router.get("/cricheroes/player/{player_id}/tournaments")
async def cricheroes_player_tournaments(
    player_id: str,
    name: str = "",
    _auth: None = Depends(require_api_token),
):
    """Tournaments this player actually played. Pass name=30YCA to keep only those."""
    if not str(player_id).isdigit():
        raise HTTPException(status_code=400, detail="player_id must be numeric")
    try:
        rows = tournaments_for_player(player_id, name)
    except ScrapeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("player tournaments failed")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")
    return {"player_id": player_id, "name": name, "tournaments": rows, "total": len(rows)}


@api_router.get("/cricheroes/organizer/{organizer_id}")
async def cricheroes_organizer_json(organizer_id: str, _auth: None = Depends(require_api_token)):
    """Every tournament hosted by an organiser. 30 YCA is 192049."""
    if not str(organizer_id).isdigit():
        raise HTTPException(status_code=400, detail="organizer_id must be numeric")
    try:
        return list_organizer_tournaments(organizer_id)
    except ScrapeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("organizer list failed")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


@api_router.post("/cricheroes/organizer/{organizer_id}/import")
async def cricheroes_organizer_import(
    organizer_id: str,
    req: OrganizerImport,
    _auth: None = Depends(require_api_token),
):
    """Import one slice of an organiser's tournaments. Repeat with next_offset."""
    if not str(organizer_id).isdigit():
        raise HTTPException(status_code=400, detail="organizer_id must be numeric")
    try:
        return scrape_organizer(
            organizer_id,
            include_scorecards=req.include_scorecards,
            scorecard_limit=req.scorecard_limit,
            offset=req.offset,
            limit=req.limit,
        )
    except ScrapeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("organizer import failed")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


@api_router.post("/cricheroes/tournaments")
async def cricheroes_tournaments_post(req: TournamentsQuery, _auth: None = Depends(require_api_token)):
    """Scrape several past tournaments. Career stats count only these tournaments."""
    try:
        return scrape_tournaments(
            req.tournament_ids,
            include_scorecards=req.include_scorecards,
            scorecard_limit=req.scorecard_limit,
        )
    except ScrapeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("career scrape failed")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


@api_router.get("/cricheroes/player/{player_id}")
async def cricheroes_player_json(player_id: str, _auth: None = Depends(require_api_token)):
    """Photo, batting hand, bowling style, and role for a scorecard player id."""
    if not str(player_id).isdigit():
        raise HTTPException(status_code=400, detail="player_id must be numeric")
    try:
        return scrape_player(player_id)
    except ScrapeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("player profile failed")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


@api_router.get("/cricheroes/tournament/{tournament_id}")
async def cricheroes_tournament_json(
    tournament_id: str,
    include_scorecards: bool = True,
    scorecard_limit: int = 100,
    _auth: None = Depends(require_api_token),
):
    """Every match in a CricHeroes tournament, plus teams, points table, and scorecards."""
    return _tournament_or_400(tournament_id, include_scorecards, scorecard_limit)


@api_router.get("/cricheroes/tournament/{tournament_id}/csv")
async def cricheroes_tournament_csv(
    tournament_id: str,
    include_scorecards: bool = True,
    scorecard_limit: int = 100,
    _auth: None = Depends(require_api_token),
):
    return _csv_response(_tournament_or_400(tournament_id, include_scorecards, scorecard_limit))


@api_router.post("/cricheroes/tournament")
async def cricheroes_tournament_post(req: TournamentQuery, _auth: None = Depends(require_api_token)):
    return _tournament_or_400(req.tournament_id, req.include_scorecards, req.scorecard_limit)


# ---------------- Admin fees and WhatsApp ----------------

def _fee_or_http(exc: FeeError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@api_router.put("/admin/teams/{team_id}/spoc")
async def admin_save_spoc(team_id: str, req: TeamSpocBody, _auth: None = Depends(require_api_token)):
    """Save the team SPOC who receives the match-fee WhatsApp."""
    try:
        return fee_store.save_spoc(
            team_id,
            req.spoc_name,
            req.phone,
            country_code=req.country_code,
            team_name=req.team_name,
            tournament_id=req.tournament_id,
        )
    except FeeError as exc:
        raise _fee_or_http(exc)


@api_router.get("/admin/teams/{team_id}/spoc")
async def admin_get_spoc(
    team_id: str,
    tournament_id: str = "",
    _auth: None = Depends(require_api_token),
):
    try:
        row = fee_store.get_spoc(team_id, tournament_id)
    except FeeError as exc:
        raise _fee_or_http(exc)
    if row is None:
        raise HTTPException(status_code=404, detail="Team SPOC not found")
    return row


@api_router.get("/admin/spocs")
async def admin_list_spocs(tournament_id: str = "", _auth: None = Depends(require_api_token)):
    try:
        rows = fee_store.list_spocs(tournament_id)
    except FeeError as exc:
        raise _fee_or_http(exc)
    return {"tournament_id": tournament_id, "spocs": rows, "total": len(rows)}


@api_router.delete("/admin/teams/{team_id}/spoc")
async def admin_delete_spoc(
    team_id: str,
    tournament_id: str = "",
    _auth: None = Depends(require_api_token),
):
    try:
        deleted = fee_store.delete_spoc(team_id, tournament_id)
    except FeeError as exc:
        raise _fee_or_http(exc)
    if not deleted:
        raise HTTPException(status_code=404, detail="Team SPOC not found")
    return {"deleted": True, "team_id": team_id, "tournament_id": tournament_id}


@api_router.put("/admin/tournaments/{tournament_id}/match-fee")
async def admin_save_tournament_fee(
    tournament_id: str,
    req: TournamentMatchFeeBody,
    _auth: None = Depends(require_api_token),
):
    """Per-match fee each team pays. Lovable shows this on every new upcoming match."""
    try:
        return fee_store.save_tournament_fee(
            tournament_id,
            req.amount,
            req.currency,
            req.payment_details,
        )
    except FeeError as exc:
        raise _fee_or_http(exc)


@api_router.get("/admin/tournaments/{tournament_id}/match-fee")
async def admin_get_tournament_fee(tournament_id: str, _auth: None = Depends(require_api_token)):
    try:
        row = fee_store.get_tournament_fee(tournament_id)
    except FeeError as exc:
        raise _fee_or_http(exc)
    if row is None:
        raise HTTPException(status_code=404, detail="Tournament match fee is not set")
    return row


@api_router.post("/admin/tournaments/{tournament_id}/match-fee/apply")
async def admin_apply_tournament_fee(
    tournament_id: str,
    req: FeeMatchesBody,
    _auth: None = Depends(require_api_token),
):
    """Attach the tournament fee to upcoming matches Lovable already loaded."""
    try:
        return fee_store.apply_tournament_fee(
            tournament_id,
            [match.model_dump() for match in req.matches],
            include_completed=req.include_completed,
        )
    except FeeError as exc:
        raise _fee_or_http(exc)


@api_router.post("/admin/tournaments/{tournament_id}/fees/preview")
async def admin_preview_fees(
    tournament_id: str,
    req: FeeMatchesBody,
    _auth: None = Depends(require_api_token),
):
    """Join scraped matches with the saved fee and each team's SPOC."""
    try:
        return fee_store.preview_fees(
            tournament_id,
            [match.model_dump() for match in req.matches],
        )
    except FeeError as exc:
        raise _fee_or_http(exc)


@api_router.get("/admin/tournaments/{tournament_id}/fees")
async def admin_list_tournament_fees(tournament_id: str, _auth: None = Depends(require_api_token)):
    try:
        rows = fee_store.list_tournament_match_fees(tournament_id)
    except FeeError as exc:
        raise _fee_or_http(exc)
    return {"tournament_id": tournament_id, "fees": rows, "total": len(rows)}


@api_router.put("/admin/matches/{match_id}/fees")
async def admin_save_match_fees(match_id: str, req: MatchFeeBody, _auth: None = Depends(require_api_token)):
    """Set the amount each team pays. Call this after the match is completed."""
    try:
        rows = fee_store.save_match_fees(
            match_id,
            [team.model_dump() for team in req.teams],
            tournament_id=req.tournament_id,
            match_title=req.match_title,
            match_date=req.match_date,
            currency=req.currency,
            payment_details=req.payment_details,
        )
    except FeeError as exc:
        raise _fee_or_http(exc)
    return {"match_id": match_id, "fees": rows}


@api_router.get("/admin/matches/{match_id}/fees")
async def admin_get_match_fees(match_id: str, _auth: None = Depends(require_api_token)):
    try:
        rows = fee_store.list_match_fees(match_id)
    except FeeError as exc:
        raise _fee_or_http(exc)
    return {"match_id": match_id, "fees": rows}


@api_router.post("/admin/matches/{match_id}/fees/notify")
async def admin_notify_match_fee(
    match_id: str,
    req: NotifyFeeBody,
    _auth: None = Depends(require_api_token),
):
    """Return the api.whatsapp.com link that opens the admin's WhatsApp with the fee message."""
    try:
        return fee_store.notify_match_fee(
            match_id,
            team_id=req.team_id,
            amount=req.amount,
            payment_details=req.payment_details,
            match_title=req.match_title,
            match_date=req.match_date,
            match_status=req.match_status,
            tournament_id=req.tournament_id,
            force=req.force,
        )
    except FeeError as exc:
        raise _fee_or_http(exc)


@api_router.post("/admin/matches/{match_id}/fees/{team_id}/paid")
async def admin_mark_fee_paid(match_id: str, team_id: str, _auth: None = Depends(require_api_token)):
    try:
        return fee_store.mark_fee_paid(match_id, team_id)
    except FeeError as exc:
        raise _fee_or_http(exc)


# ---------------- Batch ----------------

MAX_BATCH = 50
BATCH_CONCURRENCY = 5


async def _scrape_one_safe(url: str, key: str) -> dict:
    try:
        data = await asyncio.to_thread(scrape, url)
        return {"key": key, "url": url, "ok": True, "data": data}
    except CloudflareBlocked as e:
        return {"key": key, "url": url, "ok": False, "error": str(e), "status": 422}
    except ScrapeError as e:
        return {"key": key, "url": url, "ok": False, "error": str(e), "status": 422}
    except Exception as e:
        logger.exception("batch scrape failed for %s", url)
        return {"key": key, "url": url, "ok": False, "error": str(e), "status": 500}


@api_router.post("/cricheroes/batch")
async def batch(req: BatchRequest, _auth: None = Depends(require_api_token)):
    tasks: list = []
    seen: set = set()

    async def _immediate(item):
        return item

    for mid in (req.match_ids or []):
        mid = str(mid).strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        if not mid.isdigit():
            tasks.append(_immediate({"key": mid, "url": "", "ok": False, "error": "match_id must be numeric", "status": 400}))
            continue
        tasks.append(_scrape_one_safe(_cricheroes_url(mid), mid))

    for u in (req.urls or []):
        u = str(u).strip()
        if not u or u in seen:
            continue
        seen.add(u)
        if not (u.startswith("http://") or u.startswith("https://")):
            tasks.append(_immediate({"key": u, "url": u, "ok": False, "error": "url must start with http(s)://", "status": 400}))
            continue
        tasks.append(_scrape_one_safe(u, u))

    if not tasks:
        raise HTTPException(status_code=400, detail="Provide match_ids or urls (non-empty).")
    if len(tasks) > MAX_BATCH:
        raise HTTPException(status_code=400, detail=f"Batch size {len(tasks)} exceeds max {MAX_BATCH}.")

    sem = asyncio.Semaphore(BATCH_CONCURRENCY)

    async def guarded(t):
        async with sem:
            return await t

    results = await asyncio.gather(*[guarded(t) for t in tasks])
    successful = sum(1 for r in results if r.get("ok"))
    return {
        "total": len(results),
        "successful": successful,
        "failed": len(results) - successful,
        "results": results,
    }


# ---------------- App setup ----------------

app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

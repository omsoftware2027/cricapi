"""
CricHeroes Scorecard API — pure API service.
No database, no interactive UI. Consumers (Lovable, n8n, Zapier, etc.) call these endpoints.
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

from scrapers import scrape, scorecard_to_csv, ScrapeError, CloudflareBlocked  # noqa: E402


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
    csv_text = scorecard_to_csv(data)
    safe_title = "".join(
        c if c.isalnum() or c in "-_ " else "_" for c in (data.get("match_title") or "scorecard")
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

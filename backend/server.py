from fastapi import FastAPI, APIRouter, HTTPException, Header, Depends
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
import uuid
from datetime import datetime, timezone

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from scrapers import scrape, scorecard_to_csv, ScrapeError, CloudflareBlocked


mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Optional bearer token protecting the /api/csv, /api/cricheroes/{id}/csv endpoints.
# If unset/empty, the endpoints stay open (useful in preview & local dev).
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


app = FastAPI()
api_router = APIRouter(prefix="/api")


# ---------------- Models ----------------

class ScrapeRequest(BaseModel):
    url: str


class ScorecardListItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    url: str
    source: str
    match_title: str
    result: str = ""
    scraped_at: datetime


class Scorecard(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    url: str
    source: str
    match_title: str
    result: str = ""
    venue: str = ""
    toss: str = ""
    innings: list = []
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def _mongo_to_scorecard(doc: dict) -> dict:
    if not doc:
        return doc
    doc.pop("_id", None)
    if isinstance(doc.get("scraped_at"), str):
        try:
            doc["scraped_at"] = datetime.fromisoformat(doc["scraped_at"])
        except ValueError:
            pass
    return doc


# ---------------- Routes ----------------

@api_router.get("/")
async def root():
    return {
        "message": "Cricket Scorecard Scraper API",
        "auth_required": bool(API_AUTH_TOKEN),
    }


@api_router.post("/scrape", response_model=Scorecard)
async def scrape_url(req: ScrapeRequest):
    url = (req.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    try:
        data = scrape(url)
    except CloudflareBlocked as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ScrapeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("scrape failed")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

    sc = Scorecard(
        url=data["url"],
        source=data["source"],
        match_title=data["match_title"],
        result=data.get("result", ""),
        venue=data.get("venue", ""),
        toss=data.get("toss", ""),
        innings=data.get("innings", []),
    )
    doc = sc.model_dump()
    doc["scraped_at"] = sc.scraped_at.isoformat()
    await db.scorecards.insert_one(doc)
    return sc


@api_router.get("/scorecards", response_model=List[ScorecardListItem])
async def list_scorecards():
    docs = await db.scorecards.find(
        {},
        {"_id": 0, "id": 1, "url": 1, "source": 1, "match_title": 1, "result": 1, "scraped_at": 1},
    ).sort("scraped_at", -1).to_list(200)
    for d in docs:
        if isinstance(d.get("scraped_at"), str):
            try:
                d["scraped_at"] = datetime.fromisoformat(d["scraped_at"])
            except ValueError:
                pass
    return docs


@api_router.get("/scorecards/{sc_id}", response_model=Scorecard)
async def get_scorecard(sc_id: str):
    doc = await db.scorecards.find_one({"id": sc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Scorecard not found")
    return _mongo_to_scorecard(doc)


@api_router.get("/scorecards/{sc_id}/csv")
async def download_csv(sc_id: str):
    doc = await db.scorecards.find_one({"id": sc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Scorecard not found")
    csv_text = scorecard_to_csv(doc)
    safe_title = "".join(c if c.isalnum() or c in "-_ " else "_" for c in (doc.get("match_title") or "scorecard"))[:80].strip() or "scorecard"
    filename = f"{safe_title}.csv"
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _scrape_and_build_csv(url: str, save: bool):
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    try:
        data = scrape(url)
    except CloudflareBlocked as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ScrapeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("scrape failed")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")
    csv_text = scorecard_to_csv(data)
    safe_title = "".join(
        c if c.isalnum() or c in "-_ " else "_" for c in (data.get("match_title") or "scorecard")
    )[:80].strip() or "scorecard"
    return csv_text, safe_title, data


@api_router.get("/csv")
async def scrape_to_csv_get(url: str, save: bool = True, _auth: None = Depends(require_api_token)):
    """Scrape a scorecard URL and return the CSV directly.

    Example: GET /api/csv?url=https://cricheroes.com/scorecard/25954216/...
    Optional query: ?save=false to skip persisting to history.
    """
    csv_text, safe_title, data = _scrape_and_build_csv(url, save)
    if save:
        sc = Scorecard(
            url=data["url"], source=data["source"], match_title=data["match_title"],
            result=data.get("result", ""), venue=data.get("venue", ""),
            toss=data.get("toss", ""), innings=data.get("innings", []),
        )
        doc = sc.model_dump()
        doc["scraped_at"] = sc.scraped_at.isoformat()
        await db.scorecards.insert_one(doc)
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.csv"'},
    )


@api_router.post("/csv")
async def scrape_to_csv_post(req: ScrapeRequest, save: bool = True, _auth: None = Depends(require_api_token)):
    """Same as GET /api/csv but takes {"url": "..."} as JSON body."""
    csv_text, safe_title, data = _scrape_and_build_csv((req.url or "").strip(), save)
    if save:
        sc = Scorecard(
            url=data["url"], source=data["source"], match_title=data["match_title"],
            result=data.get("result", ""), venue=data.get("venue", ""),
            toss=data.get("toss", ""), innings=data.get("innings", []),
        )
        doc = sc.model_dump()
        doc["scraped_at"] = sc.scraped_at.isoformat()
        await db.scorecards.insert_one(doc)
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.csv"'},
    )


@api_router.get("/cricheroes/{match_id}/csv")
async def cricheroes_match_csv(match_id: str, save: bool = True, _auth: None = Depends(require_api_token)):
    """CricHeroes shortcut: pass just the numeric match_id and get the CSV.

    Example: GET /api/cricheroes/25954216/csv
    """
    if not match_id.isdigit():
        raise HTTPException(status_code=400, detail="match_id must be numeric")
    url = f"https://cricheroes.com/scorecard/{match_id}/individual/match/live"
    csv_text, safe_title, data = _scrape_and_build_csv(url, save)
    if save:
        sc = Scorecard(
            url=data["url"], source=data["source"], match_title=data["match_title"],
            result=data.get("result", ""), venue=data.get("venue", ""),
            toss=data.get("toss", ""), innings=data.get("innings", []),
        )
        doc = sc.model_dump()
        doc["scraped_at"] = sc.scraped_at.isoformat()
        await db.scorecards.insert_one(doc)
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.csv"'},
    )


# ---------------- JSON endpoints (for API consumers like Supabase / Lovable) ----------------

def _scrape_json_only(url: str):
    """Scrape a URL and return the nested-JSON payload (no DB save)."""
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


@api_router.get("/json")
async def scrape_to_json_get(url: str, _auth: None = Depends(require_api_token)):
    """Scrape any supported URL and return nested JSON (does NOT save to history).

    Example: GET /api/json?url=https://cricheroes.com/scorecard/25954216/...
    Response shape: {source, url, match_title, result, venue, toss, innings: [{
        innings_number, team, total, overs, batting: [{player_id, batter, dismissal, runs, balls, fours, sixes, sr}],
        bowling: [{player_id, bowler, overs, maidens, runs, wickets, no_balls, wides, econ}],
        yet_to_bat: [{player_id, name}], extras, total_line, fall_of_wickets
    }]}
    """
    return _scrape_json_only(url)


@api_router.post("/json")
async def scrape_to_json_post(req: ScrapeRequest, _auth: None = Depends(require_api_token)):
    """Same as GET /api/json but takes {"url": "..."} as JSON body."""
    return _scrape_json_only((req.url or "").strip())


@api_router.get("/cricheroes/{match_id}")
async def cricheroes_match_json(match_id: str, _auth: None = Depends(require_api_token)):
    """CricHeroes shortcut: pass just the numeric match_id and get nested JSON.

    Example: GET /api/cricheroes/25954216
    """
    if not match_id.isdigit():
        raise HTTPException(status_code=400, detail="match_id must be numeric")
    url = f"https://cricheroes.com/scorecard/{match_id}/individual/match/live"
    return _scrape_json_only(url)


@api_router.delete("/scorecards/{sc_id}")
async def delete_scorecard(sc_id: str):
    res = await db.scorecards.delete_one({"id": sc_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Scorecard not found")
    return {"deleted": True}


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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

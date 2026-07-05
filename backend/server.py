from fastapi import FastAPI, APIRouter, HTTPException
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

from scrapers import scrape, scorecard_to_csv, ScrapeError, CloudflareBlocked


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

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
    return {"message": "Cricket Scorecard Scraper API"}


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

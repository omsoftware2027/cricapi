# Cricket Scorecard Scraper — PRD

## Original Problem
User: "can you create a webscrapping project, if i paste a link, i need to download the data in csv a cricket Score card"

User's initial URL was a **CricHeroes** scorecard. CricHeroes is Cloudflare-protected at the IP layer — this app now bypasses it via a **Bright Data residential proxy** provided by the user.

## Architecture
- **Backend**: FastAPI (`server.py`) + scrapers module (`scrapers.py`) + config (`config.py`)
- **Frontend**: React 19 + Tailwind (Retool/Linear aesthetic, Cabinet Grotesk + IBM Plex fonts)
- **Storage**: MongoDB (`test_database.scorecards`) for history
- **Scraping**:
  - `cricbuzz.com` → `curl_cffi` (Chrome TLS fingerprint) + BeautifulSoup DOM parsing
  - `espncricinfo.com` → `curl_cffi` + BeautifulSoup DOM parsing
  - `cricheroes.com` → Bright Data residential proxy + private JSON API `api.cricheroes.in/api/v1/scorecard/get-scorecard/{match_id}` (`api-key: cr!CkH3r0s`, `udid` header)

## Endpoints
- `POST /api/scrape` — takes `{url}`, returns scorecard, persists to Mongo
- `GET /api/scorecards` — history list
- `GET /api/scorecards/{id}` — one scorecard
- `GET /api/scorecards/{id}/csv` — CSV download with `attachment` header
- `DELETE /api/scorecards/{id}` — remove

## Features Implemented (Aug 07, 2026)
- Paste-and-scrape UI with client-side URL validation and red error banner
- Full batting (Batter, Dismissal, R, B, 4s, 6s, SR) and bowling (Bowler, O, M, R, W, NB, WD, ECON) tables
- Innings tabs when multi-innings match
- Single-file CSV export (MATCH INFO + per-innings BATTING/BOWLING/EXTRAS/FOW)
- History sidebar with re-view + delete
- Cricbuzz + ESPN Cricinfo scrapers (Jul 05, 2026)
- CricHeroes scraper via Bright Data proxy (Aug 07, 2026)

## Env Vars (backend/.env)
- `MONGO_URL`, `DB_NAME`, `CORS_ORIGINS`
- `BRIGHTDATA_PROXY_HOST=brd.superproxy.io`
- `BRIGHTDATA_PROXY_PORT=44445`
- `BRIGHTDATA_PROXY_USER` (user-supplied)
- `BRIGHTDATA_PROXY_PASS` (user-supplied)

## Backlog / Not Implemented
- P1: Bulk download (multiple scorecards → ZIP)
- P1: Fall-of-wickets and partnerships as separate CSV sections/columns
- P2: Cost meter for Bright Data usage
- P2: Compare-two-matches view
- P2: Public shareable link for a saved scorecard

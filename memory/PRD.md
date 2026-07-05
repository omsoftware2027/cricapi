# Cricket Scorecard Scraper — PRD

## Original Problem Statement
> can you create a webscrapping project, if i paste a link , i need to download the data in csv a cricket Score card
> Example link user provided: https://cricheroes.com/scorecard/25954216/individual/professional-cricket-academy-vs-st-martins-cricket-academy/live

## User Choices (from ask_human)
- Any pasted link (generic scraper)
- CSV: full match summary + both batting + bowling (single file)
- Both innings in one CSV
- Preview scorecard in tables before downloading
- Keep history of past scraped scorecards

## Tech Stack
- Backend: FastAPI + curl_cffi (Chrome TLS impersonation) + BeautifulSoup4/lxml + MongoDB
- Frontend: React 19 + Tailwind + lucide-react + axios
- Design: Swiss / high-contrast "Retool-Linear" (crimson #BE123C accent, IBM Plex + Cabinet Grotesk)

## Supported sources
- ✅ Cricbuzz (fully working)
- ✅ ESPN Cricinfo (generic parser)
- ❌ CricHeroes — blocked by Cloudflare from server IPs (surfaces a clear 422 error message)

## Architecture
- `backend/scrapers.py` — `scrape(url)`, `scorecard_to_csv(sc)`, `detect_source()`, `ScrapeError`, `CloudflareBlocked`
- `backend/server.py` — routes:
  - `POST /api/scrape` → scrape URL and persist
  - `GET  /api/scorecards` → list (newest first)
  - `GET  /api/scorecards/{id}` → single scorecard
  - `GET  /api/scorecards/{id}/csv` → download CSV
  - `DELETE /api/scorecards/{id}` → remove
- `frontend/src/App.js` — sidebar (history), URL input, preview with innings tabs, batting & bowling tables, Download CSV button

## Implemented (Jan 2026)
- URL paste → server-side scrape (Cricbuzz DOM parser: batting rows w/ dismissal, bowling rows, extras, total, DNB, FOW; ESPN generic table parser)
- Live preview with match meta (title, venue, toss, result), innings tabs, batting (7 cols) & bowling (8 cols) tables
- Single-file CSV export via `/csv` endpoint
- History sidebar (list + click-to-reload + delete-with-confirm)
- Client-side URL validation with unified `data-testid="error-message"` banner
- Clear error messaging for Cloudflare-blocked sites (cricheroes)
- Full `data-testid` coverage on every interactive/data element

## Testing
- Backend (iteration_1): 10/10 endpoint scenarios pass
- Frontend E2E (iteration_2): 6/6 flows pass (validation, scrape, tab switch, CSV download 200, history click reload, delete confirm)

## Backlog / Next
- P1: Bypass Cloudflare (FlareSolverr or paid scraping proxy) to enable CricHeroes
- P1: Match-level XLSX export (multi-sheet: Info, Inn1-Batting, Inn1-Bowling, ...)
- P2: Auto-refresh live scorecards on interval
- P2: Search / filter / bulk-export history
- P2: Share-view link (public read-only URL for a stored scorecard)

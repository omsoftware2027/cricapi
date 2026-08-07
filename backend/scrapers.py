"""
Cricket scorecard scrapers for multiple sites.
Supports: Cricbuzz, ESPN Cricinfo, Cricheroes (via Bright Data residential proxy)
"""
from __future__ import annotations
import re
from typing import Optional
from urllib.parse import urlparse

import requests as _requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cc_requests

from config import (
    brightdata_proxy_url,
    CRICHEROES_API_BASE,
    CRICHEROES_API_KEY,
    CRICHEROES_UDID,
    CRICHEROES_UA,
)


class ScrapeError(Exception):
    pass


class CloudflareBlocked(ScrapeError):
    pass


def _fetch_html(url: str) -> str:
    try:
        r = cc_requests.get(url, impersonate="chrome", timeout=25)
    except Exception as e:
        raise ScrapeError(f"Network error while fetching URL: {e}")

    if r.status_code == 403 or (
        "Just a moment" in r.text or "cf-error-details" in r.text or "Attention Required" in r.text
    ):
        raise CloudflareBlocked(
            "The target site is protected by Cloudflare and blocks server requests. "
            "Please try a Cricbuzz or ESPN Cricinfo scorecard URL instead."
        )
    if r.status_code >= 400:
        raise ScrapeError(f"HTTP {r.status_code} returned by target site.")
    return r.text


def detect_source(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "cricbuzz" in host:
        return "cricbuzz"
    if "cricinfo" in host or "espncricinfo" in host:
        return "cricinfo"
    if "cricheroes" in host:
        return "cricheroes"
    return "unknown"


def scrape(url: str) -> dict:
    src = detect_source(url)
    if src == "cricheroes":
        # CricHeroes has its own API path; no HTML fetch needed
        return _scrape_cricheroes(url)
    html = _fetch_html(url)
    if src == "cricbuzz":
        return _scrape_cricbuzz(html, url)
    if src == "cricinfo":
        return _scrape_cricinfo(html, url)
    raise ScrapeError(
        "Unsupported site. Please paste a Cricbuzz, ESPN Cricinfo or CricHeroes scorecard URL."
    )


# --------------------------- CRICHEROES ---------------------------

_CRICHEROES_MATCH_RE = re.compile(r"/scorecard/(\d+)")


def _cricheroes_match_id(url: str) -> Optional[str]:
    m = _CRICHEROES_MATCH_RE.search(url)
    return m.group(1) if m else None


def _scrape_cricheroes(url: str) -> dict:
    match_id = _cricheroes_match_id(url)
    if not match_id:
        raise ScrapeError(
            "Could not extract the match ID from that CricHeroes URL. "
            "It should look like https://cricheroes.com/scorecard/<match_id>/..."
        )

    proxy = brightdata_proxy_url()
    if not proxy:
        raise ScrapeError(
            "CricHeroes is Cloudflare-protected. A Bright Data residential proxy is required. "
            "Please configure BRIGHTDATA_PROXY_USER and BRIGHTDATA_PROXY_PASS on the server."
        )

    api_url = f"{CRICHEROES_API_BASE}/scorecard/get-scorecard/{match_id}"
    headers = {
        "User-Agent": CRICHEROES_UA,
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://cricheroes.com",
        "Referer": "https://cricheroes.com/",
        "api-key": CRICHEROES_API_KEY,
        "udid": CRICHEROES_UDID,
        "device-type": "Chrome: 128.0.0.0",
    }
    try:
        r = _requests.get(
            api_url,
            headers=headers,
            proxies={"http": proxy, "https": proxy},
            verify=False,
            timeout=90,
        )
    except Exception as e:
        raise ScrapeError(f"Failed to reach CricHeroes via proxy: {e}")

    if r.status_code != 200:
        raise ScrapeError(f"CricHeroes API returned HTTP {r.status_code}.")
    try:
        payload = r.json()
    except ValueError:
        raise ScrapeError("CricHeroes API returned invalid JSON.")
    if not payload.get("status"):
        err = (payload.get("error") or {}).get("message") or "Unknown error"
        raise ScrapeError(f"CricHeroes API error: {err}")

    d = payload["data"]

    # Match meta
    team_a = d.get("team_a") or {}
    team_b = d.get("team_b") or {}
    match_title = f"{team_a.get('name','')} vs {team_b.get('name','')}"
    tour = d.get("tournament_name") or ""
    if tour:
        match_title = f"{match_title}, {tour}"
    venue_parts = [d.get("ground_name") or "", d.get("city_name") or ""]
    venue = ", ".join([p for p in venue_parts if p])
    toss = d.get("toss_details") or ""
    result = (d.get("match_summary") or {}).get("summary") or d.get("match_result") or ""

    # Innings — merge each team's scorecard entries and sort by inning number
    innings_list = []
    for team in (team_a, team_b):
        team_name = team.get("name") or ""
        for sc in team.get("scorecard") or []:
            inn_num = sc.get("inning") or 0
            # Match up header info from team.innings by inning number
            inn_meta = {}
            for inn in team.get("innings") or []:
                if inn.get("inning") == inn_num:
                    inn_meta = inn
                    break

            total_run = inn_meta.get("total_run", "")
            total_wicket = inn_meta.get("total_wicket", "")
            overs_played = inn_meta.get("overs_played", "")
            total = f"{total_run}/{total_wicket}" if total_run != "" else ""

            # Batting
            batting_rows = []
            for b in sc.get("batting") or []:
                batting_rows.append({
                    "player_id": str(b.get("player_id", "")),
                    "batter": (b.get("name") or "").strip(),
                    "dismissal": (b.get("how_to_out") or "").strip(),
                    "runs": str(b.get("runs", "")),
                    "balls": str(b.get("balls", "")),
                    "fours": str(b.get("4s", "")),
                    "sixes": str(b.get("6s", "")),
                    "sr": str(b.get("SR", "")),
                })

            # Bowling
            bowling_rows = []
            for bw in sc.get("bowling") or []:
                overs = bw.get("overs", "")
                balls = bw.get("balls", "")
                # cricheroes stores overs and balls separately; overs like 3, balls like 2 => "3.2"
                if balls:
                    overs_str = f"{overs}.{balls}"
                else:
                    overs_str = str(overs)
                bowling_rows.append({
                    "player_id": str(bw.get("player_id", "")),
                    "bowler": (bw.get("name") or "").strip(),
                    "overs": overs_str,
                    "maidens": str(bw.get("maidens", "")),
                    "runs": str(bw.get("runs", "")),
                    "wickets": str(bw.get("wickets", "")),
                    "no_balls": str(bw.get("noball", "")),
                    "wides": str(bw.get("wide", "")),
                    "econ": str(bw.get("economy_rate", "")),
                })

            # Extras summary text
            extras_obj = sc.get("extras") or {}
            extras_str = ""
            if extras_obj:
                extras_str = f"Extras {extras_obj.get('total','')} {extras_obj.get('summary','')}".strip()

            total_line = ""
            if total_run != "":
                total_line = f"Total {total_run}/{total_wicket} ({overs_played} Overs)"

            # Yet to bat
            dnb = sc.get("to_be_bat") or []
            dnb_str = ""
            if isinstance(dnb, list) and dnb:
                names = [x.get("name","") if isinstance(x, dict) else str(x) for x in dnb]
                dnb_str = "Yet to bat: " + ", ".join([n for n in names if n])

            # Fall of wickets - cricheroes has a summary string ready to use
            fow_obj = sc.get("fall_of_wicket") or {}
            fow_str = ""
            if isinstance(fow_obj, dict):
                summary = fow_obj.get("summary")
                if summary:
                    fow_str = f"Fall of Wickets: {summary}"

            innings_list.append({
                "innings_number": int(inn_num) if inn_num else len(innings_list) + 1,
                "team": team_name,
                "total": total,
                "overs": str(overs_played),
                "score_header": f"{team_name} {total} ({overs_played} Ov)".strip(),
                "batting": batting_rows,
                "bowling": bowling_rows,
                "extras": extras_str,
                "total_line": total_line,
                "did_not_bat": dnb_str,
                "fall_of_wickets": fow_str,
            })

    innings_list.sort(key=lambda x: x["innings_number"])

    if not innings_list:
        raise ScrapeError("CricHeroes returned no innings data for this match.")

    return {
        "source": "cricheroes",
        "url": url,
        "match_title": match_title.strip(", "),
        "result": result,
        "venue": venue,
        "toss": toss,
        "innings": innings_list,
    }


# --------------------------- CRICBUZZ ---------------------------

def _scrape_cricbuzz(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    title_el = soup.select_one("h1")
    match_title = title_el.get_text(" ", strip=True) if title_el else ""
    # Cricbuzz duplicates title; keep the second half after ' - Scorecard'
    if " - Scorecard" in match_title:
        match_title = match_title.split(" - Scorecard")[0]
    match_title = re.sub(r"\s+", " ", match_title).strip()

    # Match result / status
    result = ""
    status_el = soup.select_one('[class*="cb-scrcrd-status"], [class*="cbSuccess"], [class*="cb-text-complete"], [class*="cb-text-live"]')
    if status_el:
        result = status_el.get_text(" ", strip=True)
    if not result:
        # try headings containing "won" / "match"
        for h in soup.select("div, span"):
            t = h.get_text(" ", strip=True)
            if 5 < len(t) < 160 and re.search(r"\bwon by\b|match tied|match drawn|no result", t, re.I):
                result = t
                break

    # Innings sections: id="scard-team-{X}-innings-{Y}"
    innings_sections = soup.select('[id^="scard-team-"][id*="innings-"]')
    # header (score/overs) is in id="team-{X}-innings-{Y}"
    innings_list = []
    for sec in innings_sections:
        sid = sec.get("id", "")
        # e.g. scard-team-43-innings-1
        m = re.match(r"scard-team-(\d+)-innings-(\d+)", sid)
        if not m:
            continue
        team_id, inn_num = m.group(1), m.group(2)
        header = soup.select_one(f"#team-{team_id}-innings-{inn_num}")
        header_text = header.get_text(" ", strip=True) if header else f"Innings {inn_num}"

        # Extract team name and score from header like "SLA 1st Innings Sri Lanka A 1st Innings 366-10 (110 Ov)"
        team_name = ""
        total = ""
        overs = ""
        if header_text:
            # Match score-overs like "366-10 (110 Ov)" — require the digits followed by ( or Ov
            score_m = re.search(r"(\d+[-/]\d+)\s*\((\d+(?:\.\d+)?)\s*Ov", header_text)
            if score_m:
                total = score_m.group(1)
                overs = score_m.group(2)
            else:
                score_m2 = re.search(r"(\d+[-/]\d+)", header_text)
                if score_m2:
                    total = score_m2.group(1)
            # Team name is before "1st/2nd Innings" (the human name after abbreviation)
            name_m = re.search(r"\d(?:st|nd|rd|th)\s+Innings\s+(.+?)\s+\d(?:st|nd|rd|th)\s+Innings", header_text)
            if name_m:
                team_name = name_m.group(1).strip()
            else:
                team_name = header_text.split(" ")[0]

        batting_rows = []
        # rows: div class contains "scorecard-bat-grid" but not the header row (which has "Batter" first)
        bat_grid_rows = sec.select('div.grid.scorecard-bat-grid, div[class*="scorecard-bat-grid"]')
        for row in bat_grid_rows:
            children = row.find_all(recursive=False)
            if not children:
                continue
            first_text = children[0].get_text(" ", strip=True)
            if first_text.lower().startswith("batter"):
                continue
            # children[0] is a div containing name + dismissal
            name_block = children[0]
            name_el = name_block.find(["a", "span"])
            name = name_el.get_text(" ", strip=True) if name_el else name_block.get_text(" ", strip=True).split("\n")[0]
            # dismissal is remaining text
            full_text = name_block.get_text(" ", strip=True)
            dismissal = full_text.replace(name, "", 1).strip()
            # numeric columns are the remaining children (skip trailing empty)
            nums = [c.get_text(" ", strip=True) for c in children[1:] if c.get_text(" ", strip=True) != ""]
            padded = (nums + [""] * 5)[:5]
            batting_rows.append({
                "batter": name,
                "dismissal": dismissal,
                "runs": padded[0],
                "balls": padded[1],
                "fours": padded[2],
                "sixes": padded[3],
                "sr": padded[4],
            })

        # Extras / Total / Did not bat / FOW - find divs starting with these labels
        extras = ""
        total_line = ""
        did_not_bat = ""
        fall_of_wickets = ""

        for div in sec.select("div"):
            t = div.get_text(" ", strip=True)
            if not t or len(t) > 800:
                continue
            lo = t.lower()
            if lo.startswith("extras") and not extras and len(t) < 200:
                extras = t
            elif lo.startswith("total") and not total_line and len(t) < 200:
                total_line = t
            elif (lo.startswith("did not bat") or lo.startswith("yet to bat")) and not did_not_bat:
                did_not_bat = t
            elif lo.startswith("fall of wickets") and not fall_of_wickets:
                fall_of_wickets = t
            if extras and total_line and did_not_bat and fall_of_wickets:
                break

        # Bowling rows
        bowling_rows = []
        bowl_grid_rows = sec.select('div.grid.scorecard-bowl-grid, div[class*="scorecard-bowl-grid"]')
        for row in bowl_grid_rows:
            children = row.find_all(recursive=False)
            if not children:
                continue
            first_text = children[0].get_text(" ", strip=True)
            if first_text.lower().startswith("bowler"):
                continue
            # Layout: [<a>Name</a>, O, M, R, W, NB, WD, ECON, <a>optional trailing]
            name = first_text
            nums = [c.get_text(" ", strip=True) for c in children[1:] if c.get_text(" ", strip=True) != ""]
            padded = (nums + [""] * 7)[:7]
            bowling_rows.append({
                "bowler": name,
                "overs": padded[0],
                "maidens": padded[1],
                "runs": padded[2],
                "wickets": padded[3],
                "no_balls": padded[4],
                "wides": padded[5],
                "econ": padded[6],
            })

        innings_list.append({
            "innings_number": int(inn_num),
            "team": team_name or f"Team {team_id}",
            "total": total,
            "overs": overs,
            "score_header": header_text,
            "batting": batting_rows,
            "bowling": bowling_rows,
            "extras": extras,
            "total_line": total_line,
            "did_not_bat": did_not_bat,
            "fall_of_wickets": fall_of_wickets,
        })

    if not innings_list:
        raise ScrapeError("Could not parse the Cricbuzz scorecard structure.")

    # Venue / toss / format from other meta blocks
    venue = ""
    toss = ""
    for row in soup.select("div"):
        t = row.get_text(" ", strip=True)
        if t.startswith("Venue") and not venue and len(t) < 200:
            venue = t.replace("Venue", "").strip(" :")
        elif t.startswith("Toss") and not toss and len(t) < 200:
            toss = t.replace("Toss", "").strip(" :")
        if venue and toss:
            break

    return {
        "source": "cricbuzz",
        "url": url,
        "match_title": match_title,
        "result": result,
        "venue": venue,
        "toss": toss,
        "innings": innings_list,
    }


# --------------------------- CRICINFO ---------------------------

def _scrape_cricinfo(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    title = soup.select_one("h1")
    match_title = title.get_text(" ", strip=True) if title else ""

    # Result
    result = ""
    status_el = soup.select_one('[class*="ds-text-tight-l"], [class*="ds-text-title"], [class*="header-info"]')
    for el in soup.select('span, p, div'):
        t = el.get_text(" ", strip=True)
        if 5 < len(t) < 200 and re.search(r"\bwon by\b|match tied|match drawn|no result", t, re.I):
            result = t
            break

    # Innings blocks: usually inside <div class="ds-rounded-lg"> that contain tables
    innings_list = []
    # ESPN cricinfo uses tables with class "ds-w-full ds-table ds-table-md ds-table-auto"
    tables = soup.select("table")
    # Group tables in pairs (batting, bowling)
    # But we need innings headers. Try finding parent sections with headers
    inning_headers = soup.select('[class*="ds-text-title-xs"], span.ds-text-title-xs, .ci-team-scores')
    # Simpler: iterate all sections with data-testid
    sections = soup.select('[class*="ci-scorecard"], [class*="scorecard"]')
    # Fallback: parse tables in order

    # Find all "1st Innings", "2nd Innings" headings
    innings_titles = []
    for h in soup.select("span, h5, h3, div"):
        t = h.get_text(" ", strip=True)
        if re.match(r"^[A-Z].{1,40}\s+(1st|2nd|3rd|4th) Innings$", t) and t not in innings_titles:
            innings_titles.append(t)

    # For each innings title, find the next 2 tables (batting + bowling)
    idx = 0
    all_tables = soup.select("table")
    # separate by inspecting header cells
    for it_title in innings_titles:
        team_name = re.sub(r"\s+(1st|2nd|3rd|4th) Innings$", "", it_title)
        inn_num = 1
        m = re.search(r"(1st|2nd|3rd|4th) Innings", it_title)
        if m:
            inn_num = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4}[m.group(1)]

        batting_rows = []
        bowling_rows = []
        # take next 2 tables from all_tables (batting then bowling)
        chosen = all_tables[idx: idx + 2]
        idx += 2
        for tbl in chosen:
            headers = [th.get_text(" ", strip=True).lower() for th in tbl.select("thead th, thead td")]
            if not headers:
                # inspect first row
                first_tr = tbl.select_one("tr")
                headers = [c.get_text(" ", strip=True).lower() for c in first_tr.find_all(["th", "td"])] if first_tr else []
            is_bowling = any(h in ("o", "overs") for h in headers)
            for tr in tbl.select("tbody tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if not cells or len(cells) < 3:
                    continue
                if is_bowling:
                    # Bowler, O, M, R, W, ECON, 0s, 4s, 6s, WD, NB (varies)
                    padded = (cells + [""] * 11)[:11]
                    bowling_rows.append({
                        "bowler": padded[0],
                        "overs": padded[1],
                        "maidens": padded[2],
                        "runs": padded[3],
                        "wickets": padded[4],
                        "econ": padded[5],
                        "no_balls": padded[9] if len(cells) > 9 else "",
                        "wides": padded[10] if len(cells) > 10 else "",
                    })
                else:
                    # Batting: Batter, Dismissal, R, B, M, 4s, 6s, SR
                    padded = (cells + [""] * 8)[:8]
                    batting_rows.append({
                        "batter": padded[0],
                        "dismissal": padded[1],
                        "runs": padded[2],
                        "balls": padded[3],
                        "fours": padded[5],
                        "sixes": padded[6],
                        "sr": padded[7],
                    })

        innings_list.append({
            "innings_number": inn_num,
            "team": team_name,
            "total": "",
            "overs": "",
            "score_header": it_title,
            "batting": batting_rows,
            "bowling": bowling_rows,
            "extras": "",
            "total_line": "",
            "did_not_bat": "",
            "fall_of_wickets": "",
        })

    if not innings_list:
        raise ScrapeError("Could not parse the ESPN Cricinfo scorecard. The page structure may have changed.")

    return {
        "source": "cricinfo",
        "url": url,
        "match_title": match_title,
        "result": result,
        "venue": "",
        "toss": "",
        "innings": innings_list,
    }


# --------------------------- CSV export ---------------------------

def scorecard_to_csv(sc: dict) -> str:
    """Serialize scorecard to a single CSV string."""
    import csv, io

    buf = io.StringIO()
    w = csv.writer(buf)

    # Match info
    w.writerow(["MATCH INFO"])
    w.writerow(["Title", sc.get("match_title", "")])
    w.writerow(["Source", sc.get("source", "")])
    w.writerow(["URL", sc.get("url", "")])
    w.writerow(["Venue", sc.get("venue", "")])
    w.writerow(["Toss", sc.get("toss", "")])
    w.writerow(["Result", sc.get("result", "")])
    w.writerow([])

    for inn in sc.get("innings", []):
        w.writerow([f"INNINGS {inn.get('innings_number','')} - {inn.get('team','')}"])
        w.writerow(["Score", inn.get("total", ""), "Overs", inn.get("overs", "")])
        w.writerow([])

        # Batting
        w.writerow(["Batting"])
        w.writerow(["CricHeroes Player ID", "Batter", "Dismissal", "R", "B", "4s", "6s", "SR"])
        for b in inn.get("batting", []):
            w.writerow([
                b.get("player_id", ""), b.get("batter", ""), b.get("dismissal", ""), b.get("runs", ""),
                b.get("balls", ""), b.get("fours", ""), b.get("sixes", ""), b.get("sr", "")
            ])
        if inn.get("extras"):
            w.writerow([inn["extras"]])
        if inn.get("total_line"):
            w.writerow([inn["total_line"]])
        if inn.get("did_not_bat"):
            w.writerow([inn["did_not_bat"]])
        if inn.get("fall_of_wickets"):
            w.writerow([inn["fall_of_wickets"]])
        w.writerow([])

        # Bowling
        w.writerow(["Bowling"])
        w.writerow(["CricHeroes Player ID", "Bowler", "O", "M", "R", "W", "NB", "WD", "ECON"])
        for bw in inn.get("bowling", []):
            w.writerow([
                bw.get("player_id", ""), bw.get("bowler", ""), bw.get("overs", ""), bw.get("maidens", ""),
                bw.get("runs", ""), bw.get("wickets", ""), bw.get("no_balls", ""),
                bw.get("wides", ""), bw.get("econ", "")
            ])
        w.writerow([])
        w.writerow([])

    return buf.getvalue()

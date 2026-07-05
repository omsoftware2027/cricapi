"""
Cricket scorecard scrapers for multiple sites.
Supports: Cricbuzz, ESPN Cricinfo, Cricheroes (blocked by Cloudflare - best-effort)
"""
from __future__ import annotations
import re
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests as cc_requests


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
    html = _fetch_html(url)
    if src == "cricbuzz":
        return _scrape_cricbuzz(html, url)
    if src == "cricinfo":
        return _scrape_cricinfo(html, url)
    if src == "cricheroes":
        # If we reach here, Cloudflare didn't block, try a generic approach
        raise ScrapeError(
            "Cricheroes scraping is not supported (site blocks server requests via Cloudflare)."
        )
    raise ScrapeError(
        "Unsupported site. Please paste a Cricbuzz or ESPN Cricinfo scorecard URL."
    )


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
        w.writerow(["Batter", "Dismissal", "R", "B", "4s", "6s", "SR"])
        for b in inn.get("batting", []):
            w.writerow([
                b.get("batter", ""), b.get("dismissal", ""), b.get("runs", ""),
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
        w.writerow(["Bowler", "O", "M", "R", "W", "NB", "WD", "ECON"])
        for bw in inn.get("bowling", []):
            w.writerow([
                bw.get("bowler", ""), bw.get("overs", ""), bw.get("maidens", ""),
                bw.get("runs", ""), bw.get("wickets", ""), bw.get("no_balls", ""),
                bw.get("wides", ""), bw.get("econ", "")
            ])
        w.writerow([])
        w.writerow([])

    return buf.getvalue()

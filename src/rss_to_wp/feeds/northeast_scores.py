"""Read completed varsity basketball games directly from Northeast's official athletics site."""

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup

from rss_to_wp.feeds.filter import is_within_window


HOST = "https://www.nemccathletics.com"
COLLEGE = "Northeast Mississippi Community College"
CENTRAL = ZoneInfo("America/Chicago")
RETAINED = Path(__file__).with_name("verified_college_scores.json")
SPORTS = {HOST + "/sports/mbkb/index": ("mbkb", "Men's"),
          HOST + "/sports/wbkb/index": ("wbkb", "Women's")}


def _page(url):
    response = requests.get(url, timeout=(6, 20), allow_redirects=False, headers={
        "User-Agent": "PrentissNews/1.0 (+https://prentissnews.com/)",
        "Accept-Encoding": "gzip, deflate",
    })
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError("Unexpected official athletics response")
    return BeautifulSoup(response.content, "html.parser")


def _text(node):
    return " ".join(node.stripped_strings)


def read_final(page, url, date, sport):
    box = page.select_one("article.game-boxscore.bkb")
    if box is None:
        raise ValueError("Official basketball box score unavailable")
    head = [_text(x) for x in box.select(".head h1 span")]
    line = box.select_one(".linescore table")
    if line is None or not line.select_one("th") or not re.fullmatch(r"Final(?:/\d*OT)?", _text(line.th), re.I):
        return None
    teams = [_text(x) for x in line.select("tr th[scope=row]")]
    if len(teams) != 2 or COLLEGE not in teams or len(head) != 4 or head[::2] != teams:
        raise ValueError("Box score team identities disagree")
    if datetime.strptime(head[-1], "%B %d, %Y").date() != date.date():
        raise ValueError("Box score date and schedule link disagree")
    totals = [_text(x) for x in line.select("td.total")]
    if len(totals) != 2 or not all(x.isdigit() for x in totals) or totals[0] == totals[1]:
        raise ValueError("Box score has no decisive final")
    title = f"{sport} basketball, {head[-1]}: {teams[0]} {totals[0]}, {teams[1]} {totals[1]}"
    evidence = [title + "."]
    periods = [_text(x) for x in line.select("th.score:not(.total)")]
    for row in line.select("tr:has(th[scope=row])"):
        values = [_text(x) for x in row.select("td.score:not(.total)")]
        if len(values) == len(periods) and all(x.isdigit() for x in values):
            if sum(map(int, values)) != int(_text(row.select_one("td.total"))):
                raise ValueError("Period scores do not add up to the official total")
            evidence.append(_text(row.th) + " scoring by period: " + ", ".join(
                f"period {period}, {value} points" for period, value in zip(periods, values)
            ) + ".")
    for row in box.select(".game-info tr"):
        if row.th and row.td and _text(row.th) == "Location":
            evidence.append("Game location: " + _text(row.td) + ".")
    stats = box.select_one(".team-stats table")
    if stats is None or [_text(h) for h in stats.select("tr")[0].select("th")][1:] != teams:
        raise ValueError("Official team statistics missing or mislabeled")
    for row in stats.select("tr:has(th[scope=row])"):
        values = [_text(c) for c in row.select("td")]
        if len(values) == 2 and all(re.fullmatch(r"\d+(?:[-.]\d+)?%?", s) for s in values):
            evidence.append(f"{_text(row.th)}: {teams[0]} {values[0]}, {teams[1]} {values[1]}.")
    for table in box.select("#boxscore-tabpanel .player-stats table"):
        team = _text(table.caption) if table.caption else ""
        headers = [_text(h) for h in table.select("tr")[0].select("th")]
        if team not in teams or not headers or headers[-1] != "PTS":
            continue
        leaders = []
        for row in table.select("tr:has(.player-name)"):
            values = [_text(c) for c in row.select("td")]
            if len(values) == len(headers) - 1 and values[-1].isdigit():
                leaders.append((int(values[-1]), _text(row.select_one(".player-name"))))
        for points, player in sorted(leaders, reverse=True)[:3]:
            evidence.append(f"{player} scored {points} points for {team}.")
    return feedparser.FeedParserDict(title=title, link=url, published=date.isoformat(), summary=" ".join(evidence))


def _live_scores(url, hours):
    path, sport = SPORTS[url]
    listing = _page(url)
    pattern = re.compile(re.escape(HOST) + rf"/sports/{path}/\d{{4}}-\d{{2}}/boxscores/(\d{{8}})_[a-z0-9]+\.xml")
    targets = {}
    upcoming = set()
    listed_final = set()
    for link in listing.select("a[href]"):
        target = urljoin(url, link["href"])
        match = pattern.fullmatch(target)
        if match:
            date = datetime.strptime(match[1], "%Y%m%d").replace(tzinfo=CENTRAL)
            card = link.find_parent(class_="card-body")
            status = card.select_one(".sport-date-status .status") if card else None
            if status and re.match(r"^Final\b", _text(status), re.I):
                listed_final.add(target)
            elif link.get("title", "").casefold() == "live stats" and status:
                tip = re.fullmatch(r"(\d{1,2}:\d{2} [AP]M) (CST|CDT)", _text(status), re.I)
                if tip:
                    clock = datetime.strptime(tip[1].upper(), "%I:%M %p")
                    start = date.replace(hour=clock.hour, minute=clock.minute)
                    if start.tzname() == tip[2].upper() and start > datetime.now(CENTRAL):
                        upcoming.add(target)
            targets[target] = date
    if not targets and not listing.select_one(f'a[href*="/sports/{path}/"][href$="/schedule"]'):
        raise ValueError("Official basketball schedule unavailable")
    entries = []
    for target, date in sorted(targets.items(), key=lambda item: item[1], reverse=True):
        if target in upcoming and target not in listed_final:
            continue
        if is_within_window(date, hours=hours, timezone="America/Chicago"):
            entry = read_final(_page(target), target, date, sport)
            if entry is not None:
                entries.append(entry)
    return feedparser.FeedParserDict(entries=entries, feed={"title": COLLEGE + " Athletics"})


def parse_college_scores(url, hours):
    try:
        return _live_scores(url, hours)
    except requests.RequestException as exc:
        # Retained desk-verified records expire on the original game date window.
        # Only a transport failure may use them; identity or score conflicts must surface.
        records = json.loads(RETAINED.read_bytes())
        field = SPORTS[url][0]
        pattern = re.compile(re.escape(HOST) + rf"/sports/{field}/\d{{4}}-\d{{2}}/boxscores/(\d{{8}})_[a-z0-9]+\.xml")
        entries = []
        for record in records["listings"].get(url, []):
            match = pattern.fullmatch(record["link"])
            date = datetime.fromisoformat(record["published"])
            if not match or match[1] != date.strftime("%Y%m%d"):
                raise ValueError("Retained box score identity mismatch") from exc
            if is_within_window(date, hours=hours, timezone="America/Chicago"):
                entries.append(feedparser.FeedParserDict(record))
        verified_at = datetime.fromisoformat(records["checked_at"])
        recently_checked = timedelta(0) <= datetime.now(timezone.utc) - verified_at <= timedelta(hours=18)
        verified_empty = url in records["listings"] and not records["listings"][url] and recently_checked
        if not entries and not verified_empty:
            raise
        logging.getLogger(__name__).warning("Official athletics source unavailable (%s); using %d current desk-verified box scores",
                                         type(exc).__name__, len(entries))
        return feedparser.FeedParserDict(entries=entries, feed={"title": COLLEGE + " Athletics"})


def refresh_verified_scores():
    """Refresh both official listings for the recovery desk, without any WordPress writes."""
    listings = {}
    for url in SPORTS:
        listing = _live_scores(url, 72)
        listings[url] = [{key: entry[key] for key in ("title", "link", "published", "summary")}
                         for entry in listing.entries]
    verified = datetime.now(timezone.utc)
    snapshot = {"checked_at": verified.isoformat(), "listings": listings}
    with NamedTemporaryFile(mode="w", dir=RETAINED.parent, encoding="utf-8", suffix=".tmp", delete=False) as output:
        temporary = Path(output.name)
        json.dump(snapshot, output, indent=2, ensure_ascii=False)
        output.write("\n")
    try:
        temporary.replace(RETAINED)
    finally:
        temporary.unlink(missing_ok=True)
    return sum(len(items) for items in listings.values())


if __name__ == "__main__":
    import argparse
    command = argparse.ArgumentParser(description="Refresh desk-verified Northeast basketball box scores")
    command.add_argument("--refresh", required=True, action="store_true")
    command.parse_args()
    print(f"Verified {refresh_verified_scores()} recent finals from both official basketball listings")

"""Varsity basketball reporting from matched team schedules and final box scores."""

from datetime import datetime, timedelta
from functools import lru_cache
import re
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from feedparser import FeedParserDict
import requests

BASE = "https://www.maxpreps.com"
SCHOOLS = ("wheeler/wheeler-eagles", "new-site/new-site-royals")
SCHEDULES = {
    f"{BASE}/ms/{school}/basketball/{level}schedule/"
    for school in SCHOOLS
    for level in ("", "girls/")
}
TEAM = re.compile(r"/ms/[a-z0-9-]+/[a-z0-9-]+/basketball/(girls/)?")
GAME = re.compile(
    r"/ms/basketball/(girls/)?game/[a-z0-9-]+/(\d{1,2}-\d{1,2}-\d{4})/\?c=[a-f0-9-]{36}"
)
ZONE = ZoneInfo("America/Chicago")


def page(url):
    response = requests.get(
        url,
        timeout=(5, 15),
        allow_redirects=False,
        headers={
            "User-Agent": "PrentissNews/1.0 (+https://prentissnews.com/)",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    canonical = soup.select_one('link[rel="canonical"]')
    if response.status_code != 200 or canonical is None or canonical.get("href") != url:
        raise ValueError("Unconfirmed basketball record identity")
    return soup


def results(schedule):
    records = {}
    for row in schedule.select("table tbody tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 3 or not cells[0].a or not cells[1].a:
            continue
        path = cells[0].a.get("href", "")
        game = GAME.fullmatch(path)
        other = cells[1].a.get("href", "")
        name = cells[1].select_one(".name")
        team = TEAM.fullmatch(other)
        score = re.fullmatch(
            r"([WL])\s*(\d+)[-–](\d+)(?:\s*\(\d*OT\))?", cells[2].get_text(" ", strip=True)
        )
        if not game or not team or team[1] != game[1] or not score or not name:
            continue
        high, low = sorted(map(int, score.groups()[1:]), reverse=True)
        if high == low:
            continue
        local, away = (high, low) if score[1] == "W" else (low, high)
        records[path] = (
            datetime.strptime(game[2], "%m-%d-%Y").date(),
            other,
            name.get_text(strip=True),
            local,
            away,
        )
    return records


def county_scores(url, hours):
    if url not in SCHEDULES:
        raise ValueError("Unknown county school")
    schedule = lru_cache(maxsize=12)(lambda path: results(page(BASE + path + "schedule/")))
    home = url.removeprefix(BASE).removesuffix("schedule/")
    local = schedule(home)
    entries = []
    now = datetime.now(ZONE)
    for game, (day, opponent, opponent_name, points, against) in local.items():
        played = datetime.combine(day, datetime.min.time(), ZONE)
        if not now - timedelta(hours=hours) <= played <= now:
            continue
        other = schedule(opponent)
        if game not in other or other[game][1] != home or other[game][3:] != (against, points):
            continue
        box = page(BASE + game)
        table = box.select_one('table[data-testid="box-score-table"]')
        final = box.select_one(".final")
        if not table or not final or final.get_text(strip=True) != "Final":
            continue
        sides = {}
        for row in table.select("tbody tr"):
            link = row.select_one("th a.team-cell")
            total = row.select_one("td.final-score")
            if link and total and total.get_text(strip=True).isdigit():
                sides[link.get("href")] = (
                    int(total.get_text(strip=True)),
                    [v.get_text(strip=True) for v in row.select("td:not(.final-score)")],
                )
        if (
            set(sides) != {home, opponent}
            or sides[home][0] != points
            or sides[opponent][0] != against
        ):
            continue
        name = other[game][2]
        sport = "girls varsity basketball" if "/girls/" in game else "boys varsity basketball"
        title = f"{name} {sport} {'beats' if points > against else 'falls to'} {opponent_name} {max(points, against)}–{min(points, against)}"
        # Explicit school identities from the district's Our Schools directory.
        school_name = "Wheeler Attendance Center" if "/wheeler/" in home else "New Site High School"
        evidence = [
            f"{title} on {day:%A, %B %d, %Y}. Both teams' schedules confirm the final score in the game box.",
            f"The Prentiss County School District in Mississippi lists {school_name} in its school directory (www.prentisscountyschools.com/our-district/our-schools).",
        ]
        if "/booneville/thrasher-rebels/" in opponent:
            evidence.append(
                "Thrasher Attendance Center also belongs to the Prentiss County School District, making this a game between two county schools."
            )
        periods = [h.get_text(strip=True) for h in table.select("thead th")][1:-1]
        if periods == ["Q1", "Q2", "Q3", "Q4"] and all(
            len(nums) == 4 and all(n.isdigit() for n in nums) and sum(map(int, nums)) == total
            for total, nums in sides.values()
        ):
            for i, label in enumerate(("first", "second", "third", "fourth")):
                evidence.append(
                    f"In the {label} quarter {name} scored {sides[home][1][i]} points and {opponent_name} scored {sides[opponent][1][i]} points."
                )
        for team, records in ((name, local), (opponent_name, other)):
            season = [row for row in records.values() if row[0] <= day]
            wins = sum(row[3] > row[4] for row in season)
            evidence.append(
                f"Through {day:%B %d}, {team}'s schedule lists {wins} wins and {len(season) - wins} losses "
                f"with final scores. The points in those games total {sum(row[3] for row in season)} for "
                f"{team} and {sum(row[4] for row in season)} for its opponents."
            )
            past = sorted(
                (r for r in records.values() if day - timedelta(days=28) <= r[0] < day),
                reverse=True,
            )
            for date, _, rival, scored, allowed in past[:5]:
                evidence.append(
                    f"On {date:%B %d}, {team} {'won against' if scored > allowed else 'lost to'} {rival}; "
                    f"{team} scored {scored} points and {rival} scored {allowed}."
                )
        entries.append(
            FeedParserDict(
                id=BASE + game,
                link=BASE + game,
                title=title,
                summary=" ".join(evidence),
                published=played.isoformat(),
            )
        )
    return FeedParserDict(entries=entries, feed={"title": "Varsity basketball records on MaxPreps"})


def school_finals(url, hours):
    for school in SCHOOLS:
        program = f"{BASE}/ms/{school}/"
        if url == program:
            entries = []
            for sport in ("basketball/schedule/", "basketball/girls/schedule/"):
                entries.extend(county_scores(program + sport, hours).entries)
            return FeedParserDict(
                entries=entries, feed={"title": "Varsity basketball records on MaxPreps"}
            )
    raise ValueError("Unknown Prentiss County school program")

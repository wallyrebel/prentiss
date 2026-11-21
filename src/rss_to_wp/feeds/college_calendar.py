"""Current fall closing and examination schedules from Northeast's college catalog."""

from datetime import date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from feedparser import FeedParserDict
import requests

BASE = "https://catalog.nemcc.edu/general-information/"
CALENDAR = BASE + "calendar.html"
GRID = BASE + "calendar-grids.html"
CENTRAL = ZoneInfo("America/Chicago")


def term_table(url, heading):
    response = requests.get(
        url,
        timeout=(5, 15),
        allow_redirects=False,
        headers={"Accept-Encoding": "gzip, deflate", "User-Agent": "PrentissNews/1.0"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    if (
        response.status_code != 200
        or not soup.title
        or "Northeast Mississippi Community College" not in soup.title.get_text()
    ):
        raise ValueError("Northeast catalog identity unavailable")
    node = next(
        (h for h in soup.select("h2,h3,h4") if h.get_text(" ", strip=True) == heading), None
    )
    if node is None:
        return []
    table = node.find_next("table")
    if table is None or table.find_previous(["h2", "h3", "h4"]) != node:
        raise ValueError("Missing catalog term table")
    return [
        [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"], recursive=False)]
        for row in table.select("tr")
    ]


def fall_deadlines():
    now = datetime.now(CENTRAL)
    year = now.year
    # The edition is only relevant in the week before Thanksgiving in November.
    if now.month != 11:
        return FeedParserDict(entries=[], feed={})
    calendar = term_table(CALENDAR, f"Fall Semester {year}")
    dates = {row[2]: row[0] for row in calendar if len(row) == 3}
    closed = next(
        (
            v
            for k, v in dates.items()
            if k.casefold().startswith("thanksgiving holidays") and "college closed" in k.casefold()
        ),
        "",
    )
    match = re.fullmatch(r"November (\d{1,2})\s*[-–]\s*(\d{1,2})", closed)
    if not match:
        return FeedParserDict(entries=[], feed={})
    start = date(year, 11, int(match[1]))
    end = date(year, 11, int(match[2]))
    eligibility = datetime.combine(start - timedelta(days=3), time.min, CENTRAL)
    if (
        now < eligibility
        or now.date() >= start
        or start.weekday() != 0
        or end != start + timedelta(days=4)
    ):
        return FeedParserDict(entries=[], feed={})
    grid = term_table(GRID, f"Fall {year}")
    if not grid or not grid[0][0].startswith(f"Fall {year} Term Code:"):
        raise ValueError("Northeast fall term grid unavailable")
    columns = grid[0][1:]
    data = {row[0]: row[1:] for row in grid[1:] if len(row) == len(columns) + 1}
    if not {"Classes Begin", "Withdrawal Period", "Finals"} <= data.keys():
        raise ValueError("Fall term grid incomplete")
    facts = [
        f"Northeast Mississippi Community College in Booneville lists its fall {year} Thanksgiving holidays from "
        f"{start:%A, %B %d} through {end:%A, %B %d}. The college calendar explicitly lists the college as closed for that period.",
        "The college publishes separate schedules for seated classes, virtual classes and microterms. The type of course determines the applicable withdrawal and examination dates.",
    ]
    for event, period in dates.items():
        if event in {
            "Last Day of Full Term Seated Classes",
            "Final Exams for Full Term Seated Classes",
            "Fall Commencement Ceremony",
            "Administrative Offices Open",
            "Winter Break-(College Closed)",
        }:
            facts.append(f"The academic calendar lists {period} for this event: {event}.")
    count = 0
    for i, column in enumerate(columns):
        section = re.fullmatch(
            r"(Seated Full Term|Virtual Full Term|Virtual Short Term 2|Microterm) PTRM (\d+)",
            column,
        )
        if section:
            course, code = section.groups()
            facts.append(
                f"For fall {year} {course.lower()} courses (part-of-term code {code}), the catalog lists a start date of {data['Classes Begin'][i]}. "
                f"The withdrawal period for {course.lower()} runs {data['Withdrawal Period'][i]}. "
                f"The final examination date or window for {course.lower()} is {data['Finals'][i]}."
            )
            count += 1
    if count != 4:
        raise ValueError("Unrecognized fall course formats")
    facts.append(
        f"Sources: Northeast's college calendar at {CALENDAR} and its term-by-term Important Dates table at {GRID}."
    )
    entry = FeedParserDict(
        link=f"{GRID}?term={year}-fall",
        id=f"nemcc-fall-deadlines-{year}",
        title="Northeast fall exam dates by course type after Thanksgiving in Booneville",
        summary=" ".join(facts),
        published=eligibility.isoformat(),
    )
    return FeedParserDict(
        entries=[entry], feed={"title": "Northeast Mississippi Community College catalog"}
    )

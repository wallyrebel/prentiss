"""Prentiss County branch access through the Thanksgiving closing."""
from datetime import date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from feedparser import FeedParserDict
import requests

LIBRARY = "https://www.nereg.lib.ms.us/"
BOONEVILLE = LIBRARY + "george-e-allen-library/"
BALDWYN = LIBRARY + "anne-spencer-cox-library-baldwyn/"
CATALOG = LIBRARY + "catalog-search/"
CENTRAL = ZoneInfo("America/Chicago")


def get_page(url):
    response = requests.get(url, timeout=(5, 15), allow_redirects=False,
                            headers={"User-Agent":"PrentissNews/1.0 (+https://prentissnews.com/)", "Accept-Encoding":"gzip, deflate"})
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError("Library page unavailable")
    return BeautifulSoup(response.content, "html.parser")


def thanksgiving_branches(now=None, page=get_page):
    now = now or datetime.now(CENTRAL)
    first = date(now.year, 11, 1)
    holiday = first + timedelta(days=(3 - first.weekday()) % 7 + 21)
    available = datetime.combine(holiday - timedelta(days=3), time.min, CENTRAL)
    feed = FeedParserDict(entries=[],feed={"title":"Northeast Regional Library"})
    if not available <= now < datetime.combine(holiday, time.min, CENTRAL):
        return feed
    home = page(LIBRARY)
    notice = next((p.get_text(" ", strip=True) for p in home.select("p")
                   if p.get_text(" ", strip=True).startswith("All Northeast Regional Library System branches will be closed")), "")
    if not re.search(rf"November [^.]*?\b{holiday.day},?\s*(?:&|and)\s*{holiday.day + 1},\s*{holiday.year}\b", notice):
        return feed
    records = []
    for town, name, url in [("Booneville", "George E. Allen Library", BOONEVILLE), ("Baldwyn", "Anne Spencer Cox Library", BALDWYN)]:
        branch = page(url)
        contact = next((p.get_text(" ", strip=True) for p in branch.select(".elementor-widget-text-editor")
                        if town + ", MS" in p.get_text(" ", strip=True) and "Phone:" in p.get_text(" ", strip=True)), "")
        hours = [h.get_text(" ", strip=True) for h in branch.select("h2") if h.get_text(" ",strip=True).startswith("Open ")]
        if not hours or not contact or not branch.title or name.lower() not in branch.title.get_text(" ", strip=True).lower():
            raise ValueError("Branch schedule or identity unavailable")
        records.append(f"{name} in {town}. Regular opening times are {'; '.join(hours)}. Branch contact information: {contact.split(' Fax:')[0]}. Verified branch schedule: {url}.")
    wednesday, friday, saturday = [holiday + timedelta(days=n) for n in (-1, 1, 2)]
    facts = [f"Northeast Regional Library's branch closure list covers {holiday:%A, %B %d} and {friday:%A, %B %d, %Y} at both Prentiss County branches. The system publishes an annual closure list; this is a holiday-week service guide. The surrounding dates are {wednesday:%A, %B %d} and {saturday:%A, %B %d}.", *records]
    catalog = page(CATALOG)
    for p in catalog.select("p"):
        text = " ".join(p.stripped_strings)
        if text.startswith("If you would like to log into your library account online") or text.startswith("A reminder about placing holds"):
            facts.append(text.split("Your PIN number will be")[0].strip())
    for p in home.select("p"):
        text = " ".join(p.stripped_strings)
        if text.startswith(("You can stream titles instantly", "Borrow ebooks, audiobooks, magazines")):
            facts.append(text)
    facts.append(f"Catalog and renewals: {CATALOG}. Regional closure notice and digital-resource links: {LIBRARY}.")
    feed.entries.append(FeedParserDict(id=f"prentiss-library-thanksgiving-{holiday.year}",
                        link=BOONEVILLE + f"?edition=thanksgiving-{holiday.year}",
                        title="Thanksgiving library access in Booneville and Baldwyn, including Saturday and online options",
                        summary=" ".join(facts), published=available.isoformat()))
    return feed

"""Read full releases from the explicitly configured Mississippi SOS newsroom."""

from urllib.parse import urljoin, urlsplit
import re
import requests
from datetime import datetime, timezone, timedelta

import feedparser
import pendulum
from bs4 import BeautifulSoup

from rss_to_wp.feeds.filter import is_within_window
from rss_to_wp.utils.http import fetch_url_content

NEWS_URL = "https://www.sos.ms.gov/news"


def newsroom_page(url):
    response = requests.get(url, timeout=(5, 20), allow_redirects=False,
                            headers={"User-Agent": "PrentissNews/1.0 (+https://prentissnews.com/)"})
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError("Newsroom page unavailable")
    return BeautifulSoup(response.content, "html.parser")


def extension_release(url, feed_date):
    if not re.fullmatch(r"https://extension\.msstate\.edu/news/[a-z0-9-]+/\d{4}/[a-z0-9-]+", url):
        return None
    page = newsroom_page(url)
    stamp = page.select_one(".article--published time[datetime]")
    body = page.select_one(".article--body .block--field-blocknodenewsfield-body")
    if not stamp or not body or not feed_date:
        raise ValueError("Extension release lacks a publication date or body")
    if datetime.fromisoformat(stamp["datetime"]) != feed_date:
        raise ValueError("Extension release and feed publication dates disagree")
    return " ".join(p.get_text(" ", strip=True) for p in body.select("p"))


def parse_college_news(url, hours):
    if url != "https://nemcc.meritpages.com/news":
        raise ValueError("Unsupported college newsroom")
    rows = newsroom_page(url).select(".headline-and-summary")
    if not rows:
        raise ValueError("College newsroom listing unavailable")
    entries = []
    for row in rows[:10]:
        link = row.select_one("h3 a[href]")
        cite = row.select_one("cite")
        if not link or not cite or not re.fullmatch(r"https://nemcc\.meritpages\.com/news/[a-z0-9-]+/\d+", link["href"]):
            continue
        text, zone = cite.get_text(" ", strip=True).rsplit(" ", 1)
        offset = {"EST": -5, "EDT": -4}[zone]
        date = datetime.strptime(" ".join(text.split()), "%b %d, %Y, %I:%M%p").replace(tzinfo=timezone(timedelta(hours=offset)))
        if not is_within_window(date, hours=hours, timezone="America/Chicago"):
            continue
        release = newsroom_page(link["href"]).select_one("#press-release")
        if not release or not release.select("p"):
            raise ValueError("College release body unavailable")
        entries.append(feedparser.FeedParserDict(title=link.get_text(" ", strip=True),
                       link=link["href"], published=date.isoformat(),
                       summary=" ".join(p.get_text(" ", strip=True) for p in release.select("p"))))
    return feedparser.FeedParserDict(entries=entries, feed={"title": "Northeast Mississippi Community College"})


def parse_official_news(url, hours=168):
    # This is a fixed source adapter, not an arbitrary URL crawler. Do not follow
    # external news links, videos, or use page navigation/footer text as evidence.
    if url != NEWS_URL:
        raise ValueError("Unsupported official newsroom")
    listing = BeautifulSoup(fetch_url_content(url), "html.parser")
    rows = listing.select("main .views-row")
    if not rows:
        raise ValueError("Official newsroom layout changed")
    entries = []
    seen = set()
    for row in rows[:10]:
        link = row.select_one(".view-node a[href]")
        date_node = row.select_one(".fst-italic")
        if not link or not date_node:
            continue
        date = pendulum.from_format(
            date_node.get_text(strip=True), "dddd, MMMM D, YYYY", tz="America/Chicago"
        )
        if not is_within_window(date, hours=hours, timezone="America/Chicago"):
            continue
        target = urljoin(url, link["href"])
        parts = urlsplit(target)
        if (
            parts.scheme != "https"
            or parts.netloc != "www.sos.ms.gov"
            or not parts.path.startswith("/news/")
            or target in seen
        ):
            continue
        seen.add(target)
        page = BeautifulSoup(fetch_url_content(target), "html.parser")
        body = page.select_one("article.node--type-news .field--name-body")
        title = page.select_one("h1")
        publication = page.select_one("article.node--type-news .field--name-field-publication-date")
        if not title or not publication:
            raise ValueError("Official release layout changed")
        # Some newsroom entries link to another outlet and contain no release.
        if not body or not body.get_text(strip=True):
            continue
        actual_date = pendulum.from_format(
            publication.get_text(strip=True), "dddd, MMMM D, YYYY", tz="America/Chicago"
        )
        if actual_date != date:
            raise ValueError("Conflicting official publication dates")
        entries.append(
            feedparser.FeedParserDict(
                title=title.get_text(" ", strip=True),
                link=target,
                published=date.isoformat(),
                content=[{"value": str(body)}],
            )
        )
    return feedparser.FeedParserDict(
        entries=entries, feed={"title": "Mississippi Secretary of State"}
    )

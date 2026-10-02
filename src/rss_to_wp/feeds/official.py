"""Read full releases from the explicitly configured Mississippi SOS newsroom."""

from urllib.parse import urljoin, urlsplit

import feedparser
import pendulum
from bs4 import BeautifulSoup

from rss_to_wp.feeds.filter import is_within_window
from rss_to_wp.utils.http import fetch_url_content

NEWS_URL = "https://www.sos.ms.gov/news"


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

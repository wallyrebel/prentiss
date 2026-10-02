from unittest.mock import Mock

import pendulum
import pytest

from rss_to_wp.feeds.official import NEWS_URL, parse_official_news
from rss_to_wp.wordpress.client import WordPressClient


def test_official_reader_uses_dated_body_and_ignores_external_links(monkeypatch):
    date = pendulum.now("America/Chicago").format("dddd, MMMM D, YYYY")
    row = '<div class="views-row"><div class="fst-italic">{}</div><div class="view-node"><a href="{}">More</a></div></div>'
    listing = (
        "<main>"
        + row.format(date, "/news/deadline")
        + row.format(date, "https://example.org/news/other")
        + "</main>"
    )
    article = f'<h1>Registration deadline</h1><article class="node--type-news"><div class="field--name-field-publication-date">{date}</div><div class="field--name-body"><p>Official instructions.</p></div></article><footer><div class="field--name-body">Not evidence</div></footer>'
    fetch = Mock(side_effect=[listing, article])
    monkeypatch.setattr("rss_to_wp.feeds.official.fetch_url_content", fetch)
    result = parse_official_news(NEWS_URL)
    assert len(result.entries) == 1
    assert "Official instructions" in result.entries[0].content[0]["value"]
    assert "Not evidence" not in result.entries[0].content[0]["value"]
    assert fetch.call_count == 2


def test_official_reader_does_not_follow_external_article_link(monkeypatch):
    date = pendulum.now("America/Chicago").format("dddd, MMMM D, YYYY")
    listing = f'<main><div class="views-row"><div class="fst-italic">{date}</div><div class="view-node"><a href="/news/interview">More</a></div></div></main>'
    article = f'<h1>Interview</h1><article class="node--type-news"><div class="field--name-field-publication-date">{date}</div><a href="https://example.org">External news</a></article>'
    fetch = Mock(side_effect=[listing, article])
    monkeypatch.setattr("rss_to_wp.feeds.official.fetch_url_content", fetch)
    assert not parse_official_news(NEWS_URL).entries
    assert fetch.call_count == 2


def test_official_layout_failure_is_reported(monkeypatch):
    monkeypatch.setattr(
        "rss_to_wp.feeds.official.fetch_url_content", Mock(return_value="<main>Unavailable</main>")
    )
    with pytest.raises(ValueError, match="layout changed"):
        parse_official_news(NEWS_URL)


@pytest.mark.parametrize(
    "status,body,passes",
    [
        (
            "publish",
            '<p>Approved body.</p><p><a href="https://example.org/source">Source</a></p>',
            True,
        ),
        ("draft", '<p>Approved body.</p><a href="https://example.org/source">Source</a>', False),
        ("publish", '<p>Truncated.</p><a href="https://example.org/source">Source</a>', False),
        ("publish", "<p>Approved body.</p>", False),
    ],
)
def test_publish_readback_is_public_and_checks_status_content_and_sources(
    monkeypatch, status, body, passes
):
    wp = WordPressClient("https://example.org", "test", "test")
    wp._rate_limit = Mock()
    wp.session.get = Mock(side_effect=AssertionError("Must check without authentication"))
    response = Mock()
    response.json.return_value = {
        "id": 99,
        "status": status,
        "link": "https://example.org/article",
        "content": {"rendered": body},
    }
    get = Mock(return_value=response)
    monkeypatch.setattr("rss_to_wp.wordpress.client.requests.get", get)
    if passes:
        assert (
            wp.verify_post(99, "publish", "<p>Approved body.</p>", ["https://example.org/source"])[
                "id"
            ]
            == 99
        )
    else:
        with pytest.raises(RuntimeError):
            wp.verify_post(99, "publish", "<p>Approved body.</p>", ["https://example.org/source"])
    assert "auth" not in get.call_args.kwargs


def test_readback_allows_wordpress_smart_punctuation(monkeypatch):
    wp = WordPressClient("https://example.org", "test", "test")
    response = Mock()
    response.json.return_value = {
        "id": 99,
        "status": "publish",
        "link": "https://example.org/article",
        "content": {
            "rendered": '<p>The clerk\u2019s notice says \u201cMonday.\u201d</p><a href="https://example.org/source">Source</a>'
        },
    }
    monkeypatch.setattr("rss_to_wp.wordpress.client.requests.get", Mock(return_value=response))
    wp.verify_post(
        99, "publish", '<p>The clerk\'s notice says "Monday."</p>', ["https://example.org/source"]
    )

import json
from types import SimpleNamespace
from unittest.mock import Mock

import feedparser
import pendulum
import pytest
import requests
from pydantic import ValidationError

from rss_to_wp.config import AppSettings, FeedConfig, FeedsConfig, QualityPolicy
from rss_to_wp.editorial import (
    Evidence,
    Review,
    Source,
    assessment_key,
    candidate_groups,
    canonical_source_url,
    unique_source_text,
    validate_article,
    validate_review,
)
from rss_to_wp.feeds.filter import is_within_window, parse_entry_date
from rss_to_wp.feeds.parser import get_entry_content
from rss_to_wp.pipeline import collect_sources, run_pipeline
from rss_to_wp.rewriter.openai_client import OpenAIRewriter
from rss_to_wp.storage import DedupeStore
from rss_to_wp.wordpress.client import WordPressClient

TEXT = """Booneville Public Library will hold a family reading workshop on October 8, 2026, from 4 to 6 p.m. The workshop takes place in the library meeting room at 100 Main Street in Booneville. Library director Jane Smith said the free program will help parents practice reading aloud with young children. Families should register by October 6 by calling the library during regular opening hours. Registration is limited to twenty families because the meeting room has limited seating. Each family will receive two books purchased through a donation from the library friends group. Children must attend with a parent or another adult caregiver throughout the afternoon session. Library staff will demonstrate three reading exercises and give families time to practice each one together. No library card is required to attend the workshop or receive the donated books. Participants should enter through the main entrance, which has an accessible ramp beside the parking lot. The library will provide printed instructions describing the exercises so families can repeat them at home. Smith said the program was developed in response to parent requests collected during the summer reading program. Staff will collect feedback after the workshop to determine whether another session should be offered later this year."""


@pytest.fixture
def feed():
    return FeedConfig(name="Local Feed 1", url="https://example.org/feed", max_per_run=2)


@pytest.fixture
def source(feed):
    return Source(
        {},
        feed,
        "link:test",
        "https://example.org/story",
        "Booneville library workshop",
        TEXT,
        pendulum.now("UTC").isoformat(),
        "Booneville Public Library",
    )


@pytest.fixture
def policy():
    return QualityPolicy(min_article_words=150)


@pytest.fixture
def article():
    sentences = TEXT.split(". ")
    return {
        "decision": "publish",
        "headline": "Booneville library schedules family reading workshop",
        "excerpt": "The library will offer reading exercises and books for families at an October workshop.",
        "body": "".join(
            f"<p>{'. '.join(sentences[i : i + 3])}</p>" for i in range(0, len(sentences), 3)
        ),
    }


@pytest.fixture
def review():
    facts = [Evidence(answer=s, quote=s, source_index=0) for s in TEXT.split(". ")[:6]]
    return Review(
        approved=True,
        reason="Supported",
        same_event=True,
        all_claims_supported=True,
        complete_5w=True,
        locally_relevant=True,
        useful_details=True,
        no_padding=True,
        attribution_correct=True,
        no_conflicts=True,
        not_duplicate=True,
        coverage=dict(zip(["who", "what", "where", "when", "why", "local_relevance"], facts)),
        facts=facts,
    )


def test_valid_article_and_review(article, source, policy, review):
    assert not validate_article(article, [source], policy)
    assert not validate_review(review, [source], policy)


@pytest.mark.parametrize("field", ["who", "what", "where", "when", "why", "local_relevance"])
def test_each_missing_w_is_rejected(review, source, policy, field):
    del review.coverage[field]
    assert "missing_5w_evidence" in validate_review(review, [source], policy)


@pytest.mark.parametrize(
    "field",
    [
        "approved",
        "same_event",
        "all_claims_supported",
        "complete_5w",
        "locally_relevant",
        "useful_details",
        "no_padding",
        "attribution_correct",
        "no_conflicts",
        "not_duplicate",
    ],
)
def test_every_review_gate_is_required(review, source, policy, field):
    setattr(review, field, False)
    assert field in validate_review(review, [source], policy)


def test_invented_evidence_rejected(review, source, policy):
    review.facts[0].quote = "This sentence does not occur in the source."
    assert "unverified_evidence" in validate_review(review, [source], policy)


def test_repeated_facts_rejected(review, source, policy):
    review.facts = [review.facts[0]] * 6
    assert "too_few_distinct_facts" in validate_review(review, [source], policy)


def test_boolean_string_is_not_approval(review):
    data = review.model_dump()
    data["approved"] = "true"
    with pytest.raises(ValidationError):
        Review.model_validate(data)


@pytest.mark.parametrize(
    "body",
    [
        "<p>Short article.</p>",
        "<script>" + TEXT + "</script>",
        '<p style="display:none">' + TEXT + "</p>",
    ],
)
def test_thin_or_hidden_content_fails(article, source, policy, body):
    article["body"] = body
    assert validate_article(article, [source], policy)


def test_inflation_and_repetition_fail(article, source, policy):
    article["body"] *= 3
    errors = validate_article(article, [source], policy)
    assert "excessive_expansion" in errors and "repeated_paragraphs" in errors


def test_tracking_does_not_create_new_source():
    assert (
        canonical_source_url("https://www.facebook.com/123/posts/456/?utm_source=x&fbclid=y#top")
        == "https://www.facebook.com/123/posts/456"
    )
    assert canonical_source_url("https://example.org/?id=5&fbclid=x") == "https://example.org/?id=5"


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "file:///data", "https://user:secret@example.org/"]
)
def test_unsafe_attribution_urls_fail(url):
    with pytest.raises(ValueError):
        canonical_source_url(url)


def test_future_dates_and_old_dates_are_not_fresh():
    assert not is_within_window(pendulum.now().add(days=1))
    assert not is_within_window(pendulum.now().subtract(days=3))
    assert is_within_window(pendulum.now().subtract(hours=1))


def test_parsed_rss_dates_are_utc():
    import time

    assert (
        parse_entry_date(
            {"published_parsed": time.struct_time((2026, 10, 1, 12, 0, 0, 3, 274, 0))}
        ).hour
        == 12
    )


def test_empty_full_content_uses_summary():
    assert (
        get_entry_content({"content": [{"value": ""}], "summary": "Real summary"}) == "Real summary"
    )


def test_duplicate_sources_do_not_inflate_word_count(source):
    assert unique_source_text([source, source]) == unique_source_text([source])


def test_unrelated_short_sources_are_not_combined(source, policy):
    from dataclasses import replace

    a = replace(
        source,
        text="Library reading workshop invites families to practice literacy exercises together.",
    )
    b = replace(
        source,
        url="https://example.org/2",
        text="Road crews will close Highway 45 for paving work on Tuesday morning.",
    )
    assert len(candidate_groups([a, b], policy)) == 2


def test_matching_short_sources_can_combine(source, policy):
    from dataclasses import replace

    a = replace(
        source,
        text="Library reading workshop invites families to practice literacy exercises together. Parents register before Thursday afternoon.",
    )
    b = replace(
        a,
        url="https://example.org/2",
        text=a.text + " Participants receive donated books and printed instructions for exercises.",
    )
    assert len(candidate_groups([a, b], policy)[0]) == 2


def test_updated_source_is_reconsidered(source, policy):
    old = assessment_key([source], policy, "model")
    source.text += " Additional confirmed details."
    assert old != assessment_key([source], policy, "model")


@pytest.fixture
def settings():
    return AppSettings(
        openai_api_key="test",
        wordpress_base_url="https://example.org",
        wordpress_username="test",
        wordpress_app_password="test",
    )


def test_scan_past_duplicates_and_thin_entries(monkeypatch, tmp_path, feed, settings):
    now = pendulum.now().to_rfc822_string()
    entries = [
        feedparser.FeedParserDict(
            title=f"entry {i}", link=f"https://example.org/{i}", published=now, summary=TEXT
        )
        for i in range(8)
    ]
    monkeypatch.setattr(
        "rss_to_wp.pipeline.parse_feed", lambda _: SimpleNamespace(entries=entries, feed={})
    )
    store = DedupeStore(tmp_path / "db")
    for i in range(5):
        store.mark_processed(
            str(i), feed.url, "title", f"https://example.org/{i}", i + 1, "https://example.org/p"
        )
    found = collect_sources(FeedsConfig(feeds=[feed]), settings, store, 72, [])
    assert len(found) == 3 and found[0].url.endswith("/5")


def test_old_dry_run_does_not_poison_state(tmp_path):
    store = DedupeStore(tmp_path / "db")
    store.mark_processed("x", "f", "t", "https://example.org/a", 0, "dry-run://not-published")
    assert not store.is_source_processed("https://example.org/a")


def test_dry_run_never_marks_or_uploads(
    monkeypatch, tmp_path, source, article, review, settings, policy
):
    monkeypatch.setattr("rss_to_wp.pipeline.collect_sources", lambda *args: [source])
    writer = Mock()
    writer.rewrite_sources.return_value = {**article, "review": review.model_dump()}
    store = DedupeStore(tmp_path / "db")
    summary, _ = run_pipeline(
        FeedsConfig(feeds=[source.feed], quality=policy),
        settings,
        store,
        writer,
        None,
        True,
        report_path=tmp_path / "r.json",
    )
    assert summary["would_publish"] == 1
    assert store.get_processed_count() == 0


def test_rejected_article_never_writes_to_wordpress(
    monkeypatch, tmp_path, source, settings, policy
):
    monkeypatch.setattr("rss_to_wp.pipeline.collect_sources", lambda *args: [source])
    writer = Mock()
    writer.rewrite_sources.return_value = {"skip": True, "reason": "missing_when"}
    wp = Mock()
    wp.recent_stories.return_value = []
    wp.check_duplicate_by_source_url.return_value = False
    store = DedupeStore(tmp_path / "db")
    summary, _ = run_pipeline(
        FeedsConfig(feeds=[source.feed], quality=policy),
        settings,
        store,
        writer,
        wp,
        report_path=tmp_path / "r.json",
    )
    assert summary["skipped"] == 1
    wp.create_post.assert_not_called()
    wp.upload_media.assert_not_called()
    assert store.get_processed_count() == 0


def test_wp_outage_stops_before_model(monkeypatch, tmp_path, source, settings):
    writer = Mock()
    wp = Mock()
    wp.recent_stories.side_effect = requests.ConnectionError("down")
    summary, _ = run_pipeline(
        FeedsConfig(feeds=[source.feed]),
        settings,
        DedupeStore(tmp_path / "db"),
        writer,
        wp,
        report_path=tmp_path / "r.json",
    )
    assert summary["error"] == 1
    writer.rewrite_sources.assert_not_called()
    wp.create_post.assert_not_called()


def test_wordpress_duplicate_failure_is_not_assumed_clear():
    wp = WordPressClient("https://example.org", "u", "p")
    wp.session = Mock()
    wp.session.get.side_effect = requests.Timeout()
    with pytest.raises(RuntimeError):
        wp.check_duplicate_by_source_url("https://example.org/source")


def test_duplicate_lookup_decodes_html_and_tracking():
    wp = WordPressClient("https://example.org", "u", "p")
    wp.session = Mock()
    wp.session.get.return_value.headers = {"X-WP-TotalPages": "1"}
    wp.session.get.return_value.json.return_value = [
        {
            "content": {
                "rendered": '<a href="https://example.org/s?id=3&amp;utm_source=fb">Source</a>'
            }
        }
    ]
    assert wp.check_duplicate_by_source_url("https://example.org/s?id=3")


def test_truncated_model_output_cannot_publish():
    writer = OpenAIRewriter("test")
    writer.client = Mock()
    writer.client.chat.completions.create.return_value.choices = [
        SimpleNamespace(finish_reason="length", message=SimpleNamespace(content="{}"))
    ]
    with pytest.raises(ValueError):
        writer._json("prompt", {}, writer.model)


def test_malformed_json_cannot_publish():
    writer = OpenAIRewriter("test")
    writer.client = Mock()
    writer.client.chat.completions.create.return_value.choices = [
        SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content='prefix {"headline":"story"}')
        )
    ]
    with pytest.raises(json.JSONDecodeError):
        writer._json("prompt", {}, writer.model)


def test_workflow_uses_array_arguments_and_serialization():
    text = __import__("pathlib").Path(".github/workflows/rss_to_wp.yml").read_text()
    assert "cancel-in-progress: false" in text
    assert '"${args[@]}"' in text
    run_block = text.split("shell: bash")[1].split("- name: Preserve")[0]
    assert "${{ inputs.single_feed }}" not in run_block
    restore_block = text.split("- name: Restore database")[1].split("- name:")[0]
    assert "if: inputs.dry_run != true" in restore_block
    save_block = text.split("- name: Preserve database")[1].split("- name:")[0]
    assert "inputs.dry_run != true" in save_block

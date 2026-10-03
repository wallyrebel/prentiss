"""Collect across feeds before reviewing and publishing; every gate fails closed."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pendulum

from rss_to_wp.config import AppSettings, FeedsConfig
from rss_to_wp.editorial import (
    LOCAL_TERMS,
    Source,
    assessment_key,
    canonical_source_url,
    plain_text,
    unique_source_text,
    words,
)
from rss_to_wp.feeds import (
    get_entry_content,
    get_entry_link,
    get_entry_title,
    parse_feed,
    pick_entries,
)
from rss_to_wp.feeds.filter import parse_entry_date
from rss_to_wp.feeds.official import parse_official_news
from rss_to_wp.images import download_image, find_fallback_image, find_rss_image
from rss_to_wp.rewriter.openai_client import EditorialResponseError


def error_details(exc):
    """Keep raw exception messages and response bodies out of run artifacts."""
    result = {"reason": type(exc).__name__}
    if isinstance(exc, EditorialResponseError):
        result["diagnostics"] = exc.diagnostics
    return result


def collect_sources(
    config: FeedsConfig, settings: AppSettings, store, hours: int, report: list[dict]
) -> list[Source]:
    by_url = {}
    for feed_config in config.feeds:
        source_hours = feed_config.max_age_hours or hours
        try:
            feed = (
                parse_official_news(feed_config.url, source_hours)
                if feed_config.source_type == "ms_sos_news"
                else parse_feed(feed_config.url)
            )
        except Exception as exc:
            report.append(
                {
                    "status": "error",
                    "feed": feed_config.name,
                    "reason": type(exc).__name__,
                }
            )
            continue
        if feed is None:
            report.append(
                {
                    "status": "error",
                    "feed": feed_config.name,
                    "reason": "feed_fetch_failed",
                }
            )
            continue
        # A valid empty feed is not an operational failure. Consider ALL fresh
        # entries; the publish quota must not hide older qualifying stories.
        entries = pick_entries(
            feed.entries,
            max_count=len(feed.entries),
            hours_window=source_hours,
            timezone=settings.timezone,
        )
        for entry in entries:
            try:
                url = canonical_source_url(get_entry_link(entry) or "")
                if store.is_source_processed(url):
                    report.append({"status": "duplicate", "url": url})
                    continue
                date = parse_entry_date(entry)
                source = Source(
                    entry,
                    feed_config,
                    f"link:{url}",
                    url,
                    plain_text(get_entry_title(entry)),
                    plain_text(get_entry_content(entry)),
                    pendulum.instance(date).in_timezone(settings.timezone).isoformat(),
                    feed_config.source_name or plain_text(feed.feed.get("title", feed_config.name)),
                )
                if len(source.text) > 30000:
                    report.append(
                        {
                            "status": "skipped",
                            "url": url,
                            "reason": "source_too_large_for_automatic_review",
                        }
                    )
                    continue
                if url not in by_url or len(source.text) > len(by_url[url].text):
                    by_url[url] = source
            except (ValueError, TypeError) as exc:
                report.append(
                    {
                        "status": "error",
                        "feed": feed_config.name,
                        "reason": type(exc).__name__,
                    }
                )
    # Prioritize a local connection and substantive source material.
    return sorted(
        by_url.values(),
        key=lambda s: (
            bool(LOCAL_TERMS.search(s.text)),
            len(words(s.text)),
            s.published,
        ),
        reverse=True,
    )


def publish_article(article, sources, config, settings, wp):
    primary = sources[0]
    category_names = [primary.feed.default_category] if primary.feed.default_category else []
    for rule in config.category_rules:
        if rule.matches(article["headline"]) and rule.category not in category_names:
            category_names.append(rule.category)
    category_ids = []
    for name in category_names:
        cat_id = wp.get_or_create_category(name)
        if not cat_id:
            raise RuntimeError("Category could not be resolved")
        category_ids.append(cat_id)
    media_id = None
    image_result = None
    image_url = find_rss_image(primary.entry, base_url=primary.url)
    if image_url:
        image_result = download_image(image_url)
    if not image_result and settings.use_stock_images:
        fallback = find_fallback_image(
            title=article["headline"],
            feed_name=primary.feed.name,
            pexels_key=settings.pexels_api_key,
            unsplash_key=settings.unsplash_access_key,
        )
        if fallback:
            image_result = download_image(fallback["url"])
            if image_result:
                # Visible caption avoids suggesting stock depicts the actual event.
                article[
                    "body"
                ] += "<p><em>Featured image is an illustrative stock photograph.</em></p>"
    if image_result:
        image_bytes, filename, _ = image_result
        media_id = wp.upload_media(image_bytes, filename, alt_text="")
    return wp.create_post(
        title=article["headline"],
        content=article["body"],
        excerpt=article["excerpt"],
        category_ids=category_ids,
        tag_ids=wp.get_or_create_tags(primary.feed.default_tags),
        featured_media_id=media_id,
        sources=[{"url": s.url, "name": s.source_name} for s in sources],
    )


def run_pipeline(
    config,
    settings,
    store,
    rewriter,
    wp,
    dry_run=False,
    hours=72,
    report_path=Path("data/run-report.json"),
):
    report = []
    published = []
    per_feed = Counter()
    attempts = 0
    try:
        recent_hours = max([hours] + [f.max_age_hours or hours for f in config.feeds])
        recent_stories = wp.recent_stories(recent_hours) if wp else []
        sources = collect_sources(config, settings, store, hours, report)
        # Remove existing sources individually BEFORE grouping so one old source
        # cannot prevent new complementary sources from being reconsidered.
        if wp:
            fresh = []
            for source in sources:
                if wp.check_duplicate_by_source_url(source.url):
                    report.append({"status": "duplicate", "url": source.url})
                else:
                    fresh.append(source)
            sources = fresh
        groups = (
            rewriter.group_sources(sources, config.quality)
            if len(sources) > 1
            else [[s] for s in sources]
        )
        for group in groups:
            record = {
                "urls": [s.url for s in group],
                "source_words": len(words(unique_source_text(group))),
            }
            key = assessment_key(
                group,
                config.quality,
                settings.openai_model + settings.openai_review_model,
            )
            if record["source_words"] < config.quality.min_source_words:
                report.append(
                    {
                        **record,
                        "status": "skipped",
                        "reason": "insufficient_source_words",
                    }
                )
                continue
            cached = store.rejection_reason(key)
            if cached:
                report.append({**record, "status": "skipped", "reason": cached, "cached": True})
                continue
            if (
                len(published) >= config.quality.max_posts_per_run
                or attempts >= config.quality.max_reviews_per_run
                or any(per_feed[s.feed.name] >= s.feed.max_per_run for s in group)
            ):
                report.append({**record, "status": "deferred", "reason": "run_limit"})
                continue
            try:
                # Check WordPress before model/image spending. Errors are not
                # interpreted as permission to publish. Dry runs perform no WP IO.
                attempts += 1
                article = rewriter.rewrite_sources(
                    group, config.quality, recent_stories=recent_stories
                )
                if article.get("skip"):
                    reason = article.get("reason", "editorial_rejection")
                    report.append(
                        {
                            **record,
                            "status": "skipped",
                            "reason": reason,
                            "article_words": article.get("article_words"),
                            "review": article.get("review"),
                        }
                    )
                    if not dry_run:
                        store.reject(key, reason)
                    continue
                if dry_run:
                    post = {"id": 0, "title": {"rendered": article["headline"]}}
                else:
                    post = publish_article(article, group, config, settings, wp)
                    if not post:
                        raise RuntimeError("WordPress did not confirm creation")
                    if post.get("duplicate"):
                        report.append({**record, "status": "duplicate"})
                        continue
                    if not isinstance(post.get("id"), int) or post["id"] <= 0:
                        raise RuntimeError("WordPress returned no valid post ID")
                    wp.verify_post(
                        post["id"],
                        expected_status=settings.wordpress_post_status,
                        expected_content=article["body"],
                        source_urls=[s.url for s in group],
                    )
                    for source in group:
                        store.mark_processed(
                            source.key,
                            source.feed.url,
                            source.title,
                            source.url,
                            post["id"],
                            post.get("link"),
                        )
                published.append(post)
                recent_stories.append(
                    {
                        "id": post["id"],
                        "title": article["headline"],
                        "excerpt": article["excerpt"],
                    }
                )
                for name in {s.feed.name for s in group}:
                    per_feed[name] += 1
                report.append(
                    {
                        **record,
                        "status": ("would_publish" if dry_run else settings.wordpress_post_status),
                        "headline": article["headline"],
                        "body": article["body"],
                        "article_words": len(words(article["body"])),
                        "post_id": post["id"],
                        "url": post.get("link"),
                        "review": article["review"],
                    }
                )
            except Exception as exc:
                # Avoid serializing API exceptions, which can expose auth data.
                report.append(
                    {
                        **record,
                        "status": "error",
                        **error_details(exc),
                        "replay": {
                            "sources": [s.payload() for s in group],
                            "policy": config.quality.model_dump(),
                            "recent_stories": recent_stories,
                        },
                    }
                )
    except Exception as exc:
        report.append(
            {
                "status": "error",
                **error_details(exc),
                "stage": "collect_or_wordpress_preflight",
            }
        )
    finally:
        report_path = Path(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        summary = dict(Counter(row["status"] for row in report))
        report_path.write_text(
            json.dumps(
                {"dry_run": dry_run, "summary": summary, "decisions": report},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return summary, published

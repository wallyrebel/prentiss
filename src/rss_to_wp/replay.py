"""One bounded editorial replay; no WordPress client, images or database."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from rss_to_wp.config import FeedConfig, QualityPolicy
from rss_to_wp.editorial import (
    Source,
    canonical_source_url,
    unique_source_text,
    words,
    review_schema,
    validate_article,
    validate_review,
)
from rss_to_wp.pipeline import error_details
from rss_to_wp.rewriter.openai_client import OpenAIRewriter, REVIEW_PROMPT


def replay(snapshot: dict, rewriter: OpenAIRewriter) -> dict:
    policy = QualityPolicy.model_validate(snapshot.get("policy", {}))
    rows = snapshot["sources"]
    if not 1 <= len(rows) <= policy.max_sources_per_article:
        raise ValueError("Replay requires one bounded source group")
    sources = []
    for row in rows:
        url = canonical_source_url(row["url"])
        if not isinstance(row["text"], str) or len(row["text"]) > 30000:
            raise ValueError("Replay source text exceeds production limit")
        sources.append(
            Source(
                {},
                FeedConfig(name="Replay", url=url),
                "link:" + url,
                url,
                row["title"],
                row["text"],
                row["source_published_at"],
                row["source_name"],
            )
        )
    record = {
        "dry_run": True,
        "urls": [s.url for s in sources],
        "source_words": len(words(unique_source_text(sources))),
    }
    if record["source_words"] < policy.min_source_words:
        return {**record, "status": "skipped", "reason": "insufficient_source_words"}
    try:
        if snapshot.get("review_request"):
            request = snapshot["review_request"]
            payload = {
                "audience": policy.audience,
                "min_words": policy.min_article_words,
                "max_words": min(
                    policy.max_article_words,
                    int(record["source_words"] * policy.max_expansion_ratio),
                ),
                "min_facts": policy.min_facts,
                "sources": [s.payload() for s in sources],
                "schema": review_schema(sources),
                "recent_stories": snapshot.get("recent_stories", []),
                **{
                    key: request[key]
                    for key in ("article", "previous_review", "validation_errors", "instruction")
                    if key in request
                },
            }
            data = rewriter._json(REVIEW_PROMPT, payload, rewriter.review_model)
            review = rewriter._review(data)
            errors = validate_article(payload["article"], sources, policy) + validate_review(
                review, sources, policy
            )
            return {
                **record,
                "status": "reviewed",
                "validation_errors": errors,
                "review": review.model_dump(),
                "response": rewriter.last_response_diagnostics,
            }
        result = rewriter.rewrite_sources(sources, policy, snapshot.get("recent_stories", []))
        return {
            **record,
            "status": "skipped" if result.get("skip") else "would_publish",
            "result": result,
        }
    except Exception as exc:
        return {**record, "status": "error", **error_details(exc)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/editorial-replay.json"))
    args = parser.parse_args()
    model = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
    rewriter = OpenAIRewriter(
        os.environ["OPENAI_API_KEY"],
        model,
        review_model=os.getenv("OPENAI_REVIEW_MODEL", model),
    )
    result = replay(json.loads(args.snapshot.read_text()), rewriter)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "result"}))
    return 1 if result["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())

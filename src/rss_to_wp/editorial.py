"""Fail-closed editorial validation and conservative source grouping."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from rss_to_wp.config import FeedConfig, QualityPolicy

POLICY_VERSION = "2026-10-02-v2"
LOCAL_TERMS = re.compile(
    r"\b(Prentiss County|Booneville|Baldwyn|Jumpertown|New Site|Thrasher|Wheeler|NEMCC|Northeast Mississippi Community College)\b",
    re.I,
)


def plain_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = unicodedata.normalize("NFKC", soup.get_text(" ", strip=True))
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    text = re.sub(r"\s+", " ", text).strip()
    # HTML boundaries around linked/bold phrases insert artificial spaces.
    # Preserve words and punctuation while matching actual contiguous quotes.
    return re.sub(r"\s+([,.;:!?])", r"\1", text)


def words(text: str) -> list[str]:
    return re.findall(r"\b\w+(?:['’-]\w+)*\b", plain_text(text))


def canonical_source_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"https", "http"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise ValueError("Source must be a public HTTP(S) URL")
    host = parts.hostname.lower()
    if host in {"m.facebook.com", "www.facebook.com"}:
        host = "www.facebook.com"
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query)
        if not k.lower().startswith("utm_")
        and k.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
    ]
    return urlunsplit(
        (
            parts.scheme.lower(),
            host + (f":{parts.port}" if parts.port else ""),
            parts.path.rstrip("/") or "/",
            urlencode(sorted(query)),
            "",
        )
    )


@dataclass
class Source:
    entry: dict
    feed: FeedConfig
    key: str
    url: str
    title: str
    text: str
    published: str
    source_name: str

    def payload(self) -> dict:
        return {
            "title": self.title,
            "text": self.text,
            "url": self.url,
            "source_name": self.source_name,
            "source_published_at": self.published,
        }


def unique_source_text(sources: list[Source]) -> str:
    """Syndicated/repeated sentences do not create more supporting material."""
    sentences: list[str] = []
    for source in sources:
        for sentence in re.split(r"(?<=[.!?])\s+", source.text):
            if sentence and not any(
                SequenceMatcher(None, sentence.lower(), old.lower()).ratio() > 0.85
                for old in sentences
            ):
                sentences.append(sentence)
    return " ".join(sentences)


def candidate_groups(sources: list[Source], policy: QualityPolicy) -> list[list[Source]]:
    """Only propose merging short sources with substantial shared vocabulary.

    This is a candidate heuristic, not an assertion that two reports match.
    The independent review must confirm the same event, people, place and time.
    """
    groups: list[list[Source]] = []
    stop = set(
        "with this that from have will their they been more about county mississippi today tomorrow facebook photos post school department college community news says said please information".split()
    )

    def tokens(s):
        return {w.lower() for w in words(s.text) if len(w) > 3 and w.lower() not in stop}

    for source in sources:
        own = tokens(source)
        placed = False
        if len(words(source.text)) < policy.min_source_words:
            for group in groups:
                if len(group) >= policy.max_sources_per_article or any(
                    len(words(s.text)) >= policy.min_source_words for s in group
                ):
                    continue
                # Complete linkage avoids chaining distinct events together.
                if all(
                    len(own & tokens(s)) >= 8
                    and len(own & tokens(s)) / max(1, len(own | tokens(s))) >= 0.4
                    and abs(
                        datetime.fromisoformat(source.published).timestamp()
                        - datetime.fromisoformat(s.published).timestamp()
                    )
                    <= 86400
                    for s in group
                ):
                    group.append(source)
                    placed = True
                    break
        if not placed:
            groups.append([source])
    return groups


def assessment_key(sources: list[Source], policy: QualityPolicy, model: str) -> str:
    parts = sorted(f"{s.url}|{s.text}" for s in sources)
    return hashlib.sha256(
        (POLICY_VERSION + policy.model_dump_json() + model + "|".join(parts)).encode()
    ).hexdigest()


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=5)
    quote: str = Field(min_length=15)
    source_index: int = Field(ge=0, strict=True)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: StrictBool
    reason: str
    coherent_scope: StrictBool
    all_claims_supported: StrictBool
    complete_5w: StrictBool
    locally_relevant: StrictBool
    useful_details: StrictBool
    no_padding: StrictBool
    attribution_correct: StrictBool
    no_conflicts: StrictBool
    not_duplicate: StrictBool
    coverage: dict[str, Evidence]
    facts: list[Evidence]


def validate_article(article: dict, sources: list[Source], policy: QualityPolicy) -> list[str]:
    errors = []
    for field in ("headline", "excerpt", "body"):
        if not isinstance(article.get(field), str) or not article[field].strip():
            errors.append(f"missing_{field}")
    if errors:
        return errors
    if "<" in article["headline"] or "<" in article["excerpt"]:
        errors.append("markup_in_metadata")
    soup = BeautifulSoup(article["body"], "html.parser")
    # The publisher appends trusted source links; model HTML cannot add links,
    # hidden text, attributes, embeds or scripts to inflate word counts.
    if any(
        tag.name not in {"p", "h2", "ul", "ol", "li", "strong", "em", "blockquote"} or tag.attrs
        for tag in soup.find_all(True)
    ):
        errors.append("unsafe_html")
    count = len(words(article["body"]))
    if not policy.min_article_words <= count <= policy.max_article_words:
        errors.append("article_word_count")
    if count > len(words(unique_source_text(sources))) * policy.max_expansion_ratio:
        errors.append("excessive_expansion")
    paragraphs = [p.get_text(" ", strip=True).lower() for p in soup.find_all("p")]
    if len(paragraphs) < 4:
        errors.append("too_few_paragraphs")
    if len(set(paragraphs)) != len(paragraphs):
        errors.append("repeated_paragraphs")
    if len(article["headline"]) > 160 or len(words(article["excerpt"])) > 65:
        errors.append("metadata_too_long")
    return errors


def validate_review(review: Review, sources: list[Source], policy: QualityPolicy) -> list[str]:
    errors = []
    for field in (
        "approved",
        "coherent_scope",
        "all_claims_supported",
        "complete_5w",
        "locally_relevant",
        "useful_details",
        "no_padding",
        "attribution_correct",
        "no_conflicts",
        "not_duplicate",
    ):
        if getattr(review, field) is not True:
            errors.append(field)
    if set(review.coverage) != {"who", "what", "where", "when", "why", "local_relevance"}:
        errors.append("missing_5w_evidence")
    if len({e.answer.casefold().strip() for e in review.facts}) < policy.min_facts:
        errors.append("too_few_distinct_facts")
    if len({e.quote.casefold().strip() for e in review.facts}) < policy.min_facts:
        errors.append("repeated_fact_evidence")
    evidence = list(review.coverage.values()) + review.facts
    for e in evidence:
        if (
            e.source_index >= len(sources)
            or plain_text(e.quote).casefold()
            not in plain_text(sources[e.source_index].text).casefold()
        ):
            errors.append("unverified_evidence")
            break
    if set(e.source_index for e in evidence) != set(range(len(sources))):
        errors.append("unused_source")
    return errors


def validate_proposed_groups(proposals, sources, policy):
    """A planner can nominate sources, never waive the article/review gates."""
    groups = []
    used = set()
    if not isinstance(proposals, list):
        return groups
    for indexes in proposals:
        if not isinstance(indexes, list) or not 2 <= len(indexes) <= policy.max_sources_per_article:
            continue
        if any(type(i) is not int or i < 0 or i >= len(sources) for i in indexes):
            continue
        if len(set(indexes)) != len(indexes) or used.intersection(indexes):
            continue
        group = [sources[i] for i in indexes]
        dates = [datetime.fromisoformat(s.published).timestamp() for s in group]
        if max(dates) - min(dates) > 72 * 3600:
            continue
        if len(words(unique_source_text(group))) < policy.min_source_words:
            continue
        used.update(indexes)
        groups.append(group)
    return groups

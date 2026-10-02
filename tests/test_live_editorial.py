"""Opt-in model checks. Synthetic examples are reviewed, never sent to WordPress."""

import os

import pytest

from rss_to_wp.config import FeedConfig, QualityPolicy
from rss_to_wp.editorial import Review, Source, review_schema, validate_review
from rss_to_wp.rewriter.openai_client import REVIEW_PROMPT, OpenAIRewriter

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_EDITORIAL_EVALS") != "1", reason="Live model evaluation is opt-in"
)

# Fictional editorial fixture, not an actual announcement or publishing input.
PARAGRAPHS = [
    "Booneville Public Library will hold a family reading workshop on October 8, 2026, from 4 to 6 p.m. The free workshop takes place in the library meeting room at 100 Main Street in Booneville. Library director Jane Smith said the program will help parents practice reading aloud with young children.",
    "Families should register by October 6 by calling the library during regular opening hours. Telephone registration is already available on weekdays. Saturday registration calls will be accepted on October 3 from 9 a.m. to noon. The Saturday service adds an option without changing weekday registration availability.",
    "Registration is limited to twenty families because the meeting room has limited seating. Each family will receive two books purchased through a donation from the library friends group. Children must attend with a parent or another adult caregiver throughout the afternoon session.",
    "Library staff will demonstrate three reading exercises and give families time to practice each one together. No library card is required to attend the workshop or receive the donated books. Participants should enter through the main entrance, which has an accessible ramp beside the parking lot.",
    "The library will provide printed instructions describing the exercises so families can repeat them at home. Smith said the program was developed in response to parent requests collected during the summer reading program. Staff will collect feedback after the workshop to determine whether another session should be offered later this year.",
    "The workshop is part of the library's October family literacy series. Staff will offer both large-print and standard-print instructions. Families can choose books in English or Spanish. The library will not charge a registration fee or require a deposit.",
]


@pytest.mark.parametrize("incorrect_schedule", [False, True])
def test_live_reviewer_preserves_schedule_qualifiers(incorrect_schedule):
    source = Source(
        {},
        FeedConfig(name="Evaluation fixture", url="https://example.org/feed"),
        "fixture",
        "https://example.org/fixture",
        "Library workshop",
        " ".join(PARAGRAPHS),
        "2026-10-01T09:00:00-05:00",
        "Booneville Public Library",
    )
    body = "".join(f"<p>{paragraph}</p>" for paragraph in PARAGRAPHS)
    if incorrect_schedule:
        body = body.replace(
            "Telephone registration is already available on weekdays. Saturday registration calls will be accepted on October 3 from 9 a.m. to noon. The Saturday service adds an option without changing weekday registration availability.",
            "Telephone registration begins on Saturday, October 3, from 9 a.m. to noon. Registration is not available before that Saturday opening. Families must wait until October 3 to call the library and register for the workshop.",
        )
    model = os.getenv("OPENAI_REVIEW_MODEL", "gpt-4.1")
    writer = OpenAIRewriter(os.environ["OPENAI_API_KEY"], model=model, review_model=model)
    data = writer._json(
        REVIEW_PROMPT,
        {
            "audience": QualityPolicy().audience,
            "min_facts": 6,
            "sources": [source.payload()],
            "schema": review_schema([source]),
            "recent_stories": [],
            "article": {
                "headline": "Booneville library schedules October family reading workshop",
                "excerpt": "The free family workshop offers reading exercises and books on October 8. Registration is required by October 6.",
                "body": body,
            },
        },
        model,
    )
    review = Review.model_validate(data)
    if incorrect_schedule:
        assert not review.approved and not review.all_claims_supported, review.reason
    else:
        assert not validate_review(review, [source], QualityPolicy()), review.reason

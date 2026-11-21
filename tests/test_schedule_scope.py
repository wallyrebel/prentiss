import json
from pathlib import Path

from rss_to_wp.rewriter.openai_client import voting_schedule_conflict


def test_archived_schedule_does_not_move_the_start_of_all_absentee_voting():
    fixture = json.loads((Path(__file__).parent / "fixtures/election_schedule.json").read_bytes())
    assert voting_schedule_conflict(fixture["sources"], fixture["unsupported_article"])
    assert voting_schedule_conflict(fixture["sources"], fixture["supported_article"]) is None
    assert voting_schedule_conflict([], fixture["unsupported_article"]) is None

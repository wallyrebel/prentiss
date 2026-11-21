from types import SimpleNamespace

from rss_to_wp.feeds.county_basketball import matched_program_results


def test_pair_requires_two_different_teams_of_the_same_programs_on_the_same_date():
    def record(level, day="2026-11-20", opponent="Thrasher", feed_type="county_scores"):
        return SimpleNamespace(feed=SimpleNamespace(source_type=feed_type),
                               entry={"county_matchup": (day, "New Site", opponent), "team_level": level})

    boys, girls = record("boys"), record("girls")
    other = [record("girls", opponent="Wheeler"), record("girls", day="2026-11-19"),
             record("girls", feed_type="rss")]
    assert matched_program_results([boys, *other]) == []
    assert matched_program_results([boys, girls, *other]) == [[boys, girls]]
    assert matched_program_results([boys, record("boys"), *other]) == []

from datetime import datetime

from bs4 import BeautifulSoup
import pytest
import requests

from rss_to_wp.feeds.northeast_scores import CENTRAL, COLLEGE, read_final
from rss_to_wp.feeds import northeast_scores


def test_only_confirmed_future_live_stats_skip_box_lookup(monkeypatch):
    from datetime import timedelta
    future = datetime.now(CENTRAL) + timedelta(days=1)
    code = future.strftime("%Y%m%d")
    schedule = northeast_scores.HOST + "/sports/mbkb/index"
    path = f"/sports/mbkb/2026-27/boxscores/{code}_abcd.xml"
    def listing(status):
        return BeautifulSoup(f"""
          <a href="/sports/mbkb/2026-27/schedule">Schedule</a>
          <a href="{path}">Live stats</a>
          <div class="card-body"><div class="sport-date-status"><span class="status">{status}</span></div>
          <a href="{path}" title="Live stats">Live stats</a></div>""", "html.parser")
    pages = {schedule: listing("7:30 PM " + future.tzname())}
    monkeypatch.setattr(northeast_scores, "_page", pages.__getitem__)
    monkeypatch.setattr(northeast_scores, "is_within_window", lambda *args, **kwargs: True)
    assert not northeast_scores._live_scores(schedule, 72).entries
    pages[schedule] = listing("Final")
    pages[northeast_scores.HOST + path] = BeautifulSoup("<main>Unavailable</main>", "html.parser")
    with pytest.raises(ValueError, match="box score unavailable"):
        northeast_scores._live_scores(schedule, 72)


def box(status, total="70"):
    return BeautifulSoup(f"""
      <article class="game-boxscore bkb">
        <div class="head"><h1><span>{COLLEGE}</span><span>vs.</span><span>Visitors</span>
        <span>November 20, 2026</span></h1></div>
        <div class="linescore"><table><tr><th>{status}</th><th class="score">1</th>
        <th class="score">2</th><th class="score total">T</th></tr>
        <tr><th scope="row">{COLLEGE}</th><td class="score">30</td><td class="score">40</td>
        <td class="score total">{total}</td></tr>
        <tr><th scope="row">Visitors</th><td class="score">31</td><td class="score">30</td>
        <td class="score total">61</td></tr></table></div>
        <div class="team-stats"><table><tr><th>Stat</th><th>{COLLEGE}</th><th>Visitors</th></tr>
        <tr><th scope="row">Rebounds</th><td>35</td><td>30</td></tr></table></div>
      </article><footer>Previous game scores are not evidence</footer>""", "html.parser")


def test_only_confirmed_matching_box_scores_supply_evidence():
    day = datetime(2026, 11, 20, tzinfo=CENTRAL)
    assert read_final(box("Halftime"), "box", day, "Men's") is None
    result = read_final(box("Final"), "box", day, "Men's")
    assert "Visitors 61" in result.title and "Rebounds" in result.summary
    assert "Previous game" not in result.summary
    with pytest.raises(ValueError, match="Period scores"):
        read_final(box("Final", total="77"), "box", day, "Men's")
    with pytest.raises(ValueError, match="date"):
        read_final(box("Final"), "box", day.replace(day=19), "Men's")


def test_retained_finals_expire_and_never_replace_a_conflicting_box(monkeypatch, tmp_path):
    from unittest.mock import Mock
    import json
    url = northeast_scores.HOST + "/sports/mbkb/index"
    entry = {"title":"Verified final", "published":"2026-11-20T00:00:00-06:00",
             "link":northeast_scores.HOST+"/sports/mbkb/2026-27/boxscores/20261120_abcd.xml", "summary":"Final"}
    retained = tmp_path / "scores.json"
    retained.write_text(json.dumps({"checked_at":"2026-11-20T18:00:00+00:00", "listings":{url:[entry]}}))
    monkeypatch.setattr(northeast_scores, "RETAINED", retained)
    live = Mock(side_effect=requests.HTTPError("unavailable"))
    monkeypatch.setattr(northeast_scores, "_live_scores", live)
    monkeypatch.setattr(northeast_scores, "is_within_window", Mock(return_value=True))
    assert northeast_scores.parse_college_scores(url, 72).entries
    monkeypatch.setattr(northeast_scores, "is_within_window", Mock(return_value=False))
    with pytest.raises(requests.HTTPError):
        northeast_scores.parse_college_scores(url, 72)
    live.side_effect = ValueError("Scores disagree")
    with pytest.raises(ValueError, match="Scores disagree"):
        northeast_scores.parse_college_scores(url, 72)

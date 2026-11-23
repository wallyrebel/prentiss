from datetime import datetime
from types import SimpleNamespace

from bs4 import BeautifulSoup
import pytest
import requests

from rss_to_wp.editorial import unique_source_text
from rss_to_wp.feeds import northeast_scores
from rss_to_wp.feeds.northeast_scores import CENTRAL, COLLEGE, read_final


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


def test_box_keeps_distinct_stats_in_team_order_and_reads_labeled_player_columns():
    official = box('Final')
    extra = BeautifulSoup('<tr><th scope="row">Assists</th><td>18</td><td>6</td></tr>', 'html.parser')
    official.select_one('.team-stats table').append(extra.tr)
    players = BeautifulSoup(f'''<div id="boxscore-tabpanel"><div class="player-stats"><table>
      <caption>{COLLEGE}</caption><tr><th>Player</th><th>AST</th><th>FGM-A</th><th>REB</th><th>PTS</th></tr>
      <tr><th class="player-name">Example Guard</th><td>4</td><td>5-8</td><td>3</td><td>13</td></tr>
      </table></div></div>''', 'html.parser')
    official.article.append(players.div)
    entry = read_final(official, 'box', datetime(2026, 11, 20, tzinfo=CENTRAL), "Women's")
    source = SimpleNamespace(text=entry.summary)
    distinct = unique_source_text([source, source])
    assert 'Rebounds: 35 versus 30; Assists: 18 versus 6' in distinct
    assert 'after period 1, 30 to 31; after period 2, 70 to 61' in distinct
    assert distinct.count('Example Guard: 13 points, 5 of 8 field goals, 3 rebounds, 4 assists') == 1


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


def test_play_by_play_requires_official_scorer_and_matching_period_total():
    page = BeautifulSoup('''<div id="pbp-tabpanel"><table><caption>1st Quarter</caption>
      <tr class="score-changed"><td class="time">08:22</td><td class="v-score">0</td>
      <td class="h-score">2</td><td class="play"><span class="text">Home Guard made layup</span>
      <img class="team-logo" alt="Home"/></td></tr></table></div>''', 'html.parser')
    parse = northeast_scores._scoring_plays
    evidence = parse(page, ['Away', 'Home'], [[0], [2]])
    assert len(evidence) == 1 and '08:22 remaining, Home: Home Guard made layup' in evidence[0]
    assert parse(page, ['Away', 'Home'], [[0], [3]]) == []
    page.img['alt'] = 'Away'
    assert parse(page, ['Away', 'Home'], [[0], [2]]) == []


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

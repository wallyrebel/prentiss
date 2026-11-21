from datetime import datetime

from bs4 import BeautifulSoup
import pytest

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

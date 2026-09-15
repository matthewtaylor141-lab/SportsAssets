"""Capture-leak closure (owner green-light 2026-08-17 evening, upgrade
#3): word-form market types stop masquerading as spreads, the copies
join is date-anchored, and an unmapped row now says WHY (near-miss
diagnostics with the venue's real strings)."""

import tempfile
import time

from edge.ledger.service import Ledger
from edge.shadow.copy_sports import market_type_of
from edge.shadow.kalshi_copies import sweep
from tests.test_kalshi_copies import _ROW, _Kalshi


def test_word_form_suffixes_classify_correctly():
    """These used to fall through to the bare-line fallback and read as
    SPREAD — letting exact-scores through spread cells and player props
    past the prop block."""
    assert market_type_of("lal-dep-elc-2026-08-17-exact-score-2-3") \
        == "exact_score"
    assert market_type_of("lal-dep-elc-2026-08-17-total-0pt5") == "total"
    assert market_type_of(
        "lal-dep-elc-2026-08-17-team-total-home-1pt5") == "prop"
    assert market_type_of(
        "lal-dep-elc-2026-08-17-corners-over-9pt5") == "prop"
    assert market_type_of(
        "nba-lal-bos-2026-08-17-player-points-james-25pt5") == "prop"
    # An unrecognized long word beside a line is an unknown market
    # type, never silently a spread.
    assert market_type_of("mlb-nyy-bos-2026-07-22-handicap-3pt5") \
        == "unknown"


def test_word_forms_do_not_break_the_known_grammar():
    assert market_type_of("mlb-nyy-bos-2026-07-22") == "moneyline"
    assert market_type_of("mlb-nyy-bos-2026-07-22-o8pt5") == "total"
    assert market_type_of("mlb-nyy-bos-2026-07-22-3pt5") == "spread"
    assert market_type_of("mlb-nyy-bos-2026-07-22-pos-2pt5") == "spread"


def test_copies_join_is_date_anchored():
    """A dated slug must never name-match a venue event on a DIFFERENT
    date — the same two teams meet again and a stale row would buy
    today's game (the adds sweep always had this anchor)."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(0.48)          # fixture event is dated 26AUG04
    row = {**_ROW, "slug": "wnba-dal-chi-2026-08-05",
           "entered_ts": time.time() - 60}
    st = sweep(kalshi=ka, ledger=led, identities=[row], live=True)
    assert st.get("skipped_unmapped") == 1
    assert not ka.orders


def test_unmapped_near_miss_names_the_venue_strings():
    """When a same-date event exists but the names miss the bar, the
    funnel entry must carry the venue's actual outcome strings and
    scores — the whale side alone never says why the join failed."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(0.48, outcomes={"KXWNBAGAME-26AUG04DALCHI-DAL": "T-DAL",
                                 "KXWNBAGAME-26AUG04DALCHI-CHI": "T-CHI"})
    st = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True)
    assert st.get("skipped_unmapped") == 1
    ex = st.get("unmapped_ex") or []
    assert ex and "near=" in ex[0] and "scores" in ex[0], ex


def test_market_title_supplies_full_name_candidates():
    """The identity row's market title ('A vs B') feeds the join full
    names, so a venue string can match at the bar even when the slug
    label alone cannot."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(0.48, outcomes={"Dallas Wings": "T-DAL",
                                 "Chicago Sky": "T-CHI"})
    # 0.73 against the venue string: misses the 0.95 hit bar alone,
    # passes the 0.6 candidate gate against the title side.
    row = {**_ROW, "outcome": "Dallas Wing Basket",
           "market_title": "Dallas Wings vs Chicago Sky"}
    st = sweep(kalshi=ka, ledger=led, identities=[row], live=True)
    assert st["copied"] == 1
    assert ka.orders and ka.orders[0][0] == "T-DAL"

"""RC6.2 LANE p-coverage: THE TERMS READER READS KALSHI'S OWN WORDS, AND
NOTHING IT READ BEFORE CHANGES.

Every Kalshi game-winner rule block read (research-sql run 37939739782 T3,
360 real blocks) settles the never-played game with "the market will resolve
to a fair price" -- unreadable to bettor_settlement_terms.read_terms, so the
condition read as venue-SILENT and every mapped Kalshi basketball, hockey
and NFL winner was labelled VENUE_RULES_SILENT_ON. Read, it is a price the
venue sets, not the bookmaker's stake return: a stated difference (stricter,
never looser). And Kalshi states the delayed-but-played game in a separate
sentence ("remain open and close after the rescheduled game has finished
(within two days)"), which "postponed" made a second C_NOT_PLAYED payout;
such a sentence describes a fixture that IS completed and is not read as a
rule for the never-completed one. The same correction removes a false
self-contradiction from the venue's UFC texts.

Fixture: tests/fixtures/venue_rules_texts_terms_reader_2026_10_09.json --
the venue's own texts, each with the reading b3f1b0cd's code gave it.
Label only for Kalshi (data-only venue: no fair-value source, no book);
no Polymarket US money-line text reads differently.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from sportsassets import bettor_settlement_terms as ST
from sportsassets.market_plane import populate as POP
from sportsassets.market_plane import rules as RULES
from sportsassets.market_plane import settlement as S

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
T = json.loads((FIX / "venue_rules_texts_terms_reader_2026_10_09.json")
               .read_text())


@pytest.mark.parametrize("r", T["pmus_money_lines"],
                         ids=lambda r: r["contract_id"])
def test_every_polymarket_money_line_text_reads_exactly_as_before(r):
    rt = ST.read_terms(r["rules_text"])
    assert {"terms": rt["terms"], "contradicted": rt["contradicted"]} == \
        r["terms_read_at_b3f1b0cd"]


def _kalshi(r):
    m = {"ticker": r["contract_id"].split(":", 1)[1],
         "event_ticker": r["event_ticker"],
         "rules_primary": r["rules_primary"],
         "rules_secondary": r["rules_secondary"], "status": "active",
         "market_type": "binary",
         "_series": {"ticker": r["series"], "tags": r["tags"],
                     "title": r["series_title"]}}
    c = POP.kalshi_contract_row(m, now=1.0)
    rr = RULES.kalshi_row(m)
    rr = dict(rr, rules_text="%s\n\n%s" % (rr["rules_text"],
                                           rr["rules_secondary"]))
    return c, rr


@pytest.mark.parametrize("r", T["kalshi_winners"],
                         ids=lambda r: r["series"])
def test_a_kalshi_winner_states_its_never_played_payout(r):
    c, rr = _kalshi(r)
    rt = ST.read_terms(rr["rules_text"])
    assert rt["terms"].get(ST.C_NOT_PLAYED) == ST.PAY_VENUE_FAIR_PRICE
    assert rt["contradicted"] == []
    st = S.state_for(c, rules=rr, rules_looked_up=True,
                     derivative_terms=True)
    assert st["state"] == S.NOT_PROVEN and not st["proven"]
    before = r["plane_state_at_b3f1b0cd"][3]
    if before.startswith(S.R_BOOK_TERMS_SCOPE):
        # soccer / baseball: the book side is still withheld for scope
        assert st["why"] == before
    else:
        assert st["why"].startswith(
            "%s:%s:" % (S.R_INCOMPATIBLE_NOT_PRICEABLE, ST.C_NOT_PLAYED)), \
            st["why"]
        # ...and only that condition: the ordinary result is still unstated
        terms = st["evidence"]["terms"]
        assert terms["mismatched_conditions"] == [ST.C_NOT_PLAYED]
        assert terms["per_condition"][ST.C_FULL]["verdict"] == \
            ST.V_VENUE_SILENT


def test_the_relabel_is_the_silent_rows_and_no_other():
    moved = [r for r in T["kalshi_winners"]
             if r["plane_state_at_b3f1b0cd"][3].startswith(
                 S.R_VENUE_RULES_SILENT_ON)]
    assert {r["tags"][0] for r in moved} == {"Basketball", "Hockey",
                                             "Football"}
    assert len(moved) >= 6


def test_a_delayed_then_played_sentence_is_not_the_never_played_rule():
    cfl = next(r for r in T["kalshi_winners"] if r["series"] == "KXCFLGAME")
    assert "remain open and close after the rescheduled game has finished" \
        in cfl["rules_secondary"]
    assert cfl["terms_read_at_b3f1b0cd"]["terms"][ST.C_NOT_PLAYED] == \
        ST.PAY_LATER
    rt = ST.read_terms("%s\n\n%s" % (cfl["rules_primary"],
                                     cfl["rules_secondary"]))
    assert rt["terms"][ST.C_NOT_PLAYED] == ST.PAY_VENUE_FAIR_PRICE
    used = [e for e in rt["evidence"][ST.C_NOT_PLAYED]
            if "remain open" in e["sentence"]]
    assert used and used[0]["used"] is False


@pytest.mark.parametrize("r", T["pmus_ufc"], ids=lambda r: r["contract_id"])
def test_a_ufc_text_is_no_longer_read_as_contradicting_itself(r):
    assert r["terms_read_at_b3f1b0cd"]["contradicted"] == [ST.C_NOT_PLAYED]
    rt = ST.read_terms(r["rules_text"])
    assert rt["contradicted"] == []
    assert rt["terms"][ST.C_NOT_PLAYED] == ST.PAY_LAST_FAIR_MARKET_PRICE


def test_the_fair_price_never_equals_a_stake_return():
    for cond in ST.CONDITIONS:
        assert not ST.same_payout(cond, ST.PAY_VENUE_FAIR_PRICE,
                                  ST.PAY_STAKE_BACK)
        assert not ST.same_payout(cond, ST.PAY_VENUE_FAIR_PRICE,
                                  ST.PAY_LAST_FAIR_MARKET_PRICE)
    assert ST.PAY_VENUE_FAIR_PRICE in ST.PAYOUTS

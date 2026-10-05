"""CAPITAL-CRITICAL: EVERY OPEN PAPER POSITION IN EXACTLY ONE FRESHNESS CLASS,
AND XAVIER'S MANAGEMENT PACKET GATE (pure).

  * the classifier's rule order (first match wins) and each class's evidence:
    EXTERNAL_UNAVAILABLE only with a venue state on an observation, FRESH /
    QUIET_VALID only on a successful read inside the 300 s SLA (the ledger's
    own MARK_STALE_AFTER_S, not a new number), UNMARKED only for a current
    book with no exit level, FEED_GAP only when our read path explains it,
    STALE otherwise;
  * exhaustive: random inputs always land in exactly one class, and the
    summary counts every position (none silently dropped); rates are null,
    never 0, with nothing markable;
  * the packet: HOLD / EXIT / REDUCE / a hedge only on a complete packet; an
    entry-time probability is NO_FRESH_PROBABILITY whatever else is present.
"""
from __future__ import annotations

import random

import pytest

from sportsassets import bettor_paper_freshness as PMF
from sportsassets import bettor_paper_ledger as L
from sportsassets import xavier_freshness as XF
from sportsassets import xavier_packet as XPK
from sportsassets.agents import paper_xavier as PX

NOW = 1_790_400_000.0


def md(bids=(), offers=()):
    return {"bids": [{"px": {"value": "%.2f" % p}, "qty": "%s" % q}
                     for p, q in bids],
            "offers": [{"px": {"value": "%.2f" % p}, "qty": "%s" % q}
                       for p, q in offers]}


def obs(oid, age, *, bids=((0.40, 50),), offers=((0.42, 50),), state=None):
    m = md(bids, offers)
    return {"obs_id": oid, "at": NOW - age, "bids": m["bids"],
            "offers": m["offers"], "market_state": state}


def cls(**kw):
    kw.setdefault("now", NOW)
    kw.setdefault("holding_side", "LONG")
    return PMF.classify(**kw)


def test_the_sla_is_the_ledgers_own_and_the_classes_are_exhaustive():
    assert PMF.SLA_S == L.MARK_STALE_AFTER_S == 300.0
    assert set(PMF.CLASSES) == {"FRESH", "QUIET_VALID", "STALE", "FEED_GAP",
                                "UNMARKED", "EXTERNAL_UNAVAILABLE"}
    assert set(PMF.RULES) == set(PMF.CLASSES)
    assert "EXTERNAL_UNAVAILABLE" not in PMF.MARKABLE
    assert PMF.FRESHLY_MANAGEABLE == ("FRESH", "QUIET_VALID")
    # the packet's notion of a current book IS the classifier's
    assert tuple(XPK.CURRENT_BOOK_CLASSES) == PMF.FRESHLY_MANAGEABLE
    assert XPK.E_FRESH == PX.E_FRESH == XF.E_FRESH
    assert 0 < PMF.MAX_STALE_MANAGEMENT_RATE < 1


def test_fresh_and_quiet_valid_need_a_successful_read_inside_the_sla():
    c = cls(last_ok=obs(2, 10), prev_ok=obs(1, 70, bids=((0.39, 50),)))
    assert c["class"] == PMF.FRESH
    assert c["mark"]["price"] == pytest.approx(0.40)
    assert c["mark"]["source"] == "paper_book_observations:2"
    assert c["mark"]["age_s"] == pytest.approx(10.0)
    assert c["mark"]["exit_depth_at_mark"] == pytest.approx(50.0)
    assert c["mark"]["bid"] == pytest.approx(0.40)
    assert c["mark"]["ask"] == pytest.approx(0.42)
    q = cls(last_ok=obs(2, 10), prev_ok=obs(1, 70))
    assert q["class"] == PMF.QUIET_VALID
    assert q["reason"] == "RE_READ_INSIDE_SLA_PRICE_UNCHANGED"
    # first read ever, inside the SLA
    assert cls(last_ok=obs(2, 299))["class"] == PMF.FRESH
    # the same unchanged price read 301 s ago is NOT quiet-valid: it is old
    old = cls(last_ok=obs(2, 301), prev_ok=obs(1, 400))
    assert old["class"] == PMF.STALE


def test_a_short_position_is_marked_on_its_own_exit_side():
    # SHORT exits by buying the complement: its exit consumes the offers
    c = cls(holding_side="SHORT", last_ok=obs(2, 5, bids=(),
                                              offers=((0.42, 30),)))
    assert c["class"] == PMF.FRESH
    assert c["mark"]["price"] == pytest.approx(0.58)
    assert c["mark"]["exit_depth_at_mark"] == pytest.approx(30.0)


def test_unmarked_is_a_current_book_without_an_exit_level():
    c = cls(last_ok=obs(2, 20, bids=(), offers=((0.42, 50),)))
    assert c["class"] == PMF.UNMARKED
    assert c["mark"]["price"] is None
    assert c["reason"].startswith("NO_EXECUTABLE_EXIT_LEVEL_IN_A_CURRENT_BOOK")


@pytest.mark.parametrize("state", ["MARKET_STATE_EXPIRED",
                                   "MARKET_STATE_CLOSED",
                                   "MARKET_STATE_TERMINATED",
                                   "MARKET_STATE_SETTLED"])
def test_external_unavailable_needs_the_venues_terminal_state_as_evidence(
        state):
    # permanent: any age; and it beats every other rule (first match)
    for age in (5, 5000):
        c = cls(last_ok=obs(9, age, state=state))
        assert c["class"] == PMF.EXTERNAL_UNAVAILABLE
        assert c["evidence"]["last_ok_obs_id"] == 9
        assert state in c["reason"]


def test_a_transient_not_open_state_is_evidence_only_inside_the_sla():
    c = cls(last_ok=obs(9, 30, state="MARKET_STATE_HALTED"))
    assert c["class"] == PMF.EXTERNAL_UNAVAILABLE
    # a halt read long ago proves nothing about now
    c = cls(last_ok=obs(9, 900, state="MARKET_STATE_HALTED"))
    assert c["class"] == PMF.STALE
    # an OPEN market is not external
    c = cls(last_ok=obs(9, 30, state="MARKET_STATE_OPEN"))
    assert c["class"] == PMF.FRESH


def test_feed_gap_is_our_read_path_failing_or_not_attempted():
    # the newest attempt after the last success failed
    c = cls(last_ok=obs(1, 900), last_attempt={"obs_id": 2, "at": NOW - 30,
                                               "error": "ReadTimeout"})
    assert c["class"] == PMF.FEED_GAP
    assert c["reason"] == "READ_FAILED:ReadTimeout"
    assert c["evidence"]["failed_obs_id"] == 2
    # a failure BEFORE the last success explains nothing: STALE
    c = cls(last_ok=obs(3, 900), last_attempt={"obs_id": 2,
                                               "at": NOW - 950,
                                               "error": "ReadTimeout"})
    assert c["class"] == PMF.STALE
    # the refresh run recorded it skipped (budget), after the last success
    c = cls(last_ok=obs(1, 900),
            run_outcome={"outcome": "SKIPPED_RUN_READ_CAP",
                         "why": "SKIPPED_RUN_READ_CAP", "run_id": 7,
                         "run_at": NOW - 20})
    assert c["class"] == PMF.FEED_GAP
    assert c["reason"].startswith("SKIPPED_RUN_READ_CAP")
    # never read at all
    assert cls(last_ok=None)["class"] == PMF.FEED_GAP
    assert cls(last_ok=None)["reason"] == "NEVER_READ"
    # a failed read inside the SLA does not hide a good read inside it
    c = cls(last_ok=obs(1, 40), last_attempt={"obs_id": 2, "at": NOW - 5,
                                              "error": "X"})
    assert c["class"] == PMF.FRESH


def test_random_inputs_always_land_in_exactly_one_class():
    rnd = random.Random(270)
    states = [None, "MARKET_STATE_OPEN", "MARKET_STATE_EXPIRED",
              "MARKET_STATE_HALTED", "garbage"]
    rows = []
    for i in range(3000):
        last_ok = None if rnd.random() < 0.15 else obs(
            i, rnd.uniform(0, 1500),
            bids=() if rnd.random() < 0.2 else ((rnd.choice((0.4, 0.5)),
                                                 10),),
            state=rnd.choice(states))
        prev = None if rnd.random() < 0.4 else obs(
            i - 1, rnd.uniform(0, 3000), bids=((rnd.choice((0.4, 0.5)), 10),))
        att = None if rnd.random() < 0.5 else {
            "obs_id": i + 1, "at": NOW - rnd.uniform(0, 2000),
            "error": rnd.choice([None, "E"])}
        run = None if rnd.random() < 0.6 else {
            "outcome": rnd.choice(["READ_OK", "SKIPPED_HOURLY_BUDGET",
                                   "READ_FAILED"]),
            "run_at": NOW - rnd.uniform(0, 2000)}
        c = cls(holding_side=rnd.choice(["LONG", "SHORT"]), last_ok=last_ok,
                prev_ok=prev, last_attempt=att, run_outcome=run)
        assert c["class"] in PMF.CLASSES
        assert c["rule"] == PMF.RULES[c["class"]]
        if c["class"] in PMF.FRESHLY_MANAGEABLE:
            # never fresh on an old or absent read
            assert last_ok is not None and c["mark"]["age_s"] <= PMF.SLA_S
            assert c["mark"]["price"] is not None
        rows.append(dict(c, strategy=rnd.choice(["A", "B"])))
    s = PMF.summarize(rows)
    assert s["open_positions"] == s["classified"] == len(rows)
    assert sum(v["count"] for v in s["counts"].values()) == len(rows)
    assert s["markable"] == len(rows) - s["counts"][
        "EXTERNAL_UNAVAILABLE"]["count"]
    assert s["fresh_rate"] == pytest.approx(
        s["freshly_manageable"] / s["markable"], abs=1e-6)


def test_rates_are_null_never_zero_with_nothing_markable():
    s = PMF.summarize([])
    assert s["fresh_rate"] is None and s["stale_management_rate"] is None
    assert s["meets_target"] is None
    ext = cls(last_ok=obs(1, 5, state="MARKET_STATE_EXPIRED"))
    s = PMF.summarize([dict(ext, strategy="A")])
    assert s["markable"] == 0 and s["fresh_rate"] is None
    assert s["by_strategy"]["A"]["allocation_blocked"] is False


def test_the_allocation_threshold_is_predeclared_and_per_strategy():
    fresh = dict(cls(last_ok=obs(1, 5)), strategy="A")
    stale = dict(cls(last_ok=obs(1, 900)), strategy="A")
    s = PMF.summarize([fresh] * 4 + [stale])            # 20% stale
    assert s["by_strategy"]["A"]["stale_management_rate"] == pytest.approx(
        0.2)
    assert s["by_strategy"]["A"]["allocation_blocked"] is False
    s = PMF.summarize([fresh] * 3 + [stale] * 2)        # 40% stale
    assert s["by_strategy"]["A"]["allocation_blocked"] is True


def test_settlement_fingerprint_and_protection_state():
    ident = {"us_market_slug": "m", "holding_side": "LONG",
             "payout_event": "HOME", "payout_is_complement": False,
             "fixture": "fx"}
    fp = PMF.settlement_fingerprint(ident)
    assert fp and fp == PMF.settlement_fingerprint(dict(ident))
    assert fp != PMF.settlement_fingerprint(dict(ident, holding_side="SHORT"))
    assert PMF.settlement_fingerprint(dict(ident, payout_event=None)) is None
    p = PMF.protection_state([{"order_id": "o", "state": "RESTING",
                               "qty": 10, "filled_qty": 0,
                               "limit_price": 0.5}], 10)
    assert p["state"] == "PROTECTED_RESTING" and p["known"]
    assert PMF.protection_state([], 10)["state"] == \
        "UNPROTECTED_NO_STANDING_ORDER"
    assert PMF.protection_state([{"order_id": "o", "state":
                                  "CANCEL_PENDING"}], 10)["state"] == \
        "PROTECTION_CANCEL_PENDING"


# ═════════════════════════════════════════════════════════════════════
# THE MANAGEMENT PACKET (pure)
# ═════════════════════════════════════════════════════════════════════

def _packet(**over):
    kw = dict(residual={"open_qty": 10, "ledger_open_qty": 10,
                        "reconciled": True},
              evidence_state=XF.E_FRESH, probability_source="PINNACLE_ONLY",
              valuation_id=5,
              mark={"obs_id": 3, "age_s": 5, "bid": 0.4, "ask": 0.42,
                    "exit_depth_at_mark": 50, "exit_depth_total": 80},
              mark_class="FRESH",
              settlement={"fingerprint": "settle:x", "payout_event": "HOME",
                          "payout_is_complement": False},
              protection={"state": "PROTECTED_RESTING", "known": True})
    kw.update(over)
    return XPK.build(**kw)


def test_a_complete_packet_permits_management():
    p = _packet()
    g = XPK.gate(p)
    assert g == {"complete": True, "missing": [], "refusal": None}
    for a in ("HOLD", "EXIT", "REDUCE", "NETTING", "ACQUIRE_INDIRECT_HEDGE"):
        assert XPK.permits(a, p)


@pytest.mark.parametrize("over,missing", [
    ({"residual": {"open_qty": 10, "ledger_open_qty": 9,
                   "reconciled": False}}, XPK.P_QTY),
    ({"residual": None}, XPK.P_QTY),
    ({"evidence_state": XF.E_STALE,
      "probability_source": "ENTRY_TIME_MEASURE"}, XPK.P_PROBABILITY),
    ({"evidence_state": XF.E_NONE}, XPK.P_PROBABILITY),
    ({"mark_class": "STALE"}, XPK.P_BOOK),
    ({"mark_class": "FEED_GAP"}, XPK.P_BOOK),
    ({"mark": {"exit_depth_at_mark": 0}}, XPK.P_DEPTH),
    ({"settlement": {"fingerprint": None}}, XPK.P_SETTLEMENT),
    ({"protection": None}, XPK.P_PROTECTION),
])
def test_each_missing_element_is_a_named_refusal(over, missing):
    p = _packet(**over)
    g = XPK.gate(p)
    assert g["complete"] is False
    assert missing in g["missing"]
    assert g["refusal"] == XPK.R_XAVIER_PACKET_INCOMPLETE
    for a in ("HOLD", "EXIT", "REDUCE", "NETTING", "ACQUIRE_INDIRECT_HEDGE"):
        assert not XPK.permits(a, p), a
    # a non-action (waiting for evidence) is not a management action
    assert XPK.permits(XF.REC_WAITING, p)


def test_an_entry_time_probability_can_never_carry_a_management_action():
    """Whatever else is current, Xavier cannot manage from the entry
    decision's probability: it is STALE_ENTRY_TIME_PROBABILITY by its own
    30 s rule, so the packet lacks NO_FRESH_PROBABILITY."""
    m = PX.probability_evidence(
        {"p": 0.62, "source": "ENTRY_TIME_MEASURE", "stale": True,
         "entry_pinnacle_at": NOW - 3605}, at=NOW, limit_s=30.0, qty=10)
    assert m["evidence_state"] == PX.E_STALE
    p = _packet(evidence_state=m["evidence_state"],
                probability_source="ENTRY_TIME_MEASURE")
    g = XPK.gate(p)
    assert g["missing"] == [XPK.P_PROBABILITY]
    assert not XPK.permits("HOLD", p)
    # even an entry-time probability that would be inside 30 s by its own
    # stamp is not FRESH when the measure says it is stale
    m = PX.probability_evidence(
        {"p": 0.62, "source": "ENTRY_TIME_MEASURE", "stale": True,
         "entry_pinnacle_at": NOW - 3}, at=NOW, limit_s=30.0, qty=10)
    assert m["evidence_state"] == PX.E_STALE
    assert not XPK.gate(_packet(evidence_state=m["evidence_state"]))[
        "complete"]


def test_an_absent_packet_is_missing_every_element():
    g = XPK.gate(None)
    assert g["missing"] == list(XPK.ELEMENTS)


def test_the_new_refusal_codes_are_in_the_taxonomy():
    from sportsassets import refusal_taxonomy_table as T
    for code in (PMF.R_STALE_MANAGEMENT_BLOCKS_ALLOCATION,
                 PMF.R_STALE_MANAGEMENT_UNREADABLE,
                 XPK.R_XAVIER_PACKET_INCOMPLETE):
        assert code in T.TABLE, code

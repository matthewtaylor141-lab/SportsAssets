"""A PINNACLE STAMP AFTER THE DECISION INSTANT IS NEVER FRESH.

`age = at - observed_at` with only an upper bound reads a future stamp (a
clock disagreement, or a provider/frame time confused with change time) as
the freshest possible quote. bettor_hold_value and the devig gate already
refused it; the Derek decision gate, the Xavier measure, the entry
freshness and the policy view did not. Production had zero such decisions
(min recorded age +2.009 s over 993, read 2026-10-01T23:56Z), so this
changes no recorded decision.
"""
from __future__ import annotations

import datetime as dt

from sportsassets import bettor_venue_currency as VC
from sportsassets.agents import derek_policy as DP
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.workers import ext_pinnacle_loop as loop

AT = 1_790_000_000.0


def test_derek_gate_refuses_a_future_stamp_and_keeps_the_past_rule():
    fut = PD._pinnacle({"pinnacle": {"p": 0.55, "observed_at": AT + 3.0}},
                       at=AT, max_age=30.0)
    assert fut["qualified"] is False
    assert fut["refusal"] == DP.R_FRESHNESS_UNKNOWN
    assert fut["qualification"] == "CLOCKS_DISAGREE"
    ok = PD._pinnacle({"pinnacle": {"p": 0.55, "observed_at": AT - 2.0}},
                      at=AT, max_age=30.0)
    assert ok["qualified"] is True and ok["qualification"] == "FRESH"
    old = PD._pinnacle({"pinnacle": {"p": 0.55, "observed_at": AT - 31.0}},
                       at=AT, max_age=30.0)
    assert old["qualified"] is False and old["qualification"] == "STALE"


def test_policy_view_never_labels_a_future_stamp_fresh():
    v = DP._freshness_view({"pinnacle_age_s": -1.0, "pinnacle_limit_s": 30.0},
                           basis="t")
    assert v["pinnacle_qualification"] == "UNKNOWN"
    v = DP._freshness_view({"pinnacle_age_s": 1.0, "pinnacle_limit_s": 30.0},
                           basis="t")
    assert v["pinnacle_qualification"] == "FRESH"


def test_entry_freshness_refuses_a_future_bookmaker_stamp():
    now = AT
    sub = {"alive_at": now - 0.5, "last_update_at": now - 1.0}
    vq = {"book_currency": VC.evaluate(now=now, subscription=sub,
                                       bound_s=30.0),
          "venue_ts": now - 2.0, "read_at": now,
          "venue_clock": {"our_response_received_at": now}}
    fr = loop._entry_freshness({"observed_at": now + 5.0}, vq,
                               now + 1.0)
    assert fr["fresh"] is False and "AFTER" in fr["why"]
    past = loop._entry_freshness({"observed_at": now - 2.0}, vq, now + 1.0)
    assert past.get("unknown_side") != "pinnacle_clock"


class _Conn:
    def __init__(self, observed_at):
        self.observed_at = observed_at

    async def fetchrow(self, sql, *a):
        if "FROM paper_decisions d JOIN paper_orders" in sql:
            return {"decision_id": "d1", "p_pinnacle": 0.5,
                    "decided_at": dt.datetime.fromtimestamp(
                        AT - 600, dt.timezone.utc), "valuation_id": 7}
        if "SELECT payout_event" in sql:
            return {"payout_event": "HOME_WIN", "payout_is_complement": False}
        if "SELECT id, probability, observed_at" in sql:
            return {"id": 8, "probability": 0.6,
                    "observed_at": dt.datetime.fromtimestamp(
                        self.observed_at, dt.timezone.utc)}
        return None


async def test_xavier_measure_marks_a_future_stamp_stale():
    """This is the measure Xavier's stale guard reads: a future stamp must
    arrive stale, so it cannot drive a discretionary sale."""
    ctx = {"now": AT, "config": {"entry": {"valuation_lookback_s": 3600.0,
                                           "pinnacle_max_age_s": 30.0}}}
    pos = {"group_id": "g1", "holding_side": "LONG",
           "us_market_slug": "s1"}
    fut = await PB.xavier_measure(_Conn(AT + 4.0), ctx, pos=pos)
    assert fut["stale"] is True and fut["source"] == "PINNACLE_ONLY_LATEST"
    cur = await PB.xavier_measure(_Conn(AT - 4.0), ctx, pos=pos)
    assert cur["stale"] is False and cur["source"] == "PINNACLE_ONLY_CURRENT"

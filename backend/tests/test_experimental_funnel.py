"""THE FUNNEL: why activity is or is not occurring.

Owner directive 2026-09-20, after the identity blocker cleared:
"instrument the funnel so management can see why activity is or is not
occurring." The buckets are the owner's own names.

WHAT THESE TESTS PROTECT. The funnel is DERIVED from the append-only
ledger rather than counted alongside it, so its only real failure mode
is a predicate that puts a row in the wrong bucket -- or in none. Two
things are pinned:

  * the NO_TRADE split is EXHAUSTIVE, and it is exhaustive for a
    reason that comes from the code: X1's frozen gate emits exactly
    two reasons and returns None otherwise, so `why LIKE 'SPREAD_%'`
    partitions NO_TRADE with nothing left over;
  * every BUY lands in a named bucket, checked by counting the
    remainder rather than by trusting the list.

NOTHING HERE TUNES X1. The frozen spread threshold, the lookback and
the thresholds are not read, written or referenced by the funnel.
"""

from __future__ import annotations

import pytest

from sportsassets import shadow_experiment_signals as sig
from sportsassets import shadow_experimental_engine as eng
from sportsassets import shadow_experimental_store as xstore

# The owner's list, verbatim and in order.
REQUIRED_BUCKETS = (
    "OPPORTUNITIES", "X1_ELIGIBLE", "NO_TRADE_SPREAD", "NO_TRADE_SIGNAL",
    "BUY_YES", "BUY_NO", "BUY_BLOCKED_IDENTITY", "BUY_BLOCKED_STALE_BOOK",
    "BUY_EXECUTED", "PARTIAL_FILLS", "FULL_FILLS",
)


class FunnelPool:
    """Answers the two funnel statements with counted fixture rows."""

    def __init__(self, decisions, observations=0, bindings=None):
        self.decisions = decisions
        self.observations = observations
        self.bindings = bindings or []

    async def fetchrow(self, sql, *args):
        if "bettor_identity_bindings" in sql:
            return self._identity()
        return self._decisions(args)

    async def fetch(self, sql, *args):
        """The per-experiment split (owner 2026-09-20): the same buckets
        under the portfolio that produced them, so the control's first
        position can never be read as the model's."""
        out = {}
        for r in self.decisions:
            e = r.get("experiment_id") or ""
            out.setdefault(e, []).append(r)
        rows = []
        for e, rs in sorted(out.items()):
            sub = FunnelPool(rs)._decisions(("", e))
            rows.append(dict(sub, experiment_id=e,
                             entry_notional=sum(
                                 r.get("executed_notional_usd") or 0
                                 for r in rs)))
        return rows

    def _decisions(self, args):
        experiment_id = args[1] if len(args) > 1 else ""
        d = self.decisions

        def n(pred):
            return sum(1 for r in d if pred(r))

        def spread(r):
            return (r.get("why") or "").startswith("SPREAD_")

        def executed(r):
            return (r.get("executed_notional_usd") or 0) > 0

        def unfilled(r):
            return (r.get("unfilled_notional_usd") or 0) > 0

        def is_buy(r):
            return str(r.get("action") or "").startswith("BUY")

        return {
            "opportunities": self.observations,
            "decisions": len(d),
            "x1_eligible": n(lambda r: r.get("experiment_id") == experiment_id),
            "no_trade_spread": n(lambda r: r.get("action") == "NO_TRADE"
                                 and spread(r)),
            "no_trade_signal": n(lambda r: r.get("action") == "NO_TRADE"
                                 and not spread(r)),
            "buy_yes": n(lambda r: r.get("action") == "BUY_YES"),
            "buy_no": n(lambda r: r.get("action") == "BUY_NO"),
            "buy_blocked_identity": n(
                lambda r: is_buy(r) and r.get("execution_status")
                == "BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE"),
            "buy_blocked_stale_book": n(
                lambda r: is_buy(r)
                and r.get("book_freshness_status") in ("STALE", "ABSENT")),
            "buy_executed": n(lambda r: is_buy(r) and executed(r)),
            "partial_fills": n(lambda r: executed(r) and unfilled(r)),
            "full_fills": n(lambda r: executed(r) and not unfilled(r)),
            "buy_unaccounted": n(
                lambda r: is_buy(r) and not executed(r)
                and r.get("execution_status")
                != "BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE"
                and r.get("book_freshness_status") not in ("STALE", "ABSENT")),
            "action_unaccounted": n(
                lambda r: r.get("action") not in ("NO_TRADE", "BUY_YES",
                                                  "BUY_NO")),
        }

    def _identity(self):
        b = self.bindings
        return {
            "markets": len({r["market_id"] for r in b}),
            "yes_eligible": sum(1 for r in b if r.get("execution_eligible")
                                and r.get("outcome_leg") in ("yes", "long")),
            "no_eligible": sum(1 for r in b if r.get("execution_eligible")
                               and r.get("outcome_leg") in ("no", "short")),
            "exact": sum(1 for r in b
                         if r.get("identity_status") == "EXACT_SAME_CONTRACT"),
            "complement_pending": sum(
                1 for r in b if str(r.get("identity_status") or "").startswith(
                    "STRUCTURALLY")),
            "ambiguous": sum(1 for r in b
                             if r.get("identity_status") == "AMBIGUOUS"),
            "unresolved": sum(1 for r in b
                              if r.get("identity_status") == "NOT_IDENTIFIED"),
            "prose_conflicts": sum(
                1 for r in b if r.get("settlement_prose_conflict")),
        }


X1 = "X1_SHORT_HORIZON_DIRECTION"


def decision(**kw):
    base = {"experiment_id": X1, "action": "NO_TRADE", "why": None,
            "execution_status": "NO_EXECUTION_INTENDED",
            "book_freshness_status": None,
            "executed_notional_usd": None, "unfilled_notional_usd": None}
    base.update(kw)
    return base


# ── the buckets the directive named ──────────────────────────────────


@pytest.mark.asyncio
async def test_every_bucket_the_directive_named_is_present():
    out = await xstore.funnel(FunnelPool([]))
    for bucket in REQUIRED_BUCKETS:
        assert bucket in out, bucket


@pytest.mark.asyncio
async def test_the_no_trade_split_puts_the_spread_gate_on_its_own_line():
    """The production case: an EXACT_SAME_CONTRACT market that X1
    declined because the book was too wide. Management must see that as
    a SPREAD refusal, not as "the model found nothing"."""
    rows = [
        decision(why="SPREAD_ABOVE_FROZEN_MAX"),
        decision(why="SPREAD_NOT_IDENTIFIED"),
        decision(why="the drift is inside the frozen band"),
        decision(why=None),
    ]
    out = await xstore.funnel(FunnelPool(rows))
    assert out["NO_TRADE_SPREAD"] == 2
    assert out["NO_TRADE_SIGNAL"] == 2
    assert out["NO_TRADE_SPREAD"] + out["NO_TRADE_SIGNAL"] == 4


def test_the_spread_gate_emits_exactly_the_two_reasons_the_split_assumes():
    """THE PARTITION'S JUSTIFICATION, checked against the gate itself
    rather than assumed. If a third SPREAD reason is ever added, this
    fails here instead of silently landing in NO_TRADE_SIGNAL."""
    wide = {"bid": 0.30, "ask": 0.70, "mid": 0.50}
    assert sig.m1_gate(wide) == "SPREAD_ABOVE_FROZEN_MAX"
    assert sig.m1_gate({}) == "SPREAD_NOT_IDENTIFIED"
    # A readable, tight book passes the gate entirely.
    assert sig.m1_gate({"bid": 0.49, "ask": 0.51, "mid": 0.50}) is None

    import inspect
    src = inspect.getsource(sig.m1_gate)
    reasons = {line.split('"')[1] for line in src.splitlines()
               if 'return "' in line}
    assert reasons == {"SPREAD_ABOVE_FROZEN_MAX", "SPREAD_NOT_IDENTIFIED"}


@pytest.mark.asyncio
async def test_a_buy_blocked_by_identity_is_counted_as_blocked_not_as_absent():
    """The 14 historical signals. They are BUY signals that could not
    execute -- a different fact from the model not firing, and the
    screen has to say which."""
    rows = [decision(action="BUY_YES", why=None,
                     execution_status="BLOCKED_IDENTITY_NOT_EXECUTION_"
                                      "ELIGIBLE")]
    out = await xstore.funnel(FunnelPool(rows))
    assert out["BUY_YES"] == 1
    assert out["BUY_BLOCKED_IDENTITY"] == 1
    assert out["BUY_EXECUTED"] == 0
    assert out["NO_TRADE_SPREAD"] == out["NO_TRADE_SIGNAL"] == 0


@pytest.mark.asyncio
async def test_a_buy_blocked_by_a_stale_book_is_its_own_bucket():
    rows = [decision(action="BUY_YES", execution_status="NOT_IDENTIFIED",
                     book_freshness_status="STALE"),
            decision(action="BUY_YES", execution_status="NOT_IDENTIFIED",
                     book_freshness_status="ABSENT")]
    out = await xstore.funnel(FunnelPool(rows))
    assert out["BUY_BLOCKED_STALE_BOOK"] == 2
    assert out["BUY_BLOCKED_IDENTITY"] == 0
    assert out["unaccounted"]["BUY_ROWS_IN_NO_NAMED_BUCKET"] == 0


@pytest.mark.asyncio
async def test_partial_and_full_fills_are_counted_apart():
    """§12: a partial is a legitimate trade, and it is not a full one."""
    rows = [
        decision(action="BUY_YES", execution_status="PARTIAL",
                 book_freshness_status="CURRENT",
                 executed_notional_usd=271.50, unfilled_notional_usd=728.50),
        decision(action="BUY_YES", execution_status="EXECUTED",
                 book_freshness_status="CURRENT",
                 executed_notional_usd=1000.0, unfilled_notional_usd=0.0),
    ]
    out = await xstore.funnel(FunnelPool(rows))
    assert out["BUY_EXECUTED"] == 2
    assert out["PARTIAL_FILLS"] == 1
    assert out["FULL_FILLS"] == 1


# ── the partition is checked, not trusted ────────────────────────────


@pytest.mark.asyncio
async def test_a_buy_in_no_named_bucket_is_reported_rather_than_lost():
    """A BUY that is neither executed nor blocked by identity nor by a
    stale book belongs to a bucket nobody named. The funnel says so
    instead of quietly dropping it, because a screen that always adds
    up is a screen that cannot tell you it is wrong."""
    rows = [decision(action="BUY_YES", execution_status="SOMETHING_NEW",
                     book_freshness_status="CURRENT")]
    out = await xstore.funnel(FunnelPool(rows))
    assert out["unaccounted"]["BUY_ROWS_IN_NO_NAMED_BUCKET"] == 1


@pytest.mark.asyncio
async def test_an_action_outside_the_three_is_reported_too():
    rows = [decision(action="SELL_YES")]
    out = await xstore.funnel(FunnelPool(rows))
    assert out["unaccounted"]["ACTIONS_OUTSIDE_THE_THREE"] == 1


def test_the_engine_produces_exactly_three_actions():
    """The funnel's action buckets are exhaustive because the engine's
    actions are. Pinned so a fourth action shows up here first."""
    assert {eng.BUY_YES, eng.BUY_NO, eng.NO_TRADE} == {
        "BUY_YES", "BUY_NO", "NO_TRADE"}


# ── the identity side, which explains a quiet funnel ─────────────────


@pytest.mark.asyncio
async def test_the_identity_census_rides_along_so_a_quiet_day_is_explainable():
    """Zero trades with zero eligible markets and zero trades with eight
    eligible markets are completely different situations, and the panel
    must not render them the same way."""
    bindings = [
        {"market_id": "m1", "outcome_leg": "yes", "execution_eligible": True,
         "identity_status": "EXACT_SAME_CONTRACT",
         "settlement_prose_conflict": "two sentences disagree"},
        {"market_id": "m1", "outcome_leg": "no", "execution_eligible": False,
         "identity_status": "STRUCTURALLY_IDENTIFIED_COMPLEMENT_PENDING_"
                            "INSTITUTIONAL_CONFIRMATION",
         "settlement_prose_conflict": None},
        {"market_id": "m2", "outcome_leg": "yes", "execution_eligible": False,
         "identity_status": "AMBIGUOUS", "settlement_prose_conflict": None},
    ]
    out = await xstore.funnel(FunnelPool([], bindings=bindings))
    ident = out["identity"]
    assert ident["MARKETS_BOUND"] == 2
    assert ident["YES_EXECUTION_ELIGIBLE"] == 1
    assert ident["EXACT_SAME_CONTRACT"] == 1
    assert ident["COMPLEMENT_PENDING"] == 1
    assert ident["AMBIGUOUS"] == 1
    assert ident["SETTLEMENT_PROSE_CONFLICTS"] == 1


@pytest.mark.asyncio
async def test_opportunities_counts_what_the_lane_sampled():
    out = await xstore.funnel(FunnelPool([], observations=417))
    assert out["OPPORTUNITIES"] == 417


# ── the funnel changes nothing ───────────────────────────────────────


def test_the_funnel_writes_nothing_and_tunes_nothing():
    """It is a read. A funnel that could write would be a second
    account of the same events, and the two would disagree the first
    time a tick died between the increment and the insert."""
    import inspect
    src = inspect.getsource(xstore.funnel) + xstore.FUNNEL_SQL \
        + xstore.FUNNEL_IDENTITY_SQL
    lowered = src.lower()
    for word in ("insert", "update ", "delete", "drop", "alter"):
        assert word not in lowered, word
    # And it names no threshold: tuning lives in the frozen registry.
    for word in ("threshold", "max_spread", "lookback", "m1_max"):
        assert word not in lowered, word


@pytest.mark.asyncio
async def test_the_panel_survives_a_funnel_that_cannot_be_computed():
    """A panel that loses one section still shows the rest, and an
    unavailable funnel is named rather than rendered as zeros -- a
    funnel of zeros and a broken funnel mean different things."""
    from sportsassets.api import command_experimental as ce

    class Broken:
        async def fetchrow(self, sql, *args):
            raise RuntimeError("replica unavailable")

        async def fetch(self, sql, *args):
            return []

    out = await ce.summary(Broken())
    assert "unavailable" in out["funnel"]
    assert "replica unavailable" in out["funnel"]["unavailable"]

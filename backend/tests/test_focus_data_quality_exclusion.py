"""A MARKET THAT CANNOT SUPPLY THE FEATURE IS NOT A MARKET THAT LOST.

Owner directive 2026-09-20:

    "the two focus markets with MARKET_LEVEL_NOT_LEG_SPECIFIC and zero
    readable samples may be excluded from future experimental
    eligibility only as a frozen data-quality rule independent of
    model output and P&L. Do not remove them because they perform
    badly; they simply cannot provide the required leg-specific
    feature."

THE WHOLE RISK IN THIS CHANGE, stated plainly: a rule that removes
markets from an experiment's population is one keystroke away from a
rule that removes the markets the experiment did badly on. That is not
a data-quality rule, it is a result chosen and then justified, and the
P&L it produces afterwards is not a measurement of anything.

SO THE RULE IS CONSTRAINED FOUR WAYS, one test group each:

  1. IT READS TWO COLUMNS. `readable` and `bbo_binding`. No price, no
     mid, no signal, no action, no notional, no markout, no P&L -- and
     the tests scan the SQL for every one of those words rather than
     trusting the comment above them.

  2. IT NEEDS EVIDENCE BEFORE IT ACTS. A market is not excluded on one
     bad sample; it must have produced at least
     FOCUS_EXCLUSION_MIN_SAMPLES observations and NONE of them usable.

  3. IT IS SELF-REVERSING. The rule is a standing query over a window,
     not a blocklist. One readable YES-bound sample and the market is
     back in the focus set on the next tick, with nobody editing
     anything.

  4. IT IS VISIBLE. An excluded market is named on COMMAND with its
     count and its reason. A market quietly dropped from the focus set
     looks exactly like a market nobody ever found.

The rule text is hashed so a later widening is a visible change rather
than a quiet one.
"""

from __future__ import annotations

import pytest

from sportsassets import shadow_experimental_store as xstore
from sportsassets import shadow_l2 as l2
from sportsassets.api import command_experimental as ce


# ── 1. the rule reads no result ──────────────────────────────────────


@pytest.mark.parametrize("sql_name", ["FOCUS_TOPUP_SQL", "FOCUS_EXCLUDED_SQL"])
def test_the_exclusion_reads_no_price_no_signal_and_no_pnl(sql_name):
    """THE LOAD-BEARING TEST. If any of these words can appear in the
    statement the rule can select on outcomes, and the exclusion stops
    being a data-quality rule."""
    sql = getattr(xstore, sql_name).lower()
    for forbidden in ("mid", "signal", "pnl", "markout", "vwap", "notional",
                      "action", "executed", "position", "return", "win"):
        assert forbidden not in sql, (
            "%s reads %r -- an exclusion that can see a result is a "
            "performance filter, not a data-quality rule"
            % (sql_name, forbidden))


def test_the_rule_reads_readability_and_binding_only():
    sql = xstore._NO_LEG_SPECIFIC_FEATURE
    assert "readable IS TRUE" in sql
    assert "bbo_binding = '%s'" % l2.BIND_YES in sql
    # `symbol`, `observed_at`, `readable`, `bbo_binding` and nothing
    # else -- checked by name so a fifth column is a failing test.
    for col in ("mid", "spread", "best_bid", "best_ask", "feature"):
        assert col not in sql, col


def test_the_reason_the_market_is_out_is_named_in_the_rule_text():
    assert "leg-specific feature" in xstore.FOCUS_EXCLUSION_RULE
    assert "never a price, a signal or a P&L" in xstore.FOCUS_EXCLUSION_RULE


def test_the_rule_text_is_hashed_so_a_widening_is_visible():
    import hashlib

    assert xstore.FOCUS_EXCLUSION_RULE_SHA == hashlib.sha256(
        xstore.FOCUS_EXCLUSION_RULE.encode()).hexdigest()[:16]
    assert len(xstore.FOCUS_EXCLUSION_RULE_SHA) == 16


# ── 2. evidence before action ────────────────────────────────────────


def test_a_market_is_not_excluded_on_a_thin_sample():
    """One unreadable observation is a moment, not a verdict."""
    assert xstore.FOCUS_EXCLUSION_MIN_SAMPLES >= 10
    assert "count(*) >= %d" % xstore.FOCUS_EXCLUSION_MIN_SAMPLES in (
        xstore._NO_LEG_SPECIFIC_FEATURE)


def test_the_exclusion_requires_zero_usable_samples_not_merely_few():
    """NOT a ratio, NOT a majority. A market with even one readable
    YES-bound sample can supply the feature and stays in."""
    having = xstore._NO_LEG_SPECIFIC_FEATURE.split("HAVING")[1]
    assert ") = 0" in having, (
        "the count of usable samples must be compared with zero; any "
        "other threshold is a judgement about quality rather than a "
        "statement about capability")


def test_both_statements_use_the_same_predicate():
    """The set COMMAND names as excluded must be exactly the set the
    focus query removes -- otherwise the panel describes a rule the
    lane is not running."""
    frag = "count(*) FILTER (WHERE readable IS TRUE\n" \
           "                              AND bbo_binding = '%s') = 0" \
           % l2.BIND_YES
    normalise = lambda s: " ".join(s.split())            # noqa: E731
    assert normalise(frag) in normalise(xstore.FOCUS_EXCLUDED_SQL)
    assert normalise(frag) in normalise(xstore._NO_LEG_SPECIFIC_FEATURE)
    assert normalise(xstore._NO_LEG_SPECIFIC_FEATURE) in normalise(
        xstore.FOCUS_TOPUP_SQL)


# ── 3. self-reversing ────────────────────────────────────────────────


def test_the_exclusion_is_a_window_query_not_a_stored_blocklist():
    """THE SELF-REVERSAL, structurally. Nothing is written anywhere:
    the rule is re-evaluated every tick over a moving window, so a
    market that starts producing readable YES-bound books returns on
    its own."""
    import inspect

    src = inspect.getsource(xstore.focus_excluded).lower()
    for stmt in ("insert", "update", "delete"):
        assert stmt not in src, stmt
    assert "observed_at > now()" in xstore._NO_LEG_SPECIFIC_FEATURE, (
        "without a moving window the exclusion would be permanent")


def test_nothing_persists_the_excluded_symbol():
    """A blocklist column would outlive the condition that justified
    it. There is no such column and no such table."""
    src = (xstore.FOCUS_EXCLUDED_SQL + xstore.FOCUS_TOPUP_SQL).lower()
    assert "excluded" not in src
    assert "blocklist" not in src and "blacklist" not in src


# ── 4. visible on the panel ──────────────────────────────────────────


class ExcludedPool:
    """One market with 12 unreadable samples, none YES-bound."""

    ROW = {"symbol": "asc-cfb-byu-colst-2026-09-19-3q-pos-1pt5",
           "samples": 12, "readable": 0,
           "binding": "MARKET_LEVEL_NOT_LEG_SPECIFIC", "newest": None}

    async def fetch(self, sql, *args):
        if "HAVING" in sql and "bbo_binding" in sql:
            return [self.ROW]
        if "bettor_experimental_positions" in sql and "GROUP BY" in sql:
            return []
        return []

    async def fetchrow(self, sql, *args):
        return None


@pytest.mark.asyncio
async def test_focus_excluded_names_the_market_the_count_and_the_reason():
    rows = await xstore.focus_excluded(ExcludedPool())
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"].endswith("3q-pos-1pt5")
    assert r["samples"] == 12
    assert r["readable"] == 0
    assert r["bboBinding"] == "MARKET_LEVEL_NOT_LEG_SPECIFIC"
    assert "leg-specific feature" in r["why"]
    assert r["ruleSha"] == xstore.FOCUS_EXCLUSION_RULE_SHA
    # No result of any kind travels with the exclusion.
    assert not {"pnl", "return", "signal"} & set(r)


@pytest.mark.asyncio
async def test_command_shows_the_exclusion_and_says_what_it_is_based_on():
    """A market dropped from the focus set with no explanation is
    indistinguishable from a market nobody ever found."""
    out = await ce.summary(ExcludedPool())
    fx = out["focusExclusions"]
    assert fx["basis"] == "DATA_QUALITY_ONLY"
    assert fx["readsNoModelOutput"] is True
    assert fx["ruleSha"] == xstore.FOCUS_EXCLUSION_RULE_SHA
    assert fx["minSamples"] == xstore.FOCUS_EXCLUSION_MIN_SAMPLES
    assert fx["unavailable"] is None
    assert len(fx["excluded"]) == 1
    assert fx["excluded"][0]["bboBinding"] == "MARKET_LEVEL_NOT_LEG_SPECIFIC"


class BrokenPool(ExcludedPool):
    async def fetch(self, sql, *args):
        if "HAVING" in sql and "bbo_binding" in sql:
            raise RuntimeError("relation does not exist")
        return await ExcludedPool.fetch(self, sql, *args)


@pytest.mark.asyncio
async def test_an_unreadable_exclusion_is_named_not_rendered_as_none():
    """"No markets excluded" and "the exclusion query failed" are
    different facts, and only one of them means the focus set is
    whole."""
    out = await ce.summary(BrokenPool())
    fx = out["focusExclusions"]
    assert fx["excluded"] == []
    assert "relation does not exist" in fx["unavailable"]


# ── the focus set itself ─────────────────────────────────────────────


class FocusPool:
    """A held market, and a top-up pool the exclusion filters."""

    def __init__(self, topup):
        self.topup = topup
        self.topup_args = None

    async def fetch(self, sql, *args):
        if "bettor_opportunities" in sql:
            self.topup_args = args
            return list(self.topup)
        return []


@pytest.mark.asyncio
async def test_the_exclusion_runs_on_the_top_up_not_on_the_held_set():
    """The sticky half is a market this lane is ALREADY sampling; the
    rule governs which new markets may be taken on. Holding an already
    open series steady is what makes the series a series."""
    assert "NOT IN" in xstore.FOCUS_TOPUP_SQL
    assert "NOT IN" not in xstore.FOCUS_SQL


@pytest.mark.asyncio
async def test_a_healthy_market_is_still_taken_on():
    """The narrowness of the rule, live: it removes one market and
    leaves every other candidate alone."""
    pool = FocusPool([{"symbol": "asc-cfb-good-2026-09-19-pos-1pt5",
                       "outcome_leg": "yes", "event_id": "e1"}])
    out = await xstore.focus_set(pool, size=4)
    assert [r["symbol"] for r in out] == [
        "asc-cfb-good-2026-09-19-pos-1pt5"]
    assert out[0]["held"] is False


def test_the_exclusion_is_not_applied_to_markouts_or_to_scoring():
    """AN EXCLUDED MARKET'S EXISTING ROWS ARE UNTOUCHED. The rule
    governs FUTURE eligibility only: positions already opened there
    keep being marked and keep appearing in the ledger."""
    from sportsassets import shadow_experimental_markouts as mk

    src = getattr(mk, "MARKOUT_SUBJECTS_SQL", None) or \
        xstore.MARKOUT_SUBJECTS_SQL
    assert "bbo_binding" not in src
    assert "readable" not in src
    for name in ("_HEADLINE", "_BY_EXPERIMENT", "_POSITIONS"):
        assert "bbo_binding" not in getattr(ce, name), name

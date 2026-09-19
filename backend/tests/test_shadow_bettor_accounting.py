"""THE MANAGEMENT ACCOUNTING, AND THE RULES IT MUST NOT BREAK.

Owner directive 2026-09-19: "Management does not only want P&L. COMMAND
must continuously answer: HOW MUCH CAPITAL HAVE WE PLAYED THROUGH? HOW
MUCH CAPITAL DID WE ACTUALLY NEED? HOW MANY TIMES DID WE RECYCLE IT? HOW
MUCH DID WE MAKE? WHAT RETURN DID THAT CAPITAL PRODUCE?"

The dangerous failure on this screen is not a crash. It is a
well-formed page of plausible numbers that overstates what happened --
0.00% where nothing was deployed, $1,000 played where $650 was
supported, a peak of $1,650 where the same $1,000 was recycled twice.
Each of those has a test here.

THE ARITHMETIC ITSELF was proved against a real PostgreSQL with the
directive's own worked example before any of this was written; these
pin the invariants that arithmetic has to keep honoring.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from sportsassets import shadow as sh
from sportsassets import shadow_bettor_accounting as acct
from sportsassets import shadow_bettor_policy as bpol
from sportsassets import shadow_bettor_sizing as szpol
from sportsassets import shadow_lanes as lanes
from sportsassets import shadow_store as store
from sportsassets.api import command_shadow as CS

ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ACCT_SRC = (BACKEND / "sportsassets"
            / "shadow_bettor_accounting.py").read_text()
SIZING_SRC = (BACKEND / "sportsassets"
              / "shadow_bettor_sizing.py").read_text()
M074 = (BACKEND / "migrations"
        / "074_bettor_shadow_accounting.sql").read_text()
UI = (ROOT / "frontend" / "public" / "command" / "shadow.js").read_text()


# ── the $1,000 standard ──────────────────────────────────────────────


def test_the_standard_notional_is_one_thousand_dollars():
    assert szpol.STANDARD_BETTOR_SHADOW_NOTIONAL_USD == 1000
    assert szpol.COHORT == "BETTOR_$1000_STANDARD"
    assert szpol.DECLARATION["standardNotionalUsd"] == 1000


def test_intended_is_not_executed():
    """The directive's own worked example: 1000 intended, 650
    supported, 350 unfilled, and only the 650 is economics."""
    out = szpol.size(650)
    assert out["intendedNotionalUsd"] == 1000
    assert out["executedEntryNotionalUsd"] == 650
    assert out["unfilledNotionalUsd"] == 350
    assert out["status"] == "LIQUIDITY_LIMITED"


def test_liquidity_is_never_invented_to_reach_the_standard():
    """"Never invent liquidity to reach $1,000.\""""
    for executable in (0, 1, 649.99, 650, 999.99):
        out = szpol.size(executable)
        assert out["executedEntryNotionalUsd"] == executable
        assert out["executedEntryNotionalUsd"] <= 1000
    # And a book DEEPER than the standard does not raise the size.
    assert szpol.size(50_000)["executedEntryNotionalUsd"] == 1000


def test_an_unread_book_is_not_zero_and_not_one_thousand():
    """An entry may not be sized against a book nobody read. Neither
    substitution is safe: zero understates and 1000 invents."""
    out = szpol.size(None)
    assert out["status"] == "EXECUTABLE_LIQUIDITY_NOT_IDENTIFIED"
    assert out["executedEntryNotionalUsd"] is None
    assert out["unfilledNotionalUsd"] is None


def test_the_sizing_policy_does_not_confer_eligibility():
    """"The $1,000 assumption determines sizing. It does NOT determine
    whether BETTOR trades.\""""
    assert szpol.DECLARATION["confersEligibility"] is False
    assert szpol.DECLARATION["eligibilityRemainsWith"] == \
        bpol.BETTOR_POLICY_VERSION
    # The EV policy still emits exactly one action, so nothing can be
    # sized today at all.
    assert bpol.ACTION_SET == [sh.NO_TRADE]


def test_the_sizing_freeze_did_not_disturb_the_ev_policy():
    """Editing BETTOR_EV_SHADOW_V1's declaration would move POLICY_SHA
    and the decision table's foreign key would then refuse every
    further decision -- the exact failure that cost 99 of them. So the
    sizing rule is declared BESIDE the EV policy, never inside it."""
    assert bpol.policy_sha() == bpol.POLICY_SHA
    assert bpol.DECLARATION["sizing"] == "NOT_APPLICABLE"
    assert bpol.DECLARATION["sizingPolicyVersion"] == "NOT_APPLICABLE"
    # Two versions, two hashes, two tables.
    assert szpol.SIZING_POLICY_VERSION != bpol.BETTOR_POLICY_VERSION
    assert szpol.POLICY_SHA != bpol.POLICY_SHA
    assert "bettor_sizing_policies" in M074


def test_the_sizing_declaration_is_hashed_and_frozen_append_only():
    frozen = szpol.frozen_policy()
    assert frozen["policySha"] == szpol.POLICY_SHA
    assert len(szpol.POLICY_SHA) == 64
    assert "bettor_sizing_policies_immutable" in M074
    assert "shadow_append_only()" in M074
    # A changed rule under the same version yields a different hash,
    # which is what makes REFUSED possible at all.
    changed = dict(szpol.DECLARATION, standardNotionalUsd=2000)
    assert szpol.policy_sha(changed) != szpol.POLICY_SHA


def test_a_refused_sizing_freeze_does_not_stop_collection():
    """"Do not delay prospective BETTOR collection while adding this
    reporting." Sizing is consulted only for an eligible entry, and
    none can exist while the action set is [NO_TRADE], so a refusal is
    a named problem rather than a halt."""
    worker = (BACKEND / "sportsassets" / "workers"
              / "shadow_bettor.py").read_text()
    block = worker[worker.index("sized = await store.freeze_sizing_policy"):]
    block = block[:block.index("boot = {")]
    assert "REFUSED" in block
    # The EV freeze clears storeReady; the sizing freeze must NOT.
    assert "storeReady=False" not in block


# ── zero is a claim ──────────────────────────────────────────────────


def test_a_zero_denominator_is_not_a_zero_per_cent():
    """"Where denominator is zero: NOT_APPLICABLE, not 0%.\""""
    for bad in (0, 0.0, None):
        assert acct.ratio(123.0, bad) == "NOT_APPLICABLE"
    assert acct.ratio(None, 100) == "NOT_APPLICABLE"
    # And a real ratio is still a real ratio.
    assert acct.ratio(1650, 1000) == 1.65


def test_a_missing_figure_is_not_zero_dollars():
    assert acct.usd(None) == "NOT_IDENTIFIED"
    assert acct.usd(None, missing="NOT_APPLICABLE") == "NOT_APPLICABLE"
    assert acct.usd(12.5) == 12.5
    # Nothing played really is nothing played, and only there.
    assert acct._z(None) == 0.0


def test_the_ui_prints_the_servers_word_rather_than_coercing_it():
    """A formatter that ran String(v) through a number path would turn
    NOT_APPLICABLE into NaN or 0 on the screen, which is exactly the
    0.00% the directive forbids."""
    for fn in ("money", "rate", "turns", "hold"):
        assert "const %s = " % fn in UI or "const %s =" % fn in UI
    # Each tests isNum FIRST and falls through to the word.
    assert "const rate = v => isNum(v) ? (v * 100).toFixed(2) + '%' : word(v)" \
        in UI
    assert "const word = v => esc(String(v || '').replace(/_/g, ' '))" in UI


# ── the three capital numbers stay three numbers ─────────────────────


def test_entry_notional_and_turnover_are_not_the_same_sum():
    """"How many dollars have we put through the strategy on entries?"
    and "How much total trading activity did the strategy generate?"
    are different questions with different answers."""
    assert szpol.ENTRY_KINDS == (szpol.ENTRY,)
    assert set(szpol.TURNOVER_KINDS) == set(szpol.LEG_KINDS)
    assert len(szpol.TURNOVER_KINDS) > len(szpol.ENTRY_KINDS)
    for kind in ("EXIT", "CASHOUT", "PAIR", "COMPLEMENT"):
        assert kind in szpol.TURNOVER_KINDS
        assert kind not in szpol.ENTRY_KINDS
    # The SQL tells them apart by the leg kind, not by a heuristic.
    assert "execution_leg_kind = 'ENTRY'" in ACCT_SRC


def test_every_leg_kind_the_code_uses_is_legal_in_the_database():
    declared = set(re.findall(r"'([A-Z_]+)'",
                              M074[M074.index("shadow_exec_leg_kind"):
                                   M074.index("-- INTENDED = EXECUTED")]))
    for kind in szpol.LEG_KINDS:
        assert kind in declared, kind


def test_capital_deployed_is_derived_not_sampled():
    """A sampler's peak depends on the sampling rate and misses a
    position that opened and closed between two samples. The timeline
    is read from the position events themselves."""
    assert "CREATE OR REPLACE VIEW bettor_capital_timeline" in M074
    assert "bettor_capital_timeline" in ACCT_SRC
    assert "capital_samples" not in M074
    # Peak is the max of a running sum over the event stream.
    assert "max(deployed)" in ACCT_SRC


def test_capital_turns_is_never_called_leverage():
    """"Do not call either one 'leverage.'\""""
    for src in (ACCT_SRC, SIZING_SRC, M074):
        assert not re.search(r"\bleverage\b", src, re.I) or \
            "NOT_APPLICABLE" in src
    # The UI says so in words where a reader might assume otherwise.
    assert "recycling, not leverage" in UI


def test_the_database_refuses_invented_liquidity_and_bad_arithmetic():
    """The writer cannot record executed > intended, and cannot record
    a trio that does not add up."""
    assert "shadow_exec_no_invented_liquidity" in M074
    assert "shadow_exec_notional_balances" in M074
    assert "shadow_exec_notional_non_negative" in M074


# ── what is not counted ──────────────────────────────────────────────


def test_unidentified_passive_fills_and_markout_are_not_economics():
    """"Do not count unidentified passive fills. Do not count
    counterfactual markout as realized P&L.\""""
    assert sh.PASSIVE_QUEUE_MODEL_ESTIMATE in acct.EXCLUDED_CLASSES
    assert sh.PASSIVE_COUNTERFACTUAL_MARKOUT in acct.EXCLUDED_CLASSES
    assert sh.PASSIVE_QUEUE_MODEL_ESTIMATE not in acct.ECONOMIC_CLASSES
    assert sh.PASSIVE_COUNTERFACTUAL_MARKOUT not in acct.ECONOMIC_CLASSES
    # The two sets partition the vocabulary -- no class is unaccounted
    # for, which is how one would silently drift into the totals.
    assert set(acct.ECONOMIC_CLASSES) | set(acct.EXCLUDED_CLASSES) == \
        set(sh.EXECUTION_CLASSES)


def test_the_excluded_rows_are_counted_rather_than_dropped():
    """Silently omitting them would read as "there were none"."""
    assert "excludedFromEconomics" in ACCT_SRC
    assert "_EXCLUDED_SQL" in ACCT_SRC


def test_shadow_economic_is_not_the_same_claim_as_realizable_in_cash():
    """sh.REALIZABLE_CLASSES answers "would this have been real money".
    Conflating the two is how a shadow return starts reading as an
    investment return."""
    assert sh.REALIZABLE_CLASSES == frozenset({sh.ACTUAL_FILL})
    assert acct.ECONOMIC_CLASSES != tuple(sh.REALIZABLE_CLASSES)
    assert sh.MARKETABLE_RECONSTRUCTED in acct.ECONOMIC_CLASSES


# ── one lane ─────────────────────────────────────────────────────────


def test_rn1_economics_are_never_combined_with_bettor_economics():
    """"Use the independent BETTOR_EV_SHADOW lane. Do not combine RN1
    economics with BETTOR economics.\""""
    assert acct.LANE == lanes.BETTOR_EV_SHADOW
    # Every SQL template carries the lane filter.
    for name in ("_EXECUTIONS_SQL", "_PNL_SQL", "_UNREALIZED_SQL",
                 "_POSITIONS_SQL", "_DECISIONS_SQL", "_EQUITY_SQL"):
        body = ACCT_SRC[ACCT_SRC.index(name + " = "):]
        body = body[:body.index('"""', body.index('"""') + 3)]
        assert "lane = '%(lane)s'" in body, name
    # And the one view it leans on filters the lane in the database.
    view = M074[M074.index("CREATE OR REPLACE VIEW bettor_capital_timeline"):]
    assert "d.lane = 'BETTOR_EV_SHADOW'" in view
    # No caller can ask for RN1 or for both.
    assert "RN1_SHADOW" not in ACCT_SRC


def test_the_accounting_never_writes():
    """Accounting that could write would eventually write, and the
    ledger's whole value is that the claim recorded at T0 is the claim
    read back later."""
    for verb in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE"):
        assert not re.search(r"\b%s\b\s+(INTO|TABLE|FROM|\w)" % verb,
                             ACCT_SRC), verb
    assert "pool.execute" not in ACCT_SRC


# ── periods ──────────────────────────────────────────────────────────


def test_every_period_the_directive_names_exists():
    assert set(acct.PERIODS) == {"TODAY", "7D", "30D", "ALL"}


def test_periods_are_cut_on_event_timestamps_in_the_database():
    """"Use event timestamps, not browser-local grouping." Two readers
    in two time zones must see the same number."""
    assert "AT TIME ZONE 'UTC'" in acct._PERIOD_SQL["TODAY"]
    assert acct._PERIOD_SQL["ALL"] == "NULL::timestamptz"
    # Nothing in the front end computes a boundary.
    assert "accounting/all" in UI
    assert "getTimezoneOffset" not in UI


def test_an_unknown_period_is_refused_rather_than_defaulted():
    with pytest.raises(ValueError):
        acct.period_bound_sql("LAST_TUESDAY")


# ── the screen stays visibly shadow ──────────────────────────────────


def test_every_accounting_payload_carries_the_disclosure():
    assert '"disclosure"' in ACCT_SRC
    assert '"realOrderActivity": "NONE"' in ACCT_SRC
    assert "SIMULATED / COUNTERFACTUAL EXECUTION" in ACCT_SRC
    assert "sh.CAPITAL_AT_RISK" in ACCT_SRC


def test_the_panel_draws_the_disclosure_above_the_numbers():
    """"Every performance panel must remain visibly: BETTOR EV SHADOW /
    NO REAL CAPITAL / SIMULATED / COUNTERFACTUAL EXECUTION." A
    screenshot of this tab is the artifact most likely to be mistaken
    for actual investment performance."""
    tab = UI[UI.index("function accountingTab()"):]
    tab = tab[:tab.index("\n  /* ── shell")]
    assert "disclosure(all.environment)" in tab
    assert tab.index("sh-acct-banner") < tab.index("acctTopline")
    assert "NO REAL CAPITAL" in tab


def test_the_top_line_shows_every_figure_the_directive_listed():
    for label in ("NET SHADOW P&L", "TODAY'S P&L", "ENTRY NOTIONAL PLAYED",
                  "GROSS TRADING TURNOVER", "CURRENT CAPITAL DEPLOYED",
                  "PEAK CAPITAL DEPLOYED", "CAPITAL TURNS",
                  "RETURN ON ENTRY NOTIONAL", "RETURN ON PEAK CAPITAL",
                  "OPEN POSITIONS", "CLOSED POSITIONS", "WIN RATE",
                  "MAX DRAWDOWN", "AVG HOLD TIME"):
        assert label in UI, label


def test_the_waterfall_is_shown_component_by_component():
    """One blended P&L number would hide which part is settled, which
    is a mark, and which is a cost."""
    for label in ("Settled", "Realized exit", "Pairing", "Cash-out",
                  "Unrealized executable", "Fees", "Spread cost",
                  "Slippage", "Adverse selection"):
        assert "['%s'," % label in UI, label


def test_the_returns_are_not_presented_as_interchangeable():
    """"Do not present these as interchangeable." Five denominators,
    five rows, each named."""
    for key in ("returnOnEntryNotional", "returnOnPeakCapital",
                "returnOnAverageCapital", "settledReturnOnEntryNotional",
                "realizedReturnOnEntryNotional"):
        assert key in ACCT_SRC and key in UI, key


def test_no_starting_bankroll_is_invented():
    """"Do not choose starting capital retrospectively to flatter
    returns. If management has not yet selected a hypothetical starting
    bankroll, report capital-required metrics without inventing one.\""""
    assert "starting_shadow_capital=None" in ACCT_SRC
    assert acct.NOT_SELECTED == "NOT_SELECTED"
    # The percentage needs a bankroll and stays unanswerable without one.
    block = ACCT_SRC[ACCT_SRC.index('"maxDrawdownPct"'):]
    assert "NOT_APPLICABLE if starting_shadow_capital is None" in block


# ── nothing here makes BETTOR trade ──────────────────────────────────


def test_the_accounting_has_no_decision_path():
    """"Do not invent trades to create P&L. This accounting starts when
    the EV Engine actually generates an eligible prospective shadow
    trade.\""""
    # THE PROSE IS ALLOWED TO NAME WHAT THE CODE MAY NOT DO. Scanning
    # the whole file caught its own docstring saying "no threshold",
    # which is the sentence that makes the rule legible. So this reads
    # executable lines only.
    code = "\n".join(line for line in ACCT_SRC.splitlines()
                     if line.strip() and not line.lstrip().startswith("#"))
    code = re.sub(r'"""(?:.|\n)*?"""', "", code)
    for word in ("threshold", "place_order", "submit", "venue_client",
                 "proposed_action =", "pmus", "clob", "live_executor"):
        assert word not in code, word
    # And no import could reach a venue from here even indirectly.
    imports = [l for l in ACCT_SRC.splitlines() if l.startswith(("import ",
                                                                "from "))]
    assert all("shadow" in l or "__future__" in l for l in imports), imports


def test_the_command_route_refuses_an_unknown_period():
    assert "SHADOW_ACCOUNTING_PERIOD_UNKNOWN" in \
        (BACKEND / "sportsassets" / "api" / "command_shadow.py").read_text()
    app = (BACKEND / "sportsassets" / "api" / "app.py").read_text()
    assert "/api/command/shadow/accounting" in app
    assert "status_code=400" in app


def test_a_failed_read_is_not_a_page_of_zeros():
    """On this screen above all others, a tidy $0 during an outage
    would be a claim about performance."""
    src = (BACKEND / "sportsassets" / "api" / "command_shadow.py").read_text()
    block = src[src.index("async def accounting("):]
    assert "SHADOW_ACCOUNTING_UNREAD" in block
    assert "_guard" in block


def test_the_store_freeze_is_idempotent_and_refuses_a_changed_rule():
    src = (BACKEND / "sportsassets" / "shadow_store.py").read_text()
    block = src[src.index("async def freeze_sizing_policy"):]
    block = block[:block.index("# ── RN1 observations")]
    for status in ("FROZEN", "ALREADY_FROZEN", "REFUSED", "STORE_NOT_READY"):
        assert status in block, status
    assert "policy_sha" in block


# ── the panel is actually callable ───────────────────────────────────
#
# WHY A FAKE POOL AND NOT JUST SOURCE READING. Every other test here
# reads the file. None of them would have caught `_guard(pool, what,
# acct.report, pool, period=period)` -- `_guard` takes positional
# arguments only, so that call raised TypeError the first time it ran
# and no amount of reading the text made it obvious. This exercises the
# real code path end to end against a stub.


class _FakeRow(dict):
    """asyncpg's Row is keyed like a mapping, which is all this needs."""


class _FakePool:
    """Every aggregate over no rows returns NULL, which is exactly what
    an empty ledger looks like -- so this also pins that the empty
    payload is the honest one rather than a crash."""

    def __init__(self):
        self.queries = []

    async def fetchrow(self, sql, *args):
        self.queries.append(sql)
        return _FakeRow({k: None for k in (
            "entry_notional_played", "intended_notional", "unfilled_notional",
            "gross_turnover", "fees", "fees_missing", "spread_cost",
            "spread_missing", "slippage_cost", "slip_missing",
            "adverse_selection", "adverse_identified", "legs", "entry_legs",
            "unclassed_legs", "settled_pnl", "realized_exit_pnl",
            "pairing_pnl", "cashout_pnl", "events", "unrealized_pnl",
            "marked_open_positions", "peak", "capital_hours", "first_at",
            "current_deployed", "positions", "open_positions",
            "closed_positions", "settled_positions", "winners", "losers",
            "breakeven", "average_win", "average_loss", "gross_profit",
            "gross_loss", "max_win", "max_loss", "average_return",
            "median_return", "avg_hold_s", "median_hold_s", "p90_hold_s",
            "opportunities_evaluated", "decisions", "no_trade_decisions",
            "acting_decisions", "high_water_mark", "max_drawdown",
            "current_equity", "current_drawdown", "points")})

    async def fetch(self, sql, *args):
        self.queries.append(sql)
        return []

    async def fetchval(self, sql, *args):
        self.queries.append(sql)
        return None


def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


def test_one_period_reports_without_raising():
    pool = _FakePool()
    out = _run(CS.accounting(pool, period="TODAY"))
    assert out["period"] == "TODAY"
    assert out["lane"] == lanes.BETTOR_EV_SHADOW


def test_every_period_reports_and_the_panel_assembles():
    pool = _FakePool()
    out = _run(CS.accounting_all(pool))
    assert sorted(out["periods"]) == sorted(acct.PERIODS)
    assert out["cohort"] == szpol.COHORT
    assert out["standardNotionalUsd"] == 1000
    assert out["disclosure"]["realOrderActivity"] == "NONE"
    assert out["disclosure"]["capitalAtRisk"] == 0


def test_the_empty_ledger_reads_exactly_as_the_directive_says():
    """Section 12, verbatim: "ENTRY_NOTIONAL_PLAYED = $0 /
    GROSS_TRADING_TURNOVER = $0 / CURRENT_CAPITAL_DEPLOYED = $0 /
    NET_SHADOW_PNL = $0 / RETURN_ON_ENTRY_NOTIONAL = NOT_APPLICABLE /
    RETURN_ON_PEAK_CAPITAL = NOT_APPLICABLE." Dollars played really are
    zero; the returns are unanswerable, not zero per cent."""
    r = _run(CS.accounting(_FakePool(), period="ALL"))
    assert r["capitalPlayed"]["entryNotionalPlayedUsd"] == 0.0
    assert r["capitalPlayed"]["grossTradingTurnoverUsd"] == 0.0
    assert r["capitalRequired"]["currentCapitalDeployedUsd"] == 0.0
    assert r["pnl"]["netShadowPnlUsd"] == 0.0
    assert r["returns"]["returnOnEntryNotional"] == "NOT_APPLICABLE"
    assert r["returns"]["returnOnPeakCapital"] == "NOT_APPLICABLE"
    # And nothing anywhere claims a rate of 0%.
    assert r["statistics"]["winRate"] == "NOT_APPLICABLE"
    assert r["equity"]["startingShadowCapital"] == "NOT_SELECTED"
    assert r["equity"]["maxDrawdownPct"] == "NOT_APPLICABLE"


def test_a_maximum_is_only_taken_over_its_own_side():
    """The first fixture with one winner and no losers printed
    MAX_LOSS = +65.00, because min() ranged over every closed trade.
    With no losers there is no worst loss."""
    body = ACCT_SRC[ACCT_SRC.index("AS max_win"):]
    assert "result_pnl > 0)\n          AS max_win" in ACCT_SRC
    assert "result_pnl < 0)\n          AS max_loss" in ACCT_SRC
    del body

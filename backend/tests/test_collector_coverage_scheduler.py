"""THE COLLECTOR'S COVERAGE SCHEDULER (P0 incident, owner 2026-10-04).

"A production trading system cannot arbitrarily lose an entire major sport
because four other sports consumed a collector budget." First-come truncation
of a fixed ranking at four metered keys left NCAAF unfetched in 137 of the 153
production cycles with a venue `cfb` event in the next 24 h (research-sql
incident_collector_cap_a, run 37233454453), while 910 of 1,518 calls went to
competitions with nothing to trade in the next 24 h. These tests prove the
replacement, `collector_coverage.plan`, multi-cycle:

  * a Saturday slate (~100 cfb events plus the NFL, MLB and soccer) serves
    NCAAF in EVERY cycle it has an event in the horizon, alongside MLB, the
    NFL and soccer, inside the UNCHANGED budget of four metered calls;
  * no enabled competition waits longer than its stated bound -- and under
    overload, longer than its stated starvation bound;
  * the budget is respected every cycle: never more metered calls than
    declared (scheduled fetches, re-fetches and discovery refreshes all
    counted), never more credits than the allowance, never more than the
    24 h envelope -- at the estimated and at a measured cost;
  * priority: earliest deadline, then held, then live/in play, then
    metered-dependent, then the most imminent start, then the largest slate;
  * every competition not fetched has a receipt with a reason, and a
    DEFERRED_TO_SLOT receipt's slot is kept;
  * the evaluation bound (MAX_PER_CYCLE) is shared by reserve, never by order.

Pure: no database, no network, no clock.
"""
from __future__ import annotations

import math
import random

import pytest

from sportsassets import collector_coverage as cov

CYCLE = 900.0
H = 3600.0
#: 2026-10-03T08:00:00Z, Saturday morning US Eastern (04:00 ET)
SAT = 1791014400.0
BUDGET = cov.MAX_METERED_CALLS_PER_CYCLE

NCAAF = "americanfootball_ncaaf"
NFL = "americanfootball_nfl"
MLB = "baseball_mlb"
UNL = "soccer_uefa_nations_league"
BRB = "soccer_brazil_serie_b"
USLC = "soccer_usa_usl_championship"
ARG2 = "soccer_argentina_primera_nacional"
NWSL = "soccer_usa_nwsl"
MLS = "soccer_usa_mls"


def _starts(first, n, spread_h):
    """n event starts from `first`, spread evenly over `spread_h` hours."""
    if n <= 1:
        return [first]
    return [first + i * spread_h * H / (n - 1) for i in range(n)]


#: THE SATURDAY SLATE, shaped on production's 2026-10-03 ET board (research-sql
#: run 37233454453, C7: cfb 107, uslc 10, arg2 9, mlb 4, brb 3, nwsl 3, unl 3)
#: plus the Sunday NFL slate (14 games, cand24): (key, family, token, listed,
#: feed_covered, event starts). Soccer and MLB are feed-covered as in
#: production (pinnapi_feed_scope sport_ids [6, 1]); football is not.
SATURDAY = [
    (NCAAF, "football", "cfb", True, False,
     _starts(SAT + 8 * H, 100, 12.0)),               # 16:00Z .. 04:00Z
    (NFL, "football", "nfl", True, False,
     [SAT + 29.5 * H] + _starts(SAT + 33 * H, 12, 3.5)
     + [SAT + 40.3 * H]),                             # Sunday 13:30Z ..
    (MLB, "baseball", "mlb", True, True,
     _starts(SAT + 9 * H, 4, 10.0)),
    (UNL, "soccer", "unl", True, True, _starts(SAT + 5 * H, 3, 6.0)),
    (BRB, "soccer", "brb", True, True, _starts(SAT + 11 * H, 3, 4.0)),
    (USLC, "soccer", "uslc", True, True, _starts(SAT + 15 * H, 10, 8.0)),
    (ARG2, "soccer", "arg2", False, True, _starts(SAT + 10 * H, 9, 9.0)),
    (NWSL, "soccer", "nwsl", False, True, _starts(SAT + 12 * H, 3, 6.0)),
    (MLS, "soccer", "mls", True, True, [SAT + 60 * H]),
]


def _horizon(starts, now):
    """What the venue horizon read reports at `now`: events in the horizon
    and the next start (an event started in the last 6 h still counts)."""
    alive = [s for s in starts if s > now - cov.HORIZON_BEHIND_S]
    inh = [s for s in alive if s <= now + cov.HORIZON_AHEAD_S]
    return len(inh), (min(alive) if alive else None)


def _board_at(board, now, last, *, held=None, since=None):
    out = []
    for key, fam, tok, listed, feed, starts in board:
        ev, nxt = _horizon(starts, now)
        out.append(cov.competition(
            key=key, family=fam, token=tok, listed=listed,
            active=True if listed else None, confirmed=(key == MLB),
            held=(held or {}).get(key, 0), events_in_horizon=ev,
            next_start=nxt, feed_covered=feed,
            last_served_at=last.get(key),
            demand_since=(since or {}).get(key)))
    return out


def _run(board, *, cost, cycles=96, start=SAT, held=None,
         envelope=cov.DAILY_CREDIT_ENVELOPE, max_calls=BUDGET):
    """Drive `cycles` consecutive cycles exactly as the collector does: each
    plan sees the last-served instants of the plans before it, the
    `demand_since` its own receipts handed back, and the 24 h ledger of what
    they spent, stamped on the cycle clock."""
    last, since, ledger, history = {}, {}, [], []
    for k in range(cycles):
        now = start + k * CYCLE
        spent = cov.spent_in_window(ledger, now=now, cycle_s=CYCLE)
        plan = cov.plan(_board_at(board, now, last, held=held, since=since),
                        now=now, cycle_s=CYCLE, daily_envelope=envelope,
                        spent_24h=spent, spend_ledger=list(ledger),
                        cost_per_fetch=cost, max_calls=max_calls)
        for key, _ in plan["fetch_order"]:
            cov.settle(plan, key, ok=True, at=now + 1.0, credits=cost,
                       basis=cov.COST_MEASURED)
            last[key] = now
            ledger.append((now, cost))
        since = {r["key"]: r["demand_since"] for r in plan["receipts"]
                 if r.get("demand_since") is not None}
        history.append((now, plan))
    return history


def _fetched(plan):
    return {k for k, _ in plan["fetch_order"]}


def _receipt(plan, key):
    return next(r for r in plan["receipts"] if r["key"] == key)


def _in_demand(r):
    return r["planned"] in (cov.SCHEDULED, cov.DEFERRED_TO_SLOT,
                            cov.DEFERRED_NO_SLOT_WITHIN_ENVELOPE)


def _max_wait(history):
    """{key: the longest run of consecutive cycles in demand and not
    fetched}."""
    waiting, worst = {}, {}
    for _, plan in history:
        for r in plan["receipts"]:
            if _in_demand(r) and r["planned"] != cov.SCHEDULED:
                waiting[r["key"]] = waiting.get(r["key"], 0) + 1
                worst[r["key"]] = max(worst.get(r["key"], 0),
                                      waiting[r["key"]])
            else:
                waiting.pop(r["key"], None)
    return worst


def _assert_starvation_bounds(history, label=""):
    """Every run of consecutive deferrals is at most the LARGEST starvation
    bound stated during the run, minus one: the bound is stated with the
    demand N of the cycle, and the proof's N is the demand when the
    competition reached its deadline -- competitions that enter later can
    never get ahead of one already past its bound."""
    run, stated = {}, {}
    for now, plan in history:
        assert len(plan["fetch_order"]) <= plan["calls"]["budget"]
        for r in plan["receipts"]:
            if _in_demand(r) and r["planned"] != cov.SCHEDULED:
                run[r["key"]] = run.get(r["key"], 0) + 1
                sb = r["starvation_bound_cycles"]
                assert sb is not None, (label, r)
                stated[r["key"]] = max(stated.get(r["key"], 0), sb)
                assert run[r["key"]] <= stated[r["key"]] - 1, (
                    label, now, run[r["key"]], stated[r["key"]], r)
            else:
                run.pop(r["key"], None)
                stated.pop(r["key"], None)


# ═════════════════════════════════════════════════════════════════════
# 1 · THE SATURDAY SLATE, MULTI-CYCLE
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("cost,basis", [
    (cov.CREDITS_PER_FETCH_ESTIMATE, "the conservative 20-credit estimate"),
    (3.0, "a measured 3 credits (h2h x three regions)"),
    (1.0, "a measured 1 credit")])
def test_saturday_serves_ncaaf_every_cycle_alongside_mlb_nfl_and_soccer(
        cost, basis):
    history = _run(SATURDAY, cost=cost, cycles=192)   # Sat 08Z .. Mon 08Z
    cfb_cycles = nfl_cycles = 0
    served = {k: 0 for k in (NCAAF, NFL, MLB, UNL, BRB, USLC)}
    for now, plan in history:
        got = _fetched(plan)
        # THE DECLARED BUDGET, every cycle: four metered calls at most
        assert len(got) <= BUDGET, (basis, now, got)
        for k in served:
            served[k] += k in got
        if _horizon(SATURDAY[0][5], now)[0] > 0:
            cfb_cycles += 1
            assert NCAAF in got, (basis, now, _receipt(plan, NCAAF))
        if _horizon(SATURDAY[1][5], now)[0] > 0:
            nfl_cycles += 1
            assert NFL in got, (basis, now, _receipt(plan, NFL))
    # NCAAF's whole Saturday slate is inside the horizon from 08Z, so it is
    # served every one of those cycles; the test exercised both footballs
    # for most of the two days
    assert cfb_cycles > 80 and nfl_cycles > 80, (cfb_cycles, nfl_cycles)
    # ...and MLB and the soccer competitions were served ALONGSIDE, not
    # bought with it: each within its two-cycle bound while in the horizon
    for k in (MLB, UNL, BRB, USLC):
        assert served[k] > 0, (basis, k)
    worst = _max_wait(history)
    for k in (MLB, UNL, BRB, USLC):
        assert worst.get(k, 0) <= cov.BOUND_CYCLES_FEED_COVERED - 1, (
            basis, k, worst)


def test_an_overloaded_saturday_still_serves_ncaaf_within_its_bound():
    """THE SAME SATURDAY WITH THREE MORE LISTED SOCCER COMPETITIONS IN THE
    HORIZON: demand (two football keys at one cycle, seven feed-covered
    keys at two) is 5.5 calls a cycle against the unchanged four, so the
    stated bounds cannot all hold and the plan SAYS so (`feasible` False,
    a starvation bound on every receipt). Nothing is starved: every
    competition is served within its stated starvation bound, NCAAF in all
    but a handful of the cycles its slate is in the horizon, and never
    more than four calls a cycle."""
    extra = [
        ("soccer_usa_mls_x", "soccer", "mlsx", True, True,
         _starts(SAT + 9 * H, 6, 10.0)),
        ("soccer_mexico_ligamx", "soccer", "lmx", True, True,
         _starts(SAT + 14 * H, 9, 8.0)),
        ("soccer_brazil_campeonato", "soccer", "bra", True, True,
         _starts(SAT + 12 * H, 8, 8.0))]
    for cost in (cov.CREDITS_PER_FETCH_ESTIMATE, 3.0):
        history = _run(SATURDAY + extra, cost=cost, cycles=192)
        assert any(p["feasible"] is False for _, p in history)
        _assert_starvation_bounds(history, label=cost)
        cfb_cycles = cfb_served = 0
        for now, plan in history:
            assert len(plan["fetch_order"]) <= BUDGET
            if _horizon(SATURDAY[0][5], now)[0] > 0:
                cfb_cycles += 1
                cfb_served += NCAAF in _fetched(plan)
        assert cfb_cycles > 80
        assert cfb_served >= cfb_cycles - 5, (cost, cfb_served, cfb_cycles)
        worst = _max_wait(history)
        # NCAAF's stated starvation bound under this load is four cycles
        # (two + ceil(8 / 4)); it never waited more than one
        assert worst.get(NCAAF, 0) <= 1, worst
        for key, *_ in SATURDAY + extra:
            if key in (ARG2, NWSL):                  # the provider lists none
                continue
            assert any(key in _fetched(p) for _, p in history), key


def test_the_old_first_come_rule_dropped_ncaaf_and_the_schedule_does_not():
    """THE MEASURED FAILURE, REPRODUCED AND REPAIRED. Sunday 2026-10-04
    (heartbeat): venue board cfb 3, unl 26, nfl 14, brb 8. The old rule --
    MLB always, then the top three by board events over an unbounded future,
    truncated at four -- requested MLB, UNL, NFL and Brazil Serie B and
    dropped NCAAF, whose three games were the ones about to start, while
    Serie B and UNL had nothing in the next 24 h."""
    board = {"mlb": 4, "unl": 26, "nfl": 14, "brb": 8, "cfb": 3}
    old = [MLB] + [k for k, _ in sorted(
        [(UNL, board["unl"]), (NFL, board["nfl"]), (BRB, board["brb"]),
         (NCAAF, board["cfb"])], key=lambda kv: -kv[1])][:BUDGET - 1]
    assert NCAAF not in old
    now = SAT + 24 * H                                  # Sunday 08:00Z
    comps = [
        cov.competition(key=NCAAF, family="football", listed=True,
                        active=True, events_in_horizon=3,
                        next_start=now + 2 * H, board_events=3),
        cov.competition(key=NFL, family="football", listed=True, active=True,
                        events_in_horizon=14, next_start=now + 5.5 * H,
                        board_events=14),
        cov.competition(key=MLB, family="baseball", confirmed=True,
                        listed=True, active=True, events_in_horizon=2,
                        next_start=now + 9 * H, feed_covered=True),
        cov.competition(key=UNL, family="soccer", listed=True, active=True,
                        events_in_horizon=0, next_start=now + 50 * H,
                        board_events=26, feed_covered=True),
        cov.competition(key=BRB, family="soccer", listed=True, active=True,
                        events_in_horizon=0, next_start=now + 30 * H,
                        board_events=8, feed_covered=True)]
    plan = cov.plan(comps, now=now, cycle_s=CYCLE)
    assert [k for k, _ in plan["fetch_order"]] == [NCAAF, NFL, MLB]
    for k in (UNL, BRB):
        assert _receipt(plan, k)["planned"] == \
            cov.SKIPPED_NO_VENUE_EVENT_IN_HORIZON
    assert plan["calls"]["scheduled"] == 3 <= BUDGET


def test_no_competition_waits_longer_than_its_stated_bound():
    for cost in (cov.CREDITS_PER_FETCH_ESTIMATE, 3.0):
        history = _run(SATURDAY, cost=cost, cycles=192)
        assert all(p["feasible"] for _, p in history), cost
        bounds = {}
        for _, plan in history:
            for r in plan["receipts"]:
                if _in_demand(r):
                    bounds[r["key"]] = r["bound_cycles"]
                    # feasible: the starvation bound IS the staleness bound
                    assert r["starvation_bound_cycles"] == r["bound_cycles"]
        worst = _max_wait(history)
        for k, w in worst.items():
            # a deferral never outlasts the bound: bound - 1 consecutive
            # deferrals at most
            assert w <= bounds[k] - 1, (cost, k, w, bounds[k])


# ═════════════════════════════════════════════════════════════════════
# 2 · THE BUDGET ITSELF IS RESPECTED
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("cost", [cov.CREDITS_PER_FETCH_ESTIMATE, 3.0, 1.0])
def test_never_more_calls_or_credits_than_declared(cost):
    history = _run(SATURDAY, cost=cost, cycles=288)   # three days
    allowance = cov.per_cycle_allowance(cov.DAILY_CREDIT_ENVELOPE, CYCLE)
    spends = []
    for now, plan in history:
        s = cov.spend(plan)
        assert len(plan["fetch_order"]) <= BUDGET
        assert cov.calls_made(plan) <= plan["calls"]["budget"] == BUDGET
        assert s <= allowance + 1e-9, (now, s)
        assert s == plan["envelope"]["planned_spend"]
        rec = cov.cycle_receipt(plan)
        assert rec["budget_respected"] is True
        assert rec["calls_made"] == len(plan["fetch_order"])
        spends.append((now, s))
    for i in range(len(spends)):
        window = sum(s for t, s in spends[i:] if t - spends[i][0] < 86400.0)
        assert window <= cov.DAILY_CREDIT_ENVELOPE + 1e-9, (i, window)


def test_extra_metered_calls_are_claimed_against_the_same_budget():
    """A re-fetch or an unmeasured discovery refresh is a metered call: it
    is made only when `claim_call` grants one, and never past the budget."""
    comps = [cov.competition(key="k%d" % i, family="soccer", listed=True,
                             active=True, events_in_horizon=3,
                             next_start=SAT + H)
             for i in range(2)]
    plan = cov.plan(comps, now=SAT, cycle_s=CYCLE)
    assert plan["calls"]["scheduled"] == 2 and cov.calls_left(plan) == 2
    assert cov.claim_call(plan, cov.CALL_REFETCH) is True
    assert cov.claim_call(plan, cov.CALL_EVENTS) is True
    assert cov.calls_left(plan) == 0
    assert cov.claim_call(plan, cov.CALL_EVENTS) is False
    rec = cov.cycle_receipt(plan)
    assert rec["calls_made"] == 4 == rec["calls_budget"]
    assert rec["calls_by_kind"] == {cov.CALL_ODDS: 2, cov.CALL_REFETCH: 1,
                                    cov.CALL_EVENTS: 1}
    assert rec["budget_respected"] is True
    # a plan that failed to build records no budget and grants nothing
    assert cov.claim_call({"failed": "X", "receipts": []},
                          cov.CALL_REFETCH) is False


def test_a_cheaper_measured_call_spends_less_and_never_buys_more_calls():
    est = _run(SATURDAY, cost=cov.CREDITS_PER_FETCH_ESTIMATE, cycles=96)
    cheap = _run(SATURDAY, cost=3.0, cycles=96)
    est_spend = sum(cov.spend(p) for _, p in est)
    cheap_spend = sum(cov.spend(p) for _, p in cheap)
    assert cheap_spend < est_spend <= cov.DAILY_CREDIT_ENVELOPE
    assert max(len(p["fetch_order"]) for _, p in cheap) <= BUDGET
    assert sum(len(p["fetch_order"]) for _, p in cheap) == \
        sum(len(p["fetch_order"]) for _, p in est)


def test_the_envelope_stops_spend_when_the_day_is_spent():
    comps = [cov.competition(key="k%d" % i, family="football", listed=True,
                             active=True, events_in_horizon=5,
                             next_start=SAT + H)
             for i in range(6)]
    # R30A FIX STAGE: the held competition has its game IN PLAY. This pin
    # used a held competition with NO venue event in the horizon -- exactly
    # the case that took a metered call every cycle for nothing (positions
    # on finished games awaiting settlement; adversarial review, finding 1)
    # and is now SKIPPED by name.
    comps.append(cov.competition(key=UNL, family="soccer", listed=True,
                                 active=True, held=1, events_in_horizon=1,
                                 next_start=SAT - H,
                                 last_served_at=SAT - CYCLE,
                                 demand_since=SAT - 4 * CYCLE))
    plan = cov.plan(comps, now=SAT, cycle_s=CYCLE)
    assert plan["fetch_order"][0][0] == UNL                    # held, due
    # 20 credits left of the day: the held competition alone
    tight = cov.plan(comps, now=SAT, cycle_s=CYCLE,
                     spent_24h=cov.DAILY_CREDIT_ENVELOPE - 20.0)
    assert [k for k, _ in tight["fetch_order"]] == [UNL]
    assert tight["envelope"]["planned_spend"] == 20.0
    spent = cov.plan(comps, now=SAT, cycle_s=CYCLE,
                     spent_24h=cov.DAILY_CREDIT_ENVELOPE)
    assert spent["fetch_order"] == []
    for r in spent["receipts"]:
        # the rule run forward (the envelope rolls over) still gives a slot:
        # with no ledger the day's spend is taken as spent NOW, the latest
        # it can have been, so the slot is never earlier than the truth
        # (R30A fix stage: it used to assume a FULL envelope the next cycle)
        assert r["planned"] == cov.DEFERRED_TO_SLOT and r["next_slot_at"]
        assert r["budget_binding"] == cov.BINDING_DAILY_ENVELOPE
        assert r["next_slot_at"] - SAT >= cov.envelope_window_s(CYCLE)


def test_a_call_dearer_than_a_cycle_allowance_is_named_not_slotted():
    comps = [cov.competition(key=NCAAF, family="football", listed=True,
                             active=True, events_in_horizon=10, cost=500.0)]
    plan = cov.plan(comps, now=SAT, cycle_s=CYCLE)
    r = plan["receipts"][0]
    assert r["planned"] == cov.DEFERRED_NO_SLOT_WITHIN_ENVELOPE
    assert r["next_slot_at"] is None and r["starvation_bound_cycles"] is None
    assert plan["fetch_order"] == []


# ═════════════════════════════════════════════════════════════════════
# 3 · PRIORITY
# ═════════════════════════════════════════════════════════════════════

def _c(key, **kw):
    base = dict(key=key, family="soccer", listed=True, active=True,
                events_in_horizon=5, next_start=SAT + 6 * H)
    base.update(kw)
    return cov.competition(**base)


def _order(comps, now=SAT, max_calls=99):
    plan = cov.plan(comps, now=now, cycle_s=CYCLE, max_calls=max_calls,
                    cost_per_fetch=1.0)
    return [k for k, _ in plan["fetch_order"]], plan


def test_priority_earliest_deadline_dominates_everything_else():
    """A competition whose bound expires this cycle outranks a held, live,
    metered-dependent, imminent, larger one that is not yet due -- that is
    the starvation protection, so nothing ranks above it."""
    # R30A FIX STAGE: both were in demand before this cycle, which the
    # scheduler is now TOLD (`demand_since`, fed back from the previous
    # plan's receipts) instead of inferring from the last service -- the
    # inference let a competition re-entering the horizon count as overdue
    # on a service from a previous run (adversarial review, finding 4).
    late = _c("a_late", feed_covered=True, next_start=SAT + 20 * H,
              events_in_horizon=1, last_served_at=SAT - 3 * CYCLE,
              demand_since=SAT - 10 * CYCLE)
    fresh = _c("b_fresh", held=1, next_start=SAT - H, family="football",
               events_in_horizon=50, last_served_at=SAT - CYCLE,
               feed_covered=False, demand_since=SAT - 10 * CYCLE)
    # bound 1 for the held one: served last cycle it is due (1); the
    # feed-covered one (bound 2) served three cycles ago is PAST it (2)
    order, plan = _order([fresh, late], max_calls=1)
    assert order == ["a_late"]
    assert _receipt(plan, "a_late")["overdue_cycles"] == 2
    assert _receipt(plan, "b_fresh")["overdue_cycles"] == 1


def test_priority_among_the_equally_due_held_live_metered_imminent_slate():
    # R30A FIX STAGE: all six are DUE THIS CYCLE, each by its own bound --
    # the one-cycle ones entering demand now, the two-cycle (feed-covered)
    # ones in demand since last cycle and never served. (This pin made all
    # six "never served" and so equally due whatever their bound, which is
    # the defect the adversarial review's finding 4 removed: a competition
    # entering demand is due only when its own bound says.)
    ago = dict(demand_since=SAT - CYCLE)
    held = _c("held", held=1)
    live = _c("live", next_start=SAT - 0.5 * H)
    metered = _c("metered", family="football", feed_covered=False)
    feed_soon = _c("feed_soon", feed_covered=True, next_start=SAT + H, **ago)
    feed_later_big = _c("feed_later_big", feed_covered=True,
                        next_start=SAT + 3 * H, events_in_horizon=40, **ago)
    feed_later_small = _c("feed_later_small", feed_covered=True,
                          next_start=SAT + 3 * H, events_in_horizon=2, **ago)
    comps = [feed_later_small, feed_later_big, feed_soon, metered, live,
             held]
    order, plan = _order(comps)
    # all due this cycle by their own bounds: equally due (1)
    assert {_receipt(plan, k)["overdue_cycles"] for k in order} == {1}
    assert order == ["held", "live", "metered", "feed_soon",
                     "feed_later_big", "feed_later_small"]
    assert _receipt(plan, "live")["in_play"] is True


def test_live_first_never_overrides_a_deadline():
    """In play first among the equally due -- but a live competition served
    last cycle (not due) waits behind a pre-match one whose bound expires."""
    # R30A FIX STAGE: both in demand before this cycle (see above)
    live = _c("live", feed_covered=True, next_start=SAT - H,
              last_served_at=SAT - CYCLE, demand_since=SAT - 10 * CYCLE)
    due = _c("due", feed_covered=True, next_start=SAT + 10 * H,
             last_served_at=SAT - 2 * CYCLE, demand_since=SAT - 10 * CYCLE)
    order, _ = _order([live, due], max_calls=1)
    assert order == ["due"]


def test_the_plan_is_deterministic():
    now = SAT + 14 * H
    a = cov.plan(_board_at(SATURDAY, now, {}), now=now, cycle_s=CYCLE)
    b = cov.plan(list(reversed(_board_at(SATURDAY, now, {}))), now=now,
                 cycle_s=CYCLE)
    assert a["fetch_order"] == b["fetch_order"]
    assert [r["key"] for r in a["receipts"]] != \
        [r["key"] for r in b["receipts"]]           # inputs did differ


# ═════════════════════════════════════════════════════════════════════
# 4 · OVERLOAD: THE STARVATION BOUND
# ═════════════════════════════════════════════════════════════════════

def test_overload_is_stated_and_no_competition_starves():
    """Nine metered-dependent competitions and four calls: the bound of one
    cycle cannot hold, the plan SAYS so, and each is still served within
    bound + ceil((N - 1) / K) = 1 + 2 = 3 cycles."""
    board = [("f%d" % i, "football", "t%d" % i, True, False,
              _starts(SAT + (i + 1) * H, 5, 4.0)) for i in range(9)]
    history = _run(board, cost=cov.CREDITS_PER_FETCH_ESTIMATE, cycles=48)
    assert all(p["feasible"] is False for _, p in history)
    sb = {r["starvation_bound_cycles"] for _, p in history
          for r in p["receipts"] if _in_demand(r)}
    # R30A FIX STAGE: the STATED overload bound counts from the largest
    # staleness bound a competition can carry (two), because a
    # competition's own bound can relax mid-run (a held position closes) --
    # the adversarial harness found a wait one cycle past the bound stated
    # with the competition's current bound of one. The OBSERVED wait on this
    # fixed board is still within the tighter figure, asserted below.
    assert sb == {cov.BOUND_CYCLES_MAX + math.ceil(8 / BUDGET)}
    worst = _max_wait(history)
    assert max(worst.values()) <= 1 + math.ceil(8 / BUDGET) - 1, worst
    # every one of them was served, repeatedly
    for i in range(9):
        assert sum(("f%d" % i) in _fetched(p) for _, p in history) >= 12


def test_held_positions_cannot_starve_the_rest():
    """Five held competitions (bound 1) and four calls: held is a tie-break
    among the equally due, not a tier above the deadline, so a competition
    that is not held is still served within its starvation bound."""
    board = [("h%d" % i, "soccer", "h%d" % i, True, True,
              _starts(SAT + H, 3, 2.0)) for i in range(5)]
    board.append((NCAAF, "football", "cfb", True, False,
                  _starts(SAT + 2 * H, 50, 10.0)))
    held = {"h%d" % i: 1 for i in range(5)}
    history = _run(board, cost=1.0, cycles=24, held=held)
    worst = _max_wait(history)
    bound = 1 + math.ceil(5 / BUDGET)
    assert worst.get(NCAAF, 0) <= bound - 1, worst
    assert sum(NCAAF in _fetched(p) for _, p in history) >= 24 // bound


def test_random_boards_keep_every_stated_bound():
    rng = random.Random(20261004)
    overloaded = 0
    for trial in range(40):
        board = []
        for i in range(rng.randint(3, 14)):
            fam = rng.choice(("soccer", "football", "baseball"))
            feed = fam != "football" and rng.random() < 0.6
            first = SAT + rng.uniform(-3, 40) * H
            board.append(("c%d" % i, fam, "t%d" % i, rng.random() > 0.15,
                          feed, _starts(first, rng.randint(1, 30),
                                        rng.uniform(0, 30))))
        held = {"c0": 1} if rng.random() < 0.3 else None
        history = _run(board, cost=rng.choice((1.0, 3.0, 20.0)), cycles=96,
                       max_calls=rng.choice((2, 3, 4)), held=held)
        overloaded += any(p["feasible"] is False for _, p in history)
        _assert_starvation_bounds(history, label=trial)
        # and where the demand is feasible the staleness bound itself holds
        for _, plan in history:
            if plan["feasible"]:
                for r in plan["receipts"]:
                    if _in_demand(r):
                        assert r["starvation_bound_cycles"] == \
                            r["bound_cycles"]
    # the boards exercised the overload path, not only the easy one
    assert overloaded >= 5, overloaded


# ═════════════════════════════════════════════════════════════════════
# 5 · RECEIPTS, NEVER SILENT DROPS
# ═════════════════════════════════════════════════════════════════════

def test_every_competition_has_exactly_one_receipt_with_a_reason():
    now = SAT + 12 * H
    plan = cov.plan(_board_at(SATURDAY, now, {}), now=now, cycle_s=CYCLE)
    keys = [r["key"] for r in plan["receipts"]]
    assert sorted(keys) == sorted(b[0] for b in SATURDAY)
    by = {r["key"]: r for r in plan["receipts"]}
    assert by[ARG2]["planned"] == cov.PROVIDER_DOES_NOT_LIST
    assert by[NWSL]["planned"] == cov.PROVIDER_DOES_NOT_LIST
    assert by[MLS]["planned"] == cov.SKIPPED_NO_VENUE_EVENT_IN_HORIZON
    for r in plan["receipts"]:
        assert r["planned"] in cov.PLANNED_RECEIPTS
        assert r["why"], r
    for r in plan["receipts"]:
        if r["planned"] == cov.DEFERRED_TO_SLOT:
            assert "metered calls" in r["why"] and r["next_slot_at"] > now


def test_a_deferral_carries_a_slot_and_the_slot_is_kept():
    history = _run(SATURDAY, cost=cov.CREDITS_PER_FETCH_ESTIMATE, cycles=96)
    promised = 0
    by_time = {now: plan for now, plan in history}
    for now, plan in history:
        for r in plan["receipts"]:
            if r["planned"] != cov.DEFERRED_TO_SLOT:
                continue
            promised += 1
            slot = r["next_slot_at"]
            assert slot is not None and slot > now
            if by_time.get(slot) is None:
                continue                       # past the simulated day
            window = [(t, p) for t, p in history if now < t <= slot]
            # The promise is made on the board as it stands. A competition
            # whose last event leaves the horizon before its slot is SKIPPED
            # by name then (nothing left to fetch), not silently dropped.
            if any(_receipt(p, r["key"])["planned"]
                   == cov.SKIPPED_NO_VENUE_EVENT_IN_HORIZON for _, p in window):
                continue
            # otherwise it is fetched AT OR BEFORE the promised cycle
            assert [t for t, p in window if r["key"] in _fetched(p)], (now, r)
    assert promised > 0


def test_cycles_since_served_is_counted_on_every_receipt():
    history = _run(SATURDAY, cost=cov.CREDITS_PER_FETCH_ESTIMATE, cycles=8)
    first = history[0][1]
    assert all(r["cycles_since_served"] is None for r in first["receipts"])
    for now, plan in history[1:]:
        for r in plan["receipts"]:
            if r["last_served_at"] is not None:
                assert r["cycles_since_served"] == round(
                    (now - r["last_served_at"]) / CYCLE)


def test_no_venue_event_in_the_horizon_costs_nothing_and_says_when():
    now = SAT
    starts = [now + 30 * H, now + 31 * H]
    comps = [cov.competition(key=BRB, family="soccer", listed=True,
                             active=True, events_in_horizon=0,
                             next_start=min(starts), feed_covered=True)]
    plan = cov.plan(comps, now=now, cycle_s=CYCLE)
    r = plan["receipts"][0]
    assert r["planned"] == cov.SKIPPED_NO_VENUE_EVENT_IN_HORIZON
    assert plan["fetch_order"] == [] and plan["envelope"]["planned_spend"] == 0
    assert r["next_slot_at"] == min(starts) - cov.HORIZON_AHEAD_S


def test_an_unread_horizon_is_not_an_empty_one():
    """A failed venue read must not make a competition free to skip."""
    comps = [cov.competition(key=NCAAF, family="football", listed=True,
                             active=True, events_in_horizon=None)]
    plan = cov.plan(comps, now=SAT, cycle_s=CYCLE)
    assert plan["receipts"][0]["planned"] == cov.SCHEDULED
    assert plan["receipts"][0]["horizon_known"] is False


def test_a_listing_with_no_stated_start_is_not_an_empty_horizon():
    """A venue market with no gameStartTime (us_premap.game_start NULL)
    cannot be placed in or out of the horizon: the competition stays in
    demand rather than being SKIPPED on a zero count. Found through the
    real cycle (test_completed_game_collector_path): its MLB listing states
    no start, and the zero count skipped the only competition it fetches."""
    comps = [cov.competition(key=MLB, family="baseball", confirmed=True,
                             listed=True, active=True, events_in_horizon=0,
                             start_unknown=2, feed_covered=True),
             cov.competition(key=BRB, family="soccer", listed=True,
                             active=True, events_in_horizon=0,
                             next_start=SAT + 40 * H, feed_covered=True)]
    plan = cov.plan(comps, now=SAT, cycle_s=CYCLE)
    by = {r["key"]: r for r in plan["receipts"]}
    assert by[MLB]["planned"] == cov.SCHEDULED
    assert by[MLB]["events_start_unknown"] == 2
    assert "no stated start time" in by[MLB]["why"]
    # a competition whose every listing states a start outside the horizon
    # is still skipped, by name
    assert by[BRB]["planned"] == cov.SKIPPED_NO_VENUE_EVENT_IN_HORIZON
    assert by[BRB]["events_start_unknown"] == 0


def test_an_unread_catalogue_confirms_nothing_but_the_confirmed_and_held():
    comps = [cov.competition(key=MLB, family="baseball", confirmed=True,
                             listed=None, events_in_horizon=3),
             cov.competition(key=NCAAF, family="football", listed=None,
                             events_in_horizon=50),
             cov.competition(key=UNL, family="soccer", listed=None, held=2,
                             events_in_horizon=1),
             cov.competition(key=BRB, family="soccer", listed=None, held=1,
                             events_in_horizon=0)]
    by = {r["key"]: r for r in cov.plan(comps, now=SAT,
                                        cycle_s=CYCLE)["receipts"]}
    assert by[MLB]["planned"] == cov.SCHEDULED
    assert by[NCAAF]["planned"] == cov.PROVIDER_CATALOGUE_UNREAD
    # a held competition is served though the catalogue is unread...
    assert by[UNL]["planned"] == cov.SCHEDULED
    # ...but not whatever its horizon. R30A FIX STAGE: this pin read "held
    # is served whatever its horizon", which spent a metered call every
    # cycle on positions on finished games awaiting settlement (adversarial
    # review, finding 1): a held competition with no venue event in the
    # horizon is now skipped by name.
    assert by[BRB]["planned"] == cov.SKIPPED_NO_VENUE_EVENT_IN_HORIZON
    assert "held" in by[BRB]["why"]


def test_an_inactive_competition_is_refused_separately():
    comps = [cov.competition(key=MLS, family="soccer", listed=True,
                             active=False, events_in_horizon=4)]
    assert cov.plan(comps, now=SAT, cycle_s=CYCLE)["receipts"][0][
        "planned"] == cov.PROVIDER_LISTS_INACTIVE


def test_settled_receipts_carry_what_the_fetch_cost():
    comps = [cov.competition(key=NCAAF, family="football", listed=True,
                             active=True, events_in_horizon=10)]
    plan = cov.plan(comps, now=SAT, cycle_s=CYCLE)
    assert cov.unsettled(plan) == [NCAAF]
    r = cov.settle(plan, NCAAF, ok=True, at=SAT + 2, credits=1.0,
                   basis=cov.COST_MEASURED, events=97)
    assert r["final"] == cov.FETCHED and r["credits_charged"] == 1.0
    assert cov.unsettled(plan) == [] and cov.spend(plan) == 1.0
    failed = cov.plan(comps, now=SAT, cycle_s=CYCLE)
    cov.settle(failed, NCAAF, ok=False, at=SAT + 2, credits=0.0,
               basis=cov.COST_MEASURED)
    assert failed["receipts"][0]["final"] == cov.FETCH_FAILED
    d = cov.digest(plan)
    assert d["cycle"]["calls_made"] == 1 and d["spent_this_cycle"] == 1.0
    assert d["by_receipt"] == {cov.FETCHED: 1}


# ═════════════════════════════════════════════════════════════════════
# 6 · THE EVALUATION BOUND, SHARED BY RESERVE
# ═════════════════════════════════════════════════════════════════════

def test_the_evaluation_bound_is_shared_max_min_fair():
    shares = cov.evaluation_shares([(NCAAF, 100), (NFL, 14), (MLB, 4)], 40)
    assert shares == {NCAAF: 22, NFL: 14, MLB: 4}
    assert sum(shares.values()) == 40
    reserve = cov.reserve_after([NCAAF, NFL, MLB], shares)
    # NCAAF, first, may evaluate while evaluated < 40 - 18 = 22: the NFL's 14
    # and MLB's 4 are kept for them
    assert reserve == {NCAAF: 18, NFL: 4, MLB: 0}
    # a competition with less demand than its share releases the surplus
    assert cov.evaluation_shares([(NFL, 2), (NCAAF, 100)], 40) == {
        NFL: 2, NCAAF: 38}
    # fewer slots than competitions: one each, in fetch order
    assert cov.evaluation_shares([("a", 5), ("b", 5), ("c", 5)], 2) == {
        "a": 1, "b": 1, "c": 0}


def test_within_a_fetch_deferred_first_then_live_then_soon():
    deferred = {"e2": SAT - 900.0}
    events = ["e1", "e2", "e3"]
    order = sorted(events, key=lambda e: cov.candidate_order_key(
        e, commence_epoch=SAT + 20 * H, now=SAT, deferred_since=deferred,
        freshness_key=(events.index(e),)))
    assert order[0] == "e2"
    starts = {"e1": SAT + 20 * H, "e2": SAT + H, "e3": SAT - 0.5 * H}
    live = sorted(events, key=lambda e: cov.candidate_order_key(
        e, commence_epoch=starts[e], now=SAT, deferred_since={},
        freshness_key=(events.index(e),)))
    assert live == ["e3", "e2", "e1"]


# ═════════════════════════════════════════════════════════════════════
# 7 · THE DECLARED BUDGET, STATED
# ═════════════════════════════════════════════════════════════════════

def test_the_budget_is_unchanged_and_stated_with_its_arithmetic():
    chg = cov.ENVELOPE_CHANGE
    assert cov.MAX_METERED_CALLS_PER_CYCLE == 4
    assert chg["max_metered_calls_per_cycle"] == 4
    assert chg["daily_envelope_credits"] == cov.DAILY_CREDIT_ENVELOPE == 7680.0
    assert cov.per_cycle_allowance(cov.DAILY_CREDIT_ENVELOPE, CYCLE) == 80.0
    # at the conservative estimate the envelope buys exactly the four calls
    assert 80.0 / cov.CREDITS_PER_FETCH_ESTIMATE == BUDGET
    for k in ("arithmetic", "what_changed", "net_change_in_spend"):
        assert chg[k]


# ═════════════════════════════════════════════════════════════════════
# 8 · REPAIRS AFTER THE ADVERSARIAL REVIEW (each test failed first)
# ═════════════════════════════════════════════════════════════════════

#: A held position's competition whose last venue event started 30 h ago:
#: a FINISHED game whose paper position waits for its settlement row (the
#: held watch's query counts any positive net quantity with no settlement).
#: The provider's odds response no longer lists such a game.
def _finished_held(i):
    return ("soccer_held_finished_%d" % i, "soccer", "hf%d" % i, True, True,
            [SAT - 30 * H])


@pytest.mark.parametrize("n_held", [3, 4])
def test_a_held_competition_with_nothing_in_the_horizon_costs_nothing(n_held):
    """FINDING 1. A held competition passed the gate whatever its horizon
    with a one-cycle bound, so held positions on finished games awaiting
    settlement took a metered call EVERY cycle for a response that cannot
    list them: on the Saturday board with three of them NCAAF was served in
    51 of 96 cycles, and 181 of 384 calls went to competitions with no venue
    event in the horizon. A held competition is now held to the same
    horizon as every other: nothing to fetch, no call."""
    board = SATURDAY + [_finished_held(i) for i in range(n_held)]
    held = {_finished_held(i)[0]: 1 for i in range(n_held)}
    history = _run(board, cost=cov.CREDITS_PER_FETCH_ESTIMATE, cycles=96,
                   held=held)
    cfb_cycles = cfb_served = wasted = 0
    for now, plan in history:
        for key, _ in plan["fetch_order"]:
            r = _receipt(plan, key)
            # never a metered call on a competition KNOWN to have no venue
            # event in the horizon (and none listed without a start)
            wasted += (r["events_in_horizon"] == 0
                       and not r["events_start_unknown"])
        for i in range(n_held):
            r = _receipt(plan, _finished_held(i)[0])
            assert r["planned"] == cov.SKIPPED_NO_VENUE_EVENT_IN_HORIZON, r
            assert "held" in r["why"], r
        if _horizon(SATURDAY[0][5], now)[0] > 0:
            cfb_cycles += 1
            cfb_served += NCAAF in _fetched(plan)
    assert wasted == 0
    assert cfb_cycles == 96 and cfb_served == 96, (cfb_served, cfb_cycles)


def test_a_held_competition_in_play_is_still_served_every_cycle():
    """The held priority stays where it is useful: a held position whose
    game is in play keeps its competition at a one-cycle bound."""
    board = SATURDAY + [("soccer_held_live", "soccer", "hl", True, True,
                         [SAT - 1 * H])]
    history = _run(board, cost=cov.CREDITS_PER_FETCH_ESTIMATE, cycles=8,
                   held={"soccer_held_live": 1})
    for _, plan in history:
        r = _receipt(plan, "soccer_held_live")
        assert r["bound_cycles"] == cov.BOUND_CYCLES_HELD
        assert "soccer_held_live" in _fetched(plan), r


def test_a_new_competition_waits_no_longer_than_its_own_bound():
    """FINDING 4 (minimal reproduction). A never-served competition counted
    as DUE NOW whatever its bound, so four feed-covered soccer competitions
    in play (bound two) tied with NCAAF (bound one) and won the in-play
    tie-break: NCAAF was DEFERRED with a stated bound of one cycle although
    the demand (4 x 1/2 + 1 = 3 calls) fits in four. A competition entering
    demand now has its deadline counted from its own bound."""
    now = SAT
    comps = [cov.competition(key="soccer_live_%d" % i, family="soccer",
                             listed=True, active=True, events_in_horizon=2,
                             next_start=now - 0.5 * H, feed_covered=True)
             for i in range(4)]
    comps.append(cov.competition(key=NCAAF, family="football", listed=True,
                                 active=True, events_in_horizon=50,
                                 next_start=now + 2 * H))
    plan = cov.plan(comps, now=now, cycle_s=CYCLE)
    assert plan["feasible"] is True
    assert NCAAF in _fetched(plan), _receipt(plan, NCAAF)
    for r in plan["receipts"]:
        if r["planned"] == cov.DEFERRED_TO_SLOT:
            # a bound-two competition may wait ONE cycle: its slot is the next
            assert r["bound_cycles"] == 2 and \
                r["next_slot_at"] == now + CYCLE, r


def test_a_competition_re_entering_the_horizon_does_not_jump_the_queue():
    """FINDING 4. A competition re-entering the horizon carried the instant
    it was last served, days ago, and so counted as long overdue -- ahead of
    a bound-one competition whose deadline is this cycle. Its staleness is
    counted from when it entered demand."""
    now = SAT
    due = cov.competition(key=NCAAF, family="football", listed=True,
                          active=True, events_in_horizon=50,
                          next_start=now + 2 * H, last_served_at=now - CYCLE)
    back = cov.competition(key=UNL, family="soccer", listed=True,
                           active=True, events_in_horizon=2,
                           next_start=now + 20 * H, feed_covered=True,
                           last_served_at=now - 30 * CYCLE)
    plan = cov.plan([back, due], now=now, cycle_s=CYCLE, max_calls=1)
    assert _fetched(plan) == {NCAAF}, plan["receipts"]
    r = _receipt(plan, UNL)
    assert r["planned"] == cov.DEFERRED_TO_SLOT
    # entering demand now, with a two-cycle bound: not overdue at all
    assert r["overdue_cycles"] == 0
    # one call for 1 + 1/2 calls of demand: overloaded, and said so; its
    # slot is within the starvation bound the plan states
    assert plan["feasible"] is False
    assert r["next_slot_at"] - now <= r["starvation_bound_cycles"] * CYCLE
    # with the two calls the demand needs, both are served now
    both = cov.plan([back, due], now=now, cycle_s=CYCLE, max_calls=2)
    assert _fetched(both) == {NCAAF, UNL} and both["feasible"] is True


def _fuzz_boards(trials, *, max_calls, jitter, held_toggle, seed0=0):
    """THE VERIFIER'S ADVERSARIAL HARNESS (fuzz_bound2), as a test: boards
    whose events enter and leave the horizon, held positions toggling,
    cadence jitter of up to 500 s, uniform cost (the production shape:
    plan_coverage never sets a per-competition cost). Yields
    (trial, step, plan) after feeding each plan's receipts back exactly as
    the collector does."""
    for trial in range(trials):
        rng = random.Random(seed0 + trial)
        board = []
        for i in range(rng.randint(2, 16)):
            fam = rng.choice(("soccer", "football", "baseball"))
            feed = fam != "football" and rng.random() < 0.6
            first = SAT + rng.uniform(-5, 48) * H
            k = rng.randint(1, 25)
            spread = rng.uniform(0, 30)
            starts = [first + j * spread * H / max(1, k - 1)
                      for j in range(k)]
            board.append(("c%d" % i, fam, feed, starts, rng.random() > 0.1))
        cost = rng.choice((1.0, 3.0, 20.0))
        last, carry, ledger = {}, {}, []
        held_keys: set = set()
        now = SAT
        for step in range(150):
            if held_toggle and rng.random() < 0.05:
                held_keys = {b[0] for b in board if rng.random() < 0.2}
            comps = []
            for key, fam, feed, starts, listed in board:
                ev, nxt = _horizon(starts, now)
                comps.append(cov.competition(
                    key=key, family=fam, listed=listed,
                    active=True if listed else None,
                    held=1 if key in held_keys else 0,
                    events_in_horizon=ev, next_start=nxt, feed_covered=feed,
                    last_served_at=last.get(key), **carry.get(key, {})))
            spent = sum(c for t, c in ledger if now - t < 86400.0)
            plan = cov.plan(comps, now=now, cycle_s=CYCLE, spent_24h=spent,
                            spend_ledger=list(ledger), cost_per_fetch=cost,
                            max_calls=max_calls)
            yield trial, step, plan
            for key, _ in plan["fetch_order"]:
                last[key] = now
                ledger.append((now, cost))
            carry = _carry(plan)
            now += CYCLE + (rng.uniform(0, 500) if jitter else 0.0)


def _carry(plan):
    """What the collector feeds back from a plan's receipts into the next
    plan's competitions."""
    return {r["key"]: {"demand_since": r["demand_since"]}
            for r in plan["receipts"] if r.get("demand_since") is not None}


@pytest.mark.parametrize("max_calls,jitter,held_toggle", [
    (4, False, False), (4, False, True), (4, True, False), (4, True, True),
    (2, False, True), (2, True, True), (1, True, True)])
def test_changing_boards_never_wait_past_a_stated_bound(max_calls, jitter,
                                                        held_toggle):
    """FINDING 4 (the fuzz). On fixed boards the stated bounds held; on
    changing boards the verifier's harness found 14-27 runs of deferrals
    longer than every bound stated during the run, all of them in plans
    declared feasible. Every plan now states the bound that holds for its
    demand AS IT STANDS -- feasible only when nothing is already past its
    bound and every competition due this cycle fits in it -- and no run of
    deferrals outlasts the largest bound stated during it."""
    run, stated, violations, overloaded = {}, {}, [], 0
    for trial, step, plan in _fuzz_boards(
            90, max_calls=max_calls, jitter=jitter, held_toggle=held_toggle):
        if step == 0:
            run, stated = {}, {}
        assert len(plan["fetch_order"]) <= max_calls
        overloaded += plan["feasible"] is False
        for r in plan["receipts"]:
            if _in_demand(r) and r["planned"] != cov.SCHEDULED:
                run[r["key"]] = run.get(r["key"], 0) + 1
                sb = r["starvation_bound_cycles"]
                assert sb is not None, (trial, step, r)
                stated[r["key"]] = max(stated.get(r["key"], 0), sb)
                if run[r["key"]] > stated[r["key"]] - 1:
                    violations.append((trial, step, r["key"], run[r["key"]],
                                       stated[r["key"]], plan["feasible"]))
                if plan["feasible"]:
                    assert sb == r["bound_cycles"], r
            else:
                run.pop(r["key"], None)
                stated.pop(r["key"], None)
    assert violations == [], violations[:10]
    assert overloaded > 0


def test_the_envelope_slot_is_derived_from_the_real_ledger():
    """FINDING 6. When the 24 h envelope was spent the forward run assumed a
    FULL envelope from the next cycle on, so the promised slot was not
    derived from the real spend; and the reason said the budget 'went to
    higher-ranked competitions' at rank 0, when nothing was fetched at all.
    The forward run now rolls the actual ledger forward, and the reason
    names the binding constraint."""
    now = SAT
    comps = [cov.competition(key="k%d" % i, family="football", listed=True,
                             active=True, events_in_horizon=5,
                             next_start=now + H) for i in range(3)]
    # the whole envelope spent 1 h ago: it frees when that spend leaves the
    # rolling day, the first cycle at or after now - 1 h + the window
    ledger = [(now - H, cov.DAILY_CREDIT_ENVELOPE)]
    plan = cov.plan(comps, now=now, cycle_s=CYCLE,
                    spent_24h=cov.DAILY_CREDIT_ENVELOPE, spend_ledger=ledger)
    assert plan["fetch_order"] == []
    window = cov.envelope_window_s(CYCLE)
    frees = now - H + window
    slot = now + math.ceil((frees - now) / CYCLE) * CYCLE
    assert slot - now < 86400.0
    for rank, r in enumerate(sorted(plan["receipts"],
                                    key=lambda x: x["priority_rank"])):
        assert r["planned"] == cov.DEFERRED_TO_SLOT
        assert r["budget_binding"] == cov.BINDING_DAILY_ENVELOPE
        assert "higher-ranked" not in r["why"], r["why"]
        assert "24 h envelope" in r["why"]
        # the rule run forward on the real ledger: the first slot is when
        # the spend leaves the window, and nothing is promised before it
        assert r["next_slot_at"] >= slot, (r, slot)
    assert min(r["next_slot_at"] for r in plan["receipts"]) == slot
    # a call budget spent on higher-ranked competitions says so
    full = cov.plan(comps + [cov.competition(
        key="k%d" % i, family="football", listed=True, active=True,
        events_in_horizon=5, next_start=now + H) for i in range(3, 6)],
        now=now, cycle_s=CYCLE)
    for r in full["receipts"]:
        if r["planned"] == cov.DEFERRED_TO_SLOT:
            assert r["budget_binding"] == cov.BINDING_CALLS
            assert "higher-ranked" in r["why"]


def test_the_candidate_slot_uses_the_stated_starvation_bound():
    """FINDING 7. A provider event the evaluation bound deferred was
    promised a slot from the competition's STALENESS bound even when the
    demand was overloaded and only the (larger) starvation bound holds."""
    rec = {"bound_cycles": 1, "starvation_bound_cycles": 3}
    assert cov.candidate_slot(cycle_at=SAT, cycle_s=CYCLE, receipt=rec,
                              position=0, share=10) == SAT + 3 * CYCLE
    assert cov.candidate_slot(cycle_at=SAT, cycle_s=CYCLE, receipt=rec,
                              position=25, share=10) == SAT + 9 * CYCLE
    feasible = {"bound_cycles": 2, "starvation_bound_cycles": 2}
    assert cov.candidate_slot(cycle_at=SAT, cycle_s=CYCLE, receipt=feasible,
                              position=0, share=4) == SAT + 2 * CYCLE

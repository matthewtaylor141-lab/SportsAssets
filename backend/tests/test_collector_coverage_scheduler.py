"""THE COLLECTOR'S COVERAGE SCHEDULER (P0 incident, owner 2026-10-04).

"A production trading system cannot arbitrarily lose an entire major sport
because four other sports consumed a collector budget." The four-key count
left NCAAF unfetched in 137 of the 153 production cycles with a venue `cfb`
event in the next 24 h (research-sql incident_collector_cap_a, run
37233454453). These tests prove the replacement, `collector_coverage.plan`:

  * a Saturday board (~100 cfb events plus NFL, MLB and soccer) serves NCAAF
    and the NFL in EVERY cycle they have an event in the horizon;
  * no enabled competition waits longer than its stated bound;
  * spend never exceeds the envelope -- per cycle and over the day -- at the
    conservative per-fetch estimate and at a measured lower cost;
  * every competition not fetched has a receipt, and a DEFERRED_TO_SLOT
    receipt's slot is kept;
  * the evaluation bound (MAX_PER_CYCLE) is shared by reserve, never by order.

Pure: no database, no network, no clock.
"""
from __future__ import annotations

import random

import pytest

from sportsassets import collector_coverage as cov

CYCLE = 900.0
H = 3600.0
#: 2026-10-03T08:00:00Z, Saturday morning US Eastern (04:00 ET)
SAT = 1791014400.0

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


#: THE SATURDAY BOARD, shaped on production's 2026-10-03 ET board (research-sql
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
    """What the venue board query reports at `now`: events in the horizon
    and the next start (any event started in the last 6 h counts)."""
    alive = [s for s in starts if s > now - cov.HORIZON_BEHIND_S]
    inh = [s for s in alive if s <= now + cov.HORIZON_AHEAD_S]
    return len(inh), (min(alive) if alive else None)


def _board_at(board, now, last, *, held=None):
    out = []
    for key, fam, tok, listed, feed, starts in board:
        ev, nxt = _horizon(starts, now)
        out.append(cov.competition(
            key=key, family=fam, token=tok, listed=listed,
            active=True if listed else None, confirmed=(key == MLB),
            held=(held or {}).get(key, 0), events_in_horizon=ev,
            next_start=nxt, feed_covered=feed,
            last_served_at=last.get(key)))
    return out


def _run_day(board, *, cost, cycles=96, start=SAT, held=None,
             envelope=cov.DAILY_CREDIT_ENVELOPE):
    """Drive `cycles` consecutive cycles: each plan sees the last-served
    instants of the plans before it and the 24 h ledger of what they spent."""
    last, ledger, history = {}, [], []
    for k in range(cycles):
        now = start + k * CYCLE
        # the half-open day (now - 24 h, now]: 96 cycle starts at the cadence
        spent = sum(c for t, c in ledger if now - t < 86400.0)
        plan = cov.plan(_board_at(board, now, last, held=held), now=now,
                        cycle_s=CYCLE, daily_envelope=envelope,
                        spent_24h=spent, cost_per_fetch=cost)
        for key, _ in plan["fetch_order"]:
            cov.settle(plan, key, ok=True, at=now + 1.0, credits=cost,
                       basis=cov.COST_MEASURED)
            last[key] = now
            ledger.append((now, cost))
        history.append((now, plan))
    return history


def _fetched(plan):
    return {k for k, _ in plan["fetch_order"]}


def _receipt(plan, key):
    return next(r for r in plan["receipts"] if r["key"] == key)


# ═════════════════════════════════════════════════════════════════════
# 1 · THE SATURDAY BOARD
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("cost,basis", [
    (cov.CREDITS_PER_FETCH_ESTIMATE, "the conservative estimate (4 a cycle)"),
    (3.0, "a measured 3 credits (h2h x three regions)"),
    (1.0, "a measured 1 credit (the bookmakers filter)")])
def test_the_saturday_board_serves_ncaaf_and_nfl_every_cycle_they_have_events(
        cost, basis):
    history = _run_day(SATURDAY, cost=cost, cycles=192)   # Sat 08Z .. Mon 08Z
    cfb_cycles = nfl_cycles = 0
    for now, plan in history:
        got = _fetched(plan)
        if _horizon(SATURDAY[0][5], now)[0] > 0:
            cfb_cycles += 1
            assert NCAAF in got, (basis, now, _receipt(plan, NCAAF))
        if _horizon(SATURDAY[1][5], now)[0] > 0:
            nfl_cycles += 1
            assert NFL in got, (basis, now, _receipt(plan, NFL))
    # the test exercised both, for most of the two days
    assert cfb_cycles > 80 and nfl_cycles > 80, (cfb_cycles, nfl_cycles)


def test_no_enabled_competition_waits_longer_than_its_stated_bound():
    for cost in (cov.CREDITS_PER_FETCH_ESTIMATE, 3.0):
        history = _run_day(SATURDAY, cost=cost, cycles=192)
        assert all(p["feasible"] for _, p in history), cost
        waiting: dict = {}
        for now, plan in history:
            for r in plan["receipts"]:
                in_demand = r["planned"] in (cov.SCHEDULED,
                                             cov.DEFERRED_TO_SLOT)
                if not in_demand:
                    waiting.pop(r["key"], None)
                    continue
                if r["planned"] == cov.SCHEDULED:
                    waiting.pop(r["key"], None)
                    continue
                waiting[r["key"]] = waiting.get(r["key"], 0) + 1
                # a deferral never outlasts the bound: bound_cycles - 1
                # consecutive deferrals at most
                assert waiting[r["key"]] <= r["bound_cycles"] - 1, (
                    cost, now, r)


@pytest.mark.parametrize("cost", [cov.CREDITS_PER_FETCH_ESTIMATE, 3.0, 1.0])
def test_spend_never_exceeds_the_envelope(cost):
    history = _run_day(SATURDAY, cost=cost, cycles=288)   # three days
    allowance = cov.per_cycle_allowance(cov.DAILY_CREDIT_ENVELOPE, CYCLE)
    spends = []
    for now, plan in history:
        s = cov.spend(plan)
        assert s <= allowance + 1e-9, (now, s)
        assert s == plan["envelope"]["planned_spend"]
        spends.append((now, s))
    for i in range(len(spends)):
        window = sum(s for t, s in spends[i:] if t - spends[i][0] < 86400.0)
        assert window <= cov.DAILY_CREDIT_ENVELOPE + 1e-9, (i, window)


def test_a_cheaper_measured_request_buys_coverage_not_spend():
    """The same board at a measured 3 credits serves every competition in the
    horizon every cycle, and spends LESS than the estimated 4-fetch day."""
    est = _run_day(SATURDAY, cost=cov.CREDITS_PER_FETCH_ESTIMATE, cycles=96)
    cheap = _run_day(SATURDAY, cost=3.0, cycles=96)
    est_spend = sum(cov.spend(p) for _, p in est)
    cheap_spend = sum(cov.spend(p) for _, p in cheap)
    assert cheap_spend < est_spend <= cov.DAILY_CREDIT_ENVELOPE
    for now, plan in cheap:
        for r in plan["receipts"]:
            assert r["planned"] != cov.DEFERRED_TO_SLOT, (now, r)


# ═════════════════════════════════════════════════════════════════════
# 2 · RECEIPTS, NEVER SILENT DROPS
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


def test_a_deferral_carries_a_slot_and_the_slot_is_kept():
    history = _run_day(SATURDAY, cost=cov.CREDITS_PER_FETCH_ESTIMATE,
                       cycles=96)
    promised = 0
    by_time = {now: plan for now, plan in history}
    for now, plan in history:
        for r in plan["receipts"]:
            if r["planned"] != cov.DEFERRED_TO_SLOT:
                continue
            promised += 1
            slot = r["next_slot_at"]
            assert slot is not None and slot > now
            later = by_time.get(slot)
            if later is None:
                continue                       # past the simulated day
            window = [(t, p) for t, p in history if now < t <= slot]
            # The promise is made on the board as it stands. A competition
            # whose last event leaves the horizon before its slot is SKIPPED
            # by name then (nothing left to fetch), not silently dropped.
            if any(_receipt(p, r["key"])["planned"]
                   == cov.SKIPPED_NO_VENUE_EVENT_IN_HORIZON for _, p in window):
                continue
            # otherwise it is fetched AT OR BEFORE the promised cycle
            fetched_by = [t for t, p in window if r["key"] in _fetched(p)]
            assert fetched_by, (now, r)
    assert promised > 0


def test_no_venue_event_in_the_horizon_costs_nothing_and_says_when_it_will():
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


def test_an_unread_catalogue_confirms_nothing_but_the_confirmed_and_held():
    comps = [cov.competition(key=MLB, family="baseball", confirmed=True,
                             listed=None, events_in_horizon=3),
             cov.competition(key=NCAAF, family="football", listed=None,
                             events_in_horizon=50),
             cov.competition(key=UNL, family="soccer", listed=None, held=2,
                             events_in_horizon=0)]
    by = {r["key"]: r for r in cov.plan(comps, now=SAT,
                                        cycle_s=CYCLE)["receipts"]}
    assert by[MLB]["planned"] == cov.SCHEDULED
    assert by[NCAAF]["planned"] == cov.PROVIDER_CATALOGUE_UNREAD
    # held is served unconditionally -- even with no event in the horizon
    assert by[UNL]["planned"] == cov.SCHEDULED


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


# ═════════════════════════════════════════════════════════════════════
# 3 · PRIORITY, THE ENVELOPE AND HELD POSITIONS
# ═════════════════════════════════════════════════════════════════════

def test_held_positions_first_and_never_beyond_the_envelope():
    comps = [cov.competition(key="k%d" % i, family="soccer", listed=True,
                             active=True, events_in_horizon=5,
                             next_start=SAT + H)
             for i in range(6)]
    comps.append(cov.competition(key=UNL, family="soccer", listed=True,
                                 active=True, held=1, events_in_horizon=0,
                                 last_served_at=SAT - CYCLE))
    plan = cov.plan(comps, now=SAT, cycle_s=CYCLE)
    assert plan["fetch_order"][0][0] == UNL
    # the envelope nearly spent: held may use the remainder beyond the
    # per-cycle allowance, never beyond the 24 h envelope
    tight = cov.plan(comps, now=SAT, cycle_s=CYCLE,
                     spent_24h=cov.DAILY_CREDIT_ENVELOPE - 20.0)
    assert [k for k, _ in tight["fetch_order"]] == [UNL]
    assert tight["envelope"]["planned_spend"] == 20.0
    spent = cov.plan(comps, now=SAT, cycle_s=CYCLE,
                     spent_24h=cov.DAILY_CREDIT_ENVELOPE)
    assert spent["fetch_order"] == []
    for r in spent["receipts"]:
        assert r["planned"] == cov.DEFERRED_TO_SLOT and r["next_slot_at"]


def test_metered_dependent_before_feed_covered_and_feed_covered_never_starves():
    comps = []
    for i in range(4):
        comps.append(cov.competition(key="football%d" % i, family="football",
                                     listed=True, active=True,
                                     events_in_horizon=10,
                                     next_start=SAT + 5 * H))
    comps.append(cov.competition(key=UNL, family="soccer", listed=True,
                                 active=True, events_in_horizon=10,
                                 next_start=SAT + H, feed_covered=True))
    # never served: everyone is equally overdue; the metered-dependent go
    # first although the soccer event starts sooner
    first = cov.plan(comps, now=SAT, cycle_s=CYCLE)
    assert UNL not in _fetched(first)
    # and the feed-covered competition is still served within the run
    # (overload: five competitions, four slots) -- its deadline outranks
    history_last = {k: SAT for k in _fetched(first)}
    served = False
    for step in range(1, 4):
        now = SAT + step * CYCLE
        cs = [dict(c, last_served_at=history_last.get(c["key"]))
              for c in comps]
        plan = cov.plan(cs, now=now, cycle_s=CYCLE)
        for k in _fetched(plan):
            history_last[k] = now
        if UNL in _fetched(plan):
            served = True
            break
    assert served, "a feed-covered competition must not starve"
    assert first["feasible"] is False      # stated, not hidden


def test_the_plan_is_deterministic():
    now = SAT + 14 * H
    a = cov.plan(_board_at(SATURDAY, now, {}), now=now, cycle_s=CYCLE)
    b = cov.plan(list(reversed(_board_at(SATURDAY, now, {}))), now=now,
                 cycle_s=CYCLE)
    assert a["fetch_order"] == b["fetch_order"]


def test_random_feasible_boards_keep_every_bound():
    rng = random.Random(20261004)
    for trial in range(25):
        board = []
        for i in range(rng.randint(3, 12)):
            fam = rng.choice(("soccer", "football", "baseball"))
            feed = fam != "football" and rng.random() < 0.6
            first = SAT + rng.uniform(-3, 40) * H
            board.append(("c%d" % i, fam, "t%d" % i, rng.random() > 0.15,
                          feed, _starts(first, rng.randint(1, 30),
                                        rng.uniform(0, 30))))
        history = _run_day(board, cost=rng.choice((1.0, 3.0, 20.0)),
                           cycles=96)
        waiting: dict = {}
        for now, plan in history:
            if not plan["feasible"]:
                continue
            for r in plan["receipts"]:
                if r["planned"] == cov.DEFERRED_TO_SLOT:
                    waiting[r["key"]] = waiting.get(r["key"], 0) + 1
                    assert waiting[r["key"]] <= r["bound_cycles"] - 1, (
                        trial, now, r)
                else:
                    waiting.pop(r["key"], None)


# ═════════════════════════════════════════════════════════════════════
# 4 · THE EVALUATION BOUND, SHARED BY RESERVE
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


def test_a_deferred_candidate_goes_first_in_its_next_fetch():
    deferred = {"e2": SAT - 900.0}
    events = ["e1", "e2", "e3"]
    order = sorted(events, key=lambda e: cov.candidate_order_key(
        e, commence_epoch=SAT + 20 * H, now=SAT, deferred_since=deferred,
        freshness_key=(events.index(e),)))
    assert order[0] == "e2"
    # and urgency (time to event) before the freshness order
    soon = sorted(events, key=lambda e: cov.candidate_order_key(
        e, commence_epoch=(SAT + H if e == "e3" else SAT + 20 * H), now=SAT,
        deferred_since={}, freshness_key=(events.index(e),)))
    assert soon == ["e3", "e1", "e2"]


def test_the_envelope_change_is_stated_with_its_arithmetic():
    chg = cov.ENVELOPE_CHANGE
    assert chg["daily_envelope_credits"] == cov.DAILY_CREDIT_ENVELOPE == 7680.0
    assert cov.per_cycle_allowance(cov.DAILY_CREDIT_ENVELOPE, CYCLE) == 80.0
    # at the conservative estimate the default buys exactly today's four
    assert 80.0 / cov.CREDITS_PER_FETCH_ESTIMATE == 4
    for k in ("arithmetic", "what_changed", "net_change_in_spend"):
        assert chg[k]

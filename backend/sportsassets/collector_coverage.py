"""THE COLLECTOR'S COVERAGE SCHEDULER (pure: no I/O, no clock, no database).

THE INCIDENT (P0, owner 2026-10-04). "A production trading system cannot
arbitrarily lose an entire major sport because four other sports consumed a
collector budget." The red team found NCAAF `budget_dropped` under the
collector's four-key metered budget, and the Saturday 2026-10-03 NCAAF slate
(about 107 venue `cfb` events) never reached the funnel. Measured over the
last 7 days of production (research-sql incident_collector_cap_a, run
37233454453, 458 scheduled cycles, ext_candidate_outcomes + us_premap):

    competition  cycles with a venue event   fetched   NOT fetched
                 in the next 24 h
    cfb  NCAAF                 153              16         137
    nfl  NFL                   131              62          69
    uslc USL Championship      194               0         194
    mlb / unl / brb       218 / 209 / 110   all / all / 103    0 / 0 / 7

  and in the SAME cycles the four slots went, 910 times out of 1,518
  metered fetches, to a competition with NO venue event in the next 24 h
  (brb 333, unl 242, mlb 240, uwcl 80, cfb 15) -- because `select_sports`
  ranked by the venue board's event count over its whole unbounded future,
  gave MLB a permanent slot, and truncated the ranked list at four.

WHICH CAUSE IT WAS. Not the size of the budget: 910 of the 1,518 calls went
to competitions with nothing to trade in the next 24 h. It was FIRST-COME
TRUNCATION of a FIXED RANKING with NO ROTATION (the same competitions lost
every cycle) and no notion of WHEN a competition's events are. It was not a
pagination budget, a time budget or an API quota response: the account
reported 14,559,615 credits remaining (heartbeat 2026-10-04T20:28Z).
MAX_PER_CYCLE (40 evaluations) truncated NOTHING in the same 7 days: 0
DEFERRED rows of 23,602 judged.

WHAT REPLACES THE TRUNCATION -- AND WHAT DOES NOT CHANGE.

  THE DECLARED BUDGET STAYS. At most MAX_METERED_CALLS_PER_CYCLE (four, the
  value MAX_METERED_SPORTS_PER_CYCLE always had) metered provider calls a
  cycle, and at most the per-cycle share of the DAILY CREDIT ENVELOPE (today's
  stated spend, 7,680 credits a day) -- whichever binds first. Raising either
  is an owner resource decision (incident RC8: "Interim, owner resource
  decision: raise MAX_METERED_SPORTS_PER_CYCLE to about 8-12"); nothing here
  makes it. Every call's measured cost is recorded.

  WHO GETS THE CALLS is a deterministic, receipted schedule:

  * A competition with no venue event in the decision horizon (next 24 h, or
    started in the last 6 h) costs nothing this cycle
    (SKIPPED_NO_VENUE_EVENT_IN_HORIZON, with the instant it enters it) --
    A HELD POSITION'S COMPETITION INCLUDED. Found by the adversarial review:
    a held competition used to pass whatever its horizon, and "held" is any
    paper fill with a positive net quantity and no settlement row, so
    positions on FINISHED games awaiting settlement took a metered call
    every cycle for a response that no longer lists them (the Saturday
    board with three of them: NCAAF served in 51 of 96 cycles, 181 of 384
    calls on competitions with no venue event in the horizon).
  * Each competition in the horizon carries a STALENESS BOUND in cycles: 1
    for a held position's competition and for a competition whose
    decision-time probability depends on the metered fetch (football today);
    2 for a competition whose Pinnacle price the subscribed feed already
    carries in this process (its discovery seeds are refreshed through the
    provider's events endpoint, measured and counted, so the metered slot no
    longer gates its valuations).
  * THE DEADLINE is counted from the later of the cycle the competition was
    last served and the cycle before it ENTERED DEMAND (`demand_since`, fed
    back from the previous plan's receipts): a competition entering the
    horizon, or re-entering it days after it was last served, may wait
    exactly its own bound -- never "due now" whatever its bound, and never
    "long overdue" on a service from a previous run (both found by the
    adversarial review: they let competitions wait one cycle past a stated
    bound in plans declared feasible).
  * ORDER, a total order (same inputs, same plan):
      1. EARLIEST DEADLINE -- how far past its bound a competition would be
         if skipped now. This is the starvation protection, so nothing ranks
         above it: a skipped competition's lateness grows every cycle until
         it outranks everything that is not later still. Among competitions
         ALREADY PAST their bound, the exact deadline decides (earliest
         first), so a competition served after another fell due can never be
         served again before it;
      2. a held position's competition, among the equally due;
      3. LIVE / IN PLAY (an event started within the horizon's 6 h tail --
         which can include a game that has already finished: the venue
         board states no end time, so this is a tie-break, never a bound)
         before pre-match;
      4. metered-dependent before feed-covered;
      5. the most IMMINENT venue start;
      6. the largest SLATE (events in the horizon);
      7. the provider key.
  * Every competition not fetched gets a receipt; DEFERRED_TO_SLOT carries
    the cycle in which the same deterministic rule fetches it, found by
    running the rule forward (board, costs and cadence held constant).

THE BOUND, STATED PER PLAN AND PROVED (tests/test_collector_coverage_
scheduler.py, including the adversarial review's changing-board harness).
With uniform per-call cost and K calls a cycle (K = the smaller of the call
budget and what the per-cycle credit allowance buys), each plan states the
bound that holds for its demand AS IT STANDS:

  * FEASIBLE -- sum over the competitions in demand of 1 / bound_cycles <= K
    (and the same in credits), NOTHING already past its bound, every
    competition due this cycle fits in this cycle's K, and the 24 h
    envelope does not cut this cycle below K. Then every competition due is
    served now, and none waits more than `bound_cycles - 1` cycles in a row.
    With bounds of 1 and 2 and earliest-deadline-first, n1 bound-1 and n2
    bound-2 competitions leave at most max(n1, 2*n1 + n2 - K) due the next
    cycle, which is <= K exactly when n1 + n2/2 <= K.
  * OVERLOADED (anything else) -- stated on the plan (`feasible: False`, with
    `feasibility` naming which condition failed), never hidden -- no
    competition waits more than B + ceil((N - 1) / K) cycles, N the
    competitions in demand and B = BOUND_CYCLES_MAX (two), the largest bound
    a competition can carry: its own bound may relax mid-run (a held
    position closes), so the proof counts from the cycle it falls due under
    the LARGEST bound. From that cycle, any competition served is
    re-deadlined at least a cycle after it and can never be served again
    before it (exact deadlines order those past their bound), a competition
    entering demand is deadlined after it, and each cycle serves K, so at
    most N - 1 others can be served first.
  * When the 24 h envelope cuts a cycle below K, no starvation bound is
    stated (None, `feasibility` says why): the envelope, not the rule, then
    decides, and the slot of every deferral is the rule run forward on the
    ACTUAL rolling ledger.
"""
from __future__ import annotations

import math

VERSION = "COLLECTOR_COVERAGE_SCHEDULER_V1"

# ── receipts (per cycle, per competition) ────────────────────────────
SCHEDULED = "SCHEDULED"
FETCHED = "FETCHED"
FETCH_FAILED = "FETCH_FAILED"
DEFERRED_TO_SLOT = "DEFERRED_TO_SLOT"
SKIPPED_NO_VENUE_EVENT_IN_HORIZON = "SKIPPED_NO_VENUE_EVENT_IN_HORIZON"
PROVIDER_DOES_NOT_LIST = "PROVIDER_DOES_NOT_LIST"
PROVIDER_LISTS_INACTIVE = "PROVIDER_LISTS_INACTIVE"
#: the provider's unmetered catalogue could not be read: unread is not
#: "does not list", and nothing unconfirmed is spent on
PROVIDER_CATALOGUE_UNREAD = "PROVIDER_CATALOGUE_UNREAD"
#: the forward run of the rule found no slot inside SIMULATION_CYCLES -- the
#: budget cannot carry the demand (a single call costing more than a cycle's
#: credit allowance, or a rolling ledger that frees no call within the
#: simulated day). Named rather than given an invented slot. A spent 24 h
#: envelope whose spend leaves the rolling day inside the simulated day is
#: NOT this: it is DEFERRED_TO_SLOT at the cycle the ACTUAL ledger frees a
#: call, with `budget_binding` DAILY_CREDIT_ENVELOPE_EXHAUSTED.
DEFERRED_NO_SLOT_WITHIN_ENVELOPE = "DEFERRED_NO_SLOT_WITHIN_ENVELOPE"

PLANNED_RECEIPTS = (SCHEDULED, DEFERRED_TO_SLOT,
                    SKIPPED_NO_VENUE_EVENT_IN_HORIZON, PROVIDER_DOES_NOT_LIST,
                    PROVIDER_LISTS_INACTIVE, PROVIDER_CATALOGUE_UNREAD,
                    DEFERRED_NO_SLOT_WITHIN_ENVELOPE)
FINAL_RECEIPTS = tuple(r for r in PLANNED_RECEIPTS if r != SCHEDULED) + (
    FETCHED, FETCH_FAILED)
#: The receipts that are a BUDGET DROP: in the horizon, enabled, and not
#: fetched because the cycle's budget could not carry it (`budget_binding`
#: names which limit: the calls, the credit allowance or the 24 h envelope).
BUDGET_DROPPED = (DEFERRED_TO_SLOT, DEFERRED_NO_SLOT_WITHIN_ENVELOPE)

#: One row per cycle: the declared budget and what the cycle used of it.
CYCLE_BUDGET = "CYCLE_BUDGET"

#: WHICH LIMIT DEFERRED A COMPETITION (receipt `budget_binding`), so a
#: deferral's reason is never "went to higher-ranked competitions" when
#: nothing ranked above it was fetched:
#:   the cycle's metered calls all went to competitions ranked above it
BINDING_CALLS = "CALL_BUDGET_USED_BY_HIGHER_RANKED"
#:   what is left of the cycle's credit allowance cannot buy its call
BINDING_CYCLE_CREDITS = "CYCLE_CREDIT_ALLOWANCE_EXHAUSTED"
#:   what is left of the rolling 24 h envelope cannot buy its call
BINDING_DAILY_ENVELOPE = "DAILY_CREDIT_ENVELOPE_EXHAUSTED"

#: WHY A PLAN IS (NOT) FEASIBLE (plan `feasibility`).
FEASIBLE = "FEASIBLE"
F_DEMAND = "DEMAND_EXCEEDS_WHAT_THE_BUDGET_CARRIES_WITHIN_THE_BOUNDS"
F_BACKLOG = "A_COMPETITION_IS_ALREADY_PAST_ITS_BOUND"
F_DUE = "MORE_COMPETITIONS_DUE_THIS_CYCLE_THAN_ITS_CALLS"
F_ENVELOPE = "THE_24H_ENVELOPE_CUTS_THIS_CYCLE_BELOW_ITS_CALLS"

#: The receipts of a competition IN THE SCHEDULE'S ROTATION: enabled and
#: listed by the provider, whether fetched this cycle, deferred to a slot, or
#: waiting for its first venue event to enter the horizon. Derek's coverage
#: census reads them as the collector's mandate, so a scheduling decision is
#: never filed as a scope decision.
ROTATION_RECEIPTS = (SCHEDULED, FETCHED, FETCH_FAILED, DEFERRED_TO_SLOT,
                     DEFERRED_NO_SLOT_WITHIN_ENVELOPE,
                     SKIPPED_NO_VENUE_EVENT_IN_HORIZON)

# ── per-candidate receipt (MAX_PER_CYCLE) ────────────────────────────
#: a provider event the cycle's evaluation bound did not reach. Never judged;
#: carries the slot in which its competition is next fetched, and it goes
#: FIRST in that fetch (oldest deferral first), so the slot is real.
CANDIDATE_DEFERRED_TO_SLOT = "CANDIDATE_DEFERRED_TO_SLOT"

# ── the decision horizon ──────────────────────────────────────────────
#: A venue event starting within this many seconds puts its competition in
#: the horizon. 24 h: on the 7-day production board every mapped competition
#: with an event inside 24 h was also on the board well before it (cfb's
#: Saturday slate, 107 events, was listed by Friday), so a competition enters
#: the horizon ~96 cycles before its first event, and the 30 s freshness rule
#: makes a valuation taken days ahead worthless at entry anyway.
HORIZON_AHEAD_S = 24 * 3600.0
#: ... and an event that STARTED up to this long ago is still in it (in-play),
#: the same window the venue board query uses (`game_start > now() - 6 h`).
HORIZON_BEHIND_S = 6 * 3600.0
#: A provider event starting within this long is URGENT in the per-fetch
#: evaluation order (after events already in play).
SOON_S = 6 * 3600.0

#: STALENESS BOUNDS, in cycles. See the module docstring.
BOUND_CYCLES_HELD = 1
BOUND_CYCLES_METERED_DEPENDENT = 1
BOUND_CYCLES_FEED_COVERED = 2
#: The largest staleness bound any competition can carry. The overload bound
#: is stated with it: a competition's OWN bound can relax in the middle of a
#: run of deferrals (its held position closes, or the feed starts carrying
#: its family), which moves its deadline one cycle later -- the adversarial
#: harness found a competition that fell due under a held bound of one and
#: then waited one cycle past a starvation bound stated with that one.
BOUND_CYCLES_MAX = max(BOUND_CYCLES_HELD, BOUND_CYCLES_METERED_DEPENDENT,
                       BOUND_CYCLES_FEED_COVERED)

#: A cycle that starts this fraction of a cycle early (cadence jitter) is
#: still the next cycle: deadlines are counted in whole cycles, and the
#: rolling day of the envelope ends this much early (`envelope_window_s`).
DEADLINE_TOLERANCE = 0.1

#: How far forward the rule is run to find a deferred competition's slot: TWO
#: days of cycles at the nominal cadence -- one whole rolling day for a spent
#: envelope to free (the ledger is rolled forward, never assumed full) and
#: one more for the queue it built to drain. (One day, the first version,
#: could not find the slot of the fifth competition behind a spent envelope.)
SIMULATION_CYCLES = 192

# ── the declared budget ───────────────────────────────────────────────
#: METERED PROVIDER CALLS A CYCLE, AT MOST. The value
#: ext_pinnacle_loop.MAX_METERED_SPORTS_PER_CYCLE has carried since cand22
#: (METERED_BUDGET_CHANGE "after": four keys). UNCHANGED: what changed is who
#: gets the four calls. Every metered request counts against it -- a
#: scheduled fetch, a mid-cycle re-fetch, and an events-endpoint discovery
#: refresh whose cost is not yet measured as zero.
MAX_METERED_CALLS_PER_CYCLE = 4
#: TODAY'S SPEND IN CREDITS, AS THE CODE STATES IT. METERED_BUDGET_CHANGE
#: "after": four keys, ~80 credits a cycle, ~7.7k a day at the 900 s cadence.
#: 80 credits x 96 cycles = 7,680.
DAILY_CREDIT_ENVELOPE = 7680.0
#: The per-call cost charged until the provider's own per-request usage
#: header has been read: the upper end of METERED_BUDGET_CHANGE's "18-21
#: credits each". With it the envelope buys exactly the four calls a cycle; a
#: MEASURED lower cost spends less, never more calls.
CREDITS_PER_FETCH_ESTIMATE = 20.0

COST_MEASURED = "MEASURED_PROVIDER_USAGE_HEADER"
COST_ESTIMATED = "ESTIMATED_UPPER_BOUND_UNTIL_MEASURED"

#: Kinds of metered call a cycle can make, all counted against the budget.
CALL_ODDS = "ODDS_FETCH"
CALL_REFETCH = "ODDS_REFETCH"
CALL_EVENTS = "EVENTS_DISCOVERY"

ENVELOPE_CHANGE = {
    "daily_envelope_credits": DAILY_CREDIT_ENVELOPE,
    "max_metered_calls_per_cycle": MAX_METERED_CALLS_PER_CYCLE,
    "arithmetic": ("80 credits a cycle (METERED_BUDGET_CHANGE 'after': four "
                   "keys at ~20) x 96 cycles a day (86,400 s / 900 s) = "
                   "7,680 credits a day: the envelope equals today's stated "
                   "spend, and the call budget stays four a cycle"),
    "per_cycle_allowance": ("envelope x cycle_s / 86,400 = 80 credits at the "
                            "900 s cadence; a held position's competition may "
                            "use the rolling-24 h remainder beyond it, never "
                            "beyond the envelope or the call budget"),
    "what_changed": ("not the budget: WHO gets the four calls. First-come "
                     "truncation of a fixed ranking (MLB always, then venue "
                     "events over an unbounded future) is replaced by an "
                     "earliest-deadline schedule over the competitions with "
                     "a venue event in the next 24 h, with a stated "
                     "staleness bound per competition and a receipt for "
                     "every competition not fetched"),
    "net_change_in_spend": ("none: at most four calls and 80 credits a "
                            "cycle, as before. A measured per-call cost below "
                            "the 20-credit estimate spends less. 910 of 1,518 "
                            "fetches in the last 7 days went to competitions "
                            "with no venue event in the next 24 h; those "
                            "calls now go to competitions with events"),
    "measured_key_usage": ("the key's cumulative usage rose 1,386 credits "
                           "between the 20:28Z and 20:48Z cycle heartbeats on "
                           "2026-10-04 (440,385 -> 441,771) with four "
                           "collector fetches in between -- far above the "
                           "collector's own stated ~80 a cycle, so other "
                           "consumers share the key. The receipts now record "
                           "each collector request's own measured cost"),
}


def per_cycle_allowance(daily_envelope: float, cycle_s: float) -> float:
    """The daily envelope pro-rated to one cycle of the cadence."""
    return float(daily_envelope) * float(cycle_s) / 86400.0


def envelope_window_s(cycle_s: float) -> float:
    """THE ROLLING DAY THE ENVELOPE IS COUNTED OVER, on the CYCLE clock.

    Every spend is stamped with the instant of the cycle that made it (never
    the instant the fetch finished -- the adversarial review's finding: a
    fetch stamped mid-cycle stayed in the day one cycle longer than the
    cycle that made it, so the day held 96 cycles of spend at the 900 s
    cadence and about once a day a cycle saw the envelope spent and fetched
    nothing). The day is half-open, (now - window, now], and ends
    DEADLINE_TOLERANCE of a cycle early, the same tolerance the deadlines
    use: start-to-start is 900 s plus noise of milliseconds either way
    (`next_cycle_delay`), so the cycle exactly one day ago must fall out of
    the day even when this one started a few milliseconds early. At the
    900 s cadence the day then holds the 95 previous cycles plus this one:
    96 x the per-cycle allowance is exactly the envelope."""
    return 86400.0 - DEADLINE_TOLERANCE * float(cycle_s)


def spent_in_window(ledger, *, now: float, cycle_s: float) -> float:
    """Credits in the rolling day ending at `now` (see envelope_window_s).
    `ledger`: (cycle instant, credits) pairs; an entry after `now` has not
    been spent yet as far as this cycle is concerned."""
    w = envelope_window_s(cycle_s)
    return round(sum(float(c) for a, c in (ledger or ())
                     if 0.0 <= float(now) - float(a) < w), 6)


def _num(v):
    try:
        if v is None or isinstance(v, bool):
            return None
        out = float(v)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def competition(*, key, family, token=None, listed=None, active=None,
                confirmed=False, held=0, events_in_horizon=None,
                next_start=None, board_events=None, feed_covered=False,
                last_served_at=None, demand_since=None, cost=None,
                start_unknown=0) -> dict:
    """One enabled competition's facts, normalised. Pure.

    `listed` / `active`: the provider's unmetered catalogue (None = unread).
    `confirmed`: a key this lane has always valued (SPORTS_CONFIRMED), run
    even when the catalogue is unread, as before.
    `events_in_horizon` / `next_start`: from the venue's own catalogue; None
    is UNKNOWN and never treated as zero (an unread horizon is not an empty
    one: the competition stays in demand). `next_start` is the earliest venue
    start not older than the horizon's 6 h tail, so `next_start <= now`
    means an event is in play (or started within the tail).
    `start_unknown`: venue events of this competition, refreshed by the venue
    sweep within the last day, whose market states NO start time
    (`us_premap.game_start` NULL: the venue sent no gameStartTime). A listing
    with no stated start is not evidence that nothing starts in the horizon,
    so such a competition is never SKIPPED on a zero count -- the unread-
    horizon rule, applied per event.
    `feed_covered`: the subscribed feed carries this family's Pinnacle price
    in this process now.
    `last_served_at`: the cycle instant the competition was last given its
    metered call.
    `demand_since`: the cycle instant its CURRENT RUN IN DEMAND began (the
    previous plan's receipt carries it; the caller feeds it back, and only
    for a competition that was in demand in that plan). None = it enters
    demand now. Its staleness is counted from the later of `last_served_at`
    and the cycle before `demand_since`, so a never-served competition AGES
    from the cycle it entered demand, and a competition re-entering the
    horizon is not treated as overdue on a service from a previous run."""
    return {"key": str(key), "family": str(family or ""),
            "token": None if token is None else str(token),
            "listed": listed, "active": active, "confirmed": bool(confirmed),
            "held": int(held or 0),
            "events_in_horizon": (None if events_in_horizon is None
                                  else int(events_in_horizon)),
            "next_start": _num(next_start),
            "board_events": (None if board_events is None
                             else int(board_events)),
            "feed_covered": bool(feed_covered),
            "last_served_at": _num(last_served_at),
            "demand_since": _num(demand_since),
            "cost": _num(cost),
            "start_unknown": max(0, int(start_unknown or 0))}


_FIELDS = ("key", "family", "token", "listed", "active", "confirmed", "held",
           "events_in_horizon", "next_start", "board_events", "feed_covered",
           "last_served_at", "demand_since", "cost", "start_unknown")


def _norm(c: dict) -> dict:
    """A competition dict, normalised whether or not `competition()` built
    it (idempotent)."""
    return competition(**{k: c.get(k) for k in _FIELDS})


def bound_cycles(c: dict) -> int:
    if c["held"] > 0:
        return BOUND_CYCLES_HELD
    if c["feed_covered"]:
        return BOUND_CYCLES_FEED_COVERED
    return BOUND_CYCLES_METERED_DEPENDENT


def in_play(c: dict, *, now: float) -> bool:
    """An event of this competition started within the horizon's tail."""
    return c["next_start"] is not None and c["next_start"] <= now


def _gate(c: dict, *, now: float) -> tuple:
    """(planned receipt or None, why) before any budget is considered."""
    if c["listed"] is None and not c["confirmed"] and c["held"] <= 0:
        return (PROVIDER_CATALOGUE_UNREAD,
                "the provider's unmetered catalogue was not read, so this key "
                "is unconfirmed and no metered call is made on it")
    if c["listed"] is False:
        return (PROVIDER_DOES_NOT_LIST,
                "the provider's own catalogue does not carry this key")
    if c["active"] is False:
        return (PROVIDER_LISTS_INACTIVE,
                "the provider lists this key with active=false")
    if c["events_in_horizon"] == 0 and c["start_unknown"] > 0:
        return (None, "%d venue event(s) listed with no stated start time: "
                      "not evidence of an empty horizon, so it stays in "
                      "demand" % c["start_unknown"])
    if c["events_in_horizon"] == 0:
        why = ("no venue event starts in the next %.0f h or started in the "
               "last %.0f h, so this competition costs nothing this cycle"
               % (HORIZON_AHEAD_S / 3600.0, HORIZON_BEHIND_S / 3600.0))
        if c["held"] > 0:
            why += ("; %d held position(s) on it, but the provider's odds "
                    "response lists no game outside that window (a finished "
                    "game awaiting settlement is not in it), so a metered "
                    "call would return nothing to manage" % c["held"])
        return (SKIPPED_NO_VENUE_EVENT_IN_HORIZON, why)
    if c["held"] > 0:
        return (None, "a held position's competition, in the horizon: a "
                      "one-cycle bound")
    return (None, None)


def _reference(c: dict, *, now: float, cycle_s: float) -> float:
    """The instant a competition's staleness is counted from: the later of
    the cycle it was last served and the cycle BEFORE its current run in
    demand began (`demand_since`; None = the run begins now)."""
    since = c["demand_since"] if c["demand_since"] is not None else now
    entry = min(float(since), float(now)) - float(cycle_s)
    if c["last_served_at"] is None:
        return entry
    return max(float(c["last_served_at"]), entry)


def deadline(c: dict, *, now: float, cycle_s: float) -> float:
    """The cycle instant by which the competition must be served to keep its
    bound: its reference plus `bound_cycles` cycles."""
    return _reference(c, now=now, cycle_s=cycle_s) + \
        bound_cycles(c) * float(cycle_s)


def _overdue_cycles(c: dict, *, now: float, cycle_s: float) -> int:
    """Whole cycles by which the competition would be PAST its bound at the
    next cycle if it were skipped now (1 = due this cycle; 0 = may wait one
    more cycle; 2+ = already past its bound).

    Counted from `_reference`, so a competition ENTERING demand now is due
    exactly when its own bound says (bound 1: this cycle; bound 2: the next)
    and then ages one per cycle, served or not, like any other. (The first
    version made every never-served competition "due now" whatever its
    bound and kept a re-entering competition's old service as its clock;
    the adversarial review's fuzz found both letting a competition wait one
    cycle past a stated bound in plans declared feasible.)"""
    over = (now - _reference(c, now=now, cycle_s=cycle_s)) + cycle_s - \
        bound_cycles(c) * cycle_s
    return max(-1000, min(1000, int(math.floor(
        (over + DEADLINE_TOLERANCE * cycle_s) / cycle_s))))


def _order_key(c: dict, *, now: float, cycle_s: float) -> tuple:
    start = c["next_start"]
    soon = math.inf if start is None else max(now, start)
    od = _overdue_cycles(c, now=now, cycle_s=cycle_s)
    return (-od,
            # PAST ITS BOUND: the exact deadline, earliest first. A
            # competition served after another fell due is re-deadlined at
            # least a cycle after it, so it can never be served again first
            # (the starvation bound's proof needs exactly this).
            deadline(c, now=now, cycle_s=cycle_s) if od >= 2 else 0.0,
            0 if c["held"] > 0 else 1,
            0 if in_play(c, now=now) else 1,
            1 if c["feed_covered"] else 0,
            soon,
            -(c["events_in_horizon"] if c["events_in_horizon"] is not None
              else (c["board_events"] or 0)),
            c["key"])


def _select(demand: list, *, now: float, cycle_s: float, cycle_budget: float,
            daily_remaining: float, cost_of, max_calls: int) -> tuple:
    """One cycle of the rule: (ordered, scheduled keys, spend, binding).
    Never more than `max_calls` competitions, never more credits than the
    cycle's allowance (a held competition: the 24 h remainder). `binding`
    names, per competition not chosen, the limit that deferred it and what
    the competitions ranked above it had spent."""
    ordered = sorted(demand, key=lambda c: _order_key(c, now=now,
                                                      cycle_s=cycle_s))
    spent, chosen, binding = 0.0, [], {}
    for c in ordered:
        if len(chosen) >= max_calls:
            binding[c["key"]] = (BINDING_CALLS, spent, len(chosen))
            continue
        cost = cost_of(c)
        cap = daily_remaining if c["held"] > 0 else min(cycle_budget,
                                                        daily_remaining)
        if spent + cost <= cap + 1e-9:
            chosen.append(c["key"])
            spent += cost
        elif spent + cost > daily_remaining + 1e-9:
            binding[c["key"]] = (BINDING_DAILY_ENVELOPE, spent, len(chosen))
        else:
            binding[c["key"]] = (BINDING_CYCLE_CREDITS, spent, len(chosen))
    return ordered, chosen, spent, binding


def calls_per_cycle(*, max_calls: int, allowance: float,
                    max_cost: float) -> int:
    """K: the competitions one cycle can fetch -- the call budget, or what
    the credit allowance buys at the dearest call, whichever is smaller."""
    if max_cost <= 0:
        return int(max_calls)
    return max(0, min(int(max_calls), int(math.floor(allowance / max_cost
                                                     + 1e-9))))


def _why_deferred(rank: int, binding: tuple | None, *, cost: float,
                  max_calls: int, cycle_budget: float,
                  daily_remaining: float, daily_envelope: float) -> str:
    """The reason a competition in demand was not fetched, naming the limit
    that bound -- never 'went to higher-ranked competitions' when nothing
    ranked above it was fetched (the adversarial review's finding)."""
    kind, spent_above, n_above = binding or (BINDING_CALLS, 0.0, 0)
    if kind == BINDING_CALLS:
        if n_above == 0:
            return ("rank %d: the declared call budget is %d metered calls a "
                    "cycle" % (rank, max_calls))
        return ("rank %d: this cycle's %d metered calls went to the %d "
                "higher-ranked competition(s)" % (rank, max_calls, n_above))
    if kind == BINDING_DAILY_ENVELOPE:
        return ("rank %d: the rolling 24 h envelope (%.1f credits) has %.1f "
                "left%s, less than this call's %.1f credits; the slot is the "
                "rule run forward on the actual ledger, the cycle it frees "
                "a call" % (rank, daily_envelope,
                            max(0.0, daily_remaining - spent_above),
                            (" after %.1f planned for %d higher-ranked "
                             "competition(s)" % (spent_above, n_above))
                            if n_above else "", cost))
    return ("rank %d: this cycle's credit allowance (%.1f) has %.1f left%s, "
            "less than this call's %.1f credits"
            % (rank, cycle_budget, max(0.0, cycle_budget - spent_above),
               (" after %d higher-ranked competition(s)" % n_above)
               if n_above else "", cost))


def plan(competitions, *, now: float, cycle_s: float,
         daily_envelope: float = DAILY_CREDIT_ENVELOPE,
         spent_24h: float = 0.0,
         spend_ledger=None,
         cost_per_fetch: float = CREDITS_PER_FETCH_ESTIMATE,
         cost_basis: str = COST_ESTIMATED,
         max_calls: int = MAX_METERED_CALLS_PER_CYCLE,
         simulate: bool = True) -> dict:
    """THIS CYCLE'S PLAN: one planned receipt per competition, and the fetch
    order. Pure and deterministic: the same inputs give the same plan.

    `spend_ledger`: the rolling day's spend as (cycle instant, credits)
    pairs, so the forward run of the rule knows WHEN spend leaves the day
    (`envelope_window_s`). Spend in `spent_24h` beyond the ledger has no
    known instant and is taken as spent NOW -- the latest it can have been,
    so a promised slot is never earlier than the real ledger allows.

    Never raises on a malformed competition: it is normalised by
    `competition()` first, so the caller passes the dicts it built."""
    comps = [_norm(c) for c in (competitions or ())]
    cycle_s = float(cycle_s)
    max_calls = max(0, int(max_calls))
    allowance = per_cycle_allowance(daily_envelope, cycle_s)
    window = envelope_window_s(cycle_s)
    ledger = []
    for a, c in (spend_ledger or ()):
        a, c = _num(a), _num(c)
        if a is not None and c is not None and c > 0 and \
                0.0 <= now - a < window:
            ledger.append((a, c))
    ledger_sum = sum(c for _, c in ledger)
    spent_24h = max(0.0, float(spent_24h or 0.0), ledger_sum)
    unknown_instant = spent_24h - ledger_sum
    daily_remaining = max(0.0, float(daily_envelope) - spent_24h)
    cycle_budget = min(allowance, daily_remaining)

    def cost_of(c):
        return c["cost"] if c["cost"] is not None else float(cost_per_fetch)

    receipts: dict = {}
    demand = []
    for c in comps:
        planned, why = _gate(c, now=now)
        r = {"key": c["key"], "family": c["family"], "token": c["token"],
             "held": c["held"], "feed_covered": c["feed_covered"],
             "in_play": in_play(c, now=now),
             "events_in_horizon": c["events_in_horizon"],
             "events_start_unknown": c["start_unknown"],
             "horizon_known": c["events_in_horizon"] is not None,
             "next_start": c["next_start"],
             "last_served_at": c["last_served_at"],
             "staleness_s": (None if c["last_served_at"] is None
                             else round(now - c["last_served_at"], 3)),
             # whole cycles since the competition was last fetched; None =
             # never fetched within the scheduler's memory (24 h of receipts)
             "cycles_since_served": (
                 None if c["last_served_at"] is None
                 else max(0, int(round((now - c["last_served_at"])
                                       / cycle_s)))),
             "bound_cycles": bound_cycles(c),
             "bound_s": bound_cycles(c) * cycle_s,
             "starvation_bound_cycles": None,
             "cost_estimate": cost_of(c), "cost_basis": cost_basis,
             "next_slot_at": None, "next_service_at": None,
             "priority_rank": None, "overdue_cycles": None,
             "deadline_at": None, "demand_since": None,
             "budget_binding": None,
             "planned": planned, "why": why}
        if planned == SKIPPED_NO_VENUE_EVENT_IN_HORIZON and \
                c["next_start"] is not None and c["next_start"] > now:
            # When it ENTERS the horizon: its first event minus the horizon.
            r["next_slot_at"] = max(now + cycle_s,
                                    c["next_start"] - HORIZON_AHEAD_S)
        receipts[c["key"]] = r
        if planned is None:
            # in demand: its run in demand continues, or begins now
            r["demand_since"] = (c["demand_since"]
                                 if c["demand_since"] is not None else now)
            demand.append(c)

    ordered, chosen, spend_, binding = _select(
        demand, now=now, cycle_s=cycle_s, cycle_budget=cycle_budget,
        daily_remaining=daily_remaining, cost_of=cost_of,
        max_calls=max_calls)
    overdue = {c["key"]: _overdue_cycles(c, now=now, cycle_s=cycle_s)
               for c in demand}

    # ── THE STATED BOUNDS (module docstring) ─────────────────────────
    max_cost = max([cost_of(c) for c in demand] or [float(cost_per_fetch)])
    k = calls_per_cycle(max_calls=max_calls, allowance=allowance,
                        max_cost=max_cost)
    k_now = calls_per_cycle(max_calls=max_calls, allowance=cycle_budget,
                            max_cost=max_cost)
    call_util = sum(1.0 / bound_cycles(c) for c in demand)
    credit_util = sum(cost_of(c) / bound_cycles(c) for c in demand)
    due = [key for key, od in overdue.items() if od >= 1]
    if k <= 0 or k_now < k:
        feasibility = F_ENVELOPE
    elif not (call_util <= k + 1e-9 and credit_util <= allowance + 1e-9):
        feasibility = F_DEMAND
    elif any(od >= 2 for od in overdue.values()):
        feasibility = F_BACKLOG
    elif len(due) > k_now:
        feasibility = F_DUE
    else:
        feasibility = FEASIBLE
    feasible = feasibility == FEASIBLE
    for c in demand:
        r = receipts[c["key"]]
        r["deadline_at"] = round(deadline(c, now=now, cycle_s=cycle_s), 3)
        if feasibility == F_ENVELOPE:
            # the envelope, not the rule, decides this cycle: no analytic
            # bound is stated; the slot below is the ledger run forward
            r["starvation_bound_cycles"] = None
        elif feasible:
            r["starvation_bound_cycles"] = bound_cycles(c)
        else:
            r["starvation_bound_cycles"] = BOUND_CYCLES_MAX + int(
                math.ceil((len(demand) - 1) / float(k)))

    for rank, c in enumerate(ordered):
        r = receipts[c["key"]]
        r["priority_rank"] = rank
        r["overdue_cycles"] = overdue[c["key"]]
        if c["key"] in chosen:
            r["planned"] = SCHEDULED
            r["why"] = ("rank %d, within this cycle's budget" % rank) + (
                "; " + r["why"] if r["why"] else "")
        else:
            r["planned"] = DEFERRED_TO_SLOT
            b = binding.get(c["key"])
            r["budget_binding"] = (b or (BINDING_CALLS,))[0]
            r["why"] = _why_deferred(
                rank, b, cost=cost_of(c), max_calls=max_calls,
                cycle_budget=cycle_budget, daily_remaining=daily_remaining,
                daily_envelope=float(daily_envelope))

    # ── WHERE EACH COMPETITION IS NEXT SERVED: the rule run forward ───
    # Board, costs and cadence held constant; the envelope rolled forward on
    # the ACTUAL ledger (plus this cycle's planned spend), never assumed
    # full. A deferred competition's slot is `next_slot_at`; a scheduled
    # one's next fetch after this one is `next_service_at`.
    deferred = [key for key, r in receipts.items()
                if r["planned"] == DEFERRED_TO_SLOT]
    if demand and simulate:
        state = {c["key"]: dict(c) for c in demand}
        for key in state:
            state[key]["demand_since"] = receipts[key]["demand_since"]
        for key in chosen:
            state[key]["last_served_at"] = now
        sim = list(ledger)
        if unknown_instant > 1e-9:
            sim.append((now, unknown_instant))
        if spend_ > 0:
            sim.append((now, spend_))
        waiting = set(state)
        for step in range(1, SIMULATION_CYCLES + 1):
            t = now + step * cycle_s
            sim = [(a, c) for a, c in sim if t - a < window]
            rem = max(0.0, float(daily_envelope) - sum(c for _, c in sim))
            _, got, spent_t, _ = _select(
                list(state.values()), now=t, cycle_s=cycle_s,
                cycle_budget=min(allowance, rem), daily_remaining=rem,
                cost_of=cost_of, max_calls=max_calls)
            if spent_t > 0:
                sim.append((t, spent_t))
            for key in got:
                state[key]["last_served_at"] = t
                if key in waiting:
                    receipts[key]["next_service_at"] = t
                    if key in deferred:
                        receipts[key]["next_slot_at"] = t
                    waiting.discard(key)
            if not waiting:
                break
        for key in deferred:
            if receipts[key]["next_slot_at"] is None:
                receipts[key]["planned"] = DEFERRED_NO_SLOT_WITHIN_ENVELOPE
                receipts[key]["why"] = (
                    "no slot within %d cycles at %d calls and %.1f credits a "
                    "cycle on the actual 24 h ledger: the budget cannot "
                    "carry this demand" % (SIMULATION_CYCLES, max_calls,
                                           allowance))
    for r in receipts.values():
        r["final"] = r["planned"] if r["planned"] != SCHEDULED else None
    fetch_order = [(c["key"], c["family"]) for c in ordered
                   if c["key"] in chosen]
    return {
        "version": VERSION, "at": now, "cycle_s": cycle_s,
        "envelope": {"daily_credits": float(daily_envelope),
                     "per_cycle_allowance": round(allowance, 3),
                     "window_s": window,
                     "spent_24h": round(spent_24h, 3),
                     "spent_24h_without_instant": round(unknown_instant, 3),
                     "daily_remaining": round(daily_remaining, 3),
                     "cycle_budget": round(cycle_budget, 3),
                     "planned_spend": round(spend_, 3),
                     "cost_per_fetch": float(cost_per_fetch),
                     "cost_basis": cost_basis},
        # THE DECLARED CALL BUDGET and what this cycle uses of it. `extra`
        # counts the metered calls beyond the scheduled fetches (re-fetches,
        # unmeasured discovery refreshes), each claimed through `claim_call`.
        "calls": {"budget": max_calls, "scheduled": len(chosen),
                  "extra": {}, "per_cycle_capacity": k,
                  "capacity_this_cycle": k_now},
        "utilization_per_cycle": round(credit_util, 3),
        "call_utilization_per_cycle": round(call_util, 3),
        "feasible": feasible,
        "feasibility": feasibility,
        "due_this_cycle": len(due),
        "demand": len(demand),
        "receipts": [receipts[c["key"]] for c in comps],
        "fetch_order": fetch_order,
        "horizon": {"ahead_s": HORIZON_AHEAD_S, "behind_s": HORIZON_BEHIND_S},
    }


def calls_made(plan_out: dict) -> int:
    """Metered calls this cycle: one per scheduled fetch (made or attempted)
    plus every extra call claimed."""
    calls = (plan_out or {}).get("calls") or {}
    return int(calls.get("scheduled") or 0) + sum(
        int(v) for v in (calls.get("extra") or {}).values())


def calls_left(plan_out: dict) -> int:
    calls = (plan_out or {}).get("calls") or {}
    return max(0, int(calls.get("budget") or 0) - calls_made(plan_out))


def claim_call(plan_out: dict, kind: str) -> bool:
    """CLAIM ONE METERED CALL BEYOND THE SCHEDULED FETCHES, or refuse: the
    caller makes the call only on True. A plan with no budget recorded (the
    plan failed) claims nothing."""
    calls = (plan_out or {}).get("calls")
    if not isinstance(calls, dict) or calls_left(plan_out) <= 0:
        return False
    extra = calls.setdefault("extra", {})
    extra[kind] = int(extra.get(kind) or 0) + 1
    return True


def settle(plan_out: dict, key: str, *, ok: bool, at: float,
           credits: float, basis: str, events: int | None = None,
           detail: dict | None = None) -> dict | None:
    """Turn a SCHEDULED receipt into FETCHED / FETCH_FAILED with what the
    fetch actually cost. Returns the receipt, or None for an unknown key."""
    for r in plan_out.get("receipts") or ():
        if r["key"] == key and r["planned"] == SCHEDULED:
            r["final"] = FETCHED if ok else FETCH_FAILED
            r["fetched_at"] = at
            r["credits_charged"] = float(credits)
            r["credits_basis"] = basis
            r["provider_events"] = events
            if detail:
                r["detail"] = dict(detail)
            return r
    return None


def unsettled(plan_out: dict) -> list:
    """SCHEDULED receipts the cycle never fetched -- a fault, named."""
    return [r["key"] for r in plan_out.get("receipts") or ()
            if r["planned"] == SCHEDULED and r.get("final") is None]


def spend(plan_out: dict) -> float:
    """Credits actually charged by this cycle's metered calls."""
    return round(sum(float(r.get("credits_charged") or 0.0)
                     + float(r.get("discovery_credits") or 0.0)
                     for r in plan_out.get("receipts") or ()), 6)


def cycle_receipt(plan_out: dict) -> dict:
    """THE CYCLE'S OWN RECEIPT: the declared budget beside what was used of
    it, so 'never more metered calls than the budget' is a recorded fact per
    cycle, not a claim."""
    calls = plan_out.get("calls") or {}
    env = plan_out.get("envelope") or {}
    made = calls_made(plan_out)
    budget = int(calls.get("budget") or 0)
    spent = spend(plan_out)
    by: dict = {}
    for r in plan_out.get("receipts") or ():
        fin = r.get("final") or r.get("planned")
        by[fin] = by.get(fin, 0) + 1
    by_kind = {CALL_ODDS: int(calls.get("scheduled") or 0)}
    for k, v in (calls.get("extra") or {}).items():
        by_kind[k] = int(v)
    return {"calls_budget": budget,
            "calls_made": made,
            "calls_by_kind": by_kind,
            "credits_allowance": env.get("cycle_budget"),
            "credits_daily_remaining": env.get("daily_remaining"),
            "credits_spent": spent,
            # a held competition may spend the 24 h remainder beyond the
            # per-cycle allowance, never beyond it
            "budget_respected": (
                made <= budget
                and spent <= max(float(env.get("cycle_budget") or 0.0),
                                 float(env.get("daily_remaining") or 0.0))
                + 1e-9),
            "feasible": plan_out.get("feasible"),
            "feasibility": plan_out.get("feasibility"),
            "demand": plan_out.get("demand"),
            "call_utilization_per_cycle":
                plan_out.get("call_utilization_per_cycle"),
            "by_receipt": by}


# ── THE PER-CYCLE EVALUATION BOUND (MAX_PER_CYCLE), WITHOUT STARVATION ──

def evaluation_shares(demands, total: int) -> dict:
    """Max-min fair ('water-filling') shares of `total` evaluation slots.

    `demands`: [(key, events)] in fetch order. Every competition gets
    min(its demand, an equal share of what is left), and slots a competition
    cannot use flow to the others. With fewer slots than competitions the
    earlier ones (fetch order) get one each. Deterministic.
    """
    items = [(str(k), max(0, int(d or 0))) for k, d in (demands or ())]
    need = dict(items)
    out = {k: 0 for k, _ in items}
    left = max(0, int(total))
    active = [k for k, d in items if d > 0]
    while left > 0 and active:
        share = left // len(active)
        if share == 0:
            for k in active[:left]:
                out[k] += 1
            break
        nxt = []
        for k in active:
            give = min(share, need[k] - out[k])
            out[k] += give
            left -= give
            if out[k] < need[k]:
                nxt.append(k)
        active = nxt
    return out


def reserve_after(order, shares: dict) -> dict:
    """For each competition in fetch order, the slots reserved for the
    competitions AFTER it: a competition may evaluate while
    evaluated < total - reserve_after[key], so an early competition can use
    what later ones cannot, and never what they are owed."""
    keys = [str(k) for k in order]
    out, acc = {}, 0
    for k in reversed(keys):
        out[k] = acc
        acc += int(shares.get(k, 0))
    return out


def candidate_slot(*, cycle_at: float, cycle_s: float, receipt: dict,
                   position: int, share: int) -> float:
    """THE SLOT PROMISED TO A PROVIDER EVENT THE EVALUATION BOUND DEFERRED.

    It goes first in its competition's next fetches (oldest deferral
    first), `share` of them per fetch, so the n-th deferred event is judged
    within (1 + n // share) fetches, and each fetch comes within the bound
    the plan STATED for the competition: the starvation bound, which is the
    staleness bound when the demand is feasible and the larger overload
    bound when it is not. (The first version used the staleness bound
    always -- optimistic under overload: the adversarial review's finding.)
    Where the plan states no starvation bound (the 24 h envelope decides the
    cycle), the competition's next service from the rule run forward on the
    actual ledger spaces the fetches; failing that, its staleness bound."""
    cycles = receipt.get("starvation_bound_cycles")
    if cycles is None:
        nxt = receipt.get("next_service_at")
        if nxt is not None and float(nxt) > float(cycle_at):
            cycles = max(1, int(math.ceil(
                (float(nxt) - float(cycle_at)) / float(cycle_s) - 1e-9)))
        else:
            cycles = receipt.get("bound_cycles") or BOUND_CYCLES_HELD
    fetches = 1 + max(0, int(position)) // max(1, int(share))
    return float(cycle_at) + float(cycle_s) * int(cycles) * fetches


def candidate_order_key(event_id, *, commence_epoch, now: float,
                        deferred_since: dict, freshness_key) -> tuple:
    """The order a fetch's events are judged in.

    1. Events the evaluation bound DEFERRED in an earlier cycle, oldest
       deferral first -- this is what makes a deferral's next slot real.
    2. Live first: an event already in play, then one starting within SOON_S,
       then the rest.
    3. The existing freshest-first order (lever B) within that.
    """
    since = deferred_since.get(str(event_id)) if event_id is not None else None
    if commence_epoch is None:
        urgency = 2
    elif commence_epoch <= now:
        urgency = 0
    elif commence_epoch - now <= SOON_S:
        urgency = 1
    else:
        urgency = 2
    return (0 if since is not None else 1,
            since if since is not None else 0.0,
            urgency) + tuple(freshness_key)


def digest(plan_out: dict | None) -> dict | None:
    """The plan, bounded for a heartbeat: the cycle's budget receipt, counts
    by receipt, and one compact row per competition."""
    if not isinstance(plan_out, dict):
        return None
    rows = []
    for r in plan_out.get("receipts") or ():
        rows.append({k: r.get(k) for k in (
            "key", "token", "planned", "final", "priority_rank",
            "events_in_horizon", "events_start_unknown", "next_start",
            "in_play", "held", "held_finished",
            "feed_covered", "bound_cycles", "starvation_bound_cycles",
            "cycles_since_served", "staleness_s", "next_slot_at",
            "budget_binding", "credits_charged", "credits_basis",
            "discovery")})
    cyc = cycle_receipt(plan_out) if plan_out.get("calls") else None
    return {"version": plan_out.get("version"),
            "envelope": plan_out.get("envelope"),
            "cycle": cyc,
            "feasible": plan_out.get("feasible"),
            "feasibility": plan_out.get("feasibility"),
            "utilization_per_cycle": plan_out.get("utilization_per_cycle"),
            "call_utilization_per_cycle":
                plan_out.get("call_utilization_per_cycle"),
            "by_receipt": (cyc or {}).get("by_receipt") or {},
            "competitions": rows[:40],
            "spent_this_cycle": spend(plan_out),
            "unsettled": unsettled(plan_out),
            "failed": plan_out.get("failed")}

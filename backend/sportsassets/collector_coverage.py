"""THE COLLECTOR'S COVERAGE SCHEDULER (pure: no I/O, no clock, no database).

THE INCIDENT (P0, owner 2026-10-04). "A production trading system cannot
arbitrarily lose an entire major sport because four other sports consumed a
collector budget." The red team found NCAAF `budget_dropped` under the
collector's four-sport metered budget. Measured over the last 7 days of
production (research-sql incident_collector_cap_a, run 37233454453, 458
scheduled cycles, ext_candidate_outcomes + us_premap):

    competition  cycles with a venue event   fetched   NOT fetched
                 in the next 24 h
    cfb  NCAAF                 153              16         137
    nfl  NFL                   131              62          69
    uslc USL Championship      194               0         194
    mlb / unl / brb       218 / 209 / 110   all / all / 103    0 / 0 / 7

  and in the SAME cycles the four slots went, 910 times out of 1,518
  metered fetches, to a competition with NO venue event in the next 24 h
  (brb 333, unl 242, mlb 240, uwcl 80, cfb 15) -- because `select_sports`
  ranked by the venue board's event count over its whole unbounded future
  and gave MLB a permanent slot.

WHICH OF THE OWNER'S CAUSES IT WAS. A FIXED SPORT COUNT (four keys,
MAX_METERED_SPORTS_PER_CYCLE) combined with PRIORITY-ORDER STARVATION (a
deterministic ranking with no rotation, by events that may be days away, so
the same competitions lost every cycle). It was not a pagination budget, a
time budget or an API quota response: the account reported 14,559,615 credits
remaining (heartbeat 2026-10-04T20:28Z). MAX_PER_CYCLE (40 evaluations)
truncated NOTHING in the same 7 days: 0 DEFERRED rows of 23,602 judged.

WHAT REPLACES IT. Every enabled competition -- a venue-board token with a
provider key the provider's own catalogue confirms, plus every held
position's competition unconditionally -- is served within a STATED,
receipted staleness bound, inside an explicit daily credit envelope:

  * A competition with no venue event in the decision horizon costs nothing
    this cycle (SKIPPED_NO_VENUE_EVENT_IN_HORIZON, with the instant it will
    enter the horizon).
  * Each competition in the horizon carries a bound in cycles: 1 for a held
    position's competition and for a competition whose decision-time
    probability depends on the metered fetch; 2 for a competition whose
    Pinnacle price the subscribed feed already carries (its discovery is
    refreshed through the unmetered events endpoint every cycle, so the
    metered slot no longer gates its valuations).
  * Order: held first, then EARLIEST DEADLINE (how far past its bound a
    competition would be if skipped now), then metered-dependent before
    feed-covered, then the soonest venue event, then the most events in the
    horizon, then the key -- a total order, so the same inputs always give
    the same plan.
  * Budget: the per-cycle allowance is the daily envelope pro-rated to the
    cadence, and the rolling-24 h remainder is never exceeded -- a held
    competition may use the remainder beyond the per-cycle allowance, never
    beyond the envelope.
  * Every competition not fetched gets a receipt; DEFERRED_TO_SLOT carries
    the cycle in which the same deterministic rule will fetch it, found by
    running the rule forward (board, costs and cadence held constant).

THE ENVELOPE DEFAULT IS TODAY'S SPEND, STATED WITH ITS ARITHMETIC
(ENVELOPE_CHANGE). What changes is what a credit buys: the per-request cost
is CHARGED AS MEASURED (the provider's per-request usage header) and only
estimated, conservatively, until it is measured -- so a cheaper request
shape becomes more coverage, never more spend.
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
#: envelope cannot carry the demand (held competitions alone exhausting it,
#: or a single fetch costing more than a cycle's allowance). Never expected at
#: the defaults; named rather than given an invented slot.
DEFERRED_NO_SLOT_WITHIN_ENVELOPE = "DEFERRED_NO_SLOT_WITHIN_ENVELOPE"

PLANNED_RECEIPTS = (SCHEDULED, DEFERRED_TO_SLOT,
                    SKIPPED_NO_VENUE_EVENT_IN_HORIZON, PROVIDER_DOES_NOT_LIST,
                    PROVIDER_LISTS_INACTIVE, PROVIDER_CATALOGUE_UNREAD,
                    DEFERRED_NO_SLOT_WITHIN_ENVELOPE)
FINAL_RECEIPTS = tuple(r for r in PLANNED_RECEIPTS if r != SCHEDULED) + (
    FETCHED, FETCH_FAILED)

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

#: STALENESS BOUNDS, in cycles. See the module docstring.
BOUND_CYCLES_HELD = 1
BOUND_CYCLES_METERED_DEPENDENT = 1
BOUND_CYCLES_FEED_COVERED = 2

#: A cycle that starts this fraction of a cycle early (cadence jitter) is
#: still the next cycle: deadlines are counted in whole cycles.
DEADLINE_TOLERANCE = 0.1

#: How far forward the rule is run to find a deferred competition's slot: one
#: day of cycles at the nominal cadence.
SIMULATION_CYCLES = 96

# ── the envelope ─────────────────────────────────────────────────────
#: TODAY'S SPEND, AS THE CODE STATES IT. ext_pinnacle_loop.METERED_BUDGET_CHANGE
#: "after": four keys, ~80 credits a cycle, ~7.7k a day at the 900 s cadence.
#: 80 credits x 96 cycles = 7,680.
DAILY_CREDIT_ENVELOPE = 7680.0
#: The per-fetch cost charged until the provider's own per-request usage header
#: has been read: the upper end of METERED_BUDGET_CHANGE's "18-21 credits each".
#: With it the default envelope buys exactly today's four fetches a cycle; a
#: MEASURED lower cost buys more coverage at the same spend, never more spend.
CREDITS_PER_FETCH_ESTIMATE = 20.0

COST_MEASURED = "MEASURED_PROVIDER_USAGE_HEADER"
COST_ESTIMATED = "ESTIMATED_UPPER_BOUND_UNTIL_MEASURED"

ENVELOPE_CHANGE = {
    "daily_envelope_credits": DAILY_CREDIT_ENVELOPE,
    "arithmetic": ("80 credits a cycle (METERED_BUDGET_CHANGE 'after': four "
                   "keys at ~20) x 96 cycles a day (86,400 s / 900 s) = "
                   "7,680 credits a day: the envelope equals today's stated "
                   "spend"),
    "per_cycle_allowance": ("envelope x cycle_s / 86,400 = 80 credits at the "
                            "900 s cadence; a held position's competition may "
                            "use the rolling-24 h remainder beyond it, never "
                            "beyond the envelope"),
    "what_changed": ("the four-key count is gone. Spend is bounded by the "
                     "envelope in CREDITS, each fetch is charged what the "
                     "provider reports it cost (x-requests-last), and only "
                     "until that is read is a fetch charged the 20-credit "
                     "upper estimate -- which reproduces today's four fetches "
                     "a cycle exactly. A competition with no venue event in "
                     "the next 24 h costs nothing"),
    "net_change_in_spend": ("none at the default: the envelope is today's "
                            "~7.7k a day. Coverage per credit rises: 910 of "
                            "1,518 fetches in the last 7 days went to "
                            "competitions with no venue event in the next "
                            "24 h, and those fetches now go to competitions "
                            "with events"),
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
                last_served_at=None, cost=None) -> dict:
    """One enabled competition's facts, normalised. Pure.

    `listed` / `active`: the provider's unmetered catalogue (None = unread).
    `confirmed`: a key this lane has always valued (SPORTS_CONFIRMED), run
    even when the catalogue is unread, as before.
    `events_in_horizon` / `next_start`: from the venue's own catalogue; None
    is UNKNOWN and never treated as zero (an unread horizon is not an empty
    one: the competition stays in demand).
    `feed_covered`: the subscribed feed carries this family's Pinnacle price
    in this process now.
    `last_served_at`: the cycle instant the competition was last fetched."""
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
            "cost": _num(cost)}


_FIELDS = ("key", "family", "token", "listed", "active", "confirmed", "held",
           "events_in_horizon", "next_start", "board_events", "feed_covered",
           "last_served_at", "cost")


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
    if c["held"] > 0:
        return (None, "a held position's competition is served "
                      "unconditionally (within the envelope)")
    if c["events_in_horizon"] == 0:
        return (SKIPPED_NO_VENUE_EVENT_IN_HORIZON,
                "no venue event starts in the next %.0f h or started in the "
                "last %.0f h, so this competition costs nothing this cycle"
                % (HORIZON_AHEAD_S / 3600.0, HORIZON_BEHIND_S / 3600.0))
    return (None, None)


def _overdue_cycles(c: dict, *, now: float, cycle_s: float) -> int:
    """Whole cycles by which the competition would be PAST its bound at the
    next cycle if it were skipped now (1 = due this cycle).

    A NEVER-SERVED competition is DUE (1), not infinitely overdue: a
    competition entering the horizon, or every competition after a restart,
    must take its turn by the same deadline rule -- a sentinel that put it
    ahead of everything would let a newly listed soccer competition bump a
    metered-dependent sport whose deadline is this cycle."""
    if c["last_served_at"] is None:
        return 1
    over = (now - c["last_served_at"]) + cycle_s - bound_cycles(c) * cycle_s
    return max(-1000, min(1000, int(math.floor(
        (over + DEADLINE_TOLERANCE * cycle_s) / cycle_s))))


def _order_key(c: dict, *, now: float, cycle_s: float) -> tuple:
    start = c["next_start"]
    soon = math.inf if start is None else max(now, start)
    return (0 if c["held"] > 0 else 1,
            -_overdue_cycles(c, now=now, cycle_s=cycle_s),
            1 if c["feed_covered"] else 0,
            soon,
            -(c["events_in_horizon"] if c["events_in_horizon"] is not None
              else (c["board_events"] or 0)),
            c["key"])


def _select(demand: list, *, now: float, cycle_s: float, cycle_budget: float,
            daily_remaining: float, cost_of) -> tuple:
    """One cycle of the rule: (ordered, scheduled keys, spend)."""
    ordered = sorted(demand, key=lambda c: _order_key(c, now=now,
                                                      cycle_s=cycle_s))
    spent, chosen = 0.0, []
    for c in ordered:
        cost = cost_of(c)
        cap = daily_remaining if c["held"] > 0 else min(cycle_budget,
                                                        daily_remaining)
        if spent + cost <= cap + 1e-9:
            chosen.append(c["key"])
            spent += cost
    return ordered, chosen, spent


def plan(competitions, *, now: float, cycle_s: float,
         daily_envelope: float = DAILY_CREDIT_ENVELOPE,
         spent_24h: float = 0.0,
         cost_per_fetch: float = CREDITS_PER_FETCH_ESTIMATE,
         cost_basis: str = COST_ESTIMATED,
         simulate: bool = True) -> dict:
    """THIS CYCLE'S PLAN: one planned receipt per competition, and the fetch
    order. Pure and deterministic: the same inputs give the same plan.

    Never raises on a malformed competition: it is normalised by
    `competition()` first, so the caller passes the dicts it built."""
    comps = [_norm(c) for c in (competitions or ())]
    cycle_s = float(cycle_s)
    allowance = per_cycle_allowance(daily_envelope, cycle_s)
    spent_24h = max(0.0, float(spent_24h or 0.0))
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
             "events_in_horizon": c["events_in_horizon"],
             "horizon_known": c["events_in_horizon"] is not None,
             "next_start": c["next_start"],
             "last_served_at": c["last_served_at"],
             "staleness_s": (None if c["last_served_at"] is None
                             else round(now - c["last_served_at"], 3)),
             "bound_cycles": bound_cycles(c),
             "bound_s": bound_cycles(c) * cycle_s,
             "cost_estimate": cost_of(c), "cost_basis": cost_basis,
             "next_slot_at": None, "priority_rank": None,
             "planned": planned, "why": why}
        if planned == SKIPPED_NO_VENUE_EVENT_IN_HORIZON and \
                c["next_start"] is not None and c["next_start"] > now:
            # When it ENTERS the horizon: its first event minus the horizon.
            r["next_slot_at"] = max(now + cycle_s,
                                    c["next_start"] - HORIZON_AHEAD_S)
        receipts[c["key"]] = r
        if planned is None:
            demand.append(c)

    ordered, chosen, spend = _select(
        demand, now=now, cycle_s=cycle_s, cycle_budget=cycle_budget,
        daily_remaining=daily_remaining, cost_of=cost_of)
    for rank, c in enumerate(ordered):
        r = receipts[c["key"]]
        r["priority_rank"] = rank
        r["overdue_cycles"] = _overdue_cycles(c, now=now, cycle_s=cycle_s)
        if c["key"] in chosen:
            r["planned"] = SCHEDULED
            r["why"] = r["why"] or (
                "in the horizon, rank %d, within this cycle's allowance"
                % rank)
        else:
            r["planned"] = DEFERRED_TO_SLOT
            r["why"] = ("rank %d: this cycle's allowance (%.1f credits, %.1f "
                        "left of the 24 h envelope) is spent on higher-ranked "
                        "competitions" % (rank, cycle_budget, daily_remaining))

    # ── WHERE EACH DEFERRED COMPETITION'S SLOT IS ───────────────────
    utilization = sum(cost_of(c) / bound_cycles(c) for c in demand)
    deferred = [k for k, r in receipts.items()
                if r["planned"] == DEFERRED_TO_SLOT]
    if deferred and simulate:
        state = {c["key"]: dict(c) for c in demand}
        for k in chosen:
            state[k]["last_served_at"] = now
        waiting = set(deferred)
        for step in range(1, SIMULATION_CYCLES + 1):
            t = now + step * cycle_s
            _, got, _ = _select(list(state.values()), now=t, cycle_s=cycle_s,
                                cycle_budget=allowance,
                                daily_remaining=float(daily_envelope),
                                cost_of=cost_of)
            for k in got:
                state[k]["last_served_at"] = t
                if k in waiting:
                    receipts[k]["next_slot_at"] = t
                    waiting.discard(k)
            if not waiting:
                break
        for k in waiting:
            receipts[k]["planned"] = DEFERRED_NO_SLOT_WITHIN_ENVELOPE
            receipts[k]["why"] = (
                "no slot within %d cycles at %.1f credits a cycle: the "
                "envelope cannot carry this demand" % (SIMULATION_CYCLES,
                                                       allowance))
    for r in receipts.values():
        r["final"] = r["planned"] if r["planned"] != SCHEDULED else None
    fetch_order = [(c["key"], c["family"]) for c in ordered
                   if c["key"] in chosen]
    return {
        "version": VERSION, "at": now, "cycle_s": cycle_s,
        "envelope": {"daily_credits": float(daily_envelope),
                     "per_cycle_allowance": round(allowance, 3),
                     "spent_24h": round(spent_24h, 3),
                     "daily_remaining": round(daily_remaining, 3),
                     "cycle_budget": round(cycle_budget, 3),
                     "planned_spend": round(spend, 3),
                     "cost_per_fetch": float(cost_per_fetch),
                     "cost_basis": cost_basis},
        # Feasible when every competition in the horizon can be served within
        # its bound: the per-cycle demand (cost / bound) fits the allowance.
        "utilization_per_cycle": round(utilization, 3),
        "feasible": utilization <= allowance + 1e-9,
        "receipts": [receipts[c["key"]] for c in comps],
        "fetch_order": fetch_order,
        "horizon": {"ahead_s": HORIZON_AHEAD_S, "behind_s": HORIZON_BEHIND_S},
    }


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
    """Credits actually charged by this cycle's fetches."""
    return round(sum(float(r.get("credits_charged") or 0.0)
                     for r in plan_out.get("receipts") or ()), 6)


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


def candidate_order_key(event_id, *, commence_epoch, now: float,
                        deferred_since: dict, freshness_key) -> tuple:
    """The order a fetch's events are judged in.

    1. Events the evaluation bound DEFERRED in an earlier cycle, oldest
       deferral first -- this is what makes a deferral's next slot real.
    2. Urgency: in play or starting within 6 h before later events (time to
       event).
    3. The existing freshest-first order (lever B) within that.
    """
    since = deferred_since.get(str(event_id)) if event_id is not None else None
    soon = (commence_epoch is not None
            and commence_epoch - now <= HORIZON_BEHIND_S)
    return (0 if since is not None else 1,
            since if since is not None else 0.0,
            0 if soon else 1) + tuple(freshness_key)


def digest(plan_out: dict | None) -> dict | None:
    """The plan, bounded for a heartbeat: counts by receipt and one compact
    row per competition."""
    if not isinstance(plan_out, dict):
        return None
    by: dict = {}
    rows = []
    for r in plan_out.get("receipts") or ():
        fin = r.get("final") or r.get("planned")
        by[fin] = by.get(fin, 0) + 1
        rows.append({k: r.get(k) for k in (
            "key", "token", "planned", "final", "priority_rank",
            "events_in_horizon", "next_start", "held", "feed_covered",
            "bound_cycles", "staleness_s", "next_slot_at", "credits_charged",
            "credits_basis", "discovery")})
    return {"version": plan_out.get("version"),
            "envelope": plan_out.get("envelope"),
            "feasible": plan_out.get("feasible"),
            "utilization_per_cycle": plan_out.get("utilization_per_cycle"),
            "by_receipt": by, "competitions": rows[:40],
            "spent_this_cycle": spend(plan_out),
            "unsettled": unsettled(plan_out)}

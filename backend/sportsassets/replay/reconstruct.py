"""THE R30 CHAIN AT EACH DECISION CLOCK, AND THE TIMELINE AFTER IT.

Every read goes through the choke point (pit.Reader); nothing here holds a
query of its own. Two clocks per opportunity:

  DECISION CLOCK  max(decided_at, the decision row's recorded_at): the
                  instant the decision was made AND durable. Every input of
                  the replayed R30 chain (valuation, book, Karen, Eddie's
                  history, Allie's inputs, the rails, the alternatives, the
                  hurdle tape, the session's configuration) is read AT this
                  clock: only rows recorded at or before it. The audit scope
                  "<decision_id>:DECISION" carries the latest recorded stamp
                  used; the database CHECKs it is <= the decision clock.
  HORIZON         the run's clock_end: the PAPER execution, Xavier's
                  reviews, the settlements and the marks are read at the
                  horizon (scope "<decision_id>:OUTCOME"), never later.

THE COMPONENTS (each MEASURED or UNAVAILABLE with its reason):

  provider_observation  external_valuations: provider, book, observed_at,
                        age, outcome books, probability
  bettor_receipt        received_at and the receipt latency
  mapping               the contract identity the resolver bound and the
                        event start from a row VISIBLE at the clock
  valuation             probability, executable price, edge, admissible,
                        refusals, settlement comparison (outcome columns are
                        hidden by the choke point)
  venue_book            the decision's own book observation (else the
                        latest at or before the decision), age, top of book
  derek                 canonical_components.derek_component on the record
  karen                 Karen's challenges OPEN AT THE CLOCK on this market
                        or strategy, the state rebuilt from the append-only
                        karen_challenge_events recorded by the clock; no
                        Karen record at or before the clock -> UNAVAILABLE
  eddie                 Eddie's estimate recorded by the clock, else
                        agents/eddie.estimate (pure) on the decision, its
                        book and Eddie's history AS OF the clock (floored to
                        the history bucket); the queue at submission comes
                        from the ACKNOWLEDGED order event (paper_orders'
                        queue columns are rewritten in place and hidden)
  allie                 allie_capital.allocate on point-in-time inputs (event
                        start, settlement lags recorded before the clock,
                        open exposure from position truth, usable idle cash
                        from the ledger after the hedge reserve, Allie's own
                        hurdle sample, the effective per-order rail).
                        Authority stays SHADOW_PENDING_OWNER_APPROVAL.
  rails                 THE HARD RISK RAILS EFFECTIVE AT THE CLOCK (the
                        capital policy recorded on the decision, else the
                        session configuration): per-order cap, per-market and
                        per-fixture concentration headroom, the hedge reserve
                        and the concurrent-group count, each from
                        point-in-time positions, open orders and the ledger.
                        Every benchmark AND Allie is clamped by exactly these;
                        a configured rail whose input cannot be measured makes
                        every benchmark UNAVAILABLE.
  opportunity_score     lost_opportunity.score.score (pure) at the decision
  canonical_intent      canonical_intent.build_decision_intent (pure) for an
                        ENTER; when it cannot be built the CAUSE is recorded:
                        DECISION_CONTENT (the decision's own record makes it
                        unbuildable -> the R30 invariant applies) or
                        REPLAY_EVIDENCE_MISSING (the replay cannot see what
                        the live chain had -> UNAVAILABLE, never a zero)
  alternatives          the contemporaneous QUALIFIED alternatives in the
                        same sleeve: opportunities an allocator could LEGALLY
                        take under the same hard rules (Derek ENTER, or a
                        refusal for capital only) with a positive recorded
                        executable net after fees; hard-rule refusals
                        (freshness, settlement, entry switch, already held,
                        ...) are never alternatives; an admissible
                        opportunity whose economics were never recorded makes
                        the alternative set UNAVAILABLE
  hurdle                the HURDLE_QUANTILE of the qualified tape's expected
                        profit per capital-hour over the tape window

Nothing here writes, sizes, submits or manages anything.
"""
from __future__ import annotations

import math

from .. import allie_capital as AC
from .. import canonical_components as CC
from .. import canonical_intent as CI
from ..intel import attribution as IA
from ..intel import common as IC
from ..lost_opportunity import classify as LC
from ..lost_opportunity import score as SC
from ..profitability import economics as EC
from . import pit as P

VERSION = "R30_REPLAY_RECONSTRUCT_V2"
HOUR = 3600.0
DAY = 86400.0

#: THE RUN PARAMETERS (research / tournament parameters, persisted with every
#: run and its params sha -- never owner-approved rails, never read by any
#: production path)
DEFAULT_PARAMS = {
    "alternatives_window_s": 300.0,
    "tape_window_s": 7 * DAY,
    "hurdle_quantile": 0.75,
    "min_hurdle_sample": 10,
    "eddie_history_bucket_s": 3600.0,
    "exposure_lookback_s": 14 * DAY,
    "lag_lookback_s": 60 * DAY,
    "karen_lookback_s": 30 * DAY,
    "allie_hurdle_lookback_s": 7 * DAY,
    "mark_points_max": 120,
    "max_decisions": 200,
    "capital_hour_step_usd": 50.0,
    "max_reviews_per_position": 50000,
    "max_fills_per_position": 20000,
    #: the row bound of each run-level snapshot (<= pit.MAX_SNAPSHOT_ROWS)
    "snapshot_max_rows": P.MAX_SNAPSHOT_ROWS,
}

FRESH = "FRESH_CURRENT_PROBABILITY"
SELL_ROLES = ("EXIT", "REDUCE", "STANDING_PROTECTION")
TERMINAL = ("FILLED", "PARTIALLY_FILLED", "EXPIRED", "CANCELED", "CANCELLED",
            "REJECTED")
#: the paper ledger's capital refusals (bettor_paper_ledger R_*, restated:
#: this module imports no ledger module)
R_CASH = "INSUFFICIENT_AVAILABLE_PAPER_CASH"
R_HEDGE = "AN_ENTRY_MAY_NOT_SPEND_THE_HEDGE_RESERVE"
CASH_REFUSALS = (R_CASH, R_HEDGE)
RAIL_REFUSALS = ("ABOVE_THE_PER_ORDER_CAP",
                 "ABOVE_THE_PER_MARKET_CONCENTRATION_CAP",
                 "ABOVE_THE_PER_FIXTURE_CONCENTRATION_CAP",
                 "ABOVE_THE_MAXIMUM_CONCURRENT_GROUPS")
CAPITAL_REFUSALS = CASH_REFUSALS + RAIL_REFUSALS
#: a refusal an ALLOCATOR could have changed (capital, not a hard rule on the
#: opportunity itself): such a refused opportunity is still admissible
ALLOCATION_ONLY_REFUSALS = CAPITAL_REFUSALS
R_ORDER_REFUSED = "PAPER_RISK_REFUSED_THE_ORDER"
CAP_KEYS = ("per_order_cap_usd", "per_market_cap_usd", "per_fixture_cap_usd",
            "max_concurrent_groups", "hedge_reserve_fraction")
#: the account under the owner's capital policy (bettor_paper_limits.
#: ACCOUNT_ID, restated: the replay imports no paper module -- pinned equal by
#: tests/test_r30_replay_units.py)
OWNER_POLICY_ACCOUNT = "paper_acct_main"
QUALIFIED, NOT_QUALIFIED, UNRECORDED = ("QUALIFIED", "NOT_QUALIFIED",
                                        "ECONOMICS_UNRECORDED")
DECISION_CONTENT = "DECISION_CONTENT"
EVIDENCE_MISSING = "REPLAY_EVIDENCE_MISSING"

num = IC.num
jl = IC.jload


def un(why, **extra) -> dict:
    return dict({"status": "UNAVAILABLE", "why": why}, **extra)


def _r(v, k=6):
    return None if v is None else round(float(v), k)


def quantile(xs, q):
    return AC.quantile(list(xs), q)


def _chunks(xs, n):
    xs = list(xs)
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


DEC_COLS = (
    "decision_id", "session_id", "account_id", "decided_at", "valuation_id",
    "us_market_slug", "holding_side", "intent", "fixture", "label",
    "verdict", "refusal", "refusals", "p_internal", "p_pinnacle",
    "p_blended", "pinnacle", "book_obs_id", "book", "proposed_qty",
    "limit_price", "economics", "policy_version", "policy_decision",
    "strategy", "provenance")
#: the tape needs only what econ_view and the qualification read
TAPE_COLS = (
    "decision_id", "account_id", "decided_at", "us_market_slug",
    "holding_side", "fixture", "verdict", "refusal", "refusals",
    "p_internal", "p_pinnacle", "p_blended", "proposed_qty", "limit_price",
    "economics", "policy_decision", "strategy")


def decision_clock(dec: dict) -> float:
    """The instant the decision was made AND durable."""
    return max(float(dec["decided_at"]), float(dec["__recorded_at"]))


# ═════════════════════════════════════════════════════════════════════
# PURE VIEWS OF A DECISION RECORD
# ═════════════════════════════════════════════════════════════════════

def _levels(acq: dict, cost, fees) -> list | None:
    """The decision's recorded book walk as [(qty, price, fee)] (the
    acquisition's walk; fees per level from its fee basis when it lines up,
    else the recorded total fee pro rata by cost). None when not recorded."""
    walk = acq.get("walk") if isinstance(acq, dict) else None
    if not isinstance(walk, list) or not walk:
        return None
    lv = []
    for w in walk:
        if not isinstance(w, dict):
            return None
        q, px = num(w.get("take")), num(w.get("price"))
        if q is None or px is None or q <= 0:
            return None
        lv.append([q, px, None])
    fb = acq.get("fee_basis")
    if isinstance(fb, list) and len(fb) == len(lv) and all(
            isinstance(f, dict) and num(f.get("fee_usd")) is not None
            and num(f.get("qty")) is not None
            and abs(num(f.get("qty")) - lv[i][0]) < 1e-6
            for i, f in enumerate(fb)):
        for i, f in enumerate(fb):
            lv[i][2] = num(f["fee_usd"])
    elif fees is not None and cost:
        tot = sum(q * px for q, px, _ in lv)
        for x in lv:
            x[2] = fees * (x[0] * x[1]) / tot if tot else 0.0
    else:
        return None
    return [tuple(x) for x in lv]


def econ_view(dec: dict) -> dict:
    """The decision's own executable economics: planned qty, plan price d,
    acquisition cost, fees, expected net after fees, the probability, the
    capital required (cost + fees, as R30 computes it), the capital per
    contract and the expected return per dollar r = (p - d) / d. `known` is
    False -- with `why_unknown` -- when any of net / capital / r was never
    recorded: that is NOT the same as a non-positive net."""
    e = jl(dec.get("economics")) or {}
    acq = e.get("acquisition") if isinstance(e, dict) else None
    acq = acq if isinstance(acq, dict) else {}
    p, p_basis = IA.decision_probability(dec)
    d, d_basis = IA.decision_price(dec)
    qty, cost = num(acq.get("qty")), num(acq.get("acquisition_cost_usd"))
    fees, net = num(acq.get("fees_usd")), num(acq.get("expected_net_profit_usd"))
    basis = "PAPER_DECISION_ECONOMICS_ACQUISITION" if net is not None else None
    if net is None:
        le = LC.decision_economics(dec)
        if le.get("computable"):
            net, basis = le["net"], le["basis"]
            qty = qty if qty is not None else le.get("qty")
            cost = cost if cost is not None else le.get("cost")
            fees = fees if fees is not None else le.get("fees")
    if qty is None:
        qty = num(dec.get("proposed_qty"))
    cost_basis = "RECORDED"
    if cost is None and qty and d is not None:
        cost, cost_basis = qty * d, "PLANNED_QTY_x_PLAN_PRICE"
    cap_req = None if cost is None else cost + (fees or 0.0)
    r = None if p is None or d is None or d <= 0 else (p - d) / d
    why = None
    if net is None:
        why = "DECISION_TIME_EXECUTABLE_NET_NOT_RECORDED"
    elif cap_req is None or cap_req <= 0:
        why = "DECISION_TIME_CAPITAL_REQUIRED_NOT_RECORDED"
    elif r is None:
        why = ("DECISION_CARRIES_NO_PROBABILITY" if p is None
               else "DECISION_CARRIES_NO_PLANNED_PRICE")
    known = why is None
    cpc = (cap_req / qty) if known and qty and qty > 0 else None
    return {"p": p, "p_basis": p_basis, "d": d, "d_basis": d_basis,
            "qty": qty, "cost": cost, "cost_basis": cost_basis, "fees": fees,
            "net": net, "net_basis": basis, "capital_required": cap_req,
            "capital_per_contract": cpc, "r": r,
            "roi": (None if net is None or not cap_req else net / cap_req),
            "known": known, "why_unknown": why,
            "positive": bool(known and net > 0),
            "levels": _levels(acq, cost, fees)}


def _codes(row: dict) -> set:
    out = set(row.get("refusals") or [])
    if row.get("refusal"):
        out.add(row["refusal"])
    return {c for c in out if c}


def admissible(row: dict) -> tuple:
    """Could an ALLOCATOR legally take this opportunity under the same hard
    rules? Derek ENTER = yes; a refusal for capital only = yes (another
    allocation could have funded it); any other refusal is a hard rule on
    the opportunity itself (stale probability past the 30 s rule, settlement
    not supported, entries switched off, already held, ...) = no."""
    if row.get("verdict") == "ENTER":
        return True, "POLICY_ADMITTED_ENTER"
    codes = _codes(row)
    if codes and codes <= set(ALLOCATION_ONLY_REFUSALS):
        return True, "REFUSED_FOR_CAPITAL_ONLY: %s" % ",".join(sorted(codes))
    return False, "HARD_RULE_REFUSAL: %s" % (
        row.get("refusal") or (sorted(codes)[0] if codes
                               else "NO_REFUSAL_CODE"))


def qualify(row: dict, e: dict) -> dict:
    ok, why = admissible(row)
    if not ok:
        return {"state": NOT_QUALIFIED, "why": why, "admissible": False}
    if not e["known"]:
        return {"state": UNRECORDED, "why": e["why_unknown"],
                "admissible": True}
    if e["net"] <= 0:
        return {"state": NOT_QUALIFIED, "admissible": True,
                "why": "NON_POSITIVE_EXECUTABLE_NET_AFTER_FEES (measured)"}
    return {"state": QUALIFIED, "why": why, "admissible": True}


def wire_of(dec: dict, limit):
    """The LONG-side wire price of the decision's limit (the venue quotes
    the LONG side; a SHORT pays 1 - wire)."""
    lp = num(limit)
    if lp is None:
        return None
    return lp if str(dec.get("holding_side")) != "SHORT" else 1.0 - lp


# ═════════════════════════════════════════════════════════════════════
# THE RUN CONTEXT: snapshots read once, answered per clock
# ═════════════════════════════════════════════════════════════════════

class RunContext:
    def __init__(self, reader: P.Reader, *, start: float, end: float,
                 params: dict | None = None, account_id: str | None = None):
        self.R = reader
        #: an account-scoped run sees only that account's opportunity tape
        #: (the opportunities available to THIS book); settlements stay
        #: market facts across accounts
        self.account_id = account_id
        self.start, self.end = float(start), float(end)
        self.params = dict(DEFAULT_PARAMS, **(params or {}))
        self.notes: dict = {}
        self._hist: dict = {}
        self._views: dict = {}
        self._econ: dict = {}
        self.present: dict = {}
        self.premap = self.theses = self.intents = None
        self.karen = self.karen_events = None
        self.karen_first_at = None

    async def prepare(self, decisions: list) -> None:
        R, p = self.R, self.params
        for t in ("karen_challenges", "karen_challenge_events",
                  "eddie_execution_estimates", "canonical_decision_intents",
                  "canonical_management_intents", "us_premap",
                  "xavier_entry_theses", "external_valuations",
                  "paper_audrey_findings"):
            self.present[t] = await R.has(t)
        accounts = sorted({d["account_id"] for d in decisions
                           if d.get("account_id")})
        bound = max(1, min(P.MAX_SNAPSHOT_ROWS, int(p["snapshot_max_rows"])))
        # THE TAPE covers [first replayed decision - tape window, horizon]
        # (decisions are replayed oldest first, the tape is read in recorded
        # order, so a bounded read covers the replayed decisions first; a
        # clock past what it covers is TAPE_TRUNCATED, never "none")
        first = min([float(d["decided_at"]) for d in decisions]
                    + [self.start])
        where = ("decided_at >= to_timestamp($1) AND decided_at <= "
                 "to_timestamp($2)")
        args: tuple = (first - p["tape_window_s"], self.end)
        if self.account_id:
            where += " AND account_id = $3"
            args = args + (self.account_id,)
        self.tape = await R.snapshot(
            "paper_decisions", until=self.end, cols=TAPE_COLS, where=where,
            args=args, id_col="decision_id", max_rows=bound)
        # settlements: market facts, every account, all history (positions
        # opened before the run need their settlements; the table is small)
        self.settlements = await R.snapshot(
            "paper_settlements", until=self.end,
            cols=("settlement_id", "account_id", "position_key", "version",
                  "group_id", "us_market_slug", "holding_side", "qty",
                  "outcome", "payout_per_contract", "settled_at"),
            id_col="settlement_id", max_rows=bound)
        slugs = sorted({r.get("us_market_slug") for r in
                        self.tape.at_covered(scope="_tape")
                        + self.settlements.at_covered(scope="_tape")
                        if r.get("us_market_slug")} |
                       {d.get("us_market_slug") for d in decisions
                        if d.get("us_market_slug")})
        if self.present["us_premap"] and slugs:
            self.premap = await R.snapshot(
                "us_premap", until=self.end,
                cols=("market_slug", "game_start", "team_league",
                      "sports_type", "event_slug", "updated_at",
                      "identifier", "side_norm"),
                where="market_slug = ANY($1::text[]) AND game_start IS NOT "
                      "NULL", args=(slugs,),
                id_col=("identifier", "side_norm"), max_rows=bound)
        if self.present["xavier_entry_theses"] and slugs:
            self.theses = await R.snapshot(
                "xavier_entry_theses", until=self.end,
                cols=("thesis_id", "us_market_slug", "event_start_at"),
                where="us_market_slug = ANY($1::text[]) AND event_start_at "
                      "IS NOT NULL", args=(slugs,), id_col="thesis_id", max_rows=bound)
        if self.present["canonical_decision_intents"]:
            self.intents = await R.snapshot(
                "canonical_decision_intents", until=self.end,
                cols=("intent_id", "decision_id", "created_at", "sleeve",
                      "allie"),
                where="sleeve = $1 AND created_at >= to_timestamp($2)",
                args=("INVESTMENT", first - p["allie_hurdle_lookback_s"]),
                id_col="intent_id", max_rows=bound)
        # fills of the replayed accounts, all history: position truth at any
        # clock needs every open position, however old
        self.fills = await R.snapshot(
            "paper_fills", until=self.end,
            cols=("fill_id", "account_id", "group_id", "role", "direction",
                  "holding_side", "us_market_slug", "fixture", "qty", "price",
                  "fee_usd", "gross_usd", "filled_at"),
            where="account_id = ANY($1::text[])", args=(accounts,),
            id_col="fill_id", max_rows=bound)
        if self.present["karen_challenges"]:
            first_k = await R.rows(
                "karen_challenges", clock=self.end, cols=("challenge_id",),
                order="created_at", limit=1, scope="_karen")
            self.karen_first_at = (first_k[0]["__recorded_at"] if first_k
                                   else None)
            lo = first - p["karen_lookback_s"]
            self.karen = await R.snapshot(
                "karen_challenges", until=self.end,
                cols=("challenge_id", "target_kind", "target_id",
                      "challenged_at", "severity", "detector"),
                where="challenged_at >= to_timestamp($1)", args=(lo,),
                id_col="challenge_id", max_rows=bound)
            if self.present["karen_challenge_events"]:
                self.karen_events = await R.snapshot(
                    "karen_challenge_events", until=self.end,
                    cols=("event_id", "challenge_id", "kind", "at"),
                    where="recorded_at >= to_timestamp($1)",
                    args=(lo - DAY,), id_col="event_id", max_rows=bound)
        for name, snap in (("tape", self.tape),
                           ("settlements", self.settlements),
                           ("premap", self.premap), ("theses", self.theses),
                           ("intents", self.intents), ("fills", self.fills),
                           ("karen", self.karen),
                           ("karen_events", self.karen_events)):
            if snap is not None and snap.truncated:
                self.notes["SNAPSHOT_TRUNCATED_%s" % name.upper()] = (
                    "%d rows read (the bound); complete only up to %.3f: a "
                    "clock after it reads the dependent component as "
                    "UNAVAILABLE" % (len(snap), snap.complete_until))

    # ── the per-clock view (event start, settlement lag) ─────────────
    def view(self, clock: float, scope: str) -> "ClockView":
        """The point-in-time indexes at ONE clock, built once (each read
        through a snapshot's `at`, audited in `scope`)."""
        key = (float(clock), scope)
        v = self._views.get(key)
        if v is None:
            if len(self._views) > 64:
                self._views.clear()
            v = self._views[key] = ClockView(self, float(clock), scope)
        return v

    def event_start(self, slug, clock, scope) -> tuple:
        return self.view(clock, scope).event_start(slug)

    def hours_to_release(self, dec, clock, scope) -> tuple:
        return self.view(clock, scope).hours_to_release(dec)

    def econ(self, row: dict) -> dict:
        """econ_view, cached per decision id (a tape row is immutable)."""
        k = row.get("decision_id")
        e = self._econ.get(k)
        if e is None:
            e = self._econ[k] = econ_view(row)
        return e

    # ── Eddie's history as of the clock ───────────────────────────────
    async def eddie_history(self, clock, scope) -> dict:
        """agents/eddie.history_stats rebuilt through the choke point at
        the bucket floor of the clock (never after it)."""
        from ..agents import eddie as E
        b = math.floor(clock / self.params["eddie_history_bucket_s"]) * \
            self.params["eddie_history_bucket_s"]
        if b in self._hist:
            # the cached read was made at b <= clock: audit it in this scope
            h, recs = self._hist[b]
            sc = self.R.scopes.setdefault(scope, P.Scope())
            for rec in recs:
                sc.max_recorded_at = (rec if sc.max_recorded_at is None else
                                      max(sc.max_recorded_at, rec))
            sc.max_clock = b if sc.max_clock is None else max(sc.max_clock, b)
            return h
        lo = b - E.HISTORY_LOOKBACK_S
        hscope = "_eddie_history:%d" % int(b)
        orders = await self.R.rows(
            "paper_orders", clock=b, scope=hscope,
            cols=("order_id", "order_type", "state", "created_at",
                  "decided_at", "terminal_at"),
            where="created_at >= to_timestamp($1)", args=(lo,),
            order="created_at DESC", limit=E.HISTORY_LIMIT)
        # THE QUEUE AT SUBMISSION: paper_orders.queue_ahead_qty is rewritten
        # in place by the simulator (hidden by the choke point); the
        # ACKNOWLEDGED order event records the queue the order was placed
        # behind, append-only
        queue: dict = {}
        for ids in _chunks([o["order_id"] for o in orders], 2000):
            for e in await self.R.rows(
                    "paper_order_events", clock=b, scope=hscope,
                    cols=("event_id", "order_id", "detail"),
                    where="order_id = ANY($1::text[]) AND kind = $2",
                    args=(ids, "ACKNOWLEDGED"), limit=P.MAX_LIMIT):
                q = num((jl(e.get("detail")) or {}).get("queue_ahead_qty"))
                if q is not None:
                    queue[e["order_id"]] = q
        fills = await self.R.rows(
            "paper_fills", clock=b, scope=hscope,
            cols=("fill_id", "order_id", "holding_side", "us_market_slug",
                  "price", "book_obs_id", "filled_at"),
            where="filled_at >= to_timestamp($1)", args=(lo,),
            order="filled_at DESC", limit=E.HISTORY_LIMIT)
        ords = {o["order_id"]: o for o in orders}
        first_fill: dict = {}
        for f in fills:
            if f["order_id"] in ords:
                first_fill[f["order_id"]] = min(
                    f["filled_at"], first_fill.get(f["order_id"],
                                                   f["filled_at"]))
        o_rows = [{"order_type": o["order_type"],
                   "state": o.get("state_at_clock"),
                   "queue_ahead_qty": queue.get(o["order_id"]),
                   "submit_s": (None if o.get("decided_at") is None else
                                o["created_at"] - o["decided_at"])}
                  for o in orders]
        f_rows = [{"order_type": ords[oid]["order_type"],
                   "ttf_s": t - ords[oid]["created_at"]}
                  for oid, t in first_fill.items()]
        # markouts: the fill's own book and the first book 30..300 s later,
        # both recorded by the bucket (bounded to the latest 200 fills)
        recent = [f for f in fills if f.get("book_obs_id") is not None
                  and f["order_id"] in ords][:200]
        markouts = []
        if recent:
            books = await self.R.rows(
                "paper_book_observations", clock=b, scope=hscope,
                cols=("obs_id", "us_market_slug", "observed_at", "bids",
                      "offers"),
                where="us_market_slug = ANY($1::text[]) AND observed_at >= "
                      "to_timestamp($2) AND error IS NULL",
                args=(sorted({f["us_market_slug"] for f in recent}),
                      min(f["filled_at"] for f in recent) - E.MARKOUT_TO_S),
                order="observed_at", limit=P.MAX_LIMIT)
            by_id = {bk["obs_id"]: bk for bk in books}
            by_slug: dict = {}
            for bk in books:
                by_slug.setdefault(bk["us_market_slug"], []).append(bk)
            for f in recent:
                b0 = by_id.get(f["book_obs_id"])
                b1 = next((bk for bk in by_slug.get(f["us_market_slug"], [])
                           if f["filled_at"] + E.MARKOUT_FROM_S
                           <= bk["observed_at"]
                           <= f["filled_at"] + E.MARKOUT_TO_S), None)
                if b0 is None or b1 is None:
                    continue
                m0 = E._mid_of(b0["bids"], b0["offers"], f["holding_side"])
                m1 = E._mid_of(b1["bids"], b1["offers"], f["holding_side"])
                if m0 is None or m1 is None:
                    continue
                markouts.append({"order_type": ords[f["order_id"]][
                    "order_type"], "markout_pp": m0 - m1,
                    "fill_id": f["fill_id"]})
        h = E.summarise_history(o_rows, f_rows, markouts)
        h["read_at"] = b
        h["basis"] = ("eddie.summarise_history on paper orders / fills / "
                      "books recorded by %.0f (the history bucket floor of "
                      "the decision clock); the queue at submission from the "
                      "ACKNOWLEDGED order events" % b)
        recs = [r for r in (self.R.scope(hscope).get("max_recorded_at"),)
                if r is not None]
        self._hist[b] = (h, recs)
        return await self.eddie_history(clock, scope)

    async def session_config(self, session_id, clock, scope) -> dict | None:
        """The paper session's configuration: a VERSIONED POLICY effective
        from its start (only a session started by the clock is visible)."""
        rows = await self.R.rows(
            "paper_sessions", clock=clock, scope=scope,
            cols=("session_id", "config", "started_at"),
            where="session_id = $1", args=(session_id,), limit=1)
        return (jl(rows[0]["config"]) or {}) if rows else None


class ClockView:
    """Event starts and the settlement lag AS OF ONE CLOCK."""

    def __init__(self, ctx: RunContext, clock: float, scope: str):
        self.clock = clock
        self.premap: dict = {}
        self.theses: dict = {}
        self.incomplete: list = []
        if ctx.premap is not None:
            try:
                for r in ctx.premap.at(clock, scope=scope):
                    k = r["market_slug"]
                    if r.get("game_start") is not None and (
                            k not in self.premap
                            or r["__recorded_at"] > self.premap[k][1]):
                        self.premap[k] = (r["game_start"], r["__recorded_at"])
            except P.SnapshotIncomplete as exc:
                self.incomplete.append(str(exc))
        if ctx.theses is not None:
            try:
                for r in ctx.theses.at(clock, scope=scope):
                    k = r["us_market_slug"]
                    if k not in self.theses or \
                            r["__recorded_at"] > self.theses[k][1]:
                        self.theses[k] = (r["event_start_at"],
                                          r["__recorded_at"])
            except P.SnapshotIncomplete as exc:
                self.incomplete.append(str(exc))
        self.lag_why = None
        try:
            self.settlements = ctx.settlements.at(clock, scope=scope)
        except P.SnapshotIncomplete as exc:
            self.settlements, self.lag_why = None, str(exc)
        samples = []
        if self.settlements is not None:
            lo = clock - ctx.params["lag_lookback_s"]
            first: dict = {}
            for s in self.settlements:
                if s.get("outcome") not in ("WON", "LOST") or \
                        s.get("settled_at") is None or s["settled_at"] < lo:
                    continue
                k = s["us_market_slug"]
                if k not in first or s["settled_at"] < first[k]["settled_at"]:
                    first[k] = s
            for slug, s in first.items():
                st, _ = self.event_start(slug)
                if st is not None:
                    samples.append((s["__recorded_at"], s["settled_at"] - st))
        self.lag_samples = samples
        if self.settlements is None:
            self.lag, self.lag_n = None, 0
        else:
            self.lag, self.lag_n = EC.settlement_lag(samples, as_of=clock)

    def event_start(self, slug) -> tuple:
        """(epoch, basis) of the market's event start from a row VISIBLE at
        the clock, else (None, why)."""
        if not slug:
            return None, "NO_MARKET_SLUG"
        if slug in self.premap:
            return self.premap[slug][0], (
                "us_premap.game_start (row last written at or before the "
                "clock)")
        if slug in self.theses:
            return self.theses[slug][0], (
                "xavier_entry_theses.event_start_at (an earlier entry on "
                "this market, recorded before the clock)")
        if self.incomplete:
            return None, ("EVENT_START_SNAPSHOT_TRUNCATED: %s"
                          % self.incomplete[0])
        return None, ("NO_EVENT_START_RECORDED_AT_OR_BEFORE_THE_CLOCK: "
                      "us_premap rows are rewritten in place (none was last "
                      "written before the clock) and no earlier entry thesis "
                      "on this market recorded one")

    def hours_to_release(self, dec) -> tuple:
        """(hours, basis | why, event start, lag, lag_n)."""
        st, st_basis = self.event_start(dec.get("us_market_slug"))
        if st is None:
            return None, st_basis, st, self.lag, self.lag_n
        if self.lag is None:
            return None, (self.lag_why or "SETTLEMENT_LAG_SAMPLE_%d_BELOW_%d"
                          % (self.lag_n, EC.MIN_LAG_SAMPLES)), st, self.lag, \
                self.lag_n
        h = max(0.0, st - float(dec["decided_at"]) + self.lag) / HOUR
        return (h if h > 0 else None,
                st_basis if h > 0 else "NON_POSITIVE_HOLD", st, self.lag,
                self.lag_n)


# ═════════════════════════════════════════════════════════════════════
# THE DECISION LIST
# ═════════════════════════════════════════════════════════════════════

async def load_decisions(R: P.Reader, *, start: float, end: float,
                         limit: int, account_id: str | None = None,
                         strategies=None, enter_only: bool = False) -> list:
    """The historical opportunities: paper decisions decided in [start,
    end] and recorded by the horizon, oldest first, bounded (optionally one
    account, some strategies, ENTER verdicts only)."""
    where = "decided_at >= to_timestamp($1) AND decided_at <= to_timestamp($2)"
    args: list = [start, end]
    if account_id:
        args.append(account_id)
        where += " AND account_id = $%d" % len(args)
    if strategies:
        args.append(sorted(strategies))
        where += " AND strategy = ANY($%d::text[])" % len(args)
    if enter_only:
        args.append("ENTER")
        where += " AND verdict = $%d" % len(args)
    return await R.rows("paper_decisions", clock=end, cols=DEC_COLS,
                        where=where, args=tuple(args),
                        order="decided_at, decision_id", limit=int(limit),
                        scope="_decisions")


# ═════════════════════════════════════════════════════════════════════
# THE COMPONENTS AT THE DECISION CLOCK
# ═════════════════════════════════════════════════════════════════════

async def valuation_chain(ctx: RunContext, dec, clock, scope) -> dict:
    out = {}
    vid = dec.get("valuation_id")
    pin = jl(dec.get("pinnacle")) or {}
    if vid is None or not ctx.present.get("external_valuations"):
        why = ("DECISION_RECORDS_NO_VALUATION_ID" if vid is None
               else "EXTERNAL_VALUATIONS_TABLE_ABSENT")
        for k in ("provider_observation", "bettor_receipt", "valuation"):
            out[k] = un(why)
        out["mapping"] = un(why)
        return out
    rows = await ctx.R.rows(
        "external_valuations", clock=clock, scope=scope,
        cols=("id", "version", "provider", "book", "devig_method", "venue",
              "condition_id", "contract_selection", "sport_family", "market",
              "period", "line", "settlement_rule", "event_key", "observed_at",
              "received_at", "age_s", "outcome_books", "overround",
              "mapped_outcome", "mapping_match", "probability",
              "executable_price", "cost_per_contract",
              "estimated_edge_per_contract", "decision", "admissible",
              "refusals", "payout_event", "payout_is_complement",
              "buy_intent", "contract_identity_basis",
              "settlement_comparison", "us_market_slug"),
        where="id = $1", args=(int(vid),), limit=1)
    if not rows:
        why = "VALUATION_ROW_NOT_RECORDED_AT_OR_BEFORE_THE_CLOCK"
        for k in ("provider_observation", "bettor_receipt", "valuation",
                  "mapping"):
            out[k] = un(why, valuation_id=vid)
        return out
    v = rows[0]
    out["provider_observation"] = {
        "status": "MEASURED", "valuation_id": v["id"],
        "provider": v["provider"], "book": v["book"],
        "observed_at": v["observed_at"], "age_s": v["age_s"],
        "outcome_books": v["outcome_books"], "overround": v["overround"],
        "devig_method": v["devig_method"], "probability": v["probability"],
        "decision_pinnacle": {k: pin.get(k) for k in (
            "observed_at", "received_at", "age_s", "limit_s",
            "qualification", "provider") if k in pin}}
    rec, obs = v.get("received_at"), v.get("observed_at")
    out["bettor_receipt"] = (
        {"status": "MEASURED", "received_at": rec,
         "receipt_latency_s": (None if rec is None or obs is None
                               else _r(rec - obs, 3)),
         "valuation_written_at": v["__recorded_at"]}
        if rec is not None else un("VALUATION_RECORDS_NO_RECEIPT_TIME"))
    st, st_basis = ctx.event_start(dec.get("us_market_slug"), clock, scope)
    out["mapping"] = {
        "status": "MEASURED", "us_market_slug": v.get("us_market_slug"),
        "condition_id": v.get("condition_id"),
        "contract_selection": v.get("contract_selection"),
        "mapped_outcome": v.get("mapped_outcome"),
        "mapping_match": v.get("mapping_match"),
        "payout_event": v.get("payout_event"),
        "payout_is_complement": v.get("payout_is_complement"),
        "buy_intent": v.get("buy_intent"),
        "identity_basis": v.get("contract_identity_basis"),
        "sport_family": v.get("sport_family"), "market": v.get("market"),
        "event_key": v.get("event_key"), "event_start_at": st,
        "event_start_basis": st_basis}
    out["valuation"] = {
        "status": "MEASURED", "valuation_id": v["id"],
        "version": v["version"], "probability": v["probability"],
        "executable_price": v["executable_price"],
        "cost_per_contract": v["cost_per_contract"],
        "estimated_edge_per_contract": v["estimated_edge_per_contract"],
        "decision": v["decision"], "admissible": v["admissible"],
        "refusals": v.get("refusals") or [],
        "settlement_rule": v.get("settlement_rule"),
        "settlement_comparison": jl(v.get("settlement_comparison")),
        "outcome_columns": "hidden by the choke point (their stamps are the "
                           "venue settlement time, not the write)"}
    out["_row"] = v
    return out


BOOK_COLS = ("obs_id", "us_market_slug", "observed_at", "bids", "offers",
             "tick", "market_state", "error")


async def venue_book(ctx: RunContext, dec, clock, scope) -> dict:
    row = None
    basis = None
    if dec.get("book_obs_id") is not None:
        got = await ctx.R.rows("paper_book_observations", clock=clock,
                               scope=scope, cols=BOOK_COLS,
                               where="obs_id = $1",
                               args=(int(dec["book_obs_id"]),), limit=1)
        if got:
            row, basis = got[0], "THE_DECISION'S_OWN_BOOK_OBSERVATION"
    if row is None and dec.get("us_market_slug"):
        got = await ctx.R.rows(
            "paper_book_observations", clock=clock, scope=scope,
            cols=BOOK_COLS,
            where="us_market_slug = $1 AND observed_at <= to_timestamp($2)",
            args=(dec["us_market_slug"], float(dec["decided_at"])),
            order="observed_at DESC", limit=1)
        if got:
            row, basis = got[0], "LATEST_BOOK_AT_OR_BEFORE_THE_DECISION"
    if row is None:
        return {"row": None, "component": un(
            "NO_BOOK_OBSERVATION_RECORDED_AT_OR_BEFORE_THE_CLOCK")}
    v = IC.book_view(row.get("bids"), row.get("offers"))
    return {"row": row, "component": {
        "status": "MEASURED" if not row.get("error") else "UNAVAILABLE",
        "why": None if not row.get("error") else "BOOK_READ_ERROR: %s"
        % row.get("error"),
        "obs_id": row["obs_id"], "observed_at": row["observed_at"],
        "age_at_decision_s": _r(float(dec["decided_at"])
                                - row["observed_at"], 3),
        "best_bid": v["best_bid"], "best_offer": v["best_offer"],
        "spread": v["spread"], "top5_depth_usd": _r(v["top5_depth_usd"], 2),
        "basis": basis}}


KAREN_RESOLVED = ("UPHELD", "REJECTED", "WITHDRAWN")


async def karen_at(ctx: RunContext, dec, clock, scope) -> dict:
    """Karen's challenges OPEN AT THE CLOCK on this market or strategy
    (canonical_components.karen_at_decision's rule, point in time). The
    state comes from karen_challenge_events recorded by the clock (the
    challenge row's own response / resolution stamps are caller-supplied
    and hidden by the choke point)."""
    if not ctx.present.get("karen_challenges"):
        return {"state": "UNAVAILABLE",
                "why": "KAREN_CHALLENGES_TABLE_ABSENT (migration 207)"}
    if ctx.karen_first_at is None or ctx.karen_first_at > clock + P.EPS_S:
        return {"state": "UNAVAILABLE",
                "why": ("NO_KAREN_RECORD_AT_OR_BEFORE_THE_CLOCK: Karen was "
                        "not recording challenges then; her review is not "
                        "invented")}
    if ctx.karen_events is None:
        return {"state": "UNAVAILABLE",
                "why": ("KAREN_CHALLENGE_EVENTS_TABLE_ABSENT: a challenge's "
                        "state at the clock is not reconstructable")}
    lb = ctx.params["karen_lookback_s"]
    try:
        rows = ctx.karen.at(clock, scope=scope,
                            since=("challenged_at", clock - lb))
        events = ctx.karen_events.at(clock, scope=scope)
    except P.SnapshotIncomplete as exc:
        return {"state": "UNAVAILABLE", "why": "KAREN_%s" % exc}
    rows = [r for r in rows if r["challenged_at"] <= clock + P.EPS_S]
    state: dict = {}
    for e in events:
        k = e["kind"]
        if k in KAREN_RESOLVED:
            state[e["challenge_id"]] = "RESOLVED"
        elif k == "RESPONDED" and state.get(e["challenge_id"]) != "RESOLVED":
            state[e["challenge_id"]] = "RESPONDED"
    open_ = [r for r in rows if state.get(r["challenge_id"]) is None]
    tids = sorted({r["target_id"] for r in open_
                   if r["target_kind"] == "paper_decisions"})
    tdec = {}
    for ids in _chunks(tids, P.MAX_LIMIT):
        for t in await ctx.R.rows(
                "paper_decisions", clock=clock, scope=scope,
                cols=("decision_id", "us_market_slug", "strategy"),
                where="decision_id = ANY($1::text[])", args=(ids,),
                limit=len(ids)):
            tdec[t["decision_id"]] = t
    on_m = sum(1 for r in open_ if (tdec.get(r["target_id"]) or {}).get(
        "us_market_slug") == dec.get("us_market_slug"))
    on_s = sum(1 for r in open_ if (tdec.get(r["target_id"]) or {}).get(
        "strategy") == dec.get("strategy"))
    return {"state": ("OPEN_CHALLENGES_ON_THIS_MARKET_OR_STRATEGY"
                      if (on_m or on_s) else
                      "NO_OPEN_CHALLENGE_ON_THIS_MARKET_OR_STRATEGY"),
            "open_on_market": on_m, "open_on_strategy": on_s,
            "open_total": len(open_),
            "authority": "CHALLENGE_ONLY_ZERO_AUTHORITY",
            "basis": "karen_challenges raised by the clock, OPEN unless a "
                     "RESPONDED / UPHELD / REJECTED / WITHDRAWN event was "
                     "recorded by the clock (karen_challenge_events)"}


def eddie_candidate(dec) -> dict:
    return {k: dec.get(k) for k in (
        "decision_id", "decided_at", "us_market_slug", "holding_side",
        "proposed_qty", "limit_price", "p_blended", "p_pinnacle",
        "p_internal", "book_obs_id", "economics")}


async def eddie_at(ctx: RunContext, dec, clock, book_row, scope) -> dict:
    """Eddie's estimate recorded by the clock, else the pure estimator on
    the point-in-time history (what R30 computes at the decision)."""
    from ..agents import eddie as E
    if ctx.present.get("eddie_execution_estimates"):
        got = await ctx.R.rows(
            "eddie_execution_estimates", clock=clock, scope=scope,
            cols=("estimate_id", "estimator_version", "recommendation",
                  "expected_net_executable_edge_pp",
                  "expected_executable_ev_usd", "expected_fill_probability",
                  "execution_style", "max_executable_qty", "unmeasured"),
            where="decision_id = $1", args=(dec["decision_id"],),
            order="created_at DESC", limit=1)
        if got:
            est = dict(got[0], unmeasured=jl(got[0].get("unmeasured")) or {})
            comp = CC.eddie_component(est)
            comp["replay_basis"] = "RECORDED_BY_THE_CLOCK"
            return comp
    hist = await ctx.eddie_history(clock, scope)
    est = E.estimate(eddie_candidate(dec), book_row, hist, now=clock)
    comp = CC.eddie_component(est)
    comp["replay_basis"] = ("RECOMPUTED: agents/eddie.estimate on the "
                            "decision, its book and the history as of the "
                            "clock")
    comp["history_read_at"] = hist.get("read_at")
    return comp


# ═════════════════════════════════════════════════════════════════════
# POSITION TRUTH, THE LEDGER AND THE HARD RAILS AT THE CLOCK
# ═════════════════════════════════════════════════════════════════════

def book_state_at(ctx: RunContext, dec, clock, scope, own_groups) -> dict:
    """OPEN EXPOSURE FROM POSITION TRUTH at the clock, the paper ledger's
    own rule (bettor_paper_ledger.positions: average cost incl. fees x open
    quantity, open = bought - sold - settled), from every fill and
    settlement of the account recorded by the clock -- not entry filled_qty
    x limit (red-team item 9). This decision's own groups are excluded."""
    acct = dec.get("account_id")
    try:
        fills = [f for f in ctx.fills.at(clock, scope=scope)
                 if f.get("account_id") == acct]
        setts = [s for s in ctx.settlements.at(clock, scope=scope)
                 if s.get("account_id") == acct]
    except P.SnapshotIncomplete as exc:
        return un("OPEN_EXPOSURE_UNMEASURED: %s" % exc)
    settled: dict = {}
    for s in setts:
        k = (s["group_id"], s["us_market_slug"], str(s["holding_side"]))
        if k not in settled or (s.get("version") or 0) > settled[k][0]:
            settled[k] = ((s.get("version") or 0), num(s.get("qty")) or 0.0)
    pos: dict = {}
    for f in fills:
        if f["group_id"] in own_groups:
            continue
        k = (f["group_id"], f["us_market_slug"], str(f["holding_side"]))
        st = pos.setdefault(k, {"bought": 0.0, "cost": 0.0, "sold": 0.0,
                                "fixture": f.get("fixture")})
        q = num(f["qty"]) or 0.0
        if str(f.get("direction")) == "BUY":
            gross = num(f.get("gross_usd"))
            if gross is None:
                gross = q * (num(f["price"]) or 0.0)
            st["bought"] += q
            st["cost"] += gross + (num(f.get("fee_usd")) or 0.0)
        else:
            st["sold"] += q
    by_market: dict = {}
    by_fixture: dict = {}
    groups: set = set()
    fixture_groups: set = set()
    book = 0.0
    for k, st in pos.items():
        open_q = st["bought"] - st["sold"] - settled.get(k, (0, 0.0))[1]
        if open_q <= 1e-9 or st["bought"] <= 0:
            continue
        basis = st["cost"] / st["bought"] * open_q
        book += basis
        by_market[k[1]] = by_market.get(k[1], 0.0) + basis
        if st.get("fixture"):
            by_fixture[st["fixture"]] = by_fixture.get(st["fixture"],
                                                       0.0) + basis
        groups.add(k[0])
        if dec.get("fixture") and st.get("fixture") == dec.get("fixture"):
            fixture_groups.add(k[0])
    fx = dec.get("fixture")
    return {"status": "MEASURED", "book_open_usd": book,
            "by_market": by_market, "by_fixture": by_fixture,
            "position_groups": groups,
            "fixture_open_usd": by_fixture.get(fx, 0.0) if fx else 0.0,
            "fixture_open_groups": len(fixture_groups),
            "basis": ("PAPER_FILLS_NET_OF_SALES_AND_SETTLEMENTS_RECORDED_BY_"
                      "THE_CLOCK (position truth: average cost incl. fees x "
                      "open quantity; this decision's own group excluded)")}


async def ledger_at(ctx: RunContext, dec, clock, scope, own_groups) -> dict:
    """cash / reserved / available AT THE CLOCK from the paper ledger: the
    running balances of the latest row recorded by the clock, minus this
    decision's own group's deltas recorded by then (its reservation is the
    allocation being decided)."""
    acct = dec.get("account_id")
    rows = await ctx.R.rows(
        "paper_ledger", clock=clock, scope=scope,
        cols=("seq", "cash_after_usd", "reserved_after_usd"),
        where="account_id = $1", args=(acct,),
        order="committed_at DESC, seq DESC", limit=1)
    if not rows:
        return un("NO_PAPER_LEDGER_ROW_RECORDED_AT_OR_BEFORE_THE_CLOCK")
    cash, res = num(rows[0]["cash_after_usd"]), num(rows[0][
        "reserved_after_usd"])
    if cash is None or res is None:
        return un("LEDGER_ROW_UNREADABLE")
    own_c = own_r = 0.0
    if own_groups:
        mine = await ctx.R.rows(
            "paper_ledger", clock=clock, scope=scope,
            cols=("seq", "cash_delta_usd", "reserved_delta_usd"),
            where="account_id = $1 AND group_id = ANY($2::text[])",
            args=(acct, sorted(own_groups)), limit=P.MAX_LIMIT)
        if len(mine) >= P.MAX_LIMIT:
            return un("OWN_GROUP_LEDGER_ROWS_TRUNCATED")
        own_c = sum(num(r["cash_delta_usd"]) or 0.0 for r in mine)
        own_r = sum(num(r["reserved_delta_usd"]) or 0.0 for r in mine)
    cash, res = cash - own_c, res - own_r
    return {"status": "MEASURED", "cash_usd": cash, "reserved_usd": res,
            "available_usd": cash - res,
            "basis": ("PAPER_LEDGER_RUNNING_BALANCE_AT_THE_CLOCK (seq %s) "
                      "minus this decision's own group" % rows[0]["seq"])}


def caps_at(dec: dict, cfg) -> tuple:
    """THE CAPS EFFECTIVE AT THE CLOCK: the capital policy RECORDED ON THE
    DECISION (paper_decisions.provenance.capital_policy, written with every
    decision since the owner policy, bettor_paper_limits) -- a versioned
    policy recorded at the time -- else the session configuration effective
    from its start. For the owner-policy account a decision whose
    provenance is absent or errored cannot say which applied: UNAVAILABLE."""
    prov = jl(dec.get("provenance"))
    cp = prov.get("capital_policy") if isinstance(prov, dict) else None
    if isinstance(cp, dict):
        return ({k: cp.get(k) for k in CAP_KEYS},
                "DECISION_PROVENANCE_CAPITAL_POLICY %s" % cp.get("version"))
    if dec.get("account_id") == OWNER_POLICY_ACCOUNT and (
            not isinstance(prov, dict) or prov.get("error")):
        return None, ("CAPITAL_POLICY_AT_THE_CLOCK_NOT_RECORDED: the decision "
                      "records no provenance, so whether the owner capital "
                      "policy or the session caps applied is unknown")
    if cfg is None:
        return None, "SESSION_CONFIGURATION_NOT_VISIBLE_AT_THE_CLOCK"
    risk = cfg.get("risk") or {}
    return ({k: risk.get(k) for k in CAP_KEYS},
            "SESSION_CONFIGURATION_EFFECTIVE_AT_THE_CLOCK")


async def open_orders_at(ctx: RunContext, dec, clock, scope,
                         own_groups) -> dict:
    """The account's orders OPEN AT THE CLOCK (created by it, terminal
    state not yet durable) and each open BUY's remaining reservation from
    the ledger rows recorded by the clock."""
    acct = dec.get("account_id")
    rows: list = []
    got = await ctx.R.paged(
        "paper_orders", clock=clock, scope=scope,
        cols=("order_id", "group_id", "direction", "us_market_slug",
              "fixture", "state", "terminal_at"),
        where="account_id = $1 AND created_at >= to_timestamp($2)",
        args=(acct, clock - ctx.params["exposure_lookback_s"]),
        id_col="order_id", on_page=rows.extend)
    if got["truncated"]:
        return un("OPEN_ORDERS_READ_TRUNCATED")
    open_ = [o for o in rows if o.get("state_at_clock") == "OPEN_AT_THE_CLOCK"
             and o["group_id"] not in own_groups]
    buys = [o["order_id"] for o in open_ if str(o.get("direction")) == "BUY"]
    rem: dict = {}
    for ids in _chunks(buys, 2000):
        lrows: list = []
        g = await ctx.R.paged(
            "paper_ledger", clock=clock, scope=scope,
            cols=("seq", "order_id", "reserved_delta_usd"),
            where="account_id = $1 AND order_id = ANY($2::text[])",
            args=(acct, ids), id_col="seq", on_page=lrows.extend)
        if g["truncated"]:
            return un("OPEN_ORDER_RESERVATIONS_READ_TRUNCATED")
        for r in lrows:
            rem[r["order_id"]] = rem.get(r["order_id"], 0.0) + (
                num(r["reserved_delta_usd"]) or 0.0)
    by_market: dict = {}
    by_fixture: dict = {}
    for o in open_:
        if str(o.get("direction")) != "BUY":
            continue
        x = max(0.0, rem.get(o["order_id"], 0.0))
        by_market[o["us_market_slug"]] = by_market.get(
            o["us_market_slug"], 0.0) + x
        if o.get("fixture"):
            by_fixture[o["fixture"]] = by_fixture.get(o["fixture"], 0.0) + x
    return {"status": "MEASURED", "by_market": by_market,
            "by_fixture": by_fixture,
            "groups": {o["group_id"] for o in open_},
            "basis": ("orders created within %.0f days and open at the "
                      "clock; remaining reservation from ledger rows "
                      "recorded by the clock" %
                      (ctx.params["exposure_lookback_s"] / DAY))}


class Rails:
    """THE HARD RISK RAILS AT ONE CLOCK, applied identically to every
    benchmark and to Allie (capital units: USD reserved, fees included)."""

    def __init__(self, *, caps, source, ledger, book, orders, why=None):
        self.caps, self.source = caps or {}, source
        self.ledger, self.book, self.orders = ledger, book, orders
        self.why = why
        self.status = "UNAVAILABLE" if why else "MEASURED"
        self.available = self.idle = self.keep = None
        self.slots = None
        if why:
            return
        cash, avail = ledger["cash_usd"], ledger["available_usd"]
        frac = num(self.caps.get("hedge_reserve_fraction")) or 0.0
        self.keep = cash * frac
        self.available = avail
        # the ledger refuses an entry when available - reserve < keep: the
        # most an entry may reserve is available - keep
        self.idle = max(0.0, avail - self.keep)
        mx = self.caps.get("max_concurrent_groups")
        if mx is not None:
            n = len(book["position_groups"] | orders["groups"])
            self.slots = max(0, int(mx) - n)

    def per_order(self):
        return num(self.caps.get("per_order_cap_usd"))

    def headroom(self, slug, fixture) -> dict:
        out = {}
        mc = num(self.caps.get("per_market_cap_usd"))
        if mc is not None:
            ex = self.book["by_market"].get(slug, 0.0) + \
                self.orders["by_market"].get(slug, 0.0)
            out["market_headroom_usd"] = max(0.0, mc - ex)
        fc = num(self.caps.get("per_fixture_cap_usd"))
        if fc is not None and fixture:
            ex = self.book["by_fixture"].get(fixture, 0.0) + \
                self.orders["by_fixture"].get(fixture, 0.0)
            out["fixture_headroom_usd"] = max(0.0, fc - ex)
        return out

    def for_opportunity(self, slug, fixture, capacity_usd=None) -> dict:
        """The V2 rails dict of one opportunity (None = not configured)."""
        if self.status != "MEASURED":
            return {"unmeasured": {"rails": self.why}}
        r = {"hard_rail_usd": self.per_order(),
             "idle_capital_usd": self.idle, "capacity_usd": capacity_usd,
             "group_slot_usd": (None if self.slots is None
                                else (None if self.slots > 0 else 0.0))}
        r.update(self.headroom(slug, fixture))
        return r

    def cap_for(self, slug, fixture, capital_required,
                capacity_usd=None) -> float:
        lim = [capital_required]
        for k, v in self.for_opportunity(slug, fixture,
                                         capacity_usd).items():
            if k != "unmeasured" and v is not None:
                lim.append(v)
        return max(0.0, min(lim))

    def as_dict(self) -> dict:
        return {"status": self.status, "why": self.why,
                "caps": self.caps, "caps_source": self.source,
                "available_usd": _r(self.available, 4),
                "hedge_reserve_keep_usd": _r(self.keep, 4),
                "usable_idle_usd": _r(self.idle, 4),
                "group_slots_left": self.slots,
                "ledger_basis": (self.ledger or {}).get("basis"),
                "exposure_basis": (self.book or {}).get("basis"),
                "orders_basis": (self.orders or {}).get("basis")}


async def rails_at(ctx: RunContext, dec, clock, scope, *, caps, source,
                   ledger, book, own_groups) -> Rails:
    """Build the rails; any CONFIGURED rail whose input is unmeasured makes
    the whole set UNAVAILABLE (a benchmark under fewer rails than the
    legacy sizing faced would not be 'identical rails')."""
    if caps is None:
        return Rails(caps=None, source=source, ledger=None, book=None,
                     orders=None, why="RAILS_UNMEASURED: %s" % source)
    if ledger.get("status") != "MEASURED":
        return Rails(caps=caps, source=source, ledger=None, book=None,
                     orders=None,
                     why="IDLE_CAPITAL_UNMEASURED_AT_THE_CLOCK: %s"
                     % ledger.get("why"))
    need_pos = any(caps.get(k) is not None for k in (
        "per_market_cap_usd", "per_fixture_cap_usd", "max_concurrent_groups"))
    if need_pos and book.get("status") != "MEASURED":
        return Rails(caps=caps, source=source, ledger=ledger, book=None,
                     orders=None, why="RAIL_INPUT_UNMEASURED: %s"
                     % book.get("why"))
    orders = {"by_market": {}, "by_fixture": {}, "groups": set(),
              "basis": "not needed (no concentration or group cap)"}
    if need_pos:
        orders = await open_orders_at(ctx, dec, clock, scope, own_groups)
        if orders.get("status") != "MEASURED":
            return Rails(caps=caps, source=source, ledger=ledger, book=book,
                         orders=None, why="RAIL_INPUT_UNMEASURED: %s"
                         % orders.get("why"))
    if book.get("status") != "MEASURED":
        book = {"by_market": {}, "by_fixture": {}, "position_groups": set()}
    return Rails(caps=caps, source=source, ledger=ledger, book=book,
                 orders=orders)


def allie_hurdle_sample(ctx: RunContext, clock, scope) -> list:
    """Allie's own hurdle sample (canonical_components.allie_at_decision):
    the latest 50 MEASURED INVESTMENT intents' adjusted profit per
    capital-hour created within 7 days and recorded by the clock."""
    if ctx.intents is None:
        return []
    try:
        rows = [r for r in ctx.intents.at(clock, scope=scope)
                if r.get("created_at") is not None and r["created_at"]
                > clock - ctx.params["allie_hurdle_lookback_s"]]
    except P.SnapshotIncomplete:
        return []
    rows.sort(key=lambda r: -r["created_at"])
    out = []
    for r in rows:
        a = jl(r.get("allie")) or {}
        if a.get("status") != "MEASURED":
            continue
        x = num((a.get("correlation_concentration") or {}).get(
            "adjusted_profit_per_capital_hour"))
        if x is not None:
            out.append(x)
        if len(out) >= 50:
            break
    return out


async def entry_orders(ctx: RunContext, dec, clock, scope) -> list:
    return await ctx.R.rows(
        "paper_orders", clock=clock, scope=scope,
        cols=("order_id", "group_id", "role", "direction", "holding_side",
              "intent", "us_market_slug", "fixture", "order_type",
              "time_in_force", "qty", "limit_price", "wire_price",
              "reserved_usd", "created_at", "decision_id", "state",
              "filled_qty", "terminal_at", "terminal_reason", "strategy"),
        where="decision_id = $1 AND role = $2",
        args=(dec["decision_id"], "ENTRY"), order="created_at", limit=20)


async def recorded_intent(ctx: RunContext, dec, clock, scope):
    if not ctx.present.get("canonical_decision_intents"):
        return None
    got = await ctx.R.rows(
        "canonical_decision_intents", clock=clock, scope=scope,
        cols=("intent_id", "content_sha", "holding_side", "order_intent",
              "order_type", "time_in_force", "limit_price", "wire_price",
              "target_qty", "sleeve", "strategy", "created_at", "allie",
              "eddie", "karen"),
        where="decision_id = $1", args=(dec["decision_id"],), limit=1)
    return got[0] if got else None


def canonical_intent_at(dec, *, ev, comps, cfg, orders, clock) -> dict:
    """THE R30 CANONICAL DECISION INTENT the chain builds for this
    decision (canonical_intent.build_decision_intent, pure), or why not --
    with the CAUSE: DECISION_CONTENT (the record itself cannot carry an
    intent: the R30 invariant then means no new INVESTMENT exposure) or
    REPLAY_EVIDENCE_MISSING (what the live chain had is not visible to the
    replay at the clock: UNAVAILABLE, never a zero)."""
    if dec.get("verdict") != "ENTER":
        return {"status": "NOT_APPLICABLE", "action": "NO_INTENT_REFUSE",
                "why": "R30 builds a canonical decision intent for an ENTER "
                       "only"}
    ent = (cfg or {}).get("entry") or {}
    o = orders[0] if orders else {}
    order_type = o.get("order_type") or ent.get("order_type")
    tif = o.get("time_in_force") or ent.get("time_in_force")
    if order_type is None or tif is None:
        return un("NO_ORDER_FORM_RECORDED_AT_THE_CLOCK: neither the entry "
                  "order nor the session configuration visible at the clock "
                  "names one (the live chain had its configuration; the "
                  "replay cannot see it)",
                  action="NO_CANONICAL_DECISION_INTENT",
                  cause=EVIDENCE_MISSING)
    limit = dec.get("limit_price") if dec.get("limit_price") is not None \
        else o.get("limit_price")
    wire = o.get("wire_price") if o.get("wire_price") is not None \
        else wire_of(dec, limit)
    pin = jl(dec.get("pinnacle")) or {}
    try:
        it = CI.build_decision_intent(
            decision_id=dec["decision_id"], strategy=dec.get("strategy"),
            strategy_version=dec.get("policy_version"),
            evidence={"valuation_id": dec.get("valuation_id"),
                      "book_obs_id": dec.get("book_obs_id"),
                      "pinnacle_observed_at": pin.get("observed_at"),
                      "probability": ev["p"], "replay": True},
            opportunity_score=comps["opportunity_score"],
            derek=comps["derek"], karen=comps["karen"], allie=comps["allie"],
            eddie=comps["eddie"], us_market_slug=dec.get("us_market_slug"),
            contract={"us_market_slug": dec.get("us_market_slug"),
                      "fixture": dec.get("fixture")},
            holding_side=dec.get("holding_side"),
            order_intent=dec.get("intent") or o.get("intent"),
            order_type=order_type, time_in_force=tif, limit_price=limit,
            wire_price=wire, target_qty=dec.get("proposed_qty"),
            sizing_basis={"rule": "the historical sized quantity (Allie is "
                          "SHADOW and does not resize)",
                          "per_order_cap_usd": ((cfg or {}).get("risk") or
                                                {}).get("per_order_cap_usd")},
            created_at=clock)
    except (ValueError, TypeError, KeyError) as exc:
        msg = str(exc)
        cause = DECISION_CONTENT
        if "wire price" in msg and dec.get("limit_price") is None \
                and not orders:
            cause = EVIDENCE_MISSING
        return un("CANONICAL_INTENT_NOT_BUILDABLE: %s" % msg,
                  action="NO_CANONICAL_DECISION_INTENT", cause=cause)
    return {"status": "BUILT", "action": "ENTER_WITH_CANONICAL_INTENT",
            "intent_id": it["intent_id"], "content_sha": it["content_sha"],
            "sleeve": it["sleeve"], "holding_side": it["holding_side"],
            "order_intent": it["order_intent"], "order_type": it["order_type"],
            "time_in_force": it["time_in_force"],
            "limit_price": _r(it["limit_price"]),
            "wire_price": _r(it["wire_price"]),
            "target_qty": _r(it["target_qty"]),
            "verifies": CI.verify_intent(it)}


def compare_intents(replayed: dict, recorded: dict | None) -> dict:
    if recorded is None:
        return {"state": "NO_INTENT_RECORDED_AT_THE_TIME",
                "why": "pre-R30 decision, or no canonical hook installed"}
    if replayed.get("status") != "BUILT":
        return {"state": "REPLAY_BUILT_NO_INTENT",
                "recorded_intent_id": recorded["intent_id"]}
    diffs = []
    for k in ("holding_side", "order_intent", "order_type", "time_in_force",
              "limit_price", "wire_price", "target_qty", "sleeve"):
        a, b = replayed.get(k), recorded.get(k)
        if isinstance(b, float) or isinstance(a, float):
            same = a is not None and b is not None and abs(
                float(a) - float(b)) < 1e-6
        else:
            same = a == b
        if not same:
            diffs.append({"field": k, "replayed": a, "recorded": b})
    return {"state": "REPRODUCED" if not diffs else "ORDER_FIELDS_DIFFER",
            "recorded_intent_id": recorded["intent_id"],
            "recorded_sha": recorded["content_sha"], "differences": diffs,
            "note": "components (Eddie, Allie, Karen) are recomputed at the "
                    "clock, so the content sha differs by construction; the "
                    "ORDER fields are what parity requires"}


def opportunity_at(dec, ev, eddie, idle, idle_why, lag_samples, start,
                   clock) -> dict:
    fp = eddie.get("expected_fill_probability") \
        if eddie.get("status") == "MEASURED" else None
    cand = {"candidate_id": dec["decision_id"], "decided_at":
            float(dec["decided_at"]), "strategy": dec.get("strategy"),
            "verdict": dec.get("verdict"),
            "us_market_slug": dec.get("us_market_slug"),
            "holding_side": dec.get("holding_side"),
            "executable_opportunity_dollars": ev["net"],
            "executable_capacity_usd": ev["cost"], "event_start_at": start,
            "status": "MEASURED"}
    got = SC.score(cand, fill_probability=fp,
                   fill_basis="EDDIE_ESTIMATE_AT_DECISION" if fp is not None
                   else None, fill_source="EDDIE" if fp is not None else None,
                   idle_capital_usd=idle,
                   idle_capital_why=None if idle is not None else idle_why,
                   lag_samples=lag_samples, ctx={})
    return {"status": got.get("status"), "version": SC.VERSION,
            "opportunity_score": got.get("opportunity_score"),
            "score_basis": got.get("score_basis"), "why": got.get("why"),
            "capital_hours": got.get("capital_hours"),
            "unmeasured": got.get("unmeasured") or {},
            "as_of": "THE_DECISION_CLOCK"}


# ═════════════════════════════════════════════════════════════════════
# THE TAPE: alternatives, hurdle, allocation benchmarks
# ═════════════════════════════════════════════════════════════════════

def tape_at(ctx: RunContext, dec, clock, scope) -> dict:
    """The pre-allocation qualified-opportunity tape at the clock, in the
    decision's sleeve: per market, the LATEST decision recorded by the
    clock (the opportunity as last assessed), classified QUALIFIED /
    NOT_QUALIFIED (hard rule, or a measured non-positive net) /
    ECONOMICS_UNRECORDED. UNAVAILABLE when the bounded tape does not cover
    the clock (TAPE_TRUNCATED) -- never a measured empty set."""
    T = ctx.params["tape_window_s"]
    W = ctx.params["alternatives_window_s"]
    sleeve = CI.sleeve_of(dec.get("strategy"))
    try:
        rows = ctx.tape.at(clock, scope=scope,
                           since=("decided_at", clock - T))
    except P.SnapshotIncomplete as exc:
        return {"status": "UNAVAILABLE", "why": "TAPE_TRUNCATED: %s" % exc,
                "alternatives": [], "hurdle_rows": [], "sleeve": sleeve}
    latest: dict = {}
    other_sleeve = 0
    for r in rows:
        if r["decision_id"] == dec["decision_id"]:
            continue
        t = r.get("decided_at")
        if t is None or t > clock + P.EPS_S:
            continue
        if CI.sleeve_of(r.get("strategy")) != sleeve:
            other_sleeve += 1
            continue
        key = r.get("us_market_slug") or r["decision_id"]
        if key not in latest or t > latest[key]["t"]:
            latest[key] = {"t": t, "r": r}
    alts, hurdle_rows = [], []
    counts = {"QUALIFIED": 0, "HARD_RULE_REFUSAL": 0,
              "NON_POSITIVE_NET": 0, "ECONOMICS_UNRECORDED": 0}
    unrec_alts = unrec_tape = 0
    for x in latest.values():
        e = ctx.econ(x["r"])
        q = qualify(x["r"], e)
        in_window = (x["t"] >= clock - W and x["r"].get("us_market_slug")
                     != dec.get("us_market_slug"))
        item = {"t": x["t"], "r": x["r"], "e": e, "q": q}
        if q["state"] == QUALIFIED:
            hurdle_rows.append(item)
            if in_window:
                alts.append(item)
                counts["QUALIFIED"] += 1
        elif q["state"] == UNRECORDED:
            unrec_tape += 1
            if in_window:
                unrec_alts += 1
                counts["ECONOMICS_UNRECORDED"] += 1
        elif in_window:
            counts["HARD_RULE_REFUSAL" if not q["admissible"]
                   else "NON_POSITIVE_NET"] += 1
    return {"status": "MEASURED", "alternatives": alts,
            "hurdle_rows": hurdle_rows, "sleeve": sleeve,
            "window_counts": counts, "unrecorded_alternatives": unrec_alts,
            "unrecorded_tape": unrec_tape, "other_sleeve_rows": other_sleeve}


def alternatives_input(tape: dict, window_s) -> dict:
    """The V2 alternatives block: MEASURED returns, or UNAVAILABLE when the
    tape does not cover the clock or an admissible alternative's economics
    were never recorded (then 'held none' would be an invented zero)."""
    if tape.get("status") != "MEASURED":
        return {"status": "UNAVAILABLE", "why": tape.get("why")}
    if tape["unrecorded_alternatives"]:
        return {"status": "UNAVAILABLE", "why": (
            "ALTERNATIVES_WITH_UNRECORDED_ECONOMICS_%d: admissible "
            "opportunities in the window whose executable net was never "
            "recorded" % tape["unrecorded_alternatives"])}
    return {"status": "MEASURED",
            "returns": [a["e"]["r"] for a in tape["alternatives"]],
            "basis": ("contemporaneous QUALIFIED alternatives in the %s "
                      "sleeve within %.0f s before the clock (hard-rule "
                      "refusals excluded: %s)" % (
                          tape["sleeve"], window_s,
                          tape["window_counts"]["HARD_RULE_REFUSAL"]))}


def hurdle_at(ctx: RunContext, tape: dict, clock, scope) -> dict:
    if tape.get("status") != "MEASURED":
        return {"value": None, "n": 0, "why": tape.get("why")}
    if tape["unrecorded_tape"]:
        return {"value": None, "n": 0, "why": (
            "HURDLE_TAPE_HAS_%d_ADMISSIBLE_ROWS_WITHOUT_RECORDED_ECONOMICS"
            % tape["unrecorded_tape"])}
    ppch = []
    missing = 0
    for h in tape["hurdle_rows"]:
        hrs, _why, *_ = ctx.hours_to_release(h["r"], clock, scope)
        cap = h["e"]["capital_required"]
        if hrs is None or not cap:
            missing += 1
            continue
        ppch.append(h["e"]["net"] / (cap * hrs))
    q = ctx.params["hurdle_quantile"]
    n_min = ctx.params["min_hurdle_sample"]
    if len(ppch) < n_min:
        return {"value": None, "n": len(ppch), "without_hours": missing,
                "why": "HURDLE_TAPE_SAMPLE_%d_BELOW_%d" % (len(ppch), n_min)}
    return {"value": quantile(ppch, q), "n": len(ppch),
            "without_hours": missing, "why": None,
            "basis": ("the %d%% quantile of the expected profit per "
                      "capital-hour of the %d qualified %s opportunities "
                      "(latest per market) on the pre-allocation tape within "
                      "%.0f days before the clock whose time to release is "
                      "measured (%d without one)" % (
                          int(q * 100), len(ppch), tape["sleeve"],
                          ctx.params["tape_window_s"] / DAY, missing))}


def tranches(oid, e, cap, hours, step_usd) -> tuple:
    """THE SHADOW TRANCHES of one opportunity within its rail cap: one per
    recorded book-walk level (capital = qty x price + fee, expected net =
    qty x (p - price) - fee: deeper levels earn less per dollar), else equal
    USD steps of the recorded economics (linear). Returns (tranches, basis).
    """
    from ..research_ref import marginal_capital_value as MCV
    out = []
    left = cap
    if e.get("levels"):
        basis = "BOOK_WALK_LEVELS"
        src = [(q * px + fee, q * (e["p"] - px) - fee)
               for q, px, fee in e["levels"]]
    else:
        basis = "EQUAL_USD_STEPS_OF_%.0f (no recorded walk)" % step_usd
        n = max(1, int(math.ceil(e["capital_required"] / step_usd)))
        c = e["capital_required"] / n
        src = [(c, e["net"] * c / e["capital_required"])] * n
    for i, (c, net) in enumerate(src):
        if left <= 1e-9 or c <= 0:
            break
        take = min(c, left)
        out.append(MCV.Tranche(
            opportunity_id=oid, tranche_id="%04d" % i, capital_usd=take,
            expected_net_usd=net * take / c,
            expected_hours_to_release=hours, fill_probability=1.0))
        left -= take
    return out, basis


def allocation_benchmarks(ctx: RunContext, dec, ev, qual, tape, *, rails,
                          eddie, allie, hours, hurdle, clock, scope) -> dict:
    """The benchmark allocations of THIS decision in CAPITAL units (USD
    reserved, fees included), every one under the SAME hard rails
    (`rails`):

      EQUAL_ALLOCATION      usable idle capital / N qualified opportunities
                            at the clock (this one + the contemporaneous
                            qualified alternatives)
      ROI_ONLY_RANKING      greedy by expected net / capital required
      CAPITAL_HOUR_RANKING  research_ref.marginal_capital_value.allocate as
                            a SHADOW tranche allocator: book-walk tranches,
                            the tape hurdle per capital-hour, the hedge
                            reserve as its reserve
      RESERVE_NO_ALLOCATION 0
      ALLIE                 allie_capital's final allocatable amount

    A missing input is UNAVAILABLE with its reason; a measured zero (the
    opportunity is not legally allocatable, or its net is non-positive)
    names its basis.
    """
    from ..research_ref import marginal_capital_value as MCV
    out = {"RESERVE_NO_ALLOCATION": {"usd": 0.0, "basis": "reserve: 0"},
           "ALLIE": ({"usd": num(allie.get("final_allocatable_usd")),
                      "basis": "allie_capital.allocate (SHADOW)"}
                     if allie.get("status") == "MEASURED" else
                     {"usd": None, "why": "ALLIE_UNMEASURED: %s" % (
                         allie.get("why") or allie.get("binding_constraint"))})}
    names = ("EQUAL_ALLOCATION", "ROI_ONLY_RANKING", "CAPITAL_HOUR_RANKING")

    def all_(usd, key, txt):
        for b in names:
            out[b] = {"usd": usd, key: txt}
        return out
    if qual["state"] == NOT_QUALIFIED:
        return all_(0.0, "basis", "NOT_ALLOCATABLE (measured): %s"
                    % qual["why"])
    if qual["state"] == UNRECORDED:
        return all_(None, "why", "%s: the benchmark allocation of an "
                    "opportunity whose economics were never recorded is "
                    "unknown, not zero" % qual["why"])
    if rails.status != "MEASURED":
        return all_(None, "why", rails.why)
    if tape.get("status") != "MEASURED":
        return all_(None, "why", tape.get("why"))
    if tape["unrecorded_alternatives"]:
        return all_(None, "why", "POOL_INCOMPLETE: %d admissible "
                    "alternatives without recorded economics"
                    % tape["unrecorded_alternatives"])
    cpc = ev.get("capital_per_contract")
    cap_me = num(eddie.get("max_executable_qty")) \
        if eddie.get("status") == "MEASURED" else None
    cap_me_usd = None if cap_me is None or cpc is None else cap_me * cpc
    me = {"id": dec["decision_id"], "e": ev, "hours": hours,
          "slug": dec.get("us_market_slug"), "fixture": dec.get("fixture"),
          "capacity": cap_me_usd}
    pool = [me] + [{"id": a["r"]["decision_id"], "e": a["e"],
                    "hours": ctx.hours_to_release(a["r"], clock, scope)[0],
                    "slug": a["r"].get("us_market_slug"),
                    "fixture": a["r"].get("fixture"), "capacity": None}
                   for a in tape["alternatives"]]

    def cap_of(o):
        return rails.cap_for(o["slug"], o["fixture"],
                             o["e"]["capital_required"], o["capacity"])
    out["EQUAL_ALLOCATION"] = {
        "usd": min(rails.idle / len(pool), cap_of(me)),
        "basis": "usable idle $%.2f (after the hedge reserve) / %d "
                 "qualified opportunities at the clock, rail-clamped"
                 % (rails.idle, len(pool))}
    left = rails.idle
    slots = rails.slots
    k_roi = 0.0
    for o in sorted(pool, key=lambda o: (-(o["e"]["roi"] or -1e9), o["id"])):
        if slots is not None and slots <= 0:
            break
        take = min(cap_of(o), max(0.0, left))
        if take > 0 and slots is not None:
            slots -= 1
        left -= take
        if o is me:
            k_roi = take
    out["ROI_ONLY_RANKING"] = {
        "usd": k_roi, "basis": "greedy by expected net / capital required "
        "over %d qualified opportunities, rail-clamped" % len(pool)}
    if hours is None:
        out["CAPITAL_HOUR_RANKING"] = {
            "usd": None, "why": "THIS_OPPORTUNITY'S_TIME_TO_RELEASE_"
            "UNMEASURED_AT_THE_CLOCK"}
        return out
    if hurdle.get("value") is None:
        out["CAPITAL_HOUR_RANKING"] = {
            "usd": None, "why": "HURDLE_UNAVAILABLE: %s" % hurdle.get("why")}
        return out
    tr, dropped, bases = [], 0, set()
    for o in pool:
        c = cap_of(o)
        if o["hours"] is None or c <= 0:
            dropped += 1
            continue
        got, b = tranches(o["id"], o["e"], c, o["hours"],
                          ctx.params["capital_hour_step_usd"])
        tr.extend(got)
        bases.add(b.split(" ")[0])
    alloc = MCV.allocate(tr, rails.available, reserve_usd=rails.keep,
                         hurdle_ppch=hurdle["value"])
    k = sum(t.capital_usd for t in alloc["chosen"] if t.opportunity_id
            == me["id"])
    out["CAPITAL_HOUR_RANKING"] = {
        "usd": k, "basis": ("research_ref.marginal_capital_value.allocate "
                            "(RESEARCH_SHADOW_ONLY) over %d tranches (%s) of "
                            "%d qualified opportunities (%d without a time "
                            "to release left out), hurdle %.3g per "
                            "capital-hour (the tape), reserve $%.2f (the "
                            "hedge reserve); fill probability 1.0 for every "
                            "tranche alike" % (
                                len(tr), "/".join(sorted(bases)) or "-",
                                len(pool), dropped, hurdle["value"],
                                rails.keep or 0.0)),
        "tranches_chosen": sum(1 for t in alloc["chosen"]
                               if t.opportunity_id == me["id"]),
        "rejections": sorted({w for t, w in alloc["rejected"]
                              if t.opportunity_id == me["id"]}),
        "authority": alloc["authority"]}
    return out


# ═════════════════════════════════════════════════════════════════════
# THE OUTCOME AT THE HORIZON
# ═════════════════════════════════════════════════════════════════════

async def outcome_at_horizon(ctx: RunContext, dec, scope,
                             review_fn=None) -> dict:
    """The PAPER execution, Xavier's reviews (paged, compacted by
    `review_fn` page by page), the canonical management intents, the
    settlements and the release, read at the horizon. A per-position bound
    that stops a read is recorded (`*_truncated`), never silent."""
    R, H = ctx.R, ctx.end
    orders = await entry_orders(ctx, dec, H, scope)
    gids = sorted({o["group_id"] for o in orders})
    out = {"orders": orders, "group_ids": gids, "fills": [], "reviews": [],
           "mgmt_intents": {}, "settlements": [], "refusal": None,
           "fills_truncated": False, "reviews_truncated": False,
           "mgmt_intents_truncated": False, "settlements_why": None}
    if not orders and dec.get("verdict") == "ENTER" and \
            ctx.present.get("paper_audrey_findings"):
        f = await R.rows("paper_audrey_findings", clock=H, scope=scope,
                         cols=("finding_id", "kind", "subject", "detail",
                               "found_at"),
                         where="kind = $1 AND subject = $2",
                         args=(R_ORDER_REFUSED, dec["decision_id"]),
                         limit=1)
        if f:
            det = jl(f[0].get("detail")) or {}
            code = det.get("refusal")
            out["refusal"] = {"code": code,
                              "found_at": f[0].get("found_at"),
                              "capital": code in CAPITAL_REFUSALS,
                              "cash": code in CASH_REFUSALS}
    if not gids:
        return out
    fills: list = []
    g = await R.paged(
        "paper_fills", clock=H, scope=scope,
        cols=("fill_id", "order_id", "group_id", "role", "direction",
              "holding_side", "us_market_slug", "qty", "price", "fee_usd",
              "gross_usd", "filled_at"),
        where="group_id = ANY($1::text[])", args=(gids,), id_col="fill_id",
        max_rows=int(ctx.params["max_fills_per_position"]),
        on_page=fills.extend)
    out["fills"] = sorted(fills, key=lambda f: (f["filled_at"],
                                                f["fill_id"]))
    out["fills_truncated"] = g["truncated"]
    if ctx.present.get("canonical_management_intents"):
        mi: dict = {}

        def keep_intents(rows):
            for r in rows:
                if r.get("review_id"):
                    mi[r["review_id"]] = r.get("action")
        g = await R.paged(
            "canonical_management_intents", clock=H, scope=scope,
            cols=("intent_id", "review_id", "action"),
            where="group_id = ANY($1::text[])", args=(gids,),
            id_col="intent_id",
            max_rows=int(ctx.params["max_reviews_per_position"]),
            on_page=keep_intents)
        out["mgmt_intents"] = mi
        out["mgmt_intents_truncated"] = g["truncated"]
    compact: list = []

    def keep_reviews(rows):
        for r in rows:
            compact.append(review_fn(r, out["mgmt_intents"]) if review_fn
                           else r)
    g = await R.paged(
        "paper_xavier_reviews", clock=H, scope=scope,
        cols=("review_id", "group_id", "reviewed_at", "trigger",
              "recommendation", "refusal", "alternatives", "selection",
              "exposure", "standing", "measure", "action"),
        where="group_id = ANY($1::text[])", args=(gids,), id_col="review_id",
        max_rows=int(ctx.params["max_reviews_per_position"]),
        on_page=keep_reviews)
    out["reviews"] = sorted(compact, key=lambda r: (
        r.get("reviewed_at") or 0, r.get("review_id")))
    out["reviews_truncated"] = g["truncated"]
    try:
        out["settlements"] = [s for s in ctx.settlements.at(H, scope=scope)
                              if s["group_id"] in gids]
    except P.SnapshotIncomplete as exc:
        out["settlements_why"] = str(exc)
    return out


def contract_settlement(ctx: RunContext, slug, side, scope) -> tuple:
    """(settlement | None, why): the market's own settlement (any group,
    WON/LOST/VOID_REFUND), recorded by the horizon -- the contract's
    outcome for a position that left before settling."""
    try:
        rows = ctx.settlements.at(ctx.end, scope=scope)
    except P.SnapshotIncomplete as exc:
        return None, str(exc)
    best = None
    for s in rows:
        if s["us_market_slug"] != slug or s.get("outcome") not in (
                "WON", "LOST", "VOID_REFUND"):
            continue
        same = str(s.get("holding_side")) == str(side)
        key = (same, s.get("settled_at") or 0, s.get("version") or 0)
        if best is None or key > best[0]:
            best = (key, s)
    return (None if best is None else best[1]), None


async def marks(ctx: RunContext, slug, t0, t1, scope) -> tuple:
    """(book observations of the market between first fill and release
    recorded by the horizon, evenly thinned to mark_points_max; truncated)
    """
    if not slug or t0 is None:
        return [], False
    rows = await ctx.R.rows(
        "paper_book_observations", clock=ctx.end, scope=scope,
        cols=("observed_at", "bids", "offers"),
        where="us_market_slug = $1 AND observed_at >= to_timestamp($2) AND "
              "observed_at <= to_timestamp($3) AND error IS NULL",
        args=(slug, float(t0), float(t1 if t1 is not None else ctx.end)),
        order="observed_at", limit=P.MAX_LIMIT)
    truncated = len(rows) >= P.MAX_LIMIT
    m = int(ctx.params["mark_points_max"])
    if len(rows) > m:
        step = len(rows) / float(m)
        rows = [rows[int(i * step)] for i in range(m)]
    # the mark in force at the first fill: the latest book before it
    prior = await ctx.R.rows(
        "paper_book_observations", clock=ctx.end, scope=scope,
        cols=("observed_at", "bids", "offers"),
        where="us_market_slug = $1 AND observed_at < to_timestamp($2) AND "
              "error IS NULL", args=(slug, float(t0)),
        order="observed_at DESC", limit=1)
    if truncated:
        ctx.notes["MARKS_TRUNCATED_%s" % slug] = (
            "more than %d book observations while held: the marked drawdown "
            "of this position is UNAVAILABLE" % P.MAX_LIMIT)
    return prior + rows, truncated

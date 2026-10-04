"""THE R30 CHAIN AT EACH DECISION CLOCK, AND THE TIMELINE AFTER IT.

Every read goes through the choke point (pit.Reader); nothing here holds a
query of its own. Two clocks per opportunity:

  DECISION CLOCK  max(decided_at, the decision row's recorded_at): the
                  instant the decision was made AND durable. Every input of
                  the replayed R30 chain (valuation, book, Karen, Eddie's
                  history, Allie's inputs, the alternatives, the hurdle
                  tape, the session's configuration) is read AT this clock:
                  only rows recorded at or before it. The audit scope
                  "<decision_id>:DECISION" carries the latest recorded stamp
                  used; the database CHECKs it is <= the decision clock.
  HORIZON         the run's clock_end: the PAPER execution, Xavier's
                  reviews, the settlements and the marks are read at the
                  horizon (scope "<decision_id>:OUTCOME"), never later.

THE COMPONENTS (each MEASURED or UNAVAILABLE with its reason):

  provider_observation  external_valuations: provider, book, observed_at,
                        age, outcome books, probability
  bettor_receipt        received_at and the receipt latency
  mapping               the contract identity the resolver bound (mapped
                        outcome, payout event / complement, condition id)
                        and the pre-map row VISIBLE at the clock
  valuation             probability, executable price, edge, admissible,
                        refusals, settlement comparison
  venue_book            the decision's own book observation (else the
                        latest at or before the decision), age, top of book
  derek                 canonical_components.derek_component on the
                        decision record (the R30 builder)
  karen                 Karen's challenges OPEN AT THE CLOCK on this market
                        or strategy (state rebuilt from the challenge's own
                        stamps); no Karen record at or before the clock ->
                        UNAVAILABLE (Karen was not recording then)
  eddie                 Eddie's estimate recorded by the clock, else
                        agents/eddie.estimate (pure) on the decision, its
                        book and Eddie's history AS OF the clock (floored to
                        the history bucket, never after it), with the hard
                        rule (canonical_components.eddie_component)
  allie                 allie_capital.allocate on point-in-time inputs:
                        event start (pre-map row or an earlier entry thesis
                        visible at the clock), the settlement lags recorded
                        before the clock, open exposure from the paper
                        FILLS net of sales and settlements recorded by the
                        clock (position truth, not entry nominal), idle
                        capital from the paper LEDGER at the clock (this
                        decision's own reservation excluded), the recent
                        canonical INVESTMENT intents recorded by the clock
                        (Allie's own hurdle sample) and the session's per-
                        order rail. Authority stays
                        SHADOW_PENDING_OWNER_APPROVAL.
  opportunity_score     lost_opportunity.score.score (pure) as R30 computes
                        it at the decision
  canonical_intent      canonical_intent.build_decision_intent (pure) for an
                        ENTER; the order form from the session configuration
                        effective at the clock; compared with the intent
                        recorded at the time, when one exists
  alternatives          the contemporaneous qualified alternatives (other
                        markets, decided within the window before the clock,
                        positive executable net after fees) -- the
                        pre-allocation tape, refused and unallocated
                        opportunities included
  hurdle                the HURDLE_QUANTILE of the tape's expected profit per
                        capital-hour over the tape window (not survivor
                        biased: every qualified opportunity, not only the
                        selected intents)

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

VERSION = "R30_REPLAY_RECONSTRUCT_V1"
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
}

FRESH = "FRESH_CURRENT_PROBABILITY"
SELL_ROLES = ("EXIT", "REDUCE", "STANDING_PROTECTION")
TERMINAL = ("FILLED", "PARTIALLY_FILLED", "EXPIRED", "CANCELED", "CANCELLED",
            "REJECTED")
#: the paper ledger's capital refusals (live_parity.PAPER_CAPITAL_REFUSALS,
#: restated: this module imports no execution module)
CAPITAL_REFUSALS = (
    "INSUFFICIENT_AVAILABLE_PAPER_CASH", "AN_ENTRY_MAY_NOT_SPEND_THE_HEDGE_"
    "RESERVE", "ABOVE_THE_PER_ORDER_CAP", "ABOVE_THE_PER_MARKET_CONCENTRATION"
    "_CAP", "ABOVE_THE_PER_FIXTURE_CONCENTRATION_CAP",
    "ABOVE_THE_MAXIMUM_CONCURRENT_GROUPS")
R_ORDER_REFUSED = "PAPER_RISK_REFUSED_THE_ORDER"

num = IC.num
jl = IC.jload


def un(why, **extra) -> dict:
    return dict({"status": "UNAVAILABLE", "why": why}, **extra)


def _r(v, k=6):
    return None if v is None else round(float(v), k)


def quantile(xs, q):
    return AC.quantile(list(xs), q)


DEC_COLS = (
    "decision_id", "session_id", "account_id", "decided_at", "valuation_id",
    "us_market_slug", "holding_side", "intent", "fixture", "label",
    "verdict", "refusal", "refusals", "p_internal", "p_pinnacle",
    "p_blended", "pinnacle", "book_obs_id", "book", "proposed_qty",
    "limit_price", "economics", "policy_version", "policy_decision",
    "strategy")
TAPE_COLS = (
    "decision_id", "account_id", "decided_at", "us_market_slug",
    "holding_side", "fixture", "verdict", "refusal", "refusals",
    "p_internal", "p_pinnacle", "p_blended", "proposed_qty", "limit_price",
    "economics", "policy_decision", "strategy", "book", "pinnacle")


def decision_clock(dec: dict) -> float:
    """The instant the decision was made AND durable."""
    return max(float(dec["decided_at"]), float(dec["__recorded_at"]))


# ═════════════════════════════════════════════════════════════════════
# PURE VIEWS OF A DECISION RECORD
# ═════════════════════════════════════════════════════════════════════

def econ_view(dec: dict) -> dict:
    """The decision's own executable economics: planned qty, plan price d,
    acquisition cost, fees, expected net after fees, the probability, the
    capital required (cost + fees, as R30 computes it) and the expected
    return per dollar r = (p - d) / d."""
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
    return {"p": p, "p_basis": p_basis, "d": d, "d_basis": d_basis,
            "qty": qty, "cost": cost, "cost_basis": cost_basis, "fees": fees,
            "net": net, "net_basis": basis, "capital_required": cap_req,
            "r": r, "roi": (None if net is None or not cap_req
                            else net / cap_req),
            "qualified": bool(net is not None and net > 0 and r is not None)}


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

    async def prepare(self, decisions: list) -> None:
        R, p = self.R, self.params
        for t in ("karen_challenges", "eddie_execution_estimates",
                  "canonical_decision_intents", "canonical_management_intents",
                  "us_premap", "xavier_entry_theses", "external_valuations",
                  "paper_audrey_findings"):
            self.present[t] = await R.has(t)
        accounts = sorted({d["account_id"] for d in decisions
                           if d.get("account_id")})
        where = ("decided_at >= to_timestamp($1) AND decided_at <= "
                 "to_timestamp($2)")
        args: tuple = (self.start - p["tape_window_s"], self.end)
        if self.account_id:
            where += " AND account_id = $3"
            args = args + (self.account_id,)
        self.tape = await R.snapshot(
            "paper_decisions", until=self.end, cols=TAPE_COLS, where=where,
            args=args, order="decided_at DESC")
        self.settlements = await R.snapshot(
            "paper_settlements", until=self.end,
            cols=("settlement_id", "account_id", "position_key", "version",
                  "group_id", "us_market_slug", "holding_side", "qty",
                  "outcome", "payout_per_contract", "settled_at"),
            where="settled_at >= to_timestamp($1)",
            args=(self.start - p["lag_lookback_s"],),
            order="settled_at DESC")
        slugs = sorted({r.get("us_market_slug") for r in
                        self.tape.at(self.end, scope="_tape")
                        + self.settlements.at(self.end, scope="_tape")
                        if r.get("us_market_slug")} |
                       {d.get("us_market_slug") for d in decisions
                        if d.get("us_market_slug")})
        self.premap = None
        if self.present["us_premap"] and slugs:
            self.premap = await R.snapshot(
                "us_premap", until=self.end,
                cols=("market_slug", "game_start", "team_league",
                      "sports_type", "event_slug", "updated_at"),
                where="market_slug = ANY($1::text[]) AND game_start IS NOT "
                      "NULL", args=(slugs,), order="updated_at DESC")
        self.theses = None
        if self.present["xavier_entry_theses"] and slugs:
            self.theses = await R.snapshot(
                "xavier_entry_theses", until=self.end,
                cols=("thesis_id", "us_market_slug", "event_start_at"),
                where="us_market_slug = ANY($1::text[]) AND event_start_at "
                      "IS NOT NULL", args=(slugs,), order="recorded_at DESC")
        self.intents = None
        if self.present["canonical_decision_intents"]:
            self.intents = await R.snapshot(
                "canonical_decision_intents", until=self.end,
                cols=("intent_id", "decision_id", "created_at", "sleeve",
                      "allie"),
                where="sleeve = 'INVESTMENT' AND created_at >= "
                      "to_timestamp($1)",
                args=(self.start - p["allie_hurdle_lookback_s"],),
                order="created_at DESC")
        self.fills = await R.snapshot(
            "paper_fills", until=self.end,
            cols=("fill_id", "account_id", "group_id", "role", "direction",
                  "holding_side", "us_market_slug", "fixture", "qty", "price",
                  "fee_usd", "filled_at"),
            where="account_id = ANY($1::text[]) AND filled_at >= "
                  "to_timestamp($2)",
            args=(accounts, self.start - p["exposure_lookback_s"]),
            order="filled_at DESC")
        for name, snap in (("tape", self.tape),
                           ("settlements", self.settlements),
                           ("premap", self.premap), ("theses", self.theses),
                           ("intents", self.intents), ("fills", self.fills)):
            if snap is not None and snap.truncated:
                self.notes["SNAPSHOT_TRUNCATED_%s" % name.upper()] = (
                    "%d rows read (the bound); older rows of the window are "
                    "absent from this run" % len(snap))

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
            cols=("order_id", "order_type", "state", "queue_ahead_qty",
                  "created_at", "decided_at", "terminal_at"),
            where="created_at >= to_timestamp($1)", args=(lo,),
            order="created_at DESC", limit=E.HISTORY_LIMIT)
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
                   "queue_ahead_qty": o.get("queue_ahead_qty"),
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
                      "the decision clock)" % b)
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
        if ctx.premap is not None:
            for r in ctx.premap.at(clock, scope=scope):
                k = r["market_slug"]
                if r.get("game_start") is not None and (
                        k not in self.premap
                        or r["__recorded_at"] > self.premap[k][1]):
                    self.premap[k] = (r["game_start"], r["__recorded_at"])
        if ctx.theses is not None:
            for r in ctx.theses.at(clock, scope=scope):
                k = r["us_market_slug"]
                if k not in self.theses or \
                        r["__recorded_at"] > self.theses[k][1]:
                    self.theses[k] = (r["event_start_at"], r["__recorded_at"])
        self.settlements = ctx.settlements.at(clock, scope=scope)
        first: dict = {}
        for s in self.settlements:
            if s.get("outcome") not in ("WON", "LOST"):
                continue
            k = s["us_market_slug"]
            if k not in first or s["settled_at"] < first[k]["settled_at"]:
                first[k] = s
        samples = []
        for slug, s in first.items():
            st, _ = self.event_start(slug)
            if st is not None:
                samples.append((s["__recorded_at"], s["settled_at"] - st))
        self.lag_samples = samples
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
            return None, ("SETTLEMENT_LAG_SAMPLE_%d_BELOW_%d" % (
                self.lag_n, EC.MIN_LAG_SAMPLES)), st, self.lag, self.lag_n
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
        where += " AND verdict = 'ENTER'"
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
        "outcome_columns": "masked until their own stamps (pit registry)"}
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


async def karen_at(ctx: RunContext, dec, clock, scope) -> dict:
    """Karen's challenges OPEN AT THE CLOCK on this market or strategy
    (canonical_components.karen_at_decision's rule, point in time)."""
    if not ctx.present.get("karen_challenges"):
        return {"state": "UNAVAILABLE",
                "why": "KAREN_CHALLENGES_TABLE_ABSENT (migration 207)"}
    any_row = await ctx.R.rows("karen_challenges", clock=clock, scope=scope,
                               cols=("challenge_id",), limit=1)
    if not any_row:
        return {"state": "UNAVAILABLE",
                "why": ("NO_KAREN_RECORD_AT_OR_BEFORE_THE_CLOCK: Karen was "
                        "not recording challenges then; her review is not "
                        "invented")}
    rows = await ctx.R.rows(
        "karen_challenges", clock=clock, scope=scope,
        cols=("challenge_id", "target_kind", "target_id", "challenged_at",
              "severity", "detector", "responded_at", "resolved_at",
              "outcome"),
        where="challenged_at <= to_timestamp($1) AND challenged_at >= "
              "to_timestamp($2)",
        args=(clock, clock - ctx.params["karen_lookback_s"]),
        order="challenged_at DESC", limit=2000)
    open_ = [r for r in rows if r["state_at_clock"] == "OPEN"]
    tids = sorted({r["target_id"] for r in open_
                   if r["target_kind"] == "paper_decisions"})
    tdec = {}
    if tids:
        for t in await ctx.R.rows(
                "paper_decisions", clock=clock, scope=scope,
                cols=("decision_id", "us_market_slug", "strategy"),
                where="decision_id = ANY($1::text[])", args=(tids,),
                limit=len(tids)):
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
            "basis": "karen_challenges OPEN AT THE CLOCK (state rebuilt from "
                     "responded_at / resolved_at <= the clock)"}


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


def exposure_at(ctx: RunContext, dec, clock, scope) -> dict:
    """OPEN EXPOSURE FROM POSITION TRUTH at the clock: per group, the cost
    basis of the entry fills net of sales (average cost) and of settled
    positions, from fills and settlements recorded by the clock -- not
    entry filled_qty x limit (red-team item 9). This decision's own group
    is excluded (its order is the one being allocated)."""
    acct = dec.get("account_id")
    fills = [f for f in ctx.fills.at(clock, scope=scope)
             if f.get("account_id") == acct]
    settled = {(s["group_id"], s["us_market_slug"], str(s["holding_side"]))
               for s in ctx.settlements.at(clock, scope=scope)}
    pos: dict = {}
    for f in sorted(fills, key=lambda f: f["filled_at"]):
        k = (f["group_id"], f["us_market_slug"], str(f["holding_side"]))
        st = pos.setdefault(k, {"q": 0.0, "basis": 0.0,
                                "fixture": f.get("fixture")})
        q, px = num(f["qty"]) or 0.0, num(f["price"]) or 0.0
        fee = num(f.get("fee_usd")) or 0.0
        if str(f.get("direction")) == "BUY":
            st["q"] += q
            st["basis"] += q * px + fee
        elif st["q"] > 1e-9:
            take = min(q, st["q"])
            st["basis"] -= st["basis"] / st["q"] * take
            st["q"] -= take
    book = fixture = 0.0
    groups: set = set()
    for k, st in pos.items():
        if k in settled or st["q"] <= 1e-9 or st["basis"] <= 0:
            continue
        book += st["basis"]
        if dec.get("fixture") and st.get("fixture") == dec.get("fixture"):
            fixture += st["basis"]
            groups.add(k[0])
    return {"book_open_usd": book, "fixture_open_usd": fixture,
            "fixture_open_groups": len(groups),
            "basis": ("PAPER_FILLS_NET_OF_SALES_AND_SETTLEMENTS_RECORDED_BY_"
                      "THE_CLOCK (position truth; window %.0f days)"
                      % (ctx.params["exposure_lookback_s"] / DAY))}


async def idle_capital_at(ctx: RunContext, dec, clock, group_ids,
                          scope) -> tuple:
    """(idle USD, basis | why): the paper ledger's cash minus reserved at
    its latest row recorded by the clock, excluding this decision's own
    group (its reservation is the allocation being decided)."""
    where = "account_id = $1"
    args: tuple = (dec["account_id"],)
    if group_ids:
        where += " AND (group_id IS NULL OR NOT (group_id = ANY($2::text[])))"
        args = (dec["account_id"], sorted(group_ids))
    rows = await ctx.R.rows(
        "paper_ledger", clock=clock, scope=scope,
        cols=("seq", "cash_after_usd", "reserved_after_usd", "group_id"),
        where=where, args=args, order="committed_at DESC, seq DESC", limit=1)
    if not rows:
        return None, "NO_PAPER_LEDGER_ROW_RECORDED_AT_OR_BEFORE_THE_CLOCK"
    r = rows[0]
    cash, res = num(r["cash_after_usd"]), num(r["reserved_after_usd"])
    if cash is None or res is None:
        return None, "LEDGER_ROW_UNREADABLE"
    return cash - res, ("PAPER_LEDGER_CASH_MINUS_RESERVED_AT_THE_CLOCK "
                        "(seq %s)" % r["seq"])


def allie_hurdle_sample(ctx: RunContext, clock, scope) -> list:
    """Allie's own hurdle sample (canonical_components.allie_at_decision):
    the latest 50 MEASURED INVESTMENT intents' adjusted profit per
    capital-hour created within 7 days and recorded by the clock."""
    if ctx.intents is None:
        return []
    rows = [r for r in ctx.intents.at(clock, scope=scope)
            if r.get("created_at") is not None
            and r["created_at"] > clock - ctx.params["allie_hurdle_lookback_s"]]
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
        where="decision_id = $1 AND role = 'ENTRY'",
        args=(dec["decision_id"],), order="created_at", limit=20)


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
    decision (canonical_intent.build_decision_intent, pure), or why not."""
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
                  "order nor the session configuration names one",
                  action="NO_CANONICAL_DECISION_INTENT")
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
        return un("CANONICAL_INTENT_NOT_BUILDABLE: %s" % exc,
                  action="NO_CANONICAL_DECISION_INTENT")
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


def opportunity_at(dec, ev, eddie, idle, lag_samples, start, clock) -> dict:
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
                   idle_capital_why=None if idle is not None else
                   "NO_PAPER_LEDGER_ROW_AT_THE_CLOCK", lag_samples=lag_samples,
                   ctx={})
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
    """The pre-allocation qualified-opportunity tape at the clock: every
    decision recorded by the clock (any verdict, any strategy) whose
    decision-time economics were positive after fees, deduplicated by
    market (latest decision per market)."""
    rows = ctx.tape.at(clock, scope=scope)
    W = ctx.params["alternatives_window_s"]
    T = ctx.params["tape_window_s"]
    alts: dict = {}
    hurdle_rows: dict = {}
    for r in rows:
        if r["decision_id"] == dec["decision_id"]:
            continue
        t = r.get("decided_at")
        if t is None or t > clock or t < clock - T:
            continue
        e = ctx.econ(r)
        if not e["qualified"]:
            continue
        slug = r.get("us_market_slug")
        key = slug or r["decision_id"]
        h = hurdle_rows.get(key)
        if h is None or t > h["t"]:
            hurdle_rows[key] = {"t": t, "r": r, "e": e}
        if t >= clock - W and slug != dec.get("us_market_slug"):
            a = alts.get(key)
            if a is None or t > a["t"]:
                alts[key] = {"t": t, "r": r, "e": e}
    return {"alternatives": list(alts.values()),
            "hurdle_rows": list(hurdle_rows.values())}


def hurdle_at(ctx: RunContext, tape: dict, clock, scope) -> dict:
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
                      "capital-hour of %d qualified opportunities on the "
                      "pre-allocation tape (any verdict) within %.0f days "
                      "before the clock" % (int(q * 100), len(ppch),
                                            ctx.params["tape_window_s"] /
                                            DAY))}


def allocation_benchmarks(ctx: RunContext, dec, ev, tape, *, idle, rail,
                          eddie, allie, hours, clock, scope) -> dict:
    """The benchmark allocations of THIS decision, every one under the same
    rails (attribution_v2 clamps them identically):

      EQUAL_ALLOCATION      idle capital / N qualified opportunities at the
                            clock (this one + the contemporaneous ones)
      ROI_ONLY_RANKING      greedy by expected net / capital required
      CAPITAL_HOUR_RANKING  research_ref.marginal_capital_value.allocate as
                            a SHADOW tranche allocator (expected net per
                            capital-hour)
      RESERVE_NO_ALLOCATION 0
      ALLIE                 allie_capital's final allocatable amount
    """
    from ..research_ref import marginal_capital_value as MCV
    out = {"RESERVE_NO_ALLOCATION": {"usd": 0.0, "basis": "reserve: 0"},
           "ALLIE": ({"usd": num(allie.get("final_allocatable_usd")),
                      "basis": "allie_capital.allocate (SHADOW)"}
                     if allie.get("status") == "MEASURED" else
                     {"usd": None, "why": "ALLIE_UNMEASURED: %s" % (
                         allie.get("why") or allie.get("binding_constraint"))})}
    me = {"id": dec["decision_id"], "e": ev, "hours": hours}
    pool = [me] + [{"id": a["r"]["decision_id"], "e": a["e"],
                    "hours": ctx.hours_to_release(a["r"], clock, scope)[0]}
                   for a in tape["alternatives"]]
    if idle is None:
        why = "IDLE_CAPITAL_UNMEASURED_AT_THE_CLOCK"
        for b in ("EQUAL_ALLOCATION", "ROI_ONLY_RANKING",
                  "CAPITAL_HOUR_RANKING"):
            out[b] = {"usd": None, "why": why}
        return out
    if not ev["qualified"] or not ev["capital_required"]:
        why = ("THIS_OPPORTUNITY_IS_NOT_QUALIFIED_AT_THE_CLOCK (no positive "
               "executable net after fees)")
        for b in ("EQUAL_ALLOCATION", "ROI_ONLY_RANKING",
                  "CAPITAL_HOUR_RANKING"):
            out[b] = {"usd": 0.0, "basis": why}
        return out
    cap_me = eddie.get("max_executable_qty") \
        if eddie.get("status") == "MEASURED" else None

    def cap_of(o):
        c = o["e"]["capital_required"]
        lim = [c]
        if rail is not None:
            lim.append(rail)
        if o is me and cap_me is not None and o["e"]["d"]:
            lim.append(num(cap_me) * o["e"]["d"])
        return max(0.0, min(lim))
    out["EQUAL_ALLOCATION"] = {
        "usd": min(idle / len(pool), cap_of(me)),
        "basis": "idle $%.2f / %d qualified opportunities at the clock"
                 % (idle, len(pool))}
    left = idle
    k_roi = 0.0
    for o in sorted(pool, key=lambda o: (-(o["e"]["roi"] or -1e9), o["id"])):
        take = min(cap_of(o), max(0.0, left))
        left -= take
        if o is me:
            k_roi = take
    out["ROI_ONLY_RANKING"] = {
        "usd": k_roi, "basis": "greedy by expected net / capital required "
        "over %d qualified opportunities" % len(pool)}
    if hours is None:
        out["CAPITAL_HOUR_RANKING"] = {
            "usd": None, "why": "THIS_OPPORTUNITY'S_TIME_TO_RELEASE_"
            "UNMEASURED_AT_THE_CLOCK"}
    else:
        fp = eddie.get("expected_fill_probability") \
            if eddie.get("status") == "MEASURED" else None
        tr, dropped = [], 0
        for o in pool:
            c = cap_of(o)
            if o["hours"] is None or c <= 0 or o["e"]["net"] is None:
                dropped += 1
                continue
            tr.append(MCV.Tranche(
                opportunity_id=o["id"], tranche_id="1", capital_usd=c,
                expected_net_usd=o["e"]["net"] * c / o["e"]["capital_required"],
                expected_hours_to_release=o["hours"],
                fill_probability=1.0))
        got = MCV.allocate(tr, idle)
        k = sum(t.capital_usd for t in got["chosen"] if t.opportunity_id
                == me["id"])
        out["CAPITAL_HOUR_RANKING"] = {
            "usd": k, "basis": ("research_ref.marginal_capital_value."
                                "allocate (RESEARCH_SHADOW_ONLY) over %d "
                                "tranches (%d without a time to release "
                                "left out); fill probability 1.0 for every "
                                "tranche alike (Eddie's estimate %s is not "
                                "available for the alternatives)"
                                % (len(tr), dropped, fp)),
            "authority": got["authority"]}
    return out


# ═════════════════════════════════════════════════════════════════════
# THE OUTCOME AT THE HORIZON
# ═════════════════════════════════════════════════════════════════════

async def outcome_at_horizon(ctx: RunContext, dec, scope) -> dict:
    """The PAPER execution, Xavier's reviews, the canonical management
    intents, the settlements and the release, read at the horizon."""
    R, H = ctx.R, ctx.end
    orders = await entry_orders(ctx, dec, H, scope)
    gids = sorted({o["group_id"] for o in orders})
    out = {"orders": orders, "group_ids": gids, "fills": [], "reviews": [],
           "mgmt_intents": [], "settlements": [], "refusal": None}
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
            out["refusal"] = {"code": det.get("refusal"),
                              "found_at": f[0].get("found_at"),
                              "capital": det.get("refusal")
                              in CAPITAL_REFUSALS}
    if not gids:
        return out
    out["fills"] = await R.rows(
        "paper_fills", clock=H, scope=scope,
        cols=("fill_id", "order_id", "group_id", "role", "direction",
              "holding_side", "us_market_slug", "qty", "price", "fee_usd",
              "gross_usd", "filled_at"),
        where="group_id = ANY($1::text[])", args=(gids,),
        order="filled_at", limit=2000)
    out["reviews"] = await R.rows(
        "paper_xavier_reviews", clock=H, scope=scope,
        cols=("review_id", "group_id", "reviewed_at", "trigger",
              "recommendation", "refusal", "alternatives", "selection",
              "exposure", "standing", "measure", "action"),
        where="group_id = ANY($1::text[])", args=(gids,),
        order="reviewed_at", limit=500)
    if ctx.present.get("canonical_management_intents"):
        out["mgmt_intents"] = await R.rows(
            "canonical_management_intents", clock=H, scope=scope,
            cols=("intent_id", "review_id", "group_id", "action",
                  "target_qty", "evidence_state", "created_at"),
            where="group_id = ANY($1::text[])", args=(gids,),
            order="created_at", limit=500)
    out["settlements"] = [s for s in ctx.settlements.at(H, scope=scope)
                          if s["group_id"] in gids]
    return out


def contract_settlement(ctx: RunContext, slug, side, scope) -> dict | None:
    """The market's own settlement (any group, WON/LOST/VOID_REFUND),
    recorded by the horizon -- the contract's outcome for a position that
    left before settling."""
    best = None
    for s in ctx.settlements.at(ctx.end, scope=scope):
        if s["us_market_slug"] != slug or s.get("outcome") not in (
                "WON", "LOST", "VOID_REFUND"):
            continue
        same = str(s.get("holding_side")) == str(side)
        key = (same, s.get("settled_at") or 0, s.get("version") or 0)
        if best is None or key > best[0]:
            best = (key, s)
    return None if best is None else best[1]


async def marks(ctx: RunContext, slug, t0, t1, scope) -> list:
    """Book observations of the market between first fill and release,
    recorded by the horizon, evenly thinned to mark_points_max."""
    if not slug or t0 is None:
        return []
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
            "more than %d book observations while held: the marks cover the "
            "first %d" % (P.MAX_LIMIT, P.MAX_LIMIT))
    return prior + rows

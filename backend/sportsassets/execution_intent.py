"""ONE QUALIFIED DECISION -> ONE EXECUTION INTENT -> PAPER + ACTUAL, CONCURRENTLY.

Owner correction 2026-10-03. The authoritative object is the qualified
investment decision, not the paper order. When the versioned PinnAPI
investment policy decides ENTER, `paper_benchmark.decide_one` writes ONE
immutable execution intent (migration 199) and fans out two SIBLINGS:

  PAPER   the full modelled quantity -> paper order -> simulated fill
          (unchanged; the paper ledger and simulator own it);
  ACTUAL  `ActualLane.run(intent)`, dispatched as its own task BEFORE the
          paper order is written, on its own connection: it never waits for
          the paper order, its persistence, its acknowledgement or its
          simulated fill; a slow venue never delays the paper side.

THE ACTUAL LANE keeps every gate and adds no serialization with the paper
side (its only waits are the claim's short transaction locks, step 5 -- one
market, then the account -- never held across a venue call):
  1. the durable control (execmirror_control: enabled, not stopped) and the
     retail account's key fingerprint -- the same switch and identity the
     execution-mirror machinery uses;
  2. live eligibility, decided when the intent is written (fail closed:
     `execmirror.live_eligibility`) -- exploration, training, calibration,
     research, benchmark, unknown and unapproved versions are PAPER ONLY;
  3. the decision's age (`MAX_DECISION_AGE_S`) and the age of the executable
     book the decision priced against (`MAX_BOOK_AGE_S`, our receipt instant;
     currency is NOT claimed beyond it). The order is an IOC LIMIT at the
     decision's own wire price, so the venue can never fill it worse than
     the decided economics;
  4. live sizing: target / 1,000 rounded to whole contracts; below ONE whole
     contract (the lane's minimum; execmirror.entry_live_qty) ->
     BELOW_VENUE_MINIMUM (paper proceeds, actual refuses; never enlarged, so
     0.6 of a contract is refused, not sent as 1); the per-order cap and the
     account's buying power;
  5. IDEMPOTENCY: the actual order row is keyed by the intent
     (`execmirror_orders.execution_intent_id` UNIQUE, mirror_id 'ei:<intent>'),
     so one intent can create at most one initial submission; a duplicate
     WS event or a re-dispatch finds the claim and stops. An ambiguous
     outcome becomes UNKNOWN and is reconciled against the venue by
     `execmirror.Mirror.recover` -- never resubmitted blindly;
  6. on acknowledgement the venue order record is read at once (fills only
     from it) and a confirmed actual fill is handed straight to Xavier
     (`Mirror.live_handoffs`) -- the paper handoff is never awaited.

RC6.2 PMUS-EXEC (independent audit of this path, before any activation):
the newest account snapshot must not name a venue position that disagrees
with our venue fills (R_VENUE_POSITION_DISAGREES, step 4a); the claim
(step 5) serializes per market (a transaction advisory lock), refuses a
second exposure on one market (a non-terminal order or held contracts) and
the account's open + held notional above execmirror_control's aggregate cap
(read under an account-wide lock taken after the market's, so two markets'
claims never pass the cap on one reading), and is written only while the
control is enabled and not stopped (in the INSERT itself); the control is
re-read immediately before the send (5b).

Every step stamps a high-resolution timeline (UTC epoch nanoseconds plus a
monotonic perf_counter_ns) on the intent for the latency report.

R30A CONVERGENCE (audit P0 #2): THE ACTUAL LANE ORIGINATES NOTHING OUTSIDE A
CANONICAL INTENT. paper_benchmark.decide_one now builds the canonical decision
intent BEFORE this hook runs and names it on the payload
(evidence.canonical_intent = {intent_id, content_sha}); `create` records the
name. Immediately before its claim (step 4b) the lane calls
live_parity.authorize_live_exposure: the canonical intent must exist, verify,
be the one named, carry the SAME order (slug, intent, side, strategy and
version, order form, wire, limit, quantity -- an order re-priced from another
book is another decision), be unexpired, pass the LIVE policy and the live
gates, and come with the canonical SMALL LIVE adapter's authorization, which
SHADOW never issues. The authorization (when one ever exists) is handed to
execmirror.Venue.place, which refuses any new order without it. The book
currency and settlement gates stay, and their approvals are versioned and
hash-matched (live_rule_artifacts + live_approvals).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from decimal import Decimal
from typing import Any

from . import actual_admission as AA
from . import decision_hooks
from . import execmirror as M
from . import execmirror_probe as EP
from . import live_approvals as LAP
from . import live_book_currency as LBC
from . import live_book_evidence as LBE
from . import live_parity as LP
from . import live_rule_artifacts as LRA
from . import p5_c12_proof as C12P
from . import venue_pace

log = logging.getLogger(__name__)

VERSION = "EXECUTION_INTENT_V1"
#: The decision is admissible for the actual lane only this long after it was
#: made (its PinnAPI probability was already within the 30 s Pinnacle rule at
#: the decision instant; the actual lane adds no newer interpretation).
MAX_DECISION_AGE_S = 10.0
#: The executable book the decision priced against, by OUR receipt instant
#: (paper_benchmark.BOOK_MAX_AGE_S, the same receipt-based rule; pinned).
MAX_BOOK_AGE_S = 10.0
#: The account snapshot buying power is read from is admissible this long.
MAX_ACCOUNT_SNAPSHOT_AGE_S = 180.0

# actual_state values (migration 199)
A_PAPER_ONLY = "PAPER_ONLY"
A_DISPATCHED = "DISPATCHED"
A_NO_LANE = "LANE_NOT_RUNNING"
A_REFUSED = "REFUSED"
A_SUBMITTING = "SUBMITTING"
A_SUBMITTED = "SUBMITTED"
A_UNKNOWN = "UNKNOWN"
A_REJECTED = "REJECTED"

# actual-lane refusals (precise, never silent)
R_LANE_DISABLED = "ACTUAL_LANE_DISABLED"
R_LANE_STOPPED = "ACTUAL_LANE_STOPPED"
R_ACCOUNT_CHANGED = "RETAIL_ACCOUNT_FINGERPRINT_CHANGED"
R_CREDENTIAL = "RETAIL_CREDENTIAL_ABSENT"
R_DECISION_STALE = "DECISION_STALE_AT_ACTUAL_SUBMIT"
R_BOOK_STALE = "EXECUTABLE_BOOK_STALE_AT_ACTUAL_SUBMIT"
R_BOOK_UNKNOWN = "EXECUTABLE_BOOK_AGE_UNKNOWN"
R_ACCOUNT_UNKNOWN = "ACCOUNT_STATE_NOT_CURRENT"
R_DUPLICATE = "INTENT_ALREADY_CLAIMED"
# rc6.2 pmus-exec (independent audit of the retail path):
#: item 4 -- the newest account snapshot says the venue's position on some
#: market is not what our venue fills (plus the baseline) say: nothing new is
#: originated until it is reconciled
R_VENUE_POSITION_DISAGREES = "VENUE_POSITION_DISAGREES_WITH_MIRROR_FILLS"
#: item 2 -- one market, one exposure: a non-terminal actual order on the
#: market (any lane, any role), or contracts already held there
R_MARKET_HAS_OPEN_ORDER = "ACTUAL_MARKET_HAS_A_NON_TERMINAL_ORDER"
R_MARKET_ALREADY_HELD = "ACTUAL_MARKET_ALREADY_HELD"
#: item 2 -- the account's open + held actual notional plus this order's cost
#: above execmirror_control's aggregate cap (execmirror.aggregate_cap_usd)
R_AGGREGATE_ABOVE_CAP = "ACTUAL_OPEN_AND_HELD_NOTIONAL_ABOVE_CAP"
#: the claim's per-market lock: pg_advisory_xact_lock(SLUG_LOCK_CLASS,
#: hashtext(slug)) -- the two-key form, a key space that never overlaps the
#: single-key session locks (execmirror.LOCK_KEY, the runner's), so a hash
#: collision can never wait on the runner
SLUG_LOCK_CLASS = 0x45584D32          # 'EXM2'
#: the claim's ACCOUNT-WIDE lock (REVIEW, item 2): pg_advisory_xact_lock(
#: ACCOUNT_LOCK_CLASS, 0), taken by every claimer AFTER its market's lock and
#: BEFORE it reads the account's open + held notional, held to the claim's
#: commit. The market lock alone let two claims on DIFFERENT markets (two
#: dispatches: one task per intent, each on its own pool connection) both
#: read the notional before either inserted, both pass the aggregate cap and
#: both be sent; now the cap read and the INSERT are one critical section for
#: the whole account. The same two-key form in a class of its own, so it never
#: meets a market lock or a single-key session lock (execmirror.LOCK_KEY);
#: one fixed order (market, then account) for every claimer, so no claimer
#: waits on another in the opposite order.
ACCOUNT_LOCK_CLASS = 0x45584D33       # 'EXM3'

LANE = None          # the API process's ActualLane (installed by start())
_TASKS: set = set()


def _ns() -> int:
    return time.time_ns()


def _mark(t: dict, name: str) -> dict:
    t[name] = {"utc_ns": _ns(), "mono_ns": time.perf_counter_ns()}
    return t


def _j(v) -> str:
    return json.dumps(v, default=str)


def intent_id_for(decision_id: str) -> str:
    """Deterministic: one decision, one intent, whoever computes it."""
    return "ei_" + hashlib.sha256(decision_id.encode()).hexdigest()[:24]


def live_sizing(paper_target_qty, scale) -> dict:
    # a BUY's rule: below one whole contract is 0, never enlarged
    exact, live, delta = M.entry_live_qty(paper_target_qty, scale)
    return {"live_scale": Decimal(str(scale)), "live_raw_qty": exact,
            "live_qty": live, "rounding_delta": delta}


async def create(conn, *, decision_id: str, valuation_id, strategy: str,
                 policy_version, slug: str, order_intent: str, holding_side,
                 group_id: str, order_type: str, time_in_force: str,
                 paper_target_qty, limit_price, wire_price, book_obs_id,
                 book_observed_at: float | None, decided_at: float,
                 evidence: dict, timeline: dict) -> dict:
    """Write the ONE immutable intent of a qualified decision. Live eligibility
    is decided HERE, before the actual lane can see it. Returns the intent;
    `created` is False when it already existed (a duplicate pass)."""
    iid = intent_id_for(decision_id)
    approved, why = M.live_eligibility({"strategy": strategy,
                                        "decision_policy_version": policy_version,
                                        "role": "ENTRY"})
    # THE DECISION-TIME ADMISSION (actual_admission): an approved strategy
    # and version are necessary, never sufficient. The intent is live
    # eligible only when every recorded execution and settlement fact is
    # explicitly admissible; otherwise it is REFUSED here, before any lane
    # can see it, with the first failing requirement as its refusal.
    # THE APPROVED LIVE BOOK RULES: the code constant (empty) plus any rule
    # whose owner-approval artifact is APPROVED with an owner record and
    # whose stored sha256 is this code's (live_rule_artifacts; any failure
    # -> the constant alone).
    approved_rules = await LRA.approved_live_book_rules(conn)
    # R30A: the settlement-compatibility gate admits only under an owner
    # approval of its CURRENT configuration (live_approvals; none -> refused)
    approved_settle = await LAP.approved_settlement_gates(conn)
    adm = AA.evaluate((evidence or {}).get("admission_facts"), slug=slug,
                      order_intent=order_intent,
                      approved_book_rules=approved_rules,
                      approved_settlement_gates=approved_settle)
    why = dict(why, strategy_approved=approved, admission=adm,
               # R30A: the canonical decision intent this execution intent
               # executes (the ACTUAL lane re-verifies it before its claim;
               # none -> it can never submit)
               canonical_intent=(evidence or {}).get("canonical_intent"))
    eligible = approved and adm["verdict"] == AA.LIVE_ADMISSIBLE
    if not approved:
        state, refusal = A_PAPER_ONLY, M.STRATEGY_NOT_LIVE_ELIGIBLE
    elif not eligible:
        state, refusal = A_REFUSED, adm["refusal"]
    else:
        state, refusal = A_DISPATCHED, None
    _mark(timeline, "intent_created")
    row = await conn.fetchrow(
        """INSERT INTO execution_intents (intent_id, decision_id, valuation_id,
             strategy, policy_version, us_market_slug, order_intent, holding_side,
             group_id, order_type, time_in_force, paper_target_qty, limit_price,
             wire_price, book_obs_id, book_observed_at, decided_at, live_eligible,
             live_eligibility, actual_state, actual_refusal, evidence, timeline)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
                   to_timestamp($16),to_timestamp($17),$18,$19::jsonb,$20,$21,
                   $22::jsonb,$23::jsonb)
           ON CONFLICT (decision_id) DO NOTHING RETURNING *""",
        iid, decision_id, valuation_id, strategy, policy_version, slug,
        order_intent, holding_side, group_id, order_type, time_in_force,
        Decimal(str(paper_target_qty)),
        None if limit_price is None else Decimal(str(limit_price)),
        Decimal(str(wire_price)), book_obs_id, book_observed_at, decided_at,
        eligible, _j(why), state, refusal, _j(evidence), _j(timeline))
    if row is None:
        row = await conn.fetchrow(
            "SELECT * FROM execution_intents WHERE decision_id = $1", decision_id)
        return dict(row, created=False)
    return dict(row, created=True)


def dispatch(intent: dict) -> bool:
    """Hand an eligible intent to the actual lane WITHOUT awaiting it. The
    caller goes straight on to the paper order. False when no lane runs in
    this process (recorded by the caller as LANE_NOT_RUNNING)."""
    if not intent.get("created") or not intent.get("live_eligible"):
        return False
    lane = LANE
    if lane is None:
        return False
    task = asyncio.create_task(lane.run(intent["intent_id"]),
                               name="actual-lane-%s" % intent["intent_id"])
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return True


async def mark_no_lane(conn, intent_id: str) -> None:
    await conn.execute(
        """UPDATE execution_intents SET actual_state = $2, updated_at = now()
            WHERE intent_id = $1 AND actual_state = $3""",
        intent_id, A_NO_LANE, A_DISPATCHED)


class ActualLane:
    """The actual Polymarket lane of one process. Shares the execution-mirror
    machinery (the retail Venue, recovery, fill ingestion, Xavier handoff) so
    there is one venue client, one pacing and one audit trail."""

    def __init__(self, get_pool, mirror: "M.Mirror", *, now=time.time):
        self.get_pool = get_pool
        self.mirror = mirror
        self._now = now

    async def run(self, intent_id: str) -> dict:
        try:
            pool = await self.get_pool()
            async with pool.acquire() as conn:
                return await self._run(conn, intent_id)
        except asyncio.CancelledError:
            raise
        except Exception:                                     # noqa: BLE001
            log.exception("actual lane failed for %s", intent_id)
            return {"state": "ERROR"}

    async def _refuse(self, conn, it, code, t, **ev) -> dict:
        _mark(t, "refused")
        await conn.execute(
            """UPDATE execution_intents SET actual_state = $2, actual_refusal = $3,
                 timeline = timeline || $4::jsonb,
                 evidence = evidence || $5::jsonb, updated_at = now()
               WHERE intent_id = $1 AND actual_state = 'DISPATCHED'""",
            it["intent_id"], A_REFUSED, code, _j(t),
            _j({"actual_refusal_evidence": dict(ev, refusal=code)}))
        await M._event(conn, "ACTUAL_REFUSED", intent_id=it["intent_id"],
                       decision_id=it["decision_id"], refusal=code, **ev)
        return {"state": A_REFUSED, "refusal": code, **ev}

    async def _run(self, conn, intent_id: str) -> dict:
        t: dict = {}
        _mark(t, "lane_start")
        it = await conn.fetchrow(
            "SELECT * FROM execution_intents WHERE intent_id = $1", intent_id)
        if it is None or it["actual_state"] != A_DISPATCHED:
            return {"state": "NOT_DISPATCHED"}
        it = dict(it)
        now = self._now()
        # 1 · THE SWITCH AND THE IDENTITY
        ctl = await M.control(conn)
        if not ctl.get("enabled"):
            return await self._refuse(conn, it, R_LANE_DISABLED, t)
        if ctl.get("stopped"):
            return await self._refuse(conn, it, R_LANE_STOPPED, t)
        keys = EP.keys_present()
        if not keys["complete"]:
            return await self._refuse(conn, it, R_CREDENTIAL, t)
        if ctl.get("account_fingerprint") and \
                keys["key_fingerprint"] != ctl["account_fingerprint"]:
            return await self._refuse(conn, it, R_ACCOUNT_CHANGED, t,
                                      expected=ctl["account_fingerprint"])
        # 2 · ELIGIBILITY AND ADMISSION, RE-DERIVED FROM THE INTENT'S OWN
        #     DECISION-TIME FACTS -- the stored boolean is never trusted alone.
        approved, why = M.live_eligibility({
            "strategy": it["strategy"],
            "decision_policy_version": it["policy_version"], "role": "ENTRY"})
        if not it["live_eligible"] or not approved:
            return await self._refuse(conn, it, M.STRATEGY_NOT_LIVE_ELIGIBLE, t,
                                      eligibility=why)
        ev = it["evidence"]
        ev = json.loads(ev) if isinstance(ev, str) else dict(ev or {})
        # the approved live book rules, re-read NOW (an approval withdrawn or
        # a code hash that moved since the intent was written never admits)
        approved_rules = await LRA.approved_live_book_rules(conn)
        approved_settle = await LAP.approved_settlement_gates(conn)
        adm = AA.evaluate(ev.get("admission_facts"),
                          slug=it["us_market_slug"],
                          order_intent=it["order_intent"],
                          approved_book_rules=approved_rules,
                          approved_settlement_gates=approved_settle)
        if adm["verdict"] != AA.LIVE_ADMISSIBLE:
            return await self._refuse(conn, it, adm["refusal"], t,
                                      admission_refusals=adm["refusals"],
                                      admission_version=AA.VERSION)
        # a live stream-book verdict is current only briefly: the rule's own
        # verdict-age bound at submission (P5_LIVE_STREAM_BOOK_V1 C13)
        vstale = LBC.verdict_age_refusal(
            ((ev.get("admission_facts") or {}).get("book") or {}).get(
                "book_currency"), now=now)
        if vstale is not None:
            return await self._refuse(conn, it, vstale.pop("refusal"), t,
                                      **vstale)
        # 3 · THE DECISION AND ITS EXECUTABLE BOOK, STILL CURRENT
        _mark(t, "book_check_start")
        d_age = now - it["decided_at"].timestamp()
        if d_age > MAX_DECISION_AGE_S or d_age < -1.0:
            return await self._refuse(conn, it, R_DECISION_STALE, t,
                                      decision_age_s=round(d_age, 3),
                                      limit_s=MAX_DECISION_AGE_S)
        if it["book_observed_at"] is None:
            return await self._refuse(conn, it, R_BOOK_UNKNOWN, t)
        b_age = now - it["book_observed_at"].timestamp()
        if b_age > MAX_BOOK_AGE_S or b_age < -1.0:
            return await self._refuse(conn, it, R_BOOK_STALE, t,
                                      book_age_s=round(b_age, 3),
                                      limit_s=MAX_BOOK_AGE_S,
                                      basis="OUR_RECEIPT_INSTANT")
        _mark(t, "book_check_end")
        # 4 · LIVE SIZING, CAP AND ACCOUNT
        size = live_sizing(it["paper_target_qty"], ctl.get("scale") or 1000)
        order = {"us_market_slug": it["us_market_slug"],
                 "intent": it["order_intent"], "qty": it["paper_target_qty"],
                 "wire_price": it["wire_price"],
                 "time_in_force": it["time_in_force"],
                 "order_type": it["order_type"]}
        bp, bp_at = await self._buying_power(conn)
        if bp is None or bp_at is None or now - bp_at > MAX_ACCOUNT_SNAPSHOT_AGE_S:
            plan = None
        else:
            plan = M.plan_buy(order, scale=ctl.get("scale") or 1000,
                              buying_power=bp,
                              max_order_usd=ctl.get("max_order_usd") or 0)
        await conn.execute(
            """UPDATE execution_intents SET live_scale = $2, live_raw_qty = $3,
                 live_qty = $4, rounding_delta = $5, updated_at = now()
               WHERE intent_id = $1""",
            intent_id, size["live_scale"], size["live_raw_qty"],
            size["live_qty"], size["rounding_delta"])
        _mark(t, "risk_check_end")
        if size["live_qty"] < 1:
            return await self._refuse(conn, it, M.BELOW_VENUE_MINIMUM, t,
                                      live_raw_qty=str(size["live_raw_qty"]),
                                      why="never enlarged to reach the minimum")
        # the decision's displayed depth must cover the ACTUAL quantity
        adm_q = AA.evaluate(ev.get("admission_facts"),
                            slug=it["us_market_slug"],
                            order_intent=it["order_intent"],
                            live_qty=size["live_qty"],
                            approved_book_rules=approved_rules,
                            approved_settlement_gates=approved_settle)
        if adm_q["verdict"] != AA.LIVE_ADMISSIBLE:
            return await self._refuse(conn, it, adm_q["refusal"], t,
                                      admission_refusals=adm_q["refusals"],
                                      live_qty=size["live_qty"])
        if plan is None:
            return await self._refuse(conn, it, R_ACCOUNT_UNKNOWN, t,
                                      snapshot_at=bp_at,
                                      limit_s=MAX_ACCOUNT_SNAPSHOT_AGE_S)
        if plan.state != "PLANNED":
            return await self._refuse(conn, it, plan.exclusion, t,
                                      **{k: str(v) for k, v in plan.detail.items()})
        # 4a · THE VENUE AGREES WITH OUR BOOK (rc6.2 pmus-exec, audit item 4):
        # the newest account snapshot's reconciliation -- signed venue
        # positions against our venue fills plus the baseline -- must not
        # name a difference; one that does stops new exposure until it is
        # reconciled
        rec = await self._newest_reconciliation(conn)
        if rec is not None and rec.get("reconciled") is False:
            return await self._refuse(
                conn, it, R_VENUE_POSITION_DISAGREES, t,
                differences=dict(list((rec.get("differences") or {}).items())[:5]))
        # 4b · R30A CONVERGENCE: NO ORIGINATION OUTSIDE A CANONICAL INTENT.
        # Before anything is claimed: the canonical decision intent of THIS
        # decision must exist, verify, be the one this execution intent
        # names, carry the same order (an ACTUAL order re-priced from another
        # book is a different decision and is refused), be unexpired, pass
        # the LIVE policy and the live gates, and come with the canonical
        # SMALL LIVE adapter's authorization -- which is never issued in
        # SHADOW. So in this release the ACTUAL lane refuses here, every
        # time, and Venue.place (which refuses without that authorization)
        # is never reached.
        _mark(t, "canonical_check_start")
        canon = await LP.authorize_live_exposure(
            conn, decision_id=it["decision_id"],
            named=ev.get("canonical_intent") or {},
            order={"us_market_slug": it["us_market_slug"],
                   "order_intent": it["order_intent"],
                   "holding_side": it["holding_side"],
                   "strategy": it["strategy"],
                   "strategy_version": it["policy_version"],
                   "time_in_force": it["time_in_force"],
                   "order_type": it["order_type"],
                   "wire_price": it["wire_price"],
                   "limit_price": it["limit_price"],
                   "target_qty": it["paper_target_qty"]},
            now=now)
        if not canon.get("ok"):
            return await self._refuse(conn, it, canon["refusal"], t,
                                      canonical=canon.get("detail"))
        # 5 · THE CLAIM: at most one actual submission per intent -- and, in
        # the SAME transaction (rc6.2 pmus-exec, audit items 2 and 5):
        #   * one market at a time: a transaction lock per market (the
        #     two-key advisory form, SLUG_LOCK_CLASS), so two decisions on one
        #     contract (two loops, two passes) are serialized, and the second
        #     finds the first;
        #   * one exposure per market: a non-terminal actual order on it (any
        #     lane, any role) or contracts already held there refuses by name;
        #   * the account's aggregate cap: open + held actual notional plus
        #     this order's cost within execmirror_control's aggregate cap
        #     (execmirror.aggregate_cap_usd; NULL -> max_order_usd, fail
        #     closed small) -- read and claimed under the ACCOUNT-WIDE lock
        #     (ACCOUNT_LOCK_CLASS, after the market's), so claims on two
        #     markets at once never both pass the cap on the same reading;
        #   * the kill switch IN THE INSERT ITSELF: the row is written only if
        #     execmirror_control is enabled and not stopped at that instant,
        #     the control row share-locked so a stop either commits first (no
        #     claim) or waits for this claim and then sees it (emergency_stop
        #     is not done while it is SUBMITTING).
        mid = "ei:" + intent_id
        slug = it["us_market_slug"]
        cost = Decimal(str(plan.detail.get("cost_usd") or "0"))
        blocked = None
        claimed = None
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1, hashtext($2))",
                               SLUG_LOCK_CLASS, slug)
            dup = await conn.fetchval(
                """SELECT mirror_id FROM execmirror_orders
                    WHERE mirror_id = $1 OR execution_intent_id = $2 LIMIT 1""",
                mid, intent_id)
            if dup is None:
                ex = await M.market_exposure(conn, slug)
                if ex["non_terminal"]:
                    blocked = {"refusal": R_MARKET_HAS_OPEN_ORDER,
                               "non_terminal": ex["non_terminal"]}
                elif ex["net_held"] > 0:
                    blocked = {"refusal": R_MARKET_ALREADY_HELD,
                               "net_held": str(ex["net_held"])}
            if dup is None and blocked is None:
                # one claimer at a time for the whole account, from the cap
                # read to the commit of its INSERT (market lock first)
                await conn.execute("SELECT pg_advisory_xact_lock($1, 0)",
                                   ACCOUNT_LOCK_CLASS)
                cap, basis = M.aggregate_cap_usd(await M.control(conn))
                nt = await M.open_and_held_notional(conn)
                total = nt["open_usd"] + nt["held_usd"] + cost
                if total > cap:
                    blocked = {"refusal": R_AGGREGATE_ABOVE_CAP,
                               "open_usd": str(nt["open_usd"]),
                               "held_usd": str(nt["held_usd"]),
                               "order_cost_usd": str(cost),
                               "total_usd": str(total), "cap_usd": str(cap),
                               "cap_basis": basis}
            if dup is None and blocked is None:
                claimed = await conn.fetchval(
                    """INSERT INTO execmirror_orders (mirror_id, execution_intent_id,
                         group_id, role, strategy, us_market_slug, intent, order_type,
                         tif, post_only, wire_price, paper_qty, scaled_qty, live_qty,
                         rounding_delta, state, paper_decided_at, submit_started_at,
                         attempts, detail)
                       SELECT $1,$2,$3,'ENTRY',$4,$5,$6,$7,$8,false,$9,$10,$11,$12,$13,
                              'SUBMITTING',$14,now(),1,$15::jsonb
                        WHERE EXISTS (SELECT 1 FROM execmirror_control
                                       WHERE id = 1 AND enabled AND NOT stopped
                                         FOR SHARE)
                       ON CONFLICT DO NOTHING RETURNING mirror_id""",
                    mid, intent_id, it["group_id"], it["strategy"], slug,
                    it["order_intent"], it["order_type"], it["time_in_force"],
                    it["wire_price"], it["paper_target_qty"], size["live_raw_qty"],
                    plan.live_qty, size["rounding_delta"], it["decided_at"],
                    _j({"params": plan.params, "cost_usd": plan.detail.get("cost_usd"),
                        "execution_intent_id": intent_id,
                        "decision_id": it["decision_id"], "source": VERSION}))
        if dup is not None:
            return {"state": "DUPLICATE", "refusal": R_DUPLICATE}
        if blocked is not None:
            return await self._refuse(conn, it, blocked.pop("refusal"), t,
                                      **blocked)
        if not claimed:
            if await conn.fetchval(
                    """SELECT 1 FROM execmirror_orders
                        WHERE mirror_id = $1 OR execution_intent_id = $2""",
                    mid, intent_id):
                return {"state": "DUPLICATE", "refusal": R_DUPLICATE}
            now_ctl = await M.control(conn)
            return await self._refuse(
                conn, it, (R_LANE_DISABLED if not now_ctl.get("enabled")
                           else R_LANE_STOPPED), t, at="CLAIM")
        await conn.execute(
            """UPDATE execution_intents SET actual_state = $2, actual_mirror_id = $3,
                 updated_at = now() WHERE intent_id = $1""",
            intent_id, A_SUBMITTING, mid)
        # 5b · THE SWITCH, AGAIN, IMMEDIATELY BEFORE THE SEND (audit item 5):
        # a stop or disable that committed after the claim stops the send;
        # the claimed row is EXCLUDED (nothing was sent) and the intent
        # refused by name
        now_ctl = await M.control(conn)
        if not now_ctl.get("enabled") or now_ctl.get("stopped"):
            code = R_LANE_DISABLED if not now_ctl.get("enabled") else R_LANE_STOPPED
            await conn.execute(
                """UPDATE execmirror_orders SET state = 'EXCLUDED', exclusion = $2,
                     detail = detail || $3::jsonb, updated_at = now()
                   WHERE mirror_id = $1 AND state = 'SUBMITTING'""",
                mid, code, _j({"refused_before_send": {
                    "code": code, "at": "IMMEDIATELY_BEFORE_PLACE",
                    "nothing_sent": True}}))
            _mark(t, "refused")
            await self._finish(conn, intent_id, t, A_REFUSED, refusal=code)
            await M._event(conn, "ACTUAL_REFUSED", mirror_id=mid,
                           intent_id=intent_id, decision_id=it["decision_id"],
                           refusal=code, at="IMMEDIATELY_BEFORE_PLACE")
            return {"state": A_REFUSED, "refusal": code,
                    "at": "IMMEDIATELY_BEFORE_PLACE"}
        if self.mirror._buying_power is not None:
            self.mirror._buying_power = float(
                Decimal(str(self.mirror._buying_power))
                - Decimal(str(plan.detail.get("cost_usd", "0"))))
        # 6 · SUBMIT NOW (priority lane of the venue gate in this process)
        _mark(t, "submit_start")
        t0 = time.perf_counter_ns()
        # the canonical authorization travels to the venue adapter, which
        # refuses any new order without one (execmirror.Venue.place)
        auth_kw = ({"canonical_live_authorization": canon["token"]}
                   if canon.get("token") is not None else {})
        try:
            with venue_pace.priority_claims():
                resp = await asyncio.to_thread(self.mirror.venue().place,
                                               plan.params, **auth_kw)
        except Exception as exc:                              # noqa: BLE001
            state = M._classify(exc)            # REJECTED, or UNKNOWN to reconcile
            _mark(t, "submit_error")
            err = {"error": type(exc).__name__,
                   "status": getattr(exc, "status_code", None),
                   "detail": EP._error(exc)["detail"]}
            # FENCED ON THE CLAIM (R30A chaos review): a lane that outlived
            # its SUBMITTING lease finds recovery has taken the row over and
            # must not overwrite what recovery established from the venue;
            # its answer is recorded (and acted on only where it is evidence
            # the row lacks) by execmirror.late_submit_answer
            wrote = await conn.fetchval(
                """UPDATE execmirror_orders SET state = $2, error = $3::jsonb,
                     latency_ms = $4, updated_at = now()
                   WHERE mirror_id = $1 AND state = 'SUBMITTING'
                   RETURNING mirror_id""",
                mid, state, _j(err),
                (time.perf_counter_ns() - t0) // 1_000_000)
            if not wrote:
                got = await M.late_submit_answer(conn, mid, vid=None,
                                                 classified=state, error=err,
                                                 now=self.mirror._now())
                await M._event(conn, "ACTUAL_SUBMIT_" + state, mirror_id=mid,
                               intent_id=intent_id, error=EP._error(exc),
                               late=got)
                await self._late_timeline(conn, intent_id, t, got)
                return {"state": "LATE_" + state, "late": got}
            await self._finish(conn, intent_id, t,
                               A_REJECTED if state == "REJECTED" else A_UNKNOWN,
                               refusal=("VENUE_REJECTED" if state == "REJECTED"
                                        else None))
            await M._event(conn, "ACTUAL_SUBMIT_" + state, mirror_id=mid,
                           intent_id=intent_id, error=EP._error(exc))
            return {"state": state}
        _mark(t, "ack")
        vid = (resp or {}).get("id")
        lat_ms = (time.perf_counter_ns() - t0) // 1_000_000
        wrote = await conn.fetchval(
            """UPDATE execmirror_orders SET state = $2, venue_order_id = $3,
                 accepted_at = now(), latency_ms = $4, updated_at = now(),
                 detail = detail || $5::jsonb
               WHERE mirror_id = $1 AND state = 'SUBMITTING'
               RETURNING mirror_id""",
            mid, "OPEN" if vid else "UNKNOWN", vid, lat_ms,
            _j({"submit_executions": len((resp or {}).get("executions") or [])}))
        if not wrote:
            got = await M.late_submit_answer(
                conn, mid, vid=vid, classified="OPEN" if vid else "UNKNOWN",
                error=None, now=self.mirror._now())
            if got == "RECORDED_LATE_ACKNOWLEDGEMENT":
                await self.mirror._refresh(conn, {"mirror_id": mid,
                                                  "venue_order_id": vid})
                await self.mirror.live_handoffs(conn)
            await self._late_timeline(conn, intent_id, t, got)
            return {"state": "LATE_" + ("ACK" if vid else "UNKNOWN"),
                    "late": got, "venue_order_id": vid, "mirror_id": mid}
        await M._event(conn, "ACTUAL_ACCEPTED" if vid else "SUBMISSION_AMBIGUOUS",
                       mirror_id=mid, intent_id=intent_id, venue_order_id=vid,
                       latency_ms=lat_ms)
        if vid:
            # the venue's own order record, read at once: fills only from it
            await self.mirror._refresh(conn, {
                "mirror_id": mid, "venue_order_id": vid, "cum_qty": 0,
                "avg_px": None, "fees_usd": 0, "group_id": it["group_id"],
                "us_market_slug": it["us_market_slug"],
                "intent": it["order_intent"], "live_qty": plan.live_qty,
                "state": "OPEN"})
            if await conn.fetchval(
                    "SELECT count(*) FROM execmirror_fills WHERE mirror_id = $1", mid):
                _mark(t, "first_fill_seen")
                # an actual fill goes straight to Xavier (never via paper)
                await self.mirror.live_handoffs(conn)
                _mark(t, "xavier_handoff")
        await self._finish(conn, intent_id, t,
                           A_SUBMITTED if vid else A_UNKNOWN)
        return {"state": A_SUBMITTED if vid else A_UNKNOWN,
                "venue_order_id": vid, "mirror_id": mid}

    async def _finish(self, conn, intent_id, t, state, refusal=None) -> None:
        """The lane's own outcome onto its intent -- only while the intent
        still says the lane's submission is in flight (SUBMITTING): an intent
        recovery has already decided from venue evidence is never rewritten
        by the lane (R30A chaos review: the lease fence, on the intent)."""
        await conn.execute(
            """UPDATE execution_intents SET actual_state = $2, actual_refusal = $3,
                 timeline = timeline || $4::jsonb, updated_at = now()
               WHERE intent_id = $1 AND actual_state = $5""",
            intent_id, state, refusal, _j(t), A_SUBMITTING)

    async def _late_timeline(self, conn, intent_id, t, outcome) -> None:
        """A late answer's latency marks still reach the intent's timeline
        (evidence only: the intent's state is the venue evidence's, set by
        execmirror.late_submit_answer / recovery, never the late lane's)."""
        await conn.execute(
            """UPDATE execution_intents SET timeline = timeline || $2::jsonb,
                 updated_at = now() WHERE intent_id = $1""",
            intent_id, _j(dict(t, late_answer=outcome)))

    async def _newest_reconciliation(self, conn) -> dict | None:
        """The newest account snapshot's reconciliation (Mirror.snapshot), or
        None when there is no snapshot (its age is gated at step 4)."""
        rec = await conn.fetchval(
            "SELECT reconciliation FROM execmirror_snapshots ORDER BY at DESC LIMIT 1")
        if rec is None:
            return None
        return json.loads(rec) if isinstance(rec, str) else dict(rec)

    async def _buying_power(self, conn) -> tuple[Any, float | None]:
        """The retail account's buying power: the runner's live figure when it
        has one (decremented as this lane commits), else the latest snapshot."""
        snap = await conn.fetchrow(
            "SELECT balances, at FROM execmirror_snapshots ORDER BY at DESC LIMIT 1")
        at = None if snap is None else snap["at"].timestamp()
        if self.mirror._buying_power is not None and at is not None:
            return self.mirror._buying_power, at
        if snap is None:
            return None, None
        bal = snap["balances"]
        bal = json.loads(bal) if isinstance(bal, str) else (bal or [])
        for b in bal or []:
            if str(b.get("currency", "USD")).upper() == "USD":
                bp = b.get("buyingPower")
                return (None if bp is None else float(bp)), at
        return None, at


async def on_decision(conn, payload: dict) -> dict:
    """THE DECISION HOOK (decision_hooks.DECISION_HOOK): write the qualified
    decision's ONE intent, then dispatch the actual lane without awaiting it.
    Returns at once; the caller writes the paper order next."""
    intent = await create(conn, **payload)
    if not intent["live_eligible"]:
        await _c12_proof(conn, intent, payload)
        return {"intent_id": intent["intent_id"],
                "actual_lane": intent["actual_state"],
                "actual_refusal": intent["actual_refusal"]}
    if not intent["created"]:
        return {"intent_id": intent["intent_id"], "actual_lane": "ALREADY_DISPATCHED"}
    dispatched = (not conn.is_in_transaction()) and dispatch(intent)
    if not dispatched:
        await mark_no_lane(conn, intent["intent_id"])
    # the proof is written AFTER the dispatch, so it adds nothing to the
    # actual lane's path; it records the decision-time facts only
    await _c12_proof(conn, intent, payload)
    return {"intent_id": intent["intent_id"],
            "actual_lane": A_DISPATCHED if dispatched else A_NO_LANE}


async def _c12_proof(conn, intent: dict, payload: dict) -> None:
    """P5 C12 decision-time proof (p5_c12_proof): one record per NEW intent
    whose decision a resident stream book was evaluated for. Evidence only:
    it runs after the intent is written and its eligibility fixed, in a
    savepoint, and never raises into the decision or either lane."""
    if not intent.get("created"):
        return
    try:
        await C12P.record(conn, intent, payload)
    except Exception:                                         # noqa: BLE001
        log.debug("c12 proof not recorded", exc_info=True)


def start(get_pool, mirror) -> "ActualLane":
    """Install the process's actual lane and the decision hook (one per
    process that decides and executes)."""
    global LANE
    LANE = ActualLane(get_pool, mirror)
    decision_hooks.DECISION_HOOK = on_decision
    # the live book-currency evidence the decision records for admission
    # (P5_LIVE_STREAM_BOOK_V1; NOT_ESTABLISHED until an identity mapper is
    # installed in live_book_evidence.IDENTITY_MAPPER)
    decision_hooks.LIVE_BOOK_EVIDENCE = LBE.for_decision
    # the decision's one resident stream observation: the actual lane's
    # facts are priced from it when it is current (P5 C12)
    decision_hooks.LIVE_BOOK_STREAM = LBE.observe
    # R30 LIVE PARITY: the canonical decision / management intents and the
    # two execution adapters (SMALL LIVE is SHADOW) with their parity ledger
    from . import live_parity
    live_parity.install(get_pool, process="api")
    return LANE


def stop() -> None:
    global LANE
    LANE = None
    decision_hooks.DECISION_HOOK = None
    decision_hooks.LIVE_BOOK_EVIDENCE = None
    decision_hooks.LIVE_BOOK_STREAM = None
    from . import live_parity
    live_parity.uninstall()

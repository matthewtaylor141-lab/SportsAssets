"""PAPER MARK FRESHNESS: EVERY OPEN PAPER POSITION IN EXACTLY ONE CLASS.

THE DEFECT (production, 2026-10-05): the Command Center read "158 stale marks
> 300s, 12 unmarked, 71/241 fresh" and Xavier BLOCKED_ON_MARKET_DATA. A mark
was either "OK" or "STALE" (bettor_paper_ledger.latest_marks), a failed read
was invisible (it reads only error IS NULL rows), a read the pass never made
left no record, and a market the venue had EXPIRED looked exactly like one we
had stopped reading.

THE CLASSES (exactly one per open position; `classify`, pure; first rule that
matches wins, in this order):

  EXTERNAL_UNAVAILABLE  the venue says the market is not trading, WITH THE
                        OBSERVATION AS EVIDENCE: the newest successful book
                        read carries a TERMINAL market state (expired,
                        closed, terminated, settled, resolved, the closing
                        auction -- permanent, any age), or a NOT-OPEN
                        transient state (halted, suspended, pre-open,
                        paused) read inside the SLA. Excluded from MARKABLE.
  FRESH                 the newest successful book read is inside the SLA,
                        it publishes an executable exit price for the held
                        side, and that price differs from the read before it
                        (or there is no read before it).
  QUIET_VALID           as FRESH, but the best exit price is UNCHANGED from
                        the previous successful read: re-read inside the SLA,
                        nothing moved. Never inferred from an old read.
  UNMARKED              the newest successful book read is inside the SLA but
                        the side a close consumes publishes no executable
                        level: there is no mark price to state.
  FEED_GAP              no successful read inside the SLA, and OUR read path
                        explains it: the newest read attempt after the last
                        success FAILED (error recorded on the observation),
                        or the latest held-mark refresh run recorded the
                        market as SKIPPED (budget / deadline, with the
                        reason), or the market was never read at all.
  STALE                 a successful read exists but is older than the SLA
                        and nothing recorded explains why it was not
                        refreshed.

THE SLA IS THE EXISTING ONE: bettor_paper_ledger.MARK_STALE_AFTER_S (300 s).
Nothing here lowers it or relabels an old read as fresh. FRESH + QUIET_VALID
are the "freshly manageable" positions; the FRESH RATE is their share of the
MARKABLE positions (everything but EXTERNAL_UNAVAILABLE) and the
STALE-MANAGEMENT RATE is 1 - FRESH RATE. Unread is UNAVAILABLE, never zero.

PER POSITION (`position_rows`): the mark (source observation, timestamp, age,
best bid / ask of the held side, executable exit depth at the mark and in
total), the valuation used (the newest stored valuation of the position's own
contract: id, probability, age against the Pinnacle freshness limit), the
residual quantity reconciled against the ledger's FILL / SALE / SETTLEMENT
entries, the settlement identity and its fingerprint, and the standing
protective order's state.

THE ALLOCATION RAIL (`allocation_refusal`): a new ENTRY for a strategy whose
markable open positions have a stale-management rate above
MAX_STALE_MANAGEMENT_RATE is refused (bettor_paper_ledger.submit_order calls
it under the account lock). It only ever adds a refusal.

Read-only except `record_refusal` (append-only, migration 270).
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from . import bettor_paper_ledger as L

VERSION = "PAPER_MARK_FRESHNESS_V1"

#: THE DECLARED SLA: the ledger's own mark staleness threshold (300 s).
SLA_S = float(L.MARK_STALE_AFTER_S)

FRESH = "FRESH"
QUIET_VALID = "QUIET_VALID"
STALE = "STALE"
FEED_GAP = "FEED_GAP"
UNMARKED = "UNMARKED"
EXTERNAL_UNAVAILABLE = "EXTERNAL_UNAVAILABLE"
CLASSES = (FRESH, QUIET_VALID, STALE, FEED_GAP, UNMARKED,
           EXTERNAL_UNAVAILABLE)
MARKABLE = tuple(c for c in CLASSES if c != EXTERNAL_UNAVAILABLE)
FRESHLY_MANAGEABLE = (FRESH, QUIET_VALID)

RULES = {
    EXTERNAL_UNAVAILABLE: (
        "the newest successful book read carries a terminal market state "
        "(expired / closed / terminated / settled / resolved / closing "
        "auction; permanent, any age) or a not-open transient state (halted"
        " / suspended / pre-open / paused) read inside the %.0fs SLA; the "
        "observation id is the evidence. Excluded from markable." % SLA_S),
    FRESH: (
        "newest successful book read <= %.0fs old, an executable exit price "
        "for the held side, and that price differs from the previous "
        "successful read (or there is none)" % SLA_S),
    QUIET_VALID: (
        "newest successful book read <= %.0fs old with an executable exit "
        "price UNCHANGED from the previous successful read" % SLA_S),
    UNMARKED: (
        "newest successful book read <= %.0fs old, but the side a close "
        "consumes publishes no executable level (no mark price)" % SLA_S),
    FEED_GAP: (
        "no successful read <= %.0fs old and our read path explains it: "
        "the newest attempt after the last success failed (error on the "
        "observation), the latest held-mark refresh run recorded it "
        "SKIPPED with its reason, or the market was never read" % SLA_S),
    STALE: (
        "a successful read exists but is > %.0fs old and no failure or "
        "skip was recorded after it" % SLA_S),
}

#: THE VENUE'S TERMINAL MARKET STATES (a market that has ended; permanent):
#: workers.mirror_shadow.STATE_TERMINAL plus the settled / resolved words
#: bettor_xavier_standing_orders treats as closed.
TERMINAL_MARKET_STATES = frozenset({
    "MARKET_STATE_EXPIRED", "MARKET_STATE_CLOSED", "MARKET_STATE_TERMINATED",
    "MARKET_STATE_MATCH_AND_CLOSE_AUCTION", "MARKET_STATE_SETTLED",
    "MARKET_STATE_RESOLVED", "EXPIRED", "CLOSED", "SETTLED", "RESOLVED"})
#: NOT OPEN, BUT IT REOPENS: evidence only while read inside the SLA.
TRANSIENT_NOT_OPEN_STATES = frozenset({
    "MARKET_STATE_HALTED", "MARKET_STATE_SUSPENDED", "MARKET_STATE_PREOPEN",
    "MARKET_STATE_PAUSED", "HALTED", "SUSPENDED", "PREOPEN", "PAUSED"})

#: THE OPERATING TARGET the repair is measured against (reported, never a
#: gate): >= 95% of MARKABLE open positions FRESH or QUIET_VALID.
TARGET_FRESH_RATE = 0.95

#: THE ALLOCATION RAIL'S PREDECLARED THRESHOLD -- STRICT (P1 closeout, PM
#: directive "any applicable stale position blocks NEW PAPER entry"; was
#: 0.20). A strategy with ANY markable open position NOT freshly manageable
#: (not FRESH / QUIET_VALID) may not open a new ENTRY: its book is not being
#: managed on current evidence, so it is not grown. EXIT / REDUCE /
#: protection are never blocked by this rail. Tightening only.
MAX_STALE_MANAGEMENT_RATE = 0.0

R_STALE_MANAGEMENT_BLOCKS_ALLOCATION = (
    "STRATEGY_OPEN_POSITIONS_CANNOT_BE_FRESHLY_MANAGED")
R_STALE_MANAGEMENT_UNREADABLE = (
    "STRATEGY_STALE_MANAGEMENT_RATE_COULD_NOT_BE_READ")

K_ENTRY = "ENTRY_ALLOCATION_STALE_MANAGEMENT"
#: NO NEW PAPER EXPOSURE WHILE MANAGEMENT INTEGRITY HAS FAILED -- STRICT
#: (P1 closeout): beside the stale-mark rail, a new ENTRY of a strategy is
#: refused when ANY APPLICABLE open position of it (every open position
#: except one whose market is EXTERNAL_UNAVAILABLE -- terminal / halted at
#: the venue, where no protection can rest) (a) has no review whose
#: management packet was complete (an unreviewed position included), or (b)
#: has protection continuity other than PROTECTED_RESTING, or (c) shows a
#: broken one-live-protection invariant / unknown protection state.
#: Unreadable -> refused (fail closed). EXIT / REDUCE / protection orders
#: are never blocked. Tightening only.
R_PACKETS_BLOCK_ALLOCATION = (
    "STRATEGY_OPEN_POSITIONS_MANAGEMENT_PACKETS_INCOMPLETE")
R_PROTECTION_BLOCKS_ALLOCATION = (
    "STRATEGY_OPEN_POSITIONS_PROTECTION_CONTINUITY_FAILED")
R_INTEGRITY_UNREADABLE = "STRATEGY_MANAGEMENT_INTEGRITY_COULD_NOT_BE_READ"
#: (P1: no grace -- a position awaiting its first management is reported as
#: such, and it BLOCKS growth like any other incomplete packet until Xavier
#: has protected and reviewed it. = xavier_management.
#: PAPER_FIRST_REVIEW_BOUND_S, kept for the report only.)
FIRST_MANAGEMENT_GRACE_S = 120.0
#: protection states that refuse growth at ANY count (the invariant broken)
PROTECTION_INVARIANT_BROKEN = ("PROTECTION_MULTIPLE_LIVE_ORDERS",
                               "PROTECTION_STATE_UNKNOWN")
K_PACKET = "XAVIER_PACKET_INCOMPLETE"

HELD_INTENT = {"LONG": "ORDER_INTENT_BUY_LONG",
               "SHORT": "ORDER_INTENT_BUY_SHORT"}


def _ep(v):
    return L._epoch(v)


def _j(v):
    return L._j(v)


# ═════════════════════════════════════════════════════════════════════
# THE PURE PARTS
# ═════════════════════════════════════════════════════════════════════

def book_mark(obs: dict | None, holding_side: str) -> dict:
    """The held side's mark on one observation (pure): best exit price
    (what a close receives: the executable BID of the held side), the
    executable ask of the held side, exit depth at the mark and in total."""
    from . import bettor_book_snapshot as BS
    if not obs:
        return {"price": None, "why": "NO_OBSERVATION"}
    md = {"bids": _j(obs.get("bids")) or [],
          "offers": _j(obs.get("offers")) or []}
    held = HELD_INTENT.get(str(holding_side), "ORDER_INTENT_BUY_LONG")
    ex = BS.exit_ladder(md, held_intent=held)
    acq = BS.acquisition_ladder(md, intent=held)
    ask = None
    if acq.get("ok") and acq.get("levels"):
        ask = float(acq["levels"][0]["acquisition_price"])
    if not ex.get("ok"):
        return {"price": None, "bid": None, "ask": ask,
                "exit_depth_at_mark": 0.0, "exit_depth_total": 0.0,
                "why": ex.get("refusal") or "NO_EXIT_SIDE"}
    return {"price": float(ex["best_exit_price"]),
            "bid": float(ex["best_exit_price"]), "ask": ask,
            "exit_depth_at_mark": float(ex.get("size_at_best") or 0.0),
            "exit_depth_total": float(ex.get("displayed_depth") or 0.0),
            "why": None}


#: Where a mark's observation came from (pure, by its recorded basis /
#: source): the P0 market-data telemetry's per-source held-mark counts.
MARK_FEEDS = ("INSTITUTIONAL_STREAM", "RETAIL_STREAM", "HARVEST", "REST",
              "PUBLIC_GATEWAY")


def mark_source(obs: dict | None) -> str | None:
    if not obs:
        return None
    basis = str(obs.get("read_basis") or "")
    src = str(obs.get("source") or "")
    if basis == "HELD_MARK_INSTITUTIONAL_STREAM" or \
            src.startswith("PAPER_INSTITUTIONAL_STREAM"):
        return "INSTITUTIONAL_STREAM"
    if basis == "HELD_MARK_STREAM" or src.startswith("PAPER_MARKET_STREAM"):
        return "RETAIL_STREAM"
    if basis == "HELD_MARK_PUBLIC_GATEWAY" or src.startswith(
            "PAPER_PUBLIC_GATEWAY"):
        return "PUBLIC_GATEWAY"
    if basis == "HELD_MARK_REFRESH_SHARED_READ" or src.endswith(
            ":SHARED_READ"):
        return "HARVEST"
    return "REST"


def feed_summary(rows: list, *, now: float, sla_s: float = SLA_S) -> dict:
    """PURE: per-source counts of the held marks (the newest successful
    observation of every open position) -- all of them and the freshly
    manageable ones -- and the OLDEST held-mark age among markable
    positions (never-read positions counted apart, never as 0)."""
    by = {f: 0 for f in MARK_FEEDS}
    fresh = {f: 0 for f in MARK_FEEDS}
    oldest, never = None, 0
    for r in rows:
        m = r.get("mark") or {}
        f = m.get("feed")
        if f in by:
            by[f] += 1
            if r.get("class") in FRESHLY_MANAGEABLE:
                fresh[f] += 1
        if r.get("class") == EXTERNAL_UNAVAILABLE:
            continue
        if m.get("observed_at") is None:
            never += 1
            continue
        age = max(0.0, float(now) - float(m["observed_at"]))
        oldest = age if oldest is None else max(oldest, age)
    return {"held_marks_by_source": by,
            "fresh_marks_by_source": fresh,
            "oldest_held_mark_age_s": None if oldest is None
            else round(oldest, 3),
            "never_read_markable": never, "sla_s": sla_s,
            "oldest_within_sla": None if oldest is None
            else oldest <= sla_s}


def classify(*, now: float, holding_side: str, last_ok: dict | None,
             prev_ok: dict | None = None, last_attempt: dict | None = None,
             run_outcome: dict | None = None, sla_s: float = SLA_S) -> dict:
    """EXACTLY ONE CLASS for one open position (pure). Inputs:
      last_ok / prev_ok  the two newest SUCCESSFUL observations of the
                         market {obs_id, at, bids, offers, market_state}
      last_attempt       the newest observation of ANY kind {obs_id, at,
                         error}
      run_outcome        the latest held-mark refresh run's record of this
                         market {outcome, why, run_at}
    Returns {class, reason, rule, mark{...}, evidence{...}}."""
    now = float(now)
    mark = book_mark(last_ok, holding_side) if last_ok else {
        "price": None, "why": "NEVER_READ"}
    age = None if not last_ok else round(max(0.0, now - float(
        last_ok["at"])), 3)
    st = str((last_ok or {}).get("market_state") or "").upper()
    ev: dict[str, Any] = {"last_ok_obs_id": (last_ok or {}).get("obs_id"),
                          "market_state": (last_ok or {}).get(
                              "market_state")}

    def out(cls, reason):
        return {"class": cls, "reason": reason, "rule": RULES[cls],
                "mark": dict(mark, age_s=age, observed_at=(last_ok or {}).get(
                    "at"), obs_id=(last_ok or {}).get("obs_id"),
                    source=(None if not last_ok else
                            "paper_book_observations:%s" % last_ok["obs_id"]),
                    market_state=(last_ok or {}).get("market_state"),
                    read_basis=(last_ok or {}).get("read_basis"),
                    feed=mark_source(last_ok)),
                "evidence": ev}

    if last_ok and st in TERMINAL_MARKET_STATES:
        return out(EXTERNAL_UNAVAILABLE, "MARKET_STATE_TERMINAL:%s" % st)
    if last_ok and st in TRANSIENT_NOT_OPEN_STATES and age <= sla_s:
        return out(EXTERNAL_UNAVAILABLE, "MARKET_NOT_OPEN:%s" % st)
    if last_ok and age <= sla_s:
        if mark.get("price") is None:
            return out(UNMARKED, "NO_EXECUTABLE_EXIT_LEVEL_IN_A_CURRENT_BOOK"
                       ":%s" % (mark.get("why") or "NO_EXIT_SIDE"))
        if prev_ok:
            pm = book_mark(prev_ok, holding_side)
            ev["previous_obs_id"] = prev_ok.get("obs_id")
            ev["previous_price"] = pm.get("price")
            if pm.get("price") is not None and abs(
                    float(pm["price"]) - float(mark["price"])) < 1e-9:
                return out(QUIET_VALID, "RE_READ_INSIDE_SLA_PRICE_UNCHANGED")
            return out(FRESH, "RE_READ_INSIDE_SLA_PRICE_CHANGED")
        return out(FRESH, "FIRST_READ_INSIDE_SLA")
    # ── no successful read inside the SLA ───────────────────────────
    la = last_attempt or {}
    ok_at = float(last_ok["at"]) if last_ok else None
    if la.get("error") and (ok_at is None or float(la["at"]) > ok_at):
        ev["failed_obs_id"] = la.get("obs_id")
        return out(FEED_GAP, "READ_FAILED:%s" % str(la["error"])[:120])
    ro = run_outcome or {}
    if str(ro.get("outcome") or "").startswith(("SKIPPED", "READ_FAILED")) \
            and (ok_at is None or float(ro.get("run_at") or 0) > ok_at):
        ev["refresh_run_id"] = ro.get("run_id")
        return out(FEED_GAP, "%s:%s" % (ro["outcome"], ro.get("why") or ""))
    if not last_ok:
        return out(FEED_GAP, "NEVER_READ")
    return out(STALE, "MARK_OLDER_THAN_%dS_NO_RECORDED_ATTEMPT" % int(sla_s))


def summarize(rows: list, *, sla_s: float = SLA_S) -> dict:
    """COUNTS, RATES AND THE RULE FOR EACH (pure). Every row carries exactly
    one class; `classified == open_positions` is asserted by the caller's
    construction and reported. Rates are None (never 0) with nothing
    markable."""
    counts = {c: 0 for c in CLASSES}
    for r in rows:
        counts[r["class"]] += 1
    markable = sum(counts[c] for c in MARKABLE)
    fm = sum(counts[c] for c in FRESHLY_MANAGEABLE)
    rate = None if markable == 0 else round(fm / markable, 6)
    by_strategy: dict = {}
    for r in rows:
        s = by_strategy.setdefault(r.get("strategy") or "UNKNOWN",
                                   {c: 0 for c in CLASSES})
        s[r["class"]] += 1
    strat = {}
    for k, c in sorted(by_strategy.items()):
        mk = sum(c[x] for x in MARKABLE)
        f = sum(c[x] for x in FRESHLY_MANAGEABLE)
        strat[k] = dict(c, markable=mk, freshly_manageable=f,
                        fresh_rate=None if mk == 0 else round(f / mk, 6),
                        stale_management_rate=(
                            None if mk == 0 else round(1 - f / mk, 6)),
                        allocation_blocked=(mk > 0 and (1 - f / mk)
                                            > MAX_STALE_MANAGEMENT_RATE))
    return {
        "open_positions": len(rows), "classified": len(rows),
        "counts": {c: {"count": counts[c], "rule": RULES[c]}
                   for c in CLASSES},
        "markable": markable, "freshly_manageable": fm,
        "fresh_rate": rate,
        "stale_management_rate": None if rate is None else round(1 - rate, 6),
        "target_fresh_rate": TARGET_FRESH_RATE,
        "meets_target": None if rate is None else rate >= TARGET_FRESH_RATE,
        "sla_s": sla_s,
        "rates_rule": ("fresh_rate = (FRESH + QUIET_VALID) / markable; "
                       "markable = every open position except "
                       "EXTERNAL_UNAVAILABLE (with evidence); "
                       "stale_management_rate = 1 - fresh_rate; null when "
                       "nothing is markable, never 0"),
        "allocation_rail": {
            "max_stale_management_rate": MAX_STALE_MANAGEMENT_RATE,
            "refusal": R_STALE_MANAGEMENT_BLOCKS_ALLOCATION,
            "rule": ("a new ENTRY of a strategy whose markable open "
                     "positions have stale_management_rate > %.2f is "
                     "refused and recorded" % MAX_STALE_MANAGEMENT_RATE)},
        "by_strategy": strat}


def settlement_fingerprint(identity: dict) -> str | None:
    """A stable fingerprint of the settlement identity (pure): None unless
    the payout event and complement flag are both known."""
    if not identity or identity.get("payout_event") is None or \
            identity.get("payout_is_complement") is None:
        return None
    body = json.dumps({k: identity.get(k) for k in (
        "us_market_slug", "holding_side", "payout_event",
        "payout_is_complement", "fixture")}, sort_keys=True, default=str)
    return "settle:" + hashlib.sha256(body.encode()).hexdigest()[:20]


#: THE PROTECTION-CONTINUITY STATES (pure, protection_state). Only
#: PS_PROTECTED is explicitly valid, active and quantity-matched; every
#: other state is packet-INCOMPLETE (xavier_packet.VALID_PROTECTION_STATES).
PS_PROTECTED = "PROTECTED_RESTING"
PS_QTY_MISMATCH = "PROTECTION_QTY_DIFFERS_FROM_OPEN_QTY"
PS_CANCEL_PENDING = "PROTECTION_CANCEL_PENDING"
PS_UNPROTECTED = "UNPROTECTED_NO_STANDING_ORDER"
PS_EXPIRED = "PROTECTION_EXPIRED"
PS_CANCELLED = "PROTECTION_CANCELLED"
PS_PENDING_SIM = "PROTECTION_PENDING_SIMULATION"
PS_MULTIPLE = "PROTECTION_MULTIPLE_LIVE_ORDERS"
PS_UNKNOWN = "PROTECTION_STATE_UNKNOWN"
PROTECTION_STATES = (PS_PROTECTED, PS_QTY_MISMATCH, PS_CANCEL_PENDING,
                     PS_UNPROTECTED, PS_EXPIRED, PS_CANCELLED,
                     PS_PENDING_SIM, PS_MULTIPLE, PS_UNKNOWN)
#: the order states that are ACTIVELY resting on the simulated book
_ACTIVE_ORDER_STATES = ("RESTING", "PARTIALLY_FILLED")
#: quantity agreement tolerance (the ledger's numeric(18,6) grain)
PROTECTION_QTY_EPS = 1e-6


def _order_epoch(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return L._epoch(v)
    except Exception:                                           # noqa: BLE001
        return None


def protection_state(standing: list, open_qty: float, *,
                     now: float | None = None) -> dict:
    """THE STANDING PROTECTIVE ORDER'S CONTINUITY STATE (pure) from the
    STANDING_PROTECTION orders of one position.

    PROTECTED_RESTING (the only valid state) needs exactly ONE order, in
    RESTING or PARTIALLY_FILLED, not past its GTD expiry at `now`, whose
    remaining qty equals the open qty. Everything else is named and
    INCOMPLETE: no order (UNPROTECTED), a cancel in flight (CANCEL_PENDING),
    an order past expiry the simulator has not yet terminated (EXPIRED), a
    terminal CANCELED / EXPIRED / REJECTED row handed in (CANCELLED /
    EXPIRED), an order not yet simulated (PENDING_SIMULATION), more than one
    live-or-potentially-live order (MULTIPLE -- the one-order invariant is
    broken), a quantity mismatch, or any state not recognised (UNKNOWN)."""
    rows = [dict(s) for s in (standing or [])]
    base = {"known": True, "active": False, "qty_matched": False,
            "orders": len(rows)}

    def out(state, s=None, **kw):
        d = dict(base, state=state,
                 order_id=None if s is None else s.get("order_id"),
                 order_state=None if s is None else s.get("state"))
        d.update(kw)
        return d

    open_states = set(L.OPEN_STATES)
    live = [s for s in rows if s.get("state") in open_states]
    unknown = [s for s in rows if s.get("state") not in open_states
               and s.get("state") not in L.TERMINAL_STATES]
    if unknown:
        return out(PS_UNKNOWN, unknown[0])
    if len(live) > 1:
        return out(PS_MULTIPLE, live[0],
                   order_ids=[s.get("order_id") for s in live])
    if not live:
        term = [s for s in rows if s.get("state") in L.TERMINAL_STATES]
        if term:
            st = term[0].get("state")
            if st == "EXPIRED":
                return out(PS_EXPIRED, term[0])
            if st in ("CANCELED", "REJECTED"):
                return out(PS_CANCELLED, term[0])
        return out(PS_UNPROTECTED)
    s = live[0]
    if s.get("state") == "CANCEL_PENDING":
        return out(PS_CANCEL_PENDING, s)
    try:
        rem = float(s["qty"]) - float(s["filled_qty"])
    except Exception:                                           # noqa: BLE001
        return out(PS_UNKNOWN, s)
    exp = _order_epoch(s.get("expires_at"))
    detail = {"remaining_qty": rem, "limit_price": L.f(s.get("limit_price")),
              "expires_at": exp}
    if now is not None and exp is not None and float(now) >= exp:
        return out(PS_EXPIRED, s, **detail)
    if s.get("state") not in _ACTIVE_ORDER_STATES:
        return out(PS_PENDING_SIM, s, **detail)
    if abs(rem - float(open_qty)) > PROTECTION_QTY_EPS:
        return out(PS_QTY_MISMATCH, s, active=True, **detail)
    return out(PS_PROTECTED, s, active=True, qty_matched=True, **detail)


# ═════════════════════════════════════════════════════════════════════
# THE READS
# ═════════════════════════════════════════════════════════════════════

async def _has(conn, name: str) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        name))
    except Exception:                                           # noqa: BLE001
        return False


OBS_SQL = """
    SELECT s.slug, o.obs_id, o.observed_at, o.bids, o.offers,
           o.market_state, o.error, o.rn, o.read_basis, o.source
      FROM unnest($1::text[]) AS s(slug)
      CROSS JOIN LATERAL (
          SELECT obs_id, observed_at, bids, offers, market_state, error,
                 read_basis, source,
                 row_number() OVER (ORDER BY observed_at DESC,
                                    obs_id DESC) AS rn
            FROM (SELECT * FROM paper_book_observations
                   WHERE us_market_slug = s.slug AND error IS NULL
                   ORDER BY observed_at DESC, obs_id DESC LIMIT 2) q) o
"""
ATTEMPT_SQL = """
    SELECT s.slug, o.obs_id, o.observed_at, o.error
      FROM unnest($1::text[]) AS s(slug)
      CROSS JOIN LATERAL (
          SELECT obs_id, observed_at, error FROM paper_book_observations
           WHERE us_market_slug = s.slug
           ORDER BY observed_at DESC, obs_id DESC LIMIT 1) o
"""


async def market_evidence(conn, slugs: list, *, account_id: str) -> dict:
    """{slug: {last_ok, prev_ok, last_attempt, run_outcome}} in three
    batched reads (index paper_book_observations (slug, observed_at))."""
    slugs = sorted(set(slugs))
    out: dict = {s: {"last_ok": None, "prev_ok": None, "last_attempt": None,
                     "run_outcome": None} for s in slugs}
    if not slugs:
        return out
    for r in await conn.fetch(OBS_SQL, slugs):
        rec = {"obs_id": r["obs_id"], "at": _ep(r["observed_at"]),
               "bids": r["bids"], "offers": r["offers"],
               "market_state": r["market_state"],
               "read_basis": r["read_basis"], "source": r["source"]}
        out[r["slug"]]["last_ok" if r["rn"] == 1 else "prev_ok"] = rec
    for r in await conn.fetch(ATTEMPT_SQL, slugs):
        out[r["slug"]]["last_attempt"] = {"obs_id": r["obs_id"],
                                          "at": _ep(r["observed_at"]),
                                          "error": r["error"]}
    run = await latest_run(conn, account_id)
    if run:
        oc = run.get("outcomes") or {}
        for s in slugs:
            if s in oc:
                out[s]["run_outcome"] = dict(oc[s], run_id=run["run_id"],
                                             run_at=run["started_at"])
    return out


async def latest_run(conn, account_id: str) -> dict | None:
    """The newest held-mark refresh run of this account (migration 270);
    None when the table is absent or no run is recorded."""
    if not await _has(conn, "paper_mark_refresh_runs"):
        return None
    r = await conn.fetchrow(
        "SELECT * FROM paper_mark_refresh_runs WHERE account_id = $1 "
        " ORDER BY started_at DESC, run_id DESC LIMIT 1", account_id)
    if r is None:
        return None
    d = dict(r)
    d["outcomes"] = _j(d.get("outcomes")) or {}
    d["budget"] = _j(d.get("budget")) or {}
    for k in ("sources", "market_data"):              # migration 306
        if k in d:
            d[k] = _j(d.get(k)) or {}
    for k in ("started_at", "finished_at", "recorded_at"):
        d[k] = _ep(d.get(k))
    return d


async def classify_positions(conn, account_id: str, *, now: float,
                             positions: list | None = None) -> list:
    """Every open position of the account with its class (marks only: the
    rail's read)."""
    pos = positions if positions is not None else await L.positions(
        conn, account_id)
    ev = await market_evidence(conn, [p["us_market_slug"] for p in pos],
                               account_id=account_id)
    rows = []
    for p in pos:
        e = ev.get(p["us_market_slug"]) or {}
        c = classify(now=now, holding_side=p["holding_side"],
                     last_ok=e.get("last_ok"), prev_ok=e.get("prev_ok"),
                     last_attempt=e.get("last_attempt"),
                     run_outcome=e.get("run_outcome"))
        rows.append(dict(c, position_key=p["position_key"],
                         group_id=p["group_id"],
                         strategy=p.get("strategy"),
                         us_market_slug=p["us_market_slug"],
                         holding_side=p["holding_side"],
                         open_qty=p["open_qty"],
                         exposure_usd=p.get("cost_basis_usd"),
                         last_attempt=e.get("last_attempt"),
                         refresh_run=e.get("run_outcome")))
    return rows


LEDGER_QTY_SQL = """
    SELECT position_key,
           coalesce(sum((detail->>'qty')::numeric)
                    FILTER (WHERE kind = 'FILL'), 0) AS bought,
           coalesce(sum((detail->>'qty')::numeric)
                    FILTER (WHERE kind = 'SALE'), 0) AS sold,
           coalesce(sum((detail->>'qty')::numeric)
                    FILTER (WHERE kind = 'SETTLEMENT'), 0) AS settled
      FROM paper_ledger
     WHERE account_id = $1 AND position_key = ANY($2::text[])
       AND kind IN ('FILL', 'SALE', 'SETTLEMENT')
     GROUP BY position_key
"""

IDENTITY_SQL = """
    SELECT DISTINCT ON (o.group_id, o.us_market_slug, o.holding_side)
           o.group_id, o.us_market_slug, o.holding_side, o.fixture,
           d.valuation_id, c.payout_event, c.payout_is_complement
      FROM paper_orders o
      LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
      LEFT JOIN external_valuations c ON c.id = d.valuation_id
     WHERE o.account_id = $1 AND o.group_id = ANY($2::text[])
       AND o.role = 'ENTRY'
     ORDER BY o.group_id, o.us_market_slug, o.holding_side, o.decided_at
"""

STANDING_SQL = """
    SELECT order_id, group_id, us_market_slug, holding_side, state, qty,
           filled_qty, limit_price, expires_at
      FROM paper_orders
     WHERE account_id = $1 AND group_id = ANY($2::text[])
       AND role = 'STANDING_PROTECTION' AND state = ANY($3::text[])
"""


async def residuals(conn, account_id: str, positions: list) -> dict:
    """{position_key: {open_qty, ledger_open_qty, reconciled}}: the fills'
    open quantity against the ledger's FILL - SALE - SETTLEMENT entries."""
    keys = [p["position_key"] for p in positions]
    led = {r["position_key"]: r for r in await conn.fetch(
        LEDGER_QTY_SQL, account_id, keys)} if keys else {}
    out = {}
    for p in positions:
        r = led.get(p["position_key"])
        lo = None if r is None else float(
            r["bought"] - r["sold"] - r["settled"])
        out[p["position_key"]] = {
            "open_qty": p["open_qty"], "ledger_open_qty": lo,
            "reconciled": lo is not None and abs(
                lo - float(p["open_qty"])) <= 1e-6,
            "basis": ("paper_fills (bought - sold - settled) against "
                      "paper_ledger FILL - SALE - SETTLEMENT qty")}
    return out


async def identities(conn, account_id: str, positions: list) -> dict:
    groups = sorted({p["group_id"] for p in positions})
    rows = await conn.fetch(IDENTITY_SQL, account_id, groups) if groups \
        else []
    by = {(r["group_id"], r["us_market_slug"], r["holding_side"]): r
          for r in rows}
    out = {}
    for p in positions:
        r = by.get((p["group_id"], p["us_market_slug"], p["holding_side"]))
        ident = {"us_market_slug": p["us_market_slug"],
                 "holding_side": p["holding_side"],
                 "fixture": p.get("fixture"),
                 "entry_valuation_id": None if r is None else r[
                     "valuation_id"],
                 "payout_event": None if r is None else r["payout_event"],
                 "payout_is_complement": None if r is None else r[
                     "payout_is_complement"]}
        fp = settlement_fingerprint(ident)
        out[p["position_key"]] = dict(
            ident, fingerprint=fp, identity_ok=fp is not None,
            why=None if fp else "NO_ENTRY_VALUATION_SETTLEMENT_IDENTITY")
    return out


async def protections(conn, account_id: str, positions: list, *,
                      now: float | None = None) -> dict:
    groups = sorted({p["group_id"] for p in positions})
    rows = await conn.fetch(STANDING_SQL, account_id, groups,
                            list(L.OPEN_STATES)) if groups else []
    out = {}
    for p in positions:
        st = [dict(r) for r in rows if r["group_id"] == p["group_id"]
              and r["us_market_slug"] == p["us_market_slug"]
              and r["holding_side"] == p["holding_side"]]
        out[p["position_key"]] = protection_state(st, p["open_qty"],
                                                  now=now)
    return out


async def valuations(conn, account_id: str, positions: list, *,
                     now: float, limit_s: float | None) -> dict:
    """The newest stored valuation of each group's own contract
    (xavier_freshness.LATEST_VALUATION_SQL, within its lookback): the
    valuation id a management decision would stand on, with its age
    against the Pinnacle freshness limit."""
    from . import xavier_freshness as XF
    groups = sorted({p["group_id"] for p in positions})
    rows = {}
    if groups:
        rows = {r["group_id"]: r for r in await conn.fetch(
            XF.LATEST_VALUATION_SQL, groups,
            float(now) - XF.CONTEXT_VALUATION_LOOKBACK_S)}
    out = {}
    for p in positions:
        r = rows.get(p["group_id"])
        if r is None:
            out[p["position_key"]] = {"valuation_id": None, "fresh": False,
                                      "limit_s": limit_s,
                                      "why": "NO_STORED_VALUATION_OF_THIS_"
                                             "CONTRACT_IN_THE_LOOKBACK"}
            continue
        at = _ep(r["observed_at"])
        age = None if at is None else round(float(now) - at, 3)
        out[p["position_key"]] = {
            "valuation_id": r["id"], "probability": L.f(r["probability"]),
            "observed_at": at, "age_s": age, "limit_s": limit_s,
            "fresh": (limit_s is not None and age is not None
                      and 0 <= age <= float(limit_s)),
            "source": "external_valuations:%s" % r["id"]}
    return out


async def pinnacle_limit_s(conn, account_id: str) -> float | None:
    """The EXISTING Pinnacle freshness limit: the active session's frozen
    `entry.pinnacle_max_age_s`, else the odds source's own constant when its
    module is loaded; None otherwise (then nothing is called fresh)."""
    from . import xavier_freshness as XF
    try:
        v = await conn.fetchval(
            "SELECT (config->'entry'->>'pinnacle_max_age_s')::float8 "
            "  FROM paper_sessions WHERE account_id = $1 "
            "   AND status = 'ACTIVE' ORDER BY started_at DESC LIMIT 1",
            account_id)
        if v is not None:
            return float(v)
    except Exception:                                           # noqa: BLE001
        pass
    return XF.default_limit_s()


async def position_rows(conn, account_id: str, *, now: float,
                        positions: list | None = None) -> list:
    """EVERY OPEN POSITION, CLASSIFIED, WITH ITS MANAGEMENT FACTS."""
    pos = positions if positions is not None else await L.positions(
        conn, account_id)
    rows = await classify_positions(conn, account_id, now=now, positions=pos)
    res = await residuals(conn, account_id, pos)
    ids = await identities(conn, account_id, pos)
    prot = await protections(conn, account_id, pos, now=now)
    vals = await valuations(conn, account_id, pos, now=now,
                            limit_s=await pinnacle_limit_s(conn, account_id))
    for r in rows:
        k = r["position_key"]
        r.update(residual=res.get(k), settlement=ids.get(k),
                 protection=prot.get(k), valuation=vals.get(k))
    return rows


async def read(conn, account_id: str | None = None, *, now: float | None = None,
               rows_limit: int | None = 500) -> dict:
    """THE FRESHNESS READ MODEL: status, the summary (counts with their
    rules, rates, by strategy), the latest refresh run and the per-position
    rows. Never raises: a failed read is UNAVAILABLE with its reason, never
    zero counts."""
    acct = account_id or L.ACCOUNT_ID
    at = float(now if now is not None else time.time())
    try:
        async with conn.transaction():
            rows = await position_rows(conn, acct, now=at)
            run = await latest_run(conn, acct)
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNAVAILABLE", "version": VERSION,
                "account_id": acct, "as_of": at,
                "why": "FRESHNESS_READ_FAILED: %s: %s"
                       % (type(exc).__name__, str(exc)[:160]),
                "counts": None, "fresh_rate": None,
                "stale_management_rate": None, "rules": RULES}
    summ = summarize(rows)
    rows_sorted = sorted(rows, key=lambda r: (
        r["class"] in FRESHLY_MANAGEABLE, -(r["mark"].get("age_s") or 1e12)))
    shown = rows_sorted if rows_limit is None else rows_sorted[:rows_limit]
    return dict(summ, status="OK", version=VERSION, account_id=acct,
                as_of=at, rules=RULES, feeds=feed_summary(rows, now=at),
                refresh={"latest_run": None if run is None else {
                    k: run.get(k) for k in (
                        "run_id", "trigger", "started_at", "finished_at",
                        "held_markets", "due", "not_due", "harvested",
                        "read_attempted", "read_ok", "read_failed",
                        "skipped_budget", "skipped_terminal", "budget",
                        "error", "skipped_cooldown", "stream_books",
                        "institutional_books", "cooldown_waited_s",
                        "sources")},
                    "why": None if run is not None else
                    "NO_HELD_MARK_REFRESH_RUN_RECORDED"},
                positions=shown, positions_truncated=len(shown) < len(rows))


# ═════════════════════════════════════════════════════════════════════
# THE ALLOCATION RAIL AND THE REFUSAL RECORD
# ═════════════════════════════════════════════════════════════════════

async def strategy_stale_management(conn, account_id: str, strategy: str,
                                    *, now: float) -> dict:
    pos = [p for p in await L.positions(conn, account_id)
           if (p.get("strategy") or L.DEFAULT_STRATEGY) == strategy]
    rows = await classify_positions(conn, account_id, now=now, positions=pos)
    s = summarize(rows)
    st = s["by_strategy"].get(strategy) or {}
    return {"strategy": strategy, "open_positions": len(rows),
            "markable": s["markable"],
            "freshly_manageable": s["freshly_manageable"],
            "stale_management_rate": s["stale_management_rate"],
            "counts": {c: st.get(c, 0) for c in CLASSES},
            "not_fresh": [{"position_key": r["position_key"],
                           "class": r["class"], "reason": r["reason"]}
                          for r in rows if r["class"] not in
                          FRESHLY_MANAGEABLE][:20]}


LATEST_PACKET_SQL = """
    SELECT group_id, extract(epoch FROM max(reviewed_at))::float8
               AS reviewed_at,
           bool_and(coalesce((selection->'management_packet'->'gate'
                              ->>'complete')::boolean, false)) AS complete
      FROM (SELECT r.*, max(r.reviewed_at) OVER (PARTITION BY r.group_id)
                       AS newest
              FROM paper_xavier_reviews r
             WHERE r.account_id = $1 AND r.group_id = ANY($2::text[])) q
     WHERE reviewed_at = newest
     GROUP BY group_id
"""


def integrity_verdict(*, positions: list, packets: dict,
                      protections: dict, reviewed_at: dict | None = None,
                      now: float | None = None,
                      classes: dict | None = None) -> dict:
    """Pure. STRICT: the strategy's management integrity over its APPLICABLE
    open positions (all but EXTERNAL_UNAVAILABLE markets, `classes` by
    position key): ANY incomplete packet or ANY protection other than
    PROTECTED_RESTING refuses; any broken invariant refuses. Positions
    awaiting their first management are named (and count)."""
    rv = reviewed_at or {}
    cls = classes or {}
    excluded = [p["position_key"] for p in positions
                if cls.get(p["position_key"]) == EXTERNAL_UNAVAILABLE]
    awaiting = []
    if now is not None:
        for p in positions:
            lf = p.get("last_fill_at")
            last = rv.get(p["group_id"])
            if lf is not None and (last is None or float(last) < float(lf)) \
                    and float(now) - float(lf) <= FIRST_MANAGEMENT_GRACE_S:
                awaiting.append(p["position_key"])
    all_pos = positions
    positions = [p for p in positions if p["position_key"] not in excluded]
    n = len(positions)
    incomplete = [p["position_key"] for p in positions
                  if not packets.get(p["group_id"])]
    pstate = {p["position_key"]: (protections.get(p["position_key"]) or {})
              .get("state") for p in positions}
    unprotected = [k for k, st in pstate.items()
                   if st != PS_PROTECTED]
    broken = [p["position_key"] for p in all_pos
              if (protections.get(p["position_key"]) or {}).get("state")
              in PROTECTION_INVARIANT_BROKEN]
    pk_rate = None if n == 0 else len(incomplete) / n
    pr_rate = None if n == 0 else len(unprotected) / n
    refusal = None
    if broken or unprotected:
        refusal = R_PROTECTION_BLOCKS_ALLOCATION
    elif incomplete:
        refusal = R_PACKETS_BLOCK_ALLOCATION
    return {"open_positions": n,
            "rule": "STRICT_ANY_APPLICABLE_POSITION",
            "excluded_external_unavailable": excluded[:20],
            "awaiting_first_management": awaiting[:20],
            "packet_incomplete_rate": pk_rate,
            "protection_failure_rate": pr_rate,
            "max_rate": MAX_STALE_MANAGEMENT_RATE,
            "packet_incomplete": incomplete[:20],
            "protection_not_valid": [{"position_key": k,
                                      "state": pstate[k]}
                                     for k in unprotected][:20],
            "protection_invariant_broken": broken,
            "refusal": refusal}


async def strategy_management_integrity(conn, account_id: str, strategy: str,
                                        *, now: float) -> dict:
    pos = [p for p in await L.positions(conn, account_id)
           if (p.get("strategy") or L.DEFAULT_STRATEGY) == strategy]
    groups = sorted({p["group_id"] for p in pos})
    packets, reviewed = {}, {}
    if groups:
        for r in await conn.fetch(LATEST_PACKET_SQL, account_id, groups):
            packets[r["group_id"]] = bool(r["complete"])
            reviewed[r["group_id"]] = r["reviewed_at"]
    prot = await protections(conn, account_id, pos, now=now)
    rows = await classify_positions(conn, account_id, now=now, positions=pos)
    classes = {r["position_key"]: r["class"] for r in rows}
    return dict(integrity_verdict(positions=pos, packets=packets,
                                  protections=prot, reviewed_at=reviewed,
                                  now=now, classes=classes),
                strategy=strategy)


async def allocation_refusal(conn, *, account_id: str, strategy: str,
                             now: float) -> dict | None:
    """None when the strategy may grow; otherwise the refusal. FAIL CLOSED:
    a rate that cannot be read refuses (its own savepoint, so the caller's
    transaction survives)."""
    try:
        async with conn.transaction():
            m = await strategy_stale_management(conn, account_id, strategy,
                                                now=now)
    except Exception as exc:                                    # noqa: BLE001
        return {"refusal": R_STALE_MANAGEMENT_UNREADABLE,
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                "strategy": strategy}
    rate = m["stale_management_rate"]
    if rate is None or rate <= MAX_STALE_MANAGEMENT_RATE + 1e-12:
        # THE MARKS ARE FRESH ENOUGH; NOW THE PACKETS AND THE PROTECTION
        try:
            async with conn.transaction():
                iv = await strategy_management_integrity(
                    conn, account_id, strategy, now=now)
        except Exception as exc:                                # noqa: BLE001
            return {"refusal": R_INTEGRITY_UNREADABLE,
                    "why": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                    "strategy": strategy}
        if iv["refusal"] is None:
            return None
        return dict(iv, why=(
            "management integrity of %s failed: packet-incomplete %s, "
            "protection-failed %s of %d applicable open positions (strict: "
            "any), invariant broken on %d: no allocation growth until "
            "repaired; EXIT / REDUCE / protection unaffected"
            % (strategy, iv["packet_incomplete_rate"],
               iv["protection_failure_rate"], iv["open_positions"],
               len(iv["protection_invariant_broken"]))))
    return {"refusal": R_STALE_MANAGEMENT_BLOCKS_ALLOCATION,
            "strategy": strategy, "stale_management_rate": rate,
            "max_stale_management_rate": MAX_STALE_MANAGEMENT_RATE,
            "markable": m["markable"],
            "freshly_manageable": m["freshly_manageable"],
            "counts": m["counts"], "not_fresh": m["not_fresh"],
            "why": ("%d of %d markable open positions of %s cannot be "
                    "freshly managed (%.1f%% > %.0f%%, strict: any): no "
                    "allocation growth until they are"
                    % (m["markable"] - m["freshly_manageable"],
                       m["markable"], strategy, 100 * rate,
                       100 * MAX_STALE_MANAGEMENT_RATE))}


async def record_refusal(conn, *, account_id: str, kind: str, refusal: str,
                         at: float, strategy=None, group_id=None,
                         position_key=None, us_market_slug=None,
                         review_id=None, order_key=None, missing=None,
                         detail=None) -> int | None:
    """APPEND ONE REFUSAL (migration 270). Never raises; None when the table
    is absent or the write failed (own savepoint)."""
    if not await _has(conn, "paper_management_refusals"):
        return None
    try:
        async with conn.transaction():
            return await conn.fetchval(
                "INSERT INTO paper_management_refusals (account_id, kind, "
                " refusal, strategy, group_id, position_key, us_market_slug,"
                " review_id, order_key, missing, detail, refused_at) VALUES "
                " ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,"
                " to_timestamp($12)) RETURNING refusal_id",
                account_id, kind, refusal, strategy, group_id, position_key,
                us_market_slug, review_id, order_key,
                json.dumps(list(missing or []), default=str),
                json.dumps(detail or {}, default=str), float(at))
    except Exception:                                           # noqa: BLE001
        return None


async def position_mark(conn, *, account_id: str, slug: str,
                        holding_side: str, now: float) -> dict:
    """ONE POSITION'S MARK CLASS (the Xavier packet's book element)."""
    ev = (await market_evidence(conn, [slug], account_id=account_id)).get(
        slug) or {}
    return classify(now=now, holding_side=holding_side,
                    last_ok=ev.get("last_ok"), prev_ok=ev.get("prev_ok"),
                    last_attempt=ev.get("last_attempt"),
                    run_outcome=ev.get("run_outcome"))


def describe() -> dict:
    return {"version": VERSION, "sla_s": SLA_S, "classes": list(CLASSES),
            "rules": RULES, "markable": list(MARKABLE),
            "freshly_manageable": list(FRESHLY_MANAGEABLE),
            "target_fresh_rate": TARGET_FRESH_RATE,
            "max_stale_management_rate": MAX_STALE_MANAGEMENT_RATE}

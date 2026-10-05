"""SMALL LIVE MEANS BETTOR-ORIGINATED. READ-ONLY TRUTH, NO AUTHORITY.

TWO DIFFERENT THINGS, NEVER CONFUSED:

  LEGACY MIRROR VALIDATION   the old 1:1,000 execution mirror
                             (execmirror_*; and the Kalshi copy lane,
                             kalshi_live_intents): every live row is a COPY of
                             a paper order (or of a paper execution intent's
                             actual lane), sized paper qty / 1,000. Its state
                             comes from the real execmirror_control row --
                             today STOPPED. It is NOT the target system.

  SMALL LIVE -- BETTOR ORIGINATED
                             the target: a real venue order that originated
                             from BETTOR's own decision chain
                                 Derek decision -> allocation -> Archer
                                 execution decision -> real venue order with a
                                 venue acknowledgement.
                             Status is exactly one of STATUSES, derived from
                             stored evidence by `derive_status` (pure):
      NOT_CONFIGURED       no BETTOR-originated execution path exists, and
                           no shadow chain evidence
      SHADOW               no venue path, but the chain runs in shadow (Archer
                           execution decisions recorded; nothing is sent)
      READY_NOT_ACTIVATED  a configured path with a venue, not activated (or
                           activated with no BETTOR-originated venue-acked
                           order yet)
      ACTIVE               BETTOR-originated, venue-acknowledged orders exist
                           and the lane's heartbeat and venue are current
      DEGRADED             such orders exist but the heartbeat or the venue
                           is not current
      STOPPED              the lane's control row says stopped

  ACTIVE IS IMPOSSIBLE WITHOUT a venue-acknowledged order carrying the whole
  chain (`is_bettor_originated`). RN1, mirror and copy orders never count,
  whatever fields they carry. ARCHER'S RECOMMENDATIONS ARE NEVER ORDERS:
  EXECUTE_NOW / SKIP_EXECUTION counts are reported apart from orders
  submitted, venue-acknowledged and filled.

TODAY (this build): `BETTOR_ORIGINATED_PATH_CONFIGURED` is False -- no code
path turns an Archer execution decision into a venue order. Archer is
SHADOW_ONLY (agents/archer.py) and migration 217 refuses ARCHER on every
order, intent, fill, approval and control table; the execution intent's
actual lane (execution_intent.py) submits through the mirror machinery
without an allocation or an Archer decision. So the status is SHADOW when
Archer's shadow estimates exist, else NOT_CONFIGURED -- never ACTIVE.

This module makes no venue call, imports no order / venue / execution /
funded module, and only SELECTs. It changes no control row, cap, scale or
threshold.
"""
from __future__ import annotations

import time

VERSION = "SMALL_LIVE_TRUTH_V1"
TITLE = "SMALL LIVE — BETTOR ORIGINATED"
LEGACY_TITLE = "LEGACY MIRROR VALIDATION"

NOT_CONFIGURED, SHADOW, READY, ACTIVE, DEGRADED, STOPPED = (
    "NOT_CONFIGURED", "SHADOW", "READY_NOT_ACTIVATED", "ACTIVE", "DEGRADED",
    "STOPPED")
STATUSES = (NOT_CONFIGURED, SHADOW, READY, ACTIVE, DEGRADED, STOPPED)

#: No code path in this build submits a venue order from an Archer
#: execution decision (see the module docstring). A build that adds one
#: changes this constant through the approved process, with its own tests.
BETTOR_ORIGINATED_PATH_CONFIGURED = False
#: Why, stated on every surface.
PATH_WHY = ("no BETTOR-originated execution path exists in this build: "
            "Archer is SHADOW ONLY (no submit, cancel or capital authority, "
            "enforced in code and by migration 217), and the only real-venue "
            "order paths on record are the LEGACY MIRROR (paper-order copies "
            "and the execution intents' mirror lane)")

BETTOR = "BETTOR"
NON_BETTOR_ORIGINS = ("RN1", "COPY", "COPY_TRADE", "MIRROR", "LEGACY_MIRROR",
                      "EXECMIRROR", "EXECUTION_MIRROR", "KALSHI_MIRROR",
                      "EXECUTION_INTENT_MIRROR_LANE", "PAPER_ORDER_COPY",
                      "WHALE", "SHADOW")
#: Archer's "execute" recommendations (pinned equal to archer.EXECUTING)
EXECUTING_RECOMMENDATIONS = ("EXECUTE_NOW", "REST_LIMIT", "SPLIT")
RECOMMENDATIONS = ("EXECUTE_NOW", "REST_LIMIT", "SPLIT", "WAIT",
                   "SKIP_EXECUTION")
#: the lane heartbeat older than this is not current
HEARTBEAT_STALE_AFTER_S = 180.0

CHAIN_FIELDS = ("derek_decision_id", "allocation_id", "archer_estimate_id",
                "venue_order_id", "venue_ack_at")


def legacy_label(ctl: dict | None) -> str:
    """The legacy mirror's label, from the REAL control row: STOPPED only
    when it says stopped; RUNNING when enabled and not stopped."""
    if not ctl:
        return LEGACY_TITLE + " — UNAVAILABLE"
    if ctl.get("stopped"):
        return LEGACY_TITLE + " — STOPPED"
    if ctl.get("enabled"):
        return LEGACY_TITLE + " — RUNNING"
    return LEGACY_TITLE + " — DISABLED"


def legacy_state(ctl: dict | None) -> str:
    if not ctl:
        return "UNAVAILABLE"
    return ("STOPPED" if ctl.get("stopped") else
            "RUNNING" if ctl.get("enabled") else "DISABLED")


def is_bettor_originated(order: dict) -> tuple:
    """(True, None) only for a real venue order with the whole BETTOR chain
    and a venue acknowledgement; else (False, the reason). Mirror / RN1 /
    copy origins never count, whatever else they carry."""
    origin = str((order or {}).get("origin") or "").upper()
    if origin in NON_BETTOR_ORIGINS:
        return False, "ORIGIN_%s_IS_NOT_BETTOR" % origin
    if origin != BETTOR:
        return False, "ORIGIN_NOT_BETTOR (%s)" % (origin or "missing")
    for k in CHAIN_FIELDS:
        if not order.get(k):
            return False, "CHAIN_INCOMPLETE: no %s" % k
    if order.get("archer_recommendation") not in EXECUTING_RECOMMENDATIONS:
        return False, ("ARCHER_DID_NOT_RECOMMEND_EXECUTION (%s)"
                       % order.get("archer_recommendation"))
    if order.get("venue") not in ("polymarket_us", "kalshi"):
        return False, "NOT_A_REAL_VENUE (%s)" % order.get("venue")
    return True, None


def derive_status(ev: dict, *, now: float) -> dict:
    """ev = {path_configured, control: {enabled, stopped} | None,
    orders: [candidate venue orders], shadow: {archer_estimates},
    heartbeat_at, venues: {polymarket_us: {state}, kalshi: {state}}}.
    Pure. ACTIVE requires at least one order passing
    `is_bettor_originated`."""
    orders = list(ev.get("orders") or [])
    verified, refused = [], []
    for o in orders:
        ok, why = is_bettor_originated(o)
        (verified if ok else refused).append(o if ok else dict(o, refused=why))
    ctl = ev.get("control")
    shadow = ev.get("shadow") or {}
    venues = ev.get("venues") or {}
    connected = [k for k, v in venues.items()
                 if (v or {}).get("state") == "CONNECTED"]
    hb = ev.get("heartbeat_at")
    hb_ok = hb is not None and now - float(hb) <= HEARTBEAT_STALE_AFTER_S
    path = bool(ev.get("path_configured"))
    if ctl and ctl.get("stopped"):
        status, why = STOPPED, "the lane's control row says stopped"
    elif not path:
        if verified:
            status, why = DEGRADED, ("BETTOR-originated orders are recorded "
                                     "but no configured path exists: "
                                     "inconsistent records")
        elif (shadow.get("archer_estimates") or 0) > 0:
            status, why = SHADOW, (PATH_WHY + "; the chain runs in SHADOW "
                                   "(%d Archer execution decisions recorded, "
                                   "none sent)" % shadow["archer_estimates"])
        else:
            status, why = NOT_CONFIGURED, PATH_WHY
    elif not connected:
        status, why = (DEGRADED if verified else NOT_CONFIGURED,
                       "no venue is connected to the BETTOR-originated lane")
    elif not ctl or not ctl.get("enabled"):
        status, why = READY, "configured and connected, not activated"
    elif not verified:
        status, why = READY, ("activated, but no BETTOR-originated "
                              "venue-acknowledged order exists yet; ACTIVE "
                              "requires one")
    elif hb_ok:
        status, why = ACTIVE, ("%d BETTOR-originated venue-acknowledged "
                               "order(s); heartbeat current" % len(verified))
    else:
        status, why = DEGRADED, ("BETTOR-originated orders exist but the "
                                 "lane heartbeat is %s"
                                 % ("missing" if hb is None else "stale"))
    assert status in STATUSES
    return {"status": status, "why": why, "verified_orders": verified,
            "refused_orders": refused}


def _ep(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso(t):
    import datetime as _dt
    t = _ep(t)
    return None if t is None else _dt.datetime.fromtimestamp(
        t, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _exists(conn, table: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    table))


async def archer_counts(conn, *, now: float) -> dict:
    """Archer's RECOMMENDATIONS (never orders), all-time and last 24 h."""
    if not await _exists(conn, "eddie_execution_estimates"):
        return {"status": "UNAVAILABLE", "why": "MIGRATION_217_NOT_APPLIED",
                "recommendations": None}
    rows = await conn.fetch(
        "SELECT recommendation, count(*) AS n, "
        "       count(*) FILTER (WHERE estimated_at >= to_timestamp($1)) "
        "       AS n24, max(estimated_at) AS last_at "
        "  FROM eddie_execution_estimates GROUP BY recommendation", now - 86400)
    rec = {k: 0 for k in RECOMMENDATIONS}
    rec24 = {k: 0 for k in RECOMMENDATIONS}
    last = None
    for r in rows:
        rec[r["recommendation"]] = int(r["n"])
        rec24[r["recommendation"]] = int(r["n24"])
        t = _ep(r["last_at"])
        last = t if last is None or (t is not None and t > last) else last
    return {"status": "OK", "why": None, "recommendations": rec,
            "recommendations_24h": rec24,
            "estimates": sum(rec.values()), "last_estimated_at": _iso(last),
            "source": "eddie_execution_estimates (SHADOW ONLY: a "
                      "recommendation is not an order)"}


async def legacy_mirror(conn, *, now: float) -> dict:
    """The legacy mirror's record, labelled from its real control row."""
    out = {"title": LEGACY_TITLE, "is_target_small_live": False,
           "description": ("the old 1:1,000 execution mirror: live rows are "
                           "COPIES of paper orders (and the execution "
                           "intents' mirror lane), not BETTOR-originated "
                           "orders")}
    if not await _exists(conn, "execmirror_control"):
        out.update(state="UNAVAILABLE", label=legacy_label(None),
                   why="MIGRATION_192_NOT_APPLIED")
        return out
    ctl = await conn.fetchrow(
        "SELECT enabled, stopped, stop_done_at, scale, max_order_usd, "
        "       cutover_at, updated_at FROM execmirror_control WHERE id = 1")
    c = dict(ctl) if ctl else None
    o = await conn.fetchrow(
        "SELECT count(*) AS rows, "
        "       count(*) FILTER (WHERE state <> 'EXCLUDED') AS sent, "
        "       count(*) FILTER (WHERE state = 'EXCLUDED') AS excluded, "
        "       count(venue_order_id) AS venue_ids, "
        "       count(accepted_at) AS acked, "
        "       count(*) FILTER (WHERE execution_intent_id IS NOT NULL) "
        "       AS intent_lane, max(created_at) AS last_at "
        "  FROM execmirror_orders")
    f = await conn.fetchrow("SELECT count(*) AS n, max(observed_at) AS last_at"
                            "  FROM execmirror_fills")
    k = None
    if await _exists(conn, "kalshi_live_intents"):
        k = await conn.fetchrow(
            "SELECT count(*) AS rows, count(accepted_at) AS acked, "
            "       count(*) FILTER (WHERE state <> 'EXCLUDED') AS sent "
            "  FROM kalshi_live_intents")
    out.update(
        state=legacy_state(c), label=legacy_label(c),
        control={"enabled": bool(c and c.get("enabled")),
                 "stopped": bool(c and c.get("stopped")),
                 "stop_done_at": _iso((c or {}).get("stop_done_at")),
                 "scale": (float(c["scale"]) if c and c.get("scale")
                           is not None else None),
                 "cap_usd_per_order": (float(c["max_order_usd"]) if c and
                                       c.get("max_order_usd") is not None
                                       else None),
                 "cutover_at": _iso((c or {}).get("cutover_at")),
                 "control_changed_at": _iso((c or {}).get("updated_at"))},
        orders={"rows": int(o["rows"]), "sent_or_planned": int(o["sent"]),
                "excluded": int(o["excluded"]),
                "with_venue_order_id": int(o["venue_ids"]),
                "venue_acknowledged": int(o["acked"]),
                "from_execution_intents": int(o["intent_lane"]),
                "last_row_at": _iso(o["last_at"])},
        fills={"count": int(f["n"]), "last_at": _iso(f["last_at"])},
        kalshi_copy_lane=(None if k is None else {
            "rows": int(k["rows"]), "sent_or_planned": int(k["sent"]),
            "venue_acknowledged": int(k["acked"])}),
        source="execmirror_control + execmirror_orders + execmirror_fills + "
               "kalshi_live_intents")
    return out


async def intents_evidence(conn) -> dict | None:
    if not await _exists(conn, "execution_intents"):
        return None
    r = await conn.fetchrow(
        "SELECT count(*) AS n, count(*) FILTER (WHERE live_eligible) AS le, "
        "       count(*) FILTER (WHERE actual_state IN ('SUBMITTING', "
        "       'SUBMITTED', 'UNKNOWN')) AS submitted, "
        "       max(created_at) AS last_at FROM execution_intents")
    return {"intents": int(r["n"]), "live_eligible": int(r["le"]),
            "actual_lane_submitted": int(r["submitted"]),
            "last_at": _iso(r["last_at"]),
            "note": ("execution intents' actual lane is the LEGACY MIRROR "
                     "machinery (execmirror_control + execmirror_orders): no "
                     "allocation, no Archer execution decision -- never "
                     "BETTOR-originated")}


async def target_venues(conn) -> dict:
    """The TARGET lane's venue state, per venue, never combined."""
    pm = {"state": "NOT_CONNECTED",
          "why": ("no BETTOR-originated lane is bound to Polymarket US; the "
                  "only Polymarket US credential on record belongs to the "
                  "LEGACY MIRROR account")}
    k = {"state": "NOT_CONNECTED", "why": "no Kalshi lane is recorded"}
    if await _exists(conn, "kalshi_smalllive_control"):
        r = await conn.fetchrow(
            "SELECT (key_fingerprint IS NOT NULL) AS has_key, enabled "
            "  FROM kalshi_smalllive_control WHERE id = 1")
        n = (await conn.fetchval(
            "SELECT count(*) FROM kalshi_account_reconciliations")
             if await _exists(conn, "kalshi_account_reconciliations") else 0)
        k = {"state": "NOT_CONNECTED",
             "why": ("Kalshi control holds no key; %d account "
                     "reconciliation(s) recorded" % int(n or 0))
             if not (r and r["has_key"]) else
             ("a Kalshi key is recorded for the LEGACY copy lane only; no "
              "BETTOR-originated lane is bound to it")}
    return {"polymarket_us": pm, "kalshi": k}


def archer_funnel(counts: dict, *, verified_orders: list, legacy: dict | None
                 ) -> dict:
    """Archer's RECOMMENDATIONS apart from ORDERS. Pure."""
    rec = (counts or {}).get("recommendations")
    acked = [o for o in verified_orders if o.get("venue_ack_at")]
    return {
        "recommendations_are_not_orders": True,
        "status": (counts or {}).get("status", "UNAVAILABLE"),
        "why": (counts or {}).get("why"),
        "execute_now_recommendations": None if rec is None
        else rec.get("EXECUTE_NOW"),
        "skip_execution_recommendations": None if rec is None
        else rec.get("SKIP_EXECUTION"),
        "other_recommendations": None if rec is None else {
            k: rec.get(k) for k in ("REST_LIMIT", "SPLIT", "WAIT")},
        "recommendations_24h": (counts or {}).get("recommendations_24h"),
        "last_recommendation_at": (counts or {}).get("last_estimated_at"),
        "bettor_orders_submitted": len(verified_orders),
        "bettor_orders_venue_acknowledged": len(acked),
        "bettor_fills": sum(int(o.get("fills") or 0) for o in verified_orders),
        "orders_basis": (PATH_WHY if not BETTOR_ORIGINATED_PATH_CONFIGURED
                         else "BETTOR-originated venue orders"),
        "legacy_mirror_not_archer": (None if not legacy else {
            "label": legacy.get("label"),
            "orders_sent_or_planned": (legacy.get("orders") or {}).get(
                "sent_or_planned"),
            "venue_acknowledged": (legacy.get("orders") or {}).get(
                "venue_acknowledged"),
            "fills": (legacy.get("fills") or {}).get("count")}),
    }


async def read(conn, *, now: float | None = None) -> dict:
    """{small_live, legacy_mirror, archer_funnel}: plain SELECTs only."""
    now = float(time.time() if now is None else now)
    counts = await archer_counts(conn, now=now)
    legacy = await legacy_mirror(conn, now=now)
    venues = await target_venues(conn)
    intents = await intents_evidence(conn)
    # No BETTOR-originated order table exists in this build: the candidate
    # set is empty by construction (mirror rows are never candidates).
    orders: list = []
    ev = {"path_configured": BETTOR_ORIGINATED_PATH_CONFIGURED,
          "control": None, "orders": orders,
          "shadow": {"archer_estimates": counts.get("estimates") or 0},
          "heartbeat_at": None, "venues": venues}
    d = derive_status(ev, now=now)
    small = {
        "title": TITLE, "status": d["status"], "why": d["why"],
        "statuses": list(STATUSES), "version": VERSION,
        "chain_required": ("Derek decision -> allocation -> Archer execution "
                           "decision -> real venue order with venue ack"),
        "path_configured": BETTOR_ORIGINATED_PATH_CONFIGURED,
        "capital": {"usd": None, "why": "no capital is assigned to a "
                    "BETTOR-originated lane (none exists)"},
        "equity": {"usd": None, "why": "no BETTOR-originated venue account "
                   "exists"},
        "realized_pnl": {"usd": None, "why": "no BETTOR-originated fill "
                         "exists"},
        "unrealized_pnl": {"usd": None, "why": "no BETTOR-originated "
                           "position exists"},
        "orders": {"submitted": len(d["verified_orders"]),
                   "venue_acknowledged": len([
                       o for o in d["verified_orders"]
                       if o.get("venue_ack_at")]),
                   "basis": PATH_WHY},
        "fills": {"count": sum(int(o.get("fills") or 0)
                               for o in d["verified_orders"]),
                  "basis": "venue fills of BETTOR-originated orders only"},
        "heartbeat": {"at": None, "state": "NO_LANE_PROCESS",
                      "why": "no BETTOR-originated lane process exists"},
        "venues": venues,
        "shadow_chain": {"archer_estimates": counts.get("estimates"),
                         "last_archer_decision_at":
                             counts.get("last_estimated_at"),
                         "status": counts.get("status"),
                         "why": counts.get("why")},
        "mirror_orders_counted": 0,
        "mirror_orders_rule": ("RN1, mirror and copy orders never count "
                               "toward SMALL LIVE — BETTOR ORIGINATED"),
        "intents_evidence": intents,
        "source": ("small_live_truth.derive_status over: "
                   "eddie_execution_estimates, execmirror_control/orders, "
                   "execution_intents, kalshi_smalllive_control"),
        "as_of": _iso(now),
    }
    return {"small_live": small, "legacy_mirror": legacy,
            "archer_funnel": archer_funnel(counts,
                                         verified_orders=d["verified_orders"],
                                         legacy=legacy)}


async def read_isolated(conn, *, now: float | None = None) -> dict:
    """`read` inside its own (sub)transaction: inside a caller's open
    transaction this is a SAVEPOINT, so a failed read here can never abort
    the caller's other reads."""
    async with conn.transaction():
        return await read(conn, now=now)

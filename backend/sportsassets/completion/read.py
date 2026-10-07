"""THE COMPLETION READINESS READBACK (completion readiness, 2026-10-07).

One read-only pass over production evidence that answers the completion
package's readiness gate, section by section, every number with its source
and every unknown named (never a green it did not measure):

  runtime        shared workers: process start, RSS / high-water against its
                 own cgroup limit (workers_boot + workers_memory); the
                 dedicated market plane: running?, mode, streams, resources
  market_data    subscription mode, market-data stream count, refdata
                 coverage and pull receipt, priority freshness (the plane's
                 snapshot: numerator / denominator)
  management     held-position freshness (bettor_paper_freshness.read)
  gates          capital_readiness.feeds.gates -- SOFTWARE reds, Xavier
                 packets, canary, mirror, epoch reconciliation, the
                 profitability bind, forward economics, SMALL LIVE SHADOW
  probability    probability_authority over aggregate OOS evidence
  executable_ev  ev_authority over recent decisions (CASH unless the
                 clustered lower bound is positive)
  twin           the repaired IOC twin's agreement on FRESH orders
  revenue        Revenue Reliability V1: agent licences, CASH-incumbent
                 tournament, regimes, demonstrated capacity, daily readiness
  arbitrage      Adriana's latest scan: NO_ELIGIBLE_ARB unless a structure is
                 GUARANTEED_AFTER_COSTS (void terms not established -> none)
  venue_positions PMUS retail position confirmation: venue-confirmed, or the
                 exact owner credential it waits on
  readiness      readiness_gate.evaluate_readiness -> PAPER_SHADOW_ONLY until
                 every input is green; CAPITAL_CANDIDATE is never automatic and
                 never while any governor says CASH
  owner_blockers what only the owner can do, each with its exact action

It changes nothing: no authority, no limit, no credential, no order path.
"""
from __future__ import annotations

import json
import time

from . import evidence as EV
from . import readiness_gate as RG

VERSION = "COMPLETION_READINESS_READBACK_V1"
MODE = "READ_ONLY_NO_AUTHORITY"
ACCOUNT_ID = "paper_acct_main"
UMP_SERVICE = "universal_market_plane"
UMP_HEARTBEAT_MAX_AGE_S = 300.0
SNAPSHOT_MAX_AGE_S = 600.0
MIRROR_HEARTBEAT_MAX_AGE_S = 900.0
VENUE_CONFIRMED_SOURCE = "PMUS_VENUE_POSITIONS_WALK"
R_PMUS_NOT_ED25519 = "PMUS_SECRET_SLOT_HOLDS_NO_ED25519_KEY"
PROVEN_SETTLEMENT = ("SETTLEMENT_PROVEN_COMPATIBLE",
                     "SETTLEMENT_PROVEN_DIFFERENT_BUT_PRICED")
DEDICATED_SPEC = "ops/render_market_plane_service.yaml"


def _j(x):
    if isinstance(x, str):
        try:
            return json.loads(x)
        except ValueError:
            return None
    return x


def _epoch(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def _has(conn, table: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    table))


async def _heartbeat(conn, service: str):
    if not await _has(conn, "service_heartbeats"):
        return None
    r = await conn.fetchrow(
        "SELECT status, detail, beat_at FROM service_heartbeats "
        " WHERE service = $1", service)
    if r is None:
        return None
    return {"status": r["status"], "detail": _j(r["detail"]) or {},
            "beat_at": _epoch(r["beat_at"])}


async def _state(conn, key: str):
    if not await _has(conn, "ingestion_state"):
        return None
    return _j(await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key = $1", key))


# ── runtime ─────────────────────────────────────────────────────────────

def runtime_block(boot, mem, ump, *, now: float) -> dict:
    """Shared workers + the dedicated plane, from their own records."""
    boot = boot or {}
    from datetime import datetime
    started = None
    try:
        started = datetime.fromisoformat(str(boot.get("at"))).timestamp() \
            if boot.get("at") else None
    except ValueError:
        started = None
    md = (mem or {}).get("detail") or {}
    limit = boot.get("memory_limit_mb")
    peak, rss = md.get("peak_mb"), md.get("rss_mb")
    hw = (round(float(peak) / float(limit), 4)
          if peak and limit else None)
    ump_d = (ump or {}).get("detail") or {}
    ump_age = (now - ump["beat_at"]) if ump and ump.get("beat_at") else None
    running = ump_age is not None and ump_age <= UMP_HEARTBEAT_MAX_AGE_S
    label = ump_d.get("runtime")
    if not running:
        ump_state = ("NOT_RUNNING" if ump is None else
                     "HEARTBEAT_STALE_%ds" % int(ump_age or 0))
    elif label == "DEDICATED_READ_ONLY":
        ump_state = "DEDICATED_RUNNING"
    else:
        ump_state = "RUNNING_OUTSIDE_THE_DEDICATED_SERVICE:%s" % label
    return {
        "shared_workers": {
            "commit_sha": boot.get("commit_sha"),
            "process_started_at": started,
            "minutes_since_process_start": (round((now - started) / 60.0, 1)
                                            if started else None),
            "no_oom_basis": ("minutes since the workers process last started "
                             "(any restart -- deploy or OOM -- resets it); "
                             "Render's own OOM events are the external proof"),
            "rss_mb": rss, "peak_mb": peak, "memory_limit_mb": limit,
            "rss_highwater_fraction": hw,
            "memory_beat_at": (mem or {}).get("beat_at"),
            "dedicated_only": boot.get("dedicated_only"),
            "universal_market_plane_started_here": "universal_market_plane"
            in (boot.get("started") or [])},
        "market_plane": {
            "state": ump_state,
            "heartbeat_age_s": None if ump_age is None else round(ump_age, 1),
            "runtime_label": label,
            "subscription_mode": ump_d.get("subscription_mode"),
            "market_data_streams": ump_d.get("market_data_streams"),
            "resources": ump_d.get("resources"),
            "status": (ump or {}).get("status")}}


# ── market data ─────────────────────────────────────────────────────────

async def latest_snapshot(conn, *, now: float):
    if not await _has(conn, "market_plane_events"):
        return None, "MARKET_PLANE_EVENTS_ABSENT"
    r = await conn.fetchrow(
        "SELECT payload, at FROM market_plane_events WHERE kind = 'SNAPSHOT' "
        " ORDER BY at DESC LIMIT 1")
    if r is None:
        return None, "NO_MARKET_PLANE_SNAPSHOT"
    age = now - _epoch(r["at"])
    if age > SNAPSHOT_MAX_AGE_S:
        return None, "MARKET_PLANE_SNAPSHOT_STALE_%ds" % int(age)
    return _j(r["payload"]) or {}, None


def market_data_block(snap, why, ump_detail) -> dict:
    snap = snap or {}
    sub = snap.get("subscription") or {}
    pri = (snap.get("freshness") or {}).get("priority_universe") or {}
    reg = (snap.get("universe") or {}).get("registry") or {}
    num = None
    if pri:
        num = int(pri.get("current_pmx_stream") or 0) + int(
            pri.get("current_rest_fallback") or 0)
    den = None
    if pri:
        den = int(pri.get("denominator") or 0) - int(
            pri.get("external_unavailable") or 0)
    active = int(reg.get("active") or 0)
    return {
        "snapshot": "CURRENT" if snap else why,
        "subscription_mode": sub.get("subscription_mode") or (
            ump_detail or {}).get("subscription_mode"),
        "market_data_streams": sub.get("market_data_streams"),
        "market_data_streams_expected": sub.get(
            "market_data_streams_expected"),
        "firm_stream_budget": sub.get("firm_stream_budget"),
        "subscribed": sub.get("subscribed"), "fresh": sub.get("fresh"),
        "priority_freshness": {
            "numerator": num, "denominator": den,
            "rate": (round(num / den, 4) if num is not None and den else None),
            "target": 0.95,
            "members": pri.get("members")},
        "refdata": {
            "active_contracts": active or None,
            "pmx_listed": reg.get("pmx_listed"),
            "pmx_unlisted": reg.get("pmx_unlisted"),
            "refdata_pending": reg.get("refdata_pending"),
            "coverage_rate": (round((int(reg.get("pmx_listed") or 0) + int(
                reg.get("pmx_unlisted") or 0)) / active, 4)
                if active else None),
            "universe_pull": snap.get("refdata_universe")},
        "token": sub.get("token")}


# ── venue positions / arbitrage / settlement ───────────────────────────

def venue_positions_block(hb, *, now: float) -> dict:
    if hb is None:
        return {"status": "UNREADABLE", "why": "NO_MIRROR_SHADOW_HEARTBEAT",
                "venue_confirmed": False}
    age = now - (hb.get("beat_at") or 0)
    ps = (hb.get("detail") or {}).get("positions_source") or {}
    src = ps.get("source")
    confirmed = src == VENUE_CONFIRMED_SOURCE and age <= \
        MIRROR_HEARTBEAT_MAX_AGE_S
    out = {"venue_confirmed": confirmed, "source": src,
           "authority": ps.get("authority"),
           "primary_refusal": ps.get("primary_refusal"),
           "heartbeat_age_s": round(age, 1)}
    if confirmed:
        return dict(out, status="VENUE_CONFIRMED")
    if ps.get("primary_refusal") == R_PMUS_NOT_ED25519 or (
            src and src != VENUE_CONFIRMED_SOURCE):
        return dict(out, status="OWNER_CREDENTIAL_REQUIRED",
                    credential_type=("POLYMARKET_US_RETAIL_API_KEY "
                                     "(Ed25519, polymarket.us/developer)"),
                    owner_action=("enter a Polymarket US retail Ed25519 API "
                                  "key in the PMUS key-id / secret slots of "
                                  "sportsassets-workers; the slot now holds "
                                  "no Ed25519 key, so positions are "
                                  "LEDGER_DERIVED, never venue-confirmed"),
                    workaround="NONE (auth is never worked around)")
    return dict(out, status="NOT_CONFIRMED",
                why="HEARTBEAT_STALE" if age > MIRROR_HEARTBEAT_MAX_AGE_S
                else "NO_VENUE_POSITIONS_SOURCE")


async def arbitrage_block(conn) -> dict:
    if not await _has(conn, "adriana_arb_scans"):
        return {"verdict": "NO_ELIGIBLE_ARB", "why": "ADRIANA_TABLES_ABSENT",
                "authority": "SHADOW_ONLY"}
    r = await conn.fetchrow(
        "SELECT scan_id, started_at, finished_at, status, markets_read, "
        "       books_fresh, structures_considered, opportunities, "
        "       refusals_total FROM adriana_arb_scans "
        " ORDER BY started_at DESC LIMIT 1")
    scan = dict(r) if r else {}
    n_ok = 0
    if await _has(conn, "adriana_arb_opportunities"):
        n_ok = int(await conn.fetchval(
            "SELECT count(*) FROM adriana_arb_opportunities "
            " WHERE scan_id = $1 AND verdict = 'GUARANTEED_AFTER_COSTS'",
            scan.get("scan_id")) or 0)
    refusals = {}
    if await _has(conn, "adriana_arb_refusals"):
        for x in await conn.fetch(
                "SELECT primary_code, count(*) n FROM adriana_arb_refusals "
                " WHERE scan_id = $1 GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
                scan.get("scan_id")):
            refusals[str(x["primary_code"])] = int(x["n"])
    return {"verdict": ("GUARANTEED_AFTER_COSTS_FOUND_SHADOW_ONLY" if n_ok
                        else "NO_ELIGIBLE_ARB"),
            "guaranteed_after_costs": n_ok,
            "refusals_by_reason": refusals,
            "fail_closed": "void terms not established -> every structure "
                           "refused (agents/adriana.ESTABLISHED_VOID_TERMS)",
            "latest_scan": {k: (str(v) if not isinstance(v, (int, float,
                                                              type(None)))
                                else v) for k, v in scan.items()
                            },
            "authority": "SHADOW_ONLY"}


async def settlement_block(conn, account_id: str) -> dict:
    from .. import bettor_paper_ledger as L
    pos = await L.positions(conn, account_id)
    slugs = sorted({p.get("us_market_slug") or p.get("slug") for p in pos
                    if p.get("us_market_slug") or p.get("slug")})
    states = {}
    if slugs and await _has(conn, "market_plane_registry"):
        for r in await conn.fetch(
                "SELECT contract_id, settlement_state FROM "
                " market_plane_registry WHERE contract_id = ANY($1::text[])",
                slugs):
            states[r["contract_id"]] = r["settlement_state"]
    by: dict = {}
    for s in slugs:
        k = states.get(s) or "NOT_IN_REGISTRY"
        by[k] = by.get(k, 0) + 1
    proven = sum(v for k, v in by.items() if k in PROVEN_SETTLEMENT)
    return {"open_position_markets": len(slugs), "by_state": by,
            "proven": proven,
            "settlement_proven": bool(slugs) and proven == len(slugs)}


# ── the gate ────────────────────────────────────────────────────────────

def readiness_block(*, runtime, market_data, management, gates, probability,
                    ev, twin, revenue, settlement, venue) -> dict:
    sw = runtime["shared_workers"]
    rv = (revenue or {}).get("data") or {}
    strategies = rv.get("strategies") or {}
    capacity = sum(float((v or {}).get("executable_capacity_usd") or 0)
                   for v in strategies.values()) if isinstance(
                       strategies, dict) else 0.0
    sw_gate = (gates.get("software_reds_zero") or {})
    sw_reds = ((sw_gate.get("evidence") or {}).get("software")
               if isinstance(sw_gate.get("evidence"), dict)
               else sw_gate.get("software"))
    xav = gates.get("xavier_complete") or {}
    canary = gates.get("production_canary_clean") or {}
    small = gates.get("small_live_shadow") or {}
    mgmt_rate = management.get("fresh_rate")
    xav_rate = 1.0 if xav.get("value") is True else (
        0.0 if xav else None)
    e = RG.ReadinessEvidence(
        no_oom_minutes=float(sw.get("minutes_since_process_start") or 0.0),
        # unmeasured headroom fails closed (1.0 >= 0.85)
        worker_rss_highwater_fraction=float(
            sw.get("rss_highwater_fraction") or 1.0),
        priority_freshness_rate=market_data["priority_freshness"]["rate"],
        management_freshness_rate=mgmt_rate,
        software_red_count=(int(sw_reds) if isinstance(sw_reds, (int, float))
                            else 1),
        xavier_packet_complete_rate=xav_rate,
        canary_pass=canary.get("value") is True,
        probability_edge_lb=probability.get("improvement_lower_bound")
        if probability.get("authority") == "BETTOR_RESIDUAL_ALLOWED" else None,
        forward_independent_events=int(probability.get(
            "independent_events") or 0),
        digital_twin_fill_agreement=(twin.get("fill_agreement_rate")
                                     if twin.get("certified") else None),
        settlement_proven=bool(settlement.get("settlement_proven")),
        capacity_proven=capacity > 0,
        mirror_venue_confirmed=bool(venue.get("venue_confirmed")),
        small_live_shadow=small.get("value") is True,
        historical_paper_immutable=True)
    d = RG.evaluate_readiness(e)
    blockers = list(d.blockers)
    governors = {
        "executable_ev": ev.get("verdict"),
        "daily_revenue_readiness": (rv.get("daily_revenue_readiness") or {})
        .get("overall_status"),
        "strategy_tournament": (rv.get("strategy_tournament") or {}).get(
            "selected")}
    if "CASH" in governors.values():
        blockers.append("GOVERNOR_SAYS_CASH")
    status = "CAPITAL_CANDIDATE" if not blockers else "PAPER_SHADOW_ONLY"
    return {"status": status, "blockers": blockers, "evidence": d.evidence,
            "governors": governors,
            "auto_activation": False,
            "capital_authority_granted": False,
            "historical_paper_immutable_basis": (
                "this readback writes nothing; PAPER history is append-only "
                "by the ledger's own guards")}


def owner_blockers(runtime, venue, refdata_universe) -> list:
    out = []
    if runtime["market_plane"]["state"] != "DEDICATED_RUNNING":
        out.append({
            "code": "OWNER_ACTION_REQUIRED",
            "item": "DEDICATED_MARKET_PLANE_SERVICE",
            "state": runtime["market_plane"]["state"],
            "action": ("create the Render background worker "
                       "`sportsassets-market-plane` from %s (Docker, "
                       "./backend/Dockerfile, command `python -m "
                       "sportsassets.workers.universal_market_plane`, plan "
                       "standard) and enter exactly PMX_CLIENT_ID, PMX_KEY_ID "
                       "and PMX_PRIVATE_KEY_B64 (the values already on "
                       "sportsassets-workers) in its dashboard; DATABASE_URL "
                       "from sportsassets-db; and, for the Kalshi WebSocket "
                       "book runtime beside it, KALSHI_API_KEY_ID + "
                       "KALSHI_PRIVATE_KEY_PEM (a Kalshi API key, read-only "
                       "use -- Kalshi rep 2026-10-07). Nothing else: no PMUS "
                       "key, no LIVE_TRADING_ENABLED, no Kalshi trading "
                       "switch, no admin token" % DEDICATED_SPEC),
            "why_owner": ("render-ops has no service-create action and the "
                          "PMX secret values are entered by the owner, never "
                          "copied by automation")})
    if venue.get("status") == "OWNER_CREDENTIAL_REQUIRED":
        out.append({"code": "OWNER_CREDENTIAL_REQUIRED",
                    "item": "PMUS_RETAIL_POSITION_CONFIRMATION",
                    "credential_type": venue.get("credential_type"),
                    "action": venue.get("owner_action")})
    sc = (refdata_universe or {}).get("state_change_stream") or {}
    out.append({"code": "OWNER_ACTION_REQUIRED",
                "item": "INSTRUMENT_STATE_CHANGE_SUBSCRIPTION_SCHEMA",
                "state": sc.get("status", "VENUE_SCHEMA_NOT_PUBLISHED"),
                "action": sc.get("owner_action", (
                    "obtain the proto for CreateInstrumentStateChangeSubscription "
                    "from the Polymarket representative")),
                "covered_meanwhile_by": sc.get("covered_by")})
    return out


async def read(conn, *, account_id: str = ACCOUNT_ID,
               now: float | None = None) -> dict:
    """The whole readback, inside the caller's READ ONLY transaction."""
    from ..capital_readiness import feeds as F
    from ..revenue_reliability import read as RR
    from .. import bettor_paper_freshness as FR
    now = float(now if now is not None else time.time())
    boot = await _state(conn, "workers_boot")
    mem = await _heartbeat(conn, "workers_memory")
    ump = await _heartbeat(conn, UMP_SERVICE)
    runtime = runtime_block(boot, mem, ump, now=now)
    snap, why = await latest_snapshot(conn, now=now)
    market = market_data_block(snap, why, (ump or {}).get("detail"))
    fr = await FR.read(conn, account_id, now=now, rows_limit=0)
    management = {"status": fr.get("status"), "fresh_rate": fr.get(
        "fresh_rate"), "markable": fr.get("markable"),
        "open_positions": fr.get("open_positions"),
        "fresh": ((fr.get("counts") or {}).get("FRESH") or {}).get("count"),
        "quiet_valid": ((fr.get("counts") or {}).get("QUIET_VALID") or {})
        .get("count"), "target": fr.get("target_fresh_rate")}
    gates = await F.gates(conn, account_id=account_id, now=now,
                          source_sha=F.source_sha_from_env())
    probability = await EV.read_probability(conn)
    ev = await EV.read_ev(conn, authority=probability["authority"])
    twin = await EV.read_twin(conn)
    revenue = await RR.read(conn, account_id=account_id, now=now)
    venue = venue_positions_block(await _heartbeat(conn, "mirror_shadow"),
                                  now=now)
    arb = await arbitrage_block(conn)
    settle = await settlement_block(conn, account_id)
    ready = readiness_block(runtime=runtime, market_data=market,
                            management=management, gates=gates,
                            probability=probability, ev=ev, twin=twin,
                            revenue=revenue, settlement=settle, venue=venue)
    rv = (revenue or {}).get("data") or {}
    return {
        "version": VERSION, "mode": MODE, "as_of": now,
        "account_id": account_id,
        "small_live": "SHADOW",
        "authority_changed": False,
        "readiness": ready,
        "runtime": runtime,
        "market_data": market,
        "management_freshness": management,
        "gates": {k: {"value": v.get("value"), "reason": v.get("reason")}
                  for k, v in gates.items()},
        "gate_evidence": gates,
        "probability": probability,
        "executable_ev": ev,
        "digital_twin": twin,
        "agents": rv.get("agent_scoreboard"),
        "strategy_tournament": rv.get("strategy_tournament"),
        "regime_matrix": rv.get("regime_matrix"),
        "daily_revenue_readiness": rv.get("daily_revenue_readiness"),
        "revenue_status": (revenue or {}).get("status"),
        "arbitrage": arb,
        "venue_positions": venue,
        "settlement": settle,
        "owner_blockers": owner_blockers(runtime, venue,
                                         market.get("refdata", {}).get(
                                             "universe_pull")),
    }

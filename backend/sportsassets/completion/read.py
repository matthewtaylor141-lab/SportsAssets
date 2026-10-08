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
#: PMX gRPC PRIMARY IN THE DECIDING PROCESS. The held-mark refresh (API
#: process) records the live institutional stream digest on every run
#: (paper_mark_refresh_runs.market_data.streams.institutional); a run older
#: than the mark SLA (bettor_paper_freshness.SLA_S, 300 s) is not evidence.
PMX_RUN_MAX_AGE_S = 300.0
PMX_GRPC = "PMX_GRPC"
PMX_REST = "REST"
#: held-mark feeds (bettor_paper_freshness.MARK_FEEDS) that are REST fallback
REST_FEEDS = ("RETAIL_STREAM", "HARVEST", "REST", "PUBLIC_GATEWAY")


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
    snap = _j(r["payload"]) or {}
    if isinstance(snap, dict) and snap.get("computed_at") is None:
        snap = dict(snap, computed_at=_epoch(r["at"]))
    return snap, None


#: ── A STALE, ABSENT OR RESTARTED PLANE CERTIFIES NOTHING (RC5) ───────────
#:
#: PRODUCTION (2026-10-08, release 7fd4574e): the dedicated plane was OOM-
#: killed at its 2 GiB limit at 06:10:01Z and again at 06:12:10Z
#: (events_sportsassets-market-plane), and is cycling every ~20-60 min since
#: 05:16Z. Its books live only in its own process memory, so each restart
#: discards every one; yet the readback called the plane's last SNAPSHOT
#: "CURRENT" for up to SNAPSHOT_MAX_AGE_S (600 s) whatever happened after it
#: -- and the acceptance harness reads PMX primary from exactly that
#: (pm_bind.acceptance: snapshot CURRENT -> pmx_primary_source PMX_GRPC).
#: A snapshot of a process that has stopped beating, or of a previous
#: incarnation, is a record of books that no longer exist.
#:
#: SO a snapshot is CURRENT only when ALSO (thresholds unchanged, existing
#: ones only):
#:   * the plane's own liveness heartbeat (`universal_market_plane`, written
#:     every pass) is at most UMP_HEARTBEAT_MAX_AGE_S old -- else
#:     MARKET_PLANE_HEARTBEAT_ABSENT / _STALE_<s>s;
#:   * it was computed by the RUNNING incarnation: not before that
#:     incarnation's start (the plane's boot record, `market_plane`
#:     heartbeat detail.started_at) -- else
#:     MARKET_PLANE_SNAPSHOT_FROM_A_PREVIOUS_RUNTIME (the restart gap: the
#:     new process has not yet published its own).
#: Not CURRENT -> the readback withholds the plane's figures with the reason,
#: and the harness reads PMX primary as REST. Nothing here moves a number.
PLANE_BOOT_SERVICE = "market_plane"
R_PLANE_HEARTBEAT_ABSENT = "MARKET_PLANE_HEARTBEAT_ABSENT"
R_PLANE_PREVIOUS_RUNTIME = "MARKET_PLANE_SNAPSHOT_FROM_A_PREVIOUS_RUNTIME"


def plane_snapshot_refusal(snap, ump, boot, *, now: float) -> str | None:
    """PURE. None when the plane snapshot may stand for now; otherwise the
    named reason it may not (see above). `ump` is the plane's liveness
    heartbeat, `boot` its boot record ({detail.started_at}), each
    {status, detail, beat_at} or None."""
    if ump is None or ump.get("beat_at") is None:
        return R_PLANE_HEARTBEAT_ABSENT
    age = float(now) - float(ump["beat_at"])
    if age > UMP_HEARTBEAT_MAX_AGE_S:
        return "MARKET_PLANE_HEARTBEAT_STALE_%ds" % int(age)
    started = _epoch(((boot or {}).get("detail") or {}).get("started_at"))
    computed = _epoch((snap or {}).get("computed_at"))
    if started is not None and computed is not None and computed < started:
        return R_PLANE_PREVIOUS_RUNTIME
    return None


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


async def latest_mark_refresh(conn, account_id: str):
    """The newest FINISHED held-mark refresh run with its per-source counts
    and the market-data telemetry it recorded (migration 306), or None."""
    if not await _has(conn, "paper_mark_refresh_runs"):
        return None
    n = await conn.fetchval(
        "SELECT count(*) FROM information_schema.columns WHERE table_name = "
        "'paper_mark_refresh_runs' AND column_name = ANY($1::text[])",
        ["market_data", "sources", "institutional_books"])
    if int(n or 0) < 3:
        return None
    r = await conn.fetchrow(
        "SELECT run_id, finished_at, held_markets, institutional_books, "
        "       sources, market_data FROM paper_mark_refresh_runs "
        " WHERE account_id = $1 AND finished_at IS NOT NULL "
        " ORDER BY started_at DESC, run_id DESC LIMIT 1", account_id)
    if r is None:
        return None
    return {"run_id": r["run_id"], "finished_at": _epoch(r["finished_at"]),
            "held_markets": r["held_markets"],
            "institutional_books": r["institutional_books"],
            "sources": _j(r["sources"]) or {},
            "market_data": _j(r["market_data"]) or {}}


def consumer_reads_block(book_sources) -> dict:
    """PURE. The deciding process's consumer book reads by source, from the
    held-mark run's persisted owner telemetry (`book_sources`); UNREAD when
    the run carried none (a build before RC5, or no run)."""
    bs = book_sources if isinstance(book_sources, dict) else {}
    if not bs.get("totals"):
        return {"status": "UNREAD", "why": "NO_BOOK_SOURCE_TELEMETRY_ON_RUN"}
    reasons: dict = {}
    by = {}
    for name, c in (bs.get("by_consumer") or {}).items():
        by[name] = {"PMX_GRPC": int((c or {}).get("PMX_GRPC") or 0),
                    "REST": int((c or {}).get("REST") or 0)}
        for k, v in ((c or {}).get("fallback_reasons") or {}).items():
            reasons[k] = reasons.get(k, 0) + int(v or 0)
    top = dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:10])
    return {"status": "MEASURED", "scope": bs.get("scope"),
            "totals": dict(bs.get("totals")), "by_consumer": by,
            "pmx_share": bs.get("pmx_share"),
            "fallback_reasons_top": top,
            "enabled": bs.get("enabled"), "rule": bs.get("rule")}


def pmx_primary_block(run, feeds, *, markable, now: float) -> dict:
    """PURE. PMX gRPC PRIMARY, SOURCE BY SOURCE, from the deciding process's
    own record (never the dedicated plane's, never assumed):

      stream     requested symbols, venue acks on the current connection,
                 resident L2 books current (decision / held-mark bound), their
                 ages -- the API stream digest the newest held-mark run saved
      held       every markable held position's newest mark by source; the
                 PMX share is FRESH + QUIET_VALID marks whose observation came
                 from the institutional stream, over the SAME markable
                 denominator as held freshness (bettor_paper_freshness)
      fallback   REST-family marks, and why the primary was refused per
                 refusal (identity / same-book / not current / SLA)

    `source` is PMX_GRPC only when the run is inside PMX_RUN_MAX_AGE_S, the
    stream is CONNECTED with >= 1 current resident book, >= 1 held mark
    came from it, AND the stream is the majority source of the freshly
    manageable held marks (more than the REST family); otherwise REST with
    every reason named."""
    run = run or {}
    md = run.get("market_data") or {}
    inst = ((md.get("streams") or {}).get("institutional")) or {}
    f = feeds or {}
    held_by = dict(f.get("held_marks_by_source") or {})
    fresh_by = dict(f.get("fresh_marks_by_source") or {})
    stream_fresh = int(fresh_by.get("INSTITUTIONAL_STREAM") or 0)
    rest_fresh = sum(int(fresh_by.get(k) or 0) for k in REST_FEEDS)
    age = (None if run.get("finished_at") is None
           else round(float(now) - float(run["finished_at"]), 1))
    current = int(inst.get("held_mark_current_books")
                  or inst.get("current_books") or 0)
    why = []
    if not run:
        why.append("NO_HELD_MARK_REFRESH_RUN_RECORDED")
    elif age is None or age > PMX_RUN_MAX_AGE_S:
        why.append("HELD_MARK_RUN_OLDER_THAN_%dS" % int(PMX_RUN_MAX_AGE_S))
    if inst.get("state") != "CONNECTED":
        why.append("STREAM_NOT_CONNECTED:%s" % inst.get("state"))
    if current <= 0:
        why.append("NO_CURRENT_RESIDENT_L2_BOOK")
    if stream_fresh <= 0:
        why.append("NO_HELD_MARK_FROM_THE_STREAM")
    elif stream_fresh <= rest_fresh:
        # PRIMARY MEANS THE MAIN SOURCE. One stream mark beside ten REST
        # marks is REST with a stream attached (red-team closure #5: PMX
        # must not silently fall back to REST under a PMX label).
        why.append("STREAM_NOT_THE_MAJORITY_HELD_MARK_SOURCE")
    den = int(markable or 0)
    return {
        "source": PMX_REST if why else PMX_GRPC, "why": why,
        "process": "sportsassets-api (deciding process)",
        "evidence": "paper_mark_refresh_runs.market_data.streams."
                    "institutional + bettor_paper_freshness feeds",
        "run_id": run.get("run_id"), "run_age_s": age,
        "subscription_mode": inst.get("subscription_mode"),
        "target": inst.get("target"), "state": inst.get("state"),
        "connected": inst.get("connected"),
        "requested_symbols": inst.get("symbols"),
        "acked_symbols": inst.get("acked"),
        "refused_symbols": inst.get("refused_symbols"),
        "current_l2_books": inst.get("current_books"),
        "held_mark_current_l2_books": inst.get("held_mark_current_books"),
        "book_age_s": inst.get("book_age_s"),
        "venue_receipt_lag_s": inst.get("venue_receipt_lag_s"),
        "dropped_at_cap": inst.get("dropped_at_cap"),
        "held_subscribed": (inst.get("api_stream") or {}).get(
            "held_subscribed"),
        "held_wanted": (inst.get("api_stream") or {}).get("held_wanted"),
        "held_markable": den,
        "held_fresh_from_stream": stream_fresh,
        "held_fresh_from_rest_fallback": rest_fresh,
        "held_stream_rate": (round(stream_fresh / den, 4) if den else None),
        "held_marks_by_source": held_by,
        "fresh_marks_by_source": fresh_by,
        "oldest_held_mark_age_s": f.get("oldest_held_mark_age_s"),
        "institutional_books_last_run": run.get("institutional_books"),
        "fallback_reasons": md.get("institutional_refusals"),
        # CONSUMER USE, BY SOURCE (paper_pmx_books, RC5): every paper
        # owner and collector book read of the deciding process, PMX_GRPC
        # or REST, and every reason a PMX book did not serve -- the same
        # run's persisted telemetry. Evidence beside `source`, never an
        # input to it.
        "consumer_reads": consumer_reads_block(md.get("book_sources")),
        "accounting_rule": ("held freshness = (FRESH + QUIET_VALID) / "
                            "markable from ANY source; PMX share = those "
                            "whose newest observation is the institutional "
                            "stream; REST counted apart, never merged")}


# ── venue positions / arbitrage / settlement ───────────────────────────

def _pmus_slot_where(ps: dict, api_slot_shape: str | None) -> list:
    """WHERE the funded key must go, each service with the evidence that
    names it -- never a claim no readback carries."""
    if ps.get("pmus_slot_is_pmx_rsa_client"):
        w = ("the PMX RSA client" if ps.get("pmx_client_id_equals_pmus_key_id")
             else "an RSA PEM key")
    else:
        w = "no Ed25519 key"
    where = ["sportsassets-workers (mirror_shadow heartbeat: the slot holds "
             "%s)" % w]
    if api_slot_shape is None:
        where.append("sportsassets-api (its slot is not read here: red-team "
                     "CREDENTIAL_CLASSES by_process.api.PMUS_SLOTS)")
    elif api_slot_shape != "PMUS_RETAIL_ED25519_API_KEY_SHAPE":
        where.append("sportsassets-api (this process: %s)" % api_slot_shape)
    return where


def api_funded_slot_shape(env=None) -> str | None:
    """THIS (API) process's funded slot SHAPE enum, never a value."""
    import os
    from .. import market_data_identity as MDI
    env = os.environ if env is None else env
    try:
        return MDI.slot_shape(env.get("PMUS_KEY_ID", ""),
                              env.get("PMUS_SECRET_KEY", ""))
    except Exception:                                           # noqa: BLE001
        return None


def venue_positions_block(hb, *, now: float,
                          api_slot_shape: str | None = None) -> dict:
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
                    owner_action=("enter the FUNDED account's Polymarket US "
                                  "retail Ed25519 API key in PMUS_KEY_ID / "
                                  "PMUS_SECRET_KEY of %s; until then "
                                  "positions are LEDGER_DERIVED, never "
                                  "venue-confirmed" % " AND ".join(
                                      _pmus_slot_where(ps, api_slot_shape))),
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


#: each section of the readback gets its own savepoint and time budget, so
#: one slow or failing read is reported as UNAVAILABLE evidence (which fails
#: the readiness closed) instead of failing the whole readback
SECTION_TIMEOUT_MS = 30000
#: the whole readback's budget: a section that would start after it is
#: reported SKIPPED (never silently dropped)
TOTAL_BUDGET_S = 80.0


class _Sections:
    """Each section in its own savepoint, with min(section, remaining
    total) as its statement timeout, timed; a failure returns the section's
    default and is recorded (never raised)."""

    def __init__(self, conn):
        self.conn, self.t0, self.timings = conn, time.monotonic(), {}

    async def run(self, name: str, fn, default):
        start = time.monotonic()
        left_ms = int((TOTAL_BUDGET_S - (start - self.t0)) * 1000.0)
        if left_ms <= 1000:
            self.timings[name] = {"ms": 0.0, "ok": False, "why":
                                  "SKIPPED_TOTAL_BUDGET_%ds" % TOTAL_BUDGET_S}
            return default
        try:
            async with self.conn.transaction():
                await self.conn.execute("SET LOCAL statement_timeout = %d"
                                        % min(SECTION_TIMEOUT_MS, left_ms))
                out = await fn()
            self.timings[name] = {"ms": round((time.monotonic() - start)
                                              * 1000.0, 1), "ok": True}
            return out
        except Exception as exc:                                # noqa: BLE001
            self.timings[name] = {
                "ms": round((time.monotonic() - start) * 1000.0, 1),
                "ok": False, "why": "%s: %s" % (type(exc).__name__,
                                                str(exc)[:160])}
            return default


async def read(conn, *, account_id: str = ACCOUNT_ID,
               now: float | None = None) -> dict:
    """The whole readback, inside the caller's READ ONLY transaction; every
    section in its own savepoint with its own budget and timing."""
    from ..capital_readiness import feeds as F
    from ..revenue_reliability import read as RR
    from .. import bettor_paper_freshness as FR
    now = float(now if now is not None else time.time())
    sec = _Sections(conn)
    down_ = {"status": "SECTION_UNAVAILABLE"}

    async def _runtime():
        return (await _state(conn, "workers_boot"),
                await _heartbeat(conn, "workers_memory"),
                await _heartbeat(conn, UMP_SERVICE))
    boot, mem, ump = await sec.run("runtime", _runtime, (None, None, None))
    runtime = runtime_block(boot, mem, ump, now=now)

    async def _snap():
        s, w = await latest_snapshot(conn, now=now)
        if s is not None:
            # A STALE, ABSENT OR RESTARTED PLANE CERTIFIES NOTHING
            w = plane_snapshot_refusal(
                s, ump, await _heartbeat(conn, PLANE_BOOT_SERVICE), now=now)
            if w is not None:
                s = None
        return s, w
    snap, why = await sec.run("market_data", _snap,
                              (None, "SECTION_UNAVAILABLE"))
    market = market_data_block(snap, why, (ump or {}).get("detail"))

    async def _fr():
        return await FR.read(conn, account_id, now=now, rows_limit=0)
    fr = await sec.run("management_freshness", _fr, {}) or {}
    management = {"status": fr.get("status"), "fresh_rate": fr.get(
        "fresh_rate"), "markable": fr.get("markable"),
        "open_positions": fr.get("open_positions"),
        "fresh": ((fr.get("counts") or {}).get("FRESH") or {}).get("count"),
        "quiet_valid": ((fr.get("counts") or {}).get("QUIET_VALID") or {})
        .get("count"), "target": fr.get("target_fresh_rate"),
        # SOURCE-SPECIFIC: the newest mark of every held position by feed
        "marks_by_source": (fr.get("feeds") or {}).get(
            "held_marks_by_source"),
        "fresh_by_source": (fr.get("feeds") or {}).get(
            "fresh_marks_by_source"),
        "oldest_held_mark_age_s": (fr.get("feeds") or {}).get(
            "oldest_held_mark_age_s")}

    async def _pmx():
        return await latest_mark_refresh(conn, account_id)
    pmx_run = await sec.run("pmx_primary", _pmx, None)
    market["pmx_primary"] = pmx_primary_block(
        pmx_run, fr.get("feeds"), markable=fr.get("markable"), now=now)

    async def _gates():
        return await F.gates(conn, account_id=account_id, now=now,
                             source_sha=F.source_sha_from_env())
    gates = await sec.run("gates", _gates, {}) or {}

    async def _prob():
        return await EV.read_probability(conn)
    probability = await sec.run("probability", _prob,
                                dict(down_, authority=None))

    async def _ev():
        return await EV.read_ev(conn, authority=probability.get("authority"))
    ev = await sec.run("executable_ev", _ev, dict(down_))

    async def _twin():
        return await EV.read_twin(conn)
    twin = await sec.run("digital_twin", _twin, dict(down_))

    async def _rev():
        return await RR.read(conn, account_id=account_id, now=now)
    revenue = await sec.run("revenue", _rev, dict(down_))

    async def _venue():
        return venue_positions_block(await _heartbeat(conn, "mirror_shadow"),
                                     now=now,
                                     api_slot_shape=api_funded_slot_shape())
    venue = await sec.run("venue_positions", _venue, dict(down_))

    async def _arb():
        return await arbitrage_block(conn)
    arb = await sec.run("arbitrage", _arb, dict(down_))

    async def _settle():
        return await settlement_block(conn, account_id)
    settle = await sec.run("settlement", _settle, dict(down_))
    ready = readiness_block(runtime=runtime, market_data=market,
                            management=management, gates=gates,
                            probability=probability, ev=ev, twin=twin,
                            revenue=revenue, settlement=settle, venue=venue)
    t = sec.timings
    down = sorted(k for k, v in t.items() if not v["ok"])
    if down:
        ready["blockers"] = list(ready["blockers"]) + [
            "SECTION_UNAVAILABLE:%s" % k for k in down]
        ready["status"] = "PAPER_SHADOW_ONLY"
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
        "section_timings": t,
        "sections_unavailable": down,
        "owner_blockers": owner_blockers(runtime, venue,
                                         market.get("refdata", {}).get(
                                             "universe_pull")),
    }

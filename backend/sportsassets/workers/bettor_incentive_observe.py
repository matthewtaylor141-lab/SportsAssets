"""The incentive observation run: the smallest thing that collects it.

WHAT THIS IS NOT. It is not `bettor_live_loop` with a different market
list. That loop exists to observe, NORMALIZE, DECIDE and shadow-execute,
and every one of those stages is a liability here: the decision engine
would write verdicts nobody asked for, the settlement poller would
spend HTTP this run does not have, and the discovery refresh would
re-list the venue every fifteen minutes. This module holds a socket
open, writes down what arrives and stops. It imports no order function,
no decision engine and no shadow executor -- NOT as a policy, but as a
fact about the import list, which a test asserts.

WHAT IT REUSES RATHER THAN REBUILDS:

    the stop            `bettor_live_control` -- the same
                        `bettor_live_observation` row, the same
                        `CONTROL_EVERY_S`, the same fail-closed
                        semantics. There is not a second kill switch.
    the transport       `bettor_market_stream.MarketStream` -- the same
                        authenticated socket, the same epochs, the
                        same SUB_BATCH, the same backoff.
    the trading gate    `book_at()`, called and recorded verbatim. This
                        module does not re-implement or relax it.

FOUR BOUNDS, AND EACH ENDS THE RUN BY ITSELF:

    the ET date         [midnight ET, next midnight ET), half-open
    the control         `bettor_live_observation` set false
    HTTP                eight public requests, durable across restarts
    the socket          reconnects and resubscriptions, counted apart

THE ALLOWLIST NEVER MOVES. It is frozen from the captured manifest
before the socket opens and is not re-selected, re-ranked or topped up.
A programme that drifts mid-run VOIDS ITS MARKET-DATE for research
coverage; it does not change what is watched.

NO ORDERS. No order path exists in this module or its imports.

Run:  python -m pytest backend/tests/test_bettor_incentive_observe.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

from .. import bettor_incentive_budget as bud
from .. import bettor_incentive_feed as feed_mod
from .. import bettor_incentive_journal as jrnl_mod
from .. import bettor_incentive_manifest as man
from .. import bettor_incentive_state as st
from .. import bettor_live_control as ctl
from .. import bettor_market_stream as ms

log = logging.getLogger(__name__)

OBSERVE_VERSION = "BETTOR_INCENTIVE_OBSERVE_V1"

POLL_S = 0.5
KILL_ENV = "BETTOR_LIVE_LOOP"

# The trading gate's own bounds, passed through unchanged so the
# recorded trading verdict means what it means everywhere else.
MAX_SOURCE_AGE_S = 10.0
MAX_RECEIPT_AGE_S = 5.0

# When the single mid-run programme recheck is attempted, as a fraction
# of the window. Halfway: late enough to catch a change made after
# arming, early enough that the remaining window is still worth voiding
# or keeping.
RECHECK_AT = 0.5

# Reasons a run ends. Each is a different fact and none is an error.
END_WINDOW = "ET_DATE_ENDED"
END_CONTROL = "STOPPED_BY_CONTROL"
END_SOCKET = "SOCKET_BOUND_REACHED"
END_HTTP = "HTTP_BUDGET_REACHED"
END_DEADLINE = "RUN_FOR_S_ELAPSED"
END_ERROR = "UNRECOVERABLE"


def kill_switched() -> bool:
    return str(os.environ.get(KILL_ENV, "")).strip().lower() in \
        ("off", "0", "false", "no")


def _not_started(why: str, **kw) -> dict:
    return {"started": False, "why": why, "observe": OBSERVE_VERSION, **kw}


async def run(*, stream_factory=None, control_pool=None,
              run_for_s: float | None = None, sleep=None,
              now_fn=None) -> dict:
    """One incentive observation run. Returns; never raises to the caller.

    Every keyword is None in production. They exist so the rehearsal
    can drive THIS function through the real entry point rather than
    around it.
    """
    _sleep = sleep or asyncio.sleep
    _now = now_fn or time.time
    boot_id = uuid.uuid4().hex[:16]

    if kill_switched():
        log.info("bettor_incentive_observe: disabled by %s=off", KILL_ENV)
        return _not_started("KILL_SWITCH")

    # ── 1. THE AUTHORITATIVE STOP, READ FIRST ────────────────────────
    #
    # Before the manifest, before credentials, before a socket: a run
    # that is stopped must cost nothing at all. Same row, same
    # semantics, same fail-closed behaviour as the general loop.
    control = await ctl.read_control(control_pool)
    if ctl.is_closed(control):
        log.info("bettor_incentive_observe: not observing (%s: %s); "
                 "effective config %s", control["why"],
                 control.get("detail"), json.dumps(effective_config()))
        return _not_started(control["why"], control=control,
                            config=effective_config())

    # ── 2. THE FROZEN ALLOWLIST, AND NO FALLBACK ─────────────────────
    loaded = man.load()
    if not loaded.get("ok"):
        log.error("bettor_incentive_observe: %s (%s); not starting",
                  loaded["why"], loaded.get("detail"))
        return _not_started(loaded["why"], detail=loaded.get("detail"))
    manifest = loaded["manifest"]
    et_date = os.environ.get(man.ET_DATE_ENV) or manifest.get("et_date")
    frozen = man.freeze(manifest, et_date=et_date)
    if not frozen.get("ok"):
        # INSUFFICIENT_COVERAGE lands here, and it is a RESULT: the run
        # does not start and does not watch anything else instead.
        log.error("bettor_incentive_observe: %s -- %d qualifying markets; "
                  "NOT falling back to the general universe",
                  frozen["why"], frozen.get("markets", 0))
        return _not_started(frozen["why"], allowlist=frozen)
    slugs = frozen["slugs"]
    window = man.et_window(et_date)
    log.info("bettor_incentive_observe: %d markets, %d programme id(s), "
             "%d event start time(s). %s", frozen["markets"],
             frozen["distinct_programs"], frozen["distinct_events"],
             frozen["independence_note"])

    # ── 3. THE DURABLE RUN ROW, SO A RESTART RESUMES ─────────────────
    opened = await st.open_or_resume(
        control_pool, et_date=et_date,
        manifest_id=manifest.get("manifest_id") or "NONE",
        subcaps=bud.SUBCAPS, boot_id=boot_id)
    if not opened.get("ok"):
        log.error("bettor_incentive_observe: run row %s (%s); not starting",
                  opened["why"], opened.get("detail"))
        return _not_started(opened["why"], detail=opened.get("detail"))
    run_row = opened["run"]
    ledger = bud.RequestLedger()
    seeded = ledger.seed(opened["seed"])
    if opened["resumed"]:
        log.info("bettor_incentive_observe: RESUMED run %s (boot %d); "
                 "%d of %d requests already spent",
                 run_row["run_id"], run_row.get("boots", 0),
                 seeded["used"], ledger.total)

    # ── 4. CREDENTIALS, NAMED ────────────────────────────────────────
    from ..config import settings
    cfg = settings()
    key_id = getattr(cfg, "pmus_key_id", None)
    secret = getattr(cfg, "pmus_secret_key", None)
    if not key_id or not secret:
        # The ladder arrives over an AUTHENTICATED WebSocket. A stream
        # that cannot authenticate produces no books, and "no books"
        # must never look like "a quiet market".
        log.error("bettor_incentive_observe: no PMUS credentials in this "
                  "environment; not starting")
        return _not_started("NO_CREDENTIALS")

    # ── 5. THE JOURNAL ───────────────────────────────────────────────
    jdir = jrnl_mod.directory_for(run_row["run_id"])
    journal = jrnl_mod.Journal(jdir, run_id=run_row["run_id"])
    jopen = journal.open()
    if not jopen.get("ok"):
        log.error("bettor_incentive_observe: %s (%s); not starting -- a run "
                  "whose evidence cannot be written produces nothing",
                  jopen["why"], jopen.get("detail"))
        return _not_started(jopen["why"], detail=jopen.get("detail"))

    journal.run_open(
        observe=OBSERVE_VERSION, boot_id=boot_id, resumed=opened["resumed"],
        et_date=et_date, window=window, allowlist=frozen,
        manifest_id=manifest.get("manifest_id"),
        response_digest=manifest.get("response_digest"),
        config=effective_config(), budget=ledger.report(),
        orders="NONE -- this module imports no order path")
    journal.program_version(phase="ARM",
                            programs=frozen["programs_by_slug"],
                            manifest_id=manifest.get("manifest_id"))

    # ── 6. THE SOCKET ────────────────────────────────────────────────
    factory = stream_factory or ms.MarketStream
    holder: dict = {}

    def _on_book(slug, rec):
        """Socket thread. Classify, then write. Nothing decides here."""
        health = holder.get("health")
        if health is None:
            return
        cls = health.on_book(slug, rec)
        journal.ladder(cls, rec.get("book") or {})

    stream = factory(key_id, secret, on_book=_on_book)
    health = feed_mod.FeedHealth(stream)
    holder["health"] = health
    # The heartbeat, where the transport offers one. Its ABSENCE is
    # recorded rather than assumed away -- see `FeedHealth.liveness`.
    if hasattr(stream, "set_heartbeat_listener"):
        stream.set_heartbeat_listener(health.on_heartbeat)
    bounds = bud.ReconnectBounds()

    stream.subscribe(slugs)
    health.note_resubscribe(_batches(len(slugs)))
    bounds.note_resubscribe(_batches(len(slugs)))
    stream.start()

    t_start = _now()
    end_why = None
    last_control = t_start
    last_epoch = getattr(stream, "epoch", 0)
    last_alive = True
    gap_open_at = None
    recheck_done = False
    stop_seen_at = None
    journal.epoch(epoch=last_epoch, event="RUN_STARTED",
                  subscribed=len(slugs))

    try:
        while True:
            now = _now()

            # (a) THE WINDOW. Half-open: the next midnight belongs to
            #     the next date and is not observed.
            if now >= window["end_epoch"]:
                end_why = END_WINDOW
                break
            # (b) A BOUNDED REHEARSAL. Never set in production.
            if run_for_s is not None and now - t_start >= run_for_s:
                end_why = END_DEADLINE
                break
            # (c) THE SOCKET BOUNDS, counted apart.
            b = bounds.check(int(getattr(stream, "reconnects", 0) or 0))
            if not b["ok"]:
                end_why = END_SOCKET
                journal.gap(event="SOCKET_BOUND", why=b["why"], detail=b)
                break
            # (d) THE CONTROL, at the same cadence as everywhere else.
            if now - last_control >= ctl.CONTROL_EVERY_S:
                last_control = now
                c = await ctl.read_control(control_pool)
                if ctl.is_closed(c):
                    stop_seen_at = now
                    end_why = END_CONTROL
                    journal.gap(event="CONTROL_STOP", why=c["why"],
                                detail=c.get("detail"))
                    break

            # (e) EPOCH TRANSITIONS. A new epoch means the socket
            #     dropped and came back; every cached book is void and
            #     every slug is resubscribed, so both are recorded.
            epoch = int(getattr(stream, "epoch", 0) or 0)
            if epoch != last_epoch:
                journal.epoch(epoch=epoch, event="EPOCH_OPENED",
                              previous=last_epoch,
                              reconnects=getattr(stream, "reconnects", None),
                              resubscribed=len(slugs))
                health.note_resubscribe(_batches(len(slugs)))
                bounds.note_resubscribe(_batches(len(slugs)))
                last_epoch = epoch

            # (f) LIVENESS TRANSITIONS become GAP records. A gap is an
            #     interval we did not observe -- not a quiet book, and
            #     not a stale one.
            live = health.liveness(now)
            if live["alive"] != last_alive:
                if live["alive"]:
                    journal.gap(event="GAP_CLOSED", why=live["why"],
                                opened_at=gap_open_at,
                                duration_s=(round(now - gap_open_at, 4)
                                            if gap_open_at else None))
                    gap_open_at = None
                else:
                    gap_open_at = now
                    journal.gap(event="GAP_OPENED", why=live["why"],
                                detail=live)
                last_alive = live["alive"]

            # (g) ONE MID-RUN PROGRAMME RECHECK, if the ledger allows.
            #     Skipped without complaint when it does not: the
            #     budget binds, and a recheck is not worth breaking it
            #     for.
            if (not recheck_done
                    and now - window["start_epoch"]
                    >= RECHECK_AT * window["span_s"]):
                recheck_done = True
                r = ledger.spend(bud.K_RECHECK, why="mid-run programme terms")
                journal.program_version(
                    phase="RECHECK", programs={},
                    attempted=r["ok"], verdict=r.get("verdict"),
                    detail=r.get("detail"),
                    note=("the recheck READ is performed by the arm-time "
                          "capture path; this record reserves and reports "
                          "its budget unit"))
                await st.persist_spend(control_pool, run_row, ledger.spent)
                if ledger.remaining() <= 0:
                    log.info("bettor_incentive_observe: HTTP budget spent; "
                             "the socket keeps delivering")

            await _sleep(POLL_S)
    except asyncio.CancelledError:
        end_why = END_CONTROL
        raise
    except Exception as exc:                # noqa: BLE001 -- named
        end_why = END_ERROR
        log.error("bettor_incentive_observe: %s", type(exc).__name__)
        journal.gap(event="RUN_ERROR", why=END_ERROR,
                    detail=type(exc).__name__)
    finally:
        closed = _close_stream(stream)
        feed_report = health.report()
        # PER-MARKET COVERAGE, computed from what was actually
        # observed. It is a RESEARCH verdict and is labelled as one.
        coverage = _coverage(slugs, health, window, t_start, _now())
        journal.run_close(
            end_why=end_why, stop_seen_at=stop_seen_at,
            ran_s=round(_now() - t_start, 3),
            stream=closed, feed=feed_report, coverage=coverage,
            budget=ledger.report(),
            bounds=bounds.check(int(getattr(stream, "reconnects", 0) or 0)))
        jreport = journal.close()
        await st.persist_spend(control_pool, run_row, ledger.spent)
        if end_why in (END_WINDOW, END_DEADLINE, END_SOCKET):
            await st.close_run(control_pool, run_row, end_why)

    out = {
        "started": True, "observe": OBSERVE_VERSION, "boot_id": boot_id,
        "run_id": run_row["run_id"], "resumed": opened["resumed"],
        "end_why": end_why, "ran_s": round(_now() - t_start, 3),
        "et_date": et_date, "window": window,
        "allowlist": {k: frozen[k] for k in
                      ("slugs", "markets", "distinct_programs",
                       "distinct_events", "independence_note",
                       "dropped_by_watch_cap", "watch_max", "no_fallback")},
        "feed": feed_report, "coverage": coverage,
        "budget": ledger.report(),
        "journal": jreport, "stream": closed,
        "orders_submitted": 0,
        "order_path": "NONE -- this module imports no order function",
    }
    log.info("bettor_incentive_observe: %s after %.1fs; %s",
             end_why, out["ran_s"], json.dumps(
                 {"frames": feed_report["frames"],
                  "epochs": feed_report["epoch_count"],
                  "http": ledger.report()["used"]}, default=str))
    return out


def _batches(n: int) -> int:
    """How many subscribe requests `n` slugs take, at the SDK ceiling."""
    return max(1, (int(n) + ms.SUB_BATCH - 1) // ms.SUB_BATCH)


def _close_stream(stream) -> dict:
    try:
        stream.stop()
    except Exception as exc:                # noqa: BLE001
        return {"stopped": False, "error": type(exc).__name__}
    out = {"stopped": True,
           "reconnects": getattr(stream, "reconnects", None),
           "socket_closes": getattr(stream, "socket_closes", None),
           "socket_closed_at": getattr(stream, "socket_closed_at_iso", None),
           "socket_close_ok": getattr(stream, "socket_close_ok", None),
           "updates": getattr(stream, "updates", None)}
    return out


def _coverage(slugs, health, window, t_start, t_end) -> dict:
    """Per-market research coverage for what this boot actually watched.

    THE DENOMINATOR IS THIS BOOT'S SPAN, not the whole ET date, and the
    difference is stated rather than smoothed over. A run that was
    restarted has several boots; only the offline reconstruction,
    reading the journal's EPOCH and GAP records across boots, can say
    what fraction of the DATE was observed. Reporting a boot's fraction
    as the date's would be exactly the extrapolation this refuses to do.
    """
    span = max(0.0, t_end - t_start)
    expected = int(span)                    # one scoring instant per second
    rep = health.report()
    per = {}
    for s in slugs:
        seen = sum(v["frames"] for k, v in rep["initial_ladders"].items()
                   if k.startswith("%s@" % s))
        per[s] = {"frames": seen,
                  "had_initial_ladder": seen > 0}
    return {
        "boot_span_s": round(span, 3),
        "boot_instants_expected": expected,
        "per_market_frames": per,
        "markets_with_no_frame": [s for s, v in per.items()
                                  if not v["had_initial_ladder"]],
        "scope": ("THIS BOOT ONLY. Coverage of the ET date is computed "
                  "offline from the journal across every boot; a boot's "
                  "fraction is not the date's fraction."),
        "not_a_trading_verdict": (
            "research coverage is independent of trading eligibility"),
    }


def effective_config() -> dict:
    """What THIS PROCESS holds, read from its own environment.

    Logged even when the run does not start, because setting an
    environment variable does not prove the running process received
    it -- on 2026-09-21 a kill switch was set, acknowledged, survived a
    demonstrated restart and never reached the process.
    """
    return {
        "observe": OBSERVE_VERSION,
        "manifest_path": os.environ.get(man.MANIFEST_ENV) or None,
        "et_date": os.environ.get(man.ET_DATE_ENV) or None,
        "watch_max": os.environ.get(man.WATCH_MAX_ENV) or man.WATCH_MAX,
        "http_cap": os.environ.get(bud.TOTAL_ENV) or bud.TOTAL_CAP,
        "max_reconnects": os.environ.get(bud.RECONNECT_ENV)
        or bud.MAX_RECONNECTS,
        "max_resubscribes": os.environ.get(bud.RESUBSCRIBE_ENV)
        or bud.MAX_RESUBSCRIBES,
        "journal_dir": os.environ.get(jrnl_mod.DIR_ENV) or None,
        "journal_disk_declared": os.environ.get(jrnl_mod.DISK_ENV) or None,
        "kill_switch": os.environ.get(KILL_ENV) or None,
        "control_key": ctl.CONTROL_KEY,
        "control_every_s": ctl.CONTROL_EVERY_S,
        "max_contracts": "N/A -- no order path exists in this module",
    }


def describe() -> dict:
    return {
        "observe": OBSERVE_VERSION,
        "ends_on": [END_WINDOW, END_CONTROL, END_SOCKET, END_DEADLINE,
                    END_ERROR],
        "reuses": {"stop": ctl.CONTROL_KEY,
                   "transport": ms.STREAM_VERSION,
                   "trading_gate": "bettor_market_stream.book_at"},
        "allowlist": man.describe(),
        "budget": bud.describe(),
        "feed": feed_mod.describe(),
        "journal": jrnl_mod.describe(),
        "state": st.describe(),
        "submits_orders": False,
    }

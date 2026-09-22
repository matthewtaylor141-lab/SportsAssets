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

    # ── 3. THE ALLOWANCE ROW: WHERE THE REQUEST CEILING ACTUALLY LIVES
    #
    # Read before the run row, because an unarmed or expired allowance
    # means this process may not issue a single request and there is
    # nothing to resume into. The ceiling itself is never held here --
    # `DurableLedger` takes each unit from this row under a lock.
    # NOT `read_budget`: that gates on the general distinct allowance,
    # which this run's arm deliberately zeroes as a rollback guard.
    allowance = await ctl.read_incentive_allowance(control_pool)
    if not allowance.get("open"):
        log.info("bettor_incentive_observe: not observing (%s: %s); "
                 "effective config %s", allowance["state"],
                 allowance.get("detail"), json.dumps(effective_config()))
        return _not_started(allowance["state"], budget=allowance,
                            config=effective_config())
    probe_id = allowance.get("probe_id")
    if not allowance.get("general_caps_zeroed"):
        # NOT FATAL, BUT SAID OUT LOUD. The run is safe either way; what
        # is not safe is a later rollback that removes the mode while
        # the control is still true, because the general loop would
        # then begin discovery. `obs-arm-incentive` zeroes them.
        log.warning("bettor_incentive_observe: the allowance row leaves the "
                    "general acquisition caps NON-ZERO; a rollback that "
                    "removes the mode while the control is true could start "
                    "general discovery. Arm with obs-arm-incentive.")

    # ── 3b. THE DURABLE RUN ROW: identity and the SOCKET totals ──────
    opened = await st.open_or_resume(
        control_pool, et_date=et_date,
        manifest_id=manifest.get("manifest_id") or "NONE",
        boot_id=boot_id)
    if not opened.get("ok"):
        log.error("bettor_incentive_observe: run row %s (%s); not starting",
                  opened["why"], opened.get("detail"))
        return _not_started(opened["why"], detail=opened.get("detail"))
    run_row = opened["run"]
    ledger = bud.DurableLedger(control_pool, probe_id=probe_id)
    spent_before = await ledger.read()
    if opened["resumed"]:
        log.info("bettor_incentive_observe: RESUMED run %s (boot %d); the "
                 "allowance row already holds %s of %s requests",
                 run_row["run_id"], run_row.get("boots", 0),
                 spent_before.get("total_reserved"),
                 spent_before.get("total_cap"))

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

    # ── 5. THE JOURNAL, IN POSTGRES ──────────────────────────────────
    #
    # `open()` also writes the BOOT GAP: on a resumed run it reads the
    # last record of the previous boot and records `[then, now)` as
    # UNOBSERVED. Without it the outage would show only as an absence
    # of rows, and on a change-driven feed an absence of rows is
    # exactly what an unchanged book looks like.
    journal = jrnl_mod.PgJournal(control_pool, run_id=run_row["run_id"],
                                 boot_id=boot_id)
    jopen = await journal.open()
    if not jopen.get("ok"):
        log.error("bettor_incentive_observe: %s (%s); not starting -- a run "
                  "whose evidence cannot be written produces nothing",
                  jopen["why"], jopen.get("detail"))
        return _not_started(jopen["why"], detail=jopen.get("detail"))

    journal.run_open(
        observe=OBSERVE_VERSION, resumed=opened["resumed"],
        et_date=et_date, window=window, allowlist=frozen,
        manifest_id=manifest.get("manifest_id"),
        response_digest=manifest.get("response_digest"),
        config=effective_config(), budget=ledger.report(),
        allowance_at_boot=spent_before, probe_id=probe_id,
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

    # ── THE SOCKET ALLOWANCE BRIDGE ──────────────────────────────────
    #
    # The stream runs its own event loop on its own thread, so it
    # cannot await a coroutine on ours. This hands the reservation
    # across and BLOCKS the socket thread on the answer -- which is
    # correct: the attempt must not be made until the unit is durably
    # committed, and a connect or a subscribe is rare enough that one
    # database round trip costs nothing a connection was not already
    # costing.
    #
    # UNCERTAIN MEANS NO. A timeout or an error answers False, so the
    # attempt is not made; losing an allowance unit is the safe
    # direction and a spent-but-unused unit shows in the row.
    main_loop = asyncio.get_running_loop()

    def _socket_reserve(kind: str) -> bool:
        try:
            fut = asyncio.run_coroutine_threadsafe(
                ledger.spend(kind, why="socket"), main_loop)
            return bool(fut.result(timeout=ctl.CONTROL_READ_TIMEOUT_S)["ok"])
        except Exception as exc:            # noqa: BLE001 -- named
            log.error("bettor_incentive_observe: socket reservation (%s) "
                      "did not answer (%s); refusing the attempt",
                      kind, type(exc).__name__)
            return False

    stream = factory(key_id, secret, on_book=_on_book)
    if hasattr(stream, "set_socket_allowance"):
        stream.set_socket_allowance(_socket_reserve)
    health = feed_mod.FeedHealth(stream)
    holder["health"] = health
    # The heartbeat, where the transport offers one. Its ABSENCE is
    # recorded rather than assumed away -- see `FeedHealth.liveness`.
    if hasattr(stream, "set_heartbeat_listener"):
        stream.set_heartbeat_listener(health.on_heartbeat)

    # `subscribe()` only QUEUES the slugs; the messages that leave the
    # socket are reserved inside the stream, one unit each.
    stream.subscribe(slugs)
    stream.start()

    t_start = _now()
    end_why = None
    last_control = t_start
    last_epoch = getattr(stream, "epoch", 0)
    last_alive = True
    gap_open_at = None
    gap_open_why = None
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
            # (c) THE SOCKET ALLOWANCE, ENFORCED AT THE BOUNDARY.
            #
            # Nothing is counted here. The stream reserves each connect
            # and each subscribe message BEFORE making it, and a
            # refusal sets its stop flag. This only NOTICES that and
            # ends the run; the bound was already applied where the
            # attempt would have been.
            refused = getattr(stream, "stopped_by_allowance", None)
            if refused:
                end_why = END_SOCKET
                journal.gap(event="SOCKET_ALLOWANCE_REFUSED", why=refused,
                            detail={"connect_attempts": getattr(
                                        stream, "socket_connect_attempts", 0),
                                    "subscribe_messages": getattr(
                                        stream, "subscribe_messages_sent", 0),
                                    "refusals": getattr(
                                        stream, "refusals", [])})
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
                # COUNT BY THE EPOCH DELTA, NOT BY THE DETECTION.
                #
                # THE DEFECT THIS REPAIRS. This used to add ONE
                # resubscription per detected change. The loop polls
                # every POLL_S = 0.5s and the stream reconnects on its
                # own thread, so a flapping socket can advance the
                # epoch several times between two polls -- and the poll
                # that finally notices would record a single
                # resubscription for all of them. The resubscription
                # ceiling is the bound that exists SPECIFICALLY to
                # catch a subscribe storm, and it was the one that
                # failed to bind. `reconnects` was never affected: it
                # is read from the stream's own counter.
                #
                # Every epoch resubscribes every slug, so the honest
                # count is one batch set PER EPOCH CROSSED.
                # REPORTING ONLY. The bound lives at the boundary; what
                # is recorded here is what the stream ACTUALLY did,
                # read from its own counters rather than inferred from
                # how many epochs a poll happened to skip.
                crossed = max(1, epoch - last_epoch)
                journal.epoch(epoch=epoch, event="EPOCH_OPENED",
                              previous=last_epoch, epochs_crossed=crossed,
                              reconnects=getattr(stream, "reconnects", None),
                              connect_attempts=getattr(
                                  stream, "socket_connect_attempts", None),
                              subscribe_messages=getattr(
                                  stream, "subscribe_messages_sent", None),
                              resubscribed=len(slugs))
                health.note_resubscribe(crossed)
                last_epoch = epoch
                # CARRY THE RUN TOTALS FORWARD. An epoch transition IS
                # a reconnect, so this write is as rare as the event it
                # records.
                await st.note_socket(
                    control_pool, run_row,
                    reconnects=int(getattr(stream,
                                           "socket_connect_attempts", 0)),
                    resubscribes=int(getattr(stream,
                                             "subscribe_messages_sent", 0)))

            # (f) LIVENESS TRANSITIONS become GAP records. A gap is an
            #     interval we did not observe -- not a quiet book, and
            #     not a stale one.
            live = health.liveness(now)
            if live["alive"] != last_alive:
                if live["alive"]:
                    # `from`/`to` ON THE CLOSING RECORD, because that is
                    # the only one that knows both ends -- and it is
                    # what `journal.covers()` reads to decide whether an
                    # instant was observed. A GAP_OPENED without them
                    # marks nothing; the interval would look like a
                    # quiet book to a reconstruction, which is the whole
                    # error this release exists to avoid.
                    #
                    # The reason recorded is why the gap EXISTED, not
                    # "ALIVE", which is merely how it ended.
                    journal.gap(event="GAP_CLOSED", why=gap_open_why,
                                **{"from": gap_open_at, "to": now},
                                duration_s=(round(now - gap_open_at, 4)
                                            if gap_open_at else None))
                    gap_open_at, gap_open_why = None, None
                else:
                    gap_open_at, gap_open_why = now, live["why"]
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
                r = await ledger.spend(bud.K_RECHECK,
                                       why="mid-run programme terms")
                journal.program_version(
                    phase="RECHECK", programs={},
                    attempted=r["ok"], verdict=r.get("verdict"),
                    detail=r.get("detail"),
                    note=("the recheck READ is performed by the arm-time "
                          "capture path; this record reserves and reports "
                          "its budget unit"))
                if not r["ok"]:
                    log.info("bettor_incentive_observe: recheck not funded "
                             "(%s); the socket keeps delivering",
                             r.get("verdict"))

            await journal.flush()
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
        # A GAP THAT WAS STILL OPEN WHEN THE RUN ENDED IS STILL A GAP.
        # Leaving it unclosed would let a reconstruction treat the
        # final unobserved stretch as coverage.
        if gap_open_at is not None:
            journal.gap(event="GAP_CLOSED", why=gap_open_why or "RUN_ENDED",
                        **{"from": gap_open_at, "to": _now()},
                        duration_s=round(_now() - gap_open_at, 4),
                        closed_by="RUN_END")
        closed = _close_stream(stream)
        feed_report = health.report()
        # PER-MARKET COVERAGE, computed from what was actually
        # observed. It is a RESEARCH verdict and is labelled as one.
        coverage = _coverage(slugs, health, window, t_start, _now())
        final_bounds = {
            "scope": "PER RUN -- enforced in the allowance row at the "
                     "connect/subscribe boundary, not counted in this loop",
            "connect_attempts_this_boot": getattr(
                stream, "socket_connect_attempts", 0),
            "subscribe_messages_this_boot": getattr(
                stream, "subscribe_messages_sent", 0),
            "reconnects_reported_by_stream": getattr(
                stream, "reconnects", None),
            "refusals": getattr(stream, "refusals", []),
            "stopped_by_allowance": getattr(
                stream, "stopped_by_allowance", None)}
        spent_after = await ledger.read()
        journal.run_close(
            end_why=end_why, stop_seen_at=stop_seen_at,
            ran_s=round(_now() - t_start, 3),
            stream=closed, feed=feed_report, coverage=coverage,
            budget=ledger.report(), allowance_at_close=spent_after,
            bounds=final_bounds)
        jreport = await journal.close()
        await st.note_socket(
            control_pool, run_row,
            reconnects=final_bounds["connect_attempts_this_boot"],
            resubscribes=final_bounds["subscribe_messages_this_boot"])
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
        # THE ROW'S OWN COUNT, BEFORE AND AFTER. These differing across
        # a restart is the evidence that the ceiling survived it.
        "allowance_at_boot": spent_before, "allowance_at_close": spent_after,
        "socket_bounds": final_bounds,
        "journal": jreport, "stream": closed,
        "orders_submitted": 0,
        "order_path": "NONE -- this module imports no order function",
    }
    log.info("bettor_incentive_observe: %s after %.1fs; %s",
             end_why, out["ran_s"], json.dumps(
                 {"frames": feed_report["frames"],
                  "epochs": feed_report["epoch_count"],
                  "http_row": spent_after.get("total_reserved"),
                  "rows": jreport.get("rows_written")}, default=str))
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
        "http_cap": "%d, held in the armed allowance row (%s), not in "
                    "this process" % (bud.TOTAL_CAP, ctl.BUDGET_KEY),
        "max_reconnects": os.environ.get(bud.RECONNECT_ENV)
        or bud.MAX_RECONNECTS,
        "max_resubscribes": os.environ.get(bud.RESUBSCRIBE_ENV)
        or bud.MAX_RESUBSCRIBES,
        "journal": "postgres table %s" % jrnl_mod.TABLE,
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

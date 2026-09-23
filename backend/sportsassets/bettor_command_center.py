"""THE COMMAND CENTRE READ MODEL. Evidence in, five views out.

WHAT THIS EXISTS TO PREVENT. A dashboard's failure mode is not a wrong
number, it is a CONFIDENT number. Four specific confusions have already
cost real time on this programme and each one is refused here in code:

  1. A TRUE CONTROL FLAG IS NOT COLLECTION. `bettor_live_observation`
     being true says somebody flipped a switch. Frames in the journal
     say the venue is answering. `lifecycle()` will not report
     COLLECTING without ladder records, and ARMED_NO_FRAMES is a named
     state precisely so the gap between the two is visible.

  2. A QUIET BOOK IS NOT A BROKEN FEED. This is a change-driven feed:
     a market nobody touches sends nothing and its ladder stays
     CURRENT. `bettor_incentive_journal.segments()` already encodes
     that -- a segment runs to the last record of its boot, not its
     last ladder -- and this module calls it rather than re-deriving
     coverage from frame recency.

  3. AN OLD RESULT IS NOT THIS RUN'S RESULT. Every figure carries its
     own `as_of` and `source`. A view built from a stale artifact says
     so; it never inherits the freshness of the page around it.

  4. A MISSING MEASUREMENT IS NOT ZERO. `unknown()` is the only way to
     render an absent quantity. Zeros sum, average and chart;
     UNKNOWN does none of those, which is the entire point.

EVERY NUMBER IS A CELL, NOT A SCALAR. `cell()` carries value, source,
as_of and status together, so a renderer cannot display a figure while
dropping where it came from -- the two travel or neither does.

THIS MODULE READS. It opens no socket, places no order, changes no
control and spends no allowance. The reader is SELECT-only.

Run:  python -m pytest backend/tests/test_bettor_command_center.py
"""
from __future__ import annotations

import datetime as dt
import json
import os

VERSION = "BETTOR_COMMAND_CENTER_V1"

# ── cell status ──────────────────────────────────────────────────────
OK = "OK"
UNKNOWN = "UNKNOWN"
NOT_APPLICABLE = "NOT_APPLICABLE"
STALE = "STALE"

# ── run lifecycle, decided from evidence ─────────────────────────────
L_SCHEDULED = "SCHEDULED"
L_ARMED = "ARMED"
L_ARMED_NO_FRAMES = "ARMED_NO_FRAMES"
L_COLLECTING = "COLLECTING"
L_INTERRUPTED = "INTERRUPTED"
L_COMPLETED = "COMPLETED"
L_FAILED = "FAILED"
L_STOPPED = "STOPPED"

# ── evidence strength, for the capability map ────────────────────────
E_IMPLEMENTED = "IMPLEMENTED"
E_REPLAY = "REPLAY_TESTED"
E_LIVE_OBSERVED = "LIVE_OBSERVED"
E_LIVE_EXECUTED = "LIVE_EXECUTION_VALIDATED"
E_ABSENT = "NOT_IMPLEMENTED"

# The economic measurement window, fixed. Early frames are operational
# evidence and are excluded from it.
WINDOW_START = "2026-09-23T04:00:00+00:00"
WINDOW_END = "2026-09-24T04:00:00+00:00"


def _epoch(iso: str) -> float:
    return dt.datetime.fromisoformat(iso).timestamp()


def iso(t) -> str | None:
    if t is None:
        return None
    if isinstance(t, str):
        return t
    return dt.datetime.fromtimestamp(float(t), dt.timezone.utc).isoformat()


# ── the cell ─────────────────────────────────────────────────────────

def cell(value, source, as_of=None, status=OK, note=None) -> dict:
    """One displayable figure, inseparable from its provenance.

    `source` is mandatory and not defaulted. A figure whose origin
    nobody recorded is a figure nobody can check, and the cheapest way
    to guarantee every number on the page has one is to make it
    impossible to build a cell without it.
    """
    if not source:
        raise ValueError("every cell needs a source; refusing to build one "
                         "that cannot be traced back to a record")
    return {"value": value, "source": source, "as_of": as_of,
            "status": status, "note": note}


def unknown(source, why, as_of=None) -> dict:
    """An absent measurement. NEVER render this as 0."""
    return cell(None, source, as_of=as_of, status=UNKNOWN, note=why)


def not_applicable(source, why) -> dict:
    return cell(None, source, status=NOT_APPLICABLE, note=why)


# ═══ VIEW 1: LIVE OPERATION ══════════════════════════════════════════

E_OPENED = "GAP_OPENED"
E_CLOSED = "GAP_CLOSED"
E_RUN_ERROR = "RUN_ERROR"
E_CONTROL_STOP = "CONTROL_STOP"
E_ALLOWANCE = "SOCKET_ALLOWANCE_REFUSED"


def gap_view(records) -> dict:
    """Gaps, PAIRED. An opened gap that later closed is not open.

    THE BUG THIS EXISTS TO NOT HAVE. The journal writes GAP_OPENED with
    no `to` and GAP_CLOSED carrying both ends. Anything that decides
    "open" by looking for a record without a `to` finds every gap the
    run ever recovered from, and pins the page at INTERRUPTED for the
    rest of the day. Opened and closed are matched in time order and
    only the unmatched tail is open.
    """
    opened, closed, boot, errors, stops, refusals = [], [], [], [], [], []
    for r in records or []:
        if r.get("kind") != "GAP":
            continue
        p = r.get("payload") or {}
        ev = p.get("event")
        if ev == E_OPENED:
            opened.append(r)
        elif ev == E_CLOSED:
            closed.append(r)
        elif ev == E_RUN_ERROR:
            errors.append(r)
        elif ev == E_CONTROL_STOP:
            stops.append(r)
        elif ev == E_ALLOWANCE:
            refusals.append(r)
        elif p.get("from") is not None and p.get("to") is not None:
            # The boot gap `open()` writes before anything else. It is
            # already closed by construction: it carries both ends.
            boot.append(r)

    # Pair in time order. A GAP_CLOSED consumes the earliest unmatched
    # GAP_OPENED before it; what is left over is genuinely open.
    unmatched = list(sorted(opened, key=lambda r: r.get("at") or 0.0))
    intervals = []
    for c in sorted(closed, key=lambda r: r.get("at") or 0.0):
        p = c.get("payload") or {}
        intervals.append({"from": p.get("from"), "to": p.get("to"),
                          "why": p.get("why"),
                          "duration_s": p.get("duration_s"),
                          "closed_by": p.get("closed_by"),
                          "source": "journal GAP_CLOSED"})
        if unmatched:
            unmatched.pop(0)
    for b in boot:
        p = b.get("payload") or {}
        intervals.append({"from": p.get("from"), "to": p.get("to"),
                          "why": p.get("why") or "BOOT_GAP",
                          "duration_s": p.get("duration_s"),
                          "closed_by": "BOOT",
                          "source": "journal boot gap"})

    open_rows = [{"from": r.get("at"), "to": None,
                  "why": (r.get("payload") or {}).get("why"),
                  "source": "journal GAP_OPENED with no matching GAP_CLOSED"}
                 for r in unmatched]
    unobserved = sum(float(i.get("duration_s") or 0.0) for i in intervals)
    return {"closed": sorted(intervals, key=lambda i: i.get("from") or 0.0),
            "open": open_rows,
            "n_closed": len(intervals), "n_open": len(open_rows),
            "unobserved_s": round(unobserved, 3),
            "errors": errors, "control_stops": stops,
            "allowance_refusals": refusals,
            "note": "a gap is an interval NOBODY OBSERVED. A quiet book is "
                    "not one -- see connection_health()."}


def lifecycle(*, control, probe, ladders, gaps, errors, run_close,
              now, window_end=None) -> dict:
    """Which state the run is ACTUALLY in, from evidence only.

    THE ORDER MATTERS AND IS NOT ARBITRARY. Failure and completion are
    terminal and are read first; only then does the live/idle split
    apply. Reading "control is true" first is how a dashboard ends up
    calling a dead run healthy.

    `gaps` is the OPEN list from `gap_view()`, not every gap record.
    """
    armed = bool(probe) and probe.get("armed") is True
    n_ladders = len(ladders or [])
    end = _epoch(window_end or WINDOW_END)

    if errors:
        p = (errors[0].get("payload") or {}) if isinstance(errors[0], dict) \
            else {}
        return {"state": L_FAILED,
                "why": "a RUN_ERROR record exists: %s"
                       % (p.get("detail") or p.get("why") or "unspecified"),
                "evidence": "journal RUN_ERROR"}

    # COMPLETED MEANS NOTHING CAME AFTER, and a RUN_CLOSE alone does not
    # establish that.
    #
    # THE BUG THIS FIXES, caught by reconciling against the real run.
    # Boot A was replaced mid-run and wrote a RUN_CLOSE on its way out;
    # boot B then reopened and has been persisting frames ever since.
    # Treating any RUN_CLOSE as terminal reported the live run as
    # COMPLETED -- the most dangerous possible error on this page,
    # because it says "nothing more is owed" about a run that is still
    # going and still needs to be stopped at its fixed end.
    #
    # A close is terminal only when NO FRAME FOLLOWS IT.
    if run_close and n_ladders > 0:
        close_at = float((run_close.get("at") if isinstance(run_close, dict)
                          else 0) or 0)
        after = [r for r in ladders
                 if float(r.get("at") or 0.0) > close_at]
        if not after:
            return {"state": L_COMPLETED,
                    "why": "the run closed and no frame followed the close",
                    "evidence": "journal RUN_CLOSE at %s + %d ladder "
                                "records, none after it"
                                % (iso(close_at), n_ladders)}
        # else: a later boot reopened. Fall through to the live branches.

    if not control:
        if n_ladders > 0:
            return {"state": L_STOPPED,
                    "why": "control is false and frames exist -- the run "
                           "ended; whether that was the fixed end or an "
                           "early stop is the RUN_CLOSE record's answer, "
                           "and there is none",
                    "evidence": "control false, %d ladders, no RUN_CLOSE"
                                % n_ladders}
        if armed:
            return {"state": L_ARMED,
                    "why": "allowance armed, observation not started",
                    "evidence": "probe row in the armed shape, control false"}
        return {"state": L_SCHEDULED,
                "why": "not armed and not collecting",
                "evidence": "no armed probe row, control false"}

    # Control is TRUE from here. That alone proves nothing.
    if n_ladders == 0:
        return {"state": L_ARMED_NO_FRAMES,
                "why": "THE CONTROL IS ON AND NOTHING HAS BEEN PERSISTED. "
                       "A flipped flag and a connected socket are not "
                       "collection; this state exists so the difference "
                       "cannot hide.",
                "evidence": "control true, zero ladder records"}

    if gaps:
        g = gaps[0] if isinstance(gaps[0], dict) else {}
        return {"state": L_INTERRUPTED,
                "why": "a gap is open: %s" % (g.get("why") or "unnamed"),
                "evidence": "journal GAP_OPENED with no GAP_CLOSED"}

    if now >= end:
        return {"state": L_STOPPED,
                "why": "past the fixed window end with the control still "
                       "true -- the stop has not been applied",
                "evidence": "now >= window end, control true"}

    return {"state": L_COLLECTING,
            "why": "frames are being persisted",
            "evidence": "control true, %d ladder records, no open gap"
                        % n_ladders}


H_LIVE_QUIET = "CONNECTED_QUIET"
H_LIVE_ACTIVE = "CONNECTED_ACTIVE"
H_DISCONNECTED = "DISCONNECTED"
H_NOT_STARTED = "NOT_STARTED"
H_UNKNOWN = "UNKNOWN"


def connection_health(records, *, now, control, quiet_active_s=120.0) -> dict:
    """Connected-and-quiet vs disconnected. THEY ARE NOT THE SAME THING.

    THE MISTAKE THIS REFUSES. The feed is change-driven. A market
    nobody is trading sends nothing, for hours, while the socket is
    perfectly healthy -- so "no frame for N seconds" is not evidence of
    a broken feed, and a dashboard that colours it red teaches its
    reader to ignore red.

    So silence is never the verdict here. The verdict comes from the
    worker's OWN liveness detector, which is journalled: it writes
    GAP_OPENED when it stops believing the socket and GAP_CLOSED when
    it starts again. An unmatched GAP_OPENED means DISCONNECTED on the
    worker's own evidence. No open gap, with an epoch open, means
    CONNECTED -- and then, and only then, frame recency distinguishes
    ACTIVE from QUIET, which is a description of the BOOK, not a
    health verdict.

    When the worker is not writing at all we say UNKNOWN by name. The
    one thing we do not do is infer health from silence.
    """
    epochs = [r for r in records or [] if r.get("kind") == "EPOCH"]
    ladders = [r for r in records or [] if r.get("kind") == "LADDER"]
    gaps = gap_view(records)
    last_record = max((float(r.get("at") or 0.0) for r in records or []),
                      default=None)
    last_frame = max((float(r.get("at") or 0.0) for r in ladders),
                     default=None)

    started = any((r.get("payload") or {}).get("event") == "RUN_STARTED"
                  for r in epochs)
    reconnects = [r for r in epochs
                  if (r.get("payload") or {}).get("event") == "EPOCH_OPENED"]
    last_epoch_rec = epochs[-1] if epochs else None
    sock = {}
    for r in reversed(epochs):
        p = r.get("payload") or {}
        if p.get("connect_attempts") is not None:
            sock = p
            break

    if not started:
        verdict, why = (H_NOT_STARTED,
                        "no RUN_STARTED record -- the observation loop has "
                        "not opened a socket under this run id")
    elif gaps["n_open"]:
        verdict = H_DISCONNECTED
        why = ("the worker's own liveness detector opened a gap (%s) and "
               "has not closed it"
               % (gaps["open"][0].get("why") or "unnamed"))
    elif last_record is None:
        verdict, why = (H_UNKNOWN, "no records at all")
    elif last_frame is not None and (now - last_frame) <= quiet_active_s:
        verdict = H_LIVE_ACTIVE
        why = "a frame arrived %.0fs ago" % (now - last_frame)
    else:
        verdict = H_LIVE_QUIET
        why = ("no open gap, so the worker still believes the socket. The "
               "book is QUIET, which on a change-driven feed is a "
               "description of the market, not a fault.")

    return {
        "verdict": verdict,
        "why": why,
        "quiet_is_not_broken": True,
        "run_started": cell(started, "journal EPOCH/RUN_STARTED"),
        "epochs_opened": cell(len(reconnects), "journal EPOCH/EPOCH_OPENED",
                              note="each one is a reconnect: the socket "
                                   "dropped and came back, every cached "
                                   "book was void and every slug resubscribed"),
        "last_epoch_at": cell(iso(last_epoch_rec.get("at"))
                              if last_epoch_rec else None,
                              "journal EPOCH"),
        "last_record_at": cell(iso(last_record), "journal (any kind)"),
        "last_frame_at": (cell(iso(last_frame), "journal LADDER")
                          if last_frame is not None else
                          unknown("journal LADDER", "no frame persisted yet")),
        "silence_s": (cell(round(now - last_frame, 1), "journal LADDER",
                           note="SILENCE IS NOT A FAULT on this feed")
                      if last_frame is not None else
                      unknown("journal LADDER", "no frame persisted yet")),
        "socket_counters": (
            cell({"connect_attempts": sock.get("connect_attempts"),
                  "subscribe_messages": sock.get("subscribe_messages"),
                  "reconnects": sock.get("reconnects")},
                 "journal EPOCH payload")
            if sock else unknown("journal EPOCH payload",
                                 "no epoch record carries socket counters "
                                 "yet -- the run has not reconnected")),
    }


def split_periods(ladders, *, window_start=None, window_end=None) -> dict:
    """Early operational frames vs the economic measurement window.

    THE TWO ARE NEVER POOLED. Early collection exists to prove the
    plumbing; the window is what any reward figure may be computed on.
    Mixing them would let a longer run look like better coverage of a
    fixed-length window.
    """
    a = _epoch(window_start or WINDOW_START)
    b = _epoch(window_end or WINDOW_END)
    early, inside, late = [], [], []
    for r in ladders or []:
        t = float(r.get("at") or 0.0)
        (early if t < a else inside if t < b else late).append(r)
    return {
        "window": {"start": iso(a), "end": iso(b),
                   "half_open": "[start, end)"},
        "early": {"frames": len(early),
                  "first": iso(min((float(r["at"]) for r in early),
                                   default=None)) if early else None,
                  "last": iso(max((float(r["at"]) for r in early),
                                  default=None)) if early else None,
                  "counts_toward_measurement": False,
                  "why": "operational evidence only -- excluded from the "
                         "window's coverage and reward calculations"},
        "measurement": {"frames": len(inside),
                        "first": iso(min((float(r["at"]) for r in inside),
                                         default=None)) if inside else None,
                        "last": iso(max((float(r["at"]) for r in inside),
                                        default=None)) if inside else None},
        "after_end": {"frames": len(late),
                      "why": "past the fixed end; excluded"},
    }


def allowance(probe) -> dict:
    """Usage against every limit, with the limit shown beside it."""
    if not probe:
        return {"known": False,
                "why": "no probe row -- nothing has been armed"}
    def _u(used, cap, name):
        if used is None or cap is None:
            return unknown("ingestion_state.bettor_live_probe_state",
                           "%s not present in the probe row" % name)
        return cell({"used": used, "limit": cap,
                     "remaining": max(0, cap - used),
                     "exhausted": used >= cap},
                    "ingestion_state.bettor_live_probe_state")
    http_used = sum(x for x in (probe.get("incentive_manifest_reserved"),
                                probe.get("incentive_recheck_reserved"),
                                probe.get("incentive_retry_reserved"))
                    if isinstance(x, int))
    http_cap = sum(x for x in (probe.get("max_incentive_manifest"),
                               probe.get("max_incentive_recheck"),
                               probe.get("max_incentive_retry"))
                   if isinstance(x, int)) or None
    return {
        "known": True,
        "http": _u(http_used, http_cap, "HTTP"),
        "socket_connect": _u(probe.get("socket_connect_reserved"),
                             probe.get("max_socket_connect"), "connects"),
        "socket_subscribe": _u(probe.get("socket_subscribe_reserved"),
                               probe.get("max_socket_subscribe"),
                               "subscribes"),
        "general_loop_zeroed": cell(
            probe.get("max_distinct") == 0,
            "ingestion_state.bettor_live_probe_state",
            note="the incentive arm zeroes the general discovery caps; a "
                 "non-zero max_distinct means this is NOT the incentive "
                 "arm's row"),
        "deadline_at": cell(probe.get("deadline_at"),
                            "ingestion_state.bettor_live_probe_state"),
    }


def _merge(intervals) -> list:
    """Union of half-open intervals, sorted and coalesced."""
    out = []
    for a, b in sorted((x for x in intervals if x[1] > x[0])):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [tuple(x) for x in out]


def _subtract(base, cuts) -> list:
    """`base` minus `cuts`, both unions of intervals."""
    out = []
    for a, b in base:
        pieces = [(a, b)]
        for ca, cb in cuts:
            nxt = []
            for pa, pb in pieces:
                if cb <= pa or ca >= pb:
                    nxt.append((pa, pb))
                    continue
                if ca > pa:
                    nxt.append((pa, ca))
                if cb < pb:
                    nxt.append((cb, pb))
            pieces = nxt
        out.extend(p for p in pieces if p[1] > p[0])
    return out


def _clip(intervals, a, b) -> list:
    return [(max(x, a), min(y, b)) for x, y in intervals
            if min(y, b) > max(x, a)]


def coverage_intervals(records, *, now, allowlist=None,
                       window_start=None, window_end=None) -> dict:
    """OBSERVED TIME AS A UNION OF INTERVALS. Not a subtraction.

    THE ARITHMETIC THIS REPLACES WAS WRONG IN TWO WAYS AT ONCE, and the
    two errors nearly cancelled, which is how it survived a reading.

      1. It treated everything before the LAST boot's start as
         unobserved. Boot A collected for thirty seconds before the
         process was replaced; that time was observed and was being
         thrown away.

      2. It then subtracted the PROCESS_REPLACED gap as well -- but
         that gap lies BETWEEN the two boots' segments, so it was
         never inside the observed set to begin with. Subtracting it
         removed time that had already been excluded.

    A union cannot make either mistake. Observed time is built up from
    the segments that exist, then the recorded gaps are removed by
    INTERSECTION, so a gap outside every segment contributes nothing
    and a gap inside one is removed exactly once.

    TWO DIFFERENT QUESTIONS, ANSWERED SEPARATELY:

      achieved   -- what has actually been observed, as of `now`.
      attainable -- achieved, plus the time still remaining to the
                    FIXED end, IF collection continues unbroken. It is
                    a ceiling and is labelled one; it is not a
                    forecast and nothing may be reported from it.

    AND VALIDITY IS NOT ACTIVITY. A market that sent nothing during a
    segment was still observed for that segment: the feed is
    change-driven and silence is the market, not the collector. So
    per-market observed time is the segment union, and frame counts are
    reported beside it as activity rather than instead of it.
    """
    a = _epoch(window_start or WINDOW_START)
    b = _epoch(window_end or WINDOW_END)
    recs = list(records or [])

    try:
        from .bettor_incentive_journal import segments as _segs
        segs = _segs(recs)
    except Exception:                                         # noqa: BLE001
        segs = []

    seg_iv = _merge([(float(s["start"]), float(s["end"])) for s in segs])

    # Gaps that carry BOTH ends. A GAP_OPENED with no close has no
    # measurable extent, so it cannot be subtracted -- it is reported
    # as an open gap and bounds nothing.
    gv = gap_view(recs)
    cuts = _merge([(float(g["from"]), float(g["to"]))
                   for g in gv["closed"]
                   if g.get("from") is not None and g.get("to") is not None])

    observed = _subtract(seg_iv, cuts)
    in_window = _clip(observed, a, b)
    observed_s = round(sum(y - x for x, y in in_window), 3)

    # The remaining time to the FIXED end, from the latest instant that
    # is actually covered -- not from `now`, because the stretch between
    # the last covered instant and now is not observed either.
    last_covered = max((y for _, y in in_window), default=a)
    remaining_s = round(max(0.0, b - max(last_covered, min(now, b))), 3)

    per_market = []
    for slug in sorted(allowlist or []):
        mine = _merge([(float(s["start"]), float(s["end"])) for s in segs
                       if slug in (s.get("slugs") or [])])
        # A market present in the allowlist but silent in a segment was
        # still watched for it. `segments()` only lists slugs that sent
        # something, so silence would otherwise read as absence.
        watched = seg_iv if slug in (allowlist or []) else mine
        m_obs = _clip(_subtract(watched, cuts), a, b)
        frames = sum(1 for r in recs if r.get("kind") == "LADDER"
                     and r.get("slug") == slug and a <= float(r.get("at") or 0) < b)
        last = max((float(r["at"]) for r in recs
                    if r.get("kind") == "LADDER" and r.get("slug") == slug),
                   default=None)
        per_market.append({
            "slug": slug,
            "observed_s": round(sum(y - x for x, y in m_obs), 3),
            "frames": frames,
            "last_frame": iso(last),
            "sent_in_segments": len(mine),
            "watched_but_silent": frames == 0,
        })

    return {
        "window": {"start": iso(a), "end": iso(b), "span_s": round(b - a, 3)},
        "segments": [{"start": iso(x), "end": iso(y),
                      "seconds": round(y - x, 3)} for x, y in seg_iv],
        "gaps_subtracted": [{"from": iso(x), "to": iso(y),
                             "seconds": round(y - x, 3)} for x, y in cuts],
        "gaps_outside_segments": [
            {"from": iso(x), "to": iso(y), "seconds": round(y - x, 3),
             "why": "lies between segments, so it was never inside the "
                    "observed set and is NOT subtracted again"}
            for x, y in cuts if not _clip(seg_iv, x, y)],
        "observed_intervals": [{"from": iso(x), "to": iso(y),
                                "seconds": round(y - x, 3)}
                               for x, y in in_window],
        "achieved": cell(
            {"observed_s": observed_s,
             "window_s": round(b - a, 3),
             "fraction_of_window": round(observed_s / (b - a), 6)
             if b > a else None,
             "elapsed_window_s": round(max(0.0, min(now, b) - a), 3),
             "fraction_of_elapsed": (
                 round(observed_s / max(1e-9, min(now, b) - a), 6)
                 if now > a else None)},
            "journal segments minus recorded gaps, unioned",
            as_of=iso(now),
            note="ACHIEVED. Union of observed intervals clipped to the "
                 "window; each recorded gap removed exactly once, and "
                 "only where it intersects a segment."),
        "attainable_at_fixed_end": cell(
            {"if_unbroken_from_now_s": round(observed_s + remaining_s, 3),
             "remaining_s": remaining_s,
             "fraction_of_window": round((observed_s + remaining_s) / (b - a), 6)
             if b > a else None},
            "achieved + time remaining to the fixed end",
            as_of=iso(now),
            note="A CEILING, NOT A FORECAST. It assumes collection "
                 "continues unbroken to 2026-09-24T04:00:00Z. Nothing "
                 "may be reported from it as though it had happened."),
        "per_market": per_market,
        "validity_is_not_activity": "observed_s is how long a market was "
                                    "WATCHED. frames is how often it "
                                    "CHANGED. A watched market with zero "
                                    "frames was quiet, not missing.",
    }


def market_coverage(ladders, allowlist) -> dict:
    """Which of the frozen markets actually produced depth.

    RECEIVING A FRAME AND PERSISTING DEPTH ARE DIFFERENT. A ladder
    record with no levels is a frame that arrived and told us nothing
    about the book, and it is counted apart.
    """
    seen, with_depth = {}, {}
    for r in ladders or []:
        s = r.get("slug")
        if not s:
            continue
        seen[s] = seen.get(s, 0) + 1
        p = r.get("payload") or {}
        levels = (p.get("bids") or []) + (p.get("offers") or [])
        if levels:
            with_depth[s] = with_depth.get(s, 0) + 1
    rows = []
    for s in sorted(allowlist or []):
        rows.append({"slug": s, "frames": seen.get(s, 0),
                     "frames_with_depth": with_depth.get(s, 0),
                     "receiving": s in seen,
                     "depth_persisted": s in with_depth})
    return {"allowlisted": len(allowlist or []),
            "receiving": sum(1 for r in rows if r["receiving"]),
            "with_depth": sum(1 for r in rows if r["depth_persisted"]),
            "markets": rows,
            "independence_note": "these markets share ONE programme and "
                                 "ONE event. They are not independent "
                                 "observations and no interval is computed "
                                 "as though they were."}


# ═══ VIEW 2: TEST AND RELEASE EVIDENCE ═══════════════════════════════

K_UNIT = "UNIT"
K_SIM = "SIMULATED_TRANSPORT"
K_REPLAY = "REPLAY"
K_VENUE = "REAL_VENUE_OBSERVATION"


def suite_row(*, name, kind, sha, environment, at, complete,
              passed=None, failed=None, skipped=None, failure_ids=None,
              superseded_by=None, artifact=None) -> dict:
    """One suite result. INCOMPLETE STAYS INCOMPLETE.

    A run that did not finish has no pass/fail counts to report -- the
    counts it printed before dying describe a prefix, not a result --
    so they are withheld rather than shown with a caveat nobody reads.
    """
    row = {"suite": name, "kind": kind, "tested_sha": sha,
           "environment": environment, "at": at, "complete": bool(complete),
           "artifact": artifact, "superseded_by": superseded_by}
    if not complete:
        row.update({"passed": unknown(artifact or name,
                                      "run did not complete"),
                    "failed": unknown(artifact or name,
                                      "run did not complete"),
                    "skipped": unknown(artifact or name,
                                       "run did not complete"),
                    "failure_ids": [],
                    "why_no_counts": "an incomplete run's counts describe "
                                     "a prefix, not a result"})
        return row
    row.update({"passed": cell(passed, artifact or name, as_of=at),
                "failed": cell(failed, artifact or name, as_of=at),
                "skipped": cell(skipped, artifact or name, as_of=at),
                "failure_ids": sorted(failure_ids or [])})
    return row


def baseline_compare(*, candidate, baseline) -> dict:
    """Failure-ID difference between two SHAs, with both SHAs shown.

    UNMATCHED FAILURES ARE LABELLED UNATTRIBUTED, not silently counted
    as regressions or as pre-existing. A failure present on the
    candidate and absent from the baseline is only a regression if the
    baseline run was COMPLETE and covered the same selection -- and
    when it was not, saying "unattributed" is the honest answer.
    """
    if not candidate.get("complete") or not baseline.get("complete"):
        return {"comparable": False,
                "candidate_sha": candidate.get("tested_sha"),
                "baseline_sha": baseline.get("tested_sha"),
                "why": "one or both runs are incomplete; no attribution "
                       "is possible from a partial run",
                "unattributed": sorted(set(candidate.get("failure_ids") or [])
                                       | set(baseline.get("failure_ids")
                                             or []))}
    c = set(candidate.get("failure_ids") or [])
    b = set(baseline.get("failure_ids") or [])
    same_selection = (candidate.get("selection") == baseline.get("selection"))
    return {"comparable": True,
            "candidate_sha": candidate.get("tested_sha"),
            "baseline_sha": baseline.get("tested_sha"),
            "same_selection": same_selection,
            "new_on_candidate": sorted(c - b),
            "fixed_on_candidate": sorted(b - c),
            "on_both": sorted(c & b),
            "unattributed": [] if same_selection else sorted(c ^ b),
            "note": ("both runs covered the same selection"
                     if same_selection else
                     "the two runs did NOT cover the same selection, so "
                     "every difference is UNATTRIBUTED rather than a "
                     "regression or a fix")}


# ═══ VIEW 3: ECONOMIC EVIDENCE ═══════════════════════════════════════

M_REALIZED = "REALIZED_TRADING_PNL"
M_REPLAY = "REPLAY_PNL"
M_HYPOTHETICAL = "HYPOTHETICAL_INCENTIVE_REWARD"
M_CONFIRMED = "VENUE_CONFIRMED_REWARD"


def economic_bucket(kind, *, value, source, as_of=None, denominator=None,
                    fees=None, residual=None, capital=None, note=None,
                    policy_version=None, dataset=None, events=None,
                    execution_assumptions=None, provenance=None) -> dict:
    """One economic figure, in exactly one bucket, with its denominator.

    THE FOUR BUCKETS NEVER SUM. Realized trading P&L, replay P&L, a
    hypothetical reward share and a venue-confirmed reward are four
    different kinds of claim, and a total across them would be a
    number with no referent. They are returned as a mapping rather
    than a list so nothing can quietly fold them together.
    """
    return {
        "kind": kind,
        "value": value if isinstance(value, dict) else cell(value, source,
                                                            as_of=as_of),
        "denominator": denominator or unknown(
            source, "no denominator recorded -- a return figure without "
                    "one is not interpretable"),
        "fees": fees or unknown(source, "not recorded"),
        "residual_inventory": residual or unknown(source, "not recorded"),
        "capital_committed": capital or unknown(source, "not recorded"),
        "policy_version": policy_version,
        "dataset": dataset,
        "events": events,
        "execution_assumptions": execution_assumptions,
        "provenance": provenance,
        "note": note,
    }


def economics(artifacts: dict) -> dict:
    """The four buckets, kept apart, from whatever artifacts exist."""
    ev = artifacts.get("evaluation") or {}
    opp = artifacts.get("opportunity") or {}
    out = {"buckets": {}, "as_of": {}}

    # 1. REALIZED. There is none: no order has ever been placed.
    out["buckets"][M_REALIZED] = economic_bucket(
        M_REALIZED, value=not_applicable(
            "no order path", "no funded order has been placed; "
            "max_contracts is 0.0 and the runtime adapter holds no order "
            "path"),
        source="no order path",
        note="ZERO IS NOT THE ANSWER HERE EITHER -- there is no trading "
             "history, which is different from a history that netted zero")

    # 2. REPLAY. Development diagnostic, never out-of-sample.
    if ev:
        rows = ev.get("eval") or []
        prov = ev.get("provenance") or {}
        out["buckets"][M_REPLAY] = economic_bucket(
            M_REPLAY,
            value=cell([{"qfrac": r.get("qfrac"), "net_usd": r.get("net_usd")}
                        for r in rows],
                       "acceptance/evaluation.json", as_of=ev.get("cut")),
            source="acceptance/evaluation.json",
            denominator=cell(
                [{"qfrac": r.get("qfrac"),
                  "capital_hours": r.get("capital_hours"),
                  "per_capital_hour": r.get("per_capital_hour")}
                 for r in rows], "acceptance/evaluation.json"),
            fees=cell([{"qfrac": r.get("qfrac"),
                        "taker_fees_usd": r.get("taker_fees_usd"),
                        "rebates_usd": r.get("rebates_usd")} for r in rows],
                      "acceptance/evaluation.json"),
            residual=unknown("acceptance/evaluation.json",
                             "residual exposure not carried on these rows"),
            capital=cell([{"qfrac": r.get("qfrac"),
                           "capital_hours": r.get("capital_hours")}
                          for r in rows], "acceptance/evaluation.json"),
            policy_version=ev.get("selected"),
            dataset=ev.get("cut"),
            events=cell(rows[0].get("events") if rows else None,
                        "acceptance/evaluation.json",
                        note="EVENT clusters, not episodes"),
            execution_assumptions="tape-backed replay; SIMULATED, not "
                                  "account performance",
            provenance={
                "independent_holdout": prov.get("independent_holdout", False),
                "status": prov.get("status_of_every_figure_here")
                          or "DEVELOPMENT DIAGNOSTIC",
                "why": prov.get("why")},
            note="qualifies=%s. Development diagnostic; these dates were "
                 "inspected before the protocol existed."
                 % ev.get("qualifies"))
    else:
        out["buckets"][M_REPLAY] = economic_bucket(
            M_REPLAY, value=unknown("acceptance/evaluation.json",
                                    "artifact not present"),
            source="acceptance/evaluation.json")

    # 3. HYPOTHETICAL reward share.
    out["buckets"][M_HYPOTHETICAL] = economic_bucket(
        M_HYPOTHETICAL,
        value=cell(opp.get("rows"), "acceptance/incentive_opportunity.json",
                   as_of=opp.get("terms_source"))
        if opp else unknown("acceptance/incentive_opportunity.json",
                            "not yet computed for this run"),
        source="acceptance/incentive_opportunity.json",
        note=(opp.get("status") if opp else None)
             or "a clip INSERTED into an observed book. Changes the "
                "denominator, not other people's behaviour.",
        execution_assumptions="counterfactual on a public ladder; no order "
                              "was placed and no reward was earned")

    # 4. VENUE-CONFIRMED reward.
    out["buckets"][M_CONFIRMED] = economic_bucket(
        M_CONFIRMED,
        value=unknown("/v1/incentives/earnings (authenticated)",
                      "never read; no reward has been credited or claimed"),
        source="/v1/incentives/earnings (authenticated)",
        note="the only bucket that would constitute EARNED money. It is "
             "empty, and the hypothetical bucket above is not a proxy "
             "for it.")
    return out


# ═══ VIEW 4: CAPABILITY TRACEABILITY ═════════════════════════════════

def capability_row(*, capability, implementation, evidence_level,
                   strongest_evidence, case_study, venue_difference,
                   note=None) -> dict:
    return {"capability": capability, "implementation": implementation,
            "evidence": evidence_level,
            "strongest_evidence": strongest_evidence,
            "case_study_mechanism": case_study,
            "venue_difference": venue_difference, "note": note}


def capabilities() -> list:
    """Each promised capability against what actually exists.

    OBSERVATION NEVER VALIDATES TRADING PERFORMANCE and the ladder of
    evidence levels is the way that is enforced: LIVE_OBSERVED is a
    rung BELOW LIVE_EXECUTION_VALIDATED, and nothing reaches the top
    rung without our own fills.
    """
    return [
        capability_row(
            capability="ENTRY / ADMISSION",
            implementation="bettor_policy.admit() + flow_cover + vol_cover",
            evidence_level=E_REPLAY,
            strongest_evidence="420 replay episodes; admission decisions "
                               "also exercised on live ladders via "
                               "bettor_policy_runtime",
            case_study="two-sided market making -- quote both sides, earn "
                       "the spread, carry no direction",
            venue_difference="queue position is not observable at this "
                             "venue; the replay sweeps a queue-ahead "
                             "fraction instead of knowing one"),
        capability_row(
            capability="QUOTE PLACEMENT",
            implementation="bettor_policy.quote_prices()",
            evidence_level=E_REPLAY,
            strongest_evidence="replay across four queue scenarios",
            case_study="passive liquidity provision at the touch",
            venue_difference="aggregate ladder quantity is published, "
                             "per-order queues are not"),
        capability_row(
            capability="SIZING",
            implementation="bettor_policy.quote_size() -- EDGE_SCALED",
            evidence_level=E_IMPLEMENTED,
            strongest_evidence="measured in acceptance/strategy_v2.json; "
                               "net moved BOTH ways (+14.20 to -18.08)",
            case_study=None,
            venue_difference="banker's rounding is PER FILL, so order size "
                             "does not determine the rebate -- fill piece "
                             "size does, and we do not control it",
            note="implemented and NOT validated as an improvement"),
        capability_row(
            capability="INVENTORY MANAGEMENT",
            implementation="bettor_policy.on_fill() + "
                           "inventory_cap_breached()",
            evidence_level=E_REPLAY,
            strongest_evidence="partial fills are the normal case in the "
                               "corpus, not the exception",
            case_study="inventory control -- what separates market making "
                       "from position taking",
            venue_difference=None),
        capability_row(
            capability="COMPLETION / PAIRING",
            implementation="bettor_policy.recovery_action() COMPLETE_PAIR",
            evidence_level=E_REPLAY,
            strongest_evidence="60 of 60 filled episodes reached "
                               "FLAT_PAIRED under COMPLETE_PAIR",
            case_study="pair completion",
            venue_difference="completing as a taker costs Th_taker/"
                             "|Th_maker| = 4.8x (JUL2026) to 5.6x "
                             "(SEP2026) what resting earned"),
        capability_row(
            capability="LOSS-TAKING / EXIT",
            implementation="recovery_action() REST_EXIT / EXIT_TAKER",
            evidence_level=E_IMPLEMENTED,
            strongest_evidence="the corpus contains no material "
                               "loss-taking event, so this path is "
                               "exercised by LABELLED SCENARIO only",
            case_study="loss-taking discipline",
            venue_difference=None,
            note="scenario-exercised, not corpus-evidenced"),
        capability_row(
            capability="HOLDING",
            implementation="recovery_action() HOLD; release_action() HOLD",
            evidence_level=E_REPLAY,
            strongest_evidence="a matched pair pays exactly 1.00 at "
                               "settlement",
            case_study="hold to settlement",
            venue_difference=None),
        capability_row(
            capability="SETTLEMENT",
            implementation="bettor_settlement_ingest",
            evidence_level=E_REPLAY,
            strongest_evidence="settlement labels attached to replay "
                               "episodes from the captured tape",
            case_study="settlement at par",
            venue_difference=None),
        capability_row(
            capability="CAPITAL REUSE / NETTING",
            implementation="release_action() -- sell both legs back",
            evidence_level=E_IMPLEMENTED,
            strongest_evidence="fired twice in 470 episodes, cost $1.75, "
                               "freed 26 of 3,696 held capital-hours",
            case_study="capital recycling assumes a merge/netting call",
            venue_difference="NO MERGE CALL HAS BEEN DEMONSTRATED at this "
                             "venue. Selling both legs back is the "
                             "feasible alternative and it pays the spread "
                             "for capital a merge would return whole.",
            note="not the binding constraint"),
        capability_row(
            capability="FILL PROBABILITY (P_FILL)",
            implementation="bettor_p_fill -- returns NOT_IDENTIFIED",
            evidence_level=E_ABSENT,
            strongest_evidence="none. The engine refuses to score "
                               "MAKE_YES/MAKE_NO without it.",
            case_study="every maker strategy assumes one",
            venue_difference="a public feed shows the book, never our "
                             "order in it. Observation CANNOT supply "
                             "this; only resting our own orders can.",
            note="UNRESOLVED EXECUTION-MODEL REQUIREMENT"),
    ]


# ═══ VIEW 5: MANAGEMENT OVERVIEW ═════════════════════════════════════

def management(*, live, suites, econ, caps, now) -> dict:
    """One page. SYSTEM HEALTH AND PROFITABILITY, KEPT APART.

    The single most dangerous sentence a management page can imply is
    "it is running, therefore it is working". Operation and economics
    are two separate blocks here and neither one's status is allowed to
    colour the other's.
    """
    lc = (live or {}).get("lifecycle") or {}
    cov = (live or {}).get("coverage") or {}
    per = (live or {}).get("periods") or {}
    health = (live or {}).get("health") or {}

    complete = [s for s in (suites or []) if s.get("complete")]
    latest = max(complete, key=lambda s: s.get("at") or "", default=None)

    blockers = []
    if any(c["capability"].startswith("FILL PROBABILITY")
           and c["evidence"] == E_ABSENT for c in caps or []):
        blockers.append({
            "blocker": "P_FILL IS UNIDENTIFIED",
            "consequence": "the decision engine refuses to score any maker "
                           "action, so no order can be sized. This is an "
                           "unresolved execution-model requirement, not a "
                           "tuning problem.",
            "resolvable_by": "resting our own orders and observing fills. "
                             "A public feed shows the book, never our order "
                             "in it -- OBSERVATION CANNOT RESOLVE IT.",
            "source": "bettor_p_fill -> NOT_IDENTIFIED"})
    if lc.get("state") in (L_ARMED_NO_FRAMES, L_INTERRUPTED, L_FAILED):
        blockers.append({
            "blocker": "COLLECTION IS NOT PRODUCING FRAMES (%s)"
                       % lc.get("state"),
            "consequence": "the window's coverage is accruing gaps",
            "resolvable_by": "diagnosis within the remaining allowance",
            "source": "journal"})
    if (econ or {}).get("buckets", {}).get(M_CONFIRMED, {}) \
            .get("value", {}).get("status") == UNKNOWN:
        blockers.append({
            "blocker": "NO VENUE-CONFIRMED REWARD EXISTS",
            "consequence": "every reward figure on this system is "
                           "hypothetical. Nothing has been earned.",
            "resolvable_by": "a funded, separately authorized order "
                             "experiment",
            "source": "/v1/incentives/earnings -- never read"})

    return {
        "as_of": iso(now),
        "generated_at": iso(now),
        "system_health": {
            "heading": "IS IT RUNNING",
            "state": cell(lc.get("state"), "journal + control row",
                          as_of=iso(now), note=lc.get("why")),
            "connection": cell(health.get("verdict"), "journal EPOCH/GAP",
                               as_of=iso(now), note=health.get("why")),
            "markets_with_depth": cell(
                "%s of %s" % (cov.get("with_depth"), cov.get("allowlisted")),
                "journal LADDER vs frozen manifest", as_of=iso(now)),
            "measurement_frames": cell(
                (per.get("measurement") or {}).get("frames"),
                "journal LADDER inside [window)", as_of=iso(now)),
            "caveat": "THIS BLOCK SAYS NOTHING ABOUT MONEY. A healthy "
                      "collector is a healthy collector.",
        },
        "economic_qualification": {
            "heading": "HAS IT MADE MONEY",
            "status": cell("NOT QUALIFIED", "bettor_command_center",
                           as_of=iso(now),
                           note="no funded order has ever been placed, so "
                                "there is no realized trading P&L to "
                                "qualify. Replay results are development "
                                "diagnostics and reward figures are "
                                "hypothetical."),
            "realized_trading_pnl": (econ or {}).get("buckets", {})
                                    .get(M_REALIZED, {}).get("value"),
            "venue_confirmed_reward": (econ or {}).get("buckets", {})
                                      .get(M_CONFIRMED, {}).get("value"),
            "caveat": "OBSERVATION DOES NOT VALIDATE TRADING PERFORMANCE. "
                      "Watching a book is not trading it.",
        },
        "latest_verified_evidence": (
            {"suite": latest.get("suite"), "kind": latest.get("kind"),
             "tested_sha": latest.get("tested_sha"),
             "at": latest.get("at"),
             "environment": latest.get("environment"),
             "passed": latest.get("passed"), "failed": latest.get("failed")}
            if latest else
            {"why": "no COMPLETE suite result is available",
             "source": "acceptance artifacts"}),
        "blockers": blockers,
        "next_action": _next_action(lc.get("state"), now),
    }


def _next_action(state, now) -> dict:
    end = _epoch(WINDOW_END)
    if state == L_COLLECTING:
        return {"action": "let the window run and stop at the fixed end",
                "at": iso(end),
                "why": "the run is persisting frames; no intervention is "
                       "owed until %s" % WINDOW_END}
    if state in (L_ARMED, L_SCHEDULED):
        return {"action": "start observation at the window boundary",
                "at": WINDOW_START,
                "why": "armed is not started; the control is a separate flag"}
    if state == L_ARMED_NO_FRAMES:
        return {"action": "diagnose the feed WITHIN the remaining allowance",
                "at": iso(now),
                "why": "the control is on and nothing is persisted"}
    if state in (L_COMPLETED, L_STOPPED):
        return {"action": "analyse coverage, then decide on the funded "
                          "execution experiment",
                "at": iso(now),
                "why": "collection is over; the P_FILL question is next and "
                       "it needs separate authorization"}
    if state == L_FAILED:
        return {"action": "read the RUN_ERROR record and preserve the "
                          "partial evidence",
                "at": iso(now), "why": "the run failed"}
    return {"action": "establish the run's actual state from the journal",
            "at": iso(now), "why": "state is %s" % state}


# ═══ ASSEMBLY ════════════════════════════════════════════════════════

def build(*, records, control, probe, run_row, allowlist, suites,
          artifacts, deployed, now, window_start=None,
          window_end=None) -> dict:
    """The five views, from evidence already read. PURE.

    Everything that touches a socket, a pool or a disk happens in the
    caller. This function is given records and returns views, which is
    what makes every state on the page reachable in a test.
    """
    ladders = [r for r in records or [] if r.get("kind") == "LADDER"]
    gaps = gap_view(records)
    run_close = sorted([r for r in records or []
                        if r.get("kind") == "RUN_CLOSE"],
                       key=lambda r: float(r.get("at") or 0.0))
    run_open = [r for r in records or [] if r.get("kind") == "RUN_OPEN"]

    lc = lifecycle(control=control, probe=probe, ladders=ladders,
                   gaps=gaps["open"], errors=gaps["errors"],
                   # THE LATEST close, not the first: an early boot's
                   # close says nothing about whether a later boot is
                   # still running.
                   run_close=run_close[-1] if run_close else None,
                   now=now, window_end=window_end)
    periods = split_periods(ladders, window_start=window_start,
                            window_end=window_end)
    health = connection_health(records, now=now, control=control)
    cov = market_coverage(
        [r for r in ladders
         if _epoch(window_start or WINDOW_START) <= float(r.get("at") or 0.0)
         < _epoch(window_end or WINDOW_END)],
        allowlist)
    early_cov = market_coverage(
        [r for r in ladders
         if float(r.get("at") or 0.0) < _epoch(window_start or WINDOW_START)],
        allowlist)

    boots = sorted({r.get("boot_id") for r in records or [] if r.get("boot_id")})
    first_open = (run_open[0].get("payload") or {}) if run_open else {}

    live = {
        "lifecycle": lc,
        "identity": {
            "deployed_sha": cell(deployed.get("sha"), deployed.get("source")
                                 or "deployment probe")
            if deployed.get("sha") else
            unknown(deployed.get("source") or "deployment probe",
                    deployed.get("why") or "the running SHA was not read"),
            "run_id": cell((run_row or {}).get("run_id"),
                           "ingestion_state.bettor_incentive_run")
            if run_row else unknown("ingestion_state.bettor_incentive_run",
                                    "no run row"),
            "manifest_id": cell((run_row or {}).get("manifest_id"),
                                "ingestion_state.bettor_incentive_run")
            if run_row else unknown("ingestion_state.bettor_incentive_run",
                                    "no run row"),
            "et_date": cell((run_row or {}).get("et_date"),
                            "ingestion_state.bettor_incentive_run")
            if run_row else unknown("ingestion_state.bettor_incentive_run",
                                    "no run row"),
            "probe_id": cell((probe or {}).get("probe_id"),
                             "ingestion_state.bettor_live_probe_state")
            if probe else unknown("ingestion_state.bettor_live_probe_state",
                                  "nothing armed"),
            "boots": cell(boots, "journal boot_id column",
                          note="ONE boot id per process. More than one "
                               "means the collector was replaced, and a "
                               "BOOT_GAP was written for the interval."),
            "boot_count": cell(len(boots), "journal boot_id column"),
            "journal_run_open": cell(
                {"allowlist": len(first_open.get("allowlist") or []),
                 "window": first_open.get("window")},
                "journal RUN_OPEN") if run_open else
            unknown("journal RUN_OPEN", "the run has not opened a journal"),
        },
        "schedule": {
            "scheduled_start": cell(WINDOW_START, "frozen window",
                                    note="the ET-date boundary, fixed"),
            "actual_start": (
                cell(iso(min(float(r["at"]) for r in ladders)),
                     "journal: earliest LADDER")
                if ladders else
                unknown("journal LADDER",
                        "no frame has been persisted, so there is no "
                        "actual start")),
            "fixed_end": cell(WINDOW_END, "frozen window",
                              note="no extension and no replacement run"),
            "control_state": cell(bool(control),
                                  "ingestion_state.bettor_live_observation",
                                  as_of=iso(now),
                                  note="A TRUE FLAG IS NOT COLLECTION. See "
                                       "lifecycle."),
        },
        "frames": {
            "first_persisted": (cell(iso(min(float(r["at"]) for r in ladders)),
                                     "journal LADDER")
                                if ladders else
                                unknown("journal LADDER", "none persisted")),
            "latest_persisted": (cell(iso(max(float(r["at"]) for r in ladders)),
                                      "journal LADDER")
                                 if ladders else
                                 unknown("journal LADDER", "none persisted")),
            "total": cell(len(ladders), "journal LADDER"),
        },
        "periods": periods,
        "coverage": cov,
        "early_coverage": dict(early_cov,
                               note="OPERATIONAL EVIDENCE ONLY. These frames "
                                    "are outside the measurement window and "
                                    "are excluded from every reward figure."),
        "health": health,
        "coverage_time": coverage_intervals(
            records, now=now, allowlist=allowlist,
            window_start=window_start, window_end=window_end),
        "gaps": gaps,
        "allowance": allowance(probe),
        "segments": _segments_safe(records),
    }

    econ = economics(artifacts or {})
    caps = capabilities()
    rows = suites or []

    return {
        "version": VERSION,
        "as_of": iso(now),
        "views": {
            "live_operation": live,
            "test_evidence": {
                "suites": rows,
                "kinds": {K_UNIT: "code under test, in-process",
                          K_SIM: "a transport double -- NOT the venue",
                          K_REPLAY: "recorded venue tape, replayed",
                          K_VENUE: "the real venue, observed live"},
                "note": "a SIMULATED_TRANSPORT pass is not a venue result, "
                        "and no count here describes the observation run.",
            },
            "economics": econ,
            "capabilities": caps,
            "management": management(live=live, suites=rows, econ=econ,
                                     caps=caps, now=now),
        },
        "refusals": describe()["refusals"],
    }


def _segments_safe(records) -> dict:
    try:
        from .bettor_incentive_journal import segments as _segs
        segs = _segs(records or [])
    except Exception as exc:                                  # noqa: BLE001
        return {"known": False,
                "why": "segments unavailable: %s" % type(exc).__name__}
    return {"known": True, "n": len(segs),
            "segments": [{"boot_id": s["boot_id"], "epoch": s["epoch"],
                          "start": iso(s["start"]), "end": iso(s["end"]),
                          "ladders": s["ladders"],
                          "slugs": len(s["slugs"])} for s in segs],
            "end_basis": "the last record of the boot before its next "
                         "epoch -- NOT the last ladder, because a quiet "
                         "book sends nothing"}


def describe() -> dict:
    return {
        "version": VERSION,
        "reads_only": True,
        "refusals": [
            "a true control flag never implies persisted frames",
            "a quiet book never implies a broken feed",
            "a missing measurement renders UNKNOWN, never 0",
            "an incomplete test run reports no counts",
            "the four economic buckets never sum",
            "observation never reaches LIVE_EXECUTION_VALIDATED",
        ],
        "lifecycle_states": [L_SCHEDULED, L_ARMED, L_ARMED_NO_FRAMES,
                             L_COLLECTING, L_INTERRUPTED, L_STOPPED,
                             L_COMPLETED, L_FAILED],
        "economic_buckets": [M_REALIZED, M_REPLAY, M_HYPOTHETICAL,
                             M_CONFIRMED],
        "evidence_levels": [E_ABSENT, E_IMPLEMENTED, E_REPLAY,
                            E_LIVE_OBSERVED, E_LIVE_EXECUTED],
    }

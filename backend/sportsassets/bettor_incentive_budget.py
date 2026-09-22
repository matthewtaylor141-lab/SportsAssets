"""The outbound-request allowance for one incentive observation run.

EIGHT REQUESTS, AND EVERY OUTBOUND REQUEST IS ONE OF THEM. The earlier
budget language counted "calls" and left retries, pagination pages and
periodic rechecks outside the number, which is how a declared cap of
eight describes a run that issued thirty. Here the unit is THE OUTBOUND
HTTP REQUEST: a retry costs a unit, a second page costs a unit, and a
refused reservation is recorded rather than silently skipped.

WHAT THIS EXISTS TO PREVENT, concretely. The general worker's startup
round spends SIX LISTING PAGES and up to 1,680 BBO reads before it
subscribes to anything -- roughly 1,686 requests, documented in
`bettor_live_loop` as the reason its no-start hold begins at five
minutes rather than five seconds. An eight-request budget is not a
smaller version of that path; it is a DIFFERENT path, and the two
cannot both be live. So the acquisition kinds are not merely unused
here: `spend()` REFUSES them by name. A code path that tries to list
markets or read a BBO during this experiment fails loudly at the
reservation instead of quietly spending the venue's patience.

THE SUB-CAPS ARE PART OF THE CAP, not advice beside it:

    manifest   <=4   the incentive discovery read, INCLUDING every
                     pagination page. Programme terms arrive inside
                     this response (`timePeriods[]`), so there is no
                     per-market terms call to budget for.
    recheck    <=2   mid-run re-read to detect programme drift
    retry      <=2   held back for transport failures ANYWHERE

    total      <=8   AND 4 + 2 + 2 IS THE TOTAL. The sub-caps sum to
                     it exactly, so enforcing each one enforces the
                     total: no combination of grants reaches nine
                     without some kind exceeding its own cap. That is
                     why no cross-kind bookkeeping is added to
                     `reserve()`, which the general loop also uses.

A FIRST ATTEMPT IS CHARGED TO ITS OWN KIND; A RETRY IS CHARGED TO
`retry`. That way "how many distinct questions did we ask" and "how
much did failure cost us" are separate numbers, and exhausting the
retry reserve cannot quietly eat the recheck allowance.

WHERE THE COUNT LIVES. Not here. Every unit is reserved by
`bettor_live_control.reserve()` in one locked Postgres transaction
BEFORE the request is dispatched -- see `DurableLedger`. The caps
themselves come from the armed allowance row, so the numbers in an
approval are the numbers being enforced.

Run:  python -m pytest backend/tests/test_bettor_incentive_budget.py
"""
from __future__ import annotations

import os
import threading

BUDGET_VERSION = "BETTOR_INCENTIVE_BUDGET_V1"

TOTAL_CAP = 8

K_MANIFEST = "manifest"
K_RECHECK = "recheck"
K_RETRY = "retry"

SUBCAPS = {K_MANIFEST: 4, K_RECHECK: 2, K_RETRY: 2}

# THE ACQUISITION KINDS THE GENERAL WORKER USES, REFUSED BY NAME.
# Listing a universe, enriching it with BBO reads and polling
# settlement are all legitimate elsewhere; during this experiment they
# are defects, and naming them here turns a defect into a refusal with
# a reason attached.
FORBIDDEN = {
    "listing": "market listing is disabled in incentive observation mode; "
               "the universe comes from the frozen manifest",
    "distinct": "BBO enrichment is disabled in incentive observation mode",
    "bbo_attempt": "BBO enrichment is disabled in incentive observation mode",
    "settlement": "settlement polling is disabled in incentive observation "
                  "mode; this run ends at the ET date boundary",
}

GRANTED = "GRANTED"
R_TOTAL = "REFUSED_TOTAL_CAP"
R_SUBCAP = "REFUSED_SUBCAP"
R_FORBIDDEN = "REFUSED_FORBIDDEN_KIND"
R_UNKNOWN = "REFUSED_UNKNOWN_KIND"

# ── THE SOCKET KINDS, DELIBERATELY OUTSIDE THE EIGHT ─────────────────
#
# A connect and a subscribe are not HTTP requests and must not inflate
# the eight-request HTTP ceiling: mixing them would let a reconnect
# storm consume the manifest allowance, and would make "eight public
# requests" describe a run that made three. They are reserved through
# the SAME durable mechanism, at the connect/subscribe boundary, under
# their OWN caps in the same row.
K_SOCK_CONNECT = "socket_connect"
K_SOCK_SUBSCRIBE = "socket_subscribe"

SOCKET_CAPS = {K_SOCK_CONNECT: 20, K_SOCK_SUBSCRIBE: 40}

# OUR KIND NAMES -> THE RESERVATION KINDS THE CONTROL MODULE KNOWS.
# Two vocabularies, mapped in one place, so the allowance row's keys
# and this module's names can never drift apart silently.
CTL_KIND = {K_MANIFEST: "incentive_manifest",
            K_RECHECK: "incentive_recheck",
            K_RETRY: "incentive_retry",
            K_SOCK_CONNECT: "socket_connect",
            K_SOCK_SUBSCRIBE: "socket_subscribe"}

# Every kind this ledger will reserve, HTTP and socket together. The
# two families keep separate totals in `report()`.
ALL_CAPS = dict(SUBCAPS, **SOCKET_CAPS)

# NO ENVIRONMENT VARIABLE SETS THE CAP ANY MORE. It used to, and that
# was the wrong home for it: an allowance held in the process is
# re-granted on every restart. The caps are written into the allowance
# row when the probe is ARMED, by the operator, which means the numbers
# approved are the numbers enforced and a restart cannot widen them.


class DurableLedger:
    """Every outbound request, RESERVED IN POSTGRES BEFORE DISPATCH.

    WHAT THIS REPLACES, AND WHY THE FIRST VERSION WAS NOT A CEILING.
    The first version kept the count in memory and seeded it from a
    database row at boot. That is not a bound, and three ordinary
    events break it:

      A CRASH BETWEEN RESERVATION AND DISPATCH   the in-memory
        increment dies with the process, so the next boot seeds one
        low and the request can be made twice.
      A CRASH AFTER DISPATCH                     same increment, same
        loss, except the request definitely went out.
      TWO OVERLAPPING WORKERS                    both seed from the
        same number, neither sees the other, and eight becomes sixteen.
        The supervisor makes this ordinary rather than exotic: a slow
        shutdown and a fast restart overlap by construction.

    So nothing is counted here at all. Every unit is taken by
    `bettor_live_control.reserve()`, which was built for exactly this
    and is reused rather than reimplemented:

      * it takes `SELECT ... FOR UPDATE` on the allowance row, so two
        processes SERIALIZE and cannot both read the same `used`;
      * it reads the CONTROL and the DEADLINE inside that same
        transaction, so a stop landing mid-run refuses the next
        request rather than being noticed a poll later;
      * it increments BEFORE returning, so a grant means the unit is
        already durable when the caller dispatches;
      * it is tied to `probe_id`, so a process that outlived its probe
        answers RESERVATION_PROBE_MISMATCH and spends nothing;
      * a timeout or error answers NOT GRANTED and the caller does not
        dispatch -- losing allowance is the safe direction.

    THE COUNTERS ARE THE ROW'S. This object holds no total of its own;
    `report()` reflects grants it OBSERVED, which is a log, not the
    enforcement. The enforcement is the row.
    """

    def __init__(self, pool, *, probe_id: str | None) -> None:
        self.pool = pool
        self.probe_id = probe_id
        self.observed: dict[str, int] = {k: 0 for k in ALL_CAPS}
        self.refused: list[dict] = []
        self.log: list[dict] = []
        self._lock = threading.Lock()

    async def spend(self, kind: str, *, why: str = "") -> dict:
        """Reserve ONE outbound request durably, or refuse with a reason."""
        from . import bettor_live_control as ctl
        if kind in FORBIDDEN:
            return self._refuse(kind, R_FORBIDDEN, FORBIDDEN[kind])
        if kind not in CTL_KIND:
            return self._refuse(kind, R_UNKNOWN,
                                "no sub-cap declares %r" % kind)
        r = await ctl.reserve(self.pool, CTL_KIND[kind],
                              probe_id=self.probe_id)
        if not ctl.granted(r):
            return self._refuse(kind, r.get("why") or R_TOTAL,
                                r.get("detail"), reserved=r.get("reserved"),
                                cap=r.get("cap"))
        with self._lock:
            self.observed[kind] += 1
            rec = {"kind": kind, "why": why, "verdict": GRANTED,
                   "durable": True, "reserved": r.get("reserved"),
                   "cap": r.get("cap"), "remaining": r.get("remaining")}
            self.log.append(rec)
        return {"ok": True, **rec}

    def _refuse(self, kind, verdict, detail, **kw) -> dict:
        rec = {"kind": kind, "verdict": verdict, "detail": detail,
               "durable": True, **kw}
        with self._lock:
            self.refused.append(rec)
            self.log.append(rec)
        return {"ok": False, **rec}

    async def read(self) -> dict:
        """The AUTHORITATIVE spend, read from the ROW, not from memory.

        Reported beside `grants_observed_this_boot` precisely so the
        two can DISAGREE in the open: after a restart the row is high
        and this boot's observations start at zero, and that difference
        is the evidence the ceiling survived the restart.
        """
        from . import bettor_live_control as ctl
        import json as _json
        out = {"row_readable": False, "reserved": {}, "caps": {},
               "total_reserved": None}
        try:
            p = self.pool
            if p is None:
                from .db import get_pool
                p = await get_pool()
            raw = await p.fetchval(
                "SELECT value FROM ingestion_state WHERE key=$1",
                ctl.BUDGET_KEY)
        except Exception as exc:                # noqa: BLE001
            return dict(out, detail="read failed: %s" % type(exc).__name__)
        if raw is None:
            return dict(out, detail="no allowance row")
        v = _json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        if not isinstance(v, dict):
            return dict(out, detail="allowance value is not an object")
        def _pull(names):
            r = {k: int(v.get(ctl._COUNTER[CTL_KIND[k]], 0) or 0)
                 for k in names}
            c = {k: int(v.get(ctl._CAP[CTL_KIND[k]], names[k]) or 0)
                 for k in names}
            return r, c
        res, caps = _pull(SUBCAPS)
        sres, scaps = _pull(SOCKET_CAPS)
        return {"row_readable": True, "reserved": res, "caps": caps,
                # THE HTTP TOTAL, and only the HTTP total. Socket units
                # are reported beside it, never inside it.
                "total_reserved": sum(res.values()),
                "total_cap": sum(caps.values()),
                "socket_reserved": sres, "socket_caps": scaps,
                "probe_id": v.get("probe_id"),
                "deadline_at": v.get("deadline_at")}

    def report(self) -> dict:
        with self._lock:
            return {
                "budget": BUDGET_VERSION,
                "enforcement": "bettor_live_control.reserve -- one row "
                               "lock per request, committed BEFORE "
                               "dispatch",
                "http_declared_total": TOTAL_CAP,
                "http_subcaps": dict(SUBCAPS),
                "subcaps_sum_to_total": sum(SUBCAPS.values()) == TOTAL_CAP,
                "socket_caps": dict(SOCKET_CAPS),
                "socket_is_not_http": "a connect and a subscribe are not "
                                      "HTTP requests and do not count "
                                      "against the eight",
                "grants_observed_this_boot": dict(self.observed),
                "http_observed_this_boot": sum(
                    v for k, v in self.observed.items() if k in SUBCAPS),
                "socket_observed_this_boot": sum(
                    v for k, v in self.observed.items() if k in SOCKET_CAPS),
                "authoritative_count": "the allowance row, not this object",
                "refusals": len(self.refused),
                "refused_by_verdict": _tally(self.refused, "verdict"),
                "refused_detail": list(self.refused),
                "forbidden_kinds": sorted(FORBIDDEN),
            }


def _tally(rows, field) -> dict:
    out: dict = {}
    for r in rows:
        out[r.get(field)] = out.get(r.get(field), 0) + 1
    return out


# ── reconnects and resubscriptions are DIFFERENT bounds ──────────────
#
# One reconnect resubscribes every slug, so the two counts cannot be
# the same number and a single "connection attempts" bound would be
# whichever of the two it happened to be named after. A flapping socket
# and a subscribe loop are different failures with different costs, and
# each gets its own ceiling and its own exhaustion reason.

MAX_RECONNECTS = 20
MAX_RESUBSCRIBES = 40
RECONNECT_ENV = "BETTOR_INCENTIVE_MAX_RECONNECTS"
RESUBSCRIBE_ENV = "BETTOR_INCENTIVE_MAX_RESUBSCRIBES"

RC_OK = "WITHIN_BOUNDS"
RC_RECONNECTS = "RECONNECT_BOUND_REACHED"
RC_RESUBSCRIBES = "RESUBSCRIBE_BOUND_REACHED"


class ReconnectBounds:
    """Two independent ceilings, and they are PER RUN, not per boot.

    PER BOOT WOULD NOT BE A BOUND. The stream reconnects with a 1.0 s
    backoff doubling to a 30 s cap and does so forever; that is right
    for a service and wrong for a bounded experiment. But bounding it
    per PROCESS leaves the obvious hole open: a worker that crashes
    after twenty reconnects is restarted by the supervisor five seconds
    later with a fresh counter, and "at most twenty reconnects"
    describes a run that made two hundred. So the totals are carried in
    the durable run row and this object is SEEDED from them; `check()`
    compares prior + this boot against the ceiling.

    TWO CEILINGS, BECAUSE ONE RECONNECT RESUBSCRIBES EVERY SLUG. The
    two counts cannot be the same number, and a single bound would
    silently be whichever of the two it was named after. A flapping
    socket and a subscribe storm are different failures with different
    costs.
    """

    def __init__(self, *, max_reconnects: int | None = None,
                 max_resubscribes: int | None = None,
                 prior_reconnects: int = 0,
                 prior_resubscribes: int = 0) -> None:
        self.max_reconnects = _int_env(RECONNECT_ENV, MAX_RECONNECTS) \
            if max_reconnects is None else int(max_reconnects)
        self.max_resubscribes = _int_env(RESUBSCRIBE_ENV, MAX_RESUBSCRIBES) \
            if max_resubscribes is None else int(max_resubscribes)
        # WHAT EARLIER BOOTS OF THIS RUN ALREADY SPENT.
        self.prior_reconnects = max(0, int(prior_reconnects))
        self.prior_resubscribes = max(0, int(prior_resubscribes))
        self.resubscribes = 0               # this boot only

    def note_resubscribe(self, n: int = 1) -> None:
        self.resubscribes += int(n)

    def totals(self, reconnects: int) -> dict:
        """Run totals: what earlier boots spent, plus this boot's."""
        return {"reconnects": self.prior_reconnects + max(0, int(reconnects)),
                "resubscribes": self.prior_resubscribes + self.resubscribes,
                "this_boot": {"reconnects": max(0, int(reconnects)),
                              "resubscribes": self.resubscribes},
                "prior_boots": {"reconnects": self.prior_reconnects,
                                "resubscribes": self.prior_resubscribes}}

    def check(self, reconnects: int) -> dict:
        """`reconnects` is THIS BOOT's stream counter; the bound is the run."""
        t = self.totals(reconnects)
        base = {"scope": "PER RUN, across every boot", **t,
                "max_reconnects": self.max_reconnects,
                "max_resubscribes": self.max_resubscribes}
        if t["reconnects"] > self.max_reconnects:
            return dict(base, ok=False, why=RC_RECONNECTS)
        if t["resubscribes"] > self.max_resubscribes:
            return dict(base, ok=False, why=RC_RESUBSCRIBES)
        return dict(base, ok=True, why=RC_OK)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def describe() -> dict:
    return {
        "budget": BUDGET_VERSION,
        "total_cap": TOTAL_CAP,
        "subcaps": dict(SUBCAPS),
        "socket_caps": dict(SOCKET_CAPS),
        "socket_unit": "ONE CONNECTION ATTEMPT (initial, failed or "
                       "reconnect) or ONE SUBSCRIBE MESSAGE (a batch is "
                       "TWO messages), reserved at the boundary before "
                       "the attempt is made",
        "unit": "ONE OUTBOUND HTTP REQUEST, including every pagination "
                "page and every retry",
        "subcaps_sum_to_total": sum(SUBCAPS.values()) == TOTAL_CAP,
        "ctl_kinds": dict(CTL_KIND),
        "forbidden": dict(FORBIDDEN),
        "reconnect_bounds": {"max_reconnects": MAX_RECONNECTS,
                             "max_resubscribes": MAX_RESUBSCRIBES,
                             "counted": "separately, because one reconnect "
                                        "resubscribes every slug"},
        "where_the_count_lives": "the armed allowance row, incremented "
                                 "under a row lock by "
                                 "bettor_live_control.reserve() BEFORE "
                                 "each dispatch",
        "no_environment_variable_sets_the_cap": True,
    }

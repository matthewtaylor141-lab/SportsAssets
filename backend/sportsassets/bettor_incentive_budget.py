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

    total      <=8   and the total binds even if the sub-caps do not

A FIRST ATTEMPT IS CHARGED TO ITS OWN KIND; A RETRY IS CHARGED TO
`retry`. That way "how many distinct questions did we ask" and "how
much did failure cost us" are separate numbers, and exhausting the
retry reserve cannot quietly eat the recheck allowance.

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

TOTAL_ENV = "BETTOR_INCENTIVE_HTTP_CAP"


def _cap_from_env() -> int:
    """The cap may be LOWERED by configuration and never raised.

    An environment variable that can widen a declared bound is not a
    bound. This one can only tighten it, so the number in the
    activation package is the worst case whatever the service holds.
    """
    raw = os.environ.get(TOTAL_ENV)
    if raw is None or not str(raw).strip():
        return TOTAL_CAP
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return TOTAL_CAP
    return max(0, min(v, TOTAL_CAP))


class RequestLedger:
    """Every outbound public request this run is allowed to make.

    Thread-safe because the stream runs on its own thread and a
    mid-run recheck must not race the main loop's accounting.
    """

    def __init__(self, *, total: int | None = None, subcaps=None) -> None:
        self.total = _cap_from_env() if total is None else max(0, int(total))
        self.subcaps = dict(SUBCAPS if subcaps is None else subcaps)
        self.spent: dict[str, int] = {k: 0 for k in self.subcaps}
        self.refused: list[dict] = []
        self.log: list[dict] = []
        self.seeded_from: dict | None = None
        self._lock = threading.Lock()

    def seed(self, spent: dict) -> dict:
        """Start from what a PREVIOUS BOOT already spent.

        The supervisor restarts a returning loop forever, so a ledger
        that began at zero on every boot would grant eight requests per
        restart. `bettor_incentive_state` holds the durable count and
        hands it here. An unreadable count seeds the ledger FULL rather
        than empty: not knowing what was spent is not evidence that
        nothing was.
        """
        with self._lock:
            for k in self.spent:
                try:
                    v = int((spent or {}).get(k, 0))
                except (TypeError, ValueError):
                    v = self.subcaps.get(k, 0)
                self.spent[k] = max(0, min(v, self.subcaps.get(k, 0)))
            self.seeded_from = dict(spent or {})
            return {"seeded": dict(self.spent),
                    "used": self.used_locked(),
                    "remaining": max(0, self.total - self.used_locked())}

    # ── the one gate ─────────────────────────────────────────────────

    def spend(self, kind: str, *, why: str = "") -> dict:
        """Reserve ONE outbound request, or refuse with a reason.

        Called BEFORE the request is dispatched. A caller that ignores
        a refusal and dispatches anyway is a defect this cannot catch,
        which is why the acquisition kinds are refused by name rather
        than merely left uncalled.
        """
        with self._lock:
            if kind in FORBIDDEN:
                return self._refuse(kind, R_FORBIDDEN, FORBIDDEN[kind])
            if kind not in self.subcaps:
                return self._refuse(kind, R_UNKNOWN,
                                    "no sub-cap declares %r" % kind)
            if self.used_locked() >= self.total:
                return self._refuse(kind, R_TOTAL,
                                    "%d of %d requests already spent"
                                    % (self.used_locked(), self.total))
            if self.spent[kind] >= self.subcaps[kind]:
                return self._refuse(kind, R_SUBCAP,
                                    "%s sub-cap %d reached"
                                    % (kind, self.subcaps[kind]))
            self.spent[kind] += 1
            rec = {"kind": kind, "why": why, "verdict": GRANTED,
                   "n_kind": self.spent[kind], "n_total": self.used_locked()}
            self.log.append(rec)
            return {"ok": True, "verdict": GRANTED, **rec}

    def _refuse(self, kind: str, verdict: str, detail: str) -> dict:
        rec = {"kind": kind, "verdict": verdict, "detail": detail,
               "n_total": self.used_locked()}
        self.refused.append(rec)
        self.log.append(rec)
        return {"ok": False, **rec}

    # ── accounting ───────────────────────────────────────────────────

    def used_locked(self) -> int:
        return sum(self.spent.values())

    def used(self) -> int:
        with self._lock:
            return self.used_locked()

    def remaining(self) -> int:
        with self._lock:
            return max(0, self.total - self.used_locked())

    def report(self) -> dict:
        """What was actually spent, beside what was declared.

        `declared_total` and `used` diverging is the whole point of
        reporting both: a run that spent seven of eight is evidence,
        and a run that spent eight and was then refused twice is a
        different and equally reportable fact.
        """
        with self._lock:
            return {
                "budget": BUDGET_VERSION,
                "declared_total": self.total,
                "subcaps": dict(self.subcaps),
                "used": self.used_locked(),
                "by_kind": dict(self.spent),
                # WHAT THIS BOOT INHERITED, kept apart from what it
                # spent. Without both numbers a restart's accounting
                # cannot be checked: `by_kind` at the end of boot 2
                # legitimately exceeds `by_kind` at the end of boot 1,
                # and reading that as a reset would be wrong in one
                # direction while reading it as proof of seeding would
                # be wrong in the other.
                "seeded_from": (dict(self.seeded_from)
                                if self.seeded_from is not None else None),
                "spent_this_boot": {
                    k: v - int((self.seeded_from or {}).get(k, 0) or 0)
                    for k, v in self.spent.items()}
                if self.seeded_from is not None else dict(self.spent),
                "remaining": max(0, self.total - self.used_locked()),
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
    """Two independent ceilings, read from the stream's own counters.

    The stream reconnects with a 1.0 s backoff doubling to a 30 s cap
    and does so FOREVER; that is right for a service and wrong for a
    bounded experiment, because a socket that flaps all night produces
    an unusable day of data while looking busy. This does not change
    the stream: it reads `reconnects` and the subscription states and
    says when the run should end.
    """

    def __init__(self, *, max_reconnects: int | None = None,
                 max_resubscribes: int | None = None) -> None:
        self.max_reconnects = _int_env(RECONNECT_ENV, MAX_RECONNECTS) \
            if max_reconnects is None else int(max_reconnects)
        self.max_resubscribes = _int_env(RESUBSCRIBE_ENV, MAX_RESUBSCRIBES) \
            if max_resubscribes is None else int(max_resubscribes)
        self.resubscribes = 0

    def note_resubscribe(self, n: int = 1) -> None:
        self.resubscribes += int(n)

    def check(self, reconnects: int) -> dict:
        """`reconnects` is the stream's own counter, not ours."""
        if reconnects > self.max_reconnects:
            return {"ok": False, "why": RC_RECONNECTS,
                    "reconnects": reconnects,
                    "max_reconnects": self.max_reconnects}
        if self.resubscribes > self.max_resubscribes:
            return {"ok": False, "why": RC_RESUBSCRIBES,
                    "resubscribes": self.resubscribes,
                    "max_resubscribes": self.max_resubscribes}
        return {"ok": True, "why": RC_OK, "reconnects": reconnects,
                "resubscribes": self.resubscribes,
                "max_reconnects": self.max_reconnects,
                "max_resubscribes": self.max_resubscribes}


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
        "unit": "ONE OUTBOUND HTTP REQUEST, including every pagination "
                "page and every retry",
        "forbidden": dict(FORBIDDEN),
        "reconnect_bounds": {"max_reconnects": MAX_RECONNECTS,
                             "max_resubscribes": MAX_RESUBSCRIBES,
                             "counted": "separately, because one reconnect "
                                        "resubscribes every slug"},
        "env_can_only_tighten": [TOTAL_ENV],
    }

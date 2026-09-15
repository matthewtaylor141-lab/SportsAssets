"""Only fund a whale whose edge is demonstrated at 95%.

Owner requirement, 2026-08-30: "at least 95% statistically proven
profitable... always ensure mathematical and statistical profit."

Until now that bar was a REPORT. Whales whose interval contained zero
were funded with real money all the same, and on 2026-08-30 that was
five of the six graded books. This makes the bar a GATE.

WHAT IT READS. workers/analytics.py publishes `whale_edge_benchmark`
hourly: per whale, the ratio-estimator ROI on dollar deployed with a
95% interval over that whale's own closed history. Since the same
day's clustering fix, the interval is computed over GAMES rather than
legs, so three lots from one match count as the single result they
are. The gate reads `edge_ci95[0] > 0` — the lower bound — and never
the human-readable verdict string, which analytics does not publish
and which would be a parsing dependency if it did.

WHY THE LOWER BOUND AND NOT THE POINT ESTIMATE. 0x076daa87 shows
+6.98% on dollar deployed, the best headline number on the roster, on
an interval running from -7.46% to +21.42%. A point-estimate rule
funds him first. The honest reading is that his edge cannot be told
from zero, and the gate declines him.

IT FAILS CLOSED, ON EVERY PATH. Unreadable state, stale statistic,
stale cache, missing whale, absent interval, truncated replay — each
returns REFUSE with its own named reason. A gate that fails open is
worse than no gate, because it reads as safety while providing none.

IT DOES NOT TOUCH EXITS, EVER. At this venue whales close by buying
the complement, so an exit arrives labelled BUY and is classified well
below this point in maybe_execute. This gate sits after mirror_exit
has already had its say and before the first line that sizes an order.
A refused whale can always still be sold: exitable_whales() is not
consulted here and is not modified. Refusing to BUY and refusing to
SELL are opposite risks, and only the first is wanted.

THERE IS NO ENVIRONMENT VARIABLE THAT OPENS IT. A stale Render env
silently overrode a roster order for two days in August; the same
mistake here would fund an unproven book. Widening this gate is a
reviewed code change. Tightening it is always allowed.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

# Re-read the published statistic this often.
GATE_CACHE_TTL_S = 300.0
# HARD expiry on the READ, independent of how old the payload says it
# is. These are deliberately different clocks: the payload can be
# hours old and still be the honest latest measurement, but if we have
# not SUCCESSFULLY read the row in this long we no longer know what it
# says. A DB blip is not evidence of profit.
GATE_CACHE_MAX_AGE_S = 900.0
# How stale the measurement itself may be. The benchmark republishes
# hourly (WHALE_BENCHMARK_EVERY_S=3600), so three hours is two missed
# cycles — long enough not to flap, short enough that a dead analytics
# worker stops the money rather than coasting on last week's numbers.
GATE_STAT_MAX_AGE_S = 10800.0

_STATE_KEY = "whale_edge_benchmark"

_cache: dict[str, Any] = {"per_whale": None, "measured_at": None,
                          "read_at": 0.0, "err": "never-read"}


def _age_of(iso: str | None, now: float) -> float | None:
    if not iso:
        return None
    try:
        import datetime as _dt

        t = _dt.datetime.fromisoformat(str(iso))
        if t.tzinfo is None:
            t = t.replace(tzinfo=_dt.timezone.utc)
        return now - t.timestamp()
    except (TypeError, ValueError):
        return None


def decide(per_whale: dict | None, whale: str, *,
           stat_age_s: float | None, read_age_s: float,
           read_err: str | None = None) -> tuple[bool, str]:
    """PURE. May this whale be funded, and why not.

    Pure so the admin endpoint reconstructs the worker's decision from
    the same inputs instead of reimplementing it. The API and the
    workers are separate services (render.yaml), so an endpoint that
    computed this its own way would show the owner a verdict no money
    path ever used — an instrument reading something other than its
    subject.
    """
    if read_err:
        return False, f"edge-stat-unread:{read_err}"
    if read_age_s > GATE_CACHE_MAX_AGE_S:
        return False, "edge-stat-read-stale"
    if not isinstance(per_whale, dict):
        return False, "edge-stat-absent"
    if stat_age_s is None:
        return False, "edge-stat-undated"
    if stat_age_s > GATE_STAT_MAX_AGE_S:
        return False, "edge-stat-stale"
    g = per_whale.get(whale) or per_whale.get(whale.lower())
    if not isinstance(g, dict):
        return False, "edge-missing-whale"
    # A CAPPED REPLAY IS NOT A SAMPLE. merge_pnl stops at 2,000,000
    # fills per whale (raised from 600,000 on 2026-08-30, when the cap
    # itself turned out to be what refused rn1) and walks ORDER BY
    # condition_id, so a flagged book is a prefix of that whale's
    # MARKETS in condition_id order — not, as this comment said until
    # 2026-08-30, his earliest trades.
    #
    # The milder reading is that condition_id is a hash, so the markets
    # kept are arbitrary with respect to profitability and the prefix
    # is nearly a cluster sample. That may well be true. It is an
    # inference about how the venue mints ids, and a gate that funds on
    # it is betting real money on my reading of a hash function. Refuse
    # instead, and raise the cap when a book outgrows it — `fills_total`
    # on the payload says by how much.
    if g.get("truncated"):
        return False, "edge-truncated-replay"
    # A NEIGHBOUR'S TRUNCATION IS NOT THIS WHALE'S PROBLEM. For one
    # afternoon this refused every whale whenever any whale in the
    # payload was flagged. The stated reason was that the unflagged
    # rows in run 1403 were wrong too — and they were not. That
    # comparison put the worker's WHOLE-BOOK publish beside the probe's
    # `?since=2026-08-01` read and called the difference a fault. It is
    # a window. merge_pnl's own window_warning says so. Every published
    # interval was NARROWER than its windowed counterpart, which is
    # what more data does, not what a corrupt read does.
    #
    # The replay budget is per whale (merge_pnl:589, inside the whale
    # loop), so there is no mechanism by which one book being cut short
    # touches another's numbers. A payload-wide refusal would also
    # never lift: swisstony's book is past the cap persistently, so it
    # would have blocked the roster forever on a reason that was not
    # real.
    ci = g.get("edge_ci95")
    if not isinstance(ci, (list, tuple)) or len(ci) != 2:
        return False, "edge-no-interval"
    try:
        lo, hi = float(ci[0]), float(ci[1])
    except (TypeError, ValueError):
        return False, "edge-bad-interval"
    if lo > 0:
        return True, "edge-proven-at-95"
    if hi < 0:
        return False, "edge-losing-at-95"
    return False, "edge-not-demonstrated"


async def refresh(pool: Any) -> None:
    """Re-read the published benchmark. Stamps read_at ON SUCCESS ONLY.

    The obvious idiom — stamp at the top of the function — makes
    `now - read_at` permanently near zero, so the hard expiry above can
    never fire and a dead database funds whales forever on a verdict
    nobody can still read. live_executor.refresh_whale_overrides:1481
    already does it correctly; this copies that deliberately.
    """
    now = time.monotonic()
    if _cache["read_at"] and now - _cache["read_at"] < GATE_CACHE_TTL_S:
        return
    try:
        row = await pool.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1", _STATE_KEY)
    except Exception as exc:  # noqa: BLE001 — refuse, never assume
        _cache["err"] = type(exc).__name__
        log.warning("edge gate: benchmark read failed (%s)", _cache["err"])
        return
    if row is None:
        _cache["err"] = "no-row"
        return
    try:
        val = json.loads(row) if isinstance(row, str) else row
        pw = val.get("per_whale")
        if not isinstance(pw, dict):
            _cache["err"] = "no-per-whale"
            return
        _cache["per_whale"] = pw
        _cache["measured_at"] = val.get("measured_at")
        _cache["err"] = None
        _cache["read_at"] = now          # ONLY here. See the docstring.
    except Exception as exc:  # noqa: BLE001
        _cache["err"] = type(exc).__name__


def verdict(whale: str) -> tuple[bool, str]:
    """Decide for `whale` from whatever the last successful read holds."""
    now = time.monotonic()
    read_age = (now - _cache["read_at"]) if _cache["read_at"] else 1e9
    return decide(_cache["per_whale"], whale,
                  stat_age_s=_age_of(_cache["measured_at"], time.time()),
                  read_age_s=read_age, read_err=_cache["err"])


def snapshot() -> dict:
    """What the gate currently believes, for the probe and the endpoint."""
    now = time.monotonic()
    pw = _cache["per_whale"] or {}
    out = {"measured_at": _cache["measured_at"],
           "read_age_s": (round(now - _cache["read_at"], 1)
                          if _cache["read_at"] else None),
           "err": _cache["err"], "whales": {}}
    for w in sorted(pw):
        ok, why = verdict(w)
        g = pw.get(w) or {}
        out["whales"][w] = {
            "funded": ok, "reason": why,
            "ci95": g.get("edge_ci95"), "roi": g.get("edge_roi"),
            "clusters": g.get("edge_clusters"), "deff": g.get("edge_deff"),
            "truncated": bool(g.get("truncated")),
            # THE WHOLE-BOOK FILL COUNTS, carried so the probe can read
            # them WITHOUT re-running the replay. The probe's own
            # merge-pnl call passes ?since=2026-08-01; the publish is
            # whole-book. Reading one as the other is how a window got
            # mistaken for a corrupt payload on 2026-08-30. These two
            # fields make the gate's actual input legible on its own
            # terms, and fills_total is the distance from a refused
            # whale to a fundable one.
            "fills_read": g.get("fills_read"),
            "fills_total": g.get("fills_total"),
        }
    return out

"""THE QUALITY SCORECARD: FIVE DOMAINS, EVERY FIGURE WITH ITS EVIDENCE.

  ENGINEERING               gate results and failing tests (from the latest
                            gate artifact this process can read, else
                            UNAVAILABLE); deploy SHA alignment API == workers
  AGENTS                    Xavier review latency; Xavier's FRESHNESS, split
                            so a correct stale label is never read as a
                            defect (see "XAVIER'S FRESHNESS" below);
                            peer-challenge metrics (the
                            collaboration loop's PEER_CHALLENGE stages, and
                            KAREN's tables when they exist, else UNAVAILABLE)
  INVESTMENT_INTELLIGENCE   calibration: Brier of the decision probability
                            against the Brier of the executable price, paired
  EXECUTION                 fill rate, slippage against the decision, actual
                            admission refusals
  PROFITABILITY_EVIDENCE    the PREDECLARED forward sample (FORWARD_SAMPLE_RULE)
                            against its requirement -> UNPROVEN until met

EVERY METRIC carries: value, numerator, denominator, sample, ci (where a
sampling interval applies; method named), trend (the same metric over the
prior window of equal length), last_measured_at, status, blocker,
next_improvement. A metric that cannot be measured is UNAVAILABLE with a
reason and a null value -- never 0. A metric measured on fewer samples than
its stated minimum is INSUFFICIENT_SAMPLE: the value is shown with its
interval, and nothing is concluded from it.

XAVIER'S FRESHNESS (measurement only; Xavier's behaviour is unchanged).
Freshness is CHANGE-driven: a probability is current only within 30 s of
the provider's last price CHANGE, and an unchanged provider quote is never
called current. A SCHEDULED_BACKSTOP review of a position whose quote has
not moved (a game over, a market no longer quoted) is therefore correctly
STALE_ENTRY_TIME_PROBABILITY. Counting it against freshness conflates two
things, so the freshness quality is read from xavier_management_assessments
(migration 206; paper and actual) as:
  freshness_when_market_changed       MARKET_EVENT reviews on a FRESH
                                      probability / all MARKET_EVENT reviews
  scheduled_review_no_provider_change SCHEDULED_BACKSTOP reviews not on a
                                      fresh probability (no provider change
                                      observed since the last review) / all
                                      reviews -- informational, correctly
                                      stale, excluded from freshness quality
  market_change_to_review_sla         MARKET_EVENT reviews whose latency
                                      from the change (due_at = the provider
                                      change, or the venue book for a paper
                                      book move) is within the 30 s SLA
  market_change_review_latency_s      the median (p90 in detail) of that
                                      latency
  positions_monitored_share           OPEN positions whose latest review is
                                      inside its due bound
                                      (xavier_management.management_view)
  freshness_at_discretionary_action   reviews recommending EXIT / REDUCE /
                                      REALLOCATE, or permitting discretion,
                                      on a FRESH probability (100 % by rule)
The old `reviews_fresh_share` is kept, renamed to say it counts ALL reviews
including unchanged quotes, and is no longer a quality target.

Read-only: every figure comes from production tables and files; nothing here
writes, and nothing here can change an order, a limit or a policy.
"""
from __future__ import annotations

import datetime as _dt
import glob
import json
import math
import os
import re
import statistics
import time

VERSION = "QUALITY_SCORECARD_V1"
WINDOW_S = 7 * 86400.0
MIN_RATE_SAMPLE = 30
DOMAINS = ("ENGINEERING", "AGENTS", "INVESTMENT_INTELLIGENCE", "EXECUTION",
           "PROFITABILITY_EVIDENCE")
FRESH = "FRESH_CURRENT_PROBABILITY"
STALE = "STALE_ENTRY_TIME_PROBABILITY"
UNAVAIL = "PROBABILITY_UNAVAILABLE"
T_MARKET, T_BACKSTOP = "MARKET_EVENT", "SCHEDULED_BACKSTOP"
DISCRETIONARY = ("EXIT", "REDUCE", "REALLOCATE")
#: THE MARKET-CHANGE -> REVIEW SLA (a MEASUREMENT bound, read by nothing
#: that decides): the Pinnacle 30 s rule. A review later than this after the
#: change cannot be on a fresh reading of that change.
MARKET_CHANGE_REVIEW_SLA_S = 30.0
#: the PinnAPI feed runtime's persisted heartbeat (held watch telemetry)
FEED_HEARTBEAT_KEY = "pinnapi_feed_last"
#: management_view's own cap on positions read per book
MONITORED_VIEW_LIMIT = 500
#: Where a gate artifact may be read (file or glob of run_gate.sh's
#: `<prefix>_report.json`). Read only when the host sets it.
GATE_REPORT_ENV = "QUALITY_GATE_REPORT_PATH"

#: ── THE PREDECLARED INDEPENDENT FORWARD SAMPLE ─────────────────────────
#: Declared in code BEFORE any position it counts exists: only positions
#: whose entry is at or after `forward_start` count, so nothing used to
#: design the rule is in it. Profitability is UNPROVEN until it is met.
from .quality_stats import (FORWARD_SAMPLE_RULE, _epoch_iso,  # noqa: F401
                            forward_verdict, mean_ci, wilson)

FORWARD_START = _epoch_iso(FORWARD_SAMPLE_RULE["forward_start"])


# ═════════════════════════════════════════════════════════════════════
# STATISTICS (pure)
# ═════════════════════════════════════════════════════════════════════

def median_ci(xs: list) -> dict | None:
    """Distribution-free 95% CI for the median (binomial order stats)."""
    n = len(xs)
    if n < 6:
        return None
    s = sorted(xs)
    half = 1.96 * math.sqrt(n) / 2
    lo = max(0, int(math.floor(n / 2 - half)))
    hi = min(n - 1, int(math.ceil(n / 2 + half)) - 1)
    return {"low": round(s[lo], 3), "high": round(s[hi], 3), "level": 0.95,
            "method": "ORDER_STATISTICS"}


def metric(mid: str, name: str, *, value=None, numerator=None,
           denominator=None, sample=None, ci=None, trend=None, unit=None,
           last_measured_at=None, status: str = "MEASURED", blocker=None,
           next_improvement=None, min_sample=None, detail=None,
           why=None) -> dict:
    if value is None and status == "MEASURED":
        status = "UNAVAILABLE"
    if status == "MEASURED" and min_sample is not None and \
            (sample or 0) < min_sample:
        status = "INSUFFICIENT_SAMPLE"
        blocker = blocker or "fewer than %d samples (%s)" % (min_sample,
                                                             sample or 0)
    return {"id": mid, "name": name, "value": value, "unit": unit,
            "numerator": numerator, "denominator": denominator,
            "sample": sample, "min_sample": min_sample, "ci": ci,
            "trend": trend, "last_measured_at": last_measured_at,
            "status": status, "why": why, "blocker": blocker,
            "next_improvement": next_improvement, "detail": detail}


def unavailable(mid: str, name: str, why: str, *, next_improvement=None,
                unit=None) -> dict:
    return metric(mid, name, status="UNAVAILABLE", why=why, blocker=why,
                  next_improvement=next_improvement, unit=unit)


def trend_of(cur, prior, *, higher_is_better: bool | None = True) -> dict:
    if cur is None or prior is None:
        return {"prior_value": prior, "delta": None, "direction": None,
                "why": "PRIOR_PERIOD_UNMEASURED" if prior is None
                else "CURRENT_PERIOD_UNMEASURED"}
    d = round(cur - prior, 6)
    if d == 0 or higher_is_better is None:
        direction = "FLAT" if d == 0 else ("UP" if d > 0 else "DOWN")
    else:
        direction = ("IMPROVING" if (d > 0) == higher_is_better
                     else "DETERIORATING")
    return {"prior_value": prior, "delta": d, "direction": direction,
            "why": None}


async def _regclass(conn, name: str) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        name))
    except Exception:                                           # noqa: BLE001
        return False


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _ep(v):
    return v.timestamp() if hasattr(v, "timestamp") else _f(v)


# ═════════════════════════════════════════════════════════════════════
# ENGINEERING
# ═════════════════════════════════════════════════════════════════════

def gate_metric(*, path_spec: str | None = None) -> list:
    spec = path_spec if path_spec is not None else os.environ.get(
        GATE_REPORT_ENV, "")
    nxt = ("publish each release gate's report.json where the API can read "
           "it (%s)" % GATE_REPORT_ENV)
    if not spec:
        why = "%s_NOT_SET_ON_THIS_HOST" % GATE_REPORT_ENV
        return [unavailable("gate_result", "Latest gate result", why,
                            next_improvement=nxt),
                unavailable("failing_tests", "Failing tests (latest gate)",
                            why, next_improvement=nxt, unit="tests")]
    paths = sorted(glob.glob(spec), key=lambda p: os.path.getmtime(p),
                   reverse=True)
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        counts = doc.get("counts") or {}
        failed = int(counts.get("failed", 0)) + int(counts.get("error", 0))
        executed = doc.get("executed_count")
        at = _f(doc.get("finished_at")) or os.path.getmtime(p)
        complete = bool(doc.get("session_complete"))
        return [
            metric("gate_result", "Latest gate result",
                   value="COMPLETE" if complete else "INCOMPLETE",
                   sample=executed, last_measured_at=at,
                   detail={"exitstatus": doc.get("exitstatus"),
                           "artifact": os.path.basename(p),
                           "collect_errors": len(doc.get(
                               "collect_errors") or [])},
                   blocker=None if complete else
                   "the gate run did not record completion",
                   next_improvement="compare against the matched baseline "
                                    "(gate_verdict.py)"),
            metric("failing_tests", "Failing tests (latest gate)",
                   value=failed if complete else None, numerator=failed,
                   denominator=executed, sample=executed, unit="tests",
                   last_measured_at=at,
                   status="MEASURED" if complete else "UNAVAILABLE",
                   why=None if complete else "GATE_RUN_INCOMPLETE",
                   next_improvement="reduce failures to the capital-critical"
                                    " list's zero and the baseline's count")]
    why = "NO_READABLE_GATE_ARTIFACT_AT_%s" % GATE_REPORT_ENV
    return [unavailable("gate_result", "Latest gate result", why,
                        next_improvement=nxt),
            unavailable("failing_tests", "Failing tests (latest gate)", why,
                        next_improvement=nxt, unit="tests")]


SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def sha_alignment(api: str | None, workers: str | None) -> dict:
    a = (api or "").strip().lower()
    w = (workers or "").strip().lower()
    if not SHA_RE.match(a) or not SHA_RE.match(w):
        return {"verdict": None,
                "why": "API_SHA_UNKNOWN" if not SHA_RE.match(a)
                else "WORKERS_SHA_UNKNOWN"}
    if len(a) == 40 and len(w) == 40:
        return {"verdict": "ALIGNED" if a == w else "MISALIGNED",
                "matched_how": "EXACT"}
    short = min(len(a), len(w))
    return {"verdict": "ALIGNED" if a[:short] == w[:short] else "MISALIGNED",
            "matched_how": "PREFIX_%d" % short}


async def deploy_alignment(conn) -> dict:
    api = os.environ.get("RENDER_GIT_COMMIT")
    workers, at = None, None
    try:
        raw = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key='workers_boot'")
        v = json.loads(raw) if isinstance(raw, str) else (raw or {})
        workers = v.get("commit_sha") or v.get("commit")
        at = v.get("at")
    except Exception:                                           # noqa: BLE001
        pass
    al = sha_alignment(api, workers)
    nxt = "release API and workers from the same exact SHA"
    if al["verdict"] is None:
        return unavailable("deploy_sha_alignment", "Deploy SHA API == workers",
                           al["why"], next_improvement=nxt)
    return metric("deploy_sha_alignment", "Deploy SHA API == workers",
                  value=al["verdict"], sample=2, last_measured_at=time.time(),
                  detail={"api": api, "workers": workers,
                          "workers_boot_at": at,
                          "matched_how": al["matched_how"]},
                  blocker=None if al["verdict"] == "ALIGNED" else
                  "API and workers serve different builds",
                  next_improvement=nxt)


# ═════════════════════════════════════════════════════════════════════
# AGENTS
# ═════════════════════════════════════════════════════════════════════

async def review_latencies(conn, start: float, end: float) -> list:
    rows = await conn.fetch(
        "SELECT extract(epoch FROM (min(r.reviewed_at) - h.first_fill_at))"
        "       AS lat FROM paper_handoffs h "
        "  JOIN paper_xavier_reviews r ON r.group_id = h.group_id "
        "   AND r.account_id = h.account_id "
        "   AND r.reviewed_at >= h.first_fill_at "
        " WHERE h.first_fill_at >= to_timestamp($1) "
        "   AND h.first_fill_at < to_timestamp($2) "
        " GROUP BY h.handoff_id, h.first_fill_at", start, end)
    return [float(r["lat"]) for r in rows if r["lat"] is not None]


async def review_latency_metric(conn, now: float) -> dict:
    name = "Xavier review latency (first fill -> first review), median"
    nxt = "review each handoff in the same pass as its first fill"
    if not (await _regclass(conn, "paper_handoffs")
            and await _regclass(conn, "paper_xavier_reviews")):
        return unavailable("review_latency_s", name, "SOURCE_TABLE_ABSENT",
                           next_improvement=nxt, unit="s")
    cur = await review_latencies(conn, now - WINDOW_S, now)
    prior = await review_latencies(conn, now - 2 * WINDOW_S, now - WINDOW_S)
    med = round(statistics.median(cur), 3) if cur else None
    pmed = round(statistics.median(prior), 3) if prior else None
    unreviewed = await conn.fetchval(
        "SELECT count(*) FROM paper_handoffs h WHERE h.first_fill_at >= "
        " to_timestamp($1) AND NOT EXISTS (SELECT 1 FROM paper_xavier_reviews"
        " r WHERE r.group_id = h.group_id AND r.account_id = h.account_id)",
        now - WINDOW_S)
    return metric("review_latency_s", name, value=med, sample=len(cur),
                  unit="s", ci=median_ci(cur),
                  trend=trend_of(med, pmed, higher_is_better=False),
                  last_measured_at=now, min_sample=10,
                  detail={"p90_s": (round(sorted(cur)[int(0.9 * (len(cur)
                                                                 - 1))], 3)
                                    if cur else None),
                          "handoffs_never_reviewed": int(unreviewed or 0)},
                  why=None if cur else "NO_REVIEWED_HANDOFF_IN_WINDOW",
                  blocker=("%d handoff(s) never reviewed" % unreviewed)
                  if unreviewed else None, next_improvement=nxt)


async def fresh_share(conn, start, end) -> tuple:
    r = await conn.fetchrow(
        "SELECT count(*) AS n, count(*) FILTER (WHERE "
        " measure->>'evidence_state' = $3) AS k, max(reviewed_at) AS last "
        " FROM paper_xavier_reviews WHERE reviewed_at >= to_timestamp($1) "
        "  AND reviewed_at < to_timestamp($2)", start, end, FRESH)
    return int(r["k"]), int(r["n"]), _ep(r["last"])


FRESH_SHARE_NAME = ("All paper reviews on a fresh probability (includes "
                    "scheduled reviews of unchanged quotes)")


async def fresh_share_metric(conn, now: float) -> dict:
    """KEPT FOR CONTINUITY, NOT A QUALITY TARGET: its denominator includes
    every scheduled review of a position whose provider quote has not
    changed (correctly stale). Freshness quality is
    `freshness_when_market_changed`."""
    name = FRESH_SHARE_NAME
    nxt = ("read freshness quality from freshness_when_market_changed; this "
           "share falls whenever held quotes stop changing, by design")
    if not await _regclass(conn, "paper_xavier_reviews"):
        return unavailable("reviews_fresh_share", name,
                           "SOURCE_TABLE_ABSENT", next_improvement=nxt)
    k, n, last = await fresh_share(conn, now - WINDOW_S, now)
    pk, pn, _ = await fresh_share(conn, now - 2 * WINDOW_S, now - WINDOW_S)
    v = round(k / n, 6) if n else None
    pv = round(pk / pn, 6) if pn else None
    return metric("reviews_fresh_share", name, value=v, numerator=k,
                  denominator=n, sample=n, ci=wilson(k, n), unit="share",
                  trend=trend_of(v, pv, higher_is_better=None),
                  last_measured_at=last, min_sample=MIN_RATE_SAMPLE,
                  why=None if n else "NO_REVIEWS_IN_WINDOW",
                  detail={"counts": "every paper_xavier_reviews row, every "
                                    "trigger",
                          "not_a_quality_target": (
                              "a SCHEDULED_BACKSTOP review of an unchanged "
                              "provider quote is correctly "
                              "STALE_ENTRY_TIME_PROBABILITY and is in this "
                              "denominator"),
                          "read_instead": list(FRESHNESS_IDS)},
                  next_improvement=nxt)


# ── XAVIER'S FRESHNESS, SPLIT (xavier_management_assessments) ────────────

async def assessment_counts(conn, start: float, end: float) -> list:
    """(position_kind, trigger, evidence_state, discretionary flags) counts
    of the management assessments in [start, end)."""
    rows = await conn.fetch(
        "SELECT position_kind AS kind, trigger, evidence_state AS ev, "
        "       (recommendation = ANY($3::text[])) AS rec_disc, "
        "       discretionary_permitted AS permitted, count(*) AS n, "
        "       max(assessed_at) AS last "
        "  FROM xavier_management_assessments "
        " WHERE assessed_at >= to_timestamp($1) "
        "   AND assessed_at < to_timestamp($2) "
        " GROUP BY 1, 2, 3, 4, 5", start, end, list(DISCRETIONARY))
    return [{"kind": r["kind"], "trigger": r["trigger"], "ev": r["ev"],
             "rec_disc": bool(r["rec_disc"]),
             "permitted": bool(r["permitted"]), "n": int(r["n"]),
             "last": _ep(r["last"])} for r in rows]


def _sum(rows, pred) -> int:
    return sum(r["n"] for r in rows if pred(r))


def _last(rows, pred):
    xs = [r["last"] for r in rows if pred(r) and r["last"] is not None]
    return max(xs) if xs else None


def freshness_split(rows: list) -> dict:
    """The freshness figures from assessment counts. Pure."""
    def mk(r):
        return r["trigger"] == T_MARKET

    def bk(r):
        return r["trigger"] == T_BACKSTOP

    def unchanged(r):
        return bk(r) and r["ev"] != FRESH

    def disc(r):
        return r["rec_disc"] or r["permitted"]
    total = _sum(rows, lambda r: True)
    fresh_all = _sum(rows, lambda r: r["ev"] == FRESH)
    by_trigger: dict = {}
    for r in rows:
        t = by_trigger.setdefault(r["trigger"], {"n": 0, "fresh": 0})
        t["n"] += r["n"]
        t["fresh"] += r["n"] if r["ev"] == FRESH else 0
    by_kind: dict = {}
    for r in rows:
        if not mk(r):
            continue
        t = by_kind.setdefault(r["kind"], {"n": 0, "fresh": 0})
        t["n"] += r["n"]
        t["fresh"] += r["n"] if r["ev"] == FRESH else 0
    nochange = _sum(rows, unchanged)
    return {
        "total": total, "fresh_all": fresh_all, "by_trigger": by_trigger,
        "market_n": _sum(rows, mk),
        "market_fresh": _sum(rows, lambda r: mk(r) and r["ev"] == FRESH),
        "market_by_kind": by_kind,
        "market_by_evidence": {e: _sum(rows, lambda r, e=e: mk(r)
                                       and r["ev"] == e)
                               for e in (FRESH, STALE, UNAVAIL)},
        "market_last": _last(rows, mk),
        "backstop_n": _sum(rows, bk),
        "backstop_fresh": _sum(rows, lambda r: bk(r) and r["ev"] == FRESH),
        "backstop_stale": _sum(rows, lambda r: bk(r) and r["ev"] == STALE),
        "backstop_unavailable": _sum(rows, lambda r: bk(r)
                                     and r["ev"] == UNAVAIL),
        "no_change": nochange, "last": _last(rows, lambda r: True),
        "quality_denominator": total - nochange,
        "disc_n": _sum(rows, disc),
        "disc_fresh": _sum(rows, lambda r: disc(r) and r["ev"] == FRESH),
        "rec_n": _sum(rows, lambda r: r["rec_disc"]),
        "rec_fresh": _sum(rows, lambda r: r["rec_disc"] and r["ev"] == FRESH),
        "permitted_n": _sum(rows, lambda r: r["permitted"]),
        "permitted_fresh": _sum(rows, lambda r: r["permitted"]
                                and r["ev"] == FRESH)}


def _share(k, n):
    return round(k / n, 6) if n else None


def percentile(xs: list, q: float):
    """Nearest-rank (lower) percentile, the convention review_latency_s
    already uses. Pure."""
    if not xs:
        return None
    s = sorted(xs)
    return round(s[int(q * (len(s) - 1))], 3)


async def market_change_latencies(conn, start: float, end: float) -> tuple:
    """(latencies of MARKET_EVENT reviews with a known change instant,
    count with none, by kind)."""
    rows = await conn.fetch(
        "SELECT position_kind AS kind, review_latency_s AS lat "
        "  FROM xavier_management_assessments "
        " WHERE trigger = $3 AND assessed_at >= to_timestamp($1) "
        "   AND assessed_at < to_timestamp($2)", start, end, T_MARKET)
    lats = [float(r["lat"]) for r in rows if _f(r["lat"]) is not None]
    by_kind: dict = {}
    for r in rows:
        if _f(r["lat"]) is None:
            continue
        k = by_kind.setdefault(r["kind"], {"n": 0, "within_sla": 0})
        k["n"] += 1
        k["within_sla"] += 1 if float(r["lat"]) <= \
            MARKET_CHANGE_REVIEW_SLA_S else 0
    return lats, len(rows) - len(lats), by_kind


def sla_split(lats: list, *,
              sla_s: float = MARKET_CHANGE_REVIEW_SLA_S) -> dict:
    """Within-SLA count, median and p90 of market-change latencies. Pure."""
    k = sum(1 for x in lats if x <= sla_s)
    return {"n": len(lats), "within": k,
            "median": round(statistics.median(lats), 3) if lats else None,
            "p90": percentile(lats, 0.9),
            "max": round(max(lats), 3) if lats else None}


async def held_watch_heartbeat(conn) -> dict | None:
    """The held watch's PERSISTED telemetry (the feed runtime's heartbeat):
    cumulative counts since that process started -- not a per-event
    record. None when absent or unreadable."""
    try:
        raw = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key = $1",
            FEED_HEARTBEAT_KEY)
    except Exception:                                           # noqa: BLE001
        return None
    v = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(v, dict):
        return None
    h = v.get("held_priority_targets")
    if not isinstance(h, dict):
        return {"beat_at": _f(v.get("beat_at")),
                "held_watch": "NOT_IN_HEARTBEAT"}
    counts = h.get("counts") or {}
    return {"beat_at": _f(v.get("beat_at")),
            "held_slugs": h.get("held_slugs"),
            "held_events": h.get("held_events"),
            "unmatched": h.get("unmatched"),
            "held_changes_since_feed_start": counts.get("HELD_CHANGES"),
            "basis": "cumulative counters of the running feed process; not "
                     "a per-change record, no window"}


#: WHAT THE HELD WATCH RECORDS (pinnapi_held.HeldWatch): in memory only,
#: the LATEST provider change instant per held slug (`changes`), and a
#: cumulative HELD_CHANGES counter the feed heartbeat persists. No held
#: price-change EVENT is written anywhere, so the SLA's denominator is the
#: changes that reached a MARKET_EVENT review.
SLA_LIMITATION = (
    "held price changes are not persisted per event (pinnapi_held.HeldWatch "
    "keeps only the latest change instant per held slug, in memory); the "
    "denominator is the changes that REACHED a MARKET_EVENT review -- a "
    "change superseded by a later one before its review (coalesced) or "
    "never reviewed is not counted")


FRESHNESS_IDS = ("freshness_when_market_changed",
                 "scheduled_review_no_provider_change",
                 "market_change_to_review_sla",
                 "market_change_review_latency_s",
                 "freshness_at_discretionary_action")
FRESHNESS_NAMES = (
    "Xavier: reviews on a fresh probability when the market changed",
    ("Xavier: scheduled reviews with no provider change (correctly stale; "
     "informational)"),
    "Xavier: market change -> review within %.0f s"
    % MARKET_CHANGE_REVIEW_SLA_S,
    "Xavier: review latency after a market change, median",
    "Xavier: discretionary recommendations on a fresh probability")


async def freshness_metrics(conn, now: float) -> list:
    ids, names = FRESHNESS_IDS, FRESHNESS_NAMES
    if not await _regclass(conn, "xavier_management_assessments"):
        return [unavailable(i, n, "MIGRATION_206_NOT_APPLIED",
                            unit="s" if i.endswith("_s") else None)
                for i, n in zip(ids, names)]
    cur = freshness_split(await assessment_counts(conn, now - WINDOW_S, now))
    pri = freshness_split(await assessment_counts(conn, now - 2 * WINDOW_S,
                                                  now - WINDOW_S))
    hb = await held_watch_heartbeat(conn)
    out = []

    # 1. FRESHNESS WHEN THE MARKET CHANGED
    k, n = cur["market_fresh"], cur["market_n"]
    v = _share(k, n)
    out.append(metric(
        ids[0], names[0], value=v, numerator=k, denominator=n, sample=n,
        ci=wilson(k, n), unit="share",
        trend=trend_of(v, _share(pri["market_fresh"], pri["market_n"])),
        last_measured_at=cur["market_last"], min_sample=MIN_RATE_SAMPLE,
        why=None if n else "NO_MARKET_EVENT_REVIEW_IN_WINDOW",
        detail={"by_kind": cur["market_by_kind"],
                "by_evidence_state": cur["market_by_evidence"],
                "excludes": "SCHEDULED_BACKSTOP reviews of unchanged quotes",
                "note": ("a paper MARKET_EVENT also fires on a venue "
                         "best-exit move; one with no provider change is "
                         "correctly stale and stays in this denominator "
                         "(the stored record does not tell the two apart)")},
        blocker=None if not n or k == n else
        "%d of %d market-change reviews were not on a fresh probability"
        % (n - k, n),
        next_improvement="keep every held sport in the PinnAPI feed scope "
                         "so a provider change reaches the held watch"))

    # 2. SCHEDULED REVIEWS WITH NO PROVIDER CHANGE (informational)
    k, n = cur["no_change"], cur["total"]
    v = _share(k, n)
    qd = cur["quality_denominator"]
    out.append(metric(
        ids[1], names[1], value=v, numerator=k, denominator=n, sample=n,
        ci=wilson(k, n), unit="share of all reviews",
        trend=trend_of(v, _share(pri["no_change"], pri["total"]),
                       higher_is_better=None),
        last_measured_at=cur["last"], min_sample=MIN_RATE_SAMPLE,
        why=None if n else "NO_REVIEW_IN_WINDOW",
        detail={"informational": True,
                "count": k,
                "definition": ("SCHEDULED_BACKSTOP reviews not on a fresh "
                               "probability: the backstop is chosen only "
                               "when the held watch observed no provider "
                               "change since the last review, so the "
                               "quote is unchanged and correctly "
                               "STALE_ENTRY_TIME_PROBABILITY (an unchanged "
                               "quote is never called current)"),
                "backstop_total": cur["backstop_n"],
                "backstop_fresh": cur["backstop_fresh"],
                "backstop_stale": cur["backstop_stale"],
                "backstop_unavailable": cur["backstop_unavailable"],
                "by_trigger": cur["by_trigger"],
                "freshness_quality_denominator": qd,
                # the no-change backstops are non-fresh by definition, so
                # every fresh review stays in the numerator
                "fresh_share_excluding_unchanged_quotes": _share(
                    cur["fresh_all"], qd),
                "limitation": ("'no change' means none OBSERVED by the held "
                               "watch: a held slug the feed cannot match "
                               "(out of feed scope, unmatched) observes "
                               "none"),
                "held_watch_heartbeat": hb},
        blocker=None,
        next_improvement="none needed: these are correct stale labels; "
                         "they are excluded from freshness quality"))

    # 3. MARKET CHANGE -> REVIEW WITHIN THE SLA, AND ITS LATENCY
    lats, unknown, lby = await market_change_latencies(conn, now - WINDOW_S,
                                                       now)
    plats, _, _ = await market_change_latencies(conn, now - 2 * WINDOW_S,
                                                now - WINDOW_S)
    s, ps = sla_split(lats), sla_split(plats)
    v = _share(s["within"], s["n"])
    sla_detail = {"sla_s": MARKET_CHANGE_REVIEW_SLA_S,
                  "latency_origin": ("due_at: the provider change instant "
                                     "(PinnAPI held watch), or the venue "
                                     "book's observation for a paper book "
                                     "move"),
                  "median_s": s["median"], "p90_s": s["p90"],
                  "max_s": s["max"], "by_kind": lby,
                  "market_event_reviews_without_a_change_instant": unknown,
                  "held_watch_records": ("in memory: the latest change "
                                         "instant per held slug; persisted: "
                                         "cumulative counters in the feed "
                                         "heartbeat only"),
                  "held_watch_heartbeat": hb}
    late = s["n"] - s["within"]
    out.append(metric(
        ids[2], names[2], value=v, numerator=s["within"], denominator=s["n"],
        sample=s["n"], ci=wilson(s["within"], s["n"]), unit="share",
        trend=trend_of(v, _share(ps["within"], ps["n"])),
        last_measured_at=cur["market_last"], min_sample=MIN_RATE_SAMPLE,
        why=None if s["n"] else "NO_MARKET_EVENT_REVIEW_WITH_A_CHANGE_INSTANT",
        detail=sla_detail,
        blocker=(("%d of %d reviewed later than %.0f s; " % (
            late, s["n"], MARKET_CHANGE_REVIEW_SLA_S)) if late else "")
        + SLA_LIMITATION,
        next_improvement="persist each held price change (slug, provider "
                         "instant) so a change that never reached a review "
                         "is counted"))
    out.append(metric(
        ids[3], names[3], value=s["median"], sample=s["n"], unit="s",
        ci=median_ci(lats),
        trend=trend_of(s["median"], ps["median"], higher_is_better=False),
        last_measured_at=cur["market_last"], min_sample=10,
        why=None if lats else "NO_MARKET_EVENT_REVIEW_WITH_A_CHANGE_INSTANT",
        detail={"p90_s": s["p90"], "max_s": s["max"],
                "sla_s": MARKET_CHANGE_REVIEW_SLA_S, "by_kind": lby},
        blocker=SLA_LIMITATION if lats else None,
        next_improvement="review a held market in the pass its change "
                         "arrives"))

    # 5. FRESHNESS AT DISCRETIONARY ACTION (100 % by rule)
    k, n = cur["disc_fresh"], cur["disc_n"]
    v = _share(k, n)
    xcheck = await paper_discretion_cross_check(conn, now - WINDOW_S, now)
    out.append(metric(
        ids[4], names[4], value=v, numerator=k, denominator=n, sample=n,
        ci=wilson(k, n), unit="share",
        trend=trend_of(v, _share(pri["disc_fresh"], pri["disc_n"])),
        last_measured_at=cur["last"],
        why=None if n else "NO_DISCRETIONARY_REVIEW_IN_WINDOW",
        detail={"rule": ("EXIT / REDUCE / REALLOCATE only on "
                         "FRESH_CURRENT_PROBABILITY (migration 206 CHECKs "
                         "it); expected 1.0"),
                "recommended": {"n": cur["rec_n"], "fresh": cur["rec_fresh"]},
                "permitted": {"n": cur["permitted_n"],
                              "fresh": cur["permitted_fresh"]},
                "paper_reviews_cross_check": xcheck},
        blocker=(None if (not n or k == n) and not (xcheck or {}).get(
            "not_fresh") else "a discretionary recommendation on a "
            "non-fresh probability: %d assessment(s), %s paper review(s)"
            % (n - k, (xcheck or {}).get("not_fresh"))),
        next_improvement="hold at 100 %: no discretion on stale evidence"))
    return out


async def paper_discretion_cross_check(conn, start, end) -> dict | None:
    """paper_xavier_reviews that RECOMMENDED EXIT / REDUCE, and how many of
    them were not on a fresh probability (expected 0)."""
    if not await _regclass(conn, "paper_xavier_reviews"):
        return None
    r = await conn.fetchrow(
        "SELECT count(*) AS n, count(*) FILTER (WHERE coalesce("
        " measure->>'evidence_state', '') <> $4) AS bad "
        "  FROM paper_xavier_reviews WHERE recommendation = ANY($3::text[]) "
        "   AND reviewed_at >= to_timestamp($1) "
        "   AND reviewed_at < to_timestamp($2)", start, end,
        list(DISCRETIONARY), FRESH)
    return {"recommended": int(r["n"]), "not_fresh": int(r["bad"])}


MONITORED_NAME = "Xavier: open positions with a review inside their due bound"


def monitored_split(positions: list) -> dict:
    """OPEN positions with a review inside their due bound, per book, from
    management_view's positions. Pure."""
    out = {"open": 0, "monitored": 0, "overdue": 0, "without_review": 0,
           "by_kind": {}}
    for p in positions:
        if p.get("state") != "OPEN":
            continue
        k = out["by_kind"].setdefault(p.get("position_kind"), {
            "open": 0, "monitored": 0, "overdue": 0, "without_review": 0})
        no_review = p.get("latest_review") is None
        overdue = bool(p.get("review_overdue")) and not no_review
        for d in (out, k):
            d["open"] += 1
            d["without_review"] += 1 if no_review else 0
            d["overdue"] += 1 if overdue else 0
            d["monitored"] += 0 if (no_review or overdue) else 1
    return out


async def positions_monitored_metric(conn, now: float) -> dict:
    from . import xavier_management as XM
    mid, name = "positions_monitored_share", MONITORED_NAME
    nxt = "review every open position before its cadence + grace lapses"
    view = await XM.management_view(conn, limit=MONITORED_VIEW_LIMIT, now=now)
    if view.get("status") == "UNAVAILABLE":
        return unavailable(mid, name, view.get("why") or "VIEW_UNAVAILABLE",
                           next_improvement=nxt)
    s = monitored_split(view.get("positions") or [])
    summ = view.get("summary") or {}
    n, k = s["open"], s["monitored"]
    per_kind = {}
    for p in view.get("positions") or []:
        per_kind[p.get("position_kind")] = per_kind.get(
            p.get("position_kind"), 0) + 1
    truncated = sorted(kd for kd, c in per_kind.items()
                       if c >= MONITORED_VIEW_LIMIT)
    return metric(
        mid, name, value=_share(k, n), numerator=k, denominator=n, sample=n,
        unit="share", last_measured_at=now,
        trend={"prior_value": None, "delta": None, "direction": None,
               "why": "POINT_IN_TIME_CENSUS_NO_PRIOR_WINDOW"},
        why=None if n else "NO_OPEN_POSITION",
        detail={"open_positions": n, "monitored": k,
                "reviews_overdue": summ.get("reviews_overdue"),
                "open_without_review": summ.get("open_without_review"),
                "by_kind": s["by_kind"],
                "due_bound": ("latest review + cadence + cadence x "
                              "REREVIEW_GRACE_FACTOR (management_view)"),
                "census": "every OPEN position management_view reads; not "
                          "a sample, so no interval",
                "view_truncated_for": truncated},
        blocker=None if not n or k == n else
        "%d open position(s) overdue, %d never reviewed" % (
            s["overdue"], s["without_review"]),
        next_improvement=nxt)


async def challenge_metrics(conn, now: float) -> list:
    out = []
    nxt = "every hypothesis peer-challenged by a different agent"
    if await _regclass(conn, "agent_finding_stages"):
        async def counts(s, e):
            r = await conn.fetchrow(
                "SELECT count(*) AS n, count(*) FILTER (WHERE "
                " outcome='REFUTED') AS k, max(at) AS last FROM "
                " agent_finding_stages WHERE stage='PEER_CHALLENGE' "
                " AND at >= to_timestamp($1) AND at < to_timestamp($2)", s, e)
            return int(r["k"]), int(r["n"]), _ep(r["last"])
        k, n, last = await counts(now - WINDOW_S, now)
        pk, pn, _ = await counts(now - 2 * WINDOW_S, now - WINDOW_S)
        v = round(k / n, 6) if n else None
        out.append(metric(
            "peer_challenge_refuted_share",
            "Peer challenges that refuted the hypothesis", value=v,
            numerator=k, denominator=n, sample=n, ci=wilson(k, n),
            unit="share", trend=trend_of(v, round(pk / pn, 6) if pn else None,
                                         higher_is_better=None),
            last_measured_at=last, min_sample=MIN_RATE_SAMPLE,
            why=None if n else "NO_PEER_CHALLENGE_IN_WINDOW",
            next_improvement=nxt))
    else:
        out.append(unavailable("peer_challenge_refuted_share",
                               "Peer challenges that refuted the hypothesis",
                               "MIGRATION_203_NOT_APPLIED",
                               next_improvement=nxt))
    try:
        karen = [r["relname"] for r in await conn.fetch(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
            "  ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
            "   AND c.relkind = 'r' AND c.relname LIKE 'karen%' "
            " ORDER BY 1")]
    except Exception:                                           # noqa: BLE001
        karen = []
    if not karen:
        out.append(unavailable(
            "karen_challenges", "KAREN challenge records",
            "KAREN_TABLES_NOT_PRESENT",
            next_improvement="KAREN's challenge records, when its migration "
                             "is applied"))
    else:
        per = {}
        for t in karen:
            per[t] = int(await conn.fetchval('SELECT count(*) FROM "%s"' % t))
        out.append(metric(
            "karen_challenges", "KAREN challenge records",
            value=sum(per.values()), sample=sum(per.values()),
            unit="records", last_measured_at=now, detail={"tables": per},
            next_improvement="score KAREN's challenges by outcome once its "
                             "schema names one"))
    return out


# ═════════════════════════════════════════════════════════════════════
# INVESTMENT INTELLIGENCE
# ═════════════════════════════════════════════════════════════════════

async def brier_rows(conn, start, end) -> list:
    rows = await conn.fetch(
        "SELECT probability AS p, executable_price AS b, outcome AS y, "
        "       outcome_at FROM external_valuations "
        " WHERE record_purpose = 'ENTRY_DECISION' AND outcome_known "
        "   AND outcome IN (0, 1) AND probability BETWEEN 0 AND 1 "
        "   AND executable_price BETWEEN 0 AND 1 "
        "   AND decided_at >= to_timestamp($1) "
        "   AND decided_at < to_timestamp($2)", start, end)
    return [dict(r) for r in rows]


def brier(rows: list) -> dict:
    """Paired Brier of model vs baseline. diff < 0: model better. Pure."""
    if not rows:
        return {"n": 0, "model": None, "baseline": None, "diff": None,
                "ci": None}
    m = [(r["p"] - r["y"]) ** 2 for r in rows]
    b = [(r["b"] - r["y"]) ** 2 for r in rows]
    d = [x - y for x, y in zip(m, b)]
    return {"n": len(rows), "model": round(statistics.fmean(m), 6),
            "baseline": round(statistics.fmean(b), 6),
            "diff": round(statistics.fmean(d), 6), "ci": mean_ci(d)}


async def calibration_metric(conn, now: float) -> dict:
    name = "Calibration: Brier(decision probability) - Brier(executable price)"
    nxt = ("forward-evaluate the probability against the price on settled "
           "ENTRY_DECISION valuations")
    if not await _regclass(conn, "external_valuations"):
        return unavailable("calibration_brier_skill", name,
                           "SOURCE_TABLE_ABSENT", next_improvement=nxt)
    cur = brier(await brier_rows(conn, now - WINDOW_S, now))
    pri = brier(await brier_rows(conn, now - 2 * WINDOW_S, now - WINDOW_S))
    return metric("calibration_brier_skill", name, value=cur["diff"],
                  sample=cur["n"], ci=cur["ci"], unit="brier (lower=better)",
                  trend=trend_of(cur["diff"], pri["diff"],
                                 higher_is_better=False),
                  last_measured_at=now, min_sample=MIN_RATE_SAMPLE,
                  detail={"brier_model": cur["model"],
                          "brier_baseline": cur["baseline"],
                          "baseline": "the executable price as a probability",
                          "negative_means": "the decision probability beat "
                                            "the price"},
                  why=None if cur["n"] else "NO_SETTLED_ENTRY_DECISION_IN_WINDOW",
                  blocker=(None if cur["ci"] is None or cur["ci"]["high"] < 0
                           else "no demonstrated skill over the price"),
                  next_improvement=nxt)


# ═════════════════════════════════════════════════════════════════════
# EXECUTION
# ═════════════════════════════════════════════════════════════════════

async def fill_rate_metrics(conn, now: float) -> list:
    out = []
    nxt = "price entries inside the observed depth at decision time"

    async def paper(s, e):
        r = await conn.fetchrow(
            "SELECT count(*) AS n, count(*) FILTER (WHERE filled_qty > 0) "
            "  AS k, max(terminal_at) AS last FROM paper_orders "
            " WHERE role='ENTRY' AND terminal_at >= to_timestamp($1) "
            "   AND terminal_at < to_timestamp($2)", s, e)
        return int(r["k"]), int(r["n"]), _ep(r["last"])
    if await _regclass(conn, "paper_orders"):
        k, n, last = await paper(now - WINDOW_S, now)
        pk, pn, _ = await paper(now - 2 * WINDOW_S, now - WINDOW_S)
        v = round(k / n, 6) if n else None
        out.append(metric(
            "paper_fill_rate", "Paper ENTRY fill rate (terminal orders)",
            value=v, numerator=k, denominator=n, sample=n, ci=wilson(k, n),
            unit="share", trend=trend_of(v, round(pk / pn, 6) if pn else None),
            last_measured_at=last, min_sample=MIN_RATE_SAMPLE,
            why=None if n else "NO_TERMINAL_ENTRY_ORDER_IN_WINDOW",
            next_improvement=nxt))
    else:
        out.append(unavailable("paper_fill_rate",
                               "Paper ENTRY fill rate (terminal orders)",
                               "SOURCE_TABLE_ABSENT"))

    async def actual(s, e):
        r = await conn.fetchrow(
            "SELECT count(*) AS n, count(*) FILTER (WHERE coalesce(cum_qty,0)"
            " > 0) AS k, max(updated_at) AS last FROM execmirror_orders "
            " WHERE venue_order_id IS NOT NULL "
            "   AND created_at >= to_timestamp($1) "
            "   AND created_at < to_timestamp($2)", s, e)
        return int(r["k"]), int(r["n"]), _ep(r["last"])
    if await _regclass(conn, "execmirror_orders"):
        k, n, last = await actual(now - WINDOW_S, now)
        pk, pn, _ = await actual(now - 2 * WINDOW_S, now - WINDOW_S)
        v = round(k / n, 6) if n else None
        out.append(metric(
            "actual_fill_rate", "Actual fill rate (orders the venue accepted)",
            value=v, numerator=k, denominator=n, sample=n, ci=wilson(k, n),
            unit="share", trend=trend_of(v, round(pk / pn, 6) if pn else None),
            last_measured_at=last, min_sample=MIN_RATE_SAMPLE,
            why=None if n else "NO_ACTUAL_ORDER_ACCEPTED_IN_WINDOW",
            next_improvement=nxt))
    else:
        out.append(unavailable("actual_fill_rate",
                               "Actual fill rate (orders the venue accepted)",
                               "SOURCE_TABLE_ABSENT"))
    return out


async def paper_slippage(conn, s, e) -> tuple:
    rows = await conn.fetch(
        "SELECT f.qty, f.price, f.filled_at, o.limit_price, "
        "       (d.economics->'acquisition'->>'vwap')::float8 AS vwap "
        "  FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id "
        "  LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id "
        " WHERE o.role = 'ENTRY' AND f.direction = 'BUY' "
        "   AND f.filled_at >= to_timestamp($1) "
        "   AND f.filled_at < to_timestamp($2)", s, e)
    xs, w, last = [], 0.0, None
    tot = 0.0
    for r in rows:
        ref = _f(r["vwap"]) if r["vwap"] is not None else _f(r["limit_price"])
        px, q = _f(r["price"]), _f(r["qty"]) or 0.0
        if ref is None or px is None:
            continue
        xs.append((px - ref) * 100.0)
        tot += (px - ref) * 100.0 * q
        w += q
        last = max(last or 0, _ep(r["filled_at"]) or 0)
    return xs, (round(tot / w, 6) if w else None), last


async def slippage_metric(conn, now: float) -> dict:
    name = "Paper entry slippage vs the decision's expected price"
    nxt = "decide on the book the order will meet (decision-to-fill delay)"
    if not await _regclass(conn, "paper_fills"):
        return unavailable("paper_slippage_cents", name, "SOURCE_TABLE_ABSENT",
                           next_improvement=nxt, unit="cents/contract")
    xs, v, last = await paper_slippage(conn, now - WINDOW_S, now)
    _, pv, _ = await paper_slippage(conn, now - 2 * WINDOW_S, now - WINDOW_S)
    return metric("paper_slippage_cents", name, value=v, sample=len(xs),
                  ci=mean_ci(xs), unit="cents/contract (+ = paid more)",
                  trend=trend_of(v, pv, higher_is_better=False),
                  last_measured_at=last, min_sample=MIN_RATE_SAMPLE,
                  detail={"weighting": "quantity-weighted value; CI on the "
                                       "unweighted per-fill values"},
                  why=None if xs else "NO_ENTRY_FILL_WITH_A_DECISION_PRICE",
                  next_improvement=nxt)


async def admission(conn, s, e) -> tuple:
    r = await conn.fetchrow(
        "SELECT count(*) FILTER (WHERE actual_state <> 'PAPER_ONLY') AS n, "
        "       count(*) FILTER (WHERE actual_state = 'REFUSED') AS k, "
        "       max(updated_at) AS last FROM execution_intents "
        " WHERE decided_at >= to_timestamp($1) "
        "   AND decided_at < to_timestamp($2)", s, e)
    top = await conn.fetch(
        "SELECT actual_refusal AS r, count(*) AS n FROM execution_intents "
        " WHERE actual_state = 'REFUSED' AND decided_at >= to_timestamp($1) "
        "   AND decided_at < to_timestamp($2) GROUP BY 1 ORDER BY 2 DESC "
        " LIMIT 5", s, e)
    return int(r["k"] or 0), int(r["n"] or 0), _ep(r["last"]), \
        {t["r"]: int(t["n"]) for t in top}


async def admission_metric(conn, now: float) -> dict:
    name = "Actual admission refusals (share of live-considered intents)"
    nxt = "remove the leading refusal reason at its source"
    if not await _regclass(conn, "execution_intents"):
        return unavailable("admission_refusal_share", name,
                           "SOURCE_TABLE_ABSENT", next_improvement=nxt)
    k, n, last, top = await admission(conn, now - WINDOW_S, now)
    pk, pn, _, _ = await admission(conn, now - 2 * WINDOW_S, now - WINDOW_S)
    v = round(k / n, 6) if n else None
    return metric("admission_refusal_share", name, value=v, numerator=k,
                  denominator=n, sample=n, ci=wilson(k, n), unit="share",
                  trend=trend_of(v, round(pk / pn, 6) if pn else None,
                                 higher_is_better=False),
                  last_measured_at=last, min_sample=MIN_RATE_SAMPLE,
                  detail={"top_refusals": top},
                  why=None if n else "NO_LIVE_CONSIDERED_INTENT_IN_WINDOW",
                  blocker=(next(iter(top)) if top else None),
                  next_improvement=nxt)


# ═════════════════════════════════════════════════════════════════════
# PROFITABILITY EVIDENCE
# ═════════════════════════════════════════════════════════════════════

#: the sleeves (migration 223); the forward-sample verdict that feeds
#: confidence counts the INVESTMENT sleeve only (R30A, owner audit P0 #5)
SLEEVES = ("INVESTMENT", "TRAINING", "BENCHMARK", "UNCLASSIFIED")
#: migration 223's strategy -> sleeve map (pinned equal to
#: bettor_paper_sleeves.STRATEGY_SLEEVE by tests/test_investment_only_
#: confidence.py), for an ACTUAL position whose paper group has no durable
#: classification
STRATEGY_SLEEVE = {
    "PINNACLE_COMPLETED_GAME_PAPER": "INVESTMENT",
    "DEREK_ENTRY_POLICY_V2": "INVESTMENT",
    "PINNACLE_EXPLORATION_PAPER": "TRAINING",
    "PINNACLE_ONLY_PAPER_BENCHMARK": "BENCHMARK",
    "PINNACLE_COMPLETED_GAME_MAKER_PAPER": "BENCHMARK",
}


async def _postmortem_rows(conn, book: str) -> list:
    """The book's closed positions with each one's sleeve: the group's
    durable classification (migration 223), else -- ACTUAL only -- the
    classifier's sleeve of the recorded strategy, else UNCLASSIFIED (never
    INVESTMENT)."""
    have = await _regclass(conn, "paper_sleeve_current_v")
    rows = await conn.fetch(
        "SELECT p.opened_at, p.closed_at, p.fixture, p.us_market_slug, "
        "       p.realized_pnl_usd, p.strategy, %s AS sleeve "
        "  FROM position_postmortems p %s WHERE p.book=$1"
        % (("s.sleeve", "LEFT JOIN paper_sleeve_current_v s "
                        "ON s.group_id = p.group_id") if have
           else ("NULL::text", "")), book)
    out = []
    for r in rows:
        sleeve = r["sleeve"] or (STRATEGY_SLEEVE.get(str(r["strategy"]))
                                 if book == "ACTUAL" else None)
        out.append({"opened_at": _ep(r["opened_at"]),
                    "closed_at": _ep(r["closed_at"]),
                    "fixture": r["fixture"],
                    "us_market_slug": r["us_market_slug"],
                    "realized_pnl_usd": float(r["realized_pnl_usd"]),
                    "sleeve": sleeve if sleeve in SLEEVES
                    else "UNCLASSIFIED"})
    return out


async def profitability_metrics(conn, now: float) -> list:
    """THE PREDECLARED FORWARD SAMPLE, PER BOOK -- INVESTMENT SLEEVE ONLY.

    THE DEFECT (owner audit 2026-10-04, P0 #5): the forward-sample verdict
    read every closed PAPER position, so a TRAINING (exploration) win or
    loss, or a BENCHMARK control arm's, moved the confidence statistic a
    live-capital decision reads. It now counts the INVESTMENT sleeve only;
    every other sleeve's verdict is computed separately and shown in
    `detail.other_sleeves` (research, never the metric's value)."""
    out = []
    nxt = ("accumulate the predeclared forward sample; nothing before "
           "%s counts" % FORWARD_SAMPLE_RULE["forward_start"])
    present = await _regclass(conn, "position_postmortems")
    for book in ("PAPER", "ACTUAL"):
        mid = "forward_sample_%s" % book.lower()
        name = ("Profitability evidence (%s, INVESTMENT sleeve): forward "
                "sample" % book)
        if not present:
            m = unavailable(mid, name, "MIGRATION_209_NOT_APPLIED",
                            next_improvement=nxt)
            m["value"] = FORWARD_SAMPLE_RULE["verdict_until_sufficient"]
            m["detail"] = {"verdict": m["value"], "rule": FORWARD_SAMPLE_RULE,
                           "sleeve": "INVESTMENT"}
            out.append(m)
            continue
        allrows = await _postmortem_rows(conn, book)
        rows = [r for r in allrows if r["sleeve"] == "INVESTMENT"]
        v = forward_verdict(rows, now=now)
        others = {s: forward_verdict([r for r in allrows
                                      if r["sleeve"] == s], now=now)
                  for s in SLEEVES if s != "INVESTMENT"}
        out.append(metric(
            mid, name, value=v["verdict"], numerator=v["positions"],
            denominator=v["required_positions"], sample=v["positions"],
            ci=None if v["one_sided_lower_95_usd"] is None else {
                "low": v["one_sided_lower_95_usd"], "high": None,
                "level": 0.95, "method": "ONE_SIDED_NORMAL_MEAN"},
            unit="verdict", last_measured_at=now,
            status="MEASURED" if v["sufficient"] else "INSUFFICIENT_SAMPLE",
            detail=dict(v, rule=FORWARD_SAMPLE_RULE, book=book,
                        sleeve="INVESTMENT",
                        confidence_scope="PRODUCTION_CONFIDENCE",
                        other_sleeves={
                            s: dict(o, confidence_scope=(
                                "RESEARCH_NOT_PRODUCTION_CONFIDENCE"))
                            for s, o in others.items()}),
            blocker=None if v["sufficient"] else (
                "%d of %d independent forward INVESTMENT positions; %.1f of "
                "%d days" % (v["positions"], v["required_positions"],
                             v["calendar_days"], v["required_days"])),
            next_improvement=nxt))
    return out


# ═════════════════════════════════════════════════════════════════════
# THE SCORECARD
# ═════════════════════════════════════════════════════════════════════

async def _safe(coro_fn, mids: list, names: list) -> list:
    try:
        got = await coro_fn()
        return got if isinstance(got, list) else [got]
    except Exception as exc:                                    # noqa: BLE001
        why = "READ_FAILED:%s" % type(exc).__name__
        return [unavailable(m, n, why) for m, n in zip(mids, names)]


async def scorecard(conn, *, now: float | None = None) -> dict:
    at = float(now if now is not None else time.time())
    domains = {
        "ENGINEERING": gate_metric() + await _safe(
            lambda: deploy_alignment(conn), ["deploy_sha_alignment"],
            ["Deploy SHA API == workers"]),
        "AGENTS": (
            await _safe(lambda: review_latency_metric(conn, at),
                        ["review_latency_s"], ["Xavier review latency"])
            + await _safe(lambda: fresh_share_metric(conn, at),
                          ["reviews_fresh_share"], [FRESH_SHARE_NAME])
            + await _safe(lambda: freshness_metrics(conn, at),
                          list(FRESHNESS_IDS), list(FRESHNESS_NAMES))
            + await _safe(lambda: positions_monitored_metric(conn, at),
                          ["positions_monitored_share"],
                          [MONITORED_NAME])
            + await _safe(lambda: challenge_metrics(conn, at),
                          ["peer_challenge_refuted_share",
                           "karen_challenges"],
                          ["Peer challenges", "KAREN challenge records"])),
        "INVESTMENT_INTELLIGENCE": await _safe(
            lambda: calibration_metric(conn, at), ["calibration_brier_skill"],
            ["Calibration"]),
        "EXECUTION": (
            await _safe(lambda: fill_rate_metrics(conn, at),
                        ["paper_fill_rate", "actual_fill_rate"],
                        ["Paper fill rate", "Actual fill rate"])
            + await _safe(lambda: slippage_metric(conn, at),
                          ["paper_slippage_cents"], ["Paper slippage"])
            + await _safe(lambda: admission_metric(conn, at),
                          ["admission_refusal_share"],
                          ["Admission refusals"])),
        "PROFITABILITY_EVIDENCE": await _safe(
            lambda: profitability_metrics(conn, at),
            ["forward_sample_paper", "forward_sample_actual"],
            ["Forward sample (PAPER)", "Forward sample (ACTUAL)"]),
    }
    counts: dict[str, int] = {}
    for ms in domains.values():
        for m in ms:
            counts[m["status"]] = counts.get(m["status"], 0) + 1
    prof = {m["id"]: m["value"] for m in domains["PROFITABILITY_EVIDENCE"]}
    return {"version": VERSION, "as_of": at, "window_s": WINDOW_S,
            "trend_basis": "the same metric over the prior window of equal "
                           "length",
            "domains": domains, "status_counts": counts,
            "profitability": {
                "paper": prof.get("forward_sample_paper"),
                "actual": prof.get("forward_sample_actual"),
                "rule": FORWARD_SAMPLE_RULE},
            "never_invented": ("an unmeasured metric is UNAVAILABLE with a "
                               "null value and its reason, never 0; an "
                               "under-sampled one is INSUFFICIENT_SAMPLE")}

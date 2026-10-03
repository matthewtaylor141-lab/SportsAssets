"""THE QUALITY SCORECARD: FIVE DOMAINS, EVERY FIGURE WITH ITS EVIDENCE.

  ENGINEERING               gate results and failing tests (from the latest
                            gate artifact this process can read, else
                            UNAVAILABLE); deploy SHA alignment API == workers
  AGENTS                    Xavier review latency; share of reviews made on a
                            fresh probability; peer-challenge metrics (the
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
#: Where a gate artifact may be read (file or glob of run_gate.sh's
#: `<prefix>_report.json`). Read only when the host sets it.
GATE_REPORT_ENV = "QUALITY_GATE_REPORT_PATH"

#: ── THE PREDECLARED INDEPENDENT FORWARD SAMPLE ─────────────────────────
#: Declared in code BEFORE any position it counts exists: only positions
#: whose entry is at or after `forward_start` count, so nothing used to
#: design the rule is in it. Profitability is UNPROVEN until it is met.
FORWARD_SAMPLE_RULE = {
    "id": "BETTOR_FORWARD_SAMPLE_V1",
    "declared_at": "2026-10-03T00:00:00Z",
    "forward_start": "2026-10-04T00:00:00Z",
    "population": ("closed positions reconstructed by Audrey's postmortems "
                   "(position_postmortems), PAPER and ACTUAL evaluated "
                   "separately, opened at or after forward_start"),
    "independence": ("one position per fixture: the first closed position "
                     "on a fixture counts; later positions on the same "
                     "fixture are dependent and excluded"),
    "min_positions": 300,
    "min_calendar_days": 30,
    "test": ("one-sided 95%: the lower bound of the mean realized net P&L "
             "per position (normal approximation, z=1.645) is above 0"),
    "verdict_until_sufficient": "UNPROVEN",
    "verdicts": ["UNPROVEN", "SUPPORTED_BY_FORWARD_SAMPLE",
                 "NOT_SUPPORTED_BY_FORWARD_SAMPLE"],
}


def _epoch_iso(s: str) -> float:
    return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


FORWARD_START = _epoch_iso(FORWARD_SAMPLE_RULE["forward_start"])


# ═════════════════════════════════════════════════════════════════════
# STATISTICS (pure)
# ═════════════════════════════════════════════════════════════════════

def wilson(k: int, n: int, z: float = 1.96) -> dict | None:
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"low": round(max(0.0, c - h), 6), "high": round(min(1.0, c + h),
                                                             6),
            "level": 0.95, "method": "WILSON"}


def mean_ci(xs: list, z: float = 1.96) -> dict | None:
    if len(xs) < 2:
        return None
    m = statistics.fmean(xs)
    se = statistics.stdev(xs) / math.sqrt(len(xs))
    return {"low": round(m - z * se, 6), "high": round(m + z * se, 6),
            "level": 0.95, "method": "NORMAL_MEAN"}


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


async def fresh_share_metric(conn, now: float) -> dict:
    name = "Reviews made on a fresh probability"
    nxt = ("keep the PinnAPI feed in scope for every held sport so the "
           "measure is current")
    if not await _regclass(conn, "paper_xavier_reviews"):
        return unavailable("reviews_fresh_share", name,
                           "SOURCE_TABLE_ABSENT", next_improvement=nxt)
    k, n, last = await fresh_share(conn, now - WINDOW_S, now)
    pk, pn, _ = await fresh_share(conn, now - 2 * WINDOW_S, now - WINDOW_S)
    v = round(k / n, 6) if n else None
    pv = round(pk / pn, 6) if pn else None
    return metric("reviews_fresh_share", name, value=v, numerator=k,
                  denominator=n, sample=n, ci=wilson(k, n), unit="share",
                  trend=trend_of(v, pv), last_measured_at=last,
                  min_sample=MIN_RATE_SAMPLE,
                  why=None if n else "NO_REVIEWS_IN_WINDOW",
                  blocker=None if v is None or v >= 0.9 else
                  "%d of %d reviews were made on a stale or unavailable "
                  "probability" % (n - k, n), next_improvement=nxt)


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

def forward_verdict(rows: list, *, now: float,
                    rule: dict = FORWARD_SAMPLE_RULE) -> dict:
    """Apply the predeclared rule to closed-position rows (dicts with
    opened_at, closed_at, fixture/us_market_slug, realized_pnl_usd). Pure."""
    start = _epoch_iso(rule["forward_start"])
    seen, sample = set(), []
    for r in sorted(rows, key=lambda x: (x.get("closed_at") or 0)):
        if (r.get("opened_at") or 0) < start:
            continue
        fx = r.get("fixture") or r.get("us_market_slug")
        if fx in seen:
            continue
        seen.add(fx)
        sample.append(float(r["realized_pnl_usd"]))
    days = max(0.0, (now - start) / 86400.0)
    sufficient = (len(sample) >= rule["min_positions"]
                  and days >= rule["min_calendar_days"])
    m = round(statistics.fmean(sample), 6) if sample else None
    lower = None
    if len(sample) >= 2:
        lower = round(m - 1.645 * statistics.stdev(sample)
                      / math.sqrt(len(sample)), 6)
    if not sufficient:
        verdict = rule["verdict_until_sufficient"]
    else:
        verdict = ("SUPPORTED_BY_FORWARD_SAMPLE" if lower is not None
                   and lower > 0 else "NOT_SUPPORTED_BY_FORWARD_SAMPLE")
    return {"verdict": verdict, "sufficient": sufficient,
            "positions": len(sample), "required_positions":
            rule["min_positions"], "calendar_days": round(days, 2),
            "required_days": rule["min_calendar_days"],
            "mean_pnl_per_position_usd": m,
            "one_sided_lower_95_usd": lower, "rule_id": rule["id"]}


async def profitability_metrics(conn, now: float) -> list:
    out = []
    nxt = ("accumulate the predeclared forward sample; nothing before "
           "%s counts" % FORWARD_SAMPLE_RULE["forward_start"])
    present = await _regclass(conn, "position_postmortems")
    for book in ("PAPER", "ACTUAL"):
        mid = "forward_sample_%s" % book.lower()
        name = "Profitability evidence (%s): forward sample" % book
        if not present:
            m = unavailable(mid, name, "MIGRATION_209_NOT_APPLIED",
                            next_improvement=nxt)
            m["value"] = FORWARD_SAMPLE_RULE["verdict_until_sufficient"]
            m["detail"] = {"verdict": m["value"], "rule": FORWARD_SAMPLE_RULE}
            out.append(m)
            continue
        rows = [{"opened_at": _ep(r["opened_at"]),
                 "closed_at": _ep(r["closed_at"]),
                 "fixture": r["fixture"], "us_market_slug": r["us_market_slug"],
                 "realized_pnl_usd": float(r["realized_pnl_usd"])}
                for r in await conn.fetch(
                    "SELECT opened_at, closed_at, fixture, us_market_slug, "
                    "       realized_pnl_usd FROM position_postmortems "
                    " WHERE book=$1", book)]
        v = forward_verdict(rows, now=now)
        out.append(metric(
            mid, name, value=v["verdict"], numerator=v["positions"],
            denominator=v["required_positions"], sample=v["positions"],
            ci=None if v["one_sided_lower_95_usd"] is None else {
                "low": v["one_sided_lower_95_usd"], "high": None,
                "level": 0.95, "method": "ONE_SIDED_NORMAL_MEAN"},
            unit="verdict", last_measured_at=now,
            status="MEASURED" if v["sufficient"] else "INSUFFICIENT_SAMPLE",
            detail=dict(v, rule=FORWARD_SAMPLE_RULE),
            blocker=None if v["sufficient"] else (
                "%d of %d independent forward positions; %.1f of %d days"
                % (v["positions"], v["required_positions"],
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
                          ["reviews_fresh_share"],
                          ["Reviews made on a fresh probability"])
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

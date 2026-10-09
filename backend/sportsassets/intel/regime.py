"""K · REGIME DETECTION (SHADOW). Is the market, the feed or our own
machinery behaving differently from its recent baseline?

SEVEN SIGNALS, each compared RECENT (last RECENT_S) against BASELINE (the
BASELINE_S before that), each OK / DEGRADED / SEVERE, or UNMEASURED with a
named reason when either window has too little evidence:

  spread             median top-of-book spread (paper_book_observations)
  liquidity          median top-5 displayed depth, USD
  pinnapi_volatility mean |change| between consecutive de-vigged
                     probabilities of one contract (external_valuations)
  feed_latency       provider-stamp -> receipt p95 from the PinnAPI feed's
                     heartbeat (ingestion_state 'pinnapi_feed_last'), and
                     the median valuation quote age
  feed_gaps          heartbeat age, and the longest silence between
                     valuations against the baseline's inter-arrival p95
  mapping            the refusal-code mix of valuations (total variation
                     distance) and the share refused for mapping/identity
  calibration_drift  recent vs earlier Brier (intel/calibration.drift)
  execution          paper ENTRY fill rate and attribution slippage

THE RECOMMENDATION: any SEVERE -> NO_TRADE; any DEGRADED -> REDUCE; at
least one measured signal and none degraded -> NORMAL; nothing measurable
-> REDUCE (absence of evidence is not a normal regime).

IT IS A RECOMMENDATION WITH NO AUTHORITY. Nothing reads it to change a
limit, a size or a threshold; migration 208 stores applied = false only.
"""
from __future__ import annotations

from . import common as C

VERSION = "INTEL_REGIME_V1"
RECENT_S = 6 * 3600.0
BASELINE_S = 7 * 86400.0
MIN_N = 20
MAPPING_TOKENS = ("MAPPING", "IDENTITY", "MATCH", "RESOLVER", "CONTRACT")

THRESHOLDS = {
    "spread": {"ratio_degraded": 1.5, "ratio_severe": 2.5},
    "liquidity": {"ratio_degraded": 0.5, "ratio_severe": 0.25},
    "pinnapi_volatility": {"ratio_degraded": 2.0, "ratio_severe": 3.0},
    "feed_latency": {"p95_ms_degraded": 5000.0, "p95_ms_severe": 15000.0,
                     "age_ratio_degraded": 2.0, "age_ratio_severe": 4.0},
    "feed_gaps": {"heartbeat_age_s_degraded": 90.0,
                  "heartbeat_age_s_severe": 600.0,
                  "gap_ratio_degraded": 3.0, "gap_ratio_severe": 10.0},
    "mapping": {"tvd_degraded": 0.3, "tvd_severe": 0.5,
                "share_rise_degraded": 0.2, "share_rise_severe": 0.4},
    "calibration_drift": {"brier_rise_degraded": 0.03,
                          "brier_rise_severe": 0.06, "min_n": 30},
    "execution": {"fill_rate_drop_degraded": 0.25,
                  "fill_rate_drop_severe": 0.5,
                  "slippage_rise_degraded": 0.01,
                  "slippage_rise_severe": 0.03},
}
RANK = {"OK": 0, "DEGRADED": 1, "SEVERE": 2}


def _sig(name, status, reasons, **values) -> dict:
    out = C.Out(signal=name, status=status, reasons=list(reasons))
    for k, v in values.items():
        if isinstance(v, tuple):
            out.put(k, v[0], v[1])
        else:
            out[k] = v
    return out


def unmeasured(name, why, **values) -> dict:
    s = _sig(name, "UNMEASURED", [why], **values)
    s["unmeasured"]["status"] = why
    return s


def _worst(*pairs):
    st, why = "OK", []
    for status, reason in pairs:
        if status is None:
            continue
        if RANK[status] > RANK[st]:
            st = status
        if status != "OK":
            why.append(reason)
    return st, why


def ratio_signal(name, recent: list, baseline: list, *, higher_is_worse,
                 degraded, severe, unit) -> dict:
    if len(recent) < MIN_N or len(baseline) < MIN_N:
        return unmeasured(name, "TOO_FEW_OBSERVATIONS_RECENT_%d_BASELINE_%d"
                          "_MIN_%d" % (len(recent), len(baseline), MIN_N),
                          recent_n=len(recent), baseline_n=len(baseline))
    r, b = C.median(recent), C.median(baseline)
    if b is None or b <= 0:
        return unmeasured(name, "BASELINE_MEDIAN_IS_ZERO", recent=C.rnd(r),
                          baseline=C.rnd(b))
    ratio = r / b
    if higher_is_worse:
        st = ("SEVERE" if ratio >= severe else "DEGRADED"
              if ratio >= degraded else "OK")
    else:
        st = ("SEVERE" if ratio <= severe else "DEGRADED"
              if ratio <= degraded else "OK")
    why = [] if st == "OK" else ["%s_%s_RATIO_%.2f" % (name.upper(), unit,
                                                       ratio)]
    return _sig(name, st, why, recent=C.rnd(r), baseline=C.rnd(b),
                ratio=C.rnd(ratio), recent_n=len(recent),
                baseline_n=len(baseline))


def split(rows, *, now, key="at"):
    rcut = float(now) - RECENT_S
    bcut = rcut - BASELINE_S
    rec = [r for r in rows if (r.get(key) or 0) >= rcut]
    base = [r for r in rows if bcut <= (r.get(key) or 0) < rcut]
    return rec, base


def book_signals(obs: list, *, now) -> list:
    """obs: [{at, bids, offers}] -> spread and liquidity signals."""
    views = [dict(C.book_view(o.get("bids"), o.get("offers")), at=o["at"])
             for o in obs]
    rec, base = split(views, now=now)
    t = THRESHOLDS
    return [
        ratio_signal("spread", [v["spread"] for v in rec
                                if v["spread"] is not None],
                     [v["spread"] for v in base if v["spread"] is not None],
                     higher_is_worse=True,
                     degraded=t["spread"]["ratio_degraded"],
                     severe=t["spread"]["ratio_severe"], unit="WIDENING"),
        ratio_signal("liquidity", [v["top5_depth_usd"] for v in rec
                                   if v["bids"] or v["offers"]],
                     [v["top5_depth_usd"] for v in base
                      if v["bids"] or v["offers"]],
                     higher_is_worse=False,
                     degraded=t["liquidity"]["ratio_degraded"],
                     severe=t["liquidity"]["ratio_severe"], unit="DEPTH"),
    ]


def volatility_signal(vals: list, *, now) -> dict:
    """vals: [{key, at, p}] -> mean |dp| between consecutive quotes."""
    series: dict = {}
    for v in sorted(vals, key=lambda x: x["at"]):
        if v.get("p") is None:
            continue
        series.setdefault(v["key"], []).append(v)
    moves = []
    for s in series.values():
        for a, b in zip(s, s[1:]):
            moves.append({"at": b["at"], "dp": abs(b["p"] - a["p"])})
    rec, base = split(moves, now=now)
    t = THRESHOLDS["pinnapi_volatility"]
    if len(rec) < MIN_N or len(base) < MIN_N:
        return unmeasured("pinnapi_volatility",
                          "TOO_FEW_CONSECUTIVE_QUOTES_RECENT_%d_BASELINE_%d"
                          % (len(rec), len(base)),
                          recent_n=len(rec), baseline_n=len(base))
    r = C.mean([m["dp"] for m in rec])
    b = C.mean([m["dp"] for m in base])
    if not b:
        st = "SEVERE" if r and r > 0.05 else "OK"
        return _sig("pinnapi_volatility", st,
                    [] if st == "OK" else ["BASELINE_STATIC_RECENT_MOVING"],
                    recent=C.rnd(r), baseline=C.rnd(b),
                    ratio=(None, "BASELINE_MEAN_MOVE_IS_ZERO"))
    ratio = r / b
    st = ("SEVERE" if ratio >= t["ratio_severe"] else "DEGRADED"
          if ratio >= t["ratio_degraded"] else "OK")
    return _sig("pinnapi_volatility", st,
                [] if st == "OK" else ["PINNAPI_MEAN_MOVE_RATIO_%.2f" % ratio],
                recent=C.rnd(r), baseline=C.rnd(b), ratio=C.rnd(ratio),
                recent_n=len(rec), baseline_n=len(base))


def feed_signals(heartbeat, vals: list, *, now) -> list:
    """heartbeat: the 'pinnapi_feed_last' value or None; vals: [{at, age}]."""
    t1, t2 = THRESHOLDS["feed_latency"], THRESHOLDS["feed_gaps"]
    hb = C.jload(heartbeat) if heartbeat is not None else None
    beat = C.num(hb.get("beat_at")) if isinstance(hb, dict) else None
    p95 = None
    if isinstance(hb, dict):
        cache = hb.get("cache") if isinstance(hb.get("cache"), dict) else {}
        lat = cache.get("provider_stamp_to_receipt_ms") or {}
        p95 = C.num(lat.get("p95")) if isinstance(lat, dict) else None
    rec, base = split(vals, now=now)
    ages_r = [v["age"] for v in rec if v.get("age") is not None]
    ages_b = [v["age"] for v in base if v.get("age") is not None]
    # ── latency
    parts = []
    st_p95 = None
    if p95 is not None:
        st_p95 = ("SEVERE" if p95 >= t1["p95_ms_severe"] else "DEGRADED"
                  if p95 >= t1["p95_ms_degraded"] else "OK")
        parts.append((st_p95, "FEED_P95_MS_%.0f" % p95))
    age_ratio = None
    if len(ages_r) >= MIN_N and len(ages_b) >= MIN_N and C.median(ages_b):
        age_ratio = C.median(ages_r) / C.median(ages_b)
        parts.append((("SEVERE" if age_ratio >= t1["age_ratio_severe"] else
                       "DEGRADED" if age_ratio >= t1["age_ratio_degraded"]
                       else "OK"), "QUOTE_AGE_RATIO_%.2f" % age_ratio))
    if not parts:
        lat_sig = unmeasured(
            "feed_latency", "NO_FEED_HEARTBEAT_LATENCY_AND_TOO_FEW_QUOTE_AGES",
            provider_to_receipt_p95_ms=(None, "NO_FEED_HEARTBEAT_LATENCY"),
            quote_age_ratio=(None, "TOO_FEW_QUOTE_AGES"))
    else:
        st, why = _worst(*parts)
        lat_sig = _sig("feed_latency", st, why,
                       provider_to_receipt_p95_ms=(
                           p95, "NO_FEED_HEARTBEAT_LATENCY"),
                       quote_age_ratio=(C.rnd(age_ratio),
                                        "TOO_FEW_QUOTE_AGES"))
    # ── gaps
    parts = []
    hb_age = (float(now) - beat) if beat is not None else None
    if hb_age is not None:
        parts.append((("SEVERE" if hb_age >= t2["heartbeat_age_s_severe"]
                       else "DEGRADED" if hb_age
                       >= t2["heartbeat_age_s_degraded"] else "OK"),
                      "FEED_HEARTBEAT_AGE_S_%.0f" % hb_age))
    arrivals_b = sorted(v["at"] for v in base)
    gaps_b = [b - a for a, b in zip(arrivals_b, arrivals_b[1:])]
    gap_ratio = None
    if len(gaps_b) >= MIN_N:
        gaps_b.sort()
        p95b = gaps_b[int(0.95 * (len(gaps_b) - 1))]
        arrivals_r = sorted(v["at"] for v in rec)
        pts = ([float(now) - RECENT_S] + arrivals_r + [float(now)])
        longest = max(b - a for a, b in zip(pts, pts[1:]))
        if p95b > 0:
            gap_ratio = longest / p95b
            parts.append((("SEVERE" if gap_ratio >= t2["gap_ratio_severe"]
                           else "DEGRADED" if gap_ratio
                           >= t2["gap_ratio_degraded"] else "OK"),
                          "VALUATION_SILENCE_RATIO_%.1f" % gap_ratio))
    if not parts:
        gap_sig = unmeasured(
            "feed_gaps", "NO_FEED_HEARTBEAT_AND_TOO_FEW_BASELINE_ARRIVALS",
            heartbeat_age_s=(None, "NO_FEED_HEARTBEAT_ROW"),
            silence_ratio=(None, "TOO_FEW_BASELINE_ARRIVALS"))
    else:
        st, why = _worst(*parts)
        gap_sig = _sig("feed_gaps", st, why,
                       heartbeat_age_s=(C.rnd(hb_age),
                                        "NO_FEED_HEARTBEAT_ROW"),
                       silence_ratio=(C.rnd(gap_ratio),
                                      "TOO_FEW_BASELINE_ARRIVALS"))
    return [lat_sig, gap_sig]


def _mix(rows):
    tot: dict = {}
    for r in rows:
        codes = r.get("refusals") or ["ADMITTED"]
        for c in codes:
            tot[c] = tot.get(c, 0) + 1.0 / len(codes)
    n = sum(tot.values())
    return {k: v / n for k, v in tot.items()} if n else {}


def _mapping_share(rows):
    if not rows:
        return None
    hit = sum(1 for r in rows if any(any(t in str(c).upper()
                                         for t in MAPPING_TOKENS)
                                     for c in (r.get("refusals") or [])))
    return hit / len(rows)


def mapping_signal(vals: list, *, now) -> dict:
    """vals: [{at, refusals}] -> refusal-mix shift."""
    rec, base = split(vals, now=now)
    if len(rec) < MIN_N or len(base) < MIN_N:
        return unmeasured("mapping", "TOO_FEW_VALUATIONS_RECENT_%d_BASELINE"
                          "_%d" % (len(rec), len(base)),
                          recent_n=len(rec), baseline_n=len(base))
    a, b = _mix(rec), _mix(base)
    tvd = 0.5 * sum(abs(a.get(k, 0) - b.get(k, 0)) for k in set(a) | set(b))
    sr, sb = _mapping_share(rec), _mapping_share(base)
    t = THRESHOLDS["mapping"]
    st, why = _worst(
        (("SEVERE" if tvd >= t["tvd_severe"] else "DEGRADED"
          if tvd >= t["tvd_degraded"] else "OK"),
         "REFUSAL_MIX_TVD_%.2f" % tvd),
        (("SEVERE" if sr - sb >= t["share_rise_severe"] else "DEGRADED"
          if sr - sb >= t["share_rise_degraded"] else "OK"),
         "MAPPING_REFUSAL_SHARE_ROSE_%.2f" % (sr - sb)))
    top = sorted(a.items(), key=lambda kv: -kv[1])[:8]
    return _sig("mapping", st, why, total_variation_distance=C.rnd(tvd),
                mapping_share_recent=C.rnd(sr),
                mapping_share_baseline=C.rnd(sb),
                recent_mix_top=[{"code": k, "share": C.rnd(v)}
                                for k, v in top],
                recent_n=len(rec), baseline_n=len(base))


def calibration_signal(cal_report: dict | None) -> dict:
    if not cal_report or not cal_report.get("drift"):
        return unmeasured("calibration_drift", "NO_CALIBRATION_REPORT_THIS_"
                          "CYCLE")
    t = THRESHOLDS["calibration_drift"]
    for src in ("DECISION_P_PINNACLE", "VALUATION_PROBABILITY"):
        d = cal_report["drift"].get(src) or {}
        if (d.get("brier_change") is not None
                and d.get("recent_n", 0) >= t["min_n"]
                and d.get("baseline_n", 0) >= t["min_n"]):
            ch = d["brier_change"]
            st = ("SEVERE" if ch >= t["brier_rise_severe"] else "DEGRADED"
                  if ch >= t["brier_rise_degraded"] else "OK")
            return _sig("calibration_drift", st,
                        [] if st == "OK" else ["BRIER_ROSE_%.3f_%s" % (ch,
                                                                       src)],
                        source=src, brier_change=ch,
                        recent_brier=d.get("recent_brier"),
                        baseline_brier=d.get("baseline_brier"),
                        recent_n=d.get("recent_n"),
                        baseline_n=d.get("baseline_n"))
    return unmeasured("calibration_drift",
                      "FEWER_THAN_%d_RESOLVED_PREDICTIONS_ON_A_SIDE"
                      % t["min_n"])


def execution_signal(orders: list, attributions: list, *, now) -> dict:
    """orders: [{at, filled}] terminal paper ENTRY orders; attributions:
    attribution rows (decided_at, slippage_pc)."""
    t = THRESHOLDS["execution"]
    rec, base = split(orders, now=now)
    parts = []
    vals = {}
    if len(rec) >= MIN_N and len(base) >= MIN_N:
        fr = sum(1 for o in rec if o["filled"]) / len(rec)
        fb = sum(1 for o in base if o["filled"]) / len(base)
        drop = fb - fr
        vals["fill_rate_recent"] = C.rnd(fr)
        vals["fill_rate_baseline"] = C.rnd(fb)
        parts.append((("SEVERE" if drop >= t["fill_rate_drop_severe"] else
                       "DEGRADED" if drop >= t["fill_rate_drop_degraded"]
                       else "OK"), "FILL_RATE_DROPPED_%.2f" % drop))
    else:
        vals["fill_rate_recent"] = (None, "TOO_FEW_TERMINAL_ENTRY_ORDERS")
    sl = [{"at": a.get("decided_at"), "s": a.get("slippage_pc")}
          for a in attributions if a.get("slippage_pc") is not None
          and a.get("book") == "PAPER"]
    sr, sb = split(sl, now=now)
    if len(sr) >= MIN_N and len(sb) >= MIN_N:
        rise = C.mean([x["s"] for x in sr]) - C.mean([x["s"] for x in sb])
        vals["slippage_rise_pc"] = C.rnd(rise)
        parts.append((("SEVERE" if rise >= t["slippage_rise_severe"] else
                       "DEGRADED" if rise >= t["slippage_rise_degraded"]
                       else "OK"), "SLIPPAGE_ROSE_%.4f" % rise))
    else:
        vals["slippage_rise_pc"] = (None, "TOO_FEW_ATTRIBUTED_FILLS")
    if not parts:
        return unmeasured("execution", "TOO_FEW_ORDERS_AND_FILLS", **vals)
    st, why = _worst(*parts)
    return _sig("execution", st, why, **vals)


def recommend(signals: list) -> dict:
    measured = [s for s in signals if s["status"] != "UNMEASURED"]
    severe = [s for s in measured if s["status"] == "SEVERE"]
    degraded = [s for s in measured if s["status"] == "DEGRADED"]
    if severe:
        rec = "NO_TRADE"
        reasons = [r for s in severe for r in s["reasons"]] + [
            r for s in degraded for r in s["reasons"]]
    elif degraded:
        rec = "REDUCE"
        reasons = [r for s in degraded for r in s["reasons"]]
    elif measured:
        rec = "NORMAL"
        reasons = ["ALL_%d_MEASURED_SIGNALS_WITHIN_BASELINE" % len(measured)]
    else:
        rec = "REDUCE"
        reasons = ["NO_REGIME_SIGNAL_MEASURABLE_ABSENCE_OF_EVIDENCE_IS_NOT_"
                   "A_NORMAL_REGIME"]
    return {"recommendation": rec, "reasons": reasons,
            "measured_signals": len(measured),
            "unmeasured_signals": [s["signal"] for s in signals
                                   if s["status"] == "UNMEASURED"],
            "authority": C.AUTHORITY, "applied": False,
            "autonomous_limit_changes": False}


def report(signals: list, *, now) -> dict:
    out = C.envelope(version=VERSION, computed_at=now, signals=signals,
                     thresholds=THRESHOLDS, recent_s=RECENT_S,
                     baseline_s=BASELINE_S, min_n=MIN_N)
    out.update(recommend(signals))
    return out


# ═════════════════════════════════════════════════════════════════════
# THE READ
# ═════════════════════════════════════════════════════════════════════

def detect(obs, vrows, heartbeat, orders, cal_report, attributions, *,
           now) -> dict:
    """Every signal and the recommendation from already-read rows. Pure."""
    signals = book_signals(obs, now=now)
    signals.append(volatility_signal(vrows, now=now))
    signals.extend(feed_signals(heartbeat, vrows, now=now))
    signals.append(mapping_signal(vrows, now=now))
    signals.append(calibration_signal(cal_report))
    signals.append(execution_signal(orders, attributions, now=now))
    return report(signals, now=now)


WINDOW_ROWS = 8000                 # per window, so a busy recent window
MAX_SLUGS = 300                    # cannot crowd out the baseline


async def load_and_detect(conn, *, now, cal_report=None, attributions=None,
                          account_id=C.PAPER_ACCOUNT, experiment_id=None,
                          slug_prefix=None) -> dict:
    """Each window is read separately with its own LIMIT, so a busy recent
    window cannot crowd the baseline out of a single bounded read. Books are
    read for the markets the paper book decided on in the lookback (the
    (us_market_slug, observed_at) index), or for `slug_prefix` (tests)."""
    now = float(now)
    rcut = now - RECENT_S
    bcut = rcut - BASELINE_S
    windows = ((rcut, now), (bcut, rcut))
    if slug_prefix is None:
        slugs = [r["us_market_slug"] for r in await conn.fetch(
            "SELECT us_market_slug, max(decided_at) AS at "
            "  FROM paper_decisions WHERE decided_at >= to_timestamp($1) "
            "   AND us_market_slug IS NOT NULL "
            "   AND ($2::text IS NULL OR account_id = $2) "
            " GROUP BY us_market_slug ORDER BY at DESC LIMIT $3",
            bcut, account_id, MAX_SLUGS)]
    else:
        slugs = None
    obs = []
    for lo, hi in windows:
        obs += [{"at": float(r["at"]), "bids": r["bids"],
                 "offers": r["offers"]} for r in await conn.fetch(
            "SELECT extract(epoch FROM observed_at)::float8 AS at, bids, "
            "       offers FROM paper_book_observations "
            " WHERE observed_at >= to_timestamp($1) "
            "   AND observed_at < to_timestamp($2) AND error IS NULL "
            "   AND (($3::text[] IS NOT NULL AND us_market_slug = ANY($3)) "
            "        OR ($4::text IS NOT NULL "
            "            AND us_market_slug LIKE $4 || '%')) "
            " ORDER BY observed_at DESC LIMIT $5",
            lo, hi, slugs, slug_prefix, WINDOW_ROWS)]
    vrows = []
    for lo, hi in windows:
        vrows += [dict(r) for r in await conn.fetch(
            "SELECT coalesce(event_key, '') || '|' || coalesce(payout_event, "
            "       contract_selection, '') || '|' || version AS key, "
            "       extract(epoch FROM decided_at)::float8 AS at, "
            "       probability AS p, age_s AS age, refusals "
            "  FROM external_valuations "
            " WHERE decided_at >= to_timestamp($1) "
            "   AND decided_at < to_timestamp($2) "
            "   AND ($3::text IS NULL OR experiment_id = $3) "
            " ORDER BY decided_at DESC LIMIT $4", lo, hi, experiment_id,
            WINDOW_ROWS)]
    for v in vrows:
        v["refusals"] = list(v.get("refusals") or [])
    hb = await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key = 'pinnapi_feed_last'")
    orders = []
    for lo, hi in windows:
        orders += [{"at": float(r["at"]), "filled": bool(r["filled"])}
                   for r in await conn.fetch(
            "SELECT extract(epoch FROM coalesce(terminal_at, "
            "       updated_at))::float8 AS at, filled_qty > 0 AS filled"
            "  FROM paper_orders WHERE role = 'ENTRY' "
            "   AND state IN ('FILLED','EXPIRED','CANCELED','REJECTED',"
            "                 'PARTIALLY_FILLED') "
            "   AND coalesce(terminal_at, updated_at) >= to_timestamp($1)"
            "   AND coalesce(terminal_at, updated_at) < to_timestamp($2)"
            "   AND ($3::text IS NULL OR account_id = $3) "
            " ORDER BY coalesce(terminal_at, updated_at) DESC LIMIT $4",
            lo, hi, account_id, WINDOW_ROWS)]
    out = await C.offload(detect, obs, vrows, hb, orders, cal_report,
                          attributions or [], now=now)
    out["book_markets"] = ("PREFIX:%s" % slug_prefix if slugs is None
                           else len(slugs))
    out["window_rows_cap"] = WINDOW_ROWS
    return out

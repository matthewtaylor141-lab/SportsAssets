#!/usr/bin/env python3
"""RUN 85 PHASE 2C -- diversity / selection expansion. READ ONLY. NO CREDENTIAL.

Bounded discovery. No profitability analysis of any kind: nothing here computes
an expectancy, a fill, or a fee-adjusted figure, and the only economic quantity
recorded is DISPLAYED_SPREAD.

Every venue call goes through run85_pmus_collector._get under the authorized
boundary -- GET only, public gateway only, no credential, no signer, no order
path -- paced by the Phase 2B AdaptivePacer at 0.5 rps with exact Retry-After.
No attempt is made to discover the rate limit.

--------------------------------------------------------------------------
THE CADENCE ARITHMETIC, WHICH CONSTRAINS EVERYTHING BELOW
--------------------------------------------------------------------------
At the proven 0.5 rps aggregate ceiling one request costs 2.0 s, so a
round-robin cohort of N markets revisits each every 2.0 x N seconds:

    N = 1 ->  2.0 s     N = 4 ->  8.0 s
    N = 2 ->  4.0 s     N = 5 -> 10.0 s
    N = 3 ->  6.0 s     N = 6 -> 12.0 s

A markout at offset T needs revisit <= T. So the 5 s markout survives only at
N <= 2, and N = 2 is exactly the cohort Phase 2B showed to be too narrow to be
diverse. Diversity and the 5 s offset are in direct competition, and no choice
of bins can make that go away.

This run therefore prices BOTH horns and proposes the TIERED design that Phase
2B already proved runs: a small FAST tier that keeps the short offsets, plus a
wider SURVEY tier that carries the regime diversity at a longer revisit. The
alternative -- one flat cohort -- is reported beside it rather than hidden.

--------------------------------------------------------------------------
THE SELECTION RULE, FROZEN HERE BEFORE ANY DATA IS SEEN
--------------------------------------------------------------------------
ADMISSION (all must hold, else EXCLUDE with a named reason):
  A1 native event id AND native event slug from /v1/events
  A2 market slug present
  A3 structured sports evidence, read as a field, never a substring
  A4 marketSides shows exactly two sides, exactly one long and one short
  A5 state is open
  A6 the book carries BOTH a bid and an ask
  A7 the venue supplies orderPriceMinTickSize for the market

REGIME KEY, from observed wire data only, binned by quantiles of the
OBSERVED candidate distribution (never by thresholds imported from elsewhere):
  price_bin    terciles of mid
  spread_bin   spread measured in the market's OWN ticks: 1, 2-3, 4+
  depth_bin    terciles of min(touch bid notional, touch ask notional)
  asym_bin     log10(ask displayed notional / bid displayed notional):
               BID_HEAVY < -0.5, BALANCED -0.5..0.5, ASK_HEAVY > 0.5
  tte_bin      time to event: <24h, 1-7d, >7d, UNKNOWN
  act_bin      book content changed across the census passes: QUIET / ACTIVE

SELECTION, deterministic, no discretion at any step:
  S1 group candidates by full regime key
  S2 order regime groups by descending group size, then by key string
  S3 take one market per regime group, the market being the
     lexicographically smallest slug in that group
  S4 at most one market per native event
  S5 asymmetry coverage is forced BEFORE size-ranked filling: if any of
     BID_HEAVY / BALANCED / ASK_HEAVY exists among candidates and is not yet
     represented, its lexicographically smallest candidate is taken first
     (Phase 2B measured 27-30x ask-heaviness and the specification requires
     side competition to be a selection dimension, not an accident)
  S6 stop at the tier's cap

Nothing is hand-picked, and no market is chosen or rejected by looking at any
later measurement.

Usage: python3 run85_phase2c.py --out DIR [--detail-probe 150]
                                [--census-passes 3] [--validate-seconds 240]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import platform
import statistics
import sys
import time
from decimal import Decimal
from pathlib import Path

import httpx

_c = importlib.util.spec_from_file_location(
    "run85c", Path(__file__).with_name("run85_pmus_collector.py"))
C = importlib.util.module_from_spec(_c)
_c.loader.exec_module(C)

_b = importlib.util.spec_from_file_location(
    "run85b", Path(__file__).with_name("run85_phase2b.py"))
B = importlib.util.module_from_spec(_b)
_b.loader.exec_module(B)

PHASE = "run85/phase2c/1"
FAST_TIER_CAP = 2          # preserves the 5 s markout (2.0 x 2 = 4.0 s)
SURVEY_TIER_CAP = 6        # 2.0 x 6 = 12.0 s, preserves 30 s and 60 s


def q(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))]


def terciles(vals):
    return (q(vals, 1 / 3.0), q(vals, 2 / 3.0)) if vals else (None, None)


def bin3(v, cuts, names):
    if v is None or cuts[0] is None:
        return "UNKNOWN"
    return names[0] if v <= cuts[0] else (names[1] if v <= cuts[1] else names[2])


def iso_to_epoch(s):
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:                                   # noqa: BLE001
        return None


def spread_ticks(spread, tick):
    if spread is None or not tick:
        return None
    try:
        t = Decimal(str(tick))
        return int((Decimal(str(spread)) / t).to_integral_value(rounding="ROUND_HALF_UP")) \
            if t > 0 else None
    except Exception:                                   # noqa: BLE001
        return None


def cadence_table():
    return [(n, round(2.0 * n, 1)) for n in range(1, 7)]


# ------------------------------------------------------------- census
def build_pool(http, pacer, log, want_detail):
    """A1-A4: native identity, structured sport, verified binary."""
    print("=== 1/2. CANDIDATE POOL (native identity only) ===")
    r, rows = B.get_paced(http, B.EVENTS_PATH, pacer,
                          {"active": "true", "closed": "false", "limit": 100})
    for x in rows:
        x["stage"] = "events"
    log.extend(rows)
    body = r.get("body") or {}
    events = body.get("events") or body.get("data") or []
    events = events if isinstance(events, list) else []
    print("events returned: %d (http=%s)" % (len(events), r.get("http_status")))

    pool, excluded = [], []
    for e in events:
        if not isinstance(e, dict):
            continue
        eid, eslug = e.get("id"), e.get("slug")
        if eid is None or not eslug:
            excluded.append({"reason": "A1_NO_NATIVE_EVENT_IDENTITY",
                             "ref": str(e.get("slug") or e.get("id"))})
            continue
        for m in (e.get("markets") or []):
            if not isinstance(m, dict) or not m.get("slug"):
                excluded.append({"reason": "A2_NO_MARKET_SLUG",
                                 "ref": eslug})
                continue
            prov, why = B.structured_sport(m, e)
            if prov == "NOT_IDENTIFIED":
                excluded.append({"reason": "A3_NO_STRUCTURED_SPORTS_EVIDENCE",
                                 "ref": m["slug"]})
                continue
            pool.append({
                "event_id": eid, "event_slug": eslug,
                "event_title": e.get("title"),
                "start_time": e.get("startTime") or e.get("startDate"),
                "live": e.get("live"),
                "primary_tag": (e.get("primaryTag") or {}).get("slug")
                               if isinstance(e.get("primaryTag"), dict) else None,
                "series_slug": e.get("seriesSlug"),
                "market_slug": m["slug"], "market_title": m.get("title"),
                "outcome": m.get("outcome"),
                "sports_provenance": prov, "sports_reason": why,
            })
    native_rate = 100.0 * len(pool) / max(1, len(pool) + len(excluded))
    print("pool with native identity + structured sport: %d" % len(pool))
    print("excluded at A1-A3: %d" % len(excluded))
    print("NATIVE_EVENT_IDENTITY_RATE = %.2f%%" % native_rate)

    # one detail probe per candidate, bounded, one market per event first so a
    # single huge event cannot consume the whole budget
    seen_ev, ordered = set(), []
    for x in sorted(pool, key=lambda z: z["market_slug"]):
        if x["event_id"] not in seen_ev:
            seen_ev.add(x["event_id"])
            ordered.append(x)
    for x in sorted(pool, key=lambda z: z["market_slug"]):
        if x not in ordered:
            ordered.append(x)
    probe = ordered[:want_detail]
    print("detail probe budget: %d markets (%d distinct events in the first pass)"
          % (len(probe), len(seen_ev)))

    verified = []
    for x in probe:
        d, drows = B.get_paced(http, B.MARKET_PATH % x["market_slug"], pacer)
        for y in drows:
            y["stage"] = "detail"
            y["market_slug"] = x["market_slug"]
        log.extend(drows)
        det = ((d.get("body") or {}).get("market")
               if isinstance(d.get("body"), dict) else None) or {}
        sides, why = B.binary_sides(det)
        if sides is None:
            excluded.append({"reason": "A4_NOT_BINARY", "ref": x["market_slug"],
                             "detail": why})
            continue
        st = det.get("status") or det.get("state")
        if st and "OPEN" not in str(st).upper():
            excluded.append({"reason": "A5_NOT_OPEN", "ref": x["market_slug"],
                             "detail": str(st)})
            continue
        tick = det.get("orderPriceMinTickSize")
        if tick in (None, "", 0):
            excluded.append({"reason": "A7_NO_TICK_SIZE", "ref": x["market_slug"]})
            continue
        x.update({"long_side": sides["long"].get("identifier"),
                  "short_side": sides["short"].get("identifier"),
                  "market_state": st, "tick_size": tick,
                  "fee_coefficient": det.get("feeCoefficient"),
                  "minimum_trade_qty": det.get("minimumTradeQty"),
                  "market_type": det.get("sportsMarketType") or det.get("marketType")})
        verified.append(x)
    print("CANDIDATES_PROBED = %d" % len(probe))
    print("admitted through A1-A5,A7 = %d" % len(verified))
    return verified, excluded, native_rate, len(probe)


def census(http, pacer, log, cands, passes):
    """Repeated book reads: price, depth, asymmetry, and a change signal."""
    print()
    print("=== 3. REGIME FEATURES FROM OBSERVED WIRE DATA (%d passes) ===" % passes)
    seen = {x["market_slug"]: [] for x in cands}
    for p in range(passes):
        for x in cands:
            r, rows = B.get_paced(http, C.BOOK_PATH.format(slug=x["market_slug"]),
                                  pacer)
            for y in rows:
                y["stage"] = "census_book"
                y["census_pass"] = p
                y["market_slug"] = x["market_slug"]
                y["event_id"] = x["event_id"]
            log.extend(rows)
            if r.get("http_status") == 200:
                v = B.book_view(r.get("body"))
                seen[x["market_slug"]].append(
                    {"view": v, "sha": r.get("response_sha256"),
                     "mono": r.get("local_response_monotonic_ns"),
                     "transact_time": v.get("transact_time")})
        print("  pass %d done (spacing now %.2fs)" % (p, pacer.spacing))

    now = time.time()
    feats = []
    for x in cands:
        obs = seen.get(x["market_slug"]) or []
        if not obs:
            continue
        last = obs[-1]["view"]
        if last.get("best_bid") is None or last.get("best_ask") is None:
            continue                                   # A6
        bb, ba = last["best_bid"], last["best_ask"]
        bidN = bb * last["bid_qty_at_touch"]
        askN = ba * last["ask_qty_at_touch"]
        bd, ad = last["bid_depth_usd"], last["ask_depth_usd"]
        asym = None
        if bd and bd > 0 and ad and ad > 0:
            asym = math.log10(float(ad) / float(bd))
        shas = {o["sha"] for o in obs}
        tts = {o["transact_time"] for o in obs}
        st = iso_to_epoch(x.get("start_time"))
        feats.append({**x,
                      "best_bid": str(bb), "best_ask": str(ba),
                      "mid": str((bb + ba) / 2), "spread": str(ba - bb),
                      "spread_ticks": spread_ticks(ba - bb, x.get("tick_size")),
                      "touch_bid_qty": str(last["bid_qty_at_touch"]),
                      "touch_ask_qty": str(last["ask_qty_at_touch"]),
                      "touch_bid_notional": str(bidN),
                      "touch_ask_notional": str(askN),
                      "bid_depth_usd": str(bd), "ask_depth_usd": str(ad),
                      "depth_log_ratio": asym,
                      "observations": len(obs),
                      "distinct_book_hashes": len(shas),
                      "distinct_transact_times": len(tts),
                      "book_changed": len(shas) > 1,
                      "time_to_event_s": (st - now) if st else None})
    print("candidates with a two-sided book (A6): %d of %d" % (len(feats), len(cands)))
    return feats


# ------------------------------------------------------- bins + selection
def assign_bins(feats):
    """Section 4: bins from the OBSERVED distribution, never imported cuts."""
    mids = [float(Decimal(x["mid"])) for x in feats]
    depths = [min(float(Decimal(x["touch_bid_notional"])),
                  float(Decimal(x["touch_ask_notional"]))) for x in feats]
    cuts_price, cuts_depth = terciles(mids), terciles(depths)
    for x in feats:
        m = float(Decimal(x["mid"]))
        d = min(float(Decimal(x["touch_bid_notional"])),
                float(Decimal(x["touch_ask_notional"])))
        x["price_bin"] = bin3(m, cuts_price, ("P_LOW", "P_MID", "P_HIGH"))
        x["depth_bin"] = bin3(d, cuts_depth, ("D_THIN", "D_MID", "D_DEEP"))
        st = x.get("spread_ticks")
        x["spread_bin"] = ("UNKNOWN" if st is None else
                           "S_1T" if st <= 1 else "S_2_3T" if st <= 3 else "S_4T_PLUS")
        a = x.get("depth_log_ratio")
        x["asym_bin"] = ("UNKNOWN" if a is None else
                         "BID_HEAVY" if a < -0.5 else
                         "ASK_HEAVY" if a > 0.5 else "BALANCED")
        t = x.get("time_to_event_s")
        x["tte_bin"] = ("UNKNOWN" if t is None else
                        "T_LT_24H" if t < 86400 else
                        "T_1_7D" if t < 7 * 86400 else "T_GT_7D")
        x["act_bin"] = "ACTIVE" if x.get("book_changed") else "QUIET"
        x["regime_key"] = "|".join((x["price_bin"], x["spread_bin"],
                                    x["depth_bin"], x["asym_bin"],
                                    x["tte_bin"], x["act_bin"]))
    return {"price_terciles": cuts_price, "depth_terciles": cuts_depth}


def select(feats, cap):
    """Sections 6 and 9. Deterministic; asymmetry coverage forced first."""
    groups = {}
    for x in feats:
        groups.setdefault(x["regime_key"], []).append(x)
    for k in groups:
        groups[k].sort(key=lambda z: z["market_slug"])

    chosen, used_ev, used_key = [], set(), set()

    def take(x, why):
        if len(chosen) >= cap or x["event_id"] in used_ev:
            return False
        x = dict(x)
        x["selected_because"] = why
        chosen.append(x)
        used_ev.add(x["event_id"])
        used_key.add(x["regime_key"])
        return True

    # S5 first: force side-competition coverage
    for want in ("BID_HEAVY", "BALANCED", "ASK_HEAVY"):
        pool = sorted((x for x in feats if x["asym_bin"] == want),
                      key=lambda z: z["market_slug"])
        if pool and not any(c["asym_bin"] == want for c in chosen):
            take(pool[0], "S5_ASYMMETRY_COVERAGE_%s" % want)
    # S1-S3: one per regime, groups ordered by size then key
    for key in sorted(groups, key=lambda k: (-len(groups[k]), k)):
        if key in used_key:
            continue
        for cand in groups[key]:
            if take(cand, "S3_REGIME_%s" % key):
                break
    return chosen, groups


def diversity_verdicts(feats, chosen):
    def spread_of(field):
        return {c[field] for c in chosen if c.get(field) not in (None, "UNKNOWN")}
    avail = {f: {x[f] for x in feats if x.get(f) not in (None, "UNKNOWN")}
             for f in ("price_bin", "spread_bin", "depth_bin", "asym_bin",
                       "tte_bin", "act_bin")}
    out = {}
    for f, label in (("price_bin", "PRICE_DIVERSITY"),
                     ("spread_bin", "SPREAD_DIVERSITY"),
                     ("depth_bin", "DEPTH_DIVERSITY"),
                     ("asym_bin", "DEPTH_ASYMMETRY_DIVERSITY"),
                     ("tte_bin", "TIME_TO_EVENT_DIVERSITY"),
                     ("act_bin", "BOOK_ACTIVITY_DIVERSITY")):
        have, could = spread_of(f), avail[f]
        if not could:
            # The venue supplied nothing on this dimension for any candidate.
            # Absent is a different answer from present-and-narrow.
            out[label] = "NOT_IDENTIFIED"
        elif len(could) < 2:
            # Only ONE bin exists in the whole candidate pool. An earlier
            # version scored this SUFFICIENT because the cohort covered every
            # bin available -- true, and flattering, and useless: what the
            # multi-day decision needs to know is that this dimension will not
            # be spanned. Covering the only bin there is is not diversity.
            out[label] = "INSUFFICIENT"
        elif len(have) >= 2:
            out[label] = "SUFFICIENT"
        else:
            out[label] = "INSUFFICIENT"
    return out, avail


def validate(http, pacer, log, cohort, seconds, fh):
    """Section 8: short confirmation that the cohort shows the intended spread
    of regimes. Not used to optimise anything."""
    print()
    print("=== 8. SHORT VALIDATION SAMPLE (%ds) ===" % seconds)
    t_end = time.monotonic() + seconds
    n = 0
    while time.monotonic() < t_end:
        for x in cohort:
            if time.monotonic() >= t_end:
                break
            r, rows = B.get_paced(http, C.BOOK_PATH.format(slug=x["market_slug"]),
                                  pacer)
            for y in rows:
                y["stage"] = "validate"
                y["market_slug"] = x["market_slug"]
                y["event_id"] = x["event_id"]
                y["regime_key"] = x["regime_key"]
                y["tick_size"] = x.get("tick_size")
                if y.get("http_status") == 200:
                    v = B.book_view(y.get("body"))
                    y["view"] = {k: (str(val) if isinstance(val, Decimal) else val)
                                 for k, val in v.items()}
                fh.write(json.dumps(y) + "\n")
                n += 1
            log.append({"stage": "validate_ref", "market_slug": x["market_slug"]})
    print("  validation reads: %d" % n)
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--detail-probe", type=int, default=150)
    ap.add_argument("--census-passes", type=int, default=3)
    ap.add_argument("--validate-seconds", type=float, default=240.0)
    ap.add_argument("--census-max", type=int, default=60)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    R = []
    _real = print

    def emit(*p):
        s = " ".join(str(x) for x in p)
        R.append(s)
        _real(s)

    import builtins
    builtins.print = emit

    meta = {"phase": PHASE,
            "collector_sha256": hashlib.sha256(
                Path(__file__).with_name("run85_pmus_collector.py").read_bytes()).hexdigest(),
            "phase2b_sha256": hashlib.sha256(
                Path(__file__).with_name("run85_phase2b.py").read_bytes()).hexdigest(),
            "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "python": sys.version, "platform": platform.platform(),
            "httpx": httpx.__version__, "gateway": C.GATEWAY_BASE,
            "base_spacing_s": B.BASE_SPACING_S,
            "aggregate_rps_ceiling": 1.0 / B.BASE_SPACING_S,
            "detail_probe": a.detail_probe, "census_passes": a.census_passes,
            "validate_seconds": a.validate_seconds,
            "fast_tier_cap": FAST_TIER_CAP, "survey_tier_cap": SURVEY_TIER_CAP}
    (out / "runner_metadata.json").write_text(json.dumps(meta, indent=2))

    log = []
    pacer = B.AdaptivePacer()
    rc = 0
    try:
        emit(json.dumps(meta, indent=2))
        with httpx.Client(headers={"Accept": "application/json"}) as http:
            cands, excluded, native_rate, probed = build_pool(
                http, pacer, log, a.detail_probe)
            if not cands:
                emit("no admissible candidates; stopping")
                rc = 3
            else:
                # Bound the census: the detail probe is cheap per market but a
                # census pass costs one request per market per pass, so a large
                # verified pool would spend the whole budget re-reading books
                # instead of covering regimes. Deterministic cut by slug.
                census_pool = sorted(cands, key=lambda z: z["market_slug"])[:a.census_max]
                if len(census_pool) < len(cands):
                    emit("census bounded to %d of %d verified candidates "
                         "(deterministic, by slug)" % (len(census_pool), len(cands)))
                feats = census(http, pacer, log, census_pool, a.census_passes)
                cuts = assign_bins(feats)
                emit()
                emit("=== 4. OBSERVED REGIME DISTRIBUTION ===")
                emit("   tercile cuts: price=%s depth=%s"
                     % (cuts["price_terciles"], cuts["depth_terciles"]))
                from collections import Counter
                for f in ("price_bin", "spread_bin", "depth_bin", "asym_bin",
                          "tte_bin", "act_bin"):
                    emit("   %-11s %s" % (f, dict(Counter(x[f] for x in feats))))
                emit("   distinct full regime keys: %d"
                     % len({x["regime_key"] for x in feats}))
                emit("   ECONOMIC_REGIMES_IDENTIFIED = %d"
                     % len({x["regime_key"] for x in feats}))

                emit()
                emit("=== 7. CADENCE BUDGET at %.1f rps ===" % (1.0 / B.BASE_SPACING_S))
                emit("   N markets -> revisit; markouts a revisit supports")
                for n, rev in cadence_table():
                    ok = [str(t) for t in (5, 10, 30, 60) if rev <= t]
                    emit("     N=%d -> %4.1fs   supports: %s"
                         % (n, rev, ", ".join(ok) + "s" if ok else "none of 5/10/30/60"))
                emit("   The 5 s markout survives only at N<=2. Diversity and the")
                emit("   5 s offset cannot both be had from one flat cohort.")

                fast, groups = select(feats, FAST_TIER_CAP)
                survey, _ = select(feats, SURVEY_TIER_CAP)
                emit()
                emit("=== 6. FROZEN SELECTION APPLIED ===")
                for name, coh, rev in (("FAST", fast, 2.0 * len(fast)),
                                       ("SURVEY", survey, 2.0 * len(survey))):
                    emit("   %s tier: %d markets, revisit %.1fs" % (name, len(coh), rev))
                    for c in coh:
                        emit("     %-44s ev=%-6s %s  [%s]"
                             % (c["market_slug"][:44], c["event_id"],
                                c["regime_key"], c["selected_because"]))
                verd, avail = diversity_verdicts(feats, survey)
                emit()
                emit("=== SURVEY-TIER DIVERSITY ===")
                for k, v in verd.items():
                    emit("   %-28s %s" % (k, v))
                emit("   bins available in candidate pool: %s"
                     % {k: sorted(v) for k, v in avail.items()})

                regimes = len({x["regime_key"] for x in feats})
                emit()
                emit("=== PHASE 2C VERDICTS (census-derived) ===")
                emit("CANDIDATES_PROBED              = %d" % probed)
                emit("NATIVE_EVENT_IDENTITY_RATE     = %.2f%%" % native_rate)
                emit("ECONOMIC_REGIMES_IDENTIFIED    = %d" % regimes)
                for k in ("PRICE_DIVERSITY", "SPREAD_DIVERSITY", "DEPTH_DIVERSITY",
                          "DEPTH_ASYMMETRY_DIVERSITY", "TIME_TO_EVENT_DIVERSITY",
                          "BOOK_ACTIVITY_DIVERSITY"):
                    emit("%-30s = %s" % (k, verd[k]))
                emit("PROPOSED_MULTI_DAY_COHORT_SIZE = %d FAST + %d SURVEY = %d"
                     % (len(fast), len(survey), len(fast) + len(survey)))
                emit("EXPECTED_REVISIT_CADENCE       = FAST %.1fs / SURVEY %.1fs"
                     % (2.0 * len(fast), 2.0 * len(survey)))
                emit("SELECTION_RULE_FROZEN          = YES (docstring, pre-data)")
                emit("   The tiered proposal is two INDEPENDENT round-robins, not")
                emit("   one pooled cohort: pooling them would make every revisit")
                emit("   2.0 x (FAST+SURVEY) and destroy the short offsets the")
                emit("   FAST tier exists to preserve.")
                (out / "census.json").write_text(json.dumps(
                    {"phase": PHASE, "candidates_probed": probed,
                     "native_event_identity_rate": native_rate,
                     "tercile_cuts": {k: list(v) for k, v in cuts.items()},
                     "features": feats, "excluded": excluded[:400],
                     "fast_tier": fast, "survey_tier": survey,
                     "diversity": verd,
                     "regimes_identified": len({x["regime_key"] for x in feats})},
                    indent=2, default=str))

                with (out / "validation_samples.jsonl").open("w") as fh:
                    validate(http, pacer, log, survey, a.validate_seconds, fh)
    finally:
        builtins.print = _real

    (out / "request_log.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in log))
    (out / "throttle_events.json").write_text(json.dumps(pacer.events, indent=2))
    R.append("")
    R.append("=== THROTTLE ===")
    R.append("   429 backoff events: %d   final spacing %.2fs"
             % (len(pacer.events), pacer.spacing))

    lines = B.seal(out, R)
    ok, bad = B.verify(out)
    print("=== SEAL ===")
    for line in lines:
        print(line)
    print("SEAL_SELF_VERIFY = %s%s" % ("PASS" if ok else "FAIL",
                                       "" if ok else " " + str(bad)))
    return rc if ok else 4


if __name__ == "__main__":
    sys.exit(main())

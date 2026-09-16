#!/usr/bin/env python3
"""Apply the Target Size rule to a captured panel and report. Contacts nothing.

Reads a segment's `panel.jsonl(.gz)` and answers, per side and per quote
distance, whether a hypothetical BETTOR quote would be INSIDE the scoring
range — using `depth_panel.py`, whose rule was fixed before any of this data
existed.

WHAT IT DELIBERATELY DOES NOT DO. It does not estimate a reward, a share, a
fill probability or a markout. Those need either a denominator no public
endpoint publishes (every participant's qualifying score through time) or a
time series this file is not given. They are emitted as NOT_IDENTIFIED and are
meant to stay that way until something actually measures them.

Usage:
    python3 panel_report.py PANEL.jsonl[.gz] [PLAN.json]
"""
from __future__ import annotations

import collections
import gzip
import json
import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import depth_panel as P

NOT_IDENTIFIED = "NOT_IDENTIFIED"


def _open(path):
    p = Path(path)
    return (gzip.open(p, "rt") if p.suffix == ".gz" else p.open())


def load(path):
    with _open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def programs_of(r):
    """The programmes attached to one captured row, in both row shapes.

    C-8. Segments captured before the fix wrote ONE ROW PER PROGRAMME, so the
    same book snapshot appears up to five times; segments after it write one
    row per market carrying a PROGRAMS list. Reading both shapes here means the
    correction applies to the evidence already on disk, not only to evidence
    not yet captured.
    """
    ps = r.get("PROGRAMS")
    if isinstance(ps, list):
        return ps
    return [{k: r.get(k) for k in
             ("PROGRAM_TYPE", "PROGRAM_ID", "REWARD_POOL", "TARGET_SIZE",
              "DISCOUNT_FACTOR", "PROGRAM_PERIOD", "INSTRUMENT_STATE")}]


def observation_units(rows):
    """The panel's real unit: one book read, scored once per TARGET SIZE.

    THE DEFECT THIS FIXES. A market carries several concurrent programmes at
    DIFFERENT target sizes -- a UFC fight runs moneyline at 20,000 and props at
    1,000 at the same time. The first capture emitted one row per programme and
    the report counted each as an independent snapshot, so every market entered
    the statistics weighted by how many programmes it happened to carry. That
    is not a rounding difference: it moved ASK-one-tick-back eligibility from
    the 91.7% it is at target 20,000 to a pooled 79.6% that describes no
    programme anyone can actually quote into.

    A book read is deduped on (slug, round). Eligibility genuinely differs by
    target size, so the unit is (slug, round, TARGET_SIZE) and the two are
    NEVER pooled into one percentage.
    """
    seen, units = set(), []
    for r in rows:
        for p in programs_of(r):
            key = (r.get("slug"), r.get("round"), p.get("TARGET_SIZE"))
            if key in seen:
                continue
            seen.add(key)
            units.append((r, p))
    return units


def score_rows(rows):
    """One scored record per (book read, target size). See observation_units."""
    out = []
    for r, p in observation_units(rows):
        leg = r.get("leg") or {}
        if leg.get("http_status") != 200 or not leg.get("body"):
            out.append({"slug": r.get("slug"), "round": r.get("round"),
                        "TARGET_SIZE": p.get("TARGET_SIZE"),
                        "UNREADABLE": leg.get("error") or leg.get(
                            "http_status")})
            continue
        tick = r.get("tick")
        target = p.get("TARGET_SIZE")
        row = P.panel_row(leg["body"],
                          D(str(tick)) if tick else D("0.01"),
                          D(str(target)) if target is not None else None,
                          slug=r.get("slug"),
                          captured_at=leg.get("local_request_wall_utc"))
        row["round"] = r.get("round")
        row["PROGRAM_TYPE"] = p.get("PROGRAM_TYPE")
        row["PROGRAM_PERIOD"] = p.get("PROGRAM_PERIOD")
        row["REWARD_POOL"] = p.get("REWARD_POOL")
        row["DISCOUNT_FACTOR"] = p.get("DISCOUNT_FACTOR")
        row["INSTRUMENT_STATE"] = p.get("INSTRUMENT_STATE")
        row["TARGET_SIZE"] = target
        row["tick"] = tick
        out.append(row)
    return out


def _rate(rows, key):
    """Eligibility across readable side-observations, with the THIRD answer
    shown rather than folded away.

    `YES_ON_VISIBLE_BOOK` means the whole visible book never reached Target
    Size, so a quote at that level qualifies ON THIS SNAPSHOT but hidden or
    later size could still close the range ahead of us. Counting it as a plain
    YES would overstate eligibility; dropping it would shrink the denominator
    silently. It is reported as its own number.
    """
    vals = [r[key] for r in rows if key in r]
    if not vals:
        return NOT_IDENTIFIED
    c = collections.Counter(vals)
    decided = c["YES"] + c["NO"]
    parts = []
    if decided:
        parts.append("YES %d/%d = %.1f%%"
                     % (c["YES"], decided, 100.0 * c["YES"] / decided))
    else:
        parts.append("no decided observations")
    if c["YES_ON_VISIBLE_BOOK"]:
        parts.append("+%d target-not-reached-in-visible-book"
                     % c["YES_ON_VISIBLE_BOOK"])
    if c[NOT_IDENTIFIED]:
        parts.append("+%d NOT_IDENTIFIED" % c[NOT_IDENTIFIED])
    return "  ".join(parts)


def summarize(scored, target_size="ALL"):
    readable = [r for r in scored if "UNREADABLE" not in r]
    out = {
        "TARGET_SIZE": target_size,
        "OBSERVATION_UNIT": "ONE_BOOK_READ_PER_MARKET_PER_ROUND_PER_TARGET",
        "PANEL_OBSERVATIONS": len(scored),
        "PANEL_OBSERVATIONS_READABLE": len(readable),
        "PANEL_BOOK_READS": len({(r.get("slug"), r.get("round"))
                                 for r in readable}),
        "PANEL_MARKETS": len({r.get("slug") for r in readable}),
    }
    for side in ("BID", "ASK"):
        for k, label in ((0, "QUOTE_AT_BEST_SCORE_ELIGIBILITY"),
                         (1, "QUOTE_1_TICK_BACK_SCORE_ELIGIBILITY"),
                         (2, "QUOTE_2_TICKS_BACK_SCORE_ELIGIBILITY")):
            out["%s_%s" % (side, label)] = _rate(
                readable, "%s_QUOTE_%d_TICKS_BACK_SCORE_ELIGIBILITY"
                % (side, k))
        reached = [r["%s_TARGET_ALREADY_REACHED_AT_BEST" % side]
                   for r in readable
                   if r.get("%s_TARGET_ALREADY_REACHED_AT_BEST" % side)
                   not in (None, NOT_IDENTIFIED)]
        out["%s_TARGET_ALREADY_REACHED_AT_BEST_FREQUENCY" % side] = (
            "%d/%d = %.1f%%" % (sum(1 for x in reached if x), len(reached),
                                100.0 * sum(1 for x in reached if x)
                                / len(reached))
            if reached else NOT_IDENTIFIED)
        ahead = [r["%s_SIZE_AHEAD_0_TICKS" % side] for r in readable
                 if isinstance(r.get("%s_SIZE_AHEAD_0_TICKS" % side), D)]
        if ahead:
            ahead = sorted(ahead)
            out["%s_SIZE_AHEAD_AT_BEST_MEDIAN" % side] = str(
                ahead[len(ahead) // 2])
            out["%s_SIZE_AHEAD_AT_BEST_MIN" % side] = str(ahead[0])
            out["%s_SIZE_AHEAD_AT_BEST_MAX" % side] = str(ahead[-1])
        else:
            out["%s_SIZE_AHEAD_AT_BEST_MEDIAN" % side] = NOT_IDENTIFIED

    # Structurally unavailable from a book snapshot. Named, not omitted.
    for f in ("ESTIMATED_QUEUE_AHEAD_WITHIN_LEVEL", "TOUCH_RATE",
              "F0", "F1", "F2", "F3_TOUCH_UPPER_BOUND",
              "MARKOUT_5S", "MARKOUT_30S", "MARKOUT_60S", "MARKOUT_5M",
              "ADVERSE_SELECTION_VS_MAKER_BUDGET",
              "PAIR_CONVERSION_OPPORTUNITY", "ONE_SIDED_INVENTORY_DURATION",
              "REWARD_SHARE", "ACTUAL_REWARD"):
        out[f] = NOT_IDENTIFIED
    return out


def distributions(rows):
    d = {}
    flat = [dict(p, tick=r.get("tick")) for r in rows for p in programs_of(r)]
    for f in ("DISCOUNT_FACTOR", "REWARD_POOL", "PROGRAM_PERIOD",
              "PROGRAM_TYPE", "tick", "INSTRUMENT_STATE"):
        d[f] = collections.Counter(x.get(f) for x in flat).most_common()
    return d


def tape_observables(rows):
    """What the book payload's own `stats` block adds at LEVEL_0.

    A correction to my earlier reading of the data ladder. The book response
    carries `sharesTraded`, `notionalTraded`, `lastTradePx`, `lastTradeQty`,
    `lastTradeSetTime` and `openInterest`. Differencing two snapshots therefore
    establishes THAT trading occurred in the interval and HOW MUCH -- which is
    strictly more than the "no trade information at all" I previously assigned
    to REST polling.

    WHAT IT STILL CANNOT DO, and must not be stretched into:
      * no aggressor side. `lastTradePx` next to a one-tick spread does not
        identify which side lifted, and at any polling interval the last trade
        is the only one named however many occurred.
      * no per-trade sequence. Several trades between snapshots collapse into
        one `sharesTraded` delta.
      * nothing about WHOSE order filled, so no passive-fill evidence and no
        queue position.
    It is a polled volume counter, not a tape. It moves DEPTH_DEPLETION from
    NO to PROXY_ONLY at LEVEL_0 and moves nothing else.
    """
    snaps = {}
    for r in rows:
        leg = r.get("leg") or {}
        st = ((leg.get("body") or {}).get("marketData") or {}).get("stats")
        if not st:
            continue
        snaps.setdefault((r.get("slug"), r.get("round")), {
            "mono": leg.get("local_request_monotonic_ns"),
            "shares": st.get("sharesTraded"),
            "last_t": st.get("lastTradeSetTime"),
            "oi": st.get("openInterest")})
    out = {"TRADE_COUNTER_PRESENT_IN_BOOK_PAYLOAD": "YES" if snaps else "NO",
           "TRADE_AGGRESSOR_AVAILABLE": "NO",
           "PASSIVE_FILL_ATTRIBUTION_AVAILABLE": "NO",
           "TRADE_COUNTER_IS_A_TAPE": "NO"}
    pairs = adv = 0
    gaps = []
    for slug in sorted({k[0] for k in snaps}):
        seq = [snaps[(slug, r)] for r in
               sorted(r for (s, r) in snaps if s == slug)]
        for i in range(1, len(seq)):
            pairs += 1
            if seq[i]["mono"] and seq[i - 1]["mono"]:
                gaps.append((seq[i]["mono"] - seq[i - 1]["mono"]) / 1e9)
            if seq[i]["shares"] != seq[i - 1]["shares"]:
                adv += 1
    if pairs:
        out["CONSECUTIVE_SNAPSHOT_PAIRS"] = pairs
        out["SNAPSHOT_INTERVAL_SECONDS_MEDIAN"] = (
            "%.1f" % sorted(gaps)[len(gaps) // 2] if gaps else NOT_IDENTIFIED)
        out["ANY_TRADE_IN_INTERVAL_FREQUENCY"] = (
            "%d/%d = %.1f%%" % (adv, pairs, 100.0 * adv / pairs))
        out["THIS_IS_NOT_A_TOUCH_RATE"] = (
            "an interval-level yes/no on trading at ANY price, over a window "
            "this panel spans for only a few minutes")
    return out


def sport_concentration(rows):
    """The panel's generalizability, stated rather than left to be assumed.

    Slug order within a stratum is deterministic and uses no outcome, so it is
    not selection bias. It IS a concentration risk: if one event family sorts
    first it can fill every stratum, and the panel then measures that family's
    books rather than the board's.
    """
    fams = collections.Counter()
    for s in {r.get("slug") for r in rows}:
        parts = str(s).split("-")
        fams[parts[1] if len(parts) > 1 else str(s)] += 1
    return fams.most_common()


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print(__doc__)
        return 2
    rows = list(load(argv[0]))
    scored = score_rows(rows)
    print("=== INCENTIVE_DEPTH_PANEL (never pooled with BREADTH_CENSUS) ===")
    if len(argv) > 1 and Path(argv[1]).exists():
        plan = json.loads(Path(argv[1]).read_text())
        for k in ("DATASET", "SAMPLING_FROZEN_BEFORE_ECONOMICS",
                  "SELECTION_WITHIN_STRATUM", "strata", "markets_picked",
                  "STRATUM_SLOTS_FILLED", "DISTINCT_MARKETS_READ",
                  "rounds", "per_stratum"):
            if plan.get(k) is not None:
                print("%-42s %s" % (k, plan.get(k)))

    # PER TARGET SIZE, NEVER POOLED. Eligibility is a function of the target,
    # and a market runs several programmes at different targets at once, so a
    # single blended percentage describes no programme anyone can quote into.
    targets = sorted({r.get("TARGET_SIZE") for r in scored
                      if r.get("TARGET_SIZE") is not None},
                     key=lambda x: (x is None, x))
    for ts in targets:
        sub = [r for r in scored if r.get("TARGET_SIZE") == ts]
        print("\n--- TARGET_SIZE %s ---" % ts)
        for k, v in summarize(sub, target_size=ts).items():
            print("%-42s %s" % (k, v))
    if not targets:
        for k, v in summarize(scored).items():
            print("%-42s %s" % (k, v))

    print("\n--- decision-time strata actually captured ---")
    for k, v in distributions(rows).items():
        print("%-20s %s" % (k, v))
    conc = sport_concentration(rows)
    plan_method = None
    if len(argv) > 1 and Path(argv[1]).exists():
        plan_method = json.loads(Path(argv[1]).read_text()).get(
            "PANEL_SELECTION_METHOD")
    # THE SCOPE OF THIS PANEL'S EVIDENCE, printed beside every figure above so
    # a number cannot travel away from the label that bounds it. Lexicographic
    # order is outcome-blind, so there is NO leakage -- and it is not
    # identity-blind, so there IS a coverage artifact. Those are different
    # failures and get different fields.
    for k, v in (
            ("PANEL_GENERALIZES_TO_BOARD", "NO"),
            ("PANEL_SPORT_SCOPE",
             conc[0][0].upper() if len(conc) == 1 else
             "+".join(s.upper() for s, _ in conc)),
            ("PANEL_SELECTION_METHOD",
             plan_method or "LEXICOGRAPHIC_WITHIN_FROZEN_STRATA"),
            ("OUTCOME_LEAKAGE", "NO"),
            ("COVERAGE_BIAS", "NO" if len(conc) > 1 else "YES"),
            ("DO_NOT_USE_AS", "EXCHANGE_WIDE_OR_SPORTS_WIDE_ESTIMATE"),
    ):
        print("%-42s %s" % (k, v))
    print("%-42s %s" % ("SPORT_CONCENTRATION", conc))
    print("\n--- what the book payload's own stats block adds at LEVEL_0 ---")
    for k, v in tape_observables(rows).items():
        print("%-42s %s" % (k, v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

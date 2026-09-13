#!/usr/bin/env python3
"""RUN 85 PHASE 2C -- analysis of the frozen census, in the analyst's process.

Contacts nothing. Reads only the sealed Phase 2C archive.

WHY THIS FILE EXISTS SEPARATELY FROM THE DRIVER. The driver printed its own
terminal report on the runner. That report is evidence of what the runner saw;
it is not the analysis. Three of its lines do not survive a look at the record
it sealed, and a driver cannot be trusted to audit itself:

  1. Every one of the 19 census candidates carries a NEGATIVE time to event
     (-93.5 to -101.2 days). The tte binning tested only `< 86400` with no
     lower bound, so all 19 fell into T_LT_24H. The single-bin tte line is an
     artifact of a futures event's startTime being its season start, already
     elapsed -- not an observation that the pool is within a day of its event.

  2. Every market the probe reached is `futures` (148) or `election` (2). Not
     one game-level market was seen. Bin counts cannot repair that: the cohort
     would characterize season-long futures books only.

  3. The FAST tier is a SUBSET of the SURVEY tier, so "2 + 6 = 8" is 6 distinct
     markets under two cadences, not 8 markets.

Section 8 of the Phase 2C order also asks for a per-market table -- price,
spread, touch depth, depth ratio, time to event, book-change rate, assigned
bin -- which the runner's report summarized to bins only. It is produced here.

Run:  python3 research/run85_phase2c_analyze.py <extracted-archive-dir>
"""
from __future__ import annotations

import collections
import json
import sys
from decimal import Decimal
from pathlib import Path


def say(s=""):
    print(s)


def load(d: Path):
    census = json.loads((d / "census.json").read_text())
    log = [json.loads(x) for x in
           (d / "request_log.jsonl").read_text().splitlines() if x.strip()]
    return census, log


def dec(x):
    return Decimal(str(x)) if x is not None else None


# --------------------------------------------------------------- section 8
def per_market_table(rows, title):
    say("=== %s ===" % title)
    say("%-44s %7s %7s %6s %11s %11s %7s %9s %5s"
        % ("market_slug", "mid", "spread", "ticks", "touch_bid$",
           "touch_ask$", "log10R", "tte_days", "chg"))
    for r in rows:
        tte = r.get("time_to_event_s")
        say("%-44s %7s %7s %6s %11.2f %11.2f %7.3f %9.1f %5s"
            % (r["market_slug"], r["mid"], r["spread"], r["spread_ticks"],
               float(r["touch_bid_notional"]), float(r["touch_ask_notional"]),
               r["depth_log_ratio"],
               (tte / 86400.0) if tte is not None else float("nan"),
               "%d/%d" % (r["distinct_book_hashes"], r["observations"])))
    say()
    say("  depth$ (whole displayed ladder) and regime key")
    for r in rows:
        say("    %-44s bid$=%12.2f ask$=%12.2f  %s"
            % (r["market_slug"], float(r["bid_depth_usd"]),
               float(r["ask_depth_usd"]), r["regime_key"]))
    say()


# ------------------------------------------------- the three audit findings
def audit_time_to_event(feats):
    say("=== AUDIT 1. TIME TO EVENT IS NOT MEASURED ===")
    tte = [f["time_to_event_s"] for f in feats if f["time_to_event_s"] is not None]
    neg = [t for t in tte if t < 0]
    say("candidates with a tte value        : %d of %d" % (len(tte), len(feats)))
    say("of those, NEGATIVE (event passed)  : %d" % len(neg))
    if tte:
        say("range (days)                       : %.2f to %.2f"
            % (min(tte) / 86400.0, max(tte) / 86400.0))
    say("bins assigned                      : %s"
        % dict(collections.Counter(f["tte_bin"] for f in feats)))
    say()
    say("  Every value is negative, so the event each startTime names has")
    say("  already happened. These are season-long futures: startTime is the")
    say("  season's start, not the resolution of the contract. The binning")
    say("  tested `tte < 86400` with no lower bound, so every negative value")
    say("  landed in T_LT_24H.")
    say()
    say("  TIME_TO_EVENT_DIVERSITY = INSUFFICIENT is RETRACTED.")
    say("  The venue supplied a field; the field does not measure the thing.")
    say("  The dimension was NOT MEASURED, which is a different answer from")
    say("  measured-and-narrow, and it must not be reported as the latter.")
    say("  TIME_TO_EVENT_DIVERSITY = NOT_IDENTIFIED")
    say()
    return "NOT_IDENTIFIED"


def audit_market_type(log):
    say("=== AUDIT 2. NO GAME-LEVEL MARKET WAS REACHED ===")
    kinds = collections.Counter()
    ticks = collections.Counter()
    for r in log:
        if r.get("stage") != "detail":
            continue
        m = (r.get("body") or {}).get("market") or (r.get("body") or {})
        kinds[(m.get("status") or m.get("state"), m.get("sportsMarketType"))] += 1
        ticks[(m.get("sportsMarketType"), m.get("orderPriceMinTickSize"))] += 1
    for (status, kind), n in kinds.most_common():
        say("  %-24s %-10s %4d" % (status, kind, n))
    say()
    say("  sportsMarketType is `futures` or `election` on every one of the")
    say("  %d detail probes. A season-long futures book (worst record, batting"
        % sum(kinds.values()))
    say("  average leader, division winner) is not the market BETTOR mirrors:")
    say("  RN1's flow is game-level -- moneylines, spreads, totals. A cohort")
    say("  drawn from this pool would characterize passive fills in a market")
    say("  structure we do not trade.")
    say()
    say("  This is not a binning problem and more bins cannot fix it. It is a")
    say("  DISCOVERY problem: /v1/events?active=true&closed=false returned a")
    say("  first page of season-long events at 03:40Z. Whether game markets")
    say("  are absent at that hour, are on a later page, or need a different")
    say("  query is NOT ESTABLISHED by this run.")
    say()
    say("  GAME_LEVEL_MARKET_COVERAGE = NONE_OBSERVED")
    say()
    say("  tick size by market type (per-market storage was the right call):")
    for (kind, t), n in ticks.most_common():
        say("    %-10s tick=%-8s %4d" % (kind, t, n))
    say()
    say("  Tick is NOT constant across the venue. Phase 2B observed 0.001;")
    say("  138 of 150 markets here are 0.01. PMUS_ORDER_PRICE_MIN_TICK_SIZE")
    say("  is a PER-MARKET field and no single value may be carried forward.")
    say("  A spread measured in ticks is comparable only within one tick size.")
    say()
    return dict(kinds), dict(ticks)


def audit_tier_overlap(census):
    say("=== AUDIT 3. THE TWO TIERS OVERLAP ===")
    fast = [x["market_slug"] for x in census["fast_tier"]]
    survey = [x["market_slug"] for x in census["survey_tier"]]
    shared = [s for s in fast if s in survey]
    distinct = sorted(set(fast) | set(survey))
    say("FAST tier    : %d  %s" % (len(fast), fast))
    say("SURVEY tier  : %d" % len(survey))
    say("in both      : %d  %s" % (len(shared), shared))
    say("DISTINCT MARKETS = %d" % len(distinct))
    say()
    say("  '2 FAST + 6 SURVEY = 8' counts two markets twice. The venue-call")
    say("  budget is still 2 + 6 = 8 reads per round-robin cycle, so the")
    say("  cadence arithmetic holds -- but the number of DISTINCT books under")
    say("  observation is %d, and the diversity of the capture is bounded by" % len(distinct))
    say("  that number, not by 8.")
    say()
    return len(distinct)


def audit_pool_reach(census, log):
    say("=== AUDIT 4. WHAT THE POOL COULD HAVE SUPPORTED ===")
    feats = census["features"]
    say("probed                          : %d" % census["candidates_probed"])
    say("excluded at A5 (not open)       : %d"
        % sum(1 for x in census["excluded"] if x["reason"] == "A5_NOT_OPEN"))
    say("censused for book state         : 60")
    say("of those, TWO-SIDED book (A6)   : %d" % len(feats))
    say()
    say("  Two thirds of the censused markets had no two-sided book. A market")
    say("  with only one side cannot be a passive-fill subject at all, so the")
    say("  reachable pool for selection was %d, not 60 and not 140." % len(feats))
    say()
    tags = collections.Counter(str(f["primary_tag"]) for f in feats)
    series = collections.Counter(f["series_slug"] for f in feats)
    say("primary_tag across the %d        : %s" % (len(feats), dict(tags)))
    say("series_slug across the %d        : %s" % (len(feats), dict(series)))
    say()
    say("  Two series. primary_tag is null on the %d UFC rows, so sport"
        % tags.get("None", 0))
    say("  identity for those came from series_slug alone. Section 5 allows")
    say("  only structured metadata and forbids substring matching; series_slug")
    say("  is structured, so this is legal -- but SPORT_DIVERSITY rests on one")
    say("  field for a third of the pool.")
    say()
    return len(feats)


# ------------------------------------------------------ the asymmetry audit
FAR_HI = 0.95
FAR_LO = 0.05


def ladder_usd(levels, lo=None, hi=None):
    t = Decimal(0)
    for x in levels or []:
        p = Decimal(str(x["px"]["value"]))
        if lo is not None and not (Decimal(str(lo)) < p < Decimal(str(hi))):
            continue
        t += p * Decimal(str(x["qty"]))
    return t


def books_from(records, key="stage", want="census_book"):
    """First sighting of each distinct book in a request/sample log."""
    out = {}
    for r in records:
        if want is not None and r.get(key) != want:
            continue
        md = (r.get("body") or {}).get("marketData") or r.get("marketData") or {}
        s = md.get("marketSlug")
        if s and s not in out and (md.get("bids") or md.get("offers")):
            out[s] = md
    return out


def side_class(ratio):
    import math
    if ratio is None:
        return "UNMEASURABLE"
    lr = math.log10(float(ratio))
    return ("BID_HEAVY" if lr < -0.3 else
            "ASK_HEAVY" if lr > 0.3 else "BALANCED")


def audit_asymmetry(log, p2b_samples):
    say("=== AUDIT 5. THE ASYMMETRY MEASURE IS CONTAMINATED ===")
    books = books_from(log)
    say("two-sided-or-one-sided books sampled in the census : %d" % len(books))

    far, shares = 0, []
    for md in books.values():
        offs = md.get("offers") or []
        if not offs:
            continue
        tot = ladder_usd(offs)
        hi = ladder_usd([o for o in offs
                         if Decimal(str(o["px"]["value"])) >= Decimal(str(FAR_HI))])
        if hi > 0:
            far += 1
        if tot > 0:
            shares.append(float(hi / tot))
    shares.sort()
    say("books carrying an offer level at px >= %.2f          : %d of %d"
        % (FAR_HI, far, len(shares)))
    if shares:
        say("median share of ASK notional sitting at px >= %.2f  : %.1f%%"
            % (FAR_HI, 100 * shares[len(shares) // 2]))
        say("range                                              : %.1f%% .. %.1f%%"
            % (100 * shares[0], 100 * shares[-1]))
    blocks = collections.Counter()
    for md in books.values():
        for o in (md.get("offers") or []):
            if Decimal(str(o["px"]["value"])) >= Decimal(str(FAR_HI)):
                blocks[(o["px"]["value"], o["qty"])] += 1
    if blocks:
        (px, qty), n = blocks.most_common(1)[0]
        say("most repeated far-ask level                        : "
            "%s shares @ %s, in %d books" % (qty, px, n))
    say()
    say("  asym_bin was log10(WHOLE displayed ask notional / WHOLE displayed")
    say("  bid notional). Every book in the census carries a large resting")
    say("  block near the top of the price range, and the same block appears")
    say("  at the bottom of the bid side. The two are comparable in SHARES and")
    say("  wildly different in DOLLARS, because notional is price x quantity:")
    say("  27,500 shares at 0.99 is $27,225; the same 27,500 shares at 0.01 is")
    say("  $275. A 99x dollar gap out of a symmetric parking order.")
    say()
    say("  So the whole-ladder measure mostly reports 'this book has a far-end")
    say("  block', which is true of every book, and it reports it as ask")
    say("  heaviness. It is not measuring side competition near the touch --")
    say("  which is the thing section 9 says realizable passive fills depend on.")
    say()

    say("  Re-derived with the far ends (px>=%.2f, px<=%.2f) excluded:"
        % (FAR_HI, FAR_LO))
    cls = collections.Counter()
    usable = 0
    for s, md in sorted(books.items()):
        b = ladder_usd(md.get("bids"), FAR_LO, FAR_HI)
        a = ladder_usd(md.get("offers"), FAR_LO, FAR_HI)
        if b <= 0 or a <= 0:
            continue
        usable += 1
        cls[side_class(a / b)] += 1
    say("    books with inside-range depth on BOTH sides      : %d" % usable)
    say("    corrected classification                         : %s" % dict(cls))
    say()
    say("  BID-HEAVY BOOKS DO EXIST. The earlier reading that none exists was")
    say("  produced by the contaminated measure, not by the venue.")
    say()

    say("  The SAME test applied to the two Phase 2B books, because section 9")
    say("  is founded on their 27-30x figure:")
    p2b = books_from(p2b_samples, want=None)
    for s, md in sorted(p2b.items()):
        B, A = ladder_usd(md.get("bids")), ladder_usd(md.get("offers"))
        b = ladder_usd(md.get("bids"), FAR_LO, FAR_HI)
        a = ladder_usd(md.get("offers"), FAR_LO, FAR_HI)
        say("    %-38s whole-ladder %6.1fx   inside-range %s  -> %s"
            % (s, (A / B) if B else float("nan"),
               ("%6.2fx" % (a / b)) if b else "   n/a",
               side_class((a / b) if b else None)))
    say()
    say("  PHASE2B_DEPTH_ASYMMETRY_27_30X = RETRACTED AS A QUEUE-COMPETITION")
    say("  READING. The number is arithmetically correct over the whole")
    say("  displayed ladder and it is the wrong ladder for the question. Near")
    say("  the money one of those two books is close to balanced and the other")
    say("  is BID-heavy -- the opposite sign from the one carried forward.")
    say()
    say("  DEPTH_ASYMMETRY_DIVERSITY = NOT_IDENTIFIED")
    say("  Not INSUFFICIENT: the dimension was measured with an invalid")
    say("  instrument, so the cohort's coverage of it is unknown rather than")
    say("  narrow. S5 forced asymmetry coverage over the contaminated bins, so")
    say("  the market the rule selected as BALANCED was selected for a reason")
    say("  that does not survive this audit. The rule's MECHANISM is sound and")
    say("  tested; the FEATURE it was fed is not.")
    say()
    return dict(cls), usable


def audit_sides(census):
    say("=== AUDIT 6. HOW A LEG IS ADDRESSED ===")
    feats = census["features"]
    same = sum(1 for f in feats if f["long_side"] == f["short_side"])
    say("candidates whose long_side == short_side : %d of %d" % (same, len(feats)))
    say()
    say("  The A7 structure check is real: it requires marketSides to hold")
    say("  exactly two entries, one with long=true and one with long=false.")
    say("  All %d passed it. But both entries carry the SAME identifier" % len(feats))
    say("  string -- the market slug. The venue distinguishes the legs by the")
    say("  BOOLEAN, not by a distinct per-leg token id.")
    say()
    say("  PMUS_LEG_ADDRESSED_BY = marketSides[].long BOOLEAN")
    say("  PMUS_DISTINCT_PER_LEG_IDENTIFIER = NOT_PRESENT")
    say()
    say("  Nothing is ordered in this run and nothing will be. Recorded")
    say("  because any future code that selects a side by identifier string")
    say("  would select nothing, and would do so silently.")
    say()


def main():
    d = Path(sys.argv[1])
    census, log = load(d)
    p2b_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    p2b = ([json.loads(x) for x in
            (p2b_dir / "book_samples.jsonl").read_text().splitlines() if x.strip()]
           if p2b_dir else [])

    say("RUN 85 PHASE 2C -- ANALYSIS OF THE FROZEN CENSUS")
    say("archive dir : %s" % d.name)
    say("phase       : %s" % census["phase"])
    if p2b_dir:
        say("phase 2B    : %s (for the asymmetry cross-check)" % p2b_dir.name)
    say("NOTHING IN THIS FILE CONTACTS THE VENUE.")
    say()

    per_market_table(census["survey_tier"],
                     "8. SELECTED COHORT, PER MARKET (survey tier, 6 markets)")
    per_market_table(census["features"],
                     "8b. FULL TWO-SIDED CANDIDATE POOL (19 markets)")

    tte_verdict = audit_time_to_event(census["features"])
    audit_market_type(log)
    distinct = audit_tier_overlap(census)
    pool_n = audit_pool_reach(census, log)
    cls, usable = audit_asymmetry(log, p2b)
    audit_sides(census)

    say("=== PHASE 2C VERDICTS, AS CORRECTED BY THIS ANALYSIS ===")
    v = dict(census["diversity"])
    v["TIME_TO_EVENT_DIVERSITY"] = tte_verdict
    v["DEPTH_ASYMMETRY_DIVERSITY"] = "NOT_IDENTIFIED"
    say("CANDIDATES_PROBED              = %d" % census["candidates_probed"])
    say("NATIVE_EVENT_IDENTITY_RATE     = %.2f%%" % census["native_event_identity_rate"])
    say("TWO_SIDED_BOOK_POOL            = %d of 60 censused" % pool_n)
    say("ECONOMIC_REGIMES_IDENTIFIED    = %d (over CONTAMINATED asym_bin -- "
        "see AUDIT 5)" % census["regimes_identified"])
    for k in ("PRICE_DIVERSITY", "SPREAD_DIVERSITY", "DEPTH_DIVERSITY",
              "DEPTH_ASYMMETRY_DIVERSITY", "TIME_TO_EVENT_DIVERSITY",
              "BOOK_ACTIVITY_DIVERSITY"):
        say("%-30s = %s" % (k, v[k]))
    say("GAME_LEVEL_MARKET_COVERAGE     = NONE_OBSERVED")
    say("BID_HEAVY_BOOK_AVAILABLE       = YES (%d of %d books measurable near "
        "the money)" % (cls.get("BID_HEAVY", 0), usable))
    say("PROPOSED_MULTI_DAY_COHORT_SIZE = %d DISTINCT markets "
        "(2 FAST + 6 SURVEY, FAST a subset)" % distinct)
    say("EXPECTED_REVISIT_CADENCE       = FAST 4.0s / SURVEY 12.0s "
        "(two independent round-robins)")
    say("SELECTION_RULE_FROZEN          = YES")
    say("COHORT_DIVERSITY_VERIFIED      = NO")
    say("READY_FOR_MULTI_DAY_PHASE2_CAPTURE = NO")
    say()
    say("  READY is NO on AUDIT 2 alone, and independently on AUDIT 5.")
    say()
    say("  The selection rule is no longer the blocker. It is deterministic,")
    say("  frozen before the data, caps one market per event, forces coverage")
    say("  before size-ranked filling, and keeps the cadence usable. Its")
    say("  mechanism is tested and it behaved as specified.")
    say()
    say("  What blocks the capture is what the rule was fed:")
    say("    AUDIT 2  every market reached is season-long futures. A capture")
    say("             here measures passive fills in a structure BETTOR does")
    say("             not trade, and more capture time cannot convert that")
    say("             into evidence about game markets.")
    say("    AUDIT 5  the asymmetry feature does not measure side competition,")
    say("             so the one dimension section 9 called decisive for")
    say("             realizable passive fills was binned on a number that")
    say("             does not measure it.")
    say()
    say("  Fees are untouched by this file and remain:")
    say("  PMUS_FEE_COEFFICIENT_FIELD = 0.06 (VERIFIED as a field)")
    say("  PMUS_FEE_FORMULA / MAKER_FEE / TAKER_FEE / MAKER_REBATE = NOT_IDENTIFIED")
    say("  PMUS_FEES_RESOLVED = NO")
    say("  No fee arithmetic enters any verdict above.")


if __name__ == "__main__":
    main()

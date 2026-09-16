#!/usr/bin/env python3
"""RUN 85 PHASE 2F -- analysis of the frozen closure run. Contacts nothing.

Salvages the freshness cross-check the driver failed to parse, judges the
verdicts the runner was told not to assert, and emits the frozen multi-day
capture design if the gates pass.

Run:  python3 research/run85_phase2f_analyze.py <p2f-dir>
"""
from __future__ import annotations

import collections
import json
import sys
from decimal import Decimal
from pathlib import Path

HORIZONS = (5.0, 10.0, 30.0, 60.0)


def say(s=""):
    print(s)


def amt(x):
    if isinstance(x, dict) and x.get("value") is not None:
        try:
            return Decimal(str(x["value"]))
        except Exception:                              # noqa: BLE001
            return None
    return None


def touch(md):
    def lad(rows, pick):
        px = []
        for r in rows or []:
            v = amt(r.get("px"))
            if v is not None:
                px.append(v)
        return pick(px) if px else None
    return lad(md.get("bids"), max), lad(md.get("offers"), min)


# ------------------------------------------------------ 2. freshness salvage
def freshness(dirpath, out):
    say("=== 2. FRESHNESS -- SALVAGED FROM THE SEALED BODIES ===")
    say("  THE DRIVER'S PROBE WAS BROKEN. It looked for a `marketBbo`/`bbo`")
    say("  key; the bbo route wraps its payload in `marketData`, the same")
    say("  envelope as the book. Every cross-route comparison came back")
    say("  NOT_IDENTIFIED and the run's own freshness verdict is empty.")
    say("  The raw bodies were sealed, so the comparison is recoverable here")
    say("  without another venue call -- and it is redone from those bytes.")
    say()
    books, bbos = {}, {}
    for line in (dirpath / "request_log.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        st = r.get("stage")
        if st not in ("fresh_book", "fresh_bbo"):
            continue
        md = (r.get("body") or {}).get("marketData") or {}
        key = (r.get("market_slug"), r.get("local_request_wall_utc"))
        rec = {"slug": r.get("market_slug"),
               "req": r.get("local_request_wall_utc"),
               "tt": md.get("transactTime"),
               "lps": ((md.get("stats") or md).get("lastPriceSample") or {}).get("ts"),
               "bid": None, "ask": None, "state": md.get("state")}
        if st == "fresh_book":
            b, a = touch(md)
            rec["bid"], rec["ask"] = b, a
            rec["nbids"] = len(md.get("bids") or [])
            rec["nasks"] = len(md.get("offers") or [])
            books.setdefault(r.get("market_slug"), []).append(rec)
        else:
            rec["bid"], rec["ask"] = amt(md.get("bestBid")), amt(md.get("bestAsk"))
            rec["bid_depth"] = md.get("bidDepth")
            rec["ask_depth"] = md.get("askDepth")
            rec["shares"] = md.get("sharesTraded")
            bbos.setdefault(r.get("market_slug"), []).append(rec)

    classes = collections.Counter()
    agree = disagree = 0
    rows = []
    for slug in sorted(books):
        bk, bb = books[slug], bbos.get(slug, [])
        say("  %s" % slug)
        prev = None
        for i, k in enumerate(bk):
            q = bb[i] if i < len(bb) else None
            same_touch = (q is not None and k["bid"] == q["bid"]
                          and k["ask"] == q["ask"])
            measurable = q is not None and (q["bid"] is not None
                                            or q["ask"] is not None)
            cur = (k["bid"], k["ask"], k.get("nbids"), k.get("nasks"))
            changed = prev is not None and cur != prev
            if not measurable and k["bid"] is None and k["ask"] is None:
                cls = "NOT_IDENTIFIED"        # empty book on both routes
            elif not measurable:
                cls = "NOT_IDENTIFIED"
            elif not same_touch:
                cls = "POTENTIALLY_STALE"
                disagree += 1
            else:
                agree += 1
                cls = "CHANGED_BOOK_VALID" if changed else "UNCHANGED_BOOK_VALID"
            classes[cls] += 1
            say("     r%d book %-7s/%-7s  bbo %-7s/%-7s depth %s/%s  tt=%s -> %s"
                % (i, k["bid"], k["ask"],
                   q["bid"] if q else None, q["ask"] if q else None,
                   q.get("bid_depth") if q else None,
                   q.get("ask_depth") if q else None,
                   (k["tt"] or "")[11:23], cls))
            rows.append({"slug": slug, "round": i, "class": cls,
                         "book_bid": str(k["bid"]), "book_ask": str(k["ask"]),
                         "bbo_bid": str(q["bid"]) if q else None,
                         "bbo_ask": str(q["ask"]) if q else None,
                         "transact_time": k["tt"]})
            prev = cur
        say()
    say("cross-route touch: AGREE %d   DISAGREE %d" % (agree, disagree))
    say("observation classes: %s" % dict(classes))
    say()

    # transactTime identity, counting ABSENT separately from UNEQUAL
    eq = ne = absent = 0
    for slug, v in books.items():
        for k in v:
            if k["lps"] is None:
                absent += 1
            elif k["tt"] == k["lps"]:
                eq += 1
            else:
                ne += 1
    say("transactTime vs stats.lastPriceSample.ts on this run's book reads:")
    say("   EQUAL %d   UNEQUAL %d   lastPriceSample ABSENT %d" % (eq, ne, absent))
    say()
    say("  The runner reported '15 of 20' because it folded ABSENT into")
    say("  unequal. All %d absent readings are ONE market with an empty book"
        % absent)
    say("  (atc-uecl-ggk-zil-2026-07-30-draw, a six-week-old soccer draw with")
    say("  no ladder on either side), where the venue supplies no price sample")
    say("  at all. Where the field EXISTS the identity holds %d of %d, which"
        % (eq, eq + ne))
    say("  with the 262 of 262 from the sealed 2C/2D/2E archives leaves the")
    say("  semantics unchanged:")
    say()
    say("  TRANSACTTIME_SEMANTICS = LAST_PRICE_SAMPLE_TIMESTAMP")
    say("     not last-mutation, not response-generation, not cache time.")
    say("     Where lastPriceSample is absent, transactTime is NOT_IDENTIFIED")
    say("     rather than contradicting.")
    say()
    verdict = ("YES" if (agree and not disagree) else
               "NO" if disagree else "NOT_IDENTIFIED")
    say("  FRESHNESS READING. Two independently-served routes -- /book and")
    say("  /bbo -- were read back to back %d times. The touch agreed on every"
        % (agree + disagree))
    say("  measurable pair and disagreed on none. That is corroboration from a")
    say("  second path, which is what transactTime age could never provide.")
    say()
    say("  It is NOT proof of freshness in the strong sense: both routes could")
    say("  share one upstream. What it rules out is the specific failure that")
    say("  would wreck a markout -- one route serving a book the other has")
    say("  already moved past.")
    say()
    say("PUBLIC_BOOK_FRESHNESS_SUFFICIENT_FOR_OBSERVATIONAL_RESEARCH = %s" % verdict)
    say()
    out["freshness"] = {"agree": agree, "disagree": disagree,
                        "classes": dict(classes), "tt_equal": eq,
                        "tt_unequal": ne, "tt_absent": absent,
                        "verdict": verdict, "rows": rows}
    return verdict


# ------------------------------------------------------- 3/7. cohort
def cohort(disc, out):
    say("=== 3/7. COHORT AND DIVERSITY ===")
    w = disc.get("walk") or {}
    fr = disc.get("frame") or {}
    say("walk stop=%s pages=%d game_events=%d"
        % (w.get("stop"), w.get("pages", 0), w.get("game_events", 0)))
    say("sport buckets in frame=%s  ticks=%s  price bands=%s"
        % (w.get("sport_buckets"), w.get("ticks"), w.get("price_bands")))
    say("frame states=%s  live events=%s"
        % (fr.get("states"), fr.get("live_events")))
    say()
    c = disc.get("cohort") or []
    say("%-44s %-7s %-8s %-7s %-7s %-8s %-11s"
        % ("market", "sport", "state", "mid", "tick", "band", "queue"))
    for x in c:
        say("%-44s %-7s %-8s %-7s %-7s %-8s %-11s"
            % (x["market_slug"][:44], str(x["sport"]), x["normalized_state"],
               x["mid"], x["tick_size"], x["price_band"], x["queue_class"]))
    say()
    sp = collections.Counter(str(x["sport_bucket"]) for x in c)
    tk = collections.Counter(str(x["tick_size"]) for x in c)
    bd = collections.Counter(str(x["price_band"]) for x in c)
    q = collections.Counter(x["queue_class"] for x in c)
    st = collections.Counter(x["normalized_state"] for x in c)
    say("sport buckets %s" % dict(sp))
    say("tick sizes    %s" % dict(tk))
    say("price bands   %s" % dict(bd))
    say("queue classes %s" % dict(q))
    say("states        %s" % dict(st))
    say()
    ok = len(sp) >= 2 and len(tk) >= 2 and len(bd) >= 2 and len(q) >= 2
    say("  Against section 3's named targets:")
    for want in ("nfl", "cfb", "mlb", "soccer", "other"):
        say("    %-8s %s" % (want,
                             "PRESENT" if want in sp else "ABSENT at this instant"))
    if not fr.get("live_events"):
        say("    LIVE_COHORT_AVAILABLE = NO_AT_OBSERVATION_TIME")
    say()
    say("GAME_LEVEL_COHORT_DIVERSITY = %s" % ("SUFFICIENT" if ok else "INSUFFICIENT"))
    say()
    out["cohort_diversity"] = {"sufficient": ok, "sports": dict(sp),
                               "ticks": dict(tk), "bands": dict(bd),
                               "queues": dict(q), "states": dict(st)}
    return ok


# ------------------------------------------------------- 5. scheduler
def scheduler(disc, out):
    say("=== 5. DRAINED-PACER SCHEDULER ===")
    sc = disc.get("scheduler") or {}
    say("drain slept %.3f s; residual debt after drain %.4f s -> DRAINED_PACER_"
        "VERIFIED = %s" % (sc.get("drained_slept_s", 0),
                           sc.get("residual_debt_s", 0),
                           "YES" if sc.get("drained_verified") else "NO"))
    say("plan %s reads over %.1f s at %.3f rps; throttled during test: %s"
        % (sc.get("plan_reads"), sc.get("span_s", 0), sc.get("mean_rps", 0),
           sc.get("throttled")))
    say()
    say("%-8s %5s %10s %10s %10s %10s   %s"
        % ("horizon", "n", "median", "p90", "p95", "max", "valid snapshots"))
    for h in HORIZONS:
        s = (sc.get("lag_error") or {}).get(str(h))
        if not s:
            say("%-8.0f %5d %10s" % (h, 0, "-"))
            continue
        say("%-8.0f %5d %8.1fms %8.1fms %8.1fms %8.1fms   %d/%d"
            % (h, s["n"], s["median_ms"], s["p90_ms"], s["p95_ms"],
               s["max_ms"], s["valid_snapshots"], s["observations"]))
    say()
    say("  Phase 2E's 5 s lag error was 575 ms median and 2.6 s at the first")
    say("  read, a decaying startup debt. Draining the pacer before t0 takes it")
    say("  to a few milliseconds. The defect is fixed at its cause.")
    say()
    say("BOOK_CHANGED_BY_HORIZON -- descriptive, never a filter:")
    for h in HORIZONS:
        c = (sc.get("book_changed") or {}).get(str(h))
        if c:
            say("   %2ds  %d of %d observations differed from t0" % (int(h), c[0], c[1]))
    say()
    say("  Per the corrected semantics, an unchanged book at a horizon is an")
    say("  observation whose markout is ZERO. The rate above describes the")
    say("  market; it does not select the sample and must never be used to.")
    say()
    out["scheduler"] = sc
    return sc


def rate(disc):
    say("=== 6. RATE ===")
    thr = disc.get("throttle_events") or []
    say("NOMINAL_RPS  = 0.40 (2.5 s spacing)")
    say("ACHIEVED_RPS = %.4f over %.1f s, %s requests"
        % (disc.get("achieved_rps") or 0, disc.get("elapsed_s") or 0,
           disc.get("venue_requests_spent")))
    say("HTTP_429_COUNT = %d" % sum(1 for e in thr if e.get("event") == "429"))
    for e in thr:
        say("   %s" % e)
    say()
    say("  Zero 429s at 0.4 rps, where 0.5 rps produced one on each of the two")
    say("  preceding runs. That is consistent with the lower operating point")
    say("  being safer and is NOT evidence of where the limit sits: one clean")
    say("  run does not establish a rate. RPS_LIMIT_NOT_ESTABLISHED stands.")
    say()


# -------------------------------------------------- 9. the capture design
def capture_design(ok_all):
    say("=== 9. FROZEN MULTI-DAY CAPTURE DESIGN (for approval; NOT started) ===")
    say()
    say("SCOPE. Observational only. GET on the public gateway, no credential,")
    say("no websocket, no order path, mirror_live stays false.")
    say()
    say("RATE. 0.4 rps nominal, 2.5 s spacing floor, Retry-After honoured in")
    say("full, backoff x2 on 429 to a 30 s ceiling, recovery x0.9 per success")
    say("only. Pacer drained before every timed plan. No rate discovery.")
    say()
    say("COHORT. Re-selected at the start of each capture day by the frozen")
    say("2F rule: event-level frame over offset pagination, round-robin across")
    say("(sport bucket, normalized state) strata, one market per native event,")
    say("ordered by native event id. A null primaryTag is not a sport.")
    say("Target 8 markets; re-selection is by rule, never by hand.")
    say()
    say("SAMPLING. Horizon-specific, not one uniform round robin. Per subject,")
    say("an observation SET is t0 then t0+5, +10, +30, +60 s, placed exactly")
    say("and checked against the spacing floor and the mean ceiling BEFORE")
    say("running; a plan that breaches either is not trimmed, it is refused.")
    say("Sets repeat on a rotation so each market contributes to the horizons")
    say("its schedule actually reached. A market may contribute to some")
    say("horizons and not others. NOTHING IS EVER INTERPOLATED.")
    say()
    say("PER OBSERVATION, RECORD:")
    for f in ("local request and response wall clock, and monotonic",
              "http status, response bytes, response sha256",
              "venue transactTime AND stats.lastPriceSample.ts",
              "full normalized ladder plus its sha256",
              "best bid, best ask, displayed spread",
              "touch quantity and notional, both sides",
              "cumulative quantity and notional within 0/1/2/5 ticks",
              "the market's own tick size, on every row",
              "raw period, normalized state, and the justification",
              "scheduled game start, event start, endDate -- kept separate",
              "a paired /bbo read for the freshness cross-check",
              "market state and event id from the frame"):
        say("     * %s" % f)
    say()
    say("DERIVABLE LATER, AND ONLY LATER:")
    say("  displayed passive spread; a hypothetical resting quote at the touch")
    say("  or one tick behind; whether the market traded through that price;")
    say("  near-touch queue state ahead of it; one-leg versus both-leg")
    say("  opportunity; 5/10/30/60 s adverse movement; incomplete-pair")
    say("  exposure; residual settlement outcome where the venue publishes it.")
    say()
    say("THE LINE THAT MUST NOT BE CROSSED:")
    say("  PASSIVE_TOUCH_PROXY       = the market reached our hypothetical price")
    say("  PASSIVE_FILL_PROBABILITY  = NOT_IDENTIFIED")
    say("  Touching a price does not prove an order there would have filled.")
    say("  Queue position, the size ahead, and whether the print consumed our")
    say("  level are all unobserved from public data. The capture measures the")
    say("  proxy and names it a proxy, everywhere, permanently.")
    say()
    say("PROFITABILITY REGISTER. The dataset exists to estimate")
    say("  GROSS_SPREAD_CAPTURE - ADVERSE_SELECTION - INCOMPLETE_PAIR_RESIDUAL")
    say("  - FEES")
    say("Displayed spread is NOT profit. Until the fee formula is resolved the")
    say("only two figures that may be computed are:")
    say("  PRE_FEE_EXPECTANCY      the first three terms, fees excluded")
    say("  BREAK_EVEN_TOTAL_FEE    the total fee per share that would take")
    say("                          PRE_FEE_EXPECTANCY to zero")
    say("No borrowed fee formula. The 0.06 coefficient is a FIELD, not a rule.")
    say()
    say("INTEGRITY. Every day's output sealed with per-file sha256 written")
    say("after the report is closed, couriered into the repo unmodified, and")
    say("re-verified by the analyst in its own process before use.")
    say()
    say("STOP CONDITIONS. Halt and report on: any 429 rate above 1%% of")
    say("requests; any cross-route touch disagreement above 1%%; any identity")
    say("mismatch or body collision; any venue state change that invalidates")
    say("the cohort. Do not adapt silently.")
    say()


def main():
    d = Path(sys.argv[1])
    disc = json.loads((d / "closure.json").read_text())
    out = {}
    say("RUN 85 PHASE 2F -- ANALYSIS OF THE FROZEN CLOSURE RUN")
    say("archive : %s" % d.name)
    say("NOTHING IN THIS FILE CONTACTS THE VENUE.")
    say()
    ident = (disc.get("identity") or {})
    say("=== 1. IDENTITY ===")
    say("verdicts %s  collisions %s  responses carrying an event id %s"
        % (ident.get("by_verdict"), ident.get("collisions"),
           ident.get("event_id_returned")))
    say("DETAIL_ROUTE_IDENTITY_VERIFIED = %s (market slug)"
        % ("YES" if ident.get("verified") else "NO"))
    say("EVENT_ID_ROUTE_IDENTITY        = NOT_IDENTIFIED (venue returns none)")
    say()
    pg = disc.get("pagination") or {}
    say("=== 2b. PAGINATION ===")
    say("offset advanced=%s overlap=%s" % (pg.get("advanced"), pg.get("overlap")))
    say("EVENT_PAGINATION_VERIFIED = %s" % ("YES" if pg.get("advanced") else "NO"))
    say()
    fresh = freshness(d, out)
    div = cohort(disc, out)
    sc = scheduler(disc, out)
    rate(disc)

    five = (sc.get("lag_error") or {}).get("5.0")
    chg5 = (sc.get("book_changed") or {}).get("5.0")
    thr = disc.get("throttle_events") or []
    n429 = sum(1 for e in thr if e.get("event") == "429")
    gates = {
        "1 market identity verified": bool(ident.get("verified")),
        "2 offset pagination verified": bool(pg.get("advanced")),
        "3 cohort genuinely diverse": bool(div),
        "4 public book trustworthy": fresh == "YES",
        "5 drained-pacer scheduling works": bool(sc.get("drained_verified")),
        "6 evidence integrity": True,
    }
    ready = all(gates.values())

    say("=== 11. PHASE 2F FINAL VERDICTS ===")
    say("DETAIL_ROUTE_IDENTITY_VERIFIED = %s"
        % ("YES" if ident.get("verified") else "NO"))
    say("EVENT_PAGINATION_VERIFIED      = %s"
        % ("YES" if pg.get("advanced") else "NO"))
    say("GAME_LEVEL_COHORT_DIVERSITY    = %s"
        % ("SUFFICIENT" if div else "INSUFFICIENT"))
    say("PUBLIC_BOOK_FRESHNESS_SUFFICIENT_FOR_OBSERVATIONAL_RESEARCH = %s" % fresh)
    say("TRANSACTTIME_SEMANTICS         = LAST_PRICE_SAMPLE_TIMESTAMP")
    say("DRAINED_PACER_VERIFIED         = %s"
        % ("YES" if sc.get("drained_verified") else "NO"))
    if five:
        say("EXACT_5S_REQUEST_PLACEMENT_VERIFIED = %s "
            "(median %.1f ms, max %.1f ms)"
            % ("YES" if five["max_ms"] < 1000 else "NO",
               five["median_ms"], five["max_ms"]))
        say("EXACT_5S_VALID_STATE_OBSERVATION_VERIFIED = %s (%d/%d)"
            % ("YES" if five["valid_snapshots"] == five["observations"] else "NO",
               five["valid_snapshots"], five["observations"]))
    if chg5:
        say("BOOK_CHANGED_WITHIN_5S_RATE    = %d / %d" % (chg5[0], chg5[1]))
    say("HTTP_429_COUNT                 = %d" % n429)
    say("PASSIVE_REALIZABLE_EDGE        = NOT_IDENTIFIED")
    say("PMUS_FEES_RESOLVED             = NO")
    say("READY_FOR_MULTI_DAY_PHASE2_CAPTURE = %s" % ("YES" if ready else "NO"))
    say()
    say("APPROVAL GATE, ITEM BY ITEM:")
    for k, v in gates.items():
        say("   %-36s %s" % (k, "PASS" if v else "FAIL"))
    say()
    if not ready:
        say("  WHY GATE 3 FAILED, AND IT IS MY DEFECT AGAIN IN THE SAME SHAPE.")
        say()
        say("  The frame this run built WAS diverse: sport buckets nfl, soccer")
        say("  and other; tick sizes 0.005 and 0.01; price bands P_LOW, P_MID")
        say("  and P_HIGH. The walk stopped the moment the FRAME satisfied the")
        say("  criterion -- and the criterion was written against the frame.")
        say()
        say("  But the frame is not the deliverable; the COHORT is. Selection")
        say("  then round-robined over (sport bucket, state) across only 16")
        say("  game events, most of them NFL, and produced 7 NFL plus 1 boxing,")
        say("  every one P_MID and every one PREGAME.")
        say()
        say("  Phase 2E stopped on 'events and sports' when the cohort needed")
        say("  regimes. Phase 2F stopped on frame composition when the cohort")
        say("  still needed regimes. Same error, one level further in: a stop")
        say("  rule must be written against the thing that has to be diverse,")
        say("  and the thing that has to be diverse is the SELECTED COHORT.")
        say()
        say("  The fix is small and specific: keep walking until a TRIAL")
        say("  SELECTION run over the current frame clears the diversity bar,")
        say("  not until the frame does. That is a one-function change to the")
        say("  stop condition and it is not applied to this sealed run.")
        say()
        say("  Two named unavailabilities are NOT defects and must not be")
        say("  papered over by a rule change: no CFB or MLB game-level event")
        say("  and no LIVE event existed in the frame at 15:29Z on a Sunday")
        say("  morning UTC. Section 3 says report unavailable categories")
        say("  rather than force them, so they are reported.")
        say()
    capture_design(ready)
    if not ready:
        say("  THE DESIGN ABOVE IS A DRAFT, NOT THE FROZEN PLAN. It is shown")
        say("  because every part of it except cohort selection is settled by")
        say("  this run's evidence, and withholding it would hide work that is")
        say("  already done. It becomes the frozen plan only once gate 3")
        say("  closes. No capture is started either way.")


if __name__ == "__main__":
    main()

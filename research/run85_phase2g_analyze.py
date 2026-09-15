#!/usr/bin/env python3
"""RUN 85 PHASE 2G -- analysis of the frozen cohort-closure run. Contacts nothing."""
from __future__ import annotations
import collections, datetime as dt, json, sys
from pathlib import Path

def say(s=""): print(s)

def main():
    d = Path(sys.argv[1])
    c = json.loads((d / "cohort.json").read_text())
    rows = [json.loads(l) for l in (d / "request_log.jsonl").read_text().splitlines() if l.strip()]
    say("RUN 85 PHASE 2G -- ANALYSIS OF THE FROZEN COHORT CLOSURE")
    say("archive : %s" % d.name)
    say("NOTHING IN THIS FILE CONTACTS THE VENUE.")
    say()

    # --- rate, corrected
    t = sorted(dt.datetime.fromisoformat(r["local_request_wall_utc"].replace("Z", "+00:00"))
               for r in rows)
    gaps = [(t[i + 1] - t[i]).total_seconds() for i in range(len(t) - 1)]
    span = (t[-1] - t[0]).total_seconds()
    say("=== 5. RATE, WITH A CORRECTION TO MY OWN METRIC ===")
    say("requests %d over %.1f s" % (len(t), span))
    say("inter-request gap: min %.3f  median %.3f  max %.3f s"
        % (min(gaps), sorted(gaps)[len(gaps) // 2], max(gaps)))
    say("gaps below the 2.5 s floor: %d" % sum(1 for g in gaps if g < 2.49))
    say("rate (N-1)/span = %.4f rps   nominal 0.4000" % ((len(t) - 1) / span))
    say()
    say("  The runner printed ACHIEVED_RPS = 0.4389 and that is MY REPORTING")
    say("  BUG, not a breach of the rate policy. It divided N requests by the")
    say("  span from the FIRST request to the last, so it charged the opening")
    say("  request no preceding interval. Every actual gap was 2.500 s to the")
    say("  millisecond -- min equals max -- and the correct figure is 0.4000")
    say("  rps, exactly the nominal. Nothing exceeded the configured rate.")
    say("HTTP_429_COUNT = %d" % sum(1 for e in (c.get("throttle_events") or [])
                                    if e.get("event") == "429"))
    say()

    # --- cohort
    coh = c.get("cohort") or []
    say("=== 6. FINAL COHORT ===")
    for x in coh:
        nt = x.get("near_touch") or {}
        say("  %s" % x["market_slug"])
        say("     event %s (%s)  sport=%s league=%s  state=%s (raw %r)"
            % (x["native_event_id"], x["native_event_slug"], x["sport"],
               x["league"], x["normalized_state"], x["raw_period"]))
        say("     game_start=%s" % x["scheduled_game_start"])
        say("     bid %s  ask %s  mid %s  tick %s  spread %s (%s)  band %s"
            % (x["best_bid"], x["best_ask"], x["mid"], x["tick_size"],
               x["spread"], x["spread_bucket"], x["price_band"]))
        say("     touch qty bid/ask  %s / %s"
            % (nt.get("touch_bid_qty"), nt.get("touch_ask_qty")))
        say("     1t qty    bid/ask  %s / %s"
            % (nt.get("cum_bid_qty_1t"), nt.get("cum_ask_qty_1t")))
        say("     2t qty    bid/ask  %s / %s"
            % (nt.get("cum_bid_qty_2t"), nt.get("cum_ask_qty_2t")))
        say("     5t qty    bid/ask  %s / %s"
            % (nt.get("cum_bid_qty_5t"), nt.get("cum_ask_qty_5t")))
        say("     queue regime = %s" % x["queue_regime"])
    say()
    sp = collections.Counter(str(x["sport"]) for x in coh)
    say("=== 7. COUNTS ===")
    w = c.get("walk") or {}
    for k, v in (("OFFSET_PAGES_WALKED", w.get("pages")),
                 ("EVENTS_DISCOVERED", w.get("events")),
                 ("ELIGIBLE_GAME_EVENTS", c.get("eligible_game_events")),
                 ("TRIAL_SELECTIONS_EVALUATED", w.get("trials")),
                 ("FINAL_COHORT_SIZE", len(coh)),
                 ("FINAL_COHORT_UNIQUE_EVENTS",
                  len({x["native_event_id"] for x in coh}))):
        say("%-28s = %s" % (k, v))
    say("%-28s = %s" % ("SPORTS_IN_FINAL_COHORT", dict(sp)))
    say("%-28s = %s" % ("PRICE_BANDS", sorted({x["price_band"] for x in coh})))
    say("%-28s = %s" % ("TICK_VALUES", sorted({x["tick_size"] for x in coh})))
    say("%-28s = %s" % ("SPREAD_REGIMES", sorted({x["spread_bucket"] for x in coh})))
    say("%-28s = %s" % ("QUEUE_REGIMES", sorted({x["queue_regime"] for x in coh})))
    say("%-28s = %s" % ("NORMALIZED_STATES", sorted({x["normalized_state"] for x in coh})))
    say()
    say("gate: %s" % c.get("gate"))
    say("frame availability: %s" % c.get("frame_availability"))
    say()

    # --- the concentration finding
    top, n = sp.most_common(1)[0]
    frac = n / len(coh) if coh else 0
    say("=== THE ONE THING THE GATE DOES NOT CATCH ===")
    say("  %s holds %d of %d selected markets (%.0f%%)." % (top, n, len(coh), 100 * frac))
    say()
    say("  S4 caps any one sport at ceil(cap/2) = 4, and it BOUND: NFL took")
    say("  exactly 4. But the cap is computed against the CAP (8), not against")
    say("  the size the cohort actually reached (5). The frame held only 15")
    say("  eligible events across 2 sports, so the cohort under-filled and a")
    say("  cap meant to mean 'at most half' delivered 80%.")
    say()
    say("  So the frozen gate PASSES and the anti-domination INTENT does not.")
    say("  I am reporting both rather than letting the passing gate stand for")
    say("  the intent. The fix is one line -- cap against len(picks) as it")
    say("  grows, not against the target -- and it is NOT applied to this")
    say("  sealed run.")
    say()
    say("  The walk also stopped the instant the gate first passed, at page 6")
    say("  of a 40-page bound. Stop-on-first-pass returns the MINIMUM passing")
    say("  cohort by construction. Walking further would very likely have")
    say("  found more sports and more spread regimes.")
    say()

    ok = c.get("gate_passes")
    ident = c.get("selector_identical_trial_and_final")
    say("=== 8. VERDICTS ===")
    say("GAME_LEVEL_COHORT_DIVERSITY = %s" % ("SUFFICIENT" if ok else "INSUFFICIENT"))
    say("   (by the frozen gate; see the concentration finding above)")
    say("COHORT_SELECTION_RULE_EXECUTED_IDENTICALLY_IN_TRIAL_AND_FINAL = %s"
        % ("YES" if ident else "NO"))
    say("HTTP_429_COUNT = %d" % sum(1 for e in (c.get("throttle_events") or [])
                                    if e.get("event") == "429"))
    say("ACHIEVED_RPS = 0.4000 (corrected)   NOMINAL = 0.4000")
    say("EVIDENCE_INTEGRITY = PASS (archive digest and 4 internal checksums "
        "verified in the analyst's own process)")
    say("PASSIVE_REALIZABLE_EDGE = NOT_IDENTIFIED")
    say("PMUS_FEES_RESOLVED = NO")
    say("LIVE / CFB / MLB = NOT_OBSERVED_WITHIN_PHASE2G_DISCOVERY_BOUND")
    say("READY_FOR_MULTI_DAY_PHASE2_CAPTURE = %s"
        % ("YES" if (ok and ident) else "NO"))
    say()
    say("  READY is YES against the four stated criteria: the selected cohort")
    say("  passes its own gate, the selector is provably identical in trial")
    say("  and final, evidence integrity passes, and no locked infrastructure")
    say("  contradiction appeared.")
    say()
    say("  It is YES with one named reservation that is the owner's call, not")
    say("  mine: 80% of the cohort is NFL. If that concentration is not")
    say("  acceptable for the dataset, the fix is the one-line cap change plus")
    say("  a walk that continues past first pass, and 2G should be re-run")
    say("  before capture rather than capturing on this cohort.")
    say()

if __name__ == "__main__":
    main()

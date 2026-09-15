#!/usr/bin/env python3
"""RUN 85 PHASE 2E -- analysis of the frozen closure run. Contacts nothing.

Judges the two verdicts the runner was told not to assert, and corrects three
the runner DID assert that do not survive a look at the bytes it sealed.

Run:  python3 research/run85_phase2e_analyze.py <p2e-dir> [<p2d-dir>]
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

HORIZONS = (5.0, 10.0, 30.0, 60.0)


def say(s=""):
    print(s)


def load(d: Path):
    return json.loads((d / "discovery.json").read_text())


# ------------------------------------------------------------ 1. identity
def section_identity(disc):
    say("=== 1. DETAIL/BOOK ROUTE IDENTITY, AND WHAT IT DOES NOT COVER ===")
    it = disc["identity"]
    rows = it["rows"]
    by_route = collections.Counter((r["route"], r["verdict"]) for r in rows)
    say("probes: %d" % len(rows))
    for (route, v), n in sorted(by_route.items()):
        say("   %-7s %-28s %4d" % (route, v, n))
    say("distinct requested slugs sharing one response body: %d"
        % it["body_collisions"])
    say()
    say("  SLUG LIMB: verified. Every request's response carried back the slug")
    say("  that was asked for, on BOTH routes -- market-detail and book -- and")
    say("  no two different requested slugs returned one body. Phase 2D only")
    say("  ever checked detail, and only checked that FIELD VALUES agreed,")
    say("  which is a different and weaker question than binding.")
    say()
    say("  EVENT-ID LIMB: NOT TESTABLE. responses carrying an event id = %d."
        % it["event_id_returned"])
    say("  The detail payload does not include an event id field at all, so")
    say("  REQUESTED_EVENT_ID == RESPONSE_EVENT_ID could not be evaluated on a")
    say("  single probe. That limb is RESPONSE_ID_NOT_IDENTIFIED for all %d"
        % len(rows))
    say("  probes, and '44 IDENTITY_MATCH' must not be read as covering it.")
    say()
    say("  Event identity therefore still rests on the /v1/events payload's own")
    say("  market->event grouping, which is native and structured, but is NOT")
    say("  re-confirmed by the per-market routes.")
    say()
    say("DETAIL_ROUTE_IDENTITY_VERIFIED = YES (slug limb, both routes)")
    say("EVENT_ID_ROUTE_IDENTITY        = NOT_IDENTIFIED (venue returns none)")
    say()


# ---------------------------------------------------------- 2. pagination
def section_pagination(disc):
    say("=== 2. PAGINATION PROOF ===")
    for c in disc["pagination_proof"]:
        say("   %-8s http=%s events=%3d overlap_with_page1=%3d "
            "reproduces_page1=%-5s -> %s"
            % (c["param"], c["http"], c["events"], c["overlap_with_page1"],
               c["reproduces_page1"], "ADVANCES" if c["advanced"] else "IGNORED"))
        say("            id_set_sha256 = %s" % c["id_set_sha256"])
    say()
    say("  `page` returned HTTP 200 with a full 100-event body whose id-set")
    say("  hash is IDENTICAL to page 1's, and 100 of 100 ids overlap. It is")
    say("  silently ignored, exactly as in Phase 2D. `offset` advances with")
    say("  zero overlap. The hashes are archived so this is checkable from the")
    say("  bytes rather than from this sentence.")
    say()
    say("EVENT_PAGINATION_VERIFIED = YES, mechanism = offset")
    say()


# ------------------------------------------------- 3/4. frame and taxonomy
def section_frame(disc):
    say("=== 3/4. FRAME, TAXONOMY, AND A DEFECT IN MY OWN STOP RULE ===")
    tax, fr = disc["taxonomy"], disc["frame"]
    say("walk stopped: %s after %d pages"
        % (disc["walk_stop_reason"], len(disc["pages"])))
    say()
    for k, n in sorted(tax["by_class"].items(), key=lambda kv: -kv[1]):
        say("   %-18s %7d" % (k, n))
    say()
    say("PHASE2D_GAME_LEVEL_FLOOR = %d   (2D's frozen allowlist, on THIS walk)"
        % tax["phase2d_floor"])
    say("PHASE2E_GAME_LEVEL_COUNT = %d   (+%d GAME_THREE_WAY)"
        % (tax["phase2e_count"], tax["phase2e_count"] - tax["phase2d_floor"]))
    say("GAME_PROP counted separately and excluded = %d"
        % tax["by_class"].get("GAME_PROP", 0))
    say()
    say("GAME_ROWS_DISCOVERED          = %d" % fr["game_rows"])
    say("GAME_UNIQUE_EVENTS_DISCOVERED = %d" % fr["game_events"])
    say("GAME_ACTIVE_EVENTS_DISCOVERED = %d" % fr["active_events"])
    say("SPORT_DISTRIBUTION_BY_EVENT   = %s" % fr["sport_distribution_by_event"])
    say("NORMALIZED_STATE_BY_EVENT     = %s" % fr["normalized_state_by_event"])
    say()
    say("  DEFECT 1. MY SUFFICIENCY CRITERION STOPPED THE WALK TOO EARLY, AND")
    say("  IT CUT OFF EXACTLY THE DIVERSITY SECTION 6 NEEDED.")
    say()
    say("  The criterion I preregistered -- 40 events carrying a GAME_BINARY")
    say("  market across 4 sports -- was met at page 16 and the walk stopped.")
    say("  Phase 2D, which ran to a 24-page bound, found the game-level mass on")
    say("  pages 17-23. So the criterion fired one page before the richest part")
    say("  of the list and this run saw %d game events where 2D saw 359."
        % fr["game_events"])
    say()
    say("  The criterion counted EVENTS and SPORTS. It did not count LIVE")
    say("  events, tick sizes, price bands or queue shapes -- the dimensions")
    say("  section 6 actually asks the cohort to span. A stop rule should be")
    say("  written against the thing it is protecting, and mine was not.")
    say()
    nones = fr["sport_distribution_by_event"].get("None", 0)
    say("  DEFECT 2. SPORT IS UNREADABLE ON %d OF %d GAME EVENTS."
        % (nones, fr["game_events"]))
    say("  primaryTag is null on those, and they are the UFC events. Sport")
    say("  stratification is running blind on nearly half the frame, and a")
    say("  cohort 'spanning 4 sports' counts None as one of them.")
    say()


# -------------------------------------------------------- 6/7. the cohort
def section_cohort(disc):
    say("=== 6/7. COHORT AND NEAR-TOUCH QUEUE METRIC ===")
    c = disc["cohort"]
    say("%-44s %-7s %-8s %-10s %-7s" % ("market", "sport", "state", "queue", "tick"))
    for x in c:
        say("%-44s %-7s %-8s %-10s %-7s"
            % (x["market_slug"][:44], str(x["sport"]), x["normalized_state"],
               x["queue_class"], x["tick_size"]))
    say()
    q = collections.Counter(x["queue_class"] for x in c)
    st = collections.Counter(x["normalized_state"] for x in c)
    sp = collections.Counter(str(x["sport"]) for x in c)
    tk = collections.Counter(str(x["tick_size"]) for x in c)
    say("queue: %s" % dict(q))
    say("state: %s" % dict(st))
    say("sport: %s" % dict(sp))
    say("tick : %s" % dict(tk))
    say()
    say("  THE QUEUE METRIC BEHAVES DIFFERENTLY FROM THE RETRACTED ONE, AND")
    say("  THAT IS THE POINT. Phase 2C's whole-ladder dollar measure called 18")
    say("  of 19 books ASK_HEAVY. Quantity within one tick calls these %d"
        % q.get("BALANCED", 0))
    say("  BALANCED and %d BID_HEAVY, with no ASK_HEAVY at all. Same venue,")
    say("  different instrument, opposite picture -- which is what a")
    say("  contaminated measure being replaced looks like.")
    say()
    say("  GAME_LEVEL_COHORT_DIVERSITY = INSUFFICIENT, on three counts:")
    say("    * every market is PREGAME. Not one LIVE market was selected, so")
    say("      the pregame/live contrast section 6 names is absent entirely.")
    say("    * the sports are %s -- no CFB, no MLB, and 'None' is the UFC"
        % dict(sp))
    say("      rows whose sport the venue does not label.")
    say("    * queue shapes span two of three classes and price bands were")
    say("      never a stratification key at all.")
    say()
    say("  The cohort is honestly built -- round-robin over (sport, state),")
    say("  one market per event, ordered by native event id, no slug anywhere.")
    say("  Phase 2D DEFECT 2 is fixed. What is wrong now is upstream: the")
    say("  frame it selected from was truncated by DEFECT 1.")
    say()


# --------------------------------------------------------- 11. scheduler
def section_scheduler(disc):
    say("=== 11. EXACT-HORIZON SCHEDULER -- TWO SEPARATE QUESTIONS ===")
    sc = disc["scheduler"]
    say("plan: %d reads over %.1f s, mean %.3f rps, min gap %.1f s, "
        "throttled during test: %s"
        % (sc["plan_reads"], sc["span_s"], sc["mean_rps"], sc["min_gap_s"],
           sc["throttled_during_test"]))
    say()
    say("A. REQUEST PLACEMENT")
    say("%-8s %6s %12s %12s %12s" % ("horizon", "n", "median", "p90", "max"))
    for h in HORIZONS:
        st = (sc.get("lag_error") or {}).get(str(h))
        if not st:
            continue
        say("%-8.0f %6d %10.1fms %10.1fms %10.1fms"
            % (h, st["n"], st["median_ms"], st["p90_ms"], st["max_ms"]))
    say()
    obs = sc["observations"]
    late = [(o["target_offset_s"], o["actual_offset_s"] - o["target_offset_s"])
            for o in obs]
    late.sort()
    say("  lateness against target, in schedule order:")
    for t, l in late:
        say("     target %6.1fs  late %+.3fs" % (t, l))
    say()
    say("  THE ERROR IS A DECAYING STARTUP DEBT, NOT JITTER. The first read")
    say("  fired 2.60 s late and each subsequent read is less late, settling")
    say("  to about 0.02 s. Request latency is not the cause -- it is 31 ms")
    say("  median and 145 ms at worst. The cause is that the plan's t0 was")
    say("  taken while the pacer still owed spacing from the preceding cohort")
    say("  reads, so the whole plan started displaced and then caught up as")
    say("  its own slack absorbed the debt.")
    say()
    say("  That is a real defect and it is mine: a timed plan must begin from")
    say("  a drained pacer, and the driver did not drain it. It is also")
    say("  entirely fixable and does not implicate the venue.")
    say()
    say("  EXACT_HORIZON_REQUEST_PLACEMENT = DEMONSTRATED, WITH A NAMED")
    say("  STARTUP DEFECT. Once settled, placement error is ~20 ms.")
    say()
    say("B. MARKOUT OBSERVABILITY -- THE QUESTION THAT ACTUALLY MATTERS")
    by = collections.defaultdict(dict)
    for o in obs:
        by[o["market_slug"]][o["horizon_s"]] = o["venue_transact_time"]
    say("%-44s %6s %6s %6s %6s" % ("market", "5s", "10s", "30s", "60s"))
    tally = collections.Counter()
    for m, v in by.items():
        row = []
        for h in HORIZONS:
            new = v.get(h) != v.get(0.0)
            row.append("NEW" if new else "SAME")
            tally[(h, "NEW" if new else "SAME")] += 1
        say("%-44s %6s %6s %6s %6s" % (m[:44], *row))
    say()
    for h in HORIZONS:
        say("   h=%-5.0f venue book state CHANGED in %d of %d subjects"
            % (h, tally[(h, "NEW")], len(by)))
    say()
    say("  A MARKOUT NEEDS TWO DISTINCT BOOK STATES. At 5 s, %d of %d subjects"
        % (tally[(5.0, "NEW")], len(by)))
    say("  returned the SAME venue transactTime as t0 -- the identical book,")
    say("  re-served. One subject returned one transactTime for all five reads")
    say("  across the whole 60 s span; another was serving a book stamped 22")
    say("  minutes before the request.")
    say()
    say("  So the 5 s markout is not blocked by the rate limit, and it is not")
    say("  blocked by scheduling. On quiet pregame markets it is blocked")
    say("  because THE BOOK DOES NOT MOVE. Placing a read at t+5.000 s buys")
    say("  nothing when the venue hands back the state from t.")
    say()
    say("  EXACT_5S_SCHEDULER_EMPIRICALLY_VERIFIED = NO, as a clean claim.")
    say("  The runner asserted YES on the grounds that no throttle fired during")
    say("  the test. That was the wrong test. Split it:")
    say("     EXACT_5S_REQUEST_PLACEMENT_FEASIBLE = YES")
    say("     EXACT_5S_MARKOUT_OBSERVED           = 1 of 3 subjects")
    say("     EXACT_5S_MARKOUT_GENERALLY_USABLE   = NOT_IDENTIFIED")
    say()
    say("  This also reframes the whole horizon question. Book-update cadence,")
    say("  not request cadence, may be the binding constraint on short")
    say("  markouts -- and that is measurable, on a bigger sample, before any")
    say("  long capture is designed around a 5 s offset.")
    say()


# ------------------------------------------------------------- 12. CFB
def section_cfb(disc, p2d):
    say("=== 12. CFB TASK #48 RECHECK, ACROSS BOTH ARCHIVES ===")
    say("TASK48_PRIOR_CONCLUSION, recorded verbatim and NOT altered:")
    say('  "college football moneylines and spreads never map -- the grammar')
    say('   class finds no full-game per-side contract (the venue\'s atc rows')
    say('   for cfb are segment props) so no aec-cfb book has ever opened"')
    say()
    cf = disc.get("cfb_recheck") or {}
    found = cf.get("found") or []
    say("PHASE 2E walk (stopped early at the sufficiency criterion):")
    say("   aec-cfb-* game-level rows found: %d" % len(found))
    for f in found:
        say("      %-38s %-32s %s"
            % (f["market_slug"], f["sports_market_type"], f["state"]))
    say()
    if p2d:
        g = p2d["game_level"]
        cfb = [x for x in g if (x.get("market_slug") or "").startswith("aec-cfb-")]
        states = collections.Counter(x["state"] for x in cfb)
        types = collections.Counter(x["sports_market_type"] for x in cfb)
        two = sum(1 for x in cfb
                  if isinstance(x.get("market_sides"), list)
                  and len(x["market_sides"]) == 2)
        say("PHASE 2D walk (24 pages, the deeper one):")
        say("   aec-cfb-* game-level rows: %d" % len(cfb))
        say("   state: %s" % dict(states))
        say("   type : %s" % dict(types))
        say("   two-sided marketSides: %d of %d" % (two, len(cfb)))
        say()
        say("  On 2E alone the answer would rest on ONE row, and that row is")
        say("  already resolved. The deeper 2D walk carries %d, of which %d are"
            % (len(cfb), states.get("MARKET_STATUS_OPEN", 0)))
        say("  currently OPEN, all %d two-sided, every one typed" % two)
        say("  football_team_full_game_winner -- the full-game per-side")
        say("  contract the prior conclusion says does not exist.")
        say()
    say("CURRENT_CFB_MONEYLINE_CONTRACTS_FOUND = YES")
    say()
    say("  WHAT THIS DOES NOT SAY. It is evidence about the venue NOW. Task")
    say("  #48 was a conclusion about the venue THEN, and neither archive")
    say("  holds point-in-time evidence from that instant, so nothing here")
    say("  claims the conclusion was wrong when it was drawn. The historical")
    say("  evidence is untouched.")
    say()
    say("  What it does say is that it cannot be carried forward as a")
    say("  present-tense fact, and that the desk's CFB mapping work should be")
    say("  re-derived against current venue rows rather than against it.")
    say("TASK48_CONCLUSION_SAFE_TO_CARRY_FORWARD = NO")
    say()


def section_rate(disc):
    say("=== 10. RATE CONTROL ===")
    ev = disc.get("throttle_events") or []
    say("HTTP_429_COUNT = %d" % sum(1 for e in ev if e.get("event") == "429"))
    for e in ev:
        say("   %s" % e)
    say("venue requests spent = %s of 220 disclosed"
        % disc.get("venue_requests_spent"))
    say("final pacer spacing  = %s s" % disc.get("final_spacing_s"))
    say()
    say("  A second 429 at nominal 0.5 rps, on a second consecutive run, again")
    say("  answered with Retry-After: 10 and again fully honoured. Two runs,")
    say("  two 429s: 0.5 rps is not a safe rate, it is a rate the venue")
    say("  tolerates most of the time.")
    say("  0.5_RPS_UNCONDITIONALLY_SAFE = NO")
    say("  RPS_LIMIT_NOT_ESTABLISHED    = CARRIED FORWARD")
    say("  Nothing here argues for raising the rate.")
    say()


def main():
    d = Path(sys.argv[1])
    disc = load(d)
    p2d = load(Path(sys.argv[2])) if len(sys.argv) > 2 else None
    say("RUN 85 PHASE 2E -- ANALYSIS OF THE FROZEN CLOSURE RUN")
    say("archive : %s" % d.name)
    say("NOTHING IN THIS FILE CONTACTS THE VENUE.")
    say()
    section_identity(disc)
    section_pagination(disc)
    section_frame(disc)
    section_cohort(disc)
    section_scheduler(disc)
    section_rate(disc)
    section_cfb(disc, p2d)

    tax, fr = disc["taxonomy"], disc["frame"]
    c = disc["cohort"]
    q = collections.Counter(x["queue_class"] for x in c)
    ev = disc.get("throttle_events") or []
    say("=== 14. PHASE 2E VERDICTS, ANALYST-JUDGED ===")
    say("DETAIL_ROUTE_IDENTITY_VERIFIED          = YES (slug limb, both routes)")
    say("EVENT_ID_ROUTE_IDENTITY                 = NOT_IDENTIFIED "
        "(venue returns no event id)")
    say("EVENT_PAGINATION_VERIFIED               = YES (offset; page ignored)")
    say("GAME_LEVEL_MARKETS_FOUND                = YES")
    say("PHASE2D_GAME_LEVEL_FLOOR                = %d" % tax["phase2d_floor"])
    say("PHASE2E_GAME_LEVEL_ROWS                 = %d" % tax["phase2e_count"])
    say("PHASE2E_GAME_LEVEL_UNIQUE_EVENTS        = %d" % fr["game_events"])
    say("GAME_LEVEL_COHORT_DIVERSITY             = INSUFFICIENT")
    say("GAME_STATE_CLASSIFICATION_READY         = YES")
    say("NEAR_TOUCH_QUEUE_METRIC_READY           = YES  %s" % dict(q))
    say("EXACT_5S_SCHEDULER_EMPIRICALLY_VERIFIED = NO")
    say("   EXACT_5S_REQUEST_PLACEMENT_FEASIBLE  = YES (startup defect named)")
    say("   EXACT_5S_MARKOUT_OBSERVED            = 1 of 3 subjects")
    say("   EXACT_5S_MARKOUT_GENERALLY_USABLE    = NOT_IDENTIFIED")
    say("CURRENT_CFB_MONEYLINE_CONTRACTS_FOUND   = YES")
    say("TASK48_CONCLUSION_SAFE_TO_CARRY_FORWARD = NO")
    say("HTTP_429_COUNT                          = %d"
        % sum(1 for e in ev if e.get("event") == "429"))
    say("PMUS_FEES_RESOLVED                      = NO")
    say("PASSIVE_REALIZABLE_EDGE                 = NOT_IDENTIFIED")
    say("READY_FOR_MULTI_DAY_PHASE2_CAPTURE      = NO")
    say()
    say("  AGAINST THE APPROVAL GATE, ITEM BY ITEM:")
    say("    1 detail request identity verified ......... YES (slug limb)")
    say("    2 event pagination actually advances ....... YES")
    say("    3 diverse events, no alphabetical bias ..... BIAS FIXED,")
    say("                                                 DIVERSITY NOT MET")
    say("    4 game-state/time fields understood ........ YES")
    say("    5 near-touch queue metrics valid ........... YES")
    say("    6 scheduler produces useful horizons ....... NOT ESTABLISHED")
    say("    7 evidence archive integrity ............... YES")
    say()
    say("  Gate items 3 and 6 fail, so READY is NO. Both failures trace to")
    say("  this run rather than to the venue: a stop rule I wrote against the")
    say("  wrong quantity, and a scheduler I started from an undrained pacer.")
    say()
    say("  The item-6 failure is the more interesting one, because fixing my")
    say("  scheduler will not fix it. The binding constraint on a 5 s markout")
    say("  looks like the venue's BOOK-UPDATE cadence, not our request")
    say("  cadence, and that has to be measured on a real sample before any")
    say("  capture is designed around a short horizon.")
    say()
    say("  Fee resolution is not required for passive observational collection")
    say("  and is not claimed: PMUS_FEES_RESOLVED = NO, and no borrowed fee")
    say("  formula entered anything above.")


if __name__ == "__main__":
    main()

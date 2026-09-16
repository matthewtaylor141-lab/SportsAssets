#!/usr/bin/env python3
"""RUN 85 PHASE 2D -- analysis of the frozen discovery. Contacts nothing.

Reads only the sealed Phase 2D archive and judges the two verdicts the runner
was told not to assert: GAME_LEVEL_DISCOVERY_COVERAGE and
PHASE2C_FUTURES_ONLY_RESULT_EXPLAINED, with H1-H6 weighed separately.

It also records two defects the runner's own report does not show, one of them
a repeat of the error this very run was built to fix.

Run:  python3 research/run85_phase2d_analyze.py <p2d-dir>
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

# The venue's own enum, as observed across 73,596 market rows. GAME_V2 in the
# driver was frozen before the data and did NOT contain DRAWABLE_OUTCOME,
# because that value had never been seen. See DEFECT 1.
GAME_ALLOWLIST = {"SPORTS_MARKET_TYPE_MONEYLINE", "SPORTS_MARKET_TYPE_SPREAD",
                  "SPORTS_MARKET_TYPE_TOTAL", "SPORTS_MARKET_TYPE_TOTALS"}


def say(s=""):
    print(s)


def load(d: Path):
    disc = json.loads((d / "discovery.json").read_text())
    cal = [json.loads(x) for x in
           (d / "calibration.jsonl").read_text().splitlines() if x.strip()]
    return disc, cal


# --------------------------------------------------------- A. discovery
def section_pagination(disc):
    say("=== A1. PAGINATION MECHANISM ===")
    for f in disc["pagination_probe"]:
        say("  %-12s %-18s http=%s events=%3d ids %s..%-7s -> %s"
            % (f["param"], json.dumps(f["extra"]), f["http"], f["events"],
               f["ids_min"], f["ids_max"], f["verdict"]))
    say()
    say("  PAGINATION_MECHANISM = offset")
    say()
    say("  THE TRAP THIS PROBE WAS BUILT TO CATCH, AND DID. `page=2` and")
    say("  `skip=100` both answered HTTP 200 with a full 100-event body -- and")
    say("  BOTH returned the identical first page, ids 6435..44391. A probe")
    say("  that judged success by status code, or by 'did I get rows back',")
    say("  would have recorded page 2 as walked and silently re-counted page 1")
    say("  twenty-three times. The probe judged by whether the returned id set")
    say("  was DISJOINT from the first, which is the only question that")
    say("  distinguishes a working parameter from an ignored one.")
    say()
    say("  The venue accepts unknown query parameters without complaint. Any")
    say("  future code that paginates this API must verify the page MOVED.")
    say()


def section_walk(disc):
    say("=== A2. THE BOUNDED WALK, PAGE BY PAGE ===")
    say("%-4s %8s %8s %7s %7s %7s %7s  %s"
        % ("page", "id_min", "id_max", "rows", "GAME", "FUT", "OTHER", "periods"))
    tot = collections.Counter()
    game_pages = []
    for p in disc["pages"]:
        c = p["class_distribution"]
        tot.update(c)
        g = c.get("GAME_LEVEL", 0)
        if g:
            game_pages.append((p["page"], g))
        per = p["event_period_distribution"]
        live = {k: v for k, v in per.items() if k not in ("NS", "")}
        say("%-4s %8s %8s %7d %7d %7d %7d  %s"
            % (p["page"], p["event_ids_min"], p["event_ids_max"],
               p["market_rows"], g, c.get("FUTURES", 0),
               c.get("OTHER_STRUCTURED_SPORTS", 0),
               ("NS=%d" % per.get("NS", 0)) + (" LIVE/OTHER=%s" % live if live else "")))
    say()
    say("TOTAL across the walk: %s" % dict(tot))
    say("WALK_STOP_REASON = %s" % disc["walk_stop_reason"])
    say()
    say("  Event ids ascend monotonically across pages, 6435 on page 0 to")
    say("  109,956 on page 23. The ordering is id-ascending and `offset` walks")
    say("  it, so Phase 2C's single unpaginated call read the OLDEST 100 events")
    say("  in existence.")
    say()
    say("  GAME_LEVEL markets appear on pages %s."
        % ", ".join(str(p) for p, _ in game_pages))
    say("  They are NOT uniformly spread: the mass sits at high ids, pages")
    say("  17-23 (ids 99,994-109,956) plus a block on page 5.")
    say()
    say("  The `period` field is the venue's live-state field and it is only")
    say("  visible out here. Page 0 showed NS on all 100 events. The later")
    say("  pages carry FT, SUSP, CAN, POST, LIVE, Live, IN8, IN9, Game 1,")
    say("  Map 1, Map 4 and running clocks -- 5', 35', 37', 63', 64', 78'.")
    say("  PMUS_EVENT_PERIOD_IS_A_LIVE_STATE_FIELD = YES")
    say()
    return tot


# ------------------------------------------------ C. classification
def section_classification(disc):
    say("=== C. CLASSIFICATION, AND A DEFECT IN IT ===")
    v2 = collections.Counter()
    for p in disc["pages"]:
        v2.update(p["sports_market_type_v2"])
    say("sportsMarketTypeV2 across every walked row:")
    for k, n in v2.most_common():
        mark = "  <- GAME by the frozen allowlist" if k in GAME_ALLOWLIST else ""
        say("   %-44s %7d%s" % (k, n, mark))
    say()
    say("classification as the driver recorded it: %s" % disc["classification"])
    say()
    say("  DEFECT 1. THE GAME ALLOWLIST UNDERCOUNTS, AND I FROZE IT TOO NARROW.")
    say()
    dr = v2.get("SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME", 0)
    say("  SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME (%d rows) was not in the" % dr)
    say("  allowlist, because I had never seen the value and froze the list")
    say("  before the data. Every one of those %d rows carries the v1 type" % dr)
    say("  `soccer_team_full_time_winner` -- the soccer three-way match")
    say("  winner. That is a GAME-LEVEL market by any reading, and it is")
    say("  precisely the soccer shape the desk already cares about.")
    say()
    say("  So GAME_LEVEL_MARKETS_FOUND = YES is not affected, but the COUNT is")
    say("  a floor, not a total. I am not retroactively widening the allowlist")
    say("  to inflate the number: the rule was preregistered, it ran as")
    say("  written, and the honest report is the count it produced plus this")
    say("  named omission.")
    say()
    say("    GAME_LEVEL_ROWS_UNDER_FROZEN_ALLOWLIST = %d"
        % disc["classification"].get("GAME_LEVEL", 0))
    say("    DRAWABLE_OUTCOME_ROWS_EXCLUDED         = %d" % dr)
    say("    GAME_LEVEL_INCLUDING_DRAWABLE_OUTCOME  = %d"
        % (disc["classification"].get("GAME_LEVEL", 0) + dr))
    say("    ALLOWLIST_REVISION_REQUIRED_BEFORE_NEXT_RUN = YES")
    say()
    pr = v2.get("SPORTS_MARKET_TYPE_PROP", 0)
    say("  SPORTS_MARKET_TYPE_PROP (%d rows) is a genuinely separate" % pr)
    say("  question, not an oversight. Its v1 values are player and team")
    say("  props -- football_player_receiving_yards, soccer_game_exact_score,")
    say("  football_team_total_first_downs. These attach to a game but are not")
    say("  a team moneyline, spread or total. They stay OTHER_STRUCTURED_SPORTS")
    say("  until someone decides deliberately, which is not this run's call.")
    say()
    return v2


def section_game_detail(disc):
    say("=== C2. WHAT THE GAME-LEVEL MARKETS ARE ===")
    g = disc["game_level"]
    say("rows %d across %d unique native events"
        % (len(g), len({x["native_event_id"] for x in g})))
    say()
    say("sport (venue primaryTag):")
    for k, n in collections.Counter(str(x["sport"]) for x in g).most_common(14):
        say("   %-10s %6d" % (k, n))
    say()
    say("structured type:")
    for k, n in collections.Counter(x["sports_market_type_v2"] for x in g).most_common():
        say("   %-40s %6d" % (k, n))
    say()
    say("state: %s"
        % dict(collections.Counter(str(x["state"]) for x in g)))
    say("tick size: %s"
        % dict(collections.Counter(str(x["tick_size"]) for x in g)))
    say("feeCoefficient field: %s"
        % dict(collections.Counter(str(x["fee_coefficient_field"]) for x in g)))
    two = sum(1 for x in g if isinstance(x.get("market_sides"), list)
              and len(x["market_sides"]) == 2)
    say("rows with exactly two marketSides: %d of %d" % (two, len(g)))
    quoted = sum(1 for x in g if x.get("best_bid_quote") and x.get("best_ask_quote"))
    say("rows carrying BOTH touch quotes in discovery: %d" % quoted)
    say()
    say("  A THIRD TICK SIZE. 0.005 appears here; Phase 2B saw 0.001 and Phase")
    say("  2C saw 0.01 and 0.001. Three distinct values across three runs.")
    say("  PMUS_ORDER_PRICE_MIN_TICK_SIZE is per-market and no single value may")
    say("  ever be carried forward. A spread counted in ticks is comparable")
    say("  only within one tick size.")
    say()
    say("  The touch quotes in discovery are a DISPLAY figure carried on the")
    say("  market row, not a book read. Phase 2B's detail-vs-book check found")
    say("  them usable but not interchangeable with book state; nothing here")
    say("  revisits that, and no economic quantity is computed from them.")
    say()


# ----------------------------------------------------- D. hypotheses
def section_hypotheses(disc):
    say("=== D. WHY PHASE 2C FOUND ONLY FUTURES ===")
    pages = disc["pages"]
    p0 = pages[0]
    say("H1  game markets genuinely absent at capture time")
    say("    AGAINST, strongly. Phase 2C ran 2026-09-13T03:40Z; this ran")
    say("    2026-09-13T11:36Z, eight hours later, and found %d game-level"
        % disc["classification"].get("GAME_LEVEL", 0))
    say("    rows. Many are for games dated 2026-09-13 and 2026-09-19, so they")
    say("    existed at 03:40Z: an NFL game kicking off 2026-09-13T17:00Z was")
    say("    listed well before either run.")
    say("    NOT FULLY EXCLUDED: this run did not re-query at 03:40Z and")
    say("    cannot, so 'the exact same listing existed then' is inference")
    say("    from creation dates, not a measurement. H1 = REJECTED ON")
    say("    BALANCE, NOT DISPROVEN.")
    say()
    say("H2  game markets exist on later pages")
    say("    CONFIRMED, and this is the mechanism. Page 0 of the walk")
    say("    reproduces Phase 2C exactly: ids %s..%s, %d rows, %d game-level."
        % (p0["event_ids_min"], p0["event_ids_max"], p0["market_rows"],
           p0["class_distribution"].get("GAME_LEVEL", 0)))
    say("    Ids ascend monotonically with paging and the game-level mass sits")
    say("    at high ids. Phase 2C issued ONE unpaginated /v1/events call, so")
    say("    it read the oldest 100 events and stopped.")
    say("    H2 = CONFIRMED")
    say()
    say("H3  game markets require another public query or filter")
    say("    AGAINST as a requirement. No filter beyond `offset` was needed:")
    say("    the same active=true&closed=false query reaches them once paged.")
    say("    PARTLY SUPPORTED as an efficiency matter -- a query that sorted or")
    say("    filtered by start time would reach them far more cheaply than 24")
    say("    pages. No such parameter was probed. H3 = NOT REQUIRED; A CHEAPER")
    say("    PATH IS NOT_IDENTIFIED.")
    say()
    say("H4  game markets use different structured metadata")
    say("    PARTLY CONFIRMED, and it matters. They carry the same")
    say("    sportsMarketType/sportsMarketTypeV2 fields, so Phase 2C's")
    say("    admission logic would have ADMITTED them. But their v2 values are")
    say("    SPREAD / TOTAL / MONEYLINE / DRAWABLE_OUTCOME where futures carry")
    say("    FUTURE, and the live-state `period` field only takes non-NS values")
    say("    out here. Phase 2C could not have known that from page 0.")
    say("    H4 = PARTLY CONFIRMED (different VALUES, same FIELDS)")
    say()
    say("H5  our admission logic excluded them")
    say("    AGAINST. Phase 2C required native event identity, a structured")
    say("    sports field, two marketSides, open state and a tick size. Every")
    say("    game-level row here satisfies all five: %d of %d have exactly two"
        % (sum(1 for x in disc["game_level"]
               if isinstance(x.get("market_sides"), list)
               and len(x["market_sides"]) == 2), len(disc["game_level"])))
    say("    marketSides, all carry a tick size and a structured type.")
    say("    The markets never reached admission -- discovery stopped first.")
    say("    H5 = REJECTED")
    say()
    say("H6  NOT_IDENTIFIED")
    say("    Not needed. H2 explains the result with a demonstrated mechanism.")
    say()
    say("  PHASE2C_FUTURES_ONLY_RESULT_EXPLAINED = YES")
    say("  CAUSE = UNPAGINATED DISCOVERY OVER AN ID-ASCENDING EVENT LIST")
    say()


# ------------------------------------------------- E/F. calibration
def section_calibration(disc, cal):
    say("=== E/F. GAME-MARKET CALIBRATION, AND MY OWN REPEATED DEFECT ===")
    markets = sorted({r["market_slug"] for r in cal})
    say("markets calibrated: %d   reads: %d" % (len(markets), len(cal)))
    say()
    say("  DEFECT 2. THE CALIBRATION SAMPLE IS AN ALPHABETICAL SLICE.")
    say()
    say("  The 12 markets were chosen as sorted(by_event, key=market_slug)[:12].")
    say("  That is the SAME slug-ordered cut that this run's event-first census")
    say("  exists to replace, and that I criticised in the Phase 2C addendum")
    say("  four hours ago. The result is exactly what the criticism predicted:")
    for m in markets:
        say("     %s" % m)
    say()
    fam = collections.Counter(m.split("-")[1] for m in markets)
    say("  families represented: %s" % dict(fam))
    say("  Eleven of twelve are college football and one is boxing, out of a")
    say("  pool spanning cfb, nfl, epl, bun, lg1, uel, mls, mlb, sea and")
    say("  esports. No NFL market was calibrated at all, and NFL is the sport")
    say("  the desk most needs characterized.")
    say()
    say("  The near-touch NUMBERS below are real measurements of the markets")
    say("  named. They are NOT a characterization of PMUS game markets, and I")
    say("  am not presenting them as one. GAME_MARKET_CALIBRATION_SAMPLE =")
    say("  UNREPRESENTATIVE_BY_CONSTRUCTION.")
    say()
    say("  The fix belongs in the driver -- stratify the calibration pick the")
    say("  way the census pick is stratified -- and it is not applied here,")
    say("  because editing the sealed run's inputs after seeing its output is")
    say("  the thing that must never happen.")
    say()
    say("per-market near-touch, first read of each (quantity; tick is the")
    say("market's own):")
    say("  %-46s %9s %9s %11s %11s"
        % ("market", "best_bid", "best_ask", "cum_bid_1t", "cum_ask_1t"))
    seen = set()
    for r in cal:
        if r["market_slug"] in seen:
            continue
        seen.add(r["market_slug"])
        nt = r.get("near_touch")
        if not nt:
            say("  %-46s  near_touch NOT MEASURABLE (one-sided or no tick)"
                % r["market_slug"])
            continue
        say("  %-46s %9s %9s %11s %11s"
            % (r["market_slug"],
               "%s@%s" % (nt["best_bid_qty"], nt["best_bid_px"]),
               "%s@%s" % (nt["best_ask_qty"], nt["best_ask_px"]),
               nt["cum_bid_qty_within_1t"], nt["cum_ask_qty_within_1t"]))
    say()
    say("  Notice what the near-touch metric shows that a whole-ladder number")
    say("  would have hidden: touch quantities of 0.01 and 0.05 shares sitting")
    say("  in front of thousands of shares one tick behind. A single displayed")
    say("  best price can be a dust order. That is a queue-competition fact and")
    say("  it is exactly what the retracted whole-ladder dollar measure could")
    say("  not express.")
    say()
    say("  NO EDGE IS INFERRED FROM ANY OF IT. These are structure readings.")
    say()


def section_control_and_throttle(disc):
    say("=== B2/THROTTLE. CONTROLS ===")
    dc = disc["detail_control"]
    say("DISCOVERY_FIELDS_MATCH_MARKET_DETAIL = %s" % dc["verdict"])
    say("  compared %d markets, %d identical, %d differing"
        % (dc["compared"], dc["identical"], dc["differing"]))
    say("  Discovery-sourced fields are trustworthy for this run, so reading")
    say("  tick, fee field, sides, state and type from /v1/events -- instead of")
    say("  Phase 2C's 150 one-at-a-time detail probes -- is justified by")
    say("  measurement rather than by convenience.")
    say()
    ev = disc.get("throttle_events") or []
    say("THROTTLE EVENTS = %d" % len(ev))
    for e in ev:
        say("  %s" % e)
    say()
    if any(e.get("event") == "429" for e in ev):
        say("  A 429 AT 0.5 RPS. This is the first one observed anywhere in")
        say("  Run 85: Phase 2A, 2B and 2C each recorded a 0.00%% 429 rate.")
        say("  The venue answered with Retry-After: 10, the pacer slept the")
        say("  full 10 s and widened its spacing from 2.0 s to 4.0 s, and the")
        say("  run completed with no further 429.")
        say()
        say("  0.5 RPS IS NOT UNCONDITIONALLY SAFE. The earlier zero-429 runs")
        say("  were evidence about those runs, not a property of the rate.")
        say("  RPS_LIMIT_NOT_ESTABLISHED stays locked, and nothing here")
        say("  argues for raising the rate -- it argues the opposite, and it")
        say("  argues that any long capture must carry this backoff.")
        say()
    say("VENUE_REQUESTS_SPENT = %d of 120 disclosed"
        % disc.get("venue_requests_spent", 0))
    say()


def section_coverage(disc):
    say("=== COVERAGE JUDGMENT ===")
    pages = disc["pages"]
    last = pages[-1]
    new_last = last.get("new_event_ids")
    say("pages walked        = %d (bound %d)" % (len(pages), 24))
    say("stop reason         = %s" % disc["walk_stop_reason"])
    say("new events on the LAST page = %s" % new_last)
    say("highest id reached  = %s" % last["event_ids_max"])
    say()
    say("  The walk stopped because it hit MY OWN disclosed page bound, not")
    say("  because the venue ran out of events: the last page still returned")
    say("  %s events, all new. There are more events past id %s and this run"
        % (last["events_returned"], last["event_ids_max"]))
    say("  does not know how many.")
    say()
    say("  GAME_LEVEL_DISCOVERY_COVERAGE = INSUFFICIENT")
    say()
    say("  That is a statement about COVERAGE, not about EXISTENCE. Existence")
    say("  is settled: game markets are there in quantity. What is not")
    say("  established is the total population, whether the game-level mass")
    say("  continues past the bound, or how it moves through the day.")
    say()


def main():
    d = Path(sys.argv[1])
    disc, cal = load(d)
    say("RUN 85 PHASE 2D -- ANALYSIS OF THE FROZEN DISCOVERY")
    say("archive : %s" % d.name)
    say("NOTHING IN THIS FILE CONTACTS THE VENUE.")
    say()
    section_pagination(disc)
    section_walk(disc)
    section_classification(disc)
    section_game_detail(disc)
    section_hypotheses(disc)
    section_control_and_throttle(disc)
    section_calibration(disc, cal)
    section_coverage(disc)

    say("=== H. PHASE 2D VERDICTS, ANALYST-JUDGED ===")
    g = disc["classification"].get("GAME_LEVEL", 0)
    say("GAME_LEVEL_MARKETS_FOUND             = YES")
    say("GAME_LEVEL_UNIQUE_EVENTS_FOUND       = %d"
        % len({x["native_event_id"] for x in disc["game_level"]}))
    say("GAME_LEVEL_MARKET_ROWS_FOUND         = %d (floor; see DEFECT 1)" % g)
    say("GAME_LEVEL_ACTIVE_BINARY_BOOKS_FOUND = %d calibrated with a two-sided "
        "book" % (disc.get("calibration") or {}).get("markets", 0))
    say("GAME_LEVEL_DISCOVERY_COVERAGE        = INSUFFICIENT")
    say("PHASE2C_FUTURES_ONLY_RESULT_EXPLAINED = YES")
    say("EVENT_FIRST_CENSUS_WORKING           = YES for the census, "
        "NO for the calibration pick (DEFECT 2)")
    say("NEAR_TOUCH_DEPTH_METRIC_READY        = YES")
    say("EXACT_5S_UNREACHABLE_UNDER_CURRENT_UNIFORM_ROUND_ROBIN = YES")
    say("EXACT_5S_UNREACHABLE_UNDER_SAFE_AGGREGATE_RATE         = NO")
    say("EXACT_5S_SCHEDULER_FEASIBLE_AT_SAFE_RATE               = YES")
    say("PASSIVE_REALIZABLE_EDGE              = NOT_IDENTIFIED")
    say("PMUS_FEES_RESOLVED                   = NO")
    say("READY_FOR_MULTI_DAY_PHASE2_CAPTURE   = NO")
    say()
    say("  READY is NO on three counts, all of them fixable and none of them")
    say("  the blocker Phase 2C reported:")
    say("    1 discovery coverage is bounded by my page limit, not exhausted")
    say("    2 the game allowlist undercounts by one whole structured type")
    say("    3 the calibration sample is an alphabetical slice of one sport")
    say()
    say("  The Phase 2C blocker -- 'the venue has no game markets to capture'")
    say("  -- is GONE. It was never true; it was an artifact of reading one")
    say("  unpaginated page.")


if __name__ == "__main__":
    main()

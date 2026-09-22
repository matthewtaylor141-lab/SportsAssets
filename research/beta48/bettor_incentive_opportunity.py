"""The bridge: frozen manifest terms + real ladders -> a reward share.

WHY THIS EXISTS NOW, BEFORE THE OBSERVATION RUNS. `bettor_incentive_score`
implements the venue's formula and is pinned against the venue's own
worked example. `bettor_incentive_manifest` holds the programme terms.
Neither reads a ladder. So a day of observation would have landed and
been followed by another round of tooling before anyone could say what
it was worth. This closes that gap first.

WHAT IT DOES. For each frozen market it builds a `Program` from the
CAPTURED terms -- not from a constant in a file -- walks a sequence of
ladder snapshots, and reports, for a hypothetical clip at a stated
price offset:

    qualifying uptime      how often Target Size was met, per side
    score share            our score over the side's total score
    created eligibility    how often the side qualified ONLY because
                           we were there -- the case where the reward
                           is a payment for making the programme work
    gross reward           the pool times the integrated share

NOTHING HERE PLACES AN ORDER, and the clip is explicitly hypothetical:
the quote is inserted into an observed book to ask what it WOULD have
scored. That is a counterfactual about a public ladder, not a fill
claim, and the output labels it.

THE HONEST LIMIT, carried into every report: inserting our size changes
the denominator but not other people's behaviour. Real competitors
would have responded. The figure is an upper reading of our share at
the observed depth, not a forecast of what we would earn.

Run:  python research/beta48/bettor_incentive_opportunity.py --self-test
      python research/beta48/bettor_incentive_opportunity.py --scenario
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "backend"))

import bettor_incentive_score as inc                        # noqa: E402
from sportsassets import bettor_incentive_manifest as man   # noqa: E402

VERSION = "BETTOR_INCENTIVE_OPPORTUNITY_V1"

MANIFEST = os.path.join(HERE, "acceptance", "incentive_manifest.json")
OUT = os.path.join(HERE, "acceptance", "incentive_opportunity.json")


def programs_from_manifest(path=MANIFEST, et_date=None):
    """A `Program` per frozen market, from the CAPTURED terms.

    The terms are read from the manifest rather than from constants,
    so a programme whose pool or discount factor changed cannot be
    scored against yesterday's numbers without the change showing up
    in the diff.
    """
    w = man.load(path)
    if not w.get("ok"):
        return {"ok": False, "why": w.get("why"), "detail": w.get("detail")}
    m = w["manifest"]
    f = man.freeze(m, et_date=et_date or m.get("et_date"))
    if not f.get("ok"):
        return {"ok": False, "why": f.get("why"), "freeze": f}

    keep = set(f["slugs"])
    progs, seen = {}, {}
    for row in m["programs"]:
        s = row.get("market_slug")
        if s not in keep or s in progs:
            continue
        progs[s] = inc.Program(
            market_slug=s,
            program_id=row.get("program_id"),
            period=row.get("period"),
            reward_pool=float(row.get("reward_pool") or 0.0),
            discount_factor=float(row.get("discount_factor") or 0.0),
            target_size=int(row.get("target_size") or 0))
        seen[s] = {"program_id": row.get("program_id"),
                   "event_start_time": row.get("event_start_time")}
    return {"ok": True, "programs": progs, "freeze": f, "terms": seen,
            "et_date": m.get("et_date")}


def measure(snapshots, prog, *, clip, offset_ticks=0, walk=None,
            allocation=None):
    """What a clip at `offset_ticks` from the best WOULD have scored.

    `snapshots` is a sequence of {"BID": [(px, qty)...], "ASK": [...]}.
    The quote price is derived from EACH snapshot's own best price, not
    fixed once, because a quote that sat at a stale price would score
    against a book that had moved away from it.
    """
    walk = walk or inc.WALK_WHOLE_LEVEL
    allocation = allocation or inc.ALLOC_POOLED
    acc = {"BID": [0.0, 0], "ASK": [0.0, 0]}
    created, off_walk, no_book = 0, 0, 0

    for snap in snapshots:
        for side in ("BID", "ASK"):
            levels = snap.get(side) or []
            if not levels:
                no_book += 1
                continue
            best = inc._by_level(levels, side)[0][0]
            px = (best - offset_ticks * prog.tick if side == "BID"
                  else best + offset_ticks * prog.tick)
            r = inc.snapshot_share(levels, side, prog, round(px, 6), clip,
                                   walk)
            if not r["qualifies"]:
                continue
            acc[side][1] += 1
            acc[side][0] += r["share"]
            created += 1 if r["created_eligibility"] else 0
            if r.get("our_score", 0.0) == 0.0:
                off_walk += 1

    units = acc["BID"][1] + acc["ASK"][1]
    shares = acc["BID"][0] + acc["ASK"][0]
    if allocation == inc.ALLOC_POOLED:
        reward = prog.reward_pool * (shares / units) if units else 0.0
    else:
        half = prog.reward_pool / 2.0
        reward = sum((half * (acc[s][0] / acc[s][1]) if acc[s][1] else 0.0)
                     for s in ("BID", "ASK"))
    n = len(snapshots)
    return {
        "market": prog.market_slug, "program_id": prog.program_id,
        "clip": clip, "offset_ticks": offset_ticks,
        "snapshots": n,
        "qualifying_uptime": round(units / (2 * n), 4) if n else 0.0,
        "bid_qualifying": acc["BID"][1], "ask_qualifying": acc["ASK"][1],
        "mean_share": round(shares / units, 6) if units else 0.0,
        "reward_gross_usd": round(reward, 6),
        "clears_min_payout": reward >= 1.0,
        "side_snapshots_we_created_eligibility": created,
        "side_snapshots_our_level_fell_outside_the_walk": off_walk,
        "side_snapshots_with_no_book": no_book,
        "walk": walk, "allocation": allocation,
        "counterfactual": "our clip is INSERTED into an observed book. "
                          "It changes the denominator but not other "
                          "people's behaviour, so this is an upper "
                          "reading of our share at the observed depth, "
                          "not a forecast of earnings.",
    }


# ── the tape scenario, LABELLED ──────────────────────────────────────

def tape_snapshots(slug, limit=None):
    """Real ladders out of the captured tape, as score-ready levels."""
    import bettor_tape as tape
    by, _ = tape.load_tape()
    by, _s = tape.attach_ladders(by)
    rows = by.get(slug) or []
    out = []
    for r in rows:
        lad = r.get("ladder") or {}
        bids, offers = lad.get("bids") or [], lad.get("offers") or []
        if not bids and not offers:
            continue
        out.append({"BID": [(float(p), float(q)) for p, q in bids],
                    "ASK": [(float(p), float(q)) for p, q in offers]})
        if limit and len(out) >= limit:
            break
    return out


def scenario():
    """Exercise the machinery on REAL ladders, labelled as a scenario.

    THE MARKETS IN THE TAPE CARRY NO PROGRAMME. Applying the culture
    programme's terms to a boxing book does not measure what that book
    would have paid -- there was no pool on it. What it DOES establish
    is that the pipeline runs end to end on real depth, which is the
    thing worth knowing before a day of observation lands.
    """
    got = programs_from_manifest()
    if not got.get("ok"):
        print("no usable manifest: %s" % got.get("why"))
        return 2
    prog0 = next(iter(got["programs"].values()))
    print("=" * 78)
    print("INCENTIVE OPPORTUNITY -- %s" % VERSION)
    print("=" * 78)
    print("programme terms, AS CAPTURED: %s  pool $%.0f  DF %.2f  target %d"
          % (prog0.program_id, prog0.reward_pool, prog0.discount_factor,
             prog0.target_size))
    print("frozen markets: %d  |  programmes %d  |  events %d"
          % (got["freeze"]["markets"], got["freeze"]["distinct_programs"],
             got["freeze"]["distinct_events"]))
    print()
    print("SCENARIO, AND IT IS LABELLED ONE. The ladders below are real,")
    print("from the captured tape. The markets they come from carry NO")
    print("incentive programme, so these are NOT measurements of what")
    print("they would have paid -- there was no pool on them. The point")
    print("is that the pipeline runs on real depth.")
    print()

    import bettor_tape as tape
    by, _ = tape.load_tape()
    rows = []
    print("%-34s %5s %6s %8s %9s %10s %7s" % (
        "ladder source (no programme)", "clip", "offset", "uptime",
        "mean_shr", "gross$", "min$ok"))
    print("-" * 84)
    for slug in sorted(by)[:3]:
        snaps = tape_snapshots(slug, limit=600)
        if not snaps:
            continue
        for clip in (100.0, 500.0):
            for off in (0, 1):
                prog = inc.Program(
                    market_slug=slug, program_id=prog0.program_id,
                    period=prog0.period, reward_pool=prog0.reward_pool,
                    discount_factor=prog0.discount_factor,
                    target_size=prog0.target_size)
                r = measure(snaps, prog, clip=clip, offset_ticks=off)
                r["status"] = "SCENARIO -- this market carries no programme"
                rows.append(r)
                print("%-34s %5.0f %6d %8.3f %9.4f %10.4f %7s" % (
                    slug[:34], clip, off, r["qualifying_uptime"],
                    r["mean_share"], r["reward_gross_usd"],
                    "yes" if r["clears_min_payout"] else "no"))

    with open(OUT, "w") as fh:
        json.dump({"version": VERSION,
                   "status": "SCENARIO on real ladders from markets that "
                             "carry NO incentive programme. Not a "
                             "measurement of their reward, which is zero.",
                   "terms_source": "captured manifest %s"
                                   % got.get("et_date"),
                   "programme": {"program_id": prog0.program_id,
                                 "reward_pool": prog0.reward_pool,
                                 "discount_factor": prog0.discount_factor,
                                 "target_size": prog0.target_size},
                   "frozen_markets": got["freeze"]["slugs"],
                   "independence_note": got["freeze"]["independence_note"],
                   "rows": rows}, fh, indent=2, default=str)
    print("\nwritten: %s" % OUT)
    return 0


def self_test():
    """Arithmetic the venue's own worked example pins, end to end."""
    got = programs_from_manifest()
    assert got["ok"], got
    assert got["freeze"]["markets"] >= 10
    prog = next(iter(got["programs"].values()))
    assert prog.target_size > 0 and prog.reward_pool > 0

    T = float(prog.target_size)
    df = prog.discount_factor

    # A side that cannot meet Target Size never qualifies, whatever we do.
    thin = [{"BID": [(0.50, 1.0)], "ASK": [(0.52, 1.0)]}]
    r = measure(thin, prog, clip=1.0)
    assert r["qualifying_uptime"] == 0.0, r
    assert r["reward_gross_usd"] == 0.0

    # EXACT SHARE, not a bound. One competitor contract at the touch and
    # our clip beside it: the level holds T+1, so the side qualifies and
    # our share is T/(T+1). Pinning the number rather than "> 0" is what
    # makes this catch an arithmetic change.
    book = [{"BID": [(0.50, 1.0)], "ASK": []}]
    r = measure(book, prog, clip=T, offset_ticks=0)
    assert r["bid_qualifying"] == 1 and r["ask_qualifying"] == 0, r
    # Compared at the reported precision: `mean_share` is rounded to
    # six places, so a tolerance tighter than that tests the rounding
    # rather than the arithmetic.
    assert r["mean_share"] == round(T / (T + 1.0), 6), r
    # The empty ASK is COUNTED, not silently dropped: with no book there
    # is no best price, so a price-offset rule is undefined on that side.
    assert r["side_snapshots_with_no_book"] == 1, r

    # E2, THE ONE THAT COSTS MONEY. A competitor already meets Target
    # Size at the touch, so the walk stops at their level and a quote
    # one tick back scores ZERO -- the side qualifies and we earn
    # nothing from it. Not merely "less than at the touch".
    full = [{"BID": [(0.50, T)], "ASK": []}]
    r0 = measure(full, prog, clip=100.0, offset_ticks=0)
    r1 = measure(full, prog, clip=100.0, offset_ticks=1)
    assert r0["mean_share"] == round(100.0 / (T + 100.0), 6), r0
    assert r1["mean_share"] == 0.0, r1
    assert r1["side_snapshots_our_level_fell_outside_the_walk"] == 1, r1
    assert r1["bid_qualifying"] == 1, r1      # qualified, and we scored 0

    # The discount is applied per tick when our level IS inside the walk.
    room = [{"BID": [(0.50, 1.0)], "ASK": []}]
    r = measure(room, prog, clip=T, offset_ticks=1)
    ours = df ** 1 * T
    assert r["mean_share"] == round(ours / (ours + 1.0), 6), (r, ours)

    # Creating eligibility is COUNTED, not silently folded into share.
    near = [{"BID": [(0.50, T - 1.0)], "ASK": []}]
    r = measure(near, prog, clip=10.0)
    assert r["side_snapshots_we_created_eligibility"] == 1, r
    print("self-test OK -- terms from the captured manifest, %d markets, "
          "pool $%.0f, DF %.2f, target %d"
          % (got["freeze"]["markets"], prog.reward_pool,
             prog.discount_factor, prog.target_size))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--scenario", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.scenario:
        return scenario()
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

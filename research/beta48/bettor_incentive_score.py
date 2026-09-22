"""The liquidity-reward calculation, implemented rather than approximated.

WHAT THIS REPLACES. I reported reward share as `100 / (100 + C)` with C
a raw competitor size. That expression is a special case that holds only
when: our order is at the best price, every competitor is also at the
best price, the side already meets Target Size without us, both sides
qualify equally often, and the pool is split evenly. Change any one of
those and it is wrong.

THE DOCUMENTED RULES (docs.polymarket.us/incentives/liquidity, retrieved
2026-09-22T18:14Z), each implemented below:

  R1  Score = discountFactor ^ (ticks from best price) x OrderSize
  R2  Every second a random snapshot of the book is taken.
  R3  Target Size is the minimum AGGREGATE raw size on a side. The
      exchange walks from the best price OUTWARD accumulating orders
      until Target Size is reached. All orders within that range score;
      orders beyond it do not.
  R4  Each snapshot is normalised: the bid side and ask side are EACH
      independently normalised to 1.0, PROVIDED Target Size is met on
      that side.
  R5  Each side is scored independently; the spread between our bid and
      our offer does not matter.
  R6  There is no per-person cap.

WHAT THE DOCUMENTATION DOES NOT SAY, and is therefore a declared
assumption here rather than a fact:

  U1  HOW THE POOL IS SPLIT BETWEEN THE TWO SIDES. R4 says each side is
      normalised to 1.0 per snapshot; it does not say whether the pool
      is divided 50/50 between sides, or shared across all qualifying
      side-snapshots in proportion. Both are implemented
      (`ALLOC_POOLED` and `ALLOC_PER_SIDE`) and both are reported. They
      diverge exactly when one side qualifies more often than the other.
  U2  HOW THE POOL IS SPLIT BETWEEN OVERLAPPING PERIODS on one market-
      day. Not modelled; periods are computed separately and never added
      without saying so.
  U3  WHETHER A LEVEL IS INCLUDED WHOLE when the walk reaches Target
      Size part-way through it. `WALK_WHOLE_LEVEL` (default, and what
      the prose implies: the walk accumulates orders, and the worked
      example treats the best price as one block) versus `WALK_PRO_RATA`
      (the level is included only up to the remaining Target Size).
      Both are implemented.

OUR OWN ORDER CHANGES THE BOOK, and ignoring that is its own error:

  E1  It can make a side QUALIFY that otherwise would not. If the side
      holds less than Target Size without us and at least Target Size
      with us, we do not merely take a share -- we create the qualifying
      condition, and in a thin market we may be nearly the whole score.
  E2  It can PUSH OTHER ORDERS OUT of the walk. Adding size at the best
      price consumes Target Size earlier, so orders at worse prices that
      were inside the walk can fall outside it, which RAISES our share
      beyond a naive 1/(1+C).

  Both effects are computed by scoring the book twice -- without us and
  with us inserted -- never by adjusting a share after the fact.

A SNAPSHOT IS NOT A DAY. `period_reward()` integrates over the snapshots
of a period and divides by the number of qualifying side-snapshots. A
single snapshot's share is never multiplied by a pool.

Run:  python research/beta48/bettor_incentive_score.py
"""
from __future__ import annotations

import dataclasses

ALLOC_POOLED = "POOLED_ACROSS_SIDE_SNAPSHOTS"
ALLOC_PER_SIDE = "HALF_THE_POOL_TO_EACH_SIDE"
WALK_WHOLE_LEVEL = "WHOLE_LEVEL"
WALK_PRO_RATA = "PRO_RATA"


@dataclasses.dataclass(frozen=True)
class Program:
    """Parameters as the venue publishes them, not as we guess them."""
    market_slug: str
    program_id: str
    period: str
    reward_pool: float
    discount_factor: float
    target_size: int
    tick: float = 0.01


def _by_level(levels, side):
    """Aggregate to PRICE LEVELS, best first. BID descends, ASK ascends.

    THE WALK IS BY LEVEL, NOT BY ORDER, and this is the step whose
    absence produced the very error this module exists to correct. With
    raw orders, two entries at the same price are walked one after the
    other, so a competitor listed first can exhaust Target Size and
    exclude US AT OUR OWN PRICE -- which is precisely the
    same-level-exclusion mistake. Aggregating first makes the level
    atomic, so everyone at the best price is inside the walk together.
    """
    agg = {}
    for px, qty in levels:
        agg[round(float(px), 6)] = agg.get(round(float(px), 6), 0.0) + float(qty)
    return sorted(agg.items(), key=lambda pq: -pq[0] if side == "BID" else pq[0])


def side_scores(levels, side, prog, walk=WALK_WHOLE_LEVEL):
    """Return (qualifies, total_score, scored_levels).

    Implements R3 then R1: the walk decides WHICH PRICE LEVELS score,
    using RAW size, and only then is the discount applied.
    """
    ordered = _by_level(levels, side)
    if not ordered:
        return False, 0.0, []
    if sum(q for _, q in ordered) < prog.target_size:
        return False, 0.0, []          # R4: the side does not qualify

    best = ordered[0][0]
    acc = 0.0
    scored = []
    for px, qty in ordered:
        if acc >= prog.target_size:
            break                       # R3: this level is beyond the walk
        take = min(qty, prog.target_size - acc) if walk == WALK_PRO_RATA else qty
        acc += qty
        ticks = round(abs(px - best) / prog.tick)
        scored.append((px, take, prog.discount_factor ** ticks * take))
    return True, sum(s for _, _, s in scored), scored


def snapshot_share(levels, side, prog, our_price, our_size,
                   walk=WALK_WHOLE_LEVEL):
    """Our share of one side's score in ONE snapshot, with E1 and E2.

    The book is scored twice: as observed, and with our order inserted.
    `created_eligibility` records E1 -- that the side qualified only
    because we were there.
    """
    q_before, _, _ = side_scores(levels, side, prog, walk)
    with_us = list(levels) + [(our_price, float(our_size))]
    q_after, total_after, scored_after = side_scores(with_us, side, prog,
                                                     walk)
    if not q_after:
        return {"qualifies": False, "share": 0.0,
                "created_eligibility": False}

    best = _by_level(with_us, side)[0][0]
    ticks = round(abs(our_price - best) / prog.tick)
    our_score = prog.discount_factor ** ticks * our_size
    # E2: if our price fell outside the walk, our score is zero even
    # though the side qualifies.
    lvl = [(px, take) for px, take, _ in scored_after
           if abs(px - our_price) < 1e-9]
    if not lvl:
        our_score = 0.0                # E2: our level fell outside the walk
    elif walk == WALK_PRO_RATA:
        # the level was only partly inside; we are counted pro rata
        level_total = dict(_by_level(with_us, side))[round(our_price, 6)]
        our_score *= (lvl[0][1] / level_total) if level_total else 0.0
    return {"qualifies": True,
            "share": (our_score / total_after) if total_after else 0.0,
            "created_eligibility": (not q_before) and q_after,
            "our_score": our_score, "total_score": total_after}


def period_reward(snapshots, prog, our_quotes, walk=WALK_WHOLE_LEVEL,
                  allocation=ALLOC_POOLED):
    """Integrate over a period's snapshots. A snapshot is NOT a day.

    `snapshots` is a sequence of {"BID": levels, "ASK": levels}.
    `our_quotes` is {"BID": (price, size) or None, "ASK": ...}.

    ALLOC_POOLED    the pool is shared across every qualifying
                    side-snapshot in the period
    ALLOC_PER_SIDE  half the pool is attached to each side, and a side
                    that never qualifies forfeits its half
    """
    acc = {"BID": [0.0, 0], "ASK": [0.0, 0]}   # [sum of shares, n qualifying]
    created = 0
    for snap in snapshots:
        for side in ("BID", "ASK"):
            q = our_quotes.get(side)
            levels = snap.get(side) or []
            if q is None:
                ok, _, _ = side_scores(levels, side, prog, walk)
                if ok:
                    acc[side][1] += 1          # qualifies, we simply score 0
                continue
            r = snapshot_share(levels, side, prog, q[0], q[1], walk)
            if r["qualifies"]:
                acc[side][0] += r["share"]
                acc[side][1] += 1
                created += 1 if r["created_eligibility"] else 0

    units = acc["BID"][1] + acc["ASK"][1]
    shares = acc["BID"][0] + acc["ASK"][0]
    if allocation == ALLOC_POOLED:
        reward = prog.reward_pool * (shares / units) if units else 0.0
    else:
        half = prog.reward_pool / 2.0
        reward = 0.0
        for side in ("BID", "ASK"):
            s, n = acc[side]
            reward += half * (s / n) if n else 0.0
    return {"reward_gross": round(reward, 6),
            "qualifying_side_snapshots": units,
            "snapshots_seen": len(snapshots),
            "qualifying_uptime": round(units / (2 * len(snapshots)), 4)
            if snapshots else 0.0,
            "snapshots_we_created_eligibility": created,
            "allocation": allocation, "walk": walk,
            "bid_qualifying": acc["BID"][1], "ask_qualifying": acc["ASK"][1]}


def _demo():
    prog = Program("ccpc-demo", "culture_low_20260921", "daily_event",
                   reward_pool=50.0, discount_factor=0.25, target_size=500)
    print("=" * 72)
    print("WHY 100/(100+C) IS NOT THE CALCULATION")
    print("=" * 72)
    print("program: pool $%.0f  DF %.2f  targetSize %d"
          % (prog.reward_pool, prog.discount_factor, prog.target_size))
    print()
    cases = [
        ("competitor 400 AT our price, side already qualifies",
         [(0.50, 400.0), (0.49, 200.0)]),
        ("competitor 400 one tick BEHIND us (discounted to 25%)",
         [(0.49, 400.0), (0.48, 200.0)]),
        ("competitor 600 one tick AHEAD of us (we fall outside the walk)",
         [(0.51, 600.0)]),
        ("thin side, 450 only -- does NOT qualify until WE arrive",
         [(0.50, 450.0)]),
    ]
    print("%-58s %10s %9s" % ("book (BID side)",
                                      "100/(100+raw)", "actual"))
    print("-" * 78)
    for label, levels in cases:
        r = snapshot_share(levels, "BID", prog, 0.50, 100)
        c_raw = sum(q for _, q in levels)
        naive = 100.0 / (100.0 + c_raw)
        print("%-58s %9.3f  %8.3f%s" % (
            label, naive, r["share"],
            "  <- we created eligibility" if r["created_eligibility"] else ""))
    print()
    print("The naive form is right only in the first row, and only")
    print("because that row is the special case it assumes.")

    print()
    print("=" * 72)
    print("A SNAPSHOT IS NOT A DAY -- integrating, with uptime")
    print("=" * 72)
    # BID deep all day; ASK deep only part of the day. The two
    # allocation assumptions must then disagree.
    # BID deep all day against a 600 competitor; ASK deep for only part
    # of the day and against a 900 competitor. Different shares on the
    # two sides is exactly when the two allocation assumptions diverge.
    day = ([{"BID": [(0.50, 600.0)], "ASK": [(0.52, 900.0)]}] * 20000
           + [{"BID": [(0.50, 600.0)], "ASK": [(0.52, 50.0)]}] * 66400)
    quotes = {"BID": (0.50, 100), "ASK": (0.52, 100)}
    for alloc in (ALLOC_POOLED, ALLOC_PER_SIDE):
        out = period_reward(day, prog, quotes, allocation=alloc)
        print("  %-28s reward $%6.2f   uptime %.1f%%   units %d"
              % (alloc, out["reward_gross"],
                 100 * out["qualifying_uptime"],
                 out["qualifying_side_snapshots"]))
    print()
    print("  86,400 snapshots; the side qualifies in under half of them,")
    print("  and the two ALLOCATION assumptions give different answers.")
    print("  Which one the venue uses is NOT DOCUMENTED (U1).")


if __name__ == "__main__":
    _demo()

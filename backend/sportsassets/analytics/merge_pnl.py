"""Whale P&L with MERGES counted as the exits they are.

Every whale number this desk has produced grades at RESOLUTION. That is
blind to the way these accounts actually take profit, and the blindness
is total: SIDES reports 860,669 buys and zero sells for swisstony,
EXITS reports round_trips 0 and exit_rate 0.0 for every copied whale,
and CUTCHECK has to print "NO EXIT DATA" because the cashout basis does
not exist. Three instruments, one blind spot, and three whales cut on
the strength of it.

The owner found the mechanism in Polymarket's global microdata: they
close by buying the COMPLEMENTARY leg. The venue holds one signed net
position per market, so N shares of the other side retire N shares of
the holding and return $N in cash — YES + NO is worth exactly $1 by
construction. That is a round trip, it is in our trades table already,
and nothing has ever read it as one.

THE REPLAY. Walk each (whale, condition) in (ts, id) order carrying
both legs' share balances and cost bases. On a BUY of leg L for `size`
shares at `price`:

    m     = min(size, balance_of_other_leg)      -> EXIT (merge)
    entry = size - m                             -> genuine new position

A merge of m shares realises

    m * (1 - avg_cost_of_held_leg - price_paid_now)

because the pair returns $1 per share against what both legs cost. It
burns m from BOTH balances. A plain SELL is handled as the ordinary
round trip it already is.

WHAT THIS IS NOT. It is not a claim about our own order semantics. The
question of whether OUR BUY_SHORT is booked as a sell is separate,
unsettled, and gated behind LIVE_SHORT_COST_MODEL. This module reads
the WHALE's fills, where the complement buy is visible directly and
needs no venue model at all.
"""

from __future__ import annotations

import math
from typing import Any

# Below this many shares a leg is treated as flat — venue dust, not a
# position, and it otherwise leaves permanent 1e-9 balances that make
# every later buy look like a partial merge.
DUST = 1e-6


# Two-sided 95%.
_Z95 = 1.959963985


def _lot_interval(o: dict) -> dict:
    """ROI on dollar deployed, with a 95% interval, from the running
    sums. Same estimator the copy sleeve is judged by, so the whale's
    edge and ours are on one scale.

        R  = sum(p) / sum(s)
        SE = sqrt( n/(n-1) * (sum(p^2) - 2R*sum(ps) + R^2*sum(s^2)) )
             / sum(s)

    A NEGATIVE variance is possible here from floating-point
    cancellation on large books and is clamped to zero rather than
    handed to sqrt — an exception in a P&L report is worse than a
    degenerate interval, and a zero-width one is visibly degenerate.
    """
    n, ts, tp = o.get("lots", 0), o.get("lot_s", 0.0), o.get("lot_p", 0.0)
    # lots and deployed are facts whether or not an interval can be
    # drawn from them; a response whose SHAPE changes with the verdict
    # makes every reader special-case the bad news.
    base = {"edge_lots": n, "edge_deployed": round(ts, 2)}
    if n < 2 or ts <= 0:
        return {**base, "edge_roi": (round(tp / ts, 6) if ts > 0 else None),
                "edge_se": None, "edge_ci95": None,
                "edge_verdict": "INSUFFICIENT — fewer than two closed "
                                "lots on this book"}
    r = tp / ts
    ss = (o["lot_pp"] - 2.0 * r * o["lot_ps"] + r * r * o["lot_ss"])
    se = math.sqrt(max(0.0, n / (n - 1) * ss)) / ts
    lo, hi = r - _Z95 * se, r + _Z95 * se
    if lo > 0:
        v = (f"PROFITABLE at 95% — {r:+.2%} on dollar deployed, "
             f"interval [{lo:+.2%}, {hi:+.2%}] over {n:,} closed lots")
    elif hi < 0:
        v = (f"LOSING at 95% — {r:+.2%} on dollar deployed, interval "
             f"[{lo:+.2%}, {hi:+.2%}] over {n:,} closed lots")
    else:
        v = (f"NOT DEMONSTRATED — {r:+.2%} on dollar deployed but the "
             f"interval [{lo:+.2%}, {hi:+.2%}] contains zero over "
             f"{n:,} closed lots")
    return {**base, "edge_roi": round(r, 6), "edge_se": round(se, 6),
            "edge_ci95": [round(lo, 6), round(hi, 6)],
            "edge_verdict": v}


def _replay_stepper(payouts: dict[str, list[float]] | None = None):
    """Classify one whale's fills. Pure — no database, so it is testable.

    `fills` must be ordered (condition_id, ts, id) and carry:
        condition_id, outcome_index, side, size, price

    THE COUNTERFACTUAL (owner question 2026-08-25). `payouts` maps
    condition_id -> the venue's resolved payout per outcome index
    (markets.resolved_prices, e.g. [1, 0]). Given it, this also
    measures what the SAME closed shares would have returned if the
    whale had never exited and simply held to resolution.

    That comparison is the owner's thesis stated as arithmetic. Every
    whale number this desk has published grades at resolution, which
    is a world in which the exit never happens; the whales' real
    returns come from the exits. Per closed lot:

        actual          q * (exit_price   - avg_cost)
        held to settle  q * (payout       - avg_cost)
        EXIT VALUE      q * (exit_price   - payout)

    and for a merge the exit price of the held leg is (1 - complement
    price), because the pair returns exactly $1:

        EXIT VALUE      m * (1 - complement_price - payout_held)

    The avg_cost cancels, which is what makes this clean: the exit
    value does not depend on what he paid, only on where he got out
    versus where it finished.

    ONLY CLOSED SHARES ARE COMPARED. Positions still open at the end of
    the walk never exited in either world, so they cancel and are
    excluded rather than graded — including them would measure his open
    book, not his exits.

    Conditions with no known payout are EXCLUDED AND COUNTED, never
    treated as a zero payout. A missing resolution silently read as
    "it lost" would manufacture exit value out of nothing, which is the
    exact shape of the number this is meant to check.
    """
    per_cond: dict[str, list[list[float]]] = {}
    pay = payouts or {}
    out = {
        "n_fills": 0, "n_entries": 0, "n_merges": 0, "n_sells": 0,
        "entry_notional": 0.0, "merge_shares": 0.0,
        "realized_merge_pnl": 0.0, "realized_sell_pnl": 0.0,
        "open_shares": 0.0, "open_cost": 0.0, "rows": [],
        # RATIO-ESTIMATOR ACCUMULATORS, so each whale's edge gets a
        # confidence interval instead of a point estimate.
        #
        # "rn1 is profitable" has been asserted from a total all day.
        # +$231,495 on $24.5M of entries is +0.94% on dollar deployed,
        # and whether 94 basis points is real or noise depends entirely
        # on the dispersion behind it — which nothing was carrying.
        #
        # Accumulated in O(1) rather than by keeping the lots, because
        # these books run to 90,000 merges each. The identity that
        # makes it possible:
        #
        #   sum((p - R*s)^2) = sum(p^2) - 2R*sum(p*s) + R^2*sum(s^2)
        #
        # so five running sums carry the whole interval.
        "lots": 0, "lot_s": 0.0, "lot_p": 0.0,
        "lot_ss": 0.0, "lot_pp": 0.0, "lot_ps": 0.0,
        # counterfactual, populated only when `payouts` is supplied
        # Positions that ended at RESOLUTION rather than at a fill.
        "settled_lots": 0, "settled_shares": 0.0,
        "ungraded_open_shares": 0.0, "ungraded_open_cost": 0.0,
        "cf_closed_shares": 0.0, "cf_graded_shares": 0.0,
        "cf_ungraded_shares": 0.0,
        "cf_actual_on_graded": 0.0, "cf_hold_on_graded": 0.0,
    }

    def _payout(cid: str, leg: int) -> float | None:
        """The venue's payout for one leg, or None if unresolved."""
        v = pay.get(cid)
        if not isinstance(v, (list, tuple)) or leg >= len(v):
            return None
        try:
            return float(v[leg])
        except (TypeError, ValueError):
            return None

    def _lot(stake: float, pnl: float) -> None:
        """One CLOSED lot: what he had at risk, and what it returned."""
        if stake <= 0:
            return
        out["lots"] += 1
        out["lot_s"] += stake
        out["lot_p"] += pnl
        out["lot_ss"] += stake * stake
        out["lot_pp"] += pnl * pnl
        out["lot_ps"] += pnl * stake

    def _grade(cid: str, leg: int, q: float, exit_px: float,
               avg: float) -> None:
        """Book one closed lot into the actual-vs-held comparison."""
        out["cf_closed_shares"] += q
        po = _payout(cid, leg)
        if po is None:
            out["cf_ungraded_shares"] += q
            return
        out["cf_graded_shares"] += q
        out["cf_actual_on_graded"] += q * (exit_px - avg)
        out["cf_hold_on_graded"] += q * (po - avg)
    def step(f) -> None:
        """One fill. The loop body, unchanged."""
        cid = str(f.get("condition_id") or "")
        try:
            idx = int(f.get("outcome_index") if f.get(
                "outcome_index") is not None else -1)
            size = float(f.get("size") or 0)
            price = float(f.get("price") or 0)
        except (TypeError, ValueError):
            return
        if not cid or idx not in (0, 1) or size <= 0:
            return
        out["n_fills"] += 1
        # [shares_leg0, shares_leg1, cost_leg0, cost_leg1]
        st = per_cond.setdefault(cid, [[0.0, 0.0], [0.0, 0.0]])
        bal, cost = st[0], st[1]
        other = 1 - idx
        if (f.get("side") or "BUY").upper() == "SELL":
            q = min(size, bal[idx])
            if q > DUST:
                avg = cost[idx] / bal[idx] if bal[idx] > DUST else 0.0
                out["realized_sell_pnl"] += q * (price - avg)
                _lot(q * avg, q * (price - avg))
                _grade(cid, idx, q, price, avg)
                bal[idx] -= q
                cost[idx] -= q * avg
                out["n_sells"] += 1
            return
        # A BUY. The part that meets an opposing balance is an EXIT.
        m = min(size, bal[other])
        if m > DUST:
            avg_other = (cost[other] / bal[other]
                         if bal[other] > DUST else 0.0)
            pnl = m * (1.0 - avg_other - price)
            out["realized_merge_pnl"] += pnl
            _lot(m * avg_other, pnl)
            # The held leg's effective exit price is (1 - price paid for
            # the complement): the pair returns $1, so buying the other
            # side at `price` is selling this one at 1 - price.
            _grade(cid, other, m, 1.0 - price, avg_other)
            out["merge_shares"] += m
            out["n_merges"] += 1
            bal[other] -= m
            cost[other] -= m * avg_other
            out["rows"].append({
                "condition_id": cid, "merged_shares": round(m, 4),
                "held_avg": round(avg_other, 4),
                "complement_price": round(price, 4),
                "pnl": round(pnl, 2),
            })
        entry = size - m
        if entry > DUST:
            bal[idx] += entry
            cost[idx] += entry * price
            out["entry_notional"] += entry * price
            out["n_entries"] += 1
    def finish() -> dict:
        """Summarise. Called once, after the last fill."""
        # SETTLED POSITIONS ARE CLOSED LOTS TOO (2026-08-26, round 3).
        #
        # _lot was booked only when a FILL closed shares — a sell or a
        # complement merge. On this venue a position that simply
        # RESOLVES produces no fill at all, so it never entered a lot;
        # it accumulated into open_shares/open_cost, which nothing read.
        #
        # edge_roi therefore graded each whale ONLY on the positions he
        # CHOSE TO CLOSE, and stamped a 95% verdict on that
        # self-selected subset. Reproduced on a synthetic book: 100
        # markets, 1000 shares at 0.50, 30 closed by buying the
        # complement at 0.30 and 70 held to a zero payout. True result
        # -$29,000 on $50,000 = -58%. The estimator returned
        # "PROFITABLE at 95% - +40.00%".
        #
        # That is survivorship bias of the plainest kind — grading a
        # trader on the trades he decided to close — and it is the
        # estimator behind the published +16.26% / +3.69% / +2.11%,
        # behind the HomeRunHazard cut, and behind the sample-size
        # target on /api/admin/proof. The payouts needed to close these
        # balances were already fetched, for the exit counterfactual,
        # and simply were not applied to them.
        #
        # A resolved-but-never-closed position is a CLOSED lot at its
        # payout. An UNRESOLVED one is genuinely still open and is left
        # out and reported, because guessing its outcome would be
        # inventing the number this exists to check.
        for cid, st in per_cond.items():
            bal, cost = st[0], st[1]
            for leg in (0, 1):
                q = bal[leg]
                if q <= DUST:
                    continue
                po = _payout(cid, leg)
                if po is None:
                    out["ungraded_open_shares"] += q
                    out["ungraded_open_cost"] += cost[leg]
                    continue
                # settled: stake is what it cost, return is the payout
                out["settled_lots"] += 1
                out["settled_shares"] += q
                _lot(cost[leg], q * po - cost[leg])
        for st in per_cond.values():
            out["open_shares"] += st[0][0] + st[0][1]
            out["open_cost"] += st[1][0] + st[1][1]
            # HOW MUCH OF HIS BOOK THE EDGE ACTUALLY COVERS. Without this
        # a reader cannot tell a whole-book number from a subset one,
        # which is exactly how the subset number went unquestioned.
        _ung = out["ungraded_open_cost"]
        out["edge_coverage"] = (
            round(out["lot_s"] / (out["lot_s"] + _ung), 4)
            if (out["lot_s"] + _ung) > 0 else None)
        if _ung > 0:
            out["edge_coverage_note"] = (
                f"${_ung:,.0f} of cost sits in positions that are still "
                f"open OR whose market has no recorded payout. They are "
                f"EXCLUDED, not assumed — but the edge below describes "
                f"the rest of the book, not all of it")
        for k in ("ungraded_open_shares", "ungraded_open_cost",
                  "settled_shares"):
            out[k] = round(out[k], 2)
        for k in ("entry_notional", "merge_shares", "realized_merge_pnl",
                  "realized_sell_pnl", "open_shares", "open_cost",
                  "cf_closed_shares", "cf_graded_shares",
                  "cf_ungraded_shares",
                  "cf_actual_on_graded", "cf_hold_on_graded"):
            out[k] = round(out[k], 2)
        # EXIT VALUE: what the exits themselves were worth, on the shares
        # where both worlds can be priced. Positive means exiting beat
        # holding to resolution.
        out["exit_value"] = round(
            out["cf_actual_on_graded"] - out["cf_hold_on_graded"], 2)
        # THE INTERVAL ON HIS OWN EDGE.
        #
        # Same ratio estimator the copy sleeve is judged by, so "is this
        # whale profitable" and "are we profitable" are answered on one
        # scale and can be compared directly.
        out.update(_lot_interval(out))
        out["cf_coverage"] = (
            round(out["cf_graded_shares"] / out["cf_closed_shares"], 4)
            if out["cf_closed_shares"] > 0 else None)
        if payouts is not None and out["cf_ungraded_shares"] > 0:
            out["cf_note"] = (
                f"{out['cf_ungraded_shares']:.0f} of "
                f"{out['cf_closed_shares']:.0f} closed shares have no known "
                f"payout and are EXCLUDED from the comparison, not counted "
                f"as losses")
        out["realized_total"] = round(
            out["realized_merge_pnl"] + out["realized_sell_pnl"], 2)
        out["roi_on_entries"] = (
            round(out["realized_total"] / out["entry_notional"], 4)
            if out["entry_notional"] > 0 else None)
        out["rows"] = sorted(out["rows"], key=lambda r: r["pnl"])[:5] + \
            sorted(out["rows"], key=lambda r: -r["pnl"])[:5]
        return out

    return step, finish



def replay(fills, payouts: dict[str, list[float]] | None = None) -> dict:
    """Classify one whale's fills. Pure — no database, so it is testable.

    See _replay_stepper for the arithmetic and the counterfactual.
    """
    step, finish = _replay_stepper(payouts)
    for f in fills:
        step(f)
    return finish()


async def replay_stream(fills, payouts=None) -> dict:
    """replay() over an ASYNC iterable, holding ONE ROW at a time.

    replay() takes a list because a test hands it one. Production must
    not build that list: swisstony alone has 283,748 fills, and seven
    whales of dicts is several hundred megabytes on a ~512MB container.

    My first "streaming" version replaced pool.fetch with a server-side
    cursor and then appended every row to a list anyway — the same peak
    memory with more machinery — and the API went on dying. The probe
    read rss_mb 1133.8 and EVERY admin endpoint returned 502: PROOF,
    MERGE, SHORT-TRUTH, MEMCENSUS, UNMAPPED. The instruments meant to
    prove profitability could not answer because of the query behind
    them.

    Both forms drive the SAME stepper. Two implementations of one
    replay is how they drift, and this arithmetic is what the entire
    roster is graded on.
    """
    step, finish = _replay_stepper(payouts)
    async for f in fills:
        step(f)
    return finish()


async def whale_merge_pnl(pool: Any, whales: list[str],
                          since: str | None = None,
                          max_fills: int = 600000) -> dict:
    """Run the replay for each whale off the trades ledger.

    BOUNDED. The first probe of this endpoint came back "unavailable":
    seven whales, some with 860,000 fills, each needing an ordered walk
    the existing indexes do not serve. Migration 029 adds
    (whale_id, condition_id, ts, id); this cap is the belt to that
    braces, so a whale with an unexpected ledger cannot hang the probe.

    The cap is REPORTED, never silent — a truncated replay that reads
    as a complete one is a wrong P&L presented as a right one.
    """
    # asyncpg infers the parameter type from the CAST, so `$2::date`
    # expects a datetime.date and rejects a str with an opaque 500 —
    # which is exactly what the first two probes of this endpoint got.
    # api_true_edge_cashout already does this conversion (app.py:3578);
    # I wrote a new query instead of following the idiom next to it.
    import datetime as _dt
    import json as _json

    # THE WINDOW WAS MISBOOKING EVERY PRE-WINDOW POSITION AS AN ENTRY
    # (2026-08-25, adversarial review).
    #
    # The replay reconstructs holdings ONLY from fills inside the
    # window, seeding every balance at zero. So a complement buy that
    # retires a position opened before `since` finds bal[other] == 0,
    # is classified as a fresh ENTRY rather than a MERGE, and:
    #
    #   * its realised P&L vanishes from realized_merge_pnl
    #   * its cost inflates entry_notional — the ROI DENOMINATOR
    #   * the phantom position inflates open_shares
    #
    # Reproduced: a leg bought at 0.40 before the window and closed at
    # 0.30 inside it books as 0 merges, $0 realised and $30 of new
    # entries, against the truth of 1 merge and +$30 realised. Both
    # errors push measured ROI toward zero, on the numbers the ROSTER
    # is graded with.
    #
    # `since` now defaults to NONE — the whole book — which is the only
    # window in which the balances are actually right. The replay
    # streams through a cursor, so a full book is a single forward pass
    # rather than a memory problem. A caller that still passes a date
    # gets it, and gets told what it costs.
    since_d = None
    if since is not None:
        since_d = (since if isinstance(since, _dt.date)
                   else _dt.datetime.fromisoformat(str(since)).date())
    res: dict[str, Any] = {}
    for w in whales:
        # PAYOUTS FIRST, BY SUBQUERY. Collecting the condition ids
        # from the fills was the other reason a full pass had to be
        # materialised; the database can answer it without us holding
        # a single row.
        payouts: dict[str, list[float]] = {}
        cf_error: str | None = None
        _sub = ("SELECT DISTINCT t.condition_id FROM trades t "
                "JOIN whales wh ON wh.id = t.whale_id "
                "WHERE lower(wh.username) = $1 "
                "  AND t.condition_id IS NOT NULL")
        try:
            if since_d is None:
                prows = await pool.fetch(
                    "SELECT m.condition_id, m.resolved_prices FROM markets m "
                    " WHERE COALESCE(m.resolved, false) = true "
                    "   AND m.resolved_prices IS NOT NULL "
                    f"   AND m.condition_id IN ({_sub})", w.lower())
            else:
                prows = await pool.fetch(
                    "SELECT m.condition_id, m.resolved_prices FROM markets m "
                    " WHERE COALESCE(m.resolved, false) = true "
                    "   AND m.resolved_prices IS NOT NULL "
                    f"   AND m.condition_id IN ({_sub} AND t.ts >= $2)",
                    w.lower(), since_d)
            for pr in prows:
                v = pr["resolved_prices"]
                if isinstance(v, str):
                    try:
                        v = _json.loads(v)
                    except ValueError:
                        continue
                if isinstance(v, (list, tuple)):
                    payouts[str(pr["condition_id"])] = list(v)
        except Exception as exc:  # noqa: BLE001
            # No counterfactual rather than a WRONG one.
            payouts = {}
            cf_error = f"payout lookup failed: {type(exc).__name__}"

        # ACTUALLY STREAMED THIS TIME.
        #
        # The previous version used a server-side cursor and then
        # appended every row to a list — the same peak memory with more
        # machinery. The probe read rss_mb 1133.8 and every admin
        # endpoint returned 502.
        #
        # replay_stream walks this generator and holds one batch.
        _seen: set[str] = set()
        _n = [0]

        async def _rows():
            async with pool.acquire() as conn:
                async with conn.transaction():
                    if since_d is None:
                        cur = await conn.cursor(
                            "SELECT t.condition_id, t.outcome_index, t.side, "
                            "       t.size::float8 AS size, "
                            "       t.price::float8 AS price "
                            "  FROM trades t JOIN whales wh "
                            "    ON wh.id = t.whale_id "
                            " WHERE lower(wh.username) = $1 "
                            "   AND t.condition_id IS NOT NULL "
                            "   AND t.outcome_index IS NOT NULL "
                            " ORDER BY t.condition_id, t.ts, t.id",
                            w.lower())
                    else:
                        cur = await conn.cursor(
                            "SELECT t.condition_id, t.outcome_index, t.side, "
                            "       t.size::float8 AS size, "
                            "       t.price::float8 AS price "
                            "  FROM trades t JOIN whales wh "
                            "    ON wh.id = t.whale_id "
                            " WHERE lower(wh.username) = $1 "
                            "   AND t.ts >= $2 "
                            "   AND t.condition_id IS NOT NULL "
                            "   AND t.outcome_index IS NOT NULL "
                            " ORDER BY t.condition_id, t.ts, t.id",
                            w.lower(), since_d)
                    while _n[0] < max_fills:
                        batch = await cur.fetch(min(5000,
                                                    max_fills - _n[0]))
                        if not batch:
                            break
                        for r in batch:
                            _n[0] += 1
                            if r["condition_id"]:
                                _seen.add(str(r["condition_id"]))
                            yield r

        out = await replay_stream(_rows(), payouts)
        n_read = _n[0]
        conds = _seen
        # The error belongs to THIS WHALE'S result, not to a sibling key
        # in the whale map. A "_errors" entry beside the whales reads as
        # an eighth whale to every caller that iterates the map — the
        # probe's own jq does exactly that — and a fabricated row in a
        # P&L table is worse than the error it was reporting.
        if cf_error:
            out["cf_error"] = cf_error
        out["conditions_touched"] = len(conds)
        out["conditions_resolved"] = len(payouts)
        out["fills_read"] = n_read
        out["window"] = ("whole book" if since_d is None
                         else f"fills on or after {since_d}")
        if since_d is not None:
            out["window_warning"] = (
                "a WINDOWED replay seeds every balance at zero, so any "
                "position opened before this date has its exit booked "
                "as a fresh entry — realised P&L is understated and the "
                "ROI denominator inflated. Pass since=None for the "
                "whole book.")
        out["truncated"] = n_read >= max_fills
        if out["truncated"]:
            out["verdict_note"] = (
                f"TRUNCATED at {max_fills} fills — this is a partial "
                f"replay and the totals are floors, not totals")
        res[w] = out
    return res

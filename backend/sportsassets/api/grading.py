"""Fill-vs-miss grading math — the direct test of the copy thesis.

Owner 2026-08-12: "if we take every trade the whales take and get the same
or better price, over the long haul we get the same or better margin". That
is only true if the copies our price rule REFUSES are no better than the
ones it fills. This module scores both cohorts on the same yardstick:

  filled  — realized ROI on what we actually staked
  missed  — the counterfactual: a price-refused copy scored at HIS price
            against the market's real resolution

If misses persistently grade far above fills, "same or better" is selecting
away the whales' best trades (adverse selection) and the tolerance question
gets decided on this number instead of on theory.

Kept free of FastAPI so the arithmetic is unit-testable: the first version
lived inside the endpoint, shipped a JSONB-decode bug, and nothing could
catch it because nothing could import it.
"""

from __future__ import annotations

import json
from typing import Any


def _payout_vector(raw: Any) -> list | None:
    """Resolution payouts per outcome index, or None if unusable.

    asyncpg hands JSONB back as a STRING on a pool with no codec
    registered. Indexing that raw yields single CHARACTERS — float('[')
    raised, the caller 500'd, and the grade reported nothing while merely
    looking empty. Decode defensively and refuse anything that is not a
    list; a miss we cannot score is `unresolved`, never a guess.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    return raw if isinstance(raw, list) else None


def _blank() -> dict:
    return {"filled_n": 0, "filled_staked": 0.0, "filled_pnl": 0.0,
            "filled_staked_all": 0.0, "filled_open": 0,
            "filled_settled": 0, "missed_n": 0, "missed_staked": 0.0,
            "missed_pnl": 0.0, "missed_resolved": 0, "missed_unresolved": 0}


def grade_rows(rows) -> dict:
    """Fold live_orders rows into a per-whale filled-vs-missed scorecard.

    Each row needs: whale, status, his_price, req_usd, filled_usd, pnl,
    outcome_index, resolved_prices.
    """
    whales: dict[str, dict] = {}
    for r in rows:
        b = whales.setdefault((r["whale"] or "?").lower(), _blank())
        # THE TWO COHORTS MUST BE GRADED ON THE SAME DENOMINATOR
        # (2026-08-26, adversarial round 4).
        #
        # filled_staked accumulated EVERY filled row including ones
        # still open, while filled_pnl accumulated only the settled
        # ones -- so unrealized stake sat in the denominator with zero
        # in the numerator. The missed cohort has no such
        # contamination: missed_staked is incremented only inside the
        # resolved branch below, so unresolved misses leave both sides
        # alone.
        #
        # Both biases push the same way, and this module's own
        # docstring says the result decides a money question: "If
        # misses persistently grade far above fills, same-or-better is
        # selecting away the whales' best trades and the tolerance
        # question gets decided on this number." One cent of extra
        # tolerance on a 50c contract is 200bp -- larger than the whole
        # edge -- so an instrument tilted toward "fills grade badly" is
        # an argument to give away the edge it is measuring.
        #
        # 'cashed_out' belongs here too. It is what mirror_exit writes
        # when a copy is exited at a profit, and it was absent from the
        # feeding query entirely -- so every winning exited copy was
        # dropped from the filled cohort. Naming it here matters
        # independently: without it a cashed_out row reaching this
        # function falls through and is graded as a MISS, which is
        # worse than being dropped.
        if r["status"] in ("filled", "settled", "cashed_out"):
            b["filled_n"] += 1
            # Kept for display: what the sleeve actually has on the
            # table, open positions included. Never a ROI denominator.
            b["filled_staked_all"] += r["filled_usd"] or 0.0
            if r["pnl"] is not None:
                b["filled_settled"] += 1
                b["filled_pnl"] += r["pnl"]
                b["filled_staked"] += r["filled_usd"] or 0.0
            else:
                b["filled_open"] += 1
            continue
        # An 'unfilled' FOK is a price-refused copy: score what HIS price
        # would have returned on the actual resolution.
        b["missed_n"] += 1
        prices = _payout_vector(r["resolved_prices"])
        idx = r["outcome_index"]
        if (prices is None or idx is None
                or not (0 <= idx < len(prices))
                or not r["his_price"]):
            b["missed_unresolved"] += 1
            continue
        stake = r["req_usd"] or 0.0
        b["missed_resolved"] += 1
        b["missed_staked"] += stake
        # $stake at his price buys stake/his contracts, each worth
        # `payout` at settlement.
        b["missed_pnl"] += round(
            stake / float(r["his_price"]) * float(prices[idx]) - stake, 4)
    for b in whales.values():
        b["filled_roi"] = (round(b["filled_pnl"] / b["filled_staked"], 4)
                           if b["filled_staked"] else None)
        b["missed_roi"] = (round(b["missed_pnl"] / b["missed_staked"], 4)
                           if b["missed_staked"] else None)
        for k in ("filled_staked", "filled_pnl", "filled_staked_all",
                  "missed_staked", "missed_pnl"):
            b[k] = round(b[k], 2)
    return whales

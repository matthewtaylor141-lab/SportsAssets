"""PROOF-2: the decomposed thesis meter (owner order 2026-08-28).

The end-to-end PROOF cohort needs ~15K settled copies to resolve,
because market-outcome variance dominates a per-copy P&L sample. But
the sleeve's edge DECOMPOSES into terms with very different variance:

    sleeve_edge  =  (mix-weighted whale edge, measured on THEIR books)
                  - capture drag   (our fill price vs the whale's own)
                  - fees

and the capture drag is OUTCOME-FREE: for a filled copy, the dollar
difference vs having filled at the whale's own price is exactly
shares x (ours - his) — deterministic the moment the order fills,
independent of how the market resolves. Its CI tightens with hundreds
of fills, not tens of thousands. The whale-edge term comes from the
analytics worker's merge-inclusive per-whale CIs over their FULL
books (tens of thousands of closed lots each), published hourly under
'whale_edge_benchmark'.

This module combines the components into a live estimate of the
sleeve's edge per entry dollar with a CI, then answers the owner's
question directly: at MEASURED dollar flow, what is P(annual profit
>= 100% of principal) across a principal grid — and what flow
multiple the thesis requires. Every number derives from measured
components; the meter moves when the evidence moves.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

# The clean-cohort start the PROOF endpoint uses — execution machinery
# before this date is a different regime and would contaminate drag.
DEFAULT_SINCE = "2026-08-25T14:00:00+00:00"

TARGET_ANNUAL = 1.0            # the owner's thesis: >= 100% / year
PRINCIPAL_GRID = [100_000, 250_000, 500_000, 1_000_000, 2_000_000]
FLOW_MULTIPLES = [1, 5, 10, 30, 50]


def _phi(z: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ── THE FEE, CHARGED ─────────────────────────────────────────────────
#
# Until this module charged it, every dollar this system published for
# the Polymarket US venue was GROSS: `capture_from_rows` summed
# `kalshi_fee(...) if venue.startswith("kalshi") else 0.0`, so the one
# venue the whole copy lane trades on contributed a literal zero to the
# fee term while the lane is 100% taker. The owner's figures were
# overstated by exactly the taker fee on every PM-US fill.
#
# TWO SOURCES, IN THIS ORDER, AND NEVER ZERO BY DEFAULT.
#
#  1. THE VENUE'S OWN VALUE. Every order receipt this system persists
#     (live_orders.raw) carries the venue's executions, and each
#     execution carries commissionNotionalCollected /
#     commissionSpreadPx (the Order carries
#     commissionNotionalTotalCollected). `pmus._commission_fields` has
#     parsed those since Phase 7 rung 10 — but it runs on ONE path
#     (submit_fok under post_only=True, i.e. the mirror lane, which is
#     CANCEL-ONLY and has placed nothing, plus pmus.order_status, which
#     the rest lane calls for its terminal read). Nothing has ever read
#     them back OUT of a stored row. `receipt_executions` below does,
#     with that same parser, so a value the venue states is charged
#     the day it appears and no code change is needed to pick it up.
#
#  2. THE SCHEDULE, when the venue stated nothing. NOT zero. The
#     coefficient is READ off the venue's own market terms —
#     feeCoefficient 0.06 on 98/98 probed markets and 4,055/4,055
#     venue-wide (docs/mirror-to-a-tee-program.md §0 and §6) — and the
#     FORM is Kalshi's published shape, coefficient x shares x p x
#     (1-p). THE FORM IS AN ASSUMPTION AND IT IS LABELLED ONE
#     EVERYWHERE IT IS SERVED: the dollar formula behind PM-US's
#     coefficient is unread in this repository and no commission VALUE
#     has ever been observed from the venue (the 2026-09-02 21:52Z
#     probe printed every execution with both keys and no value). The
#     read that settles it is V1 rung 10 — one live fill whose receipt
#     states a commission — after which source (1) takes over for real
#     and the estimate stops being served.
#
# NOT ENVIRONMENT-READABLE, in either direction. A fee coefficient
# behind an env read is a money bound a shell can set to zero, which
# is the same class of lever the mirror's `capped_env` exists to
# close. It moves by deploy and review or it does not move.
#
# WHAT THIS DOES NOT CHARGE: the EXIT leg. A sale's receipt is not
# written back onto the entry row anywhere in live_executor (six
# `SET raw` sites, none of them a sale), so the exit's commission is
# not in data we hold. Every fee figure here is therefore a LOWER
# BOUND on the round trip and says so.
PMUS_FEE_COEFFICIENT = 0.06
KALSHI_FEE_COEFFICIENT = 0.07

# venue string (lower-cased, prefix-matched) -> schedule coefficient.
# A venue absent from this table has NO schedule: its rows are
# `unmeasured`, never zero. 'polymarket-clob' is deliberately absent —
# the global CLOB's fee is not read anywhere in this repository and
# `copy_sports.py`'s "Polymarket (zero fee)" comment is a belief about
# a different venue, which is the belief this unit exists to stop
# publishing as a number.
FEE_COEFFICIENT_BY_VENUE = {
    "polymarket-us": PMUS_FEE_COEFFICIENT,
    "kalshi": KALSHI_FEE_COEFFICIENT,
}
FEE_SCHEDULE_FORM = "coefficient * shares * price * (1 - price)"

FEE_VENUE = "venue"                  # the venue stated the dollars
FEE_SCHEDULE = "schedule_estimate"   # the coefficient + the assumed form
FEE_MIXED = "venue+schedule"         # some executions stated, some not
FEE_UNMEASURED = "unmeasured"        # no value, no schedule: never zero
FEE_NO_FILL = "no_fill"              # nothing traded: a true zero

# WHY a leg could not be priced. The two causes are different readings
# and the diagnostic said only one of them: a venue with no schedule in
# the table is a gap in what we know about the venue; a fill whose price
# we cannot read is a gap in the receipt. Naming the wrong one on a
# money path makes the next reader chase the wrong fix.
WHY_NO_SCHEDULE = "no fee schedule for this venue"
WHY_PRICE_UNREADABLE = "this execution's price is unreadable"

# CENSUS BOUNDS. A receipt with more executions than this, or a fee
# census that hits its row cap, is UNREADABLE — not truncated. (The
# same rule R4 landed for the position walk: a walk that hit its cap
# is not a reading of the account.)
MAX_EXECUTIONS_PER_RECEIPT = 200
MAX_FEE_CENSUS_ROWS = 4000
FEE_CENSUS_TIMEOUT_S = 8.0

# Below this many fills a per-lane rate is printed WITH its n and
# authorises nothing (§3b: "a metric below its minimum n authorises
# nothing"). 30 is the programme's book-clustered minimum.
MIN_N_FOR_A_LANE_RATE = 30

# HOW MUCH OF THE COHORT'S MONEY A FEE RATE MUST COVER BEFORE A NET
# SLEEVE EDGE IS PUBLISHED OFF IT.
#
# `fee_rate` is the fee of the rows priced IN FULL over those same
# rows' entry notional — honest as a rate — and `combine` then
# SUBTRACTS it from the whole cohort's edge, which extrapolates it to
# every row including the ones that could not be priced. At 1%
# coverage that is one row's rate published as the cohort's cost, and
# the served figure is the single number the owner reads. The lane
# rate already refuses to authorise anything below its minimum n; the
# owner-facing number had no equivalent gate at all.
#
# Below this share the pre-fee terms are still served in `capture`
# (with the measured rate and its coverage) and NO net sleeve edge is
# published — the same fail-closed direction as an unmeasurable fee.
MIN_FEE_NOTIONAL_SHARE = 0.5

# WHY A PER-DOLLAR RATE OFF THE SCHEDULE IS NOT A READING OF WHICH
# SIDE OF THE TRADE WE WERE ON. The schedule charges
# coefficient x shares x p x (1-p) and the notional is shares x p, so
# the rate per dollar is exactly coefficient x (1 - price): a maker
# fill at 0.20 and a taker fill at 0.80 differ by a factor of four
# with no reference to maker or taker at all. Every lane whose fills
# are charged this way carries this sentence beside its rate.
SCHEDULE_RATE_CAVEAT = (
    "NOT a maker-versus-taker reading: these fills are charged the "
    "SCHEDULE, and the schedule's rate per dollar is "
    "(coefficient x shares x p x (1-p)) / (shares x p) = "
    "coefficient x (1 - price). The figure therefore falls as price "
    "rises and moves with this lane's PRICE MIX, not with which side "
    "of the trade we were on.")


def kalshi_fee(shares: float, price: float) -> float:
    """Kalshi's published taker-fee formula."""
    return KALSHI_FEE_COEFFICIENT * shares * price * (1.0 - price)


def pmus_taker_fee(shares: float, price: float) -> float:
    """Polymarket US's taker fee at the venue's stated coefficient.

    ESTIMATE, not a reading: the coefficient is the venue's own
    (feeCoefficient 0.06 on every market probed), the FORM is Kalshi's
    published shape and is unverified against a PM-US commission value.
    Symmetric in p <-> 1-p, so a BUY_SHORT copy (whose fill_price names
    the long leg) is charged the same as the short leg it really is.
    """
    return PMUS_FEE_COEFFICIENT * shares * price * (1.0 - price)


def venue_key(venue: Any) -> str:
    """The fee-schedule key for a live_orders.venue string."""
    v = str(venue or "").strip().lower()
    if v.startswith("kalshi"):
        return "kalshi"
    return v


def schedule_fee(venue: Any, shares: Any, price: Any) -> float | None:
    """The schedule's fee in dollars, or None when this venue has no
    schedule in the table (unmeasured — never a zero standing in for
    one) or the fill is unreadable.

    An unreadable share count or a price outside (0, 1) is unreadable,
    not free: the caller must carry it as `unmeasured`.
    """
    coeff = FEE_COEFFICIENT_BY_VENUE.get(venue_key(venue))
    if coeff is None:
        return None
    try:
        s = float(shares)
        p = float(price)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(s) and math.isfinite(p)):
        return None
    if s <= 0:
        return 0.0
    if not 0.0 < p < 1.0:
        return None
    return coeff * s * p * (1.0 - p)


def _venue_commission(rec: Any) -> tuple[float | None, float | None]:
    """(commission_usd, commission_spread_px) THROUGH THE VENUE
    MODULE'S OWN PARSER — never a second implementation of it.

    `pmus._commission_fields` already owns every refusal this reading
    needs (an Amount or a bare scalar parses; absent, empty,
    unparseable, boolean and non-finite are None, because a bool is not
    one dollar and a NaN is not a fee). It ran on one path; this makes
    it run on the stored rows too. A venue module that cannot be
    imported here leaves the receipt UNREADABLE, which falls to the
    schedule — it never becomes zero.
    """
    try:
        from ..pmus import _commission_fields
    except Exception:  # noqa: BLE001 — contained: unreadable, not fatal
        return None, None
    try:
        return _commission_fields(rec)
    except Exception:  # noqa: BLE001
        return None, None


_FILL_TYPES = ("EXECUTION_TYPE_FILL", "EXECUTION_TYPE_PARTIAL_FILL")

# STAGES THAT PROVE A CROSS. Both are executions the venue returned in
# the CREATE response: `raw.response.executions` is the wire on every
# lane, and `raw.executions` is the parsed copy `submit_fok` attaches
# under post_only=True. An execution in either one filled before the
# order ever rested — it took liquidity. Only an execution that appears
# in the TERMINAL read alone arrived while the order was resting.
_CROSS_STAGES = ("placement", "post_only")


def _exec_number(v: Any) -> float | None:
    """A venue number in either shape: a parsed float, or the wire's
    Amount dict. Unknown stays None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, dict):
        v = v.get("value")
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _one_execution(ex: Any, stage: str) -> dict | None:
    """One stored execution -> the economics view of it, or None when
    it is not a fill we can read.

    Handles BOTH stored shapes with one reader: the PARSED record
    (`last_px` / `last_shares` / `commission_usd`, written by
    `pmus._execution_record` on the post-only path and by
    `order_status`, which is where the rest lane's terminal read lands)
    and the RAW WIRE (`lastPx` / `lastShares` /
    `commissionNotionalCollected`, which every lane stores under
    raw->response).
    """
    if not isinstance(ex, dict):
        return None
    # A FILL IS WHAT THE VENUE MODULE CALLS A FILL. `pmus.submit_fok`
    # counts shares only when `type` is one of the two FILL types
    # (pmus.py:2431), and `_execution_record` carries `type` through
    # verbatim, so a record with no type is not a fill this system has
    # ever counted as one. Admitting it inflated the charge AND
    # `n_fills` — the count `sufficient_n` gates on. A receipt whose
    # only entries are untyped now yields no fills, and `row_fee` falls
    # to the row's own schedule charge: never zero.
    if ex.get("type") not in _FILL_TYPES:
        return None
    shares = _exec_number(ex.get("last_shares"))
    if shares is None:
        shares = _exec_number(ex.get("lastShares"))
    price = _exec_number(ex.get("last_px"))
    if price is None:
        price = _exec_number(ex.get("lastPx"))
    if shares is None or shares <= 0:
        return None
    if "commission_usd" in ex:                       # parsed record
        usd = _exec_number(ex.get("commission_usd"))
        spread = _exec_number(ex.get("commission_spread_px"))
    else:                                            # the venue's wire
        usd, spread = _venue_commission(ex)
    # BOTH SPELLINGS. `pmus._execution_record` reads `aggressor` on an
    # Execution; `pmus.recent_trades` reads `isAggressor` on a Trade
    # (pmus.py:2688). Which spelling an Execution carries on the wire is
    # NOT established in this repository — the only recorded observation
    # (pmus.py:2136-2139) printed the commission keys, not this one — so
    # reading one spelling alone risks serving "the flag is absent from
    # every fill" about a flag that was present under the other name.
    agg = ex.get("aggressor")
    if not isinstance(agg, bool):
        agg = ex.get("isAggressor")
    return {
        "id": (str(ex.get("id")) if ex.get("id") else None),
        "trade_id": (str(ex.get("trade_id") or ex.get("tradeId"))
                     if (ex.get("trade_id") or ex.get("tradeId")) else None),
        "stage": stage,
        "shares": shares,
        "price": price,
        "commission_usd": usd,
        "commission_spread_px": spread,
        "aggressor": agg if isinstance(agg, bool) else None,
    }


def _exec_list(v: Any) -> list | None:
    """A stored execution list in either transport: already decoded, or
    the JSON text asyncpg hands back for a jsonb column."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except (TypeError, ValueError):
            return None
    return v if isinstance(v, list) else None


def receipt_executions(receipt: Any, *,
                       cap: int = MAX_EXECUTIONS_PER_RECEIPT
                       ) -> list[dict] | None:
    """Every readable fill in one stored order receipt, or None when
    the receipt is unreadable or over the census bound.

    THREE PLACES THE EXECUTIONS ARE STORED, and all three are read:
      * `raw.response.executions` — the venue's wire, on every lane.
        On the rest lane these are the executions AT PLACEMENT, which
        is the one structural tell of a resting order that CROSSED
        (a bid that filled before it ever rested is a taker fill).
      * `raw.final.executions` — the parsed records from the rest
        lane's terminal `order_status` read: everything the order did.
      * `raw.executions` — the parsed records submit_fok attaches
        under post_only=True (the mirror lane).

    De-duplicated by the venue's execution id, so an execution present
    both at placement and in the terminal read is ONE fill, stamped
    with the EARLIER stage (placement wins: it is the one that proves
    the cross). WHEN TWO NON-EMPTY LISTS ARE PRESENT AND AN EXECUTION
    CARRIES NO ID, THE RECEIPT IS UNREADABLE: the terminal read repeats
    the placement's executions, so without an id to join on, the same
    fill would be charged twice and counted as two fills on two
    different sides of the maker/taker split. One list needs no join
    and is read as it stands.

    AN EMPTY LIST IS NOT A LIST TO JOIN AGAINST. It contributes no
    execution and can collide with nothing, but counting it flipped the
    guard on and threw the whole receipt away the moment any execution
    lacked an id — which is the normal stored shape of a genuine maker
    fill (a GTC that rested has no placement execution, so
    raw.response.executions is [] while the fill arrives in
    raw.final.executions). The guard now counts NON-EMPTY lists.

    EACH RECORD CARRIES `placement_read`: whether this receipt contains
    a placement-stage list AT ALL (empty counts — an empty list is the
    venue affirming that nothing filled at create). Without it, an
    ABSENT placement list reads exactly like an order that rested, and
    the rest lane's lost-placement recovery path stores raw.response =
    {} — so a fill that crossed on arrival would be published as a
    maker fill, the one mis-classification direction that flatters the
    owner's premise.

    OVER THE BOUND IS UNREADABLE, NOT TRUNCATED: a partial reading of a
    receipt would under-charge the fee, which is the direction this
    unit exists to stop.
    """
    if isinstance(receipt, str):
        try:
            receipt = json.loads(receipt)
        except (TypeError, ValueError):
            return None
    if not isinstance(receipt, dict):
        return None
    resp = receipt.get("response")
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except (TypeError, ValueError):
            resp = None
    final = receipt.get("final")
    if isinstance(final, str):
        try:
            final = json.loads(final)
        except (TypeError, ValueError):
            final = None
    sources = [
        # (list, stage) — placement first so it wins the de-dupe.
        (_exec_list(receipt.get("response_execs")), "placement"),
        (_exec_list((resp or {}).get("executions")
                    if isinstance(resp, dict) else None), "placement"),
        (_exec_list(receipt.get("final_execs")), "terminal"),
        (_exec_list((final or {}).get("executions")
                    if isinstance(final, dict) else None), "terminal"),
        (_exec_list(receipt.get("executions")), "post_only"),
    ]
    present = [(s, stage) for s, stage in sources if s is not None]
    if not present:
        return None
    if sum(len(s) for s, _ in present) > cap:
        return None
    # An empty list carries no execution and can double-charge nothing.
    joinable = sum(1 for s, _ in present if s) > 1
    placement_read = any(stage in _CROSS_STAGES for _, stage in present)
    out: list[dict] = []
    seen: set[str] = set()
    for lst, stage in present:
        for ex in lst:
            rec = _one_execution(ex, stage)
            if rec is None:
                continue
            rec["placement_read"] = placement_read
            key = rec["id"] or rec["trade_id"]
            if not key:
                if joinable:
                    return None      # cannot join: would double-charge
                out.append(rec)
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append(rec)
    return out


def _order_commission(receipt: Any) -> float | None:
    """The ORDER-level commission total the venue stated, when it
    stated one: `order_status` puts it on raw->final->commission_usd
    (SDK commissionNotionalTotalCollected, pmus.py:2735, stored by the
    rest lane's terminal read).

    Used whenever NO EXECUTION stated its own — including on a row that
    carries readable executions, which is the case this reader used to
    miss entirely. commissionNotionalTotalCollected is the field the
    SDK actually types on the Order, so it is the most likely of the
    three commission fields to carry a value first, and discarding it
    because the row also had executions charged an unverified schedule
    estimate over the venue's own stated number. It is NOT added to
    per-execution values: it is the total for the same order, so
    charging both would double-count."""
    if isinstance(receipt, str):
        try:
            receipt = json.loads(receipt)
        except (TypeError, ValueError):
            return None
    if not isinstance(receipt, dict):
        return None
    if "final_commission_usd" in receipt:
        return _exec_number(receipt.get("final_commission_usd"))
    final = receipt.get("final")
    if isinstance(final, str):
        try:
            final = json.loads(final)
        except (TypeError, ValueError):
            final = None
    if isinstance(final, dict):
        return _exec_number(final.get("commission_usd"))
    return None


def entry_shares(row: dict) -> Any:
    """THE SHARES THE ENTRY LEG WAS CHARGED ON — not the shares the row
    has LEFT.

    `filled_shares` is a REMAINDER, not "what ever filled": every
    partial exit decrements it (live_executor.py:1530 and :6222,
    `filled_shares = GREATEST(filled_shares - $2, 0)`) while
    `filled_usd` is never decremented, so a 100-share entry trimmed to
    40 was charging the venue's fee on 40 shares — and the same served
    bucket then took `staked` from the full `filled_usd` and the fee
    from the remainder, so the ROI denominator and the fee denominator
    disagreed about how many shares were bought.

    Migration 040 captured the original count beside it (`orig_shares`,
    written once at the first exit and never rewritten) precisely
    because the original cannot be recovered afterwards. It is the
    charge base wherever the row carries it.

    ONE HISTORICAL ROW SHAPE THIS CANNOT FIX, said plainly rather than
    implied: 040's backfill is `SET orig_shares = filled_shares WHERE
    orig_shares IS NULL` (040_live_orders_orig_shares.sql:43-46), and
    the migration's own comment says why — a row already trimmed before
    040 ran has LOST its original count and it cannot be recovered.
    For those rows the stored `orig_shares` IS the remainder, this
    reader cannot tell, and the fee is charged on what was left. That
    understates the fee and therefore overstates `pnl_net`, on a
    bounded set of rows that stops growing the moment 040 is applied.
    """
    orig = row.get("orig_shares")
    try:
        if orig is not None and float(orig) > 0:
            return orig
    except (TypeError, ValueError):
        pass
    return row.get("shares", row.get("filled_shares"))


def _row_traded(row: dict) -> bool:
    """Did this row ever buy anything? A zero share count means one of
    two different things and the reader cannot tell them apart from the
    count alone: 'never filled' (a true zero fee) and 'filled and then
    fully exited' (a fee we paid and can no longer see). Realized
    dollars or a filled notional settle it — a row that never filled
    has neither.

    `stake` is deliberately NOT one of them: on the census's own row
    shape it is COALESCE(NULLIF(filled_usd, 0), requested_usd), so an
    order that never filled still carries the dollars it ASKED for, and
    reading those as evidence of a fill would turn a true zero into an
    unreadable one."""
    for k in ("pnl", "filled_usd", "orig_shares"):
        v = row.get(k)
        try:
            if v is not None and float(v) != 0.0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def row_fee(row: dict) -> dict:
    """What one filled row's ENTRY leg actually cost us in commission.

    {"fee_usd", "fee_source", "n_exec", "n_venue_stated",
     "n_exec_unmeasured", "fee_is_partial", "order_total_ignored",
     "why"}.

    fee_usd is None ONLY when the row is genuinely unmeasured (a venue
    with no stated value and no schedule, or a fill we cannot read) —
    never a zero standing in for an unknown, and never a zero standing
    in for "this venue is free". A stated zero from the venue IS a
    value and is charged as one.

    ONE UNREADABLE LEG DOES NOT VOID THE ROW. A single execution the
    schedule could not price used to return None for the WHOLE row,
    discarding the charge on every readable leg — under-charging, which
    is the direction this unit exists to stop. The readable legs are
    charged, the unreadable ones are COUNTED (`n_exec_unmeasured`) and
    the row is flagged `fee_is_partial`, so its dollars are a LOWER
    BOUND on that row's fee and every surface that adds them up can say
    so. A row where nothing at all could be priced is still None.

    A NEGATIVE stated commission is a REBATE and is carried through
    with its sign, so the day a maker rest earns one, the P&L shows it
    without another code change. Nothing here assumes one exists: no
    rebate value has ever been observed from this venue.
    """
    venue = row.get("venue")
    receipt = row.get("receipt", row.get("raw"))
    execs = receipt_executions(receipt)
    order_total = _order_commission(receipt)
    fee = 0.0
    n_stated = 0
    n_sched = 0
    n_unmeas = 0
    reasons: set[str] = set()
    if execs:
        for ex in execs:
            if ex["commission_usd"] is not None:
                fee += ex["commission_usd"]
                n_stated += 1
                continue
            s = schedule_fee(venue, ex["shares"], ex["price"])
            if s is None:
                n_unmeas += 1
                reasons.add(
                    WHY_NO_SCHEDULE
                    if venue_key(venue) not in FEE_COEFFICIENT_BY_VENUE
                    else WHY_PRICE_UNREADABLE)
            else:
                fee += s
                n_sched += 1
        # CLAUSE (1), ON A ROW THAT ALSO HAS EXECUTIONS: no execution
        # priced itself, but the venue priced the ORDER. Charge the
        # venue's own number rather than an unverified estimate.
        if n_stated == 0 and order_total is not None:
            return {"fee_usd": order_total, "fee_source": FEE_VENUE,
                    "n_exec": len(execs), "n_venue_stated": 1,
                    "n_exec_unmeasured": 0, "fee_is_partial": False,
                    "order_total_ignored": False,
                    "why": "no execution stated a commission; the venue "
                           "stated an order-level total and it is charged"}
        if n_stated == 0 and n_sched == 0:
            return {"fee_usd": None, "fee_source": FEE_UNMEASURED,
                    "n_exec": len(execs), "n_venue_stated": 0,
                    "n_exec_unmeasured": n_unmeas, "fee_is_partial": False,
                    "order_total_ignored": False,
                    "why": (f"no execution on this row could be priced: "
                            + "; ".join(sorted(reasons)))}
        src = (FEE_VENUE if n_sched == 0 else
               FEE_SCHEDULE if n_stated == 0 else FEE_MIXED)
        return {"fee_usd": fee, "fee_source": src, "n_exec": len(execs),
                "n_venue_stated": n_stated,
                "n_exec_unmeasured": n_unmeas,
                "fee_is_partial": bool(n_unmeas),
                # The order total is the same order's money. It is not
                # added to per-execution values; it is recorded as seen
                # and not charged so the discard is visible.
                "order_total_ignored": bool(n_stated and
                                            order_total is not None),
                "why": (f"{n_unmeas} execution(s) could not be priced and "
                        f"are not charged: " + "; ".join(sorted(reasons))
                        if n_unmeas else None)}
    if order_total is not None:
        return {"fee_usd": order_total, "fee_source": FEE_VENUE,
                "n_exec": 0, "n_venue_stated": 1, "n_exec_unmeasured": 0,
                "fee_is_partial": False, "order_total_ignored": False,
                "why": "the venue stated an order-level total"}
    shares = entry_shares(row)
    price = row.get("price", row.get("fill_price"))
    try:
        if shares is not None and float(shares) <= 0:
            # A ROW THAT TRADED AND SHOWS ZERO SHARES IS UNMEASURED,
            # NEVER `no_fill`. The mirror lane's only path off 'filled'
            # fires WHERE filled_shares = 0 (_MIRROR_CLOSE_CASHED_OUT_SQL,
            # live_executor.py:6309-6311), so a mirror book that bought
            # and fully exited reaches this reader with zero shares and
            # an accumulated pnl. Charging it the literal 0.0 of
            # `no_fill` is the `else 0.0` this unit exists to delete,
            # re-entering by another door, on the one lane the owner
            # wants a net number for.
            if _row_traded(row):
                return {"fee_usd": None, "fee_source": FEE_UNMEASURED,
                        "n_exec": 0, "n_venue_stated": 0,
                        "n_exec_unmeasured": 0, "fee_is_partial": False,
                        "order_total_ignored": False,
                        "why": "the row's share count is zero but it "
                               "carries realized dollars: it filled and "
                               "was exited, and the entry fee it paid "
                               "cannot be recovered from the remainder"}
            return {"fee_usd": 0.0, "fee_source": FEE_NO_FILL,
                    "n_exec": 0, "n_venue_stated": 0,
                    "n_exec_unmeasured": 0, "fee_is_partial": False,
                    "order_total_ignored": False,
                    "why": "nothing filled"}
    except (TypeError, ValueError):
        pass
    s = schedule_fee(venue, shares, price)
    if s is None:
        return {"fee_usd": None, "fee_source": FEE_UNMEASURED,
                "n_exec": 0, "n_venue_stated": 0, "n_exec_unmeasured": 0,
                "fee_is_partial": False, "order_total_ignored": False,
                "why": (f"no stored commission and no fee schedule for "
                        f"venue {venue_key(venue)!r}"
                        if venue_key(venue) not in FEE_COEFFICIENT_BY_VENUE
                        else "the fill's shares or price are unreadable")}
    return {"fee_usd": s, "fee_source": FEE_SCHEDULE,
            "n_exec": 0, "n_venue_stated": 0, "n_exec_unmeasured": 0,
            "fee_is_partial": False, "order_total_ignored": False,
            "why": "no stored executions; charged off the row's own "
                   "entry fill"}


def safe_row_fee(row: dict) -> dict:
    """`row_fee` with its refusal CONTAINED to the row.

    Every caller on a money path goes through this, not through
    `row_fee` directly: the fee reading is a read, and a read that
    raises must cost the row's fee, never the cohort, the page or the
    request handler above it. `charge_fees` had this guard;
    `capture_from_rows` called the reader bare, so the same guard now
    sits in one place and both use it.
    """
    try:
        return row_fee(row)
    except Exception:  # noqa: BLE001 — contained: one row, not the page
        return {"fee_usd": None, "fee_source": FEE_UNMEASURED,
                "n_exec": 0, "n_venue_stated": 0, "n_exec_unmeasured": 0,
                "fee_is_partial": False, "order_total_ignored": False,
                "why": "the fee reader refused this row"}


def charge_fees(rows: list[dict]) -> list[dict]:
    """Stamp fee_usd / fee_source on every row. Pure; never raises —
    a row this reader cannot price comes back `unmeasured`, which the
    surfaces must serve as unreadable, never as zero."""
    out = []
    for r in rows:
        f = safe_row_fee(r)
        out.append({**r, "fee_usd": f["fee_usd"],
                    "fee_source": f["fee_source"],
                    "fee_n_exec": f["n_exec"],
                    "fee_n_venue_stated": f["n_venue_stated"],
                    "fee_is_partial": f.get("fee_is_partial", False),
                    "fee_n_exec_unmeasured": f.get("n_exec_unmeasured", 0)})
    return out


# ── WHAT MAKING A MARKET ACTUALLY PAYS US ────────────────────────────
#
# The owner's belief is that mirroring the whale's resting orders makes
# us a market maker and that market making is near-guaranteed profit.
# The half of that which is true is measurable from fills we already
# own: a maker earns the spread and whatever the venue rebates. The
# half that is not is ALSO in these numbers, and it is not the fee: a
# rest fills precisely when the other side comes to it, i.e. when the
# market is moving against the rest. That is adverse selection and it
# does not appear in a commission reading at all.
#
# So this block reports, per lane, PER FILL: whether we were the
# aggressor, what commission was charged or rebated, and the per-dollar
# rate — and it reports the realised P&L beside it, because the fee is
# the smaller of the two numbers that decide whether making a market
# pays us.
#
# THE LANE IS THE STRUCTURAL READING and the venue's `aggressor` flag
# is an INDEPENDENT one:
#   * lane NULL (the IOC lane) crosses the book by construction: taker.
#   * lane 'rest' places a GTC at his exact price and cancels it after
#     a few seconds. An execution in the PLACEMENT response crossed on
#     arrival (taker); an execution that appears only in the terminal
#     read arrived while the order rested (maker).
#   * lane 'mirror' rests post-only: maker. It has placed nothing.
# When the venue's flag is present, the two readings are compared and
# the DISAGREEMENT is printed. Neither is promoted over the other until
# they agree on a real fill: the SDK's Execution.aggressor has never
# been observed carrying a value in production, so which side of the
# trade it names is unverified here.
LANE_TAKER = "taker"
LANE_MAKER = "maker"
LANE_UNKNOWN = "unknown"


def structural_role(lane: Any, stage: str, *,
                    placement_read: bool = True) -> str:
    """maker / taker from the lane and the execution's stage. Unknown
    lanes are 'unknown', never guessed into either side.

    THE STAGE TEST APPLIES TO EVERY RESTING LANE, NOT JUST 'rest'. The
    mirror lane rests post-only, but post-only is a raw environment
    read (`ml:272`) and it LATCHES ITSELF OFF on a fill-at-create
    (`ml:2288 _POST_ONLY_OK = False`) — that latch exists precisely
    because a mirror order can cross. Calling every mirror fill a maker
    fill made the one reading the owner's "we become market makers"
    claim will be quoted from unable to see the event that disproves
    it. An execution in the create response (`placement`, or the parsed
    `post_only` copy of the same response) filled before the order ever
    rested: it took liquidity, whatever the lane intended.

    `placement_read=False` means THIS RECEIPT HAS NO PLACEMENT LIST AT
    ALL, so 'appears only in the terminal read' is not evidence of
    resting — it is evidence of nothing. The rest lane's lost-placement
    recovery path stores raw.response = {}, and reading that absence as
    a maker fill is the only mis-classification direction that flatters
    the premise. Those fills are `unknown` and are counted as such.
    """
    ln = str(lane or "ioc").strip().lower() or "ioc"
    if ln in ("ioc", "sweep"):
        return LANE_TAKER
    if ln in ("rest", "mirror"):
        if stage in _CROSS_STAGES:
            return LANE_TAKER
        return LANE_MAKER if placement_read else LANE_UNKNOWN
    return LANE_UNKNOWN


def venue_role(aggressor: Any) -> str:
    """The venue's own reading: isAggressor True means we took."""
    if aggressor is True:
        return LANE_TAKER
    if aggressor is False:
        return LANE_MAKER
    return "unknown"


def rate_side(buckets: list[dict], role: str) -> dict:
    """One side of the maker-versus-taker comparison, aggregated over
    every lane that landed on that side."""
    agg = {"role": role, "n_fills": 0, "commission_usd": 0.0,
           "charged_notional_usd": 0.0, "charged_shares": 0.0,
           "n_venue_stated": 0, "n_schedule": 0, "n_unmeasured": 0}
    for b in buckets:
        if b.get("role") != role:
            continue
        agg["n_fills"] += int(b.get("n_fills") or 0)
        agg["commission_usd"] = round(
            agg["commission_usd"] + float(b.get("commission_usd") or 0.0), 6)
        agg["charged_notional_usd"] = round(
            agg["charged_notional_usd"]
            + float(b.get("charged_notional_usd") or 0.0), 6)
        agg["charged_shares"] = round(
            agg["charged_shares"] + float(b.get("charged_shares") or 0.0), 6)
        agg["n_venue_stated"] += int(b.get("n_venue_stated") or 0)
        agg["n_schedule"] += int(b.get("n_schedule") or 0)
        agg["n_unmeasured"] += int(b.get("n_unmeasured") or 0)
    agg["mean_price_charged"] = (
        round(agg["charged_notional_usd"] / agg["charged_shares"], 4)
        if agg["charged_shares"] else None)
    return agg


def rate_comparison(buckets: list[dict]) -> dict:
    """THE UNIT'S SECOND DELIVERABLE, AND ITS REFUSAL.

    "The maker side compared against the taker side on OUR OWN fills
    rather than on a claim" is a comparison of two per-dollar rates —
    and a rate computed off the fee SCHEDULE is coefficient x (1 -
    price), so two lanes charged that way differ by their PRICE MIX and
    by nothing else. Serving those two numbers side by side publishes
    "making a market costs us a seventh of what taking costs" as a
    measurement, on the one instrument built to test the owner's
    premise, when the two lanes happened to fill at different prices.
    It is the same defect as the `rate_per_dollar 0.0` that was
    blocking in round 1, arrived at from the other direction.

    So the comparison is published ONLY when both sides' commissions
    are the VENUE'S OWN STATED VALUES — the one input that carries
    information about which side of the trade we were on. Otherwise no
    rate is published for either side of the comparison and the block
    says, in words, why the figure a reader would compute himself is
    not a reading. No commission value has ever been observed from this
    venue (pmus.py:2136-2139), so today this refuses, every time, and
    that IS the honest state of deliverable (2) until V1 rung 10 lands
    one live fill whose receipt states a commission.
    """
    mk = rate_side(buckets, LANE_MAKER)
    tk = rate_side(buckets, LANE_TAKER)
    out: dict[str, Any] = {"maker": mk, "taker": tk,
                           "min_n_for_a_rate": MIN_N_FOR_A_LANE_RATE}
    if not mk["charged_notional_usd"] or not tk["charged_notional_usd"]:
        out["comparable"] = False
        out["verdict"] = (
            "no comparison: there is no charged fill on "
            + ("either side" if not mk["charged_notional_usd"]
               and not tk["charged_notional_usd"]
               else "the maker side" if not mk["charged_notional_usd"]
               else "the taker side")
            + " of this reading, so nothing is compared and no rate is "
              "published for either.")
        return out
    if mk["n_schedule"] or tk["n_schedule"]:
        out["comparable"] = False
        out["verdict"] = (
            f"NOT A READING, and no rate is published for either side: "
            f"{mk['n_schedule']} of {mk['n_schedule'] + mk['n_venue_stated']} "
            f"charged maker fill(s) and "
            f"{tk['n_schedule']} of {tk['n_schedule'] + tk['n_venue_stated']} "
            f"charged taker fill(s) are charged the SCHEDULE, whose rate "
            f"per dollar is coefficient x (1 - price). Two lanes charged "
            f"that way differ by their PRICE MIX and by nothing else — "
            f"the mean charged price here is "
            f"{mk['mean_price_charged']} on the maker side and "
            f"{tk['mean_price_charged']} on the taker side — so a "
            f"maker-versus-taker rate computed from these fills would be "
            f"a price artifact, not evidence about which side of the "
            f"trade pays more. The venue's own stated commission on both "
            f"sides is what settles it (V1 rung 10); until then this "
            f"comparison is withheld.")
        return out
    mk_rate = mk["commission_usd"] / mk["charged_notional_usd"]
    tk_rate = tk["commission_usd"] / tk["charged_notional_usd"]
    out["comparable"] = True
    out["maker_rate_per_dollar"] = round(mk_rate, 6)
    out["taker_rate_per_dollar"] = round(tk_rate, 6)
    out["maker_minus_taker_per_dollar"] = round(mk_rate - tk_rate, 6)
    out["sufficient_n"] = (mk["n_fills"] >= MIN_N_FOR_A_LANE_RATE
                           and tk["n_fills"] >= MIN_N_FOR_A_LANE_RATE)
    out["verdict"] = (
        "both sides' commissions are the VENUE'S OWN stated values, so "
        "this is a comparison of what the venue charged us and not of "
        "the schedule's price curve"
        + ("" if out["sufficient_n"] else
           f"; below the minimum n of {MIN_N_FOR_A_LANE_RATE} fills a "
           f"side, it authorises nothing")
        + ". It is the ENTRY leg only and it says nothing about adverse "
          "selection, which is the larger of the two numbers that decide "
          "whether making a market pays us.")
    return out


def fill_economics(rows: list[dict], *, max_fills: int = 400) -> dict:
    """Per-lane economics on OUR OWN fills — the maker side compared
    against the taker side on evidence, not on a claim.

    rows: live_orders rows carrying venue, lane, filled_shares,
    fill_price, pnl, filled_usd/stake and the stored receipt. Pure.
    """
    lanes: dict[str, dict] = {}
    fills: list[dict] = []
    agree = disagree = flag_present = flag_absent = 0
    rows_unreadable = 0
    charged_rows = 0
    stage_unknown = 0
    lane_unreadable = 0
    no_fill_rows = 0
    for r in rows:
        # WAS THE LANE COLUMN EVEN READ? The census's fallback statement
        # (a database without migration 041) selects `lane` as NULL, and
        # a NULL lane is the IOC lane — so every fill on such a database
        # was published as an `ioc` TAKER fill, with `receipt_census`
        # saying "read" and no marker anywhere. The direction is
        # conservative (the maker side vanishes rather than being
        # invented) but the maker side is the whole point of the
        # reading, and an absent column is not evidence of a lane.
        lane_read = r.get("lane_column_read") is not False
        lane = ("unreadable" if not lane_read else
                str(r.get("lane") or "ioc").strip().lower() or "ioc")
        venue = r.get("venue")
        try:
            execs = receipt_executions(r.get("receipt", r.get("raw")))
        except Exception:  # noqa: BLE001 — contained: one row, not the page
            execs = None
        if execs is None:
            rows_unreadable += 1
            continue
        charged_rows += 1
        if not execs:
            # READ IT, AND NOTHING FILLED. An order that placed and
            # never filled returns an EMPTY list, not None, and counting
            # it `n_rows_receipt_unreadable` conflated it with a receipt
            # this reader could not read — muddying the one counter the
            # payload uses to say how much of the maker side cannot be
            # seen. The row is still charged by `row_fee`; it just has
            # no fill to attribute to a side.
            no_fill_rows += 1
            continue
        if not lane_read:
            lane_unreadable += 1
        for ex in execs:
            sr = structural_role(
                lane, ex["stage"],
                placement_read=bool(ex.get("placement_read", True)))
            if sr == LANE_UNKNOWN and not ex.get("placement_read", True):
                stage_unknown += 1
            vr = venue_role(ex["aggressor"])
            if vr == "unknown":
                flag_absent += 1
            else:
                flag_present += 1
                if sr != "unknown":
                    if vr == sr:
                        agree += 1
                    else:
                        disagree += 1
            # A FILL WITH NO NOTIONAL cannot carry a per-dollar rate:
            # charging its commission into a lane whose denominator
            # cannot include it inflates that lane's rate. A price of
            # exactly 0.0 is the same shape as no price at all — the
            # notional is zero either way — so it counts as unmeasured
            # on both sides rather than as free money on one.
            notional = ((ex["shares"] * ex["price"])
                        if ex["price"] is not None else None)
            if notional is not None and notional <= 0:
                notional = None
            stated = ex["commission_usd"]
            if notional is None:
                fee, src = None, FEE_UNMEASURED
            elif stated is None:
                fee = schedule_fee(venue, ex["shares"], ex["price"])
                src = FEE_SCHEDULE if fee is not None else FEE_UNMEASURED
            else:
                fee, src = stated, FEE_VENUE
            key = f"{lane}:{sr}"
            b = lanes.setdefault(key, {
                "lane": lane, "role": sr, "n_fills": 0, "shares": 0.0,
                "notional_usd": 0.0, "charged_notional_usd": 0.0,
                "charged_shares": 0.0, "commission_usd": 0.0,
                "rebate_usd": 0.0, "n_venue_stated": 0,
                "n_schedule": 0, "n_unmeasured": 0})
            b["n_fills"] += 1
            b["shares"] = round(b["shares"] + ex["shares"], 6)
            if notional is not None:
                b["notional_usd"] = round(b["notional_usd"] + notional, 6)
            if src == FEE_UNMEASURED:
                b["n_unmeasured"] += 1
            else:
                b["commission_usd"] = round(b["commission_usd"] + fee, 6)
                # THE RATE'S OWN DENOMINATOR. Only the notional of the
                # fills actually CHARGED goes in it. Dividing a partial
                # numerator by the whole lane's notional serves a rate
                # diluted by however many fills the reader could not
                # price — and on a lane where nothing was measurable it
                # served 0.0, which is "making a market costs us nothing
                # per dollar": the owner's premise handed back as a
                # measurement, on the instrument built to test it.
                b["charged_notional_usd"] = round(
                    b["charged_notional_usd"] + (notional or 0.0), 6)
                # The charged shares ride along so the lane's own MEAN
                # CHARGED PRICE can be served: on a schedule-charged
                # lane the rate is coefficient x (1 - price), so the
                # price mix IS the rate and a reader must be able to
                # see it beside the number.
                b["charged_shares"] = round(
                    b["charged_shares"] + ex["shares"], 6)
                if fee < 0:
                    b["rebate_usd"] = round(b["rebate_usd"] - fee, 6)
                b["n_venue_stated" if src == FEE_VENUE
                  else "n_schedule"] += 1
            if len(fills) < max_fills:
                fills.append({
                    "lane": lane,
                    "role_structural": sr,
                    "role_venue_flag": vr,
                    "aggressor": ex["aggressor"],
                    "stage": ex["stage"],
                    "shares": ex["shares"],
                    "price": ex["price"],
                    "notional_usd": (round(notional, 4)
                                     if notional is not None else None),
                    "commission_usd": (round(fee, 6)
                                       if fee is not None else None),
                    "commission_source": src,
                    "rate_per_dollar": (round(fee / notional, 6)
                                        if fee is not None and notional
                                        else None),
                })
    for b in lanes.values():
        b["rate_per_dollar"] = (
            round(b["commission_usd"] / b["charged_notional_usd"], 6)
            if b["charged_notional_usd"] else None)
        b["rate_basis"] = (
            "commission / notional of the fills actually charged "
            f"(${b['charged_notional_usd']:.2f} of "
            f"${b['notional_usd']:.2f} priced notional)")
        # THE PRICE MIX, BESIDE THE RATE. See SCHEDULE_RATE_CAVEAT: on
        # a schedule-charged lane this number and the coefficient are
        # the whole of the rate.
        b["mean_price_charged"] = (
            round(b["charged_notional_usd"] / b["charged_shares"], 4)
            if b["charged_shares"] else None)
        b["rate_is_venue_measured"] = bool(b["n_venue_stated"]
                                           and not b["n_schedule"])
        if b["rate_per_dollar"] is not None \
                and not b["rate_is_venue_measured"]:
            b["rate_caveat"] = SCHEDULE_RATE_CAVEAT
        b["sufficient_n"] = b["n_fills"] >= MIN_N_FOR_A_LANE_RATE
        if b["n_unmeasured"]:
            b["commission_usd_is_partial"] = True
    verdict = ("unverified — the venue's aggressor flag is absent from "
               "every readable fill"
               if flag_present == 0 else
               "agrees with the lane on every fill that carries it"
               if disagree == 0 else
               f"DISAGREES with the lane on {disagree} of "
               f"{flag_present} flagged fills — the flag's meaning is "
               f"not established and neither reading may be published "
               f"as the maker share")
    by_lane = sorted(lanes.values(), key=lambda b: (b["lane"], b["role"]))
    return {
        "n_rows": len(rows),
        "n_rows_read": charged_rows,
        "n_rows_receipt_unreadable": rows_unreadable,
        # A receipt READ that holds no fill is not an unreadable one.
        "n_rows_read_no_fill": no_fill_rows,
        "by_lane": by_lane,
        # DELIVERABLE (2), AND ITS REFUSAL WHEN IT IS NOT A READING.
        # The per-lane rates above are each an honest statement of what
        # that lane's fills cost per dollar; the COMPARISON between them
        # is only a reading when the venue priced both sides, and this
        # block publishes the comparison or says why it is withheld.
        "rate_comparison": rate_comparison(by_lane),
        # WHETHER THE LANE COLUMN WAS READABLE AT ALL. Without it every
        # fill would otherwise be published as an `ioc` taker fill.
        "n_rows_lane_unreadable": lane_unreadable,
        "lane_column": (
            "read" if not lane_unreadable else
            f"UNREADABLE on {lane_unreadable} row(s) — the receipt census "
            f"fell back to the statement that omits `lane` (migration "
            f"041), so those fills cannot be attributed to either side "
            f"and are counted lane 'unreadable' / role 'unknown', never "
            f"taker"),
        "aggressor_flag": {
            "present": flag_present, "absent": flag_absent,
            "agrees_with_lane": agree, "disagrees_with_lane": disagree,
            "verdict": verdict},
        # FILLS WHOSE STAGE COULD NOT BE READ AT ALL, because the
        # receipt carries no placement list to have been absent from.
        # They are not maker fills and they are not taker fills; they
        # are the count that says how much of the maker side of this
        # reading is vouchable.
        "n_fills_stage_unknown": stage_unknown,
        "maker_side_is_vouchable": (
            "a fill counted 'maker' appears in the terminal read of a "
            "receipt that DOES carry a placement list, so 'it was not "
            "there at create' is a reading and not an absence; "
            f"{stage_unknown} fill(s) had no placement list to read and "
            "are counted stage_unknown, never maker"),
        "fills": fills,
        "min_n_for_a_rate": MIN_N_FOR_A_LANE_RATE,
        "fee_basis": "entry leg only; the exit leg's receipt is not "
                     "stored on the row, so every figure here is a "
                     "LOWER BOUND on the round-trip fee",
        "adverse_selection": "unmeasured — a commission reading cannot "
                             "see it. A rest fills when the other side "
                             "comes to it, which is when the market is "
                             "moving against the rest. The read that "
                             "settles it is the post-fill price path on "
                             "our own maker fills (analytics/"
                             "price_path.py), clustered per book, at "
                             "n >= 30.",
    }


def capture_from_rows(rows: list[dict]) -> dict:
    """The outcome-free capture term from paired copy rows.

    Each usable row carries the whale's own entry price (his_price)
    and ours (fill_price) for the same fill: drag_i = shares x
    (ours - his). The weighted mean drag rate and its SE come from
    the entry-notional-weighted sample.

    FEES ARE CHARGED PER ROW BY `row_fee`, which reads the venue's own
    commission out of the stored receipt and falls back to the venue's
    fee schedule — never to zero. Until this change the fee term was
    `kalshi_fee(...) if venue.startswith("kalshi") else 0.0`, so the
    PM-US lane, which is where every copy goes and which is 100%
    taker, contributed a literal zero: the sleeve edge this meter
    published was gross by exactly the taker fee.

    THE FEE RATE'S DENOMINATOR IS THE NOTIONAL ACTUALLY CHARGED. The
    numerator can only hold the rows the reader could price; dividing
    it by the WHOLE cohort's entry notional charges every unmeasured
    row a silent ZERO inside a figure served as net — the same defect
    this unit exists to remove, one layer up, and it moves the sign of
    the owner-facing answer: a cohort half of which is unpriceable
    served fee_rate 0.015 where the priced rows' own rate is 0.030, and
    +2.07% - 0.015 is POSITIVE where +2.07% - 0.030 is NEGATIVE. When
    NO row could be priced the rate is None and `combine` refuses to
    publish a net sleeve edge at all, rather than republishing HEAD's
    zero-fee answer under a 'net' label.

    A row charged only in PART (some legs unreadable) is counted in the
    DOLLARS but kept out of the RATE's denominator: its dollars are a
    lower bound worth keeping, its rate is not a rate.
    """
    from ..live_executor import cost_per_share

    usable = []
    fees: list[dict] = []
    for r in rows:
        his = r.get("his_price")
        raw_fill = r.get("fill_price")
        # THE ENTRY, NOT THE REMAINDER. `shares` is live_orders'
        # filled_shares, which every partial exit decrements, so a
        # trimmed row's drag, entry notional and fee were all computed
        # on what was LEFT. orig_shares (migration 040) is the count the
        # entry actually happened at; rows without it are unchanged.
        sh = entry_shares(r)
        if his is None or raw_fill is None or not sh or sh <= 0:
            continue
        if his <= 0 or raw_fill <= 0:
            continue
        # DENOMINATION (first-print finding #2, the PRICEFID short
        # bug's family): fill_price names the venue's LONG leg. On a
        # BUY_SHORT copy of his 0.10 underdog entry the row reads
        # ~0.90, and raw (ours - his) fabricated ~80c/share of drag
        # on our BEST fills — the meter's first print showed 33.6%
        # drag built almost entirely from this. cost_per_share owns
        # the long/short conversion; his_price is already the price
        # of the leg he took.
        ours = cost_per_share(float(raw_fill), r.get("intent"))
        if ours <= 0:
            continue
        # SELL sign (first-print finding 2026-08-28): for a mirror
        # exit, paying LESS than his exit price is the drag — the
        # per-share cost vs his execution is (his - ours), the mirror
        # of the BUY case. The unsigned version scored a good exit as
        # drag and a bad one as edge.
        sell = str(r.get("side") or "").upper().startswith("S")
        usable.append((float(his), float(ours), float(sh),
                       str(r.get("venue") or ""),
                       str(r.get("whale") or ""), sell,
                       str(r.get("slug") or ""),
                       str(r.get("intent") or "")))
        fees.append(safe_row_fee(r))
    n = len(usable)
    if n == 0:
        return {"n": 0, "drag_rate": None, "drag_se": None,
                "fee_rate": None, "per_whale_entry": {}}

    def _drag(h: float, o: float, sell: bool) -> float:
        return (h - o) if sell else (o - h)

    tot_his = sum(h * s for h, o, s, v, w, sl, g, it in usable)
    drag_usd = sum(_drag(h, o, sl) * s
                   for h, o, s, v, w, sl, g, it in usable)
    fee_usd = sum(f["fee_usd"] for f in fees if f["fee_usd"] is not None)
    # A SUM OVER NOTHING IS NOT $0.00 OF FEE. `round(sum([]), 2)` is the
    # literal zero this unit exists to delete, arriving by the one door
    # left open: on an all-unmeasured cohort the rate and the net were
    # correctly withheld while `fee_usd` was served as 0 — and the
    # probe line printed `fee=$0` beside `rate=unreadable`, because
    # jq's `//` falls through on null and not on zero. The sibling
    # surface already applies this rule (`finish_net` serves fee_usd
    # None on an unreadable bucket); this is the same rule here.
    measured_rows = sum(1 for f in fees if f["fee_usd"] is not None)
    fee_sources: dict[str, int] = {}
    for f in fees:
        fee_sources[f["fee_source"]] = fee_sources.get(f["fee_source"], 0) + 1
    unmeasured_rows = fee_sources.get(FEE_UNMEASURED, 0)
    partial_rows = sum(1 for f in fees if f.get("fee_is_partial"))
    # The rate's own base: the entry notional of the rows whose fee was
    # read IN FULL, at his prices (the unit every other term in the
    # sleeve edge is denominated in) and at ours (what we actually put
    # up, which differs several-fold on a BUY_SHORT copy of a long-shot
    # entry, where his 0.10 is our 0.90).
    charged_his = 0.0
    charged_ours = 0.0
    charged_fee = 0.0
    for (h, o, s, v, w, sl, g, it), f in zip(usable, fees):
        if f["fee_usd"] is not None and not f.get("fee_is_partial"):
            charged_his += h * s
            charged_ours += o * s
            charged_fee += f["fee_usd"]
    drag_rate = drag_usd / tot_his
    fee_rate = (charged_fee / charged_his) if charged_his > 0 else None
    fee_rate_ours = (charged_fee / charged_ours) if charged_ours > 0 else None
    # weighted SE of the drag rate: w_i = his-notional share
    var = 0.0
    for h, o, s, v, w, sl, g, it in usable:
        wgt = (h * s) / tot_his
        r_i = _drag(h, o, sl) / h
        var += (wgt ** 2) * ((r_i - drag_rate) ** 2)
    per_whale: dict[str, float] = {}
    for h, o, s, v, w, sl, g, it in usable:
        per_whale[w] = per_whale.get(w, 0.0) + h * s
    # DIAGNOSTIC (first live print showed drag 33.6% — implausible
    # for a FOK-at-his+2% fresh lane): name the rows that carry the
    # drag so the next probe shows whether it is real execution cost
    # or a pairing artifact (side-token mismatch, sweep-lane entries,
    # stale his_price). Top by absolute drag dollars.
    worst = sorted(usable, key=lambda u: -abs(_drag(u[0], u[1], u[5])
                                              * u[2]))[:8]
    worst_rows = [{
        "slug": g[:60], "whale": w, "venue": v,
        "side": "SELL" if sl else "BUY", "intent": it[-18:],
        "his": round(h, 4), "ours": round(o, 4),
        "shares": round(s, 2),
        "drag_usd": round(_drag(h, o, sl) * s, 2),
    } for h, o, s, v, w, sl, g, it in worst]
    return {
        "n": n,
        "entry_notional": round(tot_his, 2),
        "drag_usd": round(drag_usd, 2),
        "drag_rate": drag_rate,
        "drag_se": math.sqrt(var),
        "fee_usd": (round(fee_usd, 2) if measured_rows else None),
        "fee_rate": fee_rate,
        "fee_rate_per_our_dollar": fee_rate_ours,
        # THE RATE'S OWN DENOMINATOR, SERVED BESIDE IT.
        "fee_charged_usd": (round(charged_fee, 2)
                            if charged_his > 0 else None),
        "fee_charged_notional": round(charged_his, 2),
        "fee_rows_measured": measured_rows,
        "fee_notional_share": (round(charged_his / tot_his, 4)
                               if tot_his else None),
        "fee_rate_basis": (
            "the fee of the rows priced IN FULL over those same rows' "
            "entry notional at his prices — never over the whole "
            "cohort's, which charges an unpriceable row a silent zero "
            "inside a figure served as net"
            if fee_rate is not None else
            "unreadable — no row in this cohort could be priced in "
            "full, so there is no rate and no net sleeve edge"),
        # WHICH ROWS THE VENUE PRICED AND WHICH THE SCHEDULE DID.
        "fee_sources": fee_sources,
        "fee_rows_unmeasured": unmeasured_rows,
        "fee_rows_partial": partial_rows,
        "fee_rate_is_lower_bound": True,
        "fee_basis": ("entry leg only, and the schedule's form is "
                      "unverified against a venue commission value; "
                      "the exit leg's receipt is not stored on the row"
                      + (f"; {unmeasured_rows} row(s) carry no "
                         f"measurable fee and are charged nothing"
                         if unmeasured_rows else "")
                      + (f"; {partial_rows} row(s) carry a PARTIAL fee "
                         f"(some legs unreadable) and are counted in "
                         f"the dollars but not in the rate"
                         if partial_rows else "")),
        "per_whale_entry": per_whale,
        "worst_rows": worst_rows,
    }


def combine(capture: dict, benchmark: dict) -> dict:
    """Mix-weighted whale edge minus drag minus fees, with a CI.

    The whale mix is OUR entry-notional mix over the cohort — the
    edge we are actually buying. A rostered whale without a published
    CI contributes edge 0 with se 0 (conservative: it dilutes the
    estimate rather than inflating it) and is named in the payload.
    """
    per_entry = capture.get("per_whale_entry") or {}
    total = sum(per_entry.values())
    pw = (benchmark or {}).get("per_whale") or {}
    if not total or capture.get("drag_rate") is None:
        return {"available": False,
                "reason": "no paired fills in the cohort yet"}
    # FAIL CLOSED ON THE FEE. `sleeve_edge` is labelled "net of the
    # entry-leg fee" and p_edge_positive and the whole thesis grid ride
    # on it. With no measurable fee anywhere in the cohort, subtracting
    # 0.0 republishes the pre-fee answer under a net label — byte
    # identical to the number this unit exists to correct. There is no
    # net edge to publish, so none is published.
    if capture.get("fee_rate") is None:
        return {"available": False,
                "reason": ("the entry fee is unmeasured on every row in "
                           "this cohort, so no net sleeve edge can be "
                           "published; the pre-fee terms are in "
                           "`capture`"),
                "fee_rate": None,
                "fee_rate_basis": capture.get("fee_rate_basis"),
                "fee_rows_unmeasured": capture.get("fee_rows_unmeasured", 0),
                "fee_sources": capture.get("fee_sources") or {}}
    # AND FAIL CLOSED ON THE FEE'S COVERAGE. The rate is honest about
    # its own rows; `sleeve_edge` then subtracts it from the WHOLE
    # cohort's edge, which extrapolates one row's rate to every row.
    # A net edge published off 1% of the cohort's money is a number the
    # owner will read as the case for the sleeve, and the coverage
    # share sitting three keys away does not make it one. The lane rate
    # already refuses below its minimum n; this is the same rule on the
    # figure that actually decides things.
    share = capture.get("fee_notional_share")
    if share is None or share < MIN_FEE_NOTIONAL_SHARE:
        return {"available": False,
                "reason": (f"the entry fee could be priced in full on "
                           f"rows worth {share if share is not None else 0} "
                           f"of this cohort's entry notional, below the "
                           f"{MIN_FEE_NOTIONAL_SHARE} minimum coverage a "
                           f"net sleeve edge is published off; subtracting "
                           f"that rate from the whole cohort would "
                           f"extrapolate it to rows it was never measured "
                           f"on. The pre-fee terms and the measured rate "
                           f"are in `capture`"),
                "fee_rate": capture.get("fee_rate"),
                "fee_rate_basis": capture.get("fee_rate_basis"),
                "fee_notional_share": share,
                "min_fee_notional_share": MIN_FEE_NOTIONAL_SHARE,
                "fee_rows_unmeasured": capture.get("fee_rows_unmeasured", 0),
                "fee_sources": capture.get("fee_sources") or {}}
    mix_edge = 0.0
    mix_var = 0.0
    mix = {}
    unpublished = []
    for whale, ent in per_entry.items():
        w = ent / total
        g = None
        for k, v in pw.items():
            if k.lower() == whale.lower():
                g = v
                break
        edge = float((g or {}).get("edge_roi") or 0.0)
        ci = (g or {}).get("edge_ci95") or None
        if ci and isinstance(ci, (list, tuple)) and len(ci) == 2 \
                and ci[0] is not None and ci[1] is not None:
            se = (float(ci[1]) - float(ci[0])) / (2 * 1.96)
        else:
            edge, se = 0.0, 0.0
            unpublished.append(whale)
        mix[whale] = {"weight": round(w, 4), "edge": edge, "se": se}
        mix_edge += w * edge
        mix_var += (w * se) ** 2
    e_hat = mix_edge - capture["drag_rate"] - capture["fee_rate"]
    se = math.sqrt(mix_var + capture["drag_se"] ** 2)
    p_pos = _phi(e_hat / se) if se > 0 else (1.0 if e_hat > 0 else 0.0)
    return {
        "available": True,
        "whale_mix_edge": mix_edge,
        "whale_mix_se": math.sqrt(mix_var),
        "drag_rate": capture["drag_rate"],
        "drag_se": capture["drag_se"],
        "fee_rate": capture["fee_rate"],
        "sleeve_edge": e_hat,
        "sleeve_se": se,
        "p_edge_positive": p_pos,
        "mix": mix,
        "unpublished_whales": unpublished,
        # LABELLED WHERE IT IS SERVED (this unit, clause 3): the whale
        # mix edge is measured on THEIR books and is gross of whatever
        # they pay; the sleeve edge is net of OUR ENTRY fee only.
        "whale_mix_edge_basis": "pre_fee (their books, their fees unread)",
        "sleeve_edge_basis": ("net of the entry-leg fee; the exit leg's "
                              "commission is unmeasured, so this is an "
                              "UPPER bound on the sleeve edge"),
        "fee_rate_basis": capture.get("fee_rate_basis"),
        "fee_notional_share": capture.get("fee_notional_share"),
        "min_fee_notional_share": MIN_FEE_NOTIONAL_SHARE,
        "fee_rows_unmeasured": capture.get("fee_rows_unmeasured", 0),
        "fee_rows_partial": capture.get("fee_rows_partial", 0),
        "fee_sources": capture.get("fee_sources") or {},
    }


def thesis(edge: dict, flow_per_day: float) -> dict:
    """P(annual profit >= 100% of principal) at MEASURED flow.

    Honesty note baked into the shape: dollar flow through the books
    is set by whale activity and book depth, NOT by our principal —
    so annual profit = flow x 365 x edge regardless of principal
    until flow itself is scaled (more proven whales, more sports,
    deeper markets). The grid therefore shows, for each principal,
    the probability at today's flow AND at flow multiples — the
    multiple IS the build-out requirement.
    """
    if not edge.get("available") or flow_per_day <= 0:
        return {"available": False}
    e, se = edge["sleeve_edge"], edge["sleeve_se"]
    out = []
    for principal in PRINCIPAL_GRID:
        row: dict[str, Any] = {"principal": principal}
        for m in FLOW_MULTIPLES:
            annual = flow_per_day * 365.0 * m
            mu = annual * e
            sd = annual * se
            need = TARGET_ANNUAL * principal
            p = _phi((mu - need) / sd) if sd > 0 else (
                1.0 if mu >= need else 0.0)
            row[f"p_100pct_at_{m}x_flow"] = round(p, 4)
        row["flow_x_for_p50"] = (
            round(TARGET_ANNUAL * principal / (flow_per_day * 365.0 * e), 1)
            if e > 0 else None)
        out.append(row)
    return {
        "available": True,
        "measured_flow_per_day": round(flow_per_day, 2),
        "target_annual_return": TARGET_ANNUAL,
        "grid": out,
    }


# THE RECEIPT CENSUS. Keyed by explicit row ids (so it is bounded by
# the caller's own cap), and it lifts ONLY the four JSON paths the fee
# and economics readings use — never the whole `raw` blob, which holds
# the venue's complete API response for every order.
#
# THE COMMISSION READ IS NOT COUPLED TO THE COLUMNS IT DOES NOT NEED.
# `lane` is migration 041's and `orig_shares` is 040's; on a database
# missing either, a single statement naming them fails and the WHOLE
# census is contained to None — which would stop the venue's own stated
# commission being read on EVERY row, to buy a maker/taker split and a
# charge base. The optional columns are therefore in a second statement
# that is tried first and dropped on any error, and the commission read
# stands on migration 007's columns alone.
_RECEIPT_SELECT = """
SELECT lo.id                                       AS id,
       __LANE__,
       __ORIG__,
       lo.venue                                    AS venue,
       lo.filled_shares::float8                    AS filled_shares,
       lo.fill_price::float8                       AS fill_price,
       lo.pnl::float8                              AS pnl,
       COALESCE(NULLIF(lo.filled_usd, 0),
                lo.requested_usd)::float8          AS stake,
       jsonb_build_object(
           'response_execs',
               CASE WHEN jsonb_typeof(lo.raw #> '{response,executions}')
                         = 'array'
                    THEN lo.raw #> '{response,executions}'
                    ELSE NULL END,
           'final_execs',
               CASE WHEN jsonb_typeof(lo.raw #> '{final,executions}')
                         = 'array'
                    THEN lo.raw #> '{final,executions}'
                    ELSE NULL END,
           'executions',
               CASE WHEN jsonb_typeof(lo.raw -> 'executions') = 'array'
                    THEN lo.raw -> 'executions'
                    ELSE NULL END,
           'final_commission_usd', lo.raw #> '{final,commission_usd}'
       )                                           AS receipt
FROM live_orders lo
WHERE lo.id = ANY($1::bigint[])
"""

# What the census asks for when 040 and 041 are both applied…
RECEIPT_SQL = (
    _RECEIPT_SELECT
    .replace("__LANE__", "lo.lane                              AS lane")
    .replace("__ORIG__", "COALESCE(lo.orig_shares,\n"
                         "                lo.filled_shares)::float8"
                         "    AS orig_shares"))
# …and what it falls back to when either column is absent: the same
# four JSON paths, the same bound, no lane and no charge base.
RECEIPT_SQL_CORE = (
    _RECEIPT_SELECT
    .replace("__LANE__", "NULL::text                           AS lane")
    .replace("__ORIG__",
             "NULL::float8                        AS orig_shares"))


async def receipts_by_id(pool: Any, ids: list, *,
                         cap: int = MAX_FEE_CENSUS_ROWS,
                         timeout: float = FEE_CENSUS_TIMEOUT_S
                         ) -> dict | None:
    """{row id -> receipt row} for the given ids, or None when the
    census cannot be taken.

    BOUNDED, TIMED, AND CONTAINED. Over the cap it refuses BEFORE the
    query (a truncated census is not a reading of the cohort, and a
    partial one would under-charge the fee). On a timeout or any
    database fault it returns None and the caller serves the figure as
    PRE-FEE — a refusal that costs a label, never the page.

    THE WHOLE BUDGET IS ONE TIMEOUT. The retry without the optional
    columns runs only when the first statement failed, and both attempts
    share the same deadline, so a database missing migration 041 cannot
    turn one bounded read into two.

    EVERY ROW SAYS WHICH STATEMENT ANSWERED IT (`lane_column_read`).
    The fallback selects `lane` as NULL, and a NULL lane is the IOC
    lane — so without this flag a database missing migration 041
    publishes every fill as an `ioc` TAKER fill, with the census
    reporting "read" and nothing anywhere saying the column was not
    there. An absent column is not evidence of a lane.
    """
    import asyncio

    ids = [i for i in ids if i is not None]
    if not ids:
        return {}                      # nothing asked is a complete answer
    if len(ids) > cap:
        return None                    # over the bound: unreadable

    async def _both() -> tuple[list, bool]:
        try:
            return await pool.fetch(RECEIPT_SQL, ids), True
        except Exception:  # noqa: BLE001 — 040/041 absent: read the core
            return await pool.fetch(RECEIPT_SQL_CORE, ids), False

    try:
        rows, lane_read = await asyncio.wait_for(_both(), timeout=timeout)
    except Exception:  # noqa: BLE001 — contained: no fee, not no page
        return None
    out = {}
    for r in rows:
        d = dict(r)
        d["lane_column_read"] = lane_read
        out[d["id"]] = d
    return out


async def proof2_payload(pool: Any, since: str | None = None) -> dict:
    """Assemble the full PROOF-2 payload from live tables."""
    from .copies_record import COPY_WHALES
    from .copy_reports import LEDGER_SQL

    since_dt = datetime.fromisoformat(since or DEFAULT_SINCE)
    if since_dt.tzinfo is None:
        since_dt = since_dt.replace(tzinfo=timezone.utc)
    rows = [dict(r) for r in await pool.fetch(LEDGER_SQL,
                                              list(COPY_WHALES))]
    cohort = []
    for r in rows:
        at = r.get("placed_at")
        if at is None:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at >= since_dt:
            cohort.append(r)
    # THE RECEIPTS, so the venue's own commission is charged where it
    # exists. Unreadable is contained: the cohort keeps its drag term
    # and the fee falls to the schedule off each row's own fill.
    receipts = await receipts_by_id(pool, [r.get("id") for r in cohort])
    if receipts:
        # `orig_shares` rides in from the census too: LEDGER_SQL's
        # `shares` is filled_shares, which every partial exit
        # decrements, so without it a trimmed row's entry is charged on
        # what is LEFT. A census that could not read it leaves the key
        # absent and `entry_shares` falls back exactly as before.
        cohort = [{**r,
                   "receipt": (receipts.get(r.get("id")) or {}).get("receipt"),
                   "lane": (receipts.get(r.get("id")) or {}).get("lane"),
                   # …and so does the flag that says whether `lane` was
                   # a reading or an absent column, without which a
                   # database missing 041 publishes every fill as taker.
                   "lane_column_read": (receipts.get(r.get("id")) or {}).get(
                       "lane_column_read", True),
                   "orig_shares": (receipts.get(r.get("id")) or {}).get(
                       "orig_shares")}
                  for r in cohort]
    capture = capture_from_rows(cohort)
    economics = fill_economics(cohort)
    economics["receipt_census"] = ("read" if receipts is not None else
                                   "unreadable — fees fell back to the "
                                   "schedule on every row and the "
                                   "maker/taker split could not be read")
    raw = await pool.fetchval(
        "SELECT value FROM ingestion_state WHERE key = $1",
        "whale_edge_benchmark")
    benchmark = raw if isinstance(raw, dict) else (
        json.loads(raw) if raw else {})
    edge = combine(capture, benchmark)
    # measured flow: entry notional per day across the cohort span
    days = max(1.0, (datetime.now(timezone.utc) - since_dt).total_seconds()
               / 86_400.0)
    flow = (capture.get("entry_notional") or 0.0) / days
    return {
        "since": since_dt.isoformat(timespec="seconds"),
        "cohort_days": round(days, 2),
        "capture": {k: (round(v, 6) if isinstance(v, float) else v)
                    for k, v in capture.items()
                    if k != "per_whale_entry"},
        "edge": {k: (round(v, 6) if isinstance(v, float) else v)
                 for k, v in edge.items()},
        "thesis": thesis(edge, flow),
        "benchmark_measured_at": (benchmark or {}).get("measured_at"),
        # WHAT MAKING A MARKET ACTUALLY PAYS US, on our own fills.
        "economics": economics,
        # THE LABELS, SERVED (this unit, clause 3). Anything not named
        # here as net IS GROSS; nothing in this payload may be read as
        # net that is not.
        "basis": {
            "sleeve_edge": "net of the entry-leg fee only",
            "whale_mix_edge": "pre_fee",
            "drag_rate": "fee-free by construction (a price difference)",
            "fee_schedule_form": FEE_SCHEDULE_FORM,
            "pmus_fee_coefficient": PMUS_FEE_COEFFICIENT,
            "pmus_fee_form_verified": False,
            "settles_the_form": ("one PM-US fill whose stored receipt "
                                 "carries a commission value (V1 rung 10); "
                                 "until then every PM-US fee here is an "
                                 "ESTIMATE at the venue's own coefficient"),
        },
    }

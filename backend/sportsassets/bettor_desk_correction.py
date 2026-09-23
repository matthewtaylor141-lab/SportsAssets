"""THE FEE CORRECTION for the live shadow account. Bounded, not invented.

WHAT WENT WRONG, stated correctly this time.

The live loop was started as `run(_desk_pool)` with `fee_fn` defaulting
to None, and `Desk.__init__` turned that into a silent zero-fee lambda.
That EXPLICIT CODE PATH is the evidence, and it is the only evidence.

THE EARLIER DIAGNOSIS OVERREACHED AND IS WITHDRAWN. I wrote that
realized P&L of exactly $0.00 after 334 fills was "arithmetically
impossible" under a rebate schedule. It is not proof:

  * A fill's fee is `bankers_cents(theta * C * p * (1-p))` -- rounded
    to the cent PER FILL. `min_contracts_for_a_cent` shows a fill needs
    ~2 contracts at p=0.50 and ~41 at p=0.01 before its rebate survives
    rounding at all. A book of small fills can genuinely total zero.
  * Had entry fees been capitalised into cost basis rather than
    expensed, realized would sit at zero by construction until a leg
    closed, whatever the schedule.

Neither happens to be the case here -- `Portfolio.buy` expenses the fee
to `realized`, and the fills were large enough -- but I did not check
either before calling it impossible. The zero was a reason to LOOK. The
`fee_fn=None` path is the reason to CONCLUDE.

────────────────────────────────────────────────────────────────────
WHY THIS CORRECTION IS AN INTERVAL AND NOT A NUMBER.

`bettor_desk_fills` was never written. `_persist` inserts decisions,
state, orders, positions and ledger rows, and nothing else, so the
per-fill quantity, price, liquidity role and timestamp do not exist for
any fill the live lane has simulated. What survives is the ORDER
aggregate: `filled_qty`, `avg_fill_price`, `fees_usd`.

That is not enough for an exact recomputation, for two independent
reasons, and both push the same way:

  1. ROUNDING IS PER FILL. The true fee is the SUM of independently
     rounded per-fill amounts. From an average you can compute one
     rounded amount for the aggregate, which is a different quantity.
     The number of fills per order was not recorded, so the rounding
     discrepancy is not even boundable -- except at zero, below.

  2. p(1-p) IS CONCAVE. Sum(q_i * p_i(1-p_i)) <= Q * p_bar(1-p_bar)
     whenever the p_i differ. theta_maker is NEGATIVE, so the
     aggregate computation OVERSTATES the magnitude of the rebate.

Both give a one-sided bound in the same direction, so the interval is:

    LOWER  0.00        every fill's rebate could have rounded away
    UPPER  |aggregate| the concavity bound, itself rounded once

and the true correction lies inside it. The lower bound is not a
formality: at the prices and clip sizes this desk trades, whole orders
rounding to zero is an ordinary outcome, not a corner case.

A correction reported as its upper bound would be a number that
flatters the book, which is the same class of error as the leak this
codebase already caught once.

────────────────────────────────────────────────────────────────────
AND THE ACCOUNT WAS RESET, NOT ONLY MISPRICED.

`run()` constructs a fresh `Desk` on every start and restores only the
CURSOR. Cash, legs, orders and the consumption ledger were never read
back, so each process start began the book again at `starting_cash`
with no positions. A realized figure spanning a restart is therefore
not a running total.

Worse for auditing it after the fact: `boot_id` was written as
`str(int(time.time()))` on every row, so it identifies a write rather
than a boot and the epochs are NOT recoverable from the record. That
is reported as an incompleteness, not guessed at.

NOTHING HERE WRITES TO THE ORIGINAL TABLES. It reads them and writes
correction rows beside them.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from . import bettor_fee_schedule as FEES

VERSION = "DESK_FEE_CORRECTION_V1"

EXACT = "EXACT"
BOUNDED = "BOUNDED"
INCOMPLETE = "INCOMPLETE"

COMPLETE = "COMPLETE"

# The maker path is the only one the print-through model can produce: a
# resting order that an observed execution passes through is, by
# construction, the passive side. The exit path calls fee_fn(maker=False).
# `intent` is what distinguishes them in the surviving record.
EXIT_INTENTS = ("EXIT", "REDUCE", "LIQUIDATE")


def _d(x) -> Decimal:
    return Decimal(str(x if x is not None else 0))


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def schedule_for(ts):
    """The schedule in force at that instant, or NOTHING.

    `FEES.for_date` deliberately has no default, because "a fee is a
    fact about WHEN it was charged, and a caller that cannot name the
    date is a caller that would silently apply 0.0695 to a July fill".

    My first version wrapped it in a bare except and fell back to
    `FEES.LATEST`, which is precisely the silent default it refuses to
    provide -- it would have applied today's schedule to an undated
    fill and called the result a correction. A date that cannot be
    read returns None here, and the caller marks the order INCOMPLETE.
    """
    if ts is None:
        return None
    iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    try:
        return FEES.for_date(iso)
    except (ValueError, TypeError):
        return None


def order_correction(row) -> dict:
    """One order's fee correction, as an interval.

    `row` carries the surviving aggregate. Every field it needs is
    named here so a missing one is a refusal rather than a zero.
    """
    oid = row["order_id"]
    qty = row.get("filled_qty")
    px = row.get("avg_fill_price")
    original = row.get("fees_usd")
    intent = (row.get("intent") or "").upper()
    ts = row.get("terminal_at") or row.get("placed_at")

    missing = [k for k, v in (("filled_qty", qty), ("avg_fill_price", px),
                              ("fees_usd", original)) if v is None]
    if float(qty or 0) <= 0:
        return {"subject_id": oid, "status": INCOMPLETE,
                "reason": "NO_FILLED_QUANTITY: nothing was filled on this "
                          "order, so there is no fee to correct",
                "schedule_id": None,
                "original_fees_usd": float(original or 0.0),
                "delta_fees_lower_usd": 0.0, "delta_fees_upper_usd": 0.0,
                "detail": {"filled_qty": 0.0}}
    if missing:
        return {"subject_id": oid, "status": INCOMPLETE,
                "reason": "MISSING_FIELDS: %s. Nothing is asserted about "
                          "this order's fee." % ", ".join(missing),
                "schedule_id": None,
                "original_fees_usd": None,
                "delta_fees_lower_usd": None, "delta_fees_upper_usd": None,
                "detail": {"missing": missing}}

    sched = schedule_for(ts)
    if sched is None:
        return {"subject_id": oid, "status": INCOMPLETE,
                "reason": "NO_TIMESTAMP: neither terminal_at nor placed_at "
                          "is present, so no schedule can be selected "
                          "without guessing which was in force.",
                "schedule_id": None,
                "original_fees_usd": float(original),
                "delta_fees_lower_usd": None, "delta_fees_upper_usd": None,
                "detail": {"missing": ["terminal_at", "placed_at"]}}

    maker = intent not in EXIT_INTENTS
    agg = sched.fill_fee(_d(qty), _d(px), maker=maker)
    orig = _d(original)

    # THE INTERVAL. `agg` is the concavity bound on the aggregate; zero
    # is the all-rounded-away bound. For a maker REBATE agg is negative,
    # so the interval on the fee runs [agg, 0]; for a taker CHARGE it is
    # positive and runs [0, agg]. Either way the delta against the
    # recorded fee is bounded by the two endpoints, ordered.
    lo_fee, hi_fee = (agg, Decimal(0)) if agg < 0 else (Decimal(0), agg)
    d_lo, d_hi = lo_fee - orig, hi_fee - orig
    if d_lo > d_hi:
        d_lo, d_hi = d_hi, d_lo

    exact = (d_lo == d_hi)
    return {
        "subject_id": oid,
        # It can only be EXACT when the interval collapses, which here
        # means the correction is nil. A non-nil correction computed
        # from an average is BOUNDED by construction.
        "status": EXACT if exact else BOUNDED,
        "reason": ("NO_CHANGE: the recorded fee already sits at the only "
                   "value the surviving record admits"
                   if exact else
                   "BOUNDED_FROM_ORDER_AGGREGATE: per-fill records were "
                   "never written, and PMUS rounds per fill, so the "
                   "correction is an interval. Lower bound assumes every "
                   "fill's amount rounded to zero; upper bound is the "
                   "concavity bound from the order's average price."),
        "schedule_id": sched.schedule_id,
        "original_fees_usd": float(orig),
        "delta_fees_lower_usd": float(d_lo),
        "delta_fees_upper_usd": float(d_hi),
        # d(cash)/d(fee) == -1 and d(realized)/d(fee) == -1 in BOTH
        # `Portfolio.buy` and `Portfolio.sell`, so a fee correction
        # moves cash and realized together and by the same amount, and
        # LEAVES BASIS ALONE. That is why the identity survives it --
        # see `reconcile()`, which checks rather than assumes.
        "delta_cash_lower_usd": float(-d_hi),
        "delta_cash_upper_usd": float(-d_lo),
        "delta_realized_lower_usd": float(-d_hi),
        "delta_realized_upper_usd": float(-d_lo),
        "delta_basis_usd": 0.0,
        "detail": {
            "filled_qty": float(qty), "avg_fill_price": float(px),
            "liquidity_assumed": "MAKER" if maker else "TAKER",
            "liquidity_source": (
                "INFERRED FROM `intent`, not recorded. The per-fill "
                "`liquidity` column exists in bettor_desk_fills and that "
                "table was never written."),
            "fill_count": "NOT_IDENTIFIED",
            "aggregate_bound_usd": float(agg),
        },
    }


def reconcile(totals: dict) -> dict:
    """The identity must survive the correction, at BOTH endpoints.

    A fee correction moves cash and realized by the same signed amount
    and leaves basis untouched, so

        (cash + basis - realized) is unchanged

    should hold at the lower bound and at the upper bound. This is
    asserted as a check rather than as a comment, because the last two
    accounting defects in this system were both things a comment
    claimed and nothing verified.
    """
    out = {}
    ok = True
    for end in ("lower", "upper"):
        drift = (totals["delta_cash_%s_usd" % end]
                 + totals["delta_basis_usd"]
                 - totals["delta_realized_%s_usd" % end])
        out[end] = round(drift, 6)
        if abs(drift) > 0.005:
            ok = False
    return {"ok": ok, "identity_drift": out,
            "identity": "delta_cash + delta_basis - delta_realized == 0",
            "proves": "that the correction itself is internally "
                      "consistent",
            "does_not_prove": [
                "that the corrected figure is right -- it is an "
                "interval, and the interval's width is the honest part",
                "that the account is continuous across restarts; it is "
                "not, and no arithmetic here repairs that",
            ]}


def summarise(rows: list, *, epochs_identifiable: bool) -> dict:
    """Aggregate the per-order corrections and say what is missing."""
    tot = {k: 0.0 for k in (
        "delta_fees_lower_usd", "delta_fees_upper_usd",
        "delta_cash_lower_usd", "delta_cash_upper_usd",
        "delta_realized_lower_usd", "delta_realized_upper_usd",
        "delta_basis_usd", "original_fees_usd")}
    n = {EXACT: 0, BOUNDED: 0, INCOMPLETE: 0}
    for r in rows:
        n[r["status"]] = n.get(r["status"], 0) + 1
        for k in tot:
            v = r.get(k)
            if v is not None:
                tot[k] += float(v)
    for k in tot:
        tot[k] = round(tot[k], 2)

    reasons = []
    if n[BOUNDED]:
        reasons.append(
            "PER_FILL_RECORDS_ABSENT: bettor_desk_fills was never "
            "written, so %d orders can only be corrected to an interval. "
            "PMUS rounds per fill and the fill count is NOT_IDENTIFIED."
            % n[BOUNDED])
    if n[INCOMPLETE]:
        reasons.append(
            "REQUIRED_FIELDS_MISSING: %d orders carry no computable "
            "correction and none is asserted for them." % n[INCOMPLETE])
    if not epochs_identifiable:
        reasons.append(
            "EPOCHS_NOT_IDENTIFIABLE: boot_id was written as a per-row "
            "timestamp, so restarts cannot be located in the historical "
            "ledger. The live loop also restored only the cursor and "
            "never the book, so figures spanning a restart are not "
            "running totals. Neither is repaired by this correction.")

    status = COMPLETE if not reasons else INCOMPLETE
    return {"totals": tot, "counts": n,
            "accounting_status": status,
            "incomplete_reasons": reasons,
            "reconcile": reconcile(tot)}

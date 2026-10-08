"""KALSHI FEES -- THE PUBLISHED SCHEDULE x THE SERIES / EVENT MULTIPLIER,
VERSIONED (Kalshi rep production contract 2026-10-07; Red Team fee guard).

Kalshi confirmed: the published fee schedule applies, with series-specific
fee multipliers; there are no individual fee arrangements and no
account-specific volume-tier discounts; separate published incentive
programs may exist. So:

  terms      the fee in force for a market at a time: the latest EVENT
             override (GET /events/fee_changes) scheduled at or before then,
             else the latest SERIES change (GET /series/fee_changes) at or
             before then, else the series' own current terms (GET /series)
             as first observed by BETTOR. Each carries its change id, its
             fee_type and multiplier (the version) and its effective time.
  taker fee  the General Trading Fees Table, quadratic:
               model = multiplier x 0.07 x C x P x (1 - P)
             rounded per Kalshi's Fee Rounding page: trade fee = ceil to
             $0.000001; the cash debit is aligned to the balance precision
             ($0.01 for a non-direct member -- the conservative case), so
             the all-in cost is ceil_cent(P x C + trade fee).
  unknown    no terms, a fee type this module does not price ('flat' -- the
             Specific Trading Fees Table), a future effective time, or a
             multiplier that is not a number: the route is INELIGIBLE (the
             caller gets no fee function). Never zero, never a discount.
  maker      not priced: BETTOR takes on Kalshi; a maker fee is never
             assumed zero (fee_guard maker_known = False).
  incentives excluded: incentive-program economics enter only when their
             terms are captured and applicable (none are, in this build).

Pure: no network, no database.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal

VERSION = "KALSHI_PUBLISHED_FEES_V1"
#: the General Trading Fees Table coefficient (kalshi_orders.fee_for and the
#: canonical package's kalshi_taker_fee carry the same 0.07)
TAKER_COEFFICIENT = Decimal("0.07")
PRICED_TYPES = ("quadratic", "quadratic_with_maker_fees",
                "quadratic_with_combo_maker_fees")
NON_DIRECT_BALANCE_PRECISION = Decimal("0.01")
ACCOUNT_DISCOUNTS = "NONE (Kalshi rep 2026-10-07: no individual fee " \
    "arrangements, no account-specific volume-tier discounts)"
INCENTIVES = "EXCLUDED_UNLESS_PROGRAM_TERMS_CAPTURED_AND_APPLICABLE"
SOURCE_SERIES = "GET /series/fee_changes (published, effective-dated)"
SOURCE_EVENT = "GET /events/fee_changes (published event override)"
SOURCE_OBSERVED = "GET /series fee_type / fee_multiplier (first observed)"
_6DP = Decimal("0.000001")


def _ts(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    if isinstance(v, datetime):
        return v.timestamp()
    try:
        s = str(v).replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except ValueError:
        return None


def _mult(v) -> Decimal | None:
    try:
        m = Decimal(str(v))
    except Exception:                                           # noqa: BLE001
        return None
    return m if m.is_finite() and m >= 0 else None


def effective_terms(*, series_ticker: str, event_ticker: str | None,
                    at: float, series_changes: list, event_changes: list,
                    observed: dict | None = None) -> dict | None:
    """The terms in force at `at`, with their provenance, or None.

    series_changes / event_changes: rows {id, fee_type, fee_multiplier,
    scheduled_ts, series_ticker[, event_ticker]} (the API's own records;
    event rows may be cleared overrides: fee_type_override null).
    observed: {fee_type, fee_multiplier, first_observed_at} from GET /series.
    """
    at = float(at)
    ev = [e for e in event_changes or []
          if e.get("event_ticker") == event_ticker
          and _ts(e.get("scheduled_ts")) is not None
          and _ts(e.get("scheduled_ts")) <= at]
    if ev:
        e = max(ev, key=lambda x: _ts(x.get("scheduled_ts")))
        ft = e.get("fee_type_override", e.get("fee_type"))
        fm = e.get("fee_multiplier_override", e.get("fee_multiplier"))
        if ft is not None or fm is not None:
            return _terms(ft, fm, e.get("id"), _ts(e.get("scheduled_ts")),
                          SOURCE_EVENT, series_ticker, event_ticker)
        # a cleared override: the series terms apply again
    se = [s for s in series_changes or []
          if s.get("series_ticker") == series_ticker
          and _ts(s.get("scheduled_ts")) is not None
          and _ts(s.get("scheduled_ts")) <= at]
    if se:
        s = max(se, key=lambda x: _ts(x.get("scheduled_ts")))
        return _terms(s.get("fee_type"), s.get("fee_multiplier"), s.get("id"),
                      _ts(s.get("scheduled_ts")), SOURCE_SERIES,
                      series_ticker, event_ticker)
    if observed and observed.get("fee_type") is not None:
        first = _ts(observed.get("first_observed_at"))
        if first is not None and first <= at:
            return _terms(observed.get("fee_type"),
                          observed.get("fee_multiplier"),
                          "observed:%s" % series_ticker, first,
                          SOURCE_OBSERVED, series_ticker, event_ticker)
    return None


def _terms(ft, fm, cid, eff, source, series, event) -> dict:
    m = _mult(fm)
    version = "%s@x%s" % (ft, "?" if m is None else m.normalize())
    return {"schedule_id": str(cid) if cid else None,
            "version": version,
            "version_sha": hashlib.sha256(("%s|%s|%s|%s" % (
                VERSION, ft, m, eff)).encode()).hexdigest()[:16],
            "fee_type": ft, "multiplier": None if m is None else str(m),
            "effective_at": eff, "source": source,
            "series_ticker": series, "event_ticker": event,
            "taker_coefficient": str(TAKER_COEFFICIENT),
            "priced": ft in PRICED_TYPES and m is not None,
            "account_discounts": ACCOUNT_DISCOUNTS,
            "incentives": INCENTIVES}


def model_fee(count: int, price, multiplier) -> Decimal:
    p, c, m = Decimal(str(price)), Decimal(int(count)), Decimal(str(
        multiplier))
    return m * TAKER_COEFFICIENT * c * p * (Decimal(1) - p)


def taker_fee(count: int, price, terms: dict, *,
              balance_precision: Decimal = NON_DIRECT_BALANCE_PRECISION
              ) -> Decimal:
    """The fee (trade fee + rounding fee) of buying `count` at `price` under
    `terms`: ceil_precision(P x C + ceil_6dp(model)) - P x C. Raises
    ValueError when the terms do not price (the route is ineligible)."""
    if not terms or not terms.get("priced"):
        raise ValueError("KALSHI_FEE_TERMS_UNKNOWN")
    if terms.get("effective_at") is None:
        raise ValueError("KALSHI_FEE_EFFECTIVE_TIME_UNKNOWN")
    p = Decimal(str(price))
    c = int(count)
    if c <= 0 or not (Decimal(0) < p < Decimal(1)):
        raise ValueError("KALSHI_FEE_BAD_ORDER")
    trade = model_fee(c, p, terms["multiplier"]).quantize(
        _6DP, rounding=ROUND_CEILING)
    principal = p * Decimal(c)
    debit = ((principal + trade) / balance_precision).to_integral_value(
        rounding=ROUND_CEILING) * balance_precision
    return debit - principal


def fee_fn(terms: dict):
    """fee_fn(count, price) for the routers, or None when the terms do not
    price (no function = FEE_UNKNOWN = ineligible)."""
    if not terms or not terms.get("priced") or \
            terms.get("effective_at") is None:
        return None

    def fn(count, price, _t=terms):
        return taker_fee(count, price, _t)
    fn.terms = terms
    return fn


def fee_evidence(terms: dict | None, *, market_key: str):
    """The red team's FeeEvidence for these terms (fee_guard)."""
    from .red_team.fee_guard import FeeEvidence
    t = terms or {}
    known = bool(t.get("priced"))
    return FeeEvidence(venue="KALSHI", market_key=market_key,
                       fee_known=known, schedule_id=t.get("schedule_id"),
                       schedule_version=t.get("version"),
                       effective_at=t.get("effective_at"),
                       maker_known=False, taker_known=known)

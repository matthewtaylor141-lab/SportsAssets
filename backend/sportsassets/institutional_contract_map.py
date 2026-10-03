"""RETAIL MARKET SLUG -> INSTITUTIONAL SYMBOL, ONLY WHEN THE IDENTITY IS EXACT.

The decision path decides on a retail Polymarket US market slug, for example
`aec-mlb-sd-mil-2026-10-03`. The institutional stream
(`institutional_stream.current`) is keyed by the exchange `symbol`. This
module is the one place a slug becomes a symbol. It maps only when three
things hold at once:

  * SAME INSTRUMENT -- the venue's registered contract id
    (`metadata.cftc_instrument_id`), the instrument `symbol` and the retail
    slug are one string, AND that key composes from separately published
    venue fields (`instrument_product` + `event_id` for a moneyline; the
    existing directional / outcome proofs for everything else);
  * SAME OUTCOME SIDE -- the retail leg is YES (long), and the instrument's
    `long_participant_id` is the event's first participant, which is the
    side the retail venue documents YES to be ("In the market slug, the first
    team is always the long/YES side", /api-reference/orders/overview);
  * SAME PRICE SCALE -- the instrument's own `priceScale` and
    `fractionalQtyScale` are readable integers and its payout equals
    `priceScale` price units (it settles to $1.00), so `px / priceScale` is
    the same 0..1 dollar price the retail venue quotes as `price.value`.

Anything else is refused BY NAME. Nothing is matched on a title, a team name
or a price, and no symbol is computed from a slug: the slug is only ever
COMPARED with keys the venue published.

THE RETAIL NO LEG IS REFUSED, AND WHY. "Only the long side (YES) is directly
tradable. The short side (NO) is synthetic exposure created through positions
in the long side" (/api-reference/orders/overview). Retail NO at X is a
long-instrument order at 1-X, i.e. the OTHER side of the same book. That is
not the same outcome side, and this module does not manufacture a NO book as
1-YES.

ARE THE TWO VENUES ONE BOOK? (`BOOK_EQUIVALENCE`, attached to every mapping).
What the documentation establishes: Polymarket US is one DCM running "a
central limit order book" (/concepts/market-data); retail API orders are
"sent to the exchange" (/api-reference/orders/overview, Price Validation);
retail YES and NO orders at the same price are "two buy orders at the same
price level on the same instrument" and self-match (same page), which can
only happen if retail orders rest in the exchange instrument's own book;
partner (institutional API) order entry for retail participants "uses the
exchange `symbol`" (/partners/get-connected/quickstart); combo RFQs work "over
the same order book" (/api-reference/combos/overview).

What it does NOT establish: that the retail gateway's book
(`/v1/markets/{slug}/book`, the markets WebSocket) shows the same levels and
quantities as the exchange's MarketDataUpdate at the same instant. No
simultaneous read of both has been made from this repository. The one
production side-by-side on record (2026-09-19 22:03Z,
astatc-mls-sje-laf-2026-09-19-sh-ftts-laf, `shadow_identity` docstring) showed
an institutional touch of 0.97/0.99 against a retail read of 0.59/0.60, and it
was never resolved. So an institutional book is the retail executable price
ONLY to the extent that the documented single-CLOB premise holds. The verdict
travels with every mapping so the integrator decides with it in view.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from . import shadow_contract_family as cf
from . import shadow_identity as ident
from . import shadow_identity_resolver as resolver

VERSION = "INSTITUTIONAL_CONTRACT_MAP_V1"

BOOK_EQUIVALENCE = {
    "verdict": "SAME_INSTRUMENT_SAME_CLOB_BY_DOCUMENTATION_NOT_EMPIRICALLY_"
               "VERIFIED",
    "documented": [
        "Polymarket US runs a central limit order book (/concepts/market-data)",
        "retail API orders are sent to the exchange "
        "(/api-reference/orders/overview)",
        "retail YES and NO at one price are two buys at the same price level "
        "on the same instrument and self-match "
        "(/api-reference/orders/overview)",
        "partner order entry for retail participants uses the exchange "
        "symbol (/partners/get-connected/quickstart)",
        "combo RFQs work over the same order book "
        "(/api-reference/combos/overview)",
    ],
    "not_established": [
        "that the retail gateway book shows the same levels and quantities "
        "as the exchange MarketDataUpdate at the same instant -- no "
        "simultaneous read has been made",
        "2026-09-19 22:03Z astatc-mls-sje-laf-2026-09-19-sh-ftts-laf: "
        "institutional 0.97/0.99 vs retail 0.59/0.60, never resolved",
    ],
    "executable_price_authority": "CONDITIONAL_ON_THE_DOCUMENTED_SINGLE_CLOB",
}

# ── refusals ──────────────────────────────────────────────────────────
M_OK = None
M_NO_SLUG = "RETAIL_SLUG_ABSENT"
M_NO_RECORD = "INSTITUTIONAL_INSTRUMENT_RECORD_ABSENT"
M_LEG_UNKNOWN = "RETAIL_LEG_NOT_NAMED"
M_LEG_SHORT = "RETAIL_NO_LEG_IS_THE_SHORT_SIDE_OF_THE_LONG_INSTRUMENT"
M_NO_CFTC = "NO_REGISTERED_CONTRACT_ID"
M_KEYS_DISAGREE = "REGISTERED_ID_SYMBOL_AND_SLUG_ARE_NOT_ONE_CONTRACT"
M_FAMILY_CONTRADICTS = "VENUE_DECLARES_A_MULTI_OUTCOME_SET"
M_NOT_COMPOSED = "INSTRUMENT_KEY_DOES_NOT_COMPOSE_FROM_PRODUCT_AND_EVENT"
M_SIDE = "LONG_SIDE_IS_NOT_ESTABLISHED_AS_THE_EVENTS_FIRST_PARTICIPANT"
M_NOT_BINARY_YES = "SETTLEMENT_RULE_IS_NOT_A_BINARY_YES"
M_NOT_EXACT = "IDENTITY_NOT_EXACT"
M_SCALE = "INSTRUMENT_SCALES_UNREADABLE"
M_PAYOUT = "PAYOUT_IS_NOT_ONE_DOLLAR_IN_PRICE_UNITS"

MONEYLINE_PRODUCT = "aec"
YES_LEGS = ("yes", "long")
NO_LEGS = ("no", "short")


def _text(v):
    s = None if v is None else str(v).strip()
    return s or None


def _pos_int(v):
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError, AttributeError):
        return None
    return n if n > 0 else None


def _dec(v):
    try:
        return Decimal(str(v).strip())
    except (InvalidOperation, TypeError, ValueError, AttributeError):
        return None


def _refuse(name, why, slug, leg, basis=()):
    return {"version": VERSION, "ok": False, "refusal": name, "why": why,
            "retail_slug": slug, "retail_leg": leg,
            "institutional_symbol": None, "basis": list(basis),
            "book_equivalence": BOOK_EQUIVALENCE}


def scale_check(record) -> tuple:
    """(price_scale, qty_scale, payout, refusal, why). The instrument's OWN
    scales, never a default; payout must be exactly priceScale price units."""
    rec = record if isinstance(record, dict) else {}
    ps = _pos_int(rec.get("priceScale"))
    qs = _pos_int(rec.get("fractionalQtyScale"))
    if ps is None or qs is None:
        return None, None, None, M_SCALE, (
            "priceScale %r / fractionalQtyScale %r are not positive integers;"
            " a book is never priced with an assumed scale"
            % (rec.get("priceScale"), rec.get("fractionalQtyScale")))
    payout = _text((rec.get("eventAttributes") or {}).get("payoutValue"))
    d = _dec(payout)
    if d is None or d != Decimal(ps):
        return ps, qs, payout, M_PAYOUT, (
            "payoutValue %r is not priceScale %d price units, so px/priceScale "
            "is not shown to be the retail 0..1 dollar price" % (payout, ps))
    return ps, qs, payout, M_OK, None


def map_retail_to_institutional(retail_slug, retail_leg, instrument_record,
                                *, retail_row=None) -> dict:
    """The institutional symbol for (retail slug, retail leg), or a named
    refusal. `instrument_record` is the venue's refdata record for the
    candidate symbol, exactly as REST `/v1/refdata/instruments` returned it.
    `retail_row` (us_premap shape: market_slug, event_slug, line, ...) is used
    only by the non-moneyline proofs."""
    slug = _text(retail_slug)
    leg = (_text(retail_leg) or "").lower() or None
    if not slug:
        return _refuse(M_NO_SLUG, "no retail market slug was given", slug, leg)
    if leg in NO_LEGS:
        return _refuse(M_LEG_SHORT, (
            "retail NO is synthetic exposure on the long instrument -- an "
            "order at 1-X on the other side of the same book -- not the same "
            "outcome side; no NO book is manufactured as 1-YES"), slug, leg)
    if leg not in YES_LEGS:
        return _refuse(M_LEG_UNKNOWN, "retail leg %r is neither YES nor NO"
                       % retail_leg, slug, leg)
    rec = instrument_record if isinstance(instrument_record, dict) else None
    if not rec or not _text(rec.get("symbol")):
        return _refuse(M_NO_RECORD, "no institutional refdata record for this "
                       "market, so there is nothing to map to", slug, leg)

    meta = rec.get("metadata") or {}
    ev = rec.get("eventAttributes") or {}
    symbol = _text(rec.get("symbol"))
    cftc = _text(meta.get("cftc_instrument_id"))
    basis = []

    # 1. ONE REGISTERED CONTRACT under one key, on both venues.
    if not cftc:
        return _refuse(M_NO_CFTC, "the record carries no cftc_instrument_id",
                       slug, leg)
    if not (cftc == symbol == slug):
        return _refuse(M_KEYS_DISAGREE, (
            "cftc_instrument_id %r, symbol %r and retail slug %r are not one "
            "string" % (cftc, symbol, slug)), slug, leg)
    basis.append("cftc_instrument_id, institutional symbol and retail slug "
                 "are the same registered contract %r" % cftc)

    fam = cf.family_of(rec)
    if fam["family"] == cf.MULTI_OUTCOME_SET and \
            _text(meta.get("instrument_product")) == MONEYLINE_PRODUCT:
        return _refuse(M_FAMILY_CONTRADICTS, (
            "the product is a single-binary moneyline but the venue declares "
            "eventOutcome %r" % fam["eventOutcome"]), slug, leg, basis)

    if _text(meta.get("instrument_product")) == MONEYLINE_PRODUCT:
        got = _moneyline(rec, meta, ev, symbol, slug, leg, basis)
    else:
        got = _other(rec, slug, leg, retail_row, basis)
    if got is not None:
        return got

    # 3. SAME PRICE SCALE.
    ps, qs, payout, refusal, why = scale_check(rec)
    if refusal:
        return _refuse(refusal, why, slug, leg, basis)
    basis.append("priceScale %d, fractionalQtyScale %d, payout %s = $1.00: "
                 "px/priceScale is the retail long-side price" % (ps, qs,
                                                                 payout))
    return {"version": VERSION, "ok": True, "refusal": M_OK, "why": None,
            "retail_slug": slug, "retail_leg": leg,
            "institutional_symbol": symbol,
            # Retail YES (BUY_LONG) at p is a BUY of this instrument at p:
            # it lifts the instrument's OFFERS; a SELL_LONG hits its BIDS.
            "institutional_side": "LONG",
            "price_transform": "IDENTITY",
            "price_scale": ps, "qty_scale": qs, "payout_value": payout,
            "basis": basis, "book_equivalence": BOOK_EQUIVALENCE}


def _moneyline(rec, meta, ev, symbol, slug, leg, basis):
    """AEC (single binary, "Will <first team> win?"). Returns a refusal or
    None after appending to `basis`."""
    event_id = _text(meta.get("event_id"))
    product = _text(meta.get("instrument_product"))
    # 2a. THE KEY COMPOSES from the product code and the event id the venue
    #     published separately (docs: `{product_code}-{event_id}`, moneyline
    #     has no outcome suffix).
    if not event_id or "%s-%s" % (product, event_id) != symbol:
        return _refuse(M_NOT_COMPOSED, (
            "instrument_product %r + event_id %r do not compose to %r"
            % (product, event_id, symbol)), slug, leg, basis)
    basis.append("instrument_product %r + event_id %r compose to exactly "
                 "this key" % (product, event_id))
    # 2b. SAME OUTCOME SIDE: the long participant is the event's FIRST
    #     participant -- the side the retail venue documents YES to be.
    long_pid = _text(meta.get("long_participant_id"))
    short_pid = _text(meta.get("short_participant_id"))
    short_code = short_pid.rsplit("-", 1)[-1] if short_pid else None
    if not (long_pid and short_code and
            event_id.startswith("%s-%s-" % (long_pid, short_code))):
        return _refuse(M_SIDE, (
            "long_participant_id %r / short_participant_id %r do not open "
            "event_id %r in that order, so YES (the slug's first team) is not "
            "shown to be the instrument's long side"
            % (long_pid, short_pid, event_id)), slug, leg, basis)
    basis.append("long_participant_id %r is the event's first participant, "
                 "which is the retail YES side" % long_pid)
    # 2c. IT SETTLES AS A BINARY YES.
    if not cf.settlement_is_binary_yes(cf.proposition(rec)):
        return _refuse(M_NOT_BINARY_YES, (
            "instrument_rules does not state a 'settle to Yes if' condition"),
            slug, leg, basis)
    basis.append("instrument_rules settles the contract to Yes on one stated "
                 "condition")
    return None


def _other(rec, slug, leg, retail_row, basis):
    """Spreads, totals and outcome instruments: the existing proofs
    (shadow_identity_resolver), accepted only at EXACT_ONE_TO_ONE."""
    row = dict(retail_row or {})
    row.setdefault("market_slug", slug)
    row.setdefault("side_norm", leg)
    got = resolver.resolve(slug, leg, instrument_record=rec, retail_row=row)
    if got.get("identity_status") != ident.EXACT_ONE_TO_ONE:
        return _refuse(M_NOT_EXACT, "; ".join(got.get("why") or []) or
                       "identity is %s" % got.get("identity_status"),
                       slug, leg, basis)
    basis.extend(got.get("agree") or [])
    return None

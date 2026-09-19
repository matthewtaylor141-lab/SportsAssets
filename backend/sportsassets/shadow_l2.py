"""THE INSTITUTIONAL L2 CONTRACT, AS OBSERVED -- not as guessed.

Owner directive 2026-09-19 21:5xZ §2/§3: "USE INSTITUTIONAL L2 AS THE
EXECUTION-EVIDENCE SOURCE ... Verify the actual production L2 shape
first ... Do not adapt from guessed field names."

EVERY FIELD NAME BELOW WAS READ OFF PRODUCTION. Run 35471198997,
2026-09-19 21:42:26Z, authenticated against
api.prod.polymarketexchange.com with read:l2marketdata, symbol
astatc-mls-sje-laf-2026-09-19-sh-ftts-laf, evidenceClass
OBSERVED_PRODUCTION. The verbatim answers:

    GET /v1/orderbook/{symbol}/bbo
      keys: bestBid, bestOffer, midPrice, spread, state, symbol,
            transactTime
      bestBid   {"px": "97", "qty": "1",    "symbolSubType": ""}
      bestOffer {"px": "98", "qty": "5000", "symbolSubType": ""}

    GET /v1/orderbook/{symbol}
      keys: bids, offers, state, stats, symbol, transactTime
      bids   [{"px":"97","qty":"1"}, {"px":"39","qty":"407"},
              {"px":"1","qty":"285000"}]
      offers [{"px":"98","qty":"5000"}, {"px":"99","qty":"2500"}]

WHAT THAT SETTLES, and why each one would have been got wrong:

  PRICE FIELD is `px`, not `price` / `bidPrice`. It is a STRING of
      integer price-scale units: "97" is not 97 dollars and not 97
      cents-as-a-float, it is 97 ticks and must be divided by the
      INSTRUMENT'S OWN priceScale.
  QUANTITY FIELD is `qty`, also a string of scaled integer units,
      divided by the instrument's fractionalQtyScale.
  SIDES are `bids` and `offers`. NOT `asks`. A reader looking for
      `asks` finds nothing and reports an empty book -- which is
      indistinguishable from a genuinely empty one, and would have
      made every experimental trade refuse for the wrong reason.
  SORT ORDER is best-first on both sides, observed: bids descend
      (97, 39, 1), offers ascend (98, 99). Index 0 is the touch.
  TIMESTAMP is `transactTime`. The BBO also carries `midPrice` and
      `spread`; the depth response carries `stats` instead.
  SYMBOL IDENTITY echoes the retail slug exactly.

THE SCALES ARE NOT IN THE BOOK RESPONSE. They are per-instrument
(refdata records priceScale 100 / tickSize 0.01 for esports and ATP,
1000 / 0.005 for MLB, 1000 / 0.001 for NFL, fractionalQtyScale 100 on
every sports row seen). So a book cannot be converted to prices and
sizes ALONE -- it needs its instrument's scales beside it, and this
module REFUSES to convert without them rather than assuming 100. A
silently-assumed scale is the failure that turns a $49 fill into a
$4,900 one on an NFL row, and it would not look wrong anywhere.

NOTHING HERE OPENS A SOCKET. This is the parser and the envelope; the
transport is the authorized read-only production lane.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import shadow as sh

NOT_IDENTIFIED = sh.NOT_IDENTIFIED

# §2: the evidence source, named on every row it produces. RETAIL BBO
# and INSTITUTIONAL L2 must never be silently substituted for one
# another -- they are different books, and on the verification symbol
# they disagreed.
L2_SOURCE_INSTITUTIONAL = "PMX_PRODUCTION_L2"
L2_SOURCE_INSTITUTIONAL_BBO = "PMX_PRODUCTION_BBO"

# The venue's own words, kept as the venue spells them.
STATE_OPEN = "INSTRUMENT_STATE_OPEN"

# The observed field names. Named constants so a venue rename is a
# one-line change with a failing test, not a silent empty book.
F_PX = "px"
F_QTY = "qty"
SIDE_BIDS = "bids"
SIDE_OFFERS = "offers"
F_TRANSACT_TIME = "transactTime"
F_BEST_BID = "bestBid"
F_BEST_OFFER = "bestOffer"


class L2Refusal(sh.ShadowRefusal):
    """A book this module will not pretend to understand."""


class ScalesRequired(L2Refusal):
    """Asked to convert a book without its instrument's scales."""


def _aware(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def _scaled(raw, scale):
    """One scaled integer string -> a float, or None.

    Returns None rather than 0.0 for anything unreadable: a level whose
    price did not parse is not a level at price zero.
    """
    if raw is None or scale in (None, 0):
        return None
    try:
        return float(raw) / float(scale)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def levels_from(side_rows, *, price_scale, qty_scale) -> list:
    """[{px, qty}] in venue units -> [(price, quantity)] in real units.

    Order is PRESERVED as the venue sent it (best first, observed).
    `marketable_fill` sorts for itself, so this does not reorder and
    cannot mask a venue that changes its mind about direction.
    """
    out = []
    for row in (side_rows or []):
        if not isinstance(row, dict):
            continue
        price = _scaled(row.get(F_PX), price_scale)
        qty = _scaled(row.get(F_QTY), qty_scale)
        # A LEVEL WITH NO READABLE PRICE OR SIZE IS DROPPED, NOT ZEROED.
        # A zero-priced level would be taken first by any walk.
        if price is None or qty is None or qty <= 0:
            continue
        out.append((price, qty))
    return out


def book_from(response, *, price_scale=None, qty_scale=None,
              source=L2_SOURCE_INSTITUTIONAL, request_id=None,
              received_at=None, requested_at=None) -> dict:
    """One /v1/orderbook/{symbol} response -> the arrival book plus §2's
    provenance envelope.

    THE ENVELOPE IS NOT DECORATION. §2 requires L2_SOURCE,
    L2_REQUEST_ID, the venue's timestamp where supplied, our receive
    timestamp, the latency between them and the book version where
    supplied, preserved on every experimental decision. Without them a
    later reader cannot tell a fill reconstructed against a fresh book
    from one reconstructed against a book that arrived late.
    """
    if not isinstance(response, dict):
        raise L2Refusal("refused: an L2 response that is not an object")
    if price_scale is None or qty_scale is None:
        raise ScalesRequired(
            "refused: %r has no priceScale/fractionalQtyScale beside it. "
            "px and qty are scaled integers; converting them on an "
            "assumed scale of 100 would silently misprice every "
            "instrument that does not use it (MLB is 1000, NFL is 1000)"
            % response.get("symbol"))

    received = _aware(received_at) or datetime.now(tz=timezone.utc)
    requested = _aware(requested_at)
    venue_at = response.get(F_TRANSACT_TIME)

    bids = levels_from(response.get(SIDE_BIDS),
                       price_scale=price_scale, qty_scale=qty_scale)
    offers = levels_from(response.get(SIDE_OFFERS),
                         price_scale=price_scale, qty_scale=qty_scale)

    return {
        # what marketable_fill walks. 'asks' is OUR word for the sell
        # side, mapped from the venue's 'offers' here and nowhere else.
        "bids": bids,
        "asks": offers,
        # §2's provenance, in full
        "l2Source": source,
        "l2RequestId": request_id,
        "l2SourceTimestamp": venue_at if venue_at is not None
        else NOT_IDENTIFIED,
        "l2ReceivedTimestamp": received,
        "l2LatencyMs": (None if requested is None else
                        round((received - requested).total_seconds() * 1000,
                              1)),
        # The venue supplies no book version or sequence on this
        # endpoint; saying so is not the same as having one.
        "l2BookVersion": NOT_IDENTIFIED,
        "symbol": response.get("symbol"),
        "state": response.get("state"),
        "priceScale": price_scale,
        "qtyScale": qty_scale,
        "bidLevels": len(bids),
        "askLevels": len(offers),
        # §3's EMPTY-BOOK SEMANTICS: an OPEN instrument with no levels
        # is a real and expected state, and it is NOT an error. It means
        # no experimental trade can execute here, which §4 requires be
        # recorded as NOT_IDENTIFIED rather than filled at an assumption.
        "empty": not (bids or offers),
        "readable": True,
    }


def bbo_from(response, *, price_scale=None, qty_scale=None, **kw) -> dict:
    """The touch only. NEVER a substitute for depth.

    §2: "Never collapse BBO into L2." A one-level book built from the
    BBO would walk exactly the touch size and report everything beyond
    it as unfilled -- an answer that looks like a measurement and is
    an artefact of the endpoint chosen. So this returns the touch under
    its own source name, and `book_from` is what execution walks.
    """
    if not isinstance(response, dict):
        raise L2Refusal("refused: a BBO response that is not an object")
    if price_scale is None or qty_scale is None:
        raise ScalesRequired("refused: the BBO is scaled the same way")
    touch = {}
    for name, key in (("bid", F_BEST_BID), ("ask", F_BEST_OFFER)):
        row = response.get(key)
        if isinstance(row, dict):
            touch[name] = _scaled(row.get(F_PX), price_scale)
            touch[name + "Size"] = _scaled(row.get(F_QTY), qty_scale)
        else:
            touch[name] = None
            touch[name + "Size"] = None
    touch.update({
        "l2Source": kw.get("source", L2_SOURCE_INSTITUTIONAL_BBO),
        "l2RequestId": kw.get("request_id"),
        "l2SourceTimestamp": response.get(F_TRANSACT_TIME, NOT_IDENTIFIED),
        "symbol": response.get("symbol"),
        "state": response.get("state"),
        "isDepth": False,
    })
    return touch


# ── WHICH LEG A RETAIL BBO ACTUALLY BELONGS TO ───────────────────────
#
# Owner directive 2026-09-19 22:4xZ §2. THE DEFECT, stated plainly: the
# collector's universe returns one row per (market_slug, side_norm), so
# a slug with a `yes` and a `no` row is read TWICE -- and both reads
# call `bbo_read(slug)`, which takes no side. Both legs were therefore
# stamped with the same bid and ask, and a leg-conditional feature
# built on that series would be reading the YES book under the NO name.
#
# WHAT THE ENDPOINT ACTUALLY RETURNS, established from the adapter
# rather than assumed: there is ONE long contract per slug. The venue's
# intents map BUY_LONG -> SIDE_BUY at L and BUY_SHORT -> SIDE_SELL at
# the SAME L (pmx._SIDE_FOR). The retail `no` leg is a SELL of the yes
# contract, not a second book. So `bbo_read(slug)` is the YES
# contract's book, and it is the institutional `-laf` instrument's
# counterpart.
#
# WHY NO IS NOT DERIVED. On a mutually exclusive exhaustive set the NO
# *probability* is 1 - P(yes), but the NO *book* is not: executing the
# complement means walking the sibling instruments' depth, which is a
# different quantity from 1 minus a price. §2 allows a derivation only
# where "the transformation is mathematically exact"; for depth it is
# not, so NO_BBO is NOT_IDENTIFIED rather than a mirrored copy.

BIND_YES = "YES_CONTRACT_BOOK"
BIND_NOT_IDENTIFIED = "NO_LEG_BOOK_NOT_IDENTIFIED"
BIND_MARKET_LEVEL = "MARKET_LEVEL_NOT_LEG_SPECIFIC"

# Bumped whenever the collector's leg semantics change. It travels on
# every opportunity so an experiment can require the corrected
# semantics and never train on the duplicated rows (§3).
FEATURE_SOURCE_VERSION = "BETTOR_COLLECTOR_LEG_BOUND_V2"
FEATURE_SOURCE_VERSION_DUPLICATED = "BETTOR_COLLECTOR_LEG_DUPLICATED_V1"

_YES_LEGS = frozenset({"yes", "over", "long"})
_NO_LEGS = frozenset({"no", "under", "short"})


def bind_leg(market_state: dict | None, outcome_leg) -> dict:
    """Stamp a retail BBO with the leg it actually describes.

    Returns the state unchanged in shape, plus `bboBinding` and -- for
    a leg whose book this is NOT -- bid/ask/mid/spread blanked to
    NOT_IDENTIFIED. Blanking rather than dropping keeps the row's
    shape stable so a reader cannot mistake "not this leg's book" for
    "no observation was made".
    """
    state = dict(market_state or {})
    leg = str(outcome_leg or "").strip().lower()
    state["featureSourceVersion"] = FEATURE_SOURCE_VERSION

    if not state.get("readable"):
        state["bboBinding"] = BIND_MARKET_LEVEL
        return state
    if leg in _YES_LEGS:
        state["bboBinding"] = BIND_YES
        return state
    if leg in _NO_LEGS:
        # NOT a copy of the yes book, and not 1 - yes either.
        state["bboBinding"] = BIND_NOT_IDENTIFIED
        for field in ("bid", "ask", "mid", "spread", "spreadRelative"):
            state[field] = None
        state["whyLegBookAbsent"] = (
            "the retail endpoint returns the YES contract's book; the NO "
            "leg is a SELL of that contract and its complement depth is "
            "the sibling instruments', which this read does not carry")
        return state
    # An unnamed or unfamiliar leg gets the market-level word rather
    # than being guessed into one side.
    state["bboBinding"] = BIND_MARKET_LEVEL
    return state


def leg_is_execution_bound(market_state: dict | None) -> bool:
    """Only a YES-bound book may feed X1's feature series today."""
    return (market_state or {}).get("bboBinding") == BIND_YES

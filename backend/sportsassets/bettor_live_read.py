"""The authenticated READ-ONLY reads BETTOR's live path needs.

`bettor_prospective_runner.read_live_book()` raised NotImplementedError
with a note saying a live reader "must be injected by the deployment".
That was right about where the credentials live and wrong to leave the
reader unwritten: the injection point cannot be tested, reviewed or
costed while the thing being injected does not exist. This is that
thing.

WHAT IS AND IS NOT DONE HERE, kept apart on purpose because the owner
directive draws exactly this line:

  IMPLEMENTATION      this module. Code that, given an authenticated
                      client, performs reads. Writing it activates
                      nothing.
  READ-ONLY           calling it against the venue. Reads a public
                      book and a market's resolution state. Submits
                      nothing, writes no accounting record.
  DEPLOYMENT          running it on a schedule in production. NOT DONE
                      and not requested by writing this file. See
                      PILOT_PROPOSAL.md section 7.

NO ORDER PATH. This module imports `pmus` for `_get_client`,
`book_read` and the market listing. It does not import, reference or
wrap `submit_fok`, `close_position` or any order call, and a test
asserts that by AST scan rather than by substring, because a
forbidden-name list that matches identifiers measures spelling.

CREDENTIALS NEVER LEAVE. The client is built from the service's own
environment by `pmus._get_client()`. Nothing here returns, logs or
copies a credential, and `resolution_fields()` returns KEY NAMES ONLY
-- the same discipline `reconcile_read.capability_probe()` uses.

THE THREE CLOCKS. A book row carries the venue's own source timestamp
AND our receipt timestamp, both as ISO strings with explicit offsets.
The decision timestamp is taken by the caller at the moment it decides,
never here -- a reader that stamps the decision time is stamping the
time of the READ, and the gap between the two is exactly what a
freshness bound exists to catch. Measured on the capture: the median
source-to-receipt delay is 549.6 s.

UNREADABLE IS NOT EMPTY. A failed read is named. An absent field is
named. A market with no resolution is PENDING, which is different from
a market whose resolution we could not read, which is different again
from a market the venue does not list at all. Those four are separate
return values and were previously one empty table.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

READER_VERSION = "BETTOR_LIVE_READ_V1"

# Resolution outcomes, kept distinct because collapsing them is how an
# empty settlement table came to be read as "nothing has resolved".
RESOLVED = "RESOLVED"        # the venue reports an outcome
PENDING = "PENDING"          # the venue lists it, open, no outcome yet
UNREADABLE = "UNREADABLE"    # the read failed, or the payload made no sense
UNMATCHED = "UNMATCHED"      # the venue does not list this slug at all
# A closed market whose outcome PRICES have converged to 1 and 0. That
# is an inference from a price, not an outcome the venue reported, so it
# is never counted as RESOLVED and never carries verified semantics.
RESOLVED_DERIVED = "RESOLVED_DERIVED"

# Settlement units. `settlementPrice` is a USD Amount and the type says
# nothing about whether a settled binary reports 1, 1.0000 or 100. A
# value outside [0,1] is reported as unverified, never divided by 100.
UNITS_NOT_ESTABLISHED = "UNITS_NOT_ESTABLISHED"

# MEASURED 2026-09-21, run 35641425742, on a real slug. The market
# listing carries these 34 keys:
#
#   active archived assetPriceTerms category closed comboEnabled
#   createdAt description endDate ep3Status ep3SyncedAt feeCoefficient
#   gameStartTime hidden id line manualActivation marketSides marketType
#   minimumTradeQty orderPriceMinTickSize outcomePrices outcomes question
#   rulesDisclaimer rulesDisclaimerPopup slug sportsMarketType
#   sportsMarketTypeV2 spreadTotalSuffix startDate status tags updatedAt
#
# NONE OF MY EIGHT GUESSES IS AMONG THEM. `resolvedOutcome`,
# `winningOutcome`, `settledOutcome`, `result`, `outcome` and their
# snake_case spellings are all absent. The hypothesis was wrong, which
# is exactly what returning the key names was for.
#
# THE VENUE DOES NOT REPORT A WINNER ON THIS ENDPOINT. What it reports
# is `outcomes` (the side labels, present on open markets too) and
# `outcomePrices` (the current prices, likewise). Neither is a
# resolution. At settlement the prices converge to 1 and 0, and reading
# "1" as "this side won" is an INFERENCE FROM A PRICE, not a reported
# outcome -- which is the substitution SETTLEMENT_SEMANTICS_STATUS
# exists to prevent. So it gets its own status, RESOLVED_DERIVED, and
# is never counted as RESOLVED.
REFUTED_OUTCOME_FIELDS = ("resolvedOutcome", "resolved_outcome",
                          "winningOutcome", "winning_outcome",
                          "settledOutcome", "settled_outcome",
                          "result", "outcome")
# Kept empty rather than deleted: a payload that ever grows a reported
# outcome field should be recognised, and the refuted list above is the
# record of what was checked.
OUTCOME_FIELDS: tuple = ()

# The price vector, which is the only settlement signal available.
OUTCOME_PRICE_FIELDS = ("outcomePrices", "outcome_prices")
OUTCOME_LABEL_FIELDS = ("outcomes",)

# How close to 1 / 0 the prices must sit before the market is treated as
# having converged. Deliberately strict: a market at 0.99 has not
# settled, it is nearly certain, and those are different.
SETTLED_PRICE_TOLERANCE = 1e-9

# Also measured and worth naming, because the pilot proposal assumed
# both: `minimumTradeQty` and `orderPriceMinTickSize` are on the market,
# so the size floor and tick size are READABLE rather than assumed. And
# `feeCoefficient` is on the market, which is a direct check on WHICH
# published schedule the venue is currently applying -- 0.06 is the
# 2026-07-01 theta, 0.0695 the 2026-09-17 one.
SIZE_FLOOR_FIELDS = ("minimumTradeQty", "minimum_trade_qty")
TICK_SIZE_FIELDS = ("orderPriceMinTickSize", "order_price_min_tick_size")
FEE_COEFFICIENT_FIELDS = ("feeCoefficient", "fee_coefficient")

# `endDate` is the only one of these the payload actually carries.
SETTLED_AT_FIELDS = ("resolvedAt", "resolved_at", "settledAt", "settled_at",
                     "closedAt", "closed_at", "endDate", "end_date")

SOURCE_TS_FIELDS = ("transactTime", "transact_time", "timestamp", "ts",
                    "asOf", "as_of")


def _now_iso() -> str:
    """Receipt time, aware, taken at the instant of the read."""
    return datetime.now(timezone.utc).isoformat()


def _first(d: dict, names) -> tuple[str | None, object]:
    """(field name, value) for the first name present. Name, not guess."""
    for n in names:
        if isinstance(d, dict) and n in d and d[n] is not None:
            return n, d[n]
    return None, None


def _markets(client, pmus):
    """The markets surface of the client we were HANDED.

    `client` was a parameter that both resolution reads ignored while
    building their own from the environment -- so the injection point
    the whole design rests on did not actually inject, and a test
    supplying a fake was silently talking to the venue's builder. A
    caller that passes None is asking for the service's own client and
    says so by passing None.
    """
    c = client if client is not None else pmus._get_client()
    return c.markets


def read_book(client, market_slug: str, *, outcome_leg: str | None = None,
              now=None) -> dict:
    """One live book, in `bettor_observation_adapter.normalize`'s shape.

    Returns the adapter's input row directly, so the live path and the
    replay path normalize through the SAME code. A second shape here
    would be a second place for the depth error to live.

    `multi_level_depth` is built from the venue's own ladders, with an
    explicit `level` per entry, because the adapter reads the level the
    venue MARKS lowest rather than trusting array order. `yes_depth`
    carries the five-level SUM under the same key the capture uses, and
    the adapter treats it as cumulative -- never as executable size.

    Never raises. A failed read comes back with
    `book_readability_status` naming the failure, which the adapter
    rejects, which is the correct outcome for a read that did not
    happen.
    """
    from . import pmus

    received = now or _now_iso()
    # SAME CLIENT RULE AS THE RESOLUTION READS. `pmus.book_read` reaches
    # into `client.markets`, so a None client would return
    # NO_BOOK_FEED_ON_CLIENT -- an unreadable book that looks like a
    # venue problem and is actually a caller that meant "use ours".
    res = pmus.book_read(
        client if client is not None else pmus._get_client(), market_slug)
    row: dict = {
        "reader": READER_VERSION,
        "market_id": market_slug,
        "instrument_id": market_slug,
        "outcome_leg": outcome_leg,
        "book_received_ts": received,
        "observed_at": received,
        "venue_state": None,
        "book_readability_status": "READABLE",
        "yes_bid": None, "yes_ask": None,
        # The complement is a SEPARATE INSTRUMENT and a separate read.
        # Deriving it as 1 - own would assert the no-arbitrage identity
        # Class C already falsified on 3,732 observations.
        "no_bid": "NOT_IDENTIFIED", "no_ask": "NOT_IDENTIFIED",
    }

    md = res.get("marketData")
    if md is None or not isinstance(md, dict):
        row["book_readability_status"] = "UNREADABLE_%s" % (
            res.get("error") or "NO_MARKET_DATA")
        return row

    row["venue_state"] = (str(md["state"]) if md.get("state") is not None
                          else None)
    for key, names in (("yes_bid", ("bestBid", "best_bid", "bid")),
                       ("yes_ask", ("bestAsk", "best_ask", "ask"))):
        _, v = _first(md, names)
        row[key] = str(v) if v is not None else "NOT_IDENTIFIED"

    src_field, src = _first(md, SOURCE_TS_FIELDS)
    row["book_source_ts"] = str(src) if src is not None else "NOT_IDENTIFIED"
    row["book_source_ts_field"] = src_field

    # AGE IS COMPUTED BY THE CALLER FROM THE THREE CLOCKS, not stored
    # here. `book_age_s` in the capture is the age AT CAPTURE, and a
    # live decision taken on a stored age is a decision taken on
    # history. The live path computes it at the moment of decision.
    row["book_age_s"] = "NOT_IDENTIFIED"

    bids, asks = _ladder(md, ("bids", "bid", "buyLevels")), \
        _ladder(md, ("offers", "asks", "ask", "sellLevels"))
    if bids or asks:
        row["multi_level_depth"] = {"bid": bids, "ask": asks,
                                    "levels": max(len(bids), len(asks))}
        row["yes_depth"] = {
            "bid": "%.4f" % sum(float(x["qty"]) for x in bids),
            "ask": "%.4f" % sum(float(x["qty"]) for x in asks),
            "levelsCaptured": max(len(bids), len(asks)),
            "isCumulative": ("the sum ACROSS levels, not the quantity at "
                             "the quote; measured 939/939 rows"),
        }
    stats = md.get("stats") if isinstance(md.get("stats"), dict) else {}
    _, traded = _first(stats, ("sharesTraded", "shares_traded"))
    row["stats_shares_traded"] = (str(traded) if traded is not None
                                  else "NOT_IDENTIFIED")
    return row


def _ladder(md: dict, keys) -> list:
    """One side's ladder as [{level, qty, price}], best level FIRST.

    `level` is written explicitly rather than left implicit in array
    order, because the adapter reads the marked level and an array
    whose order is assumed is how a mid-book level becomes the touch.
    """
    raw = None
    for k in keys:
        v = md.get(k)
        if isinstance(v, list) and v:
            raw = v
            break
    if raw is None:
        return []
    out = []
    for i, lv in enumerate(raw):
        if not isinstance(lv, dict):
            continue
        px = lv.get("px") if isinstance(lv.get("px"), dict) else None
        price = (px or {}).get("value") if px else (
            lv.get("price") if lv.get("price") is not None else None)
        qty = lv.get("qty") if lv.get("qty") is not None else lv.get("size")
        if price is None or qty is None:
            continue
        out.append({"level": i, "qty": str(qty), "price": str(price)})
    return out


def resolution_fields(client, market_slug: str) -> dict:
    """KEY NAMES ONLY from one market payload. Never a value.

    The point of this is to find out what the venue actually offers for
    a resolution before anything is built on a guess. It returns the
    field names present and which of `OUTCOME_FIELDS` matched -- no
    prices, no identifiers, no account payload. The same discipline as
    `reconcile_read.capability_probe()`, for the same reason.
    """
    from . import pmus

    out = {"reader": READER_VERSION, "slug": market_slug, "ok": False,
           "keys": [], "outcome_field": None, "settled_at_field": None,
           "error": None}
    try:
        resp = _markets(client, pmus).list({"slug": [market_slug]})
        markets = list((resp or {}).get("markets") or [])
    except Exception as exc:  # noqa: BLE001 -- named, never swallowed
        out["error"] = type(exc).__name__
        return out
    if not markets:
        out["error"] = "NOT_LISTED"
        return out
    m = markets[0]
    out["ok"] = True
    out["keys"] = sorted(k for k in m if isinstance(k, str))
    out["outcome_field"] = _first(m, OUTCOME_FIELDS)[0]
    out["settled_at_field"] = _first(m, SETTLED_AT_FIELDS)[0]
    return out


def read_settlement(client, market_slug: str) -> dict:
    """`GET /v1/markets/{slug}/settlement` -- the venue's own answer.

    I did not check for this endpoint and inferred resolution from
    `outcomePrices` on the market listing instead. It exists:
    `client.markets.settlement(slug)`, returning `MarketSettlement =
    {marketSlug, settlementPrice: Amount, settledAt: str}` where
    `Amount = {value: str, currency: "USD"}`.

    UNITS ARE NOT ASSUMED. `settlementPrice` is a USD Amount, and
    whether a settled binary contract reports "1", "1.0000", "100" (if
    cents) or something else is NOT established by the type. So the raw
    string is carried through unparsed, `settlement_price_raw`, and a
    numeric reading is offered only when it lands in [0, 1] -- with
    `units_status` saying which. A value outside that range is reported
    as UNITS_UNVERIFIED rather than divided by 100 on a hunch.

    THE UNRESOLVED RESPONSE IS NOT ASSUMED EITHER. Whether an open
    market 404s, 400s, or returns a zero price is unknown until a real
    unresolved slug is read. A `NotFoundError` is reported as PENDING
    -- the market exists and has no settlement -- and every other error
    as UNREADABLE with its type, so the two never merge.
    """
    from . import pmus

    out = {"reader": READER_VERSION, "slug": market_slug,
           "status": UNREADABLE, "settlement_price_raw": None,
           "settlement_price": None, "units_status": UNITS_NOT_ESTABLISHED,
           "settled_at": None, "currency": None, "error": None,
           "endpoint": "/v1/markets/{slug}/settlement"}
    c = client if client is not None else pmus._get_client()
    try:
        resp = c.markets.settlement(market_slug) or {}
    except Exception as exc:  # noqa: BLE001 -- named, never swallowed
        name = type(exc).__name__
        out["error"] = name
        # A market with no settlement is PENDING. Any other failure is
        # a failure, and collapsing the two is what made an empty table
        # read as "nothing has matured".
        out["status"] = PENDING if name == "NotFoundError" else UNREADABLE
        return out

    amt = resp.get("settlementPrice")
    raw = amt.get("value") if isinstance(amt, dict) else amt
    out["settlement_price_raw"] = str(raw) if raw is not None else None
    out["currency"] = (amt.get("currency") if isinstance(amt, dict)
                       else None)
    out["settled_at"] = (str(resp["settledAt"])
                         if resp.get("settledAt") is not None else None)

    if raw is None:
        out["error"] = "NO_SETTLEMENT_PRICE_IN_RESPONSE"
        return out
    try:
        val = float(raw)
    except (TypeError, ValueError):
        out["error"] = "SETTLEMENT_PRICE_UNPARSABLE"
        return out
    if 0.0 <= val <= 1.0:
        out["settlement_price"] = val
        out["units_status"] = "DOLLARS_PER_CONTRACT_CONSISTENT"
        out["status"] = RESOLVED
    else:
        # Could be cents, could be a different convention, could be a
        # market that does not settle in [0,1]. Not divided by 100.
        out["units_status"] = "UNITS_UNVERIFIED_OUT_OF_0_1"
        out["error"] = "SETTLEMENT_PRICE_%s_OUTSIDE_0_1" % raw
    return out


def read_resolution(client, market_slug: str) -> dict:
    """Has this market resolved, and to what?

    FIVE OUTCOMES, NOT TWO. `bettor_state_settlements` was empty and the
    acceptance package read that as "nothing has matured". It could
    equally have meant the read failed, or the slug is not listed, or
    nothing ever called the writer -- and it was in fact the last of
    those. So each is returned separately and by name:

      RESOLVED          the venue's settlement endpoint gives a price
      RESOLVED_DERIVED  closed, outcome prices converged to 1 and 0 --
                        an inference from a price, never counted as
                        RESOLVED
      PENDING           listed and open, or settlement not found yet
      UNREADABLE        the read failed, or the payload made no sense
      UNMATCHED         the venue does not list this slug

    THE SETTLEMENT ENDPOINT IS TRIED FIRST, because it is the venue's
    own answer rather than ours. Only when it does not resolve does this
    fall back to the market listing.
    """
    from . import pmus

    st = read_settlement(client, market_slug)
    if st["status"] == RESOLVED:
        return {"reader": READER_VERSION, "slug": market_slug,
                "status": RESOLVED, "outcome": st["settlement_price_raw"],
                "outcome_field": "settlementPrice",
                "settlement_price": st["settlement_price"],
                # THE VENUE'S OWN STRING, carried under its own name as
                # well as under `outcome`. A consumer that wants to record
                # what the venue said -- rather than our float reading of
                # it -- should not have to know that `outcome` happens to
                # hold the raw value on this branch and a label on another.
                "settlement_price_raw": st["settlement_price_raw"],
                "units_status": st["units_status"],
                "settled_at": st["settled_at"], "closed": None,
                "error": None, "keys_seen": [],
                "source": "/v1/markets/{slug}/settlement"}

    out = {"reader": READER_VERSION, "slug": market_slug,
           "status": UNREADABLE, "outcome": None, "settled_at": None,
           "outcome_field": None, "closed": None, "error": None,
           "keys_seen": [], "settlement_probe": st["status"],
           "settlement_error": st["error"]}
    try:
        resp = _markets(client, pmus).list({"slug": [market_slug]})
        markets = list((resp or {}).get("markets") or [])
    except Exception as exc:  # noqa: BLE001
        out["error"] = type(exc).__name__
        return out
    if not markets:
        out["status"] = UNMATCHED
        out["error"] = "NOT_LISTED"
        return out

    m = markets[0]
    out["keys_seen"] = sorted(k for k in m if isinstance(k, str))
    out["closed"] = bool(m.get("closed"))
    _, ts = _first(m, SETTLED_AT_FIELDS)
    out["settled_at"] = str(ts) if ts is not None else None

    # These three are read because the pilot proposal ASSUMED all three
    # and the venue reports them. Values, not names: a tick size and a
    # minimum quantity are public market parameters, not account data.
    for key, names in (("minimum_trade_qty", SIZE_FLOOR_FIELDS),
                       ("tick_size", TICK_SIZE_FIELDS),
                       ("fee_coefficient", FEE_COEFFICIENT_FIELDS)):
        f, v = _first(m, names)
        out[key] = str(v) if v is not None else None

    # 1. A REPORTED outcome, if the payload ever grows one. As of
    #    2026-09-21 it does not; see REFUTED_OUTCOME_FIELDS.
    field, value = _first(m, OUTCOME_FIELDS)
    if field is not None:
        return dict(out, status=RESOLVED, outcome=str(value),
                    outcome_field=field)

    if not out["closed"]:
        return dict(out, status=PENDING)

    # 2. A DERIVED outcome: closed, with prices converged to 1 and 0.
    #    Reported separately and NEVER as RESOLVED, because reading a
    #    price as a winner is an inference the venue did not make.
    labels = _first(m, OUTCOME_LABEL_FIELDS)[1]
    prices = _first(m, OUTCOME_PRICE_FIELDS)[1]
    winner = _converged_winner(labels, prices)
    if winner is not None:
        return dict(out, status=RESOLVED_DERIVED, outcome=winner,
                    outcome_field="outcomePrices",
                    derivation=("closed market whose outcome prices "
                                "converged to 1 and 0; the venue reported "
                                "no winner and this is an inference from a "
                                "price"))

    out["error"] = "CLOSED_BUT_NO_REPORTED_OR_CONVERGED_OUTCOME"
    return out


#: The fields the venue's own market listing publishes its RULES in. The
#: 2026-09-24 payload carries both `description` and `assetPriceTerms`;
#: names are tried in order and the first present one is used, so a rename
#: costs a NOT_PUBLISHED rather than a silent empty string.
RULES_TEXT_FIELDS = ("description", "assetPriceTerms", "rules",
                     "resolutionSource", "resolutionCriteria")

R_RULES_NOT_LISTED = "VENUE_DOES_NOT_LIST_THIS_SLUG"
R_RULES_NOT_PUBLISHED = "VENUE_PUBLISHES_NO_RULES_TEXT_FOR_THIS_CONTRACT"


def read_rules_text(client, market_slug: str) -> dict:
    """The VENUE'S OWN published rules prose for one contract.

    WHY THIS EXISTS. `bettor_venue_settlement.attest` reported
    OVERTIME_RULE_NOT_ESTABLISHED and VOID_ABANDONMENT_RULE_NOT_ESTABLISHED
    with the detail "no rules text exists on either side" -- and that was
    true of what we HELD, not of what the venue publishes. The listing
    payload carries `description` and `assetPriceTerms`; nobody read them.
    A terminal rule cannot be established from evidence nobody fetched.

    It reads and returns the text. It does NOT interpret it: matching prose
    to a bookmaker rule is `attest`'s job and is done against declared
    patterns, so the interpretation is reviewable separately from the
    fetch.
    """
    from . import pmus

    out = {"reader": READER_VERSION, "slug": market_slug, "ok": False,
           "rules_text": None, "rules_field": None, "fields_present": [],
           "market_type": None, "sports_market_type": None,
           "keys_seen": [], "error": None,
           "source": "pmus:/markets?slug=<slug>:rules_text"}
    try:
        resp = _markets(client, pmus).list({"slug": [market_slug]})
        markets = list((resp or {}).get("markets") or [])
    except Exception as exc:  # noqa: BLE001
        out["error"] = type(exc).__name__
        return out
    if not markets:
        out["error"] = R_RULES_NOT_LISTED
        return out
    m = markets[0]
    out["keys_seen"] = sorted(k for k in m if isinstance(k, str))
    out["market_type"] = (str(m.get("marketType"))
                          if m.get("marketType") is not None else None)
    out["sports_market_type"] = (
        str(m.get("sportsMarketTypeV2") or m.get("sportsMarketType"))
        if (m.get("sportsMarketTypeV2") or m.get("sportsMarketType"))
        is not None else None)
    out["fields_present"] = [f for f in RULES_TEXT_FIELDS
                             if str(m.get(f) or "").strip()]
    field, value = _first(m, RULES_TEXT_FIELDS)
    if field is None or not str(value or "").strip():
        out["error"] = R_RULES_NOT_PUBLISHED
        return out
    out.update(ok=True, rules_field=field, rules_text=str(value).strip())
    return out


def _converged_winner(labels, prices):
    """The label priced at exactly 1 when every other is exactly 0.

    Strict on purpose. A market at 0.99 has not settled; it is nearly
    certain, and those are different facts. Anything less than exact
    convergence returns None and the market stays UNREADABLE.
    """
    if not isinstance(labels, (list, tuple)) or \
            not isinstance(prices, (list, tuple)) or \
            len(labels) != len(prices) or len(labels) < 2:
        return None
    vals = []
    for p in prices:
        try:
            vals.append(float(p))
        except (TypeError, ValueError):
            return None
    ones = [i for i, v in enumerate(vals)
            if abs(v - 1.0) <= SETTLED_PRICE_TOLERANCE]
    zeros = [i for i, v in enumerate(vals)
             if abs(v) <= SETTLED_PRICE_TOLERANCE]
    if len(ones) == 1 and len(zeros) == len(vals) - 1:
        return str(labels[ones[0]])
    return None


def describe() -> dict:
    return {
        "reader": READER_VERSION,
        "reads": ["markets.book (public book)",
                  "markets.list (market listing and resolution)"],
        "submits_orders": False,
        "writes_accounting": False,
        "returns_credentials": False,
        "deployed": False,
        "deployment_requires": ("a separate authorization; writing this "
                                "module activates nothing. See "
                                "PILOT_PROPOSAL.md section 7"),
        "outcome_fields_were_a_hypothesis_and_it_was_wrong": (
            "eight candidate names were guessed; a read on 2026-09-21 "
            "found none of them on the payload. Returning the key names "
            "is what corrected it, in one read"),
        "statuses": [RESOLVED, RESOLVED_DERIVED, PENDING, UNREADABLE,
                     UNMATCHED],
        "outcome_fields_refuted_2026_09_21": list(REFUTED_OUTCOME_FIELDS),
        "resolved_derived_is_not_resolved": (
            "the venue reports no winner on this endpoint. A closed "
            "market whose outcome prices converged to 1 and 0 is an "
            "INFERENCE FROM A PRICE, counted separately and never with "
            "verified semantics"),
    }

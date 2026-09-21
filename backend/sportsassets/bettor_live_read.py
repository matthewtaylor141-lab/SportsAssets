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

# Field names the venue MIGHT use for a resolution. Tried in order.
#
# This list is a HYPOTHESIS, not a specification. The SDK's market
# object is not documented to carry a resolution field and our capture
# has never held one, so the honest behaviour when none is present is
# UNREADABLE plus the key names that WERE present -- which is how the
# list gets corrected, by one read rather than by more guessing.
OUTCOME_FIELDS = ("resolvedOutcome", "resolved_outcome", "winningOutcome",
                  "winning_outcome", "settledOutcome", "settled_outcome",
                  "result", "outcome")
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
    res = pmus.book_read(client, market_slug)
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


def read_resolution(client, market_slug: str) -> dict:
    """Has this market resolved, and to what?

    FOUR OUTCOMES, NOT TWO. `bettor_state_settlements` was empty and the
    acceptance package read that as "nothing has matured". It could
    equally have meant the read failed, or the slug is not listed, or
    nothing ever called the writer -- and it was in fact the last of
    those. So each is returned separately and by name:

      RESOLVED    the venue reports an outcome
      PENDING     listed and open, no outcome yet
      UNREADABLE  the read failed, or the payload carries no field we
                  recognise -- which is a fact about OUR field list,
                  reported with the keys that WERE present
      UNMATCHED   the venue does not list this slug

    A `closed` market with no readable outcome is UNREADABLE, not
    RESOLVED: closed says trading stopped, not who won, and inferring
    the second from the first is the kind of substitution
    `SETTLEMENT_SEMANTICS_STATUS` exists to prevent.
    """
    from . import pmus

    out = {"reader": READER_VERSION, "slug": market_slug,
           "status": UNREADABLE, "outcome": None, "settled_at": None,
           "outcome_field": None, "closed": None, "error": None,
           "keys_seen": []}
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
    field, value = _first(m, OUTCOME_FIELDS)
    if field is not None:
        out.update({"status": RESOLVED, "outcome": str(value),
                    "outcome_field": field})
        _, ts = _first(m, SETTLED_AT_FIELDS)
        out["settled_at"] = str(ts) if ts is not None else None
        return out
    if not out["closed"]:
        out["status"] = PENDING
        return out
    # Closed with no readable outcome.
    out["error"] = "CLOSED_BUT_NO_OUTCOME_FIELD"
    return out


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
        "outcome_fields_are_a_hypothesis": (
            "the SDK is not documented to carry a resolution field and "
            "our capture has never held one. A market with none comes "
            "back UNREADABLE with the keys that WERE present, so one "
            "read corrects the list"),
        "statuses": [RESOLVED, PENDING, UNREADABLE, UNMATCHED],
    }

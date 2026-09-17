"""Section 4. Betfair Exchange historical adapter.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING HERE PURCHASES ANYTHING.

WHY A SEPARATE ADAPTER AND A SEPARATE OBJECT
--------------------------------------------
An exchange price and a sportsbook price are not the same information object,
and the directive is right to insist they never be merged.

A bookmaker quotes ONE number per outcome. It embeds the book's margin, its
risk position, its customer mix and its willingness to be arbitraged. It is a
price the book is prepared to lay at, to customers it chooses, in size it
chooses.

An exchange shows TWO numbers -- a back and a lay -- posted by other
participants, with a spread between them and a size at each. The mid is not a
quote anybody made; it is a construction. Traded volume is a different object
again, and available only for what actually transacted.

Collapsing those into "the price" would throw away the spread, which is the
exchange's own uncertainty, and the back/lay asymmetry, which is where its
information often sits. So this adapter emits each separately and the
interface's ADAPTERS_MAY_NOT list forbids the collapse.

WHAT IS KNOWN
-------------
Timestamped Exchange historical data from May 2015, distributed as bulk files
rather than a REST endpoint. Taken as given from the directive.

WHAT IS NOT KNOWN
-----------------
The package tiers, their contents and their prices were NOT read:
historicaldata.betfair.com and developer.betfair.com are both egress-blocked
here. Which price objects are actually present depends on the package bought
(the basic tier is commonly market-definition plus limited price points, the
advanced tiers carry full depth), so this adapter declares what it CAN emit and
marks per-object availability PACKAGE_DEPENDENT rather than asserting it.
"""

import os

import odds_snapshot_provider as OSP

NOT_IDENTIFIED = "NOT_IDENTIFIED"

PROVIDER = "BETFAIR_EXCHANGE_HISTORICAL"

HISTORICAL_START_DATE = "2015-05"
DISTRIBUTION = "BULK_FILES_NOT_A_REST_ENDPOINT"
FACTS_SOURCE = "SUPPLIED_IN_DIRECTIVE_NOT_RE_DERIVED"

EGRESS_STATUS = {
    "historicaldata.betfair.com": "BLOCKED_NO_RESPONSE_THROUGH_PROXY",
    "developer.betfair.com": "BLOCKED_NO_RESPONSE_THROUGH_PROXY",
    "CHECKED_AT": "2026-09-17",
}

# The five price objects, modelled SEPARATELY. This tuple is the contract.
PRICE_OBJECTS = ("BETFAIR_BACK", "BETFAIR_LAY", "BETFAIR_MID",
                 "BETFAIR_SPREAD", "BETFAIR_TRADED_VOLUME")

PRICE_OBJECT_SEMANTICS = {
    "BETFAIR_BACK": ("best price available to BACK the outcome -- what a "
                     "counterparty is offering to lay to you"),
    "BETFAIR_LAY": ("best price available to LAY the outcome -- what a "
                    "counterparty is offering to back at"),
    "BETFAIR_MID": ("a CONSTRUCTION, not a quote anybody made. Derived from "
                    "back and lay; never treat it as an executable price"),
    "BETFAIR_SPREAD": ("lay minus back. The exchange's own uncertainty, and "
                       "the thing a collapsed mid throws away"),
    "BETFAIR_TRADED_VOLUME": ("matched size, available only for what actually "
                              "transacted. Not a quote at all"),
}

AVAILABILITY = {o: "PACKAGE_DEPENDENT" for o in PRICE_OBJECTS}
AVAILABILITY_NOTE = (
    "which objects a file actually carries depends on the historical package. "
    "The adapter declares what it can emit and refuses per object when the "
    "file does not carry it, rather than deriving a mid from a missing side")

EXCHANGE_IS_NOT_A_BOOKMAKER = True
DO_NOT_MERGE_WITH_SPORTSBOOK_CONSENSUS = True
WHY_NOT_MERGE = (
    "a bookmaker quote embeds one firm's margin and risk position; an exchange "
    "back/lay pair is other participants' orders with a spread between them. "
    "Averaging them produces a number that is neither, and the difference "
    "between them is itself a candidate signal that merging would destroy")

ONE_VENUE_NOT_A_CONSENSUS = (
    "this is a single exchange. It is an excellent independent second opinion "
    "and a poor consensus -- P_EXTERNAL_CONSENSUS needs several books")

CREDENTIAL_ENV_VAR = "BETFAIR_HISTORICAL_PATH"
NO_DATA_IS_BUNDLED = True


def data_root():
    return os.environ.get(CREDENTIAL_ENV_VAR)


def mid_of(back, lay):
    """The constructed mid. Refuses when either side is missing."""
    if back is None or lay is None:
        return None
    return (float(back) + float(lay)) / 2.0


def spread_of(back, lay):
    if back is None or lay is None:
        return None
    return float(lay) - float(back)


def _rows_from_record(rec, requested, event):
    """One exchange record -> up to five separately-labelled rows."""
    snap = rec.get("publish_time") or rec.get("SNAPSHOT_TIMESTAMP")
    out = []
    for runner in (rec.get("runners") or []):
        name = runner.get("name") or runner.get("selectionId")
        back = runner.get("best_back")
        lay = runner.get("best_lay")
        vol = runner.get("traded_volume")
        objs = (("BETFAIR_BACK", back), ("BETFAIR_LAY", lay),
                ("BETFAIR_MID", mid_of(back, lay)),
                ("BETFAIR_SPREAD", spread_of(back, lay)),
                ("BETFAIR_TRADED_VOLUME", vol))
        for obj, val in objs:
            if val is None:
                continue          # refuse the object, do not synthesise it
            out.append(OSP.make_row(
                PROVIDER, rec.get("market_id") or event, obj,
                rec.get("market_family", "H2H"), name, val, snap, requested,
                line=rec.get("line"), home_team=rec.get("home_team"),
                away_team=rec.get("away_team"),
                commence_time=rec.get("market_time"), raw=rec))
    return out


class BetfairHistoricalAdapter(OSP.HistoricalOddsSnapshotProvider):
    """Reads recorded exchange snapshots. `reader` is injected, never built.

    BOOKMAKER carries the PRICE OBJECT name rather than a book name, because
    there is no book -- the field identifies which of the five objects the row
    is, which is what keeps them separate downstream.
    """

    PROVIDER = PROVIDER
    SUPPORTS_MARKET_FAMILIES = ("H2H", "TOTALS", "SPREAD")
    HISTORICAL_START_DATE = HISTORICAL_START_DATE
    SNAPSHOT_RESOLUTION = "STREAM_PUBLISH_TIMES_PACKAGE_DEPENDENT"
    POINT_IN_TIME_GUARANTEE = "PUBLISH_TIME_PER_RECORD"

    def __init__(self, reader=None):
        self.reader = reader

    def fetch(self, sport, league, event, requested_as_of_timestamp,
              market_family):
        if self.reader is None:
            return [], {"STATUS": OSP.REFUSAL_EGRESS_BLOCKED,
                        "DETAIL": EGRESS_STATUS,
                        "DATA_ROOT_ENV": CREDENTIAL_ENV_VAR,
                        "NO_DATA_IS_BUNDLED": NO_DATA_IS_BUNDLED}
        req = OSP._parse(requested_as_of_timestamp)
        records = self.reader(sport=sport, league=league, event=event,
                              market_family=market_family) or []
        eligible = [r for r in records
                    if OSP._parse(r.get("publish_time")) is not None
                    and OSP._parse(r.get("publish_time")) <= req]
        if not eligible:
            return [], {"STATUS": OSP.REFUSAL_NO_SNAPSHOT,
                        "RECORDS_SEEN": len(records),
                        "ALL_AFTER_REQUESTED_TIME": bool(records)}
        latest = max(eligible, key=lambda r: OSP._parse(r["publish_time"]))
        rows, meta = self._guard(_rows_from_record(latest, req, event), req)
        meta["STATUS"] = "OK" if rows else OSP.REFUSAL_NO_SNAPSHOT
        meta["SNAPSHOT_TIMESTAMP"] = str(latest.get("publish_time"))
        meta["PRICE_OBJECTS_EMITTED"] = sorted({r["BOOKMAKER"] for r in rows})
        meta["EXCHANGE_IS_NOT_A_BOOKMAKER"] = EXCHANGE_IS_NOT_A_BOOKMAKER
        return rows, meta


def describe():
    return {
        "PROVIDER": PROVIDER,
        "HISTORICAL_START_DATE": HISTORICAL_START_DATE,
        "DISTRIBUTION": DISTRIBUTION,
        "FACTS_SOURCE": FACTS_SOURCE,
        "EGRESS_STATUS": EGRESS_STATUS,
        "PRICE_OBJECTS": PRICE_OBJECTS,
        "PRICE_OBJECT_SEMANTICS": dict(PRICE_OBJECT_SEMANTICS),
        "AVAILABILITY": dict(AVAILABILITY),
        "AVAILABILITY_NOTE": AVAILABILITY_NOTE,
        "EXCHANGE_IS_NOT_A_BOOKMAKER": EXCHANGE_IS_NOT_A_BOOKMAKER,
        "DO_NOT_MERGE_WITH_SPORTSBOOK_CONSENSUS":
            DO_NOT_MERGE_WITH_SPORTSBOOK_CONSENSUS,
        "WHY_NOT_MERGE": WHY_NOT_MERGE,
        "ONE_VENUE_NOT_A_CONSENSUS": ONE_VENUE_NOT_A_CONSENSUS,
        "NOTHING_WAS_PURCHASED": True,
    }

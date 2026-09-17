"""Section 2. HistoricalOddsSnapshotProvider -- the provider-neutral interface.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING IN THIS MODULE PURCHASES ANYTHING OR SENDS A CREDENTIALLED REQUEST.

THE INVARIANT, AND IT IS THE WHOLE POINT
----------------------------------------
    SNAPSHOT_TIMESTAMP <= REQUESTED_AS_OF_TIMESTAMP

No future snapshot may ever be returned. Every previous as-of failure in this
programme came from a value that was true but not yet knowable, and an odds
feed is the easiest place in the system to reintroduce that: ask for "the odds
for this match", get today's closing line, and silently compare a forecast
against a price formed after the forecast was made.

So the check is not advice to callers. It is enforced in `validate`, every
adapter must route its rows through it, and a violating row is REFUSED rather
than trimmed or warned about. AGE_SECONDS is recorded on every row precisely so
a caller can see how stale the honest answer was; a small age is good data and
a large age is a real limitation, but a NEGATIVE age is a bug.

WHY PROVIDER-NEUTRAL
--------------------
Two adapters exist already (The Odds API, Betfair) and they disagree about
almost everything: one returns bookmaker prices, the other an exchange's back
and lay; one is a REST endpoint, the other bulk files. The experiment must not
have to know which. It asks for a snapshot at or before T and gets rows in one
shape, with PROVIDER carried so a later analysis can separate them again.

WHAT AN ADAPTER MAY NOT DO
--------------------------
  - return a row it did not receive (no fabrication, no interpolation)
  - invent a snapshot timestamp it was not given
  - silently substitute a later snapshot for a missing earlier one
  - collapse back and lay into one "price"
A missing snapshot is NO_SNAPSHOT_AT_OR_BEFORE_T. That is an answer.
"""

import datetime
import hashlib
import json

NOT_IDENTIFIED = "NOT_IDENTIFIED"

INTERFACE_NAME = "HistoricalOddsSnapshotProvider"

THE_INVARIANT = "SNAPSHOT_TIMESTAMP <= REQUESTED_AS_OF_TIMESTAMP"
NO_FUTURE_SNAPSHOT_MAY_EVER_BE_RETURNED = True

REQUEST_FIELDS = ("SPORT", "LEAGUE", "EVENT", "REQUESTED_AS_OF_TIMESTAMP",
                  "MARKET_FAMILY")

RESPONSE_FIELDS = (
    "PROVIDER", "EVENT_ID", "BOOKMAKER", "MARKET", "LINE", "OUTCOME", "PRICE",
    "SNAPSHOT_TIMESTAMP", "REQUESTED_TIMESTAMP", "AGE_SECONDS",
    "HOME_TEAM", "AWAY_TEAM", "COMMENCE_TIME", "RAW_RESPONSE_HASH",
)

MARKET_FAMILIES = ("H2H", "SPREAD", "TOTALS", "PERIOD", "PROP")

REFUSAL_NO_SNAPSHOT = "NO_SNAPSHOT_AT_OR_BEFORE_T"
REFUSAL_FUTURE_SNAPSHOT = "REFUSED_SNAPSHOT_AFTER_REQUESTED_TIME"
REFUSAL_NO_CREDENTIAL = "NO_CREDENTIAL_CONFIGURED"
REFUSAL_EGRESS_BLOCKED = "EGRESS_BLOCKED_BY_NETWORK_POLICY"

ADAPTERS_MAY_NOT = (
    "RETURN_A_ROW_THEY_DID_NOT_RECEIVE",
    "INVENT_A_SNAPSHOT_TIMESTAMP",
    "SUBSTITUTE_A_LATER_SNAPSHOT_FOR_A_MISSING_EARLIER_ONE",
    "COLLAPSE_BACK_AND_LAY_INTO_ONE_PRICE",
)

NOTHING_HERE_PURCHASES_ANYTHING = True
NO_CREDENTIAL_IS_READ_FROM_DISK = True
CREDENTIALS_COME_FROM_ENV_ONLY = True


def _parse(ts):
    if ts is None:
        return None
    if isinstance(ts, datetime.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=datetime.timezone.utc)
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(s)
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def response_hash(raw):
    """A stable hash of the provider's raw payload, so a row is auditable."""
    if raw is None:
        return NOT_IDENTIFIED
    if not isinstance(raw, (str, bytes)):
        raw = json.dumps(raw, sort_keys=True, default=str)
    if isinstance(raw, str):
        raw = raw.encode()
    return hashlib.sha256(raw).hexdigest()


def make_row(provider, event_id, bookmaker, market, outcome, price,
             snapshot_timestamp, requested_timestamp, line=None,
             home_team=None, away_team=None, commence_time=None, raw=None):
    """Build one response row. AGE_SECONDS is derived, never supplied."""
    snap = _parse(snapshot_timestamp)
    req = _parse(requested_timestamp)
    age = (req - snap).total_seconds() if (snap and req) else None
    return {
        "PROVIDER": provider,
        "EVENT_ID": event_id,
        "BOOKMAKER": bookmaker,
        "MARKET": market,
        "LINE": line,
        "OUTCOME": outcome,
        "PRICE": price,
        "SNAPSHOT_TIMESTAMP": snap.isoformat() if snap else NOT_IDENTIFIED,
        "REQUESTED_TIMESTAMP": req.isoformat() if req else NOT_IDENTIFIED,
        "AGE_SECONDS": age,
        "HOME_TEAM": home_team or NOT_IDENTIFIED,
        "AWAY_TEAM": away_team or NOT_IDENTIFIED,
        "COMMENCE_TIME": (_parse(commence_time).isoformat()
                          if _parse(commence_time) else NOT_IDENTIFIED),
        "RAW_RESPONSE_HASH": response_hash(raw),
    }


def validate(row):
    """Enforce the invariant and the schema. Returns (ok, reason)."""
    for f in RESPONSE_FIELDS:
        if f not in row:
            return False, "MISSING_FIELD_%s" % f
    snap = _parse(row.get("SNAPSHOT_TIMESTAMP"))
    req = _parse(row.get("REQUESTED_TIMESTAMP"))
    if snap is None or req is None:
        return False, "UNPARSEABLE_TIMESTAMP"
    if snap > req:
        return False, REFUSAL_FUTURE_SNAPSHOT
    age = row.get("AGE_SECONDS")
    if age is None or age < 0:
        return False, "NEGATIVE_OR_MISSING_AGE"
    if row.get("PRICE") is None:
        return False, "MISSING_PRICE"
    return True, "OK"


def validate_all(rows):
    """Partition rows into admitted and refused, with reasons. Fail closed."""
    ok, bad = [], []
    for r in rows or ():
        good, why = validate(r)
        (ok if good else bad).append(r if good else {"ROW": r, "REASON": why})
    return {"ADMITTED": ok, "REFUSED": bad,
            "ADMITTED_COUNT": len(ok), "REFUSED_COUNT": len(bad),
            "INVARIANT": THE_INVARIANT}


class HistoricalOddsSnapshotProvider(object):
    """The interface every adapter implements.

    `fetch` returns (rows, meta). It NEVER raises on a missing snapshot: a
    refusal is data, and the experiment needs to count refusals as carefully as
    it counts prices.
    """

    PROVIDER = "ABSTRACT"
    SUPPORTS_MARKET_FAMILIES = ()
    HISTORICAL_START_DATE = NOT_IDENTIFIED
    SNAPSHOT_RESOLUTION = NOT_IDENTIFIED
    POINT_IN_TIME_GUARANTEE = NOT_IDENTIFIED

    def fetch(self, sport, league, event, requested_as_of_timestamp,
              market_family):
        raise NotImplementedError

    def _guard(self, rows, requested_as_of_timestamp):
        """Every adapter routes its rows through this before returning them."""
        v = validate_all(rows)
        return v["ADMITTED"], {
            "REFUSED_COUNT": v["REFUSED_COUNT"],
            "REFUSED": v["REFUSED"],
            "INVARIANT_ENFORCED": THE_INVARIANT,
            "REQUESTED_AS_OF": str(requested_as_of_timestamp),
        }

    def describe(self):
        return {
            "PROVIDER": self.PROVIDER,
            "SUPPORTS_MARKET_FAMILIES": self.SUPPORTS_MARKET_FAMILIES,
            "HISTORICAL_START_DATE": self.HISTORICAL_START_DATE,
            "SNAPSHOT_RESOLUTION": self.SNAPSHOT_RESOLUTION,
            "POINT_IN_TIME_GUARANTEE": self.POINT_IN_TIME_GUARANTEE,
        }


class FixtureProvider(HistoricalOddsSnapshotProvider):
    """A provider backed by recorded fixtures. For tests and dry runs.

    It holds a list of (snapshot_timestamp, rows) and returns the LATEST
    snapshot at or before the request -- the same selection rule a real
    historical endpoint documents -- or a refusal when none exists. It cannot
    invent a snapshot because it only ever returns what it was given.
    """

    PROVIDER = "FIXTURE"
    SUPPORTS_MARKET_FAMILIES = MARKET_FAMILIES
    POINT_IN_TIME_GUARANTEE = "BY_CONSTRUCTION_FIXTURES_ARE_IMMUTABLE"

    def __init__(self, snapshots=None):
        self.snapshots = sorted(snapshots or [], key=lambda s: s[0])

    def fetch(self, sport, league, event, requested_as_of_timestamp,
              market_family):
        req = _parse(requested_as_of_timestamp)
        if req is None:
            return [], {"STATUS": "UNPARSEABLE_REQUESTED_TIMESTAMP"}
        chosen = None
        for ts, rows in self.snapshots:
            if _parse(ts) is not None and _parse(ts) <= req:
                chosen = (ts, rows)
        if chosen is None:
            return [], {"STATUS": REFUSAL_NO_SNAPSHOT,
                        "REQUESTED_AS_OF": req.isoformat(),
                        "EARLIEST_AVAILABLE": (str(self.snapshots[0][0])
                                               if self.snapshots
                                               else NOT_IDENTIFIED)}
        ts, raw_rows = chosen
        out = []
        for r in raw_rows:
            if market_family and r.get("MARKET") and \
                    r["MARKET"].upper() != market_family.upper():
                continue
            out.append(make_row(
                self.PROVIDER, r.get("EVENT_ID", event), r.get("BOOKMAKER"),
                r.get("MARKET"), r.get("OUTCOME"), r.get("PRICE"),
                ts, req, line=r.get("LINE"), home_team=r.get("HOME_TEAM"),
                away_team=r.get("AWAY_TEAM"),
                commence_time=r.get("COMMENCE_TIME"), raw=r))
        rows, meta = self._guard(out, req)
        meta["STATUS"] = "OK" if rows else REFUSAL_NO_SNAPSHOT
        meta["SNAPSHOT_TIMESTAMP"] = str(ts)
        return rows, meta


def describe():
    return {
        "INTERFACE_NAME": INTERFACE_NAME,
        "THE_INVARIANT": THE_INVARIANT,
        "NO_FUTURE_SNAPSHOT_MAY_EVER_BE_RETURNED":
            NO_FUTURE_SNAPSHOT_MAY_EVER_BE_RETURNED,
        "REQUEST_FIELDS": REQUEST_FIELDS,
        "RESPONSE_FIELDS": RESPONSE_FIELDS,
        "MARKET_FAMILIES": MARKET_FAMILIES,
        "ADAPTERS_MAY_NOT": ADAPTERS_MAY_NOT,
        "NOTHING_HERE_PURCHASES_ANYTHING": NOTHING_HERE_PURCHASES_ANYTHING,
        "CREDENTIALS_COME_FROM_ENV_ONLY": CREDENTIALS_COME_FROM_ENV_ONLY,
    }

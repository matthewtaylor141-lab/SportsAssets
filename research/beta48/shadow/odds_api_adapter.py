"""Section 3. The Odds API adapter. Built WITHOUT purchasing credentials.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING HERE PURCHASES ANYTHING. No credentialled request is ever sent.

WHAT IS KNOWN, AND FROM WHERE
-----------------------------
Four facts about the provider were supplied in the directive and are treated as
given rather than re-derived:

    historical featured-market data from 2020-06-06
    5-minute snapshots from September 2022
    the historical endpoint returns the closest snapshot at or EARLIER than
        the requested time
    historical access is paid

That last selection rule is the one that matters, because it is exactly this
programme's invariant already implemented on the provider's side. The adapter
still enforces it locally: a provider promise is not a guarantee, and a single
mis-parsed timestamp would reintroduce lookahead silently.

WHAT IS NOT KNOWN
-----------------
The credit formula and the plan prices were NOT verified. Both hosts are
egress-blocked from this environment (api.the-odds-api.com and the-odds-api.com
return no response through the proxy), so the documented quota arithmetic could
not be read. It is therefore a PARAMETER of this module, declared once, labelled
NOT_VERIFIED, and used by the procurement calculator through that parameter so
the whole cost model can be re-run against the real figure by editing one
constant.

That is deliberate. A cost estimate built on a half-remembered formula, with no
marker saying so, is worse than no estimate.
"""

import os

import odds_snapshot_provider as OSP

NOT_IDENTIFIED = "NOT_IDENTIFIED"

PROVIDER = "THE_ODDS_API"

# --- supplied in the directive -------------------------------------------
HISTORICAL_START_DATE = "2020-06-06"
FIVE_MINUTE_SNAPSHOTS_FROM = "2022-09"
SELECTION_RULE = "CLOSEST_SNAPSHOT_AT_OR_EARLIER_THAN_REQUESTED_TIME"
HISTORICAL_ACCESS_IS_PAID = True
FACTS_SOURCE = "SUPPLIED_IN_DIRECTIVE_NOT_RE_DERIVED"

# --- NOT verified in this environment -------------------------------------
EGRESS_STATUS = {
    "api.the-odds-api.com": "BLOCKED_NO_RESPONSE_THROUGH_PROXY",
    "the-odds-api.com": "BLOCKED_NO_RESPONSE_THROUGH_PROXY",
    "CHECKED_AT": "2026-09-17",
}

CREDIT_FORMULA = {
    "FORMULA": "CREDITS_PER_REQUEST = N_MARKETS * N_REGIONS * HISTORICAL_MULTIPLIER",
    "HISTORICAL_MULTIPLIER": 10,
    "STATUS": "NOT_VERIFIED_AGAINST_LIVE_DOCUMENTATION",
    "WHY_NOT_VERIFIED": (
        "both provider hosts are egress-blocked from this environment, so the "
        "published quota arithmetic could not be read"),
    "HOW_TO_CORRECT": (
        "edit HISTORICAL_MULTIPLIER and FORMULA here; odds_procurement reads "
        "them through this dict and every scenario re-computes"),
    "TREAT_AS": "PLANNING_PARAMETER_NOT_A_QUOTED_PRICE",
}

PLAN_PRICES = {
    "STATUS": "NOT_VERIFIED",
    "NOTE": ("no plan price is recorded because none was read from the "
             "provider. The procurement document reports credit counts and "
             "leaves the price column explicitly empty rather than guessing"),
}

# --- what the experiment needs --------------------------------------------
SUPPORTED_MARKET_FAMILIES = ("H2H", "SPREAD", "TOTALS")
LATER_MARKET_FAMILIES = ("PERIOD", "PROP")

SPORT_KEYS = {
    "epl": "soccer_epl", "elc": "soccer_efl_champ",
    "lal": "soccer_spain_la_liga", "sea": "soccer_italy_serie_a",
    "bun": "soccer_germany_bundesliga", "fl1": "soccer_france_ligue_one",
    "ere": "soccer_netherlands_eredivisie", "por": "soccer_portugal_primeira_liga",
}
SPORT_KEY_STATUS = "CONVENTIONAL_KEYS_NOT_VERIFIED_AGAINST_THE_LIVE_SPORTS_LIST"

MARKET_KEYS = {"H2H": "h2h", "SPREAD": "spreads", "TOTALS": "totals"}

DEFAULT_REGIONS = ("uk", "eu")
WHY_THOSE_REGIONS = (
    "the eight evaluated leagues are European, and UK/EU books are the ones "
    "that price them deepest. US books are available and would add breadth at "
    "linear credit cost")

CREDENTIAL_ENV_VAR = "THE_ODDS_API_KEY"
NO_CREDENTIAL_IS_BUNDLED = True


def credential_present():
    return bool(os.environ.get(CREDENTIAL_ENV_VAR))


def credits_for(n_markets, n_regions=len(DEFAULT_REGIONS), historical=True):
    """Credits for ONE request, under the declared (unverified) formula."""
    c = max(1, int(n_markets)) * max(1, int(n_regions))
    if historical:
        c *= CREDIT_FORMULA["HISTORICAL_MULTIPLIER"]
    return c


def _outcomes_from_payload(payload, market_family, requested, event):
    """Flatten the documented response shape into interface rows.

    The shape is bookmakers[] -> markets[] -> outcomes[], with a
    `last_update` per bookmaker-market and a `timestamp` on the historical
    envelope. The envelope timestamp is the SNAPSHOT time and is what the
    invariant is checked against; `last_update` is recorded but never
    substituted for it, because a bookmaker that has not moved for an hour
    still appears in a fresh snapshot.
    """
    snap = payload.get("timestamp")
    data = payload.get("data") or {}
    if isinstance(data, list):
        data = data[0] if data else {}
    home, away = data.get("home_team"), data.get("away_team")
    commence = data.get("commence_time")
    eid = data.get("id") or event
    want = MARKET_KEYS.get(market_family.upper(), market_family.lower())
    rows = []
    for bk in (data.get("bookmakers") or []):
        for mk in (bk.get("markets") or []):
            if mk.get("key") != want:
                continue
            for oc in (mk.get("outcomes") or []):
                rows.append(OSP.make_row(
                    PROVIDER, eid, bk.get("key"), market_family.upper(),
                    oc.get("name"), oc.get("price"), snap, requested,
                    line=oc.get("point"), home_team=home, away_team=away,
                    commence_time=commence, raw=payload))
    return rows


class TheOddsApiAdapter(OSP.HistoricalOddsSnapshotProvider):
    """Provider-neutral adapter. `transport` is injected, never constructed.

    With no transport and no credential the adapter REFUSES rather than
    returning an empty list that a caller might read as "no odds existed".
    Those are different answers and the distinction is the point.
    """

    PROVIDER = PROVIDER
    SUPPORTS_MARKET_FAMILIES = SUPPORTED_MARKET_FAMILIES
    HISTORICAL_START_DATE = HISTORICAL_START_DATE
    SNAPSHOT_RESOLUTION = "5_MINUTES_FROM_2022_09_COARSER_BEFORE"
    POINT_IN_TIME_GUARANTEE = SELECTION_RULE

    def __init__(self, transport=None, regions=DEFAULT_REGIONS):
        self.transport = transport
        self.regions = tuple(regions)

    def fetch(self, sport, league, event, requested_as_of_timestamp,
              market_family):
        fam = (market_family or "H2H").upper()
        if fam not in SUPPORTED_MARKET_FAMILIES:
            return [], {"STATUS": "UNSUPPORTED_MARKET_FAMILY",
                        "FAMILY": fam,
                        "SUPPORTED": SUPPORTED_MARKET_FAMILIES,
                        "LATER": LATER_MARKET_FAMILIES}
        if self.transport is None:
            return [], {"STATUS": OSP.REFUSAL_EGRESS_BLOCKED,
                        "DETAIL": EGRESS_STATUS,
                        "NOTE": ("no transport injected; the adapter refuses "
                                 "rather than returning an empty result that "
                                 "could be read as 'no odds existed'")}
        if not credential_present():
            return [], {"STATUS": OSP.REFUSAL_NO_CREDENTIAL,
                        "ENV_VAR": CREDENTIAL_ENV_VAR,
                        "NOTHING_WAS_PURCHASED": True}
        req = OSP._parse(requested_as_of_timestamp)
        payload = self.transport(
            sport_key=SPORT_KEYS.get(league, sport),
            regions=",".join(self.regions),
            markets=MARKET_KEYS[fam],
            date=req.isoformat() if req else requested_as_of_timestamp,
            event_id=event)
        if not payload:
            return [], {"STATUS": OSP.REFUSAL_NO_SNAPSHOT}
        rows = _outcomes_from_payload(payload, fam, req, event)
        rows, meta = self._guard(rows, req)
        meta["STATUS"] = "OK" if rows else OSP.REFUSAL_NO_SNAPSHOT
        meta["CREDITS_CHARGED_ESTIMATE"] = credits_for(1, len(self.regions))
        meta["CREDIT_FORMULA_STATUS"] = CREDIT_FORMULA["STATUS"]
        return rows, meta


def describe():
    return {
        "PROVIDER": PROVIDER,
        "HISTORICAL_START_DATE": HISTORICAL_START_DATE,
        "FIVE_MINUTE_SNAPSHOTS_FROM": FIVE_MINUTE_SNAPSHOTS_FROM,
        "SELECTION_RULE": SELECTION_RULE,
        "HISTORICAL_ACCESS_IS_PAID": HISTORICAL_ACCESS_IS_PAID,
        "FACTS_SOURCE": FACTS_SOURCE,
        "EGRESS_STATUS": EGRESS_STATUS,
        "CREDIT_FORMULA": dict(CREDIT_FORMULA),
        "PLAN_PRICES": dict(PLAN_PRICES),
        "SUPPORTED_MARKET_FAMILIES": SUPPORTED_MARKET_FAMILIES,
        "SPORT_KEY_STATUS": SPORT_KEY_STATUS,
        "CREDENTIAL_ENV_VAR": CREDENTIAL_ENV_VAR,
        "NO_CREDENTIAL_IS_BUNDLED": NO_CREDENTIAL_IS_BUNDLED,
        "NOTHING_WAS_PURCHASED": True,
    }

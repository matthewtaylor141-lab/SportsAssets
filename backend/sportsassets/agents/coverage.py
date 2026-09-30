"""DEREK'S COVERAGE CENSUS: WHAT THE POLYMARKET US CATALOGUE LISTS, AND WHERE
EACH CONTRACT STANDS.

A MEASURED census, not a claim. It reads only what is already stored:

  * `us_premap`             the venue's own catalogue, written by the premap
                            sweep (workers/premap.py). POLYMARKET US ONLY: the
                            global/international `markets` table is never
                            counted here and never substituted for a US
                            contract.
  * `ingestion_state`       whether that sweep has run recently (premap_last,
                            premap_last_fast), with the same freshness bound
                            the pair observer uses.
  * `external_valuations`   what the entry lane valued (both purposes).
  * `ext_candidate_outcomes` where each provider event stopped, per cycle.
  * `derek_entry_decisions` what Derek decided.

No venue is read and no Pinnacle price is invented for an uncovered market.

EVERY LISTED CONTRACT LANDS IN EXACTLY ONE FINAL STATE, so the states sum to
`listed` (asserted in `census()` and by its test):

    OUTSIDE_MANDATE    not a real sports fixture, a simulated one, a sport the
                       entry lane is not configured for, or a market type /
                       period the mandate does not cover -- each by name
    UNSUPPORTED        inside the mandate, but no probability source or no
                       settlement interpretation exists for it -- by name
    BLOCKED            supported, reached by the lane, stopped before an entry
                       decision -- by the lane's own refusal name
    EVALUATED          reached an entry decision (an ENTRY_DECISION valuation)
    NOT_YET_EVALUATED  supported, nothing recorded for it in the window

and the FUNNEL counts are listed >= within_mandate >= supported >=
receiving_current_data >= evaluated.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

VERSION = "DEREK_COVERAGE_CENSUS_V1"

#: How far back the lane's records count as "current" for a contract: two
#: scheduled cycles (ext_pinnacle_loop.CYCLE_S = 900 s).
CURRENT_WINDOW_S = 2 * 900.0

S_OUTSIDE = "OUTSIDE_MANDATE"
S_UNSUPPORTED = "UNSUPPORTED"
S_BLOCKED = "BLOCKED"
S_EVALUATED = "EVALUATED"
S_NOT_YET = "NOT_YET_EVALUATED"
FINAL_STATES = (S_OUTSIDE, S_UNSUPPORTED, S_BLOCKED, S_EVALUATED, S_NOT_YET)

X_NOT_A_FIXTURE = "NOT_A_SPORTS_FIXTURE"
X_SIMULATED = "SIMULATED_OR_ELECTRONIC_FIXTURE"
X_REALISM_UNKNOWN = "REALISM_NOT_ESTABLISHED"
X_SPORT = "SPORT_NOT_IN_THE_ENTRY_MANDATE"
X_MARKET_TYPE = "MARKET_TYPE_NOT_IN_THE_ENTRY_MANDATE"
X_PERIOD = "PERIOD_NOT_IN_THE_ENTRY_MANDATE"
X_NO_PROBABILITY = "NO_PROBABILITY_SOURCE_FOR_THIS_MARKET"
X_NO_SETTLEMENT = "NO_SETTLEMENT_INTERPRETATION_FOR_THIS_MARKET"
X_CALIBRATION_ONLY = "VALUED_FOR_CALIBRATION_ONLY"

SAMPLE_PER_STATE = 5

CATALOGUE_SQL = """
    SELECT market_slug, max(event_slug) AS event_slug,
           max(event_title) AS event_title, max(question) AS question,
           max(sports_type) AS sports_type, max(game_start) AS game_start,
           max(updated_at) AS updated_at
      FROM us_premap
     WHERE market_slug IS NOT NULL
       AND updated_at > to_timestamp($1)
     GROUP BY market_slug
"""


def mandate() -> dict:
    """THE APPROVED ENTRY MANDATE, read from the lane that executes it:
    the sports the entry lane is configured to value (ext_pinnacle_loop.SPORTS),
    full-game moneyline winners, real fixtures, Polymarket US."""
    from ..workers import ext_pinnacle_loop as L
    fams = sorted({fam for _key, fam in L.SPORTS})
    return {"families": fams,
            "sport_keys": sorted({key for key, _fam in L.SPORTS}),
            "kind": "MONEYLINE", "period": "FULL",
            "catalogue": "POLYMARKET_US (us_premap)",
            "source": "ext_pinnacle_loop.SPORTS"}


def classify_listing(row: dict, *, mand: dict) -> tuple:
    """(state, reason) for one catalogue contract BEFORE the lane's records are
    consulted: OUTSIDE_MANDATE / UNSUPPORTED with a name, or (None, None) when
    it is inside the mandate and supported. Pure."""
    from .. import bettor_funded_hedge_supply as HSUP
    from .. import bettor_indirect_structures as IS
    from .. import bettor_pinnacle_devig as devig
    from .. import bettor_venue_realism as vreal
    from .. import bettor_venue_settlement as vset
    from ..workers import ext_pinnacle_loop as L

    st = str(row.get("sports_type") or "").strip().lower()
    cls = vreal.classify(row)
    if cls.get("verdict") == vreal.SIMULATED:
        return S_OUTSIDE, X_SIMULATED
    if not st or st.split("_")[0] in ("futures",) or \
            st in vreal.NOT_A_FIXTURE_SPORTS_TYPES:
        return S_OUTSIDE, X_NOT_A_FIXTURE
    if cls.get("verdict") != vreal.REAL:
        return S_OUTSIDE, X_REALISM_UNKNOWN
    fam = st.split("_")[0]
    if fam not in mand["families"]:
        return S_OUTSIDE, "%s:%s" % (X_SPORT, fam)
    kind = HSUP.derive_kind(row)
    if kind.get("kind") != IS.KIND_MONEYLINE:
        return S_OUTSIDE, "%s:%s" % (X_MARKET_TYPE, st)
    if kind.get("period") != IS.PERIOD_FULL:
        return S_OUTSIDE, "%s:%s" % (X_PERIOD, kind.get("period"))
    if (fam, "h2h") not in devig.SUPPORTED:
        return S_UNSUPPORTED, "%s:%s" % (X_NO_PROBABILITY, fam)
    if fam not in L.PINNACLE_SETTLEMENT or \
            vset.BOOK_SETTLEMENT.get(fam) is None:
        return S_UNSUPPORTED, "%s:%s" % (X_NO_SETTLEMENT, fam)
    return None, None


async def _regclass(conn, name) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        name))
    except Exception:                                          # noqa: BLE001
        return False


def _sweep_age(raw, now) -> float | None:
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        v = raw
    at = None
    if isinstance(v, dict):
        at = v.get("at") or v.get("finished_at") or v.get("ts")
    elif v is not None:
        at = v
    try:
        return round(float(now) - float(at), 1)
    except (TypeError, ValueError):
        return None


async def census(conn, *, now: float) -> dict:
    """THE CENSUS AT `now`. Never raises: an unreadable source is named."""
    from .. import bettor_pair_observations as PO

    at = float(now)
    mand = mandate()
    out: dict[str, Any] = {"version": VERSION, "at": at, "mandate": mand,
                           "polymarket_us_only": True,
                           "never_substituted": (
                               "international Polymarket contracts (the "
                               "`markets` table) are not counted and never "
                               "stand in for a US contract"),
                           "no_price_invented": (
                               "a contract the lane never priced has no "
                               "probability here; it is NOT_YET_EVALUATED or "
                               "blocked by name")}
    if not await _regclass(conn, "us_premap"):
        return dict(out, ok=False, refusal="VENUE_CATALOGUE_NOT_IN_DB",
                    why="us_premap is absent: the premap sweep has not "
                        "created the catalogue in this database")
    try:
        sweeps = {r["key"]: r["value"] for r in await conn.fetch(
            "SELECT key, value FROM ingestion_state "
            " WHERE key IN ('premap_last', 'premap_last_fast')")}
    except Exception:                                          # noqa: BLE001
        sweeps = {}
    ages = {k: _sweep_age(v, at) for k, v in sweeps.items()}
    known = [a for a in ages.values() if a is not None]
    out["catalogue_sweep_age_s"] = ages
    out["catalogue_sweep_fresh"] = bool(
        known and min(known) <= PO.CATALOGUE_SWEEP_FRESH_S)
    out["catalogue_open_proxy"] = (
        "a contract counts as listed when the sweep re-saw it within %d s "
        "(the sweep writes open markets and prunes closed ones later)"
        % PO.CATALOGUE_RESEEN_S)
    try:
        rows = [dict(r) for r in await conn.fetch(
            CATALOGUE_SQL, at - PO.CATALOGUE_RESEEN_S)]
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, ok=False, refusal="VENUE_CATALOGUE_READ_FAILED",
                    error=type(exc).__name__)

    since = at - CURRENT_WINDOW_S
    valued: dict = {}
    if await _regclass(conn, "external_valuations"):
        for r in await conn.fetch(
                "SELECT DISTINCT ON (us_market_slug) us_market_slug, id, "
                "       record_purpose, refusals, admissible "
                "  FROM external_valuations "
                " WHERE us_market_slug IS NOT NULL "
                "   AND decided_at > to_timestamp($1) "
                "   AND decided_at <= to_timestamp($2) "
                " ORDER BY us_market_slug, "
                "          (record_purpose = 'ENTRY_DECISION') DESC, id DESC",
                since, at + 1.0):
            valued[r["us_market_slug"]] = dict(r)
    touched: dict = {}
    if await _regclass(conn, "ext_candidate_outcomes"):
        for r in await conn.fetch(
                "SELECT DISTINCT ON (us_market_slug) us_market_slug, stage, "
                "       outcome, first_refusal "
                "  FROM ext_candidate_outcomes "
                " WHERE us_market_slug IS NOT NULL "
                "   AND cycle_at > to_timestamp($1) "
                "   AND cycle_at <= to_timestamp($2) "
                " ORDER BY us_market_slug, cycle_at DESC, id DESC",
                since, at + 1.0):
            touched[r["us_market_slug"]] = dict(r)
    decided: dict = {}
    if await _regclass(conn, "derek_entry_decisions"):
        for r in await conn.fetch(
                "SELECT DISTINCT ON (us_market_slug) us_market_slug, "
                "       decision_id, verdict, refusal "
                "  FROM derek_entry_decisions "
                " WHERE us_market_slug IS NOT NULL "
                "   AND decided_at > to_timestamp($1) "
                " ORDER BY us_market_slug, decided_at DESC", since):
            decided[r["us_market_slug"]] = dict(r)

    states = {s: 0 for s in FINAL_STATES}
    reasons: dict = {s: {} for s in FINAL_STATES}
    sample: dict = {s: [] for s in FINAL_STATES}
    funnel = {"listed": 0, "within_mandate": 0, "supported": 0,
              "receiving_current_data": 0, "evaluated": 0}
    fixtures = {s: set() for s in FINAL_STATES}
    listed_fixtures: set = set()
    derek_verdicts = {"ENTER": 0, "REFUSE": 0}
    for row in rows:
        slug = row["market_slug"]
        fx = str(row.get("event_slug") or slug)
        listed_fixtures.add(fx)
        funnel["listed"] += 1
        state, reason = classify_listing(row, mand=mand)
        if state is None:
            funnel["within_mandate"] += 1
            funnel["supported"] += 1
            v, t = valued.get(slug), touched.get(slug)
            if v is not None:
                funnel["receiving_current_data"] += 1
            if v is not None and v.get("record_purpose") == "ENTRY_DECISION":
                state = S_EVALUATED
                funnel["evaluated"] += 1
                d = decided.get(slug)
                reason = ("DEREK:%s" % (d["verdict"] if d else "NOT_RECORDED"))
                if d:
                    derek_verdicts[d["verdict"]] = \
                        derek_verdicts.get(d["verdict"], 0) + 1
            elif v is not None:
                state = S_BLOCKED
                reason = "%s:%s" % (X_CALIBRATION_ONLY, (
                    list(v.get("refusals") or []) or ["UNNAMED"])[0])
            elif t is not None:
                state = S_BLOCKED
                reason = str(t.get("first_refusal") or t.get("outcome")
                             or "UNNAMED")
            else:
                state = S_NOT_YET
                reason = "NO_LANE_RECORD_IN_THE_LAST_%dS" % CURRENT_WINDOW_S
        elif state == S_UNSUPPORTED:
            funnel["within_mandate"] += 1
        states[state] += 1
        fixtures[state].add(fx)
        reasons[state][reason] = reasons[state].get(reason, 0) + 1
        if len(sample[state]) < SAMPLE_PER_STATE:
            sample[state].append({"us_market_slug": slug, "fixture": fx,
                                  "sports_type": row.get("sports_type"),
                                  "reason": reason})
    total = sum(states.values())
    blocked_by_reason = {}
    for s in (S_OUTSIDE, S_UNSUPPORTED, S_BLOCKED):
        for k, n in reasons[s].items():
            blocked_by_reason["%s:%s" % (s, k)] = n
    lane_slugs = set(valued) | set(touched)
    catalogue_slugs = {r["market_slug"] for r in rows}
    categories = {
        "listed": funnel["listed"],
        "within_mandate": funnel["within_mandate"],
        "supported": funnel["supported"],
        "receiving_current_data": funnel["receiving_current_data"],
        "evaluated": funnel["evaluated"],
        "blocked": states[S_BLOCKED] + states[S_OUTSIDE]
        + states[S_UNSUPPORTED],
        "blocked_before_evaluation": states[S_BLOCKED],
        "outside_mandate": states[S_OUTSIDE],
        "unsupported": states[S_UNSUPPORTED],
        "not_yet_evaluated": states[S_NOT_YET],
        "final_states": states,
        "final_states_sum": total,
        "sums_to_listed": total == funnel["listed"],
        "fixtures_listed": len(listed_fixtures),
        "fixtures_by_state": {s: len(v) for s, v in fixtures.items()},
        "derek_verdicts_on_evaluated": derek_verdicts,
        "lane_records_not_in_the_current_catalogue": len(
            lane_slugs - catalogue_slugs),
        "unit": "Polymarket US contracts (us_premap.market_slug); fixtures "
                "are counted by event_slug beside them",
    }
    return dict(out, ok=True, refusal=None, categories=categories,
                blocked_by_reason=blocked_by_reason, reasons_by_state=reasons,
                sample=sample,
                window_s=CURRENT_WINDOW_S)


async def record(conn, got: dict) -> str | None:
    """Append one census. Never raises; returns the id or None."""
    try:
        if not got.get("ok"):
            cats = {"ok": False, "refusal": got.get("refusal"),
                    "why": got.get("why")}
            blocked, sample = {}, {}
        else:
            cats = dict(got["categories"], mandate=got.get("mandate"),
                        catalogue_sweep_fresh=got.get("catalogue_sweep_fresh"),
                        catalogue_sweep_age_s=got.get("catalogue_sweep_age_s"))
            blocked, sample = got["blocked_by_reason"], got["sample"]
        cid = "census:%s:%s" % (
            int(float(got.get("at") or time.time())),
            hashlib.sha256(json.dumps(cats, sort_keys=True, default=str)
                           .encode()).hexdigest()[:12])
        return await conn.fetchval(
            "INSERT INTO derek_coverage_census (census_id, at, categories, "
            " blocked_by_reason, sample) VALUES ($1, to_timestamp($2), "
            " $3::jsonb, $4::jsonb, $5::jsonb) ON CONFLICT DO NOTHING "
            "RETURNING census_id",
            cid, float(got.get("at") or time.time()),
            json.dumps(cats, default=str), json.dumps(blocked, default=str),
            json.dumps(sample, default=str))
    except Exception:                                          # noqa: BLE001
        return None

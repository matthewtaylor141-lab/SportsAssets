"""BETTOR_EV_SHADOW: the PRIMARY lane's prospective collection.

Owner clarification 2026-09-19: "BETTOR EV ENGINE IS THE PRIMARY
PRODUCT. RN1_SHADOW is a secondary benchmark/research lane." And:
"BETTOR_EV_SHADOW does NOT need P_BETTOR to begin accumulating
prospective evidence. Do not wait for RN1 to generate a BETTOR
observation."

THE ASYMMETRY THIS MODULE EXISTS TO FIX. The RN1 lane is handed its
subjects: he acts, we observe, and a row follows. BETTOR has no such
generator. Left alone it would collect nothing until a fair-value model
existed, which would make the primary product a passenger of the
benchmark and would mean that on the day P_BETTOR arrives there is no
prospective market-state history to evaluate it against. So BETTOR
selects its OWN subjects, on a declared and frozen rule, and records
what it saw and what it decided.

A NO_TRADE IS A DECISION, NOT AN ABSENCE. Until independent Action EV
is established every decision here is NO_TRADE with
INDEPENDENT_EV_NOT_ESTABLISHED, and the directive is explicit that this
is valid BETTOR output. The blockers are the most valuable column in
the dataset: they are what will eventually answer whether BETTOR's
refusals saved money.

THE INDEPENDENCE WALL IS NOT NEGOTIABLE HERE. Nothing in this module
reads RN1's actions, identity, mirror decisions, future actions or any
target derived from them. Every feature is declared with an independent
provenance and `assert_lineage` refuses the row otherwise; the table
itself CHECKs rn1_features_used FALSE. "Do not weaken it to improve
apparent BETTOR performance."
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from . import shadow as sh
from . import shadow_lanes as lanes
from . import shadow_store as store

log = logging.getLogger(__name__)

MODEL_VERSION = "bettor_ev_v0_collecting"
POLICY_VERSION = "BETTOR_EV_SHADOW_V1"

# THE SELECTION RULE, FROZEN. A dataset whose selection rule is
# unrecorded cannot be reasoned about later -- every measurement over it
# is conditioned on a filter nobody can state. The version travels on
# every row.
UNIVERSE_VERSION = "BETTOR_UNIVERSE_V1"
UNIVERSE_SOURCE_PREMAP = "US_PREMAP_ACTIVE"
UNIVERSE_SOURCE_INSTITUTIONAL = "INSTITUTIONAL_INSTRUMENTS"

SELECTION_ROUND_ROBIN = (
    "round-robin over the venue's readable active universe at a bounded "
    "rate; no filter on expected profitability, so the dataset is not "
    "conditioned on what BETTOR already believes")

# ── the blocker vocabulary the directive names ───────────────────────
#
# Section 6, verbatim, plus the one that dominates today. Management
# should eventually see which of these prevents the most trades and
# whether those refusals saved money, so they are a closed vocabulary
# rather than free text.

B_EV_NOT_ESTABLISHED = lanes.REASON_EV_NOT_ESTABLISHED
B_NO_FAIR_VALUE = "NO_FAIR_VALUE"
B_NO_RELATIVE_VALUE = "NO_RELATIVE_VALUE"
B_LATENCY_DESTROYED_EDGE = "LATENCY_DESTROYED_EDGE"
B_SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
B_INSUFFICIENT_DEPTH = "INSUFFICIENT_DEPTH"
B_TOXICITY = "TOXICITY"
B_P_FILL_NOT_IDENTIFIED = "P_FILL_NOT_IDENTIFIED"
B_ACTION_EV_BELOW_THRESHOLD = "ACTION_EV_BELOW_THRESHOLD"
B_OUT_OF_DISTRIBUTION = "OUT_OF_DISTRIBUTION"
B_RISK_GATE = "RISK_GATE"
B_MARKET_STATE_UNREADABLE = "MARKET_STATE_UNREADABLE"

BLOCKERS = (B_EV_NOT_ESTABLISHED, B_NO_FAIR_VALUE, B_NO_RELATIVE_VALUE,
            B_LATENCY_DESTROYED_EDGE, B_SPREAD_TOO_WIDE,
            B_INSUFFICIENT_DEPTH, B_TOXICITY, B_P_FILL_NOT_IDENTIFIED,
            B_ACTION_EV_BELOW_THRESHOLD, B_OUT_OF_DISTRIBUTION,
            B_RISK_GATE, B_MARKET_STATE_UNREADABLE)

NOT_IDENTIFIED = "NOT_IDENTIFIED"


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _id(prefix: str, *parts) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return prefix + "_" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def _j(value):
    return None if value is None else json.dumps(value, default=str)


# ── the features BETTOR can honestly compute from a book ─────────────


def microstructure_of(market_state: dict | None) -> dict:
    """What the book itself says, and nothing it does not.

    EVERY FIELD IS EITHER MEASURED OR NOT_IDENTIFIED. A spread computed
    from a missing side, or a depth taken from top-of-book and called
    depth, would be a fabricated feature in a dataset whose whole value
    is that it was not fabricated.
    """
    if not market_state or not market_state.get("readable"):
        return {"status": NOT_IDENTIFIED,
                "why": (market_state or {}).get("whyUnreadable")
                or "no market state captured"}
    bid, ask = market_state.get("bid"), market_state.get("ask")
    mid = market_state.get("mid")
    spread = market_state.get("spread")
    out = {
        "status": "MEASURED",
        "bid": bid, "ask": ask, "mid": mid, "spread": spread,
        "spreadRelative": (spread / mid) if (spread is not None and mid)
        else None,
        # DEPTH IS NOT ESTABLISHED FROM A BBO and is not pretended to be.
        "depth": market_state.get("availableDepth") or NOT_IDENTIFIED,
        "l2Reference": market_state.get("l2Reference"),
        "stalenessMs": market_state.get("stalenessMs"),
    }
    if bid is None or ask is None:
        out["oneSided"] = True
    return out


# THE PROVENANCE OF EVERY FEATURE THIS MODULE PRODUCES. Declared, not
# inferred from names: "a name-sniffing rule misses signal_17 and
# catches rn1_free_indicator".
FEATURE_PROVENANCE = {
    "microstructure": "MARKET_MICROSTRUCTURE",
    "spread": "SPREAD",
    "depth": "DEPTH",
    "executionState": "EXECUTION_STATE",
}


def opportunity_record(*, symbol, observed_at=None, outcome_leg=None,
                       event_id=None, market_id=None, sport=None,
                       league=None, evidence_source,
                       market_state=None,
                       universe_source=UNIVERSE_SOURCE_PREMAP,
                       selection_reason=SELECTION_ROUND_ROBIN,
                       features=None, relative_value=None,
                       external_consensus=None, sport_features=None,
                       execution_features=None, latency=None,
                       model_outputs=None, cadence_s=None) -> dict:
    """One prospective BETTOR opportunity. Pure -- no database.

    THE IDEMPOTENCY IS A CADENCE BUCKET, not a raw timestamp. Without
    one, a worker restart or an overlapping tick would write the same
    market twice a second apart and the dataset would silently become a
    record of how often the loop ran rather than of what the market did.
    One row per market per bucket, and the bucket width is on the row.
    """
    if not evidence_source:
        raise store.StoreRefusal(
            "refused: a BETTOR opportunity with no EVIDENCE_SOURCE cannot "
            "be kept apart from another feed's")
    if not symbol:
        raise store.StoreRefusal("refused: an opportunity needs a symbol")

    at = observed_at or _now()
    bucket = at
    if cadence_s:
        epoch = int(at.timestamp()) // int(cadence_s) * int(cadence_s)
        bucket = datetime.fromtimestamp(epoch, tz=timezone.utc)

    micro = features if features is not None else microstructure_of(
        market_state)

    # THE WALL. Declared provenance for every feature family present,
    # checked by the lane's own rule, which fails closed on ambiguity.
    declared = {}
    if micro is not None:
        declared["microstructure"] = FEATURE_PROVENANCE["microstructure"]
    if relative_value is not None:
        declared["relativeValue"] = "CROSS_MARKET_RELATIVE_VALUE"
    if external_consensus is not None:
        declared["externalConsensus"] = \
            "EXACT_TIMESTAMP_EXTERNAL_CONSENSUS"
    if sport_features is not None:
        declared["sportFeatures"] = "SPORT_FUNDAMENTALS"
    if execution_features is not None:
        declared["executionFeatures"] = FEATURE_PROVENANCE["executionState"]
    lineage = lanes.assert_lineage(lanes.BETTOR_EV_SHADOW, declared)
    if lineage["rn1FeaturesUsed"]:
        raise store.StoreRefusal(
            "refused: a BETTOR opportunity declared an RN1 feature; the "
            "independent lane does not consume RN1")

    return {
        "bettorOpportunityId": _id("bop", UNIVERSE_VERSION, symbol,
                                   outcome_leg, evidence_source,
                                   bucket.isoformat()),
        "observedAt": at,
        "eventId": event_id, "marketId": market_id, "symbol": symbol,
        "outcomeLeg": outcome_leg, "sport": sport, "league": league,
        "universeVersion": UNIVERSE_VERSION,
        "universeSource": universe_source,
        "selectionReason": selection_reason,
        "evidenceSource": evidence_source,
        "marketStateId": (market_state or {}).get("marketStateId"),
        "microstructure": micro,
        "relativeValue": relative_value,
        "externalConsensus": external_consensus,
        "sportFeatures": sport_features,
        "executionFeatures": execution_features,
        "latency": latency,
        "modelOutputs": model_outputs,
        "featureLineage": lineage["featureLineage"],
        "rn1FeaturesUsed": False,
        "cadenceS": cadence_s,
    }


_OPPORTUNITY_INSERT = """
    INSERT INTO bettor_opportunities (
        bettor_opportunity_id, observed_at, event_id, market_id, symbol,
        outcome_leg, sport, league, universe_version, universe_source,
        selection_reason, evidence_source, market_state_id,
        microstructure, relative_value, external_consensus,
        sport_features, execution_features, latency, model_outputs,
        feature_lineage, rn1_features_used)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,
            $15::jsonb,$16::jsonb,$17::jsonb,$18::jsonb,$19::jsonb,
            $20::jsonb,$21::jsonb,$22)
    ON CONFLICT DO NOTHING
    RETURNING bettor_opportunity_id
"""


async def record_opportunity(record: dict, pool=None) -> tuple[str, bool]:
    """Append one opportunity. Returns (id, was_this_the_first_look)."""
    pool = pool or await store.get_pool()
    r = record
    row = await pool.fetchrow(
        _OPPORTUNITY_INSERT,
        r["bettorOpportunityId"], r["observedAt"], r.get("eventId"),
        r.get("marketId"), r["symbol"], r.get("outcomeLeg"),
        r.get("sport"), r.get("league"), r["universeVersion"],
        r["universeSource"], r["selectionReason"], r["evidenceSource"],
        r.get("marketStateId"), _j(r.get("microstructure")),
        _j(r.get("relativeValue")), _j(r.get("externalConsensus")),
        _j(r.get("sportFeatures")), _j(r.get("executionFeatures")),
        _j(r.get("latency")), _j(r.get("modelOutputs")),
        _j(r.get("featureLineage")), r["rn1FeaturesUsed"])
    if row is not None:
        # THE DATABASE RETURNS ITS OWN COLUMN NAME, not ours. Reading the
        # camelCase key here raised KeyError on every FIRST sighting of a
        # market -- after the row had already been written -- so the
        # opportunity landed and the tick died before it could decide.
        # 31 opportunities, zero decisions, status tick_failed, problems
        # empty. The lane that is the product was writing half of itself.
        return row["bettor_opportunity_id"], True
    return r["bettorOpportunityId"], False


# ── the decision ─────────────────────────────────────────────────────


def blockers_for(opportunity: dict, market_state: dict | None) -> list:
    """Every reason BETTOR cannot act on this, named.

    The list is ordered from the most fundamental outward, and the first
    entry today is always INDEPENDENT_EV_NOT_ESTABLISHED -- because it
    is true, and because a dataset that hid it behind a narrower
    complaint would misreport what is actually holding the engine.
    """
    out = [{"code": B_EV_NOT_ESTABLISHED,
            "why": ("no independently validated Action EV exists yet; "
                    "P_BETTOR is NOT_ESTABLISHED and is not manufactured "
                    "from the market or from RN1")},
           {"code": B_NO_FAIR_VALUE,
            "why": "no settlement fair-value model is registered"},
           {"code": B_P_FILL_NOT_IDENTIFIED,
            "why": ("no decision-grade queue or fill model; a passive "
                    "intention is never counted as a fill")}]
    if market_state is None or not market_state.get("readable"):
        out.append({"code": B_MARKET_STATE_UNREADABLE,
                    "why": (market_state or {}).get("whyUnreadable")
                    or "no market state captured"})
        return out
    micro = opportunity.get("microstructure") or {}
    if micro.get("depth") in (None, NOT_IDENTIFIED):
        out.append({"code": B_INSUFFICIENT_DEPTH,
                    "why": ("observed depth is not established from a "
                            "top-of-book feed")})
    rel = micro.get("spreadRelative")
    if isinstance(rel, (int, float)) and rel > 0.10:
        out.append({"code": B_SPREAD_TOO_WIDE,
                    "why": "spread is %.1f%% of mid" % (rel * 100)})
    return out


def decide(opportunity: dict, market_state: dict | None, *,
           decision_ts=None) -> dict:
    """BETTOR's own prospective decision.

    NO_TRADE TODAY, ALWAYS, AND HONESTLY. This function has no path that
    produces a BUY or a SELL, and that is correct rather than
    unfinished: there is no validated independent EV to act on, and a
    lane that traded anyway would be manufacturing the very claim the
    dataset exists to test. When Action EV is registered the gate opens
    here, in one place, and every row written before it is still
    readable as what BETTOR believed at the time.
    """
    decision_ts = decision_ts or _now()
    blocks = blockers_for(opportunity, market_state)
    record = lanes.not_yet_eligible(
        features=opportunity.get("featureLineage") or {},
        shadowDecisionId=_id("bdec", opportunity["bettorOpportunityId"],
                             POLICY_VERSION),
        symbol=opportunity["symbol"],
        outcomeLeg=opportunity.get("outcomeLeg") or "UNRESOLVED",
        eventId=opportunity.get("eventId"),
        marketId=opportunity.get("marketId"),
        sport=opportunity.get("sport"),
        league=opportunity.get("league"),
        modelVersion=MODEL_VERSION,
        policyVersion=POLICY_VERSION,
        evidenceSource=opportunity["evidenceSource"],
        decisionTs=decision_ts,
        featureAsofTs=opportunity.get("observedAt"),
        reasonCodes=[b["code"] for b in blocks],
        blockers=blocks,
        gateResults={"marketState": bool(
            market_state and market_state.get("readable"))},
        alternatives=[
            {"action": a,
             "why": ("not evaluated: independent Action EV is not "
                     "established, so no alternative can be ranked")}
            for a in (sh.BUY, sh.SELL, sh.HOLD)],
        pFillStatus=lanes.NOT_ESTABLISHED,
        actionEvStatus=NOT_IDENTIFIED,
        marketBid=(market_state or {}).get("bid"),
        marketAsk=(market_state or {}).get("ask"),
        mid=(market_state or {}).get("mid"),
        spread=(market_state or {}).get("spread"),
        availableDepth=(market_state or {}).get("availableDepth"),
        l2Reference=(market_state or {}).get("l2Reference"),
        pMarket=(market_state or {}).get("mid"),
    )
    record["bettorOpportunityId"] = opportunity["bettorOpportunityId"]
    record["marketStateId"] = opportunity.get("marketStateId")
    return record


async def write_decision(opportunity: dict, market_state: dict | None, *,
                         pool=None, decision_ts=None) -> tuple[str, bool]:
    if market_state is not None:
        await store.record_market_state(market_state, pool=pool)
    return await store.record_decision(
        decide(opportunity, market_state, decision_ts=decision_ts),
        pool=pool)


# ── the universe BETTOR looks at ─────────────────────────────────────
#
# The venue's own readable active universe, round-robin, bounded. No
# filter on expected profitability: a dataset selected by what BETTOR
# already believes cannot be used to test what BETTOR believes.

UNIVERSE_SQL = """
    SELECT p.identifier, p.market_slug, p.event_slug, p.event_title,
           p.side_norm, p.kind
      FROM us_premap p
     WHERE p.updated_at > now() - ($1 || ' seconds')::interval
       AND p.market_slug IS NOT NULL
     ORDER BY p.updated_at DESC
     LIMIT $2
"""


async def universe(pool, *, fresh_s=7200, limit=40) -> list:
    rows = await pool.fetch(UNIVERSE_SQL, str(int(fresh_s)), int(limit))
    return [{"identifier": r["identifier"], "symbol": r["market_slug"],
             "eventId": r["event_slug"], "eventTitle": r["event_title"],
             "outcomeLeg": r["side_norm"], "kind": r["kind"]}
            for r in rows]


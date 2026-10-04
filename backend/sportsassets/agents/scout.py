"""SCOUT: MARKET INTELLIGENCE -- COMPLIANT FEATURES, PROSPECTIVE TESTS,
NO AUTHORITY.

Scout discovers external information that might add out-of-sample value to
the PinnAPI baseline. He does NOT decide trades. Migration 217 holds his
records:

  scout_sources               every source he has considered, with its
                              licensing classification and the DECLARED
                              compliance check; only a source that passed is
                              ever ingested (the database refuses the rest)
  scout_features              one feature: SOURCE, FEATURE, EVENT scope,
                              IDENTITY basis, EXPECTED_MECHANISM,
                              PREDECLARED_HYPOTHESIS, licensing, FORWARD TEST
                              (its tournament) and INCREMENTAL PREDICTIVE
                              VALUE (the evaluator's result)
  scout_feature_observations  SOURCE_TIMESTAMP, OBSERVED_TIMESTAMP, EVENT,
                              IDENTITY, value, confidence, freshness and
                              provenance (the source row it was read from)
  scout_feature_tournaments   the FROZEN spec: baseline PinnAPI vs challenger
                              PinnAPI + feature, a predeclared metric (Brier /
                              log loss), minimum sample and improvement, and
                              the predeclared adjustment rule
  scout_tournament_samples    predictions frozen BEFORE outcomes (predicted at
                              or after the freeze, using only feature values
                              observed before the prediction)

THE ONE SOURCE (`SOURCES`). The repository already ingests the league's
official schedule (MLB Stats API, `bettor_fixture_metadata`, table
`fixture_metadata`). Scout reads THAT TABLE ONLY -- he makes no network call
and scrapes nothing new. Its classification is
OFFICIAL_PUBLIC_API_INTERNAL_RESEARCH_ONLY: public, no credential, used for
internal research features, never redistributed. A weather feed is declared
and REFUSED (no licensed source exists in the repository), so it is never
ingested.

THE VERDICT IS NEVER SCOUT'S. A tournament is evaluated by the calibration
engine (`feature_tournament.evaluate`, identity CALIBRATION_ENGINE) only once
the predeclared minimum sample has outcomes; without the predeclared
out-of-sample improvement the feature is REJECTED. Scout cannot validate,
adopt or promote his own feature (registry deny list, and migration 217's
CHECKs on scout_features / scout_feature_tournaments).
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime

from .. import bettor_fixture_metadata as FM
from . import pos_authority as PA
from . import pos_evidence as _PE  # noqa: F401
from . import registry as R

SCOUT = R.SCOUT
VERSION = "SCOUT_FEATURE_REGISTRY_V1"
AUTHORITY = "RESEARCH_SHADOW_ONLY"
EVALUATOR = "CALIBRATION_ENGINE"

FRESH_LIMIT_S = 6 * 3600
OBS_LOOKBACK_S = 14 * 86400
MAX_OBS_PER_PASS = 200
MAX_SAMPLES_PER_PASS = 200

R_NO_SCHEMA = "MIGRATION_217_IS_NOT_APPLIED"
R_NOT_COMPLIANT = "SCOUT_SOURCE_NOT_COMPLIANT"
R_DB_REFUSED = "THE_DATABASE_REFUSED_THE_FEATURE_WRITE"

# ═════════════════════════════════════════════════════════════════════
# THE DECLARED COMPLIANCE CHECK
# ═════════════════════════════════════════════════════════════════════

#: Every check a source must pass before anything from it is ingested.
COMPLIANCE_CHECKS = {
    "licensing_basis_recorded": ("a known, permitted licensing class and "
                                 "written usage terms"),
    "already_integrated_in_repo": ("read from a table the repository already "
                                   "ingests, or a licensed feed under "
                                   "contract -- never a new integration "
                                   "added by Scout"),
    "no_new_scraping": "no HTML scraping and no new network collection",
    "no_credential_circumvention": ("no login, key or paywall is bypassed; "
                                    "no credential is used beyond the "
                                    "source's own terms"),
    "internal_research_use_only": ("used for internal research features "
                                   "only; never sold or published"),
    "no_redistribution": "raw source data is never redistributed",
    "provenance_per_record": ("every observation cites the source row it "
                              "was read from"),
}
PERMITTED_LICENSES = ("OFFICIAL_PUBLIC_API_INTERNAL_RESEARCH_ONLY",
                      "LICENSED_COMMERCIAL", "OPEN_DATA_LICENSE",
                      "INTERNAL_RECORD")

SOURCES = {
    "mlb_stats_api_schedule_v1": {
        "name": "MLB Stats API, schedule (already ingested: fixture_metadata)",
        "url": FM.SOURCE_URL,
        "access_method": "EXISTING_INGESTED_TABLE",
        "licensing_class": "OFFICIAL_PUBLIC_API_INTERNAL_RESEARCH_ONLY",
        "usage_terms": (
            "The league's public schedule endpoint, already ingested by "
            "bettor_fixture_metadata (no credential). Scout reads only the "
            "fixture_metadata table for internal research features; nothing "
            "is redistributed. MLB Advanced Media's published terms restrict "
            "commercial redistribution of its data: any use beyond internal "
            "research needs a legal review first."),
        "declared": {
            "licensing_basis_recorded": True,
            "already_integrated_in_repo": True,
            "no_new_scraping": True,
            "no_credential_circumvention": True,
            "internal_research_use_only": True,
            "no_redistribution": True,
            "provenance_per_record": True,
        },
    },
    "weather_feed_unlicensed": {
        "name": "Ballpark weather (no licensed feed in the repository)",
        "url": None,
        "access_method": "LICENSED_FEED",
        "licensing_class": "UNKNOWN",
        "usage_terms": ("No weather provider is contracted or integrated; "
                        "scraping a public site would have no licensing "
                        "basis. Declared so the refusal is on record."),
        "declared": {
            "licensing_basis_recorded": False,
            "already_integrated_in_repo": False,
            "no_new_scraping": True,
            "no_credential_circumvention": True,
            "internal_research_use_only": True,
            "no_redistribution": True,
            "provenance_per_record": False,
        },
    },
}


def compliance_check(src: dict) -> dict:
    """THE DECLARED CHECK, PURE: each named check true, and a permitted
    licensing class with written terms. Returns {checks, passed, failed}."""
    d = dict(src.get("declared") or {})
    checks = {k: bool(d.get(k) is True) for k in COMPLIANCE_CHECKS}
    if src.get("licensing_class") not in PERMITTED_LICENSES or \
            not str(src.get("usage_terms") or "").strip():
        checks["licensing_basis_recorded"] = False
    if src.get("access_method") not in ("EXISTING_INGESTED_TABLE",
                                        "INTERNAL_TABLE", "LICENSED_FEED"):
        checks["already_integrated_in_repo"] = False
    failed = sorted(k for k, v in checks.items() if not v)
    return {"checks": checks, "passed": not failed, "failed": failed}


# ═════════════════════════════════════════════════════════════════════
# THE DECLARED FEATURES (each with its mechanism and hypothesis, fixed)
# ═════════════════════════════════════════════════════════════════════

def _nonstandard_format(row: dict) -> tuple:
    inn = row.get("scheduled_innings")
    dh = str(row.get("double_header") or "").upper()
    if inn is None and not dh:
        return None, "NO_FORMAT_RECORDED"
    v = 1.0 if (inn is not None and int(inn) != 9) or dh in ("Y", "S") \
        else 0.0
    return v, ("NONSTANDARD" if v else "STANDARD_9_INNINGS_SINGLE_GAME")


def _delayed(row: dict) -> tuple:
    s = " ".join(str(row.get(k) or "") for k in ("event_state_raw",
                                                 "abstract_state")).lower()
    if not s.strip():
        return None, "NO_STATE_RECORDED"
    v = 1.0 if any(w in s for w in ("delay", "postpon", "suspend")) else 0.0
    return v, "DELAYED_OR_POSTPONED" if v else "NOT_DELAYED"


FEATURES = {
    "MLB_NONSTANDARD_GAME_FORMAT": {
        "source_id": "mlb_stats_api_schedule_v1",
        "event_scope": "MLB game (fixture_metadata.condition_id)",
        "identity_basis": ("fixture_metadata.condition_id + game_pk + "
                           "official_date (the league's own game identity)"),
        "expected_mechanism": (
            "Seven-inning games and doubleheaders change starter depth, "
            "bullpen usage and the run distribution; a price formed on "
            "nine-inning priors can overstate the favourite."),
        "predeclared_hypothesis": (
            "On games the league lists as non-standard (scheduled innings "
            "!= 9 or a doubleheader), shrinking the PinnAPI probability "
            "toward 0.5 by 10% lowers the forward Brier score by at least "
            "0.001 over at least 200 settled games."),
        "reader": _nonstandard_format,
        "adjustment": {"rule": "SHRINK_TOWARD_HALF_WHEN_FEATURE_IS_1",
                       "k": 0.10},
    },
    "MLB_GAME_DELAYED_OR_POSTPONED": {
        "source_id": "mlb_stats_api_schedule_v1",
        "event_scope": "MLB game (fixture_metadata.condition_id)",
        "identity_basis": ("fixture_metadata.condition_id + game_pk + "
                           "official_date"),
        "expected_mechanism": (
            "A delayed or postponed start changes pitcher availability and "
            "lineup certainty after the market priced the game."),
        "predeclared_hypothesis": (
            "On games the league reports delayed, suspended or postponed, "
            "shrinking the PinnAPI probability toward 0.5 by 10% lowers the "
            "forward Brier score by at least 0.001 over at least 200 settled "
            "games."),
        "reader": _delayed,
        "adjustment": {"rule": "SHRINK_TOWARD_HALF_WHEN_FEATURE_IS_1",
                       "k": 0.10},
    },
}
TOURNAMENT_SPEC = {"metric": "BRIER", "min_sample": 200,
                   "min_improvement": 0.001}


def challenger_probability(p: float, feature_value, adjustment: dict) -> float:
    """THE PREDECLARED CHALLENGER: PinnAPI + feature. Pure."""
    p = float(p)
    if adjustment.get("rule") == "SHRINK_TOWARD_HALF_WHEN_FEATURE_IS_1" \
            and feature_value is not None and float(feature_value) >= 0.5:
        k = float(adjustment.get("k") or 0.0)
        p = 0.5 + (p - 0.5) * (1.0 - k)
    return min(max(p, 1e-6), 1 - 1e-6)


def _id(prefix, *parts) -> str:
    raw = json.dumps([str(p) for p in parts])
    return "%s:%s" % (prefix, hashlib.sha256(raw.encode()).hexdigest()[:24])


def feature_id_for(name: str) -> str:
    return _id("sft", VERSION, name, FEATURES[name]["source_id"])


def tournament_id_for(name: str) -> str:
    return _id("stt", VERSION, name)


def _ep(v):
    return v.timestamp() if isinstance(v, datetime) else v


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def observation_of(name: str, row: dict, *, now: float) -> dict | None:
    """ONE OBSERVATION of a declared feature from one fixture_metadata row.
    Pure. None when the row cannot support it (no condition id / time)."""
    f = FEATURES[name]
    cid = row.get("condition_id")
    src_ts = _num(_ep(row.get("retrieved_at")))
    if not cid or src_ts is None:
        return None
    v, label = f["reader"](row)
    refusals = _j(row.get("refusals")) or []
    conf = 1.0 if not refusals else 0.5
    if v is None:
        conf = 0.0
    obs_ts = max(float(now), src_ts)
    return {
        "observation_id": _id("sfo", name, cid, src_ts),
        "feature_id": feature_id_for(name), "source_id": f["source_id"],
        "source_timestamp": src_ts, "observed_timestamp": obs_ts,
        "event_key": str(cid),
        "identity": {"condition_id": cid, "game_pk": row.get("game_pk"),
                     "official_date": str(row.get("official_date") or ""),
                     "home": row.get("home_team"),
                     "away": row.get("away_team")},
        "value": v, "value_label": label, "confidence": conf,
        "freshness_s": round(obs_ts - src_ts, 3),
        "provenance": [{"kind": "fixture_metadata", "id": str(cid)}],
        "licensing_class": SOURCES[f["source_id"]]["licensing_class"]}


# ═════════════════════════════════════════════════════════════════════
# PERSISTENCE (as SCOUT: every write transaction declares him the actor)
# ═════════════════════════════════════════════════════════════════════

async def schema(conn) -> bool:
    try:
        return await conn.fetchval(
            "SELECT to_regclass('scout_features') IS NOT NULL") is True
    except Exception:                                           # noqa: BLE001
        return False


async def register_sources(conn, *, now: float) -> dict:
    """Record every declared source with its compliance result (once; a
    source's compliance record is append-only)."""
    PA.assert_may(SCOUT, "write.feature_registry")
    out = {}
    async with conn.transaction():
        await PA.act_as(conn, SCOUT)
        for sid, s in SOURCES.items():
            chk = compliance_check(s)
            await conn.execute(
                "INSERT INTO scout_sources (source_id, name, url, "
                " access_method, licensing_class, usage_terms, compliance, "
                " compliance_passed, declared_by, declared_at) VALUES "
                " ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,'SCOUT',to_timestamp($9)) "
                "ON CONFLICT (source_id) DO NOTHING", sid, s["name"],
                s["url"], s["access_method"], s["licensing_class"],
                s["usage_terms"], json.dumps(chk["checks"]), chk["passed"],
                float(now))
            out[sid] = chk
    return out


async def register_features(conn, *, now: float) -> list:
    """Register each declared feature of a COMPLIANT source, freeze its
    forward test (the spec, before any sample) and move it UNDER_TEST.
    Idempotent."""
    PA.assert_may(SCOUT, "write.feature_registry")
    PA.assert_may(SCOUT, "write.feature_tournaments")
    made = []
    for name, f in FEATURES.items():
        src = SOURCES[f["source_id"]]
        if not compliance_check(src)["passed"]:
            continue
        fid, tid = feature_id_for(name), tournament_id_for(name)
        async with conn.transaction():
            await PA.act_as(conn, SCOUT)
            res = await conn.execute(
                "INSERT INTO scout_features (feature_id, feature, source_id, "
                " licensing_class, event_scope, identity_basis, "
                " expected_mechanism, predeclared_hypothesis, proposed_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,to_timestamp($9)) "
                "ON CONFLICT DO NOTHING", fid, name, f["source_id"],
                src["licensing_class"], f["event_scope"], f["identity_basis"],
                f["expected_mechanism"], f["predeclared_hypothesis"],
                float(now))
            await conn.execute(
                "INSERT INTO scout_feature_tournaments (tournament_id, "
                " feature_id, challenger, metric, min_sample, "
                " min_improvement, adjustment, frozen_at, frozen_by) VALUES "
                " ($1,$2,$3,$4,$5,$6,$7::jsonb,to_timestamp($8),'SCOUT') "
                "ON CONFLICT DO NOTHING", tid, fid, "PINNAPI+" + name,
                TOURNAMENT_SPEC["metric"], TOURNAMENT_SPEC["min_sample"],
                TOURNAMENT_SPEC["min_improvement"],
                json.dumps(f["adjustment"]), float(now))
            await conn.execute(
                "UPDATE scout_features SET state='UNDER_TEST', "
                " forward_test_id=$2, state_reason='forward test frozen' "
                " WHERE feature_id=$1 AND state='PROPOSED'", fid, tid)
        if res.endswith("1"):
            made.append(fid)
    return made


async def ingest(conn, *, now: float, limit: int = MAX_OBS_PER_PASS) -> dict:
    """OBSERVE the declared features from the already-ingested
    fixture_metadata rows (no network call). Bounded; idempotent on
    (feature, event, source timestamp)."""
    PA.assert_may(SCOUT, "write.feature_registry")
    if await conn.fetchval("SELECT to_regclass('fixture_metadata')") is None:
        return {"observed": 0, "why": "FIXTURE_METADATA_TABLE_ABSENT"}
    rows = [dict(r) for r in await conn.fetch(
        "SELECT condition_id, retrieved_at, scheduled_innings, double_header,"
        "       event_state_raw, abstract_state, game_pk, official_date, "
        "       home_team, away_team, refusals FROM fixture_metadata "
        " WHERE retrieved_at BETWEEN to_timestamp($1) AND to_timestamp($2) "
        " ORDER BY retrieved_at DESC LIMIT $3", now - OBS_LOOKBACK_S, now,
        limit)]
    n = 0
    async with conn.transaction():
        await PA.act_as(conn, SCOUT)
        for name in FEATURES:
            if not await conn.fetchval(
                    "SELECT 1 FROM scout_features WHERE feature_id=$1",
                    feature_id_for(name)):
                continue
            for row in rows:
                o = observation_of(name, row, now=now)
                if o is None:
                    continue
                res = await conn.execute(
                    "INSERT INTO scout_feature_observations (observation_id, "
                    " feature_id, source_id, source_timestamp, "
                    " observed_timestamp, event_key, identity, value, "
                    " value_label, confidence, freshness_s, provenance, "
                    " licensing_class) VALUES ($1,$2,$3,to_timestamp($4),"
                    " to_timestamp($5),$6,$7::jsonb,$8,$9,$10,$11,$12::jsonb,"
                    " $13) ON CONFLICT DO NOTHING", o["observation_id"],
                    o["feature_id"], o["source_id"], o["source_timestamp"],
                    o["observed_timestamp"], o["event_key"],
                    json.dumps(o["identity"], default=str), o["value"],
                    o["value_label"], o["confidence"], o["freshness_s"],
                    json.dumps(o["provenance"]), o["licensing_class"])
                n += res.endswith("1")
    return {"observed": n, "rows_read": len(rows)}


async def freeze_samples(conn, *, now: float,
                         limit: int = MAX_SAMPLES_PER_PASS) -> dict:
    """FREEZE PREDICTIONS BEFORE OUTCOMES. For each open tournament, the
    first PinnAPI valuation of an observed game made at/after the freeze AND
    after the feature value was observed, with no outcome known yet, becomes
    a sample: baseline p, the feature value as then known, and the
    predeclared challenger p. Bounded; one sample per game."""
    PA.assert_may(SCOUT, "write.feature_tournaments")
    if await conn.fetchval("SELECT to_regclass('external_valuations')") \
            is None:
        return {"frozen": 0, "why": "EXTERNAL_VALUATIONS_TABLE_ABSENT"}
    made = 0
    ts = await conn.fetch(
        "SELECT tournament_id, feature_id, frozen_at, adjustment FROM "
        " scout_feature_tournaments WHERE verdict IS NULL")
    for t in ts:
        rows = await conn.fetch(
            "SELECT DISTINCT ON (v.condition_id) v.id, v.condition_id, "
            "       v.observed_at, v.probability, o.value "
            "  FROM external_valuations v "
            "  JOIN LATERAL (SELECT value FROM scout_feature_observations o "
            "        WHERE o.feature_id = $1 AND o.event_key = v.condition_id"
            "          AND o.source_timestamp <= v.observed_at "
            "        ORDER BY o.source_timestamp DESC LIMIT 1) o ON true "
            " WHERE v.observed_at >= $2 AND v.probability > 0 "
            "   AND v.probability < 1 AND v.provider = 'pinnapi.com/raw-websocket' "
            "   AND coalesce(v.outcome_known, false) = false "
            "   AND NOT EXISTS (SELECT 1 FROM scout_tournament_samples s "
            "        WHERE s.tournament_id = $3 AND s.event_key = "
            "              v.condition_id) "
            " ORDER BY v.condition_id, v.observed_at, v.id LIMIT $4",
            t["feature_id"], t["frozen_at"], t["tournament_id"], limit)
        adj = _j(t["adjustment"]) or {}
        async with conn.transaction():
            await PA.act_as(conn, SCOUT)
            for r in rows:
                p = float(r["probability"])
                res = await conn.execute(
                    "INSERT INTO scout_tournament_samples (tournament_id, "
                    " event_key, valuation_id, predicted_at, p_baseline, "
                    " feature_value, p_challenger) VALUES ($1,$2,$3,$4,$5,"
                    " $6,$7) ON CONFLICT DO NOTHING", t["tournament_id"],
                    r["condition_id"], str(r["id"]), r["observed_at"], p,
                    r["value"], challenger_probability(p, r["value"], adj))
                made += res.endswith("1")
    return {"frozen": made}


async def attach_outcomes(conn, *, limit: int = 500) -> dict:
    """The outcome of each frozen sample, once known, from the valuation it
    froze (external_valuations.outcome / outcome_at)."""
    rows = await conn.fetch(
        "SELECT s.tournament_id, s.event_key, v.outcome, v.outcome_at "
        "  FROM scout_tournament_samples s JOIN external_valuations v "
        "    ON v.id::text = s.valuation_id "
        " WHERE s.outcome IS NULL AND v.outcome_known AND v.outcome IN (0,1)"
        "   AND v.outcome_at > s.predicted_at LIMIT $1", limit)
    async with conn.transaction():
        await PA.act_as(conn, SCOUT)
        for r in rows:
            await conn.execute(
                "UPDATE scout_tournament_samples SET outcome=$3, "
                " outcome_at=$4 WHERE tournament_id=$1 AND event_key=$2 "
                "   AND outcome IS NULL", r["tournament_id"], r["event_key"],
                int(r["outcome"]), r["outcome_at"])
    return {"attached": len(rows)}


# ═════════════════════════════════════════════════════════════════════
# READS, SCORECARD, DESK, SLACK
# ═════════════════════════════════════════════════════════════════════

def _row(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if k in ("compliance", "identity", "provenance", "adjustment",
                 "result", "incremental_value"):
            v = _j(v)
        elif hasattr(v, "as_tuple"):
            v = float(v)
        out[k] = _ep(v)
    return out


async def sources(conn) -> list:
    return [_row(r) for r in await conn.fetch(
        "SELECT * FROM scout_sources ORDER BY source_id")]


async def features(conn) -> list:
    rows = await conn.fetch(
        "SELECT f.*, t.tournament_id, t.metric, t.min_sample, "
        "       t.min_improvement, t.frozen_at, t.verdict, t.improvement, "
        "       t.baseline_score, t.challenger_score, t.n AS evaluated_n, "
        "       (SELECT count(*) FROM scout_tournament_samples s WHERE "
        "         s.tournament_id = t.tournament_id) AS samples_frozen, "
        "       (SELECT count(*) FROM scout_tournament_samples s WHERE "
        "         s.tournament_id = t.tournament_id AND s.outcome IS NOT "
        "         NULL) AS samples_settled, "
        "       (SELECT count(*) FROM scout_feature_observations o WHERE "
        "         o.feature_id = f.feature_id) AS observations "
        "  FROM scout_features f LEFT JOIN scout_feature_tournaments t "
        "    ON t.feature_id = f.feature_id ORDER BY f.proposed_at, "
        "       f.feature_id")
    out = []
    for r in rows:
        d = _row(r)
        d["evidence"] = [{"kind": "scout_features", "id": d["feature_id"],
                          "href": None}]
        if d.get("tournament_id"):
            d["evidence"].append({"kind": "scout_feature_tournaments",
                                  "id": d["tournament_id"], "href": None})
        out.append(d)
    return out


async def observations(conn, *, limit: int = 50) -> list:
    rows = await conn.fetch(
        "SELECT o.*, f.feature FROM scout_feature_observations o JOIN "
        " scout_features f USING (feature_id) ORDER BY o.observed_timestamp "
        " DESC, o.observation_id LIMIT $1", max(1, min(int(limit), 500)))
    return [dict(_row(r), evidence=[{"kind": "scout_feature_observations",
                                     "id": r["observation_id"],
                                     "href": None}]) for r in rows]


async def tournaments(conn) -> list:
    return [_row(r) for r in await conn.fetch(
        "SELECT t.*, (SELECT count(*) FROM scout_tournament_samples s WHERE "
        " s.tournament_id = t.tournament_id) AS samples_frozen, (SELECT "
        " count(*) FROM scout_tournament_samples s WHERE s.tournament_id = "
        " t.tournament_id AND s.outcome IS NOT NULL) AS samples_settled "
        " FROM scout_feature_tournaments t ORDER BY t.frozen_at")]


DEFINITIONS = {
    "features_proposed": "features Scout registered (count)",
    "features_validated": ("features VALIDATED by the evaluator / features "
                           "whose tournament has a verdict"),
    "incremental_brier": ("mean (baseline Brier - challenger Brier) over "
                          "evaluated tournaments; positive = the feature "
                          "helped, out of sample"),
    "incremental_log_loss": ("mean (baseline log loss - challenger log "
                             "loss) over evaluated tournaments"),
    "incremental_realized_edge": ("realized edge of decisions that used a "
                                  "validated feature minus the PinnAPI-only "
                                  "counterfactual"),
    "lead_time_before_pinnapi_s": ("seconds from a feature observation to "
                                   "the PinnAPI price incorporating it"),
    "stale_information_rate": ("observations older than %d s when read / "
                               "observations" % FRESH_LIMIT_S),
    "false_information_rate": ("observations later shown false by the "
                               "source / observations"),
    "adoption_rate": "features ADOPTED by a named human / features VALIDATED",
    "economic_contribution_usd": ("realized P&L attributable to adopted "
                                  "features, vs the PinnAPI-only baseline"),
}


def _metric(name, num, den, *, value=None, why=None, **extra) -> dict:
    measurable = why is None and bool(den)
    if measurable and value is None:
        value = round(num / den, 6)
    return dict({"name": name, "definition": DEFINITIONS[name],
                 "numerator": num if measurable else None,
                 "denominator": den, "value": value if measurable else None,
                 "measurable": measurable,
                 "status": "MEASURED" if measurable else "UNAVAILABLE",
                 "why": None if measurable else (
                     why or "NOTHING_TO_MEASURE_YET")}, **extra)


def summarise_metrics(feats: list, tours: list, obs_total: int,
                      obs_stale: int) -> dict:
    """THE SCORECARD (pure). Null (UNAVAILABLE) until measured."""
    judged = [t for t in tours if t.get("verdict")]
    validated = [f for f in feats if f.get("state") in ("VALIDATED",
                                                        "ADOPTED")]
    adopted = [f for f in feats if f.get("state") == "ADOPTED"]
    ib = [float(t["improvement"]) for t in judged
          if t.get("metric") == "BRIER" and _num(t.get("improvement"))
          is not None]
    il = [float((t.get("result") or {}).get("log_loss_improvement"))
          for t in judged if _num((t.get("result") or {}).get(
              "log_loss_improvement")) is not None]
    m = {
        "features_proposed": _metric(
            "features_proposed", len(feats), 1, value=len(feats),
            why=None if feats else "NO_FEATURE_REGISTERED"),
        "features_validated": _metric(
            "features_validated", len(validated), len(judged),
            why=None if judged else "NO_TOURNAMENT_HAS_REACHED_ITS_"
                                    "PREDECLARED_MINIMUM_SAMPLE"),
        "incremental_brier": (_metric(
            "incremental_brier", round(sum(ib), 6), len(ib),
            value=round(sum(ib) / len(ib), 6)) if ib else _metric(
            "incremental_brier", None, 0,
            why="NO_TOURNAMENT_HAS_REACHED_ITS_PREDECLARED_MINIMUM_SAMPLE")),
        "incremental_log_loss": (_metric(
            "incremental_log_loss", round(sum(il), 6), len(il),
            value=round(sum(il) / len(il), 6)) if il else _metric(
            "incremental_log_loss", None, 0,
            why="NO_TOURNAMENT_HAS_REACHED_ITS_PREDECLARED_MINIMUM_SAMPLE")),
        "incremental_realized_edge": _metric(
            "incremental_realized_edge", None, 0,
            why="NO_VALIDATED_FEATURE_IS_CONSUMED_BY_ANY_DECISION"),
        "lead_time_before_pinnapi_s": _metric(
            "lead_time_before_pinnapi_s", None, 0,
            why="PINNAPI_LINE_MOVE_ATTRIBUTION_IS_NOT_MEASURED"),
        "stale_information_rate": _metric(
            "stale_information_rate", obs_stale, obs_total,
            why=None if obs_total else "NO_OBSERVATION_RECORDED"),
        "false_information_rate": _metric(
            "false_information_rate", None, 0,
            why="THE_SOURCE_PUBLISHES_NO_CORRECTION_FEED_TO_GRADE_AGAINST"),
        "adoption_rate": _metric(
            "adoption_rate", len(adopted), len(validated),
            why=None if validated else "NO_FEATURE_HAS_BEEN_VALIDATED"),
        "economic_contribution_usd": _metric(
            "economic_contribution_usd", None, 0,
            why="NO_ADOPTED_FEATURE_NO_ATTRIBUTABLE_PNL"),
    }
    return {"metrics": m, "features_counted": len(feats),
            "tournaments_counted": len(tours),
            "by_state": {s: sum(1 for f in feats if f.get("state") == s)
                         for s in ("PROPOSED", "UNDER_TEST", "VALIDATED",
                                   "REJECTED", "ADOPTED")},
            "rule": "null means unmeasured (UNAVAILABLE), never zero",
            "authority": AUTHORITY}


async def metrics(conn) -> dict:
    feats = await features(conn)
    tours = await tournaments(conn)
    r = await conn.fetchrow(
        "SELECT count(*) AS n, count(*) FILTER (WHERE freshness_s > $1) AS "
        " stale FROM scout_feature_observations", float(FRESH_LIMIT_S))
    return summarise_metrics(feats, tours, int(r["n"]), int(r["stale"]))


async def _latest_finding(conn, agent: str) -> str | None:
    """The agent's latest collaboration-loop finding (203), as recorded."""
    try:
        r = await conn.fetchrow(
            "SELECT finding_id, title, stage FROM agent_findings WHERE "
            " proposer=$1 ORDER BY updated_at DESC LIMIT 1", agent)
    except Exception:                                           # noqa: BLE001
        return None
    return None if r is None else "%s (%s, stage %s)" % (
        r["title"], r["finding_id"], r["stage"])


async def desk(conn) -> dict:
    st = await R.status_of(conn, SCOUT)
    feats = await features(conn)
    srcs = await sources(conn)
    met = await metrics(conn)
    alerts = []
    if st is None:
        alerts.append("NOT_REGISTERED_THE_RUNNER_HAS_NOT_STARTED")
    elif st.get("state") == R.S_FAILED:
        alerts.append("RUNNER_FAILED: %s" % (st.get("last_error") or ""))
    for s in srcs:
        if not s.get("compliance_passed"):
            alerts.append("SOURCE_REFUSED: %s (failed: %s)" % (
                s["source_id"], ", ".join(k for k, v in (
                    s.get("compliance") or {}).items() if not v)))
    under = [f for f in feats if f.get("state") == "UNDER_TEST"]
    done = [f for f in feats if f.get("state") in ("VALIDATED", "REJECTED",
                                                   "ADOPTED")]
    ec = met["metrics"]["economic_contribution_usd"]
    ib = met["metrics"]["incremental_brier"]
    return {
        "agent": "SCOUT", "name": "Scout", "role": "Market Intelligence",
        "authority": AUTHORITY,
        "heartbeat": {"state": (st or {}).get("state"),
                      "last_heartbeat_at": (st or {}).get(
                          "last_heartbeat_at"),
                      "activity": (st or {}).get("activity")},
        "current_task": (st or {}).get("activity"),
        "alerts": alerts,
        "active_searches": [{"feature": n, "source_id": f["source_id"],
                             "hypothesis": f["predeclared_hypothesis"]}
                            for n, f in FEATURES.items()],
        "data_sources": [{"source_id": s["source_id"], "name": s["name"],
                          "licensing_class": s["licensing_class"],
                          "compliance_passed": s["compliance_passed"]}
                         for s in srcs],
        "features_under_test": [{
            "feature": f["feature"], "tournament_id": f.get("tournament_id"),
            "samples_frozen": f.get("samples_frozen"),
            "samples_settled": f.get("samples_settled"),
            "min_sample": f.get("min_sample"),
            "observations": f.get("observations")} for f in under],
        "hypothesis": under[0]["predeclared_hypothesis"] if under else None,
        "experiments": [{"tournament_id": f.get("tournament_id"),
                         "metric": f.get("metric"),
                         "frozen_at": f.get("frozen_at"),
                         "progress": "%s/%s settled" % (
                             f.get("samples_settled"), f.get("min_sample"))}
                        for f in feats if f.get("tournament_id")],
        "validated_rejected": [{"feature": f["feature"], "state": f["state"],
                                "improvement": f.get("improvement")}
                               for f in done],
        "recent_finding": (
            "%s %s (improvement %s)" % (done[-1]["feature"], done[-1]["state"],
                                        done[-1].get("improvement"))
            if done else await _latest_finding(conn, SCOUT)),
        "incremental_predictive_value": {"value": ib["value"],
                                         "why": ib["why"]},
        "economic_score": {"name": "economic_contribution_usd",
                           "value": ec["value"], "why": ec["why"]},
        "affordances": "READ_ONLY_NO_SUBMIT_NO_TRADE_NO_PROMOTE",
    }


async def slack_answer(conn) -> str:
    feats = await features(conn)
    met = (await metrics(conn))["metrics"]
    lines = ["Scout · market intelligence · RESEARCH SHADOW ONLY · answered "
             "from records only (no language model); I decide no trade and "
             "cannot validate or promote my own feature."]
    if feats:
        for f in feats[:5]:
            lines.append("- %s · %s · %s/%s settled samples" % (
                f["feature"], f["state"], f.get("samples_settled"),
                f.get("min_sample")))
    else:
        lines.append("No feature is registered yet.")
    ib = met["incremental_brier"]
    lines.append("Incremental Brier: %s" % (
        "unmeasured (%s)" % ib["why"] if ib["value"] is None
        else ib["value"]))
    lines.append("Record: https://command.bettortoken.com/scout")
    return "\n".join(lines)


async def workroom_posts(conn, *, limit: int = 3) -> list:
    """EVIDENCE-LINKED COLLABORATION POSTS from records only: a frozen
    forward test (feature, tournament, spec) and an evaluator's verdict
    (tournament id, n, scores). [(key, text)]."""
    out = []
    for t in await conn.fetch(
            "SELECT t.*, f.feature FROM scout_feature_tournaments t JOIN "
            " scout_features f USING (feature_id) WHERE (t.evaluated_at > "
            " now() - interval '1 hour') OR (t.verdict IS NULL AND "
            " t.frozen_at > now() - interval '1 hour') ORDER BY "
            " coalesce(t.evaluated_at, t.frozen_at) DESC LIMIT $1", limit):
        if t["verdict"]:
            out.append(("verdict:%s" % t["tournament_id"], (
                "Scout · feature tournament %s · %s by %s\nFeature %s (%s): "
                "baseline PinnAPI %s %s vs challenger %s over %s frozen "
                "forward samples. %s\nStage: evaluator's verdict; Scout "
                "promotes nothing." % (
                    t["tournament_id"], t["verdict"], t["evaluated_by"],
                    t["feature"], t["feature_id"], t["metric"],
                    t["baseline_score"], t["challenger_score"], t["n"],
                    t["verdict_reason"]))))
        else:
            out.append(("frozen:%s" % t["tournament_id"], (
                "Scout · forward test frozen %s\nFeature %s (%s) vs "
                "PinnAPI-only: %s, minimum %s settled samples, minimum "
                "improvement %s. Asked: Karen to challenge, the calibration "
                "engine to evaluate.\nStage: hypothesis under test; nothing "
                "informs a decision until validated." % (
                    t["tournament_id"], t["feature"], t["feature_id"],
                    t["metric"], t["min_sample"], t["min_improvement"]))))
    return out


async def profile(conn) -> dict:
    st = await R.status_of(conn, SCOUT)
    return dict(PA.profile(SCOUT), registered=st is not None, status=st,
                compliance_checks=COMPLIANCE_CHECKS, evaluator=EVALUATOR)


def now() -> float:
    return time.time()

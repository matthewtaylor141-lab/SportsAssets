"""THE IMPROVEMENT PIPELINE RUNNER: REAL SIGNALS INTO ONE LEDGER (migration 221).

WHAT IT DOES, every INTERVAL_S in the API process (api/app.py lifespan):

  1. SEEDS improvement items from REAL, EXISTING signals only, deduplicated
     on (source_kind, source_key), at most MAX_NEW_ITEMS_PER_PASS per pass:
       KAREN_UPHELD_CHALLENGE  karen_challenges state UPHELD (owner: the
                               challenged agent); a challenge of a loop
                               finding is not a second item -- it is that
                               finding's PEER_CHALLENGE, mirrored below
       AUDREY_FINDING          paper_audrey_findings WARNING / CRITICAL,
                               one item per finding kind; a finding that a
                               coverage alert already routes is left to the
                               coverage source
       COVERAGE_INCIDENT       coverage_collapse_alerts that are a
                               COVERAGE_INCIDENT or CRITICAL, one item per
                               (league, stage, kind)
       EDDIE_SKIP_EXECUTION    eddie_execution_estimates SKIP_EXECUTION, one
                               item per UTC day (owner: Archer)
       FALSE_REFUSAL           lol_ledger FALSE_REFUSAL (only when the lost-
                               opportunity table exists), one item per defect
       TOURNAMENT_VERDICT      scout_feature_tournaments VALIDATED (owner
                               Scout) and poslearn_promotion_steps
                               CRITERIA_MET (a challenger met its predeclared
                               promotion criteria)
     Each item's EVIDENCE stage cites the signal rows (the database checks
     that each exists). Later rows of a grouped signal are appended as
     further EVIDENCE (no stage change, so no Slack line).
  2. MIRRORS, in canonical order and stopping at the first missing record,
     the stages the agents have ALREADY recorded elsewhere -- never text of
     its own: the owner's HYPOTHESIS from the linked collaboration-loop
     finding (agent_finding_stages) or Scout's predeclared hypothesis
     (scout_features); peer challenges from the loop and Karen's challenges
     of that finding (karen_challenges); the owner's RESPONSE from the
     target's recorded peer response; the loop's BOUNDED_EXPERIMENT /
     CANDIDATE_IMPROVEMENT as EXPERIMENT; the loop's INDEPENDENT_EVALUATION;
     the loop's RELEASE_ELIGIBILITY as ELIGIBLE_CHANGE (never for a protected
     item -- a human records that); the loop's CLOSED. Every mirrored row
     carries source_ref = the record it was taken from.
  3. PRESERVES DISAGREEMENTS: a target that DISPUTED a challenge the
     evaluator then UPHELD, a REFUTED peer challenge, a DISPUTE owner
     response -- each party's position verbatim with its record, resolved
     only from a recorded resolution (DECIDED by a third party is never
     called consensus).
  4. Writes a run row (improve_runs) and a service heartbeat
     `improvement_pipeline`.

WHAT IT NEVER DOES. It never writes a HUMAN or ENGINEERING step (the
CONTROLLED_RELEASE approver, a candidate patch, a rollback): the database
refuses those in a session that declared itself the runner, and this module
never asks. It never pushes, merges, deploys, runs a process, opens a
network connection or sends a message (the Slack digest is the bridge's,
reading the transitions this writes). It imports no order, venue, execution
or funded module. It writes only the four improve_* tables.

BOUNDED: lookback windows, per-source row limits, a per-pass item budget,
per-phase timeouts and a pass timeout. FAILURE-ISOLATED: each source and each
item runs in its own transaction; a failure is recorded by name and the rest
run. Kill switch: IMPROVEMENT_PIPELINE_ENABLED=0 (default on).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from . import improvement_stages as S

log = logging.getLogger(__name__)

ENV_KILL = "IMPROVEMENT_PIPELINE_ENABLED"
SERVICE = "improvement_pipeline"
INTERVAL_S = 600
FIRST_DELAY_S = 150
PASS_TIMEOUT_S = 120
PHASE_TIMEOUT_S = 30
STATEMENT_TIMEOUT_MS = 10000
LOOKBACK_S = 14 * 86400
SOURCE_LIMIT = 50
MAX_NEW_ITEMS_PER_PASS = 20
MAX_ADVANCE_PER_PASS = 60
MAX_EVIDENCE_EVENTS_PER_ITEM = 20
MAX_REFS = 50

R_NO_SCHEMA = "MIGRATION_221_NOT_APPLIED"
R_DISABLED = "IMPROVEMENT_PIPELINE_ENABLED_IS_OFF"


def enabled() -> bool:
    return os.getenv(ENV_KILL, "1").strip().lower() not in (
        "0", "false", "off", "no")


async def _regclass(conn, name: str) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        name))
    except Exception:                                           # noqa: BLE001
        return False


async def schema(conn) -> bool:
    return await _regclass(conn, "improve_events")


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _ep(v):
    return v.timestamp() if isinstance(v, datetime) else v


def _dt(epoch: float) -> datetime:
    return datetime.fromtimestamp(float(epoch), timezone.utc)


def _row(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if k in ("body", "source_ref", "evidence_refs", "experiment_refs",
                 "forward_result", "protected_basis", "positions",
                 "resolution_ref", "summary"):
            v = _j(v)
        out[k] = v
    return out


@asynccontextmanager
async def runner_tx(conn):
    """One transaction declared as THE RUNNER: the database then refuses any
    HUMAN / ENGINEERING row inside it. The declaration ends with it."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('bettor.improvement_runner', "
                           "'on', true)")
        await conn.execute("SET LOCAL statement_timeout = %d"
                           % STATEMENT_TIMEOUT_MS)
        try:
            yield conn
        finally:
            try:
                await conn.execute("SELECT set_config("
                                   "'bettor.improvement_runner', 'off', true)")
            except Exception:                                   # noqa: BLE001
                pass


async def ref_exists(conn, ref: dict) -> bool:
    try:
        return bool(await conn.fetchval("SELECT improve_ref_exists($1::jsonb)",
                                        json.dumps(ref)))
    except Exception:                                           # noqa: BLE001
        return False


async def existing_refs(conn, refs: list) -> list:
    out, seen = [], set()
    for r in refs:
        k = (str(r.get("kind")), str(r.get("id")))
        if k in seen or not r.get("id"):
            continue
        seen.add(k)
        if await ref_exists(conn, {"kind": k[0], "id": k[1]}):
            out.append({"kind": k[0], "id": k[1]})
    return out[:MAX_REFS]


def _day(v) -> str:
    return (v if isinstance(v, datetime) else _dt(v)).date().isoformat()


# ═════════════════════════════════════════════════════════════════════
# 1 · SEEDS: REAL SIGNALS ONLY
# ═════════════════════════════════════════════════════════════════════

def _cand(source_kind, source_key, source_ref, title, statement, owner,
          refs, protected_texts, **extra) -> dict:
    areas, basis = S.classify_protected(*protected_texts)
    return dict(source_kind=source_kind, source_key=str(source_key)[:300],
                source_ref=source_ref, title=str(title)[:300],
                problem_statement=str(statement)[:4000], owner=owner,
                evidence_refs=refs, protected_areas=areas,
                protected_basis=basis, **extra)


async def seed_karen(conn, *, now: float) -> list:
    if not await _regclass(conn, "karen_challenges"):
        return []
    rows = await conn.fetch(
        "SELECT * FROM karen_challenges WHERE state = 'UPHELD' "
        "   AND finding_id IS NULL "
        "   AND resolved_at >= to_timestamp($1) "
        " ORDER BY resolved_at DESC, challenge_id LIMIT $2",
        now - LOOKBACK_S, SOURCE_LIMIT)
    out = []
    for r in rows:
        owner = r["target_agent"]
        if owner not in S.OWNER_AGENTS:
            continue
        refs = [{"kind": "karen_challenges", "id": r["challenge_id"]},
                {"kind": r["target_kind"], "id": str(r["target_id"])}]
        out.append(_cand(
            "KAREN_UPHELD_CHALLENGE", r["challenge_id"],
            {"kind": "karen_challenges", "id": r["challenge_id"]},
            "Upheld challenge %s: %s %s (%s)" % (
                r["challenge_id"], r["target_kind"], r["target_id"],
                r["detector"]),
            "Karen's challenge %s of %s's %s %s was UPHELD by %s after the "
            "target's %s response (detector %s, severity %s). The claim, "
            "the response and the evaluation are the cited records."
            % (r["challenge_id"], owner.title(), r["target_kind"],
               r["target_id"], r["resolved_by"],
               r["response_stance"] or "absent", r["detector"],
               r["severity"]),
            owner, refs,
            (r["detector"], r["target_kind"], r["claim"]),
            origin={"kind": "karen", "row": dict(r)},
            linked_finding=r["improvement_finding_id"]))
    return out


async def _deficit_finding(conn, audrey_finding_id: str) -> dict | None:
    """The collaboration-loop finding the improvement driver opened from an
    Audrey finding, or one that cites it."""
    if not audrey_finding_id or not await _regclass(conn, "agent_findings"):
        return None
    fid = None
    if await _regclass(conn, "improvement_deficits"):
        fid = await conn.fetchval(
            "SELECT agent_finding_id FROM improvement_deficits "
            " WHERE audrey_finding_id = $1 AND agent_finding_id IS NOT NULL "
            " ORDER BY updated_at DESC LIMIT 1", audrey_finding_id)
    if fid is None:
        fid = await conn.fetchval(
            "SELECT finding_id FROM agent_findings WHERE evidence_refs @> "
            " $1::jsonb ORDER BY created_at LIMIT 1",
            json.dumps([{"kind": "paper_audrey_findings",
                         "id": audrey_finding_id}]))
    if fid is None:
        return None
    r = await conn.fetchrow("SELECT finding_id, proposer FROM agent_findings "
                            " WHERE finding_id = $1", fid)
    return dict(r) if r else None


async def seed_coverage(conn, *, now: float) -> list:
    if not await _regclass(conn, "coverage_collapse_alerts"):
        return []
    rows = await conn.fetch(
        "SELECT * FROM coverage_collapse_alerts "
        " WHERE detected_at >= to_timestamp($1) "
        "   AND (detail->>'coverage_status' = 'COVERAGE_INCIDENT' "
        "        OR severity = 'CRITICAL') "
        " ORDER BY detected_at DESC, alert_id LIMIT $2",
        now - LOOKBACK_S, SOURCE_LIMIT)
    groups: dict = {}
    for r in rows:
        groups.setdefault((r["league"], r["stage_to"], r["kind"]), []).append(r)
    out = []
    for (league, stage_to, kind), rs in groups.items():
        rs = sorted(rs, key=lambda x: (x["detected_at"], x["alert_id"]))
        first = rs[0]
        refs = []
        for r in rs:
            refs.append({"kind": "coverage_collapse_alerts",
                         "id": r["alert_id"]})
            if r["audrey_finding_id"]:
                refs.append({"kind": "paper_audrey_findings",
                             "id": r["audrey_finding_id"]})
        linked = await _deficit_finding(conn, first["audrey_finding_id"])
        owner = linked["proposer"] if linked and linked["proposer"] in \
            S.OWNER_AGENTS else "DEREK"
        detail = _j(first["detail"]) or {}
        out.append(_cand(
            "COVERAGE_INCIDENT", "%s|%s|%s" % (league, stage_to, kind),
            {"kind": "coverage_collapse_alerts", "id": first["alert_id"]},
            "Coverage incident: %s %s at %s" % (league, kind, stage_to),
            "Audrey's coverage funnel recorded %d alert(s) for %s: %s at "
            "stage %s (from %s), first on %s, severity %s. %s" % (
                len(rs), league, kind, stage_to, first["stage_from"],
                first["day"], first["severity"],
                str(detail.get("statement") or "")[:600]),
            owner, refs, (stage_to, kind, detail.get("statement")),
            linked_finding=linked["finding_id"] if linked else None))
    return out


async def seed_audrey(conn, *, now: float) -> list:
    if not await _regclass(conn, "paper_audrey_findings"):
        return []
    routed = "TRUE"
    if await _regclass(conn, "coverage_collapse_alerts"):
        routed = ("NOT EXISTS (SELECT 1 FROM coverage_collapse_alerts c "
                  " WHERE c.audrey_finding_id = f.finding_id)")
    rows = await conn.fetch(
        "SELECT f.* FROM paper_audrey_findings f "
        " WHERE f.found_at >= to_timestamp($1) "
        "   AND f.severity IN ('WARNING', 'CRITICAL') AND " + routed +
        " ORDER BY f.found_at DESC, f.finding_id LIMIT $2",
        now - LOOKBACK_S, SOURCE_LIMIT)
    groups: dict = {}
    for r in rows:
        groups.setdefault(r["kind"], []).append(r)
    out = []
    for kind, rs in groups.items():
        rs = sorted(rs, key=lambda x: (x["found_at"], x["finding_id"]))
        first = rs[0]
        linked = await _deficit_finding(conn, first["finding_id"])
        owner = linked["proposer"] if linked and linked["proposer"] in \
            S.OWNER_AGENTS else "AUDREY"
        detail = _j(first["detail"]) or {}
        stmt = detail.get("statement") if isinstance(detail, dict) else None
        subjects = sorted({str(r["subject"]) for r in rs if r["subject"]})
        sev = "CRITICAL" if any(r["severity"] == "CRITICAL" for r in rs) \
            else "WARNING"
        out.append(_cand(
            "AUDREY_FINDING", kind,
            {"kind": "paper_audrey_findings", "id": first["finding_id"]},
            "Audrey finding: %s" % kind,
            "Audrey recorded %d finding(s) of kind %s (highest severity %s) "
            "on %d subject(s) since %s%s.%s" % (
                len(rs), kind, sev, len(subjects),
                first["found_at"].isoformat(),
                " (e.g. %s)" % ", ".join(subjects[:3]) if subjects else "",
                " Statement: %s" % str(stmt)[:600] if stmt else ""),
            owner,
            [{"kind": "paper_audrey_findings", "id": r["finding_id"]}
             for r in rs],
            (kind, stmt),
            linked_finding=linked["finding_id"] if linked else None))
    return out


async def seed_archer(conn, *, now: float) -> list:
    if not await _regclass(conn, "eddie_execution_estimates"):
        return []
    rows = await conn.fetch(
        "SELECT estimate_id, decision_id, estimated_at, us_market_slug, "
        "       recommendation_reason, expected_net_executable_edge_pp "
        "  FROM eddie_execution_estimates "
        " WHERE recommendation = 'SKIP_EXECUTION' "
        "   AND estimated_at >= to_timestamp($1) "
        " ORDER BY estimated_at, estimate_id LIMIT $2",
        now - LOOKBACK_S, SOURCE_LIMIT * 4)
    groups: dict = {}
    for r in rows:
        groups.setdefault(_day(r["estimated_at"]), []).append(r)
    out = []
    for day, rs in groups.items():
        edges = [r["expected_net_executable_edge_pp"] for r in rs
                 if r["expected_net_executable_edge_pp"] is not None]
        # (266) the source kind and key keep their historical spelling:
        # they ARE the item's identity (UNIQUE (source_kind, source_key))
        # and a day already seeded before the rename must not seed twice
        out.append(_cand(
            "EDDIE_SKIP_EXECUTION", "eddie_skip:%s" % day,
            {"kind": "eddie_execution_estimates", "id": rs[0]["estimate_id"]},
            "Archer SKIP_EXECUTION on %d Derek candidate(s) (%s)" % (
                len(rs), day),
            "Archer's SHADOW estimates recorded SKIP_EXECUTION for %d Derek "
            "ENTER candidate(s) on %s (UTC): the expected net executable "
            "edge after fees, spread, slippage and adverse selection did not "
            "support executing%s. First reason recorded: %s" % (
                len(rs), day,
                " (median %.2f pp over %d measured)" % (
                    sorted(edges)[len(edges) // 2], len(edges))
                if edges else "", str(rs[0]["recommendation_reason"])[:400]),
            "ARCHER",
            [{"kind": "eddie_execution_estimates", "id": r["estimate_id"]}
             for r in rs],
            ("execution", "SKIP_EXECUTION")))
    return out


async def seed_false_refusals(conn, *, now: float) -> list:
    if not await _regclass(conn, "lol_ledger"):
        return []
    rows = await conn.fetch(
        "SELECT ledger_id, decision_ref, defect, attribution, league, "
        "       decided_at, decision_time_net_ev_usd "
        "  FROM lol_ledger WHERE classification = 'FALSE_REFUSAL' "
        "   AND classified_at >= to_timestamp($1) "
        " ORDER BY classified_at, ledger_id LIMIT $2",
        now - LOOKBACK_S, SOURCE_LIMIT * 4)
    groups: dict = {}
    for r in rows:
        groups.setdefault(r["defect"], []).append(r)
    out = []
    for defect, rs in groups.items():
        out.append(_cand(
            "FALSE_REFUSAL", "false_refusal:%s" % defect,
            {"kind": "lol_ledger", "id": rs[0]["ledger_id"]},
            "False refusals: %s (%d)" % (defect, len(rs)),
            "The lost-opportunity ledger classified %d settled Derek "
            "refusal(s) as FALSE_REFUSAL with defect %s (attribution %s): "
            "the decision-time record showed executable net EV > 0 under the "
            "policy's own thresholds. Hypothetical P&L is never summed with "
            "paper or actual." % (len(rs), defect, rs[0]["attribution"]),
            "DEREK",
            [{"kind": "lol_ledger", "id": r["ledger_id"]} for r in rs],
            (defect, rs[0]["attribution"])))
    return out


def _variant_owner(subject: str, kind: str) -> str:
    s = str(subject or "").upper()
    for a in ("DEREK", "XAVIER", "ARCHER", "AUDREY", "SCOUT"):
        if s.startswith(a):
            return a
    if s.startswith("ALLOCATOR") or s.startswith("CHIEF_ALLOCATOR"):
        return "CHIEF_ALLOCATOR"
    return "DEREK"


async def seed_tournaments(conn, *, now: float) -> list:
    out = []
    if await _regclass(conn, "scout_feature_tournaments"):
        rows = await conn.fetch(
            "SELECT t.*, f.feature, f.predeclared_hypothesis "
            "  FROM scout_feature_tournaments t "
            "  JOIN scout_features f USING (feature_id) "
            " WHERE t.verdict = 'VALIDATED' "
            "   AND t.evaluated_at >= to_timestamp($1) "
            " ORDER BY t.evaluated_at DESC LIMIT $2",
            now - LOOKBACK_S, SOURCE_LIMIT)
        for r in rows:
            out.append(_cand(
                "TOURNAMENT_VERDICT", "scout:%s" % r["tournament_id"],
                {"kind": "scout_feature_tournaments",
                 "id": r["tournament_id"]},
                "Validated feature: %s" % r["feature"],
                "Feature tournament %s VALIDATED %s against the %s baseline "
                "(%s, n=%s, improvement %s >= %s), evaluated by %s. Adoption "
                "is not decided here." % (
                    r["tournament_id"], r["feature"], r["baseline"],
                    r["metric"], r["n"], r["improvement"],
                    r["min_improvement"], r["evaluated_by"]),
                "SCOUT",
                [{"kind": "scout_feature_tournaments",
                  "id": r["tournament_id"]},
                 {"kind": "scout_features", "id": r["feature_id"]}],
                ("feature", r["metric"]),
                scout_feature=r["feature_id"]))
    if await _regclass(conn, "poslearn_promotion_steps"):
        rows = await conn.fetch(
            "SELECT s.step_id, s.registration_id, s.at, r.kind, r.subject_id, "
            "       r.family "
            "  FROM poslearn_promotion_steps s "
            "  JOIN poslearn_registrations r USING (registration_id) "
            " WHERE s.step = 'CRITERIA_MET' AND s.at >= to_timestamp($1) "
            " ORDER BY s.at DESC LIMIT $2", now - LOOKBACK_S, SOURCE_LIMIT)
        for r in rows:
            out.append(_cand(
                "TOURNAMENT_VERDICT", "poslearn:%s" % r["registration_id"],
                {"kind": "poslearn_promotion_steps", "id": str(r["step_id"])},
                "Challenger met its criteria: %s" % r["registration_id"],
                "The %s tournament challenger %s (family %s) met its "
                "predeclared promotion criteria (promotion step %s). The "
                "ladder's Karen challenge, Audrey evaluation and human "
                "approval remain separate records." % (
                    r["kind"], r["registration_id"], r["family"],
                    r["step_id"]),
                _variant_owner(r["subject_id"], r["kind"]),
                [{"kind": "poslearn_promotion_steps", "id": str(r["step_id"])},
                 {"kind": "poslearn_registrations",
                  "id": r["registration_id"]}],
                (r["kind"], r["family"])))
    return out


SOURCES = (("karen", seed_karen), ("coverage", seed_coverage),
           ("audrey", seed_audrey), ("archer", seed_archer),
           ("false_refusal", seed_false_refusals),
           ("tournament", seed_tournaments))


# ═════════════════════════════════════════════════════════════════════
# 2 · WRITES (through the database's guard; the pure guard refuses first)
# ═════════════════════════════════════════════════════════════════════

async def load(conn, item_id: str) -> tuple:
    it = await conn.fetchrow("SELECT * FROM improve_items WHERE item_id=$1",
                             item_id)
    if it is None:
        return None, []
    ev = await conn.fetch("SELECT * FROM improve_events WHERE item_id=$1 "
                          " ORDER BY event_id", item_id)
    return _row(it), [_row(e) for e in ev]


async def add_event(conn, item: dict, events: list, new: dict) -> dict:
    """Append one stage row (inside the caller's runner transaction)."""
    why = S.check_event(item, events, new, runner=True)
    if why:
        return {"ok": False, "refusal": why}
    src = new.get("source_ref")
    if src and any(e.get("stage") == new["stage"] and
                   (e.get("source_ref") or {}).get("kind") == src["kind"] and
                   str((e.get("source_ref") or {}).get("id")) == str(src["id"])
                   for e in events):
        return {"ok": False, "refusal": "ALREADY_RECORDED"}
    at = max(float(new["at"]), max([float(_ep(e["at"])) for e in events]
                                   or [float(_ep(item["created_at"]))]))
    body = dict(new.get("body") or {})
    if float(new["at"]) < at:
        # the source's own time is kept; the row's time is when the ledger
        # recorded it (never earlier than the stage before it)
        body["source_at"] = float(new["at"])
    row = await conn.fetchrow(
        "INSERT INTO improve_events (item_id, stage, stage_seq, actor, "
        " actor_class, at, body, source_ref, evidence_refs, stance, outcome, "
        " experiment_refs, recorded_by) VALUES ($1,$2,$3,$4,$5,"
        " to_timestamp($6),$7::jsonb,$8::jsonb,$9::jsonb,$10,$11,$12::jsonb,"
        " $13) RETURNING *",
        item["item_id"], new["stage"], S.SEQ[new["stage"]], new["actor"],
        new["actor_class"], at, json.dumps(body, default=str),
        json.dumps(src) if src else None,
        json.dumps(new.get("evidence_refs") or []),
        new.get("stance"), new.get("outcome"),
        json.dumps(new["experiment_refs"]) if new.get("experiment_refs")
        else None, S.RUNNER_ACTOR)
    e = _row(row)
    events.append(e)
    if e["is_transition"]:
        item["stage"] = e["stage"]
        item["stage_seq"] = e["stage_seq"]
    return {"ok": True, "event_id": e["event_id"], "stage": e["stage"],
            "transition": e["is_transition"]}


async def upsert_item(conn, c: dict, *, now: float) -> dict:
    """Create the item and its EVIDENCE stage, or append unseen evidence of
    a grouped signal. Idempotent. Inside a runner transaction."""
    iid = S.item_id_for(c["source_kind"], c["source_key"])
    refs = await existing_refs(conn, c["evidence_refs"])
    if not await ref_exists(conn, c["source_ref"]) or not refs:
        return {"ok": False, "refusal": "SOURCE_RECORD_NOT_FOUND",
                "item_id": iid}
    item, events = await load(conn, iid)
    if item is None:
        areas = c["protected_areas"]
        await conn.execute(
            "INSERT INTO improve_items (item_id, source_kind, source_key, "
            " source_ref, title, problem_statement, statement_basis, "
            " owner_agent, evidence_refs, protected_areas, protected_basis, "
            " requires_human_review, required_independent_reviews, "
            " created_by, created_at, updated_at) VALUES ($1,$2,$3,$4::jsonb,"
            " $5,$6,'RUNNER_SUMMARY_OF_CITED_RECORDS',$7,$8::jsonb,$9::text[],"
            " $10::jsonb,$11,$12,$13,to_timestamp($14),to_timestamp($14))",
            iid, c["source_kind"], c["source_key"], json.dumps(c["source_ref"]),
            c["title"], c["problem_statement"], c["owner"], json.dumps(refs),
            areas, json.dumps(c["protected_basis"]), bool(areas),
            S.required_reviews(areas), S.RUNNER_ACTOR, float(now))
        item, events = await load(conn, iid)
        got = await add_event(conn, item, events, {
            "stage": S.EVIDENCE, "actor": S.RUNNER_ACTOR,
            "actor_class": S.RUNNER, "at": now,
            "body": {"statement": c["problem_statement"],
                     "basis": "RUNNER_SUMMARY_OF_CITED_RECORDS"},
            "source_ref": c["source_ref"], "evidence_refs": refs})
        if not got["ok"]:
            raise RuntimeError(got["refusal"])
        return {"ok": True, "item_id": iid, "created": True}
    if item["stage"] in S.TERMINAL:
        return {"ok": True, "item_id": iid, "created": False}
    cited = {(r["kind"], str(r["id"])) for r in item["evidence_refs"]}
    for e in events:
        cited |= {(r["kind"], str(r["id"])) for r in e["evidence_refs"] or []}
    new = [r for r in refs if (r["kind"], r["id"]) not in cited]
    n_ev = sum(1 for e in events if e["stage"] == S.EVIDENCE)
    if new and item["stage"] == S.EVIDENCE and \
            n_ev < MAX_EVIDENCE_EVENTS_PER_ITEM:
        got = await add_event(conn, item, events, {
            "stage": S.EVIDENCE, "actor": S.RUNNER_ACTOR,
            "actor_class": S.RUNNER, "at": now,
            "body": {"statement": "%d further record(s) of the same signal"
                                  % len(new),
                     "basis": "RUNNER_SUMMARY_OF_CITED_RECORDS"},
            "source_ref": new[0], "evidence_refs": new})
        return {"ok": True, "item_id": iid, "created": False,
                "appended": len(new) if got["ok"] else 0}
    return {"ok": True, "item_id": iid, "created": False}


# ── 3 · DISAGREEMENTS ───────────────────────────────────────────────

async def open_disagreement(conn, item: dict, *, stage: str, parties: list,
                            positions: list, opened_at: float,
                            raised_event_id=None, key=()) -> str | None:
    did = S.disagreement_id_for(item["item_id"], stage, *key)
    if await conn.fetchval("SELECT 1 FROM improve_disagreements "
                           " WHERE disagreement_id=$1", did):
        return did
    await conn.execute(
        "INSERT INTO improve_disagreements (disagreement_id, item_id, stage, "
        " parties, positions, raised_event_id, opened_at, recorded_by) "
        " VALUES ($1,$2,$3,$4::text[],$5::jsonb,$6,to_timestamp($7),$8)",
        did, item["item_id"], stage, parties, json.dumps(positions, default=str),
        raised_event_id, float(opened_at), S.RUNNER_ACTOR)
    return did


async def resolve_disagreement(conn, did: str, *, state: str, by: str,
                               resolution: str, ref: dict, at: float) -> None:
    await conn.execute(
        "UPDATE improve_disagreements SET state=$2, resolved_by=$3, "
        " resolution=$4, resolution_ref=$5::jsonb, "
        " resolved_at=greatest(opened_at, to_timestamp($6)) "
        " WHERE disagreement_id=$1 AND state='OPEN'",
        did, state, by, resolution[:4000], json.dumps(ref), float(at))


def _karen_resolution(k: dict, parties: list) -> tuple | None:
    """(state, by, text) for a resolved Karen challenge, never consensus."""
    st = k.get("state")
    if st == "WITHDRAWN":
        return "WITHDRAWN", "KAREN", "Karen withdrew the challenge: %s" % (
            k.get("outcome_reason") or "")
    if st in ("UPHELD", "REJECTED") and k.get("resolved_by"):
        by = str(k["resolved_by"])
        if by.upper() in [p.upper() for p in parties]:
            return "CONCEDED", by, "%s conceded (challenge %s): %s" % (
                by, st, k.get("outcome_reason") or "")
        why = str(k.get("outcome_reason") or "").strip().rstrip(".")
        return "DECIDED", by, ("Independent evaluation by %s: %s. %s. The "
                               "losing position is preserved; this is a "
                               "decision, not agreement." % (by, st, why))
    return None


async def origin_disagreement(conn, item: dict, origin: dict) -> None:
    """A target that DISPUTED a challenge later UPHELD: the dissent stays."""
    if not origin or origin.get("kind") != "karen":
        return
    k = origin["row"]
    if k.get("response_stance") != "DISPUTE":
        return
    ref = {"kind": "karen_challenges", "id": k["challenge_id"]}
    parties = ["KAREN", k["target_agent"]]
    did = await open_disagreement(
        conn, item, stage="ORIGIN", parties=parties,
        positions=[{"party": "KAREN", "position": k["claim"],
                    "source_ref": ref},
                   {"party": k["target_agent"], "position": k["response"],
                    "source_ref": ref, "stance": "DISPUTE"}],
        opened_at=_ep(k["responded_at"]), key=(k["challenge_id"],))
    res = _karen_resolution(k, parties)
    if did and res:
        await resolve_disagreement(conn, did, state=res[0], by=res[1],
                                   resolution=res[2], ref=ref,
                                   at=_ep(k.get("resolved_at")) or 0)


# ── 4 · MIRRORS: STAGES THE AGENTS ALREADY RECORDED ──────────────────

async def _finding_stages(conn, fid: str) -> dict:
    if not fid or not await _regclass(conn, "agent_finding_stages"):
        return {}
    rows = await conn.fetch("SELECT * FROM agent_finding_stages "
                            " WHERE finding_id=$1 ORDER BY seq", fid)
    return {int(r["seq"]): _row(r) for r in rows}


async def _karen_on_finding(conn, fid: str) -> list:
    if not fid or not await _regclass(conn, "karen_challenges"):
        return []
    rows = await conn.fetch("SELECT * FROM karen_challenges "
                            " WHERE finding_id=$1 ORDER BY challenged_at, "
                            " challenge_id", fid)
    return [dict(r) for r in rows]


def _cls_for(item: dict, actor: str) -> str | None:
    a = str(actor or "").upper()
    if a == item["owner_agent"]:
        return S.OWNER_AGENT
    if a == S.KAREN:
        return S.CHALLENGER
    if a in S.AGENTS:
        return S.PEER_AGENT
    return None


async def mirror(conn, item: dict, events: list, *, linked: str | None,
                 scout_feature: str | None, now: float) -> list:
    """Append, in canonical order, every stage already on the agents'
    records; stop at the first one that is missing. Returns what was
    written. Never invents text: each row's body is the source's own."""
    wrote = []

    async def add(new):
        got = await add_event(conn, item, events, new)
        if got["ok"]:
            wrote.append(got)
        return got

    def have(stage, kind=None, rid=None) -> bool:
        return any(e["stage"] == stage and (
            kind is None or ((e.get("source_ref") or {}).get("kind") == kind
                             and str((e.get("source_ref") or {}).get("id"))
                             == str(rid))) for e in events)

    st = await _finding_stages(conn, linked) if linked else {}
    if linked and st:
        f = await conn.fetchrow("SELECT proposer FROM agent_findings "
                                " WHERE finding_id=$1", linked)
        if f is None or f["proposer"] != item["owner_agent"]:
            st = {}                 # the hypothesis must be the owner's own
    karen = await _karen_on_finding(conn, linked) if linked else []

    # HYPOTHESIS: the owner's own record
    if item["stage"] == S.EVIDENCE:
        if 2 in st:
            h = st[2]
            await add({"stage": S.HYPOTHESIS, "actor": h["actor"],
                       "actor_class": S.OWNER_AGENT, "at": _ep(h["at"]),
                       "body": {"hypothesis": h["body"].get("hypothesis"),
                                "finding_id": linked},
                       "source_ref": {"kind": "agent_finding_stages",
                                      "id": str(h["stage_id"])}})
        elif scout_feature and item["owner_agent"] == "SCOUT":
            r = await conn.fetchrow(
                "SELECT feature_id, predeclared_hypothesis, proposed_at "
                "  FROM scout_features WHERE feature_id=$1", scout_feature)
            if r is not None:
                await add({"stage": S.HYPOTHESIS, "actor": "SCOUT",
                           "actor_class": S.OWNER_AGENT, "at": now,
                           "body": {"hypothesis": r["predeclared_hypothesis"],
                                    "predeclared_at": _ep(r["proposed_at"])},
                           "source_ref": {"kind": "scout_features",
                                          "id": r["feature_id"]}})
    if item["stage"] not in (S.HYPOTHESIS, S.PEER_CHALLENGE):
        if item["stage"] == S.EVIDENCE or not st:
            return wrote

    # PEER CHALLENGES: the loop's peer, and Karen's challenges of it
    if item["stage"] in (S.HYPOTHESIS, S.PEER_CHALLENGE):
        if 3 in st and str(st[3]["actor"]).upper() != S.KAREN:
            c = st[3]
            ref = {"kind": "agent_finding_stages", "id": str(c["stage_id"])}
            if not have(S.PEER_CHALLENGE, ref["kind"], ref["id"]):
                got = await add({"stage": S.PEER_CHALLENGE, "actor": c["actor"],
                                 "actor_class": S.PEER_AGENT,
                                 "at": _ep(c["at"]), "stance": c["outcome"],
                                 "body": {"challenge": c["body"].get(
                                     "challenge")}, "source_ref": ref})
                if got["ok"] and c["outcome"] == "REFUTED" and 2 in st:
                    await open_disagreement(
                        conn, item, stage="PEER_CHALLENGE",
                        parties=[item["owner_agent"], c["actor"]],
                        positions=[
                            {"party": item["owner_agent"],
                             "position": st[2]["body"].get("hypothesis"),
                             "source_ref": {"kind": "agent_finding_stages",
                                            "id": str(st[2]["stage_id"])}},
                            {"party": c["actor"],
                             "position": c["body"].get("challenge"),
                             "source_ref": ref, "stance": "REFUTED"}],
                        opened_at=_ep(c["at"]), raised_event_id=got["event_id"],
                        key=(c["stage_id"],))
        for k in karen:
            ref = {"kind": "karen_challenges", "id": k["challenge_id"]}
            if not have(S.PEER_CHALLENGE, ref["kind"], ref["id"]):
                await add({"stage": S.PEER_CHALLENGE, "actor": S.KAREN,
                           "actor_class": S.CHALLENGER,
                           "at": _ep(k["challenged_at"]),
                           "stance": "CHALLENGES",
                           "body": {"challenge": k["claim"],
                                    "severity": k["severity"],
                                    "detector": k["detector"]},
                           "source_ref": ref})
        if 3 in st and str(st[3]["actor"]).upper() == S.KAREN and not any(
                e["actor_class"] == S.CHALLENGER for e in events):
            c = st[3]
            await add({"stage": S.PEER_CHALLENGE, "actor": S.KAREN,
                       "actor_class": S.CHALLENGER, "at": _ep(c["at"]),
                       "stance": c["outcome"],
                       "body": {"challenge": c["body"].get("challenge")},
                       "source_ref": {"kind": "agent_finding_stages",
                                      "id": str(c["stage_id"])}})

    # OWNER RESPONSE: the owner's recorded answer to Karen's challenge
    if item["stage"] in (S.PEER_CHALLENGE, S.OWNER_RESPONSE):
        for k in karen:
            if not k.get("responded_by") or \
                    k["responded_by"] != item["owner_agent"]:
                continue
            ref = {"kind": "karen_challenges", "id": k["challenge_id"]}
            if have(S.OWNER_RESPONSE, ref["kind"], ref["id"]):
                continue
            got = await add({"stage": S.OWNER_RESPONSE,
                             "actor": item["owner_agent"],
                             "actor_class": S.OWNER_AGENT,
                             "at": _ep(k["responded_at"]),
                             "stance": k["response_stance"],
                             "body": {"response": k["response"]},
                             "source_ref": ref})
            if got["ok"] and k["response_stance"] == "DISPUTE":
                parties = [S.KAREN, item["owner_agent"]]
                did = await open_disagreement(
                    conn, item, stage="OWNER_RESPONSE", parties=parties,
                    positions=[{"party": S.KAREN, "position": k["claim"],
                                "source_ref": ref},
                               {"party": item["owner_agent"],
                                "position": k["response"], "source_ref": ref,
                                "stance": "DISPUTE"}],
                    opened_at=_ep(k["responded_at"]),
                    raised_event_id=got["event_id"], key=(k["challenge_id"],))
                res = _karen_resolution(k, parties)
                if did and res:
                    await resolve_disagreement(
                        conn, did, state=res[0], by=res[1], resolution=res[2],
                        ref=ref, at=_ep(k.get("resolved_at")) or now)

    # EXPERIMENT: the loop's pre-registered experiment and candidate
    if item["stage"] in (S.OWNER_RESPONSE, S.EXPERIMENT):
        for seq in (4, 5):
            if seq not in st or str(st[seq]["actor"]).upper() != \
                    item["owner_agent"]:
                continue
            x = st[seq]
            ref = {"kind": "agent_finding_stages", "id": str(x["stage_id"])}
            if have(S.EXPERIMENT, ref["kind"], ref["id"]):
                continue
            exp_refs = [ref]
            if x.get("proposal_id"):
                exp_refs.append({"kind": "paper_improvement_proposals",
                                 "id": x["proposal_id"]})
            body = {"loop_stage": x["stage"], "scope": x.get("scope")}
            if seq == 4:
                body.update(design=x["body"].get("design"),
                            metric=x.get("metric"),
                            stopping_rule=x.get("stopping_rule"))
            else:
                body.update(description=x["body"].get("description"),
                            change=x["body"].get("change"))
            await add({"stage": S.EXPERIMENT, "actor": x["actor"],
                       "actor_class": S.OWNER_AGENT, "at": _ep(x["at"]),
                       "body": body, "source_ref": ref,
                       "experiment_refs": exp_refs})

    # INDEPENDENT EVALUATION: the loop's, by a non-owner evaluator agent
    if item["stage"] in (S.EXPERIMENT, S.INDEPENDENT_EVALUATION) and 6 in st:
        x = st[6]
        ref = {"kind": "agent_finding_stages", "id": str(x["stage_id"])}
        actor = str(x["actor"]).upper()
        if actor in S.EVALUATOR_AGENTS and actor != item["owner_agent"] and \
                not have(S.INDEPENDENT_EVALUATION, ref["kind"], ref["id"]):
            await add({"stage": S.INDEPENDENT_EVALUATION, "actor": actor,
                       "actor_class": S.INDEPENDENT_EVALUATOR,
                       "at": _ep(x["at"]), "outcome": x["outcome"],
                       "body": {"result": x["body"].get("result"),
                                "metric_value": x["body"].get("metric_value"),
                                "samples": x["body"].get("samples"),
                                "data_start": _ep(x.get("data_start")),
                                "data_end": _ep(x.get("data_end"))},
                       "source_ref": ref})

    # ELIGIBLE CHANGE: the loop's mark, never for a protected item
    if item["stage"] == S.INDEPENDENT_EVALUATION and 7 in st and \
            not item["requires_human_review"]:
        x = st[7]
        actor = str(x["actor"]).upper()
        if actor in S.EVALUATOR_AGENTS:
            await add({"stage": S.ELIGIBLE_CHANGE, "actor": actor,
                       "actor_class": S.INDEPENDENT_EVALUATOR,
                       "at": _ep(x["at"]),
                       "body": {"note": x["body"].get("note"),
                                "meaning": "eligible for a human release "
                                           "decision; nothing is released"},
                       "source_ref": {"kind": "agent_finding_stages",
                                      "id": str(x["stage_id"])}})

    # CLOSED: the loop closed the finding
    if 99 in st and item["stage"] not in S.TERMINAL:
        x = st[99]
        cls = _cls_for(item, x["actor"])
        if cls:
            await add({"stage": S.CLOSED, "actor": x["actor"],
                       "actor_class": cls, "at": _ep(x["at"]),
                       "body": {"reason": x["body"].get("reason")},
                       "source_ref": {"kind": "agent_finding_stages",
                                      "id": str(x["stage_id"])}})
    return wrote


# ═════════════════════════════════════════════════════════════════════
# 5 · THE PASS
# ═════════════════════════════════════════════════════════════════════

async def _phase(summary: dict, name: str, coro):
    try:
        async with asyncio.timeout(PHASE_TIMEOUT_S):
            return await coro
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                    # noqa: BLE001
        summary["errors"][name] = "%s: %s" % (type(exc).__name__,
                                              str(exc)[:160])
        return None


async def process(conn, c: dict, *, now: float) -> dict:
    """One candidate: upsert, then mirror, then its origin disagreement --
    in ONE runner transaction (a failure rolls back only this item)."""
    async with runner_tx(conn):
        got = await upsert_item(conn, c, now=now)
        if not got.get("ok"):
            return got
        item, events = await load(conn, got["item_id"])
        await origin_disagreement(conn, item, c.get("origin"))
        wrote = await mirror(conn, item, events,
                             linked=c.get("linked_finding"),
                             scout_feature=c.get("scout_feature"), now=now)
        return dict(got, mirrored=[w["stage"] for w in wrote],
                    stage=item["stage"])


async def pass_once(conn, *, now: float | None = None) -> dict:
    """ONE BOUNDED PASS. Never raises."""
    at = float(now if now is not None else time.time())
    t0 = time.monotonic()
    run_id = "improve-run:%s" % uuid.uuid4().hex
    summary: dict = {"run_id": run_id, "version": S.VERSION, "seen": {},
                     "created": [], "advanced": [], "refused": {},
                     "errors": {}, "authority": "NONE"}
    if not enabled():
        summary["status"] = "DISABLED"
        return summary
    if not await schema(conn):
        summary["status"] = "NO_SCHEMA"
        return summary
    cands: list = []
    for name, fn in SOURCES:
        got = await _phase(summary, "source:%s" % name, fn(conn, now=at))
        if got is not None:
            summary["seen"][name] = len(got)
            cands.extend(got)
    new_budget = MAX_NEW_ITEMS_PER_PASS
    for c in cands[:MAX_ADVANCE_PER_PASS]:
        if not enabled():                  # the kill switch, mid-pass too
            summary["errors"]["kill_switch"] = R_DISABLED
            break
        iid = S.item_id_for(c["source_kind"], c["source_key"])
        exists = await conn.fetchval("SELECT 1 FROM improve_items "
                                     " WHERE item_id=$1", iid)
        if not exists:
            if new_budget <= 0:
                continue
            new_budget -= 1
        got = await _phase(summary, "item:%s" % iid, process(conn, c, now=at))
        if got is None:
            continue
        if not got.get("ok"):
            summary["refused"][iid] = got.get("refusal")
        elif got.get("created"):
            summary["created"].append(iid)
        if got and got.get("mirrored"):
            summary["advanced"].append({"item_id": iid,
                                        "stages": got["mirrored"]})
    elapsed = round(time.monotonic() - t0, 3)
    status = "OK" if not summary["errors"] else (
        "FAILED" if not summary["seen"] else "PARTIAL")
    summary.update(status=status, elapsed_s=elapsed)
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO improve_runs (run_id, started_at, finished_at, "
                " status, summary, version) VALUES ($1, to_timestamp($2), "
                " to_timestamp($3), $4, $5::jsonb, $6)",
                run_id, at, at + elapsed, status,
                json.dumps({k: summary[k] for k in (
                    "seen", "created", "advanced", "refused", "errors")},
                    default=str), S.VERSION)
    except Exception as exc:                                    # noqa: BLE001
        summary["errors"]["run_record"] = type(exc).__name__
    try:
        from .. import db as _db
        await _db.heartbeat(SERVICE, "ok" if status == "OK" else "error",
                            {"run_id": run_id, "status": status,
                             "created": len(summary["created"]),
                             "advanced": len(summary["advanced"]),
                             "errors": summary["errors"]}, con=conn)
    except Exception:                                           # noqa: BLE001
        pass
    return summary


async def run(get_pool, *, interval_s: float = INTERVAL_S,
              first_delay_s: float = FIRST_DELAY_S) -> None:
    """THE LOOP, armed from the API lifespan; never raises except to be
    cancelled. The kill switch is read before every pass."""
    if not enabled():
        log.info("improvement pipeline disabled (%s)", ENV_KILL)
        return
    await asyncio.sleep(first_delay_s)
    while True:
        try:
            if enabled():
                pool = await get_pool()
                async with asyncio.timeout(PASS_TIMEOUT_S):
                    async with pool.acquire() as conn:
                        await pass_once(conn)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                # noqa: BLE001
            log.warning("improvement pipeline: pass failed (%s)",
                        type(exc).__name__)
        await asyncio.sleep(interval_s)

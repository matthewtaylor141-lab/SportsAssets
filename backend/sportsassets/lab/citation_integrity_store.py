"""AGENT CITATION INTEGRITY -- the append-only verdict ledger, the per-agent
scorecard and the retrospective (LAB-B; migration 243).

WRITES only `agent_citation_checks` / `agent_citation_verdicts` (append-only,
authority SHADOW_RESEARCH_ONLY): one row per verified answer text and one per
material sentence -- agent, conversation and message id, the sentence's
sha256, the cited fact ids and the records they name, the verdict and the
action taken. Nothing on a decision path reads them.

READS go through ONE point-in-time accessor (`as_of`): every historical row is
admitted only when its own timestamp is at or before the clock the caller
names, so a scorecard or a retrospective computed "as of" a moment can never
see a row recorded after it.

THE SCORECARD (`citation_metrics`, the one function another module calls --
r30b-agents' economic scorecards read it):

    citation_metrics(conn, agent="XAVIER", window_s=7 * 86400, now=time.time())
      -> [metric, ...] in CITATION_METRICS order

each metric shaped like agents/agent_scorecards.metric (value, unit, n,
numerator, denominator, window {start, end, seconds, basis}, status MEASURED /
SMALL_SAMPLE / UNAVAILABLE, reason, direction, definition, source, detail with
the Wilson 95% interval). Measured over each answer's PRIMARY text -- the
model's reply when a model composed one, else the records-only answer:

  citation_coverage_pct            100 x material sentences with an [F#] /
                                   material sentences
  wrong_fact_rate                  WRONG_FACT / cited material sentences
  unsupported_material_claim_rate  INSUFFICIENT_SUPPORT / material sentences
  stale_state_citation_rate        STALE_STATE_CITATION / cited material
                                   sentences
  records_only_fallback_rate       model replies discarded by the integrity
                                   gate / model replies checked
  correction_rate                  sentences repaired by deterministic
                                   re-citation / sentences that failed

No answers in the window -> every metric UNAVAILABLE with the reason, never 0.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import time

from . import citation_integrity as CI

SCHEMA = "bettor.lab.citation_integrity.v1"
CITATION_METRICS = ("citation_coverage_pct", "wrong_fact_rate",
                    "unsupported_material_claim_rate",
                    "stale_state_citation_rate",
                    "records_only_fallback_rate", "correction_rate")
MIN_N = 30
DEFAULT_WINDOW_S = 7 * 86400.0
MEASURED, SMALL, UNAVAILABLE = "MEASURED", "SMALL_SAMPLE", "UNAVAILABLE"
LOWER, HIGHER = "LOWER_IS_BETTER", "HIGHER_IS_BETTER"
R_NO_SCHEMA = "MIGRATION_243_NOT_APPLIED"

# ═════════════════════════════════════════════════════════════════════
# THE ONE POINT-IN-TIME ACCESSOR
# ═════════════════════════════════════════════════════════════════════

#: source -> (SQL whose WHERE ends with the clock filter, its time column).
#: Every row's OWN timestamp is compared with the clock; a row recorded
#: after the clock does not exist for the caller.
_SOURCES = {
    "checks": (
        "SELECT c.* FROM agent_citation_checks c "
        " WHERE c.checked_at <= to_timestamp($1) "
        "   AND c.recorded_at <= to_timestamp($1) "
        "   AND c.checked_at > to_timestamp($2) "
        "   AND ($3::text IS NULL OR c.agent_id = $3) "
        " ORDER BY c.checked_at DESC, c.check_id DESC LIMIT $4"),
    "verdicts": (
        "SELECT v.* FROM agent_citation_verdicts v "
        "  JOIN agent_citation_checks c USING (check_id) "
        " WHERE c.checked_at <= to_timestamp($1) "
        "   AND v.recorded_at <= to_timestamp($1) "
        "   AND c.checked_at > to_timestamp($2) "
        "   AND ($3::text IS NULL OR v.agent_id = $3) "
        " ORDER BY v.verdict_id DESC LIMIT $4"),
    # the stored answers of the persona chat (migration 180) with the
    # question each answered: the retrospective's input
    "answers": (
        "SELECT m.message_id, m.conversation_id, m.agent_id, m.at, m.body, "
        "       m.facts, m.provider, m.outcome, u.body AS question "
        "  FROM agent_chat_messages m "
        "  LEFT JOIN agent_chat_messages u ON u.message_id = m.in_reply_to "
        " WHERE m.role = 'ASSISTANT' AND m.status = 'COMPLETE' "
        "   AND m.at <= to_timestamp($1) "
        "   AND m.recorded_at <= to_timestamp($1) "
        "   AND m.at > to_timestamp($2) "
        "   AND ($3::text IS NULL OR m.agent_id = $3) "
        "   AND m.body ~ '\\[F[0-9]+\\]' "
        " ORDER BY m.at DESC, m.message_id DESC LIMIT $4"),
}


async def as_of(conn, source: str, *, clock: float, since: float,
                agent: str | None = None, limit: int = 5000) -> list:
    """Rows of `source` whose own timestamp is in (since, clock] -- the only
    way this module reads history."""
    sql = _SOURCES[source]
    return [dict(r) for r in await conn.fetch(
        sql, float(clock), float(since), agent, int(limit))]


async def schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT to_regclass('agent_citation_checks') IS NOT NULL AND "
            "       to_regclass('agent_citation_verdicts') IS NOT NULL"))
    except Exception:                                           # noqa: BLE001
        return False


# ═════════════════════════════════════════════════════════════════════
# RECORDING (append-only)
# ═════════════════════════════════════════════════════════════════════

def _j(v) -> str:
    return json.dumps(v, sort_keys=True, default=str)


def _cited_records(row: dict, facts_by_id: dict) -> list:
    out = []
    for fid in row["cited_ids"]:
        f = facts_by_id.get(fid)
        out.append({"fact_id": fid,
                    "source": (f or {}).get("source"),
                    "record_id": (f or {}).get("record_id"),
                    "field": (f or {}).get("field"),
                    "known": f is not None})
    return out


async def record(conn, *, agent: str, conversation_id: str,
                 message_id: str | None, turn_id: str | None, stage: str,
                 checked: dict, facts: list, primary: bool, published: bool,
                 now: float) -> int:
    """One verified text (a `citation_integrity.gate` result) and its
    per-sentence verdicts, in one transaction. Returns the check id."""
    before = checked["before"]
    composer = CI.COMPOSER_MODEL if stage in (CI.ST_MODEL, CI.ST_PARTIAL) \
        else CI.COMPOSER_RECORDS
    by_id = {str(f.get("fact_id")): f for f in facts or []
             if isinstance(f, dict)}
    repaired = sum(1 for r in checked["sentences"]
                   if r["action"] == CI.A_REPAIRED)
    async with conn.transaction():
        cid = await conn.fetchval(
            "INSERT INTO agent_citation_checks (agent_id, conversation_id, "
            " message_id, turn_id, stage, composer, primary_text, published, "
            " checked_at, sentences, material_sentences, "
            " cited_material_sentences, verdict_counts, repaired_sentences, "
            " action, integrity_reason, verifier_version) VALUES ($1,$2,$3,"
            " $4,$5,$6,$7,$8,to_timestamp($9),$10,$11,$12,$13::jsonb,$14,"
            " $15,$16,$17) RETURNING check_id",
            agent, conversation_id, message_id, turn_id, stage, composer,
            bool(primary), bool(published), float(now),
            int(before["sentence_count"]), int(before["material"]),
            int(before["cited_material"]), _j(before["counts"]),
            int(repaired), checked["action"],
            checked.get("reason") if checked["action"] in (
                CI.A_FALLBACK, CI.A_STATED) else None, CI.VERSION)
        for r in checked["sentences"]:
            await conn.execute(
                "INSERT INTO agent_citation_verdicts (check_id, agent_id, "
                " conversation_id, message_id, sentence_index, "
                " sentence_sha256, cited_fact_ids, cited_records, "
                " categories, verdict, failing, action, repaired_fact_ids, "
                " verifier_version) VALUES ($1,$2,$3,$4,$5,$6,$7::text[],"
                " $8::jsonb,$9::text[],$10,$11::jsonb,$12,$13::text[],$14)",
                cid, agent, conversation_id, message_id, int(r["index"]),
                r["sha256"], list(r["cited_ids"]),
                _j(_cited_records(r, by_id)), list(r["categories"]),
                r["verdict"], _j(r["failing"]), r["action"],
                list(r["repaired_ids"]), CI.VERSION)
    return int(cid)


# ═════════════════════════════════════════════════════════════════════
# THE SCORECARD (pure over rows)
# ═════════════════════════════════════════════════════════════════════

def wilson(k: int, n: int, z: float = 1.959964) -> list | None:
    """The Wilson score 95% interval for k successes in n (None for n=0)."""
    if not n:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, centre - half), 6), round(min(1.0, centre + half),
                                                     6)]


def window(now: float, seconds: float) -> dict:
    return {"start": float(now) - float(seconds), "end": float(now),
            "seconds": float(seconds), "basis": "TRAILING"}


DEFINITIONS = {
    "citation_coverage_pct": (
        "100 x material sentences carrying at least one [F#] / material "
        "sentences, over each answer's primary text", "%", HIGHER),
    "wrong_fact_rate": (
        "WRONG_FACT sentences / cited material sentences: the cited fact does "
        "not hold the sentence's material while another fact does", "ratio",
        LOWER),
    "unsupported_material_claim_rate": (
        "INSUFFICIENT_SUPPORT sentences / material sentences: no fact in the "
        "answer's list holds the cited material", "ratio", LOWER),
    "stale_state_citation_rate": (
        "STALE_STATE_CITATION sentences / cited material sentences: a "
        "SUPERSEDED / HISTORICAL record cited for the current state", "ratio",
        LOWER),
    "records_only_fallback_rate": (
        "model replies discarded by the citation-integrity gate (records-only "
        "answer published with the reason) / model replies checked", "ratio",
        LOWER),
    "correction_rate": (
        "sentences repaired by deterministic re-citation / sentences that "
        "failed verification (a repair inside a discarded reply does not "
        "count)", "ratio", HIGHER),
}


def _metric(agent, name, k, n, win, why, *, scale=1.0, detail=None):
    definition, unit, direction = DEFINITIONS[name]
    src = ["agent_citation_checks", "agent_citation_verdicts"]
    if not n:
        return {"agent": agent, "metric": name, "value": None, "unit": unit,
                "n": 0, "numerator": k, "denominator": 0, "window": win,
                "status": UNAVAILABLE, "reason": why,
                "direction": direction, "definition": definition,
                "source": src, "detail": dict(detail or {}, interval95=None,
                                              method="WILSON")}
    value = round(scale * k / n, 6)
    ci = wilson(k, n)
    status = MEASURED if n >= MIN_N else SMALL
    return {"agent": agent, "metric": name, "value": value, "unit": unit,
            "n": int(n), "numerator": int(k), "denominator": int(n),
            "window": win, "status": status,
            "reason": None if status == MEASURED else
            "n=%d < %d: shown, nothing concluded" % (n, MIN_N),
            "direction": direction, "definition": definition, "source": src,
            "detail": dict(detail or {}, method="WILSON", interval95=[
                round(scale * ci[0], 6), round(scale * ci[1], 6)])}


def metrics_from_rows(agent: str, checks: list, verdicts: list,
                      win: dict) -> list:
    """The six metrics for one agent from its ledger rows. Pure."""
    prim = [c for c in checks if c.get("primary_text")
            and c.get("stage") != CI.ST_PARTIAL]
    prim_ids = {c["check_id"] for c in prim}
    counts = {v: 0 for v in CI.VERDICTS}
    material = cited = 0
    for c in prim:
        vc = c.get("verdict_counts")
        vc = json.loads(vc) if isinstance(vc, str) else (vc or {})
        for k in counts:
            counts[k] += int(vc.get(k) or 0)
        material += int(c.get("material_sentences") or 0)
        cited += int(c.get("cited_material_sentences") or 0)
    pv = [v for v in verdicts if v.get("check_id") in prim_ids]
    failing = sum(1 for v in pv if v["verdict"] != CI.PASS)
    repaired = sum(1 for v in pv if v["action"] == CI.A_REPAIRED)
    model = [c for c in prim if c.get("stage") == CI.ST_MODEL]
    fell = sum(1 for c in model if c["action"] == CI.A_FALLBACK)
    none = "NO_CHECKED_ANSWERS_IN_WINDOW" if not prim else None
    detail = {"answers": len(prim), "material_sentences": material,
              "cited_material_sentences": cited, "verdict_counts": counts,
              "entity_mismatch": counts[CI.ENTITY_MISMATCH],
              "no_citation": counts[CI.NO_CITATION]}
    return [
        _metric(agent, "citation_coverage_pct", cited, material, win,
                none or "NO_MATERIAL_SENTENCES", scale=100.0, detail=detail),
        _metric(agent, "wrong_fact_rate", counts[CI.WRONG_FACT], cited, win,
                none or "NO_CITED_MATERIAL_SENTENCES", detail=detail),
        _metric(agent, "unsupported_material_claim_rate",
                counts[CI.INSUFFICIENT_SUPPORT], material, win,
                none or "NO_MATERIAL_SENTENCES", detail=detail),
        _metric(agent, "stale_state_citation_rate",
                counts[CI.STALE_STATE_CITATION], cited, win,
                none or "NO_CITED_MATERIAL_SENTENCES", detail=detail),
        _metric(agent, "records_only_fallback_rate", fell, len(model), win,
                none or "NO_MODEL_REPLIES_CHECKED", detail=dict(
                    detail, model_replies=len(model))),
        _metric(agent, "correction_rate", repaired, failing, win,
                none or "NO_FAILED_SENTENCES", detail=dict(
                    detail, failed_sentences=failing)),
    ]


async def citation_metrics(conn, *, agent: str,
                           window_s: float = DEFAULT_WINDOW_S,
                           now: float | None = None) -> list:
    """THE ONE FUNCTION another scorecard reads: the six metrics for `agent`
    over the trailing window ending at `now` (see the module docstring).
    Without migration 243 every metric is UNAVAILABLE (MIGRATION_243_NOT
    _APPLIED), never zero."""
    now = time.time() if now is None else float(now)
    win = window(now, window_s)
    ag = str(agent or "").upper()
    if not await schema(conn):
        return [dict(m, reason=R_NO_SCHEMA)
                for m in metrics_from_rows(ag, [], [], win)]
    checks = await as_of(conn, "checks", clock=now, since=now - window_s,
                         agent=ag)
    verdicts = await as_of(conn, "verdicts", clock=now,
                           since=now - window_s, agent=ag, limit=50000)
    return metrics_from_rows(ag, checks, verdicts, win)


async def scorecards(conn, *, agents=None, window_s: float = DEFAULT_WINDOW_S,
                     now: float | None = None) -> dict:
    now = time.time() if now is None else float(now)
    agents = list(agents or ("DEREK", "XAVIER", "AUDREY", "KAREN", "EDDIE",
                             "SCOUT"))
    return {a: await citation_metrics(conn, agent=a, window_s=window_s,
                                      now=now) for a in agents}


async def recent_failures(conn, *, now: float, window_s: float,
                          limit: int = 20) -> list:
    if not await schema(conn):
        return []
    rows = await as_of(conn, "verdicts", clock=now, since=now - window_s,
                       limit=500)
    out = []
    for r in rows:
        if r["verdict"] == CI.PASS:
            continue
        out.append({k: (v.isoformat() if isinstance(v, _dt.datetime) else
                        (json.loads(v) if k in ("failing", "cited_records")
                         and isinstance(v, str) else v))
                    for k, v in r.items()})
        if len(out) >= limit:
            break
    return out


# ═════════════════════════════════════════════════════════════════════
# THE RETROSPECTIVE (stored answers, re-verified)
# ═════════════════════════════════════════════════════════════════════

def _obj(v, default):
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


async def retrospective(conn, *, clock: float, since: float,
                        agent: str | None = None, limit: int = 2000,
                        profile: str = CI.PROFILE_FULL) -> dict:
    """Every stored, cited answer in (since, clock] re-verified against the
    facts it CITED (the only facts migration 180 persists). Returns the
    per-agent tally (`citation_integrity.retro_tally`)."""
    if not await conn.fetchval(
            "SELECT to_regclass('agent_chat_messages') IS NOT NULL"):
        return {"status": UNAVAILABLE, "why": "MIGRATION_180_NOT_APPLIED"}
    rows = await as_of(conn, "answers", clock=clock, since=since,
                       agent=agent, limit=limit)
    reports = []
    for r in rows:
        facts = _obj(r.get("facts"), [])
        prov = _obj(r.get("provider"), {})
        mode = prov.get("mode") or r.get("outcome")
        rep = CI.verify(r["body"], facts, question=r.get("question") or "",
                        profile=profile,
                        skip_prefixes=(RECORDS_LABEL_PREFIX,))
        reports.append((r["agent_id"], mode, rep))
    out = CI.retro_tally(reports)
    out.update({"profile": profile, "answers_read": len(rows),
                "clock": clock, "since": since,
                "fact_sets": "CITED_FACTS_ONLY (migration 180 stores the "
                             "facts an answer cited, not the full list it "
                             "was composed from)"})
    return out


#: the records-only fallback's own label sentence (persona_chat
#: FALLBACK_LABEL) is a disclosure, not a claim about records
RECORDS_LABEL_PREFIX = "Records-only answer — the AI answer was not used"

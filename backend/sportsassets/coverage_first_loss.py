"""THE FIRST-LOSS CENSUS: WHERE EACH PROVIDER EVENT WAS LOST, AND WHY.

THE QUESTION (coverage failures, 2026-10-05: NCAAF 58 provider events -> 0
evaluated; PINNAPI_NATIVE:BASKETBALL "normalization failure"; Brazil Serie B
ratio collapse; UEFA Nations League normalization / entry collapse). The
funnel (agents.coverage_integrity) counts how many events reached each stage
per league per day; it cannot say, for one event, WHERE it stopped and on
WHICH code, nor whether that code is ours to fix. This census does, for every
provider event the collector recorded in the window:

    PROVIDER -> NORMALIZED -> MAPPED -> SETTLEMENT -> MODEL -> FAIR_VALUE
             -> BOOK -> EV -> ENTER_PASS

  PROVIDER     the provider event was recorded but never judged (deferred by
               the evaluation bound, an unclassified ledger outcome, an
               ingestion refusal)
  NORMALIZED   the provider record gave no usable fixture price (no Pinnacle
               quote, a WS price refused, the provider's own record unread)
  MAPPED       no venue contract was established (the venue does not list the
               fixture, identity ambiguous, a family with no venue winner)
  SETTLEMENT   the two payoff functions were not shown equivalent
  MODEL        no probability source exists for the market (a capability)
  FAIR_VALUE   a probability source exists but no current fair value (thin
               outcomes, a stale provider quote)
  BOOK         the venue book was not read, not current, or too thin
  EV           judged on its economics: no positive net edge
  ENTER_PASS   reached a decision: ENTER, or PASS by a rail / policy code

EVERY EVENT HAS EXACTLY ONE FIRST LOSS (or ENTERED). Its stage is the
EARLIEST chain stage among the codes of the furthest record it reached --
the paper decision, else the sealed valuation, else the collector's event
ledger row that ranked furthest (coverage_integrity.REACH_SQL, with the
ledger stage derived for rows written without one). Each code carries its
class:

  EXTERNAL     the missing input is someone else's -- the venue does not list
               the fixture, the provider carries no Pinnacle price, the venue
               does not document its book timing -- by the lane's own
               evaluability table (bettor_external_shadow.EVALUABILITY_OF ==
               EXTERNAL_DEPENDENCY); the code IS the evidence
  SOFTWARE     the one refusal taxonomy's SOFTWARE class: engineering work
  ECONOMIC     the taxonomy's ECONOMIC class: the system working as designed
  UNCLASSIFIED a code neither table knows -- by name, never guessed

NO THRESHOLD, GATE OR RULE IS READ OR MOVED HERE: the census classifies the
codes the deciding paths already wrote.

EVERY COUNT NAMES ITS SOURCE TABLE (`count_sources`, and `source` on every
code row). A source that is absent or unreadable is UNAVAILABLE with its
reason: the stages it alone measures are null, never zero, and an event
whose fate past mapping depends on it is counted UNAVAILABLE, not lost.

Read-only: every statement is a bounded SELECT; the route runs them inside a
READ ONLY transaction under a statement timeout. Pure apart from `read`.
"""
from __future__ import annotations

import json
from typing import Any

from . import bettor_external_shadow as ext
from . import refusal_taxonomy as RT

VERSION = "COVERAGE_FIRST_LOSS_CENSUS_V1"

CHAIN = ("PROVIDER", "NORMALIZED", "MAPPED", "SETTLEMENT", "MODEL",
         "FAIR_VALUE", "BOOK", "EV", "ENTER_PASS")
ENTERED = "ENTERED"
UNAVAILABLE = "UNAVAILABLE"
EXTERNAL = "EXTERNAL"
CLASSES = (RT.SOFTWARE, RT.ECONOMIC, EXTERNAL, RT.UNCLASSIFIED)

DEFAULT_HOURS = 24
MAX_HOURS = 7 * 24
MAX_EVENTS = 20000
MAX_VALUATIONS = 50000
MAX_CODES_PER_GROUP = 40

#: the census's own codes for an event whose record carries no refusal code
#: (each classified in refusal_taxonomy_table)
R_DEFERRED = "PROVIDER_EVENT_DEFERRED_BY_THE_EVALUATION_BOUND"
R_LEDGER_UNCLASSIFIED = "PROVIDER_EVENT_LEDGER_OUTCOME_UNCLASSIFIED"
R_ADMITTED_NO_VALUATION = "ADMITTED_EVENT_HAS_NO_LINKED_VALUATION"
R_NO_DECISION = "VALUATION_REACHED_NO_PAPER_DECISION"
R_DECISION_NAMES_NO_CODE = "PAPER_DECISION_REFUSED_WITHOUT_A_CODE"

#: the table each chain stage is measured from
STAGE_SOURCE = {
    "PROVIDER": "ext_candidate_outcomes", "NORMALIZED": "ext_candidate_outcomes",
    "MAPPED": "ext_candidate_outcomes",
    "SETTLEMENT": "ext_candidate_outcomes + external_valuations + paper_decisions",
    "MODEL": "external_valuations + paper_decisions",
    "FAIR_VALUE": "ext_candidate_outcomes + external_valuations + paper_decisions",
    "BOOK": "ext_candidate_outcomes + external_valuations + paper_decisions",
    "EV": "ext_candidate_outcomes + external_valuations + paper_decisions",
    "ENTER_PASS": "paper_decisions"}
#: the stages a reader past the ledger alone measures
DOWNSTREAM = ("SETTLEMENT", "MODEL", "FAIR_VALUE", "BOOK", "EV", "ENTER_PASS")

#: lane stage (ext_candidate_outcomes.stage) -> chain stage
_LANE = {"3": "MAPPED", "4": "SETTLEMENT", "5": "BOOK", "6": "ENTER_PASS",
         "7": "ENTER_PASS", "8": "EV"}
#: refusal-taxonomy stage -> chain stage (PROBABILITY / FRESHNESS /
#: AGENT_EVALUATION are refined by family in `chain_stage`)
_TAXO = {"INGESTION": "PROVIDER", "NORMALIZATION": "NORMALIZED",
         "EVENT_IDENTITY": "MAPPED", "VENUE_MAPPING": "MAPPED",
         "MARKET_FAMILY": "MAPPED",
         "SETTLEMENT_COMPATIBILITY": "SETTLEMENT", "VENUE_BOOK": "BOOK",
         "EV": "EV", "RISK_ADMISSION": "ENTER_PASS", "ORDER": "ENTER_PASS",
         "FILL": "ENTER_PASS", "MANAGEMENT": "ENTER_PASS",
         "ACCOUNTING": "ENTER_PASS", "OUT_OF_FUNNEL": "ENTER_PASS"}
#: ledger outcomes that are not refusals
_NOT_REFUSAL = ("ADMITTED", "ENTRY_INVENTORY_WRITTEN", "EXPOSURE_RESERVED",
                "DUPLICATE_OBSERVATION_SKIPPED")


def _code(c) -> str | None:
    """The code token: a WS wrapper is read as the code it wraps."""
    s = str(c or "").strip()
    if not s:
        return None
    head, _, inner = s.partition(":")
    if head == ext.WS_REFERENCE_WRAPPER:
        return RT.normalize(inner) or head
    return RT.normalize(s)


def classify(code) -> dict:
    """{code, class, family, taxonomy_stage, evidence} for one code. Pure."""
    c = _code(code)
    k = RT.classify(c)
    if ext.EVALUABILITY_OF.get(c) == ext.EXTERNAL_DEPENDENCY:
        return {"code": c, "class": EXTERNAL, "family": k.get("family"),
                "taxonomy_stage": k.get("stage"),
                "evidence": ("bettor_external_shadow.EVALUABILITY_OF[%s] = "
                             "EXTERNAL_DEPENDENCY" % c)}
    return {"code": c, "class": k["class"], "family": k.get("family"),
            "taxonomy_stage": k.get("stage"),
            "evidence": ("refusal_taxonomy_table" if k.get("classified")
                         else k.get("why"))}


def chain_stage(code, *, lane_stage=None, mapped=False,
                default="ENTER_PASS") -> str:
    """The chain stage a code stops an event at. The lane stage the record
    carries (or the one taxonomy's lane stage of the code) first: stages 1
    and 2 before a venue contract existed are NORMALIZED, after it MODEL (a
    missing capability) / FAIR_VALUE / BOOK (a venue-book code); 3.. map
    directly. Else the refusal taxonomy's stage. Else `default`. Pure."""
    c = _code(code)
    k = RT.lookup(c) or (None, None, None)
    lane = str(lane_stage or ext.ledger_stage_of(code) or "")
    if lane[:1] in _LANE:
        return _LANE[lane[:1]]
    if lane[:1] in ("1", "2"):
        if not mapped:
            return "NORMALIZED"
        if k[2] == "VENUE_BOOK" or str(c).startswith("VENUE_"):
            return "BOOK"
        return "MODEL" if (lane[:1] == "1" and k[1] == "CAPABILITY") \
            else "FAIR_VALUE"
    st = k[2]
    if st is None:
        return default
    if st == "PROBABILITY":
        return "MODEL" if k[1] == "CAPABILITY" else "FAIR_VALUE"
    if st == "FRESHNESS":
        return "BOOK" if ("VENUE" in str(c) or "BOOK" in str(c)) \
            else "FAIR_VALUE"
    if st == "AGENT_EVALUATION":
        return {"EDGE": "EV", "EV": "EV", "PRICE": "EV",
                "DEPTH": "BOOK"}.get(k[1], "ENTER_PASS")
    return _TAXO.get(st, default)


def _earliest(codes, *, mapped, default) -> tuple:
    """(chain stage, code) of the earliest-stage code; ties keep record
    order. (None, None) for no code."""
    best = None
    for c in codes or ():
        if _code(c) is None:
            continue
        st = chain_stage(c, mapped=mapped, default=default)
        if best is None or CHAIN.index(st) < CHAIN.index(best[0]):
            best = (st, c)
    return best or (None, None)


#: A DECISION CODE THAT ONLY SAYS "THE LANE REFUSED THE PROBABILITY"
#: (paper_benchmark.R_PROBABILITY_UNQUALIFIED). Its cause is the lane's own
#: probability-stage code, which the decision now carries right behind it
#: (paper_benchmark.contract_match); the census attributes the loss to that
#: carried code, by its own class -- never to the wrapper, and never by
#: guessing: a decision written before the codes were carried keeps the
#: wrapper (SOFTWARE), named.
LANE_WRAPPERS = frozenset(("PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE",))


def _unwrap(codes, best, *, mapped) -> tuple:
    """(chain stage, code, wrapper or None): a LANE_WRAPPERS code replaced by
    the first lane probability-stage code recorded after it. Pure."""
    st, c = best
    if c is None or _code(c) not in LANE_WRAPPERS:
        return st, c, None
    seq = [str(x) for x in (codes or ())]
    try:
        i = seq.index(str(c))
    except ValueError:
        return st, c, None
    for x in seq[i + 1:]:
        k = _code(x)
        if k is None or k in LANE_WRAPPERS:
            continue
        # only a code one of the two tables knows: an unclassified carried
        # code would replace a named wrapper with an unknown
        if (ext.STAGE_OF.get(k) == "1_PROBABILITY"
                and (RT.lookup(k) is not None
                     or ext.EVALUABILITY_OF.get(k) is not None)):
            return chain_stage(x, mapped=mapped), x, c
    return st, c, None


def _jsonish(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return [v]
    return v


def _ledger_codes(ev: dict) -> list:
    """The ledger row's refusal codes in the order they fired, the first
    refusal first, wrappers unwrapped, non-refusals dropped."""
    out = []
    for c in [ev.get("first_refusal")] + list(_jsonish(ev.get("codes")) or []):
        s = str(c or "")
        if not s or s in _NOT_REFUSAL or s.startswith("FUNDED:") or \
                s == ext.WS_REFERENCE_WRAPPER:
            continue
        if s not in out:
            out.append(s)
    return out


def first_loss_of_event(ev: dict, valuations: list, decisions: list, *,
                        valuations_read: bool, decisions_read: bool) -> dict:
    """ONE provider event's first loss. `ev` is its furthest ledger row
    (LEDGER_EVENTS_SQL); `valuations` the sealed valuations linked to it;
    `decisions` the paper decisions on those. Pure."""
    out = {"stage": None, "code": None, "class": None, "source": None,
           "codes": []}
    # 1 · A PAPER DECISION: ENTER anywhere is ENTERED; else every REFUSE
    if decisions:
        if any(str(d.get("verdict") or "").upper() == "ENTER"
               for d in decisions):
            return dict(out, stage=ENTERED, source="paper_decisions",
                        strategies=sorted({str(d.get("strategy"))
                                           for d in decisions
                                           if str(d.get("verdict") or "")
                                           .upper() == "ENTER"}))
        best = None
        for d in decisions:
            codes = list(d.get("refusals") or []) or \
                ([d["refusal"]] if d.get("refusal") else [])
            st, c = _earliest(codes, mapped=True, default="ENTER_PASS")
            if st is None:
                st, c = "ENTER_PASS", R_DECISION_NAMES_NO_CODE
            st, c, wrapped = _unwrap(codes, (st, c), mapped=True)
            if best is None or CHAIN.index(st) < CHAIN.index(best[0]):
                best = (st, c, codes, wrapped)
        k = classify(best[1])
        res = dict(out, stage=best[0], code=k["code"],
                   **{"class": k["class"]}, family=k["family"],
                   evidence=k["evidence"], source="paper_decisions",
                   codes=list(best[2])[:8])
        if best[3]:
            res["carried_by"] = _code(best[3])
        return res
    reach = int(ev.get("reach") or 0)
    if valuations:
        if not decisions_read:
            return dict(out, stage=UNAVAILABLE, source="paper_decisions",
                        why="PAPER_DECISIONS_UNREAD")
        best = None
        for v in valuations:
            codes = list(v.get("refusals") or [])
            st, c = _earliest(codes, mapped=True, default="ENTER_PASS")
            if st is not None and (best is None or
                                   CHAIN.index(st) < CHAIN.index(best[0])):
                best = (st, c, codes)
        if best is None:
            best = ("ENTER_PASS", R_NO_DECISION, [])
        k = classify(best[1])
        return dict(out, stage=best[0], code=k["code"], **{"class": k["class"]},
                    family=k["family"], evidence=k["evidence"],
                    source="external_valuations", codes=list(best[2])[:8])
    outcome = str(ev.get("outcome") or "")
    if (reach >= 4 or outcome in ("ADMITTED", "ALREADY_RECORDED")) and \
            not valuations_read:
        return dict(out, stage=UNAVAILABLE, source="external_valuations",
                    why="EXTERNAL_VALUATIONS_UNREAD")
    codes = _ledger_codes(ev)
    mapped = bool(ev.get("us_market_slug")) or reach >= 4
    if outcome == "DEFERRED":
        st, c = "PROVIDER", R_DEFERRED
    elif outcome in ("ADMITTED", "ALREADY_RECORDED"):
        st, c = "FAIR_VALUE", R_ADMITTED_NO_VALUATION
    elif outcome == "REFUSED" and codes:
        c = codes[0]
        st = chain_stage(c, lane_stage=ev.get("stage"), mapped=mapped,
                         default="PROVIDER")
    else:
        st, c = "PROVIDER", R_LEDGER_UNCLASSIFIED
    k = classify(c)
    return dict(out, stage=st, code=k["code"], **{"class": k["class"]},
                family=k["family"], evidence=k["evidence"],
                source="ext_candidate_outcomes", codes=codes[:8],
                lane_stage=ev.get("stage"))


def _blank(name, family) -> dict:
    return {"league_name": name, "sport_family": family,
            "provider_events": 0,
            "provider_event_sources": {"metered": 0, "pinnapi_native": 0},
            "entered": 0, "unavailable": 0,
            "first_loss": {s: 0 for s in CHAIN},
            "by_class": {k: 0 for k in CLASSES},
            "_codes": {}}


def _add(agg: dict, ev: dict, fl: dict) -> None:
    agg["provider_events"] += 1
    src = ("pinnapi_native" if str(ev.get("provider_event_id") or "")
           .startswith("pinnapi:") else "metered")
    agg["provider_event_sources"][src] += 1
    st = fl["stage"]
    if st == ENTERED:
        agg["entered"] += 1
        return
    if st == UNAVAILABLE:
        agg["unavailable"] += 1
        return
    agg["first_loss"][st] += 1
    agg["by_class"][fl["class"]] = agg["by_class"].get(fl["class"], 0) + 1
    key = (st, fl["code"], fl["class"], fl.get("source"))
    row = agg["_codes"].setdefault(key, {
        "stage": st, "code": fl["code"], "class": fl["class"],
        "family": fl.get("family"), "source": fl.get("source"),
        "evidence": fl.get("evidence"), "events": 0, "sample": []})
    row["events"] += 1
    if len(row["sample"]) < 3:
        row["sample"].append({k: ev.get(k) for k in (
            "provider_event_id", "home", "away", "commence_time",
            "us_market_slug")})


def _finish(agg: dict, unread: dict) -> dict:
    codes = sorted(agg.pop("_codes").values(),
                   key=lambda r: (CHAIN.index(r["stage"]), -r["events"],
                                  str(r["code"])))
    # reached[s]: events that got PAST every stage before s (ENTERED reach
    # all); a stage only a downstream reader measures is null when unread
    reached, left = {}, agg["provider_events"] - agg["unavailable"]
    for s in CHAIN:
        reached[s] = left
        left -= agg["first_loss"][s]
    for s in DOWNSTREAM:
        if s in unread:
            reached[s] = None
            agg["first_loss"][s] = None
    if unread.get("ENTER_PASS"):
        agg["entered"] = None
    def _summary(st):
        return None if st is None else {
            "stage": st, "events": agg["first_loss"][st],
            "top_code": next((r for r in codes if r["stage"] == st), None)}
    lost = [s for s in CHAIN if agg["first_loss"][s]]
    agg.update(reached=reached, by_code=codes[:MAX_CODES_PER_GROUP],
               codes_truncated=len(codes) > MAX_CODES_PER_GROUP,
               # the earliest chain stage that lost an event, and the stage
               # that lost the most (ties: the earlier)
               earliest_loss=_summary(lost[0] if lost else None),
               largest_loss=_summary(max(
                   lost, key=lambda s: (agg["first_loss"][s],
                                        -CHAIN.index(s))) if lost else None),
               count_sources={
                   "provider_events": "ext_candidate_outcomes",
                   "provider_event_sources": "ext_candidate_outcomes "
                                             "(provider_event_id prefix)",
                   "first_loss": STAGE_SOURCE, "reached": STAGE_SOURCE,
                   "entered": "paper_decisions",
                   "unavailable": "the unread source named in `unavailable`",
                   "by_class": "refusal_taxonomy_table + bettor_external_"
                               "shadow.EVALUABILITY_OF over the codes above"})
    return agg


def census(events: list, valuations: list, decisions: list, *,
           reads: dict | None = None) -> dict:
    """THE CENSUS over recorded rows. `events`: one furthest ledger row per
    (sport_key, provider_event_id) (LEDGER_EVENTS_SQL); `valuations`:
    sealed valuations (VALUATIONS_SQL); `decisions`: paper decisions on them
    (DECISIONS_SQL); `reads`: {"valuations": bool, "decisions": bool} --
    False for a source that was not read. Pure."""
    from .agents.coverage_integrity import league_name
    reads = dict(reads or {})
    v_read = reads.get("valuations", True)
    d_read = reads.get("decisions", True)
    unread = {}
    if not v_read:
        for s in DOWNSTREAM:
            unread[s] = "EXTERNAL_VALUATIONS_UNREAD"
    if not d_read:
        for s in DOWNSTREAM:
            unread.setdefault(s, "PAPER_DECISIONS_UNREAD")
    by_key: dict = {}
    by_slug: dict = {}
    for v in valuations or ():
        if v.get("event_key") is not None:
            by_key.setdefault(str(v["event_key"]), []).append(v)
        if v.get("us_market_slug"):
            by_slug.setdefault(str(v["us_market_slug"]), []).append(v)
    dec_by_val: dict = {}
    for d in decisions or ():
        dec_by_val.setdefault(d.get("valuation_id"), []).append(d)
    comps: dict = {}
    sports: dict = {}
    total = _blank("ALL", None)
    linked = {"by_event_key": 0, "by_venue_contract": 0}
    for ev in events or ():
        seen: dict = {}
        for v in by_key.get(str(ev.get("provider_event_id")), ()):
            seen[v["id"]] = v
        n_key = len(seen)
        for s in list(ev.get("slugs") or []) + [ev.get("us_market_slug")]:
            for v in by_slug.get(str(s), ()) if s else ():
                seen.setdefault(v["id"], v)
        vals = list(seen.values())
        if vals:
            linked["by_event_key" if n_key else "by_venue_contract"] += 1
        decs = [d for v in vals for d in dec_by_val.get(v["id"], ())]
        fl = first_loss_of_event(ev, vals, decs, valuations_read=v_read,
                                 decisions_read=d_read)
        key = str(ev.get("sport_key") or "UNKNOWN")
        fam = ev.get("family") or None
        _add(comps.setdefault(key, _blank(league_name(key), fam)), ev, fl)
        _add(sports.setdefault(str(fam or "unknown"),
                               _blank(str(fam or "unknown"), fam)), ev, fl)
        _add(total, ev, fl)
    return {"version": VERSION, "chain": list(CHAIN),
            "classes": list(CLASSES),
            "unavailable": unread,
            "valuations_linked": linked,
            "totals": _finish(total, unread),
            "by_competition": {k: _finish(v, unread) for k, v in
                               sorted(comps.items(), key=lambda kv:
                                      -kv[1]["provider_events"])},
            "by_sport": {k: _finish(v, unread) for k, v in
                         sorted(sports.items(), key=lambda kv:
                                -kv[1]["provider_events"])},
            "stage_sources": STAGE_SOURCE}


# ═════════════════════════════════════════════════════════════════════
# THE READS (bounded SELECTs; the caller holds a READ ONLY transaction)
# ═════════════════════════════════════════════════════════════════════

def ledger_events_sql() -> str:
    """One row per (sport_key, provider_event_id) in the window: its
    furthest row by coverage_integrity's own ranking (REACH_SQL, the ledger
    stage derived for rows written without one), every venue contract its
    rows recorded, and its row count. $1 since, $2 until, $3 the cap."""
    from .agents import coverage_integrity as C
    order = "reach DESC, cycle_at DESC, id DESC"
    return """
    WITH r AS (
        SELECT id, sport_key, family, provider_event_id, cycle_at, outcome,
               first_refusal, codes, us_market_slug, home, away,
               commence_time, %(st)s AS stage_d, %(reach)s AS reach
          FROM ext_candidate_outcomes
         WHERE cycle_at >= to_timestamp($1) AND cycle_at < to_timestamp($2)
           AND provider_event_id IS NOT NULL)
    SELECT sport_key, provider_event_id, max(family) AS family,
           count(*) AS rows, max(reach) AS reach,
           (array_agg(stage_d ORDER BY %(o)s))[1] AS stage,
           (array_agg(outcome ORDER BY %(o)s))[1] AS outcome,
           (array_agg(first_refusal ORDER BY %(o)s))[1] AS first_refusal,
           (array_agg(codes ORDER BY %(o)s))[1] AS codes,
           (array_agg(us_market_slug ORDER BY %(o)s))[1] AS us_market_slug,
           array_remove(array_agg(DISTINCT us_market_slug), NULL) AS slugs,
           max(home) AS home, max(away) AS away,
           max(commence_time) AS commence_time,
           extract(epoch FROM max(cycle_at))::float8 AS last_cycle_at
      FROM r GROUP BY sport_key, provider_event_id
     ORDER BY sport_key, provider_event_id
     LIMIT $3""" % {"st": C.LEDGER_STAGE_EXPR, "reach": C.REACH_SQL,
                    "o": order}


VALUATIONS_SQL = """
    SELECT id, event_key, us_market_slug, sport_family, record_purpose,
           refusals, admissible
      FROM external_valuations
     WHERE decided_at >= to_timestamp($1) AND decided_at < to_timestamp($2)
       AND record_purpose IN ('ENTRY_DECISION', 'CALIBRATION_ONLY')
       AND (event_key = ANY($3::text[]) OR us_market_slug = ANY($4::text[]))
     ORDER BY id
     LIMIT $5"""

DECISIONS_SQL = """
    SELECT valuation_id, verdict, refusal, refusals, strategy
      FROM paper_decisions
     WHERE valuation_id = ANY($1::bigint[])
       AND decided_at >= to_timestamp($2) - interval '1 day'
     ORDER BY decided_at
     LIMIT $3"""


async def _regclass(conn, name) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        name))
    except Exception:                                           # noqa: BLE001
        return False


async def _rows(conn, table, sql, *args):
    """(rows, None) or (None, reason). A failed statement inside the
    caller's transaction is isolated in a savepoint."""
    if not await _regclass(conn, table):
        return None, "SOURCE_TABLE_ABSENT:%s" % table
    try:
        async with conn.transaction():
            return [dict(r) for r in await conn.fetch(sql, *args)], None
    except Exception as exc:                                    # noqa: BLE001
        return None, "SOURCE_READ_FAILED:%s:%s" % (table, type(exc).__name__)


async def read(conn, *, since: float, until: float) -> dict:
    """THE CENSUS OVER THE WINDOW. Never raises on a source read: an unread
    ledger is UNAVAILABLE as a whole; an unread downstream source makes the
    stages it measures null with the reason."""
    out: dict[str, Any] = {"version": VERSION, "since": float(since),
                           "until": float(until), "reads": {}}
    events, why = await _rows(conn, "ext_candidate_outcomes",
                              ledger_events_sql(), float(since),
                              float(until), MAX_EVENTS)
    if events is None:
        return dict(out, status=UNAVAILABLE, why=why,
                    reads={"ext_candidate_outcomes": {"read": False,
                                                      "why": why}})
    out["reads"]["ext_candidate_outcomes"] = {
        "read": True, "events": len(events),
        "rows": sum(int(e.get("rows") or 0) for e in events),
        "truncated": len(events) >= MAX_EVENTS}
    ids = sorted({str(e["provider_event_id"]) for e in events})
    slugs = sorted({str(s) for e in events
                    for s in (e.get("slugs") or []) if s})
    vals, vwhy = await _rows(conn, "external_valuations", VALUATIONS_SQL,
                             float(since), float(until), ids, slugs,
                             MAX_VALUATIONS)
    out["reads"]["external_valuations"] = (
        {"read": False, "why": vwhy} if vals is None else
        {"read": True, "rows": len(vals),
         "truncated": len(vals) >= MAX_VALUATIONS})
    decs, dwhy = (None, "NOT_READ_VALUATIONS_UNREAD") if vals is None else \
        await _rows(conn, "paper_decisions", DECISIONS_SQL,
                    [int(v["id"]) for v in vals], float(since),
                    MAX_VALUATIONS)
    out["reads"]["paper_decisions"] = (
        {"read": False, "why": dwhy} if decs is None else
        {"read": True, "rows": len(decs),
         "truncated": len(decs) >= MAX_VALUATIONS})
    got = census(events, vals or [], decs or [],
                 reads={"valuations": vals is not None,
                        "decisions": decs is not None})
    return dict(out, status="OK", why=None, **got)

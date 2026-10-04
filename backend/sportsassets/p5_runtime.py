"""P5_LIVE_STREAM_BOOK_V1 EVALUATED AT RUNTIME, PREDICATE BY PREDICATE (API).

WHAT THIS ANSWERS. Is the live book rule P5_LIVE_STREAM_BOOK_V1 admissible
RIGHT NOW in the process that decides (the API)? It evaluates every
requirement of the rule (C1..C13 in `live_book_currency.DOCUMENT`) plus the
owner approval of the artifact, against:

  * THIS PROCESS'S OWN STATE -- whether PMX_* names are present here,
    whether INSTITUTIONAL_MD_STREAM is on here, the market-data identity
    guard's verdict here, the state of THIS process's resident stream, the
    identity mapper and price source the decision path here actually uses,
    and the decision path's own P5 evaluation
    (`live_book_evidence.evaluate_for`) for each symbol the stream evidence
    names. The rule applies "in the deciding process at the decision
    instant"; a book resident in ANOTHER process proves nothing here.
  * THE RECORDED RUNTIME EVIDENCE the workers process writes when its stream
    is on (migration 210): `institutional_stream_evidence` (connection
    epochs, complete books, gaps, receipt age and skew, state, depth, the
    stream's own current() answer -- re-evaluated here under the rule) and
    `institutional_same_book_probe` (stream book vs retail public book).

Each predicate is PROVEN or NOT_PROVEN, never inferred. A NOT_PROVEN one is
classified:
  EXTERNAL   needs an action outside the code: an env name on a service, an
             owner approval, a venue fact (the action is named exactly);
  INTERNAL   needs code that does not exist at this SHA (named exactly);
  DEPENDENT  cannot be evaluated until the named predicate is proven;
  RUNTIME    evaluated and failing now (stale, closed, gap ...).

THE VERDICT is LIVE_ADMISSIBLE only when every predicate is PROVEN;
otherwise BLOCKED with `first_blocking` (any kind, in rule order),
`first_blocking_external` (the first EXTERNAL one, exact) and every
INTERNAL blocker listed. Nothing here weakens the rule: a predicate is
PROVEN only on the rule's own evaluation in this process. Two places are
STRICTER, and say so: C5 (sequence integrity "rests on C3, C4 and C7") is
PROVEN only when those three are; and the same-book premise the rule hands
to the identity mapping (NE7 -> LB6) is its own gate S1, PROVEN only on
recorded agreement.

ADDED BESIDE THE PREDICATES (migration 213; no predicate reads them):
`focus_universe` (the stream's prioritized universe: count, per-tier counts,
members with tier, identity or UNAVAILABLE reason), `same_book_samples` (the
S1 sample count, agreement %, contracts comparable now, the incomparable-
reason histogram) and `c12_proofs` (decision-time C12 proof records).
Unmeasured is null with a reason, never 0.

READ-ONLY. It reads the evidence tables and `live_rule_artifacts`, the
process environment by NAME ONLY (never a value), and in-memory state. It
writes nothing, sends nothing to any venue, and never returns a secret.
"""
from __future__ import annotations

import inspect
import json
import os
import socket
import time

from . import decision_hooks as DH
from . import institutional_focus_universe as FU
from . import institutional_stream as IS
from . import institutional_stream_evidence as SE
from . import live_book_currency as LBC
from . import live_book_evidence as LBE
from . import live_rule_artifacts as LRA
from . import market_data_identity as MDI
from . import pmx_institutional as PMX

VERSION = "P5_RUNTIME_EVALUATION_V1"

PROVEN = "PROVEN"
NOT_PROVEN = "NOT_PROVEN"
LIVE_ADMISSIBLE = "LIVE_ADMISSIBLE"
BLOCKED = "BLOCKED"
K_EXTERNAL = "EXTERNAL"
K_INTERNAL = "INTERNAL"
K_DEPENDENT = "DEPENDENT"
K_RUNTIME = "RUNTIME"

RULE_PREDICATES = tuple(r["id"] for r in LBC.DOCUMENT["requirements"])
A1 = "A1_ARTIFACT_OWNER_APPROVED"
S1 = "S1_SAME_BOOK_PREMISE"
ORDER = RULE_PREDICATES + (A1, S1)

#: The newest workers evidence row must be this recent to count as LIVE.
EVIDENCE_LIVE_S = 180.0
#: Stream evidence window read for aggregates.
STREAM_WINDOW_S = 900.0
#: Same-book window and the evidence it needs to be SUPPORTED.
SAME_BOOK_WINDOW_S = 86400.0
SAME_BOOK_MIN_COMPARABLE = 30
SAME_BOOK_MIN_AGREE_RATE = 0.95

API_SERVICE = "sportsassets-api"
WORKERS_SERVICE = "sportsassets-workers"

# ── reasons ──────────────────────────────────────────────────────────
X_PMX_ABSENT = "PMX_CREDENTIALS_ABSENT_FROM_DECIDING_PROCESS"
X_FLAG_OFF = "INSTITUTIONAL_MD_STREAM_NOT_ON_IN_DECIDING_PROCESS"
X_GUARD = "MARKET_DATA_IDENTITY_GUARD_REFUSED_IN_DECIDING_PROCESS"
X_VENUE_REFUSED = "VENUE_REFUSED_THE_STREAM_CREDENTIAL"
X_NOT_APPROVED = "OWNER_APPROVAL_NOT_RECORDED_FOR_THIS_RULE_HASH"
X_NO_SAMPLES = "SAME_BOOK_PROBE_HAS_NO_COMPARABLE_SAMPLES"
X_CONTRADICTED = "SINGLE_BOOK_PREMISE_CONTRADICTED_BY_RECORDED_SAMPLES"
I_NOT_STARTED = "NO_CODE_PATH_STARTS_THE_STREAM_IN_THE_DECIDING_PROCESS"
I_NO_MAPPER = "IDENTITY_MAPPER_NOT_INSTALLED_IN_DECIDING_PROCESS"
I_NO_HOOK = "LIVE_BOOK_EVIDENCE_HOOK_NOT_INSTALLED_IN_DECIDING_PROCESS"
I_REST_PRICED = "DECISION_PRICED_FROM_REST_PAPER_BOOK_NOT_THE_STREAM_BOOK"
R_NO_STREAM_BOOK = "NO_CURRENT_STREAM_BOOK_TO_PRICE_FROM"
I_C13_ABSENT = "ACTUAL_LANE_DOES_NOT_ENFORCE_VERDICT_AGE_AT_SUBMIT"
R_INCONCLUSIVE = "SAME_BOOK_EVIDENCE_INCONCLUSIVE"


# ═════════════════════════════════════════════════════════════════════
# THIS PROCESS
# ═════════════════════════════════════════════════════════════════════

STREAM_WHEN_CURRENT = "INSTITUTIONAL_STREAM_WHEN_CURRENT"


def _decision_price_source() -> str:
    """The ACTUAL lane's price source in this process's decision path:
    the stream observation when current (decision_hooks.LIVE_BOOK_STREAM is
    installed by execution_intent.start), else the REST paper book."""
    return (STREAM_WHEN_CURRENT if DH.LIVE_BOOK_STREAM is not None
            else "REST_PAPER_BOOK")


def _c13_enforced() -> bool:
    try:
        from . import execution_intent as EI
        src = inspect.getsource(EI.ActualLane._run)
    except Exception:                                         # noqa: BLE001
        return False
    return "LBC.verdict_age_refusal(" in src


def process_state(env=None) -> dict:
    """THIS process, by names and states only (never a value)."""
    env = os.environ if env is None else env
    pres = PMX.presence(env)
    try:
        guard = MDI.guard(MDI.PMX, env=env)
    except Exception as exc:                                  # noqa: BLE001
        guard = "GUARD_FAILED:%s" % type(exc).__name__
    dig = IS.digest()
    return {
        "role": "API (deciding process)",
        "service_expected": API_SERVICE,
        "host": os.environ.get("RENDER_INSTANCE_ID") or socket.gethostname(),
        "pid": os.getpid(),
        "pmx_credential_names": {k: v for k, v in pres.items()
                                 if k.endswith("_PRESENT")},
        "pmx_missing": list(pres.get("missing") or []),
        "institutional_md_stream_flag_on": IS.enabled(env),
        "market_data_identity_guard": guard,
        "stream_state": dig.get("state"),
        "stream_state_why": dig.get("why"),
        "stream_start": dig.get("start"),
        "stream_connection_seq": dig.get("connection_seq"),
        "stream_symbols": dig.get("symbols"),
        "identity_mapper_installed": LBE.IDENTITY_MAPPER is not None,
        "live_book_evidence_hook_installed":
            DH.LIVE_BOOK_EVIDENCE is not None,
        "decision_price_source": _decision_price_source(),
        "c13_enforced_by_actual_lane": _c13_enforced(),
    }


# ═════════════════════════════════════════════════════════════════════
# THE RECORDED EVIDENCE (migration 210)
# ═════════════════════════════════════════════════════════════════════

def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


async def _has(conn, table) -> bool:
    return await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table)


LATEST_PROCESS_SQL = """
    SELECT DISTINCT ON (process_id) *
      FROM institutional_stream_evidence
     WHERE symbol = '*' AND recorded_at > now() - make_interval(secs => $1)
     ORDER BY process_id, minute DESC, recorded_at DESC
"""
LATEST_SYMBOL_SQL = """
    SELECT DISTINCT ON (symbol) *
      FROM institutional_stream_evidence
     WHERE symbol <> '*' AND recorded_at > now() - make_interval(secs => $1)
     ORDER BY symbol, minute DESC, recorded_at DESC
"""
STREAM_AGG_SQL = """
    SELECT symbol,
           count(*)                                   AS minutes,
           count(*) FILTER (WHERE current_ok)         AS minutes_current,
           sum(connects_in_minute)                    AS connects,
           sum(disconnects_in_minute)                 AS disconnects,
           sum(jsonb_array_length(gap_events))        AS gap_events,
           sum(updates_in_minute)                     AS updates,
           sum(messages_in_minute)                    AS messages,
           max(skew_max_s)                            AS skew_max_s,
           min(skew_min_s)                            AS skew_min_s,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY skew_p50_s)
                                                      AS skew_p50_s,
           max(max_interarrival_s)                    AS max_interarrival_s,
           max(receipt_age_s)                         AS receipt_age_max_s
      FROM institutional_stream_evidence
     WHERE recorded_at > now() - make_interval(secs => $1)
     GROUP BY symbol ORDER BY symbol
"""
#: S1 COUNTS ONLY COMPARABLE SAMPLES OF AN EXACT IDENTITY. A sample is
#: comparable only when the institutional_contract_map mapping was exact
#: (identity ok, institutional symbol = retail slug = the probed symbol); a
#: row carrying a comparable verdict without that is counted NOT_COMPARABLE
#: (the probe never writes one -- this makes the count not depend on it).
EXACT_SAMPLE_SQL = (
    "coalesce(identity_ok IS TRUE AND identity->>'institutional_symbol' = "
    "symbol AND retail_slug = symbol, false)")
NC_NOT_EXACT = "COMPARABLE_VERDICT_WITHOUT_EXACT_IDENTITY"
PROBE_AGG_SQL = """
    SELECT symbol,
           CASE WHEN verdict <> 'NOT_COMPARABLE' AND NOT %s
                THEN 'NOT_COMPARABLE' ELSE verdict END       AS verdict,
           count(*)                                          AS n,
           count(*) FILTER (WHERE NOT stream_changed_in_window) AS n_stable
      FROM institutional_same_book_probe
     WHERE probed_at > now() - make_interval(secs => $1)
     GROUP BY 1, 2 ORDER BY 1, 2
""" % EXACT_SAMPLE_SQL
PROBE_LATEST_SQL = """
    SELECT DISTINCT ON (symbol) symbol, probed_at, verdict, verdict_reason,
           identity_ok, identity_refusal, window_s, matched_stream_read,
           stream_book, retail_book, diff
      FROM institutional_same_book_probe
     WHERE probed_at > now() - make_interval(secs => $1)
     ORDER BY symbol, probed_at DESC
"""


async def stream_evidence(conn, *, now: float,
                          window_s: float = STREAM_WINDOW_S) -> dict:
    if not await _has(conn, "institutional_stream_evidence"):
        return {"status": "ABSENT", "why": "migration 210 not applied",
                "processes": [], "symbols": {}, "aggregates": {}}
    procs = [dict(r) for r in await conn.fetch(LATEST_PROCESS_SQL,
                                               float(window_s))]
    syms = [dict(r) for r in await conn.fetch(LATEST_SYMBOL_SQL,
                                              float(window_s))]
    aggs = {r["symbol"]: {k: (float(v) if hasattr(v, "as_integer_ratio")
                              and not isinstance(v, int) else v)
                          for k, v in dict(r).items() if k != "symbol"}
            for r in await conn.fetch(STREAM_AGG_SQL, float(window_s))}
    newest = max((p["recorded_at"].timestamp() for p in procs), default=None)
    status = ("ABSENT" if newest is None else
              "LIVE" if now - newest <= EVIDENCE_LIVE_S else "STALE")
    out_syms = {}
    for r in syms:
        read = SE.stream_read_from_row(r)
        ident = _j(r.get("identity"))
        identity = ({"status": "EXACT", "symbol": r["symbol"]}
                    if isinstance(ident, dict) and ident.get("ok")
                    and ident.get("institutional_symbol") == r["symbol"]
                    else ident if isinstance(ident, dict) else None)
        at = r.get("evaluated_at_epoch") or r["recorded_at"].timestamp()
        v = LBC.evaluate(stream_read=read, identity=identity, now=float(at),
                         us_market_slug=r["symbol"], priced_from=None)
        out_syms[r["symbol"]] = {
            "minute": _iso(r["minute"]), "recorded_at": _iso(r["recorded_at"]),
            "process_id": r["process_id"],
            "connection_id": r["connection_id"],
            "connection_epoch": r["connection_epoch"],
            "connected": r["connected"],
            "first_complete_book_after_connect_s":
                r["first_complete_book_after_connect_s"],
            "gap_open": r["gap_open"], "receipt_age_s": r["receipt_age_s"],
            "venue_receipt_skew_s": r["venue_receipt_skew_s"],
            "instrument_state": r["instrument_state"],
            "current_ok": r["current_ok"],
            "current_refusal": r["current_refusal"],
            "top_n": _j(r["top_n"]),
            "worker_p5": {
                "evaluated_at": at,
                "stream_book_verdict": v["stream_book_verdict"],
                "components": {c["component"]: {
                    "passed": c["passed"], "fails_as": c["fails_as"],
                    "reason": c["reason"]} for c in v["components"]
                    if c["component"] != "C12_PRICED_FROM_THIS_BOOK"},
                "note": ("re-evaluated under the rule on the workers' "
                         "recorded current() answer at its record instant; "
                         "C12 is not applicable (the workers price nothing)")},
        }
    return {"status": status, "newest_recorded_at_epoch": newest,
            "live_bound_s": EVIDENCE_LIVE_S, "window_s": window_s,
            "processes": [{
                "process_id": p["process_id"], "service": p["service"],
                "minute": _iso(p["minute"]),
                "recorded_at": _iso(p["recorded_at"]),
                "stream_state": p["stream_state"],
                "stream_state_why": p["stream_state_why"],
                "connection_id": p["connection_id"],
                "connection_epoch": p["connection_epoch"],
                "connects_total": p["connects_total"],
                "reconnects_total": p["reconnects_total"],
                "last_connect_at": _iso(p["last_connect_at"]),
                "last_disconnect_at": _iso(p["last_disconnect_at"]),
                "last_disconnect_why": p["last_disconnect_why"],
                "messages_total": p["messages_total"],
                "extra": _j(p["extra"])} for p in procs],
            "symbols": out_syms, "aggregates": aggs}


def same_book_status(counts: dict) -> tuple:
    """Pure: (status, detail) from {verdict: (n, n_stable)} totals."""
    agree = counts.get("AGREE_TOP_N", (0, 0))[0]
    touch = counts.get("AGREE_TOUCH_ONLY", (0, 0))[0]
    dis, dis_stable = counts.get("DISAGREE", (0, 0))
    nc = counts.get("NOT_COMPARABLE", (0, 0))[0]
    comparable = agree + touch + dis
    rate = None if not comparable else (agree + touch) / comparable
    detail = {"comparable": comparable, "agree_top_n": agree,
              "agree_touch_only": touch, "disagree": dis,
              "disagree_with_stream_stable_in_window": dis_stable,
              "not_comparable": nc, "agree_rate": None if rate is None
              else round(rate, 4),
              "supported_needs": {"min_comparable": SAME_BOOK_MIN_COMPARABLE,
                                  "min_agree_rate": SAME_BOOK_MIN_AGREE_RATE,
                                  "stable_disagreements": 0}}
    if dis_stable > 0:
        return "CONTRADICTED", detail
    if comparable == 0:
        return "UNTESTED", detail
    if comparable >= SAME_BOOK_MIN_COMPARABLE and \
            rate >= SAME_BOOK_MIN_AGREE_RATE:
        return "SUPPORTED", detail
    return "INCONCLUSIVE", detail


async def same_book_evidence(conn, *,
                             window_s: float = SAME_BOOK_WINDOW_S) -> dict:
    if not await _has(conn, "institutional_same_book_probe"):
        return {"status": "ABSENT", "why": "migration 210 not applied",
                "by_symbol": {}, "totals": {}}
    by: dict = {}
    tot: dict = {}
    for r in await conn.fetch(PROBE_AGG_SQL, float(window_s)):
        by.setdefault(r["symbol"], {})[r["verdict"]] = (r["n"], r["n_stable"])
        n, s = tot.get(r["verdict"], (0, 0))
        tot[r["verdict"]] = (n + r["n"], s + r["n_stable"])
    latest = {r["symbol"]: {k: (_iso(v) if k == "probed_at" else _j(v))
                            for k, v in dict(r).items() if k != "symbol"}
              for r in await conn.fetch(PROBE_LATEST_SQL, float(window_s))}
    status, detail = same_book_status(tot)
    return {"status": status, "window_s": window_s, "totals": detail,
            "by_symbol": {s: dict(zip(("status", "detail"),
                                      same_book_status(c), strict=True),
                                  latest=latest.get(s))
                          for s, c in by.items()}}


# ═════════════════════════════════════════════════════════════════════
# MIGRATION 213: THE FOCUS UNIVERSE, THE SAMPLE SUMMARY, THE C12 PROOFS
# ═════════════════════════════════════════════════════════════════════
#
# Added beside the predicates; they change none of them. UNMEASURED IS NULL
# WITH A REASON, NEVER 0: a table that does not exist, or a snapshot that was
# never recorded, is `null` + `why`; a percentage with no denominator is
# `null` + `why`. A count of rows in a table that exists is a measurement.

#: The focus universe snapshot must be this recent to be reported as current.
FOCUS_LIVE_S = 900.0
#: A contract is "currently comparable" with a comparable sample this recent.
CURRENT_COMPARABLE_S = 900.0
C12_PROOF_LIMIT = 20

FOCUS_LATEST_SQL = """
    SELECT DISTINCT ON (service) service, process_id, universe_id,
           computed_at, bound
      FROM institutional_focus_universe
     WHERE computed_at > now() - make_interval(secs => $1)
     ORDER BY service, computed_at DESC
"""
FOCUS_MEMBERS_SQL = """
    SELECT rank, tier, tier_rank, why, reasons, retail_slug, outcome_side,
           bettor_event, strategy, diagnostic_only, identity_status,
           unavailable_reason, institutional_symbol, market_type, period,
           retail_event_slug, institutional_event_id, settlement,
           stream_wanted, grants_live_eligibility
      FROM institutional_focus_universe
     WHERE universe_id = $1 ORDER BY rank
"""


def _per_tier(rows) -> dict:
    out = {t: 0 for t in FU.TIERS}
    for r in rows:
        out[r["tier"]] = out.get(r["tier"], 0) + 1
    return out


async def focus_universe_evidence(conn, *,
                                  window_s: float = FOCUS_LIVE_S) -> dict:
    base = {"version": FU.VERSION, "bound": FU.MAX_MEMBERS,
            "tiers": list(FU.TIERS)}
    if not await _has(conn, "institutional_focus_universe"):
        return dict(base, status="UNMEASURED", count=None, per_tier=None,
                    members=None, why="migration 213 not applied")
    latest = [dict(r) for r in await conn.fetch(FOCUS_LATEST_SQL,
                                                float(window_s))]
    if not latest:
        return dict(base, status="UNMEASURED", count=None, per_tier=None,
                    members=None,
                    why=("NO_FOCUS_UNIVERSE_SNAPSHOT_RECORDED_IN_THE_LAST_"
                         "%d_S (written only by a process whose stream is "
                         "enabled)" % int(window_s)))
    by_service = {}
    newest = max(latest, key=lambda r: r["computed_at"])
    members = []
    for snap in latest:
        rows = [dict(r) for r in await conn.fetch(FOCUS_MEMBERS_SQL,
                                                  snap["universe_id"])]
        by_service[snap["service"]] = {
            "process_id": snap["process_id"],
            "universe_id": snap["universe_id"],
            "computed_at": _iso(snap["computed_at"]),
            "count": len(rows), "per_tier": _per_tier(rows),
            "exact": sum(1 for r in rows if r["identity_status"] == FU.EXACT)}
        if snap is newest:
            members = rows
    reasons: dict = {}
    for r in members:
        if r["unavailable_reason"]:
            reasons[r["unavailable_reason"]] = reasons.get(
                r["unavailable_reason"], 0) + 1
    return dict(
        base, status="MEASURED", why=None, service=newest["service"],
        process_id=newest["process_id"], universe_id=newest["universe_id"],
        computed_at=_iso(newest["computed_at"]), count=len(members),
        per_tier=_per_tier(members),
        exact=sum(1 for r in members if r["identity_status"] == FU.EXACT),
        unavailable=sum(1 for r in members
                        if r["identity_status"] == FU.UNAVAILABLE),
        unavailable_reasons=reasons,
        members=[{k: (_j(v) if k in ("reasons", "settlement") else v)
                  for k, v in r.items()} for r in members],
        by_service=by_service)


async def _probe_has_213(conn) -> bool:
    return bool(await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE "
        "table_name = 'institutional_same_book_probe' AND "
        "column_name = 'focus_tier')"))


SAMPLES_SQL = """
    WITH p AS (
        SELECT verdict,
               verdict <> 'NOT_COMPARABLE' AND {exact} AS comparable,
               verdict <> 'NOT_COMPARABLE' AND NOT {exact} AS not_exact
          FROM institutional_same_book_probe
         WHERE probed_at > now() - make_interval(secs => $1))
    SELECT count(*)                                       AS samples,
           count(*) FILTER (WHERE comparable)             AS comparable,
           count(*) FILTER (WHERE comparable AND verdict IN
                            ('AGREE_TOP_N', 'AGREE_TOUCH_ONLY')) AS agree,
           count(*) FILTER (WHERE not_exact)              AS not_exact
      FROM p
"""
NC_HIST_SQL = """
    SELECT CASE WHEN verdict = 'NOT_COMPARABLE'
                THEN coalesce(verdict_reason, 'UNSTATED')
                ELSE '{not_exact}' END AS reason, count(*) AS n
      FROM institutional_same_book_probe
     WHERE probed_at > now() - make_interval(secs => $1)
       AND (verdict = 'NOT_COMPARABLE' OR NOT {exact})
     GROUP BY 1 ORDER BY 2 DESC, 1
"""
CURRENT_COMPARABLE_SQL = """
    SELECT DISTINCT ON (symbol) symbol, verdict, verdict_reason, probed_at,
           {tier} AS focus_tier
      FROM institutional_same_book_probe
     WHERE probed_at > now() - make_interval(secs => $1)
       AND verdict <> 'NOT_COMPARABLE' AND {exact}
     ORDER BY symbol, probed_at DESC
"""
BY_TIER_SQL = """
    SELECT coalesce(focus_tier, 'NOT_RECORDED') AS tier, count(*) AS samples,
           count(*) FILTER (WHERE verdict <> 'NOT_COMPARABLE' AND {exact})
               AS comparable
      FROM institutional_same_book_probe
     WHERE probed_at > now() - make_interval(secs => $1)
     GROUP BY 1 ORDER BY 1
"""


async def same_book_samples(conn, *, window_s: float = SAME_BOOK_WINDOW_S,
                            current_s: float = CURRENT_COMPARABLE_S) -> dict:
    """The S1 sample summary: how many samples, how many count (comparable
    AND exactly mapped), the agreement %, the contracts comparable now, and
    why the rest were not comparable. The rule's thresholds, shown."""
    base = {"window_s": window_s,
            "required": {"min_comparable": SAME_BOOK_MIN_COMPARABLE,
                         "min_agree_pct": SAME_BOOK_MIN_AGREE_RATE * 100.0},
            "counts_only": ("comparable samples of an EXACT identity "
                            "(institutional_contract_map; symbol = slug)")}
    if not await _has(conn, "institutional_same_book_probe"):
        return dict(base, status="UNMEASURED", sample_count=None,
                    comparable_count=None, agreement_pct=None,
                    agreement_pct_why="migration 210 not applied",
                    current_comparable_contracts=None,
                    incomparable_reasons=None, by_tier=None,
                    why="migration 210 not applied")
    has213 = await _probe_has_213(conn)
    fmt = {"exact": EXACT_SAMPLE_SQL, "not_exact": NC_NOT_EXACT,
           "tier": "focus_tier" if has213 else "NULL::text"}
    t = dict(await conn.fetchrow(SAMPLES_SQL.format(**fmt), float(window_s)))
    hist = {r["reason"]: r["n"] for r in await conn.fetch(
        NC_HIST_SQL.format(**fmt), float(window_s))}
    cur = [{"symbol": r["symbol"], "verdict": r["verdict"],
            "verdict_reason": r["verdict_reason"],
            "probed_at": _iso(r["probed_at"]), "focus_tier": r["focus_tier"]}
           for r in await conn.fetch(CURRENT_COMPARABLE_SQL.format(**fmt),
                                     float(current_s))]
    by_tier = ({r["tier"]: {"samples": r["samples"],
                            "comparable": r["comparable"]}
                for r in await conn.fetch(BY_TIER_SQL.format(**fmt),
                                          float(window_s))}
               if has213 else None)
    comparable = t["comparable"]
    pct = None if not comparable else round(100.0 * t["agree"] / comparable,
                                            2)
    return dict(
        base, status="MEASURED", why=None, sample_count=t["samples"],
        comparable_count=comparable, agree_count=t["agree"],
        comparable_verdicts_without_exact_identity=t["not_exact"],
        agreement_pct=pct,
        agreement_pct_why=(None if comparable else
                           "NO_COMPARABLE_EXACTLY_MAPPED_SAMPLES_IN_WINDOW"),
        current_comparable_window_s=current_s,
        current_comparable_contracts=cur, incomparable_reasons=hist,
        by_tier=by_tier,
        by_tier_why=(None if has213 else "migration 213 not applied"))


C12_COUNTS_SQL = """
    SELECT proof_status, count(*) AS n FROM p5_c12_decision_proof
     GROUP BY 1 ORDER BY 1
"""
C12_LATEST_SQL = """
    SELECT recorded_at, decision_id, execution_intent_id, strategy,
           policy_version, us_market_slug, order_intent, proof_status,
           refusal, price_source, stream_symbol, connection_epoch,
           connection_id, obs_id, book_received_at, book_venue_ts,
           book_age_s, decision_executable_price, limit_price, p5_verdict,
           failed_components, c12_passed, live_eligible, actual_state,
           actual_refusal
      FROM p5_c12_decision_proof ORDER BY recorded_at DESC, id DESC
     LIMIT $1
"""


async def c12_proofs(conn, *, limit: int = C12_PROOF_LIMIT) -> dict:
    if not await _has(conn, "p5_c12_decision_proof"):
        return {"status": "UNMEASURED", "count": None, "by_status": None,
                "records": None, "why": "migration 213 not applied"}
    by = {r["proof_status"]: r["n"] for r in await conn.fetch(C12_COUNTS_SQL)}
    recs = []
    for r in await conn.fetch(C12_LATEST_SQL, int(limit)):
        d = dict(r)
        for k in ("recorded_at", "book_received_at", "book_venue_ts"):
            d[k] = _iso(d[k])
        for k in ("decision_executable_price", "limit_price"):
            d[k] = None if d[k] is None else str(d[k])
        d["failed_components"] = _j(d["failed_components"])
        recs.append(d)
    return {"status": "MEASURED", "why": None, "count": sum(by.values()),
            "by_status": by, "records": recs,
            "note": ("one record per execution intent whose decision the "
                     "resident stream book priced or refused "
                     "(execution_intent.on_decision -> p5_c12_proof)")}


async def artifact_state(conn) -> dict:
    desc = await LRA.describe(conn)
    row = None
    try:
        if await _has(conn, "live_rule_artifacts"):
            r = await conn.fetchrow(
                "SELECT status, sha256, created_by, created_at, "
                "       owner_approval_actor, owner_approved_at "
                "  FROM live_rule_artifacts WHERE rule_id = $1 "
                "   AND version = $2", LBC.RULE_ID, LBC.VERSION)
            row = dict(r) if r else None
    except Exception:                                         # noqa: BLE001
        row = None
    approved = LBC.RULE_ID in desc.get("approved_live_book_rules", [])
    # R30A section 24: the approval has two parts -- the rule document (204)
    # AND the gate's enforced configuration (live_approvals, by config sha).
    # `owner_approved` is the intersection (live_rule_artifacts.approved_live_
    # book_rules); the second part is reported on its own as well.
    from . import live_approvals as LAP
    gate_ok = LBC.RULE_ID in await LAP.approved_gates(conn)
    return {"rule_id": LBC.RULE_ID, "version": LBC.VERSION,
            "code_sha256": LBC.SHA256,
            "owner_approved": approved,
            "gate_config_sha256": LAP.config_sha256(LAP.GATE_BOOK),
            "gate_config_approved": gate_ok,
            "stored_status": (row or {}).get("status"),
            "stored_sha256_matches_code": (None if row is None else
                                           row.get("sha256") == LBC.SHA256),
            "owner_approved_at": _iso((row or {}).get("owner_approved_at")),
            "owner_approval_actor": (row or {}).get("owner_approval_actor"),
            "approved_live_book_rules": desc.get("approved_live_book_rules")}


# ═════════════════════════════════════════════════════════════════════
# THE PREDICATES
# ═════════════════════════════════════════════════════════════════════

def _p(pid, status, *, kind=None, reason=None, action=None, depends_on=None,
       deciding_process=None, runtime_evidence=None, checks=None) -> dict:
    return {"predicate": pid, "status": status,
            "blocker": None if status == PROVEN else kind,
            "reason": None if status == PROVEN else reason,
            "action": None if status == PROVEN else action,
            "depends_on": None if status == PROVEN else depends_on,
            "checks": checks, "deciding_process": deciding_process,
            "runtime_evidence": runtime_evidence}


def c2_checks(ps: dict) -> list:
    """C2's sub-checks in THIS process, in order. The first failing one is
    the reason."""
    st = ps.get("stream_state")
    missing = ps.get("pmx_missing") or []
    start = (ps.get("stream_start") or {}).get("state")
    return [
        {"check": "PMX_CREDENTIAL_NAMES_PRESENT", "ok": not missing,
         "kind": K_EXTERNAL, "reason": X_PMX_ABSENT,
         "missing": missing,
         "action": ("owner: add the PMX_* names (%s) to %s in the Render "
                    "dashboard (the values the workers service already "
                    "holds); never print them"
                    % (", ".join(missing or PMX.CREDENTIALS_EXPECTED),
                       API_SERVICE))},
        {"check": "INSTITUTIONAL_MD_STREAM_ON", "ok":
            bool(ps.get("institutional_md_stream_flag_on")),
         "kind": K_EXTERNAL, "reason": X_FLAG_OFF,
         "action": "set INSTITUTIONAL_MD_STREAM=on on %s" % API_SERVICE},
        {"check": "MARKET_DATA_IDENTITY_GUARD_PASSES",
         "ok": ps.get("market_data_identity_guard") is None,
         "kind": K_EXTERNAL,
         "reason": "%s:%s" % (X_GUARD, ps.get("market_data_identity_guard")),
         "action": ("the PMX credential on %s must be complete and distinct "
                    "from the retail execution and funded keys" % API_SERVICE)},
        {"check": "STREAM_STARTED_IN_THIS_PROCESS",
         "ok": start not in (None, IS.S_NOT_STARTED) or st not in (
             IS.S_NOT_STARTED,),
         "kind": K_INTERNAL, "reason": I_NOT_STARTED,
         "stream_start": start,
         "action": ("the API lifespan did not run institutional_api_stream."
                    "start() in this process (it arms institutional_stream."
                    "start_default and the refdata path)")},
        {"check": "VENUE_ACCEPTS_THE_STREAM_CREDENTIAL",
         "ok": st not in ("REFUSED_BY_VENUE",
                          "CREDENTIAL_REFUSED_BY_IDENTITY_GUARD"),
         "kind": K_EXTERNAL, "reason": X_VENUE_REFUSED,
         "action": "the venue refused the stream credential: check the "
                   "PMX client's read:marketdata grant"},
        {"check": "STREAM_RUNNING_IN_THIS_PROCESS",
         "ok": st in LBC._STREAM_RUNNING, "kind": K_RUNTIME,
         "reason": "STREAM_STATE_%s" % st, "stream_state": st,
         "action": None},
    ]


def evaluate_predicates(*, ps: dict, decision: dict, stream: dict,
                        same_book: dict, artifact: dict,
                        subject: str | None) -> list:
    """Pure: every predicate, in ORDER, from the gathered facts."""
    comps = {c["component"]: c for c in decision.get("components") or ()}
    worker = ((stream.get("symbols") or {}).get(subject) or {}) \
        if subject else {}
    wcomps = (worker.get("worker_p5") or {}).get("components") or {}
    out = []

    def rt(cid):
        w = wcomps.get(cid)
        return None if w is None else {"workers_stream": w,
                                       "evidence_status": stream.get("status")}

    # C1 -- exact identity in the deciding process
    c1 = comps.get("C1_IDENTITY_EXACT") or {}
    sb_sym = (same_book.get("by_symbol") or {}).get(subject) or {}
    c1_rt = {"workers_identity": (sb_sym.get("latest") or {}).get(
        "identity_ok"), "same_book_status": sb_sym.get("status")}
    if c1.get("passed"):
        out.append(_p("C1_IDENTITY_EXACT", PROVEN,
                      deciding_process=c1, runtime_evidence=c1_rt))
    elif not ps.get("identity_mapper_installed"):
        out.append(_p("C1_IDENTITY_EXACT", NOT_PROVEN, kind=K_INTERNAL,
                      reason=I_NO_MAPPER,
                      action=("code: install an institutional_contract_map-"
                              "backed mapper as live_book_evidence."
                              "IDENTITY_MAPPER in the deciding process"),
                      deciding_process=c1, runtime_evidence=c1_rt))
    elif ps.get("stream_state") not in LBC._STREAM_RUNNING:
        # the mapper answers over refdata held only while the stream runs
        out.append(_p("C1_IDENTITY_EXACT", NOT_PROVEN, kind=K_DEPENDENT,
                      reason=c1.get("reason"),
                      depends_on="C2_STREAM_RUNNING", deciding_process=c1,
                      runtime_evidence=c1_rt))
    else:
        out.append(_p("C1_IDENTITY_EXACT", NOT_PROVEN, kind=K_RUNTIME,
                      reason=c1.get("reason"), deciding_process=c1,
                      runtime_evidence=c1_rt))

    # C2 -- the stream runs in THIS process (rule logic on this process)
    checks = c2_checks(ps)
    if ps.get("stream_state") in LBC._STREAM_RUNNING:
        out.append(_p("C2_STREAM_RUNNING", PROVEN, checks=checks,
                      deciding_process={"stream_state": ps["stream_state"]}))
    else:
        first = next(c for c in checks if not c["ok"])
        out.append(_p("C2_STREAM_RUNNING", NOT_PROVEN, kind=first["kind"],
                      reason=first["reason"], action=first["action"],
                      checks=checks,
                      deciding_process={"stream_state": ps.get("stream_state"),
                                        "why": ps.get("stream_state_why")},
                      runtime_evidence={
                          "workers_stream_evidence": stream.get("status"),
                          "workers_processes": [
                              {k: p.get(k) for k in (
                                  "process_id", "stream_state",
                                  "connection_epoch", "recorded_at")}
                              for p in stream.get("processes") or ()]}))
    c2_ok = out[-1]["status"] == PROVEN
    c1_ok = out[0]["status"] == PROVEN

    def stream_pred(cid):
        c = comps.get(cid) or {}
        if not c2_ok:
            return _p(cid, NOT_PROVEN, kind=K_DEPENDENT,
                      reason="NO_STREAM_IN_DECIDING_PROCESS",
                      depends_on="C2_STREAM_RUNNING", deciding_process=c,
                      runtime_evidence=rt(cid))
        if c.get("passed"):
            return _p(cid, PROVEN, deciding_process=c,
                      runtime_evidence=rt(cid))
        if c.get("reason") == LBC.R_NO_STREAM_READ and not c1_ok:
            return _p(cid, NOT_PROVEN, kind=K_DEPENDENT,
                      reason=LBC.R_NO_STREAM_READ,
                      depends_on="C1_IDENTITY_EXACT", deciding_process=c,
                      runtime_evidence=rt(cid))
        return _p(cid, NOT_PROVEN, kind=K_RUNTIME, reason=c.get("reason"),
                  deciding_process=c, runtime_evidence=rt(cid))

    for cid in ("C3_CONNECTION_EPOCH_ALIVE", "C4_COMPLETE_BOOK_ON_THIS_EPOCH"):
        out.append(stream_pred(cid))
    # C5 -- integrity rests on C3, C4, C7 (stricter: proven only with them)
    c5_slot = len(out)
    for cid in ("C6_VENUE_TS_PRESENT", "C7_VENUE_TS_MONOTONIC_IN_EPOCH",
                "C8_RECEIPT_AGE", "C9_VENUE_RECEIPT_SKEW", "C10_MARKET_OPEN",
                "C11_STREAM_BOOK_CURRENT"):
        out.append(stream_pred(cid))
    st = {p["predicate"]: p["status"] for p in out}
    rel = ("C3_CONNECTION_EPOCH_ALIVE", "C4_COMPLETE_BOOK_ON_THIS_EPOCH",
           "C7_VENUE_TS_MONOTONIC_IN_EPOCH")
    missing = [r for r in rel if st.get(r) != PROVEN]
    out.insert(c5_slot, _p(
        "C5_SEQUENCE_INTEGRITY", PROVEN if not missing else NOT_PROVEN,
        kind=K_DEPENDENT, reason="RELIES_ON_C3_C4_C7",
        depends_on=",".join(missing) or None,
        deciding_process=comps.get("C5_SEQUENCE_INTEGRITY")))

    # C12 -- the decision priced from THIS stream observation
    c12 = comps.get("C12_PRICED_FROM_THIS_BOOK") or {}
    src = ps.get("decision_price_source")
    if c12.get("passed"):
        out.append(_p("C12_PRICED_FROM_THIS_BOOK", PROVEN,
                      deciding_process=c12))
    elif src != STREAM_WHEN_CURRENT:
        out.append(_p("C12_PRICED_FROM_THIS_BOOK", NOT_PROVEN,
                      kind=K_INTERNAL, reason=I_REST_PRICED,
                      action=("execution_intent.start has not installed "
                              "decision_hooks.LIVE_BOOK_STREAM in this "
                              "process, so the actual lane prices from %s"
                              % src),
                      deciding_process=dict(c12, price_source=src)))
    elif not (c1_ok and c2_ok):
        out.append(_p("C12_PRICED_FROM_THIS_BOOK", NOT_PROVEN,
                      kind=K_DEPENDENT, reason=R_NO_STREAM_BOOK,
                      depends_on=("C1_IDENTITY_EXACT" if not c1_ok
                                  else "C2_STREAM_RUNNING"),
                      deciding_process=dict(c12, price_source=src)))
    else:
        out.append(_p("C12_PRICED_FROM_THIS_BOOK", NOT_PROVEN,
                      kind=K_RUNTIME, reason=c12.get("reason"),
                      deciding_process=dict(c12, price_source=src)))

    # C13 -- the actual lane's verdict-age bound at submit
    if ps.get("c13_enforced_by_actual_lane"):
        out.append(_p("C13_VERDICT_AGE_AT_SUBMIT", PROVEN,
                      deciding_process={
                          "enforced_by": "execution_intent.ActualLane._run -> "
                                         "live_book_currency."
                                         "verdict_age_refusal",
                          "limit_s": LBC.MAX_VERDICT_AGE_AT_SUBMIT_S}))
    else:
        out.append(_p("C13_VERDICT_AGE_AT_SUBMIT", NOT_PROVEN,
                      kind=K_INTERNAL, reason=I_C13_ABSENT))

    # A1 -- the owner approved THIS rule hash
    if artifact.get("owner_approved"):
        out.append(_p(A1, PROVEN, deciding_process=artifact))
    else:
        out.append(_p(A1, NOT_PROVEN, kind=K_EXTERNAL, reason=X_NOT_APPROVED,
                      action=("owner: record an approval on live_rule_"
                              "artifacts for %s v%s sha256 %s (research/"
                              "p5_live_stream_book_v1.md, 'Owner approval "
                              "action') AND a LIVE_GATE approval in "
                              "live_approvals naming this build's gate "
                              "config sha256 %s" % (
                                  LBC.RULE_ID, LBC.VERSION, LBC.SHA256,
                                  artifact.get("gate_config_sha256"))),
                      deciding_process=artifact))

    # S1 -- the single-book premise, on recorded same-book samples
    sbs = same_book.get("status")
    sb_ev = {"status": sbs, "totals": same_book.get("totals"),
             "subject": sb_sym or None}
    if sbs == "SUPPORTED":
        out.append(_p(S1, PROVEN, runtime_evidence=sb_ev))
    elif sbs == "CONTRADICTED":
        out.append(_p(S1, NOT_PROVEN, kind=K_EXTERNAL, reason=X_CONTRADICTED,
                      action=("venue fact: the retail public book and the "
                              "exchange stream book disagree while the stream "
                              "book was stable; the institutional book is not "
                              "the retail executable price"),
                      runtime_evidence=sb_ev))
    elif sbs in ("UNTESTED", "ABSENT", None):
        out.append(_p(S1, NOT_PROVEN, kind=K_EXTERNAL, reason=X_NO_SAMPLES,
                      action=("set INSTITUTIONAL_MD_STREAM=on on %s so the "
                              "same-book probe records samples"
                              % WORKERS_SERVICE),
                      runtime_evidence=sb_ev))
    else:
        out.append(_p(S1, NOT_PROVEN, kind=K_RUNTIME, reason=R_INCONCLUSIVE,
                      runtime_evidence=sb_ev))
    return out


def verdict(predicates: list) -> dict:
    failed = [p for p in predicates if p["status"] != PROVEN]
    ext = next((p for p in failed if p["blocker"] == K_EXTERNAL), None)

    def brief(p):
        if p is None:
            return None
        return {"predicate": p["predicate"], "blocker": p["blocker"],
                "reason": p["reason"], "action": p["action"],
                "depends_on": p["depends_on"]}
    return {
        "verdict": LIVE_ADMISSIBLE if not failed else BLOCKED,
        "proven": [p["predicate"] for p in predicates
                   if p["status"] == PROVEN],
        "not_proven": [p["predicate"] for p in failed],
        "first_blocking": brief(failed[0] if failed else None),
        "first_blocking_external": brief(ext),
        "internal_blockers": [brief(p) for p in failed
                              if p["blocker"] == K_INTERNAL]
        + [dict(predicate="C2_STREAM_RUNNING", blocker=K_INTERNAL,
                reason=c["reason"], action=c["action"], depends_on=None)
           for p in failed if p["predicate"] == "C2_STREAM_RUNNING"
           and p["blocker"] != K_INTERNAL
           for c in (p.get("checks") or ())
           if not c["ok"] and c["kind"] == K_INTERNAL],
        "external_blockers": [brief(p) for p in failed
                              if p["blocker"] == K_EXTERNAL]
        + [dict(predicate="C2_STREAM_RUNNING", blocker=K_EXTERNAL,
                reason=c["reason"], action=c["action"], depends_on=None)
           for p in failed if p["predicate"] == "C2_STREAM_RUNNING"
           for c in (p.get("checks") or ())
           if not c["ok"] and c["kind"] == K_EXTERNAL
           and c["reason"] != p["reason"]],
    }


async def gather(conn, *, now: float | None = None, env=None,
                 symbol: str | None = None) -> dict:
    """Every fact the predicates are judged on. Read-only."""
    at = float(now if now is not None else time.time())
    ps = process_state(env)
    stream = await stream_evidence(conn, now=at)
    same_book = await same_book_evidence(conn)
    artifact = await artifact_state(conn)
    candidates = ([symbol] if symbol else
                  sorted(stream.get("symbols") or {}))
    # THE DECISION PATH'S OWN P5 EVALUATION IN THIS PROCESS: its installed
    # identity mapper, its stream reader, its price source.
    priced = DH.LIVE_BOOK_STREAM is not None
    decisions = {}
    for s in candidates:
        cand = {"us_market_slug": s, "side": "ORDER_INTENT_BUY_LONG"}
        decisions[s] = (LBE.evaluate_decision({}, cand, now=at) if priced
                        else LBE.evaluate_for({}, cand, obs=None, now=at))
    return {"at": at, "ps": ps, "stream": stream, "same_book": same_book,
            "artifact": artifact, "decisions": decisions,
            "focus_universe": await focus_universe_evidence(conn),
            "same_book_samples": await same_book_samples(conn),
            "c12_proofs": await c12_proofs(conn)}


def assemble(facts: dict) -> dict:
    """Pure: the endpoint's answer from `gather`'s facts."""
    at = facts["at"]
    decisions = facts.get("decisions") or {}
    if decisions:
        subject = min(decisions,
                      key=lambda s: len(decisions[s]["failed_components"]))
        decision = decisions[subject]
    else:
        subject = None
        decision = LBC.not_evaluated(reason=LBC.R_IDENTITY_UNMAPPED, now=at)
    ps, stream = facts["ps"], facts["stream"]
    same_book, artifact = facts["same_book"], facts["artifact"]
    preds = evaluate_predicates(ps=ps, decision=decision, stream=stream,
                                same_book=same_book, artifact=artifact,
                                subject=subject)
    v = verdict(preds)
    return dict(v, version=VERSION, rule=LBC.RULE_ID,
                rule_version=LBC.VERSION, rule_sha256=LBC.SHA256,
                evaluated_at=at, artifact=artifact,
                owner_approved=bool(artifact.get("owner_approved")),
                deciding_process=ps, subject_symbol=subject,
                decision_path_verdict={
                    "verdict": decision.get("verdict"),
                    "reason": decision.get("reason"),
                    "stream_book_verdict": decision.get("stream_book_verdict"),
                    "failed_components": decision.get("failed_components")},
                per_symbol={s: {"verdict": d.get("verdict"),
                                "reason": d.get("reason"),
                                "failed_components":
                                    d.get("failed_components")}
                            for s, d in decisions.items()},
                predicates=preds,
                runtime_evidence={"stream": stream, "same_book": same_book},
                predicate_order=list(ORDER),
                # migration 213 (added beside; no predicate reads them)
                focus_universe=facts.get("focus_universe") or {
                    "status": "UNMEASURED", "count": None, "per_tier": None,
                    "members": None, "why": "not gathered"},
                same_book_samples=facts.get("same_book_samples") or {
                    "status": "UNMEASURED", "sample_count": None,
                    "agreement_pct": None, "why": "not gathered"},
                c12_proofs=facts.get("c12_proofs") or {
                    "status": "UNMEASURED", "count": None, "records": None,
                    "why": "not gathered"})


async def evaluate(conn, *, now: float | None = None, env=None,
                   symbol: str | None = None) -> dict:
    """THE ENDPOINT'S ANSWER. Read-only; never raises on a missing table."""
    return assemble(await gather(conn, now=now, env=env, symbol=symbol))

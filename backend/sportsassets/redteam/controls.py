"""THE RED-TEAM CONTROLS OVER BETTOR'S OWN EVIDENCE.

Each control returns {control, status GREEN / RED / UNKNOWN, blockers,
evidence}. Missing evidence is never success: a source that cannot be read
is UNKNOWN or RED, never GREEN. Pure functions take already-read evidence;
the async readers are SELECT-only (they run in the caller's READ ONLY
transaction).

  truth_quorum        INTERNAL_LEDGER / VENUE_POSITIONS / VENUE_BALANCE /
                      MARKET_DATA / AUDREY_RECONCILIATION agree
  profit_breakers     per mechanism, expected vs realized per independent
                      event (DIRECTIONAL, EXECUTION_ALPHA, XAVIER_MANAGEMENT,
                      SAME_VENUE_ARB, CROSS_VENUE_ARB, ALLOCATION_ALPHA)
  attribution         selection + execution + allocation + management +
                      settlement + outcome variance = realized PAPER P&L
  digital_twin        repaired fill replay: agreement >= 95%, optimistic
                      false fills <= 1%, lookahead 0
  sample_integrity    decision rows vs independent events; holdout registry
  multiple_testing    preregistered candidate set; no post-holdout selection
  capacity            positive-capacity frontier; deployment capped
  karen_value         saved loss - false-block opportunity cost
  credential_classes  PMX / PMUS / KALSHI slot classes (shape only; KALSHI
                      by the parsed key type, Ed25519 or RSA, never a value)
  migration_integrity applied fingerprints == this build's files
  fee_evidence        versioned fee schedules per venue / series
  settlement_certs    certified rules fingerprints, invalidations
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
from decimal import Decimal

from ..red_team import attribution as RTA
from ..red_team import capacity_guard as RTC
from ..red_team import credential_guard as RTCRED
from ..red_team import digital_twin_gate as RTT
from ..red_team import karen_value as RTK
from ..red_team import migration_guard as RTM
from ..red_team import profit_breaker as RTP
from ..red_team import sample_integrity as RTS
from ..red_team import truth_quorum as RTQ
from ..red_team.models import PositionTruth, TwinEvidence

GREEN, RED, UNKNOWN = "GREEN", "RED", "UNKNOWN"
QUORUM_MAX_AGE_S = 900.0
MECHANISMS = ("DIRECTIONAL", "SAME_VENUE_ARB", "CROSS_VENUE_ARB",
              "ROUTING_SAVINGS", "EXECUTION_ALPHA", "ALLOCATION_ALPHA",
              "XAVIER_MANAGEMENT")
TWIN_MIN_COMPARED = 100
MIN_INDEPENDENT_EVENTS = 100
MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "migrations"


def D(v) -> Decimal:
    try:
        return Decimal(str(v if v is not None else 0))
    except Exception:                                           # noqa: BLE001
        return Decimal(0)


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def result(control: str, status: str, blockers, evidence: dict) -> dict:
    return {"control": control, "status": status,
            "blockers": sorted(set(blockers or ())), "evidence": evidence}


def evidence_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True,
                                     default=str).encode()).hexdigest()


async def _has(conn, t: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t))


# ── truth-source quorum ──────────────────────────────────────────────────

def quorum(rows: list, *, now: float, audrey_open_discrepancies: int | None,
           max_age_s: float = QUORUM_MAX_AGE_S) -> dict:
    q = RTQ.truth_quorum(rows, max_age_s=max_age_s, now=now)
    blockers = list(q["blockers"])
    if audrey_open_discrepancies:
        blockers.append("AUDREY_OPEN_DISCREPANCIES:%d"
                        % audrey_open_discrepancies)
    claims = {k: sorted(v) for k, v in q["claims"].items()}
    return result("TRUTH_QUORUM", GREEN if not blockers else RED, blockers,
                  {"sources_current": list(q["sources"]),
                   "required": list(RTQ.REQUIRED_SOURCES),
                   "claims_by_source": claims, "max_age_s": max_age_s,
                   "effect": ("RED blocks NEW capital entry; exits, "
                              "reductions and protection stay available")})


async def quorum_rows(conn, *, now: float, venue_confirmed: bool,
                      market_data_green: bool) -> tuple:
    """(PositionTruth rows, Audrey open discrepancies). Missing = absent
    row (MISSING_SOURCE), never a zero row."""
    rows = []
    if await _has(conn, "execmirror_fills"):
        for r in await conn.fetch(
                "SELECT us_market_slug k, sum(CASE WHEN intent ILIKE "
                "'%SELL%' THEN -qty ELSE qty END) q FROM execmirror_fills "
                "GROUP BY 1"):
            rows.append(PositionTruth("INTERNAL_LEDGER", str(r["k"]),
                                      D(r["q"]), None, now))
        if not rows:
            # the ledger is readable and holds nothing: a current EMPTY
            # ledger (one row so the source is present, quantity zero)
            rows.append(PositionTruth("INTERNAL_LEDGER", "NO_OPEN_POSITION",
                                      Decimal(0), None, now))
    snap = None
    if await _has(conn, "execmirror_snapshots"):
        snap = await conn.fetchrow(
            "SELECT positions, balances, extract(epoch FROM at) AS at FROM "
            "execmirror_snapshots ORDER BY at DESC LIMIT 1")
    if snap is not None and venue_confirmed:
        pos = _j(snap["positions"]) or []
        n = 0
        for p in pos if isinstance(pos, list) else []:
            k = p.get("marketSlug") or p.get("slug")
            q = p.get("netPosition") or p.get("qty")
            if k is not None:
                rows.append(PositionTruth("VENUE_POSITIONS", str(k), D(q),
                                          None, float(snap["at"])))
                n += 1
        if not n:
            rows.append(PositionTruth("VENUE_POSITIONS", "NO_OPEN_POSITION",
                                      Decimal(0), None, float(snap["at"])))
    if snap is not None and _j(snap["balances"]):
        rows.append(PositionTruth("VENUE_BALANCE", "BALANCE", Decimal(1),
                                  None, float(snap["at"])))
    if market_data_green:
        rows.append(PositionTruth("MARKET_DATA", "MD", Decimal(1), None, now))
    open_disc = None
    if await _has(conn, "smalllive_reconciliations"):
        r = await conn.fetchrow(
            "SELECT count(*) FILTER (WHERE status = 'DISCREPANCY') d, "
            "extract(epoch FROM max(reconciled_at)) at FROM "
            "smalllive_reconciliations")
        if r is not None and r["at"] is not None:
            open_disc = int(r["d"] or 0)
            rows.append(PositionTruth("AUDREY_RECONCILIATION", "RECONCILED",
                                      Decimal(0), None, float(r["at"])))
    return rows, open_disc


# ── mechanisms: expected vs realized per independent event ──────────────

def mechanism_rows(attributed: list, fixtures: dict) -> list:
    """Per settled PAPER position (intel.attribution rows that claim the
    identity): DIRECTIONAL expected = model edge, realized = model edge +
    outcome variance; EXECUTION_ALPHA and XAVIER_MANAGEMENT expected 0
    (no claimed alpha) and realized = their components. One row per
    position; events cluster them."""
    out = []
    for r in attributed:
        if not r.get("identity_claimed") or r.get("realized_pnl_usd") is None:
            continue
        ev = fixtures.get(r.get("group_id")) or r.get("us_market_slug") or "?"
        me, var = D(r.get("model_edge_usd")), D(r.get("outcome_variance_usd"))
        out.append({"mechanism": "DIRECTIONAL", "event_key": ev,
                    "expected": me, "realized": me + var})
        out.append({"mechanism": "EXECUTION_ALPHA", "event_key": ev,
                    "expected": Decimal(0),
                    "realized": D(r.get("execution_edge_usd"))})
        out.append({"mechanism": "XAVIER_MANAGEMENT", "event_key": ev,
                    "expected": Decimal(0),
                    "realized": D(r.get("management_usd"))})
    return out


def by_event(rows: list) -> list:
    """Cluster rows to ONE observation per (mechanism, event): repeated
    rows of one event are one independent observation."""
    agg: dict = {}
    for r in rows:
        k = (r["mechanism"], r["event_key"])
        a = agg.setdefault(k, [Decimal(0), Decimal(0)])
        a[0] += D(r["expected"])
        a[1] += D(r["realized"])
    return [RTP.ResidualObservation(m, e, x) for (m, _ev), (e, x)
            in sorted(agg.items())]


def profit_breakers(rows: list) -> dict:
    obs = by_event(rows)
    rep = RTP.mechanism_breaker(obs, min_n=30)
    events = {}
    for r in rows:
        events.setdefault(r["mechanism"], set()).add(r["event_key"])
    out = {}
    for m in MECHANISMS:
        if m in rep:
            v = rep[m]
            exp = sum((D(r["expected"]) for r in rows if r["mechanism"] == m),
                      Decimal(0))
            rea = sum((D(r["realized"]) for r in rows if r["mechanism"] == m),
                      Decimal(0))
            out[m] = {"status": v["status"], "reason": v["reason"],
                      "independent_events": len(events.get(m, ())),
                      "expected_pnl": str(exp), "realized_pnl": str(rea),
                      "residual": str(rea - exp),
                      "mean_residual": str(v["mean_residual"]),
                      "confidence_bound_upper_90": (
                          None if v["upper_90"] is None
                          else str(v["upper_90"])),
                      "cumulative_residual": str(v["cumulative_residual"])}
        else:
            out[m] = {"status": "SHADOW_ONLY",
                      "reason": "NO_INDEPENDENT_OBSERVATIONS",
                      "independent_events": 0, "expected_pnl": "0",
                      "realized_pnl": "0", "residual": "0",
                      "mean_residual": None,
                      "confidence_bound_upper_90": None,
                      "cumulative_residual": "0"}
    eligible = [m for m, v in out.items() if v["status"] == "ELIGIBLE"]
    disabled = [m for m, v in out.items() if v["status"] == "DISABLED"]
    return result("PROFIT_BREAKERS", GREEN if eligible and not disabled
                  else RED, ["MECHANISM_DISABLED:%s" % m for m in disabled]
                  + ([] if eligible else ["NO_MECHANISM_ELIGIBLE"]),
                  {"mechanisms": out, "eligible": eligible,
                   "disabled": disabled,
                   "rule": ("each sleeve alone: a failing sleeve is disabled "
                            "without touching another; a good sleeve never "
                            "hides a failing one")})


# ── attribution ──────────────────────────────────────────────────────────

def attribution(attributed: list) -> dict:
    claim = [r for r in attributed if r.get("identity_claimed")
             and r.get("realized_pnl_usd") is not None]
    comps = {"selection": sum((D(r.get("model_edge_usd")) for r in claim),
                              Decimal(0)),
             "execution": sum((D(r.get("execution_edge_usd")) for r in claim),
                              Decimal(0)),
             # Allie resizes nothing (SHADOW weights): no realized dollar
             "allocation": Decimal(0),
             "management": sum((D(r.get("management_usd")) for r in claim),
                               Decimal(0)),
             "settlement": sum((D(r.get("settlement_usd")) for r in claim),
                               Decimal(0)),
             "outcome_variance": sum((D(r.get("outcome_variance_usd"))
                                      for r in claim), Decimal(0))}
    realized = sum((D(r.get("realized_pnl_usd")) for r in claim), Decimal(0))
    g = RTA.reconcile_attribution(realized_pnl=realized, components=comps)
    unmeasured = sum(1 for r in attributed if r.get("realized_pnl_usd")
                     is not None and not r.get("identity_claimed"))
    blockers = list(g["blockers"])
    if not claim:
        blockers.append("NO_SETTLED_POSITION_CLAIMS_THE_IDENTITY")
    return result("ATTRIBUTION", GREEN if not blockers else RED, blockers,
                  {"positions_identity_claimed": len(claim),
                   "settled_positions_without_identity": unmeasured,
                   "realized_pnl": str(g["realized"]),
                   "components": {k: str(v) for k, v in
                                  g["components"].items()},
                   "component_sum": str(g["component_sum"]),
                   "residual": str(g["residual"]),
                   "agents": {"DEREK": "selection", "ARCHER": "execution",
                              "CHIEF_ALLOCATOR": "allocation",
                              "XAVIER": "management"},
                   "rule": "one additive identity; no dollar claimed twice"})


async def attributed_positions(conn, *, now: float) -> tuple:
    from ..intel import attribution as IA
    rows = await IA.load_paper(conn, now=now)
    gids = sorted({r.get("group_id") for r in rows if r.get("group_id")})
    fx = {}
    if gids:
        fx = {r["group_id"]: r["fixture"] for r in await conn.fetch(
            "SELECT group_id, max(fixture) fixture FROM paper_fills WHERE "
            "group_id = ANY($1::text[]) GROUP BY 1", gids)}
    return rows, fx


# ── digital twin ─────────────────────────────────────────────────────────

def twin(rep: dict) -> dict:
    e = TwinEvidence(
        compared=int(rep.get("compared") or rep.get("replayed") or 0),
        matched=int(rep.get("agree") or 0),
        optimistic_false_fills=int(rep.get("optimistic_false_fills") or 0),
        lookahead_violations=int(rep.get("lookahead_violations") or 0),
        mismatch_reasons=dict(rep.get("residual_mismatch_taxonomy") or {}))
    g = RTT.twin_gate(e)
    blockers = list(g["blockers"])
    if e.compared < TWIN_MIN_COMPARED:
        blockers.append("FEWER_THAN_%d_FRESH_ORDERS:%d" % (TWIN_MIN_COMPARED,
                                                           e.compared))
    if "optimistic_false_fills" not in rep:
        blockers.append("OPTIMISTIC_FALSE_FILLS_NOT_MEASURED")
    return result("DIGITAL_TWIN", GREEN if not blockers else RED, blockers,
                  {"compared": e.compared, "matched": e.matched,
                   "agreement": g.get("agreement"),
                   "optimistic_false_fills": e.optimistic_false_fills,
                   "optimistic_false_fill_rate": g.get(
                       "optimistic_false_fill_rate"),
                   "lookahead_violations": e.lookahead_violations,
                   "mismatch_taxonomy": dict(e.mismatch_reasons),
                   "thresholds": {"min_agreement": 0.95,
                                  "max_optimistic_false_fill_rate": 0.01,
                                  "lookahead": 0,
                                  "frozen": "package defaults, unchanged"},
                   "historical_baseline": rep.get("diagnosis_receipt"),
                   "twin_pnl_used_for_capital": False})


# ── independent samples / holdout / multiple testing ────────────────────

def samples(prob: dict, registry: dict) -> dict:
    rows = int(prob.get("decisions_scored") or 0)
    events = int(prob.get("independent_events") or 0)
    parts = registry.get("partitions") or {}
    g = RTS.independent_sample_gate(
        decision_rows=rows, independent_events=events,
        train_events=set(parts.get("train") or ()),
        test_events=set(parts.get("test") or ()),
        holdout_events=set(parts.get("holdout") or ()),
        holdout_open_count=int(registry.get("holdout_opens") or 0),
        min_independent_events=MIN_INDEPENDENT_EVENTS)
    blockers = list(g["blockers"])
    if not parts:
        blockers.append("NO_EVENT_CLUSTERED_PARTITION_REGISTERED")
    return result("SAMPLE_INTEGRITY", GREEN if not blockers else RED,
                  blockers, {"decision_rows": rows,
                             "independent_events": events,
                             "row_to_event_ratio": g["row_to_event_ratio"],
                             "holdout_opens": registry.get("holdout_opens"),
                             "minimum_independent_events":
                                 MIN_INDEPENDENT_EVENTS,
                             "source": "completion probability evidence "
                                       "(first ENTER per strategy x market x "
                                       "side, event-clustered)"})


def multiple_testing(registry: dict) -> dict:
    tested = int(registry.get("candidates_tested") or 0)
    pre = int(registry.get("preregistered") or 0)
    selected_after = bool(registry.get("selected_after_holdout"))
    pbo, dsr = registry.get("pbo_ok"), registry.get("dsr_ok")
    g = RTS.multiple_testing_gate(candidates_tested=tested,
                                  pre_registered=pre,
                                  selected_after_holdout=selected_after,
                                  pbo_ok=bool(pbo), dsr_ok=bool(dsr))
    blockers = list(g["blockers"])
    if not pre:
        blockers.append("NO_PREREGISTERED_CANDIDATE_SET")
    return result("MULTIPLE_TESTING", GREEN if not blockers else RED,
                  blockers, {"candidates_tested": tested,
                             "preregistered": pre,
                             "selected_after_holdout": selected_after,
                             "pbo": "UNMEASURED" if pbo is None else pbo,
                             "dsr": "UNMEASURED" if dsr is None else dsr,
                             "champion": "CASH (no candidate beats it "
                                         "absolutely)"})


async def holdout_registry(conn) -> dict:
    if not await _has(conn, "red_team_holdout_registry"):
        return {}
    rows = await conn.fetch(
        "SELECT kind, candidate_count, detail, at FROM "
        "red_team_holdout_registry ORDER BY at")
    out = {"holdout_opens": 0, "preregistered": 0, "candidates_tested": 0,
           "selected_after_holdout": False, "partitions": {}}
    opened_at = None
    for r in rows:
        d = _j(r["detail"]) or {}
        if r["kind"] == "PREREGISTER":
            out["preregistered"] = max(out["preregistered"],
                                       int(r["candidate_count"] or 0))
            out["partitions"] = d.get("partitions") or out["partitions"]
        elif r["kind"] == "HOLDOUT_OPEN":
            out["holdout_opens"] += 1
            opened_at = opened_at or r["at"]
            out["candidates_tested"] = max(out["candidates_tested"],
                                           int(r["candidate_count"] or 0))
        elif r["kind"] == "SELECTION" and opened_at is not None \
                and r["at"] > opened_at:
            out["selected_after_holdout"] = True
    return out


# ── capacity ─────────────────────────────────────────────────────────────

def capacity(points: list, *, requested_usd) -> dict:
    pts = [RTC.CapacityPoint(int(p["qty"]), D(p["lb_ev_per_contract"]),
                             D(p["fill_probability"]), D(p["capital_hours"]),
                             D(p["expected_net"])) for p in points]
    g = RTC.capacity_frontier(pts) if pts else {
        "green": False, "max_positive_qty": 0, "best_qty": None,
        "best_expected_net": None, "blockers": ("NO_CAPACITY_EVIDENCE",)}
    proven_usd = sum((D(p["capital_usd"]) for p in points
                      if int(p["qty"]) <= int(g["max_positive_qty"] or 0)
                      and D(p["lb_ev_per_contract"]) > 0
                      and D(p["expected_net"]) > 0), Decimal(0))
    cap = RTC.deployment_cap(requested_turnover=D(requested_usd),
                             proven_positive_capacity=proven_usd)
    return result("CAPACITY", GREEN if g["green"] else RED,
                  list(g["blockers"]),
                  {"points": len(pts), "max_positive_qty":
                   g["max_positive_qty"], "best_qty": g["best_qty"],
                   "proven_positive_capacity_usd": str(proven_usd),
                   "requested_usd": str(D(requested_usd)),
                   "maximum_deployment_usd": str(cap),
                   "remainder_cash_usd": str(D(requested_usd) - cap),
                   "rule": "turnover target never overrides economics"})


# ── Karen ────────────────────────────────────────────────────────────────

#: the two twin metrics KAREN_VALUE reads, as twin.scorecards.karen writes
#: them: book COUNTERFACTUAL (a metric derived from a twin world; its basis
#: book is named in the metric, `..._paper_basis`), never book PAPER
KAREN_TWIN_METRICS = ("loss_avoided_paper_basis",
                      "profit_sacrificed_paper_basis")
KAREN_TWIN_BOOK = "COUNTERFACTUAL"
#: the twin's own verdict that a value may be concluded from (twin.common.
#: metric: INSUFFICIENT_SAMPLE is "shown, nothing concluded")
KAREN_TWIN_CONCLUSIVE = "MEASURED"


def karen_twin_blockers(rows: dict | None) -> list:
    """Why the twin's Karen rows conclude nothing, per metric, named with the
    twin's own status and reason (an absent row, UNAVAILABLE / UNPROVEN with
    its reason, INSUFFICIENT_SAMPLE with its sample). [] when both metrics
    are MEASURED with a value."""
    out = []
    for m in KAREN_TWIN_METRICS:
        r = (rows or {}).get(m)
        if r is None:
            out.append("KAREN_TWIN_ROW_ABSENT:%s" % m)
        elif r.get("status") == "INSUFFICIENT_SAMPLE":
            out.append("KAREN_TWIN_INSUFFICIENT_SAMPLE:%s:%s" % (
                m, r.get("sample_n")))
        elif r.get("status") != KAREN_TWIN_CONCLUSIVE or r.get(
                "value") is None:
            out.append("KAREN_TWIN_%s:%s:%s" % (
                r.get("status") or "NO_STATUS", m,
                r.get("reason") or "NO_REASON"))
    return out


def karen(saved_loss_usd, false_block_cost_usd, *, source: str,
          twin_rows: dict | None = None) -> dict:
    """KAREN_VALUE: saved loss - false-block cost from the twin's
    KAREN_BLOCK_ACCEPTED world. GREEN only when both values exist AND, when
    the twin's rows are supplied, both are MEASURED in one run -- a value
    the twin itself calls INSUFFICIENT_SAMPLE or UNAVAILABLE concludes
    nothing. Otherwise UNKNOWN, with the twin's own reason named."""
    why = [] if twin_rows is None else karen_twin_blockers(twin_rows)
    measured = (saved_loss_usd is not None
                and false_block_cost_usd is not None and not why)
    # an unmeasured value is null, never "0" (it used to read
    # saved_loss_usd "0" / value_added_usd "0" beside UNKNOWN)
    ev = {"saved_loss_usd": (str(D(saved_loss_usd))
                             if saved_loss_usd is not None else None),
          "false_block_opportunity_cost_usd": (
              str(abs(D(false_block_cost_usd)))
              if false_block_cost_usd is not None else None),
          "value_added_usd": (str(RTK.karen_incremental_value(
              saved_loss_usd, false_block_cost_usd)) if measured else None),
          "source": source,
          "rule": "saved loss - false-block cost; never block count"}
    if twin_rows is not None:
        ev["twin"] = {"book": KAREN_TWIN_BOOK,
                      "metrics": list(KAREN_TWIN_METRICS),
                      "rows": {m: twin_rows.get(m)
                               for m in KAREN_TWIN_METRICS}}
    return result("KAREN_VALUE", GREEN if measured else UNKNOWN,
                  [] if measured else
                  ["KAREN_COUNTERFACTUALS_UNMEASURED"] + why, ev)


# ── credentials (shape only) ─────────────────────────────────────────────

#: the Kalshi classes, read from the PARSED key (kalshi_key), never from the
#: PEM header (Kalshi: "The PEM header does not identify the key type")
KALSHI_RSA_API_KEY = "KALSHI_RSA_API_KEY"
KALSHI_ED25519_API_KEY = "KALSHI_ED25519_API_KEY"
#: a key that parses but is neither documented Kalshi type
KALSHI_KEY_TYPE_NOT_DOCUMENTED = "KALSHI_KEY_TYPE_NOT_ED25519_OR_RSA"

#: THE CLASSES EACH SLOT ACCEPTS. The red-team package's EXPECTED (imported
#: byte for byte, test_red_team_package_parity) names ONE class per slot and
#: predates Kalshi's Ed25519 keys: KALSHI = KALSHI_RSA_API_KEY. Kalshi
#: documents two key types, Ed25519 (recommended; the default) and RSA
#: (kalshi_key.DOCS), and since RC5 every Kalshi signer in this repository
#: signs with either (kalshi_key: kalshi_ws's handshake and limits read,
#: kalshi_venue's REST client). Production's Kalshi key is Ed25519 and the
#: plane's signed account-limits read answers 200 with it, yet this control
#: read RED (CREDENTIAL_CLASS_MISMATCH:KALSHI:ED25519_PEM, pm-acceptance run
#: 37738089957). So KALSHI accepts both documented classes; PMX and PMUS keep
#: the package's single class. This widens no authority: a class names a key
#: type, and Kalshi live money stays NOT ACTIVATED behind kalshi_venue's own
#: gate. A class outside the set is still a MISMATCH.
APPROVED_CLASSES = {
    "PMX": (RTCRED.EXPECTED["PMX"],),
    "PMUS": (RTCRED.EXPECTED["PMUS"],),
    "KALSHI": (KALSHI_ED25519_API_KEY, RTCRED.EXPECTED["KALSHI"]),
}
APPROVED_BASIS = {
    "KALSHI": "docs.kalshi.com getting_started/api_keys: Ed25519 "
              "(recommended) or RSA; type read from the parsed key "
              "(kalshi_key); captured at %s" % "research/kalshi_canonical_"
              "venue/REP_PRODUCTION_CONTRACT_2026-10-07/docs/getting_started_"
              "api_keys.md",
}


def kalshi_class(key_id: str, pem) -> str | None:
    """The Kalshi slot's class from the PARSED key, never its value: None
    when nothing is configured; KALSHI_ED25519_API_KEY / KALSHI_RSA_API_KEY
    for the two documented types; KALSHI_KEY_TYPE_NOT_ED25519_OR_RSA for a
    key of another algorithm; UNRECOGNISED_SHAPE for anything that is not a
    loadable key (or a key id without a key)."""
    from .. import kalshi_key as KK
    kid = str(key_id or "").strip()
    if not kid and not (pem is not None and str(pem).strip()):
        return None
    d = KK.describe(pem)
    if d.get("type") == KK.KEY_ED25519:
        return KALSHI_ED25519_API_KEY
    if d.get("type") == KK.KEY_RSA:
        return KALSHI_RSA_API_KEY
    if d.get("refusal") == KK.R_KEY_TYPE_NOT_DOCUMENTED:
        return KALSHI_KEY_TYPE_NOT_DOCUMENTED
    return "UNRECOGNISED_SHAPE"


def credential_classes(env=None) -> dict:
    """{slot: class | None} from THIS process's environment, by shape only
    (market_data_identity.slot_shape): never a value, length or prefix.
    The KALSHI slot is classified from the parsed key (kalshi_class)."""
    from .. import market_data_identity as MDI
    from .. import venue_key as VK
    env = os.environ if env is None else env

    def cls(kid, sec):
        s = env.get(sec, "")
        shape = MDI.slot_shape(env.get(kid, ""), s)
        if shape == MDI.SHAPE_ABSENT:
            return None
        # slot_shape calls ANY PEM "RSA". A PEM holding an Ed25519 key
        # (PKCS#8, 119 chars -- Kalshi issues Ed25519 by default) is not
        # an RSA key: naming it RSA made a non-RSA slot read MATCHES.
        if shape == MDI.SHAPE_RSA_PEM and VK._decode(s) is not None:
            return "ED25519_PEM"
        # something present that is neither shape is a MISMATCH, never
        # "not provisioned"
        return {MDI.SHAPE_PMUS_RETAIL: "POLYMARKET_US_ED25519",
                MDI.SHAPE_RSA_PEM: "RSA_PEM"}.get(shape, "UNRECOGNISED_SHAPE")

    def pm(c):
        return "POLYMARKET_EXCHANGE_RSA_M2M" if c == "RSA_PEM" else c

    pmx = cls("PMX_KEY_ID", "PMX_PRIVATE_KEY_B64")
    # EVERY PMUS slot present in this process is classified, and the slot
    # class is the first one that does NOT match (P1 closeout 2026-10-07):
    # the execution-mirror pair used to be read first and HID the funded
    # pair -- production's API reported PMUS = POLYMARKET_US_ED25519 while
    # its PMUS_KEY_ID / PMUS_SECRET_KEY held the PMX RSA key that
    # track_record and pmus_account sign with.
    pmus_slots = {"PMUS_KEY_ID/PMUS_SECRET_KEY":
                  pm(cls("PMUS_KEY_ID", "PMUS_SECRET_KEY")),
                  "PMUS_EXECMIRROR_KEY_ID/PMUS_EXECMIRROR_SECRET_KEY":
                  pm(cls("PMUS_EXECMIRROR_KEY_ID",
                         "PMUS_EXECMIRROR_SECRET_KEY"))}
    present = [c for c in pmus_slots.values() if c is not None]
    pmus = next((c for c in present if c != "POLYMARKET_US_ED25519"),
                present[0] if present else None)
    # the value AS CONFIGURED (env.get, not stripped): the class is read
    # from the same bytes the signers load
    kal = kalshi_class(env.get("KALSHI_API_KEY_ID", ""),
                       env.get("KALSHI_PRIVATE_KEY_PEM"))
    # BY KEY (RC6 red-team): one key pair in two venues' slots, which no
    # shape can tell apart (a Kalshi RSA key vs the PMX RSA key); names only
    from .. import credential_isolation as CI
    return {"PMX": pm(pmx), "PMUS": pmus, "KALSHI": kal,
            "PMUS_SLOTS": pmus_slots,
            "CROSS_VENUE_KEY_REUSE": CI.reuse(env)}


def credentials(by_process: dict) -> dict:
    """by_process {process: {slot: class|None}}. A MISMATCH (a wrong class
    in a slot, in any process) is RED and names the owner action; a slot
    that holds nothing blocks only its own path (reported, not RED)."""
    merged: dict = {}
    for _p, slots in by_process.items():
        for slot, c in (slots or {}).items():
            if c is not None and slot in RTCRED.EXPECTED:
                merged.setdefault(slot, set()).add(c)
    mism, missing, verdicts = [], [], {}
    for slot in RTCRED.EXPECTED:
        ok = APPROVED_CLASSES[slot]
        got = sorted(merged.get(slot) or ())
        if not got:
            missing.append(slot)
            verdicts[slot] = "NOT_PROVISIONED_PATH_BLOCKED"
        elif any(c not in ok for c in got):
            mism.append("CREDENTIAL_CLASS_MISMATCH:%s:%s" % (
                slot, ",".join(c for c in got if c not in ok)))
            verdicts[slot] = "MISMATCH_PATH_BLOCKED"
        else:
            verdicts[slot] = "MATCHES"
    actions = []
    if any(m.startswith("CREDENTIAL_CLASS_MISMATCH:PMUS") for m in mism):
        want = RTCRED.EXPECTED["PMUS"]
        where = []
        for p, s in sorted(by_process.items()):
            s = s or {}
            slots = s.get("PMUS_SLOTS")
            if isinstance(slots, dict):
                where += ["%s %s holds %s" % (p, slot, c)
                          for slot, c in sorted(slots.items())
                          if c is not None and c != want]
            elif s.get("PMUS") not in (None, want):
                # a process on a build without PMUS_SLOTS (deploy skew):
                # still named, with the slot it classified
                where.append("%s PMUS holds %s" % (p, s["PMUS"]))
        actions.append("enter a Polymarket US retail Ed25519 API key "
                       "(polymarket.us/developer) in the PMUS key-id / "
                       "secret slots%s; no slot named here holds one. "
                       "Authentication is never weakened." % (
                           (" (" + "; ".join(where) + ")") if where else ""))
    kal_bad = sorted({c for p, s in by_process.items()
                      for c in [(s or {}).get("KALSHI")]
                      if c is not None
                      and c not in APPROVED_CLASSES["KALSHI"]})
    if "ED25519_PEM" in kal_bad:
        # DEPLOY SKEW, NOT AN OWNER ACTION: a process still on a build whose
        # control classified the Kalshi PEM by shape (ED25519_PEM) and whose
        # REST signer was RSA-only. The key is a documented type; the
        # remedy is deploying this build there.
        actions.append("a process still reports KALSHI ED25519_PEM: it runs "
                       "a build that predates Ed25519 support in every "
                       "Kalshi signer; deploy this build there (the key is "
                       "a documented Kalshi type and needs no change)")
    other = [c for c in kal_bad if c != "ED25519_PEM"]
    if other:
        where = sorted("%s holds %s" % (p, (s or {}).get("KALSHI"))
                       for p, s in by_process.items()
                       if (s or {}).get("KALSHI") in other)
        actions.append("KALSHI_PRIVATE_KEY_PEM is not a Kalshi API private "
                       "key of a documented type (%s); Kalshi documents "
                       "Ed25519 (recommended) or RSA. Enter the existing "
                       "key's PEM exactly as Kalshi issued it." % "; ".join(
                           where))
    # a PMUS slot that holds NOTHING is not a mismatch (it blocks only its
    # own path) -- but one present slot must not hide another's absence:
    # an API with only the execution-mirror pair reads PMUS MATCHES while
    # the FUNDED pair is provisioned nowhere
    absent_pmus = sorted(
        "%s %s" % (p, slot) for p, s in by_process.items()
        for slot, c in (((s or {}).get("PMUS_SLOTS") or {}).items()
                        if isinstance((s or {}).get("PMUS_SLOTS"), dict)
                        else ())
        if c is None)
    # ONE KEY PAIR IN TWO VENUES' SLOTS (credential_isolation, by key): RED
    # in any process, whatever the shapes say; names only
    reused = sorted("%s:%s" % (p, pair) for p, s in by_process.items()
                    for pair in ((s or {}).get("CROSS_VENUE_KEY_REUSE")
                                 or ()))
    if reused:
        mism = mism + ["CREDENTIAL_REUSED_ACROSS_VENUES:%s" % r
                       for r in reused]
        actions.append("one key pair is configured for two venues (%s): "
                       "each venue's slot must hold that venue's own key; "
                       "remove the other venue's key from the slot it was "
                       "pasted into. The Kalshi signers refuse such a key."
                       % "; ".join(reused))
    return result("CREDENTIAL_CLASSES", RED if mism else GREEN, mism,
                  {"expected": dict(RTCRED.EXPECTED),
                   "approved": {k: list(v)
                                for k, v in APPROVED_CLASSES.items()},
                   "approved_basis": dict(APPROVED_BASIS),
                   "by_process":
                   {p: dict(s or {}) for p, s in by_process.items()},
                   "pmus_slots_not_provisioned": absent_pmus,
                   "cross_venue_key_reuse": reused,
                   "verdicts": verdicts, "not_provisioned": missing,
                   "owner_actions": actions,
                   "values_exposed": False})


# ── migrations ───────────────────────────────────────────────────────────

def repo_migrations(directory: pathlib.Path = MIGRATIONS_DIR) -> dict:
    out = {}
    for p in sorted(directory.glob("*.sql")):
        out[p.name] = hashlib.sha256(p.read_text().encode()).hexdigest()
    return out


def migrations(applied: dict, repo: dict, *, fresh_db_passed) -> dict:
    """applied {version: content_sha}, repo {file: sha256}."""
    drift = sorted(v for v, sha in applied.items()
                   if v in repo and sha and sha != repo[v])
    gone = sorted(v for v in applied if v not in repo)
    shared = {v: applied[v] for v in applied if v in repo}
    af = evidence_hash(sorted(shared.items()))
    rf = evidence_hash(sorted((v, repo[v]) for v in shared))
    g = RTM.migration_guard(applied_fingerprint=af, repo_fingerprint=rf,
                            fresh_db_passed=bool(fresh_db_passed),
                            forward_only=not gone)
    blockers = list(g["blockers"])
    if fresh_db_passed is None:
        blockers = [b for b in blockers if b != "FRESH_DB_MIGRATION_TEST_"
                    "FAILED"] + ["FRESH_DB_RESULT_NOT_IN_THIS_PROCESS"]
    status = RED if (drift or gone) else (GREEN if not blockers else UNKNOWN)
    return result("MIGRATION_INTEGRITY", status, blockers,
                  {"applied": len(applied), "repo": len(repo),
                   "edited_in_place": drift, "applied_not_in_build": gone,
                   "not_yet_applied": sorted(set(repo) - set(applied)),
                   "applied_fingerprint": af, "repo_fingerprint": rf,
                   "fresh_db": ("capital-critical's test_fresh_database_"
                                "migrates on the exact SHA (release "
                                "receipt)" if fresh_db_passed else
                                "UNPROVEN_HERE")})


async def applied_migrations(conn) -> dict:
    return {r["version"]: r["content_sha"] for r in await conn.fetch(
        "SELECT version, content_sha FROM schema_migrations")}


# ── fees ─────────────────────────────────────────────────────────────────

def fee_control(evidence: list, *, now: float) -> dict:
    from . import fees as F
    rows = [F.gate_row(e, now=now) for e in evidence]
    bad = [r for r in rows if not r["green"]]
    return result("FEE_EVIDENCE", GREEN if rows and not bad else RED,
                  ["%s:%s:%s" % (r["venue"], r["market_key"], b)
                   for r in bad for b in r["blockers"]]
                  + ([] if rows else ["NO_FEE_EVIDENCE"]),
                  {"schedules": rows,
                   "rule": "unknown fee = ineligible, never zero; maker fee "
                           "never assumed zero"})

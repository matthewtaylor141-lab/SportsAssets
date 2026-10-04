"""THE GLOBAL OPPORTUNITY RANKING ENGINE (control plane, stream C). Pure.

OWNER (control plane section 5): "Do not process them FIFO. Build a universal
ranking layer before scarce capital and execution resources are allocated
... Do not hard-code one simplistic scalar ... Admission should process the
best opportunities first. Add starvation protections for lower-frequency
strategy classes without allowing bad opportunities to jump better
opportunities merely because they waited longer."

WHAT IT DOES. One call, `rank_batch`, takes every candidate of one decision
batch (each built from the SAME immutable decision-time snapshot the
canonical decision uses -- `rank_inputs.py`), the portfolio as of the batch
clock (exposure from position/ledger truth, `allocator.portfolio_from_ledger`)
and a frozen, versioned `RankerConfig`, and returns one row per candidate:

  RANK                       1..N, best first; the admission order
  RAW_EV                     the modelled gross EV at the requested size
                             before execution costs (headline, never ranked)
  NET_EV                     E[net executable $ | fill] x the fill factor:
                             the UNCONDITIONAL expected net. The fill factor
                             is the expected fill FRACTION when measured
                             (red-team item 13: partial fills; E[filled qty]
                             / requested qty, zero-fill mass included), else
                             P(fill). OWNER-VERIFIED: pos_capacity's
                             executable EV is CONDITIONAL ON FILL, so the
                             P(fill) factor stays (lost_opportunity/score.py
                             keeps it for the same reason). Never removed.
  CAPITAL_EFFICIENCY_COMPONENT  NET_EV / expected capital-hours (USD per
                             USD-hour of capital, held to release): the
                             economic density every other component scales
  CONFIDENCE_COMPONENT       the share of the per-contract net edge that
                             survives the probability's uncertainty band x
                             the originating model/agent's historical
                             reliability (a LOWER bound, shrunk -- stream A)
  EXECUTION_COMPONENT        the share of the expected net that survives the
                             execution-cost uncertainty (slippage sd at size)
  LIQUIDITY_COMPONENT        capacity coverage of the requested size at the
                             SELECTED execution style (item 11; a measured
                             zero capacity is zero, never the full size --
                             item 12) x the share of the edge a half-spread
                             exit would leave
  RISK_COMPONENT             edge decay over (probability age + expected time
                             to fill) from the class's measured half-life
                             (LAB-A) x (1 - the settlement-exception upper
                             bound, r30c-risk)
  CORRELATION_COMPONENT      capital base / (capital base + correlated
                             exposure): the open exposure already held in the
                             candidate's event / market / team / league /
                             sport / strategy / agent / market-family / venue
                             groups, weighted by the RESEARCH correlation
                             assumption per dimension. An uncorrelated
                             candidate keeps 1.0 -- that is its
                             diversification benefit relative to a
                             correlated one
  FINAL_PRIORITY             CAPITAL_EFFICIENCY x CONFIDENCE x EXECUTION x
                             LIQUIDITY x RISK x CORRELATION. Every factor is
                             stored with its value, status, basis and inputs,
                             so the priority is reproducible from the row.

NOT ONE SIMPLISTIC SCALAR. Each component is computed from its own measured
inputs, stored separately and explained; the priority is their product so a
weakness in ANY dimension pulls the candidate down (a high-EV candidate with
no depth, a stale edge or a concentrated event does not ride its EV). Ties
break on NET_EV, then CAPITAL_EFFICIENCY, then the stable ids
(opportunity_id, snapshot_id) -- never on arrival order.

MISSING INPUTS ARE NEVER INVENTED. A factor whose input is absent is
UNAVAILABLE with its reason and takes the config's conservative penalty
(`unavailable_factor`, RESEARCH). NET_EV and CAPITAL_EFFICIENCY are the
economic base: without them there is nothing to scale, so the candidate is
UNRANKABLE (ranked after every rankable candidate, priority NULL, its reason
stored) -- the strongest penalty, not a manufactured zero. Probability
freshness is a GATE, never a factor: the 30-second rule (the frozen session
rule passed in on each candidate) is never loosened, and an unverifiable age
fails the gate.

NO FUTURE DATA. A candidate whose inputs were recorded after the batch clock
fails the gate INPUT_RECORDED_AFTER_THE_BATCH_CLOCK and is never admitted;
the readers that build candidates and the portfolio filter by the rows' own
recorded timestamps (`readers_capital.py`).

ADMISSION AND STARVATION. `admission_k` (the scarce execution slots of the
batch -- the caller passes the existing cadence bound, e.g. the frozen
session's `cadence.max_decisions_per_pass`) admits the best ELIGIBLE
candidates in RANK order. Starvation protection is a BOUNDED PER-CLASS QUOTA
inside those K slots: a strategy class that has an eligible candidate but
none in the top K gets up to `class_quota` slot(s) for its own best
candidate(s), at most floor(K x `max_reserved_share`) such slots per batch,
classes served in the order of their best candidate's rank. It is NOT a
priority boost: RANK and FINAL_PRIORITY never change, waiting time is not an
input at all (a candidate cannot climb by waiting -- it can only go stale),
only candidates that clear EVERY gate can use a quota slot, and a quota
candidate is processed in its own rank position (after every better admitted
candidate). The row stores `starvation_credit` 1 and admission basis
CLASS_QUOTA when it was admitted that way.

O(N log N) per batch: each candidate's components are O(dimensions) dict
lookups; one sort; the quota is one linear pass with a per-class dict. No
pairwise comparison across the universe.

AUTHORITY: SHADOW. Nothing here changes an order, a size, an admission order
or a gate on the production path. Integration into the canonical decision is
a later, explicit owner decision through a decision_hooks slot.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field

VERSION = "CP_OPPORTUNITY_RANKER_V1"
CONFIG_VERSION = "CP_RANKER_CONFIG_V1"
AUTHORITY = "SHADOW"
LABEL = "RESEARCH"

MEASURED, UNAVAILABLE, PARTIAL = "MEASURED", "UNAVAILABLE", "PARTIAL"
RANKABLE, UNRANKABLE = "RANKABLE", "UNRANKABLE"

ADMIT_RANK = "RANK_TOP_K"
ADMIT_QUOTA = "CLASS_QUOTA"
NOT_ADMITTED_CAPACITY = "NOT_ADMITTED_CAPACITY"
NOT_ADMITTED_GATES = "NOT_ADMITTED_GATES"
ADMISSION_BASES = (ADMIT_RANK, ADMIT_QUOTA, NOT_ADMITTED_CAPACITY,
                   NOT_ADMITTED_GATES)

SOURCES = ("PRODUCTION_SHADOW", "PAPER_REPLAY", "TEST")

# gate codes
G_UPSTREAM = "UPSTREAM_GATE_NOT_CLEARED"
G_UPSTREAM_UNKNOWN = "UPSTREAM_GATES_NOT_RECORDED"
G_FUTURE = "INPUT_RECORDED_AFTER_THE_BATCH_CLOCK"
G_UNRANKABLE = "UNRANKABLE"
G_NON_POSITIVE_NET_EV = "NON_POSITIVE_NET_EV"
G_NON_POSITIVE_CE = "NON_POSITIVE_CAPITAL_EFFICIENCY"
G_STALE = "PROBABILITY_OLDER_THAN_ITS_FRESHNESS_RULE"
G_FRESHNESS_UNKNOWN = "PROBABILITY_FRESHNESS_UNVERIFIABLE"
G_ZERO_CAPACITY = "MEASURED_ZERO_EXECUTABLE_CAPACITY"
G_NON_POSITIVE_PRIORITY = "NON_POSITIVE_FINAL_PRIORITY"

#: the exposure groups a candidate and a position can share
DIMENSIONS = ("EVENT", "MARKET", "TEAM", "SPORT", "LEAGUE", "STRATEGY",
              "AGENT", "MARKET_FAMILY", "VENUE")

#: RESEARCH correlation assumption per exposure dimension (the fraction of
#: an existing exposure in the same group treated as the same bet). The same
#: event / market is the current WORST-CASE assumption (1.0) the owner's
#: audit calls "safe but capacity-destructive" -- kept until event- and
#: settlement-based correlation evidence (r30c-risk correlation graph)
#: replaces it; the rest are labelled research weights, never approved
#: rails. Shared by the ranker's correlation component and the allocator's
#: correlated-exposure sizing reduction.
CORRELATION_ASSUMPTION = (
    ("EVENT", 1.0), ("MARKET", 1.0), ("TEAM", 0.5), ("LEAGUE", 0.1),
    ("SPORT", 0.05), ("STRATEGY", 0.05), ("AGENT", 0.05),
    ("MARKET_FAMILY", 0.05), ("VENUE", 0.0))

#: inputs the ranker must never read: waiting time is not evidence of value
IGNORED_WAITING_KEYS = ("waited_s", "queued_since", "first_seen_at",
                        "enqueued_at", "attempts", "age_in_queue_s")


@dataclass(frozen=True)
class RankerConfig:
    """THE FROZEN, VERSIONED RANKER CONFIGURATION. Every value is a RESEARCH
    parameter (addendum item 8: research / tournament parameters are kept
    apart from approved rails); its sha256 (`sha`) is stored on every row."""
    version: str = CONFIG_VERSION
    label: str = LABEL
    #: the multiplier a factor takes when its input is UNAVAILABLE
    unavailable_factor: float = 0.5
    #: quota slots per starved strategy class
    class_quota: int = 1
    #: at most this share of the admission slots may be quota slots
    max_reserved_share: float = 0.2
    correlation: tuple = CORRELATION_ASSUMPTION
    #: decimal places every stored number is rounded to (determinism)
    precision: int = 12

    def __post_init__(self):
        if not 0.0 < self.unavailable_factor < 1.0:
            raise ValueError("unavailable_factor must penalise: in (0, 1)")
        if self.class_quota < 0 or not 0.0 <= self.max_reserved_share < 1.0:
            raise ValueError("the starvation quota must be bounded")
        dims = [d for d, _ in self.correlation]
        if sorted(dims) != sorted(DIMENSIONS):
            raise ValueError("a correlation weight per dimension")
        if any(not 0.0 <= w <= 1.0 for _, w in self.correlation):
            raise ValueError("correlation weights are in [0, 1]")

    def to_dict(self) -> dict:
        return {"config_kind": "CP_RANKER_CONFIG", "version": self.version,
                "label": self.label,
                "unavailable_factor": self.unavailable_factor,
                "class_quota": self.class_quota,
                "max_reserved_share": self.max_reserved_share,
                "correlation": {d: w for d, w in self.correlation},
                "precision": self.precision,
                "ranker_version": VERSION}

    @property
    def sha(self) -> str:
        return sha_of(self.to_dict())

    def weight(self, dim: str) -> float:
        return dict(self.correlation)[dim]


DEFAULT_CONFIG = RankerConfig()


# ─────────────────────────── helpers ───────────────────────────────────

def sha_of(obj) -> str:
    """sha256 of canonical JSON (sorted keys, fixed separators)."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True,
                                     separators=(",", ":"),
                                     default=str).encode()).hexdigest()


def num(v):
    """A finite float or None (never NaN / inf, never a bool)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def clamp01(x: float) -> float:
    return 0.0 if x <= 0.0 else 1.0 if x >= 1.0 else x


def _r(v, n):
    return None if v is None else round(float(v), n)


def _factor(value, *, status, why=None, basis=None, inputs=None,
            sub=None) -> dict:
    return {"value": value, "status": status,
            "why": None if status == MEASURED else why, "basis": basis,
            "inputs": inputs or {}, "sub": sub or {}}


def _sub(value, why, cfg) -> tuple:
    """(factor, status, why): a measured value, or the conservative penalty
    with its reason."""
    if value is None:
        return cfg.unavailable_factor, UNAVAILABLE, why
    return clamp01(value), MEASURED, None


def _combine(name, subs: dict, basis: str, inputs: dict, cfg) -> dict:
    """A component as the product of its named sub-factors."""
    val = 1.0
    statuses, whys = set(), []
    for k, (v, st, why) in subs.items():
        val *= v
        statuses.add(st)
        if why:
            whys.append("%s: %s" % (k, why))
    status = (MEASURED if statuses == {MEASURED} else
              UNAVAILABLE if statuses == {UNAVAILABLE} else PARTIAL)
    return _factor(_r(val, cfg.precision), status=status,
                   why="; ".join(whys) or None, basis=basis, inputs=inputs,
                   sub={k: {"value": _r(v, cfg.precision), "status": st,
                            "why": why} for k, (v, st, why) in subs.items()})


# ─────────────────────────── exposure keys ─────────────────────────────

def keys_of(rec: dict) -> dict:
    """{dimension: [group keys]} of a candidate or a position. A dimension
    with no key is absent (its exposure is UNAVAILABLE, never pooled by
    guess -- titles are never grouped)."""
    ks = dict(rec.get("exposure_keys") or {})
    out = {}
    for d in DIMENSIONS:
        v = ks.get(d)
        if v is None or v == "" or v == []:
            continue
        vals = v if isinstance(v, (list, tuple)) else [v]
        vals = sorted({str(x) for x in vals if x not in (None, "")})
        if vals:
            out[d] = vals
    return out


def correlated_exposure(keys: dict, portfolio: dict, cfg) -> tuple:
    """(weighted correlated exposure USD, per-dimension detail, dimensions
    whose key is unknown). Exposure read from the portfolio's {dim: {key:
    usd}} (position / ledger truth plus earlier approvals of the batch)."""
    exp = (portfolio or {}).get("exposure") or {}
    total, detail, missing = 0.0, {}, []
    for d in DIMENSIONS:
        w = cfg.weight(d)
        if d not in keys:
            missing.append(d)
            continue
        held = sum(num((exp.get(d) or {}).get(k)) or 0.0 for k in keys[d])
        detail[d] = {"keys": keys[d], "exposure_usd": round(held, 6),
                     "weight": w, "weighted_usd": round(w * held, 6)}
        total += w * held
    return total, detail, missing


# ─────────────────────────── one candidate ─────────────────────────────

def _fill_factor(c: dict):
    eff = num(c.get("expected_fill_fraction"))
    if eff is not None and 0.0 <= eff <= 1.0:
        return eff, "EXPECTED_FILL_FRACTION (E[filled qty] / requested; " \
                    "zero-fill mass included)"
    fp = num(c.get("fill_probability"))
    if fp is not None and 0.0 <= fp <= 1.0:
        return fp, "P(FILL) from %s (the conditional EV's fill factor)" % (
            c.get("fill_probability_source") or "the snapshot")
    return None, None


def _capacity_usd(c: dict):
    """The SELECTED execution style's capacity (red-team item 11), with a
    measured zero kept as zero (item 12: None and 0 are different)."""
    style = c.get("execution_style")
    by = c.get("capacity_usd_by_style") or {}
    if style and style in by:
        v = num(by.get(style))
        if v is not None:
            return max(0.0, v), "CAPACITY_OF_THE_SELECTED_STYLE_%s" % style
        return None, "CAPACITY_OF_THE_SELECTED_STYLE_%s_UNMEASURED" % style
    if style and by:
        return None, ("ONLY_%s_CAPACITY_MEASURED_SELECTED_STYLE_IS_%s"
                      % ("/".join(sorted(by)), style))
    v = num(c.get("capacity_usd"))
    if v is not None:
        return max(0.0, v), "CAPACITY_USD (style not stated)"
    return None, "NO_EXECUTABLE_CAPACITY_MEASURED"


def score_candidate(c: dict, *, clock: float, portfolio: dict,
                    cfg: RankerConfig = DEFAULT_CONFIG) -> dict:
    """Every component, the priority and the gates of ONE candidate. Pure;
    reads only `c`, the portfolio and the config (waiting keys ignored)."""
    p = cfg.precision
    gates: list = []
    # ── the upstream gates (Derek / Karen / Eddie hard rule ...) ──────────
    g = c.get("gates") or {}
    if g.get("cleared") is True:
        pass
    elif g.get("cleared") is False:
        gates.append(G_UPSTREAM + ":" + ",".join(
            sorted(str(x) for x in (g.get("failures") or ["UNSTATED"]))))
    else:
        gates.append(G_UPSTREAM_UNKNOWN)
    rec_at = num(c.get("inputs_recorded_at"))
    if rec_at is None:
        rec_at = num(c.get("decided_at"))
    if rec_at is None or rec_at > clock + 1e-9:
        gates.append(G_FUTURE if rec_at is not None
                     else G_FUTURE + ":NO_RECORDED_TIMESTAMP")
    # ── RAW_EV / NET_EV ─────────────────────────────────────────────────
    raw = num(c.get("raw_ev_usd"))
    net_cond = num(c.get("net_ev_conditional_usd"))
    ff, ff_basis = _fill_factor(c)
    if net_cond is None:
        net, net_why = None, "NO_CONDITIONAL_EXECUTABLE_EV"
    elif ff is None:
        net, net_why = None, "NO_FILL_PROBABILITY_OR_FILL_FRACTION"
    else:
        net, net_why = net_cond * ff, None
    # ── CAPITAL EFFICIENCY ───────────────────────────────────────────────
    size = num(c.get("size_usd"))
    hours = num(c.get("expected_hours_to_release"))
    ch = size * hours if (size is not None and hours is not None
                          and size > 0 and hours > 0) else None
    if net is None:
        ce, ce_why = None, net_why
    elif ch is None:
        ce, ce_why = None, ("NO_REQUESTED_SIZE" if size is None or size <= 0
                            else "NO_EXPECTED_HOURS_TO_CAPITAL_RELEASE"
                            if hours is None else "NON_POSITIVE_HOLD")
    else:
        ce, ce_why = net / ch, None
    # ── CONFIDENCE ──────────────────────────────────────────────────────
    prob = num(c.get("probability"))
    unit = num(c.get("unit_cost"))
    e = num(c.get("net_edge_per_contract"))
    if e is None and prob is not None and unit is not None:
        e = prob - unit
    u = num(c.get("probability_half_width"))
    if e is None:
        surv = (None, "NO_NET_EDGE_PER_CONTRACT")
    elif e <= 0:
        surv = (0.0, None)
    elif u is None:
        surv = (None, "PROBABILITY_UNCERTAINTY_UNMEASURED")
    else:
        surv = ((e - max(0.0, u)) / e, None)
    rel = num(c.get("agent_reliability"))
    conf = _combine("CONFIDENCE", {
        "probability_band_survival": _sub(surv[0], surv[1], cfg),
        "agent_reliability": _sub(
            None if rel is None or not 0.0 <= rel <= 1.0 else rel,
            c.get("reliability_why") or "AGENT_RELIABILITY_NOT_SCORED",
            cfg)},
        "(edge - probability half-width) / edge x the originating "
        "agent/model reliability lower bound",
        {"net_edge_per_contract": _r(e, p), "probability_half_width": u,
         "agent_reliability": rel,
         "reliability_basis": c.get("reliability_basis")}, cfg)
    # ── EXECUTION ───────────────────────────────────────────────────────
    sd = num(c.get("execution_cost_sd_usd"))
    if net_cond is None or net_cond <= 0:
        es = (0.0 if net_cond is not None else None,
              "NO_CONDITIONAL_EXECUTABLE_EV")
    elif sd is None:
        es = (None, "EXECUTION_COST_UNCERTAINTY_UNMEASURED")
    else:
        es = ((net_cond - max(0.0, sd)) / net_cond, None)
    execution = _combine("EXECUTION", {
        "execution_cost_survival": _sub(es[0], es[1], cfg)},
        "(conditional net - execution-cost sd at size) / conditional net",
        {"net_ev_conditional_usd": net_cond, "execution_cost_sd_usd": sd,
         "expected_execution_cost_usd": num(
             c.get("expected_execution_cost_usd")),
         "fill_factor": _r(ff, p), "fill_factor_basis": ff_basis}, cfg)
    # ── LIQUIDITY ───────────────────────────────────────────────────────
    cap, cap_basis = _capacity_usd(c)
    if cap is None:
        cov = (None, cap_basis)
    elif cap <= 0:
        # red-team item 12: a MEASURED zero capacity is zero -- nothing can
        # be bought at a positive edge in the selected style
        cov = (0.0, None)
        gates.append(G_ZERO_CAPACITY)
    elif size is None or size <= 0:
        cov = (None, "NO_REQUESTED_SIZE")
    else:
        cov = (min(1.0, cap / size), None)
    spread = num(c.get("spread"))
    if spread is None:
        sp = (None, "SPREAD_NOT_RECORDED")
    elif e is None:
        sp = (None, "NO_NET_EDGE_PER_CONTRACT")
    elif e <= 0:
        sp = (0.0, None)
    else:
        sp = (e / (e + max(0.0, spread) / 2.0), None)
    liquidity = _combine("LIQUIDITY", {
        "capacity_coverage": _sub(cov[0], cov[1], cfg),
        "half_spread_exit_survival": _sub(sp[0], sp[1], cfg)},
        "min(1, selected-style capacity / requested size) x edge / (edge "
        "+ spread / 2)",
        {"capacity_usd": cap, "capacity_basis": cap_basis,
         "execution_style": c.get("execution_style"), "size_usd": size,
         "spread": spread}, cfg)
    # ── RISK (+ the freshness GATE) ──────────────────────────────────────
    age = num(c.get("probability_age_s"))
    limit = num(c.get("probability_max_age_s"))
    if age is None or limit is None:
        gates.append(G_FRESHNESS_UNKNOWN)
    elif age > limit:
        gates.append(G_STALE)
    hl = num(c.get("edge_half_life_s"))
    ttf = num(c.get("expected_time_to_fill_s"))
    if hl is None or hl <= 0:
        dec = (None, "EDGE_DECAY_HALF_LIFE_UNMEASURED")
    elif age is None:
        dec = (None, "PROBABILITY_AGE_UNKNOWN")
    else:
        dec = (0.5 ** ((max(0.0, age) + max(0.0, ttf or 0.0)) / hl), None)
    sx = num(c.get("settlement_exception_upper"))
    risk = _combine("RISK", {
        "edge_decay_survival": _sub(dec[0], dec[1], cfg),
        "settlement_certainty": _sub(
            None if sx is None or not 0.0 <= sx <= 1.0 else 1.0 - sx,
            "SETTLEMENT_EXCEPTION_RATE_UNMEASURED", cfg)},
        "0.5 ^ ((probability age + expected time to fill) / edge half-life)"
        " x (1 - settlement-exception upper bound)",
        {"probability_age_s": age, "probability_max_age_s": limit,
         "edge_half_life_s": hl, "expected_time_to_fill_s": ttf,
         "settlement_exception_upper": sx}, cfg)
    # ── CORRELATION ─────────────────────────────────────────────────────
    keys = keys_of(c)
    base = num((portfolio or {}).get("capital_base_usd"))
    corr_usd, corr_detail, missing = correlated_exposure(keys, portfolio,
                                                         cfg)
    if base is None or base <= 0:
        cf = (None, "NO_CAPITAL_BASE")
    else:
        cf = (base / (base + corr_usd), None)
    corr_subs = {"correlated_exposure": _sub(cf[0], cf[1], cfg)}
    unweighted_missing = [d for d in missing if cfg.weight(d) > 0]
    if unweighted_missing:
        corr_subs["unknown_groups"] = (
            cfg.unavailable_factor, UNAVAILABLE,
            "NO_KEY_FOR_" + ",".join(unweighted_missing))
    correlation = _combine("CORRELATION", corr_subs,
                           "capital base / (capital base + weighted "
                           "correlated exposure)",
                           {"capital_base_usd": base,
                            "correlated_exposure_usd": round(corr_usd, 6),
                            "by_dimension": corr_detail,
                            "dimensions_without_key": missing}, cfg)
    # ── FINAL PRIORITY ──────────────────────────────────────────────────
    tier = RANKABLE if (net is not None and ce is not None) else UNRANKABLE
    if tier == UNRANKABLE:
        gates.append(G_UNRANKABLE + ":" + (ce_why or net_why or "UNKNOWN"))
        final = None
    else:
        final = (ce * conf["value"] * execution["value"] * liquidity["value"]
                 * risk["value"] * correlation["value"])
        if net <= 0:
            gates.append(G_NON_POSITIVE_NET_EV)
        elif ce <= 0:
            gates.append(G_NON_POSITIVE_CE)
        elif final <= 0:
            gates.append(G_NON_POSITIVE_PRIORITY)
    ce_comp = _factor(_r(ce, p), status=MEASURED if ce is not None
                      else UNAVAILABLE, why=ce_why,
                      basis="NET_EV / (requested size x expected hours to "
                            "capital release)",
                      inputs={"net_ev_usd": _r(net, p), "size_usd": size,
                              "expected_hours_to_release": hours,
                              "hours_basis": c.get("hours_basis"),
                              "capital_hours": _r(ch, p)})
    strategy = c.get("strategy")
    return {
        "opportunity_id": str(c["opportunity_id"]),
        "snapshot_id": str(c["snapshot_id"]),
        "meta_decision_id": c.get("meta_decision_id"),
        "strategy": strategy,
        "strategy_class": str(c.get("strategy_class") or strategy
                              or "UNCLASSIFIED"),
        "agent": c.get("agent"),
        "decided_at": num(c.get("decided_at")),
        "tier": tier,
        "raw_ev": _r(raw, p),
        "net_ev": _r(net, p),
        "capital_efficiency_component": _r(ce, p),
        "confidence_component": conf["value"],
        "execution_component": execution["value"],
        "liquidity_component": liquidity["value"],
        "risk_component": risk["value"],
        "correlation_component": correlation["value"],
        "final_priority": _r(final, p),
        "gate_failures": sorted(set(gates)),
        "gates_passed": not gates,
        "components": {
            "NET_EV": _factor(_r(net, p), status=MEASURED if net is not None
                              else UNAVAILABLE, why=net_why,
                              basis="E[net executable $ | fill] x fill "
                                    "factor",
                              inputs={"net_ev_conditional_usd": net_cond,
                                      "fill_factor": _r(ff, p),
                                      "fill_factor_basis": ff_basis,
                                      "raw_ev_usd": raw}),
            "CAPITAL_EFFICIENCY": ce_comp, "CONFIDENCE": conf,
            "EXECUTION": execution, "LIQUIDITY": liquidity, "RISK": risk,
            "CORRELATION": correlation},
        "model_versions": dict(c.get("model_versions") or {}),
    }


# ─────────────────────────── the batch ─────────────────────────────────

def _sort_key(r: dict):
    if r["tier"] == RANKABLE:
        return (0, -r["final_priority"], -(r["net_ev"] or 0.0),
                -(r["capital_efficiency_component"] or 0.0),
                r["opportunity_id"], r["snapshot_id"])
    return (1, 0.0, 0.0, 0.0, r["opportunity_id"], r["snapshot_id"])


def admission(rows: list, admission_k, cfg: RankerConfig) -> dict:
    """THE ADMISSION SET over rows already in RANK order. Mutates each row's
    admitted / admission_basis / starvation_credit; returns the summary."""
    eligible = [r for r in rows if r["gates_passed"]]
    for r in rows:
        r["admitted"], r["starvation_credit"] = False, 0
        r["admission_basis"] = (NOT_ADMITTED_CAPACITY if r["gates_passed"]
                                else NOT_ADMITTED_GATES)
    k = None if admission_k is None else max(0, int(admission_k))
    if k is None or len(eligible) <= k:
        for r in eligible:
            r["admitted"], r["admission_basis"] = True, ADMIT_RANK
        return {"admission_k": k, "eligible": len(eligible),
                "admitted": len(eligible), "reserved_slots_max": 0,
                "quota_admitted": 0, "starved_classes": [],
                "rule": "EVERY_ELIGIBLE_CANDIDATE_FITS"}
    reserved_max = int(math.floor(k * cfg.max_reserved_share))
    top_classes = {r["strategy_class"] for r in eligible[:k]}
    best_of_class: dict = {}
    for r in eligible:                      # rank order: first seen is best
        if r["strategy_class"] in top_classes:
            continue
        best_of_class.setdefault(r["strategy_class"], []).append(r)
    starved = list(best_of_class)           # ordered by best candidate rank
    picks = []
    for cls in starved:
        for r in best_of_class[cls][:cfg.class_quota]:
            if len(picks) >= reserved_max:
                break
            picks.append(r)
        if len(picks) >= reserved_max:
            break
    for r in eligible[:k - len(picks)]:
        r["admitted"], r["admission_basis"] = True, ADMIT_RANK
    for r in picks:
        r["admitted"], r["admission_basis"] = True, ADMIT_QUOTA
        r["starvation_credit"] = 1
    return {"admission_k": k, "eligible": len(eligible),
            "admitted": k,
            "reserved_slots_max": reserved_max, "quota_admitted": len(picks),
            "starved_classes": starved,
            "quota_classes": sorted({r["strategy_class"] for r in picks}),
            "rule": ("top K eligible by RANK; up to class_quota slot(s) per "
                     "strategy class absent from the top K, at most floor(K "
                     "x max_reserved_share), served by best rank; RANK and "
                     "FINAL_PRIORITY unchanged")}


def rank_run_id(*, clock: float, cfg_sha: str, code_sha: str,
                ids: list) -> str:
    body = "|".join(["%.3f" % float(clock), cfg_sha, code_sha or ""]
                    + ["%s/%s" % x for x in sorted(ids)])
    return "cprr_" + hashlib.sha256(body.encode()).hexdigest()[:24]


def ranking_id(run_id: str, opportunity_id: str, snapshot_id: str) -> str:
    return "cpr_" + hashlib.sha256(("%s|%s|%s" % (
        run_id, opportunity_id, snapshot_id)).encode()).hexdigest()[:24]


def rank_batch(candidates: list, *, clock: float, portfolio: dict,
               admission_k=None, cfg: RankerConfig = DEFAULT_CONFIG,
               code_sha: str, source: str = "PAPER_REPLAY") -> dict:
    """RANK ONE BATCH (pure, deterministic, O(N log N)). Identical inputs in
    any order give identical output. Duplicate (opportunity_id, snapshot_id)
    pairs are refused (one row per snapshot)."""
    if source not in SOURCES:
        raise ValueError("unknown source %r" % source)
    if not code_sha:
        raise ValueError("every record carries the code sha")
    seen = set()
    rows = []
    for c in candidates:
        key = (str(c.get("opportunity_id") or ""), str(
            c.get("snapshot_id") or ""))
        if not key[0] or not key[1]:
            raise ValueError("a candidate needs opportunity_id and "
                             "snapshot_id")
        if key in seen:
            raise ValueError("duplicate candidate %s/%s" % key)
        seen.add(key)
        rows.append(score_candidate(c, clock=clock, portfolio=portfolio,
                                    cfg=cfg))
    rows.sort(key=_sort_key)
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    adm = admission(rows, admission_k, cfg)
    cfg_sha = cfg.sha
    run = rank_run_id(clock=clock, cfg_sha=cfg_sha, code_sha=code_sha,
                      ids=list(seen))
    for r in rows:
        r.update(rank_run_id=run,
                 ranking_id=ranking_id(run, r["opportunity_id"],
                                       r["snapshot_id"]),
                 as_of=float(clock), ranker_version=VERSION,
                 config_sha=cfg_sha, code_sha=code_sha, source=source,
                 authority=AUTHORITY)
    return {"rank_run_id": run, "as_of": float(clock),
            "ranker_version": VERSION, "config": cfg.to_dict(),
            "config_sha": cfg_sha, "code_sha": code_sha, "source": source,
            "authority": AUTHORITY, "admission": adm,
            "portfolio_as_of": num((portfolio or {}).get("as_of")),
            "n": len(rows),
            "n_rankable": sum(r["tier"] == RANKABLE for r in rows),
            "rows": rows}


def admission_order(run: dict) -> list:
    """The admitted rows in processing order: RANK order, quota candidates
    in their own rank position (never ahead of a better admitted one)."""
    return [r for r in run["rows"] if r["admitted"]]


def dominates(a: dict, b: dict) -> bool:
    """A strictly better than B: NET_EV, the economic base and every stored
    factor at least as good, at least one strictly better (both RANKABLE).
    For two candidates with positive priority this implies a strictly higher
    FINAL_PRIORITY, so A ranks above B whatever either waited."""
    if a["tier"] != RANKABLE or b["tier"] != RANKABLE:
        return False
    keys = ("net_ev", "capital_efficiency_component", "confidence_component",
            "execution_component", "liquidity_component", "risk_component",
            "correlation_component")
    ge = all(a[k] >= b[k] for k in keys)
    gt = any(a[k] > b[k] for k in keys)
    return ge and gt

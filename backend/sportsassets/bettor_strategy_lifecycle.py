"""PAPER TURNAROUND: THE STRATEGY LIFECYCLE AFTER LOSSES (migration 290).

PAPER ONLY. A lifecycle state is an ADDITIONAL gate on a paper strategy, on
top of the strategy allowlist and its paper_control entry switch -- never an
edit to either, never a grant. Nothing here reads or writes a per-order,
per-market or per-fixture cap, the $25 SMALL LIVE cap, the 1:1,000 scale or a
live venue; the module imports no venue, order-submission or funded module.

THE STATES (rank = how restrictive; an automatic move only ever RAISES rank):

    0 ACTIVE_CHAMPION    full size; the strategy with the best FORWARD evidence
    1 ACTIVE_CHALLENGER  full size; the initial state of every strategy (a
                         strategy with no event row is ACTIVE_CHALLENGER)
    2 REDUCED_SIZE       entries sized x REDUCED_SIZE_FACTOR at the decision,
                         and every ENTRY order's reservation capped at
                         REDUCED_SIZE_MAX_ORDER_USD at the ledger
    3 SHADOW_ONLY        decisions keep being recorded; no paper entry order
    4 QUARANTINED        no paper entry order; leaving it needs a named person
                         and goes to SHADOW_ONLY first
    5 RETIRED            terminal; no paper entry order, ever

PREDECLARED STOPPING RULES (RULES_VERSION, hashed into RULES_SHA and recorded
on every event). Evaluated over the strategy's CLOSED paper positions released
in the last WINDOW_DAYS -- every one of them, losses included; nothing is
excluded, netted against another sleeve or relabelled:

    LOSS_BUDGET_QUARANTINE   realized P&L <= -LOSS_BUDGET_QUARANTINE_USD
                             -> QUARANTINED
    DRAWDOWN_QUARANTINE      max drawdown of cumulative realized P&L
                             >= DRAWDOWN_QUARANTINE_USD -> QUARANTINED
    LOSS_BUDGET_SHADOW       realized P&L <= -LOSS_BUDGET_SHADOW_USD
                             -> SHADOW_ONLY
    LOSS_BUDGET_REDUCE       realized P&L <= -LOSS_BUDGET_REDUCE_USD
                             -> REDUCED_SIZE
    NEGATIVE_EDGE            >= MIN_CLOSED_FOR_RATE_RULES closed and the 95%
                             UPPER bound of mean P&L per position < 0
                             -> REDUCED_SIZE
    DRAWDOWN_RATE            >= MIN_CLOSED_FOR_RATE_RULES closed and max
                             drawdown / capital deployed >= DRAWDOWN_RATE_REDUCE
                             -> REDUCED_SIZE
    CHAMPION_EVIDENCE_LAPSED an ACTIVE_CHAMPION whose forward evidence no
                             longer meets PROMOTION -> ACTIVE_CHALLENGER

STALE MANAGEMENT. Separately from the state, a strategy whose
stale-management rate (bettor_stale_management, the read-only seam to the
freshness lane) exceeds STALE_MANAGEMENT_MAX_RATE over at least
STALE_MANAGEMENT_MIN_OPEN open positions is refused NEW entries, so stale
exposure cannot keep growing. Unreadable => refused.

UPWARD MOVES ARE NEVER AUTOMATIC. They name a person ("person:<name>") and
need FORWARD evidence -- closed positions whose first fill is after the later
of RULES_DECLARED_AT and the strategy's current state's own event -- never a
short-term profit alone:

    QUARANTINED -> SHADOW_ONLY        a person
    SHADOW_ONLY -> REDUCED_SIZE       a person, and no stopping rule firing
    REDUCED_SIZE -> ACTIVE_CHALLENGER a person, no rule firing, forward
                                      closed >= MIN_FORWARD_CLOSED_FOR_RESTORE
                                      and forward realized P&L >= 0
    ACTIVE_CHALLENGER -> ACTIVE_CHAMPION  a person, no rule firing, forward
                                      closed >= MIN_FORWARD_CLOSED_FOR_PROMOTION,
                                      the 95% LOWER bound of forward mean P&L
                                      per position > 0 and forward $/capital-hour
                                      > 0
    RETIRED -> anything               never

CAPITAL-HOUR REALLOCATION (advisory, paper): the capital-hours a demoted
strategy no longer uses go only to ACTIVE strategies whose FORWARD evidence
meets PROMOTION and beats the donor's; with no such recipient they stay CASH.
The plan changes no cap: each recipient keeps its unchanged per-order and
per-market limits; there is one shared simulated cash ledger.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any

VERSION = "PAPER_STRATEGY_LIFECYCLE_V1"
RULES_VERSION = "PAPER_TURNAROUND_RULES_V1"

ACTIVE_CHAMPION = "ACTIVE_CHAMPION"
ACTIVE_CHALLENGER = "ACTIVE_CHALLENGER"
REDUCED_SIZE = "REDUCED_SIZE"
SHADOW_ONLY = "SHADOW_ONLY"
QUARANTINED = "QUARANTINED"
RETIRED = "RETIRED"
STATES = (ACTIVE_CHAMPION, ACTIVE_CHALLENGER, REDUCED_SIZE, SHADOW_ONLY,
          QUARANTINED, RETIRED)
RANK = {s: i for i, s in enumerate(STATES)}
ACTIVE_STATES = (ACTIVE_CHAMPION, ACTIVE_CHALLENGER)
NO_ENTRY_STATES = (SHADOW_ONLY, QUARANTINED, RETIRED)
INITIAL_STATE = ACTIVE_CHALLENGER

AUTOMATIC_ACTOR = "AUTOMATIC_RULE_EVALUATOR"

# ── THE PREDECLARED CONSTANTS (paper dollars; entries average ~$1,000) ──
RULES_DECLARED_AT = 1_791_158_400.0          # 2026-10-05T00:00:00Z
WINDOW_DAYS = 14.0
MIN_CLOSED_FOR_RATE_RULES = 20
LOSS_BUDGET_REDUCE_USD = 2_500.0
LOSS_BUDGET_SHADOW_USD = 5_000.0
LOSS_BUDGET_QUARANTINE_USD = 10_000.0
DRAWDOWN_QUARANTINE_USD = 12_500.0
DRAWDOWN_RATE_REDUCE = 0.10
CI_Z = 1.96
REDUCED_SIZE_FACTOR = 0.5
REDUCED_SIZE_MAX_ORDER_USD = 500.0
STALE_MANAGEMENT_MAX_RATE = 0.5
STALE_MANAGEMENT_MIN_OPEN = 5
MIN_FORWARD_CLOSED_FOR_RESTORE = 20
MIN_FORWARD_CLOSED_FOR_PROMOTION = 30
DEMOTION_COMPARE_DAYS = 7.0
RUN_EVERY_S = 600.0

# A RULE THAT COULD RAISE A SIZE CANNOT BE DECLARED. Checked at import.
assert 0.0 < REDUCED_SIZE_FACTOR <= 1.0
assert REDUCED_SIZE_MAX_ORDER_USD > 0
assert LOSS_BUDGET_REDUCE_USD < LOSS_BUDGET_SHADOW_USD \
    < LOSS_BUDGET_QUARANTINE_USD

RULES = {
    "version": RULES_VERSION,
    "declared_at": RULES_DECLARED_AT,
    "window_days": WINDOW_DAYS,
    "min_closed_for_rate_rules": MIN_CLOSED_FOR_RATE_RULES,
    "loss_budget_reduce_usd": LOSS_BUDGET_REDUCE_USD,
    "loss_budget_shadow_usd": LOSS_BUDGET_SHADOW_USD,
    "loss_budget_quarantine_usd": LOSS_BUDGET_QUARANTINE_USD,
    "drawdown_quarantine_usd": DRAWDOWN_QUARANTINE_USD,
    "drawdown_rate_reduce": DRAWDOWN_RATE_REDUCE,
    "ci_z": CI_Z,
    "reduced_size_factor": REDUCED_SIZE_FACTOR,
    "reduced_size_max_order_usd": REDUCED_SIZE_MAX_ORDER_USD,
    "stale_management_max_rate": STALE_MANAGEMENT_MAX_RATE,
    "stale_management_min_open": STALE_MANAGEMENT_MIN_OPEN,
    "min_forward_closed_for_restore": MIN_FORWARD_CLOSED_FOR_RESTORE,
    "min_forward_closed_for_promotion": MIN_FORWARD_CLOSED_FOR_PROMOTION,
    "demotion_compare_days": DEMOTION_COMPARE_DAYS,
}
RULES_SHA = hashlib.sha256(json.dumps(RULES, sort_keys=True).encode()
                           ).hexdigest()

RULE_LOSS_QUARANTINE = "LOSS_BUDGET_QUARANTINE"
RULE_DRAWDOWN_QUARANTINE = "DRAWDOWN_QUARANTINE"
RULE_LOSS_SHADOW = "LOSS_BUDGET_SHADOW"
RULE_LOSS_REDUCE = "LOSS_BUDGET_REDUCE"
RULE_NEGATIVE_EDGE = "NEGATIVE_EDGE"
RULE_DRAWDOWN_RATE = "DRAWDOWN_RATE"
RULE_CHAMPION_LAPSED = "CHAMPION_EVIDENCE_LAPSED"
RULE_MANUAL = "MANUAL_TRANSITION_BY_A_NAMED_PERSON"

#: the paper strategies (pinned equal to bettor_paper_ops.STRATEGY_ORDER by a
#: test; stated here so this module imports no paper-ops module)
KNOWN_STRATEGIES = ("DEREK_ENTRY_POLICY_V2", "PINNACLE_ONLY_PAPER_BENCHMARK",
                    "PINNACLE_COMPLETED_GAME_PAPER",
                    "PINNACLE_COMPLETED_GAME_MAKER_PAPER",
                    "PINNACLE_EXPLORATION_PAPER")

R_LIFECYCLE_SHADOW_ONLY = "STRATEGY_LIFECYCLE_SHADOW_ONLY_NO_PAPER_ENTRY"
R_LIFECYCLE_QUARANTINED = "STRATEGY_LIFECYCLE_QUARANTINED_NO_PAPER_ENTRY"
R_LIFECYCLE_RETIRED = "STRATEGY_LIFECYCLE_RETIRED_NO_PAPER_ENTRY"
R_LIFECYCLE_UNREADABLE = "STRATEGY_LIFECYCLE_STATE_UNREADABLE"
R_LIFECYCLE_REDUCED_BELOW_ONE = "STRATEGY_LIFECYCLE_REDUCED_SIZE_BELOW_ONE_CONTRACT"
R_STALE_MANAGEMENT_EXCEEDED = "STALE_MANAGEMENT_RATE_ABOVE_THE_DECLARED_THRESHOLD"
R_STALE_MANAGEMENT_UNREADABLE = "STALE_MANAGEMENT_RATE_UNREADABLE"
R_TRANSITION_UNKNOWN_STATE = "LIFECYCLE_TRANSITION_UNKNOWN_STATE"
R_TRANSITION_NO_CHANGE = "LIFECYCLE_TRANSITION_IS_NOT_A_CHANGE"
R_TRANSITION_NEEDS_PERSON = "LIFECYCLE_UPWARD_TRANSITION_NEEDS_A_NAMED_PERSON"
R_TRANSITION_RETIRED_TERMINAL = "LIFECYCLE_RETIRED_IS_TERMINAL"
R_TRANSITION_NOT_ONE_STEP = "LIFECYCLE_UPWARD_TRANSITION_IS_NOT_THE_DECLARED_STEP"
R_TRANSITION_RULE_FIRING = "LIFECYCLE_A_STOPPING_RULE_IS_STILL_FIRING"
R_TRANSITION_NO_FORWARD_EVIDENCE = "LIFECYCLE_FORWARD_EVIDENCE_INSUFFICIENT"

ENTRY_STATE_REFUSAL = {SHADOW_ONLY: R_LIFECYCLE_SHADOW_ONLY,
                       QUARANTINED: R_LIFECYCLE_QUARANTINED,
                       RETIRED: R_LIFECYCLE_RETIRED}

#: the one upward step each state may take (by a named person)
UPWARD_STEP = {QUARANTINED: SHADOW_ONLY, SHADOW_ONLY: REDUCED_SIZE,
               REDUCED_SIZE: ACTIVE_CHALLENGER,
               ACTIVE_CHALLENGER: ACTIVE_CHAMPION}


def is_tightening(from_state: str, to_state: str) -> bool:
    return RANK[to_state] > RANK[from_state]


# ═════════════════════════════════════════════════════════════════════
# THE METRICS (pure)
# ═════════════════════════════════════════════════════════════════════

def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def closed_at(p: dict) -> float | None:
    """When a position closed: its settlement instant, else (fully sold) its
    last fill. None while it is open."""
    if (_num(p.get("open_qty")) or 0.0) > 1e-9:
        return None
    st = p.get("settlement") or {}
    return _num(st.get("settled_at")) or _num(p.get("last_fill_at"))


def strategy_of(p: dict, default: str = "DEREK_ENTRY_POLICY_V2") -> str:
    return p.get("strategy") or default


def metrics(positions: list, *, now: float, window_days: float = WINDOW_DAYS,
            since: float | None = None) -> dict:
    """ONE STRATEGY'S ROLLING FIGURES from its paper positions (the ledger's
    `positions(include_closed=True)` rows). Every closed position in the
    window counts; losses are never excluded. A figure without a basis is
    None (UNAVAILABLE), never zero. `since` restricts to positions first
    filled at or after it (the FORWARD sample)."""
    lo = float(now) - window_days * 86400.0
    rows = []
    open_rows = []
    for p in positions or []:
        ff = _num(p.get("first_fill_at"))
        if since is not None and (ff is None or ff < since):
            continue
        ca = closed_at(p)
        if ca is None:
            open_rows.append(p)
            continue
        if ca < lo or ca > float(now):
            continue
        rows.append((ca, p))
    rows.sort(key=lambda x: x[0])
    pnls = [(_num(p.get("realized_pnl_usd")) or 0.0) for _, p in rows]
    cost = sum((_num(p.get("acquisition_cost_usd")) or 0.0) for _, p in rows)
    fees = sum((_num(p.get("buy_fees_usd")) or 0.0)
               + (_num(p.get("sale_fees_usd")) or 0.0) for _, p in rows)
    ch = 0.0
    for ca, p in rows:
        ff = _num(p.get("first_fill_at")) or ca
        ch += (_num(p.get("acquisition_cost_usd")) or 0.0) * max(
            0.0, ca - ff) / 3600.0
    peak, cum, mdd = 0.0, 0.0, 0.0
    for x in pnls:
        cum += x
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    n = len(pnls)
    mean = (sum(pnls) / n) if n else None
    sd = (math.sqrt(sum((x - mean) ** 2 for x in pnls) / (n - 1))
          if n >= 2 else None)
    half = (CI_Z * sd / math.sqrt(n)) if sd is not None else None
    realized = sum(pnls) if n else None
    return {
        "window_days": window_days, "window_start": lo, "as_of": float(now),
        "forward_since": since,
        "closed_positions": n,
        "realized_pnl_usd": None if realized is None else round(realized, 6),
        "capital_deployed_usd": round(cost, 6) if n else None,
        "capital_hours": round(ch, 6) if n else None,
        "dollars_per_capital_hour": (round(realized / ch, 9)
                                     if n and ch > 0 else None),
        "max_drawdown_usd": round(mdd, 6) if n else None,
        "drawdown_rate": (round(mdd / cost, 9) if n and cost > 0 else None),
        "drawdown_rate_is": "max drawdown / capital deployed in the window",
        "execution_cost_usd": round(fees, 6) if n else None,
        "execution_cost_rate": (round(fees / cost, 9)
                                if n and cost > 0 else None),
        "execution_cost_is": "fees paid / capital deployed (slippage is "
                             "inside the fill prices)",
        "mean_pnl_per_position_usd": None if mean is None else round(mean, 6),
        "pnl_ci95_low": None if half is None else round(mean - half, 6),
        "pnl_ci95_high": None if half is None else round(mean + half, 6),
        "wins": sum(1 for x in pnls if x > 0),
        "losses": sum(1 for x in pnls if x < 0),
        "open_positions": len(open_rows),
        "open_cost_basis_usd": round(sum(
            (_num(p.get("cost_basis_usd")) or 0.0) for p in open_rows), 6),
        "losses_excluded": 0,
    }


def rules_firing(m: dict, *, current: str = INITIAL_STATE,
                 forward: dict | None = None) -> list:
    """Every PREDECLARED rule this evidence fires, most severe first, each
    with the numbers it fired on. Pure."""
    out = []
    pnl, mdd = m.get("realized_pnl_usd"), m.get("max_drawdown_usd")
    n = int(m.get("closed_positions") or 0)
    if pnl is not None and pnl <= -LOSS_BUDGET_QUARANTINE_USD:
        out.append((RULE_LOSS_QUARANTINE, QUARANTINED,
                    {"realized_pnl_usd": pnl,
                     "budget_usd": -LOSS_BUDGET_QUARANTINE_USD}))
    if mdd is not None and mdd >= DRAWDOWN_QUARANTINE_USD:
        out.append((RULE_DRAWDOWN_QUARANTINE, QUARANTINED,
                    {"max_drawdown_usd": mdd,
                     "limit_usd": DRAWDOWN_QUARANTINE_USD}))
    if pnl is not None and pnl <= -LOSS_BUDGET_SHADOW_USD:
        out.append((RULE_LOSS_SHADOW, SHADOW_ONLY,
                    {"realized_pnl_usd": pnl,
                     "budget_usd": -LOSS_BUDGET_SHADOW_USD}))
    if pnl is not None and pnl <= -LOSS_BUDGET_REDUCE_USD:
        out.append((RULE_LOSS_REDUCE, REDUCED_SIZE,
                    {"realized_pnl_usd": pnl,
                     "budget_usd": -LOSS_BUDGET_REDUCE_USD}))
    hi = m.get("pnl_ci95_high")
    if n >= MIN_CLOSED_FOR_RATE_RULES and hi is not None and hi < 0:
        out.append((RULE_NEGATIVE_EDGE, REDUCED_SIZE,
                    {"closed_positions": n, "pnl_ci95_high": hi}))
    dr = m.get("drawdown_rate")
    if n >= MIN_CLOSED_FOR_RATE_RULES and dr is not None \
            and dr >= DRAWDOWN_RATE_REDUCE:
        out.append((RULE_DRAWDOWN_RATE, REDUCED_SIZE,
                    {"closed_positions": n, "drawdown_rate": dr,
                     "limit": DRAWDOWN_RATE_REDUCE}))
    if current == ACTIVE_CHAMPION and forward is not None \
            and not promotion_evidence(forward)["ok"]:
        out.append((RULE_CHAMPION_LAPSED, ACTIVE_CHALLENGER,
                    {"forward": _brief(forward)}))
    out.sort(key=lambda r: -RANK[r[1]])
    return [{"rule_id": r, "requires": s, "evidence": e} for r, s, e in out]


def _brief(m: dict) -> dict:
    return {k: m.get(k) for k in (
        "closed_positions", "realized_pnl_usd", "dollars_per_capital_hour",
        "pnl_ci95_low", "pnl_ci95_high", "max_drawdown_usd",
        "drawdown_rate", "forward_since")}


def promotion_evidence(forward: dict) -> dict:
    n = int(forward.get("closed_positions") or 0)
    lo, rate = forward.get("pnl_ci95_low"), forward.get(
        "dollars_per_capital_hour")
    why = []
    if n < MIN_FORWARD_CLOSED_FOR_PROMOTION:
        why.append("forward closed %d < %d" % (
            n, MIN_FORWARD_CLOSED_FOR_PROMOTION))
    if lo is None or lo <= 0:
        why.append("forward 95%% lower bound of mean P&L %s is not > 0" % lo)
    if rate is None or rate <= 0:
        why.append("forward $/capital-hour %s is not > 0" % rate)
    return {"ok": not why, "why": why}


def restore_evidence(forward: dict) -> dict:
    n = int(forward.get("closed_positions") or 0)
    pnl = forward.get("realized_pnl_usd")
    why = []
    if n < MIN_FORWARD_CLOSED_FOR_RESTORE:
        why.append("forward closed %d < %d" % (n,
                                               MIN_FORWARD_CLOSED_FOR_RESTORE))
    if pnl is None or pnl < 0:
        why.append("forward realized P&L %s is negative or unavailable" % pnl)
    return {"ok": not why, "why": why}


def automatic_transition(current: str, m: dict, *,
                         forward: dict | None = None) -> dict | None:
    """The ONE automatic move the rules require from `current`, or None. Only
    ever a tightening (rank strictly up); RETIRED never moves. Pure."""
    if current not in RANK or current == RETIRED:
        return None
    fired = rules_firing(m, current=current, forward=forward)
    if not fired:
        return None
    top = fired[0]
    if RANK[top["requires"]] <= RANK[current]:
        return None
    return {"from_state": current, "to_state": top["requires"],
            "rule_id": top["rule_id"], "actor": AUTOMATIC_ACTOR,
            "rules_firing": fired}


def check_manual(current: str, to_state: str, *, actor: str, m: dict,
                 forward: dict) -> str | None:
    """None if a person may move `current` -> `to_state` now, else the named
    refusal. Tightening: any named person, any time. Upward: the declared one
    step, a named person, no rule firing, and the step's forward evidence."""
    if to_state not in RANK or current not in RANK:
        return R_TRANSITION_UNKNOWN_STATE
    if to_state == current:
        return R_TRANSITION_NO_CHANGE
    if current == RETIRED:
        return R_TRANSITION_RETIRED_TERMINAL
    if not (isinstance(actor, str) and actor.startswith("person:")
            and len(actor) > len("person:")):
        return R_TRANSITION_NEEDS_PERSON
    if is_tightening(current, to_state):
        return None
    if UPWARD_STEP.get(current) != to_state:
        return R_TRANSITION_NOT_ONE_STEP
    if current == QUARANTINED:
        return None
    # the STOPPING rules (the forward-evidence bars are checked below)
    if any(RANK[r["requires"]] > RANK[to_state]
           for r in rules_firing(m, current=to_state)):
        return R_TRANSITION_RULE_FIRING
    if to_state == ACTIVE_CHALLENGER and not restore_evidence(forward)["ok"]:
        return R_TRANSITION_NO_FORWARD_EVIDENCE
    if to_state == ACTIVE_CHAMPION and not promotion_evidence(forward)["ok"]:
        return R_TRANSITION_NO_FORWARD_EVIDENCE
    return None


# ═════════════════════════════════════════════════════════════════════
# SIZE: ONLY EVER SMALLER (pure)
# ═════════════════════════════════════════════════════════════════════

def decision_size_factor(state: str) -> float:
    """The factor a DECISION applies to its intended size: 1 when ACTIVE,
    REDUCED_SIZE_FACTOR when REDUCED_SIZE, 0 when no entry is allowed. Never
    above 1."""
    if state in ACTIVE_STATES:
        return 1.0
    if state == REDUCED_SIZE:
        return REDUCED_SIZE_FACTOR
    return 0.0


def ledger_qty_cap(state: str, *, qty: float, limit: float,
                   max_fee_per_contract: float = 0.0) -> float:
    """The most an ENTRY order of this state may reserve, as whole contracts:
    `qty` unchanged when ACTIVE; for REDUCED_SIZE at most
    REDUCED_SIZE_MAX_ORDER_USD / (limit + max fee) contracts; 0 otherwise.
    min(...) with the order's own qty, so it can never raise it."""
    q = max(0.0, float(qty))
    if state in ACTIVE_STATES:
        return q
    if state == REDUCED_SIZE:
        per = float(limit) + max(0.0, float(max_fee_per_contract))
        cap = math.floor(REDUCED_SIZE_MAX_ORDER_USD / per) if per > 0 else 0
        return float(min(q, cap))
    return 0.0


# ═════════════════════════════════════════════════════════════════════
# THE DATABASE (migration 290)
# ═════════════════════════════════════════════════════════════════════

TABLE = "paper_strategy_lifecycle_events"


async def schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT to_regclass('paper_strategy_lifecycle_events') "
            "IS NOT NULL"))
    except Exception:                                           # noqa: BLE001
        return False


async def current_state(conn, account_id: str, strategy: str) -> dict:
    """Read the latest local state, or inherit it through registered epochs.

    A new cash account is not strategy recovery. Until an explicit transition
    on that account, source quarantine/retirement/size restrictions still apply.
    Missing schema, failed reads or malformed ancestry refuse admission.
    """
    if not await schema(conn):
        return {"ok": False, "state": None, "why": "MIGRATION_290_NOT_APPLIED"}
    from . import bettor_paper_ledger as L
    source = account_id
    seen = set()
    try:
        epoch_schema = None
        for _ in range(64):
            if source in seen:
                return {"ok": False, "state": None, "why": "EPOCH_POLICY_ANCESTRY_CYCLE"}
            seen.add(source)
            r = await conn.fetchrow(
                "SELECT event_id, to_state, rule_id, recorded_at FROM "
                " paper_strategy_lifecycle_events WHERE account_id = $1 "
                "   AND strategy = $2 ORDER BY event_id DESC LIMIT 1",
                source, strategy)
            if r is not None:
                out = {"ok": True, "state": r["to_state"], "event_id": r["event_id"],
                       "rule_id": r["rule_id"],
                       "since": float(r["recorded_at"].timestamp())}
                if source != account_id:
                    out.update(basis="REGISTERED_EPOCH_INHERITED_STATE",
                               inherited_from_account_id=source)
                return out
            if source == L.ACCOUNT_ID:
                break
            if epoch_schema is None:
                epoch_schema = bool(await conn.fetchval(
                    "SELECT to_regclass('public.paper_account_epochs')"))
            if not epoch_schema:
                break
            parent = await conn.fetchval(
                "SELECT previous_account_id FROM paper_account_epochs WHERE account_id=$1",
                source)
            if parent is None:
                break
            if not isinstance(parent, str) or not parent:
                return {"ok": False, "state": None, "why": "EPOCH_POLICY_ANCESTRY_INVALID"}
            source = parent
        else:
            return {"ok": False, "state": None, "why": "EPOCH_POLICY_ANCESTRY_TOO_DEEP"}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "state": None, "why": type(exc).__name__}
    return {"ok": True, "state": INITIAL_STATE, "since": None,
            "event_id": None, "rule_id": None,
            "basis": "NO_EVENT_ROW_INITIAL_STATE"}


async def record(conn, *, account_id: str, strategy: str, from_state,
                 to_state: str, rule_id: str, actor: str, evidence: dict,
                 why: str, at: float) -> int:
    """APPEND one transition (the table refuses UPDATE / DELETE). `at` is the
    evaluation instant the evidence was computed at."""
    from . import bettor_paper_ledger as L
    if to_state not in RANK or (from_state is not None
                                and from_state not in RANK):
        raise ValueError(R_TRANSITION_UNKNOWN_STATE)
    if actor == AUTOMATIC_ACTOR and from_state is not None \
            and not is_tightening(from_state, to_state):
        # THE EVALUATOR CAN ONLY TIGHTEN, enforced at the write as well.
        raise ValueError(R_TRANSITION_NEEDS_PERSON)
    return await conn.fetchval(
        "INSERT INTO paper_strategy_lifecycle_events (account_id, strategy, "
        " from_state, to_state, rule_id, rules_version, rules_sha, actor, "
        " evidence, why, recorded_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,"
        " $9::jsonb,$10,$11) RETURNING event_id", account_id, strategy,
        from_state, to_state, rule_id, RULES_VERSION, RULES_SHA, actor,
        json.dumps(dict(evidence, rules=RULES), default=str), why,
        L._ts(float(at)))


async def transition(conn, *, account_id: str, strategy: str, to_state: str,
                     actor: str, why: str, now: float | None = None) -> dict:
    """A NAMED PERSON'S transition, checked against the declared graph and
    the forward evidence, recorded with that evidence. Never raises past a
    named refusal for a disallowed move."""
    at = float(now if now is not None else time.time())
    cur = await current_state(conn, account_id, strategy)
    if not cur["ok"]:
        return {"ok": False, "refusal": R_LIFECYCLE_UNREADABLE,
                "why": cur.get("why")}
    pos = await _positions(conn, account_id, strategy)
    m = metrics(pos, now=at)
    fwd = metrics(pos, now=at, window_days=3650.0,
                  since=forward_since(cur.get("since")))
    refusal = check_manual(cur["state"], to_state, actor=actor, m=m,
                           forward=fwd)
    if refusal:
        return {"ok": False, "refusal": refusal, "from_state": cur["state"],
                "to_state": to_state, "rolling": _brief(m),
                "forward": _brief(fwd)}
    eid = await record(conn, account_id=account_id, strategy=strategy,
                       from_state=cur["state"], to_state=to_state,
                       rule_id=RULE_MANUAL, actor=actor,
                       evidence={"rolling": m, "forward": fwd,
                                 "rules_firing": rules_firing(
                                     m, current=to_state, forward=fwd)},
                       why=why, at=at)
    return {"ok": True, "event_id": eid, "from_state": cur["state"],
            "to_state": to_state}


def forward_since(state_since) -> float:
    return max(RULES_DECLARED_AT, float(state_since or 0.0))


async def _positions(conn, account_id: str, strategy: str | None = None
                     ) -> list:
    from . import bettor_paper_ledger as L
    rows = await L.lineage_positions(conn, account_id)
    if strategy is None:
        return rows
    return [p for p in rows if strategy_of(p, L.DEFAULT_STRATEGY) == strategy]


# ═════════════════════════════════════════════════════════════════════
# THE ENTRY GATE (read under the paper account lock by submit_order)
# ═════════════════════════════════════════════════════════════════════

async def entry_gate(conn, *, account_id: str, strategy: str, qty, limit,
                     at: float, max_fee_per_contract: float = 0.0) -> dict:
    """For one ENTRY BUY: {refusal} or {qty (<= the order's), state, stale}.
    Fail-closed: an unreadable state or stale rate refuses."""
    cur = await current_state(conn, account_id, strategy)
    if not cur["ok"]:
        return {"refusal": R_LIFECYCLE_UNREADABLE, "why": cur.get("why")}
    state = cur["state"]
    if state in ENTRY_STATE_REFUSAL:
        return {"refusal": ENTRY_STATE_REFUSAL[state], "state": state,
                "lifecycle_event_id": cur.get("event_id")}
    from . import bettor_stale_management as SM
    st = await SM.stale_management_rate(conn, account_id, strategy, now=at)
    if not st.get("ok"):
        return {"refusal": R_STALE_MANAGEMENT_UNREADABLE, "state": state,
                "stale_management": st}
    if (st.get("open_positions") or 0) >= STALE_MANAGEMENT_MIN_OPEN and \
            st.get("rate") is not None and \
            st["rate"] > STALE_MANAGEMENT_MAX_RATE:
        return {"refusal": R_STALE_MANAGEMENT_EXCEEDED, "state": state,
                "stale_management": st,
                "threshold": STALE_MANAGEMENT_MAX_RATE}
    q = ledger_qty_cap(state, qty=float(qty), limit=float(limit),
                       max_fee_per_contract=max_fee_per_contract)
    q = math.floor(q + 1e-9) if q < float(qty) else float(qty)
    if q < 1:
        return {"refusal": R_LIFECYCLE_REDUCED_BELOW_ONE, "state": state}
    return {"refusal": None, "state": state, "qty": q,
            "capped": q < float(qty), "stale_management": {
                k: st.get(k) for k in ("open_positions", "stale",
                                       "unavailable", "rate")},
            "lifecycle_event_id": cur.get("event_id")}


async def decision_gate(conn, *, account_id: str, strategy: str,
                        at: float) -> dict:
    """The DECISION-level read (paper_derek / paper_benchmark): the state, its
    size factor and the refusal a no-entry state carries. Fail-closed."""
    cur = await current_state(conn, account_id, strategy)
    if not cur["ok"]:
        return {"ok": False, "refusal": R_LIFECYCLE_UNREADABLE,
                "size_factor": 0.0, "why": cur.get("why")}
    s = cur["state"]
    return {"ok": True, "state": s, "size_factor": decision_size_factor(s),
            "refusal": ENTRY_STATE_REFUSAL.get(s),
            "lifecycle_event_id": cur.get("event_id")}


# ═════════════════════════════════════════════════════════════════════
# THE AUTOMATIC EVALUATION (paper pass step; main account)
# ═════════════════════════════════════════════════════════════════════

_LAST_RUN: dict = {}


async def evaluate_and_record(conn, *, account_id: str, now: float,
                              strategies=None) -> dict:
    """Apply the PREDECLARED rules to every strategy on the account and
    record each REQUIRED tightening (only those) with its evidence."""
    out: dict[str, Any] = {"account_id": account_id, "at": now,
                           "transitions": [], "strategies": {}}
    if not await schema(conn):
        return dict(out, refusal="MIGRATION_290_NOT_APPLIED")
    from . import bettor_paper_ledger as L
    allpos = await L.lineage_positions(conn, account_id)
    names = set(strategies or KNOWN_STRATEGIES) | {
        strategy_of(p, L.DEFAULT_STRATEGY) for p in allpos}
    for s in sorted(names):
        pos = [p for p in allpos if strategy_of(p, L.DEFAULT_STRATEGY) == s]
        cur = await current_state(conn, account_id, s)
        if not cur["ok"]:
            out["strategies"][s] = {"refusal": R_LIFECYCLE_UNREADABLE}
            continue
        m = metrics(pos, now=now)
        fwd = metrics(pos, now=now, window_days=3650.0,
                      since=forward_since(cur.get("since")))
        t = automatic_transition(cur["state"], m, forward=fwd)
        out["strategies"][s] = {"state": cur["state"],
                                "closed": m["closed_positions"],
                                "realized_pnl_usd": m["realized_pnl_usd"]}
        if t is None:
            continue
        eid = await record(
            conn, account_id=account_id, strategy=s,
            from_state=cur["state"], to_state=t["to_state"],
            rule_id=t["rule_id"], actor=AUTOMATIC_ACTOR,
            evidence={"rolling": m, "forward": fwd,
                      "rules_firing": t["rules_firing"]},
            why="predeclared rule %s fired (%s)" % (
                t["rule_id"], json.dumps(t["rules_firing"][0]["evidence"],
                                         default=str)), at=now)
        out["transitions"].append(dict(t, strategy=s, event_id=eid))
        out["strategies"][s]["state"] = t["to_state"]
    return out


async def step(conn, ctx: dict) -> dict:
    """THE PAPER PASS STEP: on the main paper account only, at most every
    RUN_EVERY_S. Records only; never places, cancels or sizes an order."""
    from . import bettor_paper_ledger as L
    acct = ctx.get("account_id")
    now = float(ctx["now"])
    if acct != await L.selected_account(conn):
        return {"ran": False, "why": "MAIN_PAPER_ACCOUNT_ONLY"}
    last = _LAST_RUN.get(acct)
    if last is not None and 0 <= now - last < RUN_EVERY_S:
        return {"ran": False, "why": "RAN_WITHIN_RUN_EVERY_S"}
    _LAST_RUN[acct] = now
    got = await evaluate_and_record(conn, account_id=acct, now=now)
    return {"ran": True, "transitions": len(got["transitions"]),
            "refusal": got.get("refusal")}


# ═════════════════════════════════════════════════════════════════════
# THE READ MODEL (GET /api/command/paper/turnaround)
# ═════════════════════════════════════════════════════════════════════

def capital_shifted(pos: list, *, demoted_at: float | None, now: float,
                    days: float = DEMOTION_COMPARE_DAYS) -> dict:
    """Capital the strategy deployed per day (by first fill) in the `days`
    before its latest demotion vs after it. None without a demotion."""
    if demoted_at is None:
        return {"status": "NO_DEMOTION", "before_usd_per_day": None,
                "after_usd_per_day": None, "shifted_usd_per_day": None}
    span = days * 86400.0
    after_span = max(0.0, min(float(now), demoted_at + span) - demoted_at)

    def dep(lo, hi):
        return sum((_num(p.get("acquisition_cost_usd")) or 0.0) for p in pos
                   if lo <= (_num(p.get("first_fill_at")) or -1) < hi)

    before = dep(demoted_at - span, demoted_at) / days
    after = (dep(demoted_at, demoted_at + after_span) / (after_span / 86400.0)
             if after_span > 0 else None)
    ch_before = sum(
        (_num(p.get("acquisition_cost_usd")) or 0.0) * max(0.0, (
            (closed_at(p) or now) - (_num(p.get("first_fill_at")) or now)))
        / 3600.0 for p in pos
        if demoted_at - span <= (_num(p.get("first_fill_at")) or -1)
        < demoted_at) / days
    return {"status": "MEASURED" if after is not None else "TOO_EARLY",
            "demoted_at": demoted_at, "compare_days": days,
            "before_usd_per_day": round(before, 6),
            "after_usd_per_day": None if after is None else round(after, 6),
            "shifted_usd_per_day": (None if after is None
                                    else round(before - after, 6)),
            "capital_hours_per_day_before": round(ch_before, 6)}


def reallocation_plan(rows: dict) -> dict:
    """WHERE THE FREED CAPITAL-HOURS MAY GO (advisory, PAPER). Donors: every
    demoted strategy (rank >= REDUCED_SIZE) with measured freed capital-hours.
    Recipients: ACTIVE strategies whose FORWARD evidence meets PROMOTION and
    whose forward $/capital-hour beats the donor's. None => stays CASH.
    Weights are proportional to forward $/capital-hour. Changes no cap."""
    donors, recipients = [], []
    for s, r in rows.items():
        st, fwd = r.get("state"), r.get("forward") or {}
        if st in RANK and RANK[st] >= RANK[REDUCED_SIZE]:
            freed = (r.get("capital_shifted_after_demotion") or {}).get(
                "capital_hours_per_day_before")
            donors.append({"strategy": s, "state": st,
                           "freed_capital_hours_per_day": freed,
                           "forward_dollars_per_capital_hour":
                               fwd.get("dollars_per_capital_hour")})
        elif st in ACTIVE_STATES and promotion_evidence(fwd)["ok"]:
            recipients.append({"strategy": s, "state": st,
                               "forward_dollars_per_capital_hour":
                                   fwd["dollars_per_capital_hour"],
                               "forward_closed": fwd.get("closed_positions")})
    plan = []
    for d in donors:
        floor_rate = d["forward_dollars_per_capital_hour"]
        ok = [r for r in recipients
              if floor_rate is None
              or r["forward_dollars_per_capital_hour"] > floor_rate]
        tot = sum(r["forward_dollars_per_capital_hour"] for r in ok)
        plan.append({"donor": d["strategy"], "donor_state": d["state"],
                     "freed_capital_hours_per_day":
                         d["freed_capital_hours_per_day"],
                     "to": ([{"strategy": r["strategy"], "share": round(
                         r["forward_dollars_per_capital_hour"] / tot, 9)}
                         for r in ok] if ok and tot > 0 else []),
                     "otherwise": "CASH" if not ok else None})
    return {"rule": ("only toward ACTIVE strategies whose FORWARD evidence "
                     "meets the promotion bar and beats the donor's; never "
                     "on short-term profit alone; never by raising a cap"),
            "caps_changed": False, "paper_only": True,
            "donors": donors, "eligible_recipients": recipients,
            "plan": plan}


async def turnaround_view(conn, *, account_id: str, now: float) -> dict:
    """Everything the turnaround page shows, read only. Raises on a failed
    read; the route turns that into UNAVAILABLE (never zeros)."""
    if not await schema(conn):
        return {"status": "UNAVAILABLE", "why": "MIGRATION_290_NOT_APPLIED"}
    from . import bettor_paper_ledger as L
    from . import bettor_stale_management as SM
    allpos = await L.lineage_positions(conn, account_id)
    names = set(KNOWN_STRATEGIES) | {strategy_of(p, L.DEFAULT_STRATEGY)
                                     for p in allpos}
    events = [dict(r) for r in await conn.fetch(
        "SELECT event_id, strategy, from_state, to_state, rule_id, "
        "       rules_version, rules_sha, actor, why, evidence, recorded_at "
        "  FROM paper_strategy_lifecycle_events WHERE account_id = $1 "
        " ORDER BY event_id DESC LIMIT 200", account_id)]
    for e in events:
        e["recorded_at"] = float(e["recorded_at"].timestamp())
        if isinstance(e.get("evidence"), str):
            e["evidence"] = json.loads(e["evidence"])
    rows = {}
    for s in sorted(names):
        pos = [p for p in allpos if strategy_of(p, L.DEFAULT_STRATEGY) == s]
        cur = await current_state(conn, account_id, s)
        m = metrics(pos, now=now)
        fwd = metrics(pos, now=now, window_days=3650.0,
                      since=forward_since(cur.get("since")))
        demos = [e for e in events if e["strategy"] == s
                 and e["from_state"] in RANK
                 and RANK[e["to_state"]] > RANK[e["from_state"]]
                 and RANK[e["to_state"]] >= RANK[REDUCED_SIZE]]
        stale = await SM.stale_management_rate(conn, account_id, s, now=now)
        rows[s] = {
            "strategy": s, "state": cur.get("state"),
            "state_since": cur.get("since"),
            "state_rule_id": cur.get("rule_id"),
            "entry_allowed": cur.get("state") not in NO_ENTRY_STATES,
            "decision_size_factor": decision_size_factor(cur.get("state")),
            "ledger_max_order_usd": (REDUCED_SIZE_MAX_ORDER_USD
                                     if cur.get("state") == REDUCED_SIZE
                                     else None),
            "rolling": m, "forward": fwd,
            "rules_firing": rules_firing(m, current=cur.get("state"),
                                         forward=fwd),
            "stale_management": stale,
            "stale_management_blocks_entries": bool(
                stale.get("ok") is not True or (
                    (stale.get("open_positions") or 0)
                    >= STALE_MANAGEMENT_MIN_OPEN
                    and (stale.get("rate") or 0) > STALE_MANAGEMENT_MAX_RATE)),
            "capital_shifted_after_demotion": capital_shifted(
                pos, demoted_at=(demos[0]["recorded_at"] if demos else None),
                now=now)}
    return {"status": "OK", "account_id": account_id, "as_of": now,
            "version": VERSION, "rules_version": RULES_VERSION,
            "rules_sha": RULES_SHA, "rules": RULES,
            "states": list(STATES), "strategies": rows,
            "reallocation": reallocation_plan(rows),
            "events": events[:100],
            "losses_hidden_or_reclassified": 0,
            "authority": "PAPER_ONLY_NO_CAPITAL_AUTHORITY"}


def describe() -> dict:
    return {"version": VERSION, "rules_version": RULES_VERSION,
            "rules_sha": RULES_SHA, "rules": RULES, "states": list(STATES),
            "upward_step": UPWARD_STEP, "paper_only": True}

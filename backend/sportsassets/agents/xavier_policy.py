"""XAVIER_MANAGEMENT_POLICY_V1: THE POSITION MANAGER'S POLICY, EXPLICIT AND VERSIONED.

WHAT THIS IS. The parameters that describe how Xavier chooses among the
alternatives for a held position, written down as one named, versioned object
so a reader (and Audrey) can see exactly which policy a decision ran under.
It is stored and loaded through the three-agent contract's
`agent_policy_versions` table (migration 152, owned by core) and falls back to
the code default below -- labelled `source='CODE_DEFAULT'` -- whenever that
table is absent, holds no ACTIVE row, or holds a row that does not validate.

WHAT THE DEFAULT IS: THE CURRENTLY APPROVED POLICY, UNCHANGED.
`bettor_funded_decision.decide` ranks every admitted alternative on EXPECTED
NET VALUE over the whole position; among equal expected values the higher
worst case goes first, then the smaller new capital; the worst case enters
only as the owner's downside CONSTRAINT, never as the sort key. The default
parameters reproduce that choice exactly -- `apply` returns decide()'s own
selection -- and a test pins it (no silent policy change).

THE OBJECTIVE ORDER (documentation of intent, in priority order):
  1. preserve capital WITHIN the approved policy (the owner's limits are
     constraints decide() already applies; nothing here loosens them);
  2. improve the expected outcome (the ranking key);
  3. seek favorable indirect pairs (the search order -- never a purchase rule);
  4. capital efficiency (the tie-break's smaller-new-capital leg);
  5. an auditable explanation (every alternative on the record).

CAPITAL-PRESERVATION PARAMETERS ARE DISABLED BY DEFAULT.
`max_ev_sacrifice_for_downside_usd = 0`: an alternative whose worst case is
better than the expected-value winner's but whose expected value is lower is
preferred ONLY when this is set above zero, only by at most that many dollars
of expected value, and only among candidates decide() already admitted under
the approved limits. Whenever it is non-zero the record names the parameter,
its value, the expected value given up and the worst case gained.

SEARCH PREFERENCES ARE NOT PERMISSIONS. `preferred_max_combined_pair_cost`
(1.00) and `example_hedge_price_preference` (0.60) say where the search looks
first and how the record annotates a pair. They admit nothing, reject
nothing and change no ranking: a $1.10 pair is still valued on its numbers,
and an under-$1 pair is still able to lose once fees and settlement states
are counted.

WHAT IS NOT A PARAMETER HERE. Risk limits, credentials, account authority and
approval controls: `validate` refuses any of those keys by name, so no policy
version can carry them.

WHERE IT APPLIES. In the real selector: `bettor_funded_pair_cycle.pass_once`
loads the ACTIVE version once per pass and `decide_and_record` runs the ONE
decision function it names (`run`): EXPECTED_NET_VALUE -> `bettor_funded_
decision.decide` unchanged; CAPITAL_PRESERVATION_V1 -> `decide` below. Its
single winner goes through the existing binding, plan digest, claim and
execution controls; the one-measure gate checks the preservation rule at both
ends of the void range (`capital_preservation_is_robust`). The OTHER policy
runs on the same frozen inputs as `shadow_comparison` -- recorded, displayed,
never dispatched. `requires_complete_comparison` withholds acquisitions from
an incomplete hedge search before the choice (`gate_incomplete_search`).
Versions are activated only by a person (`activate_version`).
"""
from __future__ import annotations

import copy
import json
import math
from typing import Any

AGENT_ID = "XAVIER"
POLICY_KEY = "XAVIER_MANAGEMENT_POLICY"
VERSION = "V1"
SOURCE_CODE_DEFAULT = "CODE_DEFAULT"
#: The label core's registry gives an ACTIVE stored version (agents.
#: registry.SOURCE_ACTIVE_POLICY), used whichever reader loaded it.
SOURCE_TABLE = "ACTIVE_POLICY"
#: The code-declared candidate the default is shadowed against when no
#: CANDIDATE version is stored. Displayed only, never dispatched.
SOURCE_CODE_CANDIDATE = "CODE_CANDIDATE"
SOURCE_STORED_CANDIDATE = "STORED_CANDIDATE"

SEL_EXPECTED_NET_VALUE = "EXPECTED_NET_VALUE"
#: The candidate rule: decide()'s ranking, then the capital-preservation leg.
SEL_CAPITAL_PRESERVATION = "CAPITAL_PRESERVATION_V1"
#: The selection rules this module implements, and the decision function
#: each one runs. EXPECTED_NET_VALUE runs `bettor_funded_decision.decide`
#: UNCHANGED; CAPITAL_PRESERVATION_V1 runs `decide` below (decide(), then
#: the preservation leg over decide()'s own admitted candidates).
SELECTION_RULES = (SEL_EXPECTED_NET_VALUE, SEL_CAPITAL_PRESERVATION)
DECISION_FUNCTION = {
    SEL_EXPECTED_NET_VALUE: "bettor_funded_decision.decide",
    SEL_CAPITAL_PRESERVATION: "agents.xavier_policy.decide"}
#: The sacrifice the code-declared CAPITAL_PRESERVATION_V1 candidate carries
#: for the shadow comparison (a stated example, displayed only).
CODE_CANDIDATE_SACRIFICE_USD = 0.50

R_RULE_NEEDS_SACRIFICE = (
    "CAPITAL_PRESERVATION_V1_NEEDS_A_POSITIVE_EV_SACRIFICE_AND_"
    "EXPECTED_NET_VALUE_TAKES_NONE")
R_INCOMPLETE_SEARCH = "INCOMPLETE_SEARCH_POLICY_REQUIRES_COMPLETE_COMPARISON"
R_APPROVER_REQUIRED = "AN_ACTIVE_VERSION_NAMES_A_PERSON_WHO_APPROVED_IT"
R_AGENT_CANNOT_APPROVE = "NO_AGENT_MAY_APPROVE_OR_ACTIVATE_A_POLICY_VERSION"

R_UNKNOWN_KEY = "THAT_IS_NOT_A_XAVIER_POLICY_PARAMETER"
R_FORBIDDEN_KEY = "RISK_LIMITS_CREDENTIALS_AND_APPROVALS_ARE_NOT_POLICY_PARAMETERS"
R_BAD_VALUE = "THE_PARAMETER_VALUE_IS_NOT_VALID"
R_UNSUPPORTED_RULE = "THAT_SELECTION_RULE_IS_NOT_IMPLEMENTED"
R_NO_ACTIVE_ROW = "NO_ACTIVE_XAVIER_POLICY_VERSION_IS_STORED"
R_NO_TABLE = "THE_AGENT_POLICY_VERSIONS_TABLE_IS_NOT_IN_THIS_DATABASE"
R_READ_FAILED = "THE_POLICY_VERSION_READ_FAILED"
R_INVALID_STORED = "THE_STORED_POLICY_VERSION_DID_NOT_VALIDATE"

OBJECTIVE_ORDER = (
    {"rank": 1, "objective": "PRESERVE_CAPITAL_WITHIN_APPROVED_POLICY",
     "how": ("the owner's downside and new-capital limits are constraints "
             "decide() applies BEFORE choosing; a breaching alternative is "
             "removed, never out-ranked. Nothing here loosens them")},
    {"rank": 2, "objective": "IMPROVE_EXPECTED_OUTCOME",
     "how": "the ranking key: expected net value over the whole position"},
    {"rank": 3, "objective": "SEEK_FAVORABLE_INDIRECT_PAIRS",
     "how": ("the search order quotes overlapping structures first and the "
             "search preferences annotate them; a search order is not a "
             "purchase rule")},
    {"rank": 4, "objective": "CAPITAL_EFFICIENCY",
     "how": ("at equal expected value the smaller new capital goes first "
             "(the tie-break), and every alternative states its capital and "
             "duration separately")},
    {"rank": 5, "objective": "AUDITABLE_EXPLANATION",
     "how": ("every alternative with its own economics or exact blocker on "
             "the persisted record, never only the winner")},
)

#: Every parameter, with its default and what it means. The defaults ARE the
#: approved policy.
PARAMETERS: dict[str, dict] = {
    "selection_rule": {
        "default": SEL_EXPECTED_NET_VALUE,
        "doc": ("which decision function runs. EXPECTED_NET_VALUE is "
                "decide()'s approved rule, run unchanged; "
                "CAPITAL_PRESERVATION_V1 runs decide() and then the "
                "preservation leg (it needs max_ev_sacrifice_for_downside_usd "
                "> 0)")},
    "requires_complete_comparison": {
        "default": False, "group": "search_completeness",
        "doc": ("when true, an indirect acquisition is not selectable from a "
                "hedge search that did not examine every sibling (budget, "
                "limit or deadline): it is withheld by name "
                "(INCOMPLETE_SEARCH_POLICY_REQUIRES_COMPLETE_COMPARISON) "
                "BEFORE the choice, so HOLD / EXIT / REDUCE stay selectable "
                "and a protective exit is never suppressed")},
    "max_ev_sacrifice_for_downside_usd": {
        "default": 0.0, "group": "capital_preservation",
        "doc": ("DISABLED at 0. When > 0, an admitted alternative whose worst "
                "case is strictly better than the expected-value winner's, and "
                "whose expected value is at most this many dollars lower, is "
                "preferred (the best worst case among them). The record shows "
                "the parameter, the expected value given up and the worst case "
                "gained")},
    "preferred_max_combined_pair_cost": {
        "default": 1.00, "group": "search_preferences",
        "doc": ("SEARCH PREFERENCE, NOT A PERMISSION: a pair whose two legs "
                "cost at most this per matched unit (before fees) is examined "
                "first and annotated. It admits and rejects nothing")},
    "example_hedge_price_preference": {
        "default": 0.60, "group": "search_preferences",
        "doc": ("SEARCH PREFERENCE, NOT A PERMISSION (an example value): a "
                "hedge leg priced at or below this is annotated as within the "
                "preference. It admits and rejects nothing")},
}

#: Keys no policy version may carry, whatever their value.
FORBIDDEN_KEYS = (
    "max_downside_usd", "max_incremental_capital_usd", "capital_usd",
    "per_order_usd", "event_exposure_usd", "max_exposure_usd",
    "daily_loss_stop_usd", "limits", "risk_limits", "credentials",
    "api_key", "account_id", "account_authority", "authorization",
    "approval", "approved", "approved_by", "submission_enabled",
    "FUNDED_SUBMISSION_ENABLED", "REAL_ORDER_SUBMISSION_ENABLED",
    "FUNDED_EXIT_SUBMISSION_ENABLED")

NOT_PARAMETERS = (
    "risk limits (the owner-approved limits decide() applies as constraints)",
    "credentials", "account authority", "approval controls",
    "the submission switches")


def default_params() -> dict:
    return {k: v["default"] for k, v in PARAMETERS.items()}


XAVIER_MANAGEMENT_POLICY_V1: dict[str, Any] = {
    "agent_id": AGENT_ID, "policy_key": POLICY_KEY, "version": VERSION,
    "params": default_params(),
    "objective_order": [dict(o) for o in OBJECTIVE_ORDER],
    "parameter_docs": {k: v["doc"] for k, v in PARAMETERS.items()},
    "tie_break": ("at equal expected net value: the higher worst-case net, "
                  "then the smaller incremental capital (decide()'s own)"),
    "worst_case_is": "A_CONSTRAINT_NOT_THE_SORT_KEY",
    "not_parameters": list(NOT_PARAMETERS),
}


def _finite(v) -> float | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def validate(params: dict | None) -> dict:
    """ONE POLICY VERSION'S PARAMETERS, CHECKED. Pure.

    Missing keys take the default; an unknown key, a forbidden key (a risk
    limit, credential or approval), an unsupported selection rule or an
    invalid value refuses the whole version by name -- a half-applied policy
    is not a policy anybody approved."""
    raw = dict(params or {})
    out = default_params()
    for k, v in raw.items():
        if k in FORBIDDEN_KEYS:
            return {"ok": False, "refusal": R_FORBIDDEN_KEY, "key": k}
        if k not in PARAMETERS:
            return {"ok": False, "refusal": R_UNKNOWN_KEY, "key": k}
        if k == "selection_rule":
            if v not in SELECTION_RULES:
                return {"ok": False, "refusal": R_UNSUPPORTED_RULE,
                        "key": k, "value": v}
            out[k] = v
            continue
        if k == "requires_complete_comparison":
            if not isinstance(v, bool):
                return {"ok": False, "refusal": R_BAD_VALUE, "key": k,
                        "value": v}
            out[k] = v
            continue
        f = _finite(v)
        if f is None or f < 0 or (k != "max_ev_sacrifice_for_downside_usd"
                                  and not 0 < f <= 2.0):
            return {"ok": False, "refusal": R_BAD_VALUE, "key": k,
                    "value": v}
        out[k] = f
    s = float(out["max_ev_sacrifice_for_downside_usd"])
    if (out["selection_rule"] == SEL_CAPITAL_PRESERVATION) != (s > 0):
        return {"ok": False, "refusal": R_RULE_NEEDS_SACRIFICE,
                "selection_rule": out["selection_rule"], "sacrifice": s}
    return {"ok": True, "refusal": None, "params": out}


def code_default(*, why: str | None = None) -> dict:
    p = copy.deepcopy(XAVIER_MANAGEMENT_POLICY_V1)
    return dict(p, source=SOURCE_CODE_DEFAULT, why=why, approved_by=None,
                approved_at=None)


async def load(conn) -> dict:
    """THE ACTIVE POLICY, or the code default labelled CODE_DEFAULT. Never
    raises.

    Order: the core registry's `active_policy` (when core's module is
    present); else a direct guarded read of `agent_policy_versions`; else the
    code default. A stored row is used only when it validates."""
    stored = None
    try:
        from . import registry as REG                    # core's module
        got = await REG.active_policy(conn, AGENT_ID, POLICY_KEY,
                                      default=default_params())
        got = dict(got or {})
        if str(got.get("source") or "") != SOURCE_CODE_DEFAULT \
                and got.get("params") is not None:
            stored = {"version": got.get("version"),
                      "params": got.get("params"),
                      "approved_by": got.get("approved_by"),
                      "approved_at": got.get("approved_at"),
                      "read_by": "agents.registry.active_policy"}
        elif str(got.get("source") or "") == SOURCE_CODE_DEFAULT:
            return code_default(why=got.get("why") or R_NO_ACTIVE_ROW)
    except (ImportError, AttributeError):
        stored = None
    except Exception as exc:                                    # noqa: BLE001
        return code_default(why="%s:%s" % (R_READ_FAILED, type(exc).__name__))
    if stored is None:
        try:
            present = await conn.fetchval(
                "SELECT to_regclass('agent_policy_versions')")
        except Exception as exc:                                # noqa: BLE001
            return code_default(why="%s:%s" % (R_READ_FAILED,
                                               type(exc).__name__))
        if present is None:
            return code_default(why=R_NO_TABLE)
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT version, params, approved_by, approved_at "
                    "  FROM agent_policy_versions WHERE agent_id=$1 "
                    "   AND policy_key=$2 AND state='ACTIVE' "
                    " ORDER BY created_at DESC LIMIT 1", AGENT_ID, POLICY_KEY)
        except Exception as exc:                                # noqa: BLE001
            return code_default(why="%s:%s" % (R_READ_FAILED,
                                               type(exc).__name__))
        if row is None:
            return code_default(why=R_NO_ACTIVE_ROW)
        params = row["params"]
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except ValueError:
                params = {"__unparseable__": params}
        stored = {"version": row["version"], "params": params,
                  "approved_by": row["approved_by"],
                  "approved_at": row["approved_at"],
                  "read_by": "agent_policy_versions (direct)"}
    v = validate(stored.get("params"))
    if not v.get("ok"):
        return dict(code_default(why=R_INVALID_STORED),
                    rejected_version=stored.get("version"),
                    rejected_because=v)
    base = copy.deepcopy(XAVIER_MANAGEMENT_POLICY_V1)
    return dict(base, version=str(stored.get("version") or VERSION),
                params=v["params"], source=SOURCE_TABLE, why=None,
                approved_by=stored.get("approved_by"),
                approved_at=stored.get("approved_at"),
                read_by=stored.get("read_by"))


# ═════════════════════════════════════════════════════════════════════
# THE CHOICE: decide()'s SELECTION, WITH THE (DISABLED) PRESERVATION LEG
# ═════════════════════════════════════════════════════════════════════

def _worst(c: dict) -> float | None:
    return _finite((c or {}).get("worst_case_net_usd",
                                 (c or {}).get("downside_usd")))


def _key(c: dict | None):
    c = dict(c or {})
    return (c.get("action"), c.get("candidate_id"), c.get("plan_digest"),
            _finite(c.get("qty")))


def apply(verdict: dict, policy: dict | None = None) -> dict:
    """THE POLICY'S CHOICE ON decide()'s OWN RESULT. Pure.

    With the default parameters this returns decide()'s selection exactly
    (`identical_to_approved_ev_policy: True`). With
    `max_ev_sacrifice_for_downside_usd > 0` it may prefer an admitted
    candidate with a strictly better KNOWN worst case whose expected value is
    within that sacrifice, and says exactly what that costs."""
    v = dict(verdict or {})
    pol = dict(policy or code_default())
    params = dict(pol.get("params") or default_params())
    s = _finite(params.get("max_ev_sacrifice_for_downside_usd")) or 0.0
    best = v.get("selected_candidate")
    out: dict[str, Any] = {
        "policy_key": pol.get("policy_key", POLICY_KEY),
        "version": pol.get("version", VERSION),
        "source": pol.get("source", SOURCE_CODE_DEFAULT),
        "selection_rule": params.get("selection_rule"),
        "approved_ev_selected": v.get("selected"),
        "approved_ev_selected_candidate": _key(best) if best else None,
        "selected": v.get("selected"),
        "selected_candidate": best,
        "identical_to_approved_ev_policy": True,
        "capital_preservation": {
            "parameter": "max_ev_sacrifice_for_downside_usd",
            "value": s, "enabled": s > 0,
            "consequence": ("DISABLED: the expected-value winner stands"
                            if s <= 0 else None)}}
    if s <= 0 or not best:
        return out
    bw = _worst(best)
    bv = _finite(best.get("value_usd"))
    if bv is None:
        return out
    pool = []
    for c in v.get("candidates") or []:
        cv, cw = _finite(c.get("value_usd")), _worst(c)
        if cv is None or cw is None or _key(c) == _key(best):
            continue
        if bv - cv <= s + 1e-12 and (bw is None or cw > bw + 1e-12):
            pool.append(c)
    if not pool:
        out["capital_preservation"]["consequence"] = (
            "ENABLED AT %.6f AND NOTHING QUALIFIED: no admitted alternative "
            "within that expected-value sacrifice has a better known worst "
            "case, so the expected-value winner stands" % s)
        return out
    pool.sort(key=lambda c: (-_worst(c), -_finite(c.get("value_usd")),
                             _finite(c.get("incremental_capital_usd")) or 0.0))
    pick = pool[0]
    ev_given_up = round(bv - float(pick["value_usd"]), 6)
    wc_gained = (None if bw is None else round(_worst(pick) - bw, 6))
    out.update(selected=pick.get("action"), selected_candidate=pick,
               identical_to_approved_ev_policy=False)
    out["capital_preservation"]["consequence"] = {
        "preferred": _key(pick), "instead_of": _key(best),
        "expected_value_given_up_usd": ev_given_up,
        "worst_case_gained_usd": wc_gained,
        "winner_worst_case_known": bw is not None,
        "why": ("max_ev_sacrifice_for_downside_usd=%.6f permits giving up "
                "%.6f of expected value for a worst case %s"
                % (s, ev_given_up,
                   "better by %.6f" % wc_gained if wc_gained is not None
                   else "that is known where the winner's is not"))}
    return out


def decide(*, policy: dict | None = None, **kw) -> dict:
    """CAPITAL_PRESERVATION_V1's DECISION FUNCTION: `bettor_funded_decision.
    decide`, then the preservation leg over decide()'s own admitted
    candidates. With a zero sacrifice the result's `selected` /
    `selected_candidate` are decide()'s own, unchanged; `xavier_policy`
    carries the policy's statement either way."""
    from .. import bettor_funded_decision as FD

    verdict = FD.decide(**kw)
    got = apply(verdict, policy)
    out = dict(verdict, xavier_policy=got)
    if not got["identical_to_approved_ev_policy"]:
        cons = got["capital_preservation"]["consequence"]
        out.update(selected=got["selected"],
                   selected_candidate=got["selected_candidate"],
                   approved_ev_selected=verdict.get("selected"),
                   approved_ev_selected_candidate=verdict.get(
                       "selected_candidate"),
                   selection_reason=(
                       "%s under CAPITAL_PRESERVATION_V1: %s. The expected-"
                       "value winner was %s at %s; the worst case stays a "
                       "constraint the approved limits already applied"
                       % (got["selected"], cons.get("why"),
                          verdict.get("selected"),
                          (verdict.get("selected_candidate") or {}).get(
                              "value_usd"))))
    return out


def identity(policy: dict | None) -> dict:
    """The policy a decision ran under: key, version, source, rule, the
    decision function and the parameters -- persisted on the record."""
    pol = dict(policy or code_default())
    params = dict(pol.get("params") or default_params())
    rule = params.get("selection_rule") or SEL_EXPECTED_NET_VALUE
    return {"policy_key": pol.get("policy_key", POLICY_KEY),
            "version": pol.get("version", VERSION),
            "source": pol.get("source", SOURCE_CODE_DEFAULT),
            "why_source": pol.get("why"),
            "approved_by": pol.get("approved_by"),
            "selection_rule": rule,
            "decision_function": DECISION_FUNCTION.get(rule),
            "params": params}


def run(policy: dict | None, **kw) -> dict:
    """RUN THE DECISION FUNCTION THE POLICY NAMES, once. The only selector:
    EXPECTED_NET_VALUE -> `bettor_funded_decision.decide` exactly as it was;
    CAPITAL_PRESERVATION_V1 -> `decide` above. Anything else (a policy that
    did not validate) runs the approved function and says so."""
    from .. import bettor_funded_decision as FD

    ident = identity(policy)
    if ident["selection_rule"] == SEL_CAPITAL_PRESERVATION and \
            validate(ident["params"]).get("ok"):
        out = decide(policy=policy, **kw)
    else:
        out = FD.decide(**kw)
    return dict(out, decision_policy=ident)


def code_candidate() -> dict:
    """The code-declared CAPITAL_PRESERVATION_V1 candidate the default is
    shadowed against when no CANDIDATE version is stored."""
    return dict(code_default(), version="CAPITAL_PRESERVATION_V1-code",
                source=SOURCE_CODE_CANDIDATE,
                params=dict(default_params(),
                            selection_rule=SEL_CAPITAL_PRESERVATION,
                            max_ev_sacrifice_for_downside_usd=(
                                CODE_CANDIDATE_SACRIFICE_USD)),
                why=("a stated example candidate, displayed as a shadow and "
                     "never dispatched"))


async def load_shadow(conn, active: dict) -> dict:
    """THE OTHER POLICY for the shadow comparison. Active EXPECTED_NET_VALUE
    -> the newest stored CANDIDATE version that validates as
    CAPITAL_PRESERVATION_V1, else the code candidate; active
    CAPITAL_PRESERVATION_V1 -> the approved EXPECTED_NET_VALUE default.
    Never raises."""
    rule = identity(active)["selection_rule"]
    if rule == SEL_CAPITAL_PRESERVATION:
        return dict(code_default(),
                    why="the approved EXPECTED_NET_VALUE policy, as shadow")
    try:
        if await conn.fetchval(
                "SELECT to_regclass('agent_policy_versions')") is not None:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT version, params FROM agent_policy_versions "
                    " WHERE agent_id=$1 AND policy_key=$2 "
                    "   AND state='CANDIDATE' ORDER BY created_at DESC "
                    " LIMIT 1", AGENT_ID, POLICY_KEY)
            if row is not None:
                params = row["params"]
                if isinstance(params, str):
                    params = json.loads(params)
                v = validate(params)
                if v.get("ok") and v["params"]["selection_rule"] == \
                        SEL_CAPITAL_PRESERVATION:
                    return dict(code_default(), version=row["version"],
                                params=v["params"],
                                source=SOURCE_STORED_CANDIDATE, why=None)
    except Exception:                                           # noqa: BLE001
        pass
    return code_candidate()


def _selected_view(v: dict) -> dict:
    c = dict(v.get("selected_candidate") or {})
    return {"selected": v.get("selected"),
            "selected_candidate": _key(c) if c else None,
            "expected_net_usd": _finite(c.get("value_usd")),
            "worst_case_net_usd": _worst(c)}


def shadow_comparison(active_verdict: dict, shadow: dict | None,
                      **frozen) -> dict:
    """THE OTHER POLICY'S CHOICE ON THE SAME FROZEN INPUTS. Displayed, never
    dispatched: nothing reads it to bind, claim or send. Pure.

    `frozen` is the exact keyword set the active decision ran on (deep-
    copied here, so neither run can alter the other's inputs)."""
    sh = dict(shadow or code_candidate())
    try:
        v = run(sh, **copy.deepcopy(frozen))
    except Exception as exc:                                    # noqa: BLE001
        return {"policy": identity(sh), "ok": False,
                "error": type(exc).__name__, "dispatched": False}
    a, b = _selected_view(active_verdict or {}), _selected_view(v)
    act_id = dict((active_verdict or {}).get("decision_policy") or {})
    act_rule = act_id.get("selection_rule") or SEL_EXPECTED_NET_VALUE
    ev_side, cp_side = (a, b) if act_rule == SEL_EXPECTED_NET_VALUE \
        else (b, a)

    def _d(x, y):
        return None if x is None or y is None else round(x - y, 6)
    same = a["selected_candidate"] == b["selected_candidate"]
    why = (v.get("xavier_policy") or {}).get("capital_preservation") or {}
    return {
        "is": "DISPLAYED_NEVER_DISPATCHED",
        "dispatched": False, "ok": True,
        "policy": identity(sh),
        "active_policy": {k: act_id.get(k) for k in (
            "policy_key", "version", "source", "selection_rule")},
        "active_selected": a, "shadow_selected": b,
        "same_choice": same,
        "shadow_refusal": v.get("refusal"),
        "shadow_selection_reason": v.get("selection_reason"),
        "ev_change_vs_active_usd": _d(b["expected_net_usd"],
                                      a["expected_net_usd"]),
        "worst_case_change_vs_active_usd": _d(b["worst_case_net_usd"],
                                              a["worst_case_net_usd"]),
        "ev_given_up_by_capital_preservation_usd": _d(
            ev_side["expected_net_usd"], cp_side["expected_net_usd"]),
        "downside_improved_by_capital_preservation_usd": _d(
            cp_side["worst_case_net_usd"], ev_side["worst_case_net_usd"]),
        "capital_preservation": why or None,
        "frozen_inputs": ("the same hold ranking, indirect candidates, "
                          "limits and capital duration the active decision "
                          "ran on")}


def gate_incomplete_search(candidates: list, policy: dict | None,
                           search: dict | None) -> dict:
    """requires_complete_comparison, APPLIED BEFORE THE CHOICE. Pure.

    When the policy requires it and the hedge search was not complete (or
    its completeness is not established), every indirect acquisition is
    made not rankable by name -- so the choice is made over HOLD / EXIT /
    REDUCE and a protective exit is never suppressed by an incomplete
    search. Returns the candidates to decide on and the gate's statement."""
    params = dict((policy or code_default()).get("params") or {})
    sc = dict(search or {})
    need = bool(params.get("requires_complete_comparison"))
    complete = sc.get("complete")
    out = {"requires_complete_comparison": need,
           "search_complete": complete,
           "search_stop_reason": sc.get("stop_reason"),
           "applied": False, "withheld": []}
    if not need or complete is True:
        return dict(out, candidates=list(candidates or []))
    kept = []
    for c in candidates or []:
        if str(c.get("action")) == "ACQUIRE_INDIRECT_HEDGE" and \
                c.get("rankable"):
            out["withheld"].append(c.get("candidate_id"))
            kept.append(dict(c, rankable=False, value_usd=None,
                             blocker=R_INCOMPLETE_SEARCH,
                             withheld_value_usd=c.get("value_usd"),
                             why=("the active policy requires a complete "
                                  "comparison and the hedge search ended "
                                  "%s (%s): this pair is best among what was "
                                  "examined, not across the market"
                                  % (sc.get("stop_reason"),
                                     sc.get("comparison_scope")))))
        else:
            kept.append(c)
    out["applied"] = bool(out["withheld"])
    return dict(out, candidates=kept)


def _fixed_key(c: dict) -> tuple:
    a = str((c or {}).get("action") or "")
    if a == "ACQUIRE_INDIRECT_HEDGE":
        return (a, str(c.get("candidate_id")))
    return (a, a)


def capital_preservation_is_robust(verdict: dict, cv: dict,
                                   policy: dict | None) -> dict:
    """THE ONE-MEASURE GATE FOR CAPITAL_PRESERVATION_V1. Pure.

    The common valuation values every fixed action at both ends of the void
    rate's range. The preservation rule is applied at EACH end on those
    values, with each candidate's worst case (a property of the payout table,
    not of the probability); the selection may go to funded dispatch only if
    the rule picks the same fixed action at both ends and it is the one the
    decision selected. A rankable set that changes within the range refuses,
    as it does under the approved policy."""
    params = dict((policy or {}).get("params") or {})
    s = _finite(params.get("max_ev_sacrifice_for_downside_usd")) or 0.0
    cvd = dict(cv or {})
    if not cvd.get("ok"):
        return {"permitted": False,
                "refusal": "THE_COMMON_VALUATION_COULD_NOT_VALUE_THIS_POSITION",
                "cv_refusal": cvd.get("refusal")}
    rows = [r for r in cvd.get("valued") or []]
    lo_set = {tuple(r["fixed_action"][:2]) for r in rows
              if r.get("value_at_range_low") is not None}
    hi_set = {tuple(r["fixed_action"][:2]) for r in rows
              if r.get("value_at_range_high") is not None}
    if lo_set != hi_set:
        return {"permitted": False, "refusal": (
            "A_CANDIDATE_IS_RANKABLE_AT_ONE_END_OF_THE_RANGE_AND_NOT_THE_"
            "OTHER")}
    worst = {}
    for c in (verdict or {}).get("candidates") or []:
        worst[_fixed_key(c)] = _worst(c)
    same_order = dict(cvd.get("same_order_exits") or {})

    def pick(end):
        vals = {tuple(r["fixed_action"][:2]): r[end] for r in rows
                if r.get(end) is not None}
        if not vals:
            return None
        ev_w = max(vals, key=lambda k: (vals[k], str(k)))
        pool = [k for k, v in vals.items()
                if vals[ev_w] - v <= s + 1e-12 and worst.get(k) is not None
                and (worst.get(ev_w) is None
                     or worst[k] > worst[ev_w] + 1e-12)]
        if not pool:
            return ev_w
        return max(pool, key=lambda k: (worst[k], vals[k], str(k)))
    lo, hi = pick("value_at_range_low"), pick("value_at_range_high")
    sel = _fixed_key((verdict or {}).get("selected_candidate") or {})

    def same(k):
        if k is None:
            return False
        if k == sel:
            return True
        return sel[0] in same_order.get(k[0], ()) or \
            k[0] in same_order.get(sel[0], ())
    ok = same(lo) and same(hi)
    return {"permitted": ok,
            "refusal": (None if ok else
                        "THE_CAPITAL_PRESERVATION_CHOICE_CHANGES_WITHIN_THE_"
                        "VOID_RATES_UNCERTAINTY"),
            "selection_basis": ("CAPITAL_PRESERVATION_ROBUST_ACROSS_THE_VOID_"
                                "RATE_RANGE" if ok else None),
            "choice_at_range_low": None if lo is None else list(lo),
            "choice_at_range_high": None if hi is None else list(hi),
            "selected": list(sel)}


# ── ACTIVATION: A PERSON'S WRITE, NEVER AN AGENT'S ────────────────────
AGENT_ACTORS = ("DEREK", "XAVIER", "AUDREY")


async def activate_version(conn, *, version: str, params: dict,
                           approved_by: str, created_by: str,
                           statement: str | None = None,
                           now: float | None = None) -> dict:
    """ACTIVATE ONE POLICY VERSION FOR XAVIER, as the owner's approval does.

    Validates the parameters (risk limits, credentials and approvals are
    refused as parameters), refuses an agent as approver, writes the version
    and makes it the ONE ACTIVE row for the key -- the previous ACTIVE is
    RETIRED, never deleted -- in one transaction. It is for an owner-
    authenticated route or an operator; no Xavier code path calls it (a test
    pins that). Never raises."""
    import time as _t
    import datetime as _d
    who = str(approved_by or "").strip()
    if not who:
        return {"ok": False, "refusal": R_APPROVER_REQUIRED}
    if who.upper() in AGENT_ACTORS or who.upper().startswith("AGENT"):
        return {"ok": False, "refusal": R_AGENT_CANNOT_APPROVE,
                "approved_by": who}
    v = validate(params)
    if not v.get("ok"):
        return dict(v, ok=False)
    at = _d.datetime.fromtimestamp(float(now if now is not None
                                         else _t.time()), _d.timezone.utc)
    try:
        if await conn.fetchval(
                "SELECT to_regclass('agent_policy_versions')") is None:
            return {"ok": False, "refusal": R_NO_TABLE}
        async with conn.transaction():
            if await conn.fetchval(
                    "SELECT to_regclass('agent_identities')") is not None:
                await conn.execute(
                    "INSERT INTO agent_identities (agent_id, display_name, "
                    " mandate, tool_permissions) VALUES ($1,'Xavier',$2,"
                    " '{}'::jsonb) ON CONFLICT (agent_id) DO NOTHING",
                    AGENT_ID, "position management within the approved "
                    "policy")
            await conn.execute(
                "UPDATE agent_policy_versions SET state='RETIRED' "
                " WHERE agent_id=$1 AND policy_key=$2 AND state='ACTIVE' "
                "   AND version<>$3", AGENT_ID, POLICY_KEY, str(version))
            await conn.execute(
                "INSERT INTO agent_policy_versions (agent_id, policy_key, "
                " version, params, state, created_by, approved_by, "
                " approved_at, created_at) VALUES ($1,$2,$3,$4::jsonb,"
                " 'ACTIVE',$5,$6,$7,$7) ON CONFLICT (agent_id, policy_key, "
                " version) DO UPDATE SET state='ACTIVE', params=$4::jsonb, "
                " approved_by=$6, approved_at=$7",
                AGENT_ID, POLICY_KEY, str(version),
                json.dumps(v["params"]), str(created_by), who, at)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": R_READ_FAILED,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    return {"ok": True, "refusal": None, "version": str(version),
            "params": v["params"], "approved_by": who,
            "statement": (statement or "")[:500]}


def search_preference_view(*, combined_cost_per_unit=None,
                           hedge_price=None, policy: dict | None = None
                           ) -> dict:
    """HOW A PAIR SITS AGAINST THE SEARCH PREFERENCES. Pure; admits nothing."""
    params = dict((policy or code_default()).get("params") or {})
    cap = _finite(params.get("preferred_max_combined_pair_cost"))
    hp = _finite(params.get("example_hedge_price_preference"))
    cc, h = _finite(combined_cost_per_unit), _finite(hedge_price)
    return {"is_a_permission": False,
            "preferred_max_combined_pair_cost": cap,
            "combined_cost_per_unit": cc,
            "within_combined_cost_preference": (None if cc is None
                                                or cap is None
                                                else cc <= cap + 1e-12),
            "example_hedge_price_preference": hp,
            "hedge_price": h,
            "within_hedge_price_preference": (None if h is None or hp is None
                                              else h <= hp + 1e-12),
            "what_it_changes": ("nothing about admission or ranking: the "
                                "preference orders the search and annotates "
                                "the record")}


def record(verdict: dict, policy: dict | None) -> dict:
    """What the persisted Xavier decision carries about its policy. The
    verdict is the one the policy's own decision function produced (see
    `run`), so `applied_to_dispatch` is true: its winner is the only one that
    proceeds to binding, the plan digest and the claim."""
    ident = dict((verdict or {}).get("decision_policy") or identity(policy))
    xp = dict((verdict or {}).get("xavier_policy") or {})
    identical = bool(xp.get("identical_to_approved_ev_policy", True))
    sel = (verdict or {}).get("selected_candidate")
    ev_sel = ((verdict or {}).get("approved_ev_selected_candidate")
              if not identical else sel)
    return dict(ident,
                identical_to_approved_ev_policy=identical,
                approved_ev_selected=((verdict or {}).get(
                    "approved_ev_selected") if not identical
                    else (verdict or {}).get("selected")),
                approved_ev_selected_candidate=_key(ev_sel) if ev_sel
                else None,
                policy_selected=(verdict or {}).get("selected"),
                policy_selected_candidate=_key(sel) if sel else None,
                capital_preservation=(xp.get("capital_preservation") or {
                    "parameter": "max_ev_sacrifice_for_downside_usd",
                    "value": ident["params"].get(
                        "max_ev_sacrifice_for_downside_usd"),
                    "enabled": False,
                    "consequence": "DISABLED: the expected-value winner "
                                   "stands"}),
                applied_to_dispatch=True,
                dispatch_note=None)


def describe() -> dict:
    return {"policy": code_default(), "forbidden_keys": list(FORBIDDEN_KEYS),
            "selection_rules_implemented": list(SELECTION_RULES)}

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

WHERE IT APPLIES TODAY. The dispatch path is `decide()`'s selection. A
non-default capital-preservation value is evaluated and RECORDED with its
consequence on every Xavier decision; making it the dispatched choice needs
the pair cycle to call `decide` below instead of `FD.decide` (core's file),
and an ACTIVE, approved version row. Until then the record says
`applied_to_dispatch: false` whenever the policy's choice would differ.
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
SOURCE_TABLE = "AGENT_POLICY_VERSIONS"

SEL_EXPECTED_NET_VALUE = "EXPECTED_NET_VALUE"
#: The selection rules this module implements. Only the approved one.
SELECTION_RULES = (SEL_EXPECTED_NET_VALUE,)

R_UNKNOWN_KEY = "THAT_IS_NOT_A_XAVIER_POLICY_PARAMETER"
R_FORBIDDEN_KEY = "RISK_LIMITS_CREDENTIALS_AND_APPROVALS_ARE_NOT_POLICY_PARAMETERS"
R_BAD_VALUE = "THE_PARAMETER_VALUE_IS_NOT_VALID"
R_UNSUPPORTED_RULE = "ONLY_THE_APPROVED_EXPECTED_NET_VALUE_RULE_IS_IMPLEMENTED"
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
        "doc": ("the ranking key. EXPECTED_NET_VALUE is decide()'s approved "
                "rule; no other rule is implemented")},
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
        f = _finite(v)
        if f is None or f < 0 or (k != "max_ev_sacrifice_for_downside_usd"
                                  and not 0 < f <= 2.0):
            return {"ok": False, "refusal": R_BAD_VALUE, "key": k,
                    "value": v}
        out[k] = f
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
    """`bettor_funded_decision.decide`, then the policy. With the default
    policy the result's `selected` / `selected_candidate` are decide()'s own,
    unchanged; `xavier_policy` carries the policy's statement."""
    from .. import bettor_funded_decision as FD

    verdict = FD.decide(**kw)
    got = apply(verdict, policy)
    out = dict(verdict, xavier_policy=got)
    if not got["identical_to_approved_ev_policy"]:
        out.update(selected=got["selected"],
                   selected_candidate=got["selected_candidate"],
                   approved_ev_selected=verdict.get("selected"),
                   approved_ev_selected_candidate=verdict.get(
                       "selected_candidate"))
    return out


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
    """What the persisted Xavier decision carries about its policy."""
    pol = dict(policy or code_default())
    got = apply(verdict, pol)
    return {"policy_key": got["policy_key"], "version": got["version"],
            "source": got["source"], "why_source": pol.get("why"),
            "approved_by": pol.get("approved_by"),
            "params": dict(pol.get("params") or {}),
            "identical_to_approved_ev_policy": got[
                "identical_to_approved_ev_policy"],
            "approved_ev_selected": got["approved_ev_selected"],
            "approved_ev_selected_candidate": got[
                "approved_ev_selected_candidate"],
            "policy_selected": got["selected"],
            "policy_selected_candidate": _key(got["selected_candidate"])
            if got["selected_candidate"] else None,
            "capital_preservation": got["capital_preservation"],
            "applied_to_dispatch": bool(got[
                "identical_to_approved_ev_policy"]),
            "dispatch_note": (
                None if got["identical_to_approved_ev_policy"] else
                "the dispatch path is decide()'s approved expected-value "
                "selection; this policy's different choice is recorded with "
                "its consequence and is not dispatched")}


def describe() -> dict:
    return {"policy": code_default(), "forbidden_keys": list(FORBIDDEN_KEYS),
            "selection_rules_implemented": list(SELECTION_RULES)}

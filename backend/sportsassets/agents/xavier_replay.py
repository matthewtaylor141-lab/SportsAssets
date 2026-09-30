"""XAVIER'S CAPITAL-PRESERVATION TRADEOFF, REPLAYED ON HIS RECORDED DECISIONS.

WHAT THIS IS. The deterministic evaluator behind the improvement class
XAVIER_CAPITAL_PRESERVATION_TRADEOFF (`agents.improvement`). For every
recorded Xavier decision it RE-RUNS XAVIER'S ACTUAL DECISION FUNCTION
(`agents.xavier_policy.run` -- `bettor_funded_decision.decide`, then the
capital-preservation leg) on the FROZEN INPUTS persisted on the record
(`reasoning.decision_inputs`: the HOLD ranking, the indirect candidates after
the search gate, the limits and the capital duration it actually ran on),
once under the current parameter and once under each variant, applies the
same one-measure dispatch gate (`bettor_funded_pair_cycle.
common_valuation_gate`) to the persisted common valuation, and values the
action that would have been taken on the position's KNOWN settlement using
that alternative's persisted payout table (whole-position economics at its
frozen executable prices and fees).

WHAT IT DOES NOT DO. It never reconstructs an input a record does not carry:
a decision written before the inputs were persisted is excluded BY NAME. It
never contacts a venue, never writes a decision, never activates a policy. A
fill for an alternative that was not executed is UNPROVEN: the evidence is
RETROSPECTIVE, on known settlements, and says so.

THE UNIT. One weight per fixture. A position is counted at its FIRST
recorded review only: after that review the position's state (and so every
later review's inputs) would differ under a variant, so later reviews are
excluded by name rather than replayed on a state that would not have existed.

SETTLEMENTS come only from what is persisted: a funded ENTRY intent closed by
the venue's settlement (with any booked correction), and a labelled pair
observation, which records the venue's settlement of BOTH contracts whether or
not either was held. Sources that disagree make the contract CONTESTED.

EVIDENCE SCOPE. A DEMONSTRATION book (`bettor_funded_book.
is_demonstration_account`) is never read as performance. An evaluation reads
ONE scope: PRODUCTION (demonstration books excluded by name), or REHEARSAL
(demonstration books only, every result labelled REHEARSAL: it establishes
the machinery, NOTHING about opportunity).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any

VERSION = "XAVIER_TRADEOFF_REPLAY_V1"
PARAM = "max_ev_sacrifice_for_downside_usd"
UNITS = ("USD_OF_WHOLE_POSITION_EXPECTED_VALUE_GIVEN_UP_PER_DECISION "
         "(0.50 == fifty cents per decision; never a fraction or a percent)")
HOLDOUT_SALT = "xavier-tradeoff-holdout-v1"
HOLDOUT_PERCENT = 40

SCOPE_PRODUCTION = "PRODUCTION"
SCOPE_REHEARSAL = "REHEARSAL"
SCOPE_AUTO = "AUTO"
LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"

EXECUTION_ASSUMPTION = (
    "each position is valued as its FIRST recorded review's action held to "
    "the contract's settlement, at that alternative's frozen executable "
    "prices and fees (its persisted payout table); an order that was not "
    "placed is NOT proven to have filled; later reviews are not replayed "
    "(their state would differ under a variant); an action the one-measure "
    "gate would refuse is not dispatched, so the position is valued as HELD")
REHEARSAL_LABEL = (
    "REHEARSAL: every decision read comes from a DEMONSTRATION book; it "
    "establishes that the evaluator re-runs the real selector on recorded "
    "inputs and values them on recorded settlements -- NOTHING about "
    "opportunity or performance")

# ── WHY A RECORD IS NOT AN ELIGIBLE DECISION (each counted by name) ───
X_NOT_A_DECISION = "NOT_A_DECISION_RECORD"
X_NO_INPUTS = "NO_FROZEN_DECISION_INPUTS_PERSISTED"
X_DIGEST = "FROZEN_INPUTS_DIGEST_MISMATCH"
X_NO_POLICY = "NO_DECISION_POLICY_RECORDED"
X_GROUP = "TWO_LEG_GROUP_DECISIONS_ARE_NOT_REPLAYED"
X_LATER = "LATER_REVIEW_OF_A_POSITION_ALREADY_COUNTED"
X_NOT_REPRODUCED = "REPLAY_DOES_NOT_REPRODUCE_THE_RECORDED_CHOICE"
X_REPLAY_RAISED = "THE_REPLAY_RAISED"
X_NOTHING = "NOTHING_WAS_SELECTABLE"
X_NO_TABLE = "A_RANKABLE_ALTERNATIVE_HAS_NO_PAYOUT_TABLE"
X_NO_CONTRACT = "A_RANKABLE_ACQUISITIONS_CONTRACT_IS_NOT_IDENTIFIED"
X_NO_HELD = "THE_HELD_CONTRACT_IS_NOT_IDENTIFIED"
X_UNSETTLED = "OUTCOME_UNKNOWN_A_CONTRACT_IS_NOT_SETTLED"
X_CONTESTED = "OUTCOME_CONTESTED_SETTLEMENT_SOURCES_DISAGREE"
X_VOID = "A_CONTRACT_WAS_VOIDED"
X_STATE = "THE_SETTLED_STATE_IS_NOT_ESTABLISHED_IN_A_PAYOUT_TABLE"
X_NO_HOLD = "NO_HOLD_ALTERNATIVE_TO_VALUE_A_REFUSED_DISPATCH"


def _f(v):
    try:
        if v is None or isinstance(v, bool):
            return None
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def _obj(v):
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return None
    return v


def akey(c: dict | None) -> tuple:
    """One alternative's identity: action, candidate id, plan digest."""
    c = dict(c or {})
    return (str(c.get("action") or ""), str(c.get("candidate_id") or ""),
            str(c.get("plan_digest") or ""))


def _skey(k) -> str:
    return "|".join(k)


def _canon_key(k):
    from . import xavier_policy as XP
    return XP._canon(list(k)) if k is not None else None


# ═════════════════════════════════════════════════════════════════════
# 1 · SETTLEMENTS (persisted sources only)
# ═════════════════════════════════════════════════════════════════════

async def _regclass(conn, name: str) -> bool:
    try:
        return await conn.fetchval("SELECT to_regclass($1)", name) is not None
    except Exception:                                           # noqa: BLE001
        return False


def _epoch(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    return _f(v)


async def contract_settlements(conn, slugs) -> dict:
    """{slug: {"price", "void", "contested", "known_at", "sources"}} from the
    funded book's venue-settled ENTRY intents (a booked correction replaces
    the booked price) and labelled pair observations. Never a venue read."""
    want = sorted({str(s) for s in slugs if s})
    got: dict[str, list] = {s: [] for s in want}
    if not want:
        return {}
    if await _regclass(conn, "bettor_funded_intents"):
        rows = await conn.fetch(
            "SELECT intent_id, us_market_slug, settlement, closed_reason, "
            "       extract(epoch FROM closed_at)::float8 AS closed_at "
            "  FROM bettor_funded_intents WHERE us_market_slug = "
            "  ANY($1::text[]) AND kind = 'ENTRY' AND settlement IS NOT NULL "
            "   AND closed_reason IN ('SETTLED_BY_THE_VENUE', "
            "                         'VOIDED_BY_THE_VENUE') "
            " ORDER BY intent_id", want)
        corr: dict[str, dict] = {}
        if rows and await _regclass(conn,
                                    "bettor_funded_settlement_corrections"):
            for c in await conn.fetch(
                    "SELECT intent_id, to_reading, to_price::float8 AS px, "
                    "       extract(epoch FROM created_at)::float8 AS at "
                    "  FROM bettor_funded_settlement_corrections "
                    " WHERE intent_id = ANY($1::text[]) "
                    " ORDER BY created_at, correction_id",
                    [r["intent_id"] for r in rows]):
                corr[c["intent_id"]] = dict(c)
        for r in rows:
            st = _obj(r["settlement"]) or {}
            at = _f(st.get("at")) or r["closed_at"]
            void = r["closed_reason"] == "VOIDED_BY_THE_VENUE"
            px = None if void else _f(st.get("payout_price"))
            src = "bettor_funded_intents:%s" % r["intent_id"]
            c = corr.get(r["intent_id"])
            if c is not None:
                void = c["to_reading"] == "EXPLICIT_VOID"
                px = None if void else _f(c["px"])
                at = max(float(at or 0.0), float(c["at"] or 0.0))
                src += "+correction"
            got[str(r["us_market_slug"])].append(
                {"price": px, "void": void, "at": at, "source": src})
    if await _regclass(conn, "bettor_pair_observations"):
        for r in await conn.fetch(
                "SELECT observation_id, primary_slug, hedge_slug, "
                "       primary_settlement_price::float8 AS pp, "
                "       hedge_settlement_price::float8 AS hp, "
                "       extract(epoch FROM outcome_available_at)::float8 "
                "         AS at "
                "  FROM bettor_pair_observations WHERE label_status = "
                "  'LABELLED' AND (primary_slug = ANY($1::text[]) OR "
                "  hedge_slug = ANY($1::text[])) ORDER BY observation_id",
                want):
            for slug, px in ((r["primary_slug"], r["pp"]),
                             (r["hedge_slug"], r["hp"])):
                if slug in got and px is not None:
                    got[slug].append({"price": float(px), "void": False,
                                      "at": r["at"],
                                      "source": "bettor_pair_observations:%s"
                                                % r["observation_id"]})
    out = {}
    for slug, xs in got.items():
        if not xs:
            out[slug] = {"price": None, "void": False, "contested": False,
                         "known_at": None, "sources": []}
            continue
        prices = {None if x["void"] else round(float(x["price"]), 9)
                  for x in xs if x["void"] or x["price"] is not None}
        contested = len(prices) > 1
        one = next(iter(prices)) if len(prices) == 1 else None
        out[slug] = {"price": None if contested else one,
                     "void": (not contested and None in prices),
                     "contested": contested,
                     "known_at": min(float(x["at"]) for x in xs
                                     if x["at"] is not None)
                     if any(x["at"] is not None for x in xs) else None,
                     "sources": sorted(x["source"] for x in xs)}
    return out


def per_unit_payout(price, side) -> float | None:
    """A side's payout per contract at a long-side settlement price."""
    p = _f(price)
    if p is None:
        return None
    if str(side) == LONG:
        return p
    if str(side) == SHORT:
        return 1.0 - p
    return None


def settled_net(table: dict | None, cents: list) -> tuple:
    """(net, reason): the payout table's established row whose per-leg
    cents are the settled ones. Every matching row must agree."""
    t = dict(table or {})
    if t.get("kind") not in ("HELD_LEG", "ACQUISITION"):
        return None, X_NO_TABLE
    nets = set()
    for r in t.get("rows") or []:
        plc = r.get("per_leg_cents_per_unit")
        if not r.get("established") or not isinstance(plc, list) or \
                len(plc) != len(cents):
            continue
        try:
            same = all(c is not None and int(round(float(c))) == int(w)
                       for c, w in zip(plc, cents))
        except (TypeError, ValueError):
            same = False
        if same and _f(r.get("net_pnl_usd")) is not None:
            nets.add(round(float(r["net_pnl_usd"]), 6))
    if len(nets) != 1:
        return None, X_STATE
    return next(iter(nets)), None


# ═════════════════════════════════════════════════════════════════════
# 2 · THE RECORDS
# ═════════════════════════════════════════════════════════════════════

def _hedge_contract(di: dict, cand: dict) -> dict | None:
    cid = str(cand.get("candidate_id") or "")
    c = dict((di.get("acquisition_contracts") or {}).get(cid) or {})
    if c.get("slug") and c.get("side") in (LONG, SHORT):
        return {"slug": str(c["slug"]), "side": c["side"],
                "from": "reasoning.decision_inputs.acquisition_contracts"}
    if "#" in cid:
        slug, side = cid.rsplit("#", 1)
        if slug and side in (LONG, SHORT):
            return {"slug": slug, "side": side,
                    "from": "the candidate id (slug#side)"}
    return None


def recorded_replay(frozen: dict, policy: dict) -> dict:
    """The decision function, once, on a copy of the frozen inputs."""
    from . import xavier_policy as XP
    return XP.run(policy, **copy.deepcopy(frozen))


async def read_rows(conn, *, start: float, end: float,
                    scope: str = SCOPE_AUTO) -> dict:
    """EVERY XAVIER RECORD DECIDED IN (start, end], as replay rows. Returns
    {"rows", "scope", "outside_scope", "records_read"}; each row carries its
    fixture, decision and outcome instants, the frozen inputs, the recorded
    policy, the value of every rankable alternative at the settlement (or
    the named reason it has none) and `why` when it is not eligible for a
    structural reason."""
    from .. import bettor_funded_book as FB
    from . import xavier_policy as XP
    import datetime as _dt

    out: dict[str, Any] = {"rows": [], "scope": scope, "outside_scope": 0,
                           "records_read": 0}
    if not await _regclass(conn, "bettor_xavier_decisions"):
        return dict(out, refusal="THE_XAVIER_DECISION_TABLE_IS_NOT_HERE")
    has_intents = await _regclass(conn, "bettor_funded_intents")

    def ts(e):
        return _dt.datetime.fromtimestamp(float(e), _dt.timezone.utc)
    recs = await conn.fetch(
        "SELECT x.xavier_decision_id, x.account_id, x.intent_id, "
        "       x.portfolio_group_id, x.us_market_slug, "
        "       extract(epoch FROM x.decided_at)::float8 AS decided_at, "
        "       x.chosen_action, x.chosen_plan_digest, x.alternatives, "
        "       x.reasoning->'decision_inputs' AS decision_inputs, "
        "       x.reasoning->'decision_policy' AS decision_policy, "
        "       x.reasoning->'group_decision' AS group_decision, "
        "       (SELECT extract(epoch FROM min(y.decided_at))::float8 "
        "          FROM bettor_xavier_decisions y "
        "         WHERE y.intent_id = x.intent_id AND "
        "               jsonb_typeof(y.reasoning->'decision_policy') "
        "               = 'object') AS first_decided_at "
        "  FROM bettor_xavier_decisions x "
        " WHERE x.decided_at > $1 AND x.decided_at <= $2 "
        " ORDER BY x.decided_at, x.xavier_decision_id", ts(start), ts(end))
    out["records_read"] = len(recs)
    ev = {}
    if has_intents and recs:
        for r in await conn.fetch(
                "SELECT intent_id, event_key, us_market_slug, order_intent "
                "  FROM bettor_funded_intents WHERE intent_id = "
                "  ANY($1::text[])", sorted({x["intent_id"] for x in recs})):
            ev[r["intent_id"]] = dict(r)
    rows, need = [], set()
    demo_flags = []
    for x in recs:
        demo = FB.is_demonstration_account(x["account_id"])
        pol = _obj(x["decision_policy"])
        is_decision = isinstance(pol, dict)
        demo_flags.append((demo, is_decision))
    if scope == SCOPE_AUTO:
        prod = any(is_d and not d for d, is_d in demo_flags)
        scope = SCOPE_PRODUCTION if prod else SCOPE_REHEARSAL
    out["scope"] = scope
    for x in recs:
        demo = FB.is_demonstration_account(x["account_id"])
        if (scope == SCOPE_PRODUCTION) == demo:
            out["outside_scope"] += 1
            continue
        intent = ev.get(x["intent_id"]) or {}
        row: dict[str, Any] = {
            "id": x["xavier_decision_id"], "intent_id": x["intent_id"],
            "account_id": x["account_id"],
            "fixture": str(intent.get("event_key") or x["us_market_slug"]
                           or x["intent_id"]),
            "decided_at": float(x["decided_at"]),
            "outcome": None, "outcome_at": None, "why": None,
            "demonstration": demo}
        rows.append(row)
        pol = _obj(x["decision_policy"])
        di = _obj(x["decision_inputs"])
        if not isinstance(pol, dict):
            row["why"] = X_NOT_A_DECISION
            continue
        if x["first_decided_at"] is not None and \
                float(x["decided_at"]) > float(x["first_decided_at"]) + 1e-6:
            row["why"] = X_LATER
            continue
        if not isinstance(di, dict) or not di.get("version"):
            row["why"] = X_NO_INPUTS
            continue
        th = XP.thaw_inputs(di)
        if not th["ok"]:
            row["why"] = (X_DIGEST if th["refusal"] ==
                          "FROZEN_INPUTS_DIGEST_MISMATCH" else X_NO_INPUTS)
            continue
        if x["portfolio_group_id"] and _obj(x["group_decision"]):
            row["why"] = X_GROUP
            continue
        row["_frozen"] = th["frozen"]
        row["_frozen_digest"] = di.get("digest")
        row["_policy_params"] = dict(pol.get("params") or {})
        row["_recorded_sacrifice"] = _f(row["_policy_params"].get(PARAM))
        row["_cv"] = di.get("common_valuation")
        try:
            v = recorded_replay(th["frozen"], pol)
        except Exception as exc:                                # noqa: BLE001
            row["why"] = X_REPLAY_RAISED
            row["_error"] = type(exc).__name__
            continue
        sel = v.get("selected_candidate")
        rec_sel = di.get("selected")
        if _canon_key(XP._key(sel) if sel else None) != _canon_key(
                rec_sel if rec_sel is None else tuple(rec_sel)):
            row["why"] = X_NOT_REPRODUCED
            continue
        if not sel:
            row["why"] = X_NOTHING
            continue
        held = dict(di.get("held_contract") or {})
        if not held.get("slug"):
            held = {"slug": intent.get("us_market_slug")
                    or x["us_market_slug"],
                    "side": intent.get("order_intent")}
        if not held.get("slug") or held.get("side") not in (LONG, SHORT):
            row["why"] = X_NO_HELD
            continue
        alts = [a for a in (_obj(x["alternatives"]) or [])
                if isinstance(a, dict)]
        by_key = {akey(a): a for a in alts if a.get("rankable")}
        cands, bad = [], None
        for c in v.get("candidates") or []:
            a = by_key.get(akey(c))
            if a is None or not isinstance(a.get("payout_table"), dict):
                bad = X_NO_TABLE
                break
            legs = [{"slug": held["slug"], "side": held["side"]}]
            if str(c.get("action")) == "ACQUIRE_INDIRECT_HEDGE":
                hc = _hedge_contract(di, c)
                if hc is None:
                    bad = X_NO_CONTRACT
                    break
                legs.append(hc)
            cands.append({"key": akey(c), "cand": c, "alt": a, "legs": legs})
        if bad:
            row["why"] = bad
            continue
        row["_cands"] = cands
        row["_hold_key"] = next((k["key"] for k in cands
                                 if k["key"][0] == "HOLD"), None)
        for k in cands:
            for leg in k["legs"]:
                need.add(leg["slug"])
    settled = await contract_settlements(conn, need)
    for row in rows:
        if row["why"] is not None or "_cands" not in row:
            continue
        vals, why, at = {}, None, []
        for k in row.pop("_cands"):
            cents = []
            for leg in k["legs"]:
                s = settled.get(leg["slug"]) or {}
                if s.get("contested"):
                    why = why or X_CONTESTED
                    break
                if s.get("void"):
                    why = why or X_VOID
                    break
                u = per_unit_payout(s.get("price"), leg["side"])
                if u is None:
                    why = why or X_UNSETTLED
                    break
                cents.append(int(round(100.0 * u)))
                at.append(s.get("known_at"))
            if why:
                break
            net, bad = settled_net(k["alt"].get("payout_table"), cents)
            if bad:
                why = bad
                break
            a = k["alt"]
            tbl = a.get("payout_table") or {}
            vals[_skey(k["key"])] = {
                "action": k["key"][0], "settled_net_usd": net,
                "settled_cents": cents,
                "ex_ante_worst_case_usd": (
                    _f(a.get("worst_case_net_usd"))
                    if _f(a.get("worst_case_net_usd")) is not None
                    else _f(tbl.get("position_minimum_usd"))),
                "expected_net_usd": _f(a.get("expected_net_usd",
                                             a.get("value_usd"))),
                "capital_required_usd": _f(a.get("capital_required_usd"))
                or 0.0,
                "capital_released_usd": _f(a.get("capital_released_usd"))
                or 0.0}
        if why in (X_UNSETTLED, X_CONTESTED):
            # AN UNKNOWN OUTCOME is not an ineligible decision: the split
            # names it (outcome unknown), like any unsettled row
            row["_outcome_why"] = why
            continue
        if why:
            row["why"] = why
            continue
        row["outcome"] = vals
        row["outcome_at"] = max(a for a in at if a is not None) if any(
            a is not None for a in at) else None
        if row["outcome_at"] is None:
            row["outcome"] = None
            row["_outcome_why"] = X_UNSETTLED
    for row in rows:
        if row["why"] is not None:
            # A STRUCTURAL EXCLUSION is named by its own reason wherever the
            # split puts it, not hidden under "outcome unknown": the row
            # carries no outcome to use (an empty one), known at its decision.
            row["outcome"], row["outcome_at"] = {}, row["decided_at"]
    out["rows"] = rows
    return out


# ═════════════════════════════════════════════════════════════════════
# 3 · THE REPLAY (pure)
# ═════════════════════════════════════════════════════════════════════

def choose(row: dict, sacrifice: float) -> dict:
    """The action the decision function selects on this row's frozen inputs
    with `max_ev_sacrifice_for_downside_usd = sacrifice`, and whether the
    one-measure gate would dispatch it (refused -> the position is HELD)."""
    from .. import bettor_funded_pair_cycle as PC
    from . import xavier_policy as XP
    pol = XP.policy_with_sacrifice(row.get("_policy_params"), sacrifice)
    v = XP.run(pol, **copy.deepcopy(row["_frozen"]))
    sel = v.get("selected_candidate")
    if not sel:
        return {"selected": None, "effective": None, "gate": None}
    gate = PC.common_valuation_gate(v, row.get("_cv"), policy=pol)
    k = akey(sel)
    eff = k if (gate.get("permitted") or k[0] == "HOLD") else row.get(
        "_hold_key")
    return {"selected": k, "effective": eff,
            "gate": {"permitted": bool(gate.get("permitted")),
                     "refusal": gate.get("refusal")}}


def replay(rows: list, *, sacrifice: float) -> dict:
    """KNOWN SETTLEMENT, HYPOTHETICAL EXECUTION (`VERSION`).

    Reports, separately: rows considered; ELIGIBLE decisions and fixtures
    and why the rest were not; the actions the parameter selects (and how
    many the gate would refuse); net result after execution costs per
    eligible fixture; capital required and released; losing decisions and
    total loss; the worst single decision and the maximum drawdown of the
    chronological cumulative net; the mean ex-ante worst case of the chosen
    actions. RETROSPECTIVE: fills for unexecuted alternatives are UNPROVEN."""
    missing: dict[str, int] = {}
    decs = []
    for r in rows:
        why = r.get("why")
        if why is None and r.get("outcome") is None:
            why = r.get("_outcome_why") or X_UNSETTLED
        if why is not None:
            missing[why] = missing.get(why, 0) + 1
            continue
        try:
            ch = choose(r, sacrifice)
        except Exception:                                       # noqa: BLE001
            missing[X_REPLAY_RAISED] = missing.get(X_REPLAY_RAISED, 0) + 1
            continue
        if ch["effective"] is None:
            w = X_NOTHING if ch["selected"] is None else X_NO_HOLD
            missing[w] = missing.get(w, 0) + 1
            continue
        val = (r["outcome"] or {}).get(_skey(ch["effective"]))
        if val is None:
            missing[X_STATE] = missing.get(X_STATE, 0) + 1
            continue
        decs.append({"id": r["id"], "fixture": str(r["fixture"]),
                     "decided_at": r["decided_at"], "choice": ch,
                     "net": float(val["settled_net_usd"]), "val": val,
                     "demonstration": bool(r.get("demonstration"))})
    per_fx: dict[str, float] = {}
    for d in decs:
        per_fx[d["fixture"]] = per_fx.get(d["fixture"], 0.0) + d["net"]
    cum = peak = dd = 0.0
    # CHRONOLOGICAL; decisions of one review pass share an instant and are
    # ordered by fixture, so the drawdown never depends on a hash
    for d in sorted(decs, key=lambda d: (d["decided_at"], d["fixture"],
                                         d["id"])):
        cum += d["net"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    n = len(per_fx)
    nets = [d["net"] for d in decs]
    losers = [x for x in nets if x < 0]
    wcs = [d["val"]["ex_ante_worst_case_usd"] for d in decs
           if d["val"].get("ex_ante_worst_case_usd") is not None]
    actions: dict[str, int] = {}
    for d in decs:
        a = d["choice"]["effective"][0]
        actions[a] = actions.get(a, 0) + 1
    demo = sum(1 for d in decs if d["demonstration"])
    return {
        "sacrifice": float(sacrifice), "parameter": PARAM, "units": UNITS,
        "replay_version": VERSION, "rows_considered": len(rows),
        "eligible_decisions": len(decs), "fixtures": n,
        "eligible_fixtures": n, "not_eligible": missing,
        "no_eligible_decisions": not decs,
        "actions": actions,
        "dispatch_refused_by_the_gate": sum(
            1 for d in decs if d["choice"]["gate"] and
            not d["choice"]["gate"]["permitted"]
            and d["choice"]["selected"][0] != "HOLD"),
        "net_total": round(sum(nets), 6),
        "net_per_eligible_fixture": (round(sum(per_fx.values()) / n, 6)
                                     if n else None),
        "capital_required_total": round(sum(
            d["val"]["capital_required_usd"] for d in decs), 6),
        "capital_released_total": round(sum(
            d["val"]["capital_released_usd"] for d in decs), 6),
        "losing_decisions": len(losers),
        "loss_total": round(sum(losers), 6),
        "worst_decision_net_usd": (round(min(nets), 6) if nets else None),
        "max_drawdown": round(dd, 6),
        "mean_ex_ante_worst_case_usd": (round(sum(wcs) / len(wcs), 6)
                                        if wcs else None),
        "weighting": "ONE_WEIGHT_PER_FIXTURE",
        "evidence_class": ("REHEARSAL_DEMONSTRATION_BOOKS" if decs and
                           demo == len(decs) else
                           "PRODUCTION_RECORDS" if decs and not demo else
                           "MIXED" if decs else None),
        "evidence_category": "KNOWN_SETTLEMENT_HYPOTHETICAL_EXECUTION",
        "could_have_filled": "UNPROVEN",
        "execution_assumption": EXECUTION_ASSUMPTION,
        "measures": "RETROSPECTIVE_HYPOTHETICAL_EXECUTION_ON_KNOWN_SETTLEMENT",
        "_per_fixture_net": per_fx,
        "_choice": {d["id"]: _skey(d["choice"]["effective"]) for d in decs}}


def paired(var: dict, base: dict) -> dict:
    """THE VARIANT AGAINST THE CURRENT PARAMETER ON THE SAME DECISIONS, per
    eligible fixture: the mean change in settled net with a normal-
    approximation 95% interval, and the changes in drawdown, worst decision,
    ex-ante worst case, capital and losses; how many actions changed.
    Classifies the outcome -- a variant that changes no action is
    IDENTICAL_SELECTION, and no eligible decision is NO_ELIGIBLE_DECISIONS,
    never a success."""
    a, b = var.get("_per_fixture_net") or {}, base.get("_per_fixture_net") \
        or {}
    fx = sorted(set(a) | set(b))
    d = [a.get(f, 0.0) - b.get(f, 0.0) for f in fx]
    n = len(d)
    mean = sum(d) / n if n else None
    se = None
    if n > 1:
        v_ = sum((x - mean) ** 2 for x in d) / (n - 1)
        se = math.sqrt(v_ / n)
    ca, cb = var.get("_choice") or {}, base.get("_choice") or {}
    changed = sorted(i for i in set(ca) | set(cb) if ca.get(i) != cb.get(i))
    identical = not changed

    def _dd(k):
        x, y = var.get(k), base.get(k)
        return None if x is None or y is None else round(float(x)
                                                         - float(y), 6)
    if not var.get("eligible_decisions"):
        outcome = "NO_ELIGIBLE_DECISIONS"
    elif identical:
        outcome = "IDENTICAL_SELECTION"
    elif mean is not None and mean > 0:
        outcome = "IMPROVED_NET_RESULT"
    elif mean is not None and mean < 0:
        outcome = "WORSE_NET_RESULT"
    else:
        outcome = "NO_CHANGE_IN_NET_RESULT"
    return {
        "eligible_fixtures_compared": n,
        "delta_net_per_eligible_fixture": (None if mean is None
                                           else round(mean, 6)),
        "delta_net_std_error": None if se is None else round(se, 6),
        "delta_net_ci95": (None if se is None else
                           [round(mean - 1.96 * se, 6),
                            round(mean + 1.96 * se, 6)]),
        "delta_net_total": round(sum(d), 6),
        "delta_max_drawdown": _dd("max_drawdown"),
        "delta_worst_decision_net_usd": _dd("worst_decision_net_usd"),
        "delta_mean_ex_ante_worst_case_usd": _dd(
            "mean_ex_ante_worst_case_usd"),
        "delta_capital_required": _dd("capital_required_total"),
        "delta_capital_released": _dd("capital_released_total"),
        "delta_losing_decisions": int(var.get("losing_decisions") or 0)
        - int(base.get("losing_decisions") or 0),
        "actions_changed": len(changed), "changed_decisions": changed,
        "actions_identical": (int(var.get("eligible_decisions") or 0)
                              - len(changed)),
        "identical_selection": identical,
        "no_action_changed": identical,
        "objective_outcome": outcome,
        "uncertainty": ("normal approximation over eligible fixtures; "
                        "fixtures are treated as independent")}


def public(m: dict) -> dict:
    return {k: v for k, v in (m or {}).items() if not str(k).startswith("_")}


def rows_digest(rows: list) -> str:
    """THE INPUT RECORDS an evaluation read, by identity and by every field
    it used: the frozen-inputs digest, the recorded policy, the outcome
    instant and every alternative's settled value -- a changed settlement,
    correction, record or exclusion changes it."""
    flat = []
    for r in rows:
        flat.append([str(r.get("id")), str(r.get("fixture")),
                     round(float(r.get("decided_at") or 0.0), 3),
                     None if r.get("outcome_at") is None
                     else round(float(r["outcome_at"]), 3),
                     r.get("why"), r.get("_outcome_why"),
                     r.get("_frozen_digest"),
                     json.dumps(r.get("_policy_params") or {},
                                sort_keys=True, default=str),
                     json.dumps(r.get("outcome"), sort_keys=True,
                                default=str)])
    flat.sort(key=lambda x: (x[0], x[2]))
    return hashlib.sha256(json.dumps(flat, sort_keys=True).encode()
                          ).hexdigest()

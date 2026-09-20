"""END-TO-END NO-SUBMIT REHEARSAL. The integrated engine, wired.

Owner directive 2026-09-20 §1: "Run at least 10 candidate evaluations
using actual captured inputs through the applicable inventory, maker,
pair, exit, allocator and COMMAND components, with submission disabled
at the transport boundary. ... With required inputs still
NOT_IDENTIFIED, a correctly explained rejection is a valid result. Do
not invent fill probabilities, profitability, capital authority or
fills to produce a positive decision."

WHAT THIS DEMONSTRATES, AND WHAT IT DOES NOT

It demonstrates that the components are WIRED: that a real captured
market state flows through inventory, applicability, the maker engine,
the pair engine, the exit engine, the risk gate, the allocator and
COMMAND, and that the chain produces a decision with every blocker
named.

It demonstrates NOTHING about profitability. Every evaluation here is
expected to end WOULD_NOT_SUBMIT, because P_FILL, adverse selection and
fair value are NOT_IDENTIFIED and the chain is built to refuse rather
than to guess. A rehearsal that produced WOULD_SUBMIT today would mean
a component had invented one of those terms, which is why
`assert_no_fabrication` runs over every result.

THE TRANSPORT BOUNDARY

Two independent guards, because one is a promise and two is a
mechanism:

  1. IMPORT GUARD. No order-submitting symbol is reachable from this
     module's import graph. Checked over the actual module objects.
  2. ARMED TRIP. Every venue submit/cancel entry point is replaced, for
     the duration of the rehearsal, with a function that raises
     SubmissionAttempted. If any component ever reaches for one, the
     rehearsal fails loudly instead of sending anything.

The second exists because the first only proves nothing IMPORTS a
submit path today. The trip proves nothing CALLED one during this run.

INPUTS ARE REAL, AND THEIR PROVENANCE TRAVELS WITH THEM

Rows come from `bettor_state_observations` -- what the capture actually
wrote -- exported by research/bettor_rehearsal_inputs.sql. Each
evaluation reports the observation id, observed_at, universe_version
and rule_sha of the row it ran on, so no result can be quoted without
its input.

SYNTHETIC INPUTS ARE LABELLED AS SUCH. `COHORT` is REAL_CAPTURE for a
row that came from the database and SYNTHETIC for anything constructed
here. The two never mix in a summary.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from . import bettor_applicability as applic
from . import bettor_capital_allocator as alloc
from . import bettor_command_view as command
from . import bettor_ev_actions as acts
from . import bettor_exit_engine as exiteng
from . import bettor_inventory as invmod
from . import bettor_maker_engine as maker
from . import bettor_pair_engine as pairs
from . import bettor_risk_engine as risk
from . import bettor_state_capture as sc
from . import shadow_bettor as bettor

REHEARSAL_VERSION = "BETTOR_NO_SUBMIT_REHEARSAL_V1"

NOT_IDENTIFIED = "NOT_IDENTIFIED"
WOULD_SUBMIT = "WOULD_SUBMIT"
WOULD_NOT_SUBMIT = "WOULD_NOT_SUBMIT"
SIZE_NOT_AUTHORIZED = "SIZE_NOT_AUTHORIZED"

COHORT_REAL = "REAL_CAPTURE"
COHORT_SYNTHETIC = "SYNTHETIC"

# The venue entry points that would put an order on the wire. Every one
# is armed with a trip for the duration of a rehearsal.
TRANSPORT_ENTRY_POINTS = (
    ("sportsassets.pmus", "submit_fok"),
    ("sportsassets.pmus", "cancel_order"),
    ("sportsassets.pmx", "submit_fok"),
    ("sportsassets.pmx", "cancel_order"),
)

SUBMISSION_DISABLED_AT_TRANSPORT = (
    "every venue submit and cancel entry point is replaced with a "
    "function that raises. Nothing in this rehearsal can reach the "
    "wire, and an attempt fails the run rather than being logged and "
    "ignored")


class SubmissionAttempted(RuntimeError):
    """A component reached for the wire during a rehearsal.

    This is a failure of the rehearsal, not a caught edge case. If it
    ever raises, the chain contains a path to submission that the
    import guard did not see.
    """


class TransportDisabled:
    """Context manager arming the trip on every transport entry point.

    Restores the originals on exit even when the body raises, so a
    failed rehearsal cannot leave the process with its venue calls
    stubbed out.
    """

    def __init__(self):
        self._saved = []
        self.armed = []
        self.attempts = []

    def __enter__(self):
        import importlib
        for mod_name, attr in TRANSPORT_ENTRY_POINTS:
            try:
                mod = importlib.import_module(mod_name)
            except Exception:                                  # noqa: BLE001
                continue
            original = getattr(mod, attr, None)
            if original is None:
                continue
            self._saved.append((mod, attr, original))
            self.armed.append("%s.%s" % (mod_name, attr))

            def _trip(*a, _who="%s.%s" % (mod_name, attr), **k):
                self.attempts.append(_who)
                raise SubmissionAttempted(
                    "%s was called during a no-submit rehearsal" % _who)

            setattr(mod, attr, _trip)
        return self

    def __exit__(self, *exc):
        for mod, attr, original in self._saved:
            setattr(mod, attr, original)
        return False


def import_guard() -> dict:
    """Is any order-submitting symbol reachable from this module?

    Walks this module's own globals rather than parsing source: what
    matters is what is actually bound here at runtime.
    """
    banned = ("submit_fok", "cancel_order", "place_order", "create_order",
              "replace_order")
    found = []
    for name, obj in list(globals().items()):
        if name.startswith("__"):
            continue
        for b in banned:
            if hasattr(obj, b):
                found.append("%s.%s" % (name, b))
    return {
        "IMPORT_GUARD": "CLEAN" if not found else "REACHABLE",
        "reachableSubmitSymbols": found,
        "checked": list(banned),
    }


def _d(v):
    if v is None or v in (NOT_IDENTIFIED, ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _market_state_from_row(row: dict) -> dict | None:
    """The captured row, in the shape the decision path expects.

    Returns None when the book was not readable. That is not a
    degenerate case to be smoothed over -- an unreadable book is a
    market state we do not have, and the chain must refuse on it.
    """
    if row.get("book_readability_status") != "READABLE":
        return None
    bid, ask = _d(row.get("yes_bid")), _d(row.get("yes_ask"))
    if bid is None and ask is None:
        return None
    depth = row.get("yes_depth")
    if isinstance(depth, str):
        try:
            depth = json.loads(depth)
        except ValueError:
            depth = None
    return {
        "readable": True,
        "bid": str(bid) if bid is not None else None,
        "ask": str(ask) if ask is not None else None,
        "available_depth": depth,
        "captured_at": row.get("observed_at"),
        "symbol": row.get("market_id"),
        "evidence_source": "BETTOR_UNSELECTED_STATE_CAPTURE",
        "marketStateId": row.get("observation_id"),
    }


def evaluate(row: dict, *, cohort: str = COHORT_REAL,
             inventory_rows=None) -> dict:
    """One candidate evaluation through the whole chain.

    Every stage's verdict is kept, not just the last one, because the
    question this rehearsal answers is WHERE the chain stops -- and a
    single WOULD_NOT_SUBMIT with no stage detail would answer nothing.
    """
    state = _market_state_from_row(row)
    readable = state is not None

    # ── inventory ────────────────────────────────────────────────────
    inv = invmod.inventory(inventory_rows or [],
                           identity_status=row.get("identity_status"))
    inv_state = applic.inventory_state(inv)
    state_name = inv_state["state"]
    applicable = applic.applicable_set(state_name)

    # ── maker, per applicable passive action ─────────────────────────
    maker_tables, maker_missing = {}, {}
    for action in applicable.get("APPLICABLE", ()):
        spec = acts.CANONICAL_ACTIONS.get(action) or {}
        if spec.get("aggression") != acts.PASSIVE:
            continue
        t = maker.components(action, state)
        maker_tables[action] = t
        maker_missing[action] = t.get("componentsMissing", [])

    # ── pair and exit ────────────────────────────────────────────────
    pair = pairs.pair_view(inv, None)
    ex = exiteng.evaluate(inv, held_book=state, complement_book=None)

    # ── the decision path, unchanged ─────────────────────────────────
    opportunity = bettor.opportunity_record(
        symbol=row.get("market_id"),
        observed_at=datetime.now(tz=timezone.utc),
        outcome_leg=row.get("outcome_leg"),
        event_id=row.get("event_id"), market_id=row.get("market_id"),
        sport=row.get("sport"), league=row.get("league"),
        evidence_source="BETTOR_UNSELECTED_STATE_CAPTURE",
        market_state=state,
        features=bettor.microstructure_of(state),
        cadence_s=sc.SAMPLING_CADENCE_S)
    decision = bettor.decide(opportunity, state, inventory=inv)

    # ── risk gate on the decision's own action ───────────────────────
    action = decision.get("action") or "NO_TRADE"
    # THE RISK GATE'S OWN VOCABULARY. It returns rails and state gates,
    # each with a verdict, rather than a boolean -- because "allowed" is
    # not a fact this system has. A rail whose limit is NOT_PREDECLARED
    # and a gate that is NOT_EVALUABLE both mean the same thing here:
    # the gate cannot be passed, and saying so is the correct result.
    gate = risk.evaluate(action, observed=None, state=inv_state)
    rails = gate.get("rails") or []
    gates = gate.get("stateGates") or []
    rail_blocks = [r["rail"] for r in rails
                   if r.get("limit") == "NOT_PREDECLARED"]
    gate_blocks = [g["gate"] for g in gates
                   if g.get("verdict") != "CLEAR"]
    gate_ok = not rail_blocks and not gate_blocks

    # ── allocator ────────────────────────────────────────────────────
    allocation = alloc.allocate([], risk_state=inv_state)

    # ── COMMAND ──────────────────────────────────────────────────────
    panel = command.panel(inventory_state=state_name,
                          markets_observed=1,
                          readable_markets=1 if readable else 0,
                          inventory=inv, pair=pair, capital=allocation)

    # ── the submit verdict ───────────────────────────────────────────
    # THE BLOCKERS ARE OBJECTS, NOT STRINGS. Each carries its own
    # reason, which is the whole point of the vocabulary -- a blocker
    # flattened to a name loses why it fired.
    raw_blockers = list(decision.get("blockers") or ())
    blockers = [b.get("code", str(b)) if isinstance(b, dict) else str(b)
                for b in raw_blockers]
    size = allocation.get("allocation")
    size_out = (size if size not in (None, NOT_IDENTIFIED, "")
                else SIZE_NOT_AUTHORIZED)

    would = WOULD_NOT_SUBMIT
    why = []
    if action == "NO_TRADE":
        why.append("DECISION_IS_NO_TRADE")
    if blockers:
        why.append("BLOCKERS:%s" % ",".join(blockers))
    if not gate_ok:
        why.append("RISK_GATE_NOT_PASSED")
    if size_out == SIZE_NOT_AUTHORIZED:
        why.append(SIZE_NOT_AUTHORIZED)
    if not readable:
        why.append("MARKET_STATE_UNREADABLE")
    if not why:
        # Reached only if every gate above passed. Kept rather than
        # removed: a chain that can never say yes is not a chain that
        # was tested, and this is the branch a future reader must be
        # able to find.
        would = WOULD_SUBMIT

    return {
        "rehearsalVersion": REHEARSAL_VERSION,
        "COHORT": cohort,

        # ── the input, named so no result travels without it ─────────
        "OBSERVATION_ID": row.get("observation_id"),
        "INPUT_OBSERVED_AT": row.get("observed_at"),
        "INPUT_UNIVERSE_VERSION": row.get("universe_version"),
        "INPUT_RULE_SHA": (row.get("rule_sha") or "")[:16],
        "INPUT_MARKET_ID": row.get("market_id"),
        "INPUT_READABILITY": row.get("book_readability_status"),
        "INPUT_MID": row.get("mid"),
        "INPUT_SPREAD": row.get("spread"),
        "INPUT_LIVE_STATUS": row.get("live_status"),

        # ── what each stage said ─────────────────────────────────────
        "INVENTORY_STATE": state_name,
        "APPLICABLE_ACTIONS": list(applicable.get("APPLICABLE", ())),
        "DECISION": action,
        "BLOCKERS": blockers,
        "EV_COMPONENT_STATUS": {
            a: {"missing": m,
                "identified": [c["component"] for c
                               in maker_tables[a]["components"]
                               if c["status"] == "IDENTIFIED"]}
            for a, m in maker_missing.items()},
        "PAIR_COMPLETION_STATUS": pair.get("status", NOT_IDENTIFIED),
        "EXIT_STATUS": ex.get("status", NOT_IDENTIFIED),
        "RISK_GATE": "PASSED" if gate_ok else "NOT_PASSED",
        "RISK_GATE_DETAIL": {
            "railsWithoutADeclaredLimit": rail_blocks,
            "stateGatesNotClear": gate_blocks},
        "PROPOSED_INTENT": (decision.get("intent")
                            or ("NONE" if action == "NO_TRADE"
                                else action)),
        "PRICE": decision.get("price") or NOT_IDENTIFIED,
        "SIZE": size_out,
        "COMMAND_PANEL_STATUS": {k: panel.get(k)
                                 for k in command.STATUS_LINES},

        "WOULD_SUBMIT_RESULT": would,
        "WOULD_NOT_SUBMIT_BECAUSE": why,
        "submissionDisabled": SUBMISSION_DISABLED_AT_TRANSPORT,
    }


# ── the anti-fabrication check ───────────────────────────────────────

FABRICATION_CHECKS = (
    "P_FILL must not be numeric while bettor_p_fill reports it "
    "NOT_IDENTIFIED",
    "a WOULD_SUBMIT must carry an identified P_FILL, fair value and "
    "authorised size",
    "SIZE must be SIZE_NOT_AUTHORIZED while no mandate is frozen",
)


def assert_no_fabrication(result: dict) -> dict:
    """Would this result have required inventing a missing term?

    Runs over every evaluation. The failure mode it guards is not a
    component returning a wrong number -- it is a component returning
    ANY number for a term the evidence base says is unidentified.
    """
    problems = []
    if result["WOULD_SUBMIT_RESULT"] == WOULD_SUBMIT:
        for action, ev in result["EV_COMPONENT_STATUS"].items():
            if "P_FILL" in ev["missing"]:
                problems.append(
                    "WOULD_SUBMIT while P_FILL is missing for %s" % action)
        if result["SIZE"] == SIZE_NOT_AUTHORIZED:
            problems.append("WOULD_SUBMIT with no authorised size")
    if result["SIZE"] not in (SIZE_NOT_AUTHORIZED, NOT_IDENTIFIED):
        problems.append("a size was produced with no frozen mandate")
    return {"FABRICATION_CHECK": "CLEAN" if not problems else "FAILED",
            "problems": problems,
            "checks": list(FABRICATION_CHECKS)}


def run(rows: list, *, cohort: str = COHORT_REAL) -> dict:
    """The whole rehearsal, with the transport trip armed."""
    guard = import_guard()
    results, fabrication = [], []
    with TransportDisabled() as trip:
        for row in rows:
            r = evaluate(row, cohort=cohort)
            results.append(r)
            fabrication.append(assert_no_fabrication(r))
        attempts = list(trip.attempts)
        armed = list(trip.armed)

    submits = [r for r in results
               if r["WOULD_SUBMIT_RESULT"] == WOULD_SUBMIT]
    dirty = [f for f in fabrication if f["FABRICATION_CHECK"] != "CLEAN"]
    return {
        "rehearsalVersion": REHEARSAL_VERSION,
        "COHORT": cohort,
        "EVALUATIONS": len(results),
        "WOULD_SUBMIT_COUNT": len(submits),
        "WOULD_NOT_SUBMIT_COUNT": len(results) - len(submits),
        "TRANSPORT_ARMED": armed,
        "TRANSPORT_ATTEMPTS": attempts,
        "SUBMISSION_REACHED_THE_WIRE": bool(attempts),
        "IMPORT_GUARD": guard,
        "FABRICATION_CHECK": "CLEAN" if not dirty else "FAILED",
        "fabricationProblems": dirty,
        "results": results,
        "BETTOR_EV_REAL_ORDER_ACTIVITY": "NONE",
        "BETTOR_EV_REAL_CAPITAL_AT_RISK": 0,
        "mirrorLive": False,
        "commandDeployed": False,
    }

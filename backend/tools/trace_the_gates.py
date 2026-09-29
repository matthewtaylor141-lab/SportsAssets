"""TRACE ONE REAL CANDIDATE THROUGH EVERY ENTRY GATE, AND CLASSIFY EACH BLOCKER.

WHY THIS EXISTS. The owner's instruction: "Do not wait until the end to discover
that the first order cannot qualify. Immediately trace one current candidate
through every entry gate and one supported indirect structure through every
management gate."

A test suite answers "does each gate behave correctly in isolation". This answers
a different question: "for a candidate that exists right now, which gate is the
FIRST one that stops it, and what kind of thing is that". Those are not the same,
and passing suites have coexisted with a lane that cannot place an order for
reasons no test was asking about.

EVERY BLOCKER IS CLASSIFIED into exactly one of the four the owner named:

    UNFINISHED_CODE      a path that is not written, or is written and wrong
    MISSING_EVIDENCE     a read that has not happened or conflicts
    ECONOMIC_REJECTION   the arithmetic says no. NOT a blocker to remove
    OWNER_INPUT          a decision only the owner can make

The distinction that matters most is the third. An economic rejection is the
system working, and "fixing" it means forcing an order to demonstrate activity.
Those are reported and left alone.

THE GATES ARE READ FROM THE CODE, IN ORDER. `submit_for_decision` refuses at the
first thing not established, so the trace walks the same sequence and stops where
it stops -- reporting every LATER gate as NOT_REACHED rather than guessing.

Run: python -m tools.trace_the_gates [--dsn DSN] [--json OUT]
Read-only against the database it is pointed at. It never submits: the funded
switch is one of the gates it reports on.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

UNFINISHED_CODE = "UNFINISHED_CODE"
MISSING_EVIDENCE = "MISSING_OR_CONFLICTING_EVIDENCE"
ECONOMIC_REJECTION = "ECONOMIC_REJECTION"
OWNER_INPUT = "MISSING_OWNER_INPUT"

#: How each known refusal is classified. A refusal absent from here is reported
#: as UNCLASSIFIED rather than guessed at, because a wrong class here is worse
#: than an admitted gap: it tells the reader to go and fix the wrong thing.
CLASSIFY = {
    # ── schema and code paths ────────────────────────────────────────
    "THIS_DATABASE_HAS_NO_PAIR_SCHEMA": (
        UNFINISHED_CODE, "migrations 131-135 are not applied here"),
    "FUNDED_SCHEMA_IS_NOT_PRESENT": (
        UNFINISHED_CODE, "the funded columns are absent from this database"),
    "REAL_ORDER_SUBMISSION_IS_DISABLED_IN_CODE": (
        OWNER_INPUT, "FUNDED_SUBMISSION_ENABLED is a code constant and turning "
                     "it on is an owner decision, not an engineering one"),
    "THE_FUNDED_SUBMISSION_PATH_IS_DISABLED": (
        OWNER_INPUT, "the funded switch; the owner has kept it off"),
    "NO_ADAPTER": (UNFINISHED_CODE, "the venue adapter is absent or incomplete"),
    # ── evidence ─────────────────────────────────────────────────────
    "THE_VENUE_CATALOGUE_HAS_NO_ROW_FOR_THIS_CONTRACT": (
        MISSING_EVIDENCE, "us_premap carries no row for this slug"),
    "WHICH_SIDE_OF_THE_INSTRUMENT_IS_HELD_IS_NOT_STATED": (
        MISSING_EVIDENCE, "the position does not state its order_intent"),
    "THE_VENUES_OVERTIME_RULE_FOR_THIS_CONTRACT_IS_UNREAD": (
        MISSING_EVIDENCE, "the venue's settlement prose was not captured"),
    "WHETHER_THE_GRADED_INTERVAL_CAN_END_LEVEL_IS_NOT_ESTABLISHED": (
        MISSING_EVIDENCE, "the (sport, overtime) pair is not in TIE_REACHABLE"),
    "THE_PROSE_STATES_NO_RULE_FOR_THIS_OUTCOME": (
        MISSING_EVIDENCE, "the captured prose does not state this outcome"),
    "NO_APPROVED_MODEL_FOR_THAT_KEY": (
        MISSING_EVIDENCE, "the model registry holds no approved version"),
    "THE_BOOKS_DEPTH_AT_THAT_PRICE_IS_NOT_ESTABLISHED": (
        MISSING_EVIDENCE, "the venue ladder was not read"),
    "THIS_CANDIDATES_OWN_PRICE_WAS_NOT_ESTABLISHED": (
        MISSING_EVIDENCE, "the candidate's own book was not read"),
    "THE_ACCOUNTS_TOTAL_EXPOSURE_IS_NOT_READABLE": (
        MISSING_EVIDENCE, "the venue's own position read needs a credential"),
    # ── owner input ──────────────────────────────────────────────────
    "THE_SUBMISSION_IS_NOT_AUTHORIZED": (
        OWNER_INPUT, "no authorization record for this account, venue and "
                     "limit set"),
    "THE_EXECUTION_GATE_WAS_NOT_AFFIRMATIVE": (
        OWNER_INPUT, "the gate consumed a record and still said no"),
    "THE_APPROVED_LIMITS_ARE_NOT_SET": (
        OWNER_INPUT, "no approved limit set exists to tighten the frozen rails"),
    "THE_ACCOUNT_IS_NOT_ELIGIBLE": (
        OWNER_INPUT, "the account is paused or not selected"),
    "ACCOUNT_ID_NOT_IN_THE_CANONICAL_REGISTRY": (
        OWNER_INPUT, "the account has to be registered before it can be "
                     "selected, and which account is a decision only the owner "
                     "makes"),
    "THE_LANE_HOLDS_NOTHING_TO_PAIR": (
        UNFINISHED_CODE, "there is no inventory to manage. Autonomous ENTRY is "
                         "what creates it, so this is downstream of whatever "
                         "the entry trace reports -- an empty book is not a "
                         "management defect"),
    "THE_VENUES_ORDER_BOOK_WAS_NOT_READ_BY_THIS_TRACE": (
        MISSING_EVIDENCE, "this trace does not hold a venue credential, so no "
                          "candidate's own book was read"),
    "THE_ACCOUNT_IS_PAUSED": (
        OWNER_INPUT, "the pause is deliberate and stays until reconciliation "
                     "and authorization are satisfied"),
    # ── economics: REPORTED, NEVER REMOVED ───────────────────────────
    "A_RAIL_WAS_NOT_MEASURED": (
        MISSING_EVIDENCE, "a predeclared rail has no measurement"),
    "THE_PROPOSAL_IS_OVER_A_RAIL": (
        ECONOMIC_REJECTION, "the proposal exceeds an approved limit"),
    "NO_SECOND_SETTLEMENT_COMPATIBLE_CONTRACT_WAS_DISCOVERED": (
        ECONOMIC_REJECTION, "no admissible hedge exists on this fixture"),
    "THE_STRUCTURE_ITSELF_IS_UNESTABLISHABLE": (
        MISSING_EVIDENCE, "an outcome region has no determined payout"),
    "EVERY_ADMITTED_CANDIDATES_FLOOR_IS_AT_OR_BELOW_ZERO": (
        ECONOMIC_REJECTION, "the arithmetic ran and said no. Left intact: "
                            "forcing this order would be demonstrating activity"),
    "THE_DECISION_WAS_NOT_TO_ACQUIRE_A_SECOND_LEG": (
        ECONOMIC_REJECTION, "another action scored higher; this is the "
                            "comparison working"),
    "THE_DECISION_WAS_NOT_ADMITTED_SO_THERE_IS_NOTHING_TO_SEND": (
        ECONOMIC_REJECTION, "the entry decision itself said no. NOT a blocker "
                            "to remove: an unadmitted decision reaching a "
                            "venue is the failure this gate exists to prevent"),
    "THE_ADMITTED_DECISION_CARRIES_NO_SIZED_PLAN": (
        UNFINISHED_CODE, "the decision was admitted and no marketable fill was "
                         "sized for it"),
    "THE_DECISION_NAMES_NO_VENUE_CONTRACT": (
        MISSING_EVIDENCE, "the decision carries no us_market_slug"),
    "THE_DECISION_NAMES_NO_EVENT_KEY": (
        MISSING_EVIDENCE, "the per-event rail cannot be enforced without one"),
    "THE_DECISION_NAMES_NO_BUY_INTENT": (
        MISSING_EVIDENCE, "both sides of this market share one identifier, so "
                          "an unnamed side is the venue choosing for us"),
}


def classify(refusal):
    if refusal is None:
        return (None, None)
    if refusal in CLASSIFY:
        return CLASSIFY[refusal]
    return ("UNCLASSIFIED",
            "this refusal is not in tools.trace_the_gates.CLASSIFY. Add it "
            "rather than guessing its class")


class Trace:
    """The ordered gate record. Later gates are NOT_REACHED, never assumed."""

    def __init__(self, what):
        self.what = what
        self.gates = []
        self.stopped_at = None

    def gate(self, name, *, passed, refusal=None, detail=None, why=None):
        cls, note = classify(refusal)
        # THREE STATES, NOT TWO. A gate that ran after the first blocker and
        # ALSO refused is reported as WOULD_ALSO_BLOCK, because "the first
        # blocker" is not the same question as "what is the whole blocker set"
        # -- and fixing only the first one and re-running is how a trace comes
        # to be run five times to learn what one run could have said.
        row = {"gate": name,
               "state": ("PASS" if passed else
                         ("WOULD_ALSO_BLOCK" if self.stopped_at else "BLOCKED")),
               "refusal": refusal, "classification": cls,
               "classification_note": note, "why": why, "detail": detail}
        if not passed and self.stopped_at is None:
            self.stopped_at = name
            row["state"] = "BLOCKED"
        self.gates.append(row)
        return passed

    def skip(self, name, why):
        """A gate whose prerequisite never arrived. NOT a blocker.

        Reporting it as BLOCKED with no refusal was worse than saying nothing:
        it put an entry in the blocker list with a blank classification, which
        reads as "something is wrong here and we do not know what".
        """
        self.gates.append({"gate": name, "state": "NOT_EVALUATED",
                           "refusal": None, "classification": None,
                           "classification_note": None, "why": why,
                           "detail": None})

    def not_reached(self, *names):
        for n in names:
            self.gates.append({"gate": n, "state": "NOT_REACHED",
                               "refusal": None, "classification": None,
                               "why": "an earlier gate stopped the trace at %s"
                                      % self.stopped_at})

    def as_dict(self):
        by_class = {}
        for g in self.gates:
            if g["state"] in ("BLOCKED", "WOULD_ALSO_BLOCK") \
                    and g["classification"]:
                by_class.setdefault(g["classification"], []).append(g["gate"])
        return {"what": self.what, "stopped_at": self.stopped_at,
                "gates": self.gates, "blockers_by_class": by_class,
                "reached_the_end": self.stopped_at is None}


def render(traces):
    out = []
    for t in traces:
        d = t.as_dict()
        out.append("=" * 76)
        out.append(d["what"])
        out.append("=" * 76)
        for g in d["gates"]:
            mark = {"PASS": "  ok  ", "BLOCKED": " STOP ",
                    "WOULD_ALSO_BLOCK": " also ",
                    "NOT_EVALUATED": "  n/a ",
                    "NOT_REACHED": "  --  "}[g["state"]]
            out.append("%s %-46s %s" % (mark, g["gate"][:46],
                                        g["refusal"] or ""))
            if g["state"] in ("BLOCKED", "WOULD_ALSO_BLOCK"):
                out.append("       classified: %s" % g["classification"])
                if g["classification_note"]:
                    out.append("       %s" % g["classification_note"])
                if g["why"]:
                    out.append("       why: %s" % str(g["why"])[:200])
        if d["reached_the_end"]:
            out.append("  -> every gate in this sequence passed")
        else:
            out.append("  -> FIRST BLOCKER: %s" % d["stopped_at"])
            for cls, gates in sorted(d["blockers_by_class"].items()):
                out.append("  -> %-34s %s" % (cls, ", ".join(
                    g.split(" ", 1)[1] if " " in g else g for g in gates)))
        out.append("")
    return "\n".join(out)


# ═════════════════════════════════════════════════════════════════════
# THE ENTRY GATES, IN THE ORDER submit_for_decision CHECKS THEM
# ═════════════════════════════════════════════════════════════════════

async def trace_entry(conn, *, account_id, venue, decision_record):
    """One candidate order through every gate between a decision and the wire.

    The sequence mirrors `bettor_funded_execution.submit_for_decision` exactly,
    because that is the function that decides. Nothing here re-implements a
    check: each one is CALLED, and its own refusal is what gets classified.
    """
    from sportsassets import bettor_account_exposure as AE
    from sportsassets import bettor_funded_activation as FA
    from sportsassets import bettor_funded_execution as FX
    from sportsassets import bettor_funded_schema as FS

    t = Trace("ENTRY: one candidate order through every gate to the wire")

    blocked = await FS.require(conn)
    if not t.gate("1 funded schema present", passed=(blocked is None),
                  refusal=(blocked or {}).get("refusal"),
                  why=(blocked or {}).get("why")):
        t.not_reached("2 venue class", "3 order plan complete",
                      "4 account eligible", "5 approved limits exist",
                      "6 effective limits", "7 every rail measured and under",
                      "8 account-wide exposure readable",
                      "9 authorization record consumed",
                      "10 execution gate affirmative",
                      "11 FUNDED_SUBMISSION_ENABLED", "12 adapter surface")
        return t

    klass = str(decision_record.get("venue_class") or FA.VENUE_FUNDED)
    t.gate("2 venue class", passed=klass in FX.ALLOWED_VENUE_CLASSES,
           refusal=(None if klass in FX.ALLOWED_VENUE_CLASSES
                    else "THE_VENUE_CLASS_IS_NOT_A_FUNDED_ONE"),
           detail={"klass": klass, "allowed": list(FX.ALLOWED_VENUE_CLASSES)})

    plan = FX.plan_from_decision(decision_record)
    if not t.gate("3 order plan complete", passed=bool(plan.get("ok")),
                  refusal=plan.get("refusal"), why=plan.get("why"),
                  detail={k: plan.get(k) for k in
                          ("us_market_slug", "intent", "quantity",
                           "limit_price", "collateral_usd")}):
        t.not_reached("4 account eligible", "5 approved limits exist",
                      "6 effective limits", "7 every rail measured and under",
                      "8 account-wide exposure readable",
                      "9 authorization record consumed",
                      "10 execution gate affirmative",
                      "11 FUNDED_SUBMISSION_ENABLED", "12 adapter surface")
        return t

    sel = await FA.account_selection(conn, account_id)
    t.gate("4 account eligible", passed=bool(sel.get("ok")),
           refusal=sel.get("refusal"), why=sel.get("why"),
           detail={"account_id": sel.get("account_id"),
                   "state": sel.get("state")})

    approved = await FX._approved(conn)
    t.gate("5 approved limits exist", passed=bool(approved),
           refusal=(None if approved else "THE_APPROVED_LIMITS_ARE_NOT_SET"),
           detail=(approved or None))

    eff = FX.EX.effective_limits(approved) if approved else None
    t.gate("6 effective limits are MIN(frozen, approved)",
           passed=bool(eff), detail=(eff or {}).get("effective"))

    if approved:
        rails = await FX.check_rails(
            conn, plan, eff["effective"],
            account_id=sel.get("account_id") or account_id, venue=venue,
            operation_id="trace-the-gates")
        rail_ok = (rails.get("ok", True) and not rails.get("unmeasured")
                   and not rails.get("over"))
        rail_refusal = None
        if rails.get("refusal"):
            rail_refusal = rails["refusal"]
        elif rails.get("unmeasured"):
            rail_refusal = "A_RAIL_WAS_NOT_MEASURED"
        elif rails.get("over"):
            rail_refusal = "THE_PROPOSAL_IS_OVER_A_RAIL"
        t.gate("7 every rail measured and under", passed=rail_ok,
               refusal=rail_refusal,
               detail={"unmeasured": rails.get("unmeasured"),
                       "over": rails.get("over")},
               why=rails.get("why"))
    else:
        t.not_reached("7 every rail measured and under")

    exposure = await AE.account_exposure(conn,
                                        account_id=sel.get("account_id")
                                        or account_id,
                                        venue_positions=None)
    exp_ok = bool(exposure.get("ok")) and exposure.get("total_usd") is not None
    t.gate("8 account-wide exposure readable", passed=exp_ok,
           refusal=(None if exp_ok
                    else "THE_ACCOUNTS_TOTAL_EXPOSURE_IS_NOT_READABLE"),
           why=exposure.get("why"),
           detail={"state": exposure.get("state"),
                   "total_usd": exposure.get("total_usd")})

    auth = FX.EX.authorize_submission(
        account_id=sel.get("account_id") or account_id, venue=venue,
        authorization=FA._obj(await FA._state(conn, FA.AUTHORIZATION_KEY)),
        approved_limits=approved or {}, account_exposure=exposure,
        proposed_cost_usd=float(plan.get("collateral_usd") or 0.0), now=0.0)
    t.gate("9 authorization record consumed",
           passed=bool(auth.get("authorization_consumed")),
           refusal=(None if auth.get("authorization_consumed")
                    else "THE_SUBMISSION_IS_NOT_AUTHORIZED"),
           why=auth.get("refusal"))
    t.gate("10 execution gate affirmative", passed=bool(auth.get("ok")),
           refusal=(None if auth.get("ok")
                    else "THE_EXECUTION_GATE_WAS_NOT_AFFIRMATIVE"),
           why=auth.get("refusal"))
    t.gate("11 FUNDED_SUBMISSION_ENABLED",
           passed=bool(FX.FUNDED_SUBMISSION_ENABLED),
           refusal=(None if FX.FUNDED_SUBMISSION_ENABLED
                    else "THE_FUNDED_SUBMISSION_PATH_IS_DISABLED"),
           detail={"constant": FX.FUNDED_SUBMISSION_ENABLED,
                   "and_then": "three further boundaries remain after it"})
    missing = []
    try:
        mod = FX._adapter(None)
        missing = [n for n in FX.ADAPTER_SURFACE if not hasattr(mod, n)]
    except Exception as exc:                                    # noqa: BLE001
        missing = ["<adapter import failed: %s>" % type(exc).__name__]
    t.gate("12 adapter surface", passed=not missing,
           refusal=(None if not missing else "NO_ADAPTER"), detail=missing)
    return t


async def _management_from_the_database(conn, a):
    """Find a real held position in this database and trace it.

    THE POSITION IS READ, NOT CONSTRUCTED. `open_entry_positions` is the same
    reader the scheduled pass uses, so the dict the trace walks is the dict
    production walks -- including `order_intent`, which is what binds the held
    leg to its side.
    """
    from sportsassets import bettor_funded_book as FB

    positions = await FB.open_entry_positions(conn)
    if a.intent:
        positions = [p for p in positions if p.get("intent_id") == a.intent]
    if not positions:
        t = Trace("MANAGEMENT: one held position through every gate")
        t.gate("0 an open held position exists", passed=False,
               refusal="THE_LANE_HOLDS_NOTHING_TO_PAIR",
               why=("this database has no open funded ENTRY position, so there "
                    "is no inventory to manage. Autonomous ENTRY is what "
                    "creates one, and the entry trace above says what stops it"))
        return t
    pos = dict(positions[0])

    async def _prose(slug):
        """THE VENUE'S SETTLEMENT PROSE READ -- the only substituted transport
        here besides the book. The TEXT is whatever this database's own
        `us_premap` row was seeded with, and where nothing was captured the read
        returns nothing, which the gates then refuse."""
        row = await conn.fetchrow(
            "SELECT rules_text FROM us_premap_rules WHERE market_slug=$1"
            if await conn.fetchval("SELECT to_regclass('us_premap_rules') "
                                   "IS NOT NULL") else "SELECT NULL::text",
            slug) if slug else None
        text = (row or {}).get("rules_text") if row else None
        return {"ok": bool(text), "rules_text": text,
                "source": "us_premap_rules in this database", "read_at": 0.0}

    async def _quote(slug, side=None):
        if not a.with_book:
            return {"ok": False,
                    "refusal": "THE_VENUES_ORDER_BOOK_WAS_NOT_READ_BY_THIS_TRACE"}
        # A SUBSTITUTED READ, LABELLED AS ONE. This is not a claim about any
        # venue's book; it exists so the gates AFTER the book read can be
        # evaluated at all, which is what separates "the code is unfinished"
        # from "the evidence is missing".
        return {"ok": True, "price": a.book_price, "depth_qty": a.book_depth,
                "source": "SUBSTITUTED_BY_trace_the_gates_NOT_A_VENUE_READ"}

    import time as _time
    at = float(a.at) if a.at is not None else _time.time()
    return await trace_management(conn, account_id=pos.get("account_id"),
                                  venue=pos.get("venue"), position=pos, at=at,
                                  prose_reader=_prose, quoter=_quote)


async def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("RN1X_TEST_DSN", ""))
    ap.add_argument("--account", default="acct-trace")
    ap.add_argument("--venue", default="PMUS_TRACE")
    ap.add_argument("--slug", default="aec-mlb-bos-nyy-2026-10-05")
    ap.add_argument("--json", default=None)
    ap.add_argument("--management", action="store_true",
                    help="also trace one held position through the management "
                         "gates. Needs a held funded intent and its catalogue "
                         "rows in the database this is pointed at")
    ap.add_argument("--with-book", action="store_true",
                    help="supply a SUBSTITUTED order-book read so the gates "
                         "downstream of it are evaluated. This distinguishes "
                         "'unfinished code' from 'missing evidence': with the "
                         "read present, a gate that still blocks is code")
    ap.add_argument("--book-price", type=float, default=0.30)
    ap.add_argument("--book-depth", type=float, default=500.0)
    ap.add_argument("--at", type=float, default=None,
                    help="the cycle instant, as an epoch float. Defaults to "
                         "now. NOT zero: the fee schedule is keyed by DATE, and "
                         "at=0.0 is 1970 -- which my own first run of this tool "
                         "reported as a fee the schedule would not price")
    ap.add_argument("--intent", default=None,
                    help="the held intent to manage; the first open ENTRY "
                         "otherwise")
    a = ap.parse_args(argv)
    if not a.dsn:
        print("no DSN: pass --dsn or set RN1X_TEST_DSN")
        return 2

    import asyncpg
    from sportsassets import bettor_funded_activation as FA

    conn = await asyncpg.connect(a.dsn)
    traces = []
    try:
        if a.management:
            traces.append(await _management_from_the_database(conn, a))
        traces.append(await trace_entry(
            conn, account_id=a.account, venue=a.venue,
            # A DECISION IN THE SHAPE `plan_from_decision` ACTUALLY READS --
            # a `bettor_external_shadow.evaluate` record. My first draft of
            # this tracer invented the field names and the plan gate refused
            # NOT_ADMISSIBLE, which is the gate working and my record being
            # wrong. The tracer is only useful if its input is the real shape.
            decision_record={
                "decision_id": "trace-1", "venue_class": FA.VENUE_FUNDED,
                "admissible": True,
                "us_market_slug": a.slug,
                "event_key": "mlb-bos-nyy-2026-10-05",
                "order_intent": "ORDER_INTENT_BUY_LONG",
                "execution_plan": {"execution": {
                    "size": 10, "vwap": 0.55, "limit_price": 0.55}},
                "action": "ENTER"}))
    finally:
        await conn.close()
    text = render(traces)
    print(text)
    if a.json:
        with open(a.json, "w") as fh:
            json.dump([t.as_dict() for t in traces], fh, indent=1, default=str)
    return 0



# ═════════════════════════════════════════════════════════════════════
# THE MANAGEMENT GATES, FOR ONE SUPPORTED INDIRECT STRUCTURE
# ═════════════════════════════════════════════════════════════════════

async def trace_management(conn, *, account_id, venue, position, at,
                           prose_reader=None, quoter=None):
    """One held position through every gate between inventory and a hedge order.

    Drives the REAL `funded_pair_inputs`, the REAL `discover` and the REAL
    ranking. Nothing is injected: `prose_reader` and `quoter` stand in for two
    HTTP transports and nothing else, which is the same substitution the
    integration suite makes.
    """
    from sportsassets import bettor_funded_pair_cycle as PC
    from sportsassets.workers import ext_pinnacle_loop as LOOP

    t = Trace("MANAGEMENT: one held position through every gate to a hedge order")
    facts = await LOOP.funded_pair_inputs(
        conn, position, at=at, account_id=account_id, venue=venue,
        prose_reader=prose_reader, quoter=quoter)
    unavailable = list(facts.get("unavailable") or ())

    held = facts.get("held_leg")
    t.gate("1 held leg built from catalogue and prose", passed=held is not None,
           refusal=(None if held is not None else
                    (unavailable[0] if unavailable else
                     "THE_VENUE_CATALOGUE_HAS_NO_ROW_FOR_THIS_CONTRACT")),
           detail={"condition_id": getattr(held, "condition_id", None),
                   "unavailable": unavailable})

    cands = list(facts.get("candidate_legs") or ())
    # THE REAL REFUSAL, NOT A PLACEHOLDER. My first draft hardcoded
    # NO_SECOND_SETTLEMENT_COMPATIBLE_CONTRACT and so classified a missing
    # ORDER-BOOK READ as an economic rejection -- which is exactly the
    # misclassification this tool exists to prevent, and it appeared in the
    # tool itself on its first run. The supplier reports why each sibling was
    # refused; that is what gets classified.
    cread = facts.get("candidate_legs_read") or {}
    refused = list(cread.get("refused") or ())
    first_refusal = None
    if not cands:
        seen = [r.get("refusal") for r in refused if r.get("refusal")]
        first_refusal = (seen[0] if seen else
                         "NO_SECOND_SETTLEMENT_COMPATIBLE_CONTRACT_WAS_"
                         "DISCOVERED")
    t.gate("2 candidate legs built", passed=bool(cands),
           refusal=first_refusal,
           detail={"n": len(cands), "examined": cread.get("examined"),
                   "ids": [getattr(c, "condition_id", None) for c in cands][:6],
                   "refusal_counts": {
                       r: sum(1 for x in refused if x.get("refusal") == r)
                       for r in sorted({x.get("refusal") for x in refused
                                        if x.get("refusal")})}})

    tie = facts.get("sport_permits_tie")
    t.gate("3 tie partition established", passed=tie is not None,
           refusal=(None if tie is not None else
                    "WHETHER_THE_GRADED_INTERVAL_CAN_END_LEVEL_IS_NOT_"
                    "ESTABLISHED"),
           detail=facts.get("sport_permits_tie_read"))

    found = {"admitted": [], "rejected": [], "ok": False}
    if held is not None and cands and tie is not None:
        found = PC.discover(held_leg=held, candidate_legs=cands,
                            sport_permits_tie=tie,
                            fixture_can_void=bool(
                                facts.get("fixture_can_void", True)),
                            fixture_can_postpone=bool(
                                facts.get("fixture_can_postpone", True)))
    admitted = list(found.get("admitted") or ())
    if held is None or not cands or tie is None:
        t.skip("4 at least one settlement-compatible structure",
               "no held leg, no candidates or no established partition, so "
               "nothing was classified")
    else:
        t.gate("4 at least one settlement-compatible structure",
               passed=bool(admitted),
               refusal=(None if admitted else found.get("refusal")),
               detail={"examined": found.get("examined"),
                   "rejected": [{"id": r.get("condition_id"),
                                 "refusal": r.get("refusal"),
                                 "undetermined": r.get("undetermined_regions")}
                                for r in (found.get("rejected") or ())][:4]})

    ranking = {"ranked": [], "not_rankable": []}
    if admitted:
        ranking = PC.rank_admitted(
            admitted, details=facts.get("candidate_leg_details"),
            wanted_qty=position.get("residual_qty"),
            fee_usd=facts.get("fee_usd"), fee_basis=facts.get("fee_basis"),
            shared_depth=facts.get("depth"), held_leg=held,
            sport_permits_tie=tie,
            fixture_can_void=bool(facts.get("fixture_can_void", True)),
            fixture_can_postpone=bool(facts.get("fixture_can_postpone", True)))
    ranked = list(ranking.get("ranked") or ())
    nr = list(ranking.get("not_rankable") or ())
    if not admitted:
        t.skip("5 every admitted candidate priced, sized and valued",
               "no structure was admitted, so there was nothing to price")
    else:
        t.gate("5 every admitted candidate priced, sized and valued",
               passed=bool(ranked),
           refusal=(None if ranked else (nr[0].get("refusal") if nr else None)),
           detail={"ranked": [{"id": r["condition_id"],
                               "side": r.get("side"),
                               "score_usd": r.get("score_usd"),
                               "score_is": r.get("score_is")}
                              for r in ranked],
                       "not_rankable": [{"id": r.get("condition_id"),
                                         "refusal": r.get("refusal")}
                                        for r in nr][:4]})

    best = ranking.get("best_admitted")

    # ── GATE 6, CORRECTED BY THE TRACE ITSELF ────────────────────────
    #
    # MY FIRST VERSION REQUIRED A POSITIVE FLOOR, and it blocked at every hedge
    # price from $0.30 down to $0.05 -- which is what made me look. Two things
    # were wrong with it.
    #
    # FIRST, IT IS NOT THE SYSTEM'S CRITERION. `bettor_funded_decision.decide`
    # runs policy EXPECTED_NET_VALUE. Nothing in it requires a positive worst
    # case, and its own source says so: "a holding can have positive expected
    # value while its worst case is losing the stake." So a gate demanding a
    # positive floor tests a rule the system does not have, and would report
    # ECONOMIC_REJECTION where the system would rank the candidate normally.
    #
    # SECOND, THE FLOOR IS STRUCTURALLY CAPPED under the cancellation reading
    # this repository books. A basis refund makes the VOID cell exactly
    # breakeven, so the gross floor cannot exceed $0.00 however cheap the hedge
    # is, and the fee then puts it below. That is why the price sweep never
    # moved it, and it is also why the +$1.35 only ever appeared under a 50-50
    # cancellation rule.
    #
    # So the floor is REPORTED and the gate is "the ranking produced a winner".
    t.gate("6 the ranking produced a selectable winner", passed=best is not None,
           refusal=(None if best is not None else
                    "NO_SECOND_SETTLEMENT_COMPATIBLE_CONTRACT_WAS_DISCOVERED"),
           detail={"winner": (best or {}).get("condition_id"),
                   "scores": [r.get("score_usd") for r in ranked],
                   "floors_are_not_the_criterion": (
                       "the decision runs EXPECTED_NET_VALUE. A negative "
                       "fee-adjusted floor does not exclude a candidate, and "
                       "under a basis-refund cancellation rule the floor is "
                       "capped at -fee for every hedge at every price")})

    regions = facts.get("region_probabilities")
    t.gate("7 region probabilities from an approved model",
           passed=regions is not None,
           refusal=(None if regions is not None
                    else "NO_APPROVED_MODEL_FOR_THAT_KEY"),
           detail=facts.get("region_probability_read"))

    gid = position.get("portfolio_group_id")
    t.gate("8 the held position belongs to a portfolio group",
           passed=gid is not None,
           refusal=(None if gid is not None
                    else "THE_HELD_POSITION_BELONGS_TO_NO_PORTFOLIO_GROUP"),
           detail={"group_id": gid})

    plan, plan_ref = None, None
    if best is not None and gid is not None:
        try:
            plan = PC.acquisition_plan_for(
                winner=best, ranked_row=(ranking.get("best") or {}),
                account_id=account_id, venue=venue, group_id=gid,
                held_position=position, fee_usd=facts.get("fee_usd"),
                inputs_expire_at=at + 300.0)
        except PC.PlanRefused as exc:
            plan_ref = exc.as_dict()
    if best is None or gid is None:
        t.skip("9 the winner becomes a complete, side-aware order",
               "no ranked winner or no portfolio group, so no order was built")
        return t
    t.gate("9 the winner becomes a complete, side-aware order",
           passed=plan is not None,
           refusal=((plan_ref or {}).get("refusal")
                    if plan is None else None),
           why=(plan_ref or {}).get("why"),
           detail=(plan.as_dict() if plan is not None else None))
    return t

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

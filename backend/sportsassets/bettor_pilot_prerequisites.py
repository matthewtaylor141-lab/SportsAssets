"""EVERY REMAINING PREREQUISITE, SORTED BY WHO CAN ACTUALLY CLEAR IT.

WHY THIS EXISTS. The last report listed eight unmet prerequisites and then said
the first four were "yours to supply". That reading was wrong in a specific and
misleading way: it implied that naming an account and approving limits would
carry the rest. It would not. `venue_book_freshness_basis`,
`settlement_compatibility`, `market_scope_metadata` and
`an_eligible_market_with_one_coherent_chain` are statements about MARKET AND
SOURCE EVIDENCE, and no owner decision makes a quote fresher or reconciles two
bookmakers' abandonment rules.

So each prerequisite is classified into exactly one of three owners, and the
classification is a TABLE rather than a sentence, so a readback can print it
and a test can pin it:

  ENGINEERING        code that does not exist or does not work yet. Mine.
  MARKET_EVIDENCE    a measurement or a source fact that is missing, stale or
                     in conflict. NOBODY'S to decide -- it is either observed
                     or it is not, and it can change minute to minute.
  OWNER_DECISION     a choice only the capital's owner can make.

AND THE THREE DO NOT SUBSTITUTE FOR EACH OTHER. `clears()` states, for each
category, what clearing it does NOT clear -- because the failure mode here is
not missing information, it is a plausible-sounding chain of implication.
"""

from __future__ import annotations

import time

ENGINEERING = "ENGINEERING_STILL_INCOMPLETE"
MARKET_EVIDENCE = "MARKET_OR_SOURCE_EVIDENCE_MISSING_OR_CONFLICTING"
OWNER_DECISION = "OWNER_DECISION_REQUIRED"

CATEGORIES = (ENGINEERING, MARKET_EVIDENCE, OWNER_DECISION)

#: Every readiness check `bettor_funded_activation.readiness` emits, mapped to
#: the ONE party who can clear it, with the reason the mapping is that way.
#: A check absent from this table is reported as UNCLASSIFIED rather than
#: guessed at, for the same reason the evaluability table refuses to guess.
OWNER_OF = {
    "funded_submission_disabled": (
        ENGINEERING,
        "a code constant. It is currently MET because submission is off, and "
        "it is the one check that turning submission on would break"),
    "account_selected_and_clean": (
        OWNER_DECISION,
        "which account, and whether its accounting is resolved. The second "
        "half needs a venue reconciliation, which is engineering + evidence, "
        "but the CHOICE of account is the owner's"),
    "limits_recorded_and_complete": (
        OWNER_DECISION,
        "four numbers only the capital's owner can state: capital, per-order, max exposure, daily loss stop"),
    "limits_approved_by_the_owner": (
        OWNER_DECISION,
        "an approval is an act of the owner by definition; no measurement can stand in for it"),
    "approved_limits_tighten_the_enforced_rails": (
        OWNER_DECISION,
        "arithmetic on the owner's own numbers against the frozen rails; it "
        "resolves the moment the numbers exist"),
    "venue_book_freshness_basis": (
        MARKET_EVIDENCE,
        "whether the venue stated a transact time we could measure an age "
        "from. No decision makes a book fresher, and the current census has "
        "25 candidates refused QUOTE_STALE at the source's own 30 s rule"),
    "settlement_compatibility": (
        MARKET_EVIDENCE,
        "whether the two sides' published payout rules agree. 18 candidates "
        "carry a STATED conflict and 1 a rule we do not hold; an approval "
        "cannot reconcile them"),
    "market_scope_metadata": (
        MARKET_EVIDENCE,
        "whether the venue's own market-type metadata pins the scope of the "
        "contract. It is the venue's field, not ours"),
    "an_autonomous_entry_was_admitted_unwaived": (
        MARKET_EVIDENCE,
        "a candidate has to clear every gate on its own evidence. The last "
        "cycle admitted 0 of 66, and that is the engine working"),
    "an_eligible_market_with_one_coherent_chain": (
        MARKET_EVIDENCE,
        "one market where every link holds on ONE evidence row. This cannot "
        "be assembled from unrelated rows and cannot be decided"),
}

#: Prerequisites that are NOT readiness checks -- things this system needs
#: that `readiness` does not ask about, because they live outside its tables.
#: Listed explicitly so the report is the whole set and not the convenient
#: subset.
BEYOND_READINESS = (
    {"id": "funded_execution_connection",
     "category": ENGINEERING,
     "state": "BUILT_AND_TESTED_DISABLED",
     "what": ("a path from a qualifying decision to the existing venue "
              "adapter. `bettor_funded_execution` now connects them and is "
              "tested against the real `pmus.submit_fok` with transport "
              "substituted at `_get_client`"),
     "remaining": "FUNDED_SUBMISSION_ENABLED is False; flipping it is a code "
                  "change"},
    {"id": "scheduled_lane_calls_the_connection",
     "category": ENGINEERING,
     "state": "WIRED_AND_TESTED_DISABLED",
     "what": ("`workers/ext_pinnacle_loop` now calls the funded connector "
              "from its cycle, on the SAME admitted record the shadow "
              "inventory is written from, via `_funded_attempt`"),
     "remaining": ("it sends nothing: with the shipped switches the "
                   "connector refuses before the adapter is reached, and "
                   "that refusal is reported in the cycle tally")},
    {"id": "durable_funded_book",
     "category": ENGINEERING,
     "state": "BUILT_AND_TESTED",
     "what": ("`bettor_funded_book` on migrations 125-127: intent committed "
              "BEFORE the request leaves, venue order and fill identities "
              "retained, fills idempotent on the venue's own id, recovery "
              "that reconciles and cannot resubmit, and exposure preserved "
              "when the venue cannot establish an outcome. Migration 126 "
              "separated ORDER TERMINALITY from INVENTORY CLOSURE, so a "
              "FILLED entry keeps its holding in every rail until an "
              "evidenced exit or an authoritative settlement removes it; "
              "expected and observed fees are separate columns with a state; "
              "and realised P&L and drawdown are sums over "
              "`bettor_funded_economics` rather than constants"),
     "remaining": ("no funded fill exists yet, so the accounting is exercised "
                   "against substituted transport only")},
    {"id": "funded_servicing_and_the_loss_stop",
     "category": ENGINEERING,
     "state": "BUILT_AND_TESTED_DISABLED",
     "what": ("`bettor_funded_management` services what the lane holds: "
              "exits through the same `pmus.submit_fok(..., sell=True)` the "
              "desk uses, cancels, settlement from "
              "`bettor_venue_settlement_probe` (only REPORTED and VOID are "
              "authoritative -- a converged price inference is not), and one "
              "recurring pass the scheduled worker runs every cycle. The loss "
              "stop is MAX_DRAWDOWN in the owner\'s effective limit set, "
              "measured over the economics ledger and enforced by the same "
              "`check_rails` every entry passes"),
     "remaining": ("`FUNDED_EXIT_SUBMISSION_ENABLED` is False, SEPARATELY "
                   "from the entry switch, so stopping new exposure can never "
                   "strand inventory. The reconciliation and settlement reads "
                   "are not gated at all, because a book that cannot be "
                   "reconciled while the lane is paused is worse than one "
                   "that cannot trade. And with no closed funded position the "
                   "measured drawdown is a sum over an empty set -- the "
                   "measurement is real, the threshold is an owner input")},
    {"id": "funded_exit_price_source",
     "category": MARKET_EVIDENCE,
     "state": "NOT_ESTABLISHED",
     "what": ("an exit needs a limit, and this lane has no funded MARK it "
              "would stand behind. `submit_exit` refuses without a supplied "
              "price and invents none; `pnl()` reports unrealised P&L as "
              "UNMEASURED by name rather than as zero"),
     "remaining": ("until a mark source is established, an exit is an "
                   "operator decision with an operator price. Inventing one "
                   "would manufacture the very number the exit measures")},
    {"id": "venue_balance_read",
     "category": ENGINEERING,
     "state": "BUILT",
     "what": ("`pmus.balances()` reads GET /v1/account/balances via the "
              "SDK's `client.account.balances()` and returns the venue's "
              "figures verbatim, with absent fields reported absent"),
     "remaining": ("it cannot be exercised against production without a "
                   "venue credential; without one it raises, which onboarding "
                   "reports as UNREADABLE and which blocks")},
    {"id": "venue_credential",
     "category": OWNER_DECISION,
     "state": "ABSENT",
     "what": "PMUS_KEY_ID / PMUS_SECRET_KEY are not set on the service",
     "remaining": ("provisioning them is the owner's, and doing so is not by "
                   "itself an authorization to trade")},
    {"id": "account_venue_reconciliation",
     "category": ENGINEERING,
     "state": "BUILT_BLOCKED_ON_A_CREDENTIAL",
     "what": ("`bettor_account_onboarding` reconciles balances, positions, "
              "open orders and executions -- all four now reaching real "
              "adapter calls -- before any account is eligible"),
     "remaining": ("without a venue credential every read raises, so all four "
                   "answer UNREADABLE and no account can be marked eligible. "
                   "That is the reconciliation working, not a gap in it")},
    {"id": "source_calibration",
     "category": MARKET_EVIDENCE,
     "state": "NOT_ESTABLISHED",
     "what": ("the de-vigged Pinnacle probability is the SOURCE'S number. "
              "Its calibration against outcomes is measured by "
              "`external_source_calibration` and is not established"),
     "remaining": ("owner-approved entries under the research waiver are "
                   "labelled UNCALIBRATED_RESEARCH_SHADOW precisely because "
                   "of this. An approval does not calibrate a source")},
    {"id": "clock_sensitive_test_defects",
     "category": ENGINEERING,
     "state": "OPEN",
     "what": ("two pre-existing defects in the mirror and retention lanes "
              "that move the gate's failure COUNT for reasons unrelated to "
              "any diff"),
     "remaining": ("recorded in research/evidence/"
                   "CLOCK_SENSITIVE_TEST_DEFECTS.json; neither is fixed")},
    {"id": "max_event_exposure_blindness",
     "category": ENGINEERING,
     "state": "OPEN_IN_THE_SHADOW_LANE_CLOSED_IN_THE_FUNDED_ONE",
     "what": ("the SHADOW lane's OPEN_BOOK_SQL selects no event key, so its "
              "event rail cannot see siblings. The FUNDED lane carries a "
              "canonical event key on every intent (NOT NULL) and enforces "
              "MAX_EVENT_EXPOSURE per event against pending collateral"),
     "remaining": ("the shadow lane's blindness is unchanged and still "
                   "recorded. The funded lane no longer depends on it")},
    {"id": "one_position_at_a_time",
     "category": ENGINEERING,
     "state": "ENFORCED_BY_THE_DATABASE",
     "what": ("a UNIQUE index over the OPEN-POSITION subset "
              "(`bettor_funded_one_open_position`, migration 126) makes a "
              "second concurrent submission fail at the database, whatever "
              "the interleaving -- including the case where the first order "
              "FILLS between the second caller\'s rail check and its insert. "
              "Its predecessor was over the OUTSTANDING-ORDER subset and so "
              "released the slot at the moment a position was actually owned; "
              "it applies to ENTRY alone, because refusing an exit on this "
              "rule is what strands inventory"),
     "remaining": ("it was a sentence in a proposal until this index existed; "
                   "a SELECT-then-INSERT check could not have done it")},
)


def clears() -> dict:
    """WHAT CLEARING EACH CATEGORY DOES NOT CLEAR.

    Written as data because the mistake being prevented is an implication, and
    an implication is best refused explicitly.
    """
    return {
        OWNER_DECISION: {
            "clears": ["which account", "the four limit numbers",
                       "the approval itself",
                       "whether the approved set tightens the frozen rails",
                       "provisioning a venue credential"],
            "does_not_clear": [
                "venue_book_freshness_basis -- a decision does not make a "
                "quote younger",
                "settlement_compatibility -- an approval does not reconcile "
                "two published payout rules",
                "source calibration -- it is a measurement against outcomes",
                "an_autonomous_entry_was_admitted_unwaived -- a candidate "
                "qualifies on its own evidence or not at all",
                "an_eligible_market_with_one_coherent_chain -- and it may "
                "not be assembled from unrelated rows",
                "any ENGINEERING item"],
        },
        MARKET_EVIDENCE: {
            "clears": ["nothing by decision; it resolves when the market and "
                       "the sources happen to supply it, and it can "
                       "un-resolve minutes later"],
            "does_not_clear": [
                "the owner's choices", "any ENGINEERING item",
                "and it is NOT a defect to be fixed: 0 admitted of 66 is a "
                "working engine reporting nothing to do"],
        },
        ENGINEERING: {
            "clears": ["code that exists and is tested"],
            "does_not_clear": [
                "the owner's choices",
                "market evidence -- writing more code does not make a book "
                "fresher or two rulebooks agree"],
        },
    }


def classify(readiness_checks) -> dict:
    """SORT A LIVE `readiness()` RESULT, plus everything outside it.

    An unmet check whose name is not in `OWNER_OF` is reported UNCLASSIFIED,
    not assigned a plausible owner.
    """
    out = {"at": time.time(), "categories": {c: [] for c in CATEGORIES},
           "unclassified": [], "met": [], "unknown_blocks": True}
    for c in list(readiness_checks or []):
        name = c.get("check")
        met = c.get("met")
        entry = {"id": name, "met": met,
                 "state": ("MET" if met is True else
                           "UNKNOWN" if met is None else "UNMET"),
                 "evidence": c.get("evidence"), "why": c.get("why")}
        if met is True:
            out["met"].append(entry)
            continue
        owner = OWNER_OF.get(name)
        if owner is None:
            out["unclassified"].append(dict(
                entry, note=("this prerequisite has no recorded owner, so "
                             "who can clear it is UNKNOWN")))
            continue
        entry["because"] = owner[1]
        out["categories"][owner[0]].append(entry)
    for extra in BEYOND_READINESS:
        if extra["state"] in ("BUILT_AND_TESTED_DISABLED",):
            # it is engineering that EXISTS; it is still listed, as done
            out["categories"][extra["category"]].append(dict(extra, met=None))
        else:
            out["categories"][extra["category"]].append(dict(extra, met=False))
    out["counts"] = {c: len(v) for c, v in out["categories"].items()}
    out["counts"]["UNCLASSIFIED"] = len(out["unclassified"])
    out["clears"] = clears()
    out["the_thing_this_refuses_to_imply"] = (
        "that naming an account and approving limits clears freshness, "
        "settlement, calibration or candidate qualification. It clears none "
        "of them.")
    return out


def describe() -> dict:
    return {"version": "BETTOR_PILOT_PREREQUISITES_V1",
            "categories": list(CATEGORIES),
            "owner_of": {k: v[0] for k, v in OWNER_OF.items()},
            "beyond_readiness": [x["id"] for x in BEYOND_READINESS],
            "clears": clears()}

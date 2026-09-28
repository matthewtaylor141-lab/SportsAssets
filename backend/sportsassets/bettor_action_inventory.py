"""EVERY ADVERTISED ACTION, AND WHETHER IT CAN ACTUALLY BE EXECUTED.

Owner requirement, verbatim:

    "Every advertised action must either have a verified executable path or
     be explicitly unavailable. An unavailable required capability remains
     unfinished; disabling it is containment, not completion."

So this module is the single place that answers, per action: is there a
dispatch that sends it, is there a test that proves the dispatch sends it,
and if not, WHICH KIND of not.

────────────────────────────────────────────────────────────────────
THE FOUR STATUSES, AND WHY FOUR AND NOT TWO.

"Executable or unavailable" is the right rule, but "unavailable" hides
three different facts that have three different owners and three
different completion paths:

  EXECUTABLE
      A dispatch sends it and a named test proves the order reaches the
      adapter with the intended terms. This is the only status that
      counts as complete.

  NOT_APPLICABLE_ON_THIS_VENUE
      The venue's own mechanics mean the action does not exist here --
      not that we have not built it. TAKE_COMPLEMENT on PMUS is this:
      the venue documents ONE instrument per market, so "buy the
      complement" and "sell our long" are the same order on the same
      book. There is nothing to implement, and implementing something
      would be implementing a fiction.

      THIS IS COMPLETION, NOT CONTAINMENT, and the distinction is not a
      convenience: it rests on the venue's published mechanics, cited
      below, not on our preference.

  REQUIRED_BUT_UNAVAILABLE
      A capability the management report requires, which this system
      cannot perform. UNFINISHED. Disabling it is containment. Each one
      names the specific missing pieces so the completion path is
      concrete rather than "implement the feature".

  BLOCKED_ON_EVIDENCE
      The path exists and would run; an input it requires is not
      established. Owner is whoever can supply the evidence, and the
      action is neither broken nor finished.

────────────────────────────────────────────────────────────────────
WHAT THIS MODULE IS NOT. A gate. `bettor_funded_management.EXECUTABLE_ACTIONS`
is the gate, and `rank_with_hold` enforces it at selection. This is the
REGISTER, cross-checked against that gate by a test so the two cannot
drift -- an inventory that disagreed with the code would be worse than
none, which is the mistake this repository has made twice with
hand-written mutation inventories.
"""

from __future__ import annotations

EXECUTABLE = "EXECUTABLE"
NOT_APPLICABLE = "NOT_APPLICABLE_ON_THIS_VENUE"
REQUIRED_BUT_UNAVAILABLE = "REQUIRED_BUT_UNAVAILABLE"
BLOCKED_ON_EVIDENCE = "BLOCKED_ON_EVIDENCE"

#: The venue facts the NOT_APPLICABLE verdicts rest on. Cited, with the
#: retrieval hash, so a reader can check them rather than trust them.
VENUE_SOURCES = {
    "one_instrument_per_market": {
        "page": "docs.polymarket.us/concepts/orders",
        "retrieved": "2026-09-28",
        "sha256": ("ebe5d70c6820a175e761dbc2a7b88798a292bd45e6dd9397b8a6"
                   "c78e41299635"),
        "quote": ("There's only one instrument per market -- the YES side. "
                  "To trade against an outcome, you sell YES (which is the "
                  "same as buying NO)"),
    },
    "one_central_limit_order_book": {
        "page": "docs.polymarket.us/concepts/market-data",
        "retrieved": "2026-09-28",
        "sha256": ("5f96ff5b577b978179003bcdb1959a0c1178b0e535082ca524e4c"
                   "7622632a021"),
        "quote": ("Polymarket US runs a central limit order book... two "
                  "sides: Bids -- buy orders... Asks (offers) -- sell orders"),
    },
    "collateral_return_is_margin_not_merge": {
        "page": "docs.polymarket.us/market-structure/"
                "mutually-exclusive-collateral-return",
        "retrieved": "2026-09-28",
        "sha256": ("7b56aad70dc5ea4550e31b429c052d7f2600774eb5940b330866f"
                   "1f257a83164"),
        "quote": ("This is a portfolio margin optimization, not a reduction "
                  "in actual risk"),
        "and_it_pairs": ("offsetting SHORT positions in instruments from the "
                         "same mutually exclusive event -- not a long "
                         "against its own complement"),
    },
}

#: THE REGISTER. One entry per action this system advertises anywhere:
#: the ranker's candidate table, `UNIMPLEMENTED_ROUTES`, or the
#: management report's named capabilities.
ACTIONS = {
    # ── EXECUTABLE ────────────────────────────────────────────────────
    "HOLD": {
        "status": EXECUTABLE,
        "sends_an_order": False,
        "dispatch": "bettor_funded_management.manage returns ok with no exit",
        "verified_by": ("test_the_dispatcher_behaviour."
                        "test_a_genuine_hold_succeeds_sending_nothing"),
        "note": ("a successful no-order decision. The capability gate must "
                 "never catch it, which has its own test -- a HOLD excluded "
                 "by `executable_actions` would strand every position whose "
                 "best action is to wait"),
    },
    "DIRECT_EXIT": {
        "status": EXECUTABLE,
        "sends_an_order": True,
        "dispatch": ("bettor_funded_management.select_exit -> submit_exit "
                     "-> pmus.submit_fok"),
        "quantity_rule": "min(residual, size_at_best) -- depth-capped",
        "wire_price": "the best level's api_price",
        "verified_by": ("test_the_funded_lifecycle_demonstration, cases A "
                        "and B: order payload, fills and ledger readback"),
        "gated_by": "FUNDED_EXIT_SUBMISSION_ENABLED (False in shipped code)",
    },
    "REDUCE": {
        "status": EXECUTABLE,
        "sends_an_order": True,
        "dispatch": "same as DIRECT_EXIT",
        "quantity_rule": ("marginal_sale_size: every level whose per-contract "
                          "proceeds after fees beat holding"),
        "wire_price": ("the MARGINAL level's api_price when the quantity "
                       "spans more than one level, else the best level's"),
        "verified_by": "test_a_multi_level_reduce_can_actually_fill",
        "gated_by": "FUNDED_EXIT_SUBMISSION_ENABLED (False in shipped code)",
        "was_not_executable_until": (
            "2026-09-28. The dispatch bounded every exit at the BEST level, "
            "so a REDUCE selected for 10 contracts on a multi-level vwap was "
            "submitted at a price that could fill 4 -- and REDUCE is in "
            "EXECUTABLE_ACTIONS, so it was allowed to win the ranking on an "
            "advantage the order could not realise. It lost no money, "
            "because a limit is never crossed downward. It was still an "
            "advertised action whose differentiating case the dispatch could "
            "not execute"),
        "refuses_rather_than_underfilling": (
            "R_REDUCE_MARGINAL_WIRE_NOT_SUPPLIED when the sale ladder "
            "carries no wire price for the marginal level"),
    },

    # ── NOT APPLICABLE: the venue's mechanics, not our backlog ────────
    "TAKE_COMPLEMENT": {
        "status": NOT_APPLICABLE,
        "sends_an_order": False,
        "why": ("on this venue it is not a distinct action. The venue "
                "documents ONE instrument per market and ONE central limit "
                "order book, so buying NO at `a` IS selling YES at `1-a`: "
                "the same order, the same book, the same depth. There is "
                "nothing here to implement"),
        "rests_on": ["one_instrument_per_market",
                     "one_central_limit_order_book"],
        "this_is_completion_not_containment": (
            "the action is absent because the venue has no such operation, "
            "established from its published mechanics -- not because we "
            "switched it off pending work"),
        "enforced_at": ("bettor_mgmt_select: selection_eligible False with "
                        "ADVANTAGE_IS_THE_SAME_BOOK_QUOTED_TWICE. Still "
                        "PRICED and annotated, because a gap between the bid "
                        "and the complement's ask is now a visible INPUT "
                        "DEFECT worth surfacing"),
        "where_it_would_be_real": (
            "a TWO_TOKEN venue whose YES and NO are separate instruments "
            "with their own books. It is ranked and selected there, and has "
            "no dispatch there either -- so on such a venue it would read "
            "REQUIRED_BUT_UNAVAILABLE, not EXECUTABLE"),
    },

    # ── REQUIRED AND UNAVAILABLE: unfinished, with named gaps ────────
    "FORM_INDIRECT_HEDGE": {
        "status": REQUIRED_BUT_UNAVAILABLE,
        "sends_an_order": False,
        "required_by": ("the management report's indirect-pair capability "
                        "(the Bears/Panthers both-win construction)"),
        "missing": [
            "a destination-venue order plan (no venue is resolved for the "
            "second leg)",
            "inventory and exposure reservation across two venues",
            "partial-execution handling on either leg",
            "restart recovery with one leg filled and one not",
            "two-leg reconciliation and combined accounting",
        ],
        "owner": "engineering",
        "completion_path": (
            "each missing item above is a discrete piece of work. The "
            "reservation and the one-leg-filled recovery are the hard ones: "
            "a hedge half-executed is a NEW directional position, and "
            "nothing currently reserves against that"),
        "and_a_venue_dependency": (
            "the cross-event case is NOT the venue's mutually-exclusive "
            "collateral return, which requires instruments from the SAME "
            "event. Two separate games do not qualify, so no published "
            "capital efficiency supports this construction"),
        "declared_unavailable_at": ("bettor_funded_management."
                                    "UNIMPLEMENTED_ROUTES"),
    },
    "COMPLETE_PAIR": {
        "status": REQUIRED_BUT_UNAVAILABLE,
        "sends_an_order": False,
        "required_by": "the pairing brief's inventory-completion capability",
        "missing": [
            "a merge or netting mechanism that returns cash on a completed "
            "pair -- NOT_IDENTIFIED at the venue",
            "a dispatch branch",
        ],
        "owner": "engineering, blocked on a venue capability",
        "completion_path": (
            "establish whether the venue releases collateral on a completed "
            "pair. Its two published mechanisms do not: directional pairs a "
            "lower-ranked long against a higher-ranked short, and mutually "
            "exclusive pairs shorts against shorts. Both are explicitly "
            "margin optimization, not risk reduction"),
        "rests_on": ["collateral_return_is_margin_not_merge"],
        "declared_unavailable_at": ("bettor_funded_management."
                                    "UNIMPLEMENTED_ROUTES"),
    },
    "MERGE": {
        "status": REQUIRED_BUT_UNAVAILABLE,
        "sends_an_order": False,
        "required_by": "the pairing brief's capital-release capability",
        "missing": ["the venue mechanism itself (MERGE_MECHANISM "
                    "NOT_IDENTIFIED)", "a dispatch branch"],
        "owner": "venue, then engineering",
        "completion_path": (
            "same venue question as COMPLETE_PAIR. Until a merge or "
            "netting-to-cash operation is published or observed, the "
            "candidate's collateral_release stays NOT_IDENTIFIED and the "
            "action cannot be priced honestly, let alone sent"),
        "rests_on": ["collateral_return_is_margin_not_merge"],
        "ranker_blocker": "MERGE_NOT_APPLICABLE",
    },
    "POST_COMPLEMENT": {
        "status": REQUIRED_BUT_UNAVAILABLE,
        "sends_an_order": False,
        "required_by": "the pairing brief's maker-side pairing capability",
        "missing": [
            "a fill probability for OUR resting order (P_FILL_NOT_IDENTIFIED)",
            "a dispatch branch for a resting order in this lane",
            "cancel-and-replace management for an order that does not fill",
        ],
        "owner": "engineering",
        "completion_path": (
            "this action pays nothing until OUR order rests and is taken, so "
            "it cannot be valued without a fill model. It is in "
            "REQUIRES_OUR_FILL for that reason. A measured fill rate from "
            "the maker experiment is the input it needs"),
        "declared_unavailable_at": ("bettor_funded_management."
                                    "UNIMPLEMENTED_ROUTES"),
    },

    # ── BLOCKED ON EVIDENCE: would run, input not established ────────
    "HOLD_TO_SETTLEMENT": {
        "status": BLOCKED_ON_EVIDENCE,
        "sends_an_order": False,
        "why": ("carrying to settlement needs the venue's terminal rules for "
                "this contract. Absent them the terminal value is not "
                "computable, and a priced probability is not a settlement "
                "rule"),
        "blocker": "SETTLEMENT_SEMANTICS_NOT_SUPPLIED",
        "owner": "engineering (capture) then venue (per-condition grading)",
        "completion_path": (
            "the settlement census captured contract-specific scope on all "
            "1,126 evaluation rows; what is unresolved is per-condition "
            "grading -- draw, overtime, void/abandonment. Binary $1.00/$0.00 "
            "settlement is confirmed and does NOT resolve those"),
        "needs_no_order": ("so there is no dispatch to build. When the rules "
                           "are established this becomes a valuation that "
                           "competes in the table, nothing more"),
    },
}

#: Actions that count as complete for the purposes of "the supported
#: operating scope". Derived, never hand-listed.
def executable_actions() -> tuple:
    return tuple(sorted(a for a, v in ACTIONS.items()
                        if v["status"] == EXECUTABLE))


def unfinished_actions() -> tuple:
    """REQUIRED_BUT_UNAVAILABLE only. Containment is not completion."""
    return tuple(sorted(a for a, v in ACTIONS.items()
                        if v["status"] == REQUIRED_BUT_UNAVAILABLE))


def by_status() -> dict:
    out: dict = {}
    for name, v in sorted(ACTIONS.items()):
        out.setdefault(v["status"], []).append(name)
    return out


def describe() -> dict:
    """The register, with the two honest headline numbers."""
    ex = executable_actions()
    unfinished = unfinished_actions()
    return {
        "actions": ACTIONS,
        "by_status": by_status(),
        "executable": ex,
        "executable_count": len(ex),
        "unfinished": unfinished,
        "unfinished_count": len(unfinished),
        "venue_sources": VENUE_SOURCES,
        "the_rule": (
            "every advertised action either has a verified executable path "
            "or is explicitly unavailable, and an unavailable REQUIRED "
            "capability is unfinished -- disabling it is containment, not "
            "completion"),
        "so_the_honest_summary": (
            "%d actions are executable. %d are required and unavailable, "
            "which means this system is NOT complete. One is not applicable "
            "on this venue, which IS complete because the venue has no such "
            "operation. One is blocked on evidence"
            % (len(ex), len(unfinished))),
        "the_gate_is_elsewhere": (
            "bettor_funded_management.EXECUTABLE_ACTIONS is what actually "
            "restricts selection. This is the register, and a test "
            "cross-checks the two so they cannot drift"),
    }

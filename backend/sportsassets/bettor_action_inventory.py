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
    # ── THE COMPLETE AUTHENTICATED API SURFACE (2026-09-28) ────────────
    #
    # WHY THIS ENTRY EXISTS AND WHY IT IS STRONGER THAN THE OTHERS. Every
    # "the venue does not support X" verdict in this register used to rest on
    # X not being MENTIONED in the pages I happened to read. That is an
    # argument from silence and it is weak. This is the venue's own API
    # reference INDEX, so the surface is enumerated rather than sampled, and
    # a missing operation is now a positive finding.
    #
    # THE AUTHENTICATED API IS THREE GROUPS, in the venue's own words:
    #   Orders     "Place, modify, cancel, and query orders"
    #   Portfolio  "View positions and trading activity"
    #   Account    "Check balances and buying power"
    #
    # AND THE ORDERS GROUP IS, IN FULL: create-order, create-multiple-orders,
    # preview-order, modify-order, modify-multiple-orders, cancel-order,
    # cancel-multiple-orders, cancel-all-open-orders, close-position-order,
    # get-order, get-open-orders. Plus combos (create, get) and RFQs.
    #
    # THERE IS NO merge, split, netting, redeem, convert OR combine
    # OPERATION ANYWHERE IN IT. The only position-reducing operations are
    # ORDERS -- `close-position-order` sells into the book at a price; it
    # does not return cash at par against an offsetting holding.
    "authenticated_api_surface_is_enumerated": {
        "page": "docs.polymarket.us/api-reference/introduction",
        "retrieved": "2026-09-28",
        "groups": {
            "Orders": "Place, modify, cancel, and query orders",
            "Portfolio": "View positions and trading activity",
            "Account": "Check balances and buying power",
        },
        "order_operations": (
            "create-order", "create-multiple-orders", "preview-order",
            "modify-order", "modify-multiple-orders", "cancel-order",
            "cancel-multiple-orders", "cancel-all-open-orders",
            "close-position-order", "get-order", "get-open-orders"),
        "also_present": ("POST /v1/combos", "GET /v1/combos",
                         "the RFQ API (7 endpoints)"),
        "no_operation_exists_for": ("merge", "split", "netting", "redeem",
                                    "convert", "combine"),
        "why_that_is_a_finding_not_a_silence": (
            "the index enumerates the surface. An absent operation is "
            "established rather than merely unmentioned, which is what the "
            "earlier verdicts in this register rested on"),
        "and_what_close_position_actually_is": (
            "an ORDER. It sells into the book at a price and pays the "
            "spread; it is not a merge that returns cash at par"),
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
        # ── A CORRECTION I MADE BEFORE SHIPPING IT (2026-09-28) ──────────
        #
        # I was about to record the dependency here as "a SECOND VENUE --
        # none is configured". THE VENUE'S OWN API REFERENCE CONTRADICTS
        # THAT, and I found it by reading the endpoint index rather than
        # reasoning from the mechanics I already knew.
        #
        # `POST /v1/combos` creates a CANONICAL MULTI-LEG INSTRUMENT from
        # 2-10 unique legs, each {symbol, side} with side independently
        # SIDE_BUY or SIDE_SELL, on DIFFERENT markets. It returns a tradable
        # `caoc-...` instrument with INSTRUMENT_STATE_OPEN and its own
        # tickSize. A both-win construction across two markets is exactly
        # the shape that payload describes -- ON THIS VENUE, with no second
        # venue involved.
        #
        # SO THIS ACTION MAY WELL BE BUILDABLE HERE, and my earlier
        # framing -- five engineering items blocked on a venue nobody has
        # chosen -- was wrong about the blocker AND about the venue.
        "status_note": (
            "REQUIRED_BUT_UNAVAILABLE is retained, because what is now "
            "established is a VENUE CAPABILITY, not a verified path. "
            "Promoting the status on a documentation read would repeat the "
            "mistake this register exists to prevent"),
        "venue_capability_found": {
            "endpoint": "POST /v1/combos",
            "page": "docs.polymarket.us/api-reference/combos/overview",
            "retrieved": "2026-09-28",
            "what_it_does": ("creates or returns the canonical combo "
                             "instrument for a set of 2-10 unique legs, each "
                             "{symbol, side} with side SIDE_BUY or SIDE_SELL"),
            "why_it_matters_here": (
                "independent sides on different markets is the indirect-pair "
                "SHAPE. It removes the second-venue premise"),
            "and_it_is_traded_how": (
                "the page's See Also points at the RFQ API for 'combo RFQs "
                "and quotes', so a combo is quoted rather than necessarily "
                "resting on the CLOB"),
        },
        # ── AND WHY THE SHAPE IS NOT THE TRADE (2026-09-28) ────────
        #
        # A SECOND CORRECTION, from reading the combos FAQ that I should
        # have read before writing the entry above. A COMBO IS NOT A
        # SUBSTITUTE FOR THE HEDGE -- IT INVERTS IT.
        #
        #   "Every leg has to resolve the way you took it for the combo
        #    to pay." "A combo is a single position that settles once."
        #   payout = potential x PRODUCT of every leg's value
        #   "[one leg against] and the combo pays $0.00. This holds
        #    however the other legs turn out."
        #
        # A combo is MULTIPLICATIVE; separate holdings are ADDITIVE. On
        # Bears ML + Panthers +4.5, separate holdings pay $1 / $2 / $1 /
        # $1 across the four margin scenarios -- a FLOOR of $1, never
        # zero, which is exactly what makes it a hedge. The combo pays
        # $0.00 in THREE of those four. Substituting it would replace a
        # position with a guaranteed floor by a leveraged bet on the
        # conjunction, in a lane authorized only to REDUCE exposure.
        "it_is_not_a_substitute_for_the_hedge": (
            "the combo payoff is MULTIPLICATIVE (potential x product of "
            "leg values, and one losing leg pays 0.00 however the others "
            "turn out) while separate holdings are ADDITIVE with a floor. "
            "On Bears ML + Panthers +4.5 the separate pair never returns "
            "zero and the combo returns zero in three of four scenarios. "
            "It inverts the risk rather than replicating it"),
        "and_a_void_leg_scales_the_whole_position": (
            "a leg that cannot resolve settles at LFMP and is NOT "
            "removed -- the payout is multiplied by that price, so the "
            "venue's own example takes an $80 combo to $48 with one leg "
            "at 0.60 and to $12 with two at 0.60 and 0.25. A "
            "multiplicative void haircut has no analogue in additive "
            "holdings. LFMP is set by the Settlement Committee and its "
            "decisions are final, so it is a discretionary input"),
        "payoff_source": ("docs.polymarket.us/faqs/combos-faqs, retrieved "
                          "2026-09-28; tables in "
                          "COMBO_IS_NOT_A_HEDGE_2026-09-28.md"),
        "specific_remaining_dependencies": [
            "WHETHER CROSS-EVENT LEGS ARE A VALID COMBINATION. The page says "
            "legs must be open, tradable, supported instruments and that "
            "'invalid combinations are rejected' -- it does not enumerate "
            "what makes a combination invalid. Two legs from two different "
            "games may or may not qualify. NOT ESTABLISHED.",
            "WHETHER A COMBO RELEASES OR ECONOMISES COLLATERAL. A combo is a "
            "NEW instrument; nothing read so far says holding one nets "
            "against inventory already held in its legs.",
            "THE WEEKLY INSTRUMENT QUOTA. Combo creation has a "
            "participant-wide service quota of 1,000 new instruments per "
            "week across ALL accounts and both APIs, resetting Monday 00:00 "
            "UTC. A strategy that mints a combo per opportunity consumes a "
            "SHARED budget, so sizing has to account for it. Returning an "
            "existing canonical combo does not consume quota.",
            "THE EDGE RATE LIMIT. Combo and RFQ creation share 10 requests "
            "per 10 seconds, per API key and per IP.",
            "and then the five engineering items above -- reservation, "
            "partial execution, one-leg-filled recovery, two-leg "
            "reconciliation -- which a combo may simplify by making the pair "
            "ONE instrument rather than two orders. Unverified.",
        ],
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
        # ── ANSWERED THE SAME WAY MERGE WAS (2026-09-28) ─────────────────
        "venue_question_answered": (
            "NO cash-returning mechanism exists on the enumerated "
            "authenticated API. See MERGE: the surface is now enumerated "
            "rather than sampled, so this is a finding and not a silence"),
        # BUT THE PAIR ITSELF MAY BE CONSTRUCTIBLE, which is a DIFFERENT
        # question from whether completing one releases capital. This entry
        # previously collapsed the two into one blocker.
        "and_the_pair_may_be_constructible_anyway": (
            "`POST /v1/combos` builds a 2-10 leg instrument with independent "
            "sides, and combos trade on the Orders API. So the pairing "
            "brief's inventory-completion OUTCOME may be reachable as a "
            "position, even though no operation converts a completed pair "
            "into cash. Two capabilities, previously collapsed into one"),
        "rests_on": ["collateral_return_is_margin_not_merge",
                     "authenticated_api_surface_is_enumerated"],
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
        # ── THE VENUE QUESTION IS NOW ANSWERED (2026-09-28) ──────────────
        #
        # Previously this said "establish whether the venue releases
        # collateral on a completed pair", resting on two documented
        # mechanisms that do not. That was an argument from what those two
        # pages said. The API reference INDEX now enumerates the whole
        # authenticated surface, and there is NO merge, split, netting,
        # redeem, convert or combine operation in it. The only
        # position-reducing operations are orders.
        #
        # SO THE ANSWER IS NO, on the published API, and it is a positive
        # finding rather than an absence of evidence.
        "venue_question_answered": (
            "NO. The enumerated authenticated API has no operation that "
            "nets held positions to cash. `close-position-order` is an "
            "ORDER: it sells into the book and pays the spread"),
        "rests_on": ["collateral_return_is_margin_not_merge",
                     "authenticated_api_surface_is_enumerated"],
        # AND WHY IT IS STILL NOT PROMOTED TO NOT_APPLICABLE. Two things are
        # unread, and either could change the verdict:
        "specific_remaining_dependencies": [
            "WHETHER HOLDING A COMBO THAT OFFSETS EXISTING INVENTORY "
            "ECONOMISES MARGIN. `POST /v1/combos` mints a new instrument; "
            "nothing read says trading one nets against legs already held. "
            "If it does, that is a capital-efficiency route even without a "
            "merge operation. NOT ESTABLISHED.",
            "the institutional API, if one exists beyond the Retail API "
            "these pages document. The combo quota is described as spanning "
            "'both Retail and Institutional APIs', so an Institutional "
            "surface is referenced but was not read.",
        ],
        "why_the_status_is_unchanged": (
            "REQUIRED_BUT_UNAVAILABLE, not NOT_APPLICABLE. Promoting it "
            "would assert that no capital-release route exists, and two "
            "unread things bear on that. The register's whole purpose is to "
            "not make that kind of claim early"),
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
        # ── THE DEPENDENCY, NAMED PRECISELY (2026-09-28) ──────────────
        #
        # "a measured fill rate" was not specific enough to act on, so this
        # is what `bettor_p_fill` actually requires and why it cannot be
        # short-circuited. REQUIRED_SOURCE is
        # BETTOR_NATIVE_ADMITTED_FILLS_REQUIRED, and the module refuses four
        # tempting substitutes BY NAME: whale completion (another account's
        # pairs, under an order policy that is itself NOT_IDENTIFIED),
        # a touch (the price reached our level and nobody traded with us --
        # we were never in the queue), a price move (volume existed
        # somewhere, not at a queue position we never held) and displayed
        # depth (what COULD have traded, excluding hidden liquidity).
        #
        # THE CONSEQUENCE IS AN ORDERING CONSTRAINT, and it is the single
        # most decision-relevant fact in this register: the evidence is
        # BETTOR-NATIVE and PROSPECTIVE. It cannot be recovered later from a
        # tape that never recorded our orders, because there were none. So
        # P_FILL cannot be identified BEFORE real orders rest and are taken,
        # which places POST_COMPLEMENT strictly DOWNSTREAM of funded
        # activation. It is not completable in advance of it, at any level
        # of engineering effort.
        "specific_remaining_dependency": (
            "BETTOR_NATIVE_ADMITTED_FILLS_REQUIRED -- our own resting orders "
            "being taken. Prospective and unrecoverable from history"),
        "cannot_be_completed_before": (
            "real orders rest at the venue. The evidence does not exist yet "
            "and cannot be manufactured from the four refused substitutes, "
            "so this action is DOWNSTREAM of funded activation rather than a "
            "precondition for it"),
        "and_one_fill_is_not_enough": (
            "`bettor_p_fill.ONE_FILL_DOES_NOT_IDENTIFY_IT`. The ladder is "
            "NOT_IDENTIFIED -> PARTIALLY_IDENTIFIED -> IDENTIFIED, so even "
            "after activation this is an accumulation, not a switch"),
        "accumulator_is_wired": (
            "`maker_fill` records the hypothetical quote, its queue position "
            "at insert and what the book did after, which is what makes the "
            "estimate possible on the day the order path exists"),
        # ── AND A ROUTE THAT MAY NOT NEED P_FILL AT ALL (2026-09-28) ─────
        #
        # The P_FILL blocker is specific: it is about OUR ORDER RESTING and
        # being taken, which is the MAKER side. Reading the venue's RFQ and
        # combo pages shows two TAKER-side routes to the same paired
        # inventory, and a taker holds no queue position, so P_FILL does not
        # enter:
        #
        #   1 TRADE THE COMBO DIRECTLY. "Combo instruments can also be
        #     traded directly through the Orders API; using an RFQ is
        #     optional." A combo has its own order book with resting
        #     liquidity, so crossing it is the same shape as DIRECT_EXIT --
        #     which is already EXECUTABLE here.
        #   2 BE THE RFQ REQUESTER. POST /v1/rfqs, read quotes, then
        #     PUT .../accept and PUT .../confirm. You are accepting someone
        #     else's quote, not waiting in a queue.
        #
        # SO THE MAKER PATH'S BLOCKER IS UNCHANGED AND STILL DOWNSTREAM OF
        # ACTIVATION, but the CAPABILITY the pairing brief wants may be
        # reachable without it. That is a materially different position from
        # "blocked on an unmeasurable input", and it is the most actionable
        # thing in this register.
        "a_taker_route_may_avoid_p_fill_entirely": {
            "pages": ("docs.polymarket.us/api-reference/rfqs/overview",
                      "docs.polymarket.us/api-reference/combos/overview"),
            "retrieved": "2026-09-28",
            "route_1": ("cross the combo's own order book through the "
                        "Orders API. The venue states RFQ use is OPTIONAL "
                        "and that combos trade directly"),
            "route_2": ("be the RFQ requester: create, read quotes, accept "
                        "one side, confirm during last look"),
            "why_p_fill_does_not_apply": (
                "both are TAKER-side. P_FILL is the probability that OUR "
                "RESTING order is taken, and a taker holds no queue "
                "position, so the unmeasurable input is not on the path"),
            "and_what_is_still_needed": (
                "the economics. A taker pays the spread, so this route is "
                "only worth taking if the paired position's value exceeds "
                "the cost of crossing -- which is exactly the comparison "
                "the ranker already makes for DIRECT_EXIT and REDUCE, and "
                "which has NOT been run for a combo"),
            "and_one_semantic_the_lane_must_carry": (
                "the venue is explicit that QUOTE_STATUS_EXECUTED and "
                "quoteExecuted mean the paired orders were SUBMITTED and "
                "their IDs recorded -- 'They do not mean the orders "
                "filled.' That is the same submitted-is-not-filled "
                "distinction this lane already enforces, and it would have "
                "to be honoured on the RFQ path too"),
        },
        "specific_remaining_dependencies_for_the_taker_route": [
            "a combo instrument for the pair, which means POST /v1/combos "
            "and therefore the shared 1,000-instrument weekly quota",
            "an executable book read for the combo symbol -- and book "
            "currency on a combo is subject to the SAME unestablished "
            "freshness mechanism that blocks every exit today",
            "the economic comparison, never run for a combo",
            "a dispatch branch, which remains unbuilt",
        ],
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

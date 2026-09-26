"""THE EXTERNAL-VALUATION SHADOW EXPERIMENT, kept apart from everything.

It is its own experiment id, its own table and its own status tile. It
shares the entry gate, the fee schedule and the shadow order engine,
because sharing those is the point -- a valuation that only works through
a bespoke path has not been integrated. It shares NOTHING with RN1-seeded
management: different experiment, different rows, and the frozen pairing
and second-half exit policy are not consulted here at all.

WHAT IT DOES, in one line: compare an external bookmaker's de-vigged
probability against the SAME-VENUE executable ask after costs, and record
the comparison whether or not it clears.

WHAT IT REFUSES TO DO:

  * assume a resting fill. The comparison is against the ASK, crossing,
    because a resting price invents a queue position we never held. The
    execution estimate must be supplied and `p_fill` is never defaulted.
  * invent a second-half signal. This experiment only prices FULL-GAME
    markets; `period` must match explicitly and a segment contract is
    refused by PINNACLE_DEVIG_V1 rather than priced off the full game.
  * touch management's frozen exit policy. There is no exit logic here.
    An admitted entry is recorded as a shadow decision; the RN1X pairing
    and 16% trigger are a different experiment on different rows.
  * submit an order. `order_submitted` is always False and there is no
    code path that sets it True.

WHY IT IS SEPARATE FROM `bettor_fair_value`. That module reports
FV_BETTOR_INDEPENDENT = NOT_IDENTIFIED because the internal challenger was
measured WORSE than the venue price. This experiment does not revisit that
result, does not blend with it, and does not claim to supersede it. It is
a different question: not "is our model better than the market" but "does
a sharp book disagree with this venue by more than the cost of crossing".
"""

from __future__ import annotations

from . import bettor_entry_gate as gate
from . import bettor_pinnacle_devig as devig

EXPERIMENT_ID = "EXT_PINNACLE_DEVIG_V1_SHADOW"
LABEL = ("EXTERNAL BOOKMAKER VALUATION, EXPERIMENTAL SHADOW. Not a trained "
         "proprietary model, not an internally qualified settlement model, "
         "and not shown to be profitable")

#: The control row that enables it. Absence is NOT permission, matching
#: every other loop in this stack.
CONTROL_KEY = "ext_pinnacle_shadow"

#: Minimum net edge per contract, in dollars, before an entry is
#: admissible. Deliberately > 0: at exactly zero the trade is a coin flip
#: that pays the fee, and the engine's own band study says the smallest
#: apparent edges are where thin consensus lives.
MIN_NET_EDGE_PER_CONTRACT = 0.01

#: Require the anchor book plus at least this many distinct sharp books on
#: the SAME outcome. The feed module's note is the reason: "a 1c edge
#: agreed by six books is a signal; the same 1c from one book is a
#: rounding error", and its per-OUTCOME depth exists because event-level
#: depth answered the wrong question (the winner's curse audit).
MIN_OUTCOME_BOOKS = 2

R_CONTROL_OFF = "EXPERIMENT_NOT_ARMED"
R_THIN_OUTCOME = "OUTCOME_DEPTH_BELOW_FLOOR"
R_NO_CREDENTIAL = "ODDS_CREDENTIAL_NOT_PRESENT_ON_THIS_SERVICE"


def credential_present(env=None) -> dict:
    """Is the odds key readable HERE? Reported, never printed.

    The key exists: `EDGE_ODDS_API_KEY` (32 chars) is provisioned on
    edge-shadow and is a repository secret. It is NOT on
    sportsassets-api, where this decision path runs -- env-keys at
    2026-09-23T22:10:54Z lists no odds-feed key at all. Copying a
    production credential between services is refused, so provisioning it
    on the target service is an owner action and this reports the absence
    by name instead of pretending to a feed it cannot reach.
    """
    import os

    src = os.environ if env is None else env
    present = bool(str(src.get("EDGE_ODDS_API_KEY") or "").strip())
    return {
        "present": present,
        "variable": "EDGE_ODDS_API_KEY",
        "refusal": None if present else R_NO_CREDENTIAL,
        "known_locations": ["edge-shadow (Render env)",
                            "EDGE_ODDS_API_KEY (repository secret)"],
        "why": (None if present else
                ("the valuation source is wired and fail-closed: it refuses "
                 "by name rather than reaching for a feed it has no key "
                 "for. Provisioning the key on this service is an owner "
                 "action; a credential is never copied between services "
                 "from here")),
    }


def evaluate(*, contract, quote, market_state, execution_estimate, size,
             risk, fee_fn, now, method=devig.DEFAULT_METHOD,
             outcome_books=None, armed=False,
             min_net_edge_per_contract=MIN_NET_EDGE_PER_CONTRACT,
             extra_refusals=None, payout_is_complement=False,
             execution_plan=None) -> dict:
    """One contract, end to end, through the REAL gate.

    Returns a record that is persisted whether or not it clears, because
    the refusals are the deliverable when nothing clears.

    ── `execution_plan`, AND WHY THE ORDER MATTERS ──────────────────
    The execution estimate, the size and the price all depend on the
    VALUATION: a marketable order's limit is the break-even price the
    belief implies, and the size is the frozen notional at that limit.
    But the valuation is computed HERE, once, including the single
    complement inversion. A caller that wanted to size properly had two
    bad options: recompute the de-vig itself (two valuations that can
    drift), or pass placeholders (which is what the scheduled loop did
    -- `size=1.0` and a hand-written `risk={"permitted": True}`).

    So a caller may instead hand in a CALLABLE, invoked with the fair
    value this function computed, returning the estimate, the size, the
    risk verdict and the price to evaluate at:

        execution_plan(fair_value=float, contract=dict) -> {
            "execution_estimate": {...}, "size": float|None,
            "risk": {...}, "ask": float|None, "detail": {...}}

    One valuation, one inversion, and the sizing still happens outside
    this module. A plan that cannot answer returns None for `size` or
    `ask` and the gate refuses by name -- it is never a reason to fall
    back to the placeholder it replaced.
    """
    val = devig.valuation(contract=contract, quote=quote, now=now,
                          method=method)
    # THE EVENT THE CONTRACT ACTUALLY PAYS ON.
    #
    # The de-vig prices the SELECTION. On this venue a BUY_SHORT leg of
    # the same market pays on the COMPLEMENT of that selection, so the
    # probability to compare against its cost is 1 - p(selection).
    #
    # INVERTED EXACTLY ONCE, HERE. The caller supplies the flag and does
    # not pre-invert; the acquisition price it passes is already in cost
    # space (bettor_book_snapshot.acquisition_ladder). Inverting in both
    # places would silently restore the original outcome and look like a
    # working edge.
    #
    # The de-vig normalises over the COMPLETE outcome set and refuses a
    # partial one, so on a three-way book 1 - p(home) is exactly
    # p(away) + p(draw). It is NOT p(away): "NO home win" includes the
    # draw, and substituting the other team's price would be a different
    # event wearing the same number.
    _p_sel = val.get("probability")
    _p_pay = (None if _p_sel is None
              else (1.0 - float(_p_sel)) if payout_is_complement
              else float(_p_sel))
    rec: dict = {
        "experiment_id": EXPERIMENT_ID,
        "label": LABEL,
        "version": devig.VERSION,
        "source_class": devig.SOURCE_CLASS,
        "devig_method": method,
        "contract": dict(contract),
        "valuation": val,
        "probability": _p_pay,
        "probability_of_selection": _p_sel,
        "payout_is_complement": bool(payout_is_complement),
        "payout_event": (("NOT(%s)" % contract.get("selection"))
                         if payout_is_complement
                         else contract.get("selection")),
        "complement_note": (
            "probability is the probability of the event THIS CONTRACT "
            "PAYS ON. probability_of_selection is the de-vig's number for "
            "the selection itself. On a three-way book the complement of "
            "one outcome is the other two together, never the opposing "
            "team alone"),
        "raw_odds": val.get("raw_odds"),
        "observed_at": val.get("observed_at"),
        "received_at": val.get("received_at"),
        "age_s": val.get("age_s"),
        "mapped_outcome": val.get("mapped_outcome"),
        "order_submitted": False,
        "shadow_only": True,
        "refusals": list(val.get("refusals") or []),
    }

    # REFUSALS THE CALLER ALREADY ESTABLISHED, carried in rather than
    # short-circuited. The runtime loop can only learn some things --
    # whether the venue's settlement rule is established, for instance --
    # after it has done work this function would otherwise repeat. Handing
    # them in keeps the RECORD complete: the row still carries the odds,
    # the mapping, the venue quote, the probability and the costs, so
    # management can see what the engine was looking at when it refused,
    # instead of the row not existing at all.
    for code in (extra_refusals or []):
        if code not in rec["refusals"]:
            rec["refusals"].append(str(code))
    rec["caller_refusals"] = [str(c) for c in (extra_refusals or [])]

    if not armed:
        # Checked BEFORE anything else consumes budget or claims a
        # decision: an unarmed experiment records why and stops.
        rec["refusals"].insert(0, R_CONTROL_OFF)
        rec["decision"] = "NO_TRADE"
        rec["why"] = "the experiment's control row is not true"
        return rec

    # PER-OUTCOME depth, not per-event. The anchor alone is a rounding
    # error; the feed module's own audit is cited in MIN_OUTCOME_BOOKS.
    books = outcome_books if outcome_books is None else int(outcome_books)
    rec["outcome_books"] = books
    if _p_pay is not None and (
            books is None or books < MIN_OUTCOME_BOOKS):
        rec["refusals"].append(R_THIN_OUTCOME)

    # THE PLAN, BUILT ON THE VALUATION THIS FUNCTION JUST COMPUTED.
    # Invoked after the complement inversion and before anything reads a
    # price, so the sizing, the limit and the risk verdict all describe
    # the same payout event as the probability does. A plan that raises
    # is recorded as a refusal rather than allowed to abort the record --
    # the row, with its odds and its costs, is the deliverable.
    market_state = dict(market_state or {})
    plan_fee_per = None
    if execution_plan is not None:
        if _p_pay is None:
            rec["execution_plan"] = {
                "built": False,
                "why": ("no probability, so there is no break-even limit "
                        "to size against and no plan was attempted")}
        else:
            try:
                plan = execution_plan(fair_value=float(_p_pay),
                                      contract=dict(contract)) or {}
            except Exception as exc:                           # noqa: BLE001
                plan = {}
                rec["refusals"].append("EXECUTION_PLAN_FAILED:%s"
                                       % type(exc).__name__)
            rec["execution_plan"] = plan.get("detail") or {
                "built": bool(plan)}
            # THE PLAN'S OWN NAMED REASONS, kept. The gate can only say
            # EXECUTION_ESTIMATE_NOT_IDENTIFIED; it cannot say whether
            # the ladder was unreadable or was simply priced beyond
            # break-even, and those are different problems for whoever
            # reads the cycle report.
            rec["plan_refusals"] = [str(c)
                                    for c in (plan.get("refusals") or [])]
            for code in rec["plan_refusals"]:
                if code not in rec["refusals"]:
                    rec["refusals"].append(code)
            execution_estimate = plan.get("execution_estimate",
                                          execution_estimate)
            size = plan.get("size", size)
            risk = plan.get("risk", risk)
            if plan.get("ask") is not None:
                # THE PRICE OF THE QUANTITY ACTUALLY CLAIMED. Pricing a
                # multi-level size off level one understates the cost and
                # turns depth into edge.
                #
                # WHICH price this is, is the plan's to say, not ours. The
                # acquisition cost of the walked quantity, the limit that
                # would be submitted, and any worst-case reservation are
                # three different numbers, and a hard-coded label here
                # once asserted the first while the loop handed in the
                # second -- the economic comparison then measured the
                # break-even price against itself and found no edge.
                market_state["ask"] = plan["ask"]
                market_state["ask_basis"] = str(
                    plan.get("ask_basis")
                    or "ACQUISITION_PRICE_BASIS_NOT_STATED_BY_THE_PLAN")
            if plan.get("fee_per_contract") is not None:
                plan_fee_per = abs(float(plan["fee_per_contract"]))
            # THE OTHER TWO PRICES, kept and never compared. Recording
            # them beside the acquisition cost is what makes a later
            # reader able to tell that the order went out at one price
            # and the economics were measured at another.
            for _k in ("submitted_limit", "worst_case_cost_per_contract"):
                if plan.get(_k) is not None:
                    rec[_k] = float(plan[_k])

    ask = (market_state or {}).get("ask")
    fee_per = None
    if plan_fee_per is not None:
        # PER-LEVEL FEES AS ACTUALLY WALKED. A fee re-derived at one
        # price is the right number only for a single-level fill; a
        # multi-level walk pays a different fee at each level.
        fee_per = plan_fee_per
        rec["cost_per_contract_basis"] = ("SUM_OF_PER_LEVEL_FEES_OVER_THE"
                                          "_WALKED_QUANTITY")
    elif ask is not None and fee_fn is not None:
        try:
            fee_per = abs(float(fee_fn(qty=1.0, price=float(ask))))
            rec["cost_per_contract_basis"] = "RE_DERIVED_AT_THE_ASK"
        except Exception:                                      # noqa: BLE001
            fee_per = None
    rec["executable_price"] = None if ask is None else float(ask)
    rec["executable_price_basis"] = (market_state or {}).get("ask_basis")
    rec["cost_per_contract"] = fee_per
    if _p_pay is not None and ask is not None \
            and fee_per is not None:
        # THE COMPARISON, stated once: the probability of the event this
        # contract PAYS ON, against the ACQUISITION price of that same
        # contract, after the cost of crossing. Both sides of this
        # subtraction describe the same payout event.
        rec["estimated_edge_per_contract"] = (
            float(_p_pay) - float(ask) - fee_per)
    else:
        rec["estimated_edge_per_contract"] = None

    admitted = gate.admit(
        action_table=None,
        model=None,
        fair_value=({"value": _p_pay,
                     "kind": devig.SOURCE_CLASS}
                    if _p_pay is not None else None),
        execution_estimate=execution_estimate,
        size=size, risk=risk, market_state=market_state, fee_fn=fee_fn,
        # SAME FEE ON BOTH SIDES. Without this the gate re-derives the
        # fee at the acquisition price while this module used the walked
        # per-level fees, and the two edges disagree by the difference.
        fee_per_contract=fee_per,
        min_net_edge_per_contract=min_net_edge_per_contract,
        external_source=val if _p_pay is not None else None,
        external_enabled=True)
    rec["gate"] = admitted
    for code in admitted.get("refusals", []):
        if code not in rec["refusals"]:
            rec["refusals"].append(code)

    # A thin outcome is a refusal of OURS, so it must veto admission even
    # though the gate knows nothing about book depth. The same is true of
    # every refusal the caller handed in: a record that carries a refusal
    # and is still admissible would make the refusal decorative.
    rec["admissible"] = bool(admitted.get("admissible")) and \
        R_THIN_OUTCOME not in rec["refusals"] and \
        not rec["caller_refusals"] and \
        not rec.get("plan_refusals")
    rec["decision"] = "BUY" if rec["admissible"] else "NO_TRADE"
    rec["proposed_size"] = size if rec["admissible"] else None
    rec["why"] = (("external probability %.4f on %s vs acquisition "
                   "%.4f less cost %.4f"
                   % (_p_pay, rec["payout_event"], float(ask), fee_per))
                  if rec["admissible"] else
                  ("; ".join(rec["refusals"]) or "no reason recorded"))
    return rec


def describe() -> dict:
    return {
        "experiment_id": EXPERIMENT_ID,
        "label": LABEL,
        "control_key": CONTROL_KEY,
        "source": devig.describe(),
        "min_net_edge_per_contract": MIN_NET_EDGE_PER_CONTRACT,
        "min_outcome_books": MIN_OUTCOME_BOOKS,
        "assumes_resting_fills": False,
        "prices_segments": False,
        "touches_frozen_exit_policy": False,
        "submits_orders": False,
        "separate_from_rn1_seeded_management": True,
        "credential": credential_present(),
    }


# ── persistence ─────────────────────────────────────────────────────

INSERT = """
    INSERT INTO external_valuations
        (experiment_id, version, source_class, provider, book, devig_method,
         venue, condition_id, us_market_slug, contract_identity_basis,
         contract_selection, sport_family, market,
         period, line, settlement_rule, event_key,
         raw_odds, outcomes_priced, expected_outcomes, overround,
         observed_at, received_at, age_s, outcome_books,
         mapped_outcome, mapping_match,
         probability, executable_price, cost_per_contract,
         estimated_edge_per_contract,
         decision, admissible, refusals, why, proposed_size,
         payout_event, payout_event_basis, probability_event,
         payout_is_complement, buy_intent, matched_side_norm,
         resolver_asked_for, ladder_side,
         -- THE REST OF THE DECISION. See migration 116: without these the
         -- production row could not answer what the fill estimate was,
         -- what size the policy chose, which rail passed or what the
         -- settlement comparison found.
         execution_estimate, risk_verdict, exposure_observed,
         settlement_comparison)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
            $18::jsonb,$19,$20,$21,
            CASE WHEN $22::double precision IS NULL THEN NULL
                 ELSE to_timestamp($22) END,
            CASE WHEN $23::double precision IS NULL THEN NULL
                 ELSE to_timestamp($23) END,
            $24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34,$35,$36,
            $37,$38,$39,$40,$41,$42,$43,$44,
            $45::jsonb,$46::jsonb,$47::jsonb,$48::jsonb)
    -- BARE `DO NOTHING`, deliberately. Migration 105's uniqueness is an
    -- EXPRESSION index (coalesce over the nullable key columns), and
    -- `ON CONFLICT ON CONSTRAINT` cannot name an index, while inferring
    -- it would mean repeating the whole coalesce list here and keeping
    -- two copies in step. The only unique things on this table are the
    -- serial primary key -- which this statement never supplies -- and
    -- that index, so an untargeted DO NOTHING can only mean "this
    -- observation is already recorded".
    --
    -- A skipped insert RETURNS NO ROW, so `persist` returns None and the
    -- caller must not count it as written.
    ON CONFLICT DO NOTHING
    RETURNING id
"""

JOIN_OUTCOME = """
    UPDATE external_valuations
       SET outcome_known = TRUE, outcome = $2,
           outcome_at = to_timestamp($3), realised_net_usd = $4
     WHERE id = $1 AND outcome_known = FALSE
"""


def _plan_json(rec: dict, section: str):
    """One section of the entry plan as JSON, or None if it was not built.

    None and `{}` mean different things here: the first says the lane
    never got far enough to compute this, the second would say it computed
    an empty answer. A row that cannot tell them apart cannot be used to
    find out where a cycle stopped.
    """
    import json as _json

    got = (rec.get("execution_plan") or {}).get(section)
    return None if got is None else _json.dumps(got, default=str)


async def persist(conn, rec: dict) -> int | None:
    """Store the record -- admissible or refused, both.

    Returns the row id. A refused record is stored with its full refusal
    list because that list is what management inspects to see WHY the
    engine did not buy.
    """
    import json

    c = rec.get("contract") or {}
    v = rec.get("valuation") or {}
    return await conn.fetchval(
        INSERT,
        rec.get("experiment_id") or EXPERIMENT_ID,
        rec.get("version") or devig.VERSION,
        rec.get("source_class") or devig.SOURCE_CLASS,
        devig.PROVIDER, devig.BOOK,
        rec.get("devig_method") or devig.DEFAULT_METHOD,
        str(c.get("venue") or ""), c.get("condition_id"),
        # THE VENUE-NATIVE IDENTITY, beside the global one and never
        # derived from it. A row that carries only the global id cannot be
        # looked up at the venue, which is the defect this fixes.
        c.get("us_market_slug"),
        (c.get("contract_identity_basis")
         or ("BOTH_PRESENT_AND_INDEPENDENTLY_SOURCED"
             if c.get("condition_id") and c.get("us_market_slug")
             else "VENUE_NATIVE_US_SLUG" if c.get("us_market_slug")
             else "GLOBAL_CONDITION_ID" if c.get("condition_id")
             else "NEITHER_IDENTITY_RECORDED")),
        str(c.get("selection") or ""), str(c.get("sport_family") or ""),
        str(c.get("market") or ""),
        (None if c.get("period") is None else str(c["period"])),
        (None if c.get("line") is None else float(c["line"])),
        (None if c.get("settlement_rule") is None
         else str(c["settlement_rule"])),
        (None if c.get("event_key") is None else str(c["event_key"])),
        json.dumps(v.get("raw_odds") or {}),
        int(v.get("outcomes_priced") or 0),
        int(v.get("expected_outcomes") or 0),
        (None if v.get("overround") is None else float(v["overround"])),
        rec.get("observed_at"), rec.get("received_at"),
        (None if rec.get("age_s") is None else float(rec["age_s"])),
        rec.get("outcome_books"),
        rec.get("mapped_outcome"), v.get("mapping_match"),
        (None if rec.get("probability") is None
         else float(rec["probability"])),
        rec.get("executable_price"), rec.get("cost_per_contract"),
        rec.get("estimated_edge_per_contract"),
        rec.get("decision") or "NO_TRADE",
        bool(rec.get("admissible")),
        list(rec.get("refusals") or []),
        str(rec.get("why") or "")[:2000],
        rec.get("proposed_size"),
        # ── THE PAYOUT IDENTITY, ON THE ROW ──────────────────────────
        #
        # A row that records `contract_selection` and `probability` but
        # not which EVENT the probability describes cannot be re-checked,
        # which is why every row written before migration 108 is held
        # rather than cleared. These columns end that: the event the
        # contract pays on, the basis for saying so, the event the
        # probability describes, and the venue side actually matched.
        #
        # `buy_intent` and `ladder_side` are stored too, and they are
        # deliberately NOT the source of the payout event -- they are the
        # evidence that the intent only ever chose a ladder.
        rec.get("payout_event") or (c.get("payout_event")
                                    or c.get("selection")),
        (c.get("payout_event_basis")
         or "RESOLVER_MATCHED_A_SIDE_FOR_THE_REQUESTED_OUTCOME"),
        rec.get("probability_event") or (c.get("probability_event")
                                         or c.get("selection")),
        bool(rec.get("payout_is_complement")),
        c.get("buy_intent"),
        c.get("matched_side_norm"),
        c.get("resolver_asked_for") or c.get("selection"),
        c.get("ladder_side"),
        # ── THE ENTRY PLAN, AS IT WAS COMPUTED ───────────────────────
        # `_plan_json` returns None rather than {} when a section was
        # never built, so an absent estimate is visibly absent instead of
        # reading as an empty one.
        _plan_json(rec, "execution"),
        _plan_json(rec, "risk"),
        _plan_json(rec, "exposure"),
        (None if rec.get("settlement_comparison") is None
         else json.dumps(rec["settlement_comparison"], default=str)))


REFUSAL_CENSUS = """
    SELECT unnest(refusals) AS refusal, count(*) AS n
      FROM external_valuations
     WHERE experiment_id = $1 AND decided_at >= now() - ($2 || ' hours')::interval
     GROUP BY 1 ORDER BY 2 DESC
"""

SUMMARY = """
    SELECT count(*) AS evaluated,
           count(*) FILTER (WHERE admissible) AS admissible,
           count(*) FILTER (WHERE NOT admissible) AS refused,
           count(*) FILTER (WHERE probability IS NOT NULL) AS priced,
           count(*) FILTER (WHERE outcome_known) AS settled,
           min(decided_at) AS first_at, max(decided_at) AS last_at,
           -- WHICH IDENTITY EACH ROW CARRIES. A count of valuations says
           -- nothing about whether the contracts can be looked up at the
           -- venue, and that distinction is the whole of run 24's finding.
           count(*) FILTER (WHERE us_market_slug IS NOT NULL)
               AS with_venue_native_identity,
           count(*) FILTER (WHERE condition_id IS NOT NULL)
               AS with_global_condition_id,
           count(*) FILTER (WHERE us_market_slug IS NOT NULL
                              AND condition_id IS NOT NULL)
               AS with_both
      FROM external_valuations
     WHERE experiment_id = $1
"""

#: THE SAME TOTALS, INSIDE THE WINDOW THE REST OF THE CENSUS USES.
#:
#: THE MIS-READING THIS PREVENTS, AND IT NEARLY CAUGHT ME. `SUMMARY` takes
#: no window: it is ALL TIME. `REFUSAL_CENSUS` and `STAGE_CENSUS` both take
#: `hours`. So a report printing `evaluated 386` beside `4_SETTLEMENT_SCOPE
#: 63` was putting an all-time numerator next to a six-hour one, and the 63
#: + 15 that legitimately add to the window's 78 candidates looked like they
#: had lost 308 rows somewhere. Two denominators side by side with no label
#: is exactly the shape of the overstatement the stage breakdown exists to
#: stop, so the windowed totals are computed and reported as their own
#: block rather than left for a reader to reconcile.
SUMMARY_IN_WINDOW = """
    SELECT count(*) AS evaluated,
           count(*) FILTER (WHERE admissible) AS admissible,
           count(*) FILTER (WHERE NOT admissible) AS refused,
           count(*) FILTER (WHERE probability IS NOT NULL) AS priced,
           min(decided_at) AS first_at, max(decided_at) AS last_at
      FROM external_valuations
     WHERE experiment_id = $1
       AND decided_at >= now() - ($2 || ' hours')::interval
"""

#: The identity split on its own, so the census can show it per basis.
IDENTITY_CENSUS = """
    SELECT coalesce(contract_identity_basis, 'UNLABELLED') AS basis,
           count(*) AS n,
           count(*) FILTER (WHERE admissible) AS admissible,
           max(decided_at) AS last_at
      FROM external_valuations
     WHERE experiment_id = $1
     GROUP BY 1 ORDER BY 1
"""


# ── WHERE IN THE PIPELINE A CANDIDATE STOPPED ────────────────────────
#
# THE CLAIM THIS CORRECTS, AND IT WAS MINE. I reported that every
# production candidate "lacked profitable depth" because each row showed
# empty walk and vwap fields. That does not follow. A candidate refused at
# the SETTLEMENT SCOPE stage never reaches execution estimation at all, so
# its walk fields are empty because the walk was never attempted -- not
# because the book was thin. And `NO_ACTION_HAS_POSITIVE_NET_EDGE` can
# fire on a candidate with no execution estimate, because the gate then
# compares the probability against the observed best ask; that is a real
# negative edge at the TOP of the book, which is a narrower statement than
# "no profitable depth exists".
#
# So the refusals are attributed to STAGES, in the order the lane runs
# them, and a candidate is counted at the EARLIEST stage that refused it.
# A candidate carries several refusals; the earliest one is the only one
# that describes where it actually stopped.
STAGES = (
    ("1_PROBABILITY", (
        "INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED",
        "NO_QUALIFIED_MODEL",
        "THIN_OUTCOME_COVERAGE",
        R_THIN_OUTCOME)),
    ("2_FRESHNESS", (
        "QUOTE_STALE",
        "VENUE_BOOK_STALE",
        "ONE_CLOCK_IS_NOT_MEASURED")),
    ("3_IDENTITY", (
        "VENUE_DOES_NOT_LIST_THIS_FIXTURE",
        "NO_PREMAP_CONTRACT_FOR_THIS_FIXTURE",
        "PAYOUT_OUTCOME_INDEX_NOT_BOUND_TO_A_TOKEN",
        "PAYOUT_OUTCOME_DISAGREES_WITH_THE_VENUE_INTENT",
        # WHICH PERIOD THE CONTRACT PAYS ON IS PART OF ITS IDENTITY. A
        # full-match probability priced against an inning-six payout is
        # the wrong contract, not a stale one.
        "VENUE_CONTRACT_PERIOD_NOT_ESTABLISHED")),
    ("4_SETTLEMENT_SCOPE", (
        "VOID_ABANDONMENT_RULE_NOT_ESTABLISHED",
        "OVERTIME_RULE_NOT_ESTABLISHED",
        "SETTLEMENT_TERMS_CONFLICT",
        "SETTLEMENT_SCOPE_NOT_ESTABLISHED",
        "UNRESOLVED_SETTLEMENT_SEMANTICS")),
    ("5_EXECUTION_ESTIMATE", (
        "NO_OBSERVED_DEPTH_INSIDE_THE_BREAK_EVEN_LIMIT",
        "EXECUTION_ESTIMATE_NOT_IDENTIFIED",
        "VENUE_BOOK_NOT_READ",
        "P_FILL_NOT_IDENTIFIED")),
    ("6_SIZING", (
        "SIZING_POLICY_NOT_APPLICABLE",
        # SIZING NOW ASKS THE RAILS FOR HEADROOM BEFORE IT SPENDS, so a
        # book with no room left refuses HERE rather than proposing a
        # position that 7_RISK then rejects. The distinction matters to
        # the census: "the book is full" is a portfolio state, while
        # RISK_GATE_BLOCKED at stage 7 is a gate on the candidate.
        "NO_RAIL_HEADROOM_FOR_ANY_POSITION",
        "RAIL_HEADROOM_NOT_MEASURED",
        "UNFILLED_NOTIONAL",)),
    ("7_RISK", (
        "RISK_GATE_BLOCKED",
        "ACTION_EXPOSURE_EFFECT_NOT_IDENTIFIED",
        "OPEN_SHADOW_BOOK_NOT_READ")),
    ("8_ECONOMICS", (
        "NO_ACTION_HAS_POSITIVE_NET_EDGE",)),
)

STAGE_OF = {code: name for name, codes in STAGES for code in codes}
STAGE_ORDER = [name for name, _ in STAGES]
STAGE_UNCLASSIFIED = "9_UNCLASSIFIED_REFUSAL"


def first_stage(refusals) -> str | None:
    """The EARLIEST pipeline stage any of these refusals belongs to.

    None when the list is empty. `9_UNCLASSIFIED_REFUSAL` when refusals
    exist but none is a code this map knows -- reported by name rather
    than silently folded into a stage, because an unmapped code means this
    table has drifted from the lane.
    """
    codes = [str(c) for c in (refusals or [])]
    if not codes:
        return None
    hits = [STAGE_OF[c] for c in codes if c in STAGE_OF]
    if not hits:
        return STAGE_UNCLASSIFIED
    return min(hits, key=lambda n: STAGE_ORDER.index(n))


#: WHAT THE SETTLEMENT COMPARISON ACTUALLY RETURNED, COUNTED.
#:
#: WHY A COUNT AND NOT A SAMPLE. The per-candidate evidence read returns 25
#: rows; the question "does ANY supported market have a compatible payoff"
#: is about the whole window. Silence (UNKNOWN) and a stated conflict
#: (INCOMPATIBLE) are different answers with different remedies -- more
#: reading fixes one and nothing fixes the other -- so they are counted
#: apart, together with which conditions carried the conflict.
#: ONE ROW PER VERDICT. No join, no unnest -- a row must be counted once.
#: (The first version of this unnested `mismatched_conditions` in the same
#: query, which counts a row with two mismatched conditions twice. The
#: conditions are asked for separately below.)
SETTLEMENT_COVERAGE = """
    SELECT coalesce(settlement_comparison::jsonb ->> 'compatibility',
                    'NOT_RECORDED') AS verdict,
           sport_family,
           count(*) AS n,
           count(*) FILTER (WHERE admissible) AS admissible,
           count(DISTINCT us_market_slug) AS slugs
      FROM external_valuations
     WHERE experiment_id = $1
       AND decided_at >= now() - ($2 || ' hours')::interval
     GROUP BY 1, 2 ORDER BY 3 DESC
"""

#: WHICH conditions carried a conflict, and on how many rows. Separate, so
#: neither number distorts the other.
SETTLEMENT_MISMATCHES = """
    SELECT m AS condition, count(*) AS rows_carrying_it
      FROM external_valuations ev,
           LATERAL jsonb_array_elements_text(
             coalesce(ev.settlement_comparison::jsonb
                        -> 'mismatched_conditions', '[]'::jsonb)) AS m
     WHERE ev.experiment_id = $1
       AND ev.decided_at >= now() - ($2 || ' hours')::interval
     GROUP BY 1 ORDER BY 2 DESC
"""

#: Per-candidate stage attribution, with the two facts that decide whether
#: "no profitable depth" is even sayable about a row: did the execution
#: estimate SUCCEED, and did a walk actually take levels.
STAGE_CENSUS = """
    SELECT refusals,
           admissible,
           sport_family,
           COALESCE(
             (execution_estimate::jsonb ->> 'ok') = 'true', FALSE)
               AS estimate_ok,
           COALESCE(jsonb_array_length(
             COALESCE(execution_estimate::jsonb -> 'levels_taken',
                      '[]'::jsonb)), 0) AS levels_taken,
           (execution_estimate::jsonb ->> 'vwap')::float8 AS vwap,
           executable_price::float8 AS executable_price,
           probability::float8 AS probability,
           estimated_edge_per_contract::float8 AS edge
      FROM external_valuations
     WHERE experiment_id = $1
       AND decided_at >= now() - ($2 || ' hours')::interval
"""


def stage_report(rows) -> dict:
    """Stage-specific counts, and what each one does and does not support.

    Pure, so the attribution can be checked without a database.
    """
    out = {"candidates": 0, "admissible": 0,
           "by_first_stage": {}, "reached_execution_estimate": 0,
           "walk_took_levels": 0,
           "negative_edge_with_a_walk": 0,
           "negative_edge_without_a_walk": 0,
           # THE COUNT NOBODY PRINTED, so "none was positive" had to be
           # inferred from two negative counts and a total. An opportunity
           # assessment answers "how many candidates showed positive net
           # edge", and that answer has to be a number in its own right --
           # including when it is zero.
           "positive_edge": 0,
           "edge_not_computed": 0,
           "by_sport": {},
           # WHICH EXECUTION THE EDGE ASSUMES. The lane buys by crossing the
           # ask and pays the TAKER fee (`fee_fn(..., maker=False)`), so
           # every edge counted here is a CROSSING edge. A passive/maker
           # strategy has different fill assumptions and a different fee
           # side; none of its numbers are in this census.
           "execution_mode": "CROSSING_THE_ASK_AT_THE_TAKER_FEE",
           "excludes": "ANY_PASSIVE_OR_MAKER_FILL_ASSUMPTION",
           "stage_order": list(STAGE_ORDER)}
    for r in rows or []:
        out["candidates"] += 1
        fam = str(r.get("sport_family") or "UNLABELLED")
        sp = out["by_sport"].setdefault(
            fam, {"candidates": 0, "admissible": 0, "priced": 0,
                  "positive_edge": 0, "negative_or_zero_edge": 0,
                  "by_first_stage": {}})
        sp["candidates"] += 1
        if r.get("admissible"):
            out["admissible"] += 1
            sp["admissible"] += 1
        st = first_stage(r.get("refusals"))
        if st is not None:
            out["by_first_stage"][st] = out["by_first_stage"].get(st, 0) + 1
            sp["by_first_stage"][st] = sp["by_first_stage"].get(st, 0) + 1
        ok = bool(r.get("estimate_ok"))
        took = int(r.get("levels_taken") or 0)
        if ok:
            out["reached_execution_estimate"] += 1
        if took > 0:
            out["walk_took_levels"] += 1
        edge = r.get("edge")
        if edge is None:
            out["edge_not_computed"] += 1
        else:
            sp["priced"] += 1
            if float(edge) > 0:
                out["positive_edge"] += 1
                sp["positive_edge"] += 1
            else:
                sp["negative_or_zero_edge"] += 1
                if took > 0:
                    out["negative_edge_with_a_walk"] += 1
                else:
                    out["negative_edge_without_a_walk"] += 1
    out["what_this_supports"] = {
        "negative_edge_with_a_walk": (
            "a book was walked and the volume-weighted cost of the sized "
            "quantity, plus the per-level fees, exceeded the probability. "
            "This is the only class about which 'no profitable depth' is "
            "sayable"),
        "negative_edge_without_a_walk": (
            "no execution estimate existed, so the gate compared the "
            "probability against the OBSERVED BEST ASK. A real negative "
            "edge at the top of the book -- which does NOT establish "
            "anything about depth further down, because none was read"),
        "refused_before_5_EXECUTION_ESTIMATE": (
            "the walk was never attempted. An empty vwap on these rows is "
            "the absence of a measurement, not a thin book"),
    }
    return out


async def census(conn, *, hours: int = 24) -> dict:
    """What the engine did and, mostly, why it did not.

    THE REFUSAL DISTRIBUTION IS THE DELIVERABLE when nothing clears, so it
    is a first-class read rather than something to be reconstructed from
    rows by hand.
    """
    rows = await conn.fetch(REFUSAL_CENSUS, EXPERIMENT_ID, str(int(hours)))
    summ = await conn.fetchrow(SUMMARY, EXPERIMENT_ID)
    ident = await conn.fetch(IDENTITY_CENSUS, EXPERIMENT_ID)
    out = {
        "experiment_id": EXPERIMENT_ID,
        "window_hours": int(hours),
        "refusals": {r["refusal"]: int(r["n"]) for r in rows},
        # ALL TIME, AND LABELLED AS SUCH. `refusals` and `stages` below are
        # windowed; this is not, and an unlabelled pair of totals on two
        # different denominators is how a report invites the wrong
        # conclusion.
        "summary": (dict(summ) if summ is not None else {}),
        "summary_window": "ALL_TIME_NOT_THE_window_hours_ABOVE",
        # THE TWO IDENTITIES, REPORTED APART. `us_market_slug` is what the
        # venue accepts; `condition_id` is the global catalogue's id. A row
        # carrying only the latter is a valuation of a contract no venue
        # read can reach.
        "contract_identity": [dict(r) for r in ident],
        "identity_note": (
            "VENUE_NATIVE_US_SLUG is us_premap.market_slug, which "
            "pmus.book_read accepts. GLOBAL_CONDITION_ID is the global "
            "catalogue's id, which it does not. BOTH means each was "
            "sourced independently -- never one derived from the other"),
        "credential": credential_present(),
    }
    # ── DOES ANY SUPPORTED MARKET HAVE A COMPARABLE PAYOFF? ──────────
    # Counted over the whole window rather than sampled, because "none"
    # is the answer that matters and a 25-row sample cannot establish it.
    try:
        cov = await conn.fetch(SETTLEMENT_COVERAGE, EXPERIMENT_ID,
                               str(int(hours)))
        mis = await conn.fetch(SETTLEMENT_MISMATCHES, EXPERIMENT_ID,
                               str(int(hours)))
        # AGGREGATED, AND SPLIT BY SPORT. One number over both sports
        # cannot answer "does the OTHER supported sport have a compatible
        # payoff" -- and that was the open question this read was used to
        # answer. The verdict rollup is kept so the old reading still works.
        by_verdict: dict = {}
        for r in cov:
            slot = by_verdict.setdefault(
                r["verdict"], {"rows": 0, "admissible": 0,
                               "distinct_slugs": 0, "by_sport": {}})
            slot["rows"] += int(r["n"])
            slot["admissible"] += int(r["admissible"])
            # DISTINCT PER SPORT, SUMMED. A slug belongs to one sport, so
            # the sum is exact; it is NOT a distinct count across sports in
            # general and is not presented as one.
            slot["distinct_slugs"] += int(r["slugs"])
            slot["by_sport"][str(r["sport_family"])] = {
                "rows": int(r["n"]), "admissible": int(r["admissible"]),
                "distinct_slugs": int(r["slugs"])}
        out["settlement_coverage"] = {
            "by_verdict": by_verdict,
            "mismatched_conditions": {r["condition"]:
                                      int(r["rows_carrying_it"])
                                      for r in mis},
            "compatible_rows": sum(int(r["n"]) for r in cov
                                   if r["verdict"] == "COMPATIBLE"),
            "reading": (
                "COMPATIBLE means every applicable condition is stated by "
                "BOTH sides and pays the same. INCOMPATIBLE means at least "
                "one is stated by both and differs -- more reading cannot "
                "fix that one. UNKNOWN means somebody is silent. A count of "
                "zero COMPATIBLE rows over the window is the measured "
                "answer to whether a tradable payoff exists here"),
        }
    except Exception as exc:                                   # noqa: BLE001
        out["settlement_coverage"] = {"computed": False,
                                      "error": type(exc).__name__}
    try:
        srows = await conn.fetch(STAGE_CENSUS, EXPERIMENT_ID,
                                 str(int(hours)))
        out["stages"] = stage_report([dict(r) for r in srows])
        # THE WINDOW'S OWN TOTALS, so the stage counts have a denominator
        # that belongs to them. `summary` above is all time.
        win = await conn.fetchrow(SUMMARY_IN_WINDOW, EXPERIMENT_ID,
                                  str(int(hours)))
        out["summary_in_window"] = (dict(win) if win is not None else {})
        out["stages"]["reconciles_with_window"] = (
            int((out["summary_in_window"] or {}).get("refused") or 0)
            == sum(out["stages"].get("by_first_stage", {}).values()))
    except Exception as exc:                                   # noqa: BLE001
        # A FAILED ATTRIBUTION IS NOT AN EMPTY ONE. The refusal counts
        # above stand on their own; this says the breakdown could not be
        # computed rather than reporting zeros for every stage.
        out["stages"] = {"computed": False,
                         "error": type(exc).__name__,
                         "why": ("the stage attribution read failed, so no "
                                 "stage-specific count is reported")}
    return out

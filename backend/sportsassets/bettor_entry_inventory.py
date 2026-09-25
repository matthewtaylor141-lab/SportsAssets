"""AN ADMITTED ENTRY BECOMES INVENTORY, IN THE LEDGER THAT ALREADY EXISTS.

WHAT THIS CLOSES. The external-valuation lane could produce an admissible
BUY and then write only a VALUATION ROW: probability, price, edge,
verdict. That is a decision record, not an entry. Nothing held a
position, nothing owed a fee, nothing carried residual quantity and
nothing had to reconcile -- so "autonomous entry works" could not be
checked against anything.

An admitted decision now lands in the SAME four tables the managed
position uses -- `rn1x_positions`, `rn1x_decisions`, `rn1x_orders`,
`rn1x_fills` -- so from the moment it exists it is read by the same
management cycle, the same accounting and the same command-centre trace.
A second ledger for entries would have been a second set of numbers to
reconcile, which is the failure this project has already paid for once.

────────────────────────────────────────────────────────────────────
WHAT IS MODELLED AND WHAT IS OBSERVED, kept apart on every row.

  OBSERVED   the venue's acquisition ladder at a stated instant, the
             bookmaker's odds at a stated instant, the fee schedule
  MODELLED   that our order arrived, and that it took the depth the
             ladder was showing

`is_modelled` is TRUE on the order and the fill, and the database
CHECKs that it cannot be anything else. `fill_basis` is
MARKETABLE_RECONSTRUCTED, never the resting-order basis, because this
order crossed. `queue_share` is 0 and that is not a queue estimate: a
crossing order does not join a queue, and the basis column says which
kind of fill this was.

────────────────────────────────────────────────────────────────────
DUPLICATE PROTECTION IS IN THE DATABASE, NOT IN THIS FUNCTION.

`rn1x_positions` is UNIQUE on (experiment_id, policy, source_trade_id),
and an autonomous entry has no source trade -- the column is NULL, and in
Postgres NULLs do not collide, so that constraint protects nothing here.
Migration 116 adds the partial unique index this lane actually needs, on
(experiment_id, policy, condition_id, outcome_index).

Every id is DERIVED from that same tuple rather than generated, so a
re-run of the same cycle writes the same rows and `ON CONFLICT DO
NOTHING` makes the write idempotent instead of additive. A repeated
entry is reported as a duplicate, which is a fact management wants, not
silently absorbed.

THE ONE-POSITION RULE IS ALSO CHECKED BEFORE THE INSERT, because the
index cannot distinguish "this exact entry again" from "a second,
different entry into the same exposure" -- and the second is a real
decision that must be refused rather than deduplicated.

────────────────────────────────────────────────────────────────────
NOTHING HERE SUBMITS ANYTHING. There is no venue client in this module,
no credential read and no submit function reachable from it. It writes
rows.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import bettor_entry_execution as entryx
from . import shadow

#: The lane's own policy name in the shared ledger, so its rows are
#: filterable apart from the seeded acceptance harness and from the
#: challenger comparison without inspecting their contents.
POLICY = "EXT_PINNACLE_ENTRY_V1"

#: The experiment row this lane's positions hang off. Registered by
#: `ensure_experiment` rather than assumed to exist.
EXPERIMENT_CODE_VERSION = "ENTRY_MARKETABLE_EXECUTION_V1"

ENTRY_KIND = "AUTONOMOUS_ENTRY_FROM_EXTERNAL_VALUATION"
ENTRY_KIND_WHY = (
    "this position was not seeded from an observed on-chain trade. It was "
    "created by this system's own entry decision, priced against an "
    "EXTERNAL BOOKMAKER VALUATION whose accuracy is not validated and "
    "which is not an internal qualified model")

#: `source_account` is NOT NULL on the shared table because a seeded
#: position always has one. An autonomous entry has none, and naming that
#: is better than borrowing an account that did not place it.
NO_SOURCE_ACCOUNT = "NO_SOURCE_ACCOUNT_AUTONOMOUS_ENTRY"

#: One of the three values `rn1x_provenance_declared` enumerates. The
#: other two belong to the seeded lane and the acceptance harness and are
#: untouched.
PROVENANCE = "AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW"

FILL_BASIS = shadow.MARKETABLE_RECONSTRUCTED
LIQUIDITY = "TAKER"

EV_BASIS = ("EXTERNAL_BOOKMAKER_VALUATION_TIMES_FILLED_QTY; the "
            "probability is Pinnacle's de-vigged price, not an internal "
            "model, and its accuracy has not been validated")

# ── refusals ────────────────────────────────────────────────────────
R_NOT_ADMISSIBLE = "DECISION_IS_NOT_ADMISSIBLE"
R_NO_FILL = "NO_MARKETABLE_FILL_TO_RECORD"
R_ALREADY_HELD = "EXPOSURE_ALREADY_HELD_BY_THIS_LANE"
R_EXISTING_LOOKUP_FAILED = "EXISTING_POSITION_LOOKUP_FAILED"

ONE_POSITION_WHY = (
    "this lane already holds a position in this exposure. A second entry "
    "into it is an ADD, which is a different decision with its own risk "
    "question -- MAX_MARKET_EXPOSURE must be re-evaluated against the "
    "combined size -- and it is refused here rather than quietly "
    "deduplicated or quietly stacked")


def _j(v) -> str:
    return json.dumps(v, sort_keys=True, default=str)


def _ts(epoch):
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc)


def position_id(*, experiment_id, condition_id, outcome_index) -> str:
    """Derived, not generated, so a re-run is idempotent."""
    return "%s:%s:%s:%d" % (experiment_id, POLICY, condition_id,
                            int(outcome_index))


def observation_key(rec) -> str:
    """The PROVIDER OBSERVATION this decision was made on.

    ── WHY THE IDS HANG OFF THIS AND NOT OFF THE WALL CLOCK ──────────
    They used to be `...:O:<int(now)>`. So a later cycle re-deciding the
    SAME exposure minted a new order id and a new fill id, both inserted,
    while the position row hit ON CONFLICT DO NOTHING and kept its
    original seed_qty and basis. Executions accumulated against inventory
    that never grew: the fills said 1,800 contracts and the position said
    900, and nothing in the schema objected.

    Keyed on the observation instead, an exact replay of the same
    observation produces the same ids and writes nothing new -- which is
    what a replay should do -- and a DIFFERENT observation produces
    different ids and is therefore visible as a second entry rather than
    absorbed as a duplicate. The three cases are then separable, which is
    what `classify_write` does.
    """
    at = (rec or {}).get("observed_at")
    if at is None:
        return "NO_OBSERVATION_TIME"
    try:
        return "%d" % round(float(at) * 1000.0)
    except (TypeError, ValueError):
        return str(at)


# ── THE THREE CASES, AND ONLY ONE OF THEM WRITES ─────────────────────
CASE_NEW = "NEW_EXPOSURE"
CASE_REPLAY = "EXACT_REPLAY_OF_A_RECORDED_OBSERVATION"
CASE_NEW_QUOTE = "NEW_QUOTE_ON_AN_ALREADY_HELD_EXPOSURE"
CASE_ADD = "ADD_TO_AN_EXISTING_POSITION"

#: ADDING TO A HELD POSITION IS NOT SUPPORTED, and it is off rather than
#: absent so the refusal has a name a reader can look up. An add is a
#: different decision: it changes the average cost, it re-opens the
#: market-exposure rail against the COMBINED size, and it needs its own
#: accounting for the second tranche. None of that is built, so a second
#: quote on a held exposure is refused instead of being written as though
#: it were the first.
ADD_SUPPORTED = False

WHY_ADD_IS_REFUSED = (
    "a second entry into an exposure this lane already holds is an ADD. "
    "It changes the position's average cost and its size, so the "
    "market-exposure rail must be re-evaluated against the combined "
    "position and the second tranche needs its own basis. That is not "
    "built. Writing it as a fresh entry would add executions to inventory "
    "that never grew, which is the defect this branch exists to refuse")


def plan_entry(rec, *, now, outcome_index, fee_fn) -> dict:
    """The four rows an admitted decision implies, or why there are none.

    Pure: it touches no database. `rec` is a
    `bettor_external_shadow.evaluate` record.
    """
    out = {"ok": False, "refusals": []}
    if not rec.get("admissible"):
        out["refusals"].append(R_NOT_ADMISSIBLE)
        out["why"] = ("a refused decision creates no inventory: %s"
                      % "; ".join(rec.get("refusals") or ["no reason"]))
        return out

    est = ((rec.get("execution_plan") or {}).get("execution")) or {}
    qty = est.get("size")
    # THE REALISED PRICE AND THE SUBMITTED LIMIT ARE READ SEPARATELY, and
    # this is the reason the estimate now names them separately. This line
    # used to read `limit_price` and use it for the fill price, the
    # position's seed price AND the order's limit at once -- three
    # different quantities from one field, which is what let a walk over
    # .62/.64/.66 be recorded as an order limited at its own average.
    price = est.get("vwap")
    if not qty or price is None:
        out["refusals"].append(R_NO_FILL)
        out["why"] = ("the decision was admitted without a sized "
                      "marketable fill, which should be unreachable: the "
                      "gate refuses an unsized entry")
        return out

    qty = float(qty)
    price = float(price)
    submitted_limit = float(est.get("submitted_limit") or price)
    if submitted_limit + 1e-9 < price:
        # A LIMIT BELOW THE PRICE PAID IS NOT POSSIBLE, and if the two
        # ever disagree that way the estimate is inconsistent with its own
        # walk. Refuse rather than record an unreconcilable fill.
        out["refusals"].append(R_NO_FILL)
        out["why"] = ("the submitted limit %.6f is below the realised vwap "
                      "%.6f, so these two numbers did not come from the "
                      "same walk" % (submitted_limit, price))
        return out

    # ── ONE FILL PER LEVEL CONSUMED, PRICED AT THAT LEVEL ────────────
    #
    # A single fill at the VWAP is not what happened, and the fee is
    # charged per fill at the price traded -- so a fee computed once at the
    # average is only coincidentally the fee actually owed. The levels come
    # from the walk itself; when it did not report them the whole quantity
    # is recorded as one fill at the VWAP and `levels_evidence` says so
    # rather than implying a breakdown that was never observed.
    levels = list(est.get("levels_taken") or [])
    if not levels:
        levels = [{"price": price, "qty": qty, "cost": qty * price}]
        levels_evidence = "VWAP_ONLY_THE_WALK_REPORTED_NO_LEVELS"
    else:
        levels_evidence = "PER_LEVEL_FROM_THE_OBSERVED_LADDER_WALK"
    fills = []
    fee = 0.0
    cost = 0.0
    for k, lv in enumerate(levels):
        lq = float(lv["qty"])
        lp = float(lv["price"])
        lf = abs(float(fee_fn(qty=lq, price=lp)))
        fee += lf
        cost += lq * lp
        fills.append({"k": k, "qty": round(lq, 6), "price": round(lp, 6),
                      "fee_usd": round(lf, 6)})
    # THE COST BASIS INCLUDES THE FEE, matching the managed position's
    # own convention: 10 contracts at 0.64 with 0.16 of fees is a basis
    # of 6.56, not 6.40. A basis that excluded fees would make every
    # return look better than it was by exactly the cost of trading.
    basis = cost + fee

    contract = rec.get("contract") or {}
    pid = position_id(experiment_id=rec["experiment_id"],
                      condition_id=contract.get("condition_id"),
                      outcome_index=outcome_index)
    obs = observation_key(rec)
    did = "%s:D:%s" % (pid, obs)
    oid = "%s:O:%s" % (pid, obs)

    # THE THREE CLOCKS, AND THE ORDER THE DATABASE CHECKS. source is the
    # bookmaker's own observation instant, detected is when the payload
    # reached us, decision is now. Collapsing them would grant this lane
    # its own detection latency for free.
    source_ts = float(rec.get("observed_at") or now)
    detected_ts = float(rec.get("received_at") or source_ts)
    detected_ts = max(detected_ts, source_ts)
    decision_ts = max(float(now), detected_ts)

    # THE SHORTFALL IS IN DOLLARS, NOT IN CONTRACTS, and that follows
    # from what the sizing policy froze: the intent is $1,000 of notional,
    # and the quantity is whatever that buys inside the break-even limit.
    # So the ORDER is for exactly the quantity the budget afforded and it
    # fills completely; what fell short is the money that could not be
    # spent, which the accounting carries as UNFILLED_NOTIONAL.
    intended_notional = float(est.get("intended_notional_usd") or 0.0)
    unfilled_notional = float(est.get("unfilled_notional_usd") or 0.0)
    order_state = ("FILLED" if unfilled_notional <= 1e-9
                   else "FILLED_LIQUIDITY_LIMITED")

    out.update({
        "ok": True,
        "position": {
            "position_id": pid,
            "experiment_id": rec["experiment_id"],
            "policy": POLICY,
            "source_trade_id": None,
            "source_account": NO_SOURCE_ACCOUNT,
            "condition_id": contract.get("condition_id"),
            "outcome_index": int(outcome_index),
            "entry_kind": ENTRY_KIND,
            "entry_kind_why": ENTRY_KIND_WHY,
            "seed_qty": round(qty, 6),
            "seed_price": round(price, 6),
            "seed_basis_usd": round(basis, 6),
            "source_ts": source_ts,
            "detected_ts": detected_ts,
            "decision_ts": decision_ts,
            "decision_basis": "RUNTIME_WALL_CLOCK",
            # ONE OF THE THREE DECLARED VALUES, exactly. A free-text
            # sentence here was refused by `rn1x_provenance_declared`,
            # correctly: the column is an enumeration so a reader can
            # group positions by how they came to exist. Migration 116
            # adds this third name; the prose belongs in the decision's
            # own labels, where it is.
            "provenance": PROVENANCE,
            # ── THE IDENTITY THIS POSITION WAS OPENED UNDER ──────────
            #
            # Recorded HERE, by the writer that knows it, because the
            # settlement consumer used to rediscover it by taking "the
            # latest admissible valuation for the same condition" -- a
            # guess that is right only while exactly one exists, and that
            # never checked whether that valuation described the side the
            # position HOLDS. A condition has two sides and settling
            # against the wrong one is irreversible.
            #
            # `payout_event` is deliberately redundant with
            # `outcome_index`: the index is the GLOBAL catalogue's
            # ordering and the name is the event, so a consumer can check
            # one against the other through `market_tokens` instead of
            # trusting either alone. See migration 119.
            # THE VENUE WHOSE POSITION MODEL GOVERNS THIS POSITION.
            # `bettor_venue_position_model.model_for` refuses an unknown
            # venue rather than defaulting, and a consumer can only honour
            # that refusal if the venue is recorded. See migration 120.
            "venue": contract.get("venue"),
            "venue_market_slug": (contract.get("us_market_slug")
                                  or rec.get("us_market_slug")),
            "venue_buy_intent": rec.get("buy_intent"),
            "venue_ladder_side": rec.get("ladder_side"),
            "payout_event": rec.get("payout_event"),
            "source_valuation_id": rec.get("valuation_row_id"),
        },
        "decision": {
            "decision_id": did,
            "position_id": pid,
            "decision_ts": decision_ts,
            "evidence_ts": source_ts,
            "evidence_id": str(rec.get("contract", {}).get("event_key")
                               or ""),
            "selected_action": "BUY",
            "selection_reason": rec.get("why") or "admitted",
            "selected_qty": round(qty, 6),
            "ev_at_decision_usd": (
                None if rec.get("estimated_edge_per_contract") is None
                else round(float(rec["estimated_edge_per_contract"]) * qty,
                           6)),
            "ev_basis": EV_BASIS,
            "policy_version": POLICY,
            "governing_rule": entryx.EXECUTION_VERSION,
            "operating_state": "ENTRY",
            "alternatives": {
                # THE WHOLE COMPARISON, not just the choice. The refusals
                # that did NOT fire are as much a part of why this was
                # admitted as the edge is.
                "gate": rec.get("gate"),
                "refusals_none_fired": list(rec.get("refusals") or []),
                "execution_plan": rec.get("execution_plan"),
                "settlement": rec.get("settlement"),
                "venue_quote": rec.get("venue_quote"),
            },
            "conditional_on_our_fill": {
                "execution_secured": False,
                "p_fill": est.get("p_fill"),
                "p_fill_basis": est.get("basis"),
                "is_forecast": False,
                "note": ("the fill is MODELLED against depth the venue was "
                         "displaying. Displayed depth is not guaranteed "
                         "depth and no order was submitted, so this "
                         "position is an accounted counterfactual, not a "
                         "held instrument"),
            },
            "input_labels": {
                "probability": "EXTERNAL_BOOKMAKER_VALUATION",
                "probability_validated": False,
                "qualified_model": False,
                "price": "OBSERVED_VENUE_ACQUISITION_LADDER",
                "fees": "PUBLISHED_FEE_SCHEDULE",
                "execution": FILL_BASIS,
                "sizing": est.get("sizing_policy_version"),
                "risk": (rec.get("execution_plan") or {}).get(
                    "risk", {}).get("limitsSha"),
                "settlement_scope": (rec.get("settlement") or {}).get(
                    "fixture_metadata", {}).get("source"),
            },
        },
        "order": {
            "order_id": oid,
            "position_id": pid,
            "decision_id": did,
            "condition_id": contract.get("condition_id"),
            "outcome_index": int(outcome_index),
            "side": "BUY",
            "intent": str(contract.get("buy_intent") or "UNKNOWN_INTENT"),
            "liquidity": LIQUIDITY,
            # THE SUBMITTED LIMIT, NOT THE VWAP. An order does not fill
            # above its own limit; recording the VWAP here made the .62 /
            # .64 / .66 walk read as an order limited at .635556 that
            # filled twice above it, which no venue could reconcile.
            "limit_price": round(submitted_limit, 6),
            "limit_basis": "BREAK_EVEN_IMPLIED_BY_THE_VALUATION",
            "realised_vwap": round(price, 6),
            "qty": round(qty, 6),
            "filled_qty": round(qty, 6),
            "state": order_state,
            "placed_at": decision_ts,
            "fill_basis": FILL_BASIS,
            "created_at_basis": "RUNTIME_WALL_CLOCK",
        },
        # ONE FILL PER LEVEL. The order was limited at `submitted_limit`
        # and each of these prices is at or inside it, so the ledger can
        # be reconciled against the book that produced it.
        "fills": [{
            "fill_id": "%s:F%03d" % (oid, f["k"]),
            "order_id": oid,
            "at": decision_ts,
            "qty": f["qty"],
            "price": f["price"],
            "fee_usd": f["fee_usd"],
            # WHICH OBSERVATION LICENSED THIS MODELLED FILL: the venue
            # book read, named by slug and read instant. Without it the
            # fill is an assertion.
            "evidence_id": "venue_book:%s@%s" % (
                contract.get("us_market_slug"),
                (rec.get("venue_quote") or {}).get("read_at")),
            "evidence_qty": f["qty"],
            # A CROSSING ORDER JOINS NO QUEUE. 0 is not a queue-share
            # estimate; `fill_basis` says which kind of fill this was.
            "queue_share": 0.0,
            "fill_basis": FILL_BASIS,
        } for f in fills],
        "levels_evidence": levels_evidence,
        "accounting": {
            "filled_qty": round(qty, 6),
            "intended_notional_usd": round(intended_notional, 6),
            "executed_notional_usd": round(qty * price, 6),
            "unfilled_notional_usd": round(unfilled_notional, 6),
            "only_executed_notional_enters_pnl": True,
            "vwap": round(price, 6),
            "fees_usd": round(fee, 6),
            "cost_basis_usd": round(basis, 6),
            "residual_qty": round(qty, 6),
            "residual_is_directional": True,
            "residual_why": ("the whole filled quantity is unpaired "
                             "directional inventory. This lane buys one "
                             "side and does not pair, so residual equals "
                             "position until it exits or settles"),
            "realized_pnl_usd": 0.0,
            "realized_why": ("nothing has been realised at entry. Any "
                             "number other than zero here would be a "
                             "mark, and a mark is not a realisation"),
            # THE EXACT INVARIANT IS OVER THE LEVELS, NOT OVER THE VWAP.
            # `qty * vwap + fees` looks like the same thing and is not: the
            # vwap is rounded to six places, so on 900 contracts that
            # identity is off by ~5e-4 and an exact check on it fails for
            # a reason that has nothing to do with the accounting. The
            # basis is the sum of what each level actually cost plus the
            # fee actually charged at each level, and that is checked
            # exactly. The vwap identity is checked too, to a tolerance
            # that scales with the quantity it was divided by, and
            # reported rather than asserted away.
            "invariant": ("cost_basis == SUM(level qty x level price) + "
                          "SUM(level fee), exactly"),
            "invariant_ok": abs(basis - (cost + fee)) < 1e-9,
            "levels_cost_usd": round(cost, 6),
            "vwap_identity": "vwap x qty == SUM(level cost), to rounding",
            "vwap_identity_residual": round(abs(qty * price - cost), 9),
            "vwap_identity_ok": abs(qty * price - cost) <= max(
                1e-6, abs(qty) * 5e-7),
        },
        "sizing": est.get("sizing"),
    })
    return out


# ── the writes ──────────────────────────────────────────────────────

EXPERIMENT_SQL = """
    INSERT INTO rn1x_experiments (experiment_id, code_version, seed_rule,
                                  policy_register, execution_basis, notes,
                                  is_modelled)
    VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6, TRUE)
    ON CONFLICT (experiment_id) DO NOTHING
"""

EXISTING_SQL = """
    SELECT p.position_id, p.seed_qty::float8 AS seed_qty,
           p.seed_basis_usd::float8 AS seed_basis_usd,
           -- HAS THIS EXACT OBSERVATION ALREADY BEEN RECORDED? The
           -- order id carries the observation key, so its presence is
           -- what separates a replay from a second entry.
           EXISTS (SELECT 1 FROM rn1x_orders o
                    WHERE o.order_id = $5) AS this_observation_recorded,
           (SELECT count(*) FROM rn1x_orders o2
                    WHERE o2.position_id = p.position_id) AS orders
      FROM rn1x_positions p
     WHERE p.experiment_id = $1 AND p.policy = $2
       AND p.condition_id = $3 AND p.outcome_index = $4
"""


def classify_write(existing, *, position_id_, order_id) -> dict:
    """Which of the three cases this write is, before anything is written.

    NEW_EXPOSURE          nothing held here. Write.
    EXACT_REPLAY          the same exposure AND the same provider
                          observation. The ids are derived from that
                          observation, so every insert would be a
                          no-op; report it as a replay rather than
                          letting "0 rows changed" read as a write.
    NEW_QUOTE_ON_HELD     the same exposure, a DIFFERENT observation.
                          This is the case that used to write a second
                          order and a second fill against a position
                          whose size never changed. Refused.
    ADD                   the same, but explicitly requested and
                          supported. It is not supported.
    """
    if existing is None:
        return {"case": CASE_NEW, "write": True,
                "why": "this lane holds nothing in this exposure"}
    if str(existing["position_id"]) != str(position_id_):
        # A DIFFERENT POSITION ON THE SAME EXPOSURE. The derived id should
        # make this impossible, so it means something else wrote here.
        return {"case": CASE_NEW_QUOTE, "write": False,
                "existing_position_id": existing["position_id"],
                "refusal": R_ALREADY_HELD,
                "why": ("a different position already holds this exposure. "
                        "%s" % ONE_POSITION_WHY)}
    if existing["this_observation_recorded"]:
        return {"case": CASE_REPLAY, "write": False,
                "existing_position_id": existing["position_id"],
                "order_id": order_id,
                "why": ("this exact provider observation is already "
                        "recorded on this position: same exposure, same "
                        "observation, same derived ids. Nothing to write")}
    return {"case": (CASE_ADD if ADD_SUPPORTED else CASE_NEW_QUOTE),
            "write": False,
            "existing_position_id": existing["position_id"],
            "existing_qty": existing["seed_qty"],
            "existing_basis_usd": existing["seed_basis_usd"],
            "existing_orders": existing["orders"],
            "refusal": R_ALREADY_HELD,
            "add_supported": ADD_SUPPORTED,
            "why": WHY_ADD_IS_REFUSED}

POSITION_SQL = """
    INSERT INTO rn1x_positions (position_id, experiment_id, policy,
        source_trade_id, source_account, condition_id, outcome_index,
        entry_kind, entry_kind_why, seed_qty, seed_price, seed_basis_usd,
        source_ts, detected_ts, decision_ts, decision_basis, provenance,
        -- MIGRATION 119: the identity this position was opened under,
        -- written by the writer that knows it rather than rediscovered
        -- later by matching on condition alone.
        venue_market_slug, venue_buy_intent, venue_ladder_side,
        payout_event, source_valuation_id,
        -- MIGRATION 120: which venue's position model applies.
        venue)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
            $18,$19,$20,$21,$22,$23)
    ON CONFLICT (position_id) DO NOTHING
"""

DECISION_SQL = """
    INSERT INTO rn1x_decisions (decision_id, position_id, decision_ts,
        evidence_ts, evidence_id, selected_action, selection_reason,
        alternatives, ev_at_decision_usd, ev_basis,
        conditional_on_our_fill, input_labels, selected_qty,
        policy_version, governing_rule, operating_state)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11::jsonb,$12::jsonb,
            $13,$14,$15,$16)
    ON CONFLICT (decision_id) DO NOTHING
"""

ORDER_SQL = """
    INSERT INTO rn1x_orders (order_id, position_id, decision_id,
        condition_id, outcome_index, side, intent, liquidity, limit_price,
        qty, filled_qty, state, placed_at, updated_at, fill_basis,
        created_at_runtime, created_at_basis)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$13,$14,$13,$15)
    ON CONFLICT (order_id) DO UPDATE SET
        filled_qty = EXCLUDED.filled_qty, state = EXCLUDED.state,
        updated_at = EXCLUDED.updated_at
"""

FILL_SQL = """
    INSERT INTO rn1x_fills (fill_id, order_id, at, qty, price, fee_usd,
        evidence_id, evidence_qty, queue_share, fill_basis)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
    ON CONFLICT (fill_id) DO NOTHING
"""


async def ensure_experiment(conn, experiment_id) -> None:
    """Register this lane's experiment row. Idempotent."""
    await conn.execute(
        EXPERIMENT_SQL, str(experiment_id), EXPERIMENT_CODE_VERSION,
        _j({"rule": "AUTONOMOUS_ENTRY_ON_EXTERNAL_VALUATION",
            "not_seeded_from_an_observed_trade": True}),
        _j({POLICY: ENTRY_KIND_WHY}),
        FILL_BASIS,
        ("external bookmaker valuation, marketable shadow entry. No order "
         "is submitted to any venue and no capital is at risk"))


async def persist_entry(conn, plan) -> dict:
    """Write the planned rows in ONE transaction, or nothing.

    A position with no order, or an order with no fill, would be a
    half-entry that every subsequent reconciliation would have to explain.
    """
    if not plan.get("ok"):
        return {"written": False, "refusals": list(plan.get("refusals")
                                                   or []),
                "why": plan.get("why")}
    pos = plan["position"]
    # THE ADD-VERSUS-DUPLICATE QUESTION, ASKED BEFORE THE INSERT. A
    # lookup that FAILS is not an absence: it stops the write, exactly as
    # the acceptance seeder does, because concluding "no position exists"
    # from an error is how duplicate inventory gets created.
    try:
        existing = await conn.fetchrow(
            EXISTING_SQL, pos["experiment_id"], POLICY, pos["condition_id"],
            pos["outcome_index"], plan["order"]["order_id"])
    except Exception as exc:                                   # noqa: BLE001
        return {"written": False, "refusals": [R_EXISTING_LOOKUP_FAILED],
                "error": type(exc).__name__,
                "why": ("the existing-position lookup failed, so whether "
                        "this exposure is already held is unknown. That "
                        "is not evidence that it is not")}
    case = classify_write(existing, position_id_=pos["position_id"],
                          order_id=plan["order"]["order_id"])
    if not case["write"]:
        return {"written": False, "case": case["case"],
                "refusals": ([case["refusal"]] if case.get("refusal")
                             else []),
                **{k: v for k, v in case.items()
                   if k not in ("write", "case", "refusal")}}

    async with conn.transaction():
        await ensure_experiment(conn, pos["experiment_id"])
        before = await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE position_id = $1",
            pos["position_id"])
        await conn.execute(
            POSITION_SQL, pos["position_id"], pos["experiment_id"],
            pos["policy"], pos["source_trade_id"], pos["source_account"],
            pos["condition_id"], pos["outcome_index"], pos["entry_kind"],
            pos["entry_kind_why"], pos["seed_qty"], pos["seed_price"],
            pos["seed_basis_usd"], _ts(pos["source_ts"]),
            _ts(pos["detected_ts"]), _ts(pos["decision_ts"]),
            pos["decision_basis"], pos["provenance"],
            pos.get("venue_market_slug"), pos.get("venue_buy_intent"),
            pos.get("venue_ladder_side"), pos.get("payout_event"),
            (None if pos.get("source_valuation_id") is None
             else int(pos["source_valuation_id"])),
            pos.get("venue"))
        d = plan["decision"]
        await conn.execute(
            DECISION_SQL, d["decision_id"], d["position_id"],
            _ts(d["decision_ts"]), _ts(d["evidence_ts"]), d["evidence_id"],
            d["selected_action"], d["selection_reason"],
            _j(d["alternatives"]), d["ev_at_decision_usd"], d["ev_basis"],
            _j(d["conditional_on_our_fill"]), _j(d["input_labels"]),
            d["selected_qty"], d["policy_version"], d["governing_rule"],
            d["operating_state"])
        o = plan["order"]
        await conn.execute(
            ORDER_SQL, o["order_id"], o["position_id"], o["decision_id"],
            o["condition_id"], o["outcome_index"], o["side"], o["intent"],
            o["liquidity"], o["limit_price"], o["qty"], o["filled_qty"],
            o["state"], _ts(o["placed_at"]), o["fill_basis"],
            o["created_at_basis"])
        for f in plan["fills"]:
            await conn.execute(
                FILL_SQL, f["fill_id"], f["order_id"], _ts(f["at"]),
                f["qty"], f["price"], f["fee_usd"], f["evidence_id"],
                f["evidence_qty"], f["queue_share"], f["fill_basis"])

    return {"written": True,
            "case": case["case"],
            "position_id": pos["position_id"],
            "decision_id": plan["decision"]["decision_id"],
            "order_id": plan["order"]["order_id"],
            "fill_ids": [f["fill_id"] for f in plan["fills"]],
            "levels_evidence": plan["levels_evidence"],
            # WAS THIS A NEW POSITION OR THE SAME CYCLE AGAIN. Derived ids
            # make a re-run idempotent, and management still wants to know
            # which of the two happened.
            "was_already_present": bool(before),
            "duplicate_protection": ("derived ids plus ON CONFLICT DO "
                                     "NOTHING, plus the partial unique "
                                     "index from migration 116"),
            "accounting": plan["accounting"],
            "refusals": []}


def describe() -> dict:
    return {
        "policy": POLICY,
        "entryKind": ENTRY_KIND,
        "fillBasis": FILL_BASIS,
        "liquidity": LIQUIDITY,
        "writesInto": ["rn1x_positions", "rn1x_decisions", "rn1x_orders",
                       "rn1x_fills"],
        "refusals": [R_NOT_ADMISSIBLE, R_NO_FILL, R_ALREADY_HELD,
                     R_EXISTING_LOOKUP_FAILED],
        "onePositionWhy": ONE_POSITION_WHY,
        "submitsNothing": True,
    }

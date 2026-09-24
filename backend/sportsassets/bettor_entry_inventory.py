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
    price = est.get("limit_price")
    if not qty or price is None:
        out["refusals"].append(R_NO_FILL)
        out["why"] = ("the decision was admitted without a sized "
                      "marketable fill, which should be unreachable: the "
                      "gate refuses an unsized entry")
        return out

    qty = float(qty)
    price = float(price)
    fee = abs(float(fee_fn(qty=qty, price=price)))
    # THE COST BASIS INCLUDES THE FEE, matching the managed position's
    # own convention: 10 contracts at 0.64 with 0.16 of fees is a basis
    # of 6.56, not 6.40. A basis that excluded fees would make every
    # return look better than it was by exactly the cost of trading.
    basis = qty * price + fee

    contract = rec.get("contract") or {}
    pid = position_id(experiment_id=rec["experiment_id"],
                      condition_id=contract.get("condition_id"),
                      outcome_index=outcome_index)
    did = "%s:D:%d" % (pid, int(float(now)))
    oid = "%s:O:%d" % (pid, int(float(now)))

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
            "limit_price": round(price, 6),
            "qty": round(qty, 6),
            "filled_qty": round(qty, 6),
            "state": order_state,
            "placed_at": decision_ts,
            "fill_basis": FILL_BASIS,
            "created_at_basis": "RUNTIME_WALL_CLOCK",
        },
        "fill": {
            "fill_id": "%s:F000" % oid,
            "order_id": oid,
            "at": decision_ts,
            "qty": round(qty, 6),
            "price": round(price, 6),
            "fee_usd": round(fee, 6),
            # WHICH OBSERVATION LICENSED THIS MODELLED FILL: the venue
            # book read, named by slug and read instant. Without it the
            # fill is an assertion.
            "evidence_id": "venue_book:%s@%s" % (
                contract.get("us_market_slug"),
                (rec.get("venue_quote") or {}).get("read_at")),
            "evidence_qty": est.get("size"),
            # A CROSSING ORDER JOINS NO QUEUE. 0 is not a queue-share
            # estimate; `fill_basis` says which kind of fill this was.
            "queue_share": 0.0,
            "fill_basis": FILL_BASIS,
        },
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
            "invariant": "cost_basis == qty * vwap + fees",
            "invariant_ok": abs(basis - (qty * price + fee)) < 1e-6,
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
    SELECT position_id, seed_qty::float8 AS seed_qty,
           seed_basis_usd::float8 AS seed_basis_usd
      FROM rn1x_positions
     WHERE experiment_id = $1 AND policy = $2
       AND condition_id = $3 AND outcome_index = $4
"""

POSITION_SQL = """
    INSERT INTO rn1x_positions (position_id, experiment_id, policy,
        source_trade_id, source_account, condition_id, outcome_index,
        entry_kind, entry_kind_why, seed_qty, seed_price, seed_basis_usd,
        source_ts, detected_ts, decision_ts, decision_basis, provenance)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
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
            pos["outcome_index"])
    except Exception as exc:                                   # noqa: BLE001
        return {"written": False, "refusals": [R_EXISTING_LOOKUP_FAILED],
                "error": type(exc).__name__,
                "why": ("the existing-position lookup failed, so whether "
                        "this exposure is already held is unknown. That "
                        "is not evidence that it is not")}
    if existing is not None and existing["position_id"] != pos["position_id"]:
        return {"written": False, "refusals": [R_ALREADY_HELD],
                "existing_position_id": existing["position_id"],
                "why": ONE_POSITION_WHY}

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
            pos["decision_basis"], pos["provenance"])
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
        f = plan["fill"]
        await conn.execute(
            FILL_SQL, f["fill_id"], f["order_id"], _ts(f["at"]), f["qty"],
            f["price"], f["fee_usd"], f["evidence_id"], f["evidence_qty"],
            f["queue_share"], f["fill_basis"])

    return {"written": True,
            "position_id": pos["position_id"],
            "decision_id": plan["decision"]["decision_id"],
            "order_id": plan["order"]["order_id"],
            "fill_id": plan["fill"]["fill_id"],
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

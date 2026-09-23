"""PERSIST ONE RN1X RUN. The connection the audit said did not exist.

The audit's finding was exact: `bettor_rn1x_run.run` had "no API/worker
caller, no database persistence caller, no published trace route", and
"migration existing in git is not production deployment". This module is
the persistence half. It writes NOTHING that the run did not produce.

THREE PROPERTIES, EACH FOR A REASON THAT HAS ALREADY BITTEN US.

1. IDEMPOTENT BY KEY, NOT BY MEMORY. Every row is keyed on values the
   run itself derives from the source trade id, so re-persisting the
   same run after a crash, a restart or a failed commit writes the same
   rows rather than a second set. The desk's identifier collision on
   2026-09-23 -- which is why `acct_fc2d773a2afa4851` is
   ACCOUNTING_UNCERTAIN to this day -- was ids that collided BETWEEN
   books. Here the experiment id and the source trade id are both in
   every key, so two experiments cannot collide and one experiment
   replayed twice cannot double-count.

2. ONE TRANSACTION PER POSITION. A position, its decisions, its orders,
   its fills and its outcome land together or not at all. A half-written
   position is exactly the state that made the desk's accounting
   uncertain: orders with no position, fills with no order.

3. NO ROW IS A CLAIM THE RUN DID NOT MAKE. `is_modelled` is CHECKed true
   in the schema, `fill_basis` carries the execution model by name, and
   `conditional_on_our_fill` carries the run's own statement that a
   computed outcome is NOT secured. Nothing here upgrades a modelled
   fill into an execution.

WHAT IT DOES NOT DO. It does not decide anything, does not read a venue,
does not touch the desk's tables or the damaged account, and holds no
order path.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

VERSION = "BETTOR_RN1X_STORE_V1"

# The tables this module writes, in dependency order. `ready()` checks
# for exactly these before a single row is attempted, so a deployment
# whose migrations have not run reports the blocker by name instead of
# raising UndefinedTableError once per cycle.
TABLES = ("rn1x_experiments", "rn1x_positions", "rn1x_decisions",
          "rn1x_orders", "rn1x_fills", "rn1x_outcomes")

NOT_READY = "RN1X_TABLES_ABSENT"


def _ts(epoch) -> datetime | None:
    """Seconds since the epoch -> an aware UTC datetime, or None.

    The runner speaks float seconds throughout (three clocks, all
    floats). The schema speaks TIMESTAMPTZ and CHECKs their ordering.
    This is the only place the two meet.
    """
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc)


def _j(value) -> str:
    return json.dumps(value, default=str, sort_keys=True)


async def ready(conn) -> dict:
    """Are the tables actually there? Asked of the live catalog.

    NOT asked of the migrations directory, and not inferred from a
    successful deploy. Migration 100 sat committed at HEAD while the
    production schema had none of these tables, because the deploy that
    would have applied it had not finished -- and the only thing that
    settled it was reading the catalog.
    """
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = ANY($1::text[])",
        list(TABLES))
    present = {r["table_name"] for r in rows}
    missing = [t for t in TABLES if t not in present]
    return {"ok": not missing, "present": sorted(present),
            "missing": missing,
            "blocker": None if not missing else NOT_READY}


async def declare_experiment(conn, *, experiment_id, code_version,
                             seed_rule, policy_register, execution_basis,
                             notes="") -> str:
    """Declare the experiment once. Re-declaring is a no-op.

    DELIBERATELY `DO NOTHING`, not `DO UPDATE`. The policy register is
    the frozen record of what the policy WAS when the experiment started.
    A policy edited after seeing a result is a different experiment and
    needs a different id -- letting a redeploy quietly overwrite the
    register is exactly how that distinction would be lost.
    """
    await conn.execute(
        "INSERT INTO rn1x_experiments (experiment_id, code_version, "
        "seed_rule, policy_register, execution_basis, notes) "
        "VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6) "
        "ON CONFLICT (experiment_id) DO NOTHING",
        experiment_id, code_version, _j(seed_rule), _j(policy_register),
        execution_basis, notes)
    return experiment_id


def position_id(experiment_id: str, policy: str, trade_id) -> str:
    """The key, derived -- never generated.

    A generated id would make a re-run write a second position for the
    same source event, and the comparison between policies would silently
    be a comparison between duplicates.
    """
    return "%s:%s:%s" % (experiment_id, policy, trade_id)


async def persist_run(conn, out: dict, *, experiment_id: str,
                      source_account: str) -> dict:
    """Write ONE completed run. Returns what was written, or why not.

    A run that failed a step is recorded as a REFUSAL, not skipped: the
    refusals are the evidence about the feed and the inventory record,
    and dropping them would leave the table showing only the conditions
    that happened to work.
    """
    steps = out.get("steps") or {}
    seed = steps.get("SEED") or {}
    src = steps.get("SOURCE") or {}
    cls = steps.get("CLASSIFY") or {}
    policy = (out.get("policy") or {}).get("policy_id") or "UNKNOWN_POLICY"

    if not src.get("ok"):
        return {"written": False, "refused_at": out.get("failed_step"),
                "why": (steps.get(out.get("failed_step") or "") or {}).get("why"),
                "note": "no source fill: there is no position to key a row on"}

    trade_id = src.get("trade_id")
    pid = position_id(experiment_id, policy, trade_id)

    # THE REFUSALS ARE ROWS TOO -- when they have a seed to hang on.
    # A CLASSIFY or SEED refusal still names a real source trade, a real
    # condition and a real reason, and those are the measurements that
    # say WHY the experiment is not running on this condition.
    if not seed.get("ok"):
        why = seed.get("why") or cls.get("why") or "refused"
        return {"written": False, "position_id": pid,
                "refused_at": out.get("failed_step"),
                "unknown_reason": seed.get("unknown_reason")
                or cls.get("kind"),
                "why": why,
                "note": ("no inventory was assigned, so no position row "
                         "exists to attach decisions or orders to. The "
                         "refusal is returned to the caller, which "
                         "records it as a blocker rather than as a "
                         "managed position")}

    final = out.get("final_state") or {}
    manage = steps.get("MANAGE") or {}
    settle = steps.get("SETTLE") or {}
    compare = steps.get("COMPARE") or {}
    acct = out.get("accounting") or {}

    wrote = {"decisions": 0, "orders": 0, "fills": 0, "outcome": False}

    # ONE TRANSACTION. Everything below lands together or not at all.
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO rn1x_positions (position_id, experiment_id, "
            "policy, source_trade_id, source_account, condition_id, "
            "outcome_index, entry_kind, entry_kind_why, unknown_reason, "
            "position_is_a_lower_bound, seed_qty, seed_price, "
            "seed_basis_usd, source_ts, detected_ts, decision_ts) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,"
            "$15,$16,$17) "
            "ON CONFLICT (position_id) DO NOTHING",
            pid, experiment_id, policy, int(trade_id), source_account,
            out.get("condition_id") or "", int(src["outcome_index"]),
            seed.get("entry_kind") or cls.get("kind") or "UNKNOWN",
            (cls.get("why") or cls.get("reason") or seed.get("label") or ""),
            cls.get("unknown_reason"),
            bool(cls.get("is_a_lower_bound", False)),
            float(seed["qty"]), float(seed["price"]),
            float(seed["basis_usd"]),
            _ts(src["source_ts"]), _ts(src["detected_ts"]),
            _ts(seed["decision_ts"]))

        # ── decisions, keyed on the position and the instant ──────────
        #
        # EVERY decision is written, including the ones that did not act.
        # A table holding only the decisions that produced an order shows
        # activity and hides the operating state, and HOLD_NO_FEASIBLE_PAIR
        # is the most common true state of this policy.
        decisions = out.get("all_decisions")
        if decisions is None:
            # Falling back to the acted-on events alone would write only
            # the decisions that produced an order, which is the
            # activity-looking subset. The run publishes the full list;
            # this branch exists for a caller that did not.
            decisions = [e["decision"] for e in (manage.get("events") or [])
                         if isinstance(e.get("decision"), dict)]
        for i, d in enumerate(decisions):
            did = "%s:D%04d" % (pid, i)
            await conn.execute(
                "INSERT INTO rn1x_decisions (decision_id, position_id, "
                "decision_ts, evidence_ts, evidence_id, selected_action, "
                "selection_reason, alternatives, ev_at_decision_usd, "
                "ev_basis, conditional_on_our_fill, input_labels) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,"
                "$11::jsonb,$12::jsonb) "
                "ON CONFLICT (decision_id) DO NOTHING",
                did, pid, _ts(d.get("at")), _ts(d.get("at")),
                d.get("evidence_id"),
                d.get("selected_action"),
                d.get("operating_state") or d.get("why") or "",
                _j({"pair_target": d.get("pair_target"),
                    "loss_trigger": d.get("loss_trigger"),
                    "phase": d.get("phase"),
                    "placement": d.get("placement"),
                    "residual_qty": d.get("residual_qty")}),
                None,
                # EV IS NOT COMPUTED BY THIS POLICY AND THE COLUMN SAYS SO.
                # MANAGEMENT_PAIR_091_STOP_16_V1 is a cost-and-threshold
                # rule, not an expected-value maximiser. Writing a number
                # here would invent an objective the policy does not have.
                "NOT_COMPUTED_POLICY_IS_NOT_EV_MAXIMISING",
                _j({"execution_secured": bool(d.get("execution_secured")),
                    "note": ("a computed pair cost is not a secured "
                             "execution until the complementary "
                             "quantity actually fills")}),
                _j(d.get("input_labels") or {}))
            wrote["decisions"] += 1

        # ── orders and their fills ───────────────────────────────────
        for o in (final.get("all_orders") or []):
            liquidity = "RESTING" if o.get("side") == "BUY" else "TAKER"
            await conn.execute(
                "INSERT INTO rn1x_orders (order_id, position_id, "
                "condition_id, outcome_index, side, intent, liquidity, "
                "limit_price, qty, filled_qty, state, placed_at, "
                "updated_at, fill_basis) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14) "
                "ON CONFLICT (order_id) DO UPDATE SET "
                "filled_qty = EXCLUDED.filled_qty, "
                "state = EXCLUDED.state, updated_at = EXCLUDED.updated_at",
                "%s:%s" % (pid, o["order_id"]), pid,
                o["condition_id"], int(o["outcome_index"]), o["side"],
                o["intent"], liquidity, float(o["limit_price"]),
                float(o["qty"]), float(o["filled_qty"]), o["state"],
                _ts(o["placed_at"]),
                _ts(o.get("terminal_at") or o["placed_at"]),
                out.get("fill_basis") or "PRINT_THROUGH_WITH_QUEUE_SHARE_V1")
            wrote["orders"] += 1
            for k, f in enumerate(o.get("fills") or []):
                await conn.execute(
                    "INSERT INTO rn1x_fills (fill_id, order_id, at, qty, "
                    "price, fee_usd, evidence_id, evidence_qty, "
                    "queue_share, fill_basis) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) "
                    "ON CONFLICT (fill_id) DO NOTHING",
                    "%s:%s:F%03d" % (pid, o["order_id"], k),
                    "%s:%s" % (pid, o["order_id"]),
                    _ts(f["at"]), float(f["qty"]), float(f["price"]),
                    float(f["fee_usd"]), f.get("evidence_id") or "",
                    None, float(out.get("queue_share") or 0.0),
                    f.get("exec_model")
                    or "PRINT_THROUGH_WITH_QUEUE_SHARE_V1")
                wrote["fills"] += 1

        # ── the outcome, separately and only if one was observed ──────
        settled = settle.get("settled")
        if settled is not None:
            await conn.execute(
                "INSERT INTO rn1x_outcomes (position_id, settled_at, "
                "payout_per_leg, realized_cash_usd, fees_usd, "
                "residual_qty, unpaired_qty, net_usd, outcome_basis) "
                "VALUES ($1,$2,$3::jsonb,$4,$5,$6,$7,$8,$9) "
                "ON CONFLICT (position_id) DO UPDATE SET "
                "payout_per_leg = EXCLUDED.payout_per_leg, "
                "realized_cash_usd = EXCLUDED.realized_cash_usd, "
                "fees_usd = EXCLUDED.fees_usd, net_usd = EXCLUDED.net_usd, "
                "written_at = now()",
                pid, _ts(out.get("resolved_at")), _j(settled),
                _f(acct.get("realized_pnl_usd")), _f(acct.get("fees_usd")),
                _f(acct.get("residual_qty")), _f(acct.get("residual_qty")),
                _f(compare.get("ours_net_usd")),
                "OBSERVED_PAYOUT_SCORING_ONLY")
            wrote["outcome"] = True

    return {"written": True, "position_id": pid, **wrote,
            "reconciles": bool(acct.get("reconciles"))}


def _f(v):
    return None if v is None else float(v)

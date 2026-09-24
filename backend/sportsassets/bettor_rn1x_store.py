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


# ── READING A POSITION BACK, FOR CONTINUING MANAGEMENT ───────────────
#
# The challenger lane could only ever decide a position ONCE, because
# `cycle` skipped any seed already written and nothing reloaded it. These
# reads are what let a later cycle pick the same position up again.
#
# "OPEN" IS DERIVED, NEVER STORED. `rn1x_positions` has no status column
# and must not grow one: a stored conclusion on an append-only row is the
# defect `shadow_position_lifecycle` exists to avoid. A position is open
# when its seeded quantity has not been fully released by its fills.
OPEN_POSITIONS_SQL = '''
    SELECT p.position_id, p.experiment_id, p.policy, p.condition_id,
           p.outcome_index, p.source_trade_id, p.source_account,
           p.seed_qty::float8    AS seed_qty,
           p.seed_price::float8  AS seed_price,
           extract(epoch FROM p.decision_ts)::float8 AS decision_ts,
           extract(epoch FROM p.source_ts)::float8   AS source_ts,
           extract(epoch FROM p.detected_ts)::float8 AS detected_ts,
           extract(epoch FROM p.available_at)::float8 AS available_at,
           p.entry_kind, p.decision_basis,
           COALESCE(f.released, 0)::float8 AS released_qty,
           COALESCE(d.n, 0)                AS decisions_so_far,
           extract(epoch FROM d.last_at)::float8 AS last_decision_at
      FROM rn1x_positions p
      LEFT JOIN (
            SELECT o.position_id,
                   sum(CASE WHEN o.side = 'SELL' THEN fl.qty ELSE 0 END)
                       AS released
              FROM rn1x_orders o
              JOIN rn1x_fills  fl ON fl.order_id = o.order_id
             GROUP BY o.position_id) f ON f.position_id = p.position_id
      LEFT JOIN (
            SELECT position_id, count(*) n, max(decision_ts) last_at
              FROM rn1x_decisions GROUP BY position_id) d
            ON d.position_id = p.position_id
     WHERE p.experiment_id = $1
       AND COALESCE(f.released, 0) < p.seed_qty
     ORDER BY d.last_at NULLS FIRST, p.decision_ts
     LIMIT $2
'''

POSITION_ORDERS_SQL = '''
    SELECT order_id, decision_id, condition_id, outcome_index, side,
           intent, limit_price::float8 AS limit_price,
           qty::float8 AS qty, filled_qty::float8 AS filled_qty, state,
           extract(epoch FROM placed_at)::float8  AS placed_at,
           extract(epoch FROM updated_at)::float8 AS updated_at,
           fill_basis
      FROM rn1x_orders WHERE position_id = $1 ORDER BY placed_at
'''

POSITION_FILLS_SQL = '''
    SELECT f.fill_id, f.order_id, extract(epoch FROM f.at)::float8 AS at,
           f.qty::float8 AS qty, f.price::float8 AS price,
           f.fee_usd::float8 AS fee_usd, f.evidence_id, f.fill_basis
      FROM rn1x_fills f
      JOIN rn1x_orders o ON o.order_id = f.order_id
     WHERE o.position_id = $1 ORDER BY f.at
'''


async def open_positions(conn, *, experiment_id: str, limit: int = 10) -> list:
    """Positions of this experiment that still hold unreleased quantity."""
    return [dict(r) for r in
            await conn.fetch(OPEN_POSITIONS_SQL, experiment_id, int(limit))]


async def load_position(conn, position_id: str) -> dict:
    """The rows a rebuild needs: the position, its orders and its fills."""
    orders = [dict(r) for r in
              await conn.fetch(POSITION_ORDERS_SQL, position_id)]
    fills = [dict(r) for r in
             await conn.fetch(POSITION_FILLS_SQL, position_id)]
    n = await conn.fetchval(
        "SELECT count(*) FROM rn1x_decisions WHERE position_id = $1",
        position_id)
    return {"orders": orders, "fills": fills,
            "decisions_so_far": int(n or 0)}


async def persist_run(conn, out: dict, *, experiment_id: str,
                      source_account: str, decision_offset: int = 0,
                      position_id_override: str | None = None) -> dict:
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
    # THE CALLER'S OWN ID WINS WHEN IT HAS ONE, AND IT MUST.
    #
    # THE DEFECT: this always derived `experiment:policy:trade_id`. For a
    # position the caller LOADED -- continuing management -- that is only
    # the right answer when the position was seeded from a trade. An
    # AUTONOMOUS ENTRY has no trade, so the derivation produced
    # `experiment:policy:None` -- a different id for the same exposure --
    # and management tried to write a SECOND position row under it. The
    # partial unique index from migration 116 refused the insert, correctly,
    # and management failed on every cycle.
    #
    # Deriving it is still right for the seeding path, where the caller has
    # no id yet and the trade is the key. Overriding it is right for the
    # managing path, where the id already exists and must be preserved
    # rather than re-derived from a field the position does not have.
    pid = position_id_override or position_id(experiment_id, policy, trade_id)

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
            # available_at, decision_basis and decision_lag_s are written
            # here because migration 104 stopped `detected_ts` from being
            # the derived availability value. A row that omitted them
            # would leave the basis NULL, which the clock audit reports as
            # UNSET rather than silently treating as prospective.
            "seed_basis_usd, source_ts, detected_ts, decision_ts, "
            "available_at, decision_basis, decision_lag_s, "
            "clock_integrity) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,"
            "$15,$16,$17,$18,$19,$20,$21) "
            "ON CONFLICT (position_id) DO NOTHING",
            # A NULL SOURCE TRADE IS A LEGITIMATE POSITION, NOT AN ERROR.
            #
            # `int(trade_id)` raised TypeError on every management cycle for
            # an AUTONOMOUS ENTRY -- a position this system created itself
            # rather than seeding from an observed cohort fill. The column
            # is nullable precisely so that case can be recorded, and the
            # alternative to passing None here would be inventing a trade
            # id, which would claim the position came from a fill that
            # never happened and make it collide with the seeded lane's
            # uniqueness key.
            #
            # Found by running the manager against an entry-created
            # position: it was examined on every cycle and failed on every
            # cycle, so the inventory existed and was never managed.
            pid, experiment_id, policy,
            (None if trade_id is None else int(trade_id)), source_account,
            out.get("condition_id") or "", int(src["outcome_index"]),
            seed.get("entry_kind") or cls.get("kind") or "UNKNOWN",
            (cls.get("why") or cls.get("reason") or seed.get("label") or ""),
            cls.get("unknown_reason"),
            bool(cls.get("is_a_lower_bound", False)),
            float(seed["qty"]), float(seed["price"]),
            float(seed["basis_usd"]),
            _ts(src["source_ts"]), _ts(src["detected_ts"]),
            _ts(seed["decision_ts"]),
            _ts(src.get("available_at") if src.get("available_at") is not None
                else max(float(src["source_ts"]),
                         float(src["detected_ts"]))),
            out.get("decision_basis"),
            out.get("decision_lag_s"),
            # Written only when the run itself says something is off. A
            # clean row carries NULL here rather than a reassuring string.
            out.get("clock_integrity"))

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
            # THE ORDINAL CONTINUES FROM WHAT IS ALREADY STORED. Without
            # the offset a second management cycle would write D0000
            # again, hit ON CONFLICT DO NOTHING and silently drop every
            # new decision -- the position would look unmanaged forever.
            did = "%s:D%04d" % (pid, i + int(decision_offset))
            hi = d.get("hold_input") or {}
            challenger = bool(d.get("arm") == "CHALLENGER"
                              or d.get("ranking"))
            # EV_BASIS IS NEVER BLANK AND NEVER BORROWED.
            #
            #   the CHAMPION does not compute an EV at all --
            #   MANAGEMENT_PAIR_091_STOP_16_V1 is a cost-and-threshold
            #   rule, and writing a number for it would invent an
            #   objective the policy does not have;
            #   the CHALLENGER does, and when it could not, the NAMED
            #   REFUSAL is what goes in the column. An empty basis beside
            #   a null number cannot be told from a number nobody tried
            #   to compute.
            # The hold value itself, read from the ranking the decision
            # carried rather than recomputed here: a second computation
            # could disagree with the one the decision was made on.
            hold_usd = None
            for c in (d.get("alternatives") or ()):
                if c.get("action") == "HOLD":
                    hold_usd = _f(c.get("value_usd"))
            if not challenger:
                ev_usd = None
                ev_basis = "NOT_COMPUTED_POLICY_IS_NOT_EV_MAXIMISING"
            elif hi.get("available"):
                ev_usd = hold_usd
                ev_basis = "EXTERNAL_LABELLED_PROBABILITY"
            else:
                ev_usd = None
                ev_basis = "EV_HOLD_%s" % (hi.get("refusal")
                                           or "NOT_IDENTIFIED")
            await conn.execute(
                "INSERT INTO rn1x_decisions (decision_id, position_id, "
                "decision_ts, evidence_ts, evidence_id, selected_action, "
                "selection_reason, alternatives, ev_at_decision_usd, "
                "ev_basis, conditional_on_our_fill, input_labels, "
                "selected_qty, policy_version, governing_rule, "
                "operating_state, is_a_deliberate_hold, input_available, "
                "input_freshness, payout_identity, hold_value_usd, "
                "hold_value_basis, venue_translation, order_state, "
                "resulting_inventory, accounting_reconciles, "
                # THE CAUSE, STORED. A decision that could not be priced
                # now carries the CHAIN that failed -- which link, on
                # which identifiers, at which timestamps -- instead of
                # leaving a reader with EV_HOLD_NOT_IDENTIFIED and no way
                # to tell a missing fixture from a stale quote.
                "input_chain) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,"
                "$11::jsonb,$12::jsonb,$13,$14,$15,$16,$17,$18,"
                "$19::jsonb,$20::jsonb,$21,$22,$23::jsonb,$24::jsonb,"
                "$25::jsonb,$26,$27::jsonb) "
                "ON CONFLICT (decision_id) DO NOTHING",
                did, pid, _ts(d.get("at")), _ts(d.get("at")),
                d.get("evidence_id"),
                d.get("selected_action"),
                # THE REASON, NOT THE STATE. `selection_reason` used to
                # be filled with `operating_state` -- a label, not a
                # reason -- so a HOLD row said "HOLD_NO_FEASIBLE_PAIR"
                # and nothing about why. The reason comes first now and
                # the state has its own column.
                (d.get("selection_reason") or d.get("why")
                 or d.get("operating_state") or ""),
                _j({"pair_target": d.get("pair_target"),
                    "loss_trigger": d.get("loss_trigger"),
                    "phase": d.get("phase"),
                    "placement": d.get("placement"),
                    "residual_qty": d.get("residual_qty"),
                    # THE WHOLE COMPARISON, both arms. `ranked` is what
                    # scored; `refused` is what could not be priced and
                    # the named blocker for each. An action that was
                    # never evaluated must not read later as one that
                    # was evaluated and rejected.
                    "ranked": d.get("alternatives"),
                    # THE QUANTITY AND BASIS HOLD WAS VALUED ON, stored.
                    # Without it the row shows a hold value and no way to
                    # check WHICH inventory produced it -- which is the
                    # seed-sized defect all over again, one level down:
                    # unverifiable rather than wrong. Found by running the
                    # acceptance gate against what the code actually
                    # writes instead of against a fixture.
                    "hold_input": d.get("hold_input"),
                    "hold_valued_on": d.get("hold_valued_on"),
                    "refused": d.get("refused"),
                    "fallback_trigger": d.get("fallback_trigger"),
                    "resting_order_decision":
                        d.get("resting_order_decision")}),
                ev_usd, ev_basis,
                _j({"execution_secured": bool(d.get("execution_secured")),
                    "note": ("a computed pair cost is not a secured "
                             "execution until the complementary "
                             "quantity actually fills")}),
                _j(d.get("input_labels") or d.get("inputs") or {}),
                _f(d.get("selected_qty")),
                d.get("policy_id"),
                d.get("governing_rule"),
                d.get("operating_state"),
                (bool(d.get("is_a_deliberate_hold"))
                 if d.get("is_a_deliberate_hold") is not None else None),
                (bool(hi.get("available")) if challenger else None),
                _j(hi.get("freshness") or {}),
                _j(hi.get("identity") or {}),
                hold_usd,
                (ev_basis if challenger else
                 "NOT_APPLICABLE_CHAMPION_DOES_NOT_VALUE_HOLD"),
                _j(d.get("venue_translation") or {}),
                _j({"placement": d.get("placement"),
                    "order_id": d.get("order_id"),
                    "cancelled_orders": d.get("cancelled_orders")}),
                _j(d.get("resulting_inventory") or {}),
                ((d.get("resulting_inventory") or {}).get("invariant_ok")),
                (None if d.get("input_chain") is None
                 else _j(d.get("input_chain"))))
            wrote["decisions"] += 1

        # ── orders and their fills ───────────────────────────────────
        for o in (final.get("all_orders") or []):
            liquidity = "RESTING" if o.get("side") == "BUY" else "TAKER"
            await conn.execute(
                "INSERT INTO rn1x_orders (order_id, position_id, "
                "condition_id, outcome_index, side, intent, liquidity, "
                # created_at_runtime is the instant the order ACTUALLY came
                # into existence, which is what migration 104's trigger
                # compares every fill against. `placed_at` stays the
                # modelled instant the lifecycle arithmetic uses; the two
                # coincide only on a replay.
                "limit_price, qty, filled_qty, state, placed_at, "
                "updated_at, fill_basis, created_at_runtime, "
                "created_at_basis) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,"
                "$15,$16) "
                "ON CONFLICT (order_id) DO UPDATE SET "
                "filled_qty = EXCLUDED.filled_qty, "
                "state = EXCLUDED.state, updated_at = EXCLUDED.updated_at",
                # PREFIXING IS IDEMPOTENT, and it has to be. On a
                # CONTINUING cycle the order was reloaded from the ledger
                # and already carries the position prefix; prefixing it
                # again minted a SECOND order row, so the position showed
                # two open orders and the one-active-order discipline was
                # broken in the store rather than in the engine. Caught
                # on the first two-cycle run.
                (o["order_id"] if str(o["order_id"]).startswith(pid + ":")
                 else "%s:%s" % (pid, o["order_id"])), pid,
                o["condition_id"], int(o["outcome_index"]), o["side"],
                o["intent"], liquidity, float(o["limit_price"]),
                float(o["qty"]), float(o["filled_qty"]), o["state"],
                _ts(o["placed_at"]),
                _ts(o.get("terminal_at") or o["placed_at"]),
                out.get("fill_basis") or "PRINT_THROUGH_WITH_QUEUE_SHARE_V1",
                # EACH ORDER'S OWN INSTANT, NOT THE RUN'S.
                #
                # This was a single run-level floor, and on a CONTINUING
                # management cycle that is wrong: an order created in an
                # earlier cycle would be stamped with THIS cycle's clock,
                # and migration 104's trigger then -- correctly -- refused
                # its already-recorded fills as preceding their own order.
                # Caught on the first two-cycle run against real Postgres.
                #
                # `placed_at` is when the order came into existence, which
                # on a RUNTIME basis is wall-clock time. It is never
                # weaker than the old floor: on the entry path every
                # order is placed at or after the run's decision instant.
                # On a replay basis it stays NULL, because the order never
                # existed in wall-clock time at all.
                (_ts(o["placed_at"])
                 if out.get("decision_basis") == "RUNTIME_WALL_CLOCK"
                 else None),
                (out.get("decision_basis") or "UNSET"))
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

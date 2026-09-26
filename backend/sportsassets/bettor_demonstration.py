"""THE CONTROLLED DEMONSTRATION: one position, the whole deployed lifecycle.

WHAT IS DEPLOYED CODE HERE AND WHAT IS CHOSEN INPUT. Every decision and
every write below is made by the functions the scheduled lane calls:

    entry        bettor_entry_inventory.plan_entry / persist_entry
    management   bettor_rn1x_run.manage_open_position
                 bettor_rn1x_store.persist_run
    settlement   bettor_entry_settlement.settle_open_positions
    accounting   bettor_mgmt_lifecycle, through the two above

Nothing in this module decides, sizes, prices, fills or books anything
itself. What it supplies is the INPUTS -- a probability, a bid with depth,
a sale ladder, a print -- and those inputs are CHOSEN, not observed. No
venue and no bookmaker is read on this path at all.

SO WHAT IT PROVES, EXACTLY: that the shipped software carries a position
from entry through recurring management, a marketable reduction, a
completing exit and reconciled accounting, and that the ledger it leaves
behind adds up. IT PROVES NOTHING ABOUT OPPORTUNITY: it is not evidence
that such a contract existed, was priced this way, or would have filled.
That is why it is booked under its own experiment id and excluded from
strategy performance, calibration and funded eligibility AT THE QUERIES
that consume those things -- not by a label on a screen.

AND IT WRITES NOTHING INTO AN OBSERVATION TABLE. The chosen print is
passed to the manager in memory; `trades`, `markets`, `market_tokens` and
`external_valuations` are never written here, so no fabricated
observation can ever be read back as if a venue or a provider had said
it. The only rows this module creates are in the `rn1x_*` ledger, under
this experiment.
"""

from __future__ import annotations

#: The demonstration's own book. Every consuming query that means "the
#: strategy" filters on the autonomous experiment, so this id is what
#: keeps software proof out of performance.
EXPERIMENT = "CONTROLLED_DEMONSTRATION_EXTERNAL_VALUATION_V1"

WHY = (
    "a controlled scenario through the DEPLOYED shadow components. Its "
    "prices, depth and probability are CHOSEN to pass every gate; they are "
    "not a market observation and this book is not strategy performance")

#: A condition id that cannot collide with a venue's: 64 hex digits of
#: "de". Nothing resolves it, which is deliberate -- an accidental live
#: read on this identity would fail rather than return someone's market.
CID = "0x" + ("de" * 32)
SLUG = "aec-demo-side-a-2026-09-26"
PAYS_ON = "Demonstration Side A"

#: A fixed clock. The demonstration is reproducible, and a reproducible
#: run cannot date itself from the wall clock.
T0 = 1_790_000_000.0
T_ENTRY = T0 + 100.0
T_FIRST_CYCLE = T0 + 700.0       # the first recurring management cycle
CYCLE_SPACING_S = 600.0          # distinct instants, one per cycle
MAX_CYCLES = 8

#: WHY THE CYCLE COUNT IS NOT FIXED AT THREE. The manager cancels and
#: re-prices rather than leaving a stale order resting, and a cancel leaves
#: the position with NO open order for that instant -- a print arriving
#: then may not fill anything, because a print cannot fill an order that
#: did not exist when it executed. That is the deployed rule and this
#: demonstration does not bend it: it runs cycles until the position is
#: flat or the budget is spent, and reports how many that took.
WHY_THE_CYCLES_ARE_NOT_FIXED = (
    "a print may only fill an order that already existed when the print "
    "executed, and the manager re-prices by cancelling. So the number of "
    "cycles to flat is an OUTCOME of the deployed lifecycle, not a "
    "constant this demonstration chose")

#: The venue's published per-contract fee, applied by the deployed fee
#: hook rather than by arithmetic in this module.
FEE_PER_CONTRACT = 0.016

SIZE = 100.0
ENTRY_VWAP = 0.62

#: EVERY CHOSEN NUMBER, IN ONE PLACE, LABELLED. A reader must be able to
#: see the whole set of inputs that are not observations.
CHOSEN_INPUTS = {
    "entry_size_contracts": SIZE,
    "entry_vwap": ENTRY_VWAP,
    "entry_submitted_limit": 0.70,
    "fee_per_contract_usd": FEE_PER_CONTRACT,
    "probability_at_entry": 0.70,
    "probability_at_reduction": 0.55,
    "bid_at_reduction": 0.74,
    "bid_depth_at_reduction": 60.0,
    "bid_at_exit": 0.71,
    "bid_depth_at_exit": 40.0,
    "print_size_each_fill": 400.0,
    "why": ("chosen so the deployed selector has a marketable reduction "
            "and then a completing exit to choose. A market that showed "
            "these prices was never observed"),
}

#: The label that travels with every decision this module drives, so a
#: reader of `rn1x_decisions` sees it on the row and not only in a report.
INPUT_VERSION = "CHOSEN_DEMONSTRATION_INPUTS_V1"

STAGES = ("ENTRY", "MANAGE_HOLD", "REDUCE", "REDUCE_FILL", "EXIT",
          "EXIT_FILL", "SETTLEMENT", "RECONCILE")


def fee_fn(qty, price, maker=False):
    """The venue's per-contract fee. Taker and maker are the same here."""
    return FEE_PER_CONTRACT * float(qty)


def entry_record(*, experiment: str = EXPERIMENT, cid: str = CID,
                 slug: str = SLUG, pays_on: str = PAYS_ON,
                 event_key: str = "demo-2026-09-26") -> dict:
    """The admissible valuation the entry writer takes as its input.

    Shaped exactly like the row `bettor_external_shadow` hands the entry
    writer, because it goes to the same function. The VALUES are chosen.
    """
    return {
        "admissible": True,
        "experiment_id": experiment,
        "observed_at": T0,
        "received_at": T0,
        "payout_event": pays_on,
        "contract": {"condition_id": cid,
                     "us_market_slug": slug,
                     "buy_intent": "ORDER_INTENT_BUY_LONG",
                     "event_key": event_key},
        "execution_plan": {"execution": {
            "size": SIZE, "vwap": ENTRY_VWAP,
            "submitted_limit": CHOSEN_INPUTS["entry_submitted_limit"],
            "intended_notional_usd": 1000.0,
            "unfilled_notional_usd": 938.0,
            "levels_taken": [{"price": ENTRY_VWAP, "qty": SIZE,
                              "cost": ENTRY_VWAP * SIZE}]}},
    }


def _probability_row(p: float, at: float, *, slug: str = SLUG,
                     pays_on: str = PAYS_ON) -> dict:
    """A probability row shaped like the de-vigged source's own.

    IT IS NOT A MODEL OUTPUT AND NOT A VENUE READING. `provider` says
    CHOSEN so a reader of the stored decision cannot mistake this for a
    Pinnacle observation.
    """
    return {"id": None, "provider": "CHOSEN_FOR_THE_DEMONSTRATION",
            "book": "none", "devig_method": "none",
            "us_market_slug": slug, "probability": float(p),
            "probability_event": pays_on, "payout_event": pays_on,
            "payout_is_complement": False,
            "buy_intent": "ORDER_INTENT_BUY_LONG",
            "matched_side_norm": "demonstration-side-a",
            "resolver_asked_for": pays_on, "ladder_side": "ASK",
            "observed_at": float(at) - 5.0,
            "received_at": float(at) - 4.0,
            "eligibility": "ELIGIBLE"}


def inputs_for(stage: str, *, at: float, held_qty: float,
               basis_per_contract: float, slug: str = SLUG,
               pays_on: str = PAYS_ON) -> dict:
    """The chosen input snapshot for one management cycle.

    The SHAPE is `managed_inputs_for`'s; the values are chosen. `ev_hold`
    is computed by the deployed `bettor_hold_value.ev_hold` from the
    probability row, so the hold side of the comparison is the production
    valuation rather than a number written here.
    """
    from . import bettor_hold_value as HV

    if stage == "REDUCE":
        p, bid = (CHOSEN_INPUTS["probability_at_reduction"],
                  CHOSEN_INPUTS["bid_at_reduction"])
        depth = CHOSEN_INPUTS["bid_depth_at_reduction"]
    elif stage == "EXIT":
        p, bid = (CHOSEN_INPUTS["probability_at_reduction"],
                  CHOSEN_INPUTS["bid_at_exit"])
        depth = CHOSEN_INPUTS["bid_depth_at_exit"]
    else:                                   # MANAGE_HOLD and the fill cycles
        p, bid = (CHOSEN_INPUTS["probability_at_entry"], 0.55)
        depth = 500.0
    row = _probability_row(p, at, slug=slug, pays_on=pays_on)
    ev = HV.ev_hold(qty=float(held_qty),
                    basis_per_contract=float(basis_per_contract),
                    probability_row=row, now=float(at),
                    payout_event_held=pays_on)
    return {
        "available": True, "book_available": True,
        "read_at": at, "input_source": INPUT_VERSION,
        "version": INPUT_VERSION,
        "probability_row": row, "ev_hold": ev,
        "bid": float(bid), "bid_size": float(depth),
        # A SALE LADDER WITH ONE LEVEL. The selector walks it and stops
        # where the marginal price stops beating the hold, which is how
        # the partial size is DERIVED rather than picked here.
        "sale_ladder": {"levels": [
            {"level": 1, "acquisition_price": float(bid),
             "qty": float(depth)}]},
        "complement_ask": None, "complement_ask_size": None,
        "venue": "PMUS", "us_market_slug": slug,
        "payout_event": pays_on, "held_is_long": True,
        "last_price": float(bid),
        "identity_matched_independently": False,
        "price_denomination": "USD_PER_CONTRACT",
        "input_labels": {"input_availability": INPUT_VERSION,
                         "inputs_are_chosen_not_observed": True,
                         "stage": stage},
    }


#: The sentinel ids the chosen prints carry. The manager sorts prints by
#: `(ts, int(id))` and labels the fill evidence `trade:<id>`, so the id has
#: to be an integer. These two are far above any real `trades.id` and no
#: row with them exists -- the print is never inserted anywhere.
PRINT_IDS = (9_000_000_001, 9_000_000_002)


def print_for(*, at: float, price: float, size: float, ident: int) -> dict:
    """One chosen print, shaped like a `trades` row -- and never stored.

    The manager licenses a modelled fill against an observed print. This
    print is CHOSEN, it is handed over in memory, and it is not inserted
    into `trades`: a fabricated observation must not be readable by
    anything else, ever. The `trade:<id>` evidence label the fill carries
    therefore points at NO ROW, deliberately, and the position it belongs
    to is in the demonstration book.
    """
    return {"id": int(ident), "outcome_index": 0, "side": "SELL",
            "size": float(size), "price": float(price),
            "ts": float(at), "detected_at": float(at)}


# ── the stages ──────────────────────────────────────────────────────

async def run_entry(conn, *, experiment: str = EXPERIMENT,
                    cid: str = CID, slug: str = SLUG,
                    pays_on: str = PAYS_ON, now: float = T_ENTRY) -> dict:
    """ENTRY, through the deployed entry writer. Idempotent by its rule.

    The identity is a parameter so the capacity harness can run this exact
    code on its own book with its own contracts, instead of a second
    implementation that could drift from this one.
    """
    from . import bettor_entry_inventory as inv

    plan = inv.plan_entry(entry_record(experiment=experiment, cid=cid,
                                       slug=slug, pays_on=pays_on,
                                       event_key=slug),
                          now=now, outcome_index=0, fee_fn=fee_fn)
    out = {"stage": "ENTRY", "plan_ok": bool(plan.get("ok")),
           "drives": ["bettor_entry_inventory.plan_entry",
                      "bettor_entry_inventory.persist_entry"]}
    if not plan.get("ok"):
        out.update(refusals=plan.get("refusals"), why=plan.get("why"))
        return out
    await inv.ensure_experiment(conn, experiment)
    wrote = await inv.persist_entry(conn, plan)
    out["write"] = wrote
    # THE ID COMES FROM THE LEDGER WHEN THE WRITER REPLAYS. A second call
    # is an EXACT_REPLAY and writes nothing, so the writer has no new row
    # to name -- and reporting `position_id: null` for a position that
    # plainly exists made a replay look like a failure. The row is asked
    # for instead.
    pid = wrote.get("position_id")
    if not pid:
        held = await _position(conn, experiment=experiment)
        pid = (held or {}).get("position_id")
        out["position_id_source"] = "READ_BACK_FROM_THE_LEDGER_AFTER_A_REPLAY"
    out["position_id"] = pid
    out["case"] = wrote.get("case")
    out["accounting"] = plan.get("accounting")
    return out


async def _open_position(conn, *, experiment: str = EXPERIMENT,
                         cid: str | None = None):
    """The demonstration's own OPEN position row, or None.

    Strict: a management cycle may only run on a position the store calls
    open, and this returns the store's own row with every column the
    manager reads.
    """
    from . import bettor_rn1x_store as store

    rows = await store.open_positions(conn, experiment_id=experiment,
                                      limit=200)
    for r in rows:
        if cid is None or str(r["condition_id"]) == cid:
            return dict(r)
    return None


async def _position(conn, *, experiment: str = EXPERIMENT):
    """The demonstration's position row for NAMING it, open or not."""
    from . import bettor_rn1x_store as store

    rows = await store.open_positions(conn, experiment_id=experiment,
                                      limit=5)
    if rows:
        return dict(rows[0])
    # A SETTLED OR FLAT POSITION IS NOT AN ABSENT ONE. `open_positions`
    # excludes terminal rows by design; the demonstration still has to be
    # able to name the position it ran, so the row itself is read.
    row = await conn.fetchrow(
        "SELECT position_id FROM rn1x_positions WHERE experiment_id = $1 "
        "ORDER BY decision_ts LIMIT 1", experiment)
    return dict(row) if row else None


async def manage_once(conn, *, stage: str, at: float, prints=(),
                      experiment: str = EXPERIMENT, cid: str | None = None,
                      slug: str = SLUG, pays_on: str = PAYS_ON) -> dict:
    """ONE recurring management cycle, by the deployed manager.

    This is the worker's own loop body with one substitution: the input
    snapshot is the chosen one instead of `managed_inputs_for`'s live
    read. The decision function, the lifecycle, the fee hook and the
    writer are the deployed ones.
    """
    from . import bettor_rn1x_run as runner
    from . import bettor_rn1x_store as store

    pos = await _open_position(conn, experiment=experiment, cid=cid)
    if pos is None:
        return {"stage": stage, "ran": False,
                "why": "the demonstration holds no open position"}
    pid = pos["position_id"]
    rows = await store.load_position(conn, pid)
    held = float(pos["seed_qty"])
    filled = sum(float(f["qty"]) for f in rows["fills"]
                 if str(f.get("side", "BUY")).upper() == "BUY")
    sold = sum(float(f["qty"]) for f in rows["fills"]
               if str(f.get("side", "")).upper() == "SELL")
    held = (filled or held) - sold
    ci = inputs_for(stage, at=at, held_qty=max(held, 1.0),
                    basis_per_contract=float(pos["seed_price"]),
                    slug=slug, pays_on=pays_on)
    rec = runner.manage_open_position(
        position=pos, orders=rows["orders"], fills=rows["fills"],
        prints=list(prints), inputs=ci, now=at, fee_fn=fee_fn,
        decisions_so_far=rows["decisions_so_far"],
        input_chain={"version": INPUT_VERSION, "source": INPUT_VERSION,
                     "read_at": at, "decided_at": at,
                     "inputs_are_chosen_not_observed": True,
                     "stage": stage})
    wrote = await store.persist_run(
        conn, rec, experiment_id=experiment,
        source_account="", decision_offset=rows["decisions_so_far"],
        position_id_override=pid)
    d = (rec.get("all_decisions") or [{}])[0]
    return {
        "stage": stage, "ran": True, "at": at, "position_id": pid,
        "drives": ["bettor_rn1x_run.manage_open_position",
                   "bettor_rn1x_store.persist_run"],
        "selected_action": d.get("selected_action"),
        "selected_qty": d.get("selected_qty"),
        "selection_reason": d.get("selection_reason"),
        "operating_state": d.get("operating_state"),
        "acted": bool(d.get("acted")),
        "prints_offered": len(list(prints)),
        "prints_applied": ((rec.get("steps") or {}).get("MANAGE")
                           or {}).get("prints_applied"),
        "inventory_before": rec.get("inventory_before"),
        "inventory_after": rec.get("inventory_after"),
        "accounting": rec.get("accounting"),
        "written": wrote.get("written"),
        "write": wrote,
    }


async def run_settlement(conn, *, now: float,
                         experiment: str = EXPERIMENT) -> dict:
    """The DEPLOYED settlement consumer, asked about THIS book only.

    IT IS RUN, NOT ASSUMED. If the reduction and the exit closed the
    position there is no residual to settle, and the consumer says so by
    finding nothing open -- which is the honest result, and is not the
    same as claiming a venue settled a contract it never saw. No venue
    settlement response is supplied or invented here.
    """
    from . import bettor_entry_inventory as inv
    from . import bettor_entry_settlement as SETTLE

    try:
        got = await SETTLE.settle_open_positions(
            conn, experiment_id=[experiment], policy=[inv.POLICY],
            now=float(now), limit=25)
    except Exception as exc:                                   # noqa: BLE001
        return {"stage": "SETTLEMENT", "ran": False,
                "error": "%s: %s" % (type(exc).__name__, exc)}
    return {"stage": "SETTLEMENT", "ran": True, "result": got,
            "drives": "bettor_entry_settlement.settle_open_positions",
            "no_settlement_response_was_supplied": True,
            "reading": ("a residual is settled from the VENUE's own "
                        "answer. Where the position was closed in the "
                        "market there is no residual, and this consumer "
                        "finding nothing open is the result")}


async def held_qty(conn, *, experiment: str = EXPERIMENT,
                   cid: str | None = None) -> float:
    """Contracts still held, from the FILLS -- not from the seed."""
    row = await conn.fetchrow(
        "SELECT coalesce(sum(CASE WHEN upper(o.side) = 'BUY' THEN f.qty "
        "ELSE -f.qty END), 0)::float8 AS held "
        "  FROM rn1x_fills f JOIN rn1x_orders o ON o.order_id = f.order_id "
        " JOIN rn1x_positions p ON p.position_id = o.position_id "
        " WHERE p.experiment_id = $1 "
        "   AND ($2::text IS NULL OR p.condition_id = $2)",
        experiment, cid)
    return float((row or {}).get("held") or 0.0)


def _stage_for(cycle: int) -> str:
    """Cycle 0 holds, cycle 1 reduces, the rest complete the exit."""
    return ("MANAGE_HOLD" if cycle == 0
            else "REDUCE" if cycle == 1 else "EXIT")


async def run_full(conn, *, max_cycles: int = MAX_CYCLES,
                   experiment: str = EXPERIMENT, cid: str = CID,
                   slug: str = SLUG, pays_on: str = PAYS_ON) -> dict:
    """ENTRY -> MANAGEMENT -> REDUCTION -> COMPLETING EXIT -> SETTLEMENT
    -> RECONCILED P&L, all of it through the deployed components.

    Idempotent in the same way the entry is: a second call finds the
    position already there, the entry writer replays, and the management
    cycles are keyed by their decision instant, so the demonstration
    cannot inflate its own book by being run twice.
    """
    out = {"experiment_id": experiment, "why": WHY,
           "describe": describe(), "stages": [],
           "condition_id": cid, "us_market_slug": slug,
           "why_the_cycles_are_not_fixed": WHY_THE_CYCLES_ARE_NOT_FIXED}
    entry = await run_entry(conn, experiment=experiment, cid=cid, slug=slug,
                            pays_on=pays_on)
    out["stages"].append(entry)
    if not entry.get("plan_ok"):
        out["ok"] = False
        return out
    out["position_id"] = entry.get("position_id")
    for i in range(int(max_cycles)):
        at = T_FIRST_CYCLE + CYCLE_SPACING_S * i
        stage = _stage_for(i)
        held = await held_qty(conn, experiment=experiment, cid=cid)
        if held <= 1e-9 and i:
            break
        prints = ()
        if i >= 2:
            # THE PRINT IS OFFERED, NOT GUARANTEED TO FILL. Whether it
            # licenses a fill is the lifecycle's decision, and a cycle
            # that applies nothing is reported as applying nothing.
            bid = (CHOSEN_INPUTS["bid_at_exit"] if stage == "EXIT"
                   else CHOSEN_INPUTS["bid_at_reduction"])
            prints = (print_for(at=at - 60.0, price=bid,
                                size=CHOSEN_INPUTS["print_size_each_fill"],
                                ident=PRINT_IDS[0] + i),)
        elif i == 1:
            prints = ()
        got = await manage_once(conn, stage=stage, at=at, prints=prints,
                                experiment=experiment, cid=cid, slug=slug,
                                pays_on=pays_on)
        got["cycle"] = i + 1
        got["held_before_cycle"] = held
        out["stages"].append(got)
        if not got.get("ran"):
            break
    out["cycles_run"] = len([s for s in out["stages"]
                            if s.get("stage") != "ENTRY"])
    out["held_after_management"] = await held_qty(conn,
                                                  experiment=experiment,
                                                  cid=cid)
    out["position_is_flat"] = out["held_after_management"] <= 1e-9
    settle = await run_settlement(conn, now=T_FIRST_CYCLE
                                 + CYCLE_SPACING_S * max_cycles,
                                  experiment=experiment)
    out["stages"].append(settle)
    # SCOPED TO THIS CONTRACT. A harness running many lifecycles in one
    # book must not re-read every earlier position on every run -- that is
    # O(n^2) and it would also report another lifecycle's numbers as this
    # one's.
    out["reconciliation"] = await reconcile(conn, experiment=experiment,
                                            cid=cid)
    out["residual_settlement"] = (
        "NOT_APPLICABLE__THE_POSITION_WAS_CLOSED_IN_THE_MARKET"
        if out["position_is_flat"] else
        "APPLICABLE__A_RESIDUAL_REMAINS_AND_ITS_SETTLEMENT_IS_THE_VENUES")
    out["ok"] = bool(out["reconciliation"].get("reconciles"))
    return out


RECONCILE_SQL = """
    SELECT p.position_id, p.experiment_id, p.policy, p.provenance,
           p.seed_qty::float8       AS seed_qty,
           p.seed_price::float8     AS seed_price,
           p.seed_basis_usd::float8 AS seed_basis_usd,
           (SELECT count(*) FROM rn1x_decisions d
             WHERE d.position_id = p.position_id)        AS decisions,
           (SELECT count(*) FROM rn1x_orders o
             WHERE o.position_id = p.position_id)        AS orders,
           (SELECT count(*) FROM rn1x_fills f JOIN rn1x_orders o
              ON o.order_id = f.order_id
            WHERE o.position_id = p.position_id)         AS fills,
           (SELECT coalesce(sum(f.qty), 0)::float8 FROM rn1x_fills f
             JOIN rn1x_orders o ON o.order_id = f.order_id
            WHERE o.position_id = p.position_id
              AND upper(o.side) = 'BUY')                 AS bought_qty,
           (SELECT coalesce(sum(f.qty * f.price), 0)::float8
              FROM rn1x_fills f JOIN rn1x_orders o
                ON o.order_id = f.order_id
             WHERE o.position_id = p.position_id
               AND upper(o.side) = 'BUY')                AS bought_usd,
           (SELECT coalesce(sum(f.qty), 0)::float8 FROM rn1x_fills f
             JOIN rn1x_orders o ON o.order_id = f.order_id
            WHERE o.position_id = p.position_id
              AND upper(o.side) = 'SELL')                AS sold_qty,
           (SELECT coalesce(sum(f.qty * f.price), 0)::float8
              FROM rn1x_fills f JOIN rn1x_orders o
                ON o.order_id = f.order_id
             WHERE o.position_id = p.position_id
               AND upper(o.side) = 'SELL')               AS sold_usd,
           (SELECT coalesce(sum(f.fee_usd), 0)::float8 FROM rn1x_fills f
             JOIN rn1x_orders o ON o.order_id = f.order_id
            WHERE o.position_id = p.position_id)          AS fees_usd,
           (SELECT count(*) FROM rn1x_outcomes x
             WHERE x.position_id = p.position_id)         AS outcomes
      FROM rn1x_positions p
     WHERE p.experiment_id = $1
       AND ($2::text IS NULL OR p.condition_id = $2)
     ORDER BY p.decision_ts
"""

DECISION_SQL = """
    SELECT decision_id, selected_action,
           selected_qty::float8 AS selected_qty,
           extract(epoch FROM decision_ts)::float8 AS decision_ts,
           operating_state, is_a_deliberate_hold, input_available,
           hold_value_usd, hold_value_basis, accounting_reconciles,
           selection_reason
      FROM rn1x_decisions
     WHERE position_id = $1
     ORDER BY decision_ts, decision_id
"""

ORDER_SQL = """
    SELECT o.order_id, o.side, o.qty::float8 AS qty,
           o.limit_price::float8 AS limit_price, o.state, o.is_modelled,
           o.liquidity, o.fill_basis,
           extract(epoch FROM o.placed_at)::float8 AS placed_at,
           (SELECT coalesce(sum(f.qty), 0)::float8 FROM rn1x_fills f
             WHERE f.order_id = o.order_id)              AS filled_qty,
           (SELECT coalesce(sum(f.fee_usd), 0)::float8 FROM rn1x_fills f
             WHERE f.order_id = o.order_id)              AS fees_usd,
           (SELECT coalesce(avg(f.price), 0)::float8 FROM rn1x_fills f
             WHERE f.order_id = o.order_id)              AS avg_fill_price
      FROM rn1x_orders o
     WHERE o.position_id = $1
     ORDER BY o.placed_at, o.order_id
"""


async def reconcile(conn, *, experiment: str = EXPERIMENT,
                    cid: str | None = None) -> dict:
    """RECONCILED ACCOUNTING, read from the ledger and cross-footed.

    The engine's own accounting comes back on every management record.
    This reads the ROWS instead -- fills, sides, prices and fees -- and
    recomputes the same figures from them. Two independent paths to one
    number is what makes it reconciled; a single number printed twice is
    not.
    """
    rows = [dict(r) for r in await conn.fetch(RECONCILE_SQL, experiment,
                                              cid)]
    out = {"experiment_id": experiment, "positions": [],
           "counts_toward_strategy_performance": False}
    for r in rows:
        pid = r["position_id"]
        decisions = [dict(x) for x in await conn.fetch(DECISION_SQL, pid)]
        orders = [dict(x) for x in await conn.fetch(ORDER_SQL, pid)]
        bought, sold = float(r["bought_qty"]), float(r["sold_qty"])
        cost = float(r["bought_usd"])
        proceeds = float(r["sold_usd"])
        fees = float(r["fees_usd"])
        held = round(bought - sold, 9)
        matched = min(bought, sold)
        # REALISED ON THE MATCHED QUANTITY ONLY, at the average prices the
        # fills actually printed. Unsold inventory is NOT marked here: an
        # unmarked holding is reported as unmarked.
        avg_buy = (cost / bought) if bought > 1e-9 else 0.0
        avg_sell = (proceeds / sold) if sold > 1e-9 else 0.0
        realised_gross = round((avg_sell - avg_buy) * matched, 6)
        out["positions"].append({
            "position_id": pid,
            "experiment_id": r["experiment_id"], "policy": r["policy"],
            "provenance": r["provenance"],
            "decisions": int(r["decisions"]), "orders": int(r["orders"]),
            "fills": int(r["fills"]), "outcome_rows": int(r["outcomes"]),
            "bought_qty": bought, "sold_qty": sold,
            "held_qty": held, "matched_qty": matched,
            "avg_buy_price": round(avg_buy, 6),
            "avg_sell_price": round(avg_sell, 6),
            "cost_of_purchases_usd": round(cost, 6),
            "proceeds_of_sales_usd": round(proceeds, 6),
            "fees_usd": round(fees, 6),
            "realised_gross_usd": realised_gross,
            "realised_net_of_fees_usd": round(realised_gross - fees, 6),
            "open_inventory_at_cost_usd": round(held * avg_buy, 6),
            "open_inventory_mark": ("NOT_APPLICABLE_POSITION_IS_FLAT"
                                    if abs(held) < 1e-9
                                    else "NOT_IDENTIFIED"),
            "quantity_identity_holds": abs(bought - sold - held) < 1e-6,
            "decision_trail": decisions,
            "order_trail": orders,
            "fees_basis": ("every fill's own fee_usd as the deployed fee "
                           "hook computed it at %0.3f per contract"
                           % FEE_PER_CONTRACT),
        })
    out["reconciles"] = all(p["quantity_identity_holds"]
                            for p in out["positions"]) and bool(rows)
    out["what_this_is"] = (
        "the demonstration book's own accounting, recomputed from the "
        "ledger rows rather than copied from the engine's report. It is "
        "software proof and is excluded from strategy performance")
    return out


def describe() -> dict:
    """The demonstration's own contract, for a reader of the desk."""
    return {
        "experiment_id": EXPERIMENT, "why": WHY,
        "stages": list(STAGES),
        "condition_id": CID, "us_market_slug": SLUG,
        "payout_event": PAYS_ON,
        "deployed_components": [
            "bettor_entry_inventory.plan_entry",
            "bettor_entry_inventory.persist_entry",
            "bettor_rn1x_run.manage_open_position",
            "bettor_rn1x_store.persist_run",
            "bettor_entry_settlement.settle_open_positions"],
        "chosen_inputs": dict(CHOSEN_INPUTS),
        "inputs_are_chosen_not_observed": True,
        "writes_no_observation_row": True,
        "submits_orders": False, "funded": False,
        "counts_toward_strategy_performance": False,
        "excluded_at": ["command_rn1x.entry_evidence inventory",
                        "bettor_source_calibration.measure",
                        "the funded readiness evidence"],
    }

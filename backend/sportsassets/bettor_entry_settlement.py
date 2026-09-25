"""SETTLEMENT FOR SHADOW INVENTORY: THE VENUE'S ANSWER, THE LEDGER'S BOOKS.

WHY THIS EXISTS. `manage_open_position` can return SETTLE, and it returns
it with `settled=None`: the challenger's settlement input is the ranking
pipeline's observed-payout dict, which this lane never populates. So
nothing in production created an outcome, and a position whose fixture had
finished was carried as open inventory and revalued every cycle off a
bookmaker's book for a contract that no longer existed.

── WHAT THE FIRST VERSION GOT WRONG, ALL OF IT MINE ──────────────────

1 IT SETTLED GROSS FILLS, NOT RESIDUAL INVENTORY. The open query summed
  `fl.qty` across EVERY order, SELL orders included, and the accounting
  multiplied that total by the payout and subtracted the seed basis. With
  fees zero to isolate the arithmetic: buy 100 at .60 ($60 out), sell 40
  at .80 ($32 in), remaining 60 win ($60 in). Correct total cash $92, net
  $32. It reported cash $140 and net $80 -- it paid the settlement on
  contracts already sold and then forgot the sale proceeds.

  The repair is not new arithmetic. `bettor_desk.Portfolio` already
  carries per-leg quantity, average cost basis, exit proceeds, fees and
  realised P&L, already refuses to net YES against NO, and already has an
  invariant that catches drift. The ledger is replayed through it.

2 IT GUESSED WHICH CONTRACT THE POSITION HELD. "The latest admissible
  valuation for the same experiment and condition" is not a link to the
  decision that opened the position, and it never checked that the
  valuation described the side actually HELD. A condition has two sides.
  The "only one distinct identity" fallback does not help either: every
  available valuation could describe the wrong one.

  Migration 119 persists the identity ON the position. Where it is absent
  -- positions opened before it, and the acceptance position, which was
  never opened through this lane -- the held payout event is resolved
  INDEPENDENTLY from `market_tokens` at the position's own
  `outcome_index`, and any candidate valuation must NAME that same event
  or the position is refused.

3 IT TREATED ANY NONBINARY PRICE AS A REFUND. Fixed in
  `ext_pinnacle_loop.outcome_from_settlement`: a void must be DECLARED by
  the venue. A price that paid neither side in full leaves an explicit
  unresolved state here, releases nothing and writes nothing.

4 A SETTLED POSITION STAYED IN MANAGEMENT. Fixed in
  `store.OPEN_POSITIONS_SQL` via migration 119's
  `rn1x_terminal_settlements` view, which names the bases that END a
  position and excludes the scoring-only observations that do not.

── WHAT THIS MODULE DOES ────────────────────────────────────────────

  read      GET /v1/markets/{slug}/settlement -- the venue's own answer,
            through `bettor_live_read`. One transport boundary.
  identify  the position's persisted entry identity, cross-checked
            against `market_tokens`; or, absent that, the held payout
            event resolved from `market_tokens` alone.
  map       `outcome_from_settlement` -- the same venue-side identity the
            calibration join uses, so the two cannot disagree.
  account   replay the position's own orders and fills through
            `bettor_desk.Portfolio`, then settle the residual legs.
  write     `rn1x_outcomes`, at most once, `ON CONFLICT DO NOTHING`.
  release   by that row alone: `exposure_from_rows` excludes any position
            whose `realized_net_usd` is non-NULL, and `open_positions`
            excludes any position with a terminal settlement.

IT NEEDS NO BOOKMAKER ODDS. A finished contract's value is the venue's
settlement price. Requiring a live quote to settle a market that is over
is the defect that left a finished fixture carried as open inventory.

IT SUBMITS NOTHING and reseeds nothing. A settlement is a row BESIDE the
position; no `provenance` is read or rewritten, so the acceptance
position stays synthetic, modelled and unfunded.
"""

from __future__ import annotations

VERSION = "EXT_PINNACLE_ENTRY_SETTLEMENT_V2"
SUPERSEDES = "EXT_PINNACLE_ENTRY_SETTLEMENT_V1"

#: The basis recorded on the outcome row. Distinct from the challenger's
#: `OBSERVED_PAYOUT_SCORING_ONLY`, because that one means "a payout we
#: saw, used for scoring, while the position may still hold inventory" and
#: these mean "the venue settled the contract we held". Migration 119's
#: `rn1x_terminal_settlements` view lists exactly these two.
#: IMPORTED, NOT RESTATED. `bettor_rn1x_store` owns `rn1x_outcomes` and
#: declares which bases END a position; duplicating the strings here is
#: exactly how the open query and the writer would drift apart.
from . import bettor_rn1x_store as _store  # noqa: E402

BASIS_SETTLED = _store.BASIS_VENUE_SETTLED
BASIS_VOID = _store.BASIS_VENUE_VOID
TERMINAL_BASES = _store.TERMINAL_OUTCOME_BASES


def _store_not_terminal() -> str:
    return _store.NOT_TERMINALLY_SETTLED

#: Outcomes of one attempt, each its own answer.
S_SETTLED = "SETTLED"
S_VOID = "VOID"
S_ALREADY = "ALREADY_SETTLED"
S_PENDING = "VENUE_HAS_NOT_SETTLED_THIS_CONTRACT"
S_NO_SLUG = "NO_VENUE_CONTRACT_RECORDED_FOR_THIS_POSITION"
S_NO_SIDE = "VENUE_SIDE_IDENTITY_NOT_ESTABLISHED"
S_WRONG_SIDE = "THE_ONLY_AVAILABLE_IDENTITY_DESCRIBES_A_DIFFERENT_OUTCOME"
S_NO_HELD_EVENT = "THE_HELD_PAYOUT_EVENT_COULD_NOT_BE_RESOLVED"
S_UNREADABLE = "VENUE_RESOLUTION_UNREADABLE"
S_NOT_AUTHORITATIVE = "READ_ESTABLISHED_NO_VENUE_SETTLEMENT"
S_NO_REFUND_ESTABLISHED = "NEITHER_SIDE_PAID_AND_NO_REFUND_IS_ESTABLISHED"
S_NOTHING_HELD = "NO_RESIDUAL_INVENTORY_REMAINS_TO_SETTLE"
S_LEDGER_INCOMPLETE = "POSITION_LEDGER_INCOMPLETE"
S_UNPAIRED_LEG = "A_SECOND_LEG_IS_HELD_AND_ITS_COMPLEMENT_IS_NOT_CONFIRMED"

# ── HOW THE OPENING INVENTORY CAME TO EXIST ──────────────────────────
#
# THE DEFECT THIS REPLACES, AND IT WAS MINE. `replay` decided "this
# position was assigned its inventory" by testing whether the ledger
# happened to contain zero BUY fills, and applied the seed AFTER replaying
# the fills. Both halves are wrong. An ASSIGNED position that has since
# sold part of its inventory has SELL fills and no BUY fills, so the sell
# was replayed against an empty book and `Portfolio.sell` RAISED -- the
# review's own case (assigned 100 at .60, sell 40 at .80, settle 60) could
# not run at all. And an acquisition-filled position that was FULLY EXITED
# also ends with nothing held, so seeding it would have resurrected
# inventory that had been deliberately closed.
#
# The distinction is PROVENANCE, which the column already records and
# migration 117 already enumerates. It is read, never inferred.
ASSIGNED = "ASSIGNED_INVENTORY_NO_ACQUISITION_EXECUTION_OF_OURS"
ACQUIRED = "ACQUIRED_BY_OUR_OWN_RECORDED_EXECUTION"

OPENING_BY_PROVENANCE = {
    # Seeded from someone else's observed on-chain trade. `ManagedPosition`
    # books exactly this at zero fee, "because the seed is ASSIGNED at
    # RN1's own fill price -- it is not an execution of ours".
    "RN1_SIGNAL_DERIVED": ASSIGNED,
    # The acceptance harness's synthetic, modelled, unfunded position.
    "ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY": ASSIGNED,
    # This lane's own entry: the order and the fill ARE in the ledger.
    "AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW": ACQUIRED,
}

#: Kept for the reports that name how a replay opened.
OPEN_FROM_FILLS = "REPLAYED_FROM_THE_POSITIONS_OWN_ACQUISITION_FILLS"
OPEN_FROM_SEED = "ASSIGNED_ONCE_FROM_THE_SEED_BEFORE_ANY_EXIT_WAS_REPLAYED"

R_PROVENANCE_UNKNOWN = "OPENING_PROVENANCE_NOT_DECLARED_FOR_THIS_POSITION"
R_VENUE_UNKNOWN = "VENUE_POSITION_MODEL_NOT_ESTABLISHED_FOR_THIS_POSITION"
R_NO_ACQUISITION = "PROVENANCE_SAYS_ACQUIRED_BUT_NO_ACQUISITION_FILL_EXISTS"
R_NOT_TRANSLATABLE = "A_FILL_HAS_NO_TRANSLATION_UNDER_THIS_VENUE_MODEL"


def opening_mode(position) -> dict:
    """ASSIGNED or ACQUIRED, from the recorded provenance. Never inferred."""
    prov = str(position.get("provenance") or "")
    mode = OPENING_BY_PROVENANCE.get(prov)
    if mode is None:
        return {"ok": False, "refusal": R_PROVENANCE_UNKNOWN,
                "provenance": prov or None,
                "declared": sorted(OPENING_BY_PROVENANCE),
                "why": ("the provenance %r does not say how this position "
                        "came to hold inventory. It is not inferred from "
                        "whether the ledger happens to contain a BUY: an "
                        "assigned position that has sold part of its "
                        "inventory has no BUY either, and so does one that "
                        "was fully exited" % (prov or None,))}
    return {"ok": True, "mode": mode, "provenance": prov}


def replay(position, orders, fills) -> dict:
    """Rebuild the position's book, in the VENUE'S OWN position model.

    Uses `bettor_desk.Portfolio` -- the existing engine: per-leg quantity
    and average cost, a SELL realised against that average, fees expensed
    when charged, an invariant that catches drift. Nothing about
    settlement arithmetic is reinvented here.

    ── THE VENUE'S SEMANTICS ARE PRESERVED, NOT REINTERPRETED ───────
    `bettor_venue_position_model` maps the venue to its position model and
    REFUSES an unknown venue rather than defaulting. On a netting venue
    (PMUS) an opposite-side acquisition REDUCES the signed position: it is
    booked as a sale of the held leg at one minus the complement's price,
    and NO second inventory leg comes into existence. On a genuine
    two-token venue both legs are held and both are settled, and that
    accounting is kept separate rather than merged into the netting case.

    A REDUCTION MAY NEVER BECOME A REVERSAL, which is the position
    model's own rule: a reducing fill is capped at the held quantity and
    any surplus is REPORTED as a discrepancy rather than trimmed away or
    turned into opposite exposure nobody decided to take.

    ── AND THE OPENING IS ESTABLISHED FIRST, ONCE ───────────────────
    Assigned inventory is booked BEFORE any fill is replayed, at zero fee,
    exactly as `bettor_mgmt_lifecycle.ManagedPosition` already does it --
    so a subsequent exit has something to sell. An acquisition-filled
    position is never seeded, so a fully exited one stays exited.

    Returns `{"ok": False, "refusal": ...}` rather than raising, so the
    caller reports the exact state.
    """
    from . import bettor_desk as desk
    from . import bettor_venue_position_model as vpm

    cond = str(position.get("condition_id") or "")
    held = int(position.get("outcome_index") or 0)

    mode = opening_mode(position)
    if not mode["ok"]:
        return {"ok": False, "refusal": mode["refusal"], "why": mode["why"],
                "opening": mode}
    vm = vpm.model_for(position.get("venue"))
    if not vm["ok"]:
        return {"ok": False, "refusal": R_VENUE_UNKNOWN, "why": vm["why"],
                "venue_model": vm}

    pf = desk.Portfolio(0.0)
    out = {"ok": True, "portfolio": pf, "venue_model": vm,
           "opening_mode": mode["mode"], "provenance": mode["provenance"],
           "nets_opposite_side": bool(vm["opposite_side_reduces"]),
           "buys": 0, "sells": 0, "reductions": 0,
           "unknown_order_fills": 0, "discrepancies": [],
           "entry_outlay_usd": 0.0, "direct_exit_proceeds_usd": 0.0,
           "reduction_proceeds_usd": 0.0, "legs": [],
           "opening_basis": None}

    # ── 1 · THE OPENING, BEFORE ANY FILL ─────────────────────────────
    if mode["mode"] == ASSIGNED:
        seed_qty = float(position.get("seed_qty") or 0.0)
        seed_basis = float(position.get("seed_basis_usd") or 0.0)
        if seed_qty <= 0:
            return {"ok": False, "refusal": R_NO_ACQUISITION,
                    "why": ("the provenance says this position was assigned "
                            "inventory, and it records no seed quantity")}
        pf.buy(cond, held, seed_qty,
               (seed_basis / seed_qty) if seed_qty else 0.0, 0.0,
               float(position.get("decision_ts") or 0.0))
        out["opening_basis"] = OPEN_FROM_SEED
        out["seed_qty"] = seed_qty
        out["seed_basis_usd"] = seed_basis
        out["entry_outlay_usd"] = seed_basis
    else:
        out["opening_basis"] = OPEN_FROM_FILLS

    # ── 2 · EVERY FILL, IN ORDER, THROUGH THE VENUE'S MODEL ──────────
    by_order = {str(o.get("order_id")): o for o in (orders or [])}
    legs_seen = {(cond, held)} if mode["mode"] == ASSIGNED else set()
    entry_outlay = float(out["entry_outlay_usd"])
    for f in sorted(fills or [], key=lambda x: (float(x.get("at") or 0.0),
                                                str(x.get("fill_id")))):
        o = by_order.get(str(f.get("order_id")))
        if o is None:
            # A FILL WHOSE ORDER IS MISSING HAS NO SIDE AND NO LEG. It is
            # counted and the position is refused; guessing BUY would
            # invent inventory and guessing SELL would invent proceeds.
            out["unknown_order_fills"] += 1
            continue
        oi = int(o.get("outcome_index") if o.get("outcome_index") is not None
                 else held)
        c = str(o.get("condition_id") or cond)
        qty = abs(float(f.get("qty") or 0.0))
        price = float(f.get("price") or 0.0)
        fee = abs(float(f.get("fee_usd") or 0.0))
        at = float(f.get("at") or 0.0)
        side = str(o.get("side") or "").upper()
        same_leg = (c == cond and oi == held)

        if same_leg and side == "SELL":
            pf.sell(c, oi, qty, price, fee, at)
            out["direct_exit_proceeds_usd"] += qty * price
            out["sells"] += 1
            legs_seen.add((c, oi))
            continue
        if same_leg:
            pf.buy(c, oi, qty, price, fee, at)
            entry_outlay += qty * price
            out["buys"] += 1
            legs_seen.add((c, oi))
            continue

        # ── THE OPPOSITE SIDE ────────────────────────────────────────
        if out["nets_opposite_side"] and c == cond and side == "BUY":
            # ONE SIGNED NET POSITION PER MARKET. "Buying the complement of
            # something you hold is not a second position; it is a sale of
            # the first." A complement bought at p retires the long at
            # 1 - p, share for share, and leaves the book FLATTER rather
            # than holding two legs.
            held_now = pf._leg(cond, held)["qty"]
            take = min(qty, held_now)
            surplus = qty - take
            if surplus > 1e-9:
                # A REDUCTION MAY NEVER BECOME A REVERSAL. The position
                # model's own rule: 150 short against 100 long exits and
                # then opens 50 of opposite exposure nobody decided to
                # take. The cap is applied and REPORTED.
                out["discrepancies"].append({
                    "at": at, "order_id": str(o.get("order_id")),
                    "requested_qty": qty, "held_qty": held_now,
                    "surplus_qty": round(surplus, 8),
                    "why": ("an opposite-side acquisition exceeded the held "
                            "quantity. It is capped at the held quantity "
                            "because a reduction may never become a "
                            "reversal, and the surplus is reported")})
            if take > 1e-9:
                exit_px = 1.0 - price
                pf.sell(cond, held, take, exit_px, fee, at)
                out["reduction_proceeds_usd"] += take * exit_px
                out["reductions"] += 1
                legs_seen.add((cond, held))
            continue
        if not out["nets_opposite_side"] and side == "BUY":
            # A GENUINE TWO-TOKEN VENUE. Both legs are really held, which
            # is what `bettor_inventory`'s MATCHED_QTY and LOCKED_PNL
            # describe, and the two are never netted.
            pf.buy(c, oi, qty, price, fee, at)
            entry_outlay += qty * price
            out["buys"] += 1
            legs_seen.add((c, oi))
            continue
        # A SELL OF SOMETHING OTHER THAN THE HELD LEG. Under either model
        # this is not a shape this consumer has established a translation
        # for, and inventing one is how a reduction becomes a reversal.
        return {"ok": False, "refusal": R_NOT_TRANSLATABLE,
                "why": ("a %s fill on %s:%d has no translation under the "
                        "%s model for a position held on %s:%d"
                        % (side or "?", c, oi, vm["model"], cond, held))}

    out["entry_outlay_usd"] = round(entry_outlay, 8)
    out["direct_exit_proceeds_usd"] = round(out["direct_exit_proceeds_usd"], 8)
    out["reduction_proceeds_usd"] = round(out["reduction_proceeds_usd"], 8)
    out["exit_proceeds_usd"] = round(out["direct_exit_proceeds_usd"]
                                     + out["reduction_proceeds_usd"], 8)
    out["legs"] = sorted("%s:%d" % k for k in legs_seen)
    if mode["mode"] == ACQUIRED and out["buys"] == 0:
        # NOT SEEDED AS A FALLBACK. The provenance says our own acquisition
        # is in the ledger; if it is not, that is a ledger fault to report,
        # not a reason to assign inventory this position never acquired.
        return {"ok": False, "refusal": R_NO_ACQUISITION,
                "why": ("the provenance says this position was acquired by "
                        "our own execution and the ledger carries no "
                        "acquisition fill for it. Inventory is not assigned "
                        "from the seed to cover the gap"),
                "portfolio": pf, "opening_mode": ACQUIRED}
    return out


def _leg_view(pf, cond, oi) -> dict:
    leg = pf._leg(str(cond), int(oi))
    return {"qty": round(leg["qty"], 8), "cost": round(leg["cost"], 8),
            "avg_cost": (round(leg["cost"] / leg["qty"], 8)
                         if leg["qty"] > 1e-9 else None),
            "fees": round(leg["fees"], 8),
            "realized": round(leg["realized"], 8)}


def accounting(replayed, *, condition_id, held_index, payout,
               complement_payout=None) -> dict:
    """Settle the RESIDUAL and report the whole position's books.

    `payout` is what the HELD leg pays per contract. `complement_payout`
    is what any OTHER leg of the same condition pays, and it is required
    when such a leg is held: on a binary condition exactly one side pays
    1, so a second leg is settled at `1 - payout` -- but only when its
    complementarity is confirmed, which is the caller's to establish.
    `bettor_inventory.COMPLEMENT_IS_NOT_A_SELL` is why the legs are
    settled separately rather than netted into one quantity.
    """
    pf = replayed["portfolio"]
    cond = str(condition_id)
    held = int(held_index)

    before = {
        "held": _leg_view(pf, cond, held),
        "cash_before_settlement": round(pf.cash, 8),
        "realized_before_settlement": round(pf.realized, 8),
        "fees_so_far": round(pf.fees, 8),
    }
    residual = pf._leg(cond, held)["qty"]

    settled_legs = []
    pnl = pf.settle(cond, held, float(payout), 0.0)
    settled_legs.append({"leg": "%s:%d" % (cond, held),
                         "payout_per_contract": float(payout),
                         "qty_settled": round(residual, 8),
                         "pnl": round(pnl, 8),
                         "is_the_held_leg": True})
    for (c, oi), leg in list(pf.legs.items()):
        if c == cond and oi == held:
            continue
        if leg["qty"] <= 1e-9 or leg["settled"]:
            continue
        if complement_payout is None:
            raise ValueError(
                "a second leg (%s:%d) is held and no complement payout was "
                "established for it" % (c, oi))
        q = leg["qty"]
        p2 = pf.settle(c, oi, float(complement_payout), 0.0)
        settled_legs.append({"leg": "%s:%d" % (c, oi),
                             "payout_per_contract": float(complement_payout),
                             "qty_settled": round(q, 8),
                             "pnl": round(p2, 8),
                             "is_the_held_leg": False})

    # CASH RECEIVED FROM SETTLEMENT is the change in cash across it; the
    # proceeds of earlier exits are already in `cash` and are NOT counted
    # again. That double count is defect 1.
    settlement_cash = pf.cash - before["cash_before_settlement"]
    inv = pf.invariant()
    return {
        "version": VERSION,
        "residual_qty_settled": round(residual, 8),
        "payout_per_contract": float(payout),
        "complement_payout_per_contract": (
            None if complement_payout is None else float(complement_payout)),
        "settled_legs": settled_legs,
        "opening_basis": replayed.get("opening_basis"),
        "buys_replayed": replayed.get("buys"),
        "sells_replayed": replayed.get("sells"),
        "discrepancies": replayed.get("discrepancies") or [],
        # ── THE FIGURES, EACH ONE ONCE ────────────────────────────────
        "settlement_cash_usd": round(settlement_cash, 8),
        # THE FIGURE THE BROKEN VERSION DROPPED. Proceeds of contracts
        # exited BEFORE settlement are cash this position already returned,
        # and they are neither part of the settlement payout nor a second
        # copy of it.
        "prior_exit_proceeds_usd": replayed.get("exit_proceeds_usd"),
        # THE TWO MECHANISMS, APART. A direct sale and an opposite-side
        # reduction return cash the same way and are executed on different
        # ladders at different prices, and a reader has to be able to tell
        # which one this position used.
        "direct_exit_proceeds_usd": replayed.get("direct_exit_proceeds_usd"),
        "reduction_proceeds_usd": replayed.get("reduction_proceeds_usd"),
        "reductions_replayed": replayed.get("reductions"),
        "venue_model": (replayed.get("venue_model") or {}).get("model"),
        "opening_mode": replayed.get("opening_mode"),
        "entry_outlay_usd": replayed.get("entry_outlay_usd"),
        "total_cash_returned_usd": round(
            float(replayed.get("exit_proceeds_usd") or 0.0)
            + settlement_cash, 8),
        "cash_from_this_position_usd": round(pf.cash, 8),
        "fees_usd": round(pf.fees, 8),
        "realized_pnl_usd": round(pf.realized, 8),
        "net_usd": round(pf.realized, 8),
        "residual_qty": 0.0,
        "residual_settled_usd": round(settlement_cash, 8),
        "unpaired_qty": 0.0,
        "held_leg_before": before["held"],
        # THE INTERMEDIATE STATE, KEPT. Cash and fees as they stood before
        # the payout applied, so a reader can check the settlement step in
        # isolation instead of only the end state.
        "cash_before_settlement_usd": before["cash_before_settlement"],
        "realized_before_settlement_usd": before["realized_before_settlement"],
        "fees_before_settlement_usd": before["fees_so_far"],
        "reconciles": bool(inv.get("ok")),
        "invariant": inv,
        "identity": ("NET = every realised leg, entry fees expensed, exit "
                     "proceeds against average cost, and the residual "
                     "settled at the venue's payout"),
        "net_is_not": ("settlement cash minus the original basis -- that "
                       "double counts contracts already exited and drops "
                       "their proceeds"),
    }


def void_accounting(replayed, *, condition_id, held_index,
                    void_evidence) -> dict:
    """A DECLARED void. Refused unless the refund is actually established.

    Kept as its own function so that nothing can reach it by falling
    through from the settled path. A void's cash entitlement -- does the
    stake come back, do fees come back -- is a claim about the venue's
    terms, and `_declared_void` has to have found the venue stating one.
    """
    if not (void_evidence or {}).get("declared"):
        raise ValueError("a void must be declared by the venue, never "
                         "inferred from a price")
    # STAKES RETURNED means the residual's own basis comes back, which is
    # a payout equal to that leg's average cost -- not the seed basis, and
    # not the gross notional. Fees already expensed stay expensed unless
    # the venue states otherwise, and that assumption is NAMED.
    pf = replayed["portfolio"]
    leg = pf._leg(str(condition_id), int(held_index))
    avg = (leg["cost"] / leg["qty"]) if leg["qty"] > 1e-9 else 0.0
    out = accounting(replayed, condition_id=condition_id,
                     held_index=held_index, payout=avg,
                     complement_payout=avg)
    out.update(void=True, void_evidence=dict(void_evidence),
               payout_is="THE_RESIDUAL_LEGS_OWN_AVERAGE_COST_RETURNED",
               fee_treatment=("FEES_ALREADY_EXPENSED_STAY_EXPENSED_UNLESS_"
                              "THE_VENUE_STATES_A_FEE_REFUND"))
    return out


# ── the reads and the one write ──────────────────────────────────────

#: Open, unsettled positions in the named experiments and policies, with
#: the identity migration 119 persists. Terminal settlements are excluded
#: by the same view `store.OPEN_POSITIONS_SQL` uses, so "open" means the
#: same thing to the manager and to this consumer.
OPEN_ENTRY_SQL = """
    SELECT p.position_id, p.condition_id, p.outcome_index, p.policy,
           p.experiment_id, p.provenance, p.entry_kind, p.venue,
           p.seed_qty::float8        AS seed_qty,
           p.seed_price::float8      AS seed_price,
           p.seed_basis_usd::float8  AS seed_basis_usd,
           extract(epoch FROM p.decision_ts)::float8 AS decision_ts,
           p.venue_market_slug, p.venue_buy_intent, p.venue_ladder_side,
           p.payout_event, p.source_valuation_id
      FROM rn1x_positions p
     WHERE p.experiment_id = ANY($1::text[])
       AND p.policy = ANY($2::text[])
       -- THE SAME CONDITION `store.OPEN_POSITIONS_SQL` USES, from the same
       -- constant, so "open" means one thing to the manager and to this
       -- consumer. A settled position is not re-read, not re-planned and
       -- not re-written: the idempotence is in the query as well as in the
       -- insert.
       AND %s
     ORDER BY p.decision_ts
     LIMIT $3
""" % _store_not_terminal()

#: The GLOBAL catalogue's own outcome at the index this position holds.
#: This is the INDEPENDENT resolution of the held payout event: it comes
#: from `market_tokens`, not from any valuation, so a valuation can be
#: CHECKED against it rather than trusted.
HELD_TOKEN_SQL = """
    SELECT token_id, outcome, outcome_index
      FROM market_tokens
     WHERE condition_id = $1 AND outcome_index = $2
"""

ALL_TOKENS_SQL = """
    SELECT token_id, outcome, outcome_index
      FROM market_tokens WHERE condition_id = $1 ORDER BY outcome_index
"""

#: Candidate venue identities recorded for this condition, WITH the payout
#: event each one describes -- which is what makes checking them possible.
CANDIDATE_IDENTITY_SQL = """
    SELECT DISTINCT us_market_slug, buy_intent, ladder_side, payout_event,
           venue, max(id) AS valuation_id
      FROM external_valuations
     WHERE condition_id = $1 AND us_market_slug IS NOT NULL
     GROUP BY us_market_slug, buy_intent, ladder_side, payout_event, venue
"""

#: EXACTLY ONCE. The primary key is the position, and DO NOTHING means a
#: concurrent or restarted writer cannot produce a second settlement or
#: overwrite the first.
WRITE_OUTCOME_SQL = """
    INSERT INTO rn1x_outcomes (position_id, settled_at, payout_per_leg,
        realized_cash_usd, fees_usd, residual_qty, residual_settled_usd,
        unpaired_qty, net_usd, outcome_basis)
    VALUES ($1, $2, $3::jsonb, $4, $5, 0, $6, 0, $7, $8)
    ON CONFLICT (position_id) DO NOTHING
    RETURNING position_id
"""


async def held_identity(conn, row) -> dict:
    """WHICH contract this position holds, and on WHAT event it pays.

    Two independent statements have to agree before anything is settled:

      the POSITION'S OWN `outcome_index`, resolved through
      `market_tokens` into a named outcome -- the global catalogue's
      answer, owing nothing to any valuation; and

      a VENUE IDENTITY (slug + intent + ladder side), either persisted on
      the position by the writer that opened it, or found among the
      valuations recorded for this condition.

    A candidate valuation is accepted ONLY if the payout event it names IS
    the held outcome. That is the check whose absence let a valuation
    describing the other side settle a position against the opposite
    result, and no count of "distinct identities" substitutes for it.
    """
    from .workers import ext_pinnacle_loop as loop

    out = {"held_outcome": None, "held_token_id": None,
           "outcomes_listed": None, "source": None,
           "us_market_slug": None, "buy_intent": None,
           "ladder_side": None, "payout_event": None, "venue": None,
           "valuation_id": None, "candidates": 0,
           "refusal": None, "why": None}
    cond = row.get("condition_id")
    idx = row.get("outcome_index")
    try:
        tok = await conn.fetchrow(HELD_TOKEN_SQL, str(cond),
                                  int(idx if idx is not None else -1))
        all_toks = [dict(r) for r in await conn.fetch(ALL_TOKENS_SQL,
                                                     str(cond))]
    except Exception as exc:                                   # noqa: BLE001
        out["refusal"] = S_NO_HELD_EVENT
        out["why"] = ("the token read failed (%s), so the event this "
                      "position pays on is not established"
                      % type(exc).__name__)
        return out
    out["outcomes_listed"] = len(all_toks)
    if tok is None:
        out["refusal"] = S_NO_HELD_EVENT
        out["why"] = ("the global catalogue lists no token at outcome "
                      "index %r for this condition, so the event this "
                      "position pays on is not established. It is not "
                      "assumed" % (idx,))
        return out
    out["held_outcome"] = str(tok["outcome"])
    out["held_token_id"] = tok["token_id"]

    want = loop._norm_outcome(out["held_outcome"])

    # ── THE POSITION'S OWN PERSISTED IDENTITY, PREFERRED AND CHECKED ──
    if row.get("venue_market_slug"):
        named = row.get("payout_event")
        if named and loop._norm_outcome(named) != want:
            out["refusal"] = S_WRONG_SIDE
            out["why"] = ("this position's own recorded payout event %r is "
                          "not the outcome at its own index %r (%r). The "
                          "row contradicts itself and is not settled"
                          % (named, idx, out["held_outcome"]))
            return out
        out.update(source="PERSISTED_ON_THE_POSITION",
                   venue=row.get("venue"),
                   us_market_slug=row["venue_market_slug"],
                   buy_intent=row.get("venue_buy_intent"),
                   ladder_side=row.get("venue_ladder_side"),
                   payout_event=named or out["held_outcome"],
                   valuation_id=row.get("source_valuation_id"),
                   candidates=1)
        return out

    # ── OTHERWISE, A CANDIDATE THAT NAMES THE HELD OUTCOME ────────────
    try:
        cands = [dict(r) for r in
                 await conn.fetch(CANDIDATE_IDENTITY_SQL, str(cond))]
    except Exception as exc:                                   # noqa: BLE001
        out["refusal"] = S_NO_SLUG
        out["why"] = ("the candidate identity read failed (%s)"
                      % type(exc).__name__)
        return out
    out["candidates"] = len(cands)
    if not cands:
        out["refusal"] = S_NO_SLUG
        out["why"] = ("no valuation records a venue contract for this "
                      "condition, and the position carries none, so there "
                      "is no market to ask")
        return out
    match = [c for c in cands
             if loop._norm_outcome(c.get("payout_event")) == want]
    if not match:
        # THE REVIEW'S CASE. Every available valuation describes a
        # different outcome -- most likely the other side of this very
        # market. Settling against it would record the opposite result.
        out["refusal"] = S_WRONG_SIDE
        out["why"] = ("%d venue identities are recorded for this condition "
                      "and none of them names the outcome this position "
                      "holds (%r). They describe %r. Nothing is settled"
                      % (len(cands), out["held_outcome"],
                         sorted({str(c.get("payout_event")) for c in cands})))
        return out
    if len({(c["us_market_slug"], c.get("buy_intent"),
             c.get("ladder_side")) for c in match}) > 1:
        out["refusal"] = S_NO_SIDE
        out["why"] = ("%d different venue identities name this position's "
                      "outcome, so which contract it holds is not "
                      "established" % len(match))
        return out
    c = match[0]
    out.update(source="RESOLVED_FROM_MARKET_TOKENS_AND_CHECKED",
               # THE VENUE COMES WITH THE IDENTITY. A position recorded
               # before migration 120 carries none, and the valuation that
               # named its contract is the only thing that knows which
               # venue's position model governs it. Absent both, the replay
               # refuses -- `model_for` never defaults.
               venue=(row.get("venue") or c.get("venue")),
               us_market_slug=c["us_market_slug"],
               buy_intent=c.get("buy_intent"),
               ladder_side=c.get("ladder_side"),
               payout_event=c.get("payout_event"),
               valuation_id=c.get("valuation_id"))
    return out


def plan(row, ident, replayed, resolution) -> dict:
    """What to do with ONE open position. Pure.

    `ident` is `held_identity`'s answer, `replayed` is `replay`'s. Keeping
    all three of the reads out of here is what lets the counterexamples be
    exercised without a venue and without a database.
    """
    from .workers import ext_pinnacle_loop as loop

    out = {"position_id": row.get("position_id"),
           "policy": row.get("policy"),
           "us_market_slug": (ident or {}).get("us_market_slug"),
           "held_outcome": (ident or {}).get("held_outcome"),
           "identity_source": (ident or {}).get("source"),
           "venue_status": str((resolution or {}).get("status") or ""),
           "settlement_read": None, "side_map": None, "venue_class": None,
           "status": None, "accounting": None, "basis": None,
           "needed_fresh_odds": False, "why": None}

    if (ident or {}).get("refusal"):
        out["status"] = ident["refusal"]
        out["why"] = ident.get("why")
        return out
    # A REPLAY THAT COULD NOT BE DONE IS NOT AN EMPTY BOOK. An unknown
    # provenance, an unestablished venue model, a missing acquisition fill
    # and an untranslatable fill each stop the settlement by name.
    if not replayed.get("ok"):
        out["status"] = replayed.get("refusal") or S_LEDGER_INCOMPLETE
        out["why"] = replayed.get("why")
        out["venue_model"] = (replayed.get("venue_model") or {}).get("model")
        return out
    out["opening_mode"] = replayed.get("opening_mode")
    out["venue_model"] = (replayed.get("venue_model") or {}).get("model")
    out["nets_opposite_side"] = replayed.get("nets_opposite_side")
    if replayed.get("discrepancies"):
        out["discrepancies"] = replayed["discrepancies"]
    if replayed.get("unknown_order_fills"):
        out["status"] = S_LEDGER_INCOMPLETE
        out["why"] = ("%d fills reference an order this position does not "
                      "have, so neither their side nor their leg is known"
                      % replayed["unknown_order_fills"])
        return out

    pf = replayed["portfolio"]
    cond = str(row.get("condition_id") or "")
    held = int(row.get("outcome_index") or 0)
    residual = pf._leg(cond, held)["qty"]
    out["residual_qty"] = round(residual, 8)
    out["legs_held"] = [k for k in replayed.get("legs") or []]
    if residual <= 1e-9:
        # FULLY EXITED. There is nothing left for a payout to pay, and
        # writing a settlement would book cash against inventory that was
        # already sold. The position is closed by its SELL fills, which is
        # what `open_positions` already measures.
        out["status"] = S_NOTHING_HELD
        out["why"] = ("every contract was exited before settlement, so "
                      "there is no residual for the venue's payout to "
                      "apply to. Realised P&L stands at %.6f"
                      % pf.realized)
        out["realized_pnl_usd"] = round(pf.realized, 8)
        return out

    got = loop.outcome_from_settlement(resolution,
                                       buy_intent=ident.get("buy_intent"),
                                       ladder_side=ident.get("ladder_side"))
    out["settlement_read"] = got.get("settlement_read")
    out["side_map"] = got.get("side_map")
    out["venue_class"] = got.get("class")

    if got.get("class") == loop.C_SIDE_UNKNOWN:
        out["status"] = S_NO_SIDE
        out["why"] = ("the recorded buy intent and ladder side do not "
                      "agree, so which side of the venue's market this "
                      "position holds is not established and the "
                      "settlement price cannot be mapped onto it")
        return out

    # ── A SECOND HELD LEG, WHICH ONLY A TWO-TOKEN VENUE CAN HAVE ─────
    #
    # On a netting venue the replay has already retired the opposite side
    # against the held one, so a second leg here would mean the model was
    # applied wrongly; it is reported rather than settled.
    others = [(c, oi) for (c, oi), leg in pf.legs.items()
              if leg["qty"] > 1e-9 and not (c == cond and oi == held)]
    same_condition = [k for k in others if k[0] == cond]
    if others and replayed.get("nets_opposite_side"):
        out["status"] = S_UNPAIRED_LEG
        out["why"] = ("this venue nets one signed position per market, so "
                      "a second held leg (%r) should not exist. It is not "
                      "settled as a pair"
                      % [("%s:%d" % k) for k in others])
        return out
    if others and len(same_condition) != len(others):
        out["status"] = S_UNPAIRED_LEG
        out["why"] = ("this position holds a leg on another condition "
                      "(%r), which no single settlement price settles"
                      % [("%s:%d" % k) for k in others if k[0] != cond])
        return out
    complement_ok = bool(same_condition) and (
        int(ident.get("outcomes_listed") or 0) == 2)
    if same_condition and not complement_ok:
        out["status"] = S_UNPAIRED_LEG
        out["why"] = ("a second leg of this condition is held and the "
                      "catalogue lists %r outcomes, so the two are not "
                      "established as complements. A pair is a claim the "
                      "identity layer makes, not an arithmetic fact"
                      % (ident.get("outcomes_listed"),))
        return out

    if got.get("outcome") is not None:
        payout = 1.0 if int(got["outcome"]) == 1 else 0.0
        out["status"] = S_SETTLED
        out["basis"] = BASIS_SETTLED
        out["accounting"] = accounting(
            replayed, condition_id=cond, held_index=held, payout=payout,
            complement_payout=((1.0 - payout) if complement_ok else None))
        out["why"] = ("the venue settled its long side at %s; this "
                      "position is %s, so the %.6f contracts it still "
                      "held pay %.1f each"
                      % (out["settlement_read"], out["side_map"],
                         residual, payout))
        return out

    if got.get("class") == loop.B_CONFIRMED_VOID:
        out["status"] = S_VOID
        out["basis"] = BASIS_VOID
        out["accounting"] = void_accounting(
            replayed, condition_id=cond, held_index=held,
            void_evidence=got.get("void_evidence"))
        out["why"] = ("the venue DECLARED a void (%s = %s), so the "
                      "residual's own basis is returned"
                      % ((got.get("void_evidence") or {}).get("field"),
                         (got.get("void_evidence") or {}).get("value")))
        return out

    if got.get("class") == loop.C_NEITHER_SIDE_PAID:
        # EXPLICITLY UNRESOLVED. Nothing written, nothing released.
        out["status"] = S_NO_REFUND_ESTABLISHED
        out["void_evidence"] = got.get("void_evidence")
        out["why"] = got.get("why")
        return out

    st = out["venue_status"]
    if st == "PENDING":
        out["status"] = S_PENDING
        out["why"] = ("the venue lists the contract and has published no "
                      "settlement for it. The fixture may be over; this "
                      "venue has not settled it")
    elif st in ("UNMATCHED", "UNREADABLE", ""):
        out["status"] = S_UNREADABLE
        out["why"] = ("the venue's resolution could not be read (%s%s). "
                      "That is a read failure, not a pending settlement"
                      % (st or "NO_STATUS",
                         (": " + str((resolution or {}).get("error")))
                         if (resolution or {}).get("error") else ""))
    else:
        out["status"] = S_NOT_AUTHORITATIVE
        out["why"] = ("the read established %s, which is not the venue "
                      "settling this contract" % out["venue_class"])
    return out


async def settle_open_positions(conn, *, experiment_id, policy, now,
                                limit=25, read_resolution=None) -> dict:
    """Settle every open position, in the named experiments and policies,
    that the venue has settled.

    `experiment_id` and `policy` each take a string or a sequence of
    strings: the entry lane is one policy and the acceptance position is
    another under a different experiment, and both are settled by reading
    the same venue endpoint.

    `read_resolution(slug) -> dict` is the ONE transport boundary. It
    defaults to the loop's paced venue reader; a caller supplies a fixture
    to exercise everything else, which is all of it.

    Never raises. Reports one entry per position with the exact state.
    """
    import asyncio
    import json as _json

    from .workers import ext_pinnacle_loop as loop

    store = _store
    reader = read_resolution
    out = {"ran": True, "version": VERSION, "supersedes": SUPERSEDES,
           "examined": 0, "settled": 0, "void": 0, "already": 0,
           "unresolved": 0, "errors": 0, "nothing_held": 0,
           "by_status": {}, "results": [], "requires_fresh_odds": False}
    exps = _as_list(experiment_id)
    pols = _as_list(policy)
    out["experiments"] = exps
    out["policies"] = pols
    try:
        rows = [dict(r) for r in await conn.fetch(
            OPEN_ENTRY_SQL, exps, pols, int(limit))]
    except Exception as exc:                                   # noqa: BLE001
        return {"ran": False, "error": type(exc).__name__,
                "why": "the open-position read failed; nothing was settled"}
    out["examined"] = len(rows)
    for row in rows:
        pid = row["position_id"]
        try:
            ident = await held_identity(conn, row)
        except Exception as exc:                               # noqa: BLE001
            out["errors"] += 1
            out["results"].append({"position_id": pid,
                                   "status": S_NO_SIDE,
                                   "error": type(exc).__name__})
            continue
        # THE LEDGER IS REBUILT THROUGH THE EXISTING READER, so a fill
        # this consumer cannot see is a fill nothing else can see either.
        # The venue travels with the identity when the position row itself
        # does not carry one.
        row = dict(row, venue=(row.get("venue") or ident.get("venue")))
        try:
            led = await store.load_position(conn, pid)
            replayed = replay(row, led.get("orders"), led.get("fills"))
        except Exception as exc:                               # noqa: BLE001
            out["errors"] += 1
            out["results"].append({"position_id": pid,
                                   "status": S_LEDGER_INCOMPLETE,
                                   "error": "%s: %s" % (type(exc).__name__,
                                                        exc)})
            continue

        slug = ident.get("us_market_slug")
        res = {}
        # A POSITION WITH NOTHING LEFT TO SETTLE, OR NO IDENTITY, IS NOT
        # WORTH A PACED VENUE READ. The refusal is the same either way and
        # the request budget is shared with the collector.
        need_read = (bool(slug) and not ident.get("refusal")
                     and bool(replayed.get("ok")))
        if need_read:
            pf = replayed["portfolio"]
            need_read = pf._leg(str(row.get("condition_id") or ""),
                                int(row.get("outcome_index") or 0))["qty"] \
                > 1e-9
        if need_read:
            try:
                if reader is not None:
                    res = reader(slug)
                else:
                    res = await asyncio.to_thread(
                        loop._read_resolution_blocking, slug)
            except Exception as exc:                           # noqa: BLE001
                out["errors"] += 1
                out["results"].append({"position_id": pid,
                                       "status": S_UNREADABLE,
                                       "error": type(exc).__name__})
                continue
        try:
            got = plan(row, ident, replayed, res)
        except Exception as exc:                               # noqa: BLE001
            out["errors"] += 1
            out["results"].append({"position_id": pid,
                                   "status": "PLAN_FAILED",
                                   "error": "%s: %s" % (type(exc).__name__,
                                                        exc)})
            continue
        got["identity"] = {k: ident.get(k) for k in
                           ("held_outcome", "source", "payout_event",
                            "buy_intent", "ladder_side", "valuation_id",
                            "candidates", "outcomes_listed", "venue")}
        if got["accounting"] is None:
            if got["status"] == S_NOTHING_HELD:
                out["nothing_held"] += 1
            else:
                out["unresolved"] += 1
            out["by_status"][got["status"]] = \
                out["by_status"].get(got["status"], 0) + 1
            out["results"].append(got)
            continue
        acct = got["accounting"]
        payload = dict(acct, basis=got["basis"],
                       side_map=got["side_map"],
                       settlement_read=got["settlement_read"],
                       held_outcome=got["held_outcome"],
                       identity_source=got["identity_source"],
                       payout_event=ident.get("payout_event"),
                       us_market_slug=slug,
                       valuation_id=ident.get("valuation_id"))
        try:
            wrote = await conn.fetchval(
                WRITE_OUTCOME_SQL, pid, _settled_at(res, now),
                _json.dumps(payload, default=str),
                acct["settlement_cash_usd"], acct["fees_usd"],
                acct["residual_settled_usd"], acct["net_usd"],
                got["basis"])
        except Exception as exc:                               # noqa: BLE001
            out["errors"] += 1
            out["results"].append(dict(got, status="WRITE_FAILED",
                                       error=type(exc).__name__))
            continue
        # A SKIPPED INSERT IS NOT A SETTLEMENT. `DO NOTHING` returns no
        # row, which is exactly what a second run across a restart must
        # report -- and it must not be counted as having settled anything.
        if wrote is None:
            out["already"] += 1
            got = dict(got, status=S_ALREADY, written=False)
        else:
            out["settled" if got["status"] == S_SETTLED else "void"] += 1
            got = dict(got, written=True)
        out["by_status"][got["status"]] = \
            out["by_status"].get(got["status"], 0) + 1
        out["results"].append(got)
    return out


def _as_list(v):
    """One name or several, always a list of strings for `= ANY($1)`."""
    if v is None:
        return []
    if isinstance(v, (str, bytes)):
        return [str(v)]
    return [str(x) for x in v]


def _settled_at(resolution, now):
    """The VENUE'S settlement instant when it gives one, ours otherwise.

    Returned as a datetime because `rn1x_outcomes.settled_at` is a
    timestamptz and the column should carry the venue's own time when the
    venue states it.
    """
    import datetime as _dt

    raw = (resolution or {}).get("settled_at")
    if raw:
        try:
            s = str(raw).replace("Z", "+00:00")
            got = _dt.datetime.fromisoformat(s)
            if got.tzinfo is None:
                got = got.replace(tzinfo=_dt.timezone.utc)
            return got
        except Exception:                                      # noqa: BLE001
            pass
    return _dt.datetime.fromtimestamp(float(now), tz=_dt.timezone.utc)


def describe() -> dict:
    return {
        "version": VERSION,
        "supersedes": SUPERSEDES,
        "reads": "/v1/markets/{slug}/settlement via bettor_live_read",
        "identity": ("the position's own persisted venue identity, "
                     "cross-checked against market_tokens; or the held "
                     "payout event resolved from market_tokens alone, "
                     "with any candidate valuation required to name it"),
        "mapping": ("ext_pinnacle_loop.outcome_from_settlement -- the same "
                    "venue-side identity the calibration join uses"),
        "accounting": ("bettor_desk.Portfolio replayed over the position's "
                       "own orders and fills: residual quantity, average "
                       "cost basis, prior exit proceeds and every fee"),
        "settles": "THE_RESIDUAL_NOT_THE_GROSS_FILLS",
        "requires_fresh_bookmaker_odds": False,
        "why_not": ("a finished contract's value is the venue's settlement "
                    "price. A bookmaker probability is irrelevant to it, "
                    "and requiring one to settle a finished market is what "
                    "left a settled fixture open"),
        "void_requires": ("the venue to DECLARE a void or refund in an "
                          "identified field. A nonbinary price establishes "
                          "only that neither side was paid in full"),
        "writes": "rn1x_outcomes, exactly once per position",
        "releases_exposure_by": (
            "the outcome row itself -- exposure_from_rows excludes any "
            "position whose realized_net_usd is non-NULL, and "
            "open_positions excludes any terminal settlement"),
        "submits_orders": False,
        "terminal_bases": list(TERMINAL_BASES),
        "statuses": [S_SETTLED, S_VOID, S_ALREADY, S_PENDING, S_NO_SLUG,
                     S_NO_SIDE, S_WRONG_SIDE, S_NO_HELD_EVENT,
                     S_UNREADABLE, S_NOT_AUTHORITATIVE,
                     S_NO_REFUND_ESTABLISHED, S_NOTHING_HELD,
                     S_LEDGER_INCOMPLETE, S_UNPAIRED_LEG],
    }

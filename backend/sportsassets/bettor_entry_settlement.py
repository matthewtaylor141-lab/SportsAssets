"""THE ENTRY LANE'S SETTLEMENT CONSUMER: PRODUCTION CODE THAT CLOSES A
POSITION FROM THE VENUE'S OWN ANSWER.

WHY THIS EXISTS, AND IT IS A DEFECT REPORT. The lifecycle demonstration
INSERTED its own row into `rn1x_outcomes` and then asserted that the row
it had just written said what it wanted. Nothing in production created an
outcome for an entry-lane position: `manage_open_position` returns SETTLE
with `settled=None`, because the challenger's settlement input is the
ranking pipeline's observed-payout dict and that dict is never populated
for this lane. So the test proved that Postgres stores what you put in it.

WHAT THIS MODULE DOES, all of it production code with one stubbable
transport boundary:

  1 READS THE VENUE'S AUTHORITATIVE RESOLUTION for the contract the
    position actually holds -- `GET /v1/markets/{slug}/settlement`, the
    venue's own answer, not an inference from prices and not a sports
    feed's "Final".
  2 MAPS IT THROUGH THE VERIFIED VENUE-SIDE IDENTITY. The settlement
    price is about the venue's LONG side; a short exposure pays one minus
    it. The mapping is `ext_pinnacle_loop.outcome_from_settlement`, the
    same one the calibration join uses, so the two can never disagree.
  3 COMPLETES THE ACCOUNTING: payout, realised cash, fees, residual
    closed to zero, net.
  4 WRITES `rn1x_outcomes` EXACTLY ONCE. `ON CONFLICT DO NOTHING` plus a
    pre-check, so a restart mid-cycle cannot double-settle and a second
    run reports ALREADY_SETTLED rather than rewriting.
  5 RELEASES EXPOSURE as a consequence of 4 and not by a separate write:
    `bettor_entry_execution.exposure_from_rows` excludes any position
    whose `realized_net_usd` is non-NULL, so the rails free themselves
    when the outcome lands. One fact, one place.

WHAT IT DOES NOT DO.

  * IT NEVER NEEDS FRESH BOOKMAKER ODDS. A finished contract's value is
    the venue's settlement price, and a probability from a bookmaker is
    irrelevant to it. Requiring a live quote to settle a finished market
    is the defect that left the Arizona position open while the fixture
    had been over for days.
  * IT NEVER INFERS RESOLUTION. A pending, unreadable, unmatched,
    named-winner or converged-price answer is REPORTED BY ITS OWN NAME
    and leaves the position open. A sports feed reporting "Final"
    establishes that a game ended, not that this venue settled this
    contract under rules compatible with our terms.
  * IT NEVER SUBMITS ANYTHING. It reads and it writes our own ledger.
"""

from __future__ import annotations

VERSION = "EXT_PINNACLE_ENTRY_SETTLEMENT_V1"

#: The basis recorded on the outcome row. Distinct from the challenger's
#: `OBSERVED_PAYOUT_SCORING_ONLY`, because that one means "a payout we
#: saw, used for scoring" and this one means "the venue's own settlement
#: of the contract we hold".
BASIS_SETTLED = "VENUE_AUTHORITATIVE_SETTLEMENT_OF_THE_HELD_CONTRACT"
BASIS_VOID = "VENUE_CONFIRMED_VOID_STAKES_RETURNED"

#: Outcomes of one attempt, each its own answer.
S_SETTLED = "SETTLED"
S_VOID = "VOID"
S_ALREADY = "ALREADY_SETTLED"
S_PENDING = "VENUE_HAS_NOT_SETTLED_THIS_CONTRACT"
S_NO_SLUG = "NO_VENUE_CONTRACT_RECORDED_FOR_THIS_POSITION"
S_NO_SIDE = "VENUE_SIDE_IDENTITY_NOT_ESTABLISHED"
S_UNREADABLE = "VENUE_RESOLUTION_UNREADABLE"
S_NOT_AUTHORITATIVE = "READ_ESTABLISHED_NO_VENUE_SETTLEMENT"
S_LEDGER_INCOMPLETE = "POSITION_LEDGER_INCOMPLETE"


def accounting(*, filled_qty, cost_basis_usd, fees_usd, outcome,
               void=False) -> dict:
    """The settled position's books. Pure; no I/O, no clock.

    `cost_basis_usd` INCLUDES the fees, which is how the entry wrote it,
    so the stake alone is the basis less the fees. Both are reported
    because a void returns the stake and the fee treatment is a separate
    question.
    """
    q = float(filled_qty or 0.0)
    basis = float(cost_basis_usd or 0.0)
    fees = float(fees_usd or 0.0)
    stake = basis - fees
    if void:
        # STAKES RETURNED, AND THE FEE TREATMENT IS STATED RATHER THAN
        # ASSUMED AWAY. Whether a venue refunds its fee on a voided
        # market is not in the captured terms, so the conservative
        # reading is taken and NAMED: the stake comes back, the fee does
        # not. A reader who learns otherwise can see exactly which
        # assumption to revise.
        cash = stake
        return {
            "payout_per_contract": None,
            "void": True,
            "stake_returned_usd": round(stake, 8),
            "fee_treatment": ("FEES_ASSUMED_NOT_RETURNED_WHICH_IS_THE_"
                              "CONSERVATIVE_READING_AND_IS_NOT_IN_THE_"
                              "CAPTURED_TERMS"),
            "realized_cash_usd": round(cash, 8),
            "fees_usd": round(fees, 8),
            "cost_basis_usd": round(basis, 8),
            "residual_qty": 0.0,
            "residual_settled_usd": round(cash, 8),
            "unpaired_qty": 0.0,
            "net_usd": round(cash - basis, 8),
            "reconciles": True,
            "identity": "NET = REALIZED_CASH - COST_BASIS_INCLUDING_FEES",
        }
    # `int(0.5)` IS 0, so the cast is checked rather than trusted: a
    # fractional outcome is a void or a misread unit, never a rounded win.
    o = float(outcome)
    if o not in (0.0, 1.0):
        raise ValueError("outcome must be 0 or 1, got %r" % (outcome,))
    o = int(o)
    payout = 1.0 if o == 1 else 0.0
    cash = q * payout
    return {
        "payout_per_contract": payout,
        "void": False,
        "outcome": o,
        "realized_cash_usd": round(cash, 8),
        "fees_usd": round(fees, 8),
        "cost_basis_usd": round(basis, 8),
        "stake_usd": round(stake, 8),
        # SETTLEMENT CLOSES THE INVENTORY. There is nothing left to hold:
        # the contract no longer exists and its value is cash.
        "residual_qty": 0.0,
        "residual_settled_usd": round(cash, 8),
        "unpaired_qty": 0.0,
        "net_usd": round(cash - basis, 8),
        "reconciles": True,
        "identity": "NET = REALIZED_CASH - COST_BASIS_INCLUDING_FEES",
    }


# ── the reads and the one write ──────────────────────────────────────

#: Open entry-lane positions with the ledger figures and the venue
#: identity needed to settle them. `o.position_id IS NULL` is the "not yet
#: settled" condition, read from the same table the write lands in, so
#: there is no separate flag to drift.
# ── HOW THE VENUE CONTRACT IS FOUND, AND WHY IN TWO STEPS ────────────
#
# `rn1x_positions` carries the GLOBAL condition and an outcome index, not
# a venue-native slug. The slug lives on `external_valuations`.
#
# For an ENTRY-LANE position the link is exact: the admissible valuation
# in the same experiment for the same condition IS the decision that
# created the position, so its slug, buy intent and ladder side are that
# position's own identity.
#
# For a position created by another route -- the acceptance position, for
# instance -- there is no such row. A valuation for the same condition may
# still name the venue contract, but it may describe the OTHER side of the
# market, and `external_valuations` does not record an outcome index to
# check that against. So the fallback is only used when every valuation
# for that condition agrees on one identity; when they disagree the
# position is REFUSED with VENUE_SIDE_IDENTITY_NOT_ESTABLISHED rather than
# settled against a side it may not hold.
OPEN_ENTRY_SQL = """
    SELECT p.position_id, p.condition_id, p.outcome_index, p.policy,
           p.experiment_id,
           p.seed_qty::float8        AS seed_qty,
           p.seed_basis_usd::float8  AS cost_basis_usd,
           COALESCE(f.fees, 0)::float8   AS fees_usd,
           COALESCE(f.filled, 0)::float8 AS filled_qty,
           COALESCE(own.us_market_slug, any_.us_market_slug)
               AS us_market_slug,
           COALESCE(own.buy_intent, any_.buy_intent)   AS buy_intent,
           COALESCE(own.ladder_side, any_.ladder_side) AS ladder_side,
           COALESCE(own.payout_event, any_.payout_event) AS payout_event,
           COALESCE(own.valuation_id, any_.valuation_id) AS valuation_id,
           (own.valuation_id IS NOT NULL) AS identity_is_the_own_decision,
           COALESCE(any_.identities, 0)   AS candidate_identities
      FROM rn1x_positions p
      LEFT JOIN rn1x_outcomes o ON o.position_id = p.position_id
      LEFT JOIN (
            SELECT ord.position_id,
                   sum(fl.fee_usd) AS fees,
                   sum(fl.qty)     AS filled
              FROM rn1x_orders ord
              JOIN rn1x_fills  fl ON fl.order_id = ord.order_id
             GROUP BY ord.position_id) f
             ON f.position_id = p.position_id
      LEFT JOIN LATERAL (
            SELECT ev.id AS valuation_id, ev.us_market_slug, ev.buy_intent,
                   ev.ladder_side, ev.payout_event
              FROM external_valuations ev
             WHERE ev.experiment_id = p.experiment_id
               AND ev.condition_id = p.condition_id
               AND ev.admissible = TRUE
               AND ev.us_market_slug IS NOT NULL
             ORDER BY ev.decided_at DESC
             LIMIT 1) own ON TRUE
      LEFT JOIN LATERAL (
            SELECT count(DISTINCT ev.us_market_slug
                                  || '|' || COALESCE(ev.buy_intent, '')
                                  || '|' || COALESCE(ev.ladder_side, ''))
                       AS identities,
                   min(ev.us_market_slug) AS us_market_slug,
                   min(ev.buy_intent)     AS buy_intent,
                   min(ev.ladder_side)    AS ladder_side,
                   min(ev.payout_event)   AS payout_event,
                   max(ev.id)             AS valuation_id
              FROM external_valuations ev
             WHERE ev.condition_id = p.condition_id
               AND ev.us_market_slug IS NOT NULL) any_ ON TRUE
     WHERE p.experiment_id = ANY($1::text[])
       AND p.policy = ANY($2::text[])
       AND o.position_id IS NULL
     ORDER BY p.decision_ts
     LIMIT $3
"""

#: EXACTLY ONCE. The primary key is the position, and DO NOTHING means a
#: concurrent or restarted writer cannot produce a second settlement or
#: overwrite the first. `residual_qty` is written as 0 because settlement
#: closed the inventory.
WRITE_OUTCOME_SQL = """
    INSERT INTO rn1x_outcomes (position_id, settled_at, payout_per_leg,
        realized_cash_usd, fees_usd, residual_qty, residual_settled_usd,
        unpaired_qty, net_usd, outcome_basis)
    VALUES ($1, $2, $3::jsonb, $4, $5, 0, $6, 0, $7, $8)
    ON CONFLICT (position_id) DO NOTHING
    RETURNING position_id
"""


def plan(row, resolution) -> dict:
    """What to do with ONE open position, given the venue's answer.

    Pure, so the whole decision is testable without a database or a
    venue. Returns the status, and the accounting only when the venue
    actually settled the contract.
    """
    from .workers import ext_pinnacle_loop as loop

    out = {"position_id": row.get("position_id"),
           "us_market_slug": row.get("us_market_slug"),
           "venue_status": str((resolution or {}).get("status") or ""),
           "settlement_read": None, "side_map": None,
           "status": None, "accounting": None,
           "needed_fresh_odds": False,
           "why": None}
    if not row.get("us_market_slug"):
        out["status"] = S_NO_SLUG
        out["why"] = ("no admissible valuation recorded a venue contract "
                      "for this exposure, so there is no market to ask")
        return out
    out["identity_is_the_own_decision"] = bool(
        row.get("identity_is_the_own_decision"))
    if (not row.get("identity_is_the_own_decision")
            and int(row.get("candidate_identities") or 0) > 1):
        # AMBIGUOUS. More than one venue contract or side is recorded for
        # this condition and none of them is this position's own decision,
        # so which side it holds is not established. Picking one would be
        # a coin flip that writes an irreversible outcome.
        out["status"] = S_NO_SIDE
        out["why"] = ("%d different venue contract identities are recorded "
                      "for this condition and none of them is this "
                      "position's own admitted decision, so the side it "
                      "holds is not established"
                      % int(row["candidate_identities"]))
        return out
    if not row.get("filled_qty"):
        out["status"] = S_LEDGER_INCOMPLETE
        out["why"] = ("the position has no fills, so there is no quantity "
                      "to settle. Settling zero would write an outcome "
                      "for inventory that was never acquired")
        return out

    got = loop.outcome_from_settlement(resolution,
                                       buy_intent=row.get("buy_intent"),
                                       ladder_side=row.get("ladder_side"))
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

    if got.get("outcome") is not None:
        out["status"] = S_SETTLED
        out["accounting"] = accounting(
            filled_qty=row["filled_qty"],
            cost_basis_usd=row["cost_basis_usd"],
            fees_usd=row["fees_usd"], outcome=got["outcome"])
        out["basis"] = BASIS_SETTLED
        out["why"] = ("the venue settled its long side at %s; this "
                      "position is %s, so the event it pays on %s"
                      % (out["settlement_read"], out["side_map"],
                         "occurred" if got["outcome"] == 1
                         else "did not occur"))
        return out

    if got.get("class") == loop.B_CONFIRMED_VOID:
        out["status"] = S_VOID
        out["accounting"] = accounting(
            filled_qty=row["filled_qty"],
            cost_basis_usd=row["cost_basis_usd"],
            fees_usd=row["fees_usd"], outcome=None, void=True)
        out["basis"] = BASIS_VOID
        out["why"] = ("the venue's own settlement endpoint returned %s, "
                      "which paid neither side" % out["settlement_read"])
        return out

    # NOTHING AUTHORITATIVE. Named by which kind of nothing it was.
    st = out["venue_status"]
    if st in ("PENDING",):
        out["status"] = S_PENDING
        out["why"] = ("the venue lists the contract and has published no "
                      "settlement for it. The fixture may be over; this "
                      "venue has not settled it")
    elif st in ("UNMATCHED", "UNREADABLE", ""):
        out["status"] = S_UNREADABLE
        out["why"] = ("the venue's resolution could not be read (%s%s). "
                      "That is a read failure, not a pending settlement"
                      % (st or "NO_STATUS",
                         (": " + str(resolution.get("error")))
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
    strings: the entry lane is one policy, and the acceptance position is
    another under a different experiment, and both are settled by reading
    the same venue endpoint. NOTHING IS RESEEDED and no provenance is
    rewritten -- a settlement is an outcome row beside the position, and
    the position's `provenance` column is never touched.

    `read_resolution(slug) -> dict` is the ONE transport boundary. It
    defaults to the loop's paced venue reader; a caller supplies a fixture
    to exercise everything else, which is all of it.

    Never raises. Reports one entry per position with the exact state.
    """
    import asyncio
    import json as _json

    from .workers import ext_pinnacle_loop as loop

    reader = read_resolution
    out = {"ran": True, "version": VERSION, "examined": 0,
           "settled": 0, "void": 0, "already": 0, "unresolved": 0,
           "errors": 0, "by_status": {}, "results": [],
           "requires_fresh_odds": False}
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
        slug = row.get("us_market_slug")
        try:
            if reader is not None:
                res = reader(slug)
            else:
                res = await asyncio.to_thread(
                    loop._read_resolution_blocking, slug)
        except Exception as exc:                               # noqa: BLE001
            out["errors"] += 1
            out["results"].append({"position_id": row["position_id"],
                                   "status": S_UNREADABLE,
                                   "error": type(exc).__name__})
            continue
        got = plan(row, res)
        if got["accounting"] is None:
            out["unresolved"] += 1
            out["by_status"][got["status"]] = \
                out["by_status"].get(got["status"], 0) + 1
            out["results"].append(got)
            continue
        acct = got["accounting"]
        payload = dict(acct, basis=got["basis"], version=VERSION,
                       side_map=got["side_map"],
                       settlement_read=got["settlement_read"],
                       payout_event=row.get("payout_event"),
                       us_market_slug=slug,
                       valuation_id=row.get("valuation_id"))
        try:
            wrote = await conn.fetchval(
                WRITE_OUTCOME_SQL, row["position_id"], _settled_at(res, now),
                _json.dumps(payload, default=str),
                acct["realized_cash_usd"], acct["fees_usd"],
                acct["residual_settled_usd"], acct["net_usd"],
                got["basis"])
        except Exception as exc:                               # noqa: BLE001
            out["errors"] += 1
            got = dict(got, status="WRITE_FAILED",
                       error=type(exc).__name__)
            out["results"].append(got)
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
        "reads": "/v1/markets/{slug}/settlement via bettor_live_read",
        "mapping": ("ext_pinnacle_loop.outcome_from_settlement -- the same "
                    "venue-side identity the calibration join uses"),
        "requires_fresh_bookmaker_odds": False,
        "why_not": ("a finished contract's value is the venue's settlement "
                    "price. A bookmaker probability is irrelevant to it, "
                    "and requiring one to settle a finished market is what "
                    "left a settled fixture open"),
        "writes": "rn1x_outcomes, exactly once per position",
        "releases_exposure_by": (
            "the outcome row itself -- exposure_from_rows excludes any "
            "position whose realized_net_usd is non-NULL"),
        "submits_orders": False,
        "statuses": [S_SETTLED, S_VOID, S_ALREADY, S_PENDING, S_NO_SLUG,
                     S_NO_SIDE, S_UNREADABLE, S_NOT_AUTHORITATIVE,
                     S_LEDGER_INCOMPLETE],
    }

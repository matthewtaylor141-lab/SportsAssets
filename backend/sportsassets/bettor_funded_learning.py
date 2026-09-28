"""LEARNING FROM THE FUNDED LANE'S OWN RESULTS, and only what is learnable.

WHAT WAS ABSENT. The RN1X lane records a prospective valuation and later joins the
venue's own settlement to it, so its record is scorable -- `rn1x_model_loop` writes
through `bettor_model_inventory.record_prediction` and `ext_pinnacle_loop` joins
outcomes back. The FUNDED lane had neither half: it decided, acted, and the
decision was gone. The realised cash was there all along in
`bettor_funded_economics` (signed ENTRY_COST, EXIT_PROCEEDS, FEE, SETTLEMENT rows)
with nothing to compare it against.

── THE HONEST BOUNDARY, WHICH IS THE POINT OF THIS MODULE ────────────

THE RANKING CANNOT BE VALIDATED FROM ITS OWN CHOICES. If the system chose HOLD,
what EXIT would have returned is NEVER OBSERVED: not at the price that was quoted,
because a sale moves against itself through depth, and not at that moment, because
the moment has passed. Scoring a realised HOLD against a decision-time EXIT price
would be treating a counterfactual as a result -- and it would flatter whichever
action the system already prefers, because the alternative never pays a spread.

So the alternatives are STORED FOR THE RECORD AND NOT SCORED, and this module
refuses to produce a "which action was right" statistic. Saying that plainly is
more useful than a number that cannot mean what it appears to.

WHAT IS FALSIFIABLE IS THE WORST CASE. It is not a forecast. It is the payout in
the worst outcome the fixture admits, minus what was paid including fees, from the
venue's own settlement terms -- a claimed LOWER BOUND on the realised net. So:

    realised_net < worst_case  =>  THE MODEL OF THE WORLD WAS WRONG

and it can only have been wrong in a small number of ways: the outcome space (a
draw nobody covered), the fee (mis-estimated or a schedule change), the described
structure (a leg that did not pay where we said it would), or an unhedged
remainder (a hedge that only partly filled). A violation does not say which; it
says one of them happened, with the numbers attached, which is where to look.

That is a real test with no probability in it, on a sample of one, and it is what
this module exists to make possible.

── AND IT REFUSES TO CLAIM SKILL ─────────────────────────────────────
`score()` reports counts, the realised total, and every worst-case violation. It
does NOT report a win rate, an expected value or a projected return, and it says
so in its own output. The standing rule is that historical results, simulated
profits and passing tests do not establish a forward outcome; a module that
summed its own realised net into "the strategy makes X" would be the exact
overstatement that rule exists to prevent.

NOTHING HERE PLACES, SIZES OR FUNDS AN ORDER, and nothing here decides anything.
It records what was decided and reads back what the book says happened.
"""

from __future__ import annotations

from typing import Any

VERSION = "FUNDED_LEARNING_V1"

#: The one basis this module will write for a realised outcome. A realised net
#: taken from a price snapshot or a model would be unfalsifiable in the one
#: direction that matters, so the source is named in the row.
BASIS_FUNDED_ECONOMICS = "SUM_OF_THE_FUNDED_ECONOMICS_LEDGER"

ACTIONS = ("HOLD", "ACQUIRE_HEDGE", "REDUCE", "EXIT",
           "NO_ACTION_WAS_RANKABLE")

R_SCHEMA_UNAVAILABLE = "THE_DECISION_LEDGER_IS_NOT_IN_THIS_DATABASE"
R_NO_SUCH_DECISION = "NO_SUCH_DECISION"
R_ACTION_NOT_RECOGNISED = "THAT_IS_NOT_AN_ACTION_THIS_LANE_TAKES"
R_ALREADY_REALISED = "THAT_DECISION_ALREADY_HAS_A_REALISED_OUTCOME"
R_POSITION_IS_STILL_OPEN = "THE_POSITION_HAS_NOT_FINISHED"
R_NO_ECONOMICS_YET = "THE_BOOK_HAS_NO_ECONOMICS_FOR_THIS_POSITION"

#: HOW A WORST-CASE CLAIM CAN BE WRONG. Named so a violation points somewhere
#: rather than just reporting a number.
VIOLATION_CAUSES = (
    "THE_OUTCOME_SPACE_WAS_WRONG",       # e.g. a draw neither leg covered
    "THE_FEE_WAS_MIS_ESTIMATED",
    "A_LEG_DID_NOT_PAY_WHERE_WE_SAID",
    "THE_HEDGE_ONLY_PARTLY_FILLED",
)


class SchemaUnavailable(RuntimeError):
    """The decision ledger is not in this database, or cannot be read."""


def describe() -> dict:
    return {
        "version": VERSION,
        "records": "the funded decision BEFORE its outcome exists",
        "realised_from": BASIS_FUNDED_ECONOMICS,
        "falsifiable_claim": (
            "the worst case is a LOWER BOUND on the realised net, from the "
            "venue's settlement terms. A realised net below it means the model "
            "of the world was wrong"),
        "possible_causes_of_a_violation": list(VIOLATION_CAUSES),
        "not_scorable": (
            "the alternatives. If HOLD was chosen, what EXIT would have "
            "returned is never observed -- not at the quoted price, because a "
            "sale moves against itself through depth, and not at that moment, "
            "because it passed. They are stored and not scored"),
        "does_not_report": ["win_rate", "expected_value", "projected_return"],
        "why_not": (
            "historical results, simulated profits and passing tests do not "
            "establish a forward outcome. Summing a realised net into 'the "
            "strategy makes X' is the overstatement that rule prevents"),
        "this_module_sends_nothing": True,
    }


async def _has_schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables "
            " WHERE table_schema='public' "
            "   AND table_name='bettor_funded_decisions'"))
    except Exception as exc:                                # noqa: BLE001
        raise SchemaUnavailable(
            "the catalogue could not be read (%s), so whether the decision "
            "ledger exists is UNKNOWN" % type(exc).__name__) from exc


def _row(r) -> dict | None:
    """Rows out of this ledger, in the types a caller expects.

    THE JSONB COLUMNS ARE DECODED HERE. asyncpg hands `jsonb` back as TEXT
    unless a codec is registered on the connection, so `decision["ranked"]` was
    a string and `len()` of it was a character count. A caller iterating it would
    have got characters, and a test asserting `len(...) == 2` caught it only
    because it checked the shape rather than the truthiness. Decoding at the one
    place rows leave this module keeps every caller out of that.
    """
    if r is None:
        return None
    import json
    out = dict(r)
    for k in ("worst_case_usd", "realised_net_usd"):
        if out.get(k) is not None:
            out[k] = float(out[k])
    for k in ("ranked", "unrankable"):
        v = out.get(k)
        if isinstance(v, (str, bytes)):
            try:
                out[k] = json.loads(v)
            except (ValueError, TypeError):
                # LEFT AS IT CAME rather than replaced with an empty list: a
                # value we cannot parse is not an absence of alternatives, and
                # silently emptying it would hide a corrupt row.
                out[k] = {"UNPARSEABLE_JSON": (v if isinstance(v, str)
                                               else v.decode("utf-8", "replace"))}
    return out


async def record_decision(conn, *, decision_id: str, account_id: str,
                          venue: str, fixture: str, action: str,
                          decided_at, group_id: str | None = None,
                          worst_case_usd=None, ranking: dict | None = None,
                          inputs_present=(), inputs_missing=()) -> dict:
    """WRITE THE DECISION BEFORE ITS OUTCOME EXISTS.

    IDEMPOTENT ON `decision_id`, which the caller chooses -- so a cycle that
    re-evaluates the same group reaches the same row rather than recording one
    decision twice. The same reasoning as the reservation machine's
    `operation_id`.

    `worst_case_usd` MAY BE None, and that is a real answer: the valuation
    withholds its verdict when fees are not priced, and "no bound was claimed" is
    not a bound of zero. A NULL is never scored as one.
    """
    import json
    out: dict[str, Any] = {"version": VERSION, "decision_id": str(decision_id)}
    if action not in ACTIONS:
        return dict(out, ok=False, refusal=R_ACTION_NOT_RECOGNISED,
                    recognised=list(ACTIONS))
    try:
        if not await _has_schema(conn):
            return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE)
    except SchemaUnavailable as exc:
        return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE, why=str(exc))

    ranked = (ranking or {}).get("ranked") or []
    unrankable = (ranking or {}).get("unrankable") or []
    row_id = await conn.fetchval(
        "INSERT INTO bettor_funded_decisions "
        "(decision_id, account_id, venue, fixture, group_id, action, "
        " worst_case_usd, ranked, unrankable, inputs_present, inputs_missing, "
        " decided_at) VALUES "
        "($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11,to_timestamp($12)) "
        "ON CONFLICT (decision_id) DO NOTHING RETURNING decision_id",
        str(decision_id), str(account_id), str(venue), str(fixture),
        (None if group_id is None else str(group_id)), str(action),
        (None if worst_case_usd is None else float(worst_case_usd)),
        json.dumps(ranked, default=str), json.dumps(unrankable, default=str),
        [str(x) for x in inputs_present], [str(x) for x in inputs_missing],
        float(decided_at))
    existing = _row(await conn.fetchrow(
        "SELECT * FROM bettor_funded_decisions WHERE decision_id=$1",
        str(decision_id)))
    if row_id is None:
        # ALREADY RECORDED. Not an error and not a write -- the distinction the
        # RN1X ledger had to learn when a cycle reported 293 writes against 159
        # rows, because it was counting validated attempts.
        return dict(out, ok=True, written=False, already=True,
                    decision=existing,
                    why="this decision is already recorded; the ledger keeps "
                        "the first")
    return dict(out, ok=True, written=True, already=False, decision=existing,
                recorded_before_the_outcome=True)


async def realised_from_the_book(conn, *, group_id: str) -> dict:
    """WHAT ACTUALLY HAPPENED, SUMMED FROM THE FUNDED ECONOMICS LEDGER ONLY.

    `amount_usd` there is a SIGNED cash flow -- `ENTRY_COST` is written negative,
    `EXIT_PROCEEDS` and `SETTLEMENT` positive, `FEE` negative -- so the realised
    net is a plain sum and needs no sign convention of its own. That is checked
    against the writers rather than assumed: `bettor_funded_book` records
    `amount_usd=(cash if direction == "EXIT" else -cash)` and
    `amount_usd=-booked_fee_usd`.

    EXIT CHILDREN ARE INCLUDED. An exit is its own intent and carries no group of
    its own, so the rows are gathered by the group OR by a parent in the group --
    the same join the closure predicate uses. Summing only the group's own legs
    would omit every sale's proceeds and every sale's fee, which is most of the
    result.

    IT REFUSES WHILE THE POSITION IS STILL OPEN. A partial result compared
    against a worst case that describes the whole structure would report a
    violation that is only incompleteness.
    """
    out: dict[str, Any] = {"version": VERSION, "group_id": str(group_id),
                           "basis": BASIS_FUNDED_ECONOMICS}
    still_open = await conn.fetchval(
        "SELECT count(*) FROM bettor_funded_intents i "
        " WHERE i.portfolio_group_id = $1 AND i.kind='ENTRY' "
        "   AND bettor_funded_position_is_open(i.state, i.residual_qty, "
        "                                      i.closed_at)", str(group_id))
    if still_open:
        return dict(out, ok=False, refusal=R_POSITION_IS_STILL_OPEN,
                    open_legs=int(still_open),
                    why=("a partial result compared against a worst case that "
                         "describes the whole structure would report a "
                         "violation that is only incompleteness"))
    rows = await conn.fetch(
        "SELECT e.kind, sum(e.amount_usd)::float8 AS total, count(*) AS n "
        "  FROM bettor_funded_economics e "
        "  JOIN bettor_funded_intents i ON i.intent_id = e.intent_id "
        "  LEFT JOIN bettor_funded_intents p "
        "         ON p.intent_id = i.parent_intent_id "
        " WHERE i.portfolio_group_id = $1 OR p.portfolio_group_id = $1 "
        " GROUP BY e.kind ORDER BY e.kind", str(group_id))
    if not rows:
        return dict(out, ok=False, refusal=R_NO_ECONOMICS_YET,
                    why=("the book has recorded no economic events for this "
                         "position, so there is no realised net to read"))
    by_kind = {r["kind"]: {"usd": round(float(r["total"]), 6),
                           "events": int(r["n"])} for r in rows}
    provisional = await conn.fetchval(
        "SELECT count(*) FROM bettor_funded_economics e "
        "  JOIN bettor_funded_intents i ON i.intent_id = e.intent_id "
        "  LEFT JOIN bettor_funded_intents p "
        "         ON p.intent_id = i.parent_intent_id "
        " WHERE (i.portfolio_group_id = $1 OR p.portfolio_group_id = $1) "
        "   AND e.provisional", str(group_id))
    net = round(sum(v["usd"] for v in by_kind.values()), 6)
    return dict(out, ok=True, refusal=None, realised_net_usd=net,
                by_kind=by_kind,
                provisional_events=int(provisional or 0),
                # A PROVISIONAL FEE IS STILL AN ESTIMATE. The net is reported
                # either way, and the count says how much of it is not final --
                # because a violation caused by a provisional fee is a different
                # finding from one caused by the outcome space.
                some_of_this_is_not_final=bool(provisional),
                buckets_are_not_blended=("the kinds are reported separately as "
                                        "well as summed; a total hides a large "
                                        "positive against a larger negative"))


async def join_realised(conn, *, decision_id: str) -> dict:
    """ATTACH WHAT HAPPENED, once, and only from the book.

    Never rewrites an existing outcome -- the database refuses that too, because
    a restated result makes the worst-case claim unfalsifiable after the fact.
    """
    out: dict[str, Any] = {"version": VERSION, "decision_id": str(decision_id)}
    try:
        if not await _has_schema(conn):
            return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE)
    except SchemaUnavailable as exc:
        return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE, why=str(exc))
    row = _row(await conn.fetchrow(
        "SELECT * FROM bettor_funded_decisions WHERE decision_id=$1",
        str(decision_id)))
    if row is None:
        return dict(out, ok=False, refusal=R_NO_SUCH_DECISION)
    if row["realised_known"]:
        return dict(out, ok=True, already=True, decision=row,
                    why="this decision already has a realised outcome")
    if not row["group_id"]:
        return dict(out, ok=False, refusal=R_POSITION_IS_STILL_OPEN,
                    why="this decision names no group, so no position can be "
                        "read back for it")
    got = await realised_from_the_book(conn, group_id=row["group_id"])
    if not got.get("ok"):
        return dict(out, ok=False, refusal=got["refusal"], why=got.get("why"),
                    realised=got)
    await conn.execute(
        "UPDATE bettor_funded_decisions "
        "   SET realised_known = TRUE, realised_net_usd = $2, "
        "       realised_at = now(), realised_basis = $3 "
        " WHERE decision_id = $1 AND realised_known = FALSE",
        str(decision_id), float(got["realised_net_usd"]),
        BASIS_FUNDED_ECONOMICS)
    after = _row(await conn.fetchrow(
        "SELECT * FROM bettor_funded_decisions WHERE decision_id=$1",
        str(decision_id)))
    return dict(out, ok=True, already=False, decision=after, realised=got,
                **check_the_worst_case(after))


def check_the_worst_case(decision: dict) -> dict:
    """THE ONE FALSIFIABLE TEST, as pure arithmetic on a recorded row.

    The worst case was a claimed LOWER BOUND on the realised net, derived from
    the venue's settlement terms and what was paid. If the realised net is below
    it, the model of the world was wrong. Pure, so it can be applied to a stored
    row without a database and checked in a test without one.
    """
    wc = decision.get("worst_case_usd")
    net = decision.get("realised_net_usd")
    if wc is None:
        return {"worst_case_check": "NO_BOUND_WAS_CLAIMED",
                "why": ("the valuation withheld its verdict -- commonly because "
                        "fees were not priced -- so there is nothing to "
                        "falsify. This is NOT a bound of zero")}
    if net is None:
        return {"worst_case_check": "NO_REALISED_NET_YET"}
    # A CENT OF TOLERANCE, for the rounding that a chain of numeric sums leaves.
    # Not more: the margins this structure trades on are tens of cents, so a
    # generous tolerance would absorb the very error being looked for.
    violated = net < (wc - 0.01)
    res = {"worst_case_check": ("VIOLATED" if violated else "HELD"),
           "worst_case_usd": wc, "realised_net_usd": net,
           "shortfall_usd": round(wc - net, 6) if violated else 0.0}
    if violated:
        res["the_model_of_the_world_was_wrong"] = True
        res["possible_causes"] = list(VIOLATION_CAUSES)
        res["why"] = (
            "the worst case is a lower bound from the venue's settlement terms, "
            "so a realised net below it is not bad luck -- it means one of the "
            "listed causes applied. Which one is worth finding out")
    return res


async def unjoined(conn, *, limit: int = 100) -> list[dict]:
    """DECISIONS WHOSE OUTCOME IS NOT YET KNOWN. What a scoring pass iterates,
    and what a restart picks up."""
    return [_row(r) for r in await conn.fetch(
        "SELECT * FROM bettor_funded_decisions WHERE NOT realised_known "
        " ORDER BY decided_at LIMIT $1", int(limit))]


async def score(conn, *, account_id: str | None = None) -> dict:
    """WHAT THE RECORD SUPPORTS, AND WHAT IT DOES NOT.

    Reports counts, the realised total and every worst-case violation. It does
    NOT report a win rate, an expected value or a projected return, and it names
    those omissions rather than leaving a reader to assume the numbers were
    simply not computed yet.
    """
    out: dict[str, Any] = {"version": VERSION, "account_id": account_id}
    try:
        if not await _has_schema(conn):
            return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE)
    except SchemaUnavailable as exc:
        return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE, why=str(exc))
    sql = "SELECT * FROM bettor_funded_decisions"
    args: list = []
    if account_id is not None:
        args.append(str(account_id))
        sql += " WHERE account_id = $1"
    rows = [_row(r) for r in await conn.fetch(sql + " ORDER BY decided_at",
                                             *args)]
    realised = [r for r in rows if r["realised_known"]]
    checks = [dict(r, **check_the_worst_case(r)) for r in realised]
    violations = [c for c in checks if c["worst_case_check"] == "VIOLATED"]
    bounded = [c for c in checks if c["worst_case_check"] in ("HELD",
                                                             "VIOLATED")]
    by_action: dict[str, int] = {}
    for r in rows:
        by_action[r["action"]] = by_action.get(r["action"], 0) + 1
    return dict(
        out, ok=True, refusal=None,
        decisions_recorded=len(rows),
        outcomes_known=len(realised),
        outcomes_pending=len(rows) - len(realised),
        decisions_by_action=by_action,
        realised_net_total_usd=round(
            sum(r["realised_net_usd"] for r in realised), 6),
        bounds_claimed=len(bounded),
        bounds_held=len(bounded) - len(violations),
        bounds_violated=len(violations),
        violations=[{"decision_id": c["decision_id"], "fixture": c["fixture"],
                     "action": c["action"],
                     "worst_case_usd": c["worst_case_usd"],
                     "realised_net_usd": c["realised_net_usd"],
                     "shortfall_usd": c["shortfall_usd"]}
                    for c in violations],
        no_bound_was_claimed=len([c for c in checks
                                  if c["worst_case_check"]
                                  == "NO_BOUND_WAS_CLAIMED"]),
        # ── WHAT THIS IS NOT ─────────────────────────────────────────
        not_reported=["win_rate", "expected_value", "projected_return",
                      "which_action_was_right"],
        why_which_action_was_right_is_absent=(
            "the alternatives were never realised. If HOLD was chosen, what "
            "EXIT would have returned is not observed -- a sale moves against "
            "itself through depth and the moment has passed -- so any such "
            "statistic would score a counterfactual as a result, and would "
            "flatter whichever action the system already prefers"),
        why_the_total_is_not_a_return=(
            "a realised total over a handful of positions is a record of what "
            "happened, not a rate. Historical results, simulated profits and "
            "passing tests do not establish a forward outcome"),
        what_the_violations_mean=(
            "a worst case is a lower bound from the venue's settlement terms. "
            "Every violation is a place the model of the world was wrong, and "
            "those are the rows worth reading"),
    )

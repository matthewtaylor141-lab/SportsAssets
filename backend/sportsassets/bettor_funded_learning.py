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

#: A BOUND IS CONDITIONAL ON THE POSITION THAT ESTABLISHED IT.
#: `HOLD_TO_SETTLEMENT` means the floor assumed the position would be held to
#: settlement; `MAY_EXIT_EARLY` means it did not. A floor computed under the first
#: says nothing about a position sold on cycle three, and comparing them reports a
#: violated model when nothing about the model was wrong.
POLICY_HOLD_TO_SETTLEMENT = "HOLD_TO_SETTLEMENT"
POLICY_MAY_EXIT_EARLY = "MAY_EXIT_EARLY"
HOLDING_POLICIES = (POLICY_HOLD_TO_SETTLEMENT, POLICY_MAY_EXIT_EARLY)

CHECK_HELD = "HELD"
CHECK_VIOLATED = "VIOLATED"
CHECK_NO_BOUND = "NO_BOUND_WAS_CLAIMED"
CHECK_NO_RESULT = "NO_REALISED_NET_YET"
CHECK_INVALIDATED = "INVALIDATED_BY_A_LATER_DIVERGENCE"
CHECK_NOT_FINAL = "WITHHELD_UNTIL_THE_RESULT_IS_FINAL"
#: THE GROUP'S LEGS COULD NOT BE READ, so the bound's scope is unestablished and
#: nothing is scored against it. Distinct from CHECK_HELD on purpose: a check
#: that quietly passed because its input was missing is the worst of the five.
CHECK_SCOPE_UNMEASURED = "SCOPE_COULD_NOT_BE_MEASURED"

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
                          inputs_present=(), inputs_missing=(),
                          bound_action: str | None = None,
                          bound_filled_qty=None,
                          bound_holding_policy: str | None = None,
                          model_key: str | None = None,
                          model_version: str | None = None,
                          features: dict | None = None,
                          feature_sha: str | None = None,
                          predicted: dict | None = None) -> dict:
    """WRITE THE DECISION BEFORE ITS OUTCOME EXISTS.

    IDEMPOTENT ON `decision_id`, which the caller chooses -- so a cycle that
    re-evaluates the same group reaches the same row rather than recording one
    decision twice. The same reasoning as the reservation machine's
    `operation_id`.

    `worst_case_usd` MAY BE None, and that is a real answer: the valuation
    withholds its verdict when fees are not priced, and "no bound was claimed" is
    not a bound of zero. A NULL is never scored as one.

    ── AND THE PREDICTION IT WAS MADE FROM, ON THIS SAME IMMUTABLE ROW ──

    `model_key`, `model_version`, `features`, `feature_sha` and `predicted` are
    what turn this ledger from a record of decisions into something a model can be
    evaluated against. They live HERE, on a row a trigger already forbids
    rewriting, because a prediction that can be edited once its outcome is known
    is not a prediction.

    They are all optional and all default to NULL. A decision taken without a
    model records none of them, and `bettor_funded_model.labelled` then excludes
    it from every evaluation -- a row with no recorded vector cannot be scored,
    and scoring it against whatever the current model would say today would be
    scoring the model on its own present output.
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
    has_scope = await conn.fetchval(
        "SELECT count(*) FROM information_schema.columns "
        " WHERE table_name='bettor_funded_decisions' "
        "   AND column_name='bound_action'")
    cols = ("decision_id, account_id, venue, fixture, group_id, action, "
            "worst_case_usd, ranked, unrankable, inputs_present, "
            "inputs_missing, decided_at")
    vals = ("$1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11,"
            "to_timestamp($12)")
    args = [str(decision_id), str(account_id), str(venue), str(fixture),
            (None if group_id is None else str(group_id)), str(action),
            (None if worst_case_usd is None else float(worst_case_usd)),
            json.dumps(ranked, default=str),
            json.dumps(unrankable, default=str),
            [str(x) for x in inputs_present],
            [str(x) for x in inputs_missing], float(decided_at)]
    if has_scope:
        # ── THE SCOPE THE BOUND IS CONDITIONAL ON ────────────────────
        # Defaulted from the decision itself when the caller does not state it:
        # the action taken IS the action the bound was computed for, unless the
        # caller says otherwise. `bound_holding_policy` has no safe default and
        # is left NULL, which makes the holding-policy divergence check inactive
        # rather than silently assuming the position would be held.
        cols += ", bound_action, bound_filled_qty, bound_holding_policy"
        vals += ",$13,$14,$15"
        args += [str(bound_action or action),
                 None if bound_filled_qty is None else float(bound_filled_qty),
                 None if bound_holding_policy is None
                 else str(bound_holding_policy)]
    has_model = await conn.fetchval(
        "SELECT count(*) FROM information_schema.columns "
        " WHERE table_name='bettor_funded_decisions' "
        "   AND column_name='model_version'")
    if has_model:
        n = len(args)
        cols += ", model_key, model_version, features, feature_sha, predicted"
        vals += ",$%d,$%d,$%d::jsonb,$%d,$%d::jsonb" % (
            n + 1, n + 2, n + 3, n + 4, n + 5)
        args += [
            (None if model_key is None else str(model_key)),
            (None if model_version is None else str(model_version)),
            (None if features is None else json.dumps(features, default=str)),
            (None if feature_sha is None else str(feature_sha)),
            (None if predicted is None else json.dumps(predicted, default=str))]
    row_id = await conn.fetchval(
        "INSERT INTO bettor_funded_decisions (" + cols + ") VALUES (" + vals
        + ") ON CONFLICT (decision_id) DO NOTHING RETURNING decision_id", *args)
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


async def join_realised(conn, *, decision_id: str,
                        correction_reason: str | None = None) -> dict:
    """ATTACH WHAT HAPPENED AS A VERSIONED OUTCOME, and record the GROUP's result
    exactly once.

    ── TWO DEFECTS THIS CLOSES ──────────────────────────────────────

    1 THE SAME RESULT WAS COUNTED ONCE PER DECISION. This attached the whole
      group's realised net to a decision and `score` summed every decision, so a
      position evaluated on five cycles contributed its result five times. The
      more the system thought about a position, the more it appeared to make. The
      group's result is now written to `bettor_funded_group_results` -- one row per
      group -- and portfolio P&L sums THAT.

    2 A PROVISIONAL FEE WAS FINAL THE MOMENT IT WAS READ. The first outcome could
      never be restated, so the correction that arrives when the fee settles had
      nowhere to go. Outcomes are now APPEND-ONLY VERSIONS: a later read that
      differs is recorded as version n+1 with what it supersedes and why. The
      DECISION stays immutable -- rewriting what was expected would make its claim
      unfalsifiable -- but a measurement that cannot be corrected is not a
      measurement.
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
    if not row["group_id"]:
        return dict(out, ok=False, refusal=R_POSITION_IS_STILL_OPEN,
                    why="this decision names no group, so no position can be "
                        "read back for it")
    got = await realised_from_the_book(conn, group_id=row["group_id"])
    if not got.get("ok"):
        return dict(out, ok=False, refusal=got["refusal"], why=got.get("why"),
                    realised=got)

    net = float(got["realised_net_usd"])
    prov = int(got.get("provisional_events") or 0)
    is_final = prov == 0
    versioned = await _has_outcome_versions(conn)

    if versioned:
        prev = _row(await conn.fetchrow(
            "SELECT * FROM bettor_funded_decision_outcomes "
            " WHERE decision_id=$1 ORDER BY version DESC LIMIT 1",
            str(decision_id)))
        if prev is not None:
            unchanged = (abs(float(prev["realised_net_usd"]) - net) < 1e-9
                         and bool(prev["is_final"]) == is_final)
            if unchanged:
                scored = dict(row, realised_net_usd=net,
                              outcome_is_final=is_final,
                              provisional_events=prov)
                # MERGED, NOT SPLATTED. `check_the_worst_case` also reports
                # `outcome_is_final`, so passing both to one dict() call raised
                # TypeError -- a duplicate keyword, caught by the tests.
                res = dict(out, ok=True, already=True,
                           outcome_version=int(prev["version"]),
                           decision=scored, realised=got,
                           why="this outcome is already recorded unchanged")
                res.update(check_the_worst_case(scored))
                return res
            if prev["is_final"] and not correction_reason:
                # A FINAL RESULT IS NOT SILENTLY RESTATED. It may be corrected,
                # but the correction has to say what it is correcting and why --
                # otherwise a version number is just a silent restatement wearing
                # a badge.
                return dict(out, ok=False, refusal=R_ALREADY_REALISED,
                            outcome_version=int(prev["version"]),
                            stored_net_usd=float(prev["realised_net_usd"]),
                            new_net_usd=net,
                            why=("version %d is FINAL. A correction is allowed "
                                 "but must pass `correction_reason` so the audit "
                                 "trail says what changed and why"
                                 % prev["version"]))
        nxt = 1 if prev is None else int(prev["version"]) + 1
        await conn.execute(
            "INSERT INTO bettor_funded_decision_outcomes "
            "(outcome_id, decision_id, version, realised_net_usd, "
            " realised_basis, is_final, provisional_events, supersedes_version, "
            " correction_reason, by_kind) VALUES "
            "($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)",
            "out:%s:%d" % (decision_id, nxt), str(decision_id), nxt, net,
            BASIS_FUNDED_ECONOMICS, is_final, prov,
            None if nxt == 1 else nxt - 1,
            None if nxt == 1 else (correction_reason
                                   or "the realised net changed"),
            _dumps(got.get("by_kind") or {}))
        # ── THE GROUP'S RESULT, ONCE ─────────────────────────────────
        await conn.execute(
            "INSERT INTO bettor_funded_group_results "
            "(group_id, realised_net_usd, realised_basis, is_final, "
            " provisional_events, by_kind, version) VALUES "
            "($1,$2,$3,$4,$5,$6::jsonb,1) "
            "ON CONFLICT (group_id) DO UPDATE SET "
            "  realised_net_usd = EXCLUDED.realised_net_usd, "
            "  realised_basis = EXCLUDED.realised_basis, "
            "  is_final = EXCLUDED.is_final, "
            "  provisional_events = EXCLUDED.provisional_events, "
            "  by_kind = EXCLUDED.by_kind, "
            "  version = bettor_funded_group_results.version + 1, "
            "  read_at = now()",
            str(row["group_id"]), net, BASIS_FUNDED_ECONOMICS, is_final, prov,
            _dumps(got.get("by_kind") or {}))
    else:
        nxt = 1

    # THE CACHED COPY ON THE DECISION, for readers that only want the latest. The
    # 132 trigger refuses to restate it, so it is written once and the versioned
    # table carries corrections.
    if not row["realised_known"]:
        await conn.execute(
            "UPDATE bettor_funded_decisions "
            "   SET realised_known = TRUE, realised_net_usd = $2, "
            "       realised_at = now(), realised_basis = $3 "
            " WHERE decision_id = $1 AND realised_known = FALSE",
            str(decision_id), net, BASIS_FUNDED_ECONOMICS)
    after = _row(await conn.fetchrow(
        "SELECT * FROM bettor_funded_decisions WHERE decision_id=$1",
        str(decision_id)))
    scored = dict(after or {}, realised_net_usd=net, outcome_is_final=is_final,
                  provisional_events=prov)
    res = dict(out, ok=True, already=False, outcome_version=nxt,
               decision=scored, realised=got,
               provisional_events=prov,
               group_result_recorded_once=bool(versioned))
    res.update(check_the_worst_case(scored))
    res.setdefault("outcome_is_final", is_final)
    return res


def _dumps(obj) -> str:
    import json as _j
    return _j.dumps(obj, default=str)


async def _has_outcome_versions(conn) -> bool:
    return bool(await conn.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        " WHERE table_schema='public' "
        "   AND table_name='bettor_funded_decision_outcomes'"))


async def observed_scope(conn, *, group_id: str) -> dict:
    """WHAT THE POSITION ACTUALLY TURNED OUT TO BE, read from the book.

    ── WHY THIS EXISTS, AND IT WAS A REAL GAP ───────────────────────

    `check_the_worst_case` takes an `observed` dict and, without one, CANNOT FIND
    A SINGLE DIVERGENCE: every scope comparison is guarded on the observed field
    being present. So `bound_action`, `bound_filled_qty` and
    `bound_holding_policy` were recorded on every decision and then never
    compared against anything, and `score` reported zero invalidations no matter
    what the position did. Recorded-but-unread is the same defect as a helper no
    consumer calls.

    Measured on the pair lifecycle: a floor claimed for TEN matched units against
    a position that acquired SIX was scored as HELD.

    ── WHAT `filled_qty` MEANS HERE, PRECISELY ──────────────────────

    THE MATCHED UNITS, which is `min(primary filled, hedge filled)` for a group
    that carries both roles -- because that is the quantity a two-leg structure's
    floor is computed over. A partially acquired hedge leaves the uncovered part
    of the primary leg naked, and its payoff is NOT the structure's. For a
    single-role group it is that role's filled quantity.

    `action` IS DELIBERATELY ABSENT. What the lane actually did is not recoverable
    from the book in general -- an exit and a settlement both leave a closed
    position -- so this reader does not guess one. A field it cannot establish is
    left out, and `check_the_worst_case` then makes no claim about it, rather than
    reporting a divergence or an agreement it has not measured.
    """
    out: dict[str, Any] = {"version": VERSION, "group_id": str(group_id)}
    rows = await conn.fetch(
        "SELECT i.leg_role, i.closed_reason, "
        "       coalesce(sum(f.qty) FILTER (WHERE f.direction='ENTRY'), 0)"
        "         ::float8 AS filled "
        "  FROM bettor_funded_intents i "
        "  LEFT JOIN bettor_funded_fills f ON f.intent_id = i.intent_id "
        " WHERE i.portfolio_group_id = $1 AND i.kind = 'ENTRY' "
        " GROUP BY i.intent_id, i.leg_role, i.closed_reason", str(group_id))
    if not rows:
        return dict(out, ok=False, refusal="NO_LEGS_IN_THAT_GROUP",
                    why=("without the group's legs there is nothing to compare "
                         "a bound's scope against, and no divergence is claimed"))
    by_role: dict[str, float] = {}
    for r in rows:
        role = str(r["leg_role"] or "PRIMARY")
        by_role[role] = round(by_role.get(role, 0.0) + float(r["filled"]), 6)
    #: AN EARLY EXIT IS THE VENUE-SIDE SALE, not a settlement and not a cancel.
    exited_early = any(str(r["closed_reason"] or "") == "EXITED_IN_THE_MARKET"
                       for r in rows)
    roles = [x for x in ("PRIMARY", "HEDGE") if x in by_role]
    matched = (min(by_role[x] for x in roles) if len(roles) > 1
               else (by_role[roles[0]] if roles else 0.0))
    return dict(out, ok=True, refusal=None,
                filled_qty=matched, exited_early=exited_early,
                filled_by_role=by_role, roles_present=roles,
                matched_units_rule=(
                    "min(primary filled, hedge filled) for a two-role group: "
                    "that is the quantity a two-leg structure's floor is "
                    "computed over, and the uncovered part of the primary leg "
                    "does not have the structure's payoff"),
                action_is_not_reported=(
                    "what the lane actually did is not recoverable from the "
                    "book -- an exit and a settlement both leave a closed "
                    "position -- so no action is claimed and no divergence or "
                    "agreement is asserted about it"))


def check_the_worst_case(decision: dict, *, observed=None,
                        require_final: bool = True) -> dict:
    """THE FALSIFIABLE TEST, AND THE THREE THINGS THAT MAKE IT MEANINGLESS.

    The worst case was a claimed LOWER BOUND on the realised net, from the venue's
    settlement terms. A realised net below it means the model of the world was
    wrong -- BUT ONLY IF THE POSITION IS STILL THE ONE THE BOUND WAS COMPUTED FOR.

    1 SCOPE. A floor is conditional on the ACTION taken, the QUANTITY actually
      filled and the HOLDING POLICY assumed. If the system later sold voluntarily,
      acquired only part of a hedge, or filled a different quantity, the realised
      net belongs to a DIFFERENT position. That is `INVALIDATED`, not `VIOLATED`:
      filing it as a modelling error would send someone to debug settlement
      arithmetic that was never used.

    2 FINALITY. A realised net containing PROVISIONAL fee events is an estimate.
      Scoring a bound against an estimate produces a violation that may evaporate
      when the fee settles, so by default the check is WITHHELD until the outcome
      is final. `require_final=False` is available for a diagnostic read and says
      so in its own output.

    3 A WITHHELD BOUND IS NOT A BOUND OF ZERO. Unchanged, and still first.
    """
    wc = decision.get("worst_case_usd")
    net = decision.get("realised_net_usd")
    if wc is None:
        return {"worst_case_check": CHECK_NO_BOUND,
                "why": ("the valuation withheld its verdict -- commonly because "
                        "fees were not priced -- so there is nothing to falsify. "
                        "This is NOT a bound of zero")}
    if net is None:
        return {"worst_case_check": CHECK_NO_RESULT}

    # ── SCOPE FIRST, because a mismatch makes the comparison meaningless ──
    obs = dict(observed or {})
    scope = {"bound_action": decision.get("bound_action"),
             "bound_filled_qty": decision.get("bound_filled_qty"),
             "bound_holding_policy": decision.get("bound_holding_policy")}
    divergences = []
    if scope["bound_action"] and obs.get("action") \
            and str(obs["action"]) != str(scope["bound_action"]):
        divergences.append({"field": "action",
                            "bound_to": scope["bound_action"],
                            "actually": obs["action"]})
    if scope["bound_filled_qty"] is not None \
            and obs.get("filled_qty") is not None \
            and abs(float(obs["filled_qty"])
                    - float(scope["bound_filled_qty"])) > 1e-9:
        divergences.append({"field": "filled_qty",
                            "bound_to": float(scope["bound_filled_qty"]),
                            "actually": float(obs["filled_qty"])})
    if scope["bound_holding_policy"] == POLICY_HOLD_TO_SETTLEMENT \
            and obs.get("exited_early"):
        divergences.append({
            "field": "holding_policy",
            "bound_to": POLICY_HOLD_TO_SETTLEMENT,
            "actually": "the position was sold before settlement"})
    if divergences:
        return {"worst_case_check": CHECK_INVALIDATED,
                "worst_case_usd": wc, "realised_net_usd": net,
                "bound_scope": scope, "divergences": divergences,
                "the_model_of_the_world_was_wrong": None,
                "why": ("the realised net belongs to a different position from "
                        "the one this bound was computed for, so the comparison "
                        "says nothing about the settlement model. This is NOT a "
                        "violation and must not be filed as one")}

    if require_final and decision.get("outcome_is_final") is False:
        return {"worst_case_check": CHECK_NOT_FINAL,
                "worst_case_usd": wc, "realised_net_usd": net,
                "provisional_events": decision.get("provisional_events"),
                "why": ("this realised net still contains provisional fee "
                        "events, so it is an estimate. A violation scored "
                        "against an estimate may evaporate when the fee settles")}

    # A CENT OF TOLERANCE, for the rounding a chain of numeric sums leaves. Not
    # more: the margins this structure trades on are tens of cents, so a generous
    # tolerance would absorb the very error being looked for.
    violated = net < (wc - 0.01)
    res = {"worst_case_check": (CHECK_VIOLATED if violated else CHECK_HELD),
           "worst_case_usd": wc, "realised_net_usd": net,
           "bound_scope": scope,
           "outcome_is_final": decision.get("outcome_is_final"),
           "shortfall_usd": round(wc - net, 6) if violated else 0.0}
    if violated:
        res["the_model_of_the_world_was_wrong"] = True
        res["possible_causes"] = list(VIOLATION_CAUSES)
        res["why"] = (
            "the worst case is a lower bound from the venue's settlement terms, "
            "so a realised net below it is not bad luck -- it means one of the "
            "listed causes applied, on the position this bound was actually "
            "computed for. Which one is worth finding out")
    return res


async def unjoined(conn, *, limit: int = 100,
                   account_id: str | None = None) -> list[dict]:
    """DECISIONS WHOSE OUTCOME IS NOT YET KNOWN. What a scoring pass iterates,
    and what a restart picks up.

    THE ACCOUNT IS FILTERED BEFORE THE LIMIT. Filtering the first `limit`
    rows afterwards let another account's older backlog take the whole page,
    so this account's decisions were never examined."""
    if account_id is None:
        return [_row(r) for r in await conn.fetch(
            "SELECT * FROM bettor_funded_decisions WHERE NOT realised_known "
            " ORDER BY decided_at LIMIT $1", int(limit))]
    return [_row(r) for r in await conn.fetch(
        "SELECT * FROM bettor_funded_decisions WHERE NOT realised_known "
        "   AND account_id = $2 ORDER BY decided_at LIMIT $1", int(limit),
        str(account_id))]


async def score(conn, *, account_id: str | None = None,
                require_final: bool = True) -> dict:
    """WHAT THE RECORD SUPPORTS, AND WHAT IT DOES NOT.

    ── DECISION EVALUATION AND PORTFOLIO P&L ARE DIFFERENT QUESTIONS ──
    "Did this decision's claimed floor hold?" is per DECISION, and needs the
    group's result attached to each one. "What did the account make?" is per
    GROUP, once. This used to sum `realised_net_usd` over decisions, so a position
    evaluated on five cycles contributed its result FIVE TIMES -- the more the
    system thought about a position, the more it appeared to make. The portfolio
    total now sums `bettor_funded_group_results`, one row per group, and the
    decision counts are reported separately so the two can never be confused
    again.

    It still does NOT report a win rate, an expected value, a projected return or
    which action was right, and it names those omissions.
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
    versioned = await _has_outcome_versions(conn)

    # ── THE LATEST OUTCOME VERSION PER DECISION ──────────────────────
    latest: dict = {}
    if versioned and rows:
        for r in await conn.fetch(
                "SELECT DISTINCT ON (decision_id) decision_id, version, "
                "       realised_net_usd::float8 AS net, is_final, "
                "       provisional_events "
                "  FROM bettor_funded_decision_outcomes "
                " WHERE decision_id = ANY($1::text[]) "
                " ORDER BY decision_id, version DESC",
                [r["decision_id"] for r in rows]):
            latest[r["decision_id"]] = dict(r)

    # ── THE OBSERVED SCOPE PER GROUP, READ ONCE ──────────────────────
    #
    # WITHOUT THIS THE SCOPE CHECK WAS DEAD CODE. Every comparison in
    # `check_the_worst_case` is guarded on the observed field being present, so a
    # call with no `observed` could never report a divergence -- and the three
    # `bound_*` columns were written on every decision and read by nothing.
    seen: dict = {}
    for r in rows:
        gid = r.get("group_id")
        if gid and gid not in seen:
            try:
                seen[gid] = await observed_scope(conn, group_id=gid)
            except Exception as exc:                            # noqa: BLE001
                # UNREADABLE IS NOT "NO DIVERGENCE". An absent observation makes
                # the scope check silent, which would report a bound as HELD on
                # a position nobody measured, so it is recorded as a read failure
                # and the decision's check is withheld below.
                seen[gid] = {"ok": False,
                             "refusal": "THE_GROUPS_LEGS_COULD_NOT_BE_READ",
                             "error": "%s: %s" % (type(exc).__name__,
                                                  str(exc)[:200])}

    checks = []
    for r in rows:
        lv = latest.get(r["decision_id"])
        scored = dict(r)
        if lv is not None:
            scored["realised_net_usd"] = float(lv["net"])
            scored["outcome_is_final"] = bool(lv["is_final"])
            scored["provisional_events"] = int(lv["provisional_events"])
            scored["outcome_version"] = int(lv["version"])
        elif r["realised_known"]:
            scored["outcome_is_final"] = None
        if scored.get("realised_net_usd") is None:
            continue
        obs = seen.get(r.get("group_id")) or {}
        if obs and not obs.get("ok"):
            # THE OBSERVATION FAILED, so the scope cannot be established and no
            # bound is scored against it. Reported as UNMEASURED_SCOPE rather
            # than silently becoming HELD.
            checks.append(dict(scored, worst_case_check=CHECK_SCOPE_UNMEASURED,
                               observed_scope=obs,
                               why=("the group's legs could not be read, so "
                                    "whether this bound still describes the "
                                    "position is unknown. An unread scope is "
                                    "not an unchanged one")))
            continue
        checks.append(dict(
            scored, observed_scope=obs,
            **check_the_worst_case(
                scored, require_final=require_final,
                observed=({k: obs[k] for k in ("filled_qty", "exited_early")}
                          if obs.get("ok") else None))))

    violations = [c for c in checks
                  if c["worst_case_check"] == CHECK_VIOLATED]
    held = [c for c in checks if c["worst_case_check"] == CHECK_HELD]
    invalidated = [c for c in checks
                   if c["worst_case_check"] == CHECK_INVALIDATED]
    withheld = [c for c in checks if c["worst_case_check"] == CHECK_NOT_FINAL]
    no_bound = [c for c in checks if c["worst_case_check"] == CHECK_NO_BOUND]

    by_action: dict[str, int] = {}
    for r in rows:
        by_action[r["action"]] = by_action.get(r["action"], 0) + 1

    # ── PORTFOLIO P&L, DEDUPLICATED BY GROUP ─────────────────────────
    groups = sorted({r["group_id"] for r in rows if r["group_id"]})
    pnl_rows = []
    if versioned and groups:
        pnl_rows = [dict(r) for r in await conn.fetch(
            "SELECT group_id, realised_net_usd::float8 AS net, is_final, "
            "       provisional_events, version "
            "  FROM bettor_funded_group_results "
            " WHERE group_id = ANY($1::text[]) ORDER BY group_id", groups)]
    portfolio = {
        "basis": "ONE ROW PER GROUP, from bettor_funded_group_results",
        "groups_with_a_result": len(pnl_rows),
        "groups_seen_in_the_ledger": len(groups),
        "realised_net_total_usd": round(sum(x["net"] for x in pnl_rows), 6),
        "all_final": all(bool(x["is_final"]) for x in pnl_rows) if pnl_rows
                     else None,
        "per_group": [{"group_id": x["group_id"], "net_usd": round(x["net"], 6),
                       "is_final": bool(x["is_final"]),
                       "version": int(x["version"])} for x in pnl_rows],
        "why_not_summed_over_decisions": (
            "a position evaluated on five cycles has five DECISIONS and one "
            "economic result. Summing the decisions multiplied the same money by "
            "five, and the error grew with how much attention the position got"),
    }
    if not versioned:
        portfolio["unavailable"] = (
            "migration 134 is not applied, so there is no per-group result table "
            "and no deduplicated total is reported. A decision-summed figure is "
            "NOT offered as a substitute")
        portfolio.pop("realised_net_total_usd", None)

    return dict(
        out, ok=True, refusal=None,
        decisions_recorded=len(rows),
        outcomes_known=len(checks),
        outcomes_pending=len(rows) - len(checks),
        decisions_by_action=by_action,
        portfolio_pnl=portfolio,
        bounds_claimed=len(held) + len(violations),
        bounds_held=len(held),
        bounds_violated=len(violations),
        bounds_invalidated_by_a_later_divergence=len(invalidated),
        bounds_withheld_until_final=len(withheld),
        bounds_with_an_unmeasured_scope=len(
            [c for c in checks
             if c["worst_case_check"] == CHECK_SCOPE_UNMEASURED]),
        no_bound_was_claimed=len(no_bound),
        violations=[{"decision_id": c["decision_id"], "fixture": c["fixture"],
                     "action": c["action"],
                     "worst_case_usd": c["worst_case_usd"],
                     "realised_net_usd": c["realised_net_usd"],
                     "shortfall_usd": c["shortfall_usd"]}
                    for c in violations],
        invalidated=[{"decision_id": c["decision_id"],
                      "divergences": c["divergences"]} for c in invalidated],
        not_reported=["win_rate", "expected_value", "projected_return",
                      "which_action_was_right"],
        why_which_action_was_right_is_absent=(
            "the alternatives were never realised. If HOLD was chosen, what EXIT "
            "would have returned is not observed -- a sale moves against itself "
            "through depth and the moment has passed -- so any such statistic "
            "would score a counterfactual as a result, and would flatter "
            "whichever action the system already prefers"),
        why_the_total_is_not_a_return=(
            "a realised total over a handful of positions is a record of what "
            "happened, not a rate. Historical results, simulated profits and "
            "passing tests do not establish a forward outcome"),
        what_an_invalidated_bound_means=(
            "the realised net belongs to a different position from the one the "
            "bound was computed for -- a later voluntary sale, a partial "
            "acquisition or a different filled quantity. It is NOT a modelling "
            "error and must not be filed as one"),
        what_the_violations_mean=(
            "a worst case is a lower bound from the venue's settlement terms. "
            "Every violation is a place the model of the world was wrong, on the "
            "position it was actually computed for"),
    )

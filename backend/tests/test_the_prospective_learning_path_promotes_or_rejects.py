"""THE PROSPECTIVE NON-FUNDED LEARNING PATH, END TO END, BOTH OUTCOMES.

INTEGRATED ENGINEERING EVIDENCE -- NOT REAL-MONEY VERIFICATION AND NOT A
PERFORMANCE CLAIM. Everything here runs on an isolated migrated database and
the fixtures are synthetic by construction; what it establishes is that the
pipeline works and that its bar cannot be passed by argument. It establishes
nothing whatever about whether a real model would clear that bar on real
fixtures.

WHY THIS COMES BEFORE THE HEDGE PROOFS. `discover` asks
`bettor_funded_model.predict_for` for region probabilities, and that refuses
with NO_APPROVED_MODEL_FOR_THAT_KEY while the registry is empty. So "a valid
indirect hedge beats HOLD" is not reachable until a model can be APPROVED, and
approval is not a flag -- `promote` enforces four conditions:

    * the model is a CANDIDATE,
    * it has a recorded evaluation whose own verdict is PROSPECTIVE,
    * that evaluation saw at least MIN_EVALUATION_ROWS (40),
    * and it beats the incumbent -- or, with none, its own declared baseline --
      on the promotion metric by MIN_SKILL_MARGIN.

THE REAL PATH, NOT AN INSERT. Inventory comes through `record_intent` ->
`record_acknowledgement` -> `ingest_fills`; decisions through
`bettor_funded_learning.record_decision`, which writes the feature vector and
its sha BEFORE any outcome exists; settlement through
`bettor_funded_management.reconcile_settlement` with the venue PROBE
substituted and nothing else. Labels are then read by `labelled`, which joins
each decision to its group's legs and asks whether BOTH were paid.

LEARNING HAS NO AUTHORITY HERE. A promoted model changes what
`region_probabilities` returns and nothing else: not a risk limit, not account
eligibility, not an authorization, not an evidence requirement. Two tests at the
bottom assert that, because it is the property that makes the rest safe.
"""

import os
import contextlib
import datetime as _dt

import asyncpg
import pytest

from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_learning as FL
from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_funded_model as FMD

DSN = os.environ.get("RN1X_TEST_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="needs a migrated database")

ACCT = "acct-learnpath"
VENUE = "PMUS_LEARNPATH"
#: The instant the fit may see. Everything evaluated must be decided AFTER it,
#: which is what makes the evaluation prospective rather than a description.
FIT_THROUGH = _dt.datetime(2026, 9, 1, tzinfo=_dt.timezone.utc)


def _at(offset_s):
    """A timestamptz. `labelled`'s `after` and `record_decision`'s `decided_at`
    are both timestamptz columns; passing a float raises a DataError inside the
    label read and surfaces as THE_LABELS_COULD_NOT_BE_READ."""
    return FIT_THROUGH + _dt.timedelta(seconds=offset_s)
N_GROUPS = 46          # > MIN_EVALUATION_ROWS, with room for exclusions


@contextlib.asynccontextmanager
async def _conn():
    c = await asyncpg.connect(DSN)
    try:
        yield c
    finally:
        await c.close()


async def _has(conn, table) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    table))


async def _clean(conn):
    """SCOPED TO THIS MODULE'S ACCOUNT, and run on teardown as well as setup.

    The lesson from the 41-failure gate: a module that cleans only before
    itself has moved its mess onto whoever runs next, and an unscoped DELETE
    would wipe another module's setup.
    """
    # OUTCOMES FIRST, and by decision_id: that table carries no account_id, it
    # joins through the decision. Deleting decisions first would orphan or be
    # refused by the reference.
    if await _has(conn, "bettor_funded_decision_outcomes"):
        await conn.execute(
            "DELETE FROM bettor_funded_decision_outcomes WHERE decision_id IN ("
            " SELECT decision_id FROM bettor_funded_decisions"
            " WHERE account_id=$1)", ACCT)
    if await _has(conn, "bettor_funded_decisions"):
        await conn.execute(
            "DELETE FROM bettor_funded_decisions WHERE account_id=$1", ACCT)
    if await _has(conn, "bettor_funded_models"):
        await conn.execute("DELETE FROM bettor_funded_models "
                           "WHERE model_id LIKE 'learnpath-%'")
    await conn.execute(
        "DELETE FROM bettor_funded_fills WHERE intent_id IN ("
        " SELECT intent_id FROM bettor_funded_intents WHERE account_id=$1)",
        ACCT)
    for t in ("bettor_funded_economics", "bettor_funded_leg_reservations"):
        if await _has(conn, t):
            with contextlib.suppress(Exception):
                await conn.execute(
                    "DELETE FROM %s WHERE intent_id IN ("
                    " SELECT intent_id FROM bettor_funded_intents"
                    " WHERE account_id=$1)" % t, ACCT)
    await conn.execute("DELETE FROM bettor_funded_intents WHERE account_id=$1",
                       ACCT)
    # AND THE GROUPS THOSE INTENTS OPENED, with their one-row results. Left
    # behind, they accumulated across runs (hundreds on a reused database).
    if await _has(conn, "bettor_funded_group_results"):
        await conn.execute(
            "DELETE FROM bettor_funded_group_results WHERE group_id IN ("
            " SELECT group_id FROM bettor_funded_portfolio_groups"
            " WHERE account_id=$1)", ACCT)
    if await _has(conn, "bettor_funded_portfolio_groups"):
        with contextlib.suppress(Exception):
            await conn.execute(
                "DELETE FROM bettor_funded_portfolio_groups WHERE account_id=$1",
                ACCT)


@pytest.fixture(autouse=True)
async def _leave_nothing_behind():
    yield
    if DSN:
        async with _conn() as c:
            await _clean(c)


def _width_for(p):
    """How many outcome regions pay both legs, from the sweep parameter.

    0..4 regions. A one-point middle and a four-point middle are different
    bets, which is exactly why `features_of` carries the width.
    """
    return int(round(4.0 * float(p)))


def _probe_for(payout):
    """THE VENUE'S SETTLEMENT REPORT, substituted at the TRANSPORT only.

    `reconcile_settlement` takes `probe` precisely so the HTTP call can be
    stood in for; everything it does with the answer -- the authoritative
    reading check, the closure reason, the settlement jsonb -- is the code
    under test and runs unchanged.

    `payout=None` models a fixture the venue has NOT resolved: the reading is
    PENDING, which is not authoritative, so nothing closes and the group
    contributes NO LABEL. That is the unresolved case and it is a real one.
    """
    def probe(client, slug):
        if payout is None:
            return {"terminal_reading": "PENDING",
                    "why": "the venue has not reported this fixture"}
        if payout == "VOID":
            # THE VENUE DECLARING A VOID. A distinct terminal reading with a
            # distinct closure reason and a distinct money consequence: the
            # remaining basis is refunded rather than a payout being won.
            return {"terminal_reading": "EXPLICIT_VOID",
                    "authoritative_payout_present": True,
                    "why": "the venue declared this fixture void"}
        # `settlement_price` under `reader_verdict` is the key the consumer
        # actually reads; a payout stated anywhere else is ignored and the
        # position stays open, which is the conservative behaviour.
        return {"terminal_reading": "REPORTED_SETTLEMENT",
                "authoritative_payout_present": True,
                "reader_verdict": {"settlement_price": float(payout)},
                "why": "the venue reported a settlement"}
    return probe


async def _group(conn, *, gid, primary_pays, hedge_pays, decided_at,
                 p_middle=0.5, unresolved=False, price=0.45):
    """ONE TWO-LEG GROUP, through the real ingestion and settlement paths.

    Returns the decision id. `primary_pays` / `hedge_pays` are the venue
    payouts per leg; the LABEL is "did both legs pay", read by `labelled` from
    each leg's own settlement rather than from anything asserted here.
    """
    # THE GROUP IS CREATED BY ITS FIRST LEG, which is how production does it:
    # `record_intent` derives `grp:<intent_id>` and inserts the group row ONLY
    # when no group id is passed. Passing one for the first leg is refused with
    # THE_PORTFOLIO_GROUP_DOES_NOT_EXIST -- correctly, since a leg cannot join a
    # group nobody opened. So leg one omits it and leg two joins what leg one
    # made.
    legs = []
    group_id = None
    for role, pays in (("PRIMARY", primary_pays), ("HEDGE", hedge_pays)):
        iid = "%s-%s" % (gid, role.lower())
        coll = FX.collateral_for(price, 10, FX.LONG)
        got = await FB.record_intent(
            conn, intent_id=iid, account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug="aec-%s-%s" % (gid, role),
            event_key=gid, order_intent=FX.LONG, limit_price=price, quantity=10,
            collateral_usd=coll, effective_digest="d-%s" % iid,
            held_is_long=True, portfolio_group_id=group_id, leg_role=role,
            group_structure="INDIRECT_MIDDLE")
        if not got.get("ok"):
            # ONE LIVE INTENT AT A TIME is a real funded-book rule. Each leg is
            # settled and closed before the next group opens, below.
            return {"ok": False, "refusal": got.get("refusal"), "leg": iid}
        await FB.record_acknowledgement(conn, iid, venue_order_id="vo-" + iid,
                                        status="open")
        await FB.ingest_fills(conn, iid, [
            {"qty": 10.0, "price": price, "venue_fill_id": "vf-" + iid}])
        if group_id is None:
            group_id = await conn.fetchval(
                "SELECT portfolio_group_id FROM bettor_funded_intents"
                " WHERE intent_id=$1", iid)
            assert group_id, "the first leg did not open a group"
        legs.append((iid, pays))

    # THE DECISION IS WRITTEN BEFORE ANY OUTCOME EXISTS. That ordering is the
    # whole basis of a prospective evaluation, and `record_decision`'s own
    # docstring says so.
    did = "dec-" + gid
    # THE REAL FEATURE VECTOR, from `features_of`, not names I chose. The
    # kernel refuses a row missing a feature it was fitted on ("a model scores
    # the features it was fitted on or it does not score"), so an invented
    # vector fits and then cannot be scored -- which is how I found this.
    #
    # `middle_width_points` is the count of regions paying BOTH legs, and it is
    # the feature the label is actually about. Sweeping it is what gives the
    # skilled cohort something learnable and the unskilled cohort nothing.
    width = _width_for(p_middle)
    feats = FMD.features_of(
        {"both_win_regions": tuple(range(width)),
         "cost_cents": int(round(price * 200)),
         "min_payout_cents": 100 * (1 if width else 0),
         "max_payout_cents": 200},
        primary_cost_cents=int(round(price * 100)),
        hedge_cost_cents=int(round(price * 100)),
        overtime_included=True)
    rec = await FL.record_decision(
        conn, decision_id=did, account_id=ACCT, venue=VENUE, fixture=gid,
        # EPOCH SECONDS HERE, a datetime for `labelled(after=)`. The two
        # readers of the same column take different types, which is worth
        # knowing rather than rediscovering: a datetime here raises a
        # TypeError inside record_decision, and a float there surfaces as
        # THE_LABELS_COULD_NOT_BE_READ.
        action="ACQUIRE_HEDGE", decided_at=decided_at.timestamp(),
        group_id=group_id,
        worst_case_usd=-1.0, model_key=FMD.KEY_MIDDLE,
        model_version="baseline-0", features=feats,
        feature_sha=FMD.feature_sha(feats),
        predicted={"p_middle": float(p_middle)})
    if not rec.get("ok"):
        return {"ok": False, "refusal": rec.get("refusal"), "stage": "decision"}

    # THEN THE VENUE SETTLES, through the real consumer.
    for iid, pays in legs:
        await FM.reconcile_settlement(
            conn, intent_id=iid, client=object(),
            probe=_probe_for(None if unresolved else pays),
            now=(decided_at.timestamp() + 3600.0))
    return {"ok": True, "decision_id": did, "group_id": group_id}


async def _cohort(conn, *, n=N_GROUPS, skill=True, prefix="lp", base=100):
    """A COHORT OF RESOLVED TWO-LEG GROUPS.

    `skill=True` makes the label CORRELATED with the recorded feature, so a fit
    on it can beat the baseline. `skill=False` makes the label independent of
    it, so the same pipeline must REJECT the candidate. Both are run, because a
    pipeline that only ever promotes has not demonstrated a bar.
    """
    made = []
    for i in range(n):
        # p_hint sweeps 0.1..0.9; with skill the label follows it, without
        # skill it alternates regardless.
        p = 0.1 + 0.8 * (i % 9) / 8.0
        both = (_width_for(p) >= 3) if skill else (i % 2 == 0)
        got = await _group(
            conn, gid="%s%03d" % (prefix, i),
            primary_pays=1.0,
            # BOTH legs pay only in the both-win case; otherwise the hedge
            # leg's payout is zero and `bool_and(payout > 0)` is false.
            hedge_pays=(1.0 if both else 0.0),
            decided_at=_at(base + i), p_middle=p)
        if not got.get("ok"):
            return {"ok": False, "at": i, **got}
        made.append(got)
    return {"ok": True, "groups": made}


# ═════════════════════════════════════════════════════════════════════
# 1 · THE LABELS COME FROM SETTLEMENT, AND UNRESOLVED IS NOT A LABEL
# ═════════════════════════════════════════════════════════════════════

async def test_an_unresolved_group_contributes_no_label():
    """"A label read before the fixture finished is not a label." The venue
    reports PENDING, nothing closes, and `labelled` excludes the group -- it
    does NOT count as a loss."""
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        got = await _group(conn, gid="lp-unres", primary_pays=1.0,
                           hedge_pays=1.0, decided_at=_at(10),
                           unresolved=True)
        assert got.get("ok"), got
        rows = await FMD.labelled(conn, after=FIT_THROUGH, account_id=ACCT)
        assert rows["ok"], rows
        assert rows["n"] == 0, (
            "an unresolved fixture must contribute no label, not a zero: %r"
            % (rows,))


async def test_both_legs_paid_is_the_label_and_it_is_read_not_asserted():
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        win = await _group(conn, gid="lp-win", primary_pays=1.0,
                          hedge_pays=1.0, decided_at=_at(20))
        assert win.get("ok"), win
        rows = await FMD.labelled(conn, after=FIT_THROUGH, account_id=ACCT)
        assert rows["n"] == 1 and rows["labels"] == [1.0], rows
        await _clean(conn)
        lose = await _group(conn, gid="lp-lose", primary_pays=1.0,
                            hedge_pays=0.0, decided_at=_at(30))
        assert lose.get("ok"), lose
        rows = await FMD.labelled(conn, after=FIT_THROUGH, account_id=ACCT)
        assert rows["n"] == 1 and rows["labels"] == [0.0], rows


async def test_the_prospective_filter_excludes_anything_the_fit_could_see():
    """`after` is the registry's own `fit_through`. A decision made at or
    before it is not held-out evidence and must not be counted."""
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        before = await _group(conn, gid="lp-before", primary_pays=1.0,
                             hedge_pays=1.0, decided_at=_at(-500))
        assert before.get("ok"), before
        rows = await FMD.labelled(conn, after=FIT_THROUGH, account_id=ACCT)
        assert rows["n"] == 0, rows
        # WITHOUT the filter the same row IS visible, which is what makes the
        # filter load-bearing rather than decorative.
        unfiltered = await FMD.labelled(conn, account_id=ACCT)
        assert unfiltered["n"] == 1, unfiltered
        assert "NONE" in unfiltered["prospective_filter"]


# ═════════════════════════════════════════════════════════════════════
# 2 · PROMOTION AND REJECTION, THROUGH THE SAME PIPELINE
# ═════════════════════════════════════════════════════════════════════

#: WHERE THE TRAINING COHORT SITS: a day before the fit window closes, so every
#: training decision is inside it and every evaluation decision is after it.
TRAIN_BASE_S = -86400


async def _run_pipeline(conn, *, model_id, skill):
    """FIT ON ONE COHORT, SCORE ON ANOTHER.

    THIS USED TO FIT ON THE EVALUATION ROWS. It read `labelled(after=
    FIT_THROUGH)` -- the rows decided after the window -- fitted on them,
    declared `fit_through=FIT_THROUGH`, and then evaluated on the same rows,
    which the leak check scored PROSPECTIVE because it compares evaluation rows
    with the DECLARED window. `register` now checks that window against the
    fit's own rows and refuses that registration, so the training cohort here
    is a separate set of fixtures decided inside the window.
    """
    train = await _cohort(conn, skill=skill, prefix="lt", base=TRAIN_BASE_S)
    assert train.get("ok"), train
    coh = await _cohort(conn, skill=skill)
    assert coh.get("ok"), coh
    tr = await FMD.labelled(conn, through=FIT_THROUGH, account_id=ACCT)
    assert tr["ok"] and tr["n"] >= FMD.MIN_EVALUATION_ROWS, tr
    lab = await FMD.labelled(conn, after=FIT_THROUGH, account_id=ACCT)
    assert lab["ok"] and lab["n"] >= FMD.MIN_EVALUATION_ROWS, lab
    assert not set(tr["fixtures"]) & set(lab["fixtures"])
    fitted = FMD.fit(tr["rows"], tr["labels"], decided_at=tr["decided_at"])
    assert fitted.get("ok"), fitted
    reg = await FMD.register(conn, model_id=model_id,
                             model_version="v-" + model_id, fitted=fitted,
                             fit_through=FIT_THROUGH)
    assert reg.get("ok"), reg
    ev = await FMD.evaluate(conn, model_id=model_id, account_id=ACCT)
    prom = await FMD.promote(conn, model_id=model_id,
                             approved_by="integration-test")
    return {"labels": lab, "fit": fitted, "evaluation": ev,
            "promotion": prom}


async def test_a_candidate_with_skill_is_promoted_and_then_predicts():
    """THE POSITIVE OUTCOME, and it is the one that unblocks the hedge: with an
    APPROVED model `predict_for` stops refusing, which is what `discover` needs
    before an indirect candidate can be ranked at all."""
    async with _conn() as conn:
        for t in ("bettor_funded_decisions", "bettor_funded_models"):
            if not await _has(conn, t):
                pytest.skip("%s is not in this database" % t)
        await _clean(conn)
        got = await _run_pipeline(conn, model_id="learnpath-skill", skill=True)
        ev, prom = got["evaluation"], got["promotion"]
        assert ev.get("ok"), ev
        assert ev["n"] >= FMD.MIN_EVALUATION_ROWS, ev
        # `prospective` lives inside the recorded `evaluation` report, which is
        # the same jsonb `promote` re-reads off the model row.
        pros = (ev.get("evaluation") or {}).get("prospective") or {}
        assert pros.get("verdict") == "PROSPECTIVE", ev
        assert pros["rows_the_fit_could_have_seen"] == 0, pros
        assert prom.get("ok"), prom
        # AND THE REGISTRY NOW ANSWERS.
        appr = await FMD.approved(conn, model_key=FMD.KEY_MIDDLE)
        assert appr.get("ok"), appr
        assert appr["model"]["model_id"] == "learnpath-skill"


async def test_a_candidate_with_no_skill_is_rejected_by_the_same_bar():
    """THE NEGATIVE OUTCOME, and it is the one that makes the positive mean
    anything. Same cohort size, same pipeline, label independent of the
    feature: the promotion must be REFUSED and the registry must stay empty."""
    async with _conn() as conn:
        for t in ("bettor_funded_decisions", "bettor_funded_models"):
            if not await _has(conn, t):
                pytest.skip("%s is not in this database" % t)
        await _clean(conn)
        got = await _run_pipeline(conn, model_id="learnpath-noskill",
                                  skill=False)
        prom = got["promotion"]
        assert prom.get("ok") is False, (
            "a model with no skill over its baseline must not be approved: %r"
            % (prom,))
        assert prom.get("refusal"), prom
        appr = await FMD.approved(conn, model_key=FMD.KEY_MIDDLE)
        assert appr.get("ok") is False
        assert appr.get("refusal") == FMD.R_NO_APPROVED_MODEL


async def test_too_few_labels_refuses_even_with_a_good_fit():
    """The row bar is not passable by argument."""
    async with _conn() as conn:
        for t in ("bettor_funded_decisions", "bettor_funded_models"):
            if not await _has(conn, t):
                pytest.skip("%s is not in this database" % t)
        await _clean(conn)
        train = await _cohort(conn, n=6, skill=True, prefix="lt",
                              base=TRAIN_BASE_S)
        assert train.get("ok"), train
        coh = await _cohort(conn, n=6, skill=True)
        assert coh.get("ok"), coh
        lab = await FMD.labelled(conn, after=FIT_THROUGH, account_id=ACCT)
        assert 0 < lab["n"] < FMD.MIN_EVALUATION_ROWS
        tr = await FMD.labelled(conn, through=FIT_THROUGH, account_id=ACCT)
        fitted = FMD.fit(tr["rows"], tr["labels"], decided_at=tr["decided_at"])
        await FMD.register(conn, model_id="learnpath-thin",
                           model_version="v-thin", fitted=fitted,
                           fit_through=FIT_THROUGH)
        await FMD.evaluate(conn, model_id="learnpath-thin", account_id=ACCT)
        prom = await FMD.promote(conn, model_id="learnpath-thin",
                                 approved_by="integration-test")
        assert prom.get("ok") is False
        assert prom.get("refusal") in (FMD.R_TOO_FEW_LABELS,
                                       FMD.R_NOT_EVALUATED,
                                       FMD.R_EVALUATION_NOT_PROSPECTIVE)


async def test_an_approval_with_no_approver_is_refused():
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_models"):
            pytest.skip("migration 135 is not in this database")
        prom = await FMD.promote(conn, model_id="learnpath-anything",
                                 approved_by="  ")
        assert prom.get("ok") is False
        assert prom["refusal"] == FMD.R_NO_APPROVER


# ═════════════════════════════════════════════════════════════════════
# 3 · LEARNING HAS NO AUTHORITY OVER ANYTHING BUT THE PROBABILITY
# ═════════════════════════════════════════════════════════════════════

def test_the_model_module_cannot_reach_a_limit_or_an_authorization():
    """A promoted model changes what `region_probabilities` returns and
    nothing else. This is checked on the SOURCE rather than by behaviour,
    because the claim is about what the module can reach at all."""
    import inspect

    src = inspect.getsource(FMD)
    for forbidden in ("FUNDED_SUBMISSION_ENABLED", "REAL_ORDER_SUBMISSION",
                      "authorize(", "record_intent", "submit_order",
                      "MAX_DRAWDOWN", "record_authorization"):
        assert forbidden not in src, (
            "bettor_funded_model must not be able to reach %r: learning does "
            "not move a limit, an eligibility or an authorization"
            % forbidden)


def test_the_promotion_bar_is_declared_and_not_computed_at_the_point_of_use():
    """The bar has to be a constant a reviewer can read, not a number the
    caller supplies."""
    assert FMD.MIN_EVALUATION_ROWS == 40
    assert FMD.MIN_SKILL_MARGIN == 0.01
    import inspect
    sig = inspect.signature(FMD.promote)
    assert "min_rows" not in sig.parameters
    assert "min_margin" not in sig.parameters


# ═════════════════════════════════════════════════════════════════════
# 4 · A VOID IS NOT A MIDDLE -- A DEFECT THIS FILE FOUND
# ═════════════════════════════════════════════════════════════════════

async def test_a_voided_fixture_contributes_no_label_and_is_not_a_positive():
    """THE DEFECT THIS PINS, AND IT WAS IN PRODUCTION CODE, NOT IN A TEST.

    `LABEL_SQL` read the label as `bool_and(payout_usd > 0)`. A fixture the
    venue VOIDS closes both legs `VOIDED_BY_THE_VENUE` and REFUNDS each leg's
    remaining basis, which `reconcile_settlement` writes as `payout_usd` -- a
    positive number. So every void was labelled "the middle occurred".

    Measured before the fix, on a two-leg group at $0.45 a contract: both legs
    closed VOIDED_BY_THE_VENUE with payout_usd 4.5 each, and `labelled`
    returned n=1, labels=[1.0].

    The direction is the expensive one. Voids entering training as POSITIVES
    make the model over-predict p(both legs pay), and that error buys hedges.

    A refunded basis is not a won payout and the fixture never happened, so
    there is no correct label for it -- it is excluded, exactly like an
    unresolved fixture, rather than relabelled as a loss.
    """
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        got = await _group(conn, gid="lp-void", primary_pays="VOID",
                           hedge_pays="VOID", decided_at=_at(40))
        assert got.get("ok"), got
        # THE VOID REALLY HAPPENED, through the real consumer: both legs closed
        # for the void reason and both carry a POSITIVE refunded payout.
        rows = await conn.fetch(
            "SELECT closed_reason,"
            "       (settlement->>'payout_usd')::numeric AS payout"
            "  FROM bettor_funded_intents WHERE account_id=$1", ACCT)
        assert len(rows) == 2
        assert all(r["closed_reason"] == "VOIDED_BY_THE_VENUE" for r in rows)
        assert all(float(r["payout"]) > 0 for r in rows), (
            "the refund is positive, which is exactly why bool_and(payout > 0) "
            "read it as a win")
        # AND YET IT IS NOT A LABEL.
        lab = await FMD.labelled(conn, after=FIT_THROUGH, account_id=ACCT)
        assert lab["ok"], lab
        assert lab["n"] == 0, (
            "a voided fixture must contribute no label. Before the fix this "
            "returned n=1, labels=[1.0]: %r" % (lab,))


async def test_one_voided_leg_disqualifies_the_whole_group():
    """A group is one observation. If either leg voided, the structure's
    outcome space did not resolve and the group is excluded -- a half-void is
    not half an observation."""
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        got = await _group(conn, gid="lp-halfvoid", primary_pays=1.0,
                           hedge_pays="VOID", decided_at=_at(50))
        assert got.get("ok"), got
        lab = await FMD.labelled(conn, after=FIT_THROUGH, account_id=ACCT)
        assert lab["n"] == 0, lab


# ═════════════════════════════════════════════════════════════════════
# 5 · THE WINDOW IS CHECKED, THE HOLDOUT IS BY FIXTURE, AND THE SCHEDULE
#     JOINS AND SCORES WITHOUT PROMOTING
# ═════════════════════════════════════════════════════════════════════

async def test_a_fit_on_the_rows_it_is_scored_on_cannot_be_registered():
    """THE DEFECT THIS FILE USED TO DEMONSTRATE AS A SUCCESS.

    Fit on the decisions after the window, declare the window closed before
    them: the leak check compared evaluation rows with the declared window and
    called it PROSPECTIVE. `register` now reads when the fit's own rows were
    decided and refuses."""
    async with _conn() as conn:
        for t in ("bettor_funded_decisions", "bettor_funded_models"):
            if not await _has(conn, t):
                pytest.skip("%s is not in this database" % t)
        await _clean(conn)
        coh = await _cohort(conn, n=6, skill=True)
        assert coh.get("ok"), coh
        lab = await FMD.labelled(conn, after=FIT_THROUGH, account_id=ACCT)
        fitted = FMD.fit(lab["rows"], lab["labels"],
                         decided_at=lab["decided_at"])
        reg = await FMD.register(conn, model_id="learnpath-insample",
                                 model_version="v", fitted=fitted,
                                 fit_through=FIT_THROUGH)
        assert reg["ok"] is False
        assert reg["refusal"] == FMD.R_FIT_WINDOW_UNDERSTATED, reg
        # AND A FIT THAT DOES NOT SAY WHEN ITS ROWS WERE DECIDED IS NOT TAKEN
        # ON TRUST EITHER
        reg = await FMD.register(conn, model_id="learnpath-unstated",
                                 model_version="v",
                                 fitted=FMD.fit(lab["rows"], lab["labels"]),
                                 fit_through=FIT_THROUGH)
        assert reg["ok"] is False
        assert reg["refusal"] == FMD.R_TRAINING_WINDOW_NOT_STATED, reg
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_models "
            " WHERE model_id LIKE 'learnpath-%'") == 0


async def test_a_fixture_the_fit_could_see_is_held_out_whole():
    """THE LABEL BELONGS TO THE FIXTURE. A fixture decided once inside the
    window and again after it shares one outcome across both decisions, so its
    later decision is not held-out evidence and is dropped -- and the bar is
    counted in fixtures, so repeated decisions cannot fill it."""
    async with _conn() as conn:
        for t in ("bettor_funded_decisions", "bettor_funded_models"):
            if not await _has(conn, t):
                pytest.skip("%s is not in this database" % t)
        await _clean(conn)
        train = await _cohort(conn, n=6, skill=True, prefix="lt",
                              base=TRAIN_BASE_S)
        assert train.get("ok"), train
        coh = await _cohort(conn, n=4, skill=True)
        assert coh.get("ok"), coh
        # A SECOND, LATER DECISION ON A TRAINING FIXTURE
        first = train["groups"][0]
        feats = FMD.features_of(
            {"both_win_regions": (0,), "cost_cents": 90,
             "min_payout_cents": 100, "max_payout_cents": 200},
            primary_cost_cents=45, hedge_cost_cents=45,
            overtime_included=True)
        rec = await FL.record_decision(
            conn, decision_id="dec-lt000-again", account_id=ACCT,
            venue=VENUE, fixture="lt000", action="HOLD",
            decided_at=_at(500).timestamp(), group_id=first["group_id"],
            worst_case_usd=-1.0, model_key=FMD.KEY_MIDDLE,
            model_version="baseline-0", features=feats,
            feature_sha=FMD.feature_sha(feats), predicted={"p_middle": 0.3})
        assert rec.get("ok"), rec
        # AND THREE MORE DECISIONS ON ONE EVALUATION FIXTURE: four rows, one
        # outcome
        again = coh["groups"][0]
        for k in range(3):
            rec = await FL.record_decision(
                conn, decision_id="dec-lp000-%d" % k, account_id=ACCT,
                venue=VENUE, fixture="lp000", action="HOLD",
                decided_at=_at(600 + k).timestamp(),
                group_id=again["group_id"], worst_case_usd=-1.0,
                model_key=FMD.KEY_MIDDLE, model_version="baseline-0",
                features=feats, feature_sha=FMD.feature_sha(feats),
                predicted={"p_middle": 0.3})
            assert rec.get("ok"), rec
        tr = await FMD.labelled(conn, through=FIT_THROUGH, account_id=ACCT)
        fitted = FMD.fit(tr["rows"], tr["labels"], decided_at=tr["decided_at"])
        reg = await FMD.register(conn, model_id="learnpath-holdout",
                                 model_version="v", fitted=fitted,
                                 fit_through=FIT_THROUGH)
        assert reg["ok"], reg
        ev = await FMD.evaluate(conn, model_id="learnpath-holdout",
                                account_id=ACCT)
        assert ev["fixtures_held_out_because_the_fit_could_see_them"] == \
            ["lt000"], ev
        # 4 evaluation fixtures, 7 rows: the three repeats are rows, not events
        assert ev["n_events"] == 4, ev
        assert ev["n"] == 7, ev
        assert ev["refusal"] == FMD.R_TOO_FEW_LABELS


async def test_the_schedule_joins_outcomes_and_scores_candidates_but_never_promotes():
    """THE LEARNING LOOP ON THE SCHEDULED PATH.

    `scheduled_learning_pass` is what the funded service now runs every cycle.
    It attaches each finished group's result to its decisions as a versioned
    outcome, scores every candidate prospectively and by fixture, and leaves
    promotion to a named approver. The same candidate is then promoted by the
    owner's call, and a second pass does not undo or repeat anything."""
    from sportsassets import bettor_funded_pair_cycle as PC

    async with _conn() as conn:
        for t in ("bettor_funded_decisions", "bettor_funded_models",
                  "bettor_funded_decision_outcomes"):
            if not await _has(conn, t):
                pytest.skip("%s is not in this database" % t)
        await _clean(conn)
        train = await _cohort(conn, skill=True, prefix="lt",
                              base=TRAIN_BASE_S)
        assert train.get("ok"), train
        coh = await _cohort(conn, skill=True)
        assert coh.get("ok"), coh
        tr = await FMD.labelled(conn, through=FIT_THROUGH, account_id=ACCT)
        reg = await FMD.register(
            conn, model_id="learnpath-scheduled", model_version="v-sched",
            fitted=FMD.fit(tr["rows"], tr["labels"],
                           decided_at=tr["decided_at"]),
            fit_through=FIT_THROUGH)
        assert reg["ok"], reg
        before = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_decisions "
            " WHERE account_id=$1 AND NOT realised_known", ACCT)
        assert before == 2 * N_GROUPS

        got = await PC.scheduled_learning_pass(conn, account_id=ACCT)
        assert got["ok"], got
        assert got["promoted_anything"] is False
        join = got["join"]
        assert len(join["joined"]) == 2 * N_GROUPS, join
        assert join["refused"] == [] and join["waiting_for_the_position"] == []
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_decisions "
            " WHERE account_id=$1 AND NOT realised_known", ACCT) == 0
        # ONE GROUP RESULT PER GROUP, however many decisions it had
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_group_results r "
            "  JOIN bettor_funded_portfolio_groups g USING (group_id) "
            " WHERE g.account_id=$1", ACCT) == 2 * N_GROUPS
        scored = {c["model_id"]: c for c in got["evaluate"]["scored"]}
        mine = scored["learnpath-scheduled"]
        assert mine["ok"] is True, mine
        assert mine["prospective"] == "PROSPECTIVE"
        assert mine["awaiting"]
        # STILL A CANDIDATE: the schedule scored it and approved nothing
        assert (await FMD.approved(conn, model_key=FMD.KEY_MIDDLE))[
            "refusal"] == FMD.R_NO_APPROVED_MODEL
        # THE OWNER'S CALL DOES
        prom = await FMD.promote(conn, model_id="learnpath-scheduled",
                                 approved_by="owner@test")
        assert prom["ok"], prom
        # AND A SECOND PASS JOINS NOTHING TWICE AND PROMOTES NOTHING
        again = await PC.scheduled_learning_pass(conn, account_id=ACCT)
        assert again["join"]["joined"] == []
        assert again["promoted_anything"] is False
        appr = await FMD.approved(conn, model_key=FMD.KEY_MIDDLE)
        assert appr["model"]["model_id"] == "learnpath-scheduled"

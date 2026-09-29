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

import asyncio
import contextlib
import datetime as _dt
import json
import os
import time

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
                           "WHERE model_id LIKE 'learnpath-%' "
                           "   OR model_id LIKE 'fmc:%'")
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
    settled_at = decided_at.timestamp() + 3600.0
    for iid, pays in legs:
        await FM.reconcile_settlement(
            conn, intent_id=iid, client=object(),
            probe=_probe_for(None if unresolved else pays),
            now=settled_at)
    # THE OUTCOME BECAME KNOWN AT `settled_at`: the consumer writes the instant
    # it read the venue (`now`) into `settlement.at`, and that -- not the row's
    # `closed_at` -- is what the label query reads as availability.
    return {"ok": True, "decision_id": did, "group_id": group_id}


async def _cohort(conn, *, n=N_GROUPS, skill=True, prefix="lp", base=100,
                  step=1):
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
            decided_at=_at(base + i * step), p_middle=p)
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
#: training decision -- and its outcome, an hour later -- is inside it.
TRAIN_BASE_S = -86400


def _after_freeze_s():
    """An offset from FIT_THROUGH that lands an hour after NOW.

    PROSPECTIVE evidence is predictions on decisions made after the model was
    frozen, and a model registered in this test is frozen at the wall clock.
    So the evaluation cohort is decided after it: in a test, that is the
    future. Decisions between FIT_THROUGH and now are RETROSPECTIVE
    out-of-sample evidence, and the pipeline keeps the two apart.
    """
    return int(time.time() - FIT_THROUGH.timestamp()) + 3600


async def _run_pipeline(conn, *, model_id, skill, n_eval=N_GROUPS):
    """FIT ON ONE COHORT, FREEZE, THEN SCORE ON DECISIONS MADE AFTERWARDS.

    THIS USED TO FIT ON THE EVALUATION ROWS, and before that to call rows
    decided after the fit cutoff "prospective" even though the model was chosen
    after they happened. Now: a training cohort decided and resolved inside the
    window, a fit from those RECORDS, a registration (the freeze), and only
    then an evaluation cohort decided after it.
    """
    train = await _cohort(conn, skill=skill, prefix="lt", base=TRAIN_BASE_S)
    assert train.get("ok"), train
    tr = await FMD.labelled(conn, through=FIT_THROUGH,
                            outcomes_through=FIT_THROUGH, account_id=ACCT)
    assert tr["ok"] and tr["n_events"] >= FMD.MIN_TRAIN_EVENTS, tr
    fitted = await FMD.fit_from_records(conn, through=FIT_THROUGH,
                                        account_id=ACCT)
    assert fitted.get("ok"), fitted
    assert fitted["training_provenance"]["n_events"] == tr["n_events"]
    reg = await FMD.register(conn, model_id=model_id,
                             model_version="v-" + model_id, fitted=fitted,
                             fit_through=FIT_THROUGH)
    assert reg.get("ok"), reg
    coh = await _cohort(conn, skill=skill, n=n_eval, base=_after_freeze_s())
    assert coh.get("ok"), coh
    lab = await FMD.labelled(conn, after=FIT_THROUGH, account_id=ACCT)
    assert not set(tr["fixtures"]) & set(lab["fixtures"])
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
        assert ev["n_events"] >= FMD.MIN_EVALUATION_EVENTS, ev
        doc = ev["evaluation"]
        assert doc[FMD.EVIDENCE_PROSPECTIVE]["evidence_kind"] == "PROSPECTIVE"
        assert doc["contamination"]["rows_the_fit_could_have_seen"] == 0
        assert prom.get("ok"), prom
        cmp_ = prom["comparison"]
        assert cmp_["evidence_kind"] == "PROSPECTIVE"
        assert cmp_["weighting"] == FMD.WEIGHTING_EVENT_BALANCED
        assert cmp_["n_events"] == N_GROUPS
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
        got = await _run_pipeline(conn, model_id="learnpath-thin", skill=True,
                                  n_eval=6)
        assert 0 < got["labels"]["n_events"] < FMD.MIN_EVALUATION_EVENTS
        assert got["evaluation"]["refusal"] == FMD.R_TOO_FEW_LABELS
        prom = got["promotion"]
        assert prom.get("ok") is False
        assert prom.get("refusal") == FMD.R_TOO_FEW_LABELS, prom


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
        doc = ev["evaluation"]
        assert doc["fixtures_held_out_because_the_fit_could_see_them"] == \
            ["lt000"], ev
        # 4 evaluation fixtures, 7 rows: the three repeats are rows, not events.
        # All were decided after the cutoff and before the freeze, so they are
        # RETROSPECTIVE out-of-sample, not prospective.
        retro = doc[FMD.EVIDENCE_RETROSPECTIVE]
        assert retro["n_events"] == 4 and retro["n"] == 7, retro
        assert retro["evidence_kind"] == "RETROSPECTIVE_OUT_OF_SAMPLE"
        assert doc[FMD.EVIDENCE_PROSPECTIVE]["n_events"] == 0
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
        reg = await FMD.register(
            conn, model_id="learnpath-scheduled", model_version="v-sched",
            fitted=await FMD.fit_from_records(conn, through=FIT_THROUGH,
                                              account_id=ACCT),
            fit_through=FIT_THROUGH)
        assert reg["ok"], reg
        # THE EVALUATION COHORT IS DECIDED AFTER THE FREEZE
        coh = await _cohort(conn, skill=True, base=_after_freeze_s())
        assert coh.get("ok"), coh
        before = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_decisions "
            " WHERE account_id=$1 AND NOT realised_known", ACCT)
        assert before == 2 * N_GROUPS

        got = await PC.scheduled_learning_pass(conn, account_id=ACCT)
        assert got["ok"], got
        assert got["promoted_anything"] is False
        # THE GENERATOR FIT NOTHING: the evaluation cohort resolves after now,
        # so the resolved set is still the 46 the owner's fit already covers.
        gen = got["generate"]
        assert gen["ok"] and gen["generated"] is False, gen
        assert gen["reason"] == "NOT_ENOUGH_NEW_EVENTS", gen
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
        assert mine["prospective_events"] == N_GROUPS
        assert mine["retrospective_out_of_sample_events"] == 0
        assert mine["weighting"] == FMD.WEIGHTING_EVENT_BALANCED
        assert mine["awaiting"]
        # STILL A CANDIDATE: the schedule scored it and approved nothing
        assert (await FMD.approved(conn, model_key=FMD.KEY_MIDDLE))[
            "refusal"] == FMD.R_NO_APPROVED_MODEL
        # THE OWNER'S CALL DOES
        prom = await FMD.promote(conn, model_id="learnpath-scheduled",
                                 approved_by="owner@test")
        assert prom["ok"], prom
        # AND A SECOND PASS JOINS NOTHING TWICE, FITS NOTHING NEW AND
        # PROMOTES NOTHING
        again = await PC.scheduled_learning_pass(conn, account_id=ACCT)
        assert again["join"]["joined"] == []
        assert again["promoted_anything"] is False
        assert again["generate"]["generated"] is False
        assert again["generate"]["reason"] == "NOT_ENOUGH_NEW_EVENTS"
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_models "
            " WHERE model_id LIKE 'fmc:%'") == 0
        appr = await FMD.approved(conn, model_key=FMD.KEY_MIDDLE)
        assert appr["model"]["model_id"] == "learnpath-scheduled"


# ═════════════════════════════════════════════════════════════════════
# 6 · WINDOWS DECLARED BEFORE FITTING, AND GENERATION THAT IS IDEMPOTENT
# ═════════════════════════════════════════════════════════════════════

async def _fmc_count(conn):
    return await conn.fetchval("SELECT count(*) FROM bettor_funded_models "
                               " WHERE model_id LIKE 'fmc:%'")


async def test_when_training_takes_every_fixture_the_plan_says_no_cohort_remains():
    """TOO FEW RESOLVED FIXTURES TO HOLD ANY OUT. The plan is made before the
    fit and says so; the only evidence the model can ever have is prospective.
    And two passes racing on the same ledger register ONE candidate."""
    from sportsassets import bettor_funded_pair_cycle as PC  # noqa: F401

    async with _conn() as conn, _conn() as other:
        for t in ("bettor_funded_decisions", "bettor_funded_models"):
            if not await _has(conn, t):
                pytest.skip("%s is not in this database" % t)
        await _clean(conn)
        coh = await _cohort(conn, n=50, prefix="lg", base=TRAIN_BASE_S)
        assert coh.get("ok"), coh
        now = time.time()
        # CONCURRENT PASSES on two connections, the same ledger, the same clock
        a, b = await asyncio.gather(
            FMD.generate_candidate(conn, now=now, account_id=ACCT),
            FMD.generate_candidate(other, now=now, account_id=ACCT))
        assert a["ok"] and b["ok"], (a, b)
        # ONE REGISTRATION; THE OTHER PASS WAITED FOR IT AND NAMES IT
        first, second = (a, b) if a["generated"] else (b, a)
        assert first["generated"] is True, (a, b)
        assert second["generated"] is False, (a, b)
        assert second["reason"] == "NOT_ENOUGH_NEW_EVENTS", second
        assert second["last_fit"]["model_id"] == first["model_id"]
        assert await _fmc_count(conn) == 1
        a = first
        win = a["windows"]
        assert win["declared_before_fitting"] is True
        assert win["retrospective_holdout"]["fixtures_planned"] == 0
        assert win["retrospective_holdout"]["why"] == FMD.NO_INDEPENDENT_COHORT
        assert win["training_cutoff_epoch_s"] == pytest.approx(now)
        row = await conn.fetchrow("SELECT * FROM bettor_funded_models "
                                  " WHERE model_id=$1", a["model_id"])
        assert row["state"] == "CANDIDATE"
        prov = json.loads(row["training_provenance"]) \
            if isinstance(row["training_provenance"], str) \
            else row["training_provenance"]
        assert prov["windows"]["retrospective_holdout"]["fixtures_planned"] == 0
        assert prov["n_events"] == 50
        # EVALUATED NOW, IT HAS NO INDEPENDENT EVIDENCE, AND SAYS WHY
        ev = await FMD.evaluate(conn, model_id=a["model_id"], account_id=ACCT)
        retro = ev["evaluation"][FMD.EVIDENCE_RETROSPECTIVE]
        assert retro["n_events"] == 0
        assert retro["why"] == FMD.NO_INDEPENDENT_COHORT
        assert ev["evaluation"][FMD.EVIDENCE_PROSPECTIVE]["n_events"] == 0
        # A RESTARTED PASS ON THE SAME LEDGER FITS NOTHING
        again = await FMD.generate_candidate(conn, now=time.time(),
                                             account_id=ACCT)
        assert again["reason"] == "NOT_ENOUGH_NEW_EVENTS", again
        assert await _fmc_count(conn) == 1
        assert (await FMD.approved(conn))["ok"] is False


async def test_with_enough_fixtures_the_latest_are_a_retrospective_holdout():
    """ENOUGH TO HOLD OUT. The latest MIN_EVALUATION_EVENTS fixtures become a
    RETROSPECTIVE out-of-sample cohort, the training cutoff for decisions AND
    labels is set before them, and the model can be scored on them at once --
    as retrospective evidence, which is not what promotion reads."""
    async with _conn() as conn:
        for t in ("bettor_funded_decisions", "bettor_funded_models"):
            if not await _has(conn, t):
                pytest.skip("%s is not in this database" % t)
        await _clean(conn)
        # two hours apart, each settled an hour after its decision, so every
        # outcome is known before the next fixture is decided
        coh = await _cohort(conn, n=90, prefix="lh", base=-86400 * 12,
                            step=7200)
        assert coh.get("ok"), coh
        gen = await FMD.generate_candidate(conn, now=time.time(),
                                           account_id=ACCT)
        assert gen["ok"] and gen["generated"], gen
        win = gen["windows"]
        assert win["retrospective_holdout"]["fixtures_planned"] == \
            FMD.MIN_EVALUATION_EVENTS
        assert win["training_fixtures_planned"] == 90 - FMD.MIN_EVALUATION_EVENTS
        assert gen["n_events"] == 90 - FMD.MIN_EVALUATION_EVENTS
        ev = await FMD.evaluate(conn, model_id=gen["model_id"], account_id=ACCT)
        doc = ev["evaluation"]
        retro = doc[FMD.EVIDENCE_RETROSPECTIVE]
        assert retro["n_events"] == FMD.MIN_EVALUATION_EVENTS, retro
        assert retro["evidence_kind"] == "RETROSPECTIVE_OUT_OF_SAMPLE"
        assert retro["report"] is not None
        assert doc[FMD.EVIDENCE_PROSPECTIVE]["n_events"] == 0
        assert ev["ok"] is False and ev["refusal"] == FMD.R_TOO_FEW_LABELS
        prom = await FMD.promote(conn, model_id=gen["model_id"],
                                 approved_by="owner@test")
        assert prom["ok"] is False
        assert prom["refusal"] == FMD.R_TOO_FEW_LABELS
        assert prom["comparison"]["evidence_kind"] == "PROSPECTIVE"
        # TEN MORE RESOLVED FIXTURES: a new training set, a new id
        more = await _cohort(conn, n=10, prefix="li", base=-86400 * 2,
                             step=3 * 3600)
        assert more.get("ok"), more
        nxt = await FMD.generate_candidate(conn, now=time.time(),
                                           account_id=ACCT)
        assert nxt["generated"] is True, nxt
        assert nxt["model_id"] != gen["model_id"]
        assert await _fmc_count(conn) == 2


# ═════════════════════════════════════════════════════════════════════
# 7 · REPEATED DECISIONS ON ONE FIXTURE MANUFACTURE NOTHING
# ═════════════════════════════════════════════════════════════════════

async def _repeat_decision(conn, *, decision_id, times, first_at):
    """`times` more decisions on the fixture of `decision_id`, identical
    vector, same group -- what a held position produces cycle after cycle."""
    d = await conn.fetchrow("SELECT * FROM bettor_funded_decisions "
                            " WHERE decision_id=$1", decision_id)
    feats = d["features"]
    feats = json.loads(feats) if isinstance(feats, str) else feats
    for k in range(times):
        rec = await FL.record_decision(
            conn, decision_id="%s-rep%04d" % (decision_id, k),
            account_id=ACCT, venue=VENUE, fixture=d["fixture"], action="HOLD",
            decided_at=first_at + k * 0.001, group_id=d["group_id"],
            worst_case_usd=-1.0, model_key=FMD.KEY_MIDDLE,
            model_version="baseline-0", features=feats,
            feature_sha=FMD.feature_sha(feats), predicted={"p_middle": 0.5})
        assert rec.get("ok"), rec


async def test_repeating_one_fixture_cannot_manufacture_promotion_evidence():
    """REPRODUCED BY INDEPENDENT REVIEW: the same forty fixtures scored 3.30946
    one decision each and 0.16026 with the one successful fixture repeated
    1,000 times, and the second was promoted. Here: a model that is wrong on 39
    of 40 prospective fixtures is refused; repeating the one it gets right
    1,000 times moves the decision-weighted figure and nothing that promotion
    reads."""
    async with _conn() as conn:
        for t in ("bettor_funded_decisions", "bettor_funded_models"):
            if not await _has(conn, t):
                pytest.skip("%s is not in this database" % t)
        await _clean(conn)
        train = await _cohort(conn, skill=True, prefix="lt", base=TRAIN_BASE_S)
        assert train.get("ok"), train
        reg = await FMD.register(
            conn, model_id="learnpath-rep", model_version="v-rep",
            fitted=await FMD.fit_from_records(conn, through=FIT_THROUGH,
                                              account_id=ACCT),
            fit_through=FIT_THROUGH)
        assert reg["ok"], reg
        base = _after_freeze_s()
        right = None
        for i in range(40):
            p = 0.1 + 0.8 * (i % 9) / 8.0
            truth = _width_for(p) >= 3
            # WRONG ON EVERY FIXTURE BUT THE FIRST: the label is inverted
            label = truth if i == 0 else (not truth)
            got = await _group(conn, gid="lq%03d" % i, primary_pays=1.0,
                               hedge_pays=(1.0 if label else 0.0),
                               decided_at=_at(base + i), p_middle=p)
            assert got.get("ok"), got
            if i == 0:
                right = got["decision_id"]
        ev1 = await FMD.evaluate(conn, model_id="learnpath-rep",
                                 account_id=ACCT)
        p1 = await FMD.promote(conn, model_id="learnpath-rep",
                               approved_by="owner@test")
        assert p1["ok"] is False and p1["refusal"] == FMD.R_NO_SKILL, p1
        # THE ONE CORRECT FIXTURE, DECIDED 1,000 MORE TIMES
        await _repeat_decision(conn, decision_id=right, times=1000,
                               first_at=_at(base + 100).timestamp())
        ev2 = await FMD.evaluate(conn, model_id="learnpath-rep",
                                 account_id=ACCT)
        p2 = await FMD.promote(conn, model_id="learnpath-rep",
                               approved_by="owner@test")
        pr1 = ev1["evaluation"][FMD.EVIDENCE_PROSPECTIVE]
        pr2 = ev2["evaluation"][FMD.EVIDENCE_PROSPECTIVE]
        assert pr1["n_events"] == pr2["n_events"] == 40
        assert pr2["n"] == pr1["n"] + 1000
        # THE DECISION-WEIGHTED FIGURE IS WHAT REPETITION WOULD HAVE BOUGHT...
        dw1 = pr1["report"]["decision_weighted_for_reference_only"]["log_loss"]
        dw2 = pr2["report"]["decision_weighted_for_reference_only"]["log_loss"]
        assert dw2 < dw1 / 4, (dw1, dw2)
        # ...AND THE EVENT-BALANCED ONE, THE ONLY ONE PROMOTION READS, DID NOT
        assert pr2["log_loss"] == pytest.approx(pr1["log_loss"], abs=1e-9)
        assert p2["ok"] is False and p2["refusal"] == FMD.R_NO_SKILL, p2
        assert p2["comparison"]["candidate"] == pytest.approx(
            p1["comparison"]["candidate"], abs=1e-9)
        assert p2["comparison"]["against"] == pytest.approx(
            p1["comparison"]["against"], abs=1e-9)
        assert (await FMD.approved(conn))["ok"] is False


async def test_training_is_event_balanced_too():
    """A FIXTURE DECIDED ON MANY CYCLES COUNTS ONCE IN THE FIT. The same
    forty fixtures, with one of them repeated 999 times inside the training
    window, produce the same model."""
    async with _conn() as conn:
        for t in ("bettor_funded_decisions", "bettor_funded_models"):
            if not await _has(conn, t):
                pytest.skip("%s is not in this database" % t)
        await _clean(conn)
        train = await _cohort(conn, n=40, skill=True, prefix="lt",
                              base=TRAIN_BASE_S - 86400)
        assert train.get("ok"), train
        a = await FMD.fit_from_records(conn, through=FIT_THROUGH,
                                       account_id=ACCT)
        await _repeat_decision(conn, decision_id=train["groups"][0]
                               ["decision_id"], times=999,
                               first_at=_at(TRAIN_BASE_S).timestamp())
        b = await FMD.fit_from_records(conn, through=FIT_THROUGH,
                                       account_id=ACCT)
        assert a["ok"] and b["ok"]
        assert a["n_events"] == b["n_events"] == 40
        assert b["training_provenance"]["n_rows"] == \
            a["training_provenance"]["n_rows"] + 999
        assert b["train_base_rate"] == pytest.approx(a["train_base_rate"],
                                                     abs=1e-9)
        ma, mb = FMD.load(a["params"]), FMD.load(b["params"])
        for r in train["groups"]:
            d = await conn.fetchval("SELECT features FROM bettor_funded_decisions"
                                    " WHERE decision_id=$1", r["decision_id"])
            d = json.loads(d) if isinstance(d, str) else d
            assert mb.predict(d) == pytest.approx(ma.predict(d), abs=1e-6)


async def test_identical_models_on_one_cohort_are_never_promoted_over_each_other():
    """THE STORED EVALUATIONS ARE NOT WHAT IS COMPARED. The incumbent's stored
    score is made to look terrible; a challenger with the SAME fit is re-scored
    beside it on the same prospective fixtures, improves by exactly zero, and
    is refused."""
    async with _conn() as conn:
        for t in ("bettor_funded_decisions", "bettor_funded_models"):
            if not await _has(conn, t):
                pytest.skip("%s is not in this database" % t)
        await _clean(conn)
        got = await _run_pipeline(conn, model_id="learnpath-inc", skill=True)
        assert got["promotion"]["ok"], got["promotion"]
        await conn.execute(
            "UPDATE bettor_funded_models SET evaluation = jsonb_set("
            " evaluation, '{PROSPECTIVE,log_loss}', '9.9'::jsonb) "
            " WHERE model_id='learnpath-inc'")
        reg = await FMD.register(
            conn, model_id="learnpath-same", model_version="v-same",
            fitted=await FMD.fit_from_records(conn, through=FIT_THROUGH,
                                              account_id=ACCT),
            fit_through=FIT_THROUGH)
        assert reg["ok"], reg
        # decided after BOTH freezes
        more = await _cohort(conn, skill=True, prefix="lr",
                             base=_after_freeze_s() + 7200)
        assert more.get("ok"), more
        await FMD.evaluate(conn, model_id="learnpath-same", account_id=ACCT)
        prom = await FMD.promote(conn, model_id="learnpath-same",
                                 approved_by="owner@test")
        assert prom["ok"] is False, prom
        assert prom["refusal"] == FMD.R_NO_SKILL
        cmp_ = prom["comparison"]
        assert cmp_["against_what"] == "THE_INCUMBENT"
        assert cmp_["improvement"] == pytest.approx(0.0, abs=1e-9)
        assert cmp_["candidate"] == pytest.approx(cmp_["against"], abs=1e-9)
        assert cmp_["n_events"] >= FMD.MIN_EVALUATION_EVENTS


# ═════════════════════════════════════════════════════════════════════
# 8 · PROVENANCE: BOUND TO RECORDS, AND BROKEN BY A CORRECTION
# ═════════════════════════════════════════════════════════════════════

async def test_the_label_is_each_legs_own_side_from_the_settlement_price():
    """A PUSH IS NOT A WIN; A PARTIAL EXIT DOES NOT CHANGE THE FIXTURE; A LEG
    WE EXITED BEFORE SETTLEMENT IS NOT A LABEL; A SHORT WINS AT PRICE ZERO."""
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        push = await _group(conn, gid="lp-push", primary_pays=1.0,
                            hedge_pays=0.5, decided_at=_at(20))
        part = await _group(conn, gid="lp-part", primary_pays=1.0,
                            hedge_pays=1.0, decided_at=_at(30))
        exited = await _group(conn, gid="lp-exit", primary_pays=1.0,
                              hedge_pays=1.0, decided_at=_at(40))
        short = await _group(conn, gid="lp-short", primary_pays=1.0,
                             hedge_pays=0.0, decided_at=_at(50))
        for g in (push, part, exited, short):
            assert g.get("ok"), g
        # a partial exit leaves little held: the cash is small, the price is 1
        await conn.execute(
            "UPDATE bettor_funded_intents SET settlement = settlement || "
            " '{\"payout_usd\": 0.0}'::jsonb "
            " WHERE portfolio_group_id=$1 AND leg_role='HEDGE'",
            part["group_id"])
        # a leg that left the book through our own exit, not the venue's price
        await conn.execute(
            "UPDATE bettor_funded_intents SET closed_reason='EXITED_IN_THE_MARKET'"
            " WHERE portfolio_group_id=$1 AND leg_role='HEDGE'",
            exited["group_id"])
        # the hedge leg as a SHORT: it won because the long side settled at 0
        await conn.execute(
            "UPDATE bettor_funded_intents SET order_intent="
            " 'ORDER_INTENT_BUY_SHORT' "
            " WHERE portfolio_group_id=$1 AND leg_role='HEDGE'",
            short["group_id"])
        lab = await FMD.labelled(conn, account_id=ACCT)
        by = dict(zip(lab["decision_ids"], zip(lab["labels"], lab["pushes"])))
        assert by[push["decision_id"]] == (0.0, True), by
        assert by[part["decision_id"]] == (1.0, False), by
        assert exited["decision_id"] not in by
        assert by[short["decision_id"]] == (1.0, False), by


async def test_a_corrected_settlement_after_the_fit_invalidates_the_model():
    """THE LABEL'S OWN VERSION IS BOUND. A training leg's settlement corrected
    after registration makes the training records stop reproducing, and the
    model is not promoted on a set that no longer exists as it was fit."""
    async with _conn() as conn:
        for t in ("bettor_funded_decisions", "bettor_funded_models"):
            if not await _has(conn, t):
                pytest.skip("%s is not in this database" % t)
        await _clean(conn)
        train = await _cohort(conn, skill=True, prefix="lt", base=TRAIN_BASE_S)
        assert train.get("ok"), train
        fitted = await FMD.fit_from_records(conn, through=FIT_THROUGH,
                                            account_id=ACCT)
        prov = fitted["training_provenance"]
        rec0 = prov["records"][0]
        # THE PROVENANCE NAMES WHAT A LABEL DEPENDS ON
        assert set(rec0) >= {"decision_id", "fixture", "feature_sha",
                             "feature_schema", "label", "leg_outcomes",
                             "outcome_available_at", "decided_at"}
        assert all(leg["read_at"] is not None and leg["payout_price"]
                   is not None for leg in rec0["leg_outcomes"])
        assert prov["feature_schema_sha"] == FMD.FEATURE_SCHEMA_SHA
        reg = await FMD.register(conn, model_id="learnpath-corr",
                                 model_version="v-corr", fitted=fitted,
                                 fit_through=FIT_THROUGH)
        assert reg["ok"], reg
        coh = await _cohort(conn, skill=True, base=_after_freeze_s())
        assert coh.get("ok"), coh
        assert (await FMD.evaluate(conn, model_id="learnpath-corr",
                                   account_id=ACCT))["ok"]
        # THE VENUE CORRECTS ONE TRAINING LEG'S SETTLEMENT
        leg = rec0["leg_outcomes"][0]
        await conn.execute(
            "UPDATE bettor_funded_intents SET settlement = settlement || "
            " jsonb_build_object('payout_price', "
            "   CASE WHEN (settlement->>'payout_price')::numeric = 1 "
            "        THEN 0.0 ELSE 1.0 END, 'at', $2::float8) "
            " WHERE intent_id=$1", leg["intent_id"], time.time())
        prom = await FMD.promote(conn, model_id="learnpath-corr",
                                 approved_by="owner@test")
        assert prom["ok"] is False, prom
        assert prom["refusal"] == FMD.R_TRAINING_RECORDS_DO_NOT_REPRODUCE
        assert rec0["decision_id"] in prom["changed_records"]


async def test_a_label_learned_after_the_cutoff_is_not_training_data():
    """DECIDED BEFORE THE CUTOFF IS NOT ENOUGH: the outcome must have been
    known by it too."""
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        early = await _group(conn, gid="lt-early", primary_pays=1.0,
                             hedge_pays=1.0, decided_at=_at(-7200))
        late = await _group(conn, gid="lt-late", primary_pays=1.0,
                            hedge_pays=0.0, decided_at=_at(-60))
        assert early.get("ok") and late.get("ok")
        # lt-late was decided 60 s before the cutoff and settled an hour later
        fitted = await FMD.fit_from_records(conn, through=FIT_THROUGH,
                                            account_id=ACCT)
        ids = fitted["training_provenance"]["decision_ids"]
        assert early["decision_id"] in ids
        assert late["decision_id"] not in ids


async def test_an_approved_model_is_record_bound_in_the_database_too():
    """MIGRATION 138'S CHECK: a DECLARED fit cannot be written APPROVED, by
    `promote` or by hand."""
    async with _conn() as conn:
        for t in ("bettor_funded_decisions", "bettor_funded_models"):
            if not await _has(conn, t):
                pytest.skip("%s is not in this database" % t)
        await _clean(conn)
        rows = [dict.fromkeys(FMD.FEATURES, 1.0) for _ in range(10)]
        fitted = FMD.fit(rows, [0.0, 1.0] * 5,
                         decided_at=[FIT_THROUGH.timestamp() - 10] * 10)
        reg = await FMD.register(conn, model_id="learnpath-declared",
                                 model_version="v-decl", fitted=fitted,
                                 fit_through=FIT_THROUGH)
        assert reg["ok"] and reg["provenance"] == "DECLARED", reg
        prom = await FMD.promote(conn, model_id="learnpath-declared",
                                 approved_by="owner@test")
        assert prom["refusal"] == FMD.R_TRAINING_NOT_BOUND_TO_RECORDS
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "UPDATE bettor_funded_models SET state='APPROVED', "
                " approved_at=now(), approved_by='hand', evaluation='{}' "
                " WHERE model_id='learnpath-declared'")

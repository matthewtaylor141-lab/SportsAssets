"""AN APPROVED MODEL CHANGES A LATER DECISION. That is the claim, and it is §6.

    "A ledger or bound check alone is not self-improvement."

The criticism was right and this file is the answer to it. Five things had to
exist, and the last one is the only one that makes the other four worth having:

  1 PROSPECTIVE FEATURES AND A MODEL VERSION on the immutable decision row,
    written before the outcome exists.
  2 OUTCOME LABELS from the venue's own settlement, per leg.
  3 EVALUATION on decisions the model could not have seen -- checked against the
    registry's `fit_through`, which a trigger forbids editing.
  4 CONTROLLED PROMOTION AND ROLLBACK, with one approved version per key enforced
    by the database rather than by convention.
  5 AND THE DEMONSTRATION: the SAME structure, the SAME costs, the SAME held
    position -- two approved models, two different actions.

WHAT IS DEPLOYED AND WHAT IS CHOSEN. The kernel, the metrics, the registry, the
promotion bar and the decision policy are deployed code. The training data below
is SYNTHETIC and is labelled as such throughout: it is generated from a stated
rule so that a model CAN learn something, because the funded lane has no history
of resolved pairs yet. The point of the file is the machinery, not the model's
accuracy, and a synthetic fit proves nothing about how well this will predict real
fixtures.

    NO FUNDED ORDER IS SENT. NO VENUE IS CONTACTED.
"""

from __future__ import annotations

import dataclasses
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import bettor_funded_decision as FD
from sportsassets import bettor_funded_indirect_pair as FIP
from sportsassets import bettor_funded_learning as FL
from sportsassets import bettor_funded_model as FMD
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_indirect_structures as IS

DSN = __import__("os").environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

LABEL = "SYNTHETIC TRAINING DATA, GENERATED FROM A STATED RULE"
ACCT = "acct-funded-DEMONSTRATION-learns"
VENUE = "PMUS"
KEY = FMD.KEY_MIDDLE


# ════════════════════════════════════════════════════════════════════
# THE STRUCTURES, FROM THE CLASSIFIER
# ════════════════════════════════════════════════════════════════════

def _structure(*, primary_cents=62, hedge_cents=41):
    a = dataclasses.replace(IS.BEARS_MONEYLINE, quantity=10,
                            cost_cents_per_unit=primary_cents)
    b = dataclasses.replace(IS.PANTHERS_PLUS_4_5, quantity=10,
                            cost_cents_per_unit=hedge_cents)
    st = IS.classify(a, b, sport_permits_tie=False, fixture_can_postpone=False)
    assert st.taxonomy == IS.MIDDLE, st.why
    return st


def _outside_split(st):
    """THE MASS OUTSIDE THE MIDDLE, STATED. `region_probabilities` refuses without
    it rather than spreading the remainder uniformly, so every caller has to say
    what it believes -- including this test."""
    both = set(st.both_win_regions)
    outside = [r["region"] for r in st.table if r["region"] not in both]
    # All of it on the region the fixture actually resolved into. A stated
    # choice, not a default.
    return {r: (1.0 if r == "margin > 5" else 0.0) for r in outside}


# ════════════════════════════════════════════════════════════════════
# SYNTHETIC TRAINING DATA, FROM A STATED RULE
# ════════════════════════════════════════════════════════════════════

#: THE RULE THE SYNTHETIC LABELS FOLLOW, written down so the fit is not magic:
#: a WIDER middle window hits more often. Five regions pay both legs at the real
#: fixture's line; a one-region window hits rarely. Everything else is noise the
#: model has to ignore.
def _synthetic(n=200):
    rows, labels = [], []
    for i in range(n):
        width = 1.0 + float(i % 6)          # 1 .. 6 regions wide
        rows.append({
            "middle_width_points": width,
            "both_win_regions": width,
            "cost_cents": 95.0 + float(i % 17),
            "min_payout_cents": 100.0,
            "max_payout_cents": 200.0,
            "primary_cost_cents": 55.0 + float(i % 13),
            "hedge_cost_cents": 38.0 + float(i % 7),
            "overtime_included": 1.0 if i % 2 else 0.0,
        })
        # THE RULE: p(hit) rises with the window. Deterministic given i, so the
        # fit is reproducible; `i % 10` supplies the within-width variation that
        # stops the label being a function the model can memorise exactly.
        labels.append(1.0 if (i % 10) < int(width + 1) else 0.0)
    return rows, labels


# ════════════════════════════════════════════════════════════════════
# SEEDING THE LEDGER WITH RESOLVED DECISIONS TO EVALUATE AGAINST
# ════════════════════════════════════════════════════════════════════

def _trained_before(rows):
    """WHEN THE SYNTHETIC TRAINING ROWS WERE DECIDED: sixty days back, inside
    every fit window these tests declare. `register` now checks the declared
    window against the fit's own rows, so the rows have to say when they were
    decided -- and these are not the evaluation decisions, which are separate
    fixtures recorded after the window."""
    from datetime import datetime as _d, timedelta as _t, timezone as _z
    start = (_d.now(_z.utc) - _t(days=60)).timestamp()
    return [start + 60.0 * i for i in range(len(rows))]


async def _clean(conn):
    await conn.execute(
        "DELETE FROM bettor_funded_decision_outcomes WHERE decision_id IN "
        "(SELECT decision_id FROM bettor_funded_decisions WHERE account_id=$1)",
        ACCT)
    await conn.execute("DELETE FROM bettor_funded_decisions WHERE account_id=$1",
                       ACCT)
    await conn.execute(
        "DELETE FROM bettor_funded_economics WHERE intent_id IN "
        "(SELECT intent_id FROM bettor_funded_intents WHERE account_id=$1)",
        ACCT)
    await conn.execute(
        "DELETE FROM bettor_funded_fills WHERE intent_id IN "
        "(SELECT intent_id FROM bettor_funded_intents WHERE account_id=$1)",
        ACCT)
    await conn.execute("DELETE FROM bettor_funded_intents WHERE account_id=$1",
                       ACCT)
    await conn.execute(
        "DELETE FROM bettor_funded_group_results WHERE group_id LIKE $1",
        "grp:learns-%")
    await conn.execute(
        "DELETE FROM bettor_funded_portfolio_groups WHERE account_id=$1", ACCT)
    await conn.execute("DELETE FROM bettor_funded_models WHERE model_key=$1",
                       KEY)


async def _resolved_decision(conn, *, i, decided_at, middle_occurred,
                             features, model_version="v0-recorder"):
    """ONE RESOLVED GROUP AND THE DECISION THAT WAS TAKEN ON IT.

    THE GROUP IS CLOSED AND BOTH LEGS CARRY A SETTLEMENT PAYOUT, because that is
    where `bettor_funded_model.labelled` reads the label from -- each leg's own
    `settlement.payout_usd`, not a score and not a model. `middle_occurred` is
    encoded by paying BOTH legs or only one.
    """
    gid = "grp:learns-%d" % i
    # OPEN FIRST. 131's trigger refuses a leg joining a CLOSED group -- "it
    # cannot take leg ..." -- and it is right to: attaching exposure to a closed
    # group would make its closure reason a lie. So the legs go on, and the
    # closure comes after, exactly as the lifecycle does it.
    await conn.execute(
        "INSERT INTO bettor_funded_portfolio_groups "
        "(group_id, account_id, venue, event_key, structure, hedge_intent) "
        "VALUES ($1,$2,$3,$4,'INDIRECT_MIDDLE','ACQUIRED')",
        gid, ACCT, VENUE, "ev-learns-%d" % i)
    for role, paid in (("PRIMARY", True),
                       ("HEDGE", bool(middle_occurred))):
        iid = "fpi-learns-%d-%s" % (i, role[:1].lower())
        await conn.execute(
            "INSERT INTO bettor_funded_intents "
            "(intent_id, account_id, venue, venue_class, us_market_slug, "
            " event_key, order_intent, limit_price, quantity, collateral_usd, "
            " effective_digest, state, kind, residual_qty, closed_at, "
            " closed_reason, settlement, portfolio_group_id, leg_role) VALUES "
            "($1,$2,$3,'FUNDED',$4,$5,'ORDER_INTENT_BUY_LONG',0.5,10,5.0,'d',"
            " 'FILLED','ENTRY',0,now(),'SETTLED_BY_THE_VENUE',$6::jsonb,$7,$8)",
            iid, ACCT, VENUE, "slug-learns-%d-%s" % (i, role), "ev-learns-%d" % i,
            json.dumps({"payout_usd": (10.0 if paid else 0.0),
                        "terminal_reading": "CHOSEN_FOR_THIS_PROOF"}),
            gid, role)
    await conn.execute(
        "UPDATE bettor_funded_portfolio_groups SET closed_at=now(), "
        "  closure='BOTH_LEGS_SETTLED' WHERE group_id=$1", gid)
    got = await FL.record_decision(
        conn, decision_id="dec:learns-%d" % i, account_id=ACCT, venue=VENUE,
        fixture="fx-learns-%d" % i, action="ACQUIRE_HEDGE",
        decided_at=decided_at.timestamp(), group_id=gid,
        worst_case_usd=-0.60, model_key=KEY, model_version=model_version,
        features=features, feature_sha=FMD.feature_sha(features),
        predicted={"target": FMD.TARGET, "p_middle": 0.5})
    assert got.get("ok"), got
    return gid


# ════════════════════════════════════════════════════════════════════
# 1 · THE FEATURES ARE PROSPECTIVE, AND THEIR IDENTITY IS RECORDED
# ════════════════════════════════════════════════════════════════════

def test_every_feature_is_known_before_the_fixture_resolves():
    """A FEATURE THAT POSTDATES THE DECISION IS NOT A FEATURE. Each of these is
    read off the classified structure and its two legs at decision time."""
    st = _structure()
    feats = FMD.features_of(st, primary_cost_cents=62, hedge_cost_cents=41,
                            overtime_included=True)
    assert set(feats) == set(FMD.FEATURES)
    # THE WINDOW'S WIDTH IS THE STRUCTURE'S ECONOMICS, and a model given only
    # prices could not tell a one-point middle from a four-point one.
    assert feats["middle_width_points"] == float(len(st.both_win_regions))
    assert feats["cost_cents"] == 103.0
    assert feats["min_payout_cents"] == 100.0
    assert feats["max_payout_cents"] == 200.0


def test_the_feature_sha_changes_when_the_vector_does():
    """THE IDENTITY OF THE EXACT VECTOR SCORED. Without it, a later re-fit and a
    changed input are indistinguishable in the record."""
    a = FMD.features_of(_structure(), primary_cost_cents=62,
                        hedge_cost_cents=41, overtime_included=True)
    b = FMD.features_of(_structure(hedge_cents=42), primary_cost_cents=62,
                        hedge_cost_cents=42, overtime_included=True)
    assert FMD.feature_sha(a) == FMD.feature_sha(dict(a))
    assert FMD.feature_sha(a) != FMD.feature_sha(b)


# ════════════════════════════════════════════════════════════════════
# 2 · THE FIT GOES THROUGH THE EXISTING KERNEL
# ════════════════════════════════════════════════════════════════════

def test_the_fit_uses_the_shipped_kernel_and_carries_its_training_base_rate():
    """NOT A SECOND KERNEL, and the baseline travels WITH the model.

    `metrics.report` refuses to score without a training base rate and refuses
    the evaluation set's own -- a model scored against a rate it could not have
    known is flattered on exactly the split where drift shows up.
    """
    rows, labels = _synthetic()
    got = FMD.fit(rows, labels, decided_at=_trained_before(rows), estimator="RIDGE_LOGISTIC")
    assert got["ok"] is True, got
    assert got["kernel"].startswith("BETTOR_LEARN_KERNEL")
    assert got["params"]["kind"] == "RIDGE_LOGISTIC"
    assert got["train_rows"] == len(labels)
    assert got["train_base_rate"] == pytest.approx(sum(labels) / len(labels))
    # AND IT LEARNED THE STATED RULE: a wider window predicts higher.
    obj = FMD.load(got["params"])
    narrow = dict(rows[0], middle_width_points=1.0, both_win_regions=1.0)
    wide = dict(rows[0], middle_width_points=6.0, both_win_regions=6.0)
    assert obj.predict(wide) > obj.predict(narrow), (
        "the synthetic labels were generated from window width; a fit that "
        "cannot recover that has learned nothing and the rest of this file "
        "would be testing a constant")


def test_an_unknown_estimator_is_refused_not_defaulted():
    rows, labels = _synthetic(40)
    got = FMD.fit(rows, labels, decided_at=_trained_before(rows), estimator="XGBOOST")
    assert got["ok"] is False
    assert got["refusal"] == FMD.R_ESTIMATOR_UNKNOWN
    assert "RIDGE_LOGISTIC" in got["recognised"]


# ════════════════════════════════════════════════════════════════════
# 3 · ONE PREDICTION IS NOT A MEASURE OVER THE FIXTURE
# ════════════════════════════════════════════════════════════════════

def test_the_remaining_mass_is_not_spread_uniformly():
    """THE REFUSAL THAT KEEPS AN ASSUMPTION VISIBLE. The model prices the middle;
    how the rest is distributed is a separate statement about the fixture, and
    making it silently would put a made-up shape inside a capital decision."""
    st = _structure()
    got = FMD.region_probabilities(st, p_middle=0.4, outside_split=None)
    assert got["ok"] is False
    assert got["refusal"] == FMD.R_NO_OUTSIDE_SPLIT
    assert got["outside_regions"]


def test_an_outside_region_left_out_is_refused_not_treated_as_impossible():
    st = _structure()
    partial = dict(_outside_split(st))
    dropped = partial.popitem()[0]
    got = FMD.region_probabilities(st, p_middle=0.4, outside_split=partial)
    assert got["ok"] is False
    assert got["refusal"] == FMD.R_NO_OUTSIDE_SPLIT
    assert dropped in got["unpriced_regions"]


def test_a_stated_split_produces_a_measure_that_sums_to_one():
    st = _structure()
    got = FMD.region_probabilities(st, p_middle=0.40,
                                   outside_split=_outside_split(st))
    assert got["ok"] is True, got
    probs = got["probabilities"]
    assert set(probs) == {r["region"] for r in st.table}
    assert sum(probs.values()) == pytest.approx(1.0)
    # THE MIDDLE'S MASS IS SPLIT ACROSS THE BOTH-WIN CELLS.
    assert sum(probs[r] for r in st.both_win_regions) == pytest.approx(0.40)
    assert probs["margin > 5"] == pytest.approx(0.60)


# ════════════════════════════════════════════════════════════════════
# 4 · THE REGISTRY, ITS BAR, AND ROLLBACK
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_candidate_has_no_authority_over_a_decision():
    """REGISTERED IS NOT APPROVED, and `approved()` says so by name rather than
    returning the newest row."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        rows, labels = _synthetic()
        fitted = FMD.fit(rows, labels, decided_at=_trained_before(rows))
        reg = await FMD.register(
            conn, model_id="mdl:learns-cand", model_version="v1",
            fitted=fitted,
            fit_through=datetime.now(timezone.utc) - timedelta(days=30))
        assert reg["ok"] is True, reg
        assert reg["state"] == FMD.STATE_CANDIDATE
        got = await FMD.approved(conn, model_key=KEY)
        assert got["ok"] is False
        assert got["refusal"] == FMD.R_NO_APPROVED_MODEL
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_promotion_is_refused_without_enough_prospective_labels():
    """THE BAR IS DECLARED IN THE MODULE, not passed to `promote`. A threshold
    chosen at promotion time is a threshold chosen to be cleared."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        fit_through = datetime.now(timezone.utc) - timedelta(days=30)
        rows, labels = _synthetic()
        await FMD.register(conn, model_id="mdl:learns-thin",
                           model_version="v1", fitted=FMD.fit(rows, labels, decided_at=_trained_before(rows)),
                           fit_through=fit_through)
        # TOO FEW RESOLVED DECISIONS AFTER THE FIT WINDOW.
        for i in range(5):
            await _resolved_decision(
                conn, i=i, decided_at=fit_through + timedelta(days=1 + i),
                middle_occurred=bool(i % 2), features=rows[i])
        ev = await FMD.evaluate(conn, model_id="mdl:learns-thin")
        assert ev["ok"] is False
        assert ev["refusal"] == FMD.R_TOO_FEW_LABELS
        # THE BAR IS FIXTURES: five decisions on five fixtures here
        assert ev["n"] == 5 and ev["n_events"] == 5
        assert ev["required_events"] == FMD.MIN_EVALUATION_EVENTS
        promoted = await FMD.promote(conn, model_id="mdl:learns-thin",
                                    approved_by="owner")
        assert promoted["ok"] is False
        assert promoted["refusal"] == FMD.R_NOT_EVALUATED
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_promotion_names_who_approved_it():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        got = await FMD.promote(conn, model_id="mdl:anything", approved_by="  ")
        assert got["ok"] is False
        assert got["refusal"] == FMD.R_NO_APPROVER
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_fitted_models_arithmetic_cannot_be_edited():
    """THE STATE MOVES; THE MODEL DOES NOT. Editing the params would make every
    prediction already recorded against this version a prediction from a model
    that no longer exists."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        rows, labels = _synthetic()
        await FMD.register(conn, model_id="mdl:learns-fixed",
                           model_version="v1", fitted=FMD.fit(rows, labels, decided_at=_trained_before(rows)),
                           fit_through=datetime.now(timezone.utc))
        with pytest.raises(Exception) as e1:
            await conn.execute(
                "UPDATE bettor_funded_models SET params='{}'::jsonb "
                " WHERE model_id=$1", "mdl:learns-fixed")
        assert "not edited" in str(e1.value)
        with pytest.raises(Exception) as e2:
            await conn.execute(
                "UPDATE bettor_funded_models SET fit_through=now() "
                " WHERE model_id=$1", "mdl:learns-fixed")
        assert "fit window" in str(e2.value)
        # AND THE STATE STILL MOVES.
        await conn.execute(
            "UPDATE bettor_funded_models SET state='RETIRED', "
            "  retired_at=now(), retired_reason='test' WHERE model_id=$1",
            "mdl:learns-fixed")
        # BUT NOT BACKWARDS INTO CANDIDACY.
        with pytest.raises(Exception) as e3:
            await conn.execute(
                "UPDATE bettor_funded_models SET state='CANDIDATE' "
                " WHERE model_id=$1", "mdl:learns-fixed")
        assert "does not" in str(e3.value)
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_evaluation_is_prospective_and_the_promotion_is_atomic():
    """THE WHOLE REGISTRY PATH: fit, register, evaluate on rows the fit could not
    have seen, promote, and then promote a better one -- which retires the first
    IN THE SAME TRANSACTION, because the database permits one approved version
    per key and either order committed separately leaves two or none."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        fit_through = datetime.now(timezone.utc) - timedelta(days=30)
        rows, labels = _synthetic()

        # THREE MODELS. A base rate, which knows only the mean; a ridge, which
        # has the window width; and stumps, which fit the same feature harder.
        # Measured on this synthetic set: base 0.673, ridge 0.605, stumps 0.588
        # in log loss -- so the base rate has ZERO skill over its own baseline
        # and stumps beats the ridge by 0.017, above the declared 0.01 bar.
        await FMD.register(conn, model_id="mdl:learns-base",
                           model_version="v1-baserate",
                           fitted=FMD.fit(rows, labels, decided_at=_trained_before(rows), estimator="BASE_RATE"),
                           fit_through=fit_through)
        await FMD.register(conn, model_id="mdl:learns-ridge",
                           model_version="v2-ridge",
                           fitted=FMD.fit(rows, labels, decided_at=_trained_before(rows),
                                          estimator="RIDGE_LOGISTIC"),
                           fit_through=fit_through)
        await FMD.register(conn, model_id="mdl:learns-stumps",
                           model_version="v3-stumps",
                           fitted=FMD.fit(rows, labels, decided_at=_trained_before(rows), estimator="STUMPS"),
                           fit_through=fit_through)

        # ── RESOLVED DECISIONS, ALL AFTER THE FIT WINDOW ────────────
        for i in range(60):
            feats = rows[i]
            await _resolved_decision(
                conn, i=i, decided_at=fit_through + timedelta(hours=1 + i),
                middle_occurred=bool(labels[i]), features=feats)

        ev_base = await FMD.evaluate(conn, model_id="mdl:learns-base")
        assert ev_base["ok"] is True, ev_base
        assert ev_base["evaluation"]["prospective"]["verdict"] == "PROSPECTIVE"
        assert ev_base["evaluation"]["prospective"][
            "rows_the_fit_could_have_seen"] == 0
        assert ev_base["n"] >= FMD.MIN_EVALUATION_ROWS

        ev_ridge = await FMD.evaluate(conn, model_id="mdl:learns-ridge")
        ev_stumps = await FMD.evaluate(conn, model_id="mdl:learns-stumps")
        assert ev_ridge["ok"] is True and ev_stumps["ok"] is True
        assert ev_stumps["evaluation"]["log_loss"] < \
            ev_ridge["evaluation"]["log_loss"] < \
            ev_base["evaluation"]["log_loss"], (
            "the ordering these promotions assert has to be the measured one, "
            "or the promotion tests below assert nothing")

        # ── A BASE RATE IS NOT ADMITTED FOR BEING FIRST ─────────────
        #
        # ITS BASELINE *IS* THE TRAINING BASE RATE, so a model that predicts
        # exactly that improves on it by zero. Refusing it is the bar working: a
        # first model with no skill would otherwise become the incumbent every
        # later candidate is measured against.
        nope = await FMD.promote(conn, model_id="mdl:learns-base",
                                 approved_by="owner@test")
        assert nope["ok"] is False
        assert nope["refusal"] == FMD.R_NO_SKILL
        assert nope["comparison"]["improvement"] == pytest.approx(0.0)
        assert nope["comparison"]["against_what"] == "ITS_OWN_DECLARED_BASELINE"
        assert (await FMD.approved(conn, model_key=KEY))["ok"] is False

        # ── THE RIDGE, AGAINST ITS OWN DECLARED BASELINE ────────────
        p1 = await FMD.promote(conn, model_id="mdl:learns-ridge",
                               approved_by="owner@test")
        assert p1["ok"] is True, p1
        assert p1["comparison"]["against_what"] == "ITS_OWN_DECLARED_BASELINE"
        assert p1["retired"] is None
        assert (await FMD.approved(conn, model_key=KEY))[
            "model"]["model_version"] == "v2-ridge"

        # ── THEN STUMPS, WHICH MUST BEAT THE INCUMBENT ──────────────
        p2 = await FMD.promote(conn, model_id="mdl:learns-stumps",
                               approved_by="owner@test")
        assert p2["ok"] is True, p2
        assert p2["comparison"]["against_what"] == "THE_INCUMBENT"
        assert p2["comparison"]["improvement"] >= FMD.MIN_SKILL_MARGIN
        assert p2["retired"] == "mdl:learns-ridge"
        # ── EXACTLY ONE APPROVED VERSION ────────────────────────────
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_models "
            " WHERE model_key=$1 AND state='APPROVED'", KEY) == 1
        retired = dict(await conn.fetchrow(
            "SELECT * FROM bettor_funded_models WHERE model_id=$1",
            "mdl:learns-ridge"))
        assert retired["state"] == "RETIRED"
        assert retired["superseded_by"] == "mdl:learns-stumps"
        assert retired["retired_reason"] == "SUPERSEDED_BY_A_PROMOTED_CANDIDATE"

        # ── AND THE ROLLBACK ────────────────────────────────────────
        back = await FMD.rollback(
            conn, to_model_id="mdl:learns-ridge",
            reason="stumps mispriced two live fixtures")
        assert back["ok"] is True, back
        assert back["rolled_back"] is True
        assert back["pulled"] == "mdl:learns-stumps"
        assert (await FMD.approved(conn, model_key=KEY))[
            "model"]["model_version"] == "v2-ridge"
        pulled = dict(await conn.fetchrow(
            "SELECT * FROM bettor_funded_models WHERE model_id=$1",
            "mdl:learns-stumps"))
        assert pulled["state"] == "RETIRED"
        assert pulled["retired_reason"].startswith("ROLLED_BACK:")
        # THE RESTORED ROW KEEPS ITS ORIGINAL APPROVAL RECORD.
        restored = back["restored"]
        assert restored["approved_by"] == "owner@test"
        assert restored["approved_at"] is not None

        # ── AND A ROLLBACK CANNOT SIDESTEP THE PROMOTION BAR ────────
        #
        # The base rate was REFUSED above, so it has never been approved -- and
        # a rollback to it would be a promotion without the bar.
        bad = await FMD.rollback(conn, to_model_id="mdl:learns-base",
                                 reason="trying to skip the bar")
        assert bad["ok"] is False
        assert bad["refusal"] == FMD.R_NOTHING_TO_ROLL_BACK_TO
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_contaminated_evaluation_is_named_and_blocks_promotion():
    """THE ONE CHECK THAT MATTERS MOST. A model scored on rows it was fitted to
    measures its memory, and that is how a model with no skill gets promoted.

    The fit window is moved AFTER the decisions, so every labelled row is one the
    fit could have seen. The SQL filter then returns nothing, and the refusal is
    the thin-labels one -- which is the correct outcome and is asserted here
    rather than assumed, because the alternative (a scored contaminated set) is
    the failure this design exists to prevent.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        decided_from = datetime.now(timezone.utc) - timedelta(days=30)
        rows, labels = _synthetic()
        for i in range(60):
            await _resolved_decision(
                conn, i=i, decided_at=decided_from + timedelta(hours=i),
                middle_occurred=bool(labels[i]), features=rows[i])
        # FIT WINDOW AFTER EVERY DECISION: nothing is prospective.
        await FMD.register(
            conn, model_id="mdl:learns-leak", model_version="v1-leak",
            fitted=FMD.fit(rows, labels, decided_at=_trained_before(rows)),
            fit_through=datetime.now(timezone.utc) + timedelta(days=1))
        ev = await FMD.evaluate(conn, model_id="mdl:learns-leak")
        assert ev["ok"] is False
        assert ev["refusal"] == FMD.R_TOO_FEW_LABELS
        assert ev["n"] == 0, (
            "every decision predates this model's fit window, so none of them "
            "can measure it")
        got = await FMD.promote(conn, model_id="mdl:learns-leak",
                                approved_by="owner@test")
        assert got["ok"] is False
        assert got["refusal"] == FMD.R_NOT_EVALUATED
    finally:
        await _clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 5 · THE DEMONSTRATION: AN APPROVED MODEL CHANGES A LATER DECISION
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_an_approved_model_changes_a_later_decision():
    """THE CLAIM THIS WHOLE FILE EXISTS FOR.

    ONE held position, ONE structure, ONE set of costs, ONE fee, ONE depth
    reading, ONE set of HOLD / DIRECT_EXIT / REDUCE alternatives -- and TWO
    approved models. The pessimistic model's estimate makes HOLD the best
    expected value; the optimistic one's makes the indirect acquisition win.

    NOTHING ELSE MOVES between the two calls. The only difference is which
    version the registry says is approved, and the decision changes. That is the
    connection the criticism said was missing.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        st = _structure()
        admitted = {"condition_id": "0xhedge", "taxonomy": st.taxonomy,
                    "units": st.units, "structure": st.to_dict()}
        model_inputs = {"primary_cost_cents": 62, "hedge_cost_cents": 41,
                        "overtime_included": True,
                        "outside_split": _outside_split(st)}
        hold_ranking = {
            "version": "MGMT_SELECT_SHAPE", "not_rankable": [],
            "candidates": [
                {"action": "HOLD", "qty": 10, "value_usd": 1.80,
                 "expected_net_usd": 1.80, "downside_usd": -6.20,
                 "incremental_capital_usd": 0.0, "capital_duration_h": 26.0,
                 "evidence_quality": FD.EVIDENCE_EXTERNAL_LABELLED,
                 "execution_secured": True},
                {"action": "DIRECT_EXIT", "qty": 10, "value_usd": 0.40,
                 "expected_net_usd": 0.40, "downside_usd": 0.40,
                 "incremental_capital_usd": 0.0, "capital_duration_h": 0.0,
                 "evidence_quality": FD.EVIDENCE_VENUE_IMPLIED,
                 "execution_secured": False},
                {"action": "REDUCE", "qty": 5, "value_usd": 1.10,
                 "expected_net_usd": 1.10, "downside_usd": -3.10,
                 "incremental_capital_usd": 0.0, "capital_duration_h": 26.0,
                 "evidence_quality": FD.EVIDENCE_EXTERNAL_LABELLED,
                 "execution_secured": False},
            ]}
        common = dict(
            account_id=ACCT, venue=VENUE, fixture=IS.BEARS_PANTHERS_FIXTURE,
            group_id=None, hold_ranking=hold_ranking, admitted=admitted,
            fee_usd=0.30,
            depth=FIP.depth_supports(wanted_qty=10, depth_qty_at_price=25),
            incremental=FIP.incremental_capital_usd(
                hedge_qty=10, hedge_price=0.41, hedge_fee_usd=0.30),
            capital_duration_h=26.0, use_approved_model=True,
            model_inputs=model_inputs)

        # ── 0 · WITH NOTHING APPROVED, THE LANE DECLINES THE HEDGE ──
        #
        # NOT a fallback to some other number: the registry is the only permitted
        # source, so with no approved version there is no estimate and the
        # indirect candidate is refused by name.
        none_yet = await PC.decide_and_record(
            conn, decision_id="dec:learns-demo-0", **common)
        assert none_yet["prediction"]["refusal"] == FMD.R_NO_APPROVED_MODEL
        assert none_yet["region_probabilities_came_from"] == "NOTHING_APPROVED"
        blocked = {b["action"]: b for b in
                   none_yet["decision"]["not_rankable"]}
        assert blocked[FD.ACTION_ACQUIRE_INDIRECT_HEDGE]["blocker"] == \
            FD.R_NO_REGION_PROBABILITIES
        assert none_yet["action"] == "HOLD"

        # ── 1 · THE PESSIMISTIC MODEL, APPROVED ─────────────────────
        #
        # A BASE_RATE fitted on labels that almost never hit: it predicts a low
        # p(middle) for every input, which is exactly what a pessimistic estimate
        # is. Registered and approved directly here -- the promotion BAR has its
        # own test above, and re-running it would make this test about promotion
        # rather than about the decision changing.
        rows, _ = _synthetic(50)
        low = FMD.fit(rows, [0.0] * 45 + [1.0] * 5, estimator="BASE_RATE",
                      decided_at=_trained_before(rows))
        await FMD.register(conn, model_id="mdl:demo-low",
                           model_version="v1-pessimistic", fitted=low,
                           fit_through=datetime.now(timezone.utc))
        await conn.execute(
            "UPDATE bettor_funded_models SET state='APPROVED', "
            "  approved_at=now(), approved_by='owner@test', "
            "  evaluation='{\"log_loss\": 0.5}'::jsonb WHERE model_id=$1",
            "mdl:demo-low")

        first = await PC.decide_and_record(
            conn, decision_id="dec:learns-demo-1", **common)
        assert first["prediction"]["ok"] is True, first["prediction"]
        assert first["prediction"]["model_version"] == "v1-pessimistic"
        p_low = first["prediction"]["predicted"]["p_middle"]
        assert p_low < 0.2, p_low
        assert first["action"] == "HOLD", first["decision"]["selection_reason"]

        # ── 2 · THE OPTIMISTIC MODEL, APPROVED IN ITS PLACE ─────────
        high = FMD.fit(rows, [1.0] * 40 + [0.0] * 10, estimator="BASE_RATE",
                       decided_at=_trained_before(rows))
        await FMD.register(conn, model_id="mdl:demo-high",
                           model_version="v2-optimistic", fitted=high,
                           fit_through=datetime.now(timezone.utc))
        async with conn.transaction():
            await conn.execute(
                "UPDATE bettor_funded_models SET state='RETIRED', "
                "  retired_at=now(), retired_reason='SUPERSEDED_IN_THIS_PROOF', "
                "  superseded_by='mdl:demo-high' WHERE model_id=$1",
                "mdl:demo-low")
            await conn.execute(
                "UPDATE bettor_funded_models SET state='APPROVED', "
                "  approved_at=now(), approved_by='owner@test', "
                "  evaluation='{\"log_loss\": 0.4}'::jsonb WHERE model_id=$1",
                "mdl:demo-high")

        second = await PC.decide_and_record(
            conn, decision_id="dec:learns-demo-2", **common)
        assert second["prediction"]["model_version"] == "v2-optimistic"
        p_high = second["prediction"]["predicted"]["p_middle"]
        assert p_high > 0.6, p_high

        # ══ THE DEMONSTRATION ═══════════════════════════════════════
        assert second["action"] == PC.ACTION_ACQUIRE, second["decision"][
            "selection_reason"]
        assert first["action"] != second["action"], (
            "the approved model changed and the decision did not. That is the "
            "whole claim of this file")
        # AND NOTHING ELSE MOVED. Every other input was the same object.
        assert first["decision"]["hold_is_priced"] is True
        assert second["decision"]["hold_is_priced"] is True
        assert first["decision"]["policy"] == second["decision"]["policy"] == \
            "EXPECTED_NET_VALUE"
        hold_1 = [c for c in first["decision"]["candidates"]
                  if c["action"] == "HOLD"][0]
        hold_2 = [c for c in second["decision"]["candidates"]
                  if c["action"] == "HOLD"][0]
        assert hold_1["expected_net_usd"] == hold_2["expected_net_usd"] == 1.80
        acq_1 = [c for c in first["decision"]["candidates"]
                 if c["action"] == FD.ACTION_ACQUIRE_INDIRECT_HEDGE][0]
        acq_2 = [c for c in second["decision"]["candidates"]
                 if c["action"] == FD.ACTION_ACQUIRE_INDIRECT_HEDGE][0]
        assert acq_1["cost_usd"] == acq_2["cost_usd"], (
            "the structure and its price are identical between the two runs")
        assert acq_2["expected_net_usd"] > acq_1["expected_net_usd"]

        # ── AND BOTH DECISIONS RECORD WHAT THEY WERE PREDICTED FROM ──
        for did, version in (("dec:learns-demo-1", "v1-pessimistic"),
                             ("dec:learns-demo-2", "v2-optimistic")):
            row = dict(await conn.fetchrow(
                "SELECT model_key, model_version, feature_sha, features, "
                "       predicted FROM bettor_funded_decisions "
                " WHERE decision_id=$1", did))
            assert row["model_key"] == KEY
            assert row["model_version"] == version
            assert row["feature_sha"]
            feats = row["features"]
            if isinstance(feats, str):
                feats = json.loads(feats)
            assert set(feats) == set(FMD.FEATURES)
            assert FMD.feature_sha(feats) == row["feature_sha"], (
                "the recorded sha must be the sha of the recorded vector, or "
                "neither can be checked against a later re-fit")
        # ── AND THE DECISION FOR THE UNMODELLED RUN RECORDS NO MODEL ──
        row0 = dict(await conn.fetchrow(
            "SELECT model_key, model_version, features FROM "
            " bettor_funded_decisions WHERE decision_id=$1",
            "dec:learns-demo-0"))
        assert row0["model_version"] is None and row0["features"] is None, (
            "a decision taken with no approved model must record no model, so "
            "`labelled` excludes it rather than scoring it against whatever "
            "the model says today")
    finally:
        await _clean(conn)
        await conn.close()


def test_the_module_says_what_a_good_score_cannot_say():
    d = FMD.describe()
    assert "would have made money" in d["what_a_good_score_does_not_say"]
    assert d["promotion_bar"]["metric"] == "log_loss"
    assert "measuring its memory" in d["prospective_rule"]


# ════════════════════════════════════════════════════════════════════
# 6 · THE SCHEDULED PASS AND THE OPERATOR SURFACE
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_operator_can_see_which_model_is_deciding():
    """AN ACTION WITHOUT ITS MODEL VERSION IS UNREADABLE.

    "Acquire the hedge" means something different under a model approved this
    morning than under the one it replaced. And with NOTHING approved the panel
    must say so, because that is the state in which this lane declines every
    indirect acquisition -- an omission would look like an ordinary quiet lane.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        view = await PC.operator_view(conn, account_id=ACCT)
        assert view["ok"] is True, view
        assert view["deciding_model"]["refusal"] == FMD.R_NO_APPROVED_MODEL
        assert "declined" in view["deciding_model"]["consequence"]

        rows, labels = _synthetic(50)
        await FMD.register(conn, model_id="mdl:view", model_version="v9-view",
                           fitted=FMD.fit(rows, labels, decided_at=_trained_before(rows), estimator="BASE_RATE"),
                           fit_through=datetime.now(timezone.utc))
        # A CANDIDATE IS STILL NOT THE DECIDING MODEL.
        mid = await PC.operator_view(conn, account_id=ACCT)
        assert mid["deciding_model"]["refusal"] == FMD.R_NO_APPROVED_MODEL
        await conn.execute(
            "UPDATE bettor_funded_models SET state='APPROVED', "
            "  approved_at=now(), approved_by='owner@test', "
            "  evaluation='{}'::jsonb WHERE model_id=$1", "mdl:view")
        after = await PC.operator_view(conn, account_id=ACCT)
        assert after["deciding_model"]["model_version"] == "v9-view"
        assert after["deciding_model"]["approved_by"] == "owner@test"
        assert after["deciding_model"]["estimator"] == "BASE_RATE"
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_scheduled_pass_forwards_the_model_request_to_the_decision():
    """THE SUPPLIER DECIDES WHETHER TO USE THE MODEL, AND THE STEP SAYS WHICH.

    A lane with no approved model has to be able to run the rest of the pass,
    and a lane with one must not have its estimate silently replaced by
    whatever a supplier computed itself. Both states are visible in the step's
    own `region_probabilities_came_from`, so a reader never has to infer it.
    """
    import inspect

    src = inspect.getsource(PC.pass_once)
    assert "use_approved_model" in src
    assert 'facts.get("model_inputs")' in src
    # THE LABELS MOVED ONE CALL DOWN. `decide_and_record` now prices each
    # candidate through `_price_indirect`, which is where the registry is asked
    # and where the answer is labelled. Both functions are read, so this
    # follows the behaviour rather than one function's text; the BEHAVIOUR is
    # asserted by `test_an_unpriced_outside_region_is_not_reported_as_an_empty_
    # registry`, which reads the label off a real pass.
    step_src = (inspect.getsource(PC.decide_and_record)
                + inspect.getsource(PC._price_indirect))
    assert "NOTHING_APPROVED" in step_src
    assert "APPROVED_MODEL:%s" in step_src


# ════════════════════════════════════════════════════════════════════
# 7 · THE LABEL ONLY EXISTS FOR A TWO-ROLE GROUP
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_single_leg_group_is_not_labelled_as_a_middle_that_occurred():
    """THE DEFECT THIS PINS, CAUGHT BY RE-READING THE QUERY RATHER THAN BY A
    FAILING TEST.

    `bool_and(paid)` over a SINGLE-LEG group is just "did that one leg pay". A
    moneyline that won would have been labelled `middle_occurred = true` for a
    structure never acquired -- so the model would learn the PRIMARY LEG'S WIN
    RATE while every consumer reads its output as p(both legs pay).

    THE ERROR IS IN THE DIRECTION THAT BUYS HEDGES: the primary leg wins more
    often than the middle lands, so the mislabel inflates p(middle), inflates
    `expected_net_usd` on the acquisition, and makes the lane pay for structures
    it should have declined.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        rows, _ = _synthetic(10)
        # ── A ONE-LEG GROUP WHOSE ONLY LEG PAID ─────────────────────
        gid = "grp:learns-solo"
        await conn.execute(
            "INSERT INTO bettor_funded_portfolio_groups "
            "(group_id, account_id, venue, event_key, structure, hedge_intent) "
            "VALUES ($1,$2,$3,'ev-solo','SINGLE_LEG','NOT_APPLICABLE')",
            gid, ACCT, VENUE)
        await conn.execute(
            "INSERT INTO bettor_funded_intents "
            "(intent_id, account_id, venue, venue_class, us_market_slug, "
            " event_key, order_intent, limit_price, quantity, collateral_usd, "
            " effective_digest, state, kind, residual_qty, closed_at, "
            " closed_reason, settlement, portfolio_group_id, leg_role) VALUES "
            "('fpi-solo',$1,$2,'FUNDED','slug-solo','ev-solo',"
            " 'ORDER_INTENT_BUY_LONG',0.5,10,5.0,'d','FILLED','ENTRY',0,now(),"
            " 'SETTLED_BY_THE_VENUE',$3::jsonb,$4,'PRIMARY')",
            ACCT, VENUE, json.dumps({"payout_usd": 10.0}), gid)
        await conn.execute(
            "UPDATE bettor_funded_portfolio_groups SET closed_at=now(), "
            "  closure='ALL_LEGS_EXITED' WHERE group_id=$1", gid)
        got = await FL.record_decision(
            conn, decision_id="dec:learns-solo", account_id=ACCT, venue=VENUE,
            fixture="fx-solo", action="HOLD", decided_at=time.time(),
            group_id=gid, model_key=KEY, model_version="v0",
            features=rows[0], feature_sha=FMD.feature_sha(rows[0]),
            predicted={"target": FMD.TARGET, "p_middle": 0.5})
        assert got.get("ok"), got

        lab = await FMD.labelled(conn, account_id=ACCT)
        assert lab["ok"] is True, lab
        assert "dec:learns-solo" not in (lab.get("decision_ids") or []), (
            "a one-leg group has no both-win region, so 'did both legs pay' is "
            "not a question about it and it must not become a label")
        assert lab["n"] == 0
        assert "primary leg's win rate" in lab["only_two_role_groups"]

        # ── AND A TWO-ROLE GROUP IS LABELLED ────────────────────────
        await _resolved_decision(conn, i=99, decided_at=datetime.now(
            timezone.utc), middle_occurred=True, features=rows[1])
        both = await FMD.labelled(conn, account_id=ACCT)
        assert both["decision_ids"] == ["dec:learns-99"], both
        assert both["labels"] == [1.0]
    finally:
        await conn.execute("DELETE FROM bettor_funded_intents "
                          " WHERE intent_id='fpi-solo'")
        await _clean(conn)
        await conn.execute(
            "DELETE FROM bettor_funded_portfolio_groups WHERE group_id=$1",
            "grp:learns-solo")
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_group_with_one_leg_unsettled_is_not_labelled_either():
    """TWO MISTAKES IN ONE CONDITION, and the second was worse.

    `bool_or(settled)` was the first: a group with one leg settled and one still
    open would have been labelled from a fixture that had not finished for the
    other leg. `bool_and` fixes that.

    AND `settlement IS NOT NULL` CONSTRAINED NOTHING. The column is NOT NULL
    with a `'{}'::jsonb` default, so it was true for every row -- including one
    whose fixture had never been read. It read like a settlement check. The
    condition is now `settlement ? 'payout_usd'`, which is the same key the
    label itself reads, so a row cannot pass the check and then contribute a
    coalesce'd zero as though the leg had lost.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        rows, _ = _synthetic(4)
        gid = "grp:learns-half"
        await conn.execute(
            "INSERT INTO bettor_funded_portfolio_groups "
            "(group_id, account_id, venue, event_key, structure, hedge_intent) "
            "VALUES ($1,$2,$3,'ev-half','INDIRECT_MIDDLE','ACQUIRED')",
            gid, ACCT, VENUE)
        for role, settled in (("PRIMARY", True), ("HEDGE", False)):
            await conn.execute(
                "INSERT INTO bettor_funded_intents "
                "(intent_id, account_id, venue, venue_class, us_market_slug, "
                " event_key, order_intent, limit_price, quantity, "
                " collateral_usd, effective_digest, state, kind, residual_qty, "
                " closed_at, closed_reason, settlement, portfolio_group_id, "
                " leg_role) VALUES "
                "($1,$2,$3,'FUNDED',$4,'ev-half','ORDER_INTENT_BUY_LONG',"
                " 0.5,10,5.0,'d','FILLED','ENTRY',$5,$6,$7,$8::jsonb,$9,$10)",
                "fpi-half-%s" % role[:1].lower(), ACCT, VENUE,
                "slug-half-%s" % role,
                0 if settled else 10,
                None if not settled else __import__("datetime").datetime.now(
                    timezone.utc),
                "SETTLED_BY_THE_VENUE" if settled else None,
                # NOT None: the column is NOT NULL with a '{}' default, which
                # is how `settlement IS NOT NULL` came to be a vacuous test in
                # the label query. An unsettled leg carries the empty record,
                # exactly as production writes it.
                json.dumps({"payout_usd": 10.0} if settled else {}),
                gid, role)
        got = await FL.record_decision(
            conn, decision_id="dec:learns-half", account_id=ACCT, venue=VENUE,
            fixture="fx-half", action="ACQUIRE_HEDGE", decided_at=time.time(),
            group_id=gid, model_key=KEY, model_version="v0",
            features=rows[0], feature_sha=FMD.feature_sha(rows[0]),
            predicted={"target": FMD.TARGET, "p_middle": 0.5})
        assert got.get("ok"), got
        lab = await FMD.labelled(conn, account_id=ACCT)
        assert lab["n"] == 0, lab
        assert "dec:learns-half" not in (lab.get("decision_ids") or [])
    finally:
        await conn.execute("DELETE FROM bettor_funded_intents "
                          " WHERE intent_id LIKE 'fpi-half-%'")
        await _clean(conn)
        await conn.execute(
            "DELETE FROM bettor_funded_portfolio_groups WHERE group_id=$1",
            "grp:learns-half")
        await conn.close()

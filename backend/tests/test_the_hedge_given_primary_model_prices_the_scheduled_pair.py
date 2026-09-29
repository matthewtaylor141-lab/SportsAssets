"""THE CONDITIONAL MODEL, THE VOID RATE AND THE SCHEDULED PAIR RANKING.

ENGINEERING PROOF ON SUBSTITUTED TRANSPORT. The settlement, prose and book
reads are substituted; the catalogue, the suppliers, the classifier, the
recorder, the labeller, the registry and the pair cycle are the deployed ones.

    EVERY OBSERVATION BELOW IS SYNTHETIC, generated from a stated rule so a
    model CAN learn something. It is labelled synthetic in its price basis,
    it proves the machinery, and it is never a production substitute.
    NO VENUE IS CONTACTED. NO ORDER IS SENT: the shipped submission switches
    stay off, and the tests assert it.

What is pinned:

  * KEY_HEDGE_GIVEN_PRIMARY's records come from observations whose stored
    structures are REAL `classify` outputs on real `Leg`s -- WIN rows (a
    middle, binary given WIN) and LOSE rows (spread A-2.5 + moneyline B,
    binary given LOSE) -- with every exclusion counted by name;
  * generate -> CANDIDATE; prospective, event-balanced evaluation; `promote`
    only with a named approver on that evidence; a label correction
    withdraws the approval; funded records are refused;
  * the void rate counts FIXTURES, refuses below the bar, and reads each label
    as it stood at `through`;
  * `predict_distribution` refuses with the key named when nothing is
    approved; refuses, with a model approved, while the primary probability's
    source has no current passing calibration or the probability is outside
    the source's declared support (the entry lane's own gates on an external
    valuation that would commit new money); and otherwise returns class
    probabilities with their basis;
  * ACCEPTANCE: a scheduled pair pass through the PRODUCTION suppliers ranks
    ACQUIRE_INDIRECT_HEDGE, HOLD and DIRECT_EXIT in one decision (REDUCE is in
    it too, as not rankable: management deferred an executable plan for one
    exit action), records the distribution basis on the ledger row; with the
    source's calibration row removed it reports the acquisition not rankable
    on R_PRIMARY_SOURCE_NOT_CALIBRATED; and with no approved conditional model
    it reports the indirect candidate not rankable on THAT key's
    R_NO_APPROVED_MODEL, not on the outside split. The calibration row is
    SYNTHETIC, for a TEST source version, and removed afterwards.
"""
from __future__ import annotations

import contextlib
import dataclasses
import functools
import json
import os
import time

import asyncpg
import pytest

from sportsassets import bettor_funded_decision as FD
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_hedge_supply as HS
from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_funded_model as FMD
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_indirect_structures as IS
from sportsassets import bettor_live_read as LR
from sportsassets import bettor_mgmt_select as MS
from sportsassets import bettor_pair_observations as PO
from sportsassets import bettor_payout_states as PS
from sportsassets.workers import ext_pinnacle_loop as LOOP
from tests import test_indirect_structures as T
from tests import test_the_hedge_beats_hold_through_the_real_suppliers as HW
from tests import test_the_pairing_model_bootstraps_from_non_funded_observations as BOOT

DSN = os.environ.get("RN1X_TEST_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="needs a migrated database")

LABEL = "SYNTHETIC OBSERVATION, GENERATED FROM A STATED RULE -- NOT EVIDENCE"
LONG = PO.LONG
KEY = FMD.KEY_HEDGE_GIVEN_PRIMARY
VOID_PX = "VOID"


@contextlib.asynccontextmanager
async def _conn():
    c = await asyncpg.connect(DSN)
    try:
        yield c
    finally:
        await c.close()


#: The primary probability's source version in the acceptance pass: a TEST
#: source, so the synthetic calibration row seeded for it cannot open the
#: gate for any real source.
CAL_SOURCE = "HEDGE_ACCEPTANCE_SYNTHETIC_SOURCE"


async def _seed_calibration(conn):
    """ONE PASSING CALIBRATION ROW for the test source, read by the worker's
    own `source_calibration` -- SYNTHETIC, and labelled so in its provenance."""
    from sportsassets import bettor_source_calibration as CAL
    await conn.execute("DELETE FROM external_source_calibration "
                       " WHERE source_version=$1", CAL_SOURCE)
    await conn.execute(
        "INSERT INTO external_source_calibration (source_version, measured_at,"
        " window_start, window_end, sample_size, metric, score, tolerance, "
        " within_tolerance, measured_by, provenance) VALUES ($1, now() - "
        " interval '1 hour', now() - interval '90 days', now(), $2, 'BRIER', "
        " 0.21, 0.24, TRUE, 'HEDGE_ACCEPTANCE_TEST', $3::jsonb)",
        CAL_SOURCE, int(CAL.MIN_RESOLVED_EVENTS) + 1,
        json.dumps({"evaluator": CAL.VERSION, "supplied_by": "TEST_FIXTURE",
                    "synthetic": LABEL}))


async def _drop_models(conn):
    await conn.execute("DELETE FROM external_source_calibration "
                       " WHERE source_version=$1", CAL_SOURCE)
    await conn.execute("DELETE FROM bettor_funded_models WHERE model_key=$1 "
                       "   OR model_id LIKE 'fmc:%:obs-%'", KEY)
    # THE RELEASED HEDGE RESERVATIONS the acceptance pass leaves (its
    # acquisition reaches the connection, is refused there, and releases the
    # claim) carry no intent id, so the harness's own clean does not reach
    # them. They are this module's rows and it removes them.
    await conn.execute(
        "DELETE FROM bettor_funded_leg_reservations WHERE group_id IN ("
        " SELECT group_id FROM bettor_funded_portfolio_groups "
        "  WHERE account_id=$1)", HW.ACCT)


async def _clean(conn):
    if not await PO.has_schema(conn):
        pytest.skip("migration 140 is not in this database")
    await BOOT._purge(conn)
    await _drop_models(conn)
    await HW._clean(conn)


@pytest.fixture(autouse=True)
async def _leave_nothing_behind():
    yield
    if DSN:
        async with _conn() as c:
            if await PO.has_schema(c):
                await BOOT._purge(c)
                await _drop_models(c)
                await HW._clean(c)


class _Leg:
    def __init__(self, cid):
        self.condition_id = cid


# ── THE REAL STRUCTURES THE OBSERVATIONS STORE ───────────────────────

def _middle() -> dict:
    """Bears ML + Panthers +4.5: binary given WIN, structural given LOSE."""
    a = dataclasses.replace(IS.BEARS_MONEYLINE, cost_cents_per_unit=62)
    b = dataclasses.replace(IS.PANTHERS_PLUS_4_5, cost_cents_per_unit=41)
    st = IS.classify(a, b, sport_permits_tie=False)
    assert st.taxonomy == IS.MIDDLE, st.why
    return st.to_dict()


def _lose_binary() -> dict:
    """Spread A-2.5 (primary) + moneyline B (hedge): binary given LOSE."""
    st = IS.classify(T.spread("A", "-5/2", cost=58), T.ml("B", cost=35),
                     sport_permits_tie=False)
    assert st.taxonomy != IS.UNESTABLISHABLE, st.missing_facts
    cls = PS.payout_classes(st)
    lose = [c for c in cls["classes"] if c["primary"]["outcome"] == PS.LOSE]
    assert sorted(c["hedge"]["cents"] for c in lose) == [0, 100]
    return st.to_dict()


def _row(st, pp, hp, *, pc=55, hc=30):
    return {"st": st, "pp": pp, "hp": hp, "pc": pc, "hc": hc}


async def _cohort(conn, rows, *, prefix, first_at, step=60.0):
    """One observation per row, each on its OWN fixture, via the production
    recorder. Returns the substituted settlements keyed by slug."""
    prices = {}
    for i, r in enumerate(rows):
        p_slug, h_slug = "%s-p-%d" % (prefix, i), "%s-h-%d" % (prefix, i)
        await PO.record(
            conn, fixture="%s-fx-%d" % (prefix, i),
            admitted={"structure": r["st"], "taxonomy": r["st"].get(
                "taxonomy"), "condition_id": h_slug + "#" + LONG},
            held_leg=_Leg(p_slug + "#" + LONG), primary_slug=p_slug,
            primary_side=LONG, hedge_slug=h_slug, hedge_side=LONG,
            primary_cost_cents=r["pc"], hedge_cost_cents=r["hc"],
            overtime_included=True, price_basis={"synthetic": LABEL},
            at=first_at + step * i)
        prices[p_slug] = r["pp"]
        prices[h_slug] = r["hp"]
    return prices


def _reader(prices):
    def read(slug):
        p = prices.get(slug)
        if p is None:
            return {"status": LR.PENDING, "settlement_price": None}
        if p == VOID_PX:
            return {"status": PO.VOID, "settlement_price": None}
        return {"status": LR.RESOLVED, "settlement_price": p,
                "settlement_price_raw": str(p), "settled_at": "synthetic"}
    return read


async def _label(conn, prices, *, at):
    got = await PO.label_pending(conn, now=at, limit=10_000, recheck=10_000,
                                 settlement_reader=_reader(prices))
    assert got["ok"], got
    return got


def _training_rows(mid, loseb):
    """THE STATED RULE: given WIN on the middle the hedge wins 3 in 10; given
    LOSE on the spread/moneyline pair it wins 8 in 10. Plus one of each
    exclusion the label rule must count, and three declared voids."""
    rows = [_row(mid, 1.0, 1.0 if (i % 10) < 3 else 0.0, pc=62, hc=41)
            for i in range(30)]
    rows += [_row(loseb, 0.0, 1.0 if (i % 10) < 8 else 0.0, pc=58, hc=35)
             for i in range(30)]
    rows += [_row(mid, 0.0, 1.0, pc=62, hc=41) for _ in range(3)]  # structural
    rows += [_row(mid, 0.5, 1.0, pc=62, hc=41) for _ in range(2)]  # push
    rows += [_row({"table": [], "both_win_regions": ["r"], "cost_cents": 80,
                   "min_payout_cents": 100, "max_payout_cents": 200},
                  1.0, 1.0) for _ in range(2)]                     # no table
    rows += [_row(mid, 1.0, 0.5, pc=62, hc=41)]                     # hedge push
    rows += [_row(mid, 1.0, VOID_PX, pc=62, hc=41) for _ in range(3)]  # void
    return rows


def _eval_rows(mid, loseb):
    rows = [_row(mid, 1.0, 1.0 if (i % 10) < 3 else 0.0, pc=62, hc=41)
            for i in range(23)]
    rows += [_row(loseb, 0.0, 1.0 if (i % 10) < 8 else 0.0, pc=58, hc=35)
             for i in range(22)]
    return rows


async def _train_and_generate(conn, rows, *, prefix="cond"):
    t0 = time.time() - 3 * 86400
    prices = await _cohort(conn, rows, prefix=prefix + "tr", first_at=t0)
    await _label(conn, prices, at=t0 + 86400)
    gen = await FMD.generate_candidate(conn, now=time.time(),
                                       source=FMD.SOURCE_OBSERVATIONS,
                                       model_key=KEY)
    return gen, prices, t0


async def _prospective(conn, rows, *, prefix="cond"):
    after = time.time() + 60
    prices = await _cohort(conn, rows, prefix=prefix + "ev", first_at=after)
    await _label(conn, prices, at=after + len(rows) * 60 + 60)


# ═════════════════════════════════════════════════════════════════════
# 1 · THE CONDITIONAL, THROUGH THE SAME REGISTRY BAR
# ═════════════════════════════════════════════════════════════════════

async def test_the_conditional_is_learned_from_real_structures_and_promoted_by_the_bar():
    async with _conn() as conn:
        await _clean(conn)
        mid, loseb = _middle(), _lose_binary()
        gen, train_px, t0 = await _train_and_generate(
            conn, _training_rows(mid, loseb))
        assert gen["ok"] and gen["generated"] is True, gen
        mid_id = gen["model_id"]
        assert mid_id.startswith("fmc:%s:obs-" % KEY), mid_id
        row = FMD._row(await conn.fetchrow(
            "SELECT * FROM bettor_funded_models WHERE model_id=$1", mid_id))
        assert row["state"] == FMD.STATE_CANDIDATE
        assert row["model_key"] == KEY
        assert list(row["features"]) == list(FMD.FEATURES_HEDGE_GIVEN_PRIMARY)
        prov = row["training_provenance"]
        assert prov["kind"] == FMD.PROVENANCE_RECORDS
        assert prov["source"] == FMD.SOURCE_OBSERVATIONS
        assert prov["model_key"] == KEY
        assert prov["target"] == FMD.TARGET_HEDGE_GIVEN_PRIMARY
        assert prov["feature_schema_sha"] == \
            FMD.FEATURE_SCHEMA_SHA_HEDGE_GIVEN_PRIMARY
        # SIXTY INCLUDED FIXTURES, WIN AND LOSE ROWS, AND EVERY EXCLUSION
        # COUNTED BY NAME (the three voids are not labels at all).
        assert prov["n_events"] == 60 and prov["n_rows"] == 60
        assert prov["label_exclusions"] == {
            PO.X_C_STRUCTURAL: 3, PO.X_C_PRIMARY_PARTIAL: 2,
            "%s:%s" % (PO.X_C_CLASSES_REFUSED, PS.R_NO_TABLE): 2,
            PO.X_C_HEDGE_PUSHED: 1}, prov["label_exclusions"]
        lab = await FMD.labelled(conn, model_key=KEY,
                                 source=FMD.SOURCE_OBSERVATIONS)
        assert lab["by_primary_outcome"] == {PS.WIN: 30, PS.LOSE: 30}, lab
        assert {r["primary_won"] for r in lab["rows"]} == {0.0, 1.0}
        # ITS SHAS ARE ITS OWN: no vector collides with KEY_MIDDLE's
        mid_lab = await FMD.labelled(conn, model_key=FMD.KEY_MIDDLE,
                                     source=FMD.SOURCE_OBSERVATIONS)
        assert not set(lab["feature_shas"]) & set(mid_lab["feature_shas"])
        # PROVENANCE AND PARAMETERS REPRODUCE
        ver = await FMD.verify_provenance(conn, row, check_params=True)
        assert ver["ok"] is True, ver
        # FUNDED DECISIONS DO NOT STORE THE TABLE: refused, not guessed
        f = await FMD.labelled(conn, model_key=KEY, source=FMD.SOURCE_FUNDED)
        assert f["ok"] is False and f["refusal"] == FMD.R_FUNDED_NO_PAYOFF_TABLE
        g = await FMD.generate_candidate(conn, now=time.time(),
                                         source=FMD.SOURCE_FUNDED,
                                         model_key=KEY)
        assert g["ok"] is False and g["refusal"] == \
            FMD.R_FUNDED_NO_PAYOFF_TABLE
        # NOTHING PROSPECTIVE YET: scored, refused
        ev = await FMD.evaluate(conn, model_id=mid_id)
        assert ev["ok"] is False and ev["refusal"] == FMD.R_TOO_FEW_LABELS
        early = await FMD.promote(conn, model_id=mid_id, approved_by="owner")
        assert early["ok"] is False, early
        assert (await FMD.approved(conn, model_key=KEY))["ok"] is False
        # PROSPECTIVE, EVENT-BALANCED EVIDENCE: observed after the freeze and
        # labelled after it too
        await _prospective(conn, _eval_rows(mid, loseb))
        ev = await FMD.evaluate(conn, model_id=mid_id)
        assert ev["ok"] is True, ev
        doc = ev["evaluation"]
        assert doc[FMD.EVIDENCE_PROSPECTIVE]["n_events"] == 45
        assert doc["contamination"]["verdict"] == "CLEAN"
        assert doc["weighting"] == FMD.WEIGHTING_EVENT_BALANCED
        # A SCHEDULE NEVER PROMOTES; NOR DOES AN ANONYMOUS CALLER
        anon = await FMD.promote(conn, model_id=mid_id, approved_by=" ")
        assert anon["refusal"] == FMD.R_NO_APPROVER
        prom = await FMD.promote(conn, model_id=mid_id,
                                 approved_by="owner@test")
        assert prom["ok"] is True, prom
        cmp_ = prom["comparison"]
        assert cmp_["evidence_kind"] == FMD.EVIDENCE_PROSPECTIVE
        assert cmp_["record_source"] == FMD.SOURCE_OBSERVATIONS
        assert cmp_["improvement"] >= FMD.MIN_SKILL_MARGIN
        got = await FMD.approved(conn, model_key=KEY)
        assert got["ok"] is True and got["model"]["model_id"] == mid_id
        # ONE APPROVED PER KEY, AND THE KEYS ARE INDEPENDENT
        assert (await FMD.approved(conn, model_key=FMD.KEY_MIDDLE))[
            "ok"] is False
        # IT LEARNED THE STATED RULE
        obj = FMD.load(got["model"]["params"])
        p_win = obj.predict(FMD.conditional_features_of(
            mid, primary_cost_cents=62, hedge_cost_cents=41,
            overtime_included=True, primary_won=True))
        p_lose = obj.predict(FMD.conditional_features_of(
            loseb, primary_cost_cents=58, hedge_cost_cents=35,
            overtime_included=True, primary_won=False))
        assert p_win < 0.5 < p_lose, (p_win, p_lose)

        # A CORRECTED SETTLEMENT ON A TRAINING OBSERVATION WITHDRAWS IT
        flip = next(s for s, p in train_px.items()
                    if s.startswith("condtr-h-") and p == 1.0
                    and int(s.rsplit("-", 1)[1]) < 30)
        train_px[flip] = 0.0
        fixed = await _label(conn, train_px, at=t0 + 86400 + 10)
        assert fixed["corrected"] >= 1, fixed
        gone = await FMD.approved(conn, model_key=KEY)
        assert gone["ok"] is False
        assert gone["refusal"] == FMD.R_APPROVED_MODEL_EVIDENCE_INVALIDATED
        w = await FMD.withdraw_invalidated(conn, model_key=KEY)
        assert w["withdrawn"] is True, w
        back = await FMD.rollback(conn, to_model_id=mid_id,
                                  reason="try to restore it")
        assert back["ok"] is False, back


async def test_the_observation_pass_generates_and_scores_both_keys():
    async with _conn() as conn:
        await _clean(conn)
        mid, loseb = _middle(), _lose_binary()
        t0 = time.time() - 3 * 86400
        prices = await _cohort(conn, _training_rows(mid, loseb),
                               prefix="cpass", first_at=t0)
        await _label(conn, prices, at=t0 + 86400)
        got = await PO.observation_pass(
            conn, candidates=[], quoter=None, prose_reader=None,
            settlement_reader=_reader(prices), now=time.time())
        assert got["ok"] is True
        assert got["generate"]["generated"] is True, got["generate"]
        assert got["generate_conditional"]["generated"] is True, \
            got["generate_conditional"]
        assert got["generate_conditional"]["model_key"] == KEY
        ev = got["evaluate"]
        assert ev["ok"] is True, ev
        assert {s["model_key"] for s in ev["scored"]} == {
            FMD.KEY_MIDDLE, KEY}, ev["scored"]
        assert set(ev["withdraw_by_key"]) == {FMD.KEY_MIDDLE, KEY}
        assert ev["candidates_waiting_by_key"] == {FMD.KEY_MIDDLE: 1, KEY: 1}
        assert got["promoted_anything"] is False
        # THE HEARTBEAT SHOWS BOTH
        d = LOOP._observation_digest(got)
        assert d["generate"]["generated"] is True
        assert d["generate_conditional"]["generated"] is True
        assert d["generate_conditional"]["model_key"] == KEY
        assert set(d["evaluate"]["withdraw_by_key"]) == {FMD.KEY_MIDDLE, KEY}
        assert d["evaluate"]["candidates_waiting_by_key"][KEY] == 1


# ═════════════════════════════════════════════════════════════════════
# 2 · THE VOID RATE: FIXTURES, A BAR, NO LOOK-AHEAD
# ═════════════════════════════════════════════════════════════════════

async def test_the_void_rate_counts_fixtures_point_in_time():
    async with _conn() as conn:
        await _clean(conn)
        mid = _middle()
        base = time.time() - 10 * 86400
        # 42 settled fixtures and 3 declared voids, labelled at t1
        rows = [_row(mid, 1.0, 0.0) for _ in range(42)]
        rows += [_row(mid, 1.0, VOID_PX) for _ in range(3)]
        px = await _cohort(conn, rows, prefix="vr", first_at=base)
        # A SECOND OBSERVATION OF FIXTURE 0 -- a different pair -- is still
        # one fixture
        await PO.record(
            conn, fixture="vr-fx-0", admitted={"structure": mid,
                                               "taxonomy": "MIDDLE",
                                               "condition_id": "vr2-h#" + LONG},
            held_leg=_Leg("vr2-p#" + LONG), primary_slug="vr2-p",
            primary_side=LONG, hedge_slug="vr2-h", hedge_side=LONG,
            primary_cost_cents=62, hedge_cost_cents=41, overtime_included=True,
            price_basis={"synthetic": LABEL}, at=base + 5)
        px.update({"vr2-p": 1.0, "vr2-h": 1.0})
        t1 = base + 86400
        await _label(conn, px, at=t1)
        before = await PO.void_rate(conn, through=t1 - 60)
        assert before["ok"] is False
        assert before["refusal"] == PO.R_VOID_RATE_TOO_FEW_FIXTURES
        assert before["n_fixtures"] == 0
        got = await PO.void_rate(conn, through=t1 + 60)
        assert got["ok"] is True, got
        assert got["n_fixtures"] == 45 and got["n_void_fixtures"] == 3
        assert got["rate"] == pytest.approx(3 / 45)
        assert got["upper_95"] == pytest.approx(PO.wilson_upper_95(3, 45))
        # A VOID DECLARED LATER -- a correction of fixture 1's hedge to a
        # void at t2 -- is not seen at t1, and is seen after t2
        px["vr-h-1"] = VOID_PX
        t2 = t1 + 3600
        fixed = await _label(conn, px, at=t2)
        assert fixed["corrected"] >= 1, fixed
        at_t1 = await PO.void_rate(conn, through=t1 + 60)
        assert at_t1["n_void_fixtures"] == 3, at_t1
        now = await PO.void_rate(conn)
        assert now["n_void_fixtures"] == 4 and now["n_fixtures"] == 45, now
        # AND BELOW THE BAR IT IS REFUSED, WITH ITS COUNT
        await BOOT._purge(conn, "fixture LIKE $1", "vr-fx-1_")
        thin = await PO.void_rate(conn)
        assert thin["ok"] is False and thin["n_fixtures"] < 40, thin


# ═════════════════════════════════════════════════════════════════════
# 3 · predict_distribution
# ═════════════════════════════════════════════════════════════════════

#: THE HOLD VALUATION'S SOURCE, as `_primary_probability_for` passes it: the
#: probability is stated for the outcome the held leg pays on, and the HOLD
#: valuation record it came from was checked (owner requirement: one event
#: under every component). SYNTHETIC.
PRIMARY_SOURCE = {"from": "CHOSEN_FOR_THIS_TEST", "is": LABEL,
                  "probability_event": "CHICAGO_BEARS",
                  "payout_event_held": "CHICAGO_BEARS",
                  "record_checked": True, "valuation_row_id": 1}
#: A PASSING CALIBRATION MEASUREMENT, in the shape the worker's
#: `source_calibration` returns -- SYNTHETIC, stated for this test.
PASSING_CALIBRATION = {"measured": True, "within_tolerance": True,
                       "source_version": "SYNTHETIC_TEST_SOURCE",
                       "why": "SYNTHETIC CALIBRATION FOR THIS TEST: " + LABEL}


async def test_predict_distribution_names_the_key_it_lacks_then_prices_classes():
    async with _conn() as conn:
        await _clean(conn)
        mid, loseb = _middle(), _lose_binary()
        kw = dict(structure=mid, primary_cost_cents=62, hedge_cost_cents=41,
                  overtime_included=True, primary_probability=0.6,
                  primary_source=PRIMARY_SOURCE)
        none = await FMD.predict_distribution(conn, at=time.time(), **kw)
        assert none["ok"] is False
        assert none["refusal"] == FMD.R_NO_APPROVED_MODEL
        assert none["refused_for_key"] == KEY and KEY in none["why"]
        gen, _, _ = await _train_and_generate(conn, _training_rows(mid, loseb))
        await _prospective(conn, _eval_rows(mid, loseb))
        assert (await FMD.evaluate(conn, model_id=gen["model_id"]))["ok"]
        prom = await FMD.promote(conn, model_id=gen["model_id"],
                                 approved_by="owner@test")
        assert prom["ok"] is True, prom
        at = time.time()
        # THE ENTRY LANE'S GATES ON AN EXTERNAL PROBABILITY APPLY: no
        # current passing calibration, or a probability outside the source's
        # declared support, refuses -- the model's approval does not waive it
        nocal = await FMD.predict_distribution(conn, at=at, **kw)
        assert nocal["refusal"] == FMD.R_PRIMARY_SOURCE_NOT_CALIBRATED, nocal
        failing = await FMD.predict_distribution(
            conn, at=at, primary_calibration=dict(PASSING_CALIBRATION,
                                                  within_tolerance=False),
            **kw)
        assert failing["refusal"] == FMD.R_PRIMARY_SOURCE_NOT_CALIBRATED
        tail = await FMD.predict_distribution(
            conn, at=at, primary_calibration=PASSING_CALIBRATION,
            **dict(kw, primary_probability=0.99))
        assert tail["refusal"] == FMD.R_PRIMARY_OUTSIDE_SUPPORT, tail
        got = await FMD.predict_distribution(
            conn, at=at, primary_calibration=PASSING_CALIBRATION, **kw)
        assert got["ok"] is True, got
        assert got["distribution_basis"]["primary_gates"][
            "MODEL_TRUST_DRIFT"]["clear"] is True
        probs = got["region_probabilities"]
        assert sum(probs.values()) == pytest.approx(1.0, abs=1e-9)
        assert probs["fixture postponed; market stays open"] == 0.0
        assert got["model_id"] == gen["model_id"]
        pred = got["predicted"]
        assert pred["target"] == FMD.TARGET_HEDGE_GIVEN_PRIMARY
        assert set(pred["feature_shas_by_primary_outcome"]) == {PS.WIN,
                                                                PS.LOSE}
        assert got["feature_sha"] == FMD.feature_sha(got["features"])
        assert set(got["features"]) == set(FMD.FEATURES)
        # THE VOID RATE AS OF `at`: 68 labelled + 3 void training fixtures
        vb = got["distribution_basis"]["void"]
        assert vb["used"] is True
        assert vb["n_fixtures"] == 71 and vb["n_void_fixtures"] == 3
        assert vb["rate"] == pytest.approx(3 / 71)
        # WIN LEARNED FROM THE APPROVED MODEL, LOSE STRUCTURAL
        cond = got["distribution_basis"]["conditional"]
        assert cond[PS.WIN]["kind"] == "LEARNED"
        assert cond[PS.WIN]["p_hedge_wins"] == pytest.approx(
            pred["p_hedge_wins_given_primary"][PS.WIN])
        assert cond[PS.LOSE]["kind"] == "STRUCTURAL"
        assert pred["implied_primary_marginal"] == pytest.approx(
            (1 - 3 / 71) * 0.6)
        assert got["distribution_basis"]["primary"]["source"] == \
            PRIMARY_SOURCE
        assert got["region_probabilities_at_void_upper_95"] is not None
        # a structure the distribution cannot price is refused by name
        tie = IS.classify(dataclasses.replace(IS.BEARS_MONEYLINE,
                                              cost_cents_per_unit=62),
                          dataclasses.replace(IS.PANTHERS_PLUS_4_5,
                                              cost_cents_per_unit=41),
                          sport_permits_tie=True).to_dict()
        bad = await FMD.predict_distribution(
            conn, at=at, primary_calibration=PASSING_CALIBRATION,
            **dict(kw, structure=tie))
        assert bad["refusal"] == PS.R_PRIMARY_PARTIAL_OUTCOME_NOT_PRICED


# ═════════════════════════════════════════════════════════════════════
# 4 · ACCEPTANCE: THE SCHEDULED PAIR PASS, THROUGH THE REAL SUPPLIERS
# ═════════════════════════════════════════════════════════════════════

P_HOLD = 0.60           # the held position's probability, CHOSEN
EXIT_BID = 0.66         # the exit's best executable bid, CHOSEN
#: The bid ladder, CHOSEN so the deployed selector ranks HOLD, DIRECT_EXIT
#: and a REDUCE (four contracts above the hurdle, the rest below it).
LADDER = ((EXIT_BID, 4), (0.55, 20))


def _prose(now):
    async def read(slug):
        return {"ok": True, "rules_text": HW.PROSE_WITH_CANCELLATION,
                "rules_field": "description",
                "source": "pmus:/markets?slug=<slug>:rules_text",
                "read_at": now}
    return read


def _quoter(prices, now):
    async def quote(slug, side):
        p = prices.get(slug)
        if p is None:
            return {"ok": False, "refusal": "NOT_IN_THE_CAPTURED_BOOK"}
        return {"ok": True, "cost_per_share": p[0], "depth_qty": p[1],
                "available_qty": p[1], "inputs_expire_at": now + 30}
    return quote


async def _hw_structures(conn, iid, now):
    """The two pairings the production suppliers build on the harness
    fixture, classified by `discover` -- the structures the model learns on
    and the pass then prices."""
    held = await HS.held_leg_for(
        conn, position={"intent_id": iid, "us_market_slug": HW.HELD,
                        "order_intent": FX.LONG, "residual_qty": 10,
                        "avg_price": 0.55},
        prose_reader=_prose(now), now=now)
    assert held["ok"], held
    cands = await HS.candidate_legs_for(
        conn, held_row={"market_slug": HW.HELD, "event_slug": HW.EVENT,
                        "residual_qty": 10},
        quoter=_quoter({HW.SIB: (0.30, 500)}, now),
        prose_reader=_prose(now), now=now)
    found = PC.discover(held_leg=held["leg"],
                        candidate_legs=[c["leg"] for c in cands["legs"]],
                        sport_permits_tie=False)
    assert len(found["admitted"]) == 2, found
    return {a["condition_id"]: a["structure"] for a in found["admitted"]}


def _selector_ranking():
    """HOLD, DIRECT_EXIT and REDUCE from the DEPLOYED selector, on one
    probability -- the ev_hold record shape `rank_with_hold` reads."""
    return MS.rank_with_hold(
        10, 0.55, ev_hold={"probability": P_HOLD, "status": "IDENTIFIED"},
        bid=EXIT_BID, bid_size=LADDER[0][1], complement_ask=None,
        complement_ask_size=None, fee_fn=lambda *, qty, price: 0.0,
        venue="PMUS", us_market_slug=HW.HELD, held_is_long=True,
        sale_ladder={"levels": [{"acquisition_price": px, "api_price": px,
                                 "qty": q} for px, q in LADDER]},
        executable_actions=FM.EXECUTABLE_ACTIONS)


async def _pass(conn, iid, now, adapter):
    ranked = _selector_ranking()
    assert ranked["selected"] == "DIRECT_EXIT", ranked["selected"]
    assert {c["action"] for c in ranked["candidates"]} == {
        "HOLD", "DIRECT_EXIT", "REDUCE"}, ranked["candidates"]
    ex = next(c for c in ranked["candidates"]
              if c["action"] == "DIRECT_EXIT")
    ranking = {k: ranked.get(k) for k in ("selected", "candidates",
                                          "not_rankable")}
    deferred = {iid: {
        "intent_id": iid, "selected": "DIRECT_EXIT",
        "selected_qty": ex["qty"], "limit_price": EXIT_BID,
        "proceeds_per_contract": EXIT_BID,
        "expected_net_usd": ex["value_usd"], "inputs_expire_at": now + 300,
        "ranking": ranking}}
    mr = {iid: {"ranking": ranking, "hold_probability": {
        "probability": P_HOLD, "source_row_id": "synthetic-valuation",
        "source": {"provider": "SYNTHETIC_FOR_THIS_TEST",
                   "version": CAL_SOURCE},
        "probability_event": "BOS_WINS", "payout_event_held": "BOS_WINS",
        "status": "IDENTIFIED"}}}
    pair_inputs = functools.partial(
        LOOP.funded_pair_inputs, deferred=deferred, account_id=HW.ACCT,
        venue=HW.VENUE, management_rankings=mr, prose_reader=_prose(now),
        quoter=_quoter({HW.SIB: (0.30, 500)}, now))
    return await PC.pass_once(conn, account_id=HW.ACCT, venue=HW.VENUE,
                              pair_inputs=pair_inputs, adapter=adapter,
                              deferred_exits=deferred, now=now)


async def _decision_row(conn, decision_id):
    row = dict(await conn.fetchrow(
        "SELECT * FROM bettor_funded_decisions WHERE decision_id=$1",
        decision_id))
    for k in ("ranked", "unrankable", "predicted", "features"):
        if isinstance(row.get(k), str):
            row[k] = json.loads(row[k])
    return row


async def test_acceptance_the_scheduled_pass_ranks_the_hedge_on_the_distribution():
    async with _conn() as conn:
        if not await PO.has_schema(conn):
            pytest.skip("migration 140 is not in this database")
        await _clean(conn)
        await HW._catalogue(conn)
        iid = await HW._held(conn)
        now = time.time()
        shapes = await _hw_structures(conn, iid, now)
        # SYNTHETIC OBSERVATIONS ON THE SAME TWO STRUCTURES, each on its own
        # fixture: given the held side wins, the +1.5 side wins 3 in 10 and
        # the -1.5 side 7 in 10.
        plus = next(s for c, s in shapes.items() if c.endswith(PO.SHORT))
        minus = next(s for c, s in shapes.items() if c.endswith(LONG))

        def rows(n_each):
            out = [_row(plus, 1.0, 1.0 if (i % 10) < 3 else 0.0)
                   for i in range(n_each)]
            out += [_row(minus, 1.0, 1.0 if (i % 10) < 7 else 0.0)
                    for i in range(n_each)]
            return out
        tr = rows(25) + [_row(plus, 1.0, VOID_PX) for _ in range(2)]
        gen, _, _ = await _train_and_generate(conn, tr, prefix="acc")
        assert gen["generated"] is True, gen
        await _prospective(conn, rows(23), prefix="acc")
        assert (await FMD.evaluate(conn, model_id=gen["model_id"]))["ok"]
        prom = await FMD.promote(conn, model_id=gen["model_id"],
                                 approved_by="owner@test")
        assert prom["ok"] is True, prom
        await _seed_calibration(conn)
        void = await PO.void_rate(conn, through=now)
        assert void["ok"] is True and void["n_void_fixtures"] == 2, void

        adapter = HW._Adapter()
        got = await _pass(conn, iid, now, adapter)
        assert got["ok"] is True, got
        step = got["considered"][0]
        dec = step["decision"]
        assert dec["ok"] is True, step
        assert dec["region_probabilities_came_from"].startswith(
            "APPROVED_DISTRIBUTION:%s@" % KEY), dec
        # ── ONE DECISION, ALL FOUR ACTIONS IN IT ─────────────────────
        row = await _decision_row(conn, "dec:%s:%d" % (iid[-24:], int(now)))
        ranked = {c["action"] for c in row["ranked"]}
        assert {"HOLD", "DIRECT_EXIT",
                FD.ACTION_ACQUIRE_INDIRECT_HEDGE} <= ranked, row["ranked"]
        reduce_ = [c for c in row["unrankable"] if c["action"] == "REDUCE"]
        assert reduce_ and reduce_[0]["blocker"] == LOOP.R_NO_EXECUTABLE_PLAN
        hedges = [c for c in row["ranked"]
                  if c["action"] == FD.ACTION_ACQUIRE_INDIRECT_HEDGE]
        assert len(hedges) == 2, hedges
        # the selected action, recorded
        values = {(c["action"], c.get("candidate_id")): c["value_usd"]
                  for c in row["ranked"]}
        best = max(values.items(), key=lambda kv: kv[1])
        assert row["action"] == {FD.ACTION_ACQUIRE_INDIRECT_HEDGE:
                                 "ACQUIRE_HEDGE",
                                 "DIRECT_EXIT": "EXIT"}.get(best[0][0],
                                                            best[0][0])
        # ── ALL FOUR REST ON ONE PRIMARY MARGINAL ───────────────────
        hold = next(c for c in row["ranked"] if c["action"] == "HOLD")
        assert hold["value_per_contract"] == P_HOLD
        for h in hedges:
            cons = h["primary_marginal_consistency"]
            assert cons["same_primary_probability"] is True, cons
            assert cons["acquire_primary_probability"] == P_HOLD
            assert cons["acquire_implied_primary_marginal"] == pytest.approx(
                (1 - void["rate"]) * P_HOLD)
        # ── HOLD AND ACQUIRE ARE COMPARED UNDER ONE MEASURE ─────────
        # the acquisition ranks at HOLD's value plus its increment over HOLD
        # computed under the distribution; its whole-position expectation
        # is kept beside it
        for h in hedges:
            assert h["hold_value_usd"] == pytest.approx(hold["value_usd"])
            assert h["value_usd"] == pytest.approx(
                hold["value_usd"] + h["increment_vs_hold_same_measure_usd"])
            assert h["whole_position_expected_net_usd"] is not None
            assert h["valued_as"].startswith("HOLD's value")
        # ── THE DISTRIBUTION BASIS IS ON THE LEDGER ROW ─────────────
        assert row["model_key"] == KEY
        assert row["model_version"] == prom["model"]["model_version"]
        assert set(row["features"]) == set(FMD.FEATURES)
        assert row["feature_sha"] == FMD.feature_sha(row["features"])
        pred = row["predicted"]
        basis = pred["distribution_basis"]
        assert basis["model_id"] == gen["model_id"]
        assert basis["void"]["used"] is True
        assert basis["void"]["rate"] == pytest.approx(void["rate"])
        assert basis["void"]["n_fixtures"] == void["n_fixtures"]
        assert basis["primary"]["source"]["from"] == \
            "hold_ranking.HOLD.value_per_contract"
        assert basis["primary"]["source"]["record_checked"] is True
        assert basis["primary"]["p_win"] == P_HOLD
        assert set(pred["feature_shas_by_primary_outcome"]) == {PS.WIN,
                                                                PS.LOSE}
        assert pred["primary_marginal_consistency"][
            "same_primary_probability"] is True
        assert "decision_sensitive_to_void_rate" in \
            pred["void_rate_sensitivity"]
        assert sum(pred["class_probabilities"].values()) == \
            pytest.approx(1.0, abs=1e-9)
        # ONE EVENT UNDER EVERY COMPONENT, the evidence behind the learned
        # conditional, and what each probability is conditional on
        assert set(basis["same_event"]) == {"fixture", "side", "period",
                                            "overtime", "settlement"}
        assert all(v["agrees"] for v in basis["same_event"].values())
        for c in basis["conditional_evidence"].values():
            assert c["fixtures"] >= PS.MIN_CONDITIONAL_COHORT_FIXTURES
            assert c["wilson_95"][0] <= c["wilson_95"][1]
        assert basis["probability_kinds"]["probabilities"] == \
            PS.UNCONDITIONAL
        assert basis["probability_kinds"]["basis.primary.p_win"] == \
            PS.CONDITIONAL_ON_NORMAL_SETTLEMENT
        # ── NOTHING WAS SENT ────────────────────────────────────────
        assert FX.FUNDED_SUBMISSION_ENABLED is False
        assert adapter.sent == [], adapter.sent
        assert got["opened_anything"] is False

        assert basis["primary"]["source"]["calibration"]["measured"] is True
        assert basis["primary"]["source"]["calibration"]["source_version"] \
            == CAL_SOURCE
        assert basis["primary_gates"]["MODEL_TRUST_DRIFT"]["clear"] is True

        # ── THE SAME CYCLE WITH NO CURRENT CALIBRATION ──────────────
        await conn.execute("DELETE FROM external_source_calibration "
                           " WHERE source_version=$1", CAL_SOURCE)
        uncal = now + 1
        adapter_u = HW._Adapter()
        got_u = await _pass(conn, iid, uncal, adapter_u)
        row_u = await _decision_row(conn,
                                    "dec:%s:%d" % (iid[-24:], int(uncal)))
        blocked_u = [c for c in row_u["unrankable"]
                     if c["action"] == FD.ACTION_ACQUIRE_INDIRECT_HEDGE]
        assert len(blocked_u) == 2, row_u["unrankable"]
        for b in blocked_u:
            assert b["prediction"]["refusal"] == \
                FMD.R_PRIMARY_SOURCE_NOT_CALIBRATED, b["prediction"]
        assert got_u["considered"][0]["decision"][
            "region_probabilities_came_from"].startswith(
            "DISTRIBUTION_COULD_NOT_PRICE_THIS_STRUCTURE:%s"
            % FMD.R_PRIMARY_SOURCE_NOT_CALIBRATED)
        assert adapter_u.sent == []

        # ── THE SAME CYCLE WITH NO APPROVED CONDITIONAL MODEL ───────
        await conn.execute("DELETE FROM bettor_funded_models "
                           " WHERE model_key=$1", KEY)
        await _seed_calibration(conn)
        later = now + 2
        adapter2 = HW._Adapter()
        got2 = await _pass(conn, iid, later, adapter2)
        step2 = got2["considered"][0]
        assert step2["decision"]["region_probabilities_came_from"] == \
            "NOTHING_APPROVED_FOR:%s" % KEY, step2["decision"]
        row2 = await _decision_row(conn,
                                   "dec:%s:%d" % (iid[-24:], int(later)))
        blocked = [c for c in row2["unrankable"]
                   if c["action"] == FD.ACTION_ACQUIRE_INDIRECT_HEDGE]
        assert len(blocked) == 2, row2["unrankable"]
        for b in blocked:
            assert b["blocker"] == FD.R_NO_REGION_PROBABILITIES
            assert b["prediction"]["refusal"] == FMD.R_NO_APPROVED_MODEL
            assert b["prediction"]["model_key"] == KEY
            assert "OUTSIDE" not in json.dumps(b["prediction"])
        assert {c["action"] for c in row2["ranked"]} == {"HOLD",
                                                         "DIRECT_EXIT"}
        assert row2["model_key"] is None
        assert adapter2.sent == []

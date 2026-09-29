"""THE PAIRING MODEL'S BOOTSTRAP: NON-FUNDED OBSERVATIONS, LABELLED BY THE
VENUE'S OWN SETTLEMENTS, THROUGH THE SAME REGISTRY BAR.

The circle: funded labels need both legs held; holding the second needs an
approved model; approval needs labels. These tests pin the way out and pin that
it is not a shortcut:

  * an observation is produced by the PRODUCTION discovery (catalogue rows,
    settlement prose, `discover`) with nothing held, reserved or sent, and is
    idempotent within its bucket;
  * its label is read from BOTH contracts' venue settlements, side-aware; a
    push or void is not a label; a pending contract leaves it waiting; a
    corrected settlement writes a new version with its history;
  * a model fit on observations is a CANDIDATE with record-bound provenance
    naming its source; it is promoted only by `promote`, with a named
    approver, on prospective event-balanced evidence -- and a correction after
    approval withdraws its pricing authority;
  * the entry cycle runs the observer, and the observation price carries its
    book-currency verdict and is never usable for orders without one.

ENGINEERING PROOF ON SUBSTITUTED TRANSPORT: the book, prose and settlement
reads are substituted; the catalogue, supplier, classifier, recorder, labeller
and registry are the deployed ones.
"""
from __future__ import annotations

import contextlib
import json
import os
import time

import asyncpg
import pytest

from sportsassets import bettor_funded_model as FMD
from sportsassets import bettor_live_read as LR
from sportsassets import bettor_pair_observations as PO
from tests import test_the_hedge_beats_hold_through_the_real_suppliers as HW

DSN = os.environ.get("RN1X_TEST_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="needs a migrated database")

LONG, SHORT = PO.LONG, PO.SHORT
NOW = HW.NOW
PRICES = {HW.HELD: (0.55, 500), HW.SIB: (0.30, 500)}


@contextlib.asynccontextmanager
async def _conn():
    c = await asyncpg.connect(DSN)
    try:
        yield c
    finally:
        await c.close()


async def _purge(conn, where: str = "TRUE", *args):
    """Observations and their label history are append-only by trigger. A test
    removes its own rows with triggers suspended for ITS transaction only."""
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "DELETE FROM bettor_pair_observation_labels WHERE observation_id "
            " IN (SELECT observation_id FROM bettor_pair_observations "
            "     WHERE %s)" % where, *args)
        await conn.execute(
            "DELETE FROM bettor_pair_observations WHERE %s" % where, *args)


async def _clean(conn):
    if not await PO.has_schema(conn):
        pytest.skip("migration 140 is not in this database")
    await _purge(conn)
    await conn.execute("DELETE FROM bettor_funded_models "
                       " WHERE model_id LIKE 'fmc:%:obs-%' "
                       "    OR model_id LIKE 'obsboot-%'")
    await HW._clean(conn)


@pytest.fixture(autouse=True)
async def _leave_nothing_behind():
    yield
    if DSN:
        async with _conn() as c:
            if await PO.has_schema(c):
                await _purge(c)
                await c.execute("DELETE FROM bettor_funded_models "
                                " WHERE model_id LIKE 'fmc:%:obs-%' "
                                "    OR model_id LIKE 'obsboot-%'")


def _settlements(prices: dict, *, status=LR.RESOLVED):
    """slug -> settlement read, in `bettor_live_read.read_settlement`'s shape."""
    def read(slug):
        p = prices.get(slug)
        if p is None:
            return {"status": LR.PENDING, "settlement_price": None}
        return {"status": status, "settlement_price": p,
                "settlement_price_raw": str(p), "settled_at": "2026-10-06"}
    return read


async def _observe(conn, *, at=NOW):
    return await PO.observe_candidate(
        conn, us_market_slug=HW.HELD, side=LONG, quoter=HW._quoter(PRICES),
        prose_reader=HW._prose_reader(), now=at)


# ═════════════════════════════════════════════════════════════════════
# 1 · AN OBSERVATION, FROM THE PRODUCTION DISCOVERY, HOLDING NOTHING
# ═════════════════════════════════════════════════════════════════════

async def test_an_observation_is_the_production_discovery_frozen_and_holds_nothing():
    async with _conn() as conn:
        await _clean(conn)
        await HW._catalogue(conn)
        intents_before = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents")
        got = await _observe(conn)
        assert got["ok"] is True, got
        assert got["sent_anything"] is False
        assert got["recorded"] and all(r["written"] for r in got["recorded"])
        row = dict(await conn.fetchrow(
            "SELECT * FROM bettor_pair_observations WHERE observation_id=$1",
            got["recorded"][0]["observation_id"]))
        assert row["primary_slug"] == HW.HELD and row["primary_side"] == LONG
        assert row["hedge_slug"] == HW.SIB
        assert row["label_status"] == PO.AWAITING
        feats = json.loads(row["features"]) if isinstance(
            row["features"], str) else row["features"]
        structure = json.loads(row["structure"]) if isinstance(
            row["structure"], str) else row["structure"]
        # THE MODEL'S OWN VECTOR, from the classifier's output and the
        # displayed costs -- nothing re-derived here.
        assert feats == FMD.features_of(
            structure, primary_cost_cents=float(row["primary_cost_cents"]),
            hedge_cost_cents=float(row["hedge_cost_cents"]),
            overtime_included=row["overtime_included"])
        assert row["feature_sha"] == FMD.feature_sha(feats)
        assert float(row["primary_cost_cents"]) == 55.0
        basis = json.loads(row["price_basis"]) if isinstance(
            row["price_basis"], str) else row["price_basis"]
        assert set(basis) == {"primary", "hedge"}
        assert basis["primary"]["usable_for_orders"] is False
        # NOTHING HELD, RESERVED OR SENT
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents") == intents_before
        # THE SAME PAIR IN THE SAME HOUR IS ONE OBSERVATION...
        again = await _observe(conn, at=NOW + 60)
        assert [r["written"] for r in again["recorded"]] == \
            [False] * len(again["recorded"])
        # ...AND A NEW HOUR IS A NEW ONE, with its own instant
        later = await _observe(conn, at=NOW + PO.BUCKET_S)
        assert all(r["written"] for r in later["recorded"])
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_pair_observations") == \
            2 * len(got["recorded"])
        # AND WHAT WAS OBSERVED CANNOT BE EDITED
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute(
                "UPDATE bettor_pair_observations SET hedge_cost_cents=1 "
                " WHERE observation_id=$1", row["observation_id"])


async def test_an_unpriced_first_leg_is_not_observed():
    async with _conn() as conn:
        await _clean(conn)
        await HW._catalogue(conn)
        got = await PO.observe_candidate(
            conn, us_market_slug=HW.HELD, side=LONG,
            quoter=HW._quoter({HW.SIB: (0.30, 500)}),
            prose_reader=HW._prose_reader(), now=NOW)
        assert got["ok"] is False and got["refusal"] == PO.R_NO_PRICE
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_pair_observations") == 0


# ═════════════════════════════════════════════════════════════════════
# 2 · THE LABEL IS THE VENUE'S TWO SETTLEMENTS
# ═════════════════════════════════════════════════════════════════════

async def _one(conn):
    got = await _observe(conn)
    oid = got["recorded"][0]["observation_id"]
    return dict(await conn.fetchrow(
        "SELECT * FROM bettor_pair_observations WHERE observation_id=$1", oid))


def _pays(side, won):
    """The long-side settlement price at which `side` won (or lost)."""
    return (1.0 if won else 0.0) if side == LONG else (0.0 if won else 1.0)


async def test_the_label_is_both_sides_won_read_from_both_settlements():
    async with _conn() as conn:
        await _clean(conn)
        await HW._catalogue(conn)
        row = await _one(conn)
        oid, hs = row["observation_id"], row["hedge_side"]
        # ONE CONTRACT STILL OPEN: nothing is written
        got = await PO.label_pending(conn, now=NOW + 10, settlement_reader=
                                     _settlements({HW.HELD: 1.0}))
        assert got["awaiting"] >= 1 and got["labelled"] == 0
        # BOTH SETTLED, BOTH OBSERVED SIDES WON
        got = await PO.label_pending(conn, now=NOW + 20, settlement_reader=
                                     _settlements({HW.HELD: 1.0,
                                                   HW.SIB: _pays(hs, True)}))
        assert got["labelled"] >= 1, got
        r = dict(await conn.fetchrow(
            "SELECT * FROM bettor_pair_observations WHERE observation_id=$1",
            oid))
        assert r["label_status"] == PO.LABELLED and r["middle_occurred"] is True
        assert r["primary_won"] is True and r["hedge_won"] is True
        assert r["outcome_available_at"].timestamp() == pytest.approx(NOW + 20)
        assert r["label_version"] == 1
        # THE VENUE CORRECTS THE HEDGE'S SETTLEMENT: a new version, with history
        got = await PO.label_pending(conn, now=NOW + 30, settlement_reader=
                                     _settlements({HW.HELD: 1.0,
                                                   HW.SIB: _pays(hs, False)}))
        assert got["corrected"] >= 1, got
        r = dict(await conn.fetchrow(
            "SELECT * FROM bettor_pair_observations WHERE observation_id=$1",
            oid))
        assert r["middle_occurred"] is False and r["label_version"] == 2
        hist = [dict(h) for h in await conn.fetch(
            "SELECT label_version, middle_occurred FROM "
            " bettor_pair_observation_labels WHERE observation_id=$1 "
            " ORDER BY label_version", oid)]
        assert hist == [{"label_version": 1, "middle_occurred": True},
                        {"label_version": 2, "middle_occurred": False}]
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute(
                "UPDATE bettor_pair_observation_labels SET middle_occurred="
                "true WHERE observation_id=$1", oid)
        # A LABEL CANNOT BE REWRITTEN WITHOUT A NEW VERSION
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute(
                "UPDATE bettor_pair_observations SET middle_occurred=true, "
                " hedge_won=true WHERE observation_id=$1", oid)


async def test_a_push_or_void_is_not_a_label_and_a_short_side_wins_at_zero():
    async with _conn() as conn:
        await _clean(conn)
        await HW._catalogue(conn)
        row = await _one(conn)
        got = await PO.label_pending(conn, now=NOW + 20, settlement_reader=
                                     _settlements({HW.HELD: 1.0,
                                                   HW.SIB: 0.5}))
        assert got["not_a_label"] >= 1, got
        r = await conn.fetchrow(
            "SELECT label_status, middle_occurred, label_why FROM "
            " bettor_pair_observations WHERE observation_id=$1",
            row["observation_id"])
        assert r["label_status"] == PO.NOT_A_LABEL
        assert r["middle_occurred"] is None
        assert r["label_why"] == PO.WHY_PUSH_OR_VOID
    # side-awareness, as a pure rule
    assert PO.won(LONG, 1.0) is True and PO.won(LONG, 0.0) is False
    assert PO.won(SHORT, 0.0) is True and PO.won(SHORT, 1.0) is False
    assert PO.won(LONG, 0.5) is None and PO.won(SHORT, None) is None


# ═════════════════════════════════════════════════════════════════════
# 3 · A MODEL FIT ON OBSERVATIONS CLEARS THE SAME BAR, AND ONLY THAT BAR
# ═════════════════════════════════════════════════════════════════════

class _Leg:
    def __init__(self, cid):
        self.condition_id = cid


def _structure(width: int) -> dict:
    return {"both_win_regions": ["r%d" % k for k in range(width)],
            "cost_cents": 80, "min_payout_cents": 100,
            "max_payout_cents": 200, "table": []}


async def _cohort(conn, *, n, first_at, prefix, skill=True, step=60.0):
    """`n` observations on `n` distinct fixtures via the production recorder,
    labelled via the production labeller from substituted settlements. With
    `skill`, a wider window hits more often -- a rule a model can learn."""
    prices, ids = {}, []
    for i in range(n):
        w = 1 + (i % 4)
        hit = (i % 10) < (2 * w + 1) if skill else (i % 2 == 0)
        p_slug, h_slug = "%s-p-%d" % (prefix, i), "%s-h-%d" % (prefix, i)
        got = await PO.record(
            conn, fixture="%s-fx-%d" % (prefix, i),
            admitted={"structure": _structure(w), "taxonomy": "MIDDLE",
                      "condition_id": h_slug + "#" + LONG},
            held_leg=_Leg(p_slug + "#" + LONG), primary_slug=p_slug,
            primary_side=LONG, hedge_slug=h_slug, hedge_side=LONG,
            primary_cost_cents=55, hedge_cost_cents=30 + w,
            overtime_included=True, price_basis={"test": True},
            at=first_at + step * i)
        ids.append(got["observation_id"])
        prices[p_slug] = 1.0
        prices[h_slug] = 1.0 if hit else 0.0
    return ids, prices


async def _label(conn, prices, *, at):
    got = await PO.label_pending(conn, now=at, limit=10_000, recheck=10_000,
                                 settlement_reader=_settlements(prices))
    assert got["ok"], got
    return got


async def test_a_model_fit_on_observations_is_promoted_only_through_the_bar():
    async with _conn() as conn:
        await _clean(conn)
        t0 = time.time() - 3 * 86400
        _, train_px = await _cohort(conn, n=50, first_at=t0, prefix="obtr")
        await _label(conn, train_px, at=t0 + 86400)
        # THE SCHEDULE'S GENERATOR, ON THE OBSERVATION SOURCE
        gen = await FMD.generate_candidate(conn, now=time.time(),
                                           source=FMD.SOURCE_OBSERVATIONS)
        assert gen["ok"] and gen["generated"] is True, gen
        mid = gen["model_id"]
        assert ":obs-" in mid
        row = dict(await conn.fetchrow(
            "SELECT * FROM bettor_funded_models WHERE model_id=$1", mid))
        prov = json.loads(row["training_provenance"]) if isinstance(
            row["training_provenance"], str) else row["training_provenance"]
        assert prov["kind"] == FMD.PROVENANCE_RECORDS
        assert prov["source"] == FMD.SOURCE_OBSERVATIONS
        assert prov["n_events"] == 50 and len(prov["decision_ids"]) == 50
        assert row["state"] == "CANDIDATE"
        assert (await FMD.verify_provenance(conn, row))["ok"] is True
        # NO PROSPECTIVE EVIDENCE YET: the bar refuses
        early = await FMD.promote(conn, model_id=mid, approved_by="owner@test")
        assert early["ok"] is False, early
        # OBSERVATIONS MADE AFTER THE FREEZE, LABELLED AFTER IT TOO
        after = time.time() + 60
        _, eval_px = await _cohort(conn, n=45, first_at=after, prefix="obev")
        await _label(conn, eval_px, at=after + 45 * 60 + 60)
        ev = await FMD.evaluate(conn, model_id=mid)
        assert ev["ok"] is True, ev
        assert ev["evaluation"]["record_source"] == FMD.SOURCE_OBSERVATIONS
        assert ev["evaluation"][FMD.EVIDENCE_PROSPECTIVE]["n_events"] == 45
        # A SCHEDULE NEVER PROMOTES; A NAMED APPROVER DOES, ON THE SAME BAR
        prom = await FMD.promote(conn, model_id=mid, approved_by="owner@test")
        assert prom["ok"] is True, prom
        assert prom["comparison"]["record_source"] == FMD.SOURCE_OBSERVATIONS
        assert (await FMD.approved(conn))["model"]["model_id"] == mid

        # A CORRECTED SETTLEMENT ON A TRAINING OBSERVATION WITHDRAWS IT
        flip = next(s for s, p in train_px.items() if "-h-" in s and p == 1.0)
        train_px[flip] = 0.0
        got = await _label(conn, train_px, at=t0 + 86400 + 10)
        # (re-read inside the correction window)
        assert got["corrected"] >= 1, got
        gone = await FMD.approved(conn)
        assert gone["ok"] is False
        assert gone["refusal"] == FMD.R_APPROVED_MODEL_EVIDENCE_INVALIDATED
        w = await FMD.withdraw_invalidated(conn)
        assert w["withdrawn"] is True


async def test_observations_and_funded_decisions_are_separate_sources():
    async with _conn() as conn:
        await _clean(conn)
        t0 = time.time() - 3 * 86400
        await _cohort(conn, n=10, first_at=t0, prefix="obsrc")
        obs = await FMD.labelled(conn, source=FMD.SOURCE_OBSERVATIONS)
        # nothing labelled yet -- awaiting settlement is not a label
        assert obs["ok"] and obs["n"] == 0
        bad = await FMD.labelled(conn, source="WHATEVER")
        assert bad["ok"] is False
        gen = await FMD.generate_candidate(conn, now=time.time(),
                                           source="WHATEVER")
        assert gen["ok"] is False


# ═════════════════════════════════════════════════════════════════════
# 4 · THE CYCLE RUNS IT, AND THE PRICE SAYS HOW CURRENT IT WAS
# ═════════════════════════════════════════════════════════════════════

def test_the_entry_cycle_runs_the_observer():
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as L
    src = inspect.getsource(L.cycle)
    assert "observable.append(" in src
    assert "_pair_observation_pass(conn, observable" in src
    assert '"pair_observation": pair_observation' in src
    body = inspect.getsource(L._pair_observation_pass)
    assert "observation_quote(" in body and "_venue_prose" in body


async def test_the_production_pass_observes_through_the_loops_own_readers(
        monkeypatch):
    from sportsassets.workers import ext_pinnacle_loop as L

    quote = HW._quoter(PRICES)

    async def _q(slug, side, *, now=None):
        return await quote(slug, side)
    monkeypatch.setattr(L, "observation_quote", _q)
    monkeypatch.setattr(L, "_venue_prose", HW._prose_reader())
    async with _conn() as conn:
        await _clean(conn)
        await HW._catalogue(conn)
        got = await L._pair_observation_pass(conn, [(HW.HELD, LONG),
                                                    (HW.HELD, LONG)],
                                             now=NOW)
        assert got["ok"] is True, got
        assert got["candidates_offered"] == 1, "duplicates are one candidate"
        assert got["observed"][0]["recorded"], got
        assert got["sent_anything"] is False
        assert got["promoted_anything"] is False
        assert got["generate"]["reason"] == "TOO_FEW_TRAINING_EVENTS"


def test_an_observation_price_carries_its_currency_and_orders_cannot_use_it(
        monkeypatch):
    import asyncio

    from sportsassets.workers import ext_pinnacle_loop as L

    fn = L._real_observation_quote    # set aside by the conftest guard
    lvl = lambda p, q: {"px": {"value": "%.2f" % p, "currency": "USD"},  # noqa: E731
                        "qty": str(q)}
    monkeypatch.setattr(L, "_read_book_blocking", lambda slug: {
        "marketData": {"offers": [lvl(0.41, 50), lvl(0.43, 80)],
                       "bids": [lvl(0.39, 40)],
                       "transactTime": "2026-09-29T12:00:00Z"}})
    got = asyncio.run(fn("aec-x", LONG, now=time.time()))
    assert got["ok"] is True, got
    assert got["price"] == pytest.approx(0.41)
    assert got["book_currency"]["verdict"] != "ESTABLISHED"
    assert got["usable_for_orders"] is False
    assert "NOT AN ORDER PRICE" in got["what_this_is"]
    bad = asyncio.run(fn("aec-x", "ORDER_INTENT_SELL_LONG", now=time.time()))
    assert bad["ok"] is False



async def test_the_labeller_rotates_so_nothing_is_starved():
    """LEAST RECENTLY READ FIRST. With room for 3 reads a pass, 9 unsettled
    observations are each read once in 3 passes -- not the same 3 forever."""
    async with _conn() as conn:
        await _clean(conn)
        ids, _ = await _cohort(conn, n=9, first_at=NOW, prefix="obrot")
        reads: list = []

        def reader(slug):
            reads.append(slug)
            return {"status": LR.PENDING, "settlement_price": None}
        for k in range(3):
            await PO.label_pending(conn, now=NOW + 100 + k, limit=3,
                                   recheck=0, settlement_reader=reader)
        primaries = sorted({s for s in reads if "-p-" in s})
        assert len(primaries) == 9, primaries

"""THE GROSS EDGE'S INPUTS ARE VALIDATED PER DECISION; A VALUATION WITHOUT A
SOURCE INSTANT NO LONGER COLLAPSES FOR EVER (P0 incident, inc-edge).

OWNER DECISION 2026-10-04 (binding): keep the BELOW_MIN_GROSS_EDGE threshold
(991 rows / 33 markets a day on 191b299) but validate its inputs. Every
decision that reaches the gross-edge step now carries a VALIDATION RECEIPT
(`gross_edge_inputs`): p is the held side's probability (the de-vig
recomputed from the row's own Pinnacle prices), the price is the side a BUY
consumes (ask for a long, 1 - bid for a short), the fee evaluates, the
Pinnacle age was inside its unchanged 30 s rule at the decision instant and
the book is inside its unchanged bound. A decision whose inputs fail is a
SOFTWARE refusal with the failing check's code -- never BELOW_MIN_GROSS_EDGE.

AND THE OBSERVATION KEY (migration 251, second half). A valuation the de-vig
refused before aging (football: MARKET_NOT_IN_SUPPORTED_SET) carried
observed_at NULL, and the uniqueness key coalesced NULL to -infinity, so the
first such row of a contract was its only one for ever -- the NFL slate
stopped valuing at 13:28Z on 2026-10-04. The de-vig now records the quote's
source instant on those refusals and the key no longer collapses NULL rows.

The rows are production-shaped and written by the real writers: the recorded
Greece v Germany decision papercg:91f66dd61a308f61f5b4c244 (its raw odds, its
stored power probability, its recorded book levels), `ext.evaluate` +
`ext.persist`, then one real paper pass. No real money, no venue order.
"""
from __future__ import annotations

import asyncio
import copy
import pathlib
import time

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import gross_edge_inputs as GEI
from sportsassets import refusal_taxonomy as RT
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR
from sportsassets.workers import ext_pinnacle_loop as loop

from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests import test_the_other_side_of_the_contract_is_valued as OS

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
CG = PB.CG_STRATEGY
LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
SLUG = "atc-unl-gre-ger-2026-10-04-gre"


def _fee(px):
    # the deployed schedule, per contract (0.0695 x p x (1 - p))
    return 0.0695 * px * (1.0 - px)


def _row(rec):
    """The persisted row's fields the validation reads, as `persist` writes
    them from the record (raw_odds, devig_method, mapped_outcome, payout
    naming, probability, buy_intent)."""
    c = rec["contract"]
    return {"contract_selection": c["selection"],
            "payout_event": rec["payout_event"],
            "payout_is_complement": rec["payout_is_complement"],
            "buy_intent": c["buy_intent"],
            "raw_odds": rec["valuation"].get("raw_odds"),
            "devig_method": rec["devig_method"],
            "mapped_outcome": rec["mapped_outcome"],
            "probability": rec["probability"]}


def _levels(offers, bids, side):
    from sportsassets import bettor_paper_simulator as SIM
    md = {"offers": offers, "bids": bids}
    lv = SIM.levels_for(md, direction="BUY", holding_side=side)
    return md, lv


def _validate(rec, *, side, offers=OS.GRE_OFFERS, bids=OS.GRE_BIDS,
              pin=None, levels=None, consumed=None, obs_at=None, fee=_fee,
              now=1000.0):
    md, lv = _levels(offers, bids, side)
    return GEI.validate(
        p=rec["probability"], side=side, row=_row(rec),
        levels=lv["levels"] if levels is None else levels,
        consumed_side=lv["side"] if consumed is None else consumed, md=md,
        fee_per_contract=fee,
        pin=pin or {"age_s": 5.0, "limit_s": 30.0, "qualified": True},
        decided_at=now, edge_at=now + 2.0,
        book_observed_at=now + 1.0 if obs_at is None else obs_at,
        book_max_age_s=PB.BOOK_MAX_AGE_S, threshold_edge_pp=0.5)


def _home_and_complement(now=None):
    now = time.time() if now is None else now
    rec, contract, evq, vq, extra = OS._home_record(
        SLUG, OS.GRE_ODDS, OS.GRE_OFFERS, OS.GRE_BIDS, now=now)
    comp, why = OS._complement(rec, contract, evq, vq, extra, now=now)
    assert why is None
    return rec, comp


def _codes(receipt):
    return {c["check"]: c["passed"] for c in receipt["checks"]}


# ═════════════════════════════════════════════════════════════════════
# 1 · THE RECEIPT, PURE, ON THE RECORDED ROW
# ═════════════════════════════════════════════════════════════════════

def test_the_recorded_home_and_complement_inputs_validate():
    """The recorded BELOW_MIN_GROSS_EDGE row: every input is what it claims
    to be, so that refusal is genuine economics. The complement (SHORT, on
    the bids at 1 - bid) validates on the same book."""
    rec, comp = _home_and_complement()
    r = _validate(rec, side="LONG")
    assert r["ok"], r
    assert _codes(r) == {"PROBABILITY": True, "ORIENTATION": True,
                         "PRICE_SIDE": True, "BOOK_NOT_CROSSED": True,
                         "FEE": True, "PINNACLE_AGE": True, "BOOK_AGE": True}
    o = next(c for c in r["checks"] if c["check"] == "ORIENTATION")
    assert o["expected"] == pytest.approx(OS.GRE_P_STORED, abs=1e-12)
    ps = next(c for c in r["checks"] if c["check"] == "PRICE_SIDE")
    assert ps["expected_side"] == "offers" and ps["value"] == 0.16
    c = _validate(comp, side="SHORT")
    assert c["ok"], c
    ps = next(x for x in c["checks"] if x["check"] == "PRICE_SIDE")
    assert ps["expected_side"] == "bids"
    assert ps["value"] == pytest.approx(0.85)          # 1 - best bid 0.15
    o = next(x for x in c["checks"] if x["check"] == "ORIENTATION")
    assert o["expected"] == pytest.approx(1.0 - OS.GRE_P_STORED, abs=1e-12)
    assert r["threshold_edge_pp"] == 0.5               # echoed, unchanged


def test_a_probability_not_oriented_to_the_held_side_is_named():
    """The double-inversion the lane warns about: a complement row carrying
    p(selection) instead of 1 - p. Recomputed from the row's own prices, it
    is refused by name."""
    _rec, comp = _home_and_complement()
    bad = copy.deepcopy(comp)
    bad["probability"] = OS.GRE_P_STORED              # p(Greece) on NOT(Greece)
    r = _validate(bad, side="SHORT")
    assert r["refusals"] == [GEI.R_ORIENTATION]
    # a payout naming that contradicts the complement flag
    bad2 = copy.deepcopy(comp)
    bad2["payout_event"] = "Greece"
    assert _validate(bad2, side="SHORT")["refusals"] == [GEI.R_ORIENTATION]
    # the held side is not the row's buy intent
    rec, _ = _home_and_complement()
    assert _validate(rec, side="SHORT", offers=OS.GRE_OFFERS)[
        "refusals"][0] == GEI.R_ORIENTATION


def test_a_row_without_its_pinnacle_prices_is_unverifiable_not_assumed():
    rec, _ = _home_and_complement()
    bad = copy.deepcopy(rec)
    bad["valuation"]["raw_odds"] = {}
    assert _validate(bad, side="LONG")["refusals"] == [
        GEI.R_ORIENTATION_UNVERIFIABLE]
    bad = copy.deepcopy(rec)
    bad["mapped_outcome"] = None
    assert _validate(bad, side="LONG")["refusals"] == [
        GEI.R_ORIENTATION_UNVERIFIABLE]


def test_the_price_must_be_the_side_a_buy_consumes():
    rec, _ = _home_and_complement()
    # a LONG priced off the BIDS (the wrong side of the book)
    _md, wrong = _levels(OS.GRE_OFFERS, OS.GRE_BIDS, "SHORT")
    r = _validate(rec, side="LONG", levels=wrong["levels"],
                  consumed=wrong["side"])
    assert GEI.R_PRICE_SIDE in r["refusals"]
    # the right side named but a level that is not the best offer first
    _md, right = _levels(OS.GRE_OFFERS, OS.GRE_BIDS, "LONG")
    shuffled = list(reversed(right["levels"]))
    assert GEI.R_PRICE_SIDE in _validate(
        rec, side="LONG", levels=shuffled)["refusals"]


def test_a_crossed_book_is_not_an_economic_input():
    rec, _ = _home_and_complement()
    bids = [OS._lv(0.18, 500.0)] + OS.GRE_BIDS        # best bid above 0.16
    r = _validate(rec, side="LONG", bids=bids)
    assert r["refusals"] == [GEI.R_BOOK_CROSSED]


def test_the_fee_must_evaluate():
    rec, _ = _home_and_complement()

    def boom(px):
        raise ValueError("fee schedule unreadable")
    assert _validate(rec, side="LONG", fee=boom)["refusals"] == [GEI.R_FEE]
    assert _validate(rec, side="LONG", fee=lambda px: float("nan"))[
        "refusals"] == [GEI.R_FEE]
    assert _validate(rec, side="LONG", fee=lambda px: -0.01)[
        "refusals"] == [GEI.R_FEE]


def test_the_pinnacle_age_rule_is_the_existing_one_at_the_decision_instant():
    rec, _ = _home_and_complement()
    over = {"age_s": 30.5, "limit_s": 30.0, "qualified": False}
    assert _validate(rec, side="LONG", pin=over)["refusals"] == [
        GEI.R_PINNACLE_AGE]
    unknown = {"age_s": None, "limit_s": 30.0, "qualified": False}
    assert _validate(rec, side="LONG", pin=unknown)["refusals"] == [
        GEI.R_PINNACLE_AGE]
    # 29 s at the decision instant passes; the age when the edge was
    # computed (2 s later, 31 s) is RECORDED and gates nothing -- the 30 s
    # rule's instant is unchanged
    edge = {"age_s": 29.0, "limit_s": 30.0, "qualified": True}
    r = _validate(rec, side="LONG", pin=edge)
    assert r["ok"]
    pa = next(c for c in r["checks"] if c["check"] == "PINNACLE_AGE")
    assert pa["age_when_the_edge_was_computed_s"] == pytest.approx(31.0)
    assert pa["age_when_the_edge_was_computed_gates_nothing"] is True


def test_the_book_age_rule_is_the_policys_bound():
    rec, _ = _home_and_complement()
    r = _validate(rec, side="LONG", obs_at=1000.0 + 2.0 - PB.BOOK_MAX_AGE_S
                  - 0.5)
    assert r["refusals"] == [GEI.R_BOOK_AGE]
    md, lv = _levels(OS.GRE_OFFERS, OS.GRE_BIDS, "LONG")
    r = GEI.validate(p=rec["probability"], side="LONG", row=_row(rec),
                     levels=lv["levels"], consumed_side=lv["side"], md=md,
                     fee_per_contract=_fee,
                     pin={"age_s": 5.0, "limit_s": 30.0, "qualified": True},
                     decided_at=1000.0, edge_at=1001.0,
                     book_observed_at=None, book_max_age_s=PB.BOOK_MAX_AGE_S)
    assert r["refusals"] == [GEI.R_BOOK_AGE]


def test_every_validation_code_is_software_and_the_threshold_is_economic():
    for code in GEI.REFUSALS:
        k = RT.classify(code)
        assert k["classified"] and k["class"] == RT.SOFTWARE, k
        assert RT.decision_class("REFUSE", [code]) == RT.REJECTED_SOFTWARE
    assert RT.classify(PB.R_EDGE)["class"] == RT.ECONOMIC
    assert RT.decision_class("REFUSE", [PB.R_EDGE]) == RT.REJECTED_ECONOMIC
    # never both: a failed input is not ALSO an economic refusal
    assert RT.decision_class("REFUSE", [GEI.R_ORIENTATION, PB.R_EDGE]) == \
        RT.REJECTED_SOFTWARE


def test_the_validation_module_is_pure():
    import ast
    tree = ast.parse(pathlib.Path(GEI.__file__).read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mods |= {node.module or ""} | {a.name for a in node.names}
        elif isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
    assert mods <= {"__future__", "annotations", "math", "json", "",
                    "bettor_book_snapshot", "bettor_pinnacle_devig"}, mods


# ═════════════════════════════════════════════════════════════════════
# 2 · THROUGH THE REAL WRITER AND THE REAL PAPER PASS
# ═════════════════════════════════════════════════════════════════════

async def _nosleep(_):
    return None


@pytest.fixture
def cg_on(monkeypatch, new_strategies_off):
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PL.set_policy_control(PB.CG_POLICY["control_key"], True)
    PL.set_policy_control(PB.CONTROL_KEY, False)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    yield
    PB._CONTEXT_CACHE.clear()


async def _decide(conn, recs, *, offers, bids, label):
    now = time.time() + 5.0
    await PL.purge_everything(conn)
    await PL.purge_research_models(conn)
    acct = await PL.new_account(conn, label, now=now)
    ids = [await ext.persist(conn, copy.deepcopy(r)) for r in recs]
    assert all(ids)
    t = PL.Transport(now)
    t.books[SLUG] = {"offers": offers, "bids": bids}
    client = PL.client(t)
    t.t = max(t.t, now)
    out = await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                              market_data=client, config=acct["config"],
                              force=True, fee_fn=None, sleep=_nosleep)
    assert out["ran"] and not out["errors"], out["errors"]
    assert client.mutation_attempts == 0
    decs = []
    for vid in ids:
        d = await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
            " valuation_id=$2 AND strategy=$3", acct["session_id"], vid, CG)
        assert d is not None
        decs.append(d)
    return acct, decs


@pg
async def test_the_recorded_below_min_gross_edge_carries_a_passing_receipt(
        cg_on):
    """The recorded production decision, re-decided by the real pass: still
    BELOW_MIN_GROSS_EDGE at -1.79 pp (threshold 0.5 unchanged), and now with
    a receipt showing every input validated -- the refusal is economics."""
    conn = await H.connect()
    try:
        rec, comp = _home_and_complement(time.time() + 3.0)
        acct, (h, c) = await _decide(conn, [rec, comp], offers=OS.GRE_OFFERS,
                                     bids=OS.GRE_BIDS, label="geiok")
        assert h["refusal"] == PB.R_EDGE, h["refusals"]
        pdx = H.j(h["policy_decision"])
        assert pdx["threshold_edge_pp"] == 0.5
        assert pdx["gross_edge_inputs"]["ok"] is True
        assert all(pdx["gross_edge_inputs"]["checks"].values())
        receipt = H.j(h["economics"])["gross_edge_inputs"]
        assert receipt["version"] == GEI.VERSION and receipt["ok"]
        cond = {x["condition"]: x for x in pdx["conditions"]}
        assert cond["gross_edge_inputs_validated"]["passed"] is True
        assert RT.decision_class(h["verdict"], h["refusals"]) == \
            RT.REJECTED_ECONOMIC
        # the complement, on the bids, also validated and decided economically
        assert H.j(c["policy_decision"])["gross_edge_inputs"]["ok"] is True
        assert c["refusal"] == PB.R_FEES_CONSUME_EDGE
    finally:
        await PL.purge_everything(conn)
        await OS._clean(conn, SLUG)
        await conn.close()


@pg
async def test_a_mis_oriented_row_is_a_software_refusal_not_an_edge_refusal(
        cg_on):
    """A complement row persisted with p(selection) -- the double inversion
    -- would have been recorded BELOW_MIN_GROSS_EDGE (0.142 against 0.85).
    Now it is refused by the orientation check, SOFTWARE, with its receipt;
    no edge is judged on it and nothing is ordered."""
    conn = await H.connect()
    try:
        _rec, comp = _home_and_complement(time.time() + 3.0)
        bad = copy.deepcopy(comp)
        bad["probability"] = OS.GRE_P_STORED
        acct, (d,) = await _decide(conn, [bad], offers=OS.GRE_OFFERS,
                                   bids=OS.GRE_BIDS, label="geibad")
        assert d["verdict"] == "REFUSE"
        assert d["refusals"] == [GEI.R_ORIENTATION], d["refusals"]
        assert PB.R_EDGE not in d["refusals"]
        pdx = H.j(d["policy_decision"])
        assert pdx["gross_edge_pp"] is None
        receipt = H.j(d["economics"])["gross_edge_inputs"]
        assert receipt["ok"] is False
        assert receipt["unvalidated_best_level_edge_pp"] < 0
        assert RT.decision_class(d["verdict"], d["refusals"]) == \
            RT.REJECTED_SOFTWARE
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE session_id=$1",
            acct["session_id"]) == 0
    finally:
        await PL.purge_everything(conn)
        await OS._clean(conn, SLUG)
        await conn.close()


@pg
async def test_a_crossed_production_book_is_a_software_refusal(cg_on):
    conn = await H.connect()
    try:
        rec, _ = _home_and_complement(time.time() + 3.0)
        bids = [OS._lv(0.18, 500.0)] + OS.GRE_BIDS
        acct, (d,) = await _decide(conn, [rec], offers=OS.GRE_OFFERS,
                                   bids=bids, label="geicross")
        assert d["refusals"] == [GEI.R_BOOK_CROSSED], d["refusals"]
        assert RT.decision_class(d["verdict"], d["refusals"]) == \
            RT.REJECTED_SOFTWARE
    finally:
        await PL.purge_everything(conn)
        await OS._clean(conn, SLUG)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · ONE VALUATION PER OBSERVATION: NO MORE COLLAPSE ON NULL
# ═════════════════════════════════════════════════════════════════════

NFL_ODDS = {"Philadelphia Eagles": 1.62, "Denver Broncos": 2.40}


def _nfl_record(observed_at, *, slug, now):
    """An NFL money-line valuation exactly as the cycle builds it today: a
    calibration-only record (the venue book's currency is not established)
    whose de-vig refuses MARKET_NOT_IN_SUPPORTED_SET before its aging step,
    because football is not in the de-vig's measured set."""
    contract = OS._contract(slug, family="football",
                            selection="Philadelphia Eagles")
    q = OS._ev_quote(NFL_ODDS, observed_at=(observed_at or 0.0),
                     event_key=contract["event_key"])
    if observed_at is None:
        q["observed_at"] = q["received_at"] = None
    shown = OS._displayed(OS.GRE_OFFERS, OS.GRE_BIDS, LONG)
    basis = {"refusal": loop.R_BOOK_CURRENCY_NOT_ESTABLISHED,
             "displayed": dict(shown, usable_for_orders=False),
             "book_currency": {"verdict": "NOT_ESTABLISHED"}}
    return ext.evaluate(
        contract=contract, quote=q,
        market_state=loop._displayed_market_state(basis),
        execution_plan=None,
        execution_estimate={"p_fill": None, "basis": "P_FILL_NOT_IDENTIFIED",
                            "crossing": True},
        size=None, risk={"permitted": False,
                         "reason": "NO_EXECUTION_PLAN_WAS_BUILT"},
        fee_fn=lambda qty, price: 0.0695 * qty * price * (1 - price),
        now=now, outcome_books=3, armed=True, payout_is_complement=False,
        extra_refusals=[loop.R_BOOK_CURRENCY_NOT_ESTABLISHED],
        record_purpose=ext.PURPOSE_CALIBRATION_ONLY,
        calibration_only_evidence={
            "venue_read_refusal": basis["refusal"],
            "book_currency": basis["book_currency"],
            "displayed_quote": basis["displayed"],
            "decision_instant_epoch_s": now, "decision_lag_s": 0.5})


def test_a_refusal_before_aging_records_the_quotes_source_instant():
    now = time.time()
    rec = _nfl_record(now - 7.0, slug="aec-nfl-phi-den-x", now=now)
    v = rec["valuation"]
    assert v["refusals"][0] == devig.R_UNSUPPORTED_MARKET
    assert v["probability"] is None and rec["probability"] is None
    assert v["observed_at"] == pytest.approx(now - 7.0)
    assert v["observed_at_basis"] == devig.OBSERVED_AT_NOT_AGED
    assert v.get("age_s") is None                      # never aged
    assert rec["observed_at"] == pytest.approx(now - 7.0)
    # a quote with no readable instant records none, and still refuses
    rec2 = _nfl_record(None, slug="aec-nfl-phi-den-x", now=now)
    assert rec2["observed_at"] is None
    assert rec2["valuation"]["refusals"][0] == devig.R_UNSUPPORTED_MARKET


@pg
def test_the_old_key_collapsed_every_later_nfl_valuation_and_251_keeps_them():
    """THE 13:28Z DEFECT, reproduced: under migration 106's key, two NFL
    valuations of one contract with NULL observed_at are one row for ever.
    Under 251 (and the de-vig's source instant) each provider instant is its
    own row, an unchanged re-read is still one row, and a quote with no
    instant at all is one row per evaluation -- never one row for ever."""
    async def go():
        conn = await H.connect()
        slug = "aec-nfl-phi-den-2026-10-04-k%d" % int(time.time() * 1000)
        try:
            now = time.time()
            a = _nfl_record(None, slug=slug, now=now)
            b = _nfl_record(None, slug=slug, now=now + 60.0)
            tr = conn.transaction()
            await tr.start()
            try:
                # the rollback's own guard needs a table 106 can index
                await conn.execute(
                    "DELETE FROM external_valuations WHERE "
                    "payout_is_complement OR observed_at IS NULL")
                await conn.execute((MIG / "rollback" /
                                    "251_external_valuations_one_per_"
                                    "observation_per_side.down.sql")
                                   .read_text())
                assert await ext.persist(conn, copy.deepcopy(a))
                # BEFORE: the second evaluation is swallowed as a duplicate
                assert await ext.persist(conn, copy.deepcopy(b)) is None
            finally:
                await tr.rollback()
            # AFTER: no instant at all -> one row per evaluation
            ia = await ext.persist(conn, copy.deepcopy(a))
            await asyncio.sleep(0.01)
            ib = await ext.persist(conn, copy.deepcopy(b))
            assert ia and ib and ia != ib
            # with the source instant the de-vig now records: one row per
            # provider instant, and an unchanged re-read collapses
            t1 = _nfl_record(now - 20.0, slug=slug, now=now)
            t1b = _nfl_record(now - 20.0, slug=slug, now=now + 30.0)
            t2 = _nfl_record(now - 5.0, slug=slug, now=now + 45.0)
            i1 = await ext.persist(conn, copy.deepcopy(t1))
            assert i1
            assert await ext.persist(conn, copy.deepcopy(t1b)) is None
            i2 = await ext.persist(conn, copy.deepcopy(t2))
            assert i2 and i2 != i1
            rows = await conn.fetch(
                "SELECT observed_at, probability, refusals FROM "
                "external_valuations WHERE us_market_slug=$1", slug)
            assert len(rows) == 4
            assert all(r["probability"] is None for r in rows)
            assert all(list(r["refusals"])[0] == devig.R_UNSUPPORTED_MARKET
                       for r in rows)
        finally:
            await OS._clean(conn, slug)
            await conn.close()

    asyncio.run(go())


@pg
def test_251_builds_over_history_without_rewriting_a_row():
    """Every row unique under 106's key is unique under 251's: the index is
    rebuilt in place over the rows already present and no row changes."""
    async def go():
        conn = await H.connect()
        try:
            tr = conn.transaction()
            await tr.start()
            try:
                before = await conn.fetchval(
                    "SELECT md5(string_agg(id::text || ':' || "
                    "coalesce(observed_at::text, 'NULL'), ',' ORDER BY id)) "
                    "FROM external_valuations")
                n = await conn.fetchval(
                    "SELECT count(*) FROM external_valuations")
                await conn.execute((MIG / "251_external_valuations_one_per_"
                                    "observation_per_side.sql").read_text())
                after = await conn.fetchval(
                    "SELECT md5(string_agg(id::text || ':' || "
                    "coalesce(observed_at::text, 'NULL'), ',' ORDER BY id)) "
                    "FROM external_valuations")
                assert after == before
                assert await conn.fetchval(
                    "SELECT count(*) FROM external_valuations") == n
                d = await conn.fetchval(
                    "SELECT indexdef FROM pg_indexes WHERE indexname="
                    "'external_valuations_one_per_observation'")
                assert "buy_intent" in d and "observed_at IS NULL" in d
                assert "infinity" not in d
            finally:
                await tr.rollback()
        finally:
            await conn.close()

    asyncio.run(go())

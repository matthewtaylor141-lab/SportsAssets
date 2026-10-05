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
    because football is not in the de-vig's measured set.

    (integration) Since the R30A NFL stream the NFL and college money lines
    ARE admitted, by their own measurements (bettor_pinnacle_devig.
    SUPPORTED_BY_LEAGUE nfl / cfb), so an `aec-nfl-` slug no longer refuses
    before aging. The callers' slugs now name a football league that no
    measurement admitted (`aec-ufl-`): the same refusal-before-aging path
    the 13:28Z defect took, which every non-admitted football league still
    takes."""
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
    rec = _nfl_record(now - 7.0, slug="aec-ufl-phi-den-x", now=now)
    v = rec["valuation"]
    assert v["refusals"][0] == devig.R_UNSUPPORTED_MARKET
    assert v["probability"] is None and rec["probability"] is None
    assert v["observed_at"] == pytest.approx(now - 7.0)
    assert v["observed_at_basis"] == devig.OBSERVED_AT_NOT_AGED
    assert v.get("age_s") is None                      # never aged
    assert rec["observed_at"] == pytest.approx(now - 7.0)
    # a quote with no readable instant records none, and still refuses
    rec2 = _nfl_record(None, slug="aec-ufl-phi-den-x", now=now)
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
        slug = "aec-ufl-phi-den-2026-10-04-k%d" % int(time.time() * 1000)
        try:
            now = time.time()
            a = _nfl_record(None, slug=slug, now=now)
            b = _nfl_record(None, slug=slug, now=now + 60.0)
            tr = conn.transaction()
            await tr.start()
            try:
                # the rollback's own guard needs a table 106 can index (and
                # no row labelled with its clock, which it refuses to drop)
                await conn.execute(
                    "DELETE FROM external_valuations WHERE "
                    "payout_is_complement OR observed_at IS NULL "
                    "OR observed_at_basis IS NOT NULL")
                await conn.execute((MIG / "rollback" /
                                    "251_external_valuations_one_per_"
                                    "observation_per_side.down.sql")
                                   .read_text())
                assert await conn.fetchval(
                    "SELECT count(*) FROM information_schema.columns WHERE "
                    " table_name='external_valuations' AND "
                    " column_name='observed_at_basis'") == 0
                # THE KEY is what this half reproduces: the writer under test
                # also writes 251's label column, put back bare (no CHECK)
                # inside this rolled-back transaction
                await conn.execute("ALTER TABLE external_valuations ADD "
                                   "COLUMN observed_at_basis text")
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


# ═════════════════════════════════════════════════════════════════════
# 4 · THE REVIEW OF 7bd084b: WHAT THE VALIDATION ITSELF GOT WRONG
# ═════════════════════════════════════════════════════════════════════
#
# Two checks would have turned real candidates into SOFTWARE refusals:
#
#   OFF-CENT BOOKS. PRICE_SIDE compared the ladder's first level with the
#   best RAW level, but the simulator's ladder (`levels_for`) drops every
#   level whose wire price is not a whole cent -- the adapter formats every
#   price %.2f (ADAPTER_CENT_GRID), so such a level cannot be traded by us.
#   The venue quotes them: MLB tick 0.005, NFL tick 0.001 (pmx.py). A book
#   whose best offer was 0.535 was refused GROSS_EDGE_INPUT_PRICE_NOT_THE_
#   BUY_SIDE_OF_THE_BOOK although the ladder correctly started at 0.54.
#
#   A DECLARED VENUE CONVERSION. The in-flight NFL stream (wt_r30a_nfl
#   966903f, paper_benchmark.apply_venue_conversion) replaces pin["p"] for
#   an NFL money line with (1 - t) p + 0.5 t BEFORE the edge, keeping the
#   book's number as p_book_conditional_no_tie. PROBABILITY required p to
#   equal the row's stored value within 1e-9, so after integration every
#   NFL decision would have been refused -- 0 NFL ENTERs, the original P0.

OFF_CENT_OFFERS = [OS._lv(0.155, 40.0)] + OS.GRE_OFFERS
OFF_CENT_BIDS = [OS._lv(0.155, 60.0)] + OS.GRE_BIDS


def test_an_off_cent_best_level_the_adapter_cannot_trade_is_not_a_defect():
    rec, comp = _home_and_complement()
    md, lv = _levels(OFF_CENT_OFFERS, OS.GRE_BIDS, "LONG")
    assert lv["excluded_off_cent_grid"] == 1
    assert lv["levels"][0]["price"] == 0.16
    r = _validate(rec, side="LONG", offers=OFF_CENT_OFFERS)
    assert r["ok"], r["refusals"]
    ps = next(c for c in r["checks"] if c["check"] == "PRICE_SIDE")
    assert ps["value"] == ps["expected"] == 0.16
    # the level the adapter cannot trade is NAMED on the receipt, not hidden
    assert ps["off_cent_levels_better_than_the_first_tradable"] == [
        {"price": 0.155, "qty": 40.0}]
    # the SHORT side: a sub-cent best BID (0.155) is not ours to hit either
    c = _validate(comp, side="SHORT", bids=OFF_CENT_BIDS)
    assert c["ok"], c["refusals"]
    ps = next(x for x in c["checks"] if x["check"] == "PRICE_SIDE")
    assert ps["value"] == pytest.approx(0.85)          # 1 - best cent bid
    assert ps["expected"] == pytest.approx(0.85)
    # a ladder that skips a TRADABLE better level is still refused
    _md, right = _levels(OS.GRE_OFFERS, OS.GRE_BIDS, "LONG")
    assert GEI.R_PRICE_SIDE in _validate(
        rec, side="LONG", offers=OFF_CENT_OFFERS,
        levels=right["levels"][1:])["refusals"]


def test_the_verifiers_mlb_off_cent_reproduction_now_validates():
    """scratchpad incedge_v/offcent.py, verbatim in substance: p = 0.573,
    offers 0.535 x 40 and 0.54 x 100 (MLB tick 0.005): the ladder starts at
    0.54 (a 3.3 pp edge) and every input validates."""
    import json
    from sportsassets import bettor_paper_simulator as SIM
    md = {"offers": [{"px": {"value": "0.535"}, "qty": "40"},
                     {"px": {"value": "0.54"}, "qty": "100"}],
          "bids": [{"px": {"value": "0.52"}, "qty": "100"}]}
    lv = SIM.levels_for(md, direction="BUY", holding_side="LONG")
    odds = {"Home": 1.70, "Away": 2.25}
    names = sorted(odds)
    p = dict(zip(names, devig.devig([odds[n] for n in names],
                                    devig.DEFAULT_METHOD)))["Home"]
    row = {"contract_selection": "Home", "payout_event": "Home",
           "payout_is_complement": False, "buy_intent": LONG,
           "raw_odds": json.dumps(odds), "mapped_outcome": "Home",
           "devig_method": devig.DEFAULT_METHOD, "probability": p}
    r = GEI.validate(p=p, side="LONG", row=row, levels=lv["levels"],
                     consumed_side=lv["side"], md=md,
                     fee_per_contract=lambda px: 0.01,
                     pin={"age_s": 5.0, "limit_s": 30.0, "qualified": True},
                     decided_at=1000.0, edge_at=1000.5,
                     book_observed_at=999.0, book_max_age_s=30.0)
    assert r["ok"], r["refusals"]
    assert lv["levels"][0]["price"] == 0.54


def test_the_cent_grid_is_the_simulators_own():
    """One rule, never two: the validation's grid predicate agrees with the
    simulator's on every tick the venue quotes."""
    from sportsassets import bettor_paper_simulator as SIM
    for milli in range(1, 1000):
        px = milli / 1000.0
        assert GEI.on_adapter_grid(px) == SIM._cent(px), px


NFL_TWO_WAY = {"Kansas City Chiefs": 1.60, "Denver Broncos": 2.45}


def _nfl_row():
    import json
    names = sorted(NFL_TWO_WAY)
    p = dict(zip(names, devig.devig([NFL_TWO_WAY[n] for n in names],
                                    devig.DEFAULT_METHOD)))[
        "Kansas City Chiefs"]
    return {"contract_selection": "Kansas City Chiefs",
            "payout_event": "Kansas City Chiefs",
            "payout_is_complement": False, "buy_intent": LONG,
            "raw_odds": json.dumps(NFL_TWO_WAY),
            "mapped_outcome": "Kansas City Chiefs",
            "devig_method": devig.DEFAULT_METHOD, "probability": p}


def _nfl_converted_pin(p_book, *, t=0.004, lo=0.001):
    """THE PIN EXACTLY AS THE NFL STREAM LEAVES IT (wt_r30a_nfl 966903f,
    paper_benchmark.apply_venue_conversion + bettor_nfl_settlement.
    venue_value): pin["p"] is the venue payout equivalent at the worst end
    of the cited tie-rate interval, the book's number is kept beside it."""
    worst = round((1.0 - t) * p_book + 0.5 * t, 12)
    rec = {"applies": True, "version": "NFL_SETTLEMENT_V1", "p": worst,
           "refusal": None, "p_book_conditional_no_tie": p_book,
           "tie_payout_per_contract": 0.5, "tie_rate_used": t,
           "tie_rate_end_used": "HIGHEST", "tie_rate_interval": [lo, t],
           "p_venue_at_interval_ends": [
               round((1.0 - lo) * p_book + 0.5 * lo, 12), worst],
           "formula": "p_venue = (1 - t) * p_book + 0.50 * t"}
    return {"p": worst, "qualified": True, "age_s": 3.0, "limit_s": 30.0,
            "p_book_conditional_no_tie": p_book, "venue_conversion": rec,
            "p_is": ("VENUE_PAYOUT_EQUIVALENT_AT_THE_WORST_END_OF_THE_CITED_"
                     "TIE_RATE_INTERVAL")}


def _nfl_validate(p, pin):
    md = {"offers": [{"px": {"value": "0.55"}, "qty": "100"}],
          "bids": [{"px": {"value": "0.53"}, "qty": "100"}]}
    return GEI.validate(
        p=p, side="LONG", row=_nfl_row(),
        levels=[{"price": 0.55, "wire": 0.55, "qty": 100.0}],
        consumed_side="offers", md=md, fee_per_contract=lambda px: 0.01,
        pin=pin, decided_at=1000.0, edge_at=1000.1,
        book_observed_at=999.5, book_max_age_s=30.0)


def test_a_declared_venue_conversion_is_reproduced_not_refused():
    """scratchpad incedge_v/nflconv.py: row 0.61014, converted 0.609699 was
    refused NOT_A_PROBABILITY. A conversion the decision DECLARES, from the
    row's own probability, by its stated formula, validates -- and is
    re-derived, never trusted."""
    row = _nfl_row()
    pin = _nfl_converted_pin(row["probability"])
    r = _nfl_validate(pin["p"], pin)
    assert r["ok"], r["refusals"]
    checks = {c["check"]: c for c in r["checks"]}
    assert checks["PROBABILITY"]["passed"]
    assert checks["PROBABILITY"]["stored_on_the_row"] == row["probability"]
    assert checks["VENUE_CONVERSION"]["passed"]
    assert checks["ORIENTATION"]["expected"] == pytest.approx(
        row["probability"], abs=1e-12)        # the BOOK's number, oriented


def test_a_conversion_that_does_not_reproduce_is_a_software_refusal():
    row = _nfl_row()
    # the converted value does not follow from its own formula
    pin = _nfl_converted_pin(row["probability"])
    pin["p"] = pin["venue_conversion"]["p"] = pin["p"] + 0.01
    assert _nfl_validate(pin["p"], pin)["refusals"] == [GEI.R_CONVERSION]
    # a tie rate outside its own declared interval
    pin = _nfl_converted_pin(row["probability"])
    pin["venue_conversion"]["tie_rate_interval"] = [0.0, 0.002]
    assert _nfl_validate(pin["p"], pin)["refusals"] == [GEI.R_CONVERSION]
    # the conversion was applied to a number that is not the row's
    pin = _nfl_converted_pin(row["probability"] - 0.02)
    assert _nfl_validate(pin["p"], pin)["refusals"] == [GEI.R_PROBABILITY]
    # a moved p with NO declared conversion is still not the row's value
    plain = {"age_s": 3.0, "limit_s": 30.0, "qualified": True}
    assert _nfl_validate(row["probability"] - 0.0004, plain)[
        "refusals"] == [GEI.R_PROBABILITY]
    # a conversion that says it does not apply changes nothing
    pin = _nfl_converted_pin(row["probability"])
    pin["venue_conversion"]["applies"] = False
    assert _nfl_validate(pin["p"], pin)["refusals"] == [GEI.R_PROBABILITY]
    assert RT.classify(GEI.R_CONVERSION)["class"] == RT.SOFTWARE


# ── DEREK V2 AND THE MAKER: THE SAME RECEIPT ─────────────────────────
#
# The validation covered paper_benchmark.decide_one only (CG, PINNACLE_ONLY).
# Derek V2 still emitted BELOW_MIN_GROSS_EDGE with no receipt
# (derek_policy.R_BELOW) and the maker judged p - L on the resting price
# with none (paper_maker.resting_price).

def _derek_ctx(acct, client, now):
    ctx = {"session": {"session_id": acct["session_id"],
                       "config": acct["config"]},
           "session_id": acct["session_id"], "account_id": acct["account_id"],
           "config": acct["config"], "market_data": client, "books_read": 0,
           "now": now, "deadline": time.monotonic() + 30, "first_fills": [],
           "fills": 0, "fee_fn": None,
           # THE RESEARCH MODEL, injected at its seam (`_context` serves
           # ctx["derek"] as is): fitting one is the Derek harness's proof;
           # this proof is about the inputs of the edge it is blended into.
           "derek": {"model": {"ok": True, "refusal": None,
                               "model_id": "test-model", "features": [],
                               "model_version": "v-test",
                               "approval_status": "CANDIDATE"},
                     "model_attempt": None,
                     "void": {"status": "UNMEASURED"}, "calibration": {}}}
    ctx["clock"] = lambda: ctx["now"]
    return ctx


@pg
async def test_derek_v2_refuses_unvalidated_inputs_as_software(monkeypatch):
    """The double-inverted complement row through Derek's real decision and
    writer: before, BELOW_MIN_GROSS_EDGE (economic); now the orientation
    check's code (SOFTWARE), with its receipt, and no economic verdict."""
    conn = await H.connect()
    monkeypatch.setattr(PD, "score", lambda model, **kw: {
        "ok": True, "p": 0.20, "features": {}, "feature_basis": "test"})
    try:
        now = time.time() + 3.0
        await PL.purge_everything(conn)
        _rec, comp = _home_and_complement(now - 1.0)
        # Derek is the STRICT settlement policy: the soccer per-side text's
        # postponement clause is a genuine payout difference it refuses
        # (SETTLEMENT_NOT_SUPPORTED, before any book read). These rows carry
        # a COMPATIBLE comparison so the decision reaches its gross edge,
        # which is what this proof is about.
        scmp = PL.settlement_comparison("COMPATIBLE")
        bad = copy.deepcopy(comp)
        bad["probability"] = OS.GRE_P_STORED
        bad["settlement_comparison"] = scmp
        vid = await ext.persist(conn, bad)
        okrec = copy.deepcopy(_rec)
        okrec["settlement_comparison"] = scmp
        good = await ext.persist(conn, okrec)
        row = await conn.fetchrow("SELECT * FROM external_valuations "
                                  " WHERE id=$1", vid)
        acct = await PL.new_account(conn, "geiderek", now=now)
        t = PL.Transport(now)
        t.books[SLUG] = {"offers": OS.GRE_OFFERS, "bids": OS.GRE_BIDS}
        client = PL.client(t)
        t.t = max(t.t, now)
        rec = await PD.decide_one(conn, _derek_ctx(acct, client, now),
                                  dict(row))
        d = await conn.fetchrow("SELECT * FROM paper_decisions WHERE "
                                " decision_id=$1", rec["decision_id"])
        assert d["verdict"] == "REFUSE"
        assert list(d["refusals"]) == [GEI.R_ORIENTATION], d["refusals"]
        assert "BELOW_MIN_GROSS_EDGE" not in list(d["refusals"])
        receipt = H.j(d["pinnacle"])["gross_edge_inputs"]
        assert receipt["version"] == GEI.VERSION and receipt["ok"] is False
        assert RT.decision_class(d["verdict"], d["refusals"]) == \
            RT.REJECTED_SOFTWARE
        # the well-formed home row: validated, then judged economically
        grow = await conn.fetchrow("SELECT * FROM external_valuations "
                                   " WHERE id=$1", good)
        acct2 = await PL.new_account(conn, "geiderek2", now=now)
        rec2 = await PD.decide_one(conn, _derek_ctx(acct2, client, now),
                                   dict(grow))
        d2 = await conn.fetchrow("SELECT * FROM paper_decisions WHERE "
                                 " decision_id=$1", rec2["decision_id"])
        r2 = H.j(d2["pinnacle"])["gross_edge_inputs"]
        assert r2["ok"] is True, r2["refusals"]
        assert H.j(d2["policy_decision"])["gross_edge_inputs"]["ok"] is True
        assert not set(d2["refusals"]) & set(GEI.REFUSALS)
        assert client.mutation_attempts == 0
    finally:
        await PL.purge_everything(conn)
        await OS._clean(conn, SLUG)
        await conn.close()


@pytest.fixture
def maker_only(monkeypatch):
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    for k in (CG, PB.MAKER_STRATEGY, PB.EXPLORE_STRATEGY, PB.CONTROL_KEY):
        PL.set_policy_control(k, k == PB.MAKER_STRATEGY)
    yield
    # the migrated launch selection: CG and exploration on, maker and the
    # strict benchmark off
    for k in (CG, PB.MAKER_STRATEGY, PB.EXPLORE_STRATEGY, PB.CONTROL_KEY):
        PL.set_policy_control(k, k in (CG, PB.EXPLORE_STRATEGY))
    PB._CONTEXT_CACHE.clear()


@pg
async def test_the_maker_refuses_unvalidated_inputs_as_software(maker_only):
    conn = await H.connect()
    try:
        now = time.time() + 5.0
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        rec, comp = _home_and_complement(now - 2.0)
        bad = copy.deepcopy(comp)
        bad["probability"] = OS.GRE_P_STORED
        t = PL.Transport(now)
        t.books[SLUG] = {"offers": OS.GRE_OFFERS, "bids": OS.GRE_BIDS}
        client = PL.client(t)

        async def one_pass(tag, record):
            acct = await PL.new_account(conn, tag, now=now)
            vid = await ext.persist(conn, copy.deepcopy(record))
            assert vid
            t.t = max(t.t, now)
            out = await PR.paper_pass(conn, now=now,
                                      account_id=acct["account_id"],
                                      market_data=client,
                                      config=acct["config"], force=True,
                                      fee_fn=None, sleep=_nosleep)
            assert out["ran"] and not out["errors"], out["errors"]
            return await conn.fetchrow(
                "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
                " strategy=$2 AND valuation_id=$3", acct["session_id"],
                PB.MAKER_STRATEGY, vid)
        # the double-inverted row alone (one maker entry per fixture would
        # otherwise answer first, for whichever row the pass decided first)
        d = await one_pass("geimaker", bad)
        assert list(d["refusals"]) == [GEI.R_ORIENTATION], d["refusals"]
        receipt = H.j(d["economics"])["gross_edge_inputs"]
        assert receipt["ok"] is False
        assert H.j(d["economics"])["resting_price"] in ({}, None)
        # the well-formed home row: validated, then priced by the maker rule
        h = await one_pass("geimaker2", rec)
        assert H.j(h["economics"])["gross_edge_inputs"]["ok"] is True
        assert not set(h["refusals"]) & set(GEI.REFUSALS)
        assert client.mutation_attempts == 0
    finally:
        await PL.purge_everything(conn)
        await OS._clean(conn, SLUG)
        await conn.close()


@pg
async def test_an_off_cent_production_book_is_decided_on_its_economics(cg_on):
    """The recorded Greece row on a book whose best offer is a sub-cent
    0.155: still BELOW_MIN_GROSS_EDGE on the tradable 0.16 (economics), not
    a SOFTWARE price-side refusal."""
    conn = await H.connect()
    try:
        rec, _ = _home_and_complement(time.time() + 3.0)
        acct, (d,) = await _decide(conn, [rec], offers=OFF_CENT_OFFERS,
                                   bids=OS.GRE_BIDS, label="geioffcent")
        assert d["refusals"] == [PB.R_EDGE], d["refusals"]
        assert H.j(d["economics"])["gross_edge_inputs"]["ok"] is True
        assert RT.decision_class(d["verdict"], d["refusals"]) == \
            RT.REJECTED_ECONOMIC
    finally:
        await PL.purge_everything(conn)
        await OS._clean(conn, SLUG)
        await conn.close()


# ── THE SOURCE-INSTANT LABEL IS STORED ON THE ROW ────────────────────

@pg
def test_the_source_instant_label_is_stored_on_the_row():
    """bettor_pinnacle_devig sets observed_at_basis QUOTE_SOURCE_INSTANT_NOT_
    AGED on a refusal before aging; the persisted row now carries it (it was
    dropped by `persist`, so only age_s NULL told the cases apart), and the
    table refuses the label on a row that was aged or priced."""
    async def go():
        conn = await H.connect()
        slug = "aec-ufl-phi-den-2026-10-04-lbl%d" % int(time.time() * 1000)
        try:
            now = time.time()
            rec = _nfl_record(now - 9.0, slug=slug, now=now)
            assert rec["observed_at_basis"] == devig.OBSERVED_AT_NOT_AGED
            vid = await ext.persist(conn, copy.deepcopy(rec))
            row = await conn.fetchrow(
                "SELECT observed_at_basis, age_s, probability, "
                " extract(epoch from observed_at) AS obs "
                " FROM external_valuations WHERE id=$1", vid)
            assert row["observed_at_basis"] == devig.OBSERVED_AT_NOT_AGED
            assert row["age_s"] is None and row["probability"] is None
            assert float(row["obs"]) == pytest.approx(now - 9.0, abs=1e-3)
            # a valued (aged) record carries no label
            rec2, _ = _home_and_complement(now)
            vid2 = await ext.persist(conn, copy.deepcopy(rec2))
            assert await conn.fetchval(
                "SELECT observed_at_basis FROM external_valuations "
                " WHERE id=$1", vid2) is None
            import asyncpg
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "UPDATE external_valuations SET observed_at_basis=$2 "
                    " WHERE id=$1", vid2, devig.OBSERVED_AT_NOT_AGED)
            # and the rollback refuses rather than erase the label
            tr = conn.transaction()
            await tr.start()
            try:
                await conn.execute(
                    "DELETE FROM external_valuations WHERE "
                    "payout_is_complement OR observed_at IS NULL")
                with pytest.raises(asyncpg.RaiseError,
                                   match="labelled with their observed_at"):
                    await conn.execute((MIG / "rollback" /
                                        "251_external_valuations_one_per_"
                                        "observation_per_side.down.sql")
                                       .read_text())
            finally:
                await tr.rollback()
        finally:
            await OS._clean(conn, slug)
            await OS._clean(conn, SLUG)
            await conn.close()

    asyncio.run(go())


# ═════════════════════════════════════════════════════════════════════
# THE IDENTITY CONVERSION IS THE NCAAF ONE, ON AN NCAAF ROW (incident
# release, verifier finding 6)
# ═════════════════════════════════════════════════════════════════════
#
# `conversion` took the identity path (p_venue = p_book, t = 0) from the
# declaration's OWN fields -- no interval, `tie_probability_completed`
# present -- not from the league. An NFL-shaped declaration carrying t = 0
# and no interval would have validated as the identity, skipping the NFL's
# worst-end re-derivation. Only our own code builds declarations, so it was
# not reachable, but the validator must not let a declaration choose its own
# check: the identity path now requires the NCAAF settlement's own version
# AND a row on the NCAAF venue league token; anything else takes the
# worst-end path, which refuses a declaration with no interval.

def _identity_pin(p_book, *, version=None):
    from sportsassets import bettor_ncaaf_settlement as NC
    rec = {"applies": True, "version": version or NC.VERSION, "p": p_book,
           "refusal": None, "p_book_conditional_no_tie": p_book,
           "p_is": NC.P_IS_EQUIVALENT, "tie_probability_completed": 0.0,
           "formula": "p_venue = (1 - t) * p_book + 0 * t"}
    return {"p": p_book, "qualified": True, "age_s": 3.0, "limit_s": 30.0,
            "p_book_conditional_no_tie": p_book, "venue_conversion": rec,
            "p_is": NC.P_IS_EQUIVALENT}


def _validate_on(slug, p, pin):
    row = dict(_nfl_row(), us_market_slug=slug)
    md = {"offers": [{"px": {"value": "0.55"}, "qty": "100"}],
          "bids": [{"px": {"value": "0.53"}, "qty": "100"}]}
    return GEI.validate(
        p=p, side="LONG", row=row,
        levels=[{"price": 0.55, "wire": 0.55, "qty": 100.0}],
        consumed_side="offers", md=md, fee_per_contract=lambda px: 0.01,
        pin=pin, decided_at=1000.0, edge_at=1000.1,
        book_observed_at=999.5, book_max_age_s=30.0)


def test_the_ncaaf_identity_conversion_validates_on_an_ncaaf_row():
    p_book = _nfl_row()["probability"]
    pin = _identity_pin(p_book)
    r = _validate_on("aec-cfb-frest-washst-2026-10-03", pin["p"], pin)
    assert r["ok"], r["refusals"]
    checks = {c["check"]: c for c in r["checks"]}
    assert checks["VENUE_CONVERSION"]["passed"]


def test_a_declaration_cannot_choose_the_identity_check_for_itself():
    from sportsassets import bettor_ncaaf_settlement as NC
    assert GEI.IDENTITY_CONVERSION_VERSION == NC.VERSION
    assert GEI.IDENTITY_CONVERSION_LEAGUE == NC.LEAGUE
    p_book = _nfl_row()["probability"]
    # an NFL-shaped declaration with t = 0 and no interval, on an NFL row
    pin = _identity_pin(p_book, version="NFL_SETTLEMENT_V1")
    r = _validate_on("aec-nfl-kc-lv-2026-10-04", pin["p"], pin)
    assert r["refusals"] == [GEI.R_CONVERSION], r
    # the NCAAF declaration on a row that is not an NCAAF contract
    pin = _identity_pin(p_book)
    r = _validate_on("aec-nfl-kc-lv-2026-10-04", pin["p"], pin)
    assert r["refusals"] == [GEI.R_CONVERSION], r
    # a row with no venue slug cannot establish the league either
    r = _validate_on(None, pin["p"], pin)
    assert r["refusals"] == [GEI.R_CONVERSION], r

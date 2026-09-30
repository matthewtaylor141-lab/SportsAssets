"""DEREK'S INTERNAL MODEL CAN NOW QUALIFY WITHOUT BOOK CURRENCY (migration 170).

THE DEADLOCK. Derek's approved internal model (bettor_funded_model.
KEY_ENTRY_PAYOUT) could be trained only on `derek_entry_decisions`, which exist
only for ENTRY_DECISION valuations, which exist only when the venue read
established book currency -- never, in production, until P5 is resolved. So no
training record could ever accrue, although learning how a price relates to
its settlement needs no proof that the price was executable.

WHAT THIS FILE PROVES, through the real scheduled path with only transport
boundaries substituted (the empty-book and Derek harnesses), ALL DATA
SYNTHETIC AND LABELLED AS SUCH:

  1. CALIBRATION_ONLY opportunities become research observations through
     `ext_pinnacle_loop.cycle()` -> `derek.after_cycle`, with the submission
     switches OFF, no account binding, no approved model and trade admission
     refused -- frozen features, the displayed price with its basis, cohort,
     local receipt time, the venue's own stamp, timing uncertainty and source
     identity, and the contemporaneous Pinnacle reading. Idempotent.
  2. Later settlement is joined by the production join; voids, unjoined and
     unverified outcomes are not labels; missing data is counted.
  3. Labels -> `fit_from_records` (training population declared on the model
     record: source, cohort, price-basis mix, description, the unresolved
     training-vs-live input difference) -> `register` -> `evaluate` on
     prospective fixtures the fit could not see -> APPROVAL ELIGIBILITY,
     decided both ways: a model that beats the raw venue price and the base
     rate beyond the jackknife uncertainty is eligible; one that only repeats
     the price is not, and `promote` refuses it by name.
  4. A NAMED PERSON promotes the eligible one; Derek's gate then uses it in a
     real cycle, and the cycle's research observation freezes its prediction.
  5. The research table cannot size or send: no such column, CHECKs, append
     only, and no execution module reads it (census). A calibration-only
     valuation still can never be admissible or become a Derek decision.
  6. The one-time BACKFILL of older stored valuations, in the same scheduled
     step: bounded, walked backwards, marked BACKFILL_FROM_STORED_VALUATION,
     every value the stored row's own, no model prediction invented, and
     idempotent.
     Backfilled rows are RETROSPECTIVE_STORED evidence: a model fitted after
     the backfill can never count them toward MIN_EVALUATION_EVENTS or a
     promotion; only PROSPECTIVE_LIVE rows recorded after its registration
     count.
  7. Derek's DAILY MODEL RUN through `after_cycle`, both branches: too few
     labelled fixtures -> INSUFFICIENT_LABELLED_FIXTURES with the exact
     counts, once per day; enough -> fit, register and evaluate a CANDIDATE,
     never promoted. Every attempted fit is recorded (refused ones too), its
     evaluation cohort declared before fitting and frozen with it; the run
     lists the ids it attempted. The workspace shows the latest run.

SYNTHETIC EVIDENCE. Every price, probability, book and settlement below is a
test fixture. The approval is a test's (APPROVER says so). Nothing here is a
measurement of any market.
"""

from __future__ import annotations

import ast
import datetime as _dt
import json
import os
import pathlib
import time

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_model as FM
from sportsassets import bettor_valuation_purpose as vp
from sportsassets import bettor_venue_currency as VC
from sportsassets.agents import derek as D
from sportsassets.agents import derek_policy as DP
from sportsassets.agents import derek_research as DR
from sportsassets.agents import runtime as AR
from sportsassets.workers import ext_pinnacle_loop as loop

from tests import _emptybook_fixture as F
from tests import test_derek_enters_on_conservative_agreement as DT

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs a migrated database")

BACKEND = pathlib.Path(__file__).resolve().parents[1]
SYN = "derek-research-syn-"
LONG = "ORDER_INTENT_BUY_LONG"
APPROVER = "owner@test (SYNTHETIC APPROVAL OF SYNTHETIC RESEARCH EVIDENCE)"
#: The shipped seams, captured before any test substitutes them.
_REAL_AR_GATE = AR.gate_for_funded_entry
_SHIPPED_BCE = loop.book_currency_evidence

#: SYNTHETIC RULES: wins out of PER_PRICE fixtures at each displayed price.
PER_PRICE = 25
#: The venue under-prices the event (true rates 0.48 / 0.76 / 0.96): a price
#: calibration can learn something the raw price does not say.
RULE_INFORMATIVE = {0.30: 12, 0.50: 19, 0.70: 24}
#: The venue's price IS the rate (0.32 / 0.48 / 0.72): a calibration of it can
#: only repeat it.
RULE_CALIBRATED = {0.30: 8, 0.50: 12, 0.70: 18}
#: THE DAILY-RUN TEST'S OWN DAYS, far from any real cycle's day.
RUN_DAYS = (time.time() + 400 * 86400.0, time.time() + 401 * 86400.0)


# ═════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════

async def _connect():
    import asyncpg
    return await asyncpg.connect(DSN)


async def _ensure_schema(conn):
    await DT._ensure_schema(conn)
    await conn.execute((BACKEND / "migrations" /
                        "170_derek_research_observations.sql").read_text())


async def _purge(conn):
    """Everything this file created, and nothing else."""
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "DELETE FROM derek_research_observations "
            " WHERE condition_id LIKE $1 OR us_market_slug LIKE $1 "
            "    OR fixture LIKE $4 "
            "    OR us_market_slug = ANY($2::text[]) "
            "    OR condition_id = ANY($3::text[])", SYN + "%",
            [F.US_SLUG, DT.US_SLUG], [F.CONDITION, DT.CONDITION],
            "condition:" + SYN + "%")
        await conn.execute(
            "DELETE FROM derek_entry_decisions WHERE us_market_slug LIKE $1",
            SYN + "%")
        await conn.execute(
            "DELETE FROM derek_research_model_attempts "
            " WHERE attempt_id LIKE 'derek-research-%'")
        await conn.execute(
            "DELETE FROM derek_research_model_runs "
            " WHERE run_day = ANY($1::date[])", [DR._day_of(t)
                                                for t in RUN_DAYS])
    await conn.execute("DELETE FROM bettor_funded_models WHERE model_key = $1",
                       FM.KEY_ENTRY_PAYOUT)
    await conn.execute(
        "DELETE FROM external_valuations WHERE condition_id LIKE $1",
        SYN + "%")


_UNSET = object()


async def _pause_backfill(conn):
    """Mark the one-time backfill exhausted for the duration of a test, so
    it cannot pull a shared database's older rows into a test's cohort.
    Returns the prior state to restore."""
    prior = await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1",
        DR.BACKFILL_STATE_KEY)
    await DR._save_state(conn, {"exhausted": True,
                                "paused_by": "a test (SYNTHETIC)"})
    return prior


async def _restore_backfill(conn, prior):
    if prior is None:
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                           DR.BACKFILL_STATE_KEY)
    else:
        await conn.execute(
            "UPDATE ingestion_state SET value=$2 WHERE key=$1",
            DR.BACKFILL_STATE_KEY, prior)


def _contract(cid):
    return {"venue": "PMUS", "condition_id": cid, "selection": "HOME",
            "us_market_slug": cid, "sport_family": "baseball",
            "market": "h2h", "period": "FULL_GAME", "line": None,
            "settlement_rule": "R", "event_key": "e-" + cid,
            "buy_intent": LONG, "ladder_side": "ASK"}


async def _calibration_row(conn, *, cid, price, p_pin, decided_at,
                           venue_ts=True, book="pinnacle") -> int:
    """A SYNTHETIC calibration-only valuation, through the lane's own
    `ext.evaluate` (sealed CALIBRATION_ONLY) and `ext.persist`, with the
    displayed-quote evidence in the shape `venue_quote` produces."""
    shown = {"ok": True, "usable_for_orders": False,
             "what_this_is": vp.DISPLAYED_NOT_AN_ORDER_PRICE,
             "acquisition_price": price, "side_consumed": "ASK",
             "pays_on": "THE_PRICED_OUTCOME", "intent": LONG, "slug": cid,
             "read_at": decided_at - 2.0,
             "venue_ts": (decided_at - 3.0) if venue_ts else None,
             "venue_clock_basis": ("VENUE_TRANSACT_TIME" if venue_ts
                                   else "VENUE_CLOCK_NOT_PROVIDED"),
             "book_currency_verdict": VC.NOT_ESTABLISHED,
             "book_currency_mechanism": None}
    rec = ext.evaluate(
        contract=_contract(cid),
        quote={"book": book,
               "outcomes": {"HOME": 1.0 / p_pin, "AWAY": 1.0 / (1.0 - p_pin)},
               "observed_at": decided_at - 5.0,
               "received_at": decided_at - 4.0, "event_key": "e-" + cid,
               "period": "FULL_GAME", "line": None, "settlement_rule": "R"},
        market_state={"ask": price, "depth": 500.0, "readable": True,
                      "ask_basis": "DISPLAYED_SYNTHETIC_NOT_USABLE_FOR_ORDERS"},
        execution_estimate={"p_fill": 0.9, "basis": "TEST", "crossing": True},
        size=None, risk={"permitted": False, "reason": "SYNTHETIC"},
        fee_fn=lambda qty, price: 0.0, now=decided_at, outcome_books=4,
        armed=True, record_purpose=vp.CALIBRATION_ONLY,
        calibration_only_evidence={
            "venue_read_refusal": loop.R_BOOK_CURRENCY_NOT_ESTABLISHED,
            "book_currency": {"verdict": VC.NOT_ESTABLISHED,
                              "mechanism": None},
            "displayed_quote": shown})
    assert rec["record_purpose"] == vp.CALIBRATION_ONLY
    assert rec["admissible"] is False and rec["executable_price"] is None
    vid = await ext.persist(conn, rec)
    await conn.execute("UPDATE external_valuations SET decided_at = "
                       "to_timestamp($2) WHERE id = $1", vid, decided_at)
    return vid


async def _entry_row(conn, *, cid, price, p_pin, decided_at,
                     received_at=None) -> int:
    """A SYNTHETIC entry-decision valuation (an executable price on an
    established book), in the shape the Derek harness writes; with
    `received_at`, its risk verdict carries the venue clock the lane
    records."""
    risk = None if received_at is None else json.dumps({
        "freshness_evidence": {
            "venue_age_s": 1.0, "venue_limit_s": 10.0,
            "venue_age_basis": "SYNTHETIC_M1_SUBSCRIPTION",
            "venue_clock": {"our_response_received_at": received_at,
                            "parsed_epoch_s": None,
                            "basis": "VENUE_CLOCK_NOT_PROVIDED"}}})
    vid = await conn.fetchval(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, condition_id, "
        " us_market_slug, contract_selection, sport_family, market, period, "
        " raw_odds, outcomes_priced, expected_outcomes, observed_at, "
        " received_at, probability, executable_price, cost_per_contract, "
        " decision, admissible, refusals, why, payout_event, buy_intent, "
        " ladder_side, record_purpose, decided_at, event_key) "
        "VALUES ($1,'PINNACLE_DEVIG_V1','EXTERNAL_BOOKMAKER_VALUATION',"
        " 'the-odds-api.com/v4','pinnacle','power','PMUS',$2,$2,"
        " 'HOME','baseball','h2h','FULL_GAME','{}'::jsonb,2,2,"
        " to_timestamp($3 - 5),to_timestamp($3 - 4),$4,$5,0.0175,'NO_TRADE',"
        " false, ARRAY['SYNTHETIC_RESEARCH_RECORD'],'synthetic test evidence',"
        " 'HOME',$6,'ASK','ENTRY_DECISION',to_timestamp($3),'e-' || $2) "
        "RETURNING id", ext.EXPERIMENT_ID, cid, float(decided_at),
        float(p_pin), float(price), LONG)
    if risk is not None:
        await conn.execute("UPDATE external_valuations SET risk_verdict = "
                           "$2::jsonb WHERE id = $1", vid, risk)
    return vid


def _outcomes(rule, per=PER_PRICE):
    """[(price, won)] in a fixed order: the first `wins` fixtures at each
    price win."""
    out = []
    for price, wins in rule.items():
        for i in range(per):
            out.append((price, 1 if i < wins else 0))
    return out


async def _calibration_cohort(conn, *, tag, rule, start,
                              per=PER_PRICE) -> list:
    """One calibration-only valuation per fixture, observed by Derek's
    scheduled hook. Returns [(valuation_id, cid, won, decided_at)]."""
    made = []
    for n, (price, won) in enumerate(_outcomes(rule, per)):
        cid = "%s%s-%03d" % (SYN, tag, n)
        vid = await _calibration_row(conn, cid=cid, price=price,
                                     p_pin=min(0.97, price + 0.15),
                                     decided_at=start + n)
        made.append((vid, cid, won, start + n))
    got = await D.after_cycle(conn, cycle={"elapsed_s": 0.0},
                              now=start + len(made) + 1)
    # CALIBRATION-ONLY ROWS ARE NEVER DEREK CANDIDATES...
    assert got["candidates"] == 0 and got["decisions_recorded"] == 0, got
    # ...AND EVERY ONE BECAME A RESEARCH OBSERVATION.
    res = got["research_observations"]
    assert res["recorded"] == len(made), res
    assert res["by_cohort"] == {FM.COHORT_DISPLAYED: len(made)}, res
    return made


def _stub_settlements(monkeypatch, wins: dict):
    """THE VENUE'S SETTLEMENT READ, at its transport boundary. SYNTHETIC:
    listed slugs settle at 1 or 0; everything else stays pending."""
    from sportsassets import bettor_live_read as lr

    def settled(slug):
        if slug in wins:
            won = wins[slug]
            return {"status": lr.RESOLVED,
                    "settlement_price": 1.0 if won else 0.0,
                    "settlement_price_raw": "1" if won else "0"}
        return {"status": lr.PENDING}
    monkeypatch.setattr(loop, "_read_resolution_blocking", settled)


async def _label_prospectively(conn, made):
    """Prospective rows are decided in the future relative to the join's
    `now() - 2 hours` queue, so their settlement is written by the
    production join's own statement, one hour after each decision."""
    for vid, _cid, won, decided in made:
        await conn.execute(loop.JOIN_RESOLVED_SQL, vid, int(won),
                           decided + 3600.0, loop.B_SETTLEMENT_PRICE,
                           loop.SIDE_LONG, "1" if won else "0")


async def _train_register(conn, monkeypatch, *, tag, rule, model_id):
    """Training cohort -> production join -> labels -> fit -> register."""
    now = time.time()
    made = await _calibration_cohort(conn, tag=tag + "-train", rule=rule,
                                     start=now - 3 * 86400)
    _stub_settlements(monkeypatch, {cid: won for _v, cid, won, _t in made})
    joined = await loop.join_outcomes(conn, limit=100000)
    assert joined["resolved"] >= len(made), joined
    lab = await DR.labelled_observations(
        conn, decision_ids=["derek-research:val:%d" % v for v, *_ in made])
    assert lab["ok"] and lab["n"] == len(made), lab.get("refusal")
    assert set(lab["price_basis"]) == {FM.PRICE_BASIS_DISPLAYED}
    assert set(lab["cohorts"]) == {FM.COHORT_DISPLAYED}
    # THE ONE FIT PATH, RECORDED AS AN ATTEMPT (a person's here: no run id)
    att = await DR.attempt_fit(conn, model_id=model_id,
                               cohort=FM.COHORT_DISPLAYED,
                               through=time.time() + 1.0)
    assert att["outcome"] == DR.ATTEMPT_REGISTERED, att
    assert att["recorded"] is True
    return made, att


async def _prospective(conn, *, tag, rule, n_exec=0):
    """Observations decided AFTER the model was frozen, then settled."""
    t = time.time() + 120.0
    made = []
    for n, (price, won) in enumerate(_outcomes(rule)):
        cid = "%s%s-%03d" % (SYN, tag, n)
        vid = await _calibration_row(conn, cid=cid, price=price,
                                     p_pin=min(0.97, price + 0.15),
                                     decided_at=t + n)
        made.append((vid, cid, won, t + n))
    exe = []
    for k in range(n_exec):
        cid = "%s%s-exec-%03d" % (SYN, tag, k)
        decided = t + len(made) + k
        vid = await _entry_row(conn, cid=cid, price=0.50, p_pin=0.62,
                               decided_at=decided)
        exe.append((vid, cid, 1 if k % 4 else 0, decided))
    got = await D.after_cycle(conn, cycle={"elapsed_s": 0.0},
                              now=t + len(made) + n_exec + 1)
    res = got["research_observations"]
    assert res["recorded"] == len(made) + n_exec, res
    await _label_prospectively(conn, made + exe)
    return made, exe


# ═════════════════════════════════════════════════════════════════════════
# 1 · NO PATH FROM THE RESEARCH TABLE TO AN ORDER (source census)
# ═════════════════════════════════════════════════════════════════════════

#: EVERY module under `sportsassets` that names the research table, what it
#: does with it, and how many times it names it. A new reader fails this test
#: until it is classified here; an execution module may never appear.
RESEARCH_READERS = {
    # the writer, the labeller, the coverage count and the summary
    "agents/derek_research.py": (
        "WRITER_BACKFILL_LABELLER_COVERAGE_DAILY_RUN_AND_SUMMARY", 10),
    # the registry: which fixtures a fit could have seen (holdout)
    "bettor_funded_model.py": ("MODEL_REGISTRY_HOLDOUT_FIXTURES", 1),
    # reporting: does the table exist (collection metrics, workspace)
    "agents/derek.py": ("COLLECTION_METRICS_EXISTENCE_CHECK", 1),
    "api/agents_derek.py": ("WORKSPACE_EXISTENCE_CHECK", 1),
}
#: The modules that size, plan, reserve, send, manage or settle orders.
EXECUTION_MODULES = (
    "bettor_funded_execution.py", "bettor_entry_execution.py",
    "bettor_entry_inventory.py", "bettor_funded_activation.py",
    "bettor_funded_management.py", "bettor_funded_book.py", "pmus.py",
    "calibration_execute.py", "workers/ext_pinnacle_loop.py",
    "agents/xavier_policy.py", "agents/xavier_ladder.py",
    "agents/handoff.py", "agents/runtime.py", "agents/derek_policy.py")


def _pkg():
    return pathlib.Path(DR.__file__).resolve().parents[1]


def test_no_execution_module_reads_the_research_table():
    root = _pkg()
    found = {}
    for p in root.rglob("*.py"):
        if "__pycache__" in str(p):
            continue
        n = p.read_text(errors="ignore").count("derek_research_observations")
        if n:
            found[str(p.relative_to(root))] = n
    unknown = set(found) - set(RESEARCH_READERS)
    assert not unknown, ("unclassified reader(s) of derek_research_observations"
                         ": %s" % sorted(unknown))
    moved = {rel: (RESEARCH_READERS[rel][1], n) for rel, n in found.items()
             if RESEARCH_READERS[rel][1] != n}
    assert not moved, ("a classified module's mentions changed (pinned, "
                       "found): %s -- look at the new reader, then re-pin"
                       % moved)
    for rel in EXECUTION_MODULES:
        assert rel not in found, rel
        src = (root / rel).read_text(errors="ignore")
        # nor does it import the research module
        assert "derek_research" not in src, rel


def test_the_research_module_imports_and_calls_nothing_that_trades():
    tree = ast.parse(pathlib.Path(DR.__file__).read_text())
    imported, called = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported |= {a.name for a in node.names}
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.Call):
            f = node.func
            called.add(f.attr if isinstance(f, ast.Attribute)
                       else getattr(f, "id", ""))
    for mod in ("bettor_funded_execution", "bettor_entry_execution",
                "bettor_entry_inventory", "bettor_funded_activation",
                "bettor_funded_management", "bettor_funded_book", "pmus",
                "calibration_execute", "ext_pinnacle_loop"):
        assert not any(mod in m for m in imported), (mod, imported)
    for fn in ("submit_for_decision", "plan_from_decision", "plan_entry",
               "persist_entry", "_funded_attempt", "gate_for_funded_entry",
               "guarded_submit", "economics", "walk", "create",
               "place_order", "authorize", "reserve"):
        assert fn not in called, fn
    # THE ONLY THING THAT LEAVES THE MODULE FOR A DECISION IS A PROBABILITY,
    # through the registry: the gate reads the model, never the table.
    gate_src = pathlib.Path(DP.__file__).read_text()
    assert "derek_research" not in gate_src


# ═════════════════════════════════════════════════════════════════════════
# 2 · ONE OBSERVATION, FROM ONE VALUATION (pure)
# ═════════════════════════════════════════════════════════════════════════

def _cal_dict(**over):
    r = {"id": 7, "experiment_id": ext.EXPERIMENT_ID,
         "record_purpose": vp.CALIBRATION_ONLY, "venue": "PMUS",
         "decided_at": 1790000100.0, "event_key": "e-x",
         "condition_id": "c-x", "us_market_slug": "aec-x",
         "buy_intent": LONG, "ladder_side": "ASK", "payout_event": "HOME",
         "payout_is_complement": False, "probability": 0.61,
         "overround": 0.025, "devig_method": "power",
         "version": "PINNACLE_DEVIG_V1", "observed_at": 1790000095.0,
         "received_at": 1790000096.0, "executable_price": None,
         "risk_verdict": None,
         "calibration_only_evidence": json.dumps({
             "usable_for_orders": False,
             "book_currency": {"verdict": VC.NOT_ESTABLISHED,
                               "mechanism": None},
             "compared_at_the_displayed_price": {
                 "price": 0.55, "usable_for_orders": False},
             "displayed_quote": {"acquisition_price": 0.55,
                                 "usable_for_orders": False,
                                 "read_at": 1790000098.0,
                                 "venue_ts": 1790000097.0,
                                 "venue_clock_basis": "VENUE_TRANSACT_TIME",
                                 "slug": "aec-x", "intent": LONG,
                                 "side_consumed": "ASK"}})}
    r.update(over)
    return r


def test_a_calibration_valuation_becomes_a_frozen_displayed_price_observation():
    obs, why = DR.observation_from_row(_cal_dict(), approved=None)
    assert why is None
    assert obs["cohort"] == FM.COHORT_DISPLAYED
    assert obs["price_basis"] == FM.PRICE_BASIS_DISPLAYED
    assert obs["price"] == 0.55
    assert obs["features"] == {"acquisition_price": 0.55,
                               "payout_is_complement": 0.0}
    assert obs["feature_sha"] == FM.feature_sha(obs["features"])
    # TIMING: our receipt instant, the venue's own stamp, the uncertainty.
    assert obs["price_received_at"] == 1790000098.0
    assert obs["price_source_ts"] == 1790000097.0
    assert obs["price_source_ts_basis"].startswith("VENUE_TRANSACT_TIME")
    assert obs["price_timing_uncertainty"] == DR.TIMING_AGE_UNKNOWN
    assert VC.NOT_ESTABLISHED in obs["price_timing_basis"]
    ident = obs["price_source_identity"]
    assert ident["endpoint"] == DR.VENUE_BOOK_ENDPOINT
    assert (ident["market_slug"], ident["intent"], ident["side_consumed"]) \
        == ("aec-x", LONG, "ASK")
    # THE CONTEMPORANEOUS PINNACLE READING
    assert (obs["pinnacle_p"], obs["pinnacle_observed_at"],
            obs["pinnacle_received_at"], obs["pinnacle_overround"],
            obs["devig_method"], obs["pinnacle_source_version"]) == (
        0.61, 1790000095.0, 1790000096.0, 0.025, "power",
        "PINNACLE_DEVIG_V1")
    # NO MODEL: stated, never invented
    assert obs["model_p"] is None
    assert obs["model_absent_reason"].startswith(DR.A_NO_APPROVED_MODEL)
    assert obs["collection_mode"] == DR.MODE_LIVE
    assert obs["evidence_class"] == FM.EVIDENCE_CLASS_LIVE
    # nothing on it claims execution
    for k in ("qty", "quantity", "size", "verdict", "plan", "limit_price",
              "admissible", "depth", "fill", "edge"):
        assert k not in obs, k


def test_an_absent_venue_stamp_stays_unknown():
    ev = json.loads(_cal_dict()["calibration_only_evidence"])
    ev["displayed_quote"].update(venue_ts=None,
                                 venue_clock_basis="VENUE_CLOCK_NOT_PROVIDED")
    obs, _ = DR.observation_from_row(
        _cal_dict(calibration_only_evidence=json.dumps(ev)))
    assert obs["price_source_ts"] is None
    assert "VENUE_CLOCK_NOT_PROVIDED" in obs["price_source_ts_basis"]
    # an older record that never carried the clock says so
    ev["displayed_quote"].pop("venue_ts")
    ev["displayed_quote"].pop("venue_clock_basis")
    obs, _ = DR.observation_from_row(
        _cal_dict(calibration_only_evidence=json.dumps(ev)))
    assert obs["price_source_ts"] is None
    assert obs["price_source_ts_basis"].startswith("NOT_CARRIED")


def test_an_entry_valuation_is_the_executable_cohort():
    risk = {"freshness_evidence": {
        "venue_age_s": 1.2, "venue_limit_s": 10.0,
        "venue_age_basis": "M1_SUBSCRIPTION",
        "venue_clock": {"our_response_received_at": 1790000098.5,
                        "parsed_epoch_s": None,
                        "basis": "VENUE_CLOCK_NOT_PROVIDED"}}}
    obs, why = DR.observation_from_row(_cal_dict(
        record_purpose=vp.ENTRY_DECISION, executable_price=0.5,
        calibration_only_evidence=None, risk_verdict=json.dumps(risk)))
    assert why is None
    assert (obs["cohort"], obs["price_basis"], obs["price"]) == (
        FM.COHORT_EXECUTABLE, FM.PRICE_BASIS_EXECUTABLE, 0.5)
    assert obs["price_timing_uncertainty"] == DR.TIMING_BOUNDED
    assert obs["price_received_at"] == 1790000098.5
    assert obs["price_source_ts"] is None


def test_a_valuation_that_cannot_be_observed_is_named():
    assert DR.observation_from_row(_cal_dict(probability=None))[1] == \
        DR.S_NO_PINNACLE
    ev = json.loads(_cal_dict()["calibration_only_evidence"])
    ev["compared_at_the_displayed_price"]["price"] = None
    ev["displayed_quote"]["acquisition_price"] = None
    assert DR.observation_from_row(_cal_dict(
        calibration_only_evidence=json.dumps(ev)))[1] == DR.S_NO_PRICE
    assert DR.observation_from_row(_cal_dict(
        record_purpose="SOMETHING"))[1] == DR.S_UNKNOWN_PURPOSE


def test_a_model_approved_after_the_decision_is_not_frozen_onto_it():
    fitted = FM.fit([{"acquisition_price": p, "payout_is_complement": 0.0}
                     for p in (0.3, 0.5, 0.7, 0.5)], [0, 1, 1, 0],
                    features=list(FM.FEATURES_ENTRY_PAYOUT))
    ap = {"ok": True, "model": {"model_id": "m", "model_version": "m",
                                "params": fitted["params"],
                                "approved_at": 1790000200.0}}
    obs, _ = DR.observation_from_row(_cal_dict(), approved=ap)
    assert obs["model_p"] is None
    assert obs["model_absent_reason"].startswith(DR.A_APPROVED_AFTER)
    ap["model"]["approved_at"] = 1790000000.0
    obs, _ = DR.observation_from_row(_cal_dict(), approved=ap)
    assert obs["model_p"] == pytest.approx(
        FM.load(fitted["params"]).predict(obs["features"]))
    assert obs["model_id"] == "m" and obs["model_absent_reason"] is None


def test_the_minimums_are_the_codes_exact_counts_in_fixtures():
    m = FM.qualification_minimums(FM.KEY_ENTRY_PAYOUT)
    assert m["min_train_events"] == FM.MIN_TRAIN_EVENTS == 40
    assert m["min_prospective_evaluation_events"] == \
        FM.MIN_EVALUATION_EVENTS == 40
    assert m["unit"].startswith("FIXTURES")


# ═════════════════════════════════════════════════════════════════════════
# 3 · THE TABLE CANNOT AUTHORIZE AN ORDER (migration 170)
# ═════════════════════════════════════════════════════════════════════════

@pg
async def test_the_research_table_has_no_order_columns_and_refuses_claims():
    import asyncpg
    conn = await _connect()
    try:
        await _ensure_schema(conn)
        cols = {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            " WHERE table_name = 'derek_research_observations'")}
        for bad in ("qty", "quantity", "size", "proposed_size", "verdict",
                    "decision", "plan", "execution_plan", "limit_price",
                    "admissible", "executable_price", "depth", "fill",
                    "fills", "edge", "gross_edge_pp", "expected_net_profit_usd",
                    "order_id", "intent_id", "account_id"):
            assert bad not in cols, bad
        assert {"price_usable_for_orders", "execution_quality", "cohort",
                "price_basis", "price_received_at", "price_source_ts",
                "price_timing_uncertainty", "price_source_identity",
                "pinnacle_overround"} <= cols
        obs, _ = DR.observation_from_row(_cal_dict(
            id=-424242, condition_id=SYN + "ddl", us_market_slug=SYN + "ddl"))
        base = DR._insert_args(obs)
        await _purge(conn)
        assert await conn.fetchval(DR.INSERT_SQL, *base) == \
            obs["observation_id"]
        # IDEMPOTENT per valuation.
        assert await conn.fetchval(DR.INSERT_SQL, *base) is None
        for sql, match in (
                ("UPDATE derek_research_observations SET "
                 "price_usable_for_orders = TRUE WHERE valuation_id = -424242",
                 "append-only"),
                ("DELETE FROM derek_research_observations "
                 " WHERE valuation_id = -424242", "append-only")):
            with pytest.raises(asyncpg.PostgresError, match=match):
                await conn.execute(sql)
        # THE CHECKS, at insert, one claim at a time.
        cols_sql = DR.INSERT_SQL.split("(", 1)[1].split(")", 1)[0]
        names = [c.strip() for c in cols_sql.replace("\n", " ").split(",")]
        for field, value, ck in (
                ("collection_mode", "SOMETHING", "collection_mode_ck"),
                ("evidence_class", FM.EVIDENCE_CLASS_STORED,
                 "evidence_class_ck"),
                ("price_basis", FM.PRICE_BASIS_EXECUTABLE, "basis_follows"),
                ("cohort", FM.COHORT_EXECUTABLE, "basis_follows"),
                ("price_timing_uncertainty", DR.TIMING_BOUNDED,
                 "basis_follows"),
                ("price", 1.0, "price_ck")):
            args = list(base)
            args[0] = "derek-research:ddl:%s" % field
            args[1] = -424243
            args[names.index(field)] = value
            with pytest.raises(asyncpg.PostgresError, match=ck):
                await conn.fetchval(DR.INSERT_SQL, *args)
        # A BACKFILLED ROW CANNOT CARRY A MODEL PREDICTION
        args = list(base)
        args[0], args[1] = "derek-research:ddl:bf", -424245
        for f, v in (("collection_mode", DR.MODE_BACKFILL),
                     ("evidence_class", FM.EVIDENCE_CLASS_STORED),
                     ("model_p", 0.6),
                     ("model_id", "m"), ("model_absent_reason", None)):
            args[names.index(f)] = v
        with pytest.raises(asyncpg.PostgresError, match="backfill_no_model"):
            await conn.fetchval(DR.INSERT_SQL, *args)
        # A RETROSPECTIVE ROW WITHOUT THE ORIGINAL TIMESTAMPS IS REFUSED
        args = list(base)
        args[0], args[1] = "derek-research:ddl:bft", -424246
        for f, v in (("collection_mode", DR.MODE_BACKFILL),
                     ("evidence_class", FM.EVIDENCE_CLASS_STORED),
                     ("price_received_at", None)):
            args[names.index(f)] = v
        with pytest.raises(asyncpg.PostgresError,
                           match="stored_timestamps"):
            await conn.fetchval(DR.INSERT_SQL, *args)
        # NO ROW MAY CLAIM ORDER USE OR KNOWN EXECUTION QUALITY, whatever
        # writes it: a minimal raw row, with one claim added at a time.
        raw = ("INSERT INTO derek_research_observations "
               "(observation_id, valuation_id, experiment_id, record_purpose,"
               " cohort, fixture, payout_is_complement, decided_at, price, "
               " price_basis, price_source, price_source_ts_basis, "
               " price_timing_uncertainty, price_timing_basis, "
               " price_source_identity, pinnacle_p, features, feature_sha, "
               " model_absent_reason, observer_version, collection_mode, "
               " evidence_class%s)"
               " VALUES "
               "($1, -424244, 'X', 'CALIBRATION_ONLY', "
               " 'DISPLAYED_PRICE_AGE_UNKNOWN', 'condition:%sraw', false, "
               " now(), 0.5, 'DISPLAYED_BOOK_CURRENCY_UNESTABLISHED', 's', "
               " 'b', 'AGE_BOUND_UNKNOWN', 'b', '{}'::jsonb, 0.5, "
               " '{\"acquisition_price\": 0.5, "
               "\"payout_is_complement\": 0}'::jsonb, 's', 'none', 'v', "
               " 'LIVE_CYCLE', 'PROSPECTIVE_LIVE'%s)")
        for col, val, ck in (
                ("price_usable_for_orders", "TRUE", "never_for_orders"),
                ("execution_quality", "'FILLED'", "execution_unknown")):
            with pytest.raises(asyncpg.PostgresError, match=ck):
                await conn.execute(raw % (", " + col, SYN, ", " + val),
                                   "derek-research:raw:" + col)
        # the control: the same minimal row without a claim is accepted
        assert "INSERT" in await conn.execute(raw % ("", SYN, ""),
                                              "derek-research:raw:ok")
    finally:
        await _purge(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 4 · COLLECTION THROUGH THE REAL CYCLE, INDEPENDENT OF EVERY AUTHORITY
# ═════════════════════════════════════════════════════════════════════════

@pg
async def test_the_scheduled_cycle_collects_with_nothing_authorized(
        monkeypatch):
    """`ext_pinnacle_loop.cycle()` with the production currency seam (every
    read refuses VENUE_BOOK_CURRENCY_NOT_ESTABLISHED), the submission switches
    OFF, no account binding, no activation, no source calibration, no approved
    model and Derek's real gate: the candidate is refused admission and
    recorded CALIBRATION_ONLY -- and `derek.after_cycle` still records its
    research observation."""
    conn = await _connect()
    prior = _UNSET
    venue = F.Venue()
    try:
        await _ensure_schema(conn)
        prior = await _pause_backfill(conn)
        await F.clean(conn)
        await _purge(conn)
        await F.seed(conn)
        # ── TAKE AWAY EVERY AUTHORITY THE FIXTURE SUPPLIES ───────────────
        for key in (FA.ACCOUNT_KEY, FA.AUTHORIZATION_KEY, FA.LIMITS_KEY):
            await conn.execute("DELETE FROM ingestion_state WHERE key=$1", key)
        await conn.execute("DELETE FROM bettor_desk_accounts "
                           " WHERE account_id=$1", F.ACCT)
        await conn.execute("DELETE FROM external_source_calibration "
                           " WHERE measured_by='EMPTY_BOOK_LIFECYCLE_TEST'")
        F.substitute(monkeypatch, venue)
        monkeypatch.setattr(loop, "book_currency_evidence", _SHIPPED_BCE)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", False)
        monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", False)
        from sportsassets import bettor_funded_management as FMG
        monkeypatch.setattr(FMG, "FUNDED_EXIT_SUBMISSION_ENABLED", False)
        monkeypatch.setattr(AR, "gate_for_funded_entry", _REAL_AR_GATE)
        assert _SHIPPED_BCE(F.US_SLUG)["subscription"] is None
        assert (await FM.approved(conn, model_key=FM.KEY_ENTRY_PAYOUT))[
            "refusal"] == FM.R_NO_APPROVED_MODEL
        assert await conn.fetchval("SELECT count(*) FROM ingestion_state "
                                   " WHERE key=$1", FA.ACCOUNT_KEY) == 0

        out = await loop.cycle(conn)
        assert out["ran"] is True, out.get("why")
        assert out["refusals"].get(loop.R_BOOK_CURRENCY_NOT_ESTABLISHED) == 1
        assert "ADMITTED" not in out["refusals"], out["refusals"]
        assert out["calibration_only"]["recorded"] == 1

        v = dict(await conn.fetchrow(
            "SELECT id, record_purpose, admissible, probability, "
            " extract(epoch FROM observed_at) AS obs_at, "
            " extract(epoch FROM received_at) AS rcv_at, overround, "
            " calibration_only_evidence "
            " FROM external_valuations WHERE condition_id=$1", F.CONDITION))
        assert v["record_purpose"] == vp.CALIBRATION_ONLY
        assert v["admissible"] is False
        o = await conn.fetchrow(
            "SELECT *, extract(epoch FROM price_received_at) AS rcv, "
            " extract(epoch FROM price_source_ts) AS vts, "
            " extract(epoch FROM pinnacle_observed_at) AS pobs, "
            " extract(epoch FROM pinnacle_received_at) AS prcv "
            " FROM derek_research_observations WHERE valuation_id=$1",
            v["id"])
        assert o is not None, "the scheduled cycle recorded no observation"
        assert o["cohort"] == FM.COHORT_DISPLAYED
        assert o["price_basis"] == FM.PRICE_BASIS_DISPLAYED
        assert o["price"] == pytest.approx(F.OFFERS[0][0])
        assert json.loads(o["features"]) == {
            "acquisition_price": pytest.approx(F.OFFERS[0][0]),
            "payout_is_complement": (1.0 if o["payout_is_complement"]
                                     else 0.0)}
        assert o["price_usable_for_orders"] is False
        assert o["execution_quality"] == "UNKNOWN"
        # TIMING AND SOURCE, from the lane's own read
        ev = json.loads(v["calibration_only_evidence"])
        shown = ev["displayed_quote"]
        assert float(o["rcv"]) == pytest.approx(shown["read_at"], abs=1e-3)
        assert shown["venue_ts"] is not None       # the venue sent one
        assert float(o["vts"]) == pytest.approx(shown["venue_ts"], abs=1e-3)
        assert o["price_timing_uncertainty"] == DR.TIMING_AGE_UNKNOWN
        ident = json.loads(o["price_source_identity"])
        assert ident["market_slug"] == F.US_SLUG
        assert ident["intent"] == LONG and ident["endpoint"]
        # THE CONTEMPORANEOUS PINNACLE READING
        assert o["pinnacle_p"] == pytest.approx(float(v["probability"]))
        assert float(o["pobs"]) == pytest.approx(float(v["obs_at"]),
                                                 abs=1e-3)
        assert float(o["prcv"]) == pytest.approx(float(v["rcv_at"]),
                                                 abs=1e-3)
        assert o["pinnacle_overround"] == pytest.approx(
            float(v["overround"]))
        # NO MODEL WAS APPROVED: nothing frozen, the reason named
        assert o["model_p"] is None
        assert o["model_absent_reason"].startswith(DR.A_NO_APPROVED_MODEL)

        # NOTHING TRADED AND NO DEREK DECISION FOR A CALIBRATION ROW
        assert venue.creates_sent() == []
        assert await conn.fetchval(
            "SELECT count(*) FROM derek_entry_decisions WHERE valuation_id=$1",
            v["id"]) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1",
            F.ACCT) == 0

        # IDEMPOTENT: the hook again records nothing new.
        again = await D.after_cycle(conn, cycle=out, now=time.time())
        assert again["research_observations"]["recorded"] == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM derek_research_observations "
            " WHERE valuation_id=$1", v["id"]) == 1
    finally:
        if prior is not _UNSET:
            await _restore_backfill(conn, prior)
        await F.clean(conn)
        await _purge(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 5 · WHAT IS NOT A LABEL, AND WHAT IS MISSING, COUNTED
# ═════════════════════════════════════════════════════════════════════════

@pg
async def test_void_unjoined_and_unverified_outcomes_are_not_labels(
        monkeypatch):
    conn = await _connect()
    prior = _UNSET
    try:
        await _ensure_schema(conn)
        prior = await _pause_backfill(conn)
        await _purge(conn)
        start = time.time() - 3 * 3600.0
        ids = {}
        for n, tag in enumerate(("won", "void", "unjoined", "unverified")):
            cid = "%slab-%s" % (SYN, tag)
            ids[tag] = (await _calibration_row(
                conn, cid=cid, price=0.4, p_pin=0.5, decided_at=start + n),
                cid)
        # a valuation with NO Pinnacle reading (another book's payload)
        nopin = await _calibration_row(conn, cid=SYN + "lab-nopin",
                                       price=0.4, p_pin=0.5,
                                       decided_at=start + 5, book="smarkets")
        got = await D.after_cycle(conn, cycle={}, now=start + 10)
        res = got["research_observations"]
        assert res["recorded"] == 4, res
        assert res["not_observed"] == {DR.S_NO_PINNACLE: 1}, res
        assert await conn.fetchval(
            "SELECT count(*) FROM derek_research_observations "
            " WHERE valuation_id=$1", nopin) == 0

        _stub_settlements(monkeypatch, {ids["won"][1]: 1})
        joined = await loop.join_outcomes(conn, limit=100000)
        assert joined["resolved"] >= 1
        await conn.execute(loop.JOIN_VOID_SQL, ids["void"][0],
                           loop.B_CONFIRMED_VOID, loop.SIDE_LONG, "void",
                           time.time())
        # an outcome written with NO verified basis (the shadow's own
        # JOIN_OUTCOME statement records none)
        await conn.execute(ext.JOIN_OUTCOME, ids["unverified"][0], 1,
                           time.time(), None)

        obs_ids = ["derek-research:val:%d" % v for v, _ in ids.values()]
        lab = await FM.labelled(conn, model_key=FM.KEY_ENTRY_PAYOUT,
                                decision_ids=obs_ids,
                                source=FM.SOURCE_RESEARCH_OBSERVATIONS)
        assert lab["ok"] and lab["n"] == 1, lab
        assert lab["decision_ids"] == ["derek-research:val:%d"
                                       % ids["won"][0]]
        assert lab["labels"] == [1.0]
        assert lab["leg_outcomes"][0][0]["outcome_basis"] == \
            loop.B_SETTLEMENT_PRICE

        cov = await DR.prospective_coverage(conn, after=start - 1)
        mine = cov["by_cohort"][FM.COHORT_DISPLAYED]
        # (other rows in a shared database may add to these; ours are
        # at least counted)
        assert mine["fixtures_lacking_pinnacle"] >= 1
        assert mine["fixtures_lacking_label"] >= 4
        assert mine["of_which_void"] >= 1
        assert mine["of_which_outcome_without_verified_basis"] >= 1
    finally:
        if prior is not _UNSET:
            await _restore_backfill(conn, prior)
        await _purge(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 6 · TRAIN -> EVALUATE -> ELIGIBILITY -> A NAMED PERSON -> DEREK USES IT
# ═════════════════════════════════════════════════════════════════════════

@pg
async def test_a_model_that_beats_the_price_qualifies_and_derek_uses_it(
        monkeypatch):
    conn = await _connect()
    prior = _UNSET
    try:
        await DT._seed(conn)                # the Derek harness's world
        await _ensure_schema(conn)
        prior = await _pause_backfill(conn)
        await _purge(conn)
        mid = "derek-research-model-informative"
        train, att = await _train_register(
            conn, monkeypatch, tag="inf", rule=RULE_INFORMATIVE, model_id=mid)

        # ── THE MODEL RECORD DECLARES ITS TRAINING POPULATION ─────────
        row = await conn.fetchrow(
            "SELECT training_provenance FROM bettor_funded_models "
            " WHERE model_id=$1", mid)
        prov = json.loads(row["training_provenance"])
        assert prov["source"] == FM.SOURCE_RESEARCH_OBSERVATIONS
        pop = prov["training_population"]
        assert pop["record_source"] == FM.SOURCE_RESEARCH_OBSERVATIONS
        assert pop["training_cohorts"] == [FM.COHORT_DISPLAYED]
        assert pop["cohort_mix"] == {FM.COHORT_DISPLAYED: {
            "rows": 3 * PER_PRICE, "fixtures": 3 * PER_PRICE}}
        assert pop["price_basis_mix"] == {FM.PRICE_BASIS_DISPLAYED: {
            "rows": 3 * PER_PRICE, "fixtures": 3 * PER_PRICE}}
        assert pop["model_description"] == FM.ENTRY_PAYOUT_DESCRIPTION
        assert pop["model_description"] == (
            "a separately fitted market-price calibration model (features: "
            "price, payout side); not an independent sports forecast")
        shift = pop["input_distribution_shift"]
        assert shift["status"] == "UNRESOLVED"
        assert shift["applied_live_to_cohort"] == FM.COHORT_EXECUTABLE
        assert shift["executable_trading_performance"].startswith(
            "NOT_ESTABLISHED")
        assert pop["minimums"]["min_train_events"] == 40
        assert prov["n_events"] == 3 * PER_PRICE >= FM.MIN_TRAIN_EVENTS
        assert pop["evidence_class_mix"] == {FM.EVIDENCE_CLASS_LIVE: {
            "rows": 3 * PER_PRICE, "fixtures": 3 * PER_PRICE}}
        # THE EVALUATION COHORT, DECLARED BEFORE FITTING AND FROZEN
        ec = prov["windows"]["evaluation_cohort"]
        assert ec["declared_before_fitting"] is True
        assert ec["cohorts"] == [FM.COHORT_DISPLAYED]
        assert ec["evidence_class"] == FM.EVIDENCE_CLASS_LIVE
        assert ec["source"] == FM.SOURCE_RESEARCH_OBSERVATIONS
        assert ec["prospective_window_start"].startswith(
            "THIS MODEL'S REGISTRATION INSTANT")
        import asyncpg
        with pytest.raises(asyncpg.PostgresError, match="not edited"):
            await conn.execute(
                "UPDATE bettor_funded_models SET training_provenance = "
                "training_provenance || '{\"windows\": {}}'::jsonb "
                " WHERE model_id=$1", mid)
        a = await conn.fetchrow("SELECT * FROM derek_research_model_attempts"
                                " WHERE attempt_id=$1", mid)
        assert a["outcome"] == DR.ATTEMPT_REGISTERED
        assert a["train_rows"] == a["train_fixtures"] == 3 * PER_PRICE
        assert a["records_sha"] == prov["records_sha"]
        assert json.loads(a["evaluation_cohort"]) == ec

        # ── NO PROSPECTIVE EVIDENCE YET: NOT ELIGIBLE, NOT PROMOTABLE ─
        ev0 = await FM.evaluate(conn, model_id=mid)
        assert ev0["ok"] is False and ev0["refusal"] == FM.R_TOO_FEW_LABELS
        p0 = await FM.promote(conn, model_id=mid, approved_by=APPROVER)
        assert p0["ok"] is False

        # ── PROSPECTIVE: displayed-price fixtures, plus a few executable
        #    ones reported separately ─────────────────────────────────
        pros, exe = await _prospective(conn, tag="inf-pros",
                                       rule=RULE_INFORMATIVE, n_exec=10)
        ev = await FM.evaluate(conn, model_id=mid)
        assert ev["ok"] is True, (ev.get("refusal"), ev.get("failed"))
        doc = ev["evaluation"]
        assert doc["contamination"]["verdict"] == "CLEAN"
        assert doc["evaluated_on_cohorts"] == [FM.COHORT_DISPLAYED]
        mc = doc["market_comparison"]
        assert mc["n_events"] == 3 * PER_PRICE
        assert mc["same_rows_for_every_predictor"] is True
        for k in (FM.P_MODEL, FM.P_PRICE, FM.P_PINNACLE, FM.P_BASE):
            s = mc["predictors"][k]
            assert s["log_loss"] > 0 and 0 <= s["brier"] <= 1
            assert s["log_loss_uncertainty"]["status"] == "OK"
            assert s["brier_uncertainty"]["status"] == "OK"
            assert "ece" in s["calibration"]
        vp_ = mc["model_vs_raw_venue_price"]
        assert vp_["log_loss_improvement"] >= FM.MIN_SKILL_MARGIN
        assert vp_["log_loss_improvement_uncertainty"]["ci95"][0] > 0
        vb = mc["model_vs_training_base_rate"]
        assert vb["log_loss_improvement_uncertainty"]["ci95"][0] > 0
        assert mc["model_vs_pinnacle"]["role"].startswith(
            "REPORTED_NOT_A_CONDITION")
        el = doc["approval_eligibility"]
        assert el["eligible"] is True and el["failed"] == []
        assert el["pinnacle_comparison_is_a_condition"] is False
        assert el["minimums"]["min_prospective_evaluation_events"] == 40
        assert el["trained_on_cohorts"] == [FM.COHORT_DISPLAYED]
        assert el["input_distribution_shift"]["status"] == "UNRESOLVED"
        assert doc["prospective_evidence_class_mix"] == {
            FM.EVIDENCE_CLASS_LIVE: {"rows": 3 * PER_PRICE,
                                     "fixtures": 3 * PER_PRICE}}
        pmix = el["input_distribution_shift"]["prospective_by_cohort"]
        assert pmix[FM.COHORT_DISPLAYED]["fixtures"] == 3 * PER_PRICE
        assert pmix[FM.COHORT_EXECUTABLE]["fixtures"] == 10
        # THE EXECUTABLE COHORT, ON ITS OWN, deciding nothing
        sep = el["separately_reported_cohorts"][FM.COHORT_EXECUTABLE]
        assert sep["n_events"] == 10 and sep["role"].startswith(
            "REPORTED_SEPARATELY")
        cov = el["missing_data_coverage"]
        assert cov["ok"] is True
        assert cov["by_cohort"][FM.COHORT_DISPLAYED]["fixtures"] >= \
            3 * PER_PRICE

        # ── ONLY A NAMED PERSON PROMOTES IT ──────────────────────────
        assert (await FM.promote(conn, model_id=mid, approved_by=""))[
            "refusal"] == FM.R_NO_APPROVER
        prom = await FM.promote(conn, model_id=mid, approved_by=APPROVER)
        assert prom["ok"] is True, prom
        pc = prom["comparison"]
        assert pc["market_comparison"]["model_beats_raw_price"] is True
        assert pc["approval_eligibility"]["eligible"] is True
        ap = await FM.approved(conn, model_key=FM.KEY_ENTRY_PAYOUT)
        assert ap["ok"] and ap["provenance_verified"]
        assert ap["model"]["approved_by"] == APPROVER

        # ── DEREK'S GATE USES IT, IN A REAL CYCLE ────────────────────
        await DT.league_reports_not_started(conn)
        DT._stub(monkeypatch, p_home=0.60)
        captured: list = []
        DT._wrap_funded_attempt(monkeypatch, captured)
        out = await loop.cycle(conn)
        assert out["refusals"].get("ADMITTED") == 1, out["refusals"]
        assert out["order_submitted"] is False
        gate = captured[0]["gate"]
        est = gate["decision"]["estimates"]["model"]
        assert est["model_id"] == mid
        assert est["is"] == FM.ENTRY_PAYOUT_DESCRIPTION
        # WHAT THE ACTIVE POLICY'S (V2) USE OF IT IS: one half of an
        # average with Pinnacle, stated as not independent confirmation.
        assert est["policy_use_is"] == FM.ENTRY_POLICY_AGREEMENT_IS
        assert est["policy_use_is"].startswith(
            "POLICY_AVERAGE_OF_TWO_MARKET_DERIVED_ESTIMATES_NOT_INDEPENDENT")
        assert est["p"] == pytest.approx(FM.load(ap["model"]["params"])
                                         .predict({"acquisition_price": 0.5,
                                                   "payout_is_complement":
                                                   0.0}))
        # THE ACTIVE POLICY (V2) ADMITS IT: the blended average of the two
        # estimates clears 5 pp at $0.50, net of fees.
        assert gate["verdict"] == DP.ENTER, gate.get("refusal")
        assert gate["decision"]["policy_name"] == DP.POLICY_V2
        assert gate["decision"]["params"]["min_gross_edge_pp"] == 0.05
        # the cycle's ENTRY_DECISION valuation was observed too, with the
        # approved model's prediction frozen on it
        vid = captured[0]["rec"]["valuation_row_id"]
        o = await conn.fetchrow(
            "SELECT cohort, model_id, model_p, features "
            "  FROM derek_research_observations WHERE valuation_id=$1", vid)
        assert o["cohort"] == FM.COHORT_EXECUTABLE
        assert o["model_id"] == mid
        assert o["model_p"] == pytest.approx(FM.load(
            ap["model"]["params"]).predict(json.loads(o["features"])))

        # ── A CALIBRATION ROW IS STILL NEVER ADMISSIBLE ──────────────
        cal_ids = [v for v, *_ in train + pros]
        assert await conn.fetchval(
            "SELECT count(*) FROM derek_entry_decisions "
            " WHERE valuation_id = ANY($1::bigint[])", cal_ids) == 0
        vrow = dict(await conn.fetchrow(
            "SELECT * FROM external_valuations WHERE id=$1", cal_ids[0]))
        cand = DP.candidate_from_row(vrow)
        dec = DP.evaluate(cand, model=DP.model_estimate(
            ap, cand, at=time.time()), authority=[])
        assert dec["verdict"] == DP.REFUSE
        assert DP.R_NOT_ENTRY in dec["all_refusals"]
        plan = FX.plan_from_decision(dict(cand["plan_input"],
                                          admissible=True))
        assert plan["ok"] is False
        assert plan["refusal"] == vp.R_CALIBRATION_ONLY
        import asyncpg
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("UPDATE external_valuations SET "
                               "admissible=true, decision='BUY' WHERE id=$1",
                               cal_ids[0])
        # AND AN OBSERVATION HANDED TO THE CONNECTOR'S PLANNER PLANS NOTHING
        orow = dict(await conn.fetchrow(
            "SELECT * FROM derek_research_observations WHERE valuation_id=$1",
            cal_ids[0]))
        assert FX.plan_from_decision(dict(orow, admissible=True))["ok"] \
            is False

        # ── THE WORKSPACE STATES THE MINIMUMS AND WHAT THE MODEL IS ──
        from sportsassets.api import agents_derek as A
        ws = await A.workspace(conn)
        mq = ws["sections"]["model_qualification"]
        assert mq["status"] == "OK", mq
        assert mq["data"]["minimums"]["min_train_events"] == 40
        assert mq["data"]["minimums"]["min_prospective_evaluation_events"] \
            == 40
        assert mq["data"]["entry_policy_use"] == FM.ENTRY_POLICY_AGREEMENT_IS
        mine = [m for m in mq["data"]["models"] if m["model_id"] == mid]
        assert mine and mine[0]["training_population"][
            "training_cohorts"] == [FM.COHORT_DISPLAYED]
        im = ws["sections"]["versions"]["data"]["internal_model"]
        assert im["description"] == FM.ENTRY_PAYOUT_DESCRIPTION
        assert im["minimums"]["min_prospective_evaluation_events"] == 40
    finally:
        if prior is not _UNSET:
            await _restore_backfill(conn, prior)
        await DT._cleanup(conn)
        await _purge(conn)
        await conn.close()


@pg
async def test_a_model_that_only_repeats_the_price_is_not_eligible(
        monkeypatch):
    conn = await _connect()
    prior = _UNSET
    try:
        await _ensure_schema(conn)
        prior = await _pause_backfill(conn)
        await _purge(conn)
        mid = "derek-research-model-calibrated"
        await _train_register(conn, monkeypatch, tag="cal",
                              rule=RULE_CALIBRATED, model_id=mid)
        await _prospective(conn, tag="cal-pros", rule=RULE_CALIBRATED)
        ev = await FM.evaluate(conn, model_id=mid)
        assert ev["ok"] is False
        assert ev["refusal"] == FM.R_NO_INFORMATION_BEYOND_THE_PRICE, ev
        doc = ev["evaluation"]
        el = doc["approval_eligibility"]
        assert el["eligible"] is False
        assert "beats_raw_venue_price_by_margin_and_beyond_uncertainty" in \
            el["failed"]
        mc = doc["market_comparison"]
        assert mc["n_events"] == 3 * PER_PRICE >= FM.MIN_EVALUATION_EVENTS
        assert mc["model_beats_raw_price"] is False
        # MEASURED, NOT ASSUMED: a price calibration need not beat the base
        # rate beyond the uncertainty either; it is reported either way.
        assert "ci95" in mc["model_vs_training_base_rate"][
            "log_loss_improvement_uncertainty"]
        # ...and the named person's promotion is refused by name
        prom = await FM.promote(conn, model_id=mid, approved_by=APPROVER)
        assert prom["ok"] is False
        assert prom["refusal"] == FM.R_NO_INFORMATION_BEYOND_THE_PRICE, prom
        assert (await FM.approved(conn, model_key=FM.KEY_ENTRY_PAYOUT))[
            "refusal"] == FM.R_NO_APPROVED_MODEL
    finally:
        if prior is not _UNSET:
            await _restore_backfill(conn, prior)
        await _purge(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 8 · THE CLOSED-FORM JACKKNIFE IS THE METRICS MODULE'S JACKKNIFE
# ═════════════════════════════════════════════════════════════════════════

def test_the_comparisons_jackknife_equals_metrics_clustered_jackknife():
    from sportsassets.learn import metrics as M
    vals = [((i * 37) % 11) / 10.0 for i in range(60)]
    groups = ["g%d" % (i % 17) for i in range(60)]
    w = FM.event_weights(groups)
    mine = FM.cluster_jackknife_of_mean(vals, w, groups)
    ref = M.clustered_jackknife(
        list(zip(vals, w)), [0] * 60, groups,
        lambda p, y: sum(v * x for v, x in p) / sum(x for _, x in p))
    assert mine["status"] == ref["status"] == "OK"
    assert mine["statistic"] == pytest.approx(ref["statistic"], abs=1e-12)
    assert mine["se_clustered"] == pytest.approx(ref["se_clustered"],
                                                 abs=1e-12)
    assert mine["ci95"] == pytest.approx(ref["ci95"], abs=1e-12)
    few = FM.cluster_jackknife_of_mean([0.1] * 5, [1.0] * 5, list("abcde"))
    assert few["status"] == "INSUFFICIENT_CLUSTERS"


def test_a_backfilled_observation_invents_no_prediction():
    fitted = FM.fit([{"acquisition_price": p, "payout_is_complement": 0.0}
                     for p in (0.3, 0.5, 0.7, 0.5)], [0, 1, 1, 0],
                    features=list(FM.FEATURES_ENTRY_PAYOUT))
    ap = {"ok": True, "model": {"model_id": "m", "model_version": "m",
                                "params": fitted["params"],
                                "approved_at": 1.0}}
    live, _ = DR.observation_from_row(_cal_dict(), approved=ap)
    bf, _ = DR.observation_from_row(_cal_dict(), approved=ap,
                                    mode=DR.MODE_BACKFILL)
    assert live["model_p"] is not None
    assert bf["collection_mode"] == DR.MODE_BACKFILL
    assert bf["evidence_class"] == FM.EVIDENCE_CLASS_STORED
    assert bf["model_p"] is None and bf["model_id"] is None
    assert bf["model_absent_reason"] == DR.A_BACKFILL
    # everything else is the same stored-row reading
    for k in set(live) - {"model_p", "model_id", "model_version",
                          "model_absent_reason", "collection_mode",
                          "evidence_class"}:
        assert bf[k] == live[k], k
    # RETROSPECTIVE ONLY WHERE THE STORED ROW HOLDS THE ORIGINAL TIMESTAMPS
    ev = json.loads(_cal_dict()["calibration_only_evidence"])
    ev["displayed_quote"].pop("read_at")
    row = _cal_dict(calibration_only_evidence=json.dumps(ev))
    assert DR.observation_from_row(row, mode=DR.MODE_BACKFILL)[1] == \
        DR.S_BACKFILL_NO_PRICE_RECEIPT
    assert DR.observation_from_row(row)[1] is None      # live keeps it
    assert DR.observation_from_row(_cal_dict(observed_at=None),
                                   mode=DR.MODE_BACKFILL)[1] == \
        DR.S_BACKFILL_NO_PINNACLE_STAMP


def test_a_degenerate_fit_is_named():
    rows = [{"acquisition_price": p, "payout_is_complement": 0.0}
            for p in (0.3, 0.5, 0.7)]
    fitted = FM.fit(rows, [1, 1, 1], features=list(FM.FEATURES_ENTRY_PAYOUT))
    assert DR._degenerate(fitted, rows, [1, 1, 1]) == \
        "SINGLE_CLASS_TRAINING_LABELS"
    fitted = FM.fit(rows, [0, 1, 1], features=list(FM.FEATURES_ENTRY_PAYOUT))
    assert DR._degenerate(fitted, rows, [0, 1, 1]) is None


# ═════════════════════════════════════════════════════════════════════════
# 9 · THE ONE-TIME BACKFILL, THROUGH THE SCHEDULED STEP
# ═════════════════════════════════════════════════════════════════════════

@pg
async def test_the_backfill_carries_only_stored_values_and_runs_once(
        monkeypatch):
    conn = await _connect()
    prior = _UNSET
    try:
        await _ensure_schema(conn)
        await _purge(conn)
        prior = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1",
            DR.BACKFILL_STATE_KEY)
        old = time.time() - 2 * 86400.0
        mine = {}
        mine["stamped"] = await _calibration_row(
            conn, cid=SYN + "bf-stamped", price=0.42, p_pin=0.55,
            decided_at=old)
        mine["unstamped"] = await _calibration_row(
            conn, cid=SYN + "bf-unstamped", price=0.61, p_pin=0.70,
            decided_at=old + 1, venue_ts=False)
        mine["entry"] = await _entry_row(conn, cid=SYN + "bf-entry",
                                         price=0.50, p_pin=0.62,
                                         decided_at=old + 2,
                                         received_at=old + 1.5)
        # STORED WITHOUT OUR RECEIPT TIME: excluded, counted by reason
        noreceipt = await _entry_row(conn, cid=SYN + "bf-noreceipt",
                                     price=0.50, p_pin=0.62,
                                     decided_at=old + 2.5)
        nopin = await _calibration_row(
            conn, cid=SYN + "bf-nopin", price=0.40, p_pin=0.5,
            decided_at=old + 3, book="smarkets")
        top = max(list(mine.values()) + [nopin, noreceipt])
        # THE BACKFILL MAY NOT RE-QUERY ANYTHING: every transport raises.
        def _no(*a, **k):
            raise AssertionError("the backfill re-queried a source")

        async def _ano(*a, **k):
            raise AssertionError("the backfill re-queried a source")
        for name in ("fetch_odds", "venue_quote"):
            monkeypatch.setattr(loop, name, _ano)
        for name in ("_read_book_blocking", "_read_resolution_blocking"):
            monkeypatch.setattr(loop, name, _no)
        # start the walk just above our rows (a shared database's own
        # history below them may be walked too; nothing below asserts it)
        await DR._save_state(conn, {"cursor_below_id": top + 1})
        before = time.time()
        got = await D.after_cycle(conn, cycle={}, now=time.time())
        bf = got["research_observations"]["backfill"]
        assert bf["ok"] is True, bf
        assert bf["recorded"] >= 3 and bf["candidates"] <= \
            DR.BACKFILL_PER_CYCLE
        assert bf["not_observed"].get(DR.S_NO_PINNACLE, 0) >= 1
        assert bf["not_observed"].get(DR.S_BACKFILL_NO_PRICE_RECEIPT, 0) >= 1
        assert bf["cursor_below_id"] < top + 1
        for excluded in (nopin, noreceipt):
            assert await conn.fetchval(
                "SELECT count(*) FROM derek_research_observations "
                " WHERE valuation_id=$1", excluded) == 0

        for tag, vid in mine.items():
            v = dict(await conn.fetchrow(
                "SELECT *, extract(epoch FROM decided_at) AS d, "
                " extract(epoch FROM observed_at) AS ob, "
                " extract(epoch FROM received_at) AS rc "
                " FROM external_valuations WHERE id=$1", vid))
            o = dict(await conn.fetchrow(
                "SELECT *, extract(epoch FROM decided_at) AS d, "
                " extract(epoch FROM recorded_at) AS rec, "
                " extract(epoch FROM price_received_at) AS prc, "
                " extract(epoch FROM price_source_ts) AS pts, "
                " extract(epoch FROM pinnacle_observed_at) AS pob, "
                " extract(epoch FROM pinnacle_received_at) AS prv "
                " FROM derek_research_observations WHERE valuation_id=$1",
                vid))
            assert o["collection_mode"] == DR.MODE_BACKFILL, tag
            assert o["evidence_class"] == FM.EVIDENCE_CLASS_STORED
            assert o["valuation_id"] == vid
            # RECORDED NOW; DECIDED WHEN THE VALUATION SAYS
            assert float(o["rec"]) >= before - 5.0
            assert float(o["d"]) == pytest.approx(float(v["d"]), abs=1e-3)
            # THE PINNACLE READING IS THE STORED ONE
            assert o["pinnacle_p"] == pytest.approx(v["probability"])
            assert float(o["pob"]) == pytest.approx(float(v["ob"]), abs=1e-3)
            assert float(o["prv"]) == pytest.approx(float(v["rc"]), abs=1e-3)
            assert o["devig_method"] == v["devig_method"]
            assert o["pinnacle_source_version"] == v["version"]
            assert (o["pinnacle_overround"] is None) == \
                (v["overround"] is None)
            # NO PREDICTION INVENTED
            assert o["model_p"] is None and o["model_id"] is None
            assert o["model_absent_reason"] == DR.A_BACKFILL
            if v["record_purpose"] == vp.CALIBRATION_ONLY:
                ev = json.loads(v["calibration_only_evidence"])
                shown = ev["displayed_quote"]
                assert o["price"] == pytest.approx(
                    ev["compared_at_the_displayed_price"]["price"])
                assert float(o["prc"]) == pytest.approx(shown["read_at"],
                                                        abs=1e-3)
                if shown.get("venue_ts") is None:
                    assert o["pts"] is None     # not supplied -> NULL
                    assert "NOT_SUPPLIED_BY_THE_VENUE" in \
                        o["price_source_ts_basis"]
                else:
                    assert float(o["pts"]) == pytest.approx(
                        shown["venue_ts"], abs=1e-3)
                assert o["cohort"] == FM.COHORT_DISPLAYED
            else:
                assert o["price"] == pytest.approx(v["executable_price"])
                clock = json.loads(v["risk_verdict"])["freshness_evidence"][
                    "venue_clock"]
                assert float(o["prc"]) == pytest.approx(
                    clock["our_response_received_at"], abs=1e-3)
                # the venue supplied no stamp: NULL, with the clock's reason
                assert o["pts"] is None
                assert "VENUE_CLOCK_NOT_PROVIDED" in o["price_source_ts_basis"]
                assert o["price_timing_uncertainty"] == DR.TIMING_BOUNDED
                assert o["cohort"] == FM.COHORT_EXECUTABLE

        # ── TWICE INSERTS NOTHING ────────────────────────────────────
        # Over the id range the first step walked, [its cursor, top]:
        lo = bf["cursor_below_id"]
        span = ("SELECT count(*) FROM derek_research_observations "
                " WHERE valuation_id >= $1 AND valuation_id <= $2")
        n_span = await conn.fetchval(span, lo, top)
        n_mine = await conn.fetchval(
            "SELECT count(*) FROM derek_research_observations "
            " WHERE valuation_id = ANY($1::bigint[])", list(mine.values()))
        assert n_mine == 3
        # (a) the same walk again, from the same cursor, the same size
        await DR._save_state(conn, {"cursor_below_id": top + 1})
        again = await DR.backfill(conn, older_than=time.time() - 1800.0,
                                  now=time.time(), limit=bf["candidates"])
        assert again["ok"] is True
        assert await conn.fetchval(span, lo, top) == n_span
        # (b) the rows handed straight to the recorder: ON CONFLICT
        rows = [dict(r) for r in await conn.fetch(
            DR._SELECT.replace("AND NOT EXISTS",
                               "AND v.id = ANY($2::bigint[]) AND EXISTS"),
            ext.EXPERIMENT_ID, list(mine.values()))]
        assert len(rows) == 3
        out = {"recorded": 0, "already_recorded": 0, "not_observed": {},
               "by_cohort": {}, "model_frozen": 0, "errors": {}}
        await DR._record_rows(conn, rows, out, approved=None,
                              mode=DR.MODE_BACKFILL)
        assert out["recorded"] == 0 and out["already_recorded"] == 3, out
        assert await conn.fetchval(
            "SELECT count(*) FROM derek_research_observations "
            " WHERE valuation_id = ANY($1::bigint[])",
            list(mine.values())) == n_mine == 3

        # ── EXHAUSTION: a step that finds nothing ends the backfill ──
        await DR._save_state(conn, {"cursor_below_id": top + 1})
        done = await DR.backfill(conn, older_than=1.0, now=time.time())
        assert done["exhausted"] is True and done["recorded"] == 0
        skip = await DR.backfill(conn, older_than=time.time(),
                                 now=time.time())
        assert skip["skipped"] is True and skip["recorded"] == 0
    finally:
        if prior is not _UNSET:
            await _restore_backfill(conn, prior)
        await _purge(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 10 · THE DAILY MODEL RUN, BOTH BRANCHES, THROUGH after_cycle
# ═════════════════════════════════════════════════════════════════════════

@pg
async def test_the_daily_model_run_fits_and_evaluates_but_never_promotes(
        monkeypatch):
    import asyncpg
    conn = await _connect()
    prior = _UNSET
    try:
        await _ensure_schema(conn)
        prior = await _pause_backfill(conn)
        await _purge(conn)
        day1, day2 = RUN_DAYS
        start = time.time() - 3 * 86400.0
        # 12 labelled displayed-price fixtures (SYNTHETIC)
        few = await _calibration_cohort(
            conn, tag="run-a", rule={0.30: 2, 0.50: 3, 0.70: 4},
            start=start, per=4)
        _stub_settlements(monkeypatch, {c: w for _v, c, w, _t in few})
        await loop.join_outcomes(conn, limit=100000)

        # ── BRANCH 1: TOO FEW -> ONE RUN, THE EXACT COUNTS ───────────
        got = await D.after_cycle(conn, cycle={}, now=day1)
        run = got["model_run"]
        assert run["ran"] is True, run
        assert run["outcome"] == DR.RUN_INSUFFICIENT
        disp = run["counts"]["by_cohort"][FM.COHORT_DISPLAYED]
        exe = run["counts"]["by_cohort"][FM.COHORT_EXECUTABLE]
        assert disp["have_labelled_fixtures"] == 12, disp
        assert disp["need"] == FM.MIN_TRAIN_EVENTS == 40
        assert disp["shortfall"] == 40 - 12
        assert exe["have_labelled_fixtures"] == 0 and exe["shortfall"] == 40
        assert run["counts"]["need_prospective_fixtures"] == 40
        assert run["promoted"] is False
        again = await D.after_cycle(conn, cycle={}, now=day1 + 60.0)
        assert again["model_run"]["already_ran"] is True
        assert await conn.fetchval(
            "SELECT count(*) FROM derek_research_model_runs "
            " WHERE run_day=$1", DR._day_of(day1)) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_models WHERE model_key=$1",
            FM.KEY_ENTRY_PAYOUT) == 0

        # ── BRANCH 2: ENOUGH -> FIT, REGISTER, EVALUATE; NO PROMOTION ─
        more = await _calibration_cohort(conn, tag="run-b",
                                         rule=RULE_INFORMATIVE,
                                         start=start + 1000.0)
        _stub_settlements(monkeypatch, {c: w for _v, c, w, _t in more})
        await loop.join_outcomes(conn, limit=100000)
        got = await D.after_cycle(conn, cycle={}, now=day2)
        run = got["model_run"]
        assert run["outcome"] == DR.RUN_FITTED, run
        disp = run["counts"]["by_cohort"][FM.COHORT_DISPLAYED]
        assert disp["have_labelled_fixtures"] == 12 + 3 * PER_PRICE
        assert disp["shortfall"] == 0
        f = run["fitted"][FM.COHORT_DISPLAYED]
        assert f["refit"] is True and f["ok"] is True, f
        # EVERY ATTEMPT IS LISTED ON THE RUN AND RECORDED
        assert run["attempted_model_ids"] == [f["model_id"]]
        assert list(await conn.fetchval(
            "SELECT attempted_model_ids FROM derek_research_model_runs "
            " WHERE run_day=$1", DR._day_of(day2))) == [f["model_id"]]
        a = await conn.fetchrow("SELECT * FROM derek_research_model_attempts"
                                " WHERE attempt_id=$1", f["model_id"])
        assert a["outcome"] == DR.ATTEMPT_REGISTERED
        assert a["run_id"] == run["run_id"]
        assert a["train_fixtures"] == 12 + 3 * PER_PRICE
        assert a["records_sha"] and a["attempted_set_sha"]
        assert json.loads(a["evaluation_cohort"])["cohorts"] == [
            FM.COHORT_DISPLAYED]
        # the INSUFFICIENT run attempted nothing
        assert list(await conn.fetchval(
            "SELECT attempted_model_ids FROM derek_research_model_runs "
            " WHERE run_day=$1", DR._day_of(day1))) == []
        # A FAILED ATTEMPT IS RECORDED TOO: the executable cohort has no
        # labelled fixture, so the fit is refused -- and written down.
        bad = await DR.attempt_fit(conn, model_id="derek-research-attempt-"
                                   "refused", cohort=FM.COHORT_EXECUTABLE,
                                   through=day2, now=day2)
        assert bad["outcome"] == DR.ATTEMPT_FIT_REFUSED
        assert bad["recorded"] is True
        b = await conn.fetchrow("SELECT * FROM derek_research_model_attempts"
                                " WHERE attempt_id=$1", bad["attempt_id"])
        assert b["outcome"] == DR.ATTEMPT_FIT_REFUSED and b["refusal"]
        assert b["train_rows"] == 0 and b["train_fixtures"] == 0
        assert b["records_sha"] is None
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_models WHERE model_id=$1",
            bad["attempt_id"]) == 0
        assert f["model_id"] == "%s:%s:%s" % (
            DR.AUTO_MODEL_PREFIX, FM.COHORT_DISPLAYED, DR._day_of(day2))
        assert f["training_fixtures"] == 12 + 3 * PER_PRICE
        # the executable cohort is NOT pooled in: too few, not fitted
        assert FM.COHORT_EXECUTABLE not in run["fitted"]
        m = await conn.fetchrow(
            "SELECT state, approved_by, evaluation, training_provenance "
            "  FROM bettor_funded_models WHERE model_id=$1", f["model_id"])
        assert m["state"] == FM.STATE_CANDIDATE and m["approved_by"] is None
        prov = json.loads(m["training_provenance"])
        assert prov["training_population"]["training_cohorts"] == [
            FM.COHORT_DISPLAYED]
        ev = json.loads(m["evaluation"])
        assert "market_comparison" in ev and "approval_eligibility" in ev
        row = await conn.fetchrow(
            "SELECT outcome, evaluations, promoted FROM "
            " derek_research_model_runs WHERE run_day=$1", DR._day_of(day2))
        assert row["outcome"] == DR.RUN_FITTED and row["promoted"] is False
        evs = json.loads(row["evaluations"])
        e = evs[f["model_id"]]
        # evaluated against price, Pinnacle and the base rate -- here with
        # no prospective fixture yet, so it is honestly not eligible
        for k in ("model_vs_raw_venue_price", "model_vs_training_base_rate",
                  "model_vs_pinnacle"):
            assert k in e
        assert e["ok"] is False and e["refusal"] == FM.R_TOO_FEW_LABELS
        assert e["promoted"] is False
        # NEVER PROMOTED ON A SCHEDULE
        assert (await FM.approved(conn, model_key=FM.KEY_ENTRY_PAYOUT))[
            "refusal"] == FM.R_NO_APPROVED_MODEL
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await conn.execute("UPDATE derek_research_model_runs SET "
                               "promoted = TRUE WHERE run_day=$1",
                               DR._day_of(day2))

        # ── THE WORKSPACE SHOWS THE LATEST RUN AND ITS COUNTS ────────
        from sportsassets.api import agents_derek as A
        mq = (await A.workspace(conn))["sections"]["model_qualification"]
        lr_ = mq["data"]["latest_model_run"]
        assert lr_["run_day"] == str(DR._day_of(day2))
        assert lr_["outcome"] == DR.RUN_FITTED
        assert lr_["counts"]["by_cohort"][FM.COHORT_DISPLAYED][
            "have_labelled_fixtures"] == 12 + 3 * PER_PRICE
        assert lr_["promoted"] is False
        assert lr_["attempted_model_ids"] == [f["model_id"]]
        assert any(x["attempt_id"] == bad["attempt_id"]
                   for x in mq["data"]["recent_attempts"])
    finally:
        if prior is not _UNSET:
            await _restore_backfill(conn, prior)
        await _purge(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 11 · A BACKFILL IS NEVER PROSPECTIVE EVIDENCE
# ═════════════════════════════════════════════════════════════════════════

@pg
async def test_backfilled_rows_never_count_as_prospective_evidence(
        monkeypatch):
    """A model is fitted and registered; THEN fixtures decided after its
    registration arrive only through the backfill (RETROSPECTIVE_STORED) --
    the pattern the live collector would have produced, labelled and
    informative. They are reported as retrospective and count toward
    neither MIN_EVALUATION_EVENTS nor a promotion."""
    conn = await _connect()
    prior = _UNSET
    try:
        await _ensure_schema(conn)
        prior = await _pause_backfill(conn)
        await _purge(conn)
        mid = "derek-research-model-after-backfill"
        await _train_register(conn, monkeypatch, tag="retro",
                              rule=RULE_INFORMATIVE, model_id=mid)
        created = float((await conn.fetchval(
            "SELECT extract(epoch FROM created_at) FROM bettor_funded_models"
            " WHERE model_id=$1", mid)))
        # decided AFTER registration, never seen by the live collector
        t = time.time() + 120.0
        made = []
        for n, (price, won) in enumerate(_outcomes(RULE_INFORMATIVE)):
            cid = "%sretro-late-%03d" % (SYN, n)
            vid = await _calibration_row(conn, cid=cid, price=price,
                                         p_pin=min(0.97, price + 0.15),
                                         decided_at=t + n)
            made.append((vid, cid, won, t + n))
        top = max(v for v, *_ in made)
        await DR._save_state(conn, {"cursor_below_id": top + 1})
        bf = await DR.backfill(conn, older_than=t + len(made) + 1.0,
                               now=time.time(), limit=len(made))
        assert bf["recorded"] == len(made), bf
        await _label_prospectively(conn, made)
        classes = {r["evidence_class"] for r in await conn.fetch(
            "SELECT evidence_class FROM derek_research_observations "
            " WHERE valuation_id = ANY($1::bigint[])",
            [v for v, *_ in made])}
        assert classes == {FM.EVIDENCE_CLASS_STORED}
        assert all(d > created for *_x, d in made)   # decided after freeze

        ev = await FM.evaluate(conn, model_id=mid)
        assert ev["ok"] is False and ev["refusal"] == FM.R_TOO_FEW_LABELS
        doc = ev["evaluation"]
        assert doc["n_events"] == 0                       # prospective
        assert doc["PROSPECTIVE"]["n_events"] == 0
        np_ = doc["not_prospective"]
        assert np_["of_which_retrospective_stored"] >= len(made)
        rm = doc["retrospective_market_comparison"]
        assert rm["role"].startswith("RETROSPECTIVE_NOT_PROSPECTIVE")
        assert rm["n_events"] >= len(made)
        assert rm["evidence_class_mix"][FM.EVIDENCE_CLASS_STORED][
            "fixtures"] >= len(made)
        assert doc["approval_eligibility"]["eligible"] is False
        assert doc["approval_eligibility"]["prospective_fixtures_counted"] \
            == 0
        prom = await FM.promote(conn, model_id=mid, approved_by=APPROVER)
        assert prom["ok"] is False
        assert prom["refusal"] == FM.R_TOO_FEW_LABELS, prom
        assert (await FM.approved(conn, model_key=FM.KEY_ENTRY_PAYOUT))[
            "refusal"] == FM.R_NO_APPROVED_MODEL
    finally:
        if prior is not _UNSET:
            await _restore_backfill(conn, prior)
        await _purge(conn)
        await conn.close()

"""LAB-F DRIFT SENTINEL OVER POSTGRES: PRODUCTION-SHAPED READS, ANTI-LOOKAHEAD,
THE SHADOW RECORD (migration 244) AND THE READ-ONLY ENDPOINT.

ALL DATA HERE IS SYNTHETIC TEST DATA written into a scratch test database
inside a transaction every test rolls back, under a fresh paper account. The
rows carry the JSON shapes production writes (research-sql run 37231355643,
section G: pinnacle {p, at, received_at, age_s, provider, ...} with epoch
numbers, book {age_at_decision_s, observed_at_is, levels, ...},
policy_decision {gross_edge_pp, parameters {version_id, source}},
economics {acquisition: null | {qty, expected_net_profit_usd, ...}}, label
{competition, market_type, event_key}), and go through the real paper tables
and their triggers (the ENTRY order's sleeve classification is written by
migration 223's trigger, exactly as in production).

The clock is 2017-07-14 (1.5e9): every other suite's rows are recorded after
it, so the point-in-time accessor keeps them out and the measurement sees only
this test's rows -- which is itself the anti-lookahead property under test.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import asyncpg
import pytest

from sportsassets.lab import drift_reads as R
from sportsassets.lab import drift_sentinel as DS
from sportsassets.lab import drift_store as ST

try:
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
UP = (MIG / "244_lab_drift_sentinel.sql").read_text()
DOWN = (MIG / "rollback" / "244_lab_drift_sentinel.down.sql").read_text()

HOUR = 3600.0
DAY = 86400.0
CLOCK = 1_500_000_000.0
FIRST = CLOCK - 10 * DAY            # the version's first decision
CG = "PINNACLE_COMPLETED_GAME_PAPER"
PV = "PINNACLE_COMPLETED_GAME_PAPER_V3"
PARAM = "paperparam:PINNACLE_COMPLETED_GAME_PAPER:V2"
SIM = "PAPER_SIM_V1"


def _uid(tag="x"):
    return "%s%s" % (tag, uuid.uuid4().hex[:12])


async def _tx():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    if await conn.fetchval("SELECT to_regclass('lab_drift_runs') IS NULL"):
        await conn.execute(UP)
    return conn, tx


async def _decision(conn, acct, *, at, recorded=None, gross=None,
                    verdict="REFUSE", refusal="BELOW_MIN_GROSS_EDGE",
                    slug=None, league="MLB", provider="pinnapi.com/raw-websocket",
                    latency=0.5, age=6.0, book_age=0.4, acquisition=None,
                    pv=PV, param=PARAM, fixture=None):
    did = "papercg:" + _uid()
    slug = slug or _uid("lab-mkt-")
    pin = {"p": 0.55, "at": at - age, "received_at": at - age + latency,
           "age_s": age, "limit_s": 30.0, "method": "power",
           "provider": provider, "qualified": True, "qualification": "FRESH",
           "refusal": None, "decided_at": at}
    book = {"book_obs_id": None, "observed_at": at - book_age,
            "observed_at_is": "OUR_RECEIPT_INSTANT",
            "age_at_decision_s": book_age, "error": None,
            "levels": [{"price": 0.5, "qty": 100.0}], "depth_levels": 1,
            "displayed_depth": 100.0, "book_currency": "NOT_ESTABLISHED",
            "basis": "OBSERVED_PAPER_BOOK_LEVELS_NOT_THE_VALUATION_QUOTE"}
    pdx = {"strategy": CG, "policy_version": pv, "threshold_edge_pp": 0.5,
           "gross_edge_pp": gross, "edge_at_vwap_pp": None,
           "net_expected_profit_usd": (acquisition or {}).get(
               "expected_net_profit_usd"),
           "admitted": verdict == "ENTER",
           "refusal": None if verdict == "ENTER" else refusal,
           "parameters": {"policy_key": CG, "source": "ACTIVE_VERSION",
                          "version_id": param, "version_no": 2,
                          "values": {"min_gross_edge_pp": 0.5}}}
    econ = {"strategy": CG, "version": pv, "threshold_edge_pp": 0.5,
            "best_level_edge_pp": gross, "acquisition": acquisition,
            "refusals": [] if verdict == "ENTER" else [refusal]}
    label = {"competition": league, "market_type": "MONEYLINE",
             "event_key": "e-" + slug, "strategy": CG,
             "policy_version": pv}
    await conn.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, us_market_slug, holding_side, fixture, label, verdict, "
        " refusal, refusals, internal_model, p_pinnacle, pinnacle, book, "
        " economics, qualification_gaps, policy_version, policy_decision, "
        " simulator_version, strategy, recorded_at) VALUES ($1,$2,$3,"
        " to_timestamp($4),$5,'LONG',$6,$7::jsonb,$8,$9,$10,'{}'::jsonb,0.55,"
        " $11::jsonb,$12::jsonb,$13::jsonb,'[]'::jsonb,$14,$15::jsonb,$16,"
        " $17,to_timestamp($18))",
        did, acct["session_id"], acct["account_id"], float(at), slug,
        fixture or ("event:" + slug), json.dumps(label), verdict,
        None if verdict == "ENTER" else refusal,
        [] if verdict == "ENTER" else [refusal], json.dumps(pin),
        json.dumps(book), json.dumps(econ), pv, json.dumps(pdx), SIM, CG,
        float(recorded if recorded is not None else at + 0.25))
    return {"decision_id": did, "slug": slug, "at": at}


async def _entry(conn, acct, d, *, filled, qty=10.0, terminal_after=60.0,
                 fill_recorded=None):
    gid = "paper_group_" + _uid()
    oid = "paperord:" + _uid()
    at = d["at"]
    # THE SLEEVE CLASSIFICATION AT THE ORDER'S OWN INSTANT. In production
    # migration 223's AFTER INSERT trigger writes it in the order's own
    # transaction, so classified_at == the order's created_at. The trigger
    # stamps clock_timestamp(), i.e. the test's wall clock, which is years
    # after this test's back-dated clock; the same row (the trigger's exact
    # columns and functions) is written first with the order's instant, and
    # the trigger's ON CONFLICT DO NOTHING then keeps it.
    await conn.execute(
        "INSERT INTO paper_sleeve_classifications (classification_id, "
        " account_id, group_id, sleeve, strategy, policy_version, "
        " decision_id, entry_order_id, basis, classifier_version, "
        " classified_by, entry_at, classified_at) "
        "SELECT 'psc:' || md5($1 || ':PAPER_SLEEVE_V1'), $2, $1, "
        "       paper_sleeve_of($3, d.policy_version), $3, d.policy_version, "
        "       $4, $5, paper_sleeve_basis($3, d.policy_version), "
        "       'PAPER_SLEEVE_V1', 'ENTRY_TRIGGER', to_timestamp($6), "
        "       to_timestamp($6) "
        "  FROM paper_decisions d WHERE d.decision_id = $4",
        gid, acct["account_id"], CG, d["decision_id"], oid, float(at) + 1)
    await conn.execute(
        "INSERT INTO paper_orders (order_id, idempotency_key, account_id, "
        " session_id, group_id, role, direction, holding_side, intent, "
        " us_market_slug, fixture, label, order_type, time_in_force, "
        " allow_partial, qty, limit_price, wire_price, filled_qty, state, "
        " decision_id, decided_at, eligible_at, expires_at, "
        " simulator_version, strategy, created_at, terminal_at, "
        " terminal_reason) VALUES ($1,$1,$2,$3,$4,'ENTRY','BUY','LONG',"
        " 'ORDER_INTENT_BUY_LONG',$5,$6,'{}'::jsonb,'MARKETABLE','IOC',true,"
        " $7,0.5,0.5,$8,$9,$10,to_timestamp($11),to_timestamp($11),"
        " to_timestamp($11 + 90),$12,$13,to_timestamp($11 + 1),"
        " to_timestamp($11 + $14),$15)",
        oid, acct["account_id"], acct["session_id"], gid, d["slug"],
        "event:" + d["slug"], qty, qty if filled else 0.0,
        "FILLED" if filled else "EXPIRED", d["decision_id"], float(at), SIM,
        CG, float(terminal_after),
        "FILLED" if filled else "THE_OBSERVED_BOOK_WAS_UNREADABLE")
    if filled:
        await conn.execute(
            "INSERT INTO paper_fills (fill_id, idempotency_key, order_id, "
            " account_id, session_id, group_id, role, direction, "
            " holding_side, us_market_slug, fixture, label, qty, price, "
            " wire_price, fee_usd, gross_usd, filled_at, basis, "
            " simulator_version, strategy, recorded_at) VALUES ($1,$1,$2,$3,"
            " $4,$5,'ENTRY','BUY','LONG',$6,$7,'{}'::jsonb,$8,0.5,0.5,0.01,"
            " $9,to_timestamp($10),'DEPTH_WALK_WITHIN_LIMIT',$11,$12,"
            " to_timestamp($13))",
            "paperfill:" + _uid(), oid, acct["account_id"],
            acct["session_id"], gid, d["slug"], "event:" + d["slug"], qty,
            qty * 0.5, float(at) + 30, SIM, CG,
            float(fill_recorded if fill_recorded is not None else at + 31))
    return {"order_id": oid, "group_id": gid}


async def _eddie(conn, d, *, slip, adverse, created, version="TEST"):
    eid = "eddie_est:" + _uid()
    unm = {k: "TEST" for k in (
        "theoretical_edge", "fees", "spread_cost", "slippage",
        "adverse_selection", "fill_probability", "time_to_fill",
        "capital_hours", "max_executable_size", "net_executable_edge")}
    refs = json.dumps([{"kind": "paper_decision", "id": d["decision_id"]}])
    await conn.execute(
        "INSERT INTO eddie_execution_estimates (estimate_id, decision_id, "
        " estimator_version, estimated_at, recommendation, "
        " recommendation_reason, unmeasured, evidence_refs) VALUES ($1,$2,"
        " $6,to_timestamp($3),'WAIT','TEST',$4::jsonb,$5::jsonb)",
        eid, d["decision_id"], float(d["at"]), json.dumps(unm), refs,
        version)
    await conn.execute(
        "INSERT INTO eddie_execution_outcomes (outcome_id, estimate_id, "
        " decision_id, source, measured_at, realized_slippage_pp, "
        " realized_adverse_selection_pp, unmeasured, evidence_refs, "
        " created_at) VALUES ($1,$2,$3,'PAPER',to_timestamp($4),$5,$6,$7::jsonb,"
        " $8::jsonb,to_timestamp($4))",
        "eddie_out:" + _uid(), eid, d["decision_id"], float(created), slip,
        adverse, json.dumps({"realized_execution_loss": "TEST",
                             "predicted_execution_loss": "TEST"}), refs)


async def _econ(conn, acct, o, d, *, released, hours, computed, state="CLOSED",
                revision=1):
    pk = "paperpos:%s:%s:%s:LONG" % (acct["account_id"], o["group_id"],
                                     d["slug"])
    unm = {"expected_net_profit_usd": "TEST", "expected_capital_hours": "TEST",
           "realized_profit_per_capital_hour": "TEST",
           "expected_profit_per_capital_hour": "TEST", "capital_hours": "TEST"}
    if state != "CLOSED":
        unm["net_profit_usd"] = "OPEN"
    await conn.execute(
        "INSERT INTO pos_position_economics (econ_id, book, position_key, "
        " revision, run_id, computed_at, group_id, strategy, state, "
        " released_at, time_committed_h, net_profit_usd, unmeasured, "
        " content_sha256, version) VALUES ($1,'PAPER',$2,$3,'run-lab',"
        " to_timestamp($4),$5,$6,$7,to_timestamp($8),$9,$10,$11::jsonb,'sha',"
        " 'TEST')", "econ-lab-" + _uid(), pk, revision, float(computed),
        o["group_id"], CG, state, float(released), hours,
        1.0 if state == "CLOSED" else None, json.dumps(unm))
    return pk


async def _settle(conn, acct, o, d, *, outcome, settled, recorded,
                  version=1):
    pk = "paperpos:%s:%s:%s:LONG" % (acct["account_id"], o["group_id"],
                                     d["slug"])
    await conn.execute(
        "INSERT INTO paper_settlements (settlement_id, account_id, "
        " position_key, settlement_event_key, version, group_id, "
        " us_market_slug, holding_side, qty, outcome, payout_per_contract, "
        " payout_usd, evidence, evidence_source, settled_at, recorded_at) "
        " VALUES ($1,$2,$3,'test',$9,$4,$5,'LONG',10,$6,1,10,'{}'::jsonb,"
        " 'TEST_EVIDENCE',to_timestamp($7),to_timestamp($8))",
        "papersettle:" + _uid(), acct["account_id"], pk, o["group_id"],
        d["slug"], outcome, float(settled), float(recorded), int(version))


async def _world(conn):
    """25 contracts per window (two rows each); the comparison window's
    gross edge shifted +3 pp and its probability source switched; 20 ENTER
    decisions per window with ENTRY orders (half filled), Eddie outcomes,
    closed position economics and settlements."""
    acct = await H.new_account(conn, "labdrift", now=FIRST - DAY)
    w = DS.derive_windows([{"strategy": CG, "policy_version": PV,
                            "param_version_id": PARAM, "first_at": FIRST,
                            "last_at": CLOCK - 60, "n": 1}],
                          [{"strategy": CG, "policy_version": PV,
                            "param_version_id": PARAM, "t": CLOCK - 60}],
                          clock=CLOCK)[CG]
    out = {"acct": acct, "windows": w, "enters": {"ref": [], "cmp": []}}
    # the version's first decision, exactly at FIRST
    await _decision(conn, acct, at=FIRST, gross=0.0, slug=_uid("lab-first-"))
    for win, shift, prov in (("ref", 0.0, "the-odds-api.com/v4"),
                             ("cmp", 3.0, "pinnapi.com/raw-websocket")):
        a, b = w[win]
        for u in range(25):
            slug = "lab-%s-%d-%s" % (win, u, _uid())
            for k in range(2):
                t = a + (b - a) * (u * 2 + k + 1) / 52.0
                await _decision(conn, acct, at=t, gross=shift + (u % 5) * 0.1,
                                slug=slug, provider=prov,
                                latency=0.25 + k * 0.5)
        for u in range(20):
            t = a + (b - a) * (u + 0.5) / 21.0
            d = await _decision(
                conn, acct, at=t, verdict="ENTER", refusal=None,
                gross=shift + 1.0, slug="lab-enter-%s-%d-%s" % (win, u, _uid()),
                provider=prov,
                acquisition={"qty": 20.0, "expected_net_profit_usd": 0.4,
                             "vwap": 0.5, "fees_usd": 0.2})
            o = await _entry(conn, acct, d, filled=(u % 2 == 0))
            out["enters"][win].append((d, o))
            await _eddie(conn, d, slip=0.1 * u, adverse=None if u % 4 else 0.3,
                         created=t + 120)
            if u % 2 == 0:
                await _econ(conn, acct, o, d, released=t + 2 * HOUR,
                            hours=2.0, computed=t + 3 * HOUR)
                await _settle(conn, acct, o, d,
                              outcome="WON" if u % 4 else "VOID_REFUND",
                              settled=t + 2 * HOUR, recorded=t + 2 * HOUR + 5)
    # the strategy's latest decision, inside the comparison horizon
    await _decision(conn, acct, at=CLOCK - 60, gross=3.0,
                    slug=_uid("lab-last-"))
    return out


async def _gather(conn, clock=CLOCK):
    return await R.gather(conn, clock=clock, since=None,
                          ref_max_s=DS.DEFAULTS["ref_max_s"],
                          cmp_max_s=DS.DEFAULTS["cmp_max_s"])


def _f(rep, metric, evidence=DS.OBSERVED, league=DS.ALL):
    (f,) = [x for x in rep["findings"] if x["strategy"] == CG
            and x["metric"] == metric and x["evidence_class"] == evidence
            and x["league"] == league]
    return f


# ═════════════════════════════════════════════════════════════════════
# PRODUCTION-SHAPED READS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_production_shaped_rows_are_read_and_measured():
    conn, tx = await _tx()
    try:
        world = await _world(conn)
        data = await _gather(conn)
        mine = [r for r in data["versions"] if r["strategy"] == CG
                and r["policy_version"] == PV]
        assert mine and mine[0]["first_at"] == pytest.approx(FIRST, abs=1e-3)
        assert mine[0]["param_version_id"] == PARAM
        rep = DS.compute(data)
        w = rep["windows"][CG]
        assert w["version_key"] == "%s|%s" % (PV, PARAM)
        assert w["ref"] == pytest.approx(world["windows"]["ref"], abs=1e-3)
        # the decision JSON is read as production writes it
        row = next(r for r in data["decisions"] if r["verdict"] == "ENTER")
        assert float(row["acq_qty"]) == 20.0
        assert float(row["net_ev_usd"]) == 0.4
        assert row["pin_provider"] in ("the-odds-api.com/v4",
                                       "pinnapi.com/raw-websocket")
        obs = DS.decision_observations(data["decisions"], rep["windows"])[CG]
        ex = [o for o in obs if o["metric"] == "executable_edge_pp"]
        assert ex and all(o["value"] == pytest.approx(2.0) for o in ex)
        lat = sorted({round(o["value"], 6) for o in obs
                      if o["metric"] == "source_latency_s"})
        # received_at - at: 0.25 / 0.75 on the refused rows, 0.5 on entries
        assert lat == [0.25, 0.5, 0.75]
        # gross edge: 46 contracts a window (25 refused + 20 entered, plus
        # the version's first decision / the strategy's latest one)
        g = _f(rep, "gross_edge_pp")
        assert (g["n_ref"], g["n_cmp"]) == (46, 46)
        assert g["status"] == DS.MATERIAL
        assert g["statistic"]["quantile_shifts"][1]["shift"] == \
            pytest.approx(3.0, abs=0.5)
        assert _f(rep, "probability_source_mix")["status"] == DS.MATERIAL
        assert _f(rep, "book_age_s")["status"] == DS.NORMAL
        # PAPER_SIMULATION from the real order / fill / sleeve tables
        fp = _f(rep, "fill_probability", DS.PAPER_SIM)
        assert (fp["n_ref"], fp["n_cmp"]) == (20, 20)
        assert fp["statistic"]["ref_shares"] == {"FILLED": 0.5,
                                                 "NOT_FILLED": 0.5}
        assert fp["status"] == DS.NORMAL
        sl = _f(rep, "slippage_pp", DS.PAPER_SIM)
        assert (sl["n_ref"], sl["n_cmp"]) == (20, 20)
        mk = _f(rep, "markout_pp", DS.PAPER_SIM)
        assert mk["status"] == DS.UNAVAILABLE and "INSUFFICIENT" in mk["why"]
        assert (mk["n_ref"], mk["n_cmp"]) == (5, 5)
        cr = _f(rep, "capital_release_h", DS.PAPER_SIM)
        assert (cr["n_ref"], cr["n_cmp"]) == (10, 10)   # via the sleeve trigger
        se = _f(rep, "settlement_exception", DS.PAPER_SIM)
        assert (se["n_ref"], se["n_cmp"]) == (10, 10)
        assert rep["context"]["positions_unattributed"] == {"econ": 0,
                                                            "settlements": 0}
        assert rep["question_h"]["answer"] == "YES_MATERIAL_DRIFT_PRESENT"
        assert CG in rep["question_h"]["material"]
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# ANTI-LOOKAHEAD
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_deliberately_future_dated_rows_change_nothing():
    conn, tx = await _tx()
    try:
        world = await _world(conn)
        before = DS.compute(await _gather(conn))
        acct = world["acct"]
        # (1) a NEW policy version decided before the clock, RECORDED after
        #     it: leaked, it would become the active version
        await _decision(conn, acct, at=CLOCK - 30, recorded=CLOCK + HOUR,
                        pv="PINNACLE_COMPLETED_GAME_PAPER_V9", gross=50.0,
                        slug=_uid("lab-leak-"))
        # (2) rows of the active version decided after the clock
        for k in range(30):
            await _decision(conn, acct, at=CLOCK + 10 + k, gross=-40.0,
                            slug=_uid("lab-future-"), provider="LEAK")
        # (3) rows of the active version recorded after the clock
        for k in range(30):
            await _decision(conn, acct, at=CLOCK - 3600 + k,
                            recorded=CLOCK + 5 + k, gross=99.0,
                            slug=_uid("lab-late-"), provider="LEAK")
        # (4) a fill recorded after the clock on an order that had none
        for d, o in world["enters"]["cmp"]:
            if not await conn.fetchval(
                    "SELECT count(*) FROM paper_fills WHERE order_id=$1",
                    o["order_id"]):
                break
        await conn.execute(
            "INSERT INTO paper_fills (fill_id, idempotency_key, order_id, "
            " account_id, session_id, group_id, role, direction, "
            " holding_side, us_market_slug, fixture, label, qty, price, "
            " wire_price, fee_usd, gross_usd, filled_at, basis, "
            " simulator_version, strategy, recorded_at) VALUES ($1,$1,$2,$3,"
            " $4,$5,'ENTRY','BUY','LONG',$6,'fx','{}'::jsonb,10,0.5,0.5,0,5,"
            " to_timestamp($7),'DEPTH_WALK_WITHIN_LIMIT',$8,$9,"
            " to_timestamp($10))", "paperfill:" + _uid(), o["order_id"],
            acct["account_id"], acct["session_id"], o["group_id"], d["slug"],
            CLOCK - 10, SIM, CG, CLOCK + 50)
        # (5) an Eddie outcome created after the clock; (6) a newer economics
        #     revision computed after it, reopening a position
        await _eddie(conn, d, slip=77.0, adverse=77.0, created=CLOCK + 70,
                     version="TEST_LATE")
        d2, o2 = world["enters"]["cmp"][0]
        await _econ(conn, acct, o2, d2, released=CLOCK - 100, hours=999.0,
                    computed=CLOCK + 80, revision=2)
        await _settle(conn, acct, o2, d2, outcome="SETTLED_AT_VENUE_PRICE",
                      settled=CLOCK - 50, recorded=CLOCK + 90, version=2)
        data = await _gather(conn)
        after = DS.compute(data)
        assert after["windows"] == before["windows"]
        assert after["windows"][CG]["policy_version"] == PV
        assert after["findings"] == before["findings"]
        assert after["question_h"] == before["question_h"]
        assert not [r for r in data["decisions"]
                    if r["pin_provider"] == "LEAK"]
        # and THE ACCESSOR drops every one of them on its own, without the
        # SQL clock predicate (a reader that forgot it cannot leak)
        raw = R._rows(await conn.fetch(
            "SELECT extract(epoch FROM decided_at)::float8 AS t, "
            " extract(epoch FROM recorded_at)::float8 AS recorded_at "
            "  FROM paper_decisions WHERE pinnacle->>'provider' = 'LEAK' "
            "    OR policy_version = 'PINNACLE_COMPLETED_GAME_PAPER_V9'"))
        assert len(raw) == 61
        assert R.point_in_time(raw, CLOCK, source="decisions") == []
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# THE SHADOW RECORD (244)
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_run_is_recorded_append_only_and_once_per_episode():
    conn, tx = await _tx()
    try:
        await _world(conn)
        rep = DS.compute(await _gather(conn))
        got = await ST.record_run(conn, rep, recorded_by="test operator",
                                  computed_at=CLOCK + 10)
        assert got["recorded"], got
        rid = got["run_id"]
        row = await conn.fetchrow("SELECT * FROM lab_drift_runs WHERE "
                                  " run_id=$1", rid)
        assert row["authority"] == "SHADOW_RESEARCH_ONLY"
        assert row["production_effect"] == "NONE"
        assert json.loads(row["question_h"])["answer"] == \
            "YES_MATERIAL_DRIFT_PRESENT"
        assert row["report_sha256"] == ST.canonical_sha256(rep)
        assert await conn.fetchval(
            "SELECT count(*) FROM lab_drift_findings WHERE run_id=$1",
            rid) == len(rep["findings"])
        tasks = await conn.fetch("SELECT * FROM lab_drift_tasks WHERE "
                                 " run_id=$1 ORDER BY task_id", rid)
        assert {t["task_class"] for t in tasks} == {"WORK_ITEM",
                                                    "RESEARCH_TASK"}
        assert {t["agent_id"] for t in tasks
                if t["task_class"] == "WORK_ITEM"} == {"AUDREY", "SCOUT"}
        # the same report again: nothing new
        again = await ST.record_run(conn, rep, recorded_by="test operator",
                                    computed_at=CLOCK + 20)
        assert again == {"recorded": False, "run_id": rid,
                         "why": "IDENTICAL_REPORT_ALREADY_RECORDED"}
        # a later run of the same episode adds a run but no new task
        rep2 = dict(rep, as_of=rep["as_of"] + 1)
        got2 = await ST.record_run(conn, rep2, recorded_by="test operator",
                                   computed_at=CLOCK + 30)
        assert got2["recorded"] and got2["tasks_new"] == 0
        # append-only
        for sql in ("UPDATE lab_drift_runs SET recorded_by='x'",
                    "DELETE FROM lab_drift_findings",
                    "UPDATE lab_drift_tasks SET kind='x'",
                    "TRUNCATE lab_drift_tasks"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute(sql)
            await sp.rollback()
        # nothing outside 244's tables and the reads' sources was written
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_work_requests WHERE request_id LIKE "
            " 'labdrift:%'") == 0 if await conn.fetchval(
            "SELECT to_regclass('agent_work_requests') IS NOT NULL") else True
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_the_database_refuses_what_the_record_may_not_say():
    conn, tx = await _tx()
    try:
        await conn.execute(
            "INSERT INTO lab_drift_runs (run_id, as_of, computed_at, "
            " sentinel_version, stats_version, params, question_h, "
            " strategies, findings_n, tests_n, report_sha256, recorded_by) "
            " VALUES ('labdriftrun:' || repeat('a', 24), to_timestamp(1), "
            " to_timestamp(2), 'v', 's', '{}', '{\"answer\": \"X\"}', '[]', "
            " 1, 0, repeat('b', 64), 't')")
        bad = [
            # a run computed before its own clock
            "INSERT INTO lab_drift_runs (run_id, as_of, computed_at, "
            " sentinel_version, stats_version, params, question_h, "
            " strategies, findings_n, tests_n, report_sha256, recorded_by) "
            " VALUES ('labdriftrun:' || repeat('c', 24), to_timestamp(5), "
            " to_timestamp(2), 'v', 's', '{}', '{\"answer\": \"X\"}', '[]', "
            " 0, 0, repeat('b', 64), 't')",
            # authority other than SHADOW_RESEARCH_ONLY
            "INSERT INTO lab_drift_runs (run_id, as_of, computed_at, "
            " sentinel_version, stats_version, params, question_h, "
            " strategies, findings_n, tests_n, report_sha256, recorded_by, "
            " authority) VALUES ('labdriftrun:' || repeat('d', 24), "
            " to_timestamp(1), to_timestamp(2), 'v', 's', '{}', "
            " '{\"answer\": \"X\"}', '[]', 0, 0, repeat('b', 64), 't', "
            " 'AUTHORITATIVE')",
            # UNAVAILABLE without its reason
            "INSERT INTO lab_drift_findings (run_id, finding_no, strategy, "
            " version_key, league, metric, evidence_class, status, n_ref, "
            " n_cmp, windows, source) VALUES ('labdriftrun:' || "
            " repeat('a', 24), 1, 's', 'v', 'ALL', 'm', 'PAPER_SIMULATION', "
            " 'UNAVAILABLE', 0, 0, '{}', 'src')",
            # a tested status without its p / q
            "INSERT INTO lab_drift_findings (run_id, finding_no, strategy, "
            " version_key, league, metric, evidence_class, status, n_ref, "
            " n_cmp, windows, source, statistic) VALUES ('labdriftrun:' || "
            " repeat('a', 24), 2, 's', 'v', 'ALL', 'm', 'PAPER_SIMULATION', "
            " 'MATERIAL_DRIFT', 30, 30, '{}', 'src', '{}')",
            # a work item claimed as enqueued, or for another agent
            "INSERT INTO lab_drift_tasks (task_id, run_id, task_class, "
            " agent_id, kind, strategy, version_key, record, "
            " integration_status) VALUES ('labdrift:' || repeat('e', 24), "
            " 'labdriftrun:' || repeat('a', 24), 'WORK_ITEM', 'AUDREY', "
            " 'POLICY_REVALIDATION', 's', 'v', '{}', 'ENQUEUED')",
            "INSERT INTO lab_drift_tasks (task_id, run_id, task_class, "
            " agent_id, kind, strategy, version_key, record, "
            " integration_status) VALUES ('labdrift:' || repeat('f', 24), "
            " 'labdriftrun:' || repeat('a', 24), 'WORK_ITEM', 'XAVIER', "
            " 'POLICY_REVALIDATION', 's', 'v', '{}', "
            " 'NOT_ENQUEUED_RETURNED_FOR_LATER_INTEGRATION')",
            # a production effect
            "INSERT INTO lab_drift_tasks (task_id, run_id, task_class, kind, "
            " strategy, version_key, record, production_effect) VALUES "
            " ('labdrifttask:' || repeat('a', 24), 'labdriftrun:' || "
            " repeat('a', 24), 'RESEARCH_TASK', 'TOURNAMENT_REVALIDATION', "
            " 's', 'v', '{}', 'POLICY_CHANGE')",
        ]
        for sql in bad:
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute(sql)
            await sp.rollback()
        # the rollback refuses while a run is recorded ...
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(DOWN)
        await sp.rollback()
        # ... and with none drops only 244's objects, then 244 re-applies
        sp = conn.transaction()
        await sp.start()
        try:
            await conn.execute("SET LOCAL session_replication_role = replica")
            for t in ("lab_drift_tasks", "lab_drift_findings",
                      "lab_drift_runs"):
                await conn.execute("DELETE FROM %s" % t)
            await conn.execute("SET LOCAL session_replication_role = origin")
            await conn.execute(DOWN)
            for t in ("lab_drift_runs", "lab_drift_findings",
                      "lab_drift_tasks"):
                assert await conn.fetchval("SELECT to_regclass($1)",
                                           t) is None, t
            assert await conn.fetchval(
                "SELECT to_regclass('paper_decisions')") is not None
            await conn.execute(UP)
            await conn.execute(UP)                   # idempotent
            assert await conn.fetchval(
                "SELECT to_regclass('lab_drift_runs')") is not None
        finally:
            await sp.rollback()
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# THE READ-ONLY ENDPOINT
# ═════════════════════════════════════════════════════════════════════

class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False
        return _Ctx()


@pg
async def test_the_endpoint_answers_question_h_and_writes_nothing(
        monkeypatch):
    from sportsassets.api import command_lab_drift as CLD
    conn, tx = await _tx()
    try:
        await _world(conn)

        async def pool():
            return _Pool(conn)
        monkeypatch.setattr(CLD, "_pool", pool)
        CLD._CACHE.clear()
        counts = {t: await conn.fetchval("SELECT count(*) FROM %s" % t)
                  for t in ("paper_decisions", "paper_orders", "paper_fills",
                            "lab_drift_runs", "lab_drift_tasks")}
        got = await CLD.lab_drift(as_of=CLOCK, strategy=None,
                                  findings="tested")
        assert got["status"] == "OK", got["why"]
        assert got["authority"] == "SHADOW_RESEARCH_ONLY"
        d = got["data"]
        assert d["question_h"]["answer"] == "YES_MATERIAL_DRIFT_PRESENT"
        assert all(f["status"] != DS.UNAVAILABLE for f in d["findings"])
        cg = [s for s in d["strategies"] if s["strategy"] == CG][0]
        assert cg["confidence_modifier"]["value"] == 0.5
        assert {w["agent_id"] for w in d["work_items"]} == {"AUDREY", "SCOUT"}
        only = await CLD.lab_drift(as_of=CLOCK, strategy=CG, findings="none")
        assert only["data"]["findings"] == []
        assert {s["strategy"] for s in only["data"]["strategies"]} == {CG}
        future = await CLD.lab_drift(as_of=4_000_000_000.0, strategy=None,
                                     findings="all")
        assert future["status"] == "UNAVAILABLE"
        assert future["why"] == "AS_OF_IS_IN_THE_FUTURE"
        after = {t: await conn.fetchval("SELECT count(*) FROM %s" % t)
                 for t in counts}
        assert after == counts
    finally:
        await tx.rollback()
        await conn.close()

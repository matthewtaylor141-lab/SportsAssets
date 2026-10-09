"""RC6 PROVENANCE OFFLOAD -- PROOF ON THE LEDGER: a research model that
verified a moment ago REFUSES as soon as its training set changes. No
success survives a change, whichever piece of a training record moved.

THE OWNER'S DIRECTIVE (2026-10-09): "preserve complete verification and
fail-closed behaviour; never cache a successful verification across changed
training records or settlements; add behavioural tests showing that
corrected labels, missing decisions and changed feature vectors still
refuse". The offload (ff85d723: bettor_funded_model.verify_provenance re-hashes
in `_reproduce` on the API's CPU lane; paper_derek.research_model reads the
registry WITHOUT the up-to-16 MB stored records and reads them back only to
name a mismatch) moved WHERE the check runs and WHAT the registry read
ships. These tests pin that WHAT IT DECIDES did not move.

THE PATH. The one a PAPER decision takes (paper_derek._context ->
research_model -> verify_provenance), on a REAL Postgres, through production's
own code end to end: valuations written as the entry experiment writes them,
observed by `derek_research.observation_from_row` and its INSERT, settled,
fitted by `bettor_funded_model.fit_from_records` and registered by
`register` (which itself re-reads and re-hashes, with the refit). Each test
first proves the model VERIFIES -- on the same model object it then
re-checks -- then changes ONE thing in the ledger and proves the next check
REFUSES (R_TRAINING_RECORDS_DO_NOT_REPRODUCE; Derek's model read refuses
RESEARCH_MODEL_PROVENANCE_NOT_VERIFIED), naming the changed decision where
there is one to name. The training-set hash ran on the CPU lane every time
(`_records_sha` spied: one hash per check, never on the loop thread) -- so a
refusal is a fresh computation, not a remembered one. The model a PAPER
decision reads is the slim one (no stored records), and a mismatch still
names the changed decision from the registry.

WHAT CHANGES, AND HOW. A corrected label and a changed settlement (its basis,
its reading instant) are UPDATEs of the valuation's settlement columns. A
missing decision is a settlement turned void (the row leaves the labelled
set) or the observation row gone. A changed feature vector is the vector and
its identity rewritten together. The research table is append-only by
trigger (migration 170), so the last two are written with the trigger
bypassed (session_replication_role = replica) -- a corruption or an
operator's repair, which is exactly what the provenance check exists to
catch. A vector rewritten WITHOUT its identity is
test_rc6_provenance_open_defects (it is NOT refused today).

ISOLATION. Every test runs in ONE transaction that is rolled back: the
other research observations and research models are hidden inside it, so
the fit sees this file's rows only, and nothing here survives the test.
ALL DATA SYNTHETIC: the prices, probabilities and outcomes are fixtures.

THE DATABASE. RN1X_TEST_DSN (the CI suites set it) or DATABASE_URL. A ledger
proof that cannot reach a ledger FAILS here; it does not skip.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import threading
import time

import pytest

LANE = "api-cpu"
SYN = "rc6-prov-proof-syn-"
N_ROWS = 40
DSN = (os.environ.get("RN1X_TEST_DSN") or os.environ.get("DATABASE_URL")
       or "")


def _dsn() -> str:
    if not DSN.startswith("postgres"):
        pytest.fail("needs RN1X_TEST_DSN (set by the CI suites) or "
                    "DATABASE_URL: a migrated Postgres. A provenance proof "
                    "that cannot reach the ledger proves nothing")
    return DSN


# ═════════════════════════════════════════════════════════════════════
# THE LEDGER, BUILT THROUGH PRODUCTION'S OWN WRITERS
# ═════════════════════════════════════════════════════════════════════

async def _valuation(conn, *, cid: str, price: float, decided: float) -> int:
    """A SYNTHETIC entry-experiment valuation, outcome unknown at insert
    (the table's own prospective-only trigger), in the shape the entry
    lane writes."""
    from sportsassets import bettor_external_shadow as ext
    return await conn.fetchval(
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
        " to_timestamp($3 - 5),to_timestamp($3 - 4),0.55,$4,0.0175,"
        " 'NO_TRADE', false, ARRAY['SYNTHETIC_RESEARCH_RECORD'],"
        " 'synthetic test evidence','HOME','ORDER_INTENT_BUY_LONG','ASK',"
        " 'ENTRY_DECISION',to_timestamp($3),'e-' || $2) RETURNING id",
        ext.EXPERIMENT_ID, cid, float(decided), float(price))


async def _ledger(conn, n: int = N_ROWS) -> list:
    """`n` settled research observations: [(valuation_id, observation_id,
    outcome)]. Each observation is written by `observation_from_row` and
    DR.INSERT_SQL, each settlement by the outcome join's own columns."""
    from sportsassets import bettor_external_shadow as ext
    from sportsassets.agents import derek_research as DR
    start = time.time() - 3 * 86400.0
    made = []
    for i in range(n):
        decided = start + 60.0 * i
        vid = await _valuation(conn, cid="%s%03d" % (SYN, i),
                               price=0.30 + 0.01 * (i % 40), decided=decided)
        row = await conn.fetchrow(DR._SELECT + " AND v.id = $2",
                                  ext.EXPERIMENT_ID, vid)
        obs, why = DR.observation_from_row(dict(row))
        assert obs is not None, why
        assert await conn.fetchval(DR.INSERT_SQL, *DR._insert_args(obs))
        won = 1 if i % 3 == 0 else 0
        await conn.execute(
            "UPDATE external_valuations SET outcome_known = TRUE, "
            " outcome = $2, outcome_at = to_timestamp($3), outcome_basis = $4"
            " WHERE id = $1 AND outcome_known = FALSE",
            vid, won, decided + 3600.0, "VENUE_SETTLEMENT_PRICE")
        made.append((vid, obs["observation_id"], won))
    return made


async def _isolate(conn) -> None:
    """Inside this test's transaction only (rolled back): no other research
    observation or entry-payout model is visible, so the fit is this file's
    rows and the newest candidate is this file's model."""
    from sportsassets import bettor_funded_model as FM
    await conn.execute("SET LOCAL session_replication_role = replica")
    await conn.execute("DELETE FROM derek_research_observations")
    await conn.execute("DELETE FROM bettor_funded_models WHERE model_key = $1",
                       FM.KEY_ENTRY_PAYOUT)
    await conn.execute("SET LOCAL session_replication_role = origin")


async def _registered(conn, model_id: str) -> dict:
    """fit_from_records -> register: the production fit and registration
    (register re-reads, re-hashes and refits before it writes)."""
    from sportsassets import bettor_funded_model as FM
    through = _dt.datetime.fromtimestamp(time.time(), _dt.timezone.utc)
    fit = await FM.fit_from_records(
        conn, through=through, model_key=FM.KEY_ENTRY_PAYOUT,
        source=FM.SOURCE_RESEARCH_OBSERVATIONS,
        cohorts=[FM.COHORT_EXECUTABLE])
    assert fit.get("ok"), fit.get("refusal")
    assert fit["train_rows"] == N_ROWS
    reg = await FM.register(conn, model_id=model_id, model_version=model_id,
                            fitted=fit, fit_through=through,
                            model_key=FM.KEY_ENTRY_PAYOUT)
    assert reg.get("ok"), (reg.get("refusal"), reg.get("why"))
    return reg


class _Spy:
    """Every training-set hash (`_records_sha`, the digest the check
    compares) and the thread it ran on. The same function on every tree:
    on production's RC6 source (dfb474de) it ran on the loop thread."""

    def __init__(self, monkeypatch):
        from sportsassets import bettor_funded_model as FM
        self.runs: list = []
        real = FM._records_sha

        def spy(*a, **k):
            t = threading.current_thread()
            self.runs.append((t.name, t.ident))
            return real(*a, **k)
        monkeypatch.setattr(FM, "_records_sha", spy)


async def _check(conn, model: dict, spy: _Spy, loop_tid: int) -> dict:
    """One verification of `model` and one research-model read, each
    re-hashing on the lane (asserted here, every call)."""
    from sportsassets import bettor_funded_model as FM
    from sportsassets.agents import paper_derek as PD
    before = len(spy.runs)
    ver = await FM.verify_provenance(conn, model)
    got = await PD.research_model(conn, at=time.time() + 1.0)
    return {"verify": ver, "research_model": got,
            "rehashes": spy.runs[before:], "loop_tid": loop_tid}


def _scenario(monkeypatch, change, *, restore=None) -> dict:
    """Build, register, verify (success), apply `change`, verify again
    (expected refusal), optionally `restore` and verify a third time; all
    in one rolled-back transaction. `change(conn, made)` returns the
    observation id it changed (or None)."""
    import asyncpg

    from sportsassets.agents import paper_derek as PD
    spy = _Spy(monkeypatch)
    PD._CONTEXT_CACHE.clear()

    async def go():
        loop_tid = threading.get_ident()
        conn = await asyncpg.connect(_dsn())
        tr = conn.transaction()
        await tr.start()
        try:
            await _isolate(conn)
            made = await _ledger(conn)
            await _registered(conn, model_id=SYN + "model")
            first = await PD.research_model(conn, at=time.time() + 1.0)
            assert first["ok"] is True, first
            assert first["model_id"] == SYN + "model"
            model = first["model"]
            out = {"made": made, "slim": "records" not in
                   model["training_provenance"],
                   "before": await _check(conn, model, spy, loop_tid)}
            out["changed"] = await change(conn, made)
            out["after"] = await _check(conn, model, spy, loop_tid)
            if restore is not None:
                await restore(conn, made)
                out["restored"] = await _check(conn, model, spy, loop_tid)
            return out
        finally:
            await tr.rollback()
            await conn.close()
    try:
        return asyncio.run(go())
    finally:
        PD._CONTEXT_CACHE.clear()


def _verified(chk: dict) -> None:
    ver, got = chk["verify"], chk["research_model"]
    assert ver["ok"] is True, ver
    assert len(ver["records"]) == N_ROWS
    assert got["ok"] is True and got["provenance_verified"] is True, got
    _rehashed_on_the_lane(chk)


def _refused(chk: dict, *, changed: str | None) -> None:
    from sportsassets import bettor_funded_model as FM
    from sportsassets.agents import paper_derek as PD
    ver, got = chk["verify"], chk["research_model"]
    assert ver["ok"] is False, ver
    assert ver["refusal"] == FM.R_TRAINING_RECORDS_DO_NOT_REPRODUCE, ver
    assert "records" not in ver and "lab" not in ver, \
        "a refusal must not carry a training set a caller could use"
    if changed is not None:
        # named from the registry's own stored records (the slim read left
        # them there; the mismatch path reads them back)
        assert ver["changed_records"] == [changed], ver
    assert got["ok"] is False, got
    assert got["refusal"] == PD.R_MODEL_UNVERIFIED, got
    assert got["why"] == FM.R_TRAINING_RECORDS_DO_NOT_REPRODUCE, got
    assert got["provenance_verified"] is False
    if changed is not None:
        _rehashed_on_the_lane(chk)


def _rehashed_on_the_lane(chk: dict) -> None:
    runs = chk["rehashes"]
    # one re-hash per check (verify_provenance + research_model): nothing
    # remembered a previous answer
    assert len(runs) == 2, runs
    assert all(name.startswith(LANE) for name, _ in runs), runs
    assert all(tid != chk["loop_tid"] for _, tid in runs), \
        "the training records were re-hashed on the event loop thread"


# ═════════════════════════════════════════════════════════════════════
# 1 · A CORRECTED TRAINING LABEL
# ═════════════════════════════════════════════════════════════════════

def test_a_corrected_label_refuses_after_an_earlier_success(monkeypatch):
    async def correct(conn, made):
        vid, oid, _won = made[7]
        await conn.execute("UPDATE external_valuations SET outcome = "
                           "1 - outcome WHERE id = $1", vid)
        return oid

    got = _scenario(monkeypatch, correct)
    _verified(got["before"])
    _refused(got["after"], changed=got["changed"])
    # the model a PAPER decision read carried no stored records (RC6 slim
    # registry read), and the refusal above still named the decision
    assert got["slim"] is True


def test_the_verdict_follows_the_ledger_both_ways_on_one_model_object(
        monkeypatch):
    """Success, a corrected label (refused), the correction reversed
    (verified again) -- the same model dict each time: the answer is the
    ledger's at the instant of the check, recomputed every time."""
    async def correct(conn, made):
        vid, oid, _won = made[11]
        await conn.execute("UPDATE external_valuations SET outcome = "
                           "1 - outcome WHERE id = $1", vid)
        return oid

    async def reverse(conn, made):
        vid, _oid, _won = made[11]
        await conn.execute("UPDATE external_valuations SET outcome = "
                           "1 - outcome WHERE id = $1", vid)

    got = _scenario(monkeypatch, correct, restore=reverse)
    _verified(got["before"])
    _refused(got["after"], changed=got["changed"])
    _verified(got["restored"])


# ═════════════════════════════════════════════════════════════════════
# 2 · A MISSING DECISION
# ═════════════════════════════════════════════════════════════════════

def test_a_decision_whose_settlement_turned_void_refuses(monkeypatch):
    """The settlement becomes a CONFIRMED void: the decision is no longer a
    label, so one named training decision is missing."""
    async def void(conn, made):
        vid, _oid, _won = made[3]
        await conn.execute(
            "UPDATE external_valuations SET outcome_known = FALSE, "
            " outcome = NULL, outcome_at = NULL, "
            " outcome_basis = 'CONFIRMED_VOID' WHERE id = $1", vid)
        return None

    got = _scenario(monkeypatch, void)
    _verified(got["before"])
    _refused(got["after"], changed=None)
    assert got["after"]["verify"]["why"] == \
        "%d decision(s) named, %d still labelled" % (N_ROWS, N_ROWS - 1)


def test_a_decision_record_that_is_gone_refuses(monkeypatch):
    """The observation row itself is gone (the append-only trigger
    bypassed: a corruption or a repair)."""
    async def delete(conn, made):
        _vid, oid, _won = made[19]
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute("DELETE FROM derek_research_observations "
                           " WHERE observation_id = $1", oid)
        await conn.execute("SET LOCAL session_replication_role = origin")
        return None

    got = _scenario(monkeypatch, delete)
    _verified(got["before"])
    _refused(got["after"], changed=None)
    assert got["after"]["verify"]["why"] == \
        "%d decision(s) named, %d still labelled" % (N_ROWS, N_ROWS - 1)


# ═════════════════════════════════════════════════════════════════════
# 3 · A CHANGED FEATURE VECTOR
# ═════════════════════════════════════════════════════════════════════

def test_a_changed_feature_vector_refuses_after_an_earlier_success(
        monkeypatch):
    """The decision-time vector and its identity rewritten together (the
    append-only trigger bypassed)."""
    from sportsassets import bettor_funded_model as FM

    async def rewrite(conn, made):
        vid, oid, _won = made[23]
        feats = json.loads(await conn.fetchval(
            "SELECT features::text FROM derek_research_observations "
            " WHERE valuation_id = $1", vid))
        feats["acquisition_price"] = round(
            1.0 - float(feats["acquisition_price"]), 9)
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "UPDATE derek_research_observations SET features = $2::jsonb, "
            " feature_sha = $3 WHERE valuation_id = $1",
            vid, json.dumps(feats), FM.feature_sha(feats))
        await conn.execute("SET LOCAL session_replication_role = origin")
        return oid

    got = _scenario(monkeypatch, rewrite)
    _verified(got["before"])
    _refused(got["after"], changed=got["changed"])


def test_a_vector_that_gained_a_feature_refuses(monkeypatch):
    """A key added to the vector (its schema changed) with its identity
    rewritten to match."""
    from sportsassets import bettor_funded_model as FM

    async def widen(conn, made):
        vid, oid, _won = made[29]
        feats = json.loads(await conn.fetchval(
            "SELECT features::text FROM derek_research_observations "
            " WHERE valuation_id = $1", vid))
        feats["late_addition"] = 1.0
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "UPDATE derek_research_observations SET features = $2::jsonb, "
            " feature_sha = $3 WHERE valuation_id = $1",
            vid, json.dumps(feats), FM.feature_sha(feats))
        await conn.execute("SET LOCAL session_replication_role = origin")
        return oid

    got = _scenario(monkeypatch, widen)
    _verified(got["before"])
    _refused(got["after"], changed=got["changed"])


# ═════════════════════════════════════════════════════════════════════
# 4 · A CHANGED SETTLEMENT
# ═════════════════════════════════════════════════════════════════════

def test_a_settlement_read_on_another_basis_refuses(monkeypatch):
    """Same label, a different (still accepted) settlement basis: the
    label's own version changed, so the training record did."""
    async def rebase(conn, made):
        vid, oid, _won = made[31]
        await conn.execute("UPDATE external_valuations SET outcome_basis = "
                           "'VENUE_REPORTED_OUTCOME' WHERE id = $1", vid)
        return oid

    got = _scenario(monkeypatch, rebase)
    _verified(got["before"])
    _refused(got["after"], changed=got["changed"])


def test_a_settlement_re_read_at_another_instant_refuses(monkeypatch):
    """Same label, same basis, the settlement reading re-booked one second
    later: when the label became known is part of the record."""
    async def reread(conn, made):
        vid, oid, _won = made[37]
        await conn.execute("UPDATE external_valuations SET outcome_at = "
                           "outcome_at + interval '1 second' WHERE id = $1",
                           vid)
        return oid

    got = _scenario(monkeypatch, reread)
    _verified(got["before"])
    _refused(got["after"], changed=got["changed"])

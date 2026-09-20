"""THE PROSPECTIVE LOOP, pinned where it could quietly become retrospective.

Owner directive 2026-09-19 23:0xZ: "Start X1 prospective experimental
collection using this bridge as soon as the frozen eligibility
requirements are satisfied."

THE FAILURES THESE PREVENT:

  A DECISION WALKED AGAINST A BOOK IT ALREADY SAW. The bridge answers
  minutes after the request, so seal and execution happen on different
  ticks. If the arrival query ever returned a book observed BEFORE the
  seal, the reconstruction would fill at a price the decision knew and
  every result in the lane would be fiction that looks like a
  measurement.

  THE TICK RATE COUNTED AS TRADES. The collector writes one
  opportunity per market per 300s bucket; this loop runs every 60s.
  Without one-seal-per-experiment-per-opportunity the same book would
  be decided five times.

  A CONTROL ON A DIFFERENT POPULATION. X1 and X1C are sealed in the
  same pass against the same population id, or the comparison is
  between two tapes.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import shadow_experiment_registry as reg
from sportsassets import shadow_experiment_versions as ver
from sportsassets import shadow_experimental_engine as eng
from sportsassets import shadow_experimental_store as xstore
from sportsassets import shadow_experiments as xp
from sportsassets import shadow_identity as ident
from sportsassets import shadow_l2 as l2
from sportsassets.workers import shadow_experimental as worker

NOW = datetime(2026, 9, 19, 23, 40, tzinfo=timezone.utc)
SYMBOL = "astatc-mls-sje-laf-2026-09-19-sh-ftts-laf"

INSTRUMENT = {
    "symbol": SYMBOL,
    "productId": "astatc-mls-sje-laf-2026-09-19-sh-ftts",
    "priceScale": "100", "fractionalQtyScale": "100",
    "expirationDate": "2026-09-20T04:00:00Z",
    "metadata": {"event_id": "mls-sje-laf-2026-09-19",
                 "outcome_strike": "laf",
                 "market_sport_type": "soccer_game_second_half_ftts",
                 "event_start_time": "2026-09-19T23:30:00Z"},
    "eventAttributes": {"eventId": "astatc-mls-sje-laf-2026-09-19-sh-ftts",
                        "eventOutcome": "EVENT_OUTCOME_MUTUALLY_EXCLUSIVE",
                        "payoutValue": "100",
                        "question": "Will LAFC score first in the 2H?"},
}
RETAIL = {"identifier": "0xabc", "market_slug": SYMBOL,
          "event_slug": "mls-sje-laf-2026-09-19", "side_norm": "yes",
          "kind": "ftts", "line": None}


# ── mechanism versus policy, kept apart ──────────────────────────────
#
# X1 V1 and its control are BLOCKED from creating new positions (owner
# 2026-09-20 §1, EXPERIMENT_VERSION_EXIT_SEMANTICS_INCOMPLETE). That is
# a policy fact about today, and it is pinned by its own tests below.
#
# The tests that exercise the WALK, the SEAL and the RE-ENTRY BOUNDARY
# are about mechanism, and they must keep working when the policy moves
# -- otherwise the day the successor is armed, the machinery it runs on
# has no coverage at all. So they lift the version blocker explicitly
# and say so, rather than being deleted or quietly left failing.


@pytest.fixture
def version_unblocked(monkeypatch):
    """Run the position-creation machinery under a COMPLETE contract.

    Clearing the blocker list is not enough, and the reason is worth
    stating: position_creation falls back to reading the declaration
    itself, and X1 V1's exit contract is genuinely incomplete -- so it
    is refused by the backstop even with the list empty. That backstop
    is the point, so the fixture does not disable it. It supplies the
    successor's contract instead, armed, which is what these tests will
    be covering once the successor is reviewed.
    """
    armed = dict(reg.X1V2, readiness=xp.ARMED)
    monkeypatch.setattr(ver, "POSITION_CREATION_BLOCKED", {})
    monkeypatch.setattr(worker, "_declaration_of", lambda _id: armed)
    return armed


# ── a fake pool that speaks just enough asyncpg ──────────────────────


class FakePool:
    """Records every statement and answers the worker's three reads."""

    def __init__(self, *, opportunities=(), evidence=(), instrument=None,
                 retail=None):
        self.opportunities = list(opportunities)
        self.evidence = list(evidence)
        self.instrument = instrument
        self.retail = retail
        self.seals: dict = {}
        self.decisions: dict = {}
        self.positions: dict = {}
        self.populations: dict = {}
        self.requests: list = []
        self.experiments: list = []
        self.markouts: list = []
        self.position_events: list = []

    # -- reads -------------------------------------------------------
    async def fetch(self, sql, *args):
        if "FROM bettor_experimental_observations" in sql \
                and "DISTINCT ON (symbol)" not in sql:
            return [r for r in self.opportunities
                    if r["microstructure"].get("featureSourceVersion")
                    == args[1]
                    and r["microstructure"].get("bboBinding") == args[2]]
        if "DISTINCT ON (symbol)" in sql:
            return []
        # NO PRE-BOUND ROW. These fixtures deliberately exercise the
        # FALLBACK binding path -- the one that resolves from whatever
        # instrument record the bridge wrote -- so that both paths stay
        # covered. The pre-bound path has its own file.
        if "FROM bettor_identity_bindings" in sql:
            return []
        if "FROM bettor_l2_evidence" in sql and "DISTINCT ON" in sql:
            if self.instrument is None:
                return []
            return [{"instrument_id": SYMBOL,
                     "instrument_record": json.dumps(self.instrument),
                     "price_scale": 100, "quantity_scale": 100,
                     "received_timestamp": NOW}]
        if "FROM us_premap" in sql:
            return [] if self.retail is None else [dict(self.retail)]
        if "FROM bettor_experimental_seals" in sql and "ANY(" in sql:
            return [{"experiment_id": s["experiment_id"],
                     "experimental_observation_id":
                         s["experimental_observation_id"]}
                    for s in self.seals.values()
                    if s["experimental_observation_id"] in args[0]]
        if "FROM bettor_experimental_seals" in sql:
            return [s for s in self.seals.values()
                    if s["status"] == "SEALED"]
        # §11: THE RE-ENTRY GUARD'S OWN READ, answered from the positions
        # this fake has actually opened. Modelled rather than stubbed to
        # [] -- a stub would let the guard pass every check and the
        # enforcement would look wired while refusing nothing.
        if "FROM bettor_experimental_positions" in sql \
                and "max(opened_at)" in sql:
            experiment, markets = args[0], set(args[1])
            last: dict = {}
            for p in self.positions.values():
                if p[2] != experiment or p[3] not in markets:
                    continue
                prev = last.get(p[3])
                if prev is None or p[6] > prev:
                    last[p[3]] = p[6]
            return [{"experiment_id": experiment, "market_id": m,
                     "last_opened_at": at} for m, at in last.items()]
        raise AssertionError("unexpected fetch: %.60s" % sql)

    async def fetchrow(self, sql, *args):
        if "FROM bettor_l2_evidence" in sql:
            after = args[1]
            later = [e for e in self.evidence
                     if e["received_timestamp"] > after]
            return min(later, key=lambda e: e["received_timestamp"]) \
                if later else None
        if sql.strip().startswith("INSERT INTO bettor_experimental_seals"):
            if args[1] in self.seals:
                return None
            self.seals[args[1]] = {
                "seal_sha": args[0], "experimental_decision_id": args[1],
                "experiment_id": args[2], "bettor_opportunity_id": args[5],
                "symbol": args[6], "action": args[8], "sealed_at": args[9],
                "seal": args[10], "experimental_observation_id": args[11],
                "status": "SEALED"}
            return {"experimental_decision_id": args[1]}
        if sql.strip().startswith("INSERT INTO bettor_experimental_decisions"):
            if args[0] in self.decisions:
                return None
            # BY NAME, NEVER BY POSITION. A hand-counted index is how a
            # vwap ends up in a slippage column.
            self.decisions[args[0]] = dict(
                zip(xstore.DECISION_COLUMNS, args))
            return {"experimental_decision_id": args[0]}
        if sql.strip().startswith("INSERT INTO bettor_experiments"):
            self.experiments.append(args[0])
            return {"experiment_id": args[0]}
        if sql.strip().startswith("INSERT INTO bettor_experimental_markouts"):
            self.markouts.append(args)
            return {"markout_id": args[0]}
        if sql.strip().startswith(
                "INSERT INTO bettor_experimental_position_events"):
            # ON CONFLICT (position_id, event_type, sequence_no) DO
            # NOTHING, modelled -- the ledger is append-only and a
            # re-run must not double an event.
            key = (args[1], args[4], args[6])
            if key in {(e[1], e[4], e[6]) for e in self.position_events}:
                return None
            self.position_events.append(args)
            return {"position_event_id": args[0]}
        raise AssertionError("unexpected fetchrow: %.60s" % sql)

    async def execute(self, sql, *args):
        head = sql.strip().split("\n")[0]
        if "INSERT INTO bettor_eligible_populations" in head:
            self.populations[args[0]] = args
        elif "INSERT INTO bettor_l2_requests" in head:
            # ON CONFLICT DO NOTHING, modelled. The real statement has
            # it, and without it here the fake would disagree with the
            # database about how many venue requests this lane makes.
            if args[0] not in {r[0] for r in self.requests}:
                self.requests.append(args)
        elif "INSERT INTO bettor_experimental_positions" in head:
            self.positions[args[0]] = args
        elif "UPDATE bettor_experimental_seals" in head:
            row = self.seals.get(args[0])
            if row and "SET status" in sql:
                row["status"] = args[1]
        else:
            raise AssertionError("unexpected execute: %.60s" % sql)


def micro(mid, **kw):
    bid, ask = round(mid - 0.005, 4), round(mid + 0.005, 4)
    base = {"status": "MEASURED", "readable": True, "bid": bid, "ask": ask,
            "mid": mid, "spread": 0.01, "spreadRelative": 0.01 / mid,
            "bboBinding": l2.BIND_YES,
            "featureSourceVersion": l2.FEATURE_SOURCE_VERSION}
    base.update(kw)
    return base


def opp(i, mid, **kw):
    return {"experimental_observation_id": "xobs%d" % i,
            "bettor_opportunity_id": None, "symbol": SYMBOL,
            "outcome_leg": "yes", "event_id": "ev1",
            "observed_at": NOW - timedelta(seconds=60 * (5 - i)),
            "evidence_source": "PMUS_BBO", "microstructure": micro(mid, **kw)}


RISING = [opp(1, 0.36), opp(2, 0.38), opp(3, 0.40)]
FLAT = [opp(1, 0.400), opp(2, 0.4005), opp(3, 0.4002)]


def ev_row(at, *, offers=None):
    return {"l2_evidence_id": "l2ev_%s" % at.strftime("%H%M%S"),
            "l2_request_id": "l2rq_1", "request_id": "venue-req",
            "instrument_id": SYMBOL,
            "source_timestamp": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "received_timestamp": at, "l2_book_sha": "bk16",
            "bids": json.dumps([{"px": "40", "qty": "500"}]),
            "offers": json.dumps(offers if offers is not None
                                 else [{"px": "41", "qty": "30000"},
                                       {"px": "42", "qty": "20000"}]),
            "price_scale": 100, "quantity_scale": 100,
            "venue_request_ms": 103.5, "bridge_latency_ms": 612000.0,
            "latency_regime": "GITHUB_BRIDGE",
            "venue_state": l2.STATE_OPEN}


# ── §2/§3: only corrected, YES-bound rows are eligible ───────────────


@pytest.mark.asyncio
async def test_a_duplicated_leg_row_is_never_in_the_population():
    """"Do not let X1 train/evaluate from a price series whose leg
    identity is incorrect." The filter is in the query."""
    stale = [dict(o, microstructure=micro(
        0.4, featureSourceVersion=l2.FEATURE_SOURCE_VERSION_DUPLICATED))
        for o in RISING]
    pool = FakePool(opportunities=stale, instrument=INSTRUMENT,
                    retail=RETAIL)
    out = await worker.seal_population(pool, now=NOW)
    assert out["status"] == "no_eligible_population"
    assert not pool.seals


@pytest.mark.asyncio
async def test_a_no_leg_book_is_never_in_the_population():
    rows = [dict(o, outcome_leg="no",
                 microstructure=micro(0.4,
                                      bboBinding=l2.BIND_NOT_IDENTIFIED))
            for o in RISING]
    pool = FakePool(opportunities=rows, instrument=INSTRUMENT, retail=RETAIL)
    out = await worker.seal_population(pool, now=NOW)
    assert out["status"] == "no_eligible_population"


@pytest.mark.asyncio
async def test_a_market_with_too_few_samples_is_not_decided():
    pool = FakePool(opportunities=RISING[:2], instrument=INSTRUMENT,
                    retail=RETAIL)
    out = await worker.seal_population(pool, now=NOW)
    assert out["tooFewSamples"] == 1
    assert not pool.seals


# ── §7: the seal, written before any arrival evidence exists ─────────


@pytest.mark.asyncio
async def test_the_seal_is_written_and_the_decision_is_not():
    """A P&L-bearing decision may not exist until its arrival book has
    been observed. Between the two ticks there is a SEAL and nothing
    else."""
    pool = FakePool(opportunities=RISING, instrument=INSTRUMENT,
                    retail=RETAIL)
    out = await worker.seal_population(pool, now=NOW)
    assert out["sealed"] == 2                      # X1 and its control
    # ONE BOOK REQUEST, SHARED. X1 and its control must be scored
    # against the SAME arrival; two requests would fetch two books
    # minutes apart and the comparison would be of latency, not models.
    assert out["requested"] == 1
    assert len(pool.requests) == 1
    assert not pool.decisions
    assert all(s["status"] == "SEALED" for s in pool.seals.values())


@pytest.mark.asyncio
async def test_x1_and_its_control_share_one_population_id():
    pool = FakePool(opportunities=RISING, instrument=INSTRUMENT,
                    retail=RETAIL)
    await worker.seal_population(pool, now=NOW)
    ids = {json.loads(s["seal"])["eligiblePopulationId"]
           for s in pool.seals.values()}
    assert len(ids) == 1 and ids != {None}
    assert set(pool.populations) == ids


@pytest.mark.asyncio
async def test_the_same_opportunity_is_never_sealed_twice():
    """The collector's bucket is 300s and this loop runs every 60s."""
    pool = FakePool(opportunities=RISING, instrument=INSTRUMENT,
                    retail=RETAIL)
    await worker.seal_population(pool, now=NOW)
    before = len(pool.seals)
    out = await worker.seal_population(pool, now=NOW + timedelta(seconds=60))
    assert out["status"] == "already_sealed"
    assert len(pool.seals) == before


@pytest.mark.asyncio
async def test_a_no_trade_is_recorded_at_once_and_asks_for_no_book():
    """"Do not convert existing NO_TRADE decisions into experimental
    trades" -- and a refusal needs no arrival."""
    pool = FakePool(opportunities=FLAT, instrument=INSTRUMENT,
                    retail=RETAIL)
    out = await worker.seal_population(pool, now=NOW)
    assert out["noTrade"] == 1                     # X1 flat; X1C is long
    statuses = {a["execution_status"] for a in pool.decisions.values()}
    assert eng.NO_EXECUTION_INTENDED in statuses
    assert len(pool.requests) == 1                 # the control's only


# ── §7: the arrival must have been observed AFTER the decision ───────


@pytest.mark.asyncio
async def test_a_book_observed_before_the_seal_is_not_an_arrival():
    """THE MOST IMPORTANT TEST IN THIS FILE. A reconstruction against a
    book the decision already saw is lookahead wearing a measurement's
    clothes."""
    pool = FakePool(opportunities=RISING, instrument=INSTRUMENT,
                    retail=RETAIL,
                    evidence=[ev_row(NOW - timedelta(minutes=5))])
    await worker.seal_population(pool, now=NOW)
    out = await worker.drain_seals(pool, now=NOW + timedelta(seconds=60))
    assert out["waiting"] == 2 and out["executed"] == 0
    assert not pool.decisions


@pytest.mark.asyncio
async def test_the_first_book_after_the_seal_is_the_arrival():
    later = NOW + timedelta(minutes=6)
    pool = FakePool(opportunities=RISING, instrument=INSTRUMENT,
                    retail=RETAIL,
                    evidence=[ev_row(NOW - timedelta(minutes=5)),
                              ev_row(later),
                              ev_row(later + timedelta(minutes=10))])
    await worker.seal_population(pool, now=NOW)
    out = await worker.drain_seals(pool, now=later + timedelta(seconds=30))
    assert out["executed"] == 2
    used = {a["l2_evidence_id"] for a in pool.decisions.values()}     # l2_evidence_id
    assert used == {"l2ev_%s" % later.strftime("%H%M%S")}


@pytest.mark.asyncio
async def test_the_walk_fills_what_the_book_had_and_opens_a_position(version_unblocked):
    later = NOW + timedelta(minutes=6)
    pool = FakePool(opportunities=RISING, instrument=INSTRUMENT,
                    retail=RETAIL, evidence=[ev_row(later)])
    await worker.seal_population(pool, now=NOW)
    out = await worker.drain_seals(pool, now=later + timedelta(seconds=30))
    assert out["filled"] == 2
    assert len(pool.positions) == 2
    executed = {round(a["executed_notional_usd"], 2)
                for a in pool.decisions.values()}
    assert executed == {207.0}                    # 500 qty across 0.41/0.42
    unfilled = {round(a["unfilled_notional_usd"], 2)
                for a in pool.decisions.values()}
    assert unfilled == {793.0}
    regimes = {a["latency_regime"] for a in pool.decisions.values()}
    assert regimes == {"GITHUB_BRIDGE"}
    # "Do not manufacture latency" -- the bridge's measured figure.
    assert {a["observed_arrival_latency_ms"]
            for a in pool.decisions.values()} == {612000.0}


@pytest.mark.asyncio
async def test_a_seal_whose_book_never_arrives_is_not_identified():
    """§4: "If no valid arrival L2 exists: SHADOW_EXECUTION =
    NOT_IDENTIFIED and no P&L-bearing trade is created." """
    pool = FakePool(opportunities=RISING, instrument=INSTRUMENT,
                    retail=RETAIL)
    await worker.seal_population(pool, now=NOW)
    out = await worker.drain_seals(pool, now=NOW + timedelta(minutes=30))
    assert out["expired"] == 2 and out["executed"] == 0
    for args in pool.decisions.values():
        assert args["execution_status"] == eng.NOT_IDENTIFIED
        assert args["executed_notional_usd"] is None
        assert args["unfilled_notional_usd"] is None   # NOT $0.00
        assert args["position_id"] is None                        # no position
    assert not pool.positions


@pytest.mark.asyncio
async def test_an_empty_offer_side_is_not_identified_rather_than_unfilled():
    later = NOW + timedelta(minutes=6)
    pool = FakePool(opportunities=RISING, instrument=INSTRUMENT,
                    retail=RETAIL, evidence=[ev_row(later, offers=[])])
    await worker.seal_population(pool, now=NOW)
    await worker.drain_seals(pool, now=later + timedelta(seconds=30))
    for args in pool.decisions.values():
        assert args["execution_status"] == eng.NOT_IDENTIFIED
        assert args["executed_notional_usd"] is None


@pytest.mark.asyncio
async def test_a_seal_edited_in_the_database_is_never_executed():
    later = NOW + timedelta(minutes=6)
    pool = FakePool(opportunities=RISING, instrument=INSTRUMENT,
                    retail=RETAIL, evidence=[ev_row(later)])
    await worker.seal_population(pool, now=NOW)
    for row in pool.seals.values():
        body = json.loads(row["seal"])
        body["intendedNotionalUsd"] = 50000.0
        row["seal"] = json.dumps(body)
    out = await worker.drain_seals(pool, now=later + timedelta(seconds=30))
    assert out["refused"] == 2 and not pool.decisions


# ── the identity gate, at T0 and not re-derived at arrival ───────────


@pytest.mark.asyncio
async def test_a_market_with_no_institutional_record_is_not_executed():
    later = NOW + timedelta(minutes=6)
    pool = FakePool(opportunities=RISING, instrument=None, retail=RETAIL,
                    evidence=[ev_row(later)])
    out = await worker.seal_population(pool, now=NOW)
    assert out["notIdentified"] == 1
    await worker.drain_seals(pool, now=later + timedelta(seconds=30))
    for args in pool.decisions.values():
        assert args["execution_status"] == eng.BLOCKED_IDENTITY
        assert args["executed_notional_usd"] is None
        # THE ACTION SURVIVES THE BLOCK.
        assert args["action"] in (eng.BUY_YES, eng.NO_TRADE)


def test_the_binding_used_at_arrival_is_the_one_sealed_at_t0():
    sealed = {"identityBindingStatus": ident.EXACT_ONE_TO_ONE,
              "identityBindingSha": "abc",
              "institutionalInstrumentId": SYMBOL, "outcomeLeg": "yes"}
    assert worker.binding_at_t0(sealed)["executionEligible"] is True
    basket = dict(sealed,
                  identityBindingStatus=ident.EXACT_ONE_TO_COMPLEMENT_BASKET)
    # Exact, but with no walkable basket §4 still refuses it.
    with pytest.raises(ident.IdentityRefusal):
        ident.assert_execution_eligible(
            worker.binding_at_t0(basket),
            basket_walkable=worker.binding_at_t0(basket)["basketWalkable"])


# ── the eligibility rule is written onto the population ──────────────


@pytest.mark.asyncio
async def test_the_population_records_the_rule_that_selected_it():
    pool = FakePool(opportunities=RISING, instrument=INSTRUMENT,
                    retail=RETAIL)
    await worker.seal_population(pool, now=NOW)
    args = next(iter(pool.populations.values()))
    assert args[2] == l2.FEATURE_SOURCE_VERSION
    assert args[4] == xstore.ELIGIBILITY_RULE
    assert args[5] == xstore.ELIGIBILITY_RULE_SHA


def test_only_armed_experiments_can_seal():
    assert {e["experimentId"] for e in reg.armed()} == {
        "X1_SHORT_HORIZON_DIRECTION", "X1C_NULL_CONTROL"}


# ── a deployment whose migration has not run ─────────────────────────


def _constraint_rows(admits=True):
    """The CHECK definitions the readiness gate reads, as pg prints
    them. §11's refusal status lives in one of these, not in a
    column."""
    return [{"table_name": table, "constraint_name": name,
             "definition": ("CHECK (execution_status = ANY (ARRAY['EXECUTED'"
                            "::text%s]))"
                            % (", '" + wanted + "'::text" if admits else ""))}
            for table, name, wanted in xstore.REQUIRED_CONSTRAINT_TEXT]



@pytest.mark.asyncio
async def test_a_missing_table_is_named_rather_than_crashed_on():
    """The other shadow lanes cost a heartbeat naming the blocker when
    their migration has not run; this one does too. A crash loop tells
    nobody which table is missing."""
    class Catalog:
        def __init__(self, have):
            self.have = have

        async def fetch(self, sql, *args):
            if "information_schema.tables" in sql:
                return [{"table_name": t} for t in args[0] if t in self.have]
            if "pg_constraint" in sql:
                return _constraint_rows()
            return [{"table_name": t, "column_name": c}
                    for t, c in xstore.REQUIRED_COLUMNS]

    full = await xstore.store_ready(Catalog(set(xstore.REQUIRED_TABLES)))
    assert full["storeReady"] is True and not full["problems"]

    partial = await xstore.store_ready(
        Catalog(set(xstore.REQUIRED_TABLES) - {"bettor_experimental_seals"}))
    assert partial["storeReady"] is False
    assert "table bettor_experimental_seals is absent" in partial["problems"]


@pytest.mark.asyncio
async def test_a_table_without_its_later_columns_is_not_ready():
    """An ALTER that did not run leaves a table that exists and an
    insert that cannot."""
    class Catalog:
        async def fetch(self, sql, *args):
            if "information_schema.tables" in sql:
                return [{"table_name": t} for t in xstore.REQUIRED_TABLES]
            if "pg_constraint" in sql:
                return _constraint_rows()
            return [{"table_name": t, "column_name": c}
                    for t, c in xstore.REQUIRED_COLUMNS
                    if c != "walked_book_sha"]

    out = await xstore.store_ready(Catalog())
    assert out["storeReady"] is False
    assert out["problems"] == [
        "bettor_experimental_decisions.walked_book_sha is absent"]


# ── §12: the bridge only fetches what is asked for ───────────────────


@pytest.mark.asyncio
async def test_an_open_horizon_queues_a_markout_book_request():
    """WITHOUT THIS THERE ARE NO MARKOUTS AT ALL. The bridge fetches
    only what a request names, so the only institutional book that
    would ever exist for a symbol is its arrival -- and a markout
    measured against the book the position was opened on is the entry
    price wearing a later label."""
    class Pool(FakePool):
        async def fetch(self, sql, *args):
            if "JOIN bettor_experimental_positions" in sql:
                return [{"experimental_decision_id": "xdec_1",
                         "market_id": SYMBOL,
                         "decision_timestamp": NOW,
                         "position_id": "xpos_1",
                         "entry_qty": 500.0, "entry_vwap": 0.414}]
            if "FROM bettor_experimental_markouts" in sql:
                return []
            return await super().fetch(sql, *args)

        async def fetchrow(self, sql, *args):
            if "FROM bettor_l2_evidence" in sql:
                return None
            return await super().fetchrow(sql, *args)

    pool = Pool()
    out = await worker.take_markouts(pool, now=NOW + timedelta(seconds=400))
    assert out["requested"] == 1
    assert pool.requests[0][5] == "MARKOUT"


@pytest.mark.asyncio
async def test_one_request_per_symbol_per_bridge_cycle_not_per_tick():
    """The id is derived from (symbol, purpose, instant), so the
    instant is bucketed to the bridge's own cadence -- otherwise sixty
    ticks an hour would write sixty rows one fetch would answer."""
    a = worker._bridge_bucket(NOW)
    b = worker._bridge_bucket(NOW + timedelta(seconds=59))
    c = worker._bridge_bucket(NOW + timedelta(seconds=900))
    assert a == b and a != c
    assert int(a.timestamp()) % worker.BRIDGE_CADENCE_S == 0


def test_a_position_past_every_horizon_stops_being_asked_about():
    """A book half an hour after a 300-second target is not that
    markout under any tolerance; asking forever would load the venue
    to produce rows nothing can use."""
    subject = {"experimentalDecisionId": "xdec_1", "symbol": SYMBOL,
               "decisionTimestamp": NOW}
    assert worker._markout_window_open(
        subject, NOW + timedelta(seconds=400), set()) is True
    assert worker._markout_window_open(
        subject, NOW + timedelta(hours=2), set()) is False
    # and a position whose every horizon is already written is done
    done = {("xdec_1", h) for h, _s in
            __import__("sportsassets.shadow_experimental_markouts",
                       fromlist=["x"]).HORIZONS}
    assert worker._markout_window_open(
        subject, NOW + timedelta(seconds=400), done) is False


# ── the lane's own sampler ───────────────────────────────────────────
#
# WHY IT EXISTS, from production at 00:01Z: the decision-grade
# collector's corrected rows read YES_CONTRACT_BOOK n=17 symbols=17 --
# one sample per market per hour. X1's frozen rule needs three
# successive captured samples, so on that feed it could never fire.


def test_a_readable_quote_becomes_a_yes_bound_sample():
    obs = worker.observation_of(
        {"symbol": SYMBOL, "outcomeLeg": "yes"},
        {"bid": 0.40, "ask": 0.42, "state": "open"}, NOW)
    assert obs["readable"] is True
    assert obs["microstructure"]["mid"] == pytest.approx(0.41)
    assert obs["microstructure"]["spreadRelative"] == pytest.approx(
        0.02 / 0.41)
    assert obs["microstructure"]["bboBinding"] == l2.BIND_YES
    assert obs["microstructure"]["featureSourceVersion"] == \
        l2.FEATURE_SOURCE_VERSION
    # DEPTH IS NOT ESTABLISHED FROM A BBO and is not pretended to be.
    assert obs["microstructure"]["depth"] == l2.NOT_IDENTIFIED


def test_an_unreadable_book_is_written_down_rather_than_dropped():
    """A hole in the series is indistinguishable from a market nobody
    looked at. The eligibility query filters on `readable`, not on the
    row's absence."""
    for quote, expect in (
            ({"bid": None, "ask": None, "state": None,
              "error": "Timeout"}, "QUOTE_READ_FAILED_Timeout"),
            ({"bid": None, "ask": None, "state": "closed"},
             "VENUE_MARKET_STATE_closed"),
            ({"bid": None, "ask": None, "state": None},
             "VENUE_RETURNED_NO_QUOTE")):
        obs = worker.observation_of({"symbol": SYMBOL, "outcomeLeg": "yes"},
                                    quote, NOW)
        assert obs["readable"] is False
        assert obs["whyUnreadable"] == expect
        assert obs["microstructure"]["bboBinding"] == l2.BIND_MARKET_LEVEL


def test_one_observation_per_market_per_tick_bucket():
    """A restart mid-tick must not write the same instant twice."""
    a = xstore.observation_id(SYMBOL, "yes", NOW, 60)
    b = xstore.observation_id(SYMBOL, "yes",
                              NOW + timedelta(seconds=30), 60)
    c = xstore.observation_id(SYMBOL, "yes",
                              NOW + timedelta(seconds=90), 60)
    assert a == b and a != c
    assert a != xstore.observation_id(SYMBOL, "no", NOW, 60)


def test_the_no_leg_is_never_sampled_as_a_yes_book():
    obs = worker.observation_of({"symbol": SYMBOL, "outcomeLeg": "no"},
                                {"bid": 0.40, "ask": 0.42}, NOW)
    assert obs["microstructure"]["bboBinding"] == l2.BIND_NOT_IDENTIFIED
    assert obs["microstructure"]["bid"] is None


def test_the_eligibility_rule_changed_and_its_hash_says_so():
    """The rule string is hashed onto every population, so populations
    drawn from the collector's feed and from this lane's own sampler
    are permanently distinguishable rather than silently merged."""
    assert "focus-set sampler" in xstore.ELIGIBILITY_RULE
    assert len(xstore.ELIGIBILITY_RULE_SHA) == 16
    assert xstore.ELIGIBILITY_RULE_SHA != "9b3d16e000000000"


@pytest.mark.asyncio
async def test_the_store_wait_re_checks_and_wakes_when_the_alter_lands():
    """THE API SERVICE RUNS THE MIGRATIONS; THIS WORKER IS A DIFFERENT
    SERVICE, and the two deploy in no guaranteed order. A wait loop
    that only heartbeated would leave the lane dark until someone
    restarted it by hand -- minutes after the ALTER it was waiting for
    had landed, and with nothing saying so."""
    src = (pathlib.Path(worker.__file__).read_text())
    body = src.split("async def run(")[1]
    assert "while not ready[\"storeReady\"]:" in body
    assert "await xstore.store_ready(pool)" in body.split(
        "while not ready[\"storeReady\"]:")[1]


# ── §10/§11: the re-entry clause, enforced in the live loop ──────────
#
# Owner directive 2026-09-20 §11: "X1 has zero re-entry violations...
# but the rule must become actual enforcement rather than accidental
# compliance caused by the ~65s tick cadence. Do not let polling
# cadence serve as the risk control."
#
# THESE DRIVE THE WORKER, not the guard. A guard that is correct in
# isolation and never reached from the loop is exactly the failure the
# directive names: the lane would keep passing because the sampler is
# slow, and would start violating the instant it got faster.


def _second_tick_opportunities(at):
    """The same market, sampled again in a later 60s bucket."""
    return [dict(o, experimental_observation_id="xobs_b%d" % i,
                 observed_at=at - timedelta(seconds=30 * (3 - i)))
            for i, o in enumerate(RISING, start=1)]


@pytest.mark.asyncio
async def test_a_second_entry_inside_the_frozen_horizon_is_refused(version_unblocked):
    """30s after the first entry, on the same market, in the loop."""
    first = NOW + timedelta(minutes=6)
    second = first + timedelta(seconds=30)
    pool = FakePool(opportunities=RISING, instrument=INSTRUMENT,
                    retail=RETAIL, evidence=[ev_row(first)])
    await worker.seal_population(pool, now=NOW)
    await worker.drain_seals(pool, now=first + timedelta(seconds=30))
    assert len(pool.positions) == 2                 # X1 and its control

    # THE SECOND TICK. A fresh observation on the same symbol, whose
    # arrival book lands 30s after the first entry -- inside the frozen
    # 60s horizon that both lanes declare.
    later_seal = first + timedelta(seconds=10)
    pool.opportunities = _second_tick_opportunities(later_seal)
    pool.evidence = [ev_row(second)]
    out = await worker.seal_population(pool, now=later_seal)
    assert out["sealed"] == 2
    await worker.drain_seals(pool, now=second + timedelta(seconds=30))

    # NO NEW POSITION, ON EITHER LANE. The frozen clause forbids the
    # entry; it does not delay it, so there is no retry either.
    assert len(pool.positions) == 2
    refused = [d for d in pool.decisions.values()
               if d["execution_status"] == eng.REFUSED_REENTRY]
    assert len(refused) == 2
    for d in refused:
        # §11's whole point: the refusal carries no position and no
        # economics, or it would be counted as a trade downstream.
        assert d["position_id"] is None
        assert d["executed_notional_usd"] is None
        assert d["filled_qty"] is None
        assert "horizon" in (d["why"] or "")
    # AND THE FIRST ENTRIES ARE UNTOUCHED.
    assert len(pool.position_events) == 2
    assert {e[4] for e in pool.position_events} == {"POSITION_OPENED"}


@pytest.mark.asyncio
async def test_an_entry_outside_the_frozen_horizon_is_permitted(version_unblocked):
    """The guard must refuse re-entry, not entry. 65s apart -- the
    cadence that has been doing this work by accident -- still opens."""
    first = NOW + timedelta(minutes=6)
    second = first + timedelta(seconds=65)
    pool = FakePool(opportunities=RISING, instrument=INSTRUMENT,
                    retail=RETAIL, evidence=[ev_row(first)])
    await worker.seal_population(pool, now=NOW)
    await worker.drain_seals(pool, now=first + timedelta(seconds=30))

    later_seal = first + timedelta(seconds=40)
    pool.opportunities = _second_tick_opportunities(later_seal)
    pool.evidence = [ev_row(second)]
    await worker.seal_population(pool, now=later_seal)
    await worker.drain_seals(pool, now=second + timedelta(seconds=30))

    assert len(pool.positions) == 4
    assert not [d for d in pool.decisions.values()
                if d["execution_status"] == eng.REFUSED_REENTRY]
    assert len(pool.position_events) == 4


@pytest.mark.asyncio
async def test_the_guard_reads_the_registry_not_a_hand_built_dict():
    """The declaration dict is camelCase. A guard that looked for
    `exit_rule` would find nothing, govern nothing, and refuse nothing
    -- while every hand-built-dict test still passed."""
    decl = worker._declaration_of("X1_SHORT_HORIZON_DIRECTION")
    assert decl is not None and decl["experimentId"] \
        == "X1_SHORT_HORIZON_DIRECTION"
    from sportsassets import shadow_reentry_guard as rg
    assert rg.governed(decl) is True
    assert rg.horizon_seconds(decl) == 60


@pytest.mark.asyncio
async def test_a_constraint_that_would_refuse_the_refusal_is_not_ready():
    """§11 (migration 086). If the CHECK has not been widened, the
    write that records a re-entry refusal fails -- at the exact moment
    the rule fires. A readiness gate that only looked at tables and
    columns would call that deployment ready."""
    class Catalog:
        async def fetch(self, sql, *args):
            if "information_schema.tables" in sql:
                return [{"table_name": t} for t in xstore.REQUIRED_TABLES]
            if "pg_constraint" in sql:
                return _constraint_rows(admits=False)
            return [{"table_name": t, "column_name": c}
                    for t, c in xstore.REQUIRED_COLUMNS]

    out = await xstore.store_ready(Catalog())
    assert out["storeReady"] is False
    assert out["problems"] == [
        "bettor_experimental_decisions.bettor_exp_execution_status "
        "does not admit REFUSED_REENTRY_INSIDE_HORIZON"]


@pytest.mark.asyncio
async def test_the_lifecycle_event_table_is_part_of_readiness():
    """§1 (migration 085). open_position appends POSITION_OPENED. A
    worker that booted ahead of the table would write the position and
    fail on its first lifecycle event -- a position with no history."""
    class Catalog:
        async def fetch(self, sql, *args):
            if "information_schema.tables" in sql:
                return [{"table_name": t} for t in args[0]
                        if t != "bettor_experimental_position_events"]
            if "pg_constraint" in sql:
                return _constraint_rows()
            return [{"table_name": t, "column_name": c}
                    for t, c in xstore.REQUIRED_COLUMNS]

    out = await xstore.store_ready(Catalog())
    assert out["storeReady"] is False
    assert out["problems"] == [
        "table bettor_experimental_position_events is absent"]

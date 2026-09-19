"""BETTOR_EV_SHADOW IS THE PRIMARY LANE, and these pin what that means.

Owner clarification 2026-09-19: "BETTOR EV ENGINE IS THE PRIMARY
PRODUCT. RN1_SHADOW is a secondary benchmark/research lane." /
"BETTOR_EV_SHADOW does NOT need P_BETTOR to begin accumulating
prospective evidence." / "Do not use RN1 to make the independent lane
look active."

The most load-bearing tests here are the independence ones. The
temptation this lane will face, repeatedly, is to borrow RN1's signal
to make itself look alive -- and the directive names that explicitly.
So the wall is checked three ways: the declared provenance, the writer's
refusal, and the table's own CHECK.
"""

from __future__ import annotations

import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import shadow as sh
from sportsassets import shadow_bettor as bettor
from sportsassets import shadow_bettor_ops as ops
from sportsassets import shadow_lanes as lanes
from sportsassets import shadow_store as store
from sportsassets.api import command_shadow as CS

ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
BETTOR_SRC = (BACKEND / "sportsassets" / "shadow_bettor.py").read_text()
WORKER_SRC = (BACKEND / "sportsassets" / "workers"
              / "shadow_bettor.py").read_text()
# Operational telemetry, deliberately OUTSIDE the decision module: the
# independence test below refuses any FROM shadow_decisions in
# shadow_bettor.py, and the right answer when it fired was to move the
# code rather than loosen the rule.
OPS_SRC = (BACKEND / "sportsassets" / "shadow_bettor_ops.py").read_text()
MIGRATION = (BACKEND / "migrations"
             / "071_bettor_opportunities.sql").read_text()
SHADOW_JS = (ROOT / "frontend" / "public" / "command"
             / "shadow.js").read_text()
CS_SRC = (BACKEND / "sportsassets" / "api"
          / "command_shadow.py").read_text()

NOW = datetime(2026, 9, 19, 18, 30, 0, tzinfo=timezone.utc)

MUTATING = (r"\bUPDATE\s+\w+\s+SET\b", r"\bDELETE\s+FROM\b",
            r"\bTRUNCATE\b", r"\bDROP\s+TABLE\b",
            r"\bON\s+CONFLICT[^;]*DO\s+UPDATE\b")


def _book(**over):
    fields = dict(captured_at=NOW, symbol="aec-nfl-x",
                  evidence_source="PMUS_BBO", bid=0.48, ask=0.52)
    fields.update(over)
    return store.market_state_record(**fields)


def _op(**over):
    fields = dict(symbol="aec-nfl-x", observed_at=NOW, outcome_leg="HOME",
                  evidence_source="PMUS_BBO", market_state=_book(),
                  cadence_s=300)
    fields.update(over)
    return bettor.opportunity_record(**fields)


# ── the independence wall ────────────────────────────────────────────


def test_the_bettor_lane_never_reads_rn1():
    """Checked in the source, because the wall has to survive somebody
    reaching for the nearest available signal on a quiet afternoon."""
    # Identifiers and table accesses, not English: this module's own
    # prose talks about which blockers prevent the most TRADES, and a
    # test that tripped on that would be switched off within a week.
    for forbidden in ("rn1_observations", "shadow_rn1", "RN1_ACTION",
                      "RN1_ACCOUNT_IDENTITY", "RN1_DERIVED_TARGET",
                      "rn1_price", "mirror_books"):
        assert forbidden not in BETTOR_SRC, forbidden
    for table in ("trades", "mirror_books", "rn1_observations",
                  "shadow_decisions"):
        assert not re.search(r"\bFROM\s+%s\b" % table, BETTOR_SRC,
                             re.IGNORECASE), table
        assert not re.search(r"\bJOIN\s+%s\b" % table, BETTOR_SRC,
                             re.IGNORECASE), table
    for forbidden in ("rn1_observations", "shadow_rn1", "mirror_books"):
        assert forbidden not in WORKER_SRC, forbidden


def test_an_opportunity_declares_only_independent_provenance():
    op = _op()
    assert op["rn1FeaturesUsed"] is False
    for provenance in op["featureLineage"].values():
        assert provenance in lanes.INDEPENDENT_PROVENANCES, provenance


def test_the_table_refuses_an_rn1_derived_opportunity():
    """Not a runtime refusal -- a database CHECK. A policy the schema
    permits is not a policy."""
    assert "bettor_opportunity_independent" in MIGRATION
    assert "CHECK (rn1_features_used IS FALSE)" in MIGRATION


def test_every_bettor_decision_descends_from_an_opportunity():
    assert "shadow_decisions_bettor_observed" in MIGRATION
    assert "bettor_opportunity_id IS NOT NULL" in MIGRATION


# ── collecting without P_BETTOR ──────────────────────────────────────


def test_bettor_collects_without_p_bettor_and_says_so():
    d = bettor.decide(_op(), _book())
    assert d["lane"] == lanes.BETTOR_EV_SHADOW
    assert d["proposedAction"] == sh.NO_TRADE
    assert d["pBettor"] is None
    assert d["pBettorStatus"] == lanes.NOT_ESTABLISHED
    assert d["informationEv"] is None


def test_the_first_blocker_is_always_the_true_one():
    """A dataset that hid INDEPENDENT_EV_NOT_ESTABLISHED behind a
    narrower complaint would misreport what is actually holding the
    engine."""
    codes = [b["code"] for b in bettor.decide(_op(), _book())["blockers"]]
    assert codes[0] == bettor.B_EV_NOT_ESTABLISHED


def test_a_no_trade_carries_its_reasons():
    d = bettor.decide(_op(), _book())
    assert d["reasonCodes"], "a NO_TRADE with no reason is unauditable"
    assert set(d["reasonCodes"]) <= set(bettor.BLOCKERS)


def test_there_is_no_path_that_produces_a_trade_today():
    """Not an oversight. There is no validated independent EV to act on,
    and a lane that traded anyway would manufacture the very claim the
    dataset exists to test."""
    for state in (_book(), _book(bid=0.01, ask=0.99), None):
        d = bettor.decide(_op(market_state=state), state)
        assert d["proposedAction"] == sh.NO_TRADE


def test_an_unreadable_book_is_a_named_blocker_not_a_skip():
    d = bettor.decide(_op(market_state=None), None)
    codes = [b["code"] for b in d["blockers"]]
    assert bettor.B_MARKET_STATE_UNREADABLE in codes


def test_depth_absent_from_a_bbo_is_reported_not_invented():
    op = _op()
    assert op["microstructure"]["depth"] == bettor.NOT_IDENTIFIED
    codes = [b["code"] for b in bettor.decide(op, _book())["blockers"]]
    assert bettor.B_INSUFFICIENT_DEPTH in codes


def test_a_wide_spread_is_named():
    wide = _book(bid=0.30, ask=0.70)
    codes = [b["code"]
             for b in bettor.decide(_op(market_state=wide), wide)["blockers"]]
    assert bettor.B_SPREAD_TOO_WIDE in codes


# ── the dataset's own integrity ──────────────────────────────────────


def test_the_selection_rule_travels_with_every_row():
    op = _op()
    assert op["universeVersion"] == bettor.UNIVERSE_VERSION
    assert op["universeSource"]
    assert op["selectionReason"]


def test_the_evidence_source_is_required():
    with pytest.raises(store.StoreRefusal) as exc:
        _op(evidence_source="")
    assert "EVIDENCE_SOURCE" in str(exc.value)


def test_one_row_per_market_per_cadence_bucket():
    """Without a bucket the dataset records how often the loop ran
    rather than what the market did."""
    a = _op(observed_at=NOW)
    b = _op(observed_at=NOW + timedelta(seconds=30))
    assert a["bettorOpportunityId"] == b["bettorOpportunityId"]
    c = _op(observed_at=NOW + timedelta(seconds=600))
    assert a["bettorOpportunityId"] != c["bettorOpportunityId"]


def test_a_different_market_is_a_different_row():
    assert _op()["bettorOpportunityId"] != \
        _op(symbol="aec-nfl-y")["bettorOpportunityId"]


def test_a_different_evidence_source_is_a_different_row():
    """Two sources looking at one market at one instant are two
    observations, not one. When institutional L2 arrives beside the
    retail book this is what keeps them separable."""
    assert _op()["bettorOpportunityId"] != \
        _op(evidence_source="INSTITUTIONAL_L2")["bettorOpportunityId"]


@pytest.mark.parametrize("pattern", MUTATING)
def test_the_bettor_writer_and_worker_only_insert(pattern):
    for name, src in (("shadow_bettor.py", BETTOR_SRC),
                      ("workers/shadow_bettor.py", WORKER_SRC)):
        assert not re.search(pattern, src, re.IGNORECASE), name


def test_the_bettor_worker_has_no_order_path():
    for forbidden in ("create_order", "place_order", "submit_order",
                      "cancel_order", "ORDER_INTENT", "live_executor"):
        assert forbidden not in WORKER_SRC, forbidden


# ── COMMAND leads with BETTOR ────────────────────────────────────────


def test_the_api_declares_bettor_primary():
    env = CS.environment()
    assert env["identity"] == "BETTOR EV ENGINE"
    assert env["primaryLane"] == lanes.BETTOR_EV_SHADOW
    assert env["benchmarkLane"] == lanes.RN1_SHADOW
    assert env["laneOrder"][0] == lanes.BETTOR_EV_SHADOW
    assert "PRIMARY" in env["laneRoles"][lanes.BETTOR_EV_SHADOW]
    assert "BENCHMARK" in env["laneRoles"][lanes.RN1_SHADOW]


def test_the_hybrid_lane_exists_only_as_not_yet_active():
    hybrid = CS.environment()["hybridLane"]
    assert hybrid["name"] == "RN1_PLUS_BETTOR"
    assert hybrid["state"] == "NOT_YET_ACTIVE"


def test_the_ui_takes_its_lane_order_from_the_server():
    """A front end that hard-coded the order would drift from the API
    the first time the hierarchy changed."""
    assert "environment.laneOrder" in SHADOW_JS
    assert "environment.primaryLane" in SHADOW_JS


def test_the_ui_leads_with_bettor_in_its_own_fallback():
    fallback = re.search(r"laneOrder\)\s*\n?\s*\|\|\s*\[([^\]]+)\]",
                         SHADOW_JS)
    assert fallback, "no fallback order found"
    assert fallback.group(1).index("BETTOR_EV_SHADOW") < \
        fallback.group(1).index("RN1_SHADOW")


def test_the_kpi_row_leads_with_bettor():
    kpi = SHADOW_JS[SHADOW_JS.index("const KPI = ["):]
    kpi = kpi[:kpi.index("];")]
    assert kpi.index("bettorEvDecisions") < kpi.index("rn1SignalsObserved")


def test_the_page_is_titled_for_the_product_not_the_benchmark():
    head = SHADOW_JS[SHADOW_JS.index("function shell()"):]
    head = head[:head.index("function paint")]
    assert "BETTOR EV Engine" in head
    assert "<h1>Shadow</h1>" not in head


def test_the_rn1_blocker_panel_is_labelled_as_the_benchmark():
    assert "Why BETTOR said no" in SHADOW_JS
    assert "Why the RN1 benchmark said no" in SHADOW_JS


def test_a_missing_bettor_store_does_not_503_the_whole_screen():
    """A staged rollout is not an outage, and must not be drawn as one."""
    assert "STORE_NOT_READY" in (
        BACKEND / "sportsassets" / "api" / "command_shadow.py").read_text()
    assert "_bettor_counts" in (
        BACKEND / "sportsassets" / "api" / "command_shadow.py").read_text()


# ── the benchmark feed's health is reported beside BETTOR, not through it ──


def test_the_rn1_feed_is_its_own_health_component():
    """Owner: "Never let an RN1 feed failure make BETTOR appear down."
    So the feed is a component in its own right, and it declares that
    BETTOR does not depend on it."""
    comps = CS.HEALTH_COMPONENTS
    assert "RN1_BENCHMARK_FEED" in comps
    assert comps[0] == "BETTOR_EV_ENGINE", "the primary engine is read first"
    assert len(set(comps)) == len(comps), "a duplicated key hides a component"


def test_the_feed_row_carries_the_four_named_fields():
    for field in ("rn1FeedLastSourceEvent", "rn1FeedLastReceivedEvent",
                  "rn1FeedLagSeconds", "rn1FeedStatus"):
        assert field in CS_SRC, field
    assert '"affectsBettor": False' in CS_SRC


def test_bettor_health_does_not_depend_on_rn1():
    assert "dependsOnRn1=False" in CS_SRC


def test_the_ui_draws_every_component_the_server_reports():
    """A panel keyed off a hard-coded list silently omits a component the
    server added -- the one shape of this screen that could hide a
    failure."""
    assert "function healthKeys" in SHADOW_JS
    assert "RN1_BENCHMARK_FEED" in SHADOW_JS
    for field in ("rn1FeedLastSourceEvent", "rn1FeedLastReceivedEvent",
                  "rn1FeedLagSeconds", "affectsBettor"):
        assert field in SHADOW_JS, field


def test_the_ui_health_list_leads_with_bettor():
    block = SHADOW_JS[SHADOW_JS.index("const HEALTH_LABEL = {"):]
    block = block[:block.index("};")]
    assert block.index("BETTOR_EV_ENGINE") < block.index("RN1_BENCHMARK_FEED")
    assert block.index("BETTOR_EV_ENGINE") < block.index("RN1_LISTENER")


# ── the writer actually writes ───────────────────────────────────────
#
# Both bugs below were live in production for the whole first hour of
# BETTOR collection: 31 opportunities observed, ZERO decisions written,
# the worker reporting tick_failed with an empty problems list. Neither
# was visible to any test that only read the module's prose.

STORE_SRC = (BACKEND / "sportsassets" / "shadow_store.py").read_text()


def _insert_shape(src, name):
    """(column count, highest $N) for a named INSERT constant."""
    block = src[src.index(name):]
    block = block[:block.index('"""', block.index('"""') + 3)]
    cols = block[block.index("(") + 1:block.index(")")]
    ncols = len([c for c in cols.replace("\n", " ").split(",") if c.strip()])
    highest = max(int(m) for m in re.findall(r"\$(\d+)", block))
    return ncols, highest


@pytest.mark.parametrize("module,const", [
    ("shadow_store.py", "_DECISION_INSERT"),
    ("shadow_bettor.py", "_OPPORTUNITY_INSERT"),
])
def test_every_insert_binds_as_many_values_as_it_names_columns(module, const):
    src = STORE_SRC if module == "shadow_store.py" else BETTOR_SRC
    ncols, highest = _insert_shape(src, const)
    assert ncols == highest, (
        "%s names %d columns but binds $1..$%d" % (const, ncols, highest))


def test_a_bettor_decision_carries_the_column_its_check_requires():
    """Migration 071 requires bettor_opportunity_id on every
    BETTOR_EV_SHADOW row. An INSERT that omitted it failed the CHECK on
    every single decision -- silently, because the worker caught the
    exception and reported tick_failed with no problem named."""
    assert "bettor_opportunity_id" in STORE_SRC, \
        "record_decision does not write the column 071's CHECK demands"
    assert 'r.get("bettorOpportunityId")' in STORE_SRC
    assert bettor.decide(_op(), _book())["bettorOpportunityId"]


@pytest.mark.parametrize("src_name", ["shadow_store.py", "shadow_bettor.py"])
def test_returning_columns_are_read_by_the_name_the_database_uses(src_name):
    """asyncpg keys a Row by the column name Postgres returns, which is
    snake_case unless quoted. Reading row["camelCase"] raises KeyError
    AFTER the row is already written -- the worst shape of failure,
    because the store is right and the caller dies."""
    src = STORE_SRC if src_name == "shadow_store.py" else BETTOR_SRC
    for col in re.findall(r"RETURNING\s+(\w+)", src):
        assert 'row["%s"]' % col in src, (
            "%s RETURNs %s but never reads row[%r]" % (src_name, col, col))
        camel = re.sub(r"_(\w)", lambda m: m.group(1).upper(), col)
        assert 'row["%s"]' % camel not in src, (
            "%s reads row[%r]; Postgres returns %r" % (src_name, camel, col))


def test_every_lane_check_column_is_actually_written():
    """Derived from the migrations, not hardcoded: any constraint of the
    shape CHECK (lane <> 'X' OR col IS NOT NULL) names a column the
    decision INSERT must bind. Hardcoding one name would leave the next
    lane's constraint to be discovered the way this one was -- in
    production, an hour later, with zero rows written."""
    decision_insert = STORE_SRC[STORE_SRC.index("_DECISION_INSERT"):]
    decision_insert = decision_insert[:decision_insert.index("ON CONFLICT")]
    required = set()
    for mig in sorted((BACKEND / "migrations").glob("*.sql")):
        for lane, col in re.findall(
                r"CHECK\s*\(\s*lane\s*<>\s*'(\w+)'\s*OR\s*(\w+)\s+IS\s+NOT\s+NULL",
                mig.read_text(), re.IGNORECASE):
            required.add((lane, col))
    assert required, "no lane constraints found; the test has gone blind"
    for lane, col in sorted(required):
        assert col in decision_insert, (
            "lane %s requires %s but the decision INSERT never binds it"
            % (lane, col))


# ── the pipeline watches itself ──────────────────────────────────────

M073 = (BACKEND / "migrations"
        / "073_bettor_decision_pipeline.sql").read_text()


def test_an_annotation_is_not_a_decision():
    """Owner: the 31 opportunities "may NOT be presented as prospective
    shadow decisions." An annotation has no action, no price, no size
    and no lane -- it is a note that a decision is missing."""
    block = M073[M073.index("CREATE TABLE IF NOT EXISTS bettor_opportunity_annotations"):]
    block = block[:block.index(");")]
    for forbidden in ("proposed_action", "proposed_price", "proposed_side",
                      "proposed_quantity", "lane", "p_bettor"):
        assert forbidden not in block, forbidden


def test_an_annotation_is_stamped_when_it_was_made():
    """Retrospective evidence must not wear a prospective timestamp."""
    assert "bettor_annotation_is_retrospective" in M073
    assert "CHECK (annotated_at >= observed_at)" in M073


def test_the_annotation_kinds_are_the_declared_two():
    assert ops.ANNOTATION_WRITER_INCIDENT == \
        "DECISION_NOT_RECORDED_DUE_TO_WRITER_INCIDENT"
    assert ops.ANNOTATION_WRITER_INCIDENT in M073
    assert ops.ANNOTATION_UNEXPLAINED in M073


def test_annotations_and_failures_are_append_only():
    for table in ("bettor_opportunity_annotations", "bettor_decision_failures"):
        assert "%s_immutable" % table in M073, table
    assert M073.count("shadow_append_only()") >= 2


def test_the_orphan_allowance_is_one_number_in_two_places():
    """The worker, the API and any research query must not drift into
    three different ideas of what an orphan is."""
    assert "interval '180 seconds'" in M073
    assert ops.ORPHAN_ALLOWANCE_S == 180


def test_an_in_flight_opportunity_is_not_an_orphan():
    view = M073[M073.index("CREATE OR REPLACE VIEW bettor_orphan_opportunities"):]
    assert "o.observed_at < now() - interval '180 seconds'" in view


def test_a_failure_records_the_error_in_its_own_words():
    """A paraphrase would reproduce the incident, where the worker
    caught its exception and reported an empty problems list."""
    assert "error_text" in M073
    assert "type(error).__name__" in OPS_SRC
    assert "error_text" in OPS_SRC


def test_every_failure_stage_the_worker_uses_is_a_legal_stage():
    used = set(re.findall(r'_note\(pool, "(\w+)"', WORKER_SRC))
    used |= set(re.findall(r'record_failure\(stage="(\w+)"', WORKER_SRC))
    assert used, "the worker records no failures at all"
    for stage in used:
        assert stage in ops.FAILURE_STAGES, stage
        assert "'%s'" % stage in M073, stage


def test_one_markets_failure_does_not_discard_the_tick():
    """Nine healthy markets were thrown away because the tenth raised."""
    body = WORKER_SRC[WORKER_SRC.index("async def tick("):]
    body = body[:body.index("async def run(")]
    assert body.count("continue") >= 2, \
        "a per-market failure must not break the loop"
    assert "_note(pool, \"DECISION_WRITE\"" in body
    assert "_note(pool, \"OPPORTUNITY_WRITE\"" in body


def test_a_failed_tick_names_its_own_error():
    """`problems` comes from the STORE-READINESS check and is empty
    whenever the schema is fine, so tick_failed used to carry a
    reassuring empty list and no cause at all."""
    assert "tickError" in WORKER_SRC
    assert WORKER_SRC.index("tickError") < WORKER_SRC.index("stats.update(boot)")


def test_the_worker_never_raises_while_recording_a_failure():
    note = WORKER_SRC[WORKER_SRC.index("async def _note("):]
    note = note[:note.index("async def tick(")]
    assert "except Exception" in note, \
        "a writer that can fail while writing down its own failure is " \
        "the bug this mechanism exists to end"


# ── COMMAND shows it honestly ────────────────────────────────────────


def test_command_has_a_pipeline_component():
    assert "BETTOR_DECISION_PIPELINE" in CS.HEALTH_COMPONENTS
    assert "_pipeline" in CS_SRC


def test_collection_working_is_not_enough_for_live():
    """Owner: "Do not show LIVE / HEALTHY merely because opportunity
    collection works." An orphan or a recorded failure is DEGRADED."""
    block = CS_SRC[CS_SRC.index("async def _pipeline("):]
    block = block[:block.index("async def summary(")]
    assert 'if orphans or failures:' in block
    assert block.index("DEGRADED") < block.index('state = "LIVE"')


def test_the_pipeline_declares_no_target_rate():
    """Owner: do not invent a target success rate after seeing the
    result. The number is reported and judged by a human."""
    block = CS_SRC[CS_SRC.index("async def _pipeline("):]
    block = block[:block.index("async def summary(")]
    assert not re.search(r"successRate\s*[<>]=?\s*0\.\d", block)
    assert "opportunityToDecisionSuccessRate" in block


def test_a_missing_073_degrades_rather_than_503s():
    block = CS_SRC[CS_SRC.index("async def _pipeline("):]
    block = block[:block.index("async def summary(")]
    assert "STORE_NOT_READY" in block
    assert "migration 073" in block


def test_the_ui_draws_the_two_counters_side_by_side():
    """31 beside 0 with nothing saying so WAS the incident."""
    assert "BETTOR_DECISION_PIPELINE" in SHADOW_JS
    assert "pipelineDetail" in SHADOW_JS
    for field in ("orphanOpportunities", "decisionWriteFailures",
                  "lastSuccessfulDecision", "lastFailure",
                  "opportunityToDecisionSuccessRate"):
        assert field in SHADOW_JS, field


def test_the_decision_module_never_reads_the_decision_ledger():
    """The wall stayed blunt. When adding pipeline health made
    shadow_bettor.py read shadow_decisions, the fix was to move the
    code out, not to carve an exception into the independence test."""
    assert "shadow_decisions" not in BETTOR_SRC
    assert "shadow_decisions" in OPS_SRC


def test_telemetry_can_never_become_a_decision_input():
    """decide() cannot reach the ops module: shadow_bettor does not
    import it, and the import only runs the other way."""
    assert "shadow_bettor_ops" not in BETTOR_SRC
    assert "import shadow_bettor as bettor" in OPS_SRC

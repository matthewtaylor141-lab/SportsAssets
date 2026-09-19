"""THE STORE'S GUARANTEES, PINNED.

Owner directive 2026-09-19: before the first row, verify LANE =
RN1_SHADOW, P_BETTOR NULL/NOT_ESTABLISHED, INFORMATION_EV
NULL/NOT_ESTABLISHED, SHADOW_MODE TRUE, CAPITAL_AT_RISK 0, no default
lane, no UPDATE/DELETE path.

Most of these are checked here WITHOUT A DATABASE, against the
migration's own text and the writer's own source. That is deliberate
rather than lazy: the guarantees have to hold on a machine that has
never connected to Postgres, and a test that needs a live database to
notice a mutating statement is a test that will be skipped in CI on the
day it matters.
"""

from __future__ import annotations

import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import shadow as sh
from sportsassets import shadow_lanes as lanes
from sportsassets import shadow_policy as pol
from sportsassets import shadow_rn1
from sportsassets import shadow_store as store

ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
STORE_SRC = (BACKEND / "sportsassets" / "shadow_store.py").read_text()
RN1_SRC = (BACKEND / "sportsassets" / "shadow_rn1.py").read_text()
WORKER_SRC = (BACKEND / "sportsassets" / "workers" / "shadow_rn1.py").read_text()
API_SRC = (BACKEND / "sportsassets" / "api" / "command_shadow.py").read_text()
MIGRATION = (BACKEND / "migrations"
             / "070_rn1_prospective_observation.sql").read_text()

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)

# A statement shape, not a bare word: the modules discuss corrections
# and invalidation in prose, and a test that trips on the English would
# be turned off within a week and then it would protect nothing.
MUTATING = (
    r"\bUPDATE\s+\w+\s+SET\b",
    r"\bDELETE\s+FROM\b",
    r"\bTRUNCATE\b",
    r"\bDROP\s+TABLE\b",
    r"\bON\s+CONFLICT[^;]*DO\s+UPDATE\b",
)


def _sample_observation(**over):
    fields = dict(source_type="CHAIN", bettor_received_ts=NOW,
                  rn1_source_ts=NOW - timedelta(milliseconds=800),
                  side="BUY", rn1_price=0.42, rn1_quantity=1000,
                  tx_hash="0xabc", asset="token-1", symbol="aec-nfl-ne-sea",
                  outcome_leg="NE", market_id="cond-1")
    fields.update(over)
    return store.observation_record(**fields)


def _book(**over):
    fields = dict(captured_at=NOW, symbol="aec-nfl-ne-sea",
                  evidence_source="PMUS_BBO", bid=0.41, ask=0.43,
                  available_depth={"asks": [{"price": 0.43, "size": 400}],
                                   "bids": [{"price": 0.41, "size": 400}]})
    fields.update(over)
    return store.market_state_record(**fields)


# ── the append-only writer ───────────────────────────────────────────


@pytest.mark.parametrize("pattern", MUTATING)
def test_the_writer_holds_no_mutating_statement(pattern):
    """The database trigger and the missing code path are two
    INDEPENDENT guarantees, and this is the second one. The trigger
    protects the table from anyone; this protects the table from us."""
    assert not re.search(pattern, STORE_SRC, re.IGNORECASE), (
        "shadow_store.py contains %s — a correction is an append, not an "
        "edit, and an incident is not an exception to that" % pattern)


@pytest.mark.parametrize("pattern", MUTATING)
def test_neither_the_rn1_writer_nor_its_worker_mutates(pattern):
    for name, src in (("shadow_rn1.py", RN1_SRC),
                      ("workers/shadow_rn1.py", WORKER_SRC)):
        assert not re.search(pattern, src, re.IGNORECASE), \
            "%s contains %s" % (name, pattern)


@pytest.mark.parametrize("pattern", MUTATING)
def test_the_command_shadow_api_only_reads(pattern):
    assert not re.search(pattern, API_SRC, re.IGNORECASE)


def test_the_shadow_worker_has_no_order_path():
    """MEASUREMENT ONLY, checked in the import graph rather than
    promised in a docstring."""
    for forbidden in ("create_order", "place_order", "submit_order",
                      "cancel_order", "ORDER_INTENT", "live_executor"):
        assert forbidden not in WORKER_SRC, forbidden
    for forbidden in ("create_order", "place_order", "pmus", "clob"):
        assert forbidden not in STORE_SRC, forbidden


def test_the_api_module_imports_no_venue_client():
    for forbidden in ("pmus", "clob", "gamma", "live_executor",
                      "PMX_", "private_key"):
        assert forbidden not in API_SRC, forbidden


# ── the idempotency key ──────────────────────────────────────────────


def test_a_venue_native_fill_id_is_preferred_and_says_so():
    key = store.observation_key(venue="polymarket", source_fill_id="fill-9")
    assert key["basis"] == store.VENUE_NATIVE_FILL_ID
    assert "fill-9" in key["canonical"]


def test_the_derived_key_is_not_market_side_price():
    """"Do not deduplicate economically distinct RN1 fills merely
    because they have the same market, side and price." Two fills that
    differ ONLY in size, or ONLY in instant, are two fills."""
    base = dict(venue="polymarket", tx_hash="0xabc", asset="tok",
                side="BUY", price=0.42, source_ts=NOW)
    a = store.observation_key(quantity=100, **base)["key"]
    b = store.observation_key(quantity=101, **base)["key"]
    assert a != b

    later = dict(base, source_ts=NOW + timedelta(seconds=1))
    c = store.observation_key(quantity=100, **later)["key"]
    assert a != c

    other_tx = dict(base, tx_hash="0xdef")
    assert a != store.observation_key(quantity=100, **other_tx)["key"]


def test_the_same_fill_normalizes_to_the_same_key():
    base = dict(venue="polymarket", tx_hash="0xABC", asset="tok",
                side="buy", quantity=100, price=0.42, source_ts=NOW)
    a = store.observation_key(**base)["key"]
    b = store.observation_key(**dict(base, tx_hash="0xabc", side="BUY",
                                     quantity=100.0000001, price="0.420000"))
    assert a == b["key"]


def test_a_key_that_would_have_to_guess_is_refused():
    with pytest.raises(store.StoreRefusal) as exc:
        store.observation_key(venue="polymarket", tx_hash="0xabc",
                              asset="tok", side="BUY", price=0.42,
                              quantity=None)
    assert "quantity" in str(exc.value)


# ── the sighting ─────────────────────────────────────────────────────


def test_an_observation_requires_the_one_clock_only_we_hold():
    with pytest.raises(store.StoreRefusal) as exc:
        _sample_observation(bettor_received_ts=None)
    assert "BETTOR_RECEIVED" in str(exc.value)


def test_a_missing_source_timestamp_is_named_not_substituted():
    obs = _sample_observation(rn1_source_ts=None,
                              source_ts_status=store.TS_MISSING)
    assert obs["rn1SourceTs"] is None
    assert obs["sourceTsStatus"] == store.TS_MISSING


def test_claiming_the_source_supplied_a_timestamp_it_did_not_is_refused():
    with pytest.raises(store.StoreRefusal):
        _sample_observation(rn1_source_ts=None,
                            source_ts_status=store.SOURCE_SUPPLIED)


def test_a_substituted_clock_declares_itself():
    class Ev:
        source = "chain"
        ts_epoch = int(NOW.timestamp())
        ts_fallback = True
        dedupe_key = "d"
        tx_hash = "0xabc"
        asset = "tok"
        side = "BUY"
        price = 0.42
        size = 1000
        condition_id = "cond"
        market_slug = "sym"
        event_slug = "ev"
        outcome = "NE"

    obs = shadow_rn1.observation_from_trade_event(Ev(), received_at=NOW)
    assert obs["sourceTsStatus"] == store.FALLBACK_SUBSTITUTED


def test_an_observation_says_it_is_not_a_trade():
    assert _sample_observation()["observationIsNotATrade"] is True


def test_a_fill_of_no_shares_is_not_a_fill():
    with pytest.raises(store.StoreRefusal):
        _sample_observation(rn1_quantity=0)


# ── corrections stay appends ─────────────────────────────────────────


def test_an_invalidation_references_the_original_and_is_a_new_row():
    original = _sample_observation()
    superseding = store._supersede(original, store.OBSERVATION_INVALIDATED,
                                   "chain reorg at block 61,203,114")
    assert superseding["supersedesObservationId"] == \
        original["rn1ObservationId"]
    assert superseding["rn1ObservationId"] != original["rn1ObservationId"]
    # the key is SHARED on purpose: that is how the two rows are known
    # to be about the same fill
    assert superseding["idempotencyKey"] == original["idempotencyKey"]
    assert superseding["recordKind"] == store.OBSERVATION_INVALIDATED


def test_an_erasure_wearing_a_label_is_refused():
    with pytest.raises(store.StoreRefusal) as exc:
        store._supersede(_sample_observation(),
                         store.OBSERVATION_INVALIDATED, "")
    assert "erasure" in str(exc.value)


def test_two_different_corrections_do_not_collide():
    original = _sample_observation()
    a = store._supersede(original, store.OBSERVATION_CORRECTED, "size restated",
                         {"rn1Quantity": 990})
    b = store._supersede(original, store.OBSERVATION_CORRECTED, "price restated",
                         {"rn1Price": 0.425})
    assert a["rn1ObservationId"] != b["rn1ObservationId"]


# ── the market state ─────────────────────────────────────────────────


def test_a_market_state_must_name_its_evidence_source():
    with pytest.raises(store.StoreRefusal) as exc:
        store.market_state_record(captured_at=NOW, symbol="s",
                                  evidence_source="")
    assert "EVIDENCE_SOURCE" in str(exc.value)


def test_an_unreadable_book_carries_a_reason_and_no_prices():
    state = store.market_state_record(
        captured_at=NOW, symbol="s", evidence_source="PMUS_BBO",
        readable=False, why_unreadable="VENUE_MARKET_STATE_HALTED")
    assert state["mid"] is None and state["spread"] is None
    assert state["whyUnreadable"] == "VENUE_MARKET_STATE_HALTED"


def test_unreadable_with_no_reason_is_refused():
    with pytest.raises(store.StoreRefusal):
        store.market_state_record(captured_at=NOW, symbol="s",
                                  evidence_source="X", readable=False)


# ── the decision, before the first row ───────────────────────────────


def test_an_rn1_decision_is_in_the_rn1_lane_with_belief_held_closed():
    record = shadow_rn1.decide(_sample_observation(), _book())
    assert record["lane"] == lanes.RN1_SHADOW
    assert record["pBettor"] is None
    assert record["pBettorStatus"] == lanes.NOT_ESTABLISHED
    assert record["informationEv"] is None
    assert record["shadowMode"] is True
    assert record["capitalAtRisk"] == 0
    assert record["realOrderSubmissionEnabled"] is False


def test_an_rn1_decision_with_a_manufactured_belief_is_refused():
    with pytest.raises(store.StoreRefusal) as exc:
        store.rn1_decision_record(
            rn1_observation_id="obs", shadowDecisionId="d", symbol="s",
            outcomeLeg="L", modelVersion="m", evidenceSource="e",
            decisionTs=NOW, proposedAction=sh.NO_TRADE, pBettor=0.55)
    assert "independent belief" in str(exc.value)


def test_a_decision_with_no_observation_behind_it_is_refused():
    with pytest.raises(store.StoreRefusal) as exc:
        store.rn1_decision_record(
            rn1_observation_id=None, shadowDecisionId="d", symbol="s",
            outcomeLeg="L", modelVersion="m", evidenceSource="e",
            decisionTs=NOW, proposedAction=sh.NO_TRADE)
    assert "not in the lane" in str(exc.value)


def test_rn1_price_is_never_bettors_executable_price():
    """The whole comparison depends on this. RN1 filled at 0.42; we
    would have had to pay the ask we actually saw."""
    obs = _sample_observation(rn1_price=0.42)
    record = shadow_rn1.decide(obs, _book(bid=0.41, ask=0.43))
    assert record["rn1Price"] == 0.42
    assert record["proposedPrice"] == 0.43
    assert record["priceWhenBettorObserved"] == 0.43


def test_a_sell_is_priced_off_the_bid_not_the_ask():
    record = shadow_rn1.decide(_sample_observation(side="SELL"),
                               _book(bid=0.41, ask=0.43))
    assert record["proposedPrice"] == 0.41


def test_no_book_is_a_recorded_no_trade_not_a_missing_row():
    record = shadow_rn1.decide(_sample_observation(), None)
    assert record["proposedAction"] == sh.NO_TRADE
    assert record["blockers"][0]["code"] == shadow_rn1.B_NO_MARKET_STATE


def test_an_unreadable_book_blocks_by_name():
    state = store.market_state_record(
        captured_at=NOW, symbol="s", evidence_source="PMUS_BBO",
        readable=False, why_unreadable="VENUE_MARKET_STATE_HALTED")
    record = shadow_rn1.decide(_sample_observation(), state)
    assert record["proposedAction"] == sh.NO_TRADE
    assert record["blockers"][0]["code"] == shadow_rn1.B_BOOK_UNREADABLE


def test_unknown_depth_refuses_a_size_rather_than_inventing_one():
    record = shadow_rn1.decide(_sample_observation(),
                               _book(available_depth=None))
    assert record["proposedAction"] == sh.NO_TRADE
    assert record["blockers"][0]["code"] == shadow_rn1.B_NO_DEPTH


def test_a_shadow_fill_never_exceeds_the_depth_we_observed():
    record = shadow_rn1.decide(
        _sample_observation(rn1_quantity=100000),
        _book(available_depth={"asks": [{"price": 0.43, "size": 37}]}))
    assert record["proposedQuantity"] == 37
    assert "CAPPED_AT_OBSERVED_DEPTH" in record["reasonCodes"]


def test_a_buy_reads_the_ask_side_of_the_depth():
    """Reading the wrong side grants liquidity that was never offered."""
    thin_ask = {"asks": [{"price": 0.43, "size": 5}],
                "bids": [{"price": 0.41, "size": 9000}]}
    record = shadow_rn1.decide(_sample_observation(rn1_quantity=100000),
                               _book(available_depth=thin_ask))
    assert record["proposedQuantity"] == 5


def test_every_no_trade_is_retained_with_its_reason():
    record = shadow_rn1.decide(_sample_observation(), None)
    assert record["reasonCodes"], "a NO_TRADE with no reason is unauditable"


def test_the_decision_id_is_deterministic_for_one_observation():
    obs = _sample_observation()
    a = shadow_rn1.decide(obs, _book())["shadowDecisionId"]
    b = shadow_rn1.decide(obs, _book())["shadowDecisionId"]
    assert a == b


# ── the frozen policy ────────────────────────────────────────────────


def test_the_policy_declares_every_version_the_directive_names():
    frozen = pol.frozen_policy()
    for key in ("pairingRuleVersion", "cashoutRuleVersion",
                "latencyPolicyVersion", "executionReconstructionVersion",
                "sizingPolicyVersion", "policySha", "actionSet"):
        assert frozen[key], key
    assert frozen["lane"] == lanes.RN1_SHADOW
    assert frozen["actionSet"] == list(sh.ACTIONS)


def test_the_policy_sha_moves_when_a_rule_moves():
    changed = dict(pol.DECLARATION)
    changed["sizing"] = dict(pol.SIZING, ratio=0.2)
    assert pol.policy_sha(changed) != pol.POLICY_SHA


def test_the_policy_sha_is_not_a_file_hash():
    """A comment edit must not invent a new policy generation. The two
    hashes are separate for exactly this reason."""
    assert pol.POLICY_SHA != pol.POLICY_CODE_SHA


def test_a_hash_that_could_not_be_computed_says_so(monkeypatch):
    monkeypatch.setattr(pol, "CODE_FILES", ("no_such_module.py",))
    assert pol.policy_code_sha() == pol.NOT_IDENTIFIED


def test_the_policy_holds_rn1_belief_closed():
    assert pol.DECLARATION["pBettor"] == lanes.NOT_ESTABLISHED
    assert pol.DECLARATION["informationEv"] == lanes.NOT_ESTABLISHED
    assert pol.DECLARATION["fairValueManufacturedFromRn1"] is False


def test_the_policy_carries_the_standing_safety_labels():
    assert pol.DECLARATION["shadowMode"] is True
    assert pol.DECLARATION["realOrderSubmissionEnabled"] is False
    assert pol.DECLARATION["capitalAtRisk"] == 0
    assert pol.DECLARATION["mirrorLive"] is False


def test_the_five_second_horizon_stays_dead():
    assert "5S" not in pol.SCORING["horizons"]


def test_the_sizing_policy_is_copied_from_the_live_knobs_not_read():
    """A frozen policy that read an operator dial would move with it,
    which is the whole drift this freeze exists to prevent."""
    assert "pinnedFrom" in pol.SIZING
    assert "settings" not in pol.__dict__
    src = (BACKEND / "sportsassets" / "shadow_policy.py").read_text()
    assert "getenv" not in src and "settings(" not in src


# ── the migration's own text ─────────────────────────────────────────


def test_every_shadow_table_carries_an_append_only_trigger():
    for table in ("rn1_observations", "shadow_market_states",
                  "shadow_policy_versions"):
        assert "%s_immutable" % table in MIGRATION
        assert "EXECUTE FUNCTION shadow_append_only()" in MIGRATION


def test_the_dedupe_index_is_partial_over_sightings_only():
    assert "rn1_observations_idem_idx" in MIGRATION
    assert "WHERE record_kind = 'OBSERVATION'" in MIGRATION


def test_a_correction_must_point_at_what_it_corrects():
    assert "rn1_obs_correction_references" in MIGRATION


def test_an_rn1_decision_cannot_be_written_without_an_observation():
    assert "shadow_decisions_rn1_observed" in MIGRATION


def test_a_decision_cannot_name_an_unfrozen_policy():
    assert "shadow_decisions_policy_frozen" in MIGRATION
    assert "REFERENCES shadow_policy_versions" in MIGRATION


def test_the_four_prices_have_four_columns():
    for column in ("rn1_price", "price_when_bettor_observed",
                   "price_when_bettor_decided", "price_at_shadow_arrival"):
        assert column in MIGRATION, column


def test_the_lane_column_still_has_no_default():
    lanes_sql = (BACKEND / "migrations" / "069_shadow_lanes.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS lane TEXT," in lanes_sql
    assert "lane TEXT DEFAULT" not in lanes_sql


def test_the_preflight_checks_every_guarantee_by_name():
    for name in ("shadow_decisions_shadow_only", "shadow_decisions_lane",
                 "shadow_decisions_rn1_no_belief",
                 "shadow_decisions_rn1_observed",
                 "shadow_decisions_policy_frozen"):
        assert name in store.REQUIRED_CONSTRAINTS
    assert "column_default" in STORE_SRC
    assert "rn1_observations_idem_idx" in STORE_SRC


# ── the backfill must never look prospective ─────────────────────────


def test_the_ingestion_hook_sits_after_the_notify_gate():
    """notify=False IS the deep history import. Those rows are genuine
    first inserts of fills that happened weeks ago; writing them into a
    prospective ledger would turn it into a backtest."""
    src = (BACKEND / "sportsassets" / "ingestion" / "pipeline.py").read_text()
    notify_gate = src.index("if not notify:\n        return trade_id, True")
    hook = src.index("await _shadow_observe(ev, _shadow_received)")
    was_insert = src.index('if not row["was_insert"]:')
    assert was_insert < notify_gate < hook


def test_the_arrival_instant_is_stamped_before_the_pool():
    src = (BACKEND / "sportsassets" / "ingestion" / "pipeline.py").read_text()
    stamp = src.index("_shadow_received = datetime.now")
    pool = src.index("pool = await get_pool()", stamp - 2000)
    assert stamp < pool


def test_the_shadow_hook_cannot_break_ingestion():
    src = (BACKEND / "sportsassets" / "ingestion" / "pipeline.py").read_text()
    body = src[src.index("async def _shadow_observe"):
               src.index("def _feed_payload")]
    assert "except Exception" in body
    statements = [ln.strip() for ln in body.splitlines()
                  if ln.strip().startswith("raise")]
    assert not statements, statements


# ── section 7: a recovered fill is not a prospective observation ──────

M072 = (BACKEND / "migrations"
        / "072_recovered_is_not_prospective.sql").read_text()


def test_recovered_is_a_named_kind_not_an_absence():
    """A late fill is real evidence about the venue and is kept. What it
    may not do is claim to be evidence about our own foresight."""
    assert store.RECOVERED_AFTER_INGESTION_INCIDENT in M072
    assert "'RECOVERED_AFTER_INGESTION_INCIDENT'" in M072


def test_exactly_one_kind_may_back_a_prospective_decision():
    assert store.PROSPECTIVE_KINDS == (store.OBSERVATION,)


def test_the_prohibition_is_a_foreign_key_not_a_convention():
    """Owner: recovered fills "cannot generate a prospective shadow
    decision." A writer that merely declines to is a writer that can be
    edited in a hurry during the next outage."""
    assert "prospective_kind" in M072
    assert "shadow_decisions_prospective_only" in M072
    assert "FOREIGN KEY (rn1_observation_id, rn1_observation_kind)" in M072
    assert "REFERENCES rn1_observations (rn1_observation_id, prospective_kind)" \
        in M072


def test_prospective_kind_is_null_for_every_other_kind():
    """The whole guard rests on this CASE having no ELSE."""
    gen = M072[M072.index("prospective_kind"):]
    gen = gen[:gen.index("STORED")]
    assert "CASE WHEN record_kind = 'OBSERVATION' THEN 'OBSERVATION' END" in gen
    assert "ELSE" not in gen.upper()


def test_a_recovered_row_names_its_incident():
    assert "rn1_obs_recovery_named" in M072
    assert "recovery_incident" in M072 and "recovered_at" in M072


def test_recovery_is_not_a_correction():
    """It supersedes nothing, because nothing was there -- so the
    correction constraint must not demand a predecessor of it."""
    # rindex, because the statement above it DROPs the old constraint by
    # the same name and the DROP is not what is being read here.
    block = M072[M072.rindex("ADD CONSTRAINT rn1_obs_correction_references"):]
    block = block[:block.index(";")]
    assert "'RECOVERED_AFTER_INGESTION_INCIDENT'" in block
    assert "supersedes_observation_id IS NULL" in block


def test_the_readiness_check_requires_the_new_guards():
    for name in ("shadow_decisions_prospective_only", "rn1_obs_recovery_named"):
        assert name in store.REQUIRED_CONSTRAINTS, name


def test_the_migration_writes_nothing_to_existing_rows():
    """Append-only is not suspended to add a guard."""
    for pattern in (r"\bUPDATE\s+\w+\s+SET\b", r"\bDELETE\s+FROM\b",
                    r"\bTRUNCATE\b", r"\bDROP\s+TABLE\b"):
        assert not re.search(pattern, M072, re.IGNORECASE), pattern

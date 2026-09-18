"""THE LEDGER SAYS WHICH EXCHANGE, AND WHICH OF ITS ENVIRONMENTS.

WHY THIS FILE EXISTS. Polymarket approved an institutional account. The
institutional gateway has a production host and a TEST-FUNDED preproduction
host -- `POST /v1/positions/balance` answered `buyingPower "1000000"` on
preprod on 2026-09-10 -- so the moment a second lane can write to this
ledger, an untagged number stops meaning anything. Three dollars of preprod
"spend" is not three per cent of the approved $100; a preprod fill is not
evidence of a production fill.

The separation is STRUCTURAL. A session is bound to one (venue,
environment); a ticket naming another pair is refused rather than coerced,
so there is no blended total to correct afterwards.

WHAT IS DELIBERATELY NOT WIDENED. The approved boundary is ONE unresolved
lifecycle -- not one per environment. Two things keep that true across a
second session and both are pinned below: the lifecycle index is on a
CONSTANT expression, so it spans every session; and `claim_send` asks for
an unresolved attempt ANYWHERE, not just in its own session.

THE VENUE'S OWN CORRELATION IDENTIFIER. The institutional REST order schema
carries `clordId` and the order rows echo it back, so where a venue offers
native correlation the ledger has somewhere to put it. It is recorded
BEFORE the send, beside -- never instead of -- the pre-image: on the retail
venue `mint_client_id()` answers None because `CreateOrderParams` has no
such field, and None here records that the venue offers none rather than
that we failed to keep one.

NOTHING HERE REACHES A VENUE OR A DATABASE.
"""
import asyncio
import pathlib
import re

import pytest

from sportsassets import calibration as cal
from sportsassets import calibration_adapter as adapter
from sportsassets import calibration_execute as ex
from sportsassets import calibration_store as store

from tests.test_calibration_claim import (FakePool, RecordingVenue,
                                          approved_row, session_row)

ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/migrations/067_calibration_environment_tag.sql"
ROLLBACK = (ROOT / "backend/migrations/rollback"
                 / "067_calibration_environment_tag.down.sql")
LIFECYCLES = ROOT / "backend/migrations/065_calibration_lifecycles.sql"


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# THE ADMISSIBLE TICKET the endpoint tests already use, so this file
# measures the environment rule and not some other refusal.
from tests.test_calibration_endpoints import ticket                # noqa: E402


def a_session(**over):
    """A durable session in `load`'s shape, without touching a database."""
    s = {
        "experiment": cal.EXPERIMENT, "sessionId": "MICRO-EXEC-CAL-1",
        "authorisedBy": cal.AUTHORISED_BY,
        "limits": {
            "maxAllInCostPerTradeLifecycle":
                cal.MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE,
            "maxSessionCumulativeSpend": cal.MAX_SESSION_CUMULATIVE_SPEND,
            "maxConcurrentOrderPositionLifecycles":
                cal.MAX_CONCURRENT_ORDER_POSITION_LIFECYCLES,
        },
        "spent": 0.0, "stopped": False, "lifecycles": [],
        "venue": "polymarket-us", "environment": "PRODUCTION",
    }
    s.update(over)
    return s


# ── the environment is an identity field, not a default ──────────────

class TestAnUndeclaredEnvironmentIsANamedBlocker:
    def test_a_ticket_without_one_is_refused_by_name(self):
        t = ticket()
        t.pop("environment")
        why = cal.refusals(t, a_session(), {"available": 5000.0})
        assert "%s:environment" % cal.R_IDENTITY in why

    def test_an_empty_string_is_refused_too(self):
        why = cal.refusals(ticket(environment="   "), a_session(),
                           {"available": 5000.0})
        assert "%s:environment" % cal.R_IDENTITY in why

    def test_a_third_word_is_refused_by_its_own_name(self):
        why = cal.refusals(ticket(environment="STAGING"), a_session(),
                           {"available": 5000.0})
        assert "%s:STAGING" % cal.R_ENVIRONMENT in why

    def test_a_declared_matching_environment_raises_nothing(self):
        why = cal.refusals(ticket(), a_session(), {"available": 5000.0})
        assert why == [], why

    def test_the_two_environments_are_one_tuple_shared_by_both_modules(self):
        # Not two lists that happen to agree today: a third environment
        # admitted by one module and refused by the other is exactly the
        # defect this guards against.
        assert store.ENVIRONMENTS is cal.ENVIRONMENTS
        assert cal.ENVIRONMENTS == ("PRODUCTION", "PREPROD")


# ── a session is bound to one venue and one environment ──────────────

class TestASessionIsBoundAndRefusesTheOtherLane:
    def _reserve(self, t, srow=None, pool=None):
        # No open lifecycle: the one permitted slot is free, so what is
        # measured here is the environment rule and nothing else.
        pool = pool or FakePool(session=srow or session_row(), lifecycles=[])
        return run(store.reserve(t, {"available": 5000.0}, "matt",
                                 pool=pool)), pool

    def test_a_preprod_ticket_cannot_enter_a_production_session(self):
        pool = FakePool(session=session_row())
        with pytest.raises(ValueError) as exc:
            run(store.reserve(ticket(environment="PREPROD"),
                              {"available": 5000.0}, "matt", pool=pool))
        assert store.R_ENV_MISMATCH in str(exc.value)
        assert "PREPROD" in str(exc.value) and "PRODUCTION" in str(exc.value)

    def test_and_nothing_is_written_when_it_is_refused(self):
        pool = FakePool(session=session_row())
        with pytest.raises(ValueError):
            run(store.reserve(ticket(environment="PREPROD"),
                              {"available": 5000.0}, "matt", pool=pool))
        assert not [s for s, _ in pool.executed
                    if "INSERT INTO calibration_lifecycles" in s]

    def test_a_production_ticket_cannot_enter_a_preprod_session(self):
        pool = FakePool(session=session_row(session_id="PMX-PREPROD-1",
                                            venue="pmx_preprod",
                                            environment="PREPROD"))
        with pytest.raises(ValueError) as exc:
            run(store.reserve(ticket(), {"available": 5000.0}, "matt",
                              pool=pool, session_id="PMX-PREPROD-1"))
        assert store.R_ENV_MISMATCH in str(exc.value)

    def test_a_different_venue_is_refused_by_its_own_name(self):
        pool = FakePool(session=session_row())
        with pytest.raises(ValueError) as exc:
            run(store.reserve(ticket(venue="pmx_preprod"),
                              {"available": 5000.0}, "matt", pool=pool))
        assert store.R_VENUE_MISMATCH in str(exc.value)

    def test_a_matching_ticket_is_admitted(self):
        got, pool = self._reserve(ticket())
        assert got["spent"] == 0.0
        ins = [(s, a) for s, a in pool.executed
               if "INSERT INTO calibration_lifecycles" in s]
        assert len(ins) == 1

    def test_the_environment_is_the_fourth_bound_parameter(self):
        # $4, straight after the venue. The positional statement is what
        # actually writes the column, so it is what is pinned.
        _, pool = self._reserve(ticket())
        sql, args = [(s, a) for s, a in pool.executed
                     if "INSERT INTO calibration_lifecycles" in s][0]
        assert re.search(r"\(session_id, client_order_id, venue, environment,",
                         sql)
        assert args[2] == "polymarket-us"
        assert args[3] == "PRODUCTION"
        # and the money is still where the statement says it is
        assert args[9] == 0.40 and args[10] == 10


class TestTheBudgetCarriesWhatItIsAbout:
    def test_load_reports_the_session_s_venue_and_environment(self):
        s = run(store.load(FakePool(session=session_row())))
        assert s["venue"] == "polymarket-us"
        assert s["environment"] == "PRODUCTION"

    def test_a_preprod_session_reports_itself_as_preprod(self):
        s = run(store.load(FakePool(session=session_row(
            venue="pmx_preprod", environment="PREPROD"))))
        assert (s["venue"], s["environment"]) == ("pmx_preprod", "PREPROD")

    def test_the_budget_block_command_shows_carries_them_too(self):
        b = run(store.budget(FakePool(session=session_row())))
        assert b["venue"] == "polymarket-us"
        assert b["environment"] == "PRODUCTION"

    def test_a_lifecycle_row_reports_its_own_environment(self):
        pool = FakePool(session=session_row(),
                        lifecycles=[approved_row(environment="PREPROD")])
        lc = run(store.load(pool))["lifecycles"][0]
        assert lc["environment"] == "PREPROD"

    def test_ensure_session_refuses_an_environment_it_cannot_keep_apart(self):
        with pytest.raises(ValueError):
            run(store.ensure_session(FakePool(session=session_row()),
                                     environment="STAGING"))

    def test_ensure_session_writes_the_pair_into_the_row(self):
        pool = FakePool(session=session_row())
        run(store.ensure_session(pool, session_id="PMX-PREPROD-1",
                                 venue="pmx_preprod", environment="PREPROD"))
        sql, args = [(s, a) for s, a in pool.executed
                     if "INSERT INTO calibration_sessions" in s][0]
        assert "venue, environment" in sql
        assert args[-2:] == ("pmx_preprod", "PREPROD")


# ── the attempt says which exchange, without a join ──────────────────

class TestTheAttemptCarriesTheLaneItWasMadeIn:
    def test_it_takes_them_from_the_lifecycle_not_the_session(self):
        # The session here says PRODUCTION and the lifecycle says PREPROD.
        # The attempt must follow the ORDER, because the order is what was
        # sent. (In service this pair cannot disagree -- `reserve` refuses
        # it -- which is exactly why the test constructs it by hand.)
        pool = FakePool(session=session_row(),
                        lifecycles=[approved_row(environment="PREPROD",
                                                 venue="pmx_preprod")])
        got = run(store.claim_send("CAL-0001", "matt", pool=pool))
        assert got["CLAIMED"] is True
        assert pool.attempts[0]["environment"] == "PREPROD"
        assert pool.attempts[0]["venue"] == "pmx_preprod"

    def test_the_bound_snapshot_names_the_environment(self):
        pool = FakePool(session=session_row(),
                        lifecycles=[approved_row()])
        got = run(store.claim_send("CAL-0001", "matt", pool=pool))
        assert got["bound"]["environment"] == "PRODUCTION"

    def test_a_lifecycle_with_no_environment_cannot_be_claimed(self):
        # An order whose environment is unknown is an order nobody can
        # attribute afterwards, so the claim names it rather than
        # defaulting it.
        row = approved_row()
        row["environment"] = None
        pool = FakePool(session=session_row(), lifecycles=[row])
        got = run(store.claim_send("CAL-0001", "matt", pool=pool))
        assert got["CLAIMED"] is False
        assert any("APPROVED_FIELDS_INCOMPLETE" in b and "environment" in b
                   for b in got["blockers"]), got["blockers"]


class TestOneUnresolvedAttemptAnywhereBlocksEverything:
    def test_an_attempt_in_another_session_blocks_this_one(self):
        pool = FakePool(session=session_row(), lifecycles=[approved_row()])
        pool.attempts.append({
            "attempt_id": "ATT-PREPROD-1", "session_id": "PMX-PREPROD-1",
            "client_order_id": "PMX-0001", "state": "SENT_OUTCOME_UNKNOWN",
            "claimed_by": "matt", "claimed_at": "2026-09-18T16:00:00Z",
            "pre_open_order_ids": [], "venue_order_id": None,
            "outcome": None, "reason": None, "venue": "pmx_preprod",
            "environment": "PREPROD", "venue_clord_id": "c-1"})
        got = run(store.claim_send("CAL-0001", "matt", pool=pool))
        assert got["CLAIMED"] is False
        assert store.R_ALREADY_CLAIMED in got["blockers"]
        assert got["outstanding"]["session_id"] == "PMX-PREPROD-1"

    def test_the_global_read_has_no_session_filter(self):
        assert "session_id = $1" not in store.ANY_UNRESOLVED_SQL
        assert "session_id = $1" in store.UNRESOLVED_SQL

    def test_the_session_scoped_read_still_answers_its_own_question(self):
        pool = FakePool(session=session_row())
        pool.attempts.append({
            "attempt_id": "ATT-1", "session_id": "MICRO-EXEC-CAL-1",
            "client_order_id": "CAL-0001", "state": "CLAIMED",
            "claimed_by": "matt", "claimed_at": "2026-09-18T16:00:00Z",
            "pre_open_order_ids": None, "venue_order_id": None,
            "outcome": None, "reason": None, "venue": "polymarket-us",
            "environment": "PRODUCTION", "venue_clord_id": None})
        assert run(store.unresolved_attempt(pool))["attempt_id"] == "ATT-1"


# ── the venue's own correlation identifier ───────────────────────────

class TestTheRetailVenueOffersNoClientIdentifier:
    def test_mint_client_id_answers_none(self):
        v = adapter.LiveVenue(submit_fn=lambda *a, **k: {},
                              status_fn=lambda *a: None,
                              cancel_fn=lambda *a: {},
                              open_orders_fn=lambda *a, **k: [])
        assert v.mint_client_id() is None

    def test_and_the_module_says_why_rather_than_leaving_it_blank(self):
        assert "no client-supplied" in adapter.LiveVenue \
            .VENUE_HAS_NO_CLIENT_IDENTIFIER


class TestTheIdentifierIsRecordedBeforeTheSend:
    def _world(self, venue):
        pool = FakePool(session=session_row(), lifecycles=[approved_row()])
        return pool, run(ex.guarded_submit(venue, "CAL-0001", "matt",
                                           store=store, pool=pool))

    def test_a_venue_with_none_still_writes_the_pre_image(self):
        v = RecordingVenue(pre=["V-1", "V-2"])
        pool, got = self._world(v)
        assert got["sent"] is True
        assert got["venueClordId"] is None
        assert pool.attempts[0]["pre_open_order_ids"] == ["V-1", "V-2"]
        assert pool.attempts[0]["venue_clord_id"] is None

    def test_a_venue_with_one_records_it_AND_the_pre_image(self):
        v = RecordingVenue(pre=["V-1"])
        v.mint_client_id = lambda: "1f0c4a2e-0000-4000-8000-000000000001"
        pool, got = self._world(v)
        assert got["venueClordId"] == "1f0c4a2e-0000-4000-8000-000000000001"
        # BOTH. The stronger evidence does not excuse dropping the weaker.
        assert pool.attempts[0]["venue_clord_id"] == got["venueClordId"]
        assert pool.attempts[0]["pre_open_order_ids"] == ["V-1"]

    def test_it_is_durable_before_the_venue_is_called(self):
        order = []
        v = RecordingVenue(pre=[])
        v.mint_client_id = lambda: "c-42"

        pool = FakePool(session=session_row(), lifecycles=[approved_row()])
        real_execute = pool.execute

        async def spy(sql, *a):
            if "venue_clord_id" in sql:
                order.append("recorded")
            return await real_execute(sql, *a)

        pool.execute = spy
        real_submit = v.submit

        def submit_spy(**kw):
            order.append("sent")
            return real_submit(**kw)

        v.submit = submit_spy
        run(ex.guarded_submit(v, "CAL-0001", "matt", store=store, pool=pool))
        assert order == ["recorded", "sent"]

    def test_a_venue_that_cannot_mint_one_sends_nothing(self):
        def boom():
            raise RuntimeError("no key")

        v = RecordingVenue(pre=[])
        v.mint_client_id = boom
        pool, got = self._world(v)
        assert got["sent"] is False
        assert got["reason"].startswith("CLIENT_ID_NOT_MINTED")
        assert v.sends == []
        assert pool.attempts[0]["state"] == "NOT_SENT"

    def test_a_venue_that_mints_rubbish_sends_nothing(self):
        v = RecordingVenue(pre=[])
        v.mint_client_id = lambda: ""
        pool, got = self._world(v)
        assert got["sent"] is False
        assert v.sends == []

    def test_none_leaves_an_already_recorded_identifier_alone(self):
        # COALESCE($3, venue_clord_id). A later write that does not know
        # the identifier must not erase the one that was recorded.
        pool = FakePool(session=session_row(), lifecycles=[approved_row()])
        run(store.claim_send("CAL-0001", "matt", pool=pool))
        att = pool.attempts[0]["attempt_id"]
        run(store.record_pre_image(att, ["V-1"], pool=pool,
                                   venue_clord_id="c-9"))
        assert pool.attempts[0]["venue_clord_id"] == "c-9"
        pool.attempts[0]["state"] = "CLAIMED"
        run(store.record_pre_image(att, ["V-1"], pool=pool))
        assert pool.attempts[0]["venue_clord_id"] == "c-9"

    def test_a_non_string_identifier_is_refused_outright(self):
        pool = FakePool(session=session_row(), lifecycles=[approved_row()])
        run(store.claim_send("CAL-0001", "matt", pool=pool))
        att = pool.attempts[0]["attempt_id"]
        with pytest.raises(ValueError):
            run(store.record_pre_image(att, [], pool=pool,
                                       venue_clord_id=17))


# ── the migration says what the code relies on ───────────────────────

class TestTheMigrationSaysWhatTheCodeRelies_on:
    def test_both_tables_and_the_sessions_row_learn_the_environment(self):
        sql = MIGRATION.read_text()
        for table in ("calibration_sessions", "calibration_lifecycles",
                      "calibration_send_attempts"):
            assert re.search(
                r"ALTER TABLE %s\s+ADD COLUMN IF NOT EXISTS environment" % table,
                sql), table

    def test_the_backfill_is_production_because_that_is_what_exists(self):
        sql = MIGRATION.read_text()
        assert sql.count("DEFAULT 'PRODUCTION'") == 3
        assert "'pmus'" not in sql          # the ticket builder says this
        assert "DEFAULT 'polymarket-us'" in sql

    def test_the_default_venue_the_code_uses_is_the_one_backfilled(self):
        assert ("DEFAULT '%s'" % store.DEFAULT_VENUE) in MIGRATION.read_text()

    def test_a_third_environment_cannot_be_stored(self):
        sql = MIGRATION.read_text()
        assert sql.count(
            "CHECK (environment IN ('PRODUCTION', 'PREPROD'))") == 3

    def test_the_clord_column_is_nullable_and_says_why(self):
        sql = MIGRATION.read_text()
        assert "ADD COLUMN IF NOT EXISTS venue_clord_id TEXT;" in sql
        assert "NOT NULL" not in sql.split("venue_clord_id TEXT")[1][:40]

    def test_there_is_a_rollback_and_it_warns_what_is_lost(self):
        down = ROLLBACK.read_text()
        assert "DROP COLUMN IF EXISTS environment" in down
        assert "DROP COLUMN IF EXISTS venue_clord_id" in down
        low = down.lower()
        assert "unresolved" in low and "reconciliation" in low

    def test_the_one_open_lifecycle_index_is_still_global(self):
        # ON ((1)) -- a constant, so the unique index spans EVERY session
        # and a preprod lifecycle cannot run beside a production one.
        # Migration 067 must not have narrowed it to a per-session key.
        idx = LIFECYCLES.read_text()
        assert re.search(r"CREATE UNIQUE INDEX IF NOT EXISTS\s+"
                         r"calibration_one_open_lifecycle\s+"
                         r"ON calibration_lifecycles \(\(1\)\)", idx)
        # and 067 adds no competing unique index of its own
        assert "CREATE UNIQUE INDEX" not in MIGRATION.read_text()

    def test_the_migration_is_additive(self):
        sql = MIGRATION.read_text().upper()
        for forbidden in ("DROP TABLE", "DROP COLUMN", "DELETE FROM",
                          "TRUNCATE", "ALTER COLUMN"):
            assert forbidden not in sql, forbidden


class TestTheEvidenceCommandDeclaresItsOwnEnvironment:
    def test_it_is_production_because_pmus_has_no_preprod_host(self):
        from sportsassets import calibration_evidence as ev
        assert ev.EVIDENCE_ENVIRONMENT == "PRODUCTION"
        assert ev.EVIDENCE_ENVIRONMENT in cal.ENVIRONMENTS

    def test_the_ticket_it_builds_carries_it(self):
        from sportsassets import calibration_evidence as ev
        src = pathlib.Path(ev.__file__).read_text()
        assert '"environment": EVIDENCE_ENVIRONMENT,' in src

    def test_the_institutional_adapter_still_names_no_production_host(self):
        # Untouched by this change, and re-asserted here because the
        # environment tag only means anything while preprod stays preprod.
        from sportsassets import pmx
        src = pathlib.Path(pmx.__file__).read_text()
        assert pmx.PMX_BASE_URL == "https://api.preprod.polymarketexchange.com"
        assert "api.prod.polymarketexchange.com" not in src
        assert pmx.VENUE == "pmx_preprod"


# ── the preprod runtime's credentials live in the secret store ───────

WORKFLOW = ROOT / ".github/workflows/pmx-preprod.yml"


class TestThePreprodRuntimeTakesNoCredentialFromTheForm:
    def _wf(self):
        import yaml
        d = yaml.safe_load(WORKFLOW.read_text())
        return d, (d.get("on") or d.get(True))

    def test_the_only_inputs_left_are_the_action_the_arg_and_the_confirm(self):
        # A credential passed as a workflow_dispatch input is printed in the
        # step's env block by the runner BEFORE anything can mask it. Runs
        # 1-24 (2026-09-10) carry the client id, participant resource name
        # and key id in their logs because of exactly this.
        _, on = self._wf()
        assert sorted(on["workflow_dispatch"]["inputs"]) == \
            ["action", "arg", "confirm"]

    def test_every_credential_is_read_from_the_secret_store(self):
        src = WORKFLOW.read_text()
        for name in ("PMX_CLIENT_ID", "PMX_PARTICIPANT_ID", "PMX_KEY_ID",
                     "PMX_PRIVATE_KEY_B64"):
            assert "secrets.%s" % name in src, name
        assert "inputs.private_key_b64" not in src
        assert "inputs.client_id" not in src

    def test_a_missing_secret_refuses_before_any_request(self):
        src = WORKFLOW.read_text()
        assert "is not set in the secret store" in src
        assert "PMX_PRIVATE_KEY_B64 is not set" in src

    def test_the_hosts_are_still_preprod_only(self):
        src = WORKFLOW.read_text()
        assert "api.preprod.polymarketexchange.com" in src
        assert "api.prod.polymarketexchange.com" not in src
        assert "pmx-prod" not in src

    def test_the_three_venue_operations_are_served_by_the_tested_module(self):
        # reconcile-order and order-stream USED to be inline heredoc
        # Python, where the search body, the pagination walk and the
        # verdict could not be unit-tested. They now run
        # research/institutional/pmx_preprod_ops.py, which is, and the
        # heredoc refuses them so there is only one implementation.
        d, _ = self._wf()
        names = [s.get("name") or s.get("uses")
                 for s in d["jobs"]["preprod"]["steps"]]
        assert "Run the preprod operation" in names
        assert "actions/checkout@v4" in names      # the module has to exist

        run = [s for s in d["jobs"]["preprod"]["steps"]
               if s.get("name") == "Run the exploratory read"][0]["run"]
        gate = run.split('case "$ACTION" in', 1)[1].split("esac", 1)[0]
        assert "served by the module step" in gate
        script = run.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        for gone in ("reconcile-order", "order-stream"):
            assert 'act == "%s"' % gone not in script, gone

    def test_the_guessed_stream_paths_are_gone(self):
        # Three REST/SSE-shaped paths were guessed at. The real contract
        # is gRPC, and the guesses must not survive anywhere in the file.
        src = WORKFLOW.read_text()
        for guessed in ("/v1/trading/orders/stream", "/v1/stream/orders",
                        "/v1/trading/orders/subscribe"):
            assert guessed not in src, guessed

    def test_the_documented_grpc_contract_is_what_is_used(self):
        src = WORKFLOW.read_text()
        assert "grpc_tools.protoc" in src
        assert "polymarket/v1" in src
        assert "OrderEntryAPIStub" in src
        assert "CreateOrderSubscriptionRequest" in src
        # pinned runtime, not "latest"
        assert 'grpcio==' in src and 'grpcio-tools==' in src \
            and 'protobuf==' in src

    def test_a_missing_generated_client_is_named_not_worked_around(self):
        net = ROOT / "research/institutional/pmx_preprod_net.py"
        src = net.read_text()
        assert "GENERATED_CLIENT_MISSING" in src
        # no hand-rolled stand-in for a protobuf message
        assert "class CreateOrderSubscriptionRequest" not in src

    def test_the_module_steps_pass_the_secrets_not_inputs(self):
        d, _ = self._wf()
        step = [s for s in d["jobs"]["preprod"]["steps"]
                if s.get("name") == "Run the preprod operation"][0]
        env = step["env"]
        for name in ("PMX_CLIENT_ID", "PMX_PARTICIPANT_ID", "PMX_KEY_ID",
                     "PMX_PRIVATE_KEY_B64"):
            assert "secrets.%s" % name in env[name], name

    def test_an_unhandled_exception_is_not_a_named_verdict(self):
        d, _ = self._wf()
        step = [s for s in d["jobs"]["preprod"]["steps"]
                if s.get("name") == "Run the preprod operation"][0]["run"]
        # rc 0 clean, rc 1 a NAMED verdict, anything else fails the step
        assert 'if [ "$RC" -gt 1 ]; then exit "$RC"; fi' in step

    def test_an_empty_result_is_not_read_as_non_submission(self):
        ops = (ROOT / "research/institutional/pmx_preprod_ops.py").read_text()
        assert "NOT proof of non-submission" in ops
        assert "permission to send another order" in ops
        assert "trace package" in ops
        # and the rule is enforced, not merely written down: no failure
        # class may be classified NOT_FOUND
        import sys
        sys.path.insert(0, str(ROOT / "research" / "institutional"))
        import pmx_preprod_ops as O
        for cls in (O.B_HTTP, O.B_MALFORMED, O.B_ROW_SHAPE,
                    O.B_TOKEN_REPEAT, O.B_PAGE_CAP):
            q = [{"query": "orderId", "blockers": ["%s: x" % cls]}]
            assert O.verdict(q, [])["status"] != O.R_NOT_FOUND

    def test_the_receipts_are_retained_and_sanitized(self):
        d, _ = self._wf()
        names = [s.get("name") for s in d["jobs"]["preprod"]["steps"]]
        assert "Publish the receipts" in names
        ops = (ROOT / "research/institutional/pmx_preprod_ops.py").read_text()
        # the writer REFUSES a credential rather than trusting the caller
        assert "_refuse_secrets" in ops
        assert "BEGIN PRIVATE" in ops and "access_token" in ops

    def test_the_duplicate_probe_claims_preview_repeatability_ONLY(self):
        d, _ = self._wf()
        run = [s for s in d["jobs"]["preprod"]["steps"]
               if s.get("name") == "Run the exploratory read"][0]["run"]
        script = run.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        dup = re.split(r"\nelif act ", script.split(
            'elif act == "duplicate-preview":', 1)[1], maxsplit=1)[0]
        assert "PREVIEW_REPEATABILITY" in dup
        for disclaimed in ("insert idempotency", "duplicate-order protection",
                           "single-send boundary"):
            assert disclaimed in dup, disclaimed
        assert "DUPLICATE_INSERT_PROPOSAL.md" in dup

    def test_the_duplicate_insert_test_is_proposed_not_performed(self):
        prop = ROOT / "research/institutional/DUPLICATE_INSERT_PROPOSAL.md"
        text = prop.read_text()
        assert "NOT AUTHORIZED" in text and "NOT PERFORMED" in text
        for section in ("Exact scope", "Maximum dummy exposure",
                        "Observation plan", "Cleanup plan"):
            assert section in text, section
        assert "PREPROD ONLY" in text
        assert "INCONCLUSIVE" in text

    def test_the_trace_package_exists_and_does_not_claim_a_clord_id(self):
        pkg = ROOT / "research/institutional/SUPPORT_TRACE_PACKAGE.md"
        text = pkg.read_text()
        assert "exchange-returned `orderId`" in text
        assert "must not be treated as one" in text
        assert "NOT RECOVERED" in text

    def test_the_stream_run_does_not_claim_market_wide_aggressor(self):
        ops = (ROOT / "research/institutional/pmx_preprod_ops.py").read_text()
        assert "OUR OWN executions" in ops
        assert "full-market aggressor coverage" in ops
        assert "unverified" in ops

    def test_the_embedded_script_still_compiles(self):
        d, _ = self._wf()
        run = [s for s in d["jobs"]["preprod"]["steps"]
               if s.get("name") == "Run the exploratory read"][0]["run"]
        script = run.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        compile(script, "pmx-preprod.run", "exec")



# ── the owning session is DERIVED, never supplied ────────────────────

class TestTheMoneyPathsCannotCrossLedgers:
    """`record_spend` and `book_cash` took the lifecycle by its client order
    id and the session by a separately supplied (or defaulted) id, and
    nothing checked the second owned the first. With one session that was
    redundant; with the institutional preprod lane it is a cross-ledger
    write."""

    def _pool(self, lifecycle_session="PMX-PREPROD-1"):
        pool = FakePool(
            session=session_row(),
            lifecycles=[approved_row(session_id=lifecycle_session,
                                     environment="PREPROD",
                                     venue="pmx_preprod")])
        return pool

    def test_a_preprod_lifecycle_with_a_production_session_is_refused(self):
        pool = self._pool()
        with pytest.raises(ValueError) as exc:
            run(store.book_cash("CAL-0001", 3.00, pool=pool,
                                session_id="MICRO-EXEC-CAL-1"))
        assert store.R_SESSION_NOT_THE_OWNER in str(exc.value)
        assert "PMX-PREPROD-1" in str(exc.value)

    def test_and_neither_ledger_is_touched(self):
        pool = self._pool()
        with pytest.raises(ValueError):
            run(store.book_cash("CAL-0001", 3.00, pool=pool,
                                session_id="MICRO-EXEC-CAL-1"))
        wrote = [s for s, _ in pool.executed if s.strip().upper().startswith("UPDATE")]
        assert wrote == [], wrote

    def test_record_spend_refuses_the_same_crossing(self):
        pool = self._pool()
        with pytest.raises(ValueError) as exc:
            run(store.record_spend("CAL-0001", 1.00, pool=pool,
                                   session_id="MICRO-EXEC-CAL-1"))
        assert store.R_SESSION_NOT_THE_OWNER in str(exc.value)
        assert [s for s, _ in pool.executed
                if s.strip().upper().startswith("UPDATE")] == []

    def test_the_owner_is_derived_when_none_is_supplied(self):
        # The default is None -- derive -- not the production session id.
        import inspect
        for fn in (store.book_cash, store.record_spend):
            assert inspect.signature(fn).parameters["session_id"].default is None

    def test_the_session_written_is_the_lifecycle_s_own(self):
        pool = self._pool()
        pool.session = session_row(session_id="PMX-PREPROD-1",
                                   venue="pmx_preprod", environment="PREPROD")
        run(store.book_cash("CAL-0001", 3.00, pool=pool))
        sess = [(s, a) for s, a in pool.executed
                if "UPDATE calibration_sessions" in s]
        assert sess and sess[-1][1][0] == "PMX-PREPROD-1"

    def test_an_unknown_lifecycle_is_still_named(self):
        pool = FakePool(session=session_row(), lifecycles=[])
        with pytest.raises(ValueError) as exc:
            run(store.book_cash("NOPE", 1.0, pool=pool))
        assert "CALIBRATION_UNKNOWN_LIFECYCLE" in str(exc.value)

    def test_record_spend_says_what_supersedes_it(self):
        assert "book_cash" in store.SUPERSEDED_BY_BOOK_CASH
        assert "idempotent" in store.SUPERSEDED_BY_BOOK_CASH


# ── the runtime destination, matched before anything is written ──────

class TestLabelsDoNotEstablishTheDestination:
    class Bare:
        """Everything `guarded_submit` needs EXCEPT identity(). Not a
        subclass of RecordingVenue: inheriting would bring the method back
        and the test would measure nothing."""

        def __init__(self):
            self.sends, self.reads = [], []

        def open_order_ids(self, market_id):
            self.reads.append(market_id)
            return []

        def submit(self, **kw):
            self.sends.append(kw)
            return {"ok": True, "order_id": "V-1", "status": "open",
                    "filled_shares": 0, "fill_price": None}

    def _venue(self, identity):
        if identity is _MISSING:
            return self.Bare()
        v = RecordingVenue(pre=[])
        v.identity = identity if callable(identity) else (lambda: identity)
        return v

    def _go(self, v, bound_over=None):
        row = approved_row(**(bound_over or {}))
        pool = FakePool(session=session_row(), lifecycles=[row])
        return pool, run(ex.guarded_submit(v, "CAL-0001", "matt",
                                           store=store, pool=pool))

    MATCHING = {"venue": "polymarket-us", "environment": "PRODUCTION",
                "account": "bettortoken-main"}

    def test_a_matching_runtime_sends(self):
        v = self._venue(dict(self.MATCHING))
        pool, got = self._go(v)
        assert got["sent"] is True and len(v.sends) == 1

    def test_a_different_environment_sends_nothing(self):
        v = self._venue({**self.MATCHING, "environment": "PREPROD"})
        pool, got = self._go(v)
        assert got["sent"] is False and v.sends == []
        assert ex.D_MISMATCH in got["reason"] and "environment" in got["reason"]

    def test_a_different_venue_sends_nothing(self):
        v = self._venue({**self.MATCHING, "venue": "pmx_preprod"})
        _, got = self._go(v)
        assert got["sent"] is False and ex.D_MISMATCH in got["reason"]

    def test_a_different_account_sends_nothing(self):
        v = self._venue({**self.MATCHING, "account": "someone-else"})
        _, got = self._go(v)
        assert got["sent"] is False and ex.D_MISMATCH in got["reason"]

    def test_an_adapter_with_no_identity_sends_nothing(self):
        v = self._venue(_MISSING)
        _, got = self._go(v)
        assert got["sent"] is False and ex.D_UNESTABLISHED in got["reason"]

    def test_an_identity_that_raises_sends_nothing(self):
        def boom():
            raise RuntimeError("no config")
        v = self._venue(boom)
        _, got = self._go(v)
        assert got["sent"] is False and ex.D_UNESTABLISHED in got["reason"]

    def test_an_unidentified_account_sends_nothing(self):
        v = self._venue({**self.MATCHING, "account": None,
                         "accountBlocker": "ACCOUNT_NOT_IDENTIFIED"})
        _, got = self._go(v)
        assert got["sent"] is False and ex.D_UNESTABLISHED in got["reason"]

    def test_the_check_happens_BEFORE_the_pre_image_is_read_or_written(self):
        # Nothing about a send that must not occur is written down, and the
        # venue is not even read.
        v = self._venue({**self.MATCHING, "environment": "PREPROD"})
        pool, got = self._go(v)
        assert v.reads == []                      # open_order_ids never called
        assert got["preOpenOrderIds"] is None
        assert pool.attempts[0]["pre_open_order_ids"] is None
        assert pool.attempts[0]["state"] == "NOT_SENT"

    def test_the_retail_adapter_answers_its_own_runtime(self):
        v = adapter.LiveVenue(submit_fn=lambda *a, **k: {},
                              status_fn=lambda *a: None,
                              cancel_fn=lambda *a: {},
                              open_orders_fn=lambda *a, **k: [])
        who = v.identity()
        assert who["venue"] == "polymarket-us"
        assert who["environment"] == "PRODUCTION"
        assert "submitModule" in who


_MISSING = object()


# ── two clocks, kept apart ───────────────────────────────────────────

class TestTheTokenLifetimeComesFromTheVenue:
    def test_the_assertion_window_is_not_the_token_window(self):
        from sportsassets import pmx
        # TOKEN_EXP_S is the CLIENT ASSERTION's own exp. It must not be the
        # thing the access-token cache is keyed on.
        assert pmx.TOKEN_EXP_S == 60
        assert pmx.token_reuse_window(86400) > pmx.TOKEN_EXP_S

    def test_a_stated_lifetime_is_used_with_a_buffer(self):
        from sportsassets import pmx
        w = pmx.token_reuse_window(86400)
        assert w == pytest.approx(86400 * 0.8)
        assert w < 86400 - pmx.TOKEN_SAFETY_FLOOR_S

    def test_a_short_lifetime_is_floored_not_fractioned(self):
        from sportsassets import pmx
        # 60 s: 80% is 48, but the floor says never within 30 s of expiry,
        # so 30 wins. The SMALLER of the two always wins.
        assert pmx.token_reuse_window(60) == pytest.approx(30.0)

    def test_a_lifetime_shorter_than_the_floor_never_reuses(self):
        from sportsassets import pmx
        assert pmx.token_reuse_window(20) == 0.0

    def test_no_stated_lifetime_falls_back_to_the_constant(self):
        from sportsassets import pmx
        assert pmx.token_reuse_window(None) == pmx.TOKEN_REFRESH_S

    def test_expires_in_is_read_only_when_the_body_states_one(self):
        from sportsassets import pmx
        assert pmx._expires_in({"expires_in": 3600}) == 3600.0
        assert pmx._expires_in({"expires_in": "3600"}) == 3600.0
        assert pmx._expires_in({}) is None
        assert pmx._expires_in({"expires_in": 0}) is None
        assert pmx._expires_in({"expires_in": -5}) is None
        assert pmx._expires_in({"expires_in": True}) is None
        assert pmx._expires_in({"expires_in": "soon"}) is None
        assert pmx._expires_in(None) is None

    def test_the_150_second_constant_is_no_longer_the_cache_key(self):
        from sportsassets import pmx
        import inspect
        src = inspect.getsource(pmx._token)
        assert "TOKEN_REFRESH_S" not in src, \
            "the cache must key on the venue's own window, not the constant"
        assert '_tok["window"]' in src

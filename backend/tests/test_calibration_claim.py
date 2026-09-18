"""ONE APPROVAL PERMITS AT MOST ONE SEND ATTEMPT.

The previous handoff took a caller-supplied row and an optional
persistence callable, and it SENT BEFORE IT RECORDED. Two callers with
the same approved ticket both passed the checks and both reached the
venue. A crash between the send and the record left nothing to reconcile
against. `persist=None` meant a send with no record at all.

These tests drive the REAL `calibration_store` against a fake pool whose
INSERT enforces the unique constraint the migration declares, so the
exclusion being tested is the database's, not a Python `if`. The two
interesting cases are the ones management named: two simultaneous callers,
and a restart after an ambiguous send.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from sportsassets import calibration as cal
from sportsassets import calibration_execute as ex
from sportsassets import calibration_store as store


# ── a fake pool with the migration's actual constraints ──────────────

class UniqueViolation(RuntimeError):
    """What asyncpg raises; the name is what the store matches on."""
    def __init__(self, msg="UniqueViolationError: client_order_id"):
        super().__init__(msg)


UniqueViolationError = UniqueViolation


class FakeCon:
    def __init__(self, pool):
        self.pool = pool

    async def fetchrow(self, sql, *a):
        return await self.pool.fetchrow(sql, *a)

    async def execute(self, sql, *a):
        return await self.pool.execute(sql, *a)

    def transaction(self):
        return _Ctx()


class _Ctx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Acquire:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return FakeCon(self.pool)

    async def __aexit__(self, *a):
        return False


class FakePool:
    """Enough asyncpg for the claim path, with the UNIQUE constraint real."""

    def __init__(self, session=None, lifecycles=None, attempts=None,
                 broken=False, on_insert_attempt=None):
        self.session = session if session is not None else session_row()
        self.lifecycles = list(lifecycles if lifecycles is not None
                               else [approved_row()])
        self.attempts = list(attempts or [])
        self.broken = broken
        self.on_insert_attempt = on_insert_attempt
        self.executed = []

    def acquire(self):
        return _Acquire(self)

    async def fetchrow(self, sql, *a):
        if self.broken:
            raise RuntimeError("connection reset")
        if "FROM calibration_sessions" in sql:
            return self.session
        if "FROM calibration_lifecycles" in sql and "client_order_id = $1" in sql:
            for lc in self.lifecycles:
                if lc["client_order_id"] == a[0]:
                    return dict(lc)
            return None
        return None

    async def fetch(self, sql, *a):
        if self.broken:
            raise RuntimeError("connection reset")
        if "FROM calibration_send_attempts" in sql:
            states = set(a[1])
            return [dict(x) for x in self.attempts if x["state"] in states]
        if "FROM calibration_lifecycles" in sql:
            return [dict(x) for x in self.lifecycles]
        return []

    async def execute(self, sql, *a):
        if self.broken:
            raise RuntimeError("connection reset")
        self.executed.append((sql, a))

        if "INSERT INTO calibration_send_attempts" in sql:
            if self.on_insert_attempt:
                self.on_insert_attempt(self, a)
            # THE UNIQUE CONSTRAINT. One attempt per client_order_id, ever.
            if any(x["client_order_id"] == a[2] for x in self.attempts):
                raise UniqueViolation()
            self.attempts.append({
                "attempt_id": a[0], "session_id": a[1], "client_order_id": a[2],
                "claimed_by": a[3], "bound_fields": json.loads(a[4]),
                "state": "CLAIMED", "pre_open_order_ids": None,
                "venue_order_id": None, "outcome": None, "reason": None,
                "claimed_at": "2026-09-18T17:00:00Z"})
            return "INSERT 0 1"

        if "UPDATE calibration_send_attempts" in sql:
            want = None
            if "state = 'CLAIMED'" in sql:
                want = "CLAIMED"
            elif "state = 'PRE_IMAGE_RECORDED'" in sql and "SET state" in sql \
                    and "'SENT_OUTCOME_UNKNOWN'" in sql:
                want = "PRE_IMAGE_RECORDED"
            hit = 0
            for x in self.attempts:
                if x["attempt_id"] != a[0]:
                    continue
                if want and x["state"] != want:
                    continue
                if "'PRE_IMAGE_RECORDED'" in sql and "pre_open_order_ids" in sql:
                    x["pre_open_order_ids"] = json.loads(a[1])
                    x["state"] = "PRE_IMAGE_RECORDED"
                elif "'SENT_OUTCOME_UNKNOWN'" in sql and "sent_at" in sql:
                    x["state"] = "SENT_OUTCOME_UNKNOWN"
                elif "'NOT_SENT'" in sql:
                    if x["state"] not in ("CLAIMED", "PRE_IMAGE_RECORDED"):
                        continue
                    x["state"] = "NOT_SENT"
                    x["outcome"] = "NOT_SENT"
                    x["reason"] = a[1]
                else:                                   # resolve_attempt
                    x["state"] = a[1]
                    x["outcome"] = a[2]
                    x["reason"] = a[3]
                    x["venue_order_id"] = a[4] or x["venue_order_id"]
                hit += 1
            return "UPDATE %d" % hit

        if "UPDATE calibration_lifecycles" in sql:
            for lc in self.lifecycles:
                if lc["client_order_id"] == a[0]:
                    if "cash_booked_usd = GREATEST" in sql:
                        lc["cash_booked_usd"] = max(
                            float(lc.get("cash_booked_usd") or 0), float(a[1]))
                        lc["spent_usd"] = max(float(lc.get("spent_usd") or 0),
                                              float(a[1]))
                    return "UPDATE 1"
            return "UPDATE 0"

        if "UPDATE calibration_sessions" in sql:
            if "SUM(l.cash_booked_usd)" in sql:
                self.session = dict(self.session, spent_usd=sum(
                    float(lc.get("cash_booked_usd") or 0)
                    for lc in self.lifecycles))
            return "UPDATE 1"
        return "UPDATE 1"


def session_row(**over):
    row = {"session_id": "MICRO-EXEC-CAL-1", "experiment": cal.EXPERIMENT,
           "authorised_by": cal.AUTHORISED_BY, "max_all_in_usd": 5.00,
           "max_spend_usd": 100.00, "max_open": 1, "spent_usd": 0.0,
           "stopped": False, "stopped_at": None, "stopped_by": None,
           "stop_reason": None}
    row.update(over)
    return row


TICKET = {"venue": "polymarket-us", "account": "bettortoken-main",
          "marketId": "aec-atp-sin-alc-2026-09-18", "outcome": "SIN to win",
          "outcomeSide": "LONG", "side": "BUY",
          "orderType": "LIMIT_GTC_POST_ONLY", "price": 0.39, "quantity": 11}


def approved_row(**over):
    row = {"client_order_id": "CAL-0001", "venue": "polymarket-us",
           "account": "bettortoken-main",
           "market_id": "aec-atp-sin-alc-2026-09-18", "outcome": "SIN to win",
           "side": "BUY", "order_type": "LIMIT_GTC_POST_ONLY",
           "price": 0.39, "quantity": 11, "entry_fee_usd": 0.20,
           "exit_fee_usd": 0.40, "all_in_usd": 4.89, "reserve_usd": 4.89,
           "inventory_plan": "hold to settlement; no re-entry",
           "approved_by": "matt", "state": cal.APPROVED,
           "venue_order_id": None, "spent_usd": 0.0, "cash_booked_usd": 0.0,
           "quantity_": None, "ticket": json.dumps(TICKET),
           "venue_terminal_state": None, "fills_reconciled": False}
    row.update(over)
    return row


class RecordingVenue:
    """Counts what actually reached the venue."""

    def __init__(self, pre=(), submit=None, raise_on_submit=False,
                 raise_on_pre=False):
        self.pre = list(pre)
        self._submit = submit if submit is not None else {
            "ok": True, "order_id": "V-991", "status": "open",
            "filled_shares": 0, "fill_price": None}
        self.raise_on_submit = raise_on_submit
        self.raise_on_pre = raise_on_pre
        self.sends = []
        self.reads = []

    def open_order_ids(self, market_id):
        self.reads.append(market_id)
        if self.raise_on_pre:
            raise RuntimeError("venue 503")
        return self.pre

    def submit(self, **kw):
        self.sends.append(kw)
        if self.raise_on_submit:
            raise RuntimeError("connection reset")
        return self._submit


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── the claim itself ─────────────────────────────────────────────────

class TestTheClaimIsServerSideAndExclusive:
    def test_a_clean_approval_claims_once_and_binds_the_approved_fields(self):
        pool = FakePool()
        got = run(store.claim_send("CAL-0001", "matt", pool=pool))
        assert got["CLAIMED"] is True
        b = got["bound"]
        for f in store.BOUND_FIELDS:
            assert b[f] not in (None, ""), f
        assert (b["price"], b["quantity"], b["outcomeSide"]) == \
            (0.39, 11, "LONG")
        assert b["approvedBy"] == "matt"

    def test_the_caller_supplies_no_economic_field(self):
        """The ticket is not a parameter. A caller cannot send at a price
        or size no human approved, because it never gets to name one."""
        import inspect
        sig = inspect.signature(store.claim_send)
        assert list(sig.parameters)[:2] == ["client_order_id", "claimed_by"]
        assert not {"price", "quantity", "ticket"} & set(sig.parameters)

    def test_a_second_claim_on_the_same_approval_is_refused(self):
        pool = FakePool()
        first = run(store.claim_send("CAL-0001", "matt", pool=pool))
        second = run(store.claim_send("CAL-0001", "matt", pool=pool))
        assert first["CLAIMED"] is True
        assert second["CLAIMED"] is False
        assert store.R_ALREADY_CLAIMED in second["blockers"]

    @pytest.mark.parametrize("over,blocker", [
        ({"state": cal.SUBMITTED}, store.R_NOT_APPROVED),
        ({"reserve_usd": 0.0}, store.R_NO_RESERVE),
        ({"venue_order_id": "V-1"}, store.R_ALREADY_SENT),
    ])
    def test_the_durable_row_must_actually_permit_the_send(self, over,
                                                           blocker):
        pool = FakePool(lifecycles=[approved_row(**over)])
        got = run(store.claim_send("CAL-0001", "matt", pool=pool))
        assert got["CLAIMED"] is False
        assert blocker in got["blockers"]

    def test_no_row_at_all_refuses(self):
        pool = FakePool(lifecycles=[])
        got = run(store.claim_send("CAL-0001", "matt", pool=pool))
        assert got["blockers"] == [store.R_NO_ROW]

    def test_the_operator_stop_refuses_a_perfect_row(self):
        pool = FakePool(session=session_row(stopped=True))
        got = run(store.claim_send("CAL-0001", "matt", pool=pool))
        assert got["blockers"] == [store.R_STOPPED]

    def test_an_unattributed_claim_is_refused(self):
        for who in (None, "", "   "):
            got = run(store.claim_send("CAL-0001", who, pool=FakePool()))
            assert got["CLAIMED"] is False

    def test_an_unreachable_store_never_returns_claimed(self):
        got = run(store.claim_send("CAL-0001", "matt",
                                   pool=FakePool(broken=True)))
        assert got["CLAIMED"] is False

    def test_an_approved_row_missing_an_economic_field_refuses(self):
        """outcomeSide lives on the approved ticket. A row without it
        cannot be bound, and an unbound field is not sendable."""
        pool = FakePool(lifecycles=[approved_row(
            ticket=json.dumps({k: v for k, v in TICKET.items()
                               if k != "outcomeSide"}))])
        got = run(store.claim_send("CAL-0001", "matt", pool=pool))
        assert got["CLAIMED"] is False
        assert any("APPROVED_FIELDS_INCOMPLETE" in b for b in got["blockers"])


class TestTwoSimultaneousCallers:
    """The case management named. Both callers hold the same approved
    ticket and both pass every in-process check; only the database can
    decide which one sends."""

    def test_only_one_of_two_concurrent_claims_wins(self):
        pool = FakePool()

        async def both():
            return await asyncio.gather(
                store.claim_send("CAL-0001", "caller-a", pool=pool),
                store.claim_send("CAL-0001", "caller-b", pool=pool))
        a, b = run(both())
        assert sorted([a["CLAIMED"], b["CLAIMED"]]) == [False, True]
        loser = a if not a["CLAIMED"] else b
        assert store.R_ALREADY_CLAIMED in loser["blockers"]
        assert len(pool.attempts) == 1

    def test_only_one_of_two_concurrent_callers_reaches_the_venue(self):
        """End to end through guarded_submit: one send, one attempt row."""
        pool = FakePool()
        venue_a, venue_b = RecordingVenue(), RecordingVenue()

        async def both():
            return await asyncio.gather(
                ex.guarded_submit(venue_a, "CAL-0001", "caller-a",
                                  store=store, pool=pool),
                ex.guarded_submit(venue_b, "CAL-0001", "caller-b",
                                  store=store, pool=pool))
        ra, rb = run(both())
        sent = len(venue_a.sends) + len(venue_b.sends)
        assert sent == 1, (venue_a.sends, venue_b.sends)
        assert sorted([ra["sent"], rb["sent"]]) == [False, True]
        refused = ra if not ra["sent"] else rb
        assert refused["outcome"] == ex.NOT_CLAIMED
        assert len(pool.attempts) == 1

    def test_the_loser_never_even_reads_the_venue(self):
        """The refusal happens before the pre-image read, so a blocked
        caller adds no venue load at all."""
        pool = FakePool()
        run(store.claim_send("CAL-0001", "first", pool=pool))
        v = RecordingVenue()
        got = run(ex.guarded_submit(v, "CAL-0001", "second", store=store,
                                    pool=pool))
        assert got["sent"] is False
        assert v.reads == [] and v.sends == []

    def test_a_race_that_only_the_insert_catches_still_refuses(self):
        """Both callers see an empty attempts table -- the in-process
        check cannot separate them -- and the unique index does."""
        seen = {"n": 0}

        def race(pool, _args):
            # the other caller's row lands between the read and the write
            seen["n"] += 1
            if seen["n"] == 1:
                pool.attempts.append({
                    "attempt_id": "ATT-OTHER", "session_id": "S",
                    "client_order_id": "CAL-0001", "claimed_by": "ghost",
                    "bound_fields": {}, "state": "CLAIMED",
                    "pre_open_order_ids": None, "venue_order_id": None,
                    "outcome": None, "reason": None,
                    "claimed_at": "2026-09-18T17:00:00Z"})
        pool = FakePool(on_insert_attempt=race)
        got = run(store.claim_send("CAL-0001", "matt", pool=pool))
        assert got["CLAIMED"] is False
        assert store.R_ALREADY_CLAIMED in got["blockers"]


class TestNothingIsSentWithoutAPlaceToRecordIt:
    def test_no_store_means_no_send(self):
        v = RecordingVenue()
        got = run(ex.guarded_submit(v, "CAL-0001", "matt", store=None))
        assert got["outcome"] == ex.NO_CLAIM_STORE
        assert got["sent"] is False
        assert v.reads == [] and v.sends == []

    def test_an_unwritable_pre_image_means_no_send(self):
        """The claim is spent, but nothing goes to the venue: an
        unrecordable send is one nobody can reconcile."""
        class NoWrite(FakePool):
            async def execute(self, sql, *a):
                if "UPDATE calibration_send_attempts" in sql and \
                        "pre_open_order_ids" in sql:
                    raise RuntimeError("disk full")
                return await FakePool.execute(self, sql, *a)
        pool = NoWrite()
        v = RecordingVenue()
        got = run(ex.guarded_submit(v, "CAL-0001", "matt", store=store,
                                    pool=pool))
        assert got["sent"] is False
        assert v.sends == []
        assert "PRE_IMAGE_NOT_PERSISTED" in got["reason"]

    def test_an_unreadable_pre_image_means_no_send_and_a_spent_claim(self):
        pool = FakePool()
        v = RecordingVenue(raise_on_pre=True)
        got = run(ex.guarded_submit(v, "CAL-0001", "matt", store=store,
                                    pool=pool))
        assert got["sent"] is False and v.sends == []
        assert pool.attempts[0]["state"] == "NOT_SENT"
        # and the approval is used up: no second attempt on this id
        again = run(store.claim_send("CAL-0001", "matt", pool=pool))
        assert again["CLAIMED"] is False

    def test_the_pre_image_is_durable_before_the_send(self):
        pool = FakePool(lifecycles=[approved_row()])
        order = []

        class Watch(RecordingVenue):
            def submit(self, **kw):
                order.append(("send", pool.attempts[0]["state"],
                              pool.attempts[0]["pre_open_order_ids"]))
                return RecordingVenue.submit(self, **kw)
        run(ex.guarded_submit(Watch(pre=["A", "B"]), "CAL-0001", "matt",
                              store=store, pool=pool))
        assert order == [("send", "SENT_OUTCOME_UNKNOWN", ["A", "B"])]


class TestAmbiguitySurvivesACrash:
    """A restart after an ambiguous send. The attempt row was written
    before the network call, so what the venue may be holding is still
    on disk when the process comes back."""

    def test_a_lost_response_leaves_an_unresolved_attempt(self):
        pool = FakePool()
        v = RecordingVenue(raise_on_submit=True)
        got = run(ex.guarded_submit(v, "CAL-0001", "matt", store=store,
                                    pool=pool))
        assert got["outcome"] == ex.AMBIGUOUS and got["sent"] is True
        row = run(store.unresolved_attempt(pool=pool))
        assert row["state"] == "SENT_OUTCOME_UNKNOWN"
        assert row["client_order_id"] == "CAL-0001"

    def test_a_restart_finds_it_and_refuses_to_send_again(self):
        pool = FakePool()
        run(ex.guarded_submit(RecordingVenue(raise_on_submit=True),
                              "CAL-0001", "matt", store=store, pool=pool))

        # --- process dies here; everything in memory is gone ---
        fresh_pool = FakePool(session=pool.session,
                              lifecycles=pool.lifecycles,
                              attempts=pool.attempts)
        outstanding = run(store.unresolved_attempt(pool=fresh_pool))
        assert outstanding is not None
        assert outstanding["pre_open_order_ids"] is not None

        v = RecordingVenue()
        got = run(ex.guarded_submit(v, "CAL-0001", "matt", store=store,
                                    pool=fresh_pool))
        assert got["sent"] is False
        assert v.sends == []

    def test_an_unresolved_attempt_blocks_a_DIFFERENT_approval_too(self):
        """The single-lifecycle limit is spent on the unresolved one."""
        pool = FakePool()
        run(ex.guarded_submit(RecordingVenue(raise_on_submit=True),
                              "CAL-0001", "matt", store=store, pool=pool))
        pool.lifecycles.append(approved_row(client_order_id="CAL-0002"))
        got = run(store.claim_send("CAL-0002", "matt", pool=pool))
        assert got["CLAIMED"] is False
        assert store.R_ALREADY_CLAIMED in got["blockers"]
        assert "spent on it" in got["why"]

    def test_a_clean_send_resolves_and_an_ambiguous_one_does_not(self):
        clean = FakePool()
        run(ex.guarded_submit(RecordingVenue(), "CAL-0001", "matt",
                              store=store, pool=clean))
        assert clean.attempts[0]["state"] == "RESOLVED"
        assert run(store.unresolved_attempt(pool=clean)) is None

        lost = FakePool()
        run(ex.guarded_submit(RecordingVenue(submit={"ok": True}),
                              "CAL-0001", "matt", store=store, pool=lost))
        assert lost.attempts[0]["state"] == "SENT_OUTCOME_UNKNOWN"

    def test_a_named_venue_refusal_resolves_the_attempt(self):
        """The venue said no. Nothing rests, nothing filled, and the
        attempt is closed -- but the approval is still spent."""
        pool = FakePool()
        run(ex.guarded_submit(
            RecordingVenue(submit={"ok": False,
                                   "status": "post_only_rejected"}),
            "CAL-0001", "matt", store=store, pool=pool))
        assert pool.attempts[0]["state"] == "RESOLVED"
        assert run(store.claim_send("CAL-0001", "matt",
                                    pool=pool))["CLAIMED"] is False

    def test_the_outcome_record_failing_leaves_the_block_in_force(self):
        class NoResolve(FakePool):
            async def execute(self, sql, *a):
                if "UPDATE calibration_send_attempts" in sql and \
                        "resolved_at" in sql:
                    raise RuntimeError("disk full")
                return await FakePool.execute(self, sql, *a)
        pool = NoResolve()
        got = run(ex.guarded_submit(RecordingVenue(), "CAL-0001", "matt",
                                    store=store, pool=pool))
        assert got["attemptRecorded"] is False
        assert "OUTCOME WAS NOT RECORDED" in got["note"]
        assert pool.attempts[0]["state"] == "SENT_OUTCOME_UNKNOWN"
        assert run(store.unresolved_attempt(pool=pool)) is not None


class TestTheBoundFieldsAreWhatIsSent:
    def test_the_venue_call_carries_the_approved_values(self):
        pool = FakePool()
        v = RecordingVenue()
        run(ex.guarded_submit(v, "CAL-0001", "matt", store=store, pool=pool))
        sent = v.sends[0]
        assert sent["price"] == 0.39
        assert sent["quantity"] == 11
        assert sent["market_id"] == "aec-atp-sin-alc-2026-09-18"
        assert sent["outcome_side"] == "LONG"
        assert sent["side"] == "BUY"

    def test_the_bound_snapshot_is_returned_for_the_record(self):
        pool = FakePool()
        got = run(ex.guarded_submit(RecordingVenue(), "CAL-0001", "matt",
                                    store=store, pool=pool))
        assert got["boundFields"]["quantity"] == 11
        assert pool.attempts[0]["bound_fields"]["price"] == 0.39


class TestTheHighWaterMarkIsAtomicAndDurable:
    """`record_spend` added a delta the CALLER computed from a prior total
    it was holding. Two processes holding the same prior total book the
    same cash twice -- the book 1333 shape. The high-water mark lives in
    the database now."""

    def test_booking_the_same_total_twice_books_the_cash_once(self):
        pool = FakePool()
        run(store.book_cash("CAL-0001", 4.29, pool=pool))
        first = float(pool.session["spent_usd"])
        run(store.book_cash("CAL-0001", 4.29, pool=pool))
        assert float(pool.session["spent_usd"]) == first == 4.29

    def test_ten_reads_of_one_terminal_status_book_it_once(self):
        pool = FakePool()
        for _ in range(10):
            run(store.book_cash("CAL-0001", 4.29, pool=pool))
        assert float(pool.session["spent_usd"]) == 4.29

    def test_a_rising_total_books_only_the_increase(self):
        pool = FakePool()
        run(store.book_cash("CAL-0001", 2.00, pool=pool))
        run(store.book_cash("CAL-0001", 4.29, pool=pool))
        assert float(pool.session["spent_usd"]) == 4.29
        assert float(pool.lifecycles[0]["cash_booked_usd"]) == 4.29

    def test_a_falling_total_is_refused_not_netted(self):
        pool = FakePool()
        run(store.book_cash("CAL-0001", 4.29, pool=pool))
        with pytest.raises(ValueError) as e:
            run(store.book_cash("CAL-0001", 1.00, pool=pool))
        assert "CASH_TOTAL_FELL" in str(e.value)
        assert float(pool.session["spent_usd"]) == 4.29

    def test_two_concurrent_bookings_of_the_same_total_book_it_once(self):
        pool = FakePool()

        async def both():
            return await asyncio.gather(
                store.book_cash("CAL-0001", 4.29, pool=pool),
                store.book_cash("CAL-0001", 4.29, pool=pool))
        run(both())
        assert float(pool.session["spent_usd"]) == 4.29

    def test_the_caller_supplies_no_prior_total(self):
        import inspect
        sig = inspect.signature(store.book_cash)
        assert "cash_total" in sig.parameters
        assert not {"prior", "booked_usd", "delta"} & set(sig.parameters)

    def test_a_negative_total_is_refused(self):
        with pytest.raises(ValueError):
            run(store.book_cash("CAL-0001", -1.0, pool=FakePool()))

    def test_an_unknown_lifecycle_is_refused(self):
        with pytest.raises(ValueError) as e:
            run(store.book_cash("CAL-NOPE", 1.0, pool=FakePool()))
        assert "UNKNOWN_LIFECYCLE" in str(e.value)


class TestTheMigrationSaysWhatTheCodeRelies_on:
    def test_the_unique_constraint_is_declared(self):
        import pathlib
        sql = pathlib.Path("backend/migrations/"
                           "066_calibration_send_attempts.sql").read_text()
        assert "client_order_id   TEXT NOT NULL UNIQUE" in sql
        assert "cash_booked_usd" in sql
        assert "CHECK (cash_booked_usd >= 0)" in sql

    def test_there_is_a_rollback_and_it_warns_about_the_history(self):
        import pathlib
        down = pathlib.Path(
            "backend/migrations/rollback/"
            "066_calibration_send_attempts.down.sql").read_text()
        assert "DROP TABLE IF EXISTS calibration_send_attempts" in down
        assert "DISCARDS THAT HISTORY" in down

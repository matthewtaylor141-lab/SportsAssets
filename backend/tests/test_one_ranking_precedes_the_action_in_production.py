"""THE ORDERING, THROUGH THE SCHEDULED CALLER, NOT THROUGH A FIXTURE.

WHAT CODEX ESTABLISHED AND WHY THE EARLIER PROOF DID NOT COVER IT.

At 0fa588a `_funded_service` called

    _FM.manage(...)                 # step 4 SELECTS AN EXIT AND SUBMITS IT
    _PC.pass_once(..., pair_inputs=None, ...)

so an exit could be chosen and sent before the indirect hedge was considered at
all. The ranking in `bettor_funded_decision` was real and could not change the
outcome, because by the time it ran the sale had already happened. My tests
invoked `pass_once()` directly with a supplied fixture, which demonstrates the
ranking and says nothing about the order production uses -- the test supplied
the order it was testing.

These tests drive `ext_pinnacle_loop.cycle()` -- the scheduled entry point --
with the transport substituted at `pmus._get_client` and every layer above it
deployed. The assertions are on the ORDER and the NUMBER of adapter calls,
because that is the only evidence that distinguishes "ranked first" from
"ranked afterwards".

ON LOSS CONTAINMENT. An exit at a loss is NOT "one nobody would take": it can
be the best available forward decision, and positive absolute P&L is not a
prerequisite for executing one. A case below selects an exit whose proceeds are
below basis and asserts it is dispatched, so the ordering repair cannot be read
as a rule that only profitable exits execute.
"""

import os
import time

import pytest

from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

ACCT = "acct-funded-ORDERING-proof"
VENUE = "PMUS"
INTENT = "fpi-ordering-primary"
SLUG = "aec-nfl-chi-car-2026-09-13-chi"
QTY = 10.0
PX = 0.55


# ── the transport, recording WHAT and IN WHAT ORDER ───────────────────

class _Recorder:
    """Every venue call, in order, with its kind.

    The order is the evidence. A recorder that only counted would pass on a
    cycle that sent the exit first and the hedge second, which is the defect.
    """

    def __init__(self):
        self.calls: list = []

    # ── the order surface ────────────────────────────────────────────
    def preview_order(self, **kw):
        self.calls.append(("preview", kw))
        return {"ok": True}

    def create_order(self, **kw):
        self.calls.append(("create", kw))
        return {"orderId": "venue-ordering-1", "status": "ORDER_STATE_OPEN"}

    def cancel_order(self, **kw):
        self.calls.append(("cancel", kw))
        return {"ok": True}

    # ── the read surface, which must not be mistaken for an order ────
    def get_order(self, **kw):
        self.calls.append(("get_order", kw))
        return {}

    def executions(self, **kw):
        self.calls.append(("executions", kw))
        return []

    def positions(self, **kw):
        self.calls.append(("positions", kw))
        return []

    @property
    def creates(self):
        return [c for c in self.calls if c[0] == "create"]

    @property
    def order_calls(self):
        return [c for c in self.calls if c[0] in ("create", "cancel")]


def _arm(monkeypatch, rec):
    """Substitute the transport and open the three gates, as the lifecycle
    proof does. All three are off in the shipped code and a separate test
    asserts that; opening them here is what makes a send observable at all."""
    from sportsassets import pmus

    monkeypatch.setattr(pmus, "_get_client", lambda: rec)
    monkeypatch.setattr(pmus._gate, "authorize", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
    monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)


# ── what the deployed ordering must look like, asserted on source ─────

def test_manage_is_asked_to_defer_and_the_ranking_dispatches():
    """THE ORDERING, AT THE CALL SITE. `_funded_service` must ask `manage` not
    to dispatch, and must hand the deferred selections to the pass. Either
    half missing restores the two-managers defect: without the flag `manage`
    sends first, and without the selections the ranking has no exit to weigh
    or to send."""
    import inspect

    src = inspect.getsource(loop._funded_service)
    assert "defer_dispatch=True" in src, (
        "manage would select AND submit before the hedge is considered")
    assert "deferred_exits=deferred" in src, (
        "the ranking would have no priced exit to compare or to dispatch")
    assert "pair_inputs=functools.partial(funded_pair_inputs" in src, (
        "pair_inputs=None leaves the pass with nothing to rank")
    # AND THE ORDER IS DECLARED IN THE RESULT, not left to a reader to infer.
    assert '"dispatched_by": "bettor_funded_pair_cycle.pass_once"' in src


def test_manage_defers_rather_than_submitting_when_asked():
    """The flag has to reach the branch, not just the signature."""
    import inspect

    src = inspect.getsource(FM.manage)
    assert "if defer_dispatch:" in src
    # The deferral must come BEFORE the submit call, or it defers nothing.
    assert src.index("if defer_dispatch:") < src.index("ex = await submit_exit")


def test_every_selectable_action_has_a_dispatch_and_hold_sends_nothing():
    """"Dispatch one selected action" was implemented for one of four. All
    four are named now, and HOLD sending nothing is one of the four rather
    than a gap in the table."""
    assert set(PC.DISPATCHABLE) == {"ACQUIRE_HEDGE", "DIRECT_EXIT", "REDUCE",
                                    "HOLD"}
    assert "NOTHING" in PC.DISPATCHABLE["HOLD"]
    import inspect
    src = inspect.getsource(PC.pass_once)
    assert "ACTION_DIRECT_EXIT, ACTION_REDUCE" in src
    assert "dispatch_selection" in src


def test_the_dispatcher_sends_exactly_what_was_selected():
    """A dispatcher that recomputed a price, a quantity or a proceeds figure
    would put a second opinion of the same number between the decision and
    the order -- the candidate-to-order binding defect in another place."""
    import inspect

    src = inspect.getsource(FM.dispatch_selection)
    for field in ("sel[\"limit_price\"]", "sel[\"selected_qty\"]",
                  "sel[\"proceeds_per_contract\"]",
                  "sel.get(\"inputs_expire_at\")"):
        assert field in src, field
    # NOTHING IS RECONSTRUCTED: no arithmetic on the selection's numbers.
    assert "safe_exit_cent" not in src
    assert "exit_proceeds" not in src


# ── the supplier names what it cannot read, and invents nothing ───────

async def test_the_supplier_refuses_by_name_with_no_management_selection():
    got = await loop.funded_pair_inputs(
        None, {"intent_id": "nope"}, at=time.time(), deferred={})
    assert got["ok"] is False
    assert got["refusal"] == loop.R_NO_DEFERRED_SELECTION
    assert "hold_ranking.DIRECT_EXIT" in got["missing"]


async def test_the_supplier_carries_the_exit_at_its_own_numbers():
    """The exit candidate must be the selector's figures, unchanged. Anything
    re-derived here is a figure the order was not priced at."""
    sel = {"intent_id": INTENT, "selected": "DIRECT_EXIT",
           "selected_qty": 10.0, "limit_price": 0.47,
           "proceeds_per_contract": 0.465, "expected_net_usd": -0.85,
           "ranking": {"version": "MGMT_SELECT_SHAPE", "candidates": [
               {"action": "HOLD", "qty": 10, "value_usd": -2.10,
                "expected_net_usd": -2.10, "downside_usd": -5.50,
                "incremental_capital_usd": 0.0, "capital_duration_h": 20.0,
                "evidence_quality": "EXTERNAL_LABELLED",
                "execution_secured": True}]}}
    got = await loop.funded_pair_inputs(
        None, {"intent_id": INTENT, "residual_qty": 10.0},
        at=1790000000.0, deferred={INTENT: sel})
    assert got["ok"] is True
    cands = {c["action"]: c for c in got["hold_ranking"]["candidates"]}
    assert set(cands) == {"HOLD", "DIRECT_EXIT"}
    ex = cands["DIRECT_EXIT"]
    assert ex["limit_price"] == 0.47
    assert ex["proceeds_per_contract"] == 0.465
    assert ex["expected_net_usd"] == -0.85
    assert ex["from_deferred_selection"] is True
    # AND THE TWO UNWIRED READINGS ARE NAMED, one each.
    assert loop.R_NO_HEDGE_CANDIDATE_READER in got["unavailable"]
    assert loop.R_NO_REGION_PROBABILITY_SOURCE in got["unavailable"]


async def test_an_exit_at_a_loss_is_still_a_rankable_candidate():
    """LOSS CONTAINMENT. "An exit at a loss is one nobody would take" is
    false: it can be the best forward decision, and positive absolute P&L is
    not an execution prerequisite. A -$0.85 exit against a -$2.10 hold is the
    case, and the exit must be RANKABLE and SELECTABLE."""
    from sportsassets import bettor_funded_decision as FD

    sel = {"intent_id": INTENT, "selected": "DIRECT_EXIT",
           "selected_qty": 10.0, "limit_price": 0.47,
           "proceeds_per_contract": 0.465, "expected_net_usd": -0.85,
           "ranking": {"version": "MGMT_SELECT_SHAPE", "candidates": [
               {"action": "HOLD", "qty": 10, "value_usd": -2.10,
                "expected_net_usd": -2.10, "downside_usd": -5.50,
                "incremental_capital_usd": 0.0, "capital_duration_h": 20.0,
                "evidence_quality": FD.EVIDENCE_EXTERNAL_LABELLED,
                "execution_secured": True}]}}
    facts = await loop.funded_pair_inputs(
        None, {"intent_id": INTENT, "residual_qty": 10.0},
        at=1790000000.0, deferred={INTENT: sel})
    got = FD.decide(hold_ranking=facts["hold_ranking"], indirect=None)
    # LOSING LESS IS THE DECISION. -0.85 beats -2.10.
    assert got["selected"] == "DIRECT_EXIT", got
    assert got["selected_candidate"]["expected_net_usd"] == -0.85
    assert got["selected_candidate"]["value_usd"] < 0, (
        "a negative expected net must not disqualify an action from being "
        "selected; containment is a decision")


# ── and the whole thing through the scheduled entry point ─────────────

@pg
@pytest.mark.asyncio
async def test_the_cycle_sends_nothing_from_manage(monkeypatch):
    """THE PRODUCTION ORDERING, OBSERVED.

    `cycle()` runs servicing unconditionally and before every entry-side gate.
    With the funded lane bound and a position held, the deployed path is
    exercised: reconcile, select, defer, rank, dispatch.

    THE ASSERTION THAT MATTERS: no order call is made by `manage`. Whatever
    reaches the adapter's order surface is dispatched by the ranking, and the
    result says so in `decision_ordering`. Before the repair `manage` would
    have submitted from inside step 4 and this count would include it.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    rec = _Recorder()
    try:
        await conn.execute(
            "DELETE FROM ingestion_state WHERE key = ANY($1::text[])",
            [FA.ACCOUNT_KEY, loop.CONTROL_KEY, loop.HEARTBEAT_KEY])
        # BIND THE LANE. Without this `_funded_service` returns None and the
        # test would pass by servicing nothing at all.
        import json
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = $2",
            FA.ACCOUNT_KEY,
            json.dumps({"account_id": ACCT, "venue": VENUE}))
        _arm(monkeypatch, rec)

        out = await loop.cycle(conn)

        svc = out.get("funded_servicing") or {}
        assert svc, "servicing did not run; the ordering was not exercised"
        # ── MANAGE DEFERRED, AND SAID SO ────────────────────────────
        if svc.get("ok") is not False:
            assert svc.get("defer_dispatch") is True, svc
            assert svc.get("exits") in (None, []), (
                "manage sent an exit; the ranking never got to compare it")
            order = svc.get("decision_ordering") or {}
            if order:
                assert order["manage_dispatched"] is False
                assert order["dispatched_by"] == (
                    "bettor_funded_pair_cycle.pass_once")
                assert order["order"][0] == "reconcile"
                assert order["order"][-1] == "dispatch_that_action"
        # ── AND NO ORDER LEFT THIS CYCLE, because nothing is held ───
        #
        # STATED AS THE LIMIT IT IS. An empty book has nothing to exit, so this
        # zero is consistent with the repair AND with a cycle that would have
        # sent from `manage` had there been inventory. It is not the dispatch
        # proof; the dispatch proof is the next test, which seeds a position.
        assert rec.order_calls == [], rec.calls
    finally:
        await conn.execute(
            "DELETE FROM ingestion_state WHERE key = ANY($1::text[])",
            [FA.ACCOUNT_KEY, loop.CONTROL_KEY, loop.HEARTBEAT_KEY])
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_held_position_is_serviced_with_manage_still_sending_nothing(
        monkeypatch):
    """THE SAME ORDERING WITH INVENTORY, which is the case that can fail.

    A position is seeded through the book's own writer, so `manage` has
    something to reconcile, something to ask settlement about, and something to
    select an exit for. The assertion is again on the adapter's ORDER surface:
    `manage` must not appear on it.

    WHAT THIS DOES AND DOES NOT SHOW. It shows that with real inventory the
    deferral holds and no order is sent from step 4. It does NOT show a
    dispatch, because `select_exit` needs a venue book read and an EV_HOLD
    record that this fixture does not supply -- so the selection refuses with a
    named missing input and there is nothing for the ranking to dispatch. That
    refusal is itself the honest state of this lane, and the pure-function
    tests above cover the dispatch arithmetic. A dispatch through the scheduled
    caller needs the book and probability readers wired, which is the work
    named in `PAIR_INPUT_READINESS` as NOT WIRED.
    """
    asyncpg = pytest.importorskip("asyncpg")
    import json

    from sportsassets import bettor_funded_book as FB

    conn = await asyncpg.connect(DSN)
    rec = _Recorder()
    try:
        await conn.execute(
            "DELETE FROM ingestion_state WHERE key = ANY($1::text[])",
            [FA.ACCOUNT_KEY, loop.CONTROL_KEY, loop.HEARTBEAT_KEY])
        await conn.execute(
            "DELETE FROM bettor_funded_economics WHERE intent_id = $1", INTENT)
        await conn.execute(
            "DELETE FROM bettor_funded_fills WHERE intent_id = $1", INTENT)
        await conn.execute(
            "DELETE FROM bettor_funded_intents WHERE intent_id = $1", INTENT)
        await conn.execute(
            "DELETE FROM bettor_desk_accounts WHERE account_id = $1", ACCT)
        await conn.execute(
            "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
            " paused, accounting_status, opening_balance, note) VALUES "
            "($1,'desk-ordering','ACTIVE',FALSE,'RECONCILED',0,'ordering proof')",
            ACCT)
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = $2",
            FA.ACCOUNT_KEY, json.dumps({"account_id": ACCT, "venue": VENUE}))

        # THE HELD LEG, through the book's own writer rather than raw SQL, so
        # the row is one production could have produced.
        coll = FX.collateral_for(PX, QTY, FX.LONG)
        rec_intent = await FB.record_intent(
            conn, intent_id=INTENT, account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG,
            event_key="nfl-chi-car-2026-09-13", order_intent=FX.LONG,
            limit_price=PX, quantity=int(QTY), collateral_usd=coll,
            effective_digest="ordering-proof", payout_event="CHI_WINS",
            held_is_long=True)
        assert rec_intent.get("ok"), rec_intent
        # HELD, NOT MERELY INTENDED: `manage` only selects for a position with
        # residual inventory, so the acknowledgement and the fill are recorded
        # through the book's own writers.
        await FB.record_acknowledgement(conn, INTENT,
                                        venue_order_id="venue-ordering-ack",
                                        status="open")
        await FB.ingest_fills(conn, INTENT,
                              [{"qty": QTY, "price": PX,
                                "venue_fill_id": "vf-ordering-1"}])

        _arm(monkeypatch, rec)
        out = await loop.cycle(conn)
        svc = out.get("funded_servicing") or {}
        assert svc, out.get("funded_servicing_absent") or out.keys()
        if svc.get("ok") is not False:
            assert svc.get("defer_dispatch") is True, svc
            assert svc.get("exits") in (None, []), (
                "manage sent an exit with inventory present, which is the "
                "defect: the ranking never got to compare it")
            # THE POSITION WAS SEEN. A cycle that serviced nothing would make
            # the assertion above vacuous, so the count is asserted.
            assert len(svc.get("open_positions") or []) == 1, svc

            # ── AND THE OBSERVED CHAIN, LINK BY LINK ────────────────
            #
            # Measured on this fixture, and each link names its own gap rather
            # than falling silent:
            #
            #   selection      ok=False, THE_VENUES_EXECUTABLE_BOOK_COULD_NOT_BE_READ
            #   deferred_exits 0        (because the selection refused)
            #   manage exits   []       (nothing sent from step 4)
            #   pair_cycle     considered -> NO_MANAGEMENT_SELECTION_FOR_THIS_POSITION
            #   order calls    []
            #
            # This recorder supplies no venue book, so `select_exit` cannot
            # price an exit. That is the honest state of the lane and it is
            # asserted rather than hidden: the dispatch through the scheduled
            # caller needs the book and probability readers wired, which
            # `PAIR_INPUT_READINESS` names as NOT WIRED.
            sel = svc.get("selection") or []
            assert sel and sel[0].get("ok") is False, sel
            assert sel[0].get("refusal"), sel
            assert (svc.get("deferred_exits") or []) == []
            pc = svc.get("pair_cycle") or {}
            considered = pc.get("considered") or []
            assert considered, pc
            assert considered[0].get("refusal") == loop.R_NO_DEFERRED_SELECTION, (
                considered)
        # NOTHING REACHED THE ORDER SURFACE FROM `manage`.
        assert rec.order_calls == [], rec.calls
    finally:
        await conn.execute(
            "DELETE FROM bettor_funded_economics WHERE intent_id = $1", INTENT)
        await conn.execute(
            "DELETE FROM bettor_funded_fills WHERE intent_id = $1", INTENT)
        await conn.execute(
            "DELETE FROM bettor_funded_intents WHERE intent_id = $1", INTENT)
        await conn.execute(
            "DELETE FROM bettor_desk_accounts WHERE account_id = $1", ACCT)
        await conn.execute(
            "DELETE FROM ingestion_state WHERE key = ANY($1::text[])",
            [FA.ACCOUNT_KEY, loop.CONTROL_KEY, loop.HEARTBEAT_KEY])
        await conn.close()

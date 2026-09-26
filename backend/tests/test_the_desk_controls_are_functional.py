"""THE DESK'S CONTROLS DO WHAT THEIR LABELS SAY, OR REFUSE BY NAME.

WHY THIS FILE EXISTS. An operating panel with inert buttons is a locked
mockup, and a mockup of a kill switch is worse than none: an operator may
believe they have one. So every control here performs its write on the
lane's EXISTING control surface and reports the READBACK, and the tests
below assert the state afterwards rather than the fact that a call
returned.

AND THE ASYMMETRY IS THE POINT. A control that REMOVES authority (pause,
halt, cancel) is available; one that GRANTS it is not implemented here at
all. Funded resume is refused by name, the ACCOUNTING_UNCERTAIN account
cannot be selected, and a recorded limit set is a PROPOSAL that changes no
enforced rail.

THE READ ROLES STAY READ-ONLY. A desk cookie opens every read on this
surface and no write; a write needs a CONTROL-scoped operator session -- the
`bt_control` cookie, minted from the operator password, which opens these
actions and no /api/admin route. The browser never carries the service
credential. That distinction is asserted, because sharing one credential
between "look at the numbers" and "halt the lane" is exactly the mistake a
single gate would make, and `test_the_operator_session_is_scoped.py` pins
the scoping itself.
"""

from __future__ import annotations

import inspect
import json
import os

import pytest
from fastapi import HTTPException

from sportsassets import bettor_demonstration as DEMO
from sportsassets import bettor_desk as dk
from sportsassets import bettor_desk_controls as CTL
from sportsassets import bettor_external_shadow as ext
from sportsassets.api import app as A

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")


# ── the contract, without a database ────────────────────────────────

def test_the_panel_grants_no_authority():
    d = CTL.describe()
    assert d["grants_authority"] == []
    assert set(d["removes_authority"]) == {"pause", "halt",
                                           "cancel-working-orders"}
    assert d["submits_orders"] is False
    assert d["touches_live_orders"] is False
    assert CTL.R_FUNDED_RESUME in d["refused_by_name"]
    assert CTL.R_PAUSED_ACCOUNT in d["refused_by_name"]
    assert d["enforced_rails_live_in"] == "bettor_entry_execution"


def test_the_halt_writes_the_key_the_executor_actually_reads():
    """A halt that wrote a key nobody reads would report a stop that never
    happened. The constant is imported from the executor, not retyped."""
    from sportsassets import live_executor as LE

    assert CTL.LIVE_PAUSE_KEY == LE.PAUSE_KEY


def test_a_jsonb_false_is_not_truthy():
    assert CTL._truthy(json.dumps(False)) is False
    assert CTL._truthy("false") is False
    assert CTL._truthy(None) is False
    assert CTL._truthy(json.dumps(True)) is True


def test_the_cancel_statement_cannot_reach_an_unmodelled_order():
    sql = " ".join(CTL.CANCEL_SQL.split())
    assert "AND is_modelled" in sql, sql
    assert "state = ANY($2::text[])" in sql, sql
    assert "experiment_id = ANY($3::text[])" in sql, sql
    # It cancels; it does not delete, and it does not touch a fill.
    assert "DELETE" not in sql.upper()
    assert "rn1x_fills" not in sql


def test_a_read_credential_cannot_write():
    """A command cookie is refused with a NAMED 403, not a bare 401: an
    operator signed in for reading has to be told what is missing."""
    with pytest.raises(HTTPException) as e:
        A.require_command_control(x_admin_token="", x_desk_token="",
                                  bt_control="", bt_command="a-read-cookie")
    assert e.value.status_code == 403
    assert e.value.detail["reason"] == "CONTROL_REQUIRES_AN_OPERATOR_SESSION"
    assert e.value.detail["reads_still_work"] is True
    assert e.value.detail[
        "no_service_credential_is_needed_in_the_browser"] is True
    with pytest.raises(HTTPException) as e2:
        A.require_command_control(x_admin_token="", x_desk_token="",
                                  bt_control="", bt_command="")
    assert e2.value.status_code == 401


def test_the_desk_page_lives_under_the_cookies_own_path():
    """THE DEFECT THIS PINS. The COMMAND cookie is minted with
    `path=/api/command`, so a page served at `/command/desk` never receives
    it: gated correctly, unreachable in a browser. The page is under the
    cookie's path and the short URL redirects to it."""
    paths = {r.path: r for r in A.app.routes if hasattr(r, "path")}
    assert "/api/command/bettor/desk/page" in paths
    assert "/command/desk" in paths
    import inspect

    src = inspect.getsource(A.bettor_desk_page_redirect)
    assert "/api/command/bettor/desk/page" in src
    # AND THE COOKIE'S PATH IS STILL THAT PREFIX, so the pairing holds.
    sess = inspect.getsource(A.command_session_open)
    assert 'path="/api/command"' in sess


def test_an_unauthenticated_page_request_gets_a_sign_in_form():
    from sportsassets.api.desk_signin import SIGN_IN_HTML

    assert "/api/command/session" in SIGN_IN_HTML
    assert "password" in SIGN_IN_HTML
    # It holds no credential of its own and stores nothing.
    for never in ("localStorage", "sessionStorage", "document.cookie"):
        assert never not in SIGN_IN_HTML, never


def test_the_page_carries_the_control_path_and_the_token_field():
    """The shipped page must actually contain the wiring, not just the
    repository copy of it: the API serves the inlined build."""
    from sportsassets.api.desk_page import DESK_PAGE_HTML as H

    assert "/api/command/bettor/control/" in H
    assert "btn-halt" in H and "btn-pause" in H and "btn-cancel" in H
    assert "optoken" in H
    # AND IT NEVER STORES THE TOKEN. The prose above the field says so;
    # what matters is that the page makes no storage CALL, so the test
    # looks for the accessor rather than the word.
    import re as _re

    assert not _re.search(r"(local|session)Storage\s*[.\[]", H), (
        "the desk page must not touch browser storage at all")
    # THE ACTIVATION BUTTON IS NOT DISABLED ANY MORE, and that is the
    # point: a greyed-out control proves nothing because anybody can POST.
    # It sends, and the SERVER refuses with the unmet prerequisites.
    assert 'id="btn-activate"' in H
    assert 'id="btn-activate" disabled' not in H
    assert "refused by the server" in H


def test_every_action_reports_requested_applied_and_failed_separately():
    """"The button was pressed", "the state changed" and "it did not work"
    are three facts. A single ok flag collapses them, and that is how a
    failed halt gets read as a halt."""
    assert CTL.RESULT_FIELDS == ("requested", "applied", "failed")
    src = inspect.getsource(CTL)
    for fn in ("def pause", "def resume", "def cancel_working_orders",
               "def halt", "def set_limits", "def set_account"):
        body = src.split(fn, 1)[1].split("\nasync def ")[0].split(
            "\ndef ")[0]
        for f in CTL.RESULT_FIELDS:
            assert '"%s"' % f in body, (fn, f)
    # ACTIVATE DELEGATES to the activation module, so the three facts are
    # produced there -- and every return path in it carries all three.
    from sportsassets import bettor_funded_activation as FA

    auth = inspect.getsource(FA.authorize)
    for f in CTL.RESULT_FIELDS:
        assert '"%s"' % f in auth or ("%s=" % f) in auth, f
    # every `return dict(out, ...)` on a refusal path names applied and
    # failed, so a refusal can never read as an applied action
    for chunk in auth.split("return dict(out,")[1:]:
        head = chunk[:400]
        assert "applied" in head and "failed" in head, head[:200]


def test_activation_is_refused_on_the_server_not_by_a_disabled_button():
    """A greyed-out control proves nothing -- anybody can POST. The endpoint
    itself must refuse, with the prerequisites it found unmet."""
    from sportsassets import bettor_funded_activation as FA

    src = inspect.getsource(A.bettor_control_act)
    assert "CTL.activate" in src
    assert "status_code=409" in src
    # THE REFUSAL IS 409 AND THE AUTHORISATION IS 200. A path that answered
    # 409 either way would make a complete authorisation unobservable.
    assert 'if not got.get("ok"):' in src
    assert "activate" in CTL.ACTIONS
    flat = " ".join(inspect.getsource(CTL.activate).split())
    assert "establishes nothing" in flat
    # The readiness refusal itself lives in the activation module now, and
    # the desk's constant is the same string rather than a second one.
    assert CTL.R_NOT_READY == "FUNDED_ACTIVATION_PREREQUISITES_NOT_MET"
    assert FA.R_READINESS_UNMET == CTL.R_NOT_READY
    auth = " ".join(inspect.getsource(FA.authorize).split())
    assert "R_READINESS_UNMET" in auth
    # AND THE SHIPPED PAGE SENDS IT, rather than sitting inert.
    from sportsassets.api.desk_page import DESK_PAGE_HTML as H

    assert "btn-activate" in H and "Request funded activation" in H
    assert "sendControl('activate'" in H


def test_the_page_reads_the_servers_readiness_not_its_own():
    """The screen and the endpoint must not be able to disagree."""
    from sportsassets.api.desk_page import DESK_PAGE_HTML as H

    assert "a.readiness" in H
    assert "ready.checks" in H
    desk = inspect.getsource(A.bettor_desk)
    assert "CTL.readiness" in desk
    assert "unmet_prerequisites" in desk


def test_a_partial_limit_set_is_not_an_approved_limit_set():
    """Pure: the refusal does not need a database to be correct."""
    assert set(CTL.REQUIRED_LIMITS) == {"capital_usd", "per_order_usd",
                                        "max_exposure_usd",
                                        "daily_loss_stop_usd"}


# ── against a real database ─────────────────────────────────────────

#: The canonical registry ids these tests use. The PAUSED one is the
#: accounting-uncertain account: it stays paused, and it is stopped by its
#: ROW, so no rename or relabel reaches past the guard.
_PAUSED_ACCT = "acct-paused-accounting-uncertain"
_CLEAN_ACCT = "acct-pilot-test-001"
_CLOSED_ACCT = "acct-closed-unattributable"


async def _seed_accounts(conn):
    await conn.execute("DELETE FROM bettor_desk_accounts")
    await conn.execute(
        """
        INSERT INTO bettor_desk_accounts
          (account_id, desk_id, status, opening_balance, opened_at, note,
           provenance, paused, pause_reason, accounting_status,
           accounting_detail)
        VALUES
          ($1,'desk-1','ACTIVE',0, now(),'the accounting-uncertain account',
           '{}'::jsonb, TRUE,'accounting recovery defect','UNCERTAIN',
           '{}'::jsonb),
          ($2,'desk-2','ACTIVE',0, now(),'a clean test-venue pilot',
           '{}'::jsonb, FALSE, NULL,'CLEAN','{}'::jsonb),
          ($3,'desk-3','CLOSED_UNATTRIBUTABLE',0, now(),'a closed period',
           '{}'::jsonb, FALSE, NULL,'CLEAN','{}'::jsonb)
        """, _PAUSED_ACCT, _CLEAN_ACCT, _CLOSED_ACCT)


@pg
async def test_every_control_action_takes_and_reads_back():
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        # PAUSE, then RESUME, each confirmed from the row itself.
        got = await CTL.pause(conn, by="test")
        assert got["ok"] is True and got["armed_confirmed"] is False
        assert CTL._truthy(await CTL._read_state(conn, ext.CONTROL_KEY)) \
            is False
        got = await CTL.resume(conn, by="test")
        assert got["ok"] is True and got["armed_confirmed"] is True
        assert got["scope"] == "RESEARCH_SHADOW_ONLY"
        assert got["funded_submission"] == "DISABLED"
        assert CTL._truthy(await CTL._read_state(conn, ext.CONTROL_KEY)) \
            is True

        # LIMITS: a partial set is refused and stores nothing.
        await conn.execute("DELETE FROM ingestion_state WHERE key = $1",
                           CTL.LIMITS_KEY)
        bad = await CTL.set_limits(conn, by="test",
                                   proposed={"capital_usd": 250})
        assert bad["ok"] is False
        assert bad["refusal"] == CTL.R_LIMITS_INCOMPLETE
        assert set(bad["missing"]) == {"per_order_usd", "max_exposure_usd",
                                       "daily_loss_stop_usd"}
        assert await CTL._read_state(conn, CTL.LIMITS_KEY) is None
        # A per-order limit above the capital limit is refused too.
        worse = await CTL.set_limits(conn, by="test", proposed={
            "capital_usd": 100, "per_order_usd": 250,
            "max_exposure_usd": 100, "daily_loss_stop_usd": 50})
        assert worse["ok"] is False, worse
        assert await CTL._read_state(conn, CTL.LIMITS_KEY) is None
        good = await CTL.set_limits(conn, by="test", proposed={
            "capital_usd": 250, "per_order_usd": 25,
            "max_exposure_usd": 100, "daily_loss_stop_usd": 50})
        assert good["ok"] is True
        assert good["changes_an_enforced_limit"] is False
        assert good["activation_still_blocked"] is True
        assert good["stored"]["approved"] is False
        assert good["stored"]["enforced"] is False

        # ACCOUNT: resolved against the CANONICAL REGISTRY, and the paused
        # account is stopped by its ROW -- not by its display name.
        from sportsassets import bettor_funded_activation as FA

        await conn.execute("DELETE FROM ingestion_state WHERE key = $1",
                           CTL.ACCOUNT_KEY)
        await _seed_accounts(conn)
        for blob, want in (
                # a display name alone is not an identity
                ({"name": "PMUS ACCOUNTING_UNCERTAIN", "venue": "PMUS_TEST"},
                 FA.R_NO_ACCOUNT),
                # an id that is in no registry
                ({"account_id": "made-up-1", "venue": "PMUS_TEST"},
                 FA.R_ACCOUNT_UNKNOWN),
                # a venue whose class is not established
                ({"account_id": _CLEAN_ACCT, "venue": "SOMEWHERE"},
                 FA.R_VENUE_UNKNOWN),
                # THE PAUSED ACCOUNT, UNDER A FLATTERING NEW NAME. The row
                # says paused, so the rename changes nothing.
                ({"account_id": _PAUSED_ACCT, "name": "Perfectly Fine Desk",
                  "venue": "PMUS_TEST"}, FA.R_ACCOUNT_PAUSED),
                # and an account whose period is closed
                ({"account_id": _CLOSED_ACCT, "venue": "PMUS_TEST"},
                 FA.R_ACCOUNT_NOT_ACTIVE)):
            ref = await CTL.set_account(conn, by="test", account=blob)
            assert ref["ok"] is False, ref
            assert ref["refusal"] == want, (blob, ref["refusal"])
            assert await CTL._read_state(conn, CTL.ACCOUNT_KEY) is None
        ok = await CTL.set_account(conn, by="test", account={
            "name": "Bettor Pilot One", "venue": "PMUS_TEST",
            "account_id": _CLEAN_ACCT})
        assert ok["ok"] is True and ok["activates_nothing"] is True
        assert ok["stored"]["account_id"] == _CLEAN_ACCT
        assert ok["stored"]["venue_class"] == "TEST"
        assert ok["stored"]["approved"] is False
        # the registry's own fields are recorded beside it, for the audit
        assert ok["stored"]["registry"]["accounting_status"] == "CLEAN"
        assert ok["stored"]["registry"]["paused"] is False

        # REQUESTED / APPLIED / FAILED, on a real action against real rows.
        p1 = await CTL.pause(conn, by="test")
        assert p1["requested"] == {"armed": False}
        assert p1["applied"] == {"armed": False}
        assert p1["failed"] is None
        r1 = await CTL.resume(conn, by="test")
        assert r1["requested"] == {"armed": True}
        assert r1["applied"] == {"armed": True}
        assert r1["failed"] is None

        # ACTIVATION: the limits are recorded but NOT owner-approved, so it
        # stops there and names that, with nothing applied.
        act = await CTL.activate(conn, by="test")
        assert act["ok"] is False
        assert act["applied"] is None
        assert act["failed"]["refusal"] == FA.R_LIMITS_NOT_APPROVED
        assert act["funded_submission"] == "DISABLED"
        assert act["authorises_capital"] is False
        assert act["bound_account"]["account_id"] == _CLEAN_ACCT
        # AND THE READINESS IS DERIVED, not asserted. With the market
        # evidence REMOVED the market checks go to UNKNOWN -- which blocks --
        # rather than quietly defaulting to met. (The evidence is removed
        # here rather than assumed absent, so the assertion does not depend
        # on what another test left behind.)
        await conn.execute("DELETE FROM ingestion_state WHERE key = $1",
                           "ext_pinnacle_last_cycle")
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
        ready = await FA.readiness(conn, account_id=_CLEAN_ACCT)
        assert ready["ready"] is False
        named = {c["check"] for c in ready["checks"]}
        for must in ("funded_submission_disabled", "account_selected_and_clean",
                     "limits_approved_by_the_owner",
                     "limits_recorded_and_complete",
                     "approved_limits_tighten_the_enforced_rails",
                     "venue_book_freshness_basis", "settlement_compatibility",
                     "market_scope_metadata",
                     "an_autonomous_entry_was_admitted_unwaived"):
            assert must in named, (must, sorted(named))
        # an UNKNOWN check is not a met check, and it blocks
        assert ready["unknown_count"] >= 1, [
            (c["check"], c["met"]) for c in ready["checks"]]
        assert all(c["met"] is not True for c in ready["unmet"])
        unknown = {c["check"] for c in ready["checks"] if c["met"] is None}
        assert "venue_book_freshness_basis" in unknown

        # STATE reads the rows, and says so per field.
        st = await CTL.state(conn)
        assert st["research_lane_armed"]["value"] is True
        assert st["account_proposal"]["account_id"] == _CLEAN_ACCT
        assert st["account_proposal"]["name"] == "Bettor Pilot One"
        assert st["limits_proposal"]["proposed"]["capital_usd"] == 250.0
        assert st["funded_submission"] == "DISABLED"
        # THE FIELD SAYS WHAT IT COUNTS. `live_orders` is the funded lane's
        # own history (production holds 166,585 rows from the earlier live
        # beta); what matters here is that THIS lane submitted none.
        assert st["funded_orders_this_lane_submitted"] == 0
        assert "live_orders_rows_all_time" in st
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           [CTL.LIMITS_KEY, CTL.ACCOUNT_KEY])
        await conn.close()


@pg
async def test_the_cancel_control_actually_cancels_a_working_order():
    """A CANCEL THAT CANCELS NOTHING PROVES NOTHING. The lifecycle is run
    just far enough to leave a working modelled order, and the control has
    to move exactly that order to CANCELLED."""
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            "DELETE FROM rn1x_fills WHERE order_id IN (SELECT order_id FROM "
            "rn1x_orders WHERE position_id LIKE $1)", DEMO.EXPERIMENT + "%")
        for t in ("rn1x_orders", "rn1x_decisions", "rn1x_outcomes"):
            await conn.execute(
                "DELETE FROM " + t + " WHERE position_id LIKE $1",
                DEMO.EXPERIMENT + "%")
        await conn.execute(
            "DELETE FROM rn1x_positions WHERE experiment_id = $1",
            DEMO.EXPERIMENT)
        await DEMO.run_entry(conn)
        await DEMO.manage_once(conn, stage="MANAGE_HOLD",
                               at=DEMO.T_FIRST_CYCLE)
        await DEMO.manage_once(conn, stage="REDUCE",
                               at=DEMO.T_FIRST_CYCLE + 600.0)
        open_before = await conn.fetchval(
            "SELECT count(*) FROM rn1x_orders o JOIN rn1x_positions p "
            "ON p.position_id = o.position_id WHERE p.experiment_id = $1 "
            "AND o.state = ANY($2::text[])",
            DEMO.EXPERIMENT, list(dk.OPEN_STATES))
        assert open_before >= 1, (
            "the lifecycle left no working order, so this test would prove "
            "nothing")
        got = await CTL.cancel_working_orders(
            conn, by="test", experiments=[DEMO.EXPERIMENT])
        assert got["cancelled"] == open_before, got
        assert got["funded_orders_cancelled"] == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_orders o JOIN rn1x_positions p "
            "ON p.position_id = o.position_id WHERE p.experiment_id = $1 "
            "AND o.state = ANY($2::text[])",
            DEMO.EXPERIMENT, list(dk.OPEN_STATES)) == 0
        # NO FILL WAS INVENTED OR REMOVED by a cancellation.
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_fills f JOIN rn1x_orders o "
            "ON o.order_id = f.order_id JOIN rn1x_positions p "
            "ON p.position_id = o.position_id WHERE p.experiment_id = $1",
            DEMO.EXPERIMENT) >= 1
    finally:
        await conn.close()


@pg
async def test_the_halt_stops_three_things_and_names_a_partial_one():
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        await CTL.resume(conn, by="test")
        got = await CTL.halt(conn, by="test", reason="a test halt",
                             experiments=[DEMO.EXPERIMENT])
        c = got["components"]
        assert c["research_lane"]["confirmed"] is False
        assert c["funded_executor_paused"]["confirmed"] is True
        assert "working_orders" in c
        # THE OPERATOR STOP MAY FAIL, and then the halt is PARTIAL rather
        # than successful. Whichever happened, the two facts agree.
        assert got["halted"] == got["ok"]
        assert got["partial"] is (not got["ok"])
        if c["operator_stop"].get("taken") is not True:
            assert got["ok"] is False, got
        assert "liquidate inventory" in got["does_not"]
        # AND THE ROWS SAY SO AFTERWARDS, not just the response.
        assert CTL._truthy(await CTL._read_state(conn, ext.CONTROL_KEY)) \
            is False
        assert CTL._truthy(
            await CTL._read_state(conn, CTL.LIVE_PAUSE_KEY)) is True
    finally:
        await conn.close()

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
surface and no write; a write needs the operator token. That distinction is
asserted, because sharing one credential between "look at the numbers" and
"halt the lane" is exactly the mistake a single gate would make.
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
                                  bt_command="a-read-cookie")
    assert e.value.status_code == 403
    assert e.value.detail["reason"] == "CONTROL_REQUIRES_THE_OPERATOR_TOKEN"
    assert e.value.detail["reads_still_work"] is True
    with pytest.raises(HTTPException) as e2:
        A.require_command_control(x_admin_token="", x_desk_token="",
                                  bt_command="")
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
               "def halt", "def set_limits", "def set_account",
               "def activate"):
        body = src.split(fn, 1)[1].split("\nasync def ")[0].split(
            "\ndef ")[0]
        for f in CTL.RESULT_FIELDS:
            assert '"%s"' % f in body, (fn, f)


def test_activation_is_refused_on_the_server_not_by_a_disabled_button():
    """A greyed-out control proves nothing -- anybody can POST. The endpoint
    itself must refuse, with the prerequisites it found unmet."""
    src = inspect.getsource(A.bettor_control_act)
    assert "CTL.activate" in src
    assert "status_code=409" in src
    assert "activate" in CTL.ACTIONS
    flat = " ".join(inspect.getsource(CTL.activate).split())
    assert "R_NOT_READY" in flat
    assert CTL.R_NOT_READY == "FUNDED_ACTIVATION_PREREQUISITES_NOT_MET"
    # The sentence is split across two string literals in the source, so
    # the halves are what is checked.
    assert "disabled button in a page" in flat, flat[:200]
    assert "establishes nothing" in flat
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

        # ACCOUNT: the paused account is refused BY NAME and stores nothing.
        await conn.execute("DELETE FROM ingestion_state WHERE key = $1",
                           CTL.ACCOUNT_KEY)
        for blob in ({"name": "PMUS ACCOUNTING_UNCERTAIN", "venue": "PMUS"},
                     {"name": "desk", "venue": "PMUS",
                      "account_id": "accounting_uncertain-1"}):
            ref = await CTL.set_account(conn, by="test", account=blob)
            assert ref["ok"] is False, ref
            assert ref["refusal"] == CTL.R_PAUSED_ACCOUNT, ref
            assert await CTL._read_state(conn, CTL.ACCOUNT_KEY) is None
        unnamed = await CTL.set_account(conn, by="test", account={})
        assert unnamed["ok"] is False
        assert unnamed["refusal"] == "ACCOUNT_NOT_NAMED"
        ok = await CTL.set_account(conn, by="test", account={
            "name": "Bettor Pilot One", "venue": "PMUS",
            "account_id": "pilot-1"})
        assert ok["ok"] is True and ok["activates_nothing"] is True
        assert ok["stored"]["bound"] is False
        assert ok["stored"]["approved"] is False

        # REQUESTED / APPLIED / FAILED, on a real action against real rows.
        p1 = await CTL.pause(conn, by="test")
        assert p1["requested"] == {"armed": False}
        assert p1["applied"] == {"armed": False}
        assert p1["failed"] is None
        r1 = await CTL.resume(conn, by="test")
        assert r1["requested"] == {"armed": True}
        assert r1["applied"] == {"armed": True}
        assert r1["failed"] is None

        # ACTIVATION IS REFUSED, and every unmet check is named.
        act = await CTL.activate(conn, by="test")
        assert act["ok"] is False
        assert act["applied"] is None
        assert act["failed"]["refusal"] == CTL.R_NOT_READY
        assert act["readiness"]["ready"] is False
        named = {c["check"] for c in act["readiness"]["checks"]}
        for must in ("funded_submission_disabled", "account_named",
                     "limits_approved_by_the_owner",
                     "venue_book_freshness_basis"):
            assert must in named, must
        assert "account_approved_by_the_owner" in act["failed"]["unmet"]
        assert act["funded_submission"] == "DISABLED"

        # STATE reads the rows, and says so per field.
        st = await CTL.state(conn)
        assert st["research_lane_armed"]["value"] is True
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

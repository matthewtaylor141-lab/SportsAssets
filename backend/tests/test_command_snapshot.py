"""COMMAND's read model, checked against the BROWSER'S OWN VALIDATOR.

The recurring defect in this codebase is a rule declared in one place
and unenforced where the work happens. `core.validate` is the rule: it
is what the deployed page actually runs against every payload, and a
snapshot this module builds that fails it is not a snapshot, it is a
red FEED UNAVAILABLE banner. So the central test here does not
re-implement the contract in Python. It runs `frontend/public/command/
core.js` under node, on the real payload, with `{remote: true}` -- the
same options the connected feed uses.

If node is unavailable the contract tests SKIP rather than pass. A
contract check that cannot run is not a contract check that passed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

import pytest

from sportsassets import calibration as cal
from sportsassets.api import command_snapshot as CS

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CORE_JS = os.path.join(REPO, "frontend", "public", "command", "core.js")

NOW = 1789000000.0          # a fixed clock; nothing here depends on today


def _dt(iso):
    import datetime as dt
    return dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc)


def book(**over):
    b = {
        "id": 1407,
        "condition_id": "0xcond-1407",
        "us_market_slug": "aec-atp-sin-alc-2026-09-18",
        "game_key": "atp-sin-alc-2026-09-18",
        "long_asset": "0xtok-sin",
        "intent": "ORDER_INTENT_BUY_LONG",
        "state": "live",
        "frozen_reason": None,
        "ledger_net": 412,
        "avg_cost": 0.41,
        "last_mark": 0.44,
        "realized_pnl": 12.5,
        "open_order_id": None,
        "last_reason": "at his level",
        "opened_at": _dt("2026-09-18T11:02:00Z"),
        "updated_at": _dt("2026-09-18T12:40:00Z"),
        "closed_at": None,
    }
    b.update(over)
    return b


def order(**over):
    o = {
        "id": 8801, "book_id": 1407,
        "us_market_slug": "aec-atp-sin-alc-2026-09-18",
        "kind": "increase", "side": "BUY_LONG", "price": 0.41,
        "state": "open", "venue_state": "OPEN", "quantity": 200,
        "filled": 0.0, "reason": "rest at his level", "order_id": "V-1",
        "placed_at": _dt("2026-09-18T12:39:00Z"),
        "updated_at": _dt("2026-09-18T12:39:05Z"), "done_at": None,
        "condition_id": "0xcond-1407", "game_key": "atp-sin-alc-2026-09-18",
        "long_asset": "0xtok-sin",
    }
    o.update(over)
    return o


def refusal(**over):
    r = {
        "id": 55, "at": _dt("2026-09-18T12:38:00Z"),
        "condition_id": "0xcond-999", "us_slug": "aec-atp-a-b-2026-09-18",
        "refusal": "side_band", "target": 120, "mark": 0.62,
        "his_px": 0.44, "long_asset": "0xtok-a",
    }
    r.update(over)
    return r


def records(**over):
    r = {"books": [book()], "orders": [order()], "refusals": [refusal()],
         "beats": [{"service": "mirror_live", "status": "ok", "detail": {},
                    "beat_at": _dt("2026-09-18T12:40:30Z")}],
         "registered": []}
    r.update(over)
    return r


ACCOUNT = {"cash": 41290.11, "position_value": 18320.40,
           "unrealized": -240.15, "realized_mtd": 1180.62,
           "gross_volume_mtd": 244109.0}


def built(**kw):
    kw.setdefault("records", records())
    kw.setdefault("account", ACCOUNT)
    kw.setdefault("session", cal.empty_session("CAL-1"))
    kw.setdefault("mirror_live", False)
    kw.setdefault("now", NOW)
    return CS.build(**kw)


# ── the browser's own validator ──────────────────────────────────────

# core.js is loaded by EVALUATING ITS SOURCE, not by require(): the
# frontend package.json declares "type": "module", so node treats every
# .js under it as ESM, `module` is undefined there and core.js's own
# CommonJS export line never runs. Evaluating the source in a function
# scope with a `module` of our own is how the browser gets it too (a
# <script> tag, no module system at all), so this runs the same code the
# deployed page runs.
_LOADER = """
const fs = require('fs');
const src = fs.readFileSync(CORE_PATH, 'utf8');
const mod = {exports: {}};
new Function('module', 'exports', 'window', src)(mod, mod.exports, undefined);
const C = mod.exports;
"""


def _run_node(body, argv=()):
    node = shutil.which("node")
    if not node:
        pytest.skip("node unavailable; the browser contract cannot be run")
    script = ("const CORE_PATH=%s;" % json.dumps(CORE_JS)) + _LOADER + body
    out = subprocess.run([node, "-e", script, *argv],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip(), "node produced no output: %s" % out.stderr
    return json.loads(out.stdout)


def _node_validate(payload, audience="INTERNAL", remote=True):
    body = (
        "const p=JSON.parse(process.argv[1]);"
        "const v=C.validate(p,{remote:%s,audience:%s});"
        "process.stdout.write(JSON.stringify(v));"
    ) % ("true" if remote else "false", json.dumps(audience))
    return _run_node(body, [json.dumps(payload)])


def test_the_loader_really_loaded_the_browser_contract():
    """A guard on the guard. If core.js stops exporting `validate`, the
    contract tests below would silently become assertions about an empty
    object -- which is exactly the shape of failure this whole session
    keeps finding."""
    got = _run_node("process.stdout.write(JSON.stringify("
                    "{keys: Object.keys(C).length, "
                    " hasValidate: typeof C.validate === 'function', "
                    " schema: C.SCHEMA}));")
    assert got["hasValidate"] is True
    assert got["schema"] == CS.SCHEMA
    assert got["keys"] > 10


class TestTheBrowserAcceptsIt:
    def test_a_built_snapshot_passes_core_validate_on_the_connected_feed(self):
        v = _node_validate(built())
        assert v["ok"] is True, v["errors"]

    def test_the_investor_projection_passes_as_investor(self):
        v = _node_validate(CS.investor_projection(built()),
                           audience="INVESTOR")
        assert v["ok"] is True, v["errors"]

    def test_a_snapshot_with_unknown_money_still_passes(self):
        """Nulls are the contract's way of saying unavailable. A page
        that only validates when every number is known would push us
        straight back to printing zeros."""
        v = _node_validate(built(account=None, account_error="venue 503"))
        assert v["ok"] is True, v["errors"]

    def test_the_demo_fixture_is_refused_on_the_connected_feed(self):
        """A control: the validator we are trusting really does refuse
        illustrative data, so a pass above means something."""
        v = _run_node("const v=C.validate(C.demoSnapshot(),{remote:true});"
                      "process.stdout.write(JSON.stringify(v));")
        assert v["ok"] is False
        assert any("Demo data refused" in e for e in v["errors"])


class TestUnavailableIsNeverZero:
    def test_an_unread_account_leaves_cash_null_not_zero(self):
        s = built(account=None, account_error="venue 503")
        assert s["account"]["cash"] is None
        assert s["account"]["value"] is None
        assert s["account"]["available"] is None

    def test_available_is_null_when_either_side_is_unknown(self):
        s = built(account={"cash": None, "position_value": 10.0})
        assert s["account"]["available"] is None

    def test_the_unread_account_is_shown_as_a_failed_service_not_a_number(self):
        s = built(account=None, account_error="venue 503")
        names = [x["name"] for x in s["services"]]
        assert "Venue account ledger" in names
        row = [x for x in s["services"] if x["name"] == "Venue account ledger"][0]
        assert row["status"] == "UNAVAILABLE"
        assert "not zero" in row["detail"]

    def test_provenance_lists_what_has_no_source(self):
        s = built(account=None, account_error="x")
        assert "account.cash" in s["provenance"]["unavailable"]
        assert any("history" in u for u in s["provenance"]["unavailable"])

    def test_an_out_of_range_mark_becomes_null_not_a_clamped_price(self):
        s = built(records=records(books=[book(last_mark=1.4)]))
        p = s["positions"][0]
        assert p["mark"] is None and p["value"] is None


class TestAFailedRetrievalIsNotAnEmptyBook:
    @pytest.mark.asyncio
    async def test_no_pool_refuses_by_name(self):
        with pytest.raises(CS.RetrievalIncomplete) as e:
            await CS.read_records(None)
        assert e.value.reason == "NO_DATABASE_POOL"

    @pytest.mark.asyncio
    async def test_a_raising_read_refuses_instead_of_returning_nothing(self):
        class Boom:
            async def fetch(self, *a):
                raise RuntimeError("connection reset")
        with pytest.raises(CS.RetrievalIncomplete) as e:
            await CS.read_records(Boom())
        assert e.value.reason == "LEDGER_READ_FAILED"
        assert "mirror_books" in e.value.detail


class TestOwnershipIsDeclaredNeverInferred:
    def test_a_large_book_is_not_reclassified_as_a_hand_position(self):
        """`pmus_account._scorecard` calls anything over $5 manual. That
        heuristic must never reach this read model, or a mirror book
        that grew would silently become the owner's own trade and its
        performance would be blended into his."""
        big = book(ledger_net=99999, avg_cost=0.99)
        s = built(records=records(books=[big]))
        assert s["positions"][0]["lane"] == CS.LANE_AUTONOMOUS

    def test_an_operator_registration_is_what_makes_a_row_manual(self):
        s = built(records=records(registered=[{
            "us_market_slug": "aec-atp-sin-alc-2026-09-18",
            "asset": "0xtok-sin", "shares": 412, "side": "LONG",
            "note": "owner keeps it", "registered_by": "matt"}]))
        assert s["positions"][0]["lane"] == CS.LANE_MANUAL

    def test_the_lanes_are_declared_in_the_payload(self):
        s = built()
        assert set(s["lanes"]["declared"]) == set(CS.LANES)
        assert "never inferred from trade size" in s["lanes"][
            "ownershipIsDeclared"]

    def test_source_is_never_illustrative(self):
        s = built()
        assert s["source"] == CS.SOURCE_NATIVE
        for row in s["positions"] + s["orders"] + s["decisions"]:
            assert row["evidence"] != "ILLUSTRATIVE"


class TestTheExitLifecycleIsFourFacts:
    @pytest.mark.parametrize("over,stage", [
        ({"state": "closing"}, CS.EXIT_CONSIDERED),
        ({"state": "closing", "open_order_id": 900}, CS.EXIT_SUBMITTED),
        ({"state": "closing", "ledger_net": 0}, CS.EXIT_FILLED),
        ({"state": "closed", "closed_at": _dt("2026-09-18T12:00:00Z")},
         CS.POSITION_RECONCILED),
    ])
    def test_each_stage_is_distinguishable(self, over, stage):
        assert CS._exit_stage(book(**over)) == stage

    def test_a_considered_exit_is_not_reported_as_a_sale(self):
        assert CS._exit_stage(book(state="closing")) != CS.EXIT_FILLED


class TestOrderStates:
    @pytest.mark.parametrize("state,filled,want", [
        ("open", 0, "RESTING"),
        ("open", 40, "PARTIAL"),
        ("filled", 200, "FILLED"),
        ("cancelled", 0, "CANCELLED"),
        ("cancelled", 30, "CANCELLED_PARTIAL"),
        ("rejected", 0, "REJECTED"),
        ("unknown", 0, "UNRESOLVED"),
        ("lost", 0, "UNRESOLVED"),
        ("placing", 0, "SUBMITTING"),
    ])
    def test_each_state_keeps_its_own_word(self, state, filled, want):
        assert CS.order_status(state, filled, 200) == want

    def test_an_unresolved_order_is_not_collapsed_into_cancelled(self):
        """The row that can still surprise the ledger must stay visible."""
        assert CS.order_status("unknown", 0, 10) != "CANCELLED"
        assert CS.order_status("lost", 0, 10) != "CANCELLED"

    def test_an_overfill_is_named_rather_than_silently_clamped(self):
        row = CS.order_row(order(state="filled", filled=260, quantity=200))
        assert row["filled"] == 200          # the contract refuses > qty...
        assert row["reason"].startswith("OVERFILL venue 260")   # ...but it says so


class TestIdentity:
    def test_two_different_contents_never_share_a_sequence(self):
        a = built(now=NOW)
        b = built(records=records(books=[book(ledger_net=500)]), now=NOW + 1)
        assert a["sequence"] != b["sequence"]
        assert a["snapshotId"] != b["snapshotId"]

    def test_identical_content_at_the_same_instant_is_identical(self):
        a, b = built(now=NOW), built(now=NOW)
        assert a == b

    def test_the_sequence_rises_with_the_clock(self):
        assert built(now=NOW)["sequence"] < built(now=NOW + 0.5)["sequence"]

    def test_the_as_of_is_utc_with_a_z(self):
        assert built()["asOf"].endswith("Z")


class TestTheInvestorProjectionIsMadeOnTheServer:
    def test_no_internal_record_survives(self):
        inv = CS.investor_projection(built())
        assert inv["positions"] == [] and inv["orders"] == []
        assert inv["decisions"] == [] and inv["models"] == []
        assert inv["audience"] == "INVESTOR"

    def test_allocation_still_describes_the_book(self):
        inv = CS.investor_projection(built())
        assert inv["allocation"] and inv["allocation"][0]["value"] > 0


class TestTheCalibrationBudgetIsCarried:
    def test_the_three_numbers_are_in_the_payload(self):
        s = built()
        assert s["calibration"]["spent"] == 0.0
        assert s["calibration"]["reserved"] == 0.0
        assert s["calibration"]["remaining"] == 100.0

    def test_a_reserve_shows_as_reserved_capital_on_the_account(self):
        sess = cal.empty_session("CAL-1")
        sess["lifecycles"].append({"clientOrderId": "x", "reserve": 4.60,
                                   "state": cal.SUBMITTED})
        s = built(session=sess)
        assert s["account"]["reserved"] == 4.60
        assert s["account"]["available"] == round(41290.11 - 4.60, 2)

    def test_command_never_claims_order_authority(self):
        s = built()
        gate = [g for g in s["gates"]
                if g["name"] == "Order submission from COMMAND"][0]
        assert gate["status"] == "BLOCKED"
        assert s["engine"]["autoPromotion"] is False

    def test_the_deployed_reconciliation_gate_is_pending_until_proved(self):
        s = built()
        g = [x for x in s["gates"]
             if x["name"] == "Deployed browser reconciliation"][0]
        assert g["status"] == "PENDING"


class TestTheSleeveModeComesFromTheWorkersOwnRow:
    @pytest.mark.parametrize("value,live", [
        (True, True), ("true", True),
        (False, False), ("false", False), (None, False),
        ("on", False), (1, False), ("1", False),
    ])
    def test_only_an_unambiguous_true_reads_as_live(self, value, live):
        """An ambiguous arming switch reads OFF. `mirror_live=false` is a
        standing owner order; a dashboard that renders "on" or 1 as LIVE
        would report the sleeve armed while it is paused."""
        assert CS.mirror_live_of([{"value": value}]) is live

    def test_a_missing_row_is_not_live(self):
        assert CS.mirror_live_of([]) is False

    def test_the_gate_reports_the_row_it_read(self):
        s = built(records=records(mirrorLive=False), mirror_live=None)
        g = [x for x in s["gates"]
             if x["name"] == "Autonomous strategy admission"][0]
        assert g["status"] == "BLOCKED" and "false" in g["detail"]
        assert s["engine"]["mode"] == "EXITS_ONLY"


# ── the route, not just the builder ──────────────────────────────────
# Access control is a claim about the ENDPOINT. Testing the read model
# and asserting the route is protected is the same mistake as testing
# run_census and assuming the workflow called it.

class TestAccessIsEnforcedServerSide:
    @staticmethod
    def _client():
        from fastapi.testclient import TestClient
        from sportsassets.api import app as app_mod
        return TestClient(app_mod.app), app_mod

    def test_the_snapshot_refuses_an_anonymous_reader(self):
        client, _ = self._client()
        for path in ("/api/command/snapshot",
                     "/api/command/investor/snapshot"):
            assert client.get(path).status_code == 401, path

    def test_a_forged_cookie_is_refused(self):
        client, app_mod = self._client()
        r = client.get("/api/command/snapshot",
                       cookies={app_mod.COMMAND_COOKIE: "9999999999.deadbeef"})
        assert r.status_code == 401

    def test_the_wrong_password_mints_nothing(self, monkeypatch):
        client, app_mod = self._client()
        r = client.post("/api/command/session", json={"password": "nope"})
        assert r.json()["ok"] is False
        assert app_mod.COMMAND_COOKIE not in r.cookies

    def test_the_right_password_sets_an_httponly_cookie_and_returns_no_token(
            self, monkeypatch):
        client, app_mod = self._client()
        monkeypatch.setattr(app_mod, "settings", lambda: _FakeSettings())
        r = client.post("/api/command/session", json={"password": "letmein"})
        body = r.json()
        assert body["ok"] is True
        # THE TOKEN IS NEVER IN THE BODY. It exists only as a cookie the
        # page cannot read.
        assert "token" not in body
        raw = r.headers.get("set-cookie", "")
        assert app_mod.COMMAND_COOKIE in raw
        assert "HttpOnly" in raw and "Secure" in raw
        assert "Path=/api/command" in raw

    def test_the_cookie_is_scoped_so_it_is_not_sent_to_other_endpoints(self):
        """Path=/api/command means the browser attaches it to COMMAND's
        reads and to nothing else -- not to the desk, not to admin."""
        _, app_mod = self._client()
        import inspect
        src = inspect.getsource(app_mod.command_session_open)
        assert 'path="/api/command"' in src
        assert "httponly=True" in src and "secure=True" in src


class _FakeSettings:
    desk_password = "letmein"
    admin_token = "admin-token-for-tests"

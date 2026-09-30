"""CONTRACT: THE COMMAND CENTRE PAPER PANELS AGAINST THE REAL PAPER MODULES.

The paper panels on the Derek, Xavier and Audrey pages read
/api/command/paper/*, which the paper-session change serves
(api/command_paper.py, bettor_paper_ledger.py, bettor_paper_readmodel.py,
bettor_paper_session.py). THE SERVER IS AUTHORITATIVE. The page proofs in
test_command_centre_agent_pages.py render fixtures written in those modules'
shapes; this file rebuilds every one of those shapes FROM THE REAL MODULES --
account_payload, balances() with and without a mark, entry_view(), the SSE
frames of stream_events() (snapshot, ledger replay by sequence, heartbeat,
unavailable), and the session, Derek, Xavier and Audrey payloads -- over an
asyncpg-shaped stand-in that answers only the modules' own SQL, and then
runs the pages' own JavaScript on the real output.

It SKIPS, by name, only in a build without the paper modules (the page
branch on its own). Once they are merged it runs, and any drift between the
server's shapes and the fixtures the pages are proven against fails here;
at that point it belongs in tools/capital_critical_tests.txt.
"""
from __future__ import annotations

import json

import pytest

from tests.test_command_centre_agent_pages import (  # fixtures and helpers, no tests
    ACCOUNT_ID, ACCOUNT_NO_SCHEMA, AUDREY_PAYLOAD, BAL_MARKED, BAL_UNMARKED, CCP, DECISION,
    DEREK_PAYLOAD, DRAWDOWN, E1, E2, E3, E4, ENABLED, GROUP, HEALTH, LABELS, DATA_LABEL, MARK_STALE,
    ORDER_ID, POSITION, PSLUG, SESSION, SESSION_PAYLOAD, SESSION_ROW, T0, UNAV_SCHEMA,
    XAVIER_PAYLOAD, _account, _frames, _j, _node)


def _paper_modules():
    try:
        from sportsassets import bettor_paper_ledger as L
        from sportsassets import bettor_paper_readmodel as RM
        from sportsassets import bettor_paper_session as S
        from sportsassets.api import command_paper as CP
    except ImportError as exc:
        pytest.skip("the paper-session modules are not in this build (%s): the page proofs' "
                    "fixtures are written in their shapes; this check runs once they are merged" % exc)
    return L, RM, S, CP


class _PaperConn:
    """asyncpg-shaped stand-in answering exactly the reads the paper read
    models make (the SQL is theirs); any other statement fails the test."""

    def __init__(self, *, schema=True, ledger=(), fills=(), session=True, control=True):
        import datetime as _dt
        from decimal import Decimal as Dm
        self.D, self.dt = Dm, _dt
        self.schema = schema
        self.rows = [self._ledger_row(e) for e in ledger]
        self.fills = list(fills)
        self.session = session
        self.control = control

    def ts(self, e):
        return self.dt.datetime.fromtimestamp(e, self.dt.timezone.utc)

    def _ledger_row(self, e):
        r = {"seq": e["sequence"]}
        for k in ("kind", "idempotency_key", "account_id", "session_id", "order_id", "fill_id",
                  "group_id", "position_key", "settlement_key", "corrects_seq", "event_source",
                  "simulator_version", "data_label"):
            r[k] = e[k]
        for k in ("cash_delta_usd", "reserved_delta_usd", "cash_after_usd", "reserved_after_usd"):
            r[k] = self.D(str(e[k]))
        r["detail"] = json.dumps(e["detail"])
        r["committed_at"] = self.ts(e["committed_at"])
        return r

    async def fetchval(self, sql, *a):
        if "to_regclass" in sql:
            return self.schema
        if "sum(fee_usd)" in sql:
            return sum((self.D(str(f["fee_usd"])) for f in self.fills), self.D(0))
        if "max(committed_at)" in sql:
            return max((r["committed_at"] for r in self.rows), default=None)
        raise AssertionError("unexpected fetchval: " + sql)

    async def fetchrow(self, sql, *a):
        if "FROM paper_accounts" in sql:
            return {"account_id": a[0], "account_key": "BETTORTOKEN_PAPER_MAIN",
                    "starting_cash_usd": self.D("500000")} if self.rows else None
        if "coalesce(sum(cash_delta_usd)" in sql:
            return {"cash": sum((r["cash_delta_usd"] for r in self.rows), self.D(0)),
                    "reserved": sum((r["reserved_delta_usd"] for r in self.rows), self.D(0)),
                    "last_seq": max((r["seq"] for r in self.rows), default=None), "n": len(self.rows)}
        if "FROM paper_ledger WHERE account_id = $1 ORDER BY seq DESC LIMIT 1" in sql:
            return max(self.rows, key=lambda r: r["seq"]) if self.rows else None
        if "FROM paper_control" in sql:
            return {"enabled": True, "why": "owner", "updated_by": "owner",
                    "updated_at": self.ts(T0 - 86400)} if self.control else None
        if "FROM paper_sessions" in sql:
            return {"session_id": SESSION_ROW["session_id"], "account_id": ACCOUNT_ID,
                    "started_at": self.ts(T0 - 23400), "config": json.dumps({"reporting_tz": "America/New_York"}),
                    "config_sha": "a" * 64, "simulator_version": "PAPER_SIM_V1",
                    "reporting_tz": "America/New_York", "status": "ACTIVE"} if self.session else None
        if "FROM paper_session_health" in sql:
            return {"heartbeat_at": self.ts(T0 + 55), "passes": 41, "errors": 0, "mutation_attempts": 0,
                    "last_mutation_attempt": None, "last_pass": "{}", "last_error": None,
                    "recent_heartbeats": "[]"}
        raise AssertionError("unexpected fetchrow: " + sql)

    async def fetch(self, sql, *a):
        if "WITH f AS" in sql:
            if not self.fills:
                return []
            f = self.fills[0]
            return [{"group_id": GROUP, "us_market_slug": PSLUG, "holding_side": "LONG", "fixture": "NYY @ BOS",
                     "label": "{}", "bought": self.D(str(f["qty"])), "buy_gross": self.D(str(f["gross_usd"])),
                     "buy_fees": self.D(str(f["fee_usd"])), "sold": None, "sale_gross": None, "sale_fees": None,
                     "first_fill_at": self.ts(T0 + 3), "last_fill_at": self.ts(T0 + 3), "settled_qty": None,
                     "payout_usd": None, "settled": None, "settlement_version": None, "settled_at": None}]
        if "FROM paper_book_observations" in sql:
            return []                                    # no observed book: the mark is UNAVAILABLE
        if "FROM paper_ledger WHERE account_id = $1 AND seq > $2" in sql:
            return sorted([r for r in self.rows if r["seq"] > a[1]], key=lambda r: r["seq"])[:a[2]]
        if "FROM paper_ledger WHERE account_id = $1" in sql and "ORDER BY seq DESC LIMIT $2" in sql:
            return sorted(self.rows, key=lambda r: -r["seq"])[:a[1]]
        if "FROM paper_equity_snapshots" in sql:
            return [{"at": self.ts(T0 - 3600), "equity_usd": self.D("500120")},
                    {"at": self.ts(T0 - 1800), "equity_usd": None},
                    {"at": self.ts(T0), "equity_usd": self.D("500015.8")}]
        if "FROM paper_decisions" in sql and "count(*)" in sql:
            return [{"verdict": "ENTER", "reason": "ENTER", "n": 1}]
        if "FROM paper_decisions" in sql:
            return [dict(DECISION, decided_at=self.ts(T0 + 1), label="{}", pinnacle="{}", book="{}",
                         economics="{}", refusals="[]", qualification_gaps="[]", alternatives="[]",
                         p_internal=self.D("0.561"), p_pinnacle=self.D("0.548"), p_blended=self.D("0.556"),
                         proposed_qty=self.D("1000"), limit_price=self.D("0.5"))]
        if "FROM paper_orders" in sql and "'ENTRY'" in sql:
            return [{"order_id": ORDER_ID, "account_id": ACCOUNT_ID, "group_id": GROUP, "role": "ENTRY",
                     "direction": "BUY", "holding_side": "LONG", "intent": "ORDER_INTENT_BUY_LONG",
                     "us_market_slug": PSLUG, "fixture": "NYY @ BOS", "label": "{}", "qty": self.D("1000"),
                     "filled_qty": self.D("800"), "limit_price": self.D("0.5"), "reserved_usd": self.D("500.5"),
                     "reserved_remaining_usd": self.D("0"), "state": "CANCELED", "queue_basis": None,
                     "created_at": self.ts(T0 + 2), "expires_at": self.ts(T0 + 12)}]
        if any(t in sql for t in ("FROM paper_orders", "FROM paper_fills", "FROM paper_handoffs",
                                  "FROM paper_xavier_reviews", "FROM paper_audrey_reports",
                                  "FROM paper_audrey_findings")):
            return []
        raise AssertionError("unexpected fetch: " + sql)


FILL = {"qty": 800.0, "gross_usd": 400.0, "fee_usd": 0.2}


def _sse_frames(chunks):
    out = []
    for c in chunks:
        ev = {"id": None}
        for line in c.strip().splitlines():
            k, _, v = line.partition(": ")
            ev[k] = v
        ev["data"] = json.loads(ev["data"])
        out.append(ev)
    return out


async def _real_contract(monkeypatch):
    import contextlib
    L, RM, S, CP = _paper_modules()
    monkeypatch.setenv(S.ENV_FLAG, "on")
    conn = _PaperConn(ledger=[E1, E2, E3, E4], fills=[FILL])

    @contextlib.asynccontextmanager
    async def acquire():
        yield conn

    async def nosleep(_):
        return None

    async def frames(**kw):
        return _sse_frames([c async for c in CP.stream_events(acquire, sleep=nosleep, max_polls=1,
                                                               heartbeat_s=0.0, **kw)])
    mark = {PSLUG: {"LONG": MARK_STALE}}
    got = {
        "account": await CP.account_payload(conn, entries=50, now=T0 + 60),
        "no_schema": await CP.account_payload(_PaperConn(schema=False), now=T0),
        "marked": await L.balances(conn, ACCOUNT_ID, now=T0 + 60, marks=mark),
        "open": await frames(last_event_id=None),
        "resume": await frames(last_event_id=2),
        "gone": _sse_frames([c async for c in CP.stream_events(
            _acq(_PaperConn(schema=False)), last_event_id=None, sleep=nosleep, max_polls=1)]),
        "session": await RM.session_payload(conn, now=T0 + 60),
        "derek": await RM.derek_payload(conn, now=T0 + 60),
        "xavier": await RM.xavier_payload(conn, now=T0 + 60),
        "audrey": await RM.audrey_payload(conn, now=T0 + 60),
        "labels": CP._labels(), "schema": CP._unavailable_schema(),
        "entry": L.entry_view(conn.rows[2]), "kinds": L.KINDS,
    }
    return json.loads(json.dumps(got, default=str)), L


def _acq(conn):
    import contextlib

    @contextlib.asynccontextmanager
    async def acquire():
        yield conn
    return acquire


async def test_the_fixtures_are_the_real_paper_modules_shapes(monkeypatch):
    real, L = await _real_contract(monkeypatch)
    acct = real["account"]
    assert set(acct) == set(_account()) and set(real["no_schema"]) == set(ACCOUNT_NO_SCHEMA)
    assert real["no_schema"]["account"] == UNAV_SCHEMA == real["schema"]
    assert acct["account"]["status"] == "OK" and acct["ledger"]["status"] == "OK"
    assert set(acct["account"]["data"]) == set(BAL_UNMARKED) == set(real["marked"])
    assert set(acct["account"]["data"]["open_positions"][0]) == set(POSITION)
    assert set(real["marked"]["open_positions"][0]["mark"]) == set(MARK_STALE)
    assert set(acct["session"]) == set(SESSION) and set(acct["drawdown"]["data"]) == set(DRAWDOWN)
    for e in acct["ledger"]["data"]:
        assert set(e) == set(E1), e
    assert real["entry"] == json.loads(json.dumps(E3))        # entry_view() of the same row, value for value
    assert tuple(real["kinds"]) == CCP.LEDGER_KINDS
    assert real["labels"] == {"data_label": DATA_LABEL, "labels": LABELS}
    # the same figures as the fixture, from the one derived-figures function
    for k in ("cash_usd", "reserved_usd", "available_usd", "open_position_value_usd", "total_equity_usd",
              "realized_pnl_usd", "unrealized_pnl_usd", "marks_complete", "unmarked_positions",
              "equity_basis", "last_sequence", "equity_excluding_unmarked_usd", "ledger_consistent"):
        assert acct["account"]["data"][k] == BAL_UNMARKED[k], k
    for k in ("open_position_value_usd", "total_equity_usd", "unrealized_pnl_usd", "stale_marks",
              "marks_complete", "equity_basis"):
        assert real["marked"][k] == BAL_MARKED[k], k
    # the stream: event names, ids and every frame's keys
    fr = _frames()
    op = real["open"]
    assert [f["event"] for f in op] == ["snapshot", "heartbeat"] and op[0]["id"] == "4" and op[1]["id"] is None
    assert set(op[0]["data"]) == set(fr["snapshot"]) and set(op[1]["data"]) == set(fr["heartbeat"])
    rs = real["resume"]
    assert [f["event"] for f in rs] == ["ledger", "ledger", "heartbeat"]
    assert [f["id"] for f in rs[:2]] == ["3", "4"]
    assert set(rs[0]["data"]) == set(fr["ledger"][0]) and set(rs[1]["data"]) == set(fr["ledger"][1])
    assert rs[0]["data"]["running_balances"] == fr["ledger"][0]["running_balances"]
    assert [f["event"] for f in real["gone"]] == ["unavailable"] and set(real["gone"][0]["data"]) == set(fr["unavailable"])
    # the per-agent and session routes: the section keys the pages read
    for name, payload in (("derek", DEREK_PAYLOAD), ("xavier", XAVIER_PAYLOAD), ("audrey", AUDREY_PAYLOAD),
                          ("session", SESSION_PAYLOAD)):
        r = real[name]
        secs = {k for k, v in r.items() if isinstance(v, dict) and "status" in v}
        want = set(CCP.PAPER_CONTRACT["GET /api/command/paper/%s" % name])
        assert secs == want, (name, secs)
        assert set(r) == set(payload), name
    assert set(real["session"]["health"]["data"]) == set(HEALTH)
    assert set(real["session"]["enablement"]["data"]) == set(ENABLED)
    assert set(real["session"]["session"]["data"]) == set(SESSION_ROW)
    assert set(real["derek"]["opportunities"]["data"][0]) == set(DECISION)
    assert set(CCP.PAPER_BALANCE_KEYS) - {"refusal"} <= set(real["marked"])
    assert set(CCP.PAPER_ENTRY_KEYS) <= set(real["entry"])


async def test_the_pages_render_the_real_paper_modules_output(monkeypatch):
    real, _L = await _real_contract(monkeypatch)
    got = _node("xavier", """
      var R = %s, P = CC.paper, ok = function (j) { return {kind: 'OK', json: j}; };
      var st = P.newState(); P.fromAccount(st, R.account);
      var acc = P.account(P.view(st), null);
      var live = P.newState(); R.open.forEach(function (f) { P.onEvent(live, f.event, f.data); });
      var res = P.newState(); P.fromAccount(res, R.account);
      var moves = R.resume.map(function (f) { return P.onEvent(res, f.event, f.data); });
      return {banner: P.banner(ok(R.account)), off: P.banner(ok(R.no_schema)), acc: acc,
              ledger: P.ledger(P.entries(st), st.fresh), liveSeq: live.seq, liveN: P.entries(live).length, moves: moves,
              dd: P.drawdown(R.account.drawdown), pos: P.section('xavier', 'positions', ok(R.xavier)),
              opp: P.section('derek', 'opportunities', ok(R.derek)), ord: P.section('derek', 'orders', ok(R.derek)),
              sess: P.sessionPanel(ok(R.session))};
    """ % _j(real))
    assert got["banner"] == {"state": "ACTIVE", "text": "LIVE MARKET DATA · SIMULATED EXECUTION · $500,000 STARTING BANKROLL"}
    assert got["off"]["text"] == "PAPER SESSION NOT RUNNING — MIGRATION_171_IS_NOT_APPLIED"
    assert got["acc"]["status"] == "OK" and "$499,599.80" in got["acc"]["html"]
    assert got["acc"]["html"].count('<span class="ns">NOT STATED</span>') == 3
    assert 'data-label-item="slug:%s|ORDER_INTENT_BUY_LONG"' % PSLUG in got["ledger"] and got["ledger"].count("<tr data-seq=") == 4
    assert got["liveSeq"] == 4 and got["liveN"] == 4
    assert got["moves"] == ["replay", "replay", "heartbeat"]   # the GET was already current to entry 4
    assert "current $104.20 · max $104.20 (0.02%)" in got["dd"] and "1 skipped for incomplete marks" in got["dd"]
    assert got["pos"]["status"] == "OK" and "NO MARK" in got["pos"]["html"]
    assert got["opp"]["status"] == "OK" and got["ord"]["status"] == "OK" and "CANCELED" in got["ord"]["html"]
    assert got["sess"]["status"] == "OK" and "0 (expected 0)" in got["sess"]["html"]

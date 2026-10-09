"""THE WHOLE OPEN GAMMA CATALOGUE, BY THE VENUE'S DOCUMENTED CURSOR (P0,
2026-10-09).

PRODUCTION (render-ops 37933193326 / 37933204766, sportsassets-workers): every
metadata cycle logs

    metadata cycle ok: {'active_sports_markets': 233, 'gamma_paging':
      {'pages': 21, 'markets': 2100, 'stopped': 'OFFSET_CEILING',
       'max_offset': 2000, 'truncated': True}, ...}

-- the same first 2,100 open markets every minute, nothing past offset 2000
(the API answers offset 2100 with 422 "offset too large, use /markets/keyset
for deeper pagination"), and the heartbeat says ok.

The venue documents the way past it (docs.polymarket.com, "List markets
(keyset pagination)"): GET /markets/keyset, limit <= 100, closed, after_cursor
= the previous response's next_cursor, next_cursor "present only when the
number of returned markets equals the effective limit. Omitted on the last
page"; offset rejected with 422, an invalid cursor 422, 503 when keyset
pagination is not configured.

These tests pin: the whole catalogue is read across cycles by a rotation that
never spends more requests a cycle than the offset walk did; only documented
parameters are sent; a failed page resumes from the held cursor and a refused
cursor restarts the rotation, both named; a keyset the venue does not serve
as documented falls back to the offset walk inside the same budget, its
truncation named; and a truncated catalogue is no longer a green metadata
cycle.
"""
from __future__ import annotations

import asyncio
import base64
import inspect

import pytest

from sportsassets import gamma as G

DOCUMENTED_KEYSET_PARAMS = {"closed", "limit", "after_cursor"}
OFFSET_WALK_BUDGET = G.GAMMA_MAX_OFFSET // 100 + 1        # 21, production


class _Resp:
    def __init__(self, status, body):
        import json as _json
        self.status_code = status
        self.text = body if isinstance(body, str) else _json.dumps(body)
        self._body = body
        self.request = None

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


def _cursor(i: int) -> str:
    return base64.b64encode(("id>%d" % i).encode()).decode()


def _index(c: str) -> int:
    return int(base64.b64decode(c.encode()).decode().split(">")[1])


class _Gamma:
    """The gamma API as documented and as production measured it: /markets
    offset paging refused past offset 2000 with the production body;
    /markets/keyset with an opaque cursor, next_cursor only on a full page,
    offset and unknown parameters refused with 422."""

    def __init__(self, n, *, keyset=True, fail=None, envelope=True):
        self.n, self.keyset, self.fail, self.envelope = n, keyset, fail, envelope
        self.calls: list = []

    async def get(self, path, params=None):
        p = dict(params or {})
        self.calls.append((path, p))
        if self.fail is not None:
            r = self.fail(path, p, len(self.calls))
            if r is not None:
                return r
        if path == "/markets":
            off, lim = int(p.get("offset", 0)), int(p.get("limit", 100))
            if off > G.GAMMA_MAX_OFFSET:
                return _Resp(422, {"type": "validation error",
                                   "error": "offset too large, use "
                                            "/markets/keyset for deeper "
                                            "pagination"})
            return _Resp(200, [{"id": i} for i in range(off, min(off + lim,
                                                                 self.n))])
        if path == "/markets/keyset":
            if not self.keyset:
                return _Resp(503, {"error": "keyset pagination not configured"})
            if set(p) - DOCUMENTED_KEYSET_PARAMS:
                return _Resp(422, {"type": "validation error",
                                   "error": "unknown or rejected parameter: %s"
                                            % sorted(set(p) - DOCUMENTED_KEYSET_PARAMS)})
            lim = int(p.get("limit", 20))
            if not 1 <= lim <= 100:
                return _Resp(422, {"error": "limit"})
            start = 0
            if "after_cursor" in p:
                try:
                    start = _index(p["after_cursor"]) + 1
                except Exception:  # noqa: BLE001
                    return _Resp(422, {"error": "invalid cursor"})
            got = [{"id": i} for i in range(start, min(start + lim, self.n))]
            if not self.envelope:
                return _Resp(200, got)
            body = {"markets": got}
            if len(got) == lim:
                body["next_cursor"] = _cursor(got[-1]["id"])
            return _Resp(200, body)
        return _Resp(404, {"error": "not found"})

    async def aclose(self):
        return None

    def keyset_calls(self):
        return [p for path, p in self.calls if path == "/markets/keyset"]


def _client(http):
    c = G.GammaClient.__new__(G.GammaClient)
    c._http, c._open_params, c.last_paging = http, None, {}
    return c


def _cycles(c, http, k):
    """k metadata cycles: the markets each read, the requests each spent."""
    out = []
    for _ in range(k):
        before = len(http.calls)
        got = asyncio.run(c.fetch_open_markets())
        out.append(([m["id"] for m in got], len(http.calls) - before,
                    dict(c.last_paging)))
    return out


# ── the whole catalogue, inside the same budget ──────────────────────────

def test_the_whole_catalogue_is_read_across_cycles_within_the_old_budget():
    """5,000 open markets: the offset walk reads the same first 2,100 every
    cycle and never the other 2,900. The rotation reads all 5,000 across
    three cycles, never more than 21 requests a cycle."""
    http = _Gamma(5000)
    c = _client(http)
    runs = _cycles(c, http, 3)
    seen = set()
    for ids, spent, paging in runs:
        assert spent <= OFFSET_WALK_BUDGET, spent
        assert paging["mode"] == "KEYSET_ROTATION"
        assert paging["request_budget"] == OFFSET_WALK_BUDGET
        seen |= set(ids)
    assert seen == set(range(5000))
    # 50 full pages, then the documented last page (next_cursor omitted)
    assert [r[1] for r in runs] == [21, 21, 9]
    assert [r[2]["stopped"] for r in runs] == [
        G.K_CYCLE_BUDGET_SPENT, G.K_CYCLE_BUDGET_SPENT, G.K_ROTATION_COMPLETE]
    assert all(r[2]["truncated"] is False for r in runs)
    last = runs[-1][2]
    assert last["catalogue_complete"] is True
    assert last["last_complete_rotation"]["markets"] == 5000
    assert last["last_complete_rotation"]["cycles"] == 3
    assert last["last_complete_rotation"]["pages"] == 51
    # the catalogue was not complete before the rotation reached its end
    assert runs[0][2]["catalogue_complete"] is False
    # and the next cycle starts the next rotation from the beginning
    nxt = _cycles(c, http, 1)[0]
    assert nxt[0][:3] == [0, 1, 2]
    assert nxt[2]["rotation"]["number"] == 2


def test_only_documented_parameters_are_sent_and_never_an_offset():
    http = _Gamma(2500)
    c = _client(http)
    _cycles(c, http, 2)
    calls = http.keyset_calls()
    assert calls
    for p in calls:
        assert set(p) <= DOCUMENTED_KEYSET_PARAMS, p
        assert "offset" not in p
        assert p["closed"] == "false" and int(p["limit"]) == 100
    # each cursor sent is the one the previous page returned
    assert "after_cursor" not in calls[0]
    assert all(_index(p["after_cursor"]) == 99 + 100 * i
               for i, p in enumerate(calls[1:]) if "after_cursor" in p)


def test_a_small_catalogue_is_one_complete_rotation_per_cycle():
    http = _Gamma(350)
    c = _client(http)
    (ids, spent, paging), = _cycles(c, http, 1)
    assert sorted(ids) == list(range(350)) and spent == 4
    assert paging["stopped"] == G.K_ROTATION_COMPLETE
    assert paging["catalogue_complete"] is True and paging["rotation"] is None


# ── failures: named, and the rotation resumes ────────────────────────────

def test_a_failed_page_keeps_the_cursor_and_the_next_cycle_resumes_there():
    def fail(path, p, n):
        # the 6th request of the rotation answers 500
        return _Resp(500, {"error": "internal"}) if n == 6 else None

    http = _Gamma(5000, fail=fail)
    c = _client(http)
    (ids1, spent1, p1), (ids2, spent2, p2) = _cycles(c, http, 2)
    assert p1["stopped"] == "HTTP_500" and p1["truncated"] is True
    assert ids1 == list(range(500))
    assert p1["rotation"]["cursor_held"] is True
    # resumed from the held cursor: nothing skipped, nothing re-read
    assert ids2[0] == 500 and ids2 == list(range(500, 2600))
    assert p2["stopped"] == G.K_CYCLE_BUDGET_SPENT and p2["truncated"] is False
    assert p2["rotation"]["number"] == 1


def test_a_refused_cursor_restarts_the_rotation_by_name():
    def fail(path, p, n):
        if path == "/markets/keyset" and n == 3:
            return _Resp(422, {"error": "invalid cursor"})
        return None

    http = _Gamma(5000, fail=fail)
    c = _client(http)
    (ids1, _, p1), (ids2, _, p2) = _cycles(c, http, 2)
    assert p1["stopped"] == G.K_CURSOR_REFUSED and p1["truncated"] is True
    assert p1["rotation"] is None
    assert ids2[0] == 0 and p2["rotation"]["number"] == 2


# ── a keyset the venue does not serve: the offset walk, named ────────────

def test_an_unserved_keyset_falls_back_to_the_offset_walk_inside_the_budget(
        monkeypatch):
    http = _Gamma(5000, keyset=False)
    c = _client(http)
    old = _Gamma(5000)
    old_c = _client(old)
    asyncio.run(old_c.fetch_active_sports_markets())   # what a cycle cost before

    (ids, spent, paging), = _cycles(c, http, 1)
    assert paging["mode"] == "OFFSET_FALLBACK"
    assert paging["truncated"] is True and paging["catalogue_complete"] is False
    assert paging["keyset_unavailable"]["status"] == 503
    assert spent == len(old.calls), "no request may be added to the cycle"
    assert len(ids) == 2000          # one page of the budget went to the keyset
    # the keyset is not asked again until KEYSET_RETRY_AFTER_S has passed
    before = len(http.keyset_calls())
    (ids2, spent2, p2), = _cycles(c, http, 1)
    assert len(http.keyset_calls()) == before and len(ids2) == 2100
    assert p2["stopped"] == "OFFSET_CEILING" and p2["truncated"] is True
    monkeypatch.setattr(c, "_keyset_retry_at", 0.0)
    _cycles(c, http, 1)
    assert len(http.keyset_calls()) == before + 1


def test_a_body_that_is_not_the_documented_envelope_is_not_parsed_as_one():
    http = _Gamma(5000, envelope=False)
    c = _client(http)
    (ids, spent, paging), = _cycles(c, http, 1)
    assert paging["mode"] == "OFFSET_FALLBACK"
    assert G.K_ENVELOPE_UNREADABLE in paging["keyset_unavailable"]["why"]
    assert spent <= OFFSET_WALK_BUDGET + 1     # +1: the param-variant probe,
    # which the offset walk made before too (first cycle only)


# ── the metadata cycle is not green on a truncated catalogue ─────────────

class _Stop(Exception):
    pass


def _one_metadata_cycle(monkeypatch, paging):
    from sportsassets.workers import metadata_refresher as M

    beats = []

    class _FakeGamma:
        def __init__(self):
            self.last_paging = {}

        async def fetch_open_markets(self):
            self.last_paging = dict(paging)
            return []

        async def fetch_active_sports_markets(self):
            self.last_paging = dict(paging)
            return []

    class _Pool:
        async def fetch(self, *a):
            return []

        async def execute(self, *a):
            return "UPDATE 0"

    async def _pool():
        return _Pool()

    async def _zero(*a, **k):
        return 0

    async def _positions():
        return {}

    async def _beat(name, status, detail):
        beats.append((name, status, detail))

    async def _sleep(_s):
        raise _Stop()

    monkeypatch.setattr(M.gamma, "GammaClient", _FakeGamma)
    monkeypatch.setattr(M, "backfill_unenriched", _zero)
    monkeypatch.setattr(M, "get_pool", _pool)
    monkeypatch.setattr(M, "sync_all_positions", _positions)
    monkeypatch.setattr(M, "heartbeat", _beat)
    monkeypatch.setattr(M.asyncio, "sleep", _sleep)
    with pytest.raises(_Stop):
        asyncio.run(M.main())
    assert len(beats) == 1
    return beats[0]


def test_a_truncated_catalogue_makes_the_metadata_cycle_degraded(monkeypatch):
    """Production said `metadata cycle ok` beside OFFSET_CEILING truncated
    on every cycle. DEGRADED, by name, never green."""
    name, status, detail = _one_metadata_cycle(monkeypatch, {
        "pages": 21, "markets": 2100, "stopped": "OFFSET_CEILING",
        "max_offset": 2000, "truncated": True})
    assert name == "metadata" and status == "degraded"
    assert "OFFSET_CEILING" in detail["errors"]["catalogue"]
    assert detail["gamma_paging"]["truncated"] is True


def test_a_rotation_cycle_reading_its_share_is_ok(monkeypatch):
    name, status, detail = _one_metadata_cycle(monkeypatch, {
        "mode": "KEYSET_ROTATION", "pages": 21, "markets": 2100,
        "stopped": G.K_CYCLE_BUDGET_SPENT, "truncated": False,
        "catalogue_complete": False})
    assert status == "ok" and "errors" not in detail


def test_the_metadata_loop_walks_the_rotation():
    from sportsassets.workers import metadata_refresher as M
    src = inspect.getsource(M.main)
    assert "client.fetch_open_markets()" in src
    assert 'detail["gamma_paging"] = dict(client.last_paging)' in src

"""Drive maybe_execute END TO END (adapted from the re-review's probe): 
through the ladder harness -- empty IOC -> one GTC at wire_limit(his) ->
cancel -> final read -> the fill UPDATE and the lane stamp."""
import asyncio
from datetime import date

from sportsassets import live_executor, pmus

TODAY = date.today().isoformat()


class _Pool:
    def __init__(self):
        self.updates = []

    async def fetchval(self, sql, *a):
        if "INSERT INTO live_orders" in sql:
            return 101
        if "sum(pnl)" in sql:
            return 0.0
        if "bool_or" in sql:
            return None
        return None

    async def fetchrow(self, sql, *a):
        if "/* prior-copy */" in sql or "/* add-holder */" in sql:
            return None          # no prior row on the market: a fresh copy
        return {"day": 0.0, "total": 0.0}

    async def fetch(self, sql, *a):
        return []

    async def execute(self, sql, *a):
        self.updates.append((" ".join(sql.split()), a))


def _payload(**over):
    p = {"id": 1, "whale_id": 2, "whale_username": "RN1", "asset": "123",
         "condition_id": "0xc", "side": "BUY", "outcome": "Over 3.5",
         "size": 909.0, "price": 0.55, "notional": 499.95,
         "market_title": None, "market_slug": None, "event_slug": None}
    p.update(over)
    return p


def _wire(monkeypatch, pool, mapped_slug, *, gtc_final_filled, gtc_final_state="cancelled",
          cancel_ok=True):
    ctx = {"market_slug": f"epl-ars-che-{TODAY}-o3pt5",
           "event_slug": None, "market_title": "Arsenal vs Chelsea O/U",
           "event_title": None, "outcome": "Over 3.5"}

    async def fake_get_pool():
        return pool

    async def fake_paused(_pool):
        return False

    async def fake_ctx(_pool, _payload):
        return dict(ctx)

    from sportsassets import copy_sports as _cs
    monkeypatch.setattr(_cs, "HALTED_SPORTS", frozenset(), raising=True)
    monkeypatch.setattr(live_executor, "get_pool", fake_get_pool)
    monkeypatch.setattr(live_executor, "_is_paused", fake_paused)
    monkeypatch.setattr(live_executor, "_market_context", fake_ctx)
    monkeypatch.setattr(live_executor, "active_venue", lambda: "polymarket-us")
    monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "off")
    monkeypatch.setattr(live_executor, "COPY_CUT_WHALES", frozenset())
    monkeypatch.setitem(live_executor.PER_FILL_BY_WHALE, "rn1", 225.00)
    monkeypatch.setenv("LIVE_VERIFIED_WHALES", "")
    monkeypatch.setattr(pmus, "resolve_market_exact", lambda *a, **k: None)
    monkeypatch.setattr(
        pmus, "resolve_market",
        lambda *a, **k: {"market_slug": mapped_slug, "title": "O/U",
                         "outcome": "Over 3.5", "matched_by": "fuzzy",
                         "intent": "ORDER_INTENT_BUY_LONG", "score": 1.0})
    monkeypatch.setattr(pmus, "account_holds", lambda slug: False)
    # the lane snapshots the open book before it rests (rounds nine and
    # eleven): an unreadable book means no rest, so the fake answers
    monkeypatch.setattr(pmus, "open_orders", lambda slugs=None: [])
    monkeypatch.setattr(live_executor, "REST_BID_ENABLED", True)
    monkeypatch.setattr(live_executor, "REST_BID_BUDGET_USD", 2500.0)
    monkeypatch.setattr(live_executor, "_REST_RESERVED_USD", 0.0)
    calls = []

    def submit(slug, limit, shares, sell=False,
               tif="TIME_IN_FORCE_FILL_OR_KILL", intent=None):
        calls.append(("place", slug, limit, shares, tif, intent))
        raw = {"response": {"executions": [{"order": {"intent": intent}}]}}
        if tif == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL":
            # the adapter's rule: ok = filled > 0; an empty IOC is ok=False
            return {"ok": False, "order_id": "ioc-1", "status": "canceled",
                    "fill_price": None, "filled_shares": 0.0, "raw": raw}
        assert tif == "TIME_IN_FORCE_GOOD_TILL_CANCEL"
        return {"ok": False, "order_id": "gtc-1", "status": "new",
                "fill_price": None, "filled_shares": 0.0, "raw": raw}

    def cancel(oid, slug):
        calls.append(("cancel", oid, slug))
        return {"ok": cancel_ok}

    def status(oid):
        calls.append(("status", oid))
        return {"order_id": oid, "filled_shares": gtc_final_filled,
                "avg_px": 0.55 if gtc_final_filled else None,
                "state": gtc_final_state}

    monkeypatch.setattr(pmus, "submit_fok", submit)
    monkeypatch.setattr(pmus, "cancel_order", cancel)
    monkeypatch.setattr(pmus, "order_status", status)

    async def _sleep(s):
        pass
    monkeypatch.setattr(live_executor.asyncio, "sleep", _sleep)
    return calls


def _final(pool):
    return [u for u in pool.updates
            if "filled_usd=$6, raw=$7::jsonb, error=$8" in u[0]]


def test_e2e_empty_ioc_then_rest_fill(monkeypatch):
    pool = _Pool()
    slug = f"tsc-epl-ars-che-{TODAY}-o3pt5"
    calls = _wire(monkeypatch, pool, slug, gtc_final_filled=100.0)
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    kinds = [c[0] for c in calls]
    assert kinds == ["place", "place", "cancel", "status"], kinds
    ioc, gtc = calls[0], calls[1]
    assert ioc[4] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
    assert gtc[4] == "TIME_IN_FORCE_GOOD_TILL_CANCEL"
    assert gtc[2] == live_executor.wire_limit(0.55, "ORDER_INTENT_BUY_LONG")
    assert ioc[2] > gtc[2], "the IOC carried tolerance; the rest must not"
    assert gtc[3] == ioc[3], "same clip"
    assert calls[2] == ("cancel", "gtc-1", slug)
    persist = [u for u in pool.updates
               if "SET order_id=$2, raw=COALESCE(raw, '{}'::jsonb) || $3::jsonb WHERE id=$1" in u[0]]
    assert persist and persist[0][1][:2] == (101, "gtc-1")
    assert '"lane": "rest"' in persist[0][1][2]
    # persist happens BEFORE the cancel
    idx_persist = pool.updates.index(persist[0])
    fin = _final(pool)
    assert fin, pool.updates
    row_id, status, oid, filled, fp, spent, raw, err = fin[-1][1]
    assert (row_id, status, oid, filled, fp) == (101, "filled", "gtc-1", 100.0, 0.55)
    assert spent == 55.0 and err is None
    assert '"lane": "rest"' in raw
    lane = [u for u in pool.updates if "SET lane=$2" in u[0]]
    assert lane and lane[-1][1] == (101, "rest")
    assert pool.updates.index(fin[-1]) > idx_persist
    # t=0 of the price path: the pre-trade ask, recorded AFTER the fill
    pp = [u for u in pool.updates if "INSERT INTO price_path" in u[0]]
    assert pp and pp[-1][1][0] == 101 and 0.0 < pp[-1][1][1] < 1.0
    assert pool.updates.index(pp[-1]) > pool.updates.index(fin[-1])


def test_e2e_a_lost_gtc_response_holds_the_row_submitting_with_no_id(monkeypatch):
    """ROUND FOUR'S BLOCKER end to end: the GTC placement raises after
    the venue accepted it and nothing matching is on the book. The row
    must be 'submitting' with NO order id -- the reapers' signature --
    never 'unfilled' under the IOC's id."""
    pool = _Pool()
    slug = f"tsc-epl-ars-che-{TODAY}-o3pt5"
    calls = _wire(monkeypatch, pool, slug, gtc_final_filled=0.0)

    def submit(s, limit, shares, sell=False, tif="TIME_IN_FORCE_FILL_OR_KILL",
               intent=None):
        calls.append(("place", s, limit, shares, tif, intent))
        if tif == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL":
            return {"ok": False, "order_id": "ioc-1", "status": "canceled",
                    "fill_price": None, "filled_shares": 0.0, "raw": {}}
        raise TimeoutError("read timed out after the venue accepted")

    monkeypatch.setattr(pmus, "submit_fok", submit)
    monkeypatch.setattr(pmus, "open_orders", lambda slugs=None: [])
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    fin = _final(pool)
    row_id, status, oid, filled, fp, spent, raw, err = fin[-1][1]
    assert (status, oid, filled) == ("submitting", None, 0.0)
    assert "held 'submitting' with no id" in err


def test_e2e_empty_ioc_then_rest_unfilled_records_the_ioc(monkeypatch):
    pool = _Pool()
    slug = f"tsc-epl-ars-che-{TODAY}-o3pt5"
    calls = _wire(monkeypatch, pool, slug, gtc_final_filled=0.0)
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    assert [c[0] for c in calls] == ["place", "place", "cancel", "status"]
    fin = _final(pool)
    row_id, status, oid, filled, fp, spent, raw, err = fin[-1][1]
    assert (status, oid, filled, spent) == ("unfilled", "ioc-1", 0.0, 0.0)
    lane = [u for u in pool.updates if "SET lane=$2" in u[0]]
    assert lane[-1][1] == (101, "ioc")
    pp = [u for u in pool.updates if "INSERT INTO price_path" in u[0]]
    assert pp and pp[-1][1][0] == 101          # t=0 recorded on the miss too


def test_e2e_rest_unknown_keeps_submitting_under_the_gtc_id(monkeypatch):
    pool = _Pool()
    slug = f"tsc-epl-ars-che-{TODAY}-o3pt5"
    # ROUND TWO: a TERMINAL read is authoritative whatever the cancel said,
    # so "unknown" needs the order still OPEN after two failed cancels.
    calls = _wire(monkeypatch, pool, slug, gtc_final_filled=0.0, cancel_ok=False,
                  gtc_final_state="open")
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    assert [c[0] for c in calls].count("cancel") == 2
    fin = _final(pool)
    row_id, status, oid, filled, fp, spent, raw, err = fin[-1][1]
    assert (status, oid, filled) == ("submitting", "gtc-1", 0.0)
    assert "reconcile against the venue" in err
    lane = [u for u in pool.updates if "SET lane=$2" in u[0]]
    assert lane[-1][1] == (101, "rest")

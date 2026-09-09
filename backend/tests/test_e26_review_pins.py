"""E26 (FILL lane 26) -- the adversarial review's pins.

HIGH-1. The sweep's span opened at cursor + 1, and the cursor is the
block of the newest log ANY path handled: a socket log landing after
the last sweep moved it past blocks the sweep never read, so a log the
socket dropped in one of them -- or in the SAME block as the one it
delivered (the 17:04:57 shape: three txs the relayer settled within
seconds) -- was behind the cursor for good and never swept. The sweep
is now contiguous with its OWN last end (`_sweep_end`), opens at the
cursor block itself on the first sweep, and resumes at the cursor block
after a lag past CHAIN_SWEEP_MAX_BLOCKS (the catch-up owns that gap).

MEDIUM-1. The poller's overflow walk fired on an all-new page, and a
first page whose pre-probe RAISED read as all-new (`seen = set()`): the
walk then made two more venue requests and 300 ingest attempts on every
poll of every busy whale for as long as the probe failed, and logged the
overflow line each time. An unreadable probe is not knowledge: one
page, as today. On an overflow page it ends the walk there, counted.
"""

from __future__ import annotations

import logging
import time

from sportsassets.ingestion import chain as ch
from sportsassets.ingestion import poller as P
from tests.test_e26_chain_sweep import (
    CURSOR, TIP, TX1, TX2, TX3, WHALE, _Pool, _Resp, _Rpc, _fill_log, _listener, _run,
)
from tests.test_e26_poll_overflow import LOGGER, WHALE as PWHALE, _SeenPool, _Venue, _poll, _rows, world  # noqa: F401


def _by_span(logs):
    """eth_getLogs that honours fromBlock / toBlock (the suite's fake
    served every log whatever the span, which hid the defect)."""
    def f(params):
        lo, hi = int(params["fromBlock"], 16), int(params["toBlock"], 16)
        return _Resp({"result": [l for l in logs if lo <= int(l["blockNumber"], 16) <= hi]})
    return f


# ── HIGH-1: the sweep's own end ───────────────────────────────────────

def test_a_socket_log_landing_after_the_last_sweep_does_not_hide_an_older_dropped_one(monkeypatch):
    """Sweep 1 reads up to 1008 (tip 1010). The socket then delivers TX1
    at block 1012 -- the cursor moves to 1012 -- while TX2's log at
    block 1010 was dropped and TX3 shares block 1012 with TX1. Sweep 2
    (tip 1020) must open at 1009, the block after its own last end, and
    land TX2 and TX3; opened at cursor + 1 = 1013 it landed neither."""
    logs = [_fill_log(WHALE, TX1, hex(1012)), _fill_log(WHALE, TX2, hex(1010)),
            _fill_log(WHALE, TX3, hex(1012))]
    rpc, pool = _Rpc(get_logs=_by_span(logs)), _Pool()
    lst, ingested, _ = _listener(rpc, pool, monkeypatch)
    _run(lst._sweep_once())
    assert pool.cursor == TIP - 2 and ingested == [] and lst._sweep_end == TIP - 2
    rpc.tip = 1020
    _run(lst._handle_log(logs[0]))                      # the socket hands over TX1 @1012
    assert pool.cursor == 1012 and [e.tx_hash for e in ingested] == [TX1]
    _run(lst._sweep_once())
    assert rpc.get_logs_calls()[-1]["fromBlock"] == hex(TIP - 2 + 1), \
        "contiguous with the sweep's own end, not with the socket's block"
    assert rpc.get_logs_calls()[-1]["toBlock"] == hex(1018)
    assert rpc.receipts_fetched() == [TX1, TX2, TX3], "one receipt each; TX1's not a second time"
    assert [e.tx_hash for e in ingested] == [TX1, TX2, TX3]
    assert pool.cursor == 1018 and lst._sweep_end == 1018
    assert lst._sweep_state()["found"] == 2


def test_the_first_sweep_opens_at_the_cursor_block_so_a_sibling_of_the_last_handled_log_is_read(monkeypatch):
    """The socket handled TX1 in block 1004 (the cursor); TX2 settled in
    the SAME block and its log was dropped. The first sweep reads block
    1004 itself: TX1 drops on _v3_seen before any RPC, TX2 lands."""
    logs = [_fill_log(WHALE, TX1, hex(1004)), _fill_log(WHALE, TX2, hex(1004))]
    rpc, pool = _Rpc(get_logs=_by_span(logs)), _Pool(cursor=1004)
    lst, ingested, _ = _listener(rpc, pool, monkeypatch, v3_seen={TX1: time.time()})
    _run(lst._sweep_once())
    assert rpc.get_logs_calls() == [{"fromBlock": hex(1004), "toBlock": hex(TIP - 2),
                                     "address": lst._addresses, "topics": lst._topics}]
    assert rpc.receipts_fetched() == [TX2] and [e.tx_hash for e in ingested] == [TX2]
    assert lst._sweep_state()["found"] == 1 and pool.cursor == TIP - 2


def test_a_lag_past_max_blocks_resumes_at_the_cursor_block_and_within_it_continues(monkeypatch):
    """The sweep's own end 200 blocks behind the cursor at MAX 100: the
    catch-up backfill owns that gap (as today) and the sweep resumes at
    the cursor block; 50 behind, it continues from its own end, at most
    MAX blocks per sweep."""
    monkeypatch.setattr(ch, "CHAIN_SWEEP_MAX_BLOCKS", 100.0)
    rpc, pool = _Rpc(tip=5010, get_logs=_by_span([])), _Pool(cursor=5000)
    lst, _, _ = _listener(rpc, pool, monkeypatch)
    lst._sweep_end = 4800
    _run(lst._sweep_once())
    c = rpc.get_logs_calls()[-1]
    assert (c["fromBlock"], c["toBlock"]) == (hex(5000), hex(5008))
    assert lst._sweep_end == 5008 and pool.cursor == 5008
    lst._sweep_end = 4950
    _run(lst._sweep_once())
    c = rpc.get_logs_calls()[-1]
    assert (c["fromBlock"], c["toBlock"]) == (hex(4951), hex(5008)), "min(tip - 2, start + MAX - 1)"
    lst._sweep_end = 4950
    rpc.tip = 6000
    _run(lst._sweep_once())
    c = rpc.get_logs_calls()[-1]
    assert (c["fromBlock"], c["toBlock"]) == (hex(4951), hex(5050)), "never more than MAX blocks"
    assert lst._sweep_end == 5050 and pool.cursor == 5050


def test_a_failed_sweep_keeps_its_own_end_and_the_next_one_retries_the_same_span(monkeypatch):
    calls = {"n": 0}

    def flaky(params):
        calls["n"] += 1
        if calls["n"] == 2:
            return _Resp({"error": {"code": -32000, "message": "busy"}})
        return _Resp({"result": []})
    rpc, pool = _Rpc(get_logs=flaky), _Pool()
    lst, _, _ = _listener(rpc, pool, monkeypatch)
    _run(lst._sweep_once())
    assert lst._sweep_end == TIP - 2
    rpc.tip = 1020
    _run(lst._sweep_once())
    assert lst._sweep_end == TIP - 2 and lst._sweep_state()["failed"] == 1 and pool.cursor == TIP - 2
    _run(lst._sweep_once())
    assert rpc.get_logs_calls()[-1]["fromBlock"] == hex(TIP - 1) and lst._sweep_end == 1018


def test_nothing_confirmed_past_the_last_sweep_makes_no_get_logs(monkeypatch):
    rpc, pool = _Rpc(tip=CURSOR + 1, get_logs=_by_span([])), _Pool()     # tip - 2 < cursor
    lst, _, _ = _listener(rpc, pool, monkeypatch)
    _run(lst._sweep_once())
    assert rpc.get_logs_calls() == [] and pool.saves == [] and getattr(lst, "_sweep_end", None) is None
    rpc.tip = TIP
    _run(lst._sweep_once())
    assert lst._sweep_end == TIP - 2
    _run(lst._sweep_once())                                                # nothing new: no call
    assert len(rpc.get_logs_calls()) == 1


# ── MEDIUM-1: an unreadable pre-probe is not an all-new page ──────────

def test_an_unreadable_first_page_pre_probe_is_not_an_all_new_page(world, caplog):
    """The pre-probe raising (a statement timeout) leaves `seen` empty;
    that is not knowledge that every key is new. One page, as today: no
    second request, no overflow counted, no line."""
    state, ingested = world

    class _DeadProbe(_SeenPool):
        async def fetch(self, sql, *args):
            raise RuntimeError("statement timeout")
    state["pool"] = _DeadProbe()
    state["venue"] = _Venue({0: _rows(0), 100: _rows(100), 200: _rows(200)})
    caplog.set_level(logging.WARNING, logger=LOGGER)
    assert _poll() == 100
    assert len(state["venue"].requests) == 1
    assert P.page_overflow_counts() == {} and "overflow" not in caplog.text


def test_an_unreadable_pre_probe_on_an_overflow_page_ends_the_walk_there(world, caplog):
    """The second page's probe raises: its rows stand (ingest's ON
    CONFLICT is the gate), the walk ends, page_overflow counted, the
    bound's own line NOT written (nothing was reached)."""
    state, ingested = world

    class _ProbeDiesOnPage2(_SeenPool):
        async def fetch(self, sql, *args):
            if self.any_queries:
                raise RuntimeError("statement timeout")
            return await super().fetch(sql, *args)
    state["pool"] = _ProbeDiesOnPage2()
    state["venue"] = _Venue({0: _rows(0), 100: _rows(100), 200: _rows(200), 300: _rows(300)})
    caplog.set_level(logging.WARNING, logger=LOGGER)
    assert _poll() == 200
    assert len(state["venue"].requests) == 2
    assert P.page_overflow_counts() == {"RN1": 1}
    assert "no row seen" not in caplog.text and "pre-probe unreadable" in caplog.text


# ── the fold's pins: two mutants on the delta's own lines survived the
# review's table (which was cut on the builder's code) ─────────────────

def test_a_raising_handler_keeps_the_sweeps_own_end_so_the_next_sweep_re_reads_the_span(monkeypatch):
    """A handler that raises leaves the cursor unsaved (the builder's
    rule) AND `_sweep_end` unmoved: were `_sweep_end` remembered on a
    broken sweep, the next one would open past the span the cursor was
    deliberately not advanced over, and the re-read the docs promise
    would never happen (fold mutant F4)."""
    rpc, pool = _Rpc(), _Pool()
    lst, ingested, _ = _listener(rpc, pool, monkeypatch, v3_seen={TX1: time.time()})
    lst._sweep_end = CURSOR

    async def dead_ingest(ev):
        raise RuntimeError("database gone")
    monkeypatch.setattr(ch, "ingest_trade_result", dead_ingest)
    _run(lst._sweep_once())
    assert lst._sweep_state()["failed"] == 2 and pool.saves == [] and pool.cursor == CURSOR
    assert lst._sweep_end == CURSOR, "a broken sweep does not move the sweep's own end"
    assert rpc.get_logs_calls()[-1]["fromBlock"] == hex(CURSOR + 1)
    monkeypatch.setattr(ch, "ingest_trade_result", lambda ev: _ok(ingested, ev))
    _run(lst._sweep_once())
    assert rpc.get_logs_calls()[-1]["fromBlock"] == hex(CURSOR + 1), "the same span again"


async def _ok(ingested, ev):
    ingested.append(ev)
    return True


def test_a_lag_of_exactly_max_blocks_is_still_read_whole_from_the_sweeps_own_end(monkeypatch):
    """The lag rule's boundary: a sweep end exactly MAX blocks behind
    the cursor continues from its own end and reaches the cursor block
    in this one sweep (nothing skipped); one block further behind
    resumes at the cursor (fold mutant F7: `>=` would skip the MAX-block
    gap that one sweep can still cover)."""
    monkeypatch.setattr(ch, "CHAIN_SWEEP_MAX_BLOCKS", 100.0)
    rpc, pool = _Rpc(tip=5010, get_logs=_by_span([])), _Pool(cursor=5000)
    lst, _, _ = _listener(rpc, pool, monkeypatch)
    lst._sweep_end = 4900
    _run(lst._sweep_once())
    c = rpc.get_logs_calls()[-1]
    assert (c["fromBlock"], c["toBlock"]) == (hex(4901), hex(5000)), "lag == MAX: contiguous, ends at the cursor"
    assert lst._sweep_end == 5000
    lst._sweep_end = 4899
    pool.cursor = 5000
    _run(lst._sweep_once())
    c = rpc.get_logs_calls()[-1]
    assert (c["fromBlock"], c["toBlock"]) == (hex(5000), hex(5008)), "lag == MAX + 1: the cursor block"

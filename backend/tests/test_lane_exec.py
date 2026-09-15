"""Execution by detection lane (analytics/lane_exec.py) and the send
stamps the executor writes for it."""
import asyncio
import inspect

from sportsassets import live_executor as le
from sportsassets.analytics import lane_exec as lx


def _r(lane="chain", status="filled", filled_usd=48.0, reaction=1.2, det=0.4,
       his_ts=1000.0, t_send=None, t_reply=None, stake=48.0, pnl=None,
       key="g1", settled=False):
    return {"lane": lane, "status": status, "filled_usd": filled_usd,
            "reaction_s": reaction, "det_lag": det, "his_ts": his_ts,
            "t_send": t_send, "t_reply": t_reply, "stake": stake, "pnl": pnl,
            "event_key": key, "settled": settled}


def test_percentiles_interpolate_and_survive_gaps():
    assert lx._pct([], 0.5) is None
    assert lx._pct([3.0], 0.9) == 3.0
    assert lx._pct([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert lx._pct([1.0, None, 3.0], 0.5) == 2.0


def test_each_lane_counts_its_own_attempts_fills_and_seconds():
    rows = [_r(t_send=1001.5, t_reply=1001.9),
            _r(status="unfilled", filled_usd=0, reaction=1.4, t_send=1002.0, t_reply=1002.3),
            _r(status="rejected", filled_usd=0, reaction=1.1),
            _r(lane="poll", reaction=280.0, det=275.0, t_send=1290.0, t_reply=1290.5),
            _r(lane="poll", status="error", filled_usd=0, reaction=300.0)]
    out = lx.summarize(rows)
    c, p = out["lanes"]["chain"], out["lanes"]["poll"]
    assert c["attempts"] == 3 and c["filled"] == 1 and c["unfilled"] == 1 and c["rejected"] == 1
    assert c["fill_rate"] == round(1 / 3, 4)
    assert c["latency"]["send_s"]["n"] == 2 and c["latency"]["send_s"]["p50"] == 1.75
    assert c["latency"]["venue_rtt_s"]["p90"] == round(0.3 + 0.9 * 0.1, 3)
    assert p["attempts"] == 2 and p["filled"] == 1 and p["error"] == 1
    assert p["latency"]["reaction_s"]["p50"] == 290.0
    assert out["reading"].startswith("chain lane: 3 attempts, fill rate 33%")


def test_refusals_are_named_and_a_reclaim_send_is_not_a_lane_latency():
    rows = [_r(status="rejected", filled_usd=0) | {"err": "one position per game"},
            _r(status="rejected", filled_usd=0) | {"err": "one position per game"},
            _r(status="rejected", filled_usd=0) | {"err": "unmapped: no US market"},
            _r(t_send=1001.0, t_reply=1001.2),
            _r(t_send=1000.0 + 90_000.0, t_reply=1000.0 + 90_000.3)]   # sweep reclaim
    out = lx.summarize(rows)
    c = out["lanes"]["chain"]
    assert c["rejected"] == 3
    assert c["rejected_reasons"][0] == {"reason": "one position per game", "n": 2}
    assert c["rejected_reasons"][1] == {"reason": "unmapped: no US market", "n": 1}
    assert c["latency"]["send_s"]["n"] == 1 and c["latency"]["send_s"]["p50"] == 1.0
    assert c["latency"]["venue_rtt_s"]["n"] == 2


def test_a_fill_needs_dollars_not_just_a_status():
    out = lx.summarize([_r(status="filled", filled_usd=0)])
    assert out["lanes"]["chain"]["filled"] == 0


def test_settled_roi_is_clustered_and_gated_at_thirty_games():
    rows = [_r(status="settled", settled=True, pnl=5.0, key=f"g{i}") for i in range(12)]
    out = lx.summarize(rows)
    s = out["lanes"]["chain"]["settled"]
    assert s["clusters"] == 12 and s["verdict"].startswith("PROVISIONAL")
    rows = [_r(status="settled", settled=True, pnl=5.0 if i % 5 else -2.0, key=f"g{i}")
            for i in range(40)]
    s = lx.summarize(rows)["lanes"]["chain"]["settled"]
    assert s["verdict"] == "POSITIVE at 95%"


def test_no_chain_rows_says_so():
    assert lx.summarize([_r(lane="poll")])["reading"] == "no chain-lane copies in the window"


def test_the_cohort_reads_fresh_buys_by_source_with_the_stamps():
    class _Pool:
        def __init__(self):
            self.calls = []

        async def fetch(self, sql, *a):
            self.calls.append((sql, a))
            return [_r(t_send=1001.0, t_reply=1001.4)]

    pool = _Pool()
    out = asyncio.run(lx.cohort_lane_exec(pool, 7, "RN1"))
    sql, args = pool.calls[0]
    assert "(lo.raw->>'t_send')::float8 AS t_send" in sql
    assert "COALESCE(t.source, 'unknown') AS lane" in sql
    assert "lo.reaction_s IS NOT NULL" in sql and "lo.side = 'BUY'" in sql
    assert args == (7, "rn1")
    assert out["lanes"]["chain"]["attempts"] == 1 and out["whale"] == "rn1"


def test_the_executor_stamps_send_and_reply_around_the_ioc():
    src = inspect.getsource(le.maybe_execute)
    i = src.index("result = await _ioc_guarded(")
    assert '_timing = {"t_send": time.time()' in src[i - 300:i]
    assert '_timing["t_reply"] = time.time()' in src[i:i + 400]
    j = src.index('**(locals().get("_timing") or {})')
    # the stamps are folded into the receipt, which is what the fill
    # UPDATE writes as raw (and what an add leg's merge writes on it)
    assert "_receipt = json.dumps(" in src[j - 200:j]
    k = src.index("raw=$7::jsonb", j)
    assert "_receipt," in src[k:k + 1400]

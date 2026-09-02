"""ADD LEGS (owner order 2026-09-02, "make all 4 of these changes now"):
his fresh BUY on an outcome HIS filled copy row already holds is copied
as an add leg and merged into that row on fill. Driven end to end
through the rest-lane harness: the already-taken referee lets the
candidate through, never-add declares the add, the IOC fills, and ONE
statement books the fill onto the standing row and retires the leg.

Review round one (2026-09-02) added: the twin refusal (one whale fill,
two trade ids), the no-second-position rule (a standing row mid-exit
names the leg instead of a 'filled' row beside it), the relative exit
write, the daily cap's own clock for legs, and the reaper's merge."""
import asyncio
import inspect
import json
import pathlib

from sportsassets import live_executor, pmus
from tests.test_rest_lane_end_to_end import TODAY, _payload, _Pool, _wire

STANDING = {"id": 7, "status": "filled", "whale": "rn1", "asset": "123",
            "filled_shares": 100.0, "fill_price": 0.50, "filled_usd": 50.0,
            "adds": None, "tx_hash": "0xaaa"}


class _AddPool(_Pool):
    """The standing row is on the ledger: the asset is taken (fetchval),
    its holder is HIS filled row (add-holder), and the market's prior
    row is the same (prior-copy). `merge_hits` is a list of outcomes for
    successive merge statements; `standing_after` is what the standing
    row's status reads when a merge missed."""

    def __init__(self, standing=None, merge_hits=(True,), taken=1,
                 standing_after="cashed_out", cand_tx=None):
        super().__init__()
        self.standing = dict(STANDING) if standing is None else standing
        self.merge_hits = list(merge_hits)
        self.taken = taken
        self.standing_after = standing_after
        self.cand_tx = cand_tx
        self.merges = []

    async def fetchval(self, sql, *a):
        if "SELECT 1 FROM live_orders WHERE asset = $1" in sql:
            return self.taken
        if "/* add-standing */" in sql:
            return self.standing_after
        if "/* add-tx */" in sql:
            return self.cand_tx
        return await super().fetchval(sql, *a)

    async def fetchrow(self, sql, *a):
        s = " ".join(sql.split())
        if "/* add-holder */" in s:
            return {"status": self.standing["status"],
                    "whale": self.standing["whale"],
                    "recent": self.standing.get("recent", True)}
        if "/* prior-copy */" in s:
            return self.standing
        if "SET status = 'merged'" in s:
            self.merges.append((s, a))
            hit = self.merge_hits.pop(0) if self.merge_hits else False
            return {"shares": 150.0, "px": 0.533333, "usd": 80.0} if hit else None
        return await super().fetchrow(sql, *a)


def _fill_ioc(monkeypatch, calls, shares=50.0, price=0.60):
    def submit(slug, limit, sh, sell=False,
               tif="TIME_IN_FORCE_FILL_OR_KILL", intent=None):
        calls.append(("place", slug, limit, sh, tif, intent))
        assert tif == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
        return {"ok": True, "order_id": "ioc-9", "status": "filled",
                "fill_price": price, "filled_shares": shares,
                "raw": {"response": {"executions": [{"order": {"intent": intent}}]}}}
    monkeypatch.setattr(pmus, "submit_fok", submit)


def _run(monkeypatch, pool, reaction=5.0, **over):
    slug = f"tsc-epl-ars-che-{TODAY}-o3pt5"
    calls = _wire(monkeypatch, pool, slug, gtc_final_filled=0.0)
    monkeypatch.setattr(live_executor, "ADDS_ENABLED", True)
    monkeypatch.setattr(live_executor, "MAX_ADDS_PER_MARKET", 3)
    _fill_ioc(monkeypatch, calls)
    over.setdefault("tx_hash", "0xbbb")          # his fresh fill, a new tx
    asyncio.run(live_executor.maybe_execute(_payload(**over), reaction))
    return calls


def _rejections(pool):
    return [a[1] for s, a in pool.updates
            if "SET status='rejected', error=$2" in s]


def _generic_fill(pool):
    return [u for u in pool.updates if "filled_usd=$6, raw=$7::jsonb, error=$8" in u[0]]


def _named(pool):
    return [u for u in pool.updates
            if "SET status='error', order_id=COALESCE($6, order_id)" in u[0]]


def test_a_fresh_buy_on_his_own_filled_row_is_an_add_merged_in_one_statement(monkeypatch):
    pool = _AddPool()
    calls = _run(monkeypatch, pool)
    # the IOC went out, once, and NOTHING rested after it
    assert [c[0] for c in calls] == ["place"], calls
    assert not _rejections(pool)
    # the leg named its standing row on its own raw BEFORE the order
    stamp = [u for u in pool.updates
             if "SET raw = COALESCE(raw, '{}'::jsonb) || $2::jsonb WHERE id=$1" in u[0]]
    assert stamp and stamp[0][1] == (101, json.dumps({"add_of": 7}))
    # ONE statement: standing row grows, leg row retires as 'merged'
    assert len(pool.merges) == 1
    sql, args = pool.merges[0]
    assert "WHERE id = $2 AND status = 'filled'" in sql
    assert "filled_shares = filled_shares + $3::float8" in sql
    assert "orig_shares = COALESCE(orig_shares, filled_shares::float8) + $3::float8" in sql
    assert "requested_usd = COALESCE(requested_usd, 0) + $6::float8" in sql
    # share-weighted price, computed from the row's CURRENT columns
    assert ("(COALESCE(fill_price, 0)::float8 * filled_shares::float8 + $4::float8 * $3::float8)"
            " / (filled_shares::float8 + $3::float8)") in sql
    assert "SET status = 'merged'" in sql and "filled_shares = 0, fill_price = NULL, filled_usd = 0" in sql
    leg_id, standing_id, shares, px, usd, req, adds, oid, raw, err = args
    assert (leg_id, standing_id, shares, px, oid) == (101, 7, 50.0, 0.60, "ioc-9")
    assert usd == 30.0 and req > 0
    legs = json.loads(adds)
    assert legs[0]["trade_id"] == "1" and legs[0]["row_id"] == 101
    assert legs[0]["shares"] == 50.0 and legs[0]["price"] == 0.60 and legs[0]["usd"] == 30.0
    assert legs[0]["tx_hash"] == "0xbbb" and legs[0]["his_price"] == 0.55
    r = json.loads(raw)
    assert r["add_of"] == 7 and r["add_leg"]["order_id"] == "ioc-9"
    assert "t_send" in r and "t_reply" in r, "the venue timing stamps ride on the leg"
    assert err == "add-merged: +50 @ 0.6 ($30.00) into row #7 — leg 1 of 3"
    # the generic fill UPDATE did NOT run: the leg is not a second fill
    assert _generic_fill(pool) == [] and _named(pool) == []
    lane = [u for u in pool.updates if "SET lane=$2" in u[0]]
    assert lane and lane[-1][1] == (101, "ioc")


def test_a_standing_row_that_is_gone_leaves_the_leg_as_its_own_fill(monkeypatch):
    pool = _AddPool(merge_hits=(False,), standing_after="cashed_out")
    _run(monkeypatch, pool)
    assert len(pool.merges) == 1
    generic = _generic_fill(pool)
    assert generic, pool.updates
    row_id, status, oid, filled, fp, spent, raw, err = generic[-1][1]
    assert (row_id, status, oid, filled, fp, spent) == (101, "filled", "ioc-9", 50.0, 0.60, 30.0)
    assert err.startswith("add-unmerged: standing row #7 left 'filled'")
    assert _named(pool) == []


def test_a_standing_row_mid_exit_names_the_leg_never_a_second_position(monkeypatch):
    """REVIEW ROUND ONE (blocking): a 'filled' leg beside an 'exiting'
    standing row would refuse the exit's write-back and wedge it for
    good. The leg is recorded under the orphan name with its figures."""
    for st in ("exiting", "settled", "submitting"):
        pool = _AddPool(merge_hits=(False,), standing_after=st)
        _run(monkeypatch, pool)
        assert _generic_fill(pool) == [], st
        named = _named(pool)
        assert len(named) == 1, (st, pool.updates)
        sql, args = named[0]
        assert "filled_shares=$2, fill_price=$3, filled_usd=$4, error=$5" in sql
        assert "WHERE id=$1 AND status IN ('submitting', 'error')" in sql
        assert args[0] == 101 and args[1:4] == (50.0, 0.60, 30.0) and args[5] == "ioc-9"
        assert args[4].startswith("ORPHAN FILL RECORDED on a row that cannot re-enter 'filled' "
                                  "(add leg of row #7;")
        assert f"standing row was '{st}'" in args[4]
        # the receipt and the standing row's id stay on the leg's raw
        raws = [u for u in pool.updates if "SET raw = $2::jsonb WHERE id=$1" in u[0]]
        assert raws and json.loads(raws[-1][1][1])["add_of"] == 7


def test_a_merge_that_misses_while_the_row_is_filled_is_retried_once(monkeypatch):
    pool = _AddPool(merge_hits=(False, True), standing_after="filled")
    _run(monkeypatch, pool)
    assert len(pool.merges) == 2
    assert _generic_fill(pool) == [] and _named(pool) == []
    # and when both tries miss with the row still 'filled': named, not a
    # second position
    pool2 = _AddPool(merge_hits=(False, False), standing_after="filled")
    _run(monkeypatch, pool2)
    assert len(pool2.merges) == 2 and _generic_fill(pool2) == []
    assert len(_named(pool2)) == 1


def test_the_other_outcome_of_a_market_we_hold_is_never_an_add(monkeypatch):
    """His hedge or reversal on the market's other token must not be
    merged onto OUR side. Same slug, different asset -> never-add."""
    pool = _AddPool(standing={**STANDING, "asset": "456"})
    calls = _run(monkeypatch, pool)
    assert calls == [] and pool.merges == []
    rej = _rejections(pool)
    assert rej and rej[0].startswith("never-add: this market was already copied")
    assert "other outcome" in rej[0]


def test_a_twin_detection_of_one_fill_is_refused_by_its_transaction(monkeypatch):
    """REVIEW ROUND ONE: one whale fill can land as two trades rows
    (key-divergent twins). The transaction hash is the fill's identity."""
    # same tx as the standing row's trade (case-insensitive)
    pool = _AddPool()
    calls = _run(monkeypatch, pool, tx_hash="0xAAA")
    assert calls == [] and pool.merges == []
    assert "twin detection" in _rejections(pool)[0]
    # same tx as a merged leg
    pool = _AddPool(standing={**STANDING, "adds": json.dumps([{"trade_id": "9", "tx_hash": "0xccc"}])})
    calls = _run(monkeypatch, pool, tx_hash="0xccc")
    assert calls == [] and "twin detection" in _rejections(pool)[0]
    # no hash on the payload: the trade's own hash is read; unknowable -> refused
    pool = _AddPool(cand_tx=None)
    calls = _run(monkeypatch, pool, tx_hash=None)
    assert calls == [] and "twin detection" in _rejections(pool)[0]
    pool = _AddPool(cand_tx="0xddd")
    calls = _run(monkeypatch, pool, tx_hash=None)
    assert [c[0] for c in calls] == ["place"] and len(pool.merges) == 1


def test_a_row_that_took_its_legs_refuses_a_fourth(monkeypatch):
    legs = [{"trade_id": str(i), "shares": 10.0, "tx_hash": f"0x{i}"} for i in (11, 12, 13)]
    pool = _AddPool(standing={**STANDING, "adds": json.dumps(legs)})
    calls = _run(monkeypatch, pool)
    assert calls == []
    assert "3 legs already" in _rejections(pool)[0]


def test_a_trade_already_merged_is_refused_on_replay(monkeypatch):
    pool = _AddPool(standing={**STANDING, "adds": json.dumps([{"trade_id": "1", "tx_hash": "0xeee"}])})
    calls = _run(monkeypatch, pool)
    assert calls == []
    assert "this trade already merged" in _rejections(pool)[0]


def test_another_whales_row_and_a_row_in_flight_refuse(monkeypatch):
    for standing in ({**STANDING, "whale": "0x076daa87"},
                     {**STANDING, "status": "submitting"}):
        pool = _AddPool(standing=standing)
        calls = _run(monkeypatch, pool)
        assert calls == [] and pool.merges == []
        # the already-taken referee stopped it before any row existed
        assert not [u for u in pool.updates if "INSERT" in u[0]]


def test_adds_off_and_the_sweep_path_stay_never_add(monkeypatch):
    pool = _AddPool()
    monkeypatch.setattr(live_executor, "ADDS_ENABLED", False)
    slug = f"tsc-epl-ars-che-{TODAY}-o3pt5"
    calls = _wire(monkeypatch, pool, slug, gtc_final_filled=0.0)
    _fill_ioc(monkeypatch, calls)
    asyncio.run(live_executor.maybe_execute(_payload(tx_hash="0xbbb"), 5.0))
    assert calls == [] and pool.merges == []
    # a sweep re-offer (reaction None) never qualifies as an add either:
    # the asset is taken and the cheap referee stops it with no row
    pool2 = _AddPool()
    calls2 = _wire(monkeypatch, pool2, slug, gtc_final_filled=0.0)
    monkeypatch.setattr(live_executor, "ADDS_ENABLED", True)
    _fill_ioc(monkeypatch, calls2)
    asyncio.run(live_executor.maybe_execute(_payload(tx_hash="0xbbb"), None))
    assert calls2 == [] and pool2.merges == []


def test_the_asset_referee_only_yields_to_his_own_filled_row():
    src = inspect.getsource(live_executor.maybe_execute)
    i = src.index("/* add-holder */")
    block = src[i:i + 400]
    assert '_row_get(holder, "status") == "filled"' in block
    assert '_row_get(holder, "whale") == username' in block
    assert "ORDER BY (status = 'submitting') DESC" in src[i - 400:i]
    # the never-add referee ranks the row on HIS asset first, then a
    # leg in flight, so a second leg waits its turn
    j = src.index("/* prior-copy */")
    q = src[j:j + 1200]
    assert "ORDER BY (asset = $3) DESC, (status = 'submitting') DESC" in q
    assert "AND placed_at > now() - interval '48 hours'" in q


def test_an_add_leg_never_rests():
    src = inspect.getsource(live_executor.maybe_execute)
    i = src.index("_r = await _rest_after_ioc(")
    assert 'locals().get("add_of") is None and not (' in src[i - 400:i]


def test_the_partial_exit_write_is_relative_to_the_rows_current_shares():
    """REVIEW ROUND ONE: an absolute remainder from a pre-claim read
    erased a leg merged between the read and the write."""
    src = inspect.getsource(live_executor.mirror_exit)
    i = src.index("filled_shares=GREATEST(filled_shares - $2::float8, 0)")
    assert "orig_shares=COALESCE(orig_shares, filled_shares::float8)" in src[i - 200:i + 200]
    assert "filled_shares=$2, orig_shares=COALESCE(orig_shares, $4)" not in src


def test_the_daily_cap_counts_legs_on_their_own_clock():
    src = inspect.getsource(live_executor._caps_room)
    assert "jsonb_array_elements(COALESCE(lo2.raw->'adds', '[]'::jsonb)) a" in src
    assert "lo2.placed_at <= now() - interval '24 hours'" in src
    assert "(a->>'ts')::float8 > extract(epoch FROM now() - interval '24 hours')" in src
    rest = inspect.getsource(live_executor._rest_lane_spent)
    assert "jsonb_array_elements(COALESCE(lo2.raw->'adds', '[]'::jsonb)) a" in rest


def test_migration_045_splits_the_asset_claim_and_names_the_status():
    sql = pathlib.Path(live_executor.__file__).parents[1].joinpath(
        "migrations", "045_add_legs.sql").read_text()
    assert "'merged'" in sql and "live_orders_status_check" in sql
    i = sql.index("CREATE UNIQUE INDEX IF NOT EXISTS live_orders_one_fill_per_asset")
    fill = sql[i:sql.index(";", i)]
    assert "WHERE status IN ('filled', 'settled')" in fill
    assert "'submitting'" not in fill
    j = sql.index("CREATE UNIQUE INDEX IF NOT EXISTS live_orders_one_inflight_per_asset")
    inflight = sql[j:sql.index(";", j)]
    assert "WHERE status = 'submitting'" in inflight
    assert "NOT IN ('manual', 'underdog')" in inflight
    # the executor treats the in-flight index like the fill index: a
    # concurrent duplicate stopped at the INSERT is the guard working
    src = inspect.getsource(live_executor.maybe_execute)
    assert '"live_orders_one_inflight_per_asset" in str(exc)' in src


def test_a_merged_leg_is_neither_retried_nor_a_refusal():
    sweep = pathlib.Path(live_executor.__file__).with_name("workers").joinpath(
        "copy_sweep.py").read_text()
    assert "OR lo2.status = 'merged'" in sweep
    from sportsassets.analytics import gate_edge, lane_exec
    src = inspect.getsource(gate_edge.cohort_gate_edge)
    # a merged leg is a TAKEN trade (review round two): it joins the
    # taken cohort by status and never the refused one (its status is
    # not 'rejected', so gate_of never runs on it)
    assert "'cashed_out', 'exiting', 'merged')" in src
    rows_ge = [{"status": "merged", "error": "add-merged: +50 @ 0.6", "size": 50.0,
                "price": 0.6, "payout": 1.0, "event_key": "g1"}]
    out_ge = gate_edge.score([], rows_ge)
    assert out_ge["taken_at_his_price"]["taken"] == 1 and out_ge["n_refused_scored"] == 0
    rows = [{"lane": "chain", "status": "merged", "filled_usd": 0.0, "reaction_s": 1.0,
             "det_lag": 0.2, "his_ts": 1000.0, "t_send": 1001.0, "t_reply": 1001.7,
             "stake": 30.0, "pnl": None, "event_key": "g1", "settled": False}]
    out = lane_exec.summarize(rows)
    assert out["lanes"]["chain"]["filled"] == 1 and out["lanes"]["chain"]["attempts"] == 1
    app = pathlib.Path(live_executor.__file__).with_name("api").joinpath("app.py").read_text()
    assert "status IN ('filled', 'settled', 'merged'))::int AS fills" in app
    assert "abs(pnl) > $2::float8" in app and "abs(COALESCE(pnl, 0)) > $2::float8" in app


# ------------------------------------------------------------ the reaper

class _ReaperPool:
    """A leg row (id 55) that names standing row 7 on its raw."""

    def __init__(self, standing_status="filled", leg_figures=True, merge_hits=True):
        self.standing_status = standing_status
        self.leg_figures = leg_figures
        self.merge_hits = merge_hits
        self.ex, self.merges = [], []

    async def fetchrow(self, sql, *a):
        s = " ".join(sql.split())
        if "/* add-leg */" in s:
            return {"add_of": "7", "trade_id": 1, "requested_usd": 33.0,
                    "his_price": 0.55, "raw": json.dumps({"add_of": 7, "t_send": 1.0})}
        if "/* add-standing */" in s:
            return {"status": self.standing_status, "adds": None}
        if "/* named-standing */" in s:
            return {"status": self.standing_status, "settled_at": None}
        if "/* named-leg */" in s:
            return ({"id": 55, "order_id": "ioc-9", "add_of": "7",
                     "filled_shares": 50.0 if self.leg_figures else 0.0,
                     "fill_price": 0.6, "filled_usd": 30.0 if self.leg_figures else 0.0})
        if "SET status = 'merged'" in s:
            self.merges.append((s, a))
            return {"shares": 150.0, "px": 0.53, "usd": 80.0} if self.merge_hits else None
        return None

    async def fetchval(self, sql, *a):
        if "SELECT tx_hash FROM trades" in sql:
            return "0xbbb"
        if "SELECT status FROM live_orders WHERE id = $1" in sql:
            return self.standing_status
        return None

    async def execute(self, sql, *a):
        self.ex.append((" ".join(sql.split()), a))


def test_the_reaper_merges_a_reconciled_add_leg_onto_its_standing_row():
    p = _ReaperPool(standing_status="filled")
    ok = asyncio.run(live_executor._book_add_leg_if_any(p, 55, 50.0, 0.6, 30.0, "ioc-9", "t"))
    assert ok is True and len(p.merges) == 1 and p.ex == []
    sql, args = p.merges[0]
    assert args[:8] == (55, 7, 50.0, 0.6, 30.0, 33.0, args[6], "ioc-9")
    leg = json.loads(args[6])[0]
    assert leg["tx_hash"] == "0xbbb" and leg["his_price"] == 0.55 and leg["trade_id"] == "1"
    assert json.loads(args[8])["t_send"] == 1.0, "the leg's own receipt survives"
    # not an add leg at all: None, nothing written
    class _Plain(_ReaperPool):
        async def fetchrow(self, sql, *a):
            if "/* add-leg */" in sql:
                return {"add_of": None, "trade_id": 1, "requested_usd": 1.0,
                        "his_price": 0.5, "raw": "{}"}
            return await super().fetchrow(sql, *a)
    p2 = _Plain()
    assert asyncio.run(live_executor._book_add_leg_if_any(p2, 55, 50.0, 0.6, 30.0, "o", "t")) is None
    assert p2.ex == [] and p2.merges == []


def test_the_reaper_names_a_leg_with_its_figures_when_the_standing_row_is_not_filled():
    """REVIEW ROUND ONE (major): the crashed leg's fill figures were
    lost. They are recorded under the orphan name, never beside the row."""
    p = _ReaperPool(standing_status="exiting")
    ok = asyncio.run(live_executor._book_add_leg_if_any(p, 55, 50.0, 0.6, 30.0, "ioc-9", "why"))
    assert ok is False and p.merges == [] and len(p.ex) == 1
    sql, args = p.ex[0]
    assert "SET status='error', order_id=COALESCE($6, order_id)" in sql
    assert "filled_shares=$2, fill_price=$3, filled_usd=$4, error=$5" in sql
    assert args[0] == 55 and args[1:4] == (50.0, 0.6, 30.0) and args[5] == "ioc-9"
    assert args[4].startswith("ORPHAN FILL RECORDED") and "add leg of row #7" in args[4]
    assert args[4].endswith("— why")


def test_a_named_add_leg_merges_waits_or_falls_through_by_its_standing_row():
    r = {"id": 55, "add_of": "7"}
    # standing 'filled' again -> merged
    p = _ReaperPool(standing_status="filled")
    assert asyncio.run(live_executor._merge_named_add_leg(p, r)) is True
    assert len(p.merges) == 1
    # standing live but not 'filled' -> wait (never promote beside it);
    # 'settled' retires the leg instead (its own test below)
    for st in ("exiting", "submitting"):
        p = _ReaperPool(standing_status=st)
        assert asyncio.run(live_executor._merge_named_add_leg(p, r)) is None, st
        assert p.merges == []
    # standing gone -> the promotion path
    p = _ReaperPool(standing_status="cashed_out")
    assert asyncio.run(live_executor._merge_named_add_leg(p, r)) is False
    # a named leg without figures beside a LIVE standing row waits (review
    # round two): it is never promoted beside that row
    p = _ReaperPool(standing_status="filled", leg_figures=False)
    assert asyncio.run(live_executor._merge_named_add_leg(p, r)) is None
    # the reaper's orphan branch honours the tri-state
    src = inspect.getsource(live_executor._reap_one_submitting_row)
    i = src.index('if err.startswith("ORPHAN FILL RECORDED"):')
    assert "if await _merge_named_add_leg(pool, r) is not False:" in src[i:i + 1200]
    # and both reconcile sites book an add leg through the merge
    assert inspect.getsource(live_executor._reconcile_row_by_id).count("_book_add_leg_if_any(") == 1
    assert inspect.getsource(live_executor._book_from_log_by_oid).count("_book_add_leg_if_any(") == 1


def test_a_leg_that_died_mid_flight_is_named_not_skipped_forever(monkeypatch):
    """The reaper cannot book a crashed add leg 'filled' beside its
    standing row (the one-fill index refuses). The last-resort catch
    names the row so it leaves the in-flight claim."""
    class _P:
        def __init__(self):
            self.ex = []

        async def fetch(self, sql, *a):
            return [{"id": 55, "order_id": None, "us_market_slug": "s",
                     "requested_shares": 50.0, "status": "submitting", "error": None,
                     "his_price": 0.5, "limit_price": 0.52, "whale_username": "rn1",
                     "pre_ids": None, "add_of": "7", "age_s": 900.0, "intent": None}]

        async def execute(self, sql, *a):
            self.ex.append((" ".join(sql.split()), a))

    async def boom(pool, pmus, r):
        raise RuntimeError('duplicate key value violates unique constraint '
                           '"live_orders_one_fill_per_asset"')

    monkeypatch.setattr(live_executor, "_reap_one_submitting_row", boom)
    pool = _P()
    asyncio.run(live_executor._reap_stale_submitting(pool))
    assert len(pool.ex) == 1
    sql, args = pool.ex[0]
    assert "SET status='error', error=$2 WHERE id=$1 AND status='submitting'" in sql
    assert args[0] == 55 and args[1].startswith("ORPHAN FILL RECORDED")
    assert "add leg of row #7" in args[1]
    # a non-add row raising the same way is still just skipped
    class _P2(_P):
        async def fetch(self, sql, *a):
            r = (await super().fetch(sql))[0]
            return [{**r, "add_of": None}]
    pool2 = _P2()
    asyncio.run(live_executor._reap_stale_submitting(pool2))
    assert pool2.ex == []


def test_merge_helper_shape():
    class _P:
        def __init__(self):
            self.calls = []

        async def fetchrow(self, sql, *a):
            self.calls.append((" ".join(sql.split()), a))
            return None

    p = _P()
    ok = asyncio.run(live_executor._merge_add_leg(
        p, 101, 7, 50.0, 0.6, 30.0, 33.0, "ioc-9", "1",
        {"response": {}, "t_send": 1.0, "t_reply": 1.7}, 2, tx_hash="0xABC",
        his_price=0.55))
    assert ok is False and len(p.calls) == 1
    sql, args = p.calls[0]
    assert sql.startswith("WITH leg AS (")
    assert "), s AS ( UPDATE live_orders SET fill_price = CASE" in sql
    assert "FROM s WHERE l.id = $1" in sql
    assert args[:6] == (101, 7, 50.0, 0.6, 30.0, 33.0)
    leg = json.loads(args[6])[0]
    assert leg["order_id"] == "ioc-9" and leg["tx_hash"] == "0xabc" and leg["his_price"] == 0.55
    assert args[7] == "ioc-9"
    assert json.loads(args[8])["add_of"] == 7
    assert args[9] == "add-merged: +50 @ 0.6 ($30.00) into row #7 — leg 2 of 3"
    # IDEMPOTENT ON THE LEG (review round two): a lost reply or two reaper
    # passes must not book the leg twice
    assert "WITH leg AS (" in sql and "FOR UPDATE" in sql
    assert "AND EXISTS (SELECT 1 FROM leg)" in sql
    assert "@> jsonb_build_array(jsonb_build_object('row_id', $1::bigint))" in sql
    assert "WHERE l.id = $1 AND l.status IN ('submitting', 'error')" in sql


# ------------------------------------------------- review round two

def test_a_holder_older_than_the_never_add_window_retires_the_asset(monkeypatch):
    """The two referees keep one clock: never-add judges rows placed
    within 48 h, so an older holder must stop the copy here (as before
    adds) rather than let it run as a fresh copy with no add declared."""
    pool = _AddPool(standing={**STANDING, "recent": False})
    calls = _run(monkeypatch, pool)
    assert calls == [] and pool.merges == []
    assert not [u for u in pool.updates if "INSERT" in u[0]]


def test_the_reaper_reconciles_an_add_leg_by_id_through_the_merge(monkeypatch):
    """Drive _reconcile_row_by_id with add_of set: the venue says filled,
    and the fill is booked by the merge statement, never by a bare
    'filled' write beside the standing row."""
    class _P(_ReaperPool):
        def __init__(self):
            super().__init__(standing_status="filled")
            self.ex = []

        async def execute(self, sql, *a):
            self.ex.append((" ".join(sql.split()), a))

    class _Pmus:
        @staticmethod
        def cancel_order(oid, slug=None):
            return {"ok": True}

        @staticmethod
        def order_status(oid):
            return {"order_id": oid, "filled_shares": 50.0, "avg_px": 0.6,
                    "state": "filled", "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"}

    async def _sleep(s):
        pass
    monkeypatch.setattr(live_executor.asyncio, "sleep", _sleep)
    p = _P()
    res = asyncio.run(live_executor._reconcile_row_by_id(
        p, _Pmus, 55, "ioc-9", "s", "ORDER_INTENT_BUY_LONG", 900.0, add_of="7"))
    assert res == "filled"
    assert len(p.merges) == 1 and p.merges[0][1][:2] == (55, 7)
    assert not [e for e in p.ex if "SET status='filled'" in e[0]], \
        "an add leg is never booked 'filled' beside its standing row"
    # without add_of the old path is untouched
    p2 = _P()
    res2 = asyncio.run(live_executor._reconcile_row_by_id(
        p2, _Pmus, 56, "ioc-8", "s", "ORDER_INTENT_BUY_LONG", 900.0))
    assert res2 == "filled" and p2.merges == []
    assert [e for e in p2.ex if "SET status='filled'" in e[0]]


def test_the_lost_bid_adopter_books_an_add_leg_through_the_merge():
    src = inspect.getsource(live_executor._adopt_lost_bid)
    i = src.index("reconciled from the venue trade log: lost order of our exact")
    assert "_book_add_leg_if_any(" in src[i:i + 900]
    assert "if _booked is None:" in src[i:i + 900]


def test_a_named_leg_beside_a_settled_row_is_retired_settled_at_zero():
    r = {"id": 55, "add_of": "7"}
    p = _ReaperPool(standing_status="settled")
    assert asyncio.run(live_executor._merge_named_add_leg(p, r)) is True
    assert p.merges == []
    sql, args = p.ex[-1]
    assert "SET status='settled', pnl=0" in sql and args[0] == 55
    assert "standing row #7 settled before the merge" in args[2]
    # a figure-less named leg beside a LIVE standing row waits; it is
    # never promoted beside it
    p2 = _ReaperPool(standing_status="exiting", leg_figures=False)
    assert asyncio.run(live_executor._merge_named_add_leg(p2, r)) is None
    p3 = _ReaperPool(standing_status="filled", leg_figures=False)
    assert asyncio.run(live_executor._merge_named_add_leg(p3, r)) is None


def test_the_exit_claim_is_the_authoritative_read():
    src = inspect.getsource(live_executor.mirror_exit)
    i = src.index("UPDATE live_orders SET status='exiting' ")
    blk = src[i:i + 1200]
    # after the claim the row is 'exiting' and a merge needs 'filled', so
    # the post-claim read is final; sizing and the base use it
    assert "FROM live_orders WHERE id = $1 /* post-claim */" in blk
    assert "COALESCE(orig_shares, filled_shares)::float8 AS orig_qty" in blk
    assert '_fresh["qty"] = float(_q)' in blk
    assert src.index('_fresh = dict(row)') < src.index('_orig = int(_row_get(row, "orig_qty")')


def test_the_stale_exit_reaper_subtracts_shares_other_rows_explain():
    src = inspect.getsource(live_executor._reap_stale_exiting)
    assert "error LIKE 'ORPHAN FILL RECORDED%'" in src
    assert "if held - _explained < 1:" in src


def test_the_volume_governor_counts_legs_on_their_own_clock():
    src = inspect.getsource(live_executor.volume_normalized_clip)
    assert "jsonb_array_elements(COALESCE(lo2.raw->'adds', '[]'::jsonb)) a" in src
    assert "lo2.placed_at <= now() - interval '24 hours'" in src
    assert "lower(COALESCE(lo2.whale_username, '')) = $1" in src

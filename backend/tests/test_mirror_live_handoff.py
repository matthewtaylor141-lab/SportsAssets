"""Mirror P1, step 7b: the hand-off from the per-fill lane to the book.

Position mirroring (owner order 2026-09-02, "go for it, let's get this
working") holds one standing book per market for a whale the deploy
names, and the per-fill lane must stand aside for him on EVERY market
-- whole-whale, because the panel review weighed a per-market rule and
refused it: it races on the market the mirror is about to open and
leaves two regimes exiting one whale (spec 3.1). The rule is one gate
in maybe_execute, and where it stands is the whole of its meaning:

  BELOW the exit dispatch   his pre-mirror per-fill rows (lane NULL,
                            'ioc', 'rest') are still classified and sold
                            by classify_exit -> mirror_exit as before
  ABOVE every entry gate,   no per-fill dollar leaves for a mirrored
  the sizing, the INSERT    whale from either caller: the fresh path
  and both submit sites     (execute_copy) or copy_sweep's reclaim call

The predicate (mirror_mode) reads the environment only, so the gate
cannot fail open on a read; the wake it sends the reconciler is a
courtesy that must never raise, because the refusal that follows it is
the money decision. copy_sweep subtracts mirrored whales from the
roster its candidate query reads -- a belt, so refusals do not ration
the pass -- and the exit census endpoint can name mx_mirror_owns_market
beside the coverage line it keeps clean (spec 3.2).

Three kinds of pin, the way the neighbouring step files pin theirs:

1. SOURCE: the gate stands between the two lines the spec names, once,
   with nothing but comments around it, and before every gate below;
2. BEHAVIOUR: maybe_execute is driven through the rest-lane harness
   (test_rest_lane_end_to_end._Pool/_wire) on both callers with the
   switch on -- refused by name, no row, no venue call -- and with the
   switch off, where the very same payload places today's ladder (the
   control that proves the harness has teeth);
3. the EXIT: a classified exit for the same whale's legacy 'ioc' row
   still reaches mirror_exit and claims it while the gate is armed.

Nothing here places an order except the switch-off controls, and those
place against the harness's fakes.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import re
import sys
import textwrap
import types

import pytest

from sportsassets import live_executor as le
from sportsassets import pmus
from sportsassets.workers import copy_sweep as cs
from tests.test_rest_lane_end_to_end import TODAY, _Pool, _payload, _wire

MIRROR_LIVE = "sportsassets.workers.mirror_live"
SLUG = f"tsc-epl-ars-che-{TODAY}-o3pt5"
EXIT_PENDING_LINE = 'return _copy_stop("was_an_exit_pending", username)'
GATE_LINE = "if mirror_mode(username):"
ROSTER_LINE = ('if payload.get("side") != "BUY" or username not in '
               "cfg.source_whales():")
GATE_BLOCK = ('if mirror_mode(username): '
              '_mirror_notify(payload.get("condition_id")) '
              'return _copy_stop("mirror_mode", username)')


def _code(src: str) -> str:
    """Comment lines removed: a gate named only in prose cannot pass."""
    return "\n".join(ln for ln in src.splitlines()
                     if not ln.strip().startswith(("#", "--")))


def _src(fn) -> str:
    return _code(inspect.getsource(fn))


def _pos(text: str, needle: str) -> int:
    assert needle in text, f"not found: {needle!r}"
    return text.index(needle)


def _body(fn) -> str:
    """The function's statements with the docstring removed, unparsed,
    so a word in the prose cannot satisfy or fail a pin on the code."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    node = tree.body[0]
    stmts = node.body
    if (stmts and isinstance(stmts[0], ast.Expr)
            and isinstance(stmts[0].value, ast.Constant)
            and isinstance(stmts[0].value.value, str)):
        stmts = stmts[1:]
    return "\n".join(ast.unparse(s) for s in stmts)


def _census(reason: str, whale: str = "rn1") -> int:
    return le._COPY_CENSUS.get(f"{reason}|{whale}", 0)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("PMUS_MIRROR", "PMUS_MIRROR_WHALES", "MIRROR_WHALES"):
        monkeypatch.delenv(k, raising=False)


def _mirror(monkeypatch, whales: str = "rn1", mode: str = "on") -> None:
    monkeypatch.setenv("PMUS_MIRROR", mode)
    monkeypatch.setenv("PMUS_MIRROR_WHALES", whales)


# ───────────────────────────── 1. the source pins ─────────────────────────────

class TestTheGateStandsWhereTheSpecPutsIt:
    def test_between_the_exit_dispatch_and_the_entry_roster_and_nothing_else(self):
        """Spec 3.1: immediately after the dispatch block's
        was_an_exit_pending return, before the side/roster line. Once
        comments are gone the gate is the ONLY code between the two."""
        code = _src(le.maybe_execute)
        i, j, k = (_pos(code, EXIT_PENDING_LINE), _pos(code, GATE_LINE),
                   _pos(code, ROSTER_LINE))
        assert i < j < k
        between = code[i + len(EXIT_PENDING_LINE):k]
        assert " ".join(between.split()) == GATE_BLOCK

    def test_below_the_classifier_the_dispatch_and_the_pool(self):
        s = _src(le.maybe_execute)
        g = _pos(s, GATE_LINE)
        assert _pos(s, "pool = await get_pool()") < _pos(s, "classify_exit(") \
            < _pos(s, "await mirror_exit(") < g

    def test_above_every_entry_gate_the_sizing_the_insert_and_both_submit_sites(self):
        s = _src(le.maybe_execute)
        g = _pos(s, GATE_LINE)
        below = [
            '_copy_stop("not_buy_or_off_roster"',
            '_copy_stop("whale_cut"',
            "overspend_halt(pool)",
            "edge_gate.verdict(",
            "if not copy_allowed(",
            "_game_too_far_out(mslug)",
            '_copy_stop("already_taken"',
            "/* add-holder */",
            "/* prior-copy */",
            "_loss_breaker_tripped(pool)",
            "_copy_day_room(pool, cfg)",
            "volume_normalized_clip(",
            "INSERT INTO live_orders",
            "_mapping_admitted(",
            "_ioc_guarded(",
            "_rest_after_ioc(",
        ]
        for needle in below:
            assert g < _pos(s, needle), needle
        # every reference to the venue's order call is past the gate
        subs = [m.start() for m in re.finditer(r"pmus\.submit_fok", s)]
        assert subs and all(g < p for p in subs)

    def test_the_predicate_the_wake_and_the_reason_appear_exactly_once(self):
        code = _src(le.maybe_execute)
        assert code.count("mirror_mode(") == 1
        assert code.count("_mirror_notify(") == 1
        assert code.count('_copy_stop("mirror_mode"') == 1

    def test_the_reason_is_counted_before_any_row_exists(self):
        """The funnel census (test_copy_census_sees_the_whole_funnel)
        counts every pre-INSERT return; this one carries the whale."""
        s = _src(le.maybe_execute)
        region = s[:_pos(s, "INSERT INTO live_orders")]
        assert re.search(r'_copy_stop\("mirror_mode", username\)', region)

    def test_the_predicate_reads_no_database(self):
        """The spec's grep: an env-only predicate cannot fail open on a
        read. test_mirror_live_helpers pins the same for the helper
        pair; this is the gate's own reading of it."""
        for fn in (le.mirror_mode, le.mirror_allowlist):
            body = _body(fn)
            for bad in ("ingestion_state", "await", "pool", "fetch", "settings("):
                assert bad not in body, (fn.__name__, bad)
        assert le.mirror_mode("rn1") is False, "unset env: off for everyone"


class TestTheWakeHelperCannotRaiseOrTrade:
    def test_it_imports_the_worker_lazily_and_swallows_its_absence(self):
        body = _body(le._mirror_notify)
        assert "from .workers import mirror_live" in body
        # ModuleNotFoundError (an ImportError) for the worker's own name is
        # the quiet absence; any other import failure is named in the log
        assert "except ModuleNotFoundError" in body
        assert body.index("except ModuleNotFoundError") < body.index(".notify(")
        assert not inspect.iscoroutinefunction(le._mirror_notify)

    def test_the_worker_is_never_imported_at_module_load(self):
        head = inspect.getsource(le).split("\ndef ", 1)[0]
        assert "mirror_live" not in head
        assert MIRROR_LIVE not in sys.modules or sys.modules[MIRROR_LIVE] is None

    def test_it_touches_no_pool_and_no_venue(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(le._mirror_notify)))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        for bad in ("pool", "pmus", "get_pool"):
            assert bad not in names, bad
        for bad in ("submit_fok", "close_position", "fetchval", "fetchrow",
                    "fetch", "execute", "cancel_order"):
            assert bad not in attrs, bad
        assert not any(isinstance(n, (ast.Await, ast.AsyncFunctionDef))
                       for n in ast.walk(tree))

    def test_it_only_calls_the_workers_notify(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(le._mirror_notify)))
        called = [ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)]
        assert called.count("_ml.notify") == 1, called
        assert set(called) <= {"_ml.notify", "log.warning"}, called


# ───────────────────────────── 2. the wake helper ─────────────────────────────

def _absent_worker(monkeypatch):
    """`from .workers import mirror_live` resolves the PACKAGE ATTRIBUTE
    before sys.modules (step 7b review): once the real module has been
    imported anywhere, sys.modules alone cannot make it absent. Both
    have to say so."""
    import sportsassets.workers as _wpkg
    monkeypatch.setitem(sys.modules, MIRROR_LIVE, None)
    monkeypatch.delattr(_wpkg, "mirror_live", raising=False)


def _fake_worker(monkeypatch, notify):
    import sportsassets.workers as _wpkg
    mod = types.ModuleType(MIRROR_LIVE)
    mod.notify = notify
    monkeypatch.setitem(sys.modules, MIRROR_LIVE, mod)
    monkeypatch.setattr(_wpkg, "mirror_live", mod, raising=False)
    return mod


class TestTheWakeHelperBehaviour:
    def test_no_worker_module_is_a_no_op(self, monkeypatch):
        """None in sys.modules is Python's own "this module is absent"
        marker (the import raises ModuleNotFoundError), which holds
        whether or not step 9's file has landed in the tree."""
        _absent_worker(monkeypatch)
        assert le._mirror_notify("0xc") is None
        assert le._mirror_notify(None) is None

    def test_a_present_worker_is_woken_with_the_condition(self, monkeypatch):
        calls: list = []
        _fake_worker(monkeypatch, calls.append)
        assert le._mirror_notify("0xc") is None
        assert calls == ["0xc"]

    def test_a_wake_that_raises_is_named_and_swallowed(self, monkeypatch, caplog):
        def boom(_cid):
            raise RuntimeError("wake table missing")
        _fake_worker(monkeypatch, boom)
        with caplog.at_level(logging.WARNING, logger="sportsassets.live_executor"):
            assert le._mirror_notify("0xc") is None
        assert any("mirror wake failed for 0xc" in r.message for r in caplog.records)

    def test_a_worker_without_notify_is_swallowed_too(self, monkeypatch):
        mod = _fake_worker(monkeypatch, None)
        del mod.notify
        assert le._mirror_notify("0xc") is None

    def test_a_package_attribute_alone_is_honoured(self, monkeypatch):
        """The import form's real resolution order: a fake left on the
        package attribute is what the gate calls, whatever sys.modules
        says -- so the absent tests above have to clear both."""
        import sportsassets.workers as _wpkg
        calls: list = []
        mod = types.ModuleType(MIRROR_LIVE)
        mod.notify = calls.append
        monkeypatch.setattr(_wpkg, "mirror_live", mod, raising=False)
        monkeypatch.setitem(sys.modules, MIRROR_LIVE, None)
        assert le._mirror_notify("0xc") is None
        assert calls == ["0xc"]

    def test_a_missing_dependency_inside_the_worker_is_named(self, monkeypatch, tmp_path, caplog):
        """ModuleNotFoundError for ANOTHER name is a bad deploy of the
        worker, not its absence: named in the log, wake dropped."""
        import sportsassets.workers as _wpkg
        _absent_worker(monkeypatch)
        (tmp_path / "mirror_live.py").write_text("import no_such_dependency_xyz\n")
        monkeypatch.setattr(_wpkg, "__path__", [str(tmp_path)] + list(_wpkg.__path__))
        monkeypatch.delitem(sys.modules, MIRROR_LIVE, raising=False)
        with caplog.at_level(logging.WARNING, logger="sportsassets.live_executor"):
            assert le._mirror_notify("0xc") is None
        assert any("would not import" in r.message for r in caplog.records)
        monkeypatch.delitem(sys.modules, MIRROR_LIVE, raising=False)
        monkeypatch.delattr(_wpkg, "mirror_live", raising=False)

    def test_a_worker_module_that_fails_to_import_is_named_and_swallowed(
            self, monkeypatch, tmp_path, caplog):
        """Not an ImportError: a module that exists and raises while it
        loads (a bad deploy of step 9) must not turn a refused copy into
        an exception. A real file on the package path, so the import
        machinery itself is what raises."""
        from sportsassets import workers as _pkg

        (tmp_path / "mirror_live.py").write_text(
            'raise RuntimeError("bad deploy")\n')
        monkeypatch.delitem(sys.modules, MIRROR_LIVE, raising=False)
        monkeypatch.setattr(_pkg, "__path__", [str(tmp_path)] + list(_pkg.__path__))
        with caplog.at_level(logging.WARNING, logger="sportsassets.live_executor"):
            assert le._mirror_notify("0xc") is None
        assert any("would not import" in r.message for r in caplog.records)
        assert MIRROR_LIVE not in sys.modules


# ───────────────────────────── 3. the harness ─────────────────────────────────

class _HandoffPool(_Pool):
    """The rest-lane harness's pool, recording every statement and the
    live_orders INSERTs, which are the one write the gate must
    prevent."""

    def __init__(self):
        super().__init__()
        self.inserts: list = []
        self.sqls: list[str] = []

    async def fetchval(self, sql, *a):
        s = " ".join(sql.split())
        self.sqls.append(s)
        if "INSERT INTO live_orders" in s:
            self.inserts.append(a)
        return await super().fetchval(sql, *a)

    async def fetchrow(self, sql, *a):
        self.sqls.append(" ".join(sql.split()))
        return await super().fetchrow(sql, *a)

    async def fetch(self, sql, *a):
        self.sqls.append(" ".join(sql.split()))
        return await super().fetch(sql, *a)


def _no_exit(monkeypatch):
    """classify_exit answering "not an exit", recording that it was
    asked: the dispatch precedes the gate in behaviour, not only in
    source. (The real classifier would reach for the sibling map and
    Gamma on this fixture's bare pool, which is not what these tests
    are about.)"""
    asked: list = []

    async def fake(_pool, asset, whale, size, trade_id=None):
        asked.append((asset, whale))
        return None

    monkeypatch.setattr(le, "classify_exit", fake)
    return asked


def _drive(monkeypatch, pool, reaction=5.0, **over):
    calls = _wire(monkeypatch, pool, SLUG, gtc_final_filled=100.0)
    asked = _no_exit(monkeypatch)
    out = asyncio.run(le.maybe_execute(_payload(**over), reaction))
    return out, calls, asked


def _placed_todays_ladder(calls, pool) -> None:
    """test_rest_lane_end_to_end's own expectation of this harness: the
    empty IOC, the GTC at his wire, the cancel, the read; one INSERT."""
    assert [c[0] for c in calls] == ["place", "place", "cancel", "status"], calls
    assert len(pool.inserts) == 1


class TestAMirroredWhalesBuyNeverLeavesADollar:
    @pytest.mark.parametrize("mode", ["on", "exits"])
    def test_the_fresh_detection_call_is_refused_by_name(self, monkeypatch, mode):
        _mirror(monkeypatch, mode=mode)
        pool = _HandoffPool()
        before = _census("mirror_mode")
        out, calls, asked = _drive(monkeypatch, pool)
        assert out is None
        assert calls == [] and pool.inserts == [] and pool.updates == []
        assert _census("mirror_mode") == before + 1
        assert asked == [("123", "rn1")], "classified first, refused second"
        # nothing past the gate ran: no referee, room, ladder or caps read
        assert not [s for s in pool.sqls if "live_orders" in s]

    def test_the_reclaim_call_with_reaction_none_is_refused_the_same_way(self, monkeypatch):
        """copy_sweep calls maybe_execute(payload, None) with its own
        payload shape (sweep_recovery); the gate reads neither."""
        _mirror(monkeypatch)
        pool = _HandoffPool()
        before = _census("mirror_mode")
        out, calls, _ = _drive(monkeypatch, pool, reaction=None,
                               sweep_recovery=True, tx_hash="0xabc",
                               ts_epoch=1.0, sport="soccer", event_title=None)
        assert out is None and calls == [] and pool.inserts == []
        assert _census("mirror_mode") == before + 1

    def test_the_fresh_path_through_execute_copy(self, monkeypatch):
        """ingestion/pipeline -> execute_copy -> maybe_execute: the
        semaphore, the reaction stamp and the staleness ceiling all run
        and the gate still answers before any row exists."""
        _mirror(monkeypatch)
        pool = _HandoffPool()
        calls = _wire(monkeypatch, pool, SLUG, gtc_final_filled=100.0)
        asked = _no_exit(monkeypatch)
        before = _census("mirror_mode")
        assert asyncio.run(le.execute_copy(_payload())) is None
        assert calls == [] and pool.inserts == []
        assert asked == [("123", "rn1")]
        assert _census("mirror_mode") == before + 1

    def test_the_wake_carries_the_condition_and_precedes_the_count(self, monkeypatch):
        _mirror(monkeypatch)
        seen: list = []
        _fake_worker(monkeypatch, lambda cid: seen.append((cid, _census("mirror_mode"))))
        before = _census("mirror_mode")
        pool = _HandoffPool()
        _drive(monkeypatch, pool)
        assert seen == [("0xc", before)]
        assert _census("mirror_mode") == before + 1

    def test_with_the_worker_absent_the_refusal_stands_alone(self, monkeypatch):
        _mirror(monkeypatch)
        _absent_worker(monkeypatch)
        pool = _HandoffPool()
        before = _census("mirror_mode")
        out, calls, _ = _drive(monkeypatch, pool)
        assert out is None and calls == [] and pool.inserts == []
        assert _census("mirror_mode") == before + 1


class TestWithTheSwitchOffTheSamePayloadTakesTodaysPath:
    def test_an_unset_env_places_the_ladder(self, monkeypatch):
        """The control, and the harness's proof of teeth: the exact
        payload refused above places today's IOC -> GTC ladder."""
        seen: list = []
        _fake_worker(monkeypatch, seen.append)
        pool = _HandoffPool()
        before = _census("mirror_mode")
        out, calls, asked = _drive(monkeypatch, pool)
        _placed_todays_ladder(calls, pool)
        assert asked == [("123", "rn1")]
        assert seen == [], "no wake without the switch"
        assert _census("mirror_mode") == before

    @pytest.mark.parametrize("env", [
        {"PMUS_MIRROR": "off", "PMUS_MIRROR_WHALES": "rn1"},
        {"PMUS_MIRROR": "on"},                                   # nobody named
        {"PMUS_MIRROR": "on", "PMUS_MIRROR_WHALES": "swisstony"},
        {"PMUS_MIRROR": "on", "PMUS_MIRROR_WHALES": "rn1",
         "MIRROR_WHALES": "swisstony"},                          # outside the shadow set
        {"PMUS_MIRROR": "shadow", "PMUS_MIRROR_WHALES": "rn1"},
    ])
    def test_every_other_setting_leaves_rn1_on_the_per_fill_lane(self, monkeypatch, env):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        pool = _HandoffPool()
        before = _census("mirror_mode")
        _, calls, _ = _drive(monkeypatch, pool)
        _placed_todays_ladder(calls, pool)
        assert _census("mirror_mode") == before

    def test_another_whale_is_untouched_while_rn1_is_mirrored(self, monkeypatch):
        """Whole-whale means THIS whale: a roster whale outside the
        allowlist walks past the gate to the entry gates below it (the
        fixture's swisstony is stopped there by his own cell gate,
        which is today's answer for him on this market)."""
        _mirror(monkeypatch, whales="rn1")
        monkeypatch.setitem(le.PER_FILL_BY_WHALE, "swisstony", 225.00)
        pool = _HandoffPool()
        before = {k: v for k, v in le._COPY_CENSUS.items() if k.endswith("|swisstony")}
        _, _, asked = _drive(monkeypatch, pool, whale_username="SwissTony")
        assert asked == [("123", "swisstony")]
        delta = {k.split("|")[0]: v - before.get(k, 0)
                 for k, v in le._COPY_CENSUS.items()
                 if k.endswith("|swisstony") and v != before.get(k, 0)}
        assert len(delta) == 1 and list(delta.values()) == [1], delta
        reason, = delta
        s = _src(le.maybe_execute)
        assert reason != "mirror_mode"
        # the site that counted him: a literal, or a prefix joined to a
        # clause name (the cell gate's "cell_gate_" + verdict)
        sites = [_pos(s, f'_copy_stop("{reason[:n]}"')
                 for n in range(len(reason), 0, -1)
                 if f'_copy_stop("{reason[:n]}"' in s]
        assert sites, reason
        assert _pos(s, GATE_LINE) < sites[0], \
            "his refusal must come from an entry gate BELOW the hand-off"


# ───────────────────────────── 4. the exit still sells ────────────────────────

class _Proceeded(BaseException):
    """Raised from the first collaborator past mirror_exit's claim:
    proof that the exit went on WITH the row. BaseException so no
    `except Exception` on the way out can turn it into a reason."""


class _LegacyExitPool(_HandoffPool):
    """rn1's pre-mirror row on the sibling token (lane 'ioc' by default)
    served to mirror_exit's lookup under its lane guard, the claim
    recorded, mirror_books answered from `owns`."""

    def __init__(self, lane="ioc", *, owns=False):
        super().__init__()
        self.lane = lane
        self.owns = owns
        self.claims: list = []

    async def fetchrow(self, sql, *a):
        s = " ".join(sql.split())
        self.sqls.append(s)
        if "age_s" in s and "'submitting'" in s:
            return None                          # nothing in flight
        if "FROM live_orders WHERE asset = $1" in s and "status = 'filled'" in s:
            assert "COALESCE(lane,'') <> 'mirror'" in s, "the exit lookup lost its lane guard"
            if a != ("456", "rn1") or self.lane == "mirror":
                return None
            return {"id": 7, "us_market_slug": SLUG, "qty": 100.0, "orig_qty": 100.0,
                    "entry": 0.5, "intent": "ORDER_INTENT_BUY_LONG"}
        if "FILTER (WHERE t.side='BUY')" in s:
            return {"bought": 1000.0, "sold": 0.0}
        return await super().fetchrow(sql, *a)

    async def fetchval(self, sql, *a):
        s = " ".join(sql.split())
        if "SET status='exiting'" in s:
            self.sqls.append(s)
            self.claims.append(a)
            return a[0]
        if "FROM mirror_books" in s:
            self.sqls.append(s)
            return self.owns
        return await super().fetchval(sql, *a)


def _classified(monkeypatch):
    """classify_exit: his BUY of token 123 closes all of his 456."""
    async def fake(_pool, asset, whale, size, trade_id=None):
        return {"asset": "456", "exit_via_asset": asset, "closed_frac": 1.0,
                "his_open_shares": 1000.0, "his_exit_shares": 1000.0}
    monkeypatch.setattr(le, "classify_exit", fake)


def _drive_exit(monkeypatch, pool):
    calls = _wire(monkeypatch, pool, SLUG, gtc_final_filled=100.0)
    _classified(monkeypatch)

    async def _held(_slug):
        raise _Proceeded()

    monkeypatch.setattr(le, "_pm_held", _held)
    monkeypatch.setattr(pmus, "close_position", lambda *a, **k: pytest.fail("close_position"))
    try:
        out = asyncio.run(le.maybe_execute(_payload(), 5.0))
    except _Proceeded:
        out = "proceeded"
    return out, calls


class TestAClassifiedExitStillReachesMirrorExit:
    @pytest.mark.parametrize("lane", ["ioc", "rest", None])
    def test_his_legacy_row_is_claimed_while_the_gate_is_armed(self, monkeypatch, lane):
        """The gate is BELOW the dispatch: with PMUS_MIRROR=on for rn1,
        his complement buy is classified, mirror_exit finds his
        pre-mirror row, claims it 'exiting' and goes on to the venue --
        and no entry is sized, inserted or placed."""
        _mirror(monkeypatch)
        pool = _LegacyExitPool(lane)
        before = _census("mirror_mode")
        out, calls = _drive_exit(monkeypatch, pool)
        assert out == "proceeded"
        assert pool.claims == [(7,)]
        assert calls == [] and pool.inserts == []
        assert _census("mirror_mode") == before, "an exit never reaches the gate"

    def test_the_dispatch_returns_was_an_exit_pending_not_mirror_mode(self, monkeypatch):
        """The same drive with mirror_exit as a recorder, so the return
        and the census can be read instead of the claim."""
        _mirror(monkeypatch)
        pool = _HandoffPool()
        calls = _wire(monkeypatch, pool, SLUG, gtc_final_filled=100.0)
        _classified(monkeypatch)
        seen: list = []

        async def rec(payload):
            seen.append((payload["side"], payload["asset"], payload["whale_username"]))
            return "mx_SOLD"

        monkeypatch.setattr(le, "mirror_exit", rec)
        b_exit, b_gate = _census("was_an_exit_pending"), _census("mirror_mode")
        assert asyncio.run(le.maybe_execute(_payload(), 5.0)) is None
        assert seen == [("SELL", "456", "RN1")]
        assert calls == [] and pool.inserts == []
        assert _census("was_an_exit_pending") == b_exit + 1
        assert _census("mirror_mode") == b_gate

    def test_a_vanish_on_the_books_own_token_is_named_and_never_claimed(self, monkeypatch):
        """His only row on the token is the book's standing row: the
        trade lane answers mx_mirror_owns_market (spec 3.2), claims
        nothing, and the BUY that carried the signal is not copied."""
        _mirror(monkeypatch)
        pool = _LegacyExitPool("mirror", owns=True)
        before = le._EXIT_CENSUS.get("mx_mirror_owns_market", 0)
        b_gate = _census("mirror_mode")
        out, calls = _drive_exit(monkeypatch, pool)
        assert out is None
        assert pool.claims == [] and calls == [] and pool.inserts == []
        assert le._EXIT_CENSUS.get("mx_mirror_owns_market", 0) == before + 1
        assert _census("mirror_mode") == b_gate
        assert "mx_mirror_owns_market" not in le.EXIT_PENDING_REASONS

    def test_with_the_switch_off_the_legacy_row_is_claimed_exactly_the_same(self, monkeypatch):
        pool = _LegacyExitPool("ioc")
        out, calls = _drive_exit(monkeypatch, pool)
        assert out == "proceeded" and pool.claims == [(7,)] and calls == []


# ───────────────────────────── 5. the sweep's belt ────────────────────────────

def _candidate(**over) -> dict:
    r = {"id": 1, "whale_id": 2, "whale_username": "RN1", "tx_hash": "0xabc",
         "asset": "123", "condition_id": "0xc", "side": "BUY", "outcome": "Over 3.5",
         "outcome_index": 0, "size": 909.0, "price": 0.55, "notional": 499.95,
         "market_title": None, "event_title": None, "market_slug": None,
         "event_slug": None, "sport": "soccer", "ts_epoch": 1.0}
    r.update(over)
    return r


class _SweepPool(_HandoffPool):
    """copy_sweep's pool: serves its candidate rows to the candidate
    query WHATEVER roster it is handed (the belt off, so the gate is
    proven to hold alone) and records the roster the query received."""

    def __init__(self, rows):
        super().__init__()
        self.rows = list(rows)
        self.rosters: list = []

    async def fetch(self, sql, *a):
        s = " ".join(sql.split())
        self.sqls.append(s)
        if "SELECT DISTINCT ON (t.asset)" in s:
            self.rosters.append(list(a[0]))
            return list(self.rows)
        return []


def _wire_sweep(monkeypatch, pool, roster=("rn1", "swisstony")):
    calls = _wire(monkeypatch, pool, SLUG, gtc_final_filled=100.0)
    asked = _no_exit(monkeypatch)

    async def _pool():
        return pool

    async def _zero(_p):
        return 0

    monkeypatch.setattr(cs, "get_pool", _pool)
    monkeypatch.setattr(cs, "settings", lambda: types.SimpleNamespace(
        source_whales=lambda: set(roster)))
    for reaper in ("_reap_stale_submitting", "_reap_stale_resting_bids",
                   "_reap_stale_exiting"):
        monkeypatch.setattr(le, reaper, _zero)
    return calls, asked


class TestTheSweepDropsMirroredWhalesFromItsPass:
    def test_the_roster_the_query_reads_omits_him(self, monkeypatch):
        _mirror(monkeypatch)
        pool = _SweepPool([])
        _wire_sweep(monkeypatch, pool)
        out = asyncio.run(cs.sweep_once())
        assert pool.rosters == [["swisstony"]]
        assert out["candidates"] == 0 and out["attempted"] == 0

    def test_with_the_switch_off_the_roster_is_what_it_was(self, monkeypatch):
        pool = _SweepPool([])
        _wire_sweep(monkeypatch, pool)
        asyncio.run(cs.sweep_once())
        assert pool.rosters == [["rn1", "swisstony"]]

    def test_a_row_of_his_that_reaches_the_pass_is_still_refused_by_the_gate(self, monkeypatch):
        """The belt switched off in the fixture (his row served anyway):
        maybe_execute(payload, None) refuses by name, writes no row."""
        _mirror(monkeypatch)
        pool = _SweepPool([_candidate()])
        calls, asked = _wire_sweep(monkeypatch, pool)
        before = _census("mirror_mode")
        out = asyncio.run(cs.sweep_once())
        assert out["attempted"] == 1 and out["processed"] == 1
        assert calls == [] and pool.inserts == []
        assert asked == [("123", "rn1")]
        assert _census("mirror_mode") == before + 1

    def test_with_the_switch_off_the_same_row_places_todays_order(self, monkeypatch):
        """The reclaim call carries reaction None, so today's path for a
        sweep row is the IOC alone (the rest lane, like adds, is guarded
        on a fresh detection): one order, one INSERT."""
        pool = _SweepPool([_candidate()])
        calls, _ = _wire_sweep(monkeypatch, pool)
        before = _census("mirror_mode")
        out = asyncio.run(cs.sweep_once())
        assert out["attempted"] == 1
        assert [c[0] for c in calls] == ["place"], calls
        assert calls[0][4] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
        assert len(pool.inserts) == 1
        assert _census("mirror_mode") == before

    def test_the_subtraction_is_spelled_at_the_pass_and_the_query_is_untouched(self):
        s = _src(cs.sweep_once)
        assert "from ..live_executor import mirror_mode" in s
        i = _pos(s, "_roster = settings().source_whales()")
        j = _pos(s, "whales = sorted(_roster - {w for w in _roster if mirror_mode(w)})")
        assert i < j < _pos(s, "rows = await pool.fetch(")
        assert s.count("mirror_mode(") == 1
        assert "whales, PRICE_CEILING," in s
        head = inspect.getsource(cs).split("\ndef _game_date", 1)[0]
        assert "mirror_mode" not in head, "imported at the pass, like the reapers"


# ───────────────────────────── 6. the exit census can name it ─────────────────

class _BeatPool:
    def __init__(self, counts):
        self.counts = counts

    async def fetchrow(self, _sql, *_a):
        return {"detail": {"exit_census": dict(self.counts), "exit_recent": []},
                "beat_at": "2026-09-02T00:00:00Z"}


def _endpoint(monkeypatch, counts) -> dict:
    from sportsassets.api import app as A

    async def _pool():
        return _BeatPool(counts)

    monkeypatch.setattr(A, "get_pool", _pool)
    return asyncio.run(A.admin_exit_census())


class TestTheExitCensusEndpointNamesTheBook:
    def _src(self) -> str:
        from sportsassets.api import app as A
        return _code(inspect.getsource(A.admin_exit_census))

    def test_it_is_read_beside_no_position_of_ours_not_filed_as_a_defect(self):
        s = self._src()
        i = _pos(s, 'counts.get("mx_no_position_of_ours")')
        j = _pos(s, 'counts.get("mx_mirror_owns_market")')
        assert i < j < _pos(s, "defect_keys = (")
        defects = s[_pos(s, "defect_keys = ("):_pos(s, "return {")]
        assert "mx_mirror_owns_market" not in defects, \
            "a book the mirror unwinds is not a post-position defect"

    def test_a_vanish_on_a_booked_market_is_a_coverage_reading(self, monkeypatch):
        out = _endpoint(monkeypatch, {"mx_reached_position_lookup": 10,
                                      "mx_no_position_of_ours": 4,
                                      "mx_mirror_owns_market": 6})
        r = out["read_this_first"]
        assert r["stopped_because_we_never_copied_his_entry"] == 4
        assert r["stopped_because_the_mirror_book_holds_the_market"] == 6
        assert "FILL RATE constraint" in r["verdict"]
        assert r["post_position_refusals"] == {}

    def test_without_a_book_the_reading_is_todays(self, monkeypatch):
        out = _endpoint(monkeypatch, {"mx_reached_position_lookup": 10,
                                      "mx_no_position_of_ours": 10})
        r = out["read_this_first"]
        assert r["stopped_because_the_mirror_book_holds_the_market"] == 0
        assert "FILL RATE constraint" in r["verdict"]
        held = _endpoint(monkeypatch, {"mx_reached_position_lookup": 10,
                                       "mx_no_position_of_ours": 3,
                                       "mx_entry_in_flight": 7})
        assert "HELD" in held["read_this_first"]["verdict"]
        defect = _endpoint(monkeypatch, {"mx_reached_position_lookup": 10,
                                         "mx_no_position_of_ours": 3,
                                         "mx_venue_unfilled": 7})
        assert "read post_position_refusals" in defect["read_this_first"]["verdict"]
        assert defect["read_this_first"]["post_position_refusals"] == {"mx_venue_unfilled": 7}

    def test_the_book_counts_with_an_entry_in_flight_too(self, monkeypatch):
        out = _endpoint(monkeypatch, {"mx_reached_position_lookup": 10,
                                      "mx_no_position_of_ours": 2,
                                      "mx_mirror_owns_market": 5,
                                      "mx_entry_in_flight": 3})
        assert "HELD" in out["read_this_first"]["verdict"]

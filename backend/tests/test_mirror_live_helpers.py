"""Mirror P1, step 1: the gates maybe_execute shares with the mirror book.

Position mirroring (owner order 2026-09-02) holds one standing book per
market and must admit a book under the same levers the per-fill lane
copies under: the rolling-loss breaker, the sleeve's room, the four
mapping gates, the game-date rule. Copying those clauses into a worker
would hand the mirror a second, drifting copy of each money rule -- the
failure the verified-set unification (test_verified_set) was built
against -- so they were factored out of maybe_execute into helpers the
mirror reads too.

This file holds three things:

1. that maybe_execute calls each helper where the inline code stood, in
   the same order relative to the gates around it, and keeps no second
   copy of any rule (the step-1 review item: "diff of maybe_execute is
   only call-site substitutions");
2. the truth table of every helper, including what an UNREADABLE read
   returns -- each helper names its own result and the caller applies
   its lane's stance (the per-fill lane keeps failing open on the loss
   breaker; the mirror will refuse on None);
3. that the hand-off predicate mirror_mode reads the environment only,
   so it cannot fail open on a read.

The refusal texts the mapping gate writes are pinned verbatim here
because gate_edge parses them off the row and three existing test files
match on them; a changed byte is a changed census.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import textwrap
import types
from datetime import date, timedelta

import pytest

from sportsassets import live_executor as le
from tests.test_live_executor_ladder import _LadderPool, _payload, _wire

TODAY = date.today()
SLUG = f"atc-epl-ars-che-{TODAY.isoformat()}-ars"


def _src(fn) -> str:
    return inspect.getsource(fn)


def _pos(src: str, needle: str) -> int:
    assert needle in src, f"not found: {needle!r}"
    return src.index(needle)


# ───────────────────────────── 1. the call sites ─────────────────────────────

class TestMaybeExecuteCallsEachHelperWhereTheInlineCodeStood:
    def test_the_gates_keep_their_order(self):
        """edge gate, cell gate, game date, already-taken, loss breaker,
        room, sizing, the INSERT, the mapping gate, the echo, the venue:
        the same order the inline code ran in, now through the helpers."""
        s = _src(le.maybe_execute)
        order = [
            "edge_gate.verdict(",
            "if not copy_allowed(",
            "_game_too_far_out(mslug)",
            '_copy_stop("already_taken"',
            "_loss_breaker_tripped(pool)",
            '_copy_stop("loss_breaker"',
            "_copy_day_room(pool, cfg)",
            'return _copy_stop("no_budget_room"',
            "volume_normalized_clip(",
            "INSERT INTO live_orders",
            "_mapping_admitted(",
            "_spawn_echo(pool, row_id",
            "pmus.submit_fok",
        ]
        positions = [_pos(s, n) for n in order]
        assert positions == sorted(positions), list(zip(order, positions))

    def test_the_inline_bodies_are_gone(self):
        """No second copy of any rule survives in the caller. A rule with
        two homes is a rule that drifts."""
        s = _src(le.maybe_execute)
        for gone in ("sum(pnl)", "_caps_room(", "PENNY_TRIAL_DAILY_USD",
                     "PENNY_TRIAL_TOTAL_USD", "date.today()", "timedelta(",
                     "lost_24h", '"side_echo_tripped"', '"mapping_quarantine"',
                     '"premap_live"', 'os.getenv("LIVE_HOLD_WHALES"',
                     "_tripped ="):
            assert gone not in s, gone

    def test_the_room_reading_still_feeds_sizing(self):
        """day_room/total_room are read once and used by the per-fill
        sizing below, as before."""
        s = _src(le.maybe_execute)
        assert _pos(s, "_copy_day_room(pool, cfg)") < _pos(
            s, "min(cfg.live_max_per_fill_usd, day_room, total_room)")

    def test_the_caps_query_has_exactly_one_home(self):
        assert "_caps_room(pool)" in _src(le._copy_day_room)

    def test_the_loss_breaker_keeps_this_lanes_fail_open(self):
        """The helper answers None for an unreadable ledger; THIS lane
        maps it to 0.0 realized against the same threshold, the stance
        it has carried since 2026-08-12. The mirror's stance is its own."""
        s = _src(le.maybe_execute)
        i = _pos(s, "_loss_breaker_tripped(pool)")
        block = s[i:_pos(s, '_copy_stop("loss_breaker"')]
        assert "is None" in block
        assert "0.0 <= -PMUS_LOSS_BREAKER_USD" in block

    def test_the_refusal_text_is_written_by_the_caller_verbatim(self):
        s = _src(le.maybe_execute)
        i = _pos(s, "_mapping_admitted(")
        tail = s[i:i + 700]
        assert "UPDATE live_orders SET status='rejected', error=$2 " in tail
        assert "row_id, _q_reason" in tail

    def test_the_echo_follows_only_a_quarantine_refusal(self):
        """The side-echo trip logs and stops; the verified and hold gates
        record and stop; only the quarantine spawns the shadow echo --
        exactly the inline branches, keyed on the declared prefixes."""
        s = _src(le.maybe_execute)
        i = _pos(s, "_mapping_admitted(")
        j = _pos(s, "_spawn_echo(pool, row_id")
        between = s[i:j]
        a = _pos(between, "_MAPPING_REFUSAL_SIDE_ECHO")
        b = _pos(between, "_MAPPING_REFUSAL_QUARANTINE")
        assert a < b
        assert "side-echo circuit" in between[a:b]
        assert "mirror_mode(" not in between


def test_the_helper_signatures_are_the_specs():
    assert list(inspect.signature(le._loss_breaker_tripped).parameters) == ["pool"]
    assert list(inspect.signature(le._copy_day_room).parameters) == ["pool", "cfg"]
    assert list(inspect.signature(le._game_too_far_out).parameters) == ["slug"]
    assert list(inspect.signature(le._mapping_admitted).parameters) == [
        "pool", "username", "mapping_src", "q_slug"]
    assert list(inspect.signature(le._protected_order_ids).parameters) == ["pool"]
    assert list(inspect.signature(le.mirror_mode).parameters) == ["username"]
    assert list(inspect.signature(le._mirror_owns_asset).parameters) == ["pool", "asset"]
    for fn in (le._loss_breaker_tripped, le._copy_day_room, le._mapping_admitted,
               le._protected_order_ids, le._mirror_owns_asset):
        assert inspect.iscoroutinefunction(fn), fn.__name__
    for fn in (le._game_too_far_out, le.mirror_mode, le.mirror_allowlist):
        assert not inspect.iscoroutinefunction(fn), fn.__name__


# ───────────────────────────── 2. the loss breaker ────────────────────────────

class _LossPool:
    SQL = ("SELECT COALESCE(sum(pnl), 0) FROM live_orders "
           "WHERE settled_at > now() - interval '24 hours' "
           "AND status IN ('settled', 'cashed_out') "
           "AND COALESCE(whale_username, '') NOT IN "
           "('manual', 'underdog')")

    def __init__(self, value=None, boom=False):
        self.value, self.boom, self.sql = value, boom, []

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        if self.boom:
            raise RuntimeError("db blip")
        return self.value


class TestLossBreakerTripped:
    @pytest.fixture(autouse=True)
    def _threshold(self, monkeypatch):
        monkeypatch.setattr(le, "PMUS_LOSS_BREAKER_USD", 5000.0)

    def test_truth_table(self):
        assert asyncio.run(le._loss_breaker_tripped(_LossPool(-5000.0))) is True
        assert asyncio.run(le._loss_breaker_tripped(_LossPool(-4999.99))) is False
        assert asyncio.run(le._loss_breaker_tripped(_LossPool(0.0))) is False
        assert asyncio.run(le._loss_breaker_tripped(_LossPool(250.0))) is False
        assert asyncio.run(le._loss_breaker_tripped(_LossPool(None))) is False, \
            "an empty ledger sums to nothing, not to a trip"
        assert asyncio.run(le._loss_breaker_tripped(_LossPool("-6000"))) is True

    def test_unreadable_is_none_not_a_verdict(self):
        assert asyncio.run(le._loss_breaker_tripped(_LossPool(boom=True))) is None

    def test_the_query_is_the_inline_one(self):
        p = _LossPool(0.0)
        asyncio.run(le._loss_breaker_tripped(p))
        assert p.sql == [_LossPool.SQL]

    def test_it_logs_the_breaker_line_when_tripped(self, caplog):
        with caplog.at_level(logging.WARNING, logger="sportsassets.live_executor"):
            asyncio.run(le._loss_breaker_tripped(_LossPool(-5000.0)))
        assert any(r.message.startswith(
            "LOSS BREAKER: copy sleeve realized -5000.00 in 24h "
            "(threshold -5000)") for r in caplog.records)

    def test_maybe_execute_still_copies_on_an_unreadable_ledger(self, monkeypatch):
        """Today's fail-open, pinned by behaviour rather than by source:
        the per-fill lane's caps and venue guards still bound every
        order, so an unreadable loss ledger must not stop it."""
        class _Blind(_LadderPool):
            async def fetchval(self, sql, *a):
                if "sum(pnl)" in sql:
                    raise RuntimeError("ledger down")
                return await super().fetchval(sql, *a)

        pool = _Blind([])
        submitted = _wire(monkeypatch, pool,
                          f"tsc-epl-ars-che-{TODAY.isoformat()}-o3pt5")
        asyncio.run(le.maybe_execute(_payload(), 5.0))
        assert submitted, "an unreadable loss ledger must not block the per-fill lane"

    def test_maybe_execute_still_refuses_when_tripped(self, monkeypatch):
        pool = _LadderPool([])
        pool.lost_24h = -5000.0
        submitted = _wire(monkeypatch, pool,
                          f"tsc-epl-ars-che-{TODAY.isoformat()}-o3pt5")
        asyncio.run(le.maybe_execute(_payload(), 5.0))
        assert not submitted
        assert not pool.updates, "the breaker fires before any row exists"


# ───────────────────────────── 3. the room ────────────────────────────────────

class TestCopyDayRoom:
    @staticmethod
    def _cfg():
        return types.SimpleNamespace(live_max_daily_usd=250.0,
                                     live_max_total_usd=2000.0)

    @staticmethod
    def _caps(monkeypatch, day, total):
        async def fake(_pool):
            return (day, total)
        monkeypatch.setattr(le, "_caps_room", fake)

    @pytest.fixture(autouse=True)
    def _knobs(self, monkeypatch):
        monkeypatch.setattr(le, "COPY_MODE", "penny_trial")
        monkeypatch.setattr(le, "PENNY_TRIAL_DAILY_USD", 500.0)
        monkeypatch.setattr(le, "PENNY_TRIAL_TOTAL_USD", 5000.0)
        monkeypatch.setattr(le, "PROBE_DAY_USD", 0.0)

    def test_penny_trial_knobs_are_the_authority(self, monkeypatch):
        """config caps 250/2000 with rooms 100/1000 means 150/1000 spent;
        the trial knobs 500/5000 minus that spend are the rooms."""
        self._caps(monkeypatch, 100.0, 1000.0)
        assert asyncio.run(le._copy_day_room(None, self._cfg())) == (350.0, 4000.0)

    def test_the_probe_day_cap_only_lowers(self, monkeypatch):
        self._caps(monkeypatch, 100.0, 1000.0)
        monkeypatch.setattr(le, "PROBE_DAY_USD", 200.0)
        assert asyncio.run(le._copy_day_room(None, self._cfg())) == (50.0, 4000.0)
        monkeypatch.setattr(le, "PROBE_DAY_USD", 900.0)
        assert asyncio.run(le._copy_day_room(None, self._cfg())) == (350.0, 4000.0)

    def test_unlimited_knobs_read_as_unlimited(self, monkeypatch):
        self._caps(monkeypatch, 100.0, 1000.0)
        monkeypatch.setattr(le, "PENNY_TRIAL_DAILY_USD", float("inf"))
        monkeypatch.setattr(le, "PENNY_TRIAL_TOTAL_USD", float("inf"))
        assert asyncio.run(le._copy_day_room(None, self._cfg())) == (
            float("inf"), float("inf"))

    def test_outside_penny_trial_the_config_rooms_stand(self, monkeypatch):
        self._caps(monkeypatch, 100.0, 1000.0)
        monkeypatch.setattr(le, "COPY_MODE", "live")
        assert asyncio.run(le._copy_day_room(None, self._cfg())) == (100.0, 1000.0)

    def test_exhausted_rooms_come_back_as_such(self, monkeypatch):
        """maybe_execute's `day_room <= 0 or total_room <= 1` reads these
        numbers; the helper does not clamp them."""
        self._caps(monkeypatch, -30.0, 0.5)
        monkeypatch.setattr(le, "COPY_MODE", "live")
        assert asyncio.run(le._copy_day_room(None, self._cfg())) == (-30.0, 0.5)

    def test_an_unreadable_ledger_raises_as_before(self, monkeypatch):
        """This lane has always let _caps_room's failure propagate rather
        than size against a guess; the helper does not turn a raise into
        a number."""
        async def boom(_pool):
            raise RuntimeError("db down")
        monkeypatch.setattr(le, "_caps_room", boom)
        with pytest.raises(RuntimeError):
            asyncio.run(le._copy_day_room(None, self._cfg()))


# ───────────────────────────── 4. the game date ───────────────────────────────

class TestGameTooFarOut:
    @staticmethod
    def _d(n):
        return (TODAY + timedelta(days=n)).isoformat()

    def test_truth_table(self):
        assert le._game_too_far_out(f"tsc-epl-ars-che-{self._d(0)}-o3pt5") is False
        assert le._game_too_far_out(f"tsc-epl-ars-che-{self._d(1)}-o3pt5") is False
        assert le._game_too_far_out(f"tsc-epl-ars-che-{self._d(2)}-o3pt5") is True
        assert le._game_too_far_out(f"tsc-epl-ars-che-{self._d(-3)}-o3pt5") is False
        assert le._game_too_far_out(f"aec-atp-x-y-{self._d(30)}") is True

    def test_undated_or_unparseable_is_not_far_out(self):
        """The inline rule judged an undated slug by the other gates and
        swallowed a ValueError; both stances kept."""
        assert le._game_too_far_out("tsc-no-date-here") is False
        assert le._game_too_far_out("") is False
        assert le._game_too_far_out(None) is False
        assert le._game_too_far_out("x-2026-13-45-y") is False


# ───────────────────────────── 5. the mapping gate ────────────────────────────

class _StatePool:
    """ingestion_state only: the helper reads nothing else and writes
    nothing. `boom` names keys whose read raises."""

    def __init__(self, state=None, boom=()):
        self.state = dict(state or {})
        self.boom = set(boom)
        self.reads: list[str] = []

    async def fetchval(self, sql, *a):
        assert sql == "SELECT value FROM ingestion_state WHERE key=$1", sql
        key = a[0]
        self.reads.append(key)
        if key in self.boom:
            raise RuntimeError("db blip")
        return self.state.get(key)

    async def execute(self, *a, **k):
        raise AssertionError("the mapping gate must not write")

    async def fetch(self, *a, **k):
        raise AssertionError("the mapping gate reads ingestion_state only")

    async def fetchrow(self, *a, **k):
        raise AssertionError("the mapping gate reads ingestion_state only")


def _admit(pool, username="rn1", src="premap", slug=SLUG):
    return asyncio.run(le._mapping_admitted(pool, username, src, slug))


SIDE_ECHO_TEXT = ("side-echo tripped: confirmed wrong-side evidence — "
                  "copying halted pending admin review "
                  f"(slug={SLUG[:120]})")
NOT_VERIFIED_TEXT = ("not verified-profitable: only whales certified by "
                     "the TRUEEDGE counterfactual may spend "
                     f"(slug={SLUG[:100]})")
HOLD_TEXT = ("hold: pending paper certification at the new "
             "detection latency (TRUEEDGE 2026-08-24) "
             f"(slug={SLUG[:120]})")


def _quarantined(src):
    return ("quarantined: mapping class unverified "
            "after wrong-side incident 2026-08-23 "
            f"(src={src}, slug={SLUG[:120]})")


def _premap_live(src):
    return ("premap-live: whale not in the "
            "verified-profitable set (TRUEEDGE 2026-08-24) "
            f"(src={src}, slug={SLUG[:120]})")


class TestMappingAdmitted:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for k in ("LIVE_MAPPING_QUARANTINE", "LIVE_PREMAP", "LIVE_PREMAP_WHALES",
                  "LIVE_VERIFIED_WHALES", "LIVE_HOLD_WHALES"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setattr(le, "_roster_override", None)

    # 1. the side-echo circuit
    def test_a_tripped_circuit_refuses_whatever_the_env_says(self, monkeypatch):
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "off")
        monkeypatch.setenv("LIVE_PREMAP", "on")
        for tv in ("true", True, 1):
            assert _admit(_StatePool({"side_echo_tripped": tv})) == (False, SIDE_ECHO_TEXT)

    def test_an_unreadable_circuit_is_tripped(self, monkeypatch):
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "off")
        assert _admit(_StatePool(boom={"side_echo_tripped"})) == (False, SIDE_ECHO_TEXT)

    def test_a_false_or_absent_circuit_admits(self, monkeypatch):
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "off")
        for st in ({}, {"side_echo_tripped": "false"}, {"side_echo_tripped": False}):
            p = _StatePool(st)
            assert _admit(p) == (True, None)
            assert p.reads == ["side_echo_tripped"], \
                "with the env override the quarantine switch is not read"

    # 2. the verified set
    def test_the_verified_set_refuses_an_uncertified_whale(self, monkeypatch):
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "off")
        monkeypatch.setenv("LIVE_VERIFIED_WHALES", "swisstony")
        assert _admit(_StatePool()) == (False, NOT_VERIFIED_TEXT)

    def test_an_empty_verified_set_disables_that_gate(self, monkeypatch):
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "off")
        monkeypatch.setenv("LIVE_VERIFIED_WHALES", "")
        assert _admit(_StatePool(), username="nobody") == (True, None)

    def test_the_stored_roster_beats_the_env(self, monkeypatch):
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "off")
        monkeypatch.setenv("LIVE_VERIFIED_WHALES", "rn1")
        monkeypatch.setattr(le, "_roster_override", {"swisstony"})
        assert _admit(_StatePool()) == (False, NOT_VERIFIED_TEXT)

    # 3. the hold
    def test_a_held_whale_is_refused_even_with_quarantine_off(self, monkeypatch):
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "off")
        monkeypatch.setenv("LIVE_HOLD_WHALES", "RN1")
        assert _admit(_StatePool()) == (False, HOLD_TEXT)
        assert _admit(_StatePool(), username="RN1") == (False, HOLD_TEXT), \
            "the whale name is case-normalized"

    def test_the_hold_default_is_empty(self, monkeypatch):
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "off")
        assert _admit(_StatePool()) == (True, None)

    # 4. the quarantine and its resume lane
    def test_env_on_refuses_a_fuzzy_mapping(self, monkeypatch):
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "on")
        monkeypatch.setenv("LIVE_PREMAP", "on")
        monkeypatch.setenv("LIVE_PREMAP_WHALES", "rn1")
        assert _admit(_StatePool(), src="fuzzy") == (False, _quarantined("fuzzy"))

    def test_env_on_refuses_a_premap_whale_off_the_allowlist(self, monkeypatch):
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "on")
        monkeypatch.setenv("LIVE_PREMAP", "on")
        monkeypatch.setenv("LIVE_PREMAP_WHALES", "swisstony")
        assert _admit(_StatePool(), src="premap") == (False, _premap_live("premap"))
        assert _admit(_StatePool(), src="exact") == (False, _premap_live("exact"))

    def test_the_resume_lane_admits_exactly_the_resume_classes(self, monkeypatch):
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "on")
        monkeypatch.setenv("LIVE_PREMAP", "on")
        monkeypatch.setenv("LIVE_PREMAP_WHALES", "rn1")
        for src in sorted(le.QUARANTINE_RESUME_SRC):
            assert _admit(_StatePool(), src=src) == (True, None), src
        assert _admit(_StatePool(), src="yesno_exact") == (False, _quarantined("yesno_exact"))
        assert _admit(_StatePool(), src="fuzzy") == (False, _quarantined("fuzzy"))
        assert _admit(_StatePool(), src=None) == (False, _quarantined("None"))

    def test_the_premap_live_db_switch(self, monkeypatch):
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "on")
        monkeypatch.setenv("LIVE_PREMAP_WHALES", "rn1")
        p = _StatePool({"premap_live": "true"})
        assert _admit(p) == (True, None)
        assert p.reads == ["side_echo_tripped", "premap_live"]
        for st in ({"premap_live": "false"}, {}, {"premap_live": None}):
            assert _admit(_StatePool(st)) == (False, _quarantined("premap"))
        assert _admit(_StatePool(boom={"premap_live"})) == (False, _quarantined("premap")), \
            "an unreadable premap-live switch refuses"
        monkeypatch.setenv("LIVE_PREMAP", "off")
        assert _admit(_StatePool({"premap_live": "true"})) == (False, _quarantined("premap")), \
            "the env override beats the DB in either direction"

    def test_the_quarantine_db_switch(self, monkeypatch):
        p = _StatePool({"mapping_quarantine": "false"})
        assert _admit(p, src="fuzzy") == (True, None)
        assert p.reads == ["side_echo_tripped", "mapping_quarantine"]
        assert _admit(_StatePool({}), src="fuzzy") == (False, _quarantined("fuzzy")), \
            "absent is ON"
        assert _admit(_StatePool(boom={"mapping_quarantine"}), src="fuzzy") == \
            (False, _quarantined("fuzzy")), "unreadable stays ON"
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "on")
        assert _admit(_StatePool({"mapping_quarantine": "false"}), src="fuzzy") == \
            (False, _quarantined("fuzzy")), "the env override beats the DB"

    def test_the_gates_run_in_the_inline_order(self, monkeypatch):
        """When every gate would refuse, the FIRST one names the refusal:
        circuit, then verified, then hold, then quarantine. Peeling them
        off one at a time exposes the next."""
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "on")
        monkeypatch.setenv("LIVE_VERIFIED_WHALES", "swisstony")
        monkeypatch.setenv("LIVE_HOLD_WHALES", "rn1")
        tripped = {"side_echo_tripped": "true"}
        assert _admit(_StatePool(tripped), src="fuzzy") == (False, SIDE_ECHO_TEXT)
        assert _admit(_StatePool(), src="fuzzy") == (False, NOT_VERIFIED_TEXT)
        monkeypatch.setenv("LIVE_VERIFIED_WHALES", "")
        assert _admit(_StatePool(), src="fuzzy") == (False, HOLD_TEXT)
        monkeypatch.setenv("LIVE_HOLD_WHALES", "")
        assert _admit(_StatePool(), src="fuzzy") == (False, _quarantined("fuzzy"))

    def test_every_refusal_carries_a_declared_prefix_and_only_one(self):
        """maybe_execute keys the follow-up on the prefix: the circuit's
        text logs, the quarantine's texts spawn the echo, the other two
        record and stop. The dispatch must be total and exclusive."""
        texts = [SIDE_ECHO_TEXT, NOT_VERIFIED_TEXT, HOLD_TEXT,
                 _quarantined("fuzzy"), _premap_live("premap")]
        for t in texts:
            hits = [t.startswith(le._MAPPING_REFUSAL_SIDE_ECHO),
                    t.startswith(le._MAPPING_REFUSAL_QUARANTINE),
                    t.startswith(("not verified-profitable:", "hold:"))]
            assert sum(hits) == 1, t
        assert le._MAPPING_REFUSAL_SIDE_ECHO == "side-echo tripped:"
        assert le._MAPPING_REFUSAL_QUARANTINE == ("premap-live:", "quarantined:")

    def test_it_never_writes(self):
        """_StatePool raises on execute/fetch/fetchrow; every path above
        ran against it, so this only makes the property explicit."""
        s = _src(le._mapping_admitted)
        assert ".execute(" not in s and ".fetch(" not in s and ".fetchrow(" not in s

    def test_the_gate_source_carries_the_pins_the_old_tests_read(self):
        """test_quarantine_admits_exact and test_verified_set pin these
        phrases in maybe_execute's source; the code now lives here, so
        the same phrases are pinned where the decision is made."""
        s = _src(le._mapping_admitted)
        assert '_whale_set("LIVE_VERIFIED_WHALES")' in s
        assert '_whale_set("LIVE_PREMAP_WHALES")' in s
        assert '"homerunhazard,0x076daa87"' not in s
        gate = s[s.index("QUARANTINE_RESUME_SRC"):]
        gate = gate[:gate.index("_q_on and not _premap_ok")]
        assert "username in _allowed" in gate
        assert "_premap_ok = False" in s and "fail safe: refuse" in s
        assert 'f"(src={mapping_src}, ' in s and 'f"(src=premap, slug=' not in s
        assert s.index('"side_echo_tripped"') < s.index('_whale_set("LIVE_VERIFIED_WHALES")') \
            < s.index('"LIVE_HOLD_WHALES"') < s.index('"mapping_quarantine"') \
            < s.index('"premap_live"')


# ───────────────────────────── 6. the protected ids ───────────────────────────

class _FetchPool:
    def __init__(self, manual=(), mirror=(), boom=()):
        self.manual, self.mirror = list(manual), list(mirror)
        self.boom = set(boom)
        self.sql: list[str] = []

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "mirror_orders" in sql:
            if "mirror" in self.boom:
                raise RuntimeError('relation "mirror_orders" does not exist')
            return [{"order_id": o} for o in self.mirror]
        assert "live_orders" in sql, sql
        if "manual" in self.boom:
            raise RuntimeError("db blip")
        return [{"order_id": o} for o in self.manual]


class TestProtectedOrderIds:
    MANUAL_SQL = ("SELECT order_id FROM live_orders WHERE order_id IS NOT "
                  "NULL AND COALESCE(whale_username,'') = 'manual'")

    def test_the_union_of_both_reads(self):
        got = asyncio.run(le._protected_order_ids(
            _FetchPool(manual=["m1", 42], mirror=["x9"])))
        assert got == {"m1", "42", "x9"}
        assert asyncio.run(le._protected_order_ids(_FetchPool())) == set()

    def test_none_when_the_manual_read_fails(self):
        assert asyncio.run(le._protected_order_ids(
            _FetchPool(mirror=["x9"], boom={"manual"}))) is None

    def test_none_when_the_mirror_table_is_absent(self):
        """Migration 047 not applied: the reaper must sweep nothing, not
        sweep with half a list."""
        assert asyncio.run(le._protected_order_ids(
            _FetchPool(manual=["m1"], boom={"mirror"}))) is None

    def test_the_manual_query_is_the_reapers_and_the_reaper_reads_through_the_helper(self):
        p = _FetchPool()
        asyncio.run(le._protected_order_ids(p))
        assert p.sql[0] == self.MANUAL_SQL
        # P1 step 8 wired the reaper to the helper: the manual query lives
        # in ONE place, and the reaper skips its pass when the set is None
        reaper = _src(le._reap_stale_resting_bids)
        assert "await _protected_order_ids(pool)" in reaper
        assert "SELECT order_id FROM live_orders WHERE order_id IS NOT" not in reaper
        assert p.sql[1] == "SELECT order_id FROM mirror_orders WHERE order_id IS NOT NULL"


# ───────────────────────────── 7. the hand-off predicate ──────────────────────

class TestMirrorMode:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for k in ("PMUS_MIRROR", "PMUS_MIRROR_WHALES", "MIRROR_WHALES"):
            monkeypatch.delenv(k, raising=False)

    def test_an_unset_env_is_off_for_everyone(self):
        for w in ("rn1", "RN1", "swisstony", "", None):
            assert le.mirror_mode(w) is False, w
        assert le.mirror_allowlist() == set()

    def test_on_without_a_named_whale_is_nobody(self, monkeypatch):
        monkeypatch.setenv("PMUS_MIRROR", "on")
        assert le.mirror_allowlist() == set()
        assert le.mirror_mode("rn1") is False

    def test_the_mode_truth_table(self, monkeypatch):
        monkeypatch.setenv("PMUS_MIRROR_WHALES", "rn1")
        for mode, expect in (("on", True), ("exits", True), ("off", False),
                             ("", False), ("shadow", False), ("ON", True),
                             (" exits ", True), ("1", False), ("true", False)):
            monkeypatch.setenv("PMUS_MIRROR", mode)
            assert le.mirror_mode("rn1") is expect, mode
            assert le.mirror_mode("RN1") is expect, mode
            assert le.mirror_mode(" rn1 ") is expect, mode
            assert le.mirror_mode("swisstony") is False, mode
            assert le.mirror_mode("") is False and le.mirror_mode(None) is False

    def test_the_allowlist_is_the_intersection_with_the_shadow_set(self, monkeypatch):
        monkeypatch.setenv("PMUS_MIRROR", "on")
        monkeypatch.setenv("PMUS_MIRROR_WHALES", "rn1, SwissTony")
        monkeypatch.setenv("MIRROR_WHALES", "swisstony")
        assert le.mirror_allowlist() == {"swisstony"}
        assert le.mirror_mode("rn1") is False
        assert le.mirror_mode("swisstony") is True
        monkeypatch.setenv("MIRROR_WHALES", "rn1,swisstony")
        assert le.mirror_allowlist() == {"rn1", "swisstony"}
        monkeypatch.delenv("MIRROR_WHALES")          # the shadow's default is rn1
        assert le.mirror_allowlist() == {"rn1"}

    def test_it_does_not_inherit_whale_sets_roster_default(self, monkeypatch):
        """_whale_set answers the verified roster for an unset env; the
        hand-off's unset env must be nobody, or PMUS_MIRROR=on alone would
        silently stop per-fill copying for the roster's whales."""
        monkeypatch.setenv("PMUS_MIRROR", "on")
        assert "rn1" in le._whale_set("PMUS_MIRROR_WHALES")
        assert le.mirror_mode("rn1") is False

    def test_it_performs_no_io(self):
        for fn in (le.mirror_mode, le.mirror_allowlist):
            s = _src(fn)
            for bad in ("await", "pool", "ingestion_state", "get_pool",
                        "fetch", "asyncpg", "settings("):
                assert bad not in s, (fn.__name__, bad)
            tree = ast.parse(textwrap.dedent(s))
            assert not any(isinstance(n, (ast.Await, ast.AsyncFunctionDef,
                                          ast.AsyncFor, ast.AsyncWith))
                           for n in ast.walk(tree)), fn.__name__


# ───────────────────────────── 8. the book's claim ────────────────────────────

class _ExistsPool:
    def __init__(self, value=None, boom=False):
        self.value, self.boom = value, boom
        self.calls: list[tuple] = []

    async def fetchval(self, sql, *a):
        self.calls.append((sql, a))
        if self.boom:
            raise RuntimeError('relation "mirror_books" does not exist')
        return self.value


class TestMirrorOwnsAsset:
    def test_truth_table(self):
        assert asyncio.run(le._mirror_owns_asset(_ExistsPool(True), "123")) is True
        assert asyncio.run(le._mirror_owns_asset(_ExistsPool(1), "123")) is True
        assert asyncio.run(le._mirror_owns_asset(_ExistsPool(False), "123")) is False
        assert asyncio.run(le._mirror_owns_asset(_ExistsPool(None), "123")) is False

    def test_unreadable_is_false(self):
        """Falls to mx_no_position_of_ours in the caller -- a settled
        reason that sells nothing either."""
        assert asyncio.run(le._mirror_owns_asset(_ExistsPool(boom=True), "123")) is False

    def test_the_predicate_reads_open_books_on_either_token(self):
        p = _ExistsPool(True)
        asyncio.run(le._mirror_owns_asset(p, "123"))
        (sql, args), = p.calls
        assert "mirror_books" in sql
        assert "state <> 'closed'" in sql
        assert "long_asset = $1 OR other_asset = $1" in sql
        assert args == ("123",)
        p2 = _ExistsPool(False)
        asyncio.run(le._mirror_owns_asset(p2, None))
        assert p2.calls[0][1] == ("",)

"""The short branch earns its way back one verified fill at a time.

Owner, 2026-08-26: "I see no short orders at all today on the ledger. I
need to make sure we are performing optimally, and based on the data,
alot of the profit sits in the short orders." Approved: "Yes approved."

The ban shipped after 2026-08-23, when six ORDER_INTENT_BUY_SHORT copies
landed on the wrong side. The units bug behind them was found and fixed,
and short_model_confirmed() now agrees with the venue 50/50 within
authorization. What was NEVER re-tested is the side itself: netPosition's
sign against the intent we sent. It has not run on a short because we
have not placed one.

So the flag stays OFF and this changes nothing for anyone who does not
set it. What it changes for someone who does is that LIVE_ALLOW_SHORT=on
stops meaning "open the floodgates on an unproven branch" and starts
meaning "place ONE short, read the venue's position sign, and only place
the next if that sign was right."

The failure modes this file pins are the ones that would make the
probation a lie:
  * a lock that leaks -> shorts stop forever and it looks like the ban
  * a lock released before the verdict exists -> N shorts race out
    before the first proof, which is the whole race it was built to close
  * a mismatch that does not re-arm -> the 2026-08-23 incident again
  * a lock still taken after the class is proved -> a permanent
    one-at-a-time serialisation, i.e. a probation that never ends
  * a tally that cannot be read, read as permission
"""

from __future__ import annotations

import ast
import inspect
import json

import pytest

from sportsassets import live_executor as le


class FakePool:
    """Reads/writes ingestion_state; jsonb round-trips as a dict."""

    def __init__(self, state=None, *, raise_on_read=False,
                 raise_on_write=False):
        self.state = dict(state or {})
        self.raise_on_read = raise_on_read
        self.raise_on_write = raise_on_write
        self.writes = 0

    async def fetchval(self, sql, *args):
        if self.raise_on_read:
            raise RuntimeError("db down")
        return self.state.get(args[0])

    async def execute(self, sql, *args):
        if self.raise_on_write:
            raise RuntimeError("db down")
        self.writes += 1
        self.state[args[0]] = json.loads(args[1])


@pytest.fixture(autouse=True)
def _fresh_lock():
    """The lock is module state; a test that leaks it would silently
    change every later verdict in this file."""
    while le._SHORT_LOCK.locked():
        le._SHORT_LOCK.release()
    yield
    while le._SHORT_LOCK.locked():
        le._SHORT_LOCK.release()


class TestGate:
    @pytest.mark.asyncio
    async def test_unreadable_tally_refuses(self):
        """Not knowing whether shorts are proved is not permission to
        place four of them at once."""
        ok, why, probation = await le._short_gate(
            FakePool(raise_on_read=True))
        assert ok is False
        assert "unreadable" in why
        assert probation is True

    @pytest.mark.asyncio
    async def test_empty_tally_admits_exactly_one(self):
        pool = FakePool()
        ok, _why, probation = await le._short_gate(pool)
        assert (ok, probation) == (True, True)

    @pytest.mark.asyncio
    async def test_a_second_short_is_refused_while_one_is_in_flight(self):
        pool = FakePool()
        ok, _why, probation = await le._short_gate(pool)
        assert ok and probation
        await le._SHORT_LOCK.acquire()          # what the caller does
        ok2, why2, _ = await le._short_gate(pool)
        assert ok2 is False
        assert "one short at a time" in why2

    @pytest.mark.asyncio
    async def test_one_mismatch_re_arms_the_ban_permanently(self):
        """Even with the quota otherwise satisfied. A wrong-side fill is
        the exact event the branch was banned for; more right answers do
        not average it away."""
        pool = FakePool({le.SHORT_PROOF_KEY: {
            "ok": le.SHORT_PROBATION_N + 50, "mismatch": 1}})
        ok, why, _ = await le._short_gate(pool)
        assert ok is False
        assert "WRONG SIDE" in why
        assert "human" in why

    @pytest.mark.asyncio
    async def test_the_lock_is_not_taken_once_proved(self):
        """A probation that never ends is just a slower ban: taking the
        serialising lock after the class is proved would queue every
        short behind the one before it forever."""
        pool = FakePool({le.SHORT_PROOF_KEY: {
            "ok": le.SHORT_PROBATION_N, "mismatch": 0}})
        ok, _why, probation = await le._short_gate(pool)
        assert ok is True
        assert probation is False

    @pytest.mark.asyncio
    async def test_proved_class_is_not_blocked_by_a_held_lock(self):
        pool = FakePool({le.SHORT_PROOF_KEY: {
            "ok": le.SHORT_PROBATION_N, "mismatch": 0}})
        await le._SHORT_LOCK.acquire()
        ok, _why, probation = await le._short_gate(pool)
        assert (ok, probation) == (True, False)

    @pytest.mark.asyncio
    async def test_the_quota_is_more_than_one(self):
        """One verified fill is a coin flip that landed heads."""
        assert le.SHORT_PROBATION_N >= 2


class TestProofTally:
    @pytest.mark.asyncio
    async def test_an_ok_fill_advances_the_count(self):
        pool = FakePool()
        await le._record_short_proof(pool, ok=True, net=-40, slug="atc-x")
        assert pool.state[le.SHORT_PROOF_KEY]["ok"] == 1
        assert pool.state[le.SHORT_PROOF_KEY]["mismatch"] == 0

    @pytest.mark.asyncio
    async def test_a_wrong_side_fill_is_recorded_as_a_mismatch(self):
        pool = FakePool({le.SHORT_PROOF_KEY: {"ok": 2, "mismatch": 0}})
        await le._record_short_proof(pool, ok=False, net=+40, slug="atc-x")
        assert pool.state[le.SHORT_PROOF_KEY]["mismatch"] == 1
        # and that is enough to shut the branch
        allowed, _why, _ = await le._short_gate(pool)
        assert allowed is False

    @pytest.mark.asyncio
    async def test_it_keeps_its_own_key_apart_from_side_echo_last(self):
        """side_echo_last mixes intents. A verified LONG says nothing
        about which side BUY_SHORT buys, and counting one as the other
        would let the probation graduate on evidence it never gathered."""
        assert le.SHORT_PROOF_KEY != "side_echo_last"
        pool = FakePool()
        await le._record_short_proof(pool, ok=True, net=-1, slug="s")
        assert "side_echo_last" not in pool.state

    @pytest.mark.asyncio
    async def test_bookkeeping_failure_never_breaks_the_fill(self):
        pool = FakePool(raise_on_write=True)
        await le._record_short_proof(pool, ok=True, net=-1, slug="s")

    @pytest.mark.asyncio
    async def test_an_unreadable_prior_does_not_erase_a_mismatch(self):
        """Starting from {} on a read error would let a transient blip
        launder away the one record that matters. The gate is what has
        to fail closed, and it does -- so the write is allowed to start
        fresh only because the reader refuses, not because it is safe."""
        src = inspect.getsource(le._short_gate)
        assert "return False" in src.split("except")[1]


class TestEchoOwnsTheVerdictWindow:
    def test_the_lock_is_handed_to_the_echo_not_dropped_at_placement(self):
        """The proof this probation waits for -- netPosition's sign --
        is read inside the echo, seconds after the placer returns. If
        the placer's finally released the lock, SHORT_PROBATION_N orders
        would go out before the first verdict existed."""
        src = inspect.getsource(le.maybe_execute)
        assert "owns_short_lock=_short_probation_held" in src
        after = src[src.index("owns_short_lock=_short_probation_held"):]
        assert "_short_probation_held = False" in after, (
            "ownership moved to the echo but the placer still thinks it "
            "holds the lock -- the finally will double-release it")

    def test_the_echo_releases_in_a_finally(self):
        # Read the CODE, not the prose: the docstring says the word
        # "finally" too, and splitting on the first hit found the
        # sentence rather than the clause. Guessing a slice width
        # against today's formatting is how instruments end up unable
        # to see their subject.
        fn = ast.parse(
            inspect.getsource(le._echo_then_release_short).lstrip()).body[0]
        tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
        assert tries, "no try/finally at all"
        assert any(
            "_SHORT_LOCK.release()" in ast.unparse(stmt)
            for t in tries for stmt in t.finalbody), (
            "the release is not in a finally -- a raising or cancelled "
            "echo would leak the lock and stop shorts permanently")

    @pytest.mark.asyncio
    async def test_the_echo_releases_even_when_it_raises(self):
        async def boom():
            raise RuntimeError("venue down")

        await le._SHORT_LOCK.acquire()
        with pytest.raises(RuntimeError):
            await le._echo_then_release_short(boom())
        assert not le._SHORT_LOCK.locked()

    def test_spawn_echo_does_not_wrap_unless_it_owns_the_lock(self):
        """Shadow echoes and long copies also call _spawn_echo. Wrapping
        those would release a lock some other short is holding."""
        sig = inspect.signature(le._spawn_echo)
        assert sig.parameters["owns_short_lock"].default is False
        src = inspect.getsource(le._spawn_echo)
        assert "if owns_short_lock:" in src

    @pytest.mark.asyncio
    async def test_the_position_sign_check_records_short_proof(self):
        """netPosition's sign is the only end-to-end answer, so the
        recorder has to sit inside the branch that reads it."""
        src = inspect.getsource(le._side_echo_verify)
        assert "position_side" in src
        block = src[src.index("position_side"):]
        assert "_record_short_proof(" in block
        assert "is_short_intent(intent)" in block[:block.index(
            "_record_short_proof(")]


class TestPlacementPath:
    def test_probation_held_is_bound_before_the_try(self):
        """The finally reads it. Bound inside the try, any exception
        raised earlier would surface as a NameError from the cleanup
        path and bury the real traceback -- the same defect that hid
        behind `stats` in the exit reaper."""
        tree = ast.parse(inspect.getsource(le.maybe_execute).lstrip())
        fn = tree.body[0]

        def first_line(name, node, out):
            for n in ast.walk(node):
                if isinstance(n, ast.Name) and n.id == name:
                    out.append((n.lineno, isinstance(n.ctx, ast.Store)))
            return out

        marks = sorted(first_line("_short_probation_held", fn, []))
        assert marks, "the flag vanished"
        assert marks[0][1] is True, "first mention is a read, not a bind"
        tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)
                 and any(h.finalbody for h in [n])]
        assert tries
        outer = min(tries, key=lambda n: n.lineno)
        assert marks[0][0] < outer.lineno, (
            "bound inside the try; the finally can raise NameError")

    def test_the_finally_releases_only_what_it_holds(self):
        src = inspect.getsource(le.maybe_execute)
        tail = src[src.rindex("finally:"):]
        assert "_short_probation_held and _SHORT_LOCK.locked()" in tail

    def test_the_gate_engages_only_when_the_flag_is_on(self):
        """Unset LIVE_ALLOW_SHORT must behave exactly as it did before
        this shipped: refused, shadowed, no lock, no tally."""
        src = inspect.getsource(le.maybe_execute)
        block = src[src.index("_short_gate(pool)") - 900:
                    src.index("_short_gate(pool)")]
        assert 'LIVE_ALLOW_SHORT' in block
        assert '== "on"' in block

    def test_the_default_ban_branch_survives(self):
        src = inspect.getsource(le.maybe_execute)
        assert '!= "on"' in src, (
            "the unconditional refusal for the unset flag is gone")

    def test_a_refused_short_still_releases_nothing_it_never_took(self):
        """The refusal returns before the acquire, so the finally's
        guard must be the flag, not the lock's state -- another short
        may legitimately hold it."""
        src = inspect.getsource(le.maybe_execute)
        gate = src[src.index("_short_gate(pool)"):]
        gate = gate[:gate.index("_short_probation_held = True")]
        assert gate.index("return") < gate.index("_SHORT_LOCK.acquire()")

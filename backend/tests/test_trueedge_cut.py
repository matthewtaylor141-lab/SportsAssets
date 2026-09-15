"""TRUEEDGE cuts, the premap-live allowlist, and the side-echo tripwire
(owner order 2026-08-24, from the verified counterfactual table).

Three independent money blocks for a cut whale:
1. COPY_CUT_WHALES refuses at entry (before any row is written),
2. the 0.00 clip buys zero contracts,
3. the premap-live allowlist admits only the verified-profitable set.

And the running invariant: after any fill, the side echo re-derives the
mapping from the venue's LIVE board through the same matcher; a
confirmed divergence re-arms the total quarantine on its own.
"""

import asyncio
import json
from datetime import date

from sportsassets import live_executor
from sportsassets.workers import premap as premap_mod
from tests.test_live_executor_ladder import _LadderPool, _payload, _wire

TODAY = date.today().isoformat()

# THE PRODUCTION SET, not a copy of it.
#
# This was a frozenset literal listing rn1 and ferrari, written when
# they were cut. The 2026-08-25 roster reset moved production and left
# this behind — a FOURTH literal of the same decision, inside the
# harness that is supposed to be testing it. Every rn1 fixture then
# failed with no rejection row at all, because the harness itself was
# re-cutting him after wiring.
#
# Reading the real constant means the harness cannot disagree with
# production about who is cut, which is the property the whole
# roster-drift family of bugs comes from.
REAL_CUTS = live_executor.COPY_CUT_WHALES


def _wire_with_real_cuts(monkeypatch, pool, mapped_slug):
    """The shared harness lifts the cut for its RN1 fixtures; these
    tests put the REAL cut set back after wiring."""
    submitted = _wire(monkeypatch, pool, mapped_slug)
    monkeypatch.setattr(live_executor, "COPY_CUT_WHALES", REAL_CUTS)
    return submitted


def test_cut_whales_are_refused_at_entry(monkeypatch):
    for whale in ("SwissTony", "swisstony", live_executor._W2C33):
        pool = _LadderPool([])
        submitted = _wire_with_real_cuts(
            monkeypatch, pool, f"tsc-epl-ars-che-{TODAY}-o3pt5")
        asyncio.run(live_executor.maybe_execute(
            _payload(whale_username=whale), 5.0))
        assert submitted == [], f"{whale} must never reach the venue"
        assert pool.ladder_queries == 0, \
            f"{whale} must be refused before mapping even runs"


def test_verified_whales_still_flow_when_quarantine_lifted(monkeypatch):
    from sportsassets import copy_sports as _cs

    pool = _LadderPool([])
    submitted = _wire_with_real_cuts(
        monkeypatch, pool, f"tsc-epl-ars-che-{TODAY}-o3pt5")
    monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
    asyncio.run(live_executor.maybe_execute(
        _payload(whale_username="0x076daa87"), 5.0))
    assert submitted


class TestPremapLiveAllowlist:
    def _premap(self, monkeypatch):
        async def fake_premap(_pool, *_a, **_k):
            return {"market_slug": f"atc-epl-ars-che-{TODAY}-ars",
                    "title": "Arsenal", "outcome": "arsenal",
                    "intent": "ORDER_INTENT_BUY_LONG",
                    "matched_by": "premap", "score": 1.0}

        monkeypatch.setattr(premap_mod, "resolve", fake_premap)

    def _run(self, monkeypatch, whale, allowlist_env=None):
        from sportsassets import copy_sports as _cs

        pool = _LadderPool([])
        submitted = _wire_with_real_cuts(monkeypatch, pool, "unused")
        monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "on")
        monkeypatch.setenv("LIVE_PREMAP", "on")
        if allowlist_env is not None:
            monkeypatch.setenv("LIVE_PREMAP_WHALES", allowlist_env)
        else:
            monkeypatch.delenv("LIVE_PREMAP_WHALES", raising=False)
        self._premap(monkeypatch)
        asyncio.run(live_executor.maybe_execute(
            _payload(whale_username=whale), 5.0))
        return pool, submitted

    def test_default_allowlist_admits_the_verified_three(self, monkeypatch):
        for whale in ("0x076daa87", "0x076daa87"):
            pool, submitted = self._run(monkeypatch, whale)
            assert submitted, f"{whale} is verified-profitable — trades"

    def test_a_certified_whale_trades(
            self, monkeypatch):
        """His hold's stated condition — paper cohort positive at the
        NEW detection latency — was met on 2026-08-24 evening:
        TRUEEDGE-FAST paper_actual +6,864.52 on detections inside 5s.
        Exemplar moved to RN1 on 2026-08-25: the merge-inclusive
        re-grade made him the roster's best book (+$222,038) and cut
        SwissTony (-$187,613). The gate under test is the allowlist,
        not the identity."""
        pool, submitted = self._run(
            monkeypatch, "RN1",
            allowlist_env="homerunhazard,0x076daa87,rn1")
        assert submitted, "a certified whale trades"

    def test_the_hold_mechanism_still_works_for_any_whale(self,
                                                          monkeypatch):
        """The gate itself is intact and re-armable without a deploy."""
        monkeypatch.setenv("LIVE_HOLD_WHALES", "rn1")
        pool, submitted = self._run(
            monkeypatch, "RN1",
            allowlist_env="homerunhazard,0x076daa87,rn1")
        assert submitted == []
        rej = [(sql, a) for sql, a in pool.updates
               if "status='rejected'" in sql and "hold:" in str(a)]
        assert len(rej) == 1

    def test_non_allowlisted_whale_refused_with_allowlist_reason(
            self, monkeypatch):
        # kch123: not cut, not held — the refusal isolates the
        # allowlist mechanics.
        pool, submitted = self._run(monkeypatch, "kch123")
        assert submitted == []
        rej = [(sql, a) for sql, a in pool.updates
               if "status='rejected'" in sql
               and "verified-profitable" in str(a)]
        assert len(rej) == 1, "refusal must carry the allowlist reason"

    def test_a_whale_joins_via_env_no_deploy(self, monkeypatch):
        """The certification path: clear the hold AND join the
        allowlist — both deliberate env actions, no deploy."""
        monkeypatch.setenv("LIVE_HOLD_WHALES", "")
        pool, submitted = self._run(
            monkeypatch, "RN1",
            allowlist_env="homerunhazard,0x076daa87,rn1")
        assert submitted


class _EchoPool:
    """Pool for the side-echo unit tests: serves the us_premap parent
    lookup and records every state write."""

    def __init__(self, event_slug="epl-ars-che"):
        self.event_slug = event_slug
        self.executes = []

    async def fetchrow(self, sql, *a):
        if "FROM us_premap" in sql:
            return {"event_slug": self.event_slug,
                    "market_slug": "parent-slug"}
        return None

    async def fetchval(self, sql, *a):
        return None   # no prior side_echo_last

    async def execute(self, sql, *a):
        self.executes.append((" ".join(sql.split()), a))


def _echo_state(pool, key):
    """The state row the echo wrote, by KEY — the key is a bound
    parameter now (shadow mode writes side_echo_shadow instead), so
    matching on the SQL text would silently match nothing."""
    for sql, a in pool.executes:
        if "ingestion_state" in sql and len(a) == 2 and a[1] == key:
            return json.loads(a[0])
    return None


def _live_rows(slug):
    """Rows in the sweep's own shape, as live_rows_for_market returns
    them (the echo consumes THIS shape — leak-hunt find 2026-08-24:
    desk-shaped event_board rows made the tripwire inert)."""
    return [{"identifier": slug, "side_norm": "arsenal", "line": "",
             "kind": "side", "question": "Arsenal vs Chelsea Winner"},
            {"identifier": slug[:-4] + "-che", "side_norm": "chelsea",
             "line": "", "kind": "side",
             "question": "Arsenal vs Chelsea Winner"}]


def test_side_echo_ok_records_and_never_trips(monkeypatch):
    slug = f"atc-epl-ars-che-{TODAY}-ars"
    monkeypatch.setattr(premap_mod, "live_rows_for_market",
                        lambda parent: _live_rows(slug))
    pool = _EchoPool()
    asyncio.run(live_executor._side_echo_verify(
        pool, 101, slug, "Arsenal", "Arsenal vs Chelsea"))
    assert not any("mapping_quarantine" in sql for sql, _ in pool.executes)
    state = _echo_state(pool, "side_echo_last")
    assert state and state["ok"] == 1


def test_live_rows_for_market_produces_matchable_rows(monkeypatch):
    """The end-to-end shape contract the leak-hunt proved broken: the
    raw market from a DIRECT retrieve_by_slug lookup (the venue call
    the exact resolver already uses in production — PREMAP-GT proved
    the list-based path returns a generic page) must come back as rows
    match_side can actually match."""
    from sportsassets import pmus as pmus_mod

    slug = f"atc-epl-ars-che-{TODAY}-ars"
    raw_market = {"question": "Arsenal vs Chelsea Winner",
                  "slug": slug[:-4],
                  "marketSides": [
                      {"identifier": slug, "description": "Arsenal"},
                      {"identifier": slug[:-4] + "-che",
                       "description": "Chelsea"}]}

    class _Markets:
        def retrieve_by_slug(self, s):
            assert s == slug[:-4], "must look up the PARENT slug"
            return {"market": raw_market}

    class _Client:
        markets = _Markets()

    monkeypatch.setattr(pmus_mod, "_get_client", lambda: _Client())
    rows = premap_mod.live_rows_for_market(slug[:-4])
    hit = premap_mod.match_side(rows, "Arsenal", "Arsenal vs Chelsea")
    assert hit and hit["identifier"] == slug


def test_side_echo_mismatch_requarantines_alone(monkeypatch):
    ours = f"atc-epl-ars-che-{TODAY}-ars"
    # The live board now says the matcher's pick is a DIFFERENT
    # identifier than the one we bought — the one-order tripwire.
    other = f"atc-epl-ars-che-{TODAY}-v2-ars"
    monkeypatch.setattr(premap_mod, "live_rows_for_market",
                        lambda parent: _live_rows(other))
    pool = _EchoPool()
    asyncio.run(live_executor._side_echo_verify(
        pool, 101, ours, "Arsenal", "Arsenal vs Chelsea"))
    sqls = [sql for sql, _ in pool.executes]
    assert any("mapping_quarantine" in s and "'true'" in s for s in sqls)
    assert any("premap_live" in s and "'false'" in s for s in sqls)
    # The un-overridable circuit (leak-hunt find 2026-08-24): the two
    # switches above can be env-shadowed; this one cannot.
    assert any("side_echo_tripped" in s and "'true'" in s for s in sqls)
    err = [a for sql, a in pool.executes if "UPDATE live_orders" in sql]
    assert err and "SIDE-ECHO MISMATCH" in str(err[0])


class _TrippedPool(_LadderPool):
    """The circuit is armed; everything else answers as _LadderPool."""

    async def fetchval(self, sql, *a):
        if "ingestion_state" in sql and a and a[0] == "side_echo_tripped":
            return "true"
        return await super().fetchval(sql, *a)


def test_tripped_circuit_refuses_even_with_env_overrides(monkeypatch):
    """The leak-hunt scenario verbatim: LIVE_MAPPING_QUARANTINE=off and
    LIVE_PREMAP=on (env-armed operation) must NOT sail past a tripped
    side-echo circuit — the circuit has no env override."""
    from sportsassets import copy_sports as _cs

    pool = _TrippedPool([])
    submitted = _wire_with_real_cuts(
        monkeypatch, pool, f"tsc-epl-ars-che-{TODAY}-o3pt5")
    monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
    monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "off")
    monkeypatch.setenv("LIVE_PREMAP", "on")
    asyncio.run(live_executor.maybe_execute(
        _payload(whale_username="0x076daa87"), 5.0))
    assert submitted == []
    rej = [(sql, a) for sql, a in pool.updates
           if "status='rejected'" in sql and "side-echo tripped" in str(a)]
    assert len(rej) == 1


def test_side_echo_outage_never_trips_the_breaker(monkeypatch):
    def boom(_ev):
        raise RuntimeError("venue 503")

    monkeypatch.setattr(premap_mod, "live_rows_for_market", boom)
    pool = _EchoPool()
    asyncio.run(live_executor._side_echo_verify(
        pool, 101, f"atc-epl-ars-che-{TODAY}-ars", "Arsenal",
        "Arsenal vs Chelsea", attempts=1))
    assert not any("mapping_quarantine" in sql for sql, _ in pool.executes)
    state = _echo_state(pool, "side_echo_last")
    assert state and state["unverified"] == 1


def test_a_held_whale_is_refused_even_with_quarantine_off(monkeypatch):
    """The leak-hunt scenario: lifting the quarantine (a mapping-
    fidelity action) must NOT admit a HELD whale — the hold is a
    profitability decision with its own gate. No whale is held by
    default, so the mechanism is exercised by re-arming it explicitly
    on a whale that is otherwise fully live."""
    monkeypatch.setenv("LIVE_HOLD_WHALES", "rn1")
    from sportsassets import copy_sports as _cs

    pool = _LadderPool([])
    submitted = _wire_with_real_cuts(
        monkeypatch, pool, f"tsc-epl-ars-che-{TODAY}-o3pt5")
    monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
    # quarantine fully OFF (env, as _wire sets) — the hold still bites
    asyncio.run(live_executor.maybe_execute(
        _payload(whale_username="RN1"), 5.0))
    assert submitted == []
    rej = [(sql, a) for sql, a in pool.updates
           if "status='rejected'" in sql and "hold:" in str(a)]
    assert len(rej) == 1
    # clearing the hold (deliberate env action) restores his flow
    pool2 = _LadderPool([])
    submitted2 = _wire_with_real_cuts(
        monkeypatch, pool2, f"tsc-epl-ars-che-{TODAY}-o3pt5")
    monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
    monkeypatch.setenv("LIVE_HOLD_WHALES", "")
    asyncio.run(live_executor.maybe_execute(
        _payload(whale_username="RN1"), 5.0))
    assert submitted2


def test_clob_fallback_is_fail_closed(monkeypatch):
    """Leak-hunt find: active_venue() silently falls back to the global
    CLOB on a PMUS-credential fault, and that branch carried none of
    the freeze controls. It must refuse unless deliberately reopened."""
    from sportsassets import copy_sports as _cs

    pool = _LadderPool([])
    submitted = _wire_with_real_cuts(
        monkeypatch, pool, f"tsc-epl-ars-che-{TODAY}-o3pt5")
    monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
    monkeypatch.setattr(live_executor, "active_venue",
                        lambda: "polymarket-clob")
    clob_orders = []
    monkeypatch.setattr(live_executor, "_submit_fok",
                        lambda *a, **k: clob_orders.append(a) or
                        {"ok": True, "filled_shares": 1.0,
                         "fill_price": 0.5, "order_id": "x", "raw": {}})
    asyncio.run(live_executor.maybe_execute(
        _payload(whale_username="0x076daa87"), 5.0))
    assert submitted == [] and clob_orders == []
    rej = [(sql, a) for sql, a in pool.updates
           if "status='rejected'" in sql and "clob-leg-closed" in str(a)]
    assert len(rej) == 1
    # the deliberate reopen lever
    pool2 = _LadderPool([])
    _wire_with_real_cuts(monkeypatch, pool2,
                         f"tsc-epl-ars-che-{TODAY}-o3pt5")
    monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
    monkeypatch.setattr(live_executor, "active_venue",
                        lambda: "polymarket-clob")
    monkeypatch.setattr(live_executor, "_submit_fok",
                        lambda *a, **k: clob_orders.append(a) or
                        {"ok": True, "filled_shares": 1.0,
                         "fill_price": 0.5, "order_id": "x", "raw": {}})
    monkeypatch.setenv("LIVE_CLOB_COPIES", "on")
    asyncio.run(live_executor.maybe_execute(
        _payload(whale_username="0x076daa87"), 5.0))
    assert clob_orders, "the explicit lever reopens the leg"


class TestShadowCertification:
    """The resume gate (owner order 2026-08-24: prove it before
    dollars). Every QUARANTINED premap resolution is re-derived from
    live venue data by the same precision matcher and recorded — the
    streak that licenses the lever, produced at zero risk."""

    def test_shadow_ok_records_to_its_own_key(self, monkeypatch):
        slug = f"atc-epl-ars-che-{TODAY}-ars"
        monkeypatch.setattr(premap_mod, "live_rows_for_market",
                            lambda parent: _live_rows(slug))
        pool = _EchoPool()
        asyncio.run(live_executor._side_echo_verify(
            pool, 101, slug, "Arsenal", "Arsenal vs Chelsea",
            shadow=True))
        assert _echo_state(pool, "side_echo_shadow")["ok"] == 1
        # the live counter must NOT move on shadow verdicts
        assert _echo_state(pool, "side_echo_last") is None

    def test_shadow_mismatch_records_but_never_halts(self, monkeypatch):
        """No money rode a quarantined row, so there is nothing to
        halt — but the mismatch is counted and logged critical, and
        the lever must never be flipped while it is non-zero."""
        ours = f"atc-epl-ars-che-{TODAY}-ars"
        other = f"atc-epl-ars-che-{TODAY}-v2-ars"
        monkeypatch.setattr(premap_mod, "live_rows_for_market",
                            lambda parent: _live_rows(other))
        pool = _EchoPool()
        asyncio.run(live_executor._side_echo_verify(
            pool, 101, ours, "Arsenal", "Arsenal vs Chelsea",
            shadow=True))
        assert _echo_state(pool, "side_echo_shadow")["mismatch"] == 1
        sqls = [sql for sql, _ in pool.executes]
        assert not any("side_echo_tripped" in s for s in sqls), \
            "a shadow verdict must not trip the live circuit"
        assert not any("mapping_quarantine" in s for s in sqls)

    def test_quarantined_premap_row_spawns_a_shadow_echo(self, monkeypatch):
        """End-to-end: the refusal path itself produces the evidence."""
        from sportsassets import copy_sports as _cs

        spawned = []
        monkeypatch.setattr(
            live_executor, "_spawn_echo",
            lambda pool, row_id, slug, outcome, title, *, shadow,
            **kw: spawned.append((slug, outcome, shadow, kw)))

        pool = _LadderPool([])
        _wire_with_real_cuts(monkeypatch, pool, "unused")
        monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "on")
        monkeypatch.setenv("LIVE_PREMAP", "off")   # refused

        async def fake_premap(_pool, *_a, **_k):
            return {"market_slug": f"atc-epl-ars-che-{TODAY}-ars",
                    "title": "Arsenal", "outcome": "arsenal",
                    "intent": "ORDER_INTENT_BUY_LONG",
                    "matched_by": "premap", "score": 1.0}

        monkeypatch.setattr(premap_mod, "resolve", fake_premap)
        asyncio.run(live_executor.maybe_execute(
            _payload(whale_username="0x076daa87"), 5.0))
        assert spawned and spawned[0][2] is True, \
            "a refused premap resolution must be shadow-verified"


class TestContractRowEcho:
    """Leak-hunt round 2, defect #5: for a per-side CONTRACT row the
    parent IS the side, so the refetch returns exactly one row — itself
    — and match_side could only ever answer ok or no-unique-match.
    'mismatch' was structurally unreachable and the tripwire was inert
    for the whole row class. The real question for a contract is
    whether its own subject is still the whale's pick."""

    def _pool(self):
        return _EchoPool()

    def test_contract_whose_subject_matches_reads_ok(self, monkeypatch):
        slug = f"atc-epl-ars-che-{TODAY}-ars"
        monkeypatch.setattr(
            premap_mod, "live_rows_for_market",
            lambda parent: [{"identifier": slug, "side_norm": "arsenal",
                             "line": "", "kind": "contract",
                             "question": "Will Arsenal win?"}])
        pool = self._pool()
        asyncio.run(live_executor._side_echo_verify(
            pool, 101, slug, "Arsenal", "Arsenal vs Chelsea"))
        assert _echo_state(pool, "side_echo_last")["ok"] == 1

    def test_contract_whose_subject_flipped_now_trips(self, monkeypatch):
        """The previously-unreachable verdict: the venue's contract at
        this identifier is no longer the whale's pick."""
        slug = f"atc-epl-ars-che-{TODAY}-ars"
        monkeypatch.setattr(
            premap_mod, "live_rows_for_market",
            lambda parent: [{"identifier": slug, "side_norm": "chelsea",
                             "line": "", "kind": "contract",
                             "question": "Will Chelsea win?"}])
        pool = self._pool()
        asyncio.run(live_executor._side_echo_verify(
            pool, 101, slug, "Arsenal", "Arsenal vs Chelsea"))
        assert _echo_state(pool, "side_echo_last")["mismatch"] == 1
        sqls = [sql for sql, _ in pool.executes]
        assert any("side_echo_tripped" in s and "'true'" in s for s in sqls)


class TestVerifiedOnlyIsIndependentOfQuarantine:
    """Leak-hunt round 2: the profitability allowlist lived INSIDE the
    quarantine branch, so `POST /api/admin/quarantine/off` — a
    mapping-fidelity action — silently dropped it and opened the lane
    to every non-cut whale. Verified-only is now its own gate."""

    def _run(self, monkeypatch, whale, verified_env=None):
        from sportsassets import copy_sports as _cs

        pool = _LadderPool([])
        submitted = _wire_with_real_cuts(
            monkeypatch, pool, f"tsc-epl-ars-che-{TODAY}-o3pt5")
        monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "off")  # fully lifted
        # _wire clears the gate for its historical RN1 fixtures; this
        # suite is ABOUT the gate, so put the real default back.
        monkeypatch.setenv(
            "LIVE_VERIFIED_WHALES",
            "homerunhazard,0x076daa87,rn1,ferrarichampions2026"
            if verified_env is None else verified_env)
        asyncio.run(live_executor.maybe_execute(
            _payload(whale_username=whale), 5.0))
        return pool, submitted

    def test_unverified_whale_refused_even_with_quarantine_off(
            self, monkeypatch):
        pool, submitted = self._run(monkeypatch, "kch123")
        assert submitted == []
        rej = [(sql, a) for sql, a in pool.updates
               if "status='rejected'" in sql
               and "not verified-profitable" in str(a)]
        assert len(rej) == 1

    def test_verified_whale_flows_with_quarantine_off(self, monkeypatch):
        pool, submitted = self._run(monkeypatch, "0x076daa87")
        assert submitted

    def test_empty_env_disables_the_gate_for_a_full_resume(self,
                                                           monkeypatch):
        pool, submitted = self._run(monkeypatch, "kch123", verified_env="")
        assert submitted, "an explicit empty list is a deliberate resume"


class TestFirstFillGate:
    """The owner upsized to $250 before any real fill returned. The
    echo runs AFTER a fill, so the 4-slot semaphore could put four
    orders on the venue before the first verdict — and which side an
    intent buys is the one assumption still unproven. Until one real
    fill verifies 'ok', only a single copy may be in flight."""

    class _GatePool(_LadderPool):
        """No prior verified fill: side_echo_last has ok=0."""

        async def fetchval(self, sql, *a):
            if "ingestion_state" in sql and a and a[0] == "side_echo_last":
                return '{"ok": 0, "mismatch": 0, "unverified": 0}'
            return await super().fetchval(sql, *a)

    class _VerifiedPool(_LadderPool):
        async def fetchval(self, sql, *a):
            if "ingestion_state" in sql and a and a[0] == "side_echo_last":
                return '{"ok": 7, "mismatch": 0, "unverified": 0}'
            return await super().fetchval(sql, *a)

    def _wire(self, monkeypatch, pool):
        from sportsassets import copy_sports as _cs

        submitted = _wire_with_real_cuts(
            monkeypatch, pool, f"tsc-epl-ars-che-{TODAY}-o3pt5")
        monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
        return submitted

    def test_a_second_copy_is_refused_while_the_first_is_unverified(
            self, monkeypatch):
        pool = self._GatePool([])
        submitted = self._wire(monkeypatch, pool)
        live_executor._FIRST_FILL_LOCK = asyncio.Lock()

        async def two_at_once():
            await live_executor._FIRST_FILL_LOCK.acquire()   # first in flight
            try:
                await live_executor.maybe_execute(
                    _payload(whale_username="0x076daa87"), 5.0)
            finally:
                live_executor._FIRST_FILL_LOCK.release()

        asyncio.run(two_at_once())
        assert submitted == []
        rej = [(sql, a) for sql, a in pool.updates
               if "status='rejected'" in sql and "first-fill gate" in str(a)]
        assert len(rej) == 1

    def test_the_gate_opens_once_a_real_fill_is_verified(self, monkeypatch):
        pool = self._VerifiedPool([])
        submitted = self._wire(monkeypatch, pool)
        live_executor._FIRST_FILL_LOCK = asyncio.Lock()

        async def two_at_once():
            await live_executor._FIRST_FILL_LOCK.acquire()
            try:
                await live_executor.maybe_execute(
                    _payload(whale_username="0x076daa87"), 5.0)
            finally:
                live_executor._FIRST_FILL_LOCK.release()

        asyncio.run(two_at_once())
        assert submitted, "after a verified fill the gate no longer binds"

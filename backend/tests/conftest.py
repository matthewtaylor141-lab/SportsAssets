"""Test-wide default: the emergency halt is OFF inside the suite.

The 2026-08-25 halt fails closed in production — `copy_halted()` is
True unless LIVE_COPY_HALT=off — and it sits at the common gate that
every copy crosses. That is correct for the money path and useless for
the suite: it short-circuits maybe_execute before any downstream gate
runs, so 42 tests of the cell gate, verified-only gate, first-fill
gate, quarantine and sizing would pass while testing nothing.

So the suite lifts the halt and `test_copy_halt.py` asserts the
PRODUCTION default separately, with the env cleared. Lifting it here
cannot hide a regression in the halt itself.
"""

import sys
import time

import pytest


@pytest.fixture(autouse=True)
def _lift_emergency_halt(monkeypatch):
    monkeypatch.setenv("LIVE_COPY_HALT", "off")


@pytest.fixture(autouse=True)
def _legacy_net_cap(request, monkeypatch):
    """The per-market / per-game cap at $2,500 for every test written
    under it (2026-09-09 ~21:05Z, owner order: the $2,500 is per TRADE
    -- rules.MIRROR_CLIP_USD -- and rules.MIRROR_NET_CAP_USD is
    UNBOUNDED by code default; the environment may still lower it, and
    that lowered reading is exactly what these fixtures exercise: the
    E1 game room, game_cap_scaled / game_cap_full, the cap at the mark).
    The module attribute is set the way an operator's lowering lands
    it, at tick time; the reader and the import default are untouched.
    A module that declares `NET_CAP_UNBOUNDED = True` (test_cap_per_trade
    pins the production default and the per-trade behaviour) runs on
    the real default."""
    if getattr(request.module, "NET_CAP_UNBOUNDED", False):
        return
    from sportsassets.analytics import mirror_live_rules as rules

    monkeypatch.setattr(rules, "MIRROR_NET_CAP_USD", 2500.0)


@pytest.fixture(autouse=True)
def _no_clob_from_tests(monkeypatch):
    """C7 (2026-09-07): premap.resolve asks edge_marks._game_start for
    his kickoff when a condition_id is handed in, and that read falls
    back to the public CLOB when market_starts has no row. No test
    reaches the network: a test that wants an instant seeds its fake
    pool's market_starts read; every other read is 'unread' (deferred),
    which the resolver names kick:unknown."""
    from sportsassets.workers import edge_marks

    def _refuse(_condition_id):
        raise RuntimeError("no CLOB from tests")

    monkeypatch.setattr(edge_marks, "fetch_game_start", _refuse)


@pytest.fixture(autouse=True)
def _edge_gate_seeded(monkeypatch):
    """The 95% gate has a proven verdict for the fixture whales.

    Same stance as the halt above. The gate fails CLOSED when it cannot
    read a published benchmark, which is right for money and useless
    here: no test publishes `whale_edge_benchmark`, so every one of the
    39 tests downstream of it would refuse at the gate and pass while
    proving nothing about the sizing, mapping and first-fill logic they
    exist to check.

    Seeding the module cache is exactly the state a SUCCESSFUL read
    produces — not a bypass, and there is no bypass to reach for: the
    gate reads no environment variable by design.

    test_edge_gate.py pins the production behaviour — every fail-closed
    path, the interval rule, and the placement below the exit path —
    against these same functions with the cache unseeded.
    """
    import time

    from sportsassets import edge_gate

    monkeypatch.setitem(
        edge_gate._cache, "per_whale",
        {"rn1": {"edge_ci95": [0.0028, 0.046], "edge_roi": 0.0244},
         "swisstony": {"edge_ci95": [0.005, 0.04], "edge_roi": 0.02},
         "homerunhazard": {"edge_ci95": [0.004, 0.05], "edge_roi": 0.026},
         "kch123": {"edge_ci95": [0.004, 0.05], "edge_roi": 0.026},
         "ferrarichampions2026": {"edge_ci95": [0.003, 0.05],
                                  "edge_roi": 0.02},
         "0x076daa87": {"edge_ci95": [0.003, 0.05], "edge_roi": 0.02},
         "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563-1759935795465":
             {"edge_ci95": [0.003, 0.05], "edge_roi": 0.02}})
    import datetime as _dt
    monkeypatch.setitem(edge_gate._cache, "measured_at",
                        _dt.datetime.now(_dt.timezone.utc).isoformat())
    monkeypatch.setitem(edge_gate._cache, "read_at", time.monotonic())
    monkeypatch.setitem(edge_gate._cache, "err", None)
    # refresh() would re-read the (absent) row and clear nothing; make
    # it a no-op so the seeded cache survives the money path calling it.
    async def _noop(_pool):
        return None
    monkeypatch.setattr(edge_gate, "refresh", _noop)


@pytest.fixture(autouse=True)
def _permissive_ask(monkeypatch):
    """The pre-trade ask check needs a readable book.

    It fails CLOSED on an unreadable ask, which is right for money and
    wrong for the suite: the stub venues here expose no order book, so
    every test of an upstream gate would refuse at the ask check and
    pass while proving nothing. Answer with an ask far below any limit
    under test, so the check is satisfied and the gate under test is
    what actually decides.

    test_ask_guard.py exercises the real check against real numbers.
    """
    from sportsassets import pmus

    monkeypatch.setattr(pmus, "side_ask", lambda slug, intent: 0.01)
    # The side-price-band check compares that ask to the WHALE's price,
    # and the fixture whales pay ~0.55 — so a fixed 0.01 stub would trip
    # the band on every test. Widen the band past 1.0 for the suite;
    # test_side_band.py pins the real width and the real refusals.
    # This reaches the copy lane's executor only: the mirror's candidate
    # reads rules.LIVE_SIDE_PRICE_BAND_MAX, a capped_env rail the
    # environment can only LOWER (FILL lane 0a, 2026-09-08), so a mirror
    # test that needs a wider band sets the constant by name.
    monkeypatch.setenv("LIVE_SIDE_PRICE_BAND", "2.0")


@pytest.fixture(autouse=True)
def _clear_overspend_breaker(monkeypatch):
    """Same problem, different switch.

    The overspend breaker reads ingestion_state through the pool, and
    the stub pools in this suite answer EVERY fetchval with a truthy
    value — so the breaker reads "tripped" and short-circuits
    maybe_execute before any gate under test runs. Neutralized here;
    test_overspend_breaker.py exercises the real function directly.
    """
    from sportsassets import live_executor as le

    async def _clear(_pool):
        return None

    monkeypatch.setattr(le, "overspend_halt", _clear)


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """Module-level caches must not leak between tests.

    _SIBLING_CACHE lives on live_executor and is keyed by token with a
    300s TTL, so one test populating it makes the NEXT test's stub pool
    unreachable -- _sibling_from_positions answers from the cache and
    never calls it. test_sibling_fallback passed in isolation and failed
    in the suite, and only surfaced because an unrelated new test file
    changed collection order.

    A test that passes only in one ordering is proving nothing, and the
    ordering it needs is invisible. Cleared before and after, so a test
    inherits nothing and leaves nothing.
    """
    from sportsassets import live_executor as _le

    _le._SIBLING_CACHE.clear()
    _le._SIBLING_CACHE_AT = None
    yield
    _le._SIBLING_CACHE.clear()
    _le._SIBLING_CACHE_AT = None


@pytest.fixture(autouse=True)
def _clear_kalshi_board_cache():
    """The Kalshi board cache (2026-09-05) is module state on the API
    app: 20 s TTL, keyed by series set, holding an asyncio.Lock and
    the background refresh task per key. Two tests that stub
    _kalshi_sweep for the same league inside one 20 s window would
    read each other's board, and a lock that once waited is bound to
    that test's event loop -- the next loop cannot use it. Same stance
    as the cache above: cleared before and after. Only when the app
    module is already imported; importing it here would pull FastAPI
    into every test that never touches it. A test that makes a board
    stale awaits the refresh task it started before it returns."""
    import sys

    def _clear() -> None:
        mod = sys.modules.get("sportsassets.api.app")
        cache = getattr(mod, "_kalshi_board_cache", None)
        if cache is not None:
            cache.clear()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _override_state_does_not_leak():
    """The roster/clip overrides are module state, and since 2026-09-05
    a read that FAILS adopts a CLOSED value (UNREADABLE) instead of
    keeping the last one -- including a pool that cannot be obtained.
    A test that drives a money path against an unreachable pool
    (test_live_executor_mapping's staleness test) therefore left the
    gate closed for every test after it: eleven sizing tests read a
    0.0 clip and failed only in the suite, passing alone. Same stance
    as the cache above: snapshot, then restore, so a test inherits
    nothing and leaves nothing. The files that pin the closed state
    (test_gate_fails_closed, test_verified_set, test_clips_follow_the_
    rules) reset it themselves as well."""
    from sportsassets import live_executor as _le

    names = ("_roster_override", "_clip_override", "_roster_read_at",
             "_closed_read_at", "_closed_since", "_closed_error")
    saved = {n: getattr(_le, n) for n in names}
    yield
    for n, v in saved.items():
        setattr(_le, n, v)


# MODULES WHOSE SUBJECT IS WHAT THE ADAPTER PUTS ON THE WIRE.
#
# An explicit allowlist, not a suite-wide default, and the difference
# matters. _install_snapshot_for_tests does not merely supply a
# snapshot: it REPLACES execution_gate._authorize_read, so bind(), read_state()
# and the whole live read path are never exercised by anything running
# under it. Armed suite-wide, a broken production binding would look
# exactly like a working one -- no test would ever call the code that
# binds.
#
# So the default is the production default: unbound, which denies. Only
# the modules below opt in, each because its subject is the params the
# adapter builds rather than whether the system may trade. Every other
# test in the suite -- including any future one that accidentally
# reaches a submission path -- meets the real gate and fails loudly.
#
# The live initialization and refresh path is exercised, with this
# fixture explicitly off, in test_execution_gate_integration.py.
_GATE_ARMED_MODULES = frozenset({
    "test_pmus",                 # what submit_fok sends
    "test_pmus_commission",      # fee fields on the created order
    "test_preview_guard",        # the venue-cost guard, before the create
    "test_s4_review_pins",       # pinned wire shapes
    "test_side_intent",          # which side the intent selects
    "test_calibration_adapter",  # the adapter's own wire contract
    # R30A: how many creates submit_fok puts on the (mocked) wire per call
    # when the venue answers 429 / 5xx / a timeout / a dropped connection
    "test_order_posts_are_sent_once_and_429s_are_named",
})


@pytest.fixture(autouse=True)
def _gate_default_is_the_production_default(request):
    """Arm the order gate ONLY for the allowlisted modules above.

    Everything else runs with the gate unbound, which is what a process
    that cannot read the kill switch should be: refusing. Restoration
    is unconditional, so an armed module leaves nothing behind for the
    next one -- the same snapshot-and-restore stance as the two fixtures
    above.
    """
    from sportsassets import execution_gate as _gate

    mod = getattr(request.node, "module", None)
    name = mod.__name__.rsplit(".", 1)[-1] if mod else ""
    if name not in _GATE_ARMED_MODULES:
        # Production default. Make sure a previous module's arming
        # cannot leak in: restore before yielding as well as after.
        _gate._restore_for_tests()
        yield
        _gate._restore_for_tests()
        return

    import time
    _gate._install_snapshot_for_tests(_gate.Snapshot(
        paused=False, venue="polymarket-us", copy_halted=False,
        loss_stop=False, overspend=False, read_at=time.time(),
        ok=True, why="allowlisted adapter-behaviour test"))
    yield
    _gate._restore_for_tests()


# ── THE SUITE'S WORKING DIRECTORY, MADE DETERMINISTIC ────────────────
#
# THE DEFECT THIS CLOSES, AND IT COST ME A WRONG REPORT. Tests read two
# families of repository files by RELATIVE path:
#
#     "migrations/103_external_valuations.sql"    -> relative to backend/
#     ".github/workflows/calibration-evidence.yml" -> relative to the ROOT
#
# Those two need DIFFERENT working directories, so no single invocation
# directory satisfies both and the suite's result depended on where pytest
# was started. Run from `backend/`, six calibration tests failed on a file
# that exists; run from the root, twelve entry-lane tests failed on a
# migration that exists. I reported the first set to the owner as eight
# real failures in the funded execution path and named a missing workflow
# file among the causes. The file was not missing. The count was wrong.
#
# A relative path in a test is a path whose meaning depends on the
# operator, so both families are anchored here instead. `BACKEND_ROOT` is
# made the working directory because it is what the majority expect, and
# `REPO_ROOT` is exported for the readers that want the other one, so a
# test never has to guess which it got.
import os                                                    # noqa: E402
import pathlib                                               # noqa: E402

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent

#: Anchored repository paths, for tests that read files rather than code.
#: Prefer these to a bare relative string: the string is only correct from
#: one directory and says nothing about which one.
def backend_path(*parts) -> pathlib.Path:
    return BACKEND_ROOT.joinpath(*parts)


def repo_path(*parts) -> pathlib.Path:
    return REPO_ROOT.joinpath(*parts)


_CWD_WAS = None


def pytest_configure(config):
    """Run the suite from `backend/` wherever pytest was started.

    A HOOK AND NOT A FIXTURE, AND THAT MATTERS. Several tests read
    repository files at MODULE level -- `test_bettor_observation_adapter`
    opens "../research/beta48/acceptance/replay_sample_rows.json" on the
    import line -- which happens during COLLECTION, before any fixture of
    any scope has run. A session-scoped autouse fixture is therefore too
    late: the import has already failed. `pytest_configure` runs before
    collection, so it is early enough to fix every one of them at once.
    """
    global _CWD_WAS
    _CWD_WAS = os.getcwd()
    os.chdir(BACKEND_ROOT)


def pytest_unconfigure(config):
    """Put the caller's shell back where it was."""
    if _CWD_WAS:
        os.chdir(_CWD_WAS)


@pytest.fixture(autouse=True)
def _no_observation_reads_from_tests(monkeypatch):
    """The non-funded pair observer (2026-09-29) runs inside every LIVE entry
    cycle and reads the venue: a displayed book price per contract and, later,
    each contract's settlement. Tests that drive `cycle()` substitute
    `venue_quote`, not these, so without this the observer would reach the
    network from the suite. Both reads refuse here, by name; the observer's
    own tests substitute a quoter and a settlement reader explicitly, and
    `test_the_pairing_model_bootstraps_from_non_funded_observations` pins the
    production wiring."""
    from sportsassets import bettor_pair_observations as po
    from sportsassets.workers import ext_pinnacle_loop as loop

    async def _no_book(slug, side, *, now=None):
        return {"ok": False, "refusal": "NO_VENUE_BOOK_READ_FROM_TESTS"}

    def _no_settlement(slug):
        return {"status": "UNREADABLE", "error": "NO_SETTLEMENT_READ_FROM_TESTS"}

    # The real one stays reachable, by name, for the test that pins it with
    # its own substituted book read.
    monkeypatch.setattr(loop, "_real_observation_quote",
                        loop.observation_quote, raising=False)
    monkeypatch.setattr(loop, "observation_quote", _no_book)
    monkeypatch.setattr(po, "_real_production_settlement",
                        po._production_settlement, raising=False)
    monkeypatch.setattr(po, "_production_settlement", _no_settlement)
    # THE FUNDED SETTLEMENT RE-READ (migration 141) runs inside every funded
    # servicing pass; a test that drives it without its own probe records a
    # re-read that established nothing, never a disagreement.
    from sportsassets import bettor_funded_management as fm

    def _no_recheck(client, slug):
        return {"terminal_reading": "UNREADABLE",
                "why": "NO_SETTLEMENT_RECHECK_READ_FROM_TESTS"}

    monkeypatch.setattr(fm, "_real_settlement_probe", fm._settlement_probe,
                        raising=False)
    monkeypatch.setattr(fm, "_settlement_probe", _no_recheck)


#: Harness users that compare against a REAL database clock (Postgres now()),
#: which cannot be re-anchored; re-anchoring Python's clock there would split
#: the two clocks instead of joining them. Named, not inferred.
_DB_CLOCK_MODULES = frozenset({
    "tests.test_l2_review_pins",
    "tests.test_mirror_loss_sum_real_pg",
})


@pytest.fixture(autouse=True)
def _mirror_harness_clock_is_its_own_now(request, monkeypatch):
    """THE MIRROR HARNESS'S CLOCK IS NOW, NOT THE SUITE'S ELAPSED TIME.

    `tests/test_mirror_live_worker.py` fixes `NOW = time.time()` at IMPORT --
    at collection, for a full-suite run -- and drives `ml.tick_once(...,
    now_ts=NOW)`, while the worker stamps a snapshot's read with the real
    clock (`t.snap_read_at[whale] = time.time()`). Once the suite has run
    longer than the snapshot freshness window before a harness test executes,
    that snapshot reads `snapshot_stale` / `snap_market_stale` against NOW
    and nothing is placed: the e4dc132 and edd2a2f gates reached
    test_e21_fast_add_replan about 9-10 minutes after collection and failed
    it, the dff544c baseline reached it at about 8 minutes and passed, and
    every run that reached it sooner passed. Advancing the real clock by 300 s
    after collection reproduces the failure in isolation.

    The worker is right; the harness's two clocks drifted apart. For every
    test that uses the harness, the real clock is re-anchored to the harness's
    NOW for the duration of the test, so it reads as it did when the tests
    were written: the same relationship, independent of suite length.
    """
    harness = sys.modules.get("tests.test_mirror_live_worker")
    if harness is None:
        yield
        return
    mod = request.module
    uses = mod is harness or any(v is harness for v in vars(mod).values()) \
        or any(getattr(v, "__module__", None) == harness.__name__
               for v in vars(mod).values() if callable(v))
    if not uses or getattr(mod, "__name__", "") in _DB_CLOCK_MODULES:
        yield
        return
    real = time.time
    start = real()
    monkeypatch.setattr(time, "time", lambda: harness.NOW + (real() - start))
    # The worker's own module-level stamps from an earlier test were taken on
    # the real clock; on the re-anchored clock they would sit in the future
    # and throttle this test's ticks. Start each test from the loop's
    # initial values, as a fresh process would (a test that needs one sets it).
    ml = sys.modules.get("sportsassets.workers.mirror_live")
    if ml is not None:
        for name in ("_backoff_until", "_last_tick_at", "_fast_last_at"):
            if hasattr(ml, name):
                monkeypatch.setattr(ml, name, 0.0)
    yield


@pytest.fixture
def new_strategies_off():
    """THE MAKER-ENTRY AND EXPLORATION STRATEGIES (migration 189) OFF for a
    proof of an older policy, so its counts are that policy's alone; their
    rows go back to the migrated launch state afterwards (maker OFF,
    exploration ON). The new strategies' own proofs switch them explicitly."""
    from tests import paper_live_fixture as PL
    migrated = {"PINNACLE_COMPLETED_GAME_MAKER_PAPER": False,
                "PINNACLE_EXPLORATION_PAPER": True}
    for k in migrated:
        PL.set_policy_control(k, False)
    yield
    for k, on in migrated.items():
        PL.set_policy_control(k, on)


@pytest.fixture(autouse=True)
def _capital_authority_seeded(request, monkeypatch):
    """PAPER CAPITAL AUTHORITY (migration 305): the forward-economics verdict
    seeded POSITIVE, and the absent-evidence check lifted, for every proof
    written before the gate existed.

    Same stance as the edge gate above. The gate fails CLOSED -- forward
    economics are UNKNOWN until a strategy has 20 settled forward
    observations, and an ENTRY that carries no capital-eligibility evidence
    is refused -- which is right for the paper book and useless here: no
    older proof settles 20 forward positions before it submits, and the
    ledger proofs submit bare orders, so every one of them would refuse at
    this gate and pass while proving nothing about the cash, cap, lifecycle
    and simulator logic they exist to check.

    Seeded, not bypassed: an order that DOES carry evidence is still held to
    executable EV > 0, and the stopping rules are evaluated at the entry in
    every test. A module that declares `CAPITAL_AUTHORITY_ENFORCED = True`
    (tests/test_capital_authority.py) runs the production functions."""
    if getattr(request.module, "CAPITAL_AUTHORITY_ENFORCED", False):
        return
    from sportsassets import bettor_capital_authority as CA

    async def _seeded(conn, account_id, strategy, *, now, positions=None):
        return dict(CA.forward_verdict([1.0] * CA.MIN_FORWARD_OBSERVATIONS,
                                       []), ok=True, as_of=float(now),
                    seeded="TEST_SUITE_SEEDED_POSITIVE")

    monkeypatch.setattr(CA, "forward_economics", _seeded)
    monkeypatch.setattr(CA, "missing_ev_evidence_refusal", lambda o: None)


@pytest.fixture(autouse=True)
def _profitability_bind_seeded(request, monkeypatch):
    """THE PROFITABILITY BIND (migration 309): the learned-state gates seeded
    neutral for every proof written before the bind existed.

    Same stance as the capital authority above. The bind fails CLOSED: with
    no fitted calibration for a contract's sport x family x regime the
    probability used is the market price (CASH), an unknown regime refuses,
    the champion rule needs a positive CI lower bound, and churn control
    refuses a re-entry within its cooldown -- right for the paper book and
    useless here: older proofs submit bare contracts with no catalogue row
    and no settled history, so every one would refuse at this gate and pass
    while proving nothing about the cash, cap, lifecycle and simulator logic
    they exist to check. Seeded here: `entry_bind` passes the quantity
    through unchanged, `authority_extra` (champion / regime) refuses
    nothing, and Xavier's HOLD probability is the raw measure. A module that
    declares `PROFITABILITY_BIND_ENFORCED = True` (tests/test_profitability_
    bind.py) runs the production functions."""
    if getattr(request.module, "PROFITABILITY_BIND_ENFORCED", False):
        return
    from sportsassets import bettor_paper_profitability_bind as PBIND

    async def _entry(conn, *, qty_in=None, qty_cap=None, **kw):
        qs = [x for x in (qty_in, qty_cap) if x is not None]
        return {"refusal": None, "qty": min(qs) if qs else None,
                "qty_in": qty_in, "seeded": "TEST_SUITE_SEEDED_PASSTHROUGH",
                "descriptor": {"regime": "SEEDED"}}

    async def _extra(conn, **kw):
        return {"refusal": None, "seeded": "TEST_SUITE_SEEDED_PASSTHROUGH"}

    async def _mgmt(conn, *, p_raw=None, **kw):
        return {"p_raw": p_raw, "p_hold": p_raw, "haircut_per_contract": 0.0,
                "status": "TEST_SUITE_SEEDED_PASSTHROUGH"}

    monkeypatch.setattr(PBIND, "entry_bind", _entry)
    monkeypatch.setattr(PBIND, "authority_extra", _extra)
    monkeypatch.setattr(PBIND, "management_economics", _mgmt)

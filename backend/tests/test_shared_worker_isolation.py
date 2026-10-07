"""SHARED WORKERS NEVER SUPERVISE THE UNIVERSAL MARKET PLANE, AND A LOOP OFF
BY CONFIGURATION IS NOT RESTARTED EVERY FIVE SECONDS (completion readiness).

Production 2026-10-07: sportsassets-workers was OOM-killed repeatedly at its
2 GiB limit while the market plane ran beside the capital-critical loops;
UNIVERSAL_MARKET_PLANE=off then made the loop return and be restarted every
RESTART_DELAY_SECONDS forever. Adapted from the completion package's
runtime_patch/tests/test_shared_worker_isolation.py to the current tree
(startable_loops already filtered the venue-write loops)."""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ALL_PY = ROOT / "sportsassets" / "workers" / "all.py"


def test_shared_workers_do_not_start_universal_market_plane(monkeypatch):
    from sportsassets.workers import all as workers_all

    monkeypatch.setenv("UNIVERSAL_MARKET_PLANE", "on")
    names = [name for name, _ in workers_all.startable_loops()]
    assert "universal_market_plane" not in names
    assert "premap" in names
    assert "institutional_md" in names


def test_universal_market_plane_is_dedicated_only_and_not_even_registered():
    from sportsassets.workers import all as workers_all

    assert "universal_market_plane" in workers_all.DEDICATED_ONLY_LOOPS
    assert "universal_market_plane" not in [n for n, _ in workers_all.LOOPS]


def test_even_if_registered_again_it_is_refused_by_source_rule(monkeypatch):
    from sportsassets.workers import all as workers_all

    monkeypatch.setenv("UNIVERSAL_MARKET_PLANE", "on")

    async def fake():
        return None
    loops = list(workers_all.LOOPS) + [("universal_market_plane", fake)]
    assert "universal_market_plane" not in [n for n, _ in workers_all.startable_loops(loops)]


def test_the_shared_worker_module_does_not_import_the_market_plane():
    tree = ast.parse(ALL_PY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = [a.name for a in node.names]
            assert "universal_market_plane" not in names
            assert "universal_market_plane" not in (node.module or "")
        if isinstance(node, ast.Import):
            assert not any("universal_market_plane" in a.name for a in node.names)
    assert "universal_market_plane.run" not in ALL_PY.read_text()


def test_the_boot_marker_names_the_dedicated_only_loops(monkeypatch):
    from sportsassets.workers import all as workers_all

    m = workers_all._boot_marker("2026-10-07T00:00:00+00:00")
    assert m["dedicated_only"] == ["universal_market_plane"]
    assert "universal_market_plane" not in m["started"]
    assert "sportsassets-market-plane" in m["dedicated_only_runtime"]["universal_market_plane"]


async def _drive(factory, monkeypatch, restarts_allowed=2):
    """Run supervise() with sleep patched; stop after `restarts_allowed`
    restarts so a churning loop is observable without waiting."""
    from sportsassets.workers import all as workers_all

    calls = {"n": 0, "sleeps": 0}
    real_factory = factory

    async def counted():
        calls["n"] += 1
        return await real_factory()

    async def fake_sleep(s):
        calls["sleeps"] += 1
        if calls["sleeps"] > restarts_allowed:
            raise asyncio.CancelledError
    monkeypatch.setattr(workers_all.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(workers_all._LH, "spawn_record", lambda *a, **k: None)
    try:
        await workers_all.supervise("x_loop", counted)
    except asyncio.CancelledError:
        pass
    return calls


def test_a_loop_off_by_configuration_is_not_restarted(monkeypatch):
    from sportsassets.workers.loop_contract import LOOP_DISABLED

    async def off():
        return LOOP_DISABLED
    calls = asyncio.run(_drive(off, monkeypatch))
    assert calls["n"] == 1 and calls["sleeps"] == 0


def test_a_clean_none_return_is_still_restarted_so_db_controlled_loops_poll(monkeypatch):
    async def clean():
        return None
    calls = asyncio.run(_drive(clean, monkeypatch, restarts_allowed=2))
    assert calls["n"] == 3


def test_a_crash_is_still_restarted(monkeypatch):
    async def boom():
        raise RuntimeError("x")
    calls = asyncio.run(_drive(boom, monkeypatch, restarts_allowed=2))
    assert calls["n"] == 3


@pytest.mark.parametrize("mod,env", [
    ("institutional_md", "INSTITUTIONAL_MD"),
    ("shadow_bettor", "SHADOW_BETTOR"),
    ("shadow_experimental", "SHADOW_EXPERIMENTAL"),
    ("shadow_rn1", "SHADOW_RN1_DECIDE"),
    ("bettor_state", "BETTOR_STATE_CAPTURE"),
    ("universal_market_plane", "UNIVERSAL_MARKET_PLANE"),
])
def test_env_switched_off_loops_return_the_disabled_contract(monkeypatch, mod, env):
    import importlib

    from sportsassets.workers.loop_contract import LOOP_DISABLED

    m = importlib.import_module("sportsassets.workers." + mod)
    monkeypatch.setenv(env, "off")
    src = (ROOT / "sportsassets" / "workers" / (mod + ".py")).read_text()
    assert "return LOOP_DISABLED" in src
    got = asyncio.run(asyncio.wait_for(m.run(), 5))
    assert got == LOOP_DISABLED

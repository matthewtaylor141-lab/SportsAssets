"""OBSERVABILITY IS OPERATIONALLY INDEPENDENT OF TRADING ENABLEMENT.

Two claims, proved separately because they fail in different directions:

  A. Observability may run while trading is OFF.
     RN1_OBSERVABILITY_SHADOW=true with mirror_live=false must be a legal,
     working configuration. If anything ever makes the collector REQUIRE
     mirror_live=true, that is a coupling which would quietly turn "collect
     evidence" into "collect evidence only while trading", and the evidence we
     most need is from the period when trading is paused.

  B. Observability cannot turn trading ON.
     Setting RN1_OBSERVABILITY_SHADOW=true must not mutate mirror_live, or any
     other trading switch, by any path.

FAIL CLOSED means: if the coupling in (A) is ever introduced, this file fails
and the collector does not ship. It does not mean the collector degrades quietly.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
OBS = BACKEND / "sportsassets" / "obs"
WORKER = BACKEND / "sportsassets" / "workers" / "rn1_observability.py"

# Every switch that can enable trading, anywhere in the tree. The collector must
# neither read one as a precondition nor write one.
TRADING_SWITCHES = (
    "mirror_live", "PMUS_MIRROR", "LIVE_TRADING_ENABLED", "LIVE_COPY_HALT",
    "live_trading_paused", "copy_probe_enabled", "mirror_flatten",
    "mirror_loss_stop", "mirror_loss_rearm", "mirror_post_only_block",
    "MIRROR_SHADOW", "MIRROR_VENUE",
)


def _code_tokens(path: pathlib.Path) -> set[str]:
    """Names, attributes and string literals in a module, excluding docstrings.

    Prose is excluded on purpose: these files explain their relationship to
    mirror_live in English, and a check that cannot tell a sentence from a
    statement would be testing the comments.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) \
               and isinstance(body[0].value, ast.Constant) \
               and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            out.add(node.value)
    return out


def _obs_sources() -> list[pathlib.Path]:
    return sorted(OBS.rglob("*.py")) + [WORKER]


# ---------------------------------------------------------------- claim A
def test_the_collector_starts_with_trading_off(monkeypatch):
    """RN1_OBSERVABILITY_SHADOW=true + mirror_live=false is a legal config."""
    monkeypatch.setenv("RN1_OBSERVABILITY_SHADOW", "true")
    monkeypatch.delenv("PMUS_MIRROR", raising=False)
    from sportsassets.obs.config import shadow_enabled
    assert shadow_enabled() is True, (
        "the collector must be enabled by its own flag alone, with no trading "
        "switch involved"
    )


def test_the_shadow_flag_is_the_only_input_to_shadow_enabled():
    """FAIL CLOSED on a coupling: shadow_enabled reads ONE env var.

    If someone later makes it also consult mirror_live or PMUS_MIRROR, this
    fails -- which is the point. The evidence run 83 exists to gather is from
    exactly the period when trading is paused, so a collector that required
    trading to be on would be unable to collect it.
    """
    path = OBS / "config.py"
    module = ast.parse(path.read_text(), filename=str(path))
    fn = next(n for n in module.body
              if isinstance(n, ast.FunctionDef) and n.name == "shadow_enabled")
    literals = {n.value for n in ast.walk(fn)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    env_names = {l for l in literals if l.isupper() or "_" in l}
    assert env_names == {"RN1_OBSERVABILITY_SHADOW"}, (
        f"shadow_enabled consults {sorted(env_names)}; it must consult only its "
        "own flag"
    )


@pytest.mark.parametrize("switch", TRADING_SWITCHES)
def test_no_observability_module_reads_a_trading_switch(switch):
    """Not as a precondition, not as a gate, not at all."""
    offenders = []
    for path in _obs_sources():
        tokens = _code_tokens(path)
        if any(switch in t for t in tokens):
            offenders.append(path.name)
    assert not offenders, (
        f"{switch} is referenced in code by {offenders}. The collector must be "
        "operationally independent of trading enablement in both directions."
    )


# ---------------------------------------------------------------- claim B
def test_no_observability_module_can_write_a_trading_switch():
    """Nothing here calls a state setter, an env mutation, or an UPDATE.

    Reading is already refused above; this is the write side, checked
    separately because a module could plausibly write without reading.
    """
    forbidden_calls = ("setenv", "putenv", "set_state", "_set_state",
                       "set_switch", "update_state")
    offenders = []
    for path in _obs_sources():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name in forbidden_calls:
                    offenders.append(f"{path.name}:{name}")
            # os.environ[...] = ... would be a Subscript assignment target
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Subscript):
                        base = getattr(tgt.value, "attr", None) \
                            or getattr(tgt.value, "id", None)
                        if base == "environ":
                            offenders.append(f"{path.name}:environ[...]=")
    assert not offenders, (
        "an observability module mutates configuration: " + ", ".join(offenders)
    )


def test_setting_the_shadow_flag_does_not_change_any_trading_switch(monkeypatch):
    """Behavioural, not textual: flip the flag and re-read the trading switches.

    A textual check can be defeated by indirection. This one imports the
    collector with the flag on and confirms every trading switch in the
    environment is exactly where it was.
    """
    import importlib
    import os

    monkeypatch.delenv("RN1_OBSERVABILITY_SHADOW", raising=False)
    before = {k: os.environ.get(k) for k in TRADING_SWITCHES}

    monkeypatch.setenv("RN1_OBSERVABILITY_SHADOW", "true")
    for name in ("sportsassets.obs.config", "sportsassets.obs.clock",
                 "sportsassets.obs.book", "sportsassets.obs.schedule",
                 "sportsassets.obs.record", "sportsassets.obs.collector",
                 "sportsassets.workers.rn1_observability"):
        importlib.reload(importlib.import_module(name))
    from sportsassets.obs.config import shadow_enabled
    assert shadow_enabled() is True

    after = {k: os.environ.get(k) for k in TRADING_SWITCHES}
    assert after == before, (
        "importing the observability package with its flag on changed a trading "
        f"switch: {[k for k in before if before[k] != after[k]]}"
    )


def test_the_worker_returns_rather_than_degrading_when_the_flag_is_off(monkeypatch):
    """Inert by code default, and inert by RETURNING -- not by looping quietly.

    A loop that runs and collects nothing looks identical in the logs to a loop
    that is collecting and failing.
    """
    import asyncio

    monkeypatch.delenv("RN1_OBSERVABILITY_SHADOW", raising=False)
    from sportsassets.workers import rn1_observability
    # No pool, no http client: if main() tried to do anything it would raise.
    asyncio.run(rn1_observability.main())


def test_instrumentation_failure_cannot_enable_trading():
    """Acceptance 12, restated as an import-graph fact.

    The trading path does not import the obs package, so no failure inside the
    instrumentation -- exception, missing env, bad parse -- can change what the
    trading path does. The one edge in the graph runs the other way:
    ingestion/pipeline.py calls into obs, never obs into a trading module.
    """
    trading = ("live_executor.py", "workers/mirror_live.py",
               "workers/copy_sweep.py", "workers/whale_exits.py",
               "workers/underdog.py", "pmus.py")
    for rel in trading:
        path = BACKEND / "sportsassets" / rel
        if not path.exists():
            continue
        tokens = _code_tokens(path)
        assert not any("obs.collector" in t or "obs.record" in t for t in tokens), (
            f"{rel} reaches into the observability package; the dependency must "
            "run only from ingestion into obs"
        )


# ------------------------------------------------ RETENTION: nothing deletes
def test_the_retention_worker_cannot_reach_the_observability_tables():
    """No deletion of run 83 forward observations, enforced where it matters.

    workers/retention.py holds its plan in TABLES and re-checks it against
    PINNED every cycle -- two objects on purpose, so an edit that adds a table
    to one and not the other refuses rather than deletes. This pins rn1_obs_*
    as absent from both, so adding one later has to defeat this test as well.
    """
    from sportsassets.workers import retention
    named = {t[0] for t in retention.TABLES} | {p[0] for p in retention.PINNED}
    assert not any(n.startswith("rn1_obs") for n in named), (
        f"the retention worker names an observability table: {sorted(named)}"
    )
    assert named == {"ai_trades", "copy_probes"}, (
        "the retention worker's table set changed; run 83's evidence must stay "
        "outside it until a cohort is sealed"
    )


def test_the_migration_forbids_deletes_not_just_updates():
    """Retention by construction: a DELETE cannot execute on these tables."""
    sql = (BACKEND / "migrations" / "062_rn1_observability.sql").read_text()
    for table in ("rn1_obs_events", "rn1_obs_snapshots",
                  "rn1_obs_transitions", "rn1_obs_clock_sync"):
        assert f"BEFORE UPDATE OR DELETE ON {table}" in sql
    assert "RAISE EXCEPTION" in sql

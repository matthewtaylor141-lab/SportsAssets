"""The software class is retired (owner 2026-08-12).

"Can we remove the software plays, and just have the copies running!? I
thought we already did" — the class HAD been paused on 08-06, then
re-armed on 08-10 by setting EDGE_STRATEGY_LIVE=1 in the production
environment, which is why it was still trading (and losing) after the
owner believed it was off.

The trap these tests exist to catch: a "kill" implemented by flipping the
EDGE_STRATEGY_LIVE code default is a NO-OP, because the environment sets
that variable to 1 and the environment wins. The first test therefore
reproduces the exact production condition.
"""

from edge.shadow.strategy_gate import strategy_live, strategy_retired


def test_env_armed_strategy_is_still_dark(monkeypatch):
    """PRODUCTION CONDITION: EDGE_STRATEGY_LIVE=1, no retirement var."""
    monkeypatch.setenv("EDGE_STRATEGY_LIVE", "1")
    monkeypatch.delenv("EDGE_STRATEGY_RETIRED", raising=False)
    assert strategy_retired() is True
    assert strategy_live() is False, \
        "an env-armed strategy must not survive the retirement"


def test_retired_by_default_with_no_env_at_all(monkeypatch):
    monkeypatch.delenv("EDGE_STRATEGY_LIVE", raising=False)
    monkeypatch.delenv("EDGE_STRATEGY_RETIRED", raising=False)
    assert strategy_live() is False


def test_owner_can_rearm_without_a_deploy(monkeypatch):
    """Un-retiring restores the ORIGINAL switch, it does not bypass it.

    Since the 2026-08-17 hard-off (test_engine_trades_off.py) re-arming
    also needs EDGE_ENGINE_TRADES=1 — set here so this test keeps
    proving what it always proved, one layer down."""
    monkeypatch.setenv("EDGE_ENGINE_TRADES", "1")
    monkeypatch.setenv("EDGE_STRATEGY_RETIRED", "0")
    monkeypatch.setenv("EDGE_STRATEGY_LIVE", "1")
    assert strategy_live() is True

    monkeypatch.setenv("EDGE_STRATEGY_LIVE", "0")
    assert strategy_live() is False, \
        "un-retiring must not arm a strategy whose own switch is off"


def test_gate_is_read_at_call_time_not_import_time(monkeypatch):
    """The engine is long-lived; an operator must not need a redeploy."""
    monkeypatch.setenv("EDGE_ENGINE_TRADES", "1")   # 08-17 hard-off open
    monkeypatch.setenv("EDGE_STRATEGY_RETIRED", "1")
    assert strategy_live() is False
    monkeypatch.setenv("EDGE_STRATEGY_RETIRED", "0")
    monkeypatch.setenv("EDGE_STRATEGY_LIVE", "1")
    assert strategy_live() is True


def test_runner_gate_delegates_to_the_retirement(monkeypatch):
    from edge.shadow.runner import _strategy_live

    monkeypatch.setenv("EDGE_STRATEGY_LIVE", "1")
    monkeypatch.delenv("EDGE_STRATEGY_RETIRED", raising=False)
    assert _strategy_live() is False


def test_copies_do_not_read_the_strategy_switch():
    """The copy sleeves must be untouched by this — they are the book.

    Source assertion, deliberately: the 2026-08-07 outage happened
    because the Kalshi copy loop sat INSIDE a strategy-gated block and
    went dark with the switch. Nothing about copies may consult it.
    """
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "edge"
    copies = (src / "shadow" / "kalshi_copies.py").read_text()
    assert "EDGE_STRATEGY_LIVE" not in copies
    assert "strategy_live" not in copies
    assert "strategy_retired" not in copies

    # The underdog sleeve is the owner's own $2 dog book, not software.
    dogs = (src / "shadow" / "kalshi_underdog.py").read_text()
    assert "EDGE_STRATEGY_LIVE" not in dogs
    assert "strategy_live" not in dogs


def test_the_kalshi_copy_loop_arms_on_its_own_conditions():
    """Regression on the 08-07 outage, pinned at the call site."""
    import pathlib
    import re

    runner = (pathlib.Path(__file__).resolve().parents[1] / "src" / "edge"
              / "shadow" / "runner.py").read_text()
    m = re.search(r'if os\.environ\.get\("EDGE_KCOPY", "1"\) != "0"[^\n]*\n'
                  r'(?:[^\n]*\n){0,3}', runner)
    assert m, "the copy loop's arming condition moved — re-verify it"
    assert "_strategy_live" not in m.group(0)


def test_crypto_scanner_rides_the_retirement():
    """xv_crypto was outside EDGE_STRATEGY_LIVE entirely — a gap that
    'turn the strategy off' is assumed to close and did not."""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "edge"
           / "shadow" / "xv_crypto.py").read_text()
    assert "strategy_retired" in src
    live_block = src[src.index("live = (bool(self._is_live())"):]
    assert "not strategy_retired()" in live_block[:400]


def test_cycle_telemetry_reports_the_class_state():
    """"Is the software off?" must be a probe READ, not an inference.

    The class ran live for two days on an environment variable while the
    code default said otherwise, and no line on the wire contradicted
    it. The engine now posts its own gate every cycle.
    """
    import pathlib

    runner = (pathlib.Path(__file__).resolve().parents[1] / "src" / "edge"
              / "shadow" / "runner.py").read_text()
    assert 'funnel["strategy_class"]' in runner
    block = runner[runner.index('funnel["strategy_class"]'):][:220]
    assert "_strategy_live()" in block, \
        "the readout must come from the real gate, not a constant"
    assert "retired" in block and "LIVE" in block

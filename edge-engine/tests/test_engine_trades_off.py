"""Engine trades hard-off (owner 2026-08-17): "Shut off the 'engine
trades' completely and only have our trades be copied."

The trap is the same one test_strategy_retired.py exists for, one layer
deeper: the 08-12 retirement is itself an environment variable
(EDGE_STRATEGY_RETIRED) that a re-arm may have set to 0 alongside
EDGE_STRATEGY_LIVE=1. Only a THIRD variable the environment has never
seen (EDGE_ENGINE_TRADES) can guarantee dark-on-deploy, so the first
test reproduces the fully re-armed environment."""

from edge.shadow.strategy_gate import (engine_trades_off, strategy_live,
                                       strategy_retired)


def test_fully_rearmed_env_is_still_dark(monkeypatch):
    """PRODUCTION WORST CASE: both older switches armed in the env."""
    monkeypatch.setenv("EDGE_STRATEGY_RETIRED", "0")
    monkeypatch.setenv("EDGE_STRATEGY_LIVE", "1")
    monkeypatch.delenv("EDGE_ENGINE_TRADES", raising=False)
    assert engine_trades_off() is True
    assert strategy_live() is False, \
        "a re-armed environment must not survive the 08-17 hard-off"


def test_rearming_needs_all_three_switches(monkeypatch):
    monkeypatch.setenv("EDGE_ENGINE_TRADES", "1")
    monkeypatch.delenv("EDGE_STRATEGY_RETIRED", raising=False)
    monkeypatch.delenv("EDGE_STRATEGY_LIVE", raising=False)
    assert strategy_live() is False, \
        "the hard-off alone must not re-arm past the older gates"

    monkeypatch.setenv("EDGE_STRATEGY_RETIRED", "0")
    monkeypatch.setenv("EDGE_STRATEGY_LIVE", "1")
    assert strategy_live() is True


def test_the_hard_off_reaches_strategy_retired_directly(monkeypatch):
    """xv_crypto consults strategy_retired(), not strategy_live() — the
    hard-off must fold in there so every consumer sees one truth."""
    monkeypatch.setenv("EDGE_STRATEGY_RETIRED", "0")
    monkeypatch.delenv("EDGE_ENGINE_TRADES", raising=False)
    assert strategy_retired() is True


def test_read_at_call_time_not_import_time(monkeypatch):
    monkeypatch.setenv("EDGE_STRATEGY_RETIRED", "0")
    monkeypatch.setenv("EDGE_STRATEGY_LIVE", "1")
    monkeypatch.delenv("EDGE_ENGINE_TRADES", raising=False)
    assert strategy_live() is False
    monkeypatch.setenv("EDGE_ENGINE_TRADES", "1")
    assert strategy_live() is True


def test_funnel_reports_the_hard_off_state():
    """"Are engine trades off?" must be a probe READ — the readout has
    to come from the real gate, not a constant string."""
    import pathlib

    runner = (pathlib.Path(__file__).resolve().parents[1] / "src" / "edge"
              / "shadow" / "runner.py").read_text()
    block = runner[runner.index('funnel["strategy_class"]'):][:260]
    assert "engine_trades_off()" in block
    assert "owner order 2026-08-17" in block


def test_copies_and_fsc_do_not_ride_the_engine_switch():
    """The copy sleeves are the book and FSC is the owner's own order —
    neither may consult the strategy gate's on/off (the 08-07 outage was
    a copy loop inside a strategy-gated block). FSC's only gates are its
    own kill switch and the account-level stops."""
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "edge"
    for f in ("kalshi_copies.py", "kalshi_fsc.py"):
        text = (src / "shadow" / f).read_text()
        assert "EDGE_STRATEGY_LIVE" not in text, f
        assert "strategy_live" not in text, f
        assert "strategy_retired" not in text, f
        assert "EDGE_ENGINE_TRADES" not in text, f

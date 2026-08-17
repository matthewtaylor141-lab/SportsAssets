"""Owner order 2026-08-17 night: on Kalshi, the first-set-comeback
sleeve is the ONLY tennis trader ("There should not be any other tennis
plays at all"), and ALL copies happen on Polymarket. These tests pin
every Kalshi order path to that rule."""

import pathlib

from edge.shadow.kalshi_guard import TENNIS_LEAGUES, is_tennis_ticker

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "edge"


def test_tennis_ticker_identifier():
    assert is_tennis_ticker("KXATPMATCH-26AUG17SINALC-SIN")
    assert is_tennis_ticker("KXWTACHALLENGERMATCH-26AUG17ANDPLI-AND")
    assert is_tennis_ticker("KXITFMATCH-26AUG17CASDEA-CAS")
    assert is_tennis_ticker("KXCHALLENGERMATCH-26AUG17BOYWAT-WAT")
    assert not is_tennis_ticker("KXMLBGAME-26AUG17STLCIN-STL")
    assert "atp" in TENNIS_LEAGUES and "itf" in TENNIS_LEAGUES


def test_kalshi_copy_sweep_is_dark_by_default():
    """PM-only routing: the copy loop keeps running (FSC and venue
    truth ride it) but the kcopy sweep call sits behind
    EDGE_KCOPY_PM_ONLY, default ON."""
    runner = (SRC / "shadow" / "runner.py").read_text()
    gate = 'os.environ.get("EDGE_KCOPY_PM_ONLY", "1")'
    assert gate in runner, \
        "PM-only must be the DEFAULT — env-proof, no deploy to re-arm"
    block = runner[runner.index(gate):][:800]
    assert "st = kcopy(" in block, \
        "the sweep call must still exist behind the gate"


def test_underdog_sleeve_refuses_tennis_terminally():
    """Source pin (the sweep's queue is an HTTP relay): the tennis
    refusal must sit in the task loop, consult the shared identifiers,
    and report terminally — so even an unpaused sleeve can never place
    a Kalshi tennis order again."""
    src = (SRC / "shadow" / "kalshi_underdog.py").read_text()
    assert "TENNIS_LEAGUES" in src
    i = src.index("TENNIS_LEAGUES")
    block = src[i - 600:i + 600]
    assert '"tennis_blocked"' in block
    assert "_report(" in block, "the refusal must be TERMINAL (reported)"
    assert src.index('league = str(t.get("league")') < src.index(
        "TENNIS_LEAGUES"), "block must run before any entry logic"


def test_xv_watch_never_registers_a_tennis_pair():
    from edge.shadow.xv_watch import XVWatch

    class _Leg:
        def __init__(self, token):
            self.token = token
            self.adapter = None
            self.outcome = "X"
            self.price = 0.5
            self.size = 100.0

    w = XVWatch(ledger=None, is_live=lambda: False)
    pool = {"Sinner": {"kalshi": _Leg("KXATPMATCH-26AUG17SINALC-SIN"),
                       "polymarket-us": _Leg("0xabc")},
            "Alcaraz": {"kalshi": _Leg("KXATPMATCH-26AUG17SINALC-ALC"),
                        "polymarket-us": _Leg("0xdef")}}
    w.publish("ev1", "Sinner v Alcaraz", "atp", pool)
    assert w.registered() == 0, "tennis pair must never register"

    # League tag alone is enough even if tokens look non-tennis.
    pool2 = {"A": {"kalshi": _Leg("T-A"), "polymarket-us": _Leg("0x1")},
             "B": {"kalshi": _Leg("T-B"), "polymarket-us": _Leg("0x2")}}
    w.publish("ev2", "A v B", "wta", pool2)
    assert w.registered() == 0

    # Non-tennis still registers.
    w.publish("ev3", "A v B", "mlb", pool2)
    assert w.registered() == 1

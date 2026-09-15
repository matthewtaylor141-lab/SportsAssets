"""The TRUEEDGE cut on the Kalshi leg (owner order 2026-08-24, venue
parity): even though this leg is hard-paused (EDGE_KCOPY_PM_ONLY=1),
re-enabling it must never resurrect the cut whales. conftest restores
historical clips for the legacy fixtures, so this test pins the REAL
map by reading the source file itself."""

import re
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1]
       / "src" / "edge" / "shadow" / "kalshi_copies.py").read_text()


def _clip_of(name: str) -> float:
    m = re.search(r'"%s": (\d+(?:\.\d+)?)' % re.escape(name), SRC)
    assert m, f"{name} missing from PER_COPY_USD source"
    return float(m.group(1))


def test_cut_whales_are_zero_in_the_source_map():
    assert _clip_of("rn1") == 0.00
    assert _clip_of("ferrarichampions2026") == 0.00
    assert re.search(r"_W2C33: 0\.00", SRC), "0x2c33 must be a 0.00 block"


def test_no_lingering_sport_cells_for_rn1():
    """A (whale, sport) cell WINS over the whale clip — a lingering rn1
    row would resurrect him past the 0.00 base."""
    assert not re.search(r'\("rn1", "(tennis|baseball|soccer)"\): [1-9]',
                         SRC)


def test_verified_whales_at_parity():
    assert _clip_of("homerunhazard") == 300.00
    assert _clip_of("0x076daa87") == 300.00

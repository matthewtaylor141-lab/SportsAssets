"""C28 P0 (frontend): A RESTING ORDER IS NOT PROTECTION, ON EVERY PAGE.

The served command pages (frontend/public/command, Netlify) never label a
standing / resting / pending quantity as protection, and the position room
renders the server's protection ledger -- position qty, unprotected qty,
standing order qty, filled protection qty, the CONDITIONAL floor IF FILLED,
the realized floor, the current executable exit and the current worst-case
exposure -- with the conditional floor never presented as realized.

The same grep rule runs on the backend branch
(backend/tests/test_resting_is_not_protection.py) over its checkout.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "public" / "command"
FRONTEND_GLOBS = ("position*.js", "positions*.js", "xavier*.js",
                  "workspace.js", "equity-wall.js", "floor*.js", "live.js",
                  "office.js", "paper.js")
_PROT = re.compile(r"protect", re.I)
_STANDING = re.compile(r"standing|resting|pending|awaiting|open .*order", re.I)
_NEG = re.compile(r"\bnot\b|unfilled|until|\bno\b", re.I)
_STR = re.compile(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`[^`]*`")


def frontend_violations(text: str) -> list:
    bad = []
    for m in _STR.finditer(text):
        s = m.group(0)
        if re.fullmatch(r"['\"][A-Z_]+['\"]", s):
            continue        # an enum code (a role name), not a label
        if _PROT.search(s) and _STANDING.search(s) and not _NEG.search(s):
            bad.append(s)
    for line in text.splitlines():
        if re.search(r"(standing|resting)[a-z_]*_qty", line) and re.search(
                r"['\"][^'\"]*\b[Pp]rotect(ion|ed)\b[^'\"]*['\"]", line) and \
                not _NEG.search(" ".join(_STR.findall(line))):
            bad.append(line.strip())
    return bad


def test_the_grep_rule_catches_a_violation():
    assert frontend_violations("fact('Standing protection', qty(p.x))")
    assert frontend_violations(
        "fact('Protection', qty(p.standing_resting_qty))")
    assert not frontend_violations(
        "fact('Standing orders (resting, NOT protection until filled)', "
        "qty(p.standing_resting_qty))")


def test_no_command_page_labels_standing_quantity_as_protection():
    files = sorted({p for g in FRONTEND_GLOBS for p in FRONTEND.glob(g)})
    assert any(p.name == "position.js" for p in files), files
    bad = {p.name: frontend_violations(p.read_text()) for p in files}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, bad


def test_the_position_room_renders_the_whole_protection_ledger():
    js = (FRONTEND / "position.js").read_text()
    for field in ("position_qty", "unprotected_qty", "standing_order_qty",
                  "filled_protection_qty", "conditional_floor_if_filled_usd",
                  "realized_floor_usd", "current_executable_exit",
                  "current_worst_case_exposure_usd", "p.line"):
        assert field in js, field
    for label in ("'Position qty'", "'Unprotected qty'",
                  "'Standing order qty'", "'Filled protection qty'",
                  "'Conditional floor · IF FILLED'", "'Realized floor'",
                  "'Executable exit now'", "'Worst-case exposure now'"):
        assert label in js, label
    # the conditional scenario columns and the if-it-fills card say IF FILLED
    assert "IF FILLED · every standing order (conditional)" in js
    assert "IF FILLED · conditional · " in js
    # the eight canonical states + explicit UNKNOWN in the legend
    assert ("['PROPOSED', 'SUBMITTED', 'RESTING', 'PARTIAL', 'FILLED', "
            "'CANCELLED', 'REJECTED', 'EXPIRED', 'UNKNOWN']") in js
    # each ACTUAL venue shows its OWN connection (KALSHI — NOT_CONNECTED)
    assert "v.connection" in js and "cn.display" in js


def test_live_page_unprotected_never_nets_resting():
    js = (FRONTEND / "live.js").read_text()
    assert "Standing orders (resting, NOT protection until filled)" in js
    assert "Standing protection" not in js

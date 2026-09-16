#!/usr/bin/env python3
"""Position lifecycle and re-entry. The rule management asked about, in code.

BETTOR MAY RE-ENTER A MARKET IT HAS EXITED. Information changes: a player is
scratched, a line moves, the complement that never came now exists. Refusing to
re-enter would be its own error -- it would let one bad exit permanently
blacklist a market whose economics have since become good.

WHAT IS FORBIDDEN IS RE-ENTERING *BECAUSE* OF THE OLD POSITION.

    LOSS_RECOVERY_SIZING   sizing up to win back what was lost
    REVENGE_REENTRY        re-entering because the exit hurt
    BREAKEVEN_TARGETING    holding or adding until the combined book is flat

All three share one mechanism: the prior realised P&L reaches forward and
changes the probability, the size, or the urgency of the next decision. So the
prohibition is structural rather than advisory. A re-entry gets a NEW
POSITION_ID and a NEW ENTRY DECISION, the old position is closed and never
modified, and its P&L becomes SUNK_PNL -- a field the entry gate is not given
and therefore cannot read.

`test_lifecycle.py` proves the entry gate cannot see it by walking the AST for
any reference to SUNK_PNL inside the gate, rather than by trusting this
docstring. A prohibition that only exists in prose is not a prohibition.
"""
from __future__ import annotations

import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from position_state import (  # noqa: E402
    NOT_IDENTIFIED, COUNTERFACTUAL, TERMINAL_STATES, PROVENANCE_CLASSES,
    OBSERVED_ACTUAL_POSITION, ProvenanceViolation,
)

LOSS_RECOVERY_SIZING = "LOSS_RECOVERY_SIZING"
REVENGE_REENTRY = "REVENGE_REENTRY"
BREAKEVEN_TARGETING = "BREAKEVEN_TARGETING"

PROHIBITED_REENTRY_MOTIVES = (LOSS_RECOVERY_SIZING, REVENGE_REENTRY,
                              BREAKEVEN_TARGETING)

# The entry gate is a pure function of CURRENT economics. These are the only
# inputs it is permitted to receive, and SUNK_PNL is deliberately not among
# them.
ENTRY_GATE_INPUTS = ("MARKET_ID", "CURRENT_BOOK", "COMPLEMENT_BOOK",
                     "FAIR_VALUE", "FAIR_VALUE_STATUS", "EVENT_EXPOSURE",
                     "CORRELATION_EXPOSURE", "CAPITAL_AVAILABLE",
                     "SIZE_FROM_CURRENT_ECONOMICS")
ENTRY_GATE_FORBIDDEN_INPUTS = ("SUNK_PNL", "PRIOR_POSITION_PNL",
                               "REALISED_LOSS", "BREAKEVEN_PRICE",
                               "RECOVERY_TARGET")


class ReentryViolation(RuntimeError):
    """Raised when a re-entry would be driven by the closed position."""


def close_position(position, terminal_state, at, realised_pnl=NOT_IDENTIFIED):
    """Close a position and convert its P&L to SUNK_PNL.

    The rename is the point. From here the number is history: it is reported,
    it is auditable, and it is not an input to anything.
    """
    if terminal_state not in TERMINAL_STATES:
        raise ValueError("unknown terminal state: %r" % (terminal_state,))
    if position.get("CLOSED_AT", NOT_IDENTIFIED) != NOT_IDENTIFIED:
        raise ValueError("position already closed at %r"
                         % (position["CLOSED_AT"],))
    out = dict(position)
    out["TERMINAL_STATE"] = terminal_state
    out["CLOSED_AT"] = at
    out["SUNK_PNL"] = realised_pnl
    out["SUNK_PNL_IS_NOT_AN_INPUT_TO_ANY_FUTURE_DECISION"] = True
    out["REOPENABLE"] = False
    return out


def reentry_decision(closed_position, new_position_id, gate_inputs,
                     gate_passes, motive=None, size=None):
    """A re-entry is a NEW decision, or it does not happen.

    Three things are checked, and each of them has been a real failure mode in
    real trading systems:

    1. The old position must be CLOSED. A "re-entry" that mutates the original
       is an average-down wearing a different name.
    2. The gate must be given no field that carries the old P&L forward.
       Passing SUNK_PNL in, even unused, is refused -- because the next edit
       of the gate will use it.
    3. The stated motive must not be one of the three prohibited ones.

    Passing all three does not authorise anything: the re-entry still has to
    clear the CURRENT entry gate on its own economics, which is the
    `gate_passes` argument and is decided elsewhere.
    """
    if closed_position.get("CLOSED_AT", NOT_IDENTIFIED) == NOT_IDENTIFIED:
        raise ReentryViolation(
            "the prior position is still open: re-entry is a NEW position, "
            "never a modification of a live one")
    if new_position_id == closed_position.get("POSITION_ID"):
        raise ReentryViolation(
            "a re-entry needs a NEW POSITION_ID; reusing the old one would "
            "reopen a closed trade")
    leaked = [k for k in gate_inputs if k in ENTRY_GATE_FORBIDDEN_INPUTS]
    if leaked:
        raise ReentryViolation(
            "the entry gate was handed the closed position's P&L: %r. The "
            "gate decides on CURRENT economics only." % (leaked,))
    if motive in PROHIBITED_REENTRY_MOTIVES:
        raise ReentryViolation("prohibited re-entry motive: %r" % (motive,))

    return {
        "NEW_POSITION_ID": new_position_id,
        "NEW_ENTRY_DECISION": True,
        "PRIOR_POSITION_ID": closed_position.get("POSITION_ID"),
        "PRIOR_POSITION_SUNK_PNL": closed_position.get("SUNK_PNL",
                                                       NOT_IDENTIFIED),
        "SUNK_PNL_INFLUENCED_THIS_DECISION": "NO",
        "SIZE_SOURCE": "CURRENT_ECONOMICS_ONLY",
        "SIZE": size if size is not None else NOT_IDENTIFIED,
        "ENTRY_GATE_CLEARED": bool(gate_passes),
        "REENTRY_PERMITTED": bool(gate_passes),
        "LABEL": COUNTERFACTUAL,
    }


def size_is_independent_of_sunk(size_a, size_b):
    """Two otherwise-identical states must size identically, whatever was lost.

    This is the property the prohibition actually asserts, expressed so a test
    can check it rather than a reviewer having to believe it.
    """
    if size_a == NOT_IDENTIFIED or size_b == NOT_IDENTIFIED:
        return NOT_IDENTIFIED
    return D(str(size_a)) == D(str(size_b))

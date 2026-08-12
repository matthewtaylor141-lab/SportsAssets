"""The software-class kill switch — one predicate, every strategy path.

OWNER DIRECTIVE 2026-08-12: "Can we remove the software plays, and just
have the copies running!?"

Terminology, because the daily report's wording caused this confusion:
"software" on the owner's breakdown is a DERIVED REMAINDER (account P&L
minus every attributed sleeve), but the trades behind it are real — the
edge-engine's own fair-value/threshold entries, $25 a ticket since the
owner re-armed the class on 2026-08-10 (config/risk.yaml). Those are what
this switch retires. Whale copies, the underdog cash-out sleeve and the
manual desk are NOT software class and are untouched.

WHY A SECOND VARIABLE, and not just a default flip: the strategy's own
switch, EDGE_STRATEGY_LIVE, is SET TO 1 in the production environment.
Changing its code default from "0" to "0" would have shipped a diff that
reads like a kill and changes nothing at all — the env still wins. The
retirement therefore has to be a gate the running environment does not
already override, and it defaults to RETIRED so no env change is needed
to make the plays stop.

Re-arming is deliberate and needs no deploy: set EDGE_STRATEGY_RETIRED=0
on the engine service, and the class returns to whatever
EDGE_STRATEGY_LIVE says.

Strategy intents keep PAPER-logging every cycle. The model stays graded
against the shadow record for the day it earns its seat back; it just
stops spending money to be measured.
"""

from __future__ import annotations

import os


def strategy_retired() -> bool:
    """True when the software class may place no real orders at all.

    Read at CALL time, never cached at import: the engine is a
    long-lived process and an operator must be able to flip this
    without a redeploy.
    """
    return os.environ.get("EDGE_STRATEGY_RETIRED", "1") != "0"


def strategy_live() -> bool:
    """True only when software-class entries may execute for real."""
    if strategy_retired():
        return False
    return os.environ.get("EDGE_STRATEGY_LIVE", "0") == "1"

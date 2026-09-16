#!/usr/bin/env python3
"""The Phase-2A harvest. Reads a sealed capture off disk. Contacts nothing.

WHAT THIS PRODUCES, in the order the answer has to be read:

    THE CAPTURE          markets, validated events, sport mix, rows, errors
    THE FIELD            what SHARES_TRADED did, and what it still does not mean
    THE BOOK             spread, uptime, update rate, quote lifetime, depth --
                         every one of them measurable, none of them a fill rate
    THE EVENT LADDER     HYPOTHETICAL_QUOTES / TOUCHES / TRADE_EVIDENCE /
                         ADMITTED_COUNTERFACTUAL_FILLS, as four separate counts
    THE BOTTLENECK       PUBLIC_TICK_DATA_SUFFICIENT_FOR_FILL_IDENTIFICATION

THE ANSWER TO THE LAST ONE IS EXPECTED TO BE NO, and this module is written so
that saying so is the easy path rather than the awkward one. If admitted fills
are zero because execution evidence does not exist, the report says exactly
that, names the evidence that would settle it, and does NOT reach for another
approximation. A third fill heuristic would be the same mistake as the first
two, with more machinery around it.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import book_metrics as BM                                      # noqa: E402
import maker_fill as MF                                        # noqa: E402
import tick_semantics as TS                                    # noqa: E402

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# One hypothetical quote every N observations per market, so the sample is not
# dominated by whichever market updated most often.
QUOTE_EVERY_N_TICKS = 10
QUOTE_SIZE = "10"
QUOTE_HORIZON_S = 300

# What would settle the fill question. Named here so the report can point at it
# instead of inventing a fourth model.
EVIDENCE_THAT_WOULD_IDENTIFY_A_FILL = (
    "PUBLIC_EXECUTION_TAPE_WITH_TIMESTAMP_PRICE_QUANTITY",
    "AN_EXECUTION_TYPE_COLUMN_OR_A_BLOCK_PUBLICATION",
    "A_SIDE_OR_AGGRESSOR_FLAG",
    "QUEUE_POSITION_OR_ORDER_LEVEL_BOOK_UPDATES",
)
NEXT_STEP_IF_INSUFFICIENT = (
    "OBTAIN_EVIDENCE_CAPABLE_OF_RESOLVING_EXECUTION_AND_QUEUE, "
    "OR A BOUNDED MICRO-LIVE VALIDATION AFTER EXPLICIT AUTHORIZATION")
DO_NOT_INVENT_ANOTHER_FILL_APPROXIMATION = True


def read_capture(path):
    """Read ticks.jsonl or ticks.jsonl.gz. Local file only."""
    p = Path(path)
    op = gzip.open if p.suffix == ".gz" else open
    rows = []
    with op(p, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def sport_of(slug, universe=None):
    """The stratum a slug was frozen into, from the universe file if present."""
    if universe:
        for s in universe.get("SELECTED", []) or []:
            if (s.get("slug") if isinstance(s, dict) else s) == slug:
                return (s.get("STRATUM") if isinstance(s, dict)
                        else NOT_IDENTIFIED)
    parts = (slug or "").split("-")
    return parts[1] if len(parts) > 1 else NOT_IDENTIFIED


def hypothetical_quotes(rows, every=QUOTE_EVERY_N_TICKS, size=QUOTE_SIZE,
                        horizon_s=QUOTE_HORIZON_S, model="F1",
                        semantics=None, runtime=None):
    """Evaluate one resting BID per market every `every` observations.

    No tape is joined because none exists for this capture, so every row comes
    back UNKNOWN. The walk is still run in full: the counts of TOUCH and of the
    reason each quote is unresolved are the measurement.
    """
    by_slug = {}
    for r in rows:
        if r.get("kind") == "TICK_ERROR" or r.get("ELAPSED_S") is None:
            continue
        by_slug.setdefault(r.get("slug"), []).append(r)
    out = []
    for slug, rs in by_slug.items():
        rs.sort(key=lambda r: r["ELAPSED_S"])
        for i in range(0, len(rs), every):
            q = MF.hypothetical_quote(rs[i], MF.SIDE_BID, size)
            if q["QUOTE_PRICE"] == NOT_IDENTIFIED:
                continue
            w = MF.walk_after_entry(q, rs[i + 1:], horizon_s=horizon_s)
            out.append(MF.fill_status(q, w, model=model, semantics=semantics,
                                      runtime=runtime))
    return out


def harvest(path, universe_path=None, event_index=None):
    """The whole Phase-2A report for one sealed capture."""
    rows = read_capture(path)
    universe = None
    if universe_path and Path(universe_path).exists():
        universe = json.loads(Path(universe_path).read_text())

    slugs = sorted({r.get("slug") for r in rows if r.get("slug")})
    mix = {}
    for s in slugs:
        k = sport_of(s, universe)
        mix[k] = mix.get(k, 0) + 1

    field = TS.diagnose(rows)
    book = BM.report(rows)
    quotes = hypothetical_quotes(rows, runtime=field)
    ladder = MF.summarise_fills(quotes)

    admitted = ladder["ADMITTED_COUNTERFACTUAL_FILLS"]
    sufficient = "YES" if admitted > 0 else "NO"

    return {
        # --- the capture ---
        "CAPTURE_PATH": str(path),
        "ROWS": len(rows),
        "TICK_ERRORS": book["TICK_ERRORS"],
        "MARKETS": len(slugs),
        "SPORT_MIX": mix,
        "VALIDATED_EVENTS": (event_index.get("VALID_EVENTS")
                             if event_index else NOT_IDENTIFIED),

        # --- the field, with its two halves apart ---
        "SHARES_TRADED_RUNTIME": {k: field[k] for k in (
            "MONOTONIC_WITHIN_MARKET", "NEGATIVE_DELTAS_OBSERVED",
            "RESET_EVENTS_OBSERVED", "MISSING_TRANSITIONS",
            "FIELD_UPDATE_FREQUENCY", "SAME_VALUE_DESPITE_BOOK_CHANGES",
            "LARGE_DISCONTINUITIES", "OBSERVED_TRANSITIONS")},
        "SHARES_TRADED_VENUE_SEMANTICS": field["VENUE_SEMANTICS"],
        "SHARES_TRADED_DELTA_STATUS": TS.SHARES_TRADED_DELTA_STATUS,
        "RUNTIME_BEHAVIOUR_IS_NOT_VENUE_SEMANTICS": True,

        # --- the book ---
        "BOOK": book,

        # --- the ladder, four separate counts ---
        "HYPOTHETICAL_QUOTES": ladder["HYPOTHETICAL_QUOTES"],
        "TOUCHES": ladder["TOUCHES"],
        "TRADE_EVIDENCE": ladder["TRADE_EVIDENCE"],
        "ADMITTED_COUNTERFACTUAL_FILLS": admitted,
        "WHY_UNRESOLVED": {
            "NO_PER_PRICE_ATTRIBUTION": ladder["UNKNOWN_NO_ATTRIBUTION"],
            "VOLUME_BOUND_INSUFFICIENT":
                ladder["UNKNOWN_VOLUME_BOUND_INSUFFICIENT"],
            "WINDOW_UNREADABLE": ladder["UNKNOWN_WINDOW_UNREADABLE"],
        },

        # --- the bottleneck, stated plainly ---
        "PUBLIC_TICK_DATA_SUFFICIENT_FOR_FILL_IDENTIFICATION": sufficient,
        "WHY": ("no execution evidence exists in this capture: the tick feed "
                "carries no per-print tape, no side and no queue position, so "
                "TRADE_EVIDENCE is NOT_IDENTIFIED and no fill can be admitted"
                if sufficient == "NO" else "execution evidence was joined"),
        "EVIDENCE_THAT_WOULD_IDENTIFY_A_FILL":
            list(EVIDENCE_THAT_WOULD_IDENTIFY_A_FILL),
        "NEXT_STEP": NEXT_STEP_IF_INSUFFICIENT,
        "DO_NOT_INVENT_ANOTHER_FILL_APPROXIMATION":
            DO_NOT_INVENT_ANOTHER_FILL_APPROXIMATION,

        # --- and what is still not reportable ---
        "PROFITABILITY": NOT_IDENTIFIED,
        "WIN_RATE": NOT_IDENTIFIED,
        "EXPECTED_MONTHLY_RETURN": NOT_IDENTIFIED,
        "ORDERS_PLACED": 0,
        "CAPITAL_DEPLOYED": 0,
        "mirror_live": False,
    }


def _jsonable(o):
    from decimal import Decimal as D
    if isinstance(o, D):
        return str(o)
    raise TypeError("not JSON serializable: %s" % type(o).__name__)


if __name__ == "__main__":                                    # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--universe", default=None)
    a = ap.parse_args()
    print(json.dumps(harvest(a.capture, a.universe), indent=1, sort_keys=True,
                     default=_jsonable))

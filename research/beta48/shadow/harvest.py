#!/usr/bin/env python3
"""The Phase-2A harvest. Reads a sealed capture off disk. Contacts nothing.

FOUR SECTIONS, in the order the answer has to be read:

    A  CAPTURE QUALITY   rows, markets, events, duration, REVISIT CADENCE,
                         missingness, failed reads -- and the SAMPLE SCOPE
    B  BOOK STRUCTURE    spread, uptime, touch depth, depth change, book and
                         mid change frequency, time at price. All _OBSERVED.
    C  HYPOTHETICAL      quotes, touches, MOVE_THROUGH events, post-quote book
       QUOTE PATH        markouts. A price path, not an execution record.
    D  EXECUTION         TRADE_EVIDENCE, COUNTERFACTUAL_FILLS, and then
       IDENTIFICATION    PUBLIC_TICK_DATA_SUFFICIENT_FOR_FILL_IDENTIFICATION

THE ANSWER TO THE LAST ONE IS EXPECTED TO BE NO, and this module is written so
that saying so is the easy path rather than the awkward one. If admitted fills
are zero because execution evidence does not exist, the conclusion is
PUBLIC_BOOK_SNAPSHOTS_CANNOT_IDENTIFY_FILL -- not "we need a more aggressive
approximation". A third fill heuristic would be the first two's mistake with
more machinery around it.

TWO SCOPE GUARDS THAT TRAVEL WITH EVERY FIGURE.

    24 CAPTURED MARKETS ARE NOT 788. The capture was stratified by sport and
    frozen before any tick was seen, which makes it reproducible and
    unrigged -- it does not make it representative. Nothing here is scaled to
    the candidate universe, and CAPTURE_SAMPLE_REPRESENTATIVE_OF_788 is
    NOT_ESTABLISHED.

    MARKETS FROM ONE EVENT ARE NOT INDEPENDENT. Where an event map is
    available, every rate is reported market-weighted AND event-weighted, so a
    single event family carrying several captured markets cannot decide the
    number by itself.
"""
from __future__ import annotations

import gzip
import json
import sys
from decimal import Decimal as D
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
CONCLUSION_IF_ZERO = "PUBLIC_BOOK_SNAPSHOTS_CANNOT_IDENTIFY_FILL"
NOT_THE_CONCLUSION = "WE_NEED_A_MORE_AGGRESSIVE_APPROXIMATION"

# ---------------------------------------------------------------------------
# THE VALIDITY GATE. A capture can work perfectly as a pipeline and still be
# useless as a sample, and run 35120338223 was exactly that. So the report
# decides, from its own numbers, whether its book figures may be read as
# market characterisation at all -- and when they may not, it says so in a
# label that travels on every one of them.
# ---------------------------------------------------------------------------

DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY_RATE_LIMIT_SELECTED_SAMPLE"

MAX_ACCEPTABLE_FAILED_READ_SHARE = D("0.05")
MIN_ACCEPTABLE_MARKET_YIELD = D("0.90")      # observed / attempted
MIN_SPORTS_OBSERVED = 2

# What a quarantined figure may NOT be used for. Listed so the prohibition is
# machine-readable and cannot be lost in prose.
QUARANTINED_FIGURES_MAY_NOT_ENTER = (
    "BETTOR_STRATEGY_THRESHOLDS", "QUOTE_PLACEMENT_CALIBRATION",
    "SPORT_COMPARISONS", "CAPACITY", "PROFITABILITY",
    "MANAGEMENT_GENERALIZATIONS",
)


def capture_validity(a, book):
    """Is this capture eligible for substantive book analysis? Six checks.

    A pipeline that ran to completion is not a sample. This function separates
    the two questions and answers both, because the first one being YES is
    exactly what makes the second one easy to forget.
    """
    attempted = a["MARKETS_ATTEMPTED"]
    observed = a["MARKETS_WITH_ANY_READABLE_BOOK"]
    share = a["FAILED_READ_SHARE"]
    yield_ = (D(observed) / D(attempted)) if attempted else D(0)
    sports = len([k for k, v in a["SPORT_MIX_OBSERVED"].items() if v])
    checks = {
        "FAILED_READ_SHARE_ACCEPTABLE": (
            share != NOT_IDENTIFIED and share <= MAX_ACCEPTABLE_FAILED_READ_SHARE),
        "MARKET_YIELD_ACCEPTABLE": yield_ >= MIN_ACCEPTABLE_MARKET_YIELD,
        "MULTIPLE_SPORTS_RETURNED_BOOKS": sports >= MIN_SPORTS_OBSERVED,
        "NO_WHOLE_MARKET_STARVATION": observed == attempted,
        "EVENT_IDENTITY_PRESENT":
            a.get("VALIDATED_DISTINCT_EVENTS") != NOT_IDENTIFIED,
        "MISSINGNESS_RANDOM": not a["MISSINGNESS"]["MISSINGNESS_IS_NOT_RANDOM"],
    }
    ok = all(checks.values())
    return {
        "CAPTURE_PIPELINE_FUNCTIONAL": "YES",
        "CAPTURE_VALIDITY": "PASS" if ok else "FAIL",
        "MARKET_CHARACTERIZATION_VALID": "YES" if ok else "NO",
        "CAPTURE_MISSINGNESS_RANDOM": (
            "YES" if checks["MISSINGNESS_RANDOM"] else "NO"),
        "CHECKS": checks,
        "FAILED_CHECKS": [k for k, v in checks.items() if not v],
        "MARKET_YIELD": yield_,
        "SPORTS_WITH_READABLE_BOOKS": sports,
        "SECTION_B_AND_C_STATUS": (None if ok else DIAGNOSTIC_ONLY),
        "QUARANTINED_FIGURES_MAY_NOT_ENTER": (
            [] if ok else list(QUARANTINED_FIGURES_MAY_NOT_ENTER)),
        "WHY": ("every check passed" if ok else
                "the pipeline ran; the SAMPLE did not survive. Book figures "
                "here describe whichever markets the venue happened to let "
                "through, and that is not the venue"),
    }

# The candidate universe the 24 were drawn from. A count of markets, and -- per
# the correction to section 15a -- not a capacity.
CANDIDATE_UNIVERSE = 788
CANDIDATE_UNIVERSE_MEANING = "HIGH_ACTIVITY_CANDIDATE_MARKETS_AT_DECISION"


def sample_scope(universe, captured_markets):
    """How the 24 were chosen, and what that does NOT license.

    Freezing the selection before any tick was seen buys reproducibility and
    rules out picking the markets that flattered the result. It does not buy
    representativeness: the strata were sports, the draw inside each was a
    salted hash, and no design was chosen to make the 24 stand for the 788.
    """
    u = universe or {}
    return {
        "CANDIDATE_UNIVERSE": CANDIDATE_UNIVERSE,
        "CANDIDATE_UNIVERSE_MEANING": CANDIDATE_UNIVERSE_MEANING,
        "CAPTURED_MARKETS": captured_markets,
        "CAPTURE_SELECTION_RULE": u.get("SELECTION", NOT_IDENTIFIED),
        "SELECTION_SALT": u.get("SELECTION_SALT", NOT_IDENTIFIED),
        "SPORT_STRATIFICATION": u.get("PER_STRATUM_SELECTED", NOT_IDENTIFIED),
        "AVAILABLE_PER_STRATUM": u.get("AVAILABLE_PER_STRATUM",
                                       NOT_IDENTIFIED),
        # The strata were SPORTS. No event stratification was applied, which is
        # why section 4's event weighting exists at all.
        "EVENT_STRATIFICATION": "NONE_APPLIED",
        "SELECTION_TIMESTAMP": u.get("SOURCE_CENSUS_RUN", NOT_IDENTIFIED),
        "SELECTION_FROZEN_BEFORE_CAPTURE": (
            "YES" if u.get("FROZEN_BEFORE_ANY_TICK_WAS_SEEN") else
            NOT_IDENTIFIED),
        "NOT_SELECTED_BY_ACTIVITY_RANK": bool(
            u.get("NOT_SELECTED_BY_ACTIVITY_RANK")),

        "CAPTURE_SAMPLE_REPRESENTATIVE_OF_788": "NOT_ESTABLISHED",
        "WHY": ("stratified by SPORT and frozen before any tick -- "
                "reproducible and unrigged, but no sampling design was chosen "
                "to make these 24 stand for the 788"),
        "EXTRAPOLATION_TO_THE_UNIVERSE": "NOT_PERFORMED",
        "ANY_PERCENTAGE_HERE_DESCRIBES_THE_24": True,
    }


def event_map_from_board(board_path, slugs=None):
    """slug -> venue-native EVENT_ID, read from a sealed board walk.

    Uses `event_identity`, which reads the venue's own marketSides rather than
    a slug. Markets whose identity is unresolved are simply absent from the
    map: an unresolved market is not quietly given an event of its own.
    """
    import event_identity as EI
    want = set(slugs) if slugs else None
    out = {}
    p = Path(board_path)
    op = gzip.open if p.suffix == ".gz" else open
    with op(p, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except ValueError:
                continue
            slug = m.get("slug")
            if want is not None and slug not in want:
                continue
            key, lv = EI.event_identity(m)
            if lv == EI.LEVEL_V1_CONTEST and key != EI.NOT_IDENTIFIED:
                out[slug] = key
    return out


def event_clustering(slugs, event_of):
    """Distinct events inside the capture, and how the markets cluster."""
    if not event_of:
        return {"VALIDATED_DISTINCT_EVENTS": NOT_IDENTIFIED,
                "MARKETS_PER_EVENT": NOT_IDENTIFIED,
                "MARKETS_WITH_UNRESOLVED_EVENT_IDENTITY": len(slugs),
                "MARKETS_TREATED_AS_INDEPENDENT": False,
                "WHY": "no event map supplied; independence is NOT assumed"}
    groups = {}
    unresolved = 0
    for s in slugs:
        k = event_of.get(s)
        if k is None:
            unresolved += 1
            continue
        groups.setdefault(k, []).append(s)
    sizes = sorted(len(v) for v in groups.values())
    return {
        "VALIDATED_DISTINCT_EVENTS": len(groups),
        "MARKETS_PER_EVENT": {
            "N": len(sizes),
            "MIN": sizes[0] if sizes else NOT_IDENTIFIED,
            "P50": sizes[len(sizes) // 2] if sizes else NOT_IDENTIFIED,
            "MAX": sizes[-1] if sizes else NOT_IDENTIFIED,
        },
        "MARKETS_WITH_UNRESOLVED_EVENT_IDENTITY": unresolved,
        "MARKETS_FROM_ONE_EVENT_ARE_NOT_INDEPENDENT": True,
        "MARKETS_TREATED_AS_INDEPENDENT": False,
        "DEPLOYABLE_CAPITAL_CAPACITY": NOT_IDENTIFIED,
    }


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


def harvest(path, universe_path=None, event_of=None, board_path=None):
    """The whole Phase-2A report for one sealed capture, in four sections."""
    rows = read_capture(path)
    universe = None
    if universe_path and Path(universe_path).exists():
        universe = json.loads(Path(universe_path).read_text())

    # ATTEMPTED IS NOT OBSERVED, AND THIS CAPTURE MADE THAT MATTER. A slug
    # appears in the rows whether the read returned a book or an error, so
    # counting distinct slugs counts what we ASKED FOR. The markets that
    # actually produced a book are a separate, smaller set, and every figure in
    # section B describes only those.
    slugs = sorted({r.get("slug") for r in rows if r.get("slug")})
    observed = sorted({r.get("slug") for r in rows
                       if r.get("slug") and r.get("kind") != "TICK_ERROR"})
    mix, mix_obs = {}, {}
    for s in slugs:
        k = sport_of(s, universe)
        mix[k] = mix.get(k, 0) + 1
    for s in observed:
        k = sport_of(s, universe)
        mix_obs[k] = mix_obs.get(k, 0) + 1

    # Why the reads failed, in the venue's own words, and where.
    fail_kind, fail_status, fail_by_slug = {}, {}, {}
    for r in rows:
        if r.get("kind") != "TICK_ERROR":
            continue
        e = str(r.get("error"))[:60]
        fail_kind[e] = fail_kind.get(e, 0) + 1
        st = str(r.get("status"))
        fail_status[st] = fail_status.get(st, 0) + 1
        fail_by_slug[r.get("slug")] = fail_by_slug.get(r.get("slug"), 0) + 1

    if event_of is None and board_path and Path(board_path).exists():
        event_of = event_map_from_board(board_path, slugs)

    field = TS.diagnose(rows)
    book = BM.report(rows, event_of=event_of)
    quotes = hypothetical_quotes(rows, runtime=field)
    ladder = MF.summarise_fills(quotes)
    clusters = event_clustering(slugs, event_of)

    elapsed = [r["ELAPSED_S"] for r in rows if r.get("ELAPSED_S") is not None]
    admitted = ladder["ADMITTED_COUNTERFACTUAL_FILLS"]
    sufficient = "YES" if admitted > 0 else "NO"

    out = {
        # =================================================== A. CAPTURE =====
        "A_CAPTURE_QUALITY": dict({
            "CAPTURE_PATH": str(path),
            "ROWS": len(rows),

            # Attempted vs observed, never collapsed into one "MARKETS".
            "MARKETS_ATTEMPTED": len(slugs),
            "MARKETS_WITH_ANY_READABLE_BOOK": len(observed),
            "MARKETS": len(observed),
            "SPORT_MIX_ATTEMPTED": mix,
            "SPORT_MIX_OBSERVED": mix_obs,
            "SPORT_MIX": mix_obs,
            "EVERY_SECTION_B_FIGURE_DESCRIBES_THE_OBSERVED_MARKETS_ONLY": True,

            "DURATION_S": (max(elapsed) - min(elapsed)) if elapsed else
                          NOT_IDENTIFIED,
            "FAILED_READS": book["TICK_ERRORS"],
            "FAILED_READ_SHARE": (
                (D(book["TICK_ERRORS"]) / D(len(rows))) if rows
                else NOT_IDENTIFIED),
            "FAILED_READ_KINDS": fail_kind,
            "FAILED_READ_STATUS": fail_status,
            "MARKETS_WITH_ZERO_READABLE_BOOKS": len(slugs) - len(observed),
            "MISSINGNESS": {
                "TICK_ERRORS": book["TICK_ERRORS"],
                "SHARES_TRADED_MISSING_TRANSITIONS":
                    field["MISSING_TRANSITIONS"],
                "MISSINGNESS_IS_NOT_RANDOM": (len(observed) < len(slugs)),
                "WHY": ("whole markets failed rather than scattered reads, so "
                        "the observed set is a SELECTED subset of the frozen "
                        "universe and the sport stratification did not survive"
                        if len(observed) < len(slugs) else "no market was lost"),
            },
            "REVISIT_CADENCE": book["REVISIT_CADENCE"],
            "SAMPLE_SCOPE": sample_scope(universe, len(slugs)),
        }, **clusters),

        # =================================================== B. THE BOOK ====
        "B_BOOK_STRUCTURE": book,

        # THE FIELD. Kept beside the book because it is a property of the
        # capture, not of the market -- and its two halves stay apart.
        "SHARES_TRADED_RUNTIME": {k: field[k] for k in (
            "MONOTONIC_WITHIN_MARKET", "NEGATIVE_DELTAS_OBSERVED",
            "RESET_EVENTS_OBSERVED", "MISSING_TRANSITIONS",
            "FIELD_UPDATE_FREQUENCY", "SAME_VALUE_DESPITE_BOOK_CHANGES",
            "LARGE_DISCONTINUITIES", "OBSERVED_TRANSITIONS")},
        "SHARES_TRADED_VENUE_SEMANTICS": field["VENUE_SEMANTICS"],
        "SHARES_TRADED_DELTA_STATUS": TS.SHARES_TRADED_DELTA_STATUS,
        "RUNTIME_BEHAVIOUR_IS_NOT_VENUE_SEMANTICS": True,

        # ============================================ C. THE QUOTE PATH =====
        "C_HYPOTHETICAL_QUOTE_PATH": {
            "HYPOTHETICAL_QUOTES": ladder["HYPOTHETICAL_QUOTES"],
            "TOUCHES": ladder["TOUCHES"],
            "MOVE_THROUGH_OBSERVED":
                book["MARKET_MOVED_THROUGH_QUOTE_OBSERVED"],
            "MOVE_THROUGH_OBSERVED_MARKET_WEIGHTED_RESOLVED_ONLY":
                book.get("MARKET_MOVED_THROUGH_QUOTE_OBSERVED"
                         "_MARKET_WEIGHTED_RESOLVED_ONLY", NOT_IDENTIFIED),
            "MOVE_THROUGH_OBSERVED_EVENT_WEIGHTED_RESOLVED_ONLY":
                book.get("MARKET_MOVED_THROUGH_QUOTE_OBSERVED"
                         "_EVENT_WEIGHTED_RESOLVED_ONLY", NOT_IDENTIFIED),
            "POST_QUOTE_BOOK_MARKOUTS": {
                k: v for k, v in book.items()
                if k.startswith("POST_QUOTE_BOOK_MARKOUT")},
            "MOVE_THROUGH_IS_NOT_TRADE": True,
            "MOVE_THROUGH_IS_NOT_A_COUNTERFACTUAL_FILL": True,
        },

        # ================================ D. EXECUTION IDENTIFICATION =======
        "D_EXECUTION_IDENTIFICATION": {
            "TRADE_EVIDENCE": ladder["TRADE_EVIDENCE"],
            "COUNTERFACTUAL_FILLS": admitted,
            "WHY_UNRESOLVED": {
                "NO_PER_PRICE_ATTRIBUTION": ladder["UNKNOWN_NO_ATTRIBUTION"],
                "VOLUME_BOUND_INSUFFICIENT":
                    ladder["UNKNOWN_VOLUME_BOUND_INSUFFICIENT"],
                "WINDOW_UNREADABLE": ladder["UNKNOWN_WINDOW_UNREADABLE"],
            },
            "PUBLIC_TICK_DATA_SUFFICIENT_FOR_FILL_IDENTIFICATION": sufficient,
            "CONCLUSION": (CONCLUSION_IF_ZERO if sufficient == "NO"
                           else "EXECUTION_EVIDENCE_WAS_JOINED"),
            "NOT_THE_CONCLUSION": NOT_THE_CONCLUSION,
            "WHY": ("no execution evidence exists in this capture: the tick "
                    "feed carries no per-print tape, no side and no queue "
                    "position, so TRADE_EVIDENCE is NOT_IDENTIFIED and no fill "
                    "can be admitted" if sufficient == "NO"
                    else "execution evidence was joined"),
            "EVIDENCE_THAT_WOULD_IDENTIFY_A_FILL":
                list(EVIDENCE_THAT_WOULD_IDENTIFY_A_FILL),
            "NEXT_STEP": NEXT_STEP_IF_INSUFFICIENT,
            "DO_NOT_INVENT_ANOTHER_FILL_APPROXIMATION":
                DO_NOT_INVENT_ANOTHER_FILL_APPROXIMATION,
        },

        # --- promoted to the top level because they are read first ---
        "HYPOTHETICAL_QUOTES": ladder["HYPOTHETICAL_QUOTES"],
        "TOUCHES": ladder["TOUCHES"],
        "TRADE_EVIDENCE": ladder["TRADE_EVIDENCE"],
        "ADMITTED_COUNTERFACTUAL_FILLS": admitted,
        "PUBLIC_TICK_DATA_SUFFICIENT_FOR_FILL_IDENTIFICATION": sufficient,

        # --- and what is still not reportable ---
        "PROFITABILITY": NOT_IDENTIFIED,
        "WIN_RATE": NOT_IDENTIFIED,
        "EXPECTED_MONTHLY_RETURN": NOT_IDENTIFIED,
        "REALIZED_MAKER_ECONOMICS": "NOT_ESTABLISHED",
        "ORDERS_PLACED": 0,
        "CAPITAL_DEPLOYED": 0,
        "mirror_live": False,
    }

    # THE GATE, AND THE LABEL IT PUTS ON B AND C WHEN IT FAILS. Stamped INTO
    # the sections rather than kept beside them, so a figure copied out of
    # section B carries its own quarantine.
    v = capture_validity(out["A_CAPTURE_QUALITY"], book)
    out["CAPTURE_VALIDITY"] = v
    out["MARKET_CHARACTERIZATION_VALID"] = v["MARKET_CHARACTERIZATION_VALID"]
    if v["CAPTURE_VALIDITY"] != "PASS":
        for section in ("B_BOOK_STRUCTURE", "C_HYPOTHETICAL_QUOTE_PATH"):
            out[section]["STATUS"] = DIAGNOSTIC_ONLY
            out[section]["MARKET_CHARACTERIZATION_VALID"] = "NO"
            out[section]["MAY_NOT_ENTER"] = list(
                QUARANTINED_FIGURES_MAY_NOT_ENTER)
            out[section]["WHY"] = v["WHY"]
    return out


def _jsonable(o):
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

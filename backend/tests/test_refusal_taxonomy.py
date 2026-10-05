"""THE REFUSAL TAXONOMY CLASSIFIES EVERY REFUSAL CODE IN THE CODE BASE (R30A).

The owner: "there must be no generic 'unsupported' bucket if a more precise
reason can be identified". This test parses EVERY module under sportsassets/,
enumerates every `R_*` string constant (module level, class level, nested,
tuple-unpacked, dict-valued) and fails on any code that is neither classified
in `refusal_taxonomy_table.TABLE` nor declared in `NOT_REFUSAL` with a reason.
A new refusal constant therefore cannot ship unclassified.
"""
from __future__ import annotations

import ast
import pathlib
import re

from sportsassets import refusal_taxonomy as RT
from sportsassets import refusal_taxonomy_table as TT

ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
NAME = re.compile(r"^R_[A-Z0-9_]+$")


def _constants() -> dict:
    """{normalized code: [module:NAME, ...]} for every R_* string constant."""
    out: dict = {}
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for tgt in node.targets:
                pairs = []
                if isinstance(tgt, ast.Name) and NAME.match(tgt.id):
                    pairs = [(tgt.id, node.value)]
                elif isinstance(tgt, ast.Tuple) and isinstance(
                        node.value, ast.Tuple):
                    pairs = [(a.id, b) for a, b in zip(tgt.elts,
                                                       node.value.elts)
                             if isinstance(a, ast.Name) and NAME.match(a.id)]
                for name, val in pairs:
                    vals = []
                    if isinstance(val, ast.Constant) and isinstance(
                            val.value, str):
                        vals = [val.value]
                    elif isinstance(val, ast.Dict):
                        vals = [v.value for v in val.values
                                if isinstance(v, ast.Constant)
                                and isinstance(v.value, str)]
                    for v in vals:
                        code = RT.normalize(v)
                        if code:
                            out.setdefault(code, []).append(
                                "%s:%s" % (rel, name))
    return out


def test_every_refusal_constant_in_the_code_base_is_classified():
    consts = _constants()
    assert len(consts) > 1000, "the enumeration found too few constants"
    missing = {c: where for c, where in consts.items()
               if c not in TT.TABLE and c not in TT.NOT_REFUSAL}
    assert not missing, (
        "UNCLASSIFIED REFUSAL CODES -- add each to "
        "sportsassets/refusal_taxonomy_table.py with its class, family and "
        "stage (or to NOT_REFUSAL with why):\n" + "\n".join(
            "  %s  (%s)" % (c, ", ".join(w[:3])) for c, w in
            sorted(missing.items())))


def test_every_row_is_a_valid_class_family_and_stage():
    bad = []
    for code, row in TT.TABLE.items():
        assert isinstance(row, tuple) and len(row) == 3, code
        cls, fam, stage = row
        if cls not in RT.CLASSES or fam not in RT.FAMILIES[cls] \
                or stage not in RT.STAGES:
            bad.append((code, row))
        assert RT.normalize(code) == code, "key is not normalized: %r" % code
    assert not bad, bad
    assert not set(TT.TABLE) & set(TT.NOT_REFUSAL)
    assert all(isinstance(v, str) and len(v) > 10
               for v in TT.NOT_REFUSAL.values())


#: THE CODES PRODUCTION WROTE on paper decisions, orders, the collector ledger
#: and the lane in the 24 h / 7 d to 2026-10-04 20:54Z (research-sql runs
#: 37233864395 and the incident delivery reads), each pinned to its class.
PRODUCTION_CODES = {
    # software: the candidate's economics were never established
    "SETTLEMENT_NOT_SUPPORTED": ("SOFTWARE", "SETTLEMENT"),
    "NO_COMPLETED_GAME_TERMS_FOR_THIS_SPORT": ("SOFTWARE", "SETTLEMENT"),
    "NO_RESEARCH_MODEL_CANDIDATE_EXISTS": ("SOFTWARE", "DATA"),
    "NO_QUALIFIED_PINNACLE_PROBABILITY": ("SOFTWARE", "DATA"),
    "PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE": ("SOFTWARE", "DATA"),
    "PROBABILITY_EVIDENCE_STALE": ("SOFTWARE", "FRESHNESS_PLUMBING"),
    "FEED_QUOTE_OLDER_THAN_LIMIT": ("SOFTWARE", "FRESHNESS_PLUMBING"),
    "FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE": (
        "SOFTWARE", "FRESHNESS_PLUMBING"),
    "FEED_SOCKET_CLOSED": ("SOFTWARE", "DATA"),
    "PINNAPI_PRIMARY_INPUT_CHANGED": ("SOFTWARE", "FRESHNESS_PLUMBING"),
    "PINNAPI_PRIMARY_CLOCK_INVALID": ("SOFTWARE", "FRESHNESS_PLUMBING"),
    "PINNAPI_PRIMARY_NO_EXACT_FIXTURE": ("SOFTWARE", "MAPPING"),
    "PINNAPI_PRIMARY_SPORT_UNSUPPORTED": ("SOFTWARE", "CAPABILITY"),
    "BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE": (
        "SOFTWARE", "FRESHNESS_PLUMBING"),
    "OUTCOME_DEPTH_BELOW_FLOOR": ("SOFTWARE", "DATA"),
    "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED": ("SOFTWARE", "FRESHNESS_PLUMBING"),
    "CALIBRATION_ONLY_RECORD_IS_NOT_AN_ENTRY_CANDIDATE": (
        "SOFTWARE", "FRESHNESS_PLUMBING"),
    "VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE": (
        "SOFTWARE", "SETTLEMENT"),
    "NO_PINNACLE_ON_EVENT": ("SOFTWARE", "DATA"),
    "QUOTE_STALE_ON_ARRIVAL": ("SOFTWARE", "FRESHNESS_PLUMBING"),
    "VENUE_BOOK_READ_FAILED": ("SOFTWARE", "DATA"),
    "NO_VENUE_CONTRACT_FOR_EVENT": ("SOFTWARE", "MAPPING"),
    "THE_OBSERVED_BOOK_WAS_UNREADABLE": ("SOFTWARE", "DATA"),
    "NO_READABLE_BOOK_OBSERVED_BEFORE_THE_ORDER_EXPIRED": ("SOFTWARE", "DATA"),
    "VENUE_COOLDOWN_EXCEEDS_THE_DECISION_DEADLINE": (
        "SOFTWARE", "FRESHNESS_PLUMBING"),
    "PAPER_BOOK_READ_DEADLINE_EXCEEDED": ("SOFTWARE", "FRESHNESS_PLUMBING"),
    "HOLD_ON_STALE_PROBABILITY": ("SOFTWARE", "FRESHNESS_PLUMBING"),
    "WAITING_FOR_FRESH_EVIDENCE": ("SOFTWARE", "FRESHNESS_PLUMBING"),
    # economic: judged on edge / EV / depth, or a deliberate rail
    "BELOW_MIN_GROSS_EDGE": ("ECONOMIC", "EDGE"),
    "GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT": ("ECONOMIC", "EDGE"),
    "NET_EV_NOT_POSITIVE_AFTER_FEES": ("ECONOMIC", "EV"),
    "BELOW_MIN_NET_EV": ("ECONOMIC", "EV"),
    "NO_DISPLAYED_LIQUIDITY_WITHIN_THE_LIMIT": ("ECONOMIC", "DEPTH"),
    "THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT": ("ECONOMIC", "RISK_RAIL"),
    "STRATEGY_ENTRIES_DISABLED": ("ECONOMIC", "RISK_RAIL"),
    "STRATEGY_NOT_LIVE_ELIGIBLE": ("ECONOMIC", "RISK_RAIL"),
    "SKIP_EXECUTION": ("ECONOMIC", "EV"),
}


def test_the_codes_production_writes_are_classified_as_pinned():
    for code, (cls, fam) in PRODUCTION_CODES.items():
        got = RT.classify(code)
        assert got["classified"], code
        assert (got["class"], got["family"]) == (cls, fam), (code, got)


def test_decision_class_never_guesses():
    assert RT.decision_class("ENTER", []) == RT.ENTER
    assert RT.decision_class("REFUSE", ["BELOW_MIN_GROSS_EDGE"]) == \
        RT.REJECTED_ECONOMIC
    # any software code means the economics were not established
    assert RT.decision_class("REFUSE", [
        "BELOW_MIN_GROSS_EDGE", "PROBABILITY_EVIDENCE_STALE"]) == \
        RT.REJECTED_SOFTWARE
    # an unknown code with no software code is UNCLASSIFIED, never economic
    assert RT.decision_class("REFUSE", [
        "BELOW_MIN_GROSS_EDGE", "A_CODE_NOBODY_CLASSIFIED"]) == \
        RT.REJECTED_UNCLASSIFIED
    assert RT.decision_class("REFUSE", []) == RT.REJECTED_UNCLASSIFIED
    assert RT.decision_class("REFUSE", None) == RT.REJECTED_UNCLASSIFIED
    # suffixes are normalized away
    assert RT.classify("MODEL_RUN_RAISED:ValueError")["class"] == "SOFTWARE"
    b = RT.binding(["BELOW_MIN_GROSS_EDGE", "SETTLEMENT_NOT_SUPPORTED"])
    assert b["code"] == "SETTLEMENT_NOT_SUPPORTED"
    s = RT.summarize({"BELOW_MIN_GROSS_EDGE": 3, "NOPE_NOT_A_CODE": 2,
                      "PROBABILITY_EVIDENCE_STALE": 1})
    assert s["by_class"] == {"ECONOMIC": 3, "UNCLASSIFIED": 2,
                             "SOFTWARE": 1}
    assert s["unclassified"] == {"NOPE_NOT_A_CODE": 2}
    nr = RT.classify("bbo_attempt")
    assert not nr["classified"] and "NOT_A_REFUSAL" in nr["why"]


def test_the_taxonomy_is_pure():
    """It imports nothing from the paper, execution or funded paths (and no
    I/O module), so every reader -- the command centre, the lost-opportunity
    layer, research tooling -- can import it."""
    for name in ("refusal_taxonomy.py", "refusal_taxonomy_table.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                mods.add("." * n.level + (n.module or ""))
                mods |= {a.name for a in n.names}
        assert mods <= {"__future__", "annotations", "re", ".",
                        "refusal_taxonomy_table"}, (name, mods)

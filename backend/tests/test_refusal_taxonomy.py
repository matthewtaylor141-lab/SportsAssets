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
               if c not in TT.TABLE and c not in TT.NOT_REFUSAL
               and c not in TT.WRAPPERS}
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
    assert not set(TT.WRAPPERS) & (set(TT.TABLE) | set(TT.NOT_REFUSAL))
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


#: REVIEW OF 7bd084b: these were ECONOMIC / RISK_RAIL. Each says the software
#: does not have, or could not read, something it needs -- DATA, which the
#: taxonomy's own definition puts under SOFTWARE.
DATA_GAPS_ARE_NOT_RISK_RAILS = (
    "ACCOUNT_ID_NOT_SUPPLIED", "ACCOUNT_WIDE_EXPOSURE_COULD_NOT_BE_MEASURED",
    "ACCOUNT_WIDE_EXPOSURE_WAS_NOT_SUPPLIED",
    "ACTION_EXPOSURE_EFFECT_NOT_IDENTIFIED", "R_ACCOUNT_EXPOSURE_UNREADABLE",
    "THE_APPROVED_LIMIT_SET_WAS_NOT_SUPPLIED_TO_COMPARE",
    "LIVE_POLICY_ROW_MISSING_OR_UNREADABLE")


def test_a_data_gap_is_software_never_a_risk_rail():
    for code in DATA_GAPS_ARE_NOT_RISK_RAILS:
        got = RT.classify(code)
        assert (got["class"], got["family"]) == ("SOFTWARE", "DATA"), got
        assert RT.decision_class("REFUSE", [code]) == RT.REJECTED_SOFTWARE
    # the rails that ARE deliberate stay economic
    for code in ("ABOVE_THE_PER_ORDER_CAP", "ABOVE_THE_MAXIMUM_CONCURRENT_"
                 "GROUPS", "THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT"):
        assert RT.classify(code)["class"] == "ECONOMIC", code


def test_the_risk_wrapper_is_classified_by_the_code_it_wraps():
    w = "PAPER_RISK_REFUSED_THE_ORDER"
    alone = RT.classify(w)
    assert not alone["classified"] and alone["class"] == RT.UNCLASSIFIED
    assert "WRAPPER" in alone["why"]
    # never economic on its own
    assert RT.decision_class("REFUSE", [w]) == RT.REJECTED_UNCLASSIFIED
    cap = RT.classify_wrapped(w, "ABOVE_THE_PER_FIXTURE_CONCENTRATION_CAP")
    assert cap["class"] == "ECONOMIC" and cap["wrapped_by"] == w
    for inner in ("THE_ORDER_IS_MALFORMED", "NOT_A_PAPER_IDENTIFIER",
                  "THE_PAPER_ACCOUNT_DOES_NOT_EXIST"):
        k = RT.classify_wrapped(w, inner)
        assert k["class"] == "SOFTWARE" and k["wrapped_by"] == w, inner
    none = RT.classify_wrapped(w, None)
    assert none["class"] == RT.UNCLASSIFIED and none["wrapped_by"] == w
    assert RT.classify_wrapped(w, "A_CODE_NOBODY_CLASSIFIED")["class"] == \
        RT.UNCLASSIFIED
    # a code that wraps nothing is classified as itself
    assert RT.classify_wrapped("BELOW_MIN_GROSS_EDGE", "X")["class"] == \
        "ECONOMIC"


# ═════════════════════════════════════════════════════════════════════
# CODES NOT NAMED R_* (incident release, verifier finding 3)
# ═════════════════════════════════════════════════════════════════════
#
# The enumeration above reads R_*-named constants only. The NCAAF stream's
# five strict-policy codes are named S_* (bettor_ncaaf_settlement.
# STRICT_CODES) and ride behind SETTLEMENT_NOT_SUPPORTED on EVERY NCAAF
# strict decision (derek_policy, paper_derek, paper_benchmark), so the agent
# funnel's per-code `by_class` counted them UNCLASSIFIED: RT.summarize over
# SETTLEMENT_NOT_SUPPORTED + the five gave {'SOFTWARE': 2... 'UNCLASSIFIED':
# 5}. The fallback literals the PinnAPI re-read and the line lane emit were
# unclassified too.

def _strict_codes() -> dict:
    """{code: module:NAME} for every code a module lists in a module-level
    `STRICT_CODES` tuple (the strict settlement policy's precise refusals,
    whatever their constants are named), resolved through that module's own
    string constants."""
    out: dict = {}
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        consts = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) \
                    and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                consts[node.targets[0].id] = node.value.value
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) \
                    and node.targets[0].id == "STRICT_CODES" \
                    and isinstance(node.value, ast.Tuple):
                for e in node.value.elts:
                    if isinstance(e, ast.Name) and e.id in consts:
                        out[consts[e.id]] = "%s:%s" % (path.name, e.id)
                    elif isinstance(e, ast.Constant):
                        out[e.value] = "%s:<literal>" % path.name
    return out


def test_the_ncaaf_strict_codes_are_classified_settlement():
    from sportsassets import bettor_ncaaf_settlement as NC
    found = _strict_codes()
    assert set(NC.STRICT_CODES) <= set(found), found
    for code, where in found.items():
        got = RT.classify(code)
        assert got["classified"], (code, where)
        assert (got["class"], got["family"], got["stage"]) == (
            RT.SOFTWARE, "SETTLEMENT", "SETTLEMENT_COMPATIBILITY"), (code, got)
    # the agent funnel's per-code view of an NCAAF strict refusal
    s = RT.summarize({c: 1 for c in ["SETTLEMENT_NOT_SUPPORTED"]
                      + list(NC.STRICT_CODES)})
    assert s["by_class"] == {"SOFTWARE": 6}, s
    assert not s["unclassified"]


#: the literal codes the merged streams emit without a constant: the PinnAPI
#: re-read's fallbacks (ext_pinnacle_loop) and the line lane's report states
#: (ext_pinnacle_loop line lane, bettor_market_family.census)
EMITTED_LITERALS = {
    "PINNAPI_READ_RAISED:ValueError": ("SOFTWARE", "INTEGRITY"),
    "PINNAPI_READ_REFUSED_WITHOUT_A_REASON": ("SOFTWARE", "DATA"),
    "LINE_INSTRUMENT_RAISED:KeyError": ("SOFTWARE", "INTEGRITY"),
    "PINNACLE_LINE_NOT_READ": ("SOFTWARE", "DATA"),
    "LINE_CENSUS_RAISED:TypeError": ("SOFTWARE", "INTEGRITY"),
    "LINE_PERSIST:UniqueViolationError": ("SOFTWARE", "INTEGRITY"),
}


def test_the_streams_literal_codes_are_classified():
    for code, (cls, fam) in EMITTED_LITERALS.items():
        got = RT.classify(code)
        assert got["classified"], code
        assert (got["class"], got["family"]) == (cls, fam), (code, got)
    # each is still emitted where the verifier found it (a renamed literal
    # must be reclassified, not silently orphaned)
    src = {p.name: p.read_text(encoding="utf-8") for p in (
        ROOT / "workers" / "ext_pinnacle_loop.py",
        ROOT / "bettor_market_family.py")}
    for code in EMITTED_LITERALS:
        lit = RT.normalize(code)
        assert any('"%s' % lit in t for t in src.values()), lit

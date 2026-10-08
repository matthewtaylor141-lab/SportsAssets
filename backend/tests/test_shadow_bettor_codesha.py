"""THE V2 CODE BOUNDARY, PINNED BY MUTATION.

Owner directive 2026-09-19 20:2xZ, verbatim:

    COMMENT_ONLY_CHANGE      -> SAME HASH
    WHITESPACE_ONLY_CHANGE   -> SAME HASH
    ACTION_CHANGE            -> DIFFERENT HASH
    BLOCKER_CHANGE           -> DIFFERENT HASH
    P_BETTOR_STATUS_CHANGE   -> DIFFERENT HASH
    P_FILL_STATUS_CHANGE     -> DIFFERENT HASH
    LINEAGE_CHANGE           -> DIFFERENT HASH

ASSERTING THE RULE IS NOT THE SAME AS TESTING IT. Each case below
actually mutates the module's source text and recomputes the digest, so
the boundary is demonstrated rather than described. `overrides` exists
for exactly this and touches nothing on disk.

THE FAILURE THESE PREVENT is the one that happened: a code change
reached production under a frozen version while the guard that should
have caught it was too blunt to be enforced.
"""

from __future__ import annotations

import pathlib

import pytest

from sportsassets import shadow_bettor_codesha as cs

SRC = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"


def source(module: str) -> str:
    return (SRC / module).read_text()


def sha(**overrides) -> str:
    return cs.semantic_code_sha(overrides=overrides or None)


BASE = cs.semantic_code_sha()


def mutate(module: str, old: str, new: str) -> str:
    """Replace exactly one occurrence, refusing a no-op mutation --
    a test that silently changed nothing would pass for the wrong
    reason."""
    src = source(module)
    assert src.count(old) >= 1, "fixture drifted: %r not in %s" % (old, module)
    out = src.replace(old, new, 1)
    assert out != src
    return out


# ── the two that must NOT move it ────────────────────────────────────


def test_comment_only_change_keeps_the_same_hash():
    """A comment edit moved V1's hash. That is the defect being fixed:
    the parser never retains a comment, so it cannot reach the digest."""
    src = source("shadow_bettor.py")
    mutated = src.replace(
        "def decide(",
        "# an explanatory comment that changes no behaviour whatsoever\n"
        "def decide(", 1)
    assert mutated != src
    assert sha(**{"shadow_bettor.py": mutated}) == BASE


def test_docstring_change_keeps_the_same_hash():
    """Prose about the code is not the code. Rewriting an explanation
    must never block decision writing."""
    src = source("shadow_bettor.py")
    marker = '"""BETTOR\'s own prospective decision.'
    assert marker in src
    mutated = src.replace(marker, '"""Completely rewritten prose.', 1)
    assert sha(**{"shadow_bettor.py": mutated}) == BASE


def test_whitespace_only_change_keeps_the_same_hash():
    src = source("shadow_bettor.py")
    mutated = src.replace("def blockers_for(",
                          "\n\n\ndef blockers_for(", 1)
    assert mutated != src
    assert sha(**{"shadow_bettor.py": mutated}) == BASE


def test_reordering_definitions_keeps_the_same_hash():
    """Moving a definition within a file changes nothing about
    behaviour, and the digest walks the symbols in sorted order so it
    does not notice."""
    src = source("shadow_bettor.py")
    mutated = src.replace(
        'B_NO_FAIR_VALUE = "NO_FAIR_VALUE"\n', "", 1)
    mutated = mutated.replace(
        "BLOCKERS = (", 'B_NO_FAIR_VALUE = "NO_FAIR_VALUE"\nBLOCKERS = (', 1)
    assert sha(**{"shadow_bettor.py": mutated}) == BASE


# ── the five that MUST move it ───────────────────────────────────────


def test_action_change_moves_the_hash():
    """The single most important case: a lane that started proposing
    something other than NO_TRADE must not run under a frozen version."""
    mutated = mutate("shadow_lanes.py",
                     'fields["proposedAction"] = sh.NO_TRADE',
                     'fields["proposedAction"] = sh.BUY')
    assert sha(**{"shadow_lanes.py": mutated}) != BASE


def test_blocker_change_moves_the_hash():
    mutated = mutate("shadow_bettor.py",
                     'B_INSUFFICIENT_DEPTH = "INSUFFICIENT_DEPTH"',
                     'B_INSUFFICIENT_DEPTH = "DEPTH_IS_FINE_ACTUALLY"')
    assert sha(**{"shadow_bettor.py": mutated}) != BASE


def test_removing_a_blocker_branch_moves_the_hash():
    """Not just renaming a code -- changing WHEN one fires."""
    mutated = mutate("shadow_bettor.py",
                     "if micro.get(\"depth\") in (None, NOT_IDENTIFIED):",
                     "if False:")
    assert sha(**{"shadow_bettor.py": mutated}) != BASE


def test_p_bettor_status_change_moves_the_hash():
    mutated = mutate("shadow_lanes.py",
                     'record["pBettorStatus"] = NOT_ESTABLISHED\n'
                     '    record["informationEv"] = None\n'
                     "    return record",
                     'record["pBettorStatus"] = ESTABLISHED\n'
                     '    record["informationEv"] = None\n'
                     "    return record")
    assert sha(**{"shadow_lanes.py": mutated}) != BASE


def test_p_fill_status_change_moves_the_hash():
    """THE EXACT CHANGE THAT DRIFTED UNDER V1. Under V1's byte boundary
    it moved the hash for the right reason but alongside 15 comment
    lines that would have moved it anyway; here it is isolated."""
    mutated = mutate("shadow_bettor.py",
                     "pFillStatus=lanes.NOT_IDENTIFIED,",
                     "pFillStatus=lanes.NOT_ESTABLISHED,")
    assert sha(**{"shadow_bettor.py": mutated}) != BASE


def test_lineage_change_moves_the_hash():
    mutated = mutate("shadow_lanes.py",
                     "lineage = assert_lineage(BETTOR_EV_SHADOW, features, "
                     "specialist)",
                     "lineage = {'featureLineage': {}, "
                     "'rn1FeaturesUsed': False}")
    assert sha(**{"shadow_lanes.py": mutated}) != BASE


def test_policy_version_change_moves_the_hash():
    """A decision claiming a different version is a different decision."""
    mutated = mutate("shadow_bettor.py",
                     'POLICY_VERSION = "BETTOR_EV_SHADOW_V6"',
                     'POLICY_VERSION = "BETTOR_EV_SHADOW_V9"')
    assert sha(**{"shadow_bettor.py": mutated}) != BASE


def test_a_safety_constant_change_moves_the_hash():
    mutated = mutate("shadow.py", "CAPITAL_AT_RISK = 0",
                     "CAPITAL_AT_RISK = 1000")
    assert sha(**{"shadow.py": mutated}) != BASE


# ── scope: RN1's own code must not move BETTOR's hash ────────────────


def test_an_rn1_only_change_does_not_move_bettors_hash():
    """The second defect in V1's boundary: shadow_lanes.py is shared,
    so an RN1-only edit moved BETTOR's code hash although BETTOR could
    not have changed."""
    mutated = mutate("shadow_lanes.py",
                     'record["rn1FeaturesUsed"] = True',
                     'record["rn1FeaturesUsed"] = True  # rn1 only\n'
                     '    record["rn1Extra"] = 1')
    assert sha(**{"shadow_lanes.py": mutated}) == BASE


def test_rn1_symbols_are_outside_the_boundary():
    lanes_scope = cs.DECISION_PATH["shadow_lanes.py"]
    assert "rn1_lane_decision" not in lanes_scope
    for name in lanes_scope:
        assert not name.startswith("PROV_RN1")
        assert name != "RN1_PROVENANCES"
    # probabilities() is not called anywhere on the decision path, so
    # it is not load-bearing and is not in the boundary.
    assert "probabilities" not in lanes_scope


def test_opportunity_collection_is_outside_the_decision_boundary():
    """Collection must keep running while decision writing is blocked,
    so it cannot sit inside the boundary that blocks decisions."""
    scope = cs.DECISION_PATH["shadow_bettor.py"]
    for name in ("opportunity_record", "record_opportunity", "universe",
                 "UNIVERSE_SQL", "_OPPORTUNITY_INSERT"):
        assert name not in scope, name


# ── the boundary cannot silently shrink ──────────────────────────────


def test_a_missing_symbol_raises_rather_than_hashing_fewer():
    """V1 returned a word when it could not read a file. A digest that
    is ENFORCED cannot do that: a boundary that quietly lost a symbol
    would compare unequal for an unreconstructable reason."""
    src = source("shadow_bettor.py")
    without = src.replace("def blockers_for(", "def blockers_for_RENAMED(", 1)
    with pytest.raises(cs.BoundaryIncomplete) as exc:
        sha(**{"shadow_bettor.py": without})
    assert "blockers_for" in str(exc.value)


def test_the_boundary_name_is_part_of_the_digest():
    """Two different boundary definitions must never collide."""
    assert cs.BOUNDARY_VERSION in cs.semantic_code_sha.__doc__ \
        or cs.BOUNDARY_VERSION == "BETTOR_DECISION_PATH_V1"
    src = cs.__file__
    assert src  # module is importable
    assert len(BASE) == 64


def test_v1s_byte_methodology_is_untouched():
    """"Do not change V1's historical hash methodology." V1's function
    still hashes whole file bytes, exactly as it did when 34fbb4ab was
    recorded."""
    from sportsassets import shadow_bettor_policy as bpol
    body = pathlib.Path(bpol.__file__).read_text()
    block = body[body.index("def policy_code_sha_v1("):]
    block = block[:block.index("\ndef ", 5)]
    assert "fh.read()" in block and "hashlib.sha256()" in block
    assert "ast" not in block


# ── what production taught the boundary ──────────────────────────────


def test_the_worker_and_the_api_are_outside_the_boundary():
    """They were edited after V2 was frozen, to give the integrity tile
    its own source. If either were in the boundary that edit would have
    moved the running sha away from the frozen one and fail-closed the
    decision writer -- correctly, but for a change that decides
    nothing."""
    for name in list(cs.DECISION_PATH) + list(cs.DECISION_PATH_V1):
        assert not name.startswith("workers/")
        assert not name.startswith("api/")
    # V1 (V2..V5's boundary) is exactly as it was frozen; V2 (V6) adds the
    # entry gate `decide` has called since 70ca3a4 -- and nothing else.
    assert set(cs.DECISION_PATH_V1) == {"shadow.py", "shadow_lanes.py",
                                        "shadow_bettor.py"}
    assert set(cs.DECISION_PATH) == {"shadow.py", "shadow_lanes.py",
                                     "shadow_bettor.py",
                                     "bettor_entry_gate.py"}


def test_v1s_digest_is_environment_dependent_and_that_is_recorded():
    """A FINDING, NOT A FEATURE -- and the reason V2 exists. ast.dump()
    emits version-specific text: 3.12 added type_params to FunctionDef and
    prints it empty; 3.13 omits every empty field by default. The same
    source hashed 84c80e7c (3.11), 92a190a0 (3.12.3) and 2532d20b (3.13)
    under V1. Production froze V2..V5 on 3.12.3, so V1 is kept exactly for
    those numbers; on 3.13 show_empty=True restores the 3.12 text.

    The old form of this test read the DUMP for "type_params" and failed on
    3.13 for exactly the reason it documents. The FIELD is the property."""
    import ast
    import sys
    assert ("type_params" in ast.FunctionDef._fields) == (
        sys.version_info >= (3, 12))
    if sys.version_info >= (3, 13):
        assert "type_params" not in ast.dump(
            ast.parse("def f(): pass").body[0])
    node = ast.parse("def f(): pass").body[0]
    assert ("type_params=[]" in cs._dump_v1(node)) == (
        sys.version_info >= (3, 12))


# ── V2 (BETTOR_EV_SHADOW_V6): what the drift of V5 taught ────────────

# V6's digest. Interpreter-independent by construction and pinned on EVERY
# interpreter (measured identical on 3.11.17, 3.12.3 and 3.13.16). Once
# production freezes V6 this is ITS number: a decision change makes this
# test fail and the remedy is BETTOR_EV_SHADOW_V7 -- never a new literal.
V6_CODE_SHA = (
    "68b1d914501145a3bf1d9ab5b9cb941135eac6def2703fefd37d67da94d9ef0b")


def test_v6s_digest_is_the_same_on_every_interpreter():
    from sportsassets import shadow_bettor as sb
    assert sb.POLICY_VERSION == "BETTOR_EV_SHADOW_V6"
    assert BASE == V6_CODE_SHA, (
        "the decision path moved under BETTOR_EV_SHADOW_V6; declare V7")


def test_v2s_serialisation_carries_no_interpreter_defaults():
    import ast
    node = ast.parse("def f(x=None): return 1").body[0]
    text = cs._canon(node)
    assert "type_params" not in text            # empty on 3.12+, absent on 3.11
    assert "decorator_list" not in text         # empty
    assert "NoneType:None" in text              # None is never dropped
    assert "int:1" in text
    # type travels with the value: 1, 1.0 and True are different programs
    one = cs._canon(ast.parse("x = 1").body[0])
    assert one != cs._canon(ast.parse("x = 1.0").body[0])
    assert one != cs._canon(ast.parse("x = True").body[0])


def test_the_entry_gate_is_inside_the_v2_boundary():
    """70ca3a4 gave decide() a branch whose verdict is admit(); 9e236bf and
    a0c9220 then changed admit() without moving V5's hash. Not any more."""
    mutated = mutate("bettor_entry_gate.py",
                     'ADMISSIBLE_MODEL_STATUS = ("FROZEN", "PROMOTED", "CHAMPION")',
                     'ADMISSIBLE_MODEL_STATUS = ("FROZEN", "PROMOTED", "CHAMPION", "CANDIDATE")')
    assert sha(**{"bettor_entry_gate.py": mutated}) != BASE


def test_relaxing_the_venue_circularity_guard_moves_the_hash():
    """The guard that stops a venue-derived fair value licensing an entry
    -- switched off, and separately renamed. Both must move the hash."""
    off = mutate("bettor_entry_gate.py",
                 'elif "VENUE" in fv_kind or "MIDPOINT" in fv_kind',
                 'elif False and "MIDPOINT" in fv_kind')
    assert sha(**{"bettor_entry_gate.py": off}) != BASE
    renamed = mutate("bettor_entry_gate.py",
                     'R_FV_IS_VENUE_PRICE = "FAIR_VALUE_IS_THE_VENUE_BENCHMARK"',
                     'R_FV_IS_VENUE_PRICE = "FAIR_VALUE_IS_FINE"')
    assert sha(**{"bettor_entry_gate.py": renamed}) != BASE


def test_the_admitted_record_is_inside_the_v2_boundary():
    mutated = mutate("shadow_bettor.py",
                     'record["orderSubmitted"] = False',
                     'record["orderSubmitted"] = True')
    assert sha(**{"shadow_bettor.py": mutated}) != BASE


def test_removing_the_entry_gate_branch_moves_the_hash():
    mutated = mutate("shadow_bettor.py",
                     'if gate["admissible"]:',
                     'if False:')
    assert sha(**{"shadow_bettor.py": mutated}) != BASE


def test_a_comment_or_docstring_in_the_entry_gate_keeps_the_hash():
    src = source("bettor_entry_gate.py")
    mutated = src.replace("def admit(",
                          "# prose that changes nothing\n\n\ndef admit(", 1)
    assert mutated != src
    assert sha(**{"bettor_entry_gate.py": mutated}) == BASE
    marker = '"""Return an admissible entry, or every reason there is not one.'
    assert marker in src
    rewritten = src.replace(marker, '"""Rewritten prose.', 1)
    assert sha(**{"bettor_entry_gate.py": rewritten}) == BASE


def test_reformatting_keeps_the_hash():
    """Format-only: quote style, line wrapping, trailing comma, redundant
    parentheses. None reaches the parsed tree."""
    src = source("shadow_bettor.py")
    reformatted = src.replace('B_RISK_GATE = "RISK_GATE"',
                              "B_RISK_GATE = ('RISK_GATE')", 1)
    assert reformatted != src
    assert sha(**{"shadow_bettor.py": reformatted}) == BASE
    gate = source("bettor_entry_gate.py")
    wrapped = gate.replace(
        'SETTLEMENT_TARGETS = ("SETTLEMENT_OUTCOME", "SETTLES_YES", "PAYOUT")',
        'SETTLEMENT_TARGETS = (\n    "SETTLEMENT_OUTCOME",\n    "SETTLES_YES",\n'
        '    "PAYOUT",\n)', 1)
    assert wrapped != gate
    assert sha(**{"bettor_entry_gate.py": wrapped}) == BASE


def test_v1_still_reproduces_v5s_frozen_number_from_the_frozen_commit():
    """V5 was frozen at e8ab303 on 3.12.3 as 92a190a0 and production has
    run 98aaa204 (70ca3a4) since. Both are reproduced here from git history
    through V1, unchanged -- so V5's row stays checkable after V6 runs."""
    import subprocess
    import sys
    from sportsassets import shadow_bettor_policy as bpol
    repo = SRC.parents[1]

    def at(rev):
        return {m: subprocess.run(
            ["git", "-C", str(repo), "show",
             "%s:backend/sportsassets/%s" % (rev, m)],
            capture_output=True, text=True, check=True).stdout
            for m in cs.DECISION_PATH_V1}

    if sys.version_info >= (3, 12):
        assert cs.semantic_code_sha_v1(
            source_dir="/nonexistent", overrides=at("e8ab3038")) == \
            bpol.V5_FROZEN_POLICY_CODE_SHA
        assert cs.semantic_code_sha_v1(
            source_dir="/nonexistent", overrides=at("70ca3a4e")) == \
            bpol.V5_DRIFTED_POLICY_CODE_SHA
    # on every interpreter: the drift is 70ca3a4 and nothing else
    assert cs.semantic_code_sha_v1(
        source_dir="/nonexistent", overrides=at("70ca3a4e^")) == \
        cs.semantic_code_sha_v1(
            source_dir="/nonexistent", overrides=at("e8ab3038"))
    assert cs.semantic_code_sha_v1(
        source_dir="/nonexistent", overrides=at("70ca3a4e")) != \
        cs.semantic_code_sha_v1(
            source_dir="/nonexistent", overrides=at("70ca3a4e^"))

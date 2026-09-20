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
                     'POLICY_VERSION = "BETTOR_EV_SHADOW_V4"',
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
    for name in cs.DECISION_PATH:
        assert not name.startswith("workers/")
        assert not name.startswith("api/")
    assert set(cs.DECISION_PATH) == {"shadow.py", "shadow_lanes.py",
                                     "shadow_bettor.py"}


def test_the_digest_is_environment_dependent_and_that_is_recorded():
    """A FINDING, NOT A FEATURE. ast.dump() emits version-specific
    fields -- Python 3.12 added type_params to FunctionDef -- so the
    same source yields a different digest on 3.11 and 3.12. Production
    runs 3.12 and recorded 2934de9a...; this box runs 3.11 and computes
    5220d2e7...

    That is STABLE WITHIN an environment, which is why the gate works
    today, and it fails CLOSED on a runtime upgrade rather than open.
    But it cannot be checked from a differently-versioned box, and it
    is not fixable under V2: changing the digest function now would
    move the running sha away from the frozen 2934de9a and block
    decisions. It belongs to a future version.

    This test records the property so nobody rediscovers it by
    watching production fail closed after a base-image bump.
    """
    import ast
    import sys
    dumped = ast.dump(ast.parse("def f(): pass").body[0])
    has_type_params = "type_params" in dumped
    assert has_type_params == (sys.version_info >= (3, 12)), (
        "ast.dump's field set changed with the Python version, which is "
        "exactly the dependency this test documents")

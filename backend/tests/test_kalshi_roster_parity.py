"""The FOURTH literal encoding one decision, and it was inverted.

live_executor's comment calls COPY_CUT_WHALES "the THIRD gate on the
same decision" and names the pinning tests that hold the three backend
literals together. There is a fourth, in another package: PER_COPY_USD
in the Kalshi copy sleeve. Nothing pinned it, and it drifted into the
exact inverse of the roster.

The 2026-08-25 reset was made on the first whale P&L this desk has
produced that can see a MERGE — these accounts close by merging
complementary outcomes back to USDC, which is not a trade and appears in
no trade feed, so every prior roster decision was blind to the exits
that decide their P&L. rn1 and ferrari came back as the two best books;
swisstony as the second worst. The owner then cut homerunhazard on
2026-08-26.

The Kalshi map still read rn1 0.00, ferrari 0.00, swisstony 300.00 and
homerunhazard 300.00, with a ("homerunhazard","baseball") cell at
600.00 and a spread multiplier of 1.5 applied on top with no ceiling —
$900 an order, against the $250 the PMUS leg enforces "after every
override and multiplier".

The leg is dark by env default (EDGE_KCOPY_PM_ONLY="1"), and its own
comment advertises the single flip as the way to re-arm. One environment
variable would have resumed copying both cut whales, above the
authorized cap, while refusing both whales we actually trade — with no
code change and no test failure.

This file is the pin. It reads the edge-engine file as TEXT: the two
packages do not import each other, and a test that could not run in a
backend-only checkout would be a pin that is not there when it matters.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from sportsassets import live_executor as le


def _kalshi_src() -> str:
    here = pathlib.Path(__file__).resolve()
    root = next(
        (p for p in here.parents
         if (p / "backend" / "sportsassets" / "live_executor.py").exists()
         and (p / "edge-engine" / "src" / "edge" / "shadow"
              / "kalshi_copies.py").exists()),
        None)
    if root is None:
        pytest.skip("sibling tree not present in this checkout")
    return (root / "edge-engine" / "src" / "edge" / "shadow"
            / "kalshi_copies.py").read_text()


def _assign(src: str, name: str):
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return node.value
    raise AssertionError(f"{name} is not a module-level assignment")


class _Resolve(ast.NodeTransformer):
    """Turn the two non-literal shapes these maps use into constants.

    `_W2C33` is written as a NAME in both files, and the ceiling is
    `float(os.environ.get(KEY, DEFAULT))`. Reading the module by import
    is not available — the two packages do not import each other, and a
    pin that cannot run in a backend-only checkout is not there when it
    matters — so the AST is resolved instead.

    Deliberately ast and not a regex. A regex that stops at the first
    closing brace returns a TRUNCATED map and then reports agreement on
    the half it managed to read; this repository has shipped exactly
    that mistake, more than once.
    """

    def __init__(self, names: dict):
        self.names = names

    def visit_Name(self, node):  # noqa: N802 — ast API
        if node.id in self.names:
            return ast.copy_location(
                ast.Constant(value=self.names[node.id]), node)
        return node

    def visit_Call(self, node):  # noqa: N802 — ast API
        self.generic_visit(node)
        f = node.func
        # os.environ.get(KEY, DEFAULT) -> DEFAULT (the code's own value
        # when the variable is unset, which is what ships).
        if isinstance(f, ast.Attribute) and f.attr == "get" \
                and len(node.args) == 2:
            return node.args[1]
        # float(x) / int(x) around it, and frozenset({...}).
        if isinstance(f, ast.Name) and f.id in ("float", "int", "frozenset",
                                                "set"):
            inner = ast.literal_eval(node.args[0]) if node.args else None
            val = {"float": float, "int": int,
                   "frozenset": frozenset, "set": set}[f.id](inner)
            # literal_eval cannot rebuild a set from a Constant, so the
            # resolved value is carried out through a sentinel the
            # caller unwraps.
            return ast.copy_location(_Const(val), node)
        return node


class _Const(ast.AST):
    """A resolved value that ast.literal_eval cannot represent (a set).

    _fields is empty so NodeTransformer walks past it untouched.
    """

    _fields = ()

    def __init__(self, value):
        self.value = value


def _literal(src: str, name: str):
    """The value of a module-level assignment, parsed not imported."""
    names = {"_W2C33": ast.literal_eval(_assign(src, "_W2C33"))}
    node = _Resolve(names).visit(_assign(src, name))
    if isinstance(node, _Const):
        return node.value
    ast.fix_missing_locations(node)
    return ast.literal_eval(node)


class TestTheCutRosterIsTheSameOnBothVenues:
    def test_the_cut_set_matches_live_executor(self):
        src = _kalshi_src()
        assert _literal(src, "CUT_WHALES") == set(le.COPY_CUT_WHALES)

    def test_every_cut_whale_has_a_zero_clip(self):
        src = _kalshi_src()
        clips = _literal(src, "PER_COPY_USD")
        for w in le.COPY_CUT_WHALES:
            assert clips.get(w, 0.0) == 0.0, \
                f"{w} is cut on PMUS and sized at {clips.get(w)} on Kalshi"

    def test_every_cut_whale_has_no_live_sport_override(self):
        """A sport override for a blocked whale is a dead cell, and a
        dead cell carrying a number is one edit from being live."""
        src = _kalshi_src()
        for (w, _sport), v in _literal(src, "PER_COPY_USD_SPORT").items():
            if w in le.COPY_CUT_WHALES:
                assert v == 0.0, f"({w}) override still sized at {v}"

    def test_every_verified_whale_is_not_blocked(self):
        src = _kalshi_src()
        clips = _literal(src, "PER_COPY_USD")
        for w in le._whale_set("LIVE_VERIFIED_WHALES"):
            assert clips.get(w, 0.0) > 0.0, \
                f"{w} is copied on PMUS and blocked on Kalshi"

    def test_the_two_clip_maps_agree_whale_for_whale(self):
        """Not just the signs. A whale sized differently on two venues
        is a roster decision nobody made."""
        src = _kalshi_src()
        k = _literal(src, "PER_COPY_USD")
        for w, usd in le.PER_FILL_BY_WHALE.items():
            assert w in k, f"{w} is missing from the Kalshi map entirely"
            assert k[w] == pytest.approx(usd), \
                f"{w}: PMUS {usd}, Kalshi {k[w]}"

    def test_the_inversion_is_stated_so_it_cannot_be_lost(self):
        """The whales this got backwards, named. If a future edit
        reverses them again these assertions are what fails."""
        src = _kalshi_src()
        k = _literal(src, "PER_COPY_USD")
        assert k["rn1"] > 0 and k["ferrarichampions2026"] > 0
        assert k["swisstony"] == 0.0 and k["homerunhazard"] == 0.0


class TestTheClipHasACeiling:
    def test_a_ceiling_exists_at_all(self):
        src = _kalshi_src()
        assert "MAX_COPY_USD" in src

    def test_it_is_not_looser_than_the_pmus_leg(self):
        src = _kalshi_src()
        cap = _literal(src, "MAX_COPY_USD")
        assert cap <= le.LIVE_MAX_CLIP_USD, \
            f"Kalshi authorizes {cap} where PMUS authorizes " \
            f"{le.LIVE_MAX_CLIP_USD}"

    def test_it_is_applied_AFTER_the_multiplier(self):
        """The defect was arithmetic, not the absence of a number: 600
        x 1.5 = 900. A ceiling applied to the base would not have
        caught it."""
        src = _kalshi_src()
        fn = src[src.index("def _per_copy_usd"):]
        fn = fn[:fn.index("\n\n\n")] if "\n\n\n" in fn else fn
        assert fn.index("mult") < fn.index("MAX_COPY_USD")

    def test_the_worst_historical_cell_would_now_be_capped(self):
        """The concrete number: ("homerunhazard","baseball") at 600.00
        with a spread's x1.5."""
        src = _kalshi_src()
        cap = _literal(src, "MAX_COPY_USD")
        assert 600.00 * 1.5 > cap
        assert min(600.00 * 1.5, cap) == cap


class TestTheBlockIsStructuralNotACellValue:
    def test_the_cut_set_is_consulted_before_any_clip_is_read(self):
        src = _kalshi_src()
        fn = src[src.index("def _per_copy_usd"):]
        assert fn.index("CUT_WHALES") < fn.index("PER_COPY_USD_SPORT"), \
            "a cut whale must be refused before a cell can size him"

    def test_a_resized_cell_cannot_defeat_the_cut(self):
        """The point of a structural block: someone edits a number back
        in and the whale is still refused."""
        src = _kalshi_src()
        fn = src[src.index("def _per_copy_usd"):]
        assert "return 0.00" in fn.split("PER_COPY_USD_SPORT")[0]


class TestTheLegIsStillDark:
    def test_the_pm_only_default_has_not_been_flipped(self):
        """Nothing in this fix arms the Kalshi leg. It stays off by
        default; the fix is that arming it would no longer copy the
        whales the owner cut."""
        here = pathlib.Path(__file__).resolve()
        root = next(
            (p for p in here.parents
             if (p / "edge-engine" / "src" / "edge").exists()), None)
        if root is None:
            pytest.skip("sibling tree not present in this checkout")
        runner = (root / "edge-engine" / "src" / "edge" / "shadow"
                  / "runner.py")
        if not runner.exists():
            pytest.skip("runner.py not present")
        src = runner.read_text()
        assert 'os.environ.get("EDGE_KCOPY_PM_ONLY", "1")' in src

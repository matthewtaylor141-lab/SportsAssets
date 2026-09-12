"""Run 83.2: the population admission path, and the V1 defects it closes.

EVERY TEST HERE THAT MATTERS IS A REGRESSION TEST FOR A MEASURED PRODUCTION
FAILURE, not a hypothetical. The numbers in the docstrings come from
RUN83_ACTIVATION_FAILED_V1 and from research/run831_population_audit.sql.

The three classes marked DELIBERATELY FAILING PRE-FIX are written so that they
PASS on this tree and FAIL on the V1 shape. Each one names, in its assertion
message, the V1 behaviour it rejects -- so if someone reverts the fix the test
does not merely go red, it says what came back.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from sportsassets.obs import subject
from sportsassets.obs.subject import AdmissionReason

_HERE = pathlib.Path(__file__).resolve().parent
_PIPELINE = _HERE.parent / "sportsassets" / "ingestion" / "pipeline.py"


# ---------------------------------------------------------------- the filter
def test_unset_subject_admits_nothing(monkeypatch):
    """DELIBERATELY FAILING PRE-FIX: V1 had no subject filter at all.

    V1's observe() checked shadow_enabled() and nothing else, so with no subject
    configured it admitted EVERY tracked wallet's fill. Measured consequence:
    11,867 of 12,535 events (94.671%) belonged to the other nineteen wallets.
    """
    monkeypatch.delenv("RN1_OBSERVABILITY_SUBJECT_WHALE_ID", raising=False)
    ok, reason = subject.admit(whale_id=2, was_insert=True)
    assert not ok, (
        "an unconfigured subject admitted an event. V1 shipped exactly this: no "
        "subject filter, and 94.671% of the first cohort was other wallets.")
    assert reason == AdmissionReason.NOT_CONFIGURED


@pytest.mark.parametrize("raw", ["", "   ", "abc", "2x", "0", "-1", "1.5"])
def test_malformed_subject_is_treated_as_unset(monkeypatch, raw):
    """A subject that cannot be parsed is NOT configured -- never coerced.

    Coercing "2x" to 2 would silently admit a population nobody chose, and the
    rows would all look valid.
    """
    monkeypatch.setenv("RN1_OBSERVABILITY_SUBJECT_WHALE_ID", raw)
    assert subject.configured_subject_whale_id() is None
    ok, reason = subject.admit(whale_id=2, was_insert=True)
    assert not ok and reason == AdmissionReason.NOT_CONFIGURED


def test_a_different_wallet_is_refused(monkeypatch):
    """The nineteen other roster wallets are refused one at a time."""
    monkeypatch.setenv("RN1_OBSERVABILITY_SUBJECT_WHALE_ID", "2")
    # whale ids taken verbatim from the audit's roster listing.
    for other in (1, 3, 5, 20, 21, 26, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37,
                  38, 39, 40):
        ok, reason = subject.admit(whale_id=other, was_insert=True)
        assert not ok, f"whale {other} was admitted while the subject is 2"
        assert reason == AdmissionReason.NOT_SUBJECT


def test_the_subject_is_admitted_on_first_receipt(monkeypatch):
    monkeypatch.setenv("RN1_OBSERVABILITY_SUBJECT_WHALE_ID", "2")
    ok, reason = subject.admit(whale_id=2, was_insert=True)
    assert ok and reason == AdmissionReason.ADMITTED


def test_a_re_swept_fill_is_refused_even_for_the_subject(monkeypatch):
    """DELIBERATELY FAILING PRE-FIX: V1 admitted re-swept history.

    V1's hook ran BEFORE the canonical insert, so a fill the ledger had already
    held -- up to 36.9 days, measured -- was observed as a brand-new event with a
    fresh anchor. 11,855 of the poll lane's 12,125 events were exactly this.
    """
    monkeypatch.setenv("RN1_OBSERVABILITY_SUBJECT_WHALE_ID", "2")
    ok, reason = subject.admit(whale_id=2, was_insert=False)
    assert not ok, (
        "a fill the canonical dedupe said was NOT new was admitted. That is the "
        "V1 defect: 94.6% of the first cohort was re-swept history, some of it "
        "36.9 days old, each row carrying a fresh receipt anchor.")
    assert reason == AdmissionReason.NOT_FIRST_RECEIPT


def test_an_event_without_a_whale_id_is_refused(monkeypatch):
    monkeypatch.setenv("RN1_OBSERVABILITY_SUBJECT_WHALE_ID", "2")
    ok, reason = subject.admit(whale_id=None, was_insert=True)
    assert not ok and reason == AdmissionReason.NO_WHALE_ID


def test_admit_is_pure_and_does_no_io(monkeypatch):
    """It runs on the ingestion hot path; a round trip here would be a defect."""
    monkeypatch.setenv("RN1_OBSERVABILITY_SUBJECT_WHALE_ID", "2")
    src = pathlib.Path(subject.__file__).read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "admit")
    assert not [n for n in ast.walk(fn) if isinstance(n, ast.Await)], (
        "subject.admit() awaits something. It is called synchronously from "
        "ingest_trade_result; an await here puts I/O on the ingestion path.")
    assert not isinstance(fn, ast.AsyncFunctionDef)


# ------------------------------------------------- the ordering in the pipeline
def _pipeline_tree() -> ast.Module:
    # Parsed from disk rather than imported: importing sportsassets.ingestion
    # pulls the whole worker graph (pywebpush et al) into a unit test.
    return ast.parse(_PIPELINE.read_text())


def _ingest_fn() -> ast.AsyncFunctionDef:
    return next(n for n in ast.walk(_pipeline_tree())
                if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "ingest_trade_result")


def test_the_anchor_is_stamped_before_anything_else():
    """_obs_stamp must be the FIRST statement, ahead of the pool acquisition.

    Stamping after `await get_pool()` would measure the pool, which is precisely
    what makes copy_probes.reaction_s unusable as a latency figure (run 82).
    """
    fn = _ingest_fn()
    body = [s for s in fn.body if not isinstance(s, ast.Expr)
            or not isinstance(s.value, ast.Constant)]      # skip the docstring
    first = body[0]
    assert isinstance(first, ast.Assign), f"first statement is {ast.dump(first)[:80]}"
    assert isinstance(first.value, ast.Call)
    assert getattr(first.value.func, "id", None) == "_obs_stamp", (
        "the first statement of ingest_trade_result is no longer the receipt "
        "stamp; the anchor would then include whatever runs ahead of it")


def test_admission_happens_after_the_canonical_insert():
    """DELIBERATELY FAILING PRE-FIX: V1 observed before the insert.

    The ordering is the fix, so it is asserted structurally: every _obs_admit
    call must appear later in the source than the `row = await pool.fetchrow(`
    that produces was_insert.
    """
    fn = _ingest_fn()
    # By line number inside the function, via the AST -- a substring scan of the
    # file also matches the `def _obs_admit(` above and the docstrings that
    # mention it, which is how the first version of this test failed on a
    # correct tree.
    inserts = [n.lineno for n in ast.walk(fn)
               if isinstance(n, ast.Await)
               and isinstance(n.value, ast.Call)
               and getattr(n.value.func, "attr", None) == "fetchrow"]
    admits = [n.lineno for n in ast.walk(fn)
              if isinstance(n, ast.Call)
              and getattr(n.func, "id", None) == "_obs_admit"]
    stamps = [n.lineno for n in ast.walk(fn)
              if isinstance(n, ast.Call)
              and getattr(n.func, "id", None) == "_obs_stamp"]
    assert inserts, "no pool.fetchrow found in ingest_trade_result"
    assert admits, "ingest_trade_result never calls _obs_admit"
    assert stamps, "ingest_trade_result never calls _obs_stamp"
    assert all(a > max(inserts) for a in admits), (
        "an _obs_admit call precedes the canonical trades insert. Admission "
        "cannot know was_insert before the statement that produces it, and "
        "admitting early is the V1 defect that observed 36.9-day-old fills.")
    assert max(stamps) < min(inserts), (
        "the receipt stamp is no longer strictly before the insert; the anchor "
        "would then include the pool round trip (run 82's copy_probes defect)")


def test_both_dedupe_outcomes_reach_admission():
    """A duplicate the DO UPDATE's WHERE matched nothing for returns NO row.

    That branch must still call _obs_admit(was_insert=False) -- otherwise a
    stamped pending observation is silently abandoned and the counters under-
    report, which is how a filter's own blind spot goes unnoticed.
    """
    src = _PIPELINE.read_text()
    assert "_obs_admit(_obs_pending, was_insert=False)" in src, (
        "the row-is-None duplicate branch does not call _obs_admit")
    assert 'was_insert=bool(row["was_insert"])' in src, (
        "the normal branch does not pass the canonical was_insert through")


def test_the_instrument_does_not_own_a_second_dedupe_authority():
    """Owner decision 4: reject a per-lane first_seen flag.

    The research instrument must consume the canonical answer, not derive its
    own. So no TradeEvent field named first_seen, and nothing in obs/ may query
    trades to decide first-receipt.
    """
    # Checked in the AST, not the text: pipeline.py's prose EXPLAINS why
    # first_seen was rejected, and a substring scan cannot tell an explanation
    # from a reintroduction.
    tree = _pipeline_tree()
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    kwargs = {k.arg for n in ast.walk(tree) if isinstance(n, ast.Call)
              for k in n.keywords if k.arg}
    fields = set()
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        for stmt in cls.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                fields.add(stmt.target.id)
    assert "first_seen" not in (names | attrs | kwargs | fields), (
        "a first_seen name reappeared as code in the pipeline. Owner decision 4 "
        "rejected it: the lanes must not each create a first-receipt truth "
        "source beside the canonical ON CONFLICT.")
    obs_dir = _HERE.parent / "sportsassets" / "obs"
    for path in obs_dir.glob("*.py"):
        text = path.read_text()
        assert "FROM trades" not in text and "from trades" not in text, (
            f"{path.name} queries the trades table. First-receipt is the "
            f"canonical insert's answer and must arrive as a parameter.")

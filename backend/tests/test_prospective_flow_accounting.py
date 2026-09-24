"""EVERY CANDIDATE ACCOUNTED FOR, so 400 and 3 stop contradicting.

The prospective lane reported `examined 400, refusals {CLASSIFY/UNKNOWN 2,
SEED/INITIAL_ENTRY 1}`. Both numbers were true and neither was the story:

  * `examined` counted the SCAN (SCAN_BATCH = 400 trades rows), not the
    processing;
  * `MAX_SEEDS_PER_CYCLE` is 3, so three processed rows is the CAP being
    reached, not a sample of the 400;
  * three branches skipped a row with `continue` and no counter at all --
    a duplicate condition inside the batch, a position already written,
    and everything past the cap.

A reader comparing 400 to 3 could only conclude that 397 rows vanished.
These tests pin the taxonomy and the identity that makes the census
self-checking.
"""
import ast
import inspect

from sportsassets.workers import rn1x_shadow as SH

BUCKETS = ("processed", "duplicate_condition_in_batch", "already_replayed",
           "deferred_cap", "not_reached_after_error")


def _cycle_src():
    return inspect.getsource(SH.cycle)


def test_the_cap_is_what_it_looks_like():
    """Three processed rows out of four hundred fetched is the cap, and the
    payload must say so rather than leaving it to be discovered."""
    assert SH.MAX_SEEDS_PER_CYCLE == 3
    assert SH.SCAN_BATCH == 400
    src = _cycle_src()
    assert '"cap_per_cycle"' in src
    assert '"scan_batch"' in src


def test_every_bucket_exists_and_is_named():
    src = _cycle_src()
    assert '"fetched"' in src, "the scan count must not be called examined"
    for b in BUCKETS:
        assert '"%s"' % b in src, b
    for b in ("refused", "failed", "written"):
        assert '"%s"' % b in src, b


def test_no_continue_in_the_candidate_loop_skips_silently():
    """A `continue` with no counter beside it is how 397 rows disappeared.

    Walks the loop body and requires every `continue` to be preceded,
    within its own branch, by a `flow[...]` increment.
    """
    tree = ast.parse(_cycle_src())
    loops = [n for n in ast.walk(tree)
             if isinstance(n, ast.For) and "cands" in ast.unparse(n.iter)]
    assert loops, "the candidate loop was not found"
    for loop in loops:
        for node in ast.walk(loop):
            if not isinstance(node, ast.If):
                continue
            body = ast.unparse(node.body)
            if "continue" not in body:
                continue
            assert "flow[" in body, (
                "a skipped candidate must increment a counter:\n%s" % body)


def test_the_identity_is_computed_not_asserted():
    """fetched == processed + duplicates + already + deferred + unreached.

    Computed in the payload as a boolean, so a future gap surfaces in the
    census instead of in someone's arithmetic.
    """
    src = _cycle_src()
    assert '"accounted"' in src
    for b in BUCKETS:
        assert 'flow["%s"]' % b in src, b


def test_the_identity_holds_on_the_arithmetic_it_claims():
    """The check itself must be the sum, not a constant True."""
    tree = ast.parse(_cycle_src())
    found = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and "accounted" in ast.unparse(node.targets)):
            found = ast.unparse(node.value)
    assert found, "no accounted assignment"
    assert "==" in found
    for b in BUCKETS:
        assert b in found, "%s missing from the identity" % b


def test_examined_is_kept_as_an_alias_for_the_old_readers():
    """Renaming it outright would break the reads that already quote it,
    and the number itself was never wrong -- only its name."""
    src = _cycle_src()
    assert '"examined": len(cands)' in src
    assert '"fetched": len(cands)' in src


def test_the_flow_reaches_the_heartbeat_the_tile_reads():
    src = inspect.getsource(SH.run)
    assert '"flow"' in src, (
        "the command centre reads the heartbeat summary, so the accounting "
        "has to be in it")


def test_a_failed_row_does_not_advance_the_cursor():
    """Unchanged behaviour, re-pinned here because the new counters sit in
    the same branch: a write that did not commit must be seen again."""
    src = _cycle_src()
    assert "stopped_at_error = rid" in src
    i = src.index("stopped_at_error = rid")
    assert "safe_id = rid" not in src[i:i + 400], (
        "the cursor must not move past a failed row")

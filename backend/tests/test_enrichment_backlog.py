"""The '(no slug)' funnel bucket's supply line (owner order 2026-08-13).

1,978 rejected copies in 7 days carried no metadata at all. The
backfill re-enricher scans `newest 200 unenriched`, so tokens Gamma
will NEVER know (other asset classes, delisted markets) accumulate
until they fill the whole window — then every fresh enrichable trade
behind them is never retried, while each cycle re-asks Gamma the same
200 dead questions. Source-asserted (importing the pipeline pulls in
redis, absent from this environment, same as test_chain_decode).
"""

import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "sportsassets" / "ingestion" / "pipeline.py").read_text()


def test_dead_tokens_leave_the_window():
    assert "MAX_ENRICH_FAILS" in SRC
    assert "NOT (asset = ANY($2::text[]))" in SRC, \
        "dead tokens must be excluded in the SELECT, not just skipped " \
        "after fetch — they were CLOGGING the window itself"


def test_failures_are_counted_and_successes_forgiven():
    body = SRC[SRC.index("async def backfill_unenriched"):]
    assert "_enrich_fails[a] = (n + 1, now)" in body
    assert "_enrich_fails.pop(str(row[\"asset\"]), None)" in body, \
        "a token that finally enriches must not stay blacklisted"


def test_write_off_is_slower_than_catalog_lag_and_amnestied():
    """Review 2026-08-13: 5 fails at a 60s cadence branded a merely-
    LATE token dead in ~5 minutes — faster than the catalog's own
    supply. 30 cycles (~30 min) writes off, 24h amnesty un-writes."""
    assert "MAX_ENRICH_FAILS = 30" in SRC
    assert "ENRICH_AMNESTY_S = 24 * 3600.0" in SRC
    body = SRC[SRC.index("async def backfill_unenriched"):]
    assert "now - ts > ENRICH_AMNESTY_S" in body


def test_trim_keeps_the_dead_not_the_new():
    """Review 2026-08-13 (confirmed): oldest-first trimming evicted
    exactly the confirmed-dead tokens — the memory worth keeping —
    and let the clog rebuild. Low-count entries are dropped first."""
    body = SRC[SRC.index("async def backfill_unenriched"):]
    assert "if n < MAX_ENRICH_FAILS" in body, \
        "the trim must target low-count (alive) entries first"


def test_ledger_is_bounded_and_observable():
    assert "8000" in SRC and "4000" in SRC, "the fail ledger is bounded"
    body = SRC[SRC.index("async def backfill_unenriched"):]
    assert "enrich_stats.update" in body
    assert "unenriched_1k" in body and "dead_tokens" in body
    ref = (pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
           / "workers" / "metadata_refresher.py").read_text()
    assert "enrich_stats" in ref, "stats must ride the heartbeat"


def test_backlog_probe_is_bounded():
    """The visibility count must never scan the whole trades table."""
    body = SRC[SRC.index("async def backfill_unenriched"):]
    assert "LIMIT 1000) t" in body

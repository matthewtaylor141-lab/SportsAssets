"""RC6.3 lifecycle-proof: ALLIE AT THE DECISION WAS UNAVAILABLE BECAUSE ONE OF
HER READS SCANNED us_premap ONCE PER SETTLED MARKET.

PRODUCTION EVIDENCE (SELECT-only research-sql runs 38009262456 and
38009524858, 2026-10-10 00:29Z / 00:33Z, research/rc63_lifecycle_proof_1.sql
and _2.sql on claude/p0-closeout):

  * canonical_decision_intents, all time: Allie MEASURED 39 (last
    2026-10-05 21:29:55Z), UNAVAILABLE COMPONENT_TIMEOUT_AT_DECISION_2.0S 74
    (2026-10-05 21:02:04Z .. 2026-10-06 03:00:57Z, every intent since
    21:38:48Z), UNAVAILABLE UNMEASURED_CAPITAL_EFFICIENCY 2. The Opportunity
    Score timed out on the same intents: both read the settlement-lag samples
    under the one cache key "lol_lags".
  * the settlement-lag read (profitability.reads.settlement_lag_samples) ran
    a LATERAL probe of us_premap per settled market; us_premap has no
    market_slug index (pg_indexes: only (identifier, side_norm) and a gin on
    event_keys) and holds 192,554 rows. EXPLAIN ANALYZE on production: 102
    settled markets -> 102 sequential scans, 2,249,324 buffer hits,
    7,807 ms -- four times the canonical component bound
    (canonical_components.COMPONENT_TIMEOUT_S = 2.0 s).
  * per intent, the settled-market count at the decision crossed from 21-29
    (MEASURED, 0.2-2.3 s) to 25-39 (timeout, 4.2-7.8 s). A timed-out read is
    never cached (canonical_components._cached stores only a result), so
    every later decision paid it again.

THE FIX reads the game starts once for all settled markets (as
lost_opportunity.reads.event_starts already does); the 2.0 s bound is
unchanged and asserted here. These proofs run on a real Postgres with the
production row count of us_premap (synthetic rows, scratch account; removed
afterwards).
"""
from __future__ import annotations

import os
import time

import asyncpg
import pytest

from sportsassets import canonical_components as CC
from sportsassets.profitability import reads as PR
from tests import paper_harness as H

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

PREFIX = "rc63lag-"
#: the production shape (research-sql 38009262456, B7 / B8)
PREMAP_ROWS = 192_554
SETTLED_MARKETS = 102

#: the per-market LATERAL read this proof replaced, verbatim (the reference
#: its results must equal)
OLD_LATERAL_SQL = (
    "SELECT extract(epoch FROM s.recorded_at)::float8 AS rec, "
    "       extract(epoch FROM s.settled_at - g.game_start)::float8 "
    "       AS lag "
    "  FROM (SELECT DISTINCT ON (us_market_slug) us_market_slug, "
    "               recorded_at, settled_at FROM paper_settlements "
    "         WHERE settled_at >= to_timestamp($1) "
    "           AND outcome IN ('WON', 'LOST') "
    "         ORDER BY us_market_slug, settled_at) s "
    "  JOIN LATERAL (SELECT game_start FROM us_premap "
    "                 WHERE market_slug = s.us_market_slug "
    "                   AND game_start IS NOT NULL "
    "                 ORDER BY updated_at DESC NULLS LAST LIMIT 1) g "
    "    ON true LIMIT $2")


async def _clean(conn, acct=None):
    await conn.execute("DELETE FROM us_premap WHERE identifier LIKE $1",
                       PREFIX + "%")
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "DELETE FROM paper_settlements WHERE settlement_id LIKE $1",
            "paperset:" + PREFIX + "%")


async def _settle(conn, acct, slug, *, outcome, settled_at, recorded_at,
                  n=0):
    sid = "paperset:%s%s:%d" % (PREFIX, slug, n)
    pk = "paperpos:%s:%sgrp:%s:LONG" % (acct, PREFIX, slug)
    await conn.execute(
        "INSERT INTO paper_settlements (settlement_id, account_id, "
        " position_key, settlement_event_key, version, group_id, "
        " us_market_slug, holding_side, qty, outcome, payout_per_contract, "
        " payout_usd, evidence, evidence_source, settled_at, recorded_at) "
        "VALUES ($1,$2,$3,$4,1,$5,$6,'LONG',1,$7,$8,$8,'{}'::jsonb,"
        " 'RC63_LAG_PROOF_SYNTHETIC',to_timestamp($9),to_timestamp($10))",
        sid, acct, pk, "venue-final:%s:%d" % (slug, n), PREFIX + "grp:" + slug,
        slug, outcome, 1 if outcome == "WON" else 0, float(settled_at),
        float(recorded_at))


async def _premap(conn, ident, slug, *, game_start, updated_at):
    await conn.execute(
        "INSERT INTO us_premap (identifier, side_norm, market_slug, "
        " game_start, updated_at) VALUES ($1, 'LONG', $2, "
        " to_timestamp($3), to_timestamp($4))", PREFIX + ident, slug,
        game_start, float(updated_at))


async def _production_shape(conn, now: float) -> dict:
    """SETTLED_MARKETS settled markets with their pre-map rows, the edge
    cases the reference read decides, and filler pre-map rows up to the
    production row count."""
    acct = (await H.new_account(conn, "rc63lag", now=now))["account_id"]
    rec = now - 60.0
    slugs = []
    for i in range(SETTLED_MARKETS):
        slug = "%smkt-%03d" % (PREFIX, i)
        gs = now - 86400.0 * (1 + i % 30) - 3600.0 * (i % 7)
        # lags 3.5 h + 60 i + 7 s: none equals an edge case's lag below
        await _settle(conn, acct, slug, outcome="WON" if i % 2 else "LOST",
                      settled_at=gs + 3.5 * 3600 + 60 * i + 7,
                      recorded_at=rec)
        # an older pre-map row with a DIFFERENT start: the newest row wins
        await _premap(conn, "old-%03d" % i, slug, game_start=gs - 7200.0,
                      updated_at=now - 30 * 86400.0)
        await _premap(conn, "new-%03d" % i, slug, game_start=gs,
                      updated_at=now - 86400.0 * (i % 30) - 10.0)
        slugs.append(slug)
    edge = PREFIX + "edge-"
    t = now - 5 * 86400.0
    # B: the newest row has no start -> the newest row WITH a start
    await _settle(conn, acct, edge + "b", outcome="WON", settled_at=t,
                  recorded_at=rec)
    await _premap(conn, "eb1", edge + "b", game_start=None,
                  updated_at=now - 100.0)
    await _premap(conn, "eb2", edge + "b", game_start=t - 4 * 3600.0,
                  updated_at=now - 200.0)
    # D: no pre-map row at all -> no sample
    await _settle(conn, acct, edge + "d", outcome="LOST", settled_at=t,
                  recorded_at=rec)
    # E: two settlements -> the EARLIER one
    await _settle(conn, acct, edge + "e", outcome="WON", settled_at=t,
                  recorded_at=rec, n=0)
    await _settle(conn, acct, edge + "e", outcome="LOST",
                  settled_at=t + 900.0, recorded_at=rec, n=1)
    await _premap(conn, "ee", edge + "e", game_start=t - 3 * 3600.0,
                  updated_at=now - 300.0)
    # F: a void refund only -> not an ordinary settlement
    await _settle(conn, acct, edge + "f", outcome="VOID_REFUND",
                  settled_at=t, recorded_at=rec)
    await _premap(conn, "ef", edge + "f", game_start=t - 3600.0,
                  updated_at=now - 300.0)
    # G: settled before the lookback -> out of the window
    await _settle(conn, acct, edge + "g", outcome="WON",
                  settled_at=now - (PR.LOOKBACK_DAYS * 2 + 3) * 86400.0,
                  recorded_at=rec)
    await _premap(conn, "eg", edge + "g", game_start=t - 3600.0,
                  updated_at=now - 300.0)
    # H: pre-map rows without a start only -> no sample
    await _settle(conn, acct, edge + "h", outcome="LOST", settled_at=t,
                  recorded_at=rec)
    await _premap(conn, "eh", edge + "h", game_start=None,
                  updated_at=now - 300.0)
    have = await conn.fetchval("SELECT count(*) FROM us_premap")
    fill = max(0, PREMAP_ROWS - int(have))
    await conn.execute(
        "INSERT INTO us_premap (identifier, side_norm, market_slug, "
        " game_start, updated_at) "
        "SELECT $1 || 'fill-' || g, 'LONG', $1 || 'fillmkt-' || (g % 60000),"
        "       CASE WHEN g % 2 = 0 THEN now() - make_interval(hours => g % 900)"
        "       END, now() - make_interval(secs => g) "
        "  FROM generate_series(1, $2) g", PREFIX, fill)
    await conn.execute("ANALYZE us_premap")
    await conn.execute("ANALYZE paper_settlements")
    return {"acct": acct, "slugs": slugs,
            "premap_rows": int(await conn.fetchval(
                "SELECT count(*) FROM us_premap"))}


@pg
async def test_the_lag_read_equals_the_per_market_reference_and_passes_us_premap_once():
    conn = await asyncpg.connect(DSN)
    now = time.time()
    try:
        await _clean(conn)
        shape = await _production_shape(conn, now)
        assert shape["premap_rows"] >= PREMAP_ROWS
        since = now - PR.LOOKBACK_DAYS * 2 * 86400.0
        ref = [(r["rec"], r["lag"]) for r in await conn.fetch(
            OLD_LATERAL_SQL, since, PR.MAX_ROWS)]
        # THE SAME SAMPLES as the reference read (as a multiset; the
        # consumer is a median, settlement_lag)
        async with conn.transaction():
            # a serial plan so this transaction's own counter sees every
            # tuple read (parallel workers report their own); the read
            # itself is unchanged
            await conn.execute("SET LOCAL max_parallel_workers_per_gather = 0")
            before = await conn.fetchrow(
                "SELECT coalesce(seq_tup_read, 0) AS seq, "
                "       coalesce(idx_tup_fetch, 0) AS idx "
                "  FROM pg_stat_xact_user_tables WHERE relname = 'us_premap'")
            got = await PR.settlement_lag_samples(conn, now=now)
            after = await conn.fetchrow(
                "SELECT coalesce(seq_tup_read, 0) AS seq, "
                "       coalesce(idx_tup_fetch, 0) AS idx "
                "  FROM pg_stat_xact_user_tables WHERE relname = 'us_premap'")
        assert sorted(x[0] for x in got) == sorted(x[0] for x in ref)
        assert sorted(x[1] for x in got) == pytest.approx(
            sorted(x[1] for x in ref), abs=1e-6)
        assert len(got) == len(ref) >= SETTLED_MARKETS + 2
        mine = {round(x[1], 3) for x in got}
        # the edge cases, decided as the reference decides them (D, F, G and
        # H contribute no sample: the multiset equality above covers them)
        assert round(4 * 3600.0, 3) in mine          # B: newest WITH a start
        assert round(3 * 3600.0, 3) in mine          # E: the earlier settlement
        assert round(3 * 3600.0 + 900.0, 3) not in mine
        # ONE PASS OVER us_premap, whatever the number of settled markets
        # (the per-market probe read SETTLED_MARKETS + edges x the table)
        read = (after["seq"] - (before["seq"] or 0)) + (
            after["idx"] - (before["idx"] or 0))
        assert read <= shape["premap_rows"], (
            "us_premap tuples read by one lag read: %d for a table of %d "
            "rows (%.1f passes)" % (read, shape["premap_rows"],
                                    read / shape["premap_rows"]))
    finally:
        await _clean(conn)
        await conn.close()


@pg
async def test_allie_is_measured_at_the_decision_on_the_production_shape_inside_the_unchanged_bound():
    # the bound is NOT relaxed to manufacture availability
    assert CC.COMPONENT_TIMEOUT_S == 2.0
    conn = await asyncpg.connect(DSN)
    now = time.time()
    try:
        await _clean(conn)
        await _production_shape(conn, now)
        CC.reset_cache()
        decision = {"decision_id": PREFIX + "decision", "decided_at": now,
                    "event_start_at": now + 3 * 3600.0, "fixture": None,
                    "executable_opportunity_dollars": 40.0,
                    "capital_required_usd": 500.0, "limit_price": 0.5,
                    "depth_within_limit": 2000, "per_order_cap_usd": 1000.0,
                    "us_market_slug": PREFIX + "mkt-000"}
        eddie = {"status": "MEASURED", "expected_executable_ev_usd": 50.0,
                 "max_executable_qty": 1000}
        t0 = time.perf_counter()
        got = await CC.allie_at_decision(conn, decision=decision, eddie=eddie,
                                         now=now)
        took = time.perf_counter() - t0
        assert got.get("why") != "COMPONENT_TIMEOUT_AT_DECISION_2.0S", (
            took, got)
        assert got["status"] == "MEASURED", got
        samples = await PR.settlement_lag_samples(conn, now=now)
        n = len([1 for r, lag in samples if lag is not None and lag >= 0
                 and r is not None and r <= now])
        assert n >= SETTLED_MARKETS
        assert got["evidence"]["settlement_lag_samples"] == n
        assert got["evidence"]["measured"]["settlement_lag"] is True
        assert got["expected_hours_to_capital_release"] is not None
        assert took < CC.COMPONENT_TIMEOUT_S
        # and the Opportunity Score, which shares the read, is not timed out
        CC.reset_cache()
        opp = await CC.opportunity_at_decision(
            conn, decision=dict(decision, status="MEASURED"),
            eddie=dict(eddie, expected_fill_probability=0.5), now=now)
        assert opp.get("why") != "COMPONENT_TIMEOUT_AT_DECISION_2.0S", opp
    finally:
        CC.reset_cache()
        await _clean(conn)
        await conn.close()

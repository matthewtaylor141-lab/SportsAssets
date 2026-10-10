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

REVIEW 1 -- THE ONE-PASS READ STILL LEFT ALLIE UNAVAILABLE IN PRODUCTION.
us_premap is a live catalogue: workers/premap.py deletes the rows it has not
re-seen for PRUNE_HOURS (26 h), and a settled market is no longer listed.
On 2026-10-10 production had 102 settled markets in the lookback and ONE
still in the pre-map (research-sql 38014993180, V3-V5), so the one-pass read
returned 1 sample, below allie_capital.MIN_LAG_SAMPLE = 5: the next decision
would have recorded Allie UNAVAILABLE (SETTLEMENT_LAG_SAMPLE_1_BELOW_5), and
the Opportunity Score and the warehouse's expected release with her. The
first proofs below give every settled market a pre-map row -- the pre-map
BEFORE the prune, which is not the production state -- and prove the cost
and the reference equality there. The pruned proofs age the settled
markets' pre-map rows past PRUNE_HOURS, apply premap's own prune statement,
and prove the read takes each start from the warehouse's recorded event
start (pos_position_economics), else Xavier's entry thesis, labelled with
its basis, and that Allie is MEASURED inside the unchanged bound. The
timing proofs depend on the machine; the deterministic proof of the base
defect is the tuple count of the first test.
"""
from __future__ import annotations

import inspect
import os
import time

import asyncpg
import pytest

from sportsassets import allie_capital as AC
from sportsassets import canonical_components as CC
from sportsassets.profitability import economics as EC
from sportsassets.profitability import reads as PR
from sportsassets.workers import premap as PM
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
        await conn.execute(
            "DELETE FROM pos_position_economics WHERE econ_id LIKE $1",
            PREFIX + "%")
        await conn.execute(
            "DELETE FROM xavier_entry_theses WHERE thesis_id LIKE $1",
            PREFIX + "%")


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


#: the metrics a warehouse row names when it leaves them NULL
#: (pos_econ_named_nulls_ck)
_ECON_UNMEASURED = (
    '{"net_profit_usd": "T", "expected_net_profit_usd": "T", '
    '"capital_hours": "T", "expected_capital_hours": "T", '
    '"realized_profit_per_capital_hour": "T", '
    '"expected_profit_per_capital_hour": "T"}')


async def _warehouse(conn, slug, *, event_start, computed_at, revision=1,
                     book="PAPER", tag="p"):
    """One pos_position_economics row (a revision of one position) with the
    event start the warehouse recorded for it (None: no start recorded)."""
    pk = "paperpos:%s%s:%s" % (PREFIX, tag, slug)
    cf = book == "COUNTERFACTUAL"
    await conn.execute(
        "INSERT INTO pos_position_economics (econ_id, book, position_key, "
        " revision, counterfactual_kind, basis_book, basis_position_key, "
        " run_id, computed_at, us_market_slug, holding_side, strategy, "
        " state, event_start_at, unmeasured, content_sha256, version) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, 'rc63lag-run', "
        " to_timestamp($8), $9, 'LONG', 'RC63_LAG_PROOF_SYNTHETIC', "
        " 'CLOSED', to_timestamp($10), $11::jsonb, $12, 'rc63lag')",
        "%s%s:%s:%s:%d" % (PREFIX, book, tag, slug, revision), book, pk,
        revision, "HOLD_TO_SETTLEMENT" if cf else None,
        "PAPER" if cf else None, pk if cf else None, float(computed_at),
        slug, None if event_start is None else float(event_start),
        _ECON_UNMEASURED, "0" * 64)


async def _thesis(conn, slug, *, event_start, recorded_at):
    """One Xavier entry thesis on the market with its event start."""
    await conn.execute(
        "INSERT INTO xavier_entry_theses (thesis_id, position_kind, "
        " group_id, position_ref, us_market_slug, holding_side, entered_at, "
        " entry_qty, probability_limit_s, assumptions, evidence_refs, "
        " event_start_at, expiry_basis, counterfactuals, content_sha256, "
        " recorded_at) "
        "VALUES ($1, 'PAPER', $2, $3, $4, 'LONG', to_timestamp($5), 1, 600, "
        " '{}'::jsonb, '[]'::jsonb, to_timestamp($6), 'RC63_LAG_PROOF', "
        " '{\"HOLD_TO_SETTLEMENT\": null, \"IMMEDIATE_EXIT\": null, "
        "   \"ACTUAL_XAVIER\": null}'::jsonb, $7, to_timestamp($5))",
        PREFIX + "thesis:" + slug, PREFIX + "grp:" + slug,
        PREFIX + "ref:" + slug, slug, float(recorded_at), float(event_start),
        "0" * 64)


#: premap's own prune (workers/premap.py, the full lane's sweep end), scoped
#: here to this proof's rows so nothing else in the database is touched
PRUNE_SQL = ("DELETE FROM us_premap WHERE updated_at < now() - "
             "interval '%s hours'" % int(PM.PRUNE_HOURS))


async def _prune_settled_premap(conn, *, keep: str) -> str:
    """The settled markets stop being listed: the sweep no longer re-sees
    their pre-map rows, which age past PRUNE_HOURS (all but `keep`, settled
    inside the last day), and premap's own prune statement deletes them."""
    src = inspect.getsource(PM)
    assert ('"DELETE FROM us_premap WHERE updated_at < now() - "\n'
            '                "interval \'%s hours\'%s" % (int(PRUNE_HOURS), '
            'guard_sql)') in src, "premap's prune statement changed"
    await conn.execute(
        "UPDATE us_premap SET updated_at = now() - make_interval("
        " hours => $3 + 1) WHERE identifier LIKE $1 "
        "   AND identifier NOT LIKE $2 AND market_slug <> $4",
        PREFIX + "%", PREFIX + "fill-%", int(PM.PRUNE_HOURS), keep)
    return await conn.execute(PRUNE_SQL + " AND identifier LIKE $1",
                              PREFIX + "%")


async def _production_shape(conn, now: float) -> dict:
    """SETTLED_MARKETS settled markets with their pre-map rows, the edge
    cases the reference read decides, and filler pre-map rows up to the
    production row count."""
    acct = (await H.new_account(conn, "rc63lag", now=now))["account_id"]
    rec = now - 60.0
    slugs = []
    starts = {}
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
        starts[slug] = (gs, gs + 3.5 * 3600 + 60 * i + 7)
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
        "       END, now() - make_interval(secs => g % 90000) "
        "  FROM generate_series(1, $2) g", PREFIX, fill)
    await conn.execute("ANALYZE us_premap")
    await conn.execute("ANALYZE paper_settlements")
    return {"acct": acct, "slugs": slugs, "starts": starts,
            "premap_rows": int(await conn.fetchval(
                "SELECT count(*) FROM us_premap"))}


#: the basis each sample's game start is labelled with (the read's own
#: constants are asserted equal to these in the pruned proof)
BASIS_PREMAP = "US_PREMAP_GAME_START"
BASIS_WAREHOUSE = "POS_POSITION_ECONOMICS_EVENT_START"
BASIS_THESIS = "XAVIER_ENTRY_THESIS_EVENT_START"

#: the settled markets (by index) whose start survives only in Xavier's
#: entry thesis, and those with no durable start at all (one of them with a
#: COUNTERFACTUAL warehouse row only, which is not a real position's)
THESIS_ONLY = (5, 41, 77)
NO_START = (17, 89)
COUNTERFACTUAL_ONLY = (53,)
KEEP = 0       # the one settled market the pre-map still holds


async def _pruned_production_shape(conn, now: float) -> dict:
    """The production state on 2026-10-10 (research-sql 38014993180): the
    settled markets' pre-map rows pruned but one, their event starts
    recorded by the warehouse (100 of 102 in production) or Xavier's
    thesis. Returns the expected (lag, basis) per settled market."""
    shape = await _production_shape(conn, now)
    expect = {}
    for i, slug in enumerate(shape["slugs"]):
        gs, settled = shape["starts"][slug]
        if i in NO_START:
            continue
        if i in COUNTERFACTUAL_ONLY:
            await _warehouse(conn, slug, event_start=gs - 7777.0,
                             computed_at=now - 3 * 86400.0,
                             book="COUNTERFACTUAL", tag="cf")
            continue
        if i in THESIS_ONLY:
            await _thesis(conn, slug, event_start=gs + 600.0,
                          recorded_at=gs - 3600.0)
            expect[slug] = (settled - gs - 600.0, BASIS_THESIS)
            continue
        wstart = gs - 1800.0 if i % 10 == 0 else gs
        if i % 7 == 3:
            # an older revision with another start, and a newer revision
            # recorded after the prune with NO start: the latest recorded
            # start is the one taken, never erased by a later NULL
            await _warehouse(conn, slug, event_start=gs - 5400.0,
                             computed_at=now - 10 * 86400.0, revision=1)
            await _warehouse(conn, slug, event_start=wstart,
                             computed_at=now - 5 * 86400.0, revision=2)
            await _warehouse(conn, slug, event_start=None,
                             computed_at=now - 3600.0, revision=3)
        else:
            await _warehouse(conn, slug, event_start=wstart,
                             computed_at=now - 2 * 86400.0)
        if i % 10 == 0:
            # Xavier's thesis disagrees: the warehouse is taken first
            await _thesis(conn, slug, event_start=gs + 900.0,
                          recorded_at=gs - 3600.0)
        expect[slug] = (settled - wstart, BASIS_WAREHOUSE)
    keep = shape["slugs"][KEEP]
    gs, settled = shape["starts"][keep]
    # the pre-map still holds it: its own start, though the warehouse
    # (gs - 1800) and the thesis (gs + 900) disagree
    expect[keep] = (settled - gs, BASIS_PREMAP)
    shape["pruned"] = await _prune_settled_premap(conn, keep=keep)
    await conn.execute("ANALYZE us_premap")
    await conn.execute("ANALYZE pos_position_economics")
    await conn.execute("ANALYZE xavier_entry_theses")
    shape["expect"] = expect
    shape["premap_rows"] = int(await conn.fetchval(
        "SELECT count(*) FROM us_premap"))
    return shape


def _decision(now):
    return {"decision_id": PREFIX + "decision", "decided_at": now,
            "event_start_at": now + 3 * 3600.0, "fixture": None,
            "executable_opportunity_dollars": 40.0,
            "capital_required_usd": 500.0, "limit_price": 0.5,
            "depth_within_limit": 2000, "per_order_cap_usd": 1000.0,
            "us_market_slug": PREFIX + "mkt-000"}


EDDIE = {"status": "MEASURED", "expected_executable_ev_usd": 50.0,
         "max_executable_qty": 1000}


@pg
async def test_the_lag_read_equals_the_per_market_reference_and_passes_us_premap_once():
    conn = await asyncpg.connect(DSN)
    now = time.time()
    try:
        await _clean(conn)
        shape = await _production_shape(conn, now)
        assert shape["premap_rows"] >= PREMAP_ROWS
        # durable starts that DISAGREE with the pre-map on every market the
        # pre-map holds (review 1): the pre-map is taken first, so the read
        # still equals the pre-map-only reference wherever the pre-map
        # holds the market
        for slug, (gs, _) in shape["starts"].items():
            await _warehouse(conn, slug, event_start=gs - 1800.0,
                             computed_at=now - 86400.0)
            await _thesis(conn, slug, event_start=gs + 900.0,
                          recorded_at=gs - 3600.0)
        for slug in (PREFIX + "edge-b", PREFIX + "edge-e"):
            await _warehouse(conn, slug, event_start=now - 9 * 86400.0,
                             computed_at=now - 86400.0)
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
async def test_allie_is_measured_inside_the_unchanged_bound_at_the_production_premap_row_count_with_every_settled_market_still_mapped():
    # NOT the production state: every settled market keeps its pre-map row
    # here (the pre-map before the prune); this proves the COST at the
    # production row count -- the pruned proofs below prove the production
    # state. The bound is NOT relaxed to manufacture availability
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


@pg
async def test_the_lag_read_survives_the_premap_prune_each_start_labelled_with_its_basis():
    conn = await asyncpg.connect(DSN)
    now = time.time()
    try:
        await _clean(conn)
        shape = await _pruned_production_shape(conn, now)
        # premap's own prune removed the settled markets' rows but one
        assert int(shape["pruned"].split()[-1]) >= (SETTLED_MARKETS - 1) * 2
        held = await conn.fetchval(
            "SELECT count(DISTINCT market_slug) FROM us_premap "
            " WHERE market_slug = ANY($1::text[])", shape["slugs"])
        assert held == 1
        # AT LEAST THE SAMPLE ALLIE NEEDS (the base read gives 1 here)
        samples = await PR.settlement_lag_samples(conn, now=now)
        assert len(samples) >= AC.MIN_LAG_SAMPLE
        assert len(samples) >= EC.MIN_LAG_SAMPLES
        # EVERY market's start from the first source that holds it, the
        # basis named; none from a counterfactual row; none invented
        assert (PR.LAG_START_PREMAP, PR.LAG_START_WAREHOUSE,
                PR.LAG_START_THESIS) == (BASIS_PREMAP, BASIS_WAREHOUSE,
                                         BASIS_THESIS)
        async with conn.transaction():
            await conn.execute("SET LOCAL max_parallel_workers_per_gather = 0")
            before = {r["relname"]: r["seq"] + r["idx"] for r in await conn.fetch(
                "SELECT relname, coalesce(seq_tup_read, 0) AS seq, "
                "       coalesce(idx_tup_fetch, 0) AS idx "
                "  FROM pg_stat_xact_user_tables WHERE relname = ANY($1)",
                ["us_premap", "pos_position_economics",
                 "xavier_entry_theses"])}
            recs = await PR.settlement_lag_records(conn, now=now)
            after = {r["relname"]: r["seq"] + r["idx"] for r in await conn.fetch(
                "SELECT relname, coalesce(seq_tup_read, 0) AS seq, "
                "       coalesce(idx_tup_fetch, 0) AS idx "
                "  FROM pg_stat_xact_user_tables WHERE relname = ANY($1)",
                ["us_premap", "pos_position_economics",
                 "xavier_entry_theses"])}
        got = {r["us_market_slug"]: (r["lag"], r["game_start_basis"])
               for r in recs}
        assert set(got) == set(shape["expect"]), (
            sorted(set(got) ^ set(shape["expect"])))
        for slug, (lag, basis) in shape["expect"].items():
            assert got[slug][1] == basis, (slug, got[slug], basis)
            assert got[slug][0] == pytest.approx(lag, abs=1e-3), slug
        bases = [b for _, b in got.values()]
        assert bases.count(PR.LAG_START_PREMAP) == 1
        assert bases.count(PR.LAG_START_THESIS) == len(THESIS_ONLY)
        assert bases.count(PR.LAG_START_WAREHOUSE) == SETTLED_MARKETS - 1 - (
            len(THESIS_ONLY) + len(NO_START) + len(COUNTERFACTUAL_ONLY))
        for i in NO_START + COUNTERFACTUAL_ONLY:
            assert shape["slugs"][i] not in got
        # the consumers' shape is the records' projection
        assert sorted(samples) == sorted((r["rec"], r["lag"]) for r in recs)
        # ONE PASS PER TABLE, whatever the number of settled markets
        for t in ("us_premap", "pos_position_economics",
                  "xavier_entry_theses"):
            rows = await conn.fetchval("SELECT count(*) FROM %s" % t)
            read = after.get(t, 0) - before.get(t, 0)
            assert read <= rows, (t, read, rows)
        # a fallback table absent from this database is read as absent
        # (the pre-map's one market alone, as the read before the fix)
        async with conn.transaction():
            await conn.execute("ALTER TABLE pos_position_economics "
                               "RENAME TO rc63lag_pe_absent")
            await conn.execute("ALTER TABLE xavier_entry_theses "
                               "RENAME TO rc63lag_xt_absent")
            only = await PR.settlement_lag_records(conn, now=now)
            await conn.execute("ALTER TABLE rc63lag_pe_absent "
                               "RENAME TO pos_position_economics")
            await conn.execute("ALTER TABLE rc63lag_xt_absent "
                               "RENAME TO xavier_entry_theses")
        assert [(r["us_market_slug"], r["game_start_basis"])
                for r in only] == [(shape["slugs"][KEEP],
                                    PR.LAG_START_PREMAP)]
    finally:
        await _clean(conn)
        await conn.close()


@pg
async def test_allie_and_the_opportunity_score_are_measured_after_the_premap_prune_inside_the_unchanged_bound():
    # THE PRODUCTION STATE: the settled markets' pre-map rows pruned but
    # one. The base read gives 1 sample here, and Allie UNAVAILABLE
    # UNMEASURED_CAPITAL_EFFICIENCY:expected_hours_to_capital_release
    # (SETTLEMENT_LAG_SAMPLE_1_BELOW_5). The bound is NOT relaxed.
    assert CC.COMPONENT_TIMEOUT_S == 2.0
    conn = await asyncpg.connect(DSN)
    now = time.time()
    try:
        await _clean(conn)
        shape = await _pruned_production_shape(conn, now)
        assert shape["premap_rows"] >= PREMAP_ROWS - SETTLED_MARKETS * 3
        CC.reset_cache()
        t0 = time.perf_counter()
        got = await CC.allie_at_decision(conn, decision=_decision(now),
                                         eddie=EDDIE, now=now)
        took = time.perf_counter() - t0
        samples = await PR.settlement_lag_samples(conn, now=now)
        n = len([1 for r, lag in samples if lag is not None and lag >= 0
                 and r is not None and r <= now])
        assert n >= AC.MIN_LAG_SAMPLE, n
        assert got.get("why") != "COMPONENT_TIMEOUT_AT_DECISION_2.0S", (
            took, got)
        assert got["status"] == "MEASURED", got
        assert got["evidence"]["settlement_lag_samples"] == n
        assert got["evidence"]["measured"]["settlement_lag"] is True
        assert got["expected_hours_to_capital_release"] is not None
        assert took < CC.COMPONENT_TIMEOUT_S
        # the Opportunity Score shares the read: its hold is measured (it
        # may stay UNAVAILABLE for this scratch database's other gaps, e.g.
        # no PAPER capital snapshot, never for the settlement lag)
        CC.reset_cache()
        opp = await CC.opportunity_at_decision(
            conn, decision=dict(_decision(now), status="MEASURED"),
            eddie=dict(EDDIE, expected_fill_probability=0.5), now=now)
        assert opp.get("why") != "COMPONENT_TIMEOUT_AT_DECISION_2.0S", opp
        assert EC.R_NO_LAG not in str(opp.get("why") or ""), opp
    finally:
        CC.reset_cache()
        await _clean(conn)
        await conn.close()

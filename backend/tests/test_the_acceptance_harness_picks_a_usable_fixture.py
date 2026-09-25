"""THE ACCEPTANCE HARNESS MUST ADOPT ONLY WHAT CAN STILL BE DEMONSTRATED.

THE BLOCKER THESE TESTS OPEN, AND WHY IT IS NOT A WEAKENED GUARD.
`EXISTING_ACCEPTANCE_SQL` looks up on `(experiment_id, policy)` and adopts
whatever it finds. The acceptance position is an MLB game that finished on
2026-09-24: the odds provider stops carrying a finished fixture, no
`EV_HOLD` can be identified for it again, and the harness therefore kept
returning `adopted: true` on a position no recurring demonstration could
ever use. Step 19 then had no supported subject at all and fell back to
judging unsupported tennis inventory.

WHAT THE IDEMPOTENCE GUARD ACTUALLY PROTECTS is a SECOND position on the
SAME market -- "adding inventory while reporting idempotence". Declining a
stale adoption does not do that. So under an explicit flag the adoption
becomes conditional on the harness's OWN candidate predicate, and:

  * the stale position is left exactly as it is -- not modified, not
    reseeded, not settled, provenance untouched;
  * nothing is seeded while ANY acceptance position is still usable, so
    there is never more than one demonstrable acceptance position;
  * an unreadable staleness check adopts rather than seeds, because an
    unreadable check establishes nothing and the conservative direction is
    the one that writes no row;
  * with the flag off, behaviour is byte-for-byte what it was.

The usability question is answered by reusing `COVERED_CANDIDATES_SQL`'s
clauses, per clause, so a refusal names which clause failed instead of
asserting staleness.
"""

from __future__ import annotations

import os

import pytest

from sportsassets.workers import ext_pinnacle_loop as EXT
from sportsassets.workers import rn1x_shadow as W

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

EXP = "ACCEPTANCE_FRESHNESS_TEST_EXPERIMENT"
STALE_COND = "c-acc-stale-finished"
LIVE_COND = "c-acc-live-current"

POS_SQL = """
    INSERT INTO rn1x_positions (position_id, experiment_id, policy,
        source_trade_id, source_account, condition_id, outcome_index,
        entry_kind, entry_kind_why, seed_qty, seed_price, seed_basis_usd,
        source_ts, detected_ts, decision_ts, decision_basis, provenance)
    VALUES ($1,$2,$3,NULL,'t',$4,0,'NEW','t',10.0,0.60,6.0,
            now(),now(),now(),'RUNTIME_WALL_CLOCK',$5)
    ON CONFLICT (position_id) DO NOTHING
"""

UNTOUCHED_SQL = """
    SELECT provenance, entry_kind, seed_qty::text AS q,
           seed_price::text AS p, seed_basis_usd::text AS b, condition_id
      FROM rn1x_positions WHERE position_id = $1
"""


def _labels():
    return sorted({lbl for fam in {f for _, f in EXT.SPORTS}
                   for lbl in EXT.VENUE_SPORT_LABELS.get(fam, ())})


async def _market(conn, cond, *, sport, closed, resolved, tokens=2,
                  fresh=True):
    await conn.execute(
        "INSERT INTO markets (condition_id, title, event_title, slug, "
        "sport, closed, resolved, updated_at) "
        "VALUES ($1,'t','t',$2,$3,$4,$5, "
        "        CASE WHEN $6 THEN now() ELSE now() - interval '30 days' END) "
        "ON CONFLICT (condition_id) DO UPDATE SET sport = EXCLUDED.sport, "
        "closed = EXCLUDED.closed, resolved = EXCLUDED.resolved, "
        "updated_at = EXCLUDED.updated_at", cond, cond + "-slug", sport,
        closed, resolved, fresh)
    for i in range(tokens):
        await conn.execute(
            "INSERT INTO market_tokens (token_id, condition_id, outcome, "
            "outcome_index) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (token_id) DO NOTHING",
            "%s-tok%d" % (cond, i), cond, "Side %d" % i, i)


async def _seed(conn):
    await conn.execute(
        "INSERT INTO rn1x_experiments (experiment_id, code_version, "
        "seed_rule, policy_register, execution_basis, notes) "
        "VALUES ($1,'V','{}'::jsonb,'{}'::jsonb,'MODELLED','t') "
        "ON CONFLICT (experiment_id) DO NOTHING", EXP)


async def _cleanup(conn):
    for sql, a in (
            ("DELETE FROM rn1x_decisions WHERE position_id LIKE $1",
             "%s:%%" % EXP),
            ("DELETE FROM rn1x_positions WHERE experiment_id = $1", EXP),
            ("DELETE FROM markets WHERE condition_id = ANY($1::text[])",
             [STALE_COND, LIVE_COND]),
            ("DELETE FROM rn1x_experiments WHERE experiment_id = $1", EXP)):
        try:
            await conn.execute(sql, a)
        except Exception:                                      # noqa: BLE001
            pass


# ── the usability predicate names the clause that failed ─────────────

@pg
@pytest.mark.asyncio
async def test_it_names_which_clause_made_the_fixture_unusable():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _cleanup(conn)
        await _seed(conn)
        sport = _labels()[0]
        await _market(conn, STALE_COND, sport=sport, closed=True,
                      resolved=True)
        got = await W.acceptance_fixture_still_usable(
            conn, STALE_COND, labels=_labels(),
            stale_after_s=EXT.MARKET_STALE_AFTER_S)
        assert got["usable"] is False
        assert "NOT_CLOSED" in got["failed"]
        assert "NOT_RESOLVED" in got["failed"]
        assert got["predicate"].startswith("COVERED_CANDIDATES_SQL")

        await _market(conn, LIVE_COND, sport=sport, closed=False,
                      resolved=False)
        ok = await W.acceptance_fixture_still_usable(
            conn, LIVE_COND, labels=_labels(),
            stale_after_s=EXT.MARKET_STALE_AFTER_S)
        assert ok["usable"] is True, ok
        assert ok["failed"] == []
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_uncovered_sport_and_a_stale_row_each_fail_their_clause():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _cleanup(conn)
        await _seed(conn)
        await _market(conn, STALE_COND, sport="Tennis", closed=False,
                      resolved=False)
        got = await W.acceptance_fixture_still_usable(
            conn, STALE_COND, labels=_labels(),
            stale_after_s=EXT.MARKET_STALE_AFTER_S)
        assert got["usable"] is False
        assert got["failed"] == ["COVERED_SPORT"], got
        # THE TENNIS CASE, named as coverage rather than as staleness --
        # which is the distinction step 19 was collapsing.
        assert got["row"]["sport"] == "Tennis"

        await _market(conn, LIVE_COND, sport=_labels()[0], closed=False,
                      resolved=False, fresh=False)
        st = await W.acceptance_fixture_still_usable(
            conn, LIVE_COND, labels=_labels(),
            stale_after_s=EXT.MARKET_STALE_AFTER_S)
        assert st["failed"] == ["FRESH_ENOUGH"], st
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_one_token_is_not_a_bindable_market():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _cleanup(conn)
        await _seed(conn)
        await _market(conn, LIVE_COND, sport=_labels()[0], closed=False,
                      resolved=False, tokens=1)
        got = await W.acceptance_fixture_still_usable(
            conn, LIVE_COND, labels=_labels(),
            stale_after_s=EXT.MARKET_STALE_AFTER_S)
        assert got["failed"] == ["BOTH_TOKENS"], got
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_missing_market_row_establishes_nothing_by_name():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        got = await W.acceptance_fixture_still_usable(
            conn, "c-does-not-exist-at-all", labels=_labels(),
            stale_after_s=EXT.MARKET_STALE_AFTER_S)
        assert got["usable"] is False
        assert got["failed"] == ["NO_MARKETS_ROW_FOR_THIS_CONDITION"]
    finally:
        await conn.close()


# ── the adoption path, with and without the flag ─────────────────────

@pg
@pytest.mark.asyncio
async def test_with_the_flag_off_a_stale_position_is_still_adopted():
    """DEFAULT BEHAVIOUR IS UNCHANGED. The flag is opt-in precisely so that
    this release cannot alter what the existing route already does."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _cleanup(conn)
        await _seed(conn)
        await _market(conn, STALE_COND, sport=_labels()[0], closed=True,
                      resolved=True)
        pid = "%s:POS:stale" % EXP
        await conn.execute(POS_SQL, pid, EXP, W.ACCEPTANCE_POLICY,
                           STALE_COND, W.ACCEPTANCE_PROVENANCE)
        got = await W.seed_acceptance_position(conn, experiment_id=EXP)
        assert got["adopted"] is True, got
        assert got["position_id"] == pid
        assert "stale_adoption_declined" not in got
        assert "existing_fixture_check" not in got
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_with_the_flag_on_a_stale_adoption_is_declined_by_name():
    """AND THE STALE POSITION IS LEFT EXACTLY AS IT IS. Declining to adopt
    is not touching: no reseed, no settlement, no provenance change."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _cleanup(conn)
        await _seed(conn)
        await _market(conn, STALE_COND, sport=_labels()[0], closed=True,
                      resolved=True)
        pid = "%s:POS:stale" % EXP
        await conn.execute(POS_SQL, pid, EXP, W.ACCEPTANCE_POLICY,
                           STALE_COND, W.ACCEPTANCE_PROVENANCE)
        before = dict(await conn.fetchrow(UNTOUCHED_SQL, pid))

        got = await W.seed_acceptance_position(conn, experiment_id=EXP,
                                              fresh_if_stale=True)
        assert got["adopted"] is False, got
        d = got.get("stale_adoption_declined")
        assert d, got
        assert d["position_id"] == pid
        assert d["refusal"] == W.R_STALE_ADOPTION_DECLINED
        assert d["left_untouched"] is True
        assert "NOT_CLOSED" in d["failed_clauses"]

        # IT WENT ON TO LOOK FOR A CURRENT FIXTURE, and there is none in
        # this fixture DB, so it refuses by name rather than inventing one.
        assert got["created"] is False
        assert got.get("refusal") in (W.R_NO_COVERED_CANDIDATE,
                                      W.R_NO_MAPPED_CANDIDATE), got

        # AND NOTHING ABOUT THE STALE POSITION MOVED.
        after = dict(await conn.fetchrow(UNTOUCHED_SQL, pid))
        assert after == before, (before, after)
        assert after["provenance"] == W.ACCEPTANCE_PROVENANCE
        n = await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE experiment_id = $1",
            EXP)
        assert n == 1, "no second position may be written on a refusal"
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_it_will_not_seed_while_a_usable_acceptance_position_exists():
    """THE GUARD, RE-AIMED AND NOT WEAKENED. Two acceptance positions on
    live fixtures would be two demonstrable subjects and an ambiguous
    gate, so a still-usable one blocks seeding outright."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _cleanup(conn)
        await _seed(conn)
        sport = _labels()[0]
        await _market(conn, STALE_COND, sport=sport, closed=True,
                      resolved=True)
        await _market(conn, LIVE_COND, sport=sport, closed=False,
                      resolved=False)
        stale = "%s:POS:stale" % EXP
        live = "%s:POS:live" % EXP
        await conn.execute(POS_SQL, stale, EXP, W.ACCEPTANCE_POLICY,
                           STALE_COND, W.ACCEPTANCE_PROVENANCE)
        await conn.execute(POS_SQL, live, EXP, W.ACCEPTANCE_POLICY,
                           LIVE_COND, W.ACCEPTANCE_PROVENANCE)
        # The lookup returns one of them; whichever it is, a usable
        # acceptance position exists and nothing new may be seeded.
        got = await W.seed_acceptance_position(conn, experiment_id=EXP,
                                              fresh_if_stale=True)
        assert got["created"] is False, got
        if got["adopted"]:
            assert got["condition_id"] == LIVE_COND
        else:
            assert got["refusal"] == W.R_ANOTHER_IS_DEMONSTRABLE, got
            assert any(x["condition_id"] == LIVE_COND
                       for x in got["demonstrable_positions"])
        n = await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE experiment_id = $1",
            EXP)
        assert n == 2, "no third position"
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_live_fixture_is_adopted_under_the_flag_too():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _cleanup(conn)
        await _seed(conn)
        await _market(conn, LIVE_COND, sport=_labels()[0], closed=False,
                      resolved=False)
        pid = "%s:POS:live" % EXP
        await conn.execute(POS_SQL, pid, EXP, W.ACCEPTANCE_POLICY,
                           LIVE_COND, W.ACCEPTANCE_PROVENANCE)
        got = await W.seed_acceptance_position(conn, experiment_id=EXP,
                                              fresh_if_stale=True)
        assert got["adopted"] is True, got
        assert got["position_id"] == pid
        assert got["existing_fixture_check"]["usable"] is True
        assert "stale_adoption_declined" not in got
    finally:
        await _cleanup(conn)
        await conn.close()

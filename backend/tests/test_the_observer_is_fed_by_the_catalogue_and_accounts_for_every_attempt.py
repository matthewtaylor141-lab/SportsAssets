"""THE NON-FUNDED OBSERVER, FED BY THE VENUE'S CATALOGUE, ACCOUNTABLE PER ATTEMPT.

On the production cycle of 2026-09-29 the observer was offered nothing: its
only candidates were identities the ENTRY lane resolved, and the entry lane
refused every event before identity. These tests pin the repair and pin that
it is not a relaxation:

  * candidates come from the venue's own catalogue (`us_premap`) through the
    supplier's OWN predicates -- realism, a graded variable, fixture identity,
    orientation, a captured overtime rule -- applied before any venue read,
    and every excluded row and fixture is counted by name;
  * a stale catalogue is refused, not read as the venue's current listing;
  * every attempted candidate is accounted for: its outcome and the refusal
    at every depth, in the pass result and in the append-only attempt ledger
    (migration 142), which refuses an unnamed refusal;
  * the pass's venue reads are counted and bounded, and a book read past the
    budget is refused by name without a request;
  * observation-sourced candidates are SCORED without a funded account, and
    nothing is promoted;
  * the cycle runs the observer when entry is BLOCKED (from the catalogue),
    not when the lane is STOPPED, and the heartbeat persists what it did.

ENGINEERING PROOF ON SUBSTITUTED TRANSPORT: the book, prose and settlement
reads are substituted; the catalogue query, screen, supplier, discovery,
recorder, ledger, labeller, registry and heartbeat are the deployed ones.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import time

import asyncpg
import pytest

from sportsassets import bettor_funded_model as FMD
from sportsassets import bettor_live_read as LR
from sportsassets import bettor_pair_observations as PO
from tests import test_the_hedge_beats_hold_through_the_real_suppliers as HW
from tests import test_the_pairing_model_bootstraps_from_non_funded_observations as BOOT

DSN = os.environ.get("RN1X_TEST_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="needs a migrated database")

LONG, SHORT = PO.LONG, PO.SHORT
PRICES = {HW.HELD: (0.55, 500), HW.SIB: (0.30, 500)}
#: Extra fixtures this module seeds, removed afterwards.
X_EVENTS = ("mlb-sea-tex-2026-10-05", "atp-abc-xyz-2026-10-05",
            "mlb-det-cle-2026-10-05", "ebfpl-ars-che-2026-10-05",
            "mlb-hou-oak-2026-10-05")


@contextlib.asynccontextmanager
async def _conn():
    c = await asyncpg.connect(DSN)
    try:
        yield c
    finally:
        await c.close()


async def _purge_attempts(conn):
    if await conn.fetchval(
            "SELECT to_regclass('bettor_pair_observation_attempts')") is None:
        return
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute("DELETE FROM bettor_pair_observation_attempts")


async def _clean(conn):
    await BOOT._clean(conn)
    await _purge_attempts(conn)
    await conn.execute("DELETE FROM us_premap WHERE event_slug = ANY($1)",
                       list(X_EVENTS))
    PO._ATTEMPTED.clear()


_SWEEP_KEYS = ("premap_last", "premap_last_fast")


@pytest.fixture(autouse=True)
async def _leave_nothing_behind():
    saved = {}
    if DSN:
        async with _conn() as c:
            for k in _SWEEP_KEYS:
                saved[k] = await c.fetchval(
                    "SELECT value::text FROM ingestion_state WHERE key=$1", k)
    yield
    if DSN:
        async with _conn() as c:
            await _clean(c)
            await HW._clean(c)
            for k, v in saved.items():
                if v is None:
                    await c.execute("DELETE FROM ingestion_state WHERE key=$1",
                                    k)
                else:
                    await c.execute(
                        "INSERT INTO ingestion_state (key, value) VALUES "
                        "($1, $2::jsonb) ON CONFLICT (key) DO UPDATE SET "
                        "value = $2::jsonb", k, v)
    PO._ATTEMPTED.clear()


async def _sweep_ran(conn, *, ago_s: float = 60.0):
    at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=ago_s)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ('premap_last', "
        " $1::jsonb) ON CONFLICT (key) DO UPDATE SET value=$1::jsonb",
        json.dumps({"mode": "full", "at": at.isoformat(timespec="seconds")}))
    await conn.execute("DELETE FROM ingestion_state WHERE key='premap_last_fast'")


async def _row(conn, *, event, slug, st, abbr, intent, title=None,
               signed=None, line="00", side="x", starts="3 hours",
               seen="now()"):
    await conn.execute(
        "INSERT INTO us_premap (identifier, event_slug, event_title, "
        " market_slug, question, kind, line, side_norm, intent, signed, "
        " team_abbr, team_name, sports_type, game_start, updated_at) VALUES "
        " ($1,$2,$3,$4,'Who will win?','side',$5,$6,$7,$8,$9,$9,$10, "
        "  now() + ($11::text)::interval, %s)" % seen,
        "%s:%s:%s" % (slug, abbr, intent), event, title or "A vs. B", slug,
        line, side, intent, signed, abbr, st, starts)


async def _seed_the_catalogue(conn):
    """The HW fixture (a moneyline and a spread: admissible), plus one row of
    every kind the screen must exclude before a venue read."""
    await HW._catalogue(conn)
    for c in ("updated_at",):
        await conn.execute("ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS "
                           "%s timestamptz NOT NULL DEFAULT now()" % c)
    # a tennis match: its family has no captured overtime prose
    await _row(conn, event="atp-abc-xyz-2026-10-05",
               slug="aec-atp-abc-xyz-2026-10-05",
               st="tennis_match_winner", abbr="abc", intent=LONG)
    # a baseball player prop: not a graded variable
    await _row(conn, event="mlb-det-cle-2026-10-05",
               slug="astatc-mlb-det-cle-2026-10-05-hits",
               st="baseball_player_hits", abbr="det", intent=LONG)
    # a simulated soccer fixture wearing a real family's type
    await _row(conn, event="ebfpl-ars-che-2026-10-05",
               slug="aec-ebfpl-ars-che-2026-10-05",
               st="soccer_team_full_time_winner", abbr="ars", intent=LONG,
               title="eSoccer Battle: ARS vs. CHE")
    # a real baseball fixture with only ONE graded contract
    await _row(conn, event="mlb-sea-tex-2026-10-05",
               slug="aec-mlb-sea-tex-2026-10-05",
               st="baseball_team_full_game_winner", abbr="sea", intent=LONG)
    # a real fixture already started, and one the sweep stopped seeing
    await _row(conn, event="mlb-hou-oak-2026-10-05",
               slug="aec-mlb-hou-oak-2026-10-05",
               st="baseball_team_full_game_winner", abbr="hou", intent=LONG,
               starts="-1 hour")
    await _row(conn, event="mlb-hou-oak-2026-10-05",
               slug="asc-mlb-hou-oak-2026-10-05-neg-1pt5",
               st="baseball_team_full_game_spread", abbr="hou", intent=LONG,
               signed="-1.5", seen="now() - interval '5 hours'")


# ═════════════════════════════════════════════════════════════════════
# 1 · CANDIDATES FROM THE CATALOGUE, SCREENED BEFORE ANY VENUE READ
# ═════════════════════════════════════════════════════════════════════

async def test_the_catalogue_offers_the_real_graded_fixture_and_names_every_exclusion():
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        await _sweep_ran(conn)
        got = await PO.catalogue_candidates(conn, now=time.time())
        assert got["ok"] is True, got
        mine = [c for c in got["candidates"] if c["fixture"] == HW.EVENT]
        assert len(mine) == 1, got["candidates"]
        c = mine[0]
        # a full-game winner's LONG side is the preferred first leg
        assert c["us_market_slug"] == HW.HELD and c["side"] == LONG
        assert c["source"] == PO.SOURCE_CATALOGUE
        assert c["graded_contracts"] == 2
        others = {c["fixture"] for c in got["candidates"]}
        for fx in X_EVENTS:
            assert fx not in others, (fx, got["candidates"])
        ex = got["excluded_rows"]
        assert ex.get(PO.X_FAMILY_NOT_CAPTURED, 0) >= 1
        assert ex.get(PO.X_NOT_A_GRADED_VARIABLE, 0) >= 1
        assert ex.get(PO.X_NOT_REAL, 0) >= 1, ex
        assert got["excluded_fixtures"].get(PO.X_FEWER_THAN_TWO, 0) >= 1
        assert got["venue_reads"] == 0


async def test_a_stale_catalogue_is_refused_by_name():
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        await _sweep_ran(conn, ago_s=PO.CATALOGUE_SWEEP_FRESH_S + 600)
        got = await PO.catalogue_candidates(conn, now=time.time())
        assert got["ok"] is False
        assert got["refusal"] == PO.R_CATALOGUE_SWEEP_STALE
        assert got["candidates"] == []
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           list(_SWEEP_KEYS))
        none = await PO.catalogue_candidates(conn, now=time.time())
        assert none["refusal"] == PO.R_CATALOGUE_SWEEP_STALE


async def test_a_fixture_observed_or_refused_recently_is_not_offered_again():
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        await _sweep_ran(conn)
        PO.note_attempt(HW.EVENT, at=time.time(), refusal="SOMETHING_NAMED")
        got = await PO.catalogue_candidates(conn, now=time.time())
        assert HW.EVENT not in {c["fixture"] for c in got["candidates"]}
        assert got["excluded_fixtures"].get(PO.X_ATTEMPTED_RECENTLY, 0) >= 1
        PO._ATTEMPTED.clear()
        # observed: the table itself is the memory
        obs = await BOOT._observe(conn, at=time.time())
        assert obs["recorded"], obs
        again = await PO.catalogue_candidates(conn, now=time.time())
        assert HW.EVENT not in {c["fixture"] for c in again["candidates"]}
        assert again["excluded_fixtures"].get(PO.X_OBSERVED_RECENTLY, 0) >= 1


def test_the_screen_is_the_suppliers_own_predicates():
    base = {"intent": LONG, "event_slug": HW.EVENT,
            "event_title": "Boston Red Sox vs. New York Yankees",
            "question": "Who will win?", "market_slug": HW.HELD,
            "sports_type": "baseball_team_full_game_winner",
            "team_abbr": "bos", "side_norm": "boston red sox"}
    assert PO.screen_row(base) is None
    assert PO.screen_row(dict(base, intent="ORDER_INTENT_SELL_LONG")) \
        == PO.X_SIDE
    assert PO.screen_row(dict(base, sports_type="efootball_team_full_time_"
                                                "winner")) \
        == PO.X_FAMILY_NOT_CAPTURED
    assert PO.screen_row(dict(base, event_title="Simulated Reality League")) \
        == PO.X_NOT_REAL
    assert PO.screen_row(dict(base, sports_type="baseball_player_hits")) \
        == PO.X_NOT_A_GRADED_VARIABLE
    assert PO.screen_row(dict(base, event_slug="mlb-bos-nyy-sea-2026-10-05")) \
        == PO.X_FIXTURE_IDENTITY
    assert PO.screen_row(dict(base, team_abbr="lad")) == PO.X_ORIENTATION


# ═════════════════════════════════════════════════════════════════════
# 2 · EVERY ATTEMPT ACCOUNTED FOR; EVERY READ COUNTED AND BOUNDED
# ═════════════════════════════════════════════════════════════════════

def _reader(slug):
    return {"status": LR.PENDING, "settlement_price": None}


async def test_a_catalogue_candidate_is_observed_and_its_attempt_is_ledgered():
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        await _sweep_ran(conn)
        cat = await PO.catalogue_candidates(conn, now=time.time())
        mine = [c for c in cat["candidates"] if c["fixture"] == HW.EVENT]
        got = await PO.observation_pass(
            conn, candidates=[], catalogue=mine,
            quoter=HW._quoter(PRICES), prose_reader=HW._prose_reader(),
            settlement_reader=_reader, now=time.time())
        assert got["ok"] is True, got
        assert got["candidates_offered_by_source"] == {
            PO.SOURCE_CATALOGUE: 1}
        a = got["observed"][0]
        assert a["outcome"] == "RECORDED", a
        assert a["source"] == PO.SOURCE_CATALOGUE
        assert a["fixture"] == HW.EVENT
        assert got["observations_written"] >= 1
        assert got["outcomes"] == {"RECORDED": 1}
        u = got["venue_usage"]
        # the held contract plus its sibling's two sides, each a book read
        assert u["book_reads"] >= 2 and u["book_refused_for_budget"] == 0
        assert u["bound"]["book_reads_per_pass"] == PO.BOOK_READS_PER_PASS
        row = dict(await conn.fetchrow(
            "SELECT * FROM bettor_pair_observation_attempts "
            " WHERE pass_id=$1", got["pass_id"]))
        assert row["outcome"] == "RECORDED"
        assert row["candidate_source"] == PO.SOURCE_CATALOGUE
        assert row["observations_written"] >= 1
        assert got["attempt_ledger"] == {"present": True, "written": 1,
                                         "failed": 0}
        assert got["sent_anything"] is False
        assert got["promoted_anything"] is False


async def test_a_refused_attempt_names_its_refusal_at_every_depth():
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        got = await PO.observation_pass(
            conn, candidates=[], catalogue=[{"us_market_slug": HW.HELD,
                                             "side": LONG,
                                             "fixture": HW.EVENT}],
            quoter=HW._quoter({}), prose_reader=HW._prose_reader(),
            settlement_reader=_reader, now=time.time())
        a = got["observed"][0]
        assert a["outcome"] == "REFUSED"
        assert a["refusal"] == PO.R_NO_PRICE
        assert a["quote_refusal"] == "NOT_IN_THE_CAPTURED_BOOK"
        assert got["refusals"] == {
            PO.R_NO_PRICE + " <- NOT_IN_THE_CAPTURED_BOOK": 1}
        row = dict(await conn.fetchrow(
            "SELECT * FROM bettor_pair_observation_attempts "
            " WHERE pass_id=$1", got["pass_id"]))
        assert row["outcome"] == "REFUSED"
        assert row["refusal"] == PO.R_NO_PRICE
        detail = row["detail"] if isinstance(row["detail"], dict) \
            else json.loads(row["detail"])
        assert detail["quote_refusal"] == "NOT_IN_THE_CAPTURED_BOOK"
        # a refused fixture is remembered, so the next pass does not retry it
        assert HW.EVENT in PO._ATTEMPTED


async def test_a_book_read_past_the_budget_is_refused_without_a_request():
    calls = []
    inner = HW._quoter(PRICES)

    async def counting(slug, side):
        calls.append((slug, side))
        return await inner(slug, side)
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        got = await PO.observation_pass(
            conn, candidates=[(HW.HELD, LONG)], quoter=counting,
            prose_reader=HW._prose_reader(), settlement_reader=_reader,
            now=time.time(), book_budget=1)
        a = got["observed"][0]
        u = got["venue_usage"]
        assert len(calls) == 1, calls
        assert u["book_reads"] == 1 and u["book_refused_for_budget"] >= 1
        assert a["budget_limited"] is True
        assert a["outcome"] == "NOTHING_ADMITTED", a
        assert a["second_legs_refused"], a
        # A BUDGET-LIMITED ATTEMPT WAS NOT A JUDGEMENT of the fixture, so it
        # is not remembered as refused.
        assert HW.EVENT not in PO._ATTEMPTED


async def test_the_attempt_ledger_refuses_an_unnamed_refusal_and_is_append_only():
    async with _conn() as conn:
        await _clean(conn)
        ins = ("INSERT INTO bettor_pair_observation_attempts (pass_id, "
               " attempted_at, finished_at, candidate_source, us_market_slug, "
               " side, outcome, refusal, observations_written) VALUES "
               " ('t', now(), now(), $1, 's', $2, $3, $4, $5) "
               "RETURNING attempt_id")
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.fetchval(ins, PO.SOURCE_CATALOGUE, LONG, "REFUSED",
                                None, 0)
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.fetchval(ins, PO.SOURCE_CATALOGUE, LONG, "RECORDED",
                                None, 0)
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.fetchval(ins, "SOMEWHERE_ELSE", LONG, "REFUSED",
                                "X", 0)
        aid = await conn.fetchval(ins, PO.SOURCE_ENTRY, LONG, "REFUSED",
                                  "NAMED", 0)
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute("UPDATE bettor_pair_observation_attempts SET "
                               " refusal='OTHER' WHERE attempt_id=$1", aid)
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute("DELETE FROM bettor_pair_observation_attempts "
                               " WHERE attempt_id=$1", aid)


async def test_settlement_reads_are_counted_per_pass():
    async with _conn() as conn:
        await _clean(conn)
        _, prices = await BOOT._cohort(conn, n=2, first_at=time.time() - 7200,
                                       prefix="obcnt")
        got = await PO.observation_pass(
            conn, candidates=[], quoter=None, prose_reader=None,
            settlement_reader=BOOT._settlements(prices), now=time.time())
        assert got["labels"]["labelled"] == 2, got["labels"]
        u = got["venue_usage"]
        assert u["settlement_reads"] == 4          # two pairs, two legs each
        assert u["settlement_dispatches"] == 0     # a substituted reader
        assert u["settlement_dispatches_unknown"] == 0


# ═════════════════════════════════════════════════════════════════════
# 3 · SCORED WITHOUT A FUNDED ACCOUNT; NOTHING PROMOTED
# ═════════════════════════════════════════════════════════════════════

async def test_observation_candidates_are_scored_without_a_funded_account():
    async with _conn() as conn:
        await _clean(conn)
        t0 = time.time() - 3 * 86400
        _, px = await BOOT._cohort(conn, n=50, first_at=t0, prefix="obscr")
        await BOOT._label(conn, px, at=t0 + 86400)
        got = await PO.observation_pass(
            conn, candidates=[], quoter=None, prose_reader=None,
            settlement_reader=_reader, now=time.time())
        assert got["generate"]["generated"] is True, got["generate"]
        mid = got["generate"]["model_id"]
        ev = got["evaluate"]
        assert ev["ok"] is True, ev
        scored = {s["model_id"]: s for s in ev["scored"]}
        assert mid in scored, ev
        # NO PROSPECTIVE EVIDENCE YET: scored, recorded, refused -- by name
        assert scored[mid]["ok"] is False
        assert scored[mid]["refusal"] == FMD.R_TOO_FEW_LABELS
        assert scored[mid]["prospective_events"] == 0
        stored = await conn.fetchval(
            "SELECT evaluation FROM bettor_funded_models WHERE model_id=$1",
            mid)
        assert stored is not None
        state = await conn.fetchval(
            "SELECT state FROM bettor_funded_models WHERE model_id=$1", mid)
        assert state == FMD.STATE_CANDIDATE
        assert ev["promoted_anything"] is False
        assert got["promoted_anything"] is False
        assert (await FMD.approved(conn))["ok"] is False


# ═════════════════════════════════════════════════════════════════════
# 4 · THE CYCLE RUNS IT WHEN ENTRY IS BLOCKED, AND THE HEARTBEAT KEEPS IT
# ═════════════════════════════════════════════════════════════════════

async def _heartbeat_row(conn):
    from sportsassets.workers import ext_pinnacle_loop as L
    v = await conn.fetchval("SELECT value FROM ingestion_state WHERE key=$1",
                            L.HEARTBEAT_KEY)
    return json.loads(v) if isinstance(v, str) else v


async def test_a_blocked_entry_cycle_still_observes_from_the_catalogue(
        monkeypatch):
    from sportsassets.workers import ext_pinnacle_loop as L

    quote = HW._quoter(PRICES)

    async def _q(slug, side, *, now=None):
        return await quote(slug, side)

    async def _running(conn):
        return True, None

    async def _ready(conn):
        return True

    async def _no_service(conn, *, now=None):
        return None
    monkeypatch.setattr(L, "observation_quote", _q)
    monkeypatch.setattr(L, "_venue_prose", HW._prose_reader())
    monkeypatch.setattr(L, "_running", _running)
    monkeypatch.setattr(L, "_table_ready", _ready)
    monkeypatch.setattr(L, "_funded_service", _no_service)
    monkeypatch.setattr(L.ext, "credential_present",
                        lambda: {"present": False,
                                 "refusal": "ODDS_CREDENTIAL_ABSENT"})
    monkeypatch.setattr(L, "_LAST_OBSERVATION_PASS", [0.0])
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        await _sweep_ran(conn)
        out = await L.cycle(conn)
        assert out["state"] == "BLOCKED"
        po = out["pair_observation"]
        assert po["ran"] is True and po["ok"] is True, po
        assert po["lane_state"] == "BLOCKED:ODDS_CREDENTIAL_ABSENT"
        assert po["candidates_offered_by_source"].get(PO.SOURCE_CATALOGUE)
        assert any(a["outcome"] == "RECORDED" for a in po["observed"]), po
        hb = await _heartbeat_row(conn)
        d = hb["pair_observation"]
        assert d["ran"] is True
        assert d["observations_written"] >= 1
        assert d["candidates"]["by_source"].get(PO.SOURCE_CATALOGUE)
        assert d["catalogue"]["ok"] is True
        assert d["venue_usage"]["book_reads"] >= 1
        assert hb["why"] == "ODDS_CREDENTIAL_ABSENT"
        # A SECOND BLOCKED CYCLE WITHIN THE INTERVAL does not read again,
        # and says so.
        again = await L.cycle(conn)
        assert again["pair_observation"]["ran"] is False
        assert again["pair_observation"]["why"] == L.R_OBSERVATION_NOT_DUE


async def test_a_stopped_lane_makes_no_observation_reads_and_says_so(
        monkeypatch):
    from sportsassets.workers import ext_pinnacle_loop as L

    reads = []

    async def _q(slug, side, *, now=None):
        reads.append(slug)
        return {"ok": False, "refusal": "SHOULD_NOT_BE_CALLED"}

    async def _stopped(conn):
        return False, "OPERATOR_STOPPED_THE_LANE"

    async def _no_service(conn, *, now=None):
        return None
    monkeypatch.setattr(L, "observation_quote", _q)
    monkeypatch.setattr(L, "_running", _stopped)
    monkeypatch.setattr(L, "_funded_service", _no_service)
    monkeypatch.setattr(L, "_LAST_OBSERVATION_PASS", [0.0])
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        await _sweep_ran(conn)
        out = await L.cycle(conn)
        assert out["state"] == "STOPPED"
        assert reads == []
        hb = await _heartbeat_row(conn)
        assert hb["why"] == "OPERATOR_STOPPED_THE_LANE"
        assert hb["pair_observation"]["ran"] is False
        assert hb["pair_observation"]["why"] == \
            L.R_OBSERVER_STOPPED_WITH_THE_LANE


# ═════════════════════════════════════════════════════════════════════
# 5 · THE DIGESTS: BOUNDED, NEVER RAISING, NOTHING SILENTLY NULL
# ═════════════════════════════════════════════════════════════════════

def test_the_observation_digest_keeps_every_count_and_bounds_the_sample():
    from sportsassets.workers import ext_pinnacle_loop as L

    assert L._observation_digest(None) is None
    assert "digest_failed" in L._observation_digest("nonsense")
    many = [{"us_market_slug": "s%d" % i, "side": LONG, "outcome": "REFUSED",
             "refusal": PO.R_NO_PRICE, "recorded": []} for i in range(20)]
    d = L._observation_digest({
        "ok": True, "observed": many, "candidates_offered": 20,
        "attempted": 20, "outcomes": {"REFUSED": 20},
        "refusals": {PO.R_NO_PRICE: 20},
        "labels": {"ok": True, "labelled": 3}, "generate": {"ok": True},
        "evaluate": {"ok": True, "scored": [{"model_id": "m"}]},
        "venue_usage": {"book_reads": 7}})
    assert d["ran"] is True
    assert len(d["attempts"]) == L.SERVICING_DIGEST_LIMIT
    assert d["attempts_truncated_at"] == L.SERVICING_DIGEST_LIMIT
    assert d["outcomes"] == {"REFUSED": 20}
    assert d["refusals"] == {PO.R_NO_PRICE: 20}
    assert d["labels"]["labelled"] == 3
    assert d["venue_usage"] == {"book_reads": 7}
    stopped = L._observation_digest({"ran": False, "why": "X"})
    assert stopped["ran"] is False and stopped["why"] == "X"


def test_the_servicing_digest_counts_the_settlement_reads_it_used_to_drop():
    from sportsassets.workers import ext_pinnacle_loop as L

    d = L._servicing_digest({
        "ok": True, "selection": [],
        "settlement": [{"closed": True}, {"closed": False,
                                          "refusal": "STILL_OPEN"}],
        "settlement_rechecks": {"ok": True, "disagreements": 1,
                                "rechecked": [{"verdict": "AGREES"},
                                              {"verdict": "DISAGREES"}]}})
    assert d["settlement"] == {"positions_read": 2,
                               "by_result": {"CLOSED": 1, "STILL_OPEN": 1}}
    assert d["settlement_rechecks"]["by_verdict"] == {"AGREES": 1,
                                                      "DISAGREES": 1}
    assert d["settlement_rechecks"]["disagreements"] == 1


async def test_an_oversized_heartbeat_is_trimmed_and_says_so():
    from sportsassets.workers import ext_pinnacle_loop as L

    class _C:
        def __init__(self):
            self.args = None

        async def execute(self, sql, *args):
            self.args = args
    c = _C()
    await L._heartbeat(c, {
        "state": "LIVE", "refusals": {"A": 1},
        "mapped_candidate_ledger": [{"pad": "x" * 1000}] * 400,
        "pair_observation": {"ok": True, "observed": [
            {"us_market_slug": "s", "refusal": "R" * 20000}] * 3}})
    hb = json.loads(c.args[1])
    assert hb["heartbeat_trimmed"]["emptied"], hb["heartbeat_trimmed"]
    assert hb["mapped_candidate_ledger"] == []
    assert hb["refusals"] == {"A": 1}
    assert len(c.args[1]) <= L.HEARTBEAT_MAX_BYTES


# ═════════════════════════════════════════════════════════════════════
# 6 · SIBLINGS ARE SCREENED BEFORE THEY COST A READ; TRUNCATION IS SAID
# ═════════════════════════════════════════════════════════════════════

async def test_a_sibling_that_cannot_be_a_leg_is_refused_before_its_book_is_read():
    calls = []
    inner = HW._quoter(PRICES)

    async def counting(slug, side):
        calls.append(slug)
        return await inner(slug, side)
    prop = "astatc-mlb-bos-nyy-2026-10-05-hits"
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        # a player prop on the SAME fixture: never a graded variable
        await _row(conn, event=HW.EVENT, slug=prop, st="baseball_player_hits",
                   abbr="bos", intent=LONG)
        got = await PO.observe_candidate(
            conn, us_market_slug=HW.HELD, side=LONG, quoter=counting,
            prose_reader=HW._prose_reader(), now=time.time())
        assert got["ok"] is True, got
        assert prop not in calls, calls
        assert got["second_legs_refused"].get(PO.X_NOT_A_GRADED_VARIABLE), got
        # the admissible sibling was still read and observed
        assert HW.SIB in calls and got["recorded"], got


async def test_a_truncated_sweep_is_reported_not_hidden():
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ('premap_last', "
            " $1::jsonb) ON CONFLICT (key) DO UPDATE SET value=$1::jsonb",
            json.dumps({"mode": "full", "at": at, "truncated": True}))
        got = await PO.catalogue_candidates(conn, now=time.time())
        assert got["ok"] is True
        assert got["sweep_truncated"]["premap_last"] is True


async def test_the_cooldown_resume_row_keeps_what_was_resumed():
    from sportsassets.workers import ext_pinnacle_loop as L

    class _C:
        args = None

        async def execute(self, sql, *args):
            _C.args = args
    await L._heartbeat(_C(), {"state": "COOLDOWN_RESUME_AT_STARTUP",
                              "cooldown_resume": {"resumed": False,
                                                  "why": "NOTHING_STORED"}},
                       key=L.COOLDOWN_RESUME_KEY)
    hb = json.loads(_C.args[1])
    assert hb["cooldown_resume"] == {"resumed": False, "why": "NOTHING_STORED"}


# ═════════════════════════════════════════════════════════════════════
# 4 · WHY NO SECOND CONTRACT: EACH SIBLING BY CONTRACT, AND A CONCLUSION
#     THAT SEPARATES ABSENCE FROM A BLOCK
# ═════════════════════════════════════════════════════════════════════

def _sib(cat, rank=1, **kw):
    return dict({"category": cat, "search_rank": rank}, **kw)


def test_absence_is_concluded_only_when_nothing_that_could_match_was_unread():
    C = PO
    assert C.attempt_conclusion([], fixture_pairs=0) == C.N_ABSENT
    # only props on the fixture: no second GRADED contract exists
    assert C.attempt_conclusion([_sib(C.C_FILTERED, rank=3)] * 5,
                                fixture_pairs=5) == C.N_GRADED_ABSENT
    # every graded sibling examined and graded on another variable
    assert C.attempt_conclusion([_sib(C.C_INCOMPATIBLE)] * 3,
                                fixture_pairs=3) == C.N_INCOMPATIBLE
    # ONE sibling the pass could not afford to read: not a determination
    assert C.attempt_conclusion(
        [_sib(C.C_INCOMPATIBLE), _sib(C.C_READ_BUDGET)],
        fixture_pairs=2) == C.N_BUDGET
    assert C.attempt_conclusion(
        [_sib(C.C_INCOMPATIBLE), _sib(C.C_METADATA)],
        fixture_pairs=2) == C.N_METADATA
    assert C.attempt_conclusion(
        [_sib(C.C_INCOMPATIBLE), _sib(C.C_INTERPRETATION)],
        fixture_pairs=2) == C.N_INTERPRETATION
    assert C.attempt_conclusion(
        [_sib(C.C_INCOMPATIBLE), _sib(C.C_UNPRICED)],
        fixture_pairs=2) == C.N_UNPRICED
    # a refusal nobody classified never reads as absence
    assert C.attempt_conclusion([_sib(C.C_UNNAMED)],
                                fixture_pairs=1) == C.N_UNNAMED
    # siblings past the limit were never examined
    assert C.attempt_conclusion([_sib(C.C_INCOMPATIBLE)], fixture_pairs=90,
                                truncated=True) == C.N_LIMIT
    # a sibling in ANOTHER period can never be protection, so its missing
    # prose does not turn a determination into a block
    assert C.attempt_conclusion(
        [_sib(C.C_INCOMPATIBLE), _sib(C.C_METADATA, rank=2)],
        fixture_pairs=2) == C.N_INCOMPATIBLE
    assert C.attempt_conclusion([_sib(C.C_ADMITTED)]) == C.N_ADMITTED


def test_every_supplier_and_discovery_refusal_has_a_category():
    from sportsassets import bettor_funded_hedge_supply as HS
    from sportsassets import bettor_funded_pair_cycle as PC

    # an unpriced sibling is split by WHY its quote failed
    assert PO.sibling_category(HS.R_CANDIDATE_NOT_PRICED,
                               quote_refusal=PO.R_READ_BUDGET) \
        == PO.C_READ_BUDGET
    assert PO.sibling_category(HS.R_CANDIDATE_NOT_PRICED,
                               quote_refusal=PO.R_PASS_DEADLINE) \
        == PO.C_READ_BUDGET
    assert PO.sibling_category(HS.R_CANDIDATE_NOT_PRICED,
                               quote_refusal="NOT_IN_THE_CAPTURED_BOOK") \
        == PO.C_UNPRICED
    for name in (PC.R_NOT_DISTINCT, PC.R_NOT_SETTLEMENT_COMPATIBLE,
                 HS.R_OT_NO_PROSE, HS.R_OT_PROSE_SILENT,
                 HS.R_OT_PROSE_STATES_BOTH, HS.R_OVERTIME_NOT_CAPTURED,
                 HS.R_ORIENTATION_NOT_ESTABLISHED, HS.R_KIND_NOT_DERIVABLE,
                 PO.X_NOT_A_GRADED_VARIABLE, PO.X_ORIENTATION):
        assert PO.sibling_category(name) != PO.C_UNNAMED, name
    assert PO.sibling_category("A_NAME_NO_ONE_DECLARED") == PO.C_UNNAMED
    assert PO.key_differs_on(["f", "FULL", "MARGIN", "INCL"],
                             ["f", "FULL", "MARGIN", "EXCL"]) == ["overtime"]


async def _ledger_detail(conn, pass_id):
    row = dict(await conn.fetchrow(
        "SELECT * FROM bettor_pair_observation_attempts WHERE pass_id=$1",
        pass_id))
    d = row["detail"]
    return row, (d if isinstance(d, dict) else json.loads(d))


async def test_the_ledger_names_each_sibling_its_stage_and_category():
    """THE SIX REFUSED ATTEMPTS COULD NOT BE TRACED because only counts were
    kept. Each sibling is now recorded by contract, with the stage that
    stopped it, the refusal and the category; the attempt with a conclusion.
    Here the spread sibling has no displayed price, and a prop on the same
    fixture is screened out before any read."""
    prop = "astatc-mlb-bos-nyy-2026-10-05-hits"
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        await _row(conn, event=HW.EVENT, slug=prop, st="baseball_player_hits",
                   abbr="bos", intent=LONG)
        got = await PO.observation_pass(
            conn, candidates=[], catalogue=[{"us_market_slug": HW.HELD,
                                             "side": LONG,
                                             "fixture": HW.EVENT}],
            quoter=HW._quoter({HW.HELD: (0.55, 500)}),
            prose_reader=HW._prose_reader(), settlement_reader=_reader,
            now=time.time())
        a = got["observed"][0]
        assert a["outcome"] == "NOTHING_ADMITTED", a
        assert a["conclusion"] == PO.N_UNPRICED, a
        assert got["conclusions"] == {PO.N_UNPRICED: 1}
        row, d = await _ledger_detail(conn, got["pass_id"])
        assert d["conclusion"] == PO.N_UNPRICED
        assert d["budget_limited"] is False
        assert d["held"]["us_market_slug"] == HW.HELD
        assert len(d["held"]["grading_key"]) == 4
        by = {(s["market_slug"], s["side"]): s for s in d["siblings"]}
        spread = [s for k, s in by.items() if k[0] == HW.SIB]
        assert spread and all(s["stage"] == "QUOTE" for s in spread), by
        assert all(s["category"] == PO.C_UNPRICED for s in spread)
        assert all(s["quote_refusal"] == "NOT_IN_THE_CAPTURED_BOOK"
                   for s in spread)
        props = [s for k, s in by.items() if k[0] == prop]
        assert props and props[0]["stage"] == "SCREEN"
        assert props[0]["category"] == PO.C_FILTERED
        assert d["siblings_total"] == len(d["siblings"])
        assert sum(d["sibling_categories"].values()) == d["siblings_total"]


async def test_a_budget_limited_attempt_concludes_nothing_about_the_fixture():
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        got = await PO.observation_pass(
            conn, candidates=[(HW.HELD, LONG)], quoter=HW._quoter(PRICES),
            prose_reader=HW._prose_reader(), settlement_reader=_reader,
            now=time.time(), book_budget=1)
        a = got["observed"][0]
        assert a["conclusion"] == PO.N_BUDGET, a
        _, d = await _ledger_detail(conn, got["pass_id"])
        assert d["budget_limited"] is True
        assert any(s["category"] == PO.C_READ_BUDGET for s in d["siblings"])


async def test_an_admitted_sibling_is_recorded_as_admitted():
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        got = await PO.observe_candidate(
            conn, us_market_slug=HW.HELD, side=LONG,
            quoter=HW._quoter(PRICES), prose_reader=HW._prose_reader(),
            now=time.time())
        assert got["conclusion"] == PO.N_ADMITTED, got
        adm = [s for s in got["siblings"] if s["stage"] == "ADMITTED"]
        assert adm and adm[0]["market_slug"] == HW.SIB


# ═════════════════════════════════════════════════════════════════════
# 5 · ROTATION SURVIVES A RESTART: THE LEDGER IS THE MEMORY
# ═════════════════════════════════════════════════════════════════════

def _restart():
    """What a new process has: no in-memory attempts, pass counter at 0."""
    PO._ATTEMPTED.clear()
    PO._PASSES[0] = 0


async def test_a_restart_does_not_reselect_the_fixtures_just_refused():
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        await _sweep_ran(conn)
        first = await PO.observation_pass(
            conn, candidates=[], catalogue=[{"us_market_slug": HW.HELD,
                                             "side": LONG,
                                             "fixture": HW.EVENT}],
            quoter=HW._quoter({}), prose_reader=HW._prose_reader(),
            settlement_reader=_reader, now=time.time())
        assert first["observed"][0]["outcome"] == "REFUSED"
        _restart()
        cat = await PO.catalogue_candidates(conn, now=time.time())
        assert cat["attempt_memory"]["source"] == "ATTEMPT_LEDGER"
        assert HW.EVENT not in {c["fixture"] for c in cat["candidates"]}
        assert cat["excluded_fixtures"].get(PO.X_ATTEMPTED_RECENTLY) == 1
        # and the same identity offered by the entry lane is not re-attempted
        again = await PO.observation_pass(
            conn, candidates=[(HW.HELD, LONG)], quoter=HW._quoter({}),
            prose_reader=HW._prose_reader(), settlement_reader=_reader,
            now=time.time())
        assert again["attempted"] == 0
        assert again["not_attempted"][PO.X_ATTEMPTED_RECENTLY] == 1
        # after the retry interval it is eligible again
        later = time.time() + PO.ATTEMPT_RETRY_S + 60
        _restart()
        cat2 = await PO.catalogue_candidates(conn, now=later)
        assert cat2["excluded_fixtures"].get(PO.X_ATTEMPTED_RECENTLY, 0) == 0


async def test_a_budget_limited_fixture_queues_behind_never_attempted_ones():
    other = "mlb-det-cle-2026-10-05"
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        await _sweep_ran(conn)
        # a second admissible-shaped fixture: a moneyline and a spread
        await _row(conn, event=other, slug="aec-mlb-det-cle-2026-10-05",
                   st="baseball_team_full_game_winner", abbr="det",
                   intent=LONG, title="Detroit Tigers vs. Cleveland Guardians")
        await _row(conn, event=other,
                   slug="asc-mlb-det-cle-2026-10-05-neg-1pt5",
                   st="baseball_team_full_game_spread", abbr="cle",
                   intent=LONG, signed="-1.5",
                   title="Detroit Tigers vs. Cleveland Guardians")
        before = await PO.catalogue_candidates(conn, now=time.time())
        fx = [c["fixture"] for c in before["candidates"]]
        assert HW.EVENT in fx and other in fx, before
        # HW's fixture is attempted but budget-limited: not a refusal
        await PO.observation_pass(
            conn, candidates=[(HW.HELD, LONG)], quoter=HW._quoter(PRICES),
            prose_reader=HW._prose_reader(), settlement_reader=_reader,
            now=time.time(), book_budget=1)
        _restart()
        after = await PO.catalogue_candidates(conn, now=time.time())
        order = [c["fixture"] for c in after["candidates"]]
        assert HW.EVENT in order, after
        assert order.index(other) < order.index(HW.EVENT), order
        assert after["excluded_fixtures"].get(PO.X_ATTEMPTED_RECENTLY, 0) == 0


# ═════════════════════════════════════════════════════════════════════
# 6 · A PER-OUTCOME YES/NO CONTRACT IS NOT A MONEYLINE ON ITS NO SIDE
# ═════════════════════════════════════════════════════════════════════

async def test_a_held_side_sharing_its_orientation_with_the_other_side_is_refused():
    """THE PRODUCTION ROW SHAPE (research run 287): atc-...-eri states
    team_abbr=eri on its YES and its NO row. The NO side ("Eritrea does not
    win") was built as a moneyline backing Eritrea -- the YES side's payout.
    The held path now refuses it, as the sibling path always did."""
    from sportsassets import bettor_funded_hedge_supply as HS

    ev = "afcq-eri-rsa-2026-10-05"
    slug = "atc-afcq-eri-rsa-2026-10-05-eri"
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        await conn.execute("DELETE FROM us_premap WHERE event_slug=$1", ev)
        for intent, sn in ((LONG, "yes"), (SHORT, "no")):
            await _row(conn, event=ev, slug=slug,
                       st="soccer_team_full_time_winner", abbr="eri",
                       intent=intent, side=sn, title="Eritrea vs. South Africa")
        try:
            for intent in (SHORT, LONG):
                got = await HS.held_leg_for(
                    conn, position={"intent_id": "t", "us_market_slug": slug,
                                    "order_intent": intent,
                                    "limit_price": 0.4, "filled_qty": 1,
                                    "residual_qty": 1},
                    prose_reader=HW._prose_reader(
                        "Resolves on the result after 90 minutes plus "
                        "stoppage time. " + HW.CANCELLATION_CLAUSE),
                    now=time.time())
                assert got["ok"] is False, (intent, got)
                assert got["refusal"] == HS.R_BOTH_SIDES_CLAIM_ONE_ORIENTATION
                assert got["leg"] is None
            # the two-row moneyline with distinct codes still builds
            ok = await HS.held_leg_for(
                conn, position={"intent_id": "t", "us_market_slug": HW.HELD,
                                "order_intent": LONG, "limit_price": 0.55,
                                "filled_qty": 1, "residual_qty": 1},
                prose_reader=HW._prose_reader(), now=time.time())
            assert ok["ok"] is True, ok
        finally:
            await conn.execute("DELETE FROM us_premap WHERE event_slug=$1", ev)


# ═════════════════════════════════════════════════════════════════════
# 7 · THE STRUCTURAL CENSUS: WHAT THE CATALOGUE ALONE ESTABLISHES
# ═════════════════════════════════════════════════════════════════════

def _cat(slug, st, abbrs, signed=(None, None), ev="x-a-b-2026-10-05"):
    return [{"market_slug": slug, "sports_type": st, "team_abbr": a,
             "intent": i, "signed": sg, "side_norm": "yes",
             "event_slug": ev}
            for a, i, sg in zip(abbrs, (LONG, SHORT), signed)]


def test_the_census_reads_the_production_row_shapes():
    # soccer: three per-outcome yes/no contracts, each naming ONE team on
    # both rows (the draw row has no code and never passes the screen)
    soccer = (_cat("atc-e-eri", "soccer_team_full_time_winner", ("eri", "eri"))
              + _cat("atc-e-rsa", "soccer_team_full_time_winner", ("rsa", "rsa")))
    got = PO.structural_verdict(soccer)
    assert got["verdict"] == PO.S_NO_USABLE_SHAPE, got
    assert got["unsupported_shape_contracts"] == 2
    # MLB: the two-row winner and a spread share FULL_GAME / MARGIN
    mlb = (_cat("aec-m", "baseball_team_full_game_winner", ("bos", "nyy"))
           + _cat("asc-m-neg-1pt5", "baseball_team_full_game_spread",
                  ("bos", "nyy"), ("-1.5", "+1.5")))
    got = PO.structural_verdict(mlb)
    assert got["verdict"] == PO.S_PAIRABLE, got
    assert got["shared_keys"] == {"FULL_GAME/MARGIN": 2}
    # one contract
    assert PO.structural_verdict(mlb[:2])["verdict"] == PO.S_ONE_CONTRACT


async def test_a_structurally_pairable_fixture_is_offered_before_one_that_cannot_pair():
    ev = "afcq-eri-rsa-2026-10-05"
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        await _sweep_ran(conn)
        await conn.execute("DELETE FROM us_premap WHERE event_slug=$1", ev)
        try:
            # a soccer fixture starting SOONER than the HW fixture
            for suf in ("eri", "rsa"):
                for intent, sn in ((LONG, "yes"), (SHORT, "no")):
                    await _row(conn, event=ev, slug="atc-%s-%s" % (ev, suf),
                               st="soccer_team_full_time_winner", abbr=suf,
                               intent=intent, side=sn, starts="20 minutes",
                               title="Eritrea vs. South Africa")
            got = await PO.catalogue_candidates(conn, now=time.time())
            order = [c["fixture"] for c in got["candidates"]]
            assert ev in order and HW.EVENT in order, got
            assert order.index(HW.EVENT) < order.index(ev), order
            census = got["structural_census"]
            assert census[PO.S_PAIRABLE]["fixtures"] >= 1
            assert census[PO.S_NO_USABLE_SHAPE]["by_family"].get("soccer") == 1
        finally:
            await conn.execute("DELETE FROM us_premap WHERE event_slug=$1", ev)

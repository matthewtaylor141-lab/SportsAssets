"""RE-ESTABLISHING A LEGACY POSITION'S VENUE IDENTITY, AND REFUSING TO.

WHAT PRODUCTION DID, VERBATIM. The acceptance position was opened before
migration 119 put the venue identity on the row, so it carries NULL and the
settlement consumer had to look for a valuation that NAMES the outcome the
position holds. It refused:

    THE_ONLY_AVAILABLE_IDENTITY_DESCRIBES_A_DIFFERENT_OUTCOME
    "2 venue identities are recorded for this condition and none of them
     names the outcome this position holds ('Arizona Diamondbacks'). They
     describe ['Colorado Rockies', 'None']"

That refusal happens BEFORE the venue is asked anything, so venue
settlement was untested for that position. `bettor_legacy_identity_repair`
re-derives the binding from `premap.resolve` -- never from a valuation --
and these tests hold it to both halves of that contract: it must bind the
held side when the resolver agrees, and it must REFUSE, writing nothing,
in each of the ways the binding could be wrong. The refusal tests matter
more than the happy path: a repair that writes the wrong slug settles the
position against the opposite team.
"""

from __future__ import annotations

import json
import re
import os
import time

import pytest

from sportsassets import bettor_entry_settlement as S
from sportsassets import bettor_legacy_identity_repair as REPAIR

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

CONDITION = "c-legacy-az-col"
SLUG = "az-col-legacy-2026-09-24"
#: The VENUE's own contract for the side the position holds. Distinct
#: from the sibling's, so discrimination is observable.
US_SLUG_HELD = "aec-mlb-az-col-2026-09-24-az"
US_SLUG_SIBLING = "aec-mlb-az-col-2026-09-24-col"
HELD = "Arizona Diamondbacks"
SIBLING = "Colorado Rockies"
PID = "LEGACY_REPAIR_TEST:POS:-1"
EXPERIMENT = "LEGACY_REPAIR_TEST_EXPERIMENT"
POLICY = "LEGACY_REPAIR_TEST_POLICY"
#: The acceptance position's own provenance, which the repair must leave
#: exactly as it is. Synthetic, modelled, unfunded.
PROVENANCE = "ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY"

SEEDS = ("103_external_valuations.sql",
         "105_external_valuations_one_per_observation.sql",
         "116_one_entry_position_per_exposure.sql",
         "117_entry_lane_evidence_and_calibration.sql",
         "118_outcome_join_provenance_and_audit.sql",
         "119_positions_carry_their_venue_identity.sql",
         "120_positions_record_their_venue.sql",
         "121_legacy_position_identity_repair.sql")

POS_SQL = """
    INSERT INTO rn1x_positions (position_id, experiment_id, policy,
        source_trade_id, source_account, condition_id, outcome_index,
        entry_kind, entry_kind_why, seed_qty, seed_price, seed_basis_usd,
        source_ts, detected_ts, decision_ts, decision_basis, provenance,
        venue_market_slug, venue_buy_intent, venue_ladder_side,
        payout_event, venue)
    VALUES ($1,$2,$3,NULL,'legacy',$4,$5,'NEW','legacy fixture',
            100.0,0.60,60.0,now(),now(),now(),'RUNTIME_WALL_CLOCK',$6,
            $7,$8,$9,$10,$11)
    ON CONFLICT (position_id) DO NOTHING
"""

#: Everything a repair must never touch, read back as one row so the
#: assertion is an equality on the whole set rather than a spot check.
UNTOUCHED_SQL = """
    SELECT provenance, entry_kind, source_trade_id, source_account,
           seed_qty::text AS seed_qty, seed_price::text AS seed_price,
           seed_basis_usd::text AS seed_basis_usd, condition_id,
           outcome_index
      FROM rn1x_positions WHERE position_id = $1
"""


async def _seed(conn, *, identity=None):
    for name in SEEDS:
        await conn.execute(open("migrations/%s" % name).read())
    await conn.execute(
        "INSERT INTO markets (condition_id, title, event_title, slug, "
        "sport, closed, resolved) VALUES ($1,$2,$3,$4,'MLB',false,false) "
        "ON CONFLICT (condition_id) DO UPDATE SET title = EXCLUDED.title, "
        "event_title = EXCLUDED.event_title, slug = EXCLUDED.slug",
        CONDITION, "Will Arizona Diamondbacks beat Colorado Rockies?",
        "Arizona Diamondbacks vs. Colorado Rockies", SLUG)
    # THE GLOBAL CATALOGUE. The held outcome is read from HERE at the
    # position's own index -- not from any valuation -- which is the whole
    # point of the repair.
    await conn.execute(
        "INSERT INTO market_tokens (token_id, condition_id, outcome, "
        "outcome_index) VALUES ($1,$2,$3,0),($4,$2,$5,1) "
        "ON CONFLICT (token_id) DO UPDATE SET outcome = EXCLUDED.outcome, "
        "outcome_index = EXCLUDED.outcome_index",
        "tok-legacy-az", CONDITION, HELD, "tok-legacy-col", SIBLING)
    await conn.execute(
        "INSERT INTO rn1x_experiments (experiment_id, code_version, "
        "seed_rule, policy_register, execution_basis, notes) "
        "VALUES ($1,'V','{}'::jsonb,'{}'::jsonb,'MODELLED',$2) "
        "ON CONFLICT (experiment_id) DO NOTHING", EXPERIMENT,
        "legacy identity repair fixture")
    await conn.execute("DELETE FROM rn1x_positions WHERE position_id = $1",
                       PID)
    i = identity or {}
    await conn.execute(POS_SQL, PID, EXPERIMENT, POLICY, CONDITION, 0,
                       PROVENANCE, i.get("venue_market_slug"),
                       i.get("venue_buy_intent"), i.get("venue_ladder_side"),
                       i.get("payout_event"), i.get("venue"))


async def _cleanup(conn):
    for sql, args in (
            ("DELETE FROM rn1x_outcomes WHERE position_id = $1", (PID,)),
            ("DELETE FROM rn1x_positions WHERE position_id = $1", (PID,)),
            ("DELETE FROM external_valuations WHERE condition_id = $1",
             (CONDITION,)),
            ("DELETE FROM markets WHERE condition_id = $1", (CONDITION,))):
        try:
            await conn.execute(sql, *args)
        except Exception:                                      # noqa: BLE001
            pass


def _resolver(monkeypatch, answers):
    """Stand in for the venue catalogue at premap's own boundary.

    `answers` maps the outcome ASKED FOR to what the resolver returns, so
    a test can make the two sides agree, disagree, or be indistinguishable
    without inventing `us_premap` rows for each case.
    """
    from sportsassets.workers import premap as _pm

    async def fake(conn_, title, event_title, outcome, slug, **kw):
        return answers.get(str(outcome))

    monkeypatch.setattr(_pm, "resolve", fake)


def _long(slug, side):
    return {"market_slug": slug, "intent": "ORDER_INTENT_BUY_LONG",
            "outcome": side, "title": "Will %s win?" % side,
            "matched_by": "premap", "score": 1.0}


# ── what the repair refuses to do, before what it does ───────────────

def test_it_never_reads_valuations_and_says_so():
    d = REPAIR.describe()
    assert d["reads_external_valuations"] is False
    assert d["fills_only_nulls"] is True
    assert d["reseeds"] is False
    for col in ("provenance", "entry_kind", "seed_qty", "seed_price",
                "seed_basis_usd"):
        assert col in d["never_touches"]


def test_the_module_does_not_reference_the_valuation_table_at_all():
    """THE STRONGEST AVAILABLE GUARANTEE that no other side's row is
    borrowed is that the source of the wrong answer is never queried. It
    is checked against the source text, not against a promise."""
    import inspect

    # EVERY SQL STATEMENT THE MODULE HOLDS, and none of them may name the
    # table whose contents produced the wrong answer. Checked against the
    # statements rather than the whole source, because the docstring says
    # the word on purpose -- explaining what is not queried is not
    # querying it.
    sql = [v for k, v in vars(REPAIR).items()
           if k.endswith("_SQL") and isinstance(v, str)]
    assert sql, "the module should hold its statements as named constants"
    for stmt in sql:
        assert "external_valuations" not in stmt.lower(), stmt
    # AND NO OTHER TABLE IS REACHED BY AN INLINE STATEMENT EITHER: the
    # only reads are the named constants above.
    body = inspect.getsource(REPAIR.repair)
    for verb in ("fetchrow(", "fetch(", "fetchval(", "execute("):
        for m in re.finditer(re.escape(verb) + r"\s*([A-Za-z_\"\']+)", body):
            assert not m.group(1).startswith(("\"", "'")), \
                "an inline SQL string bypasses the named statements: %s" % m.group(0)


def test_it_does_not_derive_the_held_side_from_the_intent():
    """The cross-check an earlier draft of this module had -- and which
    would have refused every correct repair of a short-side holding on the
    `aec-` family, where both sides share one identifier and the intent is
    the only side selector."""
    d = REPAIR.describe()
    assert "outcome_index" in d["does_not_check"]["what"]
    assert "intent" in d["does_not_check"]["what"]
    assert "the_resolved_side_names_the_held_outcome" in d["cross_checks"]
    assert ("the_resolver_discriminates_the_sibling_side"
            in d["cross_checks"])


@pg
@pytest.mark.asyncio
async def test_it_binds_the_held_side_and_records_its_derivation(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        before = dict(await conn.fetchrow(UNTOUCHED_SQL, PID))
        _resolver(monkeypatch, {HELD: _long(US_SLUG_HELD, HELD),
                                SIBLING: _long(US_SLUG_SIBLING, SIBLING)})
        got = await REPAIR.repair(conn, PID, now=time.time())
        assert got["ok"] is True, got
        assert got["written"] is True, got
        assert got["refusal"] is None, got

        # THE OUTCOME CAME FROM THE CATALOGUE, at the position's own index.
        assert got["held_outcome"] == HELD
        assert got["held_token_id"] == "tok-legacy-az"
        assert got["identity"]["venue_market_slug"] == US_SLUG_HELD
        assert got["identity"]["payout_event"] == HELD
        assert got["identity"]["venue_ladder_side"] == "ASK"
        assert got["identity"]["venue"] == REPAIR.VENUE
        for name, c in got["cross_checks"].items():
            assert c["passed"] is True, (name, c)

        # AND THE ROW NOW CARRIES IT, with an auditable derivation.
        row = dict(await conn.fetchrow(
            "SELECT venue_market_slug, venue_buy_intent, venue_ladder_side, "
            "payout_event, venue, identity_repair::text AS ir "
            "FROM rn1x_positions WHERE position_id = $1", PID))
        assert row["venue_market_slug"] == US_SLUG_HELD
        assert row["payout_event"] == HELD
        assert row["venue"] == REPAIR.VENUE
        audit = json.loads(row["ir"])
        assert audit["version"] == REPAIR.VERSION
        assert audit["resolver_function"] == "workers.premap.resolve"
        assert audit["read_external_valuations"] is False
        assert audit["asked_for"] == HELD
        assert "market_tokens" in audit["asked_for_source"]
        # THE RECORD MUST BE RE-DERIVABLE FROM ITSELF, so every check it
        # claims to have run is in it with its own verdict.
        for name in REPAIR.describe()["cross_checks"]:
            assert audit["cross_checks"][name]["passed"] is True

        # AND NOTHING ELSE MOVED. Synthetic, modelled, unfunded, unreseeded.
        after = dict(await conn.fetchrow(UNTOUCHED_SQL, PID))
        assert after == before, (before, after)
        assert after["provenance"] == PROVENANCE
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_it_refuses_when_the_resolved_side_is_the_other_team(
        monkeypatch):
    """THE PRODUCTION FAILURE CLASS ITSELF. If the resolver answers with
    Colorado's side for a position holding Arizona, binding it would
    settle the position against the opposite team. Nothing is written."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        _resolver(monkeypatch, {HELD: _long(US_SLUG_SIBLING, SIBLING)})
        got = await REPAIR.repair(conn, PID, now=time.time())
        assert got["ok"] is False, got
        assert got["written"] is False
        assert got["refusal"] == REPAIR.R_SIDE_NOT_THE_OUTCOME, got
        assert SIBLING in got["why"] and HELD in got["why"]
        chk = got["cross_checks"]["the_resolved_side_names_the_held_outcome"]
        assert chk["passed"] is False
        row = await conn.fetchrow("SELECT venue_market_slug, payout_event, "
                                  "identity_repair FROM rn1x_positions "
                                  "WHERE position_id = $1", PID)
        assert row["venue_market_slug"] is None
        assert row["payout_event"] is None
        assert row["identity_repair"] is None
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_it_refuses_a_binding_that_cannot_tell_the_sides_apart(
        monkeypatch):
    """If BOTH outcomes resolve to the same slug with the same intent, the
    identity does not identify which side is held -- so it is not an
    identity. This is the check that speaks to the production refusal."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        both = dict(_long(US_SLUG_HELD, HELD))
        _resolver(monkeypatch, {HELD: both,
                                SIBLING: dict(both, outcome=SIBLING)})
        got = await REPAIR.repair(conn, PID, now=time.time())
        assert got["ok"] is False, got
        assert got["refusal"] == REPAIR.R_SIDES_NOT_DISCRIMINATED, got
        chk = got["cross_checks"][
            "the_resolver_discriminates_the_sibling_side"]
        assert chk["passed"] is False
        assert chk["sibling_market_slug"] == US_SLUG_HELD
        row = await conn.fetchrow("SELECT venue_market_slug FROM "
                                  "rn1x_positions WHERE position_id = $1",
                                  PID)
        assert row["venue_market_slug"] is None
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_shared_identifier_with_opposite_intents_discriminates(
        monkeypatch):
    """On the `aec-` family both sides carry the SAME identifier and the
    intent is the only side selector, so an identical slug with the
    OPPOSITE intent is a discriminating binding and must be accepted --
    including when the held side is the venue's SHORT leg, which the
    contract still PAYS ON."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        shared = "aec-mlb-az-col-2026-09-24"
        _resolver(monkeypatch, {
            HELD: {"market_slug": shared,
                   "intent": "ORDER_INTENT_BUY_SHORT", "outcome": HELD,
                   "matched_by": "premap", "score": 1.0},
            SIBLING: _long(shared, SIBLING)})
        got = await REPAIR.repair(conn, PID, now=time.time())
        assert got["ok"] is True, got
        assert got["written"] is True, got
        # THE PAYOUT DID NOT INVERT. A short intent selects the BID ladder
        # for acquisition cost; the contract still pays on Arizona.
        assert got["identity"]["payout_event"] == HELD
        assert got["identity"]["venue_ladder_side"] == "BID"
        assert got["identity"]["venue_buy_intent"] == "ORDER_INTENT_BUY_SHORT"
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_it_will_not_revise_an_identity_the_writer_recorded(
        monkeypatch):
    """A repair fills a gap. A position whose opening writer recorded its
    identity is not a gap, and overwriting it would be an edit."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn, identity={
            "venue_market_slug": US_SLUG_HELD,
            "venue_buy_intent": "ORDER_INTENT_BUY_LONG",
            "venue_ladder_side": "ASK", "payout_event": HELD,
            "venue": "PMUS"})
        _resolver(monkeypatch, {HELD: _long(US_SLUG_SIBLING, SIBLING)})
        got = await REPAIR.repair(conn, PID, now=time.time())
        assert got["ok"] is False
        assert got["refusal"] == REPAIR.R_ALREADY, got
        assert got["identity"]["venue_market_slug"] == US_SLUG_HELD
        row = await conn.fetchrow("SELECT venue_market_slug, identity_repair "
                                  "FROM rn1x_positions WHERE position_id = $1",
                                  PID)
        assert row["venue_market_slug"] == US_SLUG_HELD
        assert row["identity_repair"] is None
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_dry_run_resolves_and_cross_checks_but_writes_nothing(
        monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        _resolver(monkeypatch, {HELD: _long(US_SLUG_HELD, HELD),
                                SIBLING: _long(US_SLUG_SIBLING, SIBLING)})
        got = await REPAIR.repair(conn, PID, now=time.time(), write=False)
        assert got["ok"] is True, got
        assert got["written"] is False
        assert got["identity"]["venue_market_slug"] == US_SLUG_HELD
        row = await conn.fetchrow("SELECT venue_market_slug FROM "
                                  "rn1x_positions WHERE position_id = $1",
                                  PID)
        assert row["venue_market_slug"] is None
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_it_refuses_when_the_venue_lists_no_contract(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        _resolver(monkeypatch, {})
        got = await REPAIR.repair(conn, PID, now=time.time())
        assert got["ok"] is False
        assert got["refusal"] == REPAIR.R_NO_PREMAP, got
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_repair_is_what_lets_the_consumer_reach_the_venue(
        monkeypatch):
    """THE POINT OF THE WHOLE EXERCISE. Before the repair the settlement
    consumer refuses on IDENTITY and never asks the venue anything, so
    venue settlement is untested. After it, the consumer gets as far as
    the venue's own resolution -- and whatever the venue then says is a
    real answer about this position rather than a local dead end.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        asked = []

        # THE ONE TRANSPORT BOUNDARY, and the consumer calls it
        # SYNCHRONOUSLY -- it wraps its default blocking reader in
        # `to_thread` itself, so a fixture is a plain function.
        def read_resolution(slug):
            asked.append(slug)
            return {"ok": False, "why": "the venue has not resolved it yet"}

        # BEFORE: refused on identity, and the venue was never asked.
        pre = await S.settle_open_positions(
            conn, experiment_id=EXPERIMENT, policy=POLICY, now=time.time(),
            read_resolution=read_resolution)
        mine = [r for r in pre["results"] if r.get("position_id") == PID]
        assert mine, pre
        assert mine[0]["status"] in (S.S_NO_SLUG, S.S_NO_SIDE,
                                     S.S_WRONG_SIDE, S.S_NO_HELD_EVENT), \
            mine[0]
        assert asked == [], "the venue must not be asked without an identity"

        # THE REPAIR.
        _resolver(monkeypatch, {HELD: _long(US_SLUG_HELD, HELD),
                                SIBLING: _long(US_SLUG_SIBLING, SIBLING)})
        rep = await REPAIR.repair(conn, PID, now=time.time())
        assert rep["written"] is True, rep

        # AFTER: the consumer reaches the venue and reports ITS answer.
        post = await S.settle_open_positions(
            conn, experiment_id=EXPERIMENT, policy=POLICY, now=time.time(),
            read_resolution=read_resolution)
        mine = [r for r in post["results"] if r.get("position_id") == PID]
        assert mine, post
        assert mine[0]["status"] not in (S.S_NO_SLUG, S.S_NO_SIDE,
                                         S.S_WRONG_SIDE, S.S_NO_HELD_EVENT), \
            mine[0]
        assert asked == [US_SLUG_HELD], asked
    finally:
        await _cleanup(conn)
        await conn.close()

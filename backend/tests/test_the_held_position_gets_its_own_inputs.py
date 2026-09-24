"""THE MISSING INPUT CONNECTION, for a position we already hold.

Independent inspection of production found the remaining operational
blocker: every challenger decision on the one live position is a BLIND
hold. `ev_basis` reads `EV_HOLD_NOT_IDENTIFIED`, `input_available` is
false, `input_freshness` is `{}` and `venue_translation` says
VENUE_POSITION_MODEL_NOT_ESTABLISHED with `venue null`.

THE CAUSE IS A SCHEDULE, NOT A MISSING FEATURE.

`challenger_inputs_for` reads the freshest ELIGIBLE row in
`external_valuations`. That table is written by the ENTRY lane on
`ext_pinnacle_loop.CYCLE_S = 900 s`, and the applicable odds rule is
`bettor_pinnacle_devig.MAX_QUOTE_AGE_S = 30 s`. A management decision
that reads it is refused as stale unless it happens to run inside a 30 s
window of a 900 s cadence -- and only for a position whose market was in
that cycle's candidate set. The venue slug and the buy intent came off
the SAME row, so a position without one had no venue identity either:
that is why `venue` is null on every blind row. Two symptoms, one cause.

So the managed inputs are PULLED for the decision, through the same
adapters, and the chain reports which link failed instead of collapsing
six different causes into one string.

WHAT THESE TESTS COVER, AND WHAT THEY DO NOT. They cover the chain's
ORDER, each link's refusal WITH its identifiers, the budget, the coverage
statement, and that a priced decision no longer requires an
`external_valuations` row at all. The provider payload is injected and
the venue catalogue resolver is injected -- `workers.premap.resolve`
reads `us_premap`, whose key logic has its own tests and would have to be
re-implemented in a fixture to be exercised here. `bettor_venue_mapping`
is NOT injected: the fixture-to-event match is the real matcher.
"""

import asyncio
import os

import pytest

from sportsassets.workers import rn1x_shadow as W

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

_EXP = "RN1X_SHADOW_CHALLENGER_HOLD_RANKED_V1"
_POL = "SHADOW_CHALLENGER_HOLD_RANKED_V1"
_COND = "0xtest_pull"
_SLUG = "aec-mlb-chc-mia-2026-09-24-cubs"
_T0 = 1_800_000_000.0

#: the venue's ladder: a close of a held LONG consumes the BIDS
_BOOK = {"bids": [{"px": {"value": "0.7200"}, "qty": "80"}],
         "offers": [{"px": {"value": "0.7400"}, "qty": "900"}]}


def _event(*, home="Chicago Cubs", away="Miami Marlins", observed_at,
           cubs=1.60, fish=2.50, event_id="ev-1"):
    """One provider event, shaped exactly as the odds payload arrives."""
    return {"id": event_id, "home_team": home, "away_team": away,
            "commence_time": "2026-09-24T23:05:00Z",
            "bookmakers": [
                {"key": "pinnacle", "last_update": observed_at,
                 "markets": [{"key": "h2h", "last_update": observed_at,
                              "outcomes": [{"name": home, "price": cubs},
                                           {"name": away, "price": fish}]}]},
                {"key": "smarkets", "last_update": observed_at,
                 "markets": [{"key": "h2h",
                              "outcomes": [{"name": home, "price": cubs},
                                           {"name": away, "price": fish}]}]},
            ]}


def _payload(events, *, received_at):
    return {"ok": True, "status": 200, "events": events,
            "received_at": received_at, "credits_remaining": 6_000_000}


def _odds(events, *, received_at=_T0, calls=3):
    """A ManagedOdds whose provider is this payload, counted like the real
    one so the budget assertions are about the real budget."""
    async def fetch(sport_key):
        return _payload(events, received_at=received_at)
    return W.ManagedOdds(fetch=fetch, calls=calls)


async def _resolver_ok(conn, *, market_row, priced_outcome):
    return {"ok": True, "us_market_slug": _SLUG,
            "intent": "ORDER_INTENT_BUY_LONG",
            "payout_event": priced_outcome,
            "payout_event_basis": "VENUE_CATALOGUE_SIDE_EXPANSION",
            "probability_event": priced_outcome,
            "matched_side_norm": "cubs", "ladder_side": "BID",
            "resolver": "stub.for.this.test",
            "resolver_asked_for": priced_outcome}


async def _resolver_no_contract(conn, *, market_row, priced_outcome):
    return {"ok": False, "refusal": "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP",
            "why": ("the venue's own catalogue carries no contract for "
                    "this fixture and outcome"),
            "resolver": "stub.for.this.test",
            "global_slug": market_row.get("slug")}


async def _resolver_other_side(conn, *, market_row, priced_outcome):
    return {"ok": True, "us_market_slug": _SLUG,
            "intent": "ORDER_INTENT_BUY_LONG",
            "payout_event": "Miami Marlins",       # NOT what we hold
            "payout_event_basis": "VENUE_CATALOGUE_SIDE_EXPANSION",
            "matched_side_norm": "fish", "ladder_side": "BID",
            "resolver": "stub.for.this.test"}


# ── the vocabularies, inverted rather than re-written ────────────────

def test_the_sport_label_maps_to_the_providers_family():
    assert W.family_for_label("MLB") == "baseball"
    assert W.family_for_label("Soccer") == "soccer"
    assert W.family_for_label("mlb") == "baseball"


def test_an_uncovered_sport_is_none_not_a_guess():
    """The coverage statement has to be possible. NBA and NHL are absent
    from the provider set BECAUSE the book does not quote them on this
    plan, and a guess here would turn that into a mapping bug."""
    assert W.family_for_label("NBA") is None
    assert W.family_for_label("NHL") is None
    assert W.family_for_label(None) is None


# ── the budget ──────────────────────────────────────────────────────

def test_one_request_serves_every_position_in_a_family():
    calls = []

    async def fetch(key):
        calls.append(key)
        return _payload([], received_at=_T0)

    o = W.ManagedOdds(fetch=fetch, calls=3)

    async def go():
        a = await o.for_family("baseball")
        b = await o.for_family("baseball")
        return a, b

    a, b = asyncio.run(go())
    assert calls == ["baseball_mlb"], calls
    assert o.calls_made == 1, "the second position must reuse the payload"
    assert a["payloads"] and b["payloads"]


def test_the_budget_refuses_by_name_rather_than_looking_like_no_fixture():
    async def fetch(key):
        return _payload([], received_at=_T0)

    o = W.ManagedOdds(fetch=fetch, calls=1)

    async def go():
        await o.for_family("baseball")          # spends the one call
        return await o.for_family("soccer")     # two keys, no budget

    got = asyncio.run(go())
    assert W.R_MANAGED_BUDGET in got["refusals"], got
    assert o.calls_made == 1


def test_no_credential_is_its_own_refusal():
    o = W.ManagedOdds(api_key=None)
    got = asyncio.run(o.for_family("baseball"))
    assert got["refusals"] == [W.R_MANAGED_NO_CREDENTIAL]
    assert o.calls_made == 0, "a missing credential costs nothing"


# ── the chain, link by link ─────────────────────────────────────────

async def _fixture(c, *, sport="MLB", title="Chicago Cubs vs Miami Marlins",
                   closed=False):
    for sql in ("DELETE FROM rn1x_fills", "DELETE FROM rn1x_orders",
                "DELETE FROM rn1x_decisions", "DELETE FROM rn1x_positions"):
        await c.execute(sql)
    await c.execute("DELETE FROM ingestion_state WHERE key LIKE 'rn1x%cursor'")
    for sql in ("DELETE FROM external_valuations WHERE condition_id = $1",
                "DELETE FROM market_tokens WHERE condition_id = $1",
                "DELETE FROM trades WHERE condition_id = $1",
                "DELETE FROM markets WHERE condition_id = $1"):
        await c.execute(sql, _COND)
    await c.execute(
        "INSERT INTO markets(condition_id,slug,sport,title,event_title,"
        "closed,resolved,updated_at) VALUES($1,$2,$3,$4,$5,$6,false,now())",
        _COND, "mlb-chc-mia-2026-09-24", sport, title, title, closed)
    await c.execute("INSERT INTO whales(id,address) VALUES(9,'0xwhale9') "
                    "ON CONFLICT (id) DO NOTHING")
    for i, n, t in ((0, "Chicago Cubs", _COND + "-t0"),
                    (1, "Miami Marlins", _COND + "-t1")):
        await c.execute(
            "INSERT INTO market_tokens(token_id,condition_id,outcome,"
            "outcome_index) VALUES($1,$2,$3,$4) "
            "ON CONFLICT (token_id) DO NOTHING", t, _COND, n, i)
    await c.execute(
        "INSERT INTO rn1x_experiments(experiment_id,code_version,seed_rule,"
        "policy_register,execution_basis) VALUES($1,'v','{}'::jsonb,"
        "'{}'::jsonb,'b') ON CONFLICT DO NOTHING", _EXP)


async def _position(c, trade_id=994001, qty=100, price=0.57):
    from sportsassets import bettor_rn1x_store as store
    pid = store.position_id(_EXP, _POL, trade_id)
    await c.execute(
        """INSERT INTO rn1x_positions(position_id,experiment_id,policy,
        source_trade_id,source_account,condition_id,outcome_index,
        entry_kind,entry_kind_why,seed_qty,seed_price,seed_basis_usd,
        source_ts,detected_ts,decision_ts,available_at,decision_basis,
        decision_lag_s) VALUES($1,$2,$3,$4,'RN1',$5,0,'NEW','seeded',
        $6::numeric,$7::numeric,$6::numeric*$7::numeric,to_timestamp($8),to_timestamp($8),to_timestamp($8),
        to_timestamp($8),'RUNTIME_WALL_CLOCK',0)
        ON CONFLICT (position_id) DO NOTHING""",
        pid, _EXP, _POL, trade_id, _COND, qty, price, _T0)
    return pid


def _chain(conn, **kw):
    return W.managed_inputs_for(conn, condition_id=_COND, outcome_index=0,
                                read_book=lambda slug: {"marketData": _BOOK},
                                **kw)


@pg
def test_the_whole_chain_completes_with_no_valuation_row_at_all():
    """THE POINT OF THE CHANGE. `external_valuations` is EMPTY for this
    condition and the decision is still priced, so a priced hold no longer
    waits for the entry lane's 900 s writer to coincide with a 30 s rule."""
    import asyncpg

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c)
            rows = await c.fetchval(
                "SELECT count(*) FROM external_valuations "
                "WHERE condition_id = $1", _COND)
            ci = await _chain(c, odds=_odds([_event(observed_at=_T0 - 8)]),
                              now=_T0, resolve_identity=_resolver_ok)
            return rows, ci
        finally:
            await c.close()

    rows, ci = asyncio.run(run())
    assert rows == 0, "the entry lane wrote nothing for this condition"
    assert ci["first_failing_link"] is None, ci["chain"]
    assert ci["available"] is True and ci["book_available"] is True
    assert ci["input_source"] == "PULLED_FOR_THIS_DECISION"
    names = [x["link"] for x in ci["chain"]]
    assert names == ["1_HELD_EXPOSURE", "2_VENUE_CONTRACT",
                     "3_PROVIDER_FIXTURE", "4_PROBABILITY",
                     "5_EXIT_LADDER"], names
    # link 1 · the held exposure, from the tokens
    assert ci["payout_event"] == "Chicago Cubs"
    # link 2 · the venue contract and the intent, NOT from a valuation row
    assert ci["us_market_slug"] == _SLUG
    assert ci["held_intent"] == "ORDER_INTENT_BUY_LONG"
    assert ci["venue"] == "PMUS"
    # link 3 · the fixture, matched by the REAL mapper
    fix = next(x for x in ci["chain"] if x["link"] == "3_PROVIDER_FIXTURE")
    assert fix["provider_event_id"] == "ev-1"
    assert fix["events_considered"] == 1
    # link 4 · a probability, aged 8 s against the odds engine's 30 s
    p = next(x for x in ci["chain"] if x["link"] == "4_PROBABILITY")
    assert p["age_s"] == pytest.approx(8.0, abs=0.01)
    assert p["max_age_s"] == 30.0
    assert 0.55 < ci["probability_row"]["probability"] < 0.65
    assert ci["probability_row"]["payout_event"] == "Chicago Cubs"
    assert ci["probability_row"]["eligibility"] == "ELIGIBLE"
    # link 5 · the executable exit, off the side a close consumes
    assert ci["bid"] == pytest.approx(0.72)
    assert ci["bid_size"] == pytest.approx(80.0)
    assert ci["complement_ask"] == pytest.approx(0.28)


@pg
def test_a_stale_quote_stops_at_link_four_with_its_age_and_bound():
    """The 30 s rule is not widened to manufacture activity. A quote the
    rule rejects fails the chain AND says by how much."""
    import asyncpg

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c)
            return await _chain(
                c, odds=_odds([_event(observed_at=_T0 - 200)]),
                now=_T0, resolve_identity=_resolver_ok)
        finally:
            await c.close()

    ci = asyncio.run(run())
    f = ci["first_failing_link"]
    assert f["link"] == "4_PROBABILITY", ci["chain"]
    assert f["refusal"] == "QUOTE_STALE"
    assert f["age_s"] == pytest.approx(200.0, abs=0.01)
    assert f["max_age_s"] == 30.0
    assert ci["available"] is False
    assert ci.get("probability_row") is None


@pg
def test_an_uncovered_sport_says_so_instead_of_blaming_the_mapping():
    """'This exposure has no provider coverage' is a different finding
    from 'the fixture did not map', and only one of them is about us."""
    import asyncpg

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c, sport="NHL")
            return await _chain(c, odds=_odds([]), now=_T0,
                                resolve_identity=_resolver_ok)
        finally:
            await c.close()

    ci = asyncio.run(run())
    f = ci["first_failing_link"]
    assert f["link"] == "3_PROVIDER_FIXTURE"
    assert f["refusal"] == W.R_MANAGED_FAMILY
    assert f["sport_label"] == "NHL"
    assert "baseball" in f["provider_families"]
    assert "NO PROVIDER COVERAGE" in f["why"]


@pg
def test_a_fixture_absent_from_the_payload_is_not_the_same_refusal():
    import asyncpg

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c)
            other = _event(home="Houston Astros", away="Oakland Athletics",
                           observed_at=_T0 - 5, event_id="ev-other")
            return await _chain(c, odds=_odds([other]), now=_T0,
                                resolve_identity=_resolver_ok)
        finally:
            await c.close()

    ci = asyncio.run(run())
    f = ci["first_failing_link"]
    assert f["link"] == "3_PROVIDER_FIXTURE"
    assert f["refusal"] == W.R_MANAGED_FIXTURE
    assert f["events_considered"] == 1
    assert "map_event" in f["why"]


@pg
def test_no_venue_contract_stops_at_link_two_with_the_global_slug():
    import asyncpg

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c)
            return await _chain(c, odds=_odds([]), now=_T0,
                                resolve_identity=_resolver_no_contract)
        finally:
            await c.close()

    ci = asyncio.run(run())
    f = ci["first_failing_link"]
    assert f["link"] == "2_VENUE_CONTRACT"
    assert f["refusal"] == "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP"
    assert f["global_slug"] == "mlb-chc-mia-2026-09-24"
    assert f["asked_for"] == "Chicago Cubs"
    # AND THE CHAIN STOPPED: no odds request was spent on a position that
    # has no contract to exit into.
    assert ci.get("odds_request") is None


@pg
def test_the_resolver_and_the_tokens_must_agree_on_the_payout_event():
    """Two independent sources for one fact. A disagreement is refused,
    never reconciled -- adopting the resolver's answer here is the
    self-confirming identity defect in a new place."""
    import asyncpg

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c)
            return await _chain(c, odds=_odds([]), now=_T0,
                                resolve_identity=_resolver_other_side)
        finally:
            await c.close()

    ci = asyncio.run(run())
    f = ci["first_failing_link"]
    assert f["link"] == "2_VENUE_CONTRACT"
    assert f["refusal"] == W.R_RESOLVER_DISAGREES
    assert "Miami Marlins" in f["why"] and "Chicago Cubs" in f["why"]


@pg
def test_no_market_row_fails_at_link_one_because_the_tokens_go_with_it():
    """`market_tokens.condition_id` is ON DELETE CASCADE, so a condition
    with no `markets` row has no tokens either and the chain refuses at
    link 1, not link 2. Asserted as it actually behaves: writing the test
    the other way round would have documented an ordering the schema
    makes impossible."""
    import asyncpg

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c)
            await c.execute("DELETE FROM markets WHERE condition_id = $1",
                            _COND)
            left = await c.fetchval(
                "SELECT count(*) FROM market_tokens WHERE condition_id = $1",
                _COND)
            return left, await _chain(c, odds=_odds([]), now=_T0,
                                      resolve_identity=_resolver_ok)
        finally:
            await c.close()

    left, ci = asyncio.run(run())
    assert left == 0, "the cascade took the tokens with the market"
    f = ci["first_failing_link"]
    assert f["link"] == "1_HELD_EXPOSURE"
    assert f["refusal"] == W.R_IDENTITY_UNRESOLVED
    assert ci.get("odds_request") is None


# ── the whole thing, through the worker, twice ───────────────────────

@pg
def test_two_management_cycles_on_one_position_both_price_the_hold():
    """ACCEPTANCE, in a controlled copy of the production shape: the SAME
    position across two cycles, each with a fresh probability, the current
    residual, a selected action and quantity, and the chain persisted."""
    import asyncpg

    from sportsassets import bettor_rn1x_store as store
    from sportsassets.workers import ext_pinnacle_loop as EXT

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        orig_resolve = EXT.resolve_venue_identity
        orig_book = EXT._read_book_blocking
        EXT.resolve_venue_identity = _resolver_ok
        EXT._read_book_blocking = lambda slug: {"marketData": _BOOK}
        try:
            await _fixture(c)
            pid = await _position(c)
            c1 = await W.manage_open_positions(
                c, experiment_id=_EXP, now=_T0 + 10,
                odds=_odds([_event(observed_at=_T0 + 5)],
                           received_at=_T0 + 6))
            # A SECOND CYCLE, its own quote, its own clock.
            c2 = await W.manage_open_positions(
                c, experiment_id=_EXP, now=_T0 + 130,
                odds=_odds([_event(observed_at=_T0 + 125, cubs=1.40)],
                           received_at=_T0 + 126))
            rows = [dict(r) for r in await c.fetch(
                "SELECT decision_id, selected_action, selected_qty::float8 q,"
                " hold_value_usd, ev_basis, input_available, operating_state,"
                " input_freshness->>'age_from_observation_s' age,"
                " input_freshness->>'bound_s' bound,"
                " payout_identity->>'row_payout_event' pays,"
                " resulting_inventory->>'residual_qty' resid,"
                " accounting_reconciles,"
                " input_chain->>'first_failing_link' ffl,"
                " input_chain->'chain' chain,"
                " venue_translation->'HOLD'->>'ok' venue_ok,"
                " selection_reason"
                " FROM rn1x_decisions WHERE position_id = $1"
                " ORDER BY decision_ts, decision_id", pid)]
            npos = await c.fetchval("SELECT count(*) FROM rn1x_positions")
            return c1, c2, rows, npos
        finally:
            EXT.resolve_venue_identity = orig_resolve
            EXT._read_book_blocking = orig_book
            await c.close()

    c1, c2, rows, npos = asyncio.run(run())

    assert c1["managed"] == 1 and c2["managed"] == 1
    assert c1["priced_holds"] == 1 and c2["priced_holds"] == 1, (
        "a management cycle that cannot price a hold is the blocker")
    assert c1["no_inputs"] == 0 and c2["no_inputs"] == 0
    assert c1["input_source"] == "BETTOR_MANAGED_INPUT_PULL_V1"
    assert npos == 1, "one position row, managed twice -- not two rows"
    assert len(rows) == 2, rows
    for r in rows:
        assert r["input_available"] is True, r
        assert r["hold_value_usd"] is not None, r
        assert r["ev_basis"] == "EXTERNAL_LABELLED_PROBABILITY", r
        assert r["pays"] == "Chicago Cubs", r
        assert float(r["age_s"] if "age_s" in r else r["age"]) <= 30.0, r
        assert float(r["bound"]) == 30.0, r
        assert float(r["resid"]) == pytest.approx(100.0), r
        assert r["accounting_reconciles"] is True, r
        assert r["selected_action"] is not None, r
        assert r["q"] is not None, r
        # THE VENUE MODEL IS ESTABLISHED NOW: the slug and the intent came
        # from the catalogue rather than from a valuation row that did not
        # exist, which is the second symptom of the same cause.
        assert r["venue_ok"] == "true", r
        assert r["ffl"] is None, r
        chain = r["chain"]
        if isinstance(chain, str):
            import json
            chain = json.loads(chain)
        assert [x["link"] for x in chain][-1] == "5_EXIT_LADDER", chain
    # the two decisions are at two real, distinct instants
    assert rows[0]["decision_id"] != rows[1]["decision_id"]


@pg
def test_a_blind_cycle_records_which_link_failed():
    """The replacement for the generic summary. A cycle that cannot price
    still writes a decision -- and now that decision says WHERE it
    stopped, on which identifiers, at which age."""
    import asyncpg

    from sportsassets.workers import ext_pinnacle_loop as EXT

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        orig_resolve = EXT.resolve_venue_identity
        orig_book = EXT._read_book_blocking
        EXT.resolve_venue_identity = _resolver_ok
        EXT._read_book_blocking = lambda slug: {"marketData": _BOOK}
        try:
            await _fixture(c)
            pid = await _position(c)
            got = await W.manage_open_positions(
                c, experiment_id=_EXP, now=_T0 + 10,
                # 400 s old: the odds rule refuses it
                odds=_odds([_event(observed_at=_T0 - 390)]))
            row = dict(await c.fetchrow(
                "SELECT ev_basis, input_available, hold_value_usd,"
                " input_labels->>'first_failing_link' link,"
                " input_labels->>'first_failing_refusal' refusal,"
                " input_chain->'first_failing_link' ffl"
                " FROM rn1x_decisions WHERE position_id = $1", pid))
            return got, row
        finally:
            EXT.resolve_venue_identity = orig_resolve
            EXT._read_book_blocking = orig_book
            await c.close()

    got, row = asyncio.run(run())
    assert got["managed"] == 1 and got["priced_holds"] == 0
    assert got["first_failing_links"] == {"4_PROBABILITY": 1}, got
    assert row["hold_value_usd"] is None
    assert row["input_available"] is False
    assert row["link"] == "4_PROBABILITY"
    assert row["refusal"] == "QUOTE_STALE"
    ffl = row["ffl"]
    if isinstance(ffl, str):
        import json
        ffl = json.loads(ffl)
    assert ffl["age_s"] > 30.0 and ffl["max_age_s"] == 30.0

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


def _odds(events, *, received_at=_T0, calls=3, at=None):
    """A ManagedOdds whose provider is this payload, counted like the real
    one so the budget assertions are about the real budget.

    `at` anchors its clock to the caller's timeline. Without it the
    process-wide cache and the pacing gate would be judged against the real
    wall clock while the decisions run in a declared epoch, and a cached
    payload would look 9.7 million seconds old (or new).
    """
    async def fetch(sport_key):
        return _payload(events, received_at=received_at)
    return W.ManagedOdds(fetch=fetch, calls=calls,
                         clock=(None if at is None else (lambda: float(at))))


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
    assert "EXITS ARE UNAVAILABLE" in f["consequence"]
    # THE CHAIN DOES NOT STOP. Holding needs the identity and a
    # probability, not a contract, so the walk continues and the exits --
    # only the exits -- are what the missing contract costs.
    assert ci["identity_cross_check"] == "NOT_AVAILABLE_NO_VENUE_CONTRACT"
    # this fixture's provider payload is empty, so the walk stops at link 3
    # -- the point is that it got PAST link 2 at all
    assert [x["link"] for x in ci["chain"]] == [
        "1_HELD_EXPOSURE", "2_VENUE_CONTRACT", "3_PROVIDER_FIXTURE"]


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
                           received_at=_T0 + 6, at=_T0 + 10))
            # A SECOND CYCLE, its own quote, its own clock. 120 s later,
            # which is the lane's real cadence and past the declared 45 s
            # request interval.
            c2 = await W.manage_open_positions(
                c, experiment_id=_EXP, now=_T0 + 130,
                odds=_odds([_event(observed_at=_T0 + 125, cubs=1.40)],
                           received_at=_T0 + 126, at=_T0 + 130))
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
                odds=_odds([_event(observed_at=_T0 - 390)], at=_T0 + 10))
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


@pg
def test_no_venue_contract_still_prices_the_hold_and_refuses_the_exits():
    """A PRICED HOLD IS ACCEPTABLE; a missing-input hold is not. When the
    venue catalogue carries no contract, exiting is impossible and holding
    is the ONLY action -- which is precisely when the hold value matters
    most. Refusing to price it there would be the wrong way round."""
    import asyncpg

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c)
            return await _chain(
                c, odds=_odds([_event(observed_at=_T0 - 6)]), now=_T0,
                resolve_identity=_resolver_no_contract)
        finally:
            await c.close()

    ci = asyncio.run(run())
    # the probability is there, from the tokens' identity alone
    assert ci["available"] is True
    assert ci["book_available"] is False
    assert 0.55 < ci["probability_row"]["probability"] < 0.65
    assert ci["probability_row"]["payout_event"] == "Chicago Cubs"
    assert ci["probability_row"]["us_market_slug"] is None, (
        "no contract, so no venue slug -- and the row says so rather than "
        "carrying one from somewhere else")
    # and the exits are refused BY NAME, not silently priced
    last = ci["chain"][-1]
    assert last["link"] == "5_EXIT_LADDER" and last["ok"] is False
    assert last["refusal"] == "NO_VENUE_CONTRACT_SO_NO_LADDER"
    assert last["hold_is_priced_anyway"] is True
    assert ci.get("bid") is None and ci.get("sale_ladder") is None
    # the FIRST failing link is still the one that failed first
    assert ci["first_failing_link"]["link"] == "2_VENUE_CONTRACT"


# ── A PULL IS NOT "FRESH BY CONSTRUCTION" ────────────────────────────
#
# The quote can be inside the bound when the fetch STARTS and outside it by
# the time the decision is taken. There are two distinct ways for that to
# happen and they are refused in two different places, so both are tested:
#
#   a SLOW PROVIDER   the response itself arrives late, so the quote is
#                     already past the bound when the de-vig asks ->
#                     refused at link 4 of the PULL;
#   SLOW LOCAL WORK   the quote is fine at the pull and the remaining work
#                     (here the venue book read) pushes the DECISION past
#                     the bound -> the pull succeeds and the DECISION
#                     refuses.
#
# Collapsing either case into one batch timestamp would price a stale
# quote as fresh.

def _cycle(c, *, base, quote_age, provider_delay=0.0, book_delay=0.0):
    """One management cycle on a clock we advance inside the provider call
    and inside the venue read, exactly as real latency would."""
    from sportsassets.workers import ext_pinnacle_loop as EXT

    moment = {"t": base}

    async def fetch(sport_key):
        moment["t"] += provider_delay
        return _payload([_event(observed_at=base - quote_age)],
                        received_at=moment["t"])

    def read(slug):
        moment["t"] += book_delay
        return {"marketData": _BOOK}

    EXT._read_book_blocking = read
    return W.manage_open_positions(
        c, experiment_id=_EXP, now=base, clock=lambda: moment["t"],
        odds=W.ManagedOdds(fetch=fetch, calls=3,
                           clock=lambda: moment["t"]))


@pg
def test_a_slow_provider_response_is_refused_at_the_pull():
    """20 s old at the fetch, a 15 s response: 35 s old when the de-vig
    asks, so link 4 refuses and no probability is adopted."""
    import asyncpg

    from sportsassets.workers import ext_pinnacle_loop as EXT

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        orig_resolve, orig_book = (EXT.resolve_venue_identity,
                                   EXT._read_book_blocking)
        EXT.resolve_venue_identity = _resolver_ok
        try:
            await _fixture(c)
            pid = await _position(c)
            got = await _cycle(c, base=_T0, quote_age=20.0,
                               provider_delay=15.0)
            row = dict(await c.fetchrow(
                "SELECT hold_value_usd, input_available, ev_basis,"
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
    assert got["priced_holds"] == 0
    assert got["first_failing_links"] == {"4_PROBABILITY": 1}, got
    assert row["hold_value_usd"] is None
    assert row["input_available"] is False
    assert row["link"] == "4_PROBABILITY"
    assert row["refusal"] == "QUOTE_STALE"
    ffl = row["ffl"]
    if isinstance(ffl, str):
        import json
        ffl = json.loads(ffl)
    assert ffl["age_s"] == pytest.approx(35.0, abs=0.01)
    assert ffl["max_age_s"] == 30.0


@pg
def test_local_latency_after_a_fresh_pull_is_refused_at_the_decision():
    """THE CASE THAT NEEDS THE SEPARATE CLOCKS. The quote is 20 s old when
    the de-vig asks -- inside the rule, so the pull succeeds and hands over
    a probability -- and the venue read then costs 12 s. At the decision
    the quote is 32 s old and the DECISION refuses it."""
    import asyncpg

    from sportsassets.workers import ext_pinnacle_loop as EXT

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        orig_resolve, orig_book = (EXT.resolve_venue_identity,
                                   EXT._read_book_blocking)
        EXT.resolve_venue_identity = _resolver_ok
        try:
            await _fixture(c)
            pid = await _position(c)
            # fast first, to prove the same inputs DO price when prompt
            quick = await _cycle(c, base=_T0, quote_age=20.0,
                                 provider_delay=0.0, book_delay=1.0)
            slow = await _cycle(c, base=_T0 + 300, quote_age=20.0,
                                provider_delay=0.0, book_delay=12.0)
            rows = [dict(r) for r in await c.fetch(
                "SELECT decision_id, hold_value_usd, ev_basis,"
                " input_available,"
                " input_freshness->>'age_from_observation_s' age,"
                " input_freshness->>'bound_s' bound,"
                " input_chain->'clocks' clocks,"
                " input_chain->>'input_latency_s' latency,"
                " input_chain->>'decided_at' decided_at,"
                " input_chain->'first_failing_link' ffl"
                " FROM rn1x_decisions WHERE position_id = $1"
                " ORDER BY decision_ts, decision_id", pid)]
            return quick, slow, rows
        finally:
            EXT.resolve_venue_identity = orig_resolve
            EXT._read_book_blocking = orig_book
            await c.close()

    quick, slow, rows = asyncio.run(run())

    assert quick["priced_holds"] == 1, "21 s at the decision is inside 30 s"
    assert slow["priced_holds"] == 0, (
        "32 s at the decision is outside it, and the pull cannot know that "
        "because the pull happened 12 s earlier")
    assert slow["no_inputs"] == 0, (
        "the PULL succeeded and the DECISION refused -- different findings, "
        "and the census must not merge them")
    assert slow["first_failing_links"] == {}, (
        "no link failed: the chain completed and then time passed")
    assert len(rows) == 2, rows
    ok, refused = rows

    assert ok["hold_value_usd"] is not None
    assert float(ok["age"]) == pytest.approx(21.0, abs=0.01)
    assert refused["hold_value_usd"] is None
    assert refused["input_available"] is False
    assert "STALE" in (refused["ev_basis"] or ""), refused
    assert float(refused["age"]) == pytest.approx(32.0, abs=0.01)
    assert float(refused["bound"]) == 30.0
    # jsonb null, not SQL NULL: the column carries the chain and the chain
    # records no failure
    assert refused["ffl"] in (None, "null"), "the chain itself did not fail"
    assert float(refused["latency"]) == pytest.approx(12.0, abs=0.01)

    # FOUR CLOCKS, SEPARATE, AND THEY DISAGREE.
    cl = refused["clocks"]
    if isinstance(cl, str):
        import json
        cl = json.loads(cl)
    base = _T0 + 300
    assert cl["pull_started_at"] == pytest.approx(base)
    assert cl["provider_observed_at"] == pytest.approx(base - 20)
    assert cl["provider_received_at"] == pytest.approx(base)
    assert cl["book_read_at"] == pytest.approx(base + 12)
    assert cl["inputs_ready_at"] >= cl["book_read_at"]
    assert float(refused["decided_at"]) >= cl["inputs_ready_at"], (
        "a decision cannot predate the inputs it used")
    assert len({cl["provider_observed_at"], cl["provider_received_at"],
                cl["book_read_at"]}) == 3, (
        "three distinct instants, not one stamp copied three times")


# ── THE DECLARED INTERVAL BINDS ACROSS CYCLES ────────────────────────
#
# MANAGED_ODDS_MIN_INTERVAL_S was declared and unused: every cycle built a
# fresh ManagedOdds, so the interval bound only WITHIN a batch. Two cycles
# 10 s apart -- or a cycle and the diagnostic read -- each spent their own
# request against a quota that does not care which code path spent it.

@pytest.fixture(autouse=True)
def _forget_the_process_pacing():
    W.odds_gate_reset()
    yield
    W.odds_gate_reset()


def test_the_gate_is_process_wide_and_not_per_instance():
    calls = []

    async def fetch(key):
        calls.append(key)
        return _payload([], received_at=1000.0)

    async def go():
        # two SEPARATE ManagedOdds, as two cycles build
        a = W.ManagedOdds(fetch=fetch, calls=3, clock=lambda: 1000.0)
        b = W.ManagedOdds(fetch=fetch, calls=3, clock=lambda: 1010.0)
        first = await a.for_family("baseball")
        second = await b.for_family("baseball")
        return first, second

    first, second = asyncio.run(go())
    assert first["calls_made"] == 1, "the first cycle spends its request"
    assert second["calls_made"] == 0, (
        "10 s later is inside the declared 45 s interval, and a NEW "
        "instance must not reset it")
    assert calls == ["baseball_mlb"], calls
    # 10 s later the payload is still inside the odds bound, so the second
    # caller is SERVED from the process cache rather than refused -- the
    # quota is preserved either way, and being served is better.
    assert second["reused"][0]["from"] == "PROCESS_CACHE"


def test_past_the_interval_the_next_request_is_allowed():
    calls = []

    async def fetch(key):
        calls.append(key)
        return _payload([], received_at=1000.0)

    async def go():
        a = W.ManagedOdds(fetch=fetch, calls=3, clock=lambda: 1000.0)
        b = W.ManagedOdds(fetch=fetch, calls=3, clock=lambda: 1046.0)
        await a.for_family("baseball")
        return await b.for_family("baseball")

    second = asyncio.run(go())
    assert second["calls_made"] == 1, "46 s later is past the 45 s interval"
    assert calls == ["baseball_mlb", "baseball_mlb"]


def test_a_payload_in_hand_is_reused_instead_of_paced_out():
    """Inside the odds bound the process reuses what it already has, so a
    paced caller is not blinded -- and the reuse is NOT wider than the
    rule: the ceiling is the 30 s odds bound, and the de-vig still ages
    the quote on top of it."""
    calls = []

    async def fetch(key):
        calls.append(key)
        return _payload([_event(observed_at=995.0)], received_at=1000.0)

    async def go():
        a = W.ManagedOdds(fetch=fetch, calls=3, clock=lambda: 1000.0)
        await a.for_family("baseball")
        # 20 s later: paced out of a NEW request, and served from cache
        b = W.ManagedOdds(fetch=fetch, calls=3, clock=lambda: 1020.0)
        warm = await b.for_family("baseball")
        # 40 s later: past the cache TTL, and still inside the interval
        d = W.ManagedOdds(fetch=fetch, calls=3, clock=lambda: 1040.0)
        cold = await d.for_family("baseball")
        return warm, cold

    warm, cold = asyncio.run(go())
    assert calls == ["baseball_mlb"], "one request served all three callers"
    assert warm["calls_made"] == 0 and warm["payloads"]
    assert warm["reused"][0]["from"] == "PROCESS_CACHE"
    assert warm["reused"][0]["cache_age_s"] == pytest.approx(20.0, abs=0.01)
    assert W.MANAGED_ODDS_CACHE_TTL_S == 30.0, (
        "the reuse ceiling is the odds rule, not the request interval")
    assert cold["calls_made"] == 0 and not cold["payloads"], (
        "past the TTL the cache is not used, and the interval still "
        "forbids a new request -- so this caller is blind and says so")
    assert W.R_MANAGED_PACED in cold["refusals"]


def test_the_diagnostic_read_spends_the_same_quota_as_a_cycle():
    """The read-only chain endpoint goes through the same gate. Provider
    quota does not care which of our code paths spent it."""
    calls = []

    async def fetch(key):
        calls.append(key)
        return _payload([], received_at=1000.0)

    async def go():
        cycle = W.ManagedOdds(fetch=fetch, calls=3, clock=lambda: 1000.0)
        await cycle.for_family("baseball")
        # the diagnostic read, 5 s later, as a verify step would call it
        diag = W.ManagedOdds(fetch=fetch, calls=2, clock=lambda: 1005.0)
        return await diag.for_family("baseball")

    diag = asyncio.run(go())
    assert calls == ["baseball_mlb"], "the diagnostic did NOT spend a second"
    assert diag["calls_made"] == 0
    assert diag["reused"][0]["from"] == "PROCESS_CACHE"


# ── THE SHIPPED ACCEPTANCE GATE, RUN AGAINST WHAT THE CODE WRITES ────
#
# The gate lives in command-verify.yml. A gate exercised only against
# hand-built fixtures proves the gate, not the code: the code could omit a
# field the gate requires and nobody would find out until a production run
# failed. So this test extracts the ACTUAL program from the workflow and
# runs it over the rows two real management cycles persisted.

@pg
def test_the_code_satisfies_the_shipped_acceptance_gate():
    import json
    import pathlib
    import re
    import shutil
    import subprocess

    import asyncpg

    if shutil.which("jq") is None:
        pytest.skip("jq is not installed here")
    wf = (pathlib.Path(__file__).resolve().parents[2]
          / ".github" / "workflows" / "command-verify.yml")
    if not wf.exists():
        pytest.skip("the workflow is not in this checkout")
    m = re.search(r"ACC='(.*?)'\n", wf.read_text(), re.S)
    assert m, "the acceptance program is not where this test looks for it"
    prog = m.group(1)

    from sportsassets.workers import ext_pinnacle_loop as EXT

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        orig_resolve, orig_book = (EXT.resolve_venue_identity,
                                   EXT._read_book_blocking)
        EXT.resolve_venue_identity = _resolver_ok
        EXT._read_book_blocking = lambda slug: {"marketData": _BOOK}
        try:
            await _fixture(c)
            pid = await _position(c)
            await W.manage_open_positions(
                c, experiment_id=_EXP, now=_T0 + 10,
                odds=_odds([_event(observed_at=_T0)], received_at=_T0 + 1,
                           at=_T0 + 10))
            await W.manage_open_positions(
                c, experiment_id=_EXP, now=_T0 + 130,
                odds=_odds([_event(observed_at=_T0 + 120, cubs=1.45)],
                           received_at=_T0 + 121, at=_T0 + 130))
            # normalised exactly as the workflow normalises the trace
            rows = await c.fetch(
                "SELECT decision_id, decision_ts, selected_action,"
                " selected_qty::float8 q, hold_value_usd, selection_reason,"
                " accounting_reconciles, payout_identity, input_chain,"
                " alternatives, resulting_inventory, input_freshness"
                " FROM rn1x_decisions WHERE position_id = $1"
                " ORDER BY decision_ts, decision_id", pid)
            return [dict(r) for r in rows]
        finally:
            EXT.resolve_venue_identity = orig_resolve
            EXT._read_book_blocking = orig_book
            await c.close()

    rows = asyncio.run(run())
    assert len(rows) == 2, rows

    def j(v):
        if isinstance(v, str):
            return json.loads(v)
        return v or {}

    doc = {"decisions": [
        {"id": r["decision_id"], "ts": r["decision_ts"].isoformat(),
         "action": r["selected_action"], "qty": r["q"],
         "hold": r["hold_value_usd"], "reason": r["selection_reason"] or "",
         "reconciles": r["accounting_reconciles"],
         "pays": j(r["payout_identity"]).get("row_payout_event"),
         "chain": j(r["input_chain"]), "alt": j(r["alternatives"]),
         "inv": j(r["resulting_inventory"]),
         "fr": j(r["input_freshness"])}
        for r in rows]}
    got = subprocess.run(["jq", "-e", prog], input=json.dumps(doc),
                         capture_output=True, text=True)
    assert got.returncode in (0, 1), got.stderr[:400]
    verdicts = json.loads(got.stdout)
    incomplete = [(v["id"], v["missing"]) for v in verdicts if v["missing"]]
    assert not incomplete, (
        "the code wrote a decision the SHIPPED gate rejects: %s" % incomplete)
    assert len({v["ts"] for v in verdicts}) == 2, (
        "two distinct runtime instants, not one decision counted twice")

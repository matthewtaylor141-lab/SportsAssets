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
import contextlib
import os
import pathlib

import pytest

from sportsassets.workers import rn1x_shadow as W

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

_EXP = "RN1X_SHADOW_CHALLENGER_HOLD_RANKED_V1"
_POL = "SHADOW_CHALLENGER_HOLD_RANKED_V1"
_BENCH = "MANAGEMENT_PAIR_091_STOP_16_V1"   # the frozen benchmark
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


# ── THE VENUE'S OWN SETTLEMENT PROSE, AS THE VENUE PUBLISHES IT ──────
#
# `read_rules` is injected exactly like the book reader. The text below is
# the SHAPE of a listing description, not a transcription of one: what is
# under test is that prose which states the terminal case establishes the
# overtime rule, and that prose which contradicts the book rule refuses.
_RULES_AGREEING = ("This market settles on the final result of the game, "
                   "including any extra innings. If the game is abandoned "
                   "or postponed and not completed, the market is void and "
                   "stakes are returned.")
_RULES_CONFLICTING = ("This market settles after nine innings only; extra "
                      "innings are excluded. If the game is abandoned the "
                      "market is void and stakes are returned.")
_VOID_REFUND = "STAKE_REFUNDED_MARKET_VOID"
_RULES_SILENT = ("This market settles on the outcome of the listed event. "
                 "See the venue rulebook for further terms.")


def _rules(text):
    """A venue rules reader returning `text`, or a named absence for None."""
    def read(slug, now=None):
        if text is None:
            return {"ok": False, "slug": slug, "read_at": 0.0,
                    "error": "VENUE_PUBLISHES_NO_RULES_TEXT_FOR_THIS_CONTRACT"}
        return {"ok": True, "slug": slug, "rules_text": text,
                "rules_field": "description", "read_at": 0.0,
                "source": "pmus:/markets?slug=<slug>:rules_text"}
    return read


@contextlib.contextmanager
def _book_void_rule(cls):
    """Hold the BOOKMAKER's abandonment rule for the duration of a test.

    `BOOK_VOID_RULE` ships EMPTY on purpose: a value in it is a claim about
    a third party's published terms and may only be added with a citation.
    A test may hold one to exercise the established path; production
    reports VOID_ABANDONMENT_BOOK_RULE_NOT_HELD until one is captured.
    """
    from sportsassets import bettor_venue_settlement as VS
    before = dict(VS.BOOK_VOID_RULE)
    try:
        if cls is None:
            VS.BOOK_VOID_RULE.pop("baseball", None)
        else:
            VS.BOOK_VOID_RULE["baseball"] = cls
        yield VS
    finally:
        VS.BOOK_VOID_RULE.clear()
        VS.BOOK_VOID_RULE.update(before)

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
                     "4b_SETTLEMENT_COMPATIBILITY",
                     "5_EXIT_LADDER"], names
    # SETTLEMENT COMPATIBILITY IS RECORDED AND CONDITIONS THE HOLD VALUE.
    # It does not stop the chain -- refusing to price would assert the
    # position is worthless -- and it is NOT a HOLD_TO_SETTLEMENT-only
    # concern: p x qty is realised at settlement either way, so an
    # unestablished terminal rule conditions the number the ranking uses.
    sc = next(x for x in ci["chain"]
              if x["link"] == "4b_SETTLEMENT_COMPATIBILITY")
    assert sc["blocks_chain"] is False
    assert sc["blocks_outright"] == "HOLD_TO_SETTLEMENT"
    assert "ORDINARY HOLD VALUE TOO" in sc["conditions"]
    assert ci["settlement"]["book_rule"] is not None
    assert "ORDINARY HOLD TOO" in ci["settlement"]["governs"]
    assert ci["settlement"]["hold_value_is_conditional"] is (
        not ci["settlement"]["overall_established"])
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
                c, read_rules=_rules(_RULES_AGREEING), experiment_id=_EXP, now=_T0 + 10,
                odds=_odds([_event(observed_at=_T0)], received_at=_T0 + 1,
                           at=_T0 + 10))
            await W.manage_open_positions(
                c, read_rules=_rules(_RULES_AGREEING), experiment_id=_EXP, now=_T0 + 130,
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

    with _book_void_rule(_VOID_REFUND):
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


@pg
def test_the_feeds_own_lag_is_measured_across_the_payload():
    """If the provider's lag alone exceeds the odds rule, no cadence of
    ours can fix it. One fixture cannot tell "this event is quiet" from
    "this feed is late", so the census covers every event carrying a
    Pinnacle h2h -- and it is a measurement, never a reason to widen."""
    import asyncpg

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c)
            evs = [_event(observed_at=_T0 - 10),
                   _event(home="Houston Astros", away="Oakland Athletics",
                          observed_at=_T0 - 48, event_id="ev-2"),
                   _event(home="Chicago White Sox", away="Kansas City Royals",
                          observed_at=_T0 - 70, event_id="ev-3")]
            return await _chain(c, odds=_odds(evs, received_at=_T0, at=_T0),
                                now=_T0, resolve_identity=_resolver_ok)
        finally:
            await c.close()

    ci = asyncio.run(run())
    ages = ci["provider_quote_ages_s"]
    assert ages["n"] == 3
    assert ages["min"] == pytest.approx(10.0)
    assert ages["max"] == pytest.approx(70.0)
    assert ages["median"] == pytest.approx(48.0)
    assert ages["over_the_odds_rule"] == 2, (
        "two of the three are past 30 s before any latency of ours")
    assert ages["odds_rule_s"] == 30.0
    # AND THE RULE IS STILL 30 s: measuring a slow feed does not relax it
    assert ci["probability_row"]["probability"] is not None, (
        "our own fixture was 10 s old and is priced")
    fr = next(x for x in ci["chain"] if x["link"] == "4_PROBABILITY")
    assert fr["max_age_s"] == 30.0


# ── UNCOVERED IS NOT THE SAME AS OVER, AND NEITHER IS OUR MAPPING ────

@pg
def test_an_uncovered_sport_reports_the_providers_catalogue_too():
    """'Not in OUR set' is a decision we can revisit. 'Not in THEIR
    catalogue' is not. The chain asks which, on a call that costs no
    credits, so nobody has to argue about extending the set blind."""
    import asyncpg

    from sportsassets.workers import ext_pinnacle_loop as EXT

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        orig_cat = getattr(EXT, "fetch_sport_catalogue", None)
        orig_key = os.environ.get("EDGE_ODDS_API_KEY")

        async def cat(*, api_key, timeout=20.0):
            return {"ok": True, "status": 200, "metered": False,
                    "sports": [{"key": "tennis_atp_wimbledon",
                                "group": "Tennis", "title": "ATP Wimbledon",
                                "active": False},
                               {"key": "baseball_mlb", "group": "Baseball",
                                "title": "MLB", "active": True}]}

        EXT.fetch_sport_catalogue = cat
        os.environ["EDGE_ODDS_API_KEY"] = "test-key-not-a-real-one"
        try:
            await _fixture(c, sport="Tennis")
            return await _chain(c, odds=_odds([], at=_T0), now=_T0,
                                resolve_identity=_resolver_ok)
        finally:
            if orig_cat is not None:
                EXT.fetch_sport_catalogue = orig_cat
            if orig_key is None:
                os.environ.pop("EDGE_ODDS_API_KEY", None)
            else:
                os.environ["EDGE_ODDS_API_KEY"] = orig_key
            await c.close()

    ci = asyncio.run(run())
    f = ci["first_failing_link"]
    assert f["link"] == "3_PROVIDER_FIXTURE"
    assert f["refusal"] == W.R_MANAGED_FAMILY
    assert f["sport_label"] == "Tennis"
    cat = f["provider_catalogue"]
    assert cat["asked"] is True and cat["ok"] is True
    assert cat["metered"] is False, "the catalogue read costs no credits"
    keys = [x["key"] for x in cat["keys_for_this_sport"]]
    assert keys == ["tennis_atp_wimbledon"], keys
    assert "no threshold is widened" in f["remedy"]


@pg
def test_the_fixtures_own_clock_is_reported_apart_from_our_flags():
    """A market our refresher still calls open can be a fixture that
    finished yesterday, and no provider or freshness policy can price a
    hold on an event that is over. `closed`/`resolved` are OUR flags;
    game_start is the fixture's."""
    import asyncpg

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c)
            await c.execute(
                "INSERT INTO market_starts(condition_id, game_start) "
                "VALUES($1, to_timestamp($2)) ON CONFLICT (condition_id) "
                "DO UPDATE SET game_start = to_timestamp($2)",
                _COND, _T0 - 86_400)
            unknown = await c.execute(
                "DELETE FROM market_starts WHERE condition_id = 'nope'")
            ci = await _chain(c, odds=_odds([_event(observed_at=_T0 - 5)],
                                           at=_T0),
                              now=_T0, resolve_identity=_resolver_ok)
            await c.execute("DELETE FROM market_starts WHERE condition_id = $1",
                            _COND)
            bare = await _chain(c, odds=_odds([_event(observed_at=_T0 - 5)],
                                              at=_T0),
                                now=_T0, resolve_identity=_resolver_ok)
            return ci, bare
        finally:
            await c.close()

    ci, bare = asyncio.run(run())
    fx = ci["fixture"]
    assert fx["game_start_known"] is True
    assert fx["is_past_start"] is True
    assert fx["seconds_since_start"] == pytest.approx(86_400.0, abs=1.0)
    assert fx["market_flags"] == {"closed": False, "resolved": False}, (
        "our flags still say open -- which is the point of reporting both")
    # AND AN ABSENT START IS NOT A START OF ZERO
    assert bare["fixture"]["game_start_known"] is False
    assert bare["fixture"]["is_past_start"] is None


# ── THE ACCEPTANCE POSITION, AND WHAT IT MAY NEVER CLAIM ─────────────
#
# The live challenger position is ATP tennis, which the provider set does
# not cover, so the manager cannot be demonstrated on it at all. Waiting
# for an RN1 seed in a covered sport is waiting on a coincidence. An
# acceptance position is created instead -- and every one of these tests
# is about it being unmistakable for the real thing.

def _covered(c, *, sport="MLB"):
    return _fixture(c, sport=sport)


@pg
def test_the_acceptance_position_is_entered_at_the_executable_price():
    import asyncpg

    from sportsassets.workers import ext_pinnacle_loop as EXT

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c)
            got = await W.seed_acceptance_position(
                c, experiment_id=_EXP, now=_T0,
                odds=_odds([_event(observed_at=_T0 - 5)], at=_T0),
                resolve_identity=_resolver_ok,
                read_book=lambda slug: {"marketData": _BOOK},
                fee_fn=lambda qty, price, maker=False: 0.02 * qty * price)
            row = None
            if got.get("created"):
                row = dict(await c.fetchrow(
                    "SELECT policy, provenance, entry_kind, entry_kind_why,"
                    " source_trade_id, source_account,"
                    " seed_qty::float8 q, seed_price::float8 p,"
                    " seed_basis_usd::float8 basis, decision_basis"
                    " FROM rn1x_positions WHERE position_id = $1",
                    got["position_id"]))
            return got, row
        finally:
            await c.close()

    got, row = asyncio.run(run())
    assert got["created"] is True, got
    # THE ENTRY IS THE LADDER'S OWN PRICE, READ NOW. The book's offers are
    # .74, which is the acquisition side for a held LONG.
    assert got["entry"]["price"] == pytest.approx(0.74)
    assert got["entry"]["qty"] == pytest.approx(10.0)
    assert got["entry"]["fee_usd"] == pytest.approx(0.02 * 10 * 0.74)
    assert got["entry"]["basis_usd"] == pytest.approx(
        10 * 0.74 + 0.02 * 10 * 0.74)
    assert got["entry"]["depth_at_price"] >= 10.0
    # AND THE INPUTS THE MANAGER WILL NEED WERE CHECKED BEFORE WRITING
    assert got["inputs_at_entry"]["probability"] is not None
    assert got["inputs_at_entry"]["exit_price"] == pytest.approx(0.72)
    # ── WHAT THE ROW MAY NEVER CLAIM ────────────────────────────────
    assert row["policy"] == "ACCEPTANCE_SHADOW_MANAGER_DEMO_V1"
    assert row["policy"] != _POL, "not the challenger's policy"
    assert row["policy"] != _BENCH, "not the benchmark's policy"
    assert row["provenance"] == "ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY"
    assert row["entry_kind"] == "ACCEPTANCE_MODELLED_ENTRY"
    assert row["source_account"] == \
        "ACCEPTANCE_HARNESS_NOT_AN_OBSERVED_ACCOUNT"
    assert row["source_trade_id"] < 0, (
        "a NEGATIVE sentinel, so it can never be read as a trades.id")
    assert row["decision_basis"] == "RUNTIME_WALL_CLOCK"
    why = row["entry_kind_why"]
    for phrase in ("MODELLED", "NOT an RN1 signal",
                   "NOT an executed order", "NOT funded"):
        assert phrase in why, (phrase, why)
    assert "AN_RN1_SIGNAL" in got["is_not"]
    assert "FUNDED" in got["is_not"]


@pg
def test_seeding_the_same_market_twice_is_idempotent():
    """A repeat call ADOPTS the existing position instead of minting a
    second one on the same exposure."""
    import asyncpg

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c)
            kw = dict(experiment_id=_EXP, now=_T0,
                      resolve_identity=_resolver_ok,
                      read_book=lambda slug: {"marketData": _BOOK})
            a = await W.seed_acceptance_position(
                c, odds=_odds([_event(observed_at=_T0 - 5)], at=_T0), **kw)
            W.odds_gate_reset()
            b = await W.seed_acceptance_position(
                c, odds=_odds([_event(observed_at=_T0 - 5)], at=_T0), **kw)
            n = await c.fetchval(
                "SELECT count(*) FROM rn1x_positions WHERE policy = $1",
                "ACCEPTANCE_SHADOW_MANAGER_DEMO_V1")
            return a, b, n
        finally:
            await c.close()

    a, b, n = asyncio.run(run())
    # THE SECOND CALL ADOPTS, it does not create. Both return the SAME
    # position and no second row is written -- and the second call says
    # which of the two happened rather than reporting a create it did not
    # perform.
    assert a["created"] is True and a["adopted"] is False
    assert b["created"] is False and b["adopted"] is True
    assert a["position_id"] == b["position_id"]
    assert b["decisions_so_far"] == 0 and b["released_qty"] == 0.0
    assert n == 1, "one position on one exposure, however often it is asked"


@pg
def test_an_uncoverable_candidate_is_refused_with_its_reason():
    """A tennis market cannot supply a complete entry, and the seeder says
    so per candidate rather than writing a position it cannot manage."""
    import asyncpg

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c, sport="Tennis")
            return await W.seed_acceptance_position(
                c, experiment_id=_EXP, now=_T0,
                odds=_odds([_event(observed_at=_T0 - 5)], at=_T0),
                resolve_identity=_resolver_ok,
                read_book=lambda slug: {"marketData": _BOOK})
        finally:
            await c.close()

    got = asyncio.run(run())
    assert got["created"] is False
    assert got["refusal"] == W.R_NO_COVERED_CANDIDATE
    assert got["candidates_examined"] == 0, (
        "a tennis market is not a covered candidate in the first place")
    assert "MLB" in got["covered_sport_labels"]


@pg
def test_the_acceptance_position_is_managed_by_the_production_lifecycle():
    """THE DEMONSTRATION. The same position, two distinct cycles, through
    the same `manage_open_positions` the challenger lane runs -- and every
    field the shipped acceptance gate requires, present on both rows."""
    import json
    import pathlib
    import re
    import shutil
    import subprocess

    import asyncpg

    from sportsassets.workers import ext_pinnacle_loop as EXT

    if shutil.which("jq") is None:
        pytest.skip("jq is not installed here")
    wf = (pathlib.Path(__file__).resolve().parents[2]
          / ".github" / "workflows" / "command-verify.yml")
    if not wf.exists():
        pytest.skip("the workflow is not in this checkout")
    prog = re.search(r"ACC='(.*?)'\n", wf.read_text(), re.S).group(1)

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        orig_resolve, orig_book = (EXT.resolve_venue_identity,
                                   EXT._read_book_blocking)
        EXT.resolve_venue_identity = _resolver_ok
        EXT._read_book_blocking = lambda slug: {"marketData": _BOOK}
        try:
            await _fixture(c)
            seeded = await W.seed_acceptance_position(
                c, read_rules=_rules(_RULES_AGREEING), experiment_id=_EXP, now=_T0,
                odds=_odds([_event(observed_at=_T0 - 5)], at=_T0),
                resolve_identity=_resolver_ok,
                read_book=lambda slug: {"marketData": _BOOK})
            assert seeded["created"], seeded
            pid = seeded["position_id"]
            # TWO CYCLES, 120 s apart, each pulling its own quote -- the
            # same lifecycle the challenger lane runs, not a special path.
            c1 = await W.manage_open_positions(
                c, read_rules=_rules(_RULES_AGREEING), experiment_id=_EXP, now=_T0 + 60,
                odds=_odds([_event(observed_at=_T0 + 50)],
                           received_at=_T0 + 51, at=_T0 + 60))
            c2 = await W.manage_open_positions(
                c, read_rules=_rules(_RULES_AGREEING), experiment_id=_EXP, now=_T0 + 180,
                odds=_odds([_event(observed_at=_T0 + 170, cubs=1.45)],
                           received_at=_T0 + 171, at=_T0 + 180))
            rows = [dict(r) for r in await c.fetch(
                "SELECT decision_id, decision_ts, selected_action,"
                " selected_qty::float8 q, hold_value_usd, selection_reason,"
                " accounting_reconciles, payout_identity, input_chain,"
                " alternatives, resulting_inventory, input_freshness"
                " FROM rn1x_decisions WHERE position_id = $1"
                " ORDER BY decision_ts, decision_id", pid)]
            return seeded, c1, c2, rows
        finally:
            EXT.resolve_venue_identity = orig_resolve
            EXT._read_book_blocking = orig_book
            await c.close()

    with _book_void_rule(_VOID_REFUND):
        seeded, c1, c2, rows = asyncio.run(run())

    assert c1["managed"] == 1 and c2["managed"] == 1
    assert c1["priced_holds"] == 1 and c2["priced_holds"] == 1
    assert len(rows) == 2, rows

    def j(v):
        return json.loads(v) if isinstance(v, str) else (v or {})

    doc = {"decisions": [
        {"id": r["decision_id"], "ts": r["decision_ts"].isoformat(),
         "action": r["selected_action"], "qty": r["q"],
         "hold": r["hold_value_usd"], "reason": r["selection_reason"] or "",
         "reconciles": r["accounting_reconciles"],
         "pays": j(r["payout_identity"]).get("row_payout_event"),
         "chain": j(r["input_chain"]), "alt": j(r["alternatives"]),
         "inv": j(r["resulting_inventory"]),
         "fr": j(r["input_freshness"])} for r in rows]}
    got = subprocess.run(["jq", "-e", prog], input=json.dumps(doc),
                         capture_output=True, text=True)
    assert got.returncode in (0, 1), got.stderr[:400]
    verdicts = json.loads(got.stdout)
    incomplete = [(v["id"], v["missing"]) for v in verdicts if v["missing"]]
    assert not incomplete, (
        "the SHIPPED acceptance gate rejects the demonstration: %s"
        % incomplete)
    assert len({v["ts"] for v in verdicts}) == 2, "two distinct instants"
    # SETTLEMENT COMPATIBILITY IS ON THE ROW, whatever it says
    ch = j(rows[0]["input_chain"])
    sc = [x for x in (ch.get("chain") or [])
          if x["link"] == "4b_SETTLEMENT_COMPATIBILITY"]
    assert sc and sc[0]["blocks_chain"] is False
    assert "book_rule" in sc[0]


# ── THE DEFECT RUN 37 MEASURED, AS A REGRESSION ──────────────────────
#
# Production examined eight candidates and refused all eight at link 2 with
# NO_VENUE_NATIVE_CONTRACT_IN_PREMAP. The pool was "the eight most recently
# updated rows in a covered sport", which in production meant markets
# carrying sport = 'Soccer' whose titles are "GOP uses 'Nuclear Option' ..."
# and Segunda Division fixtures dated 2025-11-30. Recency says nothing
# about whether a venue-native contract exists.
#
# So the pool is now ordered by whether one is KNOWN to exist -- evidenced
# by an external_valuations row carrying a us_market_slug -- and this test
# builds exactly that situation: a decoy updated LATER with no such row,
# and the real market with one. The decoy must not be chosen.

@pg
def test_the_pool_prefers_a_market_with_a_known_venue_contract():
    import asyncpg

    decoy = "0xdecoy_no_premap"

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c)
            await c.execute("DELETE FROM market_tokens WHERE condition_id=$1",
                            decoy)
            await c.execute("DELETE FROM markets WHERE condition_id=$1", decoy)
            # The real market is made STRICTLY OLDER, so recency alone would
            # pick the decoy and the assertion below is discriminating.
            await c.execute("UPDATE markets SET updated_at = now() - "
                            "interval '5 minutes' WHERE condition_id = $1",
                            _COND)
            # THE DECOY: same covered sport, updated AFTER the real market,
            # both tokens present -- and no venue contract has ever been
            # resolved for it. Under the old ordering this is candidate #1.
            await c.execute(
                "INSERT INTO markets(condition_id,slug,sport,title,"
                "event_title,closed,resolved,updated_at) "
                "VALUES($1,$2,'MLB',$3,$3,false,false,now())",
                decoy, "gop-nuclear-option",
                "GOP uses 'Nuclear Option' to break filibuster")
            for i, n in ((0, "Yes"), (1, "No")):
                await c.execute(
                    "INSERT INTO market_tokens(token_id,condition_id,outcome,"
                    "outcome_index) VALUES($1,$2,$3,$4) "
                    "ON CONFLICT (token_id) DO NOTHING",
                    decoy + "-t%d" % i, decoy, n, i)
            # THE EVIDENCE that the real market has a venue-native contract:
            # a row the ENTRY lane wrote, carrying the slug. It is used to
            # CHOOSE the market and never to price it.
            await c.execute(
                "INSERT INTO external_valuations(experiment_id,version,"
                "source_class,provider,book,devig_method,venue,condition_id,"
                "us_market_slug,contract_selection,sport_family,market,"
                "raw_odds,outcomes_priced,expected_outcomes,probability,"
                "observed_at,received_at,decision,admissible) "
                "VALUES('EXT','PINNACLE_DEVIG_V1',"
                "'EXTERNAL_BOOKMAKER_VALUATION','PINNACLE','pinnacle',"
                "'power','PMUS',$1,$2,'Chicago Cubs','baseball','h2h',"
                "'{}'::jsonb,2,2,0.7,now() - interval '3 hours',"
                "now() - interval '3 hours','NO_TRADE',false)",
                _COND, "aec-mlb-chc-mia-2026-09-24-cubs")
            got = await W.seed_acceptance_position(
                c, experiment_id=_EXP, now=_T0,
                odds=_odds([_event(observed_at=_T0 - 5)], at=_T0),
                resolve_identity=_resolver_ok,
                read_book=lambda slug: {"marketData": _BOOK})
            return got
        finally:
            await c.execute("DELETE FROM market_tokens WHERE condition_id=$1",
                            decoy)
            await c.execute("DELETE FROM markets WHERE condition_id=$1", decoy)
            await c.close()

    got = asyncio.run(run())
    assert got["created"] is True, got
    assert got["condition_id"] == _COND, (
        "the market with a KNOWN venue contract must be tried first; the "
        "decoy was updated later and has none")
    # THE EVIDENCED ONE WAS FIRST, and the decoy was never examined at all.
    assert got["considered"][0]["condition_id"] == _COND
    assert got["considered"][0]["venue_slug_seen"] == \
        "aec-mlb-chc-mia-2026-09-24-cubs"
    assert got["candidates_with_a_known_venue_contract"] >= 1
    assert got["pool_blocker"] is None
    # AND THE CENSUS SAYS WHERE THE POOL NARROWED, per covered sport.
    pool = {r["sport"]: r for r in got["pool"]}
    assert pool["MLB"]["open_markets"] >= 2
    assert pool["MLB"]["with_both_tokens"] >= 2
    assert pool["MLB"]["with_venue_contract"] >= 1


@pg
def test_a_pool_with_no_mapped_market_says_so_by_name():
    """No venue contract anywhere is a NAMED blocker, not a silent miss."""
    import asyncpg

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c)
            # no external_valuations row carries a slug for this condition
            got = await W.seed_acceptance_position(
                c, experiment_id=_EXP, now=_T0,
                odds=_odds([_event(observed_at=_T0 - 5)], at=_T0),
                resolve_identity=_resolver_ok,
                read_book=lambda slug: {"marketData": _BOOK})
            return got
        finally:
            await c.close()

    got = asyncio.run(run())
    # The market still premaps through the resolver, so it IS entered --
    # the preference is not a filter. What must be true is that the absence
    # of evidence is REPORTED.
    assert got["candidates_with_a_known_venue_contract"] == 0
    assert got["pool_blocker"] == \
        "NO_COVERED_MARKET_HAS_A_KNOWN_VENUE_NATIVE_CONTRACT"
    assert got["budget_s"] == W.ACCEPTANCE_BUDGET_S


# ── THE GATE READS THE API, SO TEST THE API'S OWN SHAPE ──────────────
#
# Run 38's acceptance step reported three items missing on decisions that
# had every one of them: the deployed challenger loop made seven ranked
# decisions on the acceptance position, each with a probability 1-12 s old
# against its 30 s bound and a priced DIRECT_EXIT it beat, and the gate
# still said HELD_EXPOSURE_MAPPING, VENUE_CONTRACT_AND_INTENT and
# EXIT_LADDER_AND_DEPTH were absent.
#
# They were not absent. `command_rn1x.trace` did not SELECT `input_chain`,
# so the three items the gate reads off the chain were invisible THROUGH
# THE READ. The earlier coupling test built its own SELECT and therefore
# could not catch it.
#
# This one runs the shipped gate over the PRODUCTION READ's own output.

@pg
def test_the_trace_read_carries_what_the_shipped_gate_requires():
    import json
    import pathlib
    import re
    import shutil
    import subprocess

    import asyncpg

    from sportsassets.api import command_rn1x as CR
    from sportsassets.workers import ext_pinnacle_loop as EXT

    if shutil.which("jq") is None:
        pytest.skip("jq is not installed here")
    wf = (pathlib.Path(__file__).resolve().parents[2]
          / ".github" / "workflows" / "command-verify.yml")
    if not wf.exists():
        pytest.skip("the workflow is not in this checkout")
    prog = re.search(r"ACC='(.*?)'\n", wf.read_text(), re.S).group(1)

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        orig_resolve, orig_book = (EXT.resolve_venue_identity,
                                   EXT._read_book_blocking)
        EXT.resolve_venue_identity = _resolver_ok
        EXT._read_book_blocking = lambda slug: {"marketData": _BOOK}
        try:
            await _fixture(c)
            pid = await _position(c)
            for k, at in ((0, _T0 + 10), (1, _T0 + 130)):
                await W.manage_open_positions(
                    c, read_rules=_rules(_RULES_AGREEING), experiment_id=_EXP, now=at,
                    odds=_odds([_event(observed_at=at - 10)],
                               received_at=at - 9, at=at))
            # THE PRODUCTION READ ITSELF -- asyncpg's Connection exposes the
            # same fetch/fetchrow/fetchval the route hands a pool.
            return await CR.trace(c, pid)
        finally:
            EXT.resolve_venue_identity = orig_resolve
            EXT._read_book_blocking = orig_book
            await c.close()

    with _book_void_rule(_VOID_REFUND):
        got = asyncio.run(run())
    assert got.get("found") is True, got

    def j(v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:                                  # noqa: BLE001
                return {}
        return v or {}

    # normalised EXACTLY as command-verify normalises it
    rows = [{"id": d["decision_id"], "ts": str(d["decision_ts"]),
             "action": d.get("selected_action") or "NULL",
             "qty": d.get("selected_qty"),
             "hold": d.get("hold_value_usd"),
             "reason": d.get("selection_reason") or "",
             "reconciles": d.get("accounting_reconciles"),
             "pays": j(d.get("payout_identity")).get("row_payout_event"),
             "chain": j(d.get("input_chain")),
             "alt": j(d.get("alternatives")),
             "inv": j(d.get("resulting_inventory")),
             "fr": j(d.get("input_freshness"))}
            for d in got["decisions"]]
    assert len(rows) >= 2, rows
    out = subprocess.run(["jq", "-c", prog], input=json.dumps(
        {"decisions": rows}, default=float), capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    verdict = json.loads(out.stdout)
    missing = {m for v in verdict for m in v["missing"]}
    assert not missing, (
        "the PRODUCTION READ does not carry what the shipped gate needs: %s"
        % sorted(missing))
    assert len([v for v in verdict if not v["missing"]]) >= 2
    assert len({v["ts"] for v in verdict if not v["missing"]}) >= 2


# ── UNRESOLVED OR CONFLICTING SETTLEMENT CANNOT PASS ─────────────────
#
# `4b_SETTLEMENT_COMPATIBILITY` was recorded as nonblocking with the note
# that it blocks HOLD_TO_SETTLEMENT only -- while `ev_hold` valued the
# ordinary HOLD at p x qty realised AT SETTLEMENT. The same dependency,
# under a different action name.
#
# The value is still computed: refusing to compute it would assert the
# position is worthless, which is the assertion most likely to force an
# exit. What changes is that the condition travels with the number and the
# acceptance gate will not call such a row a complete, verified
# HOLD-versus-exit comparison.
#
# These rows are otherwise COMPLETE: same fixture, same fresh probability,
# same ladder, same residual, same ranking, same reconciled accounting.
# Only the settlement evidence differs.

def _gate_missing(rows):
    """The shipped gate's verdict over normalised decision rows."""
    import json
    import pathlib
    import re
    import shutil
    import subprocess

    if shutil.which("jq") is None:
        pytest.skip("jq is not installed here")
    wf = (pathlib.Path(__file__).resolve().parents[2]
          / ".github" / "workflows" / "command-verify.yml")
    if not wf.exists():
        pytest.skip("the workflow is not in this checkout")
    prog = re.search(r"ACC='(.*?)'\n", wf.read_text(), re.S).group(1)
    out = subprocess.run(["jq", "-c", prog], input=json.dumps(
        {"decisions": rows}, default=float), capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _normalised_trace(conn_rows):
    import json

    def j(v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:                                  # noqa: BLE001
                return {}
        return v or {}
    return [{"id": d["decision_id"], "ts": str(d["decision_ts"]),
             "action": d.get("selected_action") or "NULL",
             "qty": d.get("selected_qty"),
             "hold": d.get("hold_value_usd"),
             "reason": d.get("selection_reason") or "",
             "reconciles": d.get("accounting_reconciles"),
             "pays": j(d.get("payout_identity")).get("row_payout_event"),
             "chain": j(d.get("input_chain")),
             "alt": j(d.get("alternatives")),
             "inv": j(d.get("resulting_inventory")),
             "fr": j(d.get("input_freshness"))}
            for d in conn_rows]


def _two_cycles_with(prose, *, book_void):
    """Two management cycles under one settlement-evidence condition."""
    import asyncpg

    from sportsassets.api import command_rn1x as CR
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
            for at in (_T0 + 10, _T0 + 130):
                await W.manage_open_positions(
                    c, read_rules=_rules(prose), experiment_id=_EXP, now=at,
                    odds=_odds([_event(observed_at=at - 10)],
                               received_at=at - 9, at=at))
            return await CR.trace(c, pid)
        finally:
            EXT.resolve_venue_identity = orig_resolve
            EXT._read_book_blocking = orig_book
            await c.close()

    with _book_void_rule(book_void):
        got = asyncio.run(run())
    assert got.get("found") is True, got
    return _normalised_trace(got["decisions"])


@pg
def test_settlement_unresolved_blocks_a_complete_comparison():
    """Prose that says NOTHING about the terminal case: every other item is
    present and the row is CONDITIONAL, never complete."""
    rows = _two_cycles_with(_RULES_SILENT, book_void=None)
    verdict = _gate_missing(rows)
    assert len(verdict) >= 2, verdict
    for v in verdict:
        assert v["missing"] == ["SETTLEMENT_COMPATIBILITY_ESTABLISHED"], v
    assert not [v for v in verdict if not v["missing"]], (
        "an unresolved terminal rule must not produce a COMPLETE row")
    # and the condition is ON THE STORED VALUE, not merely in a comment
    tr = rows[0]["alt"]["hold_input"]["terminal_rule"]
    assert tr["asked"] is True and tr["established"] is False
    assert tr["book_rule"] == "FULL_GAME_INCLUDING_EXTRA_INNINGS"
    assert "ORDINARY HOLD TOO" in tr["governs"]
    assert rows[0]["alt"]["hold_input"]["value_is_conditional"] is True
    assert rows[0]["hold"] is not None, (
        "the value is still COMPUTED -- refusing would assert the position "
        "is worthless")
    ex = [c for c in rows[0]["alt"]["ranked"] if c["action"] == "HOLD"]
    assert ex and ex[0]["value_is_conditional"] is True
    assert ex[0]["conditional_on"], ex[0]


@pg
def test_settlement_conflicting_blocks_a_complete_comparison():
    """Prose that CONTRADICTS the book rule is louder than unknown, and it
    cannot pass either."""
    rows = _two_cycles_with(_RULES_CONFLICTING, book_void=_VOID_REFUND)
    verdict = _gate_missing(rows)
    assert len(verdict) >= 2, verdict
    for v in verdict:
        assert v["missing"] == ["SETTLEMENT_COMPATIBILITY_ESTABLISHED"], v
    tr = rows[0]["alt"]["hold_input"]["terminal_rule"]
    assert tr["established"] is False and tr["conflicts"] is True
    assert "OVERTIME_RULE_CONFLICTS_WITH_BOOK_RULE" in tr["unmet"], tr
    # the chain records it too, on the decision's own stored evidence
    l4b = next(x for x in rows[0]["chain"]["chain"]
               if x["link"] == "4b_SETTLEMENT_COMPATIBILITY")
    assert l4b["ok"] is False and l4b["conflicts"] is True
    assert l4b["venue_rules_text_read"] is True


@pg
def test_settlement_established_from_the_venues_own_prose_passes():
    """When the venue publishes prose that agrees with the book rule AND the
    bookmaker rule is held, the row is COMPLETE -- so the requirement is a
    real gate and not an unreachable one."""
    rows = _two_cycles_with(_RULES_AGREEING, book_void=_VOID_REFUND)
    verdict = _gate_missing(rows)
    assert len([v for v in verdict if not v["missing"]]) >= 2, verdict
    assert len({v["ts"] for v in verdict if not v["missing"]}) >= 2
    tr = rows[0]["alt"]["hold_input"]["terminal_rule"]
    assert tr["established"] is True and tr["unmet"] == []
    assert rows[0]["alt"]["hold_input"]["value_is_conditional"] is False
    l4b = next(x for x in rows[0]["chain"]["chain"]
               if x["link"] == "4b_SETTLEMENT_COMPATIBILITY")
    assert l4b["ok"] is True
    assert l4b["venue_rules_field"] == "description"


# ── RESEEDING ACROSS A PROCESS RESTART ───────────────────────────────
#
# THE DEFECT. The sentinel came from `hash(("ACCEPTANCE", slug))`, and
# Python salts `hash` for str per interpreter (PYTHONHASHSEED is random by
# default). So the "idempotent" identifier changed at every restart: the
# next deploy would have written a SECOND position on the same market while
# the route reported idempotence, and this very repair would have created a
# replacement instead of adopting the position already being managed.
#
# This runs the seeder in REAL separate interpreters, with two different
# hash seeds, against the row created here.

@pg
def test_reseeding_across_a_process_restart_adopts_the_same_position():
    import json
    import os
    import subprocess
    import sys

    import asyncpg

    child = (
        "import asyncio, json, sys\n"
        "sys.path.insert(0, %r)\n"
        "import asyncpg\n"
        "from sportsassets.workers import rn1x_shadow as W\n"
        "async def main():\n"
        "    c = await asyncpg.connect(%r, timeout=10)\n"
        "    try:\n"
        "        got = await W.seed_acceptance_position(\n"
        "            c, experiment_id=%r)\n"
        "    finally:\n"
        "        await c.close()\n"
        "    print(json.dumps({k: got.get(k) for k in\n"
        "        ('created', 'adopted', 'position_id', 'source_trade_id',\n"
        "         'sentinel_scheme', 'decisions_so_far', 'released_qty')}))\n"
        "    print(json.dumps({'stable': W.acceptance_sentinel(%r),\n"
        "        'salted': -(abs(hash(('ACCEPTANCE', %r))) %% 2000000000)}))\n"
        "asyncio.run(main())\n"
        % (str(pathlib.Path(__file__).resolve().parents[1]), DSN, _EXP,
           _SLUG, _SLUG))

    async def setup():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c)
            got = await W.seed_acceptance_position(
                c, read_rules=_rules(_RULES_AGREEING), experiment_id=_EXP,
                now=_T0, odds=_odds([_event(observed_at=_T0 - 5)], at=_T0),
                resolve_identity=_resolver_ok,
                read_book=lambda slug: {"marketData": _BOOK})
            row = dict(await c.fetchrow(
                "SELECT seed_qty::float8 q, seed_basis_usd::float8 b,"
                " count(*) OVER () AS n FROM rn1x_positions"
                " WHERE policy = $1", W.ACCEPTANCE_POLICY))
            return got, row
        finally:
            await c.close()

    first, before = asyncio.run(setup())
    assert first["created"] is True, first

    outs = []
    for seed in ("0", "1"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        r = subprocess.run([sys.executable, "-c", child], env=env,
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr[-2000:]
        lines = [x for x in r.stdout.strip().splitlines() if x.startswith("{")]
        outs.append((json.loads(lines[0]), json.loads(lines[1])))

    async def after():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            return dict(await c.fetchrow(
                "SELECT count(*) n, sum(seed_qty)::float8 q,"
                " sum(seed_basis_usd)::float8 b FROM rn1x_positions"
                " WHERE policy = $1", W.ACCEPTANCE_POLICY))
        finally:
            await c.close()

    end = asyncio.run(after())

    # ── the same position, from two fresh interpreters ────────────────
    for got, sent in outs:
        assert got["adopted"] is True, got
        assert got["created"] is False, got
        assert got["position_id"] == first["position_id"], got
        assert got["sentinel_scheme"] == W.ACCEPTANCE_SENTINEL_NAMESPACE
    # ── and NO inventory was added ───────────────────────────────────
    assert end["n"] == 1, "a restart must not mint a second position"
    assert end["q"] == pytest.approx(before["q"])
    assert end["b"] == pytest.approx(before["b"])

    # ── the identity is stable; the one it replaced was not ──────────
    stable = {s["stable"] for _, s in outs}
    salted = {s["salted"] for _, s in outs}
    assert len(stable) == 1, "the sentinel must not vary across processes"
    assert stable.pop() == W.acceptance_sentinel(_SLUG)
    assert len(salted) == 2, (
        "this test is only meaningful if hash() really does vary across "
        "interpreters here; it did not, so the defect is not reproduced")

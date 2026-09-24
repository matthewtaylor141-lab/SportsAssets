"""THE FOUR DEFECTS INDEPENDENT INSPECTION FOUND IN DEPLOYED 5b19bc5.

All four were in the PRODUCTION CONNECTION -- the builder, the identity
check, the cycle and the freshness bound -- and none of them could be
caught by a test that hands already-correct prices to the selector. These
exercise the real path: builder -> ranking -> lifecycle -> store.

  1 EXIT PRICES WERE ACQUISITION COSTS. `challenger_inputs_for` asked
    `acquisition_ladder` for the opposite intent and passed that ladder's
    acquisition price through as `bid`. On YES bid .60 / ask .63 it
    supplied, for a held long, bid .40 and complement_ask .63. Both are
    wrong: exiting a long pays the raw bid .60, and neutralising it costs
    .40. For a held short: exit .37, complement .63 -- the builder had
    .63 and .40, i.e. exactly transposed.

  2 THE IDENTITY CHECK COULD NOT FAIL. `payout_event_held` was read off
    the valuation row and handed back to `bettor_hold_value` for
    comparison against that same row. Any row for the condition passed,
    including one describing the OPPOSING exposure.

  3 A POSITION WAS DECIDED ONCE, EVER. `cycle` skipped any seed already
    written and `Managed` lived inside that one call, so there was no
    refreshed book, no re-decision and no later fill.

  4 A 30-MINUTE-OLD PROBABILITY WAS ADMISSIBLE IN PLAY. The 1800 s bound
    was justified by pre-match stability and no pre-match restriction was
    enforced.
"""
import asyncio

import pytest

from sportsassets import bettor_book_snapshot as BS
from sportsassets import bettor_hold_value as HV
from sportsassets import bettor_mgmt_lifecycle as LC

LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"
PAYS = "Chicago Cubs"

#: YES bid .60 / ask .63 -- the book the reproduction used.
BOOK = {"bids": [{"px": {"value": "0.6000"}, "qty": "500"},
                 {"px": {"value": "0.5800"}, "qty": "300"}],
        "offers": [{"px": {"value": "0.6300"}, "qty": "900"},
                   {"px": {"value": "0.6500"}, "qty": "400"}]}


def _fee(qty, price, maker=False):
    return 0.01 * float(qty) * float(price)


# ── 1 · EXITING IS NOT ACQUIRING ─────────────────────────────────────

def test_a_held_long_exits_at_the_bid_not_at_the_shorts_cost():
    x = BS.exit_ladder(BOOK, held_intent=LONG)
    assert x["ok"] is True
    assert x["best_exit_price"] == pytest.approx(0.60), (
        "exiting a long pays the raw YES bid")
    assert x["best_complement_price"] == pytest.approx(0.40), (
        "neutralising it costs 1 - bid")
    # THE NUMBER THE BUILDER USED TO SUPPLY AS `bid`
    assert x["best_exit_price"] != pytest.approx(0.40)
    # QUANTITY COMES FROM THE SIDE THAT PAYS -- the bids.
    assert x["size_at_best"] == pytest.approx(500.0)
    assert x["side_consumed"] == "BID"


def test_a_held_short_exits_at_one_minus_the_ask():
    x = BS.exit_ladder(BOOK, held_intent=SHORT)
    assert x["best_exit_price"] == pytest.approx(0.37)
    assert x["best_complement_price"] == pytest.approx(0.63)
    # the builder had these exactly transposed
    assert x["best_exit_price"] != pytest.approx(0.63)
    assert x["size_at_best"] == pytest.approx(900.0)
    assert x["side_consumed"] == "ASK"


def test_the_two_prices_sum_to_one_at_every_level():
    """Not a coincidence: on a one-signed-net venue receiving q and
    paying (1 - q) to neutralise are the same trade."""
    for held in (LONG, SHORT):
        x = BS.exit_ladder(BOOK, held_intent=held)
        for lv in x["levels"]:
            assert lv["exit_price"] + lv["complement_price"] == \
                pytest.approx(1.0, abs=1e-9)


def test_every_ladder_level_is_converted_not_just_the_top():
    """§: "Correct both scalar prices and every ladder level used for
    sizing." A sizing walk against a hold hurdle reads the levels."""
    x = BS.exit_ladder(BOOK, held_intent=LONG)
    assert [lv["exit_price"] for lv in x["levels"]] == \
        [pytest.approx(0.60), pytest.approx(0.58)]
    # best exit FIRST, which is the reverse of cheapest-acquisition-first
    assert x["levels"][0]["exit_price"] > x["levels"][1]["exit_price"]
    sale = BS.as_sale_ladder(x)
    assert [lv["acquisition_price"] for lv in sale["levels"]] == \
        [pytest.approx(0.60), pytest.approx(0.58)], (
        "the sizing walker compares `acquisition_price` against the hold "
        "value, so it must be handed EXIT PROCEEDS in that field")
    assert sale["price_space"] == "EXIT_PROCEEDS_PER_CONTRACT"


def test_an_absent_exit_side_is_refused_by_name():
    x = BS.exit_ladder({"offers": BOOK["offers"]}, held_intent=LONG)
    assert x["ok"] is False
    assert x["refusal"]


# ── 4 · FRESHNESS IS BOUNDED BY THE EVENT STATE ──────────────────────

def _row(age, now=10_000.0):
    return {"id": 1, "probability": 0.70, "probability_event": PAYS,
            "payout_event": PAYS, "payout_is_complement": False,
            "observed_at": now - age, "received_at": now - age,
            "eligibility": "ELIGIBLE"}


def _ev(age, state=None, **kw):
    return HV.ev_hold(qty=100.0, basis_per_contract=0.57,
                      probability_row=_row(age), now=10_000.0,
                      payout_event_held=PAYS, event_state=state, **kw)


def test_the_strict_bound_is_the_repositorys_own_in_play_bound():
    from sportsassets import bettor_progress_feed as feed
    from sportsassets import bettor_rn1x_policy as pol

    assert HV.MAX_PROBABILITY_AGE_S == 120.0
    assert HV.MAX_PROBABILITY_AGE_S == feed.MAX_AGE_S
    assert HV.MAX_PROBABILITY_AGE_S == pol.PROGRESS_MAX_AGE_S, (
        "a probability must not be allowed to be staler than the "
        "progress reading beside it")


def test_a_half_hour_old_probability_is_refused_in_play():
    """THE DEFECT. 1800 s was justified by pre-match stability and
    applied with no pre-match restriction."""
    assert _ev(1700.0, "IN_PLAY")["refusal"] == HV.R_STALE
    assert _ev(300.0, "IN_PLAY")["refusal"] == HV.R_STALE
    assert _ev(60.0, "IN_PLAY")["status"] == "IDENTIFIED"


def test_an_unknown_event_state_gets_the_strict_bound():
    """Absence of evidence is not evidence of pre-match."""
    for state in (None, "", "UNKNOWN", "something-unrecognised"):
        r = _ev(1700.0, state)
        assert r["refusal"] == HV.R_STALE, state
        assert r["age_bound_s"] == pytest.approx(120.0), state


def test_a_break_is_not_pre_match():
    """Play is stopped; the market is not. The restart reprices it."""
    for state in ("BREAK", "SUSPENDED"):
        assert _ev(1700.0, state)["refusal"] == HV.R_STALE
        assert _ev(1700.0, state)["age_bound_s"] == pytest.approx(120.0)


def test_pre_match_gets_the_longer_bound_only_when_declared():
    r = _ev(1700.0, "PRE_MATCH")
    assert r["status"] == "IDENTIFIED"
    assert r["age_bound_s"] == pytest.approx(1800.0)
    assert r["event_state"] == "PRE_MATCH"


def test_a_settled_or_void_event_is_refused_outright():
    for state in ("FINAL", "ABANDONED"):
        r = _ev(5.0, state)
        assert r["refusal"] == HV.R_EVENT_SETTLED
        assert r["ev_hold_usd"] is None


def test_a_caller_can_tighten_the_bound_but_never_widen_it():
    wide = _ev(1700.0, "IN_PLAY", max_age_s=99_999.0)
    assert wide["refusal"] == HV.R_STALE
    assert wide["age_bound_s"] == pytest.approx(120.0)
    tight = _ev(60.0, "PRE_MATCH", max_age_s=30.0)
    assert tight["refusal"] == HV.R_STALE
    assert tight["age_bound_s"] == pytest.approx(30.0)


# ── 3 · RELOADING IS A REPLAY, AND TWO REBUILDS AGREE ────────────────

def _position(**kw):
    base = {"position_id": "P1", "condition_id": "0xc",
            "outcome_index": 0, "seed_qty": 100.0, "seed_price": 0.57,
            "decision_ts": 1000.0, "policy": "SHADOW_CHALLENGER_HOLD_RANKED_V1"}
    base.update(kw)
    return base


def _orders_and_fills():
    orders = [{"order_id": "O1", "condition_id": "0xc", "outcome_index": 0,
               "side": "SELL", "intent": "EXIT", "limit_price": 0.90,
               "qty": 50.0, "filled_qty": 20.0, "state": "PARTIALLY_FILLED",
               "placed_at": 1001.0, "updated_at": 1002.0,
               "decision_id": "d1"}]
    fills = [{"order_id": "O1", "at": 1002.0, "qty": 20.0, "price": 0.91,
              "fee_usd": 0.18, "evidence_id": "trade:1"}]
    return orders, fills


def test_a_reload_reproduces_inventory_AND_the_working_order():
    orders, fills = _orders_and_fills()
    m = LC.reload_managed(position=_position(), orders=orders, fills=fills,
                          fee_fn=_fee)
    # the sale actually moved the book
    assert m.held(m.leg) == pytest.approx(80.0)
    assert m.residual() == pytest.approx(80.0)
    # AND THE RESTING ORDER IS BACK, which is what keeps the
    # one-active-order discipline true across cycles. Without it a new
    # cycle would place a second order without cancelling the first.
    assert [o.order_id for o in m.open_orders()] == ["O1"]
    assert m.open_orders()[0].remaining == pytest.approx(30.0)
    assert m.pf.invariant()["ok"] is True
    assert m.reloaded["fills_replayed"] == 1


def test_two_independent_reloads_are_identical():
    """Restart recovery: the ledger, not memory, is the state."""
    orders, fills = _orders_and_fills()
    a = LC.reload_managed(position=_position(), orders=orders, fills=fills,
                          fee_fn=_fee)
    b = LC.reload_managed(position=_position(), orders=orders, fills=fills,
                          fee_fn=_fee)
    assert a.state()["portfolio"] == b.state()["portfolio"]
    assert a.residual() == pytest.approx(b.residual())
    assert [o.order_id for o in a.open_orders()] == \
        [o.order_id for o in b.open_orders()]


def test_a_replayed_print_cannot_fill_twice():
    """`Consumption` is keyed on the evidence id, so re-offering a print
    the ledger already recorded takes nothing."""
    orders, fills = _orders_and_fills()
    m = LC.reload_managed(position=_position(), orders=orders, fills=fills,
                          fee_fn=_fee)
    before = m.residual()
    again = m.on_print(at=1003.0, outcome_index=0, price=0.91, size=400.0,
                       evidence_id="trade:1")
    assert not again, "the same print filled a second time"
    assert m.residual() == pytest.approx(before)


def test_a_terminal_order_does_not_come_back_open():
    orders, fills = _orders_and_fills()
    orders[0]["state"] = "CANCELLED"
    m = LC.reload_managed(position=_position(), orders=orders, fills=fills,
                          fee_fn=_fee)
    assert m.open_orders() == []


# ── the pieces the production path must bind against ─────────────────

def test_the_builders_signature_requires_the_positions_own_identity():
    """§2: the identity may not be taken from the quote. The builder now
    REQUIRES the seeded outcome index, so a caller cannot omit it and
    fall back to whatever the valuation says."""
    import inspect

    from sportsassets.workers import rn1x_shadow as W

    sig = inspect.signature(W.challenger_inputs_for)
    sig.bind(None, condition_id="0xc", outcome_index=0, seed_qty=1.0,
             seed_price=0.5)
    with pytest.raises(TypeError):
        sig.bind(None, condition_id="0xc", seed_qty=1.0, seed_price=0.5)
    # and it no longer derives the payout event from the row
    src = "\n".join(l.split("#", 1)[0] for l in
                    inspect.getsource(W.challenger_inputs_for).splitlines())
    assert 'payout_event_held = ident["payout_event"]' in src
    assert 'payout_event = v.get("payout_event")\n' not in src


def test_the_builder_uses_the_exit_ladder_not_a_bare_acquisition_one():
    import inspect

    from sportsassets.workers import rn1x_shadow as W

    src = "\n".join(l.split("#", 1)[0] for l in
                    inspect.getsource(W.challenger_inputs_for).splitlines())
    assert "exit_ladder(" in src
    assert 'bid=xl["best_exit_price"]' in src
    assert 'complement_ask=xl["best_complement_price"]' in src
    # the transposed pair that shipped
    assert 'sale["best_acquisition_price"]' not in src


def test_the_management_phase_exists_and_is_driven_every_cycle():
    import inspect

    from sportsassets.workers import rn1x_shadow as W

    assert hasattr(W, "manage_open_positions")
    src = inspect.getsource(W.cycle)
    assert "manage_open_positions(" in src
    assert '"management": managed' in src


def test_the_continuing_record_carries_the_positions_own_policy():
    """THE BUG THE FIRST TWO-CYCLE RUN CAUGHT. `persist_run` derives the
    position_id from out["policy"]["policy_id"]; omitting it resolved to
    UNKNOWN_POLICY and every continuing decision landed on a SECOND,
    fabricated position row."""
    from sportsassets import bettor_rn1x_run as R

    rec = R.manage_open_position(
        position=_position(policy="SHADOW_CHALLENGER_HOLD_RANKED_V1"),
        orders=[], fills=[], prints=[], inputs=None, now=2000.0,
        fee_fn=_fee)
    assert rec["policy"]["policy_id"] == "SHADOW_CHALLENGER_HOLD_RANKED_V1"
    assert rec["cycle"] == "CONTINUING_MANAGEMENT"
    # a cycle with no inputs still records a decision, by name
    d = rec["all_decisions"][0]
    assert d["operating_state"] in ("HOLD_FOR_MISSING_INPUT",
                                   "HOLD_BY_FALLBACK_RULE")
    assert rec["steps"]["MANAGE"]["decisions_without_inputs"] == 1
    # and it does not claim to have opened the position
    assert rec["steps"]["SEED"]["replayed_not_reopened"] is True


def test_the_decision_ordinal_appends_rather_than_colliding():
    import inspect

    from sportsassets import bettor_rn1x_store as S

    assert "decision_offset" in str(inspect.signature(S.persist_run))
    src = inspect.getsource(S.persist_run)
    assert 'did = "%s:D%04d" % (pid, i + int(decision_offset))' in src


def test_freshness_is_recomputed_per_decision_not_once_per_position():
    import inspect

    from sportsassets.workers import rn1x_shadow as W

    assert hasattr(W, "_ev_at")
    src = inspect.getsource(W._replay_one)
    assert "_ev_at(_s, at)" in src, (
        "the snapshot must carry the ROW and re-age it at each decision")


# ── THE REAL PATH, AGAINST A REAL DATABASE ───────────────────────────
#
# Everything above exercises the pieces. These exercise the CONNECTION:
# builder -> ranking -> lifecycle -> store, twice, on one position, with
# the book moving between cycles and no new RN1 entry. A test that hands
# already-correct prices to the selector cannot detect defect 1, and a
# test that runs one cycle cannot detect defect 3.

import os

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

_SLUG = "aec-mlb-chc-mia-2026-09-24-cubs"
_EXP = "RN1X_SHADOW_CHALLENGER_HOLD_RANKED_V1"
_POL = "SHADOW_CHALLENGER_HOLD_RANKED_V1"
_T0 = 1_800_000_000.0

_B_TIGHT = {"bids": [{"px": {"value": "0.6000"}, "qty": "500"}],
            "offers": [{"px": {"value": "0.6300"}, "qty": "900"}]}
_B_RALLY = {"bids": [{"px": {"value": "0.8500"}, "qty": "40"},
                     {"px": {"value": "0.8000"}, "qty": "600"}],
            "offers": [{"px": {"value": "0.8800"}, "qty": "900"}]}

_VAL_SQL = """INSERT INTO external_valuations(experiment_id,version,
 source_class,provider,book,devig_method,venue,condition_id,us_market_slug,
 contract_selection,sport_family,market,raw_odds,outcomes_priced,
 expected_outcomes,probability,observed_at,received_at,decision,admissible,
 payout_event,probability_event,payout_is_complement,buy_intent,
 matched_side_norm,resolver_asked_for,ladder_side,settlement_rule)
 VALUES('EXT','PINNACLE_DEVIG_V1','EXTERNAL_BOOKMAKER_VALUATION','PINNACLE',
 'pinnacle','power','PMUS',$1,$2,$3,'baseball','h2h','{}'::jsonb,2,2,$4,
 to_timestamp($5),to_timestamp($5),'NO_TRADE',false,$3,$3,false,
 'ORDER_INTENT_BUY_LONG','x',$3,'ASK','NINE_INNINGS')"""


async def _fixture(c, cond):
    # ONE STATEMENT PER CALL: asyncpg prepares, and a prepared statement
    # cannot carry multiple commands.
    for sql in ("DELETE FROM rn1x_fills",
                "DELETE FROM rn1x_orders",
                "DELETE FROM rn1x_decisions",
                "DELETE FROM rn1x_positions"):
        await c.execute(sql)
    # AND LEAVE NO CURSOR BEHIND. These fixtures insert `trades` rows,
    # and a cycle that sees them advances the lane cursors in
    # `ingestion_state`. Leaving those advanced contaminated a shared
    # test DSN and made two unrelated persistence tests read
    # IDLE_NO_CANDIDATES -- which I first mistook for a regression in my
    # own change. The residue is mine to clear.
    await c.execute(
        "DELETE FROM ingestion_state WHERE key LIKE 'rn1x%cursor'")
    for sql in ("DELETE FROM external_valuations WHERE condition_id = $1",
                "DELETE FROM market_tokens WHERE condition_id = $1",
                "DELETE FROM trades WHERE condition_id = $1",
                "DELETE FROM markets WHERE condition_id = $1"):
        await c.execute(sql, cond)
    await c.execute("INSERT INTO markets(condition_id,slug,resolved) "
                    "VALUES($1,'s',false)", cond)
    for i, n, t in ((0, "Chicago Cubs", cond + "-t0"),
                    (1, "Miami Marlins", cond + "-t1")):
        await c.execute(
            "INSERT INTO market_tokens(token_id,condition_id,outcome,"
            "outcome_index) VALUES($1,$2,$3,$4) "
            "ON CONFLICT (token_id) DO NOTHING", t, cond, n, i)
    await c.execute(
        "INSERT INTO rn1x_experiments(experiment_id,code_version,seed_rule,"
        "policy_register,execution_basis) "
        "VALUES($1,'v','{}'::jsonb,'{}'::jsonb,'b') "
        "ON CONFLICT DO NOTHING", _EXP)


async def _add_position(c, cond, trade_id):
    from sportsassets import bettor_rn1x_store as store
    pid = store.position_id(_EXP, _POL, trade_id)
    await c.execute(
        """INSERT INTO rn1x_positions(position_id,experiment_id,policy,
        source_trade_id,source_account,condition_id,outcome_index,
        entry_kind,entry_kind_why,seed_qty,seed_price,seed_basis_usd,
        source_ts,detected_ts,decision_ts,available_at,decision_basis,
        decision_lag_s) VALUES($1,$2,$3,$4,'RN1',$5,0,'NEW','seeded',
        100,0.57,57.0,to_timestamp($6),to_timestamp($6),to_timestamp($6),
        to_timestamp($6),'RUNTIME_WALL_CLOCK',0)
        ON CONFLICT (position_id) DO NOTHING""",
        pid, _EXP, _POL, trade_id, cond, _T0)
    return pid


@pg
def test_the_builder_supplies_exit_proceeds_through_the_real_path():
    """THE DEFECT, END TO END. Not a unit check on the ladder: the
    builder reads a real valuation row, resolves the real identity and
    hands the ranking real numbers."""
    import asyncpg

    from sportsassets.workers import ext_pinnacle_loop as EXT
    from sportsassets.workers import rn1x_shadow as W

    cond = "0xtest_exit"

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        orig = EXT._read_book_blocking
        EXT._read_book_blocking = lambda slug: {"marketData": _B_TIGHT}
        try:
            await _fixture(c, cond)
            await _add_position(c, cond, 990101)
            await c.execute(_VAL_SQL, cond, _SLUG, "Chicago Cubs", 0.70, _T0)
            return await W.challenger_inputs_for(
                c, condition_id=cond, outcome_index=0,
                seed_qty=100.0, seed_price=0.57)
        finally:
            EXT._read_book_blocking = orig
            await c.close()

    ci = asyncio.run(run())
    assert ci["available"] is True and ci["book_available"] is True
    # YES bid .60 / ask .63, held long
    assert ci["bid"] == pytest.approx(0.60), "the exit pays the raw bid"
    assert ci["complement_ask"] == pytest.approx(0.40)
    assert ci["bid_size"] == pytest.approx(500.0), "bid-side quantity"
    # the numbers the shipped builder supplied
    assert ci["bid"] != pytest.approx(0.40)
    assert ci["complement_ask"] != pytest.approx(0.63)
    # and the sizing ladder is in proceeds space
    assert ci["sale_ladder"]["levels"][0]["acquisition_price"] == \
        pytest.approx(0.60)
    # identity came from the POSITION, not the quote
    assert ci["position_identity"]["basis"] == \
        "MARKET_TOKENS_OUTCOME_FOR_THE_SEEDED_INDEX"
    assert ci["identity_matched_independently"] is True


@pg
def test_a_valuation_for_the_opposing_exposure_is_refused():
    """§2. The only eligible row prices the OTHER side. It must not
    silently become this position's identity."""
    import asyncpg

    from sportsassets.workers import ext_pinnacle_loop as EXT
    from sportsassets.workers import rn1x_shadow as W

    cond = "0xtest_other"

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        orig = EXT._read_book_blocking
        EXT._read_book_blocking = lambda slug: {"marketData": _B_TIGHT}
        try:
            await _fixture(c, cond)
            await _add_position(c, cond, 990201)
            await c.execute(_VAL_SQL, cond, _SLUG, "Miami Marlins", 0.30,
                            _T0)
            ci = await W.challenger_inputs_for(
                c, condition_id=cond, outcome_index=0,
                seed_qty=100.0, seed_price=0.57)
            mg = await W.manage_open_positions(c, experiment_id=_EXP,
                                               now=_T0 + 60)
            return ci, mg
        finally:
            EXT._read_book_blocking = orig
            await c.close()

    ci, mg = asyncio.run(run())
    assert ci["position_identity"]["payout_event"] == "Chicago Cubs"
    assert ci["valuation_payout_event"] == "Miami Marlins"
    assert ci["available"] is False
    assert ci["reason"] == W.R_VALUATION_IS_THE_OTHER_SIDE
    assert ci["probability_row"] is None, (
        "a probability for the event we LOSE on is worse than none")
    # the cycle still ran and recorded the blindness by name
    r = [x for x in mg["results"] if x.get("input_reason")]
    assert r and r[0]["input_available"] is False
    assert r[0]["input_reason"] == W.R_VALUATION_IS_THE_OTHER_SIDE


@pg
def test_one_position_is_managed_across_two_cycles_with_no_new_entry():
    """§3. THE SAME position, twice, with the book moving between --
    which is what the shipped lane could not do at all."""
    import asyncpg

    from sportsassets import bettor_mgmt_lifecycle as LCX
    from sportsassets import bettor_rn1x_store as store
    from sportsassets.workers import ext_pinnacle_loop as EXT
    from sportsassets.workers import rn1x_shadow as W

    cond = "0xtest_two"
    book = {"md": _B_TIGHT}

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        orig = EXT._read_book_blocking
        EXT._read_book_blocking = lambda slug: {"marketData": book["md"]}
        try:
            await _fixture(c, cond)
            pid = await _add_position(c, cond, 990301)
            await c.execute(_VAL_SQL, cond, _SLUG, "Chicago Cubs", 0.70,
                            _T0)
            # CYCLE 1 -- bid .60 is below the .70 hold value
            c1 = await W.manage_open_positions(c, experiment_id=_EXP,
                                               now=_T0 + 30)
            # the market rallies; the probability is refreshed so it stays
            # inside the 120 s bound
            book["md"] = _B_RALLY
            await c.execute(_VAL_SQL, cond, _SLUG, "Chicago Cubs", 0.70,
                            _T0 + 50)
            c2 = await W.manage_open_positions(c, experiment_id=_EXP,
                                               now=_T0 + 60)
            rows = [dict(r) for r in await c.fetch(
                "SELECT decision_id, extract(epoch FROM decision_ts)::float8"
                " ts, selected_action, selected_qty::float8 q,"
                " operating_state, accounting_reconciles"
                " FROM rn1x_decisions WHERE position_id = $1"
                " ORDER BY decision_ts, decision_id", pid)]
            npos = await c.fetchval("SELECT count(*) FROM rn1x_positions")
            # RESTART RECOVERY of that same position, from the ledger
            pr = dict(await c.fetchrow(
                "SELECT position_id, condition_id, outcome_index, policy,"
                " seed_qty::float8 seed_qty, seed_price::float8 seed_price,"
                " extract(epoch FROM decision_ts)::float8 decision_ts"
                " FROM rn1x_positions WHERE position_id = $1", pid))
            led = await store.load_position(c, pid)
            return c1, c2, rows, npos, pr, led
        finally:
            EXT._read_book_blocking = orig
            await c.close()

    c1, c2, rows, npos, pr, led = asyncio.run(run())

    # ONE position row -- no duplicate minted by a continuing cycle
    assert npos == 1, "a continuing cycle fabricated a second position"
    # TWO decisions, at TWO REAL AND DIFFERENT instants
    assert len(rows) == 2, rows
    assert rows[0]["ts"] == pytest.approx(_T0 + 30)
    assert rows[1]["ts"] == pytest.approx(_T0 + 60)
    assert rows[0]["decision_id"].endswith("D0000")
    assert rows[1]["decision_id"].endswith("D0001"), (
        "the ordinal must APPEND; colliding on D0000 would silently drop "
        "every decision after the first")
    # the decision CHANGED because the inputs changed
    assert rows[0]["selected_action"] == "HOLD"
    assert rows[1]["selected_action"] in ("DIRECT_EXIT", "TAKE_COMPLEMENT")
    assert all(r["accounting_reconciles"] for r in rows)
    assert c1["managed"] == 1 and c2["managed"] == 1

    # RESTART RECOVERY: two rebuilds from the ledger agree, and the
    # working order comes back so one-active-order still holds.
    a = LCX.reload_managed(position=pr, orders=led["orders"],
                           fills=led["fills"], fee_fn=_fee)
    b = LCX.reload_managed(position=pr, orders=led["orders"],
                           fills=led["fills"], fee_fn=_fee)
    assert a.state()["portfolio"] == b.state()["portfolio"]
    assert len(a.open_orders()) <= 1, "one active management order"
    assert a.pf.invariant()["ok"] is True

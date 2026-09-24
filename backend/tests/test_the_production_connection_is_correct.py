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
from sportsassets import bettor_mgmt_select as MS

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


# ── 4 · FRESHNESS IS THE ODDS SOURCE'S OWN RULE ──────────────────────
#
# TWO CORRECTIONS, both mine. 1,800 s was justified by pre-match
# stability with no pre-match restriction enforced. I then replaced it
# with 120 s from `bettor_progress_feed.MAX_AGE_S` and argued that a
# probability must not be staler than the progress reading beside it --
# which is wrong, because those bounds measure DIFFERENT INPUTS: a
# period index is a discrete state, a price moves continuously. The
# applicable rule for THIS input already exists.

def _row(age, now=10_000.0):
    return {"id": 1, "probability": 0.70, "probability_event": PAYS,
            "payout_event": PAYS, "payout_is_complement": False,
            "observed_at": now - age, "received_at": now - age,
            "eligibility": "ELIGIBLE"}


def _ev(age, state=None, **kw):
    return HV.ev_hold(qty=100.0, basis_per_contract=0.57,
                      probability_row=_row(age), now=10_000.0,
                      payout_event_held=PAYS, event_state=state, **kw)


def test_the_bound_is_the_odds_engines_own_thirty_seconds():
    from sportsassets import bettor_pinnacle_devig as devig

    assert HV.MAX_PROBABILITY_AGE_S == 30.0
    assert HV.MAX_PROBABILITY_AGE_S == devig.MAX_QUOTE_AGE_S, (
        "the valuation path already refuses to price a contract on a "
        "quote older than this, and a hold valuation reading the same "
        "quote may not be laxer than the entry decision reading it")
    assert HV.FRESHNESS_SOURCE["from"] == \
        "bettor_pinnacle_devig.MAX_QUOTE_AGE_S"


def test_the_progress_feeds_bound_is_not_cited_for_odds():
    """THE CLAIM I WITHDREW. 120 s bounds a period-or-clock observation,
    not a price, and it establishes nothing about odds freshness."""
    from sportsassets import bettor_progress_feed as feed

    assert feed.MAX_AGE_S == 120.0
    assert HV.MAX_PROBABILITY_AGE_S != feed.MAX_AGE_S
    assert "PERIOD OR CLOCK OBSERVATION" in HV.FRESHNESS_SOURCE["not_from"]
    assert "establishes nothing about odds freshness" in \
        HV.FRESHNESS_SOURCE["not_from"]


def test_every_admissible_state_gets_the_established_bound():
    """No state is laxer by default -- not pre-match, not unknown."""
    for state in (None, "", "UNKNOWN", "IN_PLAY", "BREAK", "SUSPENDED",
                  "PRE_MATCH", "unrecognised"):
        b = HV.bound_for(state)
        assert b["bound_s"] == pytest.approx(30.0), state
        assert b["relaxation_applied"] is None, state
    assert _ev(60.0, "PRE_MATCH")["refusal"] == HV.R_STALE
    assert _ev(60.0, "IN_PLAY")["refusal"] == HV.R_STALE
    assert _ev(20.0, "IN_PLAY")["status"] == "IDENTIFIED"


def test_the_relaxation_is_off_unless_named_by_id():
    """A longer pre-match window is PLAUSIBLE and NOT MEASURED, so it is
    a separately versioned experiment a caller must request."""
    assert "EXPERIMENTAL" in HV.RELAXATION["status"]
    assert HV.RELAXATION["bound_s"] == 1800.0
    assert "not a measured one" in HV.RELAXATION["rationale"]
    assert HV.RELAXATION["is_not_established_by"]

    off = _ev(600.0, "PRE_MATCH")
    assert off["refusal"] == HV.R_STALE, "off by default"

    on = _ev(600.0, "PRE_MATCH", relaxation=HV.RELAXATION_ID)
    assert on["status"] == "IDENTIFIED"
    assert on["age_bound_s"] == pytest.approx(1800.0)
    # AND THE DECISION SAYS SO
    assert on["freshness_relaxation"] == HV.RELAXATION_ID
    assert on["bound_is_the_established_one"] is False


def test_the_relaxation_does_not_apply_in_play_or_under_a_wrong_id():
    live = _ev(600.0, "IN_PLAY", relaxation=HV.RELAXATION_ID)
    assert live["refusal"] == HV.R_STALE
    assert live["age_bound_s"] == pytest.approx(30.0)
    assert live["freshness_relaxation"] is None
    assert "only to a DECLARED PRE_MATCH" in \
        live["freshness_relaxation_refused"]

    bogus = _ev(600.0, "PRE_MATCH", relaxation="just-let-me-through")
    assert bogus["refusal"] == HV.R_STALE
    assert "not a declared relaxation" in \
        bogus["freshness_relaxation_refused"]


def test_a_settled_or_void_event_is_refused_outright():
    for state in ("FINAL", "ABANDONED"):
        r = _ev(5.0, state)
        assert r["refusal"] == HV.R_EVENT_SETTLED
        assert r["ev_hold_usd"] is None


def test_a_caller_can_tighten_the_bound_but_never_widen_it():
    wide = _ev(600.0, "IN_PLAY", max_age_s=99_999.0)
    assert wide["refusal"] == HV.R_STALE
    assert wide["age_bound_s"] == pytest.approx(30.0)
    tight = _ev(20.0, "IN_PLAY", max_age_s=5.0)
    assert tight["refusal"] == HV.R_STALE
    assert tight["age_bound_s"] == pytest.approx(5.0)


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


def test_the_worker_no_longer_builds_a_hold_value_at_all():
    """§: "reload inventory, apply newly admitted fills, establish the
    current residual and basis, then calculate every alternative against
    that same inventory at the actual decision time."

    `_ev_at` valued holding the position's SEED quantity and price, and
    the worker called it BEFORE the reload and the fills. It is deleted,
    not corrected: anything in this module runs before the reload by
    construction. The snapshot carries the ROW; `decide_challenger`
    values holding what we actually hold, when the decision is taken.
    """
    import inspect

    from sportsassets.workers import rn1x_shadow as W

    assert not hasattr(W, "_ev_at"), "the seed-sized helper is back"
    mgmt = inspect.getsource(W.manage_open_positions)
    assert "ev_hold" not in mgmt.replace("no pre-computed hold value", ""), \
        "the phase must not build a hold value before the reload"
    sig = inspect.signature(LC.Managed.decide_challenger)
    for p in ("probability_row", "event_state", "payout_event_held",
              "freshness_relaxation"):
        assert p in sig.parameters, p


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


@pytest.fixture(autouse=True)
def _forget_the_process_pacing():
    """The provider gate is PROCESS-WIDE by design, so it outlives a test.
    These fixtures run in a declared epoch and in no particular order, so
    one test's stamp can sit in another's future -- which is a fact about
    the fixtures, not about the gate.
    """
    from sportsassets.workers import rn1x_shadow as _W

    _W.odds_gate_reset()
    yield
    _W.odds_gate_reset()


@pytest.fixture(autouse=True, scope="module")
def _leave_no_residue():
    """CLEAN UP AFTER MYSELF, AT THE END AS WELL AS THE START.

    These fixtures insert `trades` rows; a cycle that sees them advances
    the lane cursors in `ingestion_state`. `_fixture` clears those before
    each test, but the LAST test still leaves them advanced -- and on a
    reused DSN that residue made two unrelated persistence tests read
    IDLE_NO_CANDIDATES on the NEXT run, which is exactly the mistake I
    already made once by hand.
    """
    yield
    if not DSN:
        return
    import asyncpg

    async def clean():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await c.execute(
                "DELETE FROM ingestion_state WHERE key LIKE 'rn1x%cursor'")
            for cond in ("0xtest_exit", "0xtest_other", "0xtest_two",
                         "0xtest_residual", "0xtest_contain"):
                for sql in ("DELETE FROM rn1x_fills",
                            "DELETE FROM rn1x_orders",
                            "DELETE FROM rn1x_decisions",
                            "DELETE FROM rn1x_positions",
                            "DELETE FROM external_valuations "
                            "WHERE condition_id = $1",
                            "DELETE FROM trades WHERE condition_id = $1",
                            "DELETE FROM market_tokens "
                            "WHERE condition_id = $1",
                            "DELETE FROM markets WHERE condition_id = $1"):
                    if "$1" in sql:
                        await c.execute(sql, cond)
                    else:
                        await c.execute(sql)
        finally:
            await c.close()

    asyncio.run(clean())


_SLUG = "aec-mlb-chc-mia-2026-09-24-cubs"
_EXP = "RN1X_SHADOW_CHALLENGER_HOLD_RANKED_V1"
_POL = "SHADOW_CHALLENGER_HOLD_RANKED_V1"
_BENCH = "MANAGEMENT_PAIR_091_STOP_16_V1"        # the frozen benchmark
_T0 = 1_800_000_000.0

_B_TIGHT = {"bids": [{"px": {"value": "0.6000"}, "qty": "500"}],
            "offers": [{"px": {"value": "0.6300"}, "qty": "900"}]}
_B_RALLY = {"bids": [{"px": {"value": "0.8500"}, "qty": "40"},
                     {"px": {"value": "0.8000"}, "qty": "600"}],
            "offers": [{"px": {"value": "0.8800"}, "qty": "900"}]}

# ── THE MANAGED PULL'S INPUTS, INJECTED ──────────────────────────────
#
# The management phase no longer reads `external_valuations`: that table
# is written by the ENTRY lane every 900 s and the odds rule is 30 s, so a
# priced management decision could only ever happen by coincidence. It
# PULLS instead. These tests therefore supply the provider payload and the
# venue catalogue the pull asks, rather than a valuation row.
#
# THE ODDS ARE SOLVED, NOT GUESSED: 1.40 / 3.1135938239247523 is the pair
# whose power de-vig returns EXACTLY 0.70 for the home side, so the
# reported case's 13.00-vs-10.40 arithmetic survives the change of input
# source instead of being quietly restated at some other probability.
_ODDS_P70 = (1.40, 3.1135938239247523)


def _pull(observed_at, *, odds=_ODDS_P70, received_at=None, calls=3,
          at=None):
    from tests.test_the_held_position_gets_its_own_inputs import (
        _event, _odds)

    ev = _event(observed_at=observed_at, cubs=odds[0], fish=odds[1])
    return _odds([ev], received_at=(received_at if received_at is not None
                                   else observed_at), calls=calls,
                 at=(at if at is not None else observed_at))


async def _resolver(conn, *, market_row, priced_outcome):
    from tests.test_the_held_position_gets_its_own_inputs import (
        _resolver_ok)

    return await _resolver_ok(conn, market_row=market_row,
                              priced_outcome=priced_outcome)


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
    # A REAL FIXTURE ROW. `bettor_venue_mapping.map_event` matches the
    # provider's teams against title + event_title, and
    # `family_for_label` reads `sport`, so a placeholder row would fail
    # link 3 for the wrong reason.
    await c.execute(
        "INSERT INTO markets(condition_id,slug,sport,title,event_title,"
        "resolved,closed,updated_at) VALUES($1,$2,'MLB',$3,$3,false,false,"
        "now())", cond, "mlb-chc-mia-2026-09-24",
        "Chicago Cubs vs Miami Marlins")
    # `trades.whale_id` is a foreign key. A fixture that inserts a print
    # needs the whale to exist first.
    await c.execute("INSERT INTO whales(id,address) VALUES(9,'0xwhale9') "
                    "ON CONFLICT (id) DO NOTHING")
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
        orig_res = EXT.resolve_venue_identity
        EXT._read_book_blocking = lambda slug: {"marketData": _B_TIGHT}
        try:
            await _fixture(c, cond)
            await _add_position(c, cond, 990201)
            await c.execute(_VAL_SQL, cond, _SLUG, "Miami Marlins", 0.30,
                            _T0)
            ci = await W.challenger_inputs_for(
                c, condition_id=cond, outcome_index=0,
                seed_qty=100.0, seed_price=0.57)
            # THE SAME QUESTION ON THE MANAGEMENT PATH, which no longer
            # reads that row at all. Its identity comes from the tokens
            # and the venue catalogue, so the way an opposing identity can
            # reach it is a RESOLVER that names the other side -- and that
            # is refused the same way, by comparing two independent
            # sources rather than adopting either.
            from tests.test_the_held_position_gets_its_own_inputs import (
                _resolver_other_side)
            EXT.resolve_venue_identity = _resolver_other_side
            mg = await W.manage_open_positions(
                c, experiment_id=_EXP, now=_T0 + 60,
                odds=_pull(_T0 + 50))
            return ci, mg
        finally:
            EXT._read_book_blocking = orig
            EXT.resolve_venue_identity = orig_res
            await c.close()

    ci, mg = asyncio.run(run())
    assert ci["position_identity"]["payout_event"] == "Chicago Cubs"
    assert ci["valuation_payout_event"] == "Miami Marlins"
    assert ci["available"] is False
    assert ci["reason"] == W.R_VALUATION_IS_THE_OTHER_SIDE
    assert ci["probability_row"] is None, (
        "a probability for the event we LOSE on is worse than none")
    # the cycle still ran and recorded the blindness by name -- and on the
    # management path the name is the LINK that refused, with the two
    # disagreeing events in its `why`
    r = [x for x in mg["results"] if x.get("input_reason")]
    assert r and r[0]["input_available"] is False
    assert r[0]["input_reason"] == W.R_RESOLVER_DISAGREES
    assert r[0]["first_failing_link"] == "2_VENUE_CONTRACT"
    assert mg["priced_holds"] == 0, (
        "an opposing identity must not produce a priced hold")


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
        orig_res = EXT.resolve_venue_identity
        EXT._read_book_blocking = lambda slug: {"marketData": book["md"]}
        EXT.resolve_venue_identity = _resolver
        try:
            await _fixture(c, cond)
            pid = await _add_position(c, cond, 990301)
            # NO VALUATION ROW IS WRITTEN. Each cycle pulls its own quote,
            # observed 10 s before the decision it prices.
            # CYCLE 1 -- exit .60 is below the .70 hold value
            c1 = await W.manage_open_positions(
                c, experiment_id=_EXP, now=_T0 + 30,
                odds=_pull(_T0 + 20, at=_T0 + 30))
            # the market rallies, and the second cycle's probability is
            # its own -- not the first cycle's, re-aged
            book["md"] = _B_RALLY
            # +120 s, the lane's real cadence: past the declared 45 s
            # provider interval, so this cycle asks for its own quote
            # instead of being served a cached one it would have to refuse.
            c2 = await W.manage_open_positions(
                c, experiment_id=_EXP, now=_T0 + 150,
                odds=_pull(_T0 + 140, at=_T0 + 150))
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
            EXT.resolve_venue_identity = orig_res
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


# ── HOLD IS VALUED ON WHAT WE ACTUALLY HOLD ──────────────────────────
#
# THE DEFECT, found by independent inspection of 12260cb. The continuing
# worker computed EV_HOLD from the position's SEED quantity and price,
# then `manage_open_position` reloaded the portfolio and applied newly
# admitted fills, and `decide_challenger` ranked the ACTUAL RESIDUAL
# against that seed-sized number. After a partial exit HOLD carried the
# value of contracts already sold.
#
# THE REPORTED CASE, before fees:
#     seed 100 @ .57 · residual 80 · p .70 · exit .72 on all 80
#     shipped HOLD 13.00 (100 contracts)  -> HOLD selected
#     correct HOLD 10.40 ( 80 contracts)  -> DIRECT_EXIT at 12.00 wins

def test_the_ranking_derives_holds_total_from_the_inventory_it_ranks():
    """The structural half of the fix. Even handed a seed-sized record,
    the ranking must size HOLD like every other alternative -- and say
    that the record disagreed."""
    seed_sized = HV.ev_hold(
        qty=100.0, basis_per_contract=0.57, probability_row=_row(10.0),
        now=10_000.0, payout_event_held=PAYS, event_state="IN_PLAY")
    assert seed_sized["ev_hold_usd"] == pytest.approx(13.0)

    r = MS.rank_with_hold(
        80.0, 0.57, ev_hold=seed_sized, bid=0.72, bid_size=80.0,
        fee_fn=lambda qty, price, maker=False: 0.0, venue="PMUS",
        us_market_slug="aec-x", held_is_long=True)

    hold = next(c for c in r["ranked"] if c["action"] == "HOLD")
    exit_ = next(c for c in r["ranked"] if c["action"] == "DIRECT_EXIT")
    assert hold["qty"] == pytest.approx(80.0)
    assert hold["value_usd"] == pytest.approx(10.40), (
        "HOLD must be valued on the 80 contracts being ranked")
    assert exit_["value_usd"] == pytest.approx(12.00)
    assert r["selected"] == "DIRECT_EXIT", (
        "this is the selection the shipped ordering got wrong")

    mm = r["hold_input"]["quantity_mismatch"]
    assert mm is not None, "a record built on another quantity must be named"
    assert mm["record_computed_on_qty"] == pytest.approx(100.0)
    assert mm["inventory_being_ranked_qty"] == pytest.approx(80.0)
    assert mm["derived_ev_hold_usd"] == pytest.approx(10.40)


def test_decide_challenger_values_hold_after_the_fills():
    """The ordering half. `decide_challenger` is handed the ROW and
    values holding the residual it has at that instant."""
    m = _managed_after_partial_exit()
    assert m.residual() == pytest.approx(80.0)
    d = m.decide_challenger(
        at=10_000.0, probability_row=_row(10.0), event_state="IN_PLAY",
        payout_event_held=PAYS, bid=0.72, bid_size=80.0, venue="PMUS",
        us_market_slug="aec-x", last_price=0.72, seconds_open=600.0,
        decision_id="d")
    # the decision records WHAT it valued
    assert d["hold_valued_on"]["qty"] == pytest.approx(80.0)
    assert d["hold_valued_on"]["after"] == \
        "RELOAD_AND_NEWLY_ADMITTED_FILLS"
    hi = d["hold_input"]
    assert hi["qty_valued"] == pytest.approx(80.0)
    assert hi["ev_hold_usd"] == pytest.approx(10.40)
    assert hi["ev_hold_basis"] == \
        "DERIVED_FROM_THE_INVENTORY_BEING_RANKED"
    assert hi["quantity_mismatch"] is None, (
        "valued in the right place, so there is nothing to reconcile")
    assert d["selected_action"] == "DIRECT_EXIT"
    assert d["selected_qty"] == pytest.approx(80.0)


def _managed_after_partial_exit():
    """100 @ .57, then 20 sold at .90 through a real order and fill."""
    m = LC.Managed(condition_id="0xc", outcome_index=0, seed_qty=100.0,
                   seed_price=0.57, at=1000.0, fee_fn=lambda qty, price,
                   maker=False: 0.0)
    m.place("DIRECT_EXIT", at=1001.0, price=0.90, qty=20.0,
            decision_id="x")
    m.on_print(at=1002.0, outcome_index=0, price=0.91, size=80.0,
               evidence_id="t1")
    return m


def test_realized_pnl_is_not_folded_into_any_alternative():
    """§: "Carry realized P&L consistently; do not include exited
    contracts in only one alternative." The 20 already sold are realized
    and sunk; no candidate may carry them, and HOLD must not be the one
    that does."""
    m = _managed_after_partial_exit()
    realized = m.pf.to_dict()["realized_pnl_usd"]
    assert realized > 0, "the partial exit realized something"
    d = m.decide_challenger(
        at=10_000.0, probability_row=_row(10.0), event_state="IN_PLAY",
        payout_event_held=PAYS, bid=0.72, bid_size=80.0, venue="PMUS",
        us_market_slug="aec-x", last_price=0.72, seconds_open=600.0,
        decision_id="d")
    for c in d["alternatives"]:
        assert c["qty"] <= 80.0 + 1e-9, c
        # no candidate may be inflated by the realized amount
        assert abs(c["value_usd"] - realized) > 1e-9 or c["value_usd"] == 0
    # realized P&L is reported SEPARATELY, on the inventory view
    assert d["resulting_inventory"]["realized_pnl_usd"] == \
        pytest.approx(realized)
    assert d["resulting_inventory"]["invariant_ok"] is True


@pg
def test_the_residual_hold_case_through_the_continuing_worker():
    """THE WHOLE PATH: worker -> reload -> fills -> valuation -> ranking
    -> persistence, on the reported numbers, then restart recovery of
    that same position."""
    import asyncpg

    from sportsassets import bettor_mgmt_lifecycle as LCX
    from sportsassets import bettor_rn1x_store as store
    from sportsassets.workers import ext_pinnacle_loop as EXT
    from sportsassets.workers import rn1x_shadow as W

    cond = "0xtest_residual"
    # exit proceeds .72 for 80 -> the bid side must show .72 with size 80
    book = {"bids": [{"px": {"value": "0.7200"}, "qty": "80"}],
            "offers": [{"px": {"value": "0.7400"}, "qty": "900"}]}
    # cycle 1's book pays .90, which exits 20 of the 100
    book1 = {"bids": [{"px": {"value": "0.9000"}, "qty": "20"},
                      {"px": {"value": "0.5000"}, "qty": "900"}],
             "offers": [{"px": {"value": "0.9200"}, "qty": "900"}]}
    cur = {"md": book1}

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        orig = EXT._read_book_blocking
        orig_res = EXT.resolve_venue_identity
        EXT._read_book_blocking = lambda slug: {"marketData": cur["md"]}
        EXT.resolve_venue_identity = _resolver
        try:
            await _fixture(c, cond)
            pid = await _add_position(c, cond, 991001)
            # EACH CYCLE PULLS ITS OWN QUOTE, observed 10 s before the
            # decision it prices. The odds pair de-vigs to EXACTLY .70, so
            # the reported case's 13.00-vs-10.40 arithmetic is the same
            # arithmetic after the change of input source.
            c1 = await W.manage_open_positions(
                c, experiment_id=_EXP, now=_T0 + 30,
                odds=_pull(_T0 + 20, at=_T0 + 30))
            # a print crosses the resting sell and fills 20
            await c.execute(
                "INSERT INTO trades(id,tx_hash,asset,whale_id,condition_id,"
                "outcome_index,side,size,price,notional,sport,ts,"
                "detected_at,source,dedupe_key) VALUES(991500,'0xtx',$3,9,"
                "$1,0,'BUY',80,0.91,72.8,'baseball',to_timestamp($2),"
                "to_timestamp($2),'chain','dk-991500')",
                cond, _T0 + 100, cond + "-t0")
            # cycle 2: the book now pays .72 on 80, and the quote is its
            # own
            cur["md"] = book
            # +120 s, the lane's real cadence: past the declared 45 s
            # provider interval, so this cycle asks for its own quote
            # instead of being served a cached one it would have to refuse.
            c2 = await W.manage_open_positions(
                c, experiment_id=_EXP, now=_T0 + 150,
                odds=_pull(_T0 + 140, at=_T0 + 150))
            rows = [dict(r) for r in await c.fetch(
                "SELECT decision_id, selected_action,"
                " selected_qty::float8 q, hold_value_usd,"
                " operating_state, accounting_reconciles,"
                " resulting_inventory->>'residual_qty' resid,"
                " alternatives->'ranked' ranked"
                " FROM rn1x_decisions WHERE position_id = $1"
                " ORDER BY decision_ts, decision_id", pid)]
            pr = dict(await c.fetchrow(
                "SELECT position_id, condition_id, outcome_index, policy,"
                " seed_qty::float8 seed_qty, seed_price::float8 seed_price,"
                " extract(epoch FROM decision_ts)::float8 decision_ts"
                " FROM rn1x_positions WHERE position_id = $1", pid))
            led = await store.load_position(c, pid)
            return c1, c2, rows, pr, led
        finally:
            EXT._read_book_blocking = orig
            EXT.resolve_venue_identity = orig_res
            await c.close()

    c1, c2, rows, pr, led = asyncio.run(run())

    assert c1["managed"] == 1 and c2["managed"] == 1
    assert len(rows) == 2, rows
    # cycle 2 ran AFTER the fill, so the residual is 80
    second = rows[1]
    assert float(second["resid"]) == pytest.approx(80.0), (
        "the decision must be taken on the post-fill inventory")
    # HOLD'S PERSISTED TOTAL AGREES WITH THE POST-FILL PORTFOLIO
    assert second["hold_value_usd"] == pytest.approx(10.40, abs=1e-6), (
        "0.70 x 80 - 0.57 x 80 = 10.40, not the seed-sized 13.00")
    # and every ranked alternative is sized to that same inventory
    ranked = second["ranked"]
    if isinstance(ranked, str):
        import json
        ranked = json.loads(ranked)
    assert ranked, second
    for cand in ranked:
        assert float(cand["qty"]) <= 80.0 + 1e-9, cand

    # RESTART RECOVERY OF THIS SAME CASE
    a = LCX.reload_managed(position=pr, orders=led["orders"],
                           fills=led["fills"], fee_fn=_fee)
    b = LCX.reload_managed(position=pr, orders=led["orders"],
                           fills=led["fills"], fee_fn=_fee)
    assert a.state()["portfolio"] == b.state()["portfolio"]
    assert a.pf.invariant()["ok"] is True
    # the rebuilt residual is what the decision was taken on
    assert a.residual() == pytest.approx(float(second["resid"]))
    # and a hold valuation on the rebuild agrees with the persisted one
    basis_per = (a.pf._leg(a.condition_id, a.leg)["cost"]
                 / max(a.held(a.leg), 1e-12))
    again = HV.ev_hold(qty=a.residual(), basis_per_contract=basis_per,
                       probability_row=_row(10.0), now=10_000.0,
                       payout_event_held=PAYS, event_state="IN_PLAY")
    assert again["ev_hold_usd"] == pytest.approx(
        second["hold_value_usd"], abs=1e-6)


# ── CONTAINMENT: THE AFFECTED EVIDENCE STAYS DISTINGUISHABLE ─────────
# §: "Keep affected evidence distinguishable." Migration 111 must hold
# exactly the challenger rows the fifth defect could have changed, and
# leave alone the ones it could not -- a containment that holds the whole
# arm proves nothing, and one that holds nothing contains nothing.

@pg
def test_migration_111_holds_exactly_the_rows_the_defect_could_change():
    import pathlib

    import asyncpg

    from sportsassets import bettor_rn1x_store as store
    from sportsassets.scripts import migrate

    sql = pathlib.Path(migrate.MIGRATIONS_DIR).joinpath(
        "111_contain_the_seed_sized_hold_and_the_wrong_freshness_bound.sql"
    ).read_text()
    cond = "0xtest_contain"

    async def run():
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await _fixture(c, cond)
            pid = await _add_position(c, cond, 992001)   # seed 100
            # four rows, differing only in what the defect could reach
            rows = (
                # residual == seed: valued on the right quantity anyway
                ("untouched", 100.0, 12.0),
                # residual < seed: the seed-sized hold could differ
                ("moved", 80.0, 13.0),
                # no residual recorded: cannot be shown to be sound
                ("silent", None, 13.0),
            )
            for did, resid, hv in rows:
                inv = ("null" if resid is None
                       else '{"residual_qty": %r}' % resid)
                await c.execute(
                    """INSERT INTO rn1x_decisions(decision_id,position_id,
                    decision_ts,selection_reason,alternatives,ev_basis,
                    hold_value_usd,resulting_inventory,input_freshness,
                    eligibility) VALUES($1,$2,to_timestamp($3),'r',
                    '{}'::jsonb,'b',$4,$5::jsonb,
                    '{"age_from_observation_s": 10.0}'::jsonb,'ELIGIBLE')""",
                    did, pid, _T0, hv, inv)
            # a fourth: fresh enough for the progress feed, stale for odds
            await c.execute(
                """INSERT INTO rn1x_decisions(decision_id,position_id,
                decision_ts,selection_reason,alternatives,ev_basis,
                hold_value_usd,resulting_inventory,input_freshness,
                eligibility) VALUES('stale',$1,to_timestamp($2),'r',
                '{}'::jsonb,'b',12.0,'{"residual_qty": 100.0}'::jsonb,
                '{"age_from_observation_s": 95.0}'::jsonb,'ELIGIBLE')""",
                pid, _T0)
            # THE FROZEN BENCHMARK, same table, other policy, residual
            # moved: it has no hold value to mis-size and must be left be.
            bpid = store.position_id(_EXP, _BENCH, 992002)
            await c.execute(
                """INSERT INTO rn1x_positions(position_id,experiment_id,
                policy,source_trade_id,source_account,condition_id,
                outcome_index,entry_kind,entry_kind_why,seed_qty,seed_price,
                seed_basis_usd,source_ts,detected_ts,decision_ts,
                available_at,decision_basis,decision_lag_s)
                VALUES($1,$2,$3,992002,'RN1',$4,0,'NEW','seeded',100,0.57,
                57.0,to_timestamp($5),to_timestamp($5),to_timestamp($5),
                to_timestamp($5),'RUNTIME_WALL_CLOCK',0)""",
                bpid, _EXP, _BENCH, cond, _T0)
            await c.execute(
                """INSERT INTO rn1x_decisions(decision_id,position_id,
                decision_ts,selection_reason,alternatives,ev_basis,
                hold_value_usd,resulting_inventory,input_freshness,
                eligibility) VALUES('bench',$1,to_timestamp($2),'r',
                '{}'::jsonb,'b',13.0,'{"residual_qty": 80.0}'::jsonb,
                '{"age_from_observation_s": 95.0}'::jsonb,'ELIGIBLE')""",
                bpid, _T0)
            async with c.transaction():
                await c.execute(sql)
            bench = await c.fetchval(
                "SELECT eligibility FROM rn1x_decisions "
                "WHERE decision_id = 'bench'")
            out = {r["decision_id"]: dict(r) for r in await c.fetch(
                "SELECT decision_id, eligibility, ineligible_reason "
                "FROM rn1x_decisions WHERE position_id = $1", pid)}
            return out, bench
        finally:
            await c.close()

    out, bench = asyncio.run(run())

    assert bench == "ELIGIBLE", (
        "the frozen benchmark never called bettor_hold_value and has no "
        "hold value to mis-size; containing it would be over-reach")
    assert out["untouched"]["eligibility"] == "ELIGIBLE", (
        "a decision taken while the residual still equalled the seed was "
        "valued on the right quantity; holding it would be over-reach")
    assert out["moved"]["eligibility"] == \
        "INELIGIBLE_HOLD_VALUED_ON_THE_SEED_NOT_THE_RESIDUAL"
    assert out["silent"]["eligibility"] == \
        "INELIGIBLE_HOLD_VALUED_ON_THE_SEED_NOT_THE_RESIDUAL"
    assert out["stale"]["eligibility"] == \
        "INELIGIBLE_PROBABILITY_STALE_UNDER_THE_ODDS_RULE"
    for k in ("moved", "silent", "stale"):
        assert out[k]["ineligible_reason"], k
        # the record is HELD, not deleted: the row is still there to read
        assert out[k]["eligibility"].startswith("INELIGIBLE_")

"""WHAT MAKING A MARKET ACTUALLY PAYS US — the fee we charge and the
maker/taker split, both measured on fills we already hold.

The owner's premise is that mirroring the whale's resting orders makes
us a market maker and that market making is near-guaranteed profit. A
maker does earn the spread and whatever the venue rebates; a maker also
carries inventory and is filled precisely when the market is coming to
him. Nothing in this file argues either way. It pins the readings:

  1. THE FEE IS CHARGED. Until this unit, every Polymarket US row in
     every published P&L was charged a literal zero while the copy lane
     is 100% taker on a venue that states a fee coefficient on every
     market. The venue's own commission is read out of the stored order
     receipt where it exists, the venue's schedule charges the row
     where it does not, and NOTHING falls back to zero.
  2. THE MAKER SIDE IS COMPARED WITH THE TAKER SIDE ON OUR OWN FILLS —
     per fill: aggressor, commission charged or rebated, per-dollar
     rate — never on a claim about market making in general.
  3. ANYTHING STILL GROSS IS LABELLED GROSS wherever it is served.

Every test here fails against HEAD a1aedc5.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from sportsassets.api import copies_record as cr
from sportsassets.api import proof2 as p2


# ── fixtures: the two shapes a stored receipt can carry ──────────────

def _amt(v):
    return {"value": v, "currency": "USD"}


def _wire(shares="10", px="0.50", commission=None, aggressor=None,
          eid=None, typ="EXECUTION_TYPE_FILL"):
    """One execution exactly as the venue's wire stores it under
    raw->response->executions (every lane persists this)."""
    ex = {"id": eid, "type": typ, "lastShares": shares, "lastPx": _amt(px)}
    if commission is not None:
        ex["commissionNotionalCollected"] = _amt(commission)
    if aggressor is not None:
        ex["aggressor"] = aggressor
    return ex


def _parsed(shares=10.0, px=0.50, commission=None, aggressor=None,
            eid=None, typ="EXECUTION_TYPE_FILL"):
    """One execution as `pmus._execution_record` parsed it — the shape
    that lands under raw->final->executions on the rest lane and under
    raw->executions on the post-only (mirror) lane."""
    return {"id": eid, "type": typ, "last_shares": shares, "last_px": px,
            "aggressor": aggressor, "commission_usd": commission,
            "commission_spread_px": None}


def _row(venue="polymarket-us", shares=10.0, price=0.50, receipt=None,
         lane=None, pnl=0.0, stake=None):
    return {"venue": venue, "filled_shares": shares, "fill_price": price,
            "receipt": receipt, "lane": lane, "pnl": pnl,
            "filled_usd": (stake if stake is not None
                           else (shares or 0) * (price or 0))}


# ── 1. the fee is charged, and never to zero ─────────────────────────

def test_a_polymarket_us_fill_is_no_longer_charged_zero():
    """THE DEFECT THIS UNIT EXISTS FOR. `capture_from_rows` charged
    `kalshi_fee(...) if venue.startswith("kalshi") else 0.0`, so the
    one venue every copy goes to contributed a literal zero."""
    f = p2.row_fee(_row(shares=100.0, price=0.50))
    assert f["fee_usd"] > 0.0
    assert f["fee_source"] == p2.FEE_SCHEDULE
    # 0.06 x 100 x 0.5 x 0.5 = $1.50 on $50 of notional
    assert f["fee_usd"] == pytest.approx(1.50)


def test_the_venues_own_value_is_charged_when_the_receipt_carries_one():
    """Source 1: the commission the venue itself stated. It beats the
    schedule and it is not re-estimated."""
    r = _row(shares=10.0, price=0.50,
             receipt={"response": {"executions": [
                 _wire(shares="10", px="0.50", commission="0.07")]}})
    f = p2.row_fee(r)
    assert f["fee_usd"] == pytest.approx(0.07)
    assert f["fee_source"] == p2.FEE_VENUE
    assert f["n_venue_stated"] == 1
    # and it is NOT the schedule's number for the same fill
    assert p2.schedule_fee("polymarket-us", 10.0, 0.50) == pytest.approx(0.15)


def test_a_stated_zero_is_a_value_and_a_missing_one_is_not():
    """`pmus._commission_fields` already draws this line (zero is a
    value, not an absence). The charge must draw the same one: a venue
    that says the fill was free is believed; a venue that says nothing
    is charged the schedule."""
    stated = p2.row_fee(_row(receipt={"response": {"executions": [
        _wire(commission="0")]}}))
    assert stated["fee_usd"] == 0.0 and stated["fee_source"] == p2.FEE_VENUE
    silent = p2.row_fee(_row(receipt={"response": {"executions": [_wire()]}}))
    assert silent["fee_usd"] > 0.0
    assert silent["fee_source"] == p2.FEE_SCHEDULE


def test_the_parse_is_the_venue_modules_own_and_not_a_second_one():
    """The parser exists and ran on ONE path. This unit makes it run on
    the stored rows — through the same function, so its refusals (a
    bool is not one dollar, a NaN is not a fee) cannot drift."""
    from sportsassets import pmus

    seen = []
    real = pmus._commission_fields

    def _spy(rec):
        seen.append(rec)
        return real(rec)

    pmus._commission_fields = _spy
    try:
        p2.row_fee(_row(receipt={"response": {"executions": [
            _wire(commission="0.09")]}}))
    finally:
        pmus._commission_fields = real
    assert seen, "the stored receipt was parsed by a second implementation"


@pytest.mark.parametrize("bad", [True, "nan", "Infinity", "abc"])
def test_an_unparseable_commission_is_charged_the_schedule_not_zero(bad):
    """A value the venue module refuses is UNKNOWN, and unknown falls to
    the schedule — the one direction that cannot understate the cost."""
    f = p2.row_fee(_row(receipt={"response": {"executions": [
        {"type": "EXECUTION_TYPE_FILL", "lastShares": "10",
         "lastPx": _amt("0.50"),
         "commissionNotionalCollected": bad}]}}))
    assert f["fee_source"] == p2.FEE_SCHEDULE
    assert f["fee_usd"] == pytest.approx(0.15)


def test_a_rebate_keeps_its_sign_and_pays_us():
    """The maker rebate the owner is counting on: if the venue ever
    states one, it arrives as a negative commission and ADDS to the
    P&L. Nothing here assumes one exists — no rebate value has ever
    been observed from this venue."""
    f = p2.row_fee(_row(receipt={"response": {"executions": [
        _wire(commission="-0.04")]}}))
    assert f["fee_usd"] == pytest.approx(-0.04)
    assert f["fee_source"] == p2.FEE_VENUE
    net = cr.finish_net({"pnl": 10.0, "staked": 50.0, "fee_usd": -0.04,
                         "fee_measured_rows": 1, "fee_unmeasured_rows": 0})
    assert net["pnl_net"] == pytest.approx(10.04)


def test_a_venue_with_no_schedule_is_unmeasured_and_never_zero():
    """'polymarket-clob' is deliberately absent from the schedule
    table: its fee is not read anywhere in this repository, and a zero
    for it would be exactly the claim this unit removes."""
    f = p2.row_fee(_row(venue="polymarket-clob"))
    assert f["fee_usd"] is None
    assert f["fee_source"] == p2.FEE_UNMEASURED
    assert "no fee schedule" in f["why"]


def test_an_unreadable_price_is_unmeasured_not_free():
    for px in (None, 0.0, 1.0, 1.5, -0.2):
        f = p2.row_fee(_row(price=px))
        assert f["fee_usd"] is None, px
        assert f["fee_source"] == p2.FEE_UNMEASURED, px


def test_the_fee_is_symmetric_across_the_long_and_short_leg():
    """The venue's fill_price names the LONG leg, so a BUY_SHORT copy
    of a 0.10 entry is recorded at 0.90. p(1-p) is symmetric, so the
    charge is the same either way and no intent conversion is needed
    in the fee path."""
    assert p2.pmus_taker_fee(100, 0.10) == pytest.approx(
        p2.pmus_taker_fee(100, 0.90))


def test_the_fee_coefficient_cannot_be_set_from_a_shell():
    """A fee coefficient behind an environment read is a money bound a
    shell can set to zero — the class of lever the mirror's downward-
    only `capped_env` exists to close. This module reads no
    environment at all."""
    src = inspect.getsource(p2)
    assert "os.environ" not in src and "getenv" not in src
    assert p2.PMUS_FEE_COEFFICIENT == 0.06


def test_the_schedules_form_is_served_as_unverified():
    """The coefficient is the venue's own; the FORM is Kalshi's
    published shape and no PM-US commission value has ever been
    observed against it. Anything built on it is an ESTIMATE and says
    so wherever it is served."""
    note = cr.basis_note()
    assert note["pmus_fee_form_verified"] is False
    assert note["fee_schedule_form"] == p2.FEE_SCHEDULE_FORM
    assert note["pnl"] == cr.PRE_FEE


# ── 2. the census: bounded, timed, contained ─────────────────────────

def test_a_receipt_over_the_execution_bound_is_unreadable_not_truncated():
    """A partial reading of a receipt UNDER-charges the fee, which is
    the direction this unit exists to stop. Over the bound the receipt
    is unreadable and the row falls to the schedule off its own fill."""
    big = {"response": {"executions": [
        _wire(commission="0.01")
        for _ in range(p2.MAX_EXECUTIONS_PER_RECEIPT + 1)]}}
    assert p2.receipt_executions(big) is None
    f = p2.row_fee(_row(shares=100.0, price=0.50, receipt=big))
    assert f["fee_source"] == p2.FEE_SCHEDULE
    assert f["fee_usd"] == pytest.approx(1.50)


def test_the_census_refuses_over_its_row_cap():
    class _Pool:
        async def fetch(self, sql, *a, **k):        # pragma: no cover
            raise AssertionError("the census must refuse before querying")

    ids = list(range(p2.MAX_FEE_CENSUS_ROWS + 1))
    assert asyncio.run(p2.receipts_by_id(_Pool(), ids)) is None


def test_a_census_fault_costs_the_fee_and_never_the_page():
    """CONTAINED. A read that cannot be taken must not unwind into the
    handler: the record still builds, on the schedule, labelled."""
    class _Boom:
        async def fetch(self, sql, *a, **k):
            raise RuntimeError("no database")

    assert asyncio.run(p2.receipts_by_id(_Boom(), [1, 2])) is None

    class _Slow:
        async def fetch(self, sql, *a, **k):
            await asyncio.sleep(5)

    assert asyncio.run(
        p2.receipts_by_id(_Slow(), [1], timeout=0.01)) is None


def test_the_census_lifts_only_the_paths_it_needs():
    """Never the whole `raw` blob: it holds the venue's complete API
    response for every order."""
    sql = " ".join(p2.RECEIPT_SQL.split())
    assert "lo.raw #> '{response,executions}'" in sql
    assert "lo.raw #> '{final,executions}'" in sql
    assert "lo.raw -> 'executions'" in sql
    assert "lo.raw #> '{final,commission_usd}'" in sql
    assert "lo.raw AS" not in sql and "lo.raw," not in sql
    assert "$1::bigint[]" in sql, "keyed by explicit ids, so it is bounded"


def test_nothing_here_places_or_prices_an_order():
    """This unit only reads, charges and reports. Checked on the parse
    tree, so a mention in a comment is not a call and a call cannot
    hide in one."""
    import ast

    forbidden = {"submit_fok", "close_position", "cancel_order",
                 "create", "place", "execute"}
    for mod in (p2, cr):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (fn.attr if isinstance(fn, ast.Attribute)
                    else fn.id if isinstance(fn, ast.Name) else "")
            assert name not in forbidden, (mod.__name__, name)
        for lit in ast.walk(tree):
            if isinstance(lit, ast.Constant) and isinstance(lit.value, str):
                up = " ".join(lit.value.split()).upper()
                assert "INSERT INTO LIVE_ORDERS" not in up
                assert "UPDATE LIVE_ORDERS" not in up


# ── 3. the maker side, measured against the taker side ───────────────

def test_the_ioc_lane_is_a_taker_and_the_mirror_lane_is_a_maker():
    assert p2.structural_role(None, "placement") == p2.LANE_TAKER
    assert p2.structural_role("ioc", "terminal") == p2.LANE_TAKER
    assert p2.structural_role("mirror", "terminal") == p2.LANE_MAKER
    assert p2.structural_role("something-new", "terminal") == "unknown"


def test_a_rest_that_crossed_on_placement_is_a_taker_fill():
    """The rest lane's seed of genuine maker fills is real, and it is
    not the whole lane: a bid that filled BEFORE it ever rested took
    liquidity. The placement response is the tell — it is the one
    structural reading that separates the two, and it is in data we
    already hold."""
    assert p2.structural_role("rest", "placement") == p2.LANE_TAKER
    assert p2.structural_role("rest", "terminal") == p2.LANE_MAKER

    rows = [_row(lane="rest", receipt={
        "response": {"executions": [_wire(eid="x1", shares="4", px="0.50")]},
        "final": {"executions": [_parsed(eid="x1", shares=4.0, px=0.50),
                                 _parsed(eid="x2", shares=6.0, px=0.50)]}})]
    econ = p2.fill_economics(rows)
    roles = {(b["lane"], b["role"]): b for b in econ["by_lane"]}
    assert roles[("rest", "taker")]["n_fills"] == 1
    assert roles[("rest", "taker")]["shares"] == 4.0
    assert roles[("rest", "maker")]["n_fills"] == 1
    assert roles[("rest", "maker")]["shares"] == 6.0


def test_each_fill_names_aggressor_commission_and_the_per_dollar_rate():
    """The unit's second deliverable, exactly: per fill, whether we
    were the aggressor, the commission charged or rebated, and the
    rate per dollar.

    The receipt carries an EMPTY placement list, which is the venue
    affirming that nothing filled at create — the shape that makes
    'it appears only in the terminal read' a reading of resting rather
    than an absence."""
    rows = [_row(lane="rest", receipt={
        "response": {"executions": []},
        "final": {"executions": [
            _parsed(shares=100.0, px=0.50, commission=0.10,
                    aggressor=False)]}})]
    econ = p2.fill_economics(rows)
    fill = econ["fills"][0]
    assert fill["aggressor"] is False
    assert fill["role_venue_flag"] == p2.LANE_MAKER
    assert fill["role_structural"] == p2.LANE_MAKER
    assert fill["commission_usd"] == pytest.approx(0.10)
    assert fill["commission_source"] == p2.FEE_VENUE
    assert fill["notional_usd"] == pytest.approx(50.0)
    assert fill["rate_per_dollar"] == pytest.approx(0.002)


def test_the_maker_side_is_compared_with_the_taker_side_on_our_fills():
    """A per-lane per-dollar rate for each side, from our own
    receipts — not from a claim about what makers earn.

    BOTH SIDES HERE ARE THE VENUE'S OWN STATED VALUES, which is the one
    input that carries information about which side of the trade we
    were on, and the only state in which the comparison is published at
    all: see
    `test_the_maker_versus_taker_rate_is_not_published_off_the_schedule`
    for the state the system is actually in."""
    rows = [
        _row(lane="ioc", receipt={"response": {"executions": [
            _wire(shares="100", px="0.50", commission="1.50")]}}),
        _row(lane="rest", receipt={
            "response": {"executions": []},
            "final": {"executions": [
                _parsed(shares=100.0, px=0.50, commission=0.30)]}}),
    ]
    econ = p2.fill_economics(rows)
    by = {(b["lane"], b["role"]): b for b in econ["by_lane"]}
    assert by[("ioc", "taker")]["rate_per_dollar"] == pytest.approx(0.03)
    assert by[("rest", "maker")]["rate_per_dollar"] == pytest.approx(0.006)
    assert by[("ioc", "taker")]["rate_is_venue_measured"] is True
    assert "rate_caveat" not in by[("rest", "maker")]
    rc = econ["rate_comparison"]
    assert rc["comparable"] is True
    assert rc["maker_rate_per_dollar"] == pytest.approx(0.006)
    assert rc["taker_rate_per_dollar"] == pytest.approx(0.03)
    assert rc["maker_minus_taker_per_dollar"] == pytest.approx(-0.024)
    assert rc["sufficient_n"] is False, "two fills authorise nothing"
    assert "adverse selection" in rc["verdict"]


def test_a_lane_rate_below_its_minimum_n_says_so():
    """§3b: a metric below its minimum n authorises nothing. The rate
    still prints — with its n and the flag."""
    rows = [_row(lane="rest", receipt={"final": {"executions": [
        _parsed(commission=0.01)]}})]
    b = p2.fill_economics(rows)["by_lane"][0]
    assert b["n_fills"] == 1 and b["sufficient_n"] is False
    many = [_row(lane="rest", receipt={"final": {"executions": [
        _parsed(commission=0.01)]}}) for _ in range(p2.MIN_N_FOR_A_LANE_RATE)]
    assert p2.fill_economics(many)["by_lane"][0]["sufficient_n"] is True


def test_the_venue_flag_and_the_lane_are_two_readings_not_one():
    """The SDK's Execution.aggressor has never been observed carrying a
    value in production, so which side of the trade it names is
    unverified. A disagreement with the lane is PRINTED; neither
    reading is promoted over the other."""
    absent = p2.fill_economics([_row(lane="ioc", receipt={
        "response": {"executions": [_wire()]}})])
    assert absent["aggressor_flag"]["present"] == 0
    assert "unverified" in absent["aggressor_flag"]["verdict"]

    agrees = p2.fill_economics([_row(lane="ioc", receipt={
        "response": {"executions": [_wire(aggressor=True)]}})])
    assert agrees["aggressor_flag"]["agrees_with_lane"] == 1
    assert agrees["aggressor_flag"]["disagrees_with_lane"] == 0

    fights = p2.fill_economics([_row(lane="ioc", receipt={
        "response": {"executions": [_wire(aggressor=False)]}})])
    assert fights["aggressor_flag"]["disagrees_with_lane"] == 1
    assert "DISAGREES" in fights["aggressor_flag"]["verdict"]


def test_adverse_selection_is_named_unmeasured_with_the_read_that_settles_it():
    """The half of the owner's premise a commission reading cannot
    see. It is not softened and it is not guessed."""
    econ = p2.fill_economics([])
    assert econ["adverse_selection"].startswith("unmeasured")
    assert "price_path" in econ["adverse_selection"]
    assert "LOWER BOUND" in econ["fee_basis"]


def test_an_unreadable_receipt_is_counted_not_silently_dropped():
    econ = p2.fill_economics([_row(receipt=None), _row(receipt={"x": 1})])
    assert econ["n_rows"] == 2
    assert econ["n_rows_receipt_unreadable"] == 2
    assert econ["n_rows_read"] == 0


# ── 4. the served surfaces: charged, and labelled where still gross ──

def _settled(whale="rn1", pnl=10.0, stake=50.0, venue="polymarket-us",
             shares=100.0, price=0.50, day="2026-09-03"):
    return {"whale": whale, "day": day, "pnl": pnl, "filled_usd": stake,
            "venue": venue, "filled_shares": shares, "fill_price": price,
            "us_market_slug": "aec-atp-a-b-2026-09-03"}


def test_the_record_charges_the_fee_and_labels_the_gross_figure():
    rows = p2.charge_fees([_settled()])
    out = cr.scorecard(rows)
    t = out["total"]
    assert t["pnl"] == 10.0, "the gross figure keeps its meaning"
    assert t["pnl_basis"] == cr.PRE_FEE
    assert t["fee_usd"] == pytest.approx(1.50)
    assert t["pnl_net"] == pytest.approx(8.50)
    assert t["net_basis"] == cr.NET_OF_ENTRY_FEE
    assert t["roi_net"] == pytest.approx(8.50 / 50.0)
    assert t["pnl_net"] < t["pnl"], "the truthful fee makes it worse"
    assert out["basis"]["pnl"] == cr.PRE_FEE


def test_every_served_bucket_carries_its_own_basis():
    """A page showing one net number beside four gross ones reads as
    net throughout."""
    out = cr.scorecard(p2.charge_fees([_settled(), _settled(whale="kch123")]))
    for bucket in ([out["total"]] + out["by_whale"] + out["daily"]
                   + out["by_whale_sport"] + out["daily_by_whale"]):
        assert bucket["pnl_basis"] == cr.PRE_FEE
        assert "pnl_net" in bucket and "net_basis" in bucket


def test_an_unmeasured_row_is_counted_and_never_charged_zero():
    rows = p2.charge_fees([_settled(venue="polymarket-clob")])
    assert rows[0]["fee_usd"] is None
    t = cr.scorecard(rows)["total"]
    assert t["fee_unmeasured_rows"] == 1
    assert t["pnl_net"] is None and t["net_basis"] == "unreadable"


def test_a_partly_measured_cohort_serves_an_upper_bound():
    rows = p2.charge_fees([_settled(), _settled(venue="polymarket-clob")])
    t = cr.scorecard(rows)["total"]
    assert t["fee_measured_rows"] == 1 and t["fee_unmeasured_rows"] == 1
    assert t["pnl_net"] == pytest.approx(20.0 - 1.50)
    assert t["pnl_net_is_upper_bound"] is True


def test_the_kalshi_merge_never_inherits_a_stale_net():
    """`pnl` grows by the other venue's dollars. A `pnl_net` carried
    over unchanged would describe a smaller book than the `pnl` printed
    beside it — and the engine's export carries no fee figure at all,
    so its rows fold in as unmeasured."""
    pm = cr.scorecard(p2.charge_fees([_settled()]))["total"]
    merged = cr.merge_totals(pm, {"settled": 4, "wins": 2, "losses": 2,
                                  "pnl": 40.0, "staked": 100.0})
    assert merged["pnl"] == pytest.approx(50.0)
    assert merged["fee_unmeasured_rows"] == 4
    assert merged["pnl_net"] == pytest.approx(48.50)
    assert merged["pnl_net_is_upper_bound"] is True
    assert merged["pnl_net"] != pm["pnl_net"], "the net moved with the gross"


def test_the_ledger_line_carries_its_fee_its_source_and_its_basis():
    rows = p2.charge_fees([_settled()])
    line = cr.trades_list(rows, with_fees=True)[0]
    assert line["pnl_basis"] == cr.PRE_FEE
    assert line["fee_usd"] == pytest.approx(1.50)
    assert line["fee_source"] == p2.FEE_SCHEDULE
    assert line["pnl_net"] == pytest.approx(8.50)
    # the pinned shape for callers that do not ask is untouched
    assert "fee_usd" not in cr.trades_list(rows)[0]


def test_todays_scoreline_can_be_served_net():
    rows = p2.charge_fees([_settled(day="2026-09-03")])
    t = cr.today_stats(rows, "2026-09-03", with_fees=True)
    assert t["pnl"] == 10.0 and t["pnl_basis"] == cr.PRE_FEE
    assert t["pnl_net"] == pytest.approx(8.50)
    # and the four-key shape other callers pin is unchanged
    assert set(cr.today_stats(rows, "2026-09-03")) == {
        "pnl", "settled", "wins", "losses"}


def test_the_build_serves_the_labelled_ledger_and_the_net_scoreline():
    src = inspect.getsource(cr.build)
    assert "trades_list(windowed, with_fees=True)" in src
    assert "with_fees=True)" in src.split('out["today"]')[1]
    assert "charge_the_fee(pool, windowed, census)" in src


def test_the_exit_leg_is_named_unmeasured_where_the_net_is_served():
    """Half the round trip is not in data we hold, and the surface says
    so rather than implying the net is complete."""
    note = cr.basis_note()
    assert note["exit_leg_fee"].startswith("unmeasured")
    assert "UPPER bound" in note["exit_leg_fee"]


# ── 5. the two ways this reader could over- or under-charge ──────────

def test_the_terminal_read_never_charges_the_placement_fill_twice():
    """The rest lane stores the placement response AND the terminal
    `order_status` read, and the terminal read REPEATS the placement's
    executions. Joined on the venue's execution id, that is one fill."""
    receipt = {"response_execs": [_wire(eid="x1", shares="10", px="0.50",
                                        commission="0.05")],
               "final_execs": [_parsed(eid="x1", shares=10.0, px=0.50,
                                       commission=0.05),
                               _parsed(eid="x2", shares=5.0, px=0.50,
                                       commission=0.02)]}
    execs = p2.receipt_executions(receipt)
    assert [e["id"] for e in execs] == ["x1", "x2"]
    assert [e["stage"] for e in execs] == ["placement", "terminal"]
    assert p2.row_fee(_row(receipt=receipt))["fee_usd"] == pytest.approx(0.07)


def test_two_unjoinable_lists_are_unreadable_not_double_charged():
    """Without an id there is nothing to join on, and the same fill
    would be charged twice AND counted on both sides of the maker/taker
    split. Unreadable falls to the schedule off the row's own fill."""
    receipt = {"response_execs": [_wire(shares="10", px="0.50",
                                        commission="0.05")],
               "final_execs": [_parsed(shares=10.0, px=0.50,
                                       commission=0.05)]}
    assert p2.receipt_executions(receipt) is None
    f = p2.row_fee(_row(shares=10.0, price=0.50, receipt=receipt))
    assert f["fee_source"] == p2.FEE_SCHEDULE
    assert f["fee_usd"] == pytest.approx(0.15)
    # one list alone needs no join and is read as it stands
    assert len(p2.receipt_executions(
        {"response_execs": [_wire(shares="10", px="0.50")]})) == 1


def test_a_fill_with_no_price_is_unmeasured_on_both_sides_of_the_rate():
    """Its commission has no notional to divide by; charging one
    without the other inflates the lane's per-dollar rate."""
    econ = p2.fill_economics([_row(lane="rest", receipt={"final_execs": [
        {"type": "EXECUTION_TYPE_FILL", "last_shares": 10.0,
         "last_px": None, "commission_usd": 0.05, "aggressor": None}]})])
    b = econ["by_lane"][0]
    assert b["n_fills"] == 1 and b["n_unmeasured"] == 1
    assert b["commission_usd"] == 0.0 and b["notional_usd"] == 0.0
    assert b["rate_per_dollar"] is None
    assert b["commission_usd_is_partial"] is True


# ── 6. THE REVIEW'S FINDINGS, PINNED ─────────────────────────────────
#
# Every test below reproduces a defect a reviewer drove through these
# functions and demonstrated with a probe. Where the reviewer wrote a
# probe, the probe's scenario IS the test.


def test_an_unpriceable_row_does_not_dilute_the_cohorts_fee_rate():
    """BLOCKING. `fee_rate` summed the fee of the rows it could price
    and divided by the WHOLE cohort's entry notional, so every
    unmeasured row was charged a silent ZERO inside a figure served as
    `net of the entry-leg fee` — the exact defect this unit exists to
    remove, one layer up.

    The reviewer's probe: 100sh @ 0.50 on polymarket-us (priceable)
    beside 100sh @ 0.50 on polymarket-clob (no schedule). The served
    rate was 0.015; the priced row's own rate is 0.030."""
    rows = [{"his_price": 0.50, "fill_price": 0.50, "shares": 100.0,
             "venue": "polymarket-us", "whale": "rn1", "side": "BUY"},
            {"his_price": 0.50, "fill_price": 0.50, "shares": 100.0,
             "venue": "polymarket-clob", "whale": "rn1", "side": "BUY"}]
    cap = p2.capture_from_rows(rows)
    assert cap["fee_usd"] == pytest.approx(1.50)
    assert cap["fee_rate"] == pytest.approx(0.030), "not 0.015"
    assert cap["fee_charged_notional"] == pytest.approx(50.0)
    assert cap["fee_notional_share"] == pytest.approx(0.5)
    assert cap["fee_rows_unmeasured"] == 1


def test_the_dilution_used_to_flip_the_sign_of_the_owners_answer():
    """The number that changes is the one the owner reads. At the
    +2.07% benchmark the diluted rate served a POSITIVE sleeve edge
    where the measured rows' own rate makes it NEGATIVE."""
    rows = [{"his_price": 0.50, "fill_price": 0.50, "shares": 100.0,
             "venue": "polymarket-us", "whale": "rn1", "side": "BUY"},
            {"his_price": 0.50, "fill_price": 0.50, "shares": 100.0,
             "venue": "polymarket-clob", "whale": "rn1", "side": "BUY"}]
    bench = {"per_whale": {"rn1": {"edge_roi": 0.0207,
                                   "edge_ci95": [0.0200, 0.0214]}}}
    e = p2.combine(p2.capture_from_rows(rows), bench)
    assert e["sleeve_edge"] == pytest.approx(0.0207 - 0.030)
    assert e["sleeve_edge"] < 0, "the diluted answer was +0.0057"
    assert e["p_edge_positive"] < 0.5


def test_a_cohort_with_no_measurable_fee_publishes_no_net_sleeve_edge():
    """BLOCKING. With nothing priceable the fee term was the literal
    0.0 and `sleeve_edge` came back byte-identical to the pre-fee
    answer — still labelled net. There is no net edge to publish, so
    none is published."""
    rows = [{"his_price": 0.50, "fill_price": 0.50, "shares": 100.0,
             "venue": "polymarket-clob", "whale": "rn1", "side": "BUY"}]
    cap = p2.capture_from_rows(rows)
    assert cap["fee_rate"] is None
    assert cap["fee_rate_basis"].startswith("unreadable")
    e = p2.combine(cap, {"per_whale": {"rn1": {
        "edge_roi": 0.0207, "edge_ci95": [0.0200, 0.0214]}}})
    assert e["available"] is False
    assert "unmeasured on every row" in e["reason"]
    assert "sleeve_edge" not in e and "p_edge_positive" not in e
    assert p2.thesis(e, 1000.0)["available"] is False


def test_a_lane_rate_is_never_the_zero_of_an_unmeasured_lane():
    """BLOCKING. `rate_per_dollar` divided a numerator holding only the
    charged fills by a denominator holding every priced fill, so a lane
    where nothing was measurable served rate 0.0 — 'making a market
    costs us nothing per dollar', the owner's premise handed back as a
    measurement, on the instrument built to test it."""
    econ = p2.fill_economics([_row(lane="mirror", venue="polymarket-clob",
                                   receipt={"executions": [
                                       _parsed(shares=100.0, px=0.50)]})])
    b = econ["by_lane"][0]
    assert b["notional_usd"] == pytest.approx(50.0)
    assert b["n_unmeasured"] == 1
    assert b["rate_per_dollar"] is None, "not 0.0"
    assert b["charged_notional_usd"] == 0.0


def test_a_partly_measured_lane_rate_is_not_diluted_by_the_rest():
    """Two maker fills of equal notional, one on a venue with a
    schedule and one on a venue without: the lane's rate is the priced
    fill's own, not that fill's fee spread across both fills' dollars.
    Each lane's dilution was its own, so the maker-vs-taker COMPARISON
    that is this unit's second deliverable compared two numbers scaled
    by two unrelated fractions."""
    econ = p2.fill_economics([
        _row(lane="rest", venue="polymarket-us", receipt={
            "response_execs": [],
            "final_execs": [_parsed(shares=100.0, px=0.50, eid="a")]}),
        _row(lane="rest", venue="polymarket-clob", receipt={
            "response_execs": [],
            "final_execs": [_parsed(shares=100.0, px=0.50, eid="b")]}),
    ])
    b = econ["by_lane"][0]
    assert b["n_fills"] == 2 and b["n_unmeasured"] == 1
    assert b["rate_per_dollar"] == pytest.approx(0.03), "not 0.015"
    assert b["notional_usd"] == pytest.approx(100.0)
    assert b["charged_notional_usd"] == pytest.approx(50.0)
    assert b["commission_usd_is_partial"] is True


def test_the_venues_order_total_is_charged_even_when_the_row_has_fills():
    """MAJOR. Clause (1) is 'the venue's own stored commission first'.
    `_order_commission` was consulted ONLY when the receipt carried no
    executions, so a row whose executions state nothing ignored the
    order-level total the venue DID state and charged an unverified
    estimate over it. commissionNotionalTotalCollected is the field the
    SDK actually types on the Order."""
    receipt = {"final_execs": [_parsed(shares=10.0, px=0.50, eid="e1")],
               "final_commission_usd": 0.35}
    f = p2.row_fee(_row(shares=10.0, price=0.50, receipt=receipt))
    assert f["fee_usd"] == pytest.approx(0.35), "was 0.15, the estimate"
    assert f["fee_source"] == p2.FEE_VENUE
    assert f["n_venue_stated"] == 1


def test_an_order_total_beside_stated_executions_is_recorded_not_added():
    """It is the same order's money. Charging both would double-count,
    so the executions win and the discard is VISIBLE."""
    receipt = {"final_execs": [_parsed(shares=10.0, px=0.50, eid="e1",
                                       commission=0.04)],
               "final_commission_usd": 0.35}
    f = p2.row_fee(_row(receipt=receipt))
    assert f["fee_usd"] == pytest.approx(0.04)
    assert f["order_total_ignored"] is True


def test_one_unreadable_leg_does_not_void_the_readable_legs_fee():
    """MAJOR. One execution the schedule could not price returned None
    for the WHOLE row, throwing away the charge on every readable leg —
    under-charging, the direction this unit exists to stop. The
    reviewer's probe: [100sh @ 0.50 readable] + [5sh, no price]."""
    receipt = {"final_execs": [
        _parsed(shares=100.0, px=0.50, eid="e1"),
        {"type": "EXECUTION_TYPE_FILL", "id": "e2", "last_shares": 5.0,
         "last_px": None}]}
    f = p2.row_fee(_row(receipt=receipt))
    assert f["fee_usd"] == pytest.approx(1.50), "the readable leg's own"
    assert f["fee_is_partial"] is True
    assert f["n_exec_unmeasured"] == 1
    assert f["fee_source"] == p2.FEE_SCHEDULE


def test_the_refusal_names_the_cause_it_actually_had():
    """The `why` read \"venue 'polymarket-us' has no fee schedule\" when
    polymarket-us HAS one and the real cause was an unreadable price. A
    diagnostic that misnames its own refusal, on a reading that has to
    be vouchable."""
    priceless = p2.row_fee(_row(receipt={"final_execs": [
        {"type": "EXECUTION_TYPE_FILL", "last_shares": 5.0,
         "last_px": None}]}))
    assert priceless["fee_usd"] is None
    assert "no fee schedule" not in priceless["why"], \
        "polymarket-us HAS a schedule; the price was the problem"
    assert p2.WHY_PRICE_UNREADABLE in priceless["why"]
    no_sched = p2.row_fee(_row(venue="polymarket-clob", receipt={
        "final_execs": [_parsed(shares=5.0, px=0.50)]}))
    assert p2.WHY_NO_SCHEDULE in no_sched["why"]


def test_a_partial_row_is_in_the_dollars_but_not_in_the_rate():
    """Its dollars are worth keeping; its rate is not a rate."""
    rows = [{"his_price": 0.50, "fill_price": 0.50, "shares": 100.0,
             "venue": "polymarket-us", "whale": "rn1", "side": "BUY",
             "receipt": {"final_execs": [
                 _parsed(shares=100.0, px=0.50, eid="a"),
                 {"type": "EXECUTION_TYPE_FILL", "id": "b",
                  "last_shares": 5.0, "last_px": None}]}}]
    cap = p2.capture_from_rows(rows)
    assert cap["fee_usd"] == pytest.approx(1.50)
    assert cap["fee_rows_partial"] == 1
    assert cap["fee_rate"] is None, "a partial charge is not a rate"
    assert "PARTIAL" in cap["fee_basis"]


def test_the_entry_fee_is_charged_on_the_entry_not_on_the_remainder():
    """BLOCKING. `filled_shares` is decremented by every partial exit
    (live_executor.py:1530, :6222) while `filled_usd` never is, so a
    100-share $0.50 entry trimmed to 40 was charged $0.60 instead of
    $1.50 — and the same served bucket took `staked` from the full
    notional and the fee from the remainder. Migration 040's
    `orig_shares` is the entry's own count."""
    trimmed = {"venue": "polymarket-us", "filled_shares": 40.0,
               "orig_shares": 100.0, "fill_price": 0.50,
               "pnl": 5.0, "filled_usd": 50.0}
    assert p2.row_fee(trimmed)["fee_usd"] == pytest.approx(1.50)
    # a row without 040's column is unchanged: the remainder, as before
    no_orig = dict(trimmed)
    no_orig.pop("orig_shares")
    assert p2.row_fee(no_orig)["fee_usd"] == pytest.approx(0.60)


def test_a_capture_row_is_denominated_by_the_entry_it_actually_bought():
    """The fee, the drag and the entry notional all move to the same
    base, so a trimmed row cannot charge 100 shares of fee against 40
    shares of notional."""
    rows = [{"his_price": 0.50, "fill_price": 0.50, "shares": 40.0,
             "orig_shares": 100.0, "venue": "polymarket-us",
             "whale": "rn1", "side": "BUY"}]
    cap = p2.capture_from_rows(rows)
    assert cap["entry_notional"] == pytest.approx(50.0)
    assert cap["fee_usd"] == pytest.approx(1.50)
    assert cap["fee_rate"] == pytest.approx(0.03)


def test_a_fully_exited_row_is_unmeasured_and_never_a_free_no_fill():
    """BLOCKING. The mirror lane's only path off 'filled' fires WHERE
    filled_shares = 0 (_MIRROR_CLOSE_CASHED_OUT_SQL,
    live_executor.py:6309-6311), so a mirror book that bought and fully
    exited reached this reader with zero shares and an accumulated pnl
    — and was charged the literal $0.00 of FEE_NO_FILL, counted
    MEASURED, and served as `net_of_entry_fee`. That is the `else 0.0`
    this unit exists to delete, re-entering by another door, on the one
    lane the owner wants a net number for."""
    exited = {"whale": "rn1", "day": "2026-09-03", "venue": "polymarket-us",
              "filled_shares": 0.0, "fill_price": 0.50,
              "pnl": 7.5, "filled_usd": 50.0}
    f = p2.row_fee(exited)
    assert f["fee_usd"] is None and f["fee_source"] == p2.FEE_UNMEASURED
    t = cr.scorecard(p2.charge_fees([exited]))["total"]
    assert t["pnl"] == 7.5
    assert t["fee_measured_rows"] == 0 and t["fee_unmeasured_rows"] == 1
    assert t["pnl_net"] is None and t["net_basis"] == "unreadable"
    # a row that genuinely never filled is still a true zero
    never = {"venue": "polymarket-us", "filled_shares": 0.0,
             "fill_price": 0.50, "pnl": 0.0, "filled_usd": 0.0}
    assert p2.row_fee(never)["fee_source"] == p2.FEE_NO_FILL


def test_an_empty_placement_list_is_not_a_list_to_join_against():
    """MAJOR. `joinable` counted every list that was not None, so an
    EMPTY placement array flipped the guard on and threw the whole
    receipt away the moment an execution lacked an id — which is the
    normal stored shape of a genuine maker fill (a GTC that rested has
    no placement execution). An empty list can collide with nothing."""
    receipt = {"response_execs": [],
               "final_execs": [_parsed(shares=10.0, px=0.50,
                                       commission=0.05)]}
    execs = p2.receipt_executions(receipt)
    assert execs is not None and len(execs) == 1
    assert p2.row_fee(_row(receipt=receipt))["fee_usd"] == pytest.approx(0.05)
    econ = p2.fill_economics([_row(lane="rest", receipt=receipt)])
    assert econ["by_lane"] and econ["by_lane"][0]["role"] == p2.LANE_MAKER
    # two NON-EMPTY unjoinable lists are still refused
    assert p2.receipt_executions(
        {"response_execs": [_wire(shares="10", px="0.50")],
         "final_execs": [_parsed(shares=10.0, px=0.50)]}) is None


def test_a_mirror_order_that_crossed_is_not_counted_as_a_maker_fill():
    """MAJOR. `if lane == 'mirror': return maker` was unconditional
    while the rest lane got a stage test. Post-only is a raw env read
    (ml:272) and it LATCHES ITSELF OFF on a fill-at-create
    (ml:2288 _POST_ONLY_OK = False) — that latch exists because a
    mirror order can cross. The one reading the owner's
    'we become market makers' claim will be quoted from could not see
    the event that disproves it."""
    assert p2.structural_role("mirror", "placement") == p2.LANE_TAKER
    assert p2.structural_role("mirror", "post_only") == p2.LANE_TAKER
    assert p2.structural_role("mirror", "terminal") == p2.LANE_MAKER
    econ = p2.fill_economics([_row(lane="mirror", receipt={
        "response": {"executions": [_wire(shares="10", px="0.50",
                                          eid="m1")]}})])
    assert [(b["lane"], b["role"], b["n_fills"]) for b in econ["by_lane"]] \
        == [("mirror", "taker", 1)]


def test_a_fill_with_no_placement_list_to_read_is_not_a_maker_fill():
    """MAJOR. An ABSENT placement list was read as evidence of resting.
    raw.response is {} on the rest lane's lost-placement recovery path
    (live_executor.py ~6786), so RECEIPT_SQL's #> '{response,executions}'
    yields NULL and every fill on that row was stamped 'terminal' and
    published as a maker fill — the only mis-classification direction
    that flatters the owner's premise, and it feeds n_fills, which
    sufficient_n gates on."""
    econ = p2.fill_economics([_row(lane="rest", receipt={
        "final_execs": [_parsed(shares=100.0, px=0.50, commission=3.0,
                                eid="t1")]})])
    b = econ["by_lane"][0]
    assert b["role"] == "unknown", "not maker"
    assert econ["n_fills_stage_unknown"] == 1
    assert "stage_unknown" in econ["maker_side_is_vouchable"]
    # with the placement list present-and-empty it IS a reading
    ok = p2.fill_economics([_row(lane="rest", receipt={
        "response_execs": [],
        "final_execs": [_parsed(shares=100.0, px=0.50, commission=3.0,
                                eid="t1")]})])
    assert ok["by_lane"][0]["role"] == p2.LANE_MAKER
    assert ok["n_fills_stage_unknown"] == 0


def test_an_untyped_execution_is_not_a_fill():
    """MINOR. `pmus.submit_fok` counts a fill only when `type` is one
    of the two FILL types (pmus.py:2431). Admitting an untyped
    execution inflated the charge and `n_fills` — the count
    `sufficient_n` gates on."""
    assert p2._one_execution({"lastShares": "10", "lastPx": _amt("0.50")},
                             "placement") is None
    # and the row still charges: the schedule, off its own entry fill
    f = p2.row_fee(_row(shares=10.0, price=0.50, receipt={
        "response_execs": [{"lastShares": "10", "lastPx": _amt("0.50")}]}))
    assert f["fee_usd"] == pytest.approx(0.15)
    assert f["fee_source"] == p2.FEE_SCHEDULE


def test_the_venues_other_aggressor_spelling_is_read_too():
    """MINOR. `pmus.recent_trades` reads `isAggressor` (pmus.py:2688)
    while `_execution_record` reads `aggressor`. Which spelling an
    Execution carries on the wire is unverified here, and reading one
    alone risks serving 'the flag is absent from every fill' about a
    flag that was present."""
    rec = p2._one_execution({"type": "EXECUTION_TYPE_FILL",
                             "lastShares": "10", "lastPx": _amt("0.50"),
                             "isAggressor": True}, "placement")
    assert rec["aggressor"] is True
    econ = p2.fill_economics([_row(lane="ioc", receipt={
        "response_execs": [{"type": "EXECUTION_TYPE_FILL",
                            "lastShares": "10", "lastPx": _amt("0.50"),
                            "isAggressor": True}]})])
    assert econ["aggressor_flag"]["present"] == 1


def test_a_zero_price_fill_carries_no_notional_and_so_no_rate():
    """MINOR. The guard was `if notional is not None`, which admits
    0.0, so a stated commission on a zero-price fill went into the lane
    numerator with nothing in the denominator."""
    econ = p2.fill_economics([_row(lane="ioc", receipt={"final_execs": [
        _parsed(shares=10.0, px=0.0, commission=0.05)]})])
    b = econ["by_lane"][0]
    assert b["commission_usd"] == 0.0 and b["n_unmeasured"] == 1
    assert b["rate_per_dollar"] is None


def test_the_commission_read_is_not_coupled_to_the_columns_it_omits():
    """MINOR. RECEIPT_SQL selects `lane` (migration 041) and
    `orig_shares` (040) beside the four JSON paths. On a database
    missing either, one statement naming them fails and the WHOLE
    census is contained to None — so the venue's own stated commission
    would stop being read on EVERY row to buy a maker/taker split."""
    import asyncio

    seen = []

    class _Pool:
        async def fetch(self, sql, *a):
            seen.append(sql)
            if "lo.lane" in sql:
                raise RuntimeError('column "lane" does not exist')
            return [{"id": 1, "lane": None, "orig_shares": None,
                     "venue": "polymarket-us", "filled_shares": 10.0,
                     "fill_price": 0.5, "pnl": 1.0, "stake": 5.0,
                     "receipt": {}}]

    got = asyncio.run(p2.receipts_by_id(_Pool(), [1]))
    assert got is not None and 1 in got, "the commission read survived"
    assert len(seen) == 2, "the fallback ran once, under the same deadline"


def test_the_fee_readers_refusal_cannot_escape_one_row():
    """A fail-closed refusal on a money path must be CONTAINED. Both
    callers go through the same guard now; `capture_from_rows` called
    the reader bare."""
    class _Exploding(dict):
        def get(self, k, *a):
            if k == "receipt":          # read only inside the fee reader
                raise RuntimeError("boom")
            return super().get(k, *a)

    bad = _Exploding({"his_price": 0.5, "fill_price": 0.5, "shares": 10.0,
                      "whale": "rn1", "venue": "polymarket-us"})
    cap = p2.capture_from_rows([bad])
    assert cap["n"] == 1
    assert cap["fee_rows_unmeasured"] == 1 and cap["fee_rate"] is None
    assert p2.charge_fees([bad])[0]["fee_usd"] is None


# ── 7. the served record's own findings ──────────────────────────────


def test_a_partial_line_is_charged_on_the_entry_it_bought():
    """MAJOR. build()'s partials SELECT aliased the share column away
    (`lo.filled_shares AS remaining_shares`), so a partial row carried
    neither `shares` nor `filled_shares` and row_fee's row-level charge
    could never fire — every partial line without a stored receipt came
    back `unmeasured`, while the comment beside it said it fell back to
    the schedule. It does now, and on the ENTRY, not the remainder."""
    prow = {"whale": "rn1", "pnl": 3.25, "filled_usd": 50.0,
            "fill_price": 0.50, "remaining_shares": 40.0,
            "filled_shares": 40.0, "orig_shares": 100.0,
            "venue": "polymarket-us", "us_market_slug": "s"}
    line = cr.partials_list(p2.charge_fees([prow]))[0]
    assert line["fee_usd"] == pytest.approx(1.50)
    assert line["fee_source"] == p2.FEE_SCHEDULE
    assert line["pnl_basis"] == cr.PRE_FEE
    assert line["pnl_net"] == pytest.approx(3.25 - 1.50)
    assert line["remaining_shares"] == 40.0


def test_the_partials_query_gives_the_fee_reader_a_column_it_knows():
    """The defect was in the SQL, so the pin is on the SQL."""
    src = inspect.getsource(cr)
    partials_sql = src.split("partial_rows = await pool.fetch")[1][:1200]
    assert "AS remaining_shares" in partials_sql
    assert "AS filled_shares" in partials_sql, \
        "the fee reader looks for `shares` / `filled_shares`"
    assert "COALESCE(lo.orig_shares, lo.filled_shares)" in partials_sql


def test_every_kalshi_only_bucket_carries_its_own_basis_too():
    """MAJOR. Clause 3 says 'wherever it is served'. merge_daily's and
    merge_by_whale's ELSE branches built Kalshi-only days and whales by
    hand with no pnl_basis, no pnl_net and no net_basis, and they sat
    in the same arrays as fully labelled rows — a consumer keying on
    pnl_basis silently under-counts."""
    pm = cr.scorecard(p2.charge_fees([_settled()]))
    day = [d for d in cr.merge_daily(
        pm["daily"], [{"day": "2026-09-02", "settled": 2, "wins": 1,
                       "losses": 1, "pnl": 20.0, "staked": 50.0}])
        if d["day"] == "2026-09-02"][0]
    assert day["pnl_basis"] == cr.PRE_FEE
    assert day["pnl_net"] is None and day["net_basis"] == "unreadable"
    assert day["fee_unmeasured_rows"] == 2
    whale = [w for w in cr.merge_by_whale(
        pm["by_whale"], {"kch123": {"settled": 1, "wins": 1, "losses": 0,
                                    "pnl": 5.0, "staked": 10.0}})
        if w["whale"] == "kch123"][0]
    assert whale["pnl_basis"] == cr.PRE_FEE
    assert whale["net_basis"] == "unreadable"


def test_the_floored_kalshi_total_is_labelled_when_it_is_rebuilt():
    """floor_export rebuilds `total` through merge_totals on two
    fee-less sides, so the fee block never ran and the venue line was
    served bare."""
    out = cr.floor_export(
        {"total": {"settled": 3, "wins": 2, "losses": 1, "pnl": 30.0,
                   "staked": 60.0},
         "daily": [{"day": "2026-09-03", "settled": 1, "wins": 1,
                    "losses": 0, "pnl": 10.0, "staked": 20.0},
                   {"day": "2026-08-01", "settled": 2, "wins": 1,
                    "losses": 1, "pnl": 20.0, "staked": 40.0}]},
        "2026-09-01")
    assert out["total"]["pnl_basis"] == cr.PRE_FEE
    assert out["total"]["net_basis"] == "unreadable"
    assert out["total"]["fee_usd"] is None


def test_label_unmeasured_is_idempotent():
    """It is applied on two paths that can meet (floor_export, then
    the venue split), and double-counting a bucket's settled rows into
    fee_unmeasured_rows would misreport the reading."""
    once = cr.label_unmeasured({"settled": 3, "pnl": 30.0, "staked": 60.0})
    assert once["fee_unmeasured_rows"] == 3
    assert cr.label_unmeasured(once)["fee_unmeasured_rows"] == 3


def test_an_all_unmeasured_bucket_serves_no_fee_zero():
    """MINOR. `bucket.setdefault("fee_usd", 0.0)` ran BEFORE the
    early return, so an all-unmeasured bucket printed a $0.00 fee
    beside its unreadable net."""
    b = cr.finish_net({"pnl": 5.0, "staked": 10.0,
                       "fee_unmeasured_rows": 1})
    assert b["fee_usd"] is None
    assert b["pnl_net"] is None and b["net_basis"] == "unreadable"


def test_a_quiet_morning_is_not_a_failed_reading():
    """MINOR. `net_basis: unreadable` on a day with nothing settled
    prints as a failed reading every quiet morning and trains the
    operator to ignore the word."""
    t = cr.today_stats([], "2026-09-04", with_fees=True)
    assert t["settled"] == 0
    assert t["net_basis"] == "nothing settled — nothing to charge"
    assert t["pnl_net"] == 0.0 and t["fee_usd"] == 0.0


def test_a_partial_row_is_counted_where_the_record_adds_it_up():
    """A row charged in part contributes real dollars and an
    incomplete charge; the bucket says so rather than implying the
    total is complete."""
    rows = p2.charge_fees([{**_settled(), "receipt": {"final_execs": [
        {"type": "EXECUTION_TYPE_FILL", "id": "a", "last_shares": 100.0,
         "last_px": 0.50},
        {"type": "EXECUTION_TYPE_FILL", "id": "b", "last_shares": 5.0,
         "last_px": None}]}}])
    assert rows[0]["fee_is_partial"] is True
    t = cr.scorecard(rows)["total"]
    assert t["fee_partial_rows"] == 1
    assert t["fee_measured_rows"] == 1
    assert t["pnl_net_is_upper_bound"] is True


def test_the_public_census_is_bounded_and_says_what_it_read():
    """MAJOR. /api/copies-record is public, unauthenticated and
    uncached, and the census makes Postgres DETOAST the whole `raw`
    column for every row it touches — the 8s wait_for bounds the
    caller's wait, not the database's work. It is bounded to the newest
    rows, everything outside is still CHARGED off the schedule, and the
    payload says which — so a growing record can never silently stop
    reading the venue's own commission."""
    import asyncio

    asked = []

    class _Pool:
        async def fetch(self, sql, ids, *a):
            asked.append(list(ids))
            return []

    rows = [{"id": i, "whale": "rn1", "venue": "polymarket-us",
             "filled_shares": 10.0, "fill_price": 0.5, "pnl": 1.0}
            for i in range(cr.FEE_CENSUS_MAX_IDS + 25)]
    census = {}
    out = asyncio.run(cr.charge_the_fee(_Pool(), rows, census))
    # THE NUMBER ITSELF IS PINNED. It bounds how much of the `raw`
    # column a PUBLIC, uncached endpoint asks Postgres to detoast, and
    # a fixture built from the constant would follow it anywhere: this
    # was the one mutation the round-2 review's 20 survived.
    assert cr.FEE_CENSUS_MAX_IDS == 400
    assert len(asked[0]) == cr.FEE_CENSUS_MAX_IDS
    assert census["copy_rows"] == cr.FEE_CENSUS_MAX_IDS + 25
    assert census["rows_beyond_the_window"] == 25
    assert census["read"] is True
    # and every row outside the window is still charged, never zero
    assert all(r["fee_usd"] == pytest.approx(0.15) for r in out)
    assert all(r["fee_source"] == p2.FEE_SCHEDULE for r in out)


def test_a_refused_census_is_distinguishable_from_an_empty_one():
    """proof2 serves economics.receipt_census; the public record served
    nothing equivalent, so a census over the cap, timed out or faulted
    read exactly like one that ran and found no venue value."""
    import asyncio

    class _Pool:
        async def fetch(self, sql, ids, *a):
            raise RuntimeError("no")

    census = {}
    rows = [{"id": 1, "whale": "rn1", "venue": "polymarket-us",
             "filled_shares": 10.0, "fill_price": 0.5}]
    out = asyncio.run(cr.charge_the_fee(_Pool(), rows, census))
    assert census["read"] is False
    assert "refused or timed out" in census["note"]
    assert out[0]["fee_usd"] == pytest.approx(0.15), "the schedule charged"


def test_the_settled_cohort_asks_for_the_entrys_own_share_count():
    """And still serves the record on a database without migration
    040 — the fee falls back to the remainder exactly as before, which
    is the pre-existing behaviour and not a new one."""
    import asyncio

    seen = []

    class _Pool:
        async def fetch(self, sql, *a):
            seen.append(sql)
            if "orig_shares" in sql:
                raise RuntimeError('column "orig_shares" does not exist')
            return [{"id": 1}]

    assert asyncio.run(cr._settled_copy_rows(_Pool())) == [{"id": 1}]
    assert "COALESCE(lo.orig_shares, lo.filled_shares)" in seen[0]
    assert "orig_shares" not in seen[1]


def test_the_fee_rate_names_whose_dollar_it_is_per():
    """MINOR. `fee_rate` divides OUR fee by HIS entry notional — the
    unit every other term in the sleeve edge is denominated in, so the
    subtraction is right — but on a BUY_SHORT copy of a 0.10 entry we
    put up 0.90 for the same shares, and the rate per OUR dollar is
    several-fold smaller. Both are served; neither is implied."""
    rows = [{"his_price": 0.10, "fill_price": 0.90, "shares": 100.0,
             "venue": "polymarket-us", "whale": "rn1", "side": "BUY",
             "intent": ""}]
    cap = p2.capture_from_rows(rows)
    assert cap["fee_rate"] > cap["fee_rate_per_our_dollar"]
    assert cap["fee_rate_basis"].startswith("the fee of the rows priced")


def test_the_software_complement_says_its_net_is_all_estimate():
    """MINOR. Its buckets get finish_net, so its `pnl_net` reads as
    complete — but these rows are never in the receipt census
    (`charge_the_fee` filters the id list to COPY_WHALES), so their fee
    is 100% schedule estimate, always, and nothing said so."""
    out = cr.software_scorecard(p2.charge_fees([
        {"whale": "engine", "day": "2026-09-03", "pnl": -4.0,
         "filled_usd": 50.0, "venue": "polymarket-us",
         "filled_shares": 100.0, "fill_price": 0.50}]))
    assert out["total"]["pnl_net"] == pytest.approx(-5.50)
    assert "ALWAYS" in out["basis"]["fee_source_order"][0]
    assert out["basis"]["pmus_fee_form_verified"] is False
    src = inspect.getsource(cr.charge_the_fee)
    assert 'in COPY_WHALES' in src, "the census never sees these rows"


def test_clause_threes_residual_is_named_not_implied():
    """MINOR. api/track_record.py and analytics/proof.py still serve
    gross copy P&L with no basis key at all, beside a record that says
    pre_fee everywhere. Neither is in this unit's ownership; the
    surface names them rather than reading wider than it is."""
    note = cr.basis_note()
    named = " ".join(note["still_unlabelled_elsewhere"])
    assert "track_record.py" in named and "proof.py" in named
    # and the claim is true today
    import pathlib
    root = pathlib.Path(cr.__file__).resolve().parents[1]
    for rel in ("api/track_record.py", "analytics/proof.py"):
        body = (root / rel).read_text()
        assert "pnl_basis" not in body, rel


# ── 8. round 3: the comparison, the budget, and four honest numbers ──


def test_the_maker_versus_taker_rate_is_not_published_off_the_schedule():
    """MAJOR. Deliverable (2) is "the maker side compared against the
    taker side on OUR OWN fills rather than on a claim". On every fill
    the venue did not price — which, on this repository's last recorded
    observation (pmus.py:2136-2139), is EVERY fill there has ever been —
    the per-lane rate is

        schedule_fee / notional
          = coefficient x shares x p x (1-p) / (shares x p)
          = coefficient x (1 - price)

    so the two lanes' rates differ by their PRICE MIX and by nothing
    else. Serving them side by side published "making a market costs us
    a seventh of what taking costs" as a measurement, on the one
    instrument built to test the owner's premise. No rate is published
    for either side of the comparison now, and the payload says why in
    words."""
    for p_maker, p_taker in ((0.20, 0.80), (0.90, 0.30)):
        econ = p2.fill_economics([
            _row(lane="rest", receipt={
                "response_execs": [],
                "final_execs": [_parsed(shares=100.0, px=p_maker, eid="m")]}),
            _row(lane="ioc", receipt={
                "response_execs": [_parsed(shares=100.0, px=p_taker,
                                           eid="t")]}),
        ])
        by = {(b["lane"], b["role"]): b for b in econ["by_lane"]}
        mk, tk = by[("rest", "maker")], by[("ioc", "taker")]
        # the artifact itself, to the digit
        assert mk["rate_per_dollar"] == pytest.approx(
            p2.PMUS_FEE_COEFFICIENT * (1 - p_maker))
        assert tk["rate_per_dollar"] == pytest.approx(
            p2.PMUS_FEE_COEFFICIENT * (1 - p_taker))
        # each rate carries the caveat, and the price mix beside it
        for b in (mk, tk):
            assert b["rate_is_venue_measured"] is False
            assert "(1 - price)" in b["rate_caveat"]
            assert "PRICE MIX" in b["rate_caveat"]
        assert mk["mean_price_charged"] == pytest.approx(p_maker)
        assert tk["mean_price_charged"] == pytest.approx(p_taker)
        # and the COMPARISON is refused, in words, with no number
        rc = econ["rate_comparison"]
        assert rc["comparable"] is False
        assert "maker_rate_per_dollar" not in rc
        assert "taker_rate_per_dollar" not in rc
        assert "maker_minus_taker_per_dollar" not in rc
        assert "coefficient x (1 - price)" in rc["verdict"]
        assert "PRICE MIX" in rc["verdict"]
        assert str(p_maker) in rc["verdict"], "the price mix is named"


def test_a_comparison_needs_the_venues_own_value_on_both_sides():
    """One side priced by the venue and the other by the schedule is
    not a comparison either — it compares a reading with an arithmetic
    identity."""
    econ = p2.fill_economics([
        _row(lane="ioc", receipt={"response_execs": [
            _parsed(shares=100.0, px=0.50, commission=1.50, eid="t")]}),
        _row(lane="rest", receipt={
            "response_execs": [],
            "final_execs": [_parsed(shares=100.0, px=0.50, eid="m")]}),
    ])
    rc = econ["rate_comparison"]
    assert rc["comparable"] is False
    assert rc["maker"]["n_schedule"] == 1
    assert rc["taker"]["n_venue_stated"] == 1
    assert "maker_rate_per_dollar" not in rc


def test_a_reading_with_only_one_side_compares_nothing():
    econ = p2.fill_economics([_row(lane="rest", receipt={
        "response_execs": [],
        "final_execs": [_parsed(shares=100.0, px=0.50, commission=0.30)]})])
    rc = econ["rate_comparison"]
    assert rc["comparable"] is False
    assert "taker side" in rc["verdict"]
    assert p2.fill_economics([])["rate_comparison"]["comparable"] is False


def test_an_all_unmeasured_cohort_publishes_no_fee_dollars():
    """MINOR. `round(sum([]), 2)` is a literal zero, and the probe line
    printed `fee=$0 rate=unreadable` beside it — jq's `//` falls
    through on null, not on zero. The sibling surface already applies
    this rule (`finish_net` serves fee_usd None on an unreadable
    bucket)."""
    cap = p2.capture_from_rows([
        {"his_price": 0.50, "fill_price": 0.50, "shares": 100.0,
         "venue": "polymarket-clob", "whale": "rn1", "side": "BUY"}
        for _ in range(3)])
    assert cap["n"] == 3
    assert cap["fee_usd"] is None, "not 0"
    assert cap["fee_charged_usd"] is None, "not 0"
    assert cap["fee_rows_measured"] == 0
    assert cap["fee_rate"] is None
    # a cohort that CAN be priced still serves its dollars
    ok = p2.capture_from_rows([
        {"his_price": 0.50, "fill_price": 0.50, "shares": 100.0,
         "venue": "polymarket-us", "whale": "rn1", "side": "BUY"}])
    assert ok["fee_usd"] == pytest.approx(1.50)
    assert ok["fee_rows_measured"] == 1


def test_a_net_sleeve_edge_needs_a_minimum_of_the_cohorts_money():
    """MINOR. `fee_rate` is honest about its own rows; `combine` then
    subtracts it from the WHOLE cohort's edge, which extrapolates it to
    every row. One priceable row beside 99 unpriceable ones published a
    net sleeve edge — the single number the owner reads — off 1%
    coverage, with no minimum-coverage gate anywhere, while the LANE
    rate has had a minimum n since round 1."""
    rows = [{"his_price": 0.50, "fill_price": 0.50, "shares": 100.0,
             "venue": "polymarket-us", "whale": "rn1", "side": "BUY"}]
    rows += [{"his_price": 0.50, "fill_price": 0.50, "shares": 100.0,
              "venue": "polymarket-clob", "whale": "rn1", "side": "BUY"}
             for _ in range(99)]
    bench = {"per_whale": {"rn1": {"edge_roi": 0.0207,
                                   "edge_ci95": [0.0200, 0.0214]}}}
    cap = p2.capture_from_rows(rows)
    assert cap["fee_notional_share"] == pytest.approx(0.01)
    e = p2.combine(cap, bench)
    assert e["available"] is False
    assert "sleeve_edge" not in e and "p_edge_positive" not in e
    assert str(p2.MIN_FEE_NOTIONAL_SHARE) in e["reason"]
    assert e["fee_notional_share"] == pytest.approx(0.01)
    assert p2.thesis(e, 1000.0)["available"] is False
    # at the gate it publishes, and the coverage rides with it
    at_gate = p2.combine(p2.capture_from_rows(rows[:2]), bench)
    assert at_gate["available"] is True
    assert at_gate["fee_notional_share"] == pytest.approx(0.5)
    assert at_gate["min_fee_notional_share"] == p2.MIN_FEE_NOTIONAL_SHARE
    assert at_gate["sleeve_edge"] < 0


def test_a_database_without_the_lane_column_publishes_no_taker_fill():
    """MINOR. The census's fallback statement (a database missing
    migration 041) selects `lane` as NULL, and a NULL lane IS the IOC
    lane — so every fill was published as an `ioc` TAKER fill with
    `receipt_census: read` and no marker anywhere. An absent column is
    not evidence of a lane."""
    import asyncio

    class _NoLane:
        async def fetch(self, sql, ids, *a):
            if "lo.lane" in sql:
                raise RuntimeError('column "lane" does not exist')
            return [{"id": 1, "lane": None, "orig_shares": None,
                     "venue": "polymarket-us", "filled_shares": 100.0,
                     "fill_price": 0.5, "pnl": 1.0, "stake": 50.0,
                     "receipt": {"response_execs": [],
                                 "final_execs": [
                                     _parsed(shares=100.0, px=0.5,
                                             eid="a")]}}]

    got = asyncio.run(p2.receipts_by_id(_NoLane(), [1]))
    assert got[1]["lane_column_read"] is False
    econ = p2.fill_economics([got[1]])
    b = econ["by_lane"][0]
    assert (b["lane"], b["role"]) == ("unreadable", p2.LANE_UNKNOWN)
    assert econ["n_rows_lane_unreadable"] == 1
    assert econ["lane_column"].startswith("UNREADABLE")
    assert econ["rate_comparison"]["comparable"] is False
    # and with the column present nothing changes
    econ_ok = p2.fill_economics([{**got[1], "lane_column_read": True,
                                  "lane": "rest"}])
    assert econ_ok["n_rows_lane_unreadable"] == 0
    assert econ_ok["lane_column"] == "read"
    assert econ_ok["by_lane"][0]["role"] == p2.LANE_MAKER


def test_a_partial_lines_net_says_it_is_a_whole_fee_on_a_part():
    """MINOR. A partial line's `pnl` is the realized leg so far and its
    `fee_usd` is the WHOLE entry's fee, and build() puts these lines in
    the same `trades` array as settled lines whose `pnl_net` means
    something different. The charge is deliberate and conservative; the
    silence about it was not."""
    prow = {"whale": "rn1", "pnl": 3.25, "filled_usd": 50.0,
            "fill_price": 0.50, "remaining_shares": 40.0,
            "filled_shares": 40.0, "orig_shares": 100.0,
            "venue": "polymarket-us", "us_market_slug": "s"}
    line = cr.partials_list(p2.charge_fees([prow]))[0]
    assert line["pnl_net"] == pytest.approx(1.75)
    assert line["net_basis"] == cr.PARTIAL_NET_BASIS
    assert "LOWER bound" in line["net_basis"]
    assert "PARTIAL realization" in line["net_basis"]
    # the settled line in the same array says which net IT is
    settled = cr.trades_list(p2.charge_fees([_settled()]), with_fees=True)[0]
    assert settled["net_basis"] == cr.NET_OF_ENTRY_FEE
    assert settled["net_basis"] != line["net_basis"]
    # an unreadable fee says so on both
    unread = cr.partials_list(p2.charge_fees(
        [{**prow, "venue": "polymarket-clob"}]))[0]
    assert unread["pnl_net"] is None and unread["net_basis"] == "unreadable"


def test_a_census_with_no_budget_left_is_skipped_and_says_so():
    """MAJOR. Each census carried its own full 8s budget."""
    import asyncio

    asked = []

    class _Pool:
        async def fetch(self, sql, ids, *a):
            asked.append(list(ids))
            return []

    rows = [{"id": 1, "whale": "rn1", "venue": "polymarket-us",
             "filled_shares": 10.0, "fill_price": 0.5}]
    census = {}
    out = asyncio.run(cr.charge_the_fee(_Pool(), rows, census, budget_s=0.0))
    assert asked == [], "the database was not asked at all"
    assert census["skipped"] is True and census["read"] is False
    assert census["rows_asked"] == 0
    assert "NOT TAKEN" in census["note"]
    assert out[0]["fee_usd"] == pytest.approx(0.15), "the schedule charged"
    assert out[0]["fee_source"] == p2.FEE_SCHEDULE
    # a budget that is left over still takes the census
    census2 = {}
    asyncio.run(cr.charge_the_fee(_Pool(), rows, census2, budget_s=5.0))
    assert asked and census2["skipped"] is False and census2["read"] is True
    assert census2["budget_s"] == 5.0


def test_the_two_censuses_share_one_request_budget(monkeypatch):
    """MAJOR. /api/copies-record is public, unauthenticated, uncached
    and un-rate-limited, and build() took TWO censuses — the settled
    cohort's and the partials' — each with its own full budget, the
    second started unconditionally after the first had already spent
    its whole budget without an answer. A merely SLOW database (the
    case a detoasting read produces; a database that FAULTS answers in
    milliseconds) made the page wait 2 x 8 = 16 seconds and hold an
    ASGI worker for it, per visitor.

    Driven end to end on the real build() against a pool whose census
    hangs and whose other statements are instant."""
    import asyncio
    import time as _time

    import sportsassets.db as _db

    monkeypatch.setattr(cr, "FEE_CENSUS_TIMEOUT_S", 0.4)
    monkeypatch.setattr(p2, "FEE_CENSUS_TIMEOUT_S", 0.4)
    censuses = []

    class _SlowCensus:
        async def fetch(self, sql, *a, **kw):
            if "$1::bigint[]" in sql:
                censuses.append(sql)
                await asyncio.sleep(30)     # never answers
                return []
            if "status IN ('settled'" in sql:
                return [{"id": 1, "whale": "rn1", "day": "2026-09-03",
                         "pnl": 10.0, "filled_usd": 50.0,
                         "us_market_slug": "aec-atp-a-b-2026-09-03",
                         "status": "settled", "venue": "polymarket-us",
                         "filled_shares": 100.0, "orig_shares": 100.0,
                         "fill_price": 0.5, "latency_s": 1.0}]
            if "lo.status = 'filled'" in sql:
                return [{"id": 2, "whale": "rn1", "pnl": 3.25,
                         "filled_usd": 50.0, "fill_price": 0.5,
                         "remaining_shares": 40.0, "filled_shares": 40.0,
                         "orig_shares": 100.0, "us_market_slug": "s",
                         "venue": "polymarket-us", "latency_s": 1.0}]
            return []

        async def fetchval(self, sql, *a, **kw):
            return None

    async def _pool():
        return _SlowCensus()

    monkeypatch.setattr(_db, "get_pool", _pool)
    t0 = _time.monotonic()
    out = asyncio.run(cr.build("2026-09-01"))
    elapsed = _time.monotonic() - t0

    assert len(censuses) == 1, "the second census was not attempted"
    assert elapsed < 0.4 * 2, f"one budget, not two: {elapsed:.2f}s"
    assert out["fee_census"]["read"] is False
    part = out["fee_census"]["partials"]
    assert part["read"] is False and part["skipped"] is True
    assert part["skipped_after_first_census_failed"] is True
    assert "NOT TAKEN" in part["note"]
    # and the page is still served, with every row charged off the
    # schedule and nothing anywhere charged zero
    assert out["total"]["fee_usd"] == pytest.approx(1.50)
    assert all(t["fee_source"] == p2.FEE_SCHEDULE for t in out["trades"])
    assert out["partials"]["realized_net"] == pytest.approx(1.75)


def test_a_receipt_read_that_holds_no_fill_is_not_an_unreadable_one():
    """MINOR. An order that placed and never filled returns an EMPTY
    execution list, not None — it was READ. Counting it
    `n_rows_receipt_unreadable` muddied the one counter the payload
    uses to say how much of the maker side cannot be seen, and that
    counter is what stands behind `maker_side_is_vouchable`. The row is
    still charged off its own entry fill."""
    read_empty = _row(receipt={"response_execs": [], "final_execs": []})
    econ = p2.fill_economics([read_empty, _row(receipt=None)])
    assert econ["n_rows_receipt_unreadable"] == 1, "only the None one"
    assert econ["n_rows_read"] == 1
    assert econ["n_rows_read_no_fill"] == 1
    assert econ["by_lane"] == [], "an empty receipt attributes no fill"
    assert p2.row_fee(read_empty)["fee_usd"] == pytest.approx(0.15)

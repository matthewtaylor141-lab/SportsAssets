"""The evidence gatherer: it fetches the facts, or names what it could not.

The preflight endpoint consumed whatever the caller typed in. That makes
an operator the source of the very facts the check exists to verify. This
module goes and reads them, and most tests here are about what happens
when a read fails -- because the only interesting property is that a
missing fact becomes a named blocker rather than a plausible default.

The readers are exercised against the OFFICIAL schema field names
(orderPriceMinTickSize, minimumTradeQty, currentBalance, buyingPower,
marketSides[].long), because the first version guessed at all five and
every guess was wrong.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sportsassets import calibration as cal
from sportsassets import calibration_evidence as ce
from sportsassets import calibration_fees as cf

MARKET = "aec-atp-sin-alc-2026-09-18"

# THE EXIT IS DECLARED BEFORE THE ENTRY, in full. Management's proposed
# policy: one exit order, partial fills allowed, and NO automatic
# replacement -- so a partial fill leaves a remainder that is not
# re-offered, and what happens to it is stated rather than assumed.
EXIT_POLICY = {
    "maxExitOrders": 1,
    "partialFillsAllowed": True,
    "automaticReplacementOrders": False,
    "exitPriceLimit": 0.35,
    "entryCancellationDeadline": "2026-09-18T21:00:00Z",
    "residualInventoryFallback": "hold to settlement; no re-entry",
    "holdsRemainderThroughSettlement": True,
}

# The preview the venue returns for the sized order. It states the
# expected charge AND the execution role, because the schedule is
# role-dependent.
def agreeing_preview(price, quantity):
    return {"order": {"expectedFee": {
        "value": str(cf.expected_fee(price, quantity,
                                     role=cf.ROLE_TAKER)["FEE"]),
        "currency": "USD"}, "executionRole": "TAKER"}}

CLEAR_COORD = {"domain": {"STATE": "CLEAR"},
               "reservation": {"RESERVATION_HELD": True},
               "blockers": [], "MAY_READ_THE_VENUE": True}


def readers(**over):
    r = {
        "account": lambda: {"account": "pmus:ABCD1234", "cash": 5000.0,
                            "currentBalance": 5000.0, "buyingPower": 7500.0},
        "open_orders": lambda m: [],
        "market": lambda m: {"slug": m, "outcome": "SIN to win",
                             "outcomeSide": "LONG", "outcomeSideBlocker": None,
                             "priceBasis": "LONG", "sideIdentifier": "sin",
                             "venue": "polymarket-us",
                             "expiry": "2026-09-18T23:00:00Z"},
        "book": lambda m: {"bid": 0.39, "ask": 0.40, "priceBasis": "LONG"},
        "rules": lambda m: {"tick": 0.01, "minQuantity": 1},
        "fees": lambda m: {"schedule": dict(cf.SCHEDULE),
                           "model": "docs.polymarket.us/fees effective "
                                    "2026-09-17 (taker 0.0695)"},
    }
    r.update(over)
    return r


def boom(*_a, **_k):
    raise RuntimeError("venue 503")


# The CLI invocation the workflow uses, with every declared term.
CLI_ARGS = [
    "--market", MARKET, "--outcome-side", "LONG",
    "--max-exit-orders", "1", "--partial-fills",
    "--exit-price-limit", "0.35",
    "--entry-cancellation-deadline", "2026-09-18T21:00:00Z",
    "--residual-inventory-fallback", "hold to settlement; no re-entry",
    "--hold-remainder-through-settlement",
]


def gathered(**over):
    return ce.gather(MARKET, readers(**over), outcome_side="LONG")


# ─────────────────────────────────────────────────────────────────────
# reads, timestamps, blockers
# ─────────────────────────────────────────────────────────────────────

class TestEveryReadIsStampedAndCanFailOnItsOwn:
    def test_a_complete_gather_carries_a_timestamp_per_read(self):
        ev = gathered()
        assert ev["evidenceComplete"] is True, ev["evidenceBlockers"]
        for k in ce.READ_KEYS:
            assert ev[k]["at"].endswith("Z"), k
            assert ev[k]["ok"] is True, k

    @pytest.mark.parametrize("key,blocker", [
        ("account", ce.B_ACCOUNT),
        ("open_orders", ce.B_OPEN_ORDERS),
        ("market", ce.B_MARKET),
        ("book", ce.B_BOOK),
        ("rules", ce.B_TICK),
        ("fees", ce.B_FEES),
    ])
    def test_each_failed_read_becomes_a_named_blocker(self, key, blocker):
        ev = gathered(**{key: boom})
        assert blocker in ev["evidenceBlockers"]
        assert ev["evidenceComplete"] is False

    def test_a_readable_but_empty_book_is_still_a_blocker(self):
        ev = gathered(book=lambda m: {"bid": 0.39, "ask": None,
                                      "priceBasis": "LONG"})
        assert ce.B_BOOK in ev["evidenceBlockers"]

    def test_a_missing_minimum_quantity_is_named_separately(self):
        ev = gathered(rules=lambda m: {"tick": 0.01})
        assert ce.B_MIN_QTY in ev["evidenceBlockers"]
        assert ce.B_TICK not in ev["evidenceBlockers"]

    def test_nothing_is_invented(self):
        assert "never asked to supply a fact" in gathered()["nothingIsInvented"]


# ─────────────────────────────────────────────────────────────────────
# C. the official schema contract
# ─────────────────────────────────────────────────────────────────────

class FakeMarkets:
    def __init__(self, market=None, bbo=None, raises=None):
        self._market = market if market is not None else {
            "market": {
                "slug": MARKET,
                "title": "Sinner vs Alcaraz",
                "closeTime": "2026-09-18T23:00:00Z",
                # THE OFFICIAL NAMES. The first version looked for
                # tickSize and minQuantity and found neither.
                "orderPriceMinTickSize": "0.01",
                "minimumTradeQty": 1,
                "marketSides": [
                    {"long": True, "identifier": "sin",
                     "description": "Sinner to win"},
                    {"long": False, "identifier": "sin",
                     "description": "Sinner not to win"},
                ]}}
        self._bbo = bbo if bbo is not None else {
            "marketData": {"bestBid": 0.39, "bestAsk": 0.40}}
        self._raises = raises

    def retrieve_by_slug(self, slug):
        if self._raises:
            raise self._raises
        return self._market

    def bbo(self, slug):
        return self._bbo


class FakeAccount:
    def __init__(self, resp=None, raises=None):
        self._resp = resp if resp is not None else {
            "balances": [{"currency": "USD",
                          "currentBalance": {"value": "5000.00"},
                          "buyingPower": {"value": "7500.00"}}]}
        self._raises = raises

    def balances(self):
        if self._raises:
            raise self._raises
        return self._resp


class FakeOrders:
    def __init__(self, resp=None, preview=None):
        self._resp = resp if resp is not None else {"orders": []}
        self._preview = preview
        self.created = []
        self.previewed = []

    def list(self, params=None):
        return self._resp

    def preview(self, params):
        self.previewed.append(params)
        return self._preview

    def create(self, params):                     # pragma: no cover
        self.created.append(params)
        raise AssertionError("the evidence command must never create")


class FakeSDK:
    def __init__(self, **kw):
        self.markets = FakeMarkets(**{k: v for k, v in kw.items()
                                      if k in ("market", "bbo", "raises")})
        self.account = FakeAccount(kw.get("balances"), kw.get("account_raises"))
        self.orders = FakeOrders(kw.get("orders"), kw.get("preview"))


class Config:
    pmus_key_id = "ABCD1234EFGH"
    pmus_secret_key = "never-read-here"


class TestTheOfficialSchemaFieldNames:
    def test_the_tick_and_minimum_come_from_the_documented_fields(self):
        r = ce.default_readers(client=FakeSDK(), config=Config,
                               declared_side="LONG")
        got = r["rules"](MARKET)
        assert got["tick"] == 0.01
        assert got["minQuantity"] == 1
        assert got["fields"] == {"tick": "orderPriceMinTickSize",
                                 "minQuantity": "minimumTradeQty"}

    def test_the_old_guessed_names_are_not_used(self):
        """tickSize / minQuantity are not the venue's field names."""
        assert ce.F_TICK == "orderPriceMinTickSize"
        assert ce.F_MIN_QTY == "minimumTradeQty"
        r = ce.default_readers(client=FakeSDK(market={"market": {
            "slug": MARKET, "tickSize": "0.01", "minQuantity": 5,
            "marketSides": []}}), config=Config, declared_side="LONG")
        got = r["rules"](MARKET)
        assert got["tick"] is None and got["minQuantity"] is None

    def test_current_balance_and_buying_power_are_kept_apart(self):
        r = ce.default_readers(client=FakeSDK(), config=Config,
                               declared_side="LONG")
        got = r["account"]()
        assert got["currentBalance"] == 5000.0
        assert got["buyingPower"] == 7500.0
        # the budget check spends CASH, never buying power
        assert got["cash"] == got["currentBalance"] != got["buyingPower"]

    def test_a_balances_payload_with_no_usd_row_raises(self):
        r = ce.default_readers(
            client=FakeSDK(balances={"balances": [{"currency": "EUR"}]}),
            config=Config, declared_side="LONG")
        with pytest.raises(ValueError):
            r["account"]()


class TestAccountIdentityIsCredentialBound:
    def test_it_comes_from_configuration_not_from_the_payload(self):
        got = ce.account_identity(Config)
        assert got["ACCOUNT"] == "pmus:ABCD1234"
        assert got["BLOCKER"] is None
        assert "credential-bound" in got["source"]

    def test_an_undocumented_accountId_is_not_required(self):
        """get-account-balances does not promise one. Requiring it would
        make a perfectly real account read as unidentified."""
        r = ce.default_readers(client=FakeSDK(), config=Config,
                               declared_side="LONG")
        assert "accountId" not in str(FakeAccount().balances())
        assert r["account"]()["account"] == "pmus:ABCD1234"

    def test_no_configured_credential_is_a_blocker(self):
        class NoKey:
            pmus_key_id = ""
        got = ce.account_identity(NoKey)
        assert got["ACCOUNT"] is None
        assert got["BLOCKER"] == ce.B_ACCOUNT_ID

    def test_the_secret_never_reaches_the_evidence(self):
        got = ce.account_identity(Config)
        assert Config.pmus_secret_key not in str(got)
        assert got["fullKeyInEvidence"] is False
        assert Config.pmus_key_id not in got["ACCOUNT"]     # truncated


class TestTheOutcomeSideIsDeclaredThenVerified:
    ROW = FakeMarkets()._market["market"]

    def test_a_declared_long_resolves_to_the_venue_s_long_side(self):
        got = ce.resolve_side(self.ROW, "LONG")
        assert got["OUTCOME_SIDE"] == "LONG"
        assert got["description"] == "Sinner to win"
        assert got["long"] is True
        assert got["BLOCKER"] is None

    def test_a_declared_short_resolves_to_the_other_side(self):
        got = ce.resolve_side(self.ROW, "SHORT")
        assert got["description"] == "Sinner not to win"
        assert got["long"] is False

    def test_an_undeclared_side_is_a_blocker_and_never_defaults_to_long(self):
        for declared in (None, "", "YES", "Sinner to win"):
            got = ce.resolve_side(self.ROW, declared)
            assert got["OUTCOME_SIDE"] is None
            assert got["BLOCKER"] == ce.B_SIDE_NOT_DECLARED

    def test_a_side_the_venue_does_not_list_is_a_blocker(self):
        row = {"marketSides": [{"long": True, "description": "only side"}]}
        assert ce.resolve_side(row, "SHORT")["BLOCKER"] == ce.B_SIDE_NOT_FOUND

    def test_two_matching_sides_are_ambiguous_not_a_coin_flip(self):
        row = {"marketSides": [{"long": True, "description": "a"},
                               {"long": True, "description": "b"}]}
        assert ce.resolve_side(row, "LONG")["BLOCKER"] == ce.B_SIDE_AMBIGUOUS

    def test_the_outcome_is_the_side_description_not_the_market_title(self):
        r = ce.default_readers(client=FakeSDK(), config=Config,
                               declared_side="LONG")
        got = r["market"](MARKET)
        assert got["outcome"] == "Sinner to win"
        assert got["marketTitle"] == "Sinner vs Alcaraz"
        assert got["outcome"] != got["marketTitle"]


class TestThePriceBasisIsCarriedThrough:
    def test_a_long_ticket_quotes_the_long_book(self):
        r = ce.default_readers(client=FakeSDK(), config=Config,
                               declared_side="LONG")
        b = r["book"](MARKET)
        assert (b["bid"], b["ask"]) == (0.39, 0.40)
        assert b["priceBasis"] == "LONG"

    def test_a_short_ticket_quotes_the_complement(self):
        """The complementary outcome trades at 1 - p. Reading the LONG
        book and sending a SHORT order would price two different things
        and differ by the whole spread between them."""
        r = ce.default_readers(client=FakeSDK(), config=Config,
                               declared_side="SHORT")
        b = r["book"](MARKET)
        assert b["priceBasis"] == "SHORT"
        assert (b["bid"], b["ask"]) == (0.60, 0.61)     # 1-0.40, 1-0.39
        assert (b["longBid"], b["longAsk"]) == (0.39, 0.40)

    def test_a_basis_mismatch_between_book_and_market_is_a_blocker(self):
        ev = ce.gather(MARKET, readers(
            book=lambda m: {"bid": 0.6, "ask": 0.61, "priceBasis": "SHORT"}),
            outcome_side="LONG")
        assert ce.B_PRICE_BASIS in ev["evidenceBlockers"]

    def test_the_ticket_carries_the_side_the_adapter_will_map(self):
        t = ce.propose(gathered(), cal.empty_session("S"),
                       exit_policy=EXIT_POLICY,
                       preview=agreeing_preview)["ticket"]
        from sportsassets import calibration_adapter as ad
        p = ad.order_params(dict(t, orderType=t["orderType"]))
        assert p["outcomeSide"] == "LONG"
        assert p["nativeIntentExpected"] == "ORDER_INTENT_BUY_LONG"


# ─────────────────────────────────────────────────────────────────────
# B. the fee source
# ─────────────────────────────────────────────────────────────────────

class TestTheFeeScheduleAndItsProvenance:
    def test_the_published_terms_are_recorded(self):
        assert cf.SCHEDULE["TAKER_COEFFICIENT"] == "0.0695"
        assert cf.SCHEDULE["MAKER_REBATE_COEFFICIENT"] == "-0.0125"
        assert cf.SCHEDULE["EFFECTIVE_DATE"] == "2026-09-17"
        assert "docs.polymarket.us/fees" in cf.SCHEDULE["SOURCES"][0]

    def test_retrieval_is_recorded_honestly(self):
        """The pages were not fetched by this code. Saying so is what
        keeps a documented expectation from passing as an observation."""
        assert cf.SCHEDULE["RETRIEVED_HERE"] is False
        assert "403" in cf.SCHEDULE["WHY_NOT_RETRIEVED_HERE"]

    def test_the_provenance_records_that_the_min_form_was_our_own_guess(self):
        """The earlier `min(p, 1-p)` was not supplied by any directive.
        Recording that is the difference between a corrected module and
        one that quietly changed its mind."""
        assert cf.SCHEDULE["FORMULA"] == \
            "coefficient * quantity * price * (1 - price)"
        assert "own guess" in cf.SCHEDULE["SUPPLIED_BY"]
        assert "min(" in cf.SCHEDULE["SUPPLIED_BY"]

    @pytest.mark.parametrize("quantity,price,expected", [
        # SUPPLIED FROM THE SOURCE, asserted as literals. Not computed by
        # the implementation under test -- that is how the wrong formula
        # survived its first review.
        (10, "0.39", "0.17"),
        (100, "0.50", "1.74"),
    ])
    def test_the_independent_vectors(self, quantity, price, expected):
        got = cf.expected_fee(price, quantity, role=cf.ROLE_TAKER)
        assert got["FEE"] == Decimal(expected)

    def test_the_min_form_would_have_failed_those_vectors(self):
        """0.0695 * 10 * min(0.39, 0.61) = 0.27, not 0.17 -- the old
        guess overstated the charge by 64% at this price."""
        wrong = (Decimal("0.0695") * 10 * Decimal("0.39")).quantize(
            Decimal("0.01"))
        assert wrong == Decimal("0.27")
        assert cf.expected_fee("0.39", 10)["FEE"] == Decimal("0.17")

    def test_the_arithmetic_is_exact_not_binary_float(self):
        q = cf.expected_fee("0.39", 10, role=cf.ROLE_TAKER)
        assert isinstance(q["FEE"], Decimal)
        assert q["priceFactor"] == Decimal("0.2379")
        assert q["rounding"] == "ROUND_HALF_UP"

    def test_the_price_factor_is_p_times_one_minus_p(self):
        assert cf.price_factor("0.39") == Decimal("0.2379")
        assert cf.price_factor("0.61") == Decimal("0.2379")   # symmetric
        assert cf.price_factor("0.50") == Decimal("0.25")     # the maximum
        assert cf.price_factor(0) is None and cf.price_factor(1) is None

    def test_the_worst_price_factor_is_a_quarter(self):
        assert cf.WORST_PRICE_FACTOR == Decimal("0.25")
        for p in ("0.01", "0.25", "0.39", "0.5", "0.75", "0.99"):
            assert cf.price_factor(p) <= cf.WORST_PRICE_FACTOR

    def test_a_schedule_not_yet_effective_does_not_apply(self):
        q = cf.expected_fee("0.39", 10, at="2026-09-16")
        assert q["BLOCKER"] == cf.B_SCHEDULE_NOT_EFFECTIVE
        assert q["FEE"] is None

    def test_an_unsupported_role_is_refused(self):
        assert cf.expected_fee("0.39", 10, role="WHATEVER")["BLOCKER"] == \
            cf.B_UNSUPPORTED_ROLE

    def test_there_is_no_default_fee(self):
        assert "no default fee" in cf.NO_INVENTED_DEFAULT
        assert cf.expected_fee(None, 10)["BLOCKER"] == cf.B_BAD_INPUT
        assert cf.expected_fee("0.39", None)["BLOCKER"] == cf.B_BAD_INPUT


class TestTheThreeQuantitiesAreDistinct:
    """Expected charge, reserved allowance and collected fee are three
    different numbers. Collapsing any two is how a conservative reserve
    starts being compared for equality with a maker charge."""

    def test_they_are_labelled_and_rounded_differently(self):
        exp = cf.expected_fee("0.39", 10)
        res = cf.reserve_allowance("0.39", 10)
        assert exp["kind"] == "EXPECTED_CHARGE"
        assert res["kind"] == "RESERVE_ALLOWANCE"
        assert exp["rounding"] == "ROUND_HALF_UP"
        assert res["rounding"] == "ROUND_CEILING"
        assert res["FEE"] >= exp["FEE"]

    def test_the_reserve_is_not_a_prediction(self):
        assert "EQUALITY" in cf.A_RESERVE_IS_NOT_A_PREDICTION
        assert "notAPrediction" in cf.reserve_allowance("0.39", 10)

    def test_a_rebate_role_reserves_zero_not_a_negative(self):
        res = cf.reserve_allowance("0.39", 10, role=cf.ROLE_MAKER)
        assert res["FEE"] == Decimal("0.00")
        exp = cf.expected_fee("0.39", 10, role=cf.ROLE_MAKER)
        assert exp["FEE"] < 0

    def test_the_collected_fee_is_read_back_and_unreadable_is_not_zero(self):
        assert cf.collected_fee({"feeCollected": "0.17"})["FEE"] == \
            Decimal("0.17")
        got = cf.collected_fee({})
        assert got["FEE"] is None and got["BLOCKER"]


class TestTheFeeDependsOnTheSizeSoItIsQuotedAfterSizing:
    def test_doubling_the_quantity_doubles_the_fee(self):
        a = cf.expected_fee("0.39", 10)["FEE"]
        b = cf.expected_fee("0.39", 20)["FEE"]
        assert b > a
        assert abs(b - 2 * a) <= Decimal("0.01")

    def test_the_ticket_fee_matches_the_ticket_quantity(self):
        got = ce.propose(gathered(), cal.empty_session("S"),
                         exit_policy=EXIT_POLICY, preview=agreeing_preview)
        t = got["ticket"]
        expect = cf.entry_reserve(t["price"], t["quantity"])["FEE"]
        assert Decimal(str(t["entryFeeReserve"])) == expect

    def test_the_size_fits_the_cap_with_its_own_fee_not_another_size_s(self):
        got = ce.propose(gathered(), cal.empty_session("S"),
                         exit_policy=EXIT_POLICY, preview=agreeing_preview)
        t = got["ticket"]
        assert got["allInCost"] <= cal.MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE
        # one more share, with the fee THAT size would incur, does not fit
        n = t["quantity"] + 1
        bigger = (n * t["price"]
                  + float(cf.entry_reserve(t["price"], n)["FEE"])
                  + float(cf.exit_reserve(n, EXIT_POLICY)["FEE"]))
        assert bigger > cal.MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE


class TestTheEntryIsReservedAtTheTakerRate:
    def test_a_post_only_entry_still_reserves_the_taker_fee(self):
        got = cf.entry_reserve("0.39", 10, post_only=True)
        assert got["role"] == cf.ROLE_TAKER
        assert got["FEE"] > 0
        assert "order 153" in got["whyTakerRate"]

    def test_the_rebate_is_never_netted_off_the_reserve(self):
        entry = cf.entry_reserve("0.39", 10)["FEE"]
        rebate = cf.recorded_rebate("0.39", 10, cf.ROLE_MAKER)
        assert rebate["REBATE"] < 0
        assert entry > 0                       # unreduced
        assert rebate["appliedToBudget"] is False
        assert rebate["appliedToReserve"] is False
        assert "never reduces a reserve" in rebate["note"]
        assert "cumulative session spending" in rebate["note"]

    def test_a_taker_role_earns_no_rebate_to_record(self):
        assert cf.recorded_rebate("0.39", 10, cf.ROLE_TAKER)["REBATE"] == \
            Decimal("0.00")


class TestTheExitIsBoundedWithoutKnowingItsPrice:
    def test_the_bound_uses_the_worst_price_factor(self):
        got = cf.exit_reserve(10, EXIT_POLICY)
        assert got["worstPriceFactor"] == Decimal("0.25")
        # 0.0695 * 10 * 0.25 = 0.17375, rounded UP, one permitted order
        assert got["perOrder"] == Decimal("0.18")
        assert got["FEE"] == Decimal("0.18")

    def test_more_permitted_exit_orders_reserve_more(self):
        base = dict(EXIT_POLICY)
        one = cf.exit_reserve(10, dict(base, maxExitOrders=1))["FEE"]
        three = cf.exit_reserve(10, dict(base, maxExitOrders=3))["FEE"]
        assert three == 3 * one

    @pytest.mark.parametrize("policy", [
        None, {}, {"maxExitOrders": 0, "partialFillsAllowed": True,
                   "automaticReplacementOrders": False},
        {"maxExitOrders": 1, "automaticReplacementOrders": False},
        {"partialFillsAllowed": True, "automaticReplacementOrders": False},
        # automatic replacement unstated: how many fee events the exit can
        # produce is then unknown
        {"maxExitOrders": 1, "partialFillsAllowed": True},
    ])
    def test_an_unstated_exit_policy_is_a_blocker(self, policy):
        assert cf.exit_reserve(10, policy)["BLOCKER"] == cf.B_EXIT_POLICY

    def test_a_partial_fill_does_not_authorise_another_order(self):
        got = cf.exit_reserve(10, EXIT_POLICY)
        assert got["automaticReplacementOrders"] is False
        assert got["maxExitOrders"] == 1
        assert "not re-offered" in got["aPartialFillDoesNotAuthoriseAnother"]

    def test_a_preview_alone_does_not_bound_the_exit(self):
        assert "has not happened" in cf.A_PREVIEW_DOES_NOT_BOUND_THE_EXIT
        assert "note" in cf.exit_reserve(10, EXIT_POLICY)

    def test_no_ticket_without_a_stated_exit_policy(self):
        got = ce.propose(gathered(), cal.empty_session("S"), exit_policy=None)
        assert got["ticket"] is None
        assert ce.B_EXIT_POLICY_MISSING in got["blockers"]


class TestTheTicketDeclaresItsExitInFull:
    @pytest.mark.parametrize("drop,blocker", [
        ("exitPriceLimit", ce.B_EXIT_PRICE_LIMIT),
        ("entryCancellationDeadline", ce.B_CANCEL_DEADLINE),
        ("residualInventoryFallback", ce.B_RESIDUAL_PLAN),
    ])
    def test_a_missing_term_blocks_the_ticket(self, drop, blocker):
        policy = {k: v for k, v in EXIT_POLICY.items() if k != drop}
        got = ce.propose(gathered(), cal.empty_session("S"),
                         exit_policy=policy, preview=agreeing_preview)
        assert got["ticket"] is None
        assert got["blockers"] == [blocker]

    def test_holding_through_settlement_must_be_explicit(self):
        policy = {k: v for k, v in EXIT_POLICY.items()
                  if k != "holdsRemainderThroughSettlement"}
        got = ce.propose(gathered(), cal.empty_session("S"),
                         exit_policy=policy, preview=agreeing_preview)
        assert got["ticket"] is None
        assert got["blockers"] == [ce.B_RESIDUAL_PLAN]

    def test_a_complete_policy_puts_every_term_on_the_ticket(self):
        t = ce.propose(gathered(), cal.empty_session("S"),
                       exit_policy=EXIT_POLICY,
                       preview=agreeing_preview)["ticket"]
        assert t["exitPriceLimit"] == 0.35
        assert t["entryCancellationDeadline"] == "2026-09-18T21:00:00Z"
        assert t["residualInventoryFallback"] == \
            "hold to settlement; no re-entry"
        assert t["holdsRemainderThroughSettlement"] is True
        assert "RECONCILED inventory" in t[
            "exitMaySellOnlyReconciledInventory"]
        assert "no new entry" in t["noNewEntryWhileUnresolved"].lower()


class TestTheDocumentedFeeIsReconciledAgainstThePreview:
    def test_agreement_establishes_the_fee(self):
        obs = cf.preview_fee({"order": {"expectedFee": {"value": "0.17",
                                                        "currency": "USD"},
                                        "executionRole": "TAKER"}})
        got = cf.reconcile("0.39", 10, obs)
        assert got["AGREED"] is True and got["BLOCKER"] is None
        assert got["comparing"] == "EXPECTED_CHARGE vs EXPECTED_CHARGE"

    def test_a_disagreement_is_a_blocker_not_a_preference(self):
        obs = cf.preview_fee({"order": {"expectedFee": "1.50",
                                        "executionRole": "TAKER"}})
        got = cf.reconcile("0.39", 10, obs)
        assert got["AGREED"] is False
        assert got["BLOCKER"] == cf.B_DISAGREEMENT

    def test_a_maker_preview_is_compared_at_the_maker_rate(self):
        """NOT against the conservative taker reserve. The reserve rounds
        up and assumes the taker role; requiring it to equal a maker
        charge fails precisely when it is doing its job."""
        maker = cf.expected_fee("0.39", 10, role=cf.ROLE_MAKER)["FEE"]
        obs = cf.preview_fee({"order": {"expectedFee": str(maker),
                                        "executionRole": "MAKER"}})
        got = cf.reconcile("0.39", 10, obs)
        assert got["AGREED"] is True
        assert got["role"] == cf.ROLE_MAKER
        assert got["documented"] < 0
        # and it does NOT equal the reserve, which is a different quantity
        assert cf.entry_reserve("0.39", 10)["FEE"] != abs(got["documented"])

    def test_a_preview_stating_no_fee_is_unreadable_not_zero(self):
        got = cf.preview_fee({"order": {"executionRole": "TAKER"}})
        assert got["FEE"] is None
        assert got["BLOCKER"] == cf.B_PREVIEW_UNREADABLE

    def test_a_collected_to_date_field_is_not_a_future_guarantee(self):
        got = cf.preview_fee({"order": {"feeCollected": "0.00",
                                        "executionRole": "TAKER"}})
        assert got["BLOCKER"] == cf.B_PREVIEW_IS_HISTORICAL
        assert "already taken" in got["why"]

    def test_a_preview_without_a_role_cannot_be_checked(self):
        got = cf.preview_fee({"order": {"expectedFee": "0.17"}})
        assert got["BLOCKER"] == cf.B_PREVIEW_ROLE

    def test_a_preview_in_unknown_units_is_refused(self):
        got = cf.preview_fee({"order": {
            "expectedFee": {"value": "0.17", "currency": "EUR"},
            "executionRole": "TAKER"}})
        assert got["BLOCKER"] == cf.B_PREVIEW_UNITS

    def test_a_missing_preview_is_a_blocker_not_a_skipped_check(self):
        """The old code reconciled only when a preview happened to be
        passed, and the CLI never passed one."""
        got = cf.reconcile("0.39", 10, None)
        assert got["AGREED"] is False
        assert got["BLOCKER"] == cf.B_PREVIEW_MISSING

    def test_no_preview_means_no_ticket(self):
        got = ce.propose(gathered(), cal.empty_session("S"),
                         exit_policy=EXIT_POLICY, preview=None)
        assert got["ticket"] is None
        assert got["blockers"] == [cf.B_PREVIEW_MISSING]

    def test_a_preview_that_raises_is_a_blocker(self):
        def boom_preview(_p, _q):
            raise RuntimeError("venue 503")
        got = ce.propose(gathered(), cal.empty_session("S"),
                         exit_policy=EXIT_POLICY, preview=boom_preview)
        assert got["ticket"] is None
        assert got["blockers"] == [cf.B_PREVIEW_UNREADABLE]

    def test_a_disagreeing_preview_stops_the_ticket(self):
        got = ce.propose(gathered(), cal.empty_session("S"),
                         exit_policy=EXIT_POLICY,
                         preview=lambda p, q: {"order": {
                             "expectedFee": "9.99",
                             "executionRole": "TAKER"}})
        assert got["ticket"] is None
        assert got["blockers"] == [cf.B_DISAGREEMENT]

    def test_an_agreeing_preview_lets_the_ticket_through(self):
        got = ce.propose(gathered(), cal.empty_session("S"),
                         exit_policy=EXIT_POLICY, preview=agreeing_preview)
        assert got["ticket"] is not None
        assert got["feeAgreement"]["AGREED"] is True


# ─────────────────────────────────────────────────────────────────────
# freshness, provenance, passive pricing  (unchanged rules, re-pinned)
# ─────────────────────────────────────────────────────────────────────

class TestFreshnessIsComputedNotAsserted:
    def test_a_fresh_gather_says_so_and_carries_its_age(self):
        t = ce.propose(gathered(), cal.empty_session("S"),
                       exit_policy=EXIT_POLICY,
                       preview=agreeing_preview)["ticket"]
        assert t["stateFresh"] is True
        assert t["stateAgeSeconds"] < ce.MAX_EVIDENCE_AGE_S

    def test_an_old_read_is_stale_and_blocks(self):
        ev = gathered()
        for k in ce.READ_KEYS:
            ev[k]["at"] = "2026-09-18T16:59:30Z"
        ev["book"]["at"] = "2026-09-18T00:00:00Z"
        f = ce.freshness(ev, now="2026-09-18T17:00:00Z")
        assert f["FRESH"] is False and f["staleReads"] == ["book"]

    def test_a_timestamp_that_will_not_parse_is_not_fresh(self):
        ev = gathered()
        ev["rules"]["at"] = "whenever"
        assert ce.freshness(ev)["unreadableTimestamps"] == ["rules"]

    def test_the_freshness_field_is_never_a_literal_in_the_source(self):
        import inspect
        src = inspect.getsource(ce.propose)
        assert '"stateFresh": True' not in src
        assert '"stateFresh": bool(fresh["FRESH"])' in src


class TestMissingProvenanceIsABlockerNotAString:
    @pytest.mark.parametrize("over,blocker", [
        ({"account": lambda: {"cash": 5000.0}}, ce.B_ACCOUNT_ID),
        ({"market": lambda m: {"slug": m, "outcomeSide": "LONG",
                               "priceBasis": "LONG",
                               "expiry": "2026-09-18T23:00:00Z"}},
         ce.B_OUTCOME),
        ({"market": lambda m: {"slug": m, "outcome": "SIN to win",
                               "priceBasis": "LONG",
                               "expiry": "2026-09-18T23:00:00Z"}},
         ce.B_OUTCOME_SIDE),
        ({"market": lambda m: {"slug": m, "outcome": "SIN to win",
                               "outcomeSide": "LONG",
                               "priceBasis": "LONG"}}, ce.B_EXPIRY),
        ({"fees": lambda m: {"schedule": dict(cf.SCHEDULE)}}, ce.B_FEE_MODEL),
    ])
    def test_each_missing_fact_blocks(self, over, blocker):
        ev = gathered(**over)
        assert blocker in ev["evidenceBlockers"]
        assert ce.propose(ev, cal.empty_session("S"),
                          exit_policy=EXIT_POLICY)["ticket"] is None

    def test_the_string_that_used_to_stand_in_for_them_is_gone(self):
        import ast
        import pathlib
        tree = ast.parse(pathlib.Path(ce.__file__).read_text())
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                d = ast.get_docstring(node, clean=False)
                if d:
                    docs.add(d)
        emitted = [n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and n.value not in docs]
        assert not [s for s in emitted if "NOT IDENTIFIED" in s]


class TestThePassivePriceRule:
    def test_the_price_is_the_bid_and_never_the_ask(self):
        got = ce.passive_price({"bid": 0.39, "ask": 0.40}, 0.01,
                               "LIMIT_GTC_POST_ONLY")
        assert got["PRICE"] == 0.39 and got["crossesTheSpread"] is False

    @pytest.mark.parametrize("book,tick,blocker", [
        ({"bid": None, "ask": 0.40}, 0.01, ce.B_NO_BID),
        ({"bid": 0.39, "ask": None}, 0.01, ce.B_BOOK),
        ({"bid": 0.41, "ask": 0.40}, 0.01, ce.B_CROSSED_BOOK),
        ({"bid": 0.395, "ask": 0.40}, 0.01, ce.B_PRICE_OFF_TICK),
        ({"bid": 0.39, "ask": 0.40}, None, ce.B_TICK),
    ])
    def test_a_book_the_rule_cannot_price_is_refused(self, book, tick,
                                                     blocker):
        got = ce.passive_price(book, tick, "LIMIT_GTC_POST_ONLY")
        assert got["BLOCKER"] == blocker and got["PRICE"] is None

    def test_a_refusal_never_falls_back_to_an_aggressive_price(self):
        ev = gathered(book=lambda m: {"bid": None, "ask": 0.40,
                                      "priceBasis": "LONG"})
        got = ce.propose(ev, cal.empty_session("S"), exit_policy=EXIT_POLICY)
        assert got["ticket"] is None and got["blockers"] == [ce.B_NO_BID]

    def test_a_non_post_only_type_has_no_automatic_passive_price(self):
        assert ce.passive_price({"bid": 0.39, "ask": 0.40}, 0.01,
                                "LIMIT_IOC")["BLOCKER"] == \
            ce.B_ORDER_TYPE_NOT_PRICEABLE


# ─────────────────────────────────────────────────────────────────────
# D. coordination
# ─────────────────────────────────────────────────────────────────────

class TestCoordinationIsNotOptional:
    @pytest.mark.parametrize("coord", [
        {"blockers": ["PROTECTED_RESEARCH_WINDOW_ACTIVE"]},
        {"blockers": ["DOMAIN_STATE_NOT_ESTABLISHED"]},
        {"blockers": ["NO_EXECUTION_RESERVATION_HELD"]},
    ])
    def test_a_blocked_domain_means_nothing_is_read_at_all(self, coord):
        reads = []

        def watched():
            reads.append("account")
            return {"cash": 1.0, "account": "a"}
        got = ce.run(MARKET, readers=readers(account=watched),
                     session=cal.empty_session("S"), coordination=coord,
                     outcome_side="LONG", exit_policy=EXIT_POLICY)
        assert got["blockers"] == coord["blockers"]
        assert got["evidence"] is None
        assert reads == []

    def test_there_is_no_argument_that_turns_the_census_off(self):
        import inspect
        sig = inspect.signature(ce.run)
        assert "require_idle_domain" not in sig.parameters
        cli = inspect.getsource(ce._cli)
        assert "--require-idle-domain" not in cli
        # the only related flag records an acceptance; it does not skip
        assert "--accept-snapshot-only" in cli

    def test_omitting_every_optional_argument_still_coordinates(self):
        """run() with nothing but a market must not read the venue
        without asking the domain first."""
        import inspect
        src = inspect.getsource(ce.run)
        i_coord = src.index("cd.coordination")
        i_gather = src.index("ev = gather(")
        assert i_coord < i_gather


# ─────────────────────────────────────────────────────────────────────
# the command
# ─────────────────────────────────────────────────────────────────────

class TestTheCommandRuns:
    def test_run_returns_a_complete_report(self):
        got = ce.run(MARKET, readers=readers(), preview=agreeing_preview,
                     session=cal.empty_session("S"), coordination=CLEAR_COORD,
                     outcome_side="LONG", exit_policy=EXIT_POLICY)
        assert got["evidence"]["evidenceComplete"] is True
        assert got["proposal"]["ticket"]["price"] == 0.39
        assert got["submissionReachable"] is False
        assert got["sessionSource"] == "caller"

    def test_the_durable_session_is_looked_up_when_none_is_given(self):
        seen = []

        def loader():
            seen.append(1)
            return cal.empty_session("DURABLE")
        got = ce.run(MARKET, readers=readers(), preview=agreeing_preview,
                     session_loader=loader, coordination=CLEAR_COORD,
                     outcome_side="LONG", exit_policy=EXIT_POLICY)
        assert seen == [1] and got["sessionSource"] == "durable"

    def test_an_unavailable_budget_refuses_rather_than_assuming_one(self):
        def loader():
            raise RuntimeError("database down")
        got = ce.run(MARKET, readers=readers(), session_loader=loader,
                     coordination=CLEAR_COORD, outcome_side="LONG",
                     exit_policy=EXIT_POLICY)
        assert got["blockers"] == ["BUDGET_STATE_UNAVAILABLE"]
        assert got["proposal"]["ticket"] is None

    def test_the_cli_runs_end_to_end_against_a_fake_transport(
            self, tmp_path, monkeypatch, capsys):
        import json as _json
        from sportsassets import pmus
        sdk = FakeSDK(preview={"order": {
            "expectedFee": {"value": "0.17", "currency": "USD"},
            "executionRole": "TAKER"}})
        monkeypatch.setattr(pmus, "_get_client", lambda: sdk)
        monkeypatch.setattr(ce, "_durable_session",
                            lambda: cal.empty_session("S"))
        real = ce.default_readers
        monkeypatch.setattr(ce, "default_readers",
                            lambda **kw: real(
                                client=sdk, config=Config,
                                declared_side=kw.get("declared_side"),
                                market_id=kw.get("market_id")))
        monkeypatch.setattr("sportsassets.calibration_domain.coordination",
                            lambda **kw: CLEAR_COORD)
        out = tmp_path / "evidence.json"
        rc = ce._cli(CLI_ARGS + ["--out", str(out)])
        report = _json.loads(out.read_text())
        assert report["marketId"] == MARKET
        assert report["evidence"]["account"]["value"]["account"] == \
            "pmus:ABCD1234"
        assert report["evidence"]["book"]["value"]["bid"] == 0.39
        t = report["proposal"]["ticket"]
        assert t is not None, report["blockers"]
        assert t["outcomeSide"] == "LONG"
        assert t["price"] == 0.39
        assert rc == 0
        assert _json.loads(capsys.readouterr().out)["marketId"] == MARKET
        assert sdk.orders.created == []

    def test_the_cli_refuses_without_a_declared_outcome_side(
            self, tmp_path, monkeypatch):
        import json as _json
        from sportsassets import pmus
        sdk = FakeSDK()
        monkeypatch.setattr(pmus, "_get_client", lambda: sdk)
        monkeypatch.setattr(ce, "_durable_session",
                            lambda: cal.empty_session("S"))
        real = ce.default_readers
        monkeypatch.setattr(ce, "default_readers",
                            lambda **kw: real(
                                client=sdk, config=Config,
                                declared_side=kw.get("declared_side")))
        monkeypatch.setattr("sportsassets.calibration_domain.coordination",
                            lambda **kw: CLEAR_COORD)
        out = tmp_path / "e.json"
        rc = ce._cli([a for a in CLI_ARGS
                      if a not in ("--outcome-side", "LONG")]
                     + ["--out", str(out)])
        report = _json.loads(out.read_text())
        assert report["proposal"]["ticket"] is None
        assert ce.B_SIDE_NOT_DECLARED in report["blockers"]
        assert rc == 1


class TestSubmissionIsNotReachable:
    def test_the_module_imports_no_order_creation_path(self):
        import pathlib
        src = pathlib.Path(ce.__file__).read_text()
        code = "\n".join(ln for ln in src.splitlines()
                         if not ln.strip().startswith("#"))
        code = code.split('"""', 2)[-1]
        for forbidden in ("submit_fok", "orders.create", "calibration_execute",
                          "LiveVenue", "calibration_adapter"):
            assert forbidden not in code, forbidden

    def test_there_is_no_submit_flag(self):
        import inspect
        flags = [ln for ln in inspect.getsource(ce._cli).splitlines()
                 if "add_argument" in ln]
        assert flags
        assert not any("submit" in ln for ln in flags), flags

    def test_a_preview_is_a_read_and_a_create_is_not_reachable(self):
        """preview-order is one of the official sources. It is read-only;
        the fake raises if create is ever called."""
        sdk = FakeSDK(preview={"order": {"expectedFee": "0.17",
                                         "executionRole": "TAKER"}})
        got = cf.preview_fee(sdk.orders.preview({"request": {}}))
        assert got["FEE"] == Decimal("0.17")
        assert sdk.orders.created == []

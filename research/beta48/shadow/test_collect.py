#!/usr/bin/env python3
"""The PHASE_2A collector's boundary and its pairing logic.

The boundary is proved by walking the AST, as everywhere else in this
programme. This is the ONE module in the shadow package that touches a network,
so it is the one that has to be checked hardest.
"""
import ast
import json
import unittest
from decimal import Decimal as D
from pathlib import Path

import collect as C
import position_state as P

SRC = Path(C.__file__).read_text()
TREE = ast.parse(SRC)


def _calls():
    for n in ast.walk(TREE):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                yield f.attr
            elif isinstance(f, ast.Name):
                yield f.id


class TheBoundaryIsStructural(unittest.TestCase):

    def test_no_mutating_http_verb_is_ever_called(self):
        banned = {"post", "put", "patch", "delete", "options", "head",
                  "request", "stream", "send"}
        self.assertEqual(banned & set(_calls()), set())

    def test_no_order_or_credential_verb_appears(self):
        banned = {"place_order", "submit", "submit_order", "cancel", "sign",
                  "authenticate", "login", "getenv", "getpass"}
        self.assertEqual(banned & set(_calls()), set())
        self.assertNotIn("environ", {n.attr for n in ast.walk(TREE)
                                     if isinstance(n, ast.Attribute)})

    def test_only_one_host_is_ever_addressed(self):
        strings = [n.value for n in ast.walk(TREE)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        hosts = [s for s in strings if "://" in s]
        self.assertEqual(hosts, [C.HOST])

    def test_the_declared_boundary_matches_the_tree(self):
        self.assertEqual(C.METHOD, "GET")
        self.assertIsNone(C.CREDENTIAL)
        self.assertFalse(C.ORDER_PATH_EXISTS)
        self.assertFalse(C.mirror_live)

    def test_the_decision_engine_is_still_network_free(self):
        """The whole point of the split: this file reads, position_state
        decides, and the decider cannot reach a venue."""
        eng = ast.parse(Path(P.__file__).read_text())
        imports = set()
        for n in ast.walk(eng):
            if isinstance(n, ast.Import):
                imports |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                imports.add(n.module.split(".")[0])
        self.assertNotIn("httpx", imports)

    def test_the_read_is_paced_and_the_rate_is_declared(self):
        self.assertEqual(C.RATE_LIMIT_RPS, 2.0)
        self.assertIn("wait", set(_calls()))


class SelectionIsFrozenNotRanked(unittest.TestCase):

    def _rows(self, n, high=True):
        return [{"slug": "m%d" % i,
                 "DECISION": {"HIGH_ACTIVITY_AT_DECISION": high}}
                for i in range(n)]

    def test_only_high_activity_markets_are_candidates(self):
        rows = self._rows(3, high=True) + [
            {"slug": "cold", "DECISION": {"HIGH_ACTIVITY_AT_DECISION": False}}]
        sel = C.select_candidates(rows)
        self.assertEqual(len(sel), 3)
        self.assertNotIn("cold", [s for s, _ in sel])

    def test_the_order_is_a_salted_hash_not_scan_order(self):
        """Scan order biases toward whatever the board listed first; activity
        rank selects the most active markets and then reports an activity
        distribution measured on them."""
        rows = self._rows(50)
        sel = [s for s, _ in C.select_candidates(rows)]
        self.assertNotEqual(sel, ["m%d" % i for i in range(50)])

    def test_the_order_is_deterministic_under_the_same_salt(self):
        rows = self._rows(20)
        self.assertEqual(C.select_candidates(rows), C.select_candidates(rows))

    def test_a_different_salt_gives_a_different_order(self):
        rows = self._rows(30)
        a = [s for s, _ in C.select_candidates(rows)]
        b = [s for s, _ in C.select_candidates(rows, salt="OTHER")]
        self.assertNotEqual(a, b)


class TheComplementIsNeverGuessed(unittest.TestCase):
    """EVENT_KEY_VALIDATED = NO, so a family key alone is not an identity."""

    def _row(self, slug, start, team_a, team_b, prov="p1"):
        """The venue's own shape: teams and provider ids hang off
        marketSides, which is where underlying_event_key reads them."""
        def side(t):
            return {"team": {"id": t,
                             "providerIds": [{"provider": "P",
                                              "providerId": prov}]}}
        return {"slug": slug, "gameStartTime": start,
                "marketSides": [side(team_a), side(team_b)]}

    def test_no_sibling_means_no_complement(self):
        comp, key, why = C.resolve_complement(
            "aec-nfl-a-b-2026-09-20-x", {}, {})
        self.assertEqual(comp, C.NOT_IDENTIFIED)
        self.assertEqual(why, "NO_SIBLING_IN_FAMILY")

    def test_a_weak_identity_refuses_rather_than_pairing(self):
        """Two siblings in a family whose identity cannot be proved are NOT a
        pair. A basis computed across two unrelated contests is a number about
        nothing."""
        a, b = "fam-x-one", "fam-x-two"
        board = {a: {"slug": a}, b: {"slug": b}}
        fam = C.E.market_family_key(a)
        comp, key, why = C.resolve_complement(a, board, {fam: [a, b]})
        self.assertEqual(comp, C.NOT_IDENTIFIED)
        self.assertTrue(why.startswith("IDENTITY_"))

    def test_more_than_one_sibling_is_ambiguous_not_a_choice(self):
        a = "aec-nfl-min-chi-2026-09-20-min"
        b = "aec-nfl-min-chi-2026-09-20-chi"
        c = "aec-nfl-min-chi-2026-09-20-tie"
        rows = {s: self._row(s, "2026-09-20T17:00:00Z", "MIN", "CHI")
                for s in (a, b, c)}
        fam = C.E.market_family_key(a)
        comp, key, why = C.resolve_complement(a, rows, {fam: [a, b, c]})
        self.assertEqual(comp, C.NOT_IDENTIFIED)
        self.assertIn("AMBIGUOUS", why)

    def test_a_proved_two_outcome_contest_does_pair(self):
        a = "aec-nfl-min-chi-2026-09-20-min"
        b = "aec-nfl-min-chi-2026-09-20-chi"
        rows = {s: self._row(s, "2026-09-20T17:00:00Z", "MIN", "CHI")
                for s in (a, b)}
        fam = C.E.market_family_key(a)
        comp, key, why = C.resolve_complement(a, rows, {fam: [a, b]})
        self.assertEqual(comp, b)
        self.assertTrue(why.startswith("OK_"))
        self.assertNotEqual(key, C.NOT_IDENTIFIED)


class PairBasisComesFromTwoArrivingBooks(unittest.TestCase):

    def _book(self, bid=None, ask=None):
        md = {"bids": [], "offers": []}
        if bid is not None:
            md["bids"] = [{"px": {"value": bid}, "qty": "100"}]
        if ask is not None:
            md["offers"] = [{"px": {"value": ask}, "qty": "100"}]
        md["state"] = "MARKET_STATE_OPEN"
        return {"marketData": md}

    def test_one_binary_book_gives_both_bases_and_never_one_number(self):
        """The venue shape: one ladder, both sides. Crossing costs 1+spread,
        resting costs 1-spread, and picking the wrong one is the difference
        between a trade that cannot win and one that cannot lose."""
        b = C.pair_basis(self._book(bid="0.41", ask="0.42"))
        self.assertEqual(b["COMPLEMENT_SHAPE"], "ONE_BINARY_BOOK_BOTH_SIDES")
        self.assertEqual(b["AGGRESSIVE_PAIR_BASIS"], D("1.01"))
        self.assertEqual(b["PASSIVE_PAIR_BASIS"], D("0.99"))

    def test_crossing_both_legs_can_never_be_profitable_here(self):
        for bid, ask in (("0.41", "0.42"), ("0.05", "0.09"), ("0.90", "0.99")):
            b = C.pair_basis(self._book(bid=bid, ask=ask))
            self.assertGreaterEqual(b["AGGRESSIVE_PAIR_BASIS"], D("1.00"))
            self.assertFalse(b["AGGRESSIVE_PAIR_PROFITABLE_BEFORE_FEES"])

    def test_resting_both_legs_is_profitable_before_fees_if_both_fill(self):
        b = C.pair_basis(self._book(bid="0.41", ask="0.42"))
        self.assertTrue(b["PASSIVE_PAIR_PROFITABLE_BEFORE_FEES"])
        self.assertTrue(b["PASSIVE_PAIR_REQUIRES_BOTH_RESTS_TO_FILL"])
        self.assertEqual(b["ACTUAL_BETTOR_FILL_PROBABILITY"],
                         C.NOT_IDENTIFIED)

    def test_two_independent_books_are_still_supported(self):
        """The engine must not bake in the shape it happens to see today."""
        b = C.pair_basis(self._book(bid="0.40", ask="0.41"),
                         self._book(bid="0.55", ask="0.56"))
        self.assertEqual(b["COMPLEMENT_SHAPE"], "TWO_INDEPENDENT_BOOKS")
        self.assertEqual(b["AGGRESSIVE_PAIR_BASIS"], D("0.97"))
        self.assertTrue(b["AGGRESSIVE_PAIR_PROFITABLE_BEFORE_FEES"])

    def test_a_missing_quote_is_no_price_not_a_high_price(self):
        b = C.pair_basis(self._book(bid="0.41"))
        self.assertEqual(b["AGGRESSIVE_PAIR_BASIS"], C.NOT_IDENTIFIED)
        self.assertEqual(b["PASSIVE_PAIR_BASIS"], C.NOT_IDENTIFIED)

    def test_decimals_are_exact_not_floats(self):
        b = C.pair_basis(self._book(bid="0.1", ask="0.2"))
        self.assertEqual(b["PASSIVE_PAIR_BASIS"], D("0.9"))
        self.assertIsInstance(b["PASSIVE_PAIR_BASIS"], D)


class TheObservationSetsEveryProbeExplicitly(unittest.TestCase):

    def _book(self, bids=True, offers=True):
        md = {"state": "MARKET_STATE_OPEN",
              "bids": [{"px": {"value": "0.41"}, "qty": "120"}] if bids else [],
              "offers": [{"px": {"value": "0.43"}, "qty": "90"}] if offers
                        else [],
              "stats": {"sharesTraded": "205.06",
                        "lastTradeSetTime": "2026-09-16T15:00:00Z"}}
        return {"marketData": md}

    def test_a_read_book_gives_infeasible_not_unknown(self):
        """We DID read this book, so 'no depth to cross' is a measured
        absence and must not block the comparison."""
        obs = C.observation("m1", self._book(bids=False), None,
                            {"TIME_UNPAIRED_S": 30}, "t", 1.0)
        f = P.feasibility(obs)
        self.assertEqual(f[P.A_PASSIVE_SELL_EXIT], P.INFEASIBLE)
        self.assertEqual(f[P.A_HEDGE], P.INFEASIBLE)
        self.assertNotIn(P.NOT_IDENTIFIED, set(f.values()))

    def test_a_pair_exists_on_the_own_book_without_a_sibling(self):
        """The complement is this book's other side, so a two-sided book
        offers both pair actions even with no sibling contract."""
        obs = C.observation("m1", self._book(), None,
                            {"TIME_UNPAIRED_S": 30}, "t", 1.0)
        self.assertEqual(obs["PAIR_BASIS"]["PASSIVE_PAIR_BASIS"], D("0.98"))
        self.assertEqual(obs["COMPLEMENT_BOOK"], C.NOT_IDENTIFIED)
        f = P.feasibility(obs)
        self.assertEqual(f[P.A_AGGRESSIVE_COMPLEMENT_PAIR], P.FEASIBLE)
        self.assertEqual(f[P.A_PASSIVE_COMPLEMENT_PAIR], P.FEASIBLE)

    def test_a_one_sided_book_offers_no_pair_at_all(self):
        obs = C.observation("m1", self._book(offers=False), None,
                            {"TIME_UNPAIRED_S": 30}, "t", 1.0)
        self.assertEqual(obs["PAIR_BASIS"]["PASSIVE_PAIR_BASIS"],
                         C.NOT_IDENTIFIED)
        f = P.feasibility(obs)
        self.assertEqual(f[P.A_PASSIVE_COMPLEMENT_PAIR], P.INFEASIBLE)

    def test_fair_value_is_never_populated_from_the_venue(self):
        obs = C.observation("m1", self._book(), self._book(),
                            {"TIME_UNPAIRED_S": 30}, "t", 1.0)
        self.assertEqual(obs["FAIR_VALUE"], C.NOT_IDENTIFIED)
        self.assertEqual(P.fair_value_status(obs), P.NOT_IDENTIFIED)

    def test_trade_activity_carries_its_disclaimer(self):
        obs = C.observation("m1", self._book(), None,
                            {"TIME_UNPAIRED_S": 30}, "t", 1.0)
        self.assertTrue(obs["TRADE_ACTIVITY"]["IS_NOT_A_FILL_RATE"])


class TheTelemetryRowIsThePhase2AProduct(unittest.TestCase):

    def setUp(self):
        self.pri = P.load_priors(
            Path(__file__).resolve().parent.parent / "evidence"
            / "whale_audit" / "whale_exit_priors_v1.json")
        md = {"state": "MARKET_STATE_OPEN",
              "bids": [{"px": {"value": "0.41"}, "qty": "120"}],
              "offers": [{"px": {"value": "0.43"}, "qty": "90"}],
              "stats": {"sharesTraded": "205.06"}}
        self.book = {"marketData": md}
        self.obs = C.observation("m1", self.book, self.book,
                                 {"TIME_UNPAIRED_S": 45}, "t", 1.0,
                                 event_id="e1", sport="nfl")

    def test_every_section_7_field_is_present(self):
        row = C.telemetry_row(self.obs, self.pri)
        for f in ("MARKET_ID", "EVENT_ID", "SPORT", "MARKET_TYPE",
                  "TIME_UNPAIRED", "CURRENT_BID", "CURRENT_ASK",
                  "CURRENT_SPREAD", "CURRENT_DEPTH", "TRADE_ACTIVITY",
                  "COMPLEMENT_BID", "COMPLEMENT_ASK", "PAIR_BASIS",
                  "FAIR_VALUE", "FAIR_VALUE_STATUS", "CAPITAL_OCCUPANCY",
                  "EVENT_EXPOSURE", "CORRELATION_EXPOSURE",
                  "ACTION_FEASIBILITY", "ALL_ACTION_EVS", "ACTION_CHOSEN",
                  "ACTIONS_NOT_CHOSEN", "ACTION_REASON", "DUAL_PRIOR"):
            self.assertIn(f, row, f)

    def test_both_priors_are_on_every_row(self):
        d = C.telemetry_row(self.obs, self.pri)["DUAL_PRIOR"]
        self.assertNotEqual(d["COMPLETION_HAZARD_PRIOR3"], C.NOT_IDENTIFIED)
        self.assertNotEqual(d["COMPLETION_HAZARD_PRIOR4"], C.NOT_IDENTIFIED)
        self.assertLess(d["COMPLETION_HAZARD_PRIOR4"],
                        d["COMPLETION_HAZARD_PRIOR3"])
        self.assertIn(d["ACTION_DISAGREEMENT"], ("YES", "NO"))

    def test_the_row_declares_pnl_unidentified_and_unreportable(self):
        row = C.telemetry_row(self.obs, self.pri)
        self.assertEqual(row["BETTOR_EXIT_ENGINE_PNL"], C.NOT_IDENTIFIED)
        self.assertFalse(row["PROFITABILITY_REPORTABLE"])
        self.assertEqual(row["PHASE"], P.PHASE_2A)

    def test_no_action_is_chosen_because_fair_value_is_unidentified(self):
        """Stated in advance. This is the correct Phase-2A output."""
        row = C.telemetry_row(self.obs, self.pri)
        self.assertEqual(row["ACTION_CHOSEN"], P.A_NO_ACTION_RECORDED)
        self.assertEqual(row["FAIR_VALUE_STATUS"], C.NOT_IDENTIFIED)

    def test_the_row_round_trips_with_exact_decimals(self):
        row = C.telemetry_row(self.obs, self.pri)
        back = json.loads(json.dumps(row, default=C._jsonable))
        self.assertEqual(back["CURRENT_BID"], "0.41")
        self.assertEqual(back["PAIR_BASIS"]["PASSIVE_PAIR_BASIS"], "0.98")


class TheSummaryRefusesTheForbiddenFields(unittest.TestCase):

    def test_profitability_win_rate_and_return_are_not_reportable(self):
        s = C.summarise([])
        for k in ("PROFITABILITY", "WIN_RATE", "EXPECTED_MONTHLY_RETURN"):
            self.assertEqual(s[k], "NOT_REPORTABLE_THIS_PHASE")

    def test_the_blocking_reasons_are_counted_separately(self):
        rows = [{"ACTION_REASON": P.COMPARISON_NOT_IDENTIFIED,
                 "FAIR_VALUE_STATUS": P.NOT_IDENTIFIED,
                 "DUAL_PRIOR": {"ACTION_DISAGREEMENT": "NO"}},
                {"ACTION_REASON": P.FEASIBILITY_NOT_IDENTIFIED,
                 "FAIR_VALUE_STATUS": P.FV_VALIDATED,
                 "DUAL_PRIOR": {"ACTION_DISAGREEMENT": "YES"}},
                {"ACTION_REASON": P.DOMINATES,
                 "FAIR_VALUE_STATUS": P.FV_VALIDATED,
                 "DUAL_PRIOR": {"ACTION_DISAGREEMENT": "NO"}}]
        s = C.summarise(rows)
        self.assertEqual(s["TELEMETRY_ROWS"], 3)
        self.assertEqual(s["POSITIONS_BLOCKED_BY_FAIR_VALUE"], 1)
        self.assertEqual(s["POSITIONS_BLOCKED_BY_FILL_UNCERTAINTY"], 1)
        self.assertEqual(s["POSITIONS_WITH_ALL_ACTIONS_PRICED"], 1)
        self.assertEqual(s["PRIOR3_VS_PRIOR4_ACTION_DISAGREEMENT"], 1)


if __name__ == "__main__":
    unittest.main()

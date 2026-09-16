#!/usr/bin/env python3
"""The stage-2 census's capability boundary, proved STRUCTURALLY before contact.

AST scans, not substring scans. A substring scan trips on the prose that states
the guarantee, and that error class has already appeared three times in this
programme.
"""
import ast
import json
import unittest
from pathlib import Path

import census_collect as C

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
                  "send", "request", "stream"}
        self.assertEqual(banned & set(_calls()), set())

    def test_the_only_client_constructed_is_an_httpx_client(self):
        made = [n for n in ast.walk(TREE) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "Client"]
        for n in made:
            self.assertIsInstance(n.func.value, ast.Name)
            self.assertEqual(n.func.value.id, "httpx")

    def test_no_credential_secret_wallet_or_signer_name_is_bound(self):
        banned = ("api_key", "apikey", "secret", "passphrase", "private_key",
                  "privkey", "mnemonic", "wallet", "signer", "sign",
                  "authorization", "bearer", "token")
        for n in ast.walk(TREE):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                self.assertNotIn(n.id.lower(), banned)
            if isinstance(n, ast.arg):
                self.assertNotIn(n.arg.lower(), banned)

    def test_it_imports_no_trading_sdk_and_no_env_reader(self):
        mods = set()
        for n in ast.walk(TREE):
            if isinstance(n, ast.Import):
                for a in n.names:
                    mods.add(a.name.split(".")[0])
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module.split(".")[0])
        for bad in ("os", "subprocess", "socket", "web3", "eth_account",
                    "py_clob_client", "requests", "urllib"):
            self.assertNotIn(bad, mods)

    def test_it_reuses_the_authorized_collector_paths_verbatim(self):
        # The census does not invent an endpoint. It reads the book path the
        # already-authorized collector uses.
        import fwd_collect as F
        self.assertEqual(F.BOOK_PATH, "/v1/markets/{slug}/book")
        self.assertIn("BOOK_PATH", SRC)

    def test_it_writes_no_fill_field(self):
        for bad in ("FILL", "FILLED", "ORDER", "EXPECTANCY", "ROI", "PNL"):
            self.assertNotIn('"%s"' % bad, SRC)


class TheRoutingIsPreregisteredAndFair(unittest.TestCase):

    def _board(self, n=400):
        evs = []
        for i in range(n):
            wide = (i % 3 == 0)
            evs.append({
                "slug": "aec-lg%d-a-b-2026-09-%02d" % (i % 7, (i % 27) + 1),
                "status": "MARKET_STATUS_OPEN",
                "orderPriceMinTickSize": 0.01,
                "board_bestBidQuote": {"value": "0.40", "currency": "USD"},
                "board_bestAskQuote": {"value": "0.90" if wide else "0.41",
                                       "currency": "USD"},
            })
        return evs

    def test_routed_and_rejected_partition_the_board(self):
        b = self._board()
        _, s = C.plan(b)
        self.assertEqual(s["STAGE1_BROAD_ROUTED_MARKETS"]
                         + s["STAGE1_REJECTED_MARKETS"], len(b))

    def test_the_audit_lane_is_a_fraction_of_the_REJECTS(self):
        b = self._board()
        _, s = C.plan(b, audit_fraction=0.10)
        self.assertEqual(s["AUDIT_LANE_MARKETS"],
                         int(s["STAGE1_REJECTED_MARKETS"] * 0.10))

    def test_the_audit_lane_is_not_parked_at_the_end_of_the_scan(self):
        # The trap: audit markets read last get maximum time to tighten, so the
        # false-negative rate would measure scan latency, not routing error.
        # A large audit lane, so the balance check is a real test of the
        # interleaving and not a coin flip on a dozen rows.
        sched, s = C.plan(self._board(3000), audit_fraction=0.9)
        self.assertGreater(s["AUDIT_LANE_MARKETS"], 400)
        third = len(sched) // 3
        first = [l for _, l, _ in sched[:third]].count("AUDIT")
        last = [l for _, l, _ in sched[-third:]].count("AUDIT")
        self.assertGreater(first, 0)
        self.assertGreater(last, 0)
        self.assertLess(abs(first - last) / max(first, last), 0.2)

    def test_the_schedule_is_deterministic_under_one_salt(self):
        b = self._board()
        a1 = [(p, l, e["slug"]) for p, l, e in C.plan(b)[0]]
        a2 = [(p, l, e["slug"]) for p, l, e in C.plan(b)[0]]
        self.assertEqual(a1, a2)

    def test_a_different_salt_gives_a_different_order(self):
        b = self._board()
        a = [e["slug"] for _, _, e in C.plan(b, salt="SALT-A")[0]]
        z = [e["slug"] for _, _, e in C.plan(b, salt="SALT-B")[0]]
        self.assertNotEqual(a, z)

    def test_the_plan_never_claims_fairness_from_position_alone(self):
        _, s = C.plan(self._board())
        self.assertFalse(s["SCHEDULE_FAIRNESS_ASSERTED_FROM_POSITION"])

    def test_profitability_is_not_established_by_this_experiment(self):
        _, s = C.plan(self._board())
        self.assertEqual(s["MAKER_PROFITABILITY"], "NOT_ESTABLISHED")

    def test_the_routed_count_is_named_stage1_broad_not_eligible(self):
        # 9,182 is STAGE1_BROAD_ROUTED_MARKETS. It is NOT
        # PROVEN_MAKER_ELIGIBLE_MARKETS: ACTIVE, HIGH_ACTIVITY and every
        # execution term are still unknown at this point.
        _, s = C.plan(self._board())
        self.assertIn("STAGE1_BROAD_ROUTED_MARKETS", s)
        self.assertNotIn("ELIGIBLE_MARKETS", s)
        self.assertNotIn("PROVEN_MAKER_ELIGIBLE_MARKETS", s)


if __name__ == "__main__":
    unittest.main()


class EveryRowMustSurviveJsonDumps(unittest.TestCase):
    """The census failed its first live run here, and the symptom was visible an
    hour earlier: `SPREAD_TICKS` is a Decimal, so a stage-1 dict cannot be
    json.dumps'd without a default. This pins it."""

    def _row(self):
        return {"slug": "aec-nfl-a-b-2026-09-19",
                "status": "MARKET_STATUS_OPEN",
                "orderPriceMinTickSize": 0.01,
                "board_bestBidQuote": {"value": "0.40", "currency": "USD"},
                "board_bestAskQuote": {"value": "0.42", "currency": "USD"}}

    def test_stage1_output_contains_a_decimal(self):
        from decimal import Decimal
        s1 = C.E.stage1(self._row())
        self.assertIsInstance(s1["SPREAD_TICKS"], Decimal)

    def test_a_stage1_dict_round_trips_through_json(self):
        s1 = C.E.stage1(self._row())
        back = json.loads(json.dumps({"STAGE1": s1}, default=C._jsonable))
        self.assertEqual(back["STAGE1"]["SPREAD_TICKS"], "2")

    def test_the_decimal_is_written_exactly_and_never_as_a_float(self):
        from decimal import Decimal
        # 0.1 is not representable in binary floating point. A float conversion
        # would corrupt a measured value; a string keeps it exact.
        self.assertEqual(C._jsonable(Decimal("0.1")), "0.1")
        self.assertIsInstance(C._jsonable(Decimal("0.1")), str)

    def test_an_unexpected_type_still_raises_rather_than_being_coerced(self):
        with self.assertRaises(TypeError):
            C._jsonable(object())

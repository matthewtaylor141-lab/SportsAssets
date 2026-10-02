"""Actual WS cache, source selector, devig and collector/paper boundary.

Standard-library runner; no sockets, tokens, DB or orders. Collector helpers
are compiled from their actual AST to avoid optional API dependencies. The
full collection cycle, Postgres and release gate remain required separately.
"""
import ast
import asyncio
import copy
import importlib
import json
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "sportsassets"
PKG = "_primary_ws_proof"
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT)]
sys.modules[PKG] = pkg
F = importlib.import_module(PKG + ".pinnapi_feed")
P = importlib.import_module(PKG + ".pinnapi_primary")
D = importlib.import_module(PKG + ".bettor_pinnacle_devig")
EXT = importlib.import_module(PKG + ".bettor_external_shadow")
DP = importlib.import_module(PKG + ".agents.derek_policy")
runtime = types.ModuleType(PKG + ".pinnapi_feed_runtime")
runtime._STATE = {}
sys.modules[runtime.__name__] = runtime


def functions(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)


X = {"__package__": PKG + ".workers", "devig": D,
     "SHARP_BOOKS": {"pinnacle", "betfair_ex_eu"}, "PINNACLE_MAX_AGE_S": 30}
functions("workers/ext_pinnacle_loop.py",
          {"pinnacle_h2h", "primary_pinnacle_h2h", "validate_primary_pinnacle"}, X)
PD = {"__package__": PKG + ".agents", "DP": types.SimpleNamespace(
    R_NO_PINNACLE="NO_PROB", R_STALE="STALE", R_FRESHNESS_UNKNOWN="UNKNOWN")}
functions("agents/paper_derek.py", {"_pinnacle", "recheck_primary_reference"}, PD)
AT = 1791050000.0


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def seed(family="baseball", live=False, moved=True):
    sid = 6 if family == "baseball" else 1
    teams = ("Atlanta Braves", "Los Angeles Dodgers") if sid == 6 else ("Brazil", "Japan")
    stream = "live" if live else "prematch"
    prices = [{"designation": "home", "price": -125},
              {"designation": "away", "price": 110}]
    if sid == 1:
        prices.append({"designation": "draw", "price": 300})
    market = {"key": F.FULL_GAME_MONEYLINE_KEY, "type": "moneyline",
              "period": 0, "status": "open", "prices": prices}
    start = AT - 1800 if live else AT + 3600
    event = {"id": 123, "startTime": iso(start), "isLive": live,
             "participants": [{"name": teams[0], "alignment": "home"},
                              {"name": teams[1], "alignment": "away"}],
             "markets": [market]}
    c = F.FeedCache()
    e = c.new_connection([(stream, sid)])
    c.apply({"type": "snapshot", "stream": stream, "sport_id": sid,
             "ts": (AT-60)*1000, "events": [event]}, epoch=e,
            received_ms=(AT-60)*1000+5)
    if moved:
        moved_market = copy.deepcopy(market)
        moved_market["prices"][0]["price"] = -120
        frame = ({"type": "live", "op": "upd", "rec": {"id": 123,
                  "markets": [moved_market]}} if live else
                 {"type": "prematch_markets", "matchup_id": 123, "data": [moved_market]})
        c.apply(dict(frame, sport_id=sid, ts=(AT-2)*1000), epoch=e,
                received_ms=(AT-2)*1000+5)
    books = [{"key": k, "markets": [{"key": "h2h", "last_update": iso(AT-5),
                "outcomes": [{"name": teams[0], "price": 1.5},
                             {"name": teams[1], "price": 2.5}] +
                            ([{"name": "Draw", "price": 3}] if sid == 1 else [])}]}
             for k in ("pinnacle", "betfair_ex_eu")]
    original = {"id": "discovery-123", "home_team": teams[0], "away_team": teams[1],
                "commence_time": iso(start), "bookmakers": books}
    runtime._STATE = {"owner": types.SimpleNamespace(cache=c), "runtime_id": "r1"}
    return c, original


class PrimarySource(unittest.TestCase):
    def choose(self, event, family="baseball", at=AT):
        return X["primary_pinnacle_h2h"](event, received_at=AT-4, family=family, at=at)

    def test_actual_collector_prefers_ws_prices_and_clocks(self):
        c, ev = seed()
        q = self.choose(ev)
        self.assertEqual(q["reference_input"]["provider"], P.PROVIDER)
        self.assertNotEqual(q["prices"][ev["home_team"]], 1.5)
        self.assertEqual(q["observed_at"], AT-2)
        self.assertEqual(q["received_at"], AT-2+.005)
        self.assertEqual(q["event_id"], ev["id"])
        self.assertTrue(X["validate_primary_pinnacle"](q, at=AT)["ok"])

    def test_soccer_complete_set_and_reversed_teams_are_atomic(self):
        c, ev = seed("soccer", live=True)
        ev["home_team"], ev["away_team"] = ev["away_team"], ev["home_team"]
        q = self.choose(ev, "soccer")
        self.assertEqual(set(q["prices"]), {"Brazil", "Japan", "Draw"})
        self.assertEqual(q["prices"]["Brazil"], F.american_to_decimal(-120))
        v = D.valuation(contract={"sport_family": "soccer", "market": "h2h",
            "selection": "Japan", "event_key": ev["id"], "period": "FULL_GAME", "line": None},
            quote={"book": "pinnacle", "outcomes": q["prices"],
                   "event_key": ev["id"], "period": "FULL_GAME", "line": None,
                   "observed_at": q["observed_at"], "received_at": q["received_at"]}, now=AT)
        self.assertIsNotNone(v["probability"])
        self.assertEqual(v["expected_outcomes"], 3)

    def test_duplicate_pinnacle_or_second_book_cannot_inflate_depth(self):
        _, ev = seed()
        ev["bookmakers"] *= 3
        q = self.choose(ev)
        self.assertEqual(set(q["depth"].values()), {2})
        ev["bookmakers"] = [b for b in ev["bookmakers"] if b["key"] == "pinnacle"]
        self.assertEqual(set(self.choose(ev)["depth"].values()), {1})

    def test_stale_or_future_independent_book_is_not_corroboration(self):
        for delta in (-31, 1):
            with self.subTest(delta=delta):
                _, ev = seed()
                ev["bookmakers"][1]["markets"][0]["last_update"] = iso(AT+delta)
                self.assertEqual(set(self.choose(ev)["depth"].values()), {1})

    def test_snapshot_and_heartbeat_do_not_make_price_current(self):
        c, ev = seed(moved=False)
        before = c.quotes[(123, F.FULL_GAME_MONEYLINE_KEY)].source_change_ms
        c.apply({"type": "ping", "ts": AT*1000}, epoch=c.authority.epoch,
                received_ms=AT*1000)
        q = self.choose(ev)
        self.assertIsNone(before)
        self.assertEqual(q["reference_input"]["fallback_reason"], F.R_NO_CHANGE_TIME)
        self.assertEqual(q["observed_at"], ev["bookmakers"][0]["markets"][0]["last_update"])

    def test_named_fallback_for_unknown_future_stale_and_partial_prices(self):
        for mut, expected in (
            (lambda c: setattr(c.quotes[(123, F.FULL_GAME_MONEYLINE_KEY)], "source_change_ms", None), F.R_NO_CHANGE_TIME),
            (lambda c: setattr(c.quotes[(123, F.FULL_GAME_MONEYLINE_KEY)], "source_change_ms", AT*1000+.1), F.R_FUTURE),
            (lambda c: setattr(c.quotes[(123, F.FULL_GAME_MONEYLINE_KEY)], "source_change_ms", (AT-30)*1000-.1), F.R_STALE),
            (lambda c: c.quotes[(123, F.FULL_GAME_MONEYLINE_KEY)].prices.pop("away"), "PINNAPI_PRIMARY_INCOMPLETE_OUTCOMES")):
            c, ev = seed(); mut(c)
            q = self.choose(ev)
            self.assertEqual(q["reference_input"]["fallback_reason"], expected)
            self.assertEqual(q["prices"][ev["home_team"]], 1.5)

    def test_no_pinnacle_in_rest_still_uses_authoritative_ws(self):
        _, ev = seed()
        ev["bookmakers"] = ev["bookmakers"][1:]
        self.assertEqual(self.choose(ev)["reference_input"]["provider"], P.PROVIDER)

    def test_doubleheader_missing_clock_and_same_city_are_not_guessed(self):
        c, ev = seed()
        c.events[124] = dict(c.events[123], id=124)
        self.assertEqual(self.choose(ev)["reference_input"]["fallback_reason"],
                         "PINNAPI_PRIMARY_FIXTURE_AMBIGUOUS")
        del c.events[124]
        for start in (None, "2026-10-02T12:00:00", float("nan")):
            ev["commence_time"] = start
            self.assertEqual(self.choose(ev)["reference_input"]["fallback_reason"],
                             "PINNAPI_PRIMARY_FIXTURE_UNPROVED")
        c, ev = seed(); ev["away_team"] = "Los Angeles Angels"
        self.assertEqual(self.choose(ev)["reference_input"]["fallback_reason"],
                         "PINNAPI_PRIMARY_NO_EXACT_FIXTURE")

    def test_epoch_lease_change_settlement_and_price_move_invalidate(self):
        for mutate in (lambda c: c.authority.revoke(),
                       lambda c: c.authority.grant([]),
                       lambda c: c.quotes.clear(),
                       lambda c: c.quotes[(123, F.FULL_GAME_MONEYLINE_KEY)].prices.update(home=-130),
                       lambda c: c.events[123].update(isLive=True)):
            c, ev = seed(); q = self.choose(ev); mutate(c)
            self.assertFalse(X["validate_primary_pinnacle"](q, at=AT)["ok"])

    def test_runtime_replacement_invalidates_even_if_epoch_number_equal(self):
        c, ev = seed(); q = self.choose(ev)
        runtime._STATE["runtime_id"] = "replacement"
        self.assertFalse(X["validate_primary_pinnacle"](q, at=AT)["ok"])

    def test_raw_age_boundary_and_latency_at_actual_evaluation(self):
        c, ev = seed(); q = self.choose(ev)
        chk = X["validate_primary_pinnacle"](q, at=AT+28)
        self.assertTrue(chk["ok"])
        self.assertEqual(chk["receipt_to_evaluation_ms"], 29995)
        self.assertFalse(X["validate_primary_pinnacle"](q, at=AT+28.0001)["ok"])

    def test_invalid_reference_cannot_leak_probability_to_paper(self):
        c, ev = seed(); q = self.choose(ev); c.authority.revoke()
        rec = {"probability": .7, "probability_of_selection": .7,
               "valuation": {"probability": .7}, "admissible": True,
               "decision": "BUY", "settlement_comparison": {"fixture_read": True}}
        P.stamp_record(rec, q, X["validate_primary_pinnacle"](q, at=AT))
        self.assertIsNone(rec["probability"])
        self.assertFalse(rec["admissible"])
        self.assertEqual(rec["decision"], "NO_TRADE")
        self.assertTrue(rec["settlement_comparison"]["fixture_read"])
        self.assertEqual(rec["provider"], P.PROVIDER)
        json.dumps(rec, allow_nan=False)

    def test_paper_pass_rechecks_after_book_read_and_keeps_source(self):
        c, ev = seed(); q = self.choose(ev)
        cand = {"pinnacle": {"provider": P.PROVIDER, "reference_input": q["reference_input"],
                            "p": .6, "observed_at": q["observed_at"]}}
        pin = PD["_pinnacle"](cand, at=AT, max_age=30)
        self.assertTrue(pin["qualified"])
        self.assertEqual(pin["reference_input"]["provider"], P.PROVIDER)
        c.authority.revoke(); refusals = []
        PD["recheck_primary_reference"](cand, pin, {"now": AT+1,
            "config": {"entry": {"pinnacle_max_age_s": 30}}}, refusals)
        self.assertTrue(refusals)
        self.assertFalse(pin["final_reference_check"]["qualified"])

    def test_feed_row_without_provenance_is_refused(self):
        seed()
        cand = {"pinnacle": {"provider": P.PROVIDER, "p": .6, "observed_at": AT-1}}
        self.assertEqual(PD["_pinnacle"](cand, at=AT, max_age=30)["refusal"],
                         "PINNAPI_PRIMARY_PROVENANCE_MISSING")

    def test_real_valuation_and_persistence_keep_provider_and_provenance(self):
        c, ev = seed(); q = self.choose(ev)
        rec = EXT.evaluate(contract={"venue": "PMUS", "us_market_slug": "test",
            "sport_family": "baseball", "market": "h2h", "selection": ev["home_team"],
            "event_key": ev["id"], "period": "FULL_GAME", "line": None},
            quote={"book": "pinnacle", "outcomes": q["prices"],
                   "observed_at": q["observed_at"], "received_at": q["received_at"],
                   "period": "FULL_GAME", "line": None, "event_key": ev["id"]},
            now=AT, market_state={"ask": .3, "readable": True, "depth": 100},
            execution_estimate={"p_fill": 1}, size=1,
            risk={"permitted": False}, fee_fn=lambda *args, **kw: 0,
            outcome_books=2, armed=True)
        self.assertIsNotNone(rec["probability"])
        P.stamp_record(rec, q, X["validate_primary_pinnacle"](q, at=AT))
        class Sink:
            async def fetchval(self, query, *args):
                self.args = args
                return 456
        sink = Sink()
        self.assertEqual(asyncio.run(EXT.persist(sink, rec)), 456)
        self.assertEqual(sink.args[3], P.PROVIDER)
        stored = next(json.loads(a) for a in sink.args if isinstance(a, str)
                      and a.startswith('{"reference_input":'))
        self.assertEqual(stored["reference_input"]["source_change_ms"], (AT-2)*1000)
        self.assertEqual(EXT.MIN_OUTCOME_BOOKS, 2)

    def test_real_candidate_decoder_retains_source_for_paper_guard(self):
        c, ev = seed(); q = self.choose(ev)
        cand = DP.candidate_from_row({"provider": P.PROVIDER, "probability": .6,
            "observed_at": q["observed_at"], "received_at": q["received_at"],
            "risk_verdict": {"freshness_evidence": {"pinnacle_limit_s": 30}},
            "settlement_comparison": json.dumps({"reference_input": q["reference_input"]})})
        self.assertTrue(PD["_pinnacle"](cand, at=AT, max_age=30)["qualified"])
        c.authority.revoke()
        self.assertFalse(PD["_pinnacle"](cand, at=AT, max_age=30)["qualified"])

    def test_every_entry_policy_rechecks_before_verdict(self):
        for filename in ("paper_derek.py", "paper_benchmark.py", "paper_explore.py", "paper_maker.py"):
            src = (ROOT / "agents" / filename).read_text()
            tree = ast.parse(src)
            fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "decide_one")
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and
                     ((isinstance(n.func, ast.Name) and n.func.id == "recheck_primary_reference") or
                      (isinstance(n.func, ast.Attribute) and n.func.attr == "recheck_primary_reference"))]
            verdict = next(n for n in fn.body if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "verdict" for t in n.targets))
            self.assertEqual(len(calls), 1, filename)
            self.assertLess(calls[0].lineno, verdict.lineno, filename)


if __name__ == "__main__":
    unittest.main()

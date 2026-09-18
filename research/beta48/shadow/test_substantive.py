#!/usr/bin/env python3
"""THE SUBSTANTIVE PUBLIC-BOOK CAPTURE: selection, identity, harvest.

THE REGRESSION THIS FILE EXISTS FOR. The first public capture wrote `slug` on
its rows and rebuilt event identity afterwards from a separate board file. When
that reconstruction missed, no row could say which contest it belonged to, and
the event-weighted statistics silently degraded into market-weighted ones over
whatever subset survived. Identity now travels on the row, and these tests
refuse to let it stop travelling.

Nothing here contacts a venue.
"""
import json
import tempfile
import unittest
from pathlib import Path

import collect as C
import substantive_capture as SC
import substantive_harvest as SH
import book_schema as BS
import substantive_select as SS

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOW = "2026-09-17T10:00:00+00:00"


def market(slug, start, tids, league="nfl"):
    return {"slug": slug, "id": slug + "-id", "gameStartTime": start,
            "sport": league, "eventTitle": "Contest " + "-".join(map(str, tids)),
            "marketSides": [{"teamId": t, "team": {"id": t, "league": league}}
                            for t in tids]}


def book(bid="0.50", ask="0.51"):
    # NATIVE VENUE SHAPE. The old {"bids": ..., "asks": ...} literal was
    # a shape the venue never sends; it is what hid the book-schema
    # defect. book_schema.native_book emits the production contract.
    return BS.native_book([{"px": {"value": bid}, "qty": "100"}],
                          [{"px": {"value": ask}, "qty": "100"}])


def roster(n_events=3, start="2026-09-17T10:30:00+00:00"):
    ms, books, act = [], {}, {}
    for e in range(n_events):
        for k in range(2):
            s = "ev%d-m%d" % (e, k)
            ms.append(market(s, start, [10 + e * 2, 11 + e * 2]))
            books[s] = book()
            act[s] = {"HIGH_ACTIVITY_AT_DECISION": True}
    return ms, books, act


def froze(n_events=3, now=NOW):
    ms, books, act = roster(n_events)
    return SS.freeze(ms, books, now, activity_of=act)


class SelectionIsFrozenBeforeTheFirstSampledGet(unittest.TestCase):

    def test_three_events_two_markets_each_freeze(self):
        sel = froze()
        self.assertEqual(sel["SELECTION_STATUS"], "FROZEN")
        self.assertEqual(sel["EVENT_SELECTION_FROZEN"], "YES")
        self.assertEqual(sel["EVENTS_SELECTED"], 3)
        self.assertEqual(sel["MARKETS_SELECTED"], 6)
        self.assertEqual(len(set(sel["EVENT_IDS"])), 3)
        self.assertEqual(len(set(sel["MARKET_SLUGS"])), 6)

    def test_two_events_refuses_and_does_not_substitute(self):
        """A short roster WAITS. It never borrows a stale future to fill up."""
        sel = froze(n_events=2)
        self.assertEqual(sel["SELECTION_STATUS"],
                         "INSUFFICIENT_QUALIFYING_EVENTS")
        self.assertEqual(sel["CAPTURE_MAY_START"], "NO")
        self.assertEqual(sel["MARKETS_SELECTED"], 0)
        self.assertTrue(sel["NO_SUBSTITUTION_ON_SHORTFALL"])

    def test_a_season_future_is_not_eligible(self):
        ms, books, act = roster()
        far = market("future-1", "2027-02-14T00:00:00+00:00", [90, 91])
        ms.append(far)
        books["future-1"] = book()
        act["future-1"] = {"HIGH_ACTIVITY_AT_DECISION": True}
        sel = SS.freeze(ms, books, NOW, activity_of=act)
        self.assertNotIn("future-1", sel["MARKET_SLUGS"])

    def test_a_one_sided_book_is_not_a_two_sided_book(self):
        ms, books, act = roster()
        # A ONE-SIDED book in the venue's NATIVE shape: bids, no offers.
        books["ev0-m0"] = {"marketData": {
            "bids": [{"px": {"value": "0.5"}, "qty": "1"}], "offers": []}}
        sel = SS.freeze(ms, books, NOW, activity_of=act)
        self.assertEqual(sel["SELECTION_STATUS"],
                         "INSUFFICIENT_QUALIFYING_EVENTS")

    def test_a_market_with_no_venue_native_identity_is_refused(self):
        """No title heuristics: a market with no team ids has no event."""
        ms, books, act = roster()
        blind = {"slug": "blind-1", "gameStartTime": "2026-09-17T10:30:00+00:00",
                 "title": "Chiefs vs Broncos"}
        ms.append(blind)
        books["blind-1"] = book()
        act["blind-1"] = {"HIGH_ACTIVITY_AT_DECISION": True}
        sel = SS.freeze(ms, books, NOW, activity_of=act)
        self.assertNotIn("blind-1", sel["MARKET_SLUGS"])

    def test_an_inactive_market_does_not_qualify(self):
        ms, books, act = roster()
        act["ev2-m0"] = {"HIGH_ACTIVITY_AT_DECISION": False}
        act["ev2-m1"] = {"HIGH_ACTIVITY_AT_DECISION": False}
        sel = SS.freeze(ms, books, NOW, activity_of=act)
        self.assertEqual(sel["SELECTION_STATUS"],
                         "INSUFFICIENT_QUALIFYING_EVENTS")

    def test_live_and_transition_events_outrank_pure_pregame(self):
        live = SS.event_rank(SS._parse("2026-09-17T09:00:00+00:00"),
                             SS._parse(NOW))
        trans = SS.event_rank(SS._parse("2026-09-17T10:30:00+00:00"),
                              SS._parse(NOW))
        pre = SS.event_rank(SS._parse("2026-09-17T23:00:00+00:00"),
                            SS._parse(NOW))
        self.assertEqual(live[0], 0)
        self.assertEqual(trans[0], 0)
        self.assertEqual(pre[0], 1)
        self.assertLess(live[0], pre[0])

    def test_the_order_is_a_frozen_rule_not_activity_magnitude(self):
        a = froze()
        b = froze()
        self.assertEqual(a["EVENT_IDS"], b["EVENT_IDS"])
        self.assertEqual(a["MARKET_SLUGS"], b["MARKET_SLUGS"])
        self.assertIn("circular", SS.RANK_IS_NOT_ACTIVITY_MAGNITUDE)


class IdentityTravelsWithEveryRow(unittest.TestCase):
    """The named regression. A row that cannot name its own event is not
    evidence about events."""

    BODY = {"bids": [{"price": "0.50", "size": "100"}],
            "asks": [{"price": "0.52", "size": "80"}]}

    def ident(self):
        sel = froze()
        return sel["IDENTITY_OF"]["ev0-m0"], sel

    def test_every_identity_field_is_on_the_row(self):
        blk, _ = self.ident()
        row = C.tick_row("ev0-m0", self.BODY, "2026-09-17T10:00:04+00:00", 0,
                         0.0, identity=blk,
                         request_iso="2026-09-17T10:00:03+00:00")
        for f in C.IDENTITY_FIELDS:
            self.assertIn(f, row, f)
        self.assertNotEqual(row["EVENT_ID"], NOT_IDENTIFIED)
        self.assertEqual(row["MARKET_SLUG"], "ev0-m0")
        self.assertEqual(row["GAME_START"], "2026-09-17T10:30:00+00:00")

    def test_request_and_receipt_are_separate_facts(self):
        blk, _ = self.ident()
        row = C.tick_row("ev0-m0", self.BODY, "2026-09-17T10:00:04+00:00", 0,
                         0.0, identity=blk,
                         request_iso="2026-09-17T10:00:03+00:00")
        self.assertEqual(row["REQUEST_UTC"], "2026-09-17T10:00:03+00:00")
        self.assertEqual(row["RECEIPT_UTC"], "2026-09-17T10:00:04+00:00")
        self.assertNotEqual(row["REQUEST_UTC"], row["RECEIPT_UTC"])

    def test_the_sampling_sequence_is_on_the_row(self):
        blk, _ = self.ident()
        row = C.tick_row("ev0-m0", self.BODY, "t", 7, 28.0, identity=blk)
        self.assertEqual(row["SAMPLING_SEQUENCE"], 7)

    def test_absent_identity_is_not_identified_never_guessed(self):
        """No identity is written NOT_IDENTIFIED per field -- never omitted,
        so a gap is visible on the row rather than inferred from silence."""
        row = C.tick_row("ev0-m0", self.BODY, "t", 0, 0.0)
        self.assertEqual(row["EVENT_ID"], NOT_IDENTIFIED)
        self.assertEqual(row["GAME_START"], NOT_IDENTIFIED)
        self.assertEqual(row["MARKET_SLUG"], "ev0-m0")   # the GET proves this

    def test_identity_survives_into_the_second_tick(self):
        blk, _ = self.ident()
        a = C.tick_row("ev0-m0", self.BODY, "t0", 0, 0.0, identity=blk)
        b = C.tick_row("ev0-m0", self.BODY, "t1", 1, 4.0, prev=a, identity=blk)
        self.assertEqual(b["EVENT_ID"], a["EVENT_ID"])
        self.assertFalse(b["FIRST_OBSERVATION"])


class TheDeclaredDesign(unittest.TestCase):

    def test_the_rate_is_the_confirmed_one_and_is_not_raised(self):
        self.assertEqual(SC.RATE_RPS, "0.25")
        self.assertEqual(SC.REQUEST_INTERVAL_S, 4.0)
        self.assertEqual(SC.CAPTURE_MINUTES, 90)
        self.assertEqual(SC.MARKETS, 6)
        self.assertEqual(SC.NOMINAL_REVISIT_S, 24.0)

    def test_the_plan_matches_the_declared_support(self):
        sel = froze()
        p = SC.plan(sel)
        self.assertEqual(p["MARKETS_SELECTED"], 6)
        self.assertEqual(p["EXPECTED_REQUESTS"], 1350)
        self.assertEqual(p["EXPECTED_OBSERVATIONS_PER_MARKET"], 225)
        self.assertEqual(p["EVENT_SELECTION_FROZEN"], "YES")
        self.assertTrue(p["IDENTITY_TRAVELS_WITH_EVERY_ROW"])
        self.assertIs(p["mirror_live"], False)
        self.assertEqual(p["CREDENTIAL"], "NONE")
        self.assertEqual(p["ORDER_PATH_EXISTS"], "NO")

    def test_the_declared_support_is_arithmetically_consistent(self):
        """1,350 requests over 6 markets is 225 each; at 4 s that is 90 min."""
        self.assertEqual(SC.EXPECTED_REQUESTS // SC.MARKETS,
                         SC.EXPECTED_OBS_PER_MARKET)
        self.assertAlmostEqual(
            SC.EXPECTED_REQUESTS * SC.REQUEST_INTERVAL_S,
            SC.CAPTURE_SECONDS, delta=SC.REQUEST_INTERVAL_S * SC.MARKETS)

    def test_a_short_roster_cannot_start_the_capture(self):
        sel = froze(n_events=2)
        self.assertEqual(SC.may_capture(sel)["CAPTURE_MAY_START"], "NO")

    def test_the_full_roster_may_start(self):
        sel = froze()
        self.assertEqual(SC.may_capture(sel)["CAPTURE_MAY_START"], "YES")


class TheCaptureLoopHonoursTheWindow(unittest.TestCase):

    class FakePacer:
        def wait(self):
            return None

    class FakeHTTP:
        def __init__(self, body):
            self.body = body
            self.n = 0

        def get(self, url, timeout=None, params=None):
            self.n += 1
            outer = self

            class R:
                status_code = 200

                @staticmethod
                def json():
                    return outer.body
            return R()

    def test_the_deadline_ends_the_capture_not_the_round_count(self):
        body = {"bids": [{"price": "0.5", "size": "9"}],
                "asks": [{"price": "0.52", "size": "9"}]}
        with tempfile.TemporaryDirectory() as d:
            res = C.tick_capture(d, ["a", "b"], self.FakePacer(),
                                 self.FakeHTTP(body), rounds=10_000,
                                 deadline_s=0.0)
            self.assertEqual(res["STOPPED_ON"], "DEADLINE")
            self.assertEqual(res["ROUNDS_COMPLETED"], 0)

    def test_identity_reaches_the_written_rows(self):
        body = {"bids": [{"price": "0.5", "size": "9"}],
                "asks": [{"price": "0.52", "size": "9"}]}
        sel = froze()
        with tempfile.TemporaryDirectory() as d:
            C.tick_capture(d, sel["MARKET_SLUGS"][:2], self.FakePacer(),
                           self.FakeHTTP(body), rounds=2,
                           identity_of=sel["IDENTITY_OF"])
            rows = [json.loads(x) for x in
                    (Path(d) / "ticks.jsonl").read_text().splitlines()]
        self.assertTrue(rows)
        for r in rows:
            self.assertNotEqual(r["EVENT_ID"], NOT_IDENTIFIED)
            self.assertNotEqual(r["REQUEST_UTC"], NOT_IDENTIFIED)


class TheHarvest(unittest.TestCase):

    def rows(self, n=225, markets=6, events=3, identified=True):
        out = []
        for i in range(n):
            for m in range(markets):
                ev = "EV%d" % (m // (markets // events))
                out.append({
                    "kind": "TICK", "slug": "m%d" % m, "MARKET_SLUG": "m%d" % m,
                    "EVENT_ID": ev if identified else NOT_IDENTIFIED,
                    "MARKET_ID": "m%d-id" % m, "GAME_START": "2026-09-17T10:30:00+00:00",
                    "SPORT": "nfl", "LEAGUE": "nfl", "MARKET_SIDES": [{}, {}],
                    "EVENT_IDENTITY_LEVEL": "L1",
                    "SAMPLING_SEQUENCE": i, "seq": i,
                    "REQUEST_UTC": "2026-09-17T10:00:00+00:00",
                    "RECEIPT_UTC": "2026-09-17T10:00:01+00:00",
                    "ELAPSED_S": i * 24.0,
                    "BID": "0.50", "ASK": "0.52", "BID_QTY": "10",
                    "ASK_QTY": "10", "SPREAD": "0.02",
                    "TRADE_OCCURRED": (i % 10 == 0),
                    "SHARES_TRADED": str(i), "FIRST_OBSERVATION": i == 0,
                })
        return out

    def write(self, d, rows, errors=()):
        p = Path(d) / "ticks.jsonl"
        with p.open("w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
            for e in errors:
                fh.write(json.dumps(e) + "\n")

    def test_identity_comes_off_the_rows_not_a_board_file(self):
        rows = self.rows()
        m = SH.event_map(rows)
        self.assertEqual(len(set(m.values())), 3)
        cov = SH.identity_coverage(rows)
        self.assertEqual(cov["EVENT_IDENTITY_VALID"], "YES")
        self.assertEqual(cov["EVENT_IDENTITY_COVERAGE"], 1.0)
        self.assertTrue(cov["NOT_RECONSTRUCTED_FROM_A_BOARD_FILE"])

    def test_rows_without_identity_fail_the_identity_verdict(self):
        cov = SH.identity_coverage(self.rows(identified=False))
        self.assertEqual(cov["EVENT_IDENTITY_VALID"], "NO")
        self.assertEqual(cov["ROWS_WITH_EVENT_IDENTITY"], 0)

    def test_support_floors_are_applied(self):
        self.assertEqual(SH.support(self.rows(), [])[
            "SAMPLING_SUPPORT_SUFFICIENT"], "YES")
        self.assertEqual(SH.support(self.rows(n=10), [])[
            "SAMPLING_SUPPORT_SUFFICIENT"], "NO")

    def test_the_three_weightings_are_all_reported(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, self.rows())
            h = SH.harvest(d)
        bm = h["BOOK_METRICS"]
        self.assertIn("EVENT_WEIGHTED_POPULATION", bm)
        self.assertEqual(bm["WEIGHTING"], "MARKET_WEIGHTED_ALL_MARKETS")
        self.assertTrue(any(k.endswith("_EVENT_WEIGHTED_RESOLVED_ONLY")
                            for k in bm))
        self.assertTrue(any(k.endswith("_MARKET_WEIGHTED_RESOLVED_ONLY")
                            for k in bm))

    def test_the_execution_hierarchy_is_never_collapsed(self):
        h = SH.execution_evidence(self.rows())
        self.assertEqual(h["PUBLIC_TICK_FILL_IDENTIFICATION"], "NO")
        self.assertEqual(h["ACTUAL_BETTOR_FILL_RATE"], NOT_IDENTIFIED)
        self.assertEqual(h["REALIZED_MAKER_ECONOMICS"], "NOT_ESTABLISHED")
        self.assertTrue(h["HIERARCHY_IS_NOT_COLLAPSED"])
        self.assertEqual(list(h["EXECUTION_EVIDENCE_HIERARCHY"]),
                         ["TOUCH", "TRADE_EVIDENCE", "COUNTERFACTUAL_FILL",
                          "ACTUAL_BETTOR_FILL"])

    def test_trade_evidence_is_not_our_fill(self):
        h = SH.execution_evidence(self.rows())
        self.assertGreater(h["TRADE_EVIDENCE_OBSERVATIONS"], 0)
        self.assertEqual(h["TRADE_EVIDENCE_IS"],
                         "SOMEBODY_TRADED_NOT_THAT_WE_DID")
        self.assertIn("not", h["VOLUME_CHANGE_IS_NOT_OUR_FILL"])

    def test_shares_traded_stays_diagnostic_until_its_semantics_resolve(self):
        h = SH.execution_evidence(self.rows())
        self.assertTrue(h["SHARES_TRADED_DIAGNOSTIC_ONLY"])
        self.assertEqual(h["COUNTERFACTUAL_FILL_IDENTIFICATION_STATUS"],
                         "NOT_ATTEMPTED_IN_THIS_HARVEST")

    def test_unknown_never_becomes_not_filled(self):
        self.assertIn("UNKNOWN", SH.UNKNOWN_IS_NOT_NOT_FILLED)
        self.assertIn("NOT_FILLED", SH.UNKNOWN_IS_NOT_NOT_FILLED)

    def test_failed_reads_are_named_not_dropped(self):
        errs = [{"kind": "TICK_ERROR", "slug": "m0", "status": 503,
                 "error": "http_503"}]
        with tempfile.TemporaryDirectory() as d:
            self.write(d, self.rows(), errs)
            h = SH.harvest(d)
        self.assertEqual(h["MISSINGNESS"]["FAILED_READS"], 1)
        self.assertEqual(h["MISSINGNESS"]["FAILED_READS_BY_STATUS"]["503"], 1)
        self.assertTrue(h["MISSINGNESS"]["TRUNCATED_RUNS_ARE_NOT_DROPPED"])

    def test_the_canonical_verdicts_are_all_present(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, self.rows())
            h = SH.harvest(d)
        for v in ("CAPTURE_PIPELINE_FUNCTIONAL",
                  "MARKET_CHARACTERIZATION_VALID", "EVENT_IDENTITY_VALID",
                  "SAMPLING_SUPPORT_SUFFICIENT",
                  "PUBLIC_BOOK_STATE_CHARACTERIZATION",
                  "PUBLIC_TICK_FILL_IDENTIFICATION", "ACTUAL_BETTOR_FILL_RATE",
                  "REALIZED_MAKER_ECONOMICS"):
            self.assertIn(v, h, v)
        self.assertEqual(h["MICRO_LIVE_AUTHORIZED"], "NO")
        self.assertIs(h["mirror_live"], False)


class TheSamplingSemanticsWeAlreadyCorrected(unittest.TestCase):
    """§7 was satisfied before this run and must stay satisfied."""

    def test_persistence_is_sampled_never_proven_continuous(self):
        import book_metrics as BM
        rows = [{"slug": "m", "MARKET_SLUG": "m", "seq": i, "ELAPSED_S": i * 24.,
                 "BID": "0.50", "ASK": "0.52", "BID_QTY": "1", "ASK_QTY": "1"}
                for i in range(5)]
        r = BM.persistence_runs(rows)
        self.assertEqual(r["PROVEN_CONTINUOUS_PERSISTENCE_S"], NOT_IDENTIFIED)
        self.assertTrue(any("OBSERVED_RUN_SPAN_S" in k for k in r))

    def test_class_a_counts_are_declared_undercounts(self):
        import book_metrics as BM
        self.assertEqual(BM.CLASS_A_SAMPLING_BIAS_DIRECTION, "UNDERCOUNT")
        self.assertEqual(BM.FREQUENCIES_AS_CONTINUOUS_TIME_RATES,
                         NOT_IDENTIFIED)

    def test_touch_can_never_be_promoted_to_a_fill(self):
        import maker_fill as MF
        with self.assertRaises(Exception):
            MF.promote_touch_to_fill({"TOUCH": True})


if __name__ == "__main__":
    unittest.main()

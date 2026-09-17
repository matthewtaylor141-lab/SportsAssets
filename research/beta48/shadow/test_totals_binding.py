#!/usr/bin/env python3
"""TOTALS BINDING: can an Over/Under market name the contest it is about?

A totals row carries no team id at all -- its two sides are Over and Under
instruments. So the binding has to come from somewhere, and the only honest
question is whether that somewhere is the venue's own structured data or our
imagination. These tests exist to keep it the former.

The dangerous outcome is not an unbound total. It is a total bound to the WRONG
contest, because that silently pollutes every event-level number downstream:
exposure caps, correlation, event-weighted statistics. So the negative controls
below matter more than the coverage ones, and the resolver is required to refuse
rather than guess whenever two contests could both claim a market.

Nothing here contacts a venue.
"""
import json
import unittest
from pathlib import Path

import event_identity as EI
import totals_binding as TB

NOT_IDENTIFIED = "NOT_IDENTIFIED"
FIXTURE = Path(__file__).with_name("fixtures_totals_binding.json")


def team(tid, abbr, league="nfl"):
    return {"teamId": tid, "team": {"id": tid, "abbreviation": abbr,
                                    "league": league}}


def contest_market(slug, start, a, b, kind="SPORTS_MARKET_TYPE_MONEYLINE"):
    return {"slug": slug, "id": slug + "-id", "gameStartTime": start,
            "sportsMarketTypeV2": kind,
            "marketSides": [team(*a), team(*b)]}


def totals_market(slug, start, line="44.5"):
    return {"slug": slug, "id": slug + "-id", "gameStartTime": start,
            "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_TOTAL",
            "line": line, "spreadTotalSuffix": "points",
            "sportsMarketType": "football_team_full_game_total",
            "marketSides": [
                {"id": "s-over", "description": "Over", "long": True},
                {"id": "s-under", "description": "Under", "long": False}]}


START = "2026-09-20T17:00:00Z"


class ATotalResolvesToItsOwnContest(unittest.TestCase):

    def board(self):
        return [
            contest_market("aec-nfl-det-buf-2026-09-20", START,
                           (11, "det"), (12, "buf")),
            contest_market("asc-nfl-det-buf-2026-09-20-neg-3pt5", START,
                           (11, "det"), (12, "buf"),
                           kind="SPORTS_MARKET_TYPE_SPREAD"),
            totals_market("tsc-nfl-det-buf-2026-09-20-44pt5", START),
        ]

    def test_the_total_binds(self):
        b = self.board()
        r = TB.bind_total(b[2], TB.contest_index(b))
        self.assertEqual(r["IDENTITY_STATUS"], "BOUND")
        self.assertNotEqual(r["EVENT_ID"], NOT_IDENTIFIED)

    def test_all_three_families_land_on_one_event_id(self):
        """The whole point: moneyline, spread and total on ONE key."""
        b = self.board()
        ml = EI.event_identity(b[0])[0]
        sp = EI.event_identity(b[1])[0]
        tot = TB.bind_total(b[2], TB.contest_index(b))["EVENT_ID"]
        self.assertEqual(ml, sp)
        self.assertEqual(ml, tot)

    def test_the_event_id_is_the_frozen_key_not_a_new_one(self):
        b = self.board()
        r = TB.bind_total(b[2], TB.contest_index(b))
        self.assertEqual(r["EVENT_ID"], EI.event_identity(b[0])[0])
        self.assertTrue(r["EVENT_ID_IS_THE_FROZEN_KEY"])

    def test_the_required_output_fields_are_present(self):
        b = self.board()
        r = TB.bind_total(b[2], TB.contest_index(b))
        for f in ("EVENT_ID", "MARKET_ID", "MARKET_SLUG", "MARKET_FAMILY",
                  "TOTAL_LINE", "OVER_SIDE_ID", "UNDER_SIDE_ID",
                  "GAME_START_TIME", "SPORT", "LEAGUE", "IDENTITY_SOURCE",
                  "IDENTITY_STATUS", "IDENTITY_CONFIDENCE"):
            self.assertIn(f, r, f)
        self.assertEqual(r["MARKET_FAMILY"], "TOTAL")
        self.assertEqual(r["TOTAL_LINE"], "44.5")
        self.assertEqual(r["OVER_SIDE_ID"], "s-over")
        self.assertEqual(r["UNDER_SIDE_ID"], "s-under")
        self.assertEqual(r["LEAGUE"], "nfl")

    def test_over_and_under_come_from_the_venues_own_description(self):
        b = self.board()
        r = TB.bind_total(b[2], TB.contest_index(b))
        self.assertNotEqual(r["OVER_SIDE_ID"], r["UNDER_SIDE_ID"])


class DifferentContestsNeverCollide(unittest.TestCase):

    def two_games_same_kickoff(self):
        return [
            contest_market("aec-nfl-det-buf-2026-09-20", START,
                           (11, "det"), (12, "buf")),
            contest_market("aec-nfl-kc-lv-2026-09-20", START,
                           (13, "kc"), (14, "lv")),
            totals_market("tsc-nfl-det-buf-2026-09-20-44pt5", START),
            totals_market("tsc-nfl-kc-lv-2026-09-20-47pt5", START),
        ]

    def test_same_kickoff_same_league_two_games_bind_separately(self):
        b = self.two_games_same_kickoff()
        idx = TB.contest_index(b)
        a = TB.bind_total(b[2], idx)
        c = TB.bind_total(b[3], idx)
        self.assertEqual(a["IDENTITY_STATUS"], "BOUND")
        self.assertEqual(c["IDENTITY_STATUS"], "BOUND")
        self.assertNotEqual(a["EVENT_ID"], c["EVENT_ID"])
        self.assertEqual(a["CANDIDATE_CONTESTS"], 1)

    def test_a_total_for_a_different_day_does_not_bind(self):
        b = self.two_games_same_kickoff()
        stray = totals_market("tsc-nfl-det-buf-2026-09-27-44pt5",
                              "2026-09-27T17:00:00Z")
        r = TB.bind_total(stray, TB.contest_index(b))
        self.assertEqual(r["IDENTITY_STATUS"], "UNBOUND_NO_CANDIDATE_CONTEST")
        self.assertEqual(r["EVENT_ID"], NOT_IDENTIFIED)

    def test_one_matching_abbreviation_is_not_enough(self):
        """A team is not a fixture. Both codes or nothing."""
        b = [contest_market("aec-nfl-det-buf-2026-09-20", START,
                            (11, "det"), (12, "buf")),
             totals_market("tsc-nfl-det-total-2026-09-20-24pt5", START)]
        r = TB.bind_total(b[1], TB.contest_index(b))
        self.assertEqual(r["IDENTITY_STATUS"], "UNBOUND_NO_CANDIDATE_CONTEST")

    def test_two_claimable_contests_are_refused_not_picked(self):
        """Fail closed. A coin-flip between contests is the worst outcome."""
        b = [contest_market("aec-x-aa-bb-2026-09-20", START,
                            (21, "aa"), (22, "bb")),
             # A pathological second contest whose codes are a subset of the
             # same slug's tokens: both would claim the total.
             contest_market("aec-y-aa-bb2-2026-09-20", START,
                            (23, "aa"), (24, "bb")),
             totals_market("tsc-x-aa-bb-2026-09-20-40pt5", START)]
        r = TB.bind_total(b[2], TB.contest_index(b))
        self.assertEqual(r["IDENTITY_STATUS"], "AMBIGUOUS_REFUSED")
        self.assertEqual(r["EVENT_ID"], NOT_IDENTIFIED)
        self.assertGreaterEqual(r["CANDIDATE_CONTESTS"], 2)

    def test_time_proximity_is_never_enough(self):
        near = "2026-09-20T17:05:00Z"          # five minutes out
        b = [contest_market("aec-nfl-det-buf-2026-09-20", START,
                            (11, "det"), (12, "buf")),
             totals_market("tsc-nfl-det-buf-2026-09-20-44pt5", near)]
        r = TB.bind_total(b[1], TB.contest_index(b))
        self.assertEqual(r["IDENTITY_STATUS"], "UNBOUND_NO_CANDIDATE_CONTEST")
        self.assertIn("IDENTICAL", TB.NOT_TIME_PROXIMITY.upper())


class NoTitleHeuristicIsUsed(unittest.TestCase):

    def test_the_resolver_never_reads_title_or_question(self):
        """Scans the CODE, not the prose -- the module docstring legitimately
        contains the words it is promising not to act on."""
        src = Path(TB.__file__).read_text()
        code = src.split('"""', 2)[2] if src.count('"""') >= 2 else src
        # Callable names, not the words. The module now DECLARES the eight
        # forbidden fallbacks in NO_FALLBACK, so scanning for the word "fuzzy"
        # would match the promise rather than a violation of it.
        for bad in ('.get("title")', ".get('title')", '.get("question")',
                    ".get('question')", "difflib", "SequenceMatcher",
                    "get_close_matches", "rapidfuzz", "fuzzywuzzy", "fuzz."):
            self.assertNotIn(bad, code, bad)
        self.assertNotIn("import difflib", src)

    def test_the_prohibition_is_declared_as_well_as_observed(self):
        for phrase in ("title parsing", "fuzzy matching", "nearest start time",
                       "single-team inference", "handwritten exceptions"):
            self.assertIn(phrase, TB.NO_FALLBACK, phrase)

    def test_a_rich_title_cannot_rescue_an_unbindable_total(self):
        b = [contest_market("aec-nfl-det-buf-2026-09-20", START,
                            (11, "det"), (12, "buf"))]
        t = totals_market("tsc-nfl-unknown-2026-09-20-44pt5", START)
        t["title"] = "Detroit Lions vs Buffalo Bills"
        t["question"] = "Will DET vs BUF go over 44.5?"
        r = TB.bind_total(t, TB.contest_index(b))
        self.assertEqual(r["IDENTITY_STATUS"], "UNBOUND_NO_CANDIDATE_CONTEST")

    def test_abbreviations_come_from_the_venue_not_from_the_slug(self):
        """The codes are read off the contest's own team objects."""
        b = [contest_market("aec-nfl-g1-2026-09-20", START,
                            (11, "det"), (12, "buf")),
             totals_market("tsc-nfl-det-buf-2026-09-20-44pt5", START)]
        r = TB.bind_total(b[1], TB.contest_index(b))
        self.assertEqual(r["IDENTITY_STATUS"], "BOUND")


class UnresolvedTotalsFailClosed(unittest.TestCase):

    def test_an_unbound_total_carries_no_event_id(self):
        r = TB.bind_total(totals_market("tsc-x-2026-09-20-1pt5", START), {})
        self.assertEqual(r["EVENT_ID"], NOT_IDENTIFIED)
        self.assertEqual(r["IDENTITY_SOURCE"], NOT_IDENTIFIED)
        self.assertEqual(r["IDENTITY_CONFIDENCE"], NOT_IDENTIFIED)

    def test_a_non_totals_row_is_rejected_by_family(self):
        m = contest_market("aec-nfl-det-buf-2026-09-20", START,
                           (11, "det"), (12, "buf"))
        r = TB.bind_total(m, TB.contest_index([m]))
        self.assertEqual(r["IDENTITY_STATUS"], "NOT_A_TOTALS_MARKET")

    def test_no_market_is_silently_assigned(self):
        """Every record states its status explicitly."""
        b = [contest_market("aec-nfl-det-buf-2026-09-20", START,
                            (11, "det"), (12, "buf")),
             totals_market("tsc-nfl-det-buf-2026-09-20-44pt5", START),
             totals_market("tsc-nfl-nope-2026-09-20-9pt5", START)]
        rep = TB.bind_all(b)
        self.assertEqual(rep["TOTALS_MARKETS_OBSERVED"], 2)
        self.assertEqual(rep["TOTALS_CANONICALLY_BOUND"], 1)
        self.assertEqual(rep["TOTALS_UNBOUND"], 1)
        for r in rep["RECORDS"]:
            self.assertIn(r["IDENTITY_STATUS"],
                          ("BOUND", "UNBOUND_NO_CANDIDATE_CONTEST",
                           "AMBIGUOUS_REFUSED", "NOT_A_TOTALS_MARKET"))


class TheSealedBoardEvidence(unittest.TestCase):
    """Real venue rows: 200 totals and the contests they belong to."""

    def load(self):
        return json.loads(FIXTURE.read_text())

    def test_the_sealed_totals_bind_at_high_coverage(self):
        d = self.load()
        rep = TB.bind_all(d["CONTEST_ROWS"] + d["TOTALS_ROWS"])
        self.assertGreater(rep["TOTALS_MARKETS_OBSERVED"], 0)
        self.assertGreater(rep["TOTALS_BINDING_COVERAGE_PCT"], 90.0)

    def test_the_sealed_evidence_produces_no_ambiguity(self):
        d = self.load()
        rep = TB.bind_all(d["CONTEST_ROWS"] + d["TOTALS_ROWS"])
        self.assertEqual(rep["TOTALS_AMBIGUOUS_REFUSED"], 0)

    def test_real_triplets_exist(self):
        d = self.load()
        rep = TB.bind_all(d["CONTEST_ROWS"] + d["TOTALS_ROWS"])
        self.assertGreater(rep["MONEYLINE_SPREAD_TOTAL_EVENT_TRIPLETS"], 0)

    def test_no_stronger_canonical_id_was_found(self):
        d = self.load()
        rep = TB.bind_all(d["CONTEST_ROWS"] + d["TOTALS_ROWS"])
        self.assertEqual(rep["STRONGER_CANONICAL_EVENT_ID_AVAILABLE"], "NO")
        self.assertIn("no eventId", rep["NO_EXPLICIT_VENUE_EVENT_ID"])

    def test_simultaneous_contests_in_the_real_board_do_not_share_codes(self):
        """The negative control, on real rows rather than constructed ones."""
        d = self.load()
        idx = TB.contest_index(d["CONTEST_ROWS"])
        by_start = {}
        for rec in idx.values():
            by_start.setdefault(rec["GAME_START"], []).append(rec)
        checked = 0
        for recs in by_start.values():
            for i in range(len(recs)):
                for j in range(i + 1, len(recs)):
                    checked += 1
                    self.assertFalse(recs[i]["ABBREVIATIONS"]
                                     & recs[j]["ABBREVIATIONS"])
        self.assertGreater(checked, 0, "fixture has no simultaneous contests")


class TheResolverAttachesButNeverCreatesIdentity(unittest.TestCase):
    """The architectural condition, tested rather than asserted in a comment.

    A resolver that could mint an EVENT_ID from a totals slug would look
    identical on a healthy board -- the coverage number would be the same. The
    difference shows only on a board where the contest is ABSENT, which is
    exactly the board these tests use.
    """

    def test_a_board_of_totals_alone_resolves_nothing(self):
        """No contest rows means no identities, so nothing can attach."""
        board = [totals_market("tsc-nfl-det-buf-2026-09-20-44pt5", START),
                 totals_market("tsc-nfl-kc-lv-2026-09-20-47pt5", START)]
        rep = TB.bind_all(board)
        self.assertEqual(rep["CONTESTS_INDEXED"], 0)
        self.assertEqual(rep["TOTALS_MARKETS_OBSERVED"], 2)
        self.assertEqual(rep["TOTALS_CANONICALLY_BOUND"], 0)
        self.assertEqual(rep["TOTALS_UNBOUND"], 2)

    def test_a_totals_row_contributes_no_key_to_the_contest_index(self):
        """The index is built from contest rows; totals cannot enter it."""
        tot = totals_market("tsc-nfl-det-buf-2026-09-20-44pt5", START)
        self.assertEqual(TB.contest_index([tot]), {})
        con = contest_market("aec-nfl-det-buf-2026-09-20", START,
                             (11, "det"), (12, "buf"))
        with_total = TB.contest_index([con, tot])
        without = TB.contest_index([con])
        self.assertEqual(set(with_total), set(without))

    def test_every_bound_event_id_already_existed(self):
        b = [contest_market("aec-nfl-det-buf-2026-09-20", START,
                            (11, "det"), (12, "buf")),
             totals_market("tsc-nfl-det-buf-2026-09-20-44pt5", START)]
        rep = TB.bind_all(b)
        self.assertEqual(rep["EVENT_IDS_INVENTED_BY_TOTALS"], 0)
        self.assertTrue(rep["EVERY_BOUND_EVENT_ID_PREEXISTED"])
        self.assertTrue(rep["ATTACHES_NEVER_CREATES"])
        self.assertEqual(rep["IDENTITY_AUTHORITY"], "VENUE_CONTEST_ROWS_ONLY")

    def test_the_sealed_board_invents_no_identity_either(self):
        d = json.loads(FIXTURE.read_text())
        rep = TB.bind_all(d["CONTEST_ROWS"] + d["TOTALS_ROWS"])
        self.assertEqual(rep["EVENT_IDS_INVENTED_BY_TOTALS"], 0)
        self.assertEqual(rep["CANONICAL_EVENTS_FROM_CONTEST_ROWS_ONLY"],
                         rep["CONTESTS_INDEXED"])

    def test_a_bound_record_states_the_identity_was_attached(self):
        b = [contest_market("aec-nfl-det-buf-2026-09-20", START,
                            (11, "det"), (12, "buf")),
             totals_market("tsc-nfl-det-buf-2026-09-20-44pt5", START)]
        r = TB.bind_total(b[1], TB.contest_index(b))
        self.assertTrue(r["IDENTITY_ATTACHED_NOT_CREATED"])
        self.assertTrue(r["EVENT_ID_EXISTED_INDEPENDENTLY"])
        self.assertEqual(len(r["BIND_CONDITIONS"]), 4)

    def test_an_unbound_record_says_the_event_id_did_not_exist(self):
        r = TB.bind_total(totals_market("tsc-x-2026-09-20-1pt5", START), {})
        self.assertFalse(r["EVENT_ID_EXISTED_INDEPENDENTLY"])
        self.assertEqual(r["RESOLUTION_STATUS"],
                         "UNBOUND_NO_CANDIDATE_CONTEST")

    def test_the_seven_provenance_fields_are_on_every_attached_total(self):
        """Section 7 of the brief, field by field."""
        b = [contest_market("aec-nfl-det-buf-2026-09-20", START,
                            (11, "det"), (12, "buf")),
             contest_market("asc-nfl-det-buf-2026-09-20-neg-3", START,
                            (11, "det"), (12, "buf"),
                            kind="SPORTS_MARKET_TYPE_SPREAD"),
             totals_market("tsc-nfl-det-buf-2026-09-20-44pt5", START)]
        r = TB.bind_total(b[2], TB.contest_index(b))
        for f in ("EVENT_ID", "TOTAL_MARKET_ID", "TOTAL_MARKET_SLUG",
                  "GAME_START_TIME", "MATCHED_TEAM_ABBREVIATIONS",
                  "SOURCE_CONTEST_MARKET_IDS", "IDENTITY_SOURCE",
                  "RESOLVER_VERSION", "RESOLUTION_STATUS"):
            self.assertIn(f, r, f)
            self.assertNotEqual(r[f], NOT_IDENTIFIED, f)
        self.assertEqual(sorted(r["MATCHED_TEAM_ABBREVIATIONS"]),
                         ["buf", "det"])
        # The audit trail names the rows that established the identity, so a
        # reader can go and check that it existed without this total.
        self.assertEqual(sorted(r["SOURCE_CONTEST_MARKET_IDS"]),
                         ["aec-nfl-det-buf-2026-09-20",
                          "asc-nfl-det-buf-2026-09-20-neg-3"])
        self.assertEqual(r["RESOLVER_VERSION"], "TOTALS_BINDING_V1")

    def test_the_eight_forbidden_fallbacks_are_named_in_the_record(self):
        r = TB.bind_total(totals_market("tsc-x-2026-09-20-1pt5", START), {})
        for phrase in ("title parsing", "question parsing", "fuzzy matching",
                       "partial abbreviation", "nearest start time",
                       "league-plus-approximate", "single-team inference",
                       "handwritten exceptions"):
            self.assertIn(phrase, r["NO_FALLBACK"], phrase)


class AddingTotalsIsNotPermissionToTrade(unittest.TestCase):

    def test_the_module_says_so_and_places_no_order(self):
        rep = TB.bind_all([])
        self.assertIn("EV and risk independently",
                      rep["ADDING_TOTALS_INCREASES_THE_OPPORTUNITY_SET_"
                          "NOT_PERMISSION"])
        src = Path(TB.__file__).read_text().lower()
        for bad in ("httpx", "requests", "submit", "https://"):
            self.assertNotIn(bad, src, bad)


if __name__ == "__main__":
    unittest.main()

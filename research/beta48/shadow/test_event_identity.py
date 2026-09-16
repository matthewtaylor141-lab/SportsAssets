#!/usr/bin/env python3
"""Venue-native event identity. Read from the venue's fields, never a slug."""
import unittest

import event_identity as EI


def side(team_id=None, provider=None, desc="Yes", listed=False):
    s = {"description": desc}
    if team_id is not None:
        s["teamId"] = team_id
        t = {"id": team_id, "league": "nfl", "abbreviation": "xx"}
        if provider is not None:
            t["providerId" if not listed else "providerIds"] = (
                provider if not listed
                else [{"provider": "P", "providerId": provider}])
        s["team"] = t
    return s


def row(slug, start=None, sides=()):
    m = {"slug": slug, "marketSides": list(sides)}
    if start:
        m["gameStartTime"] = start
    return m


class IdentityComesFromVenueFields(unittest.TestCase):

    def test_two_teams_and_a_start_is_a_contest(self):
        m = row("aec-nfl-det-buf", "2026-09-18T00:15:00Z",
                [side(58), side(51)])
        key, lv = EI.event_identity(m)
        self.assertEqual(lv, EI.LEVEL_V1_CONTEST)
        self.assertEqual(key, "2026-09-18T00:15:00Z|51-58")

    def test_the_key_is_order_independent(self):
        a = EI.event_identity(row("a", "T", [side(58), side(51)]))[0]
        b = EI.event_identity(row("b", "T", [side(51), side(58)]))[0]
        self.assertEqual(a, b)

    def test_one_team_is_a_subject_not_a_contest(self):
        m = row("tec-mlb-champ-atl", "2026-09-07T00:00:00Z",
                [side(3001), side(3001, desc="No")])
        key, lv = EI.event_identity(m)
        self.assertEqual(lv, EI.LEVEL_V2_SUBJECT)
        self.assertIn("SUBJECT=", key)
        self.assertNotIn(lv, EI.LEVELS_THAT_ARE_A_CONTEST)

    def test_a_start_time_alone_is_refused_as_an_identity(self):
        """The error that killed the slug key: a field that CORRELATES with
        identity is not an identity. Dozens of unrelated markets share a
        start."""
        key, lv = EI.event_identity(row("prop-1", "2026-09-18T00:15:00Z", []))
        self.assertEqual(key, EI.NOT_IDENTIFIED)
        self.assertEqual(lv, EI.LEVEL_V3_NONE)

    def test_teams_without_a_start_time_are_not_an_event(self):
        key, lv = EI.event_identity(row("x", None, [side(58), side(51)]))
        self.assertEqual(key, EI.NOT_IDENTIFIED)
        self.assertEqual(lv, EI.LEVEL_V3_NONE)

    def test_both_provider_shapes_are_read(self):
        """The scalar is what this venue sends; the list is what eligibility
        expected. Reading both means a venue change degrades gracefully."""
        self.assertEqual(EI.provider_ids(row("a", "T", [side(1, provider=11)])),
                         {11})
        self.assertEqual(
            EI.provider_ids(row("b", "T", [side(1, provider=11, listed=True)])),
            {11})

    def test_a_missing_marketsides_is_empty_not_an_error(self):
        self.assertEqual(EI.sides({"slug": "x"}), [])
        self.assertEqual(EI.team_ids({"slug": "x"}), set())


class GroupingNeverOverMerges(unittest.TestCase):

    def _board(self):
        return [
            row("g1-ml", "T1", [side(58), side(51)]),
            row("g1-spread", "T1", [side(58), side(51)]),
            row("g2-ml", "T1", [side(70), side(71)]),   # SAME start, other game
            row("fut-a", "T2", [side(3001), side(3001, desc="No")]),
            row("fut-b", "T2", [side(3002), side(3002, desc="No")]),
            row("prop-1", "T1", []),
        ]

    def test_two_games_at_one_start_are_two_events(self):
        idx = EI.index_events(self._board())
        self.assertEqual(idx["VALID_EVENTS"], 2)
        self.assertFalse(idx["START_TIME_ALONE_USED_AS_IDENTITY"])

    def test_markets_of_one_contest_group_together(self):
        idx = EI.index_events(self._board())
        sizes = sorted(len(v) for v in idx["EVENTS"].values())
        self.assertEqual(sizes, [1, 2])

    def test_two_futures_at_one_start_are_never_merged(self):
        """A futures book is not a game. Merging on start would reproduce the
        over-merge that killed the slug key."""
        idx = EI.index_events(self._board())
        self.assertEqual(idx["SUBJECT_GROUP_COUNT"], 2)

    def test_an_unbound_prop_has_no_identity_and_is_counted(self):
        idx = EI.index_events(self._board())
        self.assertEqual(idx["MARKETS_WITH_NO_EVENT_IDENTITY"], 1)
        self.assertIn("prop-1", idx["UNRESOLVED_SLUGS"])

    def test_the_event_count_is_a_count_and_says_nothing_about_capital(self):
        idx = EI.index_events(self._board())
        c = EI.independent_event_count(idx)
        self.assertEqual(c["INDEPENDENT_EVENT_COUNT"], 2)
        self.assertEqual(c["VALIDATED_DISTINCT_EVENTS"], 2)
        self.assertEqual(c["DEPLOYABLE_CAPITAL_CAPACITY"], EI.NOT_IDENTIFIED)
        self.assertFalse(c["MARKET_COUNT_USED_AS_CAPACITY"])
        self.assertFalse(c["EVENT_COUNT_USED_AS_CAPACITY"])

    def test_capital_capacity_stays_unidentified_even_on_a_clean_board(self):
        """Resolving every identity settles the COUNT and nothing else."""
        clean = [row("g1", "T1", [side(58), side(51)]),
                 row("g2", "T1", [side(70), side(71)])]
        c = EI.independent_event_count(EI.index_events(clean))
        self.assertTrue(c["EVENT_IDENTITY_COMPLETE"])
        self.assertEqual(c["INDEPENDENT_EVENT_COUNT"], 2)
        self.assertEqual(c["DEPLOYABLE_CAPITAL_CAPACITY"], EI.NOT_IDENTIFIED)

    def test_the_missing_capacity_inputs_are_named_on_the_row(self):
        c = EI.independent_event_count(EI.index_events(self._board()))
        for need in ("ACTUAL_FILLABILITY", "AVAILABLE_DEPTH", "QUOTE_SIZE",
                     "INVENTORY_DURATION", "CAPITAL_TURNOVER",
                     "EVENT_EXPOSURE_LIMIT", "CORRELATION", "RISK_BUDGET",
                     "EXECUTION_COSTS"):
            self.assertIn(need, c["CAPACITY_ADDITIONALLY_REQUIRES"])

    def test_the_old_capacity_name_is_gone(self):
        self.assertFalse(hasattr(EI, "capacity"))

    def test_markets_per_event_exposes_the_correlation(self):
        mpe = EI.markets_per_event(EI.index_events(self._board()))
        self.assertEqual(mpe["N_EVENTS"], 2)
        self.assertEqual(mpe["MAX"], 2)
        self.assertEqual(mpe["TOTAL_MARKETS_IN_EVENTS"], 3)


class TheFrozenSlugReaderIsUntouched(unittest.TestCase):

    def test_eligibility_keeps_its_frozen_semantics(self):
        """This module is ADDITIVE. The frozen reader is not replaced, so its
        published validation result still describes the thing it validated."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                               / "forward"))
        import eligibility as E
        self.assertFalse(E.EVENT_KEY_VALIDATED)
        self.assertEqual(E.EVENT_KEY_METHOD, "DERIVED_HEURISTIC_V1")

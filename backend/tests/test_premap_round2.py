"""Leak-hunt round 2 (2026-08-24): five confirmed defects in the
rewritten premap lane, each pinned by the reviewer's own reproduction.

These are money-path invariants — a regression here is a wrong-game or
wrong-market order, which is the incident class the whole lane exists
to make impossible.
"""

import asyncio

import pytest

from sportsassets.workers import premap


class _Pool:
    """Serves the premap SELECT from a fixed row list."""

    def __init__(self, rows):
        self.rows = rows
        self.last_keys = None

    async def fetch(self, sql, *a):
        assert "us_premap" in sql
        self.last_keys = set(a[0])
        return [r for r in self.rows
                if set(r["event_keys"]) & self.last_keys]


def _row(identifier, side_norm, keys, line=""):
    return {"identifier": identifier, "side_norm": side_norm,
            "kind": "side", "line": line, "question": "q",
            "event_title": "t", "event_keys": keys,
            "intent": "ORDER_INTENT_BUY_LONG"}


class TestGameAgreement:
    """Defect #2: title keys were date-free, so a whale's trade on one
    game resolved to a DIFFERENT game's live orderable side. The
    reviewer reproduced mlb-nyy-bos-2026-08-23 -> the 08-27 game."""

    def _rows(self):
        k23 = premap.event_keys_for("New York Yankees vs. Boston Red Sox",
                                    "atc-mlb-nyy-bos-2026-08-23-nyy")
        k27 = premap.event_keys_for("New York Yankees vs. Boston Red Sox",
                                    "atc-mlb-nyy-bos-2026-08-27-nyy")
        return [_row("atc-mlb-nyy-bos-2026-08-23-nyy", "new york yankees", k23),
                _row("atc-mlb-nyy-bos-2026-08-27-nyy", "new york yankees", k27)]

    def test_dated_signal_never_matches_another_days_game(self):
        pool = _Pool(self._rows())
        hit = asyncio.run(premap.resolve(
            pool, "New York Yankees vs. Boston Red Sox", None,
            "New York Yankees", "mlb-nyy-bos-2026-08-23"))
        assert hit is not None, "the whale's OWN game must still resolve"
        assert hit["market_slug"] == "atc-mlb-nyy-bos-2026-08-23-nyy"

    def test_the_other_days_row_alone_is_not_a_candidate(self):
        # only the 08-27 row exists; a 08-23 signal must refuse rather
        # than take the wrong game
        pool = _Pool(self._rows()[1:])
        hit = asyncio.run(premap.resolve(
            pool, "New York Yankees vs. Boston Red Sox", None,
            "New York Yankees", "mlb-nyy-bos-2026-08-23"))
        assert hit is None

    def test_keys_carry_both_bare_and_stamped_forms(self):
        keys = premap.event_keys_for("New York Yankees vs. Boston Red Sox",
                                     "atc-mlb-nyy-bos-2026-08-23-nyy")
        assert "new york yankees vs boston red sox" in keys
        assert "new york yankees vs boston red sox@2026-08-23" in keys
        assert premap.dated_keys(keys), "a dated slug must stamp keys"


class TestMarketTypeAgreement:
    """Defect #1: every market on an event shares one key set, so the
    candidate pool was the whole board — a moneyline pick could land on
    a spread, a segment or a prop carrying the same team name."""

    def _mixed(self):
        keys = premap.event_keys_for("Kansas City Chiefs vs. Buffalo Bills",
                                     "atc-nfl-kc-buf-2026-09-13-kc")
        return [
            _row("atc-nfl-kc-buf-2026-09-13-kc", "kansas city chiefs", keys),
            # a prop/segment market whose side carries the same name
            _row("astatc-nfl-kc-buf-2026-09-13-kc", "kansas city chiefs",
                 keys),
        ]

    def test_moneyline_pick_ignores_the_prop_row(self):
        pool = _Pool(self._mixed())
        hit = asyncio.run(premap.resolve(
            pool, "Kansas City Chiefs vs. Buffalo Bills", None,
            "Kansas City Chiefs", "nfl-kc-buf-2026-09-13"))
        assert hit is not None
        assert hit["market_slug"].startswith("atc-"), \
            "a moneyline pick must never resolve onto a prop market"

    def test_unknown_market_type_refuses(self):
        pool = _Pool(self._mixed())
        # a slug whose type the grammar does not recognize
        hit = asyncio.run(premap.resolve(
            pool, "Kansas City Chiefs vs. Buffalo Bills", None,
            "Kansas City Chiefs", "weird-format-no-type"))
        assert hit is None, "an unrecognized market type must fail closed"


class TestSpreadLines:
    """Defect #4: _market_rows stamps the line on spread rows and the
    named branch excluded every lined row, so spreads wrote rows that
    could never match — the lane was silently dead for the type."""

    def test_lined_pick_matches_its_lined_row(self):
        rows = [{"identifier": "asc-nfl-kc-buf-2026-09-13-kc",
                 "side_norm": "kansas city chiefs  3 5", "line": "3.5",
                 "signed": "-3.5",
                 "kind": "side", "question": "Spread: Kansas City (-3.5)"},
                {"identifier": "asc-nfl-kc-buf-2026-09-13-buf",
                 "side_norm": "buffalo bills  3 5", "line": "3.5",
                 "signed": "+3.5",
                 "kind": "side", "question": "Spread: Buffalo (+3.5)"}]
        hit = premap.match_side(rows, "Kansas City Chiefs -3.5",
                                "Spread: Kansas City Chiefs (-3.5)")
        assert hit and hit["identifier"].endswith("-kc")

    def test_a_row_missing_its_sign_refuses_a_signed_pick(self):
        """Fail closed: if the venue row does not state a sign we
        cannot prove the handicap direction, so we do not bet it."""
        rows = [{"identifier": "asc-x-kc",
                 "side_norm": "kansas city chiefs  3 5", "line": "3.5",
                 "kind": "side", "question": "Spread"}]
        assert premap.match_side(
            rows, "Kansas City Chiefs -3.5", "Spread (-3.5)") is None

    def test_unlined_pick_still_refuses_lined_rows(self):
        rows = [{"identifier": "asc-x-kc", "side_norm": "kansas city chiefs",
                 "line": "3.5", "kind": "side", "question": "q"}]
        assert premap.match_side(rows, "Kansas City Chiefs", "q") is None

    def test_wrong_line_never_matches(self):
        rows = [{"identifier": "asc-x-kc",
                 "side_norm": "kansas city chiefs  3 5", "line": "3.5",
                 "kind": "side", "question": "q"}]
        assert premap.match_side(
            rows, "Kansas City Chiefs -7.5",
            "Spread: Kansas City Chiefs (-7.5)") is None


class TestPartialSweepNeverFallsBack:
    """Defect #3: the except wrapped the whole page loop, so a page-7
    timeout routed a HEALTHY sweep into the degraded markets fallback,
    whose keys overwrite good rows table-wide."""

    def test_mid_sweep_failure_keeps_rows_and_skips_fallback(self,
                                                            monkeypatch):
        state = {"pages": 0, "fallback_calls": 0}
        ev = {"slug": "atc-mlb-nyy-bos-2026-08-23-nyy",
              "title": "New York Yankees vs. Boston Red Sox",
              "markets": [{"slug": "atc-mlb-nyy-bos-2026-08-23-nyy",
                           "question": "New York Yankees vs. Boston Red Sox",
                           "marketSides": [
                               {"identifier": "atc-mlb-nyy-bos-2026-08-23-nyy",
                                "description": "Yankees"},
                               {"identifier": "atc-mlb-nyy-bos-2026-08-23-bos",
                                "description": "Red Sox"}]}]}

        class _Events:
            def list(self, q):
                state["pages"] += 1
                if state["pages"] > 2:      # probe + page1 ok, then die
                    raise RuntimeError("venue 429")
                return {"events": [ev] * premap.PAGE_LIMIT}

        class _Markets:
            def list(self, q):
                state["fallback_calls"] += 1
                return {"markets": []}

        class _Client:
            events, markets = _Events(), _Markets()

        writes = []

        class _WPool:
            async def execute(self, sql, *a):
                if "us_premap" in sql and "INSERT" in sql:
                    writes.append(a[0])
                return "DELETE 0"

            async def fetchval(self, *a):
                return None

        monkeypatch.setattr(premap.pmus, "_get_client", lambda: _Client())
        monkeypatch.setattr(premap, "get_pool",
                            lambda: asyncio.sleep(0, result=_WPool()))
        monkeypatch.setattr(premap, "_ensure_table",
                            lambda pool: asyncio.sleep(0))
        summary = asyncio.run(premap.refresh())
        assert writes, "rows written before the failure are kept"
        assert state["fallback_calls"] == 0, \
            "a partially successful sweep must NOT run the degraded fallback"
        assert summary["mode"] == "events/partial"
        assert summary["err"], "the failure stays on the record"


class TestHandicapSignIsNeverErased:
    """Leak-hunt round 3: _norm strips punctuation, so 'Chiefs -3.5' and
    'Chiefs +3.5' both normalize to 'chiefs  3 5'. A whale taking +3.5
    (getting points) therefore matched the venue's -3.5 side (giving
    points) — the opposite bet — on every spread copy."""

    def _rows(self):
        from sportsassets.workers.premap import _norm, signed_line

        return [{"identifier": "asc-kc",
                 "side_norm": _norm("Kansas City Chiefs -3.5"),
                 "line": "3.5",
                 "signed": signed_line("Kansas City Chiefs -3.5"),
                 "kind": "side", "question": "Spread"},
                {"identifier": "asc-buf",
                 "side_norm": _norm("Buffalo Bills +3.5"),
                 "line": "3.5",
                 "signed": signed_line("Buffalo Bills +3.5"),
                 "kind": "side", "question": "Spread"}]

    def test_the_inversion_is_closed(self):
        # the venue lists KC only at -3.5; a +3.5 KC pick has no side
        assert premap.match_side(
            self._rows(), "Kansas City Chiefs +3.5",
            "Spread: KC (+3.5)") is None

    def test_each_side_still_matches_its_own_sign(self):
        assert premap.match_side(
            self._rows(), "Kansas City Chiefs -3.5",
            "Spread: KC (-3.5)")["identifier"] == "asc-kc"
        assert premap.match_side(
            self._rows(), "Buffalo Bills +3.5",
            "Spread: BUF (+3.5)")["identifier"] == "asc-buf"

    def test_signed_line_reads_both_directions(self):
        from sportsassets.workers.premap import signed_line

        assert signed_line("Chiefs -3.5") == "-3.5"
        assert signed_line("Bills +3.5") == "+3.5"
        assert signed_line("Over 47.5") == ""      # totals carry no sign
        assert signed_line(None) == ""


class TestWholeNumberLinesAndSignOrdering:
    """Leak-hunt round 3, the subtlest inversion found today. Only
    \\d+\\.5 was ever parsed as a line, so a WHOLE-number handicap ('-3')
    left both the venue row and the whale's pick unlined — and the sign
    check lived INSIDE the line branch, so it was skipped exactly when
    no line parsed. _norm erases +/-, so 'Chiefs -3' and 'Chiefs +3'
    are the same string: the opposite bet, silently."""

    def _rows(self):
        m = {"slug": "asc-nfl-kc-buf-2026-09-13", "question": "Spread",
             "marketSides": [
                 {"identifier": "asc-kc", "description": "Chiefs -3",
                  "long": True},
                 {"identifier": "asc-buf", "description": "Bills +3",
                  "long": False}]}
        return premap._market_rows({"slug": "e",
                                    "title": "Chiefs vs. Bills"}, m)

    def test_whole_number_lines_are_stamped_on_rows(self):
        rows = self._rows()
        assert {r["line"] for r in rows} == {"3"}
        assert {r["signed"] for r in rows} == {"-3", "+3"}

    def test_the_whole_number_inversion_is_closed(self):
        assert premap.match_side(self._rows(), "Chiefs +3",
                                 "Spread: Chiefs (+3)") is None

    def test_each_whole_number_side_still_matches_itself(self):
        rows = self._rows()
        assert premap.match_side(
            rows, "Chiefs -3", "Spread: Chiefs (-3)")["identifier"] == "asc-kc"
        assert premap.match_side(
            rows, "Bills +3", "Spread: Bills (+3)")["identifier"] == "asc-buf"

    def test_totals_read_whole_numbers_too(self):
        from sportsassets.workers.premap import _lines_of

        assert _lines_of("O/U 47") == {"47"}
        assert _lines_of("Over 2.5") == {"2.5"}
        assert _lines_of("Spread: Chiefs -3") == {"3"}


class TestOverUnderRequiresItsLine:
    """Leak-hunt round 3, the last of the ten confirmed findings.
    line_ok ended in a bare `return True`, so a row whose line failed
    to stamp satisfied ANY lined pick: a whale's Over 2.5 matched an
    Over 9.5 row — a different bet at score 1.0."""

    def test_unlined_row_never_satisfies_a_lined_pick(self):
        rows = [{"identifier": "tsc-x-o-9pt5", "side_norm": "over 9 5",
                 "line": "", "kind": "side", "question": "Total"}]
        assert premap.match_side(rows, "Over 2.5", "O/U 2.5") is None

    def test_each_line_still_matches_its_own_row(self):
        rows = [{"identifier": "tsc-o-2pt5", "side_norm": "over 2 5",
                 "line": "2.5", "kind": "side", "question": "Total"},
                {"identifier": "tsc-o-9pt5", "side_norm": "over 9 5",
                 "line": "9.5", "kind": "side", "question": "Total"}]
        assert premap.match_side(
            rows, "Over 2.5", "O/U 2.5")["identifier"] == "tsc-o-2pt5"
        assert premap.match_side(
            rows, "Over 9.5", "O/U 9.5")["identifier"] == "tsc-o-9pt5"

    def test_a_pick_with_no_line_cannot_take_a_total(self):
        rows = [{"identifier": "tsc-o-2pt5", "side_norm": "over 2 5",
                 "line": "2.5", "kind": "side", "question": "Total"}]
        assert premap.match_side(rows, "Over", None) is None


class TestWordFormTotalsAreNotSpreads:
    """market_type_of classified 'over-2pt5' as SPREAD ('over' is four
    characters, so it slipped past the >4 unknown-word guard) while
    'under-2pt5' returned unknown. With the market-type gate live that
    routed a TOTALS bet onto the game's SPREAD market — and made the
    outcome depend on which word the feed happened to use."""

    def test_word_form_over_and_under_are_totals(self):
        from sportsassets.copy_sports import market_type_of

        for slug in ("epl-ars-che-2026-08-24-over-2pt5",
                     "epl-ars-che-2026-08-24-under-2pt5",
                     "epl-ars-che-2026-08-24-ou-2pt5",
                     "epl-x-2026-08-24-ht-over-1pt5"):
            assert market_type_of(slug) == "total", slug

    def test_a_team_qualified_over_is_a_prop_not_the_game_total(self):
        from sportsassets.copy_sports import market_type_of

        assert market_type_of("epl-x-2026-08-24-home-over-3pt5") == "prop"

    def test_real_spreads_still_classify_as_spreads(self):
        from sportsassets.copy_sports import market_type_of

        assert market_type_of("nba-bos-mia-2026-08-24-bos-neg-10") == "spread"
        assert market_type_of("epl-ars-che-2026-08-24-3pt5") == "spread"


class TestCertificationAuditFindings:
    """The 80-agent mapping certification audit (2026-08-24 evening).
    Five of its seven confirmed findings were already closed by earlier
    rounds; these pin the reproductions so they stay closed, and cover
    the two that were still open — both of which weakened the
    VERIFICATION instrument rather than the mapper."""

    def _board(self, venue_q, venue_slug):
        m = {"slug": venue_slug, "question": venue_q, "marketSides": [
            {"identifier": venue_slug, "description": "Yes", "long": True},
            {"identifier": venue_slug, "description": "No", "long": False}]}
        ev = {"slug": "asc-mlb-wsn-nym-2026-08-13",
              "title": "Nationals vs Mets"}
        rows = premap._market_rows(ev, m)
        keys = premap.event_keys_for(ev["title"], ev["slug"])
        for r in rows:
            r["event_keys"] = keys
        return rows

    def _resolve(self, rows, whale_title, whale_slug, outcome="Yes"):
        class _P:
            async def fetch(self, sql, *a):
                k = set(a[0])
                return [r for r in rows if set(r["event_keys"]) & k]

        return asyncio.run(premap.resolve(
            _P(), whale_title, "Nationals vs Mets", outcome, whale_slug))

    def test_the_mirror_framed_market_is_refused(self):
        """The audit's headline: the whale buys YES on 'Nationals cover
        -1.5' and the US board frames the SAME game from the other
        team. Matching the literal 'yes' bought the METS covering — the
        exact inverse of his bet."""
        rows = self._board(
            "Will the New York Mets cover -1.5 against the Washington "
            "Nationals?", "asc-mlb-wsn-nym-2026-08-13-nym-neg-1pt5")
        assert self._resolve(
            rows, "Spread: Washington Nationals (-1.5)",
            "mlb-wsn-nym-2026-08-13-wsn-neg-1pt5") is None

    def test_a_yes_on_the_wrong_line_is_refused(self):
        rows = self._board("Will the Washington Nationals cover -1.5?",
                           "asc-mlb-wsn-nym-2026-08-13-wsn-neg-1pt5")
        assert self._resolve(
            rows, "Will the Washington Nationals cover -2.5?",
            "mlb-wsn-nym-2026-08-13-wsn-neg-2pt5") is None

    def test_the_right_market_still_resolves(self):
        rows = self._board("Will the Washington Nationals cover -1.5?",
                           "asc-mlb-wsn-nym-2026-08-13-wsn-neg-1pt5")
        hit = self._resolve(
            rows, "Will the Washington Nationals cover -1.5?",
            "mlb-wsn-nym-2026-08-13-wsn-neg-1pt5")
        assert hit["market_slug"] == "asc-mlb-wsn-nym-2026-08-13-wsn-neg-1pt5"


class TestTheCrossCheckIsActuallyIndependent:
    """Two audit findings, both about the instrument rather than the
    mapper — the more dangerous kind, because they inflate confidence."""

    def test_it_refuses_to_replay_the_resolver_that_mapped_the_trade(self):
        """The cross-check calls pmus.resolve_market_exact. For a
        mapping the EXACT resolver produced that is a bit-for-bit
        replay: it agrees with itself and certifies nothing."""
        from sportsassets import live_executor as le

        v, d = asyncio.run(le._independent_check(
            "aec-atp-x-y-2026-08-24", "Player A", "A vs. B",
            "atp-x-y-2026-08-24", "ORDER_INTENT_BUY_LONG",
            mapping_src="exact"))
        assert v == "unverified" and "replay" in d

    def test_a_premap_mapping_is_still_cross_checked(self, monkeypatch):
        from sportsassets import live_executor as le
        from sportsassets import pmus

        monkeypatch.setattr(
            pmus, "resolve_market_exact",
            lambda c, o: {"market_slug": "aec-atp-x-y-2026-08-24",
                          "intent": "ORDER_INTENT_BUY_LONG"})
        v, _ = asyncio.run(le._independent_check(
            "aec-atp-x-y-2026-08-24", "Player A", "A vs. B",
            "atp-x-y-2026-08-24", "ORDER_INTENT_BUY_LONG",
            mapping_src="premap"))
        assert v == "ok", "premap mappings ARE checkable by the exact path"


class TestTheSignedColumnIsActuallyPersisted:
    """`signed` was produced on every row and stored nowhere.

    match_side._lined_ok requires the venue side's sign to equal the
    whale's, and reads it off the row. us_premap had no such column, so
    every row loaded from the table carried signed="" — and any whale
    pick stating a sign hit `not rs` and refused. Every SIGNED SPREAD
    was structurally unresolvable through premap, which is the only
    lane allowed to trade under the quarantine.

    Found by an adversarial review, in the same pass that found the
    overspend breaker. Both are the same shape: a value converted in the
    place it is computed and not in the place it is consumed.
    """

    def test_the_row_builder_produces_it(self):
        rows = premap._market_rows(
            {"slug": "asc-nfl-kc-buf-2026-08-25", "title": "KC vs BUF"},
            {"slug": "asc-nfl-kc-buf-2026-08-25", "question": "Spread",
             "marketSides": [
                 {"identifier": "asc-nfl-kc-buf-2026-08-25-kc",
                  "description": "Kansas City Chiefs -3.5", "long": True},
                 {"identifier": "asc-nfl-kc-buf-2026-08-25-buf",
                  "description": "Buffalo Bills +3.5", "long": False}]})
        signs = {r.get("signed") for r in rows}
        assert signs - {None, ""}, "the builder must stamp a sign"

    def test_the_upsert_writes_it(self):
        import inspect

        src = inspect.getsource(premap._upsert)
        assert "signed" in src.split("VALUES")[0], "not in the column list"
        assert "signed=$" in src, "not in the DO UPDATE set"
        assert 'r.get("signed")' in src, "not in the argument list"

    def test_both_readers_select_it(self):
        import inspect

        for fn in (premap.resolve, premap.resolve_explain):
            # 'event_slug' joined the list 2026-08-27 when gate 11 of
            # the bridge stopped being vacuous; signed must still be
            # selected by BOTH readers.
            assert ("intent, signed, event_slug, market_slug "
                    "FROM us_premap" in inspect.getsource(fn)), \
                fn.__name__

    def test_the_column_exists_in_the_table_definition(self):
        import inspect

        src = inspect.getsource(premap._ensure_table)
        assert "signed text" in src
        assert "ADD COLUMN IF NOT EXISTS signed text" in src, \
            "an existing deployment needs the ALTER, not just the CREATE"

    def test_there_is_a_migration_for_it(self):
        from pathlib import Path

        root = Path(premap.__file__).resolve().parents[2]
        m = root / "migrations" / "031_us_premap_signed.sql"
        assert m.exists()
        assert "ADD COLUMN IF NOT EXISTS signed" in m.read_text()


class TestTheSignGuardIsNowFunctionalNotBlanket:
    """From 'refuse every signed pick' to 'refuse MISMATCHED signs'.
    Strictly more matching, and the inversion protection is what
    actually starts working."""

    def _rows(self, stored_sign_kc, stored_sign_buf):
        return [{"identifier": "asc-kc",
                 "side_norm": premap._norm("Kansas City Chiefs -3.5"),
                 "line": "3.5", "signed": stored_sign_kc,
                 "kind": "side", "question": "Spread"},
                {"identifier": "asc-buf",
                 "side_norm": premap._norm("Buffalo Bills +3.5"),
                 "line": "3.5", "signed": stored_sign_buf,
                 "kind": "side", "question": "Spread"}]

    def _live(self):
        return self._rows(premap.signed_line("Kansas City Chiefs -3.5"),
                          premap.signed_line("Buffalo Bills +3.5"))

    def test_a_signed_pick_refused_when_the_column_was_empty(self):
        """The old production behaviour, reproduced: every DB row read
        back with signed=None."""
        assert premap.match_side(self._rows(None, None),
                                 "Kansas City Chiefs -3.5",
                                 "Spread: KC (-3.5)") is None

    def test_the_same_pick_matches_once_the_sign_is_stored(self):
        hit = premap.match_side(self._live(), "Kansas City Chiefs -3.5",
                                "Spread: KC (-3.5)")
        assert hit is not None and hit["identifier"] == "asc-kc"

    def test_the_OTHER_side_still_matches_its_own_sign(self):
        hit = premap.match_side(self._live(), "Buffalo Bills +3.5",
                                "Spread: BUF (+3.5)")
        assert hit is not None and hit["identifier"] == "asc-buf"

    def test_THE_INVERSION_IS_STILL_REFUSED(self):
        """_norm erases +/-, so 'Chiefs -3.5' and 'Chiefs +3.5'
        normalize identically. The venue lists KC only at -3.5, so a
        +3.5 KC pick — the opposite bet — must still find nothing.
        This is the incident this lane exists to prevent and the whole
        reason the column has to be real rather than absent."""
        assert premap.match_side(self._live(), "Kansas City Chiefs +3.5",
                                 "Spread: KC (+3.5)") is None

    def test_an_unsigned_pick_on_unsigned_rows_is_unaffected(self):
        rows = [{"identifier": "atc-kc", "side_norm": "kansas city chiefs",
                 "line": "", "signed": "", "kind": "side",
                 "question": "Moneyline"}]
        hit = premap.match_side(rows, "Kansas City Chiefs", "Moneyline")
        assert hit is not None


class TestQuestionLineSurvivesDates:
    """Round 28 (owner's unmapped report, 60% no_side_match): the line
    stamp demanded exactly ONE number in the market question, so a
    date ('Aug 28', '8/28', '2026-08-28') voided the stamp and every
    over/under pick on that market refused. Dates are never lines;
    a lone decimal among leftovers is the line; real ambiguity still
    stamps '' and refuses — the wrong-line class stays impossible."""

    def test_iso_date_no_longer_voids_the_line(self):
        from sportsassets.workers.premap import _question_line
        assert _question_line(
            'Total runs in TEX-MIL on 2026-08-28: over 6.5?') == '6.5'

    def test_slash_and_word_dates_are_stripped(self):
        from sportsassets.workers.premap import _question_line
        assert _question_line(
            'Rangers @ Brewers 8/28: total runs over/under 6.5?') == '6.5'
        assert _question_line(
            'NFL: over/under 47 points, Aug 28 kickoff') == '47'

    def test_genuine_two_line_ambiguity_still_refuses(self):
        from sportsassets.workers.premap import _question_line
        assert _question_line('over 6.5 or under 9.5 double market') == ''

    def test_market_rows_stamp_the_recovered_line(self):
        from sportsassets.workers.premap import _market_rows, match_side
        ev = {'slug': 'tex-mil-2026-08-28', 'title': 'TEX vs MIL'}
        m = {'slug': 'tsc-tex-mil-2026-08-28-tot-6pt5',
             'question': 'Total runs in TEX-MIL on 2026-08-28: over 6.5?',
             'marketSides': [
                 {'identifier': 'tsc-tex-mil-2026-08-28-tot-6pt5',
                  'description': 'Over'},
                 {'identifier': 'tsc-tex-mil-2026-08-28-tot-6pt5',
                  'description': 'Under'},
             ]}
        rows = _market_rows(ev, m)
        assert [r['line'] for r in rows] == ['6.5', '6.5'], \
            'the date in the question must not void the line stamp'
        # and the whale's Over on total-6pt5 now maps end-to-end
        hit = match_side(rows, 'Over', 'TEX vs MIL total 6.5',
                         'mlb-tex-mil-2026-08-28-total-6pt5')
        assert hit is not None and hit['side_norm'] == 'over'


class TestLineCanonicalization:
    """Live census 2026-08-29: the venue writes '1.50' where the
    whale's slug decodes to '1.5' — every line comparison in the
    matcher is string equality, so numerically identical lines failed
    line_ok/_lined_ok and the row refused as no_side_match. One
    spelling per value, on both sides."""

    def test_lines_of_drops_trailing_zeros(self):
        assert premap._lines_of("Spread -1.50") == {"1.5"}
        assert premap._lines_of("Over 2.50") == {"2.5"}
        assert premap._lines_of("O/U 47") == {"47"}
        assert premap._lines_of("total 3.0") == {"3"}

    def test_signed_line_canonicalizes_the_magnitude(self):
        assert premap.signed_line("Braves -1.50") == "-1.5"
        assert premap.signed_line("Rockies +1.5") == "+1.5"

    def test_venue_trailing_zero_matches_the_whale_line(self):
        rows = [
            {"identifier": "x-atl", "side_norm": "atlanta braves",
             "line": next(iter(premap._lines_of("-1.50"))),
             "signed": premap.signed_line("Atlanta Braves -1.50"),
             "intent": "BUY_LONG"},
            {"identifier": "x-col", "side_norm": "colorado rockies",
             "line": next(iter(premap._lines_of("+1.50"))),
             "signed": premap.signed_line("Colorado Rockies +1.50"),
             "intent": "BUY_LONG"},
        ]
        hit = premap.match_side(
            rows, "Atlanta Braves",
            "Spread: Atlanta Braves (-1.5)",
            "mlb-col-atl-2026-08-28-spread-home-1pt5")
        assert hit is not None and hit["identifier"] == "x-atl", \
            "a numerically identical line must never refuse the row"

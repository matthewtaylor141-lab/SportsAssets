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

"""C9 review pins (2026-09-10, coverage lane C9, the adversarial review).

CRITICAL-1. The sweep hands `event_keys_for` the venue's EVENT slug, and
that slug is KINDLESS: the stored keys of a real row
(hard2/premap_rows_1909.log:58, `atc-els-kbk-tro-2026-09-06-kbk`) are
`{els-kbk-tro-2026-09-06, "kristiansund bk vs tromso il", ...}` -- no
kind-prefixed sibling, so the event slug the sweep keyed from was
`els-kbk-tro-2026-09-06`, and for the Patriots/Seahawks event it is
`nfl-ne-sea-2026-09-09`, which `_nfl_slug_parts` accepts. A football date
key built INSIDE `event_keys_for` therefore shifts the venue's rows too
(`nfl-ne-sea-2026-09-08` / `-10`, `ne-sea-2026-09-08` / `-10` on every
ne-sea row after its next sweep), and the two shifts meet two days apart:
his `nfl-ne-sea-2026-09-11` bound `aec-nfl-ne-sea-2026-09-09` through the
wording arm (which reads no identifier date) with no `date_shift` and no
refusal, and with the switch OFF in the resolver the rows keyed while it
was ON still bound. The key is HIS side's alone only when the RESOLVERS
add it (`nfl_date_keys` in `resolve` / `resolve_explain`) and
`event_keys_for` stays 6509878 byte for byte.

HIGH-1. The venue's NFL ladders carry 1h / 2h / 1q..4q rows under the
same event keys (hard2/nflrows_0041.txt table 5: 276 tsc and 374 asc rows
on ne-sea; `tsc-nfl-ne-sea-2026-09-09-1h-16pt5`), the wording arm reads
no segment, and `c3_same_segment` runs only under PREMAP_YN_IDENTITY: a
full-game total whose line the venue lists on a HALF ladder alone bound
the half row with the identity switch off. His segment or nothing, on
either setting, for the football date key's rows.
"""
from __future__ import annotations

import asyncio

import pytest

from sportsassets.workers import premap
from tests import test_c9_nfl_board as T

VENUE_EVENT_SLUG = f"nfl-ne-sea-{T.VENUE_DATE}"          # the sweep's own shape: kindless
VENUE_EVENT_TITLE = "New England Patriots vs. Seattle Seahawks"


@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    monkeypatch.delenv(premap.PREMAP_NFL_DATE_TOL_ENV, raising=False)
    monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)


def _board_as_the_sweep_keys_it(markets, ev_slug=VENUE_EVENT_SLUG, ev_title=VENUE_EVENT_TITLE):
    """The rows exactly as `refresh` writes them: `event_keys_for(ev.title,
    ev.slug)` on the venue's KINDLESS event slug, then keys_for_row."""
    keys = premap.event_keys_for(ev_title, ev_slug)
    rows = []
    for m in markets:
        for r in premap._market_rows({"slug": ev_slug, "title": ev_title}, m):
            r["event_keys"] = premap.keys_for_row(keys, r)
            rows.append(r)
    return rows


def _resolve(rows, slug, title, outcome):
    return asyncio.run(premap.resolve(T._Pool(rows), title, None, outcome, slug))


def _explain(rows, slug, title, outcome):
    return asyncio.run(premap.resolve_explain(T._Pool(rows), title, None, outcome, slug))


class TestCritical1TheVenueSideNeverShifts:
    def test_the_sweeps_call_on_the_venues_kindless_event_slug_is_6509878_byte_for_byte(self, monkeypatch):
        # with no title: the two keys of 6509878 and nothing else
        assert premap.event_keys_for(None, VENUE_EVENT_SLUG) == [f"ne-sea-{T.VENUE_DATE}", f"nfl-ne-sea-{T.VENUE_DATE}"]
        assert premap.event_keys_for(None, "nfl-ari-lac-2026-09-13") == ["ari-lac-2026-09-13", "nfl-ari-lac-2026-09-13"]
        # with the venue's title: the same set the switch OFF builds (OFF is
        # 6509878 byte for byte), and no key on any other date
        on = premap.event_keys_for(VENUE_EVENT_TITLE, VENUE_EVENT_SLUG)
        monkeypatch.setenv(premap.PREMAP_NFL_DATE_TOL_ENV, "off")
        off = premap.event_keys_for(VENUE_EVENT_TITLE, VENUE_EVENT_SLUG)
        assert on == off
        assert not any(k.endswith(("2026-09-08", "2026-09-10")) for k in on), on
        # event_keys_for itself carries no football date key: the resolvers do
        import inspect
        assert "nfl_date_keys" not in inspect.getsource(premap.event_keys_for)
        for fn in (premap.resolve, premap.resolve_explain):
            assert "keys.update(nfl_date_keys(global_slug))" in inspect.getsource(fn), fn.__name__

    def test_the_rows_the_sweep_writes_carry_the_venues_date_alone(self):
        rows = _board_as_the_sweep_keys_it(T._venue())
        for r in rows:
            assert f"nfl-ne-sea-{T.VENUE_DATE}" in r["event_keys"] and f"ne-sea-{T.VENUE_DATE}" in r["event_keys"]
            assert not any(k.endswith((T.HIS_DATE, "2026-09-08")) for k in r["event_keys"]), r["identifier"]

    def test_two_days_off_never_binds_and_never_reads_as_a_shift(self):
        rows = _board_as_the_sweep_keys_it(T._venue())
        for his in ("nfl-ne-sea-2026-09-11", "nfl-ne-sea-2026-09-07"):
            for slug, title, outcome in ((his, T.HIS_TITLE, "Patriots"), (his, T.HIS_TITLE, "Seahawks"),
                                         (his + "-total-41pt5", "Patriots vs. Seahawks: O/U 41.5", "Over"),
                                         (his + "-spread-home-3pt5", "Spread: Seahawks (-3.5)", "Seahawks")):
                assert _resolve(rows, slug, title, outcome) is None, (slug, outcome)
                ex = _explain(rows, slug, title, outcome)
                assert ex["step"] == "no_key_intersection" and ex["rows"] == 0, (slug, ex)
                assert "date_shift" not in ex and "date_candidates" not in ex

    def test_one_day_off_binds_with_the_shift_and_the_same_date_without(self):
        rows = _board_as_the_sweep_keys_it(T._venue())
        hit = _resolve(rows, T.HIS_ML, T.HIS_TITLE, "Patriots")
        assert T._short(hit) == (T.AEC, "patriots", T.LONG, "premap")
        assert hit["date_shift"] == {"his": T.HIS_DATE, "venue": T.VENUE_DATE}
        same = _resolve(rows, f"nfl-ne-sea-{T.VENUE_DATE}", T.HIS_TITLE, "Seahawks")
        assert T._short(same) == (T.AEC, "seahawks", T.SHORT, "premap") and "date_shift" not in same

    def test_the_switch_off_in_the_resolver_is_todays_refusal_whatever_the_sweep_wrote(self, monkeypatch):
        rows = _board_as_the_sweep_keys_it(T._venue())          # keyed with the switch ON
        monkeypatch.setenv(premap.PREMAP_NFL_DATE_TOL_ENV, "off")
        assert _resolve(rows, T.HIS_ML, T.HIS_TITLE, "Patriots") is None
        ex = _explain(rows, T.HIS_ML, T.HIS_TITLE, "Patriots")
        assert ex["step"] == "no_key_intersection" and ex["rows"] == 0 and ex["keys"] == 2 + 4 or ex["keys"] >= 2
        assert "date_shift" not in ex


class TestHigh1TheSegmentLaddersNeverBindAFullGameSlug:
    # ONE segment row per line the full game does not list (27.5 on the
    # 1h ladder alone, 13.5 on the 1q ladder alone): a twin on a second
    # segment would make the wording arm refuse as ambiguous and hide the
    # hole; the venue's ladders step by 2 on the full game (24.5, 26.5, ..
    # nflrows_0041 rows 571-586) and by 1 on the halves, so a lone half
    # row at an odd line is the realistic shape
    SEGS = [T._tsc("tsc-nfl-ne-sea-2026-09-09-1h-27pt5", T.Q_TSC.format(L="27.5")),
            T._tsc("tsc-nfl-ne-sea-2026-09-09-1q-13pt5", T.Q_TSC.format(L="13.5")),
            T._tsc("tsc-nfl-ne-sea-2026-09-09-1h-41pt5", T.Q_TSC.format(L="41.5")),
            T._tsc("tsc-nfl-ne-sea-2026-09-09-1q-41pt5", T.Q_TSC.format(L="41.5")),
            T._asc("asc-nfl-ne-sea-2026-09-09-1h-pos-3pt5", T._asc_q("", "3.5"), "3.5")]

    @pytest.mark.parametrize("identity", ["", "on"])
    def test_a_line_the_venue_lists_on_a_half_ladder_alone_never_binds_the_half_row(self, monkeypatch, identity):
        if identity:
            monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, identity)
        rows = _board_as_the_sweep_keys_it(T._venue() + self.SEGS)
        for line in ("27.5", "13.5"):
            slug, title = T.HIS_ML + f"-total-{line.replace('.', 'pt')}", f"Patriots vs. Seahawks: O/U {line}"
            for outcome in ("Over", "Under"):
                hit = _resolve(rows, slug, title, outcome)
                assert hit is None, (identity, line, outcome, hit)
                ex = _explain(rows, slug, title, outcome)
                assert ex["step"] in ("no_side_match", "unknown_market_type"), (identity, line, ex)
                assert "-1h-" not in str(ex.get("matched_by") or "") and "-1q-" not in str(ex.get("matched_by") or "")
        # the same for his first-half slug against the venue's 1h token:
        # the segment words differ and nothing binds across them (C3's rule)
        assert _resolve(rows, T.HIS_ML + "-first-half-total-27pt5", "Patriots vs. Seahawks: 1st Half O/U 27.5", "Over") is None

    @pytest.mark.parametrize("identity", ["", "on"])
    def test_the_full_game_row_binds_beside_its_half_and_quarter_siblings(self, monkeypatch, identity):
        if identity:
            monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, identity)
        rows = _board_as_the_sweep_keys_it(T._venue() + self.SEGS)
        hit = _resolve(rows, T.HIS_ML + "-total-41pt5", "Patriots vs. Seahawks: O/U 41.5", "Over")
        assert T._short(hit) == (f"tsc-nfl-ne-sea-{T.VENUE_DATE}-total-41pt5", "over", T.LONG, "premap"), (identity, hit)
        # the moneyline beside the venue's winner-1h / htft / astatc rows: the aec row alone
        extra = [
            {"slug": "atc-nfl-ne-sea-2026-09-09-winner-1h-ne", "question": "Will New England Patriots win the first half?",
             "closed": False, "marketSides": [T._side("atc-nfl-ne-sea-2026-09-09-winner-1h-ne", "Yes", True),
                                              T._side("atc-nfl-ne-sea-2026-09-09-winner-1h-ne", "No", False)]},
            {"slug": "astatc-nfl-ne-sea-2026-09-09-2pc-0pt5", "question": "1st Field Goal Scoring Team: New England Patriots",
             "closed": False, "marketSides": [T._side("astatc-nfl-ne-sea-2026-09-09-2pc-0pt5", "Patriots", True),
                                              T._side("astatc-nfl-ne-sea-2026-09-09-2pc-0pt5", "Seahawks", False)]},
        ]
        rows = _board_as_the_sweep_keys_it(T._venue() + self.SEGS + extra)
        assert T._short(_resolve(rows, T.HIS_ML, T.HIS_TITLE, "Patriots")) == (T.AEC, "patriots", T.LONG, "premap")
        assert T._short(_resolve(rows, T.HIS_ML, T.HIS_TITLE, "Seahawks")) == (T.AEC, "seahawks", T.SHORT, "premap")

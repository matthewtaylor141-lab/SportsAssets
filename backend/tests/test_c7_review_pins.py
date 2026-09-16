"""M6 review pins (2026-09-07): the mutants the builder's C7 tests did not
kill, and the findings of hard2/M6_review.md. Fixtures are
test_c7_kickoff's verbatim rows (the PREMAP-TEAM read); nothing here
reaches the venue or the CLOB (conftest refuses it).

The three H1 / H2 / H3 tests were strict xfails stating the behaviour
the mandate wants; the fixes landed with the rebase onto the landing
stack (hard2/M6_on_stack_notes.md) and they are pins now."""
from __future__ import annotations

import asyncio

import pytest

from sportsassets.workers import edge_marks, premap
from tests.test_c7_kickoff import (ACV, AMBIG, D, D5, FEED, GIL, GIL_ACV_AT, LONG, NO_EVENT, P4, UNWIT, V_ACV,
                                   V_GA_DRAW, V_GA_TOTAL, V_GIL, VENUE, _at, _board, _explain, _game, _ml, _pick, _Pool,
                                   _resolve, _team, _total, _with)

TWIN = {f"ligpor-acv-bra-{D5}": ("Academico de Viseu FC vs. SC Braga", _game(
    "ligpor", "Liga Portugal", D5, "Sep 5, 2026", ACV, _team("bra", "SC Braga", "ligpor"), GIL_ACV_AT))}


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


# ------------------------------------------------ mutant killers (survivors of hard2/m6_mutants.py)

def test_two_records_of_one_event_naming_one_club_map_nothing(armed):
    """M03: the -gil row's record (abbr gil) and question both name
    Academico: the venue's two records name ONE club -- no exclusion can
    bind what a name did not separate. Nothing maps, the -acv row
    included (its own record is fine, the event is not)."""
    q = P4.format(a="Academico de Viseu FC", b="Gil Vicente Barcelos", lg="Liga Portugal", d="Sep 5, 2026")
    bad = {f"ligpor-gil-acv-{D5}": ("Academico de Viseu FC vs. Academico de Viseu FC", [
        _ml(f"atc-ligpor-gil-acv-{D5}-gil", q, GIL_ACV_AT, dict(GIL, name="Academico de Viseu FC")),
        VENUE[f"ligpor-gil-acv-{D5}"][1][1], VENUE[f"ligpor-gil-acv-{D5}"][1][2]])}
    rows = _board({k: v for k, v in VENUE.items() if "gil-acv" not in k}, extra=bad)
    for key in ("gil", "acv", "ga-draw"):
        assert _resolve(rows, key, "Yes") is None, key
        assert _resolve(rows, key, "No") is None, key
    _out, tr = _pick(rows, "acv", "Yes")
    assert (tr["refusal"], tr["why"]) == (UNWIT, "names")


def test_the_draw_row_at_another_instant_refuses(armed):
    """M19: the event's per-team rows at 19:30Z certify the stem, but
    its -draw row states 19:31Z -- the venue contradicts itself on the
    draw: kick:no-event, never the row."""
    rows = _with(_board(), V_GA_DRAW, game_start=_at("2026-09-06T19:31:00Z"))
    for oc in ("Yes", "No"):
        assert _resolve(rows, "ga-draw", oc) is None, oc
    ex = _explain(rows, "ga-draw", "Yes")
    assert ex["split"] == NO_EVENT and ex["yn_c7"]["arm"]["why"] == "draw-row"
    rows = _with(_board(), V_GA_DRAW, game_start=None)
    assert _resolve(rows, "ga-draw", "Yes") is None


def test_team_name_is_the_records_name_never_its_safename_or_alias():
    """M22 (and the M5 merge: M5 stores safeName in its own column): a
    record whose safeName / alias differ from name stores `name`,
    folded, and nothing else."""
    rec = dict(GIL, safeName="Gil Vicente", alias="Gilistas")
    m = _ml(f"atc-ligpor-gil-acv-{D5}-gil", VENUE[f"ligpor-gil-acv-{D5}"][1][0]["question"], GIL_ACV_AT, rec)
    rows = premap._market_rows({"slug": f"ligpor-gil-acv-{D5}", "title": ""}, m)
    assert [r["team_name"] for r in rows] == ["gil vicente barcelos", "gil vicente barcelos"]
    # a record with a name but an empty safeName / alias stores the name
    rec = dict(GIL, safeName="", alias="")
    m = _ml(f"atc-ligpor-gil-acv-{D5}-gil", VENUE[f"ligpor-gil-acv-{D5}"][1][0]["question"], GIL_ACV_AT, rec)
    assert premap._market_rows({"slug": "", "title": ""}, m)[0]["team_name"] == "gil vicente barcelos"
    # a record without a name stores nothing, whatever safeName says
    rec = dict(GIL, name="", safeName="Gil Vicente Barcelos")
    m = _ml(f"atc-ligpor-gil-acv-{D5}-gil", VENUE[f"ligpor-gil-acv-{D5}"][1][0]["question"], GIL_ACV_AT, rec)
    assert premap._market_rows({"slug": "", "title": ""}, m)[0]["team_name"] is None


def test_a_family_never_rides_an_ambiguous_instant(armed):
    """M26: two events at 19:30Z name Academico (a twin); the per-team
    lane refuses kick:ambiguous and so does every family ride -- the
    spread, the btts -- by the same name. Never the first stem."""
    rows = _board(extra=TWIN)
    assert _resolve(rows, "gil", "Yes") is None
    assert _explain(rows, "gil", "Yes")["split"] == AMBIG
    assert _resolve(rows, "ga-spread", "Académico de Viseu FC") is None
    ex = _explain(rows, "ga-spread", "Académico de Viseu FC")
    assert ex["split"] == AMBIG and ex["yn_c7"]["c3"]["refusal"] == AMBIG
    assert _resolve(rows, "ga-btts", "Yes") is None
    assert _resolve(rows, "ga-draw", "Yes") is None


def test_a_prefix_club_at_the_instant_is_no_witness(armed):
    """'Sporting CP' is not 'Sporting Braga' (token sets differ); with
    the other club not identical either ('Casa Pia AC' / 'Casa Pia
    Lisbon') nothing of his is witnessed at the instant."""
    spv = {f"ligpor-spb-cas-{D5}": ("Sporting Braga vs. Casa Pia Lisbon", _game(
        "ligpor", "Liga Portugal", D5, "Sep 5, 2026", _team("spb", "Sporting Braga", "ligpor"),
        _team("cas", "Casa Pia Lisbon", "ligpor"), GIL_ACV_AT))}
    rows = _board({}, extra=spv)
    tr: dict = {}
    out = premap._yn_pick(rows, "Yes", "Will Sporting CP win on 2026-09-06?", f"por-scp-cas-{D}-scp",
                          "Sporting CP vs. Casa Pia AC", tr, kick=_at(GIL_ACV_AT))
    assert out == [] and tr["refusal"] == UNWIT
    assert tr["venue_names"] == {f"ligpor-spb-cas-{D5}": ["sporting braga", "casa pia lisbon"]}


# ------------------------------------------------ findings (hard2/M6_review.md): strict xfail until fixed

def test_h1_a_total_under_an_ambiguous_instant_refuses(armed):
    """Two events at 19:30Z name Academico; the twin lists no 2.5 total.
    The kickoff key fetched gil-acv's tsc row and the WORDING arm mapped
    it with no certification of the stem -- while the per-team lane says
    kick:ambiguous. A total must answer to the same certification."""
    rows = _board(extra=TWIN)
    assert _explain(rows, "gil", "Yes")["split"] == AMBIG
    assert _resolve(rows, "ga-total", "Over") is None
    assert _resolve(rows, "ga-total", "Under") is None


def test_h2_a_total_row_at_another_instant_refuses(armed):
    """The tsc row carries the event's kickoff keys (written from the
    per-team records' instant) but states 19:31Z / the next day / no
    instant itself; the spread refuses (kick_c3_resolve), the total
    does not (the wording arm never reads game_start)."""
    for gs in (_at("2026-09-06T19:31:00Z"), _at("2026-09-07T19:30:00Z")):
        rows = _with(_board(), V_GA_TOTAL, game_start=gs)
        assert _resolve(rows, "ga-total", "Over") is None, gs
        assert _explain(rows, "ga-total", "Over")["split"] == NO_EVENT, gs


def test_h3_the_live_path_never_reads_the_clob(monkeypatch, armed):
    """With no market_starts row, premap.resolve (the copy lane, the
    live tick under _TICK_LOCK) must read market_starts only and refuse
    kick:unknown -- never a venue GET inside resolve."""
    calls: list[str] = []

    def _record(cid):
        calls.append(cid)
        return _at(GIL_ACV_AT).timestamp()

    monkeypatch.setattr(edge_marks, "fetch_game_start", _record)
    rows = _board()
    pool = _Pool(rows, starts={})
    h = _resolve(rows, "gil", "Yes", pool=pool)
    assert calls == [] and pool.writes == []
    assert h is None
    # a non-soccer slug of the same shape asks nothing either
    pool = _Pool(rows, starts={})
    asyncio.run(premap.resolve(pool, "Will Los Angeles Lakers win on 2026-09-06?",
                               "Los Angeles Lakers vs. Boston Celtics", "Yes",
                               "nba-lal-bos-2026-09-06-lal", condition_id="0xnba"))
    assert calls == []


# ------------------------------------------------ what still holds around the findings

def test_the_per_team_the_draw_and_the_spread_refuse_another_instant_today(armed):
    """The rows the arm DOES read the instant of: the -gil row at 19:31Z
    against the -acv row at 19:30Z (the venue contradicts itself), the
    spread row at 19:31Z -- all refuse today. The pin that bounds H2."""
    rows = _with(_board(), V_GIL, game_start=_at("2026-09-06T19:31:00Z"))
    assert _resolve(rows, "gil", "Yes") is None and _explain(rows, "gil", "Yes")["split"] == NO_EVENT
    assert _resolve(rows, "acv", "Yes") is None
    rows = _with(_board(), f"asc-ligpor-gil-acv-{D5}-neg-1pt5", game_start=_at("2026-09-06T19:31:00Z"))
    assert _resolve(rows, "ga-spread", "Académico de Viseu FC") is None
    # and the -acv row's own record still maps when everything agrees
    h = _resolve(_board(), "acv", "Yes")
    assert (h["market_slug"], h["intent"], h["matched_by"]) == (V_ACV, LONG, "premap_kickoff")


def test_a_failed_clob_read_is_kick_unknown_and_retried_on_the_jobs_clock(armed):
    """What the live path gets when the venue could not be read: nothing
    mapped, kick:unknown, and the failure cached (no second GET inside
    START_RETRY_S)."""
    rows = _board()
    pool = _Pool(rows, starts={"0xgilacv": "err"})
    assert _resolve(rows, "gil", "Yes", pool=pool) is None
    assert pool.reads == ["market_starts", "us_premap"] and pool.writes == []
    ex = _explain(rows, "gil", "Yes", pool=_Pool(rows, starts={"0xgilacv": "err"}))
    assert ex["split"] == "kick:unknown" and ex["yn_c7"]["why"] == "unread"


# ------------------------------------------------ v2 re-check (hard2/M6_review_v2.md): survivors of m6_mutants_v2.py

def test_the_wording_guard_refuses_a_foreign_stems_row_when_another_stem_is_certified(armed):
    """N07 (the guard's stem check): the venue lists gil-acv with NO total
    and, at the same instant, bra-fam -- naming neither of his clubs --
    with a 2.5 total. With every row answering the fetch the wording arm
    alone picks bra-fam's tsc row; C7 certifies gil-acv from his clubs,
    the hit's stem is another event: kick:no-event why=stem, never the
    row."""
    other = {f"ligpor-bra-fam-{D5}": ("SC Braga vs. FC Famalicao", _game(
        "ligpor", "Liga Portugal", D5, "Sep 5, 2026", _team("bra", "SC Braga", "ligpor"),
        _team("fam", "FC Famalicao", "ligpor"), GIL_ACV_AT) + [
        _total(f"tsc-ligpor-bra-fam-{D5}-2pt5", "Will the total in BRA vs FAM be more than 2.5?", GIL_ACV_AT, "2.5")])}
    venue = {k: (t, [m for m in ms if not m["slug"].startswith("tsc-")]) for k, (t, ms) in VENUE.items()}
    rows = _board(venue, extra=other)
    for oc in ("Over", "Under"):
        assert _resolve(rows, "ga-total", oc, pool=_Pool(rows, all_rows=True)) is None, oc
    ex = _explain(rows, "ga-total", "Over", pool=_Pool(rows, all_rows=True))
    w = ex["yn_c7"]["wording"]
    assert ex["split"] == NO_EVENT
    assert (w["refusal"], w["why"], w["event"], w["admitted"], w["foreign_stem"]) == (
        NO_EVENT, "stem", f"ligpor-gil-acv-{D5}", f"tsc-ligpor-bra-fam-{D5}-2pt5", f"ligpor-bra-fam-{D5}")
    # the guard itself, on that hit: the stem refusal by name
    hit = next(r for r in rows if r["identifier"] == f"tsc-ligpor-bra-fam-{D5}-2pt5" and r["side_norm"].startswith("over"))
    tr: dict = {}
    assert premap._c7_wording_guard(rows, hit, _at(GIL_ACV_AT), FEED["ga-total"][1], FEED["ga-total"][2],
                                    FEED["ga-total"][0], tr) == NO_EVENT
    assert (tr["refusal"], tr["why"], tr["event"]) == (NO_EVENT, "stem", f"ligpor-gil-acv-{D5}")


def test_the_read_only_branch_names_a_failed_read_unread(armed):
    """N08: market_starts holds an err row (the job's failed venue read)
    or no row: the live path's read-only _his_kick answers (None,
    'unread') -- never 'no-game-start' (that is a record STATING no
    start), never a guess -- and the census (fetch_kick=False) reads
    kick:unknown / unread."""
    rows = _board()
    slug = FEED["gil"][0]
    assert asyncio.run(premap._his_kick(_Pool(rows, starts={"0xgilacv": "err"}), "0xgilacv", slug, fetch=False)) == (None, "unread")
    assert asyncio.run(premap._his_kick(_Pool(rows, starts={}), "0xgilacv", slug, fetch=False)) == (None, "unread")
    assert asyncio.run(premap._his_kick(_Pool(rows, starts={"0xgilacv": None}), "0xgilacv", slug, fetch=False)) == (None, "no-game-start")
    ex = _explain(rows, "gil", "Yes", pool=_Pool(rows, starts={"0xgilacv": "err"}), fetch=False)
    assert ex["split"] == "kick:unknown" and ex["yn_c7"]["why"] == "unread"
    assert _resolve(rows, "gil", "Yes", pool=_Pool(rows, starts={"0xgilacv": "err"})) is None


def test_the_exclusion_vetos_furniture_is_the_pinned_ten_plus_sd_ud():
    """The veto sets furniture aside before asking for a shared token:
    MORE furniture means more refusals (a lowering), an emptied set would
    bind 'Porto FC' to 'Boavista FC' on 'fc'. Exactly the pinned ten plus
    the club-type letters 'sd' / 'ud'; never a reserve marker."""
    assert premap._C7_FURNITURE == premap.GENERIC_CLUB_TOKENS | {"sd", "ud"}
    assert premap._C7_FURNITURE == frozenset({"fc", "cf", "sc", "ac", "afc", "ca", "cd", "club", "the", "de", "sd", "ud"})


def test_m1_two_clubs_sharing_a_name_token_never_bind_by_exclusion(armed):
    """His 'Sporting CP vs. SC Farense' against the venue's 'Sporting
    Braga vs. SC Farense' at the instant: Farense identical, the other
    club a DIFFERENT club of the same league sharing one name token. A
    contradiction, not a spelling: nothing binds. (A rule that still
    binds every spelling the brief mandates: one side's distinctive
    token set a subset of the other's -- {gil, vicente} in {gil, vicente,
    barcelos}, {santa, clara} in {santa, clara, azores}, {vitoria} in
    {vitoria, sport, clube}; {sporting, cp} is in neither direction of
    {sporting, braga}.)"""
    spv = {f"ligpor-spb-far-{D5}": ("Sporting Braga vs. SC Farense", _game(
        "ligpor", "Liga Portugal", D5, "Sep 5, 2026", _team("spb", "Sporting Braga", "ligpor"),
        _team("far", "SC Farense", "ligpor"), GIL_ACV_AT))}
    rows = _board({}, extra=spv)
    tr: dict = {}
    out = premap._yn_pick(rows, "Yes", "Will Sporting CP win on 2026-09-06?", f"por-scp-far-{D}-scp",
                          "Sporting CP vs. SC Farense", tr, kick=_at(GIL_ACV_AT))
    assert out == [] and (tr["refusal"], tr["why"]) == (UNWIT, "names")

"""RC6 LANE D2 (review finding 1): A KALSHI SERIES IS EXCLUDED FROM THE
TARGET UNIVERSE ONLY WHEN A RULE NAMES ITS CLASS.

The waterfall's Kalshi tier was read off the series ticker's suffix alone:
GAME / SPREAD / TOTAL / TEAMTOTAL were target families and EVERY other
series was EXCLUDED as KALSHI_SERIES_NOT_A_GAME_FAMILY. That label is false
for the venue's match, fight and doubles winners and for every period, map
and set winner (research-sql 37869672219 K4 / K6, the series' own rules:
KXATPMATCH "If Adolfo Daniel Vallejo wins the Paul vs Vallejo professional
tennis match", KXUFCFIGHT "wins the Pereira vs Zheleznyakova professional
MMA fight", KXNCAAF1H "College Football 1st Half Winner", 657 active rows,
KXCS2MAP "Counter-Strike 2 Map Winner", KXATPSETWINNER "ATP Set Winner"), so
a few thousand target contracts left the proposed universe with a false
named reason, and an unrecognised series was treated as excluded.

Now: market_plane.waterfall.kalshi_series_class reads the ticker AND the
venue's series title; the target families are recognised by their own
suffixes (match / fight / doubles / game winners full; half, quarter,
period, inning, map and set winners as TARGET_B); a series is EXCLUDED only
when a rule names its class (AWARD, POLL_RANKING, PLAYER_PROP, OFF_FIELD,
OTHER_PROP -- correct score among them --, OUTRIGHT -- season wins and
tournament winners among them --, MATCHUP_CONFIRMATION, MVE_PARLAY); every
other series is UNCLASSIFIED and named in the waterfall.

Every case below is a production series with its production title
(tests/fixtures/kalshi_series_2026_10_09.json: the 1,351 series of the
80,659 active Kalshi registry rows, 2026-10-09 ~01:30Z, with one
rules_primary text each), except the one MVE ticker, which the active
catalogue did not list that day and which is marked so.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from sportsassets.market_plane import waterfall as WF

HERE = pathlib.Path(__file__).resolve().parent
CAT = json.loads((HERE / "fixtures" / "kalshi_series_2026_10_09.json")
                 .read_text())
TITLE = {s: t for s, t, _n, _r in CAT["series"]}


def _k(series, title=None, *, ontology=True):
    c = {"venue": "KALSHI", "competition": series, "market_type": "binary"}
    if ontology:
        t = TITLE.get(series) if title is None else title
        c["ontology"] = {"series": {"title": t}}
    k = WF.classify(c)
    return k["tier"], k["family"], k["segment"]


# ── 1. the target families the suffix rule left out ─────────────────────

@pytest.mark.parametrize("series", (
    "KXATPMATCH", "KXUFCFIGHT", "KXITFMATCH", "KXITFWMATCH", "KXDARTSMATCH",
    "KXT20MATCH", "KXWT20MATCH", "KXRUGBYFRA14MATCH", "KXATPCHALLENGERMATCH",
    "KXWTACHALLENGERMATCH", "KXATPDOUBLES", "KXITFDOUBLES",
    "KXNCAAWVMATCH", "KXODIMATCH", "KXPOWERSLAPMATCH", "KXBOXING",
    "KXNFLTIE"))
def test_a_match_fight_or_game_winner_is_target_a(series):
    """Before: EXCLUDED / KALSHI_SERIES_NOT_A_GAME_FAMILY."""
    assert series in TITLE
    assert _k(series) == ("TARGET_A", "MONEYLINE", "FULL")


@pytest.mark.parametrize("series", (
    "KXNCAAF1H", "KXNCAAF2H", "KXNCAAF1Q", "KXNCAAF2Q", "KXNCAAF3Q",
    "KXNCAAF4Q", "KXNFL1H", "KXNFL2H", "KXNFL1Q", "KXNFL2Q", "KXNFL3Q",
    "KXNFL4Q", "KXNHL1P", "KXNHL2P", "KXNHL3P", "KXNBA1H", "KXMLS1H",
    "KXMLS2H", "KXBUNDESLIGA1H", "KXBUNDESLIGA2H", "KXBRASILEIRO1H",
    "KXWNBA1QWINNER", "KXWNBA2HWINNER", "KXMLBF5", "KXMLBINNINGWIN",
    "KXCS2MAP", "KXLOLMAP", "KXR6MAP", "KXVALORANTMAP", "KXDOTA2MAP",
    "KXATPSETWINNER", "KXWTASETWINNER"))
def test_a_period_map_or_set_winner_is_target_b(series):
    """Before: EXCLUDED, and KXCS2MAP / KXATPSETWINNER segment FULL."""
    assert series in TITLE
    assert _k(series) == ("TARGET_B", "MONEYLINE", "PERIOD")


@pytest.mark.parametrize("series,want", (
    ("KXNCAAFGAME", ("TARGET_A", "MONEYLINE", "FULL")),
    ("KXNFLSPREAD", ("TARGET_A", "SPREAD", "FULL")),
    ("KXATPGSPREAD", ("TARGET_A", "SPREAD", "FULL")),
    ("KXNCAAFTOTAL", ("TARGET_A", "TOTAL", "FULL")),
    ("KXCS2TOTALMAPS", ("TARGET_A", "TOTAL", "FULL")),
    ("KXUFCROUNDS", ("TARGET_A", "TOTAL", "FULL")),
    ("KXNFLTEAMTOTAL", ("TARGET_A", "TEAM_TOTAL", "FULL")),
    ("KXNCAAF1HSPREAD", ("TARGET_B", "SPREAD", "PERIOD")),
    ("KXMLBF5SPREAD", ("TARGET_B", "SPREAD", "PERIOD")),
    ("KXNFL4QTOTAL", ("TARGET_B", "TOTAL", "PERIOD")),
    ("KXMLBINNINGTOTAL", ("TARGET_B", "TOTAL", "PERIOD")),
    ("KXKBORFI", ("TARGET_B", "TOTAL", "PERIOD")),
    ("KXNFL1HTEAMTOTAL", ("TARGET_B", "TEAM_TOTAL", "PERIOD"))))
def test_the_line_families_keep_their_tier(series, want):
    assert _k(series) == want


# ── 2. an exclusion names its class ──────────────────────────────────────

@pytest.mark.parametrize("series,family", (
    ("KXNCAAFAWARD", "AWARD"), ("KXNBAMVP", "AWARD"),
    ("KXNHLLINDSAY", "AWARD"), ("KXNCAAFTOPAPRANK", "POLL_RANKING"),
    ("KXNCAAMBKENPOMTOP", "POLL_RANKING"), ("KXNFLRECYDS", "PLAYER_PROP"),
    ("KXNBASTATLEADER", "PLAYER_PROP"), ("KXNFLFFSEASONTOTAL", "PLAYER_PROP"),
    ("KXNFLRECYDSH2H", "PLAYER_PROP"), ("KXNEXTTEAMNBA", "OFF_FIELD"),
    ("KXCOACHOUTMLB", "OFF_FIELD"), ("KXMMACOMPETE", "OFF_FIELD"),
    ("KXFLOYDTYSONFIGHT", "OFF_FIELD"), ("KXMLSSCORE", "OTHER_PROP"),
    ("KXEPL1HSCORE", "OTHER_PROP"), ("KXATPEXACTMATCH", "OTHER_PROP"),
    ("KXNFL1HFT", "OTHER_PROP"), ("KXNFLWINMARGIN", "OTHER_PROP"),
    ("KXUFCMOV", "OTHER_PROP"), ("KXNFLWINS", "OUTRIGHT"),
    ("KXNCAAFWINS", "OUTRIGHT"), ("KXATP", "OUTRIGHT"),
    ("KXSERIEATEAMPOINTS", "OUTRIGHT"), ("KXNBAPOSTASREC", "OUTRIGHT"),
    ("KXNFLPLAYOFFHOST", "OUTRIGHT"), ("KXNFLBLOWOUT", "OUTRIGHT"),
    ("KXNCAAFBOWLGAME", "OUTRIGHT"), ("KXMLBSERIESSPREAD", "OUTRIGHT"),
    ("KXNFLMATCHUP", "MATCHUP_CONFIRMATION"),
    ("KXTEAMSINSC", "MATCHUP_CONFIRMATION")))
def test_an_excluded_series_names_its_class(series, family):
    """Before: every one of these was EXCLUDED under the one false label."""
    assert series in TITLE
    assert _k(series) == ("EXCLUDED", family, "FULL")


def test_an_mve_parlay_is_excluded_by_name():
    """The 2026-10-09 active catalogue lists no MVE series (none in the
    fixture); the class is the venue's multivariate combination ticker."""
    assert not [s for s in TITLE if s.startswith("KXMVE")]
    assert _k("KXMVESPORTSMULTIGAMEEXTENDED", "") == \
        ("EXCLUDED", "MVE_PARLAY", "FULL")


@pytest.mark.parametrize("series,title", (
    ("KXPGA3BALL", None), ("KXF1H2H", None), ("KXEPLH2H", None),
    ("KXSERIEA", None), ("KXFIFAWADVANCE", None), ("KXARGPREMDIV", None),
    ("KXSOMETHINGNEW", "Something New"), ("KXSOMETHINGNEW", "")))
def test_an_unrecognised_series_is_unclassified_never_excluded(series,
                                                               title):
    assert _k(series, title) == ("UNCLASSIFIED",
                                 "KALSHI_SERIES_NOT_RECOGNISED", "FULL")


def test_the_title_is_the_registry_rows_own():
    """ontology.series.title, as the registry stores it (a dict, or the
    jsonb text the pool returns), or absent; the period winner needs it."""
    t = TITLE["KXNHL1P"]
    for ont in ({"series": {"title": t}}, json.dumps({"series":
                                                      {"title": t}})):
        k = WF.classify({"venue": "KALSHI", "competition": "KXNHL1P",
                         "ontology": ont})
        assert (k["tier"], k["family"], k["rule"]) == \
            ("TARGET_B", "MONEYLINE", "HOCKEY_PERIOD_WINNER")
    # without the title the venue's "Period" is not read: not guessed
    k = WF.classify({"venue": "KALSHI", "competition": "KXNHL1P"})
    assert k["tier"] == "UNCLASSIFIED"
    assert k["basis"] == WF.BASIS_KALSHI


# ── 3. the whole active catalogue, against the venue's own rules text ────

#: a rules text that settles on ONE game's, match's, fight's, period's,
#: map's or set's winner (two named sides, or a numbered segment)
ONE_WINNER = re.compile(
    r"^If .+? (?:wins? the|is the winner (?:in|of) the) [^,]*?\bvs\.? "
    r"[^,]*?\b(?:game|match|fight|bout)\b(?! between)"
    r"|\bwins the (?:1st|2nd|3rd|4th) (?:half|quarter|period)"
    r"(?: \([^)]*\))? of the\b"
    r"|\bis the winner of the (?:first|second|1st|2nd|3rd|4th) "
    r"(?:half|quarter)\b"
    r"|\bwins (?:map|set) \d+ (?:in|of) the\b"
    r"|\bwins the first \d+ innings of the\b"
    r"|\bscores more runs in the \d+(?:st|nd|rd|th) inning of the\b")
#: ...combined with another outcome: an exact set score, half-time /
#: full-time
COMPOUND = re.compile(r"\bby a set score\b|\band .+? (?:wins|tie) "
                      r"(?:in )?the full game\b")
#: the result's draw / tie leg
DRAW = re.compile(r"\bneither team wins\b|^If Tie is the result\b|"
                  r"^If tie is the result\b|\b(?:draw or )?tie in the\b")


def test_no_series_whose_rules_settle_on_one_winner_is_left_out():
    """Over all 1,351 active series: no EXCLUDED or UNCLASSIFIED series has
    a rules text that settles on one event's winner, and every MONEYLINE
    series' text names one (or the result's draw leg). Contract level, the
    same holds for all 80,659 active rows' texts (scratch run 2026-10-09
    over the research corpus; the HT/FT doubles are the only matches, and
    they are COMPOUND)."""
    left_out, unproven = [], []
    for series, title, _n, text in CAT["series"]:
        k = WF.classify({"venue": "KALSHI", "competition": series,
                         "ontology": {"series": {"title": title}}})
        one = bool(ONE_WINNER.search(text)) and not COMPOUND.search(text)
        if k["tier"] in ("EXCLUDED", "UNCLASSIFIED") and one:
            left_out.append(series)
        if k["family"] == "MONEYLINE" and not (one or DRAW.search(text)):
            unproven.append(series)
    assert left_out == [] and unproven == []


def test_the_corrected_kalshi_tiers_over_the_active_catalogue():
    """The counts the lane reports (contract level, 2026-10-09 ~01:30Z):
    before, the suffix rule gave TARGET_A 16,381 / TARGET_B 9,729 /
    EXCLUDED 54,549 (research-sql 37871119335 W0)."""
    tiers, fams = {}, {}
    for series, title, n, _t in CAT["series"]:
        k = WF.classify({"venue": "KALSHI", "competition": series,
                         "ontology": {"series": {"title": title}}})
        tiers[k["tier"]] = tiers.get(k["tier"], 0) + n
        key = (k["tier"], k["family"], k["segment"])
        fams[key] = fams.get(key, 0) + n
    assert CAT["source"]["rows_total"] == sum(tiers.values()) == 80_659
    assert tiers == {"TARGET_A": 17_298, "TARGET_B": 12_369,
                     "EXCLUDED": 50_457, "UNCLASSIFIED": 535}
    assert fams[("TARGET_A", "MONEYLINE", "FULL")] == 5_698
    assert fams[("TARGET_B", "MONEYLINE", "PERIOD")] == 2_610
    assert fams[("UNCLASSIFIED", "KALSHI_SERIES_NOT_RECOGNISED",
                 "FULL")] == 535
    assert "KALSHI_SERIES_NOT_A_GAME_FAMILY" not in {f for _t, f, _s in fams}


def test_the_waterfall_names_every_unrecognised_series():
    w = WF.Waterfall(now=1_800_000_000.0)
    lost = {"state": "CODE_CONTROLLED_GAP",
            "why": "ONTOLOGY_GAPS:KALSHI_ONTOLOGY_NOT_MAPPED",
            "evidence": {"mapped": False}}
    for series in ("KXPGA3BALL", "KXPGA3BALL", "KXF1H2H", "KXATPMATCH",
                   "KXNCAAFAWARD"):
        w.add({"venue": "KALSHI", "competition": series,
               "ontology": {"series": {"title": TITLE[series]}}}, lost)
    out = w.result()
    assert out["sums_exact"] is True
    assert out["kalshi_unrecognised"] == {"KXPGA3BALL": 2, "KXF1H2H": 1}
    assert out["kalshi_unrecognised_keys_total"] == 2
    assert out["by_kalshi_rule"] == {"AWARD": 1, "GAME_WINNER": 1,
                                     WF.K_NOT_RECOGNISED: 3}
    assert out["tiers"]["UNCLASSIFIED"]["catalogue"] == 3
    assert out["excluded"] == {"KALSHI|AWARD|FULL": 1}
    assert out["by_venue_tier"]["KALSHI|TARGET_A"][WF.LOST_MAPPING] == 1

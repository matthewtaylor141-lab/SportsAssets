"""M1 review pins (2026-09-07): the rules M1.patch states that its own
tests did not hold (mutants that survived), and the one witness the
event-title head-strip opened.

  A. THE EVENT-TITLE HEAD IS NOT A SCOPE STRIP. _event_title_head drops
     ANY letters-only trailing segment, and _yn_event_sides reads the
     head -- so 'Arsenal FC vs. Chelsea FC - Women' / '- Reserves' /
     '- Aggregate' witnessed the MEN's full-game rows for the alias arm
     (wsl->epl premap_alias) and for C5's event witness, where before
     M1 the scope token in the second side refused (None). The class
     the bridge's scope list exists for ('SC Braga vs Austin FC
     (Aggregate)', the executed round-2.1 kill), reachable through a
     hyphen instead of a parenthesis. Only the feed's attested family
     segments ('Exact Score', 'Halftime Result', 'More Markets') are a
     head; anything else witnesses nothing. These three tests FAIL on
     M1.patch as delivered and pass with the closed-list fix named in
     hard2/M1_review.md.
  B. THE RAW RULE AT RESOLVE LEVEL (mutants M1/M4/M13/M14 survived): a
     venue subject that restates the NUMBER but drops a generic token
     ('Bologna 1909', 'Basel 1893') passes _yn_name_match's distinctive
     branch and must refuse yn:name-digits on BOTH arms; a reordered
     restatement ('1909 Bologna FC') refuses by the ratio floor.
  C. spread:sides-unparsed needs EVERY row of the wanted identifier to
     lack a yes/no side (mutant M12): a listed 'yes' row with the wanted
     'no' missing stays spread:line-absent.
  D. the club-slot screen keeps the bridge's adjacent-run joins (M10).
  E. the digit-code rule reads a ONE-token tail only (M5b on both
     twins): 'cu1-abc', 'cu1-cu1' stay unknown.
"""
from __future__ import annotations

import pytest

from sportsassets import pmus
from sportsassets.copy_sports import market_type_of
from sportsassets.workers import premap
from tests import test_c5_code_translation as c5
from tests.test_m1_identity_fixes import (D, HIS_NUMBERED, NUMBERED, _Pool, _board, _explain,
                                          _game, _resolve, _short, _with_question)

LONG = "ORDER_INTENT_BUY_LONG"
MEN = _game("epl", "ars", "che", "Arsenal FC", "Chelsea FC", "Premier League", "Arsenal FC vs. Chelsea FC")
MEN_CFC = _game("epl", "ars", "cfc", "Arsenal FC", "Chelsea FC", "Premier League", "Arsenal FC vs. Chelsea FC")


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


# ------------------------------------------------ A. the head is not a scope strip

class TestTheEventTitleHeadIsNotAScopeStrip:
    @pytest.mark.parametrize("seg", ["Women", "Reserves", "Aggregate", "Ladies", "Primavera",
                                     "Extra Time", "First Half", "Penalties", "U21"])
    def test_a_scope_segment_on_his_event_title_witnesses_nothing(self, armed, seg):
        """The segment stays in the second side, where the scope screen
        ('women', 'reserves', 'aggregate' ...) or the opponent witness
        ('chelsea fc ladies' is not 'chelsea fc') refuses it -- never the
        clean pair the men's rows answer to."""
        ev = f"Arsenal FC vs. Chelsea FC - {seg}"
        assert premap._yn_event_sides(ev) != ["arsenal fc", "chelsea fc"], seg
        rows = _board(MEN)
        # the alias arm (his league code differs, codes equal) needs the event witness
        args = (f"wsl-ars-che-{D}-ars", "Will Arsenal FC win on 2026-09-06?", ev, "Yes")
        assert _resolve(_Pool(rows), *args) is None, seg
        ex = _explain(_Pool(rows), *args)
        assert ex["step"] != "resolves" and ex.get("matched_by") is None, (seg, ex["step"], ex.get("split"))
        # C5's event witness for the unshared code (che -> cfc) is the same reading
        args = (f"epl-ars-che-{D}-ars", "Will Arsenal FC win on 2026-09-06?", ev, "Yes")
        assert _resolve(_Pool(_board(MEN_CFC)), *args) is None, seg
        assert "certified" not in _explain(_Pool(_board(MEN_CFC)), *args).get("yn_c5", {})

    @pytest.mark.parametrize("seg", ["Exact Score", "Halftime Result", "More Markets"])
    def test_the_attested_family_segments_are_the_head(self, armed, seg):
        ev = f"Arsenal FC vs. Chelsea FC - {seg}"
        assert premap._yn_event_sides(ev) == ["arsenal fc", "chelsea fc"]
        h = _resolve(_Pool(_board(MEN)), f"wsl-ars-che-{D}-ars", "Will Arsenal FC win on 2026-09-06?", ev, "Yes")
        assert _short(h) == (f"atc-epl-ars-che-{D}-ars", "yes", LONG, "premap_alias", "wsl->epl")

    def test_a_scope_segment_never_keys_the_men_s_game(self):
        # the key is a lookup, but the same closed list keeps a women's
        # title from fetching the men's rows at all
        keys = premap.event_keys_for("Arsenal FC vs. Chelsea FC - Women", f"wsl-ars-che-{D}-ars")
        assert f"arsenal fc vs chelsea fc@{D}" not in keys
        assert f"arsenal fc vs chelsea fc women@{D}" in keys


# --------------------------------------------- B. the raw rule at resolve level

BOL = f"atc-sea-bol-sas-{D}-bol"
Q_BOL = "Will Bologna FC 1909 win against US Sassuolo Calcio in the Serie A match scheduled for Sep 6, 2026?"
FCB = f"atc-swsl-fcb-lug-{D}-fcb"
Q_FCB = "Will FC Basel 1893 win against FC Lugano in the Swiss Super League match scheduled for Sep 6, 2026?"


class TestTheRawRuleAtResolveLevel:
    @pytest.mark.parametrize("venue_name", ["Bologna 1909", "1909 Bologna FC"])
    def test_the_identity_arm_holds_the_number_and_every_other_token(self, armed, venue_name):
        # 'bologna 1909' passes _yn_name_match's distinctive branch (fc is
        # generic); '1909 bologna fc' is the same set below the ratio floor
        assert pmus._yn_name_match("bologna fc 1909", "bologna 1909") is True
        assert pmus._yn_name_match_raw("bologna fc 1909", pmus._norm(venue_name)) is False
        slug, title, ev = HIS_NUMBERED["bol"][:3]
        rows = _with_question(_board(NUMBERED), BOL, Q_BOL.replace("Bologna FC 1909", venue_name))
        assert _resolve(_Pool(rows), slug, title, ev, "Yes") is None
        ex = _explain(_Pool(rows), slug, title, ev, "Yes")
        assert ex["step"] == "no_side_match" and ex["split"] == "yn:name-digits"

    @pytest.mark.parametrize("venue_name", ["Basel 1893", "FC Basel", "1893 FC Basel"])
    def test_the_alias_arm_holds_the_same_rule(self, armed, venue_name):
        slug, title, ev = HIS_NUMBERED["fcb"][:3]
        rows = _with_question(_board(NUMBERED), FCB, Q_FCB.replace("FC Basel 1893", venue_name))
        assert _resolve(_Pool(rows), slug, title, ev, "Yes") is None
        ex = _explain(_Pool(rows), slug, title, ev, "Yes")
        assert ex["step"] == "no_side_match" and ex["split"] == "yn:name-digits"

    @pytest.mark.parametrize("venue_name", ["FC Basel 1893 Women", "FC Basel 1893 II", "FC Basel 1893 B",
                                            "FC Basel 1893 Frauen"])
    def test_a_scoped_twin_of_the_numbered_club_refuses_at_the_slot(self, armed, venue_name):
        slug, title, ev = HIS_NUMBERED["fcb"][:3]
        rows = _with_question(_board(NUMBERED), FCB, Q_FCB.replace("FC Basel 1893", venue_name))
        assert _resolve(_Pool(rows), slug, title, ev, "Yes") is None
        assert _explain(_Pool(rows), slug, title, ev, "Yes")["split"] == "yn:scope"

    def test_the_same_day_twin_under_two_venue_codes_is_ambiguous(self, armed):
        twin = {**NUMBERED, **_game("swslw", "fcb", "lug", "FC Basel 1893", "FC Lugano", "Swiss Super League",
                                    "FC Basel 1893 vs. FC Lugano")}
        slug, title, ev = HIS_NUMBERED["fcb"][:3]
        assert _resolve(_Pool(_board(twin)), slug, title, ev, "Yes") is None
        ex = _explain(_Pool(_board(twin)), slug, title, ev, "Yes")
        assert ex["split"] == "yn:league-ambiguous" and ex["yn_c2"]["league_codes"] == ["swsl", "swslw"]

    def test_the_raw_rule_keeps_its_floor(self):
        assert pmus._yn_name_match_raw("bologna fc 1909", "1909 bologna fc") is False
        assert pmus._yn_name_match_raw("bologna fc 1909", "bologna fc 1909") is True


# --------------------------------------------------- C. spread:sides-unparsed

class TestSidesUnparsedNeedsEveryRowUnparsed:
    def test_a_listed_yes_row_with_the_wanted_no_missing_is_line_absent(self, armed):
        slug, title, ev, oc = c5.FEED["spread"]
        rows = [r for r in c5._board()
                if not (r["identifier"].endswith("-pos-1pt5") and premap._norm(r.get("side_norm")) == "no")]
        assert any(r["identifier"].endswith("-pos-1pt5") for r in rows)
        ex = _explain(_Pool(rows), slug, title, ev, oc)
        assert ex["split"] == "spread:line-absent"
        assert _resolve(_Pool(rows), slug, title, ev, oc) is None


# ------------------------------------------------------- D. the slot joins

class TestTheClubSlotKeepsTheBridgeJoins:
    @pytest.mark.parametrize("slot", ["double header", "over all", "shoot out", "s o"])
    def test_an_adjacent_run_that_joins_to_a_bridge_token_refuses(self, slot):
        assert pmus._yn_slot_bad(slot) is True
        assert premap._has_scope_token(slot) is True


# ------------------------------------------------------ E. the one-token tail

class TestTheDigitCodeReadsAOneTokenTail:
    @pytest.mark.parametrize("slug", [f"chi1-cdp-cu1-{D}-cu1-abc", f"chi1-cdp-cu1-{D}-cu1-cu1",
                                      f"chi1-cdp-cu1-{D}-cu1-cdp", f"cs2-fnc-nip-{D}-map2"])
    def test_a_longer_tail_stays_unknown(self, slug):
        assert market_type_of(slug) == "unknown"

"""§18/§19. What management sees: the product, and never a zero."""

import pytest

from sportsassets import bettor_applicability as applic
from sportsassets import bettor_command_view as cv


def _panel(**over):
    kw = dict(inventory_state="FLAT", markets_observed=1676,
              readable_markets=748)
    kw.update(over)
    return cv.panel(**kw)


def _line(panel, name):
    return panel["byLine"][name]


# ── §18: the headline is the product, not the research ───────────────

def test_the_headline_is_the_bettor_ev_engine():
    p = _panel()
    assert p["headline"] == "BETTOR EV ENGINE"
    assert p["notTheHeadline"] == "X1"
    assert "makes the research look like the business" in \
        p["whyThisHeadline"]


def test_the_x_series_is_filed_under_research():
    assert _panel()["xSeriesBelongsUnder"] == "RESEARCH / EXPERIMENTS"


# ── §18: all thirty lines, in the directive's order ──────────────────

def test_every_declared_line_is_present_and_in_order():
    p = _panel()
    assert [r["line"] for r in p["lines"]] == list(cv.LINES)
    assert len(cv.LINES) == 30


@pytest.mark.parametrize("line", cv.LINES)
def test_a_line_with_no_value_does_not_disappear(line):
    """A row that vanishes when it has no value is one nobody notices
    is gone."""
    assert line in _panel()["byLine"]


# ── NOT IDENTIFIED, never 0 ──────────────────────────────────────────

def test_no_unidentified_line_renders_as_zero():
    for r in _panel()["lines"]:
        if r["status"] != "IDENTIFIED":
            assert r["display"] == "NOT IDENTIFIED"
            assert r["display"] != "0"
            assert r["value"] != "0"
            assert r["value"] != 0


def test_the_pnl_buckets_are_no_activity_not_zero_profit():
    r = _line(_panel(), "PAIR_PNL")
    assert r["display"] == "NOT IDENTIFIED"
    assert "not zero profit -- it is no activity" in r["why"]


def test_an_uncounted_opportunity_is_not_a_count_of_zero():
    r = _line(_panel(), "MAKER_OPPORTUNITIES")
    assert r["display"] == "NOT IDENTIFIED"
    assert "not zero -- nothing has been counted" in r["why"]


def test_every_unidentified_line_carries_the_reason_it_is_not_zero():
    for r in _panel()["lines"]:
        if r["status"] != "IDENTIFIED":
            assert r["neverZero"], r["line"]


# ── a status line reading NOT IDENTIFIED is an answer ────────────────

def test_a_status_line_is_answered_even_when_the_answer_is_unidentified():
    """P_FILL_STATUS reads NOT IDENTIFIED because that IS the finding.
    A quantity line reading the same means nobody measured it."""
    p = _panel()
    s = _line(p, "P_FILL_STATUS")
    q = _line(p, "CAPITAL_HOURS")
    assert s["kind"] == cv.STATUS and s["status"] == "IDENTIFIED"
    assert q["kind"] == cv.QUANTITY and q["status"] == cv.NOT_IDENTIFIED
    assert s["display"] == q["display"] == "NOT IDENTIFIED"
    assert "different facts" in s["whyKindMatters"]


def test_the_fair_value_gate_is_shown_and_not_tuned_away():
    r = _line(_panel(), "FAIR_VALUE_STATUS")
    assert r["value"] == "FV_BETTOR_INDEPENDENT_NOT_IDENTIFIED"
    assert r["status"] == "IDENTIFIED"


# ── applicability reaches the panel ──────────────────────────────────

def test_from_flat_the_inventory_ev_lines_are_not_applicable():
    """Not NOT_IDENTIFIED and certainly not 0: they cannot exist."""
    p = _panel(inventory_state="FLAT")
    for line in ("HOLD_EV", "DIRECT_EXIT_EV", "HEDGE_EV", "MERGE_EV"):
        r = _line(p, line)
        assert r["status"] == applic.NOT_APPLICABLE, line
        assert r["display"] == "NOT IDENTIFIED"
        assert r["whyNotZero"]


def test_with_a_residual_the_inventory_ev_lines_become_applicable():
    p = _panel(inventory_state="YES_RESIDUAL")
    r = _line(p, "DIRECT_EXIT_EV")
    assert r["status"] == cv.NOT_IDENTIFIED
    assert r["status"] != applic.NOT_APPLICABLE
    assert "economics are not identified" in r["why"]


def test_the_applicable_action_list_comes_from_the_state():
    flat = _line(_panel(inventory_state="FLAT"),
                 "CURRENT_APPLICABLE_ACTIONS")["value"]
    res = _line(_panel(inventory_state="YES_RESIDUAL"),
                "CURRENT_APPLICABLE_ACTIONS")["value"]
    assert "MAKE_YES" in flat
    assert "DIRECT_EXIT" not in flat
    assert "DIRECT_EXIT" in res


def test_an_unknown_state_is_not_shown_as_flat():
    r = _line(_panel(inventory_state=applic.STATE_NOT_IDENTIFIED),
              "CURRENT_INVENTORY_STATE")
    assert r["value"] == applic.STATE_NOT_IDENTIFIED
    assert r["value"] != "FLAT"


def test_nothing_is_ranked_while_nothing_is_priced():
    r = _line(_panel(), "CURRENT_ACTION_RANKING")
    assert r["status"] == cv.NOT_IDENTIFIED
    assert "nothing to rank" in r["why"]


def test_capital_turns_names_the_broken_recycling_step():
    r = _line(_panel(), "CAPITAL_TURNS")
    assert "MERGE_OR_NET" in r["why"]


# ── the panel assembles, it does not re-derive ───────────────────────

def test_every_line_names_the_module_that_owns_it():
    for r in _panel()["lines"]:
        assert r["source"] != cv.NOT_IDENTIFIED, r["line"]


def test_the_panel_says_it_re_derives_nothing():
    assert "the nicer one wins" in _panel()["assembledNotComputed"]


def test_the_pnl_buckets_are_never_blended():
    assert "different problems with different fixes" in \
        _panel()["pnlBucketsNeverBlended"]


def test_the_mandate_status_is_on_the_panel():
    m = _panel()["shadowMandate"]
    assert m["MANDATE_STATUS"] == "PROPOSED_AWAITING_EVIDENCE_AND_OWNER_APPROVAL"
    assert m["BETTOR_EV_SHADOW_POSITIONS"] == 0
    assert m["BETTOR_EV_REAL_ORDER_ACTIVITY"] == "NONE"
    assert m["BETTOR_EV_REAL_CAPITAL_AT_RISK"] == 0


# ── §19: the explainer, and which half of it was dictated ────────────

def test_the_explainer_is_titled_how_bettor_decides():
    assert cv.explainer()["title"] == "HOW BETTOR DECIDES"


def test_the_first_five_steps_are_the_directives_wording():
    e = cv.explainer()
    assert e["stepsFromDirective"] == [1, 2, 3, 4, 5]
    by = {s["n"]: s for s in e["steps"]}
    assert by[1]["text"] == \
        "BETTOR observes the market and its own inventory."
    assert by[2]["text"].startswith("It determines which actions are")
    assert by[4]["text"].startswith("It separates maker economics")


def test_the_completed_steps_are_marked_rather_than_passed_off():
    """The directive's text was truncated at step 6. Steps 6-8 are
    completed from the architecture and say so."""
    e = cv.explainer()
    assert e["stepsCompletedFromArchitecture"] == [6, 7, 8]
    for s in e["steps"]:
        if s["n"] >= 6:
            assert s["source"] == "COMPLETED_FROM_ARCHITECTURE"
    assert "truncated partway through step 6" in e["provenance"]
    assert "those three are the ones to correct" in e["provenance"]


def test_every_step_has_plain_english_beside_it():
    for s in cv.explainer()["steps"]:
        assert s["plain"]
        assert len(s["plain"]) > 40


def test_the_explainer_states_the_current_state_plainly():
    assert "SHADOW ONLY" in cv.explainer()["currentState"]
    assert "proposed, not frozen" in cv.explainer()["currentState"]


def test_the_panel_carries_the_explainer():
    assert _panel()["explainer"]["title"] == "HOW BETTOR DECIDES"


# ── the API assembler fetches; it does not compute ───────────────────

class _FakePool:
    """Just enough pool to answer the assembler's two reads."""

    def __init__(self, total=1676, two_sided=748, unreadable=840,
                 one_sided=88, open_positions=0):
        self._row = {"total": total, "two_sided": two_sided,
                     "unreadable": unreadable, "one_sided": one_sided}
        self._open = open_positions

    async def fetchrow(self, sql, *a):
        assert "bettor_opportunities" in sql
        assert "shadow_market_states" in sql
        return dict(self._row)

    async def fetchval(self, sql, *a):
        assert "BETTOR_EV_SHADOW" in sql
        return self._open


@pytest.mark.asyncio
async def test_the_assembler_reports_flat_only_when_it_read_zero():
    from sportsassets.api import command_shadow as CS
    p = await CS.bettor_engine(_FakePool(open_positions=0))
    assert p["headline"] == "BETTOR EV ENGINE"
    assert p["byLine"]["CURRENT_INVENTORY_STATE"]["value"] == "FLAT"
    assert "not because the read failed" in p["whyStateIsWhatItIs"]


@pytest.mark.asyncio
async def test_open_positions_are_not_summarised_into_one_state():
    from sportsassets.api import command_shadow as CS
    p = await CS.bettor_engine(_FakePool(open_positions=3))
    assert p["byLine"]["CURRENT_INVENTORY_STATE"]["value"] == \
        applic.STATE_NOT_IDENTIFIED
    assert p["byLine"]["CURRENT_INVENTORY_STATE"]["value"] != "FLAT"
    assert "per market by bettor_inventory" in p["whyStateIsWhatItIs"]


@pytest.mark.asyncio
async def test_readability_separates_unreadable_from_one_sided():
    from sportsassets.api import command_shadow as CS
    p = await CS.bettor_engine(_FakePool())
    r = p["readability"]
    assert (r["TOTAL"], r["TWO_SIDED"]) == (1676, 748)
    assert r["UNREADABLE"] == 840 and r["ONE_SIDED"] == 88
    assert r["RATE"] == 0.4463
    assert "capture problem with a liquidity problem" in r["whyThreeNumbers"]


@pytest.mark.asyncio
async def test_an_empty_dataset_has_no_readability_rate():
    from sportsassets.api import command_shadow as CS
    p = await CS.bettor_engine(
        _FakePool(total=0, two_sided=0, unreadable=0, one_sided=0))
    assert p["readability"]["RATE"] == "NOT_IDENTIFIED"
    assert p["byLine"]["MARKETS_OBSERVED"]["display"] == "NOT IDENTIFIED"


@pytest.mark.asyncio
async def test_the_panel_stays_visibly_shadow():
    from sportsassets.api import command_shadow as CS
    p = await CS.bettor_engine(_FakePool())
    assert p["disclosure"]
    assert p["shadowMandate"]["BETTOR_EV_REAL_CAPITAL_AT_RISK"] == 0

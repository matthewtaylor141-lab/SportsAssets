"""§18/§19. WHAT MANAGEMENT SEES. THE BETTOR EV ENGINE, NOT X1.

Owner directive, "FLAT-STATE PROOF IS APPROVED" §18:

    "After API reconciliation, update COMMAND so management sees the
    actual architecture. Headline: BETTOR EV ENGINE. Not X1. Show
    [thirty-one lines]. Everything unidentified should visibly say
    NOT IDENTIFIED, not 0. X-series belongs under RESEARCH /
    EXPERIMENTS."

    §19: "Add a plain-English panel: HOW BETTOR DECIDES."

THE HEADLINE IS A DECISION ABOUT WHAT THE COMPANY IS BUILDING. X1 is
research infrastructure; the BETTOR EV engine is the product, and a
command surface that leads with the research makes the research look
like the business. The X-series is still shown -- under RESEARCH /
EXPERIMENTS, where it belongs.

────────────────────────────────────────────────────────────────────
NOT IDENTIFIED, NEVER 0.

Most of this panel is NOT_IDENTIFIED today and it should look that
way. A dashboard that renders an unmeasured quantity as 0 tells
management the number is zero, and zero is a finding: HOLD EV of 0
says holding is worth nothing, which nobody has established. Worse,
zeros average, sum and chart; NOT_IDENTIFIED does none of those, which
is exactly why it is the safer thing to show.

`_blank` stamps every declared line so a missing measurement is a
visible absence rather than a missing row. A row that disappears when
it has no value is a row nobody notices is gone.
────────────────────────────────────────────────────────────────────

THE PANEL IS ASSEMBLED, NOT COMPUTED. Every figure here comes from the
module that owns it -- applicability, the maker engine, the pair
engine, the hedge tax, merge, the inventory ledger -- and this module
re-derives nothing. Two implementations of the same number eventually
disagree and the nicer one wins.

NOTHING HERE PLACES, SIZES OR FUNDS AN ORDER. It is a read.
"""

from __future__ import annotations

from . import bettor_applicability as applic
from . import bettor_ev_actions as acts
from . import bettor_exit_ml as exml
from . import bettor_hedge_tax as htx
from . import bettor_maker_engine as mke
from . import bettor_merge as mrg
from . import bettor_p_fill as pf
from . import bettor_pair_engine as pe
from . import bettor_shadow_mandate as smd

NOT_IDENTIFIED = "NOT_IDENTIFIED"
DISPLAY_NOT_IDENTIFIED = "NOT IDENTIFIED"

HEADLINE = "BETTOR EV ENGINE"
NOT_THE_HEADLINE = "X1"

WHY_THIS_HEADLINE = (
    "the X-series is research infrastructure; the BETTOR EV engine is "
    "the product. A command surface that leads with the research makes "
    "the research look like the business")

X_SERIES_SECTION = "RESEARCH / EXPERIMENTS"

NEVER_ZERO = (
    "an unmeasured quantity is shown as NOT IDENTIFIED, never as 0. "
    "Zero is a finding -- a HOLD EV of 0 says holding is worth nothing, "
    "which nobody has established -- and zeros average, sum and chart "
    "while NOT IDENTIFIED does none of those")

ASSEMBLED_NOT_COMPUTED = (
    "every figure comes from the module that owns it and nothing is "
    "re-derived here. Two implementations of the same number eventually "
    "disagree and the nicer one wins")

# ── §18: the thirty-one lines, in the order the directive gives them ─

LINES = (
    "MARKETS_OBSERVED",
    "READABLE_MARKETS",
    "FAIR_VALUE_STATUS",
    "MAKER_OPPORTUNITIES",
    "TAKER_OPPORTUNITIES",
    "CURRENT_INVENTORY_STATE",
    "YES_INVENTORY",
    "NO_INVENTORY",
    "MATCHED_INVENTORY",
    "RESIDUAL_INVENTORY",
    "PAIR_BASIS",
    "PAIR_COMPLETION_STATUS",
    "CURRENT_APPLICABLE_ACTIONS",
    "CURRENT_ACTION_RANKING",
    "P_FILL_STATUS",
    "HOLD_EV",
    "POST_COMPLEMENT_EV",
    "TAKE_COMPLEMENT_EV",
    "DIRECT_EXIT_EV",
    "HEDGE_EV",
    "MERGE_EV",
    "CAPITAL_DEPLOYED",
    "CAPITAL_HOURS",
    "CAPITAL_TURNS",
    "PAIR_PNL",
    "DIRECTIONAL_PNL",
    "RESIDUAL_INVENTORY_PNL",
    "EXIT_HEDGE_PNL",
    "REBATES",
    "INCENTIVES",
)

# The five EV lines are per-action economics and share one gate.
EV_LINES = ("HOLD_EV", "POST_COMPLEMENT_EV", "TAKE_COMPLEMENT_EV",
            "DIRECT_EXIT_EV", "HEDGE_EV", "MERGE_EV")

EV_LINE_ACTION = {
    "HOLD_EV": "HOLD",
    "POST_COMPLEMENT_EV": "POST_COMPLEMENT",
    "TAKE_COMPLEMENT_EV": "TAKE_COMPLEMENT",
    "DIRECT_EXIT_EV": "DIRECT_EXIT",
    "HEDGE_EV": "HEDGE",
    "MERGE_EV": "MERGE",
}


# A LINE WHOSE VALUE IS A STATUS IS NOT A MISSING MEASUREMENT.
# P_FILL_STATUS reads "NOT IDENTIFIED" because that IS the answer: we
# have measured that P_FILL is not identified. A quantity line reading
# "NOT IDENTIFIED" means nobody has measured it. The two look the same
# on screen and are not the same fact, so the kind is carried.
QUANTITY = "QUANTITY"
STATUS = "STATUS"

STATUS_LINES = ("FAIR_VALUE_STATUS", "PAIR_COMPLETION_STATUS",
                "P_FILL_STATUS", "CURRENT_INVENTORY_STATE")

WHY_KIND_MATTERS = (
    "a STATUS line reading NOT IDENTIFIED is the measured answer -- we "
    "know P_FILL is not identified. A QUANTITY line reading NOT "
    "IDENTIFIED means nobody has measured it. They look identical on "
    "screen and they are different facts")


def _blank(line, why, *, source=None, value=None, status=None):
    """One panel line. Present whether or not it has a value."""
    kind = STATUS if line in STATUS_LINES else QUANTITY
    identified = value is not None and value != NOT_IDENTIFIED
    # A status line is answered as soon as it carries a status string,
    # even when that string is NOT_IDENTIFIED.
    answered = identified or (kind == STATUS and value is not None)
    return {
        "line": line,
        "kind": kind,
        "value": value if value is not None else NOT_IDENTIFIED,
        "display": (str(value).replace("_", " ") if kind == STATUS and answered
                    else str(value) if identified else DISPLAY_NOT_IDENTIFIED),
        "status": status or ("IDENTIFIED" if answered else NOT_IDENTIFIED),
        "source": source or NOT_IDENTIFIED,
        "why": None if answered else why,
        "neverZero": None if answered else NEVER_ZERO,
        "whyKindMatters": WHY_KIND_MATTERS if kind == STATUS else None,
    }


def panel(*, inventory_state=None, markets_observed=None,
          readable_markets=None, inventory=None, pair=None,
          capital=None, pnl=None, root=None) -> dict:
    """The §18 panel. Every declared line present, absences visible."""
    state = inventory_state or applic.STATE_NOT_IDENTIFIED
    inv = inventory or {}
    pr = pair or {}
    cap = capital or {}
    books = pnl or {}

    applicable = applic.applicable_set(state)
    p = pf.p_fill(root=root)
    recycling = mrg.capital_recycling(mrg.INSTITUTIONAL)

    rows = {}

    def put(line, **kw):
        rows[line] = _blank(line, **kw)

    put("MARKETS_OBSERVED", value=markets_observed,
        source="bettor_opportunities",
        why="no market observation count was supplied to this panel")
    put("READABLE_MARKETS", value=readable_markets,
        source="bettor_opportunities",
        why="no readability measurement was supplied to this panel")
    # §15: the research gate stays where it is and is not tuned away.
    put("FAIR_VALUE_STATUS", value="FV_BETTOR_INDEPENDENT_NOT_IDENTIFIED",
        status="IDENTIFIED", source="p_bettor_independent research",
        why=None)
    # A maker opportunity requires P_FILL. A taker opportunity requires
    # an independent fair value. Neither exists, so neither counts.
    put("MAKER_OPPORTUNITIES",
        source="bettor_maker_engine",
        why=("a maker opportunity requires P_FILL, which is "
             "NOT_IDENTIFIED until BETTOR-native fills exist. The count "
             "is not zero -- nothing has been counted"))
    put("TAKER_OPPORTUNITIES",
        source="bettor_maker_engine",
        why=("a taker opportunity requires an independent fair value, "
             "and FV_BETTOR_INDEPENDENT is NOT_IDENTIFIED. The count is "
             "not zero -- nothing has been counted"))

    put("CURRENT_INVENTORY_STATE", value=state, status="IDENTIFIED",
        source="bettor_applicability", why=None)
    put("YES_INVENTORY", value=inv.get("YES_QTY"),
        source="bettor_inventory", why="no inventory state was supplied")
    put("NO_INVENTORY", value=inv.get("NO_QTY"),
        source="bettor_inventory", why="no inventory state was supplied")
    put("MATCHED_INVENTORY", value=inv.get("MATCHED_QTY"),
        source="bettor_inventory", why="no inventory state was supplied")
    put("RESIDUAL_INVENTORY", value=inv.get("RESIDUAL_YES_QTY")
        or inv.get("RESIDUAL_NO_QTY"),
        source="bettor_inventory", why="no inventory state was supplied")

    put("PAIR_BASIS", value=pr.get("EXPECTED_PAIR_BASIS"),
        source="bettor_pair_engine",
        why="a pair basis needs one leg held and a complement price")
    put("PAIR_COMPLETION_STATUS",
        value=pr.get("PAIR_COMPLETION_STATUS") or pe.PAIR_COMPLETION_STATUS,
        status="IDENTIFIED", source="bettor_pair_engine", why=None)

    put("CURRENT_APPLICABLE_ACTIONS",
        value=", ".join(applicable["APPLICABLE"]) or None,
        source="bettor_applicability",
        why=("no action is applicable in this state. That is a fact "
             "about the state, not a missing measurement"))
    # §14: do not rank unidentified or inapplicable actions. With no
    # identified economics there is nothing to order.
    put("CURRENT_ACTION_RANKING",
        source="bettor_capital_allocator",
        why=("no applicable action has identified economics, so there "
             "is nothing to rank. A ranking built from the terms that "
             "happen to be measurable would order candidates by capital "
             "or duration and present that as an economic judgement"))

    put("P_FILL_STATUS", value=p["status"], status="IDENTIFIED",
        source="bettor_p_fill", why=None)

    for line in EV_LINES:
        action = EV_LINE_ACTION[line]
        verdict = applic.applicability(action, state)
        if verdict["APPLICABILITY_STATUS"] != applic.APPLICABLE:
            rows[line] = _blank(
                line, verdict["APPLICABILITY_REASON"],
                source="bettor_applicability",
                status=verdict["APPLICABILITY_STATUS"])
            rows[line]["whyNotZero"] = applic.WHY_ZERO_IS_WORSE_THAN_NOTHING
            continue
        rows[line] = _blank(
            line, ("the action is applicable and its economics are not "
                   "identified: %s"
                   % ", ".join(acts.CANONICAL_ACTIONS[action].get(
                       "requires") or ("no stated precondition",))),
            source="bettor_ev_bridge")

    put("CAPITAL_DEPLOYED", value=cap.get("deployed"),
        source="bettor_capital_allocator",
        why="no shadow position exists, so no capital is deployed")
    put("CAPITAL_HOURS", value=cap.get("capitalHours"),
        source="bettor_capital_allocator",
        why="capital-hours needs a holding period, and none has occurred")
    put("CAPITAL_TURNS", value=cap.get("turns"),
        source="bettor_merge",
        why=("capital turns require the recycling chain, and it is "
             "%s on the institutional venue at %s"
             % (recycling["CAPITAL_RECYCLING_AVAILABLE"],
                ", ".join(recycling["stepsNotIdentified"]) or "no step")))

    for line, bucket in (("PAIR_PNL", "PAIR_PNL"),
                         ("DIRECTIONAL_PNL", "DIRECTIONAL_PNL"),
                         ("RESIDUAL_INVENTORY_PNL", "RESIDUAL_INVENTORY_PNL"),
                         ("EXIT_HEDGE_PNL", "EXIT_HEDGE_PNL"),
                         ("REBATES", "REBATES"),
                         ("INCENTIVES", "INCENTIVES")):
        put(line, value=books.get(bucket), source="bettor_pair_engine",
            why=("no BETTOR EV shadow position has ever existed, so this "
                 "bucket has no entries. It is not zero profit -- it is "
                 "no activity"))

    identified = [k for k, v in rows.items() if v["status"] == "IDENTIFIED"]
    return {
        "headline": HEADLINE,
        "notTheHeadline": NOT_THE_HEADLINE,
        "whyThisHeadline": WHY_THIS_HEADLINE,
        "xSeriesBelongsUnder": X_SERIES_SECTION,
        "lines": [rows[k] for k in LINES],
        "byLine": rows,
        "linesIdentified": sorted(identified),
        "linesDeclared": list(LINES),
        "statusLines": list(STATUS_LINES),
        "whyKindMatters": WHY_KIND_MATTERS,
        "neverZero": NEVER_ZERO,
        "assembledNotComputed": ASSEMBLED_NOT_COMPUTED,
        "pnlBucketsNeverBlended": pe.NEVER_BLENDED,
        "shadowMandate": {
            "MANDATE_STATUS": smd.MANDATE_STATUS,
            "BETTOR_EV_SHADOW_POSITIONS": smd.BETTOR_EV_SHADOW_POSITIONS,
            "BETTOR_EV_REAL_ORDER_ACTIVITY": smd.BETTOR_EV_REAL_ORDER_ACTIVITY,
            "BETTOR_EV_REAL_CAPITAL_AT_RISK":
                smd.BETTOR_EV_REAL_CAPITAL_AT_RISK,
        },
        "explainer": explainer(),
    }


# ── §19: HOW BETTOR DECIDES, in plain English ────────────────────────
#
# Steps 1-5 are the directive's own wording. The directive's text was
# truncated partway through step 6 ("If a pair"), so steps 6-8 are
# completed from the architecture as built and are MARKED as such
# rather than presented as dictated. If the intended wording differs,
# these three are the ones to correct.

EXPLAINER_TITLE = "HOW BETTOR DECIDES"

EXPLAINER_STEPS = (
    {"n": 1, "source": "DIRECTIVE",
     "text": "BETTOR observes the market and its own inventory.",
     "plain": ("Two readings, not one. What the book looks like, and "
               "what we are already holding. The second changes which "
               "questions are even worth asking")},
    {"n": 2, "source": "DIRECTIVE",
     "text": ("It determines which actions are actually possible right "
              "now."),
     "plain": ("You cannot sell something you do not own. Actions that "
               "cannot exist in the current state are set aside before "
               "anything is priced, so an attractive number on an "
               "impossible action never competes with a real one")},
    {"n": 3, "source": "DIRECTIVE",
     "text": "It estimates the economics of every applicable action.",
     "plain": ("Every one, including doing nothing. If a figure has not "
               "been measured it is shown as NOT IDENTIFIED rather than "
               "as zero, because zero is itself a claim")},
    {"n": 4, "source": "DIRECTIVE",
     "text": ("It separates maker economics, directional economics, "
              "pair economics and inventory economics."),
     "plain": ("These four can point in opposite directions at the same "
               "time. Added together they produce one number that "
               "explains nothing -- which is how a pair machine that "
               "works can be hidden inside a book that loses money")},
    {"n": 5, "source": "DIRECTIVE",
     "text": ("If it owns an unmatched leg, it compares holding, buying "
              "the complement, selling directly, hedging and waiting."),
     "plain": ("Five routes out of the same position, with different "
               "costs and different leftovers. Selling returns the "
               "capital; buying the other leg removes the risk but "
               "keeps the capital tied up")},
    {"n": 6, "source": "COMPLETED_FROM_ARCHITECTURE",
     "text": ("If a pair completes, it records the locked pair result "
              "and asks whether the venue will actually return the "
              "capital."),
     "plain": ("A completed pair is only worth the capital it frees. "
               "Retail nets automatically at the second fill; on the "
               "institutional venue no merge mechanism has been "
               "observed, so whether the capital comes back is NOT "
               "IDENTIFIED -- and the pair result is recorded either "
               "way")},
    {"n": 7, "source": "COMPLETED_FROM_ARCHITECTURE",
     "text": ("Risk limits are applied before any ranking, not after."),
     "plain": ("A candidate the risk gate refuses is not ranked at all. "
               "Ranking first and filtering second lets an attractive "
               "number argue with a limit")},
    {"n": 8, "source": "COMPLETED_FROM_ARCHITECTURE",
     "text": ("If nothing clears the complete decision contract, BETTOR "
              "does not trade."),
     "plain": ("NO TRADE is a result, not a failure, and it is recorded "
               "as its own outcome rather than as holding something")},
)

EXPLAINER_PROVENANCE = (
    "steps 1-5 are the directive's own wording. The directive's text "
    "was truncated partway through step 6, so steps 6-8 are completed "
    "from the architecture as built and are marked "
    "COMPLETED_FROM_ARCHITECTURE rather than presented as dictated. If "
    "the intended wording differs, those three are the ones to correct")


def explainer() -> dict:
    return {
        "title": EXPLAINER_TITLE,
        "steps": [dict(s) for s in EXPLAINER_STEPS],
        "provenance": EXPLAINER_PROVENANCE,
        "stepsFromDirective": [s["n"] for s in EXPLAINER_STEPS
                               if s["source"] == "DIRECTIVE"],
        "stepsCompletedFromArchitecture": [
            s["n"] for s in EXPLAINER_STEPS
            if s["source"] == "COMPLETED_FROM_ARCHITECTURE"],
        "currentState": (
            "BETTOR EV is SHADOW ONLY. No order has been placed, no "
            "capital is at risk, and the shadow mandate that would "
            "permit even a shadow position is proposed, not frozen"),
    }


def describe() -> dict:
    return {
        "headline": HEADLINE,
        "whyThisHeadline": WHY_THIS_HEADLINE,
        "xSeriesBelongsUnder": X_SERIES_SECTION,
        "linesDeclared": list(LINES),
        "evLines": list(EV_LINES),
        "neverZero": NEVER_ZERO,
        "assembledNotComputed": ASSEMBLED_NOT_COMPUTED,
        "explainer": explainer(),
        "sources": {
            "applicability": "bettor_applicability",
            "makerEconomics": "bettor_maker_engine",
            "pairEconomics": "bettor_pair_engine",
            "hedgeTax": "bettor_hedge_tax",
            "capitalRecycling": "bettor_merge",
            "exitDistributions": "bettor_exit_ml",
            "pFill": "bettor_p_fill",
            "mandate": "bettor_shadow_mandate",
        },
        "hedgeRule": htx.THE_RULE_IS_NOT_ALWAYS_HEDGE,
        "exitPolicyStatus": exml.POLICY_STATUS,
        "makerBindingComponent": mke.describe()["bindingComponentToday"],
    }

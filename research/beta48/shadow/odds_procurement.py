"""Section 5. The procurement calculator. Nothing is purchased.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.

Management needs to know exactly what access the experiment requires, in
requests and credits, before anyone is asked to approve a spend. This computes
that from the experiment's own shape -- events x horizons x markets -- through
the provider's declared quota formula.

THE ONE THING TO WATCH
----------------------
The credit formula is NOT verified: the provider's hosts are egress-blocked
here. It lives in odds_api_adapter.CREDIT_FORMULA, is read through that dict,
and every number this module emits inherits its NOT_VERIFIED status. The
arithmetic is right; the multiplier is an assumption. Correct the constant and
every scenario re-computes.

Cost in currency is deliberately NOT estimated. No plan price was read, and a
made-up price presented beside real request counts would be the most misleading
number in the document.
"""

import odds_api_adapter as ODDS

NOT_IDENTIFIED = "NOT_IDENTIFIED"

SCENARIOS = {"A": 100, "B": 500, "C": 1000, "D": 5000}

HORIZONS = ("T-24H", "T-12H", "T-6H", "T-3H", "T-2H", "T-1H",
            "T-30M", "T-15M", "T-5M")
HORIZON_HOURS = {"T-24H": 24.0, "T-12H": 12.0, "T-6H": 6.0, "T-3H": 3.0,
                 "T-2H": 2.0, "T-1H": 1.0, "T-30M": 0.5, "T-15M": 0.25,
                 "T-5M": 1.0 / 12.0}

MARKETS = ("H2H", "SPREAD", "TOTALS")

DEFAULT_REGIONS = ODDS.DEFAULT_REGIONS

REQUESTS_PER_EVENT_HORIZON_MARKET = 1
WHY_ONE = (
    "the historical endpoint returns all bookmakers for a sport, region and "
    "market in one response, so the request count is events x horizons x "
    "markets and NOT multiplied by the number of books. More books cost "
    "regions, not requests")

COST_IN_CURRENCY = NOT_IDENTIFIED
WHY_NO_CURRENCY = (
    "no plan price was read -- the provider's hosts are egress-blocked. A "
    "fabricated price beside real request counts would be the most misleading "
    "number in the document, so the column is left explicitly empty")


def requests_for(n_events, horizons=HORIZONS, markets=MARKETS):
    return int(n_events) * len(horizons) * len(markets)


def credits_for(n_events, horizons=HORIZONS, markets=MARKETS,
                regions=DEFAULT_REGIONS):
    """Credits under the declared formula: per request, markets x regions x mult.

    Each request asks for ONE market key, so N_MARKETS in the formula is 1 per
    request and the market count multiplies the REQUEST count instead. Getting
    that the wrong way round would square the market factor.
    """
    per_request = ODDS.credits_for(1, len(regions), historical=True)
    return requests_for(n_events, horizons, markets) * per_request


def scenario_table(horizons=HORIZONS, markets=MARKETS,
                   regions=DEFAULT_REGIONS):
    rows = {}
    for name, n in SCENARIOS.items():
        rows[name] = {
            "EVENTS": n,
            "HORIZONS": len(horizons),
            "MARKETS": len(markets),
            "REGIONS": len(regions),
            "REQUESTS": requests_for(n, horizons, markets),
            "CREDITS": credits_for(n, horizons, markets, regions),
            "ESTIMATED_COST": COST_IN_CURRENCY,
        }
    return {
        "SCENARIOS": rows,
        "CREDIT_FORMULA": dict(ODDS.CREDIT_FORMULA),
        "REGIONS": tuple(regions),
        "HORIZONS": tuple(horizons),
        "MARKETS": tuple(markets),
        "WHY_ONE_REQUEST_PER_EVENT_HORIZON_MARKET": WHY_ONE,
        "WHY_NO_CURRENCY": WHY_NO_CURRENCY,
        "NOTHING_IS_PURCHASED": True,
    }


def per_horizon_breakdown(n_events, markets=MARKETS, regions=DEFAULT_REGIONS):
    """Which horizons cost what, so the ladder can be trimmed deliberately."""
    per_request = ODDS.credits_for(1, len(regions), historical=True)
    return {h: {"REQUESTS": int(n_events) * len(markets),
                "CREDITS": int(n_events) * len(markets) * per_request}
            for h in HORIZONS}


def recommended_plan(scenario="B", horizons=HORIZONS, markets=MARKETS,
                     regions=DEFAULT_REGIONS):
    """A recommendation in CREDITS, with the reasoning, not a plan name.

    Naming a tier would require the price list, which was not read. What CAN be
    stated is the credit requirement and the experimental logic behind the
    scenario choice, which is what a purchasing decision actually needs.
    """
    n = SCENARIOS[scenario]
    c = credits_for(n, horizons, markets, regions)
    return {
        "RECOMMENDED_SCENARIO": scenario,
        "EVENTS": n,
        "CREDITS_REQUIRED": c,
        "REQUESTS_REQUIRED": requests_for(n, horizons, markets),
        "PLAN_NAME": NOT_IDENTIFIED,
        "WHY_NO_PLAN_NAME": "no price list was read; see WHY_NO_CURRENCY",
        "WHY_THIS_SCENARIO": (
            "the corrected INCREMENTAL ladder needs roughly 2,300 events at "
            "the point estimate and 4,200 at P90 to resolve a 0.010 effect in "
            "the research lane. 500 events does NOT reach that. It is chosen "
            "as a FIRST PURCHASE because it is enough to measure the lead/lag "
            "relationship, which needs far fewer events than a settlement "
            "effect does -- a price move is observed on every event, whereas a "
            "settlement is one binary outcome per event"),
        "WHAT_500_EVENTS_CAN_AND_CANNOT_ANSWER": {
            "CAN": ("whether external consensus leads Polymarket, and by how "
                    "much, conditional on disagreement size"),
            "CANNOT": ("whether external consensus improves settlement "
                       "forecasting -- that needs the full incremental ladder"),
        },
        "A_CHEAPER_FIRST_STEP": (
            "three horizons (T-24H, T-2H, T-15M) on one market (H2H) over 500 "
            "events costs %d credits and still answers the lead/lag question"
            % credits_for(500, ("T-24H", "T-2H", "T-15M"), ("H2H",), regions)),
    }


def describe():
    return {
        "SCENARIOS": dict(SCENARIOS),
        "HORIZONS": HORIZONS,
        "MARKETS": MARKETS,
        "TABLE": scenario_table(),
        "RECOMMENDED": recommended_plan(),
        "NOTHING_IS_PURCHASED": True,
        "NOTHING_IS_REQUESTED": True,
    }

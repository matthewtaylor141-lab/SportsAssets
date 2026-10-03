"""Decision-time admission facts for actual-lane tests (fake venue only).

`admissible_facts` is a complete, explicitly admissible fact set in the shape
`paper_benchmark.admission_facts` records. Its book currency names
TEST_RULE, which is admissible ONLY while a test installs it with
`approve_test_rule(monkeypatch)` -- production's approved set is empty, so
these facts never admit anything outside a test that says so.
"""
from __future__ import annotations

import copy

from sportsassets import actual_admission as AA

TEST_RULE = "TEST_ONLY_APPROVED_LIVE_BOOK_RULE"

_CHECKS = ("fixture_participants_date_side_period", "payout_outcome_match",
           "probability_qualified_by_the_lane", "polymarket_us_contract",
           "market_and_line", "grading_period_full_game",
           "ordinary_completion_grading_period")


def approve_test_rule(monkeypatch) -> None:
    monkeypatch.setattr(AA, "APPROVED_LIVE_BOOK_RULES", frozenset({TEST_RULE}))


def admissible_facts(*, slug: str, order_intent: str = "ORDER_INTENT_BUY_LONG",
                     wire: float = 0.55, depth: float = 5000.0,
                     p: float = 0.62) -> dict:
    return {
        "probability": {"p": p, "qualified": True, "age_s": 1.2,
                        "limit_s": 30.0, "refusal": None,
                        "provider": "pinnapi.com/raw-websocket",
                        "authority_basis": AA.PINNAPI_AUTHORITY,
                        "evidence": "SINGLE_SOURCE_PINNAPI", "outcome_books": 1,
                        "mapped": True},
        "identity": {"us_market_slug": slug, "order_intent": order_intent,
                     "payout_event": "Home", "period": "FULL_GAME",
                     "fixture": "event:test",
                     "checks": [{"check": c, "passed": True} for c in _CHECKS]},
        "book": {"obs_id": 1, "observed_at": None,
                 "observed_at_is": "OUR_RECEIPT_INSTANT",
                 "age_at_decision_s": 0.4, "source": "TEST",
                 "market_state": "MARKET_STATE_OPEN",
                 "depth_within_limit": depth,
                 "book_currency": {"verdict": "ESTABLISHED", "rule": TEST_RULE,
                                   "subscription_state": "RUNNING"}},
        "price": {"wire": wire, "limit": wire},
        "fees": {"known": True, "fees_usd": 0.01,
                 "model": "VENUE_FEE_FUNCTION_AT_DECISION"},
        "slippage": {"model": "IOC_LIMIT_AT_THE_DECISION_WIRE_PRICE",
                     "max_price": wire},
        "economics": {"net_ev_positive": True,
                      "net_expected_profit_usd": 12.5,
                      "conditional_on": None},
        "settlement": {"compatibility": "COMPATIBLE",
                       "overall_established": True, "blockers": [],
                       "lane_refusals": [], "research_disclosure": None}}


def mutate(facts: dict, path: str, value) -> dict:
    """A copy of `facts` with one dotted path replaced."""
    out = copy.deepcopy(facts)
    cur = out
    keys = path.split(".")
    for k in keys[:-1]:
        cur = cur[k]
    cur[keys[-1]] = value
    return out

"""Decision-time admission facts for actual-lane tests (fake venue only).

`admissible_facts` is a complete, explicitly admissible fact set in the shape
`paper_benchmark.admission_facts` records. Its book currency names
TEST_RULE, which is admissible ONLY while a test installs it with
`approve_test_rule(monkeypatch)` -- production's approved set is empty, so
these facts never admit anything outside a test that says so.

R30A: `approve_test_rule` also installs the settlement-compatibility gate's
approval (actual_admission.APPROVED_SETTLEMENT_GATES, empty in production):
since R30A the settlement gate admits only under an approval of its current
configuration, exactly as the book-currency rule does, and both are what a
lane-mechanics test states as its assumption. `assume_canonical_live_
authorization` states the other assumption those tests run under: the
canonical SMALL LIVE adapter's authorization (never issued in SHADOW), which
the ACTUAL lane requires before its claim. The refusals without it are proven
in tests/test_live_parity_convergence.py. `assume_canonical_funded_origination`
is the same assumption for the funded path's BUY boundary, used next to every
test's own flip of FUNDED_SUBMISSION_ENABLED. `record_test_gate_approvals`
writes, inside a transaction the test rolls back, the owner's live-gate
configuration approvals a test needs when it exercises the DATABASE path
(live_rule_artifacts + live_approvals) instead of the code constants.
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
    monkeypatch.setattr(AA, "APPROVED_SETTLEMENT_GATES",
                        frozenset({AA.SETTLEMENT_GATE_ID}))


async def _assumed_canonical_authorization(conn, **kw):
    return {"ok": True, "refusal": None, "token": None,
            "detail": {"assumed_by_test": True,
                       "why": ("a lane-mechanics test states the canonical "
                               "SMALL LIVE authorization as an assumption; "
                               "production issues none in SHADOW")}}


def assume_canonical_live_authorization(monkeypatch) -> None:
    from sportsassets import live_parity as LP
    monkeypatch.setattr(LP, "authorize_live_exposure",
                        _assumed_canonical_authorization)


async def _assumed_canonical_origination(conn, *, rec=None, plan=None):
    return {"ok": True, "refusal": None, "token": None,
            "detail": {"assumed_by_test": True,
                       "why": ("a funded-mechanics test states the canonical "
                               "origination authorization as an assumption, "
                               "exactly as it flips FUNDED_SUBMISSION_ENABLED;"
                               " production issues none in SHADOW")}}


def assume_canonical_funded_origination(monkeypatch) -> None:
    """R30A: the funded path's BUY boundary (bettor_funded_execution.
    canonical_origination) stated as satisfied, for tests that already flip
    FUNDED_SUBMISSION_ENABLED to exercise the funded mechanics against a fake
    transport. Its refusals are proven in
    tests/test_live_parity_convergence.py."""
    from sportsassets import bettor_funded_execution as FX
    monkeypatch.setattr(FX, "canonical_origination",
                        _assumed_canonical_origination)


TEST_APPROVER = "owner@example"


async def record_test_gate_approvals(conn, gates=None, *,
                                     by: str = TEST_APPROVER) -> None:
    """INSIDE A TRANSACTION THE CALLER ROLLS BACK: the owner's live-gate
    configuration approval for each gate, matched to this build's config sha
    -- the second part a test needs, next to its approved rule artifact, for
    the book gate (and the settlement gate) to admit. Nothing persists."""
    from sportsassets import live_approvals as LAP
    assert conn.is_in_transaction(), "test approvals must be rolled back"
    for g in (LAP.GATES if gates is None else gates):
        await conn.execute(
            "INSERT INTO live_approvals (subject_kind, subject_id, "
            " subject_version, config_sha256, decision, approved_by, statement)"
            " VALUES ($1,$2,$3,$4,'APPROVE',$5,$6)",
            LAP.KIND_GATE, g, LAP.gate_version(g), LAP.config_sha256(g), by,
            "test-only approval of %s config %s (rolled back)"
            % (g, LAP.config_sha256(g)))


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

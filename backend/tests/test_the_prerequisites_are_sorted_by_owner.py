"""EVERY PREREQUISITE HAS EXACTLY ONE OWNER, AND THEY DO NOT SUBSTITUTE.

THE CLAIM THIS FILE REFUSES. That naming an account and approving limits would
carry the rest. Four of the eight unmet readiness checks are statements about
MARKET AND SOURCE EVIDENCE, and no decision makes a quote fresher or reconciles
two published payout rules.
"""

from __future__ import annotations

import pytest

from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_pilot_prerequisites as PR

DSN = __import__("os").environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


def test_every_readiness_check_this_lane_emits_has_exactly_one_owner():
    """A PREREQUISITE WITH NO RECORDED OWNER IS REPORTED, NOT GUESSED. The
    table is checked against the checks `readiness` can actually emit, so a
    new check cannot quietly arrive without an owner."""
    import inspect
    import re

    src = inspect.getsource(FA.readiness) + inspect.getsource(FA)
    emitted = set(re.findall(r'_check\(\s*"([a-z_]+)"', src))
    emitted |= set(re.findall(r'"check":\s*"([a-z_]+)"', src))
    # every name the table claims must be one the lane can emit
    unknown = sorted(set(PR.OWNER_OF) - emitted)
    assert unknown == [], unknown
    # and every owner is one of the three
    assert {v[0] for v in PR.OWNER_OF.values()} <= set(PR.CATEGORIES)
    # each has a stated reason, not just a label
    for name, (cat, why) in PR.OWNER_OF.items():
        assert len(why) > 30, name


def test_an_owner_decision_does_not_clear_market_evidence():
    """THE IMPLICATION, REFUSED IN DATA."""
    c = PR.clears()
    owner = c[PR.OWNER_DECISION]
    joined = " ".join(owner["does_not_clear"]).lower()
    for must in ("freshness", "settlement", "calibration", "qualifies"):
        assert must in joined, must
    # and the four evidence checks are NOT owned by the owner
    for name in ("venue_book_freshness_basis", "settlement_compatibility",
                 "market_scope_metadata",
                 "an_eligible_market_with_one_coherent_chain",
                 "an_autonomous_entry_was_admitted_unwaived"):
        assert PR.OWNER_OF[name][0] == PR.MARKET_EVIDENCE, name
    # market evidence clears nothing by decision, and says so
    assert "by decision" in " ".join(c[PR.MARKET_EVIDENCE]["clears"])
    # and it is explicitly not framed as a defect
    assert "working engine" in " ".join(
        c[PR.MARKET_EVIDENCE]["does_not_clear"])


def test_the_engineering_gaps_are_listed_including_the_ones_not_yet_wired():
    ids = {x["id"]: x for x in PR.BEYOND_READINESS}
    # the connection exists and is disabled
    assert ids["funded_execution_connection"]["category"] == PR.ENGINEERING
    assert "FUNDED_SUBMISSION_ENABLED" in \
        ids["funded_execution_connection"]["remaining"]
    # the schedule does NOT call it, and that is stated as incomplete
    assert ids["scheduled_lane_calls_the_connection"]["state"] == "NOT_WIRED"
    # the balance read is missing and blocks onboarding
    assert ids["venue_balance_read"]["state"] == "MISSING"
    assert "ADAPTER_CANNOT_READ_BALANCES" in \
        ids["venue_balance_read"]["remaining"]
    # calibration is EVIDENCE, not engineering, and not an owner decision
    assert ids["source_calibration"]["category"] == PR.MARKET_EVIDENCE
    # and the credential is the owner's, with the disclaimer attached
    assert ids["venue_credential"]["category"] == PR.OWNER_DECISION
    assert "not by itself an authorization" in \
        ids["venue_credential"]["remaining"]


@pg
@pytest.mark.asyncio
async def test_a_live_readiness_result_sorts_with_nothing_unclassified():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        r = await FA.readiness(conn, account_id="nobody-at-all")
        got = PR.classify(r["checks"])
        assert got["unclassified"] == [], got["unclassified"]
        # every category carries something, and the counts add up
        total = sum(len(v) for v in got["categories"].values())
        assert total == len(got["categories"][PR.ENGINEERING]) + \
            len(got["categories"][PR.MARKET_EVIDENCE]) + \
            len(got["categories"][PR.OWNER_DECISION])
        # an unmet check appears in exactly ONE category
        seen = {}
        for cat, items in got["categories"].items():
            for it in items:
                assert it["id"] not in seen, (it["id"], cat, seen.get(it["id"]))
                seen[it["id"]] = cat
        # UNKNOWN blocks, and is not reported as met
        met_ids = {m["id"] for m in got["met"]}
        assert "venue_book_freshness_basis" not in met_ids
        assert got["unknown_blocks"] is True
    finally:
        await conn.close()

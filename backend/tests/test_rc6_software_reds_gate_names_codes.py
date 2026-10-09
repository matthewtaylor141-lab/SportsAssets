"""THE SOFTWARE-REDS GATE NAMES THE CODES IT COUNTS (RC6 lane C).

Production (pm-acceptance 37836393458, release 69a8a07e): completion.json
gate_evidence.software_reds_zero carried {"software": 82, "by_class": {...}}
and nothing that said WHICH codes the 82 were; every lane after it had to
re-derive the census with research SQL. The census the gate reads
(coverage_first_loss.read) already ranks its codes; the gate now carries the
SOFTWARE ones (stage, code, events), bounded, beside the count. Evidence
only: the gate's value is `software == 0`, read exactly as before.
"""
from __future__ import annotations

import asyncio

from sportsassets import coverage_first_loss as FL
from sportsassets.capital_readiness import feeds


def _ev(pid, code, *, slug="aec-x", outcome="REFUSED", stage=None):
    return {"sport_key": "americanfootball_ncaaf", "provider_event_id": pid,
            "family": "football", "rows": 1, "reach": 4, "stage": stage,
            "outcome": outcome, "first_refusal": code, "codes": [code],
            "us_market_slug": slug, "slugs": [slug]}


def _census_read(events, valuations=(), decisions=()):
    async def read(conn, *, since, until):
        got = FL.census(list(events), list(valuations), list(decisions))
        return dict(got, status="OK", why=None)
    return read


def test_the_gate_carries_its_software_codes_and_its_value_is_unchanged(
        monkeypatch):
    events = [
        _ev("e1", "QUOTE_STALE_ON_ARRIVAL", stage="2_FRESHNESS"),
        _ev("e2", "QUOTE_STALE_ON_ARRIVAL", slug="aec-y",
            stage="2_FRESHNESS"),
        _ev("e3", "PROBABILITY_DEADLINE_PASSED_BEFORE_THE_READ_COULD_FINISH",
            slug="aec-z", stage="2_FRESHNESS"),
        # an EXTERNAL first loss: counted in by_class, never named here
        _ev("e4", "NO_VENUE_CONTRACT_FOR_EVENT", slug=None,
            stage="3_IDENTITY")]
    monkeypatch.setattr(FL, "read", _census_read(events))
    got = asyncio.run(feeds.gate_software_reds_zero(None, {"now": 1e9}))
    assert got["value"] is False
    assert got["reason"] == "SOFTWARE_RED_FIRST_LOSSES_IN_LAST_HOUR"
    ev = got["evidence"]
    assert ev["software"] == 3
    assert ev["by_class"]["EXTERNAL"] == 1
    assert ev["software_by_code"] == [
        {"stage": "FAIR_VALUE", "code": "QUOTE_STALE_ON_ARRIVAL",
         "events": 2},
        {"stage": "FAIR_VALUE",
         "code": "PROBABILITY_DEADLINE_PASSED_BEFORE_THE_READ_COULD_FINISH",
         "events": 1}]
    assert ev["software_codes_truncated"] is False


def test_zero_software_still_passes_with_an_empty_list(monkeypatch):
    monkeypatch.setattr(FL, "read", _census_read([
        _ev("e4", "NO_VENUE_CONTRACT_FOR_EVENT", slug=None,
            stage="3_IDENTITY")]))
    got = asyncio.run(feeds.gate_software_reds_zero(None, {"now": 1e9}))
    assert got["value"] is True
    assert got["evidence"]["software"] == 0
    assert got["evidence"]["software_by_code"] == []


def test_the_list_is_bounded_and_says_so(monkeypatch):
    from sportsassets import refusal_taxonomy_table as RTT
    codes = sorted(c for c in RTT.TABLE
                   if FL.classify(c)["class"] == "SOFTWARE")
    events = [_ev("e%d" % i, c, slug="aec-%d" % i, stage="2_FRESHNESS")
              for i, c in enumerate(codes[:feeds.SOFTWARE_BY_CODE_MAX + 5])]
    monkeypatch.setattr(FL, "read", _census_read(events))
    got = asyncio.run(feeds.gate_software_reds_zero(None, {"now": 1e9}))
    ev = got["evidence"]
    assert len(ev["software_by_code"]) == feeds.SOFTWARE_BY_CODE_MAX
    assert ev["software_codes_truncated"] is True
    assert ev["software"] == len(events)


def test_an_unread_census_is_still_unavailable(monkeypatch):
    async def read(conn, *, since, until):
        return {"status": "UNAVAILABLE", "why": "SOURCE_TABLE_ABSENT:x"}
    monkeypatch.setattr(FL, "read", read)
    got = asyncio.run(feeds.gate_software_reds_zero(None, {"now": 1e9}))
    assert (got["value"], got["reason"]) == (
        False, "FIRST_LOSS_CENSUS_UNAVAILABLE")

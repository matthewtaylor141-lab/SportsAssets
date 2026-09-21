"""Normalizing real captured rows: identity, provenance, and refusals."""
from __future__ import annotations

import json

import pytest

from sportsassets import bettor_observation_adapter as oa
from sportsassets import bettor_venue_contract as vc

REAL = json.load(open("../research/beta48/acceptance/replay_sample_rows.json"))


def row(**kw):
    d = dict(observation_id="o1", market_id="atc-mlb-nym-tex-2026-09-22-i5-tex",
             event_id="mlb-nym-tex-2026-09-22", outcome_leg="no",
             observed_at="2026-09-21 13:57:01+00",
             book_source_ts="2026-09-21T13:57:00Z", book_age_s="1.0",
             venue_state="MARKET_STATE_OPEN",
             book_readability_status="READABLE",
             yes_bid="0.3800", yes_ask="0.3900",
             no_bid="NOT_IDENTIFIED", no_ask="NOT_IDENTIFIED")
    d.update(kw)
    return d


class TestIdentityAndProvenance:
    def test_a_clean_row_is_accepted(self):
        r = oa.normalize(row())
        assert r.status == oa.ACCEPTED, r.reasons
        assert r.market_id.startswith("atc-mlb-nym-tex")
        assert r.event_id == "mlb-nym-tex-2026-09-22"

    def test_market_id_is_the_contract_and_event_id_is_the_game(self):
        """Real data: astatc-mlb-tor-bal-...-hrr-alekir-gte6 and
        tsc-mlb-tor-bal-2026-09-21-8pt5 share event mlb-tor-bal-2026-09-21
        and are different contracts. Pairing on the event would match a
        home-run prop against a totals line."""
        a = oa.normalize(row(market_id="astatc-mlb-tor-bal-2026-09-21-hrr-x",
                             event_id="mlb-tor-bal-2026-09-21",
                             outcome_leg="no"))
        b = oa.normalize(row(market_id="tsc-mlb-tor-bal-2026-09-21-8pt5",
                             event_id="mlb-tor-bal-2026-09-21",
                             outcome_leg="over", observation_id="o2"))
        assert a.event_id == b.event_id
        assert a.market_id != b.market_id
        out = oa.pair_legs([a, b])
        assert out["pairs"] == [], "two contracts of one event were paired"
        assert len(out["unpaired"]) == 2

    def test_two_legs_of_one_contract_do_pair(self):
        a = oa.normalize(row(outcome_leg="no"))
        b = oa.normalize(row(outcome_leg="yes", observation_id="o2"))
        out = oa.pair_legs([a, b])
        assert len(out["pairs"]) == 1
        assert sorted(out["pairs"][0]["legs"]) == ["no", "yes"]

    def test_repeats_of_one_leg_do_not_pair(self):
        a = oa.normalize(row(outcome_leg="no"))
        b = oa.normalize(row(outcome_leg="no", observation_id="o2"))
        out = oa.pair_legs([a, b])
        assert out["pairs"] == []
        assert "distinct outcome leg" in out["unpaired"][0]["why"]

    def test_the_complement_is_never_derived(self):
        r = oa.normalize(row())
        assert r.complement_source == vc.ABSENT
        assert r.complement_source != vc.DERIVED

    def test_an_observed_complement_is_labelled_as_such(self):
        r = oa.normalize(row(no_bid="0.60", no_ask="0.62"))
        assert r.complement_source == vc.OBSERVED

    def test_institutional_semantics_are_stamped_not_borrowed(self):
        r = oa.normalize(row())
        assert r.venue == "polymarket-us"
        assert r.account_class == "institutional"
        caps = vc.capabilities(r.venue, r.account_class)
        assert caps.holds_both_legs_independently == vc.UNKNOWN

    def test_depth_is_absent_not_infinite(self):
        r = oa.normalize(row())
        assert r.depth_source == "ABSENT_IN_CAPTURE_SCHEMA"
        assert oa.to_book(r).yes_ask_size == 0.0


class TestRefusals:
    @pytest.mark.parametrize("field,reason", [
        ("market_id", oa.R_NO_MARKET_ID),
        ("outcome_leg", oa.R_NO_OUTCOME),
        ("book_source_ts", oa.R_NO_TIMESTAMP),
        ("venue_state", oa.R_NO_STATE),
    ])
    def test_missing_identity_is_rejected_by_name(self, field, reason):
        r = oa.normalize(row(**{field: oa.SENTINEL}))
        assert r.status == oa.REJECTED
        assert reason in r.reasons

    def test_a_stale_book_is_rejected(self):
        r = oa.normalize(row(book_age_s="25912.107"))
        assert oa.R_STALE in r.reasons

    def test_the_sentinel_is_not_a_price_of_zero(self):
        r = oa.normalize(row(yes_bid=oa.SENTINEL))
        assert r.bid is None
        assert any(x.startswith(oa.R_PRICE_SENTINEL) for x in r.reasons)

    def test_an_expired_market_is_rejected(self):
        r = oa.normalize(row(venue_state="MARKET_STATE_EXPIRED"))
        assert "MARKET_STATE_EXPIRED" in r.reasons

    def test_the_collectors_own_unreadable_flag_is_honoured(self):
        r = oa.normalize(row(
            book_readability_status="UNREADABLE:NO_BOOK_LEVELS_PUBLISHED"))
        assert any(x.startswith(oa.R_UNREADABLE) for x in r.reasons)

    def test_a_crossed_book_is_rejected(self):
        r = oa.normalize(row(yes_bid="0.60", yes_ask="0.40"))
        assert oa.R_CROSSED in r.reasons

    def test_nothing_is_fabricated_for_a_rejected_row(self):
        r = oa.normalize(row(yes_ask=oa.SENTINEL))
        assert r.ask is None


class TestAgainstTheRealCapturedSample:
    def test_every_real_row_is_rejected_and_says_why(self):
        recs = [oa.normalize(x) for x in REAL]
        rep = oa.report(recs)
        assert rep["rows"] == 16
        assert rep["accepted"] == 0, (
            "a real row became usable; re-read the staleness finding")
        assert rep["rejection_reasons"][oa.R_STALE] == 16

    def test_the_real_book_ages_are_reported(self):
        rep = oa.report([oa.normalize(x) for x in REAL])
        ages = rep["book_age_s"]
        assert ages["within_bound"] == 0
        assert ages["min"] > ages["decision_bound_s"]
        assert ages["max"] > 25000

    def test_no_real_row_carries_a_complement(self):
        rep = oa.report([oa.normalize(x) for x in REAL])
        assert rep["complement_sources"][vc.OBSERVED] == 0
        assert rep["complement_sources"][vc.ABSENT] == 16

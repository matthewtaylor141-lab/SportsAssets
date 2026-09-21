"""Normalizing real captured rows: identity, provenance, and refusals."""
from __future__ import annotations

import json

import pytest

from sportsassets import bettor_decision_engine as de
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
             yes_depth='{"bid": "2565.0000", "ask": "2702.0000",'
                       ' "levelsCaptured": 5}',
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

    def test_three_rows_two_of_one_label_still_pair_correctly(self):
        """The old body sorted and sliced [:2], so a contract seen twice
        on one side produced a no/no 'pair'."""
        rs = [oa.normalize(row(outcome_leg="no")),
              oa.normalize(row(outcome_leg="no", observation_id="o2")),
              oa.normalize(row(outcome_leg="yes", observation_id="o3"))]
        out = oa.pair_legs(rs)
        assert len(out["pairs"]) == 1
        assert sorted(out["pairs"][0]["legs"]) == ["no", "yes"]

    def test_repeats_of_one_leg_do_not_pair(self):
        a = oa.normalize(row(outcome_leg="no"))
        b = oa.normalize(row(outcome_leg="no", observation_id="o2"))
        out = oa.pair_legs([a, b])
        assert out["pairs"] == []
        assert "repeats of one side" in out["unpaired"][0]["why"]

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

    def test_depth_is_read_from_the_row(self):
        """I declared depth absent from the schema after reading the base
        table and never querying yes_depth. It is there, with a
        five-level ladder."""
        r = oa.normalize(row())
        assert r.depth_source == "DISPLAYED_DEPTH_AT_T0"
        assert r.ask_size == 2702.0
        assert oa.to_book(r).yes_ask_size == 2702.0

    def test_a_row_without_depth_is_rejected_not_zeroed(self):
        """Zero depth and unrecorded depth are different facts and only
        one of them is about the market."""
        r = oa.normalize(row(yes_depth=None))
        assert r.status == oa.REJECTED
        assert oa.R_NO_DEPTH in r.reasons

    def test_complement_family_is_required_not_mere_difference(self):
        a = oa.normalize(row(outcome_leg="over"))
        b = oa.normalize(row(outcome_leg="no", observation_id="o2"))
        out = oa.pair_legs([a, b])
        assert out["pairs"] == [], "over/no is not a complement family"
        assert "complement family" in out["unpaired"][0]["why"]

    def test_source_timestamps_must_align(self):
        a = oa.normalize(row(outcome_leg="yes"))
        b = oa.normalize(row(outcome_leg="no", observation_id="o2",
                             book_source_ts="2026-09-21T14:00:00Z"))
        out = oa.pair_legs([a, b])
        assert out["pairs"] == []
        assert "timestamps" in out["unpaired"][0]["why"]

    def test_a_pair_is_a_candidate_not_a_verified_executable_pair(self):
        a = oa.normalize(row(outcome_leg="yes"))
        b = oa.normalize(row(outcome_leg="no", observation_id="o2"))
        out = oa.pair_legs([a, b])
        assert len(out["pairs"]) == 1
        p = out["pairs"][0]
        assert p["family"] == "no/yes"
        assert "source_skew_s" in p and "executable" in p
        assert "CANDIDATE" in out["note"]


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
    """Properties of the real sample, not counts.

    The first version asserted 16 rows and 0 accepted, which pinned one
    snapshot of the export. When the export was re-run ordered by
    freshness the sample changed and the tests failed for a reason that
    had nothing to do with the adapter. Assert what must be true of ANY
    real capture instead.
    """

    def test_no_real_row_carries_a_complement(self):
        """The sibling instrument has never been read, in any export."""
        rep = oa.report([oa.normalize(x) for x in REAL])
        assert rep["complement_sources"][vc.OBSERVED] == 0
        assert rep["complement_sources"][vc.DERIVED] == 0
        assert rep["complement_sources"][vc.ABSENT] == rep["rows"]

    def test_every_rejection_names_a_reason(self):
        recs = [oa.normalize(x) for x in REAL]
        for r in recs:
            if r.status == oa.REJECTED:
                assert r.reasons, "%s rejected with no reason" % r.market_id
            else:
                assert not r.reasons

    def test_acceptance_is_exactly_the_staleness_bound(self):
        """Nothing else in this capture disqualifies a row: the books are
        readable, open and two-sided, so age is the whole filter."""
        recs = [oa.normalize(x) for x in REAL]
        for r in recs:
            fresh = r.age_s is not None and r.age_s <= de.MAX_BOOK_AGE_S
            if r.status == oa.ACCEPTED:
                assert fresh
            else:
                # The export that produced this sample did not SELECT the
                # depth column, so these rows are additionally rejected
                # for NO_DEPTH. That is a property of the extract, not of
                # the capture -- yes_depth exists and is populated.
                assert (not fresh) or oa.R_NO_DEPTH in r.reasons

    def test_the_age_distribution_is_reported(self):
        rep = oa.report([oa.normalize(x) for x in REAL])
        ages = rep["book_age_s"]
        assert ages["decision_bound_s"] == de.MAX_BOOK_AGE_S
        assert ages["min"] <= ages["median"] <= ages["max"]
        assert 0 <= ages["within_bound"] <= rep["rows"]

    def test_a_real_accepted_row_reaches_a_decision(self):
        """End to end on REAL data: normalize -> Book -> decide."""
        recs = [oa.normalize(x) for x in REAL]
        accepted = [r for r in recs if r.status == oa.ACCEPTED]
        if not accepted:
            pytest.skip("this extract omitted the depth column; re-export "
                        "with yes_depth to exercise the decision path")
        for r in accepted[:5]:
            d = de.decide(oa.to_book(r), fees=de.Fees(source=r.fee_source),
                          max_contracts=10, venue=r.venue,
                          account_class=r.account_class)
            assert d["data_quality"] == "OK"
            assert d["selected"] == de.NO_TRADE, (
                "a real row produced an action; every path is blocked or "
                "unidentified on this venue contract")

#!/usr/bin/env python3
"""The fee schedule, asserted against CAPTURED BYTES rather than a retyping.

WHY THIS FILE EXISTS. Every fee number in this programme arrived relayed. A
relayed number is not a measured one, and the standing rule has been that it
stays labelled until our own capture holds the primary source.

Our own capture now holds it. `fwd_collect.py rules` fetched
https://docs.polymarket.us/fees on 2026-09-16 00:57:11Z and stored the page
verbatim with a sha256. These tests read THAT TEXT and assert the schedule out
of it. If the venue changes the page, or if someone edits `fees_v2.py` away
from what the page says, a test here fails.

WHAT THIS DOES NOT ESTABLISH. The page states an UPCOMING change. Reading a
page that announces 0.0695 is not the same as observing 0.0695 IN FORCE, so
`VERIFIED_FROM_CAPTURE` stays False until a segment crosses the cutover and
captures the changed state. Schedule captured; regime not yet observed.

The fixture is committed evidence, not a re-fetch: these tests contact nothing.
"""
from __future__ import annotations

import gzip
import html
import json
import re
import sys
import unittest
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fees_v2 as F  # noqa: E402

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "fee_page_20260916.txt.gz"


def _text():
    if not FIXTURE.exists():
        raise unittest.SkipTest("fee page fixture not present")
    raw = gzip.open(FIXTURE, "rt").read()
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body)))


class TheFormula(unittest.TestCase):

    def setUp(self):
        self.t = _text()

    def test_the_page_states_the_symmetric_formula(self):
        self.assertIn("Fee = ", self.t)
        self.assertIn("C is the number of contracts", self.t)
        self.assertIn("(theta) is the fee coefficient", self.t)

    def test_the_page_states_the_taker_coefficient_we_implement(self):
        self.assertIn("Taker Fee 0.06", self.t)
        self.assertEqual(F.THETA_TAKER_JUL2026, D("0.06"))

    def test_the_page_states_the_maker_coefficient_we_implement(self):
        self.assertIn("Maker Rebate -0.0125", self.t)
        self.assertEqual(F.THETA_MAKER_ALL, D("-0.0125"))

    def test_the_maker_rebate_is_applied_at_the_point_of_trade(self):
        """Which is why it is only ever credited on an actual fill."""
        self.assertIn("Maker rebate is applied at the point of trade", self.t)

    def test_the_price_domain_is_one_cent_to_ninety_nine(self):
        self.assertIn("$0.01 to $0.99", self.t)


class TheTierSchedule(unittest.TestCase):

    def setUp(self):
        self.t = _text()

    def test_every_band_we_implement_appears_on_the_page(self):
        self.assertIn("$250,000 - $999,999 10%", self.t)
        self.assertIn("$1,000,000 - $9,999,999 25%", self.t)
        self.assertIn("$10,000,000+ 50%", self.t)
        self.assertEqual(F.tier_for_prior_month_volume(250000), "T10_250k_1M")
        self.assertEqual(F.tier_for_prior_month_volume(1000000), "T25_1M_10M")
        self.assertEqual(F.tier_for_prior_month_volume(10000000),
                         "T50_10M_PLUS")

    def test_the_tier_is_set_by_the_PRIOR_calendar_month(self):
        """Not trailing 30 days, and not the current month. A tier cannot be
        earned and used inside the same month."""
        self.assertIn("prior calendar month", self.t)
        self.assertIn("immediately preceding calendar month", self.t)

    def test_rebates_are_paid_weekly_which_is_not_at_the_point_of_trade(self):
        """So a taker rebate is a RECEIVABLE, not a fill-time economic. It
        cannot be netted into a per-fill cost the way the maker rebate can."""
        self.assertIn("Rebates are paid out weekly", self.t)

    def test_the_accelerated_route_exists_and_is_an_application(self):
        self.assertIn("Accelerated Tier Placement", self.t)
        self.assertIn("verifiable proof", self.t)
        self.assertIn("another prediction market", self.t)
        self.assertEqual(F.ACCELERATED_TIER_PLACEMENT_ROUTE,
                         "DOCUMENTED_APPLICATION_OUTCOME_UNKNOWN")

    def test_a_documented_route_still_leaves_us_ineligible_until_granted(self):
        self.assertEqual(F.BETTOR_TIER_ELIGIBILITY, "NOT_IDENTIFIED")
        self.assertFalse(F.TIER_VERIFIED)


class TheAnnouncedChange(unittest.TestCase):

    def setUp(self):
        self.t = _text()

    def test_the_page_announces_the_coefficient_and_the_moment(self):
        self.assertIn("11:59 PM ET, Wednesday September 16, 2026", self.t)
        self.assertIn("increases from 0.06 to 0.0695", self.t)
        self.assertEqual(F.THETA_TAKER_SEP2026, D("0.0695"))

    def test_the_page_says_maker_and_taker_rebates_are_unchanged(self):
        self.assertIn("Maker rebates and taker rebates are unchanged", self.t)

    def test_reading_an_announcement_is_not_observing_a_regime(self):
        """The page says 'in effect today' about the OLD numbers and 'will be
        updated when the changes take effect'. Capturing that sentence is not
        the same as capturing the new regime in force."""
        self.assertIn("fees in effect today", self.t)
        self.assertIn("updated when the changes take effect", self.t)
        self.assertFalse(F.VERIFIED_FROM_CAPTURE)
        self.assertFalse(F.THETA_TAKER_SEP2026_VERIFIED)
        self.assertEqual(F.PUBLIC_FEE_SCHEDULE, "CAPTURED_FROM_PRIMARY_SOURCE")


class CombosHaveTheirOwnUpcomingCurve(unittest.TestCase):
    """A COST that is verified from primary docs, for a BENEFIT that is not.

    Both halves matter. The premium is real and dated; whether it buys lower
    execution risk is the open question, and these tests are careful not to
    answer it in either direction.
    """

    def setUp(self):
        self.t = _text()

    def test_the_page_states_a_separate_combo_curve(self):
        self.assertIn("combo taker fee curve becomes", self.t)
        self.assertIn("0.0695(1 - p) + 0.04(1 - p)^4", self.t)

    def test_the_combo_fee_is_the_single_leg_fee_plus_a_surcharge(self):
        for p in ("0.05", "0.20", "0.50", "0.80", "0.95"):
            surcharge = F.upcoming_combo_vs_single_leg_surcharge(1000, D(p))
            self.assertGreater(surcharge, 0,
                               "a combo is never cheaper to take, at p=%s" % p)

    def test_the_surcharge_peaks_in_the_cheap_tail_not_at_even_money(self):
        """p(1-p)^4 is maximised at p = 0.2, which is precisely the band the
        retrospective work kept finding interesting."""
        at_20 = F.upcoming_combo_vs_single_leg_surcharge(1000, D("0.20"))
        for other in ("0.05", "0.10", "0.30", "0.50", "0.80"):
            self.assertGreater(at_20,
                               F.upcoming_combo_vs_single_leg_surcharge(1000, D(other)))

    def test_the_surcharge_at_its_peak_is_about_29_percent_of_the_single_fee(self):
        single = F.exact_fee(F.theta_taker("SEP2026"), 1000, D("0.20")) \
            if hasattr(F, "exact_fee") else None
        from run85_trackb_fees import exact_fee
        single = exact_fee(F.theta_taker("SEP2026"), 1000, D("0.20"))
        sur = F.upcoming_combo_vs_single_leg_surcharge(1000, D("0.20"))
        self.assertEqual(sur, D("3.2768000"))          # $0.0032768/contract
        self.assertAlmostEqual(float(sur / single), 0.2946, places=3)

    def test_the_current_combo_curve_is_not_invented(self):
        """Only the UPCOMING form was captured. Nothing in the page says the
        surcharge term exists today, so the current curve stays unidentified
        rather than being back-formed by swapping the coefficient."""
        self.assertEqual(F.CURRENT_COMBO_TAKER_FEE_FORMULA, "NOT_IDENTIFIED")
        with self.assertRaises(ValueError):
            F.upcoming_combo_taker_fee(1000, D("0.50"), "JUL2026")

    def test_the_premium_is_verified_but_the_benefit_is_not_prejudged(self):
        """The venue clearly charges an upcoming premium. Whether it buys
        materially lower orphan/execution risk is UNRESOLVED -- and must not
        be recorded as zero just because it is unmeasured."""
        self.assertEqual(F.UPCOMING_COMBO_TAKER_COST_PREMIUM,
                         "VERIFIED_FROM_PRIMARY_DOCS")
        for f in ("COMBO_ORPHAN_RISK_REDUCTION", "COMBO_EXECUTION_ATOMICITY",
                  "COMBO_PARTIAL_FILL_BEHAVIOR",
                  "COMBO_NET_VALUE_VS_SINGLE_LEG_EXECUTION"):
            self.assertEqual(getattr(F, f), "NOT_IDENTIFIED", f)

    def test_combo_maker_and_incentive_treatment_stay_unidentified(self):
        """The page's 'maker rebates are unchanged' is a statement about the
        September update, not about how a combo's maker leg is treated."""
        self.assertEqual(F.COMBO_MAKER_REBATE_TREATMENT, "NOT_IDENTIFIED")
        self.assertEqual(F.COMBO_INCENTIVE_ELIGIBILITY, "NOT_IDENTIFIED")


class TheCaptureItself(unittest.TestCase):

    def test_the_fixture_is_byte_identical_to_what_the_collector_fetched(self):
        """The strongest available statement that this is evidence.

        The fixture decompresses to bytes whose sha256 equals the hash the
        COLLECTOR computed over the live response at fetch time, independently,
        before this file existed. So these tests are not reading a transcript
        of the page -- they are reading the page.
        """
        import hashlib
        if not FIXTURE.exists():
            raise unittest.SkipTest("fixture not present")
        meta = json.loads((FIXTURE.parent / "fee_page_20260916.json")
                          .read_text())
        raw = gzip.open(FIXTURE, "rb").read()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), meta["page_sha256"])
        self.assertEqual(meta["page_sha256"], F.FEE_PAGE_SHA256)
        self.assertEqual(len(raw), meta["response_bytes"])
        self.assertEqual(meta["http_status"], 200)
        self.assertEqual(meta["url"], "https://docs.polymarket.us/fees")


if __name__ == "__main__":
    unittest.main(verbosity=2)

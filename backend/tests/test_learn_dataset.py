"""THE DATASET'S REFUSALS, EXERCISED.

Every fixture here is SYNTHETIC. The failures these pin are the ones
that make a dataset look better than the data:

    a label built from a fill we could not yet have seen
    a censored row counted as a negative
    an entry after the complement is already held (the label leaks)
    a split that cuts through one market
    an ingestion gap scored as "no complementary fill happened"
    a missing gap treated as a zero gap
"""
from __future__ import annotations

import pytest

from sportsassets.learn import dataset as D

T0 = 1_790_000_000.0
H = 3600.0


def fill(account, cond, oi, t, *, side="BUY", price=0.5, size=100.0,
         source="chain", detected=None, slug="m1", sport="nfl"):
    return {"account": account, "condition_id": cond, "outcome_index": oi,
            "side": side, "price": price, "size": size, "ts": t,
            "detected_at": t if detected is None else detected,
            "source": source, "market_slug": slug, "sport": sport}


def build(fills, end=T0 + 100000.0, excl=(), horizon=H):
    return D.build(fills, horizon_s=horizon, observation_end=end,
                   coverage_exclusions=excl)


# ── ingestion mode, by provenance not by date ────────────────────────

class TestIngestionMode:

    def test_chain_and_s1_are_live_lanes_whatever_the_clocks_say(self):
        """THE CORRECTION THIS PINS. A recent `ts` does not make a row
        forward-ingested and an old one does not make it a backfill.
        The lane decides."""
        for src in ("chain", "s1"):
            r = fill("a", "c", 0, T0, source=src, detected=T0 - 5.0)
            assert D.ingestion_mode(r) == D.MODE_LIVE_LANE

    def test_poll_is_split_by_the_repositorys_own_threshold(self):
        """The poller both tails live AND repairs history, so `poll`
        alone cannot say which. 900 s is shadow_v2's own line."""
        prompt = fill("a", "c", 0, T0, source="poll", detected=T0 + 120.0)
        late = fill("a", "c", 0, T0, source="poll", detected=T0 + 901.0)
        assert D.ingestion_mode(prompt) == D.MODE_POLL_PROMPT
        assert D.ingestion_mode(late) == D.MODE_POLL_LATE

    def test_an_unknown_source_is_not_guessed(self):
        assert D.ingestion_mode(fill("a", "c", 0, T0, source="whatever")) \
            == D.MODE_UNKNOWN


# ── the clock rule ───────────────────────────────────────────────────

class TestAvailableAt:

    def test_it_is_the_later_of_the_two_clocks(self):
        assert D.available_at(fill("a", "c", 0, T0, detected=T0 - 9.0)) == T0
        assert D.available_at(fill("a", "c", 0, T0, detected=T0 + 9.0)) \
            == T0 + 9.0

    def test_a_negative_difference_never_becomes_a_negative_latency(self):
        """Run 82: 92.04% of chain-lane differences are negative, and an
        elapsed time cannot be. The cutoff must not go BACKWARDS from
        `ts` just because our stamp is earlier."""
        r = fill("a", "c", 0, T0, detected=T0 - 0.6)
        assert D.available_at(r) == T0

    def test_the_dataset_says_what_the_cutoff_does_not_establish(self):
        ds = build([fill("a", "c", 0, T0)])
        note = ds["clock_note"]
        assert "conservative cutoff" in note
        assert "not an availability guarantee" in note.lower()
        assert "92.04%" in note


# ── the label ────────────────────────────────────────────────────────

class TestLabel:

    def test_a_complementary_buy_inside_the_horizon_is_a_positive(self):
        ds = build([fill("a", "c", 0, T0),
                    fill("a", "c", 1, T0 + 600.0)])
        assert ds["n_rows"] == 1
        assert ds["rows"][0]["label"] == 1
        assert ds["rows"][0]["time_to_event_s"] == 600.0

    def test_a_complementary_buy_outside_the_horizon_is_a_negative(self):
        ds = build([fill("a", "c", 0, T0),
                    fill("a", "c", 1, T0 + H + 1.0)])
        assert ds["rows"][0]["label"] == 0
        assert ds["rows"][0]["censored"] is False

    def test_the_same_leg_again_is_not_a_complement(self):
        ds = build([fill("a", "c", 0, T0), fill("a", "c", 0, T0 + 10.0)])
        assert all(r["label"] == 0 for r in ds["rows"])

    def test_another_accounts_complement_does_not_count(self):
        """Account identity is part of the key. Two accounts in one
        market are two subjects, not one."""
        ds = build([fill("a", "c", 0, T0), fill("b", "c", 1, T0 + 60.0)])
        by = {r["account"]: r for r in ds["rows"]}
        assert by["a"]["label"] == 0

    def test_a_sell_is_never_a_complementary_buy(self):
        ds = build([fill("a", "c", 0, T0),
                    fill("a", "c", 1, T0 + 60.0, side="SELL")])
        assert ds["rows"][0]["label"] == 0

    def test_the_label_uses_availability_not_event_time(self):
        """THE LEAK THIS PINS. A complement whose EVENT time is inside
        the horizon but which we could not have seen until after it
        must not be scored as a positive at the horizon we claim."""
        ds = build([fill("a", "c", 0, T0),
                    fill("a", "c", 1, T0 + 100.0,
                         detected=T0 + H + 500.0)])   # seen long after
        assert ds["rows"][0]["label"] == 0

    def test_a_simultaneous_fill_is_not_already_known(self):
        ds = build([fill("a", "c", 0, T0), fill("a", "c", 1, T0)])
        # The complement is available at exactly t0, which is not
        # strictly after it.
        assert ds["rows"][0]["label"] == 0


# ── censoring ────────────────────────────────────────────────────────

class TestCensoring:

    def test_an_unfinished_horizon_is_censored_not_negative(self):
        ds = build([fill("a", "c", 0, T0)], end=T0 + 60.0)
        r = ds["rows"][0]
        assert r["censored"] is True
        assert r["label"] == 0          # the stored label, not a verdict
        assert D.uncensored(ds["rows"]) == []

    def test_a_positive_inside_an_unfinished_horizon_is_still_decided(self):
        """It already happened, so nothing is unknown about it."""
        ds = build([fill("a", "c", 0, T0), fill("a", "c", 1, T0 + 30.0)],
                   end=T0 + 60.0)
        assert ds["rows"][0]["censored"] is False
        assert ds["rows"][0]["label"] == 1

    def test_the_base_rate_excludes_censored_rows(self):
        ds = build([fill("a", "c1", 0, T0),
                    fill("a", "c1", 1, T0 + 10.0),
                    fill("a", "c2", 0, T0 + 99000.0)],
                   end=T0 + 99500.0)
        # one decided positive, one censored -> base rate 1.0, not 0.5
        assert ds["n_censored"] == 1
        assert ds["base_rate_uncensored"] == 1.0


# ── leakage from the running state ───────────────────────────────────

class TestEntryDefinition:

    def test_a_buy_after_the_complement_is_held_is_not_an_entry(self):
        """THE LABEL LEAK THIS PINS. Once the account holds the
        complement the question is already answered for that position,
        and such a row would be a guaranteed-looking positive."""
        ds = build([fill("a", "c", 0, T0),
                    fill("a", "c", 1, T0 + 100.0),
                    fill("a", "c", 0, T0 + 200.0)])
        assert ds["n_rows"] == 1
        assert ds["rows"][0]["decision_at"] == T0

    def test_features_see_only_fills_strictly_before_the_decision(self):
        ds = build([fill("a", "c", 0, T0, size=10.0),
                    fill("a", "c", 0, T0 + 50.0, size=20.0)])
        first, second = ds["rows"][0], ds["rows"][1]
        assert first["features"]["prior_fills_this_market"] == 0.0
        assert first["features"]["is_first_fill_in_market"] == 1.0
        assert second["features"]["prior_fills_this_market"] == 1.0
        # the second row's prior quantity is the FIRST fill's size only
        assert abs(second["features"]["prior_qty_same_leg_log"]
                   - __import__("math").log1p(10.0)) < 1e-12

    def test_a_missing_gap_carries_an_indicator_not_a_zero(self):
        """A first fill has no previous one. The sentinel is zero AND
        `is_first_fill_in_market` is 1, so the model can tell a missing
        gap from an instantaneous one."""
        ds = build([fill("a", "c", 0, T0)])
        f = ds["rows"][0]["features"]
        assert f["seconds_since_prev_fill_this_market_log"] == 0.0
        assert f["is_first_fill_in_market"] == 1.0


# ── coverage exclusions ──────────────────────────────────────────────

class TestCoverageExclusions:

    GAP = [(T0 + 1000.0, T0 + 2000.0)]

    def test_an_entry_inside_a_gap_is_dropped(self):
        ds = build([fill("a", "c", 0, T0 + 1500.0)], excl=self.GAP)
        assert ds["n_rows"] == 0
        assert ds["skipped"]["in_excluded_window"] == 1

    def test_an_entry_whose_horizon_overlaps_a_gap_is_dropped(self):
        """THE FAILURE THIS PINS. The complementary fill could have
        happened inside the hole and gone unrecorded, and scoring that
        as a negative would teach the model that this account does not
        complete."""
        ds = build([fill("a", "c", 0, T0 + 500.0)], excl=self.GAP)
        assert ds["n_rows"] == 0
        assert ds["skipped"]["horizon_overlaps_gap"] == 1

    def test_an_entry_clear_of_the_gap_survives(self):
        ds = build([fill("a", "c", 0, T0 + 5000.0)], excl=self.GAP)
        assert ds["n_rows"] == 1

    def test_the_exclusions_are_recorded_in_the_dataset(self):
        ds = build([fill("a", "c", 0, T0 + 5000.0)], excl=self.GAP)
        assert ds["coverage_exclusions"] == [[T0 + 1000.0, T0 + 2000.0]]


# ── splitting ────────────────────────────────────────────────────────

class TestSplit:

    def _ds(self):
        f = []
        for i in range(60):
            # one condition per entry, spread across time
            f.append(fill("a", "c%02d" % i, 0, T0 + i * 1000.0))
            if i % 2 == 0:
                f.append(fill("a", "c%02d" % i, 1, T0 + i * 1000.0 + 100.0))
        return build(f, end=T0 + 200000.0)

    def test_the_three_parts_are_in_time_order(self):
        ds = self._ds()
        s = D.split_by_time(ds, train_end=T0 + 30000.0,
                            calib_end=T0 + 45000.0)
        assert all(r["decision_at"] < T0 + 30000.0
                   for r in s["parts"]["TRAIN"])
        assert all(T0 + 30000.0 <= r["decision_at"] < T0 + 45000.0
                   for r in s["parts"]["CALIB"])
        assert all(r["decision_at"] >= T0 + 45000.0
                   for r in s["parts"]["EVAL"])

    def test_a_condition_never_spans_two_parts(self):
        """TWELVE ENTRIES IN ONE MARKET ARE TWELVE VIEWS OF ONE EVENT.
        A split that cuts through a condition leaks."""
        f = []
        # one condition with entries either side of the boundary
        for i in range(6):
            f.append(fill("a", "shared", 0, T0 + i * 10000.0))
        ds = build(f, end=T0 + 200000.0)
        s = D.split_by_time(ds, train_end=T0 + 25000.0,
                            calib_end=T0 + 45000.0)
        seen = {p for p, rs in s["parts"].items()
                if any(r["group_key"] == "shared" for r in rs)}
        assert len(seen) == 1
        assert s["dropped_for_group_overlap"] > 0

    def test_the_split_reports_conditions_not_just_rows(self):
        ds = self._ds()
        s = D.split_by_time(ds, train_end=T0 + 30000.0,
                            calib_end=T0 + 45000.0)
        assert sum(s["conditions"].values()) > 0
        assert set(s["counts"]) == {"TRAIN", "CALIB", "EVAL"}


# ── what is not reconstructed ────────────────────────────────────────

class TestLifecycleHonesty:

    def test_every_row_says_fills_only(self):
        ds = build([fill("a", "c", 0, T0)])
        assert ds["lifecycle_observability"] == "FILLS_ONLY"
        assert all(r["lifecycle_observability"] == "FILLS_ONLY"
                   for r in ds["rows"])

    def test_the_dataset_names_the_mechanisms_it_cannot_see(self):
        ds = build([fill("a", "c", 0, T0)])
        w = ds["what_is_not_reconstructed"].lower()
        for word in ("merge", "redemption", "transfer", "cancellation"):
            assert word in w

    def test_a_non_binary_outcome_is_skipped_not_guessed(self):
        ds = build([fill("a", "c", 4, T0)])
        assert ds["n_rows"] == 0
        assert ds["skipped"]["unknown_outcome"] == 1

    def test_a_fill_without_a_condition_is_skipped(self):
        r = fill("a", None, 0, T0)
        ds = build([r])
        assert ds["skipped"]["no_condition"] == 1


def test_the_horizon_must_be_positive():
    with pytest.raises(ValueError):
        D.build([], horizon_s=0.0, observation_end=T0)

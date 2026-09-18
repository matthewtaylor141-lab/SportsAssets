"""The evidence gatherer: it fetches the facts, or names what it could not.

The preflight endpoint consumed whatever the caller typed in. That makes
an operator the source of the very facts the check exists to verify. This
module goes and reads them, and every test here is about what happens
when a read fails -- because the only interesting property is that a
missing fact becomes a named blocker rather than a plausible default.
"""

from __future__ import annotations

import pytest

from sportsassets import calibration as cal
from sportsassets import calibration_evidence as ce


def readers(**over):
    r = {
        "account": lambda: {"cash": 5000.0, "account": "bettortoken-main"},
        "open_orders": lambda m: [],
        "market": lambda m: {"slug": m, "outcome": "XXX to win",
                             "venue": "polymarket-us",
                             "expiry": "2026-09-18T23:00:00Z"},
        "book": lambda m: {"bid": 0.39, "ask": 0.40},
        "rules": lambda m: {"tick": 0.01, "minQuantity": 1},
        "fees": lambda m: {"entry": 0.20, "exit": 0.40,
                           "model": "venue schedule 2026-09"},
    }
    r.update(over)
    return r


MARKET = "aec-atp-sin-alc-2026-09-18"


def boom(*_a, **_k):
    raise RuntimeError("venue 503")


class TestEveryReadIsStampedAndCanFailOnItsOwn:
    def test_a_complete_gather_carries_a_timestamp_per_read(self):
        ev = ce.gather(MARKET, readers())
        assert ev["evidenceComplete"] is True
        for k in ("account", "openOrders", "market", "book", "rules", "fees"):
            assert ev[k]["at"].endswith("Z"), k
            assert ev[k]["ok"] is True, k

    @pytest.mark.parametrize("key,blocker", [
        ("account", ce.B_ACCOUNT),
        ("open_orders", ce.B_OPEN_ORDERS),
        ("market", ce.B_MARKET),
        ("book", ce.B_BOOK),
        ("rules", ce.B_TICK),
        ("fees", ce.B_FEES),
    ])
    def test_each_failed_read_becomes_a_named_blocker(self, key, blocker):
        ev = ce.gather(MARKET, readers(**{key: boom}))
        assert blocker in ev["evidenceBlockers"]
        assert ev["evidenceComplete"] is False

    def test_a_readable_but_empty_book_is_still_a_blocker(self):
        ev = ce.gather(MARKET, readers(book=lambda m: {"bid": 0.39,
                                                       "ask": None}))
        assert ce.B_BOOK in ev["evidenceBlockers"]

    def test_a_missing_minimum_quantity_is_named_separately(self):
        ev = ce.gather(MARKET, readers(rules=lambda m: {"tick": 0.01}))
        assert ce.B_MIN_QTY in ev["evidenceBlockers"]
        assert ce.B_TICK not in ev["evidenceBlockers"]

    def test_a_fee_schedule_missing_one_leg_is_a_blocker(self):
        ev = ce.gather(MARKET, readers(fees=lambda m: {"entry": 0.2,
                                                       "exit": None}))
        assert ce.B_FEES in ev["evidenceBlockers"]


class TestNothingIsInvented:
    def test_incomplete_evidence_proposes_no_ticket_at_all(self):
        ev = ce.gather(MARKET, readers(fees=boom))
        got = ce.propose(ev, cal.empty_session("S"))
        assert got["ticket"] is None
        assert ce.B_FEES in got["blockers"]
        assert "nothing is filled in" in got["why"]

    def test_the_gather_says_so_in_its_own_payload(self):
        ev = ce.gather(MARKET, readers())
        assert "never asked to supply a fact" in ev["nothingIsInvented"]


class TestTheSizeIsDerivedNotChosen:
    def test_the_quantity_is_the_largest_that_fits_the_five_dollar_cap(self):
        got = ce.propose(ce.gather(MARKET, readers()), cal.empty_session("S"))
        t = got["ticket"]
        # 5.00 - 0.20 - 0.40 = 4.40 of shares at 40c -> 11
        assert t["quantity"] == 11
        assert got["allInCost"] == 5.00
        assert got["allInCost"] <= cal.MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE

    def test_a_nearly_spent_session_shrinks_the_ticket(self):
        s = cal.empty_session("S")
        s["spent"] = 98.00                     # 2.00 left
        got = ce.propose(ce.gather(MARKET, readers()), s)
        assert got["ticket"]["quantity"] == 3   # (2.00-0.60)/0.40
        assert got["allInCost"] <= 2.00

    def test_a_market_whose_minimum_does_not_fit_is_refused_not_resized(self):
        """The limit is never raised to make a market fit."""
        ev = ce.gather(MARKET, readers(
            rules=lambda m: {"tick": 0.01, "minQuantity": 100}))
        got = ce.propose(ev, cal.empty_session("S"))
        assert cal.R_MIN_QUANTITY in got["blockers"]
        assert cal.MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE == 5.00

    def test_the_proposal_is_never_submittable_by_itself(self):
        got = ce.propose(ce.gather(MARKET, readers()), cal.empty_session("S"))
        assert got["submittable"] is False
        assert "needs a human approval" in got["why"]

    def test_the_ticket_carries_the_evidence_instant(self):
        ev = ce.gather(MARKET, readers())
        got = ce.propose(ev, cal.empty_session("S"))
        assert got["ticket"]["evidenceAsOf"] == ev["gatheredAt"]


class TestSubmissionIsNotReachable:
    def test_the_module_imports_no_order_creation_path(self):
        import pathlib
        src = pathlib.Path(ce.__file__).read_text()
        code = "\n".join(ln for ln in src.splitlines()
                          if not ln.strip().startswith("#"))
        code = code.split('"""', 2)[-1]          # drop the module docstring
        for forbidden in ("submit_fok", "orders.create", "calibration_execute",
                          "LiveVenue"):
            assert forbidden not in code, forbidden

    def test_there_is_no_submit_flag(self):
        """CODE, NOT PROSE. The first version of this test grepped the
        file and matched the docstring sentence saying there is no
        --submit flag -- reading commentary as behaviour, which is the
        same mistake as trusting a comment that says the row exists."""
        import argparse
        import inspect
        src = inspect.getsource(ce._cli)
        flags = [ln for ln in src.splitlines() if "add_argument" in ln]
        assert flags, "the CLI defines no arguments at all"
        assert not any("submit" in ln for ln in flags), flags
        assert isinstance(argparse.ArgumentParser(), argparse.ArgumentParser)

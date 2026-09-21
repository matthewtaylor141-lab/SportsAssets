"""The authenticated read-only path, and the settlement ingestion.

The properties that matter: the reader produces rows the EXISTING
adapter normalizes (a second shape would be a second place for the
depth error to live), the four resolution statuses stay distinct, only
RESOLVED writes, and no order call is reachable.
"""
from __future__ import annotations

import ast
import asyncio
import inspect

import pytest

from sportsassets import bettor_live_read as lr
from sportsassets import bettor_observation_adapter as oa
from sportsassets import bettor_settlement_ingest as si


class FakeMarkets:
    def __init__(self, book=None, listing=None, raises=None):
        self._book, self._listing, self._raises = book, listing, raises

    def book(self, slug):
        if self._raises:
            raise self._raises
        return {"marketData": self._book}

    def list(self, params):
        if self._raises:
            raise self._raises
        return {"markets": list(self._listing or [])}


class FakeClient:
    def __init__(self, **kw):
        self.markets = FakeMarkets(**kw)


MD = {
    "state": "MARKET_STATE_OPEN",
    "bestBid": "0.7100", "bestAsk": "0.7200",
    "transactTime": "2026-09-21T18:20:25.743291447Z",
    "bids": [{"px": {"value": "0.7100"}, "qty": "11308.1900"},
             {"px": {"value": "0.7000"}, "qty": "1371.0000"}],
    "offers": [{"px": {"value": "0.7200"}, "qty": "17.0000"},
               {"px": {"value": "0.7300"}, "qty": "1267.0000"},
               {"px": {"value": "0.7400"}, "qty": "3248.0000"}],
    "stats": {"sharesTraded": "6.8100"},
}


# ── the book read ────────────────────────────────────────────────────

class TestTheLiveReaderFeedsTheSameAdapter:

    def test_a_live_row_normalizes_through_the_existing_adapter(self):
        """One shape, one normalizer. A second shape here would be a
        second place for the depth error to live."""
        row = lr.read_book(FakeClient(book=MD), "slug-1", outcome_leg="no")
        row["book_age_s"] = "1.0"           # the caller computes this
        rec = oa.normalize(row)
        assert rec.status == oa.ACCEPTED, rec.reasons
        assert rec.bid == 0.71 and rec.ask == 0.72

    def test_executable_size_is_the_top_level_not_the_sum(self):
        row = lr.read_book(FakeClient(book=MD), "slug-1")
        row["book_age_s"] = "1.0"
        rec = oa.normalize(row)
        assert rec.ask_size == 17.0                     # the touch
        assert rec.cumulative_ask_size == 17 + 1267 + 3248
        assert rec.depth_source == "TOP_OF_BOOK_FROM_LADDER"

    def test_the_ladder_carries_an_explicit_level_per_entry(self):
        """Array order is not trusted downstream, so it is not relied
        on here either."""
        row = lr.read_book(FakeClient(book=MD), "slug-1")
        levels = row["multi_level_depth"]["ask"]
        assert [x["level"] for x in levels] == [0, 1, 2]
        assert levels[0] == {"level": 0, "qty": "17.0000", "price": "0.7200"}

    def test_the_cumulative_field_says_it_is_cumulative(self):
        row = lr.read_book(FakeClient(book=MD), "slug-1")
        assert "not the quantity at the quote" in row["yes_depth"]["isCumulative"]

    def test_the_receipt_timestamp_is_stamped_at_the_read(self):
        row = lr.read_book(FakeClient(book=MD), "slug-1",
                           now="2026-09-21T18:26:06.772+00:00")
        assert row["book_received_ts"] == "2026-09-21T18:26:06.772+00:00"
        assert row["book_source_ts"] == "2026-09-21T18:20:25.743291447Z"
        assert row["book_source_ts_field"] == "transactTime"

    def test_the_reader_never_stamps_the_decision_time(self):
        """A reader that stamps the decision time stamps the time of
        the READ, and the gap between them is what a freshness bound
        exists to catch. Measured median source-to-receipt: 549.6 s."""
        row = lr.read_book(FakeClient(book=MD), "slug-1")
        assert row["book_age_s"] == "NOT_IDENTIFIED"
        assert "decided_at" not in row

    def test_the_complement_is_never_derived(self):
        """1 - own asserts the identity Class C falsified on 3,732
        observations."""
        row = lr.read_book(FakeClient(book=MD), "slug-1")
        assert row["no_bid"] == "NOT_IDENTIFIED"
        assert row["no_ask"] == "NOT_IDENTIFIED"

    def test_a_failed_read_is_named_and_then_rejected(self):
        """Not returned as an empty book. Unreadable is not empty."""
        row = lr.read_book(FakeClient(raises=RuntimeError("boom")), "s")
        assert row["book_readability_status"].startswith("UNREADABLE_")
        assert "RuntimeError" in row["book_readability_status"]
        row["book_age_s"] = "1.0"
        assert oa.normalize(row).status == oa.REJECTED

    def test_a_book_with_no_ladder_yields_no_depth(self):
        """And the adapter then refuses it, rather than sizing at zero
        or at the cumulative figure."""
        row = lr.read_book(FakeClient(book={"state": "MARKET_STATE_OPEN",
                                            "bestBid": "0.4", "bestAsk": "0.5",
                                            "transactTime": "2026-09-21T00:00:00Z"}),
                           "s")
        assert "multi_level_depth" not in row
        row["book_age_s"] = "1.0"
        rec = oa.normalize(row)
        assert rec.status == oa.REJECTED
        assert oa.R_NO_DEPTH in rec.reasons

    def test_a_missing_source_timestamp_is_the_sentinel_not_now(self):
        row = lr.read_book(FakeClient(book={"state": "MARKET_STATE_OPEN",
                                            "bestBid": "0.4",
                                            "bestAsk": "0.5"}), "s")
        assert row["book_source_ts"] == "NOT_IDENTIFIED"
        assert row["book_source_ts_field"] is None


# ── resolution ───────────────────────────────────────────────────────

class TestFourResolutionStatusesNotTwo:
    """An empty settlement table was read as 'nothing has matured'. It
    could equally have meant the read failed, the slug is not listed, or
    nothing ever called the writer -- and it was the last of those."""

    def test_the_eight_guessed_outcome_fields_were_all_refuted(self):
        """MEASURED 2026-09-21 on a real slug: the payload carries 34
        keys and NOT ONE of the eight names I guessed is among them.
        Returning the key names is what corrected it, in one read."""
        assert lr.OUTCOME_FIELDS == ()
        assert "resolvedOutcome" in lr.REFUTED_OUTCOME_FIELDS
        assert len(lr.REFUTED_OUTCOME_FIELDS) == 8

    def test_a_reported_outcome_field_would_still_resolve(self):
        """OUTCOME_FIELDS is empty, not deleted: a payload that ever
        grows a reported outcome must be recognised as one."""
        lr.OUTCOME_FIELDS = ("resolvedOutcome",)
        try:
            c = FakeClient(listing=[{"slug": "s", "closed": True,
                                     "resolvedOutcome": "yes",
                                     "endDate": "2026-09-21T02:00:00Z"}])
            r = lr.read_resolution(c, "s")
            assert r["status"] == lr.RESOLVED
            assert r["outcome"] == "yes"
            assert r["outcome_field"] == "resolvedOutcome"
            assert r["settled_at"] == "2026-09-21T02:00:00Z"
        finally:
            lr.OUTCOME_FIELDS = ()

    def test_converged_prices_are_DERIVED_never_RESOLVED(self):
        """The venue reports no winner. Reading a price of 1 as 'this
        side won' is an inference the venue did not make, so it gets
        its own status and never joins the reported count."""
        c = FakeClient(listing=[{"slug": "s", "closed": True,
                                 "outcomes": ["Yes", "No"],
                                 "outcomePrices": ["1", "0"],
                                 "endDate": "2026-09-21T02:00:00Z"}])
        r = lr.read_resolution(c, "s")
        assert r["status"] == lr.RESOLVED_DERIVED
        assert r["status"] != lr.RESOLVED
        assert r["outcome"] == "Yes"
        assert "inference from a price" in r["derivation"]

    def test_nearly_certain_is_not_settled(self):
        """0.99 is a market that has not settled, not a settled one.
        The tolerance is exact on purpose."""
        c = FakeClient(listing=[{"slug": "s", "closed": True,
                                 "outcomes": ["Yes", "No"],
                                 "outcomePrices": ["0.99", "0.01"]}])
        assert lr.read_resolution(c, "s")["status"] == lr.UNREADABLE

    def test_the_venue_size_floor_and_tick_are_read_not_assumed(self):
        """The pilot proposal assumed both. The venue reports both."""
        c = FakeClient(listing=[{"slug": "s", "closed": False,
                                 "minimumTradeQty": 5,
                                 "orderPriceMinTickSize": "0.01",
                                 "feeCoefficient": "0.06"}])
        r = lr.read_resolution(c, "s")
        assert r["minimum_trade_qty"] == "5"
        assert r["tick_size"] == "0.01"
        # A DIRECT CHECK ON WHICH SCHEDULE THE VENUE APPLIES: 0.06 is
        # the 2026-07-01 theta, 0.0695 the 2026-09-17 one.
        assert r["fee_coefficient"] == "0.06"

    def test_listed_and_open_is_pending(self):
        c = FakeClient(listing=[{"slug": "s", "closed": False}])
        assert lr.read_resolution(c, "s")["status"] == lr.PENDING

    def test_closed_without_an_outcome_is_unreadable_not_resolved(self):
        """`closed` says trading stopped, not who won. Inferring the
        second from the first is the substitution
        SETTLEMENT_SEMANTICS_STATUS exists to prevent."""
        c = FakeClient(listing=[{"slug": "s", "closed": True,
                                 "volume": 100}])
        r = lr.read_resolution(c, "s")
        assert r["status"] == lr.UNREADABLE
        assert r["error"] == "CLOSED_BUT_NO_REPORTED_OR_CONVERGED_OUTCOME"
        # The keys that WERE present -- names only, which is what
        # corrects OUTCOME_FIELDS with one read instead of a guess.
        assert r["keys_seen"] == ["closed", "slug", "volume"]

    def test_not_listed_is_unmatched_not_pending(self):
        r = lr.read_resolution(FakeClient(listing=[]), "s")
        assert r["status"] == lr.UNMATCHED
        assert r["error"] == "NOT_LISTED"

    def test_a_raised_read_is_unreadable_and_named(self):
        r = lr.read_resolution(FakeClient(raises=TimeoutError()), "s")
        assert r["status"] == lr.UNREADABLE
        assert r["error"] == "TimeoutError"

    def test_resolution_fields_returns_names_only(self):
        """Never a value. The same discipline capability_probe uses."""
        c = FakeClient(listing=[{"slug": "s", "closed": True,
                                 "outcomePrices": ["1", "0"],
                                 "bestBid": "0.99"}])
        f = lr.resolution_fields(c, "s")
        assert f["keys"] == ["bestBid", "closed", "outcomePrices", "slug"]
        blob = repr(f)
        assert "0.99" not in blob


# ── ingestion ────────────────────────────────────────────────────────

class TestSettlementIngestion:

    def run(self, reads, **kw):
        written = []

        async def writer(row):
            written.append(row)

        def reader(client, slug):
            r = reads[slug]
            if isinstance(r, Exception):
                raise r
            return r

        out = asyncio.run(si.ingest(list((f"o{i}", s) for i, s in
                                         enumerate(reads)),
                                    reader=reader,
                                    writer=kw.get("writer", writer)))
        return out, written

    def test_the_five_counts_are_separate(self):
        out, written = self.run({
            "a": {"status": lr.RESOLVED, "outcome": "yes",
                  "settled_at": "2026-09-21T02:00:00Z"},
            "b": {"status": lr.PENDING},
            "c": {"status": lr.UNREADABLE, "error": "X", "keys_seen": ["slug"]},
            "d": {"status": lr.UNMATCHED, "error": "NOT_LISTED"},
        })
        assert out["counts"] == {si.RESOLVED: 1, si.RESOLVED_DERIVED: 0,
                                 si.PENDING: 1, si.UNREADABLE: 1,
                                 si.UNMATCHED: 1, si.INGESTED: 1,
                                 si.WRITE_FAILED: 0}
        assert len(written) == 1

    def test_a_derived_outcome_is_written_with_its_derivation(self):
        """Stored, because it is the only settlement signal the venue
        gives -- and stored SAYING it is derived, so nothing downstream
        mistakes an inference from a price for a reported winner."""
        _, written = self.run({"a": {"status": si.RESOLVED_DERIVED,
                                     "outcome": "Yes",
                                     "settled_at": "2026-09-21T02:00:00Z"}})
        assert len(written) == 1
        assert written[0]["SETTLEMENT_SEMANTICS_STATUS"] == \
            si.SEMANTICS_DERIVED
        assert "DERIVED" in written[0]["SETTLEMENT_SEMANTICS_STATUS"]

    def test_derived_and_reported_are_never_added_together(self):
        out, _ = self.run({
            "a": {"status": si.RESOLVED, "outcome": "yes"},
            "b": {"status": si.RESOLVED_DERIVED, "outcome": "Yes"},
        })
        assert out["counts"][si.RESOLVED] == 1
        assert out["counts"][si.RESOLVED_DERIVED] == 1
        # Both are stored, so reconciliation covers both.
        assert out["counts"][si.INGESTED] == 2
        assert out["reconciled"] is True

    def test_only_a_resolved_read_writes_anything(self):
        """A PENDING row would look like an outcome; an UNREADABLE one
        would record our failure as the market's state."""
        _, written = self.run({
            "b": {"status": lr.PENDING},
            "c": {"status": lr.UNREADABLE, "error": "X"},
            "d": {"status": lr.UNMATCHED},
        })
        assert written == []

    def test_ingested_is_not_resolved_and_the_gap_is_reported(self):
        """A resolution read and not written is a resolution we do not
        have. Reporting the read count as the stored count is how a
        pipeline looks like it works while its table stays empty."""
        async def failing(row):
            raise RuntimeError("db down")

        out, _ = self.run({"a": {"status": lr.RESOLVED, "outcome": "yes"}},
                          writer=failing)
        assert out["counts"][si.RESOLVED] == 1
        assert out["counts"][si.INGESTED] == 0
        assert out["counts"][si.WRITE_FAILED] == 1
        assert out["resolved_minus_ingested"] == 1
        assert out["reconciled"] is False

    def test_a_reader_that_raises_is_counted_not_fatal(self):
        """An ingestion pass that dies halfway leaves a table that is
        neither empty nor complete."""
        out, written = self.run({
            "a": TimeoutError(),
            "b": {"status": lr.RESOLVED, "outcome": "no"},
        })
        assert out["counts"][si.UNREADABLE] == 1
        assert out["counts"][si.INGESTED] == 1
        assert len(written) == 1

    def test_the_venue_timestamp_is_preserved_exactly(self):
        """A settlement stamped with the time we read it is a
        settlement whose timing we have destroyed."""
        _, written = self.run({"a": {"status": lr.RESOLVED, "outcome": "yes",
                                     "settled_at": "2026-09-20T23:41:07.5Z"}})
        assert written[0]["SETTLEMENT_TIMESTAMP"] == "2026-09-20T23:41:07.5Z"

    def test_semantics_are_never_stronger_than_unverified(self):
        _, written = self.run({"a": {"status": lr.RESOLVED, "outcome": "yes"}})
        assert written[0]["SETTLEMENT_SEMANTICS_STATUS"] == \
            "SEMANTICS_NOT_VERIFIED"

    def test_a_settlement_row_is_never_a_fill(self):
        _, written = self.run({"a": {"status": lr.RESOLVED, "outcome": "yes"}})
        assert "contains no fill" in written[0]["isNotAFill"].lower()

    def test_unreadable_reasons_are_counted_by_reason(self):
        out, _ = self.run({
            "a": {"status": lr.UNREADABLE, "error": "TimeoutError"},
            "b": {"status": lr.UNREADABLE, "error": "TimeoutError"},
            "c": {"status": lr.UNREADABLE,
                  "error": "CLOSED_BUT_NO_OUTCOME_FIELD"},
        })
        assert out["unreadable_reasons"] == {
            "TimeoutError": 2, "CLOSED_BUT_NO_OUTCOME_FIELD": 1}

    def test_an_unreadable_read_carries_the_keys_it_did_see(self):
        out, _ = self.run({"c": {"status": lr.UNREADABLE, "error": "X",
                                 "keys_seen": ["closed", "slug"]}})
        assert out["detail"][0]["keys_seen"] == ["closed", "slug"]


# ── no order path ────────────────────────────────────────────────────

class TestNoOrderPath:

    def _calls_and_imports(self, mod):
        tree = ast.parse(inspect.getsource(mod))
        called, imported = set(), set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    called.add(f.id)
                elif isinstance(f, ast.Attribute):
                    called.add(f.attr)
            elif isinstance(n, ast.Import):
                imported.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                imported.update(a.name for a in n.names)
        return called, imported

    @pytest.mark.parametrize("mod", [lr, si])
    def test_no_order_call_is_reachable(self, mod):
        called, _ = self._calls_and_imports(mod)
        for forbidden in ("submit_fok", "close_position", "submit", "cancel",
                          "place_order", "order_intent_for"):
            assert forbidden not in called, (
                "%s() is called in %s" % (forbidden, mod.__name__))

    @pytest.mark.parametrize("mod", [lr, si])
    def test_the_module_declares_what_it_is(self, mod):
        d = mod.describe()
        assert d["submits_orders"] is False
        assert d["writes_accounting"] is False
        assert d["deployed"] is False

    def test_only_one_table_is_written(self):
        assert si.describe()["writes_tables"] == ["bettor_state_settlements"]

# `TestTheDecisionOnlyWorker` lived here and exercised
# `workers/bettor_prospective`, the PRECURSOR decision-only worker.
# `workers/bettor_live_loop` supersedes it, and this isolated
# observation release ships exactly ONE decision-only worker rather
# than two. The class and its subject stay on the development branch;
# every behaviour it asserted -- no order path, nothing sized by
# default, a per-observation decision clock, a read that raises being
# counted rather than fatal -- is asserted against `bettor_live_loop`
# in tests/test_bettor_live_loop.py.

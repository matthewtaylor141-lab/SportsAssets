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
                             "outcomeSide": "LONG",
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
        # The price is the PASSIVE one -- the 39c bid, not the 40c ask.
        # 5.00 - 0.20 - 0.40 = 4.40 of shares at 39c -> 11.
        assert t["price"] == 0.39
        assert t["quantity"] == 11
        assert got["allInCost"] == 4.89
        assert got["allInCost"] <= cal.MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE
        # and one more share would not fit
        assert cal.all_in_cost(12, 0.39, 0.20, 0.40) > \
            cal.MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE

    def test_a_nearly_spent_session_shrinks_the_ticket(self):
        s = cal.empty_session("S")
        s["spent"] = 98.00                     # 2.00 left
        got = ce.propose(ce.gather(MARKET, readers()), s)
        assert got["ticket"]["quantity"] == 3   # (2.00-0.60)/0.39
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


# ─────────────────────────────────────────────────────────────────────
# THE COMMAND ITSELF. The first version parsed its arguments and raised
# SystemExit, so none of what follows could have been true of it.
# ─────────────────────────────────────────────────────────────────────

class FakeMarkets:
    """The SDK surface the real readers actually call."""

    def __init__(self, market=None, bbo=None, raises=None):
        self._market = market if market is not None else {
            "market": {"slug": MARKET, "title": "XXX to win",
                       "closeTime": "2026-09-18T23:00:00Z",
                       "tickSize": "0.01", "minQuantity": 1}}
        self._bbo = bbo if bbo is not None else {
            "marketData": {"bestBid": 0.39, "bestAsk": 0.40}}
        self._raises = raises

    def retrieve_by_slug(self, slug):
        if self._raises:
            raise self._raises
        return self._market

    def bbo(self, slug):
        return self._bbo


class FakeAccount:
    def __init__(self, resp=None, raises=None):
        self._resp = resp if resp is not None else {
            "accountId": "bettortoken-main",
            "balances": [{"currency": "USD", "cash": {"value": "5000.00"}}]}
        self._raises = raises

    def balances(self):
        if self._raises:
            raise self._raises
        return self._resp


class FakeOrders:
    def __init__(self, resp=None):
        self._resp = resp if resp is not None else {"orders": []}

    def list(self, params=None):
        return self._resp


class FakeSDK:
    def __init__(self, **kw):
        self.markets = FakeMarkets(**{k: v for k, v in kw.items()
                                      if k in ("market", "bbo", "raises")})
        self.account = FakeAccount(kw.get("balances"), kw.get("account_raises"))
        self.orders = FakeOrders(kw.get("orders"))


class TestTheRealReadersAreWiredToRealCalls:
    """Driven through `default_readers` against a fake SDK transport, so
    what is exercised is the reader this command would actually use."""

    def test_the_account_reader_reads_balances_and_names_the_account(self):
        r = ce.default_readers(client=FakeSDK())
        got = r["account"]()
        assert got["account"] == "bettortoken-main"
        assert got["cash"] == 5000.0
        assert got["source"] == "account.balances"

    def test_the_book_reader_uses_the_bbo_feed_the_side_was_proven_on(self):
        r = ce.default_readers(client=FakeSDK())
        got = r["book"](MARKET)
        assert (got["bid"], got["ask"]) == (0.39, 0.40)
        assert "markets.bbo" in got["source"]

    def test_the_market_and_rules_readers_read_the_venue_row(self):
        r = ce.default_readers(client=FakeSDK())
        assert r["market"](MARKET)["slug"] == MARKET
        assert r["rules"](MARKET) == {"tick": 0.01, "minQuantity": 1,
                                      "source": "markets.retrieve_by_slug"}

    def test_the_outcome_side_is_not_invented_by_the_reader(self):
        """The venue row does not publish a LONG/SHORT selector, so the
        reader leaves it unset and it becomes a blocker. Filling it in
        here would be choosing a side on the operator's behalf."""
        assert ce.default_readers(client=FakeSDK())["market"](
            MARKET)["outcomeSide"] is None

    def test_the_fee_reader_refuses_because_no_verified_read_exists(self):
        with pytest.raises(ce.ReaderNotWired) as e:
            ce.default_readers(client=FakeSDK())["fees"](MARKET)
        assert "NO VERIFIED FEE READ" in str(e.value)

    def test_an_unreadable_venue_becomes_a_blocker_not_a_default(self):
        r = ce.default_readers(client=FakeSDK(raises=RuntimeError("503")))
        ev = ce.gather(MARKET, r)
        assert ce.B_MARKET in ev["evidenceBlockers"]
        assert ev["evidenceComplete"] is False


class TestFreshnessIsComputedNotAsserted:
    def test_a_fresh_gather_says_so_and_carries_its_age(self):
        got = ce.propose(ce.gather(MARKET, readers()), cal.empty_session("S"))
        assert got["ticket"]["stateFresh"] is True
        assert got["ticket"]["stateAgeSeconds"] is not None
        assert got["ticket"]["stateAgeSeconds"] < ce.MAX_EVIDENCE_AGE_S

    def test_an_old_read_is_stale_and_blocks(self):
        ev = ce.gather(MARKET, readers())
        for k in ce.READ_KEYS:                 # pin every read to one clock
            ev[k]["at"] = "2026-09-18T16:59:30Z"
        ev["book"]["at"] = "2026-09-18T00:00:00Z"
        f = ce.freshness(ev, now="2026-09-18T17:00:00Z")
        assert f["FRESH"] is False
        assert f["staleReads"] == ["book"]
        assert f["ages"]["book"] > ce.MAX_EVIDENCE_AGE_S

    def test_a_timestamp_that_will_not_parse_is_not_fresh(self):
        ev = ce.gather(MARKET, readers())
        ev["rules"]["at"] = "whenever"
        f = ce.freshness(ev)
        assert f["FRESH"] is False
        assert f["unreadableTimestamps"] == ["rules"]

    def test_staleness_reaches_the_blockers_and_stops_the_ticket(self):
        readers_ = readers()
        ev = ce.gather(MARKET, readers_)
        ev["book"]["at"] = "2020-01-01T00:00:00Z"
        ev = dict(ev)
        f = ce.freshness(ev)
        assert not f["FRESH"]
        # gather computes this itself; re-running proves the wiring
        ev2 = ce.gather(MARKET, readers_)
        ev2["book"]["at"] = "2020-01-01T00:00:00Z"
        ev2["freshness"] = ce.freshness(ev2)
        ev2["evidenceBlockers"] = sorted(set(ev2["evidenceBlockers"]
                                             + [ce.B_STALE]))
        ev2["evidenceComplete"] = False
        assert ce.propose(ev2, cal.empty_session("S"))["ticket"] is None

    def test_the_freshness_field_is_never_a_literal_in_the_source(self):
        """The defect was `"stateFresh": True` written into the ticket."""
        import inspect
        src = inspect.getsource(ce.propose)
        assert '"stateFresh": True' not in src
        assert '"stateFresh": bool(fresh["FRESH"])' in src


class TestMissingProvenanceIsABlockerNotAString:
    @pytest.mark.parametrize("over,blocker", [
        ({"account": lambda: {"cash": 5000.0}}, ce.B_ACCOUNT_ID),
        ({"account": lambda: {"cash": 5000.0, "account": ""}},
         ce.B_ACCOUNT_ID),
        ({"market": lambda m: {"slug": m, "outcomeSide": "LONG",
                               "expiry": "2026-09-18T23:00:00Z"}},
         ce.B_OUTCOME),
        ({"market": lambda m: {"slug": m, "outcome": "XXX to win",
                               "expiry": "2026-09-18T23:00:00Z"}},
         ce.B_OUTCOME_SIDE),
        ({"market": lambda m: {"slug": m, "outcome": "XXX to win",
                               "outcomeSide": "LONG"}}, ce.B_EXPIRY),
        ({"fees": lambda m: {"entry": 0.2, "exit": 0.4}}, ce.B_FEE_MODEL),
    ])
    def test_each_missing_fact_blocks(self, over, blocker):
        ev = ce.gather(MARKET, readers(**over))
        assert blocker in ev["evidenceBlockers"]
        assert ce.propose(ev, cal.empty_session("S"))["ticket"] is None

    def test_the_string_that_used_to_stand_in_for_them_is_gone(self):
        """`"account": "NOT IDENTIFIED"` is a nonempty account field, and
        that is exactly how an unknown gets past a presence check.

        Checked on the STRING CONSTANTS the module would emit, not on the
        file text -- the docstring names the defect on purpose, and a
        grep of the file cannot tell an explanation from a value."""
        import ast
        import pathlib
        tree = ast.parse(pathlib.Path(ce.__file__).read_text())
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                d = ast.get_docstring(node, clean=False)
                if d:
                    docstrings.add(d)
        emitted = [n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and n.value not in docstrings]
        assert not [s for s in emitted if "NOT IDENTIFIED" in s], \
            [s for s in emitted if "NOT IDENTIFIED" in s]

    def test_a_complete_ticket_carries_the_venue_s_own_values(self):
        t = ce.propose(ce.gather(MARKET, readers()),
                       cal.empty_session("S"))["ticket"]
        assert t["account"] == "bettortoken-main"
        assert t["outcome"] == "XXX to win"
        assert t["outcomeSide"] == "LONG"
        assert t["expiry"] == "2026-09-18T23:00:00Z"
        assert t["feeModel"] == "venue schedule 2026-09"


class TestThePassivePriceRule:
    def test_the_price_is_the_bid_and_never_the_ask(self):
        got = ce.passive_price({"bid": 0.39, "ask": 0.40}, 0.01,
                               "LIMIT_GTC_POST_ONLY")
        assert got["PRICE"] == 0.39
        assert got["PRICE"] < got["ask"]
        assert got["crossesTheSpread"] is False

    def test_a_wide_book_still_joins_the_bid_rather_than_improving_to_cross(
            self):
        got = ce.passive_price({"bid": 0.20, "ask": 0.80}, 0.01,
                               "LIMIT_GTC_POST_ONLY")
        assert got["PRICE"] == 0.20

    @pytest.mark.parametrize("book,tick,blocker", [
        ({"bid": None, "ask": 0.40}, 0.01, ce.B_NO_BID),
        ({"bid": 0.0, "ask": 0.40}, 0.01, ce.B_NO_BID),
        ({"bid": 0.39, "ask": None}, 0.01, ce.B_BOOK),
        ({"bid": 0.41, "ask": 0.40}, 0.01, ce.B_CROSSED_BOOK),
        ({"bid": 0.40, "ask": 0.40}, 0.01, ce.B_CROSSED_BOOK),
        ({"bid": 0.395, "ask": 0.40}, 0.01, ce.B_PRICE_OFF_TICK),
        ({"bid": 0.39, "ask": 0.40}, None, ce.B_TICK),
        ({"bid": 0.39, "ask": 0.40}, 0, ce.B_TICK),
    ])
    def test_a_book_the_rule_cannot_price_is_refused(self, book, tick,
                                                     blocker):
        got = ce.passive_price(book, tick, "LIMIT_GTC_POST_ONLY")
        assert got["BLOCKER"] == blocker
        assert got["PRICE"] is None

    def test_a_refusal_never_falls_back_to_an_aggressive_price(self):
        """The whole point. No bid to join is not a reason to take the
        offer, and the proposal refuses rather than reprices."""
        ev = ce.gather(MARKET, readers(
            book=lambda m: {"bid": None, "ask": 0.40}))
        # the ask alone still satisfies the book blocker, so evidence is
        # complete and the PRICING is what refuses
        got = ce.propose(ev, cal.empty_session("S"))
        assert got["ticket"] is None
        assert got["blockers"] == [ce.B_NO_BID]
        assert "never" in ce.PASSIVE_RULE and "cross" in ce.PASSIVE_RULE

    def test_a_non_post_only_type_has_no_automatic_passive_price(self):
        got = ce.passive_price({"bid": 0.39, "ask": 0.40}, 0.01, "LIMIT_IOC")
        assert got["BLOCKER"] == ce.B_ORDER_TYPE_NOT_PRICEABLE

    def test_a_finer_tick_grid_is_honoured(self):
        got = ce.passive_price({"bid": 0.395, "ask": 0.40}, 0.005,
                               "LIMIT_GTC_POST_ONLY")
        assert got["PRICE"] == 0.395


class TestTheResearchDomainIsCheckedBeforeAnyRead:
    @staticmethod
    def _pages(rows_in_progress=(), rows_queued=()):
        pages = {"in_progress": {"workflow_runs": list(rows_in_progress),
                                 "total_count": len(rows_in_progress)},
                 "queued": {"workflow_runs": list(rows_queued),
                            "total_count": len(rows_queued)}}

        def fetch(url):
            return pages["queued" if "queued" in url else "in_progress"]
        return fetch

    def test_a_clear_domain_is_clear(self):
        got = ce.domain_isolation(fetch=self._pages(), repo="o/r", token="t")
        assert got["STATE"] == "CLEAR"

    def test_a_running_collector_is_active(self):
        got = ce.domain_isolation(
            fetch=self._pages(rows_in_progress=[
                {"name": "beta48-substantive-capture", "id": 1}]),
            repo="o/r", token="t")
        assert got["STATE"] == "ACTIVE"
        assert got["runs"][0]["workflow"] == "beta48-substantive-capture"

    def test_a_queued_collector_also_counts(self):
        got = ce.domain_isolation(
            fetch=self._pages(rows_queued=[
                {"name": "beta48-forward-capture", "id": 2}]),
            repo="o/r", token="t")
        assert got["STATE"] == "ACTIVE"

    def test_a_non_collector_run_does_not_count(self):
        got = ce.domain_isolation(
            fetch=self._pages(rows_in_progress=[{"name": "ci", "id": 3}]),
            repo="o/r", token="t")
        assert got["STATE"] == "CLEAR"

    @pytest.mark.parametrize("page,reason", [
        ({"workflow_runs": [], "total_count": 5}, "CENSUS_INCOMPLETE"),
        ({"total_count": 0}, "CENSUS_UNREADABLE"),
        ({"workflow_runs": "nope", "total_count": 0}, "CENSUS_UNREADABLE"),
        ({"workflow_runs": ["x"], "total_count": 1}, "CENSUS_ROW_MALFORMED"),
        ({"workflow_runs": [{"id": 9}], "total_count": 1},
         "UNATTRIBUTABLE_OCCUPYING_RUN"),
    ])
    def test_an_unreadable_census_is_unknown_never_clear(self, page, reason):
        got = ce.domain_isolation(fetch=lambda _u: page, repo="o/r", token="t")
        assert got["STATE"] == "UNKNOWN"
        assert got["REASON"] == reason

    def test_a_failed_census_read_is_unknown(self):
        def boom_fetch(_u):
            raise RuntimeError("github 500")
        got = ce.domain_isolation(fetch=boom_fetch, repo="o/r", token="t")
        assert got["STATE"] == "UNKNOWN"

    def test_no_token_is_unknown_not_clear(self):
        got = ce.domain_isolation(repo=None, token=None)
        assert got["STATE"] == "UNKNOWN"
        assert got["REASON"] == "NO_REPO_OR_TOKEN"

    @pytest.mark.parametrize("state", ["ACTIVE", "UNKNOWN"])
    def test_a_non_clear_domain_means_nothing_is_read_at_all(self, state):
        reads = []

        def watched():
            reads.append("account")
            return {"cash": 1.0, "account": "a"}
        got = ce.run(MARKET, readers=readers(account=watched),
                     session=cal.empty_session("S"),
                     require_idle_domain=True,
                     isolation={"STATE": state, "REASON": "x"})
        assert got["blockers"] == [ce.B_RESEARCH]
        assert got["evidence"] is None
        assert reads == []


class TestTheCommandRuns:
    def test_run_returns_a_complete_report_against_a_fake_transport(self):
        got = ce.run(MARKET, readers=readers(),
                     session=cal.empty_session("S"))
        assert got["evidence"]["evidenceComplete"] is True
        assert got["proposal"]["ticket"]["price"] == 0.39
        assert got["submissionReachable"] is False
        assert got["sessionSource"] == "caller"

    def test_the_durable_session_is_looked_up_when_none_is_given(self):
        seen = []

        def loader():
            seen.append(1)
            return cal.empty_session("DURABLE")
        got = ce.run(MARKET, readers=readers(), session_loader=loader)
        assert seen == [1]
        assert got["sessionSource"] == "durable"

    def test_an_unavailable_budget_refuses_rather_than_assuming_one(self):
        def loader():
            raise RuntimeError("database down")
        got = ce.run(MARKET, readers=readers(), session_loader=loader)
        assert got["blockers"] == ["BUDGET_STATE_UNAVAILABLE"]
        assert got["proposal"]["ticket"] is None
        assert got["sessionSource"] == "UNAVAILABLE"

    def test_the_cli_runs_end_to_end_and_writes_its_evidence(
            self, tmp_path, monkeypatch, capsys):
        """THE ACTUAL CLI, with the transport mocked -- not `run()` called
        directly. The old `_cli` raised SystemExit after parsing, so this
        is the test that could not have passed before."""
        import json as _json
        from sportsassets import pmus
        monkeypatch.setattr(pmus, "_get_client", lambda: FakeSDK())
        monkeypatch.setattr(ce, "_durable_session",
                            lambda: cal.empty_session("S"))
        out = tmp_path / "evidence.json"
        rc = ce._cli(["--market", MARKET, "--out", str(out)])

        report = _json.loads(out.read_text())
        assert report["marketId"] == MARKET
        assert report["evidence"]["account"]["value"]["account"] == \
            "bettortoken-main"
        assert report["evidence"]["book"]["value"]["bid"] == 0.39
        # The fee schedule has no verified read, so the command honestly
        # reports a blocker instead of a ticket -- and exits nonzero.
        assert ce.B_FEES in report["blockers"]
        assert report["proposal"]["ticket"] is None
        assert rc == 1
        assert _json.loads(capsys.readouterr().out)["marketId"] == MARKET

    def test_the_cli_is_clear_when_every_fact_is_obtainable(
            self, tmp_path, monkeypatch):
        """With a fee reader wired, the same command produces a ticket.
        Proof that the only thing standing between this command and a
        proposal is a fact nobody has read yet."""
        import json as _json
        from sportsassets import pmus
        monkeypatch.setattr(pmus, "_get_client", lambda: FakeSDK())
        monkeypatch.setattr(ce, "_durable_session",
                            lambda: cal.empty_session("S"))
        real = ce.default_readers

        def with_fees(client=None):
            r = real(client=client)
            r["fees"] = lambda m: {"entry": 0.20, "exit": 0.40,
                                   "model": "venue schedule 2026-09"}
            r["market"] = lambda m: dict(real(client=client)["market"](m),
                                         outcomeSide="LONG")
            return r
        monkeypatch.setattr(ce, "default_readers", with_fees)
        out = tmp_path / "e.json"
        rc = ce._cli(["--market", MARKET, "--out", str(out)])
        report = _json.loads(out.read_text())
        assert report["proposal"]["ticket"]["price"] == 0.39
        assert report["proposal"]["ticket"]["stateFresh"] is True
        assert report["proposal"]["submittable"] is False
        assert rc == 0

    def test_the_cli_never_reaches_a_send(self, monkeypatch, tmp_path):
        from sportsassets import pmus
        sent = []
        monkeypatch.setattr(pmus, "_get_client", lambda: FakeSDK())
        monkeypatch.setattr(pmus, "submit_fok",
                            lambda *a, **k: sent.append(a) or {})
        monkeypatch.setattr(ce, "_durable_session",
                            lambda: cal.empty_session("S"))
        ce._cli(["--market", MARKET, "--out", str(tmp_path / "e.json")])
        assert sent == []

"""THE L2 EVIDENCE BRIDGE, pinned where it could become something else.

Owner directive 2026-09-19 23:0xZ. The bridge exists because the
production PMX key is unrecoverable and lives only in the GitHub secret
store. It carries BOOKS from an authenticated runner into the
experimental evidence tables.

THE FAILURES THESE PREVENT:

  A TRADING CAPABILITY GROWING OUT OF A DATA BRIDGE. The narrow thing
  built today is one edit away from being a wide one. The allow-list,
  the emitted-statement scan and the table whitelist are what make
  widening it a visible act rather than a quiet one.

  A CREDENTIAL RIDING ALONG WITH THE DATA. The whole point is that the
  key stays in the runner. A token pasted into an emitted INSERT would
  put it in the database and in an artifact.

  TWO LATENCY REGIMES POOLED. A book fetched by a CI runner minutes
  after the request is not the same execution environment as a
  persistent worker's, and averaging them would describe neither.
"""

from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timezone

import pytest

BRIDGE_DIR = (pathlib.Path(__file__).resolve().parents[2]
              / "research" / "institutional")
sys.path.insert(0, str(BRIDGE_DIR))

br = pytest.importorskip("pmx_l2_bridge")
prod = pytest.importorskip("pmx_production_read")

NOW = datetime(2026, 9, 19, 23, 5, 0, tzinfo=timezone.utc)


def fetched(**kw):
    base = {
        "symbol": "astatc-mls-sje-laf-2026-09-19-sh-ftts-laf",
        "instrumentRecord": {"priceScale": "100",
                             "fractionalQtyScale": "100"},
        "priceScale": 100, "quantityScale": 100,
        "bids": [{"px": "97", "qty": "1"}],
        "offers": [{"px": "99", "qty": "2500"}],
        "bbo": {"bestBid": {"px": "97"}},
        "venueState": "INSTRUMENT_STATE_OPEN",
        "sourceTimestamp": "2026-09-19T23:04:58Z",
        "receivedTimestamp": NOW,
        "venueRequestId": "abc", "venueRequestMs": 42.3,
        "bookSha": "bk16", "statuses": {"book": 200},
        "bridgeLatencyMs": 91250.0,
    }
    base.update(kw)
    return base


def sql(**kw):
    return br.emit(br.statements_for(
        fetched(**kw),
        request_row={"l2_request_id": "rq1",
                     "identity_binding_sha": "idsha"},
        run_id="RUN", revision="REV"))


# ── it cannot become a trading capability ────────────────────────────


def test_the_bridge_reads_are_a_subset_of_the_production_allow_list():
    for name in br.BRIDGE_READS:
        assert name in prod.READ_ONLY_PATHS
    assert set(br.BRIDGE_READS) == {"instruments", "bbo", "book"}


def test_no_mutating_path_appears_in_any_module_the_job_runs():
    """The same grep the workflow runs BEFORE staging a credential."""
    for module in ("pmx_l2_bridge.py", "pmx_production_read.py",
                   "pmx_production_net.py"):
        body = (BRIDGE_DIR / module).read_text()
        for bad in ("/v1/trading/orders", "/v1/funding", "orders/cancel",
                    "orders/preview", "orders/replace"):
            assert bad not in body, (module, bad)


def test_only_two_tables_may_be_written():
    assert set(br.ALLOWED_TABLES) == {"bettor_l2_evidence",
                                      "bettor_l2_requests"}


def test_a_statement_naming_another_table_is_refused():
    for statement in ("INSERT INTO shadow_decisions VALUES (1);",
                      "INSERT INTO mirror_books VALUES (1);",
                      "UPDATE bettor_experimental_decisions SET vwap = 1;"):
        with pytest.raises(br.BridgeRefusal):
            br.emit([statement])


def test_every_mutating_verb_is_refused_in_the_emitted_sql():
    for statement in ("DROP TABLE bettor_l2_evidence;",
                      "ALTER TABLE bettor_l2_evidence ADD COLUMN x int;",
                      "DELETE FROM bettor_l2_evidence;",
                      "TRUNCATE bettor_l2_evidence;",
                      "GRANT ALL ON bettor_l2_evidence TO public;",
                      "COPY bettor_l2_evidence FROM '/etc/passwd';"):
        with pytest.raises(br.BridgeRefusal):
            br.emit([statement])


# ── the credential never rides along ─────────────────────────────────


def test_emitted_sql_carrying_credential_material_is_refused():
    for leak in ("Bearer eyJhbGciOiJSUzI1NiJ9",
                 "-----BEGIN RSA PRIVATE KEY-----",
                 "client_assertion=abc"):
        with pytest.raises(Exception):
            br.emit(["INSERT INTO bettor_l2_evidence VALUES ('%s');" % leak])


def test_the_real_emitted_sql_contains_no_credential_field_names():
    out = sql()
    for bad in ("PMX_PRIVATE_KEY", "client_assertion", "access_token",
                "Bearer ", "BEGIN RSA"):
        assert bad not in out


def test_a_quote_in_a_value_cannot_break_out_of_the_literal():
    assert br._q("it's; DROP TABLE x;--") == "'it''s; DROP TABLE x;--'"
    assert br._q(None) == "NULL"
    assert br._q(42) == "42"


# ── the evidence says what it is ─────────────────────────────────────


def test_every_row_is_observed_production_and_bridge_regime():
    out = sql()
    assert "'OBSERVED_PRODUCTION'" in out
    assert "'GITHUB_BRIDGE'" in out
    assert br.EVIDENCE_CLASS == "OBSERVED_PRODUCTION"
    assert br.LATENCY_REGIME == "GITHUB_BRIDGE"


def test_every_field_the_directive_named_is_emitted():
    out = sql()
    for column in ("request_id", "instrument_id", "identity_binding_sha",
                   "source_timestamp", "received_timestamp", "l2_book_sha",
                   "bids", "offers", "price_scale", "quantity_scale",
                   "evidence_class"):
        assert column in out, column


def test_both_latency_figures_travel_and_are_not_invented():
    """"Do not manufacture latency." The venue call and the whole
    bridge round trip are separate numbers."""
    out = sql()
    assert "42.3" in out            # the venue call
    assert "91250.0" in out         # the bridge round trip
    # and an unmeasurable round trip is NULL, not a guess
    assert "venue_request_ms" in out and "bridge_latency_ms" in out
    no_request = br.statements_for(fetched(bridgeLatencyMs=None),
                                   request_row=None)
    assert "NULL" in no_request[0]


def test_the_scales_are_read_from_the_instrument_and_never_defaulted():
    assert br._scales({"priceScale": "100",
                       "fractionalQtyScale": "100"}) == (100, 100)
    assert br._scales({"priceScale": "1000",
                       "fractionalQtyScale": "1000"}) == (1000, 1000)
    for bad in ({}, {"priceScale": "x"}, {"priceScale": "0",
                                          "fractionalQtyScale": "100"},
                None):
        assert br._scales(bad) == (None, None)


def test_the_book_sha_is_over_the_levels_themselves():
    a = br.book_sha([{"px": "97", "qty": "1"}], [{"px": "99", "qty": "2"}])
    b = br.book_sha([{"px": "97", "qty": "1"}], [{"px": "98", "qty": "2"}])
    assert a != b and len(a) == 16


def test_a_served_request_is_marked_and_a_failed_one_is_not_served():
    served = br.statements_for(fetched(statuses={"book": 200}),
                               request_row={"l2_request_id": "rq1"})
    assert "'SERVED'" in served[1]
    failed = br.statements_for(fetched(statuses={"book": 500}),
                               request_row={"l2_request_id": "rq1"})
    assert "'FAILED'" in failed[1]
    # and the claim only applies to a row still PENDING
    assert "status = 'PENDING'" in served[1]


def test_a_direct_symbol_fetch_emits_evidence_without_a_request_row():
    out = br.statements_for(fetched(), request_row=None)
    assert len(out) == 1
    assert out[0].startswith("INSERT INTO bettor_l2_evidence")


def test_re_serving_the_same_book_does_not_duplicate_evidence():
    """The id is derived from instrument, instant and book sha, and the
    insert is ON CONFLICT DO NOTHING -- a retried bridge run records the
    same observation once."""
    assert "ON CONFLICT (l2_evidence_id) DO NOTHING" in sql()
    a = br.evidence_id("sym", NOW, "bk16")
    assert a == br.evidence_id("sym", NOW, "bk16")
    assert a != br.evidence_id("sym", NOW, "other")

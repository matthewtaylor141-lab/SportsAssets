#!/usr/bin/env python3
"""Offline tests for the Run 85 Phase 2B repaired calibration. Contacts nothing.

The normalization tests run against research/fixtures/run85_pmus_book_fixture.json
-- a VERBATIM Phase 2A gateway response with only the slug anonymized. That
matters: Phase 2A's analyzer passed its own hand-written fixtures and still read
every real book as empty, because the fixtures were written to the SDK's
TypedDict and the venue does not send that shape. A fixture that normalises the
thing it is meant to test proves nothing, so this one keeps the envelope, the
{value, currency} prices and the four-decimal strings exactly as they arrived.

Run:  python3 -m pytest research/test_run85_phase2b.py -q
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

SRC = Path(__file__).with_name("run85_phase2b.py")
_spec = importlib.util.spec_from_file_location("p2b", SRC)
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "run85_pmus_book_fixture.json").read_text())
BODY = FIXTURE["body"]


# ------------------------------------------------ wire schema, real payload
def test_the_fixture_is_the_shape_the_venue_actually_sends():
    """Guards the guard: if someone 'tidies' the fixture into the SDK shape,
    every other test here would start passing for the wrong reason."""
    assert "marketData" in BODY
    assert isinstance(BODY["marketData"]["bids"][0]["px"], dict)
    assert BODY["marketData"]["bids"][0]["px"]["currency"] == "USD"


def test_market_data_unwraps_the_envelope():
    d = P.market_data(BODY)
    assert d["marketSlug"] == "anon-league-champ-2026-01-01-aaa"
    assert "bids" in d and "offers" in d


def test_market_data_also_accepts_a_flat_body():
    """A venue change to the SDK's declared shape must degrade loudly, not
    silently return nothing."""
    flat = dict(BODY["marketData"])
    assert P.market_data(flat)["marketSlug"] == flat["marketSlug"]


def test_amount_reads_the_object_form_and_keeps_the_currency():
    v, cur = P.amount({"value": "0.0010", "currency": "USD"})
    assert v == Decimal("0.0010") and cur == "USD"


def test_amount_reads_a_bare_scalar_too():
    v, cur = P.amount("0.25")
    assert v == Decimal("0.25") and cur is None


def test_amount_returns_none_rather_than_guessing():
    assert P.amount(None) == (None, None)
    assert P.amount({"value": "not-a-number"})[0] is None


def test_ladder_drops_unusable_rows_without_dropping_good_ones():
    lv = P.ladder([{"px": {"value": "0.1"}, "qty": "5"},
                   {"px": None, "qty": "9"},
                   {"px": {"value": "0.2"}, "qty": "bad"},
                   "not-a-dict"])
    assert len(lv) == 1 and lv[0][0] == Decimal("0.1")


def test_book_view_reads_the_real_payload_end_to_end():
    v = P.book_view(BODY)
    assert v["state"] == "MARKET_STATE_OPEN"
    assert v["transact_time"]
    assert v["bid_levels"] == 1 and v["ask_levels"] == 21
    assert v["best_bid"] == Decimal("0.0010")
    assert v["best_ask"] == Decimal("0.0020")
    assert v["spread"] == Decimal("0.0010")
    assert v["mid"] == Decimal("0.0015")
    assert v["currencies"] == ["USD"]
    assert "lastTradePx" in v["stats_keys"]


def test_book_view_measures_touch_depth_separately_from_total_depth():
    v = P.book_view(BODY)
    assert v["bid_qty_at_touch"] == Decimal("65.0000")
    assert v["bid_depth_usd"] < v["ask_depth_usd"]   # the Phase 2A asymmetry


def test_book_view_on_a_one_sided_book_returns_none_not_zero():
    one = {"marketData": {"bids": [], "offers": [
        {"px": {"value": "0.5", "currency": "USD"}, "qty": "10"}]}}
    v = P.book_view(one)
    assert v["best_bid"] is None and v["spread"] is None and v["mid"] is None


# ------------------------------------------------------------ throttling
def test_a_429_widens_the_spacing():
    p = P.AdaptivePacer(base=2.0)
    p.on_429(None)
    assert p.spacing == 4.0


def test_backoff_is_capped():
    p = P.AdaptivePacer(base=2.0)
    for _ in range(20):
        p.on_429(None)
    assert p.spacing == P.MAX_SPACING_S


def test_recovery_decays_and_never_snaps_back():
    """The specification is explicit: do not immediately return to maximum rate
    after a 429."""
    p = P.AdaptivePacer(base=2.0)
    p.on_429(None)
    p.on_success()
    assert 2.0 < p.spacing < 4.0
    for _ in range(200):
        p.on_success()
    assert p.spacing == 2.0          # floors at base, never below


def test_the_pacer_records_every_throttle_event():
    p = P.AdaptivePacer(base=2.0)
    p.on_429("3")
    assert p.events[0]["retry_after"] == "3"
    assert p.events[0]["slept_s"] == 3.0
    assert p.events[0]["spacing_after"] == 4.0


def test_an_unparseable_retry_after_does_not_crash_or_fabricate_a_wait():
    p = P.AdaptivePacer(base=2.0)
    p.on_429("soon")
    assert p.events[0]["slept_s"] is None
    assert p.spacing == 4.0


def test_the_aggregate_ceiling_is_half_a_request_per_second():
    assert P.BASE_SPACING_S == 2.0
    assert 1.0 / P.BASE_SPACING_S == 0.5


# -------------------------------------------------------------- selection
def test_binary_sides_accepts_exactly_one_long_and_one_short():
    sides, why = P.binary_sides({"marketSides": [
        {"identifier": "m", "long": True}, {"identifier": "m", "long": False}]})
    assert sides is not None and "one long one short" in why


def test_binary_sides_refuses_a_three_sided_market():
    sides, why = P.binary_sides({"marketSides": [
        {"long": True}, {"long": False}, {"long": True}]})
    assert sides is None and "marketSides=3" in why


def test_binary_sides_refuses_a_market_with_no_sides_array():
    assert P.binary_sides({})[0] is None


def test_structured_sport_never_matches_free_text():
    """Run 83.6E.1's Oprah false positive must be impossible by construction."""
    prov, _ = P.structured_sport(
        {"slug": "oprah", "description": "will Oprah beat the epl record"},
        {"title": "Oprah", "description": "epl"})
    assert prov == "NOT_IDENTIFIED"


def test_structured_sport_reads_fields_as_fields():
    assert P.structured_sport({"team": {"league": "MLB"}}, None)[0] \
        == "STRUCTURED_TEAM_METADATA"
    assert P.structured_sport({"gameId": 7}, None)[0] \
        == "STRUCTURED_SPORTS_METADATA"
    assert P.structured_sport({}, {"series": {"slug": "mlb-2026"}})[0] \
        == "STRUCTURED_SERIES"
    assert P.structured_sport({}, {"tags": [{"slug": "baseball"}]})[0] \
        == "STRUCTURED_TAG"


# ---------------------------------------------------------------- sealing
def _mkdir(name):
    d = Path("/tmp") / name
    if d.exists():
        for f in d.iterdir():
            f.unlink()
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_seal_writes_the_report_then_hashes_it_and_self_verifies():
    d = _mkdir("r85b_seal")
    (d / "book_samples.jsonl").write_text('{"a":1}\n')
    P.seal(d, ["line one", "line two"])
    ok, bad = P.verify(d)
    assert ok and not bad
    # the report's recorded digest is of its REAL content, not of an empty file
    text = (d / "checksums.sha256").read_text()
    want = dict(l.split("  ", 1)[::-1] for l in text.strip().splitlines())
    real = hashlib.sha256((d / "run85_terminal_report.txt").read_bytes()).hexdigest()
    assert want["run85_terminal_report.txt"] == real
    assert real != hashlib.sha256(b"").hexdigest()


def test_a_post_seal_change_to_the_report_fails_verification():
    """The mandated tamper test. Phase 2A shipped a checksum that could never
    pass because the file was still being written when it was hashed; this
    proves the digest now actually binds the bytes."""
    d = _mkdir("r85b_tamper_report")
    (d / "book_samples.jsonl").write_text('{"a":1}\n')
    P.seal(d, ["original"])
    assert P.verify(d)[0]
    (d / "run85_terminal_report.txt").write_text("tampered\n")
    ok, bad = P.verify(d)
    assert not ok and bad == ["run85_terminal_report.txt"]


def test_a_post_seal_change_to_the_evidence_fails_verification():
    d = _mkdir("r85b_tamper_evidence")
    (d / "book_samples.jsonl").write_text('{"a":1}\n')
    P.seal(d, ["x"])
    (d / "book_samples.jsonl").write_text('{"a":2}\n')
    ok, bad = P.verify(d)
    assert not ok and bad == ["book_samples.jsonl"]


def test_a_single_flipped_byte_is_caught():
    d = _mkdir("r85b_tamper_byte")
    (d / "book_samples.jsonl").write_text("aaaa\n")
    P.seal(d, ["x"])
    (d / "book_samples.jsonl").write_text("aaab\n")
    assert not P.verify(d)[0]


def test_checksums_file_covers_every_evidence_file_and_not_itself():
    d = _mkdir("r85b_cover")
    for n in ("book_samples.jsonl", "request_log.jsonl", "selection.json"):
        (d / n).write_text("{}\n")
    P.seal(d, ["x"])
    names = {l.split("  ", 1)[1]
             for l in (d / "checksums.sha256").read_text().strip().splitlines()}
    assert names == {"book_samples.jsonl", "request_log.jsonl", "selection.json",
                     "run85_terminal_report.txt"}


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(vars().items())
           if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in fns:
        try:
            fn()
        except Exception as exc:                      # noqa: BLE001
            failed.append((name, exc))
    for name, exc in failed:
        print("FAIL %s: %r" % (name, exc))
    print("%d passed, %d failed" % (len(fns) - len(failed), len(failed)))
    sys.exit(1 if failed else 0)

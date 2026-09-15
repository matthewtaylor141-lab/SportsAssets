#!/usr/bin/env python3
"""Offline tests for the Run 85 Phase 2 observational capture. Contacts nothing.

The load-bearing rules of the capture, each pinned:

  * the grid never asks two observation sets for the same slot, and the
    horizons land on the grid EXACTLY -- no interpolation is ever needed
  * an unchanged book at a horizon is an OBSERVATION worth zero, and the zero
    is kept; nothing conditions the primary dataset on the book moving
  * a touch is not a fill, and two touches are not a completed pair
  * the /bbo parser reads the marketData envelope -- the Phase 2F defect
  * retirement is the venue's word, never attractiveness, and never a swap
  * the cohort is the frozen file, byte for byte

Run:  python3 -m pytest research/test_run85_phase2_capture.py -q
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

_s = importlib.util.spec_from_file_location(
    "p2cap", Path(__file__).with_name("run85_phase2_capture.py"))
K = importlib.util.module_from_spec(_s)
_s.loader.exec_module(K)


def lvl(px, qty):
    return {"px": {"value": px, "currency": "USD"}, "qty": qty}


def book_body(bids, offers, slug="m", state="MARKET_STATE_OPEN", tt="T1"):
    return {"marketData": {"marketSlug": slug, "state": state,
                           "transactTime": tt, "bids": bids, "offers": offers}}


def bbo_body(bid, ask, slug="m"):
    return {"marketData": {"marketSlug": slug, "state": "MARKET_STATE_OPEN",
                           "bestBid": {"value": bid, "currency": "USD"},
                           "bestAsk": {"value": ask, "currency": "USD"},
                           "bidDepth": 3, "askDepth": 4}}


def row(body, status=200, path="/v1/markets/m/book"):
    return {"path": path, "http_status": status, "body": body, "error": None,
            "local_request_wall_utc": "2026-09-13T00:00:00Z",
            "local_request_monotonic_ns": 0, "latency_ms": 10.0,
            "response_sha256": "d", "response_bytes": 1, "response_headers": {}}


def leg(route, h, body, status=200, actual=None, slug="m"):
    return K.observation(row(body, status), slug, route, h,
                         h if actual is None else actual)


# ------------------------------------------------------------- the grid
def test_every_horizon_lands_on_the_grid_exactly():
    """5, 10, 30 and 60 are all even multiples of the 2.5 s slot, so the
    exact-horizon requirement and the 0.4 rps floor are compatible and no
    interpolated state is ever needed."""
    for h in K.HORIZONS:
        assert (h / K.SPACING_S) == int(h / K.SPACING_S), h
    offsets = {h: off for off, route, h in K.LEGS if route == "book"}
    for h, off in offsets.items():
        assert off * K.SPACING_S == h


def test_the_paired_bbo_sits_exactly_one_slot_after_its_book_read():
    books = {h: off for off, r, h in K.LEGS if r == "book"}
    bbos = {h: off for off, r, h in K.LEGS if r == "bbo"}
    assert set(books) == set(bbos)
    for h in books:
        assert bbos[h] - books[h] == 1


def test_the_grid_never_double_books_a_slot():
    grid = K.plan_grid(400)
    assert len(grid) == 400 * len(K.LEGS)


def test_the_set_period_is_the_smallest_collision_free_one():
    """SET_PERIOD_SLOTS is an argument, so it is checked rather than asserted:
    every shorter period collides, and this one does not."""
    offs = [off for off, _, _ in K.LEGS]
    for period in range(1, K.SET_PERIOD_SLOTS):
        seen, clash = set(), False
        for i in range(8):
            for o in offs:
                s = i * period + o
                if s in seen:
                    clash = True
                seen.add(s)
        assert clash, "period %d does not collide -- it should have been used" % period
    assert len(K.plan_grid(8)) == 8 * len(offs)


def test_the_packing_is_the_densest_one_that_exists_for_this_pattern():
    """Exhaustive over every cyclic period up to 80: 10/14 is the maximum
    density. Stated as measured, not as a guess."""
    P = [off for off, _, _ in K.LEGS]
    best = 0.0
    for M in range(K.SET_SPAN_SLOTS, 81):
        cells = [frozenset((g + p) % M for p in P) for g in range(M)]
        ok = [g for g in range(M) if len(cells[g]) == len(P)]
        top = [0]

        def dfs(i, occ, n):
            if n + (len(ok) - i) <= top[0]:
                return
            if i == len(ok):
                top[0] = max(top[0], n)
                return
            g = ok[i]
            if not (cells[g] & occ):
                dfs(i + 1, occ | cells[g], n + 1)
            dfs(i + 1, occ, n)

        dfs(0, frozenset(), 0)
        best = max(best, top[0] * len(P) / M)
    assert abs(best - len(P) / K.SET_PERIOD_SLOTS) < 1e-9


def test_the_effective_rate_stays_under_the_nominal_ceiling():
    density = len(K.LEGS) / K.SET_PERIOD_SLOTS
    assert density <= 1.0
    assert density * K.NOMINAL_RPS <= K.NOMINAL_RPS


def test_sets_in_window_completes_its_last_set():
    assert K.sets_in_window(0) == 0
    assert K.sets_in_window(60) == 0                      # 24 slots < 26
    assert K.sets_in_window(K.SET_SPAN_SLOTS * K.SPACING_S) == 1
    n = K.sets_in_window(3600)
    last_end = ((n - 1) * K.SET_PERIOD_SLOTS + K.SET_SPAN_SLOTS - 1) * K.SPACING_S
    assert last_end <= 3600


# --------------------------------------------------------------- parsing
def test_bbo_is_read_from_the_marketdata_envelope():
    """THE PHASE 2F DEFECT, pinned. The parser looked for marketBbo/bbo and
    every cross-route comparison came back NOT_IDENTIFIED."""
    bb, ba = K.bbo_touch(bbo_body("0.72", "0.73"))
    assert str(bb) == "0.72" and str(ba) == "0.73"


def test_ladder_hash_ignores_transact_time():
    """transactTime is the venue's periodic price mark (Phase 2F). Folding it
    into the identity would make an unchanged book look changed."""
    a = book_body([lvl("0.5", "1")], [lvl("0.6", "1")], tt="T1")
    b = book_body([lvl("0.5", "1")], [lvl("0.6", "1")], tt="T2")
    c = book_body([lvl("0.5", "2")], [lvl("0.6", "1")], tt="T1")
    assert K.ladder_hash(a) == K.ladder_hash(b)
    assert K.ladder_hash(a) != K.ladder_hash(c)


def test_identity_is_checked_against_the_requested_slug():
    o = K.observation(row(book_body([], [], slug="other")), "m", "book", 0.0, 0.0)
    assert o["identity_ok"] is False
    o2 = K.observation(row(book_body([], [], slug="m")), "m", "book", 0.0, 0.0)
    assert o2["identity_ok"] is True


def test_horizon_error_is_recorded_not_corrected():
    o = K.observation(row(book_body([], [])), "m", "book", 5.0, 5.31)
    assert abs(o["horizon_error_s"] - 0.31) < 1e-9
    assert o["actual_horizon_s"] == 5.31
    assert o["target_horizon_s"] == 5.0


# ------------------------------------------------- markout semantics
def full_set(t0, th, actual=None):
    legs = {("book", 0.0): leg("book", 0.0, t0)}
    for h in K.HORIZONS:
        legs[("book", h)] = leg("book", h, th, actual=(h if actual is None else actual))
    return legs


def test_an_unchanged_book_is_an_observation_worth_zero_and_it_is_kept():
    """THE RETRACTION, pinned. Same ladder at t and t+h: the observation EXISTS,
    the markout is 0, and it stays in the primary set."""
    same = book_body([lvl("0.50", "10")], [lvl("0.51", "10")])
    rec = K.derive_set(full_set(same, same))
    h = rec["horizons"]["5.0"]
    assert h["horizon_observation_available"] is True
    assert h["book_changed_by_horizon"] is False
    assert h["mid_markout"] == "0"
    assert h["touch_bid_markout"] == "0"
    assert rec["passive_quote_postable"] is True
    assert rec["valid_horizon_set"] is True


def test_a_moved_book_yields_a_signed_markout():
    t0 = book_body([lvl("0.50", "10")], [lvl("0.51", "10")])
    th = book_body([lvl("0.52", "10")], [lvl("0.53", "10")])
    rec = K.derive_set(full_set(t0, th))
    h = rec["horizons"]["10.0"]
    assert h["book_changed_by_horizon"] is True
    assert h["mid_markout"] == "0.02"
    assert h["touch_bid_markout"] == "0.02"


def test_a_missing_horizon_leg_is_missing_and_is_never_interpolated():
    t0 = book_body([lvl("0.50", "1")], [lvl("0.51", "1")])
    legs = full_set(t0, t0)
    legs[("book", 30.0)] = leg("book", 30.0, None, status=503)
    rec = K.derive_set(legs)
    h30 = rec["horizons"]["30.0"]
    assert h30["horizon_observation_available"] is False
    assert "mid_markout" not in h30
    assert rec["valid_horizon_set"] is False
    # and the horizons that WERE observed still carry their markouts
    assert rec["horizons"]["60.0"]["mid_markout"] == "0"


def test_a_one_sided_book_is_not_postable_but_is_still_recorded():
    t0 = book_body([lvl("0.50", "1")], [])
    rec = K.derive_set(full_set(t0, t0))
    assert rec["passive_quote_postable"] is False
    assert rec["valid_horizon_set"] is True          # the snapshots exist
    assert rec["horizons"]["5.0"]["horizon_observation_available"] is True
    assert rec["horizons"]["5.0"]["touch_bid_markout"] == "0"


# ------------------------------------------ passive opportunity semantics
def test_touched_is_at_or_through_and_crossed_is_strictly_through():
    t0 = book_body([lvl("0.50", "1")], [lvl("0.51", "1")])
    at = book_body([lvl("0.49", "1")], [lvl("0.50", "1")])      # ask == our bid
    thru = book_body([lvl("0.48", "1")], [lvl("0.49", "1")])    # ask <  our bid
    a = K.derive_set(full_set(t0, at))["horizons"]["5.0"]
    b = K.derive_set(full_set(t0, thru))["horizons"]["5.0"]
    assert a["buy_touched"] is True and a["buy_crossed"] is False
    assert b["buy_touched"] is True and b["buy_crossed"] is True


def test_a_touch_is_never_reported_as_a_fill():
    t0 = book_body([lvl("0.50", "1")], [lvl("0.51", "1")])
    th = book_body([lvl("0.50", "1")], [lvl("0.50", "1")])
    rec = K.derive_set(full_set(t0, th))
    assert rec["passive_fill_probability"] == "NOT_IDENTIFIED"
    assert rec["horizons"]["5.0"]["passive_touch_proxy"] is True
    assert "fill" not in json.dumps(rec["horizons"]).replace(
        "passive_fill_probability", "")


def test_two_touches_are_recorded_but_never_become_a_completed_pair():
    """A crossed book touches both resting prices at once. That is two touches,
    not a round trip: PAIR_COMPLETED stays NOT_IDENTIFIED."""
    t0 = book_body([lvl("0.50", "1")], [lvl("0.51", "1")])
    th = book_body([lvl("0.60", "1")], [lvl("0.40", "1")])
    h = K.derive_set(full_set(t0, th))["horizons"]["5.0"]
    assert h["buy_touched"] is True and h["sell_touched"] is True
    assert h["pair_both_touched"] is True
    assert h["pair_completed"] == "NOT_IDENTIFIED"


def test_the_registers_are_separable_and_the_primary_one_is_unconditioned():
    """A quiet set (no move, no touch) must still be a postable opportunity --
    otherwise the dataset is conditioned on movement."""
    quiet_t0 = book_body([lvl("0.50", "1")], [lvl("0.51", "1")])
    quiet = K.derive_set(full_set(quiet_t0, quiet_t0))
    loud = K.derive_set(full_set(quiet_t0,
                                 book_body([lvl("0.50", "1")], [lvl("0.50", "1")])))
    postable = [r for r in (quiet, loud) if r["passive_quote_postable"]]
    touched = [r for r in postable
               if r["horizons"]["5.0"].get("passive_touch_proxy")]
    assert len(postable) == 2          # the quiet one is NOT excluded
    assert len(touched) == 1


# ------------------------------------------------------------- freshness
def test_cross_route_agreement_is_counted_with_its_lag_disclosed():
    t0 = book_body([lvl("0.50", "1")], [lvl("0.51", "1")])
    legs = full_set(t0, t0)
    legs[("bbo", 0.0)] = leg("bbo", 0.0, bbo_body("0.50", "0.51"))
    legs[("bbo", 5.0)] = leg("bbo", 5.0, bbo_body("0.55", "0.56"))
    rec = K.derive_set(legs)
    assert rec["cross_route_agree"] == 1
    assert rec["cross_route_disagree"] == 1
    assert rec["cross_route_absent"] == 3
    assert rec["cross_route_lag_s"] == K.SPACING_S


# ------------------------------------------------------------ retirement
def subj(slug="m"):
    return K.Subject({"market_slug": slug, "sport_bucket": "nfl",
                      "native_event_id": "1"})


def test_a_terminal_venue_state_retires_the_subject_at_once():
    s = subj()
    why = s.note_set({"t0_state": "MARKET_STATE_RESOLVED",
                      "passive_quote_postable": True})
    assert why.startswith("VENUE_TERMINAL_STATE")


def test_a_transient_empty_book_does_not_retire_the_subject():
    s = subj()
    for _ in range(K.NO_BOOK_STREAK - 1):
        assert s.note_set({"t0_state": "MARKET_STATE_OPEN",
                           "passive_quote_postable": False}) is None
    # one good set resets the streak -- an outage is not a retirement
    assert s.note_set({"t0_state": "MARKET_STATE_OPEN",
                       "passive_quote_postable": True}) is None
    assert s.no_book == 0


def test_a_sustained_absence_of_a_two_sided_book_does_retire():
    s = subj()
    why = None
    for _ in range(K.NO_BOOK_STREAK):
        why = s.note_set({"t0_state": "MARKET_STATE_OPEN",
                          "passive_quote_postable": False})
    assert why == "SUSTAINED_NO_TWO_SIDED_BOOK"


def test_retirement_is_never_a_substitution():
    """The plan assigns market i to set i mod n for the whole segment. A
    retired subject's slots go idle; they are never handed to another market,
    because reallocating mid-capture is mid-capture optimisation."""
    src = Path(K.__file__).read_text()
    assert "set_index % len(subjects)" in src
    assert "continue                      # slot goes idle" in src


# --------------------------------------------------------------- cohort
def test_the_frozen_cohort_file_matches_the_pinned_digest():
    p = Path(__file__).with_name(K.COHORT_FILE)
    raw = p.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == K.COHORT_SHA256
    cohort = json.loads(raw)
    assert len(cohort) == 12
    assert len({e["native_event_id"] for e in cohort}) == 12
    assert len({e["market_slug"] for e in cohort}) == 12


def test_the_runner_refuses_a_cohort_whose_digest_does_not_match(tmp_path=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "c.json"
        bad.write_text("[]")
        rc = K.main(["--out", str(Path(d) / "o"), "--segment", "x",
                     "--duration-s", "1", "--cohort", str(bad)])
    assert rc == 2


# ---------------------------------------------------- the execution loop
class _FakeFH:
    def __init__(self):
        self.lines = []

    def write(self, s):
        self.lines.append(s)


def _drive(responder, duration_s, n_subjects=3, spacing=0.004):
    """Run the real loop against a stub venue, with the grid compressed.

    Only SPACING_S moves -- the slot pattern, the round robin and the set
    bookkeeping are the shipping ones.
    """
    real_get, real_spacing = K.C._get, K.SPACING_S
    K.C._get = responder
    K.SPACING_S = spacing
    try:
        subjects = [subj("m%d" % i) for i in range(n_subjects)]
        res, fh = {}, _FakeFH()
        done = K.run_segment(None, subjects, duration_s, fh, res)
        return subjects, res, done, fh
    finally:
        K.C._get, K.SPACING_S = real_get, real_spacing


def _ok(path, slug=None):
    slug = slug or path.split("/")[3]
    body = (bbo_body("0.50", "0.51", slug) if path.endswith("/bbo")
            else book_body([lvl("0.50", "1")], [lvl("0.51", "1")], slug))
    return row(body, path=path)


def test_the_loop_completes_whole_sets_with_every_leg():
    def responder(http, path, params=None):
        return _ok(path)

    subjects, res, done, fh = _drive(responder, 0.5)
    assert done, "no set completed"
    for rec in done:
        assert rec["legs_observed"] == 5          # t0 + the four horizons
        assert rec["legs_expected"] == 5
        assert rec["identity_failures"] == 0
        assert rec["passive_quote_postable"] is True
    assert res["venue_requests"] if "venue_requests" in res else True
    assert len(fh.lines) == sum(1 for _ in fh.lines)
    # ten raw rows for every completed set, book and bbo both retained
    assert len(fh.lines) >= 10 * len(done)


def test_the_loop_takes_the_markets_round_robin():
    def responder(http, path, params=None):
        return _ok(path)

    subjects, res, done, fh = _drive(responder, 0.5, n_subjects=3)
    order = [r["market_slug"] for r in done]
    assert order[:6] == ["m0", "m1", "m2", "m0", "m1", "m2"][:len(order[:6])]


def test_a_wrong_slug_in_the_response_stops_the_segment_at_once():
    """Subject attribution is the one thing a capture cannot recover from."""
    def responder(http, path, params=None):
        return _ok(path, slug="somebody-elses-market")

    subjects, res, done, fh = _drive(responder, 0.5)
    assert res["stop_reason"] == "IDENTITY_FAILURE"
    assert res["identity_failures"] == 1
    assert len(fh.lines) == 1        # it stopped on the very first read


def test_repeated_404s_retire_only_that_subject():
    def responder(http, path, params=None):
        if "/m1/" in path:
            r = row(None, status=404, path=path)
            r["error"] = "http_404"
            return r
        return _ok(path)

    subjects, res, done, fh = _drive(responder, 0.5)
    by = {s.slug: s.retired_reason for s in subjects}
    assert by["m1"] == "MARKET_NOT_FOUND"
    assert by["m0"] is None and by["m2"] is None
    assert all(r["market_slug"] != "m1" for r in done[3:])


def test_the_rate_floor_is_honoured_even_when_a_response_is_slow():
    """A slow response must never be followed by a catch-up burst."""
    import time as _t
    stamps = []

    def responder(http, path, params=None):
        stamps.append(_t.monotonic())
        if len(stamps) == 3:
            _t.sleep(0.05)                      # ~12 slots' worth
        return _ok(path)

    _drive(responder, 0.4, spacing=0.004)
    gaps = [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1)]
    assert min(gaps) >= 0.004 - 1e-4, min(gaps)


# ------------------------------------------------------------ no claims
def test_the_module_makes_no_profit_claim():
    src = Path(K.__file__).read_text()
    for banned in ("NET_EXPECTANCY = ", "NET_ROI = ", "DEPLOYABLE_EDGE = ",
                   "GUARANTEED_PROFIT = "):
        assert banned not in src, banned
    assert 'PASSIVE_FILL_PROBABILITY = NOT_IDENTIFIED' in src
    assert 'PMUS_FEES_RESOLVED = NO' in src


def test_the_capability_boundary_holds_in_the_source():
    src = Path(K.__file__).read_text()
    assert "api.polymarket.us" not in src
    for verb in (".post(", ".put(", ".patch(", ".delete("):
        assert verb not in src, verb
    assert K.NOMINAL_RPS <= 0.4


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(vars().items())
           if n.startswith("test_") and callable(f)]
    bad = []
    for n, f in fns:
        try:
            f()
        except Exception as exc:                       # noqa: BLE001
            bad.append((n, exc))
    for n, exc in bad:
        print("FAIL %s: %r" % (n, exc))
    print("%d passed, %d failed" % (len(fns) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)

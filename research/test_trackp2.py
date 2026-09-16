#!/usr/bin/env python3
"""Offline gate for TRACK P2. Contacts nothing.

Pins the protocol's promises so that a later edit cannot quietly relax one
after outcomes start arriving -- which is the only moment at which any of
them would be tempting to relax.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

def _load(n, f):
    s = importlib.util.spec_from_file_location(n, Path(__file__).with_name(f))
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

P = _load("p2", "trackp2_protocol.py")
SRC_CAP = Path(__file__).with_name("trackp2_capture.py").read_text()


def rows(n, mid, rate, event_of=lambda i: i):
    """n market-sides at `mid`, of which `rate` settle YES."""
    out = []
    for i in range(n):
        y = 1 if i < int(round(rate * n)) else 0
        out.append({"market_slug": "m%d" % i, "event_id": "e%d" % event_of(i),
                    "sport": "nfl", "midpoint": mid, "outcome": y,
                    "residual": y - mid})
    return out


# ---------------------------------------------------- the frozen protocol
def test_the_protocol_hashes_and_the_hash_is_stable():
    a, b = P.spec_hash(), P.spec_hash()
    assert a == b and len(a) == 64
    assert json.loads(P.spec_blob())["PROTOCOL_VERSION"] == "TRACKP2-1"


def test_the_meaningful_effect_is_derived_not_chosen():
    """0.02 must equal the stated derivation, so it cannot drift to fit a
    result later."""
    assert abs(P.MEANINGFUL_EFFECT - (0.06 * 0.5 * 0.5 + 0.01 / 2)) < 1e-12


def test_checkpoints_and_boundaries_are_predeclared():
    assert P.CHECKPOINTS == (250, 500, 1000, 2500)
    assert P.INTERIM_Z == 3.0 and P.FINAL_Z == 1.98
    assert P.PRIMARY_ARM == "T_MINUS_60"


# ------------------------------------------------- final confirmation split
def test_final_confirmation_is_assignable_from_identity_alone():
    a = P.assignment("aec-nfl-den-kc-2026-09-14")
    assert a in ("EXPLORATORY", "P2_FINAL_CONFIRMATION")
    assert a == P.assignment("aec-nfl-den-kc-2026-09-14")   # deterministic


def test_the_reserve_is_about_twenty_percent_and_unrelated_to_outcome():
    n = 20000
    res = sum(1 for i in range(n)
              if P.assignment("slug-%d" % i) == "P2_FINAL_CONFIRMATION")
    assert 0.18 <= res / n <= 0.22, res / n


def test_checkpoint_analysis_never_sees_the_reserved_markets():
    """The exclusion must be in the eligibility filter, not a convention."""
    assert "RESERVED_P2_FINAL_CONFIRMATION" in SRC_CAP
    assert "include_final_confirmation=False" in SRC_CAP


# -------------------------------------------------------- midpoint/outcome
def test_a_one_sided_book_has_no_midpoint():
    assert P.midpoint(None, 0.6) is None
    assert P.midpoint(0.4, None) is None
    assert P.midpoint(0.6, 0.4) is None            # crossed
    assert abs(P.midpoint(0.40, 0.60) - 0.50) < 1e-12


def test_outcome_is_never_inferred_from_a_price():
    assert P.outcome_of(0, '["1","0"]') == 1
    assert P.outcome_of(1, '["1","0"]') == 0
    assert P.outcome_of(0, '["0.5","0.5"]') is None     # void-like
    assert P.outcome_of(0, '["0.7","0.3"]') is None     # a price, not a label
    assert P.outcome_of(0, None) is None


def test_void_and_cancel_are_counted_not_dropped():
    assert "VOID" in P.VOID_HANDLING and "never dropped silently" in \
        P.VOID_HANDLING
    assert "SETTLEMENT_VOID_OR_CANCELED" in " ".join(P.EXCLUSION_RULES)


# --------------------------------------------------------- the gate itself
def test_a_perfectly_calibrated_market_stops_for_futility():
    r = rows(3000, 0.50, 0.50)
    out = P.analyse(r, 3, "t")
    assert out["GATE"] == "P2-C", out["GATE_REASON"]
    assert abs(out["MEAN_CALIBRATION_RESIDUAL"]) < 1e-9


def test_a_large_stable_deviation_clears_the_interim_boundary():
    r = rows(1000, 0.50, 0.58)          # +8pp, one market per event
    out = P.analyse(r, 2, "t")
    assert out["GATE"] == "P2-A", out
    assert out["BOUNDARY_Z"] == P.INTERIM_Z


def test_a_real_but_economically_irrelevant_deviation_is_not_p2_a():
    """1pp cannot clear PMUS taker cost, so however significant it is, it
    must not open the execution experiment."""
    r = rows(20000, 0.50, 0.51)
    out = P.analyse(r, 3, "t")
    assert out["GATE"] != "P2-A", out["GATE_REASON"]


def test_an_interim_look_is_harder_to_pass_than_the_final_one():
    r = rows(400, 0.50, 0.565)
    interim, final = P.analyse(r, 0, "t"), P.analyse(r, 3, "t")
    assert interim["BOUNDARY_Z"] > final["BOUNDARY_Z"]
    assert not (interim["GATE"] == "P2-A" and final["GATE"] != "P2-A")


def test_an_underpowered_look_continues_rather_than_concluding():
    out = P.analyse(rows(60, 0.50, 0.57), 0, "t")
    assert out["GATE"] == "P2-B", out["GATE_REASON"]


def test_no_settled_markets_is_p2_b_not_a_finding():
    out = P.analyse([], 0, "t")
    assert out["GATE"] == "P2-B"
    assert out["GATE_REASON"] == "NO_ELIGIBLE_SETTLED_MARKETS_YET"


# ------------------------------------------------------------- clustering
def test_uncertainty_is_clustered_by_event_not_by_market():
    """Twelve markets from one game are one piece of evidence. If the
    interval ignored that it would be far too narrow and every gate would
    read too optimistic."""
    ind = rows(1200, 0.50, 0.56, event_of=lambda i: i)
    clu = rows(1200, 0.50, 0.56, event_of=lambda i: i // 12)
    wi = P.analyse(ind, 2, "t")["CI95_CLUSTERED_BY_EVENT"]
    wc = P.analyse(clu, 2, "t")["CI95_CLUSTERED_BY_EVENT"]
    assert (wc[1] - wc[0]) > (wi[1] - wi[0]) * 1.5, (wi, wc)
    assert P.analyse(clu, 2, "t")["N_EVENTS"] == 100


# ------------------------------------------------------- register hygiene
def test_p2_measures_register_1_and_says_so():
    s = P.spec()
    assert "PREDICTIVE" in s["REGISTERS"]["1"]
    assert "not measured by P2" in s["REGISTERS"]["2"]
    assert "BLOCK_4" in s["REGISTERS"]["3"]


def test_the_midpoint_is_never_called_executable():
    assert "never described as executable" in P.MIDPOINT_DEFINITION
    assert "MIDPOINT" in P.PRIMARY_STATISTIC


def test_the_population_rule_refuses_a_page_ceiling_as_a_boundary():
    assert "NOT a terminal boundary" in P.POPULATION_RULE
    for banned in ("RN1 filter", "activity", "liquidity", "spread",
                   "manual shortlist"):
        assert banned in P.POPULATION_RULE, banned


def test_segmentation_is_bounded_in_advance():
    assert "No league, market-type, finer price band" in P.SEGMENTATION_RULES
    assert len(P.PRICE_BANDS) == 3


# ------------------------------------------------------ capture behaviour
def test_an_unreachable_board_is_never_an_empty_board():
    """The defect this file exists to prevent: BL.discover() calls a failed
    page a terminal boundary, so a blocked host reported
    DISCOVERY_LIST_EXHAUSTED = YES with zero markets."""
    assert "ACCESS FAILURE, not an empty board" in SRC_CAP
    i_guard = SRC_CAP.index('first.get("http_status") != 200')
    i_term = SRC_CAP.index('terminal = res.get("DISCOVERY_LIST_EXHAUSTED")')
    assert i_guard < i_term, "the guard must precede the terminal verdict"


def test_the_capture_client_is_get_only_against_one_host():
    assert 'GATEWAY = "https://gateway.polymarket.us"' in SRC_CAP
    for bad in ("http.post", "http.put", "http.delete", "http.patch",
                "api.polymarket.us", "os.environ", "PRIVATE_KEY"):
        assert bad not in SRC_CAP, bad


def test_the_rate_floor_is_the_locked_one():
    assert "SPACING_S = 2.5" in SRC_CAP


def test_settlement_follow_up_is_mandatory_and_identity_preserving():
    assert "MANDATORY" in SRC_CAP
    assert "/v1/markets/%s" in SRC_CAP
    assert 'r["market_slug"]' in SRC_CAP


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(vars().items())
           if n.startswith("test_") and callable(f)]
    bad = []
    for n, f in fns:
        try:
            f()
        except Exception as exc:                        # noqa: BLE001
            bad.append((n, exc))
    for n, exc in bad:
        print("FAIL %s: %r" % (n, exc))
    print("%d passed, %d failed" % (len(fns) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)

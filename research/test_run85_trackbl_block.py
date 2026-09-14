#!/usr/bin/env python3
"""Offline tests for the Run 85 Track B-L block runner. Contacts nothing.

These pin the constraints that would be easy to violate later by accident:
a block is independent, its hypothetical orders die with it, horizons beyond
the block length are declared rather than imputed, and the plan satisfies the
2.5 s floor before anything is spent.

Run:  python3 -m pytest research/test_run85_trackbl_block.py -q
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import re
import sys
from decimal import Decimal as D
from pathlib import Path

_s = importlib.util.spec_from_file_location(
    "bl", Path(__file__).with_name("run85_trackbl_block.py"))
L = importlib.util.module_from_spec(_s)
_s.loader.exec_module(L)

SRC = Path(L.__file__).read_text()

# REAL VENUE SHAPES, lifted verbatim from sealed evidence -- the BLOCK_2
# discovery samples and the Phase 2G-R request log. The old fixtures used
# "primaryTag": "nfl", a string this venue has never sent, which is precisely
# why they could not catch the defect that would have crashed a good block.
SHAPES = json.loads(
    Path(__file__).with_name("run85_trackbl_venue_shapes.json").read_text())


def plan():
    slots = []
    for cyc in range(L.MAX_CYCLES):
        for lane in range(L.N_LANES):
            base = cyc * L.CYCLE_S + L.LANE_STAGGERS[lane]
            for off in L.BURST_OFFSETS:
                slots.append(base + off)
    return sorted(slots)


# --------------------------------------------------- block independence
def test_a_block_carries_no_state_from_any_earlier_block():
    """CROSS_BLOCK_ORDER_STATE = FORBIDDEN. The runner must not read any prior
    block's cohort, log or summary -- every block selects fresh and its
    hypothetical orders expire with it."""
    for banned in ("block_cohort.json\"", "previous_block", "prior_block",
                   "carry_over", "resume", "--continue"):
        assert banned not in SRC.replace('(out / "block_cohort.json")', ""), banned
    # the only cohort the runner ever loads is the one it just selected
    assert "select_block_v2(final)" in SRC
    assert SRC.count("read_bytes()") == 1        # only its own source, for the digest
    assert "read_text()" not in SRC


def test_the_runner_writes_only_inside_its_own_out_directory():
    writes = re.findall(r"\(out / \"[^\"]+\"\)", SRC)
    assert writes, "expected the runner to write its own artifacts"
    assert "open(" not in SRC.replace("gzip.open(", "").replace(".open(", "")


def test_hypothetical_orders_are_scoped_to_the_block_not_persisted():
    """Nothing resembling an order-state store exists."""
    for banned in ("pickle", "shelve", "sqlite", "state_file", "orders.json"):
        assert banned not in SRC.lower(), banned


# ------------------------------------------------------- block length
def test_a_block_never_exceeds_twenty_minutes():
    span = plan()[-1]
    assert span <= 20 * 60, "block span %.0f s exceeds the 20 minute cap" % span


def test_the_supported_horizon_set_is_exactly_what_the_block_can_reach():
    assert set(L.BURST_OFFSETS) == {0.0, 5.0, 10.0, 30.0, 60.0}
    assert L.DERIVABLE_LONG_HORIZONS == {"5m": 1, "10m": 2, "15m": 3}
    # every derivable horizon must actually fit inside MAX_CYCLES
    for name, cyc in L.DERIVABLE_LONG_HORIZONS.items():
        assert cyc < L.MAX_CYCLES, name


def test_the_unreachable_horizons_are_declared_and_never_imputed():
    assert set(L.UNREACHABLE_HORIZONS) == {"30m", "60m"}
    for name in L.UNREACHABLE_HORIZONS:
        minutes = int(name.rstrip("m"))
        assert minutes * 60 > plan()[-1], name        # genuinely out of reach
    assert "NOT_OBSERVED_WITHIN_BLOCK_LENGTH" in SRC
    for banned in ("interpolat", "impute", "extrapolat"):
        assert banned not in SRC.lower(), banned


# ------------------------------------------------------------ the plan
def test_the_plan_respects_the_two_and_a_half_second_floor():
    p = plan()
    gaps = [p[i + 1] - p[i] for i in range(len(p) - 1)]
    assert min(gaps) >= L.SPACING_S - 1e-9, min(gaps)


def test_no_two_reads_ever_want_the_same_instant():
    p = plan()
    assert len(p) == len(set(p))


def test_the_runner_refuses_to_start_if_the_plan_ever_violates_spacing():
    assert "PLAN_VIOLATES_SPACING" in SRC


def test_the_lane_staggers_avoid_the_offset_difference_set():
    diffs = {abs(a - b) for a in L.BURST_OFFSETS for b in L.BURST_OFFSETS} - {0.0}
    for i, x in enumerate(L.LANE_STAGGERS):
        for y in L.LANE_STAGGERS[i + 1:]:
            assert abs(x - y) not in diffs, (x, y)


# ------------------------------------------------------------ selection
def test_selection_uses_only_discovery_time_evidence():
    for c in L.candidates([{"id": 1, "primaryTag": "nfl", "tags": ["nfl"],
                            "markets": [{"slug": "m", "active": True,
                                         "closed": False, "archived": False,
                                         "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
                                         "bestBidQuote": {"value": "0.50"},
                                         "bestAskQuote": {"value": "0.51"},
                                         "orderPriceMinTickSize": "0.01"}]}], 0):
        assert c["selected_from"] == "discovery_payload"
        assert c["spread_over_mid"] is not None


def test_a_one_sided_or_closed_market_is_never_a_candidate():
    ev = {"id": 1, "primaryTag": None, "tags": [], "markets": [
        {"slug": "closed", "active": True, "closed": True, "archived": False,
         "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
         "bestBidQuote": {"value": "0.50"}, "bestAskQuote": {"value": "0.51"}},
        {"slug": "oneside", "active": True, "closed": False, "archived": False,
         "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
         "bestBidQuote": {"value": "0.50"}, "bestAskQuote": None},
        {"slug": "crossed", "active": True, "closed": False, "archived": False,
         "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
         "bestBidQuote": {"value": "0.60"}, "bestAskQuote": {"value": "0.50"}},
    ]}
    assert L.candidates([ev], 0) == []


def test_one_market_per_event_and_round_robin_over_sports():
    cands = []
    for i in range(6):
        cands.append({"native_event_id": str(i), "sport": "nfl" if i < 4 else "cfb",
                      "market_slug": "m%d" % i, "spread_over_mid": "0.01",
                      "mid": "0.5", "price_band": "NEAR_MID"})
        cands.append({"native_event_id": str(i), "sport": "nfl" if i < 4 else "cfb",
                      "market_slug": "m%d_b" % i, "spread_over_mid": "0.02",
                      "mid": "0.5", "price_band": "NEAR_MID"})
    got = L.select_block(cands, cap=4)
    assert len({g["native_event_id"] for g in got}) == len(got)
    assert len({g["sport"] for g in got}) == 2


def test_selection_is_deterministic_under_reordering():
    cands = [{"native_event_id": str(i), "sport": ["nfl", "cfb", "mlb"][i % 3],
              "market_slug": "m%d" % i, "spread_over_mid": "0.0%d" % (i + 1),
              "mid": "0.5", "price_band": "NEAR_MID"} for i in range(9)]
    a = [x["market_slug"] for x in L.select_block(list(cands), cap=6)]
    b = [x["market_slug"] for x in L.select_block(list(reversed(cands)), cap=6)]
    assert a == b


# ------------------------------------------------------ no claims
def test_the_module_never_calls_a_touch_a_fill():
    assert "MAKER_FILL_PROBABILITY = NOT_IDENTIFIED" in SRC
    assert "PAIR_COMPLETION_PROBABILITY = NOT_IDENTIFIED" in SRC
    assert "TOUCH_IS_NOT_FILL" in SRC


def test_the_capability_boundary_holds():
    assert "api.polymarket.us" not in SRC
    for verb in (".post(", ".put(", ".patch(", ".delete("):
        assert verb not in SRC, verb
    assert L.SPACING_S >= 2.5





# ============ REPAIR SCOPE: diagnostics, current-end walk, hard failure ======
def test_page_record_retains_everything_needed_to_diagnose_a_zero_result():
    """BLOCK_1 could not be diagnosed because the discovery bodies were
    stripped. These fields must survive, or the same blindness returns."""
    row = {"http_status": 200, "error": None, "response_bytes": 10,
           "response_sha256": "x", "body": {"events": []}}
    rec = L.page_record(300, row, [{"id": 7}, {"id": 9}], b"{}")
    for k in ("path", "params", "offset", "http_status", "response_sha256",
              "body_sha256_recomputed", "event_count", "first_event_id",
              "last_event_id", "event_ids", "top_level_keys"):
        assert k in rec, k
    assert rec["offset"] == 300
    assert rec["first_event_id"] == "7" and rec["last_event_id"] == "9"
    assert rec["event_count"] == 2


def test_body_samples_are_bounded_but_keep_head_and_tail():
    evs = [{"id": i} for i in range(500)]
    s = L.sample_body(evs)
    assert s["total"] == 500
    assert len(s["events_head"]) == L.BODY_SAMPLE_EVENTS
    assert len(s["events_tail"]) == L.BODY_SAMPLE_EVENTS
    assert s["events_head"][0]["id"] == 0
    assert s["events_tail"][-1]["id"] == 499


# =================== BL-SELECT-2: ACTIVITY-AWARE SELECTION ==================
def test_the_selection_rule_version_is_bumped_and_bl_select_1_is_named_retired():
    assert L.SELECTION_RULE_VERSION == "BL-SELECT-2"
    assert "BL-SELECT-1 IS RETIRED: ACTIVITY_BLIND_SELECTION" in SRC
    assert "RETIRED_FOR_TRACK_B_L" in SRC
    # and "unchanged" may never be readable as "same rule as BLOCK_3"
    assert "SELECTION_RULE_UNCHANGED_WITHIN_BLOCK" in SRC
    assert '"SELECTION_RULE_UNCHANGED"' not in SRC


def test_the_activity_fields_are_read_from_the_book_by_name():
    """These exist in /book and NOT in /v1/events -- the whole reason
    BL-SELECT-1 could not see them."""
    row = {"body": {"marketData": {
        "state": "MARKET_STATE_OPEN",
        "stats": {"sharesTraded": "6196.79",
                  "notionalTraded": {"value": "485746.31", "currency": "USD"},
                  "openInterest": "5610.15",
                  "lastTradeSetTime": "2026-09-14T12:35:56.416483152Z"},
        "bids": [{"px": {"value": "0.7800"}, "qty": "1496.0000"},
                 {"px": {"value": "0.0100"}, "qty": "9.0"}],
        "offers": [{"px": {"value": "0.7900"}, "qty": "1015.7500"}]}}}
    af = L.activity_fields(row, L._epoch("2026-09-14T17:13:39Z"))
    assert af["SHARES_TRADED"] == D("6196.79")
    assert af["NOTIONAL_TRADED"] == D("485746.31")
    assert af["OPEN_INTEREST"] == D("5610.15")
    assert af["BEST_BID"] == D("0.7800") and af["BEST_BID_SIZE"] == D("1496.0000")
    assert af["BEST_ASK"] == D("0.7900") and af["BEST_ASK_SIZE"] == D("1015.7500")
    assert 16000 < af["SECONDS_SINCE_LAST_TRADE"] < 17000        # ~4.7 h
    assert af["selected_from"] == "book_probe_receipt"


def test_a_market_with_no_last_trade_time_is_not_identified_not_zero():
    af = L.activity_fields({"body": {"marketData": {"stats": {}}}}, 1.0)
    assert af["SECONDS_SINCE_LAST_TRADE"] is None
    assert af["SHARES_TRADED"] is None and af["NOTIONAL_TRADED"] is None


def test_the_distribution_is_reported_before_any_threshold_exists():
    """Section order is load-bearing: distributions are computed and printed,
    then the gate reads them."""
    i_dist = SRC.index("CANDIDATE DISTRIBUTIONS (before any threshold")
    i_gate = SRC.index("\n        activity_gate(probed, dist)")
    assert i_dist < i_gate
    d = L.pctiles([1.0, 2.0, 3.0, 4.0, 5.0, None])
    assert d["n"] == 5 and d["n_missing"] == 1 and d["median"] == 3.0


def test_the_activity_gate_uses_a_prospective_floor_and_the_probed_median():
    dist = {"SECONDS_SINCE_LAST_TRADE": {"median": 600.0},
            "NOTIONAL_TRADED": {"median": 1000.0}}
    rows = [
        {"SECONDS_SINCE_LAST_TRADE": 60.0, "NOTIONAL_TRADED": D("5000"),
         "OPEN_INTEREST": D("10")},                       # passes
        {"SECONDS_SINCE_LAST_TRADE": 4000.0, "NOTIONAL_TRADED": D("5000"),
         "OPEN_INTEREST": D("10")},                       # stale beyond the floor
        {"SECONDS_SINCE_LAST_TRADE": 700.0, "NOTIONAL_TRADED": D("5000"),
         "OPEN_INTEREST": D("10")},                       # slower than median
        {"SECONDS_SINCE_LAST_TRADE": 60.0, "NOTIONAL_TRADED": D("10"),
         "OPEN_INTEREST": D("10")},                       # notional below median
        {"SECONDS_SINCE_LAST_TRADE": 60.0, "NOTIONAL_TRADED": D("5000"),
         "OPEN_INTEREST": None},                          # no open interest
        {"SECONDS_SINCE_LAST_TRADE": None, "NOTIONAL_TRADED": D("5000"),
         "OPEN_INTEREST": D("10")},                       # never traded
    ]
    L.activity_gate(rows, dist)
    assert [r["ACTIVITY_ELIGIBLE"] for r in rows] == [True, False, False,
                                                      False, False, False]
    assert "STALE_GT_3600s" in rows[1]["activity_reject"]
    assert "SLOWER_THAN_PROBED_MEDIAN" in rows[2]["activity_reject"]
    assert "NOTIONAL_BELOW_PROBED_MEDIAN" in rows[3]["activity_reject"]
    assert "NO_OPEN_INTEREST" in rows[4]["activity_reject"]
    assert "NO_LAST_TRADE_TIME" in rows[5]["activity_reject"]


def test_the_absolute_staleness_floor_is_reasoned_from_the_block_length():
    """20 minutes of resting: an hour without a trade is a prospective floor,
    fixed before the data, not tuned afterwards."""
    block_span = L.MAX_CYCLES * L.CYCLE_S + max(L.BURST_OFFSETS)
    assert L.MAX_SECONDS_SINCE_LAST_TRADE >= block_span
    assert L.MAX_SECONDS_SINCE_LAST_TRADE == 3600.0


def test_the_three_components_are_separate_and_never_one_opaque_score():
    row = L.derive_economics(
        {"BEST_BID": D("0.52"), "BEST_ASK": D("0.53")}, "0.01")
    for k in ("DISPLAYED_SPREAD_CAPTURE", "REBATE_LONG", "REBATE_SHORT",
              "DISPLAYED_PAIR_BUDGET", "SPREAD_TICKS", "PRICE_BAND"):
        assert k in row, k
    assert row["SPREAD_TICKS"] == 1 and row["PRICE_BAND"] == "NEAR_MID"
    assert row["DISPLAYED_PAIR_BUDGET"] == (row["DISPLAYED_SPREAD_CAPTURE"]
                                            + row["REBATE_LONG"]
                                            + row["REBATE_SHORT"])
    # activity and economics are decided by two different functions
    assert "def activity_gate(" in SRC and "def economic_gate(" in SRC
    for banned in ("total_score", "composite_score", "rank_score"):
        assert banned not in SRC.lower(), banned


def test_the_economic_gate_refuses_an_unpostable_book():
    rows = [
        {"market_state": "MARKET_STATE_OPEN", "BEST_BID": D("0.5"),
         "BEST_ASK": D("0.51"), "SPREAD_TICKS": 1, "BEST_BID_SIZE": D("5"),
         "BEST_ASK_SIZE": D("5"), "DISPLAYED_PAIR_BUDGET": D("1")},
        {"market_state": "MARKET_STATE_HALTED", "BEST_BID": D("0.5"),
         "BEST_ASK": D("0.51"), "SPREAD_TICKS": 1, "BEST_BID_SIZE": D("5"),
         "BEST_ASK_SIZE": D("5"), "DISPLAYED_PAIR_BUDGET": D("1")},
        {"market_state": "MARKET_STATE_OPEN", "BEST_BID": D("0.5"),
         "BEST_ASK": None, "SPREAD_TICKS": None, "BEST_BID_SIZE": D("5"),
         "BEST_ASK_SIZE": None, "DISPLAYED_PAIR_BUDGET": None},
    ]
    L.economic_gate(rows)
    assert [r["ECONOMICALLY_ELIGIBLE"] for r in rows] == [True, False, False]
    assert "NOT_OPEN" in rows[1]["economic_reject"]
    assert "ONE_SIDED" in rows[2]["economic_reject"]


def test_selection_seeks_price_regime_diversity_and_caps_tail():
    def m(i, band, secs):
        return {"native_event_id": str(i), "market_slug": "m%d" % i,
                "PRICE_BAND": band, "SECONDS_SINCE_LAST_TRADE": secs}
    rows = ([m(i, "TAIL", 10 + i) for i in range(10)]
            + [m(100 + i, "NEAR_MID", 50 + i) for i in range(3)]
            + [m(200 + i, "MODERATE", 80 + i) for i in range(3)])
    got = L.select_block_v2(rows, cap=6)
    bands = [g["PRICE_BAND"] for g in got]
    assert len(got) == 6
    assert bands.count("NEAR_MID") >= 2
    assert bands.count("MODERATE") >= 2
    assert bands.count("TAIL") <= 2              # the ceiling is enforced
    assert len({g["native_event_id"] for g in got}) == 6


def test_a_board_that_cannot_supply_the_regimes_is_reported_not_forced():
    def m(i):
        return {"native_event_id": str(i), "market_slug": "t%d" % i,
                "PRICE_BAND": "TAIL", "SECONDS_SINCE_LAST_TRADE": float(i)}
    got = L.select_block_v2([m(i) for i in range(10)], cap=6)
    assert len(got) == 2, "the TAIL ceiling must bind even when nothing else exists"
    assert "BAND_TARGET_SHORTFALL_REASON" in SRC
    assert "BOARD_DID_NOT_SUPPLY_ELIGIBLE_MARKETS_IN_BAND" in SRC


def test_the_shortlist_is_prospective_and_never_ranks_on_spread_alone():
    """Stage 1 ranks on imminence, which is knowable at selection time and is
    not an outcome. One market per event."""
    now = 1_000_000.0
    def c(i, band, start_off, som):
        return {"native_event_id": str(i), "market_slug": "s%d" % i,
                "price_band": band, "spread_over_mid": som,
                "game_start_time": None if start_off is None else
                datetime.datetime.utcfromtimestamp(now + start_off)
                .strftime("%Y-%m-%dT%H:%M:%SZ")}
    rows = [c(1, "NEAR_MID", 60, "0.9"), c(2, "NEAR_MID", 86400, "0.001"),
            c(3, "NEAR_MID", None, "0.002")]
    got = L.shortlist(rows, now)
    assert [g["market_slug"] for g in got] == ["s1", "s2", "s3"], \
        "imminent first, unknown last -- NOT tightest spread first"
    assert L.MAX_BOOK_PROBES > 0
    assert sum(L.SHORTLIST_PER_BAND.values()) <= L.MAX_BOOK_PROBES


def test_the_probe_receipts_are_sealed_like_the_discovery_bodies():
    i_probe = SRC.index("book_probe_receipts.jsonl.gz")
    i_status = SRC.index('res["BLOCK_STATUS"] != "OK"')
    assert i_probe < i_status
    assert "probe_rows.append" in SRC


def test_the_rate_budget_is_computed_before_any_venue_contact():
    i_budget = SRC.index("RATE BUDGET, COMPUTED BEFORE ANY VENUE CONTACT")
    i_client = SRC.index("with httpx.Client(")
    assert i_budget < i_client
    assert "TOTAL_WORST_CASE_RATE" in SRC and "NOT A SUM" in SRC
    total = L.MAX_DISCOVERY_PAGES + L.MAX_BOOK_PROBES \
        + L.N_LANES * L.MAX_CYCLES * len(L.BURST_OFFSETS)
    assert total <= L.MAX_VENUE_REQUESTS, total
    assert L.SPACING_S >= 2.5            # never weakened to fit the shortlist


def test_the_discovery_query_carries_the_venue_side_scope_filters():
    """THE BLOCK_2 BUG, pinned. Without active=true&closed=false, /v1/events is
    the whole historical archive: offset 0 was 2025-10-31 and offset 64000 was
    still 2026-08, so 8,532 of 8,532 rows came back MARKET_STATUS_RESOLVED."""
    assert L.DISCOVERY_QUERY["active"] == "true"
    assert L.DISCOVERY_QUERY["closed"] == "false"
    q = L.query_for(300)
    assert q["active"] == "true" and q["closed"] == "false"
    assert q["limit"] == L.PAGE_LIMIT and q["offset"] == 300
    # and the archive crawl is gone, not merely unused
    assert "END_PROBE_STEPS" not in SRC and "FRAME_PAGES_BACK" not in SRC


def test_offset_is_the_only_pagination_mechanism():
    """page= and skip= were shown in Phase 2E to return the identical first
    page with HTTP 200. They must not come back."""
    assert '"page"' not in SRC and "'page'" not in SRC
    assert '"skip"' not in SRC and "'skip'" not in SRC
    assert set(L.query_for(0)) == {"active", "closed", "limit", "offset"}
    assert [L.query_for(i * L.PAGE_LIMIT)["offset"] for i in range(3)] \
        == [0, L.PAGE_LIMIT, 2 * L.PAGE_LIMIT]


def test_a_query_parameter_that_was_sent_is_not_evidence_about_the_answer():
    """The frame the venue RETURNED is re-counted, and a payload that
    contradicts the scope refuses to capture."""
    assert "scope_census" in SRC
    assert "FAILED_QUERY_SCOPE_NOT_HONOURED" in SRC
    assert "FAILED_NO_OPEN_MARKET_ROWS" in SRC
    resolved = {"id": "1", "closed": True, "primaryTag": None,
                "markets": [{"status": "MARKET_STATUS_RESOLVED"}]}
    c = L.scope_census([resolved])
    assert c["EVENTS_WITH_CLOSED_TRUE"] == 1
    assert c["MARKETS_OPEN"] == 0 and c["MARKETS_RESOLVED"] == 1
    assert c["MARKETS_WITH_BID_AND_ASK"] == 0


# ============ primaryTag: REAL SHAPES, NOT STRING-ONLY FIXTURES =============
def test_a_real_venue_primary_tag_object_is_parsed_field_by_field():
    for key in ("primaryTag_nfl", "primaryTag_2gr"):
        raw = SHAPES[key]
        assert isinstance(raw, dict), key
        t = L.normalize_tag(raw)
        assert t["PRIMARY_TAG_SHAPE"] == "OBJECT", key
        assert t["PRIMARY_TAG_ID"] == str(raw["id"])
        assert t["PRIMARY_TAG_LABEL"] == raw["label"]
        assert t["PRIMARY_TAG_LEAGUE"] == raw["league"]["slug"]
        assert t["PRIMARY_TAG_SPORT_ID"] == raw["league"]["sportId"]
        assert t["SPORT_KEY"] == "sportId:%d" % raw["league"]["sportId"]
        assert isinstance(t["SPORT_KEY"], str)      # hashable, by construction


def test_the_whole_object_is_never_serialized_and_called_a_sport():
    t = L.normalize_tag(SHAPES["primaryTag_nfl"])
    for v in t.values():
        assert not (isinstance(v, str) and v.lstrip().startswith("{"))
    body = SRC[SRC.index("def normalize_tag("):SRC.index("def tag_slugs(")]
    for banned in ("json.dumps", "str(raw)", "repr(", "sort_keys"):
        assert banned not in body, banned
    # str() appears once, narrowing a known id field -- never the object
    assert body.count("str(") == 1 and "str(tid)" in body


def test_a_bare_string_tag_is_supported_only_as_the_compatibility_case():
    t = L.normalize_tag("nfl")
    assert t["PRIMARY_TAG_SHAPE"] == "STRING"
    assert t["SPORT_KEY"] == "nfl" and t["PRIMARY_TAG_LABEL"] == "nfl"
    assert "COMPATIBILITY CASE ONLY" in SRC


def test_a_null_tag_is_classified_not_guessed():
    t = L.normalize_tag(None)
    assert t["PRIMARY_TAG_SHAPE"] == "NULL"
    assert t["SPORT_KEY"] == L.NOT_IDENTIFIED
    assert t["PRIMARY_TAG_ID"] is None and t["PRIMARY_TAG_LEAGUE"] is None


def test_an_unknown_object_shape_is_not_identified_and_does_not_crash():
    for raw in ({"unexpected": {"nested": 1}}, {}, 17, ["nfl"], object()):
        t = L.normalize_tag(raw)
        assert t["PRIMARY_TAG_SHAPE"] == "UNKNOWN", raw
        assert t["SPORT_KEY"] == L.NOT_IDENTIFIED
    # a sport id alone is not prose, and prose alone is not a sport id
    t = L.normalize_tag({"label": "Boxing"})
    assert t["PRIMARY_TAG_SHAPE"] == "OBJECT"
    assert t["SPORT_KEY"] == L.NOT_IDENTIFIED          # no league.sportId given
    assert t["PRIMARY_TAG_LABEL"] == "Boxing"


def test_a_real_open_quoted_market_is_admitted_with_an_object_tag():
    ev = {"id": "77", "primaryTag": SHAPES["open_quoted_event_primaryTag"],
          "tags": SHAPES["tags"], "markets": [SHAPES["open_quoted_market_row"]]}
    got = L.candidates([ev], 0)
    assert len(got) == 1
    c = got[0]
    assert c["PRIMARY_TAG_SHAPE"] == "OBJECT"
    assert c["sport"].startswith("sportId:")
    assert c["selected_from"] == "discovery_payload"
    assert D(c["spread"]) > 0 and c["price_band"]


def test_select_block_does_not_raise_on_real_object_shaped_tags():
    """The exact crash BLOCK_2 was one successful frame away from:
    TypeError: unhashable type: 'dict'."""
    evs = []
    for i in range(6):
        m = dict(SHAPES["open_quoted_market_row"])
        m["slug"] = "%s-%d" % (m["slug"], i)
        evs.append({"id": str(i),
                    "primaryTag": SHAPES["primaryTag_nfl"] if i % 2 else
                                  SHAPES["open_quoted_event_primaryTag"],
                    "tags": SHAPES["tags"], "markets": [m]})
    # plus a null-tag event, which must neither crash nor be silently dropped
    m = dict(SHAPES["open_quoted_market_row"])
    m["slug"] = m["slug"] + "-null"
    evs.append({"id": "99", "primaryTag": None, "tags": [], "markets": [m]})
    cands = L.candidates(evs, 0)
    assert len(cands) == 7
    block = L.select_block(cands, cap=4)              # must not raise
    assert len(block) == 4
    assert len({b["native_event_id"] for b in block}) == 4
    assert all(isinstance(b["sport"], str) for b in block)
    assert L.NOT_IDENTIFIED in {c["sport"] for c in cands}


def test_the_tag_slugs_are_read_by_name_never_stringified():
    slugs = L.tag_slugs({"tags": SHAPES["tags"]})
    assert slugs and all(isinstance(s, str) for s in slugs)
    assert all(not s.startswith("{") for s in slugs)
    assert L.tag_slugs({"tags": [{"no_slug": 1}, None, 5]}) == []


def _drive(pages_by_offset):
    """Run the real discover() against a scripted venue. No network."""
    calls = []

    def fake_get(http, path, params=None, **kw):
        calls.append((path, dict(params or {})))
        evs = pages_by_offset.get(params.get("offset"), [])
        return {"http_status": 200, "path": path, "params": params,
                "body": {"events": evs}, "response_bytes": 1,
                "response_sha256": "x", "error": None}

    # get_paced calls its OWN module's collector, so both are swapped
    saved = [(m, m._get) for m in (L.C, L.B.C)]
    for m, _ in saved:
        m._get = fake_get
    try:
        res, pages, samples = {}, [], []
        evs = L.discover(None, L.B.AdaptivePacer(base=0.0),
                         L.Budget(L.MAX_VENUE_REQUESTS), pages, samples, res)
        return evs, res, pages, calls
    finally:
        for m, fn in saved:
            m._get = fn


def _ev(i, closed=False, status="MARKET_STATUS_OPEN", quoted=True):
    m = dict(SHAPES["open_quoted_market_row"])
    m["slug"], m["status"] = "m%d" % i, status
    if not quoted:
        m = {k: v for k, v in m.items()
             if k not in ("bestBidQuote", "bestAskQuote")}
    return {"id": str(i), "closed": closed,
            "primaryTag": SHAPES["open_quoted_event_primaryTag"],
            "tags": SHAPES["tags"], "markets": [m]}


def test_discovery_walks_offsets_forward_and_stops_at_a_real_boundary():
    pages = {0: [_ev(i) for i in range(100)],
             100: [_ev(100 + i) for i in range(40)],
             200: []}
    evs, res, recs, calls = _drive(pages)
    assert [c[1]["offset"] for c in calls] == [0, 100, 200]
    assert all(c[1]["active"] == "true" and c[1]["closed"] == "false"
               for c in calls)
    assert len(evs) == 140
    assert res["DISCOVERY_LIST_EXHAUSTED"] == "YES"
    assert res["FIRST_TERMINAL_OFFSET"] == 200
    assert res["PAGINATION_ADVANCES"] == "YES"
    assert res["EVENTS_DISCOVERED"] == 140
    assert res["OPEN_MARKET_ROWS"] == 140 and res["RESOLVED_MARKET_ROWS"] == 0
    assert res["EVENTS_WITH_CLOSED_TRUE"] == 0
    assert res["MARKETS_WITH_BID_AND_ASK"] == 140
    assert res["QUERY_FILTERS"] == {"active": "true", "closed": "false"}


def test_a_payload_that_contradicts_the_query_scope_is_counted_as_such():
    """BLOCK_2's frame, in miniature: the parameters were sent and the venue
    still returned closed, resolved rows."""
    _, res, _, _ = _drive({0: [_ev(i, closed=True,
                                   status="MARKET_STATUS_RESOLVED",
                                   quoted=False) for i in range(3)], 100: []})
    assert res["EVENTS_WITH_CLOSED_TRUE"] == 3
    assert res["OPEN_MARKET_ROWS"] == 0
    assert res["RESOLVED_MARKET_ROWS"] == 3
    assert res["MARKETS_WITH_BID_AND_ASK"] == 0
    # and the runner refuses to capture on exactly those two conditions
    assert 'res.get("EVENTS_WITH_CLOSED_TRUE")' in SRC
    assert 'res.get("MARKETS_OPEN")' in SRC


def test_the_walk_is_bounded_and_never_enumerates_the_archive():
    evs, res, recs, calls = _drive({i * 100: [_ev(i)] for i in range(400)})
    assert len(calls) == L.MAX_DISCOVERY_PAGES
    assert res["DISCOVERY_LIST_EXHAUSTED"] == "NO"
    assert "Enumeration of history is not the objective" in SRC


def test_an_undersized_block_is_a_hard_failure_not_a_success():
    assert "FAILED_BLOCK_TOO_SMALL" in SRC
    assert "REQUIRED_BLOCK_SIZE" in SRC
    assert "return 2" in SRC                      # non-zero exit
    assert "EXIT NON-ZERO" in SRC


def test_diagnostics_are_sealed_even_when_the_block_fails():
    """The evidence that explains a failure must survive the failure."""
    i_pages = SRC.index("discovery_pages.jsonl.gz")
    i_status = SRC.index('res["BLOCK_STATUS"] != "OK"')
    assert i_pages < i_status, "diagnostics must be written before the exit path"
    assert "discovery_body_samples.jsonl.gz" in SRC


def test_a_failed_block_is_marked_economically_unusable():
    assert "ECONOMICALLY_USABLE" in SRC
    assert "SCIENTIFIC_OBSERVATIONS" in SRC


def test_the_required_block_size_is_not_quietly_loosened():
    assert L.REQUIRED_BLOCK_SIZE >= 4
    assert L.REQUIRED_BLOCK_SIZE <= L.N_LANES


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

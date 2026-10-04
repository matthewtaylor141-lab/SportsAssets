"""FIXTURE rows for the position-room assembly. EVERY ROW HERE IS SYNTHETIC
TEST DATA (labelled FIXTURE); it is used only by tests and by the screenshot
harness, never by the serving code.

The owner's example: Derek buys the Yankees at 50c; Xavier rests a Yankees
sell at 70c, a Red Sox hedge bid is partially filled at 35c, and a further
Red Sox bid at 30c is proposed. On the venue's real two-way baseball
contract (`aec-mlb-bos-nyy-...`), the LONG row names the Red Sox and the
SHORT row names the Yankees -- so YES Yankees is the SHORT and YES Red Sox the
LONG of the SAME contract, exactly as in production (tests/fixtures/
venue_native_2026-09-29.json, aec-mlb-bos-nyy-2026-09-29).
"""
from __future__ import annotations

NOW = 1_791_158_400.0          # 2026-10-05T00:00:00Z
EVENT = "mlb-bos-nyy-2026-10-04"
SLUG = "aec-mlb-bos-nyy-2026-10-04"
UNMAPPED = "aec-mlb-sea-tex-2026-10-04"
G_NYY = "paper_g_fixture_nyy"
G_BOS = "paper_g_fixture_bos_hedge"


def premap_rows():
    base = {"event_slug": EVENT,
            "event_title": "BOS Red Sox vs. NY Yankees",
            "kind": "side", "line": "",
            "sports_type": "baseball_team_full_game_winner",
            "game_start": NOW - 5400, "market_slug": SLUG}
    return [dict(base, intent="ORDER_INTENT_BUY_LONG",
                 side_norm="boston red sox", team_abbr="bos",
                 team_name="boston red sox", team_safe_name="red sox",
                 team_id=111, team_league="mlb"),
            dict(base, intent="ORDER_INTENT_BUY_SHORT",
                 side_norm="new york yankees", team_abbr="nyy",
                 team_name="new york yankees", team_safe_name="yankees",
                 team_id=147, team_league="mlb")]


def book(age_s=12.0):
    """The LONG (Red Sox) book: bid 34c / offer 36c, so the Yankees side
    bids 64c and offers 66c."""
    return {SLUG: {"obs_id": 9001, "us_market_slug": SLUG,
                   "observed_at": NOW - age_s,
                   "source": "FIXTURE_SYNTHETIC_BOOK",
                   "bids": [{"px": {"value": "0.34"}, "qty": "2500"},
                            {"px": {"value": "0.33"}, "qty": "4000"}],
                   "offers": [{"px": {"value": "0.36"}, "qty": "1800"},
                              {"px": {"value": "0.37"}, "qty": "3000"}],
                   "market_state": "MARKET_STATE_OPEN"}}


def _o(ref, *, group, role, direction, side, qty, filled, limit, state,
       raw, at, avg=None, fees=0.0, decision=None, slug=SLUG,
       source="paper_orders", book="PAPER", venue="POLYMARKET", tif="GTD",
       otype="RESTING", reason=None):
    return {"order_ref": ref, "source": source, "book": book, "venue": venue,
            "group_id": group, "role": role, "direction": direction,
            "holding_side": side, "slug": slug, "order_type": otype,
            "tif": tif, "qty": qty, "filled_qty": filled, "limit": limit,
            "wire_price": limit if side == "LONG" else round(1 - limit, 6),
            "raw_state": raw, "state": state, "decision_id": decision,
            "created_at": at, "updated_at": at, "expires_at": at + 7200,
            "terminal_reason": reason, "strategy": "DEREK_ENTRY_POLICY_V2",
            "avg_fill": avg, "fees_usd": fees}


def paper_orders():
    t = NOW - 3600
    return [
        _o("paper_ord_fx_entry", group=G_NYY, role="ENTRY", direction="BUY",
           side="SHORT", qty=1000, filled=1000, limit=0.50, state="FILLED",
           raw="FILLED", at=t, avg=0.50, fees=3.5,
           decision="paper_dec_fx_entry", tif="IOC", otype="MARKETABLE"),
        _o("paper_ord_fx_prot_old", group=G_NYY, role="STANDING_PROTECTION",
           direction="SELL", side="SHORT", qty=1000, filled=0, limit=0.72,
           state="CANCELLED", raw="CANCELED", at=t + 60,
           reason="CANCEL_FOR_REPLACEMENT"),
        _o("paper_ord_fx_prot", group=G_NYY, role="STANDING_PROTECTION",
           direction="SELL", side="SHORT", qty=1000, filled=0, limit=0.70,
           state="RESTING", raw="RESTING", at=t + 120),
        _o("paper_ord_fx_hedge", group=G_BOS, role="HEDGE", direction="BUY",
           side="LONG", qty=400, filled=150, limit=0.35, state="PARTIAL",
           raw="PARTIALLY_FILLED", at=t + 600, avg=0.35, fees=0.5),
        _o("paper_dec_fx_rsox", group=None, role="ENTRY", direction="BUY",
           side="LONG", qty=600, filled=0, limit=0.30, state="PROPOSED",
           raw="DECIDED_ENTER_NOT_ORDERED", at=NOW - 120,
           decision="paper_dec_fx_rsox",
           source="paper_decisions (Derek ENTER, no order yet)", tif=None,
           otype=None),
        # AN UNMAPPED MARKET: no catalogue row, so it is UNGROUPED
        _o("paper_ord_fx_unmapped", group="paper_g_fixture_sea", role="ENTRY",
           direction="BUY", side="LONG", qty=200, filled=200, limit=0.55,
           state="FILLED", raw="FILLED", at=t + 30, avg=0.55, fees=0.6,
           slug=UNMAPPED, tif="IOC", otype="MARKETABLE"),
    ]


def paper_fills():
    t = NOW - 3600
    return [
        {"fill_ref": "paper_fill_fx_1", "order_ref": "paper_ord_fx_entry",
         "group_id": G_NYY, "direction": "BUY", "holding_side": "SHORT",
         "slug": SLUG, "qty": 1000.0, "price": 0.50, "fee_usd": 3.5,
         "at": t + 3, "source": "paper_fills (SIMULATOR)"},
        {"fill_ref": "paper_fill_fx_2", "order_ref": "paper_ord_fx_hedge",
         "group_id": G_BOS, "direction": "BUY", "holding_side": "LONG",
         "slug": SLUG, "qty": 150.0, "price": 0.35, "fee_usd": 0.5,
         "at": t + 700, "source": "paper_fills (SIMULATOR)"},
        {"fill_ref": "paper_fill_fx_3", "order_ref": "paper_ord_fx_unmapped",
         "group_id": "paper_g_fixture_sea", "direction": "BUY",
         "holding_side": "LONG", "slug": UNMAPPED, "qty": 200.0,
         "price": 0.55, "fee_usd": 0.6, "at": t + 33,
         "source": "paper_fills (SIMULATOR)"},
    ]


def assessment(*, fresh=True):
    # a FRESH assessment 10 s ago on a 6 s old probability is CURRENT at NOW
    # (source NOW-16 + 30 s limit); the stale variant is a HISTORICAL row
    # that recorded HOLD on stale evidence 40 s ago -- read as STALE
    at = NOW - (10 if fresh else 40)
    alts = [
        {"action": "HOLD", "rankable": True, "value_usd": 630.0,
         "ev_basis": "FRESH_CURRENT_PROBABILITY", "blocker": None},
        {"action": "EXIT", "rankable": fresh, "value_usd": 624.0,
         "fees_usd": 3.6, "qty": 1000,
         "blocker": None if fresh else
         "MEASURE_STALE_OR_ABSENT_NO_DISCRETIONARY_SALE"},
        {"action": "REDUCE", "rankable": fresh, "value_usd": 627.1,
         "fees_usd": 1.8, "qty": 500,
         "blocker": None if fresh else
         "MEASURE_STALE_OR_ABSENT_NO_DISCRETIONARY_SALE"},
        {"action": "VERIFIED_HEDGE", "rankable": False, "value_usd": None,
         "blocker": ("NO_HEDGE_WITH_PROVEN_SETTLEMENT_COMPATIBILITY_"
                     "SEARCH_NOT_RUN_ON_THIS_PATH")},
        {"action": "REALLOCATE", "rankable": False, "mode": "SHADOW",
         "recommended": False,
         "blocker": "NO_OTHER_CURRENTLY_QUALIFIED_OPPORTUNITY",
         "value_usd": 126.5},
    ]
    return {"assessment_id": "xma:fixture0001", "position_kind": "PAPER",
            "group_id": G_NYY, "review_id": "paperrev:fixture1",
            "thesis_id": "xth:fixture", "assessed_at": at,
            "trigger": "MARKET_EVENT", "due_at": at - 2,
            "review_latency_s": 2.0, "latency_bound_s": 60.0,
            "within_bound": True,
            "evidence_state": ("FRESH_CURRENT_PROBABILITY" if fresh else
                               "STALE_ENTRY_TIME_PROBABILITY"),
            "probability": 0.63, "probability_source":
            "PINNAPI_FEED (FIXTURE)", "probability_age_s": 6.0,
            "venue_economics": {"source": "paper_book_observations",
                                "best_exit": 0.64},
            "thesis_state": "STILL_VALID",
            "thesis_detail": {"state": "STILL_VALID",
                              "edge_moved_pp": 2.1},
            "alternatives": alts,
            "recommendation": "HOLD",
            "discretionary_permitted": fresh,
            "reallocate": {"mode": "SHADOW", "recommended": False},
            "policy": {"status": "READY_FOR_OWNER_APPROVAL"}}


def thesis():
    return {"thesis_id": "xth:fixture", "entry_probability": 0.56,
            "probability_source": "PINNAPI_FEED (FIXTURE)",
            "entry_ev_usd": 56.5, "entered_at": NOW - 3597,
            "evidence_expires_at": NOW - 3567,
            "thesis_expires_at": NOW - 5400 + 0,
            "expiry_basis": "PINNACLE_30S_RULE (FIXTURE)"}


def review():
    return {"review_id": "paperrev:fixture1", "group_id": G_NYY,
            "reviewed_at": NOW - 40, "recommendation": "HOLD",
            "selection": {"selected": "HOLD",
                          "selection_reason": ("HOLD ranks above REDUCE by "
                                               "2.90 USD on the fresh "
                                               "probability (FIXTURE)"),
                          "margin_over_runner_up": 2.9},
            "action": {"taken": "KEEP_STANDING",
                       "order_id": "paper_ord_fx_prot"},
            "exposure": {"open_qty": 1000}, "_table": "paper_xavier_reviews"}


def game_row(age_s=900.0, state="In Progress"):
    return {EVENT: {"event_state_raw": state, "play_has_begun": True,
                    "home_team": "New York Yankees",
                    "away_team": "Boston Red Sox",
                    "source": "MLB Stats API, schedule (FIXTURE)",
                    "source_url": "https://statsapi.mlb.com/api/v1/schedule",
                    "retrieved_at": NOW - age_s,
                    "_table": "venue_fixture_metadata",
                    "_binding": "keyed by the room's venue event (FIXTURE)"}}


def matchups():
    teams = [{"name": "Red Sox", "team_id": 111, "league": "mlb",
              "initials": "BOS", "logo": {
                  "url": "/api/command/agents/static/mlb-logo-111.svg",
                  "kind": "logo"}},
             {"name": "Yankees", "team_id": 147, "league": "mlb",
              "initials": "NYY", "logo": {
                  "url": "/api/command/agents/static/mlb-logo-147.svg",
                  "kind": "logo"}}]
    return {SLUG: teams}


def raw_paper(*, fresh=True, game_age_s=900.0, book_age_s=12.0):
    return {"book": "PAPER", "venue": "POLYMARKET", "now": NOW,
            "account_id": "paper_acct_main",
            "orders": paper_orders(), "fills": paper_fills(),
            "settlements": [], "premap": premap_rows(),
            "books": book(book_age_s),
            "xavier": {"assessments": {G_NYY: assessment(fresh=fresh)},
                       "theses": {G_NYY: thesis()},
                       "reviews": {G_NYY: review()}, "cadence_s": 60.0,
                       # the paper session's freshness limit, as recorded
                       "limit_s": 30.0,
                       "schema_present": True},
            "karen": [{"challenge_id": "kc:fixture", "detector":
                       "HOLD_ON_STALE_PROBABILITY", "target_agent": "XAVIER",
                       "target_kind": "paper_xavier_reviews",
                       "target_id": "paperrev:older", "_group_id": G_NYY,
                       "severity": "HIGH", "state": "OPEN",
                       "claim": "Review recommended HOLD on a stale "
                                "probability (FIXTURE). Prove it.",
                       "challenged_at": NOW - 900}],
            "audrey": {"findings": [], "reconciliations": [],
                       "postmortems": []},
            "eddie": {"present": False, "rows": []},
            "game_state": game_row(game_age_s), "matchups": matchups()}


def raw_actual():
    t = NOW - 3590
    o = _o("mirror_fx_entry", group=G_NYY, role="ENTRY", direction="BUY",
           side="SHORT", qty=1, filled=1, limit=0.50, state="FILLED",
           raw="FILLED", at=t, avg=0.50, fees=0.02,
           source="execmirror_orders", book="ACTUAL", tif="IOC",
           otype="MARKETABLE")
    p = _o("mirror_fx_prot", group=G_NYY, role="STANDING_PROTECTION",
           direction="SELL", side="SHORT", qty=1, filled=0, limit=0.70,
           state="CANCEL_PENDING", raw="CANCEL_REQUESTED", at=t + 120,
           source="execmirror_orders", book="ACTUAL")
    return {"book": "ACTUAL", "venue": "POLYMARKET", "now": NOW,
            "account_id": None, "orders": [o, p],
            "fills": [{"fill_ref": "emf:fx1", "order_ref": "mirror_fx_entry",
                       "group_id": G_NYY, "direction": "BUY",
                       "holding_side": "SHORT", "slug": SLUG, "qty": 1.0,
                       "price": 0.50, "fee_usd": 0.02, "at": t + 2,
                       "source": "execmirror_fills (VENUE_ORDER_RECORD)"}],
            "settlements": [], "premap": premap_rows(), "books": book(),
            "xavier": {"assessments": {}, "theses": {}, "reviews": {},
                       "cadence_s": 60.0, "schema_present": True},
            "karen": [], "audrey": {"findings": [], "reconciliations": [
                {"group_id": G_NYY, "venue": "POLYMARKET",
                 "status": "MATCHED", "reconciled_at": NOW - 30,
                 "discrepancies": []}], "postmortems": []},
            "eddie": {"present": False, "rows": []},
            "game_state": game_row(30.0), "matchups": matchups()}

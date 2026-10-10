"""KALSHI REP PRODUCTION CONTRACT (2026-10-07) -- WEBSOCKET BOOKS, SAME-
MARKET NETTING, PUBLISHED FEES, READ-ONLY BOUNDARY.

The contract is recorded as a source under research/kalshi_canonical_venue/
REP_PRODUCTION_CONTRACT_2026-10-07 (with the public docs it points to).
These are its regressions:

  WS      snapshot -> CURRENT; deltas in sequence applied; an induced
          sequence gap -> every book of the sid GAP, later deltas ignored,
          resubscribed, CURRENT only on the fresh snapshot; a disconnect ->
          GAP, never reused; the signed handshake; account limits and the
          pacing derived from them
  YES/NO  Yankees YES market != Rays NO market (instrument and book);
          Yankees YES == Rays NO only by the payoff vector; same-market
          YES / NO net; same-market NO ask = 1 - best YES bid; same-market
          YES + NO never an arb; cross-market YES / NO may price apart and
          the route may choose Rays NO when it is cheaper all-in
  FEES    the published schedule x the series / event multiplier,
          versioned; unknown = ineligible; no account discounts
  BOUNDARY the WS runtime imports nothing that can place, cancel, fund or
          authorize; dedicated-only (never in the shared workers or API)
"""
from __future__ import annotations

import ast
import asyncio
import base64
import json
import pathlib
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from sportsassets import canonical_claims as CC
from sportsassets import kalshi_fees as KF
from sportsassets import kalshi_market_data as KMD
from sportsassets import kalshi_ws as KWS
from sportsassets.agents import adriana_arb as A
from sportsassets.agents import adriana_claims as AC
from sportsassets.redteam import exposure as X
from sportsassets.red_team.models import ClaimExposure

D = Decimal
ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
NOW = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc).timestamp()
START = NOW + 4 * 3600
FX = CC.Fixture(event_key="MLB:2026-10-08T00:00Z:TB@NYY", sport="BASEBALL",
                league="MLB", start_epoch=START, outcome_kind="TWO_WAY",
                home="NYY", away="TB")
TERMS = {"overtime_included": True, "draw_rule": "SCALAR_0_50",
         "void_rule": "SCALAR_0_50", "postponement_window_hours": 48.0,
         "postponement_payout": "SCALAR_0_50",
         "verification_sources": ["MLB"]}
#: the venue's live record for KXMLBGAME on 2026-10-07 (captured in
#: live_series_fee_changes_2026-10-07.jsonl)
MLB_CHANGES = [
    {"fee_multiplier": 0.5, "fee_type": "quadratic_with_maker_fees",
     "id": "38032af2-e3fa-4659-9280-da64300b544c",
     "scheduled_ts": "2026-08-07T04:59:45.131Z", "series_ticker": "KXMLBGAME"},
    {"fee_multiplier": 1, "fee_type": "quadratic_with_maker_fees",
     "id": "16ab5717-ef1d-4323-9926-d23e3147a4f7",
     "scheduled_ts": "2025-10-04T07:00:00Z", "series_ticker": "KXMLBGAME"}]
MLB_TERMS = KF.effective_terms(series_ticker="KXMLBGAME",
                               event_ticker="KXMLBGAME-26OCT072000TBNYY",
                               at=NOW, series_changes=MLB_CHANGES,
                               event_changes=[])


def inst(venue, market, side, subject, asks, *, fee_terms=MLB_TERMS):
    return CC.Instrument(venue=venue, market_id=market, side=side,
                         subject=subject, settlement=dict(TERMS),
                         settlement_status="PROVEN",
                         mapping_status="ESTABLISHED", asks=tuple(asks),
                         observed_at=NOW, book_basis="TEST_BOOK",
                         sport="BASEBALL",
                         fee_terms=fee_terms if venue == "KALSHI" else None)


# ── WS books: snapshot, sequence, gap, resubscribe, reconnect ───────────

def snap(sid, seq, t, yes, no):
    return {"type": "orderbook_snapshot", "sid": sid, "seq": seq,
            "msg": {"market_ticker": t, "market_id": "u-" + t,
                    "yes_dollars_fp": yes, "no_dollars_fp": no}}


def delta(sid, seq, t, price, d, side):
    return {"type": "orderbook_delta", "sid": sid, "seq": seq,
            "msg": {"market_ticker": t, "market_id": "u-" + t,
                    "price_dollars": price, "delta_fp": d, "side": side}}


def test_snapshot_then_deltas_in_sequence_keep_the_book_current():
    b = KWS.WsBooks(clock=lambda: NOW)
    b.on_connected()
    assert b.on_message(snap(1, 1, "K-NYY", [["0.4300", "100.00"]],
                             [["0.5500", "80.00"]])) == "SNAPSHOT"
    assert b.current("K-NYY")["ok"]
    assert b.on_message(delta(1, 2, "K-NYY", "0.4400", "50.00", "yes")) \
        == "DELTA"
    assert b.on_message(delta(1, 3, "K-NYY", "0.4300", "-100.00", "yes")) \
        == "DELTA"
    ob = b.current("K-NYY")["book"]["orderbook_fp"]
    assert ob["yes_dollars"] == [["0.4400", "50.00"]]
    # the one reader derives the asks from the bids (one pool)
    book = KMD.book_from_orderbook(b.current("K-NYY")["book"],
                                   observed_at=NOW)
    assert book["no_asks"][0][0] == D("1") - D("0.44")      # NO ask
    assert book["yes_asks"][0][0] == D("1") - D("0.55")     # YES ask


def test_an_induced_sequence_gap_makes_every_book_of_the_sid_stale():
    b = KWS.WsBooks(clock=lambda: NOW)
    b.on_connected()
    b.on_message(snap(7, 1, "K-NYY", [["0.43", "100"]], [["0.55", "80"]]))
    b.on_message(snap(7, 2, "K-TB", [["0.44", "100"]], [["0.54", "80"]]))
    assert b.on_message(delta(7, 4, "K-NYY", "0.45", "10", "yes")) == "GAP"
    for t in ("K-NYY", "K-TB"):
        cur = b.current(t)
        assert not cur["ok"] and cur["state"] == KWS.GAP
        assert cur["why"] == KWS.R_SEQ_GAP and cur["book"] is None
    # later deltas on the broken sid never resurrect the old book
    assert b.on_message(delta(7, 5, "K-TB", "0.44", "-100", "yes")) == \
        "IGNORED_NOT_CURRENT"
    assert set(b.resubscribe) == {"K-NYY", "K-TB"}
    # the fresh snapshot (a new subscription) restores CURRENT
    b.on_message(snap(9, 1, "K-NYY", [["0.46", "20"]], [["0.52", "30"]]))
    assert b.current("K-NYY")["ok"] and not b.current("K-TB")["ok"]


def test_a_disconnect_marks_every_book_gap_and_never_reuses_it():
    b = KWS.WsBooks(clock=lambda: NOW)
    b.on_connected()
    b.on_message(snap(1, 1, "K-NYY", [["0.43", "100"]], []))
    b.on_disconnected()
    assert not b.current("K-NYY")["ok"]
    b.on_connected()      # connected != current
    assert not b.current("K-NYY")["ok"]
    assert b.current("K-NYY")["state"] == KWS.GAP


class FakeWS:
    def __init__(self, script):
        self.script = list(script)
        self.sent = []
        self.closed = False

    async def send(self, s):
        self.sent.append(json.loads(s))

    async def recv(self):
        if not self.script:
            raise ConnectionError("closed")
        return json.dumps(self.script.pop(0))

    async def close(self):
        self.closed = True


def test_the_subscriber_asks_the_sid_for_a_snapshot_after_a_gap_and_waits():
    """(RC6.2; was test_the_subscriber_resubscribes_after_a_gap_and_waits_
    for_the_snapshot, which pinned unsubscribe + subscribe.) docs.kalshi.com
    changelog 2025-09-25: a repeated subscribe merges into the existing sid
    ("If passing the same market tickers as before, no action will be
    taken") -- so the repair is update_subscription get_snapshot on the same
    sid ("returns an orderbook_snapshot for the requested market_tickers
    without modifying the subscription"). The venue's replies follow the
    documented protocol: `subscribed` names our command, `ok` takes the
    sid's next seq, the fresh snapshot follows on sid 3."""
    ws = FakeWS([
        {"type": "subscribed", "id": 1,
         "msg": {"channel": "orderbook_delta", "sid": 3}},
        snap(3, 1, "K-NYY", [["0.43", "100"]], [["0.55", "80"]]),
        delta(3, 3, "K-NYY", "0.44", "5", "yes"),          # gap (2 missing)
        {"type": "ok", "id": 2, "sid": 3, "seq": 4},
        snap(3, 5, "K-NYY", [["0.47", "10"]], [["0.51", "10"]])])

    async def connect():
        return ws
    books = KWS.WsBooks(clock=lambda: NOW)
    sub = KWS.Subscriber(connect, books, wanted=lambda: ["K-NYY"],
                         clock=lambda: NOW)

    async def go():
        with pytest.raises(ConnectionError):
            await sub.session()
    asyncio.run(go())
    cmds = [m["cmd"] for m in ws.sent]
    assert cmds == ["subscribe", "update_subscription"]
    assert ws.sent[0]["params"] == {"channels": ["orderbook_delta"],
                                    "market_tickers": ["K-NYY"]}
    assert ws.sent[1]["params"] == {"sid": 3, "market_tickers": ["K-NYY"],
                                    "action": "get_snapshot"}
    assert sub.resubscribes == 1 and books.stats["gaps"] == 1
    assert books.stats["snapshots"] == 2
    # the session ended (socket closed): connected != current -> GAP
    assert ws.closed and not books.current("K-NYY")["ok"]


def test_the_handshake_is_signed_over_timestamp_get_and_the_ws_path():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    h = KWS.auth_headers("kid-1", key, "GET", KWS.WS_PATH,
                         ts_ms="1791403200000")
    assert h["KALSHI-ACCESS-KEY"] == "kid-1"
    assert h["KALSHI-ACCESS-TIMESTAMP"] == "1791403200000"
    key.public_key().verify(
        base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]),
        b"1791403200000GET/trade-api/ws/v2",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    # the same signing message as the credentialed client's (read here,
    # never imported by the WS runtime)
    from sportsassets import kalshi_venue as KV
    assert KWS.signing_message("1", "get", "/trade-api/v2/x?y=1") == \
        KV.signing_message("1", "GET", "/trade-api/v2/x")


def test_account_limits_are_recorded_and_drive_rest_pacing():
    lim = KWS.parse_limits({"usage_tier": "advanced",
                            "read": {"refill_rate": 300,
                                     "bucket_capacity": 900},
                            "write": {"refill_rate": 300,
                                      "bucket_capacity": 900},
                            "grants": []}, as_of=NOW)
    assert lim["usage_tier"] == "advanced"
    assert lim["websocket_connection_limit"] == "NOT_RETURNED_BY_VENUE"
    assert lim["source"] == "GET /trade-api/v2/account/limits"
    p = KWS.pacing(lim)
    assert p["rest_requests_per_s"] == 30.0
    assert p["basis"].startswith("ACCOUNT_LIMITS_READ_BUCKET")
    fb = KWS.pacing(None)
    assert fb["basis"].startswith("FALLBACK_NO_ACCOUNT_LIMITS_READ")
    # the default 200-connection cap is never written down as ours
    assert not any(v == 200 for v in lim.values())


# ── the read-only boundary and the dedicated-only runtime ───────────────

def _closure(start: list) -> set:
    seen, stack = set(), list(start)
    while stack:
        m = stack.pop()
        if m in seen:
            continue
        seen.add(m)
        path = ROOT / (m.replace(".", "/") + ".py")
        if not path.exists():
            continue
        tree = ast.parse(path.read_text())
        pkg = m.split(".")[:-1]
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.level:
                base = pkg[:len(pkg) - (n.level - 1)] if n.level > 1 else pkg
                mod = base + (n.module.split(".") if n.module else [])
                for a in n.names:
                    cand = ".".join(mod + [a.name])
                    stack.append(cand if (ROOT / (cand.replace(".", "/")
                                                  + ".py")).exists()
                                 else ".".join(mod))
    return seen


def test_the_ws_runtime_imports_nothing_that_can_trade():
    seen = _closure(["kalshi_ws", "workers.kalshi_ws_market_data"])
    for bad in ("kalshi_venue", "kalshi_orders", "kalshi_account",
                "kalshi_linkage", "execmirror", "live_executor", "pmus",
                "execution_gate", "live_parity", "bettor_funded_book",
                "venue_selection", "capital_path"):
        assert not any(s.split(".")[-1] == bad for s in seen), (bad, seen)
    for f in ("kalshi_ws.py", "workers/kalshi_ws_market_data.py"):
        src = (ROOT / f).read_text()
        assert ".submit(" not in src and "cancel(" not in src.replace(
            "task.cancel()", "")
        assert "/portfolio/orders" not in src and "/orders" not in src


def test_the_ws_runtime_is_dedicated_only():
    src = (ROOT / "workers" / "all.py").read_text()
    assert "kalshi_ws_market_data" in src            # named dedicated-only
    assert "kalshi_ws_market_data.run" not in src     # never registered
    ump = (ROOT / "workers" / "universal_market_plane.py").read_text()
    assert "KWSMD.run()" in ump
    from sportsassets.workers import all as W
    assert "kalshi_ws_market_data" in W.DEDICATED_ONLY_LOOPS
    assert "kalshi_ws_market_data" not in [n for n, _ in W.LOOPS]
    api = (ROOT / "api" / "app.py").read_text()
    assert "kalshi_ws" not in api


def test_without_a_credential_the_runtime_names_the_owner_action():
    from sportsassets.workers import kalshi_ws_market_data as R
    assert not KWS.credential_present({})
    assert "sportsassets-market-plane" in R.OWNER_ACTION
    assert "never on sportsassets-api" in R.OWNER_ACTION


# ── same-market vs cross-market YES / NO ────────────────────────────────

def test_yankees_yes_market_is_not_rays_no_market_at_the_book_level():
    y = inst("KALSHI", "KXMLBGAME-X-NYY", "YES", "HOME", [(D("0.47"), 50)])
    r = inst("KALSHI", "KXMLBGAME-X-TB", "NO", "AWAY", [(D("0.45"), 50)])
    assert y.key != r.key and not CC.same_market(y, r)
    b = CC.build_claims(FX, [y, r])
    # ... and the SAME claim only by the full payoff vector
    assert y.fingerprint and y.fingerprint == r.fingerprint
    assert CC.equivalence_receipt(FX, y, r, b["states"])["verdict"] == \
        "SAME_CLAIM"
    # independent quotes, distinct prices, both kept
    assert {i.asks[0][0] for i in b["classes"][y.fingerprint]} == {
        D("0.47"), D("0.45")}


def test_the_route_may_choose_rays_no_when_it_is_cheaper_all_in():
    y = inst("KALSHI", "KXMLBGAME-X-NYY", "YES", "HOME", [(D("0.47"), 50)])
    r = inst("KALSHI", "KXMLBGAME-X-TB", "NO", "AWAY", [(D("0.45"), 50)])
    b = CC.build_claims(FX, [y, r])
    fees = CC.fee_functions(at=NOW, sport="BASEBALL", kalshi_terms=MLB_TERMS)
    got = CC.route_claim(FX, y.fingerprint, b["classes"][y.fingerprint],
                         qty=10, now=NOW + 1, fee_by_venue=fees,
                         max_age_s=30)
    assert got["best_single"]["market_id"] == "KXMLBGAME-X-TB"
    assert got["best_single"]["side"] == "NO"
    assert got["runner_up"]["market_id"] == "KXMLBGAME-X-NYY"


def test_same_market_yes_and_no_net_at_the_market_level():
    a = ClaimExposure("fpNYY", "E", "fpNYY", "KALSHI", "K-NYY:YES",
                      D(100), D(45), "s")
    b = ClaimExposure("fpTB", "E", "fpTB", "KALSHI", "K-NYY:NO", D(60),
                      D(33), "s")
    rows = X.net_same_market([a, b])
    assert len(rows) == 1 and rows[0].instrument_id == "K-NYY:YES"
    assert rows[0].qty == D(40) and rows[0].signed_notional == D(18)
    # equal sides net to nothing held
    c = ClaimExposure("fpTB", "E", "fpTB", "KALSHI", "K-NYY:NO", D(100),
                      D(55), "s")
    assert X.net_same_market([a, c]) == []
    # cross-market aliases are separate positions (separate books)
    d = ClaimExposure("fpNYY", "E", "fpNYY", "KALSHI", "K-TB:NO", D(10),
                      D(4), "s")
    assert len(X.net_same_market([a, d])) == 2


def test_same_market_yes_plus_no_is_never_an_arbitrage():
    # even a quote pair that looks profitable is never evaluated
    y = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.40"), 100)])
    n = inst("KALSHI", "K-NYY", "NO", "HOME", [(D("0.40"), 100)])
    b = CC.build_claims(FX, [y, n])
    res = AC.scan_fixture(FX, b, now=NOW)
    assert res["same_market_pairs_excluded"] == 1
    for rec in res["records"]:
        legs = (rec.get("economics") or {}).get("legs") or []
        assert len({x["market_id"] for x in legs}) == len(legs)
    assert not [r for r in res["records"]
                if r.get("verdict") == A.GUARANTEED_AFTER_COSTS]


def test_a_cross_market_kalshi_pair_is_still_found():
    y = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.44"), 100)])
    t = inst("KALSHI", "K-TB", "YES", "AWAY", [(D("0.45"), 100)])
    # a NO carries its market's YES subject (the NYY market's NO pays on
    # TB winning: the TB-win claim)
    yn = inst("KALSHI", "K-NYY", "NO", "HOME", [(D("0.57"), 100)])
    tn = inst("KALSHI", "K-TB", "NO", "AWAY", [(D("0.56"), 100)])
    b = CC.build_claims(FX, [y, t, yn, tn])
    res = AC.scan_fixture(FX, b, now=NOW)
    rec = res["records"][0]
    assert rec["verdict"] == A.GUARANTEED_AFTER_COSTS, rec["reasons"]
    legs = rec["economics"]["legs"]
    assert {x["market_id"] for x in legs} == {"K-NYY", "K-TB"}
    assert rec["claim_pair"]["same_market_combinations_excluded"] == 2


# ── fees: the published schedule x the multiplier ───────────────────────

def test_the_mlb_route_binds_the_current_series_multiplier():
    assert MLB_TERMS["multiplier"] == "0.5"
    assert MLB_TERMS["schedule_id"] == "38032af2-e3fa-4659-9280-da64300b544c"
    assert MLB_TERMS["version"] == "quadratic_with_maker_fees@x0.5"
    # 10 @ 0.45: 0.5 x 0.07 x 10 x 0.45 x 0.55 = 0.086625 -> ceil 6dp,
    # the cash debit aligned up to the cent: 4.50 + 0.086625 -> 4.59
    assert KF.taker_fee(10, D("0.45"), MLB_TERMS) == D("0.09")
    # before the change the multiplier was 1
    old = KF.effective_terms(series_ticker="KXMLBGAME", event_ticker=None,
                             at=datetime(2026, 7, 1, tzinfo=timezone.utc)
                             .timestamp(), series_changes=MLB_CHANGES,
                             event_changes=[])
    assert old["multiplier"] == "1"
    # the engine prices a Kalshi leg identically
    q = A.order_fee(A.KALSHI, [(D("0.45"), 10)], at=datetime.now(
        timezone.utc), terms=MLB_TERMS)
    assert q.known and q.fee == KF.taker_fee(10, D("0.45"), MLB_TERMS)
    assert A.KALSHI_PUBLISHED_TAKER_COEFFICIENT == KF.TAKER_COEFFICIENT


def test_an_event_override_wins_and_a_cleared_one_falls_back():
    ev = [{"id": "e1", "event_ticker": "E", "series_ticker": "KXMLBGAME",
           "fee_type_override": "quadratic", "fee_multiplier_override": 1,
           "scheduled_ts": "2026-10-07T19:00:00Z"}]
    t = KF.effective_terms(series_ticker="KXMLBGAME", event_ticker="E",
                           at=NOW, series_changes=MLB_CHANGES,
                           event_changes=ev)
    assert t["schedule_id"] == "e1" and t["multiplier"] == "1"
    # a future-scheduled override is not in force yet
    fut = [dict(ev[0], scheduled_ts="2026-10-08T02:00:00Z")]
    t = KF.effective_terms(series_ticker="KXMLBGAME", event_ticker="E",
                           at=NOW, series_changes=MLB_CHANGES,
                           event_changes=fut)
    assert t["multiplier"] == "0.5"
    cleared = ev + [{"id": "e2", "event_ticker": "E", "fee_type_override":
                     None, "fee_multiplier_override": None,
                     "scheduled_ts": "2026-10-07T19:30:00Z"}]
    t = KF.effective_terms(series_ticker="KXMLBGAME", event_ticker="E",
                           at=NOW, series_changes=MLB_CHANGES,
                           event_changes=cleared)
    assert t["schedule_id"] == MLB_CHANGES[0]["id"]


def test_an_unknown_fee_or_multiplier_makes_the_route_ineligible():
    assert KF.effective_terms(series_ticker="KXNEW", event_ticker=None,
                              at=NOW, series_changes=[], event_changes=[]) \
        is None
    flat = KF.effective_terms(series_ticker="S", event_ticker=None, at=NOW,
                              series_changes=[{"id": "f", "fee_type": "flat",
                                               "fee_multiplier": 1,
                                               "scheduled_ts": 1,
                                               "series_ticker": "S"}],
                              event_changes=[])
    assert flat["priced"] is False and KF.fee_fn(flat) is None
    fees = CC.fee_functions(at=NOW, sport="BASEBALL", kalshi_terms=None)
    assert "KALSHI" not in fees
    k = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.40"), 50)],
             fee_terms=None)
    b = CC.build_claims(FX, [k])
    got = CC.route_claim(FX, k.fingerprint, b["classes"][k.fingerprint],
                         qty=5, now=NOW + 1, fee_by_venue=fees, max_age_s=30)
    assert got["chosen"] is None and got["candidates"][0]["reason"] == \
        "FEE_UNKNOWN"
    # and no Kalshi arbitrage leg without terms
    m = AC.adriana_states(b["states"])[0]
    c, why = AC.contract_of(FX, k, m, tie_rule=A.TIE_IMPOSSIBLE, source="MLB")
    assert c is None and why == "KALSHI_FEE_TERMS_UNKNOWN"


def test_no_account_discount_or_incentive_is_assumed():
    assert "no account-specific volume-tier discounts" in \
        KF.ACCOUNT_DISCOUNTS
    assert KF.INCENTIVES.startswith("EXCLUDED_UNLESS")
    assert MLB_TERMS["account_discounts"] == KF.ACCOUNT_DISCOUNTS
    src = (ROOT / "kalshi_fees.py").read_text().lower()
    assert "rebate" not in src and "volume_tier" not in src


def test_the_rep_contract_is_recorded_as_a_new_source():
    d = (ROOT.parents[1] / "research" / "kalshi_canonical_venue" /
         "REP_PRODUCTION_CONTRACT_2026-10-07")
    txt = (d / "KALSHI_REP_PRODUCTION_CONTRACT.md").read_text()
    assert "KALSHI_REP_CONFIRMATION" in txt
    assert "FROZEN_INTEGRATION_CASE" in txt      # not relabelled
    assert (d / "docs" / "websockets_orderbook-updates.md").is_file()
    assert "38032af2-e3fa-4659-9280-da64300b544c" in (
        d / "live_series_fee_changes_2026-10-07.jsonl").read_text()

"""RC6.3 PR #5 PORT -- CHURN AND TURNOVER SURVIVE A SWITCH, END TO END.

The same defect as tests/test_rc63_day_one_churn_lineage.py, proven through
the production ledger path instead of a direct churn_check call: every entry
goes L.submit_order -> CA.ledger_entry_authority -> PBIND.entry_bind ->
churn_check with the production profitability bind and capital authority
(the conftest's passthrough is off for both: PROFITABILITY_BIND_ENFORCED,
CAPITAL_AUTHORITY_ENFORCED). Before the fix (independent verification
pr5c_v_risk_0, V1-V3): main's re-entry 10 minutes after its exit was refused
CHURN_REENTRY_AFTER_RECENT_EXIT_WITHOUT_MATERIAL_EV_GAIN, and after
activation Day One re-entered the same contract ADMITTED; after 12 main
entries in the hour Day One's first entry was ADMITTED (13 lineage entries);
after a rollback main re-entered a contract the epoch exited 1010 s earlier.
Each is now refused by name. ALL DATA SYNTHETIC; one rolled-back transaction
per test on a fresh migrated database; no epoch is activated outside it.
"""
import hashlib
import time
import uuid

import pytest

from sportsassets import bettor_capital_authority as CA
from sportsassets import bettor_paper_day_one as E
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_profitability_bind as B
from sportsassets import bettor_paper_session as S
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import bettor_strategy_lifecycle as LC
from tests import paper_harness as H
from tests.test_day_one_paper_epoch import conn, epoch_database  # noqa: F401

PROFITABILITY_BIND_ENFORCED = True
CAPITAL_AUTHORITY_ENFORCED = True

HOUR = 3600.0
CG = "PINNACLE_COMPLETED_GAME_PAPER"
FEE = H.flat_fee(0.01)
ML = "baseball_team_full_game_winner"
MAIN = L.ACCOUNT_ID


def uid():
    return uuid.uuid4().hex[:8]


def proof(tag):
    return {'release_sha': 'b' * 40,
            'packet_digest': hashlib.sha256(tag.encode()).hexdigest(),
            'verified_at': 1}


async def premap(conn, slug, *, game_start):
    await conn.execute(
        "INSERT INTO us_premap (identifier, market_slug, event_slug, "
        " side_norm, game_start, sports_type, team_name) VALUES ($1,$2,$3,"
        " 'HOME',to_timestamp($4),$5,'Test Home')",
        "rc63-vchurn-" + uid(), slug, "mlb-test-" + slug[-6:], float(game_start), ML)


def _cal(n=300, p=0.60, rate=0.60, regime=B.PRE_1H_24H):
    ones = int(round(n * rate))
    return B.fit_calibration([{"sport": "baseball", "family": B.MONEYLINE,
                               "regime": regime, "p": p,
                               "y": 1.0 if i < ones else 0.0}
                              for i in range(n)])


def _settled(outcome="WON", payout=1.0):
    async def fn(conn, slug, side):
        return {"outcome": outcome, "payout_per_contract": payout,
                "evidence": {"test": True}}
    return fn


async def seed_forward(conn, acct, strategy, *, T):
    for i in range(CA.MIN_FORWARD_OBSERVATIONS):
        slug = "%s:vfwd:%s:%d:%s" % (acct, strategy[-6:], i, uid())
        dec = T - 3000 + i
        await premap(conn, slug, game_start=dec + 3 * HOUR)
        ce = CA.evaluate_executable(
            p=0.6, levels=[{"price": 0.40, "qty": 100}], qty=100, limit=0.40,
            fee_fn=FEE, at=dec, settlement={"compatibility": "COMPATIBLE"},
            identity={"us_market_slug": slug, "payout_event": "HOME",
                      "fixture": "fx-" + slug, "holding_side": "LONG"})
        ev = CA.capital_evidence(ce, p=0.6, limit=0.40, basis="TEST",
                                 levels=[{"price": 0.40, "qty": 100}])
        got = await CA.record_shadow(
            conn, account_id=acct, strategy=strategy, decision_id="dec:" + slug,
            order_key=None, source=CA.SRC_DECISION,
            capital_refusal=CA.R_FORWARD_UNKNOWN,
            lifecycle_state=LC.ACTIVE_CHALLENGER, slug=slug,
            holding_side="LONG", fixture="fx-" + slug, payout_event="HOME",
            decided_at=dec, delay_s=2.0, expires_at=dec + 90,
            simulator_version="test", evidence=ev)
        assert got["recorded"], got
        await CA.settle_shadows(conn, now=T - 2000, account_id=acct, limit=1,
                                settlement_fn=_settled())


def _order(a, *, key, at, slug, fixture, qty=40, p=0.60, limit=0.40,
           direction="BUY", role="ENTRY", group_id=None):
    o = H.order(a, key=key, qty=qty, limit=limit, at=at, slug=slug,
                direction=direction, role=role,
                group_id=group_id or "paper_g_%s_%s" % (a["account_id"][-6:], key),
                fixture=fixture)
    o["strategy"] = CG
    if role == "ENTRY":
        levels = [{"price": limit, "qty": 1000}]
        ce = CA.evaluate_executable(
            p=p, levels=levels, qty=qty, limit=limit, fee_fn=FEE, at=at,
            settlement={"compatibility": "COMPATIBLE"},
            identity={"us_market_slug": slug, "payout_event": "HOME",
                      "fixture": fixture, "holding_side": "LONG"})
        o["capital_evidence"] = CA.capital_evidence(
            ce, p=p, limit=limit, threshold_edge_pp=0.5, basis="TEST",
            levels=levels)
    return o


async def ctx(conn, acct):
    s = await S.active_session(conn, acct)
    return {"account_id": acct, "session_id": s["session_id"], "config": s["config"]}


async def setup_main(conn, T):
    await S.ensure_session(conn, account_id=MAIN, now=T - 7200)
    await B.record_model(conn, account_id=MAIN, kind="CALIBRATION",
                         payload=_cal(), at=T - 60)
    await seed_forward(conn, MAIN, CG, T=T)
    return await ctx(conn, MAIN)


async def try_submit(conn, o, now):
    """Submit inside a savepoint that is rolled back: the verdict is read,
    no row is kept."""
    sp = conn.transaction()
    await sp.start()
    try:
        got = await L.submit_order(conn, o, fee_fn=FEE, now=now)
    finally:
        await sp.rollback()
    return got


async def round_trip(conn, a, *, key, slug, fixture, t):
    """ENTRY through the production bind, filled; then a full EXIT, filled."""
    o = _order(a, key=key, at=t, slug=slug, fixture=fixture)
    got = await L.submit_order(conn, o, fee_fn=FEE, now=t)
    assert got["ok"], got
    await H.observe(conn, slug, t + 3, offers=[(0.40, 1000)], bids=[(0.39, 1000)])
    sim = await SIM.simulate_order(conn, got["order"]["order_id"], now=t + 4, fee_fn=FEE)
    assert sim["fills"], sim
    q = sum(float(f["qty"]) for f in sim["fills"])
    ex = _order(a, key=key + "-exit", at=t + 60, slug=slug, fixture=fixture,
                qty=q, limit=0.45, direction="SELL", role="EXIT",
                group_id=got["order"]["group_id"])
    r2 = await L.submit_order(conn, ex, fee_fn=FEE, now=t + 60)
    assert r2["ok"], r2
    await H.observe(conn, slug, t + 63, offers=[(0.47, 1000)], bids=[(0.45, 1000)])
    s2 = await SIM.simulate_order(conn, r2["order"]["order_id"], now=t + 64, fee_fn=FEE)
    assert s2["fills"], s2
    assert not [p for p in await L.positions(conn, a["account_id"])
                if float(p["open_qty"]) != 0]
    return t + 64


def verdict(got):
    return "ADMITTED qty=%s" % got["order"]["qty"] if got.get("ok") else got.get("refusal")


async def test_V1_reentry_after_an_exit_is_refused_on_day_one_too(conn):
    T = float(int(time.time()))
    main = await setup_main(conn, T)
    slug, fx = "vch-X-" + uid(), "vch-fx-" + uid()
    await premap(conn, slug, game_start=T + 3 * HOUR)
    exit_at = await round_trip(conn, main, key="x1", slug=slug, fixture=fx, t=T - 2000)
    # baseline: main itself, same contract, ~10 minutes after the exit
    on_main = await try_submit(conn, _order(main, key="x2", at=T - 100, slug=slug,
                                            fixture=fx), T - 100)
    r = await E._activate_verified(conn, epoch_id="v1-" + uid(), request_id="v1-" + uid(),
                                   proof=proof("v1" + uid()))
    new, t1 = r["account_id"], r["opened_at"] + 5
    d1 = await ctx(conn, new)
    on_d1 = await L.submit_order(conn, _order(d1, key="x3", at=t1, slug=slug, fixture=fx),
                                 fee_fn=FEE, now=t1)
    print("\nV1 main exit at %.0f; main re-entry at T-100: %s | Day One (%s) re-entry at "
          "opened+5 (%.0f s after main's exit): %s | churn=%s" % (
              exit_at, verdict(on_main), new, t1 - exit_at, verdict(on_d1),
              (on_d1.get("profitability_bind") or {}).get("refusal")))
    assert on_main.get("refusal") == B.R_CHURN_RECENT_EXIT, on_main
    assert not on_d1.get("ok"), "Day One re-entered the contract main exited %.0f s earlier" % (t1 - exit_at)


async def test_V2_turnover_cap_carries_onto_day_one(conn):
    T = float(int(time.time()))
    main = await setup_main(conn, T)
    for i in range(B.MAX_ENTRIES_PER_HOUR):
        s = "vch-turn-%d-%s" % (i, uid())
        await premap(conn, s, game_start=T + 3 * HOUR)
        got = await L.submit_order(conn, _order(main, key="t%d" % i, at=T - 1200 + i,
                                                slug=s, fixture="fx-" + s, qty=4),
                                   fee_fn=FEE, now=T - 1200 + i)
        assert got["ok"], (i, got)
        await L.release_remainder(conn, order_id=got["order"]["order_id"],
                                  reason="PROBE_CANCEL", at=T - 1199 + i, state="CANCELED")
    other = "vch-other-" + uid()
    await premap(conn, other, game_start=T + 3 * HOUR)
    on_main = await try_submit(conn, _order(main, key="t13", at=T - 100, slug=other,
                                            fixture="fx-" + other, qty=4), T - 100)
    r = await E._activate_verified(conn, epoch_id="v2-" + uid(), request_id="v2-" + uid(),
                                   proof=proof("v2" + uid()))
    new, t1 = r["account_id"], r["opened_at"] + 5
    d1 = await ctx(conn, new)
    on_d1 = await L.submit_order(conn, _order(d1, key="t14", at=t1, slug=other,
                                              fixture="fx-" + other, qty=4),
                                 fee_fn=FEE, now=t1)
    n_hour = await conn.fetchval(
        "SELECT count(*) FROM paper_orders WHERE account_id=ANY($1::text[]) AND strategy=$2 "
        " AND role='ENTRY' AND direction='BUY' AND decided_at > to_timestamp($3)",
        [MAIN, new], CG, t1 - 3600)
    print("\nV2 12 main entries in the last hour; main 13th: %s | Day One first entry: %s | "
          "lineage ENTRY BUY orders in the hour before Day One's entry (incl. it): %s" % (
              verdict(on_main), verdict(on_d1), n_hour))
    assert on_main.get("refusal") == B.R_TURNOVER_CAP, on_main
    assert not on_d1.get("ok"), "Day One's turnover count restarted at zero"


async def test_V3_epoch_exit_cooldown_carries_back_after_rollback(conn):
    T = float(int(time.time()))
    await setup_main(conn, T)
    r = await E._activate_verified(conn, epoch_id="v3-" + uid(), request_id="v3-" + uid(),
                                   proof=proof("v3" + uid()))
    new, t1 = r["account_id"], r["opened_at"] + 5
    d1 = await ctx(conn, new)
    slug, fx = "vch-R-" + uid(), "vch-rfx-" + uid()
    await premap(conn, slug, game_start=t1 + 3 * HOUR)
    exit_at = await round_trip(conn, d1, key="r1", slug=slug, fixture=fx, t=t1)
    on_epoch = await try_submit(conn, _order(d1, key="r2", at=exit_at + 1000, slug=slug,
                                             fixture=fx), exit_at + 1000)
    rb = await E.rollback(conn, epoch_id=(await conn.fetchval(
        "SELECT epoch_id FROM paper_account_epochs WHERE account_id=$1", new)),
        request_id="v3-undo-" + uid())
    main = await ctx(conn, MAIN)
    on_main = await L.submit_order(conn, _order(main, key="r3", at=exit_at + 1010, slug=slug,
                                                fixture=fx), fee_fn=FEE, now=exit_at + 1010)
    print("\nV3 epoch exit at %.0f; epoch re-entry +1000 s: %s | rollback=%s | main re-entry "
          "+1010 s after the epoch's exit: %s" % (exit_at, verdict(on_epoch), rb, verdict(on_main)))
    assert on_epoch.get("refusal") == B.R_CHURN_RECENT_EXIT, on_epoch
    assert not on_main.get("ok"), "rollback forgot the epoch's exit 1010 s earlier"

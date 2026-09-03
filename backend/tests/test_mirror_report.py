"""The shadow's report, to-a-tee Phase 0 (owner order 2026-09-02 "mirror
the whales to a tee"): the census every later gate reads, computed from
the rows' JSONB detail with fixtures and no database. What is pinned:
the unmapped split by family and by his dollars; the mapped split by
mapping SOURCE against the quarantine's admissible set; the would-fill
rate split into non-legacy and legacy plans with a market-clustered
interval; the parallel short reading; the snapshot census; the dead-band
dollars; his sign split; the would-P&L settle with a game-clustered
interval; and that `latest` now carries the detail."""
import asyncio
import inspect
import json
import re

from sportsassets.analytics import mirror_report as mr
from sportsassets.analytics import proof


def _run(coro):
    return asyncio.run(coro)


def _row(cid, slug=None, **kw):
    r = {"whale": "rn1", "condition_id": cid, "us_market_slug": slug, "his_long": None,
         "snap_long": None, "would_side": None, "would_fill": None, "reason": "", "detail": "{}"}
    r.update(kw)
    if isinstance(r.get("detail"), dict):
        r["detail"] = json.dumps(r["detail"])
    return r


def test_the_admissible_set_is_the_copy_lanes_quarantine_set():
    from sportsassets import live_executor as le
    assert mr.ADMISSIBLE_SRC == le.QUARANTINE_RESUME_SRC == frozenset({"premap", "exact"})


def test_latest_carries_the_detail_and_the_unmapped_split_by_family_and_usd():
    latest = [
        _row("u1", detail={"his_slug": "atp-a-b-2026-09-02", "family": "moneyline", "sport": "tennis",
                           "explain": "no_key_intersection", "notional_6h": 1200.0, "outcome_null": 0}),
        _row("u2", detail={"his_slug": "mlb-x-y-2026-09-02-total-9pt5", "family": "total",
                           "sport": "baseball", "explain": "type_prefix_filter_emptied",
                           "notional_6h": 300.5, "outcome_null": 2}),
        _row("u3", reason="unmapped: no US market for his tokens"),      # written before Phase 0
        _row("m1", "aec-atp-a-b-2026-09-02", his_net=1000.0, mark=0.40,
             detail={"map": "premap", "family": "moneyline", "his_gross_usd": 500.0}),
        _row("m2", "atc-epl-c-d-2026-09-02-c", his_net=-2000.0, mark=0.25,
             detail={"map": "ledger", "map_class": "traded:ioc", "family": "moneyline",
                     "his_gross_usd": 1500.0, "per_side": True}),
        _row("m3", "tsc-mlb-e-f-2026-09-02-9pt5", his_net=50.0, mark=None,
             detail={"map": "exact", "family": "total"}),
    ]
    out = mr.summarize(latest, latest, {})
    # latest carries the detail as a dict, whatever shape it arrived in
    assert all(isinstance(r["detail"], dict) for r in out["latest"])
    assert out["latest"][0]["detail"]["his_slug"] == "atp-a-b-2026-09-02"
    u = out["unmapped"]
    assert u["markets"] == 3 and u["usd"] == 1500.5 and u["named"] == 2 and u["outcome_null_markets"] == 1
    assert u["by_family"] == {"moneyline": {"markets": 1, "usd": 1200.0},
                              "total": {"markets": 1, "usd": 300.5},
                              "unknown": {"markets": 1, "usd": 0.0}}
    assert u["by_explain"]["no_key_intersection"] == {"markets": 1, "usd": 1200.0}
    assert u["by_explain"]["unknown"] == {"markets": 1, "usd": 0.0}
    # mapped by SOURCE: the ledger's own class is carried, and only the
    # quarantine's admissible sources count toward what P1 can open
    s = out["mapped_by_source"]
    assert s["markets"] == 3 and s["admissible"] == 2 and s["admissible_share"] == round(2 / 3, 4)
    assert s["by_source"]["premap"] == {"markets": 1, "usd": 500.0, "unmarked": 0}
    assert s["by_source"]["ledger"] == {"markets": 1, "usd": 1500.0, "unmarked": 0}
    assert s["by_source"]["exact"] == {"markets": 1, "usd": 0.0, "unmarked": 1}
    assert s["ledger_classes"] == {"traded:ioc": 1} and s["admissible_usd"] == 500.0
    assert s["admissible_src"] == ["exact", "premap"]
    # family and sign
    assert out["family"]["moneyline"] == {"markets": 2, "neg": 1, "pos": 1, "usd": 2000.0, "per_side": 1}
    assert out["family"]["total"] == {"markets": 1, "neg": 0, "pos": 1, "usd": 0.0, "per_side": 0}
    g = out["sign"]
    assert g["neg_markets"] == 1 and g["pos_markets"] == 2 and g["flat_markets"] == 0
    assert g["neg_share_markets"] == round(1 / 3, 4)
    assert g["neg_sh"] == 2000.0 and g["pos_sh"] == 1050.0 and g["neg_share_sh"] == round(2000 / 3050, 4)
    # dollars on the leg he holds: 2000 x (1 - 0.25) short side, 1000 x 0.40 long; m3 unmarked
    assert g["neg_usd"] == 1500.0 and g["pos_usd"] == 400.0 and g["neg_share_usd"] == round(1500 / 1900, 4)
    assert g["unmarked_markets"] == 1
    assert "non-legacy" in out["reading"] and "NO ORDERS PLACED" in out["reading"]


def test_the_non_legacy_filter_excludes_the_three_legacy_shapes():
    rows = [
        # (1) the long-only target refused his side by name
        _row("a", "s1", would_side="BUY_LONG", would_fill=True, ledger_net=0,
             reason="short side not admitted; increase toward target"),
        # (2) a negative ledger: a legacy per-fill BUY_SHORT of ours
        _row("b", "s2", would_side="BUY_LONG", would_fill=True, ledger_net=-68,
             reason="increase toward target"),
        # (3) a positive ledger a per-fill row holds
        _row("c", "s3", would_side="SELL_LONG", would_fill=False, ledger_net=100,
             reason="reduce toward target", detail={"ledger_legacy": True}),
        # the plans P1 could place: a flat ledger, or the mirror's own book
        _row("d", "s4", would_side="BUY_LONG", would_fill=True, ledger_net=0,
             reason="increase toward target"),
        _row("e", "s5", would_side="BUY_LONG", would_fill=False, ledger_net=50,
             reason="increase toward target", detail={"ledger_legacy": False}),
        # a positive ledger whose facts were unreadable: neither cohort
        _row("f", "s6", would_side="BUY_LONG", would_fill=True, ledger_net=50,
             reason="increase toward target"),
        # a ledger the row never read at all
        _row("g", "s7", would_side="BUY_LONG", would_fill=True, ledger_net=None,
             reason="increase toward target"),
    ]
    assert [mr.is_legacy_plan(r) for r in rows] == [True, True, True, False, False, None, None]
    out = mr.summarize(rows, rows, {})
    nl, lg = out["would_fill_nonlegacy"], out["would_fill_legacy"]
    assert (nl["orders"], nl["resolved"], nl["fills"], nl["rate"]) == (2, 2, 1, 0.5)
    assert nl["legacy_unknown"] == 2 and nl["clusters"] == 2
    assert (lg["orders"], lg["resolved"], lg["fills"]) == (3, 3, 2)
    # the all-plans rate is untouched by the split
    assert out["would_orders"] == 7 and out["would_fill_rate"] == round(5 / 7, 4)
    # the short reading's legacy reading ignores the long target's refusal
    assert mr.is_legacy_plan(rows[0], short=True) is False


def test_the_fill_rate_interval_is_the_proof_modules_cluster_robust_standard():
    # ten plans on one market, all filled; ten on another, none: 0.5 with
    # TWO clusters -- the interval is roi_with_ci's, stake 1, pnl = filled
    rows = ([_row("m1", "s", would_side="BUY_LONG", would_fill=True) for _ in range(10)]
            + [_row("m2", "s", would_side="BUY_LONG", would_fill=False) for _ in range(10)])
    out = mr.rate_with_ci(rows)
    ref = proof.roi_with_ci([{"stake": 1.0, "pnl": 1.0 if r["would_fill"] else 0.0,
                              "event_key": r["condition_id"]} for r in rows])
    assert out["rate"] == 0.5 and out["clusters"] == 2 and out["se"] == ref["se"]
    assert out["ci95"] == [max(0.0, round(ref["ci95"][0], 4)), min(1.0, round(ref["ci95"][1], 4))]
    # twenty markets with the same fills: the same rate, a narrower interval
    spread = [dict(r, condition_id=f"m{i}") for i, r in enumerate(rows)]
    out2 = mr.rate_with_ci(spread)
    assert out2["rate"] == 0.5 and out2["clusters"] == 20
    assert out2["ci95"][1] - out2["ci95"][0] < out["ci95"][1] - out["ci95"][0]
    # unresolved plans count on neither side; nothing resolved is no rate
    assert mr.rate_with_ci([_row("m", "s", would_side="BUY_LONG")])["rate"] is None
    assert mr.rate_with_ci([])["ci95"] is None


def test_the_would_pnl_settles_one_lot_per_market_and_side_with_a_game_clustered_interval():
    rows = [
        # market A, three ticks of the same intent: ONE lot, the first touch
        {"whale": "rn1", "condition_id": "A", "at": "t1", "would_side": "BUY_LONG", "would_px": 0.40,
         "would_qty": 100, "would_fill": True, "ledger_net": 0, "reason": "increase", "detail": "{}",
         "game_key": "g1", "payout": 1.0},
        {"whale": "rn1", "condition_id": "A", "at": "t2", "would_side": "BUY_LONG", "would_px": 0.42,
         "would_qty": 100, "would_fill": True, "ledger_net": 0, "reason": "increase", "detail": "{}",
         "game_key": "g1", "payout": 1.0},
        # market B lost
        {"whale": "rn1", "condition_id": "B", "at": "t1", "would_side": "BUY_LONG", "would_px": 0.60,
         "would_qty": 50, "would_fill": True, "ledger_net": 0, "reason": "increase", "detail": "{}",
         "game_key": "g2", "payout": 0.0},
        # market C: a sell of the long token that paid nothing -- the sale's own P&L
        {"whale": "rn1", "condition_id": "C", "at": "t1", "would_side": "SELL_LONG", "would_px": 0.55,
         "would_qty": 20, "would_fill": True, "ledger_net": 0, "reason": "reduce", "detail": "{}",
         "game_key": "g2", "payout": 0.0},
        # legacy (negative ledger): dropped by the same filter the rate uses
        {"whale": "rn1", "condition_id": "D", "at": "t1", "would_side": "BUY_LONG", "would_px": 0.30,
         "would_qty": 10, "would_fill": True, "ledger_net": -5, "reason": "increase", "detail": "{}",
         "game_key": "g3", "payout": 1.0},
        # not settled yet
        {"whale": "rn1", "condition_id": "E", "at": "t1", "would_side": "BUY_LONG", "would_px": 0.30,
         "would_qty": 10, "would_fill": True, "ledger_net": 0, "reason": "increase", "detail": "{}",
         "game_key": "g4", "payout": None},
        # never filled
        {"whale": "rn1", "condition_id": "F", "at": "t1", "would_side": "BUY_LONG", "would_px": 0.30,
         "would_qty": 10, "would_fill": False, "ledger_net": 0, "reason": "increase", "detail": "{}",
         "game_key": "g5", "payout": 1.0},
    ]
    out = mr.settle_would_pnl(rows)
    assert out["lots"] == 3 and out["markets"] == 3
    # A: (1 - 0.40) x 100 = 60 on 40; B: (0 - 0.60) x 50 = -30 on 30; C: (0.55 - 0) x 20 = 11 on 9
    assert out["staked"] == 79.0 and out["pnl"] == 41.0 and out["roi"] == round(41 / 79, 6)
    assert out["clusters"] == 2 and out["ci95"] is not None
    assert out["dropped_legacy"] == 1 and out["dropped_unsettled"] == 1
    # the interval is game-clustered: A, B and C over two games, not three
    ref = proof.roi_with_ci([{"stake": 40, "pnl": 60, "event_key": "g1"},
                             {"stake": 30, "pnl": -30, "event_key": "g2"},
                             {"stake": 9, "pnl": 11, "event_key": "g2"}])
    assert out["ci95"] == ref["ci95"]
    # the short reading settles from the detail's own columns
    srows = [dict(rows[0], detail=json.dumps({"would_side_short": "SELL_LONG", "would_px_short": 0.60,
                                              "would_qty_short": 30, "would_fill_short": True}),
                  would_fill=False, payout=0.0),
             dict(rows[2], detail=json.dumps({"would_side_short": "SELL_LONG", "would_px_short": 0.50,
                                              "would_qty_short": 10, "would_fill_short": False}))]
    s = mr.settle_would_pnl(srows, short=True)
    assert s["lots"] == 1 and s["pnl"] == 18.0 and s["staked"] == 12.0
    assert mr.settle_would_pnl([])["lots"] == 0 and mr.settle_would_pnl([])["ci95"] is None


def test_the_snapshot_census_the_dead_band_the_short_reading_and_the_touch():
    latest = [
        _row("a", "s1", snap_long=1.0, snap_other=2.0, mark=0.40, reason="on target",
             detail={"snap_state": "fresh_complete", "snap_age_s": 40, "fills_since_snap": 3,
                     "target_short": -100}),
        _row("b", "s2", snap_long=1.0, snap_other=None, mark=0.40, reason="on target",
             detail={"snap_state": "fresh_partial", "snap_age_s": 120, "fills_since_snap": 9,
                     "target_short": 50}),
        _row("c", "s3", mark=0.40, reason="short side not admitted; under the dollar dead band",
             detail={"snap_state": "stale", "snap_age_s": 700, "delta": -8, "target_short": -1}),
        _row("d", "s4", mark=None, reason="under the dollar dead band", detail={"delta": 3}),
        # a row written before Phase 0: its state is read from the old keys
        _row("e", "s5", snap_long=None, snap_other=None, reason="on target",
             detail={"snap_age_s": 30, "snap_partial": True}),
    ]
    all_rows = latest + [
        _row("a", "s1", would_side="BUY_LONG", would_fill=True, ledger_net=0,
             detail={"touched_s": 12, "touch_px": 0.3, "touch_depth": {"bid": 0.3, "bid_qty": 50},
                     "would_side_short": "SELL_LONG", "would_fill_short": True}),
        _row("a", "s1", would_side="BUY_LONG", would_fill=False, ledger_net=0,
             detail={"expired_s": 640, "would_side_short": "SELL_LONG", "would_fill_short": False}),
        _row("b", "s2", would_side="BUY_LONG", would_fill=True, ledger_net=-3,
             detail={"touched_s": 300, "touch_depth": None,
                     "would_side_short": "BUY_LONG", "would_fill_short": None}),
    ]
    out = mr.summarize(latest, all_rows, {})
    sn = out["snapshot"]
    assert (sn["markets"], sn["fresh_complete"], sn["fresh_partial"], sn["stale"], sn["none"]) == (5, 1, 2, 1, 1)
    assert sn["token_na_markets"] == 2 and sn["fresh_complete_share"] == 0.2
    assert sn["age_p50_s"] == 40 and sn["age_max_s"] == 700 and sn["fills_since_snap_p50"] == 3
    # the dead band: his moves the $5 band refused, in dollars at the mark
    assert out["dead_band"] == {"markets": 2, "usd": round(8 * 0.40, 2)}
    # the short reading: rate over its resolved plans, and how many markets
    # read a negative target (his side P1 refuses)
    sh = out["short"]
    assert (sh["orders"], sh["resolved"], sh["fills"], sh["rate"]) == (3, 2, 1, 0.5)
    assert sh["neg_target_markets"] == 2 and sh["latest_neg_target"] == 2
    assert sh["nonlegacy"]["resolved"] == 2                 # the negative-ledger plan is out
    # the touch
    assert out["touch"] == {"n": 2, "touched_s_p50": 12, "touched_s_p90": 300, "depth_n": 1}


def test_mirror_shadow_report_settles_from_the_resolution_join_and_names_its_absence():
    import datetime as dt
    t0 = dt.datetime(2026, 9, 2, 18, 0, tzinfo=dt.timezone.utc)
    rows = [{"at": t0, "whale": "rn1", "condition_id": "A", "us_market_slug": "s", "would_side": "BUY_LONG",
             "would_fill": True, "would_px": 0.4, "would_qty": 10, "ledger_net": 0, "reason": "x",
             "detail": "{}", "his_long": 1.0, "snap_long": 1.0}]
    settle = [dict(rows[0], game_key="g1", payout=1.0),
              {"at": t0, "whale": "rn1", "condition_id": "B", "us_market_slug": "s2", "would_side": "BUY_LONG",
               "would_fill": True, "would_px": 0.5, "would_qty": 10, "ledger_net": 0, "reason": "x",
               "detail": "{}", "game_key": "g2", "payout": 0.0}]

    class _P:
        def __init__(self, settle_raises=False, main_raises=False):
            self.settle_raises, self.main_raises, self.sql = settle_raises, main_raises, []

        async def fetch(self, sql, *a):
            s = " ".join(sql.split())
            self.sql.append((s, a))
            if "/* would-pnl */" in s:
                if self.settle_raises:
                    raise RuntimeError("no join")
                return [dict(r) for r in settle]
            if self.main_raises:
                raise RuntimeError("no table")
            return [dict(r) for r in rows]

        async def fetchval(self, sql, *a):
            return None

    p = _P()
    out = _run(mr.mirror_shadow_report(p, 6.0, "rn1"))
    assert out["would_pnl"]["lots"] == 2 and out["would_pnl"]["pnl"] == 1.0 and out["would_pnl"]["clusters"] == 2
    assert out["would_pnl_short"]["lots"] == 0
    settle_sql = [s for s, a in p.sql if "/* would-pnl */" in s][0]
    assert "JOIN market_tokens mt ON mt.token_id = s.long_asset" in settle_sql
    assert "((m.resolved_prices -> mt.outcome_index)::text)::float8 AS payout" in settle_sql
    assert "jsonb_array_length(m.resolved_prices) > mt.outcome_index" in settle_sql
    assert "AND s.whale = $2" in settle_sql and [a for s, a in p.sql if "/* would-pnl */" in s][0] == (6.0, "rn1")
    # the settle read failing is named inside the report, never hides the window
    out2 = _run(mr.mirror_shadow_report(_P(settle_raises=True), 6.0))
    assert out2["rows"] == 1 and out2["would_pnl"] == {"error": "unavailable: RuntimeError", "lots": 0}
    # the window read failing is the same answer as before Phase 0
    assert _run(mr.mirror_shadow_report(_P(main_raises=True))) == {
        "rows": 0, "error": "unavailable: RuntimeError", "latest": []}
    # the 046 column pin reads the WINDOW select first: the settle join is its own function
    rep = " ".join(inspect.getsource(mr.mirror_shadow_report).split())
    sel = re.search(r"SELECT (.*?) FROM mirror_shadow", rep).group(1)
    assert "s." not in sel and "payout" not in sel and "FROM mirror_shadow s" not in rep


def test_candidate_slugs_tries_every_form_the_grammar_would():
    c = mr.candidate_slugs("US Open ATP: Brandon Nakashima vs Alex Michelsen", "atp-nakashi-michels-2026-09-02",
                           "atp-nakashi-michels-2026-09-02", ["Alex Michelsen", "Brandon Nakashima"])
    assert c[:2] == ["aec-atp-branak-alemic-2026-09-02", "aec-atp-alemic-branak-2026-09-02"]
    assert "aec-atp-nakashi-michels-2026-09-02" in c and "atp-nakashi-michels-2026-09-02" in c
    assert "atc-atp-nakashi-michels-2026-09-02-michels" in c
    assert c[-2:] == ["aec-atp-nakashi-michels-2026-09-02", "atc-atp-nakashi-michels-2026-09-02"] or \
        "atc-atp-nakashi-michels-2026-09-02" in c
    assert len(c) == len(set(c))
    # ITF: six abbreviated tennis forms (both player orders x both tours and
    # the bare code) beside the us-slug form and his slug verbatim
    itf = mr.candidate_slugs("ITF W15 Foo: Ana Bogdan vs Petra Martic", "itf-bogdan-martic-2026-09-02", None, [])
    assert len([s for s in itf if s.startswith("aec-itf") and "anabog" in s]) == 6
    assert "aec-itf-bogdan-martic-2026-09-02" in itf and itf[-1] == "itf-bogdan-martic-2026-09-02"
    # soccer per-team: the side form when the outcome names one code
    s = mr.candidate_slugs("Will Cardiff City FC win on 2026-09-02?", "elc-qpr-car-2026-09-02-car",
                           "elc-qpr-car-2026-09-02", ["Yes", "No"])
    assert "aec-elc-qpr-car-2026-09-02" in s and "atc-elc-qpr-car-2026-09-02" in s
    assert mr.candidate_slugs(None, None, None, []) == []


def test_mirror_cover_report_serves_candidates_dollars_class_and_source(monkeypatch):
    from tests.test_mirror_shadow import CID, HIS, M, N, SLUG, _Pool, _fill, _nosleep
    _nosleep(monkeypatch)
    monkeypatch.setattr(mr, "_num", mr._num)
    from sportsassets.workers import mirror_shadow as ms
    monkeypatch.setattr(ms.time, "time", lambda: 5000.0)
    other = [_fill(N, "BUY", 100, 0.5, 4000, market_title="Will NEOM SC win on 2026-09-03?",
                   market_slug="spl-neo-kha-2026-09-03-neo", event_slug="spl-neo-kha-2026-09-03",
                   outcome="Yes", sport="soccer")]
    tiny = [_fill(M, "BUY", 1, 0.5, 4500, market_slug="us-open-2026-winner", outcome="A")]

    class _P(_Pool):
        def __init__(self):
            super().__init__(fills=HIS, mapped=False, conds=[CID, "0xneom", "0xtiny"])
            self.by_cid = {CID: HIS, "0xneom": other, "0xtiny": tiny}

        async def fetch(self, sql, *a):
            s = " ".join(sql.split())
            self.queries.append((s, a))
            if "/* cover-shadow-latest */" in s:
                return [{"condition_id": CID, "us_market_slug": SLUG, "reason": "increase toward target",
                         "mark": 0.31, "his_long": 10654.5, "his_other": 367.42, "his_net": 10287.08,
                         "target": 596, "at": "2026-09-02T18:00:00+00:00",
                         "detail": json.dumps({"map": "premap", "map_class": "premap", "family": "moneyline",
                                               "his_gross_usd": 3556.4, "per_side": False,
                                               "ledger_legacy": False})}]
            if "AS market_title, t.event_slug" in s:
                return list(self.by_cid.get(a[1], []))
            return await super().fetch(sql, *a)

        async def fetchval(self, sql, *a):
            if "/* cover-null-condition */" in sql:
                return 7
            return await super().fetchval(sql, *a)

    from sportsassets.workers import premap

    async def _resolve(pool, market_title, event_title, outcome, global_slug):
        return None

    async def _explain(pool, market_title, event_title, outcome, global_slug):
        return {"step": "no_side_match"}

    monkeypatch.setattr(premap, "resolve", _resolve)
    monkeypatch.setattr(premap, "resolve_explain", _explain)
    p = _P()
    out = _run(mr.mirror_cover_report(p, "rn1", 24.0, map_max=1))
    assert out["markets"] == 3 and out["null_condition_fills"] == 7 and out["map_calls"] == 1
    by = {r["condition_id"]: r for r in out["conditions"]}
    # sorted by his dollars, largest first
    assert [r["condition_id"] for r in out["conditions"]] == [CID, "0xneom", "0xtiny"]
    a = by[CID]
    assert a["his_slug"] == "atp-nakashi-michels-2026-09-02" and a["family"] == "moneyline"
    assert a["sport"] == "tennis" and a["date"] == "2026-09-02" and a["usd24h"] == 3534.88
    assert a["candidates"][:2] == ["aec-atp-branak-alemic-2026-09-02", "aec-atp-alemic-branak-2026-09-02"]
    assert a["map"] == {"source": "premap", "us_slug": SLUG, "per_side": False, "map_class": "premap"}
    assert a["shadow"]["class"] == "mapped" and a["shadow"]["gross_usd"] == 3556.4
    assert a["gross_sh"] == round(10654.5 + 367.42, 4) and a["paired_sh"] == 367.42
    # the market the shadow never read: mapped here (one call), named why not
    b = by["0xneom"]
    assert b["shadow"] is None and b["map"] is None and b["explain"] == "no_side_match"
    assert b["sport"] == "soccer" and b["date"] == "2026-09-03" and b["usd24h"] == 50.0
    assert "aec-spl-neo-kha-2026-09-03" in b["candidates"] and "atc-spl-neo-kha-2026-09-03" in b["candidates"]
    # past the map budget: named as unread, never guessed
    c = by["0xtiny"]
    assert c["explain"] == "unread:map_budget" and c["date"] is None and c["family"] == "unknown"
    assert out["mapped"] == 1 and out["admissible"] == 1 and out["usd24h"] == round(3534.88 + 50.0 + 0.5, 2)
    # the read is Postgres only: no venue surface is touched by the report
    src = inspect.getsource(mr.mirror_cover_report) + inspect.getsource(mr.candidate_slugs)
    for banned in ("submit_fok", "cancel_order", "close_position", "retrieve_by_slug", "_get_client",
                   "resolve_market_exact", "resolve_market("):
        assert banned not in src, banned


# ------------------------------------------ Phase 0 review of the instruments
# (owner order 2026-09-02 "mirror the whales to a tee")

def test_the_ledgers_own_verdict_is_read_before_the_flat_ledger_shortcut():
    # a per-fill row EXITING on the slug: ledger_net 0 (exiting rows net to
    # the ledger read but the position is being closed), yet P1's legacy_row
    # referee refuses the slug -- the plan is legacy, never "one P1 could place"
    exiting = _row("x", "s", would_side="BUY_LONG", would_fill=True, ledger_net=0,
                   reason="increase toward target", detail={"ledger_legacy": True})
    assert mr.is_legacy_plan(exiting) is True
    # filled rows netting to zero (a long and a short of ours), same verdict
    netted = dict(exiting, ledger_net=0.0)
    assert mr.is_legacy_plan(netted) is True and mr.is_legacy_plan(netted, short=True) is True
    # the flat shortcut still answers when the ledger says nothing is live
    flat = _row("y", "s", would_side="BUY_LONG", would_fill=True, ledger_net=0,
                reason="increase toward target", detail={"ledger_legacy": False})
    assert mr.is_legacy_plan(flat) is False
    assert mr.is_legacy_plan(_row("z", "s", ledger_net=0, detail={})) is False
    # an unreadable ledger fact with a flat ledger is still the flat answer
    # (nothing live means no per-fill row), and with a positive one is unknown
    assert mr.is_legacy_plan(_row("w", "s", ledger_net=0, detail={"ledger_legacy": None})) is False
    assert mr.is_legacy_plan(_row("v", "s", ledger_net=5, detail={"ledger_legacy": None})) is None
    # the census counts the exiting-row plan on the legacy side
    out = mr.summarize([exiting, flat], [exiting, flat], {})
    assert out["would_fill_nonlegacy"]["orders"] == 1 and out["would_fill_legacy"]["orders"] == 1
    # the verdict is consulted before the ledger arithmetic, in the source too
    src = inspect.getsource(mr.is_legacy_plan)
    assert src.index('d.get("ledger_legacy")') < src.index('r.get("ledger_net")')


class _Recording404:
    """A venue client that records every slug the exact lane asks for and
    answers 404 to all of them, so the lane walks its whole candidate list."""

    def __init__(self):
        self.calls = []
        self.markets = self

    def retrieve_by_slug(self, slug):
        self.calls.append(slug)
        raise RuntimeError("404 not found")


def test_candidate_slugs_carries_the_exact_lanes_derivative_forms(monkeypatch):
    from sportsassets import pmus
    fixtures = [
        # a total, single-token grammar
        ("mlb-nyy-bos-2026-07-22-o8pt5", "Over 8.5", "New York Yankees vs Boston Red Sox: Total 8.5",
         ["tsc-mlb-nyy-bos-2026-07-22-8pt5", "tsc-mlb-bos-nyy-2026-07-22-8pt5"]),
        # a spread, his suffix verbatim then the three shorter forms
        ("mlb-nyy-bos-2026-07-22-nyy-neg-1pt5", "New York Yankees", "Spread: New York Yankees (-1.5)",
         ["asc-mlb-nyy-bos-2026-07-22-nyy-neg-1pt5", "asc-mlb-nyy-bos-2026-07-22-neg-1pt5",
          "asc-mlb-nyy-bos-2026-07-22-nyy-1pt5", "asc-mlb-nyy-bos-2026-07-22-1pt5"]),
        # the swapped team order, word-form total: the primary is his order
        ("mlb-bos-nyy-2026-07-22-total-8pt5", "Over 8.5", "Boston Red Sox vs New York Yankees: Total 8.5",
         ["tsc-mlb-bos-nyy-2026-07-22-8pt5", "tsc-mlb-nyy-bos-2026-07-22-8pt5"]),
    ]
    for slug, outcome, title, want in fixtures:
        assert mr.derivative_candidates(slug) == want, slug
        # equality against the slugs the exact lane itself asks the venue for
        client = _Recording404()
        monkeypatch.setattr(pmus, "_get_client", lambda c=client: c)
        assert pmus.resolve_derivative_exact(slug, outcome, title) is None
        assert client.calls == want, slug
        # and the runner's candidate set carries every one of them
        cands = mr.candidate_slugs(title, slug, slug.rsplit("-", 1)[0], [outcome])
        assert all(w in cands for w in want), (slug, cands)
        assert len(cands) == len(set(cands)) and cands[-1].startswith("atc-")
    # a spread whose team token is not one of the base's teams is a different
    # market (a corners handicap): the lane refuses before a candidate exists
    assert mr.derivative_candidates("mlb-nyy-bos-2026-07-22-corners-neg-1pt5") == []
    # a moneyline, an unparseable slug and no slug yield nothing
    assert mr.derivative_candidates("atp-nakashi-michels-2026-09-02") == []
    assert mr.derivative_candidates("us-open-2026-winner") == [] and mr.derivative_candidates(None) == []
    ml = mr.candidate_slugs("A vs B", "atp-nakashi-michels-2026-09-02", None, [])
    assert not any(s.startswith(("tsc-", "asc-")) for s in ml)

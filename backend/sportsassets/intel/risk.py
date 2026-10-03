"""J · THE PORTFOLIO RISK ENGINE (SHADOW). What is open, where it is
concentrated, what it would cost to get out, and how far equity has fallen.

PAPER AND ACTUAL ARE SEPARATE BOOKS AND ARE NEVER SUMMED. The paper book is
the fictional account's simulated fills (paper_fills / paper_settlements);
the actual book is the venue-confirmed fills of the execution mirror
(execmirror_fills, Polymarket US) and of the Kalshi small-live lane
(kalshi_live_fills). Each gets its own report; nothing here adds them.

EXPOSURE is the open cost basis: open quantity x average acquisition cost
including fees, which is also the most a binary contract can lose. It is
broken down by game, team, conference, sport, market family, live/pregame,
settlement class and venue. A dimension the records cannot establish is the
bucket UNKNOWN, and the report says why (conference has no source in these
records at all, so it is UNKNOWN with that reason rather than invented).

CORRELATION CLUSTERS: connected components of open positions that share a
game, or a team within a sport. LIQUIDITY-AT-RISK: open quantity at the mid
minus what selling it into the latest observed book returns (depth walked,
unabsorbed quantity reported and valued at zero); positions with no readable
book are counted as unmeasured, never as zero. DAILY LOSS and DRAWDOWN: from
the book's own equity series (paper_equity_snapshots; execmirror_snapshots
for the actual mirror account).

AUDREY RECOMPUTES the headline figures by an independent, SQL-only path
(agents/audrey_intel_risk.py) and files a finding when they disagree.

GAME KEY (shared definition with Audrey's path, by specification, not by
shared code): the first fill's label->>'event_key', else the fill's
fixture, else the market slug. OPEN (actual): held quantity > 0 and the
contract has no paper settlement.
"""
from __future__ import annotations

from . import common as C

VERSION = "INTEL_RISK_V1"
DIMENSIONS = ("game", "team", "conference", "sport", "market_family",
              "live_state", "settlement_class", "venue")
OPEN_EPS = 1e-9
LOOKBACK_DAYS = 45.0
TOP_N = 25
POSITIONS_CAP = 200
#: The exposure bucket the simulated paper book reports under. A display
#: label for the venue dimension only: it is never handed to the venue
#: position model or to anything that resolves a venue.
PAPER_VENUE_BUCKET = "POLYMARKET_US_SIMULATED"


def game_key(label, fixture, slug) -> str:
    lb = C.jload(label) or {}
    ek = lb.get("event_key") if isinstance(lb, dict) else None
    return str(ek or fixture or slug or "UNKNOWN")


def aggregate_paper(fills: list, settlements: dict, *, account_id) -> list:
    """Raw paper fills -> positions (open and closed). Python path."""
    pos: dict = {}
    for f in sorted(fills, key=lambda x: (C.epoch(x.get("filled_at")) or 0)):
        k = (f["group_id"], f["us_market_slug"], str(f["holding_side"]))
        p = pos.setdefault(k, {
            "book": "PAPER", "venue": PAPER_VENUE_BUCKET,
            "group_id": k[0], "us_market_slug": k[1], "holding_side": k[2],
            "fixture": f.get("fixture"), "label": C.jload(f.get("label")),
            "strategy": f.get("strategy"), "bought": 0.0, "buy_cost": 0.0,
            "sold": 0.0})
        q = C.num(f["qty"]) or 0.0
        if f["direction"] == "BUY":
            p["bought"] += q
            p["buy_cost"] += (C.num(f["gross_usd"]) or 0.0) + (
                C.num(f["fee_usd"]) or 0.0)
        else:
            p["sold"] += q
    out = []
    for k, p in pos.items():
        pk = "paperpos:%s:%s:%s:%s" % (account_id, k[0], k[1], k[2])
        st = settlements.get(pk)
        p["settled"] = C.num(st["qty"]) if st else 0.0
        p["open_qty"] = p["bought"] - p["sold"] - p["settled"]
        p["avg_cost"] = (p["buy_cost"] / p["bought"]) if p["bought"] > 0 \
            else None
        p["game"] = game_key(p["label"], p["fixture"], p["us_market_slug"])
        out.append(p)
    return out


def aggregate_actual(fills: list, settled_slugs: set) -> list:
    """Venue fills (LONG-side wire prices) -> actual positions."""
    pos: dict = {}
    for f in fills:
        side = C.side_of_intent(f.get("intent")) if f.get("intent") else str(
            f.get("holding_side") or "LONG")
        k = (f.get("venue"), f.get("group_id") or "", f["us_market_slug"],
             side)
        p = pos.setdefault(k, {
            "book": "ACTUAL", "venue": f.get("venue"), "group_id": k[1],
            "us_market_slug": k[2], "holding_side": side,
            "fixture": f.get("fixture"), "label": C.jload(f.get("label")),
            "strategy": f.get("strategy"), "bought": 0.0, "buy_cost": 0.0,
            "sold": 0.0})
        q = C.num(f["qty"]) or 0.0
        if C.is_buy_intent(f.get("intent")):
            p["bought"] += q
            p["buy_cost"] += q * (C.cost_space(f["price"], side) or 0.0) + (
                C.num(f.get("fee_usd")) or 0.0)
        else:
            p["sold"] += q
    out = []
    for k, p in pos.items():
        held = p["bought"] - p["sold"]
        p["settled"] = held if p["us_market_slug"] in settled_slugs else 0.0
        p["open_qty"] = held - p["settled"]
        p["avg_cost"] = (p["buy_cost"] / p["bought"]) if p["bought"] > 0 \
            else None
        p["game"] = game_key(p["label"], p["fixture"], p["us_market_slug"])
        out.append(p)
    return out


def classify(p: dict, *, now, pm: dict | None, val: dict | None) -> dict:
    """Fill the dimensions of one position from its records."""
    pm = pm or {}
    val = val or {}
    lb = p.get("label") or {}
    team = pm.get("team_name")
    if not team and isinstance(lb, dict):
        pays = str(lb.get("pays_on") or "").upper()
        team = (lb.get("home_team") if pays == "HOME" else
                lb.get("away_team") if pays == "AWAY" else None)
    p["team"] = team or "UNKNOWN"
    p["conference"] = (lb.get("conference") if isinstance(lb, dict)
                       else None) or "UNKNOWN"
    p["sport"] = val.get("sport_family") or pm.get("sports_type") or "UNKNOWN"
    p["market_family"] = ((lb.get("market_type") if isinstance(lb, dict)
                           else None) or val.get("market") or "UNKNOWN")
    gs = C.num(pm.get("game_start"))
    p["game_start"] = gs
    p["live_state"] = ("UNKNOWN" if gs is None else
                       "LIVE" if now >= gs else "PREGAME")
    rule = val.get("settlement_rule")
    if not rule and p.get("strategy") == "PINNACLE_COMPLETED_GAME_PAPER":
        rule = "COMPLETED_GAME_MAY_SETTLE_AT_VENUE_PRICE"
    p["settlement_class"] = rule or "UNKNOWN"
    p["exposure_usd"] = (p["open_qty"] * p["avg_cost"]
                         if p["avg_cost"] is not None else None)
    return p


def clusters(open_pos: list) -> list:
    """Connected components over shared game or (sport, team)."""
    parent = list(range(len(open_pos)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen: dict = {}
    for i, p in enumerate(open_pos):
        keys = []
        if p["game"] != "UNKNOWN":
            keys.append(("game", p["game"]))
        if p["team"] != "UNKNOWN":
            keys.append(("team", p["sport"], p["team"]))
        for k in keys:
            if k in seen:
                parent[find(i)] = find(seen[k])
            else:
                seen[k] = i
    comp: dict = {}
    for i, p in enumerate(open_pos):
        comp.setdefault(find(i), []).append(p)
    out = []
    for members in comp.values():
        exp = sum(m["exposure_usd"] or 0.0 for m in members)
        out.append({"positions": len(members), "exposure_usd": C.rnd(exp),
                    "games": sorted({m["game"] for m in members}),
                    "teams": sorted({m["team"] for m in members}),
                    "members": [m["group_id"] for m in members][:20]})
    out.sort(key=lambda c: -(c["exposure_usd"] or 0.0))
    return out


def liquidity_at_risk(open_pos: list, books: dict) -> dict:
    out = C.Out()
    total, measured, unabsorbed, rows = 0.0, 0, 0.0, []
    for p in open_pos:
        b = books.get(p["us_market_slug"])
        if not b or b.get("mid") is None:
            rows.append({"group_id": p["group_id"], "lar_usd": None,
                         "why": "NO_READABLE_TWO_SIDED_BOOK"})
            continue
        mark = C.cost_space(b["mid"], p["holding_side"])
        w = C.exit_walk(b, p["holding_side"], p["open_qty"])
        lar = p["open_qty"] * mark - w["value_usd"]
        total += lar
        measured += 1
        unabsorbed += w["unabsorbed_qty"]
        rows.append({"group_id": p["group_id"], "lar_usd": C.rnd(lar),
                     "unabsorbed_qty": C.rnd(w["unabsorbed_qty"]),
                     "book_observed_at": b.get("observed_at")})
    n = len(open_pos)
    out["positions"] = n
    out["measured_positions"] = measured
    out["unmeasured_positions"] = n - measured
    if n == 0:
        out.put("liquidity_at_risk_usd", 0.0)
        out["basis"] = "NO_OPEN_POSITIONS"
    elif measured == 0:
        out.put("liquidity_at_risk_usd", None,
                "NO_OPEN_POSITION_HAS_A_READABLE_BOOK")
    else:
        out.put("liquidity_at_risk_usd", C.rnd(total))
        out["basis"] = ("SUM_OVER_MEASURED_POSITIONS_ONLY" if measured < n
                        else "ALL_OPEN_POSITIONS_MEASURED")
    out["complete"] = measured == n
    out["unabsorbed_qty"] = C.rnd(unabsorbed)
    out["method"] = ("open_qty x mid (cost space) - value of selling open_qty "
                     "into the latest observed book; unabsorbed qty at 0")
    out["by_position"] = rows[:TOP_N]
    return out


def equity_risk(series: list, *, now, start_peak=None) -> dict:
    """Daily loss and drawdown from an equity series [(epoch, equity)]."""
    out = C.Out(snapshots=len(series))
    pts = [(t, e) for t, e in series if e is not None]
    out["skipped_incomplete"] = len(series) - len(pts)
    if not pts:
        why = "NO_EQUITY_SNAPSHOT_WITH_COMPLETE_MARKS"
        for k in ("current_equity_usd", "peak_equity_usd",
                  "current_drawdown_usd", "current_drawdown_pct",
                  "max_drawdown_usd", "daily_pnl_usd", "daily_loss_usd"):
            out.put(k, None, why)
        return out
    peak = start_peak
    max_dd = 0.0
    for _, e in pts:
        peak = e if peak is None else max(peak, e)
        max_dd = max(max_dd, peak - e)
    cur = pts[-1][1]
    out.put("current_equity_usd", C.rnd(cur))
    out.put("peak_equity_usd", C.rnd(peak))
    out.put("current_drawdown_usd", C.rnd(peak - cur))
    out.put("current_drawdown_pct", C.rnd((peak - cur) / peak * 100.0)
            if peak else None, "PEAK_EQUITY_IS_ZERO")
    out.put("max_drawdown_usd", C.rnd(max_dd))
    ds = C.day_start(now)
    before = [e for t, e in pts if t < ds]
    today = [e for t, e in pts if t >= ds]
    base = before[-1] if before else (today[0] if today else None)
    if base is None or not today:
        out.put("daily_pnl_usd", None, "NO_EQUITY_SNAPSHOT_TODAY")
        out.put("daily_loss_usd", None, "NO_EQUITY_SNAPSHOT_TODAY")
    else:
        pnl = today[-1] - base
        out.put("daily_pnl_usd", C.rnd(pnl))
        out.put("daily_loss_usd", C.rnd(max(0.0, -pnl)))
    out["day_start"] = ds
    out["reporting_tz"] = C.REPORTING_TZ
    return out


def report(positions: list, *, book: str, now, books: dict,
           equity: dict) -> dict:
    """The risk payload for ONE book."""
    op = [p for p in positions if p["open_qty"] > OPEN_EPS]
    exposure: dict = {}
    for dim in DIMENSIONS:
        b: dict = {}
        for p in op:
            k = str(p.get(dim) or "UNKNOWN")
            e = b.setdefault(k, {"positions": 0, "exposure_usd": 0.0})
            e["positions"] += 1
            e["exposure_usd"] += p["exposure_usd"] or 0.0
        exposure[dim] = sorted(
            ({"key": k, "positions": v["positions"],
              "exposure_usd": C.rnd(v["exposure_usd"])}
             for k, v in b.items()),
            key=lambda x: -(x["exposure_usd"] or 0.0))[:TOP_N]
    gross = sum(p["exposure_usd"] or 0.0 for p in op)
    out = C.Out(C.envelope(version=VERSION, book=book, computed_at=now,
                           summed_with_other_book=False))
    out["open_positions"] = len(op)
    out.put("gross_exposure_usd", C.rnd(gross))
    games = exposure["game"]
    out.put("max_game_exposure_usd", games[0]["exposure_usd"] if games
            else 0.0)
    out["distinct_games"] = len({p["game"] for p in op})
    out.put("game_concentration_hhi", C.rnd(
        sum(((g["exposure_usd"] or 0.0) / gross) ** 2 for g in games))
        if gross > 0 else None, "NO_OPEN_EXPOSURE")
    out["exposure"] = exposure
    unk = {}
    for dim in DIMENSIONS:
        n = sum(1 for p in op if str(p.get(dim) or "UNKNOWN") == "UNKNOWN")
        if n:
            unk[dim] = n
    out["unknown_dimension_positions"] = unk
    out["dimension_notes"] = {
        "conference": ("no record in this system carries a conference; "
                       "UNKNOWN unless a label states one"),
        "settlement_class": ("the valuation's settlement_rule, else the "
                             "completed-game policy's venue-price clause, "
                             "else UNKNOWN"),
        "live_state": "against us_premap.game_start; UNKNOWN without one"}
    cl = clusters(op)
    out["clusters"] = cl[:TOP_N]
    out["largest_cluster_exposure_usd"] = cl[0]["exposure_usd"] if cl else 0.0
    out["liquidity"] = liquidity_at_risk(op, books)
    out["equity"] = equity
    out["positions"] = [
        {k: (C.rnd(v) if isinstance(v, float) else v) for k, v in p.items()
         if k not in ("label",)} for p in sorted(
            op, key=lambda x: -(x["exposure_usd"] or 0.0))[:POSITIONS_CAP]]
    return out


# ═════════════════════════════════════════════════════════════════════
# THE READ
# ═════════════════════════════════════════════════════════════════════

PAPER_FILLS_SQL = """
    SELECT group_id, us_market_slug, holding_side, fixture, label, strategy,
           direction, qty, gross_usd, fee_usd, filled_at
      FROM paper_fills
     WHERE account_id = $1
       AND group_id IN (SELECT DISTINCT group_id FROM paper_fills
                         WHERE account_id = $1
                           AND filled_at >= to_timestamp($2))
     ORDER BY filled_at
     LIMIT $3
"""

ACTUAL_FILLS_SQL = """
    SELECT 'POLYMARKET_US' AS venue, f.group_id, f.us_market_slug, f.intent,
           f.qty, f.price, f.fee_usd, o.strategy, NULL::text AS fixture,
           NULL::jsonb AS label, NULL::text AS holding_side
      FROM execmirror_fills f
      LEFT JOIN execmirror_orders o ON o.mirror_id = f.mirror_id
     WHERE f.observed_at >= to_timestamp($1)
    UNION ALL
    SELECT 'KALSHI', i.group_id, i.us_market_slug,
           CASE WHEN k.action = 'buy' THEN 'BUY_' ELSE 'SELL_' END
             || coalesce(i.holding, 'LONG'),
           k.count, k.price, k.fee_usd, NULL, NULL, NULL, i.holding
      FROM kalshi_live_fills k
      LEFT JOIN kalshi_live_intents i ON i.link_id = k.link_id
     WHERE k.observed_at >= to_timestamp($1)
     LIMIT $2
"""


async def _enrich(conn, positions: list, *, now) -> None:
    from . import reads as R

    op = [p for p in positions if p["open_qty"] > OPEN_EPS]
    pm = await R.premap(conn, [p["us_market_slug"] for p in op])
    gids = sorted({p["group_id"] for p in op if p.get("group_id")})
    vids = {}
    if gids:
        for r in await conn.fetch(
                "SELECT o.group_id, d.valuation_id FROM paper_orders o "
                "  JOIN paper_decisions d ON d.decision_id = o.decision_id "
                " WHERE o.group_id = ANY($1::text[]) AND o.role = 'ENTRY'",
                gids):
            if r["valuation_id"] is not None:
                vids[r["group_id"]] = int(r["valuation_id"])
    vals = await R.valuations_by_id(conn, vids.values())
    for p in positions:
        classify(p, now=now, pm=pm.get(p["us_market_slug"]),
                 val=vals.get(vids.get(p.get("group_id"))))


async def paper_report(conn, *, now, account_id=C.PAPER_ACCOUNT,
                       days=LOOKBACK_DAYS) -> dict:
    from . import reads as R

    fills = [dict(r) for r in await conn.fetch(
        PAPER_FILLS_SQL, account_id, float(now) - days * 86400.0,
        R.MAX_ROWS)]
    setts = await R.latest_settlements(conn, account_id=account_id)
    pos = aggregate_paper(fills, setts, account_id=account_id)
    await _enrich(conn, pos, now=now)
    books = await R.latest_books(conn, [p["us_market_slug"] for p in pos
                                        if p["open_qty"] > OPEN_EPS])
    snaps = await conn.fetch(
        "SELECT extract(epoch FROM at)::float8 AS at, equity_usd "
        "  FROM paper_equity_snapshots WHERE account_id = $1 "
        "   AND at >= to_timestamp($2) ORDER BY at LIMIT $3",
        account_id, float(now) - 60 * 86400.0, R.MAX_ROWS)
    eq = equity_risk([(float(s["at"]), C.num(s["equity_usd"]))
                      for s in snaps], now=now, start_peak=500000.0)
    eq["basis"] = "paper_equity_snapshots (fictional $500,000 account)"
    return report(pos, book="PAPER", now=now, books=books, equity=eq)


async def actual_report(conn, *, now, days=LOOKBACK_DAYS) -> dict:
    from . import reads as R

    fills = [dict(r) for r in await conn.fetch(
        ACTUAL_FILLS_SQL, float(now) - days * 86400.0, R.MAX_ROWS)]
    slugs = sorted({f["us_market_slug"] for f in fills
                    if f.get("us_market_slug")})
    settled = {r["us_market_slug"] for r in await conn.fetch(
        "SELECT DISTINCT us_market_slug FROM paper_settlements "
        " WHERE us_market_slug = ANY($1::text[])", slugs)} if slugs else set()
    pos = aggregate_actual(fills, settled)
    await _enrich(conn, pos, now=now)
    books = await R.latest_books(conn, [p["us_market_slug"] for p in pos
                                        if p["open_qty"] > OPEN_EPS])
    snaps = await conn.fetch(
        "SELECT extract(epoch FROM at)::float8 AS at, balances "
        "  FROM execmirror_snapshots WHERE at >= to_timestamp($1) "
        " ORDER BY at LIMIT $2", float(now) - 60 * 86400.0, R.MAX_ROWS)
    series = []
    for s in snaps:
        bal = C.jload(s["balances"]) or []
        e = None
        if bal and isinstance(bal[0], dict):
            cb = C.num(bal[0].get("currentBalance"))
            an = C.num(bal[0].get("assetNotional"))
            e = (cb + an) if (cb is not None and an is not None) else None
        series.append((float(s["at"]), e))
    eq = equity_risk(series, now=now)
    eq["basis"] = ("execmirror_snapshots: venue currentBalance + "
                   "assetNotional of the Polymarket US mirror account; the "
                   "Kalshi lane has no equity series in these records")
    return report(pos, book="ACTUAL", now=now, books=books, equity=eq)

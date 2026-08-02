"""Live ledger service — SQLite positions/settlement/PnL (build step 1).

This is the audited average-cost pipeline (positions.Position — ported from
the $21.45M reference history, do not rewrite) wrapped in a durable store.
Every mutation loads the position row into the audited dataclass, applies the
fill/resolution through THAT code path, and writes the result back — the
methodology has exactly one implementation.

Semantics preserved exactly:
  BUY:  position grows at cost; average re-weights.
  SELL: realizes (price - avg_cost) * size on the sale date; oversells clamp.
  RESOLVE: remaining shares realize (payout - avg_cost) * size on settle date.
  Open unresolved inventory is excluded from realized P&L, never zeroed.

Additions the live engine needs on top (none of which touch the math):
  * Idempotency: fills carry a unique fill_uid (INSERT OR IGNORE); a
    resolution applies once. Replaying a journal is always safe.
  * Fees: stored per fill and reported as a separate line (net = gross -
    fees). The audited methodology is fee-free (Polymarket taker fee = 0);
    Kalshi maker fills pass fee=0, taker fills pass the 0.07*p*(1-p) charge.
    Fees never enter avg_cost — gross stays comparable to the reference.
  * Decision record: every fill stores its full decision JSON (feed snapshot,
    fair value, book, threshold, caps state). If we lose, we can see why.
  * Realization journal: every SELL/RESOLVE writes a dated realization row so
    daily PnL and the grader read measured events, not reconstructions.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any

from .positions import EPS, Fill, Position

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fills (
    id          INTEGER PRIMARY KEY,
    fill_uid    TEXT NOT NULL UNIQUE,     -- idempotency key (venue order/fill id)
    venue       TEXT NOT NULL,
    market_key  TEXT NOT NULL,            -- venue-qualified market/outcome id
    league      TEXT,
    mode        TEXT NOT NULL DEFAULT 'PAPER'
                CHECK (mode IN ('PAPER', 'LIVE_BETA', 'LIVE')),
    side        TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    qty         REAL NOT NULL CHECK (qty > 0),
    price       REAL NOT NULL CHECK (price >= 0 AND price <= 1),
    fee         REAL NOT NULL DEFAULT 0 CHECK (fee >= 0),
    ts          REAL NOT NULL,
    -- What KIND of bet this was (moneyline / draw / spread / total / prop).
    -- Recorded at decision time rather than inferred later from a slug: the
    -- engine knows exactly what it priced, and a scorecard that cannot
    -- separate props from game lines cannot tell us which one is paying.
    category    TEXT,
    decision    TEXT                      -- full decision record JSON
);
CREATE INDEX IF NOT EXISTS fills_market_idx ON fills (market_key);
CREATE INDEX IF NOT EXISTS fills_ts_idx ON fills (ts);

CREATE TABLE IF NOT EXISTS positions (
    market_key   TEXT PRIMARY KEY,
    venue        TEXT NOT NULL,
    league       TEXT,
    shares       REAL NOT NULL DEFAULT 0,
    avg_cost     REAL NOT NULL DEFAULT 0,
    cost_in      REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    fees_paid    REAL NOT NULL DEFAULT 0,
    resolved     INTEGER NOT NULL DEFAULT 0,
    payout       REAL,
    resolved_ts  REAL
);

CREATE TABLE IF NOT EXISTS realizations (
    id          INTEGER PRIMARY KEY,
    market_key  TEXT NOT NULL,
    venue       TEXT NOT NULL,
    league      TEXT,
    kind        TEXT NOT NULL CHECK (kind IN ('sell', 'resolution')),
    qty         REAL NOT NULL,
    pnl         REAL NOT NULL,            -- gross (methodology) realization
    ts          REAL NOT NULL             -- sale date / settle date
);
CREATE INDEX IF NOT EXISTS realizations_ts_idx ON realizations (ts);

-- Engine state hub (kill switch, circuit-breaker halt, watchdog trips).
CREATE TABLE IF NOT EXISTS state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,             -- JSON
    updated_at REAL NOT NULL
);

-- Mapper match-rate accounting (build step 4: daily report, >95% gate).
CREATE TABLE IF NOT EXISTS match_stats (
    day         TEXT NOT NULL,            -- UTC date
    venue       TEXT NOT NULL,
    league      TEXT NOT NULL,
    feed_events INTEGER NOT NULL DEFAULT 0,
    mapped      INTEGER NOT NULL DEFAULT 0,   -- candidate found (>=0.85)
    tradeable   INTEGER NOT NULL DEFAULT 0,   -- confidence >=0.95
    PRIMARY KEY (day, venue, league)
);

-- One-position-per-event registry (beta hard rule: never add to an event).
CREATE TABLE IF NOT EXISTS events_traded (
    event_key  TEXT PRIMARY KEY,
    market_key TEXT NOT NULL,
    venue      TEXT NOT NULL,
    ts         REAL NOT NULL
);

-- Pricing-integrity surveillance. For every outcome we price, record how
-- far our fair value sits from the venue's own mid. A correctly mapped
-- market disagrees by cents; a MIS-mapped one (our price describes a
-- different bet) disagrees by tens of cents, systematically. When a
-- (venue, league, category) slice is mostly wild, it is quarantined — no
-- trading from it — regardless of what caused the mismatch.
CREATE TABLE IF NOT EXISTS pricing_divergence (
    day       TEXT NOT NULL,
    venue     TEXT NOT NULL,
    league    TEXT NOT NULL,
    category  TEXT NOT NULL,
    n         INTEGER NOT NULL DEFAULT 0,
    n_wild    INTEGER NOT NULL DEFAULT 0,   -- |fair - venue mid| > wild threshold
    sum_abs   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, venue, league, category)
);

-- Adverse-selection detector. Our fair value can be up to 30s old; the
-- venue's book is live. If the venue moves with the sharp books, then "the
-- ask is below our fair" often just means our fair is STALE and the price
-- already moved away from us — we would be systematically buying the side
-- the market just left. That produces an engine which looks like it has
-- edge and never profits, and settlement takes days to reveal it.
--
-- So: record the fair value at entry, then the fair value for the SAME
-- market a minute later. Edge that survives is real; edge that evaporates
-- was a stale quote. Continuous, and available within minutes.
--
-- Recorded PER CATEGORY, because drift is not one number. A three-way
-- match's draw leg is the longshot, which is exactly where book margin
-- concentrates and where de-vig methods disagree most; averaging it into
-- the moneyline figure hides the leg doing the damage. The category here
-- is the pricing category, with draws split out from the two-way sides.
CREATE TABLE IF NOT EXISTS edge_drift (
    market_key TEXT PRIMARY KEY,
    price      REAL NOT NULL,      -- what we paid
    fair_entry REAL NOT NULL,      -- fair value when we bought
    ts_entry   REAL NOT NULL,
    fair_later REAL,               -- fair value one observation later
    ts_later   REAL,
    category   TEXT
);

-- The same measurement, taken on outcomes we PRICED but did not buy.
--
-- edge_drift can only learn from fills, so raising the bar on what it
-- measures starves it: fewer fills, less evidence, a bar that can never
-- come back down. This table breaks that loop. Every priced outcome is a
-- free observation of how fast fair value moves, at thousands per hour
-- across every category, costing nothing.
--
-- It is NOT the same quantity and must never be substituted for it. Our
-- fills are selected — we get filled when someone wants to sell — so
-- edge_drift carries staleness AND adverse selection, while this carries
-- staleness alone and is therefore a floor. Its job is to say which
-- categories move fastest, not what trading costs us.
CREATE TABLE IF NOT EXISTS price_drift (
    obs_id     TEXT PRIMARY KEY,   -- market + hour bucket (one sample/hour)
    market_key TEXT NOT NULL,
    category   TEXT,
    price      REAL NOT NULL,
    fair_entry REAL NOT NULL,
    ts_entry   REAL NOT NULL,
    fair_later REAL,
    ts_later   REAL,
    -- Apparent edge at sample time (fair_entry - price, before fees). Kept
    -- so reversion can be measured AGAINST the size of the claim: the
    -- winner's-curse question is not "does fair move" but "does it move
    -- more where we thought we had the most edge".
    edge_entry REAL
);
CREATE INDEX IF NOT EXISTS ix_price_drift_open
    ON price_drift(market_key) WHERE fair_later IS NULL;

-- Mode transitions are audit events (PAPER -> LIVE_BETA -> LIVE).
CREATE TABLE IF NOT EXISTS mode_log (
    id   INTEGER PRIMARY KEY,
    ts   REAL NOT NULL,
    mode TEXT NOT NULL,
    note TEXT
);
"""


def window_start(days: int, since: float | None = None) -> float:
    """Start of a report window, as a Unix timestamp.

    `days` is a ROLLING window ending now, which is the right default for a
    live dashboard and the wrong thing entirely for a re-baseline: "since
    2026-08-01" is inexpressible as a rolling count, and days=7 asked on
    2026-08-02 silently reaches back to 2026-07-26. Every measured figure
    computed that way is labelled in-window and is not.

    `since` overrides it with an absolute instant. Ledger timestamps are
    zone-free Unix epoch seconds, so the boundary is unambiguous.
    """
    return float(since) if since is not None else time.time() - days * 86_400


class Ledger:
    """Thread-safe SQLite ledger. One instance per process; connections are
    per-call cheap (SQLite handles its own locking, WAL mode)."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or os.environ.get("EDGE_LEDGER_DB", "edge_ledger.sqlite3")
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            # In-place upgrades for existing ledgers: positions/realizations
            # carry the fill mode so live-money risk controls (circuit
            # breaker) never ingest paper numbers.
            for table in ("positions", "realizations"):
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN mode TEXT "
                                 f"NOT NULL DEFAULT 'PAPER'")
                except sqlite3.OperationalError:
                    pass  # column already exists
            # Drift gained a category dimension; existing rows stay NULL and
            # are reported under '?' rather than being silently reclassified.
            try:
                conn.execute("ALTER TABLE edge_drift ADD COLUMN category TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists
            try:
                conn.execute("ALTER TABLE price_drift ADD COLUMN edge_entry REAL")
            except sqlite3.OperationalError:
                pass  # column already exists
            try:
                conn.execute("ALTER TABLE fills ADD COLUMN category TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ── position load/store through the audited dataclass ──────────────

    @staticmethod
    def _to_position(row: sqlite3.Row | None) -> Position:
        if row is None:
            return Position()
        return Position(
            shares=row["shares"], avg_cost=row["avg_cost"],
            realized_pnl=row["realized_pnl"], cost_in=row["cost_in"],
            resolved=bool(row["resolved"]),
        )

    # ── writes ──────────────────────────────────────────────────────────

    def record_fill(
        self,
        fill_uid: str,
        venue: str,
        market_key: str,
        side: str,
        qty: float,
        price: float,
        ts: float | None = None,
        fee: float = 0.0,
        league: str | None = None,
        mode: str = "PAPER",
        decision: dict[str, Any] | None = None,
        category: str | None = None,
    ) -> dict:
        """Apply one fill. Returns {applied, realized} — applied=False means
        the fill_uid was already recorded (idempotent replay)."""
        ts = time.time() if ts is None else ts
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO fills
                    (fill_uid, venue, market_key, league, mode, side, qty, price,
                     fee, ts, category, decision)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (fill_uid, venue, market_key, league, mode, side.upper(), qty,
                 price, fee, ts, category,
                 json.dumps(decision) if decision else None),
            )
            if cur.rowcount == 0:
                return {"applied": False, "realized": 0.0}

            row = conn.execute(
                "SELECT * FROM positions WHERE market_key=?", (market_key,)
            ).fetchone()
            pos = self._to_position(row)
            shares_before = pos.shares
            realized = pos.apply(Fill(side.upper(), qty, price, ts))
            sold = shares_before - pos.shares if side.upper() == "SELL" else 0.0
            conn.execute(
                """
                INSERT INTO positions (market_key, venue, league, shares, avg_cost,
                                       cost_in, realized_pnl, fees_paid, resolved, mode)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT (market_key) DO UPDATE SET
                    shares=excluded.shares, avg_cost=excluded.avg_cost,
                    cost_in=excluded.cost_in, realized_pnl=excluded.realized_pnl,
                    fees_paid=positions.fees_paid + ?,
                    league=COALESCE(positions.league, excluded.league),
                    -- a live fill promotes the position to live risk-tracking
                    mode=CASE WHEN excluded.mode != 'PAPER' THEN excluded.mode
                              ELSE positions.mode END
                """,
                (market_key, venue, league, pos.shares, pos.avg_cost, pos.cost_in,
                 pos.realized_pnl, fee, int(pos.resolved), mode, fee),
            )
            if sold > EPS:  # a real sale — even a $0 realization is an event
                conn.execute(
                    "INSERT INTO realizations (market_key, venue, league, kind, qty, "
                    "pnl, ts, mode) VALUES (?,?,?,?,?,?,?,?)",
                    (market_key, venue, league, "sell", sold, realized, ts, mode),
                )
            return {"applied": True, "realized": realized}

    def record_resolution(
        self, market_key: str, payout: float, ts: float | None = None
    ) -> dict:
        """Settle a market at payout (1.0 / 0.0). Idempotent: applies once."""
        ts = time.time() if ts is None else ts
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE market_key=?", (market_key,)
            ).fetchone()
            if row is None or row["resolved"]:
                return {"applied": False, "realized": 0.0}
            pos = self._to_position(row)
            realized = pos.resolve(payout)
            conn.execute(
                """
                UPDATE positions SET shares=?, avg_cost=?, realized_pnl=?,
                       resolved=1, payout=?, resolved_ts=?
                WHERE market_key=?
                """,
                (pos.shares, pos.avg_cost, pos.realized_pnl, payout, ts, market_key),
            )
            # Always journal the settlement (qty = shares outstanding at resolve;
            # zero-remainder settles are legitimate 0-PnL events).
            row_mode = row["mode"] if "mode" in row.keys() else "PAPER"
            conn.execute(
                "INSERT INTO realizations (market_key, venue, league, kind, qty, "
                "pnl, ts, mode) VALUES (?,?,?,?,?,?,?,?)",
                (market_key, row["venue"], row["league"], "resolution",
                 row["shares"], realized, ts, row_mode),
            )
            return {"applied": True, "realized": realized}

    # ── reads ───────────────────────────────────────────────────────────

    def position(self, market_key: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE market_key=?", (market_key,)
            ).fetchone()
        return dict(row) if row else None

    def open_positions(self, live_only: bool = False) -> list[dict]:
        q = "SELECT * FROM positions WHERE resolved=0 AND shares > ?"
        if live_only:
            q += " AND mode != 'PAPER'"
        with self._conn() as conn:
            rows = conn.execute(q + " ORDER BY market_key", (EPS,)).fetchall()
        return [dict(r) for r in rows]

    def summary(self) -> dict:
        """Headline accounting: staked, gross/net realized, open exposure."""
        with self._conn() as conn:
            agg = conn.execute(
                """
                SELECT COALESCE(sum(realized_pnl), 0) AS gross,
                       COALESCE(sum(fees_paid), 0) AS fees,
                       COALESCE(sum(CASE WHEN resolved=0 THEN shares * avg_cost END), 0)
                           AS open_cost,
                       count(*) FILTER (WHERE resolved=1) AS resolved_markets,
                       count(*) FILTER (WHERE resolved=0 AND shares > 1e-9)
                           AS open_markets
                FROM positions
                """
            ).fetchone()
            staked = conn.execute(
                "SELECT COALESCE(sum(qty * price), 0) AS staked, count(*) AS fills "
                "FROM fills WHERE side='BUY'"
            ).fetchone()
        gross, fees = agg["gross"], agg["fees"]
        return {
            "fills": staked["fills"],
            "staked": staked["staked"],
            "gross_realized": gross,
            "fees": fees,
            "net_realized": gross - fees,
            "roi_net": (gross - fees) / staked["staked"] if staked["staked"] > 0 else 0.0,
            "open_markets": agg["open_markets"],
            "open_cost": agg["open_cost"],
            "resolved_markets": agg["resolved_markets"],
        }

    def spread_report(self, days: int = 7, live_only: bool = True,
                      since: float | None = None) -> dict:
        """What crossing the spread costs us, against what we claimed to win.

        The venue quotes in whole cents. At a 50c contract one tick is 2% of
        stake, and our claimed edge is 2-3c — so the spread is not a detail
        around the edge, it is the same size as the edge. If we pay most of
        it to cross, a negative result needs no model error to explain it.

        Every fill stored its book snapshot, so this is answerable from data
        already on disk rather than by waiting for new fills.

        `paid_over_mid` is the honest cost: mid is the best two-sided
        estimate of the market's own price, and anything above it is what we
        gave up for immediacy.
        """
        since = window_start(days, since)
        mode_clause = "AND mode != 'PAPER'" if live_only else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT price, category, decision FROM fills "
                f"WHERE side='BUY' AND ts >= ? AND decision IS NOT NULL "
                f"{mode_clause}", (since,)).fetchall()

        out: dict[str, dict] = {}
        for r in rows:
            try:
                d = json.loads(r["decision"])
            except (TypeError, ValueError):
                continue
            asks, bids = d.get("book_asks") or [], d.get("book_bids") or []
            if not asks or not bids:
                continue
            best_ask, best_bid = float(asks[0][0]), float(bids[0][0])
            if not (0 < best_bid < best_ask < 1):
                continue
            mid = (best_ask + best_bid) / 2.0
            cat = r["category"] or "unknown"
            b = out.setdefault(cat, {"n": 0, "spread": 0.0, "paid_over_mid": 0.0,
                                     "edge": 0.0})
            b["n"] += 1
            b["spread"] += best_ask - best_bid
            b["paid_over_mid"] += float(r["price"]) - mid
            b["edge"] += float(d.get("edge") or 0.0)

        for b in out.values():
            n = b["n"]
            b["spread_c"] = round(b.pop("spread") / n * 100, 2)
            b["paid_over_mid_c"] = round(b.pop("paid_over_mid") / n * 100, 2)
            b["edge_c"] = round(b.pop("edge") / n * 100, 2)
            # What is left of the claimed edge after paying to cross. If this
            # is negative we were losing at the moment of entry, whatever the
            # model said.
            b["net_c"] = round(b["edge_c"] - b["paid_over_mid_c"], 2)
        return out

    def performance_by_band(self, days: int = 7, live_only: bool = True,
                            since: float | None = None) -> dict:
        """Settled P&L by entry price band, graded on OUR OWN fills.

        config/bands.yaml is calibrated from the reference account's history
        on GLOBAL Polymarket. We trade Polymarket US — different venue,
        different flow — and that transfer has never been validated. This is
        how it gets validated, eventually: with enough settlements per band
        to say anything. It is a REPORT, not an auto-blocker; at a few
        hundred settlements across twenty bands there is nothing to act on.
        """
        since = window_start(days, since)
        mode_clause = "AND f.mode != 'PAPER'" if live_only else ""
        with self._conn() as conn:
            rows = [dict(x) for x in conn.execute(
                f"""
                SELECT f.price AS price, r.pnl AS pnl,
                       COALESCE(sum(f.qty * f.price), 0) AS staked
                FROM realizations r
                JOIN fills f ON f.market_key = r.market_key AND f.side = 'BUY'
                WHERE r.ts >= ? AND r.kind = 'resolution' {mode_clause}
                GROUP BY r.id
                """, (since,)).fetchall()]
        out: dict[str, dict] = {}
        for r in rows:
            if r["staked"] <= 0:
                continue
            lo = int(float(r["price"]) * 20) * 5 / 100
            key = f"{lo:.2f}-{lo + 0.05:.2f}"
            b = out.setdefault(key, {"settled": 0, "staked": 0.0, "realized": 0.0})
            b["settled"] += 1
            b["staked"] += r["staked"]
            b["realized"] += r["pnl"]
        for b in out.values():
            b["staked"] = round(b["staked"], 2)
            b["realized"] = round(b["realized"], 2)
            b["roi"] = round(b["realized"] / b["staked"], 4) if b["staked"] else None
        return dict(sorted(out.items()))

    def performance_by_category(self, days: int = 7,
                                live_only: bool = True,
                                since: float | None = None) -> dict:
        """Settled scorecard split by what KIND of bet it was.

        The blended number cannot answer the only question that matters right
        now: props showed zero adverse drift while game lines showed -2.5c,
        but drift compares our own fair value against itself and is blind to
        a mapping error. If a prop is mapped to the wrong line, drift reads
        perfectly clean and settlement is where it shows up. This is that.
        """
        since = window_start(days, since)
        mode_clause = "AND f.mode != 'PAPER'" if live_only else ""
        with self._conn() as conn:
            rows = [dict(x) for x in conn.execute(
                f"""
                SELECT COALESCE(f.category, 'unknown') AS category,
                       r.pnl AS pnl,
                       COALESCE(sum(f.qty * f.price), 0) AS staked
                FROM realizations r
                JOIN fills f ON f.market_key = r.market_key AND f.side = 'BUY'
                WHERE r.ts >= ? AND r.kind = 'resolution' {mode_clause}
                GROUP BY r.id
                """, (since,)).fetchall()]
        out: dict[str, dict] = {}
        for r in rows:
            if r["staked"] <= 0:
                continue
            b = out.setdefault(r["category"], {
                "settled": 0, "wins": 0, "losses": 0,
                "staked": 0.0, "realized": 0.0})
            b["settled"] += 1
            b["wins"] += 1 if r["pnl"] > 0 else 0
            b["losses"] += 1 if r["pnl"] < 0 else 0
            b["staked"] += r["staked"]
            b["realized"] += r["pnl"]
        for b in out.values():
            b["staked"] = round(b["staked"], 2)
            b["realized"] = round(b["realized"], 2)
            b["roi"] = round(b["realized"] / b["staked"], 4) if b["staked"] else None
            b["win_rate"] = (round(b["wins"] / b["settled"], 4)
                             if b["settled"] else None)
        return out

    def performance(self, days: int = 7, live_only: bool = True,
                    since: float | None = None) -> dict:
        """Settled scorecard straight from the engine's OWN ledger.

        This exists because the platform API — the usual place to read
        results — can lag the engine by a deploy, and 'is this making money'
        should never be blocked on unrelated infrastructure. The engine
        settles its own positions (settle_cycle) and can therefore answer for
        itself, in the same telemetry that already reaches the operator.

        Wins and losses are counted per RESOLUTION, not per fill: buy-and-hold
        means one settlement per market, and that is the unit that either paid
        or did not. ROI is over settled stake only — charging an open
        position's cost against a zero return reports a loss that has not
        happened yet.
        """
        since = window_start(days, since)
        mode_clause = "AND f.mode != 'PAPER'" if live_only else ""
        with self._conn() as conn:
            res = conn.execute(
                f"""
                SELECT r.pnl AS pnl,
                       (SELECT COALESCE(sum(qty * price), 0) FROM fills f
                         WHERE f.market_key = r.market_key AND f.side='BUY'
                         {mode_clause}) AS staked
                FROM realizations r
                WHERE r.ts >= ? AND r.kind = 'resolution'
                """, (since,)).fetchall()
            rows = [dict(x) for x in res if x["staked"] > 0]
            fills = conn.execute(
                f"SELECT count(*) n, COALESCE(sum(qty*price),0) s FROM fills f "
                f"WHERE side='BUY' AND ts >= ? {mode_clause}", (since,)).fetchone()
            open_cost = conn.execute(
                """SELECT COALESCE(sum(shares * avg_cost), 0) FROM positions
                   WHERE resolved = 0 AND shares > 1e-9""").fetchone()[0]
        wins = [r for r in rows if r["pnl"] > 0]
        settled_stake = sum(r["staked"] for r in rows)
        realized = sum(r["pnl"] for r in rows)

        # How far from zero is this, in its own noise? A point estimate with
        # no dispersion behind it invites reading a losing week as evidence
        # of a broken engine and a winning one as evidence of an edge, when
        # at these sample sizes neither is distinguishable from nothing. The
        # unit is per-trade return, (payout - stake) / stake, so a $1 and a
        # $1.50 ticket contribute equally to the shape of the distribution.
        rets = [r["pnl"] / r["staked"] for r in rows]
        n = len(rets)
        mean = sum(rets) / n if n else None
        sd = se = sigma = None
        if n > 1:
            var = sum((x - mean) ** 2 for x in rets) / (n - 1)
            sd = var ** 0.5
            if sd > 0:
                se = sd / (n ** 0.5)
                sigma = abs(mean) / se

        return {
            "days": days, "live_only": live_only,
            "fills": int(fills["n"]), "staked": round(float(fills["s"]), 2),
            "settled": len(rows), "wins": len(wins),
            "losses": len(rows) - len(wins),
            "win_rate": round(len(wins) / len(rows), 4) if rows else None,
            "realized": round(realized, 2),
            "roi": round(realized / settled_stake, 4) if settled_stake else None,
            "open_cost": round(float(open_cost), 2),
            "mean_return": round(mean, 6) if mean is not None else None,
            "sd_per_trade": round(sd, 4) if sd is not None else None,
            "se": round(se, 6) if se is not None else None,
            "sigma_from_zero": round(sigma, 2) if sigma is not None else None,
        }

    def performance_daily(self, days: int = 7, live_only: bool = True,
                          since: float | None = None) -> list[dict]:
        """Settled P&L per UTC day, newest first.

        Bucketed on `realizations.ts`, which is the settlement instant the
        engine recorded when it resolved the position. (The platform's
        account view buckets everything as 'undated' because the venue's
        resolution activities carry no timestamp — that is a limitation of
        that path, not of this one. Grading days off the engine's own clock
        is both available and more defensible.)
        """
        since = window_start(days, since)
        mode_clause = "AND f.mode != 'PAPER'" if live_only else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT date(r.ts, 'unixepoch') AS day,
                       r.pnl AS pnl,
                       (SELECT COALESCE(sum(qty * price), 0) FROM fills f
                         WHERE f.market_key = r.market_key AND f.side='BUY'
                         {mode_clause}) AS staked
                FROM realizations r
                WHERE r.ts >= ? AND r.kind = 'resolution'
                """, (since,)).fetchall()
        by_day: dict[str, dict] = {}
        for r in rows:
            if r["staked"] <= 0:
                continue
            d = by_day.setdefault(r["day"], {
                "day": r["day"], "settled": 0, "wins": 0, "losses": 0,
                "staked": 0.0, "realized": 0.0})
            d["settled"] += 1
            d["wins"] += 1 if r["pnl"] > 0 else 0
            d["losses"] += 1 if r["pnl"] < 0 else 0
            d["staked"] += r["staked"]
            d["realized"] += r["pnl"]
        for d in by_day.values():
            d["staked"] = round(d["staked"], 2)
            d["realized"] = round(d["realized"], 2)
            d["roi"] = round(d["realized"] / d["staked"], 4) if d["staked"] else None
        return sorted(by_day.values(), key=lambda d: d["day"], reverse=True)

    def positions_export(self, days: int = 7, live_only: bool = True,
                         since: float | None = None) -> list[dict]:
        """One row per position, with everything needed to audit the trade.

        This is the artifact that makes a measured claim checkable by someone
        who does not trust the summary: entry, what we thought it was worth,
        what the bar was, what we staked, and how it resolved.

        `fair_at_plus_60s` comes from `edge_drift`, which is keyed by MARKET,
        not by fill. Under one-position-per-market that is one observation per
        position and the join is exact; where a market carries several entries
        (which should not happen, and did on 2026-08-02) the column describes
        the market, not the individual entry, and `entries` says so.
        """
        since = window_start(days, since)
        mode_clause = "AND f.mode != 'PAPER'" if live_only else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT p.market_key, p.venue, p.league, p.shares, p.avg_cost,
                       p.cost_in, p.realized_pnl, p.fees_paid, p.resolved,
                       p.payout, p.resolved_ts,
                       (SELECT count(*) FROM fills f
                         WHERE f.market_key = p.market_key AND f.side='BUY'
                         {mode_clause}) AS entries,
                       (SELECT min(ts) FROM fills f
                         WHERE f.market_key = p.market_key AND f.side='BUY'
                         {mode_clause}) AS entry_ts,
                       (SELECT f.decision FROM fills f
                         WHERE f.market_key = p.market_key AND f.side='BUY'
                         {mode_clause} ORDER BY f.ts LIMIT 1) AS decision,
                       (SELECT f.category FROM fills f
                         WHERE f.market_key = p.market_key AND f.side='BUY'
                         {mode_clause} ORDER BY f.ts LIMIT 1) AS category,
                       d.fair_entry, d.fair_later, d.price AS drift_price
                FROM positions p
                LEFT JOIN edge_drift d ON d.market_key = p.market_key
                """).fetchall()
        out = []
        for r in rows:
            if not r["entry_ts"] or r["entry_ts"] < since:
                continue
            try:
                dec = json.loads(r["decision"]) if r["decision"] else {}
            except ValueError:
                dec = {}
            out.append({
                "market_key": r["market_key"],
                "venue": r["venue"],
                "league": r["league"],
                "category": r["category"] or dec.get("category"),
                "band": dec.get("band"),
                "tier": dec.get("tier"),
                "entries": int(r["entries"] or 0),
                "entry_ts": r["entry_ts"],
                "settle_ts": r["resolved_ts"],
                "entry_price": round(float(r["avg_cost"]), 4),
                "shares": round(float(r["shares"]), 4),
                "stake": round(float(r["cost_in"]), 4),
                "fair_at_entry": r["fair_entry"],
                "fair_at_plus_60s": r["fair_later"],
                "edge_claimed": dec.get("edge"),
                "threshold_required": dec.get("threshold"),
                "resolved": bool(r["resolved"]),
                "payout": r["payout"],
                "pnl": round(float(r["realized_pnl"]) - float(r["fees_paid"]), 4)
                       if r["resolved"] else None,
            })
        return sorted(out, key=lambda d: d["entry_ts"], reverse=True)

    # ── adverse selection: does the edge survive the next observation? ──

    DRIFT_MIN_AGE_S = 60.0

    def record_entry_fair(self, market_key: str, price: float, fair: float,
                          category: str | None = None,
                          ts: float | None = None) -> None:
        """Stamp the fair value we bought against. First write wins — a
        market is entered once, and re-stamping would reset the clock."""
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO edge_drift "
                "(market_key, price, fair_entry, ts_entry, category) "
                "VALUES (?,?,?,?,?)",
                (market_key, price, fair,
                 ts if ts is not None else time.time(), category))

    def awaiting_drift(self, now: float | None = None) -> set[str]:
        """Markets bought long enough ago to be worth re-observing."""
        cutoff = (now or time.time()) - self.DRIFT_MIN_AGE_S
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT market_key FROM edge_drift "
                "WHERE fair_later IS NULL AND ts_entry <= ?", (cutoff,)).fetchall()
        return {r["market_key"] for r in rows}

    def record_drift_later(self, market_key: str, fair_later: float,
                           ts: float | None = None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE edge_drift SET fair_later=?, ts_later=? "
                "WHERE market_key=? AND fair_later IS NULL",
                (fair_later, ts if ts is not None else time.time(), market_key))

    # A drift estimate needs enough observations to be worth acting on, and
    # a ceiling so one noisy week cannot silently halt all trading.
    DRIFT_MIN_N = 12
    DRIFT_MAX_PENALTY = 0.03

    @staticmethod
    def _drift_stats(rows: list[dict]) -> dict:
        if not rows:
            return {"n": 0, "retention": None, "mean_drift_c": None,
                    "mean_drift": None, "held": 0}
        entry = [r["fair_entry"] - r["price"] for r in rows]
        later = [r["fair_later"] - r["price"] for r in rows]
        gross = sum(entry)
        mean_drift = sum(l - e for l, e in zip(later, entry)) / len(rows)
        return {
            "n": len(rows),
            # Edge remaining as a share of edge claimed. Summed rather than
            # averaged per-trade so a near-zero entry edge cannot blow up the
            # ratio.
            "retention": round(sum(later) / gross, 3) if abs(gross) > 1e-9 else None,
            "mean_drift": mean_drift,
            "mean_drift_c": round(mean_drift * 100, 2),
            "held": sum(1 for l, e in zip(later, entry) if l >= e * 0.5),
        }

    def _drift_rows(self, days: int, category: str | None = None,
                    since: float | None = None) -> list[dict]:
        since = window_start(days, since)
        sql = ("SELECT price, fair_entry, fair_later, category FROM edge_drift "
               "WHERE fair_later IS NOT NULL AND ts_entry >= ?")
        params: list = [since]
        if category is not None:
            sql += " AND COALESCE(category, '?') = ?"
            params.append(category)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def drift_report(self, days: int = 7, category: str | None = None,
                     since: float | None = None) -> dict:
        """How much of the edge we thought we had was still there a minute
        later. retention ~1.0 = the edge was real; ~0 or negative = we were
        buying quotes that had already moved.

        Reported per category as well as overall: a single blended number
        hides the one leg that is doing the damage."""
        rows = self._drift_rows(days, category, since)
        report = self._drift_stats(rows)
        if category is None:
            buckets: dict[str, list[dict]] = {}
            for r in rows:
                buckets.setdefault(r.get("category") or "?", []).append(r)
            report["by_category"] = {
                k: self._drift_stats(v) for k, v in sorted(buckets.items())}
        return report

    def record_price_observation(self, obs_id: str, market_key: str,
                                 price: float, fair: float,
                                 category: str | None = None,
                                 edge: float | None = None,
                                 ts: float | None = None) -> None:
        """A free drift sample from an outcome we priced but did not buy."""
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO price_drift (obs_id, market_key, "
                "category, price, fair_entry, ts_entry, edge_entry) "
                "VALUES (?,?,?,?,?,?,?)",
                (obs_id, market_key, category, price, fair,
                 ts if ts is not None else time.time(),
                 fair - price if edge is None else edge))

    def awaiting_price_drift(self, now: float | None = None) -> dict[str, str]:
        """{market_key: obs_id} for samples old enough to re-observe. One
        open sample per market — the newest wins if somehow several exist."""
        cutoff = (now or time.time()) - self.DRIFT_MIN_AGE_S
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT obs_id, market_key FROM price_drift "
                "WHERE fair_later IS NULL AND ts_entry <= ? ORDER BY ts_entry",
                (cutoff,)).fetchall()
        return {r["market_key"]: r["obs_id"] for r in rows}

    def record_price_drift_later(self, obs_id: str, fair_later: float,
                                 ts: float | None = None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE price_drift SET fair_later=?, ts_later=? "
                "WHERE obs_id=? AND fair_later IS NULL",
                (fair_later, ts if ts is not None else time.time(), obs_id))

    def price_drift_report(self, days: int = 7,
                           since: float | None = None) -> dict:
        """Staleness by category, measured without spending anything."""
        since = window_start(days, since)
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT price, fair_entry, fair_later, category FROM price_drift "
                "WHERE fair_later IS NOT NULL AND ts_entry >= ?",
                (since,)).fetchall()]
        report = self._drift_stats(rows)
        buckets: dict[str, list[dict]] = {}
        for r in rows:
            buckets.setdefault(r.get("category") or "?", []).append(r)
        report["by_category"] = {
            k: self._drift_stats(v) for k, v in sorted(buckets.items())}
        return report

    # Reversion needs a real spread of claim sizes before a slope means
    # anything, and far more samples than a mean does.
    REVERSION_MIN_N = 200
    REVERSION_MIN_SPREAD = 0.005   # sd of apparent edge, in probability

    def reversion(self, days: int = 7, category: str | None = None,
                  since: float | None = None) -> dict:
        """How much of an APPARENT edge gives itself back.

        The winner's-curse question is not "does fair value move" — we have
        measured that it does not, 0.0c across hundreds of free samples. It
        is whether fair moves back further where we claimed the most edge.
        We buy where fair - price is largest, which preferentially selects
        the moments our own estimate is most wrong; those revert.

        Regress the later move on the claim:  (fair_later - fair_entry)
        against (fair_entry - price). A slope of -0.7 says 70% of any
        apparent edge is our own noise and hands itself back.

        Returned as `keep` — the fraction of an apparent edge that is real —
        so it multiplies the edge directly. `keep` is None until there are
        enough samples across a wide enough range of claims to mean
        anything; callers must treat None as "no opinion", not as 1.0.
        """
        since = window_start(days, since)
        sql = ("SELECT price, fair_entry, fair_later, edge_entry FROM price_drift "
               "WHERE fair_later IS NOT NULL AND ts_entry >= ?")
        params: list = [since]
        if category is not None:
            sql += " AND COALESCE(category, '?') = ?"
            params.append(category)
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        n = len(rows)
        out = {"n": n, "slope": None, "keep": None, "spread": None}
        if n < self.REVERSION_MIN_N:
            return out
        xs = [(r["edge_entry"] if r["edge_entry"] is not None
               else r["fair_entry"] - r["price"]) for r in rows]
        ys = [r["fair_later"] - r["fair_entry"] for r in rows]
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        spread = (sxx / n) ** 0.5
        out["spread"] = round(spread, 5)
        if spread < self.REVERSION_MIN_SPREAD or sxx <= 0:
            return out       # every claim the same size: slope is meaningless
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        slope = sxy / sxx
        out["slope"] = round(slope, 4)
        # Reversion is a NEGATIVE slope. A positive slope means apparent edge
        # keeps growing, which we refuse to pay ourselves for: keep caps at 1.
        out["keep"] = round(min(1.0, max(0.0, 1.0 + slope)), 4)
        return out

    def drift_penalties(self, days: int = 7,
                        since: float | None = None) -> dict[str, float]:
        """Measured adverse drift, as an edge surcharge per category.

        The entry threshold is a bar our ESTIMATE must clear. If fair value
        systematically moves against us right after we buy, the estimate is
        optimistic by exactly that much, and a bar that ignores it is a bar
        set below the real cost of trading. So the surcharge is the measured
        adverse drift itself.

        Only adverse drift is charged — favourable drift does not lower the
        bar, because being early is not a licence to be looser. Categories
        with too few observations inherit the overall number rather than
        trading free; if nothing is measured yet the surcharge is zero and
        the bands govern alone, as they did before this existed.

        Keys are categories plus '*' for the fallback.
        """
        overall = self._drift_stats(self._drift_rows(days, since=since))

        def surcharge(stats: dict) -> float | None:
            if stats["n"] < self.DRIFT_MIN_N or stats["mean_drift"] is None:
                return None
            return round(min(max(0.0, -stats["mean_drift"]),
                             self.DRIFT_MAX_PENALTY), 4)

        base = surcharge(overall) or 0.0
        out: dict[str, float] = {"*": base}
        for cat, stats in (self.drift_report(days, since=since)
                           .get("by_category") or {}).items():
            out[cat] = surcharge(stats) if surcharge(stats) is not None else base
        return out

    def event_exposure(self, event_key: str, mode: str) -> dict:
        """Everything we hold on ONE real-world game.

        per_market_exposure bounds a single bet; nothing bounded the EVENT.
        A three-way soccer match is three markets, and a game carrying a
        moneyline plus a spread ladder plus a totals ladder is twenty or
        more — so a $1 per-market cap quietly permits $20 of correlated
        exposure on one result. The per-market cap gave false comfort.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(sum(qty * price), 0) AS cost, "
                "       count(DISTINCT market_key) AS markets "
                "FROM fills WHERE side='BUY' AND mode = ? "
                "AND json_extract(decision, '$.event_key') = ?",
                (mode, event_key)).fetchone()
        return {"cost": float(row["cost"]), "markets": int(row["markets"])}

    def daily_pnl(self) -> list[dict]:
        """Gross realized PnL per UTC day, from the realization journal."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT date(ts, 'unixepoch') AS day,
                       sum(pnl) AS pnl, count(*) AS events
                FROM realizations GROUP BY day ORDER BY day
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def by_league(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT COALESCE(league, '?') AS league,
                       sum(realized_pnl) AS gross, sum(fees_paid) AS fees,
                       count(*) AS markets
                FROM positions GROUP BY league ORDER BY gross DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def realized_pnl_since(self, ts: float, live_only: bool = False) -> float:
        """Gross realized PnL since ts. live_only=True restricts to
        live-money realizations — the ONLY correct circuit-breaker input
        (paper marks must never halt real trading)."""
        q = "SELECT COALESCE(sum(pnl), 0) FROM realizations WHERE ts >= ?"
        if live_only:
            q += " AND mode != 'PAPER'"
        with self._conn() as conn:
            row = conn.execute(q, (ts,)).fetchone()
        return float(row[0])

    def live_fill_count_since(self, ts: float) -> int:
        """Real-money fills in the window (bogus-halt detection input)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT count(*) FROM fills WHERE ts >= ? AND mode != 'PAPER'", (ts,)
            ).fetchone()
        return int(row[0])

    def live_staked_since(self, ts: float) -> float:
        """Real money actually put at risk in the window. A live loss can
        never exceed this — the bound that identifies bogus halts."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(sum(qty * price), 0) FROM fills "
                "WHERE ts >= ? AND side='BUY' AND mode != 'PAPER'", (ts,)
            ).fetchone()
        return float(row[0])

    # ── engine state hub ────────────────────────────────────────────────

    def set_state(self, key: str, value: Any) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO state (key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT (key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (key, json.dumps(value), time.time()),
            )

    def get_state(self, key: str, default: Any = None) -> Any:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def clear_state(self, key: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM state WHERE key=?", (key,))

    def list_state(self, prefix: str) -> dict[str, Any]:
        """All state entries under a prefix — how the maker reaper finds the
        orders it parked without keeping an in-memory registry that a restart
        would lose."""
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM state WHERE key LIKE ?",
                                (prefix + "%",)).fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def log_mode(self, mode: str, note: str = "") -> None:
        with self._lock, self._conn() as conn:
            conn.execute("INSERT INTO mode_log (ts, mode, note) VALUES (?,?,?)",
                         (time.time(), mode, note))

    def mode_transitions(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ts, mode, note FROM mode_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── one-position-per-event registry ─────────────────────────────────

    def claim_event(self, event_key: str, market_key: str, venue: str,
                    ts: float | None = None) -> bool:
        """Atomically claim an event. False = already traded (never add)."""
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO events_traded (event_key, market_key, venue, ts) "
                "VALUES (?,?,?,?)",
                (event_key, market_key, venue, ts if ts is not None else time.time()),
            )
            return cur.rowcount > 0

    def release_event(self, event_key: str) -> bool:
        """Give a claim back. A claim exists to stop us ADDING to an event we
        already hold; an order that rested and never filled left us holding
        nothing, so keeping its claim would silently retire the event for the
        rest of the day. Callers must confirm no position exists first."""
        with self._lock, self._conn() as conn:
            cur = conn.execute("DELETE FROM events_traded WHERE event_key=?",
                               (event_key,))
            return cur.rowcount > 0

    def event_traded(self, event_key: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM events_traded WHERE event_key=?", (event_key,)
            ).fetchone()
        return row is not None

    # ── mapper match-rate accounting ────────────────────────────────────

    def record_match_stat(self, day: str, venue: str, league: str,
                          mapped: bool, tradeable: bool) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO match_stats (day, venue, league, feed_events, mapped, tradeable)
                VALUES (?,?,?,1,?,?)
                ON CONFLICT (day, venue, league) DO UPDATE SET
                    feed_events = feed_events + 1,
                    mapped = mapped + excluded.mapped,
                    tradeable = tradeable + excluded.tradeable
                """,
                (day, venue, league, int(mapped), int(tradeable)),
            )

    def match_rate_report(self, days: int = 1) -> list[dict]:
        """Per-venue/league match rates over the last N days (step 4 report;
        check-live requires tradeable_rate > 0.95 on allowlisted leagues)."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT venue, league, sum(feed_events) AS feed_events,
                       sum(mapped) AS mapped, sum(tradeable) AS tradeable
                FROM match_stats
                WHERE day >= date('now', ?)
                GROUP BY venue, league ORDER BY venue, league
                """,
                (f"-{days} day",),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            n = d["feed_events"] or 1
            d["mapped_rate"] = round(d["mapped"] / n, 4)
            d["tradeable_rate"] = round(d["tradeable"] / n, 4)
            out.append(d)
        return out

    # ── pricing-integrity surveillance ──────────────────────────────────

    WILD_DIVERGENCE = 0.10       # |fair - venue mid| beyond this is "wild"
    QUARANTINE_MIN_N = 25        # need a real sample before judging a slice
    QUARANTINE_WILD_SHARE = 0.5  # majority wild = the slice is mis-mapped

    def record_divergence(self, day: str, venue: str, league: str,
                          category: str, abs_div: float) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO pricing_divergence (day, venue, league, category,
                                                n, n_wild, sum_abs)
                VALUES (?,?,?,?,1,?,?)
                ON CONFLICT (day, venue, league, category) DO UPDATE SET
                    n = n + 1,
                    n_wild = n_wild + excluded.n_wild,
                    sum_abs = sum_abs + excluded.sum_abs
                """,
                (day, venue, league or "?", category,
                 int(abs_div > self.WILD_DIVERGENCE), abs_div),
            )

    def divergence_report(self, days: int = 1) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT venue, league, category, sum(n) AS n,
                       sum(n_wild) AS n_wild, sum(sum_abs) AS sum_abs
                FROM pricing_divergence WHERE day >= date('now', ?)
                GROUP BY venue, league, category ORDER BY n_wild DESC
                """,
                (f"-{days} day",),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            n = d["n"] or 1
            d["wild_share"] = round(d["n_wild"] / n, 3)
            d["mean_abs_div"] = round(d["sum_abs"] / n, 4)
            d["quarantined"] = (d["n"] >= self.QUARANTINE_MIN_N
                                and d["wild_share"] > self.QUARANTINE_WILD_SHARE)
            out.append(d)
        return out

    def quarantined_slices(self, days: int = 1) -> set[tuple[str, str, str]]:
        """(venue, league, category) triples currently barred from trading
        because our prices systematically disagree with the venue's own."""
        return {(r["venue"], r["league"], r["category"])
                for r in self.divergence_report(days) if r["quarantined"]}

    def decision_record(self, fill_uid: str) -> dict | None:
        """Full audit trail for one fill — why we traded."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM fills WHERE fill_uid=?", (fill_uid,)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["decision"] = json.loads(d["decision"]) if d["decision"] else None
        return d

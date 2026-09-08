"""Lane M (PNL program, 2026-09-08) -- the adversarial review's pins on the
five render-ops presets: paired-ratio, fills-missed, on-target-why,
cand-refusals, flow-books. Every SQL pin runs the preset's OWN text (read
from the workflow) on a real Postgres carrying the 001-058 migrations and
a fixture whose figures are derived by hand in the docstrings, the way the
E9 / E13 review pins do (skipped, never faked, without a local server).

A test whose name carried `_DEFECT_` pinned the preset AS IT STOOD -- the
figure it printed, beside the figure the column name promises. FOLDED
2026-09-08: every one of the seven flipped to the true figure on purpose
(the names now carry `_folded_`; the bodies and the derivations stand,
the assertion reads the corrected preset). The findings, as they were:

  HIGH-1  paired-ratio `share_frac_pct` divides a SHORT book's stake by
          its contract-price average; the leg's stake is leg x (1 - avg)
          (rules.book_buy / _cost_px), so the short's share reads
          (1 - avg) / avg times its true value (23.3 % for 10 %).
  HIGH-2  paired-ratio `his_cost_net` on a market whose book FLIPPED:
          his_net_peak is the larger |running| on EITHER side, the vwap
          is the first book's side only -- peak x vwap is no cost he paid.
  HIGH-3  fills-missed `band_c` floors / ceils his cent in float8: 0.29
          x 100 = 28.999.., 0.55 x 100 = 55.000..01, so a rest AT his
          cent reads 1c wide on those cents (at_or_through 71.4 for 100).
  HIGH-4  on-target-why `stale_snapshot` compares target_raw with ratio
          x his_net, but on a flow book target_raw is sized on his_net
          MINUS the block: every on_target fill after the open of a
          flow-only book reads stale_snapshot (the block's dollars).
  HIGH-5  flow-books `catchup_side` reads last_plan->'catchup', which the
          worker writes on the book's FIRST plan only (every later plan
          is built fresh); the per-side dollars sum books on their first
          tick and nothing else.
  MEDIUM-1 cand-refusals `first_verdict_after_his_last` reads the first
          verdict INSIDE the 24 h window; a verdict older than the window
          that preceded his fills is invisible, so the dark window
          over-counts.
  MEDIUM-2 fills-missed takes the LATEST book's axis per market while
          paired-ratio takes the FIRST; on a flipped market the fill our
          first book answered (and filled) is read as reducing and dropped.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import pathlib
import re
import uuid

import pytest

from sportsassets.scripts import migrate
from sportsassets.workers import mirror_live as ml

YML = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
MIG_DIR = pathlib.Path(migrate.MIGRATIONS_DIR)
DSN_BASE = os.environ.get(
    "MIRROR_SQL_PIN_DSN",
    os.environ.get("S1_SQL_PIN_DSN",
                   "postgresql://sportsassets:sportsassets@localhost:5432/postgres"))
# premap.py's own DDL for the table migrations 031 / 055 alter (the worker
# creates it at boot; the migrations assume it stands)
US_PREMAP_DDL = ("CREATE TABLE IF NOT EXISTS us_premap (identifier text PRIMARY KEY, event_slug text, "
                 "event_title text, market_slug text, question text, kind text, line text, side_norm text, "
                 "event_keys text[], intent text, signed text, updated_at timestamptz NOT NULL DEFAULT now())")

# ------------------------------------------------------------------ fixture
# whale 99 is RN1 (the seed migrations own the low ids). Times are
# relative to now() so every window reads the same on any day.
#   cA  SHORT book 13: he BUY OA 1000 @0.70 (adds 1000 on the short axis,
#       the short leg's price 0.70); we SELL_LONG 100 @0.30 (order 601),
#       peak 100 x (1 - 0.30) = 70.0, settled +30. OA resolves 1: his pnl
#       +300 on 700, ROI 0.4286 = ours (30 / 70).
#   cB  the FLIP: book 14 LONG (10 @0.40, filled by order 611, settled
#       +6), then book 15 SHORT (50 @0.35 contract, order 612, peak
#       50 x 0.65 = 32.5, settled -32.5). His: BUY LB 100 @0.40, SELL LB
#       100 @0.50, BUY OB 500 @0.65. LB resolves 1: pnl +60 - 50 - 325 =
#       -315 on gross 365.
#   cC  flow book 16 LONG, live: block 1000 (fill 905, 1 h before the
#       open), flow 250 (907 30 s before the open, 906 1 h after), ratio
#       0.1, target 25 held 25 (the flow's own share, 0.1 x 250: the
#       review's row held 20, five shares stale by its own measure);
#       every fill named on_target, no order.
#   cD  no book: his fills 23 h and 20 h ago; refusals 30 h and 10 h ago.
#   cF  book 18 whose long_asset is not his token (net unreadable).
#   cG  book 19 flow_base 0 with a fill 1 h before its open, on_target.
#   cH  book 20 with a rest cancelled `replace` 30 s after his fill.
#   cI  book 22 LIVE on a resolved market beside a closed, settled episode
#       25 on the same condition: the market pairs only when EVERY book
#       is closed with settled_pnl (bool_and), so it never pairs.
#   cJ  books 23 (long) / 24 (short) with a catchup on their plan.
FIXTURE = """
INSERT INTO whales (id, address, username) VALUES (99, '0xrn1-review', 'RN1');
INSERT INTO markets (condition_id, title, slug, event_title, sport, resolved_prices, resolved) VALUES
 ('cA', 'A', 'wta-a1-a2-2026-09-08', 'ev', 'tennis', '["0", "1"]'::jsonb, true),
 ('cB', 'B', 'wta-b1-b2-2026-09-08', 'ev', 'tennis', '["1", "0"]'::jsonb, true),
 ('cC', 'C', 'wta-c1-c2-2026-09-08', 'ev', 'tennis', NULL, false),
 ('cD', 'D', 'wta-d1-d2-2026-09-08', 'ev', 'tennis', NULL, false),
 ('cF', 'F', 'wta-f1-f2-2026-09-08', 'ev', 'tennis', '["1", "0"]'::jsonb, true),
 ('cG', 'G', 'wta-g1-g2-2026-09-08', 'ev', 'tennis', NULL, false),
 ('cH', 'H', 'wta-h1-h2-2026-09-08', 'ev', 'tennis', NULL, false),
 ('cI', 'I', 'wta-i1-i2-2026-09-08', 'ev', 'tennis', '["1", "0"]'::jsonb, true),
 ('cJ', 'J', 'wta-j1-j2-2026-09-08', 'ev', 'tennis', NULL, false);
INSERT INTO market_tokens (token_id, condition_id, outcome, outcome_index) VALUES
 ('LA', 'cA', 'a1', 0), ('OA', 'cA', 'a2', 1), ('LB', 'cB', 'b1', 0), ('OB', 'cB', 'b2', 1),
 ('LC', 'cC', 'c1', 0), ('OC', 'cC', 'c2', 1), ('LD', 'cD', 'd1', 0), ('OD', 'cD', 'd2', 1),
 ('LF', 'cF', 'f1', 0), ('OF', 'cF', 'f2', 1), ('LG', 'cG', 'g1', 0), ('OG', 'cG', 'g2', 1),
 ('LH', 'cH', 'h1', 0), ('OH', 'cH', 'h2', 1), ('LI', 'cI', 'i1', 0), ('OI', 'cI', 'i2', 1),
 ('LJ', 'cJ', 'j1', 0), ('OJ', 'cJ', 'j2', 1);
INSERT INTO trades (id, whale_id, tx_hash, asset, condition_id, side, size, price, notional, market_slug, sport, ts, source, detected_at, dedupe_key) VALUES
 (901, 99, '0xr1', 'OA', 'cA', 'BUY', 1000, 0.70, 700, 'wta-a1-a2-2026-09-08', 'tennis', now() - interval '4 hours' - interval '30 seconds', 'chain', now() - interval '4 hours' - interval '28 seconds', 'r1'),
 (902, 99, '0xr2', 'LB', 'cB', 'BUY', 100, 0.40, 40, 'wta-b1-b2-2026-09-08', 'tennis', now() - interval '6 hours' - interval '30 seconds', 'chain', now() - interval '6 hours' - interval '28 seconds', 'r2'),
 (903, 99, '0xr3', 'LB', 'cB', 'SELL', 100, 0.50, 50, 'wta-b1-b2-2026-09-08', 'tennis', now() - interval '5 hours', 'chain', now() - interval '5 hours' + interval '2 seconds', 'r3'),
 (904, 99, '0xr4', 'OB', 'cB', 'BUY', 500, 0.65, 325, 'wta-b1-b2-2026-09-08', 'tennis', now() - interval '4 hours' - interval '30 seconds', 'chain', now() - interval '4 hours' - interval '28 seconds', 'r4'),
 (905, 99, '0xr5', 'LC', 'cC', 'BUY', 1000, 0.50, 500, 'wta-c1-c2-2026-09-08', 'tennis', now() - interval '3 hours', 'chain', now() - interval '3 hours' + interval '2 seconds', 'r5'),
 (906, 99, '0xr6', 'LC', 'cC', 'BUY', 200, 0.52, 104, 'wta-c1-c2-2026-09-08', 'tennis', now() - interval '1 hour', 'chain', now() - interval '1 hour' + interval '2 seconds', 'r6'),
 (907, 99, '0xr7', 'LC', 'cC', 'BUY', 50, 0.51, 25.5, 'wta-c1-c2-2026-09-08', 'tennis', now() - interval '2 hours' - interval '30 seconds', 'chain', now() - interval '2 hours' - interval '28 seconds', 'r7'),
 (908, 99, '0xr8', 'LD', 'cD', 'BUY', 100, 0.50, 50, 'wta-d1-d2-2026-09-08', 'tennis', now() - interval '23 hours', 'chain', now() - interval '23 hours' + interval '2 seconds', 'r8'),
 (909, 99, '0xr9', 'LD', 'cD', 'BUY', 100, 0.50, 50, 'wta-d1-d2-2026-09-08', 'tennis', now() - interval '20 hours', 'chain', now() - interval '20 hours' + interval '2 seconds', 'r9'),
 (910, 99, '0xr10', 'LF', 'cF', 'BUY', 100, 0.50, 50, 'wta-f1-f2-2026-09-08', 'tennis', now() - interval '3 hours', 'chain', now() - interval '3 hours' + interval '2 seconds', 'r10'),
 (911, 99, '0xr11', 'LG', 'cG', 'BUY', 100, 0.50, 50, 'wta-g1-g2-2026-09-08', 'tennis', now() - interval '3 hours', 'chain', now() - interval '3 hours' + interval '2 seconds', 'r11'),
 (912, 99, '0xr12', 'LH', 'cH', 'BUY', 100, 0.50, 50, 'wta-h1-h2-2026-09-08', 'tennis', now() - interval '1 hour', 'chain', now() - interval '1 hour' + interval '2 seconds', 'r12'),
 (913, 99, '0xr13', 'LI', 'cI', 'BUY', 100, 0.50, 50, 'wta-i1-i2-2026-09-08', 'tennis', now() - interval '3 hours', 'chain', now() - interval '3 hours' + interval '2 seconds', 'r13');
INSERT INTO mirror_books (id, whale, condition_id, us_market_slug, long_asset, other_asset, intent, ratio, state, target, target_raw, his_net, ledger_net, last_plan, peak_exposure_usd, avg_cost, settled_pnl, opened_at, closed_at, last_reason, flow_base) VALUES
 (13, 'rn1', 'cA', 'aec-wta-a1-a2', 'LA', 'OA', 'ORDER_INTENT_BUY_SHORT', 0.1, 'closed', -100, -100.0, -1000, 0, '{}'::jsonb, 70.0, 0.30, 30.0, now() - interval '4 hours', now() - interval '1 hour', 'closed: standing row settled', 0),
 (14, 'rn1', 'cB', 'aec-wta-b1-b2', 'LB', 'OB', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 10, 10.0, 100, 0, '{}'::jsonb, 4.0, 0.40, 6.0, now() - interval '6 hours', now() - interval '4 hours', 'closed: sign flip', 0),
 (15, 'rn1', 'cB', 'aec-wta-b1-b2', 'LB', 'OB', 'ORDER_INTENT_BUY_SHORT', 0.1, 'closed', -50, -50.0, -500, 0, '{}'::jsonb, 32.5, 0.35, -32.5, now() - interval '4 hours', now() - interval '1 hour', 'closed: standing row settled', 0),
 (16, 'rn1', 'cC', 'aec-wta-c1-c2', 'LC', 'OC', 'ORDER_INTENT_BUY_LONG', 0.1, 'live', 25, 25.0, 1250, 25,
   ('{"his_fills_seen": [{"id": "905", "order": null, "name": "on_target", "at": ' || extract(epoch FROM now() - interval '1 hour')::text || '}, {"id": "906", "order": null, "name": "on_target", "at": ' || extract(epoch FROM now() - interval '1 hour' + interval '20 seconds')::text || '}, {"id": "907", "order": null, "name": "on_target", "at": ' || extract(epoch FROM now() - interval '1 hour')::text || '}], "flow_base": 1000.0, "flow_net": 250.0, "game_room": 2400.0, "game_exposure": 100.0}')::jsonb,
   10.0, 0.50, NULL, now() - interval '2 hours', NULL, 'on target', 1000),
 (18, 'rn1', 'cF', 'aec-wta-f1-f2', 'LF-not-his', 'OF-not-his', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 10, 10.0, 100, 0, '{}'::jsonb, 5.0, NULL, 0.0, now() - interval '3 hours', now() - interval '1 hour', 'closed: standing row settled', 0),
 (19, 'rn1', 'cG', 'aec-wta-g1-g2', 'LG', 'OG', 'ORDER_INTENT_BUY_LONG', 0.1, 'live', 10, 10.0, 100, 10,
   ('{"his_fills_seen": [{"id": "911", "order": null, "name": "on_target", "at": ' || extract(epoch FROM now() - interval '2 hours')::text || '}]}')::jsonb,
   5.0, 0.50, NULL, now() - interval '2 hours', NULL, 'on target', 0),
 (20, 'rn1', 'cH', 'aec-wta-h1-h2', 'LH', 'OH', 'ORDER_INTENT_BUY_LONG', 0.1, 'live', 10, 10.0, 100, 0, '{}'::jsonb, 0.0, NULL, NULL, now() - interval '2 hours', NULL, 'rest placed', 0),
 (22, 'rn1', 'cI', 'aec-wta-i1-i2', 'LI', 'OI', 'ORDER_INTENT_BUY_LONG', 0.1, 'live', 10, 10.0, 100, 10, '{}'::jsonb, 5.0, 0.50, NULL, now() - interval '3 hours', NULL, 'on target', 0),
 (25, 'rn1', 'cI', 'aec-wta-i1-i2', 'LI', 'OI', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 10, 10.0, 100, 0, '{}'::jsonb, 5.0, 0.50, 1.0, now() - interval '5 hours', now() - interval '4 hours', 'closed: standing row settled', 0),
 (23, 'rn1', 'cJ', 'aec-wta-j1-j2', 'LJ', 'OJ', 'ORDER_INTENT_BUY_LONG', 0.1, 'live', 10, 10.0, 100, 0, '{"catchup": {"mark": 0.41, "vwap": 0.42, "why": "within_tol", "flow_base": 0.0}}'::jsonb, 0.0, NULL, NULL, now() - interval '1 hour', NULL, 'rest placed', 0),
 (24, 'rn1', 'cJ', 'aec-wta-j1-j2-b', 'LJ', 'OJ', 'ORDER_INTENT_BUY_SHORT', 0.1, 'live', -10, -10.0, -100, 0, '{"catchup": {"mark": 0.25, "vwap": 0.30, "why": "flow_only", "flow_base": -100.0}}'::jsonb, 0.0, NULL, NULL, now() - interval '1 hour', NULL, 'rest placed', -100);
INSERT INTO mirror_orders (id, book_id, whale, us_market_slug, kind, side, tif, his_level, price, wire, qty, state, filled, avg_px, bid_at_place, ask_at_place, placed_at, reason) VALUES
 (601, 13, 'rn1', 'aec-wta-a1-a2', 'increase', 'SELL_LONG', 'GTC', 0.31, 0.31, 0.31, 100, 'filled', 100, 0.30, 0.31, 0.32, now() - interval '4 hours' + interval '20 seconds', 'increase'),
 (611, 14, 'rn1', 'aec-wta-b1-b2', 'increase', 'BUY_LONG', 'GTC', 0.40, 0.40, 0.40, 10, 'filled', 10, 0.40, 0.39, 0.40, now() - interval '6 hours' + interval '20 seconds', 'increase'),
 (612, 15, 'rn1', 'aec-wta-b1-b2', 'increase', 'SELL_LONG', 'GTC', 0.35, 0.35, 0.35, 50, 'filled', 50, 0.35, 0.35, 0.36, now() - interval '4 hours' + interval '20 seconds', 'increase'),
 (621, 14, 'rn1', 'aec-wta-b1-b2', 'increase', 'BUY_LONG', 'GTC', 0.29, 0.29, 0.29, 5, 'expired', 0, NULL, 0.28, 0.29, now() - interval '5 hours' + interval '20 seconds', 'increase'),
 (622, 13, 'rn1', 'aec-wta-a1-a2', 'increase', 'SELL_LONG', 'GTC', 0.55, 0.55, 0.55, 5, 'expired', 0, NULL, 0.55, 0.56, now() - interval '3 hours' + interval '20 seconds', 'increase'),
 (631, 18, 'rn1', 'aec-wta-f1-f2', 'increase', 'BUY_LONG', 'GTC', 0.50, 0.50, 0.50, 10, 'expired', 0, NULL, 0.50, 0.50, now() - interval '3 hours' + interval '20 seconds', 'increase'),
 (801, 20, 'rn1', 'aec-wta-h1-h2', 'increase', 'BUY_LONG', 'GTC', 0.50, 0.50, 0.50, 10, 'cancelled', 0, NULL, 0.50, 0.50, now() - interval '1 hour' + interval '30 seconds', 'replace');
INSERT INTO mirror_candidate_refusals (at, whale, condition_id, us_slug, refusal) VALUES
 (now() - interval '30 hours', 'rn1', 'cD', 'aec-wta-d1-d2', 'no_mark'),
 (now() - interval '10 hours', 'rn1', 'cD', 'aec-wta-d1-d2', 'no_mark');
"""

PRESETS = ("paired-ratio", "fills-missed", "on-target-why", "cand-refusals", "flow-books")


def _preset(name: str) -> tuple[str, int]:
    text = YML.read_text()
    m = re.search(r'^ {16}' + re.escape(name) + r'\) SQL="(.*?)"; TO=(\d+)', text, re.M)
    assert m, name
    return m.group(1), int(m.group(2))


def _stmts(name: str) -> list[str]:
    return [s.strip() for s in _preset(name)[0].split(";") if s.strip()]


class _World:
    """One scratch database for the module: the migrations in order (as
    migrate.py applies them), the fixture, a loop the connection lives on."""

    def __init__(self):
        asyncpg = pytest.importorskip("asyncpg")
        self.loop = asyncio.new_event_loop()
        try:
            self.admin = self.loop.run_until_complete(asyncpg.connect(DSN_BASE, timeout=4))
        except Exception:  # noqa: BLE001 -- no local PG: skip, never fake
            self.loop.close()
            pytest.skip("no local postgres for the lane M review pins")
        self.name = "pnl_m_review_" + uuid.uuid4().hex[:10]
        self.loop.run_until_complete(self.admin.execute(f'CREATE DATABASE "{self.name}"'))
        self.conn = self.loop.run_until_complete(
            asyncpg.connect(DSN_BASE.rsplit("/", 1)[0] + "/" + self.name, timeout=4))
        self.loop.run_until_complete(self._up())

    async def _up(self):
        await self.conn.execute(US_PREMAP_DDL)
        for path in sorted(MIG_DIR.glob("*.sql")):
            async with self.conn.transaction():
                await self.conn.execute(path.read_text())
        await self.conn.execute(FIXTURE)

    def rows(self, sql: str) -> list[dict]:
        return [dict(r) for r in self.loop.run_until_complete(self.conn.fetch(sql))]

    def run(self, sql: str) -> None:
        self.loop.run_until_complete(self.conn.execute(sql))

    def close(self):
        self.loop.run_until_complete(self.conn.close())
        self.loop.run_until_complete(self.admin.execute(f'DROP DATABASE "{self.name}"'))
        self.loop.run_until_complete(self.admin.close())
        self.loop.close()


@pytest.fixture(scope="module")
def world():
    w = _World()
    try:
        yield w
    finally:
        w.close()


def _f(v):
    return None if v is None else float(v)


def _by(rows, key, val):
    out = [r for r in rows if r.get(key) == val]
    assert len(out) == 1, (key, val, rows)
    return out[0]


# ------------------------------------------------------- the real schema

def test_every_lane_m_statement_runs_on_the_real_schema_and_the_hourly_is_one_string(world):
    """psql -c sends the preset as ONE multi-statement string; each
    statement is also run alone so a failing one is named."""
    for name in PRESETS + ("hourly",):
        sql, to = _preset(name)
        assert to >= 30000
        for s in _stmts(name):
            world.rows(s)
        world.run(sql)
    hourly = _preset("hourly")[0]
    assert hourly.count("AS section;") == 8 and "'== paired-ratio'" in hourly and "'== fills-missed'" in hourly \
        and "'== on-target-why'" in hourly
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER"):
        for name in PRESETS + ("hourly",):
            assert bad not in _preset(name)[0], (name, bad)


def test_the_help_line_and_the_case_block_keep_the_lane_m_presets_beside_their_siblings():
    """paired-ratio after paired-day and before latency-census; fills-missed
    and on-target-why after close-rows and before verify-day -- the
    adjacencies the E9 q8 and E5 register pins regenerate from the case
    block, pinned here as the words so a case moved with its help line
    regenerated still fails."""
    text = YML.read_text()
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    assert "|paired-day|paired-ratio|latency-census|fills-answered|close-rows|fills-missed|on-target-why|verify-day|" in line
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", text[text.index('case "$ARG" in'):text.index(line)], re.M)
    i = labels.index("paired-ratio")
    assert labels[i - 1] == "paired-day" and labels[i + 1] == "latency-census"
    j = labels.index("fills-missed")
    assert labels[j - 1] == "close-rows" and labels[j + 1] == "on-target-why" and labels[j + 2] == "verify-day"
    assert labels[-1] == "hourly"


# --------------------------------------------------------- paired-ratio

def test_paired_ratio_short_book_net_cost_and_ratio_then_folded_share_frac_on_the_legs_price(world):
    """cA: his 1000 on the short axis at the short leg's 0.70 -> peak
    1000.0, vwap 0.7000, his_cost_net 700.00; his pnl +300 / 700 = 0.429,
    ours +30 / 70 = 0.429, roi_ratio 1.00, stake_frac_net 10.0 (70 / 700).
    DEFECT (HIGH-1) as it stood: our_peak_sh = staked / avg = 70 / 0.30 =
    233.3 and share_frac_pct 23.3 -- the short leg is 100 shares
    (rules.book_buy: peak = leg x (1 - avg) on a short), 10.0 %. Folded
    2026-09-08: the BUY_SHORT book's stake is divided by (1 - avg) per
    book and this pin reads 100.0 / 10.0; his_roi_net (MEDIUM-3, beside
    his_roi) = 300 / 700 = 0.429 and roi_ratio_net 1.00."""
    r = _by(world.rows(_stmts("paired-ratio")[0]), "book", 13)
    assert _f(r["his_net_peak"]) == 1000.0 and _f(r["his_vwap_adds"]) == 0.7 and _f(r["his_cost_net"]) == 700.0
    assert _f(r["his_pnl"]) == 300.0 and _f(r["his_roi"]) == 0.429 and _f(r["our_roi"]) == 0.429
    assert _f(r["roi_ratio"]) == 1.0 and _f(r["pnl_ratio"]) == 0.1 and _f(r["stake_frac_net_pct"]) == 10.0
    assert r["sign_agree"] == "same"
    assert _f(r["our_peak_sh"]) == 100.0 and _f(r["share_frac_pct"]) == 10.0, "folded HIGH-1 (was 233.3 / 23.3)"
    assert _f(r["his_roi_net"]) == 0.429 and _f(r["roi_ratio_net"]) == 1.0


def test_paired_ratio_folded_flipped_market_cost_net_is_the_peak_side_times_its_own_vwap(world):
    """cB: on the first book's (long) axis his running net is +100, 0,
    -500: his_net_peak 500.0 (the SHORT side). DEFECT (HIGH-2) as it
    stood: his_vwap_adds 0.4000 (the long side's only add) and
    his_cost_net 200.00 -- 500 short shares priced at a long add; at the
    peak he held 500 of the other token at 0.65 = 325.00. The gross
    beside it (365.00), his pnl (-315.00) and our figures (36.50 staked,
    -26.50 settled) are right. Folded 2026-09-08: the vwap is the adds on
    the side the peak was reached on (904's 500 at 0.65; 903, the SELL
    that flattened the long, adds nothing to the short side) and this pin
    reads 0.6500 / 325.00; our peak shares are the larger book's (50 short
    at 32.5 / 0.65) -> share_frac 10.0; his_roi_net -315 / 325 = -0.969."""
    r = _by(world.rows(_stmts("paired-ratio")[0]), "book", 14)
    assert _f(r["his_net_peak"]) == 500.0 and _f(r["his_vwap_adds"]) == 0.65
    assert _f(r["his_cost"]) == 365.0 and _f(r["his_pnl"]) == -315.0 and _f(r["his_roi"]) == -0.863
    assert _f(r["our_staked"]) == 36.5 and _f(r["our_settled"]) == -26.5
    assert _f(r["his_cost_net"]) == 325.0, "folded HIGH-2 (was 200.00)"
    assert _f(r["our_peak_sh"]) == 50.0 and _f(r["share_frac_pct"]) == 10.0 and _f(r["his_roi_net"]) == -0.969


def test_paired_ratio_an_unreadable_axis_prints_null_fractions_and_a_live_book_never_pairs(world):
    """cF: the book's tokens are not his -> net unreadable -> his_net_peak,
    his_cost_net, share_frac, stake_frac_net all NULL (never 0); cI: a
    LIVE book on a resolved market beside a closed, settled episode is
    not paired (every book of the market must be closed with settled_pnl:
    a frozen or live book pairs only once it is settled); the totals
    count 3 markets and 2 share fractions."""
    rows = world.rows(_stmts("paired-ratio")[0])
    r = _by(rows, "book", 18)
    assert r["his_net_peak"] is None and r["his_cost_net"] is None
    assert r["share_frac_pct"] is None and r["stake_frac_net_pct"] is None and r["our_peak_sh"] is None
    assert r["sign_agree"] == "OPPOSITE"           # his +50 against our 0.00: named, not hidden
    assert [x["book"] for x in rows if x["book"] in (22, 25)] == []
    t = world.rows(_stmts("paired-ratio")[1])[0]
    assert t["markets"] == 3 and t["share_frac_n"] == 2 and t["same_sign"] == 2 and t["opposite_sign"] == 1
    assert t["opposite"] == "18(closed: standing row settled)"


def test_paired_ratio_totals_line_folded_median_share_reads_the_true_fractions(world):
    """The one hourly line, on the fixture: his 35 / 1115 = 0.0314 = ours
    3.50 / 111.50, roi_ratio 1.00x. share_frac_med 21.9 % was the median of
    the two DEFECT fractions (23.3, 20.4); folded 2026-09-08 it reads
    10.0 % (10.0, 10.0) and stake_frac_net_med 10.6 % (10.0, 36.5 / 325 =
    11.2); his_cost_net 700 + 325 = 1025.00, his_roi_net 35 / 1025 =
    0.0341, roi_ratio_net 0.0314 / 0.0341 = 0.92 beside the gross ones."""
    t = world.rows(_stmts("paired-ratio")[1])[0]
    assert t["line"] == ("PAIRED 3 mkts his_roi 0.0314 our_roi 0.0314 roi_ratio 1.00x same 2/3 opposite "
                         "18(closed: standing row settled) share_frac_med 10.0% stake_frac_net_med 10.6% "
                         "cents_over -0.5c lat_med 50s"), t["line"]
    assert _f(t["his_cost_net"]) == 1025.0 and _f(t["his_cost"]) == 1115.0
    assert _f(t["his_roi_net"]) == 0.0341 and _f(t["roi_ratio_net"]) == 0.92 and _f(t["roi_ratio"]) == 1.0


# --------------------------------------------------------- fills-missed

def test_fills_missed_classes_on_the_fixture_and_folded_the_axis_is_the_book_whose_window_holds_the_fill(world):
    """filled 3 (901 by 601, 902 by 611, 904 by 612: 1065.00),
    refused:on_target 4 (book 16's three, book 19's one), missed_replace 1
    (912 by the rest 801 cancelled `replace`), unseen 1 (913: no plan
    holds it, no order inside 120 s). DEFECT (MEDIUM-2) as it stood: 902
    (his BUY LB 100 @0.40, answered by order 611 which FILLED) was absent
    -- the axis was the LATEST book's (15, short), so the long add read as
    reducing -- and 903 (SELL LB, the flatten) read adding on that axis
    and joined 621 within 120 s -> missed_open. Folded 2026-09-08: the
    axis is the book whose window (opened_at - 120 s, closed_at) holds the
    fill -- 902 is 30 s before book 14 opened, so the window is led by the
    120 s a fill is answered within -- the later book when two hold it
    (904: books 14 and 15 both, 15 wins), else the latest: 902 reads
    filled on book 14, 903 reads reducing and is dropped (no missed_open);
    ALL stays 9 fills, 1854.50 - 50 (903) + 40 (902) = 1844.50."""
    by = {r["class"]: r for r in world.rows(_stmts("fills-missed")[0])}
    assert by["ALL"]["n"] == 9 and _f(by["ALL"]["his_usd"]) == 1844.5
    assert by["filled"]["n"] == 3 and _f(by["filled"]["his_usd"]) == 1065.0 and by["filled"]["n_resolved"] == 3
    assert by["refused:on_target"]["n"] == 4 and _f(by["refused:on_target"]["his_usd"]) == 679.5
    assert by["missed_replace"]["n"] == 1 and "missed_open" not in by
    assert by["unseen"]["n"] == 1 and _f(by["unseen"]["his_usd"]) == 50.0 and by["unseen"]["lag_med_s"] is None
    assert "missed_expired_ioc" not in by
    per = world.rows(_stmts("fills-missed")[1])
    b14 = [r for r in per if r["book"] == 14]
    assert [r["class"] for r in b14] == ["filled"] and _f(b14[0]["his_usd"]) == 40.0, "folded MEDIUM-2 (was missed_open)"
    assert _by(per, "book", 15)["class"] == "filled" and _f(_by(per, "book", 15)["his_usd"]) == 325.0
    assert _by(per, "book", 13)["class"] == "filled" and _f(_by(per, "book", 13)["roi"]) == 0.4286
    assert _by(per, "book", 20)["class"] == "missed_replace"


def test_fills_missed_folded_band_c_rounds_his_cent_in_numeric_before_the_floor_and_ceil(world):
    """Seven entry rows, every one placed AT his cent (ask = his level on
    a BUY, bid = his level on a SELL): at_or_through_pct must read 100.0.
    DEFECT (HIGH-3) as it stood: floor(0.29 * 100) = 28 and ceil(0.55 *
    100) = 56 in float8, so orders 621 and 622 read band 1.0 and the share
    at or through printed 71.4. inside_1c still 100.0, the median 0.0.
    Folded 2026-09-08: his level x 100 is rounded to six places in numeric
    before the floor / ceil and this pin reads 100.0 (the float8
    arithmetic beneath it is pinned as the server's own)."""
    all_ = _by(world.rows(_stmts("fills-missed")[2]), "kind", "ALL")
    assert all_["orders"] == 7 and all_["with_band"] == 7 and all_["filled"] == 3
    assert _f(all_["inside_1c_pct"]) == 100.0 and _f(all_["band_med_c"]) == 0.0
    assert _f(all_["at_or_through_pct"]) == 100.0, "folded HIGH-3 (was 71.4)"
    # the arithmetic behind it, on the same server
    r = world.rows("SELECT floor(0.29::float8 * 100) AS f, ceil(0.55::float8 * 100) AS c")[0]
    assert (_f(r["f"]), _f(r["c"])) == (28.0, 56.0)


# -------------------------------------------------------- on-target-why

def test_on_target_why_block_and_unattributed_then_folded_stale_is_judged_on_the_flow_and_the_block_is_named(world):
    """905 (1 h before the open, block 1000): `block`, 500.00. 911 (1 h
    before book 19's open, flow_base 0): `unattributed` -- never block.
    DEFECT (HIGH-4) as it stood: 906 (1 h AFTER the open) and 907 (30 s
    before it, inside FIRST_SIGHT_S) read `stale_snapshot`, 129.50:
    target_raw = ratio x (his_net 1250 - block 1000), and the cause
    compared it with ratio x 1250 - 0.5 = 124.5. Folded 2026-09-08: the
    cause compares |target_raw| with ratio x |the plan's flow_net| (250 ->
    25; the row holds 25 of a target 25, the flow's own share -- the
    review's row held 20, five shares stale by that very measure, so the
    fixture reads the consistent target), not stale; a flow-only book
    whose target sits below ratio x |his_net| by the block alone is named
    `block_flow`, and both fills read it: the only stale figure there was
    the block."""
    by = {r["cause"]: r for r in world.rows(_stmts("on-target-why")[0])}
    assert by["ALL"]["fills"] == 4 and _f(by["ALL"]["his_usd"]) == 679.5 and by["ALL"]["markets"] == 2
    assert by["block"]["fills"] == 1 and _f(by["block"]["his_usd"]) == 500.0 and _f(by["block"]["lag_med_s"]) == 7200.0
    assert by["unattributed"]["fills"] == 1 and _f(by["unattributed"]["his_usd"]) == 50.0
    assert by["block_flow"]["fills"] == 2 and _f(by["block_flow"]["his_usd"]) == 129.5, \
        "folded HIGH-4 (was stale_snapshot 129.50)"
    assert "stale_snapshot" not in by
    top = {r["id"]: r for r in world.rows(_stmts("on-target-why")[1])}
    assert top[905]["cause"] == "block" and top[911]["cause"] == "unattributed"
    assert top[906]["cause"] == "block_flow" and top[907]["cause"] == "block_flow"
    assert _f(top[906]["lag_s"]) == 20.0 and top[906]["game_cap"] is None and _f(top[906]["flow_base"]) == 1000.0
    assert top[906]["flow_net"] == "250.0" and top[911]["flow_net"] is None


# -------------------------------------------------------- cand-refusals

def test_cand_refusals_folded_the_first_verdict_is_the_first_ever_not_the_first_inside_the_window(world):
    """cD: his fills 23 h and 20 h ago; verdicts 30 h and 10 h ago. The
    first verdict preceded his first fill, so first_verdict_after_his_last
    is false and the market is `verdict_while_he_traded`. DEFECT
    (MEDIUM-1) as it stood: `r` is bounded to 24 h, the first verdict it
    saw was the 10 h one, and the row printed true /
    `first_verdict_after_his_last` with a 36000 s gap. Folded 2026-09-08:
    min(at) unbounded (the index whale, condition_id, at serves it): the
    row reads false, the day's `rows` and `path` stay the window's, the
    first verdict prints with its date, and the subtotal's gap is the
    30 h verdict against his 20 h last fill: -36000 s."""
    r = _by(world.rows(_stmts("cand-refusals")[2]), "his_slug", "wta-d1-d2-2026-09-08")
    assert r["rows"] == 1 and r["path"] is not None and r["path"].startswith("no_mark@")
    assert r["first_verdict_after_his_last"] is False, "folded MEDIUM-1 (was true)"
    assert isinstance(r["first_verdict"], str) and len(r["first_verdict"]) == 16 and r["first_verdict"][4] == "-"
    d = world.rows(_stmts("cand-refusals")[3])
    assert [x["dark_window"] for x in d] == ["verdict_while_he_traded"] and d[0]["markets_no_book"] == 1
    assert _f(d[0]["first_after_last_med_s"]) == -36000.0


# ----------------------------------------------------------- flow-books

def test_flow_books_catchup_side_arithmetic_and_folded_the_verdict_rides_every_plan(world):
    """The side off a plan that carries the verdict: long 23 mark 0.41 <=
    vwap 0.42 -> better; short 24 mark 0.25 < vwap 0.30 -> worse (a short
    is better at or ABOVE his vwap); the block's dollars on the SHORT leg:
    100 x (1 - 0.30) = 70 USD (his vwap is the long token's contract
    price, vwap_of's axis), ours x 0.1 = 7. A plan without it (16, 19) ->
    unread, never a guess.
    DEFECT (HIGH-5) as it stood: the worker wrote `catchup` on the book's
    FIRST plan only -- _write_plan popped the open's `_catchup`, every
    later plan was built fresh at _tick_book, the quiet skip carried
    _SKIP_CARRIED alone -- so on production every book past its first
    tick read unread and the third statement summed nothing. Folded
    2026-09-08: _write_plan carries the prior plan's catchup the way
    _fills_seen carries his_fills_seen -- and NOT the quiet skip:
    _SKIP_CARRIED is E6's pinned contract (test_e6_tick_budget), its
    UPDATE replaces the plan, so a book quiet-skipped before its next
    read loses the verdict and reads `unread` from there (said in the
    preset's comment); the source clauses read the fold, the runtime
    carry is pinned below."""
    rows = {r["id"]: r for r in world.rows(_stmts("flow-books")[0])}
    assert rows[23]["catchup_side"] == "better" and rows[24]["catchup_side"] == "worse"
    assert rows[16]["catchup_side"] == "unread" and rows[19]["catchup_side"] == "unread"
    sides = {(r["catchup_side"], r["why"]): r for r in world.rows(_stmts("flow-books")[2])}
    w = sides[("worse", "flow_only")]
    assert w["n"] == 1 and _f(w["block_shares"]) == 100.0 and _f(w["block_usd_at_his_vwap"]) == 70.0
    assert _f(w["our_share_usd_uncapped"]) == 7.0 and _f(w["mark_minus_vwap_avg_c"]) == -5.0
    assert ("unread", "none") in sides and ("better", "within_tol") not in sides   # 23 has no block: not in stmt 3
    src = inspect.getsource(ml)
    assert src.count('plan["catchup"] = cu') == 1 and 'cu = book.pop("_catchup", None)' in src
    assert "catchup" not in ml._SKIP_CARRIED
    assert 'plan: dict[str, Any] = {"bid": r.bid, "ask": r.ask, "mark": r.mark, "venue": r.venue' in src
    assert 'prior.get("catchup")' in src, "folded HIGH-5: _write_plan carries the open's verdict forward"


def test_the_worker_carries_the_opens_catchup_onto_every_later_plan_and_writes_none_without_one():
    """Folded 2026-09-08 (HIGH-5), at runtime: _write_plan writes the
    open's own verdict when the book carries `_catchup` (popped, so it is
    written as the open's once), else the PRIOR plan's `catchup` verbatim,
    and nothing when neither holds one (never a guess); the quiet skip's
    plan does not carry it (_SKIP_CARRIED, E6's contract, unchanged).
    Nothing in the worker reads it back: the flow-books preset does."""
    class _Pool:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, *a):
            self.calls.append((sql, a))

    pool = _Pool()
    t = ml._Tick(pool=pool, pmus=None, http=None, now=1_700_000_000.0, stats=ml._new_stats())
    cu = {"mark": 0.41, "vwap": 0.42, "why": "within_tol", "flow_base": 0.0}
    book = {"id": 23, "last_plan": {"catchup": cu, "his_fills_seen": [], "kind": "hold"}}
    asyncio.run(ml._write_plan(t, book, None, 10, 10.0, None, 0.5, "on target", {"kind": "hold"}))
    assert "ml-book-plan" in pool.calls[-1][0] and json.loads(pool.calls[-1][1][-1])["catchup"] == cu
    own = {"mark": 0.5, "vwap": 0.5, "why": "small_bet", "flow_base": None}
    book2 = {"id": 24, "last_plan": {"catchup": cu}, "_catchup": own}
    asyncio.run(ml._write_plan(t, book2, None, 10, 10.0, None, 0.5, "on target", {"kind": "hold"}))
    assert json.loads(pool.calls[-1][1][-1])["catchup"] == own and "_catchup" not in book2
    book3 = {"id": 25, "last_plan": {"kind": "no_plan"}}
    asyncio.run(ml._write_plan(t, book3, None, 10, 10.0, None, 0.5, "on target", {"kind": "hold"}))
    assert "catchup" not in json.loads(pool.calls[-1][1][-1])
    book4 = {"id": 26, "last_plan": "not a plan"}
    asyncio.run(ml._write_plan(t, book4, None, 10, 10.0, None, 0.5, "on target", {"kind": "hold"}))
    assert "catchup" not in json.loads(pool.calls[-1][1][-1])
    assert "catchup" not in ml._SKIP_CARRIED and "his_fills_seen" not in ml._SKIP_CARRIED

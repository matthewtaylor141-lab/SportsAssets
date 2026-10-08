"""THE MARKET-PLANE MEMORY HARNESS: the dedicated plane's REAL run loop
(workers/universal_market_plane.run) and its REAL stream transport, against a
local Postgres seeded with SYNTHETIC rows at production cardinality, with
every venue transport faked in process. RSS and VmHWM are read per step.

WHY (RC5, 2026-10-08). sportsassets-market-plane (srv-db3idqvavr4c739udecg,
Render standard, 2 GiB, MALLOC_ARENA_MAX=2, UMP_SUBSCRIBE_ALL=on, guard mode
UMP_AND_KALSHI_WS) was OOM-killed at 2 GiB from 06:10:01Z (06:12:10Z,
06:47:20Z and ~11 more by 12:50Z) after RC4 deployed at 05:16:49Z. Render's
60 s memory: boot 736 -> 890 -> 1400 MB inside three minutes, a 1.4-1.9 GB
plateau, spikes to 1998 MB; 1613 MB within a minute of the 06:12 restart.
A bigger instance is not the repair and a restart is not a repair: this tool
names the operation, the queue or the retained object for each megabyte.

WHAT IS SYNTHETIC. Every row the `seed` command writes is generated here and
carries `syn` in its slug / ticker; nothing is copied from production. Only
the CARDINALITIES and WIDTHS are production's (pm-acceptance run 37738089957
readback, market_plane.json): POLYMARKET_US registry 113,356-113,431 rows
(71,281 active, 32,948 PMX-listed with refdata, 38,333 refdata pending, 133
evaluated candidates), 75,169 Kalshi rows, market_plane_rules 168,437 rows
(~1.7 KB each), registry rows ~2.4 KB (max 5.5 KB); the subscribe-all stream
carries the venue's ~74k instruments (refdata_universe: "a full ~74k pull").

WHAT IS REAL. `W.run()` itself, unchanged, under its own module constants
(the harness only shortens the step intervals so every pass runs every step,
and holds each pass at its heartbeat until the harness releases it); every
step function it calls; the subscribe-all `GrpcBidiTransport` (protobuf
decode, local filter, ResidentBooks) against an in-process gRPC server; the
REAL `kalshi_catalogue.walk` against a fake GET transport returning
production-shaped pages; `kalshi_ws_market_data.run` against a fake socket.

WHAT IS FAKE (no venue, no credential, no network beyond 127.0.0.1): the PMX
REST client (token + `read_instruments`), the gRPC channel target, the
Kalshi GET transport, the Kalshi socket and the Kalshi limits read.

USAGE (from backend/, MALLOC_ARENA_MAX=2 in the environment as production):

    DATABASE_URL=postgresql://... python -m tools.market_plane_memory seed
    DATABASE_URL=postgresql://... MALLOC_ARENA_MAX=2 \\
        python -m tools.market_plane_memory run --passes 5 --json out.json
    ... run --tracemalloc       (attribution; tracemalloc inflates RSS)

The run prints one row per step: RSS before / after, the step's own VmHWM
peak (/proc/self/clear_refs reset at each step boundary) and, with
--tracemalloc, the traced peak and the top retained allocation sites.
--scale seeds / runs a proportional universe (tracemalloc runs are slow at
full size); --full-every and --kalshi-every-s repeat the full repopulate
and the Kalshi walk for a steady-state run.

MEASURED HERE (full scale, five passes, MALLOC_ARENA_MAX=2; RC4 9b94ef5c ->
the RC5 repair), each step's own VmHWM rise and what it held:

  coverage_pass           +1,530.7 -> +61.0 MB  every active row, rules row
                          + parsed evidence, and a result per contract
  populate (full)           +444.1 -> +3.3 MB   catalogue, registry sha map,
                                                every upsert tuple
  Kalshi walk (thread)      +232.4 -> +127.5 MB 75,169 venue objects ->
                                                persisted fields only
  Kalshi persist            +318.3 -> +25.0 MB  every tuple + rules row
  assignment read + parse   +52.0 / +164.9 -> +58.8 MB (incl. the books'
                          own entries): full refdata records parsed, alive
                          between passes -> four fields, call-local
  heartbeat detail       1,512,797 -> 2,220 characters (every pass)
  SNAPSHOT event         1,546,980 -> 35,389 characters (every minute)
  highest step peak       1,956.5 -> 448.9 MB; RSS after five passes
                          1,669.3 -> 431.7 MB

Steady state after the repair (ten passes, a full repopulate every four, the
Kalshi walk every 150 s -- eight walks, 2.5 M stream updates): RSS 471-477 MB
from pass 2 to pass 10, no growth; highest step peak 492.1 MB.

tracemalloc at --scale 0.25 (traced peak rise per step, RC4 -> repair):
coverage_pass 344.2 -> 51.2 MB, populate(full) 110.2 -> 5.5, Kalshi walk
104.6 -> 38.7 (RC4: json decoder strings 60.2 + kalshi_catalogue.py:199
whole-object copies 28.4), Kalshi persist 95.5 -> 18.5, certify 52.4 ->
3.5 (full refdata parsed), assignment read + parse + sync 71.9 -> 18.3; the
books (institutional_stream.py:480/481 level lists, 800/801 decoded ints,
766/768 market entries) 32.1 -> 27.2, unchanged by design.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import functools
import json
import os
import random
import sys
import threading
import time
from concurrent import futures

UTC = _dt.timezone.utc

#: production cardinalities (pm-acceptance run 37738089957, market_plane.json)
PROD = {"pmus_markets": 113_431, "pmus_active": 71_281,
        "pmx_listed": 32_948, "kalshi_markets": 75_169,
        "rules_total": 168_437, "candidates": 133,
        "stream_instruments": 74_000, "premap_churn_per_10s": 6_551,
        "kalshi_series": 300, "kalshi_ws_tickers": 2_000}

LEAGUES = (("nfl", "football"), ("nba", "basketball"), ("mlb", "baseball"),
           ("nhl", "hockey"), ("epl", "soccer"), ("cfb", "football"),
           ("atp", "tennis"), ("mls", "soccer"), ("cbb", "basketball"),
           ("ucl", "soccer"), ("wnba", "basketball"), ("ufc", "mma"))
TYPES = ("{s}_team_full_game_moneyline", "{s}_team_full_game_spread",
         "{s}_team_full_game_total_points", "{s}_team_first_half_spread",
         "{s}_player_passing_yards_full_game_total", "futures",
         "{s}_team_full_game_winner", "{s}_game_total_corners")


# ── /proc readers ────────────────────────────────────────────────────

def _status(key: str) -> float | None:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith(key):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except (OSError, ValueError, IndexError):
        return None
    return None


def rss_mb() -> float | None:
    return _status("VmRSS:")


def hwm_mb() -> float | None:
    return _status("VmHWM:")


def reset_hwm() -> bool:
    """VmHWM := current RSS (Linux >= 4.0, own process). A step's peak is
    then the high-water reached inside that step alone."""
    try:
        with open("/proc/self/clear_refs", "w") as f:
            f.write("5")
        return True
    except OSError:
        return False


# ── SYNTHETIC generators (deterministic; every id carries `syn`) ─────

def _lg(i):
    return LEAGUES[i % len(LEAGUES)]


def pmus_slug(i: int) -> str:
    lg, _sp = _lg(i // 40)
    return "aec-%s-syn%05da-syn%05db-2026-10-%02d-m%d" % (
        lg, i // 40, i // 40, 1 + (i // 40) % 28, i % 40)


def pmus_event(i: int) -> str:
    lg, _sp = _lg(i // 40)
    return "aec-%s-syn%05da-syn%05db-2026-10-%02d" % (
        lg, i // 40, i // 40, 1 + (i // 40) % 28)


def pmus_type(i: int) -> str:
    _lg_, sp = _lg(i // 40)
    return TYPES[i % len(TYPES)].format(s=sp)


def instrument(slug: str, i: int) -> dict:
    """A PMX ListInstruments record the shape and width of production's
    (tests/test_contract_family.py SPREAD, ~2.3 KB of JSON)."""
    lg, sp = _lg(i // 40)
    a, b = "Synthetic Alpha %d" % (i // 40), "Synthetic Beta %d" % (i // 40)
    return {
        "symbol": slug, "productId": slug.rsplit("-", 1)[0],
        "priceScale": "1000" if lg in ("mlb", "nfl") else "100",
        "fractionalQtyScale": "100", "state": "INSTRUMENT_STATE_OPEN",
        "eventAttributes": {
            "eventId": slug.rsplit("-", 1)[0],
            "question": "Will %s beat %s in market %d (%s)?" % (a, b, i, lg),
            "strikeUnit": "decimal", "payoutValue": "100",
            "strikeValue": "%d.5" % (i % 17),
            "eventOutcome": "EVENT_OUTCOME_DIRECTIONAL",
            "evaluationType": ">",
            "eventDisplayName": "Full Game: %s vs. %s" % (a, b),
            "calculationMethod": "CALCULATION_METHOD_VALUE"},
        "metadata": {
            "event_id": pmus_event(i), "prop_type": "team",
            "product_id": slug.rsplit("-", 1)[0],
            "event_product_id": slug.rsplit("-", 1)[0],
            "market_title": "%s +%d.5 (full game)" % (a, i % 17),
            "outcome_type": "spreads", "event_category": "SPR",
            "outcome_strike": "%d.5" % (i % 17),
            "market_sport_type": pmus_type(i),
            "cftc_instrument_id": slug, "instrument_product": "aec",
            "long_participant_id": "%s-syna%d" % (lg, i // 40),
            "short_participant_id": "%s-synb%d" % (lg, i // 40),
            "long_participant_name": a, "short_participant_name": b,
            "instrument_rules":
                "This market will settle to Yes if %s, after applying a "
                "+%d.5 point spread, outscores %s in the %s game scheduled "
                "for Oct %d, 2026 (synthetic %d)." % (
                    a, i % 17, b, sp, 1 + (i // 40) % 28, i),
            "instrument_rules_display":
                "This market will settle to Yes if %s outscores %s by more "
                "than %d.5 points in the %s game scheduled for Oct %d, 2026 "
                "(synthetic %d)." % (b, a, i % 17, sp, 1 + (i // 40) % 28,
                                     i)}}


def pmus_market_object(i: int) -> dict:
    """The event board's inline market (the rules capture's input)."""
    lg, sp = _lg(i // 40)
    a, b = "Synthetic Alpha %d" % (i // 40), "Synthetic Beta %d" % (i // 40)
    return {"slug": pmus_slug(i), "sportsMarketType": pmus_type(i),
            "description": (
                "This market will settle to the winner of the %s vs %s %s "
                "game scheduled for Oct %d, 2026. Overtime and extra innings "
                "count if played. If the game is postponed and not played "
                "within 48 hours of the originally scheduled start, or is "
                "cancelled, the market will settle to the last fair market "
                "price. If the game ends in a tie the market resolves 50-50. "
                "Outcome sourced from the league's official box score "
                "(synthetic market %d of the %s board)." % (
                    a, b, sp, 1 + (i // 40) % 28, i, lg))}


def kalshi_market(j: int, series: str) -> dict:
    """One Kalshi GET /markets object, production field set and width
    (~2.6 KB of JSON incl. rules_primary / rules_secondary)."""
    ev = "%s-26OCT%02dSYN%05d" % (series, 1 + j % 28, j // 3)
    tm = "SYN%d" % (j % 3)
    iso = "2026-10-%02dT23:00:00Z" % (1 + j % 28)
    return {
        "ticker": "%s-%s" % (ev, tm), "event_ticker": ev,
        "market_type": "binary",
        "title": "Synthetic Alpha %d vs Synthetic Beta %d Winner?" % (j, j),
        "subtitle": "", "yes_sub_title": "Synthetic team %s" % tm,
        "no_sub_title": "Synthetic team %s" % tm,
        "open_time": "2026-10-01T14:00:00Z", "close_time": iso,
        "expected_expiration_time": iso, "expiration_time": iso,
        "latest_expiration_time": iso, "settlement_timer_seconds": 300,
        "status": "active", "response_price_units": "usd_cent",
        "yes_bid": 45, "yes_bid_dollars": "0.4500", "yes_ask": 47,
        "yes_ask_dollars": "0.4700", "no_bid": 53, "no_bid_dollars": "0.5300",
        "no_ask": 55, "no_ask_dollars": "0.5500", "last_price": 46,
        "last_price_dollars": "0.4600", "previous_yes_bid": 44,
        "previous_yes_bid_dollars": "0.4400", "previous_yes_ask": 48,
        "previous_yes_ask_dollars": "0.4800", "previous_price": 45,
        "previous_price_dollars": "0.4500", "volume": 12345 + j,
        "volume_fp": "%d.00" % (12345 + j), "volume_24h": 2345,
        "volume_24h_fp": "2345.00", "liquidity": 123456,
        "liquidity_dollars": "1234.5600", "open_interest": 3456,
        "open_interest_fp": "3456.00", "result": "",
        "can_close_early": True, "expiration_value": "", "category": "",
        "risk_limit_cents": 0, "notional_value": 100,
        "notional_value_dollars": "1.0000", "tick_size": 1,
        "yes_bid_size_fp": "100.00", "yes_ask_size_fp": "200.00",
        "updated_time": "2026-10-08T05:00:00Z", "occurrence_datetime": iso,
        "strike_type": "structured",
        "custom_strike": {"Team": "Synthetic team %s" % tm},
        "price_level_structure": "linear_cent",
        "price_ranges": [{"start": "0.0000", "end": "1.0000",
                          "step": "0.0100"}],
        "mve_selected_legs": [], "primary_participant_key": "syn-%d" % j,
        "early_close_condition": (
            "This market will close and expire early if the event occurs."),
        "rules_primary": (
            "If Synthetic team %s wins the Synthetic Alpha %d vs Synthetic "
            "Beta %d professional game originally scheduled for Oct %d, "
            "2026, then the market resolves to Yes. If the game is "
            "postponed and not completed within two weeks, the market "
            "resolves to the last fair price. In the event of a tie the "
            "market resolves at 50 cents (synthetic %d)." % (
                tm, j, j, 1 + j % 28, j)),
        "rules_secondary": (
            "The following market refers to the Synthetic Alpha %d vs "
            "Synthetic Beta %d professional game originally scheduled for "
            "Oct %d, 2026. If the game is cancelled or does not occur the "
            "market resolves to the last fair price. Source: the league's "
            "official results (synthetic %d)." % (j, j, 1 + j % 28, j))}


def kalshi_series(n: int) -> list:
    return [{"ticker": "KXSYN%03dGAME" % k, "title": "Synthetic series %d" % k,
             "tags": ["Sports", "Synthetic"], "category": "Sports",
             "frequency": "custom"} for k in range(n)]


class FakeKalshiGet:
    """kalshi_catalogue's transport contract (`get(url, params, timeout)`),
    production-shaped pages, a fresh decoded body per call (r.json()). The
    walk can be held at a gate so its accumulation is its own segment."""

    def __init__(self, markets: int, series: int, gate=None):
        self.series = kalshi_series(series)
        self.per = -(-markets // series)
        self.total = markets
        self.gate = gate
        self.requests = []
        self.walks_done = 0

    def get(self, url, *, params=None, timeout=None):
        if self.gate is not None:
            self.gate.wait()
        self.requests.append(("GET", url, dict(params or {})))
        p = dict(params or {})

        class R:
            status_code = 200

            def __init__(self, body):
                self._b = body

            def json(self):
                return json.loads(self._b)
        if url.endswith("/series"):
            return R(json.dumps({"series": self.series, "cursor": None}))
        k = int(p["series_ticker"][5:8])
        start = k * self.per
        stop = min(self.total, start + self.per)
        return R(json.dumps({"markets": [
            kalshi_market(j, p["series_ticker"]) for j in range(start, stop)],
            "cursor": None}))


# ── seeding ──────────────────────────────────────────────────────────

async def seed(dsn: str, *, scale: float = 1.0) -> dict:
    """Write the SYNTHETIC universe. Not a measured step: the measured run
    starts from the state a production restart starts from (registry,
    refdata, shards, coverage / settlement states and rules all present)."""
    import asyncpg
    from sportsassets.market_plane import populate as POP
    from sportsassets.market_plane import rules as RULES
    n = int(PROD["pmus_markets"] * scale)
    active = int(PROD["pmus_active"] * scale)
    listed = int(PROD["pmx_listed"] * scale)
    out = {"scale": scale}
    c = await asyncpg.connect(dsn)
    try:
        await c.execute("SET synchronous_commit = off")
        now = time.time()
        recs = []
        for i in range(n):
            live = i < active
            upd = _dt.datetime.fromtimestamp(
                now - (600 if live else 4 * 86400) - (i % 3000), UTC)
            start = _dt.datetime.fromtimestamp(
                now + 3600 * (1 + i % 96) if live else now - 5 * 86400, UTC)
            lg, sp = _lg(i // 40)
            for k, (intent, side) in enumerate(
                    (("ORDER_INTENT_BUY_LONG", "a"),
                     ("ORDER_INTENT_BUY_SHORT", "b"))):
                recs.append((
                    "0x%064x" % (i * 2 + k), pmus_event(i),
                    "Synthetic Alpha %d vs. Synthetic Beta %d" % (i // 40,
                                                                  i // 40),
                    pmus_slug(i),
                    "Market %d: will Synthetic Alpha %d cover against "
                    "Synthetic Beta %d on 2026-10-%02d?" % (
                        i % 40, i // 40, i // 40, 1 + (i // 40) % 28),
                    "side", str(i % 17 + 0.5), side,
                    [pmus_event(i), "%s:syn%d" % (lg, i // 40)], intent,
                    None, upd, "SA%d" % k,
                    "Synthetic %s %d" % ("Alpha" if k == 0 else "Beta",
                                         i // 40),
                    "Synthetic", 100000 + (i // 40) * 2 + k, lg, start,
                    pmus_type(i), "PREGAME" if live else "ENDED",
                    "VENUE_LIVE_FLAG" if live else "VENUE_ENDED_FLAG",
                    "WINDOW"))
        await c.copy_records_to_table(
            "us_premap", records=recs, columns=[
                "identifier", "event_slug", "event_title", "market_slug",
                "question", "kind", "line", "side_norm", "event_keys",
                "intent", "signed", "updated_at", "team_abbr", "team_name",
                "team_safe_name", "team_id", "team_league", "game_start",
                "sports_type", "listing_state", "listing_state_source",
                "listing_pass"])
        out["us_premap_rows"] = len(recs)
        del recs
        for lane in ("full", "calendar", "fast"):
            await _receipt(c, lane, n)
        # 133 evaluated candidates (the priority tier)
        cyc = _dt.datetime.fromtimestamp(now - 600, UTC)
        await c.executemany(
            "INSERT INTO ext_candidate_outcomes (cycle_id, cycle_at, "
            " sport_key, queue_position, outcome, us_market_slug) "
            "VALUES ($1,$2,'syn',$3,'ADMITTED',$4) ON CONFLICT DO NOTHING",
            [("syn-cycle", cyc, q, pmus_slug(q * 7))
             for q in range(PROD["candidates"])])
        out["populate"] = {k: v for k, v in (await POP.populate(
            c, since=0.0, now=now, full=True)).items() if k != "excluded"}
        # refdata for the PMX-listed set (production order: priority first)
        ids = [r["contract_id"] for r in await c.fetch(
            "SELECT contract_id FROM market_plane_registry "
            " WHERE venue='POLYMARKET_US' AND active "
            " ORDER BY priority, event_start NULLS LAST, contract_id "
            " LIMIT $1", listed)]
        idx = {pmus_slug(i): i for i in range(n)}
        for k in range(0, len(ids), 2000):
            chunk = ids[k:k + 2000]
            await c.execute(
                "UPDATE market_plane_registry r SET refdata = v.rec::jsonb, "
                "       refdata_at = now(), subscription_shard = 0 "
                "  FROM (SELECT unnest($1::text[]) AS id, "
                "               unnest($2::text[]) AS rec) v "
                " WHERE r.contract_id = v.id",
                chunk, [json.dumps(instrument(s, idx[s])) for s in chunk])
        out["pmx_listed"] = len(ids)
        # PMUS rules (the premap board capture writes these in the workers)
        npm_rules = int((PROD["rules_total"] - PROD["kalshi_markets"])
                        * scale)
        written = 0
        for k in range(0, npm_rules, 5000):
            rows = [RULES.pmus_row(pmus_market_object(i))
                    for i in range(k, min(npm_rules, k + 5000))]
            written += (await RULES.upsert(c, rows, now=now))["written"]
        out["pmus_rules"] = written
        # Kalshi registry + rules through the real persister
        tx = FakeKalshiGet(int(PROD["kalshi_markets"] * scale),
                           PROD["kalshi_series"])
        from sportsassets import kalshi_catalogue as KC
        res = KC.walk(tx, sleep=lambda s: None)
        out["kalshi"] = await POP.populate_kalshi(c, res, now=now)
        del res
        # Kalshi WS fixtures (1,000 games, 2,000 tickers)
        await c.executemany(
            "INSERT INTO kalshi_fixtures_current (event_ticker, "
            " series_ticker, team_tickers, mapping_status, start_at) "
            "VALUES ($1,'KXSYNGAME',$2,'ESTABLISHED',$3) "
            "ON CONFLICT (event_ticker) DO NOTHING",
            [("KXSYNGAME-%05d" % g, ["KXSYNGAME-%05d-A" % g,
                                     "KXSYNGAME-%05d-B" % g],
              _dt.datetime.fromtimestamp(now + 3600, UTC))
             for g in range(PROD["kalshi_ws_tickers"] // 2)])
        # warm coverage / settlement states (a restart finds them written)
        cov = await POP.coverage_pass(c, fresh_symbols=set(), now=now)
        out["coverage_active"] = cov["active"]
        out["sizes"] = dict(await c.fetchrow(
            "SELECT (SELECT count(*) FROM market_plane_registry) AS registry,"
            " (SELECT count(*) FROM market_plane_registry WHERE venue = "
            "  'POLYMARKET_US') AS pmus, (SELECT count(*) FROM "
            "  market_plane_registry WHERE venue = 'KALSHI') AS kalshi, "
            " (SELECT sum(pg_column_size(r.*)) FROM market_plane_registry r)"
            "  AS registry_bytes, (SELECT count(*) FROM market_plane_rules)"
            "  AS rules, (SELECT sum(pg_column_size(r.*)) FROM "
            "  market_plane_rules r) AS rules_bytes"))
    finally:
        await c.close()
    return out


async def _receipt(c, lane: str, n: int):
    now = _dt.datetime.now(UTC)
    await c.execute(
        "INSERT INTO venue_catalogue_receipts (lane, started_at, "
        " finished_at, outcome, pages_read, requests, events_seen, "
        " events_kept, events_dropped, markets_seen, markets_kept, "
        " markets_dropped, sides_written, truncated, version, receipt) "
        "VALUES ($1,$2,$2,'COMPLETE',1,1,1,1,0,$3,$3,0,$3,false,"
        " 'SYNTHETIC_HARNESS',$4::jsonb)",
        lane, now, n, json.dumps({"complete": True,
                                  "catalogue_complete": True,
                                  "synthetic": True}))


# ── the fakes the run loop is handed ─────────────────────────────────

class FakePMX:
    """pmx_institutional.Institutional's surface the plane uses: token()
    and read_instruments(body). Pages answer with the registry's own listed
    records (identical JSON: re-storing them changes nothing but
    refdata_at), so a run leaves the universe exactly as it found it."""
    universe: list = []

    def __init__(self, env=None, session=None):
        self.calls = []

    def token(self):
        return "tok-NOT-REAL-harness"

    def invalidate_token(self):
        pass

    def read_instruments(self, body):
        self.calls.append(sorted(body))
        u = FakePMX.universe
        if body.get("symbols"):
            # a by-symbol read the venue does not answer: a retryable
            # failure (cooldown), never an unlisted proof -- so a run
            # leaves the registry's refdata states as it found them
            return {"read": "instruments", "status": None,
                    "transportError": "SyntheticUnavailable", "ms": 5.0}
        page = int(body.get("pageToken") or 0)
        recs = [instrument(s, i) for s, i in u[page * 1000:(page + 1) * 1000]]
        nxt = "" if (page + 1) * 1000 >= len(u) else str(page + 1)
        return {"status": 200, "body": {"instruments": recs,
                                        "nextPageToken": nxt,
                                        "eof": not nxt}, "ms": 5.0}


def fake_venue_server(symbols: list, others: int, gate: threading.Event,
                      rate_per_s: float = 4000.0):
    """An in-process gRPC server for the VENDORED market-data service: on
    a connection it waits for `gate`, then sends one depth-10 book for every
    instrument (the registry's `symbols` and `others` synthetic non-registry
    instruments, as the subscribe-all stream does), then keeps re-sending
    books at `rate_per_s`. Returns (server, target, stats)."""
    import grpc
    from google.protobuf.timestamp_pb2 import Timestamp
    from sportsassets.vendor.pmx_proto import marketdatasubscription_pb2 as pb2
    from sportsassets.vendor.pmx_proto import (
        marketdatasubscription_pb2_grpc as pb2_grpc)
    universe = list(symbols) + ["syn-other-%06d" % k for k in range(others)]
    stats = {"sent": 0, "connections": 0, "received": []}

    def upd(sym, k):
        t = Timestamp()
        t.FromNanoseconds(time.time_ns())
        return pb2.BiDirectionalStreamMarketDataResponse(
            update=pb2.MarketDataUpdate(
                symbol=sym,
                bids=[pb2.BookEntry(px=450 - 5 * x, qty=1000 + k % 977 + x)
                      for x in range(10)],
                offers=[pb2.BookEntry(px=470 + 5 * x, qty=500 + k % 613 + x)
                        for x in range(10)],
                state=pb2.INSTRUMENT_STATE_OPEN, transact_time=t))

    class Venue(pb2_grpc.MarketDataSubscriptionAPIServicer):
        def BiDirectionalStreamMarketData(self, request_iterator, context):
            stats["connections"] += 1

            def drain():
                try:
                    for req in request_iterator:
                        stats["received"].append(req.WhichOneof("command"))
                except Exception:                               # noqa: BLE001
                    pass
            threading.Thread(target=drain, daemon=True).start()
            # the venue's heartbeats while the harness holds the books back
            # (a silent stream is cancelled by the transport's watchdog)
            while not gate.wait(5.0):
                if not context.is_active():
                    return
                yield pb2.BiDirectionalStreamMarketDataResponse(
                    heartbeat=pb2.Heartbeat())
            for k, s in enumerate(universe):
                stats["sent"] += 1
                yield upd(s, k)
            rnd = random.Random(7)
            while context.is_active():
                for _ in range(int(rate_per_s // 10)):
                    stats["sent"] += 1
                    yield upd(universe[rnd.randrange(len(universe))],
                              stats["sent"])
                time.sleep(0.1)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    pb2_grpc.add_MarketDataSubscriptionAPIServicer_to_server(Venue(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, "127.0.0.1:%d" % port, stats


class FakeKalshiSocket:
    """kalshi_ws Subscriber's socket: answers every subscribe with one
    orderbook_snapshot per ticker, then idles."""

    def __init__(self):
        self.q = asyncio.Queue()
        self.sent = []
        self.seq = 0

    async def send(self, raw):
        m = json.loads(raw)
        self.sent.append(m.get("cmd"))
        if m.get("cmd") == "subscribe":
            for t in m["params"]["market_tickers"]:
                self.seq += 1
                await self.q.put(json.dumps({
                    "type": "orderbook_snapshot", "sid": 1, "seq": self.seq,
                    "msg": {"market_ticker": t,
                            "yes_dollars_fp": [["0.%02d" % p, "%d.00" % (
                                100 + p)] for p in range(30, 45)],
                            "no_dollars_fp": [["0.%02d" % p, "%d.00" % (
                                90 + p)] for p in range(40, 55)]}}))

    async def recv(self):
        return await self.q.get()

    async def close(self):
        pass


# ── the probe: RSS / VmHWM (and tracemalloc) per step ────────────────

class Probe:
    """Wraps the run loop's step functions. Each top-level call is a row
    (RSS before, after, its own VmHWM peak); the inline code between two
    wrapped calls is its own row (`inline:<before name>`)."""

    def __init__(self, trace: bool = False, top_steps=()):
        self.trace = trace
        self.top_steps = set(top_steps or ())
        self.rows: list = []
        self.depth = 0
        self.pass_no = 0
        self.lock = threading.Lock()
        self._end_rss = rss_mb()
        self._snap0 = None
        self._traced0 = None
        reset_hwm()

    def _trace_start(self, name):
        if not self.trace:
            return
        import tracemalloc
        tracemalloc.reset_peak()
        self._traced0 = round(tracemalloc.get_traced_memory()[0]
                              / 1048576.0, 1)
        if name in self.top_steps:
            self._snap0 = tracemalloc.take_snapshot().filter_traces((
                tracemalloc.Filter(False, tracemalloc.__file__),
                tracemalloc.Filter(False, "<frozen importlib._bootstrap>")))

    def _row(self, name, before, t0, kind="step"):
        after, peak = rss_mb(), hwm_mb()
        row = {"pass": self.pass_no, "step": name, "kind": kind,
               "rss_before": before, "rss_after": after,
               "delta_mb": round((after or 0) - (before or 0), 1),
               "peak_mb": peak,
               "peak_delta_mb": round((peak or 0) - (before or 0), 1),
               "seconds": round(time.monotonic() - t0, 2)}
        if self.trace:
            import tracemalloc
            cur, pk = tracemalloc.get_traced_memory()
            row["traced_before_mb"] = self._traced0
            row["traced_mb"] = round(cur / 1048576.0, 1)
            row["traced_peak_mb"] = round(pk / 1048576.0, 1)
            row["traced_peak_delta_mb"] = round(
                pk / 1048576.0 - (self._traced0 or 0.0), 1)
            if self._snap0 is not None:
                snap = tracemalloc.take_snapshot().filter_traces((
                    tracemalloc.Filter(False, tracemalloc.__file__),
                    tracemalloc.Filter(False,
                                       "<frozen importlib._bootstrap>")))
                top = snap.compare_to(self._snap0, "lineno")[:6]
                row["top_retained"] = [
                    "%s:%d %+.1f MB (%+d blocks)" % (
                        s.traceback[0].filename.split("backend/")[-1],
                        s.traceback[0].lineno, s.size_diff / 1048576.0,
                        s.count_diff) for s in top]
                del snap
                self._snap0 = None
            tracemalloc.reset_peak()
            self._traced0 = round(tracemalloc.get_traced_memory()[0]
                                  / 1048576.0, 1)
        self.rows.append(row)
        return row

    def _enter(self, name):
        with self.lock:
            self.depth += 1
            if self.depth != 1:
                return None
        before, peak = rss_mb(), hwm_mb()
        if self._end_rss is not None and before is not None and (
                abs(before - self._end_rss) >= 1.0
                or (peak or 0) - self._end_rss >= 2.0):
            self._row("inline:before:%s" % name, self._end_rss,
                      time.monotonic(), kind="inline")
        # a --top snapshot is taken BEFORE the step's RSS baseline and peak
        # reset: its own memory is never billed to the step
        self._trace_start(name)
        before = rss_mb()
        reset_hwm()
        return before, time.monotonic()

    def _exit(self, name, st):
        with self.lock:
            self.depth -= 1
        if st is None:
            return
        self._row(name, st[0], st[1])
        self._end_rss = rss_mb()
        reset_hwm()

    def segment_start(self, name):
        self._trace_start(name)
        before = rss_mb()
        reset_hwm()
        return before

    def segment(self, name, before):
        """An externally timed segment (the stream fill, the walk)."""
        self._row(name, before, time.monotonic(), kind="segment")
        self._end_rss = rss_mb()
        reset_hwm()

    def wrap_async(self, owner, attr, name=None):
        fn = getattr(owner, attr)
        label = name or attr

        @functools.wraps(fn)
        async def w(*a, **k):
            st = self._enter(label)
            try:
                return await fn(*a, **k)
            finally:
                self._exit(label, st)
        setattr(owner, attr, w)
        return fn

    def wrap_sync(self, owner, attr, name=None):
        fn = getattr(owner, attr)
        label = name or attr

        @functools.wraps(fn)
        def w(*a, **k):
            st = self._enter(label)
            try:
                return fn(*a, **k)
            finally:
                self._exit(label, st)
        setattr(owner, attr, w)
        return fn


# ── the measured run ─────────────────────────────────────────────────

async def measure(dsn: str, *, passes: int = 5, trace: bool = False,
                  with_kalshi_ws: bool = True, top_steps=(),
                  full_every: int = 0, kalshi_every_s: float | None = None,
                  scale: float = 1.0) -> dict:
    """Run the REAL W.run() for `passes` passes (pass 1 = boot) under the
    probe. Returns {rows, heartbeat, snapshot_bytes, ...}."""
    import asyncpg
    os.environ.update({
        "DATABASE_URL": dsn, "UMP_RUNTIME": "HARNESS_SYNTHETIC",
        "UNIVERSAL_MARKET_PLANE": "on", "INSTITUTIONAL_MD_STREAM": "on",
        "UMP_SUBSCRIBE_ALL": "on", "KALSHI_CATALOGUE": "on",
        "PMX_CLIENT_ID": "syn-client-NOT-REAL", "PMX_KEY_ID": "syn-kid",
        "PMX_PRIVATE_KEY_B64": "c3luLU5PVC1SRUFM"})
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)
    pem = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    if with_kalshi_ws:
        os.environ.update({"KALSHI_API_KEY_ID": "syn-kalshi-NOT-REAL",
                           "KALSHI_PRIVATE_KEY_PEM": pem})
    if trace:
        import tracemalloc
        tracemalloc.start(1)
    probe = Probe(trace=trace, top_steps=top_steps)
    boot_rss = rss_mb()
    from sportsassets import institutional_stream as IS
    from sportsassets import kalshi_catalogue as KC
    from sportsassets import kalshi_ws as KWS
    from sportsassets import pmx_institutional as PMXI
    from sportsassets.market_plane import populate as POP
    from sportsassets.market_plane import registry as R
    from sportsassets.market_plane import sharded_stream as SS
    from sportsassets.workers import kalshi_ws_market_data as KWSMD
    from sportsassets.workers import universal_market_plane as W
    import_rss = rss_mb()
    c = await asyncpg.connect(dsn)
    listed = [r["contract_id"] for r in await c.fetch(
        "SELECT contract_id FROM market_plane_registry WHERE venue = "
        "'POLYMARKET_US' AND refdata IS NOT NULL AND active "
        "ORDER BY contract_id")]
    n = int(await c.fetchval("SELECT count(DISTINCT market_slug) "
                             "FROM us_premap"))
    idx = {pmus_slug(i): i for i in range(n)}
    FakePMX.universe = [(s, idx[s]) for s in listed if s in idx]
    venue_gate, walk_gate = threading.Event(), threading.Event()
    server, target, vstats = fake_venue_server(
        listed, max(0, int(PROD["stream_instruments"] * scale)
                     - len(listed)),
        venue_gate)
    kal = FakeKalshiGet(int(PROD["kalshi_markets"] * scale),
                        PROD["kalshi_series"],
                        gate=walk_gate)
    real_walk = KC.walk

    def walk(**kw):
        try:
            return real_walk(kal, sleep=lambda s: None, **kw)
        finally:
            kal.walks_done += 1
    KC.walk = walk
    PMXI.Institutional = FakePMX
    IS.GrpcBidiTransport._channel = (
        lambda self, grpc: grpc.insecure_channel(target))
    KWS.websockets_connect = (lambda key_id, pk, url=None: (
        lambda: _async_value(FakeKalshiSocket())))

    async def limits(key_id, pk, *, now):
        return {"status": "OK", "as_of": now, "source": "SYNTHETIC"}
    KWSMD.read_limits = limits
    # every pass runs every step; the full repopulate only at boot and
    # when the harness lands a new 'full' receipt (as production does)
    for k in ("POPULATE_EVERY_S", "ASSIGN_EVERY_S", "COVERAGE_EVERY_S",
              "CERTIFY_EVERY_S", "SNAPSHOT_EVERY_S"):
        setattr(W, k, 0.0)
    W.INTERVAL_S = 0.0
    W.FULL_POPULATE_EVERY_S = 10 ** 9
    if kalshi_every_s is not None:
        W.KALSHI_EVERY_S = float(kalshi_every_s)   # repeat walks (no gate)
    probe.wrap_async(W, "kalshi_step")
    probe.wrap_async(POP, "catalogue_completeness")
    probe.wrap_async(POP, "populate")
    probe.wrap_async(R, "assign_missing_shards")
    probe.wrap_async(R, "assigned_contracts")
    probe.wrap_sync(SS.Manager, "sync", "Manager.sync")
    probe.wrap_async(W, "refdata_step")
    probe.wrap_sync(W, "fresh_symbols")
    probe.wrap_async(POP, "coverage_pass")
    probe.wrap_async(W, "certify")
    probe.wrap_async(W, "snapshot")
    probe.wrap_async(R, "record_event")
    for opt in ("sync_books",):
        if hasattr(W, opt):
            probe.wrap_async(W, opt)
    beat_sizes, released = [], asyncio.Event()
    hb_real = W.heartbeat
    state = {"beats": 0}

    async def hb(service, status="ok", detail=None, con=None):
        st = probe._enter("heartbeat")
        try:
            from sportsassets.db import heartbeat_json
            beat_sizes.append(len(heartbeat_json(detail)))
            await hb_real(service, status, detail, con=con)
        finally:
            probe._exit("heartbeat", st)
        if service == W.SERVICE and status != "error":
            state["beats"] += 1
            state["last_detail"] = detail
            released.clear()
            await released.wait()
    W.heartbeat = hb
    tasks = [asyncio.ensure_future(W.run())]
    if with_kalshi_ws:
        tasks.append(asyncio.ensure_future(KWSMD.run()))

    async def until(pred, timeout=3600.0 if trace else 600.0):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            await asyncio.sleep(0.05)
        raise RuntimeError("harness: condition not reached")
    try:
        for p in range(1, passes + 1):
            probe.pass_no = p
            await until(lambda: state["beats"] >= p or any(
                t.done() for t in tasks))
            if any(t.done() for t in tasks):
                for t in tasks:
                    if t.done() and t.exception():
                        raise t.exception()
            if p == 1:
                # the stream: one book per instrument, then a steady rate
                b0 = probe.segment_start("stream:subscribe_all_books_fill")
                venue_gate.set()
                mgr_books = _books_of(SS)
                await until(lambda: mgr_books() is not None and
                            mgr_books()._messages >= len(listed) +
                            max(0, int(PROD["stream_instruments"] * scale)
                                - len(listed)))
                probe.segment("stream:subscribe_all_books_fill", b0)
                # the Kalshi walk thread (started by pass 1's kalshi_step)
                b0 = probe.segment_start("thread:kalshi_catalogue_walk")
                walk_gate.set()
                await until(lambda: kal.walks_done >= 1)
                probe.segment("thread:kalshi_catalogue_walk", b0)
            # premap's churn between passes (the incremental lane's input)
            await c.execute(
                "UPDATE us_premap SET updated_at = now() WHERE market_slug "
                "IN (SELECT market_slug FROM us_premap WHERE listing_state "
                "<> 'ENDED' ORDER BY random() LIMIT $1)",
                PROD["premap_churn_per_10s"] // 2)
            if p == 3 or (full_every and p % full_every == 0):
                await _receipt(c, "full", n)       # a new full receipt
            released.set()
        out_rss = rss_mb()
    finally:
        venue_gate.set()
        walk_gate.set()
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except BaseException:                               # noqa: BLE001
                pass
        server.stop(0)
        KC.walk = real_walk
        snap = await c.fetchrow(
            "SELECT length(payload::text) AS n FROM market_plane_events "
            " WHERE kind = 'SNAPSHOT' ORDER BY at DESC LIMIT 1")
        await c.close()
    det = state.get("last_detail") or {}
    return {"boot_rss_mb": boot_rss, "import_rss_mb": import_rss,
            "end_rss_mb": out_rss, "rows": probe.rows,
            "process_hwm_mb": hwm_mb(), "heartbeat_chars": beat_sizes[-3:],
            "snapshot_chars": snap["n"] if snap else None,
            "stream": {"sent": vstats["sent"],
                       "connections": vstats["connections"],
                       "client_commands": sorted(set(vstats["received"]))},
            "kalshi_requests": len(kal.requests),
            "heartbeat_memory": det.get("memory"),
            "trace": trace}


async def _async_value(v):
    return v


def _books_of(SS):
    """The live subscribe-all books (the Manager's shard 0), found through
    the Manager instances the run loop created."""
    import gc

    def get():
        for o in gc.get_objects():
            if isinstance(o, SS.Manager) and o.shards.get(0):
                return o.shards[0]["books"]
        return None
    cache = {}

    def books():
        if "b" not in cache:
            b = get()
            if b is None:
                return None
            cache["b"] = b
        return cache["b"]
    return books


def table(rows: list) -> str:
    out = ["%-4s %-40s %8s %8s %8s %8s %7s %9s" % (
        "pass", "step", "rss_in", "rss_out", "delta", "peak+", "sec",
        "traced_pk+")]
    for r in rows:
        out.append("%-4s %-40s %8s %8s %+8.1f %+8.1f %7.2f %9s" % (
            r["pass"], r["step"][:40], r["rss_before"], r["rss_after"],
            r["delta_mb"], r["peak_delta_mb"], r["seconds"],
            r.get("traced_peak_delta_mb", "")))
        for t in r.get("top_retained") or ():
            out.append("       %s" % t)
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("seed", "run"))
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--passes", type=int, default=5)
    ap.add_argument("--full-every", type=int, default=0,
                    help="land a new 'full' catalogue receipt every N passes "
                         "(a full repopulate), beside the one at pass 3")
    ap.add_argument("--kalshi-every-s", type=float, default=None,
                    help="repeat the Kalshi walk this often (steady state)")
    ap.add_argument("--tracemalloc", action="store_true")
    ap.add_argument("--top", default="",
                    help="comma-separated steps to snapshot (tracemalloc "
                         "top retained sites; slow)")
    ap.add_argument("--no-kalshi-ws", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    if not a.dsn:
        sys.exit("DATABASE_URL (or --dsn) is required")
    if a.cmd == "seed":
        print(json.dumps(asyncio.run(seed(a.dsn, scale=a.scale)),
                         default=str, indent=1))
        return
    if os.environ.get("MALLOC_ARENA_MAX") != "2":
        print("WARNING: MALLOC_ARENA_MAX is not 2 (production runs with 2)",
              file=sys.stderr)
    got = asyncio.run(measure(a.dsn, passes=a.passes, trace=a.tracemalloc,
                              with_kalshi_ws=not a.no_kalshi_ws,
                              top_steps=[x for x in a.top.split(",") if x],
                              full_every=a.full_every, scale=a.scale,
                              kalshi_every_s=a.kalshi_every_s))
    print(table(got["rows"]))
    print(json.dumps({k: v for k, v in got.items() if k != "rows"},
                     default=str, indent=1))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(got, f, default=str, indent=1)


if __name__ == "__main__":
    main()

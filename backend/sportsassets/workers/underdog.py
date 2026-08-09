"""Underdog cash-out sleeve — a live tandem test (owner directive
2026-08-08): $1 on EVERY MLB and tennis underdog moneyline on the US
venue, sold back the moment the book bids a 20% profit on the entry.

The sleeve's contract, in order of importance:
  1. NON-INTERFERENCE. Rows carry whale_username='underdog'; the
     one-fill-per-asset claim, the copy taken-checks and the copy caps
     all scope around this sleeve (migration 016) — it neither blocks
     nor is blocked by the copy or manual paths. It also refuses to
     ENTER any asset the account already holds through any other
     sleeve, so its sells can never touch someone else's inventory
     (positions pool at the venue; selling from a shared position
     would realize another sleeve's basis).
  2. One entry per GAME, ever — $1 a game bounds the test regardless
     of how long the game stays cheap. Entries fire in a T-MINUS-5
     window (owner directive 2026-08-08 evening): each game is bought
     in the five minutes before its scheduled start, so the sleeve
     never carries a whole day's slate as outstanding exposure at
     once. A game with no venue start time cannot honor the window
     and is skipped, counted, never guessed.
  3. Cash-out is a SELL FOK at the live best bid, only when
     bid >= entry * (1 + UNDERDOG_TAKE_PROFIT). A missed FOK simply
     waits for the next look at the fresh book (repricing each cycle,
     never blind-retrying an order).
  4. A row sold this way becomes status='cashed_out' — a terminal
     state the resolution settlement sweep never touches, so sale P&L
     survives the market's later resolution. Unsold rows ride to
     resolution like any hold.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

ENABLED = os.environ.get("UNDERDOG_ENABLED", "1") != "0"
PER_FILL_USD = float(os.environ.get("UNDERDOG_PER_FILL_USD", "1.00"))
# No day cap (owner directive 2026-08-08: "every MLB and tennis match").
# Natural bound: $1 x one-entry-per-game x the day's slate. The env knob
# stays for the day a ceiling is wanted back.
DAILY_USD = float(os.environ.get("UNDERDOG_DAILY_USD", "inf"))
TAKE_PROFIT = float(os.environ.get("UNDERDOG_TAKE_PROFIT", "0.20"))
# Entry band: a 48c "underdog" is a coin flip, and a sub-5c one is a
# lottery ticket whose book will never bid +20% — both refused.
MIN_ASK = float(os.environ.get("UNDERDOG_MIN_ASK", "0.05"))
MAX_ASK = float(os.environ.get("UNDERDOG_MAX_ASK", "0.48"))
# Minute cadence: the T-minus-5 window needs minute resolution.
ENTRY_SWEEP_S = float(os.environ.get("UNDERDOG_ENTRY_SWEEP_S", "60"))
ENTRY_LEAD_S = float(os.environ.get("UNDERDOG_LEAD_S", "300"))
ENTRY_GRACE_S = float(os.environ.get("UNDERDOG_GRACE_S", "60"))
CASHOUT_SWEEP_S = float(os.environ.get("UNDERDOG_CASHOUT_SWEEP_S", "60"))

# The markets table is the GLOBAL catalog: a game's moneyline is the
# BARE kindless slug ("mlb-tor-phi-2026-08-08") carrying both sides as
# two tokens. Execution maps to the US venue exactly like the manual
# desk (resolve_market_exact -> pmus.submit_fok) — the proven path.
_LEAGUES = ("mlb", "atp", "wta")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# ── Slate discovery (owner report 2026-08-08: "Tennis match plays for
# $1 in the cash out sleeve are not firing") ─────────────────────────
# The markets table only learns a market when a whale trades it or the
# metadata refresher's bounded venue paging happens to reach it. MLB
# games ride in on constant whale flow; tennis matches the whales skip
# never enter the catalog at all — so the sleeve couldn't see them.
# This discovery asks Gamma for the venue's soonest-ending open markets
# (today's games sort first), keeps MLB/Tennis two-token game markets,
# and upserts them into the shared catalog the entry sweep reads.
_DISCOVER_EVERY_S = float(os.environ.get("UNDERDOG_DISCOVER_EVERY_S", "900"))
# 50 pages, not 15 (owner report 2026-08-09 ~12:30pm ET: live tennis
# with no entries): endDate-ordered paging is FLOODED by crypto markets
# that expire every 15 minutes — 1,500 markets never reached the tennis
# slate, so live matches were never catalogued and their T-minus-5
# windows never existed. Paging stops early once endDates pass ~36h out.
_DISCOVER_PAGES = int(os.environ.get("UNDERDOG_DISCOVER_PAGES", "50"))
_DISCOVER_HORIZON_S = 36 * 3600
_discover_state: dict = {"ts": 0.0, "order_ok": True}


def _is_game_market(meta: dict, today: str) -> bool:
    """A candidate $1 game: MLB/Tennis, exactly two sides, open, and a
    BARE league-led slug ending at today's date (derivatives carry a
    suffix after it; other days are other sweeps' business)."""
    if meta.get("sport") not in ("MLB", "Tennis"):
        return False
    if meta.get("closed") or meta.get("resolved"):
        return False
    if len(meta.get("tokens") or []) != 2:
        return False
    slug = meta.get("slug") or ""
    return slug.startswith(_LEAGUES_PREFIXES) and slug.endswith(today)


_LEAGUES_PREFIXES = tuple(f"{lg}-" for lg in _LEAGUES)


def _parse_start(raw: str | None) -> float | None:
    """Gamma gameStartTime -> epoch. Formats seen in the wild: full ISO
    with Z, with offset, or 'YYYY-MM-DD HH:MM:SS+00'. None on anything
    unparseable — an unknown start is skipped, never guessed."""
    if not raw:
        return None
    s = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        from datetime import timezone as _tz
        dt = dt.replace(tzinfo=_tz.utc)
    return dt.timestamp()


async def _discover_games(today: str) -> dict:
    """Pull today's MLB/Tennis game markets straight from Gamma into the
    catalog. Soonest-ending markets first (today's games); falls back to
    plain paging if the venue rejects the ordering params."""
    import time as _t

    from .. import gamma

    stats = {"pages": 0, "upserted": 0, "starts_primed": 0}
    client = gamma.GammaClient()
    try:
        order = ({"order": "endDate", "ascending": "true"}
                 if _discover_state["order_ok"] else {})
        for page in range(_DISCOVER_PAGES):
            try:
                batch = await client.fetch_markets(
                    {"closed": "false", "limit": 100,
                     "offset": page * 100, **order})
            except Exception:  # noqa: BLE001
                if order:
                    # Ordering params rejected: remember, retry plain.
                    _discover_state["order_ok"] = False
                    log.warning("gamma endDate ordering rejected; "
                                "falling back to plain paging")
                    return await _discover_games(today)
                raise
            stats["pages"] += 1
            horizon_hit = False
            for raw in batch:
                meta = gamma.parse_market(raw)
                if meta is None or not _is_game_market(meta, today):
                    end = _parse_start(raw.get("endDate")
                                       or raw.get("end_date_iso"))
                    if end and end > _t.time() + _DISCOVER_HORIZON_S:
                        horizon_hit = True
                    continue
                await gamma.upsert_market(meta)
                stats["upserted"] += 1
                start = _parse_start(raw.get("gameStartTime")
                                     or raw.get("game_start_time"))
                if start and meta["slug"] not in _starts:
                    _starts[meta["slug"]] = start
                    stats["starts_primed"] += 1
            if len(batch) < 100 or horizon_hit:
                break
    finally:
        await client.close()
    _discover_state["ts"] = _t.time()
    return stats


def pick_underdog(quotes: list[tuple[str, float | None]],
                  min_ask: float = MIN_ASK,
                  max_ask: float = MAX_ASK) -> tuple[str, float] | None:
    """The cheaper side of a two-sided market, inside the entry band.
    Pure; returns (token_id, ask) or None."""
    priced = [(t, a) for t, a in quotes if a is not None and 0 < a < 1]
    if len(priced) != 2:
        return None
    tok, ask = min(priced, key=lambda p: p[1])
    other = max(p[1] for p in priced)
    if ask > max_ask or ask < min_ask:
        return None
    if ask >= other:          # equal books: nobody is the dog
        return None
    return tok, ask


def cash_out_threshold(entry: float, take: float = TAKE_PROFIT) -> float:
    """The bid at which the sale realizes the configured profit."""
    return round(entry * (1.0 + take), 4)


def shares_for(usd: float, ask: float) -> int:
    """Whole contracts $usd buys at ask, rounded DOWN — never over budget."""
    return int(usd / ask) if ask > 0 else 0


def entry_window(start_ts: float | None, now: float,
                 lead: float = ENTRY_LEAD_S,
                 grace: float = ENTRY_GRACE_S) -> str:
    """'enter' inside [start - lead, start + grace]; 'wait' before it;
    'missed' after; 'unknown' when the venue reports no start. Pure."""
    if not start_ts:
        return "unknown"
    delta = start_ts - now
    if delta > lead:
        return "wait"
    if delta < -grace:
        return "missed"
    return "enter"


# slug -> venue-reported start ts, cached per process (a game's start
# does not move minute to minute; entries happen once).
_starts: dict[str, float | None] = {}


async def _game_start_ts(cfg, condition_id: str) -> float | None:
    import httpx

    from datetime import timezone as _tz

    try:
        async with httpx.AsyncClient(base_url=cfg.clob_api_base,
                                     timeout=8) as http:
            resp = await http.get(f"/markets/{condition_id}")
        if resp.status_code != 200:
            return None
        val = (resp.json() or {}).get("game_start_time")
        if not val or not isinstance(val, str):
            return None
        parsed = datetime.fromisoformat(val.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_tz.utc)
        return parsed.timestamp()
    except Exception:  # noqa: BLE001
        return None


async def _best_bid(cfg, asset: str) -> float | None:
    import httpx

    try:
        async with httpx.AsyncClient(base_url=cfg.clob_api_base,
                                     timeout=8) as http:
            resp = await http.get("/book", params={"token_id": str(asset)})
        if resp.status_code != 200:
            return None
        bids = sorted((float(x["price"]) for x in
                       (resp.json().get("bids") or [])), reverse=True)
        return bids[0] if bids else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


async def _entry_sweep(pool) -> dict:
    from ..config import settings
    from ..live_executor import _clob_best_ask, _us_slug_candidates, active_venue
    from .. import pmus

    stats = {"games": 0, "entered": 0, "skipped_held": 0,
             "skipped_band": 0, "skipped_done": 0, "unmapped": 0}
    if active_venue() != "polymarket-us":
        stats["off"] = "venue not armed"
        return stats
    # KALSHI-LEG BACKFILL (owner 2026-08-08: "make sure we aren't
    # missing any"): every OPEN $1 position this sleeve holds on
    # Polymarket must have its Kalshi task queued — including games
    # entered before the Kalshi leg existed. Runs every sweep, so the
    # guarantee is a standing invariant, not a one-shot migration;
    # UNIQUE(game_slug) makes it free once covered. The engine's own
    # band gate decides whether a late entry still prices as a dog.
    backfilled = await pool.fetch(
        """
        INSERT INTO kud_queue (game_slug, league, dog_outcome,
                               other_outcome, per_fill_usd, take_profit)
        SELECT m.slug, split_part(m.slug, '-', 1), mt.outcome,
               mt2.outcome, $1, $2
        FROM live_orders lo
        JOIN market_tokens mt ON mt.token_id = lo.asset
        JOIN markets m ON m.condition_id = mt.condition_id
        JOIN market_tokens mt2 ON mt2.condition_id = m.condition_id
                               AND mt2.token_id <> mt.token_id
        WHERE lo.whale_username = 'underdog' AND lo.status = 'filled'
        ON CONFLICT (game_slug) DO NOTHING
        RETURNING id
        """, PER_FILL_USD, TAKE_PROFIT)
    if backfilled:
        stats["kud_backfilled"] = len(backfilled)
    day_spent = float(await pool.fetchval(
        "SELECT COALESCE(sum(filled_usd), 0) FROM live_orders "
        "WHERE whale_username = 'underdog' "
        "AND placed_at > now() - interval '24 hours'") or 0)
    today = datetime.now(ET).strftime("%Y-%m-%d")
    # Slate discovery: the catalog only knows whale-traded markets, so
    # tennis was invisible. Refresh it from the venue every 15 minutes.
    import time as _tt
    if _tt.time() - _discover_state["ts"] >= _DISCOVER_EVERY_S:
        try:
            d = await _discover_games(today)
            stats["discover"] = d
        except Exception as exc:  # noqa: BLE001 — catalog rows still serve
            stats["discover_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    # A game moneyline is the BARE slug: league-led and ENDING at the
    # date — every derivative (total, spread, prop, segment) carries a
    # suffix after it.
    rows = await pool.fetch(
        """
        SELECT m.slug, m.title, m.condition_id, mt.outcome, mt.token_id
        FROM markets m JOIN market_tokens mt USING (condition_id)
        WHERE COALESCE(m.resolved, false) = false
          AND (m.slug LIKE 'mlb-%' OR m.slug LIKE 'atp-%'
               OR m.slug LIKE 'wta-%')
          AND m.slug LIKE '%' || $1
        ORDER BY m.slug, mt.outcome_index
        """, today)
    games: dict[str, list] = {}
    for r in rows:
        games.setdefault(r["slug"], []).append(r)
    cfg = settings()
    for slug, sides in games.items():
        if len(sides) != 2:
            continue
        stats["games"] += 1
        # Per-sport visibility: "tennis not firing" must be readable
        # from the heartbeat, not inferred from a bare total.
        _lg = "games_mlb" if slug.startswith("mlb-") else "games_tennis"
        stats[_lg] = stats.get(_lg, 0) + 1
        if day_spent + PER_FILL_USD > DAILY_USD:
            stats["skipped_day_cap"] = stats.get("skipped_day_cap", 0) + 1
            continue
        # T-MINUS-5 WINDOW: only games starting within the lead fire;
        # everything else waits for its own five-minute window.
        if slug not in _starts:
            _starts[slug] = await _game_start_ts(
                cfg, next(iter(sides))["condition_id"])
        import time as _t
        win = entry_window(_starts.get(slug), _t.time())
        if win != "enter":
            k = f"skipped_{win}"
            stats[k] = stats.get(k, 0) + 1
            continue
        quotes = []
        for s in sides:
            quotes.append((str(s["token_id"]),
                           await _clob_best_ask(cfg, str(s["token_id"]))))
        dog = pick_underdog(quotes)
        if dog is None:
            stats["skipped_band"] += 1
            continue
        token, ask = dog
        outcome = next((s["outcome"] for s in sides
                        if str(s["token_id"]) == token), None)
        other = next((s["outcome"] for s in sides
                      if str(s["token_id"]) != token), None)
        # KALSHI LEG (owner directive 2026-08-08): the same game, same
        # dog, same dollar, queued for the engine's relay — only that
        # process holds Kalshi credentials. Enqueued BEFORE the PMUS
        # held-veto: a copy holding this game on Polymarket does not
        # cancel the Kalshi leg (the engine runs its own held check
        # against the Kalshi book). UNIQUE(game_slug) makes re-sweeps
        # a no-op, so each game queues exactly once.
        if outcome and other:
            queued = await pool.fetchval(
                """
                INSERT INTO kud_queue (game_slug, league, dog_outcome,
                                       other_outcome, per_fill_usd,
                                       take_profit)
                VALUES ($1, split_part($1, '-', 1), $2, $3, $4, $5)
                ON CONFLICT (game_slug) DO NOTHING
                RETURNING id
                """, slug, outcome, other, PER_FILL_USD, TAKE_PROFIT)
            if queued is not None:
                stats["kud_queued"] = stats.get("kud_queued", 0) + 1
        # NON-INTERFERENCE and one-entry-per-game in one check: any
        # existing row on either token — any sleeve INCLUDING our own,
        # any status that ever held inventory — vetoes entry. (An
        # 'unfilled' FOK does retry at the next sweep's fresh price,
        # deliberately.)
        held = await pool.fetchval(
            "SELECT 1 FROM live_orders WHERE asset = ANY($1::text[]) "
            "AND status IN ('submitting','filled','settled','cashed_out') "
            "LIMIT 1", [str(s["token_id"]) for s in sides])
        if held:
            stats["skipped_held"] += 1
            continue
        n = shares_for(PER_FILL_USD, ask)
        if n < 1:
            continue
        # US mapping first — the manual desk's exact pipeline. No US
        # market, no trade: mapped-or-refused, never guessed.
        mapping = await asyncio.to_thread(
            pmus.resolve_market_exact,
            _us_slug_candidates(slug, outcome or ""), outcome)
        if mapping is None:
            stats["unmapped"] += 1
            continue
        # NO-STACK: the token-level veto above only sees OUR sleeves'
        # rows; the engine's own fills live in its ledger. The account
        # is the referee — never add to a market it already holds.
        if await asyncio.to_thread(pmus.account_holds,
                                   mapping["market_slug"]):
            stats["skipped_held"] += 1
            continue
        limit = round(min(ask + 0.02, 0.99), 2)
        n = shares_for(PER_FILL_USD, limit)
        if n < 1:
            continue
        row_id = await pool.fetchval(
            """
            INSERT INTO live_orders (whale_username, asset, condition_id,
                                     side, his_price, limit_price,
                                     requested_usd, requested_shares,
                                     status, venue, us_market_slug)
            VALUES ('underdog', $1, $2, 'BUY', $3, $4, $5, $6,
                    'submitting', 'polymarket-us', $7)
            RETURNING id
            """, token, next(iter(sides))["condition_id"], ask, limit,
            round(n * limit, 2), float(n), mapping["market_slug"])
        try:
            r = await asyncio.to_thread(pmus.submit_fok,
                                        mapping["market_slug"], limit, n)
        except Exception as exc:  # noqa: BLE001
            await pool.execute(
                "UPDATE live_orders SET status='error', error=$2 "
                "WHERE id=$1", row_id, str(exc)[:300])
            continue
        filled = float(r.get("filled_shares") or 0)
        if r.get("ok") and filled > 0:
            px = float(r.get("fill_price") or limit)
            await pool.execute(
                """UPDATE live_orders SET status='filled', order_id=$2,
                   fill_price=$3, filled_shares=$4, filled_usd=$5
                   WHERE id=$1""",
                row_id, r.get("order_id"), px, filled,
                round(filled * px, 2))
            day_spent += filled * px
            stats["entered"] += 1
            log.warning("UNDERDOG entry %s (%s) x%d @ %.2f",
                        mapping["market_slug"], outcome, int(filled), px)
        else:
            await pool.execute(
                "UPDATE live_orders SET status='unfilled', error=$2 "
                "WHERE id=$1", row_id,
                str(r.get("status") or "unfilled")[:200])
    return stats


async def _cashout_sweep(pool) -> dict:
    from ..config import settings
    from ..live_executor import active_venue
    from .. import pmus

    stats = {"open": 0, "cashed": 0}
    if active_venue() != "polymarket-us":
        return stats
    rows = await pool.fetch(
        "SELECT id, asset, us_market_slug, fill_price::float8 AS entry, "
        "filled_shares::float8 AS qty FROM live_orders "
        "WHERE whale_username = 'underdog' AND status = 'filled' "
        "AND us_market_slug IS NOT NULL")
    cfg = settings()
    for r in rows:
        stats["open"] += 1
        # The global book's bid is the cheap TRIGGER; the sale itself is
        # a US-venue FOK whose limit IS the +20% threshold, so a fill
        # can only ever realize at least the requested profit — a
        # cross-book gap simply means no fill and a retry at the next
        # look.
        bid = await _best_bid(cfg, r["asset"])
        want = cash_out_threshold(r["entry"])
        if bid is None or bid < want:
            continue
        try:
            res = await asyncio.to_thread(
                pmus.submit_fok, r["us_market_slug"],
                round(min(want, 0.99), 2), int(r["qty"]), True)
        except Exception as exc:  # noqa: BLE001
            log.warning("underdog cash-out failed (%s): %s",
                        r["us_market_slug"], exc)
            continue
        sold = float(res.get("filled_shares") or 0)
        if res.get("ok") and sold >= r["qty"]:
            px = float(res.get("fill_price") or want)
            pnl = round((px - r["entry"]) * r["qty"], 4)
            await pool.execute(
                """UPDATE live_orders SET status='cashed_out', pnl=$2,
                   settled_at=now() WHERE id=$1""", r["id"], pnl)
            stats["cashed"] += 1
            log.warning("UNDERDOG CASH-OUT %s: %d @ %.2f (entry %.2f) "
                        "pnl +%.2f", r["us_market_slug"], int(r["qty"]),
                        px, r["entry"], pnl)
    return stats


async def _record(pool) -> dict:
    """The sleeve's cumulative scorecard, on every heartbeat: 'how is
    the 20% selling sleeve performing' must be a probe read, not an
    inference (owner question 2026-08-08 evening)."""
    # FLAT numeric keys: the health endpoint's sanitizer passes numbers
    # through but truncates strings at 80 chars — a nested dict arrives
    # stringified and cut mid-key (observed on the first probe of this
    # very counter).
    out: dict = {}
    rows = await pool.fetch(
        "SELECT status, count(*)::int AS n, "
        "COALESCE(sum(filled_usd), 0)::float8 AS usd, "
        "COALESCE(sum(pnl), 0)::float8 AS pnl "
        "FROM live_orders WHERE whale_username = 'underdog' "
        "GROUP BY status")
    for r in rows:
        out[f"pm_{r['status']}"] = r["n"]
        out[f"pm_{r['status']}_usd"] = round(r["usd"], 2)
        if r["pnl"]:
            out[f"pm_{r['status']}_pnl"] = round(r["pnl"], 2)
    try:
        krows = await pool.fetch(
            "SELECT status, count(*)::int AS n, "
            "COALESCE(sum(pnl), 0)::float8 AS pnl "
            "FROM kud_queue GROUP BY status")
        for r in krows:
            out[f"k_{r['status']}"] = r["n"]
            if r["pnl"]:
                out[f"k_{r['status']}_pnl"] = round(r["pnl"], 2)
    except Exception:  # noqa: BLE001 — table lands with migration 017
        pass
    return out


async def main() -> None:
    from ..db import get_pool, heartbeat

    if not ENABLED:
        log.warning("underdog sleeve disabled (UNDERDOG_ENABLED=0)")
        while True:
            await asyncio.sleep(3600)
    last_entry = 0.0
    while True:
        try:
            pool = await get_pool()
            import time as _t

            now = _t.time()
            if now - last_entry >= ENTRY_SWEEP_S:
                st = await _entry_sweep(pool)
                st.update(await _record(pool))
                last_entry = now
                await heartbeat("underdog", "ok", st)
                log.info("underdog entry sweep: %s", st)
            cs = await _cashout_sweep(pool)
            if cs.get("cashed"):
                log.info("underdog cashout sweep: %s", cs)
        except Exception:  # noqa: BLE001 — the supervisor restarts us
            log.exception("underdog sweep failed")
        await asyncio.sleep(CASHOUT_SWEEP_S)

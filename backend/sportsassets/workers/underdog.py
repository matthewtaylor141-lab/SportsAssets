"""Underdog cash-out sleeve v2 — RESTARTED by owner order 2026-08-12:
$2 on EVERY tennis underdog (ATP, WTA, Challenger, ITF — every tour)
and EVERY MLB underdog MONEYLINE on the US venue, entered in the five
minutes before the scheduled start, sold back the moment the book bids
a 35% profit on the entry. The owner's yardstick: what percentage of
entries cash out (reported daily).

The sleeve's contract, in order of importance:
  1. COMPLETE INDEPENDENCE (owner 2026-08-12: "completely independent
     from everything we do"). Rows carry whale_username='underdog';
     the one-fill-per-asset claim, the copy taken-checks, the copy
     never-add, the one-position-per-game rule and the copy caps all
     scope around this sleeve — it neither blocks nor is blocked by
     the copy or manual paths. It still refuses to ENTER any asset
     the account already holds through any other sleeve, so its sells
     can never touch someone else's inventory (positions pool at the
     venue; selling from a shared position would realize another
     sleeve's basis).
  2. One entry per GAME, ever — $2 a game bounds the test regardless
     of how long the game stays cheap. Entries fire in the window
     [T-5min, start] (owner 2026-08-12: "exactly 5 minutes before"):
     the minute-cadence sweep lands the buy inside T-5:00..T-4:00 in
     normal operation, and never after the scheduled start. A game
     with no venue start time cannot honor the window and is skipped,
     counted, never guessed.
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
# v2 stake (owner 2026-08-12): $2 flat on every dog.
PER_FILL_USD = float(os.environ.get("UNDERDOG_PER_FILL_USD", "2.00"))
# No day cap (owner directive 2026-08-08: "every MLB and tennis match").
# Natural bound: $2 x one-entry-per-game x the day's slate. The env knob
# stays for the day a ceiling is wanted back.
DAILY_USD = float(os.environ.get("UNDERDOG_DAILY_USD", "inf"))
# v2 exit (owner 2026-08-12): auto cash-out at +35% profit on entry
# (~$0.70 on a full $2 fill).
TAKE_PROFIT = float(os.environ.get("UNDERDOG_TAKE_PROFIT", "0.35"))
# Entry band. Widened 2026-08-09 (owner: "this should be firing every
# single mlb game and every single tennis match") — a 49c dog is still
# the cheaper side, and near-even games were the single biggest silent
# skip. The 3c floor only drops dogs whose 1c tick makes the take-profit
# trigger ambiguous.
MIN_ASK = float(os.environ.get("UNDERDOG_MIN_ASK", "0.03"))
MAX_ASK = float(os.environ.get("UNDERDOG_MAX_ASK", "0.50"))
# The v2 era boundary: scorecard stats start here so the retired
# $1/+20% test's rows never mix into the $2/+35% read. A datetime,
# not a string — asyncpg refuses implicit text->timestamptz.
V2_SINCE = datetime.fromisoformat("2026-08-12T12:00:00+00:00")
# Per-sweep cap on uncached start-time fetches (repair 2026-08-12
# evening): unbounded priming at 8s timeouts made one sweep outlast
# the 5-minute entry window. Uncovered games prime a few per sweep;
# entries always run first on what is already cached.
START_PRIME_PER_SWEEP = int(os.environ.get("UNDERDOG_PRIME_PER_SWEEP",
                                           "15"))
# Minute cadence: the T-minus-5 window needs minute resolution.
ENTRY_SWEEP_S = float(os.environ.get("UNDERDOG_ENTRY_SWEEP_S", "60"))
ENTRY_LEAD_S = float(os.environ.get("UNDERDOG_LEAD_S", "300"))
# v2: "exactly 5 minutes before" — the window is [T-5min, start] and
# entries never fire after the scheduled start (grace 0).
ENTRY_GRACE_S = float(os.environ.get("UNDERDOG_GRACE_S", "0"))
CASHOUT_SWEEP_S = float(os.environ.get("UNDERDOG_CASHOUT_SWEEP_S", "60"))
# The ENGINE's Kalshi-leg entry window, mirrored here ONLY so the
# k_windows_open / k_next_window_s heartbeat counters describe what the
# engine will actually do (the PM leg's much tighter ENTRY_GRACE_S above
# is a different window). Defaults must track kalshi_underdog.py — grace
# went 1800 -> 3600 on 2026-08-10; if the owner overrides EDGE_KUD_GRACE_S
# on the edge-shadow service it must be set identically on this worker or
# the heartbeat lies about open windows.
K_LEAD_S = 300.0
K_GRACE_S = float(os.environ.get("EDGE_KUD_GRACE_S", "3600"))

# The markets table is the GLOBAL catalog: a game's moneyline is the
# BARE kindless slug ("mlb-tor-phi-2026-08-08") carrying both sides as
# two tokens. Execution maps to the US venue exactly like the manual
# desk (resolve_market_exact -> pmus.submit_fok) — the proven path.
# v2 leagues (owner 2026-08-12: "wta, atp, challenger, etc."): every
# tennis tour the catalog can name, plus MLB moneylines.
_LEAGUES = ("mlb", "atp", "wta", "itf", "chal")
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


# Tag slugs for the sleeve's sports. A tag-filtered /markets query goes
# straight to the sport's slate — the endDate-ordered fallback below has
# to wade through thousands of 15-minute crypto expiries first and was
# reaching 3 of ~15 MLB games (owner 2026-08-09 evening: "this should be
# firing every single mlb game and every single tennis match").
_TAG_SLUGS = ("mlb", "tennis", "atp", "wta", "itf")
_tag_ids: dict[str, int | None] = {}


async def _ingest_batch(batch: list, today: str, stats: dict) -> None:
    from .. import gamma

    for raw in batch:
        meta = gamma.parse_market(raw)
        if meta is None or not _is_game_market(meta, today):
            continue
        await gamma.upsert_market(meta)
        stats["upserted"] += 1
        start = _parse_start(raw.get("gameStartTime")
                             or raw.get("game_start_time"))
        if start and meta["slug"] not in _starts:
            _starts[meta["slug"]] = start
            stats["starts_primed"] += 1


async def _discover_games(today: str) -> dict:
    """Pull today's MLB/Tennis game markets straight from Gamma into the
    catalog. Tag-filtered queries first (each sport's slate directly);
    endDate-ordered paging only as the fallback when the tag surface is
    missing, so the sleeve still sees SOMETHING on a Gamma deploy that
    drops /tags."""
    import time as _t

    from .. import gamma

    stats = {"pages": 0, "upserted": 0, "starts_primed": 0}
    client = gamma.GammaClient()
    try:
        tagged_ok = False
        for slug in _TAG_SLUGS:
            if slug not in _tag_ids:
                try:
                    _tag_ids[slug] = await client.fetch_tag_id(slug)
                except Exception:  # noqa: BLE001
                    _tag_ids[slug] = None
            tag_id = _tag_ids[slug]
            if tag_id is None:
                continue
            try:
                for page in range(10):
                    batch = await client.fetch_markets(
                        {"closed": "false", "tag_id": tag_id,
                         "related_tags": "true", "limit": 100,
                         "offset": page * 100})
                    stats["pages"] += 1
                    await _ingest_batch(batch, today, stats)
                    if len(batch) < 100:
                        break
                tagged_ok = True
                stats[f"tag_{slug}"] = tag_id
            except Exception:  # noqa: BLE001 — try the next tag/fallback
                _tag_ids[slug] = None
        if not tagged_ok:
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
                    end = _parse_start(raw.get("endDate")
                                       or raw.get("end_date_iso"))
                    if end and end > _t.time() + _DISCOVER_HORIZON_S:
                        horizon_hit = True
                await _ingest_batch(batch, today, stats)
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
# 2026-08-13: that sentence is true of baseball and NOT of tennis, where
# a match starts when the previous one on its court ends. If a cached
# start is stale, a game crosses wait -> missed with no window in
# between and _try_enter is never reached — which is exactly what the
# tennis counters look like. _seen_open records the slugs some sweep DID
# observe in-window, so a miss can be classified rather than guessed at.
_starts: dict[str, float | None] = {}
_seen_open: set[str] = set()
_missed_logged: set[str] = set()


async def _game_start_ts(cfg, condition_id: str) -> float | None:
    import httpx

    from datetime import timezone as _tz

    try:
        async with httpx.AsyncClient(base_url=cfg.clob_api_base,
                                     timeout=5) as http:
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
    # SLEEVE v2 RESTART (owner order 2026-08-12 morning: "restart
    # number 4... $2 on every underdog... cashed out when the profit
    # hits 35%"). The 2026-08-11 cut is superseded; UNDERDOG_SLEEVE=0
    # is the kill switch if it needs to go dark again.
    if os.environ.get("UNDERDOG_SLEEVE", "1") == "0":
        stats["off"] = "sleeve off (UNDERDOG_SLEEVE=0)"
        return stats
    if active_venue() != "polymarket-us":
        stats["off"] = "venue not armed"
        return stats
    # SWEEP CLOCK. A 5-minute entry window can only be caught by a sweep
    # that RUNS inside it. On 2026-08-13 tennis missed 13 windows with
    # zero attempts and every refusal counter at 0 — the signature of
    # _try_enter never being reached, not of a guard refusing. If a
    # sweep costs longer than the window is open, a whole cluster of
    # starts falls between two PASS 1 runs and is never seen. Measure
    # before changing anything: these are wall-clock milliseconds.
    import time as _clk
    _t_sweep = _clk.monotonic()
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
    # A game moneyline is the BARE slug: league-led and ENDING at the
    # date — every derivative (total, spread, prop, segment) carries a
    # suffix after it.
    rows = await pool.fetch(
        """
        SELECT m.slug, m.title, m.condition_id, mt.outcome, mt.token_id
        FROM markets m JOIN market_tokens mt USING (condition_id)
        WHERE COALESCE(m.resolved, false) = false
          AND split_part(m.slug, '-', 1) = ANY($2::text[])
          AND m.slug LIKE '%' || $1
        ORDER BY m.slug, mt.outcome_index
        """, today, list(_LEAGUES))
    games: dict[str, list] = {}
    for r in rows:
        games.setdefault(r["slug"], []).append(r)
    cfg = settings()
    import time as _t

    attempted: set[str] = set()

    async def _try_enter(slug: str, sides: list) -> None:
        nonlocal day_spent
        attempted.add(slug)
        if day_spent + PER_FILL_USD > DAILY_USD:
            stats["skipped_day_cap"] = stats.get("skipped_day_cap", 0) + 1
            return
        quotes = []
        for s in sides:
            quotes.append((str(s["token_id"]),
                           await _clob_best_ask(cfg, str(s["token_id"]))))
        dog = pick_underdog(quotes)
        if dog is None:
            stats["skipped_band"] += 1
            return
        token, ask = dog
        outcome = next((s["outcome"] for s in sides
                        if str(s["token_id"]) == token), None)
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
            return
        n = shares_for(PER_FILL_USD, ask)
        if n < 1:
            # The only two exits in this function that counted nothing.
            # A silent return is indistinguishable from "never called",
            # which is how "0 entries" stayed undiagnosable all day.
            stats["skipped_dust"] = stats.get("skipped_dust", 0) + 1
            return
        # US mapping first — the manual desk's exact pipeline. No US
        # market, no trade: mapped-or-refused, never guessed.
        mapping = await asyncio.to_thread(
            pmus.resolve_market_exact,
            _us_slug_candidates(slug, outcome or ""), outcome)
        if mapping is None:
            stats["unmapped"] += 1
            return
        # NO-STACK: the token-level veto above only sees OUR sleeves'
        # rows; the engine's own fills live in its ledger. The account
        # is the referee — never add to a market it already holds.
        if await asyncio.to_thread(pmus.account_holds,
                                   mapping["market_slug"]):
            stats["skipped_held"] += 1
            return
        # PRICE ON THE BOOK WE ACTUALLY TRADE.
        # The dog is CHOSEN on the global CLOB (both sides quoted there,
        # so the cheaper side is a fair comparison), but the order goes
        # to Polymarket US — a different venue with a different book. The
        # limit was being set from the global ask + 2c and sent to a
        # venue that never agreed to it: every entry today came back
        # "unfilled@0.51:expired", 5 for 5, and the sleeve booked zero
        # positions all day. The cash-out leg already re-quotes the US
        # slug for exactly this reason; the entry now does too. No US
        # quote, no trade — mapped-and-priced or refused, never guessed.
        us_ask = await asyncio.to_thread(pmus.slug_ask,
                                         mapping["market_slug"])
        if us_ask is None:
            stats["skipped_no_us_quote"] = \
                stats.get("skipped_no_us_quote", 0) + 1
            return
        # The venue's own price has to clear the same band: a side that
        # is the dog globally can be priced past the band here, and a
        # 70c "underdog" is not the bet the owner asked for.
        if not (MIN_ASK <= us_ask <= MAX_ASK):
            stats["skipped_band"] += 1
            return
        limit = round(min(us_ask + 0.02, 0.99), 2)
        n = shares_for(PER_FILL_USD, limit)
        if n < 1:
            stats["skipped_dust"] = stats.get("skipped_dust", 0) + 1
            return
        row_id = await pool.fetchval(
            """
            INSERT INTO live_orders (whale_username, asset, condition_id,
                                     side, his_price, limit_price,
                                     requested_usd, requested_shares,
                                     status, venue, us_market_slug)
            VALUES ('underdog', $1, $2, 'BUY', $3, $4, $5, $6,
                    'submitting', 'polymarket-us', $7)
            RETURNING id
            """, token, next(iter(sides))["condition_id"], us_ask, limit,
            round(n * limit, 2), float(n), mapping["market_slug"])
        try:
            r = await asyncio.to_thread(pmus.submit_fok,
                                        mapping["market_slug"], limit, n)
        except Exception as exc:  # noqa: BLE001
            await pool.execute(
                "UPDATE live_orders SET status='error', error=$2 "
                "WHERE id=$1", row_id, str(exc)[:300])
            return
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

    _t_pass1 = _clk.monotonic()
    # PASS 1 — WINDOWS FIRST (repair 2026-08-12 evening: the sleeve
    # took ZERO entries all day while 42 games rolled wait->missed.
    # The sweep's slow work — uncached start fetches at 8s timeouts
    # each, plus tag discovery — ran BEFORE the window check, so one
    # sweep could outlast the 5-minute window itself. In-window games
    # now trade FIRST, on cached starts, before any slow work runs.)
    for slug, sides in games.items():
        if len(sides) != 2:
            continue
        st_ts = _starts.get(slug)
        if st_ts and entry_window(st_ts, _t.time()) == "enter":
            stats["windows_open_now"] = stats.get("windows_open_now",
                                                  0) + 1
            _seen_open.add(slug)
            await _try_enter(slug, sides)

    stats["pass1_ms"] = int((_clk.monotonic() - _t_pass1) * 1000)
    _t_pass2 = _clk.monotonic()
    # PASS 2 — bookkeeping and BOUNDED priming: per-sweep start
    # fetches are capped so this pass can never outlast a window; a
    # game already inside its window when first primed enters NOW
    # (its five minutes may be half spent already).
    primed = 0
    for slug, sides in games.items():
        if len(sides) != 2:
            continue
        stats["games"] += 1
        # Per-sport visibility: "tennis not firing" must be readable
        # from the heartbeat, not inferred from a bare total.
        _lg = "games_mlb" if slug.startswith("mlb-") else "games_tennis"
        stats[_lg] = stats.get(_lg, 0) + 1
        if slug not in _starts and primed < START_PRIME_PER_SWEEP:
            _starts[slug] = await _game_start_ts(
                cfg, next(iter(sides))["condition_id"])
            primed += 1
            st_ts = _starts.get(slug)
            if (st_ts and slug not in attempted
                    and entry_window(st_ts, _t.time()) == "enter"):
                await _try_enter(slug, sides)
        # KALSHI LEG (owner 2026-08-09: "firing every single mlb game
        # and every single tennis match"): EVERY catalogued game queues
        # at first sight — before the window, the band, the quotes, and
        # every PMUS-side veto. The engine is the sleeve's Kalshi arm:
        # it runs the T-minus-5 window off start_ts, picks the dog from
        # its OWN book (the venue's dog is the venue's price, not
        # Polymarket's), and retries inside the window. The two outcome
        # names ride along solely so the engine can resolve the matchup.
        # UNIQUE(game_slug) makes re-sweeps a no-op; a game whose start
        # was unknown at first sight gets it stamped when it appears.
        _sides = list(sides)
        _start = _starts.get(slug)
        queued = await pool.fetchval(
            """
            INSERT INTO kud_queue (game_slug, league, dog_outcome,
                                   other_outcome, per_fill_usd,
                                   take_profit, start_ts)
            VALUES ($1, split_part($1, '-', 1), $2, $3, $4, $5,
                    to_timestamp($6))
            ON CONFLICT (game_slug) DO UPDATE
                SET start_ts = COALESCE(kud_queue.start_ts,
                                        EXCLUDED.start_ts)
            RETURNING (xmax = 0) AS inserted
            """, slug, _sides[0]["outcome"], _sides[1]["outcome"],
            PER_FILL_USD, TAKE_PROFIT, _start)
        if queued:
            stats["kud_queued"] = stats.get("kud_queued", 0) + 1
        win = entry_window(_starts.get(slug), _t.time())
        if win != "enter":
            k = f"skipped_{win}"
            stats[k] = stats.get(k, 0) + 1
        # A game that reached "missed" without ANY sweep seeing it open
        # never had a window we could act on. With the sweep at ~200ms
        # and a 60s cadence, five sweeps should land inside every five
        # minute window — so a silent miss means the START MOVED under
        # the cache. Re-read it once per game (capped) and report the
        # drift; that is the number that settles the tennis question.
        if (win == "missed" and slug not in _seen_open
                and slug not in _missed_logged
                and stats.get("start_rechecks", 0) < 3):
            _missed_logged.add(slug)
            stats["missed_never_open"] = \
                stats.get("missed_never_open", 0) + 1
            stats["start_rechecks"] = stats.get("start_rechecks", 0) + 1
            fresh = await _game_start_ts(
                cfg, next(iter(sides))["condition_id"])
            cached = _starts.get(slug)
            if fresh and cached:
                drift = int(fresh - cached)
                stats["start_drift_s"] = drift
                if abs(drift) > ENTRY_LEAD_S:
                    log.warning(
                        "UNDERDOG start MOVED %+ds on %s (cached %.0f "
                        "fresh %.0f) — the window was never real",
                        drift, slug, cached, fresh)
                    stats["start_moved"] = stats.get("start_moved", 0) + 1
                # Trust the venue's current answer from here on.
                _starts[slug] = fresh

    stats["pass2_ms"] = int((_clk.monotonic() - _t_pass2) * 1000)
    _t_disc = _clk.monotonic()
    # Slate discovery LAST — and deferred while any window is open or
    # opening soon, so pagination can never eat a window. Deferral is
    # bounded (a busy tennis afternoon must not starve the catalog of
    # NEW games): after 5 consecutive deferrals it runs regardless.
    import time as _tt
    if _tt.time() - _discover_state["ts"] >= _DISCOVER_EVERY_S:
        imminent = any(
            (st is not None and
             -ENTRY_GRACE_S <= st - _tt.time() <= ENTRY_LEAD_S + 120)
            for st in (_starts.get(s) for s in games))
        if imminent and _discover_state.get("deferrals", 0) < 5:
            _discover_state["deferrals"] = \
                _discover_state.get("deferrals", 0) + 1
            stats["discover_deferred"] = _discover_state["deferrals"]
        else:
            _discover_state["deferrals"] = 0
            try:
                d = await _discover_games(today)
                stats["discover"] = d
            except Exception as exc:  # noqa: BLE001 — catalog still serves
                stats["discover_error"] = \
                    f"{type(exc).__name__}: {str(exc)[:120]}"
    stats["discover_ms"] = int((_clk.monotonic() - _t_disc) * 1000)
    stats["sweep_ms"] = int((_clk.monotonic() - _t_sweep) * 1000)
    # A sweep longer than the window it is hunting cannot catch one.
    if stats["sweep_ms"] > ENTRY_LEAD_S * 1000:
        log.warning("UNDERDOG sweep %dms EXCEEDS the %ds entry window "
                    "(pass1 %sms pass2 %sms discover %sms, %d games) — "
                    "starts can fall between sweeps unseen",
                    stats["sweep_ms"], int(ENTRY_LEAD_S),
                    stats.get("pass1_ms"), stats.get("pass2_ms"),
                    stats.get("discover_ms"), stats.get("games", 0))
        stats["sweep_over_window"] = 1
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
    # v2 SCORECARD (owner 2026-08-12: "I want to see how many we cash
    # out by percentage. I need a full daily report on this every
    # day"). Era-scoped at the restart moment so the retired $1/+20%
    # rows never dilute the $2/+35% test's read. Cash-out % is
    # cashed / all entries ever filled (open ones simply haven't
    # decided yet), split by sport, plus today-in-ET counts for the
    # daily report.
    try:
        v2 = await pool.fetch(
            """
            SELECT CASE WHEN us_market_slug LIKE '%-mlb-%' THEN 'mlb'
                        ELSE 'tennis' END AS sport,
                   status, count(*)::int AS n,
                   COALESCE(sum(pnl), 0)::float8 AS pnl,
                   count(*) FILTER (WHERE placed_at >= date_trunc('day',
                     now() AT TIME ZONE 'America/New_York')
                     AT TIME ZONE 'America/New_York')::int AS n_today,
                   COALESCE(sum(pnl) FILTER (WHERE placed_at >=
                     date_trunc('day', now() AT TIME ZONE
                     'America/New_York') AT TIME ZONE
                     'America/New_York'), 0)::float8 AS pnl_today
            FROM live_orders
            WHERE whale_username = 'underdog'
              AND placed_at >= $1
              AND status IN ('filled', 'cashed_out', 'settled')
            GROUP BY 1, 2
            """, V2_SINCE)
        ent = sum(r["n"] for r in v2)
        cashed = sum(r["n"] for r in v2 if r["status"] == "cashed_out")
        out["ud2_entries"] = ent
        out["ud2_open"] = sum(r["n"] for r in v2 if r["status"] == "filled")
        out["ud2_cashed"] = cashed
        out["ud2_cashed_pct"] = (round(100.0 * cashed / ent, 1)
                                 if ent else 0.0)
        out["ud2_settled_lost"] = sum(
            r["n"] for r in v2 if r["status"] == "settled" and r["pnl"] <= 0)
        out["ud2_settled_won"] = sum(
            r["n"] for r in v2 if r["status"] == "settled" and r["pnl"] > 0)
        out["ud2_pnl"] = round(sum(r["pnl"] for r in v2), 2)
        out["ud2_today_entries"] = sum(r["n_today"] for r in v2)
        out["ud2_today_cashed"] = sum(
            r["n_today"] for r in v2 if r["status"] == "cashed_out")
        out["ud2_today_pnl"] = round(sum(r["pnl_today"] for r in v2), 2)
        for sport in ("mlb", "tennis"):
            out[f"ud2_{sport}_entries"] = sum(
                r["n"] for r in v2 if r["sport"] == sport)
            out[f"ud2_{sport}_cashed"] = sum(
                r["n"] for r in v2
                if r["sport"] == sport and r["status"] == "cashed_out")
    except Exception:  # noqa: BLE001 — the scorecard never blocks trading
        pass
    # ATTEMPTS, era-scoped. The scorecard above counts only rows that
    # HELD inventory, so a sleeve taking zero entries reads identically
    # whether it never tried or tried and was refused every time — which
    # is exactly the hole the 2026-08-12 "0 entries all day" sat in for
    # nine hours. The per-sweep stats can't answer it either: they are a
    # fresh dict each sweep, and a 5-minute window is almost never open
    # at the instant the probe samples. These four keys are the history.
    try:
        att = await pool.fetch(
            "SELECT status, count(*)::int AS n FROM live_orders "
            "WHERE whale_username = 'underdog' AND placed_at >= $1 "
            "GROUP BY status", V2_SINCE)
        out["ud2_attempts"] = sum(r["n"] for r in att)
        for r in att:
            if r["status"] in ("unfilled", "error", "submitting"):
                out[f"ud2_{r['status']}"] = r["n"]
        last = await pool.fetchrow(
            "SELECT status, error, limit_price::float8 AS limit_price "
            "FROM live_orders WHERE whale_username = 'underdog' "
            "  AND placed_at >= $1 AND status IN ('unfilled', 'error') "
            "ORDER BY placed_at DESC LIMIT 1", V2_SINCE)
        if last is not None:
            # Truncated hard: the health endpoint's sanitizer cuts
            # strings at 80 chars and a longer one arrives unreadable.
            out["ud2_last_refusal"] = (
                f"{last['status']}@{last['limit_price']}:"
                f"{str(last['error'] or '')}")[:78]
    except Exception:  # noqa: BLE001 — telemetry never blocks trading
        pass
    try:
        krows = await pool.fetch(
            "SELECT status, count(*)::int AS n, "
            "COALESCE(sum(pnl), 0)::float8 AS pnl "
            "FROM kud_queue GROUP BY status")
        for r in krows:
            out[f"k_{r['status']}"] = r["n"]
            if r["pnl"]:
                out[f"k_{r['status']}_pnl"] = round(r["pnl"], 2)
        # Stake audit (2026-08-10 evening: settled entries averaged
        # ~$11.66 against the $1 directive): the queue's per-fill values
        # must be probe-readable, not inferred from settled dollars.
        pf = await pool.fetchrow(
            "SELECT max(per_fill_usd)::float8 AS mx, "
            "avg(per_fill_usd)::float8 AS av FROM kud_queue")
        if pf is not None and pf["mx"] is not None:
            out["k_per_fill_max"] = round(pf["mx"], 2)
            out["k_per_fill_avg"] = round(pf["av"], 2)
        # Window timing, probe-readable: 42 tasks all 'waiting' is either
        # a slate that hasn't started or a timezone bug, and the
        # difference must be one heartbeat read, not a code dig
        # (2026-08-10 overnight: the first full tennis queue sat waiting
        # for hours with no way to see WHEN it would open).
        nx = await pool.fetchrow(
            "SELECT count(*) FILTER (WHERE start_ts IS NOT NULL "
            "  AND now() >= start_ts - make_interval(secs => $1) "
            "  AND now() <= start_ts + make_interval(secs => $2))::int "
            "  AS open_now, "
            "count(*) FILTER (WHERE start_ts IS NULL)::int AS no_start, "
            "extract(epoch FROM (min(start_ts) "
            "  FILTER (WHERE start_ts > now()) - now()))::int AS next_in_s "
            "FROM kud_queue WHERE status = 'queued'",
            float(K_LEAD_S), float(K_GRACE_S))
        if nx is not None:
            out["k_windows_open"] = nx["open_now"] or 0
            out["k_no_start"] = nx["no_start"] or 0
            if nx["next_in_s"] is not None:
                out["k_next_window_s"] = max(0, nx["next_in_s"] - int(K_LEAD_S))
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

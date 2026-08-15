"""Whale copies executed ON KALSHI (owner directive 2026-08-04: "we need
to ensure trades start being copied to Kalshi").

The Polymarket copy pipeline stays where it is; this is a SECOND copy leg
that expresses the same whale positions on Kalshi wherever Kalshi lists
the same proposition. Same trading rules as the Polymarket sleeve:

- his price + 2% relative tolerance, floored to the cent — we only take
  a Kalshi book whose ask is at/inside that limit;
- $5 per copy, uniform across whales (owner directive 2026-08-07; the
  per-whale map stays for the day the owner wants divergence back),
  whole contracts, rounded down;
- one copy per whale position EVER — and that means ACROSS VENUES, not
  per venue: identity rows the platform marks pmus_copied (a filled or
  settled live_orders copy already exists) are skipped here outright
  (counter skipped_already_copied). Plus the ledger claim, day budget
  for the class (EDGE_KCOPY_DAY_USD, default $200), kill switch
  EDGE_KCOPY=0;
- category "kalshi_copy" so settlement grades this venue's copy edge
  SEPARATELY from Polymarket's — the venues' fill mechanics differ and
  their measured ROI may too.

ROUTING (owner directive 2026-08-05): each whale position gets ONE copy,
executed at the best-priced venue at placement. The fast PMUS leg — the
chain listener reacting in seconds at his-price limit — is the PRIMARY;
this Kalshi leg fires only where Kalshi is the better-priced venue at
sweep time or PMUS couldn't fill/map the identity. Concretely: before
placing, the sweep peeks the PMUS ask for the same identity (stream-
cache-only peek_book, no REST) and skips Kalshi when the PMUS ask beats
Kalshi's fee-loaded effective price (counter routed_pmus_better). No
PMUS book, or Kalshi cheaper fee-loaded -> Kalshi places as before
(fresh gates, collapse floor, cross-game guard all unchanged).

Matching reuses the strict join built for the adds sweep: game key
(sorted tokens + date) plus the mapper's 0.95 name bar. Tennis works the
moment the tennis series tickers are in the Kalshi series map — the
census step in the diagnostic workflow names them from Kalshi's own
series list, so coverage is a config line, not a guess.
"""

from __future__ import annotations

import logging
import math
import re
import time
import uuid
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Owner directive 2026-08-10: RN1 to $100 per trade ON KALSHI — RN1
# only, promoted on the sleeve's own record (+$199.80 realized over 307
# settled since Aug 1, ~58% win rate). SwissTony stays $10; everyone
# else the $5 default. History: $3 -> $5 uniform 2026-08-07 -> $10 for
# the two proven sleeves 2026-08-08 -> RN1 $100 (Kalshi leg) 2026-08-10.
# NOTE the binding constraints at this clip: the day budget
# (EDGE_KCOPY_DAY_USD) caps fills at budget/100 per day, the copy
# breaker (EDGE_KCOPY_HALT_USD, default 120) now trips on ~2 full RN1
# losses, and account cash bounds concurrent open positions. Those are
# env-tunable risk-policy dials, deliberately not changed here.
# 2026-08-10 (owner funding the accounts up): ALL Kalshi copies to $50,
# RN1 to $100 — Kalshi leg only; PMUS sizing unchanged until the owner's
# Polymarket deposit lands.
# 2026-08-10 (owner approval, profitability round 2): swisstony to $100
# — his settled Kalshi-leg record (+$673 on $1,648 deployed) is the
# stronger of the two proven books. His soccer cell's 0.70 Kalshi ask
# floor still binds: only >=70c entries reach this venue at any clip.
# 2026-08-11 (owner decision, profitability round 3): swisstony to $200
# — strongest measured earner across both venues (+$760 on 345
# settled). The $1,200 breaker floor now represents ~6 full swisstony
# losses; kept deliberately (tighter protection during the scale-up),
# EDGE_KCOPY_HALT_USD overrides if it proves too twitchy.
# Same day, round 4: RN1 to $150 — profitable every single day since
# Aug 3 (+$250 lifetime on 332 settled at $100).
# 2026-08-12 (owner orders, the soccer resume): swisstony stays $200
# everywhere EXCEPT soccer, which clips at $100 per event — the
# per-(whale, sport) override below carries the exception.
PER_COPY_USD = {"rn1": 150.00, "swisstony": 200.00}
PER_COPY_USD_SPORT = {("swisstony", "soccer"): 100.00}
PER_COPY_DEFAULT = 50.00
# Inverse volume<->size scaling (owner order 2026-08-12): past this
# many fills in a venue-day, the clip shrinks proportionally — 10x
# the fills spends the same dollars at 1/10 the size.
KALSHI_BASELINE_FILLS = {"rn1": 15, "swisstony": 10}
KALSHI_BASELINE_DEFAULT = 8


def _per_copy_usd(whale: str, slug: str) -> float:
    """Clip for this whale on this market: the (whale, sport) override
    wins, then the whale clip, then the default."""
    from edge.shadow.copy_sports import sport_of

    w = (whale or "").lower()
    ov = PER_COPY_USD_SPORT.get((w, sport_of(slug or "")))
    return ov if ov is not None else PER_COPY_USD.get(w, PER_COPY_DEFAULT)
# A copy's edge is the whale's ENTRY edge, and it decays in minutes — the
# decay study prices our ~90s reaction at 1.3-1.5c of surviving edge.
# Copying an old position at today's price is buying fair value minus
# fees. Default 45 minutes, env-tunable.
MAX_AGE_S_DEFAULT = 2700.0
# Collapse guard: an ask far BELOW his entry is information (the game
# turned against the position), not a discount. Only-upper-bound
# tolerance made the sweep preferentially copy his losers-in-progress
# (observed live 2026-08-04: copied positions sitting at 1% chance).
COLLAPSE_FLOOR = 0.85

# The copy sleeve's OWN circuit breaker (owner directive 2026-08-05:
# separate the breakers — an engine-side loss day must not pause
# profitable copying). Trips on the kalshi_copy cohort's rolling-24h
# realized loss; the sizing mirrors the engine's shape at copy scale
# a fixed floor, env-tunable. 24h halt (not the engine's
# 72h): the sleeve's edge source is the whales, re-validated daily.
# Scaled 60 -> 120 with the $5 -> $10 clip promotion (2026-08-08) to
# keep the same ~12-full-loss sensitivity — an unchanged floor would
# have halved the breaker's tolerance the same hour sizing doubled.
# Scaled 120 -> 600 with the $5/$10 -> $50/$100 clip promotion
# (2026-08-10), same rule as the 60 -> 120 scaling on 2026-08-08: the
# breaker keeps its ~12-full-loss sensitivity at the new clip size —
# an unchanged floor would have tripped on two ordinary losses and
# halted the sleeve almost daily. EDGE_KCOPY_HALT_USD still overrides.
# Scaled 600 -> 1200 later the same day when swisstony joined RN1 at
# the $100 clip: with BOTH high-volume whales at $100 the dominant clip
# doubled, and an unchanged floor would have halved tolerance to ~6
# full losses — the exact mistake the scaling rule exists to prevent.
COPY_HALT_USD_DEFAULT = 1200.0
COPY_HALT_HOURS_DEFAULT = 24.0
# Owner amnesty (latest 2026-08-14, "Unlock Kalshi trading and let's
# get trades firing again!", lifting the first-ever copy-breaker trip
# on the red Cincinnati slate; previously 2026-08-06 "Let's get Kalshi
# going now"). A halt tripped BEFORE this instant is cleared, and
# losses realized before it stop counting toward a re-trip (they
# already bought one halt; without the floor the same losses re-trip a
# fresh 24h the moment the old one clears). The floor self-expires:
# 24h past the amnesty, the rolling window no longer reaches behind
# it. Halts tripped on NEW losses after the amnesty are honored in
# full.
COPY_BREAKER_AMNESTY_TS = 1786734600.0   # 2026-08-14T19:10:00Z

# League discovery cached ACROSS sweeps (2026-08-10, reaction-time work):
# the sweep now also wakes on fresh-fill events, and re-running paginated
# REST discovery per league on every wake would multiply venue calls into
# the Cloudflare throttle that already bit once. Kalshi lists a game well
# before any copy window opens, so a 5-minute-old market list prices the
# same copies; the books quoted against it are always fetched live.
_DISCO_TTL_S = 300.0
# Keyed by adapter identity as well as league so a rebuilt adapter (or a
# test's fake) never serves another instance's market list.
_DISCO_CACHE: dict[tuple[int, str], tuple[float, dict]] = {}


def _discover_cached(kalshi, league: str) -> dict:
    """{market_id: VenueMarket} for a league, at most one REST discovery
    per TTL. Failures are NOT cached — the next sweep retries."""
    now = time.time()
    key = (id(kalshi), league)
    hit = _DISCO_CACHE.get(key)
    if hit is not None and now - hit[0] < _DISCO_TTL_S:
        return hit[1]
    markets: dict = {}
    for vm in kalshi.discover_markets({league}):
        markets[vm.market_id] = vm
    _DISCO_CACHE[key] = (now, markets)
    return markets


def check_copy_breaker(ledger) -> str | None:
    """Trip/report the copy sleeve's own daily-loss halt. Returns the
    block reason or None. Same no-override contract as the engine's."""
    import os

    now = time.time()
    halt = ledger.get_state("copy_halt_until")
    if halt and float(halt.get("until", 0)) > now:
        if float(halt.get("tripped_at", 0)) < COPY_BREAKER_AMNESTY_TS:
            ledger.set_state("copy_halt_until", {
                "until": 0, "reason": "owner_amnesty_2026-08-06",
                "cleared_at": now})
            log.warning("COPY BREAKER: pre-amnesty halt cleared "
                        "(owner directive 2026-08-06)")
        else:
            return "copy_halted"
    halt_usd = float(os.environ.get("EDGE_KCOPY_HALT_USD",
                                    COPY_HALT_USD_DEFAULT))
    fn = getattr(ledger, "realized_pnl_since_for_category", None)
    if fn is None or halt_usd <= 0:
        return None
    day_pnl = float(fn(max(now - 86_400, COPY_BREAKER_AMNESTY_TS),
                       "kalshi_copy"))
    if day_pnl <= -halt_usd:
        hours = float(os.environ.get("EDGE_KCOPY_HALT_H",
                                     COPY_HALT_HOURS_DEFAULT))
        ledger.set_state("copy_halt_until", {
            "until": now + hours * 3600,
            "reason": "copy_daily_loss_circuit_breaker",
            "day_pnl": round(day_pnl, 2), "tripped_at": now})
        log.error("COPY CIRCUIT BREAKER: kalshi_copy day PnL %.2f <= "
                  "-%.2f — halting copies %sh", day_pnl, halt_usd, hours)
        return "copy_halted"
    return None


def _limit_for(his_price: float) -> float:
    """SAME-OR-BETTER (owner order 2026-08-12, superseding the his+2%
    tolerance of 2026-08-04): a copy may only cost his price or less,
    fee included — the effective taker cost (ask + fee) is compared
    against HIS price floored to the venue tick. A book that ran past
    him is a skipped copy, never a chased one."""
    import math

    return min(math.floor(round(his_price * 100, 6)) / 100.0, 0.99)


def _pmus_ask(pmus, slug: str, outcome: str) -> float | None:
    """Best PMUS ask for the whale's side, stream-cache-only. Best effort.

    The whale slug names the game's team codes in away-home order
    ("mlb-bal-tex-2026-08-04"); PMUS's per-team moneyline slug is the
    same tokens under the atc grammar with the held side appended
    ("atc-mlb-bal-tex-2026-08-04-bal"). Resolve his side by scoring the
    two codes against his outcome name (code_score, two-candidate
    discrimination with a margin — an inverted side is a bet on the
    OPPOSITE outcome, so ambiguity fails closed) and peek that slug.

    peek_book is the arbitrage watcher's cache-only read: a miss returns
    None (and arms the stream for next sweep) — never a REST call, never
    a mapper pass. Any None here means "no PMUS price visible cheaply",
    and the caller falls through to Kalshi as before.
    """
    peek = getattr(pmus, "peek_book", None)
    if peek is None:
        return None
    from edge.venues.pmus_slug import code_score

    m = _DATE_RE.search(slug or "")
    if not m:
        return None
    head = [t for t in (slug or "")[: m.start()].strip("-").split("-") if t]
    if len(head) != 3:      # league + exactly two team codes, or give up
        return None
    league, codes = head[0], head[1:]
    scores = sorted(((code_score(c, outcome), c) for c in codes),
                    reverse=True)
    if scores[0][0] < 0.8 or scores[0][0] - scores[1][0] < 0.1:
        return None
    side = scores[0][1]
    book = peek(f"atc-{league}-{codes[0]}-{codes[1]}-{m.group(0)}-{side}")
    if book is None or not book.asks or book.asks[0].size < 1:
        return None
    return book.asks[0].price


# Venue portfolio reads are two REST calls — cache briefly. Keyed by
# adapter id like _DISCO_CACHE so tests with fresh fakes never share.
_VENUE_GUARD_CACHE: dict = {}
_VENUE_GUARD_TTL_S = 60.0
# Once per PROCESS: cancel resting BUY orders the venue holds that this
# boot's ledger has no context for — they are pre-deploy orphans whose
# fills would land as unattributed, unclaimed positions.
_ORPHANS_RECONCILED: dict = {"done": False}


def _venue_guard(kalshi) -> dict | None:
    key = id(kalshi)
    hit = _VENUE_GUARD_CACHE.get(key)
    if hit and time.time() - hit["at"] < _VENUE_GUARD_TTL_S:
        return hit["map"]
    m = kalshi.open_ticker_map()
    if m is not None:
        _VENUE_GUARD_CACHE[key] = {"at": time.time(), "map": m}
    return m


def _reconcile_orphan_rests(kalshi, ledger, guard: dict, stats: dict) -> None:
    if _ORPHANS_RECONCILED["done"]:
        return
    _ORPHANS_RECONCILED["done"] = True
    for _ticker, oids in (guard.get("resting_buys") or {}).items():
        for oid in oids:
            if ledger.get_state(f"kalshi_order:{oid}"):
                continue          # this boot placed it — legitimate
            try:
                kalshi.cancel_order(oid)
                stats["orphan_rests_cancelled"] = \
                    stats.get("orphan_rests_cancelled", 0) + 1
            except Exception:  # noqa: BLE001 — venue TTL reaps it anyway
                stats["orphan_cancel_fail"] = \
                    stats.get("orphan_cancel_fail", 0) + 1


def sweep(*, kalshi, ledger, identities: list[dict], live: bool,
          day_usd: float = 200.0, max_age_s: float | None = None,
          pmus=None, on_copied=None) -> dict:
    """One pass: whale open positions -> Kalshi orders where listed."""
    from edge.shadow.kalshi_guard import (cross_side_cap, game_of,
                                          note_fill, open_kalshi_sides)
    from edge.shadow.whale_align import game_key
    from edge.venues.kalshi import _series_map
    from edge.venues.mapper import team_score

    stats = {"whale_positions": 0, "league_listed": 0, "matched": 0,
             "priced_in_tolerance": 0, "copied": 0, "spent": 0.0,
             "skipped_claimed": 0, "best_ask_gap_c": -99.0}
    # NOTE: an empty identities list does NOT return early — the maker
    # fill-event queue below must drain even when the whale feed is
    # momentarily empty, or async fills would wait for whale activity
    # to be bookkept.
    if live:
        from edge.shadow.kalshi_guard import live_blocked

        # Copy scope: the sleeve rides its OWN breaker, not the engine's
        # (owner directive 2026-08-05). Kill switch/watchdog stay global.
        # check_copy_breaker runs FIRST — it owns the halt state (amnesty
        # clearing included); live_blocked would report a stale halt the
        # breaker check is about to clear.
        blocked = check_copy_breaker(ledger) or \
            live_blocked(ledger, scope="copy")
        if blocked:
            stats["blocked"] = blocked
            # The scorecard rides even (ESPECIALLY) through a halt: the
            # first tripped breaker (2026-08-14) hid the very W/L line
            # that explained the halt, because the early return skipped
            # the day-counter block below.
            _sc = getattr(ledger, "day_scorecard_for_category", None)
            if _sc is not None:
                try:
                    stats["graded_24h"] = _sc(time.time() - 86_400,
                                              "kalshi_copy")
                except Exception:  # noqa: BLE001
                    pass
            return stats
    # VENUE-SIDE guard (incident 2026-08-11: the ledger below lives in a
    # build-dir sqlite that every deploy wipes, while the venue keeps
    # our positions and 15-minute resting buys alive — three same-day
    # deploys re-copied the same dog to $606 against a $100 clip). The
    # venue's portfolio is the one memory a deploy cannot erase, so
    # LIVE buys additionally check it, and if it cannot be read this
    # sweep places NO buys at all: fail closed, never amnesiac.
    venue_guard = None
    if live and hasattr(kalshi, "open_ticker_map"):
        venue_guard = _venue_guard(kalshi)
        if venue_guard is None:
            stats["venue_guard_unavailable"] = 1
        else:
            _reconcile_orphan_rests(kalshi, ledger, venue_guard, stats)
    sides = open_kalshi_sides(ledger)
    day = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    spend = ledger.get_state("kcopy_day") or {}
    spent = float(spend.get("spent", 0.0)) if spend.get("day") == day else 0.0
    # Day-cumulative funnel counters. The per-cycle stats reset every
    # sweep, so a probe that lands on a quiet cycle reads all zeros and
    # cannot say whether the EVENING went quiet or broke — these persist
    # with the day spend and ride the heartbeat.
    _dc = ({k: int(spend.get(k, 0))
            for k in ("rested", "maker_fills", "taker_fills")}
           if spend.get("day") == day
           else {"rested": 0, "maker_fills": 0, "taker_fills": 0})
    # Fee counter (owner audit 2026-08-12: fees were the one execution
    # cost with zero telemetry — taker fees are paid on 100% of Kalshi
    # copy fills but appeared in no P&L or heartbeat view).
    _dc["fees"] = (round(float(spend.get("fees", 0.0)), 4)
                   if spend.get("day") == day else 0.0)
    # Rolling-24h graded scorecard (owner question 2026-08-14: "lost
    # almost every tennis trade today on Kalshi" was only answerable
    # from lifetime numbers). Same window and category attribution as
    # the copy circuit breaker above it.
    _sc = getattr(ledger, "day_scorecard_for_category", None)
    if _sc is not None:
        try:
            _dc["graded_24h"] = _sc(time.time() - 86_400, "kalshi_copy")
        except Exception:  # noqa: BLE001 — telemetry never blocks copies
            pass
    # Per-whale day fill counts: the inverse volume<->size scaler's
    # denominator (owner order 2026-08-12: 10x fills -> 1/10 size).
    # KNOWN LIMIT (review 2026-08-12, corrected): this ledger is
    # build-dir sqlite, so a BUILD deploy resets the counts and the
    # clip returns to the base for the rest of the venue-day — never
    # ABOVE base, so it fails toward pre-scaler sizing. NOTE the
    # day-dollar budget does NOT backstop RN1 (he is budget-exempt by
    # owner directive) — for him the reset re-arms full clips until
    # the count rebuilds, so a reset must be VISIBLE, not silent: the
    # day_state_reset counter below rides the heartbeat whenever
    # today's state is missing mid-day.
    _nw: dict = (dict(spend.get("nw") or {})
                 if spend.get("day") == day else {})
    _dc["nw"] = _nw
    if spend and spend.get("day") != day:
        pass                              # ordinary midnight rollover
    elif not spend and datetime.now(tz=timezone.utc).hour >= 1:
        # No state at all after 01:00Z = a deploy wiped the ledger
        # mid-day (midnight boots legitimately start empty).
        _dc["day_state_reset"] = 1
        log.warning("kcopy day state EMPTY mid-day — ledger wiped by a "
                    "deploy; per-whale fill counts rebuilding from 0 "
                    "(clips at base until they recover)")

    def _scaled_per(base: float, whale: str) -> float:
        w = (whale or "").lower()
        baseline = KALSHI_BASELINE_FILLS.get(w, KALSHI_BASELINE_DEFAULT)
        n = int(_nw.get(w, 0))
        if n <= baseline:
            return base
        return max(5.0, round(base * baseline / n, 2))
    # RN1 is EXEMPT from the day budget (owner directive 2026-08-10:
    # "remove day budget for RN1 on Kalshi"). His spend is tracked so it
    # can be EXCLUDED from the cap arithmetic — an uncapped whale must
    # not eat the budget the capped whales are governed by. Account
    # cash and the copy circuit breaker remain RN1's only brakes.
    rn1_spent = (float(spend.get("rn1_spent", 0.0))
                 if spend.get("day") == day else 0.0)

    def _uncapped(whale: str) -> bool:
        return (whale or "").lower() == "rn1"

    def _save_day() -> None:
        ledger.set_state("kcopy_day",
                         {"day": day, "spent": round(spent, 2),
                          "rn1_spent": round(rn1_spent, 2), **_dc})

    # Maker fills land ASYNCHRONOUSLY (sync_kalshi_fills records the fill
    # and enqueues an event); the sweep drains the queue first so claims,
    # day spend, cross-side bookkeeping and the platform claim-back stay
    # in copy-sleeve hands and each fill is bookkept exactly once.
    q = ledger.get_state("kcopy_fill_events") or {}
    for ev in (q.get("events") or []):
        qty = float(ev.get("qty") or 0)
        px = float(ev.get("price") or 0)
        cost = round(qty * px, 2)
        spent += cost
        if _uncapped(str(ev.get("whale") or "")):
            rn1_spent += cost
        stats["spent"] = round(stats["spent"] + cost, 2)
        stats["copied"] += 1
        stats["copied_maker"] = stats.get("copied_maker", 0) + 1
        _dc["maker_fills"] += 1
        # Maker fills count toward the inverse-scaling denominator too
        # (review 2026-08-12): every fill is a fill, whichever side of
        # the book it landed on — the 10x->1/10 envelope must see all.
        _mw = str(ev.get("whale") or "").lower()
        _nw[_mw] = int(_nw.get(_mw, 0)) + 1
        note_fill(sides, ev.get("ticker") or "", px, int(qty))
        if on_copied is not None:
            try:
                on_copied(str(ev.get("asset") or ""),
                          ev.get("ticker") or "", ev.get("whale") or "")
            except Exception:  # noqa: BLE001
                stats["claim_post_fail"] = \
                    stats.get("claim_post_fail", 0) + 1
    if q.get("events"):
        _save_day()
        ledger.set_state("kcopy_fill_events", {"events": []})

    series = _series_map()
    discovered: dict = {}      # league -> {game_key: VenueMarket}
    if max_age_s is None:
        import os
        max_age_s = float(os.environ.get("EDGE_KCOPY_MAX_AGE_S",
                                         MAX_AGE_S_DEFAULT))
    now = time.time()
    for row in identities:
        stats["whale_positions"] += 1
        if row.get("pmus_copied"):
            # The fast PMUS leg (chain listener, seconds of latency)
            # already holds a filled copy of this position. One copy per
            # whale position ACROSS venues (owner directive 2026-08-05)
            # — Kalshi never duplicates a filled copy.
            stats["skipped_already_copied"] = \
                stats.get("skipped_already_copied", 0) + 1
            continue
        slug = row.get("slug") or ""
        outcome = (row.get("outcome") or "").strip()
        his_price = float(row.get("price") or 0)
        if not slug or not outcome or not (0 < his_price < 1):
            continue
        # Cell-level copy policy (owner directive 2026-08-06): each whale
        # is copied ONLY in its statistically proven sport x market-type
        # x entry-band cells — same policy module as the PMUS executor.
        # Fails closed on anything unrecognized.
        from edge.shadow.copy_sports import copy_allowed, kalshi_min_ask
        if not copy_allowed(row.get("whale") or "", slug, price=his_price):
            stats["skipped_sport"] = stats.get("skipped_sport", 0) + 1
            continue
        # FRESH entries only. Identities lacking a timestamp are treated
        # as stale, never grandfathered — an unknown age is not evidence
        # of freshness.
        entered = float(row.get("entered_ts") or 0)
        if not entered or now - entered > max_age_s:
            stats["skipped_stale"] = stats.get("skipped_stale", 0) + 1
            continue
        league = slug.split("-", 1)[0].lower()
        if league not in series:
            continue
        stats["league_listed"] += 1
        gkey = game_key(slug)
        if gkey is None:
            # Silent exits between league_listed and matched hid a
            # 4-hour no-fire window (owner report 2026-08-15) — every
            # refusal in this stretch is counted from here on.
            stats["skipped_no_gkey"] = stats.get("skipped_no_gkey", 0) + 1
            continue
        if league not in discovered:
            try:
                # Kalshi event tickers carry no slug-style date; key by
                # the market's own outcome names + the event date token
                # is already enforced in the adds sweep. Here the join
                # is name-first: index every market by its outcome set.
                discovered[league] = _discover_cached(kalshi, league)
            except Exception as e:  # noqa: BLE001
                discovered[league] = {}
                stats["disco_err"] = stats.get("disco_err", 0) + 1
                stats["disco_err_ex"] = f"{league}: {type(e).__name__}"
            # Per-league market counts answer "is discovery returning
            # anything" without a log line per market.
            dc = stats.setdefault("disco_counts", {})
            dc[league] = len(discovered[league])
        target_ticker = None
        for vm in discovered[league].values():
            names = list(vm.outcome_tokens)
            if len(names) != 2:
                continue
            # His side must hit one outcome at the mapper bar AND the
            # OTHER outcome must belong to the same matchup we hold —
            # both names scoring against the whale slug's game tokens
            # keeps "same player, different match" out.
            hit = None
            for name in names:
                if team_score(name, outcome) >= 0.95:
                    hit = name
                    break
            if hit is None:
                continue
            other = [n for n in names if n != hit][0]
            toks = [t for t in gkey.split("|")[0].split("-") if t]
            other_ok = any(team_score(other, t) >= 0.6 for t in toks) \
                or any(t.lower() in other.lower() for t in toks)
            if not other_ok:
                continue
            target_ticker = vm.outcome_tokens[hit]
            break
        if target_ticker is None:
            stats["skipped_unmapped"] = stats.get("skipped_unmapped", 0) + 1
            ex = stats.setdefault("unmapped_ex", [])
            if len(ex) < 6:
                ex.append(f"{league}:{slug}:{str(outcome)[:32]}")
            continue
        stats["matched"] += 1
        claim = f"kcopy:{slug}:{outcome[:24]}"
        if ledger.get_state(claim):
            stats["skipped_claimed"] += 1
            continue
        # NEVER-ADD, VENUE-SIDE (2026-08-10 evening: a ~$204 challenger
        # position = two ~$100 copies of the same dog reached the
        # account through two different PM-side identities — same
        # outcome, two asset ids, two distinct kcopy claims. The claim
        # keys on the PM slug, so nothing deduped the venue TICKER).
        # Any open position on this ticker refuses a second buy, no
        # matter which identity proposed it; cross_side_cap only guards
        # the OPPOSITE side and calls a same-ticker add 'directional'.
        if any(s.get("ticker") == target_ticker
               for s in (sides.get(game_of(target_ticker)) or [])):
            stats["skipped_held_ticker"] = \
                stats.get("skipped_held_ticker", 0) + 1
            continue
        # ...and the VENUE's own answer, which survives deploys
        # (incident 2026-08-11): EVENT-level, not just ticker-level.
        # The evening's guaranteed-loss pairs (owner screenshot: Halys
        # YES + Kwon YES summing over $1/pair) formed because the
        # cross-side pair math below reads the SAME wipeable local
        # ledger the never-add did — post-deploy it saw no opposite
        # side and skipped the pair constraint entirely. Owner
        # directive: copies NEVER take an offsetting position; locked
        # pairs belong to the arb sleeve alone. So any venue-held
        # position or resting buy anywhere on this EVENT refuses the
        # copy, and an unreadable venue refuses ALL buys.
        if live and hasattr(kalshi, "open_ticker_map"):
            if venue_guard is None:
                stats["skipped_no_guard"] = \
                    stats.get("skipped_no_guard", 0) + 1
                continue
            held_any = (set(venue_guard["positions"])
                        | set(venue_guard["resting_buys"]))
            tgt_event = game_of(target_ticker)
            if any(game_of(t) == tgt_event for t in held_any):
                stats["skipped_held_event"] = \
                    stats.get("skipped_held_event", 0) + 1
                continue
        book = kalshi.get_book(target_ticker, target_ticker)
        if book is None or not book.asks or book.asks[0].size < 1:
            stats["skipped_no_book"] = stats.get("skipped_no_book", 0) + 1
            continue
        ask = book.asks[0].price
        # Fee-viability carve-out: thin-edge cells (donor ROI ~2-3.5%)
        # cannot pay Kalshi's mid-price taker fee (peaks 1.75% at 50c);
        # they route here only at >=70c where the fee is <=1.47%.
        floor = kalshi_min_ask(row.get("whale") or "", slug)
        if floor and ask < floor:
            stats["skipped_fee_floor"] = stats.get("skipped_fee_floor", 0) + 1
            continue
        limit = _limit_for(his_price)
        # Fee-loaded: we cross as taker, so the true cost is ask + fee.
        # Comparing the raw ask overpaid by up to 1.75c/contract against
        # a ~2.3c gross copy edge (audit 2026-08-04) — the sleeve was
        # structurally negative-EV at mid prices without this.
        eff = ask + kalshi.taker_fee(ask)
        stats["best_ask_gap_c"] = max(stats["best_ask_gap_c"],
                                      round((limit - eff) * 100, 2))
        if eff > limit:
            # Taker cannot pay the fee inside his+2% — at mid prices the
            # 1.75c fee consumed the whole tolerance and priced_in_tolerance
            # sat at 0 cycle after cycle (owner: "more fires on Kalshi",
            # 2026-08-08). MAKER pays NO fee: rest a bid at
            # min(his+2%, ask - tick) under the venue's own 15-minute
            # expiry and let the queue come to us. Restricted to
            # KALSHI-FIRST assets (structurally ours — the PMUS executor
            # defers them) young enough that the resting window ends
            # before the PMUS sweep's one-hour reclaim can overlap it:
            # no cross-venue double-copy is possible by construction.
            # TAKER-ONLY DEFAULT (owner audit 2026-08-12): the maker
            # experiment finished 0-for-55 on its final day and 0
            # maker fills in every hourly snapshot — rests never
            # fill; they park capital and sweep attention. The rest
            # path sits behind EDGE_KCOPY_MAKER=1 for the day it
            # earns a retry; fee-out-of-tolerance copies are skipped
            # and counted instead of rested.
            import os as _os2
            if _os2.environ.get("EDGE_KCOPY_MAKER", "0") != "1":
                stats["skipped_fee_out"] = \
                    stats.get("skipped_fee_out", 0) + 1
                continue
            from edge.shadow.copy_sports import kalshi_first as _kf
            young = (now - entered) < (max_age_s - 900)
            if (live and _kf(str(row.get("asset") or "")) and young
                    and ask >= his_price * COLLAPSE_FLOOR):
                rest_key = f"kcopy_rest:{claim}"
                rest = ledger.get_state(rest_key) or {}
                if float(rest.get("until", 0)) >= now:
                    # REPRICE (owner 2026-08-10, profitability upgrade
                    # #4): a resting bid the book has moved away from is
                    # queue theater — if our stored price is no longer
                    # within 2c of the current ask, cancel and requote
                    # at the fresh book. Repost ONLY on a confirmed
                    # cancel: 'gone' means the order may have FILLED
                    # (the fill sync will claim it — a repost would
                    # double the copy) and 'error' means it may still
                    # be live (a repost would stack).
                    stale_px = (rest.get("px") is not None
                                and round(ask - float(rest["px"]), 4)
                                > 0.02)
                    if stale_px and rest.get("order_id"):
                        cx = getattr(kalshi, "cancel_order", None)
                        res = cx(rest["order_id"]) if cx else "error"
                        stats[f"reprice_{res}"] = \
                            stats.get(f"reprice_{res}", 0) + 1
                        ledger.set_state(rest_key, {})
                        if res != "cancelled":
                            continue
                        # fall through: requote at the fresh book below
                    else:
                        stats["maker_resting"] = \
                            stats.get("maker_resting", 0) + 1
                        continue
                per_m = _scaled_per(
                    _per_copy_usd(row.get("whale") or "", slug),
                    row.get("whale") or "")
                if (day_usd > 0 and not _uncapped(row.get("whale") or "")
                        and (spent - rn1_spent) + per_m > day_usd):
                    continue
                plan = kalshi.plan_maker_order(limit, ask, 0.0, 1.0)
                # (edge 0 < threshold 1: plan can only return a maker rest)
                if plan is None or plan[1]:
                    continue
                mpx = plan[0]
                # COMPETITIVE rests only (owner 2026-08-09: "I need there
                # to be volume on Kalshi"; day counters ran 38 rests /
                # 0 fills): a bid more than 2c under the ask is queue
                # theater — it expires unfilled every 15 minutes and
                # burns the sweep's attention. If our his+2% limit can't
                # sit within 2c of the ask, there is no realistic fill
                # at our price; skip and let the next sweep's book
                # decide (the claim is not burned).
                if round(ask - mpx, 4) > 0.02:
                    stats["skipped_dead_rest"] = \
                        stats.get("skipped_dead_rest", 0) + 1
                    continue
                mcount = int(per_m / mpx)
                if mcount >= 1:
                    mcount = cross_side_cap(sides, target_ticker, mpx,
                                            mcount, fee_per_contract=0.0)
                if mcount < 1:
                    continue
                rr = kalshi.place_order(target_ticker, mpx, mcount,
                                        client_order_id=str(uuid.uuid4()),
                                        taker=False)
                if rr.get("ok") and rr.get("order_id"):
                    # Context parked for sync_kalshi_fills: an async fill
                    # becomes a claimed, categorized copy; the next sweep
                    # drains the event queue for spend/claim-back.
                    ledger.set_state(
                        f"kalshi_order:{rr['order_id']}",
                        {"market_key": f"kalshi:{target_ticker}",
                         "league": league, "category": "kalshi_copy",
                         "kalshi_copy": True, "maker": True,
                         "kcopy_claim": claim,
                         "asset": str(row.get("asset") or ""),
                         "whale": row.get("whale") or "",
                         "his_price": his_price, "limit": limit,
                         "pm_slug": slug, "outcome": outcome})
                    ledger.set_state(rest_key, {"until": now + 900,
                                                "order_id": rr["order_id"],
                                                "px": mpx})
                    if venue_guard is not None:
                        venue_guard["resting_buys"].setdefault(
                            target_ticker, []).append(rr["order_id"])
                    stats["maker_rested"] = \
                        stats.get("maker_rested", 0) + 1
                    _dc["rested"] += 1
                    _save_day()
                    log.warning("KALSHI MAKER REST %s (%s): %s x%d @ %.2f "
                                "(his %.3f, ask %.2f)", outcome,
                                row.get("whale"), target_ticker, mcount,
                                mpx, his_price, ask)
            continue        # outside his price +2% fee-loaded: no taker copy
        if ask < his_price * COLLAPSE_FLOOR:
            # Far below his entry = the market learned something he
            # didn't know. That is not a discount on his edge.
            stats["skipped_collapsed"] = stats.get("skipped_collapsed",
                                                   0) + 1
            continue
        stats["priced_in_tolerance"] += 1
        import os as _os
        prefer_kalshi = _os.environ.get("EDGE_KCOPY_PREFER_KALSHI",
                                        "0") == "1"
        if pmus is not None and not prefer_kalshi:
            # Best-venue-at-placement, EVERY copy (owner directive
            # 2026-08-10 night: "copies firing on both venues, best
            # price"). If PMUS visibly shows a better ask than Kalshi's
            # fee-loaded effective price, the copy defers and the
            # 2-minute PMUS reclaim sweep takes it at the better venue;
            # no visible PMUS book means Kalshi keeps it. This peek used
            # to skip Kalshi-first assets (2026-08-09 "volume on Kalshi"
            # default) — now it prices them too. The one-copy rule is
            # untouched (pmus_copied skip, claim-back, never-add veto).
            # EDGE_KCOPY_PREFER_KALSHI=1 restores keep-when-qualifying.
            pask = _pmus_ask(pmus, slug, outcome)
            if pask is not None and pask < eff:
                stats["routed_pmus_better"] = \
                    stats.get("routed_pmus_better", 0) + 1
                continue
        per = _scaled_per(_per_copy_usd(row.get("whale") or "", slug),
                          row.get("whale") or "")
        # EDGE_KCOPY_DAY_USD <= 0 = NO day cap (owner 2026-08-10: "I dont
        # want trades missing if they are being placed by our copied and
        # tested accounts"). The copy circuit breaker and account cash
        # remain the loss brakes.
        if (day_usd > 0 and not _uncapped(row.get("whale") or "")
                and (spent - rn1_spent) + per > day_usd):
            stats["skipped_day_cap"] = stats.get("skipped_day_cap", 0) + 1
            continue
        count = int(per / ask)
        if count < 1:
            continue
        # LIQUIDITY FLOOR (owner approval 2026-08-11): only copy when
        # the top of book shows at least HALF our size. The overnight
        # bleed came from thin 3am books (table tennis, low-tier
        # challengers) where our taker order WAS the market — a whale's
        # entry edge does not survive us moving the price to buy it.
        if book.asks[0].size < count / 2:
            stats["skipped_thin_book"] = \
                stats.get("skipped_thin_book", 0) + 1
            continue
        # Same-event guard: never build both sides of one market unless
        # the completed pair locks profit, and then only pair-matched.
        capped = cross_side_cap(sides, target_ticker, ask, count,
                                fee_per_contract=kalshi.taker_fee(ask))
        if capped < 1:
            stats["skipped_cross_side"] = stats.get("skipped_cross_side",
                                                    0) + 1
            continue
        count = capped
        if not live:
            stats["copied"] += 1     # dry-run telemetry
            continue
        r = kalshi.place_order(target_ticker, ask, count,
                               client_order_id=str(uuid.uuid4()), taker=True)
        filled = int(float(r.get("count") or 0)) if r.get("ok") else 0
        if r.get("ok") and filled > 0:
            ledger.set_state(claim, {"ts": time.time(),
                                     "status": r.get("status"),
                                     "filled": filled})
        elif r.get("ok"):
            # Accepted IOC, zero filled: the copy's once-ever claim must
            # not be burned on a fill that never happened — retry next
            # sweep at whatever the book says then.
            stats["ioc_zero_fill"] = stats.get("ioc_zero_fill", 0) + 1
        else:
            stats["last_order_error"] = {
                "ticker": target_ticker,
                "status": str(r.get("status"))[:200],
                "raw": str((r.get("raw") or {}).get("error"))[:300]}
        if filled > 0:
            if r.get("order_id"):
                ledger.set_state(f"kalshi_inline:{r['order_id']}",
                                 {"ts": time.time()})
            note_fill(sides, target_ticker, ask, filled)
            if venue_guard is not None:
                # The cached venue view is up to 60s stale — teach it our
                # own fill so a second identity THIS sweep sees it held.
                venue_guard["positions"].add(target_ticker)
            stats["copied"] += 1
            # Claim the position back to the platform so the PMUS paths
            # never buy it a second time (one copy ACROSS venues). Best
            # effort — reporting must never block trading; a failed post
            # is counted and the platform's live_orders guard still
            # protects the common PM-copied-first direction.
            if on_copied is not None:
                try:
                    on_copied(str(row.get("asset") or ""), target_ticker,
                              row.get("whale") or "")
                except Exception:  # noqa: BLE001
                    stats["claim_post_fail"] = \
                        stats.get("claim_post_fail", 0) + 1
            cost = round(filled * ask, 2)
            spent += cost
            if _uncapped(row.get("whale") or ""):
                rn1_spent += cost
            stats["spent"] = round(stats["spent"] + cost, 2)
            _dc["taker_fills"] += 1
            _dc["fees"] = round(
                _dc.get("fees", 0.0) + kalshi.taker_fee(ask) * filled, 4)
            _w = (row.get("whale") or "").lower()
            _nw[_w] = int(_nw.get(_w, 0)) + 1
            _save_day()
            # The smoke probe's whole job is "prove the live order
            # path works"; a real copy fill IS that proof. Refresh the
            # state the heartbeat serves so an 8-day-old delisted-
            # ticker failure (http_410, 2026-08-04) stops masquerading
            # as the path's current health (owner audit 2026-08-12).
            try:
                ledger.set_state("kalshi_smoke_last", {
                    "ts": time.time(), "ticker": target_ticker,
                    "price": ask, "ok": True, "filled": int(filled),
                    "status": "proven_by_copy_fill"})
            except Exception:  # noqa: BLE001 — telemetry only
                pass
            ledger.record_fill(
                fill_uid=f"kcopy-{claim}-{int(time.time())}",
                venue="kalshi", market_key=f"kalshi:{target_ticker}",
                side="BUY", qty=float(filled), price=ask,
                fee=round(kalshi.taker_fee(ask) * filled, 4),
                league=league, mode="LIVE_BETA", category="kalshi_copy",
                decision={"kalshi_copy": True, "whale": row.get("whale"),
                          "his_price": his_price, "limit": limit,
                          "ask": ask, "pm_slug": slug,
                          "outcome": outcome})
            log.warning("KALSHI COPY %s (%s): %s x%d @ %.2f (his %.3f)",
                        outcome, row.get("whale"), target_ticker, filled,
                        ask, his_price)
    stats["day"] = {"spent": round(spent, 2), **_dc}
    return stats

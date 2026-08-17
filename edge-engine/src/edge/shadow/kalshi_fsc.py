"""Kalshi FIRST-SET COMEBACK sleeve (owner order 2026-08-17 evening).

"For all tennis: ATP, WTA, and ITF (identify favorites before the match
begins). Monitor the match and any favorite that loses the first set
automatically place a $100 trade on Kalshi on that player. Let the trade
play out until settlement."

Isolated by construction: its own claims (fsc:<event>), its own grading
category (kalshi_fsc), its own kill switch, day cap and thread. It never
touches the copy sleeves' state and nothing here runs on Polymarket.

FAVORITE IDENTIFICATION (owner 2026-08-17: "use the odds verification
... they have to be a pre-match favorite"): where the licensed odds
feed covers the pairing (ATP/WTA main tours, fsc_scores.poll_prematch)
the sportsbook-consensus pre-match favorite is AUTHORITATIVE — frozen
at the last pre-commence observation — and Kalshi's book is only a
pricing venue. Uncovered tiers (ITF/challenger) fall back to the
price-inferred snapshot: the favorite is the side whose two-sided MID
sits inside [FAV_MIN, FAV_MAX] on MATCH DAY, uncontradicted by the
opponent's book.

SET-1 DETECTION: "lost the first set" is either a set-score fact from
fsc_scores (enter immediately) or the favorite's market trading down
into [TRIG_LO, TRIG_HI], having dropped >= MIN_DROP from the snapshot
— no earlier than commence + START_MIN_S when the feed knows the start
time, else >= ARMED_AGE_S after arming — CONFIRMED by a second sweep
still in the band >= CONFIRM_MIN_S later.
The match-day gate keeps a days-ahead listing from arming (Kalshi lists
tennis well in advance, and a pre-match news drop on Tuesday must not
read as a Thursday set loss); the confirmation sweep refuses one-print
blips and collapses moving too fast to hold the band across two sweeps;
below TRIG_LO is worse news than one set and is skipped. Every knob is
env-tunable; a scores feed can replace the trigger later and nothing
else changes.

KNOWN RESIDUALS of price-only inference, accepted for v1 (each bounded
at PER_ENTRY_USD and stamped into the fill's decision record): a
same-day pre-match collapse that parks in the band for two sweeps can
buy before first serve; a SLOW in-match collapse (medical timeout,
creeping retirement risk) that sits in the band across two sweeps reads
as a set loss; and a deploy landing mid-match can snapshot the current
leader as "favorite".

Entries are IOC taker buys of PER_ENTRY_USD at the live ask, whole
contracts, one per match EVER, held to settlement (no exit logic).
A thin book takes a PARTIAL first fill, then later sweeps TOP UP the
position toward the full PER_ENTRY_USD while TOPUP_WINDOW_S is open
(owner: fills "need to be close to $100 not $37").
"""

from __future__ import annotations

import logging
import os
import time
import uuid

log = logging.getLogger(__name__)

# The venue series map's tennis keys. "itf" overlaps atp/wta on the
# challenger series but uniquely carries KXITFMATCH/KXITFWMATCH (there
# is no "ch" key); the per-sweep seen-set below absorbs the overlap.
LEAGUES = ("atp", "wta", "itf")

PER_ENTRY_USD = float(os.environ.get("EDGE_FSC_USD", "100"))
FAV_MIN = float(os.environ.get("EDGE_FSC_FAV_MIN", "0.55"))
FAV_MAX = float(os.environ.get("EDGE_FSC_FAV_MAX", "0.90"))
TRIG_LO = float(os.environ.get("EDGE_FSC_TRIG_LO", "0.20"))
# 0.50, not 0.45: a 0.80-0.90 favorite who drops the first set reprices
# to ~0.50-0.65, and 0.45 would have excluded that whole cohort from
# "any favorite that loses the first set".
TRIG_HI = float(os.environ.get("EDGE_FSC_TRIG_HI", "0.50"))
MIN_DROP = float(os.environ.get("EDGE_FSC_MIN_DROP", "0.12"))
# Minimum seconds between the snapshot first qualifying and any trigger:
# the proxy for "a first set has actually been contested".
ARMED_AGE_S = float(os.environ.get("EDGE_FSC_ARMED_AGE_S", "1500"))
# The trigger must hold across two sweeps this far apart before money
# moves — one book print is a blip, not a set.
CONFIRM_MIN_S = float(os.environ.get("EDGE_FSC_CONFIRM_MIN_S", "60"))
CONFIRM_MAX_S = float(os.environ.get("EDGE_FSC_CONFIRM_MAX_S", "900"))
# A snapshot older than this is a different session of play (rain delay,
# next-day carryover) — re-arm rather than trigger on stale state.
SNAP_MAX_AGE_S = float(os.environ.get("EDGE_FSC_SNAP_MAX_AGE_S", "28800"))
# 2500 -> 5000 (owner 2026-08-17 late night: "never miss a favorite
# who loses the first set") — the cap is a runaway backstop, not a
# budget that should ever bind on a real slate (50 entries/day).
DAY_USD = float(os.environ.get("EDGE_FSC_DAY_USD", "5000"))
# Thin books take a PARTIAL entry down to this floor instead of
# skipping (same order): $40 on the favorite beats missing the match.
PARTIAL_MIN_USD = float(os.environ.get("EDGE_FSC_PARTIAL_MIN_USD", "25"))
# Sanity bounds for SCORE-CONFIRMED entries (fsc_scores): when the feed
# says the first set is lost as a fact, the price bands stop being the
# trigger — but an ask outside these bounds is a broken/settled book,
# not an entry.
SCORE_LO = float(os.environ.get("EDGE_FSC_SCORE_LO", "0.03"))
SCORE_HI = float(os.environ.get("EDGE_FSC_SCORE_HI", "0.85"))
# A book wider than this is not a price (TONGEE incident 2026-08-17:
# a lone resting offer on an empty ITF book armed the dog as the
# "favorite"). Both arming and triggering require bid AND ask within
# this spread; the favorite test and drop run on the MID.
SPREAD_MAX = float(os.environ.get("EDGE_FSC_SPREAD_MAX", "0.12"))
# Sportsbook-consensus favorite floor for the feed-covered path — a
# 0.51 rounding artifact is a coin flip, not a favorite.
FEED_FAV_MIN = float(os.environ.get("EDGE_FSC_FEED_FAV_MIN", "0.52"))
# Feed-covered matches anchor the price trigger to the REAL match
# start: no trigger before commence + this many seconds (a first set
# is never over sooner). Replaces the armed-age proxy, which measured
# our own clock, not the match's.
START_MIN_S = float(os.environ.get("EDGE_FSC_START_MIN_S", "1200"))
# $100 means $100 (owner 2026-08-17: fills "need to be close to $100
# not $37"): a partial IOC entry keeps buying the remainder on later
# sweeps while this window (from first fill) is open, until less than
# TOPUP_MIN_USD is left to spend.
TOPUP_WINDOW_S = float(os.environ.get("EDGE_FSC_TOPUP_WINDOW_S", "1800"))
TOPUP_MIN_USD = float(os.environ.get("EDGE_FSC_TOPUP_MIN_USD", "5"))

# Poll scores only while tennis is actually in play (last sweep saw
# match-day tickers) — credits are shared with the fair-value feed.
_ACTIVE = {"hint": True}

_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def _day_key(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now - 4 * 3600))  # ET-ish


def _date_token(now: float) -> str:
    """'26AUG17' — the date prefix Kalshi tickers embed, in ET-ish.
    Built by hand: strftime %b is locale-dependent."""
    tm = time.gmtime(now - 4 * 3600)
    return f"{tm.tm_year % 100:02d}{_MONTHS[tm.tm_mon - 1]}{tm.tm_mday:02d}"


def _match_day_ok(event: str, now: float) -> bool:
    """True when the event's embedded date is today or yesterday (ET) —
    yesterday stays eligible so a night match live past midnight is not
    abandoned mid-monitor. Future-dated listings are the important
    refusal: they cannot have played a first set."""
    return event[:7] in (_date_token(now), _date_token(now - 86400.0))


def sweep(*, kalshi, ledger, live: bool) -> dict:
    """One monitoring pass. Reuses the copy sweep's cached league
    discovery (one REST hit per league per TTL) and the venue book
    reader; everything else is FSC-private state."""
    from edge.shadow.kalshi_copies import _discover_cached
    from edge.shadow.kalshi_guard import game_of, live_blocked

    stats: dict = {"scanned": 0, "armed": 0, "entered": 0, "spent": 0.0}
    if os.environ.get("EDGE_FSC", "1") == "0":
        return {"disabled": True}
    # Account-level stops (kill switch / watchdog) bind every live
    # spender. Sleeve/strategy halts do not pause this fixed-dollar
    # experiment — see kalshi_guard.live_blocked scope="fsc".
    if live:
        blocked = live_blocked(ledger, scope="fsc")
        if blocked:
            return {"blocked": blocked}

    now = time.time()
    day = ledger.get_state("fsc_day") or {}
    if day.get("date") != _day_key(now):
        day = {"date": _day_key(now), "spent": 0.0, "entries": 0}
    spent_today = float(day.get("spent") or 0.0)

    # Venue-held events are skipped so this sleeve cannot stack exposure
    # onto (or in front of) a copy position. open_ticker_map's contract
    # is fail-CLOSED: on None (fetch failure) we keep scanning/arming
    # (stateful, order-free) but place no entries this sweep — falling
    # back to "nothing held" is exactly the amnesia the venue guard
    # exists to prevent.
    held_events: set = set()
    guard_down = False
    if live and hasattr(kalshi, "open_ticker_map"):
        vg = kalshi.open_ticker_map()
        if vg is None:
            guard_down = True
            stats["venue_guard_down"] = True
        else:
            held_events = {game_of(t) for t in
                           (set(vg["positions"]) | set(vg["resting_buys"]))}

    # Set-score ground truth (owner 2026-08-17 night: "can there be
    # verification from one of our apis"). Fail-soft: empty rows keep
    # the price-only trigger exactly as before.
    from edge.shadow import fsc_scores
    from edge.venues.mapper import team_score
    score_rows, score_stats = fsc_scores.poll(
        active_hint=bool(_ACTIVE["hint"]))
    if score_stats:
        stats["scores"] = score_stats
    # Pre-match favorites by sportsbook consensus (owner 2026-08-17:
    # "use the odds verification... they have to be a pre-match
    # favorite"). Same fail-soft contract: an empty map leaves the
    # price-only favorite test in charge.
    pm_stats = fsc_scores.poll_prematch(active_hint=bool(_ACTIVE["hint"]))
    if pm_stats:
        stats["prematch"] = pm_stats

    seen: set = set()
    for league in LEAGUES:
        try:
            markets = _discover_cached(kalshi, league)
        except Exception:  # noqa: BLE001 — discovery retries next sweep
            stats["disco_err"] = stats.get("disco_err", 0) + 1
            continue
        for vm in markets.values():
            for _outcome, ticker in (vm.outcome_tokens or {}).items():
                if ticker in seen:
                    continue    # challenger series appear in two leagues
                seen.add(ticker)
                event = game_of(ticker)
                # Match-day gate BEFORE any state or book read: Kalshi
                # lists tennis days ahead, and a future match can't have
                # played a set — skipping it here is also what keeps the
                # scan to a book read per live-day ticker.
                if not _match_day_ok(event, now):
                    stats["skipped_offday"] = \
                        stats.get("skipped_offday", 0) + 1
                    continue
                stats["scanned"] += 1
                key = f"fsc:{event}"
                st = ledger.get_state(key) or {}
                if st.get("entered"):
                    # $100 means $100 (owner 2026-08-17: fills "need to
                    # be close to $100 not $37"): an entry that IOC-
                    # filled short keeps buying the remainder while the
                    # top-up window is open. Must run BEFORE the held-
                    # events skip — our own entry makes the event
                    # venue-held.
                    if ticker != st.get("fav_ticker"):
                        continue
                    spent_st = float(
                        st.get("spent_usd")
                        or float(st.get("filled") or 0)
                        * float(st.get("entry_px") or 0))
                    remaining = round(PER_ENTRY_USD - spent_st, 2)
                    first_ts = float(st.get("ts") or 0)
                    if remaining < TOPUP_MIN_USD \
                            or now - first_ts > TOPUP_WINDOW_S:
                        continue
                    if not live:
                        continue
                    if guard_down:
                        stats["skipped_guard_down"] = \
                            stats.get("skipped_guard_down", 0) + 1
                        continue
                    book = kalshi.get_book(ticker, ticker)
                    if book is None or not book.asks or not book.bids \
                            or book.asks[0].size < 1 \
                            or book.bids[0].size < 1:
                        continue
                    ask = book.asks[0].price
                    if ask - book.bids[0].price > SPREAD_MAX:
                        continue
                    if not (SCORE_LO <= ask <= SCORE_HI):
                        continue
                    count = min(int(remaining / ask),
                                int(book.asks[0].size))
                    if count < 1:
                        continue
                    if spent_today + count * ask > DAY_USD:
                        stats["skipped_day_cap"] = \
                            stats.get("skipped_day_cap", 0) + 1
                        continue
                    r = kalshi.place_order(
                        ticker, ask, count,
                        client_order_id=str(uuid.uuid4()), taker=True)
                    filled = int(float(r.get("count") or 0)) \
                        if r.get("ok") else 0
                    if filled <= 0:
                        if r.get("ok"):
                            stats["ioc_zero_fill"] = \
                                stats.get("ioc_zero_fill", 0) + 1
                        else:
                            stats["order_err"] = \
                                stats.get("order_err", 0) + 1
                        continue
                    if r.get("order_id"):
                        ledger.set_state(
                            f"kalshi_inline:{r['order_id']}", {"ts": now})
                    cost = round(filled * ask, 2)
                    spent_st = round(spent_st + cost, 2)
                    ledger.set_state(key, {
                        **st, "spent_usd": spent_st,
                        "filled": int(st.get("filled") or 0) + filled,
                        "topups": int(st.get("topups") or 0) + 1})
                    spent_today = round(spent_today + cost, 2)
                    day = {"date": _day_key(now), "spent": spent_today,
                           "entries": int(day.get("entries") or 0)}
                    ledger.set_state("fsc_day", day)
                    stats["topup_fills"] = \
                        stats.get("topup_fills", 0) + 1
                    stats["spent"] = round(stats["spent"] + cost, 2)
                    ledger.record_fill(
                        fill_uid=f"fsc-{event}-topup-{int(now)}",
                        venue="kalshi", market_key=f"kalshi:{ticker}",
                        side="BUY", qty=float(filled), price=ask,
                        fee=round(kalshi.taker_fee(ask) * filled, 4),
                        league=league, mode="LIVE_BETA",
                        category="kalshi_fsc",
                        decision={"fsc": True, "topup": True,
                                  "spent_usd": spent_st,
                                  "entry_px": st.get("entry_px")})
                    log.warning("FSC TOPUP %s: %d @ %.2f (position now "
                                "$%.2f of $%.0f)", ticker, filled, ask,
                                spent_st, PER_ENTRY_USD)
                    continue
                if st.get("set1_won"):
                    # Score fact: the favorite WON the first set — this
                    # match can never qualify ("loses the first set"),
                    # whatever the price later does.
                    continue
                if event in held_events:
                    stats["skipped_held"] = stats.get("skipped_held", 0) + 1
                    continue
                fav_ticker = st.get("fav_ticker")
                if fav_ticker and st.get("v") != 2:
                    # Pre-fix armed state (TONGEE incident 2026-08-17
                    # evening: an ask-only snapshot on a one-sided ITF
                    # book armed the DOG as "favorite"). Never trusted:
                    # cleared and re-armed under the two-sided rules.
                    ledger.set_state(key, {})
                    stats["rearmed_v1"] = stats.get("rearmed_v1", 0) + 1
                    continue
                if fav_ticker and ticker != fav_ticker:
                    continue    # armed: only the favorite's book is read
                book = kalshi.get_book(ticker, ticker)
                if book is None or not book.asks or book.asks[0].size < 1:
                    continue
                ask = book.asks[0].price
                # A price is only a price with BOTH sides present and a
                # sane spread. On Kalshi's thin ITF boards a lone 60c
                # offer with no bid read as "favorite at 60c" while the
                # player really traded ~30c — the sleeve then bought the
                # dog's "collapse". Ask-only books neither arm nor
                # trigger anything.
                if not book.bids or book.bids[0].size < 1:
                    stats["skipped_one_sided"] = \
                        stats.get("skipped_one_sided", 0) + 1
                    continue
                bid = book.bids[0].price
                if ask - bid > SPREAD_MAX:
                    stats["skipped_wide_spread"] = \
                        stats.get("skipped_wide_spread", 0) + 1
                    continue
                mid = round((ask + bid) / 2, 4)

                if not fav_ticker:
                    # PRE-MATCH FAVORITE. Where the odds feed covers
                    # the pairing (ATP/WTA main tours), the sportsbook
                    # consensus IS the favorite oracle (owner
                    # 2026-08-17: "use the odds verification... they
                    # have to be a pre-match favorite") — Kalshi's own
                    # book is a pricing venue, never the identifier,
                    # and the feed snapshot is frozen pre-commence so
                    # in-play drift can't rewrite it. Uncovered tiers
                    # (ITF/challenger) keep the two-sided book rules.
                    other_name = next(
                        (n for n in (vm.outcome_tokens or {})
                         if n != _outcome), "")
                    feed = fsc_scores.prematch_favorite(
                        _outcome, other_name) if other_name else None
                    if feed is not None:
                        if team_score(_outcome, feed["fav_name"]) < 0.9:
                            # This side is the feed's DOG: never
                            # armable, whatever a thin Kalshi board
                            # prints (the TONGEE class of error).
                            stats["arm_feed_dog"] = \
                                stats.get("arm_feed_dog", 0) + 1
                            continue
                        if feed["prob"] < FEED_FAV_MIN:
                            stats["arm_feed_coinflip"] = \
                                stats.get("arm_feed_coinflip", 0) + 1
                            continue
                        # No FAV band on the mid here — "every single
                        # favorite" includes the 0.92 heavy the band
                        # excluded; the mid is kept only as the drop
                        # reference.
                        ledger.set_state(key, {
                            "fav_ticker": ticker, "fav_px": mid,
                            "armed_ts": now,
                            "feed_prob": feed["prob"],
                            "commence_ts": feed["commence_ts"],
                            "v": 2})
                        stats["armed"] += 1
                        stats["armed_feed"] = \
                            stats.get("armed_feed", 0) + 1
                        continue
                    # MATCH-DAY SNAPSHOT — the favorite test runs on the
                    # MID of a two-sided book, and the opponent's book
                    # must not contradict it (both mids near or above
                    # 0.5 is book noise, not a favorite).
                    if FAV_MIN <= mid <= FAV_MAX:
                        opp_ticker = next(
                            (t for t in (vm.outcome_tokens or {}).values()
                             if t != ticker), None)
                        if opp_ticker:
                            ob = kalshi.get_book(opp_ticker, opp_ticker)
                            if ob is not None and ob.asks and ob.bids:
                                omid = (ob.asks[0].price
                                        + ob.bids[0].price) / 2
                                if omid > 0.5:
                                    stats["arm_contradicted"] = \
                                        stats.get("arm_contradicted",
                                                  0) + 1
                                    continue
                        ledger.set_state(key, {
                            "fav_ticker": ticker, "fav_px": mid,
                            "armed_ts": now, "v": 2})
                        stats["armed"] += 1
                    continue

                armed_ts = float(st.get("armed_ts") or 0)
                age = now - armed_ts
                if age > SNAP_MAX_AGE_S:
                    ledger.set_state(key, {})      # stale session — re-arm
                    stats["rearmed"] = stats.get("rearmed", 0) + 1
                    continue
                fav_px = float(st.get("fav_px") or 0)

                # SCORE GROUND TRUTH (three verdicts, all fail-soft to
                # the price path when the feed has no fresh row):
                #   fav 0 sets / opp >=1  -> first set LOST, fact: enter
                #   fav >=1 / opp 0       -> first set WON: never enter
                #   0-0 (fresh)           -> set 1 undecided: VETO any
                #                            price trigger (injury news
                #                            is not a set loss)
                score_entry = False
                sst = None
                if score_rows and len(vm.outcome_tokens or {}) == 2:
                    other_name = next(
                        (n for n, t in vm.outcome_tokens.items()
                         if t != fav_ticker), "")
                    sst = fsc_scores.set_state(score_rows, _outcome,
                                               other_name, now)
                if sst is not None and sst["fresh"] \
                        and not sst["completed"]:
                    if sst["fav_sets"] == 0 and sst["opp_sets"] >= 1:
                        if SCORE_LO <= ask <= SCORE_HI:
                            score_entry = True
                            stats["score_confirmed"] = \
                                stats.get("score_confirmed", 0) + 1
                        else:
                            stats["score_out_of_band"] = \
                                stats.get("score_out_of_band", 0) + 1
                            continue
                    elif sst["fav_sets"] >= 1 and sst["opp_sets"] == 0:
                        ledger.set_state(key, {**st, "set1_won": True})
                        stats["score_set1_won"] = \
                            stats.get("score_set1_won", 0) + 1
                        continue
                    else:       # 0-0: first set still in progress
                        if ask <= TRIG_HI:
                            stats["score_veto"] = \
                                stats.get("score_veto", 0) + 1
                        continue

                cts = float(st.get("commence_ts") or 0)
                if not score_entry:
                    if cts:
                        # Real match-start anchor (feed-covered): a
                        # first set cannot be over before commence +
                        # START_MIN_S, so no price trigger can fire —
                        # this kills the pre-match-collapse residual
                        # outright and lets a deploy that lands mid-
                        # match enter without waiting out ARMED_AGE_S.
                        if now < cts + START_MIN_S:
                            stats["skipped_prestart"] = \
                                stats.get("skipped_prestart", 0) + 1
                            continue
                    elif age < ARMED_AGE_S:
                        continue
                if not score_entry and (
                        mid > TRIG_HI or (fav_px - mid) < MIN_DROP):
                    if st.get("pend_ts"):
                        # Blip: the band didn't hold to confirmation.
                        st.pop("pend_ts", None)
                        st.pop("pend_px", None)
                        ledger.set_state(key, st)
                        stats["confirm_reset"] = \
                            stats.get("confirm_reset", 0) + 1
                    continue          # set not (yet) lost by price
                if not score_entry and mid < TRIG_LO:
                    stats["skipped_deep"] = stats.get("skipped_deep", 0) + 1
                    continue          # worse than one set — not this trade
                # TWO-SWEEP CONFIRMATION: the first qualifying look only
                # arms the pending stamp; money moves when a later sweep
                # (>= CONFIRM_MIN_S, <= CONFIRM_MAX_S) still qualifies.
                # A SCORE-confirmed set loss is already a fact — it
                # skips the price confirmation entirely.
                pend_ts = float(st.get("pend_ts") or 0)
                if not score_entry:
                    if not pend_ts or now - pend_ts > CONFIRM_MAX_S:
                        ledger.set_state(key, {**st, "pend_ts": now,
                                               "pend_px": ask})
                        stats["pending_confirm"] = \
                            stats.get("pending_confirm", 0) + 1
                        continue
                    if now - pend_ts < CONFIRM_MIN_S:
                        continue
                if spent_today + PER_ENTRY_USD > DAY_USD:
                    stats["skipped_day_cap"] = \
                        stats.get("skipped_day_cap", 0) + 1
                    continue
                count = int(PER_ENTRY_USD / ask)
                if count < 1:
                    continue
                if book.asks[0].size < count:
                    # PARTIAL ENTRY (owner 2026-08-17 late night:
                    # "never miss a favorite who loses the first
                    # set"): take what the book shows, down to the
                    # $ floor — a smaller position beats a miss.
                    part = int(book.asks[0].size)
                    if part * ask < PARTIAL_MIN_USD:
                        stats["skipped_thin"] = \
                            stats.get("skipped_thin", 0) + 1
                        continue
                    count = part
                    stats["partial_entries"] = \
                        stats.get("partial_entries", 0) + 1
                if not live:
                    stats["entered"] += 1          # dry-run telemetry
                    continue
                if guard_down:
                    stats["skipped_guard_down"] = \
                        stats.get("skipped_guard_down", 0) + 1
                    continue
                r = kalshi.place_order(ticker, ask, count,
                                       client_order_id=str(uuid.uuid4()),
                                       taker=True)
                filled = int(float(r.get("count") or 0)) if r.get("ok") else 0
                if filled <= 0:
                    if r.get("ok"):
                        stats["ioc_zero_fill"] = \
                            stats.get("ioc_zero_fill", 0) + 1
                    else:
                        stats["order_err"] = stats.get("order_err", 0) + 1
                        stats["last_order_error"] = {
                            "ticker": ticker,
                            "status": str(r.get("status"))[:200],
                            "raw": str((r.get("raw") or {}).get("error"))[:300]}
                    continue
                # Mark the order id so sync_kalshi_fills knows this fill
                # is already recorded inline — the unmarked path doubled
                # every position once (2x caps, 2x P&L; audit 2026-08-04).
                if r.get("order_id"):
                    ledger.set_state(f"kalshi_inline:{r['order_id']}",
                                     {"ts": now})
                cost = round(filled * ask, 2)
                st.pop("pend_ts", None)
                st.pop("pend_px", None)
                # spent_usd/ts seed the top-up loop: later sweeps keep
                # buying toward PER_ENTRY_USD while the window is open.
                ledger.set_state(key, {
                    **st, "entered": True, "entry_px": ask,
                    "filled": filled, "spent_usd": cost, "ts": now})
                held_events.add(event)   # same event, later series: held
                spent_today = round(spent_today + cost, 2)
                day = {"date": _day_key(now), "spent": spent_today,
                       "entries": int(day.get("entries") or 0) + 1}
                ledger.set_state("fsc_day", day)
                stats["entered"] += 1
                stats["spent"] = round(stats["spent"] + cost, 2)
                ledger.record_fill(
                    fill_uid=f"fsc-{event}-{int(now)}",
                    venue="kalshi", market_key=f"kalshi:{ticker}",
                    side="BUY", qty=float(filled), price=ask,
                    fee=round(kalshi.taker_fee(ask) * filled, 4),
                    league=league, mode="LIVE_BETA", category="kalshi_fsc",
                    decision={"fsc": True, "fav_px": fav_px,
                              "trigger_ask": ask, "armed_age_s": int(age),
                              "feed_prob": st.get("feed_prob"),
                              "score_confirmed": bool(score_entry),
                              "sets": (f"{sst['fav_sets']}-"
                                       f"{sst['opp_sets']}") if sst else None,
                              "confirm_s": int(now - pend_ts)
                              if pend_ts else None})
                log.warning("FSC ENTRY %s: %d @ %.2f (fav was %.2f, "
                            "armed %dm ago)", ticker, filled, ask, fav_px,
                            int(age / 60))
    _ACTIVE["hint"] = bool(stats["scanned"])
    stats["day"] = dict(day) if day else {}
    return stats

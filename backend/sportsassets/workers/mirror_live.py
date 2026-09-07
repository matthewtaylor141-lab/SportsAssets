"""Position mirroring, phase P1: the LIVE reconciler (owner order
2026-09-02, "go for it, let's get this working"; the P1 panel synthesis,
section 2, with the addendum's amendments).

The shadow (workers/mirror_shadow) reads each mirrored whale's book and
logs the one order it WOULD place. This worker places it -- long-only,
as a maker, under every existing breaker -- and keeps one BOOK per
(whale, market) in mirror_books, with its orders in mirror_orders and
its POSITION as one standing live_orders row (lane 'mirror', status
'filled' from open) so every consumer reads the book as the copy
position it is. Nothing here restates a rule: the arithmetic is
analytics/mirror, the decisions are analytics/mirror_live_rules, the
ledger writes are live_executor's mirror primitives, the venue reads
are the shadow's paced helpers. This module READS, CALLS THEM, and
WRITES, in the order the spec's tick names:

  0  MODE      env PMUS_MIRROR off -> SAFE (cancel-only; the loop keeps
               running so a deploy that drops the flag cannot orphan a
               rest), 'exits' -> reductions and flattens only, 'on' ->
               full; the DB switch 'mirror_live' must read exactly True
               for increases (false / absent / unreadable / malformed
               is exits-only); the trading tables must exist
  G  GUARDS    the executor's global guards in maybe_execute's order
               (any trip: a cancel-only tick), then the INCREASE-ONLY
               guards (the loss breaker, the sleeve's room, the mirror's
               own day cap and loss stop): reductions and flattens go on
  R  READS     once per tick: ratios, ONE paced positions walk, the
               account's open orders, the edge gate, the protected ids
  O  ORDERS    every non-terminal mirror order first: a lost placement
               is adopted by fingerprint or booked from the trade log by
               ORDER, an open order is read and its delta booked, a
               terminal one is written, a stale or unwanted one is
               cancelled and read until terminal
  B  BOOKS     existing books, then new candidates (newest first, under
               the per-tick cap): his position from the exit worker's
               FRESH COMPLETE snapshot (addendum section 1: fills are
               the trigger and the price, never the position), step M
               (a closed or closing market: cancel, never increase; a
               market that could not be read: cancel and HOLD, named)
               BEFORE the plan, the plan from the book's FIXED ratio,
               the freeze on venue/ledger disagreement, the act
  X  ACT       keep, cancel/replace, place a post-only GTC rest, the
               bounded take, the two flattens (paired-out rests and is
               never marketed; vanished rests, then mirror_exit's
               sole/co-held rules), the episode close
  E  CENSUS    every refusal named, every counter present, one
               heartbeat 'mirror_live', the reaper-isolation instrument

Fail closed on every read: a fact that could not be read is the named
refusal, never a guess; an unreadable ledger, venue or switch is a
tick that at most CANCELS; a trip MID-TICK (wrong sign, overfill) makes
the rest of the tick cancel-only. The reviews that shaped this file are
the P1 panel synthesis (critics C14-C16), the addendum's second-critic
amendments (sections 7-11), the step-5/6a/rules reviews it carries and
the step-9 worker review (owner order 2026-09-02, "go for it, let's get
this working": thirteen findings, each pinned in the worker tests) with
its re-review (six minors: a lost order clears the take arm, the first
post-only refusal starts the take clock, the first rest of a vanish
starts the slippage clock, a candidate's unreadable market is named as
such, a lost close is sized off this tick's walk, a closed book's rest
is cancelled 'closed'; each pinned in the worker tests' section 13),
and the residuals that re-review left (section 14: the take arm's
evidence is bounded -- cleared when the book leaves his level with no
rest standing, refused by name past twice the wait so the book rests
first; a closing book's rest is cancelled 'closing', never under its
stale freeze; the take arm reads both of the venue's post-only refusal
shapes, the to-a-tee program's Phase 7 rung 1 seam).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .. import copy_sports, edge_gate
from .. import live_executor as le
from ..analytics import mirror as mi
from ..analytics import mirror_live_rules as rules
from ..analytics.decompose import payout_of
# The side and intent strings and the flat tolerance are values; every
# CAP, WAIT and dataclass is read through the rules module AT CALL TIME
# (rules.MIRROR_..., rules.BookState): the rules module is reloaded by
# its own tests under lowered environments, and a class or cap bound
# here at import would be the stale one (an AdmissionFacts the reloaded
# admission() no longer recognises reads `facts_unreadable`).
from ..analytics.mirror_live_rules import BUY, FLAT_TOL_SHARES, ORDER_INTENT, ORDER_INTENT_SHORT, SELL
from ..analytics.roster_rules import MIRROR_ANCHOR_CLIP_USD
from ..config import settings
from ..db import get_pool, heartbeat
from .. import venue_pace
from ..venue_pace import pace
from . import mirror_shadow as ms
from . import whale_exits

log = logging.getLogger(__name__)

# The loop's own timers. Every cap and wait is imported above from the
# rules module (never restated); these two are the reconciler's clock.
# POLL_S STAYS 30 s after E2 (2026-09-06). The brief lowered it to 10 s
# ONLY with the tick under 10 s on the live book count; the parallel
# book walk (_walk_books) takes the tick off its sequential ~3.5 s a
# book (162 s with 46 books, 20:48Z) but not under the pacer's floor:
# every measurement read queues on venue_pace's one-per-0.35 s gate,
# so 46 books are >= 16 s of quote reads before a candidate is read.
# Ticks never overlap (_TICK_LOCK); a wake still cuts the poll short.
POLL_S = 30.0
WAKE_MIN_GAP_S = 5.0
SERVICE = "mirror_live"
# THE LIVE LANE'S OWN CANDIDATE READ BUDGET (E2 review round 2, HIGH-B):
# 40 candidate quote reads a tick (20 -> 40 was E2's step 5). Its own
# constant, not the shadow's ms.MAX_MARKETS_PER_TICK (20): the two
# lanes shared one name and E2 doubled the shadow's reads on a venue
# already answering 429s. capped_env: the environment may only lower it.
MAX_MARKETS_PER_TICK = int(rules.capped_env("MIRROR_LIVE_MAX_MARKETS", 40.0, floor=0.0))
# The per-market position read's wall-time bound. NOT an environment
# read: it is a containment bound, not an operator dial. The shared
# httpx client's 25 s timeout is not a per-read timeout -- it is
# per-request and shared with `_confirm_gone` -- and a data API that is
# merely SLOW raises nothing, so `MAX_MARKETS_PER_TICK` (40 since
# E2, the live lane's own) awaits of up to 25 s inside a POLL_S of 30 s is a tick that runs
# for minutes with nothing reconciled and no name for it. Five seconds
# is well clear of the throttle's own pacing (data_api_max_rps 6.0 ->
# 0.17 s a read, ~6.7 s for a whole tick's worth even if this worker
# had the budget to itself). Since E2 the books' reads run
# MIRROR_BOOK_CONCURRENCY at a time and the candidates' are cut by the
# venue-call soft guard, so a slow data API costs a tick at most
# 40 x 5 s on the candidates -- the freshness clause below
# (ms.SNAP_MAX_AGE_S) still refuses a read that lands late.
_SNAP_READ_TIMEOUT_S = 5.0
# THE TICK'S VENUE-CALL BUDGET (E6, 2026-09-07; owner 18:3xZ / 19:1xZ
# "Need everything running and running at mirror to him"). The tick
# was 80-127 s with 74-77 live books (19:10Z 79.8 s, 19:21Z 98.7 s,
# 19:28Z 127.2 s; venue_calls 89-112, every one behind the 0.35 s
# pacer) and his median entry latency measured 86.8 s: a tick that
# long IS the latency. Every existing book was read every tick (U12),
# most of them quiet -- on target, nothing open, no fill of his for an
# hour -- and their reads sat in front of the candidates every tick.
# The budget is a COUNT OF PACED VENUE CALLS a tick spends, so a tick
# lands near TICK_TARGET_S at today's book count (60 calls ~ 21 s of
# pacing plus latency), with PRIORITY so nothing money-critical waits:
#   ALWAYS READ (never budgeted): a book with an order open, a frozen
#     book (E5's exit path), a book whose last read was not on target
#     (a reduce or an increase is pending), a book with a fill of his
#     on its market inside HOT_S (the fills the tick already holds --
#     no new read), a book whose market WOKE this tick (E6 review
#     HIGH-2: a fill ingested late -- the poller catching up after a
#     deploy, a backfill -- carries an old `ts` but the wake is the
#     process's strongest money-in-motion evidence), a book whose last
#     plan was an exit's take, the take at his level or an unfilled
#     reduce (QUIET_EXIT_PLANS; census names, see docs section 28);
#   ROUND-ROBIN under the budget: the QUIET books are read every
#     QUIET_EVERY_TICKS ticks (_quiet_memo: the tick each book was last
#     quote-read on and its verdict, process-local like the candidate
#     cursor _cand_cursor); a quiet book DUE for its read that the
#     budget cannot fit is DEFERRED: it joins a FIFO queue
#     (_quiet_deferred) and the next ticks read the queue FIRST, in
#     order, max(quiet_budget, DEFERRED_MIN_PER_TICK) a tick -- the
#     floor so a budget the hot books alone spend (0) can never starve
#     a quiet book -- the overflow staying queued (E6 review MEDIUM-1:
#     the whole deferred cohort was read next tick whatever the budget,
#     102 reads at a budget of 20 after a restart); so a deferred
#     book's worst wait past its due tick is ceil(N_quiet /
#     max(quiet_budget, DEFERRED_MIN_PER_TICK)) ticks; a quiet book
#     that is not due is skipped under `book_quiet_skipped` (plan
#     `no_plan`, `read_on` the tick it will be read on). Step M (the
#     markets row, _maybe_close_episode), the standing-row close and
#     the terminal memos still run EVERY tick for EVERY book: a skipped
#     quote read never skips a close; the skip writes its name and its
#     plan on the row and NOTHING else (the last read's his_net /
#     venue_net / snapshot stand: review LOW-1);
#   CANDIDATES: the walk runs AFTER the books and takes what the budget
#     leaves -- cand_budget = max(CAND_MIN_PER_TICK, budget - calls so
#     far), the quiet share having reserved CAND_MIN_PER_TICK (review
#     MEDIUM-4: the due tick spent budget + CAND_MIN) -- the resolver's
#     map reads take what sits ABOVE the quote reads' floor
#     (max(ms.MAP_READS_PER_TICK, cand_budget - CAND_MIN_PER_TICK)) and
#     the quote reads never fall under it (min(MAX_MARKETS_PER_TICK,
#     max(CAND_MIN_PER_TICK, cand_budget - map reads)); review HIGH-1:
#     at the floor the resolver reads spent the whole share and the
#     mapped candidates behind them got no quote read).
# Fail closed: a book this process has never read (an empty memo, a
# restart) is read -- the old behaviour -- never skipped; an unreadable
# fill row is a hot book. Env may only LOWER the budget (capped_env,
# floor 20). NOT this knob: rules.MIRROR_VENUE_CALLS_PER_TICK is E2's
# soft guard on the tick's writes and candidate reads (80, its own
# name, untouched); this is the whole tick's paced-call budget under a
# name of its own. Pacing (READ_PACING_S), the per-market data-API
# throttle, what a read decides once made and order placement are not
# touched here.
TICK_TARGET_S = 25.0
VENUE_CALLS_PER_TICK = int(rules.capped_env("MIRROR_TICK_VENUE_CALLS", 60.0, floor=20.0))
HOT_S = 600.0
QUIET_EVERY_TICKS = 3
CAND_MIN_PER_TICK = 10
# the deferred queue's floor: the reads a tick makes on books the budget
# deferred even when the hot books alone spent it -- the same floor the
# candidates keep, for the same reason (a share of zero for as long as
# the hot books outnumber the budget is a quiet book never read again)
DEFERRED_MIN_PER_TICK = 10
QUIET_EXIT_PLANS = frozenset({"exit_take", "take_at_his_level", "reduce_unfilled"})
# the prior plan's fields a quiet skip's plan carries (the next read and
# a sibling's game room read them off the row: _tick_book, _held_exposure)
_SKIP_CARRIED = ("flat_since", "short_proof", "mark", "bid", "ask", "exit_px_src")
# A 'placing' row with no order id older than this is a placement whose
# response was lost with the process (step O); younger, the placement
# may still be the one in flight under this very tick's lock.
PLACING_ORPHAN_S = 60.0
# A take arm older than this many MIRROR_TAKE_AFTER_S waits is stale
# evidence of a crossing (the arm is read in _act before the room and
# the clip, so nothing else bounds its age): the take is refused by
# name and the book rests first. A multiplier of the rules' wait, never
# a wait of its own, so lengthening the wait lengthens the bound.
TAKE_ARM_STALE_WAITS = 2
# ... with a FLOOR (E2 review, MEDIUM-2b): at the 20 s wait twice the
# wait is 40 s, and at a 0 s wait (E4's owner order) it is 0 -- every
# armed take would be stale on the next tick and never fire. The stale
# bound is max(TAKE_ARM_STALE_WAITS x wait, this many seconds).
TAKE_ARM_STALE_MIN_S = 60.0
# The cancel/read discipline of the rest lane (_rest_cycle): two cancel
# attempts, then up to three reads a short gap apart until terminal.
CANCEL_ATTEMPTS = 2
CANCEL_READS = 3
CANCEL_READ_GAP_S = 0.3
# The book cross-check tolerance on settlement (spec 1e).
SETTLE_DISAGREE_USD = 0.05
# The shadow-vs-live instrument compares readings this close in time.
SHADOW_AGREE_WINDOW_S = 60.0


def _mode_line_every_ticks(default: int = 10) -> int:
    """MIRROR_MODE_LINE_EVERY_TICKS as a whole number of ticks with a
    floor of one. Absent, blank or unparseable is the default; under
    one lands on one (a modulus of zero is no cadence at all)."""
    raw = os.environ.get("MIRROR_MODE_LINE_EVERY_TICKS")
    try:
        n = int(str(raw).strip()) if raw is not None and str(raw).strip() else int(default)
    except ValueError:
        n = int(default)
    return max(1, n)


# THE OPERATOR CAN READ THE MODE ON A QUIET TICK. main() logs a tick's
# stats only when it placed, cancelled or abandoned, so a reconciler
# sitting in exits-only (the DB switch false after a trip, a dropped
# env flag) wrote nothing to the workers log for as long as it was
# quiet, and "which mode is the mirror in" had no answer short of the
# heartbeat row (adversarial pre-flight 2026-09-05, before the owner's
# "switch on the mirror system 100%" order). Every this many ticks one
# INFO line names the mode, the whales, the live books, the open orders
# and the day room, whatever the tick did (_mode_line).
MODE_LINE_EVERY_TICKS = _mode_line_every_ticks()

_STATE_LIVE = "mirror_live"
_STATE_WHALES = "mirror_live_whales"
_STATE_DEMOTED = "mirror_live_demoted"
_STATE_LOSS_STOP = "mirror_loss_stop"
# THE RE-ARM RESTARTS THE WINDOW (L1, 2026-09-07). The operator's
# `mirror-rearm` (render-ops, confirm=DO) deleted the stop at 16:51Z and
# the worker re-tripped itself at 17:07:45Z ({"sum": -5023.9545,
# "books": 214, "limit": 5000.0}): the morning's losses (tripped
# 09:46:36Z at -5,002.58) sat inside the trailing 24 h until the next
# morning, so every re-arm re-tripped within a tick or two of any new
# loss. A re-arm that cannot hold is not a re-arm. The preset now writes
# this key in the same statement as its DELETE -- {"at": now() ISO,
# "by": "render-ops", "prior": the deleted stop's value or null} -- and
# the loss sum counts only what happened AFTER the newest re-arm: the
# window starts at GREATEST(now - LOSS_WINDOW_S, at) (_loss_window_start).
# A re-arm older than the window changes nothing; a key with no
# parseable `at` reads as ABSENT and is logged once (fail closed toward
# the FULL window, never a shorter one); an unreadable key is a stop, as
# the stop key's own read is. The limit (MIRROR_LOSS_STOP_USD) and the
# exit carve-out are untouched: a standing stop key holds whatever this
# key says -- only the preset's DELETE clears it.
_STATE_LOSS_REARM = "mirror_loss_rearm"
LOSS_WINDOW_S = 24 * 3600.0
# a re-arm `at` this far AHEAD of the tick's clock is still the re-arm it
# says (a DB clock a moment ahead of the worker's); further ahead it is
# malformed -- the full window, never an empty one (review L1)
LOSS_REARM_SKEW_S = 300.0
_rearm_malformed_logged = False
# the loss window the LAST tick read (t.loss, published by tick_once;
# None on a tick that never read it): what main() hands the quiet-tick
# mode line to print as `loss=<sum>/<limit> since <HH:MM>`. A module
# global like _last_mode, and NOT a stats key: the health endpoint's
# sanitizer caps the served detail at 40 keys and an ON tick already
# fills them (_publish_fills_dedup), so a new key would only ever be
# the dropped one
_last_loss: dict | None = None
# L2: the copy sleeve's breaker the LAST tick read over the mirror's
# window (t.sleeve: sum, limit, since), for the mode line's `sleeve=`
_last_sleeve: dict | None = None
_STATE_FLATTEN = "mirror_flatten"
# S4: the read-back proof of a resting SELL_SHORT (the short cover's
# wire intent). {"proved": bool, "at", "slug", "order_id", "price",
# "bid", "ask", "echo": {...}, "why", "cancel", "booked"}; written by
# _s4_probe, read every tick beside the other mirror-state keys. Absent,
# malformed or `proved` not True: every short cover is refused
# `s4_unproven` and the probe runs at most once per S4_PROBE_GAP_S on a
# live short book with a two-sided quote. `order_id` on an UNPROVED
# record whose `cancel.ok` is False is a probe order the venue KEPT
# (S4 review round 2, GAP 2): the cancel is retried every tick before
# the walk (_s4_cancel_retry) and no second probe goes out until the
# venue reports it gone; `booked` names the mirror_orders row a probe
# that FILLED at create was booked on (GAP 1)
_STATE_S4 = "mirror_s4_proof"
S4_PROBE_GAP_S = 3600.0
# the probe's price sits this far UNDER the bid (a BUY of the long token
# that cannot fill), floored to the cent and never under 0.01
S4_PROBE_OFFSET = 0.05
# the venue's OWN side for a resting SELL_SHORT that buys the contract
# back (the SDK's Order.side; pmus._norm_order carries it as
# `venue_side`): the one fact that settles the denomination. The proof
# requires it beside the price, the quantity and the intent (S4 review,
# F1); a venue that lists the probe as ORDER_SIDE_SELL is an order in
# another price space, never proved
S4_VENUE_BUY = "ORDER_SIDE_BUY"
# the venue's order STATES that mean "this order stands on the book as
# sent" (S4 review round 2, INFO 5), as pmus._norm_order spells them
# (the SDK's OrderState enum, polymarket_us/types/orders.py:21-33, with
# the ORDER_STATE_ prefix dropped and lower-cased): NEW (accepted and
# resting), PENDING_NEW (accepted, being written to the book) and
# PENDING_RISK (accepted for listing, not yet past risk) are the states a
# standing 1-share limit passes through on the way to resting; the proof
# accepts those three. FILLED / PARTIALLY_FILLED (the venue did not read
# our price or our side as sent), CANCELED / EXPIRED / REJECTED /
# REPLACED (nothing stands), PENDING_CANCEL / PENDING_REPLACE (something
# other than our create acted on it), or no state at all are refused:
# an echo the venue lists with the right fields but in a state that is
# not a standing order proves nothing about a standing cover
S4_RESTING_STATES = frozenset({"new", "pending_new", "pending_risk"})
_STATE_SIDE_ECHO = "side_echo_last"
# THE TERMINAL MEMOS SURVIVE A DEPLOY (E6 part 3). _terminal_until and
# _terminal_book_until are process-local, so every deploy (four in the
# hour to 19:28Z) started with them empty: every book on an EXPIRED
# market was re-read once (venue_halted 38 / 78 right after 18:59Z and
# 19:28Z) and every candidate walked again (cand_unread_capped 335 /
# 325) -- 60-90 s of the first tick each time. Both memos are kept
# under ONE ingestion_state key, {"cand": [[whale, cid, until], ...],
# "book": [[whale, cid, until, state], ...], "at": ISO}, BOUNDED:
# entries whose `until` has passed are dropped on write, at most
# _TERMINAL_MEMO_MAX kept (the soonest to expire dropped first), written
# at most once per TERMINAL_MEMO_WRITE_S and only when the bounded
# snapshot changed, and read ONCE at boot. Fail closed: an unreadable
# or malformed key is an EMPTY memo (today's behaviour), never a raise;
# a malformed entry is dropped, never guessed.
_STATE_TERMINAL_MEMO = "mirror_terminal_memo"
TERMINAL_MEMO_WRITE_S = 60.0
_TERMINAL_MEMO_MAX = 4000
# THE CANDIDATE MEMOS STOP RE-READING WHAT HAS NOT CHANGED (E7,
# 2026-09-07; owner "Need everything running and running at mirror to
# him"). With E6 live (tick 47.9 / 51.3 s at 21:15Z / 21:20Z) the
# candidate stage was 25.9 / 13.3 s and the census read map_reads_capped
# 114 / 86: 100+ markets waiting on a resolver read, and the 24 h
# refusal census (cand_refusals 21:17Z) said the reads were the SAME
# ANSWERS every rotation -- `unmapped` 276 markets / 1,667 rows (each a
# paced resolver call, e.g. por-est-aro-2026-09-07-total-0pt5 unmapped
# @20:04 @20:20 @20:35 @20:51 @21:09: five paced calls for one unchanged
# answer) and `no_mark` 179 markets / 1,121 rows / $1.01M (the morning's
# tennis, the matches over, the venue still OPEN with an empty book,
# re-read at every rotation slot: a paced quote read AND a per-market
# data-API read each time, with NO memo). THE RULE: a candidate whose
# quote read found an OPEN market with no mark (the `no_mark` refusal
# rules.mirror_target returns for a candidate; never a HALTED /
# SUSPENDED / PREOPEN read -- those reopen, D1's rule -- and never a
# read that raised) is remembered per (whale, condition_id) for
# NO_MARK_TTL_S beside _unmapped_until, and skipped under
# `cand_no_mark_skipped` (no slot, no venue call) until the memo runs.
# BOTH candidate memos are RELEASED BY HIS FILLS: each stores the `at`
# of the read beside its `until`, and at the walk a memoised market
# whose newest fill of his (the `last_ts` the candidate order already
# ranks on -- no new read) is NEWER than the memo's `at`, or that WOKE
# this tick (a fill ingested late carries an old stamp: E6 review
# HIGH-2's reasoning), is read this tick, the memo dropped
# (`cand_memo_released`): C2's "a market unlisted at first sight may be
# listed a minute later" answered by evidence instead of a clock -- a
# market he is still trading is re-checked at once, one he stopped
# trading is not. GROWTH, the unmapped memo only: a market read
# unmapped again with NO fill of his since its last read (the fills
# the read holds; a fill with no readable stamp counts as one) doubles
# its TTL, ms.UNMAPPED_TTL_S -> ... -> UNMAPPED_TTL_MAX_S (a constant:
# nothing to lower under the base); a release by fill, a cleared entry
# or a deploy resets it to the base. The no_mark memo keeps its flat
# TTL (the book may fill in; his fill releases it anyway).
# `map_reads_capped` stays a no-verdict: never memoised. A BOOK's read
# never writes either memo (the book memo is _terminal_book_until's, W1
# / R4). Both memos PERSIST across a deploy under ONE ingestion_state
# key of their own, `mirror_cand_memo` = {"unmapped": [[whale, cid,
# until, at, ttl]...], "no_mark": [[whale, cid, until, at]...], "at"},
# under E6's rules exactly -- expired entries dropped on write, at most
# _TERMINAL_MEMO_MAX per list (the soonest to expire dropped first),
# written at most once per TERMINAL_MEMO_WRITE_S and only on change,
# read ONCE at boot, nothing written before that read -- and a KEY OF
# ITS OWN because E6 pins `mirror_terminal_memo`'s exact shape
# ({cand, book, at}; test_e6_tick_budget). Fail closed: an unreadable
# or malformed key is an empty memo, a malformed entry (a TTL off the
# ladder, an `until` past its `at` + TTL, a future `at`) is dropped --
# the market is READ, never skipped on a guess; a fill stamp the walk
# could not read releases nothing and grows nothing (the flat TTL, the
# C2 clock, stands). Env may only LOWER NO_MARK_TTL_S (capped_env,
# floor 60). This decides WHEN a candidate is read, never what a read
# decides: mapping, admission, the throttle's rate, READ_PACING_S and
# a book's reads are untouched.
NO_MARK_TTL_S = rules.capped_env("MIRROR_NO_MARK_TTL_S", 900.0, floor=60.0)
UNMAPPED_TTL_MAX_S = 3600.0
_STATE_CAND_MEMO = "mirror_cand_memo"
MODE_SAFE, MODE_EXITS, MODE_ON = "safe", "exits", "on"
_OFF_VALUES = frozenset({"off", "0", "false", "no"})

# Every census name the tick can emit (spec section 5, plus the names
# the rules module and the addendum introduced). The heartbeat carries
# every one at 0 so a reader can tell "never happened" from "not
# counted"; the prefixed families (mapping:<why>, edge_gate:<why>,
# cell_gate_<clause>, place_refused:<status>) are counted under their
# family name here and by full name in the bounded _mirror_stop dict.
CENSUS_KEYS: tuple[str, ...] = (
    "mode_env_off", "mode_db_off", "mode_db_unreadable", "whales_unreadable",
    "tables_absent", "no_venue", "probe_disabled", "halted", "paused",
    "overspend_halt", "mirror_overspend", "overspend_uncheckable",
    "loss_breaker", "loss_breaker_unreadable", "no_budget_room",
    "mirror_day_cap", "mirror_loss_stop", "positions_unreadable",
    "open_orders_unreadable", "protected_ids_unreadable", "tick_abandoned",
    "no_ratio", "no_mark", "no_quote", "venue_halted", "unmapped", "family", "per_side_unsupported",
    "market_closed", "market_unreadable", "game_too_far_out", "mapping", "edge_gate", "cell_gate",
    "clip_zero", "legacy_row", "slug_recent_copy", "underdog_coholds",
    "venue_already_holds", "kalshi_claimed", "side_band", "snapshot_stale",
    "snap_market_unreadable", "snap_market_capped", "snap_market_stale",
    "snap_market_no_ids", "snap_market_skipped", "drift",
    "max_books", "first_fill_gate", "asset_claimed", "book_exists",
    "short_side_refused", "on_target", "under_one_share", "dead_band", "hysteresis",
    "no_price", "venue_ledger_disagree", "wrong_sign_trip", "order_state_unknown",
    "placement_lost", "lost_ambiguous", "order_lost", "cancel_pending",
    "replace_capped", "take_capped", "ops_capped", "over_room", "open_order_pending", "rest_placed",
    "take_placed", "take_arm_stale", "post_only_rejected", "post_only_ignored", "place_refused",
    "filled_rest", "filled_take", "partial_fill", "cancelled_unfilled", "expired",
    "resting_above_level", "reduce_unfilled", "flatten_rested", "flatten_vanished",
    "vanish_unconfirmed", "no_bid_for_flatten", "flatten_holding_disagrees",
    "overfill", "closed_cashed_out",
    "closed_cancelled", "book_settle_disagree", "shadow_live_disagree",
    "reaper_touched_mirror", "demoted", "mirror_flatten", "row_not_live",
    "write_failed", "rate_limited", "book_error",
    # APPENDED AT THE END (2026-09-06): the served census is a 40-key
    # prefix of this tuple (see _INTEG_CENSUS_KEYS), so a name inserted
    # anywhere earlier moves every index after it. `ledger_dust`: a SELL
    # the venue filled within one lot past the ledger (rules
    # SELL_DUST_SHARES) -- booked to the ledger, counted, never a trip
    "ledger_dust",
    # P2 rung S0 (brief G4): the short side's placements, its one proven
    # exit, its named refusals, and the sign flip. Appended AFTER
    # ledger_dust so the first-40 projection the health endpoint serves
    # keeps its order; the ones a gate reads ride in `integ` below
    "short_open", "short_add", "short_flatten_close", "short_reduce_unproven",
    "sign_flip", "short_model_disarmed", "short_gate_refused", "short_column_absent",
    # the probe of the 050 column failing for any reason but absence
    # (the tick is refused), and the short side's share cap biting
    "intent_guard_unreadable", "short_share_cap",
    # U12 (2026-09-06): the book COUNT unreadable at admission. Used to
    # hide under `max_books`; now the count caps are unbounded by
    # default and an unreadable count still refuses -- fail closed --
    # under its own name. Appended LAST: the served census is a 40-key
    # prefix of this tuple and its order is pinned
    "books_unreadable",
    # review of U12c (FIX-1, FIX-2): the one-way step off an exact-copy
    # book, and an order under the mirror's minimum notional, not sent
    "ratio_stepped", "under_min_notional",
    # FIX-3b: the shadow row lacked what a raw-arithmetic comparison
    # needs (never a disagree)
    "shadow_check_skipped",
    # C1 (2026-09-06), the mirror maps what the copy lane maps: a
    # candidate past the tick's mapping budget (no verdict, read again
    # next tick), a mapping whose source the live worker does not admit
    # (MIRROR_LIVE_MAP_SRC), and the exact lane's venue reads and cache
    # answers, counted as events. Appended LAST: the served census is a
    # 40-key prefix of this tuple and its order is pinned
    "map_reads_capped", "map_source_unverified", "map_venue_read", "map_cache_hit",
    # C1 round 2: the grammar class's own certification -- its state
    # unreadable, the class tripped, another grammar book still awaiting
    # its first-fill echo, the venue-truth check that could not run or
    # passed, and the mismatch that trips (before an open or on the
    # first fill: `side_echo_mismatch`, the name the copy lane's own
    # circuit uses for the same fact)
    "grammar_echo_unreadable", "grammar_tripped", "grammar_probation",
    "grammar_echo_unverified", "grammar_echo_ok", "side_echo_mismatch",
    # the copy lane's soccer/esports price floor, lifted for a mirror
    # book by owner order (2026-09-06, _mirror_cell): counted, never a refusal
    "soccer_floor_lifted",
    # E1 (2026-09-06, owner order "the per game cap is at 2500 per game
    # (never more)"): the $2,500 cap is PER GAME across every market of
    # the game -- a target SCALED to the room the game has left, and a
    # target held at what the book has because the game has no room
    # (an increase of 0, never a reduce). Both sit past the served
    # 40-key prefix; `integ` is at its ceiling, so they are read off the
    # raw heartbeat and the plan (`game_room`, `game_exposure`).
    # `game_unreadable` (re-review LOW-3): the game's exposure could not
    # be read this tick (a sibling with a non-terminal order nobody can
    # read, an unreadable cost) -- no room, told apart from a game that
    # IS full. `cand_game_full_skipped` (re-review LOW-2): a candidate
    # on a game the memo (_game_full_until) says read FULL inside the
    # last GAME_FULL_MEMO_S, skipped before its venue read (an
    # unreadable game is never memoised: read again next tick).
    # All inserted BEFORE `cand_terminal_skipped`, which stays last (pinned)
    "game_cap_scaled", "game_cap_full", "game_unreadable", "cand_game_full_skipped",
    # E2 (2026-09-06, latency): the bounded take's two verdicts once the
    # wait has run -- fired at his level (one IOC at the same wire) or
    # refused on price (the book never came to him) -- and the tick's
    # venue-call count, every call as an event, with the soft guard
    # that stops the candidate walk at rules.MIRROR_VENUE_CALLS_PER_TICK.
    # Inserted BEFORE `cand_terminal_skipped`, which stays last (pinned)
    "take_at_his_level", "take_refused_price", "venue_calls", "venue_calls_capped",
    # E2 review (LOW-6): a decided order -- an entry or an exit -- refused
    # because another book abandoned the tick while this one was in
    # flight; no plan is written for it (see _tick_book)
    "abandoned_in_flight",
    # E2 review round 2 (LOW-f): the walk-order streak judge raised
    # after a book; logged and counted, the game's walk goes on
    "walk_error",
    # E2 review round 3 (MEDIUM-3): a placement-429 abandon that skipped
    # the 60 s backoff because the pacer's circuit already holds
    "backoff_skipped_circuit",
    # E4 (2026-09-06, owner order "we should exit when he exits at his
    # price or within 1c variance (tolerance)"): an exit's IOC sent (the
    # bid within the tolerance of his exit price), an exit held outside
    # the cent (the rest stands at his cent, nothing chases), and a TTL
    # re-quote to the SAME wire skipped as the no-op it is. The addendum
    # (~23:38Z, "Remove the 20 second wait on entires too"): an entry's
    # IOC sent FIRST, before any rest. Inserted BEFORE
    # `cand_terminal_skipped`, which stays last (pinned)
    "exit_take", "exit_out_of_tol", "requote_same_wire", "take_first",
    # S4 (2026-09-07): the short cover as a PRICED ORDER through the
    # placement machinery (the venue refuses close_position as an
    # unpriced limit order: "Price is required for limit order", 11
    # CLOSE rows / 0 executed on 2026-09-07). The 1-share read-back
    # probe placed / proved; a cover refused because the proof has not
    # passed; the cover's rest at floor(his) / its IOC at the ceiling
    # cent / held outside the tolerance. `s4_probe_filled` (S4 review
    # round 2, GAP 1): a probe that EXECUTED at create, booked on an
    # `s4_probe` row through the ledger like any cover fill. Inserted
    # BEFORE `cand_terminal_skipped`, which stays last (pinned)
    "s4_probe_placed", "s4_proved", "s4_unproven", "s4_probe_filled",
    "short_cover_rest", "short_cover_take", "short_cover_out_of_tol",
    # W1 / R4 (2026-09-07): an open book whose last BOOK read said the
    # market had ended (_terminal_book_until): its quote read and its
    # per-market read skipped this tick, the plan written `no_plan`
    # under `no_mark` as before. Inserted BEFORE `cand_terminal_skipped`,
    # which stays last (pinned)
    "book_terminal_skipped",
    # W2 (2026-09-07): every exit of _tick_candidate is a NAME and lands
    # on mirror_candidate_refusals (migration 054). The four exits that
    # were silent: `long_token_unknown` (his fills sit only on the
    # venue's short-side token and the catalogue names no long token),
    # `target_zero` (ratio x net rounds to nothing), `book_row_unreadable`
    # (the book row could not be read back after the open),
    # `cand_unread_capped` (the walk's cap left the candidate unread this
    # tick). `refusal_write_failed`: the refusal table could not be
    # written (absent until 054 lands, or a blip); the tick goes on.
    # Inserted BEFORE `cand_terminal_skipped`, which stays last (pinned)
    "long_token_unknown", "target_zero", "book_row_unreadable", "cand_unread_capped",
    "refusal_write_failed",
    # E5 (2026-09-07): FROZEN BOOKS FOLLOW HIS EXITS, and the operator
    # register. A book frozen `placement_lost` / `venue_ledger_disagree`
    # plans a REDUCE sized on the venue's own position toward his net
    # (_frozen_exit): `frozen_reduce` when one went out; the refusals
    # `frozen_exits_off` (the knob, off), `frozen_venue_unread` (the
    # venue's position or his per-market read not read this tick),
    # `frozen_coheld` (a live non-mirror row on the slug: the venue's
    # number is not the book's alone), `frozen_venue_flat` (nothing of
    # the book's on the venue), `frozen_no_his_exit` (his net is not
    # under ours), `frozen_reduce_only` (the plan was not a reduce:
    # never sent). `frozen_excess_sold`: a frozen reduce's fill past
    # the ledger -- the lost response's shares, sold inside the venue's
    # own reading -- named on the receipt, never the overfill trip.
    # `registered_books`: a book whose slug carries a register row
    # (migration 056); `registered_sign_refused`: a register row whose
    # sign is against the book's leg (not read: fail closed);
    # `registered_unreadable`: the register could not be read (absent
    # until 056 lands; explains nothing). Inserted BEFORE
    # `cand_terminal_skipped`, which stays last (pinned)
    "frozen_reduce", "frozen_exits_off", "frozen_venue_unread", "frozen_coheld",
    "frozen_venue_flat", "frozen_no_his_exit", "frozen_reduce_only", "frozen_excess_sold",
    "registered_books", "registered_sign_refused", "registered_unreadable",
    # E5 review: `frozen_fill_this_tick` (F1/F2: a fill booked on the
    # book after the walk -- r.venue is stale by it, nothing sized),
    # `frozen_venue_unexplained` (M1: a placement_lost book's venue
    # surplus past its leg plus its lost rows' quantity), and
    # `registered_no_increase` (F3: a book whose slug carries a register
    # row never grows). Before the pinned last key
    "frozen_fill_this_tick", "frozen_venue_unexplained", "registered_no_increase",
    # E7 (2026-09-07): a candidate skipped on the no_mark memo (its last
    # quote read found an OPEN market with no mark inside NO_MARK_TTL_S),
    # and a candidate memo (unmapped or no_mark) dropped because his
    # newer fill landed -- the market read this tick. Before E6's key,
    # which the E6 pins hold at keys[-2]
    "cand_no_mark_skipped", "cand_memo_released",
    # E6 (2026-09-07): a quiet book's quote read skipped under the tick's
    # venue-call budget (plan `no_plan`, `read_on` the tick it is read
    # on). Before the pinned last key
    "book_quiet_skipped",
    # D1 (2026-09-06): a candidate skipped because its venue state read
    # TERMINAL inside the last UNMAPPED_TTL_S (_terminal_until). Appended
    # LAST, past the served 40-key prefix; `integ` is at its own ceiling
    # (39 keys under the sanitizer's cap), so this one is read off the
    # raw heartbeat and this module's tests, not the served census
    "cand_terminal_skipped",
)
_FAMILIES = (("mapping:", "mapping"), ("edge_gate:", "edge_gate"),
             ("cell_gate_", "cell_gate"), ("place_refused:", "place_refused"))

# ------------------------------------------------------------ wake + census

# The hand-off's wake (spec 3.1): maybe_execute's gate calls notify() for
# a mirrored whale's fill, in this same process, and the loop reads the
# woken markets first. A courtesy, never a condition -- the loop polls.
_WAKE = asyncio.Event()
_WOKEN: set[str] = set()
_WOKEN_MAX = 200
_TICK_LOCK = asyncio.Lock()
_BOOK_LOCKS: dict[int, asyncio.Lock] = {}
_backoff_until = 0.0
_last_tick_at = 0.0
# THE MODE THE WORKER ACTUALLY HOLDS, for a tick inside the backoff. A
# skipped tick returned _new_stats() whole -- mode 'safe', whales [] --
# so every 10th tick that fell inside a 60 s backoff printed a mode
# line reading `mode=safe` between `mode=on` abandons, and a reader saw
# the mirror flip to SAFE every few minutes (live log 23:09-23:25Z,
# 2026-09-05). The last completed tick's mode and allowlist are kept
# here and published on the skipped tick, which also says how long the
# backoff has left.
_last_mode: str | None = None
_last_whales: list = []
_unmapped_until: dict[tuple[str, str], float] = {}
# E7 (the paragraph over NO_MARK_TTL_S): beside each candidate memo's
# `until`, the READ it was written on -- `_unmapped_memo[(whale, cid)]
# = (at, ttl)`, the memo's TTL as grown so far; `_no_mark_memo[(whale,
# cid)] = at` -- the instant his newer fill is judged against at the
# walk (_release_cand_memo) and the growth is judged from at the next
# unmapped read (_memo_unmapped). An `until` entry with no `at` beside
# it (written by hand) is released by any readable stamp: fail closed.
# `_no_mark_until` is the no_mark memo itself, _unmapped_until's shape.
# An expired entry is kept UNMAPPED_TTL_MAX_S past its `until` so the
# re-read can judge the growth, then pruned (_prune_cand_memos)
_unmapped_memo: dict[tuple[str, str], tuple[float, float]] = {}
_no_mark_until: dict[tuple[str, str], float] = {}
_no_mark_memo: dict[tuple[str, str], float] = {}
# the candidate memos' last persisted write (the instant and the
# signature), the terminal memos' idiom (_terminal_memo_last)
_cand_memo_last: dict[str, Any] = {"at": 0.0, "sig": None}
# THE TERMINAL MEMO (D1, 2026-09-06). The candidate walk spends its
# MAX_MARKETS_PER_TICK quote reads newest-touched first, and his
# newest-touched mapped markets are matches he trades to settlement:
# the 19:02Z census read `venue_halted` on 26 of 29 reads, every one
# MARKET_STATE_EXPIRED, re-read every tick while the open market behind
# them never reached a slot. A candidate whose quote read carried a
# TERMINAL state (ms.STATE_TERMINAL: expired, closed, terminated, the
# closing auction -- a market that has ended, a per-market fact) is
# remembered here per (whale, condition_id) for ms.UNMAPPED_TTL_S the
# way _unmapped_until remembers a market with no venue market, and
# skipped under `cand_terminal_skipped` until the TTL runs. NEVER for a
# HALTED / SUSPENDED / PREOPEN read (those reopen, and count toward the
# miss streak), and NEVER from an existing book's read (_tick_book never
# writes it: a book on an ended market is managed every tick until it
# closes).
_terminal_until: dict[tuple[str, str], float] = {}
# THE BOOK'S OWN TERMINAL MEMO (W1 / R4, 2026-09-07). An open book on a
# market the venue has EXPIRED, whose markets row still reads
# closed=false / resolved=false (48 of 64 in the 13:50Z census), cannot
# take step M's closing branch, so every tick read its quote
# (`venue_halted`, uncounted for the streak), got no mark, cancelled
# under `no_mark`, held -- and read its per-market position too
# (`snap_market_unreadable` on an expired market): two paced venue
# calls per book per tick. The 13:44Z tick read 26 of 27 books on
# MARKET_STATE_EXPIRED markets, >= 18 s of a 20.4 s tick, before a
# single candidate. A book whose BOOK read (_bbo, book=True) carried a
# TERMINAL state (ms.STATE_TERMINAL) is remembered here per (whale,
# condition_id) for ms.UNMAPPED_TTL_S -- the candidate memo's TTL --
# and its state string beside it (_terminal_book_state, the plan's
# `venue_terminal`); on the ticks inside the memo _tick_book skips the
# quote read AND the per-market read and writes the plan `no_plan`
# under the existing `no_mark` name (census `no_mark` as today, plus
# `book_terminal_skipped`). Step M (the markets row,
# _maybe_close_episode) still runs EVERY tick, so the close lands the
# tick the row reads closed. NEVER set on a HALTED / SUSPENDED /
# PREOPEN read (those reopen: such a book is read every tick as
# before), never on an unread state, never while the book has an order
# open (t.open_by_book / t.nonterminal: the cancel the first terminal
# read sends under `no_mark` must have landed first -- the memo is
# written on the NEXT terminal read, when nothing is open), never from
# a candidate read (that is _terminal_until's). Fail-closed: a terminal
# market accepts no order, and no order path is touched here.
_terminal_book_until: dict[tuple[str, str], float] = {}
_terminal_book_state: dict[tuple[str, str], str] = {}
# THE FULL-GAME MEMO (E1 re-review LOW-2). A candidate on a game whose
# books already hold the $2,500 opens nothing, and the first cut
# returned before books_seen, so every un-opened market of a full game
# was mapped, quote-read and charged a candidate slot again on every
# tick. The game key (_game_key_of's, the game alone) is remembered
# here for GAME_FULL_MEMO_S and every candidate on it is skipped BEFORE
# its venue read under `cand_game_full_skipped` until the memo runs; a
# game that frees up (a sibling reduced) is read again within the
# memo's minute. Only a game read FULL is memoised: an UNREADABLE game
# (a sibling's order nobody could read this tick) is a per-tick
# reading, retried by step O, and its markets are read again next
# tick. A candidate with no game key is never memoised (it is its own
# game and always has room).
GAME_FULL_MEMO_S = 60.0
_game_full_until: dict[tuple, float] = {}
# THE CANDIDATE'S REFUSAL, NAMED AND PERSISTED (W2 / P2, 2026-09-07;
# gap_planned_unopened.md section 0: 58 mapped markets, $218k in 24 h,
# had a shadow plan on an OPEN two-sided book while he traded and never
# a mirror_books row, and the lane's per-candidate verdict survived
# nowhere -- the census counts a name once per tick, the heartbeat
# keeps the last tick, four exits of _tick_candidate returned nothing).
# Every exit of _tick_candidate now returns its NAME (the census
# counters it already had are unchanged), and _walk_candidate writes
# one row per (whale, condition_id) TRANSITION to
# mirror_candidate_refusals (migration 054): a row when the name
# differs from the last one written for the pair, and again when the
# same name has stood for CAND_REFUSAL_RESTAMP_S (900 s, the memo
# class of _unmapped_until), so the writes are bounded by the active
# conditions over 900 s. `_cand_refusal_last` is the memo: (whale,
# cid) -> (name, stamp) of the last row WRITTEN -- a write that failed
# leaves it alone, so the next tick's same name is a transition again
# and the row is retried. The row carries only what the tick already
# holds (his net, the target, the mark, his level, the ask, the band,
# the long token, the book counts, his active conditions, the reads so
# far, the wall time so far): no venue call is made for it. A write
# failure never blocks the tick: `refusal_write_failed` on the census,
# logged once per process (the table is absent until 054 is applied;
# the workers never run migrations). A candidate with a book (books_seen)
# writes nothing: it is not a candidate with no book.
CAND_REFUSAL_RESTAMP_S = ms.UNMAPPED_TTL_S
# the one write's bound: past it the rows are dropped and named again on
# the next transition, exactly as a failed write (never a stop)
CAND_REFUSAL_WRITE_TIMEOUT_S = 5.0
_cand_refusal_last: dict[tuple[str, str], tuple[str, float]] = {}
_CAND_REFUSAL_MEMO_MAX = 8000
_cand_write_logged = False
# E5: the register unreadable (absent until 056 lands), logged once per process
_registered_logged = False
# E5 / P2: the two freeze reasons a frozen book may follow his exit under
FROZEN_EXIT_REASONS = frozenset({"placement_lost", "venue_ledger_disagree"})
# THE CANDIDATE WALK'S ROTATION CURSOR (W2 / P3). The walk read his
# conditions newest-fill-first up to MAX_MARKETS_PER_TICK, so on a busy
# evening (549-790 active conditions, capped_tick true in 7 of 9
# heartbeats 20:34Z-02:46Z 2026-09-06) the tail was never reached: a
# mapped market he last traded 10-20 min earlier sat behind dozens of
# fresher unmapped ones every tick. The order is now: the woken markets
# (his fill this poll, as before), then the candidates with a shadow
# plan (_shadow_planned) ahead of the rest, the two rotated together so
# a CAPPED tick RESUMES after the last candidate it walked for the
# whale -- every readable candidate is walked within
# ceil(readable / MAX_MARKETS_PER_TICK) ticks. The cursor is set only
# by a walk that broke on the cap (the candidates' budget or the soft
# guard): the last rotation candidate _walk_candidate was called for
# (a woken one never moves it); a walk that reached the end of its
# list clears it, so an uncapped tick keeps the order it always had
# (woken, planned, newest first), and a cursor no longer in the list
# restarts at the head, the planned first. The cap is unchanged (env
# may only lower it) and the memos (_unmapped_until, _terminal_until,
# the full-game memo, a market with a book) still skip BEFORE any read,
# spending no slot.
_cand_cursor: dict[str, str] = {}
# the rotation candidates the last capped walk read, in order (W2
# review): when the cursor candidate leaves his active list between
# ticks, the walk resumes after the last of these still on it
_cand_trail: dict[str, list[str]] = {}
# THE QUIET BOOKS' ROTATION (E6; the paragraph over VENUE_CALLS_PER_TICK).
# `_tick_seq` counts the ticks this process ran (past the backoff and
# the table guard). `_quiet_memo`: book id -> {seq: the tick its quote
# was last read on, quiet: whether that read left it ON TARGET with
# nothing placed and nothing open, at: the `at` of the plan THIS
# process last wrote on the row (a read's or a skip's)} -- the verdict
# is the last READ's, never a skip's plan write (a skip writes
# `book_quiet_skipped` on the row, which must not make the book hot
# next tick), and a row whose last_plan `at` is not the one this
# process wrote was written by another hand (a second process during a
# deploy, an operator, a row that is not the one the memo knew) and is
# read, never skipped. `_quiet_deferred`: the books that were DUE and
# skipped for budget, book id -> the tick it was deferred on, in the
# order they were deferred (a dict keeps insertion order: the queue is
# FIFO) -- each tick reads the head of the queue first, under
# max(quiet_budget, DEFERRED_MIN_PER_TICK) (_deferred_due). Both forget
# a book the moment it closes and any book the walk no longer lists
# (_forget_quiet, _forget_unlisted; review LOW-2). All three
# process-local, like _cand_cursor: an empty memo reads everything.
_tick_seq = 0
_quiet_memo: dict[int, dict] = {}
_quiet_deferred: dict[int, int] = {}
# E6 part 3: the terminal memos' one boot read, and the last write's
# instant and signature (the write is made only when the bounded
# snapshot changed, at most once per TERMINAL_MEMO_WRITE_S)
_terminal_memo_loaded = False
_terminal_memo_last: dict[str, Any] = {"at": 0.0, "sig": None}
# E6 part 1: the seconds this process spent inside _paced (the pacer's
# gap plus the request), summed per call across the worker threads;
# the tick snapshots it around the book walk for the timing block
_PACED_S = {"s": 0.0}
_PACED_LOCK = threading.Lock()
# The venue IGNORED the post-only flag once (executions on a post-only
# create): the flag is off for the rest of the process and the maker
# thesis is measured by price selection alone (spec X.L).
_POST_ONLY_OK = True
# The _copy_stop shape (live_executor:386-398), bounded the same way and
# for the same reason: the key space includes the whale.
_MIRROR_CENSUS: dict[str, int] = {}
_MIRROR_CENSUS_MAX = 400
_RECENT: deque = deque(maxlen=40)
_current_stats: dict | None = None
_sleep = asyncio.sleep          # indirection so a test can skip the cancel-read gap


def notify(condition_id: str | None = None) -> None:
    """Wake the loop for one market. Tolerant of None and of a blank
    id (a fill with no condition still wakes the poll); never raises,
    because the caller is the money decision on a mirrored whale's fill
    and a lost wake is a late tick, never a copy."""
    try:
        cid = str(condition_id or "").strip()
        if cid and len(_WOKEN) < _WOKEN_MAX:
            _WOKEN.add(cid)
        _WAKE.set()
    except Exception:  # noqa: BLE001 — a wake must not become the caller's exception
        log.debug("mirror_live: wake for %r dropped", condition_id, exc_info=True)


def _family(reason: str) -> str:
    for prefix, fam in _FAMILIES:
        if reason.startswith(prefix):
            return fam
    return reason


def _mirror_stop(reason: str, whale: str | None = None) -> None:
    """Count one named refusal or event: the bounded per-process dict
    (the _copy_stop shape) and the running tick's census. Returns None
    so `return _mirror_stop(...)` reads like the executor's."""
    w = (whale or "?").lower()[:40]
    key = f"{reason}|{w}"
    if key not in _MIRROR_CENSUS and len(_MIRROR_CENSUS) >= _MIRROR_CENSUS_MAX:
        key = f"{reason}|(overflow)"
    _MIRROR_CENSUS[key] = _MIRROR_CENSUS.get(key, 0) + 1
    if _current_stats is not None:
        c = _current_stats["census"]
        fam = _family(reason)
        c[fam] = c.get(fam, 0) + 1
    return None


def mirror_census_snapshot() -> dict:
    return dict(sorted(_MIRROR_CENSUS.items(), key=lambda kv: -kv[1]))


def _recent(book_id: int | None, what: str, **detail) -> None:
    _RECENT.append({"at": round(time.time(), 1), "book": book_id, "what": what,
                    **{k: v for k, v in detail.items() if v is not None}})


def _lock_for(book_id: int) -> asyncio.Lock:
    lk = _BOOK_LOCKS.get(book_id)
    if lk is None:
        if len(_BOOK_LOCKS) > 200:
            # closed books' locks are garbage -- but never a HELD one
            # (E2): the book walk runs several books at once, and a
            # lock dropped while held would hand its book a second,
            # free lock and two coroutines the same book
            for k in [k for k, v in _BOOK_LOCKS.items() if not v.locked()]:
                del _BOOK_LOCKS[k]
        lk = _BOOK_LOCKS[book_id] = asyncio.Lock()
    return lk


# THE GRAMMAR CLASS'S STATE IS READ, JUDGED AND WRITTEN BACK in
# _grammar_fill_check with a venue echo between the read and the write;
# under the parallel walk (E2) two grammar books could interleave and
# the second write would drop the first's verdict, so the whole
# read-judge-write runs under this lock (one grammar book is unverified
# at a time by the class's own rule, so it is rarely contended)
_GRAMMAR_LOCK = asyncio.Lock()


# ----------------------------------------------------------------- readings

def _num(v: Any) -> float | None:
    return rules._num(v)


def _rowcount(status) -> int:
    return ms._rowcount(status)


def _jsonish(v: Any) -> Any:
    if isinstance(v, (dict, list)) or v is None:
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


# the bound on a refused placement's receipt JSON (U13): the adapter's
# raw carries the venue's whole preview beside the guard's figures
_REFUSAL_RECEIPT_MAX = 4000


def _refusal_receipt(raw: Any) -> str:
    """The adapter's raw on a refused placement as the row's receipt
    JSON: json.dumps(raw, default=str), the shape _SQL_ORDER_PERSIST_ID
    stores, bounded to _REFUSAL_RECEIPT_MAX characters and ALWAYS valid
    JSON (a cut string would fail the column's jsonb cast and lose the
    whole update). Over the bound, the guard's scalars (expected_cost,
    venue_cost, the echoed price/quantity/side, cost_space, why) are
    kept whole and each nested value is replaced by its length under
    `truncated`; if even that is over, the head of the text is kept."""
    if not isinstance(raw, dict):
        raw = {"raw": raw}
    # NaN and the infinities are not JSON: json.dumps emits them by
    # default and the column's jsonb cast rejects the whole update, so
    # the refusal would never be recorded and the row would stay
    # 'placing' (U13 review, F3). They are written as their names.
    def _dumps(obj: Any) -> str:
        try:
            return json.dumps(obj, default=str, allow_nan=False)
        except ValueError:
            return json.dumps(_no_nan(obj), default=str, allow_nan=False)
    text = _dumps(raw)
    if len(text) <= _REFUSAL_RECEIPT_MAX:
        return text
    slim = {k: v for k, v in raw.items() if not isinstance(v, (dict, list))}
    slim["truncated"] = {k: len(_dumps(v)) for k, v in raw.items()
                         if isinstance(v, (dict, list))}
    text = _dumps(slim)
    if len(text) <= _REFUSAL_RECEIPT_MAX:
        return text
    head = text
    while True:
        out = json.dumps({"truncated": True, "head": head})
        if len(out) <= _REFUSAL_RECEIPT_MAX or not head:
            return out
        head = head[: len(head) * 2 // 3]


def _no_nan(obj: Any) -> Any:
    """`obj` with every non-finite float replaced by its name, recursively."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return "nan" if obj != obj else ("inf" if obj > 0 else "-inf")
    if isinstance(obj, dict):
        return {k: _no_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_no_nan(v) for v in obj]
    return obj


def _post_only_enabled() -> bool:
    return (_POST_ONLY_OK and os.environ.get("PMUS_MIRROR_POST_ONLY", "on")
            .strip().lower() not in _OFF_VALUES)


def _gtd_enabled() -> bool:
    return os.environ.get("PMUS_MIRROR_GTD", "off").strip().lower() in ("on", "1", "true", "yes")


def _iso(ts: float) -> str:
    return (datetime.fromtimestamp(ts, tz=timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"))


async def _state(pool, key: str) -> tuple[Any, str | None]:
    """(value, error): one ingestion_state key by the roster_auto
    _read_state idiom; an exception is the named error and the value
    None, so every caller decides its own fail-closed reading."""
    try:
        raw = await pool.fetchval("SELECT value FROM ingestion_state WHERE key=$1", key)
    except Exception as exc:  # noqa: BLE001 — unreadable is named, never guessed
        return None, type(exc).__name__
    if raw is None:
        return None, None
    if isinstance(raw, str):
        try:
            return json.loads(raw), None
        except ValueError:
            return None, "malformed"
    return raw, None


async def _write_state(pool, key: str, value: Any) -> None:
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb /* ml-state-write */",
        key, json.dumps(value, default=str))


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _rearm_at(value: Any, err: str | None, now: float | None = None) -> float | None:
    """The newest re-arm's instant (epoch) off the 'mirror_loss_rearm'
    row, or None: ABSENT, or MALFORMED -- unparseable JSON (_state's
    "malformed"), not an object, no `at`, an `at` that is not an ISO
    instant, or a naive one (no offset: it would be keyed to a guessed
    zone). Malformed reads as absent -- the FULL window, the wider one
    -- and is logged once per process (L1). A raised read is not this
    function's: the caller stops on it, as on the stop key's."""
    global _rearm_malformed_logged
    if value is None and err is None:
        return None
    at = value.get("at") if isinstance(value, dict) else None
    dt = None
    if isinstance(at, str):
        try:
            dt = datetime.fromisoformat(at.strip().replace("Z", "+00:00"))
        except ValueError:
            dt = None
    if dt is None or dt.tzinfo is None or (now is not None and dt.timestamp() > now + LOSS_REARM_SKEW_S):
        if not _rearm_malformed_logged:
            _rearm_malformed_logged = True
            log.warning("mirror_live: %s malformed (%s); read as absent, the full %d h window stands",
                        _STATE_LOSS_REARM, err or repr(at)[:80], int(LOSS_WINDOW_S // 3600))
        return None
    return dt.timestamp()


def _loss_window_start(now: float, rearm_at: float | None) -> float:
    """GREATEST(now - LOSS_WINDOW_S, rearm_at): the loss sum's window
    starts at the newest re-arm when one is inside the window, else at
    the window's own edge (L1). A re-arm older than the window changes
    nothing; None is no re-arm."""
    start = now - LOSS_WINDOW_S
    return start if rearm_at is None else max(start, min(rearm_at, now))


def _paced(fn, *args, **kwargs):
    """One venue call behind the process-wide measurement pacer, run in
    a worker thread by the caller (the shadow's _paced_bbo shape): ONE
    gap claimed, for the call's FIRST request. A call that makes a
    second request claims its own gap for it inside the adapter
    (pmus.submit_fok paced_pair=True, before the create), so the pair
    is at least a gap apart whatever the first request's HTTP latency
    (E2 review round 3, HIGH-1: a slot RESERVED for the create at claim
    time recorded it at the reserved time, not when it fired, and a
    preview slower than the gap -- the live case -- put the create and
    the next claimant inside one gap; round 2's two claims with the
    gate released between let contending BUYs interleave).

    THE WRITES GO THROUGH IT TOO (E2 review, HIGH-1). venue_pace's own
    note keeps the copy lane's money path off the pacer so a copy never
    queues behind measurement; this lane is a MAKER whose writes are
    cancels and rests, and under the parallel walk six books finishing
    their reads together placed together -- twelve HTTP inside one
    0.35 s gap, and a write 429 is `rate_limited`: the tick abandoned
    and every exit unmanaged for the 60 s backoff. So every cancel,
    placement and close of this lane claims its gaps here, and the
    venue sees one request per gap from this process whatever N is --
    half that while the 429 circuit holds (venue_pace.penalize)."""
    t0 = time.monotonic()
    try:
        pace(ms.READ_PACING_S)
        return fn(*args, **kwargs)
    finally:
        # E6: the seconds this call spent here (the gap and the request),
        # summed across the worker threads for the tick's timing block;
        # nothing about the call changes
        with _PACED_LOCK:
            _PACED_S["s"] += time.monotonic() - t0


def _rate_limited(t: "_Tick", whale: str | None, where: str) -> None:
    """A 429 was read at `where`: named on the census (`rate_limited`)
    and the process-wide pacer's circuit tripped (venue_pace.penalize:
    every lane's gap doubles for PENALTY_S). The caller decides what
    else the site does (a placement abandons the tick as before; a
    quote read refuses the market; a cancel goes to its reads)."""
    _mirror_stop("rate_limited", whale)
    until = venue_pace.penalize()
    log.warning("mirror_live: 429 on %s; pacer gap x%s for %ss (until monotonic %.0f)",
                where, venue_pace.PENALTY_MULT, venue_pace.PENALTY_S, until)


def _raw_rate_limit(raw: Any) -> bool:
    """Does the adapter's refusal `raw` name a venue 429? Read by the
    FIELDS pmus puts there and nothing else (E2 review round 4,
    MEDIUM-2): `status_code` (the SDK's status on a 4xx refusal,
    _post_only_refusal, an int), `error_type` (the exception's class
    name, as close_position's raw carries it) and `error` (the
    exception's text, which leads with the venue's words) -- the last
    two through ms.is_rate_limit's ANCHORED match. Never a substring
    over the raw as a whole: an `expected_cost` of 429.0, a `quantity`
    of 429, an order id or a price carrying '429' is not a rate limit,
    and a 400 whose message says "would cross at 0.429" is a crossing
    refusal that arms the take, nothing more.

    AN INT `status_code` IS AUTHORITATIVE (round 5, LOW d5): when the
    adapter recorded the SDK's own int status, that status decides --
    429 is a rate limit, any other int is not, whatever `error_type`
    or the text's head says (a 400 whose message begins "429 contracts
    exceeds the maximum order size" is a size refusal, and reading it
    as a 429 would abandon the tick and halve the lane for ten minutes
    on every recurrence). Only a raw with NO int status -- the adapter's
    close_failed raw, a cancel's error string -- falls back to
    `error_type` and the anchored text match."""
    if not isinstance(raw, dict):
        return False
    code = raw.get("status_code")
    if isinstance(code, int) and not isinstance(code, bool):
        return code == 429
    return ms.is_rate_limit(raw.get("error_type")) or ms.is_rate_limit(raw.get("error"))


# ------------------------------------------------------------------- SQL

_SQL_TABLE_GUARD = "SELECT 1 FROM mirror_books LIMIT 0 /* ml-table-guard */"
# THE 050 COLUMN, READ THE WAY THE WORKERS READ A COLUMN THAT MAY NOT
# EXIST YET (live_executor._position_row's orig_shares fallback): the
# workers never run migrations, so this code can reach production ahead
# of mirror_orders.intent. One probe per tick, before any statement that
# names the column; ABSENT (rules.column_missing: the driver's
# UndefinedColumnError or its text, and nothing else), the tick sends
# the 047-shaped statements below (`_SQL_*_047`), reads every order's
# intent off the book and the plan side, and the MIRROR_SHORTS knob is
# EFFECTIVELY OFF, named `short_column_absent` when the environment has
# it on -- never a raise. Any OTHER failure of the probe refuses the
# tick by name (`intent_guard_unreadable`, status degraded) exactly as
# the table guard does: a timeout on this one SELECT read as "absent"
# would turn the knob off for the tick and market-close every sole
# short book on a database blip (P2 rung S0 review). The text is the
# shadow's (ms.INTENT_GUARD_SQL, tag ml-intent-guard): both lanes probe
# with one statement and read one effective knob.
_SQL_INTENT_GUARD = ms.INTENT_GUARD_SQL
_SQL_ORDERS_OPEN_047 = """
SELECT o.id, o.book_id, o.whale, o.us_market_slug, o.kind, o.side, o.tif, o.post_only,
       o.his_level::float8 AS his_level, o.price::float8 AS price, o.wire::float8 AS wire,
       o.qty, o.order_id, o.state, o.venue_state, o.filled::float8 AS filled,
       o.booked_filled::float8 AS booked_filled, o.avg_px::float8 AS avg_px,
       o.taker_at_placement, o.pre_ids, o.reason, o.receipt,
       extract(epoch FROM o.placed_at)::float8 AS placed_ts
  FROM mirror_orders o
 WHERE o.state IN ('placing', 'open', 'unknown')
 ORDER BY o.placed_at, o.id /* ml-orders-open */
"""
# the 047 read plus the 050 column: o.receipt (U11, the order's dust_total
# rides on it) stays in its place and o.intent is appended after it
_SQL_ORDERS_OPEN = _SQL_ORDERS_OPEN_047.replace(
    "o.taker_at_placement, o.pre_ids, o.reason, o.receipt,",
    "o.taker_at_placement, o.pre_ids, o.reason, o.receipt, o.intent,")
# The order's cumulative SELL dust (rules.SELL_DUST_SHARES), kept on the
# row's own receipt JSON so it survives the poll: the worker re-reads
# every open order from the table each tick, and a per-poll delta that
# read the row flat would otherwise start from zero on every read
# (2026-09-06 fix review, money-path minor 1). No column, no migration:
# the receipt is the venue's placement response and this rides beside it.
_SQL_ORDER_DUST = """
UPDATE mirror_orders
   SET receipt = COALESCE(receipt, '{}'::jsonb) || jsonb_build_object('dust_total', $2::float8),
       updated_at = now()
 WHERE id = $1 /* ml-order-dust */
"""
_SQL_BOOK_COLS = """
SELECT b.id, b.whale, b.condition_id, b.us_market_slug, b.game_key, b.long_asset,
       b.other_asset, b.intent, b.map_source, b.ratio::float8 AS ratio,
       b.anchor_usd::float8 AS anchor_usd, b.standing_row_id, b.episode, b.flat_reopens,
       b.state, b.frozen_reason, extract(epoch FROM b.frozen_at)::float8 AS frozen_ts,
       b.frozen_ticks, b.target, b.ledger_net, b.venue_net::float8 AS venue_net,
       b.open_order_id, extract(epoch FROM b.take_armed_at)::float8 AS take_armed_ts,
       b.last_reason, b.last_plan, b.gross_buy_usd::float8 AS gross_buy_usd,
       b.gross_sell_usd::float8 AS gross_sell_usd,
       b.peak_exposure_usd::float8 AS peak_exposure_usd, b.avg_cost::float8 AS avg_cost,
       b.realized_pnl::float8 AS realized_pnl, b.settled_pnl::float8 AS settled_pnl,
       extract(epoch FROM b.opened_at)::float8 AS opened_ts,
       extract(epoch FROM b.updated_at)::float8 AS updated_ts
  FROM mirror_books b
"""
_SQL_BOOKS_OPEN = _SQL_BOOK_COLS + " WHERE b.state <> 'closed' ORDER BY b.updated_at, b.id /* ml-books-open */"
_SQL_BOOK_READ = _SQL_BOOK_COLS + " WHERE b.id = $1 /* ml-book-read */"
_SQL_BOOKS_COUNT = """
SELECT count(*) FILTER (WHERE state <> 'closed') AS live,
       count(*) FILTER (WHERE opened_at > now() - interval '24 hours') AS today
  FROM mirror_books /* ml-books-count */
"""
# THE DAY READ COUNTS WHAT IS RESTING, not only what filled. cash_usd is
# written on BOOKED FILLS alone (_SQL_ORDER_CASH, from _book_fill), and
# _place takes a placement's notional off t.mirror_day for ITS tick only,
# so an unfilled post-only rest counted against the day on no later
# tick: with the room nearly spent, up to four more books could each
# rest the same last dollars on later ticks, a worst-case gross BUY
# notional standing in a rolling 24 h of MIRROR_DAY_USD + 4 x
# LIVE_MAX_CLIP_USD ($2,250) against a rail the owner was told is $1,250
# (adversarial pre-flight 2026-09-05, before the owner's "switch on the
# mirror system 100%" order). TWO COLUMNS, read apart by _global_guards:
# `filled`, the cash of every BUY_LONG row placed in the window, and
# `open`, the unfilled remainder at the cent on the wire (the notional
# _place reserves) of every such row that may still fill -- the
# non-terminal states, exactly the set mirror_orders_one_open_per_book
# and _SQL_ORDERS_OPEN name; 'lost' and 'rejected' are terminal and
# nothing of theirs can fill. A partial fill is not counted twice: its
# filled part is in cash_usd and its remainder is qty - booked_filled,
# the cursor _book_fill advances in the transaction that writes the
# cash. They are two columns and not one sum because the two readers
# want different figures (review of the first cut, 2026-09-05): the
# BLOCK (increase_block = 'mirror_day_cap') fires on `filled` alone,
# money actually spent, because _reconcile_open answers a block by
# cancelling every resting BUY -- a block that read the rests would
# have cancelled the very rests it counted, every book alternating
# cancel / re-rest every other tick for as long as the day stayed full
# by rests, unbounded per hour (that cancel is not a replace and spends
# no budget), each cycle restarting the take wait and the TTL, the
# book absent from the venue half the time; the SIZING room
# (t.mirror_day) is MIRROR_DAY_USD - filled - open, so a new rest can
# never take room a standing rest already holds. The per-tick
# decrement in _place stays: it protects within a tick, this read
# protects across ticks.
_SQL_MIRROR_DAY_047 = """
SELECT COALESCE(sum(cash_usd), 0)::float8 AS filled,
       COALESCE(sum(CASE WHEN state IN ('placing', 'open', 'unknown')
                         THEN (qty - COALESCE(booked_filled, 0)) * wire
                         ELSE 0 END), 0)::float8 AS open
  FROM mirror_orders
 WHERE side = 'BUY_LONG' AND placed_at > now() - interval '24 hours' /* ml-mirror-day */
"""
# THE DAY READ IN COLLATERAL SPACE (P2 rung S0, brief D1 / 3.5). A row
# that GROWS a leg is a long BUY (plan side BUY_LONG carrying the wire
# intent BUY_LONG -- every row written before 050 reads as one through
# the column's DEFAULT) or a short OPEN / ADD (wire intent BUY_SHORT,
# whose plan side is SELL_LONG); a short book's cover rows (plan side
# BUY_LONG, wire intent SELL_SHORT) and a long book's sells are exits
# and count nothing. The resting remainder is priced at the cost a
# share the wire commits: the wire on a long, ONE MINUS the wire on a
# short (the wire is the CONTRACT price the venue is sent; the
# collateral is its complement, le.wire_limit). `filled` reads cash_usd,
# which _book_fill already writes as le.fill_cash on either sign.
_SQL_MIRROR_DAY = """
SELECT COALESCE(sum(cash_usd), 0)::float8 AS filled,
       COALESCE(sum(CASE WHEN state IN ('placing', 'open', 'unknown')
                         THEN (qty - COALESCE(booked_filled, 0))
                              * (CASE WHEN intent = 'ORDER_INTENT_BUY_SHORT' THEN 1 - wire ELSE wire END)
                         ELSE 0 END), 0)::float8 AS open
  FROM mirror_orders
 WHERE ((side = 'BUY_LONG' AND intent = 'ORDER_INTENT_BUY_LONG') OR intent = 'ORDER_INTENT_BUY_SHORT')
   AND placed_at > now() - interval '24 hours' /* ml-mirror-day */
"""
# THE 24 H LOSS FIGURE COUNTS A DOLLAR ONCE (E3, the day reconciliation
# of 2026-09-06 23:10Z). settled_pnl is the venue's WHOLE-position
# figure for the standing row -- _close_settled cross-checks it against
# `own = realized_pnl + shares x (payout - avg)` under
# book_settle_disagree -- so it already holds the book's realized part,
# and the first cut, which summed realized_pnl over every book updated
# in 24 h PLUS settled_pnl over every book closed-settled in 24 h,
# counted a settled book's sales twice: tonight book 16 (realized
# -244.75, settled -315.40 = sales 349.25 - cost 664.65 + 157 x 0),
# 3 (+2.48 / +156.17), 19 (-2.11 / -41.46) and 22 (+14.64 / +19.74)
# put the 22:22Z reading at -2,445 where the truth was about -2,215:
# it failed closed (too pessimistic), but the figure the owner was
# told was wrong. THE RULE: `lost` is settled_pnl over the books
# closed-settled in the window (state 'closed', settled_pnl not null,
# closed_at in 24 h) PLUS realized_pnl over every OTHER book updated
# in the window -- the open, frozen and closing books, and the closed
# books whose close was cashed_out / cancelled with settled_pnl NULL,
# whose P&L lives only in realized_pnl. A closed-settled book counts
# by its settled figure or not at all: the exclusion reads the row's
# state and settled_pnl, never its closed_at, so a settlement that has
# left the window brings no realized part back in. The settled branch
# clocks a row by closed_at, or by updated_at where a row has none:
# every close the worker writes stamps both (ml-book-settled,
# ml-book-state), so only a hand-edited row reads that way, and it
# would otherwise vanish from BOTH sums (E3 review, minor 2). `books`
# and the MIRROR_LOSS_STOP_USD stop write are as they were.
# THE WINDOW STARTS AT THE NEWEST RE-ARM (L1, the paragraph over
# _STATE_LOSS_REARM): the statement's ONE parameter is the window's
# start, GREATEST(now - LOSS_WINDOW_S, rearm_at) computed by the worker
# (_loss_window_start) from the tick's clock, and both arms and the
# count clock by it -- the text holds no interval of its own, so the
# three predicates cannot drift apart. The tick's clock is taken at
# the tick's start, so a long tick reads a window no shorter than
# now() - 24 h would (fail closed).
_SQL_LOSS_SUM = """
SELECT COALESCE((SELECT sum(settled_pnl) FROM mirror_books
                  WHERE state = 'closed' AND settled_pnl IS NOT NULL
                    AND COALESCE(closed_at, updated_at) > $1::timestamptz), 0)::float8
     + COALESCE((SELECT sum(realized_pnl) FROM mirror_books
                  WHERE updated_at > $1::timestamptz
                    AND NOT (state = 'closed' AND settled_pnl IS NOT NULL)), 0)::float8
       AS lost,
       (SELECT count(*) FROM mirror_books
         WHERE updated_at > $1::timestamptz) AS books /* ml-loss-sum */
"""
# A TAKE'S CANCEL IS A RE-QUOTE AND SPENDS THE REPLACE BUDGET. The count
# read reason = 'replace' alone, but the take arm cancels the rest under
# reason 'take' (_act's keep branch) and the book rests anew once the
# IOC is done, so a book could cycle rest -> MIRROR_TAKE_AFTER_S -> take
# -> new rest about thirty times an hour, bounded only by the per-tick
# ops budget, while MIRROR_MAX_REPLACES_PER_HOUR was meant to bound
# re-quotes (adversarial pre-flight 2026-09-05, before the owner's
# "switch on the mirror system 100%" order). Both reasons count now, and
# only the RESTS: the IOC a take places is its own row finished under
# reason 'take' as well (_place finishes it under its kind), and
# counting it would charge one re-quote twice.
_SQL_REPLACES = """
SELECT count(*) FROM mirror_orders
 WHERE book_id = $1 AND reason IN ('replace', 'take') AND tif IN ('GTC', 'GTD')
   AND done_at > now() - interval '1 hour' /* ml-replaces */
"""
# The flatten rest that starts the slippage clock is the FIRST one of
# the CURRENT vanish: still standing, or placed at or after the tick
# the book entered the vanish (the plan's vanish_since). The predicate
# bounds it to this vanish -- an unbounded min() over every flatten
# rest the book ever placed let a rest cancelled two hours ago (he
# came back, the book went on) send a fresh vanish straight to
# close_position with no rest first (step-9 review). Within the vanish
# the FIRST rest is the clock: a max() restarted the wait on every
# re-quote, so a rest re-priced at +200 s reached the slippage path at
# +500 s instead of +300 s, and a book he had LEFT sat at his level
# for as long as the market kept moving (step-9 re-review; critic C16;
# owner order 2026-09-02, "go for it, let's get this working").
_SQL_FLATTEN_REST_SINCE = """
SELECT min(extract(epoch FROM placed_at))::float8 FROM mirror_orders
 WHERE book_id = $1 AND kind = 'flatten_vanished' AND tif IN ('GTC', 'GTD')
   AND (state IN ('placing', 'open', 'unknown')
        OR placed_at >= to_timestamp($2)) /* ml-flatten-since */
"""
_SQL_ORDER_INSERT_047 = """
INSERT INTO mirror_orders (book_id, whale, us_market_slug, kind, side, tif, post_only,
                           good_till, his_level, price, wire, qty, state, pre_ids,
                           target_at_place, ledger_at_place, bid_at_place, ask_at_place,
                           reason)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'placing', $13::jsonb,
        $14, $15, $16, $17, $18)
RETURNING id /* ml-order-insert */
"""
# the 050 shape: the wire intent as the LAST parameter, so every
# positional reader of the 047 statement keeps its meaning
_SQL_ORDER_INSERT = """
INSERT INTO mirror_orders (book_id, whale, us_market_slug, kind, side, tif, post_only,
                           good_till, his_level, price, wire, qty, state, pre_ids,
                           target_at_place, ledger_at_place, bid_at_place, ask_at_place,
                           reason, intent)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'placing', $13::jsonb,
        $14, $15, $16, $17, $18, $19)
RETURNING id /* ml-order-insert */
"""
_SQL_ORDER_PERSIST_ID = """
UPDATE mirror_orders SET order_id = $2, state = 'open', venue_state = $3,
       receipt = $4::jsonb, updated_at = now()
 WHERE id = $1 /* ml-order-persist */
"""
_SQL_ORDER_ADOPT = """
UPDATE mirror_orders SET order_id = $2, state = 'open', reason = $3, updated_at = now()
 WHERE id = $1 AND order_id IS NULL /* ml-order-adopt */
"""
_SQL_ORDER_STATE = """
UPDATE mirror_orders SET state = $2, venue_state = $3, reason = $4, maker = $5,
       done_at = CASE WHEN $2 IN ('filled', 'cancelled', 'expired', 'rejected', 'lost')
                      THEN now() ELSE done_at END,
       order_id = COALESCE($6, order_id), updated_at = now()
 WHERE id = $1 /* ml-order-state */
"""
# A REFUSED PLACEMENT KEEPS THE ADAPTER'S RAW (U13, 2026-09-06). The
# first two short books were refused fifteen times over as
# place_refused:preview_mismatch (mirror_orders 49-63) and the rows
# carried nothing but the status: the expected and venue figures the
# guard compared were in the adapter's raw and were dropped here, so the
# cause (the venue's preview stating the notional, not the collateral)
# had to be reconstructed from the code. The receipt column is the same
# shape _SQL_ORDER_PERSIST_ID stores on a placed row; _SQL_ORDER_STATE
# has no receipt slot, so the refusal has its own statement.
_SQL_ORDER_REFUSED = """
UPDATE mirror_orders SET state = 'rejected', venue_state = $2, reason = $3,
       receipt = $4::jsonb, done_at = now(), updated_at = now()
 WHERE id = $1 /* ml-order-refused */
"""
_SQL_ORDER_CURSOR = """
UPDATE mirror_orders SET booked_filled = booked_filled + $2, filled = $3, avg_px = $4,
       updated_at = now()
 WHERE id = $1 AND booked_filled = $5 /* ml-order-cursor */
"""
_SQL_ORDER_CASH = """
UPDATE mirror_orders SET cash_usd = cash_usd + $2, realized = realized + $3,
       taker_at_placement = taker_at_placement OR $4, updated_at = now()
 WHERE id = $1 /* ml-order-cash */
"""
_SQL_ORDER_REASON = "UPDATE mirror_orders SET reason = $2, updated_at = now() WHERE id = $1 /* ml-order-reason */"
_SQL_ADDS_SEQ = """
SELECT count(*) FROM live_orders lo,
       jsonb_array_elements(COALESCE(lo.raw->'adds', '[]'::jsonb)) a
 WHERE lo.id = $1 AND a->>'order_id' = $2 /* ml-adds-seq */
"""
_SQL_STANDING_READ = """
SELECT status, lane, filled_shares::float8 AS filled_shares, fill_price::float8 AS fill_price,
       pnl::float8 AS pnl, raw FROM live_orders WHERE id = $1 /* ml-standing-read */
"""
_SQL_STANDING_NAME = """
UPDATE live_orders SET raw = jsonb_set(COALESCE(raw, '{}'::jsonb), '{mirror,named}', 'true'::jsonb)
 WHERE id = $1 AND lane = 'mirror' AND status = 'filled' /* ml-standing-name */
"""
_SQL_LEDGER_IDS = """
SELECT order_id FROM live_orders WHERE us_market_slug = $1 AND order_id IS NOT NULL /* ml-ledger-ids */
"""
# R7 -- WIDENING THIS QUERY TO EVERY NON-MIRROR LANE IS DEFERRED, and
# the query below is deliberately unchanged. Two shapes were built and
# both were driven into a defect, so the widening is left undone rather
# than landed nearly-right:
#
#   * WIDENED AND UNSIGNED. `ms.account_positions` returns the slug's
#     SIGNED netPosition -- the venue nets a condition's two tokens --
#     so an unsigned sum of every foreign row is not an explanation: a
#     per-fill PAIR (50 long-token shares and 50 other-token shares)
#     nets to 0 at the venue and sums to 100 here, `explained` overshoots
#     by 100 and the book freezes. That is the freeze R7 exists to
#     remove, on the reversal path R7 itself names.
#   * WIDENED AND SIGNED BY THE TWO TOKEN IDS. Signing by `asset = $2 /
#     $3` fixes the pair and drops the DESK's own rows, because
#     `live_executor._execute_manual_slug` writes a slug-direct buy as
#     `asset = 'slug:<us_market_slug>'`, which is neither token. Shares
#     this query explains TODAY would stop being explained, so a book
#     that is live now would freeze for ever (`_thaw` needs venue ==
#     explained, and there is no admin unfreeze) -- a REGRESSION, and on
#     the desk's own positions.
#
# Signing such a row correctly means reading its side back out of
# untyped JSON (`raw->'preview'->>'intent'`) on rows this worker does not
# write, and that sign decides both a freeze and the number `mi.plan`
# holds the ledger against -- get it backwards and the lane's venue
# share reads too large and the sale is sized too big, the direction R3
# exists to close. Not provable here, so not taken.
#
# What stands until it is: the desk's rows are explained, and a foreign
# per-fill row on a slug a mirror book holds still freezes that book.
# That freeze is HEAD's behaviour and it is contained (cancel-only,
# named `venue_ledger_disagree`), where a wrong sign is not.
_SQL_MANUAL_SHARES = """
SELECT COALESCE(sum(filled_shares), 0)::float8 FROM live_orders
 WHERE us_market_slug = $1 AND COALESCE(whale_username, '') = 'manual'
   AND status IN ('filled', 'exiting') /* ml-manual-shares */
"""
# THE OPERATOR REGISTER (E5 / P1, 2026-09-07; migration 056). A row is
# the owner's dated, named statement that `shares` on the slug are
# outside the book -- a lost placement response whose fill the venue
# reports and the ledger never booked (book 77, Shelton/Tsitsipas:
# venue 1,128, ledger 0, manual 0 since 2026-09-06). Written ONLY by
# the render-ops `mirror-register` dispatch (confirm=DO), from the
# book's own slug / asset / condition, after the preset printed venue
# vs ledger vs manual and refused unless the figure equals venue -
# ledger - manual exactly; the worker never writes it and never adopts
# a fill into it. Read beside _SQL_MANUAL_SHARES, the same shape, and
# treated exactly as `manual` in the venue == ledger comparison:
# explained = ledger + manual + registered. Signed like the ledger
# (negative is the short side); a sum whose sign is against the book's
# leg is not read (_tick_book, `registered_sign_refused`): a register
# row that made the ledger appear to hold what the venue does not
# would have the live plan sell shares that are not there.
_SQL_REGISTERED_SHARES = """
SELECT COALESCE(sum(shares), 0)::float8 FROM mirror_registered_positions
 WHERE us_market_slug = $1 /* ml-registered-shares */
"""
# THE CO-HOLD READ OF A FROZEN EXIT (E5 / P2): a LIVE non-mirror row on
# the slug that is not the desk's `manual` sleeve -- the copy lane's
# per-fill position the R7 paragraph above leaves unexplained. The
# venue's number is then not the book's alone, and a reduce sized on
# it would sell that lane's shares under this book: refused by name
# (`frozen_coheld`), never sized around. Unreadable refuses too.
_SQL_SLUG_COHELD = """
SELECT EXISTS (
  SELECT 1 FROM live_orders
   WHERE us_market_slug = $1 AND COALESCE(lane, '') <> 'mirror'
     AND (COALESCE(whale_username, '') <> 'manual' OR status NOT IN ('filled', 'exiting'))
     AND status IN ('filled', 'submitting', 'exiting')) /* ml-slug-coheld */
"""
# E5 review M1: a `manual` row in a live state _SQL_MANUAL_SHARES does
# not count ('submitting': its fill on the venue, its row not yet
# filled) is co-held too -- the venue's surplus is the desk's, not the
# lost response's. The bound a placement_lost book may sell up to: its
# ledger plus what its own lost rows asked for (a 'lost' row, or a
# 'placing' row whose response was lost) -- a surplus past that is
# explained by nothing the book knows, and is refused by name
# (`frozen_venue_unexplained`), never sold under the book. The rows
# come back with their side: only a row that ADDS to the leg explains
# a surplus (E5 review v3, V3-1: a lost CLOSE -- the seven books' own
# shape after P3 -- explains nothing the book holds; _lost_bound keys
# on _order_action, never on the wire side alone)
_SQL_LOST_QTY = """
SELECT side, qty FROM mirror_orders
 WHERE book_id = $1 AND (state = 'lost' OR (state = 'placing' AND order_id IS NULL)) /* ml-lost-qty */
"""
# a JSON fragment merged onto the order's receipt (the _SQL_ORDER_DUST
# shape, generalised): the frozen reduce's excess over the ledger, and
# the venue's own reading a lost close was marked 'lost' on (E5 / P3)
_SQL_ORDER_RECEIPT = """
UPDATE mirror_orders
   SET receipt = COALESCE(receipt, '{}'::jsonb) || $2::jsonb, updated_at = now()
 WHERE id = $1 /* ml-order-receipt */
"""
# A LIVE per-fill row is a claim whatever its age (spec A: it keeps its
# own exit path until it is cashed_out or settled); only the NAMED
# error rows age out of the 48 h window. The window once wrapped both
# clauses, so a three-day-old live position on the same game or the
# other outcome no longer refused admission (step-9 review).
_SQL_LEGACY_ROW = """
SELECT EXISTS (
  SELECT 1 FROM live_orders
   WHERE COALESCE(lane, '') <> 'mirror'
     AND (asset = $1 OR us_market_slug = $2 OR ($3::text IS NOT NULL AND us_market_slug LIKE $4))
     AND (status IN ('filled', 'submitting', 'exiting')
          OR (status = 'error' AND (error LIKE 'venue holds a POSITION%'
                                    OR error LIKE 'ORPHAN FILL RECORDED%'
                                    OR error LIKE 'venue has no record of order%')
              AND placed_at > now() - interval '48 hours'))) /* ml-legacy-row */
"""
_SQL_SLUG_RECENT = """
SELECT EXISTS (
  SELECT 1 FROM live_orders
   WHERE us_market_slug = $1 AND COALESCE(lane, '') <> 'mirror'
     AND status NOT IN ('rejected', 'unfilled')
     AND placed_at > now() - interval '60 minutes') /* ml-slug-recent */
"""
_SQL_UNDERDOG = """
SELECT EXISTS (
  SELECT 1 FROM live_orders
   WHERE COALESCE(whale_username, '') = 'underdog' AND asset = ANY($1::text[])
     AND status IN ('filled', 'submitting', 'exiting')) /* ml-underdog */
"""
_SQL_KALSHI = "SELECT 1 FROM kalshi_claims WHERE asset = $1 LIMIT 1 /* ml-kalshi */"
_SQL_MARKET = "SELECT closed, resolved, resolved_prices FROM markets WHERE condition_id = $1 /* ml-market */"
_SQL_TOKEN_INDEX = "SELECT outcome_index FROM market_tokens WHERE token_id = $1 /* ml-token-index */"
# ONE statement for both lanes since W2 / P1 (the shadow's map_market
# reads the same catalogue row to fill a mapping whose long side his
# fills never touched); the text and its tag are unchanged
_SQL_SIBLING_TOKEN = ms.SIBLING_TOKEN_SQL
_SQL_WHALE_ADDRESS = "SELECT address FROM whales WHERE lower(username) = $1 LIMIT 1 /* ml-whale-address */"
# THE SHADOW'S PLAN PER CANDIDATE, ONE READ PER WHALE PER TICK (W2 / P3):
# the newest shadow row of every condition in the lookback, so the
# candidate walk reads the markets the shadow planned a leg on first
# (a would_side, or a target that is not 0). Unreadable: no plan is
# known and the walk rotates over his conditions as they come.
_SQL_SHADOW_PLANNED = """
SELECT DISTINCT ON (condition_id) condition_id, would_side, target
  FROM mirror_shadow
 WHERE whale = $1 AND at >= now() - ($2::float8 * interval '1 hour')
 ORDER BY condition_id, at DESC /* ml-shadow-planned */
"""
# THE CANDIDATE'S REFUSAL, PERSISTED (W2 / P2, migration 054): the rows
# a tick collected, written ONCE at the end of the walk from one JSON
# array (a busy evening names 500+ unread candidates on its first tick;
# one round trip, never one per row). `at` is the tick's clock.
_SQL_CAND_REFUSALS = """
INSERT INTO mirror_candidate_refusals
       (whale, condition_id, us_slug, refusal, at, his_net, target, mark, his_px, ask, band,
        long_asset, long_from, books_live, opened_today, active_conditions, cand_reads, tick_s)
SELECT r.whale, r.condition_id, r.us_slug, r.refusal, to_timestamp(r.at_ts), r.his_net,
       r.target, r.mark, r.his_px, r.ask, r.band, r.long_asset, r.long_from, r.books_live, r.opened_today,
       r.active_conditions, r.cand_reads, r.tick_s
  FROM jsonb_to_recordset($1::jsonb) AS r(
       whale text, condition_id text, us_slug text, refusal text, at_ts float8,
       his_net float8, target int, mark float8, his_px float8, ask float8, band float8,
       long_asset text, long_from text, books_live int, opened_today int, active_conditions int,
       cand_reads int, tick_s float8) /* ml-cand-refusals */
"""
_SQL_SHADOW_LATEST = """
SELECT target, target_raw::float8 AS target_raw, capped, ratio::float8 AS ratio,
       his_net::float8 AS his_net, extract(epoch FROM at)::float8 AS at_ts
  FROM mirror_shadow WHERE whale = $1 AND condition_id = $2
 ORDER BY at DESC LIMIT 1 /* ml-shadow-latest */
"""
_SQL_REAPER_TOUCHED = """
SELECT count(*) FROM live_orders lo JOIN mirror_orders mo ON mo.order_id = lo.order_id
 WHERE COALESCE(lo.lane, '') <> 'mirror' /* ml-reaper-touched */
"""
_SQL_BOOK_PLAN = """
UPDATE mirror_books SET target = $2, target_raw = $3, his_net = $4, his_long = $5,
       his_other = $6, snap_long = $7, snap_other = $8, drift = $9, his_level = $10,
       venue_net = $11, last_reason = $12, last_plan = $13::jsonb, updated_at = now()
 WHERE id = $1 /* ml-book-plan */
"""
# A QUIET SKIP WRITES ITS NAME AND ITS PLAN, NOTHING ELSE (E6 review,
# LOW-1): the plan write above with no reading nulled his_net /
# his_long / his_other / snap_* / drift / venue_net / target_raw on the
# row two ticks in three, and the `zz book` rows and `planned-unopened`
# (his_net < 0) read them. The last read's figures stand until the next
# read.
_SQL_BOOK_SKIP = """
UPDATE mirror_books SET last_reason = $2, last_plan = $3::jsonb, updated_at = now()
 WHERE id = $1 /* ml-book-skip */
"""
# THE FIRST REASON STICKS: a book frozen 'overfill' whose venue then
# disagrees with its emptied ledger is still the overfill, not the
# disagreement; the later name rides in last_reason and the census.
_SQL_BOOK_FREEZE = """
UPDATE mirror_books SET frozen_reason = CASE WHEN state = 'frozen' THEN frozen_reason ELSE $2 END,
       state = 'frozen', frozen_at = COALESCE(frozen_at, now()),
       frozen_ticks = frozen_ticks + 1, last_reason = $2, updated_at = now()
 WHERE id = $1 AND state IN ('live', 'frozen') /* ml-book-freeze */
"""
_SQL_BOOK_THAW = """
UPDATE mirror_books SET state = 'live', frozen_reason = NULL, frozen_at = NULL,
       updated_at = now()
 WHERE id = $1 AND state = 'frozen' /* ml-book-thaw */
"""
_SQL_BOOK_STATE = """
UPDATE mirror_books SET state = $2, last_reason = $3,
       closed_at = CASE WHEN $2 = 'closed' THEN now() ELSE closed_at END, updated_at = now()
 WHERE id = $1 /* ml-book-state */
"""
_SQL_BOOK_OPEN_ORDER = "UPDATE mirror_books SET open_order_id = $2, updated_at = now() WHERE id = $1 /* ml-book-open-order */"
# the one-way step off an exact-copy book (rules.step_ratio): written
# for the book's life, read back from the row on every later tick
_SQL_BOOK_RATIO = "UPDATE mirror_books SET ratio = $2, updated_at = now() WHERE id = $1 /* ml-book-ratio */"
# THE FIRST REFUSAL STARTS THE TAKE CLOCK: an arm already set is kept,
# never re-stamped. Every post-only 400 once wrote now(), and _act reads
# the arm BEFORE it re-places, so at the 30 s poll a book that kept
# crossing carried a 30-second-old arm on every tick and never took
# (twelve refusals over 330 s, no IOC; step-9 re-review). An accepted
# rest, a finished order or a lost one still clears it (_disarm_take).
_SQL_BOOK_ARM = """
UPDATE mirror_books SET take_armed_at = CASE WHEN $2 THEN COALESCE(take_armed_at, now()) ELSE NULL END,
       updated_at = now()
 WHERE id = $1 /* ml-book-arm */
"""
_SQL_BOOK_LEDGER_BUY = """
UPDATE mirror_books SET ledger_net = $2, avg_cost = $3, gross_buy_usd = $4,
       peak_exposure_usd = $5, updated_at = now()
 WHERE id = $1 /* ml-book-ledger-buy */
"""
_SQL_BOOK_LEDGER_SELL = """
UPDATE mirror_books SET ledger_net = $2, gross_sell_usd = $3, realized_pnl = $4,
       updated_at = now()
 WHERE id = $1 /* ml-book-ledger-sell */
"""
_SQL_BOOK_SETTLED = """
UPDATE mirror_books SET settled_pnl = $2, own_book_pnl = $3, settle_disagree = $4,
       state = 'closed', last_reason = $5, closed_at = now(), updated_at = now()
 WHERE id = $1 /* ml-book-settled */
"""
_SQL_BOOK_REOPENS = "UPDATE mirror_books SET flat_reopens = $2 WHERE id = $1 /* ml-book-reopens */"


# --------------------------------------------------------------- the tick

@dataclass
class _Tick:
    pool: Any
    pmus: Any
    http: Any
    now: float
    stats: dict
    mode: str = MODE_SAFE
    mode_db_refusal: str = "mode_db_off"
    cancel_all: str | None = None          # every open order is cancelled under this name
    increase_block: str | None = None      # a global increase-only refusal
    allow: set = field(default_factory=set)
    narrow: set | None = None
    narrow_unreadable: bool = False
    demoted: set = field(default_factory=set)
    demoted_unreadable: bool = False
    flatten_all: bool = False
    # S4: the read-back proof as the tick read it (None when absent or
    # malformed), the read's error, and whether a probe ran this tick
    s4_proof: dict | None = None
    s4_proof_err: str | None = None
    s4_probed: bool = False
    positions: dict | None = None
    open: list | None = None
    open_error: str | None = None
    open_tried: bool = False
    protected: set | None = None
    protected_tried: bool = False
    ratios: dict = field(default_factory=dict)
    day_room: float | None = None
    total_room: float | None = None
    mirror_day: float | None = None
    ops: int = 0
    reads: int = 0                              # every quote read this tick, books and candidates
    # THE CANDIDATE WALK'S OWN QUOTE BUDGET (U12, 2026-09-06). The book
    # walk runs first and every live book is read every tick, so a
    # budget shared with it was a count cap on books by another road:
    # with MAX_MARKETS_PER_TICK live books no candidate was ever read.
    # Candidates break on THIS counter against MAX_MARKETS_PER_TICK
    # (`capped_tick`); the book walk is unbounded in reads -- exits must
    # be managed -- and `tick_s` on the stats shows the tick stretching
    cand_reads: int = 0
    misses: int = 0
    abandoned: bool = False
    # D1: the per-match rows ms.his_fills collapsed under a net-leg row
    # across every fills read this tick (books and candidates), and their
    # shares; published as the `fills_dedup` block (_publish_fills_dedup)
    fills_dedup_rows: int = 0
    fills_dedup_shares: float = 0.0
    # L1: the loss window this tick read (sum, books, limit, since,
    # rearmed_at -- the receipt's fields), None on a tick that never
    # read it; tick_once hands it to the mode line (_last_loss)
    loss: dict | None = None
    # L2: the re-arm key's ONE read this tick ((value, err) off _state),
    # made by _global_guards for the sleeve breaker and reused by
    # _loss_stop; None on a tick that never read it
    rearm: tuple | None = None
    # L2: the stop key's ONE read this tick ((value, err)), made before
    # the sleeve's read and reused by the stop step; None if never read
    stop: tuple | None = None
    # L2: the copy sleeve's breaker as this tick read it over the
    # mirror's window (sum, limit, since), None on a tick that never
    # read it; tick_once hands it to the mode line (_last_sleeve)
    sleeve: dict | None = None
    # the venue's own market state on every quote read this tick, as
    # the venue spells it (MARKET_STATE_OPEN, MARKET_STATE_HALTED, ...)
    venue_states: Counter = field(default_factory=Counter)
    # slug -> the state its LAST quote read this tick carried (None on
    # a read that failed or a payload with no state): the reading's
    # `venue_state`, which the flatten's slippage leg refuses on
    slug_states: dict = field(default_factory=dict)
    snaps: dict = field(default_factory=dict)
    # (whale, condition_id) -> the per-market read, taken at most once
    # per market per tick (Phase 1); a refused or unreadable read is
    # cached as the fail-closed tuple so it is not retried in the tick
    mkts: dict = field(default_factory=dict)
    addrs: dict = field(default_factory=dict)   # whale -> address, read once per tick
    mkt_reads: int = 0                          # every per-market read this tick, books and candidates
    cand_mkt_reads: int = 0                     # the CANDIDATES' per-market reads: the budgeted ones (U12)
    open_by_book: dict = field(default_factory=dict)   # book_id -> (order row, status)
    nonterminal: set = field(default_factory=set)      # book ids with a non-terminal order
    books_seen: set = field(default_factory=set)       # (whale, condition_id)
    # E5 review F1: book ids a fill was BOOKED on this tick (_book_fill,
    # after its commit) -- step R walks the account before step O books
    # fills, so r.venue is stale by that fill for the book walk, and the
    # frozen exit refuses on it by name (`frozen_fill_this_tick`)
    filled_books: set = field(default_factory=set)
    # mirror_orders.intent (migration 050) exists this tick: read by the
    # per-tick guard; False sends the 047-shaped statements and keeps
    # the MIRROR_SHORTS knob effectively off (P2 rung S0)
    short_col: bool = False
    # the tick's venue budget for the exact mapping lane (C1): the
    # shadow's own MapBudget, ms.MAP_READS_PER_TICK resolver calls a
    # tick across every candidate, past which a candidate is
    # `map_reads_capped` -- no verdict, read again next tick
    map_budget: Any = field(default_factory=lambda: ms.MapBudget())
    # THE PER-GAME CAP'S TICK STATE (E1). `game_books`: game key
    # (_game_key_of: the game_key alone, every whale's books of one
    # game together) -> the game's non-closed books, id ascending, as
    # the walk read them (a book opened this tick is appended);
    # `game_sized`: book id -> the exposure a book SIZED this tick counts
    # for against the room of the later books of its game (its held
    # exposure or its new target's, whichever is more; None when its
    # ledger or its resting order could not be read); `marks`: book id
    # -> the mark this tick read for it, the fallback price of a held
    # book with no avg_cost
    game_books: dict = field(default_factory=dict)
    game_sized: dict = field(default_factory=dict)
    marks: dict = field(default_factory=dict)
    # THE PARALLEL WALK'S SHARED STATE (E2). `ops_pending`: ops reserved
    # by a placement between its budget check and its venue write (an
    # _OpSlot), counted against the budget so two books in flight
    # cannot both pass the check on the same last op; `venue_calls`:
    # every venue call this tick, the soft guard's counter; the locks
    # serialise the tick's read-once caches (open orders, protected
    # ids, his snapshots, whale addresses) so a second caller waits for
    # the first read instead of reading "tried, still None"
    ops_pending: int = 0
    # book ids whose TTL/replace cancel this tick covers the rest that
    # follows (E2 review round 3, LOW-6: a re-quote is ONE op)
    requote_credit: set = field(default_factory=set)
    venue_calls: int = 0
    # the soft guard's own count (E2 review, MEDIUM-5): the tick's
    # WRITES and its CANDIDATE reads -- never the open books' reads,
    # which are the tick's fixed cost, so 40 candidate reads are
    # reachable at any book count (60 = 40 reads + 20 writes)
    guard_calls: int = 0
    open_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    protected_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    snap_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    addr_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # THE BOOK WALK'S MISS STREAK, JUDGED IN WALK ORDER (E2 review,
    # MEDIUM-4). The live streak (t.misses) counts consecutive misses
    # in ARRIVAL order, which under the parallel walk is interleaving:
    # three failing reads landing before three slow good ones abandoned
    # a tick the sequential walk never would. While `walking`, a book's
    # quote read records its outcome here by slug -- (why, detail) for
    # a counted miss, False for a reset, absent for an uncounted one --
    # `walk_done` holds the ids of the books whose tick has finished,
    # and _walk_streak judges the run over `ordered` (the walk's own
    # order) up to the first unfinished book; after the walk the live
    # streak is set to the walk's TRAILING run, exactly what the
    # sequential walk left the candidates
    walking: bool = False
    walk_order: list = field(default_factory=list)
    book_reads: dict = field(default_factory=dict)
    walk_done: set = field(default_factory=set)
    # W2 / P2: the tick's real clock (tick_once's `started`; the row's
    # `tick_s` is the wall time so far), the refusal rows collected this
    # tick, the memo entries they will commit on a successful write, and
    # his active conditions per whale as the walk read them
    started: float = field(default_factory=time.monotonic)
    cand_rows: list = field(default_factory=list)
    cand_pending: dict = field(default_factory=dict)
    active_conds: dict = field(default_factory=dict)
    # E6: the tick's sequence number (_tick_seq), its timing block
    # (seconds per step, summed per call for the two book-read parts),
    # the books' outcome classes (`read`, `on_target`, `placed`,
    # `no_mark`, `terminal_skipped`, `quiet_skipped`), the ids of the
    # books a placement went out for this tick (the outcome class and
    # the quiet verdict read it), the quiet books' budget and the reads
    # taken from it (reserved at the decision, no await between), and
    # the candidate stage's share of the budget -- map reads and quote
    # reads together -- with the quote-read cap it leaves. Review fold:
    # the markets that WOKE this tick (a woken book is hot, HIGH-2), the
    # deferred books this tick reads (the queue's head, _deferred_due)
    # and the newly DUE quiet reads taken (`due_reads`, under what the
    # budget leaves after the deferred head; `quiet_reads` counts both)
    seq: int = 0
    # E7: `books_data` split -- the books' per-market reads' seconds
    # inside the data-API throttle's wait and inside the request itself
    # (whale_exits.market_positions' `timing`), summed per call; served
    # beside the timing block as `short.data_api` (the block's own keys
    # are pinned exactly)
    timing: dict = field(default_factory=lambda: {"walk": 0.0, "orders": 0.0, "books": 0.0,
                                                  "books_venue": 0.0, "books_data": 0.0,
                                                  "candidates": 0.0,
                                                  "books_data_wait": 0.0, "books_data_req": 0.0})
    outcomes: Counter = field(default_factory=Counter)
    placed_books: set = field(default_factory=set)
    quiet_budget: int = 0
    quiet_reads: int = 0
    cand_budget: int = CAND_MIN_PER_TICK
    cand_cap: int = 0
    woken: set = field(default_factory=set)
    # E7 review MEDIUM-2: the walk's `last_ts` per condition (the stamp the
    # candidate order ranks on), for the unmapped memo's fill-independent
    # ceiling; absent = no evidence
    stamps: dict = field(default_factory=dict)
    deferred_due: set = field(default_factory=set)
    due_reads: int = 0


# ------------------------------------------- E2: the tick's shared counters
#
# The book walk runs MIRROR_BOOK_CONCURRENCY books at once, so every
# shared counter a book reads and then writes across an await needs a
# take-one primitive. asyncio is cooperative: a read-modify-write with
# no await between is atomic, and every `t.<counter> += 1` in this file
# is one of those. The two that were NOT are the ops budget (the check
# at the top of _place, the increment after the row INSERT, awaits
# between) and the tick's room readings (read in _room_qty, taken off
# in _place after the venue call): both are made take-first here.

class _OpSlot:
    """One op of the tick's budget, RESERVED at the check and either
    committed (the write went out: t.ops) or released (a refusal
    before the write: nothing spent). Idempotent either way. A
    CREDITED slot (E2 review round 3, LOW-6) is the second half of a
    re-quote -- the rest that follows a TTL or replace cancel in the
    same tick -- and spends nothing: the cancel's op covers the pair,
    so a cohort of expired rests never leaves its books bare for a tick
    with the budget gone on cancels alone."""

    __slots__ = ("t", "done")

    def __init__(self, t: "_Tick", credited: bool = False) -> None:
        self.t, self.done = t, bool(credited)

    def commit(self) -> None:
        if not self.done:
            self.done = True
            self.t.ops_pending -= 1
            self.t.ops += 1

    def release(self) -> None:
        if not self.done:
            self.done = True
            self.t.ops_pending -= 1


def _op_slot(t: "_Tick", whale: str | None, exit: bool = False) -> "_OpSlot | None":
    """Take one op of rules.MIRROR_MAX_ORDER_OPS_PER_TICK, or None and
    `ops_capped` when the budget -- committed AND reserved -- is spent.
    No await between the read and the reservation: atomic.

    AN EXIT IS EXEMPT FROM THE BUDGET (E4 review round 3, M-1). `exit`
    is a slot on an exit's path -- a reduce, the paired or vanish
    flatten, the sign-flip flatten, a short book's cover: its cancel,
    its IOC, its rest, its close -- and is NEVER refused: reserved and
    committed like any other (it still counts in `ops`, on
    `venue_calls` and the soft guard at the write), only never turned
    away. The exit's take off a standing rest is a cancel and an IOC as
    two ops with no credit, and at the budget's last op the cancel went
    and the IOC was `ops_capped`: the book had NO exit order for the
    tick, the one thing an exit is never (E2 LOW-6: "an exit is never
    shed"). The replace budget (`_exit_or_flip`) and the loss stop
    already exempt exits; this is the same exemption on the ops
    budget. Entries stay bounded exactly as before, E2's take-first
    fallback on the credit included."""
    if not exit and t.ops + t.ops_pending >= rules.MIRROR_MAX_ORDER_OPS_PER_TICK:
        _mirror_stop("ops_capped", whale)
        return None
    t.ops_pending += 1
    return _OpSlot(t)


def _venue_call(t: "_Tick", n: int = 1, guard: bool = False) -> None:
    """`n` venue HTTP calls, counted on the tick and the census
    (`venue_calls`, one event each): every paced read, cancel,
    placement (two for a BUY: preview and create), close, positions
    page and mapping resolver call. `guard` says they count against
    the soft guard too (t.guard_calls: the writes and the candidates'
    reads; an open book's reads never do)."""
    n = max(0, int(n))
    t.venue_calls += n
    if guard:
        t.guard_calls += n
    for _ in range(n):
        _mirror_stop("venue_calls")


async def _venue_read(t: "_Tick", fn, *args, guard: bool = False):
    """One paced venue READ in a worker thread, counted."""
    _venue_call(t, guard=guard)
    return await asyncio.to_thread(_paced, fn, *args)


def _write_slots(fn, args: tuple) -> int:
    """The HTTP requests a venue WRITE makes: a submit_fok that is not
    a SELL previews then creates (two); a SELL, a cancel and a close
    are one. Read off the adapter's call shape, never guessed from
    the intent."""
    if getattr(fn, "__name__", "") == "submit_fok":
        sell = bool(args[3]) if len(args) > 3 else False
        return 1 if sell else 2
    return 1


async def _pm_held(t: "_Tick", us_slug: str) -> tuple[int, float | None]:
    """(held whole contracts, avg cost) for one US market from the
    venue's OWN positions payload: le._pm_held's reading (the same
    abs(netPosition), the same `expired` refusal, the same int floor
    and cost/qty average), taken through THIS lane's pacer and counted
    page by page (E2 review, MEDIUM-3: le._pm_held pages the whole
    account up to 50 times, unpaced and uncounted). Bounded at the same
    50 pages; raises on an unreadable page, as the caller expects."""
    from ..api.pmus_account import _amt

    def _walk() -> tuple[dict, int]:
        client = t.pmus._get_client()
        positions: dict = {}
        cursor = ""
        pages = 0
        for _ in range(50):
            pace(ms.READ_PACING_S)
            pages += 1
            resp = client.portfolio.positions(
                {"limit": 100, **({"cursor": cursor} if cursor else {})}) or {}
            positions.update(resp.get("positions") or {})
            cursor = resp.get("nextCursor") or ""
            if resp.get("eof") or not cursor:
                break
        return positions, pages

    positions, pages = await asyncio.wait_for(asyncio.to_thread(_walk), timeout=30)
    _venue_call(t, pages)
    p = (positions or {}).get(us_slug) or {}
    qty = abs(_amt(p.get("netPosition")))
    if qty <= 0 or p.get("expired"):
        return 0, None
    cost = abs(_amt(p.get("cost")))
    return int(qty), (round(cost / qty, 4) if cost > 0 else None)


def _room_take(t: "_Tick", est: float) -> None:
    """This placement's notional comes off THIS tick's room readings
    BEFORE its venue call (E2): under the parallel walk two books read
    the room across the same await, and taking it after the call let
    both spend the last clip. Sequentially the same figures as before."""
    if est:
        for attr in ("day_room", "total_room", "mirror_day"):
            v = getattr(t, attr)
            if v is not None:
                setattr(t, attr, v - est)


def _room_give(t: "_Tick", est: float) -> None:
    """A placement the venue REFUSED outright (a post-only rejection, a
    refused create) spent nothing: its notional goes back."""
    _room_take(t, -float(est))


# THE SOURCES A LIVE BOOK MAY OPEN FROM (C1). map_market answers with
# 'ledger' | 'premap' | 'exact' | 'yesno' | 'grammar'. The first four go
# to le._mapping_admitted exactly as a copy fill's mapping_src does:
# 'premap' and 'exact' ride QUARANTINE_RESUME_SRC (the copy lane's own
# resume lane, same whale allowlist, same premap_live switch); 'yesno'
# is the copy lane's yesno_exact -- refused by name while the mapping
# quarantine holds (`quarantined: ... (src=yesno, ...)`), admitted the
# day the owner lifts it, the same day the copy lane's is; 'ledger' is
# the copy lane's own traded row and reads the same way.
#
# 'grammar' (the aec code side, map_lane.aec_code_side) is a MIRROR
# class (C1 round 2, owner order 2026-09-06 18:25Z "college football
# ... should be very easy to map"). The copy lane has no side rule it
# could inherit -- its aec- rule is name similarity on the venue's side
# descriptions plus the venue's long marker (pmus.resolve_market_exact),
# which refuses mascots outright -- so the 2,012 side echoes on
# side_echo_last certify nothing about a side chosen by team code. The
# class opens live books ONLY through its own certification
# (_grammar_admission), on the resume lane's switches:
#   1. venue-only truth BEFORE the open (map_lane.grammar_truth): the
#      venue's per-side contract for the chosen code
#      (atc-<lg>-<a>-<b>-<date>-<code>) must name, in its own
#      outcome/title/team fields, exactly the aec side description the
#      rule chose -- no order assumption survives that check; a listed
#      contract that names something else is a MISMATCH: refused and
#      the class is TRIPPED; an unlisted contract is unverified: refused,
#      nothing tripped;
#   2. one grammar book at a time until its FIRST FILL is echoed
#      (map_lane.grammar_fill_echo): the venue's positions payload must
#      hold the side the rule chose (marketMetadata.outcome) with the
#      book's sign, attributable to our own fill; a mismatch freezes the
#      book `side_echo_mismatch` and trips the class; further grammar
#      books are refused `grammar_tripped` until an admin clears the
#      state key;
#   3. the resume lane's own gates for the whale (le._mapping_admitted
#      as for 'exact': the side-echo circuit, the verified set, the
#      hold, LIVE_PREMAP_WHALES + premap_live) -- 'grammar' never joins
#      QUARANTINE_RESUME_SRC itself.
# Every count rides on the census and the served `integ` block.
MIRROR_LIVE_MAP_SRC: frozenset[str] = frozenset({"ledger", "premap", "exact", "yesno"})
GRAMMAR_STATE_KEY = "mirror_grammar_echo"
_GRAMMAR_VERIFIED_MAX = 200


def _position_echo(pmus, slug: str) -> tuple[dict | None, int]:
    """The venue's own echo of what we hold on `slug`: signed net and the
    side its positions payload names (marketMetadata.outcome / title);
    {"net": 0.0} when not held; None when unreadable -- and the number
    of pages read, every one paced and one venue request (E2 review
    round 2, LOW-d: five pages rode one gap and one count).
    pmus.position_side's own read with the metadata kept."""
    pages = 0
    try:
        client = pmus._get_client()
        cursor = ""
        for _ in range(5):
            pace(ms.READ_PACING_S)
            pages += 1
            resp = client.portfolio.positions(
                {"limit": 100, **({"cursor": cursor} if cursor else {})}) or {}
            for s, p in (resp.get("positions") or {}).items():
                if str(s).lower() == slug.lower():
                    meta = (p or {}).get("marketMetadata") or {}
                    return {"net": float((p or {}).get("netPosition") or 0),
                            "outcome": meta.get("outcome"), "title": meta.get("title")}, pages
            cursor = resp.get("nextCursor") or ""
            if not cursor:
                break
    except Exception:  # noqa: BLE001 — unreadable is not a verdict
        return None, pages
    return {"net": 0.0, "outcome": None, "title": None}, pages


def _market_read(pmus, slug: str) -> dict | None:
    """One paced venue market read; None when unreadable or unlisted."""
    pace(ms.READ_PACING_S)
    try:
        return (pmus._get_client().markets.retrieve_by_slug(slug) or {}).get("market") or None
    except Exception:  # noqa: BLE001 — a 404 is an answer, an error is no answer
        return None


async def _grammar_state(t: _Tick) -> dict | None:
    """The class's own state key: counts, the trip, the verified books,
    the pending sides. None when unreadable (every reader refuses)."""
    val, err = await _state(t.pool, GRAMMAR_STATE_KEY)
    if err:
        return None
    st = dict(val) if isinstance(val, dict) else {}
    st.setdefault("ok", 0)
    st.setdefault("mismatch", 0)
    st.setdefault("unverified", 0)
    st.setdefault("tripped", False)
    st.setdefault("verified", [])
    st.setdefault("pending", {})
    return st


async def _grammar_write(t: _Tick, st: dict) -> None:
    st["verified"] = list(st.get("verified") or [])[-_GRAMMAR_VERIFIED_MAX:]
    try:
        await _write_state(t.pool, GRAMMAR_STATE_KEY, st)
    except Exception:  # noqa: BLE001 — bookkeeping never breaks a tick; the next read refuses
        log.warning("mirror_live: grammar state write failed")


async def _grammar_admission(t: _Tick, whale: str, slug: str, g: dict | None) -> str | None:
    """The refusal name that keeps a grammar-mapped market from opening,
    or None when the class may open THIS book: state readable and not
    tripped, no other grammar book still awaiting its first-fill echo,
    and the venue's per-side contract naming the chosen side."""
    from .. import map_lane

    st = await _grammar_state(t)
    if st is None:
        return "grammar_echo_unreadable"
    if st.get("tripped") or int(st.get("mismatch") or 0) > 0:
        return "grammar_tripped"
    if not isinstance(g, dict) or g.get("side_index") not in (0, 1) or not g.get("his_slug"):
        return "grammar_echo_unverified"
    try:
        books = [dict(b) for b in await t.pool.fetch(_SQL_BOOKS_OPEN)]
    except Exception:  # noqa: BLE001 — unreadable books: the probation cannot be judged
        return "grammar_echo_unreadable"
    verified = {int(x) for x in (st.get("verified") or []) if str(x).isdigit()}
    if any(str(b.get("map_source") or "") == "grammar" and int(b["id"]) not in verified
           for b in books):
        return "grammar_probation"
    i = int(g["side_index"])
    _venue_call(t)
    market = await asyncio.to_thread(_market_read, t.pmus, slug)
    other_desc = ""
    if isinstance(market, dict):
        _m = market.get("market") if isinstance(market.get("market"), dict) else market
        _sides = [x for x in ((_m or {}).get("marketSides") or []) if isinstance(x, dict)]
        if len(_sides) == 2:
            other_desc = str(_sides[1 - i].get("description") or "")
    cands = await _contract_candidates(t.pool, g["his_slug"], i, str(g.get("outcome_desc") or ""),
                                       other_desc)
    if cands is None:
        verdict, detail = "unverified", "per-side contract rows unreadable"
    elif len(cands) != 1:
        verdict, detail = "unverified", f"{len(cands)} per-side contracts fit the side: {cands[:3]}"
    else:
        _venue_call(t)
        con = await asyncio.to_thread(_market_read, t.pmus, cands[0])
        # C6 (2026-09-07): the slug code at i and his outcome ride along
        # so a segment-winner contract is read against the aec side's
        # own team field (map_lane._team_truth)
        head = map_lane.slug_head(g["his_slug"])
        verdict, detail = map_lane.grammar_truth(
            con, market, i, code=(head[1], head[2])[i] if head else None,
            school=g.get("his_outcome"))
    if verdict == "ok":
        # a record already certified for this market that CONTRADICTS
        # tonight's reading is a mismatch like any other (review fold v3,
        # LOW-2): never a silent overwrite
        prior = st["certified"].get(slug) if isinstance(st.get("certified"), dict) else None
        clash = _grammar_contradiction(prior, str(g.get("outcome_desc") or ""), i)
        if clash:
            verdict, detail = "mismatch", f"certified record contradicted: {clash}"
    st[verdict] = int(st.get(verdict) or 0) + 1
    st["last"] = {"verdict": verdict, "slug": slug, "detail": detail, "at": t.now, "stage": "open"}
    if verdict == "mismatch":
        st["tripped"] = True
        log.critical("mirror_live: GRAMMAR SIDE MISMATCH before open on %s: %s -- the class is "
                     "tripped", slug, detail)
        await _grammar_write(t, st)
        return "side_echo_mismatch"
    if verdict != "ok":
        await _grammar_write(t, st)
        return "grammar_echo_unverified"
    st.setdefault("pending", {})[slug] = {"outcome_desc": g.get("outcome_desc"),
                                         "intent": g.get("intent"), "side_index": i,
                                         "his_slug": g.get("his_slug"), "at": t.now}
    # C4 (2026-09-06): the venue-truth fact itself, kept after the fill
    # echo pops `pending` -- the per-side contract for code i named this
    # mascot -- so a spread on the same event whose asc row names mascots
    # can place the question's subject at the code's position through
    # this record (premap._c4_subject_certified). Bounded like `verified`.
    _grammar_certify(st, slug, {"outcome_desc": g.get("outcome_desc"), "side_index": i,
                                "his_slug": g.get("his_slug"), "at": t.now})
    await _grammar_write(t, st)
    _mirror_stop("grammar_echo_ok", whale)
    return None


def _grammar_certify(st: dict, slug: str, rec: dict) -> None:
    """Record the venue-truth fact for `slug` in st['certified'], newest
    LAST: a market certified again is popped and re-inserted so it is
    the newest (review fold 2026-09-06, LOW: re-inserting under an
    existing key kept its old position and tonight's certification was
    the first evicted), and the oldest are evicted past
    _GRAMMAR_VERIFIED_MAX. A state whose 'certified' is not a dict is
    started afresh."""
    cert = dict(st["certified"]) if isinstance(st.get("certified"), dict) else {}
    cert.pop(slug, None)
    cert[slug] = rec
    st["certified"] = dict(list(cert.items())[-_GRAMMAR_VERIFIED_MAX:])


def _grammar_contradiction(prior: dict | None, desc: str, i: int) -> str | None:
    """THE WRONG-SIDE GUARD ON A RE-CERTIFICATION (review fold v3,
    2026-09-07, LOW-2): the record the class already holds for this aec
    market against tonight's venue-truth reading (side description
    `desc` at position `i`). The SAME mascot at the OTHER position, or
    ANOTHER mascot at THIS position, is the venue contradicting itself
    -- a wrong side one of the two times -- and reads as a mismatch
    (the caller trips the class and keeps the old record). The same
    reading is a refresh, and the complementary side (the other mascot
    at the other position: Huskies at wash after Cougars at washst) is
    the pair itself, consistent -- neither is a contradiction. Returns
    the detail, or None."""
    from .. import map_lane

    if not isinstance(prior, dict):
        return None
    pd, pi = map_lane._norm(prior.get("outcome_desc")), prior.get("side_index")
    same_desc, same_pos = pd == map_lane._norm(desc), pi == i
    if same_desc == same_pos:
        return None
    return (f"the class certified {prior.get('outcome_desc')!r} at position {pi}; the venue now "
            f"reads {desc!r} at position {i}")


async def _contract_candidates(pool, his_slug: str, i: int, desc: str,
                               other_desc: str = "") -> list[str] | None:
    """THE VENUE'S OWN PER-SIDE SUFFIX (round 3, review 3): the venue's
    per-side contract for team i is atc-<lg>-<a>-<b>-<date>-<suffix>, and
    the suffix is the VENUE'S code, not necessarily his (the probes carry
    atc-cfb-hawaii-stan-2026-08-29-h). The event's atc- rows in us_premap
    are listed and the one that fits the side is taken: a single-token
    suffix that is his code, or a prefix of his code and of no other, or
    a row whose question names the aec side description whole AND whose
    suffix does not fit the OTHER side (review fold 2026-09-06, HIGH-2:
    with only atc-cfb-washst-wash-…-wash 'Will the Cougars win?' listed,
    the question alone fitted the OTHER code's contract to washst, the
    truth check read the same name and certified Cougars at washst --
    the wrong side of every washst-wash spread; a suffix that is the
    other code, or prefixes it, is never ours by its question). Exactly
    one distinct contract, else the caller reads it as unverified. When
    the sweep lists none, his own code's slug is the one candidate -- a
    guess the venue must confirm by naming the side. None: unreadable."""
    from .. import map_lane

    parsed = map_lane.slug_head(his_slug)
    if parsed is None or i not in (0, 1):
        return []
    lg, a, b, date, _tail = parsed
    base = f"atc-{lg}-{a}-{b}-{date}-"
    code, other = (a, b)[i], (b, a)[i]
    try:
        rows = [dict(r) for r in await pool.fetch(
            "SELECT DISTINCT identifier, market_slug, question FROM us_premap "
            "WHERE identifier LIKE $1 OR market_slug LIKE $1 /* ml-grammar-contracts */",
            base + "%")]
    except Exception:  # noqa: BLE001
        return None
    want = map_lane._norm(desc)
    # a question that names OUR side is the opponent's row when it names
    # the opponent too ("Will Tigers win against Bears?", round-3 review):
    # by_question fits only a question naming our side and not the other's
    other_want = map_lane._norm(other_desc) if other_desc else ""
    fits: list[str] = []
    winner: list[tuple[str, str]] = []
    for r in rows:
        for s in (r.get("market_slug"), r.get("identifier")):
            s = str(s or "").lower()
            if not s.startswith(base):
                continue
            suffix = s[len(base):]
            if not suffix:
                continue
            if "-" in suffix:
                # C6 (2026-09-07): the venue's SEGMENT-WINNER row for the
                # code -- `winner-<seg>-<code_i>`, its question naming the
                # school ('Will Notre Dame win the first half?') -- is the
                # code's contract where the venue lists no plain one (the
                # whole cfb book: 144 such rows, no `atc-…-<code>`); the
                # `-draw` row and every other dashed suffix (a prop row)
                # never are. Read below, after the plain fits.
                seg = suffix.split("-")
                if (len(seg) == 3 and seg[0] == "winner" and seg[2] == code
                        and lg == "cfb" and map_lane._identity_on()):
                    # college football only for now, and dark without the
                    # identity switch (M5 review HIGH-1 / LOW-2)
                    school = map_lane.winner_school(r.get("question"))
                    if school and (s, school) not in winner:
                        winner.append((s, school))
                continue
            by_code = (suffix == code or (code.startswith(suffix) and not other.startswith(suffix)))
            # the suffix fits the other side: the other code itself, a
            # prefix of it (whether or not of ours too), or an extension
            # of it ('texas' beside tex; v3 LOW-3) -- its question never
            # makes it ours
            fits_other = suffix == other or other.startswith(suffix) or suffix.startswith(other)
            q_norm = map_lane._norm(r.get("question"))
            by_question = (bool(want) and want in q_norm
                           and not (other_want and other_want in q_norm)
                           and not fits_other)
            if (by_code or by_question) and s not in fits:
                fits.append(s)
    if not fits and winner:
        # every winner row for the code must name ONE school (the C6a
        # witness's bar); then the first by name stands for them all and
        # the truth check reads its payload. Rows naming two schools are
        # the venue disagreeing with itself: all of them, so the caller
        # reads 'more than one' and refuses
        slugs = sorted(s for s, _sch in winner)
        if all(map_lane.same_name(winner[0][1], sch) for _s, sch in winner[1:]):
            fits.append(slugs[0])
        else:
            fits.extend(slugs)
    if not rows:
        c = map_lane.contract_slug(his_slug, i)
        return [c] if c else []
    return fits


async def _admit_source(t: _Tick, whale: str, src: str | None, slug: str) -> tuple[bool, str | None]:
    """The mapping gate for a book's source: le._mapping_admitted on the
    source itself, except 'grammar', which rides the resume lane's
    switches (the gates 'exact' clears) under its own certification
    (_grammar_admission before an open, the trip on every increase)."""
    if str(src or "") != "grammar":
        return await le._mapping_admitted(t.pool, whale, src, slug)
    st = await _grammar_state(t)
    if st is None:
        return False, "grammar_echo_unreadable"
    if st.get("tripped") or int(st.get("mismatch") or 0) > 0:
        return False, "grammar_tripped"
    ok, why = await le._mapping_admitted(t.pool, whale, "exact", slug)
    return ok, (why.replace("(src=exact,", "(src=grammar,") if why else why)


async def _grammar_fill_check(t: _Tick, book: dict) -> str:
    """THE FIRST FILL'S ECHO on a grammar book: 'ok' when the book may
    plan this tick (verified, or nothing filled yet), 'wait' when it may
    NOT plan but is not frozen (the certification state or the venue's
    echo unreadable, the echo unattributable, the chosen side not on
    record -- round 3, review 4: an unverified side is never traded
    further on), 'frozen' when it was frozen `side_echo_mismatch`. Runs
    every tick the book holds shares until the venue's echo verifies it
    (then the book is on the verified list and the next grammar book
    may open)."""
    async with _GRAMMAR_LOCK:
        return await _grammar_fill_check_locked(t, book)


async def _grammar_fill_check_locked(t: _Tick, book: dict) -> str:
    from .. import map_lane

    st = await _grammar_state(t)
    if st is None:
        _mirror_stop("grammar_echo_unreadable", book.get("whale"))
        return "wait"
    verified = {int(x) for x in (st.get("verified") or []) if str(x).isdigit()}
    if int(book["id"]) in verified:
        return "ok"
    if abs(float(book.get("ledger_net") or 0.0)) < FLAT_TOL_SHARES:
        return "ok"                     # nothing filled yet: nothing to echo
    pend = (st.get("pending") or {}).get(str(book.get("us_market_slug")) or "") or {}
    echo, pages = await asyncio.to_thread(_position_echo, t.pmus, str(book["us_market_slug"]))
    _venue_call(t, pages)
    verdict, detail = map_lane.grammar_fill_echo(
        echo, pend.get("outcome_desc"), _book_intent(book),
        abs(float(book.get("ledger_net") or 0.0)))
    st[verdict] = int(st.get(verdict) or 0) + 1
    st["last"] = {"verdict": verdict, "slug": book.get("us_market_slug"), "detail": detail,
                  "at": t.now, "stage": "fill", "book": book["id"]}
    if verdict == "ok":
        st.setdefault("verified", []).append(int(book["id"]))
        (st.get("pending") or {}).pop(str(book.get("us_market_slug")), None)
        await _grammar_write(t, st)
        _mirror_stop("grammar_echo_ok", book.get("whale"))
        return "ok"
    if verdict == "mismatch":
        st["tripped"] = True
        await _grammar_write(t, st)
        log.critical("mirror_live: SIDE-ECHO MISMATCH on grammar book %s (%s): %s -- frozen, "
                     "the class is tripped", book["id"], book.get("us_market_slug"), detail)
        await _cancel_open_for(t, book, "side_echo_mismatch")
        await _freeze(t, book, "side_echo_mismatch", {"detail": detail})
        return "frozen"
    await _grammar_write(t, st)
    _mirror_stop("grammar_echo_unverified", book.get("whale"))
    return "wait"


# THE COUNTERS AN OPERATOR SURFACE CAN ACTUALLY READ.
# `/api/health/services` publishes this worker's stats through
# `_sanitize_detail`, which caps EVERY dict at 40 keys and appends
# `_truncated_keys` -- and `census` carries ~99. So `.detail.census.<name>`
# reads a real number for the first 40 names in CENSUS_KEYS order and a
# STRUCTURAL ZERO for every name after them: `side_band` (index 40,
# pushed past the cap when `venue_halted` took index 24 on 2026-09-05),
# `snapshot_stale` (41), every `snap_market_*` name, `drift`,
# `venue_ledger_disagree`, `wrong_sign_trip`, `order_lost`,
# `post_only_ignored` and `mirror_flatten` are all past the cap. A gate
# line reading those prints a pass that was never measured, which is
# worse than printing nothing.
#
# `integ` is the fix that lives on THIS side of the wire: one extra
# top-level key holding a small flat block of exactly the names the P1
# and integrity gate lines quote, at depth 2 and far under the cap, so a
# probe reads `.detail.integ.<name>` and gets the tick's real number. It
# is a projection of `census` and the `snap_market_*` counters, never a
# second place where a count is kept -- `_integ_block` reads them, it
# never writes them. `api/app.py` needs no change and gets none.
# `side_band` and `venue_halted` ride here so both stay on the served
# surface: the one the new key pushed past the cap, and the new key
# itself (an operator reading the halted venue reads it off `integ`).
_INTEG_CENSUS_KEYS: tuple[str, ...] = (
    "mirror_overspend", "overspend_uncheckable", "venue_ledger_disagree",
    "wrong_sign_trip", "overfill", "order_lost", "post_only_ignored",
    "mirror_flatten", "snapshot_stale", "drift", "snap_market_unreadable",
    "snap_market_capped", "snap_market_stale", "snap_market_no_ids",
    "snap_market_skipped", "side_band", "venue_halted",
    # `ledger_dust` sits past the served cap in CENSUS_KEYS, so it
    # rides here too: an operator reading the dust count reads it off
    # `integ` (2026-09-06 fix review)
    "ledger_dust",
    # P2 rung S0: what the short rungs' gates read (S3: wrong_sign_trip
    # and overfill are above; the short refusals and the flip are here)
    "short_reduce_unproven", "sign_flip", "short_model_disarmed",
    "short_gate_refused", "short_column_absent", "intent_guard_unreadable",
    "short_share_cap",
    # U12 (2026-09-06): the book COUNT unreadable at admission -- a
    # fail-closed refusal past the served cap, so it rides here where
    # an operator can read it
    "books_unreadable",
    # review of U12c: both past the served cap, so they ride here too
    "ratio_stepped", "under_min_notional", "shadow_check_skipped",
    # C1 round 2 (review D, (4)): the mapping lane's two refusals a gate
    # line reads, and the grammar class's trip -- all past the served
    # cap; three names, the block stays under it
    "map_source_unverified", "map_reads_capped", "side_echo_mismatch",
)
_INTEG_STAT_KEYS: tuple[str, ...] = (
    "snap_market_planned", "snap_market_reads", "snap_market_fresh_reads",
    "snap_market_slow",
    # the short leg's at_or_better producer (brief G4): every BUY_SHORT
    # fill booked, how many paid at or under the wire's collateral by
    # _overspend_of's own predicate, and how many could not be checked
    # -- the denominator the gate line must print beside the share
    "short_fills", "short_fills_at_or_better", "short_fills_uncheckable",
)
# The short counters live in ONE nested block on the stats (`short`, see
# _new_stats) and are projected onto `integ` under the flat names above:
# the top level of the heartbeat is capped at 40 keys by the health
# endpoint's sanitizer, and four flat short keys beside U9's
# `venue_state` would have filled the base block to the cap and dropped
# every key a tick appends after it (`venue_positions`,
# `abandon_reason`, ...) from the served surface (P2 fold, 2026-09-06)
_INTEG_SHORT_STATS: dict[str, str] = {
    "short_fills": "fills", "short_fills_at_or_better": "at_or_better",
    "short_fills_uncheckable": "uncheckable",
}


def _integ_block(stats: dict) -> dict:
    """The served projection. Flat, numeric, and bounded by construction:
    len(_INTEG_CENSUS_KEYS) + len(_INTEG_STAT_KEYS) keys, asserted under
    the sanitizer's cap by this file's own test."""
    census = stats.get("census") or {}
    out = {k: int(census.get(k) or 0) for k in _INTEG_CENSUS_KEYS}
    short = stats.get("short") or {}
    for k in _INTEG_STAT_KEYS:
        sub = _INTEG_SHORT_STATS.get(k)
        out[k] = int((short.get(sub) if sub else stats.get(k)) or 0)
    return out


def _new_stats() -> dict:
    return {"status": "ok", "mode": MODE_SAFE, "whales": [], "books_live": 0,
            "books_frozen": 0, "orders_open": 0,
            # the day room _global_guards read, in dollars (filled plus
            # resting off MIRROR_DAY_USD); None on a tick that never
            # read it (SAFE, exits, a cancel-only tick)
            "mirror_day_room": None,
            # the venue's own market state, the most common one this
            # tick's quote reads carried (None: no read carried one)
            "venue_state": None, "placed_rest": 0, "placed_take": 0,
            "filled_rest": 0, "filled_take": 0, "partial_fills": 0, "requotes": 0,
            "cancelled": 0, "flattened": 0, "closed_books": 0, "frozen_reasons": {},
            "census": {k: 0 for k in CENSUS_KEYS}, "recent": [], "abandoned": False,
            "skipped_backoff": False, "ops": 0, "reads": 0,
            # the tick's WALL TIME in seconds (1 dp; None until a tick
            # ran): the book walk is unbounded in reads since U12, so
            # this is what an operator watches stretch as books grow --
            # the 0.35 s pacer (D29) bounds the venue rate, not the tick
            "tick_s": None, "woken": [],
            "reaper_touched_mirror": 0, "post_only": _POST_ONLY_OK,
            # MIRRORSNAP, always present so a reader can tell "never
            # read" from "read and never fresh". THE DENOMINATOR IS
            # `snap_market_planned` -- every distinct market this tick
            # asked about, whatever became of the ask -- because
            # fresh/reads would exclude exactly the failures (the budget
            # cap, a market whose ids we could not form, a skipped tick)
            # and so read HIGHER than the share §3b M4 gates on. The
            # identity that must hold every tick is
            #   planned = reads + capped + no_ids + skipped
            # and `fresh_complete_share = snap_market_fresh_reads /
            # snap_market_planned`. `snap_market_fresh_reads` is a COUNT
            # here; the per-market bool of the same fact rides on the
            # plan row as `snap_market_fresh` -- two surfaces, two
            # names, never one name meaning two things.
            "snap_market_planned": 0, "snap_market_reads": 0,
            "snap_market_fresh_reads": 0, "snap_market_capped": 0,
            "snap_market_no_ids": 0, "snap_market_skipped": 0,
            "snap_market_stale": 0, "snap_market_slow": 0,
            # P2 rung S0, ONE nested block (the top level is capped at 40
            # keys by the health endpoint's sanitizer; see
            # _INTEG_SHORT_STATS): `on`, the knob as this tick read it
            # (the environment AND the 050 column), and the short leg's
            # at_or_better producer -- fills booked, fills at or under
            # the wire's collateral, fills that could not be checked --
            # served flat on `integ` as short_fills, short_fills_at_or_better,
            # short_fills_uncheckable
            "short": {"on": False, "fills": 0, "at_or_better": 0, "uncheckable": 0},
            # present from the first tick, so a reader can tell "the
            # worker has not run" from "it ran and every counter is 0"
            "integ": {k: 0 for k in _INTEG_CENSUS_KEYS + _INTEG_STAT_KEYS}}


def _shorts_on(t: _Tick) -> bool:
    """The ONE knob, as this tick may act on it: the environment's
    MIRROR_SHORTS (read through the rules module at call time) AND the
    050 column present. Read at every site that admits a short (the
    target's sign door, the candidate's open, the shadow's target
    column through its own read of the same constant); never cached
    across ticks."""
    return bool(rules.MIRROR_SHORTS) and bool(t.short_col)


def _increases_refusal(t: _Tick, whale: str) -> str | None:
    """Why this whale may not INCREASE this tick, or None. Allowlist,
    DB narrowing and demotion gate increases ONLY (addendum section 7):
    a whale removed from every list still has his books reduced and
    flattened by the steps below."""
    w = (whale or "").lower()
    if t.cancel_all:
        # a cancel-only tick -- a global guard, or a trip MID-TICK
        # (wrong sign, overfill) -- refuses every increase by the
        # trip's own name; the DB switch it wrote false is read on
        # the next tick (step-9 review: a candidate opened a book and
        # rested a BUY in the very tick that tripped live off)
        return t.cancel_all
    if t.mode == MODE_SAFE:
        return "mode_env_off"
    if t.mode == MODE_EXITS:
        return t.mode_db_refusal
    if t.increase_block:
        return t.increase_block
    if w not in t.allow:
        return "mode_env_off"
    if t.narrow_unreadable or t.demoted_unreadable:
        return "whales_unreadable"
    if t.narrow is not None and w not in t.narrow:
        return "mode_db_off"
    if w in t.demoted:
        return "demoted"
    return None


async def _read_mode(t: _Tick) -> None:
    """Step 0: the mode ladder. Every DB read fails closed to exits-only
    or to 'nobody increases'; the environment alone decides SAFE."""
    env = os.environ.get("PMUS_MIRROR", "off").strip().lower()
    if env not in (MODE_EXITS, MODE_ON):
        t.mode = MODE_SAFE
        t.stats["mode"] = MODE_SAFE
        _mirror_stop("mode_env_off")
        return
    t.allow = set(le.mirror_allowlist())
    t.stats["whales"] = sorted(t.allow)
    live, err = await _state(t.pool, _STATE_LIVE)
    if env == MODE_EXITS:
        t.mode, t.mode_db_refusal = MODE_EXITS, "mode_env_off"
        _mirror_stop("mode_env_off")
    elif err is not None:
        t.mode, t.mode_db_refusal = MODE_EXITS, "mode_db_unreadable"
        _mirror_stop("mode_db_unreadable")
    elif live is not True:
        t.mode, t.mode_db_refusal = MODE_EXITS, "mode_db_off"
        _mirror_stop("mode_db_off")
    else:
        t.mode = MODE_ON
    t.stats["mode"] = t.mode
    if t.mode != MODE_ON:
        return
    narrow, err = await _state(t.pool, _STATE_WHALES)
    if err is not None:
        t.narrow_unreadable = True
        _mirror_stop("whales_unreadable")
    elif narrow is not None:
        if isinstance(narrow, list):
            t.narrow = {str(w).strip().lower() for w in narrow}
        else:
            t.narrow_unreadable = True          # malformed narrows to nobody
            _mirror_stop("whales_unreadable")
    demoted, err = await _state(t.pool, _STATE_DEMOTED)
    if err is not None:
        t.demoted_unreadable = True
        _mirror_stop("whales_unreadable")
    elif isinstance(demoted, list):
        t.demoted = {str(w).strip().lower() for w in demoted}
    elif demoted is not None:
        t.demoted_unreadable = True
        _mirror_stop("whales_unreadable")


async def _global_guards(t: _Tick) -> None:
    """Step G, in maybe_execute's order. A tripped global guard makes
    the tick cancel-only (t.cancel_all); a tripped increase-only guard
    stops increases and lets reductions and flattens run."""
    if not settings().copy_probe_enabled:
        t.cancel_all = "probe_disabled"
    elif le.copy_halted():
        t.cancel_all = "halted"
    elif await le._is_paused(t.pool):
        t.cancel_all = "paused"
    elif await le.overspend_halt(t.pool):
        t.cancel_all = "overspend_halt"
    if t.cancel_all:
        _mirror_stop(t.cancel_all)
        return
    # THE ADMIN FLATTEN IS A REDUCTION LEVER, read in every mode that
    # can sell: it once sat below the mode early-return, so with the DB
    # switch false, absent or unreadable -- exactly the state every
    # trip leaves behind -- or PMUS_MIRROR=exits, the lever was inert
    # (step-9 review). SAFE never reaches here and still only cancels.
    flat, err = await _state(t.pool, _STATE_FLATTEN)
    if err is None and flat is True:
        t.flatten_all = True
        _mirror_stop("mirror_flatten")
    # S4: the short cover's read-back proof, read in every mode that can
    # exit (the cover is an exit). Unreadable or malformed reads as
    # unproven AND stops the probe (nothing is placed on a key we cannot
    # read back into)
    proof, err = await _state(t.pool, _STATE_S4)
    t.s4_proof_err = err
    t.s4_proof = proof if isinstance(proof, dict) else None
    if t.mode != MODE_ON:
        return
    # THE SLEEVE'S BREAKER OVER THE MIRROR'S WINDOW (L2, 2026-09-07).
    # le._loss_breaker_tripped sums EVERY settled live_orders row of the
    # last 24 h, and the mirror's standing rows settle there too, so the
    # morning's -5,000 tripped it for the rest of the day whatever the
    # re-arm said (18:23Z: `loss_breaker` 14 under a re-armed stop; the
    # owner: "turn the trip off"). The mirror reads the same sum from
    # the start ITS stop uses: _loss_window_start off the re-arm key,
    # read ONCE here (t.rearm) and reused by _loss_stop. A raised
    # re-arm read is the FULL window here -- the wider one; _loss_stop
    # then stops the tick on that same read -- and a malformed key
    # reads as absent (_rearm_at). The threshold is the sleeve's own
    # (le.PMUS_LOSS_BREAKER_USD); the stance on an unreadable ledger is
    # unchanged (refuse, by name). The copy lane's own call is untouched.
    # Behind a STANDING (or unreadable) stop key nothing else is read
    # (review L1): the stop key is read here first, cached for the stop
    # step below, and the sleeve then reads its full window without
    # consulting the re-arm key -- the tick is exits-only either way.
    t.stop = await _state(t.pool, _STATE_LOSS_STOP)
    stop_val, stop_err = t.stop
    if stop_err is not None or stop_val is not None:
        since = None
    else:
        rearm_val, rearm_err = await _read_rearm(t)
        if rearm_err is not None and rearm_err != "malformed":
            since = None
        else:
            since = _loss_window_start(t.now, _rearm_at(rearm_val, rearm_err, t.now))
    lost = await le._loss_breaker_sum(t.pool, None if since is None else _utc(since))
    limit = float(le.PMUS_LOSS_BREAKER_USD)
    if lost is None:
        t.increase_block = "loss_breaker_unreadable"
    else:
        t.sleeve = {"sum": round(lost, 4), "limit": limit,
                    "since": None if since is None else _iso(since)}
        if lost <= -limit:
            log.warning("LOSS BREAKER: copy sleeve realized %.2f since %s (threshold -%.0f)"
                        " -- the mirror refuses increases", lost,
                        t.sleeve["since"] or "24h", limit)
            t.increase_block = "loss_breaker"
    if t.increase_block is None:
        try:
            # THE SLEEVE'S TOTAL ROOM BINDS; ITS DAILY ROOM DOES NOT
            # (owner order ~14:00Z 2026-09-06, "Let's remove those caps
            # ... this limitation should never force us to decline any
            # of the possible copies"): le._copy_day_room's day figure is
            # the COPY lane's live_max_daily_usd, a day cap on the mirror
            # by another road, so it is read and set aside -- the two
            # lanes no longer share a day budget. The rest lane's
            # in-flight reservations come off the TOTAL room instead
            # (addendum section 7's concurrent-placement guard, now per
            # lane against the room that still binds). The sleeve's
            # lifetime knob defaults to no ceiling; room_scale reads a
            # number, so an unbounded room is written as the largest
            # bound there is. An unreadable ledger is still no room.
            async with le._REST_LOCK:
                _day_unused, total = await le._copy_day_room(t.pool, settings())
                total -= float(le._REST_RESERVED_USD or 0.0)
            t.day_room = 1e12
            t.total_room = float(total) if math.isfinite(total) else 1e12
        except Exception as exc:  # noqa: BLE001 — an unreadable ledger is no room
            log.warning("mirror_live: sleeve room unreadable (%s)", type(exc).__name__)
            t.increase_block = "no_budget_room"
        else:
            if not (t.total_room > 1):
                t.increase_block = "no_budget_room"
    if t.increase_block is None:
        # the mirror's own day cap: UNBOUNDED by default since 2026-09-06
        # (rules.MIRROR_DAY_USD, math.inf; the environment may lower it)
        day_cap = float(rules.MIRROR_DAY_USD)
        try:
            # the day read is switched on the per-tick 050 guard exactly
            # as the open-orders read and the INSERT are: the 050 text
            # names the column, and against a database without it every
            # tick read `mirror_day_cap` for good (P2 rung S0 review).
            # READ WHATEVER THE CAP: an unreadable spend is refused by
            # name under an unbounded cap too (fail closed)
            row = await t.pool.fetchrow(_SQL_MIRROR_DAY if t.short_col else _SQL_MIRROR_DAY_047)
            filled = float((row or {})["filled"] or 0.0) if row else 0.0
            resting = float((row or {})["open"] or 0.0) if row else 0.0
            t.mirror_day = (day_cap if math.isfinite(day_cap) else 1e12) - filled - resting
        except Exception as exc:  # noqa: BLE001
            log.warning("mirror_live: mirror day spend unreadable (%s)", type(exc).__name__)
            t.increase_block = "mirror_day_cap"
        else:
            # the block is on what FILLED and the room on what filled
            # plus what rests (the paragraph over _SQL_MIRROR_DAY); the
            # room is published for the quiet-tick mode line -- as null
            # under an unbounded cap (never 1e12 to an operator; the
            # mode line prints `day=none`), and the block can bite only
            # on a FINITE cap
            if math.isfinite(day_cap):
                t.stats["mirror_day_room"] = round(t.mirror_day, 2)
                if filled >= day_cap:
                    t.increase_block = "mirror_day_cap"
            else:
                t.stats["mirror_day_room"] = None
    if t.increase_block is None:
        # L2: the stop key was read once already for the sleeve (t.stop)
        stop, err = t.stop if t.stop is not None else await _state(t.pool, _STATE_LOSS_STOP)
        if err is not None or stop is not None:
            t.increase_block = "mirror_loss_stop"
        else:
            await _loss_stop(t)
    if t.increase_block:
        _mirror_stop(t.increase_block)


async def _read_rearm(t: _Tick) -> tuple:
    """The re-arm key's ONE read per tick (L2), cached on t.rearm for
    whoever asks first -- _global_guards' sleeve read, then _loss_stop --
    and the only place in the worker that reads it (nothing here writes
    it: only the mirror-rearm preset does)."""
    if t.rearm is None:
        t.rearm = await _state(t.pool, _STATE_LOSS_REARM)
    return t.rearm


async def _loss_stop(t: _Tick) -> None:
    """The loss stop's read and trip, with no stop key standing. L1 (the
    paragraph over _STATE_LOSS_REARM): the re-arm key is read beside the
    stop's -- a raised read is a stop exactly as the stop key's, a
    malformed one reads as absent -- and the window starts at the newest
    re-arm inside LOSS_WINDOW_S, else at the window's edge; that start is
    the one parameter _SQL_LOSS_SUM takes. The tick publishes what it
    read (`loss` on the stats: the mode line, the raw heartbeat), and a
    trip's receipt carries `since` and `rearmed_at` beside sum/books/
    limit, so mirror-state shows what window tripped; the tick keeps the
    same reading (t.loss) for the mode line. The limit and the exit
    carve-out are as they were. L2: the re-arm key is read ONCE per tick
    (_read_rearm; _global_guards' sleeve read is reused)."""
    rearm, err = await _read_rearm(t)
    if err is not None and err != "malformed":
        t.increase_block = "mirror_loss_stop"
        return
    rearm_at = _rearm_at(rearm, err, t.now)
    since = _loss_window_start(t.now, rearm_at)
    try:
        row = await t.pool.fetchrow(_SQL_LOSS_SUM, _utc(since))
        lost = float((row or {})["lost"] or 0.0) if row else 0.0
        books = int((row or {})["books"] or 0) if row else 0
    except Exception as exc:  # noqa: BLE001 — a stop that cannot be read is a stop
        log.warning("mirror_live: loss stop unreadable (%s)", type(exc).__name__)
        t.increase_block = "mirror_loss_stop"
        return
    window = {"sum": round(lost, 4), "books": books, "limit": float(rules.MIRROR_LOSS_STOP_USD),
              "since": _iso(since), "rearmed_at": None if rearm_at is None else _iso(rearm_at)}
    t.loss = dict(window)
    if lost <= -float(rules.MIRROR_LOSS_STOP_USD):
        try:
            await _write_state(t.pool, _STATE_LOSS_STOP, {"at": _iso(t.now), **window})
        except Exception:  # noqa: BLE001 — the tick still refuses
            log.warning("mirror_live: loss stop receipt not written", exc_info=True)
        t.increase_block = "mirror_loss_stop"


async def _read_open(t: _Tick) -> list | None:
    """The account's open orders, ONE call per tick, read on first
    need. None when the venue could not be listed ('open_orders_unreadable'):
    a lane that cannot see the book does not place on it."""
    async with t.open_lock:             # E2: a second caller waits for the one read
        if t.open_tried:
            return t.open
        t.open_tried = True
        try:
            t.open = list(await _venue_read(t, t.pmus.open_orders) or [])
        except Exception as exc:  # noqa: BLE001
            t.open_error = type(exc).__name__
            _mirror_stop("open_orders_unreadable")
            log.warning("mirror_live: open orders unreadable (%s)", t.open_error)
            t.open = None
        return t.open


async def _read_protected(t: _Tick) -> set | None:
    async with t.protected_lock:
        if t.protected_tried:
            return t.protected
        t.protected_tried = True
        t.protected = await le._protected_order_ids(t.pool)
        if t.protected is None:
            _mirror_stop("protected_ids_unreadable")
        return t.protected


async def _snapshot(t: _Tick, whale: str) -> tuple[dict, float | None, bool]:
    async with t.snap_lock:             # E2: one read per whale per tick, whoever asks first
        if whale not in t.snaps:
            t.snaps[whale] = await ms.snapshot_sizes(t.pool, whale)
        return t.snaps[whale]


_STATE_OPEN = "MARKET_STATE_OPEN"


def _miss(t: _Tick, why: str, detail: str | None = None, count: bool = True,
          walk_slug: str | None = None) -> None:
    """One quote read that is not a quote to trade on, by name. `count`
    says whether it is evidence of a VENUE-WIDE outage: the
    MISS_STREAK_ABANDON-th counted miss in a row abandons the tick
    under its name; an uncounted one is refused by name and leaves the
    streak exactly where it stood -- neither a step nor a reset.

    `walk_slug` is set on an open book's read DURING the parallel walk
    (E2 review, MEDIUM-4): the miss is recorded by slug for _walk_streak
    to judge in WALK order, never on the live streak, whose arrival
    order is the interleaving's."""
    _mirror_stop(why)
    if not count:
        return
    if walk_slug is not None:
        t.book_reads[walk_slug] = (why, detail)
        # judged NOW, inside the read, as the sequential walk did: the
        # book that completes the streak abandons before its own plan
        # is written (its updated_at stays, E1's round-robin walks it
        # first next tick)
        _judge_walk(t, t.walk_order, {"us_market_slug": walk_slug})
        return
    t.misses += 1
    if t.misses >= ms.MISS_STREAK_ABANDON:
        _abandon(t, why, detail)


def _walk_streak(t: _Tick, ordered: list) -> int:
    """The book walk's miss streak in WALK ORDER (E2 review, MEDIUM-4):
    over `ordered` up to the first book that is not yet JUDGEABLE --
    its quote read has not landed and its tick has not finished -- a
    counted miss steps the run, a quoted read resets it, an uncounted
    read (a halt on a book, an empty open book naming its state) or a
    book that read no quote (closing, frozen) leaves it. The
    MISS_STREAK_ABANDON-th in a row abandons the tick under the last
    miss's name -- the sequential walk's verdict, whatever order the
    reads landed in. Returns the run at the last judged book, which
    after the whole walk is what the sequential walk left t.misses at."""
    streak, last = 0, None
    for book in ordered:
        slug = str(book.get("us_market_slug"))
        if book["id"] not in t.walk_done and slug not in t.book_reads:
            break
        out = t.book_reads.get(slug)
        if out is False:
            streak = 0
        elif out:
            streak += 1
            last = out
            if streak >= ms.MISS_STREAK_ABANDON and not t.abandoned:
                _abandon(t, last[0], last[1])
    return streak


async def _bbo(t: _Tick, slug: str, book: bool = False) -> tuple[float | None, float | None]:
    """The quote read, and the venue's own word for the market it read.
    Three names, never one (2026-09-05, when five hours of a venue-wide
    MARKET_STATE_HALTED read as `no_quote`, the word for an unreadable
    book, and cost forty minutes and an external probe):

      no_quote      the read failed on every feed (the error is named
                    on the WARNING) -- OR the market is OPEN (or the
                    state is absent, the shape the SDK's typed dict
                    promises) and both quotes are empty: an open market
                    with no makers is not a placement either
      venue_halted  the venue says the market is not OPEN (HALTED,
                    SUSPENDED, PREOPEN, CLOSED, EXPIRED, ...): the
                    state is recorded on the tick and the census, and
                    a quote on such a market -- a settled market's
                    stale rests -- is NOT a quote to trade on
      a quote       on an OPEN market: the miss streak resets

    THE MISS STREAK (t.misses, ms.MISS_STREAK_ABANDON) COUNTS ONLY WHAT
    CAN BE A VENUE-WIDE OUTAGE. It was built for one (every read empty)
    and it abandons the tick and backs the loop off BACKOFF_S, so a
    per-market fact that fed it cost the mirror its cadence twice on
    2026-09-06: at 00:40Z-00:47Z, with the venue OPEN and quoting the
    markets he was in, three thin markets with empty books in a row
    abandoned every tick `no_quote` (one new book per ~90 s, exits on
    the later books walked every ~90 s instead of every 30 s); at
    12:32Z one open book whose market had EXPIRED, plus his morning's
    expired markets still inside the candidate lookback, abandoned
    ticks `(venue_halted: MARKET_STATE_EXPIRED)` between placements.

      counts    an unreadable read (the error is named), on any read;
                an empty read that names no state (the SDK-typed
                shape: it cannot be told from a halt), on any read; a
                non-OPEN, NON-TERMINAL state (HALTED, SUSPENDED,
                PREOPEN, anything not in ms.STATE_TERMINAL), quoted or
                not, on a CANDIDATE read (`book=False`)
      neither   an OPEN market with an empty book (`no_quote`, the
                per-market refusal: the walk goes on to the next
                candidate); any non-OPEN state on an EXISTING BOOK's
                read (`book=True`: the book's own handling names it
                `venue_halted`, cancels its rests under `no_mark`,
                plans nothing, refuses the flatten's slippage leg and
                closes it once the markets row reads closed -- and an
                abandon inside the book walk would skip every book
                after it, un-managed for the tick and the backoff);
                a terminal state (EXPIRED, CLOSED, TERMINATED, the
                closing MATCH_AND_CLOSE_AUCTION) on any read -- a
                market that has ended is a per-market fact,
                a venue cannot expire every market
      resets    a quoted read on an OPEN market, or one naming no state

    A venue-wide halt reads as before: three HALTED candidates in a
    row abandon `(venue_halted: MARKET_STATE_HALTED)`, and every read
    still counts `venue_halted` or `no_quote` on the census and
    publishes the state. The mirror resumes on its own when the state
    reads OPEN and a quote is present; nothing here is sticky."""
    # a candidate's read counts against the soft guard; an open book's
    # never does (E2: the books' reads are the tick's fixed cost)
    _venue_call(t, guard=not book)
    # an open book's read during the parallel walk records its outcome
    # by slug for the walk-order streak (E2 review, MEDIUM-4); a
    # candidate's read, and a book's read outside the walk (the book a
    # candidate just opened), step the live streak as before
    ws = slug if (book and t.walking) else None
    t0 = time.monotonic()
    try:
        q = await asyncio.to_thread(ms._paced_bbo, t.pmus, slug)
    except Exception as exc:  # noqa: BLE001 — an unreadable book is no quote
        q = {"bid": None, "ask": None, "state": None, "error": type(exc).__name__}
    if book:
        # E6: an existing book's paced quote read, summed per call
        t.timing["books_venue"] += time.monotonic() - t0
    t.reads += 1
    if not book:
        t.cand_reads += 1           # the candidate walk's budget; a book's read is never charged to it
    q = q if isinstance(q, dict) else {}
    bid, ask, state, err = q.get("bid"), q.get("ask"), q.get("state"), q.get("error")
    if err:
        t.slug_states[slug] = None
        if ms.is_rate_limit(err):
            # the venue's RATE LIMIT is not an outage and not an empty
            # book (E2 review round 2, HIGH-B/MEDIUM): named
            # `rate_limited`, the pacer's circuit tripped, this market
            # refused for the tick -- and the outage streak untouched
            # (neither a miss nor a reset)
            _rate_limited(t, None, f"quote read {slug}")
            return None, None
        log.warning("mirror_live: BBO for %s unreadable (%s)", slug, err)
        _miss(t, "no_quote", walk_slug=ws)
        return None, None
    state = str(state) if state is not None else None
    # the state per slug rides on the tick, for the reading the flatten's
    # slippage leg refuses on (_flatten_vanished): slug_bid reads the
    # sell bid through _bbo_quotes, which carries no state of its own
    t.slug_states[slug] = state
    if state is not None:
        t.venue_states[state] += 1
        t.stats["venue_state"] = t.venue_states.most_common(1)[0][0]
        if state != _STATE_OPEN:
            _miss(t, "venue_halted", state,
                  count=not book and state not in ms.STATE_TERMINAL, walk_slug=ws)
            return None, None
    if bid is None and ask is None:
        # OPEN and empty: the venue is up and this market has no makers,
        # a per-market refusal. Only a read that named no state counts
        _miss(t, "no_quote", count=state is None, walk_slug=ws)
    elif ws is not None:
        t.book_reads[ws] = False    # a quoted read: a reset, in walk order
    else:
        t.misses = 0
    return bid, ask


def _mark_of(bid, ask) -> float | None:
    b, a = _num(bid), _num(ask)
    if b is not None and a is not None and 0.0 < b < 1.0 and 0.0 < a < 1.0:
        return round((b + a) / 2.0, 4)
    if a is not None and 0.0 < a < 1.0:
        return a
    return None


def _abandon(t: _Tick, why: str, detail: str | None = None, *,
             rate_limited: bool = False) -> None:
    """`why` is the census name (`abandon_reason`); `detail` rides on
    the WARNING alone -- the venue's state string on a venue_halted
    abandon, so the line reads `(venue_halted: MARKET_STATE_HALTED)`.
    `rate_limited` says this abandon FOLLOWS a venue 429 the site
    already handed to _rate_limited (E2 review round 4, LOW-4): the
    60 s backoff is skipped while the pacer's circuit holds, whatever
    `why` names -- a placement's `rate_limited`, the positions walk's
    `positions_unreadable`. An abandon that names no 429 backs off as
    before, a circuit from an earlier 429 notwithstanding."""
    global _backoff_until
    if not t.abandoned:
        t.abandoned = True
        t.stats.update(abandoned=True, status="degraded", abandon_reason=why)
        _mirror_stop("tick_abandoned")
        if rate_limited and venue_pace.penalty_left() > 0.0:
            # A 429 ABANDON (E2 review round 3, MEDIUM-3; round 4,
            # LOW-4): the abandon stays (mid-tick consistency: nothing
            # more is placed), the pacer's circuit already halves the
            # rate for ten minutes, and the 60 s backoff on top left
            # every exit unmanaged for a minute -- skipped, by name,
            # while the circuit holds. The flag is the guard; the
            # `penalty_left() > 0.0` clause is kept beside it (LOW-5)
            # because every site that passes the flag calls
            # _rate_limited FIRST in the same block, so the circuit it
            # reads is the one that 429 just tripped -- a flagged
            # abandon with no circuit (never in production) still backs
            # off rather than run the next tick at the plain gap
            _mirror_stop("backoff_skipped_circuit")
            log.warning("mirror_live: tick abandoned (%s); the pacer circuit holds (%.0fs left), "
                        "no backoff", f"{why}: {detail}" if detail else why, venue_pace.penalty_left())
            return
        _backoff_until = t.now + ms.BACKOFF_S
        log.warning("mirror_live: tick abandoned (%s), backing off %ss",
                    f"{why}: {detail}" if detail else why, ms.BACKOFF_S)


async def _abandon_reconciled(t: _Tick, why: str, *, rate_limited: bool = False) -> None:
    """Abandon a tick that cannot read, AFTER settling what is already at
    the venue. The three unreadable-read returns used to sit ABOVE step O,
    so a walk we could not read left our live rests standing: unbooked (no
    fill recorded, no terminal state written) and un-TTL'd (no expiry
    cancelled) for the whole backoff. Reconciling first books what filled
    and cancels what should not stand; only then do we stop. A reconcile
    that itself fails is named and the abandon still happens -- refusing to
    plan is the point, and it must not depend on the settling succeeding."""
    try:
        await _reconcile_orders(t)
    except Exception as exc:  # noqa: BLE001 — the abandon is not optional
        t.stats["reconcile_skipped"] = type(exc).__name__
        log.warning("mirror_live: reconcile before abandon failed (%s)", type(exc).__name__)
    _abandon(t, why, rate_limited=rate_limited)


async def _market(t: _Tick, cid: str) -> dict | None:
    """{closed, resolved, resolved_prices} or None when unreadable."""
    try:
        row = await t.pool.fetchrow(_SQL_MARKET, cid)
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    return {"closed": row["closed"], "resolved": row["resolved"],
            "resolved_prices": row["resolved_prices"]}


# ------------------------------------------------------------ the ledger

class _Rebook(Exception):
    """Someone advanced the cursor first: roll back and re-read."""


class _RowNotLive(Exception):
    pass


class _Refused(Exception):
    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


def _book_state(book: dict) -> rules.BookState:
    return rules.BookState(ledger_net=float(book.get("ledger_net") or 0.0),
                     avg_cost=book.get("avg_cost"),
                     gross_buy_usd=float(book.get("gross_buy_usd") or 0.0),
                     gross_sell_usd=float(book.get("gross_sell_usd") or 0.0),
                     peak_exposure_usd=float(book.get("peak_exposure_usd") or 0.0),
                     realized_pnl=float(book.get("realized_pnl") or 0.0),
                     intent=book.get("intent"))


# ---------------------------------------------------- P2: the sign helpers
#
# The book's intent (mirror_books.intent, fixed at open) decides what a
# plan side and an order row MEAN (brief 3.3): on a long book a BUY_LONG
# row grows the leg and a SELL_LONG row shrinks it; on a short book the
# other way round. Every site that used to branch on `side == BUY` for
# "does this grow what we hold" branches on the LEG ACTION now, and on a
# long book the two are the same predicate (rules.leg_action).

def _book_short(book: dict) -> bool:
    return rules.is_short(book.get("intent"))


# ------------------------------------------- the per-game cap (E1)
#
# The $2,500 cap is per GAME across every market of the game (owner
# orders 2026-09-06 ~14:00Z and ~22:3xZ; rules.book_exposure, game_room,
# game_capped carry the arithmetic). The tick indexes every non-closed
# book by game_key before the walk -- EVERY whale's books of one game
# share the $2,500: the order is "no single EVENT having more than
# $2.5k on it", not one event per whale -- walks a game's books in id
# order, and hands each book's target the room its game has left after
# the OTHER books' exposure -- held at cost plus the resting increase,
# or the new target of a book sized earlier this tick.

def _game_key_of(book: dict) -> tuple:
    """The game a book belongs to: its game_key ALONE, whoever the
    whale (review of the first cut, which keyed on (whale, game_key)
    and let two whales on one game hold $5,000). A book with no
    game_key is its own game, keyed on its own id (rule 5)."""
    gk = book.get("game_key")
    if gk:
        return ("game", str(gk))
    return ("book", str(book.get("id")))


def _index_games(t: _Tick, books: list) -> None:
    t.game_books = {}
    t.game_sized = {}
    t.marks = {}
    for b in sorted(books, key=lambda b: int(b.get("id") or 0)):
        t.game_books.setdefault(_game_key_of(b), []).append(b)


def _resting_add_usd(t: _Tick, book: dict) -> float | None:
    """The unfilled notional of the book's resting INCREASE this tick
    (t.open_by_book, as step O left it or as _place wrote it): (qty -
    booked) x wire on a long book, x (1 - wire) -- the collateral -- on
    a short one. 0 with nothing resting or a resting reduce; None when
    the row's figures cannot be read -- and None when the book has a
    NON-TERMINAL order the tick could NOT read into open_by_book: a
    rest whose cancel did not land ('unknown', _cancel_and_settle), one
    whose status read failed (_reconcile_open), a 'placing' row with no
    id, a lost placement, an unbooked fill. Each of those may still
    stand on the venue and fill, and a figure nobody can read is no
    figure: the game's room is 0 for every sibling and the book's own
    sized figure is None (review of the first cut, which read such a
    book at $0 and let a sibling take the whole $2,500 beside a 3,600
    @ 0.49 rest that still stood)."""
    ent = t.open_by_book.get(book["id"])
    if ent is None:
        if book["id"] in t.nonterminal:
            return None
        return 0.0
    o, _st = ent
    if _order_action(o, book) != "add":
        return 0.0
    qty, wire = _num(o.get("qty")), _num(o.get("wire"))
    booked = _num(o.get("booked_filled")) or 0.0
    if qty is None or wire is None or not (0.0 < wire < 1.0):
        return None
    rem = max(0.0, qty - booked)
    return round(rem * ((1.0 - wire) if _book_short(book) else wire), 4)


def _held_exposure(t: _Tick, book: dict) -> float | None:
    """rules.book_exposure for one book as this tick can read it: the
    mark is the one this tick read for the book, else the one its last
    plan carried (a book later in the walk has not been read yet)."""
    rest = _resting_add_usd(t, book)
    if rest is None:
        return None
    mark = t.marks.get(book["id"])
    if mark is None:
        mark = _num((_jsonish(book.get("last_plan")) or {}).get("mark"))
    return rules.book_exposure(book.get("ledger_net"), book.get("avg_cost"), mark,
                               book.get("intent"), rest)


def _game_exposure(t: _Tick, book: dict) -> float | None:
    """The exposure of the OTHER non-closed books of the book's game
    this tick: a book sized earlier in the walk counts its sized figure
    (t.game_sized), any other its held exposure. None when any of them
    is unreadable -- the room is then 0 (fail closed), never a guess."""
    total = 0.0
    for b in t.game_books.get(_game_key_of(book), []):
        if b["id"] == book.get("id"):
            continue
        e = t.game_sized[b["id"]] if b["id"] in t.game_sized else _held_exposure(t, b)
        if e is None:
            return None
        total += e
    return round(total, 4)


def _book_intent(book: dict) -> str:
    """The book's BUY intent: BUY_SHORT on a short book, BUY_LONG on any
    other (P1 rows carry the column at its default)."""
    return ORDER_INTENT_SHORT if _book_short(book) else ORDER_INTENT


def _order_action(o: dict, book: dict) -> str:
    """'add' when this row GROWS the leg the book holds, 'reduce' when
    it shrinks it. A book whose intent cannot be read is read as the
    long book it was in P1: BUY grows, SELL shrinks."""
    a = rules.leg_action(book.get("intent"), o.get("side"))
    if a is None:
        a = "add" if o.get("side") == BUY else "reduce"
    return a


def _priced_exit_rest(o: dict, book: dict) -> bool:
    """Is `o` an EXIT rest AT HIS CENT (E4 rule 3): a rest that shrinks
    the leg, placed or last kept under a plan that priced it off his
    fill -- the book's last plan says `exit_px_src: 'his_fill'`, the
    same predicate _place_reserved reads for the no-good-till. Such a
    rest past its TTL is the plan's to decide (keep_or_replace's
    `stands`, `requote_same_wire`), never step O's TTL clause. An
    UNPRICED reduce rest -- he gave no exit price (a snapshot-driven
    reduction, a vanish with no fill of his, the admin flatten) -- is
    not one: it sits at the ask, not at a cent of his, and keeps
    today's `ttl` re-quote (E4 review round 3, D-1/D-2). The plan is
    read off the book because step O runs before any book is planned
    and the order row records no price source. With NO plan on file
    (a row placed before its plan was written, a book from before E4)
    the row's own facts decide: a long book's rest whose wire is the
    cent of the level it was placed against (`his_level` on the row,
    rules.sell_wire) is at his cent; any other -- a rest the ask
    lifted, a rest with no level of his, a short book's (none exists
    before rung S4) -- is unpriced, the fail-closed side (a TTL
    re-quote is never a shed exit: its re-rest rides the credit)."""
    if _order_action(o, book) != "reduce":
        return False
    src = (_jsonish(book.get("last_plan")) or {}).get("exit_px_src")
    if src is not None:
        return src == "his_fill"
    if _book_short(book):
        return False
    w, lvl = _num(o.get("wire")), rules.sell_wire(o.get("his_level"))
    return w is not None and lvl is not None and abs(w - lvl) < 1e-6


def _order_intent(o: dict, book: dict) -> str | None:
    """The WIRE intent of an order row. On a LONG book the plan side
    decides it alone (BUY_LONG grows, SELL_LONG shrinks) and the 050
    column adds nothing -- and must not be consulted: 050's DEFAULT
    back-fills BUY_LONG onto every pre-050 row, a resting SELL_LONG
    included, and a reader that trusted it would compare BUY_LONG to
    the plan's SELL_LONG and REPLACE every such rest on the first tick
    after the migration (P2 rung S0 review). On a SHORT book the column
    is read only when it carries one of the two short spellings; any
    other value (a row written before 050, the 047 projection while the
    column is absent) derives from the book and the plan side."""
    if _book_short(book):
        v = o.get("intent")
        if v in (ORDER_INTENT_SHORT, "ORDER_INTENT_SELL_SHORT"):
            return v
    w = rules.wire_side(book.get("intent"), o.get("side"))
    return None if w is None else w[0]


def _leg_of(book: dict) -> int:
    """The LEG the book holds, whole shares: |ledger_net| on a short
    book, the ledger itself on a long one (never negative there)."""
    ledger = int(book.get("ledger_net") or 0)
    return abs(ledger) if _book_short(book) else ledger


def _cost_px(px: float, book: dict) -> float:
    """The cost of one share at contract price `px` for this book: the
    executor's own formula (le.cost_per_share), so the day cap, the
    reserve and the row's requested_usd all read the collateral a short
    ties up rather than the contract price."""
    return float(le.cost_per_share(float(px), _book_intent(book)))


async def _book_fill(t: _Tick, o: dict, book: dict, inc: float, px: float | None,
                     maker: bool, taker_at_placement: bool = False) -> str:
    """Book one fill delta: ONE transaction -- the mirror_orders cursor
    (WHERE booked_filled = expected; 0 rows means someone booked it:
    roll back, re-read), the standing-row statement, the book's ledger
    columns. A crash between the venue read and this write re-books
    exactly once on the next tick. Returns 'booked' | 'rebooked' |
    'duplicate' | 'row_not_live' | 'overfill' | 'refused:<why>'; a
    write failure PROPAGATES with the transaction rolled back (addendum
    section 8).

    A SELL past the ledger by MORE than rules.SELL_DUST_SHARES is the
    overfill (freeze, trip, receipt). A SELL past it by up to that one
    lot is DUST -- the rounding between a fractional standing row and
    the whole-share ledger column (2026-09-06 01:11:57Z: row 413.76,
    ledger 414, venue sold 414.0, tripped as an overfill) -- counted
    `ledger_dust`, noted in _recent, booked to the ledger, and the
    return is 'booked': no freeze, no trip.

    DUST ACCUMULATES PER ORDER. A partial fill arrives one delta per
    poll, and a row read flat on every poll would pass each delta as
    dust on its own: 1.0 then 1.0 is two lots the ledger never held.
    So the order's `dust_total` (kept on its receipt JSON, re-read with
    the row each tick) is the figure judged: the first delta that takes
    it past SELL_DUST_SHARES is the overfill, frozen and tripped with
    `dust_total` on the receipt; a single 0.24 never is."""
    expected = float(o.get("booked_filled") or 0.0)
    new_filled = expected + inc
    side = o["side"]
    sid = book["standing_row_id"]
    booking = None
    overfill = False
    dust = 0.0
    dust_total = _dust_total(o)
    held = None
    # THE LEG DECIDES THE BOOKING (P2 rung S0, brief A5 / B3): a row
    # that grows the leg books as a buy -- the cash it cost is
    # le.fill_cash on the BOOK's intent, (1 - px) x q on a short -- and
    # a row that shrinks it books as a sale in leg space; on a long
    # book that is exactly `side == BUY`, as before
    action = _order_action(o, book)
    intent = _book_intent(book)
    try:
        async with t.pool.acquire() as conn:
            async with conn.transaction():
                tag = await conn.execute(_SQL_ORDER_CURSOR, o["id"], inc, new_filled, px,
                                         expected)
                if _rowcount(tag) == 0:
                    raise _Rebook()
                if action == "add":
                    usd = float(le.fill_cash(inc, px, intent))
                    seq = int(await conn.fetchval(_SQL_ADDS_SEQ, sid, str(o["order_id"])) or 0)
                    row = await le._book_mirror_buy(
                        conn, sid, str(o["order_id"]), seq, inc, px, usd,
                        inc * _cost_px(float(o.get("wire") or 0.0), book), o.get("his_level"),
                        maker)
                    if row is None:
                        st = await conn.fetchrow(_SQL_STANDING_READ, sid)
                        if st is not None and st["status"] == "filled" and st["lane"] == "mirror":
                            # already on raw.adds: the cursor advance is
                            # kept, nothing else moves (addendum section 9)
                            await conn.execute(_SQL_ORDER_CASH, o["id"], 0.0, 0.0,
                                               bool(taker_at_placement))
                            return "duplicate"
                        raise _RowNotLive()
                    booking = rules.book_buy(_book_state(book), inc, px, usd)
                    if booking.refusal:
                        raise _Refused(booking.refusal)
                    ns = booking.state
                    await conn.execute(_SQL_BOOK_LEDGER_BUY, book["id"], int(round(ns.ledger_net)),
                                       ns.avg_cost, ns.gross_buy_usd, ns.peak_exposure_usd)
                    await conn.execute(_SQL_ORDER_CASH, o["id"], booking.usd, 0.0,
                                       bool(taker_at_placement))
                else:
                    res = await le._book_mirror_sell(conn, sid, inc, px,
                                                     float(book.get("ledger_net") or 0.0),
                                                     intent=intent)
                    if res.get("refusal") == "row_not_live":
                        raise _RowNotLive()
                    overfill = bool(res.get("overfill"))
                    dust = float(_num(res.get("dust")) or 0.0)
                    held = _num(res.get("held"))
                    if res.get("refusal") in ("bad_fill", "no_entry_price"):
                        raise _Refused(str(res["refusal"]))
                    booking = rules.book_sell(_book_state(book), inc, px)
                    overfill = overfill or booking.overfill
                    dust = max(dust, float(_num(booking.dust) or 0.0))
                    if booking.refusal:
                        raise _Refused(booking.refusal)
                    ns = booking.state
                    await conn.execute(_SQL_BOOK_LEDGER_SELL, book["id"], int(round(ns.ledger_net)),
                                       ns.gross_sell_usd, ns.realized_pnl)
                    await conn.execute(_SQL_ORDER_CASH, o["id"], booking.usd,
                                       booking.realized or 0.0, bool(taker_at_placement))
                    if dust > 0.0:
                        # the order's cumulative dust, written with the
                        # booking it belongs to: more than a lot on one
                        # order is the overfill, whatever each delta read
                        dust_total = round(dust_total + dust, 6)
                        await conn.execute(_SQL_ORDER_DUST, o["id"], dust_total)
                        overfill = overfill or dust_total > rules.SELL_DUST_SHARES
    except _Rebook:
        return "rebooked"
    except _RowNotLive:
        await _freeze(t, book, "row_not_live")
        return "row_not_live"
    except _Refused as exc:
        log.error("mirror_live: fill on order %s refused by the ledger (%s); nothing booked",
                  o["id"], exc.why)
        return f"refused:{exc.why}"
    # committed: carry the new figures through the rest of the tick
    o["booked_filled"] = new_filled
    o["filled"] = new_filled
    o["avg_px"] = px
    if booking is not None:
        ns = booking.state
        book.update(ledger_net=int(round(ns.ledger_net)), avg_cost=ns.avg_cost,
                    gross_buy_usd=ns.gross_buy_usd, gross_sell_usd=ns.gross_sell_usd,
                    peak_exposure_usd=ns.peak_exposure_usd, realized_pnl=ns.realized_pnl)
    if dust > 0.0:
        o["dust_total"] = dust_total
    # E5 review F1 / F2: the tick booked a fill on this book after the
    # walk -- the frozen exit refuses on it -- and a frozen seat set for
    # an act in flight (a cancel's settle inside _act) moves by the
    # leg-signed fill, so the replace re-plan and the clamps read the
    # venue as the fill left it, never the walk's stale figure
    t.filled_books.add(book["id"])
    fv = _num(book.get("_frozen_venue"))
    if fv is not None:
        sign = 1.0 if action == "add" else -1.0
        if _book_short(book):
            sign = -sign
        book["_frozen_venue"] = fv + sign * inc
    if 0.0 < new_filled < float(o["qty"]) - FLAT_TOL_SHARES:
        _mirror_stop("partial_fill", o["whale"])
        t.stats["partial_fills"] += 1
    _recent(book["id"], "fill", side=side, shares=round(inc, 4), px=px, maker=maker)
    if overfill and _frozen_excess(o, book, action, new_filled):
        # A FROZEN REDUCE'S FILL PAST THE LEDGER (E5 / P2): the row was
        # sized on the venue's own position (_frozen_exit, reason
        # `frozen_reduce`), so the shares past the ledger are the lost
        # response's -- held on the venue, never booked -- and the sale
        # cannot pass zero on the venue: the fill is within the row's
        # quantity and the row within the venue's reading. The ledger
        # booked what it held (book_sell / _book_mirror_sell, above);
        # the excess is named on the receipt with its price and counted
        # -- never the overfill trip, which is for a sale the venue's
        # own number does not cover
        excess = round(inc - float(booking.booked if booking is not None else 0.0), 6)
        await _note_frozen_excess(t, o, book, excess, px)
        return "booked"
    if overfill:
        # a SELL MORE than a lot past what the ledger held -- on this
        # delta, or summed over the order's deltas -- is a SHORT on a
        # signed-net venue: freeze (which names it), and trip the DB
        # switch off with the receipt -- sold, the ledger after the
        # booking, the standing row's shares before the sale, and the
        # order's cumulative dust
        if dust > 0.0 and dust_total > rules.SELL_DUST_SHARES:
            log.error("mirror_live: book %s SELL on order %s: dust_total %s on the order is past "
                      "SELL_DUST_SHARES (%s) -- the overfill, not dust",
                      book["id"], o["id"], dust_total, rules.SELL_DUST_SHARES)
        await _freeze(t, book, "overfill")
        await _trip_live_off(t, "overfill", {"book": book["id"], "order": o["id"],
                                             "sold": inc, "ledger": book.get("ledger_net"),
                                             "held": held, "dust_total": dust_total})
        return "overfill"
    if dust > 0.0:
        # within a lot of the ledger, on this order so far: rounding
        # between the fractional row and the whole-share ledger column,
        # booked to the ledger and counted -- no freeze, no trip
        # (2026-09-06)
        _mirror_stop("ledger_dust", o["whale"])
        _recent(book["id"], "dust", shares=dust, sold=round(inc, 4), held=held,
                ledger=book.get("ledger_net"), dust_total=dust_total)
        log.info("mirror_live: book %s SELL on order %s is %s shares past the ledger (venue sold %s, "
                 "row held %s): dust, booked to the ledger, no trip; dust_total %s on the order",
                 book["id"], o["id"], dust, round(inc, 4), held, dust_total)
    return "booked"


def _frozen_excess(o: dict, book: dict, action: str, new_filled: float) -> bool:
    """Is this overfill a frozen reduce's fill past the ledger (E5 /
    P2)? The row must be the frozen exit's own (reason `frozen_reduce`),
    shrink the leg, and the venue's cumulative fill must sit inside the
    row's quantity: a venue reporting MORE than the row asked for is
    the overfill it always was."""
    if action != "reduce" or not str(o.get("reason") or "").startswith("frozen_reduce"):
        return False
    return float(new_filled) <= float(o.get("qty") or 0.0) + FLAT_TOL_SHARES


async def _note_frozen_excess(t: _Tick, o: dict, book: dict, excess: float, px: float) -> None:
    """The frozen reduce's shares past the ledger, on the row's receipt
    (cumulative shares and proceeds, the last price) and the census."""
    rec = _jsonish(o.get("receipt"))
    prior = (rec.get("frozen_excess") if isinstance(rec, dict) else None) or {}
    shares = round(float(_num(prior.get("shares")) or 0.0) + max(excess, 0.0), 6)
    usd = round(float(_num(prior.get("usd")) or 0.0) + max(excess, 0.0) * float(px), 4)
    note = {"frozen_excess": {"shares": shares, "usd": usd, "px": px, "at": t.now}}
    try:
        await t.pool.execute(_SQL_ORDER_RECEIPT, o["id"], json.dumps(note, default=str))
    except Exception:  # noqa: BLE001 — the fill is booked; the note is for a human
        log.warning("mirror_live: could not note the frozen excess on order %s", o["id"],
                    exc_info=True)
    o["receipt"] = {**(rec if isinstance(rec, dict) else {}), **note}
    _mirror_stop("frozen_excess_sold", o["whale"])
    _recent(book["id"], "frozen_excess_sold", order_row=o["id"], shares=round(excess, 4), px=px,
            ledger=book.get("ledger_net"))
    log.warning("mirror_live: book %s frozen reduce on order %s sold %s shares past the ledger "
                "(the lost response's shares, inside the venue's own reading); ledger now %s",
                book["id"], o["id"], round(excess, 4), book.get("ledger_net"))


def _dust_total(o: dict) -> float:
    """The order's cumulative SELL dust so far: the tick's own figure
    when this tick already booked dust on it, else the receipt's
    `dust_total` as the open-orders read carried it, else 0.0."""
    own = _num(o.get("dust_total"))
    if own is not None:
        return max(own, 0.0)
    rec = _jsonish(o.get("receipt"))
    kept = _num(rec.get("dust_total")) if isinstance(rec, dict) else None
    return max(kept, 0.0) if kept is not None else 0.0


async def _trip_live_off(t: _Tick, why: str, receipt: dict) -> None:
    """The side-echo circuit's shape (live_executor:5551-5563): the DB
    switch goes false with a receipt naming the evidence, and this tick
    cancels everything. An admin turns it back on. t.cancel_all is read
    by _increases_refusal (no increase, no new book), by _place and the
    slippage flatten (nothing placed), by _act's keep path (a resting
    order is cancelled) and by _tick, which stops the book walk and
    cancels what step O kept before the trip (step-9 review)."""
    t.cancel_all = t.cancel_all or why
    try:
        await _write_state(t.pool, _STATE_LIVE, False)
        await _write_state(t.pool, f"{_STATE_LIVE}_trip",
                           {"at": _iso(t.now), "why": why, **receipt})
    except Exception:  # noqa: BLE001 — the tick is already cancel-only
        log.error("mirror_live: could not write the %s trip receipt", why, exc_info=True)
    log.error("MIRROR LIVE TRIPPED OFF: %s %s", why, receipt)


async def _freeze(t: _Tick, book: dict, reason: str, detail: dict | None = None) -> None:
    if book.get("state") in ("closed", "closing"):
        _mirror_stop(reason, book.get("whale"))
        return
    await t.pool.execute(_SQL_BOOK_FREEZE, book["id"], reason)
    # the census name and the recent entry are a TRANSITION's, or a
    # reason NEW to this book (an overfill on a frozen book is a new
    # event; the W2 once-per-transition convention). A re-freeze under
    # the reason the book already carries counts nothing and emits
    # nothing (E5 review v3, V3-2) -- the `frozen_reasons` gauge below
    # stays the tick's, as before
    new_reason = book.get("state") != "frozen" or book.get("frozen_reason") != reason
    if new_reason:
        _mirror_stop(reason, book.get("whale"))
    if book.get("state") != "frozen":
        book["frozen_reason"] = reason
    book["state"] = "frozen"
    book["last_reason"] = reason
    book["frozen_ts"] = book.get("frozen_ts") or t.now
    book["frozen_ticks"] = int(book.get("frozen_ticks") or 0) + 1
    fr = t.stats["frozen_reasons"]
    fr[reason] = fr.get(reason, 0) + 1
    if new_reason:
        _recent(book["id"], "frozen", reason=reason, **(detail or {}))


async def _thaw(t: _Tick, book: dict) -> None:
    if book.get("state") != "frozen":
        return
    await t.pool.execute(_SQL_BOOK_THAW, book["id"])
    book.update(state="live", frozen_reason=None, frozen_ts=None)
    _recent(book["id"], "thawed")


async def _disarm_take(t: _Tick, book: dict) -> None:
    """Clear the take armed by a post-only 400. The arm says "the book
    was crossing when the rest was refused"; a rest that then PLACED,
    or an order that finished, contradicts or consumes that evidence,
    and a stale arm let a five-second-old rest be taken the instant
    the market touched it, past the rest-first wait (step-9 review;
    critic C15's never-IOC-first)."""
    if book.get("take_armed_ts") is None:
        return
    await t.pool.execute(_SQL_BOOK_ARM, book["id"], False)
    book["take_armed_ts"] = None


# ---------------------------------------------------------- step O: orders

def _order_side_of(o: dict) -> str:
    return "SELL" if o.get("side") == SELL else "BUY"


def _on_book_matches(o: dict, venue: dict, wire: float, qty: int) -> bool:
    """The rest lane's _bid_matches generalised to a side: OUR side, our
    cent, our whole quantity.

    THE SIDE IS THE WIRE INTENT WHEN BOTH NAME ONE (S4 review, F4). A
    cover row is side BUY (the plan side: a BUY of the long token) with
    intent SELL_SHORT, and pmus._norm_order derives the venue row's
    `side` from the intent string -- 'SELL' for any *_SELL_* intent --
    so the plan side and the derived side could never agree on a
    cover, and a cover placement whose response was lost was never
    adopted: the rest stood on the venue with no row, the book frozen
    `placement_lost`. The 050 column's intent on our row IS the wire
    intent the venue echoes (BUY_LONG / SELL_LONG / BUY_SHORT /
    SELL_SHORT), so when the row and the venue row both carry one the
    match is intent to intent -- the cover's SELL_SHORT, and a short
    ADD's BUY_SHORT (whose derived side 'BUY' never matched its row
    side SELL either). A row without an intent (pre-050) or a venue
    row reporting none falls back to the side comparison as before."""
    mine, theirs = o.get("intent"), venue.get("intent")
    if mine and theirs:
        if str(theirs) != str(mine):
            return False
    elif str(venue.get("side") or "").upper() != _order_side_of(o):
        return False
    if not venue.get("order_id"):
        return False
    try:
        px = float(venue.get("price") or 0.0)
        q = float(venue.get("quantity") or 0.0)
    except (TypeError, ValueError):
        return False
    return abs(q - float(int(qty))) <= 1e-6 and abs(px - float(wire)) <= 1e-6


async def _ledger_ids(t: _Tick, slug: str) -> set | None:
    try:
        rows = await t.pool.fetch(_SQL_LEDGER_IDS, slug)
    except Exception:  # noqa: BLE001
        return None
    return {str(r["order_id"]) for r in rows if r["order_id"]}


async def _find_lost_placement(t: _Tick, o: dict, book: dict, orders: list,
                               window: tuple[float, float]) -> tuple[str, Any]:
    """The lost-response search of step O: the book's open orders by
    fingerprint with pre_ids, the protected set and every ledger id on
    the slug excluded (spec R: the mirror's search can adopt neither a
    copy id nor a pre-placement id). ('found', order) | ('ambiguous',
    n) | ('none', None) | ('unreadable', why)."""
    protected = await _read_protected(t)
    if protected is None:
        return "unreadable", "protected_ids_unreadable"
    ledger = await _ledger_ids(t, o["us_market_slug"])
    if ledger is None:
        return "unreadable", "ledger_ids_unreadable"
    pre = {str(x) for x in (_jsonish(o.get("pre_ids")) or [])}
    exclude = pre | protected | ledger
    lo, hi = window
    cands = []
    for v in orders:
        if str(v.get("us_market_slug") or "").lower() != str(o["us_market_slug"]).lower():
            continue
        if str(v.get("order_id")) in exclude:
            continue
        if not _on_book_matches(o, v, float(o["wire"]), int(o["qty"])):
            continue
        ts = le._order_created_ts(v.get("created_at"))
        if ts is None or not (lo <= ts <= hi):
            continue
        cands.append(v)
    if len(cands) == 1:
        return "found", cands[0]
    if len(cands) > 1:
        return "ambiguous", len(cands)
    return "none", None


async def _trade_log_fills(t: _Tick, o: dict, since: float, window: tuple[float, float],
                           by_order: bool = True) -> list | None:
    """Fills of a lost placement from the venue's trade log, BY ORDER
    and exact size only (le._lost_fill_is_ours's rule): the order the
    venue names must carry our quantity, our wire and our side, sit in
    the window, and be unknown to every ledger row. None when the log
    could not be read (raises, truncated) -- the row is left. With
    `by_order` False (a sole-holder CLOSE, which has no quantity and no
    wire of its own) the size match is skipped and the caller holds
    the fills to the venue's POSITION delta instead."""
    try:
        fills = await _venue_read(t, t.pmus.recent_trades, o["us_market_slug"], since)
    except Exception as exc:  # noqa: BLE001 — unreadable is not "no fills"
        log.warning("mirror_live: trade log for %s unreadable (%s)", o["us_market_slug"],
                    type(exc).__name__)
        return None
    protected = await _read_protected(t)
    ledger = await _ledger_ids(t, o["us_market_slug"])
    if protected is None or ledger is None:
        return None
    known = protected | ledger
    lo, hi = window
    ours = []
    for f in fills or []:
        try:
            ts = float(f.get("ts") or 0.0)
            if by_order:
                oq, op = f.get("order_qty"), f.get("order_price")
                if oq is None or op is None:
                    continue
                if (abs(float(oq) - float(int(o["qty"]))) > 1e-6
                        or abs(float(op) - float(o["wire"])) > 1e-6):
                    continue
        except (TypeError, ValueError):
            continue
        if not (lo <= ts <= hi):
            continue
        side = str(f.get("side") or "").upper()
        if _order_side_of(o) not in side:
            continue
        oid = f.get("order_id")
        if not oid or str(oid) in known:
            continue
        ours.append(f)
    return ours


async def _reconcile_placing(t: _Tick, o: dict, book: dict) -> None:
    """A 'placing' row with no order id: the process died between
    orders.create and the persist. Adopt by fingerprint, else book from
    the trade log by ORDER, else freeze 'placement_lost'; past
    _LOST_FILL_WINDOW_S the order is 'lost' and the book thaws only
    when venue == ledger (step P). A short cover's row (side BUY, intent
    SELL_SHORT, tif GTC / IOC) is an ordinary row on this road: the
    fingerprint matches it by its wire intent (_on_book_matches, S4
    review F4); only the legacy CLOSE rows go to _reconcile_lost_close."""
    t.nonterminal.add(book["id"])
    age = t.now - float(o.get("placed_ts") or t.now)
    if age < PLACING_ORPHAN_S:
        _mirror_stop("open_order_pending", o["whale"])
        return
    if o.get("tif") == "CLOSE":
        await _reconcile_lost_close(t, o, book)
        return
    orders = await _read_open(t)
    if orders is None:
        return                       # named already; the row is left
    placed = float(o.get("placed_ts") or t.now)
    window = (placed - le._ORPHAN_SKEW_S, placed + le._ORPHAN_MATCH_S)
    verdict, what = await _find_lost_placement(t, o, book, orders, window)
    if verdict == "unreadable":
        log.warning("mirror_live: lost placement on row %s not searched (%s); left for the "
                    "next tick", o["id"], what)
        return
    if verdict == "found":
        oid = str(what.get("order_id"))
        await t.pool.execute(_SQL_ORDER_ADOPT, o["id"], oid, _adopt_reason(o, "adopted by fingerprint"))
        o["order_id"], o["state"] = oid, "open"
        o["reason"] = _adopt_reason(o, "adopted by fingerprint")
        _recent(book["id"], "adopted", order=oid)
        log.warning("mirror_live: order row %s adopted venue order %s by fingerprint", o["id"], oid)
        await _reconcile_open(t, o, book)
        return
    if verdict == "ambiguous":
        await _freeze(t, book, "lost_ambiguous", {"candidates": what})
        if age >= le._LOST_FILL_WINDOW_S:
            await _mark_lost(t, o, book)
        return
    fills = await _trade_log_fills(t, o, placed - 30.0, (placed - le._ORPHAN_SKEW_S,
                                                          placed + le._LOST_FILL_WINDOW_S))
    if fills is None:
        return
    if fills:
        oid = str(fills[0].get("order_id"))
        await t.pool.execute(_SQL_ORDER_ADOPT, o["id"], oid, _adopt_reason(o, "adopted from the trade log"))
        o["order_id"], o["state"] = oid, "open"
        o["reason"] = _adopt_reason(o, "adopted from the trade log")
        total = 0.0
        notional = 0.0
        for f in fills:
            if str(f.get("order_id")) != oid:
                continue
            q, px = _num(f.get("qty")), _num(f.get("price"))
            if q and px:
                total += q
                notional += q * px
        px = round(notional / total, 6) if total > 0 else float(o["wire"])
        st = {"state": "filled" if total >= float(o["qty"]) - FLAT_TOL_SHARES else "cancelled",
              "filled_shares": total, "avg_px": px}
        await _book_delta(t, o, book, st, maker=True)
        await _finish_order(t, o, book, st, "booked from the trade log")
        return
    await _freeze(t, book, "placement_lost")
    if age >= le._LOST_FILL_WINDOW_S:
        await _mark_lost(t, o, book)


async def _reconcile_lost_close(t: _Tick, o: dict, book: dict) -> None:
    """A sole-holder CLOSE row (tif 'CLOSE') whose response was lost.
    close_position carries no wire and no quantity of its own, so the
    fingerprint search and the trade log's by-ORDER match can find
    nothing for it (step-9 review: the search read float(None) and the
    row sat 'placing' for good). The venue's POSITION is the evidence
    instead: the book was the slug's sole holder when the close was
    sent, so the shares that left the account since are what the close
    sold, and the venue's own trade log names their price -- one order
    id unknown to every ledger row, its fills held to the position
    delta. Nothing sold: the row is 'lost' by name past the window and
    the book thaws when venue == ledger, so the flatten runs again. An
    unreadable position or log leaves the row for the next tick; a
    delta the log cannot account for, or more than one seller on a
    sole-held slug, stays frozen by name for a human, never booked at
    a guessed price.

    The position is THIS TICK'S paced walk (step R, t.positions), the
    one reading of the account the tick has: a le._pm_held here was a
    second whole-account walk, up to fifty pages, outside venue_pace,
    on every tick the lost row stood (step-9 re-review). A tick with
    no walk -- SAFE and every cancel-only tick reconcile orders before
    step R -- refuses by name and leaves the row: nothing is booked off
    a position nobody read. The slippage flatten keeps its own
    le._pm_held, as the spec writes it (section 2 F)."""
    t.nonterminal.add(book["id"])
    placed = float(o.get("placed_ts") or t.now)
    age = t.now - placed
    slug = o["us_market_slug"]
    if t.positions is None:
        _mirror_stop("positions_unreadable", o["whale"])
        log.warning("mirror_live: no positions walk this tick; lost close on row %s left for "
                    "the next tick", o["id"])
        return
    held = float(t.positions.get(slug.lower(), 0.0))
    if _book_short(book):
        held = abs(held)             # a short book's slug reads negative: the leg is its magnitude
    qty = int(o["qty"])
    sold = qty - int(held)
    if sold < 1:
        await _freeze(t, book, "placement_lost", {"close": "nothing_sold", "held": int(held)})
        if age >= le._LOST_FILL_WINDOW_S:
            await _mark_lost(t, o, book)
        return
    fills = await _trade_log_fills(t, o, placed - 30.0,
                                   (placed - le._ORPHAN_SKEW_S, placed + le._LOST_FILL_WINDOW_S),
                                   by_order=False)
    if fills is None:
        return
    by_id: dict[str, list] = {}
    for f in fills:
        by_id.setdefault(str(f.get("order_id")), []).append(f)
    total = notional = 0.0
    if len(by_id) == 1:
        for f in next(iter(by_id.values())):
            q, px = _num(f.get("qty")), _num(f.get("price"))
            if q and px and 0.0 < px < 1.0:
                total += q
                notional += q * px
    if len(by_id) != 1 or total < 1.0:
        detail = {"close": "unattributed", "sold": sold, "sellers": len(by_id)}
        await _freeze(t, book, "placement_lost", detail)
        if age >= le._LOST_FILL_WINDOW_S:
            # E5 / P3 (task 46's third clause): past the window the log
            # will not name the seller, and the row stood 'placing' for
            # good -- read, searched and re-frozen `close: unattributed`
            # every tick for the same seven books (16:07Z heartbeat).
            # Marked 'lost' ONCE, as the nothing_sold branch marks its
            # row, with the venue's own reading on the receipt (held,
            # sold, the sellers the log named); the book stays frozen
            # by name for a human, and the freeze stops re-emitting
            await _mark_lost(t, o, book, receipt={**detail, "held": int(held), "qty": qty,
                                                  "venue": t.positions.get(slug.lower()),
                                                  "at": t.now})
            # the mark is the one event the recent list carries for this
            # close (V3-2: _freeze itself emits on a transition only)
            _recent(book["id"], "frozen", reason="placement_lost", **detail)
        return
    oid = next(iter(by_id))
    booked = min(total, float(sold))
    px = round(notional / total, 6)
    await t.pool.execute(_SQL_ORDER_ADOPT, o["id"], oid, "adopted from the trade log by position")
    o["order_id"], o["state"] = oid, "open"
    _recent(book["id"], "adopted", order=oid, sold=sold)
    st = {"state": "filled" if booked >= qty - FLAT_TOL_SHARES else "cancelled",
          "filled_shares": booked, "avg_px": px}
    await _book_delta(t, o, book, st, maker=False, taker_at_placement=True)
    await _finish_order(t, o, book, st, "booked from the position and the trade log")


async def _mark_lost(t: _Tick, o: dict, book: dict, receipt: dict | None = None) -> None:
    await t.pool.execute(_SQL_ORDER_STATE, o["id"], "lost", None, _adopt_reason(o, "order_lost"),
                         None, None)
    await t.pool.execute(_SQL_BOOK_OPEN_ORDER, book["id"], None)
    o["state"], o["reason"] = "lost", _adopt_reason(o, "order_lost")
    if receipt:
        # the venue's own reading the row was lost on (E5 / P3), beside
        # whatever the placement's response left; best-effort, the
        # state above is what stops the re-read
        try:
            await t.pool.execute(_SQL_ORDER_RECEIPT, o["id"], json.dumps(receipt, default=str))
        except Exception:  # noqa: BLE001 — the receipt is for a human
            log.warning("mirror_live: could not write the lost receipt on order %s", o["id"],
                        exc_info=True)
    t.nonterminal.discard(book["id"])
    # A LOST ORDER FINISHES THE SAME WAY A FILLED OR CANCELLED ONE DOES:
    # the take arm goes with it. The lost path once kept the arm a
    # post-only 400 had set before the placement, so the book thawed
    # (venue == ledger) with no rest standing and an hour-old arm, and
    # the next tick fired one IOC the moment the ask touched the wire
    # -- IOC-first, past the rest-first wait (step-9 re-review; critic
    # C15; owner order 2026-09-02, "go for it, let's get this working")
    await _disarm_take(t, book)
    _mirror_stop("order_lost", o["whale"])
    _recent(book["id"], "order_lost", order_row=o["id"])


async def _order_status(t: _Tick, oid: str) -> dict | None:
    try:
        return await _venue_read(t, t.pmus.order_status, oid)
    except Exception as exc:  # noqa: BLE001 — unknown is a state, not a None
        log.warning("mirror_live: order %s unreadable (%s)", oid, type(exc).__name__)
        return None


# The venue's ladder. A BUY rest is FLOORED to it (rules.buy_wire), so
# its own cent is the most it can ever pay in a book that behaved; half
# a tick of tolerance covers the half-cent grid 42.9% of mapped markets
# quote on, and 1e-4 covers float noise on a 4-place average. A fill a
# whole cent above the wire is over that line and is the thing this
# breaker exists to catch.
_OVERSPEND_TICK = 0.01


def _overspend_of(o: dict, st: dict, intent: str | None = None) -> bool | None:
    """Did this BUY fill above the cent we wired? True / False / None
    (the comparison could not be made).

    `intent` is the BOOK's (P2 rung S0, brief D4): on a short book the
    rows that grow the leg carry the plan side SELL_LONG, their wire is
    the CONTRACT price the venue was sent and the venue's avg_px is the
    contract price it filled at, so the comparison is made in COST
    space -- what we paid a share, 1 - avg_px, against what the wire
    authorised, 1 - wire, with the same half-tick tolerance. A None
    intent is the long book it always was, byte for byte.

    THE MIRROR HAD NO COUNTERPART TO THE PER-FILL LANE'S OVERSPEND
    BREAKER. `rules.book_buy` accepts any finite price in (0,1) and is
    never handed the order's wire, so a rest that filled above its own
    cent inflated `avg_cost`, `gross_buy_usd` and the day's spend with
    nothing anywhere to detect it -- and §4's `at_or_better = 1.00`
    invariant had no instrument at all. `_book_delta` is the single
    booking entry point for all three fill paths and it holds the wire.

    A CLOSE ROW IS EXEMPT BY NAME. `close_position` has no cent of its
    own and its wire is deliberately 0.0, not None, so `avg_px > wire`
    is true for every vanish flatten; comparing it would trip the lane
    off on the one order that is working correctly.

    Only a BUY. A SELL filling above its wire is a better sale, and
    refusing it would freeze books for making money.

    THE NUMBER IT READS IS THE ORDER'S CUMULATIVE AVERAGE, and that is a
    real limit on it, stated rather than hidden. `avg_px` is the venue's
    average over the WHOLE order (`pmus._norm_order` maps `avgPx`), while
    `inc` is one tranche: 300 shares at a 0.30 wire followed by 1 share
    at 0.99 averages 0.3023 and does not trip. The venue gives no
    per-tranche price and reconstructing one from the booked cash would
    invent a number, so the check is what the venue reports. TWO
    CONSEQUENCES FOR WHOEVER COMPUTES §3b M10's `at_or_better = 1.00
    EXACT`: it must use THIS predicate (the half-tick tolerance
    included, or the gate fails on the half-cent grid 42.9% of mapped
    markets quote on while the breaker is correctly silent), and it must
    print `overspend_uncheckable` beside it as the denominator, or a
    venue that omits `avgPx` reads as a perfect score."""
    action = rules.leg_action(intent, o.get("side"))
    if action is None:
        action = "add" if str(o.get("side") or "") == BUY else "reduce"
    if action != "add" or str(o.get("tif") or "") == "CLOSE":
        return False
    avg, wire = _num(st.get("avg_px")), _num(o.get("wire"))
    if avg is None or not (0.0 < avg < 1.0) or wire is None or not (0.0 < wire < 1.0):
        return None
    if rules.is_short(intent):
        return (1.0 - avg) > (1.0 - wire) + _OVERSPEND_TICK / 2.0 + 1e-4
    return avg > wire + _OVERSPEND_TICK / 2.0 + 1e-4


async def _book_delta(t: _Tick, o: dict, book: dict, st: dict, maker: bool,
                      taker_at_placement: bool = False) -> str | None:
    filled = _num(st.get("filled_shares"))
    if filled is None:
        return None
    inc = filled - float(o.get("booked_filled") or 0.0)
    if inc < FLAT_TOL_SHARES:
        return None
    px = _num(st.get("avg_px"))
    if px is None or not (0.0 < px < 1.0):
        px = _num(o.get("wire"))     # a rest fills at its own cent; the venue named no better
    if px is None or not (0.0 < px < 1.0):
        # a CLOSE row has no cent of its own (its wire is 0.0): a fill
        # the venue did not price is refused by name and left unbooked
        # for the next read, never booked at a guess and never a
        # TypeError (step-9 review)
        _mirror_stop("no_price", o["whale"])
        log.error("mirror_live: fill on order %s carries no price and the row has no cent; "
                  "nothing booked", o["id"])
        await _freeze(t, book, "no_price")
        return "no_price"
    over = _overspend_of(o, st, book.get("intent"))
    if _book_short(book) and _order_action(o, book) == "add":
        # the short leg's at_or_better producer (P2 rung S0, brief G4):
        # the same predicate, counted with its denominator
        short = t.stats.setdefault("short", {})
        short["fills"] = int(short.get("fills") or 0) + 1
        key = ("uncheckable" if over is None
               else "at_or_better" if not over else None)
        if key:
            short[key] = int(short.get(key) or 0) + 1
    if over is None:
        # THE COMPARISON COULD NOT BE MADE, so it is counted and said
        # out loud -- never a trip on an absent number, and never
        # hidden. The fill still books at today's fallback.
        _mirror_stop("overspend_uncheckable", o["whale"])
    elif over:
        # BEFORE the booking, so a booking that fails cannot lose the
        # trip, and before `_place`'s post-only latch runs: a post-only
        # order the venue crossed anyway is precisely the fill most
        # likely to be above the wire. The shares are still booked
        # below -- they are ours whatever we paid, and a ledger that
        # does not hold them is a venue-vs-ledger freeze on top.
        detail = {"book": book["id"], "order": o["id"], "avg_px": _num(st.get("avg_px")),
                  "wire": _num(o.get("wire")), "shares": round(inc, 4)}
        await _trip_live_off(t, "mirror_overspend", detail)
        await _freeze(t, book, "mirror_overspend", detail)
    try:
        out = await _book_fill(t, o, book, inc, px, maker, taker_at_placement)
    except Exception as exc:  # noqa: BLE001 — a write failure is named; the cursor did not move
        # A LEDGER THAT CANNOT BE WRITTEN IS A BOOK THAT CANNOT BE
        # REASONED ABOUT: frozen under the failure's name (nothing new
        # on it, its rest cancelled); the order is never finalized while
        # a fill stands unbooked (_finish_order), so the next tick books
        # it exactly once off the cursor
        _mirror_stop("write_failed", o["whale"])
        log.error("mirror_live: booking on order %s failed (%s); re-booked next tick",
                  o["id"], type(exc).__name__, exc_info=True)
        await _freeze(t, book, "write_failed")
        return "write_failed"
    if out == "rebooked":
        _recent(book["id"], "rebooked", order_row=o["id"])
    return out


def _terminal_state(o: dict, st: dict) -> str:
    filled = float(_num(st.get("filled_shares")) or o.get("booked_filled") or 0.0)
    if filled >= float(o["qty"]) - FLAT_TOL_SHARES:
        return "filled"
    vs = str(st.get("state") or "").lower()
    if vs == "expired":
        return "expired"
    if vs == "rejected":
        return "rejected"
    return "cancelled"


async def _finish_order(t: _Tick, o: dict, book: dict, st: dict, reason: str | None) -> str:
    venue_filled = _num(st.get("filled_shares"))
    if venue_filled is not None and venue_filled > float(o.get("booked_filled") or 0.0) + FLAT_TOL_SHARES:
        # the venue filled more than the ledger booked (the booking
        # failed): a terminal state would drop those shares for good,
        # so the row stays 'unknown' and is re-read until they book
        await t.pool.execute(_SQL_ORDER_STATE, o["id"], "unknown", str(st.get("state") or ""),
                             "unbooked_fill", None, o.get("order_id"))
        o["state"] = "unknown"
        t.open_by_book.pop(book["id"], None)
        t.nonterminal.add(book["id"])
        await _freeze(t, book, "write_failed")
        return "unknown"
    state = _terminal_state(o, st)
    maker = (o.get("tif") in ("GTC", "GTD") and not o.get("taker_at_placement")
             and o.get("kind") != "take")
    reason = _adopt_reason(o, str(reason)) if reason is not None else None
    await t.pool.execute(_SQL_ORDER_STATE, o["id"], state, str(st.get("state") or ""),
                         reason, bool(maker), o.get("order_id"))
    o["reason"] = reason
    await t.pool.execute(_SQL_BOOK_OPEN_ORDER, book["id"], None)
    o["state"] = state
    if (state in ("cancelled", "expired") and _order_action(o, book) == "add"
            and o.get("tif") in ("GTC", "GTD")
            and t.now - 86400.0 < float(o.get("placed_ts") or t.now) < t.now
            and t.mirror_day is not None):
        # THE GIVE-BACK. The tick's day read (_SQL_MIRROR_DAY) counted
        # this rest's unfilled remainder as standing, which it was at
        # the tick's start, and only _place adjusts the reading; so a
        # replace, TTL or take cancel left the remainder inside
        # t.mirror_day and the re-quote that followed in the SAME tick
        # was under-sized by it whenever the day was within one rest
        # of the cap -- then replaced up to size on the next tick, one
        # extra replace per re-quote at high utilisation (review of
        # the first cut, 2026-09-05). The window is the read's own
        # (placed_at within 24 h): a rest placed THIS tick was never in
        # the read (_place's decrement took it), and one older than the
        # window fell out of the read already; giving either back would
        # widen the day, so both stay on the safe side and give nothing.
        t.mirror_day += (max(0.0, float(o["qty"]) - float(o.get("booked_filled") or 0.0))
                         * _cost_px(float(_num(o.get("wire")) or 0.0), book))
    book["open_order_id"] = None
    t.open_by_book.pop(book["id"], None)
    t.nonterminal.discard(book["id"])
    await _disarm_take(t, book)
    filled = float(o.get("booked_filled") or 0.0)
    w = o["whale"]
    if state == "filled":
        if maker:
            _mirror_stop("filled_rest", w)
            t.stats["filled_rest"] += 1
        else:
            _mirror_stop("filled_take", w)
            t.stats["filled_take"] += 1
        if o.get("kind") in ("flatten_paired", "flatten_vanished"):
            t.stats["flattened"] += 1
        if o.get("intent") == "ORDER_INTENT_SELL_SHORT" and _leg_of(book) < 1:
            # S4: a short book's leg covered to flat by its cover order
            # -- the take at the ceiling cent, the rest at floor(his),
            # the unpriced vanish IOC (the name close_position's fill
            # used to carry)
            _mirror_stop("short_flatten_close", w)
    elif state == "expired":
        _mirror_stop("expired", w)
    elif filled <= FLAT_TOL_SHARES and state == "cancelled":
        _mirror_stop("cancelled_unfilled", w)
        if o.get("kind") in ("reduce", "flatten_paired"):
            _mirror_stop("reduce_unfilled", w)
    if state in ("cancelled", "expired"):
        t.stats["cancelled"] += 1
    _recent(book["id"], "order_" + state, order_row=o["id"], filled=filled)
    return state


async def _reconcile_open(t: _Tick, o: dict, book: dict, cancel_reason: str | None = None) -> str:
    """An order with an id: read it, book the delta, write a terminal
    state, or cancel it when the tick or the book says so."""
    oid = str(o["order_id"])
    st = await _order_status(t, oid)
    if not st:
        await t.pool.execute(_SQL_ORDER_STATE, o["id"], "unknown", None, "order_state_unknown",
                             None, oid)
        o["state"] = "unknown"
        t.nonterminal.add(book["id"])
        await _freeze(t, book, "order_state_unknown")
        return "unknown"
    await _book_delta(t, o, book, st, maker=(o.get("kind") != "take"
                                             and not o.get("taker_at_placement")))
    if le._rest_terminal(st):
        return await _finish_order(t, o, book, st, o.get("reason"))
    if cancel_reason is None:
        if t.cancel_all:
            cancel_reason = t.cancel_all
        elif _order_action(o, book) == "add" and _increases_refusal(t, o["whale"]):
            cancel_reason = _increases_refusal(t, o["whale"])
        elif book.get("state") in ("closed", "closing"):
            # a CLOSED or CLOSING book's order is a rest nobody plans
            # for: its fill would land on a retired row, or on a market
            # that has ended (step-9 review). Named by the book's STATE
            # whatever the book was before: the settle, the episode
            # close and step M's 'closing' write never clear
            # frozen_reason, so a book frozen venue_ledger_disagree and
            # then closed -- or closing, when the cancel step M sent was
            # ops-capped and this step's cancel is the one that lands --
            # cancelled its rest under the stale freeze, and the ops
            # reader saw a live disagreement on a book that had ended
            # (step-9 re-review minor 6; its residual, task 7)
            cancel_reason = str(book["state"])
        elif book.get("state") == "frozen" and not _frozen_reduce_stands(book, o):
            # a frozen book's rest is cancelled under the freeze's name
            # -- except the frozen exit's own venue-sized reduce (E5 /
            # P2), which stands for _act to keep, replace or take like
            # a live exit's rest (the priced-exit and TTL clauses below
            # read it as they read any reduce rest)
            cancel_reason = book.get("frozen_reason") or "frozen"
        elif (t.now - float(o.get("placed_ts") or t.now) >= float(rules.MIRROR_REST_TTL_S)
              and not _priced_exit_rest(o, book)):
            # A PRICED EXIT REST PAST ITS TTL IS THE PLAN'S TO DECIDE (E4
            # rule 3, "never chase past the cent"): book 29 was cancelled
            # and re-quoted 14 times by this clause at the SAME cent while
            # the market sat far under him. A rest at HIS cent
            # (_priced_exit_rest) is left standing here and _act reads
            # it against this tick's plan: at his cent still, it stands
            # (`requote_same_wire`); at another cent or quantity it is
            # replaced there -- exempt from the replace budget. An
            # UNPRICED reduce rest (he gave no exit price: the rest sits
            # at the ask, not at a cent of his) keeps this clause's `ttl`
            # re-quote -- the reason `ttl`, never `replace`, so
            # _SQL_REPLACES does not count it against the book's entry
            # budget -- and is TTL'd through an abandoned tick as before
            # (E4 review round 3, D-1/D-2). An entry's TTL re-quote is
            # unchanged
            cancel_reason = "ttl"
    if cancel_reason:
        # the TTL re-quote of a reduce rest is the first half of an
        # exit's re-quote (its re-rest rides the credit): never
        # `ops_capped` (M-1); every other cancel here is bounded as before
        return await _cancel_and_settle(t, o, book, cancel_reason,
                                        exit=(cancel_reason == "ttl"
                                              and _order_action(o, book) == "reduce"))
    t.open_by_book[book["id"]] = (o, st)
    t.nonterminal.add(book["id"])
    if o["state"] != "open":
        await t.pool.execute(_SQL_ORDER_STATE, o["id"], "open", str(st.get("state") or ""),
                             o.get("reason"), None, oid)
        o["state"] = "open"
    return "open"


async def _cancel_and_settle(t: _Tick, o: dict, book: dict, reason: str,
                             exit: bool = False) -> str:
    """Step C: cancel twice, read until terminal (bounded), book the
    delta, write the terminal state. Non-terminal after the reads is
    'unknown', the book frozen 'cancel_pending', nothing new on it.
    `exit` is a cancel on an exit's path (the take's, the replace's, a
    flatten's, the TTL re-quote of a reduce rest): never `ops_capped`
    (_op_slot, review round 3 M-1)."""
    slot = _op_slot(t, o["whale"], exit=exit)
    if slot is None:
        # the order still RESTS: it stays in open_by_book so no caller
        # reads "nothing open" off a cancel that never went out (the
        # settled path closed a book over its resting order; step-9
        # review); the next tick's budget cancels it
        return "ops_capped"
    slot.commit()
    oid = str(o["order_id"])
    slug = o["us_market_slug"]
    cancel_ok = False
    for _attempt in range(CANCEL_ATTEMPTS):
        _venue_call(t, guard=True)
        try:
            # a venue WRITE, behind the pacer (E2 review, HIGH-1)
            c = await asyncio.to_thread(_paced, t.pmus.cancel_order, oid, slug)
            cancel_ok = bool((c or {}).get("ok"))
            cancel_err = None if cancel_ok else (c or {}).get("error")
        except Exception as exc:  # noqa: BLE001
            cancel_ok, cancel_err = False, exc
        if cancel_ok:
            break
        if ms.is_rate_limit(cancel_err):
            # a cancel the venue rate-limited (E2 review round 2): named,
            # the circuit tripped; the reads below still decide the
            # order's state, as for any failed cancel
            _rate_limited(t, o.get("whale"), f"cancel {oid}")
    st = None
    for _i in range(CANCEL_READS):
        st = await _order_status(t, oid)
        if le._rest_terminal(st):
            break
        await _sleep(CANCEL_READ_GAP_S)
    if st:
        await _book_delta(t, o, book, st, maker=(o.get("kind") != "take"
                                                 and not o.get("taker_at_placement")))
    if not le._rest_terminal(st):
        await t.pool.execute(_SQL_ORDER_STATE, o["id"], "unknown", str((st or {}).get("state") or ""),
                             _adopt_reason(o, reason), None, oid)
        o["state"], o["reason"] = "unknown", _adopt_reason(o, reason)
        t.open_by_book.pop(book["id"], None)
        t.nonterminal.add(book["id"])
        await _freeze(t, book, "cancel_pending", {"cancel_ok": cancel_ok})
        return "unknown"
    _recent(book["id"], "cancel", reason=reason, cancel_ok=cancel_ok)
    if reason in ("ttl", "replace"):
        t.stats["requotes"] += 1
        # the cancel is the first half of a re-quote: the rest that
        # follows on this book this tick rides the same op (LOW-6)
        t.requote_credit.add(book["id"])
    return await _finish_order(t, o, book, st, reason)


async def _reconcile_orders(t: _Tick, count: bool = True) -> None:
    """Step O. Every non-terminal mirror order, oldest first, under
    its book's lock, BEFORE any book is planned. Run a second time by
    _tick after a mid-tick trip (`count` False keeps the first pass's
    figure): every order the first pass KEPT is cancelled under the
    trip's name, since t.cancel_all is now set."""
    rows = [dict(r) for r in await t.pool.fetch(_SQL_ORDERS_OPEN if t.short_col
                                                else _SQL_ORDERS_OPEN_047)]
    if count:
        t.stats["orders_open"] = len(rows)
    for o in rows:
        try:
            book = await t.pool.fetchrow(_SQL_BOOK_READ, o["book_id"])
        except Exception as exc:  # noqa: BLE001
            log.warning("mirror_live: book %s unreadable (%s)", o["book_id"], type(exc).__name__)
            continue
        if not book:
            continue
        book = dict(book)
        async with _lock_for(book["id"]):
            try:
                if not o.get("order_id"):
                    await _reconcile_placing(t, o, book)
                else:
                    await _reconcile_open(t, o, book)
            except Exception as exc:  # noqa: BLE001 — one order, not the tick
                _mirror_stop("book_error", o.get("whale"))
                log.exception("mirror_live: order %s failed (%s)", o["id"], type(exc).__name__)
            if o.get("state") in ("placing", "open", "unknown"):
                t.nonterminal.add(book["id"])


# ------------------------------------------------------ step B: the books

@dataclass
class _Reading:
    """Everything step B read for one (whale, market) before deciding."""
    whale: str
    cid: str
    slug: str
    la: str
    oa: str | None
    fills: list
    his_long: float
    his_other: float
    snap: dict
    snap_age: float | None
    snap_partial: bool
    fresh_read: bool          # a snapshot young enough, complete or not
    fresh: bool               # young AND complete: the position source
    snap_long: float | None
    snap_other: float | None
    bid: float | None
    ask: float | None
    mark: float | None
    venue: float
    manual: float
    market: dict | None
    market_live: bool | None      # None: the markets row could not be read (never "closed")
    # THE PER-MARKET READ (Phase 1). Appended last so a positional
    # construction keeps its meaning. `snap_market_fresh` is True ONLY
    # when the venue answered FOR THIS CONDITION -- naming at least one
    # of its two tokens, the other leg then reading 0.0 per the callee's
    # contract -- inside the freshness window; it is None otherwise --
    # never False -- because admission and every consumer test
    # `is not True`, so a fact that was not read refuses rather than
    # admits.
    snap_market_fresh: bool | None = None
    mkt_long: float | None = None
    mkt_other: float | None = None
    mkt_net: float | None = None
    # THE VENUE'S OWN STATE FOR THIS SLUG, as this tick's quote read
    # carried it (t.slug_states): None when the read failed, the
    # payload carried no state (the SDK's typed shape), or the quote
    # was not read this tick. The flatten's slippage leg refuses on a
    # state that is present and not OPEN (2026-09-06 review of U9).
    venue_state: str | None = None
    # E5 / P1: the operator register's signed sum for the slug
    # (_SQL_REGISTERED_SHARES), 0.0 when absent or unreadable -- read
    # beside `manual` and, on a book, counted only when its sign is the
    # book's leg's (_tick_book)
    registered: float = 0.0


async def _read_market(t: _Tick, whale: str, cid: str, slug: str, la: str, oa: str | None,
                       fills: list, read_quote: bool = True,
                       market: dict | None = None, book: bool = False) -> _Reading:
    """`market` is the caller's own step-M reading when it has one
    (_tick_book reads the row once, BEFORE the plan); read here only
    for a candidate. A second read that failed would otherwise turn a
    live market into a "closed" one for the close rule (step-9 review:
    one unreadable read is the named refusal, never a fact). `book`
    says the read is an EXISTING book's, for the miss streak's rule
    (_bbo): a non-OPEN state on one is the book's own to handle, never
    venue-outage evidence."""
    pos = mi.net_positions(fills)
    his_long = float(pos.get(la, 0.0)) if la else 0.0
    his_other = float(pos.get(oa, 0.0)) if oa else 0.0
    snap, age, partial = await _snapshot(t, whale)
    fresh_read = age is not None and age <= ms.SNAP_MAX_AGE_S
    fresh = fresh_read and not partial

    def _snap_of(asset):
        if not asset or not fresh_read:
            return None
        if asset in snap:
            return float(snap[asset])
        return None if partial else 0.0

    bid = ask = None
    if read_quote:
        bid, ask = await _bbo(t, slug, book=book)
    venue = float((t.positions or {}).get(slug.lower(), 0.0)) if t.positions is not None else 0.0
    try:
        manual = float(await t.pool.fetchval(_SQL_MANUAL_SHARES, slug) or 0.0)
    except Exception:  # noqa: BLE001 — the desk's shares unreadable: explained nothing
        manual = 0.0
    registered = await _registered_shares(t, whale, slug)
    mk = market if market is not None else await _market(t, cid)
    market_live = None if mk is None else bool(mk["closed"] is False and mk["resolved"] is False)
    # THE MARKETS ROW FIRST, THEN THE VENUE READ. A candidate on a
    # closed or resolved market is refused by `market_closed` whatever
    # the per-market read says, so spending a data-API read and a budget
    # slot on it before the cheap refusal buys nothing (a book on such a
    # market never reaches here: `_tick_book` takes its closing branch
    # above). `market_live is None` is an UNREADABLE row, not a closed
    # one, and it still reads: the book must keep managing down.
    if market_live is False:
        mkf, ml_long, ml_other, mnet = None, None, None, None
    else:
        mkf, ml_long, ml_other, mnet = await _market_snap(t, whale, cid, la, oa, book=book)
    return _Reading(whale, cid, slug, la, oa, fills, his_long, his_other, snap, age,
                    bool(partial), fresh_read, fresh, _snap_of(la), _snap_of(oa), bid, ask,
                    _mark_of(bid, ask), venue, manual, mk, market_live,
                    mkf, ml_long, ml_other, mnet,
                    venue_state=t.slug_states.get(slug), registered=registered)


async def _registered_shares(t: _Tick, whale: str, slug: str) -> float:
    """The operator register's signed sum for the slug (E5 / P1), the
    way the desk's `manual` shares are read: 0.0 explains nothing. An
    unreadable register (absent until migration 056 is applied; the
    workers never run migrations) is counted `registered_unreadable`
    and logged once per process -- a book whose venue the register
    would have explained stays frozen, never a guess."""
    global _registered_logged
    try:
        return float(await t.pool.fetchval(_SQL_REGISTERED_SHARES, slug) or 0.0)
    except Exception as exc:  # noqa: BLE001 — unreadable: explained nothing (fail closed)
        _mirror_stop("registered_unreadable", whale)
        if not _registered_logged:
            _registered_logged = True
            log.warning("mirror_live: the operator register could not be read (%s); is migration "
                        "056 applied? registered shares explain nothing until it is",
                        type(exc).__name__)
        return 0.0


_MktSnap = tuple[bool | None, float | None, float | None, float | None]


async def _market_snap(t: _Tick, whale: str, cid: str, la: str, oa: str | None,
                       book: bool = False) -> _MktSnap:
    """ONE `whale_exits.market_positions` read of BOTH tokens of THIS
    condition, once per book and per candidate per tick, on its own
    bounded budget. Returns (snap_market_fresh, long, other, net).

    THIS IS THE READ THE MIRROR HAD NO CALLER FOR. `market_positions`
    has existed and been tested since Phase 1 was specified and nothing
    in `sportsassets/` called it, which is the single reason the mirror
    opens no book: the whole-book walk beside `_RAW_KEY` is truncated on
    every probe of RN1 (one token `n/a` every read), so `snap_fresh` is
    never True for him and admission refuses `snapshot_stale` on every
    candidate. This read answers for ONE market and replaces that walk
    as the position source for that market alone.

    WHAT "COMPLETE" MEANS HERE, AND WHY IT IS NOT "BOTH TOKENS CAME
    BACK". It was both-or-nothing, and that refused the ordinary
    one-sided directional position: a whale who has only ever held the
    long token of a condition has ONE row, so the read never read fresh
    and admission kept refusing `snapshot_stale` -- P1's whole purpose,
    unmet for the common case, and §3b M4's `fresh_complete_share >=
    0.95` unreachable unless nearly all his markets were two-legged. The
    CALLEE settles it and says the opposite about the same response:
    `market_positions` returns `complete=True` and reads an absent long
    leg as 0.0 ("exactly as `_confirm_gone` reads that absence"), and it
    can: the query is per-condition with `limit=100` over a condition
    that has exactly TWO tokens, so it cannot be truncated, and the
    callee refuses a row from any other condition, an empty list, a
    duplicate asset and a size that is not a finite number >= 0. An
    absent leg in an answer like that is a ZERO, not an unknown.

    So the test is: the answer must NAME AT LEAST ONE of this
    condition's two tokens, and then the other leg reads 0.0. Naming
    neither is not an answer about this market -- it is an unfiltered
    response the callee did not catch, and reading it would say "he is
    flat" about a market we never saw, so it refuses by name. Not
    knowing the sibling token id refuses too, and before the read:
    without it the other leg is unknown rather than zero, and no net can
    be formed.

    THE FRESHNESS HALF IS STRUCTURAL, AND THIS IS THE HONEST STATEMENT
    OF IT. `ts` is `time.time()` taken inside `market_positions` as the
    read completes -- OUR clock, not the venue's -- so `t.now - ts` is
    the negative of the time this tick has been running when the read
    landed. The window therefore bounds two things and no others: a tick
    that has been running longer than `ms.SNAP_MAX_AGE_S` before it acts
    on a market, and a clock that jumped (hence `abs`). It CANNOT catch
    venue-side staleness, because the venue supplies no stamp. What the
    fact measures in every normal tick is COMPLETENESS, and whoever
    quotes `MIRRORSNAP.fresh_complete_share` against §0's baseline of 0
    must say so: that baseline was a freshness-and-completeness reading
    of a different instrument (the whole-book snapshot). The read that
    is discarded on this clause is COUNTED (`snap_market_stale`), so it
    is never invisible.

    THE BUDGET AND THE TIMEOUT. This read has its OWN budget,
    `t.mkt_reads` against `MAX_MARKETS_PER_TICK`, and is NOT charged
    to `t.reads`. Sharing them was measured and was wrong: `t.reads` is
    what the candidate walk breaks on, one BBO read per market, so
    charging a second read per market silently halved the number of
    markets a tick considers -- and that number is the denominator of
    P1's own gate. Two read classes, two budgets of the same bounded
    size (`capped_env`, so no shell can widen either). AND THE BUDGET
    IS THE CANDIDATES' (U12, 2026-09-06, the same split as `t.reads` /
    `t.cand_reads`): an existing book's read (`book=True`) is never
    charged to it and is never refused by it, because a book whose
    whole-book walk is not fresh plans its reduces and its flatten on
    THIS read alone -- with the budget shared, the twenty-first live
    book had no reading, no plan and no managed exit, a count cap by
    another road. Books are unbounded here as they are in quote reads;
    the wall time of the tick (`tick_s`) is the operator's instrument.
    `t.mkt_reads` stays the total for the counters; `t.cand_mkt_reads`
    is what candidates break on (`snap_market_capped`). A candidate's
    per-market read follows its quote read one for one (_read_market),
    so the candidate walk's own budget (`capped_tick`) stops a
    candidate FIRST and this clause is the defence behind it -- it
    bites only if a candidate reaches here without spending a quote
    read, which nothing does today; the worker's tests drive it at
    this function's own level. Each read is
    additionally bounded in WALL TIME by `_SNAP_READ_TIMEOUT_S`: a data
    API that is merely SLOW raises nothing, and 20 unbounded awaits
    against a 25 s client timeout inside a 30 s poll is a tick that
    stretches to minutes with nothing reconciled, no TTL cancelled and
    no name anywhere. A timed-out read is a refused market
    (`snap_market_slow` beside `snap_market_unreadable`), never a
    refused tick.

    UNREADABLE CONTRACT: a None, raising or timed-out read refuses THAT
    MARKET under `snap_market_unreadable` and NEVER abandons the tick --
    no `_abandon`, no raise out of this function, no miss-streak. A
    market we cannot see is a market we do not trade this tick; every
    other book in the tick is unaffected. Anything short of True leaves
    `snap_market_fresh` None, so admission refuses `snapshot_stale`
    exactly as today and the drift fact falls back to the whole-book
    rule. READ THE NAME PRECISELY WHEN GRADING IT: what is refused is
    the READ, and an EXISTING book whose whole-book walk is fresh still
    plans on that walk (nothing new is admitted, and increases stay
    gated by the fallback drift rule). So `snap_market_unreadable` is a
    count of refused readings, not of refused markets, and §3b's
    "<= 5% of market-ticks" is a share of readings."""
    key = (str(whale or "").lower(), str(cid))
    if key in t.mkts:
        return t.mkts[key]
    out: _MktSnap = (None, None, None, None)
    t.mkts[key] = out                     # cached before the read: one read per market per tick
    # PLANNED: the honest denominator. Counted here, before every
    # refusal below, so no failure can fall out of the share §3b M4
    # gates on.
    t.stats["snap_market_planned"] = int(t.stats.get("snap_market_planned") or 0) + 1
    if not la or not oa:
        # no sibling token id: the other leg is unknown, not zero, and
        # no net can be formed. Refused BEFORE the read, so it costs no
        # budget slot and no data-API throttle.
        t.stats["snap_market_no_ids"] = int(t.stats.get("snap_market_no_ids") or 0) + 1
        _mirror_stop("snap_market_no_ids", whale)
        return out
    if t.http is None or t.abandoned:
        # an abandoning tick plans nothing: spend no read on it. Named,
        # because a silent return is a market missing from every counter
        t.stats["snap_market_skipped"] = int(t.stats.get("snap_market_skipped") or 0) + 1
        _mirror_stop("snap_market_skipped", whale)
        return out
    if not book and t.cand_mkt_reads >= MAX_MARKETS_PER_TICK:
        # a CANDIDATE past the budget: REFUSE the market, do not read
        # it. Its OWN census name -- budget pressure is not venue
        # unreadability, and §3b grades `snap_market_unreadable` at
        # <= 5% of market-ticks. A book is never refused here (U12)
        t.stats["snap_market_capped"] = int(t.stats.get("snap_market_capped") or 0) + 1
        _mirror_stop("snap_market_capped", whale)
        return out
    address = await _whale_address(t, whale)
    if not address:
        t.stats["snap_market_no_ids"] = int(t.stats.get("snap_market_no_ids") or 0) + 1
        _mirror_stop("snap_market_no_ids", whale)
        return out
    t.mkt_reads += 1
    if not book:
        t.cand_mkt_reads += 1
    t.stats["snap_market_reads"] = int(t.stats.get("snap_market_reads") or 0) + 1
    raw = None
    t0 = time.monotonic()
    split: dict = {}                      # E7: the read's wait / request seconds
    try:
        raw = await asyncio.wait_for(
            whale_exits.market_positions(t.http, str(address), str(cid), long_asset=la, timing=split),
            timeout=_SNAP_READ_TIMEOUT_S)
    except (asyncio.TimeoutError, TimeoutError):
        # SLOW IS A FAILURE MODE WITH A NAME. Without this the tick just
        # takes longer, silently, with live rests standing.
        t.stats["snap_market_slow"] = int(t.stats.get("snap_market_slow") or 0) + 1
        log.warning("mirror_live: per-market read of %s for %s timed out at %ss",
                    cid, whale, _SNAP_READ_TIMEOUT_S)
        raw = None
    except Exception as exc:  # noqa: BLE001 — a raising read is an unread market, not a tick
        log.warning("mirror_live: per-market read of %s for %s raised (%s)",
                    cid, whale, type(exc).__name__)
        raw = None
    if book:
        # E6: an existing book's per-market data-API read (the throttle's
        # wait and the request), summed per call; E7: the two parts,
        # as the callee measured them (a timed-out call recorded what
        # it reached)
        t.timing["books_data"] += time.monotonic() - t0
        t.timing["books_data_wait"] += float(split.get("wait") or 0.0)
        t.timing["books_data_req"] += float(split.get("req") or 0.0)
    by = raw.get("by_asset") if isinstance(raw, dict) else None
    ts = _num(raw.get("ts")) if isinstance(raw, dict) else None
    if not isinstance(by, dict) or ts is None or raw.get("complete") is not True:
        _mirror_stop("snap_market_unreadable", whale)
        return out
    if str(la) not in by and str(oa) not in by:
        # the answer names neither token of this condition: it is not a
        # reading of this market, and reading it would say "flat"
        _mirror_stop("snap_market_unreadable", whale)
        return out
    if abs(t.now - ts) > ms.SNAP_MAX_AGE_S:
        t.stats["snap_market_stale"] = int(t.stats.get("snap_market_stale") or 0) + 1
        _mirror_stop("snap_market_stale", whale)
        return out                        # read, but not fresh: `snapshot_stale`, not unreadable
    lo = _num(by.get(str(la), 0.0))
    ot = _num(by.get(str(oa), 0.0))
    if lo is None or ot is None or lo < 0 or ot < 0:
        _mirror_stop("snap_market_unreadable", whale)
        return out
    out = (True, lo, ot, mi.his_net(lo, ot))
    t.mkts[key] = out
    t.stats["snap_market_fresh_reads"] = int(t.stats.get("snap_market_fresh_reads") or 0) + 1
    return out


async def _whale_address(t: _Tick, whale: str) -> str | None:
    """The whale's venue address, read ONCE per whale per tick. It was
    re-read per market, which is one database round trip per market per
    tick for a value that cannot change inside a tick. An unreadable
    read is cached as None too: it is a fact about this tick."""
    key = str(whale or "").lower()
    async with t.addr_lock:             # E2: one read per whale per tick
        if key in t.addrs:
            return t.addrs[key]
        try:
            address = await t.pool.fetchval(_SQL_WHALE_ADDRESS, key)
        except Exception:  # noqa: BLE001 — no address is no read
            address = None
        t.addrs[key] = str(address) if address else None
        return t.addrs[key]


def _his_level(fills: list, long_asset: str | None, other_asset: str | None,
               reducing: bool, short: bool = False) -> float | None:
    """His level for the WIRE. The shadow's his_level picks the same
    fill -- his most recent move in the direction we follow, by
    timestamp (mirror_shadow.his_level) -- and since E4 review round 3
    (L-3) hands back the other-token equivalent ROUNDED THE SAME WAY as
    this one: round(1 - p, 6), the executor's own precision (the
    round(..., 6) every wire is read at), so the shadow's `exit_rest_px`
    is the live rest's cent. The wire must come from the exact figure
    (addendum section 10: rules.sell_price ceils the UNROUNDED max(his
    equivalent, ask); his other-token BUY at 0.47996 is 0.52004 to him,
    and a 4-place 0.52 -- the shadow's figure before round 3 -- rested
    a cent UNDER him, the one case the rule forbids; step-9 review).
    Six places keep 0.52004 (the rest 0.53) and absorb float noise
    (1 - 0.77 is 0.22999999999999998 in binary). The selection is
    restated from the shadow only for the short clause below; the
    worker tests pin the two agree on every fixture.

    `reducing` is "his net is moving DOWN in long-token terms" -- the
    caller's `target <= ledger`, signed -- so on a SHORT book (P2 rung
    S0, brief C3) an INCREASE of the short reads the same fills a long
    book's reduce reads (his SELL of the long token at p, his BUY of
    the other token at 1 - p: the shadow's short reading, pinned at
    0.54 = 1 - 0.46), and a REDUCE of the short (his net moving UP)
    reads his BUY of the long token at p and, on a short book only,
    his SELL of the other token at 1 - p -- a clause a long book never
    reads, so its levels are byte-identical."""
    best: tuple[float, float] | None = None
    for f in fills:
        a, side = str(f.get("asset") or ""), str(f.get("side") or "").upper()
        try:
            ts = float(f.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        p = ms._px(f)
        if p is None:
            continue
        lvl = None
        if not reducing:
            if long_asset and a == long_asset and side == "BUY":
                lvl = p
            elif short and other_asset and a == other_asset and side == "SELL":
                lvl = round(1.0 - p, 6)
        elif long_asset and a == long_asset and side == "SELL":
            lvl = p
        elif other_asset and a == other_asset and side == "BUY":
            lvl = round(1.0 - p, 6)
        if lvl is not None and (best is None or ts >= best[0]):
            best = (ts, lvl)
    return best[1] if best else None


def _fresh_agreed(r: _Reading) -> bool:
    """`last_fresh_agreed` for the drift rule: True ONLY when the
    per-market net and the fills-derived net agree within one share.

    IT WAS A HARD-CODED `True` AT BOTH CALL SITES while the rule's own
    contract says the WORKER must assert it and the default is False --
    the SMALLER of two disagreeing readings. One share is the tolerance
    because it is the tolerance every other holding comparison in this
    lane uses (`mi.VENUE_LEDGER_TOL_SHARES`) and because a sub-share
    difference cannot change a whole-share target. No per-market net
    (not read, not fresh, not complete) is not agreement: it is False.

    WHAT THIS VALUE CAN AND CANNOT DO, MEASURED, BECAUSE THE PROGRAMME
    OVERSTATES IT. Its ONE consumer is `drift_rule`'s `last_fresh_agreed`
    keyword on `_drift_for`'s fallback branch. That branch is entered
    exactly when `snap_market_fresh is not True`, and `_market_snap`
    returns either (None, None, None, None) or (True, lo, ot, net), so on
    that branch `r.mkt_net` is None and THIS FUNCTION IS FALSE BY
    CONSTRUCTION -- not by accident, by the shape of the two returns. It
    is still computed and still passed, because a literal there is the
    shape that let the old defect survive a review, and because a future
    reading that is fresh-but-not-per-market would make it live; it is
    recorded on every plan row (`fresh_agreed`) so it can be graded
    rather than asserted. `test_the_fallback_asserts_the_agreement_it_read`
    pins the value actually passed, so restoring a literal -- `True`,
    `(1 == 1)`, or anything else -- fails.

    AND THE PROPERTY THE PROGRAMME FEARED LOSING IS CARRIED ELSEWHERE,
    which is the honest statement of it. "Sell down to the smaller of two
    disagreeing readings" is delivered on the market branch by
    `_drift_for`'s `reduce_from="smaller"` when the net drift is over
    `MIRROR_DRIFT_MAX`, and on the book branch by `drift_rule` itself;
    `_net_for` consults `reduce_from` only when it HAS a snapshot, and on
    the fallback branch it has none (`r.fresh` is False there by the same
    predicate), so it returns the derived reading whatever this value
    says. `test_the_per_market_net_sizes_the_reduction_from_the_smaller_reading`
    drives that property end to end."""
    if r.mkt_net is None:
        return False
    a, b = _num(mi.his_net(r.his_long, r.his_other)), _num(r.mkt_net)
    if a is None or b is None:
        return False
    return abs(a - b) <= mi.VENUE_LEDGER_TOL_SHARES


def _drift_for(r: _Reading) -> tuple[rules.DriftRule, str]:
    """(the drift rule in force, the reading it was measured on).

    THE NET, NOT THE TOKEN, whenever the per-market read is fresh and
    complete. `drift_net_rule` has existed with no caller; the per-token
    `drift_rule` reads a MERGED PAIR LEG as drift 1.0 on each token --
    his fills say +5,000 Yes and +5,000 No, he merges the pair on-chain
    and the venue shows 0 and 0 -- which locks every increase out of
    that market for the life of the book against a true net of 0. Merged
    pairs are a large share of his shares, so that is not a corner: it
    is most of the mirror's refusals. On the net the same market reads
    0. A one-sided add reads the same number under both rules, so this
    loosens nothing where the per-token rule was right.

    The number and the position source are the SAME reading by
    construction: `_net_for` sizes from the per-market net exactly when
    this returns 'market'. Mixing them -- drift measured against one
    reading, the target sized off another -- is how "the smaller of two
    disagreeing readings" stops meaning anything.

    'book' is the fallback: the whole-book walk's per-token rule, with
    `last_fresh_agreed` asserted by `_fresh_agreed`, never a literal.

    THE UNUSABLE-READING ARM ANSWERS EXACTLY WHAT `drift_rule` ANSWERS.
    It read `"derived" if agreed else "smaller"`, which is a DIVERGENCE
    from the rule it stands in for: `drift_rule` returns `"smaller"`
    unconditionally for a reading that is not a size, before it ever
    looks at `last_fresh_agreed`. The arm is unreachable from this worker
    (`mi.net_positions` floors every token at 0.0, `_num` rejects NaN and
    infinities, and `_market_snap` refuses a negative leg, so
    `drift_net_rule` cannot return None on a fresh per-market read) --
    which is why the divergence was never seen and why it is corrected
    rather than guarded. Two rules for one question is how the next
    reader gets the wrong answer."""
    if r.snap_market_fresh is True:
        d = rules.drift_net_rule(r.his_long, r.his_other, r.mkt_long, r.mkt_other)
        if d is None:
            # a reading that is not a size (a negative net leg): the
            # per-market read is no reading at all -- stale, and from the
            # SMALLER, which is `drift_rule`'s own answer to the same
            # question (rules: the `d is None` return, before the
            # `last_fresh_agreed` branch)
            return rules.DriftRule(False, "smaller", "snapshot_stale", None), "market"
        if d > float(rules.MIRROR_DRIFT_MAX):
            return rules.DriftRule(False, "smaller", "drift", d), "market"
        return rules.DriftRule(True, "derived", None, d), "market"
    return rules.drift_rule(r.his_long, r.snap_long, r.fresh_read, r.snap_partial,
                            last_fresh_agreed=_fresh_agreed(r)), "book"


def _book_net(r: _Reading) -> float | None:
    """The WHOLE-BOOK walk's net for this market, or None when that walk
    was not a fresh complete reading. Recorded beside the per-market net
    on the plan row and used nowhere else: the rest of this lane refuses
    to act until two independent readings agree, and the two venue
    readings of the same market parting is exactly what MIRRORSNAP has
    to be able to see. The per-market read outranks it (narrower, this
    tick, complete for this market where the walk is truncated on every
    probe of him) and that preference is deliberate -- but silent
    preference with no record of the loser is not a reading, it is a
    choice nobody can audit."""
    if r.fresh and r.snap_long is not None and r.snap_other is not None:
        return mi.his_net(r.snap_long, r.snap_other)
    return None


def _net_for(r: _Reading, drift: rules.DriftRule,
             short: bool = False) -> tuple[float, float | None]:
    """(net used for the target, snapshot net). The POSITION is the
    exit worker's fresh complete snapshot (addendum section 1); on a
    fresh disagreement the smaller reading sizes the reduction; with
    no fresh read the derived reading carries reductions only.

    Phase 1 adds the per-market read of both tokens ahead of the
    whole-book walk: it is the same venue, narrower, complete for THIS
    market and stamped this tick, where the walk is truncated on every
    probe of him. It is preferred when it read fresh and complete so
    that the drift number and the position come from one reading.

    `short` (P2 rung S0, brief F2) is the MIRROR_SHORTS knob as the
    tick read it: "the smaller reading" is then the one TOWARD ZERO --
    sign x min(|derived|, |snapshot|) when the two agree on the sign,
    0 when they do not (readings that disagree on which side he is on
    justify holding neither) -- because on a negative net `min` picks
    the LARGER short. Off, `min` stands as it did."""
    derived = mi.his_net(r.his_long, r.his_other)
    snap_net = None
    if r.snap_market_fresh is True and r.mkt_net is not None:
        snap_net = float(r.mkt_net)
    elif r.fresh and r.snap_long is not None and r.snap_other is not None:
        snap_net = mi.his_net(r.snap_long, r.snap_other)
    if snap_net is None:
        return derived, None
    if drift.increase_ok:
        return snap_net, snap_net
    if drift.reduce_from == "smaller":
        if short:
            if (derived < 0) != (snap_net < 0) and derived != 0 and snap_net != 0:
                return 0.0, snap_net
            sign = -1.0 if min(derived, snap_net) < 0 else 1.0
            return sign * min(abs(derived), abs(snap_net)), snap_net
        return min(derived, snap_net), snap_net
    return derived, snap_net


async def _confirm_gone(t: _Tick, whale: str, asset: str) -> bool:
    """The mirror's OWN vanish confirmation (addendum section 8): the
    exit worker's `ours` clause excludes a book-held asset from its
    partial-walk branch, so the worker asks the data API itself. False
    on everything unreadable."""
    try:
        address = await t.pool.fetchval(_SQL_WHALE_ADDRESS, whale.lower())
    except Exception:  # noqa: BLE001
        return False
    if not address or t.http is None:
        return False
    try:
        return bool(await whale_exits._confirm_gone(t.http, t.pool, str(address), asset))
    except Exception:  # noqa: BLE001 — unknown is not gone
        return False


async def _shadow_check(t: _Tick, book: dict, target: int, net_used: float,
                        mark: float | None, allow_short: bool,
                        cap_usd: float | None = None) -> None:
    """spec 1e: a shadow reading of the same whale and market within
    60 s that computed a different target FROM THE SAME NET is an
    arithmetic divergence and is named. THE RATIOS MAY DIFFER (review
    of U12c, FIX-3): the shadow sizes from its own measured ratio and
    the book from the ratio stored at open (1.0 or MIRROR_RATIO), so
    comparing only at equal ratios never checked a 0.10 book.

    COMPARE RAW ARITHMETIC, NEVER CAPPED OR TRUNCATED OUTPUTS (FIX-3b):
    the shadow's `target` is capped at $2,500 at ITS ratio (his 24,000
    sh @ 0.50 at 1.0 is 5,000) and truncated to whole shares (0.058 x
    16 is 0), so scaling it by book.ratio / shadow.ratio named ordinary
    books. The shadow's RAW is reconstructed from the row: ratio x net
    when the row is capped (its stored raw is the cap, not arithmetic)
    or when its stored raw agrees with ratio x net; the stored raw only
    when it DISAGREES with ratio x net, which is exactly the divergence
    this instrument exists to name. That raw is scaled to the book's
    ratio, then the SAME cap the live book applies (MIRROR_NET_CAP_USD
    at the mark, in collateral on a short) and the same whole-share
    truncation and short door (mi.target_shares) -- and only then is it
    compared, a whole share or more apart being `shadow_live_disagree`
    (a P2 integrity counter: any non-zero fails the verdict, so a false
    one is not cheap). SKIPPED BY NAME (`shadow_check_skipped`, not a
    disagree) when the row lacks the fields to do that -- no raw, no
    `capped` bool, an unreadable or non-positive ratio, a mark off the
    ladder, an unreadable book ratio; a different net is not compared,
    silently, as before (addendum sections 1 and 7).

    `cap_usd` (E1) is the cap THIS book was sized at this tick -- the
    game's room when the per-game cap bound, else the per-market cap;
    None reads the per-market cap. The comparison is the arithmetic at
    that cap: a target the game room scaled is not a disagreement."""
    try:
        row = await t.pool.fetchrow(_SQL_SHADOW_LATEST, book["whale"], book["condition_id"])
    except Exception:  # noqa: BLE001 — the shadow's table is its own
        return
    if not row:
        return
    at = _num(row["at_ts"])
    if at is None or abs(t.now - at) > SHADOW_AGREE_WINDOW_S:
        return
    sr, sn, br, m = _num(row["ratio"]), _num(row["his_net"]), _num(book.get("ratio")), _num(mark)
    raw, capped = _num(row.get("target_raw")), row.get("capped")
    if (sr is None or sr <= 0 or sn is None or br is None or br <= 0 or m is None
            or not (0.01 <= m <= 0.99) or not isinstance(capped, bool)
            or (capped is False and raw is None)):
        _mirror_stop("shadow_check_skipped", book["whale"])
        return
    if abs(sn - net_used) > 1e-6:
        return
    arith = sr * sn
    shadow_raw = arith if (capped or raw is None or abs(raw - arith) <= 1e-3) else raw
    scaled = round(shadow_raw * (br / sr), 6)
    cap = float(rules.MIRROR_NET_CAP_USD)
    if cap_usd is not None and _num(cap_usd) is not None:
        cap = min(cap, float(cap_usd))          # tighten only, never raise
    expected = mi.target_shares(1.0, scaled, m, allow_short=bool(allow_short),
                                cap_usd=cap)["target"]
    if abs(float(expected) - float(target)) >= 1.0:
        _mirror_stop("shadow_live_disagree", book["whale"])
        _recent(book["id"], "shadow_live_disagree", shadow=row["target"], live=int(target),
                shadow_ratio=sr, expected=int(expected), scaled=round(scaled, 3))


async def _write_plan(t: _Tick, book: dict, r: _Reading | None, target, target_raw,
                      drift_v, his_level, reason: str, plan: dict) -> None:
    net = None
    if r is not None:
        net = mi.his_net(r.his_long, r.his_other)
    book["last_reason"] = reason
    await t.pool.execute(
        _SQL_BOOK_PLAN, book["id"], target, target_raw, net,
        r.his_long if r else None, r.his_other if r else None,
        r.snap_long if r else None, r.snap_other if r else None, drift_v, his_level,
        r.venue if r else None, reason, json.dumps(plan, default=str))


async def _cancel_open_for(t: _Tick, book: dict, reason: str, exit: bool = False) -> None:
    """Cancel the book's standing rest under `reason`. `exit` is the
    flatten's cancel before its close or slippage leg: never
    `ops_capped` (M-1)."""
    ent = t.open_by_book.get(book["id"])
    if ent is None:
        return
    o, _st = ent
    await _cancel_and_settle(t, o, book, reason, exit=exit)


async def _close_settled(t: _Tick, book: dict, standing: dict, status: str) -> None:
    """Step M's tail: the standing row left 'filled' -- settled by
    _settle_pmus_from_venue, or cashed_out / cancelled by the episode
    close -- so the book is closed and, on 'settled', its own figure is
    cross-checked against the venue's (spec 1e, book_settle_disagree)."""
    settled_pnl = own = None
    disagree = None
    if status == "settled":
        settled_pnl = _num(standing.get("pnl"))
        payout = None
        mk = await _market(t, book["condition_id"])
        try:
            idx = await t.pool.fetchval(_SQL_TOKEN_INDEX, book["long_asset"])
        except Exception:  # noqa: BLE001
            idx = None
        if mk is not None:
            payout = payout_of(mk.get("resolved_prices"), idx)
        ac = _num(book.get("avg_cost"))
        # SIGNED (P2 rung S0, brief E3): a short book's ledger is
        # negative, so `shares x (payout - avg)` IS `leg x (avg -
        # payout_long)` -- the short leg's own figure -- with no
        # branch; only the flat test reads the magnitude
        shares = float(book.get("ledger_net") or 0.0)
        if payout is not None and (ac is not None or abs(shares) <= FLAT_TOL_SHARES):
            own = round(float(book.get("realized_pnl") or 0.0)
                        + shares * (payout - (ac or 0.0)), 4)
        if settled_pnl is not None and own is not None:
            disagree = abs(settled_pnl - own) > SETTLE_DISAGREE_USD
            if disagree:
                _mirror_stop("book_settle_disagree", book["whale"])
    await t.pool.execute(_SQL_BOOK_SETTLED, book["id"], settled_pnl, own, disagree,
                         f"closed: standing row {status}")
    book["state"] = "closed"
    _forget_quiet(book["id"])
    t.stats["closed_books"] += 1
    _recent(book["id"], "closed", row=status, settled=settled_pnl, own=own)


async def _maybe_close_episode(t: _Tick, book: dict, market_live: bool | None,
                               vanished: bool, target: int | None, plan: dict,
                               venue_flat: bool = False) -> str:
    """Step E: the episode close by the rules' verdict, only with no
    non-terminal order on the book. `venue_flat` is this tick's OWN
    venue reading of the slug at zero: the sign-flip close (rules,
    2026-09-06) is handed the flip only with it, so a book flattened
    by a fill booked inside the tick (close_position, an IOC take, a
    rest found filled on re-quote) waits one tick for the venue to
    read 0 before the episode ends -- the same read-back every other
    close has (review M-1: a venue residual after a same-tick close
    would be unmanaged, and the next candidate refused
    venue_already_holds with nothing named)."""
    if (book["id"] in t.open_by_book or book["id"] in t.nonterminal
            or book.get("state") == "closed"):
        return "orders_open"
    flat_for = None
    ledger = float(book.get("ledger_net") or 0.0)
    if abs(ledger) < FLAT_TOL_SHARES and target == 0:
        since = _num((_jsonish(book.get("last_plan")) or {}).get("flat_since"))
        since = t.now if since is None else since
        plan["flat_since"] = since
        flat_for = t.now - since
    why = rules.episode_close_reason(_book_state(book), None if market_live is None else not market_live,
                                     vanished, flat_for, 0,
                                     sign_flipped=plan.get("sign_flip") is True and venue_flat is True)
    if why not in ("cashed_out", "cancelled"):
        return why
    verdict = await le._close_mirror_episode(t.pool, book["standing_row_id"],
                                             float(book.get("gross_buy_usd") or 0.0))
    if verdict is None:
        return "close_refused"
    await t.pool.execute(_SQL_BOOK_STATE, book["id"], "closed", f"closed_{verdict}")
    book["state"] = "closed"
    _forget_quiet(book["id"])
    t.stats["closed_books"] += 1
    _mirror_stop(f"closed_{verdict}", book["whale"])
    _recent(book["id"], "closed", how=verdict)
    return verdict


# ------------------------------------------- E6: the tick's venue-call budget

def _book_open(t: _Tick, book: dict) -> bool:
    """An order is open or non-terminal on the book (the reading the
    terminal memo and the quiet rule share)."""
    return (book["id"] in t.open_by_book or book["id"] in t.nonterminal
            or bool(book.get("open_order_id")))


def _exit_plan_stands(book: dict) -> bool:
    """The book's last plan was an exit's take, the take at his level or
    an unfilled reduce (QUIET_EXIT_PLANS): read on the row's own
    `last_reason` and the plan's `kind` / `reason`. Unreadable is hot."""
    try:
        lp = _jsonish(book.get("last_plan")) or {}
        names = {str(book.get("last_reason") or ""), str(lp.get("kind") or ""),
                 str(lp.get("reason") or "")}
    except Exception:  # noqa: BLE001 — an unreadable plan is a hot book
        return True
    return bool(names & QUIET_EXIT_PLANS)


def _his_fill_since(fills: list, since: float) -> bool:
    """A fill of his at or after `since` (the rows the tick already
    holds). A row with no readable `ts` counts as one: fail closed."""
    for f in fills or ():
        ts = _num((f or {}).get("ts")) if isinstance(f, dict) else None
        if ts is None or ts >= since:
            return True
    return False


def _memo_holds(book: dict) -> dict | None:
    """The quiet memo entry for the book IF the row is the one this
    process last wrote (its last_plan `at` is the memo's); None when the
    book was never read here or the row moved under another hand."""
    m = _quiet_memo.get(book["id"])
    if m is None:
        return None
    try:
        at = _num((_jsonish(book.get("last_plan")) or {}).get("at"))
    except Exception:  # noqa: BLE001 — an unreadable plan is a row the memo does not know
        return None
    if at is None or abs(at - float(m.get("at") or 0.0)) > 1e-3:
        return None
    return m


def _hot_by_row(t: _Tick, book: dict) -> bool:
    """The always-read classes the book ROW and the tick's wake alone
    decide (the fills are read inside its tick): never read by this
    process (or written by another hand since), the last read not on
    target, frozen (or any state but live), an order open, an exit plan
    standing, its market woken this tick (review HIGH-2)."""
    m = _memo_holds(book)
    return (m is None or not m.get("quiet") or book.get("state") != "live"
            or _book_open(t, book) or _exit_plan_stands(book)
            or str(book.get("condition_id")) in t.woken)


def _memo_skips(t: _Tick, book: dict) -> bool:
    """The terminal memo will skip this book before any venue call
    (_tick_book: `_terminal_book_until` ahead and nothing open) -- a
    hot book that costs the tick no read (review MEDIUM-2: ~40 books
    on EXPIRED markets each charged the quiet share one read)."""
    return (_terminal_book_until.get((book.get("whale"), book.get("condition_id")), 0.0) > t.now
            and not _book_open(t, book))


def _quiet_budget(t: _Tick, books: list) -> int:
    """What the budget leaves the quiet books once the calls the tick
    already made (steps R and O), one quote read per hot book the memo
    will not skip, and the candidates' floor (CAND_MIN_PER_TICK, review
    MEDIUM-4: reserved here so the due tick stays inside the budget)
    are counted; never negative."""
    hot = sum(1 for b in books if _hot_by_row(t, b) and not _memo_skips(t, b))
    return max(0, int(VENUE_CALLS_PER_TICK) - int(t.venue_calls) - hot - int(CAND_MIN_PER_TICK))


def _quiet_slots(t: _Tick) -> int:
    """The reads a tick makes on the DEFERRED queue: the quiet budget,
    never under DEFERRED_MIN_PER_TICK."""
    return max(int(t.quiet_budget), int(DEFERRED_MIN_PER_TICK))


def _forget_quiet(bid) -> None:
    """The rotation forgets a book (it closed; the walk no longer lists
    it): review LOW-2. A book that comes back is read, never skipped."""
    _quiet_memo.pop(bid, None)
    _quiet_deferred.pop(bid, None)


def _forget_unlisted(books: list) -> None:
    listed = {b["id"] for b in books}
    for bid in [k for k in set(_quiet_memo) | set(_quiet_deferred) if k not in listed]:
        _forget_quiet(bid)


def _deferred_due(t: _Tick) -> set:
    """The deferred books THIS tick reads: the head of the queue, in
    the order they were deferred, _quiet_slots of them. Decided before
    the walk so the walk's order (games, not deferral order) cannot
    hand a deferred book's slot to a newly due one."""
    return set(list(_quiet_deferred)[:_quiet_slots(t)])


def _deferred_read_on(t: _Tick, bid) -> int:
    """The tick a queued book is read on at this tick's slot count: the
    next tick for the first _quiet_slots of the queue once this tick's
    head is read, one tick more per slot count behind that."""
    ahead = 0
    for k in _quiet_deferred:
        if k == bid:
            break
        if k not in t.deferred_due:
            ahead += 1
    return int(t.seq) + 1 + ahead // max(1, _quiet_slots(t))


def _quiet_skip(t: _Tick, book: dict, fills: list) -> int | None:
    """THE QUIET RULE (E6), decided after step M and before the quote
    read. None: read the book now. An int: skip its reads this tick and
    write `book_quiet_skipped` with that tick number as `read_on`.

      hot (always None)   never read by this process; the last read not
                          on target; not live; an order open; an exit
                          plan standing; woken; a fill of his inside HOT_S
      deferred, at the    read now: the queue's head, FIFO, under
        queue's head      max(quiet_budget, DEFERRED_MIN_PER_TICK)
      deferred, behind    stays queued: read_on = its turn in the queue
      not due             fewer than QUIET_EVERY_TICKS ticks since its
                          last read: read_on = last + QUIET_EVERY_TICKS
      due, budget spent   joins the queue's tail (the budget less the
                          head's reads): read_on = its turn
      due, budget left    read now (one slot reserved here, no await)"""
    bid = book["id"]
    try:
        m = _memo_holds(book)
        hot = m is None or _hot_by_row(t, book) or _his_fill_since(fills, t.now - HOT_S)
        last = None if hot else int(m["seq"])
    except Exception:  # noqa: BLE001 — a memo or a row this cannot read is a book it reads
        hot, last = True, None
    if hot:
        _quiet_deferred.pop(bid, None)
        return None
    if bid in _quiet_deferred:
        if bid in t.deferred_due:
            _quiet_deferred.pop(bid, None)
            t.quiet_reads += 1
            return None
        return _deferred_read_on(t, bid)
    if t.seq < last + QUIET_EVERY_TICKS:
        return last + QUIET_EVERY_TICKS
    if t.due_reads >= max(0, int(t.quiet_budget) - len(t.deferred_due)):
        _quiet_deferred[bid] = int(t.seq)
        return _deferred_read_on(t, bid)
    t.due_reads += 1
    t.quiet_reads += 1
    return None


def _note_read(t: _Tick, book: dict, on_target: bool) -> None:
    """The quiet memo's writer for a READ: this book's quote was read
    this tick (`on_target` False until the plan's final verdict says
    otherwise); `at` is the plan's `at` this tick writes (t.now)."""
    _quiet_memo[book["id"]] = {"seq": int(t.seq), "quiet": bool(on_target), "at": float(t.now)}


def _note_skip(t: _Tick, book: dict) -> None:
    """The memo's writer for a SKIP: the seq and the verdict stand (the
    last read's), `at` moves to the plan this tick writes."""
    m = _quiet_memo.get(book["id"]) or {"seq": int(t.seq), "quiet": False}
    _quiet_memo[book["id"]] = {**m, "at": float(t.now)}


def _cand_budget(t: _Tick) -> int:
    """The candidate stage's share of the budget once the books are
    walked: map reads and candidate quote reads together, never below
    CAND_MIN_PER_TICK."""
    return max(int(CAND_MIN_PER_TICK), int(VENUE_CALLS_PER_TICK) - int(t.venue_calls))


def _map_cap(cand_budget: int) -> int:
    """The candidate stage's MAP-read cap (E6 fold, the 19:42Z tick:
    `map_reads_capped` 108 at `map_venue_read` 10, ~$100k of his 24 h
    flow unmapped by the old per-tick cap of ms.MAP_READS_PER_TICK).
    The old cap is the budget's FLOOR, never its ceiling: the resolver
    may spend what sits ABOVE the quote reads' own floor -- the share
    less CAND_MIN_PER_TICK (review HIGH-1: a candidate maps before it
    reads, so a resolver that spent the whole share left the mapped
    candidates behind it unread, every tick at the floor). An operator
    who set MIRROR_MAP_READS lowered a rail, and a lowered rail is
    honoured as a ceiling under the share."""
    floor = int(ms.MAP_READS_PER_TICK)
    share = max(floor, int(cand_budget) - int(CAND_MIN_PER_TICK))
    if os.environ.get("MIRROR_MAP_READS", "").strip():
        return max(0, min(floor, share))
    return share


def _cand_cap(t: _Tick) -> int:
    """The candidate QUOTE-read cap this tick: MAX_MARKETS_PER_TICK (env
    may only lower it), and never more than the stage's share leaves
    after its map reads (spent first: a candidate maps before it reads)
    -- never under CAND_MIN_PER_TICK, the quote reads' floor the map
    reads cannot spend (review HIGH-1)."""
    left = max(int(CAND_MIN_PER_TICK), int(t.cand_budget) - int(t.map_budget.reads))
    return max(0, min(int(MAX_MARKETS_PER_TICK), left))


def _timing_block(t: _Tick) -> dict:
    """The tick's time, measured (E6 part 1): seconds, one decimal --
    `walk` (step R, the positions walk), `orders` (step O), `books`
    (step B, wall time), `books_venue` (the books' paced venue calls:
    their quote reads and every paced write the walk sent, summed per
    call, so under the parallel walk it can exceed `books`),
    `books_data` (their per-market data-API reads, the same way),
    `candidates` (the candidate stage); the counts beside them
    (`venue_calls`, `snap_market_reads`); the books by outcome class
    (`read`: a quote read; `on_target`, `placed`, `no_mark`,
    `terminal_skipped`, `quiet_skipped`); the budget's numbers
    (`budget`, `quiet_budget`, `quiet_reads`, `cand_budget`, `map_cap`,
    `cand_cap`). Bounded: these keys and no others."""
    tm = t.timing
    out = {k: round(float(tm.get(k) or 0.0), 1)
           for k in ("walk", "orders", "books", "books_venue", "books_data", "candidates")}
    out["venue_calls"] = int(t.venue_calls)
    out["snap_market_reads"] = int(t.stats.get("snap_market_reads") or 0)
    for k in ("read", "on_target", "placed", "no_mark", "terminal_skipped", "quiet_skipped"):
        out[k] = int(t.outcomes.get(k) or 0)
    out.update(budget=int(VENUE_CALLS_PER_TICK), quiet_budget=int(t.quiet_budget),
               quiet_reads=int(t.quiet_reads), cand_budget=int(t.cand_budget),
               map_cap=int(getattr(t.map_budget, "cap", 0) or 0), cand_cap=int(t.cand_cap))
    return out


def _data_api_block(t: _Tick) -> dict:
    """The data-API wait, measured (E7 part B): the books' per-market
    reads' seconds inside the process-wide throttle's wait
    (`books_data_wait`) and inside the request (`books_data_req`),
    summed per call like `books_data` (which they split), and
    `data_rps`, the throttle's configured rate
    (settings().data_api_max_rps, shared with the live poller, the
    backfill and the reconciler; None when unreadable). Served beside
    the timing block as `short.data_api` -- the block's own keys are
    pinned exactly, so these ride in a sibling. Bounded: these keys and
    no others. Measurement only: nothing here changes a read, its
    budget or the throttle's rate."""
    tm = t.timing
    try:
        rps = _num(getattr(settings(), "data_api_max_rps", None))
    except Exception:  # noqa: BLE001 — settings unreadable: no rate to print, never a raise
        rps = None
    return {"books_data_wait": round(float(tm.get("books_data_wait") or 0.0), 1),
            "books_data_req": round(float(tm.get("books_data_req") or 0.0), 1),
            "data_rps": rps}


def _paced_seconds() -> float:
    with _PACED_LOCK:
        return float(_PACED_S["s"])


def _terminal_memo_snapshot(now: float) -> dict:
    """Both terminal memos, bounded: every entry whose `until` has
    passed dropped, at most _TERMINAL_MEMO_MAX kept (the soonest to
    expire dropped first), sorted so the same memo is the same text."""
    cand = sorted([[str(w), str(c), round(float(u), 1)] for (w, c), u in _terminal_until.items()
                   if _num(u) is not None and float(u) > now], key=lambda e: (-e[2], e[0], e[1]))
    book = sorted([[str(w), str(c), round(float(u), 1), _terminal_book_state.get((w, c))]
                   for (w, c), u in _terminal_book_until.items()
                   if _num(u) is not None and float(u) > now], key=lambda e: (-e[2], e[0], e[1]))
    return {"cand": cand[:_TERMINAL_MEMO_MAX], "book": book[:_TERMINAL_MEMO_MAX]}


def _terminal_memo_sig(snap: dict) -> str:
    return json.dumps(snap, sort_keys=True, default=str)


async def _load_terminal_memo(t: _Tick) -> None:
    """The one boot read of the terminal memos (E6 part 3). Unreadable
    or malformed: the memos stay empty (today's behaviour) and the
    reason is logged; an entry that is not [whale, cid, until(, state)]
    with a finite `until` still ahead is dropped, never guessed; a book
    entry whose state is not one of ms.STATE_TERMINAL (the only states
    the memo's writer ever records) is dropped too -- a HALTED entry in
    the key would skip a live book for its `until` (review LOW-3)."""
    value, err = await _state(t.pool, _STATE_TERMINAL_MEMO)
    loaded = 0
    if err is not None or (value is not None and not isinstance(value, dict)):
        log.warning("mirror_live: %s unreadable (%s); the terminal memos start empty",
                    _STATE_TERMINAL_MEMO, err or type(value).__name__)
    elif isinstance(value, dict):
        cand, book = value.get("cand"), value.get("book")
        for e in (cand if isinstance(cand, list) else []):
            if (isinstance(e, list) and len(e) == 3 and isinstance(e[0], str) and isinstance(e[1], str)
                    and _num(e[2]) is not None and float(e[2]) > t.now):
                _terminal_until[(e[0], e[1])] = float(e[2])
                loaded += 1
        for e in (book if isinstance(book, list) else []):
            if (isinstance(e, list) and len(e) == 4 and isinstance(e[0], str) and isinstance(e[1], str)
                    and _num(e[2]) is not None and float(e[2]) > t.now and isinstance(e[3], str)
                    and e[3] in ms.STATE_TERMINAL):
                _terminal_book_until[(e[0], e[1])] = float(e[2])
                _terminal_book_state[(e[0], e[1])] = e[3]
                loaded += 1
    # what is held now is what stands written: the next write is a change
    _terminal_memo_last.update(at=float(t.now), sig=_terminal_memo_sig(_terminal_memo_snapshot(t.now)))
    if loaded:
        log.info("mirror_live: %d terminal memo entries read at boot", loaded)


async def _persist_terminal_memo(t: _Tick) -> None:
    """The bounded snapshot, written when it changed and at most once
    per TERMINAL_MEMO_WRITE_S; a failed write is logged and retried on
    a later tick (the signature is kept only on success). Never raises.
    Nothing is written before the boot read was made: a first tick
    refused ahead of it (tables_absent, intent_guard_unreadable -- the
    pool not yet up at the deploy's own moment) would otherwise persist
    a fresh process's EMPTY memo over the old process's (review
    MEDIUM-5)."""
    if not _terminal_memo_loaded:
        return
    snap = _terminal_memo_snapshot(t.now)
    sig = _terminal_memo_sig(snap)
    if sig == _terminal_memo_last.get("sig"):
        return
    if float(t.now) - float(_terminal_memo_last.get("at") or 0.0) < TERMINAL_MEMO_WRITE_S:
        return
    try:
        await _write_state(t.pool, _STATE_TERMINAL_MEMO, {**snap, "at": _iso(float(t.now))})
    except Exception as exc:  # noqa: BLE001 — a memo that did not persist is the old behaviour
        log.warning("mirror_live: %s write failed (%s)", _STATE_TERMINAL_MEMO, type(exc).__name__)
        return
    _terminal_memo_last.update(at=float(t.now), sig=sig)


# ------------------------------- E7: the candidate memos, released by his fills

def _memo_unmapped(t: _Tick, whale: str, cid: str, fills: list) -> float:
    """The unmapped memo's ONE writer (the paragraph over NO_MARK_TTL_S):
    every candidate exit that maps this market to nothing this lane can
    open -- `unmapped`, `map_source_unverified`, the grammar class's
    refusals, `long_token_unknown` -- writes `until` = now + TTL and the
    read's `at` beside it. GROWTH: when the market's previous memo
    still stands in the dict (expired or released nothing) and NO fill
    of his landed since that read (`fills`, the rows this read holds; a
    fill with no readable stamp counts as one; a wake this tick counts
    as one) AND the walk's stamp of his newest fill on the market, when
    it holds one, is older than UNMAPPED_TTL_MAX_S (review MEDIUM-2: a
    market he entered inside the hour keeps the base TTL, listed later
    or not) the TTL doubles,
    capped at UNMAPPED_TTL_MAX_S; else the base. Returns the TTL written."""
    key = (whale, cid)
    base = float(ms.UNMAPPED_TTL_S)
    ttl = base
    prev = _unmapped_memo.get(key) if key in _unmapped_until else None
    prev_at = _memo_at(prev)
    prev_ttl = _num(prev[1]) if isinstance(prev, tuple) and len(prev) == 2 else None
    # the fill-independent ceiling (E7 review MEDIUM-2): no growth while
    # his newest fill on the market -- the walk's stamp, the `last_ts` the
    # candidate order ranks on (t.stamps; absent = no evidence) -- sits
    # inside the last UNMAPPED_TTL_MAX_S: a market he entered inside the
    # hour is re-read at the base TTL whether or not he adds
    newest = _num(t.stamps.get(cid))
    recent = newest is not None and (float(t.now) - newest) < float(UNMAPPED_TTL_MAX_S)
    if (prev_at is not None and prev_ttl is not None and cid not in t.woken
            and not _his_fill_since(fills, prev_at) and not recent):
        ttl = min(max(base, prev_ttl) * 2.0, float(UNMAPPED_TTL_MAX_S))
    _unmapped_until[key] = t.now + ttl
    _unmapped_memo[key] = (float(t.now), float(ttl))
    return ttl


def _memo_at(m: Any) -> float | None:
    """The read's `at` off a memo entry: the unmapped memo's (at, ttl)
    tuple or the no_mark memo's bare instant; unreadable is None."""
    if isinstance(m, tuple):
        return _num(m[0]) if m else None
    return _num(m)


def _memo_no_mark(t: _Tick, whale: str, cid: str, r: _Reading) -> bool:
    """The no_mark memo's ONE writer: a CANDIDATE's quote read (never a
    book's: _tick_book never calls this) that found the market OPEN --
    the venue's own state on that read, `r.venue_state` -- with no mark
    to read (`r.mark` None: an empty book, or a bid with no ask). A
    HALTED / SUSPENDED / PREOPEN read (those reopen), a read naming no
    state and a read that raised write nothing (D1's rule for the
    terminal memo, the same reason). Returns whether it was written."""
    if r.mark is not None or r.venue_state != _STATE_OPEN:
        return False
    key = (whale, cid)
    _no_mark_until[key] = t.now + float(NO_MARK_TTL_S)
    _no_mark_memo[key] = float(t.now)
    return True


def _release_cand_memo(t: _Tick, whale: str, cid: str, stamp: float | None) -> None:
    """RELEASE BY HIS FILLS, at the walk: a memoised market (either
    memo) whose newest fill of his (`stamp`, the `last_ts` the
    candidate order ranks on) is NEWER than the memo's `at`, or that
    WOKE this tick, is read this tick -- the memo dropped, counted
    `cand_memo_released` when it still held. An `until` with no `at`
    beside it is released by any readable stamp (fail closed: read). A
    stamp the walk could not read (None) releases nothing: no evidence
    of a newer fill, the memo's own TTL stands."""
    key = (whale, cid)
    woken = cid in t.woken
    for until_d, memo_d in ((_unmapped_until, _unmapped_memo), (_no_mark_until, _no_mark_memo)):
        if key not in until_d:
            continue
        at = _memo_at(memo_d.get(key))
        if not (woken or (stamp is not None and (at is None or float(stamp) > at))):
            continue
        until = _num(until_d.get(key))
        live = until is not None and until > t.now
        until_d.pop(key, None)
        memo_d.pop(key, None)
        if live:
            _mirror_stop("cand_memo_released", whale)


def _cand_memo_skips(whale: str, cid: str, now: float, stamp: float | None, woken: set) -> bool:
    """Would either candidate memo skip this market at `now`, once his
    newest fill is judged against it (the walk's release rule, judged
    without dropping anything)? For _name_unread: a market the memo
    would have skipped anyway is not one the cap cut."""
    key = (whale, cid)
    if cid in woken:
        return False
    for until_d, memo_d in ((_unmapped_until, _unmapped_memo), (_no_mark_until, _no_mark_memo)):
        at = _memo_at(memo_d.get(key))
        until = _num(until_d.get(key))
        if until is None or until <= now:
            continue
        if stamp is not None and (at is None or float(stamp) > at):
            continue                        # his newer fill: it would have been read
        return True
    return False


def _prune_cand_memos(now: float) -> None:
    """Drop every candidate memo entry UNMAPPED_TTL_MAX_S past its
    `until` (the growth it could still inform is a re-read that never
    came: the market left his active list or sat behind the cap for an
    hour -- it restarts at the base), and any `at` entry with no
    `until` beside it. Bounded by his active conditions, like the
    dicts themselves."""
    cut = float(now) - float(UNMAPPED_TTL_MAX_S)
    for until_d, memo_d in ((_unmapped_until, _unmapped_memo), (_no_mark_until, _no_mark_memo)):
        for k in [k for k, u in until_d.items() if _num(u) is None or float(u) < cut]:
            until_d.pop(k, None)
            memo_d.pop(k, None)
        for k in [k for k in memo_d if k not in until_d]:
            memo_d.pop(k, None)


def _cand_memo_snapshot(now: float) -> dict:
    """Both candidate memos, bounded the terminal memos' way: every
    entry whose `until` has passed dropped, one with no readable `at`
    (or TTL) beside it dropped (nothing to judge a release by after a
    deploy: the market is read), at most _TERMINAL_MEMO_MAX per list,
    the soonest to expire dropped first, sorted so the same memo is the
    same text."""
    un, nm = [], []
    for (w, c), u in _unmapped_until.items():
        m = _unmapped_memo.get((w, c))
        if _num(u) is None or float(u) <= now or not (isinstance(m, tuple) and len(m) == 2):
            continue
        at, ttl = _num(m[0]), _num(m[1])
        if at is None or ttl is None:
            continue
        un.append([str(w), str(c), round(float(u), 1), round(at, 1), round(ttl, 1)])
    for (w, c), u in _no_mark_until.items():
        at = _num(_no_mark_memo.get((w, c)))
        if _num(u) is None or float(u) <= now or at is None:
            continue
        nm.append([str(w), str(c), round(float(u), 1), round(at, 1)])
    un.sort(key=lambda e: (-e[2], e[0], e[1]))
    nm.sort(key=lambda e: (-e[2], e[0], e[1]))
    return {"unmapped": un[:_TERMINAL_MEMO_MAX], "no_mark": nm[:_TERMINAL_MEMO_MAX]}


def _cand_memo_entry_ok(e: Any, n: int, now: float, max_ttl: float) -> bool:
    """One persisted entry's shape: [whale, cid, until, at(, ttl)] with
    strings, a finite `until` still ahead, a finite `at` not ahead of
    `now` and not past `until`, the memo no longer than `max_ttl` (an
    entry written under a wider rail than today's is dropped: the
    lowered rail is honoured, the market read) and, for the unmapped
    memo, a TTL on the ladder [base, UNMAPPED_TTL_MAX_S] that the
    `until` respects. Anything else is dropped, never guessed."""
    if not (isinstance(e, list) and len(e) == n and isinstance(e[0], str) and isinstance(e[1], str)):
        return False
    until, at = _num(e[2]), _num(e[3])
    if until is None or at is None or until <= now or at > now or at > until or until - at > max_ttl + 1e-3:
        return False
    if n == 5:
        ttl = _num(e[4])
        if ttl is None or ttl < float(ms.UNMAPPED_TTL_S) or ttl > float(UNMAPPED_TTL_MAX_S) or until - at > ttl + 1e-3:
            return False
    return True


async def _load_cand_memo(t: _Tick) -> None:
    """The one boot read of the candidate memos (E7), the terminal
    memos' idiom: unreadable or malformed, the memos stay empty (a
    deploy reads everything, today's behaviour) and the reason is
    logged; an entry _cand_memo_entry_ok refuses is dropped."""
    value, err = await _state(t.pool, _STATE_CAND_MEMO)
    loaded = 0
    if err is not None or (value is not None and not isinstance(value, dict)):
        log.warning("mirror_live: %s unreadable (%s); the candidate memos start empty",
                    _STATE_CAND_MEMO, err or type(value).__name__)
    elif isinstance(value, dict):
        un, nm = value.get("unmapped"), value.get("no_mark")
        for e in (un if isinstance(un, list) else []):
            if _cand_memo_entry_ok(e, 5, t.now, float(UNMAPPED_TTL_MAX_S)):
                _unmapped_until[(e[0], e[1])] = float(e[2])
                _unmapped_memo[(e[0], e[1])] = (float(e[3]), float(e[4]))
                loaded += 1
        for e in (nm if isinstance(nm, list) else []):
            if _cand_memo_entry_ok(e, 4, t.now, float(NO_MARK_TTL_S)):
                _no_mark_until[(e[0], e[1])] = float(e[2])
                _no_mark_memo[(e[0], e[1])] = float(e[3])
                loaded += 1
    _cand_memo_last.update(at=float(t.now), sig=_terminal_memo_sig(_cand_memo_snapshot(t.now)))
    if loaded:
        log.info("mirror_live: %d candidate memo entries read at boot", loaded)


async def _persist_cand_memo(t: _Tick) -> None:
    """The candidate memos' bounded snapshot, written when it changed
    and at most once per TERMINAL_MEMO_WRITE_S, never before the boot
    read (E6 review MEDIUM-5), a failed write logged and retried on a
    later tick. Never raises."""
    if not _terminal_memo_loaded:
        return
    snap = _cand_memo_snapshot(t.now)
    sig = _terminal_memo_sig(snap)
    if sig == _cand_memo_last.get("sig"):
        return
    if float(t.now) - float(_cand_memo_last.get("at") or 0.0) < TERMINAL_MEMO_WRITE_S:
        return
    try:
        await _write_state(t.pool, _STATE_CAND_MEMO, {**snap, "at": _iso(float(t.now))})
    except Exception as exc:  # noqa: BLE001 — a memo that did not persist is the old behaviour
        log.warning("mirror_live: %s write failed (%s)", _STATE_CAND_MEMO, type(exc).__name__)
        return
    _cand_memo_last.update(at=float(t.now), sig=sig)


def _memo_terminal_book(t: _Tick, book: dict, r: _Reading) -> None:
    """The book memo's ONE writer (W1 / R4): this book's own quote read
    (`r.venue_state`, as _bbo recorded it on the book=True read) named
    a TERMINAL state and nothing is open on the book. HALTED /
    SUSPENDED / PREOPEN (not in ms.STATE_TERMINAL) and an unread state
    (None) write nothing; a book with an order open (t.open_by_book /
    t.nonterminal) writes nothing this tick -- the `no_mark` refusal
    below cancels the rest, and the next terminal read memoises."""
    state = r.venue_state
    if state is None or state not in ms.STATE_TERMINAL:
        return
    if book["id"] in t.open_by_book or book["id"] in t.nonterminal or book.get("open_order_id"):
        return
    key = (book["whale"], book["condition_id"])
    _terminal_book_until[key] = t.now + ms.UNMAPPED_TTL_S
    _terminal_book_state[key] = str(state)


async def _tick_book(t: _Tick, book: dict) -> None:
    """One existing book: read, step M, plan, act, close."""
    w, cid, slug = book["whale"], book["condition_id"], book["us_market_slug"]
    la, oa = book["long_asset"], book.get("other_asset")
    t.books_seen.add((w, cid))
    # E5 review F1: was the book frozen when its tick began? A book that
    # freezes THIS tick (venue vs ledger on a walk one fill stale) is on
    # its transition tick: a one-tick disagreement is not evidence of a
    # lost response, and the frozen exit never runs on it
    was_frozen = book.get("state") == "frozen"
    if was_frozen:
        t.stats["books_frozen"] += 1
        if book.get("frozen_ts") and t.now - float(book["frozen_ts"]) > float(rules.MIRROR_FROZEN_ALERT_S):
            t.stats["status"] = "degraded"
    else:
        t.stats["books_live"] += 1
    # the standing row first: a row the settlement or the close has
    # retired ends the book before any venue read
    standing = await t.pool.fetchrow(_SQL_STANDING_READ, book["standing_row_id"])
    standing = dict(standing) if standing else None
    if standing is not None and standing.get("status") in ("settled", "cashed_out", "cancelled"):
        await _cancel_open_for(t, book, "market_closed")
        # closed only once nothing of the book is non-terminal: a
        # cancel the ops budget refused, or an order 'unknown', still
        # rests, and its fill would land on the retired row (step-9
        # review); the next tick finishes the cancel, then closes
        if book["id"] not in t.open_by_book and book["id"] not in t.nonterminal:
            await _close_settled(t, book, standing, str(standing["status"]))
        return
    if standing is None or standing.get("status") != "filled" or standing.get("lane") != "mirror":
        await _cancel_open_for(t, book, "row_not_live")
        await _freeze(t, book, "row_not_live")
        return
    # the row's FRACTIONAL shares as read this tick (the same read, no
    # second query), carried on the tick's book dict so a SELL is sized
    # at min(plan, ledger, ceil(held)), never from the rounded ledger
    # alone (_sell_qty; 2026-09-06). None when the column is unreadable:
    # the sizing then stands on the ledger as before. This is the ONE
    # write of it: a fill booked later in the tick does not move it
    book["_held"] = _num(standing.get("filled_shares"))
    fills = await ms.his_fills(t.pool, w, cid)
    _count_fills_dedup(t)
    # STEP M BEFORE ANY PLAN (addendum section 10): a closed or
    # resolved market, or a closing book, cancels and never increases.
    # 'closing' is entered on a POSITIVE reading only (closed True or
    # resolved True): a markets row that could not be read, is absent
    # or is malformed is `market_unreadable` -- what rests is cancelled
    # and the book is HELD with no plan until a readable tick, because
    # one unreadable read once made a held book 'closing' for good,
    # with no reduction or flatten ever again (step-9 review)
    mk = await _market(t, cid)
    market_live = None if mk is None else bool(mk["closed"] is False and mk["resolved"] is False)
    closed_read = mk is not None and (mk["closed"] is True or mk["resolved"] is True)
    if closed_read or book.get("state") == "closing":
        if book.get("state") != "closing":
            await t.pool.execute(_SQL_BOOK_STATE, book["id"], "closing", "market_closed")
            book["state"] = "closing"
            _mirror_stop("market_closed", w)
        await _cancel_open_for(t, book, "market_closed")
        plan = {"kind": "closing", "market_live": market_live}
        why = await _maybe_close_episode(t, book, market_live, False,
                                         0 if abs(float(book.get("ledger_net") or 0)) < FLAT_TOL_SHARES else None,
                                         plan)
        if book.get("state") != "closed":
            await _write_plan(t, book, None, book.get("target"), None, None, book.get("his_level"),
                              f"closing: {why}", plan)
        return
    if market_live is not True:
        _mirror_stop("market_unreadable", w)
        await _cancel_open_for(t, book, "market_unreadable")
        await _write_plan(t, book, None, book.get("target"), None, None, book.get("his_level"),
                          "market_unreadable", {"kind": "no_plan", "market_unreadable": True,
                                                "at": t.now})
        return
    if (_terminal_book_until.get((w, cid), 0.0) > t.now
            and book["id"] not in t.open_by_book and book["id"] not in t.nonterminal
            and not book.get("open_order_id")):
        # THE BOOK'S LAST READ SAID THE MARKET HAD ENDED (W1 / R4): no
        # quote read and no per-market read until the memo's TTL runs
        # -- step M above still ran, so the row's close lands the tick
        # it reads closed. Held under the name the terminal read
        # cancelled it under; nothing rests on it (the memo is never
        # written over an open order), so there is nothing to cancel
        _mirror_stop("no_mark", w)
        _mirror_stop("book_terminal_skipped", w)
        t.outcomes["terminal_skipped"] += 1
        await _write_plan(t, book, None, book.get("target"), None, None, book.get("his_level"),
                          "no_mark", {"kind": "no_plan", "at": t.now,
                                      "venue_terminal": _terminal_book_state.get((w, cid))})
        return
    read_on = _quiet_skip(t, book, fills)
    if read_on is not None:
        # THE QUIET BOOK'S TURN IS NOT THIS TICK (E6; the paragraph over
        # VENUE_CALLS_PER_TICK): on target at its last read, nothing
        # open, no fill of his inside HOT_S, no exit plan standing --
        # its quote read and its per-market read wait for `read_on`.
        # Step M above ran, the standing row was read, nothing rests on
        # it (an open order is a hot book), so nothing is cancelled and
        # no order path is touched. The row says so by name
        _mirror_stop("book_quiet_skipped", w)
        t.outcomes["quiet_skipped"] += 1
        # what the next read takes from the PRIOR plan rides on the skip's
        # (_SKIP_CARRIED): the flat clock, the episode's one short proof
        # (recorded once: a plan without it would record it again), the
        # last read's mark and quotes (a sibling's game room reads a book
        # not read this tick at its last plan's mark, _held_exposure),
        # the exit's price source
        prior = _jsonish(book.get("last_plan")) or {}
        plan = {k: prior[k] for k in _SKIP_CARRIED if prior.get(k) is not None}
        plan.update(kind="no_plan", at=t.now, read_on=int(read_on), tick=int(t.seq),
                    last_read=int(_quiet_memo[book["id"]]["seq"]))
        if _num(plan.get("mark")) is not None:
            t.marks[book["id"]] = float(plan["mark"])
        _note_skip(t, book)
        # A SKIPPED READ NEVER SKIPS A CLOSE: the episode close runs on
        # the skipped book exactly as on a read one -- step M's own
        # market reading, the row's last target (the last read's, at
        # most QUIET_EVERY_TICKS ticks old with no fill of his since)
        # and the flat clock carried from the prior plan; `vanished` and
        # `venue_flat` are readings the skip does not have, so they are
        # handed False (the vanish and the sign-flip closes wait for a
        # read tick, never a guess)
        tg = _num(book.get("target"))
        plan["close"] = await _maybe_close_episode(t, book, market_live, False,
                                                   None if tg is None else int(tg), plan)
        if book.get("state") != "closed":
            # the skip's own write (_SQL_BOOK_SKIP): the name and the
            # plan; the last read's figures on the row stand (LOW-1)
            book["last_reason"] = "book_quiet_skipped"
            await t.pool.execute(_SQL_BOOK_SKIP, book["id"], "book_quiet_skipped",
                                 json.dumps(plan, default=str))
        return
    r = await _read_market(t, w, cid, slug, la, oa, fills, market=mk, book=True)
    t.outcomes["read"] += 1
    _note_read(t, book, False)              # read this tick; the verdict is the plan's, below
    if t.abandoned:
        return
    _memo_terminal_book(t, book, r)
    if str(book.get("map_source") or "") == "grammar":
        # the class's first-fill echo (C1 round 2): a frozen mismatch
        # plans nothing this tick, and neither does a book whose side the
        # venue has not yet confirmed (round 3: it WAITS, unfrozen)
        fc = await _grammar_fill_check(t, book)
        if fc != "ok":
            why = "side_echo_mismatch" if fc == "frozen" else "grammar_echo_unverified"
            await _write_plan(t, book, r, book.get("target"), None, None, book.get("his_level"),
                              why, {"kind": "no_plan", "at": t.now, why: True})
            return
    ledger = int(book.get("ledger_net") or 0)
    short = _book_short(book)
    shorts = _shorts_on(t)
    drift, drift_src = _drift_for(r)
    net, snap_net = _net_for(r, drift, short=shorts)
    venue_int = int(r.venue)
    # the plan's numbers, written whatever happens below
    # THE REGISTER'S SIGN (E5 / P1): a register sum is read only when
    # its sign is the book's leg's -- at or above 0 on a long book, at
    # or under 0 on a short one. Against the leg it would say the
    # venue holds LESS than the ledger (a lost close's sale registered
    # as a negative on a long book) and the thawed plan would sell
    # shares the venue does not hold: not read, named, the book stays
    # frozen. Counted as a registered book on the row's presence alone
    registered = float(r.registered or 0.0)
    sign_refused = None
    if registered != 0.0:
        _mirror_stop("registered_books", w)
        if (registered < 0.0) != short:
            _mirror_stop("registered_sign_refused", w)
            sign_refused, registered = registered, 0.0
    plan: dict[str, Any] = {"bid": r.bid, "ask": r.ask, "mark": r.mark, "venue": r.venue,
                            "manual": r.manual, "registered": registered, "ledger": ledger, "net": net,
                            "snap_net": snap_net, "fresh": r.fresh, "drift": drift.drift,
                            # MIRRORSNAP reads these: the per-market
                            # read's own verdict for THIS market, the two
                            # legs it was formed from (so a merged-pair
                            # diagnosis can be reconstructed from the row
                            # alone), the whole-book walk's net beside it
                            # where the two venue readings can be seen to
                            # part, the drift measured on the net rather
                            # than on one token, and the agreement the
                            # drift rule was handed
                            "snap_market_fresh": r.snap_market_fresh, "drift_src": drift_src,
                            "mkt_long": r.mkt_long, "mkt_other": r.mkt_other,
                            "snap_net_book": _book_net(r), "fresh_agreed": _fresh_agreed(r),
                            "at": t.now}
    if sign_refused is not None:
        plan["registered_sign_refused"] = sign_refused
    prior_plan = _jsonish(book.get("last_plan")) or {}
    # the flat clock carries only while the book IS flat: a re-bought
    # book that flattens again starts a new MIRROR_FLAT_CLOSE_S wait,
    # never closes cashed_out at once off the clock of an earlier flat
    # spell (step-9 review); dropped again below once the target reads
    # above zero
    if prior_plan.get("flat_since") is not None and abs(ledger) < FLAT_TOL_SHARES:
        plan["flat_since"] = prior_plan["flat_since"]
    # THE ONE-WAY STEP (review of U12c, FIX-1): an exact-copy book (ratio
    # 1.0) whose owner position has grown past twice the small-bet line
    # steps to MIRROR_RATIO for life -- written to the row BEFORE the
    # in-memory book moves, so a failed write steps nothing -- and is
    # named; the target below then reads the stepped ratio and the
    # reduce path sells the excess. Never up; never on any other ratio
    stepped = rules.step_ratio(book.get("ratio"), net, r.mark)
    if stepped is not None:
        await t.pool.execute(_SQL_BOOK_RATIO, book["id"], float(stepped))
        book["ratio"] = float(stepped)
        plan["ratio_stepped"] = float(stepped)
        _mirror_stop("ratio_stepped", w)
        _recent(book["id"], "ratio_stepped", ratio=float(stepped))
    # THE GAME'S ROOM (E1): the per-game cap less the OTHER books of the
    # game -- held at cost plus their resting increases, or the target a
    # book earlier in this tick's walk was sized to. Read before the
    # target, whatever the target turns out to be, so the plan row says
    # what the game had left; this book's own mark is recorded for the
    # later books' reading of it (a held book with no avg_cost)
    t.marks[book["id"]] = r.mark
    cap = float(rules.MIRROR_NET_CAP_USD)
    game_exposure = _game_exposure(t, book)
    room = rules.game_room(game_exposure, cap)
    plan.update(game_exposure=game_exposure, game_room=room)
    if game_exposure is None:
        # named ONCE per book, here, and told apart from a game that is
        # full (re-review LOW-3): the plan's null `game_exposure` says
        # which, the census counts them separately
        _mirror_stop("game_unreadable", w)
    # THE TARGET, from the book's FIXED ratio (addendum section 7)
    if t.flatten_all:
        tg = {"target": 0, "raw": 0.0, "refusal": None}
    else:
        # THE SIGN DOOR IS THE KNOB (P2 rung S0): with MIRROR_SHORTS on
        # a negative net is a signed target; off, `short_side_refused`
        # and target 0 as in P1 -- which on a SHORT book left open when
        # the knob went off is the reversal path, a flatten by
        # close_position when sole (brief section 6)
        tg = rules.mirror_target(book.get("ratio"), net, r.mark, MIRROR_ANCHOR_CLIP_USD,
                                 cap_usd=rules.MIRROR_NET_CAP_USD, allow_short=shorts)
    target = tg["target"]
    if tg.get("refusal") == "short_side_refused":
        _mirror_stop("short_side_refused", w)
    elif tg.get("refusal"):
        # NO PLAN: never "target zero, flatten". What rests is cancelled
        # by the plan's own name; the book is held.
        _mirror_stop(tg["refusal"], w)
        if tg["refusal"] == "no_mark":
            t.outcomes["no_mark"] += 1          # E6: the outcome class
        await _cancel_open_for(t, book, tg["refusal"])
        await _write_plan(t, book, r, None, None, drift.drift, book.get("his_level"),
                          tg["refusal"], {**plan, "kind": "no_plan"})
        return
    # THE PER-GAME CAP (E1, owner order 2026-09-06 "the per game cap is
    # at 2500 per game (never more)"): an INCREASE is sized again at
    # the room the game has left, AT COST (re-review MEDIUM-1). The
    # per-market cap keeps its at-the-mark reading in mi.target_shares
    # (cap / mark shares in TOTAL, unchanged); the GAME room is dollars
    # at cost, so the cap handed over for the room reading is the
    # shares already held valued AT THE MARK plus what the room leaves
    # after the book's own held cost -- the increase then costs at most
    # room - held_cost, and a mark that has fallen under the cost never
    # lets a book average down past the game's $2,500 at cost (the
    # re-review's X1: A 3,600 @ 0.49, B 1,400 @ 0.50, the mark at
    # 0.30 -- B's room is $736 and its increase $36, 120 sh, where the
    # first cut sized 736 / 0.30 = 2,453 in total and bought 1,053).
    # The same mi.target_shares scaling, so it is never refused; with
    # no room it is held at what the book has (`game_cap_full`;
    # `game_unreadable` when the game could not be read, named at the
    # read above); a reduce or a flatten is never touched
    # (rules.game_capped). The shadow is compared against the
    # ARITHMETIC figure at the cap this book was sized at (`arith`,
    # `cap_eff`), before the ledger floor, the sign flip and the short
    # share cap (E5 keeps comparing the unclamped target)
    held_cost = rules.book_exposure(book.get("ledger_net"), book.get("avg_cost"), r.mark,
                                    book.get("intent"), 0.0)
    arith, cap_eff = target, cap
    if not t.flatten_all and room < cap:
        room_tg = room_cap = None
        if room > 0 and held_cost is not None and r.mark is not None:
            px_mark = (1.0 - float(r.mark)) if short else float(r.mark)
            room_cap = round(abs(ledger) * px_mark + max(0.0, room - held_cost), 4)
        if room_cap is not None and room_cap > 0:
            room_tg = rules.mirror_target(book.get("ratio"), net, r.mark, MIRROR_ANCHOR_CLIP_USD,
                                          cap_usd=room_cap, allow_short=shorts)
            if room_tg.get("refusal"):
                room_tg = None          # the same inputs refused nothing above; belt and braces
        target, game_name = rules.game_capped(target, None if room_tg is None else room_tg["target"],
                                              ledger, short)
        if game_name is not None:
            if game_name == "game_cap_full" and game_exposure is None:
                game_name = "game_unreadable"      # counted once, at the read
            else:
                _mirror_stop(game_name, w)
            plan["game_cap"] = game_name
        if room_tg is not None and target == int(room_tg["target"]):
            arith, cap_eff = target, min(float(room_cap), cap)
    # what this book counts for against the later books of its game
    # (rule 4): what it holds at cost (`held_cost`, read above), plus
    # the larger of its resting increase and its new target's increase
    # at the mark (the rest, if it stands, IS that increase or part of
    # it; a reduce still holds until it sells, so it adds nothing and
    # subtracts nothing). An unreadable ledger, or a non-terminal order
    # the tick could not read (_resting_add_usd None), stays
    # unreadable: the later books read no room. Read BEFORE the sign
    # flip below, so a book about to flip counts its pre-flip target's
    # increase -- more counted than will stand, never less
    resting = _resting_add_usd(t, book)
    increase_sh = max(0, abs(int(target)) - abs(ledger))
    if held_cost is None or resting is None or (increase_sh and r.mark is None):
        t.game_sized[book["id"]] = None
    else:
        px = 0.0 if r.mark is None else (float(r.mark) if target >= 0 else 1.0 - float(r.mark))
        t.game_sized[book["id"]] = round(held_cost + max(resting, increase_sh * px), 4)
    # THE SIGN FLIP (brief B8, owner default Q5 (a)): his net has crossed
    # zero against this book. The book flattens under that name -- a
    # plan from a short ledger toward a positive target would run PAST
    # zero, the one sale the leg-space clamps forbid -- and the opposite
    # side opens as a NEW episode once this one has closed: flat, no
    # order open and the venue read at 0, the flip IS the close
    # (2026-09-06; no flat wait), and the one-open-per-market index
    # lets the next tick's candidate open the other side. The
    # shadow is compared against the UNCLAMPED target: it computes the
    # same signed figure from the same knob (E5)
    if rules.sign_flip(book.get("intent"), target):
        target = 0
        plan["sign_flip"] = True
        _mirror_stop("sign_flip", w)
    # THE SHORT SIDE'S SHARE CAP, after the flip and after the shadow's
    # figure is taken: the shadow computes the uncapped target
    target = _short_capped(t, target, w, plan)
    if target is not None and target != 0:
        plan.pop("flat_since", None)
    plan.update(target=target, target_raw=tg["raw"])
    await _shadow_check(t, book, arith, net, r.mark, shorts, cap_usd=cap_eff)
    # THE FREEZE: venue vs ledger + the desk's explained shares, one
    # signed subtraction on either sign (brief 3.1); since E5 the
    # operator register explains shares exactly as the desk's `manual`
    explained = ledger + r.manual + registered
    if abs(venue_int - explained) > mi.VENUE_LEDGER_TOL_SHARES:
        detail = {"venue": venue_int, "ledger": ledger, "manual": r.manual, "registered": registered}
        if r.venue != 0 and ledger != 0 and (r.venue < 0) != (ledger < 0):
            # SYMMETRIC (brief B5): the venue holds the OTHER sign of
            # what the book booked, on either book. On a short book it
            # is also the position-sign proof's negative verdict -- the
            # same tally the per-fill lane's echo writes, so one wrong
            # sign re-arms the short gate for every lane (H1) -- but
            # ONLY when the venue's magnitude is the leg's: that is the
            # genuine inversion (our BUY_SHORT booked as the long side).
            # Any other sign disagreement is a co-hold the ledger cannot
            # explain; it keeps the trip and the freeze here and never
            # touches the shared tally (P2 rung S0 review, sign lens)
            if short and abs(abs(r.venue) - abs(ledger)) <= mi.VENUE_LEDGER_TOL_SHARES:
                await le._record_short_proof(t.pool, ok=False, net=r.venue, slug=slug)
                plan["short_proof"] = "mismatch"
            await _trip_live_off(t, "wrong_sign_trip", {"book": book["id"], **detail})
            await _cancel_open_for(t, book, "wrong_sign_trip")
            await _freeze(t, book, "wrong_sign_trip", detail)
        else:
            await _cancel_frozen_open(t, book, "venue_ledger_disagree")
            if book.get("state") == "frozen":
                # E5 review v3, V3-2: a book already frozen (placement_lost:
                # the lost fill IS the disagreement) disagrees every tick
                # by the same reading; that reading sits on the plan's
                # detail, and is not a new event -- the tick's gauge and
                # the freeze's age move, the census and the recent list do not
                await t.pool.execute(_SQL_BOOK_FREEZE, book["id"], "venue_ledger_disagree")
                book["frozen_ticks"] = int(book.get("frozen_ticks") or 0) + 1
                book["last_reason"] = "venue_ledger_disagree"
                fr = t.stats["frozen_reasons"]
                fr[book["frozen_reason"]] = fr.get(book["frozen_reason"], 0) + 1
            else:
                await _freeze(t, book, "venue_ledger_disagree", detail)
            if int(book.get("frozen_ticks") or 0) > rules.MIRROR_FROZEN_NAME_TICKS:
                try:
                    await t.pool.execute(_SQL_STANDING_NAME, book["standing_row_id"])
                except Exception:  # noqa: BLE001 — the name is for a human, best-effort
                    log.warning("mirror_live: could not name the standing row of book %s",
                                book["id"], exc_info=True)
            # E5 / P2: the frozen book follows his exit, sized on the
            # venue's own position (never the ledger it disagrees with)
            # -- never on the transition tick (review F1: the walk ran
            # before step O booked this tick's fills, so a disagreement
            # that is one tick old may be that fill, not a lost response)
            if was_frozen:
                await _frozen_exit(t, book, r, target, fills, registered, plan)
            else:
                plan["frozen_exit"] = {"held": "transition_tick"}
        await _write_plan(t, book, r, target, tg["raw"], drift.drift, plan.get("his_level"),
                          book["frozen_reason"], {**plan, "kind": "frozen", **detail})
        return
    if book.get("state") == "frozen":
        if book["id"] in t.open_by_book:
            await _cancel_frozen_open(t, book, book.get("frozen_reason") or "frozen")
        if book["id"] not in t.open_by_book and book["id"] not in t.nonterminal:
            if registered == 0.0:
                # venue == ledger and nothing non-terminal: the book thaws
                await _thaw(t, book)
            else:
                # A REGISTERED BOOK NEVER THAWS (E5 review F3, owner's
                # option b): the register is an audit row and the third
                # term of the freeze comparison, nothing more. Thawed, the
                # book planned from its own ledger -- book 77 (ledger 0,
                # register 1,128) bought his net again on top of the
                # 1,128, and with him gone closed flat with the 1,128
                # unmanaged. Held frozen, the exit below sells the
                # registered shares toward his net exactly as any frozen
                # book's (the frozen seat is the venue less the desk's
                # manual shares alone)
                plan["registered_frozen"] = True
        if book.get("state") == "frozen":
            # E5 / P2: venue == ledger + the explained shares, but a
            # non-terminal row keeps the freeze -- the book still
            # follows his exit (the row's index refuses a placement
            # while it stands: `open_order_pending`, as any book)
            await _frozen_exit(t, book, r, target, fills, registered, plan)
            await _write_plan(t, book, r, target, tg["raw"], drift.drift, plan.get("his_level"),
                              book["frozen_reason"], {**plan, "kind": "frozen"})
            return
    # THE POSITION-SIGN PROOF (brief H1): venue == ledger on a short
    # book with both negative and nothing of the desk's beside them is
    # the venue saying our BUY_SHORT holds the short side -- the same
    # reading the per-fill lane's echo tallies, recorded ONCE per
    # episode into the same key the short gate reads
    if (short and ledger < 0 and r.venue < 0 and r.manual == 0 and registered == 0
            and prior_plan.get("short_proof") is None):
        await le._record_short_proof(t.pool, ok=True, net=r.venue, slug=slug)
        plan["short_proof"] = "ok"
    elif prior_plan.get("short_proof") is not None:
        plan["short_proof"] = prior_plan["short_proof"]
    # S4: the read-back probe, on the first live short book with a
    # two-sided quote while the proof is unproven (once an hour at most)
    if short:
        await _s4_probe(t, book, r)
    # INCREASES: mode, allowlist, the drift rule, the starred re-checks.
    # An increase is a move AWAY from zero on the book's own leg (P2
    # rung S0, brief C3 / G1): above the ledger on a long book, below it
    # on a short one
    inc_refusal = _increases_refusal(t, w)
    if inc_refusal is None and registered != 0.0:
        # E5 review F3 (option b): a book whose slug carries a register
        # row never grows -- the slug already holds the registered
        # shares beyond the ledger. Reachable only on a LIVE book with a
        # row written by hand (the preset registers frozen books alone,
        # and a registered book never thaws above); fail closed by name
        inc_refusal = "registered_no_increase"
    if inc_refusal is None and not drift.increase_ok:
        inc_refusal = drift.refusal or "snapshot_stale"
    increasing = (target < ledger) if short else (target > ledger)
    # his net moving DOWN in long-token terms (the same signed compare
    # on either book; _his_level reads the fills for it)
    reducing = target <= ledger
    his_px = _his_level(fills, la, oa, reducing, short=short)
    if inc_refusal is None and increasing:
        inc_refusal = await _increase_recheck(t, book, r, his_px)
    if inc_refusal is None and short and increasing:
        inc_refusal = await _short_open_refusal(t)
    # A CAPPED SHORT TARGET IS THE WHOLE PROBE: the plan's $5 dead band
    # stops churn on a book sized by his net, and a target clamped to
    # rules.MIRROR_SHORT_MAX_SHARES (ONE until rung S5) is under it by
    # construction; the band is not read on it, else the S3 probe could
    # never rest. mi.plan reads the mark for the band alone
    p = mi.plan(target, float(ledger), float(venue_int - r.manual - registered), mi.Book(r.bid, r.ask),
                his_px, None if plan.get("short_share_cap") is not None else r.mark)
    kind = None
    confirm_gone = None
    cancel_reason = None
    vanished = False
    # the token carrying his net: the OTHER token on a short book
    # (brief F3 / F5), so his leaving is confirmed on the leg we follow
    his_token = oa if (short and oa) else la
    action = rules.leg_action(book.get("intent"), p.side) if p.side else None
    if action == "add":
        kind = "increase"
        if inc_refusal:
            # the refusal is the name a resting order is cancelled
            # under (drift, snapshot_stale, the re-check's clause),
            # never the unlisted "no_plan" (step-9 review)
            _mirror_stop(inc_refusal, w)
            cancel_reason = inc_refusal
            p = None
    elif action == "reduce":
        if target != 0:
            kind = "reduce"
        elif t.flatten_all:
            kind = "flatten_vanished"
        else:
            if r.his_long <= 0 and r.his_other <= 0:
                confirm_gone = await _confirm_gone(t, w, his_token)
            kind = rules.select_flatten(target, r.his_long, r.his_other, r.fresh_read,
                                        r.snap_long, r.snap_other, r.market_live, confirm_gone,
                                        r.snap_partial)
            if kind == "vanish_unconfirmed":
                _mirror_stop("vanish_unconfirmed", w)
                kind = "flatten_paired"
    else:
        _mirror_stop(rules.plan_reason_key(p.reason), w)
        if (target == 0 and abs(ledger) < FLAT_TOL_SHARES
                and r.his_long <= 0 and r.his_other <= 0):
            # flat at target 0 with fills reading him gone: the vanish
            # is confirmed by the same rule the SELL tick used, so the
            # episode closes on it (spec 1c) instead of waiting out the
            # flat hour (step-9 review); unconfirmed is not vanished
            confirm_gone = await _confirm_gone(t, w, his_token)
            vanished = rules.select_flatten(target, r.his_long, r.his_other, r.fresh_read,
                                            r.snap_long, r.snap_other, r.market_live,
                                            confirm_gone, r.snap_partial) == "flatten_vanished"
    if kind == "flatten_vanished":
        # the vanish's own clock (the flatten rest reference reads it):
        # carried while the book stays in the vanish, reset the tick it
        # re-enters (step-9 review)
        since = _num(prior_plan.get("vanish_since")) if prior_plan.get("kind") == "flatten_vanished" else None
        plan["vanish_since"] = t.now if since is None else since
    plan.update(kind=kind, side=(p.side if p else None), qty=(p.qty if p else 0),
                price=(p.price if p else None), his_level=his_px,
                reason=(p.reason if p else inc_refusal))
    reason = p.reason if p else (inc_refusal or "no plan")
    try:
        if kind == "flatten_vanished" and p is not None:
            vanished = True
            reason = await _flatten_vanished(t, book, r, p, his_px, plan)
        else:
            reason = await _act(t, book, r, p, kind, his_px, plan, cancel_reason) or reason
    finally:
        why = await _maybe_close_episode(t, book, r.market_live, vanished, target, plan,
                                         venue_flat=abs(float(r.venue or 0.0)) < FLAT_TOL_SHARES)
        plan["close"] = why
        # E6: the book's outcome class and its quiet verdict -- ON TARGET
        # with nothing placed and nothing open is the one quiet verdict;
        # every other exit of this function leaves the book hot
        placed = book["id"] in t.placed_books
        on_target = (rules.plan_reason_key(reason) == "on_target" and not placed
                     and not _book_open(t, book) and book.get("state") == "live")
        if placed:
            t.outcomes["placed"] += 1
        elif on_target:
            t.outcomes["on_target"] += 1
        elif reason == "no_mark":
            t.outcomes["no_mark"] += 1
        _note_read(t, book, on_target)
        if reason != "tick_abandoned":
            # a decided order refused because another book abandoned
            # the tick mid-flight (E2 review, LOW-6) writes NO plan: the
            # write bumps updated_at, and E1's walk reads a bumped book
            # as reached -- this one was not, and walks first next tick
            await _write_plan(t, book, r, target, tg["raw"], drift.drift, his_px, reason, plan)


# THE MIRROR LANE'S CELL VERDICT (2026-09-06 20:0xZ). The copy lane's
# cell gate (copy_sports.copy_verdict) carries a SOCCER/ESPORTS PRICE
# FLOOR (SOCCER_PRICE_FLOOR 0.40: the copy lane never copied a soccer
# or esports pick under 0.40). The mirror is a different lane under a
# different order -- Matt, 2026-09-06 ~14:00Z: "Just trade 10% of what
# he puts on everything he takes ... this limitation should never force
# us to decline any of the possible copies"; 19:33Z: "I need more trades
# firing in the mirror sleeve! We need to be mirroring a larger
# percentage of RN1s positions" -- so the floor does not bind a mirror
# book: it is lifted here BY NAME and counted (`soccer_floor_lifted`),
# and every other clause (no_whale, whale_paused, sport_halted,
# market_type_blocked, the cells, the entry band) refuses exactly as
# before. Found because the first four soccer books C1 opened (23-26,
# 19:42Z) were refused `cell_gate_soccer_price_floor` on every tick.
_CELL_LIFTED = frozenset({"soccer_price_floor", "soccer_price_unreadable"})


def _mirror_cell(whale: str, his_slug: str | None, price: float | None) -> str | None:
    """copy_sports.copy_verdict for the mirror lane: the soccer/esports
    price floor is lifted by name (owner order above), all else stands."""
    clause = copy_sports.copy_verdict(whale, str(his_slug or ""), price=price)
    if clause in _CELL_LIFTED:
        _mirror_stop("soccer_floor_lifted", whale)
        return None
    return clause


async def _increase_recheck(t: _Tick, book: dict, r: _Reading,
                            his_px: float | None = None) -> str | None:
    """The starred admission clauses on every INCREASE (spec A): clip,
    mapping, edge, cell -- read now, never remembered from open.

    THE PRICE THE CELL READS IS THE TICK'S OWN LEVEL (2026-09-06): the
    recheck read `book["his_level"]`, a column _SQL_BOOK_COLS never
    selects, so the cell gate saw price None on every increase and
    refused every soccer and esports book `soccer_price_floor` for the
    life of the book (books 20, 23-26). The caller hands the level it
    just read from his fills; None stays None (and the floor is lifted
    for the mirror anyway, _mirror_cell)."""
    ok, why = await _admit_source(t, r.whale, book.get("map_source"), r.slug)
    edge_ok, edge_why = edge_gate.verdict(r.whale)
    his_slug = next((f.get("market_slug") for f in r.fills if f.get("market_slug")), None)
    clause = _mirror_cell(r.whale, his_slug, his_px)
    facts = rules.AdmissionFacts(increases_ok=True, per_fill_usd=le.per_fill_usd(r.whale, r.slug),
                           mapping_ok=bool(ok), mapping_why=why, edge_ok=bool(edge_ok),
                           edge_why=edge_why, cell_ok=clause is None, cell_clause=clause)
    return rules.admission(facts, increase=True)


def _short_capped(t: _Tick, target: int | None, whale: str, plan: dict | None = None) -> int | None:
    """rules.MIRROR_SHORT_MAX_SHARES on a NEGATIVE target (P2 rung S0,
    S3 expressibility): the target is clamped toward zero at the cap
    and `short_share_cap` is named when it bites -- a cap of 0 makes
    every short target 0, the P1 reading under this name. A target at
    or above zero is never touched, so a long book is byte-identical."""
    if target is None or target >= 0:
        return target
    # unbounded (the default since 2026-09-06) never clamps; a finite
    # cap, lowered from the environment, clamps and is named; an
    # unreadable cap reads as 0 -- shut -- through rules._bounded
    cap = rules._bounded(rules.MIRROR_SHORT_MAX_SHARES)
    if cap is None or -target <= cap:
        return target
    _mirror_stop("short_share_cap", whale)
    if plan is not None:
        plan["short_share_cap"] = cap
    return -cap


async def _short_open_refusal(t: _Tick) -> str | None:
    """The two doors in front of EVERY short open -- a new book, an
    add, a take (P2 rung S0, brief H1 / H2) -- read now, never
    remembered: `short_model_disarmed` when the executor's short cost
    model is switched off (LIVE_SHORT_COST_MODEL=off silently inverts
    the wire, the cash and the P&L sign at once; the mirror refuses
    rather than inherit it), then the executor's own `_short_gate`
    (fails closed on an unreadable tally, refuses on one mismatch, and
    on a probation short still in flight on the per-fill lane) as
    `short_gate_refused`. The gate's serialising lock is NOT taken: the
    mirror has no echo to release it, and a lock that leaks is the ban
    by another name; the mirror's own sign read is recorded into the
    same tally by _tick_book."""
    if not le.short_model_confirmed():
        return "short_model_disarmed"
    try:
        ok, why, _probation = await le._short_gate(t.pool)
    except Exception:  # noqa: BLE001 — an unreadable gate is a shut one
        ok, why = False, "short gate unreadable"
    if not ok:
        log.warning("mirror_live: short open held: %s", why)
        return "short_gate_refused"
    return None


def _exit_or_flip(book: dict, p: mi.Plan | None, o: dict) -> bool:
    """Is the plan against the resting order `o` an EXIT (a reduce or
    a flatten on the book's leg) or a SIDE CHANGE (the plan's side is
    not the rest's)? Either is never gated by the hour's replace budget
    (E2 review, MEDIUM-2a): the budget bounds entry churn, and an exit
    held behind it is the reversal path delayed by up to a rest's TTL.
    A plan with no side is neither.

    THE FLIP'S ADD HALF IS EXEMPT ON PURPOSE (review round 2, LOW-e): a
    BUY plan over a SELL rest -- he came back after reducing -- cancels
    the stale SELL whatever the count, because the rest on the wire is
    on the WRONG side of what the book wants and a side change is not
    the re-quote churn the budget exists to bound; the BUY that follows
    is a placement, bounded by the ops budget and the room as ever."""
    if p is None or not isinstance(p.side, str):
        return False
    if rules.leg_action(book.get("intent"), p.side) != "add":
        return True
    return p.side != o.get("side")


def _cancel_outcome(t: _Tick, book: dict, o: dict, res: str) -> str | None:
    """After a take's or a replace's cancel of the standing rest: None
    when the rest is gone and terminal, so the order that follows (the
    IOC, the re-rest) may go; otherwise THE NAME THE PLAN READS, never
    'take' (E4 review round 3, L-4: a cancel the budget refused left the
    rest standing and the plan said 'take' with nothing sent). A cancel
    refused before it went out leaves the rest in open_by_book:
    `cancel_refused:<reason>` (`ops_capped` -- unreachable on an exit,
    which is exempt (M-1), reachable on an entry at the cap). A cancel
    whose reads left the order non-terminal froze the book
    `cancel_pending`: that name, as the replace path always read it."""
    if book["id"] in t.open_by_book:
        return f"cancel_refused:{res}"
    if o["state"] not in ("filled", "cancelled", "expired"):
        return "cancel_pending"
    return None


# ------------------------------------ E5 / P2: frozen books follow his exits
#
# A book frozen `placement_lost` or `venue_ledger_disagree` cancelled its
# open orders every tick, wrote a plan of kind 'frozen' and returned: it
# never followed his exit. He sells, we hold to settlement -- a direct
# P&L divergence on markets mapped correctly (owner question 2026-09-07
# 16:5xZ; heartbeat 16:42Z: 8 books frozen, book 77 venue 1,128 / ledger
# 0). Under these rules a frozen book plans and places REDUCING orders
# only, sized on THE VENUE'S OWN POSITION -- never the ledger it
# disagrees with, never the plan's target -- toward his net exactly as
# a live book would: mi.plan with the venue in the ledger's seat (the
# same dead band and hysteresis), _act's E4 exit at his price within
# the cent (the rest at his cent, the take the tick the bid is there),
# a short book's cover through _act's S4 gate. Never an increase, never
# a flip, never a new episode; `books_live` does not count it; a reduce
# that fills books through the existing fill path (_book_fill names the
# part past the ledger `frozen_excess_sold` on the row's receipt, inside
# the venue's own reading, never the overfill trip); at venue 0 the book
# thaws on the next agreement read and closes as any flat book does.
# Every refusal is named (the CENSUS_KEYS paragraph). rules
# .MIRROR_FROZEN_EXITS=off turns the whole clause off (a knob may only
# LOWER a rail): the frozen book then does nothing but read, as before.

async def _slug_coheld(t: _Tick, slug: str) -> bool | None:
    """Is a LIVE non-mirror, non-manual row standing on the slug
    (_SQL_SLUG_COHELD)? None when the read failed or answered nothing
    a bool can be made of: the venue's number cannot be attributed."""
    try:
        v = await t.pool.fetchval(_SQL_SLUG_COHELD, slug)
    except Exception as exc:  # noqa: BLE001 — unreadable: not attributable
        log.warning("mirror_live: co-hold read for %s failed (%s)", slug, type(exc).__name__)
        return None
    return None if v is None else bool(v)


def _plan_seat(book: dict, r: _Reading, plan: dict) -> tuple[float, float]:
    """(the ledger seat, the venue seat) mi.plan is handed for this
    book: on a frozen exit the venue's own position in BOTH seats (the
    book's `_frozen_venue`, set for the act by _frozen_exit), else the
    ledger and the venue less the desk's `manual` and the register's
    shares as the plan read them (sign-checked in _tick_book)."""
    fv = _num(book.get("_frozen_venue"))
    if fv is not None:
        return float(int(fv)), float(int(fv))
    ledger = float(int(book.get("ledger_net") or 0))
    reg = float(_num(plan.get("registered")) or 0.0)
    return ledger, float(int(r.venue) - r.manual - reg)


def _frozen_reduce_stands(book: dict, o: dict) -> bool:
    """May this order row STAND on a frozen book (E5 / P2)? Only a row
    the frozen exit itself placed (reason `frozen_reduce`: sized on the
    venue's own position) that shrinks the leg, on a book frozen under
    one of FROZEN_EXIT_REASONS, with the knob on. Any other row on a
    frozen book -- an add, a live reduce sized on a ledger the venue
    disagrees with, any row under another freeze -- is cancelled under
    the freeze's name as before."""
    if not rules.MIRROR_FROZEN_EXITS or book.get("state") != "frozen":
        return False
    if book.get("frozen_reason") not in FROZEN_EXIT_REASONS:
        return False
    if _order_action(o, book) != "reduce":
        return False
    return str(o.get("reason") or "").startswith("frozen_reduce")


async def _cancel_frozen_open(t: _Tick, book: dict, reason: str) -> None:
    """The freeze's cancel of the book's standing rest, except a frozen
    reduce that stands (_frozen_reduce_stands): that rest is the frozen
    exit's to keep, replace or take this tick, through _act."""
    ent = t.open_by_book.get(book["id"])
    if ent is None:
        return
    o, _st = ent
    if _frozen_reduce_stands(book, o):
        return
    await _cancel_and_settle(t, o, book, reason)


def _adopt_reason(o: dict, text: str) -> str:
    """The reason a row is written with when its reason moves -- an
    adoption, a terminal state, an 'unknown' cancel: the frozen exit's
    marker survives it (`frozen_reduce: <text>`), so a frozen reduce
    whose response was lost and then found by fingerprint, or whose
    cancel left it 'unknown', books its fill under the same rule as one
    placed cleanly, and the `mirror-frozen` preset finds the finished
    row by its marker."""
    if str(o.get("reason") or "").startswith("frozen_reduce"):
        return f"frozen_reduce: {text}"
    return text


def _frozen_venue_own(r: _Reading) -> int:
    """The book's OWN position as the venue reports it: the slug's
    signed net less the desk's `manual` shares (explained, the desk's
    to manage), whole shares. THE REGISTER IS NOT SUBTRACTED (E5 review
    F3, option b): registered shares are the book's to exit toward his
    net -- the register explains them to the freeze comparison, it does
    not hand them to anyone else."""
    return int(r.venue) - int(round(float(r.manual or 0.0)))


async def _lost_bound(t: _Tick, book: dict) -> int | None:
    """What a placement_lost book can explain holding on the venue (E5
    review M1): its leg plus the quantity its own lost rows asked for
    (_SQL_LOST_QTY). None when the rows could not be read."""
    try:
        rows = await t.pool.fetch(_SQL_LOST_QTY, book["id"])
    except Exception as exc:  # noqa: BLE001 — unreadable: no bound is known
        log.warning("mirror_live: lost rows of book %s unreadable (%s)", book["id"], type(exc).__name__)
        return None
    if rows is None:
        return None
    # V3-1: a lost row that REDUCES the leg (a lost close) explains no
    # surplus; only what the book asked to add can sit on the venue unbooked
    v = sum(float(r["qty"] or 0.0) for r in rows if _order_action(dict(r), book) == "add")
    return _leg_of(book) + int(math.ceil(v))


async def _frozen_refuse(t: _Tick, book: dict, name: str, plan: dict, verdict: dict,
                         whale: str, cancel: bool = True) -> str:
    """A frozen exit's refusal past the eligibility and knob checks:
    counted, on the plan, and -- E5 review F4 -- a standing frozen
    reduce the plan no longer wants is cancelled under the refusal's
    name (an exit's cancel: never `ops_capped`). A live book replaces a
    rest its plan no longer wants; a frozen one may not keep selling at
    his old exit cent while he buys. `cancel=False` (E5 review v3, V3-3)
    is the read that FAILED this tick -- the venue, his market, the
    co-hold: the plan is unknown, not unwanted, and the standing reduce
    keeps standing (as `frozen_fill_this_tick` already keeps it)."""
    _mirror_stop(name, whale)
    plan["frozen_exit"] = {**verdict, "held": name}
    ent = t.open_by_book.get(book["id"])
    if cancel and ent is not None and _frozen_reduce_stands(book, ent[0]):
        await _cancel_and_settle(t, ent[0], book, name, exit=True)
        plan["frozen_exit"]["cancelled"] = ent[0]["id"]
    return name


async def _frozen_exit(t: _Tick, book: dict, r: _Reading, target: int | None, fills: list,
                       registered: float, plan: dict) -> str | None:
    """The frozen book's reduce toward his net (E5 / P2), or the named
    reason it placed nothing. Returns _act's word when a plan reached
    it, else the refusal's name; the plan's `frozen_exit` carries the
    verdict with the venue-side position and the target."""
    w, slug = r.whale, r.slug
    short = _book_short(book)
    reason = book.get("frozen_reason")
    if reason not in FROZEN_EXIT_REASONS:
        plan["frozen_exit"] = {"held": "reason_not_eligible", "reason": reason}
        return None
    if not rules.MIRROR_FROZEN_EXITS:
        # (step O already cancelled any standing frozen reduce under the
        # freeze's name: _frozen_reduce_stands reads the knob)
        _mirror_stop("frozen_exits_off", w)
        plan["frozen_exit"] = {"held": "frozen_exits_off"}
        return "frozen_exits_off"
    if book["id"] in t.filled_books:
        # E5 review F1 / F2: a fill was booked on this book after the
        # walk (step O, or the walk's own cancel-settle), so r.venue is
        # stale by it: nothing is sized this tick. A standing frozen
        # reduce keeps standing -- its leaves and the venue moved in
        # step, and it is still the exit at his cent
        _mirror_stop("frozen_fill_this_tick", w)
        plan["frozen_exit"] = {"held": "frozen_fill_this_tick"}
        return "frozen_fill_this_tick"
    if t.positions is None or r.venue is None:
        return await _frozen_refuse(t, book, "frozen_venue_unread", plan, {"why": "venue_positions"}, w, cancel=False)
    if r.snap_market_fresh is not True:
        # his side not read for THIS market this tick (unreadable,
        # capped, no ids, stale): the exit follows a net nobody read
        return await _frozen_refuse(t, book, "frozen_venue_unread", plan, {"why": "his_market_read"}, w, cancel=False)
    coheld = await _slug_coheld(t, slug)
    if coheld is None:
        return await _frozen_refuse(t, book, "frozen_venue_unread", plan, {"why": "coheld_unreadable"}, w, cancel=False)
    if coheld:
        return await _frozen_refuse(t, book, "frozen_coheld", plan, {}, w)
    venue_own = _frozen_venue_own(r)
    tgt = int(target or 0)
    verdict: dict[str, Any] = {"venue_own": venue_own, "target": tgt}
    if (venue_own <= 0) if not short else (venue_own >= 0):
        return await _frozen_refuse(t, book, "frozen_venue_flat", plan, verdict, w)
    if (tgt < 0) if not short else (tgt > 0):
        # the sign flip flattened the target to 0 above; a target
        # against the leg cannot reach here -- belt and braces
        return await _frozen_refuse(t, book, "frozen_reduce_only", plan, verdict, w)
    if reason == "placement_lost":
        # E5 review M1: a placement_lost book sells no more than it can
        # explain -- its leg plus its own lost rows' quantity; a surplus
        # past that is someone else's (a venue_ledger_disagree book has
        # no such bound: the disagreement itself is what it holds)
        bound = await _lost_bound(t, book)
        if bound is None:
            return await _frozen_refuse(t, book, "frozen_venue_unexplained", plan,
                                        {**verdict, "why": "lost_rows_unreadable"}, w)
        if abs(venue_own) > bound:
            return await _frozen_refuse(t, book, "frozen_venue_unexplained", plan,
                                        {**verdict, "bound": bound}, w)
    if (tgt >= venue_own) if not short else (tgt <= venue_own):
        # he has not exited under what the venue holds for us: read
        # only -- and a rest placed on an earlier exit of his is
        # cancelled (F4: he came back)
        return await _frozen_refuse(t, book, "frozen_no_his_exit", plan, verdict, w)
    la, oa = book["long_asset"], book.get("other_asset")
    # his level as the live path reads it: `reducing` is his net moving
    # DOWN in long-token terms (target <= the seat), which a short
    # book's cover is not (_his_level reads his buy-back for it)
    his_px = _his_level(fills, la, oa, tgt <= venue_own, short=short)
    plan["his_level"] = his_px
    # the venue in the ledger's seat: the same plan a live book makes,
    # from what the venue holds toward his net
    p = mi.plan(tgt, float(venue_own), float(venue_own), mi.Book(r.bid, r.ask), his_px,
                None if plan.get("short_share_cap") is not None else r.mark)
    plan.update(side=p.side, qty=p.qty, price=p.price, reason=p.reason)
    if p.side is None:
        await _frozen_refuse(t, book, rules.plan_reason_key(p.reason), plan, verdict, w)
        return p.reason
    if rules.leg_action(book.get("intent"), p.side) != "reduce":
        # never an increase, never a flip, never sent (belt and braces)
        return await _frozen_refuse(t, book, "frozen_reduce_only", plan, {**verdict, "side": p.side}, w)
    plan["frozen_exit"] = {**verdict, "side": p.side, "qty": p.qty}
    book["_frozen_venue"] = venue_own
    try:
        res = await _act(t, book, r, p, "reduce", his_px, plan)
    finally:
        book.pop("_frozen_venue", None)
    plan["frozen_exit"]["result"] = res
    if res in ("rest_placed", "take", "filled_at_create"):
        _mirror_stop("frozen_reduce", w)
        _recent(book["id"], "frozen_reduce", side=p.side, qty=p.qty, venue_own=venue_own,
                target=tgt, result=res)
    return res


async def _act(t: _Tick, book: dict, r: _Reading, p: mi.Plan | None, kind: str | None,
               his_px: float | None, plan: dict, cancel_reason: str | None = None) -> str | None:
    """Step X for a live book: keep / cancel-replace the resting
    order, place the rest, or fire the take. `cancel_reason`
    is the increase refusal a BUY plan was refused under; a tick that
    tripped mid-way (t.cancel_all) cancels the resting order under the
    trip's name and places nothing.

    THE EXIT AT HIS PRICE (E4, owner order 2026-09-06 ~23:24Z: "we
    should exit when he exits at his price or within 1c variance
    (tolerance)"). Every exit of a LONG book -- a reduce, the paired
    flatten, the vanish flatten, the sign-flip flatten -- with a price
    of his (`_exit_terms`, rules.exit_terms off `his_px`) is priced off
    HIM alone: the rest at the cent ceil(his price), post-only, never
    lifted to the ask; the take ONE IOC at the lowest cent at or above
    his price less rules.MIRROR_EXIT_TOL, fired on the SAME tick the
    bid is at or through that cent, with no wait, whether a rest stands
    (cancelled first) or not. Outside the tolerance NOTHING chases: the
    rest stands (`exit_out_of_tol`, the plan carries bid/ask/floor),
    and a rest past its TTL at the same cent is not cancelled and
    re-placed (`requote_same_wire`: keep_or_replace's `stands`). An
    exit is exempt from the replace budget (rule 4) and, as before,
    from every increase refusal (rule 5), and since review round 3
    (M-1) from the tick's OPS BUDGET too: every cancel, IOC and rest on
    an exit's path takes its slot whatever the count (_op_slot's
    `exit`; _place reads it off the side's leg action), so the take at
    the budget's last op can no longer cancel the rest and shed the
    IOC. A cancel that IS refused (an entry's at the cap) names the
    plan `cancel_refused:<reason>`, never 'take' (_cancel_outcome,
    L-4). A short book's cover is priced by the same terms in
    _flatten_send (a ceiling); a short's partial reduce stays
    `short_reduce_unproven`. He gave no exit price (a snapshot-driven
    reduce, a vanish with no fill of his, the admin flatten): today's
    behaviour under `exit_px_src: 'none'`.

    THE ENTRY TAKES FIRST (E4 addendum, ~23:38Z: "Remove the 20 second
    wait on entires too"): rules.MIRROR_TAKE_AFTER_S is 0 by default,
    so an increase with the ask at or through his level (the entry
    price rule, unchanged: never above him, no tolerance) sends ONE IOC
    at the wire for the plannable quantity FIRST (`take_first`) and
    rests the remainder post-only at the same wire; a rest already
    standing is cancelled and taken the tick the ask arrives, with no
    age condition. Under a LENGTHENED wait (env) the rest-first take of
    E2 is back exactly as it was, arm bounds included."""
    w = r.whale
    short = _book_short(book)
    intent = _book_intent(book)
    ex = _exit_terms(t, book, p.side if p is not None else None, his_px, plan)
    wire = _wire_for(p, his_px, r, book.get("intent"), ex)
    is_exit = p is not None and rules.leg_action(book.get("intent"), p.side) == "reduce"
    # the exit rule's two prices on a LONG book's SELL (floor / rest /
    # take) and, since S4, on a SHORT book's BUY -- the cover, a priced
    # order through this same path: the rest at floor(his) to the
    # cent, the IOC at the ceiling cent whenever the ask is at or under
    # it, held outside; gated by the read-back proof (_s4_refusal)
    long_exit = ex is not None and not short
    short_exit = ex is not None and short and is_exit
    priced_exit = long_exit or short_exit
    # THE ENTRY'S TAKE CENT IS HIS CENT (E4 review, HIGH-1). An entry's
    # rest sits at buy_price(his, bid) -- floor-to-cent of min(his, bid),
    # at or under the bid -- and a book with bid < ask is never "at or
    # through" its own rest, so a take judged at the rest's wire fired
    # only in a locked book (placed_take 0 all night). The take's
    # trigger and the IOC's limit are HIS cent, buy_wire(his): floored,
    # never above him, and the ask at or under it IS at or through his
    # level. The rest keeps its wire, keep_or_replace compares the
    # rest's wire. A short book's add stays on the rest path (its wire
    # is _short_wire's contract cent and no take-first is built for it);
    # a long exit's take-at-wire with no price of his is E2's, as before
    take_lvl = wire
    if p is not None and p.side == BUY and not short:
        lvl = rules.buy_wire(his_px)
        if lvl is not None:
            take_lvl = lvl
    ent = t.open_by_book.get(book["id"])
    if ent is not None:
        o, st = ent
        leaves = _num(st.get("leaves"))
        if leaves is None:
            leaves = float(o["qty"]) - float(o.get("booked_filled") or 0.0)
        oo = rules.OpenOrder(o["side"], _num(o.get("wire")), int(o["qty"]), leaves,
                       _num(o.get("placed_ts")), _order_intent(o, book))
        want = rules.wire_side(book.get("intent"), p.side) if p is not None else None
        decision = rules.keep_or_replace(oo, p, t.now, cancel_reason=(t.cancel_all or cancel_reason),
                                         wire=wire, intent=(want[0] if want else None),
                                         stands=priced_exit)
        if decision == "keep":
            _mirror_stop("open_order_pending", w)
            plan["open_order"] = o["id"]
            if long_exit:
                # THE EXIT'S TAKE off the standing rest (E4): the bid at
                # or through the take cent -- inside the tolerance of his
                # price -- cancels the rest and sends the one IOC there,
                # this tick, whatever the rest's age and whatever the
                # replace budget says (an exit is never blocked by it)
                if rules.at_or_through(SELL, r.bid, r.ask, ex["take"]):
                    res = await _cancel_and_settle(t, o, book, "take", exit=True)
                    named = _cancel_outcome(t, book, o, res)
                    if named is not None:
                        return named
                    left = _sell_qty(book, int(min(p.qty, max(0.0, float(o["qty"])
                                                              - float(o.get("booked_filled") or 0.0)))))
                    if left >= 1:
                        return await _place(t, book, r, "take", SELL, ex["take"], left, his_px, p,
                                            plan, tif="IOC")
                    return "take"
                # outside the cent: the rest stands at his cent, and a
                # rest past its TTL at the same cent is the no-op the
                # TTL re-quote would have been (rule 3)
                _exit_held(t, r, ex, plan, w)
                if t.now - float(o["placed_ts"]) >= float(rules.MIRROR_REST_TTL_S):
                    _mirror_stop("requote_same_wire", w)
                    plan["requote_same_wire"] = True
                return "open_order_pending"
            if short_exit:
                # THE COVER'S TAKE off its standing rest (S4): the ask
                # at or under the ceiling cent cancels the rest and
                # sends the one IOC there, this tick; outside it the
                # rest stands at floor(his) and nothing chases
                refusal = _s4_refusal(t, book, kind, plan, w)
                if refusal is not None:
                    return refusal
                if rules.at_or_through(BUY, r.bid, r.ask, ex["cover"]):
                    res = await _cancel_and_settle(t, o, book, "take", exit=True)
                    named = _cancel_outcome(t, book, o, res)
                    if named is not None:
                        return named
                    left = _cover_qty(book, int(min(p.qty, max(0.0, float(o["qty"])
                                                               - float(o.get("booked_filled") or 0.0)))))
                    if left >= 1:
                        return await _place(t, book, r, "take", BUY, ex["cover"], left, his_px, p,
                                            plan, tif="IOC")
                    return "take"
                _exit_held(t, r, ex, plan, w)
                if t.now - float(o["placed_ts"]) >= float(rules.MIRROR_REST_TTL_S):
                    _mirror_stop("requote_same_wire", w)
                    plan["requote_same_wire"] = True
                return "open_order_pending"
            # THE TAKE off a rest at his level (E2; no wait since the E4
            # addendum, unless the environment lengthened it). The
            # rest's OWN age is the wait: the arm a post-only 400 set
            # is for the no-rest case below, and every placed rest or
            # finished order clears it (_disarm_take)
            if (p is not None and rules.take_allowed(t.now - float(o["placed_ts"]), None, t.now,
                                                     r.bid, r.ask, take_lvl, p.side)):
                # THE TAKE SPENDS THE REPLACE BUDGET BEFORE IT CANCELS.
                # Counting the take's cancel (_SQL_REPLACES) bounds the
                # re-quotes that follow a take, but the take itself
                # never passed through the replace branch: the rest it
                # leaves behind is placed on the no-order path below,
                # so rest -> wait -> take -> rest ran on with the
                # budget spent and nothing refusing it. The same read
                # the replace branch makes, refused under its own name
                # and the rest kept standing, as replace_capped keeps
                # it (adversarial pre-flight 2026-09-05, before the
                # owner's "switch on the mirror system 100%" order).
                # AN EXIT IS NEVER BUDGET-GATED (E2 review, MEDIUM-2a;
                # E4 rule 4 is this same exemption): a take that reduces
                # or flattens, or a plan on the other side of the rest,
                # goes out whatever the hour's count -- the budget
                # bounds ENTRY churn only
                if (not _exit_or_flip(book, p, o)
                        and await _requotes_this_hour(t, book) >= rules.MIRROR_MAX_REPLACES_PER_HOUR):
                    _mirror_stop("take_capped", w)
                    return "take_capped"
                # the book is at his level (E2): the one IOC at the same
                # wire follows the cancel
                _mirror_stop("take_at_his_level", w)
                res = await _cancel_and_settle(t, o, book, "take", exit=is_exit)
                named = _cancel_outcome(t, book, o, res)
                if named is not None:
                    return named
                left = int(min(p.qty, max(0.0, float(o["qty"]) - float(o.get("booked_filled") or 0.0))))
                if left >= 1:
                    if is_exit:
                        return await _place(t, book, r, "take", p.side, wire, left, his_px, p,
                                            plan, tif="IOC")
                    # an entry: the IOC at his cent, then the unfilled
                    # part rests again at the wire (addendum 4)
                    return await _entry_take(t, book, r, p, take_lvl, wire, left, his_px, plan,
                                             first=False)
                return "take"
            if (p is not None and t.now - float(o["placed_ts"]) >= float(rules.MIRROR_TAKE_AFTER_S)
                    and not rules.at_or_through(p.side, r.bid, r.ask, take_lvl)):
                # the wait elapsed and the market never came to him:
                # held under target, never chased (critic C15); the
                # take's price verdict by its own name (E2)
                _mirror_stop("resting_above_level", w)
                _mirror_stop("take_refused_price", w)
            return "open_order_pending"
        if decision == "replace":
            # a SIDE CHANGE or an exit plan replaces whatever the hour's
            # count (E2 review, MEDIUM-2a; E4 rule 4 is this same
            # exemption -- an exit's rest is never held at the old cent
            # by the count): with the budget spent by take cycles, a BUY
            # rest stood over a SELL plan until its TTL
            if (not _exit_or_flip(book, p, o)
                    and await _requotes_this_hour(t, book) >= rules.MIRROR_MAX_REPLACES_PER_HOUR):
                _mirror_stop("replace_capped", w)
                return "replace_capped"
            # an exit plan's replace -- its rest moving to his cent, or
            # a BUY rest standing over the reduce -- is never
            # `ops_capped` (M-1); a refused entry replace is named (L-4)
            res = await _cancel_and_settle(t, o, book, "replace", exit=is_exit)
            named = _cancel_outcome(t, book, o, res)
            if named is not None:
                return named
            # re-plan against the ledger the cancel's booking left (or,
            # on a frozen exit, the venue's own position: _plan_seat)
            target = int(plan.get("target") or 0)
            seat_ledger, seat_venue = _plan_seat(book, r, plan)
            p = mi.plan(target, seat_ledger, seat_venue,
                        mi.Book(r.bid, r.ask), his_px,
                        None if plan.get("short_share_cap") is not None else r.mark)
            if p.side is None:
                _mirror_stop(rules.plan_reason_key(p.reason), w)
                return p.reason
            ex = _exit_terms(t, book, p.side, his_px, plan)
            wire = _wire_for(p, his_px, r, book.get("intent"), ex)
            is_exit = rules.leg_action(book.get("intent"), p.side) == "reduce"
            long_exit = ex is not None and not short
            short_exit = ex is not None and short and is_exit
            take_lvl = wire
            if p.side == BUY and not short and rules.buy_wire(his_px) is not None:
                take_lvl = rules.buy_wire(his_px)
            if (rules.leg_action(book.get("intent"), p.side) == "add"
                    and (kind != "increase" or _increases_refusal(t, w))):
                return "no plan after replace"
        else:
            # a named cancel: the plan is no order, or has no price, or
            # the tick tripped (on an exit plan's path: exempt, M-1)
            await _cancel_and_settle(t, o, book, decision, exit=is_exit)
            if p is None or p.side is None or t.cancel_all:
                return decision
            if decision == "no_price":
                _mirror_stop("no_price", w)
                return decision
    if p is None or p.side is None:
        return None
    if book["id"] in t.nonterminal:
        # a row of this book is still non-terminal (a lost placement,
        # an unknown cancel): the partial unique index would refuse the
        # INSERT below, and the name is the same
        _mirror_stop("open_order_pending", w)
        return "open_order_pending"
    if wire is None:
        _mirror_stop("no_price", w)
        if short and is_exit:
            # S4: a cover with no buy-back price of his is HELD, named
            # (exit_px_src 'none' is on the plan), never a guessed level
            plan["cover"] = "unpriced_held"
        return "no_price"
    if long_exit:
        # THE EXIT WITH NO REST STANDING (E4): the bid at or through the
        # take cent sends the one IOC there now -- no wait, no arm, no
        # MIN_MOVE_FRAC test, and a partial fill leaves NOTHING resting
        # (the next tick plans again: the bid still there is another
        # take, gone is the rest); outside the cent the rest goes at
        # his cent below, held by name
        if rules.at_or_through(SELL, r.bid, r.ask, ex["take"]):
            qty = _sell_qty(book, p.qty)
            if qty < 1:
                _mirror_stop("under_one_share", w)
                return "under_one_share"
            return await _place(t, book, r, "take", SELL, ex["take"], qty, his_px, p, plan, tif="IOC")
        _exit_held(t, r, ex, plan, w)
    elif short_exit:
        # THE COVER WITH NO REST STANDING (S4): the ask at or under the
        # ceiling cent (his buy-back plus the tolerance, buy_wire'd)
        # sends the one IOC there now -- no wait, no arm; a partial fill
        # leaves NOTHING resting (the next tick plans again). Outside
        # the cent the rest goes at floor(his) below, held by name
        refusal = _s4_refusal(t, book, kind, plan, w)
        if refusal is not None:
            return refusal
        if rules.at_or_through(BUY, r.bid, r.ask, ex["cover"]):
            qty = _cover_qty(book, p.qty)
            if qty < 1:
                _mirror_stop("under_one_share", w)
                return "under_one_share"
            return await _place(t, book, r, "take", BUY, ex["cover"], qty, his_px, p, plan, tif="IOC")
        _exit_held(t, r, ex, plan, w)
    else:
        # the take armed by a post-only rejection, with no rest standing.
        # THE ARM'S EVIDENCE IS BOUNDED: the arm says "the book was
        # crossing when the rest was refused", and it is read here BEFORE
        # the room and the clip, so it once survived every tick where this
        # step never reached _place (the room refused the clip, the
        # increase refused by name) and fired one IOC an hour later with
        # no rest ever at the level -- IOC-first, past the rest-first wait
        # (critic C15; the residual the step-9 minors re-review left, task
        # 7; owner order 2026-09-02, "mirror the whales to a tee"). Two
        # bounds: a book NOT at or through his level now has left the
        # crossing spell the arm witnessed, so the arm is cleared and the
        # next refusal starts its own clock; under a LENGTHENED wait an
        # arm older than TAKE_ARM_STALE_WAITS waits is stale evidence, the
        # take is refused by name and the book RESTS FIRST (a rest the
        # venue accepts clears the arm; a rest it refuses arms afresh from
        # this tick); the window floors at TAKE_ARM_STALE_MIN_S (E2
        # review MEDIUM-2b) so a short wait does not make every arm
        # stale at once. At the default wait of 0 (E4 addendum) the
        # arm is no longer what fires the IOC -- the take below fires
        # on the price alone -- so a stale arm only clears itself
        wait = float(rules.MIRROR_TAKE_AFTER_S)
        if book.get("take_armed_ts") and book["id"] not in t.open_by_book:
            armed = _num(book.get("take_armed_ts"))
            age = None if armed is None else t.now - armed
            if not rules.at_or_through(p.side, r.bid, r.ask, take_lvl):
                await _disarm_take(t, book)
                if age is not None and age >= wait:
                    _mirror_stop("take_refused_price", w)     # waited, the book left his level (E2)
                _recent(book["id"], "take_disarmed", why="market_away", armed_for=age)
            elif age is None or age > max(float(TAKE_ARM_STALE_WAITS) * float(rules.MIRROR_TAKE_AFTER_S),
                                          float(TAKE_ARM_STALE_MIN_S)):
                await _disarm_take(t, book)
                _mirror_stop("take_arm_stale", w)
                _recent(book["id"], "take_disarmed", why="take_arm_stale", armed_for=age)
        # THE TAKE FIRST (E4 addendum): with no wait a plan whose book is
        # at or through his level takes NOW -- an entry's IOC at the
        # wire for the plannable quantity, the remainder resting after
        # it (_entry_take); a long exit with no price of his takes at
        # its wire as before. Under a lengthened wait this is E2's armed
        # take: the arm's age is the wait (take_allowed reads it beside
        # a rest age of 0), the price rule is the same
        if rules.take_allowed(0.0, book.get("take_armed_ts"), t.now, r.bid, r.ask, take_lvl, p.side):
            if rules.leg_action(book.get("intent"), p.side) == "add":
                qty = _room_qty(t, p.qty, wire, intent)
                if qty < 1:
                    _mirror_stop("over_room", w)
                    return "over_room"
                _mirror_stop("take_at_his_level", w)
                return await _entry_take(t, book, r, p, take_lvl, wire, qty, his_px, plan, first=True)
            if not short:
                qty = _sell_qty(book, p.qty)
                if qty >= 1:
                    _mirror_stop("take_at_his_level", w)
                    return await _place(t, book, r, "take", p.side, wire, qty, his_px, p, plan, tif="IOC")
            # a short REDUCE take is a SELL_SHORT IOC -- unproven before
            # rung S4 (brief G2): the reduce path below decides
    if rules.leg_action(book.get("intent"), p.side) == "add":
        qty = _room_qty(t, p.qty, wire, intent)
        if qty < 1:
            _mirror_stop("over_room", w)
            return "over_room"
        return await _place(t, book, r, "increase", p.side, wire, qty, his_px, p, plan)
    if short:
        # THE SHORT COVER AS A PRICED ORDER (S4, 2026-09-07). Every
        # cover -- a partial reduce, the paired or vanish flatten, the
        # sign-flip flatten -- is a BUY of the long token with the
        # closing intent (SELL_SHORT on the wire), through this same
        # placement machinery, never close_position: the venue refuses
        # that call as an unpriced limit order ("Price is required for
        # limit order"; 11 CLOSE rows, 0 executed on 2026-09-07). Gated
        # by the read-back proof (`s4_unproven` on a flatten,
        # `short_reduce_unproven` on a partial reduce until the proof
        # has passed). With his buy-back price: the rest at floor(his)
        # to the cent (the take above fired if the ask was inside the
        # ceiling). With none: the flatten kinds decide below (the
        # bounded IOC when he is gone, else held); a partial reduce
        # with no price of his is held `no_price`, never guessed
        refusal = _s4_refusal(t, book, kind, plan, w)
        if refusal is not None:
            return refusal
        if ex is None:
            if kind in ("flatten_paired", "flatten_vanished"):
                return await _flatten_vanished(t, book, r, p, his_px, plan, kind=kind)
            _mirror_stop("no_price", w)
            plan["cover"] = "unpriced_held"
            log.warning("mirror_live: book %s short reduce held: no buy-back price of his to cover at "
                        "(exit_px_src none)", book["id"])
            return "no_price"
        qty = _cover_qty(book, p.qty)
        if qty < 1:
            _mirror_stop("under_one_share", w)
            return "under_one_share"
        return await _place(t, book, r, kind or "reduce", BUY, ex["rest"], qty, his_px, p, plan)
    qty = _sell_qty(book, p.qty)
    if qty < 1:
        _mirror_stop("under_one_share", w)
        return "under_one_share"
    return await _place(t, book, r, kind or "reduce", SELL, wire, qty, his_px, p, plan)


async def _requotes_this_hour(t: _Tick, book: dict) -> int:
    """The book's cancel-and-re-quote count for the hour (_SQL_REPLACES:
    the replace's cancel and the take's), the figure the replace budget
    is spent from. An unreadable count is the cap."""
    try:
        return int(await t.pool.fetchval(_SQL_REPLACES, book["id"]) or 0)
    except Exception:  # noqa: BLE001 — an unreadable count is the cap
        return int(rules.MIRROR_MAX_REPLACES_PER_HOUR)


def _cover_qty(book: dict, qty: int) -> int:
    """The COVER quantity a short book may place (S4): _sell_qty's rule
    on the leg -- min(plan qty, |ledger|, ceil(held)) when the standing
    row was readable this tick, else min(plan qty, |ledger|). A cover
    is an ordinary clamped order of OUR quantity, never the whole
    slug: co-holding is no longer a question it asks."""
    fv = _num(book.get("_frozen_venue"))
    if fv is not None:
        # E5 / P2: a frozen book's cover is clamped on the VENUE's own
        # short position, never the ledger or the row it disagrees with
        return min(int(qty), max(0, -int(fv)))
    leg = _leg_of(book)
    held = _num(book.get("_held"))
    if held is None:
        return min(int(qty), leg)
    return min(int(qty), leg, int(math.ceil(max(held, 0.0))))


def _s4_proved(t: _Tick) -> bool:
    """Has the resting SELL_SHORT read-back proof passed (S4)? Only a
    dict under `mirror_s4_proof` whose `proved` is True; anything else
    -- absent, unreadable, malformed, a recorded mismatch -- is not."""
    return isinstance(t.s4_proof, dict) and t.s4_proof.get("proved") is True


def _s4_refusal(t: _Tick, book: dict, kind: str | None, plan: dict, whale: str) -> str | None:
    """The cover's gate (S4): None when the proof has passed, else the
    refusal by name -- `s4_unproven` for a flatten (paired, vanished,
    the sign flip), `short_reduce_unproven` for a partial reduce (the
    pre-S4 name, kept until the proof has passed in production). The
    book is held, nothing sent, no freeze; the plan says why. The 050
    column is the first gate: a cover row names SELL_SHORT, which only
    the 050 INSERT can carry (`short_column_absent`, as before S4)."""
    if not t.short_col:
        _mirror_stop("short_column_absent", whale)
        plan["short_column"] = "absent"
        return "short_column_absent"
    if _s4_proved(t):
        return None
    why = (t.s4_proof_err or (t.s4_proof or {}).get("why") or "absent") if t.s4_proof is not None \
        or t.s4_proof_err else "absent"
    if kind in ("flatten_paired", "flatten_vanished") or plan.get("sign_flip") is True:
        _mirror_stop("s4_unproven", whale)
        plan["s4"] = {"unproven": why}
        return "s4_unproven"
    _mirror_stop("short_reduce_unproven", whale)
    plan["short_reduce"] = "unproven"
    plan["s4"] = {"unproven": why}
    return "short_reduce_unproven"


def _s4_probe_due(t: _Tick, book: dict, r: _Reading) -> bool:
    """Does the 1-share read-back probe run now (S4)? Once per tick,
    never proved, never with the key unreadable (nothing is placed on
    a key we cannot record into), at most once per S4_PROBE_GAP_S while
    unproven, on a LIVE short book (never frozen) with a readable
    two-sided quote on a market the venue calls OPEN (or names no
    state), the 050 column present, the tick not tripped or abandoned.
    Never while the record holds a probe order the venue KEPT (S4
    review round 2, GAP 2: one probe at a time; _s4_cancel_retry clears
    the id), and never on a book with an order standing (a probe that
    fills at create is booked on a row of its own, and the one-open-
    per-book index has room for it only when nothing else is open)."""
    if t.s4_probed or _s4_proved(t) or t.s4_proof_err is not None:
        return False
    if _s4_held_order(t.s4_proof) is not None:
        return False
    if not _book_short(book) or book.get("state") != "live" or not t.short_col:
        return False
    if t.cancel_all or t.abandoned:
        return False
    if book["id"] in t.open_by_book or book["id"] in t.nonterminal or book.get("open_order_id"):
        return False
    if r.venue_state is not None and r.venue_state != _STATE_OPEN:
        return False
    b, a = _num(r.bid), _num(r.ask)
    if b is None or a is None or not (0.0 < b < 1.0) or not (0.0 < a < 1.0) or b >= a:
        return False
    last = _num((t.s4_proof or {}).get("at"))
    return last is None or t.now - last >= float(S4_PROBE_GAP_S)


def _s4_probe_price(bid: float) -> float:
    """max(0.01, floor(bid - S4_PROBE_OFFSET)) to the cent: off-market
    for a BUY of the long token, so the probe cannot fill."""
    cents = math.floor(round((float(bid) - float(S4_PROBE_OFFSET)) * 100.0, 6))
    return round(max(1, cents) / 100.0, 2)


async def _s4_probe(t: _Tick, book: dict, r: _Reading) -> None:
    """THE READ-BACK PROOF (S4, owner-authorized in principle: "the
    resting SELL_SHORT read-back on the live short book (1 share,
    off-market, read price/side back, cancel)"). ONE 1-share post-only
    BUY of the long token with the closing intent -- the adapter sends
    intent SELL_SHORT for sell=True on a BUY_SHORT book -- at
    _s4_probe_price(bid), read back through open_orders on the slug
    (price, quantity, the venue's OWN side and the intent as the venue
    reports them), then cancelled. A match records {"proved": true, ...}
    under `mirror_s4_proof` and every cover after it goes; any mismatch
    -- refused, missing on the read-back, another price or quantity, a
    side the venue reports that is not S4_VENUE_BUY (or none reported),
    an intent that is not SELL_SHORT (or none reported), an execution
    at create, a failed cancel -- records {"proved": false, "why", ...}
    and the cover stays refused `s4_unproven`; the probe runs again
    after S4_PROBE_GAP_S. No mirror_orders row: the probe is not a
    position of the book's (its record is the state key and the tick's
    `recent`); its two writes are exit ops (exempt from the budget) and
    counted venue calls. Every venue call is paced.

    THE PRICE SPACE (S4 review, F2). The probe is placed against the
    LONG token's quote -- the book's own _bbo read, the bid and ask in
    the space `his_px` and the cover's rest / take cents are priced in
    -- and the read-back price must equal what was sent IN THAT SPACE;
    the record keeps the quote (`bid`, `ask`) it was placed against. A
    venue that reads a SELL_SHORT limit in the SHORT token's space
    ("sell the short token at >= p", i.e. buy the long at <= 1 - p)
    refuses the probe on a low-priced book (it crosses: fail closed) and
    RESTS it on a high-priced one, echoing our price and intent -- and
    every cover after it would rest where it can never fill. Only the
    venue's own side tells that venue apart: it lists such a probe as
    ORDER_SIDE_SELL, and the proof refuses it `wrong_side`. A book whose
    quote cannot be read two-sided in that space is not probed
    (_s4_probe_due).

    A 429 ON THE PROBE IS NO VERDICT (S4 review, F3) BUT IT IS AN HOUR'S
    HOLD (round 2, GAP 3). E2's rule: a venue 429 is a refusal before
    anything was processed -- named `rate_limited`, the pacer's circuit,
    the tick abandoned `rate_limited` (the backoff skipped while the
    circuit holds) -- never a fact about the order. The create under
    post_only True hands a 429 back as the post_only_rejected shape
    (status_code 429 on the raw; _raw_rate_limit reads the named
    fields), and the preview or an IOC-less create can raise the SDK's
    RateLimitError (ms.is_rate_limit on the exception): both go to the
    rate-limit path, which records {"proved": false, "why":
    "rate_limited", "at": now} so the hourly clock holds the probe --
    without the record the probe was the first write of EVERY tick while
    the venue limited creates, and abandoned each one; a placement's
    429 has the same effect but is a needed order, the probe is a
    diagnostic. Any other refusal or raise records the mismatch as
    before.

    A PROBE THAT FILLS AT CREATE IS BOOKED (round 2, GAP 1): the share
    is ours whatever the venue read, so it goes on an ordinary
    mirror_orders row of kind `s4_probe` (side BUY, the closing intent,
    qty 1, the fill's price, maker False) and through _book_delta
    exactly as a cover fill would (the ledger toward zero by one, the
    realized on it, the standing row's shares), counted
    `s4_probe_filled`; the record says `filled_at_create` with the row
    under `booked` and the cover stays gated. _cover_qty reads the
    ledger the row moved. A PROBE THE VENUE KEPT (a refused cancel,
    round 2, GAP 2) keeps its `order_id` on the record; the cancel is
    retried every tick before the walk (_s4_cancel_retry) and nothing
    is probed again until the venue reports it gone.

    THE STATE (round 2, INFO 5): the echo must also be in a standing
    state (S4_RESTING_STATES: new / pending_new / pending_risk); a
    filled, cancelled, rejected, expired or replaced echo, or one in
    no state, is `wrong_state` / `state_missing`."""
    if await _s4_leftover_filled(t, book, r):
        return
    if not _s4_probe_due(t, book, r):
        return
    t.s4_probed = True
    w, slug = r.whale, r.slug
    price = _s4_probe_price(float(r.bid))
    intent = _book_intent(book)
    rec: dict = {"proved": False, "at": t.now, "slug": slug, "price": price, "order_id": None,
                 "echo": None, "why": None, "bid": _num(r.bid), "ask": _num(r.ask)}
    echo: dict | None = None
    oid = None
    slot = _op_slot(t, w, exit=True)
    if slot is None:
        return
    try:
        _venue_call(t, 1, guard=True)
        try:
            resp = await asyncio.to_thread(_paced, t.pmus.submit_fok, slug, price, 1, True,
                                           "TIME_IN_FORCE_GOOD_TILL_CANCEL", intent, True, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the probe's raise is its verdict
            if ms.is_rate_limit(exc):
                # the request went out (the op is spent, the probe
                # counted placed) and the venue refused it before
                # processing anything: E2's path, under the hour's hold
                slot.commit()
                _mirror_stop("s4_probe_placed", w)
                await _s4_probe_rate_limited(t, book, r, rec, type(exc).__name__, str(exc)[:200])
                return
            rec["why"] = f"place_raised:{type(exc).__name__}"
            rec["echo"] = {"error": str(exc)[:200]}
            resp = None
        slot.commit()
        _mirror_stop("s4_probe_placed", w)
        if resp is not None:
            resp = resp if isinstance(resp, dict) else {}
            oid = resp.get("order_id")
            rec["order_id"] = str(oid) if oid else None
            filled = float(_num(resp.get("filled_shares")) or 0.0)
            raw = resp.get("raw") or {}
            if not oid and _raw_rate_limit(raw):
                # the create's 429 in the post_only_rejected shape, by
                # the raw's named fields (never '429' anywhere in its
                # text): a refusal, not a mismatch -- held for the hour
                await _s4_probe_rate_limited(t, book, r, rec,
                                             str(raw.get("error_type") or resp.get("status") or "429"),
                                             str(raw.get("error") or "")[:200])
                return
            if not oid:
                rec["why"] = f"place_refused:{resp.get('status') or 'no_id'}"
                rec["echo"] = {"raw": _jsonish(_refusal_receipt(raw))}
            elif filled > 0.0:
                # an off-market rest that executed at create: the venue
                # did not read our price (or our side) as we sent it --
                # and the share is ours: booked on its own row (GAP 1)
                rec["why"] = "filled_at_create"
                rec["echo"] = {"filled_shares": filled, "fill_price": resp.get("fill_price"),
                               "status": resp.get("status")}
                rec["booked"] = await _s4_book_filled(t, book, r, str(oid), filled,
                                                      resp.get("fill_price"), str(resp.get("status") or ""),
                                                      raw, price)
    finally:
        slot.release()
    if oid and rec["why"] is None:
        try:
            orders = list(await _venue_read(t, t.pmus.open_orders, [slug], guard=True) or [])
        except Exception as exc:  # noqa: BLE001 — unreadable is not a proof
            orders = None
            rec["why"] = f"read_back_raised:{type(exc).__name__}"
        if orders is not None:
            mine = [o for o in orders if str(o.get("order_id")) == str(oid)]
            if not mine:
                rec["why"] = "not_found"
                rec["echo"] = {"open_on_slug": len(orders)}
            else:
                v = mine[0]
                echo = {k: v.get(k) for k in ("order_id", "us_market_slug", "side", "venue_side",
                                                "intent", "price", "quantity", "state", "tif")}
                rec["echo"] = echo
                vp, vq = _num(v.get("price")), _num(v.get("quantity"))
                if str(v.get("us_market_slug") or "").lower() != slug.lower():
                    rec["why"] = "wrong_slug"
                elif vp is None or abs(vp - price) > 1e-9:
                    rec["why"] = "wrong_price"
                elif vq is None or abs(vq - 1.0) > 1e-9:
                    rec["why"] = "wrong_quantity"
                elif v.get("venue_side") != S4_VENUE_BUY:
                    # THE VENUE'S OWN SIDE, never the desk's derived
                    # `side` (F1): a BUY of the contract, or nothing
                    rec["why"] = "side_missing" if not v.get("venue_side") else "wrong_side"
                elif v.get("intent") != "ORDER_INTENT_SELL_SHORT":
                    rec["why"] = "intent_missing" if not v.get("intent") else "wrong_intent"
                elif str(v.get("state") or "") not in S4_RESTING_STATES:
                    # a standing order, in one of the states the venue
                    # lists a standing order under (INFO 5); a fill, a
                    # cancel, a rejection or no state proves nothing
                    rec["why"] = ("state_missing" if str(v.get("state") or "") in ("", "unknown")
                                  else "wrong_state")
    if oid and rec["why"] != "filled_at_create":
        # cancelled whatever the verdict: nothing of the probe's may stand
        _venue_call(t, 1, guard=True)
        try:
            res = await asyncio.to_thread(_paced, t.pmus.cancel_order, oid, slug)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
        if not (isinstance(res, dict) and res.get("ok")):
            err = (res or {}).get("error") if isinstance(res, dict) else None
            log.error("mirror_live: S4 probe order %s on %s could not be cancelled (%s); it may still rest: "
                      "the cancel is retried every tick until the venue reports it gone", oid, slug, err)
            # the id STAYS on the record with `cancel.ok` False (GAP 2):
            # _s4_held_order reads exactly that, the retry runs before
            # every walk, and no second probe goes out meanwhile
            rec["cancel"] = {"ok": False, "error": str(err)[:200] if err else None, "retries": 0,
                             "at": t.now}
            if ms.is_rate_limit(err):
                # the cancel's 429: named and penalized, never a verdict
                # about the order (E2's rule for a cancel)
                _rate_limited(t, w, f"S4 probe cancel {oid}")
            if rec["why"] is None:
                rec["why"] = "cancel_failed"
        else:
            rec["cancel"] = {"ok": True}
    rec["proved"] = rec["why"] is None
    rec = await _s4_record(t, rec)
    if rec["proved"]:
        _mirror_stop("s4_proved", w)
        log.warning("mirror_live: S4 read-back PROVED on %s: order %s at %s read back as %s", slug, oid,
                    price, echo)
    else:
        log.warning("mirror_live: S4 read-back NOT proved on %s (%s): %s", slug, rec["why"], rec["echo"])
    _recent(book["id"], "s4_probe", proved=rec["proved"], why=rec["why"], order=rec["order_id"],
            price=price)


async def _s4_record(t: _Tick, rec: dict) -> dict:
    """Write the proof record under the key and make it the tick's
    reading; a failed write is `record_failed:<Type>` in memory (an
    unrecorded probe is unproven, and the next tick reads the key as it
    was)."""
    try:
        await _write_state(t.pool, _STATE_S4, rec)
    except Exception as exc:  # noqa: BLE001 — unrecorded is unproven
        log.error("mirror_live: S4 proof could not be recorded (%s)", type(exc).__name__)
        rec = {**rec, "proved": False, "why": f"record_failed:{type(exc).__name__}"}
    t.s4_proof = rec
    return rec


async def _s4_probe_rate_limited(t: _Tick, book: dict, r: _Reading, rec: dict, name: str,
                                 text: str) -> None:
    """The probe's placement refused by the venue's rate limit (S4
    review, F3): E2's path for a placement's 429 -- `rate_limited` on
    the census, the pacer's circuit (_rate_limited), the tick abandoned
    `rate_limited` with the backoff skipped while the circuit holds.
    A 429 is the venue refusing the request before it processed
    anything, not a fact about the order -- but it IS the hour's hold
    (round 2, GAP 3): the record {"proved": false, "why":
    "rate_limited", "at": now, ...} is written so _s4_probe_due waits
    S4_PROBE_GAP_S before the next probe, one abandon per hour instead of
    one per tick for as long as the venue limits creates. The request
    went out: the caller counted `s4_probe_placed` as every placement's
    429 counts its op."""
    w, slug = r.whale, r.slug
    log.warning("mirror_live: S4 probe on %s refused by the venue's rate limit (%s: %s); not a verdict, "
                "held for the hour", slug, name, text)
    rec["why"] = "rate_limited"
    rec["echo"] = {"raised": name, "error": text}
    rec["proved"] = False
    await _s4_record(t, rec)
    _rate_limited(t, w, f"S4 probe {slug}")
    _recent(book["id"], "s4_probe", proved=False, why="rate_limited", raised=name)
    _abandon(t, "rate_limited", rate_limited=True)


def _s4_held_order(rec: Any) -> str | None:
    """The probe order the venue KEPT, as the record names it (round 2,
    GAP 2): an unproved record carrying `order_id` whose `cancel.ok` is
    False -- the cancel was refused (or 429'd) and nothing has reported
    the order gone since. A filled probe (no `cancel`), a cancelled one
    (`cancel.ok` True) and a proved record hold nothing."""
    if not isinstance(rec, dict) or rec.get("proved") is True:
        return None
    oid, cancel = rec.get("order_id"), rec.get("cancel")
    if not oid or not isinstance(cancel, dict) or cancel.get("ok") is not False:
        return None
    return str(oid)


async def _s4_book_filled(t: _Tick, book: dict, r: _Reading, oid: str, filled: float,
                          fill_price: Any, status: str, raw: Any, wire: float) -> dict:
    """Book the probe's share (round 2, GAP 1): the post-only latch
    tripped and the venue filled our 1-share off-market cover at
    create (or a probe the venue kept later filled, GAP 2). It is a
    share of OUR short bought back, so it is booked exactly as a cover
    fill: an ordinary mirror_orders row of kind `s4_probe` (side BUY,
    the closing intent SELL_SHORT, qty 1, GTC post-only as sent, the
    fill's price as the venue named it), the id persisted, then
    _book_delta (the leg's reduce booking: the ledger toward zero by
    one, the realized on it, the standing row's shares) and
    _finish_order (filled, maker False: it executed at create). Counted
    `s4_probe_filled`. Returns what the record keeps under `booked`:
    the row and the ledger after it, or the failure by name -- a row
    that could not be written leaves the share on the venue and not on
    the ledger, so the book is frozen `s4_probe_unbooked` (named, never
    silent; the venue/ledger read is inside its one-share tolerance
    and would not say it)."""
    w, slug = r.whale, r.slug
    args = [book["id"], w, slug, "s4_probe", BUY, "GTC", True, None, None, wire, wire, 1,
            json.dumps([]), int(_num(book.get("target")) or 0), int(book.get("ledger_net") or 0),
            r.bid, r.ask, "s4_probe"]
    try:
        row_id = int(await t.pool.fetchval(_SQL_ORDER_INSERT, *args, "ORDER_INTENT_SELL_SHORT"))
        await t.pool.execute(_SQL_ORDER_PERSIST_ID, row_id, oid, status, json.dumps(raw or {}, default=str))
    except Exception as exc:  # noqa: BLE001 — the share is on the venue; say so by name
        log.error("mirror_live: S4 probe %s on %s FILLED (%s share at %s) but its row could not be written "
                  "(%s); the share is on the venue and not on the ledger", oid, slug, filled, fill_price,
                  type(exc).__name__, exc_info=True)
        _mirror_stop("s4_probe_filled", w)
        await _freeze(t, book, "s4_probe_unbooked", {"order": oid, "filled": filled,
                                                     "error": type(exc).__name__})
        return {"row": None, "error": type(exc).__name__}
    o = {"id": row_id, "book_id": book["id"], "whale": w, "us_market_slug": slug,
         "kind": "s4_probe", "side": BUY, "tif": "GTC", "post_only": True, "his_level": None,
         "price": wire, "wire": wire, "qty": 1, "order_id": oid, "state": "open", "filled": 0.0,
         "booked_filled": 0.0, "avg_px": None, "taker_at_placement": True, "pre_ids": [],
         "placed_ts": t.now, "reason": "s4_probe", "intent": "ORDER_INTENT_SELL_SHORT"}
    st = {"state": status or "filled", "filled_shares": float(filled), "avg_px": fill_price}
    _mirror_stop("s4_probe_filled", w)
    out = await _book_delta(t, o, book, st, maker=False, taker_at_placement=True)
    await _finish_order(t, o, book, st, "s4_probe")
    log.warning("mirror_live: S4 probe %s on %s FILLED at create (%s share at %s): booked on row %s (%s), "
                "ledger %s", oid, slug, filled, fill_price, row_id, out, book.get("ledger_net"))
    _recent(book["id"], "s4_probe_filled", order=oid, row=row_id, px=fill_price, booked=out,
            ledger=book.get("ledger_net"))
    return {"row": row_id, "booked": out, "ledger": book.get("ledger_net")}


async def _s4_leftover_filled(t: _Tick, book: dict, r: _Reading) -> bool:
    """A probe the venue kept that FILLED before its cancel got through
    (round 2, GAP 2): _s4_cancel_retry read the fill off the order's
    status and left it on the record (`cancel.filled`) for the walk,
    which holds the book the share belongs to. On that book: booked as
    a filled probe (_s4_book_filled), the id cleared, the clock
    restarted. True when this book did that (nothing else is probed
    this tick)."""
    rec = t.s4_proof
    oid = _s4_held_order(rec)
    if oid is None or not _book_short(book):
        return False
    cancel = rec.get("cancel") or {}
    filled = float(_num(cancel.get("filled")) or 0.0)
    if filled <= 0.0 or str(rec.get("slug") or "").lower() != str(r.slug or "").lower():
        return False
    if book.get("state") != "live" or book["id"] in t.open_by_book or book["id"] in t.nonterminal \
            or book.get("open_order_id"):
        # the row needs the book's open slot; the record keeps the fill
        # until a tick that has it
        return False
    booked = await _s4_book_filled(t, book, r, oid, filled, cancel.get("fill_price"),
                                   str(cancel.get("state") or "filled"), None, float(rec.get("price") or 0.0))
    new = {**rec, "order_id": None, "at": t.now, "booked": booked,
           "cancel": {**cancel, "ok": True, "order": oid, "gone": "filled", "at": t.now}}
    await _s4_record(t, new)
    return True


async def _s4_cancel_retry(t: _Tick) -> None:
    """Before the walk: the probe order the venue KEPT (round 2, GAP 2)
    is cancelled again -- paced, through _guarded (no row: the orphan
    write has nothing to persist), an exit op -- every tick until the
    venue reports it gone. Gone is the cancel accepted, or a refused
    cancel followed by an order status the venue reads TERMINAL
    (cancelled / expired / rejected / replaced) or no record of the id
    at all: then the id is cleared and the hourly clock restarts from
    now (`at`), the retry count kept under `cancel`. A status that reads
    FILLED is the share of GAP 1 by another road: the fill is left on
    the record (`cancel.filled`, `fill_price`) for the walk to book on
    its book (_s4_leftover_filled) and the id is held until it does.
    A 429 on the cancel is E2's path for a cancel -- `rate_limited`,
    the pacer's circuit -- and the id is kept, retried next tick; the
    tick is not abandoned (a cancel's 429 never was). Still resting, or
    unreadable: kept, retried next tick. While an id is held nothing is
    probed (_s4_probe_due)."""
    rec = t.s4_proof
    oid = _s4_held_order(rec)
    if oid is None:
        return
    slug = str(rec.get("slug") or "")
    cancel = dict(rec.get("cancel") or {})
    if not slug:
        log.error("mirror_live: S4 probe order %s is held on a record naming no slug; cannot cancel it", oid)
        return
    if float(_num(cancel.get("filled")) or 0.0) > 0.0:
        return                              # the walk books it on its book
    slot = _op_slot(t, None, exit=True)
    if slot is None:
        return
    try:
        try:
            res = await _guarded(t, 0, t.pmus.cancel_order, oid, slug)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
        slot.commit()
    finally:
        slot.release()
    ok = isinstance(res, dict) and bool(res.get("ok"))
    err = None if ok else ((res or {}).get("error") if isinstance(res, dict) else None)
    cancel["retries"] = int(cancel.get("retries") or 0) + 1
    cancel["at"] = t.now
    gone: str | None = "cancelled" if ok else None
    if not ok:
        cancel["error"] = str(err)[:200] if err else None
        if ms.is_rate_limit(err):
            _rate_limited(t, None, f"S4 probe cancel {oid}")
            _recent(None, "s4_probe_cancel", order=oid, why="rate_limited", retries=cancel["retries"])
            await _s4_record(t, {**rec, "cancel": cancel})
            return
        # refused: does the venue still hold it? its own record decides
        st = await _order_status(t, oid)
        if st is None:
            gone = "no_record"
        else:
            filled = float(_num(st.get("filled_shares")) or 0.0)
            state = str(st.get("state") or "")
            if filled > 0.0:
                cancel.update(filled=filled, fill_price=st.get("avg_px"), state=state)
                log.error("mirror_live: S4 probe order %s on %s FILLED while the venue kept it (%s share); "
                          "booked on its book's next walk", oid, slug, filled)
                _recent(None, "s4_probe_cancel", order=oid, why="filled", filled=filled)
                await _s4_record(t, {**rec, "cancel": cancel})
                return
            if le._rest_terminal(st):
                gone = state or "terminal"
    if gone is None:
        log.error("mirror_live: S4 probe order %s on %s still rests; cancel refused again (%s), retry %s",
                  oid, slug, err, cancel["retries"])
        _recent(None, "s4_probe_cancel", order=oid, why="refused", retries=cancel["retries"])
        await _s4_record(t, {**rec, "cancel": cancel})
        return
    log.warning("mirror_live: S4 probe order %s on %s is gone (%s) after %s cancel retries; the hour "
                "restarts", oid, slug, gone, cancel["retries"])
    _recent(None, "s4_probe_cancel", order=oid, why="gone", gone=gone, retries=cancel["retries"])
    await _s4_record(t, {**rec, "order_id": None, "at": t.now,
                         "cancel": {**cancel, "ok": True, "order": oid, "gone": gone}})


def _exit_terms(t: _Tick, book: dict, side: str | None, his_px: float | None,
                plan: dict) -> dict | None:
    """rules.exit_terms for an EXIT plan of this book (E4), stamped on
    the plan: `exit_px` (his exit price), `exit_px_src` ('his_fill', or
    'none' when the rule cannot apply), and on a long book `exit_floor`
    with the rest and take cents, on a short book `exit_ceiling` with
    the cover cent. None -- and nothing stamped -- for an increase or
    no plan; None under `exit_px_src: 'none'` for the admin flatten
    (t.flatten_all: today's behaviour by the brief) and for an exit he
    gave no price for (a snapshot-driven reduce, a vanish with no fill
    of his): those keep the plan's old prices, never a guessed level."""
    if side is None or rules.leg_action(book.get("intent"), side) != "reduce":
        return None
    ex = None if t.flatten_all else rules.exit_terms(side, his_px)
    if ex is None:
        plan.update(exit_px=_num(his_px), exit_px_src="none")
        return None
    plan.update(exit_px=ex["px"], exit_px_src="his_fill")
    if "floor" in ex:
        plan.update(exit_floor=round(ex["floor"], 6), exit_rest=ex["rest"], exit_take=ex["take"])
    else:
        plan.update(exit_ceiling=round(ex["ceiling"], 6), exit_cover=ex["cover"], exit_rest=ex["rest"])
    return ex


def _exit_held(t: _Tick, r: _Reading, ex: dict, plan: dict, whale: str,
               quote: tuple | None = None) -> None:
    """The exit is HELD outside the cent (E4 rule 3): named on the
    census and on the plan with the quote and the bound it was read
    against. Nothing chases. `quote` is a fresher (bid, ask) than the
    tick's, when one was read (the priced cover's read before its
    close)."""
    _mirror_stop("exit_out_of_tol", whale)
    if "ceiling" in ex:
        _mirror_stop("short_cover_out_of_tol", whale)     # S4: the cover held outside the cent
    bound = ("floor", round(ex["floor"], 6)) if "floor" in ex else ("ceiling", round(ex["ceiling"], 6))
    bid, ask = quote if quote is not None else (r.bid, r.ask)
    plan["exit_out_of_tol"] = {"bid": bid, "ask": ask, bound[0]: bound[1], "at": t.now}


async def _entry_take(t: _Tick, book: dict, r: _Reading, p: mi.Plan, ioc_px: float, rest_px: float,
                      qty: int, his_px: float | None, plan: dict, first: bool) -> str:
    """An ENTRY's take (E4 addendum): ONE IOC at HIS cent (`ioc_px`,
    rules.buy_wire(his): the ask at or under it is at or through his
    level, and the IOC can never fill above him) for `qty`, then the
    unfilled remainder rests post-only at the rest's wire (`rest_px`,
    buy_price(his, bid)) -- the only standing order the plan leaves,
    never a second IOC (addendum 4). `first` is the take with no rest
    to cancel (`take_first`); the room the IOC reserved and did not
    fill is given back before the rest is sized (_place_reserved), so
    the plan spends its room once. A take the venue refused, a tick
    that tripped or abandoned, a book frozen by the take's own booking:
    nothing more this tick.

    THE RE-QUOTE CREDIT (E2 review round 3, LOW-6) MEETS THE TAKE-FIRST:
    a TTL or replace cancel of this book this tick covers ONE rest
    (_Tick.requote_credit, spent in _place by a non-IOC alone). The IOC
    never rides it -- it is its own op -- and the remainder's rest
    spends it: cancel + IOC + rest is two ops. An IOC the budget refused
    (`ops_capped`) with the credit standing rests the plannable quantity
    on the credit instead: the cohort's book is not left bare for the
    tick with its rest cancelled, which is what the credit is for."""
    res = await _place(t, book, r, "take", p.side, ioc_px, qty, his_px, p, plan, tif="IOC",
                       take_first=first)
    if (res == "ops_capped" and book["id"] in t.requote_credit
            and not (t.cancel_all or t.abandoned) and book.get("state") != "frozen"):
        rest = await _place(t, book, r, "increase", p.side, rest_px, qty, his_px, p, plan)
        return rest if rest == "rest_placed" else res
    if res != "take" or t.cancel_all or t.abandoned or book.get("state") == "frozen":
        return res
    left = int(math.floor(float(qty) - float(_num(plan.get("take_filled")) or 0.0) + 1e-9))
    if left < 1 or book["id"] in t.nonterminal:
        return res
    rest = await _place(t, book, r, "increase", p.side, rest_px, left, his_px, p, plan)
    return rest if rest == "rest_placed" else res


def _wire_for(p: mi.Plan | None, his_px: float | None, r: _Reading,
              intent: str | None = None, ex: dict | None = None) -> float | None:
    """The cent on the wire from the UNROUNDED facts (addendum section
    10: never plan.price). A BUY joins HIS level: with no level there is
    nothing to join and buy_price says so. A SELL is a reduction of
    what we hold: WITH HIS EXIT PRICE (E4: `ex`, rules.exit_terms) the
    rest is HIS cent, ceil(his price), and the ask never lifts it;
    without one his equivalent is one of the plan's two candidates
    and the ask alone is the plan's price when he gave none (mi.plan's
    cands) -- the admin flatten and a snapshot-driven reduction have no
    fill of his to price off, and the ask is a read fact, never a guess
    under it.

    ON A SHORT BOOK (P2 rung S0, brief C4, Q6 (a)) the SELL_LONG plan
    is an ADD to the short and its wire is the BUY_SHORT wire
    (_short_wire); the BUY_LONG plan is a COVER (S4, 2026-09-07): with
    his buy-back price it rests at floor(his) to the cent
    (rules.exit_terms(BUY)["rest"]); with none, buy_price's figure is
    returned for the record and the cover is held by name (`no_price`),
    never sent at a guessed level."""
    if p is None or p.side is None:
        return None
    if rules.is_short(intent):
        if p.side == SELL:
            return _short_wire(his_px, r.ask)
        if ex is not None and ex.get("rest") is not None:
            # S4: the cover rests at floor(his buy-back) to the cent --
            # never above him, and under the ask whenever the take did
            # not fire (the ask is then above his price plus the
            # tolerance, so above the rest): rules.exit_terms(BUY)
            return float(ex["rest"])
        return rules.buy_price(his_px, r.bid)
    if p.side == BUY:
        return rules.buy_price(his_px, r.bid)
    if ex is not None and ex.get("rest") is not None:
        return float(ex["rest"])
    return rules.sell_price(his_px if his_px is not None else r.ask, r.ask)


def _short_wire(his_px: float | None, ask: float | None) -> float | None:
    """The cent a BUY_SHORT rests at, from the UNROUNDED facts, in the
    denomination the venue reads (P2 rung S0, brief 3.2 step 4; Q6 (a)
    pinned on the 19-cent table).

    THE ORDER, IN HIS TERMS: we bid for the other side at his price or
    better. `his_px` is his level in LONG space -- one minus what he
    paid for the other token (_his_level) -- and the plan's price is
    max(his level, ask) in long space, exactly the shadow's
    `would_px_short`; so the most we pay a share for the short leg is
    `cost = 1 - max(his, ask) = min(his other-token price, 1 - ask)`,
    his level in the short leg's own price, at or under the short
    leg's bid (1 - bestAsk).

    THE NUMBER ON THE WIRE is the executor's own for that cost, the one
    every per-fill BUY_SHORT has been sent since 2026-08-25 (386 fills
    side-verified, 0 mismatch): `rest_tick(wire_limit(cost, BUY_SHORT))`
    = ceil(1 - cost) to the cent = the CONTRACT price the contract is
    sold at or above ("sell at >= 0.78 means pay <= 0.22", le.wire_limit).
    Stored AS SENT; the collateral it commits is 1 - wire a share
    (le.cost_per_share), which the day cap, the room, the reserve and
    the overspend tripwire all read. It differs from rules.sell_price's
    ceiling at 19 of 98 exact cents (float noise a hair above the cent
    steps sell_price UP; the executor's round(..., 6) reads it as the
    cent) and the executor's rounding is the one pinned. None when the
    short model is disarmed (le.wire_limit would then return the cost
    unchanged -- a limit in the wrong space, tee R7), when the ask is
    not a price, or when the cent is off the ladder."""
    if not le.short_model_confirmed():
        return None
    h, a = _num(his_px), _num(ask)
    if a is None or not (0.0 < a < 1.0):
        return None
    px_long = a if (h is None or not (0.0 < h < 1.0)) else max(h, a)
    cost = round(1.0 - px_long, 6)
    if not (0.0 < cost < 1.0):
        return None
    w = le.rest_tick(le.wire_limit(cost, ORDER_INTENT_SHORT), ORDER_INTENT_SHORT)
    return w if 0.01 <= w <= 0.99 else None


def _room_qty(t: _Tick, qty: int, wire: float | None, intent: str | None = None) -> int:
    # the MIRROR lane's own per-order clip (rules.MIRROR_CLIP_USD, $2,500:
    # owner order ~14:00Z 2026-09-06), never the copy lane's $250 -- a
    # $2,500 target is one rest, not ten sequential ones
    return rules.room_scale(int(qty), wire, rules.MIRROR_CLIP_USD, t.day_room, t.total_room,
                            t.mirror_day, intent=intent)


def _sell_qty(book: dict, qty: int) -> int:
    """The SELL quantity this tick may place against the book for the
    IOC, rest, take and reduce legs: min(plan qty, ledger, ceil(held))
    when the standing row was readable this tick (book["_held"],
    stashed ONCE by _tick_book off the same row read it already makes
    -- the standing row as the tick read it, not as a fill later in
    the tick left it), else min(plan qty, ledger) as before.

    mirror_books.ledger_net is an integer column written as
    int(round(...)); the standing row is fractional (a venue fill of
    182.76 + 231.0 = 413.76 shares reads 414 on the ledger). NOT
    floored: floor(413.76) = 413 leaves ledger 1 / row 0.76 / venue
    0.76 that nothing can sell -- under_one_share on every tick and
    never a flat close (the withdrawn first cut). At ceil(held) the
    order is 414 on 413.76 held: a one-lot overrun onto a fractional
    row is exactly what _book_fill books as dust (rules
    SELL_DUST_SHARES), so the extra lot books, never trips, and the
    book reaches flat (ledger 0, row 0.0) after one flatten. The
    ceiling never exceeds the ledger, so a row holding more than the
    ledger says still sells the ledger. ON A FROZEN EXIT (E5 / P2) the
    clamp is the VENUE's own position (the book's `_frozen_venue`):
    the ledger and the row are what the venue disagrees with, and a
    reduce sized on them would leave the lost response's shares held
    to settlement. The sole-holder close_position
    (_flatten_vanished) does NOT size here: it closes the whole slug,
    fraction included, unclamped. With the row unreadable this tick
    (None) the sizing stands on the ledger as before -- fail closed on
    the trip, which still exists for a sale more than a lot past the
    ledger."""
    fv = _num(book.get("_frozen_venue"))
    if fv is not None:
        return min(int(qty), max(0, int(fv)))
    ledger = int(book.get("ledger_net") or 0)
    held = _num(book.get("_held"))
    if held is None:
        return min(int(qty), ledger)
    return min(int(qty), ledger, int(math.ceil(max(held, 0.0))))


async def _guarded(t: _Tick, row_id: int, fn, *args, **kwargs):
    """The _ioc_guarded shape: the venue call runs shielded so our own
    cancellation cannot lose its result; on cancellation the order id
    is written onto the mirror_orders row when the call completes.
    The write is PACED (E2 review, HIGH-1: _paced) and counted for
    each HTTP request it makes (_write_slots), against the soft guard."""
    slots = _write_slots(fn, args)
    _venue_call(t, slots, guard=True)
    if slots > 1:
        # the adapter claims its own gap between its preview and its
        # create (pmus.submit_fok paced_pair): one claim per request
        kwargs = {**kwargs, "paced_pair": True}
    fut = asyncio.ensure_future(asyncio.to_thread(_paced, fn, *args, **kwargs))
    try:
        return await asyncio.shield(fut)
    except asyncio.CancelledError:
        asyncio.ensure_future(_record_orphan(t.pool, row_id, fut))
        raise


async def _record_orphan(pool, row_id: int, fut) -> None:
    try:
        result = await asyncio.wait_for(fut, timeout=120.0)
    except BaseException as exc:  # noqa: BLE001 — includes cancellation
        log.error("mirror_live: venue call on order row %s cancelled mid-call; result lost (%s)",
                  row_id, type(exc).__name__)
        return
    oid = result.get("order_id") if isinstance(result, dict) else None
    if not oid:
        return
    try:
        await pool.execute(_SQL_ORDER_PERSIST_ID, row_id, str(oid),
                           str(result.get("status") or ""), json.dumps(result.get("raw") or {},
                                                                       default=str))
    except Exception:  # noqa: BLE001
        log.error("mirror_live: orphan order %s on row %s could not be persisted", oid, row_id,
                  exc_info=True)


async def _place(t: _Tick, book: dict, r: _Reading, kind: str, side: str, wire: float,
                 qty: int, his_px: float | None, p: mi.Plan | None, plan: dict,
                 tif: str = "GTC", take_first: bool = False) -> str:
    """Step L (and T when tif is IOC): INSERT the 'placing' row with
    the pre-placement snapshot BEFORE the venue call, place, persist
    the id IMMEDIATELY, book what executed on create.

    THE OP IS RESERVED HERE AND COMMITTED AT THE WRITE (E2): the budget
    check and the `t.ops` increment had the short doors, the open-orders
    read and the row INSERT between them, and under the parallel walk
    two books could pass the check on the same last op. The slot is
    released on every refusal before the write, so `ops` still counts
    writes alone. `take_first` (E4 addendum) is an entry's IOC with no
    rest behind it: sized on the room as it stands like a rest, and
    counted `take_first`."""
    w = r.whale
    if t.cancel_all:
        # a tick that tripped mid-way (an overfill booked by the cancel
        # this placement follows, a wrong sign on another book) places
        # nothing more: the one choke point (step-9 review)
        return t.cancel_all
    if t.abandoned:
        # a book still in flight when another book abandoned the tick
        # (E2): an abandoning tick plans nothing, and places nothing --
        # named (`abandoned_in_flight`, review LOW-6), and _tick_book
        # writes no plan for it, so its updated_at stays where it was
        # and the next tick walks its game first (E1's round-robin)
        _mirror_stop("abandoned_in_flight", w)
        return "tick_abandoned"
    if book["id"] in t.requote_credit and tif != "IOC":
        # the rest after this tick's TTL/replace cancel of this book:
        # the cancel's op covers it (E2 review round 3, LOW-6)
        t.requote_credit.discard(book["id"])
        slot: _OpSlot | None = _OpSlot(t, credited=True)
    else:
        # AN EXIT'S PLACEMENT IS NEVER `ops_capped` (E4 review round 3,
        # M-1): its IOC, its rest at his cent, a flatten rest, the
        # co-held IOC. Exit-ness is the side's leg action on this book
        # -- the reading every exit clause of this file makes -- so a
        # short book's SELL (an add to the short) stays bounded and its
        # BUY (the cover) is exempt
        slot = _op_slot(t, w, exit=rules.leg_action(book.get("intent"), side) == "reduce")
    if slot is None:
        return "ops_capped"
    try:
        return await _place_reserved(t, slot, book, r, kind, side, wire, qty, his_px, p, plan, tif,
                                     take_first)
    finally:
        slot.release()


async def _place_reserved(t: _Tick, slot: _OpSlot, book: dict, r: _Reading, kind: str, side: str,
                          wire: float, qty: int, his_px: float | None, p: mi.Plan | None,
                          plan: dict, tif: str, take_first: bool = False) -> str:
    global _POST_ONLY_OK
    w, slug = r.whale, r.slug
    # THE WIRE-SIDE MAP (P2 rung S0, brief 3.3): the book's intent and
    # the plan side name the wire intent the row records, the sell flag
    # the adapter is handed beside the book's BUY intent, and whether
    # this row grows the leg (the reserve, the room, the day cap)
    ws = rules.wire_side(book.get("intent"), side)
    if ws is None:
        _mirror_stop("book_error", w)
        log.error("mirror_live: book %s carries an intent this lane cannot wire (%r)",
                  book["id"], book.get("intent"))
        return "book_error"
    wire_intent, sell, action = ws
    intent = _book_intent(book)
    # THE SMALLEST ORDER (review of U12c, FIX-2): under
    # rules.MIRROR_MIN_ORDER_USD of notional -- wire x qty on a long,
    # (1 - wire) x qty of collateral on a short (_cost_px) -- the order
    # is not sent and the book is held under its own name, before any
    # read or op is spent on it; the venue's minimum is unknown and a
    # refused order would burn an op every tick for as long as it stood.
    # A FLATTEN IS EXEMPT (FIX-2b): a position that is leaving must be
    # allowed to leave at any size -- the flatten kinds (paired,
    # vanished), the sign-flip flatten and any plan toward target 0 --
    # because a refused flatten rest never writes the order row that
    # _flatten_vanished keys its rest clock on, and close_position is
    # then never reached
    flattening = (kind in ("flatten_paired", "flatten_vanished")
                  or int(_num(plan.get("target")) or 0) == 0)
    if not flattening and float(qty) * _cost_px(float(wire), book) < float(rules.MIRROR_MIN_ORDER_USD):
        _mirror_stop("under_min_notional", w)
        return "under_min_notional"
    if wire_intent in (ORDER_INTENT_SHORT, "ORDER_INTENT_SELL_SHORT") and not t.short_col:
        # a short row is never written through the 047 INSERT: the
        # column would read BUY_LONG for a row whose wire intent is a
        # short one (the flatten path refuses the same way)
        _mirror_stop("short_column_absent", w)
        return "short_column_absent"
    if wire_intent == ORDER_INTENT_SHORT:
        # every short open -- a new book, an add, a take -- passes the
        # two doors (H1, H2), read now; a cover never reaches _place
        held = await _short_open_refusal(t)
        if held:
            _mirror_stop(held, w)
            return held
    orders = await _read_open(t)
    if orders is None:
        return "open_orders_unreadable"
    pre_ids = sorted({str(o.get("order_id")) for o in orders
                      if o.get("order_id") and str(o.get("us_market_slug") or "").lower() == slug.lower()})
    is_take = tif == "IOC"
    post_only = bool(_post_only_enabled()) and not is_take
    # AN EXIT REST AT HIS CENT STANDS UNTIL HIS CENT MOVES (E4 rule 3):
    # it carries no good-till, whatever the GTD flag says, so the venue
    # never expires it into the re-quote the rule forbids
    stands = action == "reduce" and plan.get("exit_px_src") == "his_fill"
    good_till = (_iso(t.now + float(rules.MIRROR_REST_TTL_S))
                 if (_gtd_enabled() and not is_take and not stands) else None)
    tif_rec = "IOC" if is_take else ("GTD" if good_till else "GTC")
    venue_tif = ("TIME_IN_FORCE_IMMEDIATE_OR_CANCEL" if is_take
                 else "TIME_IN_FORCE_GOOD_TILL_CANCEL")
    if action == "add" and (kind != "take" or take_first):
        # THE ROOM, READ AGAIN AND TAKEN NOW (E2): _act sized this add
        # off the tick's room across awaits another book may have spent
        # it through; the same scaling on the room as it stands, with
        # nothing between this read and the take, so two books in one
        # tick cannot each spend the last clip. Sequentially the same
        # figure (room_scale is idempotent on its own answer). A take
        # off a cancelled rest was sized under _room_qty too and is not
        # re-scaled here: its quantity is what the rest left. A take
        # with NO rest behind it (`take_first`, E4 addendum) is sized
        # here like the rest it stands in for
        qty = _room_qty(t, int(qty), wire, intent)
        if qty < 1:
            _mirror_stop("over_room", w)
            return "over_room"
    # the reserve is the COLLATERAL the row commits: the wire a share on
    # a long, one minus it on a short (brief D2) -- and this tick's room
    # is taken BEFORE the venue call, given back on an outright refusal
    est = float(qty) * _cost_px(wire, book) if action == "add" else 0.0
    _room_take(t, est)
    # THE ROW'S REASON IS THE FROZEN EXIT'S MARKER (E5 / P2): a reduce
    # sized on the venue's own position carries `frozen_reduce`, the
    # one word step O reads to let it stand on a frozen book and the
    # fill path reads to name a fill past the ledger `frozen_excess_sold`
    # instead of the overfill trip. Every other row keeps its kind
    reason_col = "frozen_reduce" if book.get("_frozen_venue") is not None else kind
    args = [book["id"], w, slug, kind, side, tif_rec, post_only, good_till,
            his_px, (p.price if p is not None else wire), wire, int(qty), json.dumps(pre_ids),
            int(_num(plan.get("target")) or 0), int(book.get("ledger_net") or 0), r.bid, r.ask,
            reason_col]
    try:
        if t.short_col:
            row_id = await t.pool.fetchval(_SQL_ORDER_INSERT, *args, wire_intent)
        else:
            row_id = await t.pool.fetchval(_SQL_ORDER_INSERT_047, *args)
    except Exception as exc:  # noqa: BLE001 — the unique index: one open order per book
        _room_give(t, est)
        if le._names_constraint(exc, "mirror_orders_one_open_per_book"):
            _mirror_stop("open_order_pending", w)
            return "open_order_pending"
        raise
    slot.commit()
    o = {"id": int(row_id), "book_id": book["id"], "whale": w, "us_market_slug": slug,
         "kind": kind, "side": side, "tif": tif_rec, "post_only": post_only,
         "his_level": his_px, "price": (p.price if p is not None else wire), "wire": wire,
         "qty": int(qty), "order_id": None, "state": "placing", "filled": 0.0,
         "booked_filled": 0.0, "avg_px": None, "taker_at_placement": False,
         "pre_ids": pre_ids, "placed_ts": t.now, "reason": reason_col, "intent": wire_intent}
    if est:
        le._REST_RESERVED_USD = float(le._REST_RESERVED_USD or 0.0) + est
    try:
        try:
            resp = await _guarded(t, o["id"], t.pmus.submit_fok, slug, wire, int(qty), sell,
                                  venue_tif, intent, post_only, good_till)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the RESPONSE is lost, not the order
            if ms.is_rate_limit(exc):
                # NOT lost: the venue REFUSED the request before it
                # processed anything (E2 review round 4, HIGH-1) -- the
                # SDK's RateLimitError raised by a BUY's preview
                # (submit_fok wraps only the create) or by an IOC take's
                # create (post_only False: the 4xx wrapper does not
                # apply); the same for any raise is_rate_limit matches
                return await _place_rate_limited(t, o, book, r, exc, est)
            return await _lost_response(t, o, book, r, exc)
    finally:
        if est:
            le._REST_RESERVED_USD = max(0.0, float(le._REST_RESERVED_USD or 0.0) - est)
    resp = resp if isinstance(resp, dict) else {}
    status = str(resp.get("status") or "")
    oid = resp.get("order_id")
    raw = resp.get("raw") or {}
    if status == "post_only_rejected":
        code = (raw or {}).get("status_code")
        # the venue's SECOND refusal shape is a 200 whose order came
        # back REJECTED (the adapter's _post_only_cross): the venue
        # minted an order there, so the rejected row names it -- the
        # 400 shape has none and the row keeps NULL, as before
        await t.pool.execute(_SQL_ORDER_STATE, o["id"], "rejected", status, "post_only_rejected",
                             None, (str(oid) if oid else None))
        await t.pool.execute(_SQL_ORDER_REASON, o["id"], f"post_only_rejected:{code}")
        _mirror_stop("post_only_rejected", w)
        # THE RULE READS THE WHOLE RAW DICT, never the bare code alone:
        # the crossing refusal comes in two shapes (an HTTP 400; a 200
        # with post_only_cross True and execution_type REJECTED), and
        # only the dict carries the second one's facts. A raw that is
        # not a dict is read as the bare code it always was (to-a-tee
        # program Phase 7 rung 1, owner order 2026-09-02 "mirror the
        # whales to a tee"; wave 2b, the worker seam)
        if rules.take_arms(raw if isinstance(raw, dict) else code):
            # the statement's COALESCE: the first arm of this crossing
            # spell stands, so the clock the take rule reads is the
            # first refusal's, not this tick's
            await t.pool.execute(_SQL_BOOK_ARM, book["id"], True)
            book["take_armed_ts"] = book.get("take_armed_ts") or t.now
        if _raw_rate_limit(raw):
            # the venue's 429 on the create, by the raw's NAMED fields
            # (status_code / error_type / the error's head; round 4,
            # MEDIUM-2): the circuit, then the abandon that skips the
            # backoff while the circuit holds
            _rate_limited(t, w, f"placement {slug}")
            _abandon(t, "rate_limited", rate_limited=True)
        _recent(book["id"], "post_only_rejected", code=code, order=(str(oid) if oid else None))
        _room_give(t, est)              # nothing rests: the room is back for this tick
        return "post_only_rejected"
    if not oid:
        # the row keeps the adapter's raw (U13): the guard's expected
        # and venue figures, the venue's preview, its `why`
        await t.pool.execute(_SQL_ORDER_REFUSED, o["id"], status,
                             f"place_refused:{status or 'no_id'}", _refusal_receipt(raw))
        _mirror_stop(f"place_refused:{status or 'no_id'}", w)
        if _raw_rate_limit(raw):
            # by the raw's named fields alone, never '429' anywhere in
            # its text (round 4, MEDIUM-2: a preview_mismatch whose
            # expected_cost is 429.0 is a mismatch, not a rate limit)
            _rate_limited(t, w, f"placement {slug}")
            _abandon(t, "rate_limited", rate_limited=True)
        _room_give(t, est)
        return f"place_refused:{status}"
    # PERSIST THE ID BEFORE ANYTHING ELSE: the un-orphanable line
    await t.pool.execute(_SQL_ORDER_PERSIST_ID, o["id"], str(oid), status,
                         json.dumps(raw, default=str))
    o.update(order_id=str(oid), state="open")
    await t.pool.execute(_SQL_BOOK_OPEN_ORDER, book["id"], o["id"])
    book["open_order_id"] = o["id"]
    t.nonterminal.add(book["id"])
    t.placed_books.add(book["id"])              # E6: the outcome class, the quiet verdict
    if is_take:
        _mirror_stop("take_placed", w)
        t.stats["placed_take"] += 1
        if take_first:
            _mirror_stop("take_first", w)
        if action == "reduce":
            # an exit's IOC, counted where it is SENT (E4 review LOW-4:
            # counted at the decision it named a take an ops-capped
            # cancel never let out)
            _mirror_stop("exit_take", w)
            if wire_intent == "ORDER_INTENT_SELL_SHORT":
                _mirror_stop("short_cover_take", w)      # S4: the cover's IOC
    else:
        _mirror_stop("flatten_rested" if kind in ("flatten_paired", "flatten_vanished")
                     else "rest_placed", w)
        t.stats["placed_rest"] += 1
        if wire_intent == "ORDER_INTENT_SELL_SHORT":
            _mirror_stop("short_cover_rest", w)          # S4: the cover's rest at floor(his)
    if wire_intent == ORDER_INTENT_SHORT:
        # the short side's own names beside the lane's (brief G4): an
        # OPEN onto a flat book, an ADD onto a held one
        _mirror_stop("short_open" if _leg_of(book) < 1 else "short_add", w)
    # the take's arm is consumed by the IOC and contradicted by a rest
    # the venue accepted (the book was not crossing after all)
    await _disarm_take(t, book)
    # this tick's own placements came off this tick's room readings
    # BEFORE the venue call (_room_take, E2), so two books in one tick
    # cannot each spend the last clip
    _recent(book["id"], "placed", kind=kind, side=side, wire=wire, qty=int(qty), tif=tif_rec,
            intent=(wire_intent if wire_intent in (ORDER_INTENT_SHORT, "ORDER_INTENT_SELL_SHORT")
                    else None))
    filled = float(_num(resp.get("filled_shares")) or 0.0)
    if is_take:
        # THE IOC CONSUMES WHAT IT FILLED AND NO MORE (E4 addendum 3):
        # the room was taken for the whole quantity before the call; the
        # part the venue did not fill rests nowhere, so it goes back
        # before the remainder's rest is sized -- one reservation for
        # the plan, never two. The plan carries the figures for the
        # caller that rests the remainder
        plan["take_qty"], plan["take_filled"] = int(qty), filled
        if est:
            _room_give(t, max(0.0, float(qty) - filled) * _cost_px(wire, book))
    if filled > 0:
        st = {"state": status, "filled_shares": filled, "avg_px": resp.get("fill_price")}
        await _book_delta(t, o, book, st, maker=False, taker_at_placement=True)
        o["taker_at_placement"] = True
        if post_only:
            _mirror_stop("post_only_ignored", w)
            _POST_ONLY_OK = False
            t.stats["status"] = "degraded"
            t.stats["post_only"] = False
            log.error("mirror_live: the venue IGNORED post-only on order %s (%s filled at "
                      "create); the flag is off for this process", oid, filled)
    if is_take or filled >= float(qty) - FLAT_TOL_SHARES or le._rest_terminal(
            {"state": status} if status else None):
        st = {"state": status or ("filled" if filled >= qty else "cancelled"),
              "filled_shares": filled, "avg_px": resp.get("fill_price")}
        await _finish_order(t, o, book, st, kind)
        return "take" if is_take else "filled_at_create"
    t.open_by_book[book["id"]] = (o, {"state": status, "filled_shares": filled,
                                      "leaves": float(qty) - filled})
    return "rest_placed"


async def _place_rate_limited(t: _Tick, o: dict, book: dict, r: _Reading, exc: BaseException | None,
                              est: float, raw: dict | None = None) -> str:
    """A venue 429 RAISED by the placement (E2 review round 4, HIGH-1):
    the SDK's RateLimitError out of a BUY's preview -- pmus.submit_fok
    wraps only the create -- or out of an IOC take's create (sent with
    post_only False, so the create's 4xx wrapper does not apply). It
    crossed _guarded into _place_reserved's except and was read as a
    LOST response: no `rate_limited`, no circuit, one MORE paced read
    into the limited venue (the open-orders search) and the book
    frozen `placement_lost` on a 'placing' row -- for twenty minutes,
    though nothing was ever sent. A 429 is the venue REFUSING the
    request before it processed it: nothing rests, so it is a refusal,
    handled as the post-only create's 429 is -- named `rate_limited`
    (the census, the pacer's circuit), the row refused
    `place_refused:rate_limited` with a receipt naming the raise, the
    room given back, and the tick abandoned `rate_limited` (mid-tick
    consistency; the backoff is skipped while the circuit holds). No
    freeze, no open-orders search. The op stays spent: a request went
    out, exactly as on the create's 429.

    THE FLATTEN'S TWO PLACEMENTS COME HERE TOO (round 5, c2/c3): the
    co-held IOC's create raising the SDK's RateLimitError (`exc`), and
    the sole-holder close whose adapter caught it into a `close_failed`
    raw naming `error_type` RateLimitError (`raw`, no exception: the
    receipt is the adapter's raw and the name its `error_type`). Both
    were lost responses on v5 -- the exit book frozen `placement_lost`
    for twenty minutes over a request the venue refused before it
    processed anything -- against the owner's order never to freeze a
    book for a refusal the venue named. The flatten reserves no room
    (`est` 0.0), so the give-back is a no-op there."""
    w, slug = r.whale, r.slug
    if exc is not None:
        name, text = type(exc).__name__, str(exc)[:200]
        receipt = _refusal_receipt({"error": text, "error_type": name,
                                    "status_code": getattr(exc, "status_code", None)})
    else:
        raw = raw if isinstance(raw, dict) else {}
        name, text = str(raw.get("error_type") or "close_failed"), str(raw.get("error") or "")[:200]
        receipt = _refusal_receipt(raw)
    log.warning("mirror_live: placement on %s refused by the venue's rate limit (%s: %s)", slug,
                name, text)
    _rate_limited(t, w, f"placement {slug}")
    await t.pool.execute(_SQL_ORDER_REFUSED, o["id"], "rate_limited", "place_refused:rate_limited",
                         receipt)
    _mirror_stop("place_refused:rate_limited", w)
    _recent(book["id"], "place_refused", status="rate_limited", raised=name)
    _room_give(t, est)
    _abandon(t, "rate_limited", rate_limited=True)
    return "place_refused:rate_limited"


async def _lost_response(t: _Tick, o: dict, book: dict, r: _Reading, exc: BaseException) -> str:
    """The placement raised: search the book by fingerprint with pre_ids,
    the protected set and every ledger id excluded; found -> adopt; not
    found -> the row stays 'placing' with no id and the book is frozen
    'placement_lost' for step O to revisit. A raise that names the
    venue's rate limit never reaches here: the caller routes it to
    _place_rate_limited (round 4, HIGH-1), a refusal, not a loss. A
    short cover's placement comes here like every other (S4 review,
    F4): its row is an ordinary GTC / IOC row, the fingerprint matches
    it by its wire intent, and a rest the search cannot find freezes
    the book `placement_lost` -- never silently live with a cover
    standing unmanaged on the venue."""
    # the text too: for a CLOSE row it is the adapter's close_failed
    # error (pmus.close_position), the only record of the venue's reason
    log.warning("mirror_live: placement on %s raised %s (%s); searching the book", r.slug,
                type(exc).__name__, str(exc)[:200])
    if o.get("tif") == "CLOSE":
        # a close carries no cent and no quantity to search by: the
        # next tick reads the venue's position (_reconcile_lost_close)
        await t.pool.execute(_SQL_BOOK_OPEN_ORDER, book["id"], o["id"])
        t.nonterminal.add(book["id"])
        await _freeze(t, book, "placement_lost", {"raised": type(exc).__name__, "close": True})
        return "placement_lost"
    try:
        # a venue READ, behind the pacer like every other (step-9 review)
        orders = list(await _venue_read(t, t.pmus.open_orders, [r.slug]) or [])
    except Exception:  # noqa: BLE001 — unreadable is not "not found"
        orders = None
    if orders is not None:
        verdict, what = await _find_lost_placement(
            t, o, book, orders, (t.now - le._ORPHAN_SKEW_S, t.now + le._ORPHAN_MATCH_S))
        if verdict == "found":
            oid = str(what.get("order_id"))
            await t.pool.execute(_SQL_ORDER_ADOPT, o["id"], oid,
                                 _adopt_reason(o, "adopted after a lost response"))
            o.update(order_id=oid, state="open", reason=_adopt_reason(o, "adopted after a lost response"))
            await t.pool.execute(_SQL_BOOK_OPEN_ORDER, book["id"], o["id"])
            book["open_order_id"] = o["id"]
            t.nonterminal.add(book["id"])
            _recent(book["id"], "adopted", order=oid)
            t.open_by_book[book["id"]] = (o, {"state": what.get("state"),
                                              "filled_shares": what.get("filled_shares"),
                                              "leaves": what.get("leaves")})
            _mirror_stop("rest_placed", r.whale)
            t.stats["placed_rest"] += 1
            t.placed_books.add(book["id"])
            await _disarm_take(t, book)
            return "rest_placed"
        if verdict == "ambiguous":
            await _freeze(t, book, "lost_ambiguous", {"candidates": what})
            return "lost_ambiguous"
    await t.pool.execute(_SQL_BOOK_OPEN_ORDER, book["id"], o["id"])
    t.nonterminal.add(book["id"])
    await _freeze(t, book, "placement_lost", {"raised": type(exc).__name__})
    return "placement_lost"


async def _flatten_vanished(t: _Tick, book: dict, r: _Reading, p: mi.Plan, his_px, plan: dict,
                            kind: str = "flatten_vanished") -> str:
    """Step F: he has LEFT the market (or the admin forced it). Rest
    the SELL at his equivalent for rules.MIRROR_FLATTEN_REST_S first; then
    mirror_exit's rules verbatim: sole holder -> close_position with
    EXIT_SLIPPAGE_BIPS; co-held -> one IOC at sell_limit_price(bid),
    refused by name when the bid is unreadable.

    ON A SHORT BOOK (S4, 2026-09-07) the flatten is the COVER, a priced
    BUY of the long token with the closing intent through _act and
    _place -- never close_position, which the venue refuses as an
    unpriced limit order ("Price is required for limit order"). With
    his buy-back price it is _act's priced cover (the rest at floor(his),
    the IOC at the ceiling cent). With NO price of his the rule cannot
    apply and the book is held `no_price` under `exit_px_src: 'none'`,
    with ONE exception: a vanish (he is gone) or the sign flip (his
    position on our side is gone) covers by one IOC at the ask bounded
    by le.buy_limit_price (the long flatten's own slippage, mirrored) for
    our quantity. Every cover passes the read-back gate first
    (_s4_refusal)."""
    w = r.whale
    short = _book_short(book)
    if kind == "flatten_vanished":
        _mirror_stop("flatten_vanished", w)
        plan["flatten"] = "vanished"
    else:
        plan["flatten"] = "paired"
    if _leg_of(book) < 1:
        return f"{kind}: flat"
    if short:
        # THE ROW A SHORT'S CLOSE WRITES NAMES SELL_SHORT, and only the
        # 050 INSERT can carry it: through the 047 statement the column
        # would read its DEFAULT, BUY_LONG, and the next day read would
        # count the cover's proceeds as a long BUY against the day rail
        # (P2 rung S0 review). While the column is absent the flatten is
        # refused by name and the book held, never mislabelled
        refusal = _s4_refusal(t, book, kind, plan, w)
        if refusal is not None:
            return refusal
        if _exit_terms(t, book, BUY, his_px, plan) is not None:
            # HIS BUY-BACK PRICE IS KNOWN: the priced cover, every tick
            # through _act (a standing BUY_SHORT add is replaced by it)
            return await _act(t, book, r, p, kind, his_px, plan) or "flatten_rested"
        if not (kind == "flatten_vanished" or plan.get("sign_flip") is True):
            # paired out with no price of his and his position on our
            # side still there: held, named, never guessed
            _mirror_stop("no_price", w)
            plan["cover"] = "unpriced_held"
            log.warning("mirror_live: book %s short flatten held: no buy-back price of his "
                        "(exit_px_src none)", book["id"])
            return "no_price"
        ask = _num(r.ask)
        if ask is None or not (0.0 < ask < 1.0):
            _mirror_stop("no_bid_for_flatten", w)
            return "no_bid_for_flatten"
        # a resting BUY_SHORT add is cancelled first: a rest that filled
        # after the cover would reopen the leg (exempt from the budget)
        await _cancel_open_for(t, book, kind, exit=True)
        if book["id"] in t.open_by_book:
            return "cancel_pending"
        if t.cancel_all:
            return t.cancel_all
        if t.abandoned:
            _mirror_stop("abandoned_in_flight", w)
            return "tick_abandoned"
        limit = le.buy_limit_price(float(ask))
        plan["cover"] = {"ioc_at_ask": ask, "limit": limit, "why": "vanished" if kind == "flatten_vanished"
                         else "sign_flip"}
        qty = _cover_qty(book, _leg_of(book))
        if qty < 1:
            _mirror_stop("under_one_share", w)
            return "under_one_share"
        return await _place(t, book, r, kind, BUY, limit, qty, his_px, p, plan, tif="IOC")
    else:
        if _exit_terms(t, book, p.side, his_px, plan) is not None:
            # HIS EXIT PRICE IS KNOWN (E4): the rest stands at his cent
            # and the take fires the tick the bid is within the
            # tolerance of it -- every tick, through _act. The slippage
            # leg below (critic C16: close_position / the IOC at the bid
            # less slippage after MIRROR_FLATTEN_REST_S) would sell past
            # the cent, so it never runs on a priced vanish: a market
            # that does not come back within a cent of him is HELD (the
            # owner's order), not slipped. It still runs for a vanish he
            # gave no price for and for the admin flatten
            # (`exit_px_src: 'none'`)
            return await _act(t, book, r, p, "flatten_vanished", his_px, plan) or "flatten_rested"
        # the reference rest is one of THIS vanish (the plan's vanish_since,
        # set by _tick_book): with none, the vanish begins now and rests
        vanish_since = _num(plan.get("vanish_since"))
        vanish_since = t.now if vanish_since is None else vanish_since
        try:
            since = _num(await t.pool.fetchval(_SQL_FLATTEN_REST_SINCE, book["id"], vanish_since))
        except Exception:  # noqa: BLE001 — unreadable: rest again, never slip
            since = t.now
        if since is None or t.now - since < float(rules.MIRROR_FLATTEN_REST_S):
            return await _act(t, book, r, p, "flatten_vanished", his_px, plan) or "flatten_rested"
        # the rest stood its wait: cancel it, then the slippage path
        await _cancel_open_for(t, book, "flatten_vanished", exit=True)
        if book["id"] in t.open_by_book:
            return "cancel_pending"
        if t.cancel_all:
            return t.cancel_all          # the cancel's booking tripped the tick: nothing more
    if t.abandoned:
        # another book abandoned the tick while this one ran (E2):
        # named, no plan written (see _place)
        _mirror_stop("abandoned_in_flight", w)
        return "tick_abandoned"
    # the op is reserved here and committed at the write (E2): the
    # bid read, the position read and the row INSERT sit between. A
    # flatten's close or IOC is an exit: never `ops_capped` (M-1)
    slot = _op_slot(t, w, exit=True)
    if slot is None:
        return "ops_capped"
    try:
        return await _flatten_send(t, slot, book, r, his_px, plan, kind)
    finally:
        slot.release()


async def _flatten_send(t: _Tick, slot: _OpSlot, book: dict, r: _Reading, his_px, plan: dict,
                        kind: str) -> str:
    """The slippage leg of _flatten_vanished, on a reserved op: sole ->
    close_position, co-held -> one IOC at sell_limit_price(bid). A LONG
    book's leg alone since S4: a short book's flatten is its priced
    cover (_flatten_vanished, _act) and never reaches here."""
    w = r.whale
    short = _book_short(book)
    if short:
        _mirror_stop("book_error", w)
        log.error("mirror_live: book %s (short) reached the close_position leg; the cover is a priced "
                  "order (S4)", book["id"])
        return "book_error"
    # the LEG, whole shares, after the cancel's booking: what a close
    # must account for on either sign
    ledger = _leg_of(book)
    if ledger < 1:
        return f"{kind}: flat"
    # THE SLIPPAGE LEG READS THE VENUE'S STATE BEFORE IT READS A BID.
    # The co-held IOC prices off slug_bid, which reads through
    # _bbo_quotes -- a feed that carries no market state -- so a CLOSED
    # market with a stale resting book (the 2026-09-05 probe: bid 0.01,
    # ask 0.20 on a settled CFB market) yielded a tradeable bid and an
    # IOC SELL went out on it in the same tick whose quote read had
    # counted the slug venue_halted; the sole-holder branch sent
    # close_position with no quote read at all (review of U9,
    # 2026-09-06). The tick's OWN read of this slug decides, by the
    # name _bbo gave it: a state that is present and not OPEN refuses
    # here, before the position read and before either order, and the
    # tick after the venue reads OPEN takes the leg as before. A state
    # of None (the read failed, or the payload carried none) is not a
    # refusal, exactly as it is not one in _bbo; the bid read below
    # still refuses a book it cannot price.
    if r.venue_state is not None and r.venue_state != _STATE_OPEN:
        _mirror_stop("venue_halted", w)
        return "venue_halted"
    close_bips = int(le.EXIT_SLIPPAGE_BIPS)
    try:
        held, _avg = await _pm_held(t, r.slug)
    except Exception as exc:  # noqa: BLE001 — cannot size: refuse, retry next tick
        _mirror_stop("no_bid_for_flatten", w)
        log.warning("mirror_live: venue position for %s unreadable (%s)", r.slug, type(exc).__name__)
        return "no_bid_for_flatten"
    # THE ONE ORDER THIS WORKER SENDS WITH NO CLAMP TO ITS OWN BOOK.
    # close_position closes the WHOLE slug, so "am I the sole holder" must
    # be certain, and it was decided by `ledger >= int(held)`. Two things
    # were wrong with that. _pm_held returns int(qty) -- it FLOORS the
    # venue's number before we ever see it -- so a foreign holding of any
    # fraction under one share read as sole and our close took it with us:
    # deterministic, no race. And the unfloored number was already in
    # hand: step R's paced walk keeps fractions (r.venue), so no second
    # whole-account walk is needed to see it (the step-9 re-review removed
    # exactly such a walk from the lost-close path; it is not coming back).
    # Sole now needs BOTH readings to say so -- the tick's fractional walk
    # and this fresh floored read -- and a disagreement between two
    # independent sources is not a reading of the account.
    # AND THE EXPLAINED-SHARE QUERY IS DELIBERATELY NOT FED IN HERE.
    # R7's text asks for it (`_SQL_MANUAL_SHARES`, widened or not); it
    # must not be done, and this is written so that a later builder does
    # not "complete" the unit. Both readings compare the ledger against
    # the TOTAL venue holding, which already includes any foreign shares,
    # so a foreign holding correctly reads NOT sole today. Subtracting
    # the explained shares could only ever make `sole` MORE likely -- and
    # `sole` is what sends `close_position`, the one order this worker
    # sends with no clamp to its own book. That is the exact direction R3
    # exists to close.
    venue_now = math.ceil(abs(float(r.venue)))
    sole_walk = ledger >= venue_now
    sole_read = ledger >= int(held)
    # (The short book's sign clause that sat here -- a mixed-sign co-hold
    # read "sole" by magnitude -- went with close_position: a short's
    # cover is a clamped order of our own quantity, S4.)
    if sole_walk != sole_read:
        # Two readings that disagree are EVIDENCE OF CO-HOLDING, not an
        # unreadable account: the walk keeps the fraction and the fresh
        # read floors it away, so `ledger < venue < ledger + 1` -- exactly
        # the sub-share case -- disagrees on every tick, deterministically
        # and forever. Refusing here would leave the book unable to exit
        # by any route (and the admin flatten inert, since it lands in
        # this same function), so the disagreement means NOT SOLE and
        # falls through to the co-held IOC below, which sells only our
        # own quantity. Counted and logged because a standing
        # disagreement is worth an operator's eye.
        _mirror_stop("flatten_holding_disagrees", w)
        log.warning("mirror_live: %s sole-holder reads disagree (walk %s, held %s, ledger %s): "
                    "treating as co-held", r.slug, r.venue, held, ledger)
    # What this gate does and does not close. The DETERMINISTIC hole is
    # closed: _pm_held floors the venue's number before we see it, so it
    # can never report a foreign fraction, while step R's paced walk keeps
    # it -- the walk is what decides. The residual is that the walk is
    # taken earlier in the tick, so a foreign fraction landing between the
    # walk and here still reads sole; that window is one tick, and the
    # close's own booking catches it afterwards (a fill above our ledger
    # is `overfill`, which freezes the book and trips the live switch).
    sole = sole_walk and sole_read
    # THE QUANTITY, decided BELOW `sole`. The sole close is
    # close_position: it closes the WHOLE slug, fraction included, so it
    # is never clamped to the row and never gated under_one_share -- a
    # sole holder at ledger 1 / row 0.76 sends the close (the withdrawn
    # first cut sat the gate above this decision, and that book could
    # never leave). Its mirror_orders row keeps qty = ledger, so
    # _reconcile_lost_close's `sold = qty - int(held)` is the whole
    # ledger. The co-held IOC sells OUR quantity only: min(ledger,
    # ceil(held)) (_sell_qty), refused under one share by name.
    qty = ledger
    if not sole:
        qty = _sell_qty(book, ledger)
        if qty < 1:
            _mirror_stop("under_one_share", w)
            return "under_one_share"
        # a venue READ, behind the pacer like every other (step-9 review)
        bid = await _venue_read(t, t.pmus.slug_bid, r.slug, True)
        if bid is None or not (0.0 < float(bid) < 1.0):
            _mirror_stop("no_bid_for_flatten", w)
            return "no_bid_for_flatten"
        limit = le.sell_limit_price(float(bid))
    orders = await _read_open(t)
    pre_ids = sorted({str(o.get("order_id")) for o in (orders or [])
                      if o.get("order_id") and str(o.get("us_market_slug") or "").lower() == r.slug.lower()})
    tif_rec = "CLOSE" if sole else "IOC"
    if book["id"] in t.nonterminal:
        _mirror_stop("open_order_pending", w)
        return "open_order_pending"
    # the row: the PLAN side of a reduce on this (long) book and the
    # WIRE intent the exit maps to (brief 3.3); a CLOSE carries no cent
    # of its own
    side = SELL
    ws = rules.wire_side(book.get("intent"), side)
    if ws is None:
        _mirror_stop("book_error", w)
        log.error("mirror_live: book %s carries an intent this lane cannot wire (%r)",
                  book["id"], book.get("intent"))
        return "book_error"
    wire_intent = ws[0]
    args = [book["id"], w, r.slug, kind, side, tif_rec, False,
            None, his_px, (None if sole else limit) or 0.0, (None if sole else limit) or 0.0,
            qty, json.dumps(pre_ids), 0, int(book.get("ledger_net") or 0), r.bid, r.ask, kind]
    try:
        if t.short_col:
            row_id = await t.pool.fetchval(_SQL_ORDER_INSERT, *args, wire_intent)
        else:
            row_id = await t.pool.fetchval(_SQL_ORDER_INSERT_047, *args)
    except Exception as exc:  # noqa: BLE001 — the unique index: one open order per book
        if le._names_constraint(exc, "mirror_orders_one_open_per_book"):
            _mirror_stop("open_order_pending", w)
            return "open_order_pending"
        raise
    slot.commit()
    # the in-memory row carries what the INSERT wrote: a CLOSE has no
    # wire and the column holds 0.0, never None (step-9 review: a None
    # here was a TypeError in the lost-placement search)
    o = {"id": int(row_id), "book_id": book["id"], "whale": w, "us_market_slug": r.slug,
         "kind": kind, "side": side, "tif": tif_rec, "post_only": False,
         "his_level": his_px, "price": 0.0 if sole else limit, "wire": 0.0 if sole else limit,
         "qty": qty, "order_id": None, "state": "placing", "filled": 0.0, "booked_filled": 0.0,
         "avg_px": None, "taker_at_placement": True, "pre_ids": pre_ids, "placed_ts": t.now,
         "reason": kind, "intent": wire_intent}
    try:
        if sole:
            resp = await _guarded(t, o["id"], t.pmus.close_position, r.slug,
                                  slippage_bips=close_bips)
        else:
            resp = await _guarded(t, o["id"], t.pmus.submit_fok, r.slug, limit, qty, True,
                                  "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL", ORDER_INTENT)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        if ms.is_rate_limit(exc):
            # the co-held IOC's create raised the SDK's RateLimitError
            # (sell=True: no preview, post_only False, no 4xx wrapper):
            # the venue's refusal by name, never a lost response and
            # never a freeze (round 5, c3) -- the same road as
            # _place_reserved's
            return await _place_rate_limited(t, o, book, r, exc, 0.0)
        return await _lost_response(t, o, book, r, exc)
    resp = resp if isinstance(resp, dict) else {}
    oid = resp.get("order_id")
    status = str(resp.get("status") or "")
    filled = float(_num(resp.get("filled_shares")) or 0.0)
    if oid:
        await t.pool.execute(_SQL_ORDER_PERSIST_ID, o["id"], str(oid), status,
                             json.dumps(resp.get("raw") or {}, default=str))
        o.update(order_id=str(oid), state="open")
    if filled > 0:
        # booked = min(filled, ledger) is the primitive's own ceiling
        await _book_delta(t, o, book, {"state": status, "filled_shares": filled,
                                       "avg_px": resp.get("fill_price")}, maker=False,
                          taker_at_placement=True)
    st = {"state": status or ("filled" if filled >= qty else "cancelled"),
          "filled_shares": filled, "avg_px": resp.get("fill_price")}
    if not oid:
        if sole and status == "close_failed":
            raw = resp.get("raw") or {}
            if _raw_rate_limit(raw):
                # the adapter caught the SDK's RateLimitError into this
                # raw (`error_type`, `error`; no int status): the venue
                # REFUSED the close before it processed anything, so
                # nothing may have executed -- the named refusal, the
                # circuit, the book held live for the next tick's
                # flatten; never the lost-close freeze (round 5, c2)
                return await _place_rate_limited(t, o, book, r, None, 0.0, raw=raw)
            # the adapter turns an exception INSIDE the close call into
            # this status (pmus.close_position); a request that timed
            # out may have executed at the venue, so it is the lost
            # response of the CLOSE row, never a refusal to retry
            return await _lost_response(t, o, book, r, RuntimeError(
                str(raw.get("error") or "close_failed")[:80]))
        await t.pool.execute(_SQL_ORDER_REFUSED, o["id"], status,
                             f"place_refused:{status or 'no_id'}",
                             _refusal_receipt(resp.get("raw") or {}))
        _mirror_stop(f"place_refused:{status or 'no_id'}", w)
        return f"place_refused:{status}"
    await _finish_order(t, o, book, st, kind)
    _recent(book["id"], "flattened", how="close_position" if sole else "ioc", filled=filled)
    return kind


# ----------------------------------------------------- step A: admission

async def _tick_candidate(t: _Tick, whale: str, cid: str, ctx: dict | None = None) -> str | None:
    """A market with no book: read everything admission wants, refuse
    by the first name, else open the book and plan it this tick.

    RETURNS THE NAME OF EVERY EXIT (W2 / P2), None only when a book
    opened: the census name `_mirror_stop` counted, or `book_seen` for
    a market that already has a book (counted nowhere, as before). The
    four exits that returned silently are named `long_token_unknown`,
    `target_zero` and `book_row_unreadable` here and `cand_unread_capped`
    in the walk. `ctx`, when given, collects what the candidate read
    before it left (slug, long token, his net, target, mark, his level,
    ask, band, the book counts) for the refusal row _walk_candidate
    writes; nothing is read for it."""
    w = whale
    d = ctx if ctx is not None else {}
    fills = await ms.his_fills(t.pool, w, cid)
    _count_fills_dedup(t)
    # the shadow's own mapper, venue module and all (C1): ledger, premap,
    # then the copy lane's exact steps -- paced, cached per market,
    # bounded per tick by t.map_budget
    mo: dict = {}
    m = await ms.map_market(t.pool, fills, t.pmus, whale=w, condition_id=cid,
                            budget=t.map_budget, out=mo)
    for _ in range(int(mo.get("venue_reads") or 0)):
        _mirror_stop("map_venue_read", w)
        _venue_call(t, guard=True)      # a resolver call is a candidate's venue call (E2)
    for _ in range(int(mo.get("cache_hit") or 0)):
        _mirror_stop("map_cache_hit", w)
    if not m:
        if mo.get("refusal") == "map_reads_capped":
            # no verdict this tick: never TTL-skipped, read again next tick
            _mirror_stop("map_reads_capped", w)
            return "map_reads_capped"
        _mirror_stop("unmapped", w)
        _memo_unmapped(t, w, cid, fills)    # E7: the TTL grows while nothing of his lands
        return "unmapped"
    src = str(m.get("source") or "")
    if src not in MIRROR_LIVE_MAP_SRC and src != "grammar":
        # a mapping class no lane has certified opens no book, whatever
        # the quarantine says (MIRROR_LIVE_MAP_SRC); the shadow measures it
        _mirror_stop("map_source_unverified", w)
        _memo_unmapped(t, w, cid, fills)
        return "map_source_unverified"
    slug, la, oa = m["us_slug"], m["long_asset"], m.get("other_asset")
    d.update(slug=slug, long_asset=la)
    if m.get("long_from"):
        # the mapper already named the long token off the catalogue
        # (ms._long_from_catalogue); the row says so (W2 review LOW-1)
        d["long_from"] = str(m["long_from"])
    if src == "grammar":
        # the class's own certification first (venue-only truth, the
        # probation, the trip): a refusal here is named and, except the
        # probation -- which is another book's wait, not this market's
        # verdict -- TTL-skipped like an unmapped market
        held = await _grammar_admission(t, w, slug, mo.get("grammar"))
        if held:
            _mirror_stop(held, w)
            if held != "grammar_probation":
                _memo_unmapped(t, w, cid, fills)
            return held
    if not la:
        # THE LONG TOKEN HIS FILLS NEVER TOUCHED (W2 / P1, class B of
        # gap_planned_unopened.md: $26.9k proven, up to $79k). Every
        # fill of his is on the venue's SHORT-side token, so the mapper
        # named no long token (_choose_long's other_of over HIS tokens)
        # and this exit was silent, every tick, until he bought the
        # other token -- 14 of 22 mapped markets on 2026-09-07 opened
        # that way. The catalogue names it by identity, exactly as the
        # OTHER token is read below for a long-only fill: the
        # condition's sibling of the token in hand (ms.map_market fills
        # it the same way, so the shadow's row agrees). Named, the
        # EXISTING short road runs unchanged from here: the signed
        # target, his other-token BUY at 1 - p, BUY_SHORT behind
        # short_model_confirmed and _short_gate. A catalogue that does
        # not name it refuses `long_token_unknown` and memoises the
        # market for the unmapped TTL: never a guess, never a token the
        # catalogue does not name.
        if oa:
            try:
                la = await t.pool.fetchval(_SQL_SIBLING_TOKEN, cid, oa)
            except Exception:  # noqa: BLE001 — an unreadable catalogue names no token
                la = None
            la = str(la) if la else None
        if not la:
            _mirror_stop("long_token_unknown", w)
            _memo_unmapped(t, w, cid, fills)
            return "long_token_unknown"
        d.update(long_asset=la, long_from="catalogue")
    if (w, cid) in t.books_seen:
        return "book_seen"
    memo_key = le._us_game_key(slug)
    if memo_key and _game_full_until.get(("game", str(memo_key)), 0.0) > t.now:
        # the game read FULL inside the last GAME_FULL_MEMO_S (E1
        # re-review LOW-2): no venue read and no candidate slot spent
        # on it until the memo runs
        _mirror_stop("cand_game_full_skipped", w)
        return "cand_game_full_skipped"
    if not oa:
        # his fills never touched the other outcome: the market names
        # it, and the book must know both tokens (the underdog referee
        # reads either, mirror_exit's _mirror_owns_asset reads either)
        try:
            oa = await t.pool.fetchval(_SQL_SIBLING_TOKEN, cid, la)
        except Exception:  # noqa: BLE001 — unknown sibling: the referees read the long token
            oa = None
        oa = str(oa) if oa else None
    r = await _read_market(t, w, cid, slug, la, oa, fills)
    d.update(mark=r.mark, ask=r.ask)
    if t.slug_states.get(slug) in ms.STATE_TERMINAL:
        # the market has ended (this read's own state, as _bbo recorded
        # it): remembered for the TTL so the next ticks' slots go to
        # markets that can open a book (D1). Only a candidate's read
        # writes this; HALTED/SUSPENDED never do (they reopen)
        _terminal_until[(w, cid)] = t.now + ms.UNMAPPED_TTL_S
    if t.abandoned:
        return "tick_abandoned"
    shorts = _shorts_on(t)
    drift, _drift_src = _drift_for(r)
    net, _snap_net = _net_for(r, drift, short=shorts)
    d["his_net"] = net
    # THE RATIO IS DECIDED HERE, AT OPEN, AND STORED ON THE BOOK (owner
    # orders 2026-09-06 ~14:00Z and ~14:10Z; rules.open_ratio): an exact
    # copy under MIRROR_SMALL_BET_USD of his dollars at the mark, else
    # MIRROR_RATIO. The shadow's reading (t.ratios) stays what
    # refresh_ratios measured -- reported, its anchor stored beside the
    # book as a diagnostic -- and no longer sizes anything live. The
    # SIZE clip is the mirror lane's own (MIRROR_CLIP_USD, at or over
    # the anchor so the ratio is stored unscaled); the copy lane's
    # per-whale clip stays the ADMISSION fact below (a demoted whale,
    # $0, opens no book: `clip_zero`), never the size
    ratio = rules.open_ratio(net, r.mark)
    anchor = (t.ratios.get(w) or {}).get("anchor_usd")
    clip = le.per_fill_usd(w, slug)
    # THE GAME'S ROOM AT OPEN (E1): a new book on a game whose other
    # books already hold the $2,500 opens nothing this tick (an increase
    # of 0, `game_cap_full`; read again next tick), and one on a game
    # with part of it left is sized at that part (`game_cap_scaled`) --
    # the same reading _tick_book makes for the book once it exists
    game_key = le._us_game_key(slug)
    cap = float(rules.MIRROR_NET_CAP_USD)
    exposure = _game_exposure(t, {"whale": w, "game_key": game_key, "id": None})
    room = rules.game_room(exposure, cap)
    if room <= 0:
        # full, or unreadable (told apart, re-review LOW-3). A FULL
        # game's un-opened markets skip their read for GAME_FULL_MEMO_S
        # (re-review LOW-2; the memo is checked before the read above);
        # an UNREADABLE game is never memoised -- its cause is a
        # sibling's order nobody could read this tick, which step O
        # retries next tick, so the market is read again then
        if exposure is None:
            _mirror_stop("game_unreadable", w)
            return "game_unreadable"
        _mirror_stop("game_cap_full", w)
        if game_key:
            _game_full_until[("game", str(game_key))] = t.now + GAME_FULL_MEMO_S
        return "game_cap_full"
    tg = rules.mirror_target(ratio, net, r.mark, rules.MIRROR_CLIP_USD,
                             cap_usd=min(room, cap), allow_short=shorts)
    d["target"] = tg.get("target")
    if tg.get("refusal"):
        _mirror_stop(tg["refusal"], w)
        if tg["refusal"] == "no_mark":
            # E7: an OPEN market with no mark is remembered for
            # NO_MARK_TTL_S (released by his next fill); a non-OPEN or
            # unread state writes nothing (_memo_no_mark)
            _memo_no_mark(t, w, cid, r)
        return str(tg["refusal"])
    if room < cap:
        full = rules.mirror_target(ratio, net, r.mark, rules.MIRROR_CLIP_USD,
                                   cap_usd=cap, allow_short=shorts)
        if int(full["target"] or 0) != int(tg["target"]):
            _mirror_stop("game_cap_scaled", w)
    target = int(tg["target"])
    # the short side's share cap (rules.MIRROR_SHORT_MAX_SHARES, ONE
    # until rung S5): a negative target is clamped toward zero and named
    target = _short_capped(t, target, w)
    d["target"] = target
    if target == 0:
        # nothing to hold: no book. Named since W2 / P2 (it was a
        # silent exit: ratio x net rounding to nothing, every tick)
        _mirror_stop("target_zero", w)
        return "target_zero"
    # A NEGATIVE TARGET IS A SHORT BOOK (P2 rung S0, brief 3.2): his
    # net is negative on the mapped market and the knob is on. Its
    # level is his most recent move DOWN in long-token terms -- his
    # other-token BUY at 1 - p, or his long-token SELL at p -- read the
    # way a long book's reduce reads it; the model must be armed
    short = target < 0
    if short and not le.short_model_confirmed():
        _mirror_stop("short_model_disarmed", w)
        return "short_model_disarmed"
    his_px = _his_level(fills, la, oa, reducing=short, short=short)
    d["his_px"] = his_px
    if his_px is None or not (0.0 < his_px < 1.0):
        _mirror_stop("no_price", w)
        return "no_price"
    mk = r.market
    if mk is None:
        # A MARKETS ROW THAT IS ABSENT OR COULD NOT BE READ IS THE NAMED
        # REFUSAL, decided here: rules.admission fails closed on a None
        # closed/resolved fact, but under its own name, `market_closed`,
        # and an unreadable read once counted as a closed market on the
        # census -- the reader could not tell a database blip from a
        # settled game. The existing-book path names the same reading
        # `market_unreadable` (step M) and the candidate now does too;
        # the rules module is not this worker's to change (step-9
        # re-review; owner order 2026-09-02, "go for it, let's get this
        # working")
        _mirror_stop("market_unreadable", w)
        return "market_unreadable"
    ok, why = await _admit_source(t, w, m.get("source"), slug)
    edge_ok, edge_why = edge_gate.verdict(w)
    his_slug = next((f.get("market_slug") for f in fills if f.get("market_slug")), None)
    clause = _mirror_cell(w, his_slug, his_px)
    legacy = slug_recent = underdog = kalshi = None
    try:
        legacy = bool(await t.pool.fetchval(_SQL_LEGACY_ROW, la, slug, game_key,
                                            f"%{game_key}%" if game_key else "%"))
        slug_recent = bool(await t.pool.fetchval(_SQL_SLUG_RECENT, slug))
        underdog = bool(await t.pool.fetchval(_SQL_UNDERDOG, [a for a in (la, oa) if a]))
    except Exception as exc:  # noqa: BLE001 — unreadable referees refuse by name
        log.warning("mirror_live: admission referees for %s unreadable (%s)", slug, type(exc).__name__)
    # the KALSHI claim reads the token we are ON: his other token on a
    # short book (brief F5); a short book with no sibling id cannot
    # name it, so the claim reads as unreadable and refuses
    try:
        if short and not oa:
            kalshi = None
        else:
            kalshi = bool(await t.pool.fetchval(_SQL_KALSHI, oa if short else la))
    except Exception:  # noqa: BLE001 — unreadable: claimed
        kalshi = None
    # the executor's own band (live_executor reads the same env with
    # the same default at its side check), parsed the one way a string
    # from the environment is parsed on this path
    band = rules._env_float("LIVE_SIDE_PRICE_BAND")
    band = 0.15 if band is None else band
    d["band"] = band
    side_band_hit = None
    if r.ask is not None:
        side_band_hit = abs(float(r.ask) - float(his_px)) > band
    books_live = opened_today = None
    try:
        row = await t.pool.fetchrow(_SQL_BOOKS_COUNT)
        if row:
            books_live, opened_today = int(row["live"] or 0), int(row["today"] or 0)
    except Exception:  # noqa: BLE001
        pass
    d.update(books_live=books_live, opened_today=opened_today)
    se, err = await _state(t.pool, _STATE_SIDE_ECHO)
    first_fill_ok = None
    if err is None and isinstance(se, dict):
        try:
            first_fill_ok = int(se.get("ok", 0)) >= 1
        except (TypeError, ValueError):
            first_fill_ok = None
    elif err is None and se is None:
        first_fill_ok = False
    facts = rules.AdmissionFacts(
        increases_ok=_increases_refusal(t, w) is None,
        increases_refusal=_increases_refusal(t, w) or "mode_env_off",
        per_fill_usd=clip, family=copy_sports.mirror_family_of(slug),
        per_side=bool(m.get("per_side", False)),
        market_closed=mk["closed"], market_resolved=mk["resolved"],
        game_too_far_out=le._game_too_far_out(slug),
        mapping_ok=bool(ok), mapping_why=why, edge_ok=bool(edge_ok), edge_why=edge_why,
        cell_ok=clause is None, cell_clause=clause, legacy_row=legacy,
        slug_recent_copy=slug_recent, underdog_coholds=underdog,
        venue_net=(None if t.positions is None else r.venue), kalshi_claimed=kalshi,
        side_band_hit=side_band_hit, snap_fresh=r.fresh, drift=drift.drift,
        books_live=books_live, opened_today=opened_today, first_fill_ok=first_fill_ok,
        # EITHER SIGHT OF HIM IS A SIGHT (rules: the clause already
        # reads both). The whole-book walk is truncated on every probe
        # of him, so this is the flag that lets a book open at all --
        # and it is True only on a read this tick, for THIS condition,
        # that named at least one of its two tokens.
        snap_market_fresh=r.snap_market_fresh)
    refusal = rules.admission(facts)
    if refusal:
        _mirror_stop(refusal, w)
        return str(refusal)
    intent = ORDER_INTENT_SHORT if short else ORDER_INTENT
    if short:
        # the two doors in front of every short open (H1, H2), after
        # admission so the census names the earlier gate first
        held = await _short_open_refusal(t)
        if held:
            _mirror_stop(held, w)
            return held
    opened = await le._open_mirror_book(t.pool, w, cid, slug, la, oa, tg["ratio_eff"], anchor,
                                        his_px, target, m.get("source"), game_key, intent=intent)
    if not opened.get("ok"):
        refused = str(opened.get("refusal") or "open_failed")
        _mirror_stop(refused, w)
        return refused
    book = await t.pool.fetchrow(_SQL_BOOK_READ, opened["book_id"])
    if not book:
        # the book opened and its row could not be read back: named
        # since W2 / P2 (it was a silent exit); the book is walked from
        # its row next tick like any other
        _mirror_stop("book_row_unreadable", w)
        return "book_row_unreadable"
    book = dict(book)
    if int(book.get("episode") or 1) > 1:
        await t.pool.execute(_SQL_BOOK_REOPENS, book["id"], int(book["episode"]) - 1)
    _recent(book["id"], "opened", whale=w, slug=slug, target=target, ratio=tg["ratio_eff"],
            intent=(intent if short else None))
    log.info("mirror_live: book %s opened for %s on %s (target %s @ %s)", book["id"], w, slug,
             target, his_px)
    # the new book joins its game for the rest of the tick (E1): a
    # second candidate on the same game reads its target as exposure
    t.game_books.setdefault(_game_key_of(book), []).append(book)
    async with _lock_for(book["id"]):
        await _tick_book(t, book)
    return None


# --------------------------------- W2: the candidate's refusal, persisted

# the exits that are not a refusal of a candidate WITH NO BOOK and so
# write no row: a market that already has a book (the walk skips it
# before the call; the name is for a direct caller)
_CAND_NOT_RECORDED = frozenset({"book_seen"})


async def _walk_candidate(t: _Tick, whale: str, cid: str) -> str | None:
    """The walk's call: _tick_candidate with a context, and its exit
    name noted for the refusal table (W2 / P2). Returns the name."""
    d: dict = {}
    name = await _tick_candidate(t, whale, cid, ctx=d)
    if name is None:
        # a book opened: no row, but the memo reads `opened` so a refusal
        # after the book closes is a transition again, whatever stood
        # before the open
        _cand_refusal_last[(whale, cid)] = ("opened", t.now)
    elif name not in _CAND_NOT_RECORDED:
        _note_candidate_refusal(t, whale, cid, name, d)
    return name


def _note_candidate_refusal(t: _Tick, whale: str, cid: str, name: str, d: dict) -> bool:
    """Queue one refusal row for (whale, cid) when `name` is a
    TRANSITION from the last row written for the pair, or the same
    name re-stamped after CAND_REFUSAL_RESTAMP_S. Returns whether a row
    was queued. Pure bookkeeping: no read, no write here."""
    key = (whale, cid)
    last = _cand_refusal_last.get(key)
    if last is not None and (t.now - last[1]) < CAND_REFUSAL_RESTAMP_S:
        # inside the restamp window the same name is no transition; nor
        # is the cap's name over ANY name (W2 review): under the rotation
        # a candidate is read one tick and left unread the next, and a
        # row each way was ~2 x cap rows every tick with no 900 s bound
        if last[0] == name or name == "cand_unread_capped":
            return False
    if key in t.cand_pending:
        return False                    # once per pair per tick
    t.cand_pending[key] = (name, t.now)
    t.cand_rows.append({
        "whale": whale, "condition_id": cid, "us_slug": d.get("slug"), "refusal": name,
        "at_ts": float(t.now), "his_net": _num(d.get("his_net")), "target": _int_or_none(d.get("target")),
        "mark": _num(d.get("mark")), "his_px": _num(d.get("his_px")), "ask": _num(d.get("ask")),
        "band": _num(d.get("band")), "long_asset": d.get("long_asset"),
        "long_from": d.get("long_from"),
        "books_live": _int_or_none(d.get("books_live", t.stats.get("books_live"))),
        "opened_today": _int_or_none(d.get("opened_today")),
        "active_conditions": _int_or_none(t.active_conds.get(whale)),
        "cand_reads": int(t.cand_reads),
        "tick_s": round(time.monotonic() - t.started, 1)})
    return True


def _int_or_none(v: Any) -> int | None:
    n = _num(v)
    return None if n is None else int(n)


async def _flush_candidate_refusals(t: _Tick) -> None:
    """ONE write of the tick's refusal rows; on success the memo takes
    the pending entries. A failure is counted (`refusal_write_failed`),
    logged once per process, and leaves the memo alone so the same
    names are transitions again next tick. Never raises."""
    global _cand_write_logged
    rows, pending = t.cand_rows, t.cand_pending
    t.cand_rows, t.cand_pending = [], {}
    if not rows:
        return
    try:
        # bounded (W2 review): the pool carries no command_timeout and the
        # write runs under _TICK_LOCK, so a hung write must not hold the tick
        await asyncio.wait_for(t.pool.execute(_SQL_CAND_REFUSALS, json.dumps(rows)),
                               CAND_REFUSAL_WRITE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — measurement never blocks the tick
        _mirror_stop("refusal_write_failed")
        if not _cand_write_logged:
            _cand_write_logged = True
            log.warning("mirror_live: mirror_candidate_refusals write failed (%s); %d rows dropped "
                        "this tick, written again on the next transition (migration 054 applied?)",
                        type(exc).__name__, len(rows))
        return
    _cand_refusal_last.update(pending)
    if len(_cand_refusal_last) > _CAND_REFUSAL_MEMO_MAX:
        # bounded: the pairs whose stamp is older than the lookback are
        # gone from his active conditions anyway
        cutoff = t.now - float(ms.LOOKBACK_H) * 3600.0
        for k in [k for k, v in _cand_refusal_last.items() if v[1] < cutoff]:
            del _cand_refusal_last[k]


async def _shadow_planned(t: _Tick, whale: str) -> set:
    """The conditions whose newest shadow row carries a plan (W2 / P3):
    a would_side, or a target that is not 0. Unreadable: the empty set,
    and the walk rotates over his conditions as they come."""
    try:
        rows = await t.pool.fetch(_SQL_SHADOW_PLANNED, whale, float(ms.LOOKBACK_H))
    except Exception as exc:  # noqa: BLE001 — no plan known is no plan, never a stop
        log.debug("mirror_live: shadow plans for %s unreadable (%s)", whale, type(exc).__name__)
        return set()
    out = set()
    for r in rows or []:
        tg = _num(r.get("target"))
        if r.get("would_side") or (tg is not None and int(tg) != 0):
            out.add(str(r.get("condition_id")))
    return out


def _candidate_order(conds: list, woken, planned, cursor: str | None) -> list:
    """The walk's order for one whale (W2 / P3): the woken markets first
    in his newest-fill order, then the rotation -- the planned
    candidates ahead of the rest, each group in newest-fill order,
    the whole rotated to resume right AFTER `cursor` (the last rotation
    candidate the previous tick walked). A cursor not in the list
    restarts at the head. Pure."""
    wk = set(woken or ())
    pl = set(planned or ())
    first = [c for c in conds if c in wk]
    rot = [c for c in conds if c not in wk and c in pl] + [c for c in conds if c not in wk and c not in pl]
    if cursor is not None and cursor in rot:
        i = rot.index(cursor) + 1
        rot = rot[i:] + rot[:i]
    return first + rot


def _name_unread(t: _Tick, whale: str, unread: list, stamps: dict | None = None) -> None:
    """The candidates the cap left unread this tick, each named
    `cand_unread_capped` (W2 / P2) -- never one a memo or a book would
    have skipped anyway: those spend no slot and were not cut. E7: a
    candidate memo his newer fill (`stamps`, the walk's) would have
    released is one the cap DID cut, and is named."""
    for cid in unread:
        if (whale, cid) in t.books_seen:
            continue
        if _cand_memo_skips(whale, cid, t.now, (stamps or {}).get(cid), t.woken):
            continue
        if _terminal_until.get((whale, cid), 0.0) > t.now:
            continue
        _mirror_stop("cand_unread_capped", whale)
        _note_candidate_refusal(t, whale, cid, "cand_unread_capped", {})


# -------------------------------------------------------------- tick_once

def _book_id(row: dict) -> int:
    return int(row.get("id") or 0)


def _game_walk_key(books: list) -> tuple:
    """A game's place in the walk: the OLDEST updated_at among its
    books, then its lowest id. A NULL updated_at sorts last, as the
    read's own `ORDER BY updated_at` puts it."""
    ts = [x for x in (_num(b.get("updated_ts")) for b in books) if x is not None]
    return (0 if ts else 1, min(ts) if ts else 0.0, min(_book_id(b) for b in books))


def _woken_first(rows: list, woken: list) -> list:
    """The book walk's order. GAMES in order of the oldest updated_at
    among their books, and WITHIN a game book id ascending (E1, rule
    4: the books of one game consume the game's room in one fixed
    order whatever their updated_at says). The game order keeps the
    abandon round-robin the first cut's id-only walk had dropped:
    _write_plan bumps updated_at, a tick abandoned at book k wrote no
    plan for k or for anything after it, so the next tick resumes with
    the games it did not reach first. A woken market brings its WHOLE
    game to the front, the games still in that order among themselves
    and each game's books still in id order, so a wake never reorders
    a game's books against each other."""
    games: dict = {}
    for r in rows:
        games.setdefault(_game_key_of(r), []).append(r)
    ordered = sorted(games.items(), key=lambda kv: _game_walk_key(kv[1]))
    wk = {_game_key_of(r) for r in rows if str(r.get("condition_id")) in woken} if woken else set()
    first = [b for k, bs in ordered if k in wk for b in sorted(bs, key=_book_id)]
    rest = [b for k, bs in ordered if k not in wk for b in sorted(bs, key=_book_id)]
    return first + rest


async def tick_once(pool, pmus, http, now_ts: float | None = None) -> dict:
    """One reconciler pass. Returns the census the heartbeat carries;
    every counter is present whatever the tick did."""
    global _current_stats, _last_tick_at, _last_loss, _last_sleeve
    now = time.time() if now_ts is None else float(now_ts)
    started = time.monotonic()          # the real clock: `now` may be the caller's
    _last_loss = None                   # L1: never a stale window on the mode line
    _last_sleeve = None                 # L2: nor a stale sleeve reading
    stats = _new_stats()
    if _TICK_LOCK.locked():
        stats.update(status="overlap", skipped_overlap=True, tick_s=0.0)
        return stats
    async with _TICK_LOCK:
        _current_stats = stats
        t = _Tick(pool=pool, pmus=pmus, http=http, now=now, stats=stats, started=started)
        woken = sorted(_WOKEN)
        _WOKEN.clear()
        _WAKE.clear()
        stats["woken"] = woken
        try:
            await _tick(t, woken)
        finally:
            _last_tick_at = now
            stats["ops"], stats["reads"] = t.ops, t.reads
            stats["tick_s"] = round(time.monotonic() - started, 1)
            stats["recent"] = list(_RECENT)[-20:]
            stats["post_only"] = _POST_ONLY_OK
            # last, and in the `finally`: an abandoned or raising tick
            # publishes the counters it did reach, never a stale block
            stats["integ"] = _integ_block(stats)
            # E6: the tick's time, INSIDE the `short` block -- the health
            # endpoint serves 40 top-level keys and an ON tick fills them
            # (pinned never truncated); `integ` is pinned under 40 keys
            # of its own, `fills_dedup` is the key the sanitizer drops on
            # a capped tick and is pinned by value, `frozen_reasons` is a
            # reason -> count map, so the one nested block with room is
            # this one (served whole, `.detail.short.timing`). The raw
            # heartbeat, the ops line and the mode line's `t=` carry it too
            stats.setdefault("short", {})["timing"] = _timing_block(t)
            # E7 part B: the data-API wait, beside the timing block (whose
            # keys are pinned exactly); the same home, served whole
            stats["short"]["data_api"] = _data_api_block(t)
            _publish_fills_dedup(t)
            await _persist_terminal_memo(t)     # E6 part 3: bounded, once per 60 s, on change
            await _persist_cand_memo(t)         # E7: the candidate memos, the same rules, their own key
            _last_loss = t.loss         # L1: the mode line's `loss=` fragment
            _last_sleeve = t.sleeve     # L2: its `sleeve=` fragment
            _current_stats = None
    return stats


def _count_fills_dedup(t: _Tick) -> None:
    """Right after every `ms.his_fills` call: add what it collapsed
    (ms.his_fills_dedup, the per-match rows dropped under a net-leg row
    and their shares) to the tick's totals."""
    d = ms.his_fills_dedup()
    t.fills_dedup_rows += int(d.get("dup_rows") or 0)
    t.fills_dedup_shares = round(t.fills_dedup_shares + float(d.get("dup_shares") or 0.0), 4)


def _publish_fills_dedup(t: _Tick) -> None:
    """The tick's fills collapse (D1), as ONE nested block appended AFTER
    every other key: `fills_dedup = {rows, shares}`, served whole as
    `.detail.fills_dedup.rows` / `.shares`. Why a block and why last:
    the health endpoint's sanitizer caps every dict at 40 keys. `integ`
    (the served projection for past-cap names) holds 39 and is pinned
    under the cap by this file's tests, so two more names cannot ride
    there; the top level holds 38 base keys plus `venue_positions` on
    every ON tick, one slot short of the cap, so a block written LAST is
    the one the sanitizer drops on a tick that also appends
    `capped_tick` or `abandon_reason` -- never one of those. The numbers
    are always present on the raw heartbeat and the ops log line."""
    t.stats["fills_dedup"] = {"rows": int(t.fills_dedup_rows),
                              "shares": round(float(t.fills_dedup_shares), 4)}


async def _tick(t: _Tick, woken: list) -> None:
    global _last_mode, _last_whales
    stats = t.stats
    # the markets that woke this tick: a book on one is hot whatever its
    # fills' stamps say (E6 review HIGH-2)
    t.woken = {str(c) for c in (woken or ())}
    if t.now < _backoff_until:
        # the mode and allowlist the worker holds, never _new_stats'
        # SAFE default (see _last_mode); read the real way when no
        # tick has completed yet
        stats["skipped_backoff"] = True
        stats["backoff_left_s"] = round(_backoff_until - t.now, 1)
        if _last_mode is None:
            await _read_mode(t)
        else:
            stats["mode"], stats["whales"] = _last_mode, list(_last_whales)
        return
    # the trading tables: workers never run migrations
    try:
        await t.pool.fetch(_SQL_TABLE_GUARD)
    except Exception as exc:  # noqa: BLE001 — absent or unreadable: refuse, never crash
        _mirror_stop("tables_absent")
        stats.update(status="degraded", tables_absent=type(exc).__name__)
        log.warning("mirror_live: mirror tables unreadable (%s); refusing", type(exc).__name__)
        return
    # the 050 column (P2 rung S0): present, the tick sends the
    # intent-carrying statements; ABSENT -- the driver's own
    # undefined-column error and nothing else (rules.column_missing) --
    # the 047 ones and the knob is effectively off, named when the
    # environment has it on, never a raise (the workers never run
    # migrations). Any other failure of the probe is the probe failing,
    # not the column missing: the tick is REFUSED by name like the table
    # guard's, because a knob read off for one tick on a transient error
    # is the reversal path -- every sole short book market-closed at
    # EXIT_SLIPPAGE_BIPS on a database blip (review, migration and
    # sign/money lenses)
    try:
        await t.pool.fetch(_SQL_INTENT_GUARD)
        t.short_col = True
    except Exception as exc:  # noqa: BLE001 — a column that is not there is a fact; a blip is not
        if not rules.column_missing(exc, "intent"):
            _mirror_stop("intent_guard_unreadable")
            stats.update(status="degraded", intent_guard_unreadable=type(exc).__name__)
            log.warning("mirror_live: mirror_orders.intent probe failed (%s); refusing the tick",
                        type(exc).__name__)
            return
        t.short_col = False
        if rules.MIRROR_SHORTS:
            _mirror_stop("short_column_absent")
            stats["short_column_absent"] = type(exc).__name__
            log.warning("mirror_live: MIRROR_SHORTS is on but mirror_orders.intent is absent "
                        "(migration 050 not applied yet: %s); shorts stay off", type(exc).__name__)
    stats.setdefault("short", {})["on"] = _shorts_on(t)
    # E6: this tick's number in the process (the quiet rotation's clock),
    # and the terminal memos' one boot read (part 3) -- once per process,
    # whatever it read: a failed boot read is the empty memo, never a retry
    # on every tick
    global _tick_seq, _terminal_memo_loaded
    _tick_seq += 1
    t.seq = _tick_seq
    if not _terminal_memo_loaded:
        _terminal_memo_loaded = True
        await _load_terminal_memo(t)
        await _load_cand_memo(t)            # E7: the candidate memos, the same one boot read
    await _read_mode(t)
    if t.mode != MODE_SAFE and le.active_venue() != "polymarket-us":
        _mirror_stop("no_venue")
        t.mode = MODE_SAFE
        stats["mode"] = MODE_SAFE
        t.cancel_all = "no_venue"
    _last_mode, _last_whales = t.mode, list(stats.get("whales") or [])
    if t.mode == MODE_SAFE:
        t.cancel_all = t.cancel_all or "mode_env_off"
        await _reconcile_orders(t)
        await _instruments(t)
        return
    await _global_guards(t)
    if t.cancel_all:
        await _reconcile_orders(t)
        await _instruments(t)
        return
    # S4 (review round 2, GAP 2): a probe order the venue kept is
    # cancelled again before anything else -- every tick, until the
    # venue reports it gone; no probe goes out while one is held
    await _s4_cancel_retry(t)
    # R: the reads, once. Ratios for the allowlist only: an open book
    # carries its own fixed ratio and never re-reads one
    t.ratios = await ms.refresh_ratios(t.pool, sorted(t.allow)) if t.allow else {}
    t0 = time.monotonic()
    t.positions, pages, limited = await ms.account_positions_walk(t.pmus)
    t.timing["walk"] += time.monotonic() - t0
    _venue_call(t, pages)               # every page the walk read (E2 review), per call
    if limited:
        _rate_limited(t, None, "positions walk")
    if t.positions is None:
        # the abandon keeps its name (the walk is what could not be
        # read); a walk that failed on a 429 skips the 60 s backoff
        # while the circuit _rate_limited just tripped holds, as a
        # placement 429 does (E2 review round 4, LOW-4)
        _mirror_stop("positions_unreadable")
        await _abandon_reconciled(t, "positions_unreadable", rate_limited=bool(limited))
        return
    stats["venue_positions"] = len(t.positions)
    if await _read_open(t) is None:
        await _abandon_reconciled(t, "open_orders_unreadable")
        return
    # THE ROSTER AND CLIPS, on the copy path's TTL (money-safety review
    # 2026-09-05). The readers below (_mapping_admitted, per_fill_usd)
    # refuse while the stored pair is UNREADABLE -- but nothing in this
    # process refreshed the pair while the copy probe was off, so a
    # rebooted worker sat on the code default with the hardcoded clips
    # and never reached the closed state at all. Never raises.
    await le.refresh_whale_overrides(t.pool)
    await edge_gate.refresh(t.pool)
    if await _read_protected(t) is None:
        await _abandon_reconciled(t, "protected_ids_unreadable")
        return
    # O: the orders first
    t0 = time.monotonic()
    await _reconcile_orders(t)
    t.timing["orders"] += time.monotonic() - t0
    if t.cancel_all:
        # a trip while booking (an overfill): the tick is cancel-only
        # from here -- what step O kept before the trip is cancelled,
        # no book is planned, no candidate opens (spec 1c "tick
        # cancel-only"; step-9 review)
        await _reconcile_orders(t, count=False)
        await _instruments(t)
        return
    # B: the books, woken markets first. EVERY live book is read every
    # tick, unbounded in reads (U12): a book's quote and per-market
    # reads are charged to the totals (t.reads, t.mkt_reads) and never
    # to the candidates' budgets below -- exits must be managed, and a
    # book walk that spent the candidates' budget was a count cap on
    # books by another road
    books = [dict(b) for b in await t.pool.fetch(_SQL_BOOKS_OPEN)]
    # the per-game index (E1): every non-closed book by game_key (every
    # whale's, one game one cap), before any book is sized, so each
    # book's target reads the room its game has left after the others
    _index_games(t, books)
    # THE BUDGET'S SHARES (E6; the paragraph over VENUE_CALLS_PER_TICK):
    # the quiet books get what the budget leaves after steps R and O,
    # one read per hot book and the candidates' floor; the deferred
    # queue's head is decided here, before the walk; a book the walk no
    # longer lists leaves the rotation (LOW-2); the paced seconds the
    # walk spends are read off the process accumulator around it
    _forget_unlisted(books)
    t.quiet_budget = _quiet_budget(t, books)
    t.deferred_due = _deferred_due(t)
    t0, paced0 = time.monotonic(), _paced_seconds()
    await _walk_books(t, _woken_first(books, woken))
    t.timing["books"] += time.monotonic() - t0
    t.timing["books_venue"] += _paced_seconds() - paced0
    if t.cancel_all:
        # a wrong-sign trip on a book: "cancel everything" (spec P) --
        # every order the earlier books kept, and no candidate
        await _reconcile_orders(t, count=False)
        await _instruments(t)
        return
    # then new candidates -- the woken first, then the shadow's planned
    # ahead of the rest, resumed from the whale's rotation cursor (W2 /
    # P3; newest-first within each group) -- for whales that may increase.
    # THE CANDIDATE STAGE'S SHARE (E6): what the budget leaves after the
    # books, map reads and quote reads together, never under
    # CAND_MIN_PER_TICK; the map cap is the old per-tick cap at least
    # (_map_cap), and the quote-read cap what the share leaves after the
    # map reads, under MAX_MARKETS_PER_TICK as before
    t.cand_budget = _cand_budget(t)
    t.map_budget.cap = _map_cap(t.cand_budget)
    t.cand_cap = _cand_cap(t)
    _prune_cand_memos(t.now)                # E7: the memos an hour past their until
    cands_t0 = time.monotonic()
    for w in sorted(t.allow):
        if t.abandoned:
            break
        refusal = _increases_refusal(t, w)
        if refusal:
            _mirror_stop(refusal, w)         # a whale who may not open a book, by name
            continue
        try:
            # E7: the same one read, with the `last_ts` it ranks on -- his
            # newest fill per market, what releases a candidate memo
            stamped = await ms.active_conditions(t.pool, w, stamped=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("mirror_live: active markets for %s unreadable (%s)", w, type(exc).__name__)
            continue
        conds = [c for c, _ in stamped]
        stamps = {c: s for c, s in stamped}
        t.stamps.update(stamps)
        t.active_conds[w] = len(conds)
        wk = set(woken)
        cursor = _cand_cursor.get(w)
        if cursor is not None and (cursor not in conds or cursor in wk):
            # the cursor candidate left his active list (its newest fill
            # aged past the lookback -- the tail of a newest-first list is
            # exactly where that happens) or was woken: resume after the
            # last candidate the capped walk read that is still on the
            # list, never at the head (W2 review: the tail starved again)
            cursor = next((c for c in reversed(_cand_trail.get(w, ())) if c in conds and c not in wk), None)
        conds = _candidate_order(conds, wk, await _shadow_planned(t, w), cursor)
        unread: list = []                 # what the cap left unread, named below (W2 / P2)
        last_rot: str | None = None       # the last rotation candidate walked this tick
        trail: list = []                  # the rotation candidates walked, in order
        capped = False
        for i, cid in enumerate(conds):
            if t.abandoned:
                break
            t.cand_cap = _cand_cap(t)
            if t.cand_reads >= MAX_MARKETS_PER_TICK or t.cand_reads >= t.cand_cap:
                # the CANDIDATES' own cap (U12: MAX_MARKETS_PER_TICK, the
                # books never spent it) -- and since E6 the stage's share
                # of the tick's budget (t.cand_cap, never above the cap):
                # what the hot books and the quiet rotation left, the map
                # reads spent first, never under CAND_MIN_PER_TICK
                # (_cand_budget, _cand_cap)
                stats["capped_tick"] = True
                unread, capped = conds[i:], True
                break
            if (w, cid) in t.books_seen:
                continue
            # E7: his newer fill (or a wake) drops a candidate memo BEFORE
            # it is checked -- the market is read this tick
            _release_cand_memo(t, w, cid, stamps.get(cid))
            if _unmapped_until.get((w, cid), 0.0) > t.now:
                continue
            if _no_mark_until.get((w, cid), 0.0) > t.now:
                # its last candidate read found the market OPEN with no
                # mark (E7): no slot spent on it until the memo's TTL
                # runs or his next fill lands
                _mirror_stop("cand_no_mark_skipped", w)
                continue
            if _terminal_until.get((w, cid), 0.0) > t.now:
                # its last candidate read said the market had ended (D1):
                # no slot spent on it until the memo's TTL runs
                _mirror_stop("cand_terminal_skipped", w)
                continue
            if t.guard_calls >= rules.MIRROR_VENUE_CALLS_PER_TICK:
                # THE SOFT GUARD (E2): a tick that has spent its guarded
                # calls -- its WRITES and its candidates' reads, never
                # the open books' reads (review MEDIUM-5: those are the
                # tick's fixed cost, and counting them left 12 candidate
                # reads at 46 books) -- reads no more candidates (a
                # market a memo or a book already skips is not one it
                # cut); the exits above were never bounded by it, and
                # the next tick reads on
                _mirror_stop("venue_calls_capped", w)
                stats["capped_tick"] = True
                unread, capped = conds[i:], True
                break
            if cid not in wk:
                last_rot = cid
                trail.append(cid)
            try:
                await _walk_candidate(t, w, cid)
            except Exception as exc:  # noqa: BLE001
                _mirror_stop("book_error", w)
                log.exception("mirror_live: candidate %s/%s failed (%s)", w, cid, type(exc).__name__)
        # THE CURSOR (W2 / P3): a capped walk resumes AFTER the last
        # rotation candidate it walked next tick; a walk that reached the
        # end of the list starts from the head (woken, planned, newest
        # first) -- nothing was starved, so nothing to resume
        if capped and last_rot is not None:
            _cand_cursor[w] = last_rot
            _cand_trail[w] = trail
        elif not capped and not t.abandoned:
            _cand_cursor.pop(w, None)
            _cand_trail.pop(w, None)
        _name_unread(t, w, unread, stamps)
    t.timing["candidates"] += time.monotonic() - cands_t0
    await _flush_candidate_refusals(t)
    await _instruments(t)


async def _walk_books(t: _Tick, ordered: list) -> None:
    """THE BOOK WALK, IN PARALLEL (E2, 2026-09-06; owner order "I want
    this firing as frequently as his ... make the latency as low as
    possible"). The walk was sequential -- each book's 2-4 reads in
    series, ~3.5 s a book, 162 s with 46 books (20:48Z heartbeat), so
    a fill of his waited up to a tick plus the poll before we acted.

    The unit of concurrency is a GAME, never a book: `ordered` (the
    woken-first, id-ascending order _woken_first gives) is grouped by
    _game_key_of in first-appearance order, and each game's books are
    ticked one after another in that order under the per-book lock, so
    the per-game cap's room is consumed in E1's one fixed order (a
    game's second book reads what its first was sized to). Games run
    under an asyncio.Semaphore of rules.MIRROR_BOOK_CONCURRENCY (env
    may only lower it; 1 is the old walk); the tasks are created in
    walk order and the semaphore hands slots out in arrival order, so
    the woken game's books start first. A book that raises is
    `book_error` for that book alone, as before. An abandon or a trip
    on one book stops every book not yet started; a book already in
    flight finishes under its own checks (_place and the flatten's
    slippage leg refuse under t.abandoned and t.cancel_all).

    What this bounds and what it does not: the in-flight database and
    data-API reads. Every measurement read still queues on the
    process-wide 0.35 s pacer (venue_pace), so the venue sees the same
    rate as before and the tick's floor is the pacer's."""
    groups: dict[tuple, list] = {}
    for book in ordered:
        groups.setdefault(_game_key_of(book), []).append(book)
    sem = asyncio.Semaphore(max(1, int(rules.MIRROR_BOOK_CONCURRENCY)))

    async def _game(gbooks: list) -> None:
        for book in gbooks:
            if t.abandoned or t.cancel_all:
                return
            async with sem:
                if t.abandoned or t.cancel_all:
                    return
                async with _lock_for(book["id"]):
                    try:
                        await _tick_book(t, book)
                    except Exception as exc:  # noqa: BLE001 — one book, not the tick
                        _mirror_stop("book_error", book.get("whale"))
                        log.exception("mirror_live: book %s failed (%s)", book["id"],
                                      type(exc).__name__)
                    finally:
                        # the miss streak, judged in WALK order as each
                        # book finishes (E2 review, MEDIUM-4)
                        t.walk_done.add(book["id"])
                        _judge_walk(t, ordered, book)

    tasks = [asyncio.create_task(_game(g)) for g in groups.values()]
    if not tasks:
        return
    t.walk_order = list(ordered)
    t.walking = True
    try:
        # every exception is caught inside _game; return_exceptions so
        # a cancellation of one task can never leave the others orphaned
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        t.walking = False
    # the candidates start from the walk's TRAILING run, exactly where
    # the sequential walk left the live streak
    if not t.abandoned:
        trailing = _judge_walk(t, ordered, None)
        if trailing is not None:
            t.misses = trailing


def _judge_walk(t: _Tick, ordered: list, book: dict | None) -> int | None:
    """_walk_streak, with a raise NAMED (`walk_error`) and logged instead
    of ending the game's walk or the tick (E2 review round 2, LOW-f:
    gather(return_exceptions=True) swallowed a raise from _game's
    finally, and the game's remaining books were silently skipped).
    None when the judge raised: the caller leaves what it has."""
    try:
        return _walk_streak(t, ordered)
    except Exception as exc:  # noqa: BLE001 — the judge, not the walk
        _mirror_stop("walk_error", (book or {}).get("whale"))
        log.exception("mirror_live: walk streak judge failed after book %s (%s)",
                      (book or {}).get("id"), type(exc).__name__)
        return None


async def _instruments(t: _Tick) -> None:
    """The reaper-isolation instrument (spec 5): a mirror order id on
    a copy row -- cancelled, adopted or booked by a reaper -- must read
    0. Unreadable is reported as such, never as 0."""
    try:
        n = int(await t.pool.fetchval(_SQL_REAPER_TOUCHED) or 0)
    except Exception as exc:  # noqa: BLE001
        t.stats["reaper_touched_mirror"] = None
        t.stats["reaper_touched_error"] = type(exc).__name__
        return
    t.stats["reaper_touched_mirror"] = n
    if n > 0:
        _mirror_stop("reaper_touched_mirror")
        t.stats["status"] = "degraded"


# ------------------------------------------------------------------- main

def _mode_line(stats: dict, ticks: int, loss: dict | None = None,
               sleeve: dict | None = None) -> None:
    """The quiet-tick line, on every MODE_LINE_EVERY_TICKS-th completed
    tick. Built from the census the tick published: `mode`, `whales`,
    `books_live`, `orders_open` and `mirror_day_room` (the day ROOM in
    dollars, what _global_guards read off MIRROR_DAY_USD after what
    filled and what rests; None on a tick that never read it) are its
    own keys, and so are `venue_state` (the venue's own market state,
    the most common one the tick's quote reads carried; None on a tick
    that read none), `abandon_reason` (printed as `abandon=` on a tick
    that abandoned) and `backoff_left_s` (printed as `backoff=` on a
    tick skipped inside the backoff, whose `mode` is the one the worker
    holds -- never SAFE unless the mode IS safe). `census` and `recent`
    are left out of the trailing dict, as the ops line leaves them out.
    `loss` (L1) is the loss window the tick read, handed over by main()
    (_last_loss, the paragraph over it): `loss=<sum>/<limit> since
    <HH:MM>` beside the day rail, the window's start to the minute --
    bounded text, the ISO's clock alone -- so a re-arm shows as the
    start moving to its instant; nothing on a tick that read none.
    `sleeve` (L2) is the copy sleeve's breaker the tick read over the
    same window (_last_sleeve): `sleeve=<sum>/<limit>` beside it."""
    if ticks < 1 or ticks % MODE_LINE_EVERY_TICKS:
        return
    extra = ""
    if stats.get("abandoned"):
        extra += " abandon=%s" % (stats.get("abandon_reason"),)
    if stats.get("skipped_backoff"):
        extra += " backoff=%s" % (stats.get("backoff_left_s"),)
    rail = ""
    if isinstance(loss, dict):
        rail = " loss=%s/%s since %s" % (loss.get("sum"), loss.get("limit"),
                                         str(loss.get("since") or "")[11:16])
    if isinstance(sleeve, dict):
        rail += " sleeve=%s/%s" % (sleeve.get("sum"), sleeve.get("limit"))
    # `day=none` under an unbounded day cap (the default since
    # 2026-09-06): the room is null, and there is no cap to print
    day = stats.get("mirror_day_room")
    if day is None and not math.isfinite(float(rules.MIRROR_DAY_USD)):
        day = "none"
    tm = (stats.get("short") or {}).get("timing") if isinstance(stats.get("short"), dict) else None
    tfrag = ""
    if isinstance(tm, dict) and not stats.get("skipped_backoff"):
        # E6: the tick's time by step, `t=walk/orders/books/cands` (seconds,
        # one decimal) beside the books it read, skipped and placed on --
        # right after the day rail, before `loss=` / `sleeve=` and
        # `venue=`, inside the line's first 400 characters; nothing on a
        # dict that carries no timing, nor on a backed-off tick (no time
        # to print, and its line is pinned as it was)
        tfrag = " t=%s/%s/%s/%s read=%s quiet=%s placed=%s" % (
            tm.get("walk"), tm.get("orders"), tm.get("books"), tm.get("candidates"),
            tm.get("read"), tm.get("quiet_skipped"), tm.get("placed"))
    log.info("mirror_live mode=%s whales=%s books=%s open=%s day=%s%s%s venue=%s%s stats=%s",
             stats.get("mode"), stats.get("whales"), stats.get("books_live"),
             stats.get("orders_open"), day, tfrag, rail, stats.get("venue_state"),
             extra, {k: v for k, v in stats.items() if k not in ("census", "recent")})


async def main() -> None:
    """The loop. PMUS_MIRROR=off is a running cancel-only loop, never an
    idle one: a deploy that drops the flag must still cancel what the
    previous process left resting."""
    import httpx

    from .. import pmus

    pool = await get_pool()
    cfg = settings()
    log.info("mirror_live up: PMUS_MIRROR=%s allowlist=%s poll=%ss",
             os.environ.get("PMUS_MIRROR", "off"), sorted(le.mirror_allowlist()), POLL_S)
    async with httpx.AsyncClient(base_url=cfg.data_api_base, timeout=25.0) as http:
        ticks = 0
        while True:
            try:
                stats = await tick_once(pool, pmus, http)
                ticks += 1
                try:
                    await heartbeat(SERVICE, str(stats.get("status") or "ok"), stats)
                except Exception:  # noqa: BLE001
                    log.debug("mirror_live: heartbeat failed")
                if stats.get("ops") or stats.get("abandoned"):
                    log.info("mirror_live: %s", {k: v for k, v in stats.items()
                                                 if k not in ("census", "recent")})
                _mode_line(stats, ticks, _last_loss, _last_sleeve)
            except Exception:  # noqa: BLE001 — the reconciler never dies
                log.exception("mirror_live pass failed")
            try:
                await asyncio.wait_for(_WAKE.wait(), timeout=POLL_S)
            except asyncio.TimeoutError:
                pass
            gap = time.time() - _last_tick_at
            if gap < WAKE_MIN_GAP_S:
                await asyncio.sleep(WAKE_MIN_GAP_S - gap)


__all__ = ["POLL_S", "WAKE_MIN_GAP_S", "MODE_LINE_EVERY_TICKS", "SERVICE", "CENSUS_KEYS",
           "notify", "tick_once", "mirror_census_snapshot", "main"]

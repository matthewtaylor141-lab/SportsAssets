"""Worker: THE RN1-SEEDED MANAGEMENT EXPERIMENT, ACTUALLY RUNNING.

The audit's row for this path read: "Batch runner; no API/worker caller,
no database persistence caller, no published trace route. Migration
existing in git is not production deployment." This loop is the worker
caller. `bettor_rn1x_store` is the persistence caller. `api/command_rn1x`
is the published route.

WHAT IT DOES, ONCE PER CYCLE.

    1. READ THE CONTROL ROW and fail closed. Absent, false, malformed or
       unreadable all mean STOPPED. Absence is not permission.
    2. VERIFY THE TABLES against the live catalog, not the migrations
       directory. Migration 100 sat committed at HEAD for hours while
       the production schema had none of its tables.
    3. TAKE ONE SEED CANDIDATE past the cursor -- one real cohort fill on
       a condition that has since RESOLVED.
    4. VERIFY INITIAL INVENTORY from the record, and refuse when it
       cannot be verified.
    5. REPLAY it through `bettor_rn1x_run.run`, which is the existing
       chain: entry classification, the frozen management policy, the
       existing `Managed` lifecycle over `bettor_desk.Order`/`Portfolio`.
    6. PERSIST the whole thing in one transaction, or record the refusal.

TWO LANES, AND I HAD ONLY BUILT ONE WHILE BLAMING THE FEED FOR IT.

I reported that no forward lane could be seeded from this feed, citing a
15.07 h detection lag on one seed that postdated settlement. That was a
wrong diagnosis of my own code. The candidate query required
`m.resolved`, so the experiment could only ever see settled markets --
HISTORICAL BY MY OWN FILTER, not by any property of the feed. The cursor
also defaults to 0, so it walks `trades` from the first row ever
recorded: at id ~300 of ~6.1M it is replaying March.

And the lag figure could not carry that weight. `trades.source`
separates `chain`/`poll` (live, `detected_at` stamped at insert) from
`backfill` (inserted months later) and `s1` (a synthetic emitter). On a
backfill row `detected_at - ts` is the age of the backfill. `lane_census`
now measures latency over LIVE lanes only, every cycle, into the
heartbeat.

  HISTORICAL   resolved markets; scored against the observed payout; its
               cursor walks from the start and is never reset here.
  PROSPECTIVE  UNRESOLVED markets, LIVE lanes only; cursor seeded at the
               feed head on a genuine first start. Decisions are recorded
               while the outcome is unknown, and no payout is read.

The two are keyed to different experiment ids and different cursors, so
their rows and their totals are never mixed.

NO CAPITAL, NO ORDERS, NO VENUE. Every order it writes is modelled and
the schema CHECKs `is_modelled`. There is no submit, cancel or funding
path in this module's import closure, and it never touches the desk's
tables or `acct_fc2d773a2afa4851`, which stays paused and
ACCOUNTING_UNCERTAIN as evidence.

Kill: the control row (prompt, no deploy) or RN1X_SHADOW=off (a deploy).
"""

from __future__ import annotations

import asyncio
import decimal
import logging
import os

from .. import bettor_rn1x_policy as pol
from .. import bettor_rn1x_run as runner
from .. import bettor_rn1x_store as store
from .. import bettor_mgmt_select as _sel
from ..db import get_pool, heartbeat

log = logging.getLogger(__name__)

SERVICE = "rn1x_shadow"
#: The standby's own service row. A process that holds no writer lock
#: produces no cycle, so it has nothing to say about one.
STANDBY_SERVICE = "rn1x_shadow_standby"
CONTROL_KEY = "rn1x_shadow"
CURSOR_KEY = "rn1x_source_cursor"

TICK_S = 20.0
IDLE_S = 120.0
# Bounded by construction. `trades` holds ~6.1M rows and a count(DISTINCT
# condition_id) over it has already blown a 240-second statement timeout
# in this project; nothing here scans the table.
SCAN_BATCH = 400          # trades rows examined per cycle
MAX_SEEDS_PER_CYCLE = 3   # conditions replayed per cycle
MAX_ROWS_PER_CONDITION = 4000

# ── TWO LANES, SEPARATELY KEYED AND SEPARATELY PERSISTED ────────────
#
# I PREVIOUSLY REPORTED THAT NO FORWARD LANE WAS POSSIBLE FROM THIS FEED.
# That was wrong, and the mistake was mine rather than the feed's. The
# candidate query below required `m.resolved`, so the experiment could only
# ever see settled markets -- historical BY MY OWN FILTER. On top of that
# the cursor defaults to 0, so it walks `trades` from the very first row:
# at id ~300 of ~6.1M it is replaying March, not consuming new activity.
# The 15.07 h "detection lag" I generalised into a feed blocker came from
# ONE seed, and `trades.source` shows why that figure cannot bear the
# weight I put on it: for a `backfill` row, `detected_at - ts` is when we
# BACKFILLED it, not when anything was detected.
#
# So there are two lanes and they never share a total:
#   HISTORICAL   resolved markets, any lane, cursor walks from the start.
#                Scored against the observed payout. Its cursor and its
#                rows are PRESERVED -- nothing here resets or discards it.
#   PROSPECTIVE  UNRESOLVED markets, LIVE lanes only, cursor seeded at the
#                feed head on first start so it consumes what arrives
#                after it begins. No payout exists yet, so it is not
#                scored; it records decisions at the time they were taken.
HISTORICAL_EXPERIMENT_ID = "RN1X_MGMT_PAIR091_STOP16_V1"
PROSPECTIVE_EXPERIMENT_ID = "RN1X_MGMT_PAIR091_STOP16_V1_PROSPECTIVE"
# Kept under the old name so existing rows, tests and readbacks that speak
# of "the experiment id" keep meaning the historical one.
EXPERIMENT_ID = HISTORICAL_EXPERIMENT_ID

HISTORICAL_CURSOR_KEY = "rn1x_source_cursor"      # unchanged, preserved
PROSPECTIVE_CURSOR_KEY = "rn1x_prospective_cursor"

# ── THE CHALLENGER LANE ──────────────────────────────────────────────
#
# The same seeds, the same order lifecycle, the same accounting, and a
# DIFFERENT DECISION POLICY. Its own experiment id and its own cursor,
# because the frozen benchmark's totals must not move when a challenger
# runs beside it, and `position_id` derives from (experiment, policy,
# trade) so a shared id would collide the two arms onto one row.
#
# IT IS PROSPECTIVE AND READS NO PAYOUT, exactly as the prospective lane
# does -- a policy scored against an outcome it was allowed to see is
# not a policy being tested.
CHALLENGER_EXPERIMENT_ID = "RN1X_SHADOW_CHALLENGER_HOLD_RANKED_V1"
CHALLENGER_CURSOR_KEY = "rn1x_challenger_cursor"

LANES = ("HISTORICAL", "PROSPECTIVE", "CHALLENGER")

# The venue contract and the probability that prices a hold, joined to
# the seed's GLOBAL condition id. `external_valuations` carries both
# identifiers -- the global one it was asked about and the venue-native
# slug the resolver matched -- which is the only reason this join is
# possible without re-running the resolver here.
#
# ELIGIBLE ROWS ONLY. Migration 108 holds every row whose payout event
# was read off the order intent, and a held row must not reach a
# decision by being the only one present.
_CHALLENGER_VALUATION = """
    SELECT id, us_market_slug, venue, contract_selection,
           probability, probability_event, payout_event,
           payout_is_complement, buy_intent, matched_side_norm,
           resolver_asked_for, ladder_side, executable_price,
           provider, book, devig_method, version, experiment_id,
           mapped_outcome, mapping_match, overround, outcomes_priced,
           expected_outcomes, outcome_books, settlement_rule,
           extract(epoch FROM observed_at)::float8  AS observed_at,
           extract(epoch FROM received_at)::float8  AS received_at,
           eligibility, ineligible_reason
      FROM external_valuations
     WHERE condition_id = $1
       AND eligibility = 'ELIGIBLE'
     ORDER BY observed_at DESC NULLS LAST, id DESC
     LIMIT 1
"""

# THE LIVE LANES, BY NAME. Migration 033 is explicit that the shadow
# instrument's evidence queries filter `source IN ('chain','poll')` so the
# S1 emitter's own rows can never be read as venue coverage. My candidate
# query had NO source filter at all, so 's1' rows were eligible seeds --
# a rule the project had written down and I had not honoured.
LIVE_SOURCES = ("chain", "poll")
SEEDABLE_SOURCES = ("chain", "poll", "backfill")   # never 's1'

EXECUTION_BASIS = "PRINT_THROUGH_WITH_QUEUE_SHARE_V1"

# Named blockers, so the command centre shows a reason rather than an
# empty panel. Each is a genuine external dependency, not a switch.
BLOCKERS = {
    "SECOND_HALF_UNDEFINED": (
        "No timestamped event-progress feed is connected, so the "
        "second-half condition in MANAGEMENT_PAIR_091_STOP_16_V1 cannot "
        "be evaluated for any sport. The loss exit is therefore "
        "UNAVAILABLE on every position and the experiment currently "
        "tests the PAIRING half of the policy only. MISSING DEPENDENCY: "
        "a per-sport event clock with observed period/half boundaries, "
        "joined point-in-time to the decision instant."),
    "NO_CONTEMPORANEOUS_BOOK": (
        "No bid/ask/depth is retained for these historical instants. "
        "The tape prints in `trades` are executions by others, not a "
        "standing book, so `bid` and `bid_size` are not supplied and a "
        "trigger that needs an executable bid cannot fire. MISSING "
        "DEPENDENCY: an archived same-venue order book at the decision "
        "instants."),
    # WITHDRAWN AS A BLOCKER. It was never one. Kept under its own name
    # rather than deleted, because a claim I published and then retracted
    # should stay visible as a retraction rather than disappear.
    "FEED_POSTDATES_SETTLEMENT_WITHDRAWN": (
        "WITHDRAWN 2026-09-23. I reported that RN1 detection postdated "
        "settlement by 11.22 hours and concluded no forward lane was "
        "possible. The 15.07 h figure came from ONE seed and could not "
        "bear that weight: `trades.source` shows `backfill` rows, whose "
        "`detected_at - ts` is the age of the backfill and not a "
        "detection latency. The experiment was historical because my own "
        "candidate query required `m.resolved`, not because of the feed. "
        "A PROSPECTIVE lane on unresolved markets and live lanes only now "
        "exists, and `lane_census` measures live latency every cycle so "
        "this is answered from persisted state rather than one sample."),
}

# Blockers that apply ONLY to the prospective lane, because a decision
# taken before resolution genuinely cannot be scored yet.
PROSPECTIVE_NOTES = {
    "NOT_YET_SCORED": (
        "A prospective decision has no observed payout, because the "
        "market has not resolved. These rows record what was decided and "
        "when; the historical lane scores the same conditions once they "
        "settle. A prospective total is never added to a scored one."),
}


def _off(name: str, default: str = "on") -> bool:
    return os.getenv(name, default).strip().lower() in (
        "off", "0", "false", "no")


# ── the control row, fail-closed four ways ──────────────────────────
async def _running(conn) -> tuple[bool, str]:
    """STOPPED unless the row says, in so many words, true.

    Absent, false, malformed, unreadable -- all STOPPED. This is the same
    shape `bettor_live_loop` uses, and for the same reason: the database
    row is the authoritative stop because it is the PROMPT one. A deploy
    is not needed to stop this loop and must not be.
    """
    try:
        raw = await conn.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key = $1",
            CONTROL_KEY)
    except Exception as exc:                                   # noqa: BLE001
        return False, "CONTROL_UNREADABLE_%s" % type(exc).__name__
    if raw is None:
        return False, "CONTROL_ROW_ABSENT"
    if raw.strip().lower() != "true":
        return False, "CONTROL_ROW_NOT_TRUE"
    return True, "RUNNING"


async def _cursor(conn, key: str = HISTORICAL_CURSOR_KEY,
                  *, seed_at_head: bool = False) -> int:
    """Resume exactly, or -- for the prospective lane only -- start at NOW.

    THE HISTORICAL CURSOR IS NEVER RESET. It defaults to 0 so the
    historical lane keeps walking `trades` from the beginning, which is
    what makes its evidence a replay of the record. Nothing in this module
    writes it backwards.

    THE PROSPECTIVE CURSOR IS DIFFERENT AND MUST BE. A prospective lane
    seeded at 0 would grind through five months of history while every row
    it published claimed to be new activity -- the same defect the desk
    loop documents at `_load_cursor`. On a genuine FIRST start it takes
    max(id), so it consumes only what arrives after it begins. A restart
    resumes from the stored value exactly, so a redeploy loses nothing.
    """
    raw = await conn.fetchval(
        "SELECT value::text FROM ingestion_state WHERE key = $1", key)
    try:
        return int(str(raw).strip().strip('"'))
    except (TypeError, ValueError):
        pass
    if not seed_at_head:
        return 0
    head = int(await conn.fetchval(
        "SELECT coalesce(max(id), 0) FROM trades") or 0)
    log.info("rn1x prospective FIRST START: seeding cursor at head %s; "
             "history before it is not replayed as new activity", head)
    return head


async def _save_cursor(conn, value: int,
                       key: str = HISTORICAL_CURSOR_KEY) -> None:
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        key, str(int(value)))


# ── seed candidates: real cohort fills on RESOLVED conditions ───────
#
# BUYS ONLY, because the runner refuses a SELL seed: a sale is not an
# entry and seeding a position from one would invent the inventory it
# sold. The join to `markets` is what makes the replay scorable -- an
# unresolved condition has no observed payout and step 8 would have
# nothing to read.
_CANDIDATES_HISTORICAL = """
    SELECT t.id, t.whale_id, t.condition_id
      FROM trades t
      JOIN markets m ON m.condition_id = t.condition_id
     WHERE t.id > $1
       AND t.condition_id IS NOT NULL
       AND t.outcome_index IN (0, 1)
       AND t.side = 'BUY'
       AND t.source = ANY($3::text[])
       AND m.resolved
       AND m.resolved_prices IS NOT NULL
     ORDER BY t.id
     LIMIT $2
"""

# THE PROSPECTIVE LANE. Live lanes only, and markets that have NOT
# resolved -- so a decision here is taken while the outcome is genuinely
# unknown. No payout is read because none exists; these rows are scored
# later, by the historical lane, once the market settles.
_CANDIDATES_PROSPECTIVE = """
    SELECT t.id, t.whale_id, t.condition_id
      FROM trades t
      JOIN markets m ON m.condition_id = t.condition_id
     WHERE t.id > $1
       AND t.condition_id IS NOT NULL
       AND t.outcome_index IN (0, 1)
       AND t.side = 'BUY'
       AND t.source = ANY($3::text[])
       AND NOT m.resolved
     ORDER BY t.id
     LIMIT $2
"""

# ── THE PROVENANCE CENSUS ───────────────────────────────────────────
#
# Rides the heartbeat every cycle, so "is this historical or prospective"
# is answered by persisted operational state rather than by reading the
# source of a query. It is deliberately ONE bounded statement on the
# `trades_detected_live_idx` window plus one cheap join on the PK.
_LANE_CENSUS = """
    SELECT count(*) FILTER (WHERE t.source = ANY($1::text[])) AS live_24h,
           count(*) FILTER (WHERE t.source = 'backfill') AS backfill_24h,
           count(*) FILTER (WHERE t.source = 's1') AS s1_24h,
           count(*) FILTER (WHERE t.source = ANY($1::text[])
                            AND t.side = 'BUY' AND NOT m.resolved)
               AS live_unresolved_buys_24h,
           round(max(extract(epoch FROM (t.detected_at - t.ts)))
                 FILTER (WHERE t.source = ANY($1::text[]))::numeric, 1)
               AS live_max_latency_s,
           round(avg(extract(epoch FROM (t.detected_at - t.ts)))
                 FILTER (WHERE t.source = ANY($1::text[]))::numeric, 1)
               AS live_avg_latency_s
      FROM trades t
      JOIN markets m ON m.condition_id = t.condition_id
     WHERE t.detected_at > now() - interval '24 hours'
"""

_SEEDED_BY_SOURCE = """
    SELECT t.source, count(*) AS seeded
      FROM rn1x_positions p
      JOIN trades t ON t.id = p.source_trade_id
     GROUP BY 1 ORDER BY 2 DESC
"""


async def lane_census(conn) -> dict:
    """What the feed actually delivered in the last 24 hours, by lane.

    THE FIGURE THAT MATTERS FOR THE BLOCKER I WRONGLY DECLARED is
    `live_unresolved_buys_24h`: live-lane BUY fills on markets that have
    not resolved. If it is non-zero then a prospective lane has evidence
    to work with, and "the feed postdates settlement" was a statement
    about backfill rows and one seed, not about the feed.

    `live_max_latency_s` is computed over LIVE lanes only, because
    `detected_at - ts` on a backfill row is the age of the backfill.
    """
    row = await conn.fetchrow(_LANE_CENSUS, list(LIVE_SOURCES))
    seeded = await conn.fetch(_SEEDED_BY_SOURCE)
    # `round(...)::numeric` comes back as Decimal, which json.dumps refuses.
    # A figure that cannot be read stays None rather than becoming 0.
    out = {k: (float(v) if isinstance(v, decimal.Decimal) else v)
           for k, v in (dict(row) if row else {}).items()}
    out["seeded_by_source"] = {r["source"]: int(r["seeded"]) for r in seeded}
    out["live_sources"] = list(LIVE_SOURCES)
    out["latency_note"] = (
        "live lanes only. For a backfill row detected_at - ts is the age "
        "of the backfill, not a detection latency")
    return out


# Every recorded fill on the condition, ours and others'. The others'
# are the EVIDENCE that licenses a modelled fill; without them a resting
# order could never fill and the experiment would be a hold-only test by
# omission rather than by policy.
_CONDITION_ROWS = """
    SELECT t.id, t.whale_id, t.outcome_index, t.side,
           t.size::float8 AS size, t.price::float8 AS price,
           extract(epoch FROM t.ts)::float8 AS ts,
           extract(epoch FROM t.detected_at)::float8 AS detected_at
      FROM trades t
     WHERE t.condition_id = $1
       AND t.outcome_index IN (0, 1)
     ORDER BY t.id
     LIMIT $2
"""


async def verify_initial_inventory(conn, *, whale_id, condition_id,
                                   trade_id) -> dict:
    """Was the account FLAT on this condition before the seed fill?

    THE AUDIT'S DEFECT 4 WAS THAT A TRUNCATED FIRST BUY WAS ENOUGH TO
    ASSIGN A POSITION, and its repair made the runner demand that the
    caller supply verified initial-inventory status -- while noting,
    correctly, that "the flag itself is not proof". So this function
    computes it rather than asserting it, from two facts in the record:

      prior_fills   the account's own earlier fills on this condition
      coverage_ok   our first recorded trade for this account is at or
                    before the condition's first recorded trade

    Both are needed. Zero prior fills alone proves nothing if our
    collection of this account began after the market opened -- the
    account could have bought before we were watching. The coverage
    comparison is what rules that out.

    IT IS STILL A BOUND, NOT A CERTIFICATE. It establishes flatness with
    respect to WHAT WE RECORDED. A gap inside our collection window
    would not show up here, and that limitation is returned with the
    verdict rather than swallowed.
    """
    prior = await conn.fetchval(
        "SELECT count(*) FROM trades WHERE whale_id = $1 "
        "AND condition_id = $2 AND id < $3",
        int(whale_id), condition_id, int(trade_id))
    acct_first = await conn.fetchval(
        "SELECT min(ts) FROM trades WHERE whale_id = $1", int(whale_id))
    cond_first = await conn.fetchval(
        "SELECT min(ts) FROM trades WHERE condition_id = $1", condition_id)
    coverage_ok = bool(acct_first and cond_first and acct_first <= cond_first)
    verified = int(prior or 0) == 0 and coverage_ok
    return {
        "verified": verified,
        "prior_fills_on_condition": int(prior or 0),
        "account_first_recorded_fill": acct_first,
        "condition_first_recorded_trade": cond_first,
        "coverage_precedes_condition": coverage_ok,
        "why_not": (None if verified else
                    ("the account has %d earlier recorded fill(s) on this "
                     "condition" % int(prior or 0)) if int(prior or 0)
                    else ("our record of this account begins after the "
                          "condition's first recorded trade, so a position "
                          "held before we were watching cannot be ruled "
                          "out")),
        "limitation": ("flatness is established with respect to RECORDED "
                       "fills. A gap inside the collection window would "
                       "not be visible here"),
    }


POSITION_IDENTITY_SQL = """
    SELECT mt.outcome, mt.outcome_index, mt.token_id
      FROM market_tokens mt
     WHERE mt.condition_id = $1 AND mt.outcome_index = $2
     LIMIT 1
"""

# THE VALUATION FOR THIS CONDITION, WHATEVER EXPOSURE IT DESCRIBES.
# Deliberately NOT filtered on the payout event: the filter is applied
# afterwards, against the POSITION's own identity, so a row describing
# the opposing side is REFUSED BY NAME instead of silently missing.
R_IDENTITY_UNRESOLVED = "POSITION_OUTCOME_IDENTITY_UNRESOLVED"
R_VALUATION_IS_THE_OTHER_SIDE = "VALUATION_DESCRIBES_THE_OPPOSING_EXPOSURE"


def _norm_outcome(v) -> str:
    return " ".join(str(v or "").strip().lower().split())


async def position_identity(conn, *, condition_id, outcome_index) -> dict:
    """WHICH EVENT THIS POSITION PAYS ON, from the position, not the quote.

    THE DEFECT THIS EXISTS FOR, found by independent inspection of
    deployed 5b19bc5. The builder read `payout_event` off the valuation
    row and handed that same string back to `bettor_hold_value` as
    `payout_event_held`, so the identity check compared the row against
    itself and could not fail. Any row for the condition was accepted --
    including one describing the OPPOSING exposure, which is a probability
    for the event our position loses on.

    `market_tokens` carries (condition_id, outcome, outcome_index) from
    the chain enrichment, so the seed's own outcome_index names the
    payout event independently of anything the valuation says. Matching
    condition_id alone cannot do this: both sides of a market share it.
    """
    row = await conn.fetchrow(POSITION_IDENTITY_SQL, condition_id,
                              int(outcome_index))
    if row is None:
        return {"ok": False, "refusal": R_IDENTITY_UNRESOLVED,
                "condition_id": condition_id,
                "outcome_index": int(outcome_index),
                "why": ("no market_tokens row names outcome_index %d on "
                        "%s, so the event this position pays on is not "
                        "established. It is NOT taken from the valuation "
                        "row: that is the self-confirming check this "
                        "refusal replaces"
                        % (int(outcome_index), condition_id))}
    r = dict(row)
    return {"ok": True, "payout_event": r.get("outcome"),
            "outcome_index": r.get("outcome_index"),
            "token_id": r.get("token_id"),
            "basis": "MARKET_TOKENS_OUTCOME_FOR_THE_SEEDED_INDEX",
            "why": ("established from the position's own seeded outcome "
                    "index, independently of the quote")}


async def challenger_inputs_for(conn, *, condition_id, outcome_index,
                                seed_qty, seed_price) -> dict:
    """Everything the challenger needs at decision time, or why not.

    THE BOOK IS READ ONCE PER CALL AND ITS INSTANT TRAVELS WITH IT. A
    live venue book is a per-instant object; `read_at` is recorded and
    every decision that consumes it ages itself against that stamp.
    THE PROBABILITY IS NOT FROZEN: the ROW is returned and `ev_hold` is
    recomputed per decision, so freshness is rechecked at each decision
    rather than once per position.

    Returns `available` false with a NAMED reason when the chain cannot
    be completed. That is not a failure of the cycle -- it is the
    finding, and it is what makes the remaining gaps countable.
    """
    import time as _t

    from sportsassets import bettor_book_snapshot as bs
    from sportsassets import bettor_hold_value as hv

    now = _t.time()
    out = {"available": False, "read_at": now, "condition_id": condition_id,
           "outcome_index": int(outcome_index)}

    # ── 1 · THE POSITION'S OWN IDENTITY, FIRST AND INDEPENDENTLY ─────
    ident = await position_identity(conn, condition_id=condition_id,
                                    outcome_index=outcome_index)
    out["position_identity"] = ident
    if not ident["ok"]:
        out["reason"] = ident["refusal"]
        out["why"] = ident["why"]
        return out
    payout_event_held = ident["payout_event"]
    out["payout_event"] = payout_event_held

    # ── 2 · A VALUATION FOR THE CONDITION, THEN CHECKED AGAINST IT ───
    val = await conn.fetchrow(_CHALLENGER_VALUATION, condition_id)
    if val is None:
        held = await conn.fetchrow(
            "SELECT id, eligibility, ineligible_reason FROM "
            "external_valuations WHERE condition_id = $1 "
            "ORDER BY id DESC LIMIT 1", condition_id)
        out["reason"] = (hv.R_INELIGIBLE if held is not None
                         else hv.R_NO_SOURCE)
        out["why"] = (
            ("the only external valuation for this condition is held: %s"
             % (dict(held).get("ineligible_reason") or
                dict(held).get("eligibility")))
            if held is not None else
            ("no external valuation row exists for this condition, so "
             "no probability can price a hold"))
        return out
    v = dict(val)
    out["valuation_row_id"] = v["id"]
    out["us_market_slug"] = v.get("us_market_slug")
    out["venue"] = v.get("venue")
    out["valuation_payout_event"] = v.get("payout_event")

    # THE CHECK THAT CAN NOW ACTUALLY FAIL. The row's payout event is
    # compared against the POSITION's, and a row describing the opposing
    # exposure is refused rather than adopted as the position's identity.
    if _norm_outcome(v.get("payout_event")) != _norm_outcome(payout_event_held):
        out["reason"] = R_VALUATION_IS_THE_OTHER_SIDE
        out["why"] = (
            "the freshest eligible valuation for this condition prices "
            "%r and this position pays on %r. It is REFUSED, not "
            "adopted: a probability for the event we lose on is worse "
            "than no probability at all. Matching condition_id alone "
            "does not distinguish the two sides of one market"
            % (v.get("payout_event"), payout_event_held))
        out["probability_row"] = None
        return out
    out["probability_row"] = v
    out["identity_matched_independently"] = True

    # ── 3 · THE VENUE BOOK, AND THE EXIT PRICES IT IMPLIES ──────────
    slug = v.get("us_market_slug")
    intent = v.get("buy_intent")
    if not slug or not intent:
        out["reason"] = "VENUE_CONTRACT_NOT_IDENTIFIED"
        out["why"] = ("the valuation row carries no venue slug or no "
                      "resolved intent, so there is no ladder to read")
        out["book_available"] = False
        return out
    out["held_intent"] = intent
    try:
        from .ext_pinnacle_loop import _read_book_blocking
        book = await asyncio.to_thread(_read_book_blocking, slug)
    except Exception as exc:                                   # noqa: BLE001
        out["reason"] = "VENUE_BOOK_READ_RAISED"
        out["why"] = "%s: %s" % (type(exc).__name__, exc)
        out["book_available"] = False
        return out
    md = (book or {}).get("marketData")
    if md is None:
        out["reason"] = "VENUE_BOOK_UNREADABLE"
        out["why"] = ("the venue read returned no marketData: %s. With a "
                      "probability present HOLD is still priced and "
                      "every exit is refused for want of a book"
                      % (book or {}).get("error"))
        out["diagnostic"] = (book or {}).get("diagnostic")
        out["book_available"] = False
        return out

    # EXITING IS NOT ACQUIRING. `exit_ladder` reads the side a CLOSE
    # would consume and returns BOTH prices for it:
    #
    #     exit_price       what closing PAYS US      (1 - complement)
    #     complement_price what neutralising COSTS
    #
    # The previous build passed the opposite side's ACQUISITION cost
    # through as `bid` -- .40 on a .60 bid for a held long -- and the
    # same side's acquisition as `complement_ask`. Both were wrong, in
    # both directions, and the ranking picked winners on the invented
    # spread between them.
    is_short = bs.is_short_intent(intent)
    xl = bs.exit_ladder(md, held_intent=intent)
    out["exit_ladder"] = xl
    if not xl.get("ok"):
        out["reason"] = xl.get("refusal") or bs.R_NO_EXIT_SIDE
        out["why"] = xl.get("why")
        out["book_available"] = False
        return out
    out.update(
        available=True, book_available=True,
        held_is_long=not is_short,
        # THE PROCEEDS FROM SELLING WHAT WE HOLD.
        bid=xl["best_exit_price"],
        bid_size=xl["size_at_best"],
        last_price=xl["best_exit_price"],
        # THE COST OF NEUTRALISING IT. Same ladder, same order on this
        # venue, and the two sum to 1.00 by construction.
        complement_ask=xl["best_complement_price"],
        complement_ask_size=xl["size_at_best"],
        # SIZING WALKS THE EXIT PROCEEDS, NOT THE ACQUISITION COST.
        sale_ladder=bs.as_sale_ladder(xl),
        settlement_semantics=v.get("settlement_rule"),
        price_denomination={
            "bid": "EXIT PROCEEDS per contract for the held exposure",
            "complement_ask": "COST per contract to neutralise it",
            "relation": bs.EXIT_RELATION,
            "side_consumed": xl.get("side_consumed"),
            "quantity_from": ("the side a close consumes, which is where "
                              "the executable size actually is"),
        },
        input_labels={
            "ev_hold": "EXTERNAL_LABELLED_PROBABILITY (%s/%s)"
                       % (v.get("provider"), v.get("devig_method")),
            "bid": "OBSERVED venue ladder, exit proceeds",
            "complement_ask": "OBSERVED venue ladder, neutralising cost",
            "payout_event": ident["basis"],
            "book_read_at": now,
            "book_is_one_snapshot": (
                "read ONCE for this call. Every decision records its own "
                "age against it, and the PROBABILITY is re-aged per "
                "decision rather than frozen"),
        })
    return out


# ── THE HELD POSITION'S OWN INPUTS, PULLED FOR THE DECISION ─────────
#
# WHY A PULL AND NOT A READ. `challenger_inputs_for` reads the freshest
# ELIGIBLE row in `external_valuations`. That table is written by the
# ENTRY lane on `ext_pinnacle_loop.CYCLE_S = 900 s`, and the applicable
# odds rule is `bettor_pinnacle_devig.MAX_QUOTE_AGE_S = 30 s`. A
# management decision that reads that table is therefore refused as stale
# unless it happens to run inside a 30 s window of a 900 s cadence -- a
# ~3% chance per cycle, and only for a position whose market was in that
# cycle's candidate set at all. That is not a freshness policy; it is a
# coincidence, and every blind hold on the production position is what it
# looks like from the outside.
#
# So the managed inputs are OBTAINED for the decision: the same
# adapters, asked at the moment the decision is taken.
#
#   1 HELD_EXPOSURE          market_tokens          -> token, payout event
#   2 VENUE_CONTRACT         workers.premap.resolve -> slug + intent
#   3 PROVIDER_FIXTURE       fetch_odds + map_event -> the event, the quote
#   4 PROBABILITY            bettor_pinnacle_devig  -> p, aged at 30 s
#   5 EXIT_LADDER            pmus book + exit_ladder-> executable exit
#   6 RANKED_DECISION        the caller's ranking
#
# Each link records what it established and, when it cannot, the refusal
# WITH the identifiers and timestamps behind it. The first failing link
# is named on the record, so `EV_HOLD_NOT_IDENTIFIED` never again stands
# in for a cause.
MANAGED_INPUT_VERSION = "BETTOR_MANAGED_INPUT_PULL_V1"

#: THE ODDS RULE, IMPORTED. Restating 30.0 here is how two numbers drift.
from .. import bettor_pinnacle_devig as _devig_rule        # noqa: E402
_DEVIG_MAX_AGE_S = _devig_rule.MAX_QUOTE_AGE_S

#: THE BUDGET IS THE CADENCE. One provider request covers every event of
#: one sport key, so a managed pull costs nothing per position -- only per
#: FAMILY, and only for families that actually hold inventory. At most
#: this many requests per management cycle.
MANAGED_ODDS_CALLS_PER_CYCLE = 3

#: AND THIS ONE BINDS ACROSS CYCLES, which is the part a per-batch cache
#: cannot do. It was declared and unused: every cycle built a fresh
#: `ManagedOdds`, so the interval was enforced only WITHIN a batch and two
#: cycles 10 s apart -- or a cycle and a diagnostic read -- each spent
#: their own request. The gate below is process-wide and every caller goes
#: through it, the read-only diagnostic included, because provider quota
#: does not care which of our code paths spent it.
MANAGED_ODDS_MIN_INTERVAL_S = 45.0

#: A payload already in hand is reused instead of re-requested, but ONLY
#: while it can still satisfy the odds rule. The ceiling is the odds bound
#: itself, not the request interval: a cached payload older than the
#: freshness rule is of no use to a decision, and reusing one for longer
#: would be widening the threshold by the back door. The de-vig still ages
#: the quote independently, so a reuse inside this window can still be
#: refused -- which is the correct order of authority.
MANAGED_ODDS_CACHE_TTL_S = float(_DEVIG_MAX_AGE_S)

#: PROCESS-WIDE, shared by the management cycles and the diagnostic read.
_ODDS_LAST_AT: dict = {}
_ODDS_CACHE: dict = {}
R_MANAGED_PACED = "MANAGED_ODDS_PACED_DEFERRED_BY_THE_DECLARED_INTERVAL"


def odds_gate(sport_key, *, now, min_interval_s=None) -> dict:
    """May this caller spend a provider request for `sport_key` now?

    Non-blocking ON PURPOSE. A management phase with a 45 s wall-clock
    budget must not sleep 45 s inside it to wait for a quota slot: it
    defers, says so by name, and the next cycle finds the position first.
    """
    gap = float(MANAGED_ODDS_MIN_INTERVAL_S if min_interval_s is None
                else min_interval_s)
    last = _ODDS_LAST_AT.get(str(sport_key))
    # A CLOCK THAT WENT BACKWARDS MUST NOT LOCK THE LANE OUT. An NTP step
    # or a restored snapshot can leave a future stamp behind, and a gate
    # that then refuses forever would starve every decision of inputs
    # until someone noticed. Allowing the request costs ONE request and
    # re-anchors the interval.
    if last is not None and float(now) < float(last):
        return {"ok": True, "last_at": float(last), "min_interval_s": gap,
                "clock_went_backwards": True}
    if last is None or float(now) - float(last) >= gap:
        return {"ok": True, "last_at": last, "min_interval_s": gap}
    return {"ok": False, "refusal": R_MANAGED_PACED, "last_at": float(last),
            "min_interval_s": gap,
            "next_allowed_in_s": round(gap - (float(now) - float(last)), 3),
            "why": ("a provider request for %s was spent %.1f s ago and the "
                    "declared interval is %.0f s. Deferring costs this "
                    "cycle its probability; spending it would breach a "
                    "budget that is the whole reason the cadence is "
                    "declared" % (sport_key, float(now) - float(last), gap))}


def odds_gate_reset():
    """Tests only: forget the process-wide pacing and cache state."""
    _ODDS_LAST_AT.clear()
    _ODDS_CACHE.clear()

R_MANAGED_NO_MARKET_ROW = "MARKET_ROW_NOT_FOUND_FOR_THE_HELD_CONDITION"
R_MANAGED_MARKET_GONE = "THE_HELD_MARKET_IS_CLOSED_OR_RESOLVED"
R_MANAGED_FAMILY = "THE_HELD_SPORT_IS_NOT_IN_THE_PROVIDER_SET"
R_MANAGED_NO_CREDENTIAL = "ODDS_CREDENTIAL_ABSENT"
R_MANAGED_PROVIDER = "THE_ODDS_PROVIDER_REFUSED_THE_REQUEST"
R_MANAGED_FIXTURE = "THE_HELD_FIXTURE_IS_NOT_IN_THE_PROVIDER_PAYLOAD"
R_MANAGED_NO_PINNACLE = "NO_PINNACLE_H2H_ON_THE_HELD_FIXTURE"
R_MANAGED_BUDGET = "MANAGED_ODDS_BUDGET_SPENT_THIS_CYCLE"
R_RESOLVER_DISAGREES = "RESOLVER_AND_TOKENS_DISAGREE_ON_THE_PAYOUT_EVENT"

MANAGED_MARKET_SQL = """
    SELECT m.condition_id, m.title, m.event_title, m.slug, m.sport,
           m.closed, m.resolved,
           extract(epoch FROM m.updated_at)::float8 AS updated_at,
           -- THE FIXTURE'S OWN CLOCK, not our bookkeeping flags. It lives
           -- in `market_starts`, written by the lane that fetches it, and
           -- is absent for a fixture nobody has asked about -- which is a
           -- different fact from a fixture that has not started.
           extract(epoch FROM s.game_start)::float8 AS game_start,
           extract(epoch FROM s.fetched_at)::float8 AS game_start_fetched_at,
           s.err AS game_start_err
      FROM markets m
 LEFT JOIN market_starts s ON s.condition_id = m.condition_id
     WHERE m.condition_id = $1
"""


def family_for_label(label) -> str | None:
    """`markets.sport` speaks leagues; the valuation source speaks families.

    Inverted from `ext_pinnacle_loop.VENUE_SPORT_LABELS` rather than
    written again: two tables that disagree about which league is
    baseball is how the entry lane once reported 44 mapping refusals for
    an empty candidate set.
    """
    from .ext_pinnacle_loop import VENUE_SPORT_LABELS

    want = str(label or "").strip().lower()
    for fam, labels in VENUE_SPORT_LABELS.items():
        if want in {str(x).strip().lower() for x in labels}:
            return fam
    return None


class ManagedOdds:
    """The provider, asked at most a few times per cycle and shared.

    One request returns every event of one sport key, so N positions in
    one family cost ONE request. `calls` is the whole budget and it is
    reported on the cycle: a pull that could not be made says so by name
    rather than looking like a fixture the provider does not carry.
    """

    def __init__(self, *, api_key=None, calls=MANAGED_ODDS_CALLS_PER_CYCLE,
                 fetch=None, now=None, clock=None,
                 min_interval_s=None):
        import time as _tt

        self.api_key = api_key
        self.calls_left = int(calls)
        self.calls_made = 0
        self._fetch = fetch
        self._cache: dict = {}
        # THE CLOCK IS THE CALLER'S, so the pacing a test asserts is the
        # pacing production performs.
        self._now = (clock if clock is not None
                     else (lambda: float(now)) if now is not None
                     else _tt.time)
        self.min_interval_s = min_interval_s
        self.spent: list = []
        self.credits_remaining = None

    @property
    def credential_present(self) -> bool:
        return bool(self.api_key) or self._fetch is not None

    async def for_family(self, family: str) -> dict:
        """Every payload this family's sport keys returned, newest first."""
        from .ext_pinnacle_loop import SPORTS, fetch_odds

        keys = [k for k, fam in SPORTS if fam == family]
        out = {"family": family, "sport_keys": keys, "payloads": [],
               "refusals": [], "calls_made": 0}
        if not keys:
            out["refusals"].append(R_MANAGED_FAMILY)
            return out
        if not self.credential_present:
            out["refusals"].append(R_MANAGED_NO_CREDENTIAL)
            return out
        for key in keys:
            hit = self._cache.get(key)
            if hit is not None:
                out["payloads"].append(hit)
                out.setdefault("reused", []).append(
                    {"sport_key": key, "from": "THIS_BATCH"})
                continue
            # A PAYLOAD THIS PROCESS ALREADY HOLDS, while it can still
            # satisfy the odds rule. Cheaper than a request and no wider
            # than the rule: the de-vig ages the quote regardless.
            shared = _ODDS_CACHE.get(key)
            if shared is not None:
                age = self._now() - float(shared[0])
                if age <= MANAGED_ODDS_CACHE_TTL_S:
                    self._cache[key] = shared[1]
                    out["payloads"].append(shared[1])
                    out.setdefault("reused", []).append(
                        {"sport_key": key, "from": "PROCESS_CACHE",
                         "cache_age_s": round(age, 3)})
                    continue
            if self.calls_left <= 0:
                out["refusals"].append(R_MANAGED_BUDGET)
                break
            # THE DECLARED CROSS-CYCLE INTERVAL, enforced here and only
            # here, so every caller obeys the same one.
            gate = odds_gate(key, now=self._now(),
                             min_interval_s=self.min_interval_s)
            if not gate["ok"]:
                out["refusals"].append(gate["refusal"])
                out.setdefault("paced", []).append(
                    {"sport_key": key,
                     "next_allowed_in_s": gate["next_allowed_in_s"],
                     "min_interval_s": gate["min_interval_s"],
                     "why": gate["why"]})
                continue
            # CLAIMED BEFORE THE AWAIT, so two concurrent callers cannot
            # both pass the gate on the same slot.
            _ODDS_LAST_AT[key] = self._now()
            self.calls_left -= 1
            self.calls_made += 1
            out["calls_made"] += 1
            try:
                got = (await self._fetch(key) if self._fetch is not None
                       else await fetch_odds(key, api_key=self.api_key))
            except Exception as exc:                           # noqa: BLE001
                got = {"ok": False,
                       "error": "%s: %s" % (type(exc).__name__, exc)}
            self.spent.append({"sport_key": key, "ok": bool(got.get("ok")),
                               "events": len(got.get("events") or []),
                               "at": self._now(),
                               "credits_used": got.get("credits_used"),
                               "credits_remaining":
                                   got.get("credits_remaining")})
            if got.get("credits_remaining") is not None:
                self.credits_remaining = got.get("credits_remaining")
            if not got.get("ok"):
                out["refusals"].append(R_MANAGED_PROVIDER)
                out["provider_status"] = got.get("status")
                continue
            self._cache[key] = got
            _ODDS_CACHE[key] = (self._now(), got)
            out["payloads"].append(got)
        out["credits_remaining"] = self.credits_remaining
        return out


def devig_epoch_or_none(value):
    """A timestamp of whatever shape the driver returned, as an epoch."""
    try:
        return value.timestamp()
    except AttributeError:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


def anchored_clock(now=None, clock=None):
    """A clock that starts at `now` and advances with REAL elapsed time.

    The chain needs four instants that genuinely differ, and the tests
    need to control how far apart they are. Freezing everything at the
    caller's `now` would make a delayed provider look instantaneous --
    which is the defect this exists to expose -- and ignoring `now`
    entirely would break every fixture that works in a declared epoch.
    So `now` anchors the timeline and the real clock supplies the deltas.
    """
    import time as _tt

    src = clock or _tt.time
    base = src()
    start = float(now) if now is not None else base

    def tick():
        return start + (src() - base)

    return tick


async def managed_inputs_for(conn, *, condition_id, outcome_index,
                             odds=None, now=None, read_book=None,
                             resolve_identity=None, clock=None):
    """The six links for ONE held position, with the first failure named.

    Returns the same input keys `challenger_inputs_for` returns when the
    chain completes, so the decision path is unchanged -- plus `chain`,
    which says what each link established, and `first_failing_link`.
    """
    import time as _t

    from sportsassets import bettor_book_snapshot as bs
    from sportsassets import bettor_pinnacle_devig as devig
    from sportsassets import bettor_venue_mapping as vmap
    from sportsassets import bettor_venue_settlement as vset

    from . import ext_pinnacle_loop as EXT

    tick = anchored_clock(now, clock)
    started = tick()
    # FOUR CLOCKS, NEVER COLLAPSED INTO ONE.
    #
    #   provider_observed_at  the BOOKMAKER's own stamp on the quote
    #   provider_received_at  when the payload reached THIS process
    #   book_read_at          when the VENUE's book reply reached us
    #   inputs_ready_at       when the last required input had arrived
    #
    # The caller then takes its decision at its OWN instant, later than
    # all of these, and ages the probability against that. Reusing one
    # batch timestamp for all of them is how a quote that went stale
    # between the fetch and the decision would be priced as fresh -- a
    # pull is NOT "fresh by construction".
    clocks = {"pull_started_at": started, "provider_observed_at": None,
              "provider_received_at": None, "book_read_at": None,
              "inputs_ready_at": None,
              "reading": ("each stamp is the instant that input actually "
                          "arrived. Freshness is measured from "
                          "provider_observed_at to the DECISION's clock, "
                          "not to any of these")}
    now = started
    out = {"available": False, "version": MANAGED_INPUT_VERSION,
           "read_at": started, "condition_id": condition_id,
           "outcome_index": int(outcome_index), "chain": [],
           "clocks": clocks,
           "first_failing_link": None, "input_source": "PULLED_FOR_THIS_DECISION"}

    def link(name, ok, *, blocks_chain=True, **facts):
        """Record a link. `blocks_chain=False` marks one that can be
        unsatisfied WITHOUT stopping the decision -- settlement
        compatibility blocks HOLD_TO_SETTLEMENT and nothing else, and
        letting it claim `first_failing_link` would report a working
        chain as broken."""
        rec = {"link": name, "ok": bool(ok),
               "blocks_chain": bool(blocks_chain), **facts}
        out["chain"].append(rec)
        if not ok and blocks_chain and out["first_failing_link"] is None:
            out["first_failing_link"] = rec
            out["reason"] = facts.get("refusal")
            out["why"] = facts.get("why")
        return rec

    # ── 1 · THE HELD EXPOSURE, from the tokens ───────────────────────
    ident = await position_identity(conn, condition_id=condition_id,
                                    outcome_index=outcome_index)
    link("1_HELD_EXPOSURE", ident["ok"],
         token_id=ident.get("token_id"),
         # `position_identity` names the event; there is no separate
         # `outcome` key on it, and printing one produced a null beside a
         # link that had in fact succeeded.
         outcome=ident.get("payout_event"),
         payout_event=ident.get("payout_event"), basis=ident.get("basis"),
         refusal=ident.get("refusal"), why=ident.get("why"))
    if not ident["ok"]:
        return out
    payout_event_held = ident["payout_event"]
    out["position_identity"] = ident
    out["payout_event"] = payout_event_held

    # ── 2 · THE VENUE CONTRACT AND THE INTENT, from the catalogue ────
    #
    # NOT from a valuation row. The row carried the slug and the intent,
    # so a position with no row had no venue identity either -- which is
    # why the production decisions record `venue null` and
    # VENUE_POSITION_MODEL_NOT_ESTABLISHED alongside the missing hold
    # value. Two failures, one cause.
    mrow = await conn.fetchrow(MANAGED_MARKET_SQL, condition_id)
    if mrow is None:
        link("2_VENUE_CONTRACT", False, refusal=R_MANAGED_NO_MARKET_ROW,
             why=("no `markets` row carries this condition, so neither "
                  "the fixture nor the venue contract can be resolved"))
        return out
    m = dict(mrow)
    out["market_row"] = {k: m.get(k) for k in
                         ("condition_id", "slug", "sport", "title",
                          "event_title", "closed", "resolved",
                          "updated_at")}
    # THE FIXTURE'S OWN CLOCK. A market our refresher still calls open can
    # be a fixture that finished yesterday, and no freshness policy or
    # provider can price a hold on an event that is over. Reported
    # separately from coverage: they are different facts with different
    # remedies.
    gs = m.get("game_start")
    gs = None if gs is None else (gs if isinstance(gs, (int, float))
                                 else devig_epoch_or_none(gs))
    out["fixture"] = {
        "game_start": gs,
        "seconds_since_start": (None if gs is None else round(started - gs, 1)),
        "starts_in_s": (None if gs is None else round(gs - started, 1)),
        "is_past_start": (None if gs is None else bool(started > gs)),
        "game_start_known": gs is not None,
        "game_start_fetched_at": m.get("game_start_fetched_at"),
        "game_start_err": m.get("game_start_err"),
        "market_flags": {"closed": m.get("closed"),
                         "resolved": m.get("resolved")},
        "reading": ("`closed`/`resolved` are OUR flags, written by the "
                    "refresher. game_start is the fixture's own clock and "
                    "does not depend on our bookkeeping")}
    # THE RESOLVER IS INJECTABLE FOR TESTS AND IS `premap.resolve` IN
    # PRODUCTION. It is not reimplemented here: a sixth resolver with its
    # own idea of side matching is how a wrong-side trade happens, and
    # `us_premap` is not reproducible in a fixture without becoming a
    # second copy of the real table's key logic. What the tests below
    # cover is this chain's ORDER and its refusal reporting; the
    # resolver's own matching has its own tests.
    _resolve = resolve_identity or EXT.resolve_venue_identity
    vid = await _resolve(conn, market_row=m,
                         priced_outcome=payout_event_held)
    if not vid.get("ok"):
        # NOT FATAL, AND THAT MATTERS. HOLDING needs the payout identity
        # and a probability; it does not need a venue contract. The
        # contract is what an EXIT needs. Returning here would forfeit a
        # priced HOLD in exactly the case where HOLD is the only action
        # available, which is the wrong way round.
        link("2_VENUE_CONTRACT", False, refusal=vid.get("refusal"),
             why=vid.get("why"), global_slug=m.get("slug"),
             resolver=vid.get("resolver"),
             asked_for=payout_event_held,
             consequence=("EXITS ARE UNAVAILABLE on this exposure -- no "
                          "venue contract, so no ladder and no venue "
                          "translation. HOLD is still priced below if a "
                          "probability exists"))
        vid = None
    # TWO INDEPENDENT SOURCES FOR ONE FACT, and they must agree. The
    # tokens say what this outcome is; the venue's catalogue says what the
    # contract pays on. A disagreement IS fatal and is never reconciled: a
    # contract that pays on the other event would price and exit the wrong
    # exposure.
    intent = None
    if vid is not None:
        if _norm_outcome(vid.get("payout_event")) != \
                _norm_outcome(payout_event_held):
            link("2_VENUE_CONTRACT", False, refusal=R_RESOLVER_DISAGREES,
                 why=("the venue catalogue says this contract pays on %r "
                      "and the market tokens say this position holds %r"
                      % (vid.get("payout_event"), payout_event_held)),
                 us_market_slug=vid.get("us_market_slug"),
                 intent=vid.get("intent"))
            return out
        intent = vid["intent"]
        link("2_VENUE_CONTRACT", True,
             us_market_slug=vid["us_market_slug"],
             intent=intent, ladder_side=vid.get("ladder_side"),
             matched_side_norm=vid.get("matched_side_norm"),
             payout_event_basis=vid.get("payout_event_basis"),
             resolver=vid.get("resolver"),
             agrees_with_tokens=True)
        out["us_market_slug"] = vid["us_market_slug"]
        out["held_intent"] = intent
        out["venue"] = "PMUS"
    else:
        out["book_available"] = False
        out["identity_cross_check"] = "NOT_AVAILABLE_NO_VENUE_CONTRACT"

    # ── 3 · THE PROVIDER'S FIXTURE, matched by the same mapper ───────
    family = family_for_label(m.get("sport"))
    if family is None:
        # IS THIS OUR CHOICE OR THEIR CATALOGUE? `/v4/sports` is not
        # metered, so the answer costs nothing and decides whether
        # extending the set is even possible.
        cat = {"asked": False}
        key = os.environ.get("EDGE_ODDS_API_KEY")
        if key:
            try:
                got_cat = await EXT.fetch_sport_catalogue(api_key=key)
                want = str(m.get("sport") or "").strip().lower()
                cat = {"asked": True, "ok": bool(got_cat.get("ok")),
                       "metered": False,
                       "keys_for_this_sport": [
                           x for x in (got_cat.get("sports") or [])
                           if want and (want in str(x.get("group") or "").lower()
                                        or want in str(x.get("title") or "").lower()
                                        or want in str(x.get("key") or "").lower())][:12]}
            except Exception as exc:                           # noqa: BLE001
                cat = {"asked": True, "ok": False,
                       "error": "%s: %s" % (type(exc).__name__, exc)}
        link("3_PROVIDER_FIXTURE", False, refusal=R_MANAGED_FAMILY,
             why=("`markets.sport` is %r, which is not in the provider "
                  "set %s. THIS EXPOSURE HAS NO PROVIDER COVERAGE -- it "
                  "is not a mapping failure and no probability exists "
                  "for it at any freshness"
                  % (m.get("sport"),
                     sorted({f for _, f in EXT.SPORTS}))),
             sport_label=m.get("sport"),
             provider_families=sorted({f for _, f in EXT.SPORTS}),
             provider_catalogue=cat,
             remedy=("extending the provider set is only possible if the "
                     "catalogue above carries this sport AND the book "
                     "quotes it on our plan. Neither is assumed here, and "
                     "no threshold is widened either way"))
        return out
    out["sport_family"] = family
    odds = odds if odds is not None else ManagedOdds(
        api_key=os.environ.get("EDGE_ODDS_API_KEY"))
    got = await odds.for_family(family)
    out["odds_request"] = {"family": family,
                           "sport_keys": got.get("sport_keys"),
                           "calls_made": got.get("calls_made"),
                           "reused": got.get("reused"),
                           "paced": got.get("paced"),
                           "credits_remaining": got.get("credits_remaining"),
                           "min_interval_s": MANAGED_ODDS_MIN_INTERVAL_S,
                           "cache_ttl_s": MANAGED_ODDS_CACHE_TTL_S,
                           "refusals": got.get("refusals")}
    quote = None
    ev = None
    considered = 0
    for payload in got.get("payloads") or []:
        received_at = payload.get("received_at") or now
        for event in payload.get("events") or []:
            considered += 1
            mapped = vmap.map_event(home=event.get("home_team"),
                                    away=event.get("away_team"),
                                    markets=[m])
            if not mapped.get("mapped"):
                continue
            q = EXT.pinnacle_h2h(event, received_at=received_at)
            if q is None:
                link("3_PROVIDER_FIXTURE", False,
                     refusal=R_MANAGED_NO_PINNACLE,
                     why=("the provider carries this fixture and no "
                          "Pinnacle h2h on it. The source is named for "
                          "Pinnacle and must not substitute another book"),
                     provider_event_id=event.get("id"),
                     home=event.get("home_team"),
                     away=event.get("away_team"),
                     events_considered=considered)
                return out
            quote, ev = q, event
            break
        if quote is not None:
            break
    if quote is None:
        link("3_PROVIDER_FIXTURE", False,
             refusal=(got.get("refusals") or [R_MANAGED_FIXTURE])[0],
             why=("no event in the provider's %s payload maps to this "
                  "fixture (%s events examined by "
                  "bettor_venue_mapping.map_event). Provider refusals: %s"
                  % (family, considered, got.get("refusals") or "none")),
             events_considered=considered,
             market_title=m.get("title"),
             provider_refusals=got.get("refusals"))
        return out
    # THE WHOLE PAYLOAD'S OBSERVATION AGES, not just our fixture's.
    #
    # If the provider's own lag exceeds the odds rule, no cadence on our
    # side can fix it and the honest answer is the measurement, not a
    # wider threshold. One fixture cannot distinguish "this event is
    # quiet" from "this feed is late", so the census is taken across
    # every event that carries a Pinnacle h2h.
    ages = []
    for payload in got.get("payloads") or []:
        rcv = payload.get("received_at") or now
        for event in payload.get("events") or []:
            q = EXT.pinnacle_h2h(event, received_at=rcv)
            if q is None:
                continue
            obs = devig._epoch(q.get("observed_at"))
            if obs is not None:
                ages.append(round(float(rcv) - float(obs), 1))
    ages.sort()
    out["provider_quote_ages_s"] = (
        None if not ages else
        {"n": len(ages), "min": ages[0], "max": ages[-1],
         "median": ages[len(ages) // 2],
         "over_the_odds_rule": sum(1 for a in ages if a > _DEVIG_MAX_AGE_S),
         "odds_rule_s": _DEVIG_MAX_AGE_S,
         "measured_from": ("the BOOKMAKER's own last_update to OUR receipt "
                           "-- the provider's lag, before any latency of "
                           "ours is added")})
    clocks["provider_observed_at"] = devig._epoch(quote.get("observed_at"))
    clocks["provider_received_at"] = (
        None if quote.get("received_at") is None
        else float(quote["received_at"]))
    if (clocks["provider_observed_at"] is not None
            and clocks["provider_received_at"] is not None):
        # THE PROVIDER'S OWN LAG, MEASURED. It is spent out of the same
        # 30 s budget as our latency, and a provider whose lag alone
        # exceeds the bound cannot serve this rule at any cadence.
        clocks["provider_lag_s"] = round(
            clocks["provider_received_at"] - clocks["provider_observed_at"], 3)
    link("3_PROVIDER_FIXTURE", True, provider_event_id=quote["event_id"],
         home=quote.get("home"), away=quote.get("away"),
         commence_time=quote.get("commence_time"),
         observed_at=quote.get("observed_at"),
         outcomes_priced=len(quote.get("prices") or {}),
         events_considered=considered,
         sharp_books_on_our_outcome=(quote.get("depth") or {}).get(
             str(payout_event_held)))

    # ── 4 · THE PROBABILITY, aged by the odds engine's own rule ──────
    book_rule = vset.BOOK_SETTLEMENT.get(family)
    contract = {"sport_family": family, "market": "h2h",
                "selection": payout_event_held,
                "event_key": quote["event_id"], "period": "FULL_GAME",
                "line": None, "settlement_rule": book_rule}
    asked_at = tick()
    clocks["probability_asked_at"] = asked_at
    val = devig.valuation(
        contract=contract,
        quote={"book": devig.BOOK, "outcomes": quote["prices"],
               "observed_at": quote["observed_at"],
               "received_at": quote["received_at"],
               "event_key": quote["event_id"], "period": "FULL_GAME",
               "line": None, "settlement_rule": book_rule},
        now=asked_at)
    if val.get("probability") is None:
        link("4_PROBABILITY", False,
             refusal=(val.get("refusals") or ["DEVIG_REFUSED"])[0],
             why=val.get("why"), all_refusals=val.get("refusals"),
             age_s=val.get("age_s"), max_age_s=val.get("max_age_s"),
             observed_at=val.get("observed_at"),
             asked_for=payout_event_held,
             outcomes_priced=val.get("outcomes_priced"),
             expected_outcomes=val.get("expected_outcomes"))
        return out
    link("4_PROBABILITY", True, probability=val["probability"],
         probability_event=val.get("mapped_outcome"),
         mapping_match=val.get("mapping_match"),
         devig_method=val.get("devig_method"),
         overround=val.get("overround"), age_s=val.get("age_s"),
         max_age_s=val.get("max_age_s"),
         observed_at=val.get("observed_at"),
         aged_against="THE BOOKMAKER'S OWN observed_at")
    # THE ROW SHAPE `bettor_hold_value` ALREADY CHECKS, built from this
    # pull rather than read from the entry lane's table. Eligibility is
    # ELIGIBLE by construction and the identity fields come from the two
    # independent sources link 2 reconciled -- there is no third opinion
    # here to adopt.
    out["probability_row"] = {
        "id": None, "pulled": True,
        "provider": devig.PROVIDER, "book": devig.BOOK,
        "devig_method": val.get("devig_method"), "version": devig.VERSION,
        "experiment_id": MANAGED_INPUT_VERSION,
        "us_market_slug": (vid or {}).get("us_market_slug"),
        "mapping_match": val.get("mapping_match"),
        "mapped_outcome": val.get("mapped_outcome"),
        "overround": val.get("overround"),
        "outcomes_priced": val.get("outcomes_priced"),
        "expected_outcomes": val.get("expected_outcomes"),
        "outcome_books": (quote.get("depth") or {}).get(
            str(val.get("mapped_outcome"))),
        "eligibility": "ELIGIBLE",
        "payout_event": payout_event_held,
        "probability_event": val.get("mapped_outcome"),
        "payout_is_complement": False,
        "buy_intent": intent,
        "matched_side_norm": (vid or {}).get("matched_side_norm"),
        "resolver_asked_for": payout_event_held,
        "ladder_side": (vid or {}).get("ladder_side"),
        "probability": val["probability"],
        "observed_at": val.get("observed_at"),
        "received_at": val.get("received_at"),
        "settlement_rule": book_rule,
        "event_key": quote["event_id"],
    }
    out["identity_matched_independently"] = True

    # ── 4b · SETTLEMENT COMPATIBILITY, ATTESTED PER FIXTURE ─────────
    #
    # The probability prices an event; the contract pays on one. Whether
    # those are the SAME event under every terminal case -- draw,
    # overtime, push, void -- is a settlement question, and it is asked
    # here per fixture from both catalogues rather than assumed. A rule
    # that is not established stays NOT_ESTABLISHED: HOLD_TO_SETTLEMENT is
    # refused for exactly that reason and nothing here manufactures the
    # compatibility to get a passing result.
    try:
        vevid = await EXT.venue_settlement_evidence(
            conn, vid["us_market_slug"]) if vid is not None else {}
    except Exception as exc:                                   # noqa: BLE001
        vevid = {"error": "%s: %s" % (type(exc).__name__, exc)}
    srule = vset.attest(
        sport_family=family, market="h2h", venue_evidence=vevid,
        book_evidence={"outcome_names": list(quote["prices"].keys()),
                       "source": "theoddsapi:h2h:%s" % devig.BOOK})
    out["settlement"] = {
        "book_rule": vset.BOOK_SETTLEMENT.get(family),
        "overall_established": bool(srule.get("overall_established")),
        "attested": srule.get("attested"),
        "unmet": srule.get("unmet"),
        "refusal": srule.get("refusal"),
        "venue_evidence_readable": bool(vevid.get("readable")),
        "draw_contract_present": vevid.get("draw_contract_present"),
        "why_it_matters": ("an unestablished terminal rule is why "
                           "HOLD_TO_SETTLEMENT is refused. It does not "
                           "stop a probability pricing the hold, and it "
                           "is never assumed in order to"),
    }
    link("4b_SETTLEMENT_COMPATIBILITY",
         bool(srule.get("overall_established")), blocks_chain=False,
         book_rule=vset.BOOK_SETTLEMENT.get(family),
         attested=srule.get("attested"), unmet=srule.get("unmet"),
         refusal=(None if srule.get("overall_established")
                  else (srule.get("refusal") or "SETTLEMENT_NOT_ESTABLISHED")),
         why=("every terminal rule attested from both catalogues"
              if srule.get("overall_established") else
              "these terminal rules are not established: %s"
              % (srule.get("unmet") or "unspecified")),
         blocks_only="HOLD_TO_SETTLEMENT",
         does_not_block="pricing the hold, or an exit at an observed price")

    # ── 5 · THE EXECUTABLE EXIT, off the ladder a close consumes ─────
    #
    # THE PROBABILITY IS IN HAND, so from here a failure costs the EXITS
    # and not the hold value.
    out["available"] = True
    if vid is None:
        link("5_EXIT_LADDER", False,
             refusal="NO_VENUE_CONTRACT_SO_NO_LADDER",
             why=("link 2 found no venue contract for this exposure, so "
                  "there is no ladder to read and no exit to price. HOLD "
                  "is priced; every exit is refused for want of a book"),
             hold_is_priced_anyway=True)
        out["book_available"] = False
        return out
    reader = read_book
    if reader is None:
        from .ext_pinnacle_loop import _read_book_blocking as reader
    try:
        book = await asyncio.to_thread(reader, vid["us_market_slug"])
        clocks["book_read_at"] = tick()
    except Exception as exc:                                   # noqa: BLE001
        link("5_EXIT_LADDER", False, refusal="VENUE_BOOK_READ_RAISED",
             why="%s: %s" % (type(exc).__name__, exc),
             us_market_slug=vid["us_market_slug"])
        out["book_available"] = False
        return out
    md = (book or {}).get("marketData")
    if md is None:
        link("5_EXIT_LADDER", False, refusal="VENUE_BOOK_UNREADABLE",
             why=("the venue read returned no marketData: %s"
                  % (book or {}).get("error")),
             diagnostic=(book or {}).get("diagnostic"),
             us_market_slug=vid["us_market_slug"])
        out["book_available"] = False
        return out
    xl = bs.exit_ladder(md, held_intent=intent)
    if not xl.get("ok"):
        link("5_EXIT_LADDER", False,
             refusal=xl.get("refusal") or bs.R_NO_EXIT_SIDE,
             why=xl.get("why"), us_market_slug=vid["us_market_slug"])
        out["book_available"] = False
        return out
    link("5_EXIT_LADDER", True, exit_price=xl["best_exit_price"],
         complement_price=xl["best_complement_price"],
         size_at_best=xl["size_at_best"],
         side_consumed=xl.get("side_consumed"),
         levels=len(xl.get("levels") or []))
    out["exit_ladder"] = xl
    out.update(
        available=True, book_available=True,
        held_is_long=not bs.is_short_intent(intent),
        bid=xl["best_exit_price"], bid_size=xl["size_at_best"],
        last_price=xl["best_exit_price"],
        complement_ask=xl["best_complement_price"],
        complement_ask_size=xl["size_at_best"],
        sale_ladder=bs.as_sale_ladder(xl),
        settlement_semantics=book_rule,
        price_denomination={
            "bid": "EXIT PROCEEDS per contract for the held exposure",
            "complement_ask": "COST per contract to neutralise it",
            "relation": bs.EXIT_RELATION,
            "side_consumed": xl.get("side_consumed")},
        input_labels={
            "ev_hold": "EXTERNAL_LABELLED_PROBABILITY (%s/%s), PULLED "
                       "FOR THIS DECISION" % (devig.PROVIDER,
                                              val.get("devig_method")),
            "bid": "OBSERVED venue ladder, exit proceeds",
            "complement_ask": "OBSERVED venue ladder, neutralising cost",
            "payout_event": "%s + %s, cross-checked"
                            % (ident["basis"], vid.get("resolver")),
            "clocks": dict(clocks),
            "probability_age_s_at_pull": val.get("age_s"),
            "freshness_is_rechecked_at": ("the DECISION's own clock. This "
                                          "age is the age at the PULL"),
        })
    clocks["inputs_ready_at"] = tick()
    out["inputs_ready_at"] = clocks["inputs_ready_at"]
    return out


# THE HOLD VALUE IS NOT BUILT HERE ANY MORE, AND THAT IS THE FIX.
#
# `_ev_at(snapshot, at)` used to live at this spot. It valued holding
# `snapshot["seed_qty"]` at `snapshot["seed_price"]` -- the position's
# ORIGINAL size and entry price -- and the worker called it BEFORE
# `manage_open_position` reloaded the portfolio and applied newly
# admitted fills. The ranking then scored the actual residual against
# that seed-sized number, so after a partial exit HOLD carried the value
# of contracts we no longer held:
#
#     seed 100 @ .57, residual 80, p .70, exit .72 on all 80
#     HOLD 13.00 (100 contracts) beat DIRECT_EXIT 12.00
#     correct HOLD 10.40 (80 contracts) loses to it
#
# It is deleted rather than corrected in place. Any helper here would be
# called before the reload by construction, because this module is what
# runs before the reload. `Managed.decide_challenger` is the only place
# that knows the residual and the basis at the decision instant, so it
# is where the valuation belongs; the snapshot carries the probability
# ROW and nothing derived from it.


# ── AN ACCEPTANCE POSITION ON A COVERED EXPOSURE ────────────────────
#
# The manager cannot be demonstrated on inventory whose inputs do not
# exist. The live challenger position is ATP tennis, which the provider
# set does not cover, and waiting for an RN1 seed to appear in a covered
# sport is waiting on a coincidence.
#
# So this creates a position the manager CAN price: a covered sport, a
# venue contract that resolves, a readable book, and a fixture the
# provider carries. Its entry is a MODELLED acquisition at the
# contemporaneous executable price with explicit fees.
#
# WHAT IT IS NOT, stated here and stamped on the row:
#   * NOT derived from an RN1 signal -- no whale, no trade id, no cohort;
#   * NOT an executed order -- nothing was submitted to any venue;
#   * NOT funded -- no capital moved and the execution flag stays off;
#   * NOT part of any benchmark result -- its policy id differs from both
#     the frozen benchmark's and the challenger's, and `provenance` marks
#     it for every read that reports performance.
ACCEPTANCE_POLICY = "ACCEPTANCE_SHADOW_MANAGER_DEMO_V1"
ACCEPTANCE_PROVENANCE = "ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY"
ACCEPTANCE_ENTRY_KIND = "ACCEPTANCE_MODELLED_ENTRY"
ACCEPTANCE_ACCOUNT = "ACCEPTANCE_HARNESS_NOT_AN_OBSERVED_ACCOUNT"

#: Small on purpose. The point is a decision, not a size.
ACCEPTANCE_QTY = 10.0

#: How many covered markets to try before giving up this call.
ACCEPTANCE_CANDIDATES = 8

R_NO_COVERED_CANDIDATE = "NO_COVERED_MARKET_COULD_SUPPLY_A_COMPLETE_ENTRY"

COVERED_CANDIDATES_SQL = """
    SELECT m.condition_id, m.title, m.event_title, m.slug, m.sport,
           m.closed, m.resolved,
           extract(epoch FROM m.updated_at)::float8 AS updated_at,
           extract(epoch FROM s.game_start)::float8 AS game_start
      FROM markets m
 LEFT JOIN market_starts s ON s.condition_id = m.condition_id
     WHERE NOT m.closed AND NOT m.resolved
       AND m.sport = ANY($1::text[])
       AND m.updated_at >= now() - make_interval(secs => $2::float8)
       -- BOTH TOKENS PRESENT, or the payout identity cannot be
       -- established from the tokens at all.
       AND (SELECT count(*) FROM market_tokens t
             WHERE t.condition_id = m.condition_id) >= 2
     ORDER BY m.updated_at DESC
     LIMIT $3
"""


async def seed_acceptance_position(conn, *, experiment_id, now=None,
                                   odds=None, clock=None,
                                   qty=ACCEPTANCE_QTY,
                                   candidates=ACCEPTANCE_CANDIDATES,
                                   resolve_identity=None, read_book=None,
                                   fee_fn=None):
    """Create ONE acceptance position on a covered exposure, or say why not.

    Returns the position and its provenance, or `created: False` with the
    per-candidate refusals. Writes NOTHING except the position row: no
    order, no fill, no valuation.
    """
    import time as _t

    from sportsassets import bettor_book_snapshot as bs

    from . import ext_pinnacle_loop as EXT

    tick = anchored_clock(now, clock)
    started = tick()
    out = {"created": False, "at": started, "experiment_id": experiment_id,
           "policy": ACCEPTANCE_POLICY,
           "provenance": ACCEPTANCE_PROVENANCE,
           "is_not": ["AN_RN1_SIGNAL", "AN_EXECUTED_ORDER", "FUNDED",
                      "PART_OF_ANY_BENCHMARK_RESULT"],
           "considered": [], "refusals": {}}
    labels = sorted({lbl for fam in {f for _, f in EXT.SPORTS}
                     for lbl in EXT.VENUE_SPORT_LABELS.get(fam, ())})
    out["covered_sport_labels"] = labels
    rows = [dict(r) for r in await conn.fetch(
        COVERED_CANDIDATES_SQL, labels, EXT.MARKET_STALE_AFTER_S,
        int(candidates))]
    out["candidates_examined"] = len(rows)
    if not rows:
        out["refusal"] = R_NO_COVERED_CANDIDATE
        out["why"] = ("no open market in a covered sport (%s) was updated "
                      "recently enough to try" % labels)
        return out

    odds = odds if odds is not None else ManagedOdds(
        api_key=os.environ.get("EDGE_ODDS_API_KEY"), clock=clock, now=now)
    fee = fee_fn or runner._fee_fn()
    _resolve = resolve_identity or EXT.resolve_venue_identity
    reader = read_book
    if reader is None:
        from .ext_pinnacle_loop import _read_book_blocking as reader

    for m in rows:
        note = {"condition_id": m["condition_id"], "sport": m.get("sport"),
                "title": (m.get("title") or "")[:60]}
        # THE PAYOUT IDENTITY FIRST, from the tokens, for BOTH sides -- the
        # entry has to name which one it takes.
        toks = [dict(r) for r in await conn.fetch(
            "SELECT outcome, outcome_index FROM market_tokens "
            "WHERE condition_id = $1 ORDER BY outcome_index",
            m["condition_id"])]
        if len(toks) < 2:
            note["refusal"] = "MARKET_TOKENS_INCOMPLETE"
            out["considered"].append(note)
            out["refusals"]["MARKET_TOKENS_INCOMPLETE"] = \
                out["refusals"].get("MARKET_TOKENS_INCOMPLETE", 0) + 1
            continue
        picked = toks[0]
        note["outcome"] = picked.get("outcome")
        note["outcome_index"] = picked.get("outcome_index")

        vid = await _resolve(conn, market_row=m,
                            priced_outcome=picked.get("outcome"))
        if not vid.get("ok"):
            code = vid.get("refusal") or "VENUE_CONTRACT_UNRESOLVED"
            note["refusal"] = code
            out["considered"].append(note)
            out["refusals"][code] = out["refusals"].get(code, 0) + 1
            continue
        note["us_market_slug"] = vid.get("us_market_slug")
        note["intent"] = vid.get("intent")

        # THE CONTEMPORANEOUS EXECUTABLE ENTRY PRICE, off the ladder the
        # intent names. This is the ACQUISITION side, and it is read now --
        # not modelled, not averaged, not taken from a valuation row.
        try:
            book = await asyncio.to_thread(reader, vid["us_market_slug"])
        except Exception as exc:                               # noqa: BLE001
            note["refusal"] = "VENUE_BOOK_READ_RAISED"
            note["detail"] = type(exc).__name__
            out["considered"].append(note)
            out["refusals"]["VENUE_BOOK_READ_RAISED"] = \
                out["refusals"].get("VENUE_BOOK_READ_RAISED", 0) + 1
            continue
        md = (book or {}).get("marketData")
        if md is None:
            note["refusal"] = "VENUE_BOOK_UNREADABLE"
            out["considered"].append(note)
            out["refusals"]["VENUE_BOOK_UNREADABLE"] = \
                out["refusals"].get("VENUE_BOOK_UNREADABLE", 0) + 1
            continue
        lad = bs.acquisition_ladder(md, intent=vid["intent"])
        if not lad.get("ok") or not (lad.get("levels") or []):
            code = lad.get("refusal") or "NO_ACQUISITION_DEPTH"
            note["refusal"] = code
            out["considered"].append(note)
            out["refusals"][code] = out["refusals"].get(code, 0) + 1
            continue
        best = lad["levels"][0]
        px = float(best["acquisition_price"])
        depth = float(best.get("qty") or 0.0)
        note["entry_price"] = px
        note["depth_at_price"] = depth
        if depth < float(qty):
            note["refusal"] = "INSUFFICIENT_DISPLAYED_DEPTH_FOR_THE_ENTRY"
            out["considered"].append(note)
            out["refusals"]["INSUFFICIENT_DISPLAYED_DEPTH_FOR_THE_ENTRY"] = \
                out["refusals"].get(
                    "INSUFFICIENT_DISPLAYED_DEPTH_FOR_THE_ENTRY", 0) + 1
            continue

        # AND THE INPUTS THE MANAGER WILL NEED. A position whose chain
        # cannot complete would demonstrate nothing, so the chain is walked
        # BEFORE the row is written and the candidate is refused if it
        # cannot be priced.
        ci = await managed_inputs_for(
            conn, condition_id=m["condition_id"],
            outcome_index=int(picked["outcome_index"]),
            odds=odds, now=tick(), clock=clock,
            read_book=reader, resolve_identity=_resolve)
        ffl = ci.get("first_failing_link") or {}
        if not (ci.get("available") and ci.get("book_available")):
            code = ffl.get("refusal") or "INPUT_CHAIN_INCOMPLETE"
            note["refusal"] = code
            note["first_failing_link"] = ffl.get("link")
            out["considered"].append(note)
            out["refusals"][code] = out["refusals"].get(code, 0) + 1
            continue
        note["chain"] = "COMPLETE"
        note["probability"] = (ci.get("probability_row") or {}).get(
            "probability")

        # ── THE ENTRY, WITH ITS FEE STATED ──────────────────────────
        entry_fee = float(fee(qty=float(qty), price=px, maker=False))
        basis = float(qty) * px + entry_fee
        at = tick()
        # A DETERMINISTIC SENTINEL, not a trade id. `source_trade_id` is
        # part of the position's unique key, and a NULL there would let the
        # same market be seeded twice; a hash of the venue slug makes a
        # repeat call idempotent instead. It is NEGATIVE so it can never be
        # mistaken for a real `trades.id`.
        sentinel = -(abs(hash(("ACCEPTANCE", vid["us_market_slug"])))
                     % 2_000_000_000)
        pid = store.position_id(experiment_id, ACCEPTANCE_POLICY, sentinel)
        await conn.execute(
            """INSERT INTO rn1x_positions(position_id, experiment_id, policy,
            source_trade_id, source_account, condition_id, outcome_index,
            entry_kind, entry_kind_why, seed_qty, seed_price, seed_basis_usd,
            source_ts, detected_ts, decision_ts, available_at,
            decision_basis, decision_lag_s, provenance)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::numeric,$11::numeric,
                   $12::numeric,to_timestamp($13),to_timestamp($13),
                   to_timestamp($13),to_timestamp($13),
                   'RUNTIME_WALL_CLOCK',0,$14)
            ON CONFLICT (position_id) DO NOTHING""",
            pid, experiment_id, ACCEPTANCE_POLICY, sentinel,
            ACCEPTANCE_ACCOUNT, m["condition_id"],
            int(picked["outcome_index"]), ACCEPTANCE_ENTRY_KIND,
            ("MODELLED acquisition of %.0f contracts at the "
             "contemporaneous executable price %.4f on %s (%s), fee %.4f "
             "by the published schedule. NOT an RN1 signal, NOT an "
             "executed order, NOT funded. Created to demonstrate the "
             "management lifecycle on a covered exposure."
             % (float(qty), px, vid["us_market_slug"], vid["intent"],
                entry_fee)),
            float(qty), px, basis, at, ACCEPTANCE_PROVENANCE)
        out.update(
            created=True, position_id=pid, condition_id=m["condition_id"],
            outcome_index=int(picked["outcome_index"]),
            payout_event=picked.get("outcome"),
            us_market_slug=vid["us_market_slug"], intent=vid["intent"],
            sport=m.get("sport"), title=m.get("title"),
            entry={"qty": float(qty), "price": px, "fee_usd": entry_fee,
                   "basis_usd": basis, "depth_at_price": depth,
                   "price_is": ("the CONTEMPORANEOUS executable acquisition "
                                "price off the ladder the intent names, "
                                "read at this instant"),
                   "fee_basis": "bettor_fee_schedule.LATEST.fill_fee",
                   "execution": ("MODELLED. No order was submitted and no "
                                 "capital moved")},
            inputs_at_entry={"probability": note.get("probability"),
                             "exit_price": ci.get("bid"),
                             "exit_size": ci.get("bid_size"),
                             "settlement": ci.get("settlement")},
            source_trade_id=sentinel,
            source_trade_id_is=("a NEGATIVE deterministic sentinel derived "
                                "from the venue slug, so a repeat call is "
                                "idempotent and it can never be mistaken "
                                "for a trades.id"))
        out["considered"].append(note)
        return out

    out["refusal"] = R_NO_COVERED_CANDIDATE
    out["why"] = ("every covered candidate refused: %s" % out["refusals"])
    return out


# ── THE CONTINUING MANAGEMENT PHASE ──────────────────────────────────
#
# THE DEFECT THIS CLOSES. `cycle` finds SEEDS and skips any whose
# position is already written. That is correct for entry -- a source
# trade opens one position -- and it meant a position was decided once
# and never again. This phase runs BESIDE the seed scan, over positions
# that are already open, and it is what makes the lane a manager rather
# than a one-time replayer.
MANAGE_BATCH = 5                 # positions re-evaluated per cycle

#: A WALL-CLOCK CEILING ON THE PHASE, because each position costs a PACED
#: venue book read and `venue_pace.pace` can sleep. Without a bound, five
#: slow reads extend the writer's cycle by however long the venue takes,
#: the heartbeat goes stale, and the operational read cannot tell a slow
#: venue from a stopped loop. Positions not reached are DEFERRED to the
#: next cycle -- they are still open and the phase runs every cycle, so
#: deferring costs a tick and loses nothing.
MANAGE_BUDGET_S = 45.0

_NEW_PRINTS_SQL = """
    SELECT id, outcome_index, side, size::float8 AS size,
           price::float8 AS price,
           extract(epoch FROM ts)::float8         AS ts,
           extract(epoch FROM detected_at)::float8 AS detected_at
      FROM trades
     WHERE condition_id = $1
       AND detected_at > to_timestamp($2)
       AND outcome_index IN (0, 1)
     ORDER BY ts, id
     LIMIT 400
"""


async def manage_open_positions(conn, *, experiment_id, limit=MANAGE_BATCH,
                                now=None, odds=None, clock=None) -> dict:
    """Re-evaluate positions that are already open. Never raises."""
    import time as _t

    tick = anchored_clock(now, clock)
    wall = tick()
    started = _t.monotonic()
    out = {"phase": "CONTINUING_MANAGEMENT", "at": wall,
           "examined": 0, "managed": 0, "acted": 0, "failed": 0,
           "no_inputs": 0, "deferred_budget": 0, "priced_holds": 0,
           "budget_s": MANAGE_BUDGET_S, "results": [],
           "input_source": MANAGED_INPUT_VERSION,
           "first_failing_links": {}}
    # ONE ODDS BUDGET FOR THE WHOLE BATCH. A provider request returns
    # every event of a sport key, so positions in one family share it and
    # the cost is per FAMILY, not per position. The ceiling is on the
    # payload so a pull that was not made cannot be read as a fixture the
    # provider does not carry.
    odds = odds if odds is not None else ManagedOdds(
        api_key=os.environ.get("EDGE_ODDS_API_KEY"), now=wall)
    try:
        open_pos = await store.open_positions(
            conn, experiment_id=experiment_id, limit=limit)
    except Exception as exc:                                   # noqa: BLE001
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
        return out
    out["examined"] = len(open_pos)
    for i, pos in enumerate(open_pos):
        if _t.monotonic() - started > MANAGE_BUDGET_S:
            # STOP BEFORE consuming this position: it has not been
            # examined, it is still open, and the next cycle will find it
            # first -- `open_positions` orders by least-recently-decided.
            out["deferred_budget"] = len(open_pos) - i
            out["why_deferred"] = (
                "the phase reached its %.0fs wall-clock budget. The "
                "remaining positions are DEFERRED, not skipped: they are "
                "still open and this phase runs every cycle"
                % MANAGE_BUDGET_S)
            break
        pid = pos["position_id"]
        try:
            rows = await store.load_position(conn, pid)
            # THE HELD POSITION'S OWN INPUTS, OBTAINED FOR THIS DECISION.
            # Reading the entry lane's table made a priced decision depend
            # on a 900 s writer coinciding with a 30 s freshness rule; the
            # pull asks the same adapters at the decision's own instant.
            ci = await managed_inputs_for(
                conn, condition_id=pos["condition_id"],
                outcome_index=int(pos["outcome_index"]),
                odds=odds, now=tick(), clock=clock)
            ci["seed_qty"] = float(pos["seed_qty"])
            ci["seed_price"] = float(pos["seed_price"])
            # THE DECISION'S OWN INSTANT, READ AFTER THE INPUTS ARRIVED.
            #
            # `wall` is when this batch STARTED. Using it as the decision
            # clock dated every decision to before its own inputs and
            # aged the probability against the wrong instant -- so a quote
            # that was fresh when the fetch began and stale by the time the
            # decision was taken would have been priced as fresh. The
            # decision is stamped here, and `decide_challenger` re-ages the
            # probability against THIS clock.
            at = tick()
            input_latency_s = round(at - wall, 3)
            if ci.get("first_failing_link"):
                _k = str((ci["first_failing_link"] or {}).get("link"))
                out["first_failing_links"][_k] = \
                    out["first_failing_links"].get(_k, 0) + 1
            # NO PRE-COMPUTED HOLD VALUE. `manage_open_position`
            # reloads the portfolio and applies newly admitted fills
            # BEFORE any decision, and `decide_challenger` values HOLD
            # on the residual that results. Computing it here, on the
            # seed, is the ordering defect 12260cb shipped.
            usable = dict(ci) if ci.get("available") else None
            if usable is None:
                out["no_inputs"] += 1
            # PRINTS SINCE THE LAST DECISION, not since entry: the
            # earlier ones were already offered to the orders that
            # existed then, and `Consumption` has them keyed.
            # THE PRINT WINDOW IS THE POSITION'S OWN HISTORY, not this
            # batch's clock: prints since the LAST decision on this
            # position, whenever that was.
            since = float(pos.get("last_decision_at")
                          or pos["decision_ts"])
            prints = [dict(r) for r in
                      await conn.fetch(_NEW_PRINTS_SQL,
                                       pos["condition_id"], since)]
            rec = runner.manage_open_position(
                position=pos, orders=rows["orders"], fills=rows["fills"],
                prints=prints, inputs=usable, now=at,
                unavailable=(None if usable else
                             {"reason": ci.get("reason"),
                              "why": ci.get("why")}),
                # THE CAUSE, NOT THE SUMMARY. Whatever the outcome, the
                # chain that produced it -- every link, its identifiers
                # and its timestamps -- is persisted with the decision,
                # so `EV_HOLD_NOT_IDENTIFIED` is never the whole story a
                # reader gets.
                input_chain={"version": ci.get("version"),
                             "source": ci.get("input_source"),
                             "read_at": ci.get("read_at"),
                             # EVERY CLOCK, SEPARATELY, ON THE ROW.
                             "clocks": ci.get("clocks"),
                             "inputs_ready_at": ci.get("inputs_ready_at"),
                             "decided_at": at,
                             "input_latency_s": input_latency_s,
                             "chain": ci.get("chain"),
                             "first_failing_link":
                                 ci.get("first_failing_link"),
                             "odds_request": ci.get("odds_request"),
                             "market_row": ci.get("market_row")},
                decisions_so_far=rows["decisions_so_far"])
            wrote = await store.persist_run(
                conn, rec, experiment_id=experiment_id,
                source_account=str(pos.get("source_account") or ""),
                decision_offset=rows["decisions_so_far"])
            d = rec["all_decisions"][0]
            out["managed"] += 1
            out["acted"] += 1 if d.get("acted") else 0
            # A PRICED HOLD IS THE THING THIS PHASE EXISTS FOR, so it is
            # counted apart from a decision that merely happened. Read
            # from the RANKED alternatives, which is where the store reads
            # `hold_value_usd` from too -- counting a different number
            # here than the one persisted is how a census starts
            # disagreeing with the rows it describes.
            _hold = None
            for _c in (d.get("alternatives") or ()):
                if _c.get("action") == "HOLD":
                    _hold = _c.get("value_usd")
            out["priced_holds"] += 1 if _hold is not None else 0
            out["results"].append({
                "position_id": pid,
                "decision_no": rows["decisions_so_far"] + 1,
                "selected": d.get("selected_action"),
                "qty": d.get("selected_qty"),
                "state": d.get("operating_state"),
                "input_available": bool(usable),
                "input_reason": ci.get("reason"),
                "first_failing_link": (ci.get("first_failing_link") or
                                       {}).get("link"),
                "probability": (ci.get("probability_row") or {}).get(
                    "probability"),
                "hold_value_usd": _hold,
                "bid": ci.get("bid"),
                "complement_ask": ci.get("complement_ask"),
                "decided_at": at,
                "input_latency_s": input_latency_s,
                "prints_applied": rec["steps"]["MANAGE"]["prints_applied"],
                "residual_after": rec["inventory_after"]["residual"],
                "reconciles": rec["accounting"]["reconciles"],
                "written": wrote.get("written"),
                "decisions_written": wrote.get("decisions"),
            })
        except Exception as exc:                               # noqa: BLE001
            log.exception("rn1x challenger management failed for %s", pid)
            out["failed"] += 1
            out["results"].append({"position_id": pid, "written": False,
                                   "error": "%s: %s"
                                            % (type(exc).__name__, exc)})
    out["elapsed_s"] = round(_t.monotonic() - started, 3)
    out["odds_calls_made"] = getattr(odds, "calls_made", None)
    out["odds_spent"] = getattr(odds, "spent", None)
    out["odds_credits_remaining"] = getattr(odds, "credits_remaining", None)
    out["odds_min_interval_s"] = MANAGED_ODDS_MIN_INTERVAL_S
    out["odds_cache_ttl_s"] = MANAGED_ODDS_CACHE_TTL_S
    # EVERY OPEN POSITION ACCOUNTED FOR, so a reader cannot mistake a
    # budget deferral for a position that went unmanaged.
    out["accounted"] = (out["examined"] ==
                        out["managed"] + out["failed"]
                        + out["deferred_budget"])
    return out


async def _replay_one(conn, *, trade_id, whale_id, condition_id,
                      prospective: bool = False,
                      challenger: bool = False) -> dict:
    lane_name = ("CHALLENGER" if challenger
                 else "PROSPECTIVE" if prospective else "HISTORICAL")
    rows = await conn.fetch(_CONDITION_ROWS, condition_id,
                            MAX_ROWS_PER_CONDITION)
    inv = await verify_initial_inventory(
        conn, whale_id=whale_id, condition_id=condition_id,
        trade_id=trade_id)
    mkt = await conn.fetchrow(
        "SELECT resolved_prices, extract(epoch FROM resolved_at)::float8 "
        "AS resolved_at FROM markets WHERE condition_id = $1", condition_id)

    payouts = None
    resolved_at = None
    # A PROSPECTIVE RUN READS NO PAYOUT, FULL STOP. Not "there happens not
    # to be one" -- the lane refuses it. A market that resolves between
    # the candidate query and this read would otherwise hand a prospective
    # decision its own outcome, which is look-ahead arriving by a race.
    if (not prospective) and mkt is not None and mkt["resolved_prices"] is not None:
        import json as _json
        raw = mkt["resolved_prices"]
        prices = _json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(prices, list):
            payouts = {i: float(p) for i, p in enumerate(prices)}
        elif isinstance(prices, dict):
            payouts = {int(k): float(v) for k, v in prices.items()}
        resolved_at = mkt["resolved_at"]

    # THE CLOCK IS READ HERE, in the runtime caller, and only the
    # prospective lane may use it. The historical lane is a counterfactual
    # replay of a market that has already settled -- stamping it with
    # today's wall clock would be a worse lie than the one being fixed --
    # so it keeps the availability basis and is labelled a replay.
    #
    # This is the line that decides whether the word "prospective" on
    # these rows means anything. Before it, the lane inherited
    # decision_ts = detected_ts from the runner's default and every row
    # was backdated to the instant its evidence arrived.
    import time as _time

    # ── the challenger's inputs, read once, before any decision ─────
    ci_fn = None
    ci_snapshot = None
    if challenger:
        seed = next((dict(r) for r in rows
                     if int(r["whale_id"]) == int(whale_id)
                     and r["side"] == "BUY"), None)
        if seed is not None:
            ci_snapshot = await challenger_inputs_for(
                conn, condition_id=condition_id,
                outcome_index=int(seed["outcome_index"]),
                seed_qty=float(seed["size"]), seed_price=float(seed["price"]))
            ci_snapshot["seed_qty"] = float(seed["size"])
            ci_snapshot["seed_price"] = float(seed["price"])

            def ci_fn(at, _s=ci_snapshot):                     # noqa: F811
                # UNAVAILABLE MEANS UNAVAILABLE. Returning a partially
                # filled dict here would let the ranking consume a book
                # or a hold value that was not established, so the whole
                # snapshot is withheld and the decision records a
                # missing input by name.
                if not _s.get("available"):
                    return None
                # THE SNAPSHOT CARRIES THE ROW, NOT A VALUE.
                # `decide_challenger` values HOLD against the residual
                # and basis it holds at that instant, and re-ages the
                # probability against that instant's clock -- so both the
                # QUANTITY and the AGE are the decision's own, not the
                # builder's.
                return dict(_s)

    out = runner.run(
        rows=[dict(r) for r in rows],
        payouts=payouts, resolved_at=resolved_at,
        source_whale_id=whale_id, condition_id=condition_id,
        initial_inventory_verified=inv["verified"],
        now=(_time.time() if prospective else None),
        decision_basis=(runner.BASIS_RUNTIME if prospective
                        else runner.BASIS_REPLAY),
        decision_policy=(runner.CHALLENGER if challenger
                         else runner.CHAMPION),
        challenger_inputs=ci_fn)
    if challenger:
        # THE INPUT SNAPSHOT IS PART OF THE EVIDENCE, available or not.
        # A cycle that decided nothing because no probability existed is
        # a finding about the valuation pipeline, and it is only
        # readable if the refusal is stored beside the run.
        out["challenger_inputs"] = {
            k: ci_snapshot.get(k) for k in
            ("available", "reason", "why", "valuation_row_id",
             "us_market_slug", "venue", "payout_event", "read_at", "book_available",
             "bid", "bid_size", "complement_ask", "complement_ask_size",
             "input_labels")} if ci_snapshot else {
            "available": False, "reason": "NO_SEED_ROW",
            "why": "no BUY fill by the source account in this condition"}
        if ci_snapshot and ci_snapshot.get("ev_hold"):
            _e = ci_snapshot["ev_hold"]
            out["challenger_inputs"]["ev_hold"] = {
                k: _e.get(k) for k in
                ("status", "refusal", "why", "probability",
                 "probability_event", "payout_event_held", "ev_hold_usd",
                 "freshness", "identity", "source_row_id")}
    out["initial_inventory_evidence"] = inv
    out["resolved_at"] = resolved_at
    out["blockers"] = sorted(BLOCKERS)
    out["lane"] = lane_name
    if prospective:
        out["prospective_notes"] = dict(PROSPECTIVE_NOTES)
        out["scored"] = False
    else:
        out["scored"] = payouts is not None
    return out


async def cycle(conn, *, lane: str = "HISTORICAL") -> dict:
    """One cycle on ONE lane. Never raises.

    `lane` selects the candidate query, the experiment id, the cursor and
    whether a payout may be read. The two lanes share this code path
    deliberately -- a separate prospective implementation would be a
    second experiment that could drift from the frozen policy -- but they
    share no identifier, no cursor and no total.
    """
    if lane not in LANES:
        raise ValueError("unknown lane %r" % (lane,))
    challenger = lane == "CHALLENGER"
    # THE CHALLENGER LANE IS PROSPECTIVE. It reads no payout, for the
    # same reason the prospective lane does not: a policy scored against
    # an outcome it was allowed to see has not been tested.
    prospective = lane in ("PROSPECTIVE", "CHALLENGER")
    running, why = await _running(conn)
    if not running:
        return {"ran": False, "state": "STOPPED", "why": why}

    rdy = await store.ready(conn)
    if not rdy["ok"]:
        return {"ran": False, "state": "BLOCKED", "why": rdy["blocker"],
                "missing_tables": rdy["missing"]}

    experiment_id = (CHALLENGER_EXPERIMENT_ID if challenger
                     else PROSPECTIVE_EXPERIMENT_ID if prospective
                     else HISTORICAL_EXPERIMENT_ID)
    cursor_key = (CHALLENGER_CURSOR_KEY if challenger
                  else PROSPECTIVE_CURSOR_KEY if prospective
                  else HISTORICAL_CURSOR_KEY)
    # THE POLICY THAT KEYS THE POSITION ROW. `position_id` derives from
    # (experiment, policy, trade), so using the champion's id on the
    # challenger lane would key both arms' rows identically and the
    # already-replayed check would make each arm skip the other's seeds.
    lane_policy = (_sel.CHALLENGER_ID if challenger else pol.POLICY_ID)
    query = (_CANDIDATES_PROSPECTIVE if prospective
             else _CANDIDATES_HISTORICAL)
    sources = list(LIVE_SOURCES if prospective else SEEDABLE_SOURCES)

    await store.declare_experiment(
        conn, experiment_id=experiment_id,
        code_version=runner.VERSION,
        seed_rule={
            "lane": lane,
            "source": ("cohort BUY fill in `trades` on an UNRESOLVED "
                       "market, live lanes only"
                       if prospective else
                       "cohort BUY fill in `trades` on a RESOLVED market"),
            "trades_source_filter": sources,
            "excludes": ("'s1' -- migration 033: the shadow instrument "
                         "must never read the emitter's own rows"),
            "venue": "POLYMARKET_GLOBAL_POLYGON",
            "event_class": "SINGLE_ACCOUNT_EXECUTIONS",
            "seed_price": "RN1's own fill price, ASSIGNED",
            "cursor_start": ("feed head on first start"
                             if prospective else "0 (walks the record)"),
            "not_evidence_of": ("that we could have obtained this fill"),
        },
        policy_register=({"policy_id": _sel.CHALLENGER_ID,
                          "policy_class": _sel.CHALLENGER_CLASS,
                          "objective": _sel.CHALLENGER_OBJECTIVE,
                          "fallback": _sel.FALLBACK_DECLARATION,
                          "benchmark": pol.POLICY_ID}
                         if challenger else pol.describe()),
        execution_basis=EXECUTION_BASIS,
        notes=(("PROSPECTIVE SHADOW. Decisions recorded before the market "
                "resolved; NOT scored, and never summed with a scored "
                "result." if prospective else
                "HISTORICAL REPLAY, scored against the observed payout.")
               + " Management-defined and experimental; not learned and "
                 "not proven profitable."))

    start = await _cursor(conn, cursor_key, seed_at_head=prospective)
    cands = await conn.fetch(query, start, SCAN_BATCH, sources)
    census = await lane_census(conn)
    if not cands:
        # PERSIST THE POSITION EVEN WHEN NOTHING WAS FOUND. Returning here
        # without saving is what made the prospective lane incapable of
        # ever producing a position: the seed lived only in this return
        # value, so the next cycle found no stored row, re-seeded at the
        # NEW head, and every row that had arrived in between was skipped
        # permanently. The only candidate it could ever catch was one
        # inserted between `max(id)` and the query microseconds later.
        #
        # Production stated it plainly and I read past it four times:
        # `rn1x_prospective_cursor` was ABSENT from `ingestion_state` while
        # the lane reported IDLE_NO_CANDIDATES every cycle, against 9,444
        # eligible live BUYs a day.
        #
        # This is safe for the historical lane and changes nothing there:
        # `start` is the value already stored (or 0 when absent, which is
        # what absence already means), so it never moves the cursor
        # backwards and never past unexamined evidence.
        await _save_cursor(conn, start, cursor_key)
        return {"ran": True, "state": "IDLE_NO_CANDIDATES", "lane": lane,
                "cursor": start, "cursor_persisted": True,
                "lane_census": census}

    seen_conditions: set[str] = set()
    results = []
    # EVERY CANDIDATE ACCOUNTED FOR. The old pair of numbers was "examined
    # 400, refusals 3", and both were true and neither was the whole story:
    # `examined` counted the SCAN, not the processing, and three branches
    # below skip a row with `continue` and no counter at all. A reader
    # comparing 400 to 3 could only conclude that 397 rows vanished.
    #
    # MAX_SEEDS_PER_CYCLE is 3, so three processed rows is the CAP being
    # reached, not a sample of the 400. These counters say so.
    flow = {"fetched": len(cands), "processed": 0,
            "duplicate_condition_in_batch": 0, "already_replayed": 0,
            "deferred_cap": 0, "refused": 0, "failed": 0, "written": 0,
            "not_reached_after_error": 0}
    # THE CURSOR IS THE HIGHEST ROW WHOSE PROCESSING FINISHED, and
    # nothing beyond it. `safe_id` only ever takes the id of a row that
    # was written, refused for a stated reason, or deliberately skipped.
    # A row whose write FAILED never advances it, so the next cycle sees
    # that row again instead of the evidence being lost for good.
    #
    # This is the desk's recovery defect in another place: there, a
    # rollback left the cursor advanced and the loop skipped to a newer
    # feed head. A cursor that moves past a write that did not commit
    # loses records silently, which is the worst available outcome --
    # worse than reprocessing, because reprocessing is idempotent here
    # and the loss is not recoverable.
    safe_id = start
    stopped_at_error = None
    for i, r in enumerate(cands):
        rid = int(r["id"])
        if len(results) >= MAX_SEEDS_PER_CYCLE:
            # Stop BEFORE consuming this row: it has not been examined.
            # Everything from here to the end of the batch is DEFERRED to
            # a later cycle, not refused and not lost -- the cursor stays
            # below it.
            flow["deferred_cap"] = len(cands) - i
            break
        if r["condition_id"] in seen_conditions:
            # One condition, one seed per cycle: a second fill on the same
            # market is the same candidate, not a new one.
            flow["duplicate_condition_in_batch"] += 1
            safe_id = rid
            continue
        seen_conditions.add(r["condition_id"])
        # Already replayed under this experiment and policy? The store's
        # key is derived, so this is a cheap check rather than a second
        # write that would be a no-op anyway.
        pid = store.position_id(experiment_id, lane_policy, rid)
        if await conn.fetchval(
                "SELECT 1 FROM rn1x_positions WHERE position_id = $1", pid):
            # Idempotence, not a refusal: this trade already has its
            # position under this experiment and policy.
            flow["already_replayed"] += 1
            safe_id = rid
            continue
        try:
            out = await _replay_one(
                conn, trade_id=rid, whale_id=int(r["whale_id"]),
                condition_id=r["condition_id"], prospective=prospective,
                challenger=challenger)
            wrote = await store.persist_run(
                conn, out, experiment_id=experiment_id,
                source_account=str(r["whale_id"]))
        except Exception as exc:                               # noqa: BLE001
            log.exception("rn1x replay failed for trade %s", rid)
            results.append({"trade_id": rid, "written": False,
                            "error": "%s: %s" % (type(exc).__name__, exc)})
            # STOP HERE. Continuing would process later rows and then
            # save a cursor above this one, which is how the failed row
            # would be skipped.
            stopped_at_error = rid
            flow["failed"] += 1
            flow["processed"] += 1
            flow["not_reached_after_error"] = len(cands) - i - 1
            break
        results.append({"trade_id": rid,
                        "condition_id": r["condition_id"], **wrote})
        flow["processed"] += 1
        if wrote.get("written"):
            flow["written"] += 1
        else:
            flow["refused"] += 1
        safe_id = rid

    # CONTINUING MANAGEMENT RUNS EVERY CYCLE, and only on the challenger
    # lane: the frozen benchmark's entry-and-walk shape is what it was
    # specified as and is not being changed here.
    managed = None
    if challenger:
        managed = await manage_open_positions(
            conn, experiment_id=experiment_id)

    await _save_cursor(conn, safe_id, cursor_key)
    # THE REFUSAL DISTRIBUTION, not just the count of writes. "400
    # examined, 0 written" is the same line whether every candidate was
    # refused for a stated reason or the first one raised and the lane
    # never got past it -- and those need opposite responses. Tallied by
    # the reason each result actually carries.
    tally: dict = {}
    for x in results:
        if x.get("written"):
            key = "WRITTEN"
        elif x.get("error"):
            key = "ERROR:" + str(x["error"]).split(":")[0]
        else:
            key = str(x.get("refused_at") or "REFUSED_UNSPECIFIED")
            if x.get("unknown_reason"):
                key += "/" + str(x["unknown_reason"])
        tally[key] = tally.get(key, 0) + 1
    # THE IDENTITY, CHECKED. Every fetched row is in exactly one bucket.
    # If this ever fails the census is lying, and a boolean in the payload
    # is how a reader finds that out without re-deriving it by hand.
    flow["accounted"] = (
        flow["fetched"] == (flow["processed"]
                            + flow["duplicate_condition_in_batch"]
                            + flow["already_replayed"]
                            + flow["deferred_cap"]
                            + flow["not_reached_after_error"]))
    flow["cap_per_cycle"] = MAX_SEEDS_PER_CYCLE
    flow["scan_batch"] = SCAN_BATCH
    flow["reading"] = (
        "fetched = rows the scan returned; processed = rows that reached a "
        "replay, capped at cap_per_cycle; the rest are duplicates of a "
        "condition already seeded this batch, positions already written, "
        "or rows deferred to a later cycle. `examined` is kept as an alias "
        "of `fetched` for the older readers and is the number that was "
        "misread as 'processed'")
    return {"ran": True, "state": "REPLAYED", "lane": lane,
            "cursor": safe_id, "examined": len(cands), "flow": flow,
            "management": managed,
            "results": results,
            "stopped_at_error": stopped_at_error,
            "refusals": tally,
            "cursor_moved": safe_id != start,
            "lane_census": census,
            "written": sum(1 for x in results if x.get("written"))}


# ── ONE WRITER, AND A STANDBY THAT ACTUALLY RETRIES ─────────────────
#
# Its OWN lock key, not the desk's: these are two different books and a
# shared key would make one of them silently a standby of the other.
#
# The retry is the audit's defect 7, which I am not repeating here. The
# desk's standby did `if not acquired: while True: sleep` and never asked
# for the lock again, so a standby could never take over -- and that is
# the containment incident at 16:41 I had attributed to a deploy. This
# loop asks again every cycle, BEFORE any startup work.
LOCK_KEY = 7723901544120032


def enabled() -> bool:
    """The env pre-check. NOT the authority -- the control row is."""
    return not _off("RN1X_SHADOW", "off")


async def run(pool_factory=None) -> None:
    """Contend for the writer lock, then cycle. Hosted in the API.

    WHY THE API AND NOT THE WORKER SERVICE. sportsassets-api and
    sportsassets-workers both track the same auto-deploy branch, so one
    push to it restarts the observation collector mid-window and writes a
    PROCESS_REPLACED gap -- that has already happened once. The API can
    be released by commit id without any push, which is how the desk loop
    came to be hosted here too. This is a deployment constraint, recorded
    rather than worked around.
    """
    get = pool_factory or get_pool
    log.info("rn1x shadow: armed; contending for the writer lock. The "
             "CONTROL ROW decides whether it runs -- absence is not "
             "permission.")
    pool = await get()
    async with pool.acquire() as conn:
        # THE LOCK IS SESSION-SCOPED, so it must be held on ONE
        # connection for the loop's whole life -- not acquired and
        # returned to the pool each cycle, which would release it.
        while not await conn.fetchval(
                "SELECT pg_try_advisory_lock($1)", LOCK_KEY):
            log.info("rn1x shadow STANDBY: another process holds the "
                     "writer lock; retrying in %ss", IDLE_S)
            # STANDBY_SERVICE, NOT SERVICE. Writing the writer's own row
            # here replaced its cycle -- flow counts, refusals and all --
            # with an empty standby record every IDLE_S. Run 26 read
            # `flow {}` and `accounted null` for exactly that reason, and
            # the flow accounting it was meant to show had been deployed
            # for an hour.
            # `con=conn` for the same reason as the cycle beat below: this
            # loop is already holding a connection, and asking the shared
            # pool for a second one is what starves request traffic.
            await heartbeat(STANDBY_SERVICE, "idle",
                            {"state": "STANDBY_NOT_THE_WRITER",
                             "why": ("another process holds the rn1x "
                                     "writer lock. This process writes "
                                     "nothing and retries")},
                            con=conn)
            await asyncio.sleep(IDLE_S)
        log.info("rn1x shadow: writer lock held")
        while True:
            delay = TICK_S
            try:
                # BOTH LANES, EVERY CYCLE, in this order. Prospective runs
                # first: it consumes what has just arrived, and making it
                # wait behind a multi-month backfill would be the same
                # mistake as seeding its cursor at zero. Each lane keeps
                # its own result so the heartbeat shows two states, never
                # one blended one.
                res_p = await cycle(conn, lane="PROSPECTIVE")
                res_h = await cycle(conn, lane="HISTORICAL")
                # THE CHALLENGER LAST, and gated by its own control key.
                # It runs the SAME seeds through a different decision
                # policy, so it must never delay the lane whose totals
                # the frozen benchmark depends on, and it must be
                # switchable off without touching either of them.
                res_c = ({"ran": False, "state": "OFF",
                          "why": "rn1x_challenger is off"}
                         if _off("rn1x_challenger") else
                         await cycle(conn, lane="CHALLENGER"))
                # A COMPACT SUMMARY THAT SURVIVES TRUNCATION. The
                # operational readback prints left(detail, 1400) and the
                # historical lane's `results` array fills that on its own,
                # so the prospective lane -- the one whose state is in
                # question -- was invisible in every production read I
                # took. jsonb orders keys by length then bytewise, so a
                # 5-character key lands before "state" and well inside the
                # window. This adds no facts; it makes the ones already
                # there readable without widening the query.
                def _sum(r):
                    return {"state": r.get("state"),
                            # EVERY CANDIDATE ACCOUNTED FOR, in the summary
                            # the command centre reads. Without it the tile
                            # showed "examined 400, refusals 3" and a
                            # reader had to guess at the other 397.
                            "flow": r.get("flow") or {},
                            "cursor": r.get("cursor"),
                            "examined": r.get("examined", 0),
                            "written": r.get("written", 0),
                            "moved": r.get("cursor_moved"),
                            "stopped": r.get("stopped_at_error"),
                            "refusals": r.get("refusals") or {}}
                res = {"lanes": {"P": _sum(res_p), "H": _sum(res_h),
                                 "C": _sum(res_c)},
                       "prospective": res_p, "historical": res_h,
                       "challenger": res_c,
                       "lane_census": (res_h.get("lane_census")
                                       or res_p.get("lane_census"))}
                states = (res_p.get("state"), res_h.get("state"),
                          res_c.get("state"))
                if "STOPPED" in states:
                    res["state"] = "STOPPED"
                elif "BLOCKED" in states:
                    res["state"] = "BLOCKED"
                elif "REPLAYED" in states:
                    res["state"] = "REPLAYED"
                else:
                    res["state"] = "IDLE_NO_CANDIDATES"
                idle = res["state"] in ("STOPPED", "BLOCKED",
                                        "IDLE_NO_CANDIDATES")
                delay = IDLE_S if idle else TICK_S
                # ON THE CONNECTION THIS LOOP ALREADY HOLDS. Acquiring a
                # second one from the shared pool is what lost this
                # record -- and with it `flow` -- seven times in three
                # hours, including during acceptance run 27.
                await heartbeat(SERVICE, "idle" if idle else "ok", res,
                                con=conn)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                           # noqa: BLE001
                log.exception("rn1x shadow cycle failed")
                delay = IDLE_S
                try:
                    await heartbeat(SERVICE, "error",
                                    {"error": "%s: %s"
                                     % (type(exc).__name__, exc)},
                                    con=conn)
                except Exception:                              # noqa: BLE001
                    # The held connection may be the casualty. Fall back
                    # to the pool so an error heartbeat is still attempted
                    # rather than silently dropped.
                    try:
                        await heartbeat(SERVICE, "error",
                                        {"error": "%s: %s"
                                         % (type(exc).__name__, exc)})
                    except Exception:                          # noqa: BLE001
                        pass
            await asyncio.sleep(delay)


async def main() -> None:
    """Worker-service entry point, kept for parity. NOT registered.

    `workers/all.py` deliberately does not list this loop: registering it
    there would require a push to the shared auto-deploy branch, which
    restarts the collector. If the worker service is ever given its own
    branch, this is the entry point -- and the advisory lock above means
    running it in both places would still leave exactly one writer.
    """
    if not enabled():
        log.info("rn1x shadow: RN1X_SHADOW is not on; not starting")
        return
    await run()

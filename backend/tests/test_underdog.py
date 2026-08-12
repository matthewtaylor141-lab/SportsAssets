"""Underdog cash-out sleeve v2 ($2 / +35%, owner restart 2026-08-12):
dog selection, sizing, the take-profit trigger, the strict pre-start
entry window, and the independence contract's pure pieces."""

from sportsassets.workers.underdog import (
    cash_out_threshold,
    pick_underdog,
    shares_for,
)


def test_the_dog_is_the_cheaper_side_inside_the_band():
    assert pick_underdog([("fav", 0.62), ("dog", 0.36)]) == ("dog", 0.36)
    # Order of sides must not matter.
    assert pick_underdog([("dog", 0.36), ("fav", 0.62)]) == ("dog", 0.36)


def test_ties_junk_and_unpriced_sides_are_refused():
    # Band widened 2026-08-09 (owner: "every single mlb game and every
    # single tennis match"): a 50c side with a pricier opponent IS the
    # dog now. Refusals left: no strictly cheaper side, sub-3c lottery
    # tickets, unpriced or missing sides.
    assert pick_underdog([("a", 0.51), ("b", 0.52)]) is None   # past 50c
    assert pick_underdog([("a", 0.49), ("b", 0.49)]) is None   # nobody cheaper
    assert pick_underdog([("a", 0.02), ("b", 0.97)]) is None   # sub-3c junk
    assert pick_underdog([("a", None), ("b", 0.40)]) is None   # unpriced side
    assert pick_underdog([("a", 0.40)]) is None                # one-sided


def test_band_edges_are_inclusive_where_they_should_be():
    assert pick_underdog([("a", 0.50), ("b", 0.52)]) == ("a", 0.50)
    assert pick_underdog([("a", 0.48), ("b", 0.55)]) == ("a", 0.48)
    assert pick_underdog([("a", 0.03), ("b", 0.96)]) == ("a", 0.03)


def test_cash_out_threshold_is_thirty_five_percent_on_entry():
    """v2 (owner 2026-08-12): +35% — roughly $0.70 on a full $2 fill."""
    assert cash_out_threshold(0.20) == 0.27
    assert cash_out_threshold(0.40) == 0.54
    # $2 at 0.25 -> 8 contracts; selling at 0.3375 realizes $0.70.
    assert cash_out_threshold(0.25) == 0.3375


def test_v2_defaults_are_two_dollars_and_thirty_five_percent():
    from sportsassets.workers import underdog as ud

    assert ud.PER_FILL_USD == 2.00
    assert ud.TAKE_PROFIT == 0.35
    assert ud.ENTRY_GRACE_S == 0.0
    # asyncpg refuses a string for a timestamptz param — the era
    # boundary must be a tz-aware datetime (first deploy shipped a
    # string and the whole ud2_* scorecard silently vanished).
    from datetime import datetime
    assert isinstance(ud.V2_SINCE, datetime)
    assert ud.V2_SINCE.tzinfo is not None


def test_sizing_never_exceeds_the_stake():
    assert shares_for(2.00, 0.25) == 8     # exactly $2.00
    assert shares_for(2.00, 0.30) == 6     # $1.80, never $2.10
    assert shares_for(2.00, 0.48) == 4     # $1.92
    assert shares_for(2.00, 0.0) == 0


def test_one_fill_index_ignores_the_underdog_sleeve():
    """The migration's partial index must scope around 'underdog' exactly
    as it does 'manual' — the sleeve neither blocks nor is blocked."""
    sql = open("migrations/016_underdog_sleeve.sql").read()
    assert "NOT IN ('manual', 'underdog')" in sql
    assert "'cashed_out'" in sql


def test_kud_queue_migration_is_one_task_per_game():
    """The Kalshi leg queues via UNIQUE(game_slug) + ON CONFLICT DO
    NOTHING — the enqueue itself is the one-per-game guarantee."""
    sql = open("migrations/017_kud_queue.sql").read()
    assert "game_slug    text NOT NULL UNIQUE" in sql
    assert "kud_queue" in sql


def test_kud_enqueue_rides_the_entry_sweep():
    """The worker must enqueue the Kalshi leg BEFORE the PMUS held-veto:
    a copy holding the game on Polymarket does not cancel the $1 Kalshi
    test (the engine runs its own held check against the Kalshi book)."""
    src = open("sportsassets/workers/underdog.py").read()
    assert "INSERT INTO kud_queue" in src
    assert "ON CONFLICT (game_slug) DO NOTHING" in src
    assert src.index("INSERT INTO kud_queue") < src.index(
        "NON-INTERFERENCE and one-entry-per-game")


def test_kud_backfill_covers_every_open_pmus_dog():
    """Owner 2026-08-08: 'make sure we aren't missing any.' Every sweep
    re-asserts that each OPEN $1 Polymarket position has its Kalshi
    task — a standing invariant, not a one-shot migration — and the
    failed-copy recovery net retries unfilled/errored copies."""
    src = open("sportsassets/workers/underdog.py").read()
    assert src.count("INSERT INTO kud_queue") == 2   # backfill + entry
    assert "lo.whale_username = 'underdog' AND lo.status = 'filled'" in src
    sweep = open("sportsassets/workers/copy_sweep.py").read()
    assert "('rejected', 'unfilled', 'error')" in sweep


def _meta(**kw):
    return {"sport": "Tennis", "closed": False, "resolved": False,
            "tokens": [{}, {}],
            "slug": "wta-jespeg-diashn-2026-08-08", **kw}


def test_discovery_keeps_only_todays_bare_game_markets():
    """Owner report 2026-08-08: tennis $1 plays not firing — the catalog
    only knew whale-traded markets. Discovery pulls the slate from the
    venue; this filter is what keeps it to the sleeve's exact universe."""
    from sportsassets.workers.underdog import _is_game_market

    today = "2026-08-08"
    assert _is_game_market(_meta(), today)
    assert _is_game_market(
        _meta(sport="MLB", slug="mlb-tor-phi-2026-08-08"), today)
    # Tomorrow's match, derivative suffix, wrong sport, wrong token
    # count, closed market: all refused.
    assert not _is_game_market(
        _meta(slug="wta-jespeg-diashn-2026-08-09"), today)
    assert not _is_game_market(
        _meta(sport="MLB", slug="mlb-tor-phi-2026-08-08-f5-tor"), today)
    assert not _is_game_market(
        _meta(sport="Soccer", slug="epl-ars-che-2026-08-08"), today)
    assert not _is_game_market(_meta(tokens=[{}]), today)
    assert not _is_game_market(_meta(closed=True), today)


def test_gamma_start_times_parse_or_refuse():
    from sportsassets.workers.underdog import _parse_start

    assert _parse_start("2026-08-08T23:05:00Z") == 1786230300.0
    assert _parse_start("2026-08-08 23:05:00+00") == 1786230300.0
    assert _parse_start("garbage") is None
    assert _parse_start(None) is None


def test_entries_fire_only_in_the_t_minus_five_window():
    """v2: 'exactly 5 minutes before' — the window is [T-5min, start];
    a game already underway is never entered (grace 0)."""
    from sportsassets.workers.underdog import entry_window

    start = 1_000_000.0
    assert entry_window(start, start - 600) == "wait"     # 10 min out
    assert entry_window(start, start - 300) == "enter"    # window opens
    assert entry_window(start, start - 30) == "enter"     # 30s before
    assert entry_window(start, start + 30) == "missed"    # in play
    assert entry_window(start, start + 120) == "missed"   # in play
    assert entry_window(None, start) == "unknown"         # no venue time


def test_copy_guards_scope_around_the_sleeve():
    """Owner 2026-08-12: the restarted sleeve is 'completely
    independent from everything we do'. The copy path's never-add,
    one-position-per-game and venue no-stack must all scope around
    whale_username='underdog' — the sleeve's $2 dogs neither consume
    a game's one copy slot nor read as a copy stack."""
    src = open("sportsassets/live_executor.py").read()
    never_add = src.index("never-add: this market was already copied")
    one_per_game = src.index("one position per game")
    no_stack = src.index("no-stack: account already holds")
    for anchor in (never_add, one_per_game):
        window = src[anchor - 1500:anchor]
        assert "<> 'underdog'" in window, "guard must exclude the sleeve"
    assert "bool_or(whale_username = 'underdog')" in src[
        no_stack - 1800:no_stack], (
        "a venue holding explained ONLY by the sleeve must not refuse "
        "the copy; unexplained holdings still fail closed")


def test_attempt_telemetry_separates_never_tried_from_refused():
    """A sleeve at zero entries must say WHICH zero it is.

    2026-08-12: the sleeve took no entries for nine hours and the
    heartbeat could not distinguish "never reached the entry" from
    "tried every window and the venue refused" — the scorecard counts
    only rows that held inventory, and the per-sweep stats are a fresh
    dict sampled almost never inside a 5-minute window.
    """
    src = open("sportsassets/workers/underdog.py").read()
    att = src.index('out["ud2_attempts"]')
    window = src[att - 700:att + 900]
    assert "placed_at >= $1" in window, "attempts must be era-scoped"
    assert "V2_SINCE" in window
    # The refusal statuses are the whole point: an attempt that never
    # filled still writes a row, and its status is the diagnosis.
    for status in ("unfilled", "error", "submitting"):
        assert status in window
    assert 'out["ud2_last_refusal"]' in src, \
        "the newest refusal's reason must ride the heartbeat"


def test_no_silent_exit_can_swallow_an_entry():
    """Every early return in _try_enter leaves a trace.

    Two `n < 1` returns counted nothing and wrote nothing, so an entry
    lost there was indistinguishable from an entry never attempted —
    which is the state the sleeve sat in all of 2026-08-12. A return is
    acceptable if it either increments a sweep stat or writes the row's
    outcome to live_orders; both are readable afterwards.
    """
    src = open("sportsassets/workers/underdog.py").read()
    body = src[src.index("async def _try_enter"):src.index("# PASS 1")]
    assert body.count('stats["skipped_dust"]') == 2, \
        "both sub-contract exits must be counted"
    lines = [ln.strip() for ln in body.split("\n")]
    for i, ln in enumerate(lines):
        if ln != "return":
            continue
        prior = " ".join(lines[max(0, i - 4):i])
        assert "stats[" in prior or "UPDATE live_orders" in prior, \
            f"untraceable return near: {prior[:80]!r}"


def test_entry_prices_on_the_venue_it_actually_trades():
    """The limit must come from Polymarket US, not the global CLOB.

    2026-08-12: the dog was quoted on the global book and the order sent
    to Polymarket US — a different venue with a different book — so the
    limit was a price that venue never agreed to. Five entries, five
    "unfilled@0.51:expired", zero positions all day. The cash-out leg
    already re-quotes the US slug; the entry has to as well.
    """
    src = open("sportsassets/workers/underdog.py").read()
    body = src[src.index("async def _try_enter"):src.index("# PASS 1")]
    submit = body.index("pmus.submit_fok")
    requote = body.index("pmus.slug_ask")
    mapped = body.index("resolve_market_exact")
    assert mapped < requote < submit, \
        "re-quote the mapped US slug BEFORE the order is sent"
    # The limit and the stored entry reference both ride the US price.
    limit_line = body[body.index("limit = round(min("):][:60]
    assert "us_ask" in limit_line, "limit must be built from the US ask"
    assert "'BUY', $3" in body and "], us_ask, limit," in body, \
        "his_price must record the venue's ask, not the global one"
    # No US quote is a refusal, never a guess.
    gap = body[requote:submit]
    assert "if us_ask is None" in gap and "return" in gap


def test_venue_price_must_clear_the_band_too():
    """A side that is the dog globally can be priced past the band on
    the venue; a 70c 'underdog' is not the bet that was ordered."""
    src = open("sportsassets/workers/underdog.py").read()
    body = src[src.index("async def _try_enter"):src.index("# PASS 1")]
    gap = body[body.index("pmus.slug_ask"):body.index("pmus.submit_fok")]
    assert "MIN_ASK <= us_ask <= MAX_ASK" in gap, \
        "the venue's own price must clear the underdog band"
    assert 'stats["skipped_band"]' in gap

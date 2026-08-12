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

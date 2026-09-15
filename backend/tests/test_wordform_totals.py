"""Word-form totals grammar + doubleheader guard (mapper-fail
diagnosis 2026-08-30, adversarially verified before build).

The feed emits totals in TWO grammars: the single-token 'o8pt5' the
exact lane always parsed, and the word form 'total-8pt5' — which fell
into the spread parser, was absorbed as team='total', and died at
_spread_exact's unknown-qualifier refusal while the venue listed the
tsc- market the whole time. MLB and soccer totals were the two largest
winnable classes in the unmapped funnel (census: mlb 7d 5,016, soccer
totals col 2,843 / arg 2,059 / ucl 1,348 all-time).

The word form carries no o/u token, so the side must come from the
whale's OUTCOME, exactly once, with any stated number equal to the
slug's own line; the swapped team order is tried only when the primary
slug is unlisted; and on a doubleheader day the game-agnostic
candidate refuses outright (fail closed on an unreadable table).
"""

import asyncio

import pytest

from sportsassets import live_executor, pmus


def _fd(slug):
    return pmus._feed_derivative(slug)


# ── grammar: exactly two tokens, bare line ───────────────────────────

def test_word_form_total_parses():
    fd = _fd("mlb-nyy-bos-2026-07-22-total-8pt5")
    assert fd == {"base": "mlb-nyy-bos-2026-07-22", "kind": "total",
                  "line": "8pt5", "side": None, "team": None}


def test_totals_plural_and_whole_number():
    fd = _fd("arg-tig-cac-2026-08-24-totals-2")
    assert fd is not None and fd["kind"] == "total"
    assert fd["line"] == "2" and fd["side"] is None


def test_decorated_line_token_refuses():
    assert _fd("mlb-nyy-bos-2026-07-22-total-1pt5x") is None


def test_team_total_three_tokens_never_matches():
    # a team total is a different market; 3 tokens must not parse as
    # the game total
    assert _fd("mlb-nyy-bos-2026-07-22-nyy-total-2pt5") is None


def test_total_games_prop_never_matches():
    assert _fd("atp-sin-alc-2026-08-24-total-games-22pt5") is None


def test_single_token_grammar_unchanged():
    fd = _fd("mlb-nyy-bos-2026-07-22-o8pt5")
    assert fd == {"base": "mlb-nyy-bos-2026-07-22", "kind": "total",
                  "line": "8pt5", "side": "o", "team": None}


# ── resolver: outcome names the side; numbers must agree ─────────────

class _Markets:
    def __init__(self, table):
        self.table = table

    def retrieve_by_slug(self, slug):
        if slug not in self.table:
            raise KeyError(slug)
        return {"market": self.table[slug]}


class _Client:
    def __init__(self, table):
        self.markets = _Markets(table)


def _table(slug):
    return {slug: {
        "slug": slug, "closed": False,
        "question": "Yankees vs Red Sox: O/U 8.5",
        "marketSides": [
            {"identifier": f"{slug}-over", "description": "Over"},
            {"identifier": f"{slug}-under", "description": "Under"},
        ]}}


WORD_SLUG = "mlb-nyy-bos-2026-07-22-total-8pt5"
VENUE = "tsc-mlb-nyy-bos-2026-07-22-8pt5"


def _use(monkeypatch, table):
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client(table))


def test_outcome_word_drives_the_side(monkeypatch):
    _use(monkeypatch, _table(VENUE))
    r = pmus.resolve_derivative_exact(WORD_SLUG, "Under")
    assert r is not None and r["market_slug"].endswith("-under")
    assert r["matched_by"] == "derivative_exact"
    assert r["intent"] == "ORDER_INTENT_BUY_LONG"
    r2 = pmus.resolve_derivative_exact(WORD_SLUG, "Over")
    assert r2 is not None and r2["market_slug"].endswith("-over")


def test_outcome_number_must_equal_the_slug_line(monkeypatch):
    _use(monkeypatch, _table(VENUE))
    assert pmus.resolve_derivative_exact(WORD_SLUG, "Over 8.5") \
        is not None
    # 'Over 2.5' on a total-8pt5 slug is a contradiction, not a match
    assert pmus.resolve_derivative_exact(WORD_SLUG, "Over 2.5") is None


def test_outcome_without_over_under_refuses(monkeypatch):
    _use(monkeypatch, _table(VENUE))
    assert pmus.resolve_derivative_exact(WORD_SLUG, "Yes") is None
    assert pmus.resolve_derivative_exact(WORD_SLUG, None) is None
    # both words is ambiguity, never a coin flip
    assert pmus.resolve_derivative_exact(WORD_SLUG,
                                         "over or under") is None


def test_single_token_side_still_wins_over_outcome_absence(monkeypatch):
    # the o/u-token grammar keeps its existing behavior verbatim
    _use(monkeypatch, _table(VENUE))
    r = pmus.resolve_derivative_exact("mlb-nyy-bos-2026-07-22-o8pt5",
                                      "Over 8.5")
    assert r is not None and r["market_slug"].endswith("-over")


# ── swapped team order: only when the primary is unlisted ────────────

def test_swapped_order_maps_when_primary_unlisted(monkeypatch):
    swapped = "tsc-mlb-bos-nyy-2026-07-22-8pt5"
    _use(monkeypatch, _table(swapped))
    r = pmus.resolve_derivative_exact(WORD_SLUG, "Under")
    assert r is not None
    assert r["market_slug"] == f"{swapped}-under"


def test_listed_primary_that_fails_never_falls_to_the_swap(monkeypatch):
    # primary EXISTS but its sides cannot corroborate the outcome; the
    # swapped sibling would corroborate — refusing outright must win
    primary_bad = {VENUE: {
        "slug": VENUE, "closed": False,
        "question": "Yankees vs Red Sox: O/U 8.5",
        "marketSides": [
            {"identifier": f"{VENUE}-a", "description": "Yes"},
            {"identifier": f"{VENUE}-b", "description": "No"},
        ]}}
    table = dict(primary_bad)
    table.update(_table("tsc-mlb-bos-nyy-2026-07-22-8pt5"))
    _use(monkeypatch, table)
    assert pmus.resolve_derivative_exact(WORD_SLUG, "Under") is None


# ── doubleheader guard: dh siblings refuse, fail closed ──────────────

class _DhPool:
    def __init__(self, hit=None, boom=False):
        self.hit = hit
        self.boom = boom
        self.seen = None

    async def fetchval(self, sql, *args):
        if self.boom:
            raise RuntimeError("db down")
        self.seen = args[0]
        return self.hit


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture()
def _loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


def test_dh_sibling_discards_the_mapping(_loop):
    pool = _DhPool(hit=1)
    assert _loop.run_until_complete(
        live_executor._dh_sibling_guard(pool, WORD_SLUG)) is True
    # both team orders and both derivative families are consulted
    assert sorted(pool.seen) == sorted([
        "tsc-mlb-nyy-bos-2026-07-22-dh%",
        "tsc-mlb-bos-nyy-2026-07-22-dh%",
        "asc-mlb-nyy-bos-2026-07-22-dh%",
        "asc-mlb-bos-nyy-2026-07-22-dh%",
    ])


def test_no_dh_siblings_keeps_the_mapping(_loop):
    assert _loop.run_until_complete(
        live_executor._dh_sibling_guard(_DhPool(hit=None),
                                        WORD_SLUG)) is False


def test_unreadable_table_fails_closed(_loop):
    assert _loop.run_until_complete(
        live_executor._dh_sibling_guard(_DhPool(boom=True),
                                        WORD_SLUG)) is True


def test_unparseable_slug_is_not_the_guards_business(_loop):
    # a slug the derivative grammar cannot read never built an exact
    # mapping, so the guard has nothing to remove
    assert _loop.run_until_complete(
        live_executor._dh_sibling_guard(_DhPool(hit=1),
                                        "not-a-derivative")) is False

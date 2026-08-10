"""The inning cover: Tie(seg) + Over 0.5(seg) >= $1 payout on every
branch (owner design 2026-08-09). A cover, not a partition — the scored
tie pays both legs and the arithmetic deliberately ignores that upside."""

import tempfile

import pytest

import edge.shadow.runner as runner_mod
from edge.analysis.consistency import Leg, find_cover_book
from edge.ledger.service import Ledger
from edge.shadow.runner import _try_arbitrage


def _leg(outcome, token, price, size=40):
    return Leg(outcome=outcome, token=token, price=price, size=size)


@pytest.fixture(autouse=True)
def _fresh_cover_seen():
    """Dark-mode telemetry dedupes by claim key across cycles; tests need
    each to start unseen."""
    runner_mod._COVER_SEEN.clear()
    yield
    runner_mod._COVER_SEEN.clear()


def test_cover_locks_when_the_pair_prices_under_a_dollar():
    b = find_cover_book("e", "i1", _leg("[i1] Tie", "t1", 0.53),
                        _leg("[i1] Over 0.5", "t2", 0.45))
    assert b is not None
    assert b.cost == 0.98 and b.profit_per_set == 0.02
    assert b.kind == "cover i1" and b.sets == 40


def test_cover_refuses_thin_margins_fees_and_junk():
    tie, over = _leg("[i1] Tie", "t1", 0.53), _leg("[i1] Over 0.5", "t2", 0.45)
    # At or past $1 there is nothing locked (module floor is 1c).
    assert find_cover_book("e", "i1", tie,
                           _leg("[i1] Over 0.5", "t2", 0.47)) is None
    # A Kalshi-scale fee on both legs eats the whole window.
    assert find_cover_book("e", "i1", tie, over,
                           fee_per_contract=0.0175) is None
    # Same token twice, bad price, no depth: all refused.
    assert find_cover_book("e", "i1", tie,
                           _leg("[i1] Over 0.5", "t1", 0.45)) is None
    assert find_cover_book("e", "i1", _leg("[i1] Tie", "t1", 0.0),
                           over) is None
    assert find_cover_book("e", "i1", tie,
                           _leg("[i1] Over 0.5", "t2", 0.45, size=0)) is None


class _Ev:
    home, away = "Orioles", "Rangers"
    sport_key = "baseball_mlb"
    league_code = "mlb"
    h2h = {"a": 1, "b": 2}

    def event_key(self):
        return "bal-tex"


class _Pmus:
    name = "polymarket-us"

    def place_order(self, *a, **kw):  # dry-run never reaches this
        raise AssertionError("dry run must not place")


@pytest.fixture()
def led():
    return Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")


def _legs(adapter, tie_price=0.53, over_price=0.45, seg="i1"):
    return [
        (adapter, "m1", _leg(f"[{seg}] Tie", "tok-tie", tie_price)),
        (adapter, "m1", _leg(f"[{seg}] Over 0.5", "tok-over", over_price)),
        (adapter, "m1", _leg("Orioles", "tok-bal", 0.46)),
        (adapter, "m1", _leg("Rangers", "tok-tex", 0.56)),
    ]


# Source-market titles that pass the identity whitelist (go-live
# 2026-08-10): the tie leg from an inning-WINNER market, the over leg
# from the combined-RUNS total. Everything the whitelist is built to
# refuse is exercised in the identity tests below.
def _titles(seg="i1"):
    word = "1st Inning" if seg == "i1" else "First 5 Innings"
    return {"tok-tie": f"{word} Winner",
            "tok-over": f"{word} Total Runs O/U 0.5"}


def test_try_arbitrage_fires_the_cover_in_dry_run(led, monkeypatch):
    monkeypatch.setenv("EDGE_COVER_ARB", "1")
    funnel: dict = {}
    fired = _try_arbitrage(ledger=led, ev=_Ev(), venue_legs=_legs(_Pmus()),
                           expected=2, sets=1, dry_run=True, funnel=funnel,
                           max_usd=50.0, leg_titles=_titles())
    assert fired and funnel.get("cover_found") == 1
    assert funnel["arb_books"][0]["cost"] == 0.98


def test_cover_is_live_by_default_with_verified_titles(led, monkeypatch):
    """Owner authorization 2026-08-09/10: with the identity gate in
    place the class fires by default; EDGE_COVER_ARB=0 re-darkens."""
    monkeypatch.delenv("EDGE_COVER_ARB", raising=False)
    funnel: dict = {}
    fired = _try_arbitrage(ledger=led, ev=_Ev(), venue_legs=_legs(_Pmus()),
                           expected=2, sets=1, dry_run=True, funnel=funnel,
                           max_usd=50.0, leg_titles=_titles())
    assert fired and funnel.get("cover_found") == 1


def test_title_gate_refuses_identity_masquerades(led, monkeypatch):
    """Every identity leak the adversarial verification confirmed: a
    TEAM total, a stat prop, a cumulative leader market, and a missing
    title must all refuse the pair — never guess."""
    monkeypatch.setenv("EDGE_COVER_ARB", "1")
    bad_overs = ["Orioles 1st Inning O/U 0.5",       # team total
                 "1st Inning Hits O/U 0.5",          # stat prop
                 "Judge 1st Inning Total Bases O/U 0.5",
                 None]                               # no title at all
    for bad in bad_overs:
        funnel: dict = {}
        titles = {**_titles(), "tok-over": bad}
        assert not _try_arbitrage(
            ledger=led, ev=_Ev(), venue_legs=_legs(_Pmus()), expected=2,
            sets=1, dry_run=True, funnel=funnel, max_usd=50.0,
            leg_titles=titles)
        assert funnel.get("cover_title_refused") == 1, bad
    # Cumulative tie market masquerading as the inning winner.
    funnel2: dict = {}
    titles2 = {**_titles(), "tok-tie": "Leader after 1st Inning"}
    assert not _try_arbitrage(
        ledger=led, ev=_Ev(), venue_legs=_legs(_Pmus()), expected=2,
        sets=1, dry_run=True, funnel=funnel2, max_usd=50.0,
        leg_titles=titles2)
    assert funnel2.get("cover_title_refused") == 1


def test_f5_locks_are_counted_dark_never_fired(led, monkeypatch):
    """F5 stays telemetry-only until per-market void rules are read: a
    rain-shortened game can settle its two condition markets under
    different clauses."""
    monkeypatch.setenv("EDGE_COVER_ARB", "1")
    funnel: dict = {}
    fired = _try_arbitrage(
        ledger=led, ev=_Ev(), venue_legs=_legs(_Pmus(), seg="f5"),
        expected=2, sets=1, dry_run=True, funnel=funnel, max_usd=50.0,
        leg_titles=_titles(seg="f5"))
    assert not fired
    assert funnel.get("cover_dark") == 1
    assert funnel["cover_locks_seen"][0]["seg"] == "f5"


def test_env_kill_switch_re_darkens_the_class(led, monkeypatch):
    """EDGE_COVER_ARB=0: a real lock is COUNTED and PRICED in telemetry
    but never bought — one env change stands the whole class down."""
    monkeypatch.setenv("EDGE_COVER_ARB", "0")
    funnel: dict = {}
    fired = _try_arbitrage(ledger=led, ev=_Ev(), venue_legs=_legs(_Pmus()),
                           expected=2, sets=1, dry_run=True, funnel=funnel,
                           max_usd=50.0, leg_titles=_titles())
    assert not fired and funnel.get("cover_dark") == 1
    assert funnel["cover_locks_seen"][0]["cost"] == 0.98


def test_cover_never_pairs_across_segments_or_sports(led):
    funnel: dict = {}
    # Tie in i1 but the only Over 0.5 is i2: no pair, nothing fires.
    legs = [
        (_Pmus(), "m1", _leg("[i1] Tie", "tok-tie", 0.53)),
        (_Pmus(), "m1", _leg("[i2] Over 0.5", "tok-over", 0.45)),
    ]
    assert not _try_arbitrage(ledger=led, ev=_Ev(), venue_legs=legs,
                              expected=2, sets=1, dry_run=True,
                              funnel=funnel, max_usd=50.0)

    class _Soccer(_Ev):
        sport_key = "soccer_epl"

    # Same shape on soccer: 'Over 0.5 goals' does not mean 'someone
    # scored in a way that excludes 0-0 ties' — refused by sport gate.
    assert not _try_arbitrage(ledger=led, ev=_Soccer(),
                              venue_legs=_legs(_Pmus()), expected=3, sets=1,
                              dry_run=True, funnel={}, max_usd=50.0)


def test_cover_refuses_the_fee_loaded_venue(led):
    class _Kalshi(_Pmus):
        name = "kalshi"

        def taker_fee(self, price):
            return 0.07 * price * (1 - price)

    assert not _try_arbitrage(ledger=led, ev=_Ev(),
                              venue_legs=_legs(_Kalshi()), expected=2,
                              sets=1, dry_run=True, funnel={}, max_usd=50.0)


def test_under_half_never_masquerades_as_the_over(led):
    legs = [
        (_Pmus(), "m1", _leg("[i1] Tie", "tok-tie", 0.40)),
        (_Pmus(), "m1", _leg("[i1] Under 0.5", "tok-under", 0.40)),
    ]
    # Tie + NRFI is the owner's original (uncovered) construction — the
    # scanner must never build it: a team winning the inning beats both.
    assert not _try_arbitrage(ledger=led, ev=_Ev(), venue_legs=legs,
                              expected=2, sets=1, dry_run=True, funnel={},
                              max_usd=50.0)


# ---------------------------------------------------------------------------
# Adversarial-verification fixes (2026-08-09): everything below pins the
# hardening the 14 confirmed findings demanded.
# ---------------------------------------------------------------------------


def test_segment_moneyline_pool_requires_all_three_sides(led):
    """A tie-missing home+away segment pool must NEVER pass as a complete
    partition — the venue's segment winner is 3-way, whatever the feed's
    full-game h2h says. This was the Mets/Pirates F5 loss."""
    funnel: dict = {}
    pm = _Pmus()
    two = [
        (pm, "m1", _leg("[f5] Orioles", "tok-bal5", 0.46)),
        (pm, "m1", _leg("[f5] Rangers", "tok-tex5", 0.44)),
    ]
    assert not _try_arbitrage(ledger=led, ev=_Ev(), venue_legs=two,
                              expected=2, sets=1, dry_run=True,
                              funnel=funnel, max_usd=50.0)
    # With the tie present and the full 3-way priced under $1, the same
    # segment IS a legitimate dutch — need=3 enables it, not just blocks.
    three = two + [(pm, "m1", _leg("[f5] Tie", "tok-tie5", 0.05))]
    fired = _try_arbitrage(ledger=led, ev=_Ev(), venue_legs=three,
                           expected=2, sets=1, dry_run=True,
                           funnel=funnel, max_usd=50.0)
    assert fired and funnel["arb_books"][0]["legs"] == 3


def test_cover_only_builds_first_inning(led, monkeypatch):
    """Late innings are out: cumulative-tie wording and half-played-9th
    settlement cannot be told apart from the per-inning proposition."""
    monkeypatch.setenv("EDGE_COVER_ARB", "1")
    for seg in ("i2", "i7", "i9"):
        funnel: dict = {}
        assert not _try_arbitrage(
            ledger=led, ev=_Ev(), venue_legs=_legs(_Pmus(), seg=seg),
            expected=2, sets=1, dry_run=True, funnel=funnel, max_usd=50.0)
        assert "cover_found" not in funnel


def test_cover_requires_exactly_mlb(led, monkeypatch):
    """Substring 'baseball' admitted NCAA 7-inning doubleheaders and
    KBO/NPB shortened-game conventions. Exact key only."""
    monkeypatch.setenv("EDGE_COVER_ARB", "1")

    class _Ncaa(_Ev):
        sport_key = "baseball_ncaa"

    assert not _try_arbitrage(ledger=led, ev=_Ncaa(),
                              venue_legs=_legs(_Pmus()), expected=2, sets=1,
                              dry_run=True, funnel={}, max_usd=50.0)


def test_two_over_shaped_legs_refuse_the_pair(led, monkeypatch):
    """Two markets answering to one canonical key is an identity
    ambiguity (team total vs game total) — refuse, never guess."""
    monkeypatch.setenv("EDGE_COVER_ARB", "1")
    funnel: dict = {}
    pm = _Pmus()
    legs = [
        (pm, "m1", _leg("[i1] Tie", "tok-tie", 0.30)),
        (pm, "m1", _leg("[i1] Over 0.5", "tok-over-a", 0.45)),
        (pm, "m1", _leg("[i1] Over 0.5", "tok-over-b", 0.60)),
    ]
    assert not _try_arbitrage(ledger=led, ev=_Ev(), venue_legs=legs,
                              expected=2, sets=1, dry_run=True,
                              funnel=funnel, max_usd=50.0,
                              leg_titles=_titles())
    assert funnel.get("cover_ambiguous") == 1


class _FillNone(_Pmus):
    def place_order(self, token, price, sets, **kw):
        return {"ok": False}


class _FillFirstOnly(_Pmus):
    def __init__(self):
        self.placed = 0

    def place_order(self, token, price, sets, **kw):
        self.placed += 1
        if self.placed == 1:
            return {"ok": True, "count": sets, "price": price}
        return {"ok": False}


def test_clean_miss_does_not_burn_the_claim(led, monkeypatch):
    """FOK misses on in-play books are transient; claim-before-execution
    turned each one into a permanent blacklist. Claim only when venue
    money moved."""
    monkeypatch.setenv("EDGE_COVER_ARB", "1")
    funnel: dict = {}
    fired = _try_arbitrage(ledger=led, ev=_Ev(),
                           venue_legs=_legs(_FillNone()), expected=2,
                           sets=1, dry_run=False, funnel=funnel,
                           max_usd=50.0, leg_titles=_titles())
    assert fired and funnel["arb_status"].get("no_fills") == 1
    assert led.get_state("arb_tried:m1:cover-i1") is None
    # Same market next cycle: still eligible — it retries.
    runner_mod._COVER_SEEN.clear()
    funnel2: dict = {}
    assert _try_arbitrage(ledger=led, ev=_Ev(),
                          venue_legs=_legs(_FillNone()), expected=2,
                          sets=1, dry_run=False, funnel=funnel2,
                          max_usd=50.0, leg_titles=_titles())


def test_exposed_leg_claims_and_freezes_the_class(led, monkeypatch):
    """One stranded leg is disqualifying (owner directive 2026-08-08):
    the claim burns AND the whole arb class self-freezes for 6h."""
    monkeypatch.setenv("EDGE_COVER_ARB", "1")
    funnel: dict = {}
    fired = _try_arbitrage(ledger=led, ev=_Ev(),
                           venue_legs=_legs(_FillFirstOnly()), expected=2,
                           sets=1, dry_run=False, funnel=funnel,
                           max_usd=50.0, leg_titles=_titles())
    assert fired and funnel["arb_status"].get("INCOMPLETE_EXPOSED") == 1
    assert led.get_state("arb_tried:m1:cover-i1") is not None
    blk = led.get_state("arb_exposed_block")
    assert blk and blk["until"] > __import__("time").time()
    # Frozen: nothing else may fire, loudly counted.
    funnel2: dict = {}
    assert not _try_arbitrage(ledger=led, ev=_Ev(),
                              venue_legs=_legs(_Pmus()), expected=2, sets=1,
                              dry_run=True, funnel=funnel2, max_usd=50.0)
    assert funnel2.get("arb_blocked_exposed") == 1

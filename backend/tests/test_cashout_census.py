"""The census that has to answer HOW MUCH — /api/admin/cashout-census.

The investigation into the owner's hand cash-outs established the
mechanism completely and the magnitude not at all, because the
environment it ran in has no database: every dollar in that plan is a
synthetic probe input. So the first thing built is not a fix. It is the
instrument, and the instrument is the gate — nothing that moves a
published total ships before a human has read its numbers.

That makes the failure modes of a census the thing under test here,
because a census that is wrong is worse than none: it authorises a
restatement of numbers the owner has already read and acted on.

  * A figure that could not be read is NULL WITH A REASON. Never zero.
    Zero says "we looked and there is nothing"; that is a claim, and
    this endpoint must never make it on a read that did not happen.
  * A read that hit its cap makes its figure null too — EVERY read.
    A floor served as a total under-sizes a restatement, which is the
    one direction that cannot be caught by eye.
  * A CEILING IS NOT A MOVE. The gate figure is what the dollars would
    ADD to the copy scoreline; the scoreline is windowed from an epoch
    and grades only settled entry rows, so a lifetime settle-agnostic
    sum is an upper bound on the move and must be split, not served as
    one number. The error direction is UP, which authorises a bigger
    restatement than the pages will ever show.
  * EVIDENCE BOUNDS DOLLARS. A slug-level share shortfall is evidence
    that some shares left. It is not evidence that every copy row on
    the slug, and its whole stake, went with them — and shares other
    rows explain are not the copy rows' at all.
  * ABSENT IS NOT SOLD. The venue's positions feed prunes settled
    markets, so a resolved market and a sold-flat one look identical in
    it. Folding those together would turn the settlement sweep's
    ordinary backlog into evidence of hand sales.
  * EXPIRED IS NOT SOLD, same reason, decidable case.
  * A PARTIAL sale across two of our rows on one slug is REFUSED, not
    allocated. A full flatten is decidable and is not refused.
  * It writes nothing. The pool here traps every write method.
"""

import ast
import asyncio
import inspect
from datetime import datetime, timedelta

import pytest

from sportsassets.api import app as app_mod


# ───────────────────────────── the harness ─────────────────────────────

class _Trap(Exception):
    pass


class _Pool:
    """Answers planned queries by a marker substring; traps every write.

    An unplanned query is an assertion failure rather than an empty
    result: a census that silently answers zero to a query nobody
    planned is exactly the bug these tests exist to catch.
    """

    def __init__(self, plan):
        self.plan = list(plan)
        self.sqls = []

    async def fetch(self, sql, *args):
        self.sqls.append(sql)
        for marker, rows in self.plan:
            if marker in sql:
                if isinstance(rows, Exception):
                    raise rows
                return rows
        raise AssertionError(f"unplanned query: {sql[:160]!r}")

    async def execute(self, *a, **k):
        raise _Trap("the census executed a statement")

    async def executemany(self, *a, **k):
        raise _Trap("the census executed statements")

    async def fetchval(self, *a, **k):
        raise _Trap("the census used fetchval (unbounded by construction)")

    async def fetchrow(self, *a, **k):
        raise _Trap("the census used fetchrow (unbounded by construction)")


# markers, one per read the endpoint makes
M_SELLS = "upper(COALESCE(lo.side"
M_PEERS = "AS manual_buy_rows"
M_ZERO = "AS reaper_retired"
M_BLIND = "AS copy_pnl_usd"
M_KALSHI = "manual_kalshi_queue"
M_LEDGER = "AS claimed_shares"

def _zrow(rows=0, copy_rows=0, copy_stake_usd=0.0, reaper_retired=0,
          desk_manual=0, first_day=None, last_day=None,
          copy_rows_in_window=None, copy_stake_usd_in_window=None):
    """The published_at_zero aggregate. The `_in_window` pair defaults to
    the LIFETIME pair, i.e. a world where every $0.00 copy line settled
    inside the served window — a test that wants the two to differ says
    so, which is the whole point of serving them apart."""
    return {"rows": rows, "copy_rows": copy_rows,
            "copy_stake_usd": copy_stake_usd,
            "copy_rows_in_window": (copy_rows if copy_rows_in_window is None
                                    else copy_rows_in_window),
            "copy_stake_usd_in_window": (
                copy_stake_usd if copy_stake_usd_in_window is None
                else copy_stake_usd_in_window),
            "reaper_retired": reaper_retired, "desk_manual": desk_manual,
            "first_day": first_day, "last_day": last_day}


_ZERO_EMPTY = [_zrow()]
_BLIND_EMPTY = [{"rows": 0, "pnl_usd": 0.0, "copy_rows": 0,
                 "copy_pnl_usd": 0.0}]

# The epoch `/api/copies-record` is served from in this environment.
# DERIVED, never hardcoded: COPIES_EPOCH is an environment override, and
# a suite that pins the window to a literal would pass for the wrong
# reason the moment it moved. IN_WINDOW is the epoch day ITSELF, which
# also pins that the boundary is inclusive — `build` drops `day <
# since_day`, so a line dated exactly on the epoch is served.
EPOCH = app_mod.COPIES_EPOCH
IN_WINDOW = EPOCH
BEFORE_EPOCH = (datetime.strptime(EPOCH, "%Y-%m-%d")
                - timedelta(days=1)).strftime("%Y-%m-%d")


def _plan(**over):
    """An empty world; every test overrides only what it is about."""
    base = {M_SELLS: [], M_PEERS: [], M_ZERO: _ZERO_EMPTY,
            M_BLIND: _BLIND_EMPTY, M_KALSHI: [], M_LEDGER: []}
    base.update(over)
    return list(base.items())


def _run(monkeypatch, plan, positions=None, venue_raises=None,
         venue_calls=None):
    pool = _Pool(plan)

    async def _gp():
        return pool

    monkeypatch.setattr(app_mod, "get_pool", _gp)

    from sportsassets.api import pmus_account

    def _pos():
        if venue_calls is not None:
            venue_calls.append(1)
        if venue_raises is not None:
            raise venue_raises
        return positions if positions is not None else {}

    monkeypatch.setattr(pmus_account, "_fetch_all_positions_sync", _pos)
    return asyncio.run(app_mod.api_cashout_census()), pool


def _sell(i, slug, status="cashed_out", pnl=None, day="2026-09-01"):
    return {"id": i, "slug": slug, "status": status, "pnl": pnl, "day": day}


def _peer(slug, copy_entry_rows=1, manual_buy_rows=0, copy_stake=0.0,
          copy_pnl=0.0, copy_settled_rows=None, copy_settled_day=IN_WINDOW,
          non_copy_holding_rows=0):
    """A slug's peer counts. By default the copy entry row has ALREADY
    settled inside the window, i.e. the case where an attributed dollar
    really does move the served total, and NO non-copy sleeve holds the
    slug — so the sale can only have been the copy row's shares."""
    if copy_settled_rows is None:
        copy_settled_rows = 1 if copy_entry_rows else 0
    return {"slug": slug, "copy_entry_rows": copy_entry_rows,
            "manual_buy_rows": manual_buy_rows, "copy_stake": copy_stake,
            "copy_pnl": copy_pnl, "copy_settled_rows": copy_settled_rows,
            "non_copy_holding_rows": non_copy_holding_rows,
            "copy_settled_day": copy_settled_day if copy_settled_rows
            else None}


def _led(slug, holding_rows=1, claimed_shares=0.0, copy_rows=1,
         copy_stake=0.0, copy_claimed_shares=None, copy_exiting_rows=0,
         short_rows=0):
    """One ledger slug. `copy_claimed_shares` defaults to the whole slug
    claim — the case where every holding row on the slug IS a copy row."""
    if copy_claimed_shares is None:
        copy_claimed_shares = claimed_shares
    return {"slug": slug, "holding_rows": holding_rows,
            "claimed_shares": claimed_shares, "copy_rows": copy_rows,
            "copy_claimed_shares": copy_claimed_shares,
            "copy_exiting_rows": copy_exiting_rows,
            "short_rows": short_rows, "copy_stake": copy_stake}


def _pos(net, expired=False):
    return {"netPosition": net, "expired": expired}


# ───────────────────── it is an instrument, not an actor ─────────────────

class TestItCannotChangeAnything:
    def test_it_writes_nothing(self, monkeypatch):
        """Every write method on the pool raises. Reaching one is the
        failure — a census on the money path must be provably incapable
        of moving a dollar, and a reader must see that at a glance."""
        out, _ = _run(monkeypatch, _plan())
        assert out["writes"].startswith("none")

    def test_no_sql_it_issues_can_mutate(self):
        src = inspect.getsource(app_mod.api_cashout_census)
        upper = src.upper()
        for verb in ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "CREATE ",
                     "TRUNCATE ", "DROP "):
            assert verb not in upper, f"the census contains {verb.strip()}"

    def test_it_is_admin_gated(self):
        route = next(r for r in app_mod.app.routes
                     if getattr(r, "path", None) == "/api/admin/cashout-census")
        assert any(d.dependency is app_mod.require_admin
                   for d in route.dependencies)

    def test_every_read_is_bounded_and_timed(self):
        """Money-path rule: every new read carries a LIMIT and a timeout.

        The timeout lives in `_cc_fetch` (one place, so a new read
        cannot forget it) and the LIMIT must be in the SQL itself.
        """
        assert "asyncio.wait_for" in inspect.getsource(app_mod._cc_fetch)
        src = inspect.getsource(app_mod.api_cashout_census)
        sqls = [n.value for n in ast.walk(ast.parse(src.lstrip()))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and "SELECT" in n.value]
        assert sqls, "no SQL found — the parse is wrong, not the code"
        for sql in sqls:
            assert "LIMIT" in sql, f"unbounded read: {sql[:120]!r}"
        # and the venue read — the only one not going through _cc_fetch
        # because it is not SQL — is bounded too
        assert "asyncio.wait_for(\n                    asyncio.to_thread" in src \
            or "wait_for(" in src.split("_fetch_all_positions_sync")[1][:400]

    def test_a_pool_failure_is_a_payload_of_nulls_not_a_stack_trace(
            self, monkeypatch):
        """`_cc_fetch` makes every READ incapable of 500ing, but the pool
        acquisition and the COPY_WHALES import sat above that envelope
        and would raise straight out of the handler. A census that
        answers with an exception instead of named nulls has broken its
        own contract at the only moment the contract matters."""
        async def _boom():
            raise RuntimeError("pool is gone")

        monkeypatch.setattr(app_mod, "get_pool", _boom)
        out = asyncio.run(app_mod.api_cashout_census())
        assert out["copy_whales"] is None
        for name, keys in app_mod._CC_FIGURE_KEYS.items():
            fig = out[name]
            assert fig["coverage"]["complete"] is False
            assert "pool is gone" in fig["coverage"]["why"]
            for k in keys:
                assert fig[k] is None, f"{name}.{k} was not nulled"
        assert out["read_this_first"]["nulls"]

    def test_every_served_figure_declares_its_null_shape(self, monkeypatch):
        """`_CC_FIGURE_KEYS` is what the fail-closed path above nulls. A
        figure served but not declared there would come back as a stack
        trace's worth of missing keys on the one path that must not
        surprise anyone."""
        out, _ = _run(monkeypatch, _plan())
        for name, keys in app_mod._CC_FIGURE_KEYS.items():
            assert name in out, f"{name} declared but never served"
            for k in keys:
                assert k in out[name], f"{name}.{k} declared but not served"
        for name, fig in out.items():
            if isinstance(fig, dict) and "coverage" in fig:
                assert name in app_mod._CC_FIGURE_KEYS, \
                    f"{name} served but its null shape is not declared"


# ─────────────────── a null is never served as a zero ───────────────────

_FIGURES = tuple(app_mod._CC_FIGURE_KEYS)


class TestAFigureThatCannotBeComputedIsNullWithAReason:
    def test_every_figure_carries_its_own_coverage(self, monkeypatch):
        out, _ = _run(monkeypatch, _plan())
        for name in _FIGURES:
            assert name in out, name
            cov = out[name]["coverage"]
            assert set(cov) >= {"complete", "why"}
            if not cov["complete"]:
                assert cov["why"], f"{name} incomplete with no reason"

    def test_a_failed_read_nulls_its_figure_and_names_why(self, monkeypatch):
        out, _ = _run(monkeypatch,
                      _plan(**{M_SELLS: RuntimeError("db down")}))
        fig = out["desk_cashouts_on_copy_slugs"]
        assert fig["rows"] is None and fig["realized_usd"] is None
        assert "RuntimeError" in fig["coverage"]["why"]
        assert fig["coverage"]["complete"] is False
        # and the figure that DEPENDS on it is nulled too, not zeroed
        rest = out["restatement_if_attributed"]
        assert rest["copies_record_add_usd"] is None
        assert rest["add_in_window_usd"] is None
        assert "RuntimeError" in rest["coverage"]["why"]

    def test_zero_is_never_substituted_for_unread(self, monkeypatch):
        """The whole point. A read that failed must not be reported as
        'we looked and found nothing' — that is what authorises shipping
        a restatement of the wrong size."""
        out, _ = _run(monkeypatch, _plan(**{M_ZERO: RuntimeError("boom")}))
        z = out["published_at_zero"]
        assert all(z[k] is None for k in z if k != "coverage")
        assert 0 not in [z[k] for k in z if k != "coverage"]

    def test_an_unreadable_venue_nulls_the_ledger_figures(self, monkeypatch):
        out, _ = _run(monkeypatch,
                      _plan(**{M_LEDGER: [_led("a", claimed_shares=10.0)]}),
                      venue_raises=RuntimeError("no credentials"))
        for name in ("stranded_copy_rows", "unattributable_co_held"):
            fig = out[name]
            assert fig["slugs"] is None and fig["rows"] is None
            assert "no credentials" in fig["coverage"]["why"]


# ───────────── EVERY read refuses at its cap, not just some ─────────────

_CAP = app_mod.CASHOUT_CENSUS_ROW_CAP
_SLUG_CAP = app_mod.CASHOUT_CENSUS_SLUG_CAP


def _at_cap_plan(which):
    """A world where exactly one read is sitting on its cap."""
    if which == "sells":
        return _plan(**{M_SELLS: [_sell(i, f"s{i}", pnl=1.0)
                                  for i in range(_CAP)]})
    if which == "published_at_zero":
        return _plan(**{M_ZERO: [_zrow(rows=_CAP, copy_rows=3,
                                       copy_stake_usd=9.0,
                                       reaper_retired=1, desk_manual=1,
                                       first_day="2026-08-01",
                                       last_day="2026-09-01")]})
    if which == "rescore_delta_blind":
        return _plan(**{M_BLIND: [{"rows": _CAP, "pnl_usd": 12.0,
                                   "copy_rows": 2, "copy_pnl_usd": 5.0}]})
    if which == "kalshi_desk_cashouts":
        return _plan(**{M_KALSHI: [
            {"rows": _CAP - 1, "usd": 12345.0, "status": "placed"},
            {"rows": 1, "usd": 5.0, "status": "filled"}]})
    if which == "ledger":
        return _plan(**{M_LEDGER: [
            _led(f"g{i}", claimed_shares=10.0, copy_stake=1.0)
            for i in range(_SLUG_CAP)]})
    raise AssertionError(which)


_CAP_CASES = [
    ("sells", ("desk_cashouts_on_copy_slugs", "restatement_if_attributed")),
    ("published_at_zero", ("published_at_zero",)),
    ("rescore_delta_blind", ("rescore_delta_blind",)),
    ("kalshi_desk_cashouts", ("kalshi_desk_cashouts",)),
    ("ledger", ("stranded_copy_rows", "unattributable_co_held",
                "venue_silent_slugs")),
]


class TestEveryReadRefusesAtItsCap:
    """The payload states on its own face that "a read that hits its cap
    makes its figure NULL, not a floor". That sentence was true of four
    of the six reads: the Kalshi read had no cap check at all and served
    a truncated GROUP BY as a total, and three of the four that did have
    one were never exercised — mutating them to `if False:` left the
    suite green. A published bound the census contradicts is worse than
    no bound, because a floor read as a total is how a restatement gets
    sized wrong in the direction nobody can see."""

    @pytest.mark.parametrize("which,figures", _CAP_CASES,
                             ids=[c[0] for c in _CAP_CASES])
    def test_a_read_that_hit_its_cap_is_refused_not_served_as_a_floor(
            self, monkeypatch, which, figures):
        out, _ = _run(monkeypatch, _at_cap_plan(which),
                      positions={"g0": _pos(0)})
        for name in figures:
            fig = out[name]
            keys = [k for k in fig if k != "coverage"]
            assert all(fig[k] is None for k in keys), \
                f"{name} served a floor: { {k: fig[k] for k in keys} }"
            assert "cap" in fig["coverage"]["why"]
            assert fig["coverage"]["complete"] is False

    def test_the_kalshi_read_is_not_the_exception_to_the_published_bound(
            self, monkeypatch):
        """Named on its own because it was the one read with no cap
        check: at the cap it served `rows` and `usd` off a subquery the
        LIMIT had already truncated. manual_kalshi_queue is append-only
        and accumulates every desk ticket ever queued, so this is
        reachable, not theoretical."""
        out, _ = _run(monkeypatch, _at_cap_plan("kalshi_desk_cashouts"))
        k = out["kalshi_desk_cashouts"]
        assert k["rows"] is None and k["usd"] is None
        assert k["by_status"] is None
        assert "NULL, not a floor" in out["bounds"]["note"]

    def test_under_the_cap_the_kalshi_class_is_still_disclosed(
            self, monkeypatch):
        out, _ = _run(monkeypatch, _plan(**{
            M_KALSHI: [{"rows": 3, "usd": 120.0, "status": "filled"},
                       {"rows": 1, "usd": 40.0, "status": "pending"}]}))
        k = out["kalshi_desk_cashouts"]
        assert k["rows"] == 4 and k["usd"] == 160.0
        assert k["by_status"] == {"filled": {"rows": 3, "usd": 120.0},
                                  "pending": {"rows": 1, "usd": 40.0}}
        assert k["coverage"]["complete"] is False
        assert "never" in k["coverage"]["why"].lower()

    def test_the_dollars_are_carried_per_status_so_the_ceiling_nets_out(
            self, monkeypatch):
        """`usd` is written at QUEUE time — `round(qty * limit, 2)` —
        before the relay touches the venue, and manual_kalshi_queue is
        append-only. A sell the desk cancelled, one the stale-queue
        reaper flipped to `error` with "nothing was sent to the venue",
        and one the relay reported `unfilled` all keep their full
        notional in the headline sum forever.

        So `usd` is a CEILING presented as a total, on a payload that
        elsewhere enumerates its ceilings by name and points at this very
        figure as "the class with no remedy". The query already computed
        sum(usd) per status; `by_status` threw it away and served counts,
        which lets a reader see THAT the figure is inflated but not by
        how much. The Polymarket path takes dollars only from
        `cashed_out` and says so."""
        out, _ = _run(monkeypatch, _plan(**{
            M_KALSHI: [{"rows": 2, "usd": 300.0, "status": "filled"},
                       {"rows": 1, "usd": 250.0, "status": "cancelled"},
                       {"rows": 1, "usd": 40.0, "status": "error"},
                       {"rows": 1, "usd": 10.0, "status": "unfilled"}]}))
        k = out["kalshi_desk_cashouts"]
        assert k["usd"] == 600.0
        assert k["by_status"] == {
            "filled": {"rows": 2, "usd": 300.0},
            "cancelled": {"rows": 1, "usd": 250.0},
            "error": {"rows": 1, "usd": 40.0},
            "unfilled": {"rows": 1, "usd": 10.0}}
        # the reader can now do the subtraction the payload could not
        never_traded = sum(k["by_status"][s]["usd"]
                           for s in ("cancelled", "error", "unfilled"))
        assert never_traded == 300.0
        assert k["usd"] - never_traded == 300.0

    def test_that_ceiling_is_named_among_the_ceilings(self, monkeypatch):
        """read_this_first enumerates which figures are ceilings and did
        not list this one, while naming it by name two keys earlier as
        the class with no remedy."""
        out, _ = _run(monkeypatch, _plan())
        ceilings = out["read_this_first"]["which_numbers_are_ceilings"]
        assert "kalshi_desk_cashouts.usd" in ceilings
        assert "QUEUE time" in ceilings
        assert "by_status" in ceilings
        cov = out["kalshi_desk_cashouts"]["coverage"]
        assert "CEILING" in cov["usd_is_a_ceiling"]
        assert "append-only" in cov["usd_is_a_ceiling"]


# ──────────────── (1) the stranded rows, and what is NOT one ────────────

def _stranded_world():
    ledger = [
        # sold flat, one row of ours: the clean fingerprint
        _led("s-sold", holding_rows=1, claimed_shares=200.0,
             copy_rows=1, copy_stake=100.0),
        # still held in full: not stranded
        _led("s-held", holding_rows=1, claimed_shares=200.0,
             copy_rows=1, copy_stake=100.0),
        # RESOLVED, not sold
        _led("s-expired", holding_rows=1, claimed_shares=100.0,
             copy_rows=1, copy_stake=50.0),
        # the venue does not list it at all
        _led("s-absent", holding_rows=1, claimed_shares=100.0,
             copy_rows=1, copy_stake=60.0),
        # two of ours, PARTIAL sale -> refused
        _led("s-partial", holding_rows=2, claimed_shares=300.0,
             copy_rows=2, copy_stake=150.0),
        # two of ours, FULL flatten -> decidable, not refused
        _led("s-flat", holding_rows=2, claimed_shares=300.0,
             copy_rows=2, copy_stake=140.0),
        # not a copy row at all
        _led("s-nocopy", holding_rows=1, claimed_shares=500.0,
             copy_rows=0, copy_stake=0.0),
    ]
    positions = {
        "s-sold": _pos(0), "s-held": _pos(200),
        "s-expired": _pos(0, expired=True),
        "s-partial": _pos(100), "s-flat": _pos(0),
        "s-nocopy": _pos(0),
    }
    return ledger, positions


class TestTheStrandedCensus:
    def test_the_counts_and_the_phantom_exposure(self, monkeypatch):
        ledger, positions = _stranded_world()
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: ledger}), positions)
        st = out["stranded_copy_rows"]
        assert st["slugs"] == 3          # s-sold, s-partial, s-flat
        assert st["rows"] == 5           # 1 + 2 + 2
        assert st["shares_short"] == 700.0
        assert st["sole_row_slugs"] == 1
        assert st["co_held_slugs"] == 2

    def test_an_expired_position_is_a_resolution_not_a_sale(self, monkeypatch):
        ledger, positions = _stranded_world()
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: ledger}), positions)
        assert out["stranded_copy_rows"]["coverage"][
            "expired_slugs_excluded"] == 1
        # $50 of stake on that slug is in NO bucket: it is not stranded
        assert out["venue_silent_slugs"]["stake_usd"] == 60.0

    def test_absent_from_the_venue_is_its_own_bucket(self, monkeypatch):
        """The positions feed prunes settled markets, so a resolved
        market and a sold-flat one are indistinguishable in it. Folding
        those into `stranded` turns the settlement backlog into evidence
        of hand sales."""
        ledger, positions = _stranded_world()
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: ledger}), positions)
        unk = out["venue_silent_slugs"]
        assert unk["slugs"] == 1 and unk["rows"] == 1
        assert unk["coverage"]["complete"] is False
        assert "prunes" in unk["coverage"]["why"]

    def test_a_row_still_held_in_full_is_not_stranded(self, monkeypatch):
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("s-held", claimed_shares=200.0, copy_stake=100.0)]}),
            {"s-held": _pos(200)})
        assert out["stranded_copy_rows"]["slugs"] == 0
        assert out["stranded_copy_rows"]["stake_usd"] == 0.0

    def test_a_shortfall_under_one_share_is_rounding(self, monkeypatch):
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("s", claimed_shares=200.0, copy_stake=100.0)]}),
            {"s": _pos(199.5)})
        assert out["stranded_copy_rows"]["slugs"] == 0

    def test_a_slug_with_no_copy_row_is_not_our_problem(self, monkeypatch):
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("x", claimed_shares=500.0, copy_rows=0, copy_stake=0.0)]}),
            {"x": _pos(0)})
        assert out["stranded_copy_rows"]["slugs"] == 0
        assert out["venue_silent_slugs"]["slugs"] == 0

    def test_a_positions_feed_of_the_wrong_shape_is_unreadable_not_empty(
            self, monkeypatch):
        """An empty book calls every copy row sold. That is the one
        wrong answer this census can give, so a feed that did not arrive
        as a mapping is refused rather than read."""
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("s", claimed_shares=200.0, copy_stake=100.0)]}),
            positions=["not", "a", "mapping"])
        st = out["stranded_copy_rows"]
        assert st["slugs"] is None
        assert "not a mapping" in st["coverage"]["why"]

    def test_an_unparsable_entry_is_undecidable_not_a_flat_position(
            self, monkeypatch):
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("s", claimed_shares=200.0, copy_stake=100.0)]}),
            positions={"s": "garbage"})
        assert out["stranded_copy_rows"]["slugs"] == 0
        assert out["stranded_copy_rows"]["coverage"][
            "unparsable_venue_entries"] == 1
        assert out["venue_silent_slugs"]["slugs"] == 1

    def test_a_partly_unparsable_book_is_not_reported_as_complete(
            self, monkeypatch):
        """With every entry unparsable this would otherwise read
        "stranded: 0 slugs, complete" beside a silent bucket holding
        everything — a census asserting it saw a book it could not
        read."""
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("s", claimed_shares=200.0, copy_stake=100.0)]}),
            positions={"s": "garbage"})
        cov = out["stranded_copy_rows"]["coverage"]
        assert cov["complete"] is False
        assert "did not parse" in cov["why"]

    def test_a_clean_book_is_reported_complete(self, monkeypatch):
        ledger, positions = _stranded_world()
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: ledger}), positions)
        assert out["stranded_copy_rows"]["coverage"]["complete"] is True

    def test_a_case_collision_keeps_the_larger_magnitude(self, monkeypatch):
        """Folding case can only make the census more conservative about
        calling a row stranded, never less."""
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("s", claimed_shares=200.0, copy_stake=100.0)]}),
            positions={"S": _pos(0), "s": _pos(200)})
        assert out["stranded_copy_rows"]["slugs"] == 0
        assert out["stranded_copy_rows"]["coverage"]["case_collisions"] == 1

    def test_the_venue_magnitude_is_read_absolutely(self, monkeypatch):
        """A SHORT reads as a negative netPosition; abs() is what says
        whether anything is still held. A signed compare would call a
        fully-held short position stranded."""
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("s", claimed_shares=200.0, copy_stake=100.0,
                 short_rows=1)]}),
            {"s": _pos(-200)})
        assert out["stranded_copy_rows"]["slugs"] == 0

    def test_a_short_venue_book_is_disclosed(self, monkeypatch):
        """`_fetch_all_positions_sync` stops at 50 pages x 100. Past that
        the feed silently returns a partial book; the missing slugs fall
        to venue_silent_slugs, which is the conservative direction, but
        nothing said the book was short."""
        cap = app_mod.CASHOUT_CENSUS_VENUE_BOOK_CAP
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("s", claimed_shares=200.0, copy_stake=100.0)]}),
            positions={f"p{i}": _pos(5) for i in range(cap)})
        cov = out["stranded_copy_rows"]["coverage"]
        assert cov["venue_positions"] == cap
        assert cov["venue_book_may_be_short"] is True
        assert out["bounds"]["venue_book_cap"] == cap

    def test_a_truncated_venue_book_is_not_reported_COMPLETE(
            self, monkeypatch):
        """THE FLAG WAS A SIBLING FIELD, NOT A GATE. `complete` was `not
        unreadable_entries` alone, so a book sitting on its page-through
        cap served a coverage block that said "complete: true, why: null"
        beside "venue_book_may_be_short: true" — the payload arguing with
        itself, and the census contradicting its own published bound
        ("a read that hits its cap makes its figure NULL, not a floor —
        EVERY read"). The flag alone is not enough: a reader who trusts
        `complete` never gets to it.

        The old test asserted the flag and never the bit, which is how
        this survived a suite that had a test for it."""
        cap = app_mod.CASHOUT_CENSUS_VENUE_BOOK_CAP
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("s", claimed_shares=200.0, copy_stake=100.0)]}),
            positions={f"p{i}": _pos(5) for i in range(cap)})
        for name in ("stranded_copy_rows", "unattributable_co_held"):
            cov = out[name]["coverage"]
            assert cov["complete"] is False, \
                f"{name} called a possibly-truncated book complete"
            assert cov["why"], f"{name} is incomplete with no reason"
            assert "TRUNCATED" in cov["why"]
            assert str(cap) in cov["why"]

    def test_a_book_under_the_cap_is_still_complete(self, monkeypatch):
        """Folding the cap into the bit must not make every census
        incomplete — only one that could not tell whole from short."""
        cap = app_mod.CASHOUT_CENSUS_VENUE_BOOK_CAP
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("s", claimed_shares=200.0, copy_stake=100.0)]}),
            positions=dict({f"p{i}": _pos(5) for i in range(cap - 2)},
                           s=_pos(200)))
        cov = out["stranded_copy_rows"]["coverage"]
        assert cov["venue_book_may_be_short"] is False
        assert cov["complete"] is True and cov["why"] is None

    def test_an_exiting_only_copy_slug_is_disclosed_not_silently_dropped(
            self, monkeypatch):
        """A slug whose only copy row is `exiting` is our own sale in
        flight and reaches none of the three buckets. Defensible; it was
        undisclosed, and an omission nothing counts is indistinguishable
        from a bug."""
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("e", holding_rows=1, claimed_shares=100.0, copy_rows=0,
                 copy_claimed_shares=0.0, copy_exiting_rows=1,
                 copy_stake=0.0)]}), {"e": _pos(0)})
        assert out["stranded_copy_rows"]["slugs"] == 0
        assert out["venue_silent_slugs"]["slugs"] == 0
        assert out["stranded_copy_rows"]["coverage"][
            "exiting_only_copy_slugs_skipped"] == 1


class TestTheShortfallDoesNotConvictTheWholeStake:
    """A slug-level shortfall was charged to every copy row on the slug
    and to its entire stake. Nothing tested that the shortfall was large
    enough to account for those rows, or that it belonged to them at
    all. The reconciler this was modelled on had exactly that refinement
    — "shares other rows explain are not this row's; only the unexplained
    remainder decides" — and it was dropped. Every case below over-stated
    phantom exposure, in the direction that makes the problem look
    bigger than it is."""

    def test_a_manual_sleeve_sale_is_not_charged_to_the_copy_rows(
            self, monkeypatch):
        """One copy row (100 sh, $50) fully held, beside one `manual`
        sleeve row (100 sh) the owner hand-sold. The venue holds 100 —
        exactly the copy row's shares. Charging the sleeve's sale to the
        copy row reports $50 of phantom exposure that is not phantom."""
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("g", holding_rows=2, claimed_shares=200.0, copy_rows=1,
                 copy_claimed_shares=100.0, copy_stake=50.0)]}),
            {"g": _pos(100)})
        st = out["stranded_copy_rows"]
        assert st["slugs"] == 0 and st["rows"] == 0
        assert st["stake_usd"] == 0.0
        assert out["unattributable_co_held"]["stake_usd"] == 0.0

    def test_the_dollars_are_bounded_by_the_shortfall(self, monkeypatch):
        """Three copy rows of 100 shares / $50 each; the venue holds
        298.5. One and a half shares are missing out of three hundred —
        that is $0.75 of evidence, not $150."""
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("g", holding_rows=3, claimed_shares=300.0, copy_rows=3,
                 copy_stake=150.0)]}), {"g": _pos(298.5)})
        st = out["stranded_copy_rows"]
        assert st["shares_short"] == 1.5
        assert st["stake_usd"] == 0.75
        assert st["stake_usd_upper_bound"] == 150.0
        assert st["rows_fully_short"] == 0

    def test_a_sole_row_dust_shortfall_does_not_strand_its_whole_stake(
            self, monkeypatch):
        """A SOLE copy row, 100 sh / $50, venue holds 98.5. This is not
        hypothetical: `copy_exit_applied` partials taken before migration
        040 never reduced filled_shares, so those rows carry a permanent
        dust shortfall of exactly this shape."""
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("g", holding_rows=1, claimed_shares=100.0, copy_rows=1,
                 copy_stake=50.0)]}), {"g": _pos(98.5)})
        st = out["stranded_copy_rows"]
        assert st["slugs"] == 1 and st["sole_row_slugs"] == 1
        assert st["stake_usd"] == 0.75
        assert st["stake_usd_upper_bound"] == 50.0
        assert st["rows_fully_short"] == 0

    def test_a_fully_missing_claim_is_still_the_whole_stake(self,
                                                            monkeypatch):
        """Bounding the dollars must not soften the case the census
        exists to find: everything the copy rows claim is gone."""
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("g", holding_rows=1, claimed_shares=100.0, copy_rows=1,
                 copy_stake=50.0)]}), {"g": _pos(0)})
        st = out["stranded_copy_rows"]
        assert st["stake_usd"] == 50.0
        assert st["stake_usd_upper_bound"] == 50.0
        assert st["rows_fully_short"] == 1

    def test_an_exiting_row_mid_sale_does_not_strand_its_held_neighbour(
            self, monkeypatch):
        """One `exiting` copy row selling its 100 shares beside one
        `filled` copy row still holding its 100. The venue holds 100 —
        the filled row's. It is not stranded."""
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("g", holding_rows=2, claimed_shares=200.0, copy_rows=1,
                 copy_claimed_shares=100.0, copy_exiting_rows=1,
                 copy_stake=50.0)]}), {"g": _pos(100)})
        assert out["stranded_copy_rows"]["slugs"] == 0
        assert out["stranded_copy_rows"]["stake_usd"] == 0.0

    def test_a_slug_holding_both_sides_of_ours_is_refused_not_measured(
            self, monkeypatch):
        """A copy row long 100 and a mirror row short 100 on ONE slug.
        Both legs share one identifier and netPosition is SIGNED, so the
        venue reports 0 while 200 shares are held. abs() cannot separate
        them, so the slug is refused and counted — never read as a
        flatten."""
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("g", holding_rows=2, claimed_shares=200.0, copy_rows=1,
                 copy_claimed_shares=100.0, copy_stake=50.0,
                 short_rows=1)]}), {"g": _pos(0)})
        st = out["stranded_copy_rows"]
        assert st["mixed_side_slugs_refused"] == 1
        assert st["slugs"] == 0 and st["stake_usd"] == 0.0
        assert "REFUSED" in st["coverage"]["netting"]

    def test_the_intent_path_is_spliced_in_never_re_typed(self):
        """Detecting a mixed-side slug needs the order intent, which
        lives in `raw` JSON. That expression has ONE definition —
        `live_executor.ORDER_INTENT_SQL` — and a repo-wide test caps how
        many copies of the literal path may exist. Open-coding a fourth
        copy here broke that cap; the census splices the constant in
        instead, and this pins it so the breakage cannot come back
        through this file."""
        from sportsassets.live_executor import ORDER_INTENT_SQL

        src = inspect.getsource(app_mod.api_cashout_census)
        assert "raw #>> '{response" not in src, \
            "the census re-typed the intent path instead of splicing it"
        assert "ORDER_INTENT_SQL" in src, "the constant is not imported"
        sql = next(n.value for n in ast.walk(ast.parse(src.lstrip()))
                   if isinstance(n, ast.Constant)
                   and isinstance(n.value, str)
                   and "AS claimed_shares" in n.value)
        assert "__INTENT__" in sql
        # and the splice really does produce the one true expression
        assert "raw #>> '{response" in sql.replace("__INTENT__",
                                                   ORDER_INTENT_SQL)

    def test_a_slug_that_is_short_on_every_row_is_still_measured(
            self, monkeypatch):
        """The refusal above is for MIXED slugs only. A slug where every
        one of our rows is a short reads correctly through abs() and must
        not be swept into the refusal."""
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("g", holding_rows=2, claimed_shares=200.0, copy_rows=2,
                 copy_stake=100.0, short_rows=2)]}), {"g": _pos(0)})
        st = out["stranded_copy_rows"]
        assert st["mixed_side_slugs_refused"] == 0
        assert st["slugs"] == 1 and st["stake_usd"] == 100.0

    def test_the_phantom_dollars_are_split_sole_versus_co_held(
            self, monkeypatch):
        """The slug COUNT was split into sole/co-held while the dollars
        were served as one undivided total. The owner sizing U2's
        retirement cannot see how much sits on decidable sole-row slugs
        versus in the class the census itself says must be refused."""
        ledger, positions = _stranded_world()
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: ledger}), positions)
        st = out["stranded_copy_rows"]
        # s-sold: 200/200 short -> all $100. s-partial: 200/300 -> $100
        # of $150. s-flat: 300/300 -> all $140.
        assert st["sole_row_stake_usd"] == 100.0
        assert st["co_held_stake_usd"] == 240.0
        assert st["stake_usd"] == 340.0
        assert st["stake_usd_upper_bound"] == 390.0
        assert st["sole_row_stake_usd"] + st["co_held_stake_usd"] == \
            st["stake_usd"]

    def test_the_labels_say_which_figure_is_a_ceiling(self, monkeypatch):
        out, _ = _run(monkeypatch, _plan())
        cov = out["stranded_copy_rows"]["coverage"]
        assert "BOUNDED BY THE EVIDENCE" in cov["stake_usd_is"]
        assert "CEILING" in cov["stake_usd_upper_bound_is"]
        assert "ceiling" in cov["rows_is"]
        ceilings = out["read_this_first"]["which_numbers_are_ceilings"]
        assert "stake_usd_upper_bound" in ceilings
        assert "track_record_markets_returning_upper_bound" in ceilings

    def test_the_venue_is_not_read_when_no_copy_row_could_use_it(
            self, monkeypatch):
        """The venue read pages the whole book unthrottled on a
        rate-limited credential shared with the executor's `_pm_held`,
        which gates every mirror exit. With no `filled` copy row there is
        nothing for it to decide — and the answer is a measured zero, not
        an unread null."""
        calls = []
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("x", claimed_shares=500.0, copy_rows=0,
                 copy_claimed_shares=0.0)]}), venue_calls=calls)
        assert calls == []
        st = out["stranded_copy_rows"]
        assert st["slugs"] == 0 and st["coverage"]["complete"] is True
        assert "not read at all" in st["coverage"]["venue_read_skipped"]

    def test_the_venue_is_read_when_a_copy_row_needs_it(self, monkeypatch):
        calls = []
        _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("g", claimed_shares=100.0, copy_stake=50.0)]}),
            {"g": _pos(0)}, venue_calls=calls)
        assert calls == [1]


# ────────────── (5) the class that must be refused, not allocated ────────

class TestTheUnattributableClass:
    def test_a_partial_sale_across_two_of_our_rows_is_refused(self,
                                                              monkeypatch):
        ledger, positions = _stranded_world()
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: ledger}), positions)
        co = out["unattributable_co_held"]
        assert co["slugs"] == 1 and co["rows"] == 2
        assert co["stake_usd"] == 100.0
        assert "REFUSE" in co["coverage"]["remedy"]

    def test_a_full_flatten_across_two_rows_is_decidable(self, monkeypatch):
        """Everything on the slug closed, so nothing has to be allocated
        — it must NOT inflate the un-attributable class the owner is
        being asked to accept as permanently unfixable."""
        ledger, positions = _stranded_world()
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: ledger}), positions)
        assert "s-flat" not in str(out["unattributable_co_held"])
        assert out["unattributable_co_held"]["slugs"] == 1
        assert out["stranded_copy_rows"]["co_held_slugs"] == 2

    def test_the_refused_dollars_are_a_subset_never_an_addend(
            self, monkeypatch):
        """The same dollars appear in stranded_copy_rows.stake_usd as
        "phantom open exposure" and here as money that must be REFUSED.
        Both are true of the same stake, so the overlap has to be stated
        or a reader will add them."""
        ledger, positions = _stranded_world()
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: ledger}), positions)
        co = out["unattributable_co_held"]
        st = out["stranded_copy_rows"]
        assert co["stake_usd"] <= st["stake_usd"]
        assert "SUBSET" in co["coverage"]["stake_is"]
        assert "never be added" in co["coverage"]["stake_is"]

    def test_it_does_not_stamp_complete_on_a_book_it_could_not_read(
            self, monkeypatch):
        """`unattributable_co_held` passed a literal `True, None` to the
        coverage helper — on the very inputs for which its sibling,
        derived from the SAME loop over the SAME book, correctly reports
        incomplete.

        A venue entry that is not a dict never reaches the
        `holding_rows >= 2` branch at all: its slug is diverted to
        venue_silent_slugs first. So this block served "0 slugs, $0.00,
        complete, why: null" beside a stranded block saying the book did
        not parse — a zero-with-a-stamp on the figure read_this_first
        calls "the class with no remedy", which is the one figure that
        must never read as "we looked and there is nothing"."""
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: [
            _led("s", claimed_shares=200.0, copy_stake=100.0)]}),
            positions={"s": "garbage"})
        co = out["unattributable_co_held"]
        st = out["stranded_copy_rows"]
        assert co["stake_usd"] == 0.0        # the zero is real...
        assert co["coverage"]["complete"] is False   # ...and unstamped
        assert "did not parse" in co["coverage"]["why"]
        # ONE bit, from one book: the two must never disagree
        assert co["coverage"]["complete"] == st["coverage"]["complete"]
        assert co["coverage"]["why"] == st["coverage"]["why"]
        # and it carries the same diagnostics, so a reader who lands on
        # this figure first is not sent hunting for them
        for extra in ("unparsable_venue_entries", "venue_book_may_be_short",
                      "venue_positions"):
            assert extra in co["coverage"], extra
        assert co["coverage"]["unparsable_venue_entries"] == 1

    def test_a_clean_book_still_reports_the_co_held_class_complete(
            self, monkeypatch):
        ledger, positions = _stranded_world()
        out, _ = _run(monkeypatch, _plan(**{M_LEDGER: ledger}), positions)
        assert out["unattributable_co_held"]["coverage"]["complete"] is True


# ───────────────── (2)+(3) the desk cash-outs and the size ──────────────

def _route_a_world():
    sells = [
        _sell(1, "m1", pnl=40.0, day="2026-09-01"),
        _sell(2, "m2", pnl=None, day="2026-09-02"),
        _sell(3, "m3", status="unfilled", pnl=None, day=None),
        _sell(4, "m4", pnl=25.0, day="2026-09-03"),
        _sell(5, "m5", pnl=10.0, day="2026-09-04"),
        _sell(6, "m6", pnl=15.0, day="2026-08-30"),
    ]
    peers = [
        _peer("m1", 1, 0, copy_stake=100.0, copy_pnl=60.0),
        _peer("m2", 1, 0, copy_stake=80.0, copy_pnl=0.0),
        _peer("m3", 1, 0, copy_stake=50.0, copy_pnl=0.0),
        _peer("m4", 2, 0, copy_stake=200.0, copy_pnl=30.0),
        _peer("m5", 0, 0, copy_stake=0.0, copy_pnl=0.0),
        # attributable, but its entry row's line is dated BEFORE the
        # epoch the copy page is served from: it restates nothing.
        _peer("m6", 1, 1, copy_stake=90.0, copy_pnl=20.0,
              copy_settled_day=BEFORE_EPOCH),
    ]
    return sells, peers


class TestTheDeskCashoutPopulation:
    def test_the_population_and_its_dollars(self, monkeypatch):
        sells, peers = _route_a_world()
        out, _ = _run(monkeypatch,
                      _plan(**{M_SELLS: sells, M_PEERS: peers}))
        a = out["desk_cashouts_on_copy_slugs"]
        assert a["rows"] == 5              # m5 sits on no copy row
        assert a["rows_off_copy_slugs"] == 1
        assert a["markets"] == 5
        assert a["markets_with_realized_sale"] == 3   # m1, m4, m6
        assert a["realized_usd"] == 80.0   # 40 + 25 + 15
        assert a["pnl_null_rows"] == 1     # m2
        assert a["unfilled_rows"] == 1     # m3 never sold anything

    def test_a_ticket_that_never_filled_carries_no_dollars(self,
                                                            monkeypatch):
        out, _ = _run(monkeypatch, _plan(**{
            M_SELLS: [_sell(1, "m", status="unfilled", pnl=None)],
            M_PEERS: [_peer("m")]}))
        a = out["desk_cashouts_on_copy_slugs"]
        assert a["rows"] == 1 and a["unfilled_rows"] == 1
        assert a["realized_usd"] == 0.0

    def test_unfilled_rows_means_unfilled_not_merely_not_cashed_out(
            self, monkeypatch):
        """The name said `unfilled` — the desk's own no-fill status — and
        the count swept up rejected, error, open and cancelled with it."""
        out, _ = _run(monkeypatch, _plan(**{
            M_SELLS: [_sell(1, "a", status="unfilled"),
                      _sell(2, "b", status="rejected"),
                      _sell(3, "c", status="error"),
                      _sell(4, "d", status="cancelled")],
            M_PEERS: [_peer(s) for s in "abcd"]}))
        a = out["desk_cashouts_on_copy_slugs"]
        assert a["rows"] == 4
        assert a["unfilled_rows"] == 1
        assert a["other_status_rows"] == 3

    def test_the_markets_label_separates_touched_from_realized(
            self, monkeypatch):
        """`markets` counts the slug of a ticket that never filled as a
        market covered by a cash-out. Recoverable, but the label
        over-reaches, so the realized subset is served beside it."""
        out, _ = _run(monkeypatch, _plan(**{
            M_SELLS: [_sell(1, "a", pnl=10.0),
                      _sell(2, "b", status="unfilled")],
            M_PEERS: [_peer("a"), _peer("b")]}))
        a = out["desk_cashouts_on_copy_slugs"]
        assert a["markets"] == 2
        assert a["markets_with_realized_sale"] == 1
        assert "filled or not" in a["coverage"]["markets_is"]

    def test_the_upper_bound_caveat_is_on_the_payload(self, monkeypatch):
        out, _ = _run(monkeypatch, _plan())
        cov = out["desk_cashouts_on_copy_slugs"]["coverage"]
        assert "UPPER bound" in cov["exit_commission"]


class TestTheRestatementMagnitude:
    def test_only_a_sole_copy_row_slug_is_attributable(self, monkeypatch):
        sells, peers = _route_a_world()
        out, _ = _run(monkeypatch,
                      _plan(**{M_SELLS: sells, M_PEERS: peers}))
        r = out["restatement_if_attributed"]
        assert r["copies_record_add_usd"] == 55.0   # m1 40 + m6 15
        assert r["copies_record_add_rows"] == 2

    def test_a_co_held_slug_is_refused_and_its_dollars_disclosed(
            self, monkeypatch):
        sells, peers = _route_a_world()
        out, _ = _run(monkeypatch,
                      _plan(**{M_SELLS: sells, M_PEERS: peers}))
        r = out["restatement_if_attributed"]
        assert r["refused_co_held_usd"] == 25.0
        assert r["refused_co_held_rows"] == 1
        assert "never" in r["coverage"]["refused_because"]

    def test_a_pnl_null_row_is_never_attributed_and_is_counted(
            self, monkeypatch):
        """Fence 2. When the desk could not read an average cost the row
        is written with pnl NULL, so the settlement sweep's `sold_by`
        subtracts nothing and the copy row has ALREADY been assigned the
        market's whole cumulative realization — the sale included.
        Attributing it again books the money twice."""
        sells, peers = _route_a_world()
        out, _ = _run(monkeypatch,
                      _plan(**{M_SELLS: sells, M_PEERS: peers}))
        r = out["restatement_if_attributed"]
        assert r["unmeasurable_rows"] == 1
        assert r["copies_record_add_usd"] == 55.0   # m2's row adds nothing

    def test_the_figure_carries_its_date_range(self, monkeypatch):
        sells, peers = _route_a_world()
        out, _ = _run(monkeypatch,
                      _plan(**{M_SELLS: sells, M_PEERS: peers}))
        r = out["restatement_if_attributed"]
        assert r["first_day"] == "2026-08-30"
        assert r["last_day"] == "2026-09-01"

    def test_the_track_record_dollars_are_null_with_a_reason_not_zero(
            self, monkeypatch):
        """That record folds the venue activity tape; this census reads
        live_orders and the positions feed. Serving a zero there would
        read as 'the track record does not move', which is false."""
        sells, peers = _route_a_world()
        out, _ = _run(monkeypatch,
                      _plan(**{M_SELLS: sells, M_PEERS: peers}))
        r = out["restatement_if_attributed"]
        assert r["track_record_add_usd"] is None
        assert "venue activity tape" in r["coverage"]["why"]
        assert r["coverage"]["complete"] is False

    def test_the_markets_that_would_return_exclude_hand_OPENED_ones(
            self, monkeypatch):
        """The standing 2026-08-22 order is untouched: a position the
        owner ENTERED by hand stays in the manual sleeve. Only a market
        whose sole manual ticket is the SALE comes back."""
        sells, peers = _route_a_world()
        out, _ = _run(monkeypatch,
                      _plan(**{M_SELLS: sells, M_PEERS: peers}))
        r = out["restatement_if_attributed"]
        # m1, m2, m3, m4 — m6 carries a manual BUY and stays in the
        # sleeve, m5 has no copy row to come back to. m3's SELL never
        # filled and still counts: see the class below.
        assert r["track_record_markets_returning_upper_bound"] == 4
        proxy = r["track_record_copy_ledger_proxy"]
        assert proxy["rows"] == 5
        assert proxy["stake_usd"] == 430.0
        assert proxy["pnl_usd"] == 90.0
        assert "NOT the track" in proxy["what_this_is"]


class TestTheGateFigureIsNotTheMove:
    """`copies_record_add_usd` is the one number this unit exists to
    produce — "what those dollars would ADD to the copy scoreline". It
    was the LIFETIME, settle-agnostic sum over every attributable desk
    sale, while every copy surface it claims to move is windowed from
    COPIES_EPOCH and grades only entry rows that have SETTLED. Both gaps
    run the same way: the figure exceeds the actual move, which
    authorises a bigger restatement than the pages will show."""

    def test_the_lifetime_total_is_split_into_the_three_gates(
            self, monkeypatch):
        sells, peers = _route_a_world()
        out, _ = _run(monkeypatch,
                      _plan(**{M_SELLS: sells, M_PEERS: peers}))
        r = out["restatement_if_attributed"]
        assert r["copies_record_add_usd"] == 55.0
        # m1's entry line is inside the window; m6's is dated before it
        assert r["add_in_window_usd"] == 40.0
        assert r["add_in_window_rows"] == 1
        assert r["add_before_window_usd"] == 15.0
        assert r["add_before_window_rows"] == 1
        assert r["add_pending_resolution_usd"] == 0.0
        assert (r["add_in_window_usd"] + r["add_before_window_usd"]
                + r["add_pending_resolution_usd"]) == \
            r["copies_record_add_usd"]

    def test_a_sale_before_the_epoch_moves_no_served_number(self,
                                                            monkeypatch):
        """`copies_record.build` drops every settled line dated before
        `since_day`, and /api/copies-record defaults that to
        COPIES_EPOCH. Under U3 the dollars land on the ENTRY row's line,
        so it is the entry's day that decides — and a line the page
        never renders cannot restate anything."""
        out, _ = _run(monkeypatch, _plan(**{
            M_SELLS: [_sell(1, "old", pnl=40.0, day="2026-09-03")],
            M_PEERS: [_peer("old", copy_settled_day=BEFORE_EPOCH)]}))
        r = out["restatement_if_attributed"]
        assert r["copies_record_add_usd"] == 40.0
        assert r["add_in_window_usd"] == 0.0
        assert r["add_before_window_usd"] == 40.0

    def test_a_sale_whose_entry_row_has_no_settled_line_moves_nothing_yet(
            self, monkeypatch):
        """The desk cash-out never closes the copy row and the settlement
        sweep grades a `filled` row only once the market RESOLVES, so an
        entry row still `filled` has NO LINE for U3 to fold onto. This is
        the NORMAL state of a recent hand sale: real money, booked at
        resolution, restating nothing anyone has read."""
        out, _ = _run(monkeypatch, _plan(**{
            M_SELLS: [_sell(1, "u", pnl=40.0, day="2026-09-03")],
            M_PEERS: [_peer("u", copy_entry_rows=1, copy_settled_rows=0)]}))
        r = out["restatement_if_attributed"]
        assert r["copies_record_add_usd"] == 40.0
        assert r["copies_record_add_rows"] == 1
        assert r["add_pending_resolution_usd"] == 40.0
        assert r["add_pending_resolution_rows"] == 1
        assert r["add_in_window_usd"] == 0.0
        assert r["add_before_window_usd"] == 0.0

    def test_the_epoch_is_named_on_the_payload(self, monkeypatch):
        sells, peers = _route_a_world()
        out, _ = _run(monkeypatch,
                      _plan(**{M_SELLS: sells, M_PEERS: peers}))
        r = out["restatement_if_attributed"]
        assert r["copies_epoch"] == app_mod.COPIES_EPOCH
        assert "add_in_window_usd" in out["read_this_first"][
            "which_number_is_the_move"]

    def test_the_entry_day_range_sits_beside_the_sale_day_range(
            self, monkeypatch):
        """`first_day`/`last_day` are the SALE days. The dollars land on
        the ENTRY row's line, on the entry's day — which is also the axis
        the epoch is applied against, so both ranges have to be served or
        the reader compares the wrong one to the window."""
        sells, peers = _route_a_world()
        out, _ = _run(monkeypatch,
                      _plan(**{M_SELLS: sells, M_PEERS: peers}))
        r = out["restatement_if_attributed"]
        assert (r["first_day"], r["last_day"]) == ("2026-08-30", "2026-09-01")
        assert (r["entry_first_day"], r["entry_last_day"]) == \
            (BEFORE_EPOCH, IN_WINDOW)
        assert "SALE days" in r["coverage"]["day_axes"]

    def test_an_unreadable_epoch_nulls_the_split_rather_than_assuming_one(
            self, monkeypatch):
        """This endpoint's own rule: a figure that cannot be computed is
        null with a named reason, never zero. An undecidable window is
        not a window containing everything."""
        monkeypatch.delattr(app_mod, "COPIES_EPOCH", raising=False)
        sells, peers = _route_a_world()
        out, _ = _run(monkeypatch,
                      _plan(**{M_SELLS: sells, M_PEERS: peers}))
        r = out["restatement_if_attributed"]
        assert r["copies_epoch"] is None
        assert r["add_in_window_usd"] is None
        assert r["add_before_window_usd"] is None
        # the lifetime total still stands, and the pending split does
        # too — neither of those needs the epoch to decide
        assert r["copies_record_add_usd"] == 55.0
        assert r["add_pending_resolution_usd"] == 0.0
        assert "COPIES_EPOCH" in r["coverage"]["epoch_why"]

    def test_the_coverage_says_which_figure_is_the_move(self, monkeypatch):
        out, _ = _run(monkeypatch, _plan())
        cov = out["restatement_if_attributed"]["coverage"]
        assert "CEILING" in cov["copies_record_add_is"]
        assert "add_in_window_usd" in cov["the_move_is"]
        assert "RESOLUTION" in cov["add_pending_resolution_is"]


class TestADeskSaleOfTheDesksOwnSharesIsNotACopySale:
    """THE ERROR RAN UP, ON THE FIGURE THAT AUTHORISES THE RESTATEMENT.

    `attributable` refused a sale only when a SECOND COPY-LANE row shared
    the slug. Every NON-copy holding row — the manual sleeve, underdog —
    was invisible to it. So a desk sale of a position the desk itself
    hand-OPENED was booked, in full, onto the copy scoreline: the same
    payload said `add_in_window_usd = $500` and, three keys away, that
    the market does not come back to the track record BECAUSE the desk
    hand-opened it. Nothing disclosed the gap — `refused_because` named
    only the two-copy-rows case and `direction` asserted "UP — money the
    copy scoreline currently books nowhere".

    The census already applies exactly this discipline on the ledger path
    ("shares other rows explain are not these rows'"). This is that rule,
    on the restatement path."""

    def test_a_sale_on_a_slug_the_manual_sleeve_holds_is_not_attributed(
            self, monkeypatch):
        out, _ = _run(monkeypatch, _plan(**{
            M_SELLS: [_sell(1, "s", pnl=500.0)],
            M_PEERS: [_peer("s", copy_entry_rows=1, manual_buy_rows=1,
                            non_copy_holding_rows=1, copy_stake=100.0)]}))
        r = out["restatement_if_attributed"]
        assert r["add_in_window_usd"] == 0.0
        assert r["copies_record_add_usd"] == 0.0
        assert r["copies_record_add_rows"] == 0
        # refused and DISCLOSED, never silently dropped
        assert r["refused_sleeve_held_usd"] == 500.0
        assert r["refused_sleeve_held_rows"] == 1
        # the dollars are still in the population that was measured
        assert out["desk_cashouts_on_copy_slugs"]["realized_usd"] == 500.0

    def test_the_payload_no_longer_contradicts_itself_on_that_slug(
            self, monkeypatch):
        """The specific incoherence: the census asserted the market stays
        in the manual sleeve because the desk hand-opened it, while
        crediting that same sale's dollars to the copy row."""
        out, _ = _run(monkeypatch, _plan(**{
            M_SELLS: [_sell(1, "s", pnl=500.0)],
            M_PEERS: [_peer("s", copy_entry_rows=1, manual_buy_rows=1,
                            non_copy_holding_rows=1, copy_stake=100.0)]}))
        r = out["restatement_if_attributed"]
        assert r["track_record_markets_returning_upper_bound"] == 0
        assert r["copies_record_add_usd"] == 0.0

    def test_an_underdog_holding_refuses_too_not_just_manual(
            self, monkeypatch):
        """The gate is NON-COPY, not manual-only: underdog is a sleeve of
        ours that holds shares the venue nets into the same slug, and
        `manual_buy_rows` cannot see it at all."""
        out, _ = _run(monkeypatch, _plan(**{
            M_SELLS: [_sell(1, "s", pnl=90.0)],
            M_PEERS: [_peer("s", copy_entry_rows=1, manual_buy_rows=0,
                            non_copy_holding_rows=1)]}))
        r = out["restatement_if_attributed"]
        assert r["copies_record_add_usd"] == 0.0
        assert r["refused_sleeve_held_rows"] == 1
        # ...and it does NOT become a co-held refusal: different remedy
        assert r["refused_co_held_usd"] == 0.0

    def test_a_clean_sole_copy_slug_is_still_attributed(self, monkeypatch):
        """The gate must not swallow the census. With no non-copy holder
        the sale is the copy row's and the dollars stand."""
        out, _ = _run(monkeypatch, _plan(**{
            M_SELLS: [_sell(1, "s", pnl=500.0)],
            M_PEERS: [_peer("s", copy_entry_rows=1)]}))
        r = out["restatement_if_attributed"]
        assert r["copies_record_add_usd"] == 500.0
        assert r["add_in_window_usd"] == 500.0
        assert r["refused_sleeve_held_usd"] == 0.0

    def test_the_two_refusal_classes_partition_the_measured_dollars(
            self, monkeypatch):
        """Attributed + co-held-refused + sleeve-refused = every measured
        dollar. A class that is neither added nor counted is a dollar
        that vanished off the payload."""
        out, _ = _run(monkeypatch, _plan(**{
            M_SELLS: [_sell(1, "clean", pnl=100.0),
                      _sell(2, "coheld", pnl=200.0),
                      _sell(3, "sleeve", pnl=400.0)],
            M_PEERS: [_peer("clean"),
                      _peer("coheld", copy_entry_rows=2),
                      _peer("sleeve", non_copy_holding_rows=2)]}))
        r = out["restatement_if_attributed"]
        assert r["copies_record_add_usd"] == 100.0
        assert r["refused_co_held_usd"] == 200.0
        assert r["refused_sleeve_held_usd"] == 400.0
        assert (r["copies_record_add_usd"] + r["refused_co_held_usd"]
                + r["refused_sleeve_held_usd"]) == \
            out["desk_cashouts_on_copy_slugs"]["realized_usd"]

    def test_the_query_counts_entry_legs_and_never_the_sale_ticket(self):
        """The desk cash-out ticket being attributed is ITSELF a `manual`
        row in `cashed_out` — a holding status. A counter that took every
        non-copy row in a holding status would count the sale ticket as
        the sleeve's holding and refuse the entire payload, so the
        counter has to name ENTRY legs: intent BUY_LONG/BUY_SHORT, or
        `side <> 'SELL'` where the venue named no intent.

        THE PATTERN IS EVALUATED, NOT SPELLED. The first version of this
        test asserted the SQL contained the literal string
        ``LIKE 'BUY%'`` — and that predicate matches NOTHING this venue
        stores, because the intent it writes is ``ORDER_INTENT_BUY_LONG``
        (pmus.py:498, :1637), not ``BUY_LONG``. The counter was therefore
        always 0, the refusal never fired, and the test that was supposed
        to guard it pinned the defect in place. So this reads the LIKE
        pattern out of the SQL, applies it the way SQL would, and asserts
        the result against the four intent literals that actually exist —
        which fails for ``BUY%`` and for anything else that cannot tell a
        buy from a sell.
        """
        import re as _re

        # Every intent literal the venue is known to write. Two must
        # match the entry-leg pattern and two must not.
        BUYS = ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT")
        SELLS = ("ORDER_INTENT_SELL_LONG", "ORDER_INTENT_SELL_SHORT")

        def _like(pattern: str, value: str) -> bool:
            """SQL LIKE, restricted to the % wildcard (no _ is used here)."""
            rx = "^" + ".*".join(_re.escape(p) for p in pattern.split("%")) + "$"
            return _re.match(rx, value) is not None

        src = inspect.getsource(app_mod.api_cashout_census)
        block = src.split("AS non_copy_holding_rows")[0]
        term = block[block.rindex("count(*) FILTER"):]

        m = _re.search(r"LIKE\s+'([^']*)'", term)
        assert m, "the entry-leg test is not a LIKE on the intent"
        pattern = m.group(1)
        # The SQL upper()s the column, so compare in upper case.
        assert all(_like(pattern.upper(), b) for b in BUYS), (
            f"LIKE '{pattern}' does not match {BUYS!r} — the intents this "
            "venue actually stores. A prefix pattern silently matches "
            "nothing and the refusal it gates never fires.")
        assert not any(_like(pattern.upper(), s_) for s_ in SELLS), (
            f"LIKE '{pattern}' also matches a SELL intent {SELLS!r}; the "
            "desk cash-out ticket being attributed is itself a SELL and "
            "would refuse every dollar on the payload")
        assert "upper(" in term, \
            "the intent must be case-folded before the pattern is applied"
        assert "<> 'SELL'" in term, "no fallback for a row with no intent"
        assert "__INTENT__" in term, \
            "the intent path must be SPLICED, never re-typed"
        assert "NOT (lower(COALESCE(whale_username, ''))" in term, \
            "the term is not the NON-copy population"

    def test_the_refusal_and_the_direction_both_say_it(self, monkeypatch):
        """A refusal nothing on the payload explains is indistinguishable
        from a missing figure, and `direction` claimed the only bias was
        the disclosed one."""
        out, _ = _run(monkeypatch, _plan())
        cov = out["restatement_if_attributed"]["coverage"]
        assert "refused_sleeve_held" in cov["refused_because"]
        assert "hand-sale" in cov["refused_because"]
        assert "sleeve_held_residual" in cov["direction"]
        # and the one shape that can still leak UP is named, not implied
        assert "SHORT ENTRY" in cov["sleeve_held_residual"]
        assert "NO non-copy row holding" in cov["copies_record_add_is"]


class TestTheReturningMarketCountIsAnUpperBound:
    """It is the only track-record NUMBER on the payload — the dollars
    are correctly null with a reason — and it was served as a count."""

    def test_a_manual_buy_that_never_filled_still_holds_its_market(
            self, monkeypatch):
        """track_record builds manual_slugs with NO status filter and no
        side filter; U4 adds only `AND side <> 'SELL'`. So an `unfilled`
        manual BUY — the desk's own no-fill status — keeps its slug in
        the manual sleeve after U4, and a census that cannot see it
        counts the market as returning when it will not."""
        out, _ = _run(monkeypatch, _plan(**{
            M_SELLS: [_sell(1, "h", pnl=40.0)],
            M_PEERS: [_peer("h", copy_entry_rows=1, manual_buy_rows=1)]}))
        r = out["restatement_if_attributed"]
        assert r["track_record_markets_returning_upper_bound"] == 0

    def test_the_manual_buy_test_matches_the_query_it_models(self):
        """The peers read must not restrict manual BUY rows to holding
        statuses: that is what made a never-filled ticket invisible."""
        src = inspect.getsource(app_mod.api_cashout_census)
        block = src.split("AS manual_buy_rows")[0]
        buy = block[block.rindex("count(*) FILTER"):]
        assert "<> 'SELL'" in buy
        assert "status = ANY" not in buy, \
            "manual_buy_rows is still status-filtered; track_record's " \
            "manual_slugs query has no status filter at all"

    def test_a_manual_sell_that_never_filled_still_takes_its_market_back(
            self, monkeypatch):
        """THE MIRROR OF THE BUY-SIDE MISMATCH ALREADY FIXED ABOVE, left
        on the SELL side. `manual_slugs` has NO status filter and U4 adds
        only `AND side <> 'SELL'`, so a slug whose only manual row is an
        `unfilled` SELL is in the sleeve today and leaves it under U4 —
        that market returns. The count was built from `cashed_out` sales
        alone, so it dropped exactly those slugs: a FLOOR, under a key
        named `..._upper_bound` whose own coverage says "It is a
        CEILING". The census's own rule is that a floor served as a total
        is how a restatement gets under-sized.

        It bites for `other_status_rows` too — open, error, rejected —
        not only `unfilled`."""
        for status in ("unfilled", "rejected", "error", "open"):
            out, _ = _run(monkeypatch, _plan(**{
                M_SELLS: [_sell(1, "s", status=status)],
                M_PEERS: [_peer("s", copy_entry_rows=1, manual_buy_rows=0,
                                copy_stake=100.0, copy_pnl=25.0)]}))
            r = out["restatement_if_attributed"]
            assert out["desk_cashouts_on_copy_slugs"]["rows"] == 1
            assert r["track_record_markets_returning_upper_bound"] == 1, \
                f"a {status} manual SELL was dropped from the ceiling"
            proxy = r["track_record_copy_ledger_proxy"]
            assert proxy["rows"] == 1 and proxy["stake_usd"] == 100.0
            assert proxy["pnl_usd"] == 25.0
            # and it adds no DOLLARS: it never sold anything
            assert r["copies_record_add_usd"] == 0.0

    def test_the_count_still_excludes_a_slug_with_no_copy_row(
            self, monkeypatch):
        """Widening to every status must not widen to slugs that have
        nothing to come back TO — a manual SELL on a slug the copy lane
        never touched was never in this count."""
        out, _ = _run(monkeypatch, _plan(**{
            M_SELLS: [_sell(1, "s", status="unfilled")],
            M_PEERS: [_peer("s", copy_entry_rows=0)]}))
        r = out["restatement_if_attributed"]
        assert r["track_record_markets_returning_upper_bound"] == 0

    def test_the_coverage_says_the_count_has_no_status_filter(
            self, monkeypatch):
        out, _ = _run(monkeypatch, _plan())
        why = out["restatement_if_attributed"]["coverage"][
            "markets_returning_is_an_upper_bound"]
        assert "IN ANY STATUS" in why
        assert "no status filter at all" in why

    def test_the_count_names_the_gates_it_cannot_see(self, monkeypatch):
        """A market leaving `manual` can still be held out of the record
        by the `attributed` set, PNL_DISPLAY_CAP, max_stake or build()'s
        own window. None of those is visible from live_orders."""
        out, _ = _run(monkeypatch, _plan())
        why = out["restatement_if_attributed"]["coverage"][
            "markets_returning_is_an_upper_bound"]
        assert "CEILING" in why
        assert "attributed" in why
        assert "PNL_DISPLAY_CAP" in why
        assert "unattributed" in why


# ─────────────── (4) the sales already published at $0.00 ───────────────

class TestThePublishedZeroPopulation:
    def test_it_is_counted_with_its_stake_and_its_days(self, monkeypatch):
        out, _ = _run(monkeypatch, _plan(**{M_ZERO: [_zrow(
            rows=7, copy_rows=5, copy_stake_usd=1234.5,
            reaper_retired=4, desk_manual=2,
            first_day="2026-08-20", last_day="2026-09-03")]}))
        z = out["published_at_zero"]
        assert z["rows"] == 7 and z["copy_rows"] == 5
        assert z["copy_stake_usd"] == 1234.5
        assert z["reaper_retired"] == 4 and z["desk_manual"] == 2
        assert (z["first_day"], z["last_day"]) == ("2026-08-20", "2026-09-03")

    def test_it_is_named_a_different_remedy_from_the_labelling_problem(
            self, monkeypatch):
        out, _ = _run(monkeypatch, _plan())
        why = out["published_at_zero"]["coverage"]["remedy"]
        assert "never measured" in why
        assert "different" in why.lower()
        assert "different remedies" in out["read_this_first"][
            "already_wrong_vs_merely_unlabelled"].lower()

    def test_the_pair_u5_would_remove_is_windowed_not_lifetime(
            self, monkeypatch):
        """THE LABEL NAMED A CONSUMER THAT NEVER SEES MOST OF THESE ROWS.
        `copy_rows` / `copy_stake_usd` carried no `day` predicate, while
        the coverage asserted they are "in the ROI denominator with a
        win-rate slot each — THIS is the pair U5 would remove".

        `copies_record.build` drops every settled line dated before
        COPIES_EPOCH before `scorecard` accrues anything, so a pre-epoch
        row is in no denominator and holds no win-rate slot: removing it
        moves nothing. With the epoch days old, most of the population is
        pre-epoch, and the figure sizing the removal was the wrong one.

        The census already applies this exact epoch split correctly on
        the restatement path."""
        out, _ = _run(monkeypatch, _plan(**{M_ZERO: [_zrow(
            rows=20, copy_rows=12, copy_stake_usd=2400.0,
            copy_rows_in_window=3, copy_stake_usd_in_window=600.0,
            first_day="2026-07-01", last_day=IN_WINDOW)]}))
        z = out["published_at_zero"]
        assert z["copy_rows"] == 12 and z["copy_stake_usd"] == 2400.0
        assert z["copy_rows_in_window"] == 3
        assert z["copy_stake_usd_in_window"] == 600.0
        assert z["copies_epoch"] == app_mod.COPIES_EPOCH
        cov = z["coverage"]
        assert "U5 WOULD REMOVE" in cov["copy_rows_in_window_is"]
        assert "LIFETIME" in cov["copy_rows_is"]
        assert "NOT the pair U5 would remove" in cov["copy_rows_is"]

    def test_the_window_is_applied_in_the_query_on_the_settled_day(self):
        """It has to be a predicate, not a caveat: the aggregate is one
        row and nothing downstream can re-split it. `day` is the ET
        settled day, which is the axis `build` compares against."""
        src = inspect.getsource(app_mod.api_cashout_census)
        block = src.split("AS copy_rows_in_window")[0]
        term = block[block.rindex("count(*) FILTER"):]
        assert "day >= $3" in term
        zero_sql = next(n.value for n in ast.walk(ast.parse(src.lstrip()))
                        if isinstance(n, ast.Constant)
                        and isinstance(n.value, str)
                        and "AS reaper_retired" in n.value)
        assert "AS copy_stake_usd_in_window" in zero_sql

    def test_an_unreadable_epoch_nulls_the_windowed_pair_not_zeroes_it(
            self, monkeypatch):
        """Same rule as the restatement split: an undecidable window is
        not a window containing nothing. The lifetime pair still stands —
        it needs no epoch."""
        monkeypatch.delattr(app_mod, "COPIES_EPOCH", raising=False)
        out, _ = _run(monkeypatch, _plan(**{M_ZERO: [_zrow(
            rows=20, copy_rows=12, copy_stake_usd=2400.0,
            copy_rows_in_window=3, copy_stake_usd_in_window=600.0)]}))
        z = out["published_at_zero"]
        assert z["copy_rows_in_window"] is None
        assert z["copy_stake_usd_in_window"] is None
        assert z["copies_epoch"] is None
        assert z["copy_rows"] == 12 and z["copy_stake_usd"] == 2400.0
        assert "COPIES_EPOCH" in z["coverage"]["epoch_why"]

    def test_the_query_only_counts_rows_the_scoreline_actually_serves(self):
        """`_SETTLED_SQL` takes `status IN ('settled','cashed_out') AND
        settled_at IS NOT NULL`. A cashed-out row with no settled_at is
        not on the page, so counting it here would over-size the
        population being removed from the ROI denominator."""
        src = inspect.getsource(app_mod.api_cashout_census)
        block = src.split("AS reaper_retired")[1]
        assert "status = 'cashed_out'" in block
        assert "pnl IS NULL" in block
        assert "settled_at IS NOT NULL" in block

    def test_the_stake_is_summed_as_the_roi_denominator_reads_it(self):
        """`scorecard` reads the denominator as bare
        `float(r.get('filled_usd') or 0)`. The
        COALESCE(NULLIF(filled_usd,0), requested_usd) pattern is correct
        for `open.stake`, which is where it was borrowed from, and wrong
        here: a row with filled_usd = 0 would contribute requested_usd to
        this census and nothing at all to the denominator it names."""
        src = inspect.getsource(app_mod.api_cashout_census)
        block = src.split("AS reaper_retired")[1]
        stake_line = next(ln for ln in block.splitlines() if "AS stake" in ln)
        assert "COALESCE(filled_usd, 0)" in stake_line
        assert "requested_usd" not in stake_line

    def test_the_headline_row_count_is_not_claimed_to_be_the_scorelines(
            self, monkeypatch):
        """`rows` counts every cashed_out/pnl-NULL row, `manual` and
        `underdog` included — and those are filtered out of the copy
        scoreline. The sentence about the ROI denominator belongs on
        copy_rows, or the probe line's headline is read as a population
        it is not."""
        out, _ = _run(monkeypatch, _plan(**{M_ZERO: [_zrow(
            rows=9, copy_rows=2, copy_stake_usd=100.0,
            reaper_retired=1, desk_manual=6)]}))
        cov = out["published_at_zero"]["coverage"]
        assert "INCLUDING" in cov["what_this_is"]
        assert "ROI" not in cov["what_this_is"]
        assert "ROI" not in cov["copy_rows_is"]
        assert "ROI" in cov["copy_rows_in_window_is"]
        assert "filled_usd" in cov["copy_stake_usd_is"]


# ───────────── the dormant hazard the plan names as a prerequisite ──────

class TestTheRescoreDeltaBlindSpot:
    def test_it_measures_the_population_the_delta_counter_misreports(
            self, monkeypatch):
        out, _ = _run(monkeypatch, _plan(**{M_BLIND: [{
            "rows": 12, "pnl_usd": 340.5, "copy_rows": 9,
            "copy_pnl_usd": 300.0}]}))
        b = out["rescore_delta_blind"]
        assert b["rows"] == 12 and b["pnl_usd"] == 340.5
        assert b["copy_rows"] == 9 and b["copy_pnl_usd"] == 300.0

    def test_it_does_not_ask_for_a_fix_that_has_already_shipped(
            self, monkeypatch):
        """THE PAYLOAD IS THE REVIEW GATE, so a sentence on it that has
        gone stale schedules work that is already done.

        This block used to say, in the present tense, that the
        restatement's delta counter "reads their old value as 0.0" and
        that the one-line fix "is left for the unit that owns that
        file". Both were true when written. Neither is true at d832e7e:
        `analytics/engine.py` reads `oldp = float(r["pnl"] or 0)`
        unconditionally and its summary carries an `overwrote_filled`
        counter for exactly this population. An owner reading the census
        would schedule a shipped fix.

        The COUNT stays — it still measures a real population, and it is
        the size of what that commit touches — so this pins the prose,
        not the number."""
        out, _ = _run(monkeypatch, _plan(**{M_BLIND: [{
            "rows": 1, "pnl_usd": 1.0, "copy_rows": 1,
            "copy_pnl_usd": 1.0}]}))
        cov = out["rescore_delta_blind"]["coverage"]
        assert "owned_by" not in cov, \
            "the payload still assigns this fix to a future unit"
        shipped = cov["already_fixed"]
        assert "d832e7e" in shipped, "the commit that closed it is not named"
        assert "overwrote_filled" in shipped
        assert "DO NOT SCHEDULE" in shipped
        # and the defect is described in the PAST tense, so no reader
        # takes the description for a live one
        assert "USED TO" in cov["what_this_is"]
        assert "reads their old value as 0.0" not in cov["what_this_is"]
        # the figure itself is untouched: it is the SIZE, not the ask
        assert out["rescore_delta_blind"]["rows"] == 1
        assert out["rescore_delta_blind"]["pnl_usd"] == 1.0

    def test_the_source_comment_does_not_claim_engine_py_is_untouched(self):
        """The comment above this read said "`analytics/engine.py` IS NOT
        THIS UNIT'S FILE and is not touched" and quoted the pre-change
        line as current. The same change set that added this census
        changed that exact line, so the comment was flatly false in its
        own tree — and a false "is not touched" is the kind of sentence a
        later reader trusts instead of checking."""
        src = inspect.getsource(app_mod.api_cashout_census)
        blind = src.split("blind_keys =")[0]
        blind = blind[blind.rindex("DELTA COUNTER"):]
        assert "is not touched" not in blind
        assert 'if r["status"] == "settled"' in blind, \
            "the old expression should still be quoted — as history"
        assert "USED TO" in blind
        assert "d832e7e" in blind

    def test_the_census_reaches_outside_its_files_for_nothing(self):
        """The measurement is the whole of this unit's answer to the
        hazard. Whether `analytics/engine.py` still carries the defect,
        or the unit that owns it has already fixed the line, the census
        counts the same population and this file imports nothing from
        there to decide it — so the two units cannot collide."""
        src = inspect.getsource(app_mod.api_cashout_census)
        assert "analytics.engine" not in src
        assert "from ..analytics" not in src


# ───────────────────────── the payload contract ─────────────────────────

class TestThePayloadSaysHowToReadIt:
    def test_it_publishes_its_own_bounds(self, monkeypatch):
        out, _ = _run(monkeypatch, _plan())
        b = out["bounds"]
        assert b["row_cap"] == app_mod.CASHOUT_CENSUS_ROW_CAP
        assert b["slug_cap"] == app_mod.CASHOUT_CENSUS_SLUG_CAP
        assert b["read_timeout_s"] == app_mod.CASHOUT_CENSUS_READ_TIMEOUT_S
        assert b["request_budget_s"] == app_mod.CASHOUT_CENSUS_BUDGET_S

    def test_it_warns_that_a_null_is_not_a_zero(self, monkeypatch):
        out, _ = _run(monkeypatch, _plan())
        assert "never be summed" in out["read_this_first"]["nulls"]

    def test_it_states_that_it_is_the_gate(self, monkeypatch):
        out, _ = _run(monkeypatch, _plan())
        assert "before" in out["read_this_first"]["gate"]

    def test_the_two_definitions_of_co_held_are_disclosed(self, monkeypatch):
        """The restatement path calls a slug co-held on ANY holding
        status — `merged` included, which is an add leg already booked
        onto its parent row rather than a second position. The ledger
        path counts only filled/exiting. Both are defensible; carrying
        two definitions on one payload without saying so is not."""
        out, _ = _run(monkeypatch, _plan())
        note = out["read_this_first"]["two_definitions_of_co_held"]
        assert "merged" in note
        assert "filled/exiting" in note
        assert "OVER-states" in note

    def test_the_budget_is_one_deadline_for_the_whole_request(self):
        b = app_mod._CashoutBudget(total=0.0)
        assert b.grant(8.0) is None
        b2 = app_mod._CashoutBudget(total=100.0)
        assert b2.grant(8.0) == pytest.approx(8.0, abs=0.5)

    def test_a_starved_read_says_so_rather_than_waiting(self):
        pool = _Pool(_plan())
        spent = app_mod._CashoutBudget(total=0.0)
        rows, why = asyncio.run(
            app_mod._cc_fetch(pool, spent, "SELECT 1 LIMIT 1"))
        assert rows is None and "budget" in why
        assert pool.sqls == []

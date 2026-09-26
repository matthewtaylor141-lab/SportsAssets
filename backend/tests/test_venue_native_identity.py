"""THE IDENTITY CROSSING, pinned where six production reads were lost.

Runs 22, 23 and 24 spent every venue book read on `markets.slug` -- the
GLOBAL catalogue's identifier -- against a US endpoint that takes
`us_premap.market_slug`. The venue answered `NotFoundError` each time, and
the loop reported it as a venue problem for two hours.

These tests pin the four properties that stop it recurring:

    1 · the venue read takes a venue-native slug and nothing else;
    2 · the crossing REUSES `workers.premap.resolve` rather than adding a
        sixth resolver with its own idea of side matching;
    3 · a contract whose buy intent is not LONG on the priced outcome
        refuses by name, because comparing p(outcome) to the ask on the
        complement is a sign error that looks like a large edge;
    4 · the record carries both identifiers, labelled, and neither is
        derived from the other.
"""
import ast
import inspect

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_venue_mapping as vmap
from sportsassets.workers import ext_pinnacle_loop as loop


# THE CATALOGUE ROW THE PERIOD CHECK READS.
#
# `resolve_venue_identity` now asks `us_premap` for the venue's own kind,
# event and side, and for how many contracts it publishes for that event.
# A connection stub with no `fetchrow` makes that read raise, which
# correctly refuses the identity -- and would leave these tests asserting
# nothing about the payout-orientation rule they exist for. So the stub
# answers with the catalogue's own fields, which is what production reads.
class _CatalogueConn:
    def __init__(self, *, kind="aec", event_slug=None, side_norm=None,
                 siblings=2):
        self._row = {"kind": kind, "event_slug": event_slug,
                     "side_norm": side_norm, "sibling_markets": siblings}

    async def fetchrow(self, sql, *args):
        return self._row




def test_the_venue_read_takes_a_venue_native_slug():
    sig = inspect.signature(loop.venue_quote)
    assert "us_slug" in sig.parameters, (
        "venue_quote must be handed the venue's own slug")
    assert "condition_id" not in sig.parameters, (
        "a global condition id must not be reachable as the read's key")
    src = inspect.getsource(loop.venue_quote)
    assert "FROM markets" not in src, (
        "the global catalogue must not be consulted for the read key")


def test_the_crossing_reuses_the_existing_resolver():
    src = inspect.getsource(loop.resolve_venue_identity)
    assert "premap" in src and "resolve(" in src, (
        "the copy lane's resolver is the crossing; do not write another")
    # and it is called with the GLOBAL slug, which is what that resolver
    # keys from -- not with the venue slug it returns
    assert 'market_row.get("slug")' in src


def test_a_short_intent_is_a_VALID_resolved_exposure():
    """WHAT THIS TEST USED TO ASSERT, AND WHY IT WAS RIGHT THEN.

    It required BUY_SHORT to be REFUSED, because `venue_quote` could only
    read the offer ladder and p(home) against the long book's ask on a
    short leg is a sign error. Run 28 refused eleven of forty-one
    candidates on that rule, correctly.

    The resolver was never wrong. On the `aec-` family both sides of a
    market carry the SAME identifier -- equal to the slug -- and the side
    is carried only by the INTENT, so BUY_SHORT is the resolver correctly
    saying "the exposure to this outcome is the short leg". The reader now
    consumes the ladder the intent names, so the exposure resolves instead
    of being refused.

    AND THE PAYOUT DOES NOT INVERT. An earlier version of this test said
    the payout event became NOT(Chicago Cubs). That was the defect: the
    resolver was asked for Cubs and returned the side that BUYS Cubs, so
    the contract PAYS ON CUBS. The intent selects the ladder and nothing
    else.

    The protection did not go away: it moved to where it belongs. The
    contract must declare which event it pays on, and the refusal now
    fires only for an intent nothing can consume."""

    class _Conn:
        pass

    import asyncio

    async def _fake_resolve(conn, title, event_title, outcome, slug, **kw):
        # WHAT premap.resolve ACTUALLY RETURNS -- and this comment used to
        # say that over a dict that got it wrong. The real resolver returns
        # the matched side under `outcome`, the venue identifier under
        # `market_slug`, and the venue's question under `title`. There is no
        # `side_norm`, no `identifier` and no `question` key.
        #
        # `resolve_venue_identity` was reading those three absent names, so
        # production recorded three nulls while this stub, agreeing with the
        # bug, recorded values. A stub that mirrors the code instead of the
        # dependency cannot catch a field-name error in either.
        return {"market_slug": "aec-mlb-chc-mia-2026-09-24-cubs",
                "intent": "ORDER_INTENT_BUY_SHORT",
                "outcome": "cubs",
                "title": "Will the Cubs win?",
                "matched_by": "keys", "score": 1.0}

    from sportsassets.workers import premap as _pm

    orig = _pm.resolve
    _pm.resolve = _fake_resolve
    try:
        out = asyncio.run(loop.resolve_venue_identity(
            _CatalogueConn(event_slug="mlb-chc-mia-2026-09-24", side_norm="cubs"), market_row={"slug": "mlb-chc-mia-2026-09-24",
                                 "condition_id": "0xabc",
                                 "title": "Will the Cubs beat the Marlins?",
                                 "event_title": "Chicago Cubs vs Miami Marlins"},
            priced_outcome="Chicago Cubs"))
    finally:
        _pm.resolve = orig
    assert out["ok"] is True, out
    assert out["intent"] == "ORDER_INTENT_BUY_SHORT"
    assert out["us_market_slug"] == "aec-mlb-chc-mia-2026-09-24-cubs"
    # THE PAYOUT EVENT IS THE REQUESTED OUTCOME.
    #
    # CORRECTED. This assertion previously required
    # payout_event == "NOT(Chicago Cubs)", which ENCODED THE DEFECT: it
    # read the payout event off the intent. premap.resolve was asked for
    # Cubs and returned the side that BUYS Cubs, so a BUY_SHORT result
    # means Cubs is the venue's short side and the contract still PAYS ON
    # CUBS. The intent selects the ladder; it does not invert the payout.
    assert out["payout_event"] == "Chicago Cubs"
    assert out["probability_event"] == "Chicago Cubs"
    assert out["payout_is_complement"] is False
    assert out["ladder_side"] == "BID", "a short buys off the bid ladder"
    assert out["matched_side_norm"] is not None, (
        "the resolver's own side evidence must be preserved")


def test_an_unconsumable_intent_still_refuses_by_name():
    """The narrowed refusal. An intent naming no side this reader can
    consume must still stop, rather than defaulting to a ladder."""

    class _Conn:
        pass

    import asyncio

    async def _fake_resolve(conn, title, event_title, outcome, slug, **kw):
        return {"market_slug": "aec-x", "intent": "ORDER_INTENT_WHATEVER"}

    from sportsassets.workers import premap as _pm

    orig = _pm.resolve
    _pm.resolve = _fake_resolve
    try:
        out = asyncio.run(loop.resolve_venue_identity(
            _CatalogueConn(event_slug="mlb-chc-mia-2026-09-24", side_norm="cubs"), market_row={"slug": "s", "condition_id": "0x1",
                                 "title": "t", "event_title": "e"},
            priced_outcome="Chicago Cubs"))
    finally:
        _pm.resolve = orig
    assert out["ok"] is False
    assert out["refusal"] == loop.R_INTENT_NOT_LONG
    assert "names no side this reader can consume" in out["why"]


def test_a_long_intent_pays_on_the_priced_outcome():
    class _Conn:
        pass

    import asyncio

    async def _fake_resolve(conn, title, event_title, outcome, slug, **kw):
        # `outcome` is carried because the venue slug ends in a side token,
        # and the PERIOD check admits a trailing token only when it is the
        # matched side. A stub that omits it leaves `-cubs` unexplained,
        # which is correctly refused: an unexplained token after the date
        # could be a half, an inning or a double chance.
        return {"market_slug": "aec-mlb-chc-mia-2026-09-24-cubs",
                "outcome": "cubs",
                "intent": "ORDER_INTENT_BUY_LONG"}

    from sportsassets.workers import premap as _pm

    orig = _pm.resolve
    _pm.resolve = _fake_resolve
    try:
        out = asyncio.run(loop.resolve_venue_identity(
            _CatalogueConn(event_slug="mlb-chc-mia-2026-09-24", side_norm="cubs"), market_row={"slug": "s", "condition_id": "0x1",
                                 "title": "t", "event_title": "e"},
            priced_outcome="Chicago Cubs"))
    finally:
        _pm.resolve = orig
    assert out["ok"] is True
    assert out["ladder_side"] == "ASK"
    assert out["payout_event"] == "Chicago Cubs"
    assert out["payout_is_complement"] is False
    # THE PERIOD IS ESTABLISHED, not asserted, and it says how.
    assert out["period"] == "FULL_GAME"
    assert out["period_evidence"]["residual"] == "cubs"


def test_no_premap_row_refuses_before_any_venue_read():
    """A fixture the venue's catalogue does not carry cannot answer a book
    read. Asking anyway spends a read the collector shares."""
    import asyncio

    async def _none(conn, *a, **kw):
        return None

    from sportsassets.workers import premap as _pm

    orig = _pm.resolve
    _pm.resolve = _none
    try:
        out = asyncio.run(loop.resolve_venue_identity(
            object(), market_row={"slug": "mlb-x-y-2026-09-24",
                                  "condition_id": "0xdef",
                                  "title": "t", "event_title": "e"},
            priced_outcome="X"))
    finally:
        _pm.resolve = orig
    assert out["refusal"] == loop.R_NO_PREMAP
    assert "GLOBAL id" in out["why"]


def test_the_mapper_hands_back_the_row_not_just_an_id():
    """Returning only a condition id is what sent the caller back to the
    database, where it found the global slug."""
    markets = [{"condition_id": "0xML", "closed": False, "resolved": False,
                "title": "Will the Chicago Cubs beat the Miami Marlins?",
                "event_title": "Chicago Cubs vs Miami Marlins",
                "slug": "mlb-chc-mia-2026-09-24"}]
    out = vmap.map_event(home="Chicago Cubs", away="Miami Marlins",
                         markets=markets)
    assert out["mapped"] is True
    assert out["market_row"]["slug"] == "mlb-chc-mia-2026-09-24"


def test_the_record_labels_which_identity_it_carries():
    src = inspect.getsource(ext.persist)
    assert "us_market_slug" in src
    assert "BOTH_PRESENT_AND_INDEPENDENTLY_SOURCED" in src
    # the basis must be derived from what is PRESENT, never asserted
    assert 'c.get("condition_id") and c.get("us_market_slug")' in src


def test_the_census_reports_the_identity_split():
    assert "us_market_slug IS NOT NULL" in ext.SUMMARY
    assert "contract_identity_basis" in ext.IDENTITY_CENSUS


def test_the_migration_does_not_fabricate_an_identity():
    """The one thing this migration must never do is invent a condition id
    or assert a global-to-US equivalence."""
    import pathlib

    sql = pathlib.Path(
        "migrations/106_external_valuations_venue_native_identity.sql"
    ).read_text()
    assert "us_market_slug text" in sql
    # both identities keyed in the uniqueness index, so two different
    # venue-native contracts cannot collide on the empty string
    assert "coalesce(us_market_slug, '')" in sql
    assert "coalesce(condition_id, '')" in sql
    # and existing rows are labelled rather than rewritten
    assert "SET contract_identity_basis = 'GLOBAL_CONDITION_ID'" in sql
    for forbidden in ("INSERT INTO external_valuations",
                      "UPDATE external_valuations\n   SET condition_id",
                      "SET us_market_slug ="):
        assert forbidden not in sql, (
            "%s would fabricate or move an identity" % forbidden)


def test_the_insert_and_its_arguments_agree():
    """A column list that outgrows its placeholders is an insert that puts
    the settlement rule where the event key belongs."""
    import re

    blk = ext.INSERT[ext.INSERT.index("INSERT INTO"):]
    body = blk[:blk.index("RETURNING id")]
    nph = len({int(m) for m in re.findall(r"\$(\d+)", body)})
    tree = ast.parse(inspect.getsource(ext.persist))
    call = [n for n in ast.walk(tree)
            if isinstance(n, ast.Await)
            and isinstance(n.value, ast.Call)][0].value
    # first positional arg is INSERT itself, the rest are the values
    nargs = len(call.args) - 1
    assert nph == nargs, (
        "%d placeholders against %d arguments" % (nph, nargs))

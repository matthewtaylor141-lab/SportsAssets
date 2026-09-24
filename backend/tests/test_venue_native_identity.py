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


def test_a_non_long_intent_refuses_by_name():
    """A SHORT contract pays on the complement. p(home) against its ask is
    a sign error, and a sign error with a plausible number attached is
    worse than a refusal."""

    class _Conn:
        pass

    import asyncio

    async def _fake_resolve(conn, title, event_title, outcome, slug, **kw):
        return {"market_slug": "aec-mlb-chc-mia-2026-09-24-marlins",
                "intent": "ORDER_INTENT_BUY_SHORT"}

    from sportsassets.workers import premap as _pm

    orig = _pm.resolve
    _pm.resolve = _fake_resolve
    try:
        out = asyncio.run(loop.resolve_venue_identity(
            _Conn(), market_row={"slug": "mlb-chc-mia-2026-09-24",
                                 "condition_id": "0xabc",
                                 "title": "Will the Cubs beat the Marlins?",
                                 "event_title": "Chicago Cubs vs Miami Marlins"},
            priced_outcome="Chicago Cubs"))
    finally:
        _pm.resolve = orig
    assert out["ok"] is False
    assert out["refusal"] == loop.R_INTENT_NOT_LONG
    assert "sign error" in out["why"]


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

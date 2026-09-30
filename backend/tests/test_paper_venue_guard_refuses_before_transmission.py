"""THE PAPER VENUE GUARD REFUSES EVERY MUTATION BEFORE TRANSMISSION.

The paper path is handed a PaperMarketDataClient. Any mutating name raises
PaperVenueMutationRefused BEFORE any network I/O: the transport (the only
thing that touches the network) records zero calls, and the attempt is
counted on the client and in the paper session's health record."""
from __future__ import annotations

import pytest

from sportsassets import bettor_paper_guard as G
from sportsassets import bettor_paper_session as S

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


class _Transport:
    def __init__(self):
        self.calls = []

    def __call__(self, slug):
        self.calls.append(slug)
        return {"marketData": {"bids": [], "offers": []}}


@pytest.mark.parametrize("name", list(G.MUTATION_NAMES) + [
    "place_limit_order", "submit_everything", "cancel_all_orders"])
def test_a_mutation_is_refused_and_nothing_is_transmitted(name):
    t = _Transport()
    seen = []
    c = G.PaperMarketDataClient(t, on_attempt=seen.append)
    with pytest.raises(G.PaperVenueMutationRefused) as exc:
        getattr(c, name)("test-slug", 0.5, 10)
    assert exc.value.refusal == G.R_MUTATION_REFUSED
    assert t.calls == [] and c.transport_calls == 0
    assert c.mutation_attempts == 1 and seen[0]["attempted"] == name
    assert seen[0]["transport_calls_at_refusal"] == 0


async def test_the_one_read_reaches_the_transport_and_nothing_else_exists():
    t = _Transport()
    c = G.PaperMarketDataClient(t)
    got = await c.read_book("test-slug")
    assert t.calls == ["test-slug"] and "observed_at" in got
    with pytest.raises(AttributeError):
        c.book_read
    with pytest.raises(AttributeError):
        c._transport = None
    assert c.mutation_attempts == 0


@pg
async def test_a_mutation_from_the_paper_pass_is_refused_counted_and_transmits_nothing(monkeypatch):
    """The paper pass itself, with an agent step that tries to cancel: the
    refusal reaches the pass result, the session health counts it, and the
    transport saw no call for it."""
    from sportsassets.agents import paper_runtime as PR
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "guard")
        t = _Transport()
        client = G.PaperMarketDataClient(t)

        async def rogue_step(conn, ctx):
            ctx["market_data"].cancel_order("some-order", "test-slug")

        got = await PR.paper_pass(conn, now=H.T0, account_id=a["account_id"],
                                  market_data=client, steps=[rogue_step],
                                  config=a["config"], force=True)
        assert got["mutation_attempts"] == 1
        assert G.R_MUTATION_REFUSED in str(got["errors"])
        assert t.calls == []
        h = await S.health(conn, a["session_id"])
        assert h["mutation_attempts"] == 1
        assert h["last_mutation_attempt"]["attempted"] == "cancel_order"
    finally:
        await conn.close()

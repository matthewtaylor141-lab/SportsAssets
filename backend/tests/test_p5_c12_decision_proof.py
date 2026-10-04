"""P5 C12 DECISION-TIME PROOF: THE DECISION, ITS INTENT AND THE ONE STREAM BOOK.

Through the real reactive path (fresh PinnAPI WS change -> collector -> the V3
completed-game decision -> the execution intent -> the ACTUAL lane) against a
FAKE retail venue, with the resident institutional stream book a
`ResidentBooks` fed directly (the fixtures of test_p5_c12_stream_priced_actual).

  §1  a CURRENT stream book of the exactly mapped contract: ONE proof record,
      PRICED_FROM_STREAM, tying decision_id, execution_intent id, stream
      symbol, epoch, connection, observation id, book receipt instant, venue
      transact_time, book age and the decision's executable price -- the
      price Venue.place was called with, once
  §2  stale, crossed, gapped, other-symbol, mapped-to-another-symbol and
      no-edge stream books: a REFUSED_BEFORE_SUBMISSION proof naming the
      refusal, the intent never live-eligible, Venue.place ZERO times
  §3  no stream book (no mapper): no proof, nothing placed
  §4  the proof is pure over the decision's own payload; a failed write never
      touches the decision; the table CHECKs a priced proof's facts and that a
      refused one never made a live-eligible intent
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import asyncpg
import pytest

from sportsassets import live_book_currency as LBC
from sportsassets import live_book_evidence as LBE
from sportsassets import p5_c12_proof as C12P
from sportsassets.agents import paper_benchmark as PB

try:
    from tests import test_p5_c12_stream_priced_actual as C12
    from tests.test_pinnapi_reactive_integration import env, pg  # noqa: F401
    from tests.test_execution_intent_fanout import _disarm
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import test_p5_c12_stream_priced_actual as C12  # type: ignore
    from test_pinnapi_reactive_integration import env, pg  # noqa: F401
    from test_execution_intent_fanout import _disarm  # type: ignore
    import paper_harness as H  # type: ignore


async def proof_of(e, it):
    r = await e.conn.fetchrow(
        "SELECT * FROM p5_c12_decision_proof WHERE decision_id = $1",
        it["decision_id"])
    return None if r is None else dict(r)


# ── §1 priced from the stream: the proof ties every fact ────────────────

@pg
async def test_a_stream_priced_decision_writes_its_c12_proof(env, monkeypatch):
    e = env
    books = C12.stream_books(e.game.us_slug)
    try:
        it = await C12.run_one(e, monkeypatch, setup=lambda: C12.install_stream(
            monkeypatch, books))
        so = it["evidence"]["stream_observation"]
        p = await proof_of(e, it)
        assert p is not None, "no C12 proof"
        assert p["proof_status"] == C12P.PRICED and p["refusal"] is None
        assert p["execution_intent_id"] == it["intent_id"]
        assert p["decision_id"] == it["decision_id"]
        assert p["us_market_slug"] == e.game.us_slug
        assert p["stream_symbol"] == e.game.us_slug
        assert p["connection_epoch"] == so["connection_epoch"] == 1
        assert p["connection_id"] == "grpc-c12"
        assert p["obs_id"] == so["obs_id"]
        assert abs(p["book_received_at"].timestamp()
                   - so["observed_at"]) < 1e-3
        assert p["book_venue_ts"] is not None
        assert p["book_age_s"] == so["age_s"]
        assert 0 <= p["book_age_s"] <= LBC.MAX_RECEIPT_AGE_S
        assert p["decision_executable_price"] == it["wire_price"]
        assert p["p5_verdict"] == LBC.ESTABLISHED
        assert p["c12_passed"] is True
        assert json.loads(p["failed_components"]) == []
        assert p["price_source"] == PB.STREAM_BOOK_SOURCE
        assert p["live_eligible"] is True
        # the proof's price is the price the venue was sent, once
        assert len(e.retail.placed) == 1
        assert float(e.retail.placed[0]["price"]["value"]) == \
            float(p["decision_executable_price"])
    finally:
        await _disarm(e)


# ── §2 every unusable stream book: refused before submission ────────────

def _refused(name):
    async def case(env, monkeypatch):
        e = env
        slug = e.game.us_slug
        sym, read_symbol = None, None
        if name == "stale":
            books = C12.stream_books(slug, age_s=5.0)
        elif name == "crossed":
            books = C12.stream_books(slug, bids=((520, 300000),),
                                     offers=((510, 300000),))
        elif name == "gap":
            books = C12.stream_books(slug)
            books.on_disconnected("test: connection lost")
        elif name == "other_symbol":
            books, read_symbol = C12.stream_books(C12.OTHER), C12.OTHER
        elif name == "mapped_to_other":
            books, sym = C12.stream_books(C12.OTHER), C12.OTHER
        elif name == "no_edge":
            books = C12.stream_books(slug, bids=((10, 300000),),
                                     offers=((990, 300000),))
        try:
            it = await C12.run_one(e, monkeypatch,
                                   setup=lambda: C12.install_stream(
                                       monkeypatch, books, symbol=sym,
                                       read_symbol=read_symbol))
            assert it["live_eligible"] is False
            await C12.no_order(e, it)                 # Venue.place: ZERO
            p = await proof_of(e, it)
            assert p is not None, (name, "no C12 proof")
            assert p["proof_status"] == C12P.REFUSED, (name, p)
            assert p["refusal"], name
            assert p["live_eligible"] is False
            assert p["execution_intent_id"] == it["intent_id"]
            assert p["decision_executable_price"] is None
            failed = json.loads(p["failed_components"])
            if name == "stale":
                assert p["p5_verdict"] == LBC.STALE
                assert "C8_RECEIPT_AGE" in failed
                assert p["refusal"] == "P5_STALE:C8_RECEIPT_AGE"
                assert p["book_age_s"] > LBC.MAX_RECEIPT_AGE_S
                assert p["stream_symbol"] == slug
            elif name == "crossed":
                assert p["p5_verdict"] == LBC.REFUSED
                assert "C12_PRICED_FROM_THIS_BOOK" in failed
                assert p["c12_passed"] is False
            elif name == "gap":
                assert p["p5_verdict"] == LBC.GAP
            elif name == "other_symbol":
                assert "C1_IDENTITY_EXACT" in failed
            elif name == "mapped_to_other":
                assert p["stream_symbol"] == C12.OTHER
                assert p["refusal"] == \
                    "STREAM_BOOK_SYMBOL_IS_NOT_THE_DECISION_CONTRACT"
            elif name == "no_edge":
                assert p["price_source"] == PB.STREAM_BOOK_SOURCE + "_NO_ORDER"
                assert p["refusal"].startswith("STREAM_PRICING_REFUSED:")
        finally:
            await _disarm(e)
    case.__name__ = "test_a_%s_stream_book_writes_a_refused_proof" % name
    return pg(case)


test_a_stale_stream_book_writes_a_refused_proof = _refused("stale")
test_a_crossed_stream_book_writes_a_refused_proof = _refused("crossed")
test_a_gap_stream_book_writes_a_refused_proof = _refused("gap")
test_an_other_symbol_stream_book_writes_a_refused_proof = \
    _refused("other_symbol")
test_a_mapping_to_another_symbol_writes_a_refused_proof = \
    _refused("mapped_to_other")
test_a_no_edge_stream_price_writes_a_refused_proof = _refused("no_edge")


# ── §3 no stream book: no proof ──────────────────────────────────────────

@pg
async def test_without_a_stream_book_no_proof_is_written(env, monkeypatch):
    e = env
    try:
        it = await C12.run_one(e, monkeypatch, setup=lambda: monkeypatch.setattr(
            LBE, "IDENTITY_MAPPER", None))
        assert await proof_of(e, it) is None
        await C12.no_order(e, it)
    finally:
        await _disarm(e)


# ── §4 pure build; a failed write; the CHECKs ────────────────────────────

def _payload(**book):
    return {"decision_id": "paperdec:x", "slug": "aec-a-b-2026",
            "evidence": {"admission_facts": {"book": book}}}


def test_a_rest_decision_builds_no_proof():
    assert C12P.build({"intent_id": "ei_x"}, _payload(
        book_currency=dict(PB.BOOK_CURRENCY),
        live_book_currency={"verdict": "NOT_ESTABLISHED",
                            "stream_read": False})) is None
    assert C12P.build({}, {}) is None


def test_a_proof_write_that_fails_never_raises_into_the_decision():
    class Conn:
        def transaction(self):
            raise RuntimeError("database down")
    payload = _payload(book_currency={
        "rule": LBC.RULE_ID, "stream_read": True, "verdict": LBC.GAP,
        "symbol": "aec-a-b-2026", "failed_components": ["C3"]})
    got = asyncio.run(C12P.record(Conn(), {"intent_id": "ei_x",
                                           "us_market_slug": "aec-a-b-2026",
                                           "decision_id": "paperdec:x",
                                           "live_eligible": False}, payload))
    assert got["written"] is False and got["why"].startswith("WRITE_FAILED")
    assert got["proof_status"] == C12P.REFUSED


@pg
async def test_the_proof_table_checks_a_priced_proofs_facts():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    ins = ("INSERT INTO p5_c12_decision_proof (version, decision_id, "
           " execution_intent_id, us_market_slug, proof_status, refusal, "
           " stream_symbol, connection_epoch, book_received_at, book_age_s, "
           " decision_executable_price, c12_passed, p5_verdict, live_eligible)"
           " VALUES ('v',$1,'ei','s',$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)")
    try:
        now = datetime.now(timezone.utc)
        await conn.execute(ins, "d0", C12P.PRICED, None, "s", 1, now, 0.1,
                           0.5, True, LBC.ESTABLISHED, True)
        bad = [
            ("d1", C12P.PRICED, None, "other", 1, now, 0.1, 0.5, True,
             LBC.ESTABLISHED, True),                      # wrong symbol
            ("d2", C12P.PRICED, None, "s", None, now, 0.1, 0.5, True,
             LBC.ESTABLISHED, True),                      # no epoch
            ("d3", C12P.PRICED, None, "s", 1, None, 0.1, 0.5, True,
             LBC.ESTABLISHED, True),                      # no book instant
            ("d4", C12P.PRICED, None, "s", 1, now, None, 0.5, True,
             LBC.ESTABLISHED, True),                      # no book age
            ("d5", C12P.PRICED, None, "s", 1, now, 0.1, None, True,
             LBC.ESTABLISHED, True),                      # no price
            ("d6", C12P.PRICED, None, "s", 1, now, 0.1, 0.5, False,
             LBC.ESTABLISHED, True),                      # C12 failed
            ("d7", C12P.PRICED, None, "s", 1, now, 0.1, 0.5, True,
             LBC.STALE, True),                            # not ESTABLISHED
            ("d8", C12P.REFUSED, "X", "s", 1, now, 5.0, None, False,
             LBC.STALE, True),                            # refused yet live
            ("d9", C12P.REFUSED, None, "s", 1, now, 5.0, None, False,
             LBC.STALE, False),                           # refusal unnamed
        ]
        for args in bad:
            with pytest.raises(asyncpg.CheckViolationError):
                async with conn.transaction():
                    await conn.execute(ins, *args)
    finally:
        await tx.rollback()
        await conn.close()

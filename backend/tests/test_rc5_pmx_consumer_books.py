"""RC5: THE PMX INSTITUTIONAL BOOK IS THE CONSUMERS' PRIMARY BOOK SOURCE.

Production (release 7fd4574e = RC4, pm-acceptance run 37738089957): the
deciding process held 30 venue-acknowledged PMX books, yet every consumer
read REST (held marks REST 4 / INSTITUTIONAL_STREAM 0; the paper owner 243
REST dispatches and 19 HTTP 429s; the collector's venue_quote always
_read_book_blocking), and the SOFTWARE first losses after RC4 were the REST
budget's: QUOTE_STALE_ON_ARRIVAL 35 / h, PROBABILITY_DEADLINE_PASSED 13 / h.

  §1  the rule (paper_pmx_books.arbitrate, pure): exact identity, the
      venue's ack on THIS connection, the stream's decision-bound current(),
      our receipt inside the owner's shared-read age (6 s, never wider),
      the reader's not_before, THIS symbol's own same-book evidence; each
      missing proof is a named REST fallback; observed_at is the stream's
      receipt, never now
  §2  the paper owner: a PMX book serves with no REST dispatch; otherwise
      REST exactly as before, counted with the reason; the public lane and
      a kill switch never serve PMX; a recorded PMX observation names its
      feed (INSTITUTIONAL_STREAM)
  §3  the collector's venue_quote: the PMX book is judged exactly as a REST
      book with ITS receipt instant, no REST read is made; only the paper /
      calibration reads may use it (funded inputs, hedge quotes and the
      probe read REST); a PMX-priced record is never a funded input
  §4  the API stream: the decision path's asked symbols are bounded by
      recency (never frozen at the first 32), fill the spare capacity after
      held markets, and take refdata the plane already persisted (recent,
      listed, exact symbol) instead of a REST read
  §5  per-symbol same-book evidence accrues on every consumer REST read of
      an exact symbol (no request added, the read unchanged)
  §6  no order path, no PinnAPI authority touched

Synthetic data, in-memory streams, fakes for every venue; no network, no
order.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from datetime import datetime, timezone

import pytest

from sportsassets import bettor_paper_freshness as PMF
from sportsassets import bettor_paper_guard as G
from sportsassets import institutional_api_stream as IAS
from sportsassets import institutional_focus_universe as FU
from sportsassets import institutional_same_book as SB
from sportsassets import institutional_stream as IS
from sportsassets import paper_market_data as PMD
from sportsassets import paper_pmx_books as PCB
from sportsassets import venue_request_gate as GRT
from sportsassets.workers import ext_pinnacle_loop as LOOP

from tests import paper_harness as H
from tests.test_institutional_contract_map import AEC

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

SYM = "aec-mlb-sd-mil-2026-10-03"
SUPPORTED = {"status": "SUPPORTED", "detail": {"comparable": 40,
                                               "agree_rate": 1.0}}
LONG = "ORDER_INTENT_BUY_LONG"


def rec_for(slug: str) -> dict:
    """AEC's exact record re-keyed to another registered contract."""
    ev = slug[len("aec-"):]
    return json.loads(json.dumps(AEC).replace("mlb-sd-mil-2026-10-03", ev))


@pytest.fixture(autouse=True)
def _clean():
    GRT.clear_hold()
    PMD.reset()
    PCB.reset()
    IS.reset()
    IAS.reset()
    FU.note_held_first([])
    PMD._TAP["tap"] = None
    yield
    GRT.clear_hold()
    PMD.reset()
    PCB.reset()
    IS.reset()
    IAS.reset()
    FU.note_held_first([])
    PMD._TAP["tap"] = None


def resident(symbol=SYM, *, received_at=None, acked=True,
             bids=((450, 1000),), offers=((470, 500),)):
    """A resident book on a live connection, received `received_at`."""
    at = time.time() if received_at is None else received_at
    b = IS.ResidentBooks()
    b.set_state(IS.S_IDLE, "test")
    b.set_instrument(symbol, rec_for(symbol))
    b.want([symbol])
    b.on_connected("grpc-test")
    if acked:
        b.on_ack(added=[symbol])
    b.on_update({"symbol": symbol, "bids": list(bids),
                 "offers": list(offers), "state": "INSTRUMENT_STATE_OPEN",
                 "transact_time": datetime.fromtimestamp(at, tz=timezone.utc),
                 "book_hidden": False}, received_at=at)
    b.on_heartbeat()
    return b


def ident(symbol=SYM, **kw):
    return dict({"status": "EXACT", "symbol": symbol,
                 "institutional_side": "LONG", "price_transform": "IDENTITY",
                 "price_scale": 1000, "qty_scale": 100, "version": "t"}, **kw)


def live_here(monkeypatch, *, books=None, evidence=True, symbol=SYM,
              received_at=None):
    """THIS process runs the stream: resident books, exact refdata, loaded
    same-book evidence -- the production defaults, nothing injected."""
    b = books if books is not None else resident(symbol,
                                                 received_at=received_at)
    monkeypatch.setattr(IS, "BOOKS", b)
    monkeypatch.setattr(IAS, "running", lambda: True)
    IAS.REFDATA[symbol] = {"record": rec_for(symbol), "at": time.time()}
    if evidence:
        PCB.EVIDENCE.put([symbol], {symbol: SUPPORTED}, {})
    return b


def rest_md(bid="0.45", ask="0.47"):
    return {"bids": [{"px": {"value": bid}, "qty": "10"}],
            "offers": [{"px": {"value": ask}, "qty": "5"}],
            "transactTime": "2026-10-08T06:00:00Z"}


class Rest:
    """A REST transport that records every dispatch."""

    def __init__(self):
        self.calls = []

    def __call__(self, slug, *, deadline_epoch_s=None):
        self.calls.append(slug)
        return {"marketData": rest_md(), "observed_at": time.time(),
                "request_accounting": {"statuses": [200]}}


def owner(rest, **kw):
    kw.setdefault("same_book_tap", False)
    return PMD.Owner(transport=rest, recent=lambda s, max_age_s: None,
                     gate=lambda: {"blocking": False, "seconds_left": 0.0,
                                   "reason": None}, **kw)


# ═════════════════════════════════════════════════════════════════════
# §1 THE RULE
# ═════════════════════════════════════════════════════════════════════

def test_a_book_with_every_proof_serves_with_its_own_receipt_instant():
    recv = time.time() - 2.0
    b = resident(received_at=recv)
    now = time.time()
    got = PCB.arbitrate(SYM, identity=ident(), current=b.current(SYM, now=now),
                        same_book=SUPPORTED, now=now)
    assert got["ok"], got
    rd = got["read"]
    assert rd["observed_at"] == recv          # OUR receipt, never now
    assert rd["book_source"] == PCB.SOURCE_PMX == "PMX_GRPC"
    assert rd["marketData"]["bids"] == [{"px": {"value": "0.45"},
                                         "qty": "10"}]
    assert rd["marketData"]["offers"] == [{"px": {"value": "0.47"},
                                           "qty": "5"}]
    assert rd["marketData"]["transactTime"] == \
        b.current(SYM, now=now)["evidence"]["snapshot"]["venue_ts"]
    assert rd["pmx"]["receipt_age_s"] == pytest.approx(now - recv, abs=1e-3)
    assert rd["pmx"]["venue_clock_decides_nothing"] is True
    assert rd["error"] is None


def test_every_missing_proof_is_a_named_rest_fallback():
    now = time.time()
    b = resident(received_at=now - 1.0)
    cur = b.current(SYM, now=now)

    def go(**kw):
        a = dict(identity=ident(), current=cur, same_book=SUPPORTED, now=now)
        a.update(kw)
        return PCB.arbitrate(SYM, **a)
    assert go()["ok"]
    assert go(identity=None)["refusal"] == PCB.R_IDENTITY
    assert go(identity=ident(status="UNAVAILABLE"))["refusal"] == \
        PCB.R_IDENTITY
    assert go(identity=ident(symbol="aec-other"))["refusal"] == PCB.R_SYMBOL
    assert go(identity=ident(institutional_side="SHORT"))["refusal"] == \
        PCB.R_ORIENTATION
    assert go(identity=ident(price_transform="COMPLEMENT"))["refusal"] == \
        PCB.R_ORIENTATION
    # aggregate agreement over OTHER symbols never admits this one
    for sb in (None, {"status": "INCONCLUSIVE"}, {"status": "CONTRADICTED"},
               {"status": "UNTESTED"}):
        assert go(same_book=sb)["refusal"] == PCB.R_SAME_BOOK
    # the reader's not_before: a book received before it never answers
    assert go(not_before_epoch=now - 0.5)["refusal"] == PCB.R_NOT_BEFORE
    assert go(not_before_epoch=now - 1.5)["ok"]
    # the stream's own refusal is carried by name
    b.on_disconnected("test")
    got = go(current=b.current(SYM, now=now))
    assert got["refusal"] == "%s:%s" % (PCB.R_STREAM, IS.R_GAP_CONNECTION)


def test_a_book_the_venue_has_not_acknowledged_on_this_connection_is_refused():
    now = time.time()
    b = resident(received_at=now - 1.0, acked=False)
    cur = b.current(SYM, now=now)
    assert cur["ok"] is True                       # the stream itself is fine
    assert cur["evidence"]["subscription"] == {
        "acked_seq": None, "acked_on_current_connection": False}
    got = PCB.arbitrate(SYM, identity=ident(), current=cur,
                        same_book=SUPPORTED, now=now)
    assert got["refusal"] == PCB.R_NOT_ACKED
    # acknowledged on THIS connection: admitted; a new connection clears it
    b.on_ack(added=[SYM])
    cur = b.current(SYM, now=now)
    assert cur["evidence"]["subscription"]["acked_on_current_connection"]
    assert PCB.arbitrate(SYM, identity=ident(), current=cur,
                         same_book=SUPPORTED, now=now)["ok"]
    b.on_connected("grpc-second")
    b.on_update({"symbol": SYM, "bids": [(450, 1000)], "offers": [(470, 500)],
                 "state": "INSTRUMENT_STATE_OPEN",
                 "transact_time": datetime.fromtimestamp(now,
                                                         tz=timezone.utc)},
                received_at=now)
    cur = b.current(SYM, now=now)
    assert cur["ok"] and cur["evidence"]["subscription"][
        "acked_on_current_connection"] is False


def test_the_receipt_limit_is_the_owners_shared_read_age_never_wider():
    assert PCB.MAX_RECEIPT_AGE_S == PMD.CACHE_MAX_AGE_S == \
        G.SHARED_BOOK_MAX_AGE_S == 6.0
    # the stream's decision bound and the held-mark SLA are unchanged
    assert IS.MAX_SNAPSHOT_AGE_S == 30.0
    assert IS.HELD_MARK_MAX_SNAPSHOT_AGE_S == PMF.SLA_S == 300.0
    now = time.time()
    b = resident(received_at=now - 7.0)
    cur = b.current(SYM, now=now)
    assert cur["ok"] is True                # current under the stream's 30 s
    for asked in (6.0, 60.0, 600.0):        # a caller cannot widen it
        got = PCB.arbitrate(SYM, identity=ident(), current=cur,
                            same_book=SUPPORTED, now=now,
                            max_receipt_age_s=asked)
        assert got["refusal"] == PCB.R_RECEIPT_OLD, asked
    b2 = resident(received_at=now - 5.0)
    assert PCB.arbitrate(SYM, identity=ident(), current=b2.current(
        SYM, now=now), same_book=SUPPORTED, now=now)["ok"]
    # a narrower consumer limit narrows it
    assert PCB.arbitrate(SYM, identity=ident(), current=b2.current(
        SYM, now=now), same_book=SUPPORTED, now=now,
        max_receipt_age_s=3.0)["refusal"] == PCB.R_RECEIPT_OLD


def test_evidence_not_loaded_recently_is_absent():
    ev = PCB.Evidence()
    now = time.time()
    assert ev.effective(SYM, ident(), now=now)["refusal"] == \
        PCB.R_SAME_BOOK_UNLOADED
    ev.put([SYM], {SYM: SUPPORTED}, {}, at=now)
    assert ev.effective(SYM, ident(), now=now)["status"] == "SUPPORTED"
    later = now + PCB.SAME_BOOK_EVIDENCE_MAX_AGE_S + 1
    assert ev.effective(SYM, ident(), now=later)["refusal"] == \
        PCB.R_SAME_BOOK_UNLOADED
    # a CONTRADICTED window wins over everything
    ev.put([SYM], {SYM: {"status": "CONTRADICTED"}}, {}, at=now)
    assert ev.effective(SYM, ident(), now=now)["status"] == "CONTRADICTED"


def test_the_stream_not_running_here_or_the_switch_off_is_rest():
    got = PCB.consumer_read(SYM, consumer="T")
    assert got["refusal"] == PCB.R_NOT_RUNNING
    got = PCB.consumer_read(SYM, consumer="T", env={PCB.ENV_FLAG: "off"})
    assert got["refusal"] == PCB.R_OFF
    got = PCB.consumer_read("KXMLBGAME-26OCT06NYYBOS-NYY", consumer="T")
    assert got["refusal"] == PCB.R_FOREIGN


# ═════════════════════════════════════════════════════════════════════
# §2 THE PAPER OWNER
# ═════════════════════════════════════════════════════════════════════

def test_the_owner_serves_the_pmx_book_with_no_rest_dispatch(monkeypatch):
    recv = time.time() - 1.0
    live_here(monkeypatch, received_at=recv)
    rest = Rest()
    o = owner(rest)
    got = o.read(SYM, deadline_epoch_s=time.time() + 5)
    assert rest.calls == []                       # no request at all
    assert got["served_by"] == "PMX_GRPC" and got["book_source"] == "PMX_GRPC"
    assert got["observed_at"] == recv
    assert got["marketData"]["offers"][0]["px"]["value"] == "0.47"
    t = o.telemetry()
    assert t["totals"]["pmx_books"] == 1 and t["totals"]["rest_dispatches"] == 0
    bs = t["book_sources"]
    assert bs["by_consumer"][PCB.C_PAPER_OWNER]["PMX_GRPC"] == 1
    assert bs["totals"] == {"PMX_GRPC": 1, "REST": 0}
    # the PaperMarketDataClient the paper pass uses reaches it too
    monkeypatch.setattr(PMD, "OWNER", o)
    md = asyncio.run(G.PaperMarketDataClient().read_book(SYM))
    assert md["book_source"] == "PMX_GRPC" and rest.calls == []


def test_every_ineligible_read_is_rest_exactly_as_before_and_counted(
        monkeypatch):
    # (a) the stream does not run here
    rest = Rest()
    o = owner(rest)
    got = o.read(SYM)
    assert rest.calls == [SYM] and got["served_by"] == "REST"
    # (b) it runs, but this symbol has no loaded same-book evidence
    live_here(monkeypatch, evidence=False)
    got = o.read(SYM)
    assert rest.calls == [SYM, SYM] and got["served_by"] == "REST"
    # (c) a book older than the shared-read age
    PCB.EVIDENCE.put([SYM], {SYM: SUPPORTED}, {})
    monkeypatch.setattr(IS, "BOOKS", resident(received_at=time.time() - 9.0))
    got = o.read(SYM)
    assert rest.calls == [SYM, SYM, SYM] and got["served_by"] == "REST"
    owner_counts = PCB.telemetry()["by_consumer"][PCB.C_PAPER_OWNER]
    assert owner_counts["PMX_GRPC"] == 0 and owner_counts["REST"] == 3
    assert owner_counts["fallback_reasons"] == {
        PCB.R_NOT_RUNNING: 1, PCB.R_SAME_BOOK_UNLOADED: 1,
        PCB.R_RECEIPT_OLD: 1}
    # (d) the kill switch: REST, by name
    monkeypatch.setenv(PCB.ENV_FLAG, "off")
    PCB.EVIDENCE.put([SYM], {SYM: SUPPORTED}, {})
    monkeypatch.setattr(IS, "BOOKS", resident(received_at=time.time()))
    o.read(SYM)
    assert len(rest.calls) == 4
    assert PCB.telemetry()["by_consumer"][PCB.C_PAPER_OWNER][
        "fallback_reasons"][PCB.R_OFF] == 1


def test_a_reader_needing_a_book_after_its_instant_is_never_answered_earlier(
        monkeypatch):
    recv = time.time() - 1.0
    live_here(monkeypatch, received_at=recv)
    rest = Rest()
    got = owner(rest).read(SYM, not_before_epoch=recv + 0.5)
    assert rest.calls == [SYM] and got["served_by"] == "REST"


def test_the_attempt_names_a_pmx_book_and_admission_still_refuses_it(
        monkeypatch):
    from sportsassets import actual_admission as AA
    from sportsassets.agents import paper_benchmark as PB
    live_here(monkeypatch)
    rest = Rest()
    got = owner(rest).read(SYM)
    assert got["book_source"] == "PMX_GRPC" and rest.calls == []
    assert PB.book_source({"got": got, "obs": {}}) == \
        PB.PMX_STREAM_BOOK_SOURCE == "PMX_GRPC_STREAM_BOOK"
    # a REST read is named exactly as before
    assert PB.book_source({"got": {"marketData": rest_md()}}) == \
        "FRESH_VENUE_READ"
    assert PB.book_source({"reused": True, "got": got}) == \
        "REUSED_IN_THIS_EVALUATION"
    # a label only: the actual lane's admission reads the paper module's
    # own BOOK_CURRENCY for it (NOT_ESTABLISHED), exactly as for REST
    facts = PB.admission_facts(
        cand={}, pin={}, match={}, p=0.5, obs={"obs_id": 1,
                                              "observed_at": 1.0},
        md=got["marketData"], sized={}, econ=None, book_age=1.0,
        book_source=PB.book_source({"got": got}))
    assert facts["book"]["source"] == "PMX_GRPC_STREAM_BOOK"
    assert facts["book"]["book_currency"]["verdict"] == "NOT_ESTABLISHED"
    assert AA.book_currency_admission(
        facts["book"]["book_currency"])["status"] == AA.NOT_ADMISSIBLE


def test_the_public_lane_never_serves_a_pmx_book(monkeypatch):
    live_here(monkeypatch)
    seen = []

    def public(slug, *, lane=None):
        seen.append(slug)
        return {"marketData": rest_md(), "observed_at": time.time()}
    o = owner(Rest(), public_transport=public)
    o.set_held([SYM])
    got = o.read(SYM, auth=PMD.AUTH_PUBLIC,
                 deadline_epoch_s=time.time() + 5)
    assert seen == [SYM] and got["served_by"] == "PUBLIC_GATEWAY"


@pg
async def test_a_recorded_pmx_observation_names_its_feed():
    from sportsassets import bettor_paper_simulator as SIM
    conn = await H.connect()
    tx = conn.transaction()
    await tx.start()
    try:
        slug = "aec-mlb-pmx-%s-2026-10-08" % uuid.uuid4().hex[:6]
        recv = time.time() - 2.0
        rd = {"marketData": rest_md(), "observed_at": recv,
              "book_source": "PMX_GRPC", "feed": "PMX_GRPC"}
        rec = await SIM.record_book(conn, slug=slug, read=rd,
                                    source="PAPER_MARKET_DATA_CLIENT",
                                    read_basis="HELD_MARK_REFRESH")
        row = await conn.fetchrow(
            "SELECT source, read_basis, observed_at FROM "
            " paper_book_observations WHERE obs_id = $1", rec["obs_id"])
        assert row["source"] == \
            "PAPER_INSTITUTIONAL_STREAM:PAPER_MARKET_DATA_CLIENT"
        assert row["observed_at"].timestamp() == pytest.approx(recv, abs=1e-3)
        assert PMF.mark_source(dict(row)) == "INSTITUTIONAL_STREAM"
        # a REST read is recorded exactly as before
        rec2 = await SIM.record_book(conn, slug=slug, read={
            "marketData": rest_md(), "observed_at": time.time()},
            source="PAPER_MARKET_DATA_CLIENT", read_basis="HELD_MARK_REFRESH")
        row2 = await conn.fetchrow(
            "SELECT source FROM paper_book_observations WHERE obs_id = $1",
            rec2["obs_id"])
        assert row2["source"] == "PAPER_MARKET_DATA_CLIENT"
        assert PMF.mark_source(dict(row2)) == "REST"
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §3 THE COLLECTOR'S venue_quote
# ═════════════════════════════════════════════════════════════════════

def _no_rest(monkeypatch):
    calls = []

    def read(slug, *, deadline_epoch_s=None):
        calls.append(slug)
        return {"marketData": rest_md(), "observed_at": time.time()}
    monkeypatch.setattr(LOOP, "_read_book_blocking", read)
    return calls


def test_venue_quote_judges_the_pmx_book_as_a_rest_book_with_its_receipt(
        monkeypatch):
    recv = time.time() - 2.0
    live_here(monkeypatch, received_at=recv)
    calls = _no_rest(monkeypatch)
    now = time.time()
    vq = asyncio.run(LOOP.venue_quote(None, us_slug=SYM, intent=LONG,
                                      now=now, pmx_allowed=True))
    assert calls == []                              # no REST read
    assert vq["book_source"] == "PMX_GRPC"
    # the SAME currency verdict a REST book gets in production: no mechanism
    # establishes it, so it is a calibration-only read with what it showed
    assert vq["refusal"] == LOOP.R_BOOK_CURRENCY_NOT_ESTABLISHED
    clock = vq["venue_clock"]
    assert clock["book_source"] == "PMX_GRPC"
    assert clock["our_response_received_at"] == recv     # ITS receipt
    assert clock["our_processing_delay_s"] >= now - recv - 1e-3
    assert clock["pmx"]["receipt_age_s"] <= PCB.MAX_RECEIPT_AGE_S
    assert vq["displayed_not_for_orders"] is not None
    assert vq["read_at"] == recv
    assert PCB.telemetry()["by_consumer"][PCB.C_COLLECTOR]["PMX_GRPC"] == 1


def test_only_the_paper_calibration_reads_may_use_the_pmx_book(monkeypatch):
    live_here(monkeypatch)
    calls = _no_rest(monkeypatch)
    # the default: every other caller (funded pair inputs, hedge quotes,
    # the admin probe) reads REST exactly as before
    vq = asyncio.run(LOOP.venue_quote(None, us_slug=SYM, intent=LONG,
                                      now=time.time()))
    assert calls == [SYM] and vq["book_source"] == "REST"
    # the collector's own reads set the context
    tok = LOOP._PMX_ALLOWED.set(True)
    try:
        vq = asyncio.run(LOOP.venue_quote(None, us_slug=SYM, intent=LONG,
                                          now=time.time()))
    finally:
        LOOP._PMX_ALLOWED.reset(tok)
    assert calls == [SYM] and vq["book_source"] == "PMX_GRPC"
    src = inspect.getsource(LOOP.cycle)
    i = src.index("_PMX_ALLOWED.set(True)")
    assert i < src.index("vq = await venue_quote(", i)
    assert "_PMX_ALLOWED.set(True)" in inspect.getsource(
        LOOP._line_instrument)
    for f in (LOOP.funded_pair_inputs, LOOP._candidate_quote):
        assert "_PMX_ALLOWED" not in inspect.getsource(f)
    # a record priced off a PMX book is never offered to the funded lane
    i = src.index("funded = (")
    assert "R_PMX_BOOK_NOT_A_FUNDED_INPUT" in src[i:i + 300]
    assert src[i:i + 400].index("_PMX_SOURCE") < \
        src[i:i + 400].index("_funded_attempt(")


def test_an_ineligible_pmx_book_leaves_the_rest_read_unchanged(monkeypatch):
    live_here(monkeypatch, evidence=False)
    calls = _no_rest(monkeypatch)
    vq = asyncio.run(LOOP.venue_quote(None, us_slug=SYM, intent=LONG,
                                      now=time.time(), pmx_allowed=True))
    assert calls == [SYM] and vq["book_source"] == "REST"
    c = PCB.telemetry()["by_consumer"][PCB.C_COLLECTOR]
    assert c == {"PMX_GRPC": 0, "REST": 1,
                 "fallback_reasons": {PCB.R_SAME_BOOK_UNLOADED: 1}}


def test_the_cycle_counts_each_candidate_read_by_source():
    lat = {}
    LOOP._note_book_source(lat, {"book_source": "PMX_GRPC"})
    LOOP._note_book_source(lat, {"book_source": "REST"})
    LOOP._note_book_source(lat, {})               # a stand-in: REST
    assert lat["venue_book_sources"] == {"PMX_GRPC": 1, "REST": 2}
    assert LOOP._PMX_SOURCE == PCB.SOURCE_PMX
    assert LOOP._REST_SOURCE == PCB.SOURCE_REST


def test_the_heartbeat_carries_the_per_cycle_and_per_consumer_sources(
        monkeypatch):
    # `_heartbeat` persists only the digests, so a count that is not in
    # them is gone after the cycle returns
    out = {"odds_freshness": {"events_per_odds_fetch": 1},
           "latency": {"venue_book_sources": {"PMX_GRPC": 7, "REST": 3}}}
    d = LOOP._freshness_digest(out)
    assert d["venue_book_sources"] == {"PMX_GRPC": 7, "REST": 3}
    live_here(monkeypatch)
    PCB.consumer_read(SYM, consumer=PCB.C_COLLECTOR)
    md = LOOP._paper_market_data_digest()
    assert md["book_sources"]["by_consumer"][PCB.C_COLLECTOR][
        "PMX_GRPC"] == 1


# ═════════════════════════════════════════════════════════════════════
# §4 THE API STREAM: ASKED SYMBOLS, SPARE CAPACITY, PLANE REFDATA
# ═════════════════════════════════════════════════════════════════════

def _slugs(n, tag="g"):
    return ["aec-mlb-sd-mil-2026-10-03-%s%02d" % (tag, i) for i in range(n)]


def test_asked_symbols_are_never_frozen_at_the_first_32(monkeypatch):
    monkeypatch.setattr(IAS, "running", lambda: True)
    t0 = time.time()
    first = _slugs(40)
    for i, s in enumerate(first):
        IAS.request(s, now=t0 + i)
    asked = IAS.asked_symbols(now=t0 + 40)
    assert len(asked) == 40                       # the 33rd onward kept
    assert asked[0] == first[-1]                  # most recent first
    # bounded: the least recently asked leaves first, never the new ask
    more = _slugs(IAS.MAX_REQUESTED, tag="h")
    for i, s in enumerate(more):
        IAS.request(s, now=t0 + 100 + i)
    asked = IAS.asked_symbols(now=t0 + 100 + len(more))
    assert len(asked) == IAS.MAX_REQUESTED == IS.MAX_SYMBOLS
    assert set(asked) == set(more)
    # an ask not repeated for REQUEST_IDLE_S leaves
    later = t0 + 100 + len(more) + IAS.REQUEST_IDLE_S + 1
    IAS.request("aec-mlb-sd-mil-2026-10-03-fresh", now=later)
    assert IAS.asked_symbols(now=later) == ["aec-mlb-sd-mil-2026-10-03-fresh"]


def test_the_new_bounds_are_the_streams_own_never_new_numbers():
    from sportsassets.market_plane import refdata_universe as RU
    # the per-process total and the idle age are institutional_stream's
    assert IAS.STREAM_MAX_SYMBOLS == IS.MAX_SYMBOLS == 200
    assert IAS.STREAM_MAX_SYMBOLS == IAS.MAX_SYMBOLS + IAS.HELD_SYMBOL_BUDGET
    assert IAS.MAX_REQUESTED == IAS.STREAM_MAX_SYMBOLS
    assert IAS.REQUEST_IDLE_S == IS.RETAIN_IDLE_S == 1800.0
    # the plane-record age a spare symbol may use is the plane's own cache
    assert IAS.PLANE_REFDATA_MAX_AGE_S == RU.FULL_REFRESH_S == 24 * 3600.0
    # and the existing bounds are unchanged
    assert (IAS.MAX_SYMBOLS, IAS.HELD_SYMBOL_BUDGET, IAS.REFDATA_REFRESH_S,
            IAS.RETRY_UNLISTED_S, IAS.BOOTSTRAPS_PER_PASS) == (
                32, 168, 3600.0, 300.0, 24)


def _plane_for(symbols, at):
    async def plane(asked):
        return {s: {"record": rec_for(s), "at": at}
                for s in asked if s in set(symbols)}
    return plane


def test_asked_symbols_fill_the_spare_capacity_after_held_markets(
        monkeypatch):
    monkeypatch.setattr(IAS, "running", lambda: True)
    IS.BOOKS.set_state(IS.S_IDLE, "test")
    focus = _slugs(30, tag="f")
    held = _slugs(3, tag="k")
    asked = _slugs(60, tag="a")
    now = time.time()
    for i, s in enumerate(asked):
        IAS.request(s, now=now - 60 + i)
    reads = []

    def boot(client, s):
        reads.append(s)
        return {"record": rec_for(s)}
    got = asyncio.run(IAS.refresh_once(
        symbols=focus, held=held, bootstrap=boot, now=now,
        max_bootstraps=500,
        plane_records=_plane_for(asked, now - 5 * 3600)))
    wanted = IS.BOOKS.wanted()
    assert set(held) <= set(wanted) and set(focus) <= set(wanted)
    assert set(asked) <= set(wanted)              # base: 2 of 60
    assert got["asked_spare"] == 58
    assert len(wanted) <= IS.MAX_SYMBOLS
    # the spare capacity added NO REST refdata read: the 58 spare symbols
    # took the plane's record (5 h old, inside its 24 h cache); the 2 asked
    # symbols in the core set, the focus set and the held markets are read
    # exactly as before (their plane record is older than REFDATA_REFRESH_S)
    spare = set(asked[:58])                       # the least recent 58
    assert not spare & set(reads)
    assert set(reads) == set(focus) | set(held) | set(asked[58:])
    assert IAS._STATE["refdata_from_plane"] == 58
    assert IAS.primary_report()["asked_spare"] == 58


def test_the_spare_capacity_never_displaces_a_held_market(monkeypatch):
    monkeypatch.setattr(IAS, "running", lambda: True)
    IS.BOOKS.set_state(IS.S_IDLE, "test")
    focus = _slugs(32, tag="f")
    held = _slugs(IAS.HELD_SYMBOL_BUDGET, tag="k")
    now = time.time()
    asked = _slugs(50, tag="a")
    for i, s in enumerate(asked):
        IAS.request(s, now=now - 60 + i)
    got = asyncio.run(IAS.refresh_once(
        symbols=focus, held=held, bootstrap=lambda c, s: {
            "record": rec_for(s)}, now=now, max_bootstraps=1000,
        plane_records=_plane_for(asked, now - 60)))
    assert got["asked_spare"] == 0
    assert set(held) <= set(IS.BOOKS.wanted())
    assert len(IS.BOOKS.wanted()) == IS.MAX_SYMBOLS


def test_a_spare_symbol_the_plane_does_not_hold_is_never_a_rest_read(
        monkeypatch):
    monkeypatch.setattr(IAS, "running", lambda: True)
    IS.BOOKS.set_state(IS.S_IDLE, "test")
    focus = _slugs(32, tag="f")
    asked = _slugs(3, tag="a")
    now = time.time()
    for i, s in enumerate(asked):
        IAS.request(s, now=now - 10 + i)
    reads, plane_asks = [], []

    def boot(client, s):
        reads.append(s)
        return {"record": rec_for(s)}

    async def plane(syms):
        plane_asks.append(list(syms))
        # one record past the plane's own cache life, one naming another
        # symbol, one absent
        return {asked[0]: {"record": rec_for(asked[0]),
                           "at": now - IAS.PLANE_REFDATA_MAX_AGE_S - 1},
                asked[1]: {"record": rec_for(asked[2]), "at": now - 60}}
    got = asyncio.run(IAS.refresh_once(symbols=focus, held=[], bootstrap=boot,
                                       now=now, max_bootstraps=500,
                                       plane_records=plane))
    assert got["asked_spare"] == 3
    assert not set(asked) & set(reads)            # never REST
    assert not set(asked) & set(IS.BOOKS.wanted())  # never subscribed
    assert IAS._STATE["refdata_from_plane"] == 0
    # re-asked of the plane only after RETRY_UNLISTED_S...
    assert IAS.pending(now + 60) == []
    asyncio.run(IAS.refresh_once(symbols=focus, held=[], bootstrap=boot,
                                 now=now + 60, plane_records=plane))
    assert not set(asked) & set(sum(plane_asks[1:], []))
    assert set(IAS.pending(now + IAS.RETRY_UNLISTED_S + 1)) == set(asked)
    # ...and a plane-only miss never delays the REST bootstrap of a symbol
    # that later fits the core set
    reads.clear()
    asyncio.run(IAS.refresh_once(symbols=focus[:31], held=[], bootstrap=boot,
                                 now=now + 61, plane_records=plane))
    assert reads == [asked[-1]]                   # the most recent ask
    assert asked[-1] in IS.BOOKS.wanted()


def test_refdata_the_plane_persisted_recently_needs_no_rest_read(
        monkeypatch):
    monkeypatch.setattr(IAS, "running", lambda: True)
    IS.BOOKS.set_state(IS.S_IDLE, "test")
    fresh, old, wrong, absent = _slugs(4, tag="p")
    now = time.time()
    reads = []

    async def plane(symbols):
        return {fresh: {"record": rec_for(fresh), "at": now - 600},
                old: {"record": rec_for(old),
                      "at": now - IAS.REFDATA_REFRESH_S - 5},
                wrong: {"record": rec_for(absent), "at": now - 60}}

    def boot(client, s):
        reads.append(s)
        return {"record": rec_for(s)}
    got = asyncio.run(IAS.refresh_once(symbols=[fresh, old, wrong, absent],
                                       bootstrap=boot, now=now,
                                       plane_records=plane))
    # the plane's recent, exact record: no REST read; a record older than
    # this process's own REFDATA_REFRESH_S, a record naming another symbol
    # and an absent one: REST, as before (core symbols never take the
    # plane's 24 h cache life)
    assert sorted(reads) == sorted([old, wrong, absent])
    assert IAS.REFDATA[fresh]["at"] == now - 600   # its own age
    assert IAS.REFDATA[fresh]["source"] == "MARKET_PLANE_REGISTRY"
    assert IAS._STATE["refdata_from_plane"] == 1
    assert fresh in IS.BOOKS.wanted() and got["subscribed"] == 4
    assert IAS.primary_report()["refdata_from_plane"] == 1
    # re-asked only when the PLANE's record is due
    assert IAS._due(fresh, now + 60) is False
    assert IAS._due(fresh, now - 600 + IAS.REFDATA_REFRESH_S + 1) is True


def test_no_plane_reader_is_the_rest_bootstrap_exactly_as_before(
        monkeypatch):
    monkeypatch.setattr(IAS, "running", lambda: True)
    IS.BOOKS.set_state(IS.S_IDLE, "test")
    focus = _slugs(4, tag="f")
    reads = []

    def boot(client, s):
        reads.append(s)
        return {"record": rec_for(s)}

    async def broken(symbols):
        raise RuntimeError("plane down")
    for pr in (None, broken):
        IAS.reset()
        reads.clear()
        got = asyncio.run(IAS.refresh_once(symbols=focus, held=[],
                                           bootstrap=boot, now=time.time(),
                                           plane_records=pr))
        assert reads == focus and got["subscribed"] == 4
        assert IAS._STATE["refdata_from_plane"] == 0


@pg
async def test_the_plane_refdata_reader_takes_only_recent_listed_records():
    import asyncpg
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=2)
    tag = uuid.uuid4().hex[:6]
    recent, older, stale, unlisted = ("aec-mlb-rf%s-%s-2026-10-08" % (k, tag)
                                      for k in ("a", "b", "c", "d"))
    try:
        async with pool.acquire() as c:
            for s, age, rd in (
                    (recent, 60, dict(AEC, symbol=recent)),
                    (older, IAS.REFDATA_REFRESH_S + 60,
                     dict(AEC, symbol=older)),
                    (stale, IAS.PLANE_REFDATA_MAX_AGE_S + 60,
                     dict(AEC, symbol=stale)),
                    (unlisted, 60, {"unlisted": True})):
                await c.execute(
                    "INSERT INTO market_plane_registry (contract_id, venue, "
                    " refdata, refdata_at, updated_at) VALUES ($1, "
                    " 'POLYMARKET_US', $2::jsonb, "
                    " now() - make_interval(secs => $3), now()) "
                    "ON CONFLICT (contract_id) DO UPDATE SET "
                    " refdata = EXCLUDED.refdata, "
                    " refdata_at = EXCLUDED.refdata_at",
                    s, json.dumps(rd), float(age))

        async def get_pool():
            return pool
        syms = [recent, older, stale, unlisted, "aec-not-there"]
        got = await IAS.plane_refdata(get_pool, syms)
        # default: the plane's own cache life (24 h)
        assert set(got) == {recent, older}
        assert got[recent]["record"]["symbol"] == recent
        assert time.time() - got[recent]["at"] == pytest.approx(60, abs=30)
        got = await IAS.plane_refdata(get_pool, syms,
                                      max_age_s=IAS.REFDATA_REFRESH_S)
        assert set(got) == {recent}
        assert await IAS.plane_refdata(None, [recent]) == {}
    finally:
        async with pool.acquire() as c:
            await c.execute("DELETE FROM market_plane_registry WHERE "
                            " contract_id = ANY($1::text[])",
                            [recent, older, stale, unlisted])
        await pool.close()


def test_every_consumer_read_refreshes_its_ask(monkeypatch):
    live_here(monkeypatch)
    seen = []
    monkeypatch.setattr(IAS, "request", lambda s, **k: seen.append(s))
    PCB.consumer_read(SYM, consumer="T")
    # a symbol whose refdata this process holds: the identity mapper alone
    # would never ask again (it asks only for a missing record), so it
    # would idle out of the stream while consumers keep reading it
    assert seen == [SYM]
    PCB.consumer_read("aec-mlb-sd-mil-2026-10-03-x01", consumer="T")
    assert seen[0] == SYM and "aec-mlb-sd-mil-2026-10-03-x01" in seen[1:]


# ═════════════════════════════════════════════════════════════════════
# §5 SAME-BOOK EVIDENCE ON EVERY CONSUMER READ OF AN EXACT SYMBOL
# ═════════════════════════════════════════════════════════════════════

def _tap(books):
    return PMD.SameBookTap(eligible=lambda s: True, current=books.current,
                           record_for=lambda s: rec_for(s))


def _retail_like(slug, books):
    """The retail read of the same book state, at the stream's venue
    instant (a same-instant, agreeing sample)."""
    cur = books.current(slug)
    md = rest_md()
    md["transactTime"] = cur["evidence"]["snapshot"]["venue_ts"]
    return md


def test_a_non_held_consumer_read_of_an_exact_symbol_is_a_sample():
    b = resident(received_at=time.time())
    tap = _tap(b)
    calls = []

    def rest(slug, deadline_epoch_s=None):
        calls.append(slug)
        return {"marketData": _retail_like(slug, b), "observed_at": time.time(),
                "marker": "the-read"}
    o = PMD.Owner(transport=rest, recent=lambda s, max_age_s: None,
                  gate=lambda: {"blocking": False}, same_book_tap=tap,
                  pmx_source=False)
    assert not o.is_held(SYM)
    got = o.read(SYM, lane_name=PMD.LANE_DISCOVERY)
    assert calls == [SYM] and got["marker"] == "the-read"   # unchanged
    rows = tap.drain()
    assert len(rows) == 1
    assert rows[0]["focus_why"] == PMD.TAP_WHY_CONSUMER
    assert rows[0]["verdict"] == SB.V_AGREE
    # a held read keeps its own why
    o.set_held([SYM])
    o.read(SYM)
    assert tap.drain()[0]["focus_why"] == PMD.TAP_WHY


def test_the_collector_read_is_a_sample_when_the_stream_holds_it(monkeypatch):
    b = live_here(monkeypatch, evidence=False)
    calls = []

    def read(slug, *, deadline_epoch_s=None):
        calls.append(slug)
        return {"marketData": _retail_like(slug, b),
                "observed_at": time.time()}
    monkeypatch.setattr(LOOP, "_read_book_blocking", read)
    vq = asyncio.run(LOOP.venue_quote(None, us_slug=SYM, intent=LONG,
                                      now=time.time(), pmx_allowed=True))
    assert calls == [SYM] and vq["book_source"] == "REST"
    rows = PMD.default_same_book_tap().drain()
    assert [r["focus_why"] for r in rows] == [PMD.TAP_WHY_CONSUMER]
    # nothing tapped where the stream does not hold the slug exactly
    out = PMD.tapped("aec-not-held-2026-10-08", lambda: {"x": 1})
    assert out == {"x": 1} and PMD.default_same_book_tap().drain() == []


# ═════════════════════════════════════════════════════════════════════
# §6 NO ORDER PATH, NO PROBABILITY AUTHORITY
# ═════════════════════════════════════════════════════════════════════

def test_the_consumer_rule_reaches_no_order_path_or_probability_source():
    src = inspect.getsource(PCB)
    for bad in ("submit", "place_order", "cancel_order", "bettor_funded",
                "live_executor", "pinnapi_primary", "pinnapi_feed",
                "requests.", "httpx"):
        assert bad not in src, bad

"""P5_LIVE_STREAM_BOOK_V1: THE VERSIONED LIVE BOOK-CURRENTNESS RULE (pure).

Fakes only: the evidence is produced by the REAL in-memory
`institutional_stream.ResidentBooks` driven with a fake clock and fake
updates (no network, no grpcio), so the rule is tested against the exact
shape `institutional_stream.current()` returns.

  §1  the document: hash deterministic and pinned, bounds in the text, the
      evidence split (venue_guaranteed quoted with URLs / locally_bounded /
      not_established), no authority keys
  §2  every component: fresh ok; stale receipt; venue ts skew (late and
      ahead); backwards ts; gap after reconnect until a new complete book; no
      epoch; market closed; identity unmapped / mismatched; missing ts;
      priced from another book; the stream's own refusals classified
  §3  the admission record and the verdict-age bound at submit
  §4  the paper decision path (through decision_hooks.LIVE_BOOK_EVIDENCE,
      installed by execution_intent.start): a stream-evaluated verdict is
      admission_facts.book.book_currency; no mapper -> NOT_ESTABLISHED
      IDENTITY_MAPPING_NOT_ESTABLISHED, no stream read, the REST path's label
      stands; the paper label and the REST price source are unchanged
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from sportsassets import actual_admission as AA
from sportsassets import institutional_stream as IS
from sportsassets import live_book_currency as LBC
from sportsassets import live_book_evidence as LBE
from sportsassets.agents import paper_benchmark as PB

SYM = "aec-mlb-nyy-bos-2026-10-04"
SLUG = "aec-mlb-nyy-bos-2026-10-04"
REC = {"symbol": SYM, "priceScale": "1000", "fractionalQtyScale": "100",
       "state": "INSTRUMENT_STATE_OPEN", "productId": "p1"}
T0 = 1_800_000_000.0
EXACT = {"status": "EXACT", "symbol": SYM, "binding_sha": "b" * 16}

#: PINNED. A change to the rule text is a NEW VERSION (new artifact row,
#: new migration), never an edit of V1.
PINNED_SHA256 = (
    "b2a49354b08b50d4afa5246cfa153e4df590f9c1623b3fa9ccae5a3b343b564a")


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def ts(offset=0.0):
    return datetime.fromtimestamp(T0 + offset, tz=timezone.utc)


def upd(*, t=0.0, state="INSTRUMENT_STATE_OPEN", bids=((450, 1000),),
        offers=((470, 500),)):
    return {"symbol": SYM, "bids": list(bids), "offers": list(offers),
            "state": state, "transact_time": None if t is None else ts(t),
            "book_hidden": False}


def books(clock, rec=REC):
    b = IS.ResidentBooks(clock=clock)
    b.set_state(IS.S_IDLE, "test")
    if rec is not None:
        b.set_instrument(SYM, rec)
    b.want([SYM])
    return b


def fresh():
    """A connected stream with one complete book received 0.3 s ago, stamped
    by the venue 0.1 s before our receipt."""
    c = Clock()
    b = books(c)
    b.on_connected("conn-A")
    c.t = T0 + 0.1
    b.on_update(upd(t=0.0))
    c.t = T0 + 0.4
    return c, b


def stream_priced(read):
    s = read["evidence"]["snapshot"]
    return {"source": "STREAM",
            "connection_epoch": read["evidence"]["connection"]["seq"],
            "received_at": s["received_at"]}


def run(c, b, *, identity=EXACT, priced="STREAM"):
    read = b.current(SYM, now=c.t)
    pf = stream_priced(read) if priced == "STREAM" else priced
    return LBC.evaluate(stream_read=read, identity=identity, now=c.t,
                        us_market_slug=SLUG, priced_from=pf)


def comp(v, cid):
    return next(x for x in v["components"] if x["component"] == cid)


# ═════════════════════════════════════════════════════════════════════
# §1 THE DOCUMENT
# ═════════════════════════════════════════════════════════════════════

def test_the_rule_text_hash_is_deterministic_and_pinned():
    assert LBC.RULE_ID == "P5_LIVE_STREAM_BOOK_V1" and LBC.VERSION == "1"
    assert LBC.SHA256 == PINNED_SHA256
    text = LBC.canonical_json(LBC.DOCUMENT)
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == PINNED_SHA256
    rev = json.loads(text, object_pairs_hook=lambda kv: dict(reversed(kv)))
    assert LBC.sha256_of(rev) == PINNED_SHA256
    other = LBC.document()
    other["bounds"]["max_receipt_age_s"] = 2.5
    assert LBC.sha256_of(other) != PINNED_SHA256
    assert LBC.CODE_RULES == {LBC.RULE_ID: {"1": PINNED_SHA256}}


def test_the_bounds_are_conservative_and_part_of_the_versioned_text():
    b = LBC.DOCUMENT["bounds"]
    assert b["max_receipt_age_s"] == LBC.MAX_RECEIPT_AGE_S == 2.0
    assert b["max_venue_receipt_skew_s"] == LBC.MAX_VENUE_RECEIPT_SKEW_S == 2.0
    assert b["max_verdict_age_at_actual_submit_s"] == 2.0
    assert b["tradable_instrument_states"] == ["INSTRUMENT_STATE_OPEN"]
    # tighter than every existing receipt / liveness / snapshot bound
    assert LBC.MAX_RECEIPT_AGE_S < PB.BOOK_MAX_AGE_S
    assert LBC.MAX_RECEIPT_AGE_S < IS.MAX_SILENCE_S < IS.MAX_SNAPSHOT_AGE_S
    assert "2 s receipt age" in LBC.DOCUMENT["bounds_rationale"]


def test_the_evidence_split_quotes_the_venue_and_names_what_is_not_bounded():
    sp = LBC.DOCUMENT["evidence_split"]
    vg, lb, ne = sp["venue_guaranteed"], sp["locally_bounded"], sp["not_established"]
    for item in vg:
        for q in item["quotes"]:
            assert q["url"].startswith("https://docs.polymarket.us/")
            assert q["quote"]
    assert any(q["quote"] == "Server timestamp of update"
               for x in vg for q in x["quotes"])
    ids = {x["id"] for x in vg} | {x["id"] for x in lb} | {x["id"] for x in ne}
    for need in ("VG6_NO_SEQUENCE_NUMBER", "LB3_RECEIPT_AGE",
                 "NE1_UNDELIVERED_CHANGES", "NE2_DEBOUNCING_AND_COALESCING",
                 "NE3_QUIET_BOOK_STALENESS", "NE4_TRANSACT_TIME_EVENT"):
        assert need in ids
    # nothing locally bounded is claimed as a venue guarantee
    assert not ({x["id"] for x in vg} & {x["id"] for x in lb})
    assert LBC.DOCUMENT["sequence"] == "SEQUENCE_NOT_PROVIDED_BY_VENUE"
    assert "does NOT meet the activating documentation D1-D4" in \
        LBC.DOCUMENT["relation_to_review"]


def test_the_rule_grants_no_authority():
    from sportsassets.agents import xavier_small_live_policy as XSP
    assert not (set(LBC.DOCUMENT) & set(XSP.FORBIDDEN_TOP_LEVEL_KEYS))
    assert "status" not in LBC.DOCUMENT
    assert LBC.DOCUMENT["authority_granted"] == "NONE"
    assert LBC.artifact()["status"] == "READY_FOR_OWNER_APPROVAL"


def test_production_still_approves_no_live_rule_in_code():
    assert AA.APPROVED_LIVE_BOOK_RULES == frozenset()


# ═════════════════════════════════════════════════════════════════════
# §2 EVERY COMPONENT
# ═════════════════════════════════════════════════════════════════════

def test_a_fresh_complete_book_on_the_live_epoch_is_established():
    c, b = fresh()
    v = run(c, b)
    assert v["verdict"] == LBC.ESTABLISHED, v["failed_components"]
    assert v["stream_book_verdict"] == LBC.ESTABLISHED
    assert v["failed_components"] == []
    assert v["subscription_state"] == "RUNNING"
    assert v["connection_epoch"] == 1 and v["book_epoch"] == 1
    assert v["receipt_age_s"] == pytest.approx(0.3)
    assert v["venue_ts_age_s"] == pytest.approx(0.4)
    assert v["venue_receipt_skew_s"] == pytest.approx(0.1)
    assert v["gap_since_snapshot"] is False
    assert v["venue_sequence"] == "SEQUENCE_NOT_PROVIDED_BY_VENUE"
    s = comp(v, "C5_SEQUENCE_INTEGRITY")
    assert s["passed"] and s["status"] == "SEQUENCE_NOT_PROVIDED_BY_VENUE"
    assert [x["component"] for x in v["components"]] == [
        "C1_IDENTITY_EXACT", "C2_STREAM_RUNNING", "C3_CONNECTION_EPOCH_ALIVE",
        "C4_COMPLETE_BOOK_ON_THIS_EPOCH", "C5_SEQUENCE_INTEGRITY",
        "C6_VENUE_TS_PRESENT", "C7_VENUE_TS_MONOTONIC_IN_EPOCH",
        "C8_RECEIPT_AGE", "C9_VENUE_RECEIPT_SKEW", "C10_MARKET_OPEN",
        "C11_STREAM_BOOK_CURRENT", "C12_PRICED_FROM_THIS_BOOK"]
    assert v["rule"] == "P5_LIVE_STREAM_BOOK_V1"
    assert v["rule_sha256"] == PINNED_SHA256


def test_a_receipt_older_than_two_seconds_is_stale():
    c, b = fresh()
    c.t = T0 + 0.1 + 2.01
    v = run(c, b)
    assert v["verdict"] == LBC.STALE and v["reason"] == LBC.R_RECEIPT_OLD
    assert comp(v, "C8_RECEIPT_AGE")["receipt_age_s"] == pytest.approx(2.01)
    # exactly at the bound still passes
    c.t = T0 + 0.1 + 2.0
    assert run(c, b)["verdict"] == LBC.ESTABLISHED


def test_a_quiet_book_is_stale_even_while_heartbeats_arrive():
    """No heartbeat interval is documented: a heartbeat keeps the CONNECTION
    alive but never refreshes a book (NE3)."""
    c, b = fresh()
    for _ in range(5):
        c.t += 1.0
        b.on_heartbeat()
    v = run(c, b)
    assert v["verdict"] == LBC.STALE and v["reason"] == LBC.R_RECEIPT_OLD


def test_venue_timestamp_far_behind_receipt_is_stale():
    c = Clock()
    b = books(c)
    b.on_connected()
    c.t = T0 + 2.5
    b.on_update(upd(t=0.0))                 # stamped 2.5 s before our receipt
    c.t = T0 + 2.6
    v = run(c, b)
    assert v["verdict"] == LBC.STALE and v["reason"] == LBC.R_SKEW_LATE
    assert comp(v, "C9_VENUE_RECEIPT_SKEW")["venue_receipt_skew_s"] == \
        pytest.approx(2.5)


def test_venue_timestamp_far_ahead_of_receipt_is_not_established():
    c = Clock()
    b = books(c)
    b.on_connected()
    c.t = T0 + 0.1
    b.on_update(upd(t=3.0))                 # venue clock 2.9 s in our future
    c.t = T0 + 0.2
    v = run(c, b)
    assert v["verdict"] == LBC.NOT_ESTABLISHED
    assert v["reason"] == LBC.R_SKEW_AHEAD


def test_a_backwards_venue_timestamp_is_a_gap_until_order_is_restored():
    c, b = fresh()
    b.on_update(upd(t=1.0))
    c.t = T0 + 1.2
    b.on_update(upd(t=0.5))                 # went backwards within the epoch
    v = run(c, b)
    assert v["verdict"] == LBC.GAP
    assert not comp(v, "C7_VENUE_TS_MONOTONIC_IN_EPOCH")["passed"]
    assert v["gap_since_snapshot"] is True
    b.on_update(upd(t=1.1))                 # at/past the high-water mark
    assert run(c, b)["verdict"] == LBC.ESTABLISHED


def test_after_reconnect_the_book_is_a_gap_until_a_new_complete_book():
    c, b = fresh()
    b.on_disconnected("reset")
    v = run(c, b)
    assert v["verdict"] == LBC.GAP and v["reason"] == LBC.R_EPOCH_LOST
    assert v["subscription_state"] != "RUNNING"
    b.on_connected("conn-B")                # epoch 2, no book yet on it
    c.t += 0.1
    v = run(c, b)
    assert v["verdict"] == LBC.GAP and v["reason"] == LBC.R_GAP_RECONNECT
    assert v["connection_epoch"] == 2 and v["book_epoch"] == 1
    assert v["gap_since_snapshot"] is True
    b.on_update(upd(t=c.t - T0 - 0.05))     # the new complete book
    c.t += 0.2
    v = run(c, b)
    assert v["verdict"] == LBC.ESTABLISHED and v["book_epoch"] == 2


def test_no_connection_epoch_is_not_established():
    c = Clock()
    b = books(c)                            # never connected
    v = run(c, b, priced={"source": "STREAM"})
    assert v["verdict"] == LBC.NOT_ESTABLISHED
    assert v["reason"] == LBC.R_NO_EPOCH
    # connected, subscribed, no complete book yet
    b.on_connected()
    v = run(c, b, priced={"source": "STREAM"})
    assert v["verdict"] == LBC.NOT_ESTABLISHED
    assert v["reason"] == LBC.R_SNAPSHOT_PENDING


def test_a_closed_market_is_refused():
    c = Clock()
    b = books(c)
    b.on_connected()
    c.t = T0 + 0.1
    b.on_update(upd(t=0.0, state="INSTRUMENT_STATE_HALTED"))
    v = run(c, b)
    assert v["verdict"] == LBC.REFUSED and v["reason"] == LBC.R_NOT_OPEN
    assert v["market_state"] == "INSTRUMENT_STATE_HALTED"


def test_an_unknown_market_state_is_not_established():
    c = Clock()
    b = books(c, rec=dict(REC, state=None))
    b.on_connected()
    c.t = T0 + 0.1
    b.on_update(upd(t=0.0, state=None))
    v = run(c, b)
    assert v["verdict"] == LBC.NOT_ESTABLISHED
    assert v["reason"] == LBC.R_STATE_UNKNOWN


def test_an_unmapped_identity_is_not_established_and_a_wrong_one_refused():
    c, b = fresh()
    for ident in (None, {}, {"status": "NOT_ESTABLISHED"},
                  {"status": "EXACT"}):
        v = run(c, b, identity=ident)
        assert v["verdict"] == LBC.NOT_ESTABLISHED
        assert v["reason"] == LBC.R_IDENTITY_UNMAPPED
    for ident in ({"status": "EXACT", "symbol": "aec-other-symbol"},
                  {"status": "AMBIGUOUS", "symbol": SYM}):
        v = run(c, b, identity=ident)
        assert v["verdict"] == LBC.REFUSED
        assert v["reason"] == LBC.R_IDENTITY_MISMATCH
    v = LBC.not_evaluated(reason=LBC.R_IDENTITY_UNMAPPED, now=c.t,
                          us_market_slug=SLUG)
    assert v["verdict"] == LBC.NOT_ESTABLISHED
    assert v["reason"] == "IDENTITY_MAPPING_NOT_ESTABLISHED"


def test_a_missing_venue_timestamp_is_not_established():
    c = Clock()
    b = books(c)
    b.on_connected()
    c.t = T0 + 0.1
    b.on_update(upd(t=None))
    v = run(c, b)
    assert v["verdict"] == LBC.NOT_ESTABLISHED
    assert v["reason"] == LBC.R_NO_VENUE_TS
    assert not comp(v, "C6_VENUE_TS_PRESENT")["passed"]


def test_a_price_from_another_book_is_never_established():
    c, b = fresh()
    for pf in (None, {"source": "REST_PAPER_BOOK", "obs_id": 7},
               {"source": "STREAM", "connection_epoch": 1,
                "received_at": T0 - 5.0}):
        v = run(c, b, priced=pf)
        assert v["verdict"] == LBC.NOT_ESTABLISHED
        assert v["reason"] in (LBC.R_PRICE_NOT_STATED, LBC.R_PRICE_OTHER_BOOK)
        assert v["stream_book_verdict"] == LBC.ESTABLISHED


def test_the_streams_own_refusals_are_classified_never_ignored():
    c, b = fresh()
    b.set_state(IS.S_STOPPED, "stopped")
    v = run(c, b)
    assert v["verdict"] == LBC.NOT_ESTABLISHED
    assert comp(v, "C11_STREAM_BOOK_CURRENT")["stream_refusal"] == \
        IS.R_NOT_RUNNING
    c, b = fresh()
    b.on_refused("PERMISSION_DENIED")
    assert run(c, b)["verdict"] == LBC.REFUSED
    # crossed book: every other component passes, the stream refuses
    c = Clock()
    b = books(c)
    b.on_connected()
    c.t = T0 + 0.1
    b.on_update(upd(t=0.0, bids=((480, 1),), offers=((470, 1),)))
    v = run(c, b)
    assert v["verdict"] == LBC.REFUSED
    assert v["failed_components"] == ["C11_STREAM_BOOK_CURRENT"]
    # every stream refusal code is classified
    for name in dir(IS):
        if name.startswith("R_") and getattr(IS, name):
            assert getattr(IS, name) in LBC.STREAM_REFUSAL_CLASS, name


def test_garbage_in_fails_closed():
    for read in (None, {}, {"ok": True}, {"ok": True, "evidence": "x"},
                 {"ok": True, "book": {}, "evidence": {"connection": 5}}):
        v = LBC.evaluate(stream_read=read, identity=EXACT, now=T0,
                         priced_from={"source": "STREAM"})
        assert v["verdict"] != LBC.ESTABLISHED


# ═════════════════════════════════════════════════════════════════════
# §3 THE ADMISSION RECORD AND THE VERDICT AGE AT SUBMIT
# ═════════════════════════════════════════════════════════════════════

def test_the_admission_record_carries_the_evidence_and_split_summary():
    c, b = fresh()
    rec = LBC.admission_record(run(c, b))
    for k in ("verdict", "rule", "subscription_state", "connection_epoch",
              "receipt_age_s", "venue_ts_age_s", "gap_since_snapshot",
              "venue_guaranteed", "locally_bounded", "not_established",
              "rule_sha256", "evaluated_at"):
        assert k in rec, k
    assert rec["verdict"] == "ESTABLISHED" and rec["rule"] == LBC.RULE_ID
    assert "NE1_UNDELIVERED_CHANGES" in rec["not_established"]
    # under the code constant (empty) an ESTABLISHED live verdict is refused
    got = AA.book_currency_admission(rec)
    assert got["status"] == AA.NOT_ADMISSIBLE
    assert AA.book_currency_admission(
        rec, approved={LBC.RULE_ID})["status"] == AA.LIVE_ADMISSIBLE


def test_the_verdict_age_at_submit_is_bounded():
    rec = {"rule": LBC.RULE_ID, "evaluated_at": T0}
    assert LBC.verdict_age_refusal(rec, now=T0 + 1.9) is None
    got = LBC.verdict_age_refusal(rec, now=T0 + 2.1)
    assert got["refusal"] == LBC.R_VERDICT_STALE
    assert LBC.verdict_age_refusal({"rule": LBC.RULE_ID}, now=T0)["refusal"] \
        == LBC.R_VERDICT_STALE
    assert LBC.verdict_age_refusal({"rule": "OTHER"}, now=T0 + 99) is None


# ═════════════════════════════════════════════════════════════════════
# §4 THE PAPER DECISION PATH
# ═════════════════════════════════════════════════════════════════════

_CAND = {"us_market_slug": SLUG, "side": "ORDER_INTENT_BUY_LONG",
         "settlement": {}}
_OBS = {"obs_id": 11, "observed_at": T0}


def _facts(live):
    return PB.admission_facts(
        cand=_CAND, pin={"qualified": True, "age_s": 1, "limit_s": 30},
        match={"checks": []}, p=0.6, obs=_OBS, md=None, sized={},
        econ=None, book_age=0.1, live_book=live)


@pytest.fixture
def hooks(monkeypatch):
    from sportsassets import decision_hooks as DH
    monkeypatch.setattr(DH, "LIVE_BOOK_EVIDENCE", LBE.for_decision)
    monkeypatch.setattr(LBE, "IDENTITY_MAPPER", None)
    monkeypatch.setattr(LBE, "READER", None)
    return DH


def test_the_paper_module_never_imports_the_live_modules():
    import pathlib
    src = pathlib.Path(PB.__file__).read_text()
    assert "live_book_currency" not in src.replace(
        '"live_book_currency"', "")
    assert "institutional_stream" not in src
    assert "import live" not in src and "live_book_evidence as" not in src


def test_without_a_mapper_the_stream_is_not_read_and_the_rest_path_stands(
        hooks):
    calls = []
    v = PB.live_book_evidence(
        {"live_book_reader": lambda *a, **k: calls.append(a)}, _CAND,
        obs=_OBS, now=T0)
    assert calls == []
    assert v["verdict"] == "NOT_ESTABLISHED" and v["stream_read"] is False
    assert v["reason"] == "IDENTITY_MAPPING_NOT_ESTABLISHED"
    assert v["rule"] == "P5_LIVE_STREAM_BOOK_V1"
    f = _facts(v)
    # the REST path's label stands; the live record is kept beside it
    assert f["book"]["book_currency"] == PB.BOOK_CURRENCY
    assert f["book"]["live_book_currency"]["reason"] == \
        "IDENTITY_MAPPING_NOT_ESTABLISHED"
    assert PB.BOOK_CURRENCY["verdict"] == "NOT_ESTABLISHED"
    got = AA.evaluate(f, slug=SLUG, approved_book_rules={LBC.RULE_ID})
    assert AA.R_BOOK_CURRENCY in got["refusals"]


def test_no_installed_hook_records_nothing_new(monkeypatch):
    from sportsassets import decision_hooks as DH
    monkeypatch.setattr(DH, "LIVE_BOOK_EVIDENCE", None)
    assert PB.live_book_evidence({}, _CAND, obs=_OBS, now=T0) is None
    f = _facts(None)
    assert f["book"]["book_currency"] == PB.BOOK_CURRENCY
    assert "paper_book_currency" not in f["book"]
    assert "live_book_currency" not in f["book"]


def test_with_an_exact_mapper_the_stream_verdict_is_the_book_currency_but_rest_pricing_never_establishes(
        hooks, monkeypatch):
    c, b = fresh()
    seen = []

    def mapper(slug, intent):
        seen.append((slug, intent))
        return dict(EXACT)

    monkeypatch.setattr(LBE, "IDENTITY_MAPPER", mapper)
    monkeypatch.setattr(LBE, "READER", lambda s, now=None: b.current(s, now=now))
    v = PB.live_book_evidence({}, _CAND, obs=_OBS, now=c.t)
    assert seen == [(SLUG, "ORDER_INTENT_BUY_LONG")]
    assert v["stream_read"] is True
    assert v["stream_book_verdict"] == "ESTABLISHED"
    assert v["verdict"] == "NOT_ESTABLISHED"
    assert v["reason"] == LBC.R_PRICE_OTHER_BOOK
    f = _facts(v)
    bc = f["book"]["book_currency"]
    assert bc["rule"] == LBC.RULE_ID and bc["connection_epoch"] == 1
    assert bc["receipt_age_s"] == pytest.approx(0.3)
    assert bc["subscription_state"] == "RUNNING"
    assert bc["gap_since_snapshot"] is False
    assert f["book"]["paper_book_currency"] == PB.BOOK_CURRENCY
    # even with the rule approved, REST-priced facts are not admissible
    got = AA.evaluate(f, slug=SLUG, approved_book_rules={LBC.RULE_ID})
    assert AA.R_BOOK_CURRENCY in got["refusals"]


def test_a_failing_mapper_reader_or_hook_records_not_established(hooks):
    def boom(*a, **k):
        raise RuntimeError("x")

    v = PB.live_book_evidence({"live_book_identity": boom}, _CAND, obs=_OBS,
                              now=T0)
    assert v["verdict"] == "NOT_ESTABLISHED" and v["stream_read"] is False
    v = PB.live_book_evidence({"live_book_identity": lambda s, i: dict(EXACT),
                               "live_book_reader": boom}, _CAND, obs=_OBS,
                              now=T0)
    assert v["verdict"] == "NOT_ESTABLISHED"
    assert v["reason"].startswith("LIVE_BOOK_EVALUATION_FAILED")
    hooks.LIVE_BOOK_EVIDENCE = boom
    v = PB.live_book_evidence({}, _CAND, obs=_OBS, now=T0)
    assert v["verdict"] == "NOT_ESTABLISHED" and v["stream_read"] is False


def test_the_executing_process_installs_the_evidence_hook():
    from sportsassets import decision_hooks as DH
    from sportsassets import execution_intent as EI
    try:
        EI.start(lambda: None, object())
        assert DH.LIVE_BOOK_EVIDENCE is LBE.for_decision
    finally:
        EI.stop()
    assert DH.LIVE_BOOK_EVIDENCE is None and DH.DECISION_HOOK is None

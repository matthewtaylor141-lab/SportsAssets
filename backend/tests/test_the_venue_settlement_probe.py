"""THE PROBE MUST TELL A PARSER DEFECT FROM AN ABSENT FIELD.

WHY IT MATTERS ENOUGH TO TEST BEFORE READING PRODUCTION. I reported
`CLOSED_BUT_NO_REPORTED_OR_CONVERGED_OUTCOME` as the venue's refusal. It is
OUR parser's verdict. Those are different claims, and only a probe that
separates them can settle which is true -- so the separation is tested
against constructed payloads here, where the answer is known, before it is
trusted against a payload where it is not.

THE SPECIFIC DEFECT MODELLED. `_converged_winner` requires `outcomePrices`
to be a list. Several venues deliver it as a JSON-encoded STRING. Both
payloads below carry prices converged to exactly 1 and 0; one is consumable
and one is rejected on the isinstance check with the data sitting right
there. A probe that called both "the venue reported no outcome" would hide
the repairable case inside the unrepairable one.
"""

from __future__ import annotations

import json

import pytest

from sportsassets import bettor_live_read as LR
from sportsassets import bettor_venue_settlement_probe as P

SLUG = "aec-mlb-az-col-2026-09-24"


# ── the doubles: the venue's two surfaces, shaped as the venue shapes them

class _Markets:
    def __init__(self, *, market=None, settlement=None, raises=None):
        self._market, self._settlement, self._raises = (
            market, settlement, raises)

    def list(self, _q):
        return {"markets": ([self._market] if self._market else [])}

    def settlement(self, _slug):
        if self._raises is not None:
            raise self._raises
        return self._settlement


class _Client:
    def __init__(self, **kw):
        self.markets = _Markets(**kw)


class _NotFoundError(Exception):
    pass


_NotFoundError.__name__ = "NotFoundError"


def _market(**over):
    """A closed MLB market, shaped as the 2026-09-21 measurement found it."""
    m = {"slug": SLUG, "closed": True, "active": False, "archived": False,
         "status": "CLOSED", "ep3Status": "SYNCED",
         "question": "Will Arizona Diamondbacks beat Colorado Rockies?",
         "outcomes": ["Arizona Diamondbacks", "Colorado Rockies"],
         "outcomePrices": ["0.5", "0.5"],
         "endDate": "2026-09-24T19:10:00Z",
         "minimumTradeQty": "0.01", "orderPriceMinTickSize": "0.01",
         "feeCoefficient": "0.0695",
         "description": "This market settles on the final result. If the "
                        "game is abandoned the market is void and stakes "
                        "are returned." * 20}
    m.update(over)
    return m


# ── 1 · a reported settlement is the venue's own answer ──────────────

def test_a_reported_settlement_price_is_authoritative():
    c = _Client(market=_market(),
                settlement={"marketSlug": SLUG,
                            "settlementPrice": {"value": "1",
                                                "currency": "USD"},
                            "settledAt": "2026-09-24T22:31:00Z"})
    got = P.probe(c, SLUG)
    assert got["terminal_reading"] == P.R_REPORTED, got
    assert got["authoritative_payout_present"] is True
    assert got["reader_verdict"]["status"] == LR.RESOLVED
    # THE ENDPOINT AND THE INSTANT ARE ON THE RECORD.
    assert got["settlement"]["endpoint"] == P.EP_SETTLEMENT
    assert got["retrieved_at"].startswith("20")
    assert got["settlement"]["top_level_keys"] == [
        "marketSlug", "settledAt", "settlementPrice"]
    # AND THE FIELD TYPES, which is what makes the capture reusable.
    sh = got["settlement"]["field_shapes"]["settlementPrice"]
    assert sh["type"] == "dict" and sh["keys"] == ["currency", "value"]


# ── 2 · a converged price is OURS, and is labelled as ours ───────────

def test_a_converged_price_is_an_inference_and_says_so():
    c = _Client(market=_market(outcomePrices=["1", "0"]),
                raises=_NotFoundError("no settlement"))
    got = P.probe(c, SLUG)
    assert got["terminal_reading"] == P.R_CONVERGED, got
    # THE DISTINCTION THE WHOLE MODULE EXISTS FOR.
    assert got["authoritative_payout_present"] is False
    assert "OUR INFERENCE" in got["why"]
    assert got["reader_verdict"]["status"] == LR.RESOLVED_DERIVED


# ── 3 · THE PARSER DEFECT, named rather than reported as absence ─────

def test_a_json_encoded_price_vector_is_now_consumed_not_a_gap():
    """THE DEFECT THIS TEST WAS WRITTEN FOR IS FIXED, so the assertion
    changes with it.

    When first written, `_converged_winner` rejected a JSON-encoded price
    vector on the isinstance check and the reader returned
    CLOSED_BUT_NO_REPORTED_OR_CONVERGED_OUTCOME -- and this test asserted
    that the probe named it as OUR gap. Production then confirmed the venue
    really does deliver `outcomePrices` as a string
    (`tests/fixtures/pmus_settled_market_2026_09_24_az_col.json`), so
    `_as_list` now decodes it and the reader reaches RESOLVED_DERIVED.

    A closed defect must not keep being reported as open, so the probe's
    `parser_gap` is gated on the reader's own verdict: consumed is not a
    gap. The inference is still OURS and still not a reported settlement,
    which is the part that must never drift.
    """
    c = _Client(market=_market(outcomePrices=json.dumps(["1", "0"])),
                raises=_NotFoundError("no settlement"))
    got = P.probe(c, SLUG)
    # THE READER NOW CONSUMES IT -- and says so as an inference, not a
    # reported settlement.
    assert got["reader_verdict"]["status"] == LR.RESOLVED_DERIVED
    assert got["terminal_reading"] == P.R_CONVERGED
    assert got["authoritative_payout_present"] is False
    assert "OUR INFERENCE" in got["why"]
    # AND THE PROBE NO LONGER CALLS IT A GAP, because it is not one.
    gap = got["parser_gap"]
    assert gap["found"] is False, gap
    assert gap["reader_status"] == LR.RESOLVED_DERIVED
    assert "not a gap" in gap["why"]
    # THE SHAPE IS STILL CAPTURED, so the string delivery stays on record.
    sh = got["listing"]["payout_candidates"]["outcomePrices"]["shape"]
    assert sh["type"] == "str" and sh["looks_like_json_encoded"] is True


def test_a_decodable_but_unconverged_vector_is_not_a_parser_gap():
    """A gap means the parser CANNOT consume the field. An unconverged
    vector was consumed fine; the market simply has not settled."""
    c = _Client(market=_market(outcomePrices=json.dumps(["0.6", "0.4"])),
                raises=_NotFoundError("no settlement"))
    got = P.probe(c, SLUG)
    assert got["reader_verdict"]["status"] == LR.UNREADABLE
    assert got["parser_gap"]["found"] is False, got["parser_gap"]


def test_a_shape_the_decoder_cannot_read_is_named_as_our_gap():
    """THE MECHANISM IS KEPT, aimed at what it should have meant all along:
    a payout field present in a shape `_as_list` cannot turn into a list,
    while the reader refuses, is OUR defect and is named as one."""
    c = _Client(market=_market(outcomePrices={"Arizona": 1, "Colorado": 0}),
                raises=_NotFoundError("no settlement"))
    got = P.probe(c, SLUG)
    assert got["reader_verdict"]["status"] == LR.UNREADABLE
    gap = got["parser_gap"]
    assert gap["found"] is True, gap
    f = [x for x in gap["fields"] if x["field"] == "outcomePrices"]
    assert f, gap
    assert f[0]["delivered_as"] == "dict"
    assert f[0]["decoder"] == "bettor_live_read._as_list"
    assert "nothing consumes" in gap["why"]
    # AND THE LIMIT IS STILL STATED: a price vector never becomes a
    # reported settlement, whatever shape it arrives in.
    assert "never a reported settlement" in gap["what_would_change"]


def test_a_genuinely_absent_field_is_not_called_a_parser_gap():
    c = _Client(market=_market(outcomePrices=["0.5", "0.5"]),
                raises=_NotFoundError("no settlement"))
    got = P.probe(c, SLUG)
    assert got["terminal_reading"] == P.R_UNREADABLE
    assert got["parser_gap"]["found"] is False
    assert "the absence is the payload's" in got["parser_gap"]["why"]
    # AND THE SHAPES ARE STILL CAPTURED, so the next reader need not guess.
    cands = got["listing"]["payout_candidates"]
    assert cands["outcomePrices"]["present"] is True
    assert cands["outcomePrices"]["shape"]["type"] == "list"
    assert cands["outcomePrices"]["shape"]["member_types"] == ["str"]


# ── 4 · a void is read from status, never from prose ─────────────────

def test_an_explicit_void_is_read_from_a_status_field():
    c = _Client(market=_market(status="CANCELED"),
                raises=_NotFoundError("no settlement"))
    got = P.probe(c, SLUG)
    assert got["terminal_reading"] == P.R_VOID, got
    assert got["listing"]["void_evidence"]["declared"] is True
    assert got["listing"]["void_evidence"]["field"] == "status"


def test_the_word_void_in_rules_prose_does_not_void_the_market():
    """EVERY rules text on this venue says "void" while describing when a
    void would occur. Matching prose would void the entire book."""
    m = _market()
    assert "void" in m["description"]
    c = _Client(market=m, raises=_NotFoundError("no settlement"))
    got = P.probe(c, SLUG)
    assert got["terminal_reading"] == P.R_UNREADABLE, got
    assert got["listing"]["void_evidence"]["declared"] is False
    assert "description" not in P.VOID_STATUS_FIELDS


# ── 5 · closed / resolved / settledAt are never payout evidence ──────

def test_closed_and_settled_at_alone_do_not_settle_anything():
    """THE INSTRUCTION MADE EXPLICIT AS A TEST. The tennis payload carried
    `closed: true`, `resolved: true` and a `settledAt`, and nothing else.
    That is a market that stopped trading, not a winner."""
    c = _Client(market=_market(closed=True, resolved=True,
                               settledAt="2026-09-24T17:38:24Z",
                               outcomePrices=["0.5", "0.5"]),
                raises=_NotFoundError("no settlement"))
    got = P.probe(c, SLUG)
    assert got["terminal_reading"] == P.R_UNREADABLE, got
    assert got["authoritative_payout_present"] is False
    for f in ("closed", "resolved", "settledAt", "endDate"):
        assert f in P.describe()["never_infers_payout_from"] or f == "resolved"


def test_an_unlisted_slug_is_unmatched_not_unreadable():
    c = _Client(market=None, raises=_NotFoundError("no settlement"))
    got = P.probe(c, SLUG)
    assert got["terminal_reading"] == P.R_UNMATCHED, got


def test_an_open_market_is_pending():
    c = _Client(market=_market(closed=False),
                raises=_NotFoundError("no settlement"))
    got = P.probe(c, SLUG)
    assert got["terminal_reading"] == P.R_PENDING, got


# ── 6 · nothing sensitive leaves, and nothing unbounded ──────────────

def test_a_secret_shaped_key_is_redacted_even_though_none_is_expected():
    c = _Client(market=_market(apiKey="live_sk_should_never_appear",
                               sessionToken="abc"),
                raises=_NotFoundError("x"))
    got = P.probe(c, SLUG)
    blob = json.dumps(got)
    assert "live_sk_should_never_appear" not in blob
    assert "<REDACTED_BY_KEY_NAME>" in blob


def test_long_prose_is_truncated_rather_than_dumped():
    c = _Client(market=_market(), raises=_NotFoundError("x"))
    got = P.probe(c, SLUG)
    shapes = got["listing"]["field_shapes"]
    assert shapes["description"]["type"] == "str"
    assert shapes["description"]["len"] > P.MAX_STR
    # The raw subset only carries payout candidates, so prose is not in it.
    assert "description" not in (got["listing"]["raw_subset"] or {})


def test_the_probe_writes_nothing_and_says_so():
    d = P.describe()
    assert d["writes"] is False
    assert d["submits_orders"] is False
    assert d["returns_credentials"] is False
    assert set(d["reuses"]) >= {"bettor_live_read.read_resolution",
                               "bettor_live_read.read_settlement"}


def test_it_never_raises_when_the_venue_read_fails():
    class _Boom:
        @property
        def markets(self):
            raise RuntimeError("transport down")

    got = P.probe(_Boom(), SLUG)
    assert got["slug"] == SLUG
    assert got["terminal_reading"] in (P.R_UNREADABLE, P.R_UNMATCHED)
    assert got["settlement"]["error"] or got["listing"]["error"]


# ── 6 · the venue's own prose, verbatim and unabridged ───────────────
#
# WHY THIS SECTION EXISTS, AND IT IS A CORRECTION. The per-condition
# settlement table I reported -- two conditions stated, five silent -- was
# computed against `tests/fixtures/pmus_settled_market_2026_09_24_az_col
# .json`, whose `description` is 183 characters. The LIVE listing for the
# same slug reports 380. A fixture is not the venue, and a claim about what
# a venue does not say cannot rest on an abridged copy of what it does.

def test_the_prose_is_quoted_in_full_not_at_the_payload_string_limit():
    """`MAX_STR` is 400 and truncating a settlement rule at 400 characters
    is exactly the mistake this closes, so the prose has its own limit and
    the TRUE length travels beside the text."""
    c = _Client(market=_market(), raises=_NotFoundError("x"))
    got = P.probe(c, SLUG)
    t = got["settlement_terms"]
    assert t["read"] is True
    assert t["field"] == "description"
    # The helper repeats its sentence 20 times, so this is far past MAX_STR.
    assert t["chars"] > P.MAX_STR
    assert t["chars"] == len(_market()["description"])
    assert len(t["text"]) <= P.MAX_PROSE
    assert t["truncated"] is (t["chars"] > P.MAX_PROSE)
    # VERBATIM: the head of the quote is the head of the venue's text.
    assert _market()["description"].startswith(t["text"][:120])


def test_it_reads_the_same_field_order_production_reads():
    """A probe that read a different field from the production reader would
    describe a rule the lane never saw."""
    from sportsassets import bettor_live_read as _LR

    assert P.PROSE_FIELDS == _LR.RULES_TEXT_FIELDS


def test_a_condition_the_prose_does_not_state_is_absence_not_agreement():
    c = _Client(market=_market(description=(
        "This market settles on the final result of the game, including "
        "any extra innings.")), raises=_NotFoundError("x"))
    t = P.probe(c, SLUG)["settlement_terms"]
    assert "DECIDED_AFTER_REGULATION" in t["stated"]
    # The five terminal cases a one-sentence blurb cannot reach.
    assert "STOPPED_BEFORE_THE_MINIMUM" in t["not_stated"]
    assert t["contradicted"] == []
    assert "ABSENCE" in t["what_not_stated_means"]
    assert "never agreement" in t["what_not_stated_means"]
    # AND IT IS NOT A VERDICT. Compatibility needs both sides and a scope.
    assert "not a compatibility verdict" in t["what_this_is_not"]
    assert "compare" in t["what_this_is_not"]
    assert "verdict" not in t
    assert "compatibility" not in t


def test_a_market_with_no_prose_field_refuses_by_name():
    m = _market()
    m.pop("description")
    c = _Client(market=m, raises=_NotFoundError("x"))
    t = P.probe(c, SLUG)["settlement_terms"]
    assert t["read"] is False
    assert t["refusal"] == P.R_NO_PROSE
    assert t["stated"] == {}


def test_a_sentence_naming_a_condition_with_no_payout_is_reported_unused():
    """The difference between "the venue is silent" and "the venue mentions
    it and our reader recognises no payout" is the difference between an
    external gap and one of ours."""
    c = _Client(market=_market(description=(
        "If the game is suspended it may be resumed at the discretion of "
        "the league.")), raises=_NotFoundError("x"))
    t = P.probe(c, SLUG)["settlement_terms"]
    assert t["stated"] == {}
    assert t["unused_evidence"], t

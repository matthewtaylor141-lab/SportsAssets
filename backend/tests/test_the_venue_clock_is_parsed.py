"""The venue's book clock is READ, and an unread clock stays UNKNOWN.

THE PRODUCTION DEFECT THESE PIN. On build 51f20e7 every book read in the
external-valuation lane recorded

    venue_clock VENUE_CLOCK_UNPARSEABLE
    fresh       null

and nine positive-edge candidates were refused by the STALE_DATA gate --
not because a book was stale, but because the age was never measured. The
parse was one line:

    vt = float(venue_ts)
    vt = vt / 1000.0 if vt > 1e11 else vt      # ms or s

which is a parser for a NUMERIC epoch field. `marketData.transactTime` is
an ISO-8601 string with a bare Z and up to nine fractional digits, so
`float()` refused every real value the venue has ever sent. The ms-or-s
heuristic never ran at all.

THE REPO ALREADY HAD THE RIGHT PARSER. `bettor_market_stream._parse_ts`
rewrites the Z, truncates the fraction to six digits and refuses a naive
stamp. The lane now reuses it. These tests pin both halves: the forms that
must parse, and the ones that must stay UNKNOWN rather than be guessed.
"""

import json
import pathlib

import pytest

from sportsassets.bettor_market_stream import _parse_ts


FIXTURE = (pathlib.Path(__file__).parent / "fixtures"
           / "pmus_book_transact_time_forms.json")


@pytest.fixture(scope="module")
def captured():
    return json.loads(FIXTURE.read_text())


# ── 1 · THE FORMS THE VENUE ACTUALLY SENDS ───────────────────────────

def test_the_fixture_names_its_field_and_its_parser(captured):
    assert captured["field_path"] == "marketData.transactTime"
    assert captured["json_type"] == "string"
    assert captured["supported_parser"].endswith("_parse_ts")


def test_every_observed_form_parses_to_its_stated_epoch(captured):
    for form in captured["observed_forms"]:
        dt = _parse_ts(form["value"])
        assert dt is not None, form["value"]
        assert dt.tzinfo is not None, form["value"]
        assert dt.timestamp() == pytest.approx(form["expected_epoch_s"],
                                               abs=1e-3), form["value"]


def test_the_nanosecond_form_is_present_and_parses(captured):
    """The form that breaks BOTH float() and fromisoformat unaided."""
    nano = [f for f in captured["observed_forms"]
            if f["fractional_digits"] == 9]
    assert nano, "the nanosecond capture must stay in the fixture"
    for f in nano:
        assert _parse_ts(f["value"]) is not None


def test_float_refuses_every_observed_form(captured):
    """The old parse, shown failing on the real data rather than argued."""
    for form in captured["observed_forms"]:
        with pytest.raises(ValueError):
            float(form["value"])


def test_fromisoformat_is_not_the_reason_to_use_the_supported_parser(captured):
    """A CORRECTION TO MY OWN FIRST DRAFT of this test.

    It asserted that 3.11's `fromisoformat` rejects a bare Z and nine
    fractional digits, so the supported parser was needed for that. Run
    against this interpreter it ACCEPTS all five forms -- the rejection was
    true of <=3.10. The defect is narrower and undiminished: the field is a
    STRING and the lane called `float()` on it.

    The reason to reuse `_parse_ts` is the NAIVE case: `fromisoformat`
    returns a naive datetime for a stamp with no zone, and `.timestamp()`
    on that silently reads it in the host's local time. `_parse_ts` refuses
    it, so the verdict stays UNKNOWN instead of being shifted by an offset.
    """
    from datetime import datetime
    for form in captured["observed_forms"]:
        assert datetime.fromisoformat(form["value"]) is not None
    naive = datetime.fromisoformat("2026-09-21T18:20:25.743291447")
    assert naive.tzinfo is None, "fromisoformat hands back a naive stamp"
    assert _parse_ts("2026-09-21T18:20:25.743291447") is None, (
        "the supported parser refuses it rather than guessing a zone")


# ── 2 · WHAT MUST STAY UNKNOWN ───────────────────────────────────────

def test_the_unparseable_values_stay_unparsed(captured):
    for bad in captured["must_stay_unparsed"]:
        assert _parse_ts(bad["value"]) is None, bad


def test_a_naive_stamp_is_refused_not_assumed_to_be_utc(captured):
    naive = "2026-09-21T18:20:25.743291447"
    assert _parse_ts(naive) is None
    assert any(b["value"] == naive for b in captured["must_stay_unparsed"])


def test_none_and_the_absent_sentinel_are_not_timestamps():
    assert _parse_ts(None) is None
    assert _parse_ts("NOT_IDENTIFIED") is None


# ── 3 · THE LANE USES THAT PARSER, AND NOTHING ELSE ──────────────────

def test_the_entry_lane_imports_the_supported_parser():
    from sportsassets.workers import ext_pinnacle_loop as loop
    assert loop._stream_parse_ts is _parse_ts


def test_the_lane_no_longer_calls_float_on_the_clock():
    """A second implementation is how the first one became float()."""
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as loop
    # COMMENTS STRIPPED. The first draft of this assertion failed on the
    # comment that EXPLAINS the old parse, which is the wrong thing to
    # forbid -- the record of a defect belongs next to its fix.
    src = "\n".join(l for l in inspect.getsource(loop.venue_quote).splitlines()
                    if not l.lstrip().startswith("#"))
    assert "float(venue_ts)" not in src
    assert "_stream_parse_ts(venue_ts)" in src
    # AND THE FALLBACK IS STILL NAMED, never our own read clock.
    assert "VENUE_CLOCK_UNPARSEABLE" in src
    assert "VENUE_CLOCK_NOT_PROVIDED" in src


def test_the_freshness_limits_are_unchanged():
    from sportsassets.workers import ext_pinnacle_loop as loop
    assert loop.PINNACLE_MAX_AGE_S == 30.0
    assert loop.MAX_VENUE_QUOTE_AGE_S == 30.0


# ── 4 · THE AGE IS RE-AGED AT THE DECISION, NOT AT THE READ ──────────

def test_the_decision_instant_governs_not_the_receipt_time():
    """`_entry_freshness` must re-age BOTH clocks at the decision."""
    from sportsassets.workers import ext_pinnacle_loop as loop

    read_at = 1_000_000.0
    decided_at = read_at + 9.0          # two more network reads happened
    venue_epoch = read_at - 4.0         # the book was 4 s old when read
    quote = {"observed_at": "2026-09-21T18:20:25Z",
             "received_at": decided_at - 1.0}
    # The quote's own clock, so only the venue side is under test here.
    got = loop._entry_freshness(
        quote={"observed_at": decided_at - 3.0, "received_at": decided_at - 1.0},
        vq={"venue_ts": venue_epoch, "age_s": 4.0,
            "age_basis": "VENUE_TRANSACT_TIME"},
        now=decided_at)
    # 13 s at the decision, not the 4 s measured at the read.
    assert got["venue_age_s"] == pytest.approx(13.0, abs=1e-6)
    assert got["venue_age_at_read_s"] == 4.0
    assert got["venue_age_basis"] == (
        "VENUE_TRANSACT_TIME_REAGED_AT_THE_DECISION")
    assert got["both_reaged_at_the_decision"] is True


def test_an_unmeasured_venue_clock_leaves_the_verdict_unknown():
    """UNKNOWN, and explicitly not an observed stale age."""
    from sportsassets.workers import ext_pinnacle_loop as loop
    got = loop._entry_freshness(
        quote={"observed_at": 1_000_000.0, "received_at": 1_000_000.0},
        vq={"venue_ts": None, "age_s": None,
            "age_basis": "VENUE_CLOCK_UNPARSEABLE"},
        now=1_000_001.0)
    assert got["fresh"] is None
    assert got["venue_age_s"] is None
    assert got["venue_age_basis"] == "VENUE_CLOCK_UNPARSEABLE"


def test_a_parsed_clock_inside_the_limit_is_fresh():
    from sportsassets.workers import ext_pinnacle_loop as loop
    now = 1_000_000.0
    got = loop._entry_freshness(
        quote={"observed_at": now - 5.0, "received_at": now - 1.0},
        vq={"venue_ts": now - 6.0, "age_s": 5.0,
            "age_basis": "VENUE_TRANSACT_TIME"},
        now=now)
    assert got["fresh"] is True, got["why"]
    assert got["venue_age_s"] == pytest.approx(6.0)
    assert got["pinnacle_age_s"] == pytest.approx(5.0)


def test_a_parsed_clock_beyond_the_limit_is_false_not_unknown():
    """The distinction the report has to make: false vs null."""
    from sportsassets.workers import ext_pinnacle_loop as loop
    now = 1_000_000.0
    got = loop._entry_freshness(
        quote={"observed_at": now - 2.0, "received_at": now - 1.0},
        vq={"venue_ts": now - 120.0, "age_s": 119.0,
            "age_basis": "VENUE_TRANSACT_TIME"},
        now=now)
    assert got["fresh"] is False
    assert got["venue_age_s"] == pytest.approx(120.0)


# ── 5 · THE RAW VALUE TRAVELS WITH THE VERDICT ───────────────────────

def test_the_clock_record_names_the_field_the_value_and_the_parser():
    """A refusal reading only "unparseable" cannot be acted on."""
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as loop
    src = inspect.getsource(loop.venue_quote)
    for key in ("field_path", "raw_type", "parser", "parser_accepts",
                "parsed_epoch_s", "age_at_read_s"):
        assert '"%s"' % key in src, key
    assert "marketData.transactTime" in src


# ── 6 · THE HEARTBEAT CARRIES THE NUMBER IT REFUSED ON ───────────────

def test_a_stale_refusal_is_only_reachable_once_the_age_is_measured():
    """Run 75's proof that the parse works, stated as a rule.

    `VENUE_QUOTE_STALE` comes from exactly one `return`, and it sits
    behind `age is not None`. When the clock does not parse, `age` is
    None and this refusal CANNOT fire -- which is why seeing it in
    production established the repair. If that guard is ever relaxed to
    fire on an unmeasured age, an UNKNOWN would start reporting itself as
    an observed stale book, and this test is what stops it.
    """
    import inspect
    import re

    from sportsassets.workers import ext_pinnacle_loop as loop
    src = inspect.getsource(loop.venue_quote)
    hits = [m for m in re.finditer(
        r"if age is not None and age > MAX_VENUE_QUOTE_AGE_S:", src)]
    assert len(hits) == 1, "the stale gate moved or was duplicated"
    after = src[hits[0].end():hits[0].end() + 400]
    assert "R_VENUE_QUOTE_STALE" in after
    # And nowhere else returns it, so there is no second, unguarded path.
    assert src.count("R_VENUE_QUOTE_STALE") == 1


def test_the_venue_error_projection_carries_the_age_limit_and_raw_value():
    """A refusal that names a number must show it.

    Run 75 printed `VENUE_QUOTE_STALE` on
    `aec-mlb-nym-wsh-2026-09-26` and could not say by how much the book
    was stale or what string it read, because the heartbeat projection
    kept only the slug and the code.
    """
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as loop
    src = inspect.getsource(loop.cycle)
    for key in ("age_s", "limit_s", "age_basis", "venue_clock"):
        assert '"%s"' % key in src, key
    assert 'diag["venue_clock"]' in src
    for key in ("field_path", "raw", "raw_type", "parsed_epoch_s",
                "age_at_read_s", "basis"):
        assert '"%s"' % key in src, key


# ── 7 · THE SEMANTICS ARE NOT ESTABLISHED, AND THE CODE SAYS SO ──────

def test_the_module_declares_the_clock_semantics_unestablished():
    """The claim the gate rests on, marked as a claim.

    `decision_instant - transactTime` measures OUR staleness only if the
    venue stamps the RESPONSE. If it stamps the LAST BOOK CHANGE, an old
    value means the book has not moved -- the ordinary condition of a quiet
    pre-game money line -- and refusing on it would refuse every quiet book
    and call the result freshness.

    Two other modules here took the other side and they are the established
    execution path: `institutional_book.current()` computes
    FRESHNESS_STATUS from BETTOR_RECEIVED_TIMESTAMP and uses transactTime
    only for MARKET_DATA_LAG_MS; `obs/streamstate` forbids subtracting a
    venue timestamp from a local reading outright.
    """
    from sportsassets.workers import ext_pinnacle_loop as loop
    assert loop.VENUE_CLOCK_SEMANTICS.startswith("UNESTABLISHED")
    assert "LAST_BOOK_UPDATE" in loop.VENUE_CLOCK_SEMANTICS
    assert "RESPONSE_STAMP" in loop.VENUE_CLOCK_SEMANTICS


def test_the_freshness_limit_is_not_relaxed_while_the_basis_is_unestablished():
    """RELAXING A GATE ON AN UNESTABLISHED READING IS LOOSENING IT.

    The conservative direction is to keep refusing. This pins the two
    limits against the values the lane has always used, so a future edit
    that widens them to let candidates through has to change this test and
    say why.
    """
    from sportsassets.workers import ext_pinnacle_loop as loop
    assert loop.MAX_VENUE_QUOTE_AGE_S == 30.0
    assert loop.PINNACLE_MAX_AGE_S == 30.0


def test_a_book_refusal_owes_five_numbers_in_the_candidate_ledger():
    """Raw value, parsed stamp, decision instant, age and limit.

    "VENUE_QUOTE_STALE" with no number is not a finding anybody can act
    on, and the semantics question cannot even be asked without the raw
    string.
    """
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as loop
    src = inspect.getsource(loop.cycle)
    for key in ("raw_transact_time", "parsed_epoch_s",
                "decision_instant_epoch_s", "age_s", "limit_s",
                "age_semantics"):
        assert '"%s"' % key in src, key


# ── 8 · THE CYCLE'S OWN LABEL, AND WHAT IT REFUSES TO CLAIM ──────────

def test_a_cycle_that_evaluated_nothing_is_labelled_an_input_path_block():
    """NOT "zero positive edge" -- that is a claim about the market.

    A cycle whose candidates never reached execution estimation did not
    measure the market, so it establishes nothing about available edge.
    """
    from sportsassets.workers import ext_pinnacle_loop as loop
    assert loop.ZERO_EVALUATED_INPUT_PATH_BLOCKED == \
        "ZERO_EVALUATED__INPUT_PATH_BLOCKED"
    import inspect
    src = inspect.getsource(loop.cycle)
    assert "ZERO_EVALUATED_INPUT_PATH_BLOCKED if evaluated == 0" in src
    assert '"cycle_label"' in src


def test_every_mapped_candidate_gets_a_ledger_row():
    """One row per candidate that reached the mapping, at its first refusal.

    Run 75 reported `mapped 3 -> evaluated 0` with no reconciliation,
    because the only per-candidate census lived in a route whose step had
    died. Three candidates failing one gate and one candidate failing three
    are different facts.
    """
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as loop
    src = inspect.getsource(loop.cycle)
    # the identity, book, record and admitted paths all record
    assert src.count("_ledger({") >= 4, src.count("_ledger({")
    assert '"first_refusal"' in src
    assert '"mapped_candidate_ledger": ledger' in src

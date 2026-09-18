"""Run 35333848994: a failed board retrieval read as an enumerated board.

The pinned walk did

    body = r.json() if r.status_code == 200 else {}
    items = body.get("markets") or body.get("data") or []
    if not items or fresh == 0:
        exhausted = True

so a non-200, a body with no row key, an unparseable body and a page the
server repeated because it ignored `offset` all collapsed to the same empty
list -- and every one was then sealed as BOARD_LIST_EXHAUSTED = YES. The run
sealed INSUFFICIENT_QUALIFYING_EVENTS over a universe of ONE market.

These tests drive the REAL walk (`board_walk`, the function `_cli()` calls)
with mocked responses. The old defect lived in `_cli()` where no test could
reach it, which is half of why it survived.
"""
import pytest

import substantive_select as S


class Resp:
    """Minimal stand-in for the httpx response the walk actually receives."""

    def __init__(self, status=200, body=None, raises=None):
        self.status_code = status
        self._body = body
        self._raises = raises

    def json(self):
        if self._raises is not None:
            raise self._raises
        return self._body


def market(slug):
    return {"slug": slug, "gameStartTime": "2026-09-18T12:00:00Z"}


def pages(*responses):
    """A get() that serves the given responses in order, then repeats the last."""
    seen = []

    def get(params):
        seen.append(dict(params))
        i = min(len(seen) - 1, len(responses) - 1)
        return responses[i]

    get.seen = seen
    return get


# --- The five outcomes are distinct ---------------------------------------

def test_the_five_retrieval_statuses_are_declared():
    assert S.BOARD_RETRIEVAL_STATUSES == (
        "VERIFIED_END_OF_BOARD", "HTTP_FAILURE", "SCHEMA_FAILURE",
        "PAGINATION_STALLED", "PAGE_CAP_REACHED",
        "SINGLE_PAGE_UNCORROBORATED",
        "COMPLETION_CONTRACT_NOT_ESTABLISHED", "PAGINATION_CONTRADICTS_END")
    assert S.VALID_UNIVERSE_WITH_SELECTION_SHORTFALL == (
        "VALID_UNIVERSE_WITH_SELECTION_SHORTFALL")


@pytest.mark.parametrize("status", [429, 500, 503, 404, 403])
def test_a_non_200_is_http_failure_not_an_exhausted_board(status):
    by_slug, receipts, st = S.board_walk(
        pages(Resp(status=status)), page_limit=2)
    assert st == S.BOARD_HTTP_FAILURE
    assert S.board_retrieval_block(by_slug, receipts, st
                                   )["BOARD_LIST_EXHAUSTED"] == "NO"
    assert receipts[-1]["HTTP_STATUS"] == status


def test_a_transport_exception_is_http_failure():
    def get(params):
        raise OSError("connection reset")

    by_slug, receipts, st = S.board_walk(get, page_limit=2)
    assert st == S.BOARD_HTTP_FAILURE
    assert "OSError" in receipts[-1]["ERROR"]


def test_an_unparseable_body_is_schema_failure():
    by_slug, receipts, st = S.board_walk(
        pages(Resp(raises=ValueError("Expecting value"))), page_limit=2)
    assert st == S.BOARD_SCHEMA_FAILURE
    assert receipts[-1]["RESPONSE_SHAPE"] == "UNPARSEABLE_BODY"


def test_a_body_with_no_row_key_is_schema_failure_not_an_empty_board():
    """THE DEFECT: {"error": ...} used to read as a finished board."""
    by_slug, receipts, st = S.board_walk(
        pages(Resp(body={"error": "unavailable", "code": 9})), page_limit=2)
    assert st == S.BOARD_SCHEMA_FAILURE
    assert receipts[-1]["RESPONSE_SHAPE"].startswith("NO_ROW_KEY:")
    assert len(by_slug) == 0


def test_a_row_key_holding_a_non_list_is_schema_failure():
    by_slug, receipts, st = S.board_walk(
        pages(Resp(body={"markets": {"slug": "x"}})), page_limit=2)
    assert st == S.BOARD_SCHEMA_FAILURE
    assert "MAPPING_KEY_NOT_A_LIST" in receipts[-1]["RESPONSE_SHAPE"]


def test_a_bare_list_body_is_read_not_crashed_on():
    """`body.get` on a list is the AttributeError family we already fixed once."""
    by_slug, receipts, st = S.board_walk(
        pages(Resp(body=[market("a")])), page_limit=2)
    assert receipts[0]["RESPONSE_SHAPE"] == "BARE_LIST"
    assert set(by_slug) == {"a"}
    assert st == S.BOARD_SINGLE_PAGE_UNCORROBORATED   # short FIRST page


def test_a_recognised_key_holding_an_empty_follow_on_page_is_a_real_end():
    by_slug, receipts, st = S.board_walk(
        pages(Resp(body={"markets": [market("a"), market("b")]}),
              Resp(body={"markets": []})), page_limit=2)
    assert st == S.BOARD_VERIFIED_END
    assert set(by_slug) == {"a", "b"}
    blk = S.board_retrieval_block(by_slug, receipts, st,
                                  endpoint=S.EVENTS_PATH)
    assert blk["BOARD_UNIVERSE_ENUMERATED"] is True
    # On an endpoint whose completion contract is NOT established the same
    # walk may not be certified -- a convention is not a contract.
    assert S.board_retrieval_block(by_slug, receipts, st,
                                   endpoint=S.MARKETS_PATH
                                   )["BOARD_UNIVERSE_ENUMERATED"] is False


def test_a_repeated_page_is_pagination_stalled_not_exhausted():
    """The venue family is documented to ignore paging params."""
    same = Resp(body={"markets": [market("a"), market("b")]})
    by_slug, receipts, st = S.board_walk(pages(same, same), page_limit=2)
    assert st == S.BOARD_PAGINATION_STALLED
    assert receipts[-1]["REPEATED_EARLIER_PAGE"] is True
    blk = S.board_retrieval_block(by_slug, receipts, st)
    assert blk["BOARD_LIST_EXHAUSTED"] == "NO"
    assert blk["BOARD_UNIVERSE_ENUMERATED"] is False


def test_a_short_follow_on_page_ends_the_walk_as_verified():
    by_slug, receipts, st = S.board_walk(
        pages(Resp(body={"markets": [market("a"), market("b")]}),
              Resp(body={"markets": [market("c")]})), page_limit=2)
    assert st == S.BOARD_VERIFIED_END
    assert set(by_slug) == {"a", "b", "c"}


def test_a_short_FIRST_page_is_uncorroborated_not_verified():
    """One request that returned less than asked is not an enumeration."""
    by_slug, receipts, st = S.board_walk(
        pages(Resp(body={"markets": [market("a")]})), page_limit=100)
    assert st == S.BOARD_SINGLE_PAGE_UNCORROBORATED
    blk = S.board_retrieval_block(by_slug, receipts, st)
    assert blk["BOARD_LIST_EXHAUSTED"] == "NO"
    assert blk["BOARD_UNIVERSE_ENUMERATED"] is False


def test_an_empty_FIRST_page_is_uncorroborated_not_verified():
    by_slug, receipts, st = S.board_walk(
        pages(Resp(body={"markets": []})), page_limit=100)
    assert st == S.BOARD_SINGLE_PAGE_UNCORROBORATED


def test_the_page_cap_is_not_an_exhausted_board():
    def get(params):
        n = params["offset"]
        return Resp(body={"markets": [market("s%d" % (n + i))
                                      for i in range(2)]})

    by_slug, receipts, st = S.board_walk(get, max_pages=3, page_limit=2)
    assert st == S.BOARD_PAGE_CAP_REACHED
    assert S.board_retrieval_block(by_slug, receipts, st
                                   )["BOARD_LIST_EXHAUSTED"] == "NO"


# --- The receipts answer the questions the failed run could not ------------

def test_receipts_carry_endpoint_params_status_shape_rows_and_pagination():
    by_slug, receipts, st = S.board_walk(
        pages(Resp(body={"markets": [market("a")], "total": 1,
                         "has_more": False})), page_limit=2)
    r = receipts[0]
    assert r["ENDPOINT"] == S.MARKETS_PATH
    assert r["PARAMS"] == {"active": "true", "closed": "false",
                           "limit": 2, "offset": 0}
    assert r["HTTP_STATUS"] == 200
    assert r["RESPONSE_SHAPE"] == "MAPPING_KEY:markets"
    assert r["ROWS_RETURNED"] == 1
    assert r["FRESH_SLUGS"] == 1
    assert r["PAGINATION_FIELDS"] == {"total": 1, "has_more": False}
    assert r["REPEATED_EARLIER_PAGE"] is False


def test_the_offset_advances_page_by_page():
    get = pages(Resp(body={"markets": [market("a"), market("b")]}),
                Resp(body={"markets": [market("c")]}))
    S.board_walk(get, page_limit=2)
    assert [p["offset"] for p in get.seen] == [0, 2]


def test_a_failed_retrieval_retains_the_receipt_that_explains_it():
    by_slug, receipts, st = S.board_walk(pages(Resp(status=503)), page_limit=2)
    blk = S.board_retrieval_block(by_slug, receipts, st)
    assert blk["BOARD_REQUEST_RECEIPTS"][0]["HTTP_STATUS"] == 503
    assert blk["BOARD_PAGES_FETCHED"] == 1


# --- The shortfall may only be blamed on the venue if the board was read ---

def test_a_shortfall_over_a_verified_board_is_the_venue():
    assert S.selection_status(S.BOARD_VERIFIED_END, False) == \
        S.VALID_UNIVERSE_WITH_SELECTION_SHORTFALL


def test_a_frozen_roster_over_a_verified_board_is_frozen():
    assert S.selection_status(S.BOARD_VERIFIED_END, True) == S.SELECTION_OK


@pytest.mark.parametrize("bad", ["HTTP_FAILURE", "SCHEMA_FAILURE",
                                 "PAGINATION_STALLED", "PAGE_CAP_REACHED",
                                 "SINGLE_PAGE_UNCORROBORATED"])
def test_a_shortfall_over_an_unread_board_is_never_blamed_on_the_venue(bad):
    st = S.selection_status(bad, False)
    assert st == "BOARD_RETRIEVAL_INCOMPLETE:" + bad
    assert S.SELECTION_INSUFFICIENT not in st
    assert S.VALID_UNIVERSE_WITH_SELECTION_SHORTFALL not in st


def test_even_a_full_roster_over_an_unread_board_is_refused():
    """A roster drawn from a universe nobody can describe is not a roster."""
    assert S.selection_status("PAGINATION_STALLED", True) == \
        "BOARD_RETRIEVAL_INCOMPLETE:PAGINATION_STALLED"


# --- The run that produced this -------------------------------------------

def test_run_35333848994_would_now_be_classified_not_called_exhausted():
    """Replay the observed shape: one row, then a page that added nothing.

    Sealed: BOARD_PAGES_FETCHED 2, BOARD_MARKETS_SEEN 1,
    BOARD_LIST_EXHAUSTED YES, SELECTION_STATUS INSUFFICIENT_QUALIFYING_EVENTS.
    Under the repair the same responses are PAGINATION_STALLED and the
    shortfall is not attributed to the venue.
    """
    one = Resp(body={"markets": [market("atc-uecl-ggk-zil-2026-07-30-ggk")]})
    by_slug, receipts, st = S.board_walk(pages(one, one), page_limit=100)
    assert len(by_slug) == 1
    assert st == S.BOARD_SINGLE_PAGE_UNCORROBORATED
    blk = S.board_retrieval_block(by_slug, receipts, st)
    assert blk["BOARD_LIST_EXHAUSTED"] == "NO"        # was "YES"
    assert blk["BOARD_REQUEST_RECEIPTS"][0]["ROWS_RETURNED"] == 1
    assert S.selection_status(st, False) == \
        "BOARD_RETRIEVAL_INCOMPLETE:SINGLE_PAGE_UNCORROBORATED"


def test_the_pinned_collapse_to_empty_is_gone_from_the_executable_source():
    """Scan CODE, not the comment that documents the defect."""
    code = "\n".join(l for l in open(S.__file__).read().splitlines()
                     if not l.lstrip().startswith("#"))
    assert "if r.status_code == 200 else {}" not in code
    assert 'body.get("markets") or body.get("data") or []' not in code


# --- ORCHESTRATION v2: the manifest is written in-job, before sampling -----

import capture_manifest as M


def _selection(frozen=True, events=("e1", "e2", "e3")):
    return {
        "EVENT_SELECTION_FROZEN": "YES" if frozen else "NO",
        "SELECTION_STATUS": "FROZEN" if frozen else "BOARD_RETRIEVAL_INCOMPLETE",
        "EVENT_IDS": list(events),
        "MARKET_IDS": ["%s-m%d" % (e, i) for e in events for i in (1, 2)],
        "MARKET_SLUGS": ["%s-s%d" % (e, i) for e in events for i in (1, 2)],
    }


def _manifest(sel, **kw):
    return M.manifest_from_selection(
        sel, code_sha="bbce49d", run_id="35333848994", capture_seconds=5400,
        host="gateway.example", selection_sha="sel", capture_spec_sha="cap",
        capture_start_utc="2026-09-18T12:00:00Z",
        request_policy={}, rate_limit_parameters={},
        scientific_definitions={}, **kw)


def test_the_manifest_refuses_a_selection_that_did_not_freeze():
    with pytest.raises(ValueError, match="SELECTION_NOT_FROZEN"):
        _manifest(_selection(frozen=False))


def test_the_manifest_refuses_a_frozen_selection_with_an_empty_roster():
    with pytest.raises(ValueError, match="ROSTER_EMPTY"):
        _manifest(_selection(events=()))


def test_the_manifest_carries_the_roster_the_selection_froze():
    sel = _selection()
    m = _manifest(sel)
    assert m["EVENT_IDS"] == sel["EVENT_IDS"]
    assert m["MARKET_IDS"] == sel["MARKET_IDS"]
    assert m["ORCHESTRATION_VERSION"] == "2"
    assert m["MANIFEST_PRECEDES_FIRST_SAMPLED_GET"] is True


def test_the_manifest_carries_the_disclosed_residual_scope():
    m = _manifest(_selection())
    assert m["INDIRECT_ISOLATION_REGIME"] == "DISCLOSED_RESIDUAL"
    assert m["INDIRECT_BETTOR_PMUS_LOAD_ISOLATION"] == "NOT_ESTABLISHED"
    assert m["INDIRECT_CONFOUND_MAGNITUDE"] == "NOT_IDENTIFIED"


def test_the_manifest_binds_to_its_own_selection_and_no_other():
    sel = _selection()
    m = _manifest(sel)
    assert M.manifest_binds_selection(m, sel)["VERIFIED"] is True
    other = _selection(events=("x1", "x2", "x3"))
    v = M.manifest_binds_selection(m, other)
    assert v["VERIFIED"] is False
    assert v["EVENT_MEMBERSHIP_MATCHES"] is False
    assert v["EVENT_SCHEDULE_MATCHES"] is False


def test_a_tampered_manifest_does_not_verify():
    sel = _selection()
    m = _manifest(sel)
    m["EVENT_IDS"] = list(m["EVENT_IDS"]) + ["smuggled"]
    v = M.manifest_binds_selection(m, sel)
    assert v["MANIFEST_INTACT"] is False
    assert v["VERIFIED"] is False


def test_the_workflow_writes_the_manifest_between_selection_and_capture():
    """Ordering is enforced by step order, not asserted by a check-in."""
    wf = open("../../../.github/workflows/beta48-substantive-capture.yml").read()
    i_sel = wf.index("Freeze the event selection")
    i_man = wf.index("Write the immutable manifest before any sampled GET")
    i_cap = wf.index("Substantive public-book capture")
    assert i_sel < i_man < i_cap
    # and it must not be skippable
    after = wf[i_man:i_cap]
    assert "if: always()" not in after
    assert "ORCHESTRATION_VERSION" in after


def test_the_module_version_and_the_workflow_version_agree():
    wf = open("../../../.github/workflows/beta48-substantive-capture.yml").read()
    i_man = wf.index("Write the immutable manifest before any sampled GET")
    blk = wf[i_man:i_man + 1200]
    assert 'ORCHESTRATION_VERSION: "%s"' % M.ORCHESTRATION_VERSION in blk


# --- The two manifest defects the independent review reproduced ------------

def test_the_cli_builds_all_three_mappings_from_the_real_modules():
    """The CLI asked substantive_capture for three names it never defined.

    getattr's "NOT_IDENTIFIED" fallback reached dict(...) and raised
    ValueError, so the manifest step could not have run. These come from the
    modules that actually own them.
    """
    cfg = M.approved_configuration()
    assert set(cfg) == {"REQUEST_POLICY", "RATE_LIMIT_PARAMETERS",
                        "SCIENTIFIC_DEFINITIONS"}
    for v in cfg.values():
        assert isinstance(v, dict) and v, "an empty mapping is not a policy"
    # and they survive build_manifest, which is where the ValueError was
    m = _manifest(_selection(), )
    assert isinstance(m["REQUEST_POLICY"], dict)


def test_the_string_not_identified_used_to_break_build_manifest():
    """Reproduce the exact failure, so the fallback cannot come back."""
    with pytest.raises(ValueError):
        M.build_manifest("sha", "2026-09-18T12:00:00Z", 5400, ["e"], ["m"],
                         ["f"], 4.0, "0.25", "NOT_IDENTIFIED", "s", "c",
                         "r", "h", "NOT_IDENTIFIED", "NOT_IDENTIFIED")


def test_a_missing_approved_definition_is_named_not_defaulted():
    class Bare:
        __name__ = "substantive_capture"

    with pytest.raises(ValueError, match="MISSING_APPROVED_DEFINITION"):
        M.request_policy_from_modules(Bare())


def test_global_request_interval_is_not_the_per_market_revisit():
    rp = M.approved_configuration()["REQUEST_POLICY"]
    assert rp["GLOBAL_REQUEST_INTERVAL_S"] == 4.0
    assert rp["PER_MARKET_REVISIT_INTERVAL_S"] == 24.0
    assert (rp["PER_MARKET_REVISIT_INTERVAL_S"] ==
            rp["GLOBAL_REQUEST_INTERVAL_S"] * rp["MARKETS_IN_ROTATION"])


def test_a_non_sorted_roster_verifies():
    """build_manifest sorts; the binding check used to compare to input order."""
    sel = _selection(events=("ev-zulu", "ev-alpha", "ev-mike"))
    m = _manifest(sel)
    assert m["EVENT_IDS"] != sel["EVENT_IDS"]           # sorted membership
    assert m["EVENT_ID_SCHEDULE"] == sel["EVENT_IDS"]   # frozen order kept
    assert M.manifest_binds_selection(m, sel)["VERIFIED"] is True


def test_a_reordered_schedule_is_caught_even_though_membership_matches():
    sel = _selection(events=("ev-zulu", "ev-alpha", "ev-mike"))
    m = _manifest(sel)
    shuffled = dict(sel, EVENT_IDS=["ev-alpha", "ev-mike", "ev-zulu"])
    v = M.manifest_binds_selection(m, shuffled)
    assert v["EVENT_MEMBERSHIP_MATCHES"] is True
    assert v["EVENT_SCHEDULE_MATCHES"] is False
    assert v["VERIFIED"] is False


def test_the_selection_file_is_bound_by_hash_not_only_the_selector_code():
    sel = _selection()
    m = _manifest(sel, selection_file_sha256="abc123")
    assert m["SELECTION_FILE_SHA256"] == "abc123"
    assert m["SELECTOR_CODE_SHA256"] == "sel"
    assert M.manifest_binds_selection(m, sel, "abc123")["VERIFIED"] is True
    assert M.manifest_binds_selection(m, sel, "different")["VERIFIED"] is False


def test_market_families_are_never_market_slugs():
    sel = _selection()
    assert sel["MARKET_SLUGS"]
    m = _manifest(sel)
    assert m["MARKET_FAMILIES"] == "NOT_IDENTIFIED"
    assert m["MARKET_FAMILIES"] != sel["MARKET_SLUGS"]


def test_manifest_created_at_is_not_a_sampling_start():
    m = _manifest(_selection())
    assert m["MANIFEST_CREATED_AT"] == "2026-09-18T12:00:00Z"
    assert m["SAMPLING_START_UTC"] == "NOT_OBSERVED_AT_MANIFEST_TIME"
    assert m["PLANNED_CAPTURE_WINDOW"]["BASIS"] == \
        "ARITHMETIC_FROM_MANIFEST_CREATED_AT"


def test_the_observed_sampling_start_is_a_separate_record_linked_by_hash():
    m = _manifest(_selection())
    before = m["MANIFEST_SHA"]
    r = M.sampling_start_record(m, "2026-09-18T12:05:11Z",
                                "2026-09-18T12:05:11Z")
    assert r["MANIFEST_SHA"] == before
    assert r["SAMPLING_START_UTC"] == "2026-09-18T12:05:11Z"
    assert m["MANIFEST_SHA"] == before          # manifest not edited
    assert m["SAMPLING_START_UTC"] == "NOT_OBSERVED_AT_MANIFEST_TIME"


# --- The retrieval contract ------------------------------------------------

def test_the_markets_endpoint_contract_is_not_established():
    assert S.BOARD_COMPLETION_CONTRACTS[S.MARKETS_PATH]["ESTABLISHED"] is False
    assert S.BOARD_COMPLETION_CONTRACTS[S.EVENTS_PATH]["ESTABLISHED"] is True


def test_pagination_evidence_outranks_a_short_page():
    full = Resp(body={"markets": [market("a"), market("b")], "has_more": True})
    short = Resp(body={"markets": [market("c")], "has_more": True})
    by, rec, st = S.board_walk(pages(full, short), page_limit=2,
                               endpoint=S.EVENTS_PATH)
    assert st == S.BOARD_VERIFIED_END               # the walk's own reading
    cert, why = S.certify_completion(st, rec, len(by), S.EVENTS_PATH)
    assert cert == S.BOARD_PAGINATION_CONTRADICTS_END
    assert "has_more" in why


def test_a_total_above_the_rows_retrieved_contradicts_the_end():
    full = Resp(body={"markets": [market("a"), market("b")], "total": 99})
    short = Resp(body={"markets": [market("c")], "total": 99})
    by, rec, st = S.board_walk(pages(full, short), page_limit=2,
                               endpoint=S.EVENTS_PATH)
    cert, why = S.certify_completion(st, rec, len(by), S.EVENTS_PATH)
    assert cert == S.BOARD_PAGINATION_CONTRADICTS_END
    assert "EXCEEDS_ROWS_RETRIEVED_3" in why


@pytest.mark.parametrize("row,problem", [
    ({"noslug": 1}, "ROW_MISSING_SLUG_KEY"),
    ({"slug": None}, "ROW_SLUG_NOT_A_NON_EMPTY_STRING"),
    ({"slug": ""}, "ROW_SLUG_NOT_A_NON_EMPTY_STRING"),
    ("not-a-mapping", "ROW_NOT_A_MAPPING"),
])
def test_a_malformed_row_retains_a_receipt_instead_of_raising(row, problem):
    """`by_slug[m["slug"]]` used to raise KeyError and seal nothing."""
    by, rec, st = S.board_walk(pages(Resp(body={"markets": [row]})),
                               page_limit=2)
    assert st == S.BOARD_SCHEMA_FAILURE
    assert rec[-1]["MALFORMED_ROWS"][0]["PROBLEM"].startswith(problem)
    assert "MALFORMED_ROWS" in rec[-1]["RESPONSE_SHAPE"]


def test_receipts_carry_timestamps_and_a_body_digest():
    by, rec, st = S.board_walk(
        pages(Resp(body={"markets": [market("a")]})), page_limit=2)
    r = rec[0]
    assert r["REQUEST_UTC"].endswith("Z")
    assert r["RECEIPT_UTC"].endswith("Z")
    assert len(r["BODY_SHA256"]) == 64


def test_body_artifacts_are_retained_when_a_sink_is_supplied():
    kept = {}

    def sink(sha, body):
        kept[sha] = body
        return "artifact:" + sha[:12]

    by, rec, st = S.board_walk(
        pages(Resp(body={"markets": [market("a")]})), page_limit=2,
        retain_bodies=sink)
    assert rec[0]["BODY_ARTIFACT"].startswith("artifact:")
    assert kept, "the body itself is resolvable from its hash"

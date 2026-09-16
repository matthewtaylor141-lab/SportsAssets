"""The capability boundary, proved by AST scan rather than asserted in prose.

The claim being proved is physical, not architectural: this collector has no
code path that could produce an authenticated or mutating request even if a
credential were handed to it. There is no parameter, header dict or branch by
which a caller could introduce one.

Run: python -m pytest research/beta48/forward/test_fwd_collect.py -q
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SRC = HERE / "fwd_collect.py"
sys.path.insert(0, str(HERE))

import fwd_collect as C  # noqa: E402

TREE = ast.parse(SRC.read_text())
TEXT = SRC.read_text()


def _code_only(tree: ast.AST) -> str:
    """The source with every docstring and comment removed.

    A banned-word scan over the RAW file is a bad test: the module docstring
    promises "no credential, no secret, no wallet, no signer" and "computes no
    expectancy, no ROI", so a naive substring scan fails on the very prose that
    states the guarantee. Worse, it would PASS a file that quietly dropped the
    promise from its docstring while keeping the behaviour.

    Scanning the unparsed AST instead tests the executable code: docstrings and
    comments are gone, string literals that the code actually uses survive.
    """
    t = ast.parse(ast.unparse(tree))
    for node in ast.walk(t):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(t))


CODE = _code_only(TREE)


# -------------------------------------------------- the capability boundary --

def test_no_mutating_http_verb_anywhere():
    """GET only. A single .post() would make this a different programme."""
    bad = {"post", "put", "patch", "delete", "request", "send", "stream"}
    found = []
    for n in ast.walk(TREE):
        if isinstance(n, ast.Attribute) and n.attr in bad:
            found.append(n.attr)
    assert not found, f"mutating/opaque HTTP verbs present: {found}"


def test_the_authenticated_host_is_not_reachable_from_this_file():
    """gateway.polymarket.us is public. api.polymarket.us needs a signature."""
    assert "gateway.polymarket.us" in CODE
    assert "api.polymarket.us" not in CODE
    assert C.GATEWAY_BASE == "https://gateway.polymarket.us"


def test_no_credential_secret_or_signing_vocabulary():
    banned = ["X-PM-Access-Key", "X-PM-Signature", "X-PM-Timestamp",
              "Authorization", "private_key", "PRIVATE_KEY", "secret",
              "SECRET", "ed25519", "Ed25519", "signer",
              "polymarket_us", "wallet", "mnemonic"]
    hits = [b for b in banned if b in CODE]
    assert not hits, f"credential/signing vocabulary present: {hits}"


def test_reads_no_environment_at_all():
    """No os.environ anywhere: a secret cannot be picked up implicitly."""
    assert "os.environ" not in CODE and "getenv" not in CODE
    for n in ast.walk(TREE):
        assert not (isinstance(n, ast.Import)
                    and any(x.name == "os" for x in n.names)), "imports os"


def test_the_whole_reachable_surface_is_enumerated():
    """Every host and path this file can reach, listed exactly.

    This used to assert "only two venue paths" and would have kept passing,
    unchanged and unnoticed, while a third and a fourth were added beside it.
    The surface is enumerated instead, so ANY addition fails here and has to be
    argued for rather than slipped in.
    """
    assert C.MARKETS_PATH == "/v1/markets"
    assert C.BOOK_PATH == "/v1/markets/{slug}/book"
    assert C.INCENTIVES_PATH == "/v1/incentives"
    assert C.DOC_URLS == ("https://docs.polymarket.us/fees",
                          "https://docs.polymarket.us/incentives/liquidity")

    # Every string literal in the file that names a host or a venue path.
    # `http_status` and `http_%d` are field/label names, not addresses.
    urlish = {n.value for n in ast.walk(TREE)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)
              and (n.value.startswith("http://")
                   or n.value.startswith("https://")
                   or n.value.startswith("/v1"))}
    assert urlish == {C.GATEWAY_BASE, C.MARKETS_PATH, C.BOOK_PATH,
                      C.INCENTIVES_PATH, *C.DOC_URLS}, sorted(urlish)

    # The doc hosts are READ-ONLY reference pages, not a trading surface.
    assert all(u.startswith("https://docs.polymarket.us/") for u in C.DOC_URLS)


def test_imports_are_stdlib_plus_httpx_only():
    mods = set()
    for n in ast.walk(TREE):
        if isinstance(n, ast.Import):
            mods.update(x.name.split(".")[0] for x in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    allowed = {"argparse", "hashlib", "json", "time", "datetime", "pathlib",
               "httpx", "__future__"}
    assert mods <= allowed, f"unexpected imports: {mods - allowed}"


# --------------------------------------------------------------- the pacer --

def test_pacer_cannot_be_configured_faster_than_the_ceiling():
    p = C.Pacer(spacing_s=0.0001)
    assert p.spacing == C.MIN_SPACING_S
    assert C.MAX_RPS == 2.0


def test_pacer_counts_every_request_it_releases():
    p = C.Pacer()
    p.spacing = 0.0            # test-local; the constructor floor is pinned above
    for _ in range(5):
        p.wait()
    assert p.requests == 5


def test_retry_after_is_read_from_the_headers_we_keep():
    assert C._retry_after({"response_headers": {"retry-after": "30"}}) == 30.0
    assert C._retry_after({"response_headers": {}}) is None
    # an unparseable value must not crash and must not become "no wait"
    assert C._retry_after({"response_headers": {"retry-after": "soon"}}) == 60.0


# -------------------------------------------------- honesty of the records --

def test_a_failed_read_is_recorded_as_a_row_not_omitted():
    """An absent row and a failed read look identical afterwards, and only
    one of them is honest."""
    class Boom:
        def get(self, *a, **k):
            raise C.httpx.ConnectError("refused")
    row = C._get(Boom(), "/v1/markets")
    assert row["error"] == "ConnectError"
    assert row["http_status"] is None
    assert "local_request_wall_utc" in row and "latency_ms" in row


def _row(slug, sides=2, **extra):
    """A market row shaped like the ones the venue ACTUALLY returned.

    Taken from run 35040105217's captured rows: no `eventSlug` anywhere, two
    `marketSides`, both quotes on the row. Mocks that omit these were testing a
    board this venue does not serve.
    """
    r = {"slug": slug, "id": slug, "title": slug, "question": "Q",
         "outcomes": '["Yes","No"]', "status": "MARKET_STATUS_OPEN",
         "marketSides": [{"identifier": slug, "description": d}
                         for d in ("Yes", "No")][:sides],
         "bestBidQuote": {"value": "0.49", "currency": "USD"},
         "bestAskQuote": {"value": "0.51", "currency": "USD"},
         "outcomePrices": '["0.51","0.49"]',
         "feeCoefficient": 0.06, "orderPriceMinTickSize": 0.01,
         "minimumTradeQty": 1, "endDate": "2026-09-20T00:00:00Z"}
    r.update(extra)
    return r


def _pager(pages):
    """An http double serving a fixed list of market-row pages."""
    state = {"n": 0}

    class H:
        def get(self, url, params=None, timeout=None):
            i = state["n"]
            state["n"] += 1
            body = {"markets": pages[i] if i < len(pages) else []}

            class R:
                status_code = 200
                headers = {}
                content = b"{}"
                json = staticmethod(lambda: body)
            return R()
    return H()


def test_board_marks_a_prefix_as_a_prefix(tmp_path):
    """DISCOVERY_LIST_EXHAUSTED = NO is the whole reason the old dataset was
    unusable. Hitting the page cap must say so."""
    pages = [[_row("s%d" % (i * C.PAGE_LIMIT + j)) for j in range(C.PAGE_LIMIT)]
             for i in range(4)]
    p = C.Pacer(); p.spacing = 0.0
    out = C.board(tmp_path, p, _pager(pages), max_pages=2)
    assert out["DISCOVERY_LIST_EXHAUSTED"] == "NO"
    assert out["PAGINATION_ADVANCED"] == "YES", "this mock DOES advance"
    assert out["pages_walked"] == 2


def test_board_marks_exhaustion_when_a_page_comes_back_short(tmp_path):
    p = C.Pacer(); p.spacing = 0.0
    out = C.board(tmp_path, p, _pager([[_row("a"), _row("b")]]), max_pages=50)
    assert out["DISCOVERY_LIST_EXHAUSTED"] == "YES"
    assert out["markets_two_sided"] == 2


def test_a_market_without_two_sides_is_recorded_not_guessed(tmp_path):
    p = C.Pacer(); p.spacing = 0.0
    out = C.board(tmp_path, p,
                  _pager([[_row("a"), _row("b", sides=1)]]), max_pages=1)
    assert out["markets_two_sided"] == 1
    assert out["skipped"][0]["reason"] == "NOT_A_TWO_SIDED_MARKET"
    assert out["skipped"][0]["side_count"] == 1


def test_the_selection_never_depends_on_an_eventSlug_field(tmp_path):
    """The defect the first live run exposed, pinned.

    Zero of 20,000 captured rows carried `eventSlug`. Grouping by it produced
    an empty dict and discarded the whole board in silence. A row with no such
    field must still be selected, and the ABSENCE must be counted so that a
    venue which later starts sending one is visible rather than silent.
    """
    p = C.Pacer(); p.spacing = 0.0
    out = C.board(tmp_path, p, _pager([[_row("a"), _row("b")]]), max_pages=1)
    assert out["markets_two_sided"] == 2, "no eventSlug must not mean no board"
    assert out["rows_with_eventSlug"] == 0
    assert all("eventSlug" not in s for s in out["selected"])


def test_the_board_records_the_venue_stated_mechanics_per_market(tmp_path):
    """feeCoefficient and the tick are READ, never assumed. The captured board
    showed three distinct tick sizes, so a single assumed tick would be wrong
    on most of it."""
    p = C.Pacer(); p.spacing = 0.0
    rows = [_row("a", orderPriceMinTickSize=0.01),
            _row("b", orderPriceMinTickSize=0.001),
            _row("c", orderPriceMinTickSize=0.005)]
    out = C.board(tmp_path, p, _pager([rows]), max_pages=1)
    assert out["tick_sizes_observed"] == [0.001, 0.005, 0.01]
    assert out["fee_coefficients_observed"] == [0.06]
    assert out["markets_with_both_board_quotes"] == 3
    assert out["selected"][0]["board_bestBidQuote"]["value"] == "0.49"


def test_a_capped_walk_is_a_prefix_and_says_so_in_those_words(tmp_path):
    """PREFIX LANGUAGE, NOT POPULATION LANGUAGE.

    A walk that stopped at the page cap has seen AT LEAST this many markets,
    not all of them. Breadth can be unbiased WITHIN the observed prefix; it is
    not exchange-wide complete, and the true size stays unknown.
    """
    pages = [[_row("s%d" % (i * C.PAGE_LIMIT + j)) for j in range(C.PAGE_LIMIT)]
             for i in range(4)]
    p = C.Pacer(); p.spacing = 0.0
    out = C.board(tmp_path, p, _pager(pages), max_pages=2)
    assert out["DISCOVERY_LIST_EXHAUSTED"] == "NO"
    assert out["OBSERVED_PREFIX_MARKETS"] == 2 * C.PAGE_LIMIT
    assert out["TRUE_ACTIVE_BOARD_SIZE"] == "NOT_IDENTIFIED"
    assert out["BREADTH_IS_EXCHANGE_WIDE_COMPLETE"] == "NO"


def test_only_a_terminal_boundary_yields_a_true_board_size(tmp_path):
    p = C.Pacer(); p.spacing = 0.0
    out = C.board(tmp_path, p, _pager([[_row("a"), _row("b")]]), max_pages=50)
    assert out["DISCOVERY_LIST_EXHAUSTED"] == "YES"
    assert out["TRUE_ACTIVE_BOARD_SIZE"] == 2
    assert out["BREADTH_IS_EXCHANGE_WIDE_COMPLETE"] == "YES"


def test_every_board_capture_is_stamped_with_its_fee_regime(tmp_path):
    p = C.Pacer(); p.spacing = 0.0
    out = C.board(tmp_path, p, _pager([[_row("a")]]), max_pages=1)
    assert out["fee_regime"] in ("JUL2026", "SEP2026")


def test_the_regime_boundary_is_applied_by_timestamp(tmp_path):
    """Two regimes must never be pooled, so the label is a function of the
    clock and nothing else."""
    assert C.fee_regime("2026-09-17T03:58:59+00:00") == "JUL2026"
    assert C.fee_regime("2026-09-17T03:59:00+00:00") == "SEP2026"
    assert C.fee_regime("2026-09-17T04:00:00Z") == "SEP2026"
    assert C.fee_regime("not a timestamp") == "UNKNOWN", (
        "an unreadable clock must never default into a regime")


# ------------------------------------------- the step whose absence was the gap --

def test_breadth_enrolls_every_slug_into_the_settlement_registry(tmp_path):
    reg = tmp_path / "registry.json"
    events = [{"slug": "a", "slugs": ["a"], "endDate": "2026-09-20T00:00:00Z"},
              {"slug": "b", "slugs": ["b"], "endDate": "2026-09-20T00:00:00Z"}]
    assert C.update_registry(reg, events) == 2
    assert C.update_registry(reg, events) == 0, "re-enrolment must be idempotent"
    d = json.loads(reg.read_text())
    assert d["a"]["resolved"] is False and d["a"]["endDate"]


def test_an_archived_paired_board_still_enrolls_both_of_its_legs(tmp_path):
    """Boards captured under the old two-slug shape must stay readable."""
    reg = tmp_path / "registry.json"
    assert C.update_registry(
        reg, [{"slugs": ["a", "b"], "endDate": "2026-09-20T00:00:00Z"}]) == 2


def test_settle_only_checks_markets_past_their_endDate(tmp_path):
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({
        "past": {"endDate": "2020-01-01T00:00:00Z", "resolved": False},
        "future": {"endDate": "2099-01-01T00:00:00Z", "resolved": False},
    }))
    seen = []
    class Spy:
        def get(self, url, params=None, timeout=None):
            seen.append(params)
            class R:
                status_code = 200
                headers = {}
                content = b"{}"
                @staticmethod
                def json():
                    return {"markets": [{"status": "MARKET_STATUS_RESOLVED",
                                         "outcomePrices": ["1", "0"]}]}
            return R()
    p = C.Pacer(); p.spacing = 0.0
    res = C.settle(tmp_path, reg, p, Spy())
    assert res["checked"] == 1, "a market that cannot have settled is not polled"
    assert seen == [{"slug": "past"}]
    assert res["newly_resolved"] == 1
    d = json.loads(reg.read_text())
    assert d["past"]["resolved"] is True
    assert d["past"]["outcomePrices"] == ["1", "0"]
    assert d["future"]["resolved"] is False


def test_an_unresolved_reread_does_not_mark_resolved(tmp_path):
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"x": {"endDate": "2020-01-01T00:00:00Z",
                                     "resolved": False}}))
    class Open:
        def get(self, *a, **k):
            class R:
                status_code = 200
                headers = {}
                content = b"{}"
                @staticmethod
                def json():
                    return {"markets": [{"status": "MARKET_STATUS_OPEN"}]}
            return R()
    p = C.Pacer(); p.spacing = 0.0
    res = C.settle(tmp_path, reg, p, Open())
    assert res["newly_resolved"] == 0
    assert json.loads(reg.read_text())["x"]["resolved"] is False


class _BookOK:
    def get(self, *a, **k):
        class R:
            status_code = 200
            headers = {}
            content = b"{}"
            json = staticmethod(lambda: {"bids": [], "offers": []})
        return R()


def test_breadth_is_complete_without_a_request_per_market(tmp_path):
    """The whole board fits in a segment BECAUSE the board carries the touch.

    20,000 book reads at the 2 rps ceiling is ~2.8 hours and does not fit a
    2-hour segment; capping the sample would reintroduce the selection bias
    this programme exists to remove. So every market gets a row and NO market
    is dropped, while book reads stay opt-in and counted.
    """
    evs = [{"slug": "s%d" % i, "slugs": ["s%d" % i]} for i in range(50)]
    p = C.Pacer(); p.spacing = 0.0
    n = C.breadth(tmp_path, evs, p, _BookOK(), book_reads=0)
    assert n == 50, "every market on the board still gets a row"
    assert p.requests == 0, "and none of them costs a venue request"
    rows = [json.loads(x) for x in
            (tmp_path / "breadth.jsonl").read_text().splitlines()]
    assert all(r["BOOK_READ"] == "NO" for r in rows)
    assert all(r["legs"][0]["error"] == "NOT_REQUESTED" for r in rows)


def test_opt_in_book_reads_are_bounded_and_labelled(tmp_path):
    evs = [{"slug": "s%d" % i, "slugs": ["s%d" % i]} for i in range(10)]
    p = C.Pacer(); p.spacing = 0.0
    C.breadth(tmp_path, evs, p, _BookOK(), book_reads=3)
    rows = [json.loads(x) for x in
            (tmp_path / "breadth.jsonl").read_text().splitlines()]
    assert [r["BOOK_READ"] for r in rows] == ["YES"] * 3 + ["NO"] * 7
    assert p.requests == 3, "the budget is a hard ceiling on venue contact"


def test_breadth_names_why_the_leg_gap_is_zero(tmp_path):
    """A zero must never be mistaken for an unmeasured field.

    A binary market's two sides arrive in ONE response, so there is no
    inter-leg interval at all -- which is strictly better than a small measured
    one. The basis is recorded so a later reader can tell that apart from a gap
    that was simply never taken.
    """
    p = C.Pacer(); p.spacing = 0.0
    n = C.breadth(tmp_path, [{"slug": "a", "slugs": ["a"]}], p, _BookOK(),
                  book_reads=1)
    assert n == 1
    row = json.loads((tmp_path / "breadth.jsonl").read_text().strip())
    assert row["leg_gap_ns"] == 0
    assert row["LEG_GAP_BASIS"] == "SINGLE_RESPONSE_BOTH_SIDES"
    assert row["BOOK_READ"] == "YES", "a real book read, and still no gap"
    assert len(row["legs"]) == 1
    assert row["fee_regime"] in ("JUL2026", "SEP2026")


def test_breadth_still_measures_a_real_gap_when_there_are_two_requests(tmp_path):
    """The Run 84 lesson is kept: if two requests ARE made, the interval
    between them is recorded on the row."""
    p = C.Pacer(); p.spacing = 0.0
    C.breadth(tmp_path, [{"slugs": ["a", "b"]}], p, _BookOK(), book_reads=1)
    row = json.loads((tmp_path / "breadth.jsonl").read_text().strip())
    assert row["LEG_GAP_BASIS"] == "TWO_REQUESTS"
    assert row["leg_gap_s"] >= 0 and len(row["legs"]) == 2


def test_no_fill_probability_or_expectancy_is_computed_here():
    """Fills are decided offline under frozen models. TOUCH is never written
    into a FILL field by the collector."""
    banned = ["fill_probability", "expectancy", "roi", "ROI", "edge",
              "rebate", "pnl", "PNL"]
    hits = [b for b in banned if b in CODE]
    assert not hits, f"the collector computes economics it must not: {hits}"


# --------------- the defect the first live run exposed, pinned as a test --

def test_a_repeated_page_ends_the_walk_and_is_named(tmp_path):
    """A guard against a defect this venue turned out NOT to have.

    CORRECTION. The first live run's zero selection was first attributed to a
    non-advancing cursor. The captured rows refute that: 20,000 rows carried
    20,000 DISTINCT slugs. Pagination advanced perfectly; the real defect was
    the missing `eventSlug`, pinned above.

    This guard is kept anyway, because a repeating cursor is a real failure
    mode elsewhere in this venue family and an undetected one would look like
    a large board. It is kept as a GUARD, not as the explanation.
    """
    page = [_row("a"), _row("b")] * (C.PAGE_LIMIT // 2)
    p = C.Pacer(); p.spacing = 0.0
    out = C.board(tmp_path, p, _pager([page] * 200), max_pages=200)
    assert out["markets_two_sided"] == 2, out
    assert out["markets_seen"] == 2, "duplicates must collapse to distinct slugs"
    assert out["PAGINATION_ADVANCED"] == "NO"
    assert out["pages_walked"] == 2, "a non-advancing page must END the walk"


def test_a_genuinely_advancing_pagination_still_walks(tmp_path):
    """The dedupe must not break a venue whose paging DOES work -- and this
    venue's paging does work, so this is the live case, not the edge case."""
    pages = [[_row("s%d" % (i * C.PAGE_LIMIT + j))
              for j in range(C.PAGE_LIMIT)] for i in range(3)]
    p = C.Pacer(); p.spacing = 0.0
    out = C.board(tmp_path, p, _pager(pages), max_pages=200)
    assert out["PAGINATION_ADVANCED"] == "YES"
    assert out["markets_seen"] == 3 * C.PAGE_LIMIT
    assert out["markets_two_sided"] == 3 * C.PAGE_LIMIT


# ------------------------------------------------- the incentives capture --

def _period(**kw):
    """One `timePeriods` entry, shaped as the venue actually returned it."""
    d = {"programId": "prog_1", "programType": "liquidityProgram",
         "start": "2026-09-15T22:00:00Z", "rewardPool": 1000,
         "status": "active", "discountFactor": 0.3, "targetSize": 500,
         "period": "live", "createdAt": "2026-09-15T22:54:30Z"}
    d.update(kw)
    return d


def _incentive_market(slug, periods=None, state="INSTRUMENT_STATE_OPEN"):
    return {"marketSlug": slug, "instrumentState": state, "category": "",
            "subcategory": "", "eventStartTime": "", "instrumentProduct": "",
            "timePeriods": periods if periods is not None else [_period()]}


def _rules_http(pages, status=200, doc_status=200):
    """`pages` is a list of incentive page bodies, served in order."""
    state = {"n": 0}

    class H:
        def get(self, url, params=None, timeout=None):
            if url.startswith("https://docs."):
                class D:
                    status_code = doc_status
                    headers = {}
                    content = b"body"
                    text = "FEE PAGE TEXT"
                    json = staticmethod(lambda: {})
                return D()
            i = min(state["n"], len(pages) - 1)
            state["n"] += 1
            body = pages[i]

            class R:
                status_code = status
                headers = {}
                content = b"body"
                json = staticmethod(lambda: body)
            return R()
    return H()


def test_rules_reads_the_economics_out_of_the_time_period(tmp_path):
    """Every economic field lives INSIDE a timePeriod, not on the market.

    The first live capture read the market level, found nothing, and wrote a
    full row of nulls beside a `raw` that held the answers.
    """
    page = {"programs": [_incentive_market("a")], "nextPageToken": ""}
    p = C.Pacer(); p.spacing = 0.0
    out = C.rules(tmp_path, p, _rules_http([page]))
    assert out["INCENTIVES_ENDPOINT_REACHABLE"] == "YES"
    prog = out["programs"][0]
    assert prog["MARKET_SLUG"] == "a"
    assert prog["INCENTIVE_PROGRAM_ACTIVE"] == "active"
    assert prog["PROGRAM_TYPE"] == "liquidityProgram"
    assert prog["REWARD_POOL"] == 1000
    assert prog["DISCOUNT_FACTOR"] == 0.3
    assert prog["TARGET_SIZE"] == 500
    assert prog["PROGRAM_PERIOD"] == "live"
    assert prog["PROGRAM_START"] == "2026-09-15T22:00:00Z"
    assert prog["INSTRUMENT_STATE"] == "INSTRUMENT_STATE_OPEN"
    assert out["fee_regime"] in ("JUL2026", "SEP2026")


def test_a_market_with_several_periods_becomes_several_records(tmp_path):
    """Markets carry 1, 4 or 5 periods with DIFFERENT pools and factors, so
    one of them may never stand in for the market."""
    mkt = _incentive_market("a", [
        _period(period="early", rewardPool=500, discountFactor=0.35),
        _period(period="day_of", rewardPool=1250, discountFactor=0.35),
        _period(period="live", rewardPool=10000, discountFactor=0.3,
                targetSize=20000)])
    p = C.Pacer(); p.spacing = 0.0
    out = C.rules(tmp_path, p,
                  _rules_http([{"programs": [mkt], "nextPageToken": ""}]))
    assert out["incentivized_markets"] == 1
    assert out["programs_parsed"] == 3
    assert [x["PROGRAM_PERIOD"] for x in out["programs"]] == [
        "early", "day_of", "live"]
    assert [x["REWARD_POOL"] for x in out["programs"]] == [500, 1250, 10000]


def test_the_incentive_list_is_walked_to_exhaustion(tmp_path):
    """The first capture took page one and left a nextPageToken unfollowed --
    the same prefix mistake that made the retrospective board unusable."""
    pages = [{"programs": [_incentive_market("s%d" % i)],
              "nextPageToken": "t%d" % i} for i in range(3)]
    pages.append({"programs": [_incentive_market("last")],
                  "nextPageToken": ""})
    p = C.Pacer(); p.spacing = 0.0
    out = C.rules(tmp_path, p, _rules_http(pages))
    assert out["incentive_pages_walked"] == 4
    assert out["INCENTIVES_LIST_EXHAUSTED"] == "YES"
    assert out["incentivized_markets"] == 4


def test_a_repeating_page_token_ends_the_walk(tmp_path):
    page = {"programs": [_incentive_market("a")], "nextPageToken": "same"}
    p = C.Pacer(); p.spacing = 0.0
    out = C.rules(tmp_path, p, _rules_http([page] * 10))
    assert out["incentive_pages_walked"] == 2, "a token that repeats is the end"


def test_absent_incentive_fields_are_not_inferred(tmp_path):
    """`end` and `maxSpread` are not in this payload. A duration is never
    guessed from `start`."""
    p = C.Pacer(); p.spacing = 0.0
    out = C.rules(tmp_path, p,
                  _rules_http([{"programs": [_incentive_market("a")],
                                "nextPageToken": ""}]))
    prog = out["programs"][0]
    assert prog["PROGRAM_END"] is None
    assert prog["MAX_SPREAD"] is None


def test_an_estimated_reward_is_never_recorded_as_an_actual_one(tmp_path):
    """The binding distinction. Nothing here has an account, so no reward has
    been earned by anyone and none may be written as if it had. Nor can this
    file know whether the target size was already met ahead of BETTOR: the
    endpoint publishes no total qualifying score."""
    p = C.Pacer(); p.spacing = 0.0
    out = C.rules(tmp_path, p,
                  _rules_http([{"programs": [_incentive_market("a")],
                                "nextPageToken": ""}]))
    prog = out["programs"][0]
    assert prog["ACTUAL_REWARD"] == "NOT_IDENTIFIED"
    assert prog["ESTIMATED_REWARD"] == "NOT_COMPUTED_HERE"
    assert prog["TARGET_SIZE_ALREADY_MET_BY_OTHERS"] == "NOT_IDENTIFIED"


def test_an_unreachable_incentives_endpoint_is_recorded_not_defaulted(tmp_path):
    p = C.Pacer(); p.spacing = 0.0
    out = C.rules(tmp_path, p, _rules_http([{}], status=404, doc_status=403))
    assert out["INCENTIVES_ENDPOINT_REACHABLE"] == "NO"
    assert out["programs_parsed"] == 0
    assert out["doc_http_statuses"] == [403, 403]
    # An absent answer stays absent. It never becomes "no program is running".
    assert out["NEGOTIATED_MARKET_MAKER_ECONOMICS"] == "NOT_IDENTIFIED"
    assert out["TIER_VERIFIED"] == "NO"


def test_the_program_types_observed_are_listed_separately(tmp_path):
    """Four programmes are documented; this endpoint has only been seen
    returning one type. Its silence is not evidence the others are not
    running, so coverage stays NOT_IDENTIFIED."""
    mkt = _incentive_market("a", [_period(programType="liquidityProgram"),
                                  _period(programType="fillProgram")])
    p = C.Pacer(); p.spacing = 0.0
    out = C.rules(tmp_path, p,
                  _rules_http([{"programs": [mkt], "nextPageToken": ""}]))
    assert out["PROGRAM_TYPES_OBSERVED"] == ["fillProgram", "liquidityProgram"]
    assert out["INCENTIVES_ENDPOINT_COVERS_ALL_PROGRAMS"] == "NOT_IDENTIFIED"
    assert set(out["DOCUMENTED_PROGRAMS"]) == {
        "VOLUME_INCENTIVE", "LIQUIDITY_INCENTIVE", "FILL_INCENTIVE",
        "MARKET_MAKER_PROGRAM"}
    # The two types stay on their own records; they are never merged.
    assert {x["PROGRAM_TYPE"] for x in out["programs"]} == {
        "liquidityProgram", "fillProgram"}


def test_a_program_end_is_captured_when_the_venue_sends_one(tmp_path):
    mkt = _incentive_market("a", [_period(end="2026-10-01T00:00:00Z")])
    p = C.Pacer(); p.spacing = 0.0
    out = C.rules(tmp_path, p,
                  _rules_http([{"programs": [mkt], "nextPageToken": ""}]))
    assert out["programs"][0]["PROGRAM_END"] == "2026-10-01T00:00:00Z"


def test_the_rule_documents_are_stored_with_a_hash(tmp_path):
    """A relayed coefficient becomes captured evidence only if the page that
    states it is stored verbatim and can be re-checked."""
    p = C.Pacer(); p.spacing = 0.0
    out = C.rules(tmp_path, p,
                  _rules_http([{"programs": [], "nextPageToken": ""}]))
    assert len(out["doc_sha256"]) == 2
    assert all(v for v in out["doc_sha256"].values())
    docs = [json.loads(x) for x in
            (tmp_path / "rules_docs.jsonl").read_text().splitlines()]
    assert any(d["text"] == "FEE PAGE TEXT" for d in docs)
    # The collector stores the page; it does NOT read a coefficient out of it.
    assert out["THETA_TAKER_OBSERVED_IN_DOC"] == "NOT_PARSED_HERE"


# ------------------------------------------ the incentive depth panel ------

def _prog(slug, **kw):
    d = {"MARKET_SLUG": slug, "PROGRAM_TYPE": "liquidityProgram",
         "REWARD_POOL": 1000, "TARGET_SIZE": 500, "DISCOUNT_FACTOR": 0.3,
         "PROGRAM_PERIOD": "live", "PROGRAM_ID": "p1",
         "INSTRUMENT_STATE": "INSTRUMENT_STATE_OPEN"}
    d.update(kw)
    return d


def _ev(slug, tick=0.01, kind="SPORTS_MARKET_TYPE_FUTURE"):
    return {"slug": slug, "slugs": [slug], "orderPriceMinTickSize": tick,
            "sportsMarketTypeV2": kind}


def test_the_panel_stratifies_only_on_decision_time_facts(tmp_path):
    """THE SELECTION RULE. Anything about how a market later behaved is
    forbidden; picking on later economics is how a panel manufactures the
    result it was built to test."""
    events = [_ev("a"), _ev("b"), _ev("c"), _ev("d")]
    rules = {"programs": [_prog("a"), _prog("b"),
                          _prog("c", REWARD_POOL=10000),
                          _prog("d", DISCOUNT_FACTOR=0.35)]}
    picked, plan = C.panel_cohort(events, rules, per_stratum=5)
    assert len(plan) == 3, "pool and discount factor separate the strata"
    assert {x["slug"] for x in picked} == {"a", "b", "c", "d"}


def test_a_later_outcome_field_cannot_change_the_panel(tmp_path):
    """The forbidden input, tested as behaviour rather than promised in prose.

    Two markets identical on every decision-time fact must land in the SAME
    stratum and be taken in the same order, no matter what is attached to them
    about how they later traded or paid. If any outcome field ever reached the
    stratum key, this would split them.
    """
    events = [_ev("a"), _ev("b")]
    plain = {"programs": [_prog("a"), _prog("b")]}
    tempting = {"programs": [
        dict(_prog("a"), realized_spread=0.001, volume_24h=10, later_pnl=-5),
        dict(_prog("b"), realized_spread=0.900, volume_24h=10 ** 9,
             later_pnl=5000)]}
    one, plan_one = C.panel_cohort(events, plain, per_stratum=1)
    two, plan_two = C.panel_cohort(events, tempting, per_stratum=1)
    assert len(plan_one) == len(plan_two) == 1, "one stratum, both times"
    assert [x["slug"] for x in one] == [x["slug"] for x in two] == ["a"]


def test_the_panel_takes_markets_in_slug_order_not_by_attractiveness(tmp_path):
    events = [_ev("z"), _ev("a"), _ev("m")]
    rules = {"programs": [_prog("z"), _prog("a"), _prog("m")]}
    picked, _ = C.panel_cohort(events, rules, per_stratum=2)
    assert [x["slug"] for x in picked] == ["a", "m"], (
        "deterministic, and unrelated to any outcome")


def test_the_panel_is_deterministic_across_input_order(tmp_path):
    rules_a = {"programs": [_prog("z"), _prog("a"), _prog("m")]}
    rules_b = {"programs": [_prog("m"), _prog("z"), _prog("a")]}
    evs = [_ev("z"), _ev("a"), _ev("m")]
    one, _ = C.panel_cohort(evs, rules_a, per_stratum=2)
    two, _ = C.panel_cohort(list(reversed(evs)), rules_b, per_stratum=2)
    assert [x["slug"] for x in one] == [x["slug"] for x in two]


def test_an_incentivized_market_off_the_board_prefix_is_not_invented(tmp_path):
    """The panel can only sample what discovery actually saw."""
    picked, _ = C.panel_cohort([_ev("a")],
                               {"programs": [_prog("a"), _prog("unseen")]}, 5)
    assert [x["slug"] for x in picked] == ["a"]


def test_the_panel_carries_the_program_facts_with_each_book(tmp_path):
    """So a book and its programme can never be matched up wrongly later."""
    p = C.Pacer(); p.spacing = 0.0
    n = C.panel(tmp_path, [_ev("a")], {"programs": [_prog("a")]},
                p, _BookOK(), per_stratum=1, rounds=1)
    assert n == 1
    row = json.loads((tmp_path / "panel.jsonl").read_text().strip())
    assert row["kind"] == "PANEL"
    assert row["TARGET_SIZE"] == 500 and row["DISCOUNT_FACTOR"] == 0.3
    assert row["tick"] == 0.01 and row["PROGRAM_TYPE"] == "liquidityProgram"
    assert row["fee_regime"] in ("JUL2026", "SEP2026")


def test_the_panel_plan_declares_it_is_a_separate_dataset(tmp_path):
    """BREADTH_CENSUS and INCENTIVE_DEPTH_PANEL are different datasets and
    must never be pooled: one is the whole observed prefix with no selection,
    the other a stratified sample chosen for depth."""
    p = C.Pacer(); p.spacing = 0.0
    C.panel(tmp_path, [_ev("a")], {"programs": [_prog("a")]}, p, _BookOK())
    plan = json.loads((tmp_path / "panel_plan.json").read_text())
    assert plan["DATASET"] == "INCENTIVE_DEPTH_PANEL"
    assert plan["NEVER_POOLED_WITH"] == "BREADTH_CENSUS"
    assert plan["SAMPLING_FROZEN_BEFORE_ECONOMICS"] == "YES"
    assert plan["SELECTION_WITHIN_STRATUM"] == "SLUG_ORDER"
    for f in ("PROGRAM_TYPE", "REWARD_POOL", "TARGET_SIZE",
              "DISCOUNT_FACTOR", "orderPriceMinTickSize"):
        assert f in plan["SELECTION_INPUTS"]


def test_a_rotating_page_token_over_identical_content_ends_the_walk():
    """THE DEFECT THAT VOIDED SEGMENT 35043611049, pinned.

    The endpoint handed back a DIFFERENT nextPageToken on every request while
    serving the SAME 100 markets. A guard that only watched for a REPEATED
    token never fired, the walk ran to the 200-page cap, and 267 real
    programme-periods were counted 200 times over as 53,400 -- a distribution
    that would have read as a sample two hundred times larger than anything
    observed.

    Termination is decided by CONTENT now, so a rotating cursor cannot inflate
    a sample again.
    """
    state = {"n": 0}

    class Rotating:
        def get(self, url, params=None, timeout=None):
            if url.startswith("https://docs."):
                class D:
                    status_code = 200
                    headers = {}
                    content = b"body"
                    text = "x"
                    json = staticmethod(lambda: {})
                return D()
            state["n"] += 1
            body = {"programs": [_incentive_market("a"),
                                 _incentive_market("b")],
                    "nextPageToken": "tok-%d" % state["n"]}   # ALWAYS NEW

            class R:
                status_code = 200
                headers = {}
                content = b"body"
                json = staticmethod(lambda: body)
            return R()

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = C.Pacer(); p.spacing = 0.0
        out = C.rules(Path(d), p, Rotating())
    assert out["incentive_pages_walked"] == 2, "a stale page must END the walk"
    assert out["INCENTIVES_PAGINATION_ADVANCED"] == "NO"
    assert out["incentivized_markets"] == 2
    assert out["programs_parsed"] == 2, "duplicates must not become records"


def test_a_genuinely_advancing_token_walk_still_collects_every_page(tmp_path):
    pages = [{"programs": [_incentive_market("s%d" % i)],
              "nextPageToken": "t%d" % i} for i in range(5)]
    pages.append({"programs": [_incentive_market("last")], "nextPageToken": ""})
    p = C.Pacer(); p.spacing = 0.0
    out = C.rules(tmp_path, p, _rules_http(pages))
    assert out["INCENTIVES_PAGINATION_ADVANCED"] == "YES"
    assert out["INCENTIVES_LIST_EXHAUSTED"] == "YES"
    assert out["incentivized_markets"] == 6
    assert out["programs_parsed"] == 6


def test_the_same_period_served_twice_is_recorded_once(tmp_path):
    same = _incentive_market("a")
    pages = [{"programs": [same, same], "nextPageToken": ""}]
    p = C.Pacer(); p.spacing = 0.0
    out = C.rules(tmp_path, p, _rules_http(pages))
    assert out["programs_parsed"] == 1

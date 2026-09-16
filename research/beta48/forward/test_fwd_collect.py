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


def test_only_two_venue_paths_exist():
    assert C.MARKETS_PATH == "/v1/markets"
    assert C.BOOK_PATH == "/v1/markets/{slug}/book"


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


def test_board_marks_a_prefix_as_a_prefix(tmp_path):
    """DISCOVERY_LIST_EXHAUSTED = NO is the whole reason the old dataset was
    unusable. Hitting the page cap must say so."""
    class Full:
        def get(self, *a, **k):
            class R:
                status_code = 200
                headers = {}
                content = b"{}"
                @staticmethod
                def json():
                    return {"markets": [
                        {"eventSlug": f"e{i}", "slug": f"s{i}"}
                        for i in range(C.PAGE_LIMIT)]}
            return R()
    p = C.Pacer(); p.spacing = 0.0
    out = C.board(tmp_path, p, Full(), max_pages=2)
    assert out["DISCOVERY_LIST_EXHAUSTED"] == "NO"
    assert out["pages_walked"] == 2


def test_board_marks_exhaustion_when_a_page_comes_back_short(tmp_path):
    class Short:
        def get(self, *a, **k):
            class R:
                status_code = 200
                headers = {}
                content = b"{}"
                @staticmethod
                def json():
                    return {"markets": [{"eventSlug": "e", "slug": "a"},
                                        {"eventSlug": "e", "slug": "b"}]}
            return R()
    p = C.Pacer(); p.spacing = 0.0
    out = C.board(tmp_path, p, Short(), max_pages=50)
    assert out["DISCOVERY_LIST_EXHAUSTED"] == "YES"
    assert out["events_two_outcome"] == 1


def test_an_event_that_is_not_two_outcome_is_recorded_not_guessed(tmp_path):
    class Three:
        def get(self, *a, **k):
            class R:
                status_code = 200
                headers = {}
                content = b"{}"
                @staticmethod
                def json():
                    return {"markets": [{"eventSlug": "e", "slug": x}
                                        for x in ("a", "b", "c")]}
            return R()
    p = C.Pacer(); p.spacing = 0.0
    out = C.board(tmp_path, p, Three(), max_pages=1)
    assert out["events_two_outcome"] == 0
    assert out["skipped"][0]["reason"] == "NOT_A_TWO_OUTCOME_EVENT"


# ------------------------------------------- the step whose absence was the gap --

def test_breadth_enrolls_every_slug_into_the_settlement_registry(tmp_path):
    reg = tmp_path / "registry.json"
    events = [{"eventSlug": "e1", "slugs": ["a", "b"], "endDate": "2026-09-20T00:00:00Z"}]
    assert C.update_registry(reg, events) == 2
    assert C.update_registry(reg, events) == 0, "re-enrolment must be idempotent"
    d = json.loads(reg.read_text())
    assert d["a"]["resolved"] is False and d["a"]["endDate"]


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


def test_breadth_records_the_gap_between_the_two_legs(tmp_path):
    """The reconstructed mid is only valid when the gap is small AND known.
    A pair with an unrecorded gap is the Run 84 failure."""
    class OK:
        def get(self, *a, **k):
            class R:
                status_code = 200
                headers = {}
                content = b"{}"
                @staticmethod
                def json():
                    return {"bids": [], "offers": []}
            return R()
    p = C.Pacer(); p.spacing = 0.0
    n = C.breadth(tmp_path, [{"eventSlug": "e", "slugs": ["a", "b"]}], p, OK())
    assert n == 1
    row = json.loads((tmp_path / "breadth.jsonl").read_text().strip())
    assert "leg_gap_s" in row and row["leg_gap_s"] >= 0
    assert len(row["legs"]) == 2


def test_no_fill_probability_or_expectancy_is_computed_here():
    """Fills are decided offline under frozen models. TOUCH is never written
    into a FILL field by the collector."""
    banned = ["fill_probability", "expectancy", "roi", "ROI", "edge",
              "rebate", "pnl", "PNL"]
    hits = [b for b in banned if b in CODE]
    assert not hits, f"the collector computes economics it must not: {hits}"

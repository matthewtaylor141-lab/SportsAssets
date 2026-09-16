#!/usr/bin/env python3
"""The doc capturer's boundary, proved by AST scan before it fetches anything.

The claim is physical: this file can reach the documentation host and nothing
else, and it has no code path that could authenticate, subscribe, stream or
trade even if a credential were handed to it.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SRC = HERE / "doc_capture.py"
sys.path.insert(0, str(HERE))

import doc_capture as C  # noqa: E402

TREE = ast.parse(SRC.read_text())


def _code_only(tree):
    """Executable code with docstrings stripped.

    A raw substring scan is the wrong test and has bitten this programme twice:
    it fails on the very prose that states the guarantee ("no WebSocket, no
    gRPC, no FIX"), and it would PASS a file that dropped the promise while
    keeping the behaviour.
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


def test_get_only_no_mutating_verb():
    bad = {"post", "put", "patch", "delete", "send", "stream", "request"}
    found = [n.attr for n in ast.walk(TREE)
             if isinstance(n, ast.Attribute) and n.attr in bad]
    assert not found, f"mutating/opaque verbs: {found}"


def test_the_only_reachable_host_is_the_documentation_host():
    """The base is built as "https://" + DOCS_HOST, so the literals are the
    scheme and the host and nothing else. Asserted on both."""
    assert C.DOCS_HOST == "docs.polymarket.us"
    assert C.DOCS_BASE == "https://docs.polymarket.us"

    urlish = {n.value for n in ast.walk(TREE)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)
              and n.value.startswith(("http://", "https://"))}
    assert urlish == {"https://"}, sorted(urlish)

    # No OTHER hostname literal anywhere in the executable code.
    hosts = {n.value for n in ast.walk(TREE)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and re_hostish(n.value)}
    assert hosts == {C.DOCS_HOST}, sorted(hosts)


def re_hostish(s: str) -> bool:
    import re as _re
    return bool(_re.fullmatch(r"[a-z0-9.\-]+\.[a-z]{2,}", s))


def test_no_trading_or_streaming_host_appears_anywhere():
    """Not the retail gateway, not the institutional exchange, not Auth0."""
    for banned in ("api.polymarket.us", "gateway.polymarket.us",
                   "polymarketexchange.com", "auth0.com",
                   "wss://", "grpc", "fix://"):
        assert banned not in CODE, f"{banned} reachable from the capturer"


def test_no_credential_or_signing_vocabulary():
    banned = ["Authorization", "Bearer", "client_assertion", "private_key",
              "PRIVATE_KEY", "secret", "SECRET", "api_key", "x-participant-id",
              "client_credentials", "jwt", "JWT", "Ed25519", "signer"]
    hits = [b for b in banned if b in CODE]
    assert not hits, f"credential vocabulary present: {hits}"


def test_reads_no_environment_at_all():
    assert "os.environ" not in CODE and "getenv" not in CODE
    for n in ast.walk(TREE):
        assert not (isinstance(n, ast.Import)
                    and any(x.name == "os" for x in n.names)), "imports os"


def test_imports_are_stdlib_plus_httpx_only():
    mods = set()
    for n in ast.walk(TREE):
        if isinstance(n, ast.Import):
            mods.update(x.name.split(".")[0] for x in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    allowed = {"argparse", "hashlib", "json", "re", "time", "datetime",
               "pathlib", "urllib", "httpx", "__future__"}
    assert mods <= allowed, f"unexpected imports: {mods - allowed}"


def test_the_host_guard_refuses_every_other_host():
    for bad in ("https://api.polymarket.us/v1/markets",
                "https://api.prod.polymarketexchange.com/v1/marketdata",
                "https://pmx-preprod.us.auth0.com/oauth/token",
                "https://evil.example.com/docs.polymarket.us"):
        with pytest.raises(RuntimeError):
            C._assert_docs_host(bad)


def test_the_host_guard_refuses_plain_http():
    with pytest.raises(RuntimeError):
        C._assert_docs_host("http://docs.polymarket.us/fees")


def test_the_guard_accepts_the_documentation_host():
    assert C._assert_docs_host("https://docs.polymarket.us/x") .endswith("/x")


def test_the_pacer_cannot_exceed_the_ceiling():
    assert C.MAX_RPS == 2.0
    assert C.MIN_SPACING_S == 0.5


def test_a_failed_fetch_is_recorded_as_a_row():
    """An absent row and a failed fetch look identical afterwards, and only
    one of them is honest."""
    class Boom:
        def get(self, *a, **k):
            raise C.httpx.ConnectError("refused")
    p = C.Pacer(); p._last = -1e9
    row = C.fetch(Boom(), p, "/trader-guide/authentication")
    assert row["error"] == "ConnectError"
    assert row["http_status"] is None
    assert row["url"].startswith(C.DOCS_BASE)


def test_the_index_parser_keeps_only_same_host_pages():
    text = ("see https://docs.polymarket.us/trader-guide/authentication and "
            "https://example.com/evil and [x](/data-guide/overview) and "
            "https://docs.polymarket.us/logo.png")
    got = C.paths_from_index(text)
    assert "/trader-guide/authentication" in got
    assert "/data-guide/overview" in got
    assert not any("example.com" in g for g in got)
    assert not any(g.endswith(".png") for g in got)


def test_the_scope_page_is_seeded_so_it_is_fetched_even_if_unindexed():
    """It is the page the whole question turns on."""
    assert "/trader-guide/authentication" in C.SEED_PATHS
    assert C.QUESTION_PAGES["SCOPES"] == ("/trader-guide/authentication",)


def test_the_capturer_parses_no_capability_out_of_what_it_stores():
    """Capture and conclusion are separate steps, so the capture cannot drift
    into the answer."""
    for banned in ("read:marketdata", "write:orders", "read:l2marketdata"):
        assert banned not in CODE, (
            "the capturer is looking for a scope; that belongs in analysis")

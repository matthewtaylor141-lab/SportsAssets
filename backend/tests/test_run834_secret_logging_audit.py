"""RUN 83.4 -- does authentication material ever escape? Tested, not asserted.

FOUR THINGS MAY NEVER APPEAR ANYWHERE: the PMUS secret, a complete signature,
a complete auth header set, and the broker caller token. `mint_id` MAY appear --
it is an identifier that ties a sample to a ledger row and is not material.

SIX PLACES ARE CHECKED, and the last two are the ones that usually leak:

    application logs        both processes, at DEBUG
    HTTP access logs        BaseHTTPRequestHandler writes a request line by
                            default; the broker overrides log_message
    structured logging      json-rendered records
    exception rendering     str(exc) and repr(exc) on the real error paths
    TRACEBACK LOCALS        `mint_market_ws_headers` has the secret in its own
                            frame. Anything that formats locals -- a debug
                            handler, an error reporter, `traceback.print_exc`
                            with a locals-aware formatter -- would print it. The
                            broker's signing failure path is therefore
                            deliberately `log.error("...")` with NO exc_info,
                            and this file checks that by AST rather than trust.
    persisted Run 83 rows   the migration text, scanned for a material column

THE SUBPROCESS CHECK IS THE REAL ONE. Static scans prove nobody wrote a bad log
line; running both processes for real and grepping every byte they emitted
proves no library wrote one on their behalf.
"""
from __future__ import annotations

import ast
import json
import logging
import pathlib
import subprocess
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
BROKER_ROOT = REPO / "pmus-broker"
COLLECTOR_ROOT = REPO / "rn1-collector"

pytestmark = pytest.mark.skipif(not BROKER_ROOT.exists(),
                                reason="run 83.4 artifacts not present")

if str(BROKER_ROOT) not in sys.path:
    sys.path.insert(0, str(BROKER_ROOT))
if str(BACKEND / "tests") not in sys.path:
    sys.path.insert(0, str(BACKEND / "tests"))


def _sources() -> list[pathlib.Path]:
    return (sorted((BROKER_ROOT / "pmusbroker").rglob("*.py"))
            + sorted((BACKEND / "sportsassets" / "obs").rglob("*.py"))
            + sorted((COLLECTOR_ROOT / "rn1collector").rglob("*.py")))


# =====================================================================
# LIVE: run both processes and grep every byte they emitted
# =====================================================================
def test_no_material_appears_in_anything_either_process_emitted(tmp_path):
    """End to end, at DEBUG, including the failure paths."""
    from test_run834_offline_e2e import Rig          # the same rig, reused

    rig = Rig(tmp_path)
    try:
        rig.run_collector()
        # Exercise the refusal paths too -- an error path is where a careless
        # implementation echoes the request back.
        rig.mint_raw({"path": "/v1/orders"}, token=rig.caller_token)
        rig.mint_raw({"consumer": "x"}, token="wrong-token")
        rig.mint_raw({"consumer": "x"}, token=None)
        _, minted = rig.mint_raw({"consumer": "x"}, token=rig.caller_token)
        signature = minted["headers"]["X-PM-Signature"]

        for p in rig.procs:
            p.terminate()
        emitted = ""
        for p in rig.procs:
            try:
                out, _ = p.communicate(timeout=15)
                emitted += out or ""
            except subprocess.TimeoutExpired:
                p.kill()
        emitted += rig.last_stdout

        assert rig.secret_b64 not in emitted, "THE SECRET WAS LOGGED"
        assert rig.secret_b64[:20] not in emitted, "a secret prefix was logged"
        assert signature not in emitted, "A COMPLETE SIGNATURE WAS LOGGED"
        assert signature[:24] not in emitted, "a signature prefix was logged"
        assert rig.caller_token not in emitted, "THE CALLER TOKEN WAS LOGGED"

        # And the artifacts the test itself wrote.
        for f in tmp_path.rglob("*"):
            if f.is_file():
                blob = f.read_text(errors="replace")
                assert rig.secret_b64 not in blob, f"secret in {f.name}"
                assert signature not in blob, f"signature in {f.name}"
                assert rig.caller_token not in blob, f"caller token in {f.name}"

        # mint_id IS allowed to travel, and does -- otherwise a sample could not
        # be tied to the ledger row that authorised its connection.
        assert "minted" in emitted or "mint" in emitted.lower()
    finally:
        rig.close()


# =====================================================================
# STATIC: nobody wrote a bad line, and nothing renders locals
# =====================================================================
def test_the_broker_never_renders_an_exception_with_locals_or_exc_info():
    """The signing frame holds the secret. Nothing may format that frame.

    `mint_market_ws_headers`'s own locals include `secret_key_b64`. A traceback
    printed with a locals-aware formatter would render it, and that is a leak
    with no log line to blame. The broker's failure path logs a fixed string.
    """
    offenders = []
    for path in sorted((BROKER_ROOT / "pmusbroker").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = _dotted(node.func)
                if fn.endswith(("format_exc", "print_exc", "print_exception",
                                "format_exception")):
                    offenders.append(f"{path.name}: {fn}")
                for kw in node.keywords:
                    if kw.arg == "exc_info":
                        offenders.append(f"{path.name}: exc_info=")
            if isinstance(node, ast.Attribute) and node.attr == "exception":
                if _dotted(node).startswith("log."):
                    offenders.append(f"{path.name}: log.exception")
    assert not offenders, (
        "the process holding the secret renders exceptions:\n  "
        + "\n  ".join(offenders))


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def test_the_broker_overrides_the_default_http_access_log():
    """BaseHTTPRequestHandler logs to stderr by default. Left alone, a later
    edit that starts echoing headers would print the one thing that must not be
    printed. The override routes through logging and takes no header."""
    src = (BROKER_ROOT / "pmusbroker" / "server.py").read_text()
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "log_message"), None)
    assert fn is not None, "the broker no longer overrides log_message"
    # Scan the CODE, not the docstring. The docstring explains that headers must
    # never be logged, and a substring scan cannot tell an explanation from a
    # violation -- the same mistake this project has now made twice, so the test
    # strips the docstring first and dumps only the statements.
    statements = [n for n in fn.body
                  if not (isinstance(n, ast.Expr)
                          and isinstance(n.value, ast.Constant)
                          and isinstance(n.value.value, str))]
    assert statements, "log_message is now only a docstring"
    body = "".join(ast.dump(n) for n in statements)
    assert "headers" not in body, body


def test_no_source_logs_a_material_expression():
    banned_args = ("headers", "_headers", "signature", "secret", "secret_key",
                   "secret_key_b64", "caller_token", "token")
    levels = ("debug", "info", "warning", "error", "exception", "critical")
    offenders = []
    for path in _sources():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = _dotted(node.func)
            if not any(fn.endswith(f".{lvl}") for lvl in levels):
                continue
            for arg in node.args + [k.value for k in node.keywords]:
                name = _dotted(arg) if isinstance(arg, (ast.Name, ast.Attribute)) else ""
                if name.split(".")[-1] in banned_args:
                    offenders.append(f"{path.name}: logs {name}")
                if isinstance(arg, ast.JoinedStr):
                    for v in ast.walk(arg):
                        if isinstance(v, (ast.Name, ast.Attribute)):
                            n2 = _dotted(v).split(".")[-1]
                            if n2 in banned_args:
                                offenders.append(f"{path.name}: f-string {n2}")
    assert not offenders, "material reaches a log call:\n  " + "\n  ".join(offenders)


# =====================================================================
# THE REDACTING REPRS, exercised rather than assumed
# =====================================================================
def test_both_material_carrying_objects_redact_themselves(caplog):
    from pmusbroker import mint

    from sportsassets.obs import handshake

    KEY = "00000000-0000-4000-8000-000000000001"
    SECRET = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
    minted = mint.mint_market_ws_headers(key_id=KEY, secret_key_b64=SECRET,
                                         consumer="audit")
    sig = minted.headers[mint.SIGNATURE_HEADER]
    material = handshake.HandshakeMaterial(
        mint_id="m1", capability_id="PMUS_MARKET_WS_HANDSHAKE",
        minted_at_wall_ms=0, advisory_max_age_ms=2000,
        _headers=dict(minted.headers))

    for obj in (minted, material):
        for rendered in (str(obj), repr(obj), f"{obj}", f"{obj!r}"):
            assert sig not in rendered
            assert SECRET not in rendered
            assert "<redacted>" in rendered

    with caplog.at_level(logging.DEBUG):
        logging.getLogger("audit").debug("%s %r %s", minted, material, minted)
        # structured/json rendering of the same objects
        logging.getLogger("audit").info(
            json.dumps({"minted": str(minted), "material": repr(material)}))
    assert sig not in caplog.text
    assert SECRET not in caplog.text


def test_an_exception_carrying_the_objects_does_not_render_material():
    from pmusbroker import mint

    minted = mint.mint_market_ws_headers(
        key_id="k", secret_key_b64="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
        consumer="audit")
    sig = minted.headers[mint.SIGNATURE_HEADER]
    try:
        raise RuntimeError(f"failed with {minted}")
    except RuntimeError as exc:
        assert sig not in str(exc)
        assert sig not in repr(exc)


def test_the_mint_ledger_stores_metadata_only():
    from pmusbroker import ledger, mint

    minted = mint.mint_market_ws_headers(
        key_id="k", secret_key_b64="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
        consumer="audit")
    rec = ledger.MintLedger().record(minted)
    assert set(rec.__dataclass_fields__) == {
        "mint_id", "minted_at_wall_ms", "capability_id", "consumer"}
    blob = json.dumps(rec.__dict__)
    assert minted.headers[mint.SIGNATURE_HEADER] not in blob
    assert "X-PM" not in blob


# =====================================================================
# PERSISTED ROWS
# =====================================================================
def test_no_run83_migration_persists_authentication_material():
    banned = ("signature", "x_pm_", "x-pm-", "access_key", "secret",
              "auth_header", "caller_token", "bearer")
    offenders = []
    for path in sorted((BACKEND / "migrations").glob("06*.sql")):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            low = line.lower()
            if low.lstrip().startswith("--"):
                continue
            if "add column" in low or "create table" in low or (
                    low.strip() and low.strip()[0].isalpha() and "text" in low):
                for frag in banned:
                    if frag in low:
                        offenders.append(f"{path.name}:{i}: {line.strip()[:70]}")
    assert not offenders, ("a run 83 table persists authentication material:\n  "
                           + "\n  ".join(offenders))
    # mint_id is retained, deliberately.
    m064 = (BACKEND / "migrations" / "064_run833_stream_channels.sql").read_text()
    assert "handshake_mint_id" in m064

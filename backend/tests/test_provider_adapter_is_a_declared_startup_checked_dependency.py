"""THE PROVIDER SDK IS A DECLARED PRODUCTION DEPENDENCY, AND THE SERVICE
CHECKS THE REAL ADAPTER AT STARTUP.

* pyproject's [project].dependencies -- what the image's `pip install .`
  installs -- names the Anthropic SDK (not only a dev extra).
* `audrey_chat.adapter_startup_check()` imports the installed SDK, records
  its version and import location, confirms the adapter's call exists and
  constructs the client as `make_client` does -- with the network refused, so
  it provably sends nothing and reads no credential.
* The API's lifespan runs it, and `provider_status` serves the result.
"""
from __future__ import annotations

import inspect
import os
import pathlib
import socket
import tomllib

from sportsassets.agents import audrey_chat as AC

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def test_the_sdk_is_a_production_dependency():
    doc = tomllib.loads((BACKEND / "pyproject.toml").read_text())
    deps = doc["project"]["dependencies"]
    assert any(d.replace(" ", "").startswith("anthropic>=1.9.0") for d in deps)


def test_the_startup_check_imports_and_builds_the_real_adapter(monkeypatch):
    def _no_network(*a, **k):
        raise AssertionError("the startup check opened a connection")
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    got = AC.adapter_startup_check()
    assert got["ok"] is True, got
    assert got["request_sent"] is False
    assert got["client_class"] == "AsyncAnthropic"
    assert got["beta_messages_create"] is True
    major, minor = (int(x) for x in got["sdk_version"].split(".")[:2])
    assert (major, minor) >= (1, 9) and major < 2
    assert os.path.isdir(got["sdk_location"])
    assert got["http_layer"]["package"] == "httpx2"
    st = AC.provider_status()
    assert st["adapter_startup_check"]["sdk_version"] == got["sdk_version"]
    assert "placeholder" not in str(st)          # nothing key-like is served


def test_a_missing_sdk_is_named_not_raised(monkeypatch):
    import builtins
    real = builtins.__import__

    def _imp(name, *a, **k):
        if name == "anthropic" or name.startswith("anthropic."):
            raise ImportError("not installed (test)")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", _imp)
    got = AC.adapter_startup_check()
    assert got["ok"] is False
    assert got["reason"] == "ADAPTER_IMPORT_OR_CONSTRUCTION_FAILED"
    assert got["error"] == "ImportError"


def test_the_lifespan_runs_the_check():
    from sportsassets.api import app as A
    assert "adapter_startup_check()" in inspect.getsource(A.lifespan)

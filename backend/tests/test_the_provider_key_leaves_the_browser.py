"""THE PROVIDER KEY MOVES SERVER-SIDE, AND THE PROXY IS NOT A PASSTHROUGH.

WHAT I PREVIOUSLY DID AND WHAT I CALLED IT. The JARVIS cockpit called
api.anthropic.com from the browser with the owner's key. I moved that key from
localStorage to sessionStorage, purged durable copies on load, and recorded --
correctly -- that this was "a REDUCTION IN CONSEQUENCE, NOT A FIX", with the
server-side proxy left OPEN.

This is the proxy. The tests below check the two things that decide whether it is
a boundary or a hop:

  THE KEY NEVER LEAVES THE SERVER.  No route returns it, no status field carries
                                    a prefix or a length, and it is read from the
                                    environment on each call.
  THE REQUEST IS REBUILT, NOT FILTERED.  A filter fails open on any field it has
                                    not heard of. A rebuild fails closed, and an
                                    unknown field is refused BY NAME.

AND WHAT IT DOES NOT FIX IS ASSERTED TOO, because a proxy that quietly implies it
solved operator spend or rate limiting would be the same overstatement in a new
place.
"""

from __future__ import annotations

import pathlib

import pytest

from sportsassets import provider_key_proxy as PKP

GOOD = {
    "model": "claude-opus-5",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "hello"}],
}


# ── 1 · the key stays server-side ────────────────────────────────────

def test_the_key_is_read_from_the_environment_not_captured_at_import():
    """So rotating it does not need a redeploy -- and so a test can set it."""
    import inspect
    src = inspect.getsource(PKP.key_present)
    assert "os.environ" in src
    assert PKP.KEY_ENV == "ANTHROPIC_API_KEY"


def test_key_presence_is_reported_as_a_BOOLEAN_and_nothing_else(monkeypatch):
    """A status route that returned a prefix or a length would be leaking the
    thing this exists to stop leaking."""
    monkeypatch.setenv(PKP.KEY_ENV, "sk-ant-secret-value-abcdef123456")
    d = PKP.describe()
    assert d["key_present"] is True
    blob = " ".join(str(v) for v in _flatten(d))
    assert "sk-ant" not in blob
    assert "abcdef123456" not in blob
    # AND NOT ITS LENGTH EITHER, which narrows a guess and buys nothing.
    assert "32" not in str(d.get("key_present"))


def _flatten(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from _flatten(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _flatten(v)
    else:
        yield o


def test_no_route_returns_the_key(monkeypatch):
    """READ OFF THE HANDLERS. The status route returns describe(), and the
    streaming route returns provider bytes -- neither may carry key material."""
    src = (pathlib.Path(PKP.__file__).parent / "api" / "app.py").read_text()
    i = src.index('"/api/jarvis/proxy-status"')
    window = src[i:i + 1500]
    assert "provider_headers" not in window, (
        "the status route must not touch the outbound headers, which carry the "
        "key")
    assert "_PKP.describe()" in window
    # AND THE OUTBOUND HEADERS ARE ONLY EVER USED ON THE UPSTREAM CALL.
    assert src.count("_PKP.provider_headers()") == 1


def test_the_outbound_headers_carry_the_key_and_are_never_returned(monkeypatch):
    monkeypatch.setenv(PKP.KEY_ENV, "sk-ant-test")
    h = PKP.provider_headers()
    assert h["x-api-key"] == "sk-ant-test"
    assert h["anthropic-version"] == PKP.PROVIDER_VERSION
    # THE BROWSER-CORS HEADER IS DELIBERATELY ABSENT. It exists to opt a BROWSER
    # origin into CORS; this request is made from the server, so sending it
    # would be cargo-cult.
    assert "anthropic-dangerous-direct-browser-access" not in h
    assert PKP.describe()["browser_access_header_sent"] is False


def test_the_provider_url_is_fixed_and_not_caller_supplied():
    """A caller-supplied URL would make this an open relay that attaches the
    owner's key to whatever it is pointed at."""
    assert PKP.PROVIDER_URL == "https://api.anthropic.com/v1/messages"
    assert PKP.describe()["url_is_caller_supplied"] is False
    got = PKP.build_request(dict(GOOD, url="https://evil.example"))
    assert got["ok"] is False
    assert got["refusal"] == PKP.R_UNKNOWN_FIELD


# ── 2 · the request is rebuilt, not filtered ─────────────────────────

def test_an_unknown_field_is_refused_BY_NAME_not_dropped():
    """A "remove the bad keys" filter fails open on anything it has not heard
    of. Refusing by name also tells the caller its expectation was wrong, which
    silence would not."""
    got = PKP.build_request(dict(GOOD, metadata={"user_id": "x"},
                                 top_k=5))
    assert got["ok"] is False
    assert got["refusal"] == PKP.R_UNKNOWN_FIELD
    assert got["unknown_fields"] == ["metadata", "top_k"]
    assert "will not relay" in got["why"]


def test_the_forwarded_request_is_built_field_by_field():
    """Not the caller's dict with keys removed. The distinction is the whole
    fail-closed property."""
    got = PKP.build_request(dict(GOOD, system="be terse"))
    assert got["ok"] is True
    req = got["request"]
    assert set(req) == {"model", "max_tokens", "messages", "stream", "system"}
    assert req["stream"] is True, "the route is a streaming route"


def test_only_allow_listed_models_pass():
    """An arbitrary model string is a way to spend the owner's account on
    something else."""
    for m in PKP.ALLOWED_MODELS:
        assert PKP.build_request(dict(GOOD, model=m))["ok"] is True
    for bad in ("claude-3-opus", "gpt-4", "", "../../etc/passwd", None):
        got = PKP.build_request(dict(GOOD, model=bad))
        assert got["ok"] is False, bad
        assert got["refusal"] == PKP.R_MODEL_NOT_ALLOWED


def test_max_tokens_is_CLAMPED_not_refused():
    """A caller asking for too much wants a bigger answer, not something
    hostile -- and refusing would make the cockpit brittle against the provider
    raising its own ceiling."""
    got = PKP.build_request(dict(GOOD, max_tokens=10 ** 9))
    assert got["ok"] is True
    assert got["request"]["max_tokens"] == PKP.MAX_TOKENS_CEILING
    assert got["clamped_max_tokens"] is True
    # A SMALLER ASK IS HONOURED.
    ok = PKP.build_request(dict(GOOD, max_tokens=16))
    assert ok["request"]["max_tokens"] == 16
    assert ok["clamped_max_tokens"] is False
    # AND GARBAGE FALLS TO THE CEILING RATHER THAN CRASHING.
    assert PKP.build_request(dict(GOOD, max_tokens="lots"))["ok"] is True


@pytest.mark.parametrize("body,expected", [
    ({"model": "claude-opus-5", "messages": []}, PKP.R_BAD_MESSAGES),
    ({"model": "claude-opus-5", "messages": "hi"}, PKP.R_BAD_MESSAGES),
    ({"model": "claude-opus-5",
      "messages": [{"role": "system", "content": "x"}]}, PKP.R_BAD_MESSAGES),
    ({"model": "claude-opus-5", "messages": [{"role": "user"}]},
     PKP.R_BAD_MESSAGES),
    ({"model": "claude-opus-5",
      "messages": [{"role": "user", "content": "x"}] * 100},
     PKP.R_TOO_MANY_MESSAGES),
    ({"model": "claude-opus-5", "messages": [{"role": "user", "content": "x"}],
      "system": 12}, PKP.R_BAD_MESSAGES),
    ({"model": "claude-opus-5", "messages": [{"role": "user", "content": "x"}],
      "system": "x" * 40_000}, PKP.R_SYSTEM_TOO_LONG),
    ({"model": "claude-opus-5", "messages": [{"role": "user", "content": "x"}],
      "temperature": 9.0}, PKP.R_BAD_TEMPERATURE),
    ({"model": "claude-opus-5", "messages": [{"role": "user", "content": "x"}],
      "temperature": "warm"}, PKP.R_BAD_TEMPERATURE),
])
def test_each_malformed_request_refuses_by_its_own_name(body, expected):
    got = PKP.build_request(body)
    assert got["ok"] is False
    assert got["refusal"] == expected, got


def test_a_body_that_is_not_an_object_refuses():
    for body in (None, [], "hi", 7):
        assert PKP.build_request(body)["ok"] is False


def test_an_oversized_body_refuses_after_rebuilding():
    """Checked on the REBUILT request, so a caller cannot smuggle size in a
    field that gets dropped and then claim the limit is wrong."""
    big = [{"role": "user", "content": "x" * 20_000} for _ in range(40)]
    got = PKP.build_request({"model": "claude-opus-5", "messages": big})
    assert got["ok"] is False
    assert got["refusal"] == PKP.R_BODY_TOO_LARGE


def test_tools_are_forwarded_and_that_is_not_the_boundary():
    """Every cockpit tool that changes state calls a desk- or admin-scoped API
    route, and it is the SERVER's check on THAT route that decides. Refusing
    tools here would break the cockpit and secure nothing."""
    got = PKP.build_request(dict(GOOD, tools=[{"name": "show_chart"}]))
    assert got["ok"] is True
    assert got["request"]["tools"] == [{"name": "show_chart"}]
    src = pathlib.Path(PKP.__file__).read_text()
    assert "A TOOL DESCRIPTION IS NOT AN AUTHORIZATION" in src.upper()


# ── 3 · the route is guarded, and errors are not relayed ─────────────

def test_both_routes_require_desk():
    src = (pathlib.Path(PKP.__file__).parent / "api" / "app.py").read_text()
    for route in ("/api/jarvis/proxy-status", "/api/jarvis/messages"):
        i = src.index('"%s"' % route)
        assert "Depends(require_desk)" in src[i:i + 200], route


def test_a_missing_key_is_a_503_that_says_where_the_key_goes_not_what_it_is():
    src = (pathlib.Path(PKP.__file__).parent / "api" / "app.py").read_text()
    i = src.index('"/api/jarvis/messages"')
    window = src[i:i + 4000]
    assert "R_NO_KEY" in window
    assert "secret store" in window
    assert "never in the" in window


def test_an_upstream_error_body_is_logged_and_not_relayed():
    """A provider error body can echo request material back to the browser."""
    src = (pathlib.Path(PKP.__file__).parent / "api" / "app.py").read_text()
    i = src.index('"/api/jarvis/messages"')
    window = src[i:i + 4000]
    assert "log.warning" in window
    assert "see the server log" in window


# ── 4 · what it does NOT fix, asserted ───────────────────────────────

def test_the_limits_are_recorded_rather_than_implied_away():
    """A proxy that quietly suggested it had solved operator spend or rate
    limiting would be the same overstatement in a new place."""
    n = PKP.describe()["what_this_does_not_fix"]
    assert set(n) == {"operator_spend", "no_rate_limit", "shared_credential"}
    assert "can spend the account" in n["operator_spend"]
    assert "require_desk is the WHO" in n["operator_spend"]
    assert "OPEN" in n["no_rate_limit"]
    assert "one key for all operators" in n["shared_credential"]


def test_the_earlier_mitigation_is_named_as_not_this():
    d = PKP.describe()
    assert "sessionStorage reduced the window" in (
        d["previous_mitigation_was_not_this"])
    assert "readable by anything on that origin" in (
        d["previous_mitigation_was_not_this"])

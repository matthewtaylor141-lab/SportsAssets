"""THE PROVIDER-KEY PROXY. The key stops living in the browser.

WHAT THIS REPLACES, AND THE PREVIOUS "FIX" WAS A REDUCTION IN CONSEQUENCE RATHER
THAN A REPAIR. The JARVIS cockpit called `api.anthropic.com` directly from the
browser with the owner's own key, using the provider's own
`anthropic-dangerous-direct-browser-access` header -- a header whose name says
what it is. Audit finding A6.

I moved the key from localStorage to sessionStorage and purged durable copies on
load. That helps: a credential typed months ago no longer sits there waiting for
one script injection. IT IS NOT A FIX. A provider key in the browser at all is
readable by anything running on that origin for as long as the tab is open, and
the repair was recorded as OPEN rather than implied away.

THIS IS THE REPAIR. The key lives in the backend's environment. The browser gets
a scoped endpoint and never sees key material.

WHAT "SCOPED" MEANS HERE, CONCRETELY. A proxy that forwards whatever it is given
is not a security boundary -- it is the same key reachable by anyone who can
reach the route, plus a hop. So the request is REBUILT from validated fields
rather than passed through:

  MODEL         must be one of a fixed allow-list. An arbitrary model string is
                a way to spend the owner's account on something else.
  MAX_TOKENS    capped server-side. A caller cannot ask for a million.
  MESSAGES      shape-checked, and bounded in count and total size.
  SYSTEM        a string, bounded.
  TOOLS         forwarded, because the cockpit's tool loop needs them -- and
                every tool that changes state calls a desk- or admin-scoped API
                route, so it is the SERVER's check on THAT route that decides.
                A tool description has never been the authorization boundary.
  EVERYTHING ELSE  DROPPED. Not forwarded, not merged, not defaulted. An
                unknown field is refused by name rather than relayed, because a
                passthrough proxy inherits every future provider feature as an
                attack surface.

AND THE ROUTE IS DESK-GUARDED. The key is the owner's; reaching it requires the
same credential that reaches the rest of the desk. That does not make the key
un-exfiltratable by an operator who already has desk access -- it makes it
un-exfiltratable by ANYTHING ELSE RUNNING IN THE BROWSER, which is the actual
finding.

WHAT THIS DOES NOT FIX, STATED SO IT IS NOT IMPLIED AWAY.

  * An operator with desk access can still spend the account through this route.
    That is what the route is for. The proxy bounds WHAT can be asked, not WHO --
    and `require_desk` is the who.
  * Nothing here rate-limits spend. A caller with desk access can loop. That is
    an OPEN item, not something this module quietly covers.
  * The key is still a single shared credential rather than a per-operator one,
    because the provider issues it that way.
"""

from __future__ import annotations

import os

#: The environment variable holding the key. Read on every call rather than
#: captured at import, so rotating it does not need a redeploy.
KEY_ENV = "ANTHROPIC_API_KEY"

#: The provider endpoint. Fixed, not caller-supplied -- a caller-supplied URL
#: would make this an open relay that happens to attach the owner's key.
PROVIDER_URL = "https://api.anthropic.com/v1/messages"
PROVIDER_VERSION = "2023-06-01"

#: THE MODEL ALLOW-LIST. Exactly the cockpit's fallback chain and nothing else.
ALLOWED_MODELS = (
    "claude-fable-5",
    "claude-opus-5",
    "claude-sonnet-5",
)

#: Server-side ceilings. A caller may ask for less and never for more.
MAX_TOKENS_CEILING = 8192
MAX_MESSAGES = 64
MAX_BODY_BYTES = 512 * 1024
MAX_SYSTEM_CHARS = 32_000

#: The ONLY fields forwarded. Anything else is refused by name.
FORWARDED_FIELDS = ("model", "max_tokens", "messages", "system", "tools",
                    "temperature", "stream")

R_NO_KEY = "PROVIDER_KEY_NOT_CONFIGURED"
R_MODEL_NOT_ALLOWED = "MODEL_IS_NOT_ON_THE_ALLOW_LIST"
R_UNKNOWN_FIELD = "REQUEST_CARRIES_A_FIELD_THIS_PROXY_DOES_NOT_FORWARD"
R_BAD_MESSAGES = "MESSAGES_ARE_MISSING_OR_MALFORMED"
R_TOO_MANY_MESSAGES = "TOO_MANY_MESSAGES"
R_BODY_TOO_LARGE = "REQUEST_BODY_IS_TOO_LARGE"
R_SYSTEM_TOO_LONG = "SYSTEM_PROMPT_IS_TOO_LONG"
R_BAD_TEMPERATURE = "TEMPERATURE_IS_OUT_OF_RANGE"


def key_present() -> bool:
    return bool((os.environ.get(KEY_ENV) or "").strip())


def provider_headers() -> dict:
    """The outbound headers, INCLUDING the key. Never returned to a caller.

    `anthropic-dangerous-direct-browser-access` is deliberately ABSENT: that
    header exists to opt a browser origin into CORS, and this request is made
    from the server. Sending it here would be cargo-cult.
    """
    return {
        "x-api-key": (os.environ.get(KEY_ENV) or "").strip(),
        "anthropic-version": PROVIDER_VERSION,
        "content-type": "application/json",
    }


def build_request(body: dict) -> dict:
    """VALIDATE AND REBUILD. Returns `{"ok", "refusal", "why", "request"}`.

    The returned request is constructed field by field from validated input. It
    is not the caller's dict with some keys removed -- that distinction matters,
    because a "remove the bad keys" filter fails open on anything it has not
    heard of, and a rebuild fails closed.
    """
    out = {"ok": False, "refusal": None, "why": None, "request": None}
    if not isinstance(body, dict):
        return dict(out, refusal=R_BAD_MESSAGES, why="the body is not an object")

    # UNKNOWN FIELDS ARE REFUSED, NOT DROPPED SILENTLY. A caller sending
    # something this proxy does not forward has a wrong expectation about what
    # will happen, and silently ignoring it is how that becomes a bug later.
    extra = sorted(set(body) - set(FORWARDED_FIELDS))
    if extra:
        return dict(out, refusal=R_UNKNOWN_FIELD,
                    why=("this proxy forwards only %s. It will not relay: %s"
                         % (", ".join(FORWARDED_FIELDS), ", ".join(extra))),
                    unknown_fields=extra)

    model = str(body.get("model") or "")
    if model not in ALLOWED_MODELS:
        return dict(out, refusal=R_MODEL_NOT_ALLOWED,
                    why=("%r is not on the allow-list %s. An arbitrary model "
                         "string is a way to spend the owner's account on "
                         "something else" % (model, list(ALLOWED_MODELS))))

    msgs = body.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return dict(out, refusal=R_BAD_MESSAGES,
                    why="`messages` must be a non-empty list")
    if len(msgs) > MAX_MESSAGES:
        return dict(out, refusal=R_TOO_MANY_MESSAGES,
                    why="%d messages against a %d limit"
                        % (len(msgs), MAX_MESSAGES))
    for i, m in enumerate(msgs):
        if not isinstance(m, dict) or m.get("role") not in ("user",
                                                            "assistant"):
            return dict(out, refusal=R_BAD_MESSAGES,
                        why=("message %d has no usable role; only 'user' and "
                             "'assistant' are forwarded" % i))
        if "content" not in m:
            return dict(out, refusal=R_BAD_MESSAGES,
                        why="message %d has no content" % i)

    # `system` TAKES BOTH SHAPES THE PROVIDER ACCEPTS, because the cockpit needs
    # the second one. A plain string is the simple form; a list of text blocks is
    # what prompt caching requires -- the cockpit puts a `cache_control`
    # breakpoint on the system block so rounds 2..N of a tool turn read the
    # cache instead of reprocessing the whole prefix.
    #
    # REFUSING THE LIST FORM WOULD HAVE BROKEN THE COCKPIT AND SECURED NOTHING,
    # and I wrote the first version that way. Both forms are bounded on the
    # total text, which is the thing worth bounding.
    system = body.get("system")
    system_text = ""
    if system is None:
        pass
    elif isinstance(system, str):
        system_text = system
    elif isinstance(system, list):
        for i, blk in enumerate(system):
            if not isinstance(blk, dict) or blk.get("type") != "text":
                return dict(out, refusal=R_BAD_MESSAGES,
                            why=("system block %d is not a text block. Only "
                                 "`{type: 'text', text: ...}` blocks are "
                                 "forwarded" % i))
            system_text += str(blk.get("text") or "")
    else:
        return dict(out, refusal=R_BAD_MESSAGES,
                    why=("`system` must be a string or a list of text blocks "
                         "when supplied"))
    if len(system_text) > MAX_SYSTEM_CHARS:
        return dict(out, refusal=R_SYSTEM_TOO_LONG,
                    why="%d characters against a %d limit"
                        % (len(system_text), MAX_SYSTEM_CHARS))

    temp = body.get("temperature")
    if temp is not None:
        try:
            temp = float(temp)
        except (TypeError, ValueError):
            return dict(out, refusal=R_BAD_TEMPERATURE,
                        why="`temperature` is not a number")
        if not 0.0 <= temp <= 1.0:
            return dict(out, refusal=R_BAD_TEMPERATURE,
                        why="`temperature` %r is outside 0.0-1.0" % temp)

    # MAX_TOKENS IS CLAMPED, NOT REFUSED. A caller asking for too much is asking
    # for a bigger answer, not doing something hostile, and refusing would make
    # the cockpit brittle against a provider raising its own ceiling.
    try:
        want = int(body.get("max_tokens") or MAX_TOKENS_CEILING)
    except (TypeError, ValueError):
        want = MAX_TOKENS_CEILING
    max_tokens = max(1, min(want, MAX_TOKENS_CEILING))

    req = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": msgs,
        # STREAMING IS THE PROXY'S DECISION, not the caller's: the route is a
        # streaming route, so the upstream request always streams.
        "stream": True,
    }
    if system is not None and system_text:
        # FORWARDED IN THE SHAPE IT ARRIVED, so a cache_control breakpoint
        # survives. Validated above; not reshaped.
        req["system"] = system
    if isinstance(body.get("tools"), list) and body["tools"]:
        # TOOLS ARE FORWARDED, AND A TOOL DESCRIPTION IS NOT AN AUTHORIZATION
        # BOUNDARY. Every cockpit tool that changes state calls a desk- or
        # admin-scoped API route, and it is the SERVER's check on that route
        # that decides. Refusing tools here would break the cockpit and secure
        # nothing.
        req["tools"] = body["tools"]
    if temp is not None:
        req["temperature"] = temp

    import json
    size = len(json.dumps(req).encode("utf-8"))
    if size > MAX_BODY_BYTES:
        return dict(out, refusal=R_BODY_TOO_LARGE,
                    why="%d bytes against a %d limit" % (size, MAX_BODY_BYTES))

    return {"ok": True, "refusal": None, "why": None, "request": req,
            "clamped_max_tokens": max_tokens != want,
            "bytes": size}


def describe() -> dict:
    return {
        "purpose": ("hold the provider key server-side so the browser never "
                    "sees it. Audit finding A6"),
        "key_env": KEY_ENV,
        "key_present": key_present(),
        "provider_url": PROVIDER_URL,
        "url_is_caller_supplied": False,
        "why_the_url_is_fixed": (
            "a caller-supplied URL would make this an open relay that attaches "
            "the owner's key to whatever it is pointed at"),
        "allowed_models": list(ALLOWED_MODELS),
        "max_tokens_ceiling": MAX_TOKENS_CEILING,
        "forwarded_fields": list(FORWARDED_FIELDS),
        "unknown_fields_are": "REFUSED BY NAME, not dropped silently",
        "requests_are": ("REBUILT from validated fields, not filtered. A "
                         "filter fails open on anything it has not heard of"),
        "browser_access_header_sent": False,
        "why_not": ("anthropic-dangerous-direct-browser-access opts a BROWSER "
                    "origin into CORS. This request is made from the server"),
        "what_this_does_not_fix": {
            "operator_spend": ("an operator with desk access can spend the "
                               "account through this route. That is what the "
                               "route is for: the proxy bounds WHAT can be "
                               "asked, and require_desk is the WHO"),
            "no_rate_limit": ("nothing here limits spend. A caller with desk "
                              "access can loop. OPEN, and not covered by this "
                              "module"),
            "shared_credential": ("one key for all operators, because the "
                                  "provider issues it that way"),
        },
        "previous_mitigation_was_not_this": (
            "moving the key from localStorage to sessionStorage reduced the "
            "window. A provider key in the browser at all is readable by "
            "anything on that origin while the tab is open"),
    }

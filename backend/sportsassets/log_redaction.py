"""SECRET-BEARING URLS NEVER REACH A LOG LINE (2026-10-08).

Found in production on sportsassets-workers (08828d04): httpx's per-request
INFO line ("HTTP Request: POST https://polygon-mainnet.g.alchemy.com/v2/
<key> ...") printed the Polygon RPC provider URL with its API key in the
PATH. The workers keep httpx at INFO on purpose (venue outages are read
from those lines, api/__init__.py), so the line stays and the credential
goes: a filter on every root handler rewrites the rendered message before
it is emitted.

What is redacted:
  * the exact configured values of the URL-carried credentials
    (POLYGON_HTTP_URL, POLYGON_WS_URL -- comma-separated rotation lists):
    each URL's path and query are replaced, the scheme and host kept;
  * any URL on a known RPC-provider host (the key is the path there);
  * credential-named query parameters on ANY URL (api_key, apikey, key,
    token, access_token, secret, signature, auth).
Venue URLs (Polymarket, Kalshi, Pinnacle ...) keep their paths: slugs and
tickers are how an outage is read. Never raises; a record it cannot render
is passed through untouched.
"""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "REDACTED"
URL_CREDENTIAL_ENVS = ("POLYGON_HTTP_URL", "POLYGON_WS_URL")
RPC_HOST_SUFFIXES = ("alchemy.com", "infura.io", "quiknode.pro",
                     "quicknode.pro", "ankr.com", "chainstack.com",
                     "blastapi.io", "drpc.org", "getblock.io",
                     "nodereal.io", "moralis-nodes.com")
SECRET_PARAMS = frozenset({"api_key", "apikey", "key", "token",
                           "access_token", "secret", "signature", "auth",
                           "x-api-key"})
_URL = re.compile(r"(?:https?|wss?)://[^\s\"'<>]+")


def _rpc_host(host: str) -> bool:
    h = (host or "").lower()
    return any(h == s or h.endswith("." + s) for s in RPC_HOST_SUFFIXES)


def _configured_prefixes(configured: frozenset) -> set:
    out = set()
    for c in configured:
        p = urlsplit(c)
        if p.netloc:
            out.add((p.netloc.lower(), p.path or "/"))
    return out


def redact_url(url: str, configured: frozenset = frozenset()) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    base = "%s://%s" % (parts.scheme, parts.netloc)
    for netloc, path in _configured_prefixes(configured):
        if parts.netloc.lower() == netloc and (parts.path or "/").startswith(
                path):
            return "%s/%s" % (base, REDACTED)
    if _rpc_host(parts.hostname or ""):
        return "%s/%s" % (base, REDACTED)
    if parts.query:
        q = [(k, REDACTED if k.lower() in SECRET_PARAMS else v)
             for k, v in parse_qsl(parts.query, keep_blank_values=True)]
        return urlunsplit((parts.scheme, parts.netloc, parts.path,
                           urlencode(q), parts.fragment))
    return url


def configured_urls(env=None) -> frozenset:
    env = os.environ if env is None else env
    out = set()
    for name in URL_CREDENTIAL_ENVS:
        for u in str(env.get(name) or "").split(","):
            u = u.strip()
            if u:
                out.add(u)
    return frozenset(out)


def redact_text(text: str, configured: frozenset = frozenset()) -> str:
    for c in configured:                 # the exact value, wherever it is
        if c and c in text:
            p = urlsplit(c)
            text = text.replace(c, "%s://%s/%s" % (p.scheme, p.netloc,
                                                   REDACTED))
    return _URL.sub(lambda m: redact_url(m.group(0), configured), text)


class SecretURLFilter(logging.Filter):
    def __init__(self, env=None):
        super().__init__()
        self.configured = configured_urls(env)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            red = redact_text(msg, self.configured)
            if red != msg:
                record.msg, record.args = red, None
        except Exception:                                       # noqa: BLE001
            pass
        return True


def install(root: logging.Logger | None = None, env=None) -> int:
    """Attach the filter to every handler of the root logger (where
    basicConfig put the process's one handler). Idempotent."""
    root = root or logging.getLogger()
    n = 0
    for h in root.handlers:
        if not any(isinstance(f, SecretURLFilter) for f in h.filters):
            h.addFilter(SecretURLFilter(env))
            n += 1
    return n

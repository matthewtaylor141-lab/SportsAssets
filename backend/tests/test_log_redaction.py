"""Secret-bearing URLs never reach a log line (production 2026-10-08: the
workers' httpx INFO line printed the Polygon RPC URL with its API key in the
path). The line stays -- venue outages are read from it -- the key goes."""
from __future__ import annotations

import logging

from sportsassets import log_redaction as LR

KEY = "Zq8mK2pX7vN4tR9wY1cB6dF3gH5jL0aS"          # synthetic, not a real key


def _emit(msg, *args, env=None):
    rec = logging.LogRecord("httpx", logging.INFO, __file__, 1, msg, args,
                            None)
    LR.SecretURLFilter(env or {}).filter(rec)
    return rec.getMessage()


def test_the_production_shape_is_redacted_and_the_line_kept():
    out = _emit('HTTP Request: %s %s "%s %d %s"', "POST",
                "https://polygon-mainnet.g.alchemy.com/v2/" + KEY,
                "HTTP/1.1", 200, "OK")
    assert KEY not in out
    assert "https://polygon-mainnet.g.alchemy.com/REDACTED" in out
    assert out.startswith("HTTP Request: POST") and "200 OK" in out


def test_a_configured_rpc_url_is_redacted_on_any_host():
    url = "https://rpc.example-node.net/private/" + KEY
    env = {"POLYGON_HTTP_URL": url,
           "POLYGON_WS_URL": "wss://a.example.org/ws/" + KEY + ",wss://b.x"}
    out = _emit("calling %s then %s", url, "wss://a.example.org/ws/" + KEY,
                env=env)
    assert KEY not in out
    assert "https://rpc.example-node.net/REDACTED" in out


def test_credential_query_parameters_are_redacted_everywhere():
    out = _emit("GET https://api.example.com/v1/x?api_key=%s&limit=5" % KEY)
    assert KEY not in out and "limit=5" in out and "api_key=REDACTED" in out


def test_venue_urls_keep_their_paths():
    u = ("https://gateway.polymarket.us/v1/markets/aec-nba-phi-bkn-2026-10-08"
         "/book")
    k = "https://api.elections.kalshi.com/trade-api/v2/markets/KXMLBGAME-X"
    assert _emit("GET %s", u) == "GET " + u
    assert _emit("GET %s", k) == "GET " + k


def test_both_processes_install_it_on_their_root_handlers():
    import pathlib
    root = pathlib.Path(LR.__file__).resolve().parent
    assert "_log_redaction.install()" in (root / "workers" /
                                         "__init__.py").read_text()
    assert "_log_redaction.install()" in (root / "api" / "app.py").read_text()
    lg = logging.Logger("t")
    h = logging.StreamHandler()
    lg.addHandler(h)
    assert LR.install(lg, env={}) == 1 and LR.install(lg, env={}) == 0


def test_a_record_it_cannot_render_passes_through():
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "%d", ("x",),
                            None)
    assert LR.SecretURLFilter({}).filter(rec) is True

"""THE DEDICATED MARKET-PLANE RUNTIME HOLDS NO ORDER AUTHORITY (closeout).

Owner decision 2026-10-06: ~74,500 active markets are not solved by raising
UMP_MAX_STREAMS inside the shared workers; the market plane runs as its own
read-only Render service, specified in ops/render_market_plane_service.yaml
(NOT render.yaml: a Blueprint change would redeploy the collector). That
service receives the database and the PMX
institutional MARKET-DATA credential only -- never a PMUS key, a Kalshi key,
the live-trading switch or the admin token."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = ("PMUS_KEY_ID", "PMUS_SECRET_KEY", "PM_PRIVATE_KEY", "PM_FUNDER",
             "LIVE_TRADING_ENABLED", "ADMIN_TOKEN", "ENGINE_INGEST_TOKEN",
             "FUNDED", "EXECMIRROR",
             # Kalshi: every trading switch stays forbidden; only the two
             # key variables the read-only WebSocket handshake needs are
             # allowed (Kalshi rep production contract 2026-10-07)
             "KALSHI_SMALLLIVE", "KALSHI_LIVE", "KALSHI_ENV",
             "KALSHI_PRIVATE_KEY_PATH", "KALSHI_ORDER", "KALSHI_SUBMIT")
KALSHI_READ_ONLY_KEYS = {"KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PEM"}


SPEC = ROOT / "ops" / "render_market_plane_service.yaml"


def _svc(name):
    doc = yaml.safe_load(SPEC.read_text())
    for s in doc["services"]:
        if s.get("name") == name:
            return s
    raise AssertionError("service %s not in render.yaml" % name)


def test_the_dedicated_runtime_runs_the_market_plane_read_only():
    s = _svc("sportsassets-market-plane")
    assert s["type"] == "worker"
    assert s["dockerCommand"] == (
        "python -m sportsassets.workers.universal_market_plane")
    env = {e["key"]: e for e in s["envVars"]}
    assert env["UMP_RUNTIME"]["value"] == "DEDICATED_READ_ONLY"
    assert set(env) >= {"DATABASE_URL", "PMX_CLIENT_ID", "PMX_KEY_ID",
                        "PMX_PRIVATE_KEY_B64"}
    for k in env:
        assert not any(f in k for f in FORBIDDEN), k
        assert not k.startswith("KALSHI") or k in KALSHI_READ_ONLY_KEYS, k
    # secrets are entered by the owner, never written in the blueprint
    for k in ("PMX_CLIENT_ID", "PMX_KEY_ID", "PMX_PRIVATE_KEY_B64",
              "KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PEM"):
        assert env[k].get("sync") is False and "value" not in env[k]


def test_the_spec_is_not_in_the_blueprint():
    # a render.yaml change on this branch would Blueprint-sync and redeploy
    # the collector: the owner creates the service from the spec
    assert "sportsassets-market-plane" not in (ROOT / "render.yaml").read_text()


def test_the_module_it_runs_imports_no_venue_write_path():
    src = (ROOT / "backend/sportsassets/workers/universal_market_plane.py"
           ).read_text()
    for w in ("place_order", "cancel_order", "submit_order", "create_order",
              "funded_submit"):
        assert w not in src, w


def test_its_only_secrets_are_the_database_and_three_pmx_market_data_values():
    """(completion readiness) the dedicated runtime may receive only
    DATABASE_URL, PMX_CLIENT_ID, PMX_KEY_ID and PMX_PRIVATE_KEY_B64; every
    other variable is a plain, non-secret switch written in the spec."""
    s = _svc("sportsassets-market-plane")
    env = {e["key"]: e for e in s["envVars"]}
    secret = {k for k, e in env.items()
              if e.get("sync") is False or "fromDatabase" in e}
    # (Kalshi rep 2026-10-07) + the Kalshi key the authenticated WebSocket
    # handshake requires -- read-only use, on this service only
    assert secret == {"DATABASE_URL", "PMX_CLIENT_ID", "PMX_KEY_ID",
                      "PMX_PRIVATE_KEY_B64", "KALSHI_API_KEY_ID",
                      "KALSHI_PRIVATE_KEY_PEM"}
    assert "PMX_PARTICIPANT_ID" not in env
    plain = {k: e.get("value") for k, e in env.items() if k not in secret}
    assert plain == {"MALLOC_ARENA_MAX": "2",
                     "UMP_RUNTIME": "DEDICATED_READ_ONLY",
                     "UNIVERSAL_MARKET_PLANE": "on",
                     "INSTITUTIONAL_MD_STREAM": "on",
                     "UMP_SUBSCRIBE_ALL": "on"}
    # the books for the subscribe-all universe do not fit in 512 MB
    assert s["plan"] in ("standard", "pro")

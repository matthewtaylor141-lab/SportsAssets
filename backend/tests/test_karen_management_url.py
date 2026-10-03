"""THE MANAGEMENT URL /karen (frontend + Netlify): the same static agent
shell as /derek, /xavier and /audrey frames /api/command/agents/karen/page;
the rewrite sits below the /api/command/* proxy and above the command-host
catch-all."""
from __future__ import annotations

import pytest


def test_the_management_url_karen_reaches_her_page_like_the_others():
    import pathlib
    try:
        import tomllib
    except ImportError:                                         # pragma: no cover
        pytest.skip("tomllib needs Python 3.11+")
    repo = pathlib.Path(__file__).resolve().parents[2]
    shell = (repo / "frontend" / "public" / "command" / "agent.html"
             ).read_text()
    assert 'href="/karen" data-agent="karen"' in shell
    assert "karen: 1" in shell
    rules = tomllib.loads((repo / "netlify.toml").read_text())["redirects"]
    host = "https://command.bettortoken.com"
    idx = {r["from"]: i for i, r in enumerate(rules)}
    k = idx["%s/karen" % host]
    assert rules[k]["to"] == "/command/agent.html" and rules[k]["status"] == 200
    assert k < idx["%s/*" % host] and idx["/api/command/*"] < k

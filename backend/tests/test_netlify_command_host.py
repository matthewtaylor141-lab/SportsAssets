"""command.bettortoken.com IS COMMAND, AND NOTHING ELSE.

Owner decision 2026-09-19: on that hostname the COMMAND interface is the
whole product. Performance, Mission and the rest of the React app are
obsolete there and must not be reachable.

THE RULE ORDER IS LOAD-BEARING. Netlify takes the first matching rule, so
three things have to stay in this sequence and a reordering would break
the site quietly:

  1. /api/command/*   path-only, therefore matches on EVERY host. COMMAND's
                      core.endpoint() refuses to fetch outside /api/command/
                      and fetches same-origin, so the API must appear on
                      whichever host is serving the page.
  2. the host rule    command.bettortoken.com/* -> /command/:splat
  3. the SPA fallback /* -> /index.html, which is www's rule now. If it ran
                      first it would hand the command host the React app --
                      the exact thing being removed.

NOTHING HERE OPENS A SOCKET or contacts Netlify. It reads the committed
configuration and the committed files.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOML = ROOT / "netlify.toml"
COMMAND = ROOT / "frontend" / "public" / "command"
HOST = "command.bettortoken.com"


def _redirects():
    try:
        import tomllib
    except ImportError:                                        # pragma: no cover
        pytest.skip("tomllib needs Python 3.11+")
    return tomllib.loads(TOML.read_text())["redirects"]


def _index_of(predicate):
    for i, rule in enumerate(_redirects()):
        if predicate(rule):
            return i
    return None


class TestTheHostServesCommand:

    def test_the_host_rule_exists_and_rewrites_to_command(self):
        rule = _redirects()[_index_of(lambda r: HOST in r["from"])]
        assert rule["from"] == "https://%s/*" % HOST
        assert rule["to"] == "/command/:splat"
        # 200 is a REWRITE: the browser keeps the command hostname rather
        # than being bounced to /command/ on another one.
        assert rule["status"] == 200
        # force, because /index.html would otherwise win for /
        assert rule["force"] is True

    def test_it_is_scoped_by_host_not_by_path(self):
        """A path-only `from` would apply to www too and take the whole
        site down to COMMAND. The full URL is what limits it."""
        rule = _redirects()[_index_of(lambda r: HOST in r["from"])]
        assert rule["from"].startswith("https://")


class TestTheOrderThatMakesItWork:

    def test_the_api_proxy_is_reached_before_the_host_rule(self):
        api = _index_of(lambda r: r["from"] == "/api/command/*")
        host = _index_of(lambda r: HOST in r["from"])
        assert api is not None and host is not None
        assert api < host, "the host rule would swallow /api/command/*"

    def test_the_api_proxy_still_applies_to_every_host(self):
        """Path-only on purpose: COMMAND fetches same-origin, so the API
        has to appear on whichever host serves the page."""
        api = _redirects()[_index_of(lambda r: r["from"] == "/api/command/*")]
        assert not api["from"].startswith("http")
        assert api["to"].endswith("/api/command/:splat")
        assert api["status"] == 200 and api["force"] is True

    def test_the_host_rule_is_reached_before_the_spa_fallback(self):
        host = _index_of(lambda r: HOST in r["from"])
        spa = _index_of(lambda r: r["from"] == "/*")
        assert host is not None and spa is not None
        assert host < spa, "the SPA fallback would serve the React app"

    def test_the_spa_fallback_survives_for_the_other_host(self):
        """www.bettortoken.com is NOT being changed: it keeps the React
        app at / and COMMAND at /command/."""
        spa = _redirects()[_index_of(lambda r: r["from"] == "/*")]
        assert spa["to"] == "/index.html"
        assert spa["status"] == 200
        assert spa.get("force") is not True


class TestEveryFileTheCommandPageNeedsIsReachable:
    """The splat has to cover COMMAND's own assets. They are referenced
    RELATIVELY from index.html, so on the command host they resolve to /
    and are rewritten into /command/ -- but only if they are really
    there."""

    def _referenced(self):
        html = (COMMAND / "index.html").read_text()
        return re.findall(r'(?:src|href)="([^":?#]+)"', html)

    def test_index_html_exists_to_be_served(self):
        assert (COMMAND / "index.html").is_file()

    def test_every_referenced_file_is_committed(self):
        refs = self._referenced()
        assert refs, "index.html referenced nothing -- the regex is wrong"
        for ref in refs:
            if ref.startswith(("http://", "https://", "//", "#")):
                continue
            assert (COMMAND / ref).is_file(), ref

    def test_the_reference_are_relative_so_the_rewrite_reaches_them(self):
        for ref in self._referenced():
            if ref.startswith(("http", "//", "#")):
                continue
            # a leading slash would resolve to the host root and rewrite
            # to /command//... rather than /command/<file>
            assert not ref.startswith("/"), ref

    def test_the_access_step_and_the_motion_layer_both_travel(self):
        refs = self._referenced()
        assert "unlock.js" in refs, "the sign-in would be dropped"
        assert "motion.js" in refs and "motion.css" in refs

    def test_the_react_app_is_not_referenced_from_command(self):
        """If COMMAND pulled anything out of the React build, the host
        rule would 404 it -- and the page would be broken rather than
        standalone."""
        for ref in self._referenced():
            assert not ref.startswith("assets/index-"), ref
            assert "/dist/" not in ref, ref

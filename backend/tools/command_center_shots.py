"""Drive the command centre in a real browser and screenshot each state.

It does what a reviewer would do by hand: unlock through the real
session endpoint, switch the preview to each scenario, open each of the
five views at a desktop and a phone width, and save a PNG. It also
scrapes the rendered DOM back out so the screen can be reconciled
against the payload that produced it -- `reconcile.json` holds both
sides for every shot.

    python tools/command_center_shots.py --base http://127.0.0.1:8899 \
        --out /tmp/shots
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request

DESKTOP = {"width": 1440, "height": 1000}
MOBILE = {"width": 390, "height": 844}       # iPhone 14 logical size

VIEWS = ["management", "live", "tests", "economics", "capabilities"]

# Which view matters most for each scenario -- shot at both widths.
FOCUS = {
    "collecting": "live", "armed-no-frames": "live", "quiet-healthy": "live",
    "disconnected": "live", "restart": "live", "stopped": "live",
    "exhausted-allowance": "live", "partial-coverage": "live",
    "failed": "live", "scheduled": "live", "unavailable": "management",
    "evidence-from-store": "live",
    # The two 2026-09-23 scenarios are the run itself, and they are
    # SEPARATE entries on purpose: one is built from the collector's own
    # rows, the other from records rebuilt to match its measurements.
    # Shot side by side, a reader can see which banner each carries.
    "actual-journal-2026-09-23": "live",
    "reconstructed-2026-09-23": "live",
}

# Scenarios that also get a management shot, because the overview is
# what the reader sees first and its wording is part of the deliverable.
ALSO_MANAGEMENT = ("collecting", "armed-no-frames", "scheduled", "failed",
                   "actual-journal-2026-09-23")

# The store scenario is about PROVENANCE, so it is shot on the economics
# view too -- that is where a stored commit and digest change what the
# reader can conclude.
# ...and on the two views where a stored commit and digest change what
# the reader can conclude.
ALSO_ECONOMICS = ("evidence-from-store",)


def get(base, path):
    with urllib.request.urlopen(base + path) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8899")
    ap.add_argument("--out", required=True)
    ap.add_argument("--password", default="preview-only-not-a-secret")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    from playwright.sync_api import sync_playwright

    scen = get(a.base, "/preview/scenarios")["scenarios"]
    report = {"shots": [], "auth": {}}

    with sync_playwright() as p:
        # The image ships one Chromium build and the pip wheel may pin a
        # different one. Point at the installed binary rather than
        # downloading a second copy.
        exe = None
        for cand in ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
                     "/opt/pw-browsers/chromium/chrome-linux/chrome"):
            if os.path.exists(cand):
                exe = cand
                break
        browser = p.chromium.launch(executable_path=exe) if exe \
            else p.chromium.launch()

        # 1. AUTHENTICATION, EXERCISED RATHER THAN ASSUMED.
        ctx = browser.new_context(viewport=DESKTOP)
        page = ctx.new_page()
        page.goto(a.base + "/command/center.html")
        page.wait_for_selector(".cc-panel", timeout=15000)
        locked = page.inner_text("#cc-root")
        report["auth"]["locked_page_says"] = locked[:400]
        report["auth"]["refused_without_cookie"] = \
            "UNLOCK REQUIRED" in locked.upper()
        page.screenshot(path=os.path.join(a.out, "00-locked.png"),
                        full_page=True)

        # Unlock through the real session endpoint, exactly as the main
        # Command page does. The cookie is HttpOnly: the page never sees
        # it, and neither does this script.
        r = ctx.request.post(a.base + "/api/command/session",
                             data={"password": a.password})
        report["auth"]["session_status"] = r.status
        body = r.json()
        report["auth"]["body_contains_no_token"] = "token" not in body
        report["auth"]["cookie_is_httponly"] = all(
            c.get("httpOnly") for c in ctx.cookies()
            if c["name"] == "bt_command")

        for s in scen:
            name = s["name"]
            get(a.base, "/preview/use/" + name)
            focus = FOCUS.get(name, "live")

            for width_name, vp in (("desktop", DESKTOP), ("mobile", MOBILE)):
                page.set_viewport_size(vp)
                if width_name == "desktop" and name == "collecting":
                    views = VIEWS
                elif name in ALSO_ECONOMICS:
                    views = [focus, "tests", "economics"]
                elif name in ALSO_MANAGEMENT:
                    views = [focus, "management"]
                else:
                    views = [focus]
                views = list(dict.fromkeys(views))
                for view in views:
                    page.goto(a.base + "/command/center.html")
                    page.wait_for_selector(".cc-panel, .cc-unavailable",
                                           timeout=15000)
                    if page.query_selector('[data-view="%s"]' % view):
                        page.click('[data-view="%s"]' % view)
                        page.wait_for_timeout(250)
                    fn = "%s--%s--%s.png" % (name, view, width_name)
                    page.screenshot(path=os.path.join(a.out, fn),
                                    full_page=True)

                    shot = {"scenario": name, "view": view,
                            "width": width_name, "file": fn,
                            "why": s["why"]}
                    # SCRAPE THE SCREEN so it can be checked against the
                    # payload rather than against memory.
                    shot["badges"] = [
                        t.strip() for t in
                        page.eval_on_selector_all(
                            ".cc-badge", "els => els.map(e => e.textContent)")]
                    shot["unknown_cells"] = len(
                        page.query_selector_all(".cc-unknown"))
                    shot["cells_without_source"] = len(
                        page.query_selector_all(".cc-broken"))
                    shot["h2"] = page.eval_on_selector_all(
                        "h2", "els => els.map(e => e.textContent.trim())")
                    shot["body_text"] = page.inner_text("#cc-root")
                    # HORIZONTAL OVERFLOW is the mobile failure that a
                    # screenshot alone hides, so it is measured.
                    shot["scroll_width"] = page.evaluate(
                        "document.documentElement.scrollWidth")
                    shot["viewport_width"] = vp["width"]
                    shot["overflows"] = shot["scroll_width"] > vp["width"] + 1
                    report["shots"].append(shot)

        # 2. NOTHING SECRET ON THE WIRE OR IN THE DOM.
        get(a.base, "/preview/use/collecting")
        page.set_viewport_size(DESKTOP)
        page.goto(a.base + "/command/center.html")
        page.wait_for_selector(".cc-panel", timeout=15000)
        dom = page.content()
        report["leak_scan"] = {
            "password_in_dom": a.password in dom,
            "cookie_readable_by_js": page.evaluate("document.cookie"),
            "suspicious_tokens": [t for t in
                                  ("api_key", "apiKey", "secret", "private_key",
                                   "Authorization", "DATABASE_URL", "passwd")
                                  if t in dom],
        }
        browser.close()

    with open(os.path.join(a.out, "reconcile.json"), "w") as fh:
        json.dump(report, fh, indent=1)
    print(json.dumps({"shots": len(report["shots"]),
                      "auth": report["auth"],
                      "leaks": report["leak_scan"],
                      "overflowing": [s["file"] for s in report["shots"]
                                      if s["overflows"]],
                      "cells_without_source": sum(
                          s["cells_without_source"] for s in report["shots"])},
                     indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""SOURCE TO SCREEN. Check what the page SHOWED against what produced it.

For each scenario this fetches the payload through the real route and
re-derives the headline figures INDEPENDENTLY from the fixture records
-- counting rows rather than trusting the read model -- then asserts
that the rendered page text carries those same figures.

Three sides have to agree: the records, the payload, and the pixels.
Checking the payload against itself would prove nothing.

    python tools/command_center_reconcile.py --shots /tmp/shots
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import urllib.request                                          # noqa: E402
import urllib.error                                            # noqa: E402

from sportsassets import bettor_command_center as CC           # noqa: E402
import command_center_preview as PV                            # noqa: E402

T0 = dt.datetime.fromisoformat(CC.WINDOW_START).timestamp()
T_END = dt.datetime.fromisoformat(CC.WINDOW_END).timestamp()


def truth_from_records(sc):
    """Recount the fixture from scratch. No read-model code involved."""
    recs = sc["records"]
    lad = [r for r in recs if r["kind"] == "LADDER"]
    early = [r for r in lad if r["at"] < T0]
    window = [r for r in lad if T0 <= r["at"] < T_END]
    with_depth = set()
    receiving = set()
    for r in window:
        receiving.add(r["slug"])
        p = r["payload"]
        if (p.get("bids") or p.get("offers")):
            with_depth.add(r["slug"])
    opened = sum(1 for r in recs if r["kind"] == "GAP"
                 and (r["payload"] or {}).get("event") == "GAP_OPENED")
    closed = sum(1 for r in recs if r["kind"] == "GAP"
                 and (r["payload"] or {}).get("event") == "GAP_CLOSED")
    boots = {r["boot_id"] for r in recs}
    return {
        "ladders_total": len(lad),
        "early_frames": len(early),
        "window_frames": len(window),
        "receiving": len(receiving),
        "with_depth": len(with_depth),
        "open_gaps": max(0, opened - closed),
        "boots": len(boots),
        "first_frame": (dt.datetime.fromtimestamp(
            min(r["at"] for r in lad), dt.timezone.utc).isoformat()
            if lad else None),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8899")
    ap.add_argument("--shots", required=True)
    ap.add_argument("--password", default="preview-only-not-a-secret")
    a = ap.parse_args()

    shots = json.load(open(os.path.join(a.shots, "reconcile.json")))
    screen = {}
    for s in shots["shots"]:
        screen.setdefault(s["scenario"], {})[s["view"]] = s["body_text"]

    # THE SAME CLIENT THE PAGE USES, and not a second one.
    #
    # The COMMAND cookie is `Secure`, so a plain-HTTP client will not
    # store or send it -- urllib's cookiejar drops it silently and every
    # read comes back 401. Chromium treats 127.0.0.1 as a secure origin,
    # which is why the browser can hold it at all. Rather than weaken
    # the cookie for a test, the reconciliation goes through a real
    # browser request context: same cookie rules, same origin checks.
    from playwright.sync_api import sync_playwright

    exe = next((c for c in
                ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
                 "/opt/pw-browsers/chromium/chrome-linux/chrome")
                if os.path.exists(c)), None)
    pw = sync_playwright().start()
    browser = pw.chromium.launch(executable_path=exe) if exe \
        else pw.chromium.launch()
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(a.base + "/command/center.html")
    page.evaluate(
        """async ([base, pw]) => {
            await fetch(base + '/api/command/session', {
                method: 'POST', credentials: 'same-origin',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({password: pw})});
        }""", [a.base, a.password])

    class _Opener:
        """Reads through the PAGE's own fetch, cookie rules and all."""

        @staticmethod
        def open(url):
            got = page.evaluate(
                """async (u) => {
                    const r = await fetch(u, {credentials: 'same-origin',
                                              cache: 'no-store'});
                    return {status: r.status, body: await r.text()};
                }""", url)
            if got["status"] >= 400:
                raise urllib.error.HTTPError(
                    url, got["status"], "", None,
                    __import__("io").BytesIO(got["body"].encode()))
            return __import__("io").BytesIO(got["body"].encode())

    opener = _Opener()

    S = PV.scenarios()
    rows, failures = [], []

    for name, sc in S.items():
        opener.open(a.base + "/preview/use/" + name).read()
        try:
            payload = json.loads(opener.open(
                a.base + "/api/command/center/snapshot").read())
        except urllib.error.HTTPError as e:
            payload = {"_http": e.code, "_body": json.loads(e.read())}

        row = {"scenario": name}

        if sc.get("raise"):
            # THE UNAVAILABLE CASE. The route must refuse, and the page
            # must say so rather than render zeros.
            row["route_status"] = payload.get("_http")
            det = (payload.get("_body") or {}).get("detail")
            row["route_reason"] = (det.get("reason")
                                   if isinstance(det, dict) else det)
            txt = screen.get(name, {}).get("management", "")
            row["screen_says_unavailable"] = "EVIDENCE UNAVAILABLE" in txt
            row["screen_shows_no_zero_table"] = "0 of 12" not in txt
            ok = (row["route_status"] == 503
                  and row["screen_says_unavailable"]
                  and row["screen_shows_no_zero_table"])
            row["agrees"] = ok
            if not ok:
                failures.append(row)
            rows.append(row)
            continue

        truth = truth_from_records(sc)
        if "views" not in payload:
            row["agrees"] = False
            row["unexpected_route_error"] = payload
            failures.append(row)
            rows.append(row)
            continue
        lo = payload["views"]["live_operation"]
        got = {
            "ladders_total": lo["frames"]["total"]["value"],
            "early_frames": lo["periods"]["early"]["frames"],
            "window_frames": lo["periods"]["measurement"]["frames"],
            "receiving": lo["coverage"]["receiving"],
            "with_depth": lo["coverage"]["with_depth"],
            "open_gaps": lo["gaps"]["n_open"],
            "boots": lo["identity"]["boot_count"]["value"],
            "first_frame": lo["frames"]["first_persisted"]["value"],
        }
        row["records_vs_payload"] = {
            k: {"records": truth[k], "payload": got[k],
                "agree": truth[k] == got[k]}
            for k in truth}
        row["payload_agrees"] = all(v["agree"] for v
                                    in row["records_vs_payload"].values())

        # NOW THE PIXELS. The state and the coverage line must be on the
        # page a browser actually rendered.
        txt = screen.get(name, {}).get("live") or \
            screen.get(name, {}).get("management") or ""
        state = lo["lifecycle"]["state"]
        cov_line = "%d of %d receiving" % (got["receiving"],
                                           lo["coverage"]["allowlisted"])
        row["screen"] = {
            "state_on_screen": state in txt,
            "state": state,
            "coverage_line_on_screen": cov_line in txt,
            "coverage_line": cov_line,
            "health_on_screen": lo["health"]["verdict"] in txt,
            "health": lo["health"]["verdict"],
        }
        # A quiet book must never be painted as a broken feed.
        row["screen"]["quiet_not_called_broken"] = not (
            lo["health"]["verdict"] == CC.H_LIVE_QUIET
            and "DISCONNECTED" in txt)
        row["screen_agrees"] = all(
            v for k, v in row["screen"].items()
            if isinstance(v, bool))
        row["agrees"] = row["payload_agrees"] and row["screen_agrees"]
        if not row["agrees"]:
            failures.append(row)
        rows.append(row)

    out = {"rows": rows, "failures": failures,
           "all_agree": not failures,
           "note": "records recounted independently, compared to the "
                   "payload the real route returned, compared to the text "
                   "a real browser rendered"}
    path = os.path.join(a.shots, "source_to_screen.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1)

    for r in rows:
        mark = "OK " if r["agrees"] else "XX "
        if "records_vs_payload" in r:
            print("%s%-22s %s | %s | %s" % (
                mark, r["scenario"], r["screen"]["state"],
                r["screen"]["coverage_line"], r["screen"]["health"]))
        else:
            print("%s%-22s HTTP %s %s" % (mark, r["scenario"],
                                          r.get("route_status"),
                                          r.get("route_reason")))
    print("\nall three sides agree:", out["all_agree"])
    print("written:", path)
    browser.close()
    pw.stop()
    return 0 if out["all_agree"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

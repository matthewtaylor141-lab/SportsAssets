"""Capture the programme manifest, PRE-DEPLOY, and write it for commit.

HOW THE MANIFEST REACHES THE WORKER. It rides in the deploy. The worker
reads `BETTOR_INCENTIVE_MANIFEST` as a PATH and loads a file; it has no
capture path of its own and cannot fetch one. So the order is:

    1. run this, here, before the deploy      <- <=4 public requests
    2. commit the file it writes to the release branch
    3. deploy       -- the manifest is now on the worker's filesystem
    4. point BETTOR_INCENTIVE_MANIFEST at that repo path
    5. arm, run

WHY PRE-DEPLOY RATHER THAN AT ARM. A manifest captured at arm would
have to be written somewhere the worker can read, and the only durable
writable thing it has is Postgres -- which would mean a schema for
something the repository already versions perfectly well. Committing it
also makes the allowlist REVIEWABLE BEFORE it is armed: the exact
markets, the exact programme terms and the response digest are in the
diff, not discovered at runtime.

WHAT THIS COSTS THE RUN'S ALLOWANCE: NOTHING. The eight-request row
budget covers the run. This capture happens before the run exists, and
its own bound is enforced here: at most `MAX_REQUESTS` outbound, counted
including retries and pagination, refused past it. The run's `manifest`
sub-cap then goes unused, and the row's recheck and retry units are all
that remain in play -- which is conservative, and the report says so.

UNAUTHENTICATED. `/v1/incentives` takes no credentials and none are
sent. `/v1/incentives/earnings`, which IS authenticated, is not touched.

Run:  python research/beta48/capture_incentive_manifest.py --et-date YYYY-MM-DD
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "backend"))

from sportsassets import bettor_incentive_manifest as man   # noqa: E402

# The out-of-band bound. Declared here, enforced here, reported here.
MAX_REQUESTS = 6
TIMEOUT_S = 20.0

DEFAULT_OUT = os.path.join(HERE, "acceptance", "incentive_manifest.json")


class LocalLedger:
    """A hard bound for a pre-deploy action, and an honest one.

    NOT the durable row. This action runs before the run exists, so
    there is no armed allowance to draw on; the bound is enforced in
    this process and the process is one operator command. Its report
    goes into the manifest so the count that was spent is part of the
    artifact rather than a claim about it.
    """

    def __init__(self, cap=MAX_REQUESTS):
        self.cap = cap
        self.spent = {}
        self.log = []

    async def spend(self, kind, *, why=""):
        total = sum(self.spent.values())
        if total >= self.cap:
            rec = {"ok": False, "kind": kind, "verdict": "REFUSED_LOCAL_CAP",
                   "detail": "%d of %d already spent" % (total, self.cap)}
            self.log.append(rec)
            return rec
        self.spent[kind] = self.spent.get(kind, 0) + 1
        rec = {"ok": True, "kind": kind, "verdict": "GRANTED", "why": why,
               "n_total": total + 1}
        self.log.append(rec)
        return rec

    def report(self):
        return {"scope": "PRE-DEPLOY, out of band -- NOT the run's row",
                "cap": self.cap, "spent": dict(self.spent),
                "used": sum(self.spent.values()), "log": self.log}


def _get(url, params):
    """One outbound GET. No credentials, no redirects followed blindly."""
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request("%s?%s" % (url, q),
                                 headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


async def _run(et_date, out_path, cap):
    ledger = LocalLedger(cap)
    m = await man.capture(_get, ledger, et_date=et_date)
    m["capture"] = {"mode": "PRE-DEPLOY, committed to the repository",
                    "request_report": ledger.report(),
                    "run_allowance_spent": 0,
                    "note": "this capture spends NONE of the run's "
                            "eight-request row budget; the run's manifest "
                            "sub-cap therefore goes unused"}
    frozen = man.freeze(m, et_date=et_date)
    m["freeze_preview"] = {k: frozen.get(k) for k in
                           ("ok", "why", "markets", "distinct_programs",
                            "distinct_events", "slugs",
                            "independence_note", "dropped_by_watch_cap")}

    print("captured %d programme rows over %d page(s); %d outbound requests"
          % (len(m.get("programs") or []), m.get("pages_read", 0),
             ledger.report()["used"]))
    if not frozen.get("ok"):
        print("FREEZE WOULD REFUSE: %s -- %s qualifying markets"
              % (frozen.get("why"), frozen.get("markets")))
        print("The run would return INSUFFICIENT COVERAGE. The manifest is "
              "still written, so the refusal is reviewable.")
    else:
        print("freeze preview: %d markets / %d programme(s) / %d event(s)"
              % (frozen["markets"], frozen["distinct_programs"],
                 frozen["distinct_events"]))
        print(frozen["independence_note"])
    man.save(out_path, m)
    print("written: %s" % out_path)
    print("\nNEXT: commit this file, deploy, then point "
          "BETTOR_INCENTIVE_MANIFEST at its repository path.")
    return 0 if frozen.get("ok") else 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--et-date", required=True)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--max-requests", type=int, default=MAX_REQUESTS)
    a = ap.parse_args()
    import asyncio
    return asyncio.run(_run(a.et_date, a.out, a.max_requests))


if __name__ == "__main__":
    sys.exit(main())

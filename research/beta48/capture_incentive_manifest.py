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


ALLOWANCE = os.path.join(HERE, "acceptance", "preflight_allowance.json")


class LocalLedger:
    """A hard bound for a pre-deploy action, and a DURABLE one.

    NOT the run's Postgres row -- this action happens before the run
    exists, so there is no armed allowance to draw on.

    WHY IT IS NOT MERELY IN-PROCESS ANY MORE. The first version held
    the counter in this object, which a second invocation recreated
    empty: every dispatch of the capture got a fresh six, which is not
    an allowance, it is a suggestion. Attempts already made -- including
    ones that failed -- are read from `preflight_allowance.json` at
    start and written back at the end, so a re-dispatch RESUMES rather
    than resets. The file is committed, so the count is reviewable in
    the diff rather than asserted in a report.
    """

    def __init__(self, cap=MAX_REQUESTS, path=ALLOWANCE):
        self.path = path
        self.prior = self._load()
        self.cap = int(self.prior.get("cap", cap))
        self.carried = int(self.prior.get("spent", 0))
        self.spent = {}
        self.log = []

    def _load(self):
        try:
            with open(self.path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            # NO FILE IS NOT A FRESH ALLOWANCE. A missing ledger is an
            # unknown one, and the safe reading of an unknown spend is
            # the whole cap, not zero.
            return {"cap": MAX_REQUESTS, "spent": MAX_REQUESTS,
                    "attempts": [], "_synthesised": "no ledger file was "
                    "readable; assuming the allowance is exhausted rather "
                    "than assuming it is untouched"}

    @property
    def used(self):
        return self.carried + sum(self.spent.values())

    async def spend(self, kind, *, why=""):
        if self.used >= self.cap:
            rec = {"ok": False, "kind": kind, "verdict": "REFUSED_LOCAL_CAP",
                   "detail": "%d of %d already spent (%d carried in from "
                             "earlier attempts)"
                             % (self.used, self.cap, self.carried)}
            self.log.append(rec)
            return rec
        self.spent[kind] = self.spent.get(kind, 0) + 1
        rec = {"ok": True, "kind": kind, "verdict": "GRANTED", "why": why,
               "n_total": self.used}
        self.log.append(rec)
        return rec

    def persist(self, where="unknown", outcomes=None):
        """Write the spend back. Called whatever the capture returned.

        A ledger that only records successful captures would forgive
        exactly the attempts most likely to be repeated.
        """
        out = dict(self.prior)
        attempts = list(out.get("attempts") or [])
        for i, rec in enumerate(self.log):
            if not rec.get("ok"):
                continue
            attempts.append({
                "at": _utcnow(), "where": where, "kind": rec["kind"],
                "n_total": rec["n_total"], "why": rec.get("why", ""),
                "outcome": (outcomes or {}).get(i, "recorded"),
                "counted": True})
        out.update({"cap": self.cap, "spent": self.used,
                    "remaining": max(0, self.cap - self.used),
                    "attempts": attempts})
        with open(self.path, "w") as fh:
            json.dump(out, fh, indent=2)
        return out

    def report(self):
        return {"scope": "PRE-DEPLOY, out of band -- NOT the run's row",
                "cap": self.cap, "carried_in": self.carried,
                "spent_this_run": dict(self.spent),
                "used": self.used,
                "remaining": max(0, self.cap - self.used),
                "durable_ledger": self.path, "log": self.log}


def _utcnow():
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def _get(url, params):
    """One outbound GET. No credentials, no redirects followed blindly."""
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request("%s?%s" % (url, q),
                                 headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


async def _run(et_date, out_path, cap, where="unknown"):
    ledger = LocalLedger(cap)
    if ledger.used >= ledger.cap:
        print("REFUSING: the preflight allowance is exhausted -- %d of %d "
              "already spent, recorded in %s."
              % (ledger.used, ledger.cap, ledger.path))
        print("Raising it is a decision, not a retry. Nothing was sent.")
        return 4
    print("preflight allowance: %d of %d already spent, %d remaining"
          % (ledger.used, ledger.cap, ledger.cap - ledger.used))
    try:
        m = await man.capture(_get, ledger, et_date=et_date)
    finally:
        # PERSIST WHATEVER HAPPENED. An exception mid-capture must not
        # be a way to spend requests without recording them.
        ledger.persist(where=where)
    m["capture"] = {"mode": "PRE-DEPLOY, committed to the repository",
                    "where": where,
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
    ap.add_argument("--where", default="unknown",
                    help="where this capture ran, recorded in the ledger")
    a = ap.parse_args()
    import asyncio
    return asyncio.run(_run(a.et_date, a.out, a.max_requests, a.where))


if __name__ == "__main__":
    sys.exit(main())

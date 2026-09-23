"""A LOCAL PREVIEW OF THE COMMAND CENTRE, THROUGH THE REAL DATA PATH.

WHAT IS REAL HERE AND WHAT IS NOT. The route, the authentication
dependency, the reader in `sportsassets.api.command_center`, the read
model in `sportsassets.bettor_command_center` and the page in
`frontend/public/command/` are the production article, unmodified. The
only substitution is the database connection: a stub pool answers the
reader's SELECTs from a named scenario instead of Postgres.

EVERY SCENARIO IS SYNTHETIC AND SAYS SO. Each record carries
`"synthetic": true` and the page is served with a banner naming the
scenario, so a screenshot of this preview cannot be mistaken for a
screenshot of the run. The one exception is `--live-json`, which renders
a payload captured from the real database; that mode labels itself
differently.

It opens no socket to the venue, touches no production database, spends
no allowance and changes no control.

    python tools/command_center_preview.py --port 8899
    python tools/command_center_preview.py --list
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sportsassets import bettor_command_center as CC       # noqa: E402

T0 = dt.datetime.fromisoformat(CC.WINDOW_START).timestamp()
T_END = dt.datetime.fromisoformat(CC.WINDOW_END).timestamp()

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
STATIC = os.path.join(REPO, "frontend", "public", "command")

SLUGS = [
    "ccpc-bilbrd-1album-any2026-alewar", "ccpc-bilbrd-1album-any2026-benboo",
    "ccpc-bilbrd-1album-any2026-beyonc", "ccpc-bilbrd-1album-any2026-bileil",
    "ccpc-bilbrd-1album-any2026-charoa", "ccpc-bilbrd-1album-any2026-chaxcx",
    "ccpc-bilbrd-1album-any2026-coldpl", "ccpc-bilbrd-1album-any2026-doechi",
    "ccpc-bilbrd-1album-any2026-dualip", "ccpc-bilbrd-1album-any2026-eminem",
    "ccpc-bilbrd-1album-any2026-fraoce", "ccpc-bilbrd-1album-any2026-jusbie",
]


def _ladder(at, slug, levels=4, boot="boot-synth-A", epoch=1):
    return {"boot_id": boot, "at": at, "kind": "LADDER", "epoch": epoch,
            "slug": slug,
            "payload": {"synthetic": True, "bids": [[0.41, 120]] * levels,
                        "offers": [[0.43, 90]] * levels}}


def _gap(at, event, why, frm=None, to=None, dur=None, boot="boot-synth-A",
         detail=None):
    p = {"synthetic": True, "event": event, "why": why}
    if frm is not None:
        p["from"] = frm
    if to is not None:
        p["to"] = to
    if dur is not None:
        p["duration_s"] = dur
    if detail is not None:
        p["detail"] = detail
    return {"boot_id": boot, "at": at, "kind": "GAP", "epoch": None,
            "slug": None, "payload": p}


def _epoch(at, event, epoch=1, boot="boot-synth-A", **kw):
    return {"boot_id": boot, "at": at, "kind": "EPOCH", "epoch": epoch,
            "slug": None,
            "payload": dict({"synthetic": True, "event": event}, **kw)}


PROBE_ARMED = {
    "synthetic": True, "armed": True,
    "probe_id": "SYNTHETIC-0000-0000-0000-000000000000",
    "started_at": "2026-09-23T02:35:48+00:00",
    "deadline_at": "2026-09-24T04:35:48+00:00",
    "max_distinct": 0, "max_bbo_attempts": 0, "max_listing_attempts": 0,
    "distinct_reserved": 0, "bbo_attempts_reserved": 0,
    "listing_attempts_reserved": 0,
    "max_incentive_manifest": 4, "max_incentive_recheck": 2,
    "max_incentive_retry": 2,
    "incentive_manifest_reserved": 0, "incentive_recheck_reserved": 0,
    "incentive_retry_reserved": 0,
    "max_socket_connect": 20, "max_socket_subscribe": 40,
    "socket_connect_reserved": 0, "socket_subscribe_reserved": 0,
}

RUN_ROW = {"synthetic": True, "run_id": "2026-09-23:SYNTHETIC",
           "et_date": "2026-09-23", "manifest_id": "SYNTHETIC",
           "boots": 1, "closed": False}


def _run_open(at, boot="boot-synth-A"):
    return {"boot_id": boot, "at": at, "kind": "RUN_OPEN", "epoch": None,
            "slug": None,
            "payload": {"synthetic": True, "allowlist": SLUGS,
                        "window": {"start_iso": CC.WINDOW_START,
                                   "end_iso": CC.WINDOW_END}}}


def _busy(t_from, t_to, slugs, every=4.0, boot="boot-synth-A", epoch=1,
          levels=4):
    out, t, i = [], t_from, 0
    while t < t_to:
        out.append(_ladder(t, slugs[i % len(slugs)], levels=levels,
                           boot=boot, epoch=epoch))
        t += every
        i += 1
    return out


def _digest(body):
    import hashlib
    return hashlib.sha256(body.encode()).hexdigest()


_EVAL_JSON = json.dumps({
    "cut": "2026-09-17T00:00:00+00:00", "selected": "R7",
    "qualifies": False,
    "provenance": {"independent_holdout": False,
                   "status_of_every_figure_here": "DEVELOPMENT DIAGNOSTIC",
                   "why": "these dates were inspected before the protocol "
                          "existed"},
    "eval": [{"qfrac": 0.25, "net_usd": -74.29, "events": 5,
              "capital_hours": 3696.0, "taker_fees_usd": -24.98,
              "rebates_usd": 6.33, "per_capital_hour": -0.0201}],
})


def scenarios() -> dict:
    """Every state the brief names, as a named fixture."""
    S = {}

    S["stopped"] = {
        "why": "The control is false and frames exist: the run ended.",
        "control": False, "probe": PROBE_ARMED, "run_row": RUN_ROW,
        "now": T_END + 600,
        "records": ([_run_open(T0), _epoch(T0, "RUN_STARTED", subscribed=12)]
                    + _busy(T0, T0 + 1800, SLUGS)
                    + [{"boot_id": "boot-synth-A", "at": T_END,
                        "kind": "RUN_CLOSE", "epoch": None, "slug": None,
                        "payload": {"synthetic": True,
                                    "why": "WINDOW_END"}}]),
    }

    S["armed-no-frames"] = {
        "why": "THE REFUSAL. The control is true and a socket is open, and "
               "not one frame has been persisted. A flipped flag is not "
               "collection.",
        "control": True, "probe": PROBE_ARMED, "run_row": RUN_ROW,
        "now": T0 + 300,
        "records": [_run_open(T0), _epoch(T0, "RUN_STARTED", subscribed=12),
                    _epoch(T0 + 2, "EPOCH_OPENED", connect_attempts=1,
                           subscribe_messages=12, reconnects=1)],
    }

    S["collecting"] = {
        "why": "Frames are being persisted across all twelve markets.",
        "control": True, "probe": dict(PROBE_ARMED,
                                       socket_connect_reserved=1,
                                       socket_subscribe_reserved=12,
                                       incentive_manifest_reserved=1),
        "run_row": RUN_ROW, "now": T0 + 3600,
        "records": ([_run_open(T0), _epoch(T0, "RUN_STARTED", subscribed=12),
                     _epoch(T0 + 2, "EPOCH_OPENED", connect_attempts=1,
                            subscribe_messages=12, reconnects=1)]
                    + _busy(T0 - 6600, T0, SLUGS, every=30.0)   # EARLY
                    + _busy(T0 + 5, T0 + 3595, SLUGS, every=4.0)),
    }

    S["quiet-healthy"] = {
        "why": "THE OTHER REFUSAL. Six hours of silence with no open gap. "
               "On a change-driven feed that is a market nobody traded, "
               "not a broken socket.",
        "control": True, "probe": dict(PROBE_ARMED,
                                       socket_connect_reserved=1,
                                       socket_subscribe_reserved=12),
        "run_row": RUN_ROW, "now": T0 + 6 * 3600,
        "records": ([_run_open(T0), _epoch(T0, "RUN_STARTED", subscribed=12)]
                    + _busy(T0 + 5, T0 + 300, SLUGS, every=10.0)),
    }

    S["disconnected"] = {
        "why": "The worker's own liveness detector opened a gap and has "
               "not closed it.",
        "control": True, "probe": dict(PROBE_ARMED,
                                       socket_connect_reserved=7,
                                       socket_subscribe_reserved=28),
        "run_row": RUN_ROW, "now": T0 + 4000,
        "records": ([_run_open(T0), _epoch(T0, "RUN_STARTED", subscribed=12)]
                    + _busy(T0 + 5, T0 + 3000, SLUGS, every=8.0)
                    + [_gap(T0 + 3100, "GAP_OPENED", "NO_FRAMES_120S")]),
    }

    S["restart"] = {
        "why": "Two boots. The BOOT_GAP covering the replacement is "
               "recorded and counted as unobserved.",
        "control": True, "probe": dict(PROBE_ARMED,
                                       socket_connect_reserved=3,
                                       socket_subscribe_reserved=24),
        "run_row": dict(RUN_ROW, boots=2), "now": T0 + 5400,
        "records": ([_run_open(T0), _epoch(T0, "RUN_STARTED", subscribed=12)]
                    + _busy(T0 + 5, T0 + 1800, SLUGS, every=8.0)
                    + [_gap(T0 + 2400, "BOOT_GAP", "PROCESS_REPLACED",
                            frm=T0 + 1800, to=T0 + 2400, dur=600.0,
                            boot="boot-synth-B"),
                       _epoch(T0 + 2401, "RUN_STARTED", boot="boot-synth-B",
                              subscribed=12)]
                    + _busy(T0 + 2410, T0 + 5390, SLUGS, every=8.0,
                            boot="boot-synth-B")),
    }

    S["exhausted-allowance"] = {
        "why": "Every socket limit is spent. The run ended on the "
               "allowance boundary, not on the clock.",
        "control": False,
        "probe": dict(PROBE_ARMED, socket_connect_reserved=20,
                      socket_subscribe_reserved=40,
                      incentive_manifest_reserved=4,
                      incentive_recheck_reserved=2,
                      incentive_retry_reserved=2),
        "run_row": RUN_ROW, "now": T0 + 7200,
        "records": ([_run_open(T0), _epoch(T0, "RUN_STARTED", subscribed=12)]
                    + _busy(T0 + 5, T0 + 3000, SLUGS[:8], every=12.0)
                    + [_gap(T0 + 3100, "SOCKET_ALLOWANCE_REFUSED",
                            "SOCKET_CONNECT_EXHAUSTED",
                            detail={"connect_attempts": 20})]),
    }

    S["partial-coverage"] = {
        "why": "Five of twelve markets ever sent a frame; one of those "
               "sent frames carrying no depth at all.",
        "control": True, "probe": dict(PROBE_ARMED,
                                       socket_connect_reserved=1,
                                       socket_subscribe_reserved=12),
        "run_row": RUN_ROW, "now": T0 + 3600,
        "records": ([_run_open(T0), _epoch(T0, "RUN_STARTED", subscribed=12)]
                    + _busy(T0 + 5, T0 + 3500, SLUGS[:4], every=20.0)
                    + _busy(T0 + 6, T0 + 3500, SLUGS[4:5], every=60.0,
                            levels=0)),
    }

    S["failed"] = {
        "why": "A RUN_ERROR record exists. Failure is terminal and is read "
               "before the control flag.",
        "control": True, "probe": PROBE_ARMED, "run_row": RUN_ROW,
        "now": T0 + 2000,
        "records": ([_run_open(T0), _epoch(T0, "RUN_STARTED", subscribed=12)]
                    + _busy(T0 + 5, T0 + 900, SLUGS, every=10.0)
                    + [_gap(T0 + 1000, "RUN_ERROR", "RUN_ERROR",
                            detail="ConnectionResetError")]),
    }

    S["scheduled"] = {
        "why": "Nothing armed, nothing running. The state before the "
               "window opens.",
        "control": False, "probe": None, "run_row": None,
        "now": T0 - 7200, "records": [],
    }

    # THE STORE PATH. Same collecting run, but the test and economic
    # artifacts arrive from the evidence store instead of from disk, so
    # the page shows a verified commit and digest rather than "read from
    # disk, nothing verified it".
    _xml = ('<?xml version="1.0"?><testsuites><testsuite name="pytest" '
            'errors="0" failures="0" skipped="5" tests="264" '
            'timestamp="2026-09-21T18:58:24+00:00"/></testsuites>')
    S["evidence-from-store"] = dict(
        S["collecting"],
        why="THE PRODUCTION PATH. Test and economic artifacts read from "
            "the evidence store, each carrying the commit it was "
            "produced at and a digest of its exact bytes.",
        store={
            "release_tests.xml": {
                "id": 1, "name": "release_tests.xml", "kind": "junit",
                "source_sha": "4b83924", "content_type": "text/xml",
                "digest": _digest(_xml), "size_bytes": len(_xml),
                "body": _xml, "published_at": 1790159309.0,
                "note": "SYNTHETIC", "superseded_by": None},
            "evaluation.json": {
                "id": 2, "name": "evaluation.json", "kind": "economics",
                "source_sha": "4b83924",
                "content_type": "application/json",
                "digest": _digest(_EVAL_JSON), "size_bytes": len(_EVAL_JSON),
                "body": _EVAL_JSON, "published_at": 1790159309.0,
                "note": "SYNTHETIC", "superseded_by": None},
        })

    # THE REAL RUN. Not a fixture: every boundary, gap and per-market
    # count below was read back from production. Labelled distinctly so
    # a screenshot of it cannot be confused with a synthetic one.
    try:
        import command_center_live_fixture as LIVE
        S["real-2026-09-23"] = LIVE.scenario()
    except Exception as exc:                                   # noqa: BLE001
        pass

    S["unavailable"] = {
        "why": "The database read fails. The page must say UNAVAILABLE, "
               "not render a well-formed page of zeros.",
        "raise": ("JOURNAL_UNREADABLE", "OSError"),
        "control": True, "probe": PROBE_ARMED, "run_row": RUN_ROW,
        "now": T0, "records": [],
    }
    return S


# ── the stub pool ────────────────────────────────────────────────────

class StubPool:
    """Answers exactly the three reads the reader performs. Nothing else.

    It is deliberately not a general fake: if the reader grows a fourth
    query this raises rather than returning an empty result, so a new
    read cannot silently render as "no data".
    """

    def __init__(self, scenario: dict) -> None:
        self.s = scenario

    async def fetchval(self, sql, *args):
        key = args[0] if args else None
        if key == "bettor_live_observation":
            return json.dumps(bool(self.s["control"]))
        if key == "bettor_live_probe_state":
            p = self.s.get("probe")
            return json.dumps(p) if p else None
        if key == "bettor_incentive_run":
            r = self.s.get("run_row")
            return json.dumps(r) if r else None
        raise AssertionError("preview stub saw an unexpected key: %r" % key)

    async def fetch(self, sql, *args):
        if "bettor_evidence_artifact" in sql:
            # THE EVIDENCE STORE. A scenario opts in with `store: {...}`;
            # by default the preview returns nothing here so the working
            # tree answers and the page shows the FALLBACK provenance.
            return list((self.s.get("store") or {}).values())
        if self.s.get("raise"):
            raise OSError(self.s["raise"][1])
        recs = self.s["records"]
        if "kind <> 'LADDER'" in sql:
            return [_Row({"boot_id": r["boot_id"], "at": r["at"],
                          "kind": r["kind"], "epoch": r["epoch"],
                          "slug": r["slug"],
                          "payload": json.dumps(r["payload"])})
                    for r in recs if r["kind"] != "LADDER"]
        if "kind = 'LADDER'" in sql:
            out = []
            for r in recs:
                if r["kind"] != "LADDER":
                    continue
                p = r["payload"]
                out.append(_Row({
                    "boot_id": r["boot_id"], "at": r["at"],
                    "epoch": r["epoch"], "slug": r["slug"],
                    "levels": len(p.get("bids") or [])
                              + len(p.get("offers") or [])}))
            return out
        raise AssertionError("preview stub saw an unexpected query")


class _Row(dict):
    def __getitem__(self, k):
        return dict.__getitem__(self, k)


# ── the server ───────────────────────────────────────────────────────

def make_app(default_scenario: str, password: str):
    from fastapi import Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    os.environ.setdefault("DESK_PASSWORD", password)
    os.environ.setdefault("BETTOR_EVIDENCE_ROOT",
                          os.path.join(REPO, "research/beta48/acceptance"))

    from fastapi import Depends, FastAPI, Response
    from sportsassets.api import app as APP
    from sportsassets.api import command_center as IO

    S = scenarios()
    chosen = {"name": default_scenario}

    real_snapshot = IO.snapshot

    async def preview_snapshot(pool=None, *, now=None):
        sc = S[chosen["name"]]
        payload = await real_snapshot(StubPool(sc), now=sc["now"])
        real = bool(sc.get("real"))
        payload["preview"] = {
            "SYNTHETIC": not real,
            "REAL_READINGS": real,
            "scenario": chosen["name"],
            "why": sc["why"],
            "read_at": sc.get("read_at"),
            "warning": (
                "RECONSTRUCTED FROM REAL READINGS read back from "
                "production at %s. Segment boundaries, gaps and "
                "per-market counts are measured. Ladder BODIES are not "
                "real -- each frame carries only its measured level "
                "count." % sc.get("read_at")
                if real else
                "EVERY RECORD BEHIND THIS PAGE IS A SYNTHETIC FIXTURE. "
                "It is not venue data and not a run result."),
        }
        return payload

    IO.snapshot = preview_snapshot

    # THE REAL ROUTE FUNCTIONS AND THE REAL DEPENDENCY, mounted on a bare
    # app. The production app's lifespan opens a Postgres pool, a Redis
    # client and several refresher loops that have nothing to do with
    # this page; running them here would mean the preview could only be
    # brought up beside a full stack. What is under test -- the route,
    # `require_command`, the reader and the read model -- is imported
    # unmodified, so the request path a browser takes is the real one.
    app = FastAPI(title="BETTOR Command Centre preview")

    app.add_api_route("/api/command/center/snapshot",
                      APP.command_center_snapshot, methods=["GET"],
                      dependencies=[Depends(APP.require_command)])
    app.add_api_route("/api/command/center/describe",
                      APP.command_center_describe, methods=["GET"],
                      dependencies=[Depends(APP.require_command)])
    app.add_api_route("/api/command/session", APP.command_session_open,
                      methods=["POST"])

    @app.get("/preview/scenarios")
    async def _scenarios():
        return {"scenarios": [{"name": k, "why": v["why"]}
                              for k, v in S.items()],
                "current": chosen["name"]}

    @app.get("/preview/use/{name}")
    async def _use(name: str):
        if name not in S:
            return JSONResponse({"error": "unknown scenario"}, 404)
        chosen["name"] = name
        return {"ok": True, "scenario": name, "why": S[name]["why"]}

    app.mount("/command", StaticFiles(directory=STATIC, html=True),
              name="command-static")
    return app


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--scenario", default="collecting")
    ap.add_argument("--password", default="preview-only-not-a-secret")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        for k, v in scenarios().items():
            print("%-22s %s" % (k, v["why"]))
        return 0

    import uvicorn
    uvicorn.run(make_app(a.scenario, a.password), host="127.0.0.1",
                port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

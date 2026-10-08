"""Fixtures for the PM acceptance receipts: the Render runtime window and
the historical PAPER fingerprints, in the shapes production returns (the
RC4 evidence packet, pm-acceptance run 37738089957: Render /services,
/deploys, /events, /metrics/memory bodies; the fingerprint JSON of
.github/pm-acceptance/paper_history_fingerprint.sql). Ids are fictional
except where a test replays the real RC4 market-plane events."""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
from datetime import datetime, timezone

SHA = "a" * 40
SIDS = {"sportsassets-api": "srv-fixtureapi0000000001",
        "sportsassets-workers": "srv-fixtureworkers000002",
        "sportsassets-market-plane": "srv-fixtureplane00000003"}
#: the RC4 market plane went live at 05:16:49.299438Z
LIVE = datetime(2026, 10, 8, 5, 16, 49, 299438, tzinfo=timezone.utc).timestamp()


def iso(t: float, frac: bool = True) -> str:
    d = datetime.fromtimestamp(t, tz=timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.%fZ" if frac else
                      "%Y-%m-%dT%H:%M:%SZ")


def ev(sid: str, typ: str, t: float, n: int, details=None) -> dict:
    """One Render /events item."""
    return {"event": {"details": details or {}, "id": "evt-%s-%04d" % (
        sid[-4:], n), "serviceId": sid, "timestamp": iso(t), "type": typ},
        "cursor": "cur-%s-%04d" % (sid[-4:], n)}


def oom(sid: str, t: float, n: int = 900) -> dict:
    """server_failed with reason.oomKilled -- the RC4 shape exactly."""
    return ev(sid, "server_failed", t, n, {
        "instanceID": sid + "-z2qbz",
        "reason": {"evicted": False, "oomKilled": {"memoryLimit": "2Gi"}}})


def _service(name: str, sid: str, *, minutes: float, commit: str):
    dep = "dep-%s" % sid[4:]
    start = LIVE
    lookup = [{"service": {"id": sid, "name": name, "ownerId": "tea-fixture",
                           "suspended": "not_suspended"}, "cursor": "c0"}]
    deploys = [
        {"deploy": {"id": dep, "commit": {"id": commit}, "status": "live",
                    "trigger": "api", "createdAt": iso(start - 84.0),
                    "startedAt": iso(start - 84.0),
                    "finishedAt": iso(start)}, "cursor": "d0"},
        {"deploy": {"id": dep + "old", "commit": {"id": "c" * 40},
                    "status": "deactivated", "createdAt": iso(start - 9000),
                    "finishedAt": iso(start - 8900)}, "cursor": "d1"}]
    # newest first, as Render returns them; the live deploy's own events
    page = [ev(sid, "server_available", start + 0.2, 3),
            ev(sid, "deploy_ended", start + 0.07, 2,
               {"deployId": dep, "deployStatus": "succeeded",
                "reason": {}, "status": 2}),
            ev(sid, "build_ended", start - 11.0, 1,
               {"buildId": "bld-x", "buildStatus": "succeeded"}),
            ev(sid, "deploy_started", start - 84.0, 0,
               {"deployId": dep, "trigger": {"manual": False}})]
    meta = {"start": iso(start, frac=False), "end": iso(start + minutes * 60),
            "limit": 100, "stopped": "SHORT_PAGE",
            "requests": [{"cursor": "", "http": "200",
                          "page": "events_%s_p000.json" % name}]}
    inst = sid + "-c52xp"
    memory = [{"labels": [{"field": "instance", "value": inst},
                          {"field": "service", "value": sid},
                          {"field": "resource", "value": sid}],
               "unit": "bytes",
               "values": [{"timestamp": iso(start + 60 * i, frac=False),
                           "value": (900 + i) * 1048576}
                          for i in range(int(minutes))]}]
    return {"lookup": lookup, "deploys": deploys, "events_meta": meta,
            "event_pages": [page], "memory": memory, "memory_http": "200"}


def render_raw(*, minutes: float = 75.0, commit: str = SHA) -> dict:
    """A complete, clean runtime receipt for all three services, each live
    since LIVE and observed for `minutes`."""
    return {"collected_at": iso(LIVE + minutes * 60),
            "services": {n: _service(n, sid, minutes=minutes, commit=commit)
                         for n, sid in SIDS.items()}}


def add_event(raw: dict, name: str, item: dict) -> dict:
    raw = copy.deepcopy(raw)
    page = raw["services"][name]["event_pages"][0]
    page.insert(0, item)
    return raw


def write_runtime(acc: pathlib.Path, raw: dict) -> None:
    """The files the workflow's Render step writes, from a raw receipt."""
    acc.mkdir(parents=True, exist_ok=True)
    (acc / "render_collection.json").write_text(json.dumps({
        "collected_at": raw["collected_at"],
        "memory_http": {n: s.get("memory_http")
                        for n, s in raw["services"].items()}}))
    for n, s in raw["services"].items():
        (acc / ("render_lookup_%s.json" % n)).write_text(
            json.dumps(s["lookup"]))
        (acc / ("deploys_%s.json" % n)).write_text(json.dumps(s["deploys"]))
        (acc / ("render_events_%s.json" % n)).write_text(
            json.dumps(s["events_meta"]))
        for q, page in zip(s["events_meta"]["requests"], s["event_pages"]):
            (acc / q["page"]).write_text(json.dumps(page))
        (acc / ("mem_%s.json" % n)).write_text(json.dumps(s["memory"]))


# ── PAPER history ──────────────────────────────────────────────────────────

CUTOFF_T = LIVE - 3600.0 - 900.0
CUTOFF = iso(CUTOFF_T, frac=False)


def _md5(x: str) -> str:
    return hashlib.md5(x.encode()).hexdigest()


def fingerprint(*, captured_at: float, cutoff: str = CUTOFF,
                writers: int = 0, accounts_since: int = 0,
                orders_projection: str = "p0") -> dict:
    """The JSON paper_history_fingerprint.sql emits, for a fixed history
    below the cutoff (identical whenever it is captured)."""
    c = datetime.fromisoformat(cutoff.replace("Z", "+00:00")).timestamp()
    return {
        "version": "PAPER_HISTORY_FINGERPRINT_V1", "cutoff": cutoff,
        "captured_at": iso(captured_at), "database": "sportsassets_db",
        "server_version_num": "160004",
        "settings": {"TimeZone": "UTC", "DateStyle": "ISO, MDY",
                     "IntervalStyle": "postgres", "extra_float_digits": "1"},
        "watermark": {"snapshot": "9:9:",
                      "cutoff_lag_s": round(captured_at - c, 3),
                      "writers_open_since_before_cutoff": writers,
                      "oldest_writer_xact_start": None,
                      "other_role_client_sessions": 0},
        "tables": {
            "paper_ledger": {"rows": 1204, "max_seq": 88231,
                             "cash_delta_total_usd": "-44287.196200",
                             "digest": _md5("ledger")},
            "paper_fills": {"rows": 311, "notional_usd": "15123.410000",
                            "fees_usd": "212.330000",
                            "digest": _md5("fills"),
                            "late_recorded_rows": 0},
            "paper_settlements": {"rows": 97, "payout_total_usd": "4410.0",
                                  "digest": _md5("settlements")},
            "paper_order_events": {"rows": 2210, "max_event_id": 99120,
                                   "digest": _md5("events")},
            "paper_decisions": {"rows": 40512, "digest": _md5("decisions"),
                                "late_recorded_rows": 0},
            "paper_accounts": {"rows": 3, "starting_cash_usd": "1500000",
                               "digest": _md5("accounts")},
            "paper_orders": {"rows": 340, "digest": _md5("orders")}},
        "projections": {
            "paper_accounts": {"rows_all": 3 + accounts_since,
                               "digest_all": _md5("all%d" % accounts_since),
                               "rows_since_cutoff": accounts_since},
            "paper_orders": {"rows": 340, "digest": _md5(orders_projection),
                             "open_rows": 4, "rows_since_cutoff": 12}},
        "columns": {t: "%s_id:text,at:timestamp with time zone" % t
                    for t in ("paper_ledger", "paper_fills",
                              "paper_settlements", "paper_order_events",
                              "paper_decisions", "paper_accounts",
                              "paper_orders")}}


def baseline_meta(pre: dict) -> dict:
    h = hashlib.sha256(json.dumps(pre).encode()).hexdigest()
    return {"run_id": "37700000001",
            "workflow_path": ".github/workflows/pm-acceptance.yml",
            "repository": "matthewtaylor141-lab/SportsAssets",
            "attestation_verified": True, "capture_sha256": h,
            "capture_sha256_in_packet": h}


def paper_receipt(*, pre_at: float = LIVE - 3600.0,
                  post_at: float = LIVE + 75 * 60.0) -> dict:
    """PRE an hour before the deploy, POST after the observation window,
    same fixed cutoff, the history below it unchanged, the projections
    moved (a new account, orders filled)."""
    pre = fingerprint(captured_at=pre_at)
    post = fingerprint(captured_at=post_at, accounts_since=1,
                       orders_projection="p1")
    return {"pre": pre, "post": post, "baseline": baseline_meta(pre)}


def write_paper(acc: pathlib.Path, receipt: dict) -> None:
    acc.mkdir(parents=True, exist_ok=True)
    for key, name in (("pre", "paper_history_pre.json"),
                      ("post", "paper_history_capture.json"),
                      ("baseline", "paper_history_baseline.json")):
        if receipt.get(key) is not None:
            (acc / name).write_text(json.dumps(receipt[key]))


#: the seven real sportsassets-market-plane events of the RC4 packet
#: (events_sportsassets-market-plane.json, read 06:40Z): live at 05:16:49Z,
#: oomKilled at 2Gi at 06:10:01Z and 06:12:10Z
RC4_PLANE_SID = "srv-db3idqvavr4c739udecg"
RC4_PLANE_EVENTS = [
    {"event": {"details": {"instanceID": "srv-db3idqvavr4c739udecg-z2qbz",
                           "reason": {"evicted": False, "oomKilled": {
                               "memoryLimit": "2Gi"}}},
               "id": "evt-db3j8eh42hec7397skfg",
               "serviceId": "srv-db3idqvavr4c739udecg",
               "timestamp": "2026-10-08T06:12:10.439986Z",
               "type": "server_failed"},
     "cursor": "GHojwD2U2lM4ZWg0MmhlYzczOTdza2Zn"},
    {"event": {"details": {}, "id": "evt-db3j8ch42hec7397shd0",
               "serviceId": "srv-db3idqvavr4c739udecg",
               "timestamp": "2026-10-08T06:12:02.361794Z",
               "type": "server_available"},
     "cursor": "GHojwD2U2lM4Y2g0MmhlYzczOTdzaGQw"},
    {"event": {"details": {"instanceID": "srv-db3idqvavr4c739udecg-z2qbz",
                           "reason": {"evicted": False, "oomKilled": {
                               "memoryLimit": "2Gi"}}},
               "id": "evt-db3j7e942hec7397rf3g",
               "serviceId": "srv-db3idqvavr4c739udecg",
               "timestamp": "2026-10-08T06:10:01.514668Z",
               "type": "server_failed"},
     "cursor": "GHojwD2U2lM3ZTk0MmhlYzczOTdyZjNn"},
    {"event": {"details": {"deployId": "dep-db3idrfavr4c739udh40",
                           "deployStatus": "succeeded", "reason": {},
                           "status": 2},
               "id": "evt-db3iegb2blpc73buh4n0",
               "serviceId": "srv-db3idqvavr4c739udecg",
               "timestamp": "2026-10-08T05:16:49.375568Z",
               "type": "deploy_ended"},
     "cursor": "DvXqQqwohGxlZ2IyYmxwYzczYnVoNG4w"},
    {"event": {"details": {"buildId": "bld-db3idrfavr4c739udh4g",
                           "buildStatus": "succeeded", "reason": {},
                           "status": 2},
               "id": "evt-db3iedjl091s7395v6v0",
               "serviceId": "srv-db3idqvavr4c739udecg",
               "timestamp": "2026-10-08T05:16:38.666745Z",
               "type": "build_ended"},
     "cursor": "DvXqQqwohGxlZGpsMDkxczczOTV2NnYw"},
    {"event": {"details": {"buildId": "bld-db3idrfavr4c739udh4g",
                           "trigger": {"clearCache": False,
                                       "deployedByRender": False,
                                       "envUpdated": False,
                                       "firstBuild": True, "manual": False,
                                       "rollback": False}},
               "id": "evt-db3idrqtliuc73d19do0",
               "serviceId": "srv-db3idqvavr4c739udecg",
               "timestamp": "2026-10-08T05:15:25.575813Z",
               "type": "build_started"},
     "cursor": "DvXqQqwohGxkcnF0bGl1YzczZDE5ZG8w"},
    {"event": {"details": {"deployId": "dep-db3idrfavr4c739udh40",
                           "trigger": {"clearCache": False,
                                       "deployedByRender": False,
                                       "envUpdated": False,
                                       "firstBuild": True, "manual": False,
                                       "rollback": False}},
               "id": "evt-db3idr8br16s73e5fu00",
               "serviceId": "srv-db3idqvavr4c739udecg",
               "timestamp": "2026-10-08T05:15:25.545198Z",
               "type": "deploy_started"},
     "cursor": "DvXqQqwohGxkcjhicjE2czczZTVmdTAw"}]
RC4_PLANE_DEPLOY = {"id": "dep-db3idrfavr4c739udh40",
                    "commit": {"id": "7fd4574e9ac8b95c355035a5bd4a9927d01c29ea"},
                    "status": "live", "trigger": "manual",
                    "createdAt": "2026-10-08T05:15:25.316436Z",
                    "startedAt": "2026-10-08T05:15:25.314831Z",
                    "finishedAt": "2026-10-08T05:16:49.299438Z"}

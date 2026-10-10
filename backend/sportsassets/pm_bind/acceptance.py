"""THE INDEPENDENT FINAL PM ACCEPTANCE HARNESS (PM Evidence Pack, component
3) FED FROM MACHINE EVIDENCE ONLY.

The harness (pm_evidence.pm_acceptance.harness, byte for byte) decides RED
/ YELLOW / GREEN. This module only COLLECTS its input, field by field, from
machine sources, each with its as_of and source:

  release (CI)       the release receipt the pm-acceptance workflow posts
                     after reading GitHub (exact-SHA gate conclusions,
                     ancestry) -- accepted by the API only when its deployed
                     SHA is the API's own RENDER_GIT_COMMIT
  runtime            workers_memory (completion readback); the no-OOM window
                     ONLY from the Render runtime receipt (below)
  market data        held / priority freshness, PMX source, Kalshi health
  controls           the red-team interlock (truth quorum, credential
                     classes, migration integrity, twin, capacity, breakers)
  economics          the forward scoreboard, the profitability governor
  PAPER history      ONLY from a pre / post fingerprint receipt (below)

A field with no machine evidence is LEFT OUT (the harness then fails that
gate) and named in `unproven` with its reason: missing evidence is never
success, and nothing here is typed from a status narrative.

TWO FACTS THE API CANNOT ESTABLISH ABOUT ITSELF (PM review of RC4,
2026-10-08, pm-acceptance run 37738089957 on 7fd4574e):

  historical_paper_immutable  was hard-coded True ("PAPER ledger
      append-only guards"). A guard is a reason to expect immutability,
      not a measurement of it. It is now bound ONLY to two independently
      collected fingerprints of the PAPER history (pm-acceptance reads the
      database read-only with its own connection): a PRE receipt captured
      before the deploy and a POST receipt recomputed at the SAME fixed
      cutoff, with a committed-row watermark, deterministic ordering and
      the same immutable row set per table (paper_history()). Missing
      baseline, a different cutoff, an unsettled watermark, a different
      schema or an unverified baseline is UNPROVEN (left out, named);
      a differing row set is CHANGED (False). Never True by default.

  no_oom_minutes  was the shared workers' process age
      (minutes_since_process_start): 76.7 in that run, a passing value,
      while sportsassets-market-plane had been oomKilled twice (06:10:01Z,
      06:12:10Z, 2Gi) since it went live at 05:16:49Z -- events the job
      had read and the harness never saw. It is now bound ONLY to the
      Render runtime receipt (runtime_window()): the bound API / workers /
      market-plane service ids, their live deploy id and commit, instance
      ids, the COMPLETE paginated event window since each went live, and
      the oomKilled / server_failed / restart verdict over it. A failure
      in the window is 0 clean minutes; a missing, malformed, truncated or
      wrong-service read is UNKNOWN (left out), never zero failures.

Both are bound by the pm-acceptance job (independent()); the API's own read
has neither receipt and reports them as unproven.
"""
from __future__ import annotations

import json
import pathlib
import re
from datetime import datetime

from ..pm_evidence.pm_acceptance import harness as H

VERSION = "PM_ACCEPTANCE_BIND_V2"
DATA = pathlib.Path(__file__).resolve().parents[1] / "pm_evidence" / "data"

PROVEN, CHANGED, UNPROVEN = "PROVEN", "CHANGED", "UNPROVEN"
CLEAN, FAILED, UNKNOWN = "CLEAN", "FAILED", "UNKNOWN"

# ── PAPER history refusals: never GREEN without both receipts ──────────────
R_PAPER_RECEIPT_ABSENT = "PAPER_HISTORY_RECEIPT_ABSENT"
R_PAPER_BASELINE_ABSENT = "PAPER_HISTORY_BASELINE_ABSENT"
R_PAPER_POST_ABSENT = "PAPER_HISTORY_POST_FINGERPRINT_ABSENT"
R_PAPER_MALFORMED = "PAPER_HISTORY_FINGERPRINT_MALFORMED"
R_PAPER_TABLE_NOT_IN_RECEIPT = "PAPER_HISTORY_TABLE_NOT_IN_RECEIPT"
R_PAPER_CUTOFF_MISMATCH = "PAPER_HISTORY_CUTOFF_MISMATCH"
R_PAPER_DIFFERENT_DATABASE = "PAPER_HISTORY_DIFFERENT_DATABASE"
R_PAPER_SESSION_SETTINGS_DIFFER = "PAPER_HISTORY_SESSION_SETTINGS_DIFFER"
R_PAPER_POST_NOT_AFTER_BASELINE = "PAPER_HISTORY_POST_NOT_AFTER_BASELINE"
R_PAPER_BASELINE_NOT_SETTLED = "PAPER_HISTORY_BASELINE_WATERMARK_NOT_SETTLED"
R_PAPER_POST_NOT_SETTLED = "PAPER_HISTORY_POST_WATERMARK_NOT_SETTLED"
R_PAPER_BASELINE_UNVERIFIED = "PAPER_HISTORY_BASELINE_PROVENANCE_UNVERIFIED"
R_PAPER_BASELINE_NOT_BEFORE_DEPLOY = "PAPER_HISTORY_BASELINE_NOT_BEFORE_DEPLOY"
R_PAPER_DEPLOY_TIME_UNKNOWN = "PAPER_HISTORY_DEPLOY_TIME_UNKNOWN"
R_PAPER_COLUMNS_CHANGED = "PAPER_HISTORY_COLUMNS_CHANGED"
R_PAPER_HISTORY_CHANGED = "PAPER_HISTORY_CHANGED"
R_PAPER_BACKDATED_ROWS = "PAPER_HISTORY_BACKDATED_ROWS"

# ── runtime-window refusals: never zero failures without a complete read ───
R_RUNTIME_RECEIPT_ABSENT = "RUNTIME_WINDOW_RECEIPT_ABSENT"
R_RUNTIME_SERVICE_NOT_READ = "RUNTIME_SERVICE_NOT_READ"
R_RUNTIME_SERVICE_LOOKUP_UNREADABLE = "RUNTIME_SERVICE_LOOKUP_UNREADABLE"
R_RUNTIME_SERVICE_ABSENT = "RUNTIME_SERVICE_ABSENT"
R_RUNTIME_SERVICE_AMBIGUOUS = "RUNTIME_SERVICE_AMBIGUOUS"
R_RUNTIME_SERVICE_SUSPENDED = "RUNTIME_SERVICE_SUSPENDED"
R_RUNTIME_DEPLOYS_UNREADABLE = "RUNTIME_DEPLOYS_UNREADABLE"
R_RUNTIME_LIVE_DEPLOY_NOT_FOUND = "RUNTIME_LIVE_DEPLOY_NOT_FOUND"
R_RUNTIME_LIVE_DEPLOY_AMBIGUOUS = "RUNTIME_LIVE_DEPLOY_AMBIGUOUS"
R_RUNTIME_DEPLOY_NOT_ON_RELEASE = "RUNTIME_DEPLOY_NOT_ON_RELEASE_SHA"
R_RUNTIME_LIVE_SINCE_UNREADABLE = "RUNTIME_LIVE_SINCE_UNREADABLE"
R_RUNTIME_WINDOW_UNREADABLE = "RUNTIME_EVENT_WINDOW_UNREADABLE"
R_RUNTIME_WINDOW_NOT_COVERED = "RUNTIME_EVENT_WINDOW_DOES_NOT_COVER_LIVE"
R_RUNTIME_EVENTS_READ_FAILED = "RUNTIME_EVENTS_READ_FAILED"
R_RUNTIME_EVENTS_MALFORMED = "RUNTIME_EVENTS_MALFORMED"
R_RUNTIME_EVENTS_CURSOR_CHAIN = "RUNTIME_EVENTS_CURSOR_CHAIN_BROKEN"
R_RUNTIME_EVENTS_TRUNCATED = "RUNTIME_EVENTS_TRUNCATED"
R_RUNTIME_EVENT_WRONG_SERVICE = "RUNTIME_EVENT_OF_ANOTHER_SERVICE"
R_RUNTIME_EVENT_WRONG_INSTANCE = "RUNTIME_EVENT_OF_ANOTHER_SERVICES_INSTANCE"
R_RUNTIME_OTHER_DEPLOY_IN_WINDOW = "RUNTIME_OTHER_DEPLOY_IN_WINDOW"
R_RUNTIME_UNCLASSIFIED_EVENT = "RUNTIME_UNCLASSIFIED_EVENT_IN_WINDOW"
R_RUNTIME_METRICS_UNREADABLE = "RUNTIME_INSTANCE_METRICS_UNREADABLE"
R_RUNTIME_METRICS_WRONG_SERVICE = "RUNTIME_METRICS_OF_ANOTHER_SERVICE"
R_RUNTIME_WRONG_INSTANCE = "RUNTIME_INSTANCE_OF_ANOTHER_SERVICE"
R_RUNTIME_NO_INSTANCE_OBSERVED = "RUNTIME_NO_INSTANCE_OBSERVED_IN_WINDOW"
R_RUNTIME_FAILURE_IN_WINDOW = "RUNTIME_FAILURE_EVENT_IN_WINDOW"

# ── PMX primary: the DEDICATED plane's current snapshot only ───────────────
R_PMX_SNAPSHOT_NOT_CURRENT = "PMX_PLANE_SNAPSHOT_NOT_CURRENT"
R_PMX_PLANE_NOT_DEDICATED = "PMX_SNAPSHOT_NOT_FROM_A_RUNNING_DEDICATED_PLANE"

#: the three Render services one release runs on; each is bound by name to
#: exactly one service id, and every runtime read must carry that id
SERVICES = ("sportsassets-api", "sportsassets-workers",
            "sportsassets-market-plane")
#: event types that end or interrupt a clean run of an instance. Render
#: reports an OOM as server_failed with details.reason.oomKilled (all 46
#: oomKilled events in the RC4 packet), a failed health check as
#: server_failed with reason.unhealthy, an operator restart as
#: server_restarted.
FAILURE_EVENT_TYPES = frozenset({"server_failed", "server_restarted",
                                 "server_hardware_failure"})
#: event types that say nothing against the running instance. Anything not
#: here and not a failure is UNCLASSIFIED: the window is not called clean
#: over an event nobody has classified.
BENIGN_EVENT_TYPES = frozenset({"server_available", "build_started",
                                "build_ended", "deploy_started",
                                "deploy_ended", "commit_ignored"})
DEPLOY_EVENT_TYPES = frozenset({"deploy_started", "deploy_ended"})

#: the PRE receipt is admissible only when its cutoff lies at least this far
#: behind the capture: every PAPER row is stamped by the database
#: (clock_timestamp() / now()) inside a transaction that is short, so ten
#: minutes after the cutoff no row below it can still be in flight -- and
#: the receipt ALSO proves no writing transaction of the application role
#: was open since before the cutoff (writers_open_since_before_cutoff = 0).
#: An evidence-quality bound on the receipt, not an acceptance threshold.
WATERMARK_SETTLE_S = 600.0

PAPER_FP_VERSION = "PAPER_HISTORY_FINGERPRINT_V1"
#: the immutable row set per table (rows below the cutoff by the PM's
#: business column AND committed below it by the database's own stamp) and
#: the fields that must match exactly between PRE and POST
IMMUTABLE_TABLES = {
    "paper_ledger": ("rows", "max_seq", "cash_delta_total_usd", "digest"),
    "paper_fills": ("rows", "notional_usd", "fees_usd", "digest"),
    "paper_settlements": ("rows", "payout_total_usd", "digest"),
    "paper_order_events": ("rows", "max_event_id", "digest"),
    "paper_decisions": ("rows", "digest"),
    "paper_accounts": ("rows", "starting_cash_usd", "digest"),
    "paper_orders": ("rows", "digest"),
}
#: tables whose business stamp is written by the application (filled_at,
#: decided_at): a row with such a stamp below the cutoff that the database
#: recorded after the PRE capture is a BACKDATED insert into history
BACKDATABLE = ("paper_fills", "paper_decisions")
#: legitimately moving projections, reconciled separately and never counted
#: as a history change: accounts gain rows, open orders change state
PROJECTIONS = {
    "paper_accounts": ("rows_all", "digest_all", "rows_since_cutoff"),
    "paper_orders": ("rows", "digest", "open_rows", "rows_since_cutoff"),
}
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_SRV = re.compile(r"^srv-[a-z0-9]+$")
_FRAC = re.compile(r"(\.\d{6})\d+")
PM_ACCEPTANCE_WORKFLOW = ".github/workflows/pm-acceptance.yml"


def spec() -> dict:
    return json.loads((DATA / "acceptance_spec.json").read_text())


def _ts(v) -> float | None:
    """An ISO-8601 instant (Render / Postgres / GitHub shapes, 'Z' or an
    offset, any fraction) as epoch seconds; anything else None."""
    if not isinstance(v, str) or not v.strip():
        return None
    s = _FRAC.sub(r"\1", v.strip()).replace(" ", "T", 1)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        return None
    return d.timestamp()


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
        return int(v)
    return None


# ── the PAPER history receipt ───────────────────────────────────────────────

def _fp_problems(fp, role: str) -> list:
    """Shape problems of one fingerprint (PRE or POST); any is UNPROVEN."""
    if not isinstance(fp, dict):
        return ["%s:%s" % (R_PAPER_MALFORMED, role)]
    bad = []
    if fp.get("version") != PAPER_FP_VERSION:
        bad.append("%s:%s:version" % (R_PAPER_MALFORMED, role))
    for k in ("cutoff", "captured_at"):
        if _ts(fp.get(k)) is None:
            bad.append("%s:%s:%s" % (R_PAPER_MALFORMED, role, k))
    if not isinstance(fp.get("database"), str) or not fp.get("database"):
        bad.append("%s:%s:database" % (R_PAPER_MALFORMED, role))
    wm = fp.get("watermark")
    if not isinstance(wm, dict) or not isinstance(
            wm.get("cutoff_lag_s"), (int, float)) or isinstance(
            wm.get("cutoff_lag_s"), bool) or _num(
            wm.get("writers_open_since_before_cutoff")) is None:
        bad.append("%s:%s:watermark" % (R_PAPER_MALFORMED, role))
    tables = fp.get("tables")
    cols = fp.get("columns")
    if not isinstance(tables, dict) or not isinstance(cols, dict):
        return bad + ["%s:%s:tables" % (R_PAPER_MALFORMED, role)]
    for t, fields in IMMUTABLE_TABLES.items():
        row = tables.get(t)
        if not isinstance(row, dict) or not all(f in row for f in fields):
            bad.append("%s:%s:%s" % (R_PAPER_TABLE_NOT_IN_RECEIPT, role, t))
            continue
        n = _num(row.get("rows"))
        d = row.get("digest")
        # md5(string_agg(...)) over zero rows is NULL; over any row a hex
        if n is None or n < 0 or (n == 0 and d is not None) or (
                n > 0 and not (isinstance(d, str) and _HEX32.match(d))):
            bad.append("%s:%s:%s" % (R_PAPER_MALFORMED, role, t))
        if t in BACKDATABLE and _num(row.get("late_recorded_rows")) is None:
            bad.append("%s:%s:%s.late_recorded_rows" % (R_PAPER_MALFORMED,
                                                         role, t))
        if not isinstance(cols.get(t), str) or not cols.get(t):
            bad.append("%s:%s:%s.columns" % (R_PAPER_TABLE_NOT_IN_RECEIPT,
                                             role, t))
    return bad


def _baseline_problems(meta) -> list:
    """The PRE receipt must be the verified capture of an earlier run of
    THIS workflow: attested packet, hash of the capture inside it."""
    if not isinstance(meta, dict):
        return [R_PAPER_BASELINE_UNVERIFIED + ":NO_PROVENANCE"]
    bad = []
    if meta.get("workflow_path") != PM_ACCEPTANCE_WORKFLOW:
        bad.append(R_PAPER_BASELINE_UNVERIFIED + ":NOT_PM_ACCEPTANCE")
    if meta.get("attestation_verified") is not True:
        bad.append(R_PAPER_BASELINE_UNVERIFIED + ":ATTESTATION")
    h, inp = meta.get("capture_sha256"), meta.get("capture_sha256_in_packet")
    if not (isinstance(h, str) and len(h) == 64 and h == inp):
        bad.append(R_PAPER_BASELINE_UNVERIFIED + ":CAPTURE_HASH")
    return bad


def paper_history(pre, post, *, baseline=None,
                  deploy_started_at: float | None = None) -> dict:
    """PROVEN only when the PRE (pre-deploy, verified) and POST fingerprints
    were taken at the SAME cutoff of the same database with the same
    session rendering and schema, both watermarks settled, and every
    immutable row set matches exactly. A differing row set is CHANGED; any
    gap in the evidence is UNPROVEN. Never PROVEN by default."""
    r = {"version": PAPER_FP_VERSION, "status": UNPROVEN, "immutable": None,
         "reasons": [], "cutoff": None, "tables": {}, "projections": {},
         "pre_captured_at": None, "post_captured_at": None}
    why = r["reasons"]
    if pre is None:
        why.append(R_PAPER_BASELINE_ABSENT)
    if post is None:
        why.append(R_PAPER_POST_ABSENT)
    if why:
        return r
    why += _fp_problems(pre, "pre") + _fp_problems(post, "post")
    if why:
        return r
    r.update(cutoff=pre["cutoff"], pre_captured_at=pre["captured_at"],
             post_captured_at=post["captured_at"])
    c_pre, c_post = _ts(pre["cutoff"]), _ts(post["cutoff"])
    if c_pre != c_post:
        why.append("%s:%s!=%s" % (R_PAPER_CUTOFF_MISMATCH, pre["cutoff"],
                                  post["cutoff"]))
    if pre["database"] != post["database"]:
        why.append(R_PAPER_DIFFERENT_DATABASE)
    if (pre.get("settings") or {}) != (post.get("settings") or {}) or not \
            pre.get("settings"):
        why.append(R_PAPER_SESSION_SETTINGS_DIFFER)
    t_pre, t_post = _ts(pre["captured_at"]), _ts(post["captured_at"])
    if not t_post > t_pre:
        why.append(R_PAPER_POST_NOT_AFTER_BASELINE)
    for fp, code in ((pre, R_PAPER_BASELINE_NOT_SETTLED),
                     (post, R_PAPER_POST_NOT_SETTLED)):
        wm = fp["watermark"]
        if float(wm["cutoff_lag_s"]) < WATERMARK_SETTLE_S or _num(
                wm["writers_open_since_before_cutoff"]) != 0:
            why.append(code)
    why += _baseline_problems(baseline)
    if deploy_started_at is None:
        why.append(R_PAPER_DEPLOY_TIME_UNKNOWN)
    elif not t_pre < deploy_started_at:
        why.append(R_PAPER_BASELINE_NOT_BEFORE_DEPLOY)
    if why:
        return r
    changed = []
    for t, fields in IMMUTABLE_TABLES.items():
        a, b = pre["tables"][t], post["tables"][t]
        if pre["columns"][t] != post["columns"][t]:
            # a schema change makes full-row digests incomparable: never a
            # claim of mutation, never a pass
            why.append("%s:%s" % (R_PAPER_COLUMNS_CHANGED, t))
            r["tables"][t] = {"match": None, "diff": ["columns"]}
            continue
        diff = [f for f in fields if a.get(f) != b.get(f)]
        if t in BACKDATABLE and _num(b["late_recorded_rows"]) > _num(
                a["late_recorded_rows"]):
            diff.append("late_recorded_rows")
            changed.append("%s:%s" % (R_PAPER_BACKDATED_ROWS, t))
        changed += ["%s:%s:%s" % (R_PAPER_HISTORY_CHANGED, t, f)
                    for f in diff if f != "late_recorded_rows"]
        r["tables"][t] = {"match": not diff, "diff": diff,
                          "rows": _num(b.get("rows"))}
    r["projections"] = _projections(pre, post)
    if changed:
        r.update(status=CHANGED, immutable=False)
        why[:0] = changed
    elif not why:
        r.update(status=PROVEN, immutable=True)
    return r


def _projections(pre, post) -> dict:
    """Account / order projections move legitimately (new accounts, open
    orders filling or expiring). They are reported and reconciled here,
    NEVER counted as history: their immutable parts (pre-cutoff account
    rows, order entry terms) are already in IMMUTABLE_TABLES."""
    out = {}
    pp, qq = pre.get("projections") or {}, post.get("projections") or {}
    for t, fields in PROJECTIONS.items():
        a, b = pp.get(t), qq.get(t)
        if not isinstance(a, dict) or not isinstance(b, dict):
            out[t] = {"status": "NOT_IN_RECEIPT"}
            continue
        o = {"moved": [f for f in fields if a.get(f) != b.get(f)],
             "pre": {f: a.get(f) for f in fields},
             "post": {f: b.get(f) for f in fields}}
        if t == "paper_accounts":
            # every account row the table gained was created at or after
            # the cutoff: rows_all - rows_since_cutoff is the immutable set
            pa = (_num(a.get("rows_all")), _num(a.get("rows_since_cutoff")))
            pb = (_num(b.get("rows_all")), _num(b.get("rows_since_cutoff")))
            ok = None not in pa + pb and pa[0] - pa[1] == pb[0] - pb[1]
            o["status"] = "RECONCILED" if ok else "NOT_RECONCILED"
            o["accounts_added"] = (pb[1] - pa[1]) if ok else None
        else:
            o["status"] = "RECONCILED_AS_PROJECTION"
        out[t] = o
    return out


# ── the Render runtime receipt ─────────────────────────────────────────────

def _labels(series) -> dict:
    return {x.get("field"): x.get("value") for x in (
        series.get("labels") or []) if isinstance(x, dict)}


def _service_window(name: str, s, *, expected_commit: str | None,
                    collected_at: float | None) -> dict:
    r = {"service": name, "status": UNKNOWN, "reasons": [],
         "service_id": None, "deploy_id": None, "commit": None,
         "live_since": None, "deploy_started_at": None, "window": None,
         "pages": 0, "events_in_window": 0, "failures": [], "instances": []}
    why = r["reasons"].append
    if not isinstance(s, dict):
        why(R_RUNTIME_SERVICE_NOT_READ)
        return r
    # 1. exactly one Render service of this exact name
    lookup = s.get("lookup")
    if not isinstance(lookup, list):
        why(R_RUNTIME_SERVICE_LOOKUP_UNREADABLE)
        return r
    hits = [x["service"] for x in lookup if isinstance(x, dict)
            and isinstance(x.get("service"), dict)
            and x["service"].get("name") == name]
    if not hits:
        why(R_RUNTIME_SERVICE_ABSENT)
        return r
    if len(hits) > 1:
        why(R_RUNTIME_SERVICE_AMBIGUOUS)
        return r
    sid = hits[0].get("id")
    if not isinstance(sid, str) or not _SRV.match(sid):
        why(R_RUNTIME_SERVICE_LOOKUP_UNREADABLE)
        return r
    r["service_id"] = sid
    if hits[0].get("suspended") not in (None, "not_suspended"):
        why(R_RUNTIME_SERVICE_SUSPENDED)
    # 2. its one live deploy, on the release commit
    deploys = s.get("deploys")
    if not isinstance(deploys, list):
        why(R_RUNTIME_DEPLOYS_UNREADABLE)
        return r
    live = [x["deploy"] for x in deploys if isinstance(x, dict)
            and isinstance(x.get("deploy"), dict)
            and x["deploy"].get("status") == "live"]
    if not live:
        why(R_RUNTIME_LIVE_DEPLOY_NOT_FOUND)
        return r
    if len(live) > 1:
        why(R_RUNTIME_LIVE_DEPLOY_AMBIGUOUS)
        return r
    dep = live[0]
    r["deploy_id"] = dep.get("id")
    r["commit"] = (dep.get("commit") or {}).get("id")
    r["live_since"] = dep.get("finishedAt")
    r["deploy_started_at"] = _ts(dep.get("createdAt"))
    if not expected_commit or r["commit"] != expected_commit:
        why("%s:%s" % (R_RUNTIME_DEPLOY_NOT_ON_RELEASE,
                       str(r["commit"])[:12]))
    start = _ts(dep.get("finishedAt"))
    if start is None or not r["deploy_id"]:
        why(R_RUNTIME_LIVE_SINCE_UNREADABLE)
        return r
    # 3. the event window: requested from live_since, complete pagination
    meta, pages = s.get("events_meta"), s.get("event_pages")
    if not isinstance(meta, dict) or not isinstance(pages, list):
        why(R_RUNTIME_WINDOW_UNREADABLE)
        return r
    w0, w1 = _ts(meta.get("start")), _ts(meta.get("end"))
    limit = _num(meta.get("limit"))
    reqs = meta.get("requests")
    if w0 is None or w1 is None or not limit or limit <= 0 or not \
            isinstance(reqs, list) or len(reqs) != len(pages) or not reqs:
        why(R_RUNTIME_WINDOW_UNREADABLE)
        return r
    if w0 > start or w1 <= start or (collected_at is not None
                                     and w1 > collected_at + 1.0):
        why(R_RUNTIME_WINDOW_NOT_COVERED)
    r["window"] = {"start": dep.get("finishedAt"), "end": meta.get("end"),
                   "minutes": round((w1 - start) / 60.0, 1)}
    # the pages that were read, in order, up to the first that was not: a
    # failure found in them still counts (one OOM is enough), but the
    # window is complete only if every page was read and chained
    good, prev_cursor = [], ""
    for i, (q, page) in enumerate(zip(reqs, pages)):
        if not isinstance(q, dict) or str(q.get("http")) != "200":
            why("%s:page%d" % (R_RUNTIME_EVENTS_READ_FAILED, i))
            break
        if not isinstance(page, list):
            why("%s:page%d" % (R_RUNTIME_EVENTS_MALFORMED, i))
            break
        if (q.get("cursor") or "") != prev_cursor:
            why("%s:page%d" % (R_RUNTIME_EVENTS_CURSOR_CHAIN, i))
            break
        good.append(page)
        prev_cursor = (page[-1].get("cursor") or "") if page and \
            isinstance(page[-1], dict) else ""
    r["pages"] = len(good)
    if len(good) == len(pages):
        last = pages[-1]
        oldest = [_ts((x.get("event") or {}).get("timestamp")) for x in last
                  if isinstance(x, dict)]
        oldest = [t for t in oldest if t is not None]
        if not (len(last) < limit or (oldest and min(oldest) < w0)):
            # a full last page with no proof it reached back past
            # live_since: the rest of the window was never read
            why(R_RUNTIME_EVENTS_TRUNCATED)
    seen = set()
    for page in good:
        for item in page:
            ev = item.get("event") if isinstance(item, dict) else None
            if not isinstance(ev, dict) or not all(
                    ev.get(k) for k in ("id", "timestamp", "type",
                                        "serviceId")) or _ts(
                    ev.get("timestamp")) is None:
                why(R_RUNTIME_EVENTS_MALFORMED)
                continue
            if ev["id"] in seen:
                continue
            seen.add(ev["id"])
            if ev["serviceId"] != sid:
                why("%s:%s" % (R_RUNTIME_EVENT_WRONG_SERVICE,
                               ev["serviceId"]))
                continue
            det = ev.get("details") if isinstance(ev.get("details"),
                                                  dict) else {}
            inst = det.get("instanceID")
            if inst is not None and not str(inst).startswith(sid + "-"):
                why("%s:%s" % (R_RUNTIME_EVENT_WRONG_INSTANCE, inst))
                continue
            t = _ts(ev["timestamp"])
            if t < start or t > w1:
                continue
            r["events_in_window"] += 1
            reason = det.get("reason") if isinstance(det.get("reason"),
                                                     dict) else {}
            if "oomKilled" in reason or ev["type"] in FAILURE_EVENT_TYPES:
                r["failures"].append({
                    "id": ev["id"], "timestamp": ev["timestamp"],
                    "type": ev["type"], "instance": inst,
                    "oom_killed": "oomKilled" in reason,
                    "reason": sorted(k for k in reason if k != "evicted")})
            elif ev["type"] in DEPLOY_EVENT_TYPES and det.get(
                    "deployId") != r["deploy_id"]:
                why("%s:%s" % (R_RUNTIME_OTHER_DEPLOY_IN_WINDOW,
                               det.get("deployId")))
            elif ev["type"] not in BENIGN_EVENT_TYPES:
                why("%s:%s" % (R_RUNTIME_UNCLASSIFIED_EVENT, ev["type"]))
    # 4. the instances that ran in the window, each of THIS service
    mem = s.get("memory")
    if not isinstance(mem, list) or str(s.get("memory_http", "200")) != \
            "200":
        why(R_RUNTIME_METRICS_UNREADABLE)
    else:
        insts = set()
        for series in mem:
            if not isinstance(series, dict):
                why(R_RUNTIME_METRICS_UNREADABLE)
                continue
            lab = _labels(series)
            for k in ("service", "resource"):
                if lab.get(k) is not None and lab.get(k) != sid:
                    why("%s:%s" % (R_RUNTIME_METRICS_WRONG_SERVICE,
                                   lab.get(k)))
            inst = lab.get("instance")
            if not isinstance(inst, str) or not inst.startswith(sid + "-"):
                why("%s:%s" % (R_RUNTIME_WRONG_INSTANCE, inst))
                continue
            if any(isinstance(v, dict) and v.get("value") is not None and
                   (_ts(v.get("timestamp")) or 0) >= start
                   for v in series.get("values") or []):
                insts.add(inst)
        r["instances"] = sorted(insts)
        if not insts:
            why(R_RUNTIME_NO_INSTANCE_OBSERVED)
    if r["failures"]:
        r["status"] = FAILED
        why("%s:%d" % (R_RUNTIME_FAILURE_IN_WINDOW, len(r["failures"])))
    elif not r["reasons"]:
        r["status"] = CLEAN
    return r


def runtime_window(raw, *, expected_commit: str | None) -> dict:
    """The no-OOM verdict for the release over its three services.

    FAILED (no_oom_minutes = 0): an oomKilled / server_failed / restart
    event of a bound instance since its service went live -- one is enough,
    whatever else is unknown. CLEAN (no_oom_minutes = the shortest
    observed window): every service bound, its live deploy on
    expected_commit (the SHA under test: tested = deployed), its event
    window complete and nothing in it. Otherwise UNKNOWN (no_oom_minutes
    None: the field is left out, the gate fails, the reasons are named)."""
    out = {"status": UNKNOWN, "no_oom_minutes": None,
           "observation_minutes": None, "expected_commit": expected_commit,
           "collected_at": None, "reasons": [], "services": {},
           "deploy_started_at": None}
    if not isinstance(raw, dict) or not isinstance(raw.get("services"),
                                                   dict):
        out["reasons"].append(R_RUNTIME_RECEIPT_ABSENT)
        return out
    out["collected_at"] = raw.get("collected_at")
    end = _ts(raw.get("collected_at"))
    if end is None:
        out["reasons"].append(R_RUNTIME_WINDOW_UNREADABLE)
    for svc in SERVICES:
        w = _service_window(svc, raw["services"].get(svc),
                            expected_commit=expected_commit,
                            collected_at=end)
        out["services"][svc] = w
        out["reasons"] += ["%s:%s" % (svc, x) for x in w["reasons"]]
    ws = list(out["services"].values())
    starts = [w["deploy_started_at"] for w in ws]
    if None not in starts:
        out["deploy_started_at"] = min(starts)
    if any(w["status"] == FAILED for w in ws):
        out.update(status=FAILED, no_oom_minutes=0.0)
    elif end is not None and all(w["status"] == CLEAN for w in ws):
        mins = min(w["window"]["minutes"] for w in ws)
        out.update(status=CLEAN, no_oom_minutes=mins,
                   observation_minutes=mins)
    return out


def render_summary(raw, *, expected_commit: str | None) -> dict:
    """render.json for the receipt body and the step summary, from the SAME
    verified window (never a separate unpaginated count)."""
    rw = runtime_window(raw, expected_commit=expected_commit)
    out = {}
    for svc, w in rw["services"].items():
        f = w["failures"]
        out[svc] = {"present": bool(w["service_id"]),
                    "service_id": w["service_id"],
                    "deploy_id": w["deploy_id"], "live_commit": w["commit"],
                    "live_since": w["live_since"],
                    "oom_events_since_live": sum(
                        1 for x in f if x["oom_killed"]),
                    "server_failed_since_live": sum(
                        1 for x in f if x["type"] == "server_failed"),
                    "restarts_since_live": sum(
                        1 for x in f if x["type"] == "server_restarted"),
                    "instances": w["instances"], "status": w["status"],
                    "reasons": w["reasons"]}
    return out


# ── binding ─────────────────────────────────────────────────────────────────

def _bind_runtime(e, prov, unproven, runtime, *, expected_commit,
                  now) -> dict:
    rw = runtime_window(runtime, expected_commit=expected_commit)
    if rw["no_oom_minutes"] is None:
        unproven["no_oom_minutes"] = rw["reasons"] or [
            R_RUNTIME_RECEIPT_ABSENT]
    else:
        e["no_oom_minutes"] = rw["no_oom_minutes"]
        prov["no_oom_minutes"] = {
            "source": "Render runtime receipt (pm-acceptance): %s; "
                      "complete paginated events since live, oomKilled / "
                      "server_failed / restart verdict %s" % (
                          ", ".join("%s=%s/%s" % (
                              k, w["service_id"], w["deploy_id"])
                              for k, w in rw["services"].items()),
                          rw["status"]),
            "as_of": now, "status": rw["status"]}
    return rw


def _bind_paper(e, prov, unproven, paper, *, deploy_started_at,
                now) -> dict:
    p = paper if isinstance(paper, dict) else {}
    ph = paper_history(p.get("pre"), p.get("post"),
                       baseline=p.get("baseline"),
                       deploy_started_at=deploy_started_at)
    if paper is None:
        ph["reasons"] = [R_PAPER_RECEIPT_ABSENT]
    if ph["immutable"] is None:
        unproven["historical_paper_immutable"] = ph["reasons"] or [
            R_PAPER_RECEIPT_ABSENT]
    else:
        e["historical_paper_immutable"] = ph["immutable"]
        prov["historical_paper_immutable"] = {
            "source": "PAPER history fingerprints (pm-acceptance, read "
                      "only): PRE %s / POST %s at the fixed cutoff %s" % (
                          ph["pre_captured_at"], ph["post_captured_at"],
                          ph["cutoff"]),
            "as_of": now, "status": ph["status"]}
    return ph


def _collect(*, red: dict, scoreboard: dict, release: dict | None,
             now: float, runtime=None, paper=None) -> tuple:
    e, prov, unproven = {}, {}, {}

    def put(k, v, source):
        if v is None:
            return
        e[k] = v
        prov[k] = {"source": source, "as_of": now}

    comp = red.get("completion") or {}
    ctr = red.get("controls") or {}
    sha = red.get("implementation_sha")
    rel = release or {}
    if rel:
        src = "red_team_release_receipts (pm-acceptance workflow: GitHub " \
              "exact-SHA gates + ancestry; deployed SHA verified by the API)"
        put("accepted_base_sha", rel.get("accepted_base_sha"), src)
        put("tested_sha", rel.get("tested_sha"), src)
        put("release_sha", rel.get("release_sha"), src)
        put("is_descendant_of_accepted_base", rel.get("descendant_of_base"),
            src)
        for k, col in (("backend_tests", "backend_tests_green"),
                       ("capital_critical", "capital_critical_green"),
                       ("commit_guard", "commit_guard_green"),
                       ("engine_diagnostic", "engine_diagnostic_green")):
            put(k, rel.get(col), src)
    blk = rel.get("blockers") or []
    if isinstance(blk, str):
        blk = json.loads(blk or "[]")
    wk_bad = [b for b in blk if str(b).startswith(
        "WORKERS_NOT_ON_RELEASE_SHA")]
    # deployed = BOTH services: a workers live commit that differs from the
    # API's (recorded by the release receipt from Render) is not deployed
    put("deployed_sha", (wk_bad[0] if wk_bad else sha)
        if sha and sha != "UNKNOWN" else None,
        "RENDER_GIT_COMMIT of the serving API" + (
            "; the workers' live commit differs (release receipt, Render)"
            if wk_bad else ""))
    mig = ctr.get("MIGRATION_INTEGRITY") or {}
    put("migration_integrity", mig.get("status") == "GREEN" if mig else None,
        "red team MIGRATION_INTEGRITY (schema_migrations vs this build)")
    sw = ((comp.get("runtime") or {}).get("shared_workers") or {})
    # no_oom_minutes is NOT the workers' process age (RC4: 76.7 min while
    # the market plane was oomKilled twice); only the runtime receipt
    rt = _bind_runtime(e, prov, unproven, runtime, expected_commit=sha or (
        rel.get("tested_sha")), now=now)
    put("worker_rss_fraction", sw.get("rss_highwater_fraction"),
        "workers_memory peak / container limit")
    put("shared_worker_starts_ump",
        sw.get("universal_market_plane_started_here")
        if sw.get("commit_sha") else None, "workers_boot.started")
    mp = ((comp.get("runtime") or {}).get("market_plane") or {})
    plane_running = mp.get("state") == "DEDICATED_RUNNING"
    put("dedicated_market_plane_present", plane_running if mp else None,
        "universal_market_plane heartbeat runtime label")
    fr = ((ctr.get("VENUE_HEALTH") or {}).get("evidence") or {}).get(
        "freshness") or {}
    held, pri = fr.get("held") or {}, fr.get("priority") or {}
    if held.get("denominator"):
        put("held_fresh", held.get("numerator"), held.get("source"))
        put("held_required", held.get("denominator"), held.get("source"))
    if pri.get("denominator"):
        put("priority_fresh", pri.get("numerator"), pri.get("source"))
        put("priority_required", pri.get("denominator"), pri.get("source"))
    q = ctr.get("TRUTH_QUORUM") or {}
    put("truth_quorum", q.get("status") == "GREEN" if q else None,
        "red team TRUTH_QUORUM")
    gates = comp.get("gate_evidence") or {}
    swg = gates.get("software_reds_zero") or {}
    ev = swg.get("evidence")
    reds = (ev.get("software") if isinstance(ev, dict)
            else swg.get("software"))
    put("software_red_count", reds if isinstance(reds, (int, float))
        else None, "capital readiness gate software_reds_zero")
    can = gates.get("production_canary_clean") or {}
    put("canary", can.get("value") if "value" in can else None,
        "capital readiness gate production_canary_clean")
    cr = ctr.get("CREDENTIAL_CLASSES") or {}
    put("credential_classes", cr.get("status") == "GREEN" if cr else None,
        "red team CREDENTIAL_CLASSES (shape only)")
    # NEVER typed True: only the pre / post fingerprint receipt binds it
    ph = _bind_paper(e, prov, unproven, paper,
                     deploy_started_at=rt.get("deploy_started_at"), now=now)
    # completion's `small_live` is a literal; the red-team readiness READS
    # the lane (execmirror_control, RC6): when it carries that reading, a
    # non-SHADOW or unread lane fails here too
    read = (red.get("authority") or {}).get("small_live")
    put("live_authority_shadow", comp.get("small_live") == "SHADOW"
        and read in (None, "SHADOW"),
        "red-team readiness authority.small_live (execmirror_control)"
        if read is not None else
        "live_authorization.SMALL_LIVE_MODE / small_live_control")
    md = comp.get("market_data") or {}
    fresh_pmx = md.get("fresh")
    pp = md.get("pmx_primary") or {}
    # THE HARNESS READS PMX FROM THE DEDICATED PLANE'S CURRENT SNAPSHOT
    # ONLY (the subscribe-all architecture the acceptance spec measures).
    # A stale snapshot is not evidence of now: with none current the source
    # is REST and the fresh count is withheld (an evidence gap), never read
    # from an old snapshot and never substituted from another process. A
    # CURRENT snapshot while the dedicated plane is not read as running is
    # not the dedicated plane's evidence either: withheld the same way.
    if md.get("snapshot") == "CURRENT" and plane_running:
        put("pmx_primary_source", "PMX_GRPC" if (fresh_pmx or 0) > 0 and
            md.get("subscription_mode") else "REST",
            "market plane snapshot subscription")
        put("pmx_grpc_fresh_count", fresh_pmx, "market plane snapshot")
    elif md:
        put("pmx_primary_source", "REST",
            "market plane snapshot not CURRENT (%s)" % md.get("snapshot")
            if md.get("snapshot") != "CURRENT" else
            "%s (runtime state %s)" % (R_PMX_PLANE_NOT_DEDICATED,
                                       mp.get("state")))
        unproven["pmx_grpc_fresh_count"] = [
            R_PMX_SNAPSHOT_NOT_CURRENT if md.get("snapshot") != "CURRENT"
            else R_PMX_PLANE_NOT_DEDICATED]
    if pp and "pmx_primary_source" in prov:
        # REPORT ONLY, never a harness input: the deciding process's own
        # stream and the held marks it produced (completion pmx_primary)
        prov["pmx_primary_source"]["deciding_process_report"] = {
            "source": pp.get("source"), "why": pp.get("why"),
            "held_fresh_from_stream": pp.get("held_fresh_from_stream"),
            "held_markable": pp.get("held_markable"),
            "evidence": "paper_mark_refresh_runs.market_data + "
                        "bettor_paper_freshness feeds"}
    kv = (((ctr.get("VENUE_HEALTH") or {}).get("evidence") or {}).get(
        "venues") or {}).get("KALSHI") or {}
    put("kalshi_market_data_current", kv.get("green") if kv else None,
        "KALSHI_HEALTH (kalshi market data heartbeat)")
    put("kalshi_mapped_markets", kv.get("denominator"),
        "KALSHI_HEALTH tracked books")
    vh = (ctr.get("VENUE_HEALTH") or {}).get("evidence") or {}
    put("venue_health_isolated", vh.get("isolated"),
        "red team VENUE_HEALTH (one entry per venue, never blended)")
    st = comp.get("settlement") or {}
    put("settlement_proven", st.get("settlement_proven") if st else None,
        "market plane settlement state of the open-position markets")
    tw = (ctr.get("DIGITAL_TWIN") or {}).get("evidence") or {}
    if tw.get("compared"):
        put("twin_compared", tw.get("compared"), "repaired fill replay")
        put("twin_matched", tw.get("matched"), "repaired fill replay")
        put("twin_optimistic_false_fills", tw.get("optimistic_false_fills"),
            "repaired fill replay")
        put("twin_lookahead_violations", tw.get("lookahead_violations"),
            "repaired fill replay")
    xav = gates.get("xavier_complete") or {}
    if "value" in xav:
        # a GREEN gate over no counted open position binds nothing (the
        # field is then without machine evidence), never 1.0 (RC6.2
        # p-xavier M-1: capital_readiness.feeds.xavier_complete_rate)
        from ..capital_readiness import feeds as _XF
        put("xavier_complete_rate", _XF.xavier_complete_rate(xav),
            "capital readiness gate xavier_complete")
    dmech = ((scoreboard or {}).get("mechanisms") or {}).get(
        "DIRECTIONAL") or {}
    lcb = dmech.get("forward_pnl_lcb_per_event")
    put("forward_edge_lower_bound", float(lcb) if lcb is not None else None,
        "forward scoreboard DIRECTIONAL event-clustered lower bound")
    cap = (ctr.get("CAPACITY") or {}).get("evidence") or {}
    if cap:
        put("proven_positive_capacity", float(
            cap.get("proven_positive_capacity_usd") or 0),
            "red team CAPACITY (bind evaluations by size)")
    gov = ((red.get("completion_readiness") or {}).get("governors") or {})
    if gov:
        put("profitability_governor", "CASH" if "CASH" in gov.values()
            else "ELIGIBLE", "completion readiness governors")
    pb = ctr.get("PROFIT_BREAKERS") or {}
    put("mechanism_breakers_green", pb.get("status") == "GREEN" if pb
        else None, "red team PROFIT_BREAKERS")
    tot = (scoreboard or {}).get("paper_ledger_realized_total_usd")
    put("paper_pnl", float(tot) if tot is not None else None,
        "PAPER ledger realized P&L (all positions)")
    return e, prov, unproven, rt, ph


def collect(*, red: dict, scoreboard: dict, release: dict | None,
            now: float, runtime=None, paper=None) -> tuple:
    """(harness input, provenance {field: {source, as_of}})."""
    e, prov, _u, _rt, _ph = _collect(red=red, scoreboard=scoreboard,
                                     release=release, now=now,
                                     runtime=runtime, paper=paper)
    return e, prov


#: the facts the pm-acceptance job binds from its OWN receipts; whatever the
#: API (any release, RC4 included) put in these fields is never carried
INDEPENDENT_FIELDS = ("historical_paper_immutable", "no_oom_minutes")


def independent(evidence: dict, *, runtime, paper, expected_commit: str,
                now: float) -> tuple:
    """The pm-acceptance job's harness input: `evidence` (the API's, with
    the CI fields already replaced by the job's own GitHub reads) with the
    INDEPENDENT_FIELDS removed and re-bound from the job's Render runtime
    receipt and PAPER fingerprints. (input, report)."""
    e = {k: v for k, v in (evidence or {}).items()
         if k not in INDEPENDENT_FIELDS}
    prov, unproven = {}, {}
    rt = _bind_runtime(e, prov, unproven, runtime,
                       expected_commit=expected_commit, now=now)
    ph = _bind_paper(e, prov, unproven, paper,
                     deploy_started_at=rt.get("deploy_started_at"), now=now)
    return e, {"runtime_window": rt, "paper_history": ph,
               "unproven": unproven, "provenance": prov,
               "replaced_from_api": {k: (evidence or {}).get(k)
                                     for k in INDEPENDENT_FIELDS
                                     if k in (evidence or {})}}


def evaluate(*, red: dict, scoreboard: dict, release: dict | None,
             now: float, runtime=None, paper=None) -> dict:
    e, prov, unproven, rt, ph = _collect(
        red=red, scoreboard=scoreboard, release=release, now=now,
        runtime=runtime, paper=paper)
    r = H.evaluate(e, spec())
    missing = sorted(set(H.CRITICAL + H.ECONOMIC) - {
        k for k, g in r["gates"].items() if g["pass"]})
    return {"version": VERSION, "as_of": now, "pm_state": r["pm_state"],
            "capital_status": r["capital_status"],
            "critical_failures": r["critical_failures"],
            "economic_or_evidence_gaps": r["economic_or_evidence_gaps"],
            "gates": r["gates"], "summary": r["summary"],
            "evidence_input": e, "provenance": prov,
            "fields_without_machine_evidence": sorted(
                set(_INPUT_FIELDS) - set(e)),
            "unproven": unproven,
            "runtime_window_status": rt["status"],
            "paper_history_status": ph["status"],
            "failing_gates": missing,
            "activates_money": False}


_INPUT_FIELDS = (
    "accepted_base_sha", "tested_sha", "release_sha", "deployed_sha",
    "is_descendant_of_accepted_base", "backend_tests", "capital_critical",
    "commit_guard", "engine_diagnostic", "migration_integrity", "truth_quorum",
    "canary", "credential_classes", "historical_paper_immutable",
    "live_authority_shadow", "no_oom_minutes", "worker_rss_fraction",
    "shared_worker_starts_ump", "dedicated_market_plane_present",
    "held_fresh", "held_required", "priority_fresh", "priority_required",
    "software_red_count", "pmx_primary_source", "pmx_grpc_fresh_count",
    "kalshi_market_data_current", "kalshi_mapped_markets",
    "venue_health_isolated", "settlement_proven", "twin_compared",
    "twin_matched", "twin_optimistic_false_fills",
    "twin_lookahead_violations", "xavier_complete_rate",
    "forward_edge_lower_bound", "proven_positive_capacity",
    "profitability_governor", "mechanism_breakers_green", "paper_pnl")


# ── the pm-acceptance job's files (acc/) ───────────────────────────────────

def _load(p: pathlib.Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def load_runtime(acc: pathlib.Path):
    """The raw Render collection the job wrote, as runtime_window() reads
    it; None when the job collected nothing (no key, step failed)."""
    acc = pathlib.Path(acc)
    col = _load(acc / "render_collection.json")
    if not isinstance(col, dict):
        return None
    out = {"collected_at": col.get("collected_at"), "services": {}}
    for svc in SERVICES:
        meta = _load(acc / ("render_events_%s.json" % svc))
        pages = None
        if isinstance(meta, dict) and isinstance(meta.get("requests"),
                                                 list):
            pages = []
            for q in meta["requests"]:
                f = q.get("page") if isinstance(q, dict) else None
                ok = isinstance(f, str) and re.fullmatch(
                    r"events_[a-z-]+_p\d{3}\.json", f)
                pages.append(_load(acc / f) if ok else None)
        out["services"][svc] = {
            "lookup": _load(acc / ("render_lookup_%s.json" % svc)),
            "deploys": _load(acc / ("deploys_%s.json" % svc)),
            "events_meta": meta, "event_pages": pages,
            "memory": _load(acc / ("mem_%s.json" % svc)),
            "memory_http": (col.get("memory_http") or {}).get(svc)}
    return out


def load_paper(acc: pathlib.Path):
    """{pre, post, baseline} as the job wrote them; None when no capture."""
    acc = pathlib.Path(acc)
    names = ("paper_history_pre.json", "paper_history_capture.json",
             "paper_history_baseline.json")
    if not any((acc / n).exists() for n in names):
        return None
    pre, post, base = (_load(acc / n) for n in names)
    return {"pre": pre, "post": post, "baseline": base}

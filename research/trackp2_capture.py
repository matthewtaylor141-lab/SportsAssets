#!/usr/bin/env python3
"""TRACK P2 capture runner: discovery -> registry -> T-60/T-10 snapshots ->
settlement follow-up.

READ ONLY. GET only, gateway.polymarket.us only, no credential, no order
path. Starts the moment egress exists; until then it reports the blocked
host and writes nothing but the refusal.

  python3 research/trackp2_capture.py --stage discover
  python3 research/trackp2_capture.py --stage snapshot
  python3 research/trackp2_capture.py --stage settle
  python3 research/trackp2_capture.py --stage analyse --checkpoint 0

STATE LIVES ON DISK, NOT IN A PROCESS. Each stage reads and rewrites
research/evidence/trackp2/registry.jsonl. A market enters once at
discovery, carries ONE identity through both snapshots and settlement, and
is never re-keyed. That single-identity rule is the whole reason the
archaeology found a zero-row join: two datasets about the same venue whose
market sets never met.
"""
from __future__ import annotations

import argparse, gzip, hashlib, importlib.util, json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, fn):
    s = importlib.util.spec_from_file_location(name, HERE / fn)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


P = _load("p2", "trackp2_protocol.py")
BL = _load("bl", "run85_trackbl_block.py")          # proven discovery walk

OUT = HERE / "evidence" / "trackp2"
REGISTRY = OUT / "registry.jsonl"
GATEWAY = "https://gateway.polymarket.us"
SPACING_S = 2.5                                      # the locked rate floor


def now():
    return datetime.now(timezone.utc)


def iso(x):
    if not x:
        return None
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except ValueError:
        return None


def load_registry():
    if not REGISTRY.exists():
        return {}
    out = {}
    with open(REGISTRY) as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                out[r["market_slug"]] = r
    return out


def save_registry(reg):
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.with_suffix(".tmp")
    with open(tmp, "w") as fh:
        for k in sorted(reg):
            fh.write(json.dumps(reg[k], sort_keys=True, default=str) + "\n")
    tmp.replace(REGISTRY)


def client():
    import httpx
    return httpx.Client(base_url=GATEWAY, timeout=30.0,
                        follow_redirects=False)


def paced_get(http, path, params, pacer):
    pacer.wait()
    wall, mono = time.time(), time.monotonic()
    r = http.get(path, params=params)
    return {"path": path, "params": params, "http_status": r.status_code,
            "LOCAL_RECEIPT_WALL": wall, "LOCAL_RECEIPT_MONOTONIC": mono,
            "body": (r.json() if r.status_code == 200 else None),
            "response_sha256": hashlib.sha256(r.content).hexdigest()}


# ------------------------------------------------------------- discovery ---
def stage_discover(say):
    """Enumerate the whole board. A page ceiling is NOT a boundary."""
    reg = load_registry()
    pacer = BL.B.AdaptivePacer(base=SPACING_S)
    pages, samples, res = [], [], {}
    with client() as http:
        events = BL.discover(http, pacer, BL.Budget(10_000), pages, samples,
                             res)
    # AN UNREACHABLE BOARD IS NOT AN EXHAUSTED BOARD. BL.discover() treats
    # a failed page as a terminal boundary -- right for a walk that ends,
    # wrong for one that never began. Without this, a blocked host reports
    # DISCOVERY_LIST_EXHAUSTED = YES with zero markets, which downstream is
    # indistinguishable from "the venue lists no sports today". Same defect
    # class as the Phase X driver's; caught here by the offline gate.
    first = pages[0] if pages else {}
    if first.get("http_status") != 200:
        raise RuntimeError(
            "PMUS discovery never returned a page: first page status=%s "
            "error=%s -- ACCESS FAILURE, not an empty board"
            % (first.get("http_status"), first.get("error")))
    terminal = res.get("DISCOVERY_LIST_EXHAUSTED") == "YES"
    say("DISCOVERY_LIST_EXHAUSTED = %s" % res.get("DISCOVERY_LIST_EXHAUSTED"))
    if not terminal:
        say("PREFIX_BOUNDED -- the population rule was NOT satisfied; "
            "recorded as such and the capture is not board-complete")
    added = excl = 0
    reasons = {}
    for ev in events:
        for m in ev.get("markets") or []:
            slug = m.get("slug")
            if not slug or slug in reg:
                continue
            why = None
            if (m.get("category") or ev.get("category")) != "sports":
                why = "NOT_A_SPORTS_MARKET"
            elif not m.get("gameStartTime"):
                why = "NO_GAME_START_TIME"
            elif (iso(m["gameStartTime"]) or now()) <= now():
                why = "GAME_START_IN_THE_PAST_AT_DISCOVERY"
            if why:
                excl += 1
                reasons[why] = reasons.get(why, 0) + 1
                reg[slug] = {"market_slug": slug, "eligible": False,
                             "exclusion": why,
                             "discovered_at": now().isoformat()}
                continue
            reg[slug] = {
                "market_slug": slug, "eligible": True, "exclusion": None,
                "assignment": P.assignment(slug),
                "event_id": ev.get("id"), "event_ticker": ev.get("ticker"),
                "event_title": ev.get("title"),
                "gameStartTime": m["gameStartTime"],
                "endDate": m.get("endDate"),
                "sportsMarketTypeV2": m.get("sportsMarketTypeV2"),
                "tags": [t.get("slug") for t in (ev.get("tags") or [])],
                "side_ids": [s.get("id") for s in
                             (m.get("marketSides") or [])],
                "side_definitions": [
                    {"id": s.get("id"), "long": s.get("long"),
                     "description": s.get("description"),
                     "team": (s.get("team") or {}).get("name")}
                    for s in (m.get("marketSides") or [])],
                "outcomes": m.get("outcomes"),
                "discovered_at": now().isoformat(),
                "snapshots": {}, "settlement": None,
                "TIME_TO_EVENT_STATUS": "VERIFIED",
                "PREFIX_BOUNDED": not terminal,
            }
            added += 1
    save_registry(reg)
    say("markets added = %d   excluded = %d %s" % (added, excl, reasons))
    say("registry size = %d (eligible %d)"
        % (len(reg), sum(1 for r in reg.values() if r.get("eligible"))))
    return 0


# -------------------------------------------------------------- snapshots ---
def stage_snapshot(say):
    """Take whichever arm is due now, within tolerance."""
    reg = load_registry()
    pacer = BL.B.AdaptivePacer(base=SPACING_S)
    due = []
    t = now().timestamp()
    for r in reg.values():
        if not r.get("eligible"):
            continue
        gs = iso(r.get("gameStartTime"))
        if gs is None:
            continue
        for arm, off in P.SNAPSHOT_OFFSETS_S.items():
            if arm in (r.get("snapshots") or {}):
                continue
            target = gs.timestamp() - off
            if abs(t - target) <= P.SNAPSHOT_TOLERANCE_S:
                due.append((r, arm, target))
    say("snapshots due now = %d" % len(due))
    if not due:
        return 0
    with client() as http:
        for r, arm, target in due:
            rec = paced_get(http, "/v1/markets/%s/book" % r["market_slug"],
                            None, pacer)
            md = (rec.get("body") or {}).get("marketData") or {}
            bids, offers = md.get("bids") or [], md.get("offers") or []

            def top(side, best):
                try:
                    return float(side[0]["px"]["value"]) if side else None
                except (KeyError, IndexError, TypeError, ValueError):
                    return None

            bb, ba = top(bids, "bid"), top(offers, "ask")
            mid = P.midpoint(bb, ba)
            st = md.get("stats") or {}
            (r.setdefault("snapshots", {}))[arm] = {
                "ARM": arm,
                "TARGET_WALL": target,
                "ACTUAL_OFFSET_S": (iso(r["gameStartTime"]).timestamp()
                                    - rec["LOCAL_RECEIPT_WALL"]),
                "http_status": rec["http_status"],
                "response_sha256": rec["response_sha256"],
                "bids": bids, "offers": offers,
                "best_bid": bb, "best_ask": ba,
                "spread": (ba - bb) if (bb is not None and ba is not None)
                          else None,
                "MIDPOINT": mid,
                "MIDPOINT_STATUS": ("VERIFIED" if mid is not None
                                    else "NOT_IDENTIFIED"),
                "status": md.get("state"),
                "lastTradePx": (st.get("lastTradePx") or {}).get("value"),
                "sharesTraded": st.get("sharesTraded"),
                "openInterest": st.get("openInterest"),
                "transactTime": md.get("transactTime"),
                "LOCAL_RECEIPT_WALL": rec["LOCAL_RECEIPT_WALL"],
                "LOCAL_RECEIPT_MONOTONIC": rec["LOCAL_RECEIPT_MONOTONIC"],
                "RAW": md,
            }
            say("  %-52s %-11s mid=%s" % (r["market_slug"], arm, mid))
    save_registry(reg)
    return 0


# ------------------------------------------------------------- settlement ---
def stage_settle(say):
    """MANDATORY follow-up. Re-read the SAME identity until terminal."""
    reg = load_registry()
    pacer = BL.B.AdaptivePacer(base=SPACING_S)
    pend = [r for r in reg.values()
            if r.get("eligible") and r.get("snapshots")
            and (r.get("settlement") or {}).get("CLASS") in (None, "PENDING")
            and (iso(r.get("endDate")) or now()) <= now()]
    say("settlement follow-ups due = %d" % len(pend))
    with client() as http:
        for r in pend:
            rec = paced_get(http, "/v1/markets/%s" % r["market_slug"], None,
                            pacer)
            m = (rec.get("body") or {}).get("market") or \
                (rec.get("body") or {}) or {}
            status = m.get("status")
            cls = ("RESOLVED" if status == "MARKET_STATUS_RESOLVED" else
                   "VOID" if status == "MARKET_STATUS_VOID" else
                   "CANCELED" if status == "MARKET_STATUS_CANCELED" else
                   "PENDING" if status else "NOT_IDENTIFIED")
            r["settlement"] = {
                "CLASS": cls, "status": status,
                "outcomePrices": m.get("outcomePrices"),
                "outcomes": m.get("outcomes"), "result": m.get("result"),
                "RETRIEVED_AT": now().isoformat(),
                "http_status": rec["http_status"],
                "response_sha256": rec["response_sha256"],
                "RAW": m,
            }
            say("  %-52s %s" % (r["market_slug"], cls))
    save_registry(reg)
    return 0


# --------------------------------------------------------------- analysis ---
def eligible_rows(reg, arm, include_final_confirmation=False):
    rows, excl = [], {}
    for r in reg.values():
        def drop(why):
            excl[why] = excl.get(why, 0) + 1
        if not r.get("eligible"):
            drop(r.get("exclusion") or "INELIGIBLE"); continue
        if not include_final_confirmation and \
                r.get("assignment") == "P2_FINAL_CONFIRMATION":
            drop("RESERVED_P2_FINAL_CONFIRMATION"); continue
        sn = (r.get("snapshots") or {}).get(arm)
        if sn is None:
            drop("NO_SNAPSHOT_IN_ARM"); continue
        if sn.get("status") != "MARKET_STATE_OPEN":
            drop("MARKET_NOT_OPEN_AT_SNAPSHOT"); continue
        if sn.get("MIDPOINT") is None:
            drop("MIDPOINT_NOT_IDENTIFIED"); continue
        s = r.get("settlement") or {}
        if s.get("CLASS") != "RESOLVED":
            drop("SETTLEMENT_" + str(s.get("CLASS") or "PENDING")); continue
        for idx, sd in enumerate(r.get("side_definitions") or []):
            y = P.outcome_of(idx, s.get("outcomePrices"))
            if y is None:
                drop("OUTCOME_NOT_ONE_HOT"); continue
            if idx != 0:
                continue          # one side per market: the LONG/first side
            rows.append({"market_slug": r["market_slug"],
                         "event_id": r.get("event_id"),
                         "sport": (r.get("tags") or ["NOT_IDENTIFIED"])[-1],
                         "midpoint": sn["MIDPOINT"], "outcome": y,
                         "residual": y - sn["MIDPOINT"]})
    return rows, excl


def stage_analyse(say, checkpoint, final):
    reg = load_registry()
    arm = P.PRIMARY_ARM
    rows, excl = eligible_rows(reg, arm, include_final_confirmation=final)
    say("PROTOCOL_SHA256 %s" % P.spec_hash())
    say("ARM %s   eligible settled markets = %d" % (arm, len(rows)))
    for k, v in sorted(excl.items(), key=lambda kv: -kv[1]):
        say("  excluded %-38s %d" % (k, v))
    target = P.CHECKPOINTS[checkpoint]
    if len(rows) < target and not final:
        say("CHECKPOINT %d needs N=%d, have %d -- NOT OPENED"
            % (checkpoint, target, len(rows)))
        return 0
    out = P.analyse(rows, checkpoint,
                    "P2_FINAL_CONFIRMATION" if final else
                    "EXPLORATORY@N=%d" % target)
    say(json.dumps(out, indent=1, default=str))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / ("p2_checkpoint_%d%s.json"
            % (checkpoint, "_final" if final else ""))).write_text(
        json.dumps(out, indent=1, default=str))
    # predeclared secondary arm, reported, never gating
    srows, _ = eligible_rows(reg, "T_MINUS_10", include_final_confirmation=final)
    if srows:
        s = P.analyse(srows, checkpoint, "SECONDARY_T_MINUS_10")
        say("SECONDARY T_MINUS_10: N=%d mean_residual=%s CI=%s (REPORTED, "
            "NEVER GATING)" % (s["N_MARKETS"],
                               s.get("MEAN_CALIBRATION_RESIDUAL"),
                               s.get("CI95_CLUSTERED_BY_EVENT")))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True,
                    choices=("discover", "snapshot", "settle", "analyse"))
    ap.add_argument("--checkpoint", type=int, default=0)
    ap.add_argument("--final-confirmation", action="store_true")
    a = ap.parse_args(argv)
    lines = []

    def say(m=""):
        lines.append(m)
        print(m)

    say("TRACK P2 %s -- protocol %s" % (a.stage.upper(), P.spec_hash()))
    if a.stage == "analyse":
        return stage_analyse(say, a.checkpoint, a.final_confirmation)
    try:
        return {"discover": stage_discover, "snapshot": stage_snapshot,
                "settle": stage_settle}[a.stage](say)
    except Exception as exc:                                # noqa: BLE001
        say("VENUE UNREACHABLE: %s: %s" % (type(exc).__name__, exc))
        say("gateway.polymarket.us is not reachable from this environment. "
            "Nothing was captured and nothing was written. This is an "
            "ACCESS fact, not an empty board.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

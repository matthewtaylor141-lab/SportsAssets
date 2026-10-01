"""Read-only PinnAPI inventory; run where the existing secret is installed.

No database writes, subscriptions, orders, key output, or extra dependencies.
This does not establish WebSocket entitlement or implement an odds adapter.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

BASE = "https://pinnapi.com"
MAX_BYTES = 8 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the authentication header to a redirected origin.
        return None


def read_json(opener, key: str, route: str):
    request = urllib.request.Request(
        BASE + route, headers={"x-portal-apikey": key, "Accept": "application/json"}
    )
    started = time.monotonic()
    try:
        with opener.open(request, timeout=15) as response:
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                return {"ok": False, "reason": "response_exceeds_probe_limit"}, None
            payload = json.loads(body)
            return {"ok": True, "http": response.status,
                    "request_elapsed_ms": round((time.monotonic()-started)*1000)}, payload
    except urllib.error.HTTPError as error:
        # Do not echo provider bodies, headers or exception strings.
        return {"ok": False, "http": error.code}, None
    except Exception as error:
        return {"ok": False, "reason": type(error).__name__}, None


def inventory(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        return {"schema_valid": False}
    events = payload["events"]
    families = Counter()
    phases = Counter()
    periods = 0
    with_state = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        phase = event.get("event_type")
        phases[phase if phase in ("live", "prematch") else "unknown"] += 1
        with_state += isinstance(event.get("state"), dict)
        tree = event.get("periods") or {}
        if not isinstance(tree, dict):
            continue
        for period in tree.values():
            if not isinstance(period, dict):
                continue
            periods += 1
            for family in ("money_line", "spreads", "totals", "team_total", "team_totals"):
                if period.get(family):
                    families[family] += 1
    return {"schema_valid": True, "event_count": len(events),
            "phase_counts": dict(phases), "period_count": periods,
            "events_with_state": with_state,
            "periods_containing_family": dict(families),
            "note": "Family counts are not tradable contracts or source-freshness proof."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-env", default="pinnapi_key",
                        help="Existing environment variable NAME, never the key value")
    parser.add_argument("--sport-id", type=int, required=True,
                        help="Sport ID from current PinnAPI documentation")
    args = parser.parse_args()
    key = os.environ.get(args.key_env)
    if not key:
        print(json.dumps({"ok": False, "reason": "key_not_present_in_selected_environment"}))
        return 2
    opener = urllib.request.build_opener(NoRedirect())
    report = {"probe": "PINNAPI_REST_READINESS_V1", "key_present": True,
              "sport_id": args.sport_id, "reads": [], "stream_entitlement": "NOT_TESTED"}
    routes = [("health", "/health")]
    routes += [(phase, "/kit/v1/markets?" + urllib.parse.urlencode(
        {"sport_id": args.sport_id, "event_type": phase})) for phase in ("live", "prematch")]
    for index, (purpose, route) in enumerate(routes):
        if index:
            time.sleep(1)  # Deliberately low-volume: at most three GETs.
        result, payload = read_json(opener, key, route)
        result["purpose"] = purpose
        if payload is not None and purpose != "health":
            result["inventory"] = inventory(payload)
        report["reads"].append(result)
        if not result["ok"]:
            break
    report["ok"] = len(report["reads"]) == 3 and all(
        r["ok"] and r.get("inventory", {}).get("schema_valid", True)
        for r in report["reads"])
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

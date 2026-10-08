"""POST THE RELEASE RECEIPT -- ONLY ON AN EXPLICIT "on" (PM review of RC4,
2026-10-08).

pm-acceptance.yml defaulted post_receipt to "on", so a dispatch that never
asked to post appended a receipt to the serving API anyway; the receipt for
08828d04 that reads green=false is one. The input now defaults to "off",
and this script is the ONLY code in the workflow that sends anything to the
API: it sends nothing unless the mode is exactly "on" (GitHub compares
`inputs.x == 'on'` case-insensitively; this does not).

A receipt is evidence, never authority. The API's receipt tables are
append-only (migration 315 triggers), and nothing here can edit or delete
a receipt. A new receipt SUPERSEDES earlier ones BY REFERENCE ONLY: its
`supersedes` names their receipt ids (the receipt the API currently reads
for its SHA, and any id the operator names), and they stay exactly where
they are.

    python3 -I .github/pm-acceptance/post_receipt.py acc
    env: POST_RECEIPT (the workflow input), ADMIN_TOKEN, BASE,
         SUPERSEDES_RECEIPT_ID (optional)

Writes acc/receipt_post.json (never the token). Stdlib only.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

MODE_ON = "on"
PATH = "/api/admin/red-team/release-receipt"
R_NOT_REQUESTED = "POST_NOT_REQUESTED"
R_NO_TOKEN = "NO_ADMIN_TOKEN"
R_NO_BASE = "NO_API_BASE"
R_NO_BODY = "NO_RECEIPT_BODY"
R_BAD_SUPERSEDES = "SUPERSEDES_NOT_A_RECEIPT_ID"
#: the API's receipt id shape: rel:<deployed sha[:12]>:<epoch seconds>
RECEIPT_ID = re.compile(r"^rel:[0-9a-f]{12}:[0-9]+$")


def should_post(mode) -> bool:
    """Exactly "on". Unset, blank, "off", "ON", "yes", "true": no POST."""
    return mode == MODE_ON


def supersedes(pm_before, named: str | None) -> tuple:
    """(receipt ids this receipt supersedes, refusals). The receipt the API
    currently reads for its SHA (pm_before.data.release_receipt) and the
    operator's named id; each must be a receipt id, or nothing is posted."""
    ids, bad = [], []
    cur = (((pm_before or {}).get("data") or {}).get("release_receipt")
           or {}).get("receipt_id") if isinstance(pm_before, dict) else None
    for rid in (cur, (named or "").strip() or None):
        if rid is None:
            continue
        if not isinstance(rid, str) or not RECEIPT_ID.match(rid):
            bad.append("%s:%s" % (R_BAD_SUPERSEDES, str(rid)[:40]))
        elif rid not in ids:
            ids.append(rid)
    return ids, bad


def post(body, *, mode, base: str, token: str, transport) -> dict:
    """The one POST, or the named reason there is none."""
    if not should_post(mode):
        return {"posted": False, "why": R_NOT_REQUESTED, "mode": mode}
    if not token:
        return {"posted": False, "why": R_NO_TOKEN}
    if not base:
        return {"posted": False, "why": R_NO_BASE}
    if not isinstance(body, dict) or not body:
        return {"posted": False, "why": R_NO_BODY}
    code, text = transport(
        "POST", base.rstrip("/") + PATH,
        {"X-Admin-Token": token, "Content-Type": "application/json"},
        json.dumps(body, sort_keys=True).encode())
    return {"posted": True, "http": code, "accepted": code == 200,
            "response": (text or "")[:1500]}


def urllib_transport(method, url, headers, data):
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, type(exc).__name__


def _load(p: pathlib.Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def main(argv, env=None, transport=urllib_transport) -> int:
    env = os.environ if env is None else env
    acc = pathlib.Path(argv[1] if len(argv) > 1 else "acc")
    mode = env.get("POST_RECEIPT")
    out = {"mode": mode}
    if not should_post(mode):
        out.update(posted=False, why=R_NOT_REQUESTED)
    else:
        body = _load(acc / "receipt_body.json")
        ids, bad = supersedes(_load(acc / "pm_before.json"),
                              env.get("SUPERSEDES_RECEIPT_ID"))
        if bad:
            out.update(posted=False, why=bad)
        else:
            if isinstance(body, dict):
                body = dict(body, supersedes=ids)
                (acc / "receipt_body.json").write_text(json.dumps(
                    body, indent=1, sort_keys=True))
            out.update(post(body, mode=mode, base=env.get("BASE", ""),
                            token=env.get("ADMIN_TOKEN", ""),
                            transport=transport), supersedes=ids)
    (acc / "receipt_post.json").write_text(json.dumps(out, indent=1))
    print("release receipt: %s" % json.dumps(out)[:1800])
    # a refused supersession is an operator error and must be loud
    return 1 if isinstance(out.get("why"), list) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

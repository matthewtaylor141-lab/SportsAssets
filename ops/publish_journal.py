"""Publish MERIDIAN's newest journal entry to the platform.

Run by the diagnostic workflow on every push: parses the top entry
block of ops/meridian-journal.md (a `mood:` line, then prose) and POSTs
it to /api/admin/meridian-journal. The server dedupes by content hash,
so re-running is free; without ADMIN_TOKEN this prints and exits."""

import json
import os
import re
import sys
import urllib.request


def main() -> int:
    try:
        raw = open("ops/meridian-journal.md", encoding="utf-8").read()
    except OSError as exc:
        print(f"  MJOURNAL error: {exc}")
        return 0
    entry = None
    for block in raw.split("\n---\n")[1:]:
        m = re.match(r"\s*mood:\s*(\w+)\s*\n+(.*)", block.strip(), re.S)
        if m:
            entry = {"mood": m.group(1), "entry": m.group(2).strip()}
            break
    token = os.environ.get("ADMIN_TOKEN", "")
    base = os.environ.get("BASE_URL", "").rstrip("/")
    if not entry:
        print("  MJOURNAL skipped: no entry block parsed")
        return 0
    if not token or not base:
        print("  MJOURNAL skipped: ADMIN_TOKEN/BASE_URL absent")
        return 0
    req = urllib.request.Request(
        f"{base}/api/admin/meridian-journal",
        data=json.dumps(entry).encode(),
        headers={"Content-Type": "application/json", "X-Admin-Token": token},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print("  MJOURNAL publish:", r.read().decode()[:160])
    except Exception as exc:  # noqa: BLE001 — diagnostics never fail the run
        print(f"  MJOURNAL error: {str(exc)[:160]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

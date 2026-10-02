"""Fetch the venue's OWN team records and logo images for given venue team ids.

Source: the venue's public gateway (no key), GET /v1/sports/teams/provider
with repeated teamIds -- the same records the venue attaches to market sides
(team.id / team.logo). Every image is stored with its provenance: requested
venue team id, the venue's record (id, name, abbreviation, league, safeName,
alias, providerIds), the logo URL, HTTP status/content-type, byte size,
sha256 and retrieval time. Nothing is matched by abbreviation here; the id is
the key. Bounded: <= 200 ids, <= 600 KB per image, images only.
"""
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

GATEWAY = "https://gateway.polymarket.us/v1/sports/teams/provider"
MAX_BYTES = 600_000
OK_TYPES = ("image/svg+xml", "image/png", "image/webp", "image/jpeg")
EXT = {"image/svg+xml": "svg", "image/png": "png", "image/webp": "webp",
       "image/jpeg": "jpg"}
UA = "BettorToken-team-logo-provenance/1 (+command.bettortoken.com)"


def get(url, accept="application/json"):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": accept})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read(MAX_BYTES + 1)
        return r.status, r.headers.get("Content-Type", ""), body


def main(ids, out):
    os.makedirs(out, exist_ok=True)
    ids = sorted({int(x) for x in ids})[:200]
    teams = {}
    for i in range(0, len(ids), 25):
        chunk = ids[i:i + 25]
        q = urllib.parse.urlencode([("teamIds", str(t)) for t in chunk])
        st, ct, body = get(GATEWAY + "?" + q)
        data = json.loads(body)
        got = data.get("teams") if isinstance(data, dict) else None
        if isinstance(got, dict):
            teams.update({str(k): v for k, v in got.items()})
        elif isinstance(got, list):
            teams.update({str(v.get("id")): v for v in got if isinstance(v, dict)})
        time.sleep(1.0)
    manifest = {"source": GATEWAY, "retrieved_at": time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "requested": ids, "teams": []}
    for tid in ids:
        rec = teams.get(str(tid))
        row = {"requested_id": tid, "venue_record": rec}
        logo = (rec or {}).get("logo") if isinstance(rec, dict) else None
        if not rec:
            row["status"] = "NO_VENUE_RECORD"
        elif not isinstance(rec.get("id"), int) or rec["id"] != tid:
            row["status"] = "VENUE_RECORD_ID_MISMATCH"
        elif not logo or not str(logo).startswith("https://"):
            row["status"] = "NO_LOGO_URL"
        else:
            try:
                st, ct, body = get(logo, accept="image/*")
                ct = ct.split(";")[0].strip().lower()
                if st != 200:
                    row["status"] = f"HTTP_{st}"
                elif ct not in OK_TYPES:
                    row["status"] = "NOT_AN_IMAGE:" + ct[:40]
                elif len(body) > MAX_BYTES or not body:
                    row["status"] = "SIZE_OUT_OF_BOUNDS"
                else:
                    name = f"venue-team-{tid}.{EXT[ct]}"
                    with open(os.path.join(out, name), "wb") as f:
                        f.write(body)
                    row.update(status="FETCHED", file=name, content_type=ct,
                               bytes=len(body),
                               sha256=hashlib.sha256(body).hexdigest(),
                               logo_url=logo)
            except Exception as exc:                            # noqa: BLE001
                row["status"] = "FETCH_ERROR:" + type(exc).__name__
            time.sleep(0.3)
        manifest["teams"].append(row)
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    by = {}
    for r in manifest["teams"]:
        by[r["status"]] = by.get(r["status"], 0) + 1
    print(json.dumps(by), len(teams), "records")


if __name__ == "__main__":
    main(sys.argv[2].replace(",", " ").split(), sys.argv[1])

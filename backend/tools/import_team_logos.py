"""Build assets/agents/team-logos from a `ping verify=team-logos` evidence dir.

Usage: python tools/import_team_logos.py <evidence dir> [--retrieved-by RUN]

For every FETCHED venue team record:
  * non-MLB: the venue's own logo image is downscaled to at most 128 px on its
    longer side (PNG, aspect kept) and stored as team-logos/venue-team-<id>.png
    with provenance (venue id, league, venue name, source URL, original and
    derived sha256, retrieval time);
  * MLB: the existing official mlbstatic SVG is reused. The venue id is bridged
    to the statsapi id ONLY by exact folded full-name equality with
    mlb-logo-sources.json ("Atlanta Braves" == "Atlanta Braves"); no
    abbreviation is consulted. No match -> the venue's own image is used.
Existing manifest entries for ids not in this evidence are kept, so coverage
only grows. Re-running on the same evidence is idempotent.
"""
import hashlib
import io
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / "sportsassets" / "assets" / "agents"
OUT = ROOT / "team-logos"
MAX_PX = 128


def fold(s):
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def main(ev_dir, retrieved_by=None):
    ev = Path(ev_dir)
    src = json.loads((ev / "manifest.json").read_text())
    mlb = json.loads((ROOT / "mlb-logo-sources.json").read_text())
    mlb_by_name = {fold(t["name"]): t for t in mlb["teams"]}
    OUT.mkdir(exist_ok=True)
    man_path = OUT / "manifest.json"
    old = json.loads(man_path.read_text()) if man_path.exists() else {}
    entries = {(e["league"], e["venue_team_id"]): e
               for e in old.get("teams", [])}
    for row in src["teams"]:
        if row.get("status") != "FETCHED":
            continue
        rec = row["venue_record"]
        tid, league = int(rec["id"]), str(rec["league"]).lower()
        base = {"venue_team_id": tid, "league": league,
                "venue_name": rec.get("name"),
                "venue_abbreviation": rec.get("abbreviation"),
                "retrieved_at": src["retrieved_at"],
                "retrieved_by": retrieved_by,
                "identity": "venue team id + league; venue name re-checked "
                            "at render"}
        bridge = mlb_by_name.get(fold(rec.get("name"))) if league == "mlb" \
            else None
        if bridge:
            f = ROOT / bridge["file"]
            entries[(league, tid)] = dict(
                base, file=bridge["file"],
                sha256=hashlib.sha256(f.read_bytes()).hexdigest(),
                source=bridge["source"],
                source_kind="official MLB CDN (existing asset)",
                bridge={"statsapi_id": bridge["id"],
                        "by": "exact full-name equality",
                        "statsapi_name": bridge["name"]})
            continue
        raw = (ev / row["file"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise SystemExit("sha256 mismatch for %s" % row["file"])
        im = Image.open(io.BytesIO(raw))
        im.load()
        im = im.convert("RGBA")
        im.thumbnail((MAX_PX, MAX_PX), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        data = buf.getvalue()
        name = "team-logos/venue-team-%d.png" % tid
        (ROOT / name).write_bytes(data)
        kind = ("venue national-team mark (country flag image)"
                if "/us-countries/" in row["logo_url"]
                else "venue team record logo")
        entries[(league, tid)] = dict(
            base, file=name, sha256=hashlib.sha256(data).hexdigest(),
            width=im.width, height=im.height,
            source=row["logo_url"], source_kind=kind,
            original_sha256=row["sha256"], original_bytes=row["bytes"])
    out = {"version": 1, "built_from": str(ev.name),
           "note": ("Team marks belong to their owners; shown only to identify "
                    "the teams in the account's own markets. Source: the "
                    "venue's public team records (side.team.logo) and, for "
                    "MLB, the official MLB CDN. No affiliation or endorsement "
                    "is implied."),
           "teams": sorted(entries.values(),
                           key=lambda e: (e["league"], e["venue_team_id"]))}
    man_path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    by = {}
    for e in out["teams"]:
        by[e["league"]] = by.get(e["league"], 0) + 1
    print(json.dumps(by))


if __name__ == "__main__":
    rb = sys.argv[sys.argv.index("--retrieved-by") + 1] \
        if "--retrieved-by" in sys.argv else None
    main(sys.argv[1], rb)

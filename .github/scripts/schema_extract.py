"""Extract ONE OpenAPI operation and every schema it transitively $refs.

Run by fetch-docs when the dispatched url carries `#op=<substring>`.
Replaces reading an arbitrary tail of a 1,275-line document and hoping
the relevant part is in it.
"""
import json, os, re, sys

frag = os.environ.get("FRAG") or ""
m = re.match(r"op=(.+)$", frag)
if not m:
    print("no op= fragment; nothing extracted")
    sys.exit(0)
want = m.group(1)

doc = json.load(open("/tmp/page.html", encoding="utf-8", errors="replace"))
paths = doc.get("paths") or {}
hits = {p: v for p, v in paths.items() if want in p}
if not hits:
    print("NO PATH MATCHED %r. available paths:" % want)
    for p in sorted(paths):
        print("   ", p)
    sys.exit(0)

seen, queue = set(), []


def walk(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                queue.append(v)
            else:
                walk(v)
    elif isinstance(node, list):
        for v in node:
            walk(v)


walk(hits)
resolved = {}
while queue:
    ref = queue.pop()
    if ref in seen:
        continue
    seen.add(ref)
    if not ref.startswith("#/"):
        resolved[ref] = "EXTERNAL REF NOT RESOLVED"
        continue
    cur = doc
    for part in ref[2:].split("/"):
        cur = (cur or {}).get(part.replace("~1", "/").replace("~0", "~"))
        if cur is None:
            break
    resolved[ref] = cur
    walk(cur)

os.makedirs("out", exist_ok=True)
payload = {"source": os.environ.get("URL_NOFRAG"), "matched_paths": hits,
           "resolved_refs": resolved}
with open("out/schema_extract.json", "w") as fh:
    json.dump(payload, fh, indent=2, sort_keys=True)
print("== MATCHED PATHS ==")
print(json.dumps(hits, indent=2, sort_keys=True))
print("== RESOLVED $refs (%d) ==" % len(resolved))
print(json.dumps(resolved, indent=2, sort_keys=True)[:40000])

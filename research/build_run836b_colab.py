#!/usr/bin/env python3
"""Build RUN836B_COLAB.ipynb -- a browser-only launcher for the frozen capture.

THE NOTEBOOK IS A LAUNCHER. IT IS NOT THE INSTRUMENT.

The frozen capture script is embedded verbatim as base64 and written to disk by
the notebook, which then verifies its SHA-256 against the frozen value before
anything runs. Nothing in the notebook edits, wraps, patches, monkeypatches or
re-implements that script: it is executed as a CHILD PROCESS with command-line
arguments, exactly as it would be from a shell, so its websocket protocol, REST
witness, subscribe payload, heartbeat, raw recording, session timing, reconnect
behaviour, checksums and completion criteria are untouched and unreachable from
the notebook's own code.

Running it as a child process is also the only correct way to run it under
Colab: the script calls asyncio.run(), and a Colab kernel already owns a
running event loop, so importing it into a cell would fail. A child process has
its own loop. That is why no change to the instrument is needed.

Discovery lives ONLY in the notebook, never in the instrument -- the
instrument's two-host allow-list still forbids the discovery host, which is
correct, because discovery output is not evidence. The notebook chooses token
ids, prints them, and passes them explicitly on the command line; the frozen
script records them in manifest.json, and that record is the scientific input.

Cell bodies below are written at column 0 and are NOT dedented. An earlier
version dedented them, and textwrap.dedent silently mangled the two cells that
embed blocks at a different indentation (the base64 blob and the preflight
heredoc) -- dedent removes the COMMON prefix, so one odd line changes every
other line. Authoring at column 0 removes the failure mode rather than working
around it.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "run836b_clob_capture.py"
OUT = HERE / "RUN836B_COLAB.ipynb"

FROZEN_SHA256 = "7931b54aef42f31b5f4118bf410340eddd71549125153ccbd9b33df29794760b"

# Fixed by the owner's authorization; constants, not runner-adjustable.
SESSIONS = 3
SESSION_SECONDS = 75
PAUSE_SECONDS = 5
REST_INTERVAL = 15
HEARTBEAT_INTERVAL = 10
TARGET_TOKENS = 4          # within the 3-5 specified
# custom_feature_enabled stays false by simply never passing the flag.


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {},
            "source": [ln + "\n" for ln in text.strip("\n").split("\n")]}


def code(body: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {},
            "outputs": [],
            "source": [ln + "\n" for ln in body.strip("\n").split("\n")]}


CELL_INSTALL = """
import subprocess, sys

print("installing...")
r = subprocess.run(
    [sys.executable, "-m", "pip", "install", "-q",
     "websockets>=14,<18", "httpx>=0.27"],
    capture_output=True, text=True)
print(r.stdout[-2000:] or "(no pip output)")
if r.returncode != 0:
    print(r.stderr[-3000:])
    raise SystemExit("STEP 1 FAILED: could not install websockets/httpx.")

import websockets, httpx
print("\\nwebsockets", websockets.__version__, "| httpx", httpx.__version__)
print("STEP 1 OK")
"""

CELL_PREFLIGHT = '''
import json, subprocess, sys, pathlib

# The check runs in a SEPARATE PROCESS on purpose. This notebook's kernel
# already owns an asyncio event loop, and the real capture also runs as its own
# process -- so this tests the same conditions the capture will actually meet.
pathlib.Path("preflight.py").write_text("""
import asyncio, json
import httpx, websockets

WS    = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
REST  = "https://clob.polymarket.com/book"
GAMMA = "https://gamma-api.polymarket.com/markets"

out = {"ws": None, "rest": None, "discovery": None}

async def check_ws():
    async with websockets.connect(WS, open_timeout=25, max_size=None) as ws:
        await ws.send(json.dumps(
            {"type": "market", "assets_ids": [], "custom_feature_enabled": False}))
        return "connected"

try:
    out["ws"] = asyncio.run(asyncio.wait_for(check_ws(), timeout=40))
except Exception as exc:
    out["ws"] = "FAILED: %s: %s" % (type(exc).__name__, exc)

try:
    with httpx.Client(timeout=25, follow_redirects=False) as c:
        # token_id=0 is not a real token. ANY http answer proves the host is
        # reachable, which is the only thing being asked here.
        out["rest"] = "HTTP %d" % c.get(REST, params={"token_id": "0"}).status_code
except Exception as exc:
    out["rest"] = "FAILED: %s: %s" % (type(exc).__name__, exc)

try:
    with httpx.Client(timeout=25, follow_redirects=True) as c:
        out["discovery"] = "HTTP %d" % c.get(GAMMA, params={"limit": 1}).status_code
except Exception as exc:
    out["discovery"] = "FAILED: %s: %s" % (type(exc).__name__, exc)

print(json.dumps(out))
""")

r = subprocess.run([sys.executable, "preflight.py"],
                   capture_output=True, text=True, timeout=240)
lines = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
if not lines:
    print(r.stdout[-2000:]); print(r.stderr[-2000:])
    raise SystemExit("STEP 3 FAILED: the reachability check did not report.")
res = json.loads(lines[-1])

print("public market feed :", res["ws"])
print("public price book  :", res["rest"])
print("market list (used only to choose markets):", res["discovery"])

ws_ok   = res["ws"] == "connected"
rest_ok = str(res["rest"]).startswith("HTTP")

if not (ws_ok and rest_ok):
    print("\\nCLOB_CAPTURE_RUNTIME_REACHABLE = NO")
    print("This machine cannot reach Polymarket's public endpoints.")
    print("Nothing is substituted and nothing else runs. Report this and stop.")
    raise SystemExit("CLOB_CAPTURE_RUNTIME_REACHABLE = NO")

print("\\nCLOB_CAPTURE_RUNTIME_REACHABLE = YES")
print("STEP 3 OK")
'''

# The selection rule is NOT written here. It lives in one file,
# research/run836b_select_tokens.py, which the GitHub Actions workflow runs
# directly and which is embedded verbatim below as the notebook's selection
# cell. Two copies would be two rules, and which one actually chose the markets
# would become a question instead of a fact.
SELECT = HERE / "run836b_select_tokens.py"
CELL_DISCOVER = SELECT.read_text(encoding="utf-8")

CELL_CAPTURE = '''
import subprocess, sys, time

cmd = [sys.executable, "run836b_clob_capture.py",
       "--sessions", "__SESSIONS__",
       "--session-seconds", "__SECS__",
       "--pause-seconds", "__PAUSE__",
       "--rest-interval", "__REST__",
       "--heartbeat-interval", "__HB__"]
for t in TOKEN_IDS:
    cmd += ["--token-id", t]
# custom_feature_enabled stays FALSE: the flag is simply never passed.

print("recording -- expect about 4 minutes of quiet, then a report.\\n")
started = time.time()
proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
CAPTURE_STDOUT = proc.stdout
print(CAPTURE_STDOUT)
if proc.stderr.strip():
    print("--- stderr ---")
    print(proc.stderr[-3000:])
print("\\n(elapsed " + str(round(time.time() - started)) + "s, exit code "
      + str(proc.returncode) + ")")
print("STEP 5 DONE -- read the result in step 6.")
'''
for _k, _v in (("__SESSIONS__", SESSIONS), ("__SECS__", SESSION_SECONDS),
               ("__PAUSE__", PAUSE_SECONDS), ("__REST__", REST_INTERVAL),
               ("__HB__", HEARTBEAT_INTERVAL)):
    CELL_CAPTURE = CELL_CAPTURE.replace(_k, str(_v))

CELL_DOWNLOAD = '''
import glob, hashlib, json, os, pathlib, tarfile

dirs = [d for d in sorted(glob.glob("run836b_capture_*")) if os.path.isdir(d)]
if not dirs:
    raise SystemExit("STEP 6 FAILED: no capture folder was produced.")
out = pathlib.Path(dirs[-1])
archive = pathlib.Path(out.name + ".tar.gz")

manifest = json.loads((out / "manifest.json").read_text())
verdict = manifest["CAPTURE_COMPLETE_FOR_RECONSTRUCTION"]

# A plain re-check that the files on disk still match the fingerprints the
# recorder wrote. This is a sanity check before you send the file. It is NOT
# the formal integrity gate, which is run on the file you send back.
bad = []
for line in (out / "checksums.sha256").read_text().splitlines():
    want, name = line.split("  ", 1)
    if hashlib.sha256((out / name).read_bytes()).hexdigest() != want:
        bad.append(name)

print("=" * 66)
print("CAPTURE_COMPLETE_FOR_RECONSTRUCTION = " + verdict)
print("=" * 66)
for r in manifest.get("incomplete_reasons", []):
    print("   missing:", r)
print("files re-checked on disk:", "ALL MATCH" if not bad else "MISMATCH " + str(bad))
if bad:
    print("Do not delete anything. Send the file anyway and report this line.")

if not archive.exists():
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(out, arcname=out.name)
archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()

print("\\n" + "=" * 66)
print("SEND THIS ONE FILE BACK:")
print("    " + archive.name)
print("    sha256 " + archive_sha)
print("=" * 66)
print("\\nAlso copy the whole output of step 5 into your reply.\\n")

try:
    from google.colab import files
    files.download(str(archive))
    print("The download should have started. If your browser blocked it, open")
    print("the folder icon in the left sidebar, find")
    print("'" + archive.name + "', and use its three-dot menu to download it.")
except Exception as exc:
    print("(automatic download unavailable: %s)" % type(exc).__name__)
    print("Open the folder icon in the left sidebar, find")
    print("'" + archive.name + "', and use its three-dot menu to download it.")
'''


def build_hash_cell(b64: str) -> str:
    chunks = [b64[i:i + 76] for i in range(0, len(b64), 76)]
    blob = "\n".join('    "%s"' % c for c in chunks)
    return (
        'import base64, hashlib, pathlib\n'
        '\n'
        'FROZEN_SHA256 = "%s"\n'
        '\n'
        '_B64 = "".join([\n'
        '%s\n'
        '])\n'
        '\n'
        'script = pathlib.Path("run836b_clob_capture.py")\n'
        'script.write_bytes(base64.b64decode(_B64))\n'
        '\n'
        'actual = hashlib.sha256(script.read_bytes()).hexdigest()\n'
        'print("expected fingerprint:", FROZEN_SHA256)\n'
        'print("actual   fingerprint:", actual)\n'
        '\n'
        'if actual != FROZEN_SHA256:\n'
        '    print("\\n" + "!" * 66)\n'
        '    print("FROZEN_INSTRUMENT_HASH_MISMATCH")\n'
        '    print("!" * 66)\n'
        '    print("The recording program does not match the approved version.")\n'
        '    print("Nothing will run. Report this line back and stop.")\n'
        '    raise SystemExit("FROZEN_INSTRUMENT_HASH_MISMATCH")\n'
        '\n'
        'print("\\nFROZEN_INSTRUMENT_HASH_VERIFIED")\n'
        'print("STEP 2 OK")\n'
    ) % (FROZEN_SHA256, blob)


def main() -> int:
    raw = SCRIPT.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != FROZEN_SHA256:
        raise SystemExit(
            "refusing to build: local script sha256 %s != frozen %s"
            % (actual, FROZEN_SHA256))

    b64 = base64.b64encode(raw).decode("ascii")

    cells = [
        md("""
# Run 83.6B — Polymarket public CLOB capture

**What you do:** `Runtime ▸ Run all`, wait about six minutes, then download the
one file it names at the end. Nothing else.

You will never need a terminal, and you will never need to edit any code.

---

**What this is.** It records what Polymarket's *public* market-data feed sends,
exactly as sent, so the protocol can be studied offline afterwards.

**What it is not.** No account, no API key, no password, no wallet, no order, no
money. It only listens. None of those are possible here: the recording program
contains no code that could place an order even if it were told to.

**About six minutes**, most of it three 75-second listening sessions with short
pauses between them. The pauses are deliberate — they are part of the experiment.

If a step fails, the notebook stops with a plain-English reason and the
remaining steps do not run. That is intended: nothing is left half-done.
"""),

        md("""
## Step 1 of 6 — Install the two libraries this needs

About 20 seconds. Ignore any pip warnings in the output.
"""),
        code(CELL_INSTALL),

        md("""
## Step 2 of 6 — Write out the sealed recording program and check its fingerprint

The program is carried inside this notebook, so nothing is downloaded from
anywhere. The notebook then checks its SHA-256 fingerprint against the approved
value. If a single byte differed, it stops.

This is what guarantees that the thing which runs is the approved instrument and
not something altered along the way.
"""),
        code(build_hash_cell(b64)),

        md("""
## Step 3 of 6 — Check this machine can actually reach Polymarket

A few seconds. It opens the public feed briefly, asks the public price book once,
and hangs up. If this machine is blocked, the notebook stops here rather than
producing a half-empty recording that would look like a quiet market.
"""),
        code(CELL_PREFLIGHT),

        md("""
## Step 4 of 6 — Pick the sports markets to listen to

You do not have to find anything. This asks Polymarket's public market list for
sports markets that are open and trading right now, and takes the busiest few
from **different** games.

**The rule is fixed in advance and written out in the code below**, and the
markets are chosen *before* any recording starts. That matters: choosing markets
after seeing what the feed did would let the choice bend the result.

The market list is used only to choose. It is not part of the evidence.
"""),
        code(CELL_DISCOVER),

        md("""
## Step 5 of 6 — Record

**This is the part that takes about four minutes. Leave the tab open.**

Three 75-second listening sessions with 5-second pauses, while separately asking
the public price book every 15 seconds as an independent check.

The output stays quiet for a while and then prints a report at the end. That is
normal — the program writes to files as it goes and reports once, at the finish.
"""),
        code(CELL_CAPTURE),

        md("""
## Step 6 of 6 — Get your file

This packages the recording and downloads it to your computer.

**Then send that one file back.** It is the whole result. Nothing else needs to
be copied and no other file matters.
"""),
        code(CELL_DOWNLOAD),

        md("""
---

### If something went wrong

Whatever the notebook printed is the answer — copy it back as-is. A stop is a
real result, not a failure to hide or retry around. In particular:

- **FROZEN_INSTRUMENT_HASH_MISMATCH** — the program was altered. Report it.
- **CLOB_CAPTURE_RUNTIME_REACHABLE = NO** — this machine is blocked from
  Polymarket. Report it.
- **CAPTURE_COMPLETE_FOR_RECONSTRUCTION = NO** — the recording ran but one of the
  evidence streams is missing. Send the file anyway; the record of an incomplete
  run is itself worth keeping.

Do not re-run to get a different answer. If a second capture is needed it gets
its own label, so the two can never be confused.
"""),
    ]

    nb = {
        "nbformat": 4, "nbformat_minor": 0,
        "metadata": {
            "colab": {"provenance": [], "toc_visible": True},
            "kernelspec": {"name": "python3", "display_name": "Python 3"},
            "language_info": {"name": "python"},
        },
        "cells": cells,
    }
    OUT.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
    print("wrote %s (%s bytes)" % (OUT, format(OUT.stat().st_size, ",")))
    print("embedded script sha256 %s" % actual)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

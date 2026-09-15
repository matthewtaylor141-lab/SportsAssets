"""The BETA48 return channel, made verifiable at the receiving end.

WHY THIS FILE EXISTS
--------------------
The research container has no egress. Every measurement comes home as a
gzip+base64 line in a GitHub Actions log, transcribed by hand. That is a
lossy channel and it lost data silently:

  ferrarichampions2026, run 35034361586 -> the captured line decoded as
  valid base64 (11,613 bytes), carried an INTACT gzip header (filename
  "ferrarichampions2026_reconstruction.json") and an INTACT gzip trailer
  (crc32=d4fdb3b5, isize=72227), and still died 24,595 bytes into
  decompression with "invalid code lengths set". A slab out of the MIDDLE
  of the line was gone. Nothing at either end of the payload showed it.

Two lessons are encoded here.

1. A header/trailer check is NOT an integrity check. The bytes that
   frame a payload can survive the loss of the payload.
2. An all-or-nothing hash tells you THAT something broke, never WHERE.
   At 15,484 base64 characters "re-transcribe the whole thing" is the
   same coin-flip that just failed.

So the emitter cuts the base64 into indexed chunks, each with its own
SHA256, under a header line carrying the whole-payload hashes. This file
reassembles them and refuses anything it cannot prove:

  * every chunk index 0..n-1 present exactly once   -> else MISSING/DUPE
  * every chunk's own SHA256 matches                -> else names the index
  * the concatenation's SHA256 matches the header
  * the gzip's SHA256 matches the header
  * the decompressed JSON's SHA256 matches the header

A failure names the chunk to re-transcribe, so a 15 KB payload is
repaired by re-reading 2,000 characters instead of all of it.

NOTHING HERE INTERPRETS THE PAYLOAD. This module only decides whether the
bytes that arrived are the bytes that left. An account whose blob does
not verify does not enter the cross-account table at all.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
from dataclasses import dataclass, field

HEADER_RE = re.compile(
    r"^BETA48H_(?P<w>\S+)\s+"
    r"json_sha256=(?P<json_sha>[0-9a-f]{64})\s+"
    r"json_bytes=(?P<json_bytes>\d+)\s+"
    r"gz_sha256=(?P<gz_sha>[0-9a-f]{64})\s+"
    r"gz_bytes=(?P<gz_bytes>\d+)\s+"
    r"b64_chars=(?P<b64_chars>\d+)\s+"
    r"chunks=(?P<chunks>\d+)\s+"
    r"chunk_chars=(?P<chunk_chars>\d+)\s*$"
)
CHUNK_RE = re.compile(
    r"^BETA48C_(?P<w>\S+)\s+(?P<i>\d+)/(?P<n>\d+)\s+"
    r"(?P<sha16>[0-9a-f]{16})\s+(?P<data>[A-Za-z0-9+/=]+)\s*$"
)

# The Actions log prefixes every line with an ISO timestamp. Strip it if
# present so a line pasted straight out of the log still parses.
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[0-9:.]+Z\s+")


class ChannelError(Exception):
    """The bytes that arrived are not provably the bytes that left."""


@dataclass
class Verified:
    wallet: str
    payload: dict
    json_sha256: str
    gz_sha256: str
    chunks: int
    chunk_chars: int
    notes: list = field(default_factory=list)


def _strip(line: str) -> str:
    return TS_RE.sub("", line.rstrip("\n"))


def parse(text: str, wallet: str | None = None) -> Verified:
    """Reassemble one wallet's blob from raw log text and PROVE it.

    Raises ChannelError naming the exact defect. Never returns a payload
    it could not verify end to end.
    """
    headers, chunks = {}, {}
    for line in text.splitlines():
        line = _strip(line)
        m = HEADER_RE.match(line)
        if m:
            headers[m.group("w")] = m.groupdict()
            continue
        m = CHUNK_RE.match(line)
        if m:
            w = m.group("w")
            i = int(m.group("i"))
            prev = chunks.setdefault(w, {}).get(i)
            if prev is not None and prev[1] != m.group("data"):
                raise ChannelError(
                    f"{w}: chunk {i} appears twice with DIFFERENT content"
                )
            chunks.setdefault(w, {})[i] = (m.group("sha16"), m.group("data"))

    if not headers:
        raise ChannelError("no BETA48H_ header line found")
    if wallet is None:
        if len(headers) != 1:
            raise ChannelError(
                f"text carries {len(headers)} wallets "
                f"({sorted(headers)}); name the one you want"
            )
        wallet = next(iter(headers))
    W = wallet.upper()
    if W not in headers:
        raise ChannelError(f"{W}: no header line (found {sorted(headers)})")

    h = headers[W]
    got = chunks.get(W, {})
    n = int(h["chunks"])

    # 1. Completeness, BY INDEX. This is the check the flat channel could
    #    not make: name what is missing instead of decompressing and
    #    hoping.
    missing = [i for i in range(n) if i not in got]
    if missing:
        raise ChannelError(
            f"{W}: {len(missing)} of {n} chunks MISSING: indices {missing} "
            f"(re-read just those lines; each is "
            f"{h['chunk_chars']} characters)"
        )
    extra = [i for i in got if i >= n]
    if extra:
        raise ChannelError(f"{W}: chunk indices beyond the declared {n}: {extra}")

    # 2. Per-chunk integrity, so a mangled chunk is LOCATED, not just
    #    detected.
    bad = [
        i
        for i in range(n)
        if hashlib.sha256(got[i][1].encode()).hexdigest()[:16] != got[i][0]
    ]
    if bad:
        raise ChannelError(
            f"{W}: {len(bad)} chunk(s) FAILED their own SHA256: indices {bad} "
            f"(re-read just those lines)"
        )

    b64 = "".join(got[i][1] for i in range(n))
    if len(b64) != int(h["b64_chars"]):
        raise ChannelError(
            f"{W}: reassembled {len(b64)} base64 chars, "
            f"header declares {h['b64_chars']}"
        )

    # 3. Whole-payload identity at every layer. Each is a different
    #    failure mode: base64 transcription, gzip transport, and the JSON
    #    the estimator actually wrote.
    try:
        gz = base64.b64decode(b64, validate=True)
    except Exception as exc:  # pragma: no cover - defensive
        raise ChannelError(f"{W}: base64 decode failed: {exc}") from exc
    if len(gz) != int(h["gz_bytes"]):
        raise ChannelError(
            f"{W}: gzip is {len(gz)} bytes, header declares {h['gz_bytes']}"
        )
    gz_sha = hashlib.sha256(gz).hexdigest()
    if gz_sha != h["gz_sha"]:
        raise ChannelError(
            f"{W}: gzip SHA256 {gz_sha} != declared {h['gz_sha']}"
        )
    raw = gzip.decompress(gz)
    if len(raw) != int(h["json_bytes"]):
        raise ChannelError(
            f"{W}: JSON is {len(raw)} bytes, header declares {h['json_bytes']}"
        )
    json_sha = hashlib.sha256(raw).hexdigest()
    if json_sha != h["json_sha"]:
        raise ChannelError(
            f"{W}: JSON SHA256 {json_sha} != declared {h['json_sha']}"
        )

    return Verified(
        wallet=W.lower(),
        payload=json.loads(raw),
        json_sha256=json_sha,
        gz_sha256=gz_sha,
        chunks=n,
        chunk_chars=int(h["chunk_chars"]),
    )

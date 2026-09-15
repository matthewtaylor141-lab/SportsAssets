"""The channel's tests, written against the failure that actually happened.

The controlling test is `test_the_ferrari_failure_is_caught_and_located`:
it reproduces the EXACT shape of the ferrarichampions2026 loss — a slab
gone from the middle of the payload, both ends intact — and asserts the
new channel names the missing indices instead of dying in zlib.

Run: python -m pytest research/beta48/test_chunked_channel.py -q
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import zlib

import pytest

from chunked_channel import ChannelError, parse

CHUNK = 2000


def emit(wallet: str, payload: dict, chunk_chars: int = CHUNK) -> str:
    """The emitter, mirrored from .github/workflows/beta48-reemit.yml.

    Kept in the test rather than imported so a drift between the runner's
    emitter and this reader shows up as a failing test, not as silence.
    """
    raw = json.dumps(payload, indent=1).encode()
    gz = gzip.compress(raw, 9, mtime=0)
    b64 = base64.b64encode(gz).decode()
    W = wallet.upper()
    chunks = [b64[i:i + chunk_chars] for i in range(0, len(b64), chunk_chars)]
    lines = [
        f"BETA48H_{W} json_sha256={hashlib.sha256(raw).hexdigest()} "
        f"json_bytes={len(raw)} gz_sha256={hashlib.sha256(gz).hexdigest()} "
        f"gz_bytes={len(gz)} b64_chars={len(b64)} chunks={len(chunks)} "
        f"chunk_chars={chunk_chars}"
    ]
    for i, c in enumerate(chunks):
        lines.append(
            f"BETA48C_{W} {i}/{len(chunks)} "
            f"{hashlib.sha256(c.encode()).hexdigest()[:16]} {c}"
        )
    lines.append(f"BETA48E_{W} end chunks={len(chunks)}")
    return "\n".join(lines)


def big_payload(n: int = 400) -> dict:
    """Large enough to span many chunks, shaped like a real blob."""
    return {
        "wallet": "ferrarichampions2026",
        "lifetime": {"edge_roi": 0.02084, "edge_lots": 78500},
        "completion_grid": {
            f"Market {i} vs. Opponent {i}": {
                "opens": 500 + i,
                "completed_within_1h": 400 + i,
                "completion_rate_1h": round(0.5 + (i % 400) / 1000, 4),
                "mean_pair_basis": round(0.96 + (i % 30) / 1000, 5),
            }
            for i in range(n)
        },
    }


def test_a_clean_round_trip_verifies():
    p = big_payload()
    v = parse(emit("ferrarichampions2026", p))
    assert v.payload == p
    assert v.wallet == "ferrarichampions2026"
    assert v.chunks > 1, "payload must span several chunks or this proves nothing"


def test_the_ferrari_failure_is_caught_and_located():
    """A slab gone from the MIDDLE, both ends intact.

    This is byte-for-byte the shape that beat the flat channel: valid
    base64, intact gzip header, intact gzip trailer, dead in the middle.
    The flat channel surfaced it only as zlib's "invalid code lengths
    set" at an unknown offset. The chunked channel must name the indices.
    """
    p = big_payload()
    lines = emit("ferrarichampions2026", p).splitlines()
    chunk_lines = [ln for ln in lines if ln.startswith("BETA48C_")]
    assert len(chunk_lines) >= 5, "need enough chunks to cut a middle out"

    lost = {2, 3}
    kept = [
        ln
        for ln in lines
        if not (
            ln.startswith("BETA48C_")
            and int(ln.split()[1].split("/")[0]) in lost
        )
    ]
    with pytest.raises(ChannelError) as exc:
        parse("\n".join(kept))
    msg = str(exc.value)
    assert "MISSING" in msg
    assert "[2, 3]" in msg, msg


def test_the_flat_channel_would_NOT_have_caught_it():
    """Pins the defect this design replaces — not just the new behaviour.

    Drop the same middle slab from a FLAT base64 line and show what the
    old channel saw: base64 still decodes, the gzip header still names
    the file, the trailer still carries a plausible crc32/isize, and the
    only signal is a zlib error at an offset nobody can act on.
    """
    raw = json.dumps(big_payload()).encode()
    gz = gzip.compress(raw, 9, mtime=0)
    b64 = base64.b64encode(gz).decode()
    # Cut a 4-character-aligned slab from the middle: alignment preserved,
    # so the header and trailer both land where a reader expects them.
    cut_at = (len(b64) // 2) // 4 * 4
    mangled = base64.b64decode(b64[:cut_at] + b64[cut_at + 400:])

    assert mangled[:3] == b"\x1f\x8b\x08", "gzip magic survives the loss"
    assert len(mangled) < len(gz), "bytes really are gone"
    with pytest.raises(zlib.error):
        zlib.decompressobj(31).decompress(mangled)


def test_a_mangled_chunk_is_located_by_index():
    p = big_payload()
    lines = emit("ferrarichampions2026", p).splitlines()
    for j, ln in enumerate(lines):
        if ln.startswith("BETA48C_") and ln.split()[1].startswith("4/"):
            parts = ln.split()
            # flip one character inside the payload, leaving the hash alone
            data = parts[3]
            parts[3] = ("A" if data[7] != "A" else "B").join(
                [data[:7], data[8:]]
            )
            lines[j] = " ".join(parts)
            break
    else:  # pragma: no cover
        pytest.fail("no chunk 4 to mangle")
    with pytest.raises(ChannelError) as exc:
        parse("\n".join(lines))
    assert "FAILED their own SHA256" in str(exc.value)
    assert "[4]" in str(exc.value)


def test_a_truncated_tail_is_caught():
    """The ordinary 'my tail_lines was too small' failure."""
    lines = emit("ferrarichampions2026", big_payload()).splitlines()
    with pytest.raises(ChannelError) as exc:
        parse("\n".join(lines[:-3]))
    assert "MISSING" in str(exc.value)


def test_actions_timestamp_prefixes_are_tolerated():
    """Lines pasted straight out of the Actions log must parse."""
    lines = emit("ferrarichampions2026", big_payload()).splitlines()
    stamped = "\n".join(f"2026-09-15T23:17:33.859{i%10}367Z {ln}"
                        for i, ln in enumerate(lines))
    assert parse(stamped).payload["wallet"] == "ferrarichampions2026"


def test_two_wallets_in_one_text_must_be_named():
    a = emit("rn1", {"wallet": "rn1", "x": list(range(500))})
    b = emit("kch123", {"wallet": "kch123", "x": list(range(500))})
    both = a + "\n" + b
    with pytest.raises(ChannelError) as exc:
        parse(both)
    assert "name the one you want" in str(exc.value)
    assert parse(both, wallet="rn1").payload["wallet"] == "rn1"
    assert parse(both, wallet="kch123").payload["wallet"] == "kch123"


def test_a_duplicated_chunk_with_different_content_is_refused():
    lines = emit("rn1", big_payload()).splitlines()
    dupe = next(ln for ln in lines if ln.startswith("BETA48C_rn1".upper())
                and ln.split()[1].startswith("1/"))
    parts = dupe.split()
    parts[3] = parts[3][:-1] + ("A" if parts[3][-1] != "A" else "B")
    with pytest.raises(ChannelError) as exc:
        parse("\n".join(lines + [" ".join(parts)]))
    assert "twice with DIFFERENT content" in str(exc.value)


def test_a_header_that_lies_about_the_json_hash_is_refused():
    """The last line of defence: chunks all verify, payload still wrong."""
    lines = emit("rn1", big_payload()).splitlines()
    h = lines[0].split()
    h[1] = "json_sha256=" + "0" * 64
    lines[0] = " ".join(h)
    with pytest.raises(ChannelError) as exc:
        parse("\n".join(lines))
    assert "JSON SHA256" in str(exc.value)

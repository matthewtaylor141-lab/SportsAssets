"""The archive snapshot must not triple itself to get compressed.

The API has been OOMing on a 2 GB line all week. The candidate I
shipped first was a type filter on the hydrate query, and I described
it as the fix. Measured on a warm process afterwards:

    archive_rows  317,681 -> 300,000   (~6%)
    rss_mb        739.9 at 25 min uptime, against a 1,032-1,762 baseline

Ambiguous at best, and nowhere near the size of the problem. The
packer was the thing worth looking at:

    base64(gzip.compress(json.dumps(rows).encode()))

At 300k slim rows that materialises the WHOLE archive three times over
before compression starts — the dumps() str, the encode() bytes, and
the compressor's input — and it fires on every 20-chunk checkpoint
during a grind, plus every rolling refresh.

Streaming into a GzipFile holds one row at a time. These tests pin the
property (bounded intermediates), not the byte output, and pin that
the format did not change — an incompatible packer would silently
retire every snapshot and send every boot back to a full grind.
"""

import gzip
import json

from sportsassets.api import track_record as tr


class TestRoundTrip:
    ROWS = [{"id": i, "t": "ACTIVITY_TYPE_TRADE", "s": "x" * 40,
             "p": i / 7.0} for i in range(2000)]

    def test_pack_unpack_is_identity(self):
        assert tr._unpack_rows(tr._pack_rows(self.ROWS)) == self.ROWS

    def test_empty_survives(self):
        assert tr._unpack_rows(tr._pack_rows([])) == []

    def test_one_row_survives(self):
        assert tr._unpack_rows(tr._pack_rows([{"a": 1}])) == [{"a": 1}]

    def test_non_json_values_still_pack(self):
        """default=str was load-bearing — datetimes reach this."""
        import datetime as dt

        row = [{"at": dt.datetime(2026, 8, 25, 5, 0, 0)}]
        assert tr._unpack_rows(tr._pack_rows(row)) == [
            {"at": "2026-08-25 05:00:00"}]


class TestTheFormatDidNotChange:
    """A snapshot written by the old packer must still load, and one
    written by the new packer must still be a plain JSON array. If the
    format drifted, every stored snapshot silently becomes unreadable
    and every boot falls back to the full grind — the exact failure
    that made the archive unreliable in the first place."""

    ROWS = [{"id": i, "v": i * 2} for i in range(500)]

    def _old_pack(self, rows):
        import base64
        return base64.b64encode(
            gzip.compress(json.dumps(rows, default=str).encode(), 5)
        ).decode()

    def test_the_new_reader_loads_an_old_snapshot(self):
        assert tr._unpack_rows(self._old_pack(self.ROWS)) == self.ROWS

    def test_the_new_writer_emits_a_json_array(self):
        import base64

        raw = gzip.decompress(base64.b64decode(tr._pack_rows(self.ROWS)))
        assert raw.startswith(b"[") and raw.endswith(b"]")
        assert json.loads(raw) == self.ROWS


class TestIntermediatesAreBounded:
    def test_the_packer_never_dumps_the_whole_list(self):
        """The property that makes this a memory fix: json.dumps is
        called per ROW, never on `rows`."""
        import inspect

        src = inspect.getsource(tr._pack_rows)
        assert "json.dumps(rows" not in src
        assert "gzip.compress(" not in src
        assert "GzipFile" in src

    def test_the_unpacker_releases_as_it_goes(self):
        import inspect

        src = inspect.getsource(tr._unpack_rows)
        assert "del comp" in src and "del plain" in src

    def test_a_large_archive_packs_without_a_giant_string(self, monkeypatch):
        """Behavioural, not source-shaped: if the packer ever goes back
        to dumping the list, this records the call."""
        big = [{"id": i, "s": "y" * 60} for i in range(20000)]
        seen = {"max_len": 0, "calls": 0}
        real = json.dumps

        def spy(obj, **kw):
            out = real(obj, **kw)
            seen["calls"] += 1
            seen["max_len"] = max(seen["max_len"], len(out))
            return out

        monkeypatch.setattr(tr.json, "dumps", spy)
        tr._pack_rows(big)
        assert seen["calls"] >= len(big), "one dumps per row expected"
        assert seen["max_len"] < 500, (
            f"largest single json.dumps was {seen['max_len']} bytes — "
            f"something is still serialising in bulk")

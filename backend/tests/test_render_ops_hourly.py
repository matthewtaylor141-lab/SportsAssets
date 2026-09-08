"""The `hourly` render-ops preset (2026-09-08): the read-only presets
the hourly status reads -- five at first, eight since the PNL program's
lane M added paired-ratio, fills-missed and on-target-why, nine since
FILL lane 0b added take-band beside fills-missed (the rest-vs-take read
rides the first hourly after the deploy) -- joined into
ONE job under '== name' section markers, so the hour's numbers come from
one log. The pins: the joined text equals the presets' own SQL (an edit
to one of them that forgets the hourly line fails here), the markers sit
in order, the preset is read-only, its output cap is its own, the help
line is the case labels regenerated.
"""
from __future__ import annotations

import re
from pathlib import Path

YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
PARTS = ("mirror-tick", "mirror-pnl", "paired-day", "paired-ratio", "latency-census", "fills-answered",
         "fills-missed", "take-band", "on-target-why")


def _sql(text: str, name: str) -> str:
    m = re.search(r'^ {16}' + re.escape(name) + r'\) SQL="(.*?)"; TO=(\d+)', text, re.M | re.S)
    assert m, name
    return m.group(1)


def test_the_hourly_preset_is_the_nine_presets_sql_joined_under_section_markers():
    text = YML.read_text()
    hourly = _sql(text, "hourly")
    expected = " ".join(
        "SELECT '== %s' AS section; %s" % (n, _sql(text, n).rstrip().rstrip(";") + ";") for n in PARTS
    )
    assert hourly == expected, "the hourly line is the nine presets' SQL joined; regenerate it"
    markers = re.findall(r"SELECT '== ([a-z-]+)' AS section;", hourly)
    assert tuple(markers) == PARTS
    # lane M: the paired ratio's one line, the mirror's own filled-vs-missed and the on_target causes
    assert "AS line FROM (SELECT count(*) AS markets" in hourly and "first_verdict_after_his_last" not in hourly
    assert "'missed_expired_ioc'" in hourly and "'stale_snapshot'" in hourly
    # FILL lane 0b: take-band's bucket table sits after fills-missed; the exit and close reads stay out
    assert markers.index("take-band") == markers.index("fills-missed") + 1
    assert "GROUP BY ROLLUP (decision, bucket)" in hourly and "'== exits-band'" not in hourly
    assert "'== closed-while-he-traded'" not in hourly and "$PS" not in hourly


def test_the_hourly_preset_is_read_only_with_its_own_output_cap_and_timeout():
    text = YML.read_text()
    block = text[text.index("hourly) SQL="):text.index('*) echo "sql: arg must be one of')]
    assert "need_confirm" not in block and "$ARG" not in block
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE"):
        assert bad not in block, bad
    assert '"; TO=120000; HEAD=1500 ;;' in block
    assert 'head -"${HEAD:-300}"' in text, "the runner's cap reads HEAD, default 300"


def test_the_help_line_is_the_case_labels_with_hourly_last():
    text = YML.read_text()
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    names = line.split("one of ", 1)[1].split(" (got", 1)[0].split("|")
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", text[text.index('case "$ARG" in'):text.index(line)], re.M)
    assert names == labels
    assert names[-1] == "hourly", "the last case label, after every preset it joins"

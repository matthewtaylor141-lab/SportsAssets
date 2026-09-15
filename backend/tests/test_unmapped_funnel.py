"""The winnable split (owner question 2026-08-13: 'how do we fix the
unmapping error to significantly increase copies').

'38,252 unmapped' blends two opposite facts: markets the venue LISTED
and our mapper failed on (our bug, winnable by code) and markets the
venue does not carry (unwinnable by any code). The wrong 'venue does
not list Asian soccer' call earlier that same day happened precisely
because nothing counted the split. Source-asserted: app.py pulls in
fastapi, absent from this environment.
"""

import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "sportsassets" / "api" / "app.py").read_text()
FUNNEL = SRC[SRC.index("api_copy_unmapped"):SRC.index("api_track_record")]


def test_winnable_split_reads_the_mapper_diag():
    """'sides:[' in the diag = the venue handed us its markets (ours to
    fix); '0ev' with no sides = search found nothing (venue gap)."""
    assert "LIKE '%sides:[%'" in FUNNEL
    assert "LIKE '%0ev%'" in FUNNEL
    assert "NOT LIKE '%sides:[%'" in FUNNEL, \
        "a diag with BOTH markers is listed, never double-counted"


def test_undiagnosed_is_its_own_bucket():
    assert "undiagnosed" in FUNNEL, \
        "rows with neither marker are counted, never guessed into a side"


def test_guard_refusals_no_longer_masquerade_as_mapping():
    """'never-add' and 'one position per game' are guards WORKING —
    counting them as no_us_market inflated the mapping problem."""
    assert "'never_add'" in FUNNEL
    assert "'one_per_game'" in FUNNEL


def test_no_slug_reports_enrichability():
    """A no-slug rejection whose token the catalog now knows will map
    on the hourly retry; one nothing knows is the enrichment gap."""
    assert "catalog_has_token" in FUNNEL
    assert "token_unknown" in FUNNEL


def test_winnable_only_counts_unmapped_reason():
    """never-add/no-stack rows also carry no diag markers — the split
    must be computed over reason='unmapped' rows only."""
    py = FUNNEL[FUNNEL.index("winnable = {"):]
    assert 'if r["reason"] == "unmapped":' in py

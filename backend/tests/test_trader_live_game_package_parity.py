"""LIVE GAME STATE V1 -- THE INSTALLED CODE IS THE PACKAGE, PLUS TWO
DISCLOSED LINES.

BETTOR_Live_Game_State_V1.zip (sha256 7386415974...b2634, MANIFEST.json
sha256 a2196b29a9...943505, 33/33 entries verified) was installed with its
own guarded installer (apply_live_game_state.py --component backend
--apply): 12 package files copied, migration 316 allocated from its
schema.sql, two hook lines inserted into api/trader_readmodel.py at the
package's exact anchors (the readmodel blob was the package's inspected
baseline a8e0af0c).

Two package lines then had to change, because this repository's own guards
reject them as shipped. Each is reversed here mechanically and the result
must hash to the package MANIFEST, so nothing else can have drifted:

  storage.py  PostgresStore.heartbeat's json.dumps gains default=str --
              tests/test_r30a_runtime_defects.py requires every heartbeat
              writer to carry a default= (a bare dumps once raised on the
              first datetime in a heartbeat, every tick)
  workers/trader_live_scores.py  the missing-DSN error string
              'TRADER_SCORE_DATABASE_URL_OR_DATABASE_URL_REQUIRED' becomes
              'TRADER_SCORE_DATABASE_URL OR DATABASE_URL REQUIRED' --
              tests/test_the_read_only_interface_cannot_mutate.py reads
              every *DATABASE_URL* token in the source as a DSN setting the
              authority boundary must state; the joined token is not one

Because of these two lines the package's verify_live_game_state.py --repo
reports both files DIFFERENT, and a re-run of its installer refuses them
('new file already exists with different contents'): that is the disclosed
compatibility diff, not drift. The package's 118 component tests run
unchanged in this suite (tests/test_live_game_state_*.py); their count is
pinned so none can be dropped.

A FIX TO THE PACKAGE IS A DISCLOSED LANE PATCH (LANE_PATCHES): a unified
diff kept under tests/fixtures/, pinned by sha256, touching only the files it
names, reversed hunk by hunk before the two lines above -- so the package
MANIFEST hash still proves that nothing else moved. RC6 lane G2 (the
canonical fixture adapter, requirement register F3) is the first.
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import re

BACKEND = pathlib.Path(__file__).resolve().parents[1]

#: package MANIFEST.json sha256, by installed path
PACKAGE_SHA256 = {
    "sportsassets/live_game_state/__init__.py":
        "e6468422b70e2af15cb65c5fbbb70247b698a451d2bbb693f6b6488e3299eca5",
    "sportsassets/live_game_state/collector.py":
        "7be1c4245c691b2eadc0856e38d2a8f43b5e98c055e25f9b488dee253de8f6fe",
    "sportsassets/live_game_state/core.py":
        "b259c4758afd5060f736ed4a51e98e9578e90a540ecfca3577c12713efc8d212",
    "sportsassets/live_game_state/integration.py":
        "1e3d82e33138d69bebf403b3e2d2d46cc10483f90463d9be7a04784ef977840d",
    "sportsassets/live_game_state/providers.py":
        "da67b75fca64d1b7d92155614469d402e395091a87919b35d73b895a7cb4db22",
    "sportsassets/live_game_state/storage.py":
        "7bb53e9b7f47c4e9c1ecc40cd3db180e7d14a273e72aa096ac185b1629ca7d39",
    "sportsassets/workers/trader_live_scores.py":
        "b267cf185bdc735d8e6118401b906e901435982b894c2774436233365c254742",
    "tests/_bettor_live_game_state_test_helpers.py":
        "aed21fd8878a8886fd03730580095f9ae3698da09c9bd3380cce2117d278a91c",
    "tests/test_live_game_state_collector.py":
        "c6b21edbd34832aedac800f49a197520b702d0b1e32a6bdc194fcc486cbf5b54",
    "tests/test_live_game_state_core.py":
        "d103f87e4ffacce46de755c9dd3c107ae94bc70ac0e63a28f589a8b44de55698",
    "tests/test_live_game_state_integration.py":
        "b983ea8f7038be4d46d1b53bb55a7fb39bd59b14b5cc725704642687cc52b913",
    "tests/test_live_game_state_providers.py":
        "390440435bd8150b41cc59e88044010b81f3cfe09a2d2746494a95340d58fab4",
    # the package's schema.sql, allocated number 316 by its installer
    "migrations/316_trader_live_game_display.sql":
        "936d8a2d6b4f4c2ebb0ea00dc804b6bf2e63e364c03ea4ff5a76b18d0b4bd488",
}

#: (installed text, package text): the ONLY permitted differences
COMPAT_DIFFS = {
    "sportsassets/live_game_state/storage.py": [(
        "        # default=str: the repository's heartbeat rule (test_r30a_runtime_defects).\n"
        "        body = json.dumps(payload, sort_keys=True, allow_nan=False, default=str)\n",
        "        body = json.dumps(payload, sort_keys=True, allow_nan=False)\n")],
    "sportsassets/workers/trader_live_scores.py": [(
        "        # Two setting names, not one token (bettor_read_only_venue dsn_settings).\n"
        "        raise RuntimeError(\"TRADER_SCORE_DATABASE_URL OR DATABASE_URL REQUIRED\")\n",
        "        raise RuntimeError(\"TRADER_SCORE_DATABASE_URL_OR_DATABASE_URL_REQUIRED\")\n")],
}

#: DISCLOSED LANE PATCHES: unified diffs of installed package files against the
#: installed baseline, each pinned by sha256. `_package_bytes` reverses every
#: hunk (its installed text must occur exactly once) BEFORE the COMPAT_DIFFS,
#: so the result must still hash to the package MANIFEST: nothing but the
#: disclosed hunks can differ.
#:
#: RC6 lane G2 (requirement register F3): the canonical fixture adapter read
#: premap keys us_premap never carried (home_team / away_team / league /
#: sport; research-sql run 37880110851), so no held fixture without a
#: fixture-metadata row could ever be established. The patch reads the
#: venue's own two team objects of the event (storage.venue_participants),
#: names the venue league codes production shows per sport
#: (core.VENUE_LEAGUE_CODES), and lets a fixture whose venue states no
#: home/away match a provider game in either orientation with a unique
#: full-name assignment (core.assign_sides). Its tests are
#: tests/test_rc6_lgs_venue_fixture_adapter.py.
LANE_PATCHES = {
    "tests/fixtures/live_game_state_lane_g2.patch":
        "227eb2a6b8565838504652ab361c344c95c785e532dc8b691ecdd806f84d7456",
}
#: the package files a lane patch may touch
LANE_PATCHED_FILES = {"sportsassets/live_game_state/core.py",
                      "sportsassets/live_game_state/storage.py"}

#: the package's component tests, per module (118 in all)
PACKAGE_TEST_COUNTS = {"test_live_game_state_collector.py": 15,
                       "test_live_game_state_core.py": 62,
                       "test_live_game_state_integration.py": 13,
                       "test_live_game_state_providers.py": 28}


def _lane_hunks(patch_text: str) -> dict:
    """{installed path: [(installed block, baseline block), ...]} of a unified
    diff, each hunk consumed by the line counts of its own @@ header."""
    out: dict = {}
    lines = patch_text.splitlines(keepends=True)
    i, path = 0, None
    while i < len(lines):
        line = lines[i]
        if line.startswith("+++ b/backend/"):
            path = line[len("+++ b/backend/"):].strip()
        elif line.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", line)
            assert m and path, line
            old_n, new_n = int(m.group(1) or 1), int(m.group(2) or 1)
            installed, baseline = [], []
            i += 1
            while old_n or new_n:
                tag, body = lines[i][:1], lines[i][1:]
                assert tag in (" ", "+", "-"), lines[i]
                if tag in (" ", "+"):
                    installed.append(body)
                    new_n -= 1
                if tag in (" ", "-"):
                    baseline.append(body)
                    old_n -= 1
                i += 1
            out.setdefault(path, []).append(("".join(installed), "".join(baseline)))
            continue
        i += 1
    return out


def _all_lane_hunks() -> dict:
    out: dict = {}
    for rel in LANE_PATCHES:
        for path, hunks in _lane_hunks((BACKEND / rel).read_text(encoding="utf-8")).items():
            out.setdefault(path, []).extend(hunks)
    return out


def _package_bytes(rel: str) -> bytes:
    text = (BACKEND / rel).read_text(encoding="utf-8")
    for installed, baseline in _all_lane_hunks().get(rel, []):
        assert text.count(installed) == 1, (rel, installed[:200])
        text = text.replace(installed, baseline)
    for installed, package in COMPAT_DIFFS.get(rel, []):
        assert text.count(installed) == 1, (rel, installed)
        text = text.replace(installed, package)
    return text.encode("utf-8")


def test_every_installed_file_is_the_package_byte_for_byte_but_the_disclosed_lines():
    for rel, want in PACKAGE_SHA256.items():
        assert hashlib.sha256(_package_bytes(rel)).hexdigest() == want, rel


def test_the_compatibility_diff_is_exactly_two_files():
    assert sorted(COMPAT_DIFFS) == ["sportsassets/live_game_state/storage.py",
                                    "sportsassets/workers/trader_live_scores.py"]
    for rel in COMPAT_DIFFS:
        raw = (BACKEND / rel).read_bytes()
        assert hashlib.sha256(raw).hexdigest() != PACKAGE_SHA256[rel], rel


def test_every_lane_patch_is_pinned_and_touches_only_its_disclosed_files():
    for rel, want in LANE_PATCHES.items():
        assert hashlib.sha256((BACKEND / rel).read_bytes()).hexdigest() == want, rel
    hunks = _all_lane_hunks()
    assert set(hunks) == LANE_PATCHED_FILES
    for rel, pairs in hunks.items():
        assert pairs and all(inst != base for inst, base in pairs), rel
        assert hashlib.sha256((BACKEND / rel).read_bytes()).hexdigest() != PACKAGE_SHA256[rel], rel
        # a hunk that no longer matches the installed file is drift, not a pass
        text = (BACKEND / rel).read_text(encoding="utf-8")
        for inst, _ in pairs:
            assert text.count(inst) == 1, (rel, inst[:200])


def test_the_package_tree_has_no_unlisted_module():
    pkg = BACKEND / "sportsassets" / "live_game_state"
    listed = {p for p in PACKAGE_SHA256 if p.startswith("sportsassets/live_game_state/")}
    assert {"sportsassets/live_game_state/" + p.name for p in pkg.glob("*.py")} == listed


def test_the_readmodel_carries_exactly_the_installer_hooks():
    src = (BACKEND / "sportsassets" / "api" / "trader_readmodel.py").read_text()
    imp = ("from .. import trader_mode as T\n"
           "from ..live_game_state.integration import enrich_snapshot as "
           "enrich_live_game_snapshot\n")
    hook = ("            snapshot = await enrich_live_game_snapshot(conn, snapshot, "
            "now=evaluated_at)\n            return snapshot\n")
    assert src.count(imp) == 1
    assert src.count(hook) == 1
    assert src.count("enrich_live_game_snapshot") == 2


def test_the_118_package_component_tests_are_all_present():
    got = {}
    for name in PACKAGE_TEST_COUNTS:
        tree = ast.parse((BACKEND / "tests" / name).read_text())
        got[name] = sum(1 for c in ast.walk(tree) if isinstance(c, ast.ClassDef)
                        for m in c.body
                        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and m.name.startswith("test_"))
    assert got == PACKAGE_TEST_COUNTS
    assert sum(got.values()) == 118

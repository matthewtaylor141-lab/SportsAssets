"""RUN 83.4 -- the artifact proofs. Physical absence, not import-graph absence.

Run 83.3 could only prove IMPORT_GRAPH_ABSENCE for the collector: the obs
modules imported no SDK, but they ran inside sportsassets-workers, whose image
installs polymarket-us for live_executor, in the mirror's own process. A late
import or a getattr was reachable in principle.

Run 83.4 builds a STANDALONE ARTIFACT and censuses the built tree. What is not
in the tree cannot be imported by any means.

THE ARTIFACT IS ASSEMBLED BY COPY, VERBATIM. Every shipped obs file is
re-hashed against its source, so the artifact runs the same bytes the 250-odd
in-place tests test. That is possible only because the obs package imports
NOTHING outside the standard library -- asserted below, because it is the
property the whole assembly rests on.
"""
from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
COLLECTOR_ROOT = REPO / "rn1-collector"
BROKER_ROOT = REPO / "pmus-broker"
OBS = BACKEND / "sportsassets" / "obs"

pytestmark = pytest.mark.skipif(not COLLECTOR_ROOT.exists(),
                                reason="run 83.4 artifacts not present")

PROHIBITED = ("polymarket_us", "py_clob_client", "live_executor", "mirror_live",
              "eth_account", "web3", "coincurve", "nacl")


def _census() -> dict:
    out = subprocess.run(
        [sys.executable, str(COLLECTOR_ROOT / "build_artifact.py"), "--json"],
        capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stdout + out.stderr
    return json.loads(out.stdout)


def test_the_obs_core_imports_only_the_standard_library():
    """The property the whole verbatim assembly depends on.

    If an obs module ever grows a third-party import, the artifact would have to
    install it -- and the census's guarantee would weaken from "this tree is all
    there is" to "this tree plus whatever that pulled in".
    """
    stdlib = set(sys.stdlib_module_names)
    strays = set()
    for path in sorted(OBS.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                strays |= {a.name.split(".")[0] for a in node.names} - stdlib
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                root = node.module.split(".")[0]
                if root not in stdlib and root != "sportsassets":
                    strays.add(root)
    assert not strays, f"the obs core now imports third-party code: {sorted(strays)}"


def test_the_collector_artifact_excludes_every_prohibited_component():
    rep = _census()
    assert rep["prohibited_findings"] == [], rep["prohibited_findings"]
    assert rep["COLLECTOR_PHYSICAL_ABSENCE"] == "ACHIEVED"


def test_the_collector_artifact_ships_the_same_bytes_that_were_tested():
    rep = _census()
    assert rep["verbatim_failures"] == [], rep["verbatim_failures"]


def test_the_collector_artifact_declares_only_three_dependencies():
    rep = _census()
    assert rep["declared_dependencies"] == [
        "httpx>=0.27", "websockets>=12.0", "asyncpg>=0.29"]
    assert set(rep["third_party_imports"]) <= {"httpx", "websockets", "asyncpg"}
    # asyncpg is DECLARED but not yet imported: the row-writing path is not
    # wired into the runner. Stated rather than quietly tidied away, because a
    # declared-but-unused dependency is still installed in the image.
    assert "asyncpg" not in rep["third_party_imports"]


def test_the_built_tree_contains_no_config_module_with_secret_fields():
    """sportsassets/config.py declares pmus_key_id and pmus_secret_key.

    It is not shipped. That is not incidental: shipping it would put
    secret-shaped configuration fields inside the process whose entire purpose
    is to have none, even though nothing in obs ever read them.
    """
    build = COLLECTOR_ROOT / "build"
    assert build.exists(), "build the artifact first"
    assert not (build / "sportsassets" / "config.py").exists()
    assert not (build / "sportsassets" / "pmus.py").exists()
    assert not (build / "sportsassets" / "db.py").exists()
    real_config = (BACKEND / "sportsassets" / "config.py").read_text()
    assert "pmus_secret_key" in real_config, (
        "the backend config no longer declares the PMUS secret -- if it moved, "
        "re-check what the artifact must now exclude")


def test_every_shipped_file_is_named_in_the_build_manifest():
    """No file reaches the artifact except by an explicit line in the manifest."""
    src = (COLLECTOR_ROOT / "build_artifact.py").read_text()
    tree = ast.parse(src)
    manifest = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.AnnAssign)
                    and getattr(n.target, "id", "") == "SHIPPED_OBS_MODULES")
    named = {e.value for e in manifest.value.elts}
    build = COLLECTOR_ROOT / "build" / "sportsassets" / "obs"
    shipped = {p.name for p in build.glob("*.py")}
    assert shipped == named, (shipped ^ named)


# =====================================================================
# THE BROKER ARTIFACT
# =====================================================================
def test_the_broker_artifact_declares_one_dependency_and_no_capability():
    toml = (BROKER_ROOT / "pyproject.toml").read_text()
    deps = toml.split("dependencies = [", 1)[1].split("]", 1)[0]
    declared = [ln.strip().strip('",') for ln in deps.splitlines()
                if ln.strip().startswith('"')]
    assert declared == ["pynacl>=1.5.0"], declared
    for banned in ("polymarket-us", "py-clob-client", "httpx", "requests",
                   "aiohttp", "asyncpg", "psycopg", "websockets", "eth-account"):
        assert banned not in deps, f"{banned} is a broker dependency"


def test_the_broker_source_imports_nothing_that_could_reach_a_venue_or_a_database():
    forbidden = {"httpx", "requests", "aiohttp", "urllib3", "urllib", "asyncpg",
                 "psycopg", "psycopg2", "sqlalchemy", "websockets",
                 "polymarket_us", "py_clob_client"}
    offenders = []
    for path in sorted((BROKER_ROOT / "pmusbroker").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in forbidden:
                        offenders.append(f"{path.name}: {a.name}")
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                if node.module.split(".")[0] in forbidden:
                    offenders.append(f"{path.name}: {node.module}")
    assert not offenders, offenders


def test_the_broker_holds_no_wallet_material_and_no_generic_signer():
    src = "\n".join(p.read_text()
                    for p in sorted((BROKER_ROOT / "pmusbroker").rglob("*.py")))
    for banned in ("eth_account", "web3", "Web3", "private_key_hex",
                   "mnemonic", "keystore"):
        assert banned not in src, banned
    # nacl IS present -- it is the signing primitive, and the broker is the one
    # process entitled to it. The restriction is that it is reachable only
    # through a function with no signing parameters.
    assert "from nacl.signing import SigningKey" in src


def test_the_two_artifacts_do_not_share_a_package():
    """Separate images, separate processes -- so separate trees, with no path
    by which one could import the other."""
    broker_pkgs = {p.name for p in (BROKER_ROOT).iterdir() if p.is_dir()
                   and not p.name.startswith((".", "__"))}
    collector_pkgs = {p.name for p in (COLLECTOR_ROOT).iterdir() if p.is_dir()
                      and not p.name.startswith((".", "__"))}
    assert "pmusbroker" in broker_pkgs
    assert "rn1collector" in collector_pkgs
    assert not (broker_pkgs & collector_pkgs - {"build", "tests"})

    collector_src = "\n".join(
        p.read_text() for p in sorted((COLLECTOR_ROOT / "rn1collector").rglob("*.py")))
    assert "pmusbroker" not in collector_src, (
        "the collector imports the broker package -- that would put the signer "
        "back in the collector's process")

"""Assemble and census the standalone rn1-collector artifact.

WHY AN ASSEMBLED ARTIFACT RATHER THAN A REFACTOR. The observability modules are
tested in place, in the backend tree, by 250-odd tests. Moving them would mean
the artifact runs different bytes from the ones under test until every test
moved too. So the artifact is ASSEMBLED BY COPY, verbatim, with no source
rewriting at all -- `_verify_verbatim` re-hashes every shipped file against its
source and fails if one differs by a byte.

That is possible only because of a property worth stating: the obs package
imports NOTHING outside the standard library. Its third-party surface is empty,
so copying it needs no dependency to come with it.

WHAT IS DELIBERATELY NOT COPIED, and this is the point of the whole exercise:

    sportsassets/config.py      declares pmus_key_id and pmus_secret_key. It is
                                replaced by NOTHING -- the obs modules never
                                imported it, and shipping it would put
                                secret-shaped configuration fields inside the
                                one process that must not have any.
    sportsassets/pmus.py        the venue client
    sportsassets/live_executor.py, mirror_*  the money path
    everything else in sportsassets/

The census then proves physical absence from the built tree and from the
declared dependency set, which is a stronger claim than the import-graph
absence Run 83.3 could make.

Run:  python3 build_artifact.py --out build
      python3 build_artifact.py --census-only
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
BACKEND = REPO / "backend"
OBS_SRC = BACKEND / "sportsassets" / "obs"

# ---------------------------------------------------------------- the manifest
# The explicit list of first-party source that ships. Adding a line here is the
# only way to widen the artifact, and it shows up in a diff.
SHIPPED_OBS_MODULES: tuple[str, ...] = (
    "__init__.py",
    "clock.py",
    "config.py",          # obs/config.py -- the OFFSETS ladder and the switches
    "book.py",
    "subject.py",
    "slot.py",
    "scheduler.py",
    "streamstate.py",
    "pmus_stream.py",
    "clob_stream.py",
    "handshake.py",
    "capacity.py",
    "record.py",
    "schedule.py",
    "cache.py",
    "collector.py",
)

# Third-party distributions the artifact is allowed to install.
ALLOWED_DISTRIBUTIONS: tuple[str, ...] = ("httpx", "websockets", "asyncpg")

# Any of these appearing in the built tree, in an import, or in the declared
# dependency set is a FAILED BUILD. The list is the owner's, verbatim.
PROHIBITED_COMPONENTS: tuple[str, ...] = (
    "polymarket_us", "polymarket-us",
    "py_clob_client", "py-clob-client",
    "live_executor", "mirror_live", "executor",
    "eth_account", "web3", "eth_keys", "coincurve",
    "nacl", "pynacl",            # the general signer -- broker only
)


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assemble(out: pathlib.Path) -> dict:
    """Copy the shipped source into a clean tree. No rewriting."""
    if out.exists():
        shutil.rmtree(out)
    pkg = out / "sportsassets"
    obs = pkg / "obs"
    obs.mkdir(parents=True)

    # A minimal package marker. The real sportsassets/__init__.py is three
    # lines and a docstring, but writing our own keeps the artifact from
    # depending on the backend package's identity at all.
    (pkg / "__init__.py").write_text(
        '"""Namespace shim for the standalone rn1-collector artifact.\n\n'
        "Only sportsassets.obs ships here. There is no config module, no venue\n"
        "client and no executor in this tree -- see build_artifact.py.\n"
        '"""\n'
    )

    shipped = {}
    for name in SHIPPED_OBS_MODULES:
        src = OBS_SRC / name
        if not src.exists():
            raise SystemExit(f"manifest names a missing source file: {name}")
        shutil.copy2(src, obs / name)
        shipped[f"sportsassets/obs/{name}"] = _sha(src)

    # The runner package ships as-is.
    shutil.copytree(HERE / "rn1collector", out / "rn1collector")
    for p in sorted((out / "rn1collector").rglob("*.py")):
        shipped[str(p.relative_to(out))] = _sha(p)
    return shipped


def _verify_verbatim(out: pathlib.Path) -> list[str]:
    """Every shipped obs file must be byte-identical to its source."""
    bad = []
    for name in SHIPPED_OBS_MODULES:
        src, dst = OBS_SRC / name, out / "sportsassets" / "obs" / name
        if not dst.exists():
            bad.append(f"{name}: missing from the artifact")
        elif _sha(src) != _sha(dst):
            bad.append(f"{name}: artifact differs from source")
    return bad


def census(out: pathlib.Path) -> dict:
    """File census, import census, and the prohibited-component scan."""
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*")
                   if p.is_file())

    imports: set[str] = set()
    for p in sorted(out.rglob("*.py")):
        tree = ast.parse(p.read_text(), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                imports.add(node.module.split(".")[0])

    declared = _declared_dependencies()

    findings = []
    for banned in PROHIBITED_COMPONENTS:
        hits = [f for f in files if banned in f.lower().replace("-", "_")]
        if hits:
            findings.append(f"FILE {banned}: {hits}")
        if banned.replace("-", "_") in imports:
            findings.append(f"IMPORT {banned}")
        if any(banned in d.lower() for d in declared):
            findings.append(f"DEPENDENCY {banned}")

    stdlib = set(sys.stdlib_module_names)
    third_party = sorted(m for m in imports
                         if m not in stdlib and m != "sportsassets"
                         and m != "rn1collector")

    return {
        "files": files,
        "file_count": len(files),
        "imports_all": sorted(imports),
        "third_party_imports": third_party,
        "declared_dependencies": declared,
        "prohibited_findings": findings,
        "verbatim_failures": _verify_verbatim(out),
    }


def _declared_dependencies() -> list[str]:
    toml = (HERE / "pyproject.toml").read_text()
    block = toml.split("dependencies = [", 1)[1].split("]", 1)[0]
    return [ln.strip().strip('",') for ln in block.splitlines()
            if ln.strip().startswith('"')]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "build"))
    ap.add_argument("--census-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    out = pathlib.Path(args.out).resolve()
    if not args.census_only:
        assemble(out)
    report = census(out)

    ok = not report["prohibited_findings"] and not report["verbatim_failures"] \
        and set(report["third_party_imports"]) <= set(ALLOWED_DISTRIBUTIONS)
    report["COLLECTOR_PHYSICAL_ABSENCE"] = "ACHIEVED" if ok else "NOT_ACHIEVED"

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"artifact         : {out}")
        print(f"files            : {report['file_count']}")
        print(f"third-party      : {report['third_party_imports'] or 'NONE'}")
        print(f"declared deps    : {report['declared_dependencies']}")
        print(f"prohibited found : {report['prohibited_findings'] or 'NONE'}")
        print(f"verbatim failures: {report['verbatim_failures'] or 'NONE'}")
        print(f"COLLECTOR_PHYSICAL_ABSENCE = {report['COLLECTOR_PHYSICAL_ABSENCE']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

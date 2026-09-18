"""GATE 0. The environment is proved before the capability boundary is.

WHY THIS EXISTS.
Run 35366130658 acquired execution, reached GATE 1 and reported `33 failed,
2541 passed`. Thirty-three tests were not broken. One package was missing.
The gate refused to continue -- it did not fail open, and no venue request
was made -- but the reason arrived as a wall of test failures instead of as
a sentence naming the package.

WHAT THIS PROVES, AND WHAT IT DOES NOT.
It proves that every pin in the requirements file is importable AND that the
version actually resolved is the version that was pinned. It does not prove
the suite passes; GATE 1 still does that, unchanged. A pin that installs but
resolves to a different version is a failure here, because a silently
different version is the same class of defect as a silently absent one: the
environment is not the environment that was validated.

It makes no network request and imports nothing from the capture path.
"""

from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import sys

# The import name is not always the distribution name. Only the entries that
# actually differ are listed; everything else imports under its own name.
IMPORT_NAME = {
    "scikit-learn": "sklearn",
    "pyyaml": "yaml",
    "pillow": "PIL",
}

# Some packages do not carry __version__ on the module object. For those the
# installed distribution metadata is the authority.
def installed_version(dist: str, module) -> str:
    v = getattr(module, "__version__", None)
    if isinstance(v, str) and v:
        return v
    try:
        from importlib import metadata
        return metadata.version(dist)
    except Exception:
        return "UNREADABLE"


def parse_requirements(text: str):
    """Only exact `name==version` pins. Anything else is refused, not guessed."""
    pins, refused = [], []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "==" not in line:
            refused.append(line)
            continue
        name, _, version = line.partition("==")
        name, version = name.strip(), version.strip()
        if not name or not version:
            refused.append(line)
            continue
        pins.append((name, version))
    return pins, refused


def prove(pins):
    findings = []
    for dist, want in pins:
        mod_name = IMPORT_NAME.get(dist.lower(), dist.replace("-", "_"))
        row = {"DISTRIBUTION": dist, "MODULE": mod_name, "PINNED": want}
        try:
            module = importlib.import_module(mod_name)
        except Exception as exc:
            row["STATUS"] = "ABSENT"
            row["DETAIL"] = "%s: %s" % (type(exc).__name__, exc)
            findings.append(row)
            continue
        got = installed_version(dist, module)
        row["INSTALLED"] = got
        if got == "UNREADABLE":
            row["STATUS"] = "VERSION_UNREADABLE"
        elif got != want:
            row["STATUS"] = "VERSION_MISMATCH"
        else:
            row["STATUS"] = "OK"
        findings.append(row)
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--requirements", default="requirements-test.txt")
    ap.add_argument("--out", default=None,
                    help="optional path for the machine-readable finding set")
    args = ap.parse_args(argv)

    path = pathlib.Path(args.requirements)
    if not path.exists():
        print("ENVIRONMENT_NOT_PROVED: requirements file not found: %s" % path)
        return 2

    pins, refused = parse_requirements(path.read_text())
    report = {
        "REQUIREMENTS": str(path),
        "PYTHON": sys.version.split()[0],
        "PINS": len(pins),
        "UNPINNED_LINES": refused,
        "FINDINGS": prove(pins),
    }
    bad = [f for f in report["FINDINGS"] if f["STATUS"] != "OK"]
    report["VERDICT"] = "ENVIRONMENT_PROVED" if not bad and not refused \
        else "ENVIRONMENT_NOT_PROVED"

    if args.out:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True))

    for f in report["FINDINGS"]:
        print("  %-14s %-16s pinned=%-10s installed=%s%s" % (
            f["STATUS"], f["DISTRIBUTION"], f["PINNED"],
            f.get("INSTALLED", "-"),
            "  " + f["DETAIL"] if "DETAIL" in f else ""))
    if refused:
        print("  UNPINNED       %s" % ", ".join(refused))
    print(report["VERDICT"])
    return 0 if report["VERDICT"] == "ENVIRONMENT_PROVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

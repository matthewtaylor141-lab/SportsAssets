"""THE RUNNING PROCESS AGAINST THE RUNTIME DEPENDENCY MANIFEST.

`requirements.lock` names every runtime distribution at the version the
release gate ran against, plus the interpreter. The image installs that file;
this module checks, in the process that is actually serving, that the
installed distributions and the interpreter are the ones named. The result is
logged at boot (one line, `RUNTIME_MANIFEST ...`), so the hosting logs show
whether the deployed artifact -- not a local rebuild -- matches.

Read-only: it reads package metadata and a file. It raises nothing.
"""
from __future__ import annotations

import importlib.metadata as md
import pathlib
import platform

#: The interpreter the gate ran on; the image's base is pinned to it.
EXPECTED_PYTHON = "3.12.3"

_CANDIDATES = (
    pathlib.Path(__file__).resolve().parents[1] / "requirements.lock",  # source
    pathlib.Path("/app/requirements.lock"),                            # image
)


def _norm(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(".", "-")


def read_lock(path: pathlib.Path | None = None) -> dict[str, str]:
    p = path or next((c for c in _CANDIDATES if c.is_file()), None)
    if p is None:
        raise FileNotFoundError("requirements.lock")
    out: dict[str, str] = {}
    for line in p.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, _, ver = line.partition("==")
        out[_norm(name)] = ver.strip()
    return out


def check(lock: dict[str, str] | None = None) -> dict:
    try:
        want = lock if lock is not None else read_lock()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "LOCK_UNREADABLE", "error": type(e).__name__}
    mismatched, missing = [], []
    for name, ver in sorted(want.items()):
        try:
            got = md.version(name)
        except md.PackageNotFoundError:
            missing.append(name)
            continue
        if got != ver:
            mismatched.append({"package": name, "locked": ver, "installed": got})
    py = platform.python_version()
    return {
        "ok": not mismatched and not missing and py == EXPECTED_PYTHON,
        "locked": len(want),
        "matched": len(want) - len(mismatched) - len(missing),
        "mismatched": mismatched,
        "missing": missing,
        "python": py,
        "python_expected": EXPECTED_PYTHON,
    }


def log_line(result: dict) -> str:
    if result.get("reason"):
        return "RUNTIME_MANIFEST ok=False reason=%s" % result["reason"]
    return ("RUNTIME_MANIFEST ok=%s matched=%d/%d python=%s(expected %s) "
            "mismatched=%s missing=%s" % (
                result["ok"], result["matched"], result["locked"],
                result["python"], result["python_expected"],
                ",".join("%s:%s!=%s" % (m["package"], m["installed"],
                                        m["locked"])
                         for m in result["mismatched"]) or "none",
                ",".join(result["missing"]) or "none"))

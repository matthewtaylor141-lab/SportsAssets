"""The API process has a log handler for its own INFO lines, and the
workers do not inherit it.

2026-09-05: the desk-sweep instrument (pmus 'desk sweep US: ...',
app 'desk sweep kalshi: ...') logged at INFO and the API log read from
Render carried none of it, only uvicorn's access lines, because no
handler had ever been configured on the root logger in the API process
-- uvicorn configures 'uvicorn.*' and nothing else, and Python's
last-resort handler prints WARNING and above. sportsassets/api/app.py
(uvicorn's entry module) now configures the root the way
workers/__init__.py does, with httpx and httpcore held to WARNING.

THE SAME DAY'S REGRESSION, pinned here too: the first cut put that
configuration in sportsassets/api/__init__.py, and the workers import
sportsassets.api.copies_record at run time (whale_exits, analytics,
the poller), so the package __init__ ran in the WORKERS process on
the first whale_exits cycle after the deploy and set httpx to WARNING
there -- the per-call lines venue outages are read from went quiet,
and the mirror's first ON ticks were abandoned on three empty quotes
with no HTTP line to say what the venue had answered. A workers-shaped
process (sportsassets.workers, then the api modules the loops import)
must keep httpx's INFO lines.

Pinned in a FRESH interpreter, because under pytest the root logger
already carries the capture plugin's handlers and basicConfig is a
no-op there -- the production condition is only reproducible from a
clean process.
"""

import inspect
import re
import subprocess
import sys
from pathlib import Path

import sportsassets.api as api_pkg
import sportsassets.api.app as api_app

BACKEND = Path(__file__).resolve().parents[1]
PROBE = (
    "import logging{imp}\n"
    "logging.getLogger('sportsassets.pmus').info('desk sweep US: probe line')\n"
    "logging.getLogger('httpx').info('HTTP Request: GET https://example.invalid')\n"
    "logging.getLogger('sportsassets.api.app').warning('probe warning')\n"
)
# the workers' shape: the process entry first, then the api modules the
# loops import at run time (whale_exits, analytics, the poller)
WORKERS_IMPORTS = (", sportsassets.workers, sportsassets.api.copies_record, "
                   "sportsassets.api.pmus_account, sportsassets.api.track_record")


def _run(imports: str) -> str:
    code = PROBE.format(imp=imports)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(BACKEND), timeout=180)
    assert r.returncode == 0, r.stderr
    return r.stderr


def test_a_fresh_api_interpreter_prints_the_packages_info_lines_and_not_httpx():
    err = _run(", sportsassets.api.app")
    assert re.search(r"^\S+ \S+ INFO sportsassets\.pmus: desk sweep US: probe line$", err, re.M), err
    assert re.search(r"^\S+ \S+ WARNING sportsassets\.api\.app: probe warning$", err, re.M), err
    assert "HTTP Request:" not in err, err


def test_without_the_app_module_the_info_line_is_lost():
    """The control: the bug as it was. Python's last-resort handler prints
    the WARNING bare and drops the INFO."""
    err = _run("")
    assert "desk sweep US: probe line" not in err, err
    assert "probe warning" in err, err


def test_a_workers_shaped_process_keeps_httpx_at_info():
    """The regression: the api modules the workers import must not
    carry the API's handler with them. The workers' own handler prints
    the package INFO line AND httpx's."""
    err = _run(WORKERS_IMPORTS)
    assert re.search(r"^\S+ \S+ INFO sportsassets\.pmus: desk sweep US: probe line$", err, re.M), err
    assert re.search(r"^\S+ \S+ INFO httpx: HTTP Request: GET https://example\.invalid$", err,
                     re.M), err


def test_the_package_init_configures_nothing_and_app_does():
    assert "basicConfig" not in inspect.getsource(api_pkg)
    assert "setLevel" not in inspect.getsource(api_pkg)
    src = inspect.getsource(api_app)
    assert "logging.basicConfig(" in src
    assert "level=logging.INFO" in src
    assert '"httpx"' in src and '"httpcore"' in src


def test_nothing_outside_uvicorn_imports_the_app_module():
    """app.py is uvicorn's entry module; the handler it configures is
    the API process's alone. A worker or library module importing it
    would inherit it the way api/__init__ was inherited."""
    pat = re.compile(r"^\s*(from\s+(\.|sportsassets\.api)\.?app\s+import|import\s+sportsassets\.api\.app)",
                     re.M)
    hits = []
    for p in (BACKEND / "sportsassets").rglob("*.py"):
        if p.name == "app.py" and p.parent.name == "api":
            continue
        if pat.search(p.read_text()):
            hits.append(str(p.relative_to(BACKEND)))
    assert hits == [], hits

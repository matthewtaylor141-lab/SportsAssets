"""The API process has a log handler for its own INFO lines.

2026-09-05: the desk-sweep instrument (pmus 'desk sweep US: ...',
app 'desk sweep kalshi: ...') logged at INFO and the API log read from
Render carried none of it, only uvicorn's access lines, because no
handler had ever been configured on the root logger in the API process
-- uvicorn configures 'uvicorn.*' and nothing else, and Python's
last-resort handler prints WARNING and above. sportsassets/api/__init__.py
now configures the root the way workers/__init__.py does, with httpx
and httpcore held to WARNING.

Pinned in a FRESH interpreter, because under pytest the root logger
already carries the capture plugin's handlers and basicConfig is a
no-op there -- the production condition (uvicorn leaves the root bare)
is only reproducible from a clean process: importing sportsassets.api
makes a package INFO line appear on stderr in the workers' format and
keeps httpx's INFO lines out; the control run without the import shows
the line missing, which is the bug as it was.
"""

import inspect
import re
import subprocess
import sys
from pathlib import Path

import sportsassets.api as api_pkg

BACKEND = Path(__file__).resolve().parents[1]
PROBE = (
    "import logging{imp}\n"
    "logging.getLogger('sportsassets.pmus').info('desk sweep US: probe line')\n"
    "logging.getLogger('httpx').info('HTTP Request: GET https://example.invalid')\n"
    "logging.getLogger('sportsassets.api.app').warning('probe warning')\n"
)


def _run(with_import: bool) -> str:
    code = PROBE.format(imp=", sportsassets.api" if with_import else "")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(BACKEND), timeout=120)
    assert r.returncode == 0, r.stderr
    return r.stderr


def test_a_fresh_interpreter_prints_the_packages_info_lines_and_not_httpx():
    err = _run(with_import=True)
    assert re.search(r"^\S+ \S+ INFO sportsassets\.pmus: desk sweep US: probe line$", err, re.M), err
    assert re.search(r"^\S+ \S+ WARNING sportsassets\.api\.app: probe warning$", err, re.M), err
    assert "HTTP Request:" not in err, err


def test_without_the_package_init_the_info_line_is_lost():
    """The control: the bug as it was. Python's last-resort handler prints
    the WARNING bare and drops the INFO."""
    err = _run(with_import=False)
    assert "desk sweep US: probe line" not in err, err
    assert "probe warning" in err, err


def test_the_configuration_lives_in_the_package_init_so_it_runs_before_app():
    src = inspect.getsource(api_pkg)
    assert "logging.basicConfig(" in src
    assert "level=logging.INFO" in src
    assert '"httpx"' in src and '"httpcore"' in src

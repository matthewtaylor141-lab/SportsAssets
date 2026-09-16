"""procmem: the process's own memory, read one way, and glibc's two
allocator levers behind status values a log line can carry.

The 2026-09-05 kills (thirteen on sportsassets-workers between 17:59:41
and 20:21:49) were read from Render's event log because the process
had no reading of its own. rss_mb is that reading -- VmRSS from
/proc/self/status, in MB -- and None where it cannot be read, never an
invented figure. cap_malloc_arenas and malloc_trim are the API's two
August guards (api/app.py _cap_malloc_arenas, api/track_record.py
_malloc_trim) with a status the caller can print: the cap returns the
same "arena_max=2 rc=1" / "unavailable: <ExcName>" string the API's
census publishes, and the trim returns whether it ran at all.

Pinned against the REAL functions with builtins.open and ctypes.CDLL
faked around them, and once each against the real platform.
"""

from __future__ import annotations

import ast
import builtins
import ctypes
import inspect
import io

import pytest

from sportsassets import procmem

_STATUS = ("Name:\tpython\n"
           "VmPeak:\t  999999 kB\n"
           "VmSize:\t  888888 kB\n"
           "VmRSS:\t  123456 kB\n"
           "VmData:\t       1 kB\n")


def _proc_status(monkeypatch, text: str | None) -> None:
    """builtins.open answers /proc/self/status with `text` (or raises
    OSError when text is None); every other path is the real open."""
    real_open = builtins.open

    def _open(path, *args, **kwargs):
        if path == "/proc/self/status":
            if text is None:
                raise OSError(2, "No such file or directory", path)
            return io.StringIO(text)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)


# ------------------------------------------------------------------ rss_mb

def test_rss_mb_reads_vmrss_in_mb_to_one_decimal(monkeypatch):
    _proc_status(monkeypatch, _STATUS)
    assert procmem.rss_mb() == 120.6          # 123456 kB / 1024, not VmPeak or VmSize


def test_rss_mb_is_none_without_a_vmrss_line(monkeypatch):
    _proc_status(monkeypatch, "Name:\tpython\nVmPeak:\t  999999 kB\nVmSize:\t  888888 kB\n")
    assert procmem.rss_mb() is None


def test_rss_mb_is_none_for_a_vmrss_line_with_no_number(monkeypatch):
    _proc_status(monkeypatch, "Name:\tpython\nVmRSS:\n")
    assert procmem.rss_mb() is None


def test_rss_mb_is_none_for_a_vmrss_line_that_is_not_a_number(monkeypatch):
    _proc_status(monkeypatch, "Name:\tpython\nVmRSS:\tabc kB\n")
    assert procmem.rss_mb() is None


def test_rss_mb_is_none_when_proc_cannot_be_opened(monkeypatch):
    _proc_status(monkeypatch, None)
    assert procmem.rss_mb() is None


def test_rss_mb_on_this_platform_is_a_positive_float_or_none():
    got = procmem.rss_mb()
    assert got is None or (isinstance(got, float) and got > 0)


# ------------------------------------------------------------ the fake libc

class _Lib:
    """What ctypes.CDLL("libc.so.6") hands back, recording each call."""

    calls: list[tuple] = []

    def __init__(self, name: str, *, rc: int = 1, trim_raises: Exception | None = None):
        self.name = name
        self.rc = rc
        self.trim_raises = trim_raises

    def mallopt(self, param: int, value: int) -> int:
        _Lib.calls.append(("mallopt", self.name, param, value))
        return self.rc

    def malloc_trim(self, pad: int) -> int:
        _Lib.calls.append(("malloc_trim", self.name, pad))
        if self.trim_raises is not None:
            raise self.trim_raises
        return 1


def _fake_cdll(monkeypatch, **kw) -> list[tuple]:
    _Lib.calls = []
    monkeypatch.setattr(ctypes, "CDLL", lambda name: _Lib(name, **kw))
    return _Lib.calls


def _cdll_raises(monkeypatch, exc: Exception) -> None:
    def _raise(name):
        raise exc

    monkeypatch.setattr(ctypes, "CDLL", _raise)


# ------------------------------------------------------- cap_malloc_arenas

def test_the_cap_takes_on_this_platform():
    """glibc returns 1 from mallopt. Off glibc the string says
    unavailable and that is the whole assertion -- what must never
    happen is a silent claim of success."""
    status = procmem.cap_malloc_arenas()
    assert isinstance(status, str) and status.startswith("arena_max=2 rc=") \
        or status.startswith("unavailable: ")
    if not status.startswith("unavailable"):
        assert status == "arena_max=2 rc=1", f"mallopt did not take: {status}"


def test_the_cap_calls_mallopt_with_m_arena_max_and_the_limit(monkeypatch):
    calls = _fake_cdll(monkeypatch)
    assert procmem.cap_malloc_arenas() == "arena_max=2 rc=1"
    assert procmem.cap_malloc_arenas(4) == "arena_max=4 rc=1"
    assert calls == [("mallopt", "libc.so.6", -8, 2), ("mallopt", "libc.so.6", -8, 4)]


def test_the_cap_reports_a_zero_rc_rather_than_claiming_success(monkeypatch):
    _fake_cdll(monkeypatch, rc=0)
    assert procmem.cap_malloc_arenas() == "arena_max=2 rc=0"


def test_the_cap_names_the_exception_when_libc_is_unavailable(monkeypatch):
    _cdll_raises(monkeypatch, OSError("libc.so.6: cannot open shared object file"))
    status = procmem.cap_malloc_arenas()
    assert status.startswith("unavailable: OSError")
    assert status == "unavailable: OSError"


def test_the_cap_never_raises(monkeypatch):
    class Odd(Exception):
        pass

    _cdll_raises(monkeypatch, Odd("no dlopen here"))
    assert procmem.cap_malloc_arenas() == "unavailable: Odd"


# ------------------------------------------------------------- malloc_trim

def test_the_trim_runs_on_this_platform():
    assert procmem.malloc_trim() is True


def test_the_trim_calls_malloc_trim_zero_and_returns_true(monkeypatch):
    calls = _fake_cdll(monkeypatch)
    assert procmem.malloc_trim() is True
    assert calls == [("malloc_trim", "libc.so.6", 0)]


def test_the_trim_is_false_when_libc_is_unavailable(monkeypatch):
    _cdll_raises(monkeypatch, OSError("libc.so.6: cannot open shared object file"))
    assert procmem.malloc_trim() is False


def test_the_trim_is_false_and_never_raises_when_the_call_itself_raises(monkeypatch):
    _fake_cdll(monkeypatch, trim_raises=AttributeError("malloc_trim"))
    assert procmem.malloc_trim() is False


# ------------------------------------------------------------------ hygiene

def test_procmem_imports_nothing_from_api():
    """The API keeps its own copies until a later cleanup; the workers'
    guard must not pull the FastAPI app into the workers process."""
    tree = ast.parse(inspect.getsource(procmem))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "api" not in (node.module or ""), ast.unparse(node)
            assert node.level == 0 or "api" not in (node.module or ""), ast.unparse(node)
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("sportsassets.api"), ast.unparse(node)
    assert "from .api" not in inspect.getsource(procmem)
    assert "sportsassets.api" not in inspect.getsource(procmem)


@pytest.mark.parametrize("fn", [procmem.cap_malloc_arenas, procmem.malloc_trim])
def test_each_lever_only_touches_the_allocator(fn):
    src = inspect.getsource(fn)
    for forbidden in ("pool", "fetch", "execute", "requests", "httpx", "open("):
        assert forbidden not in src, forbidden

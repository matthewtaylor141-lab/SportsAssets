"""THE CONTROL PLANE'S STABLE IDENTIFIERS (pure; every stream uses these).

  opportunity_key(...)     fixture | us_market_slug | holding_side | line |
                           scope -- the unique-opportunity key, written with
                           EXACTLY the normalisation the canonical intent's
                           `opportunity_key` uses (the R30A intent stream adds
                           it to canonical_intent.py; at this stream's base it
                           is absent, so it is implemented here and a test
                           pins the two equal whenever both exist)
  opportunity_id(...)      'opp_' + sha256(opportunity_key)[:24]
  opportunity_id_of(v)     the same id from a raw key or an id (an intent
                           built by the intent stream carries the RAW key in
                           its `opportunity_id` field; this maps it)
  decision_clock(v)        the decision instant as one canonical string
                           (UTC ISO-8601, microseconds, 'Z'), identical
                           whether it comes from the process clock (a float)
                           or from Postgres (a timestamptz)
  snapshot_id(...)         'snap_' + sha256(opportunity_id | decision_clock |
                           code_sha)[:24]
  code_sha()               the running commit: RENDER_GIT_COMMIT when it is a
                           git object name, else `git rev-parse HEAD` of this
                           checkout, else 'UNAVAILABLE:<why>' -- never guessed,
                           never padded from a prefix. Computed once per
                           process (the online hook does no I/O for it).
  config_sha(d)            sha256 of a configuration's canonical JSON
  canonical_json / sha256  the canonical form (the canonical intent's own:
                           Decimals and floats as fixed strings, sorted keys)

A missing key part is the empty string, stated, never guessed (the same rule
as the intent's key). This module does no I/O except `code_sha()`'s first
call.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import pathlib
import re
import subprocess
from decimal import Decimal

from .. import canonical_intent as _CI

SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
OPP_RE = re.compile(r"^opp_[0-9a-f]{24}$")
SNAP_RE = re.compile(r"^snap_[0-9a-f]{24}$")
_EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
_US = _dt.timedelta(microseconds=1)


def canonical_json(obj) -> str:
    """The canonical intent's canonical JSON (one definition everywhere)."""
    return _CI.canonical_json(obj)


def sha256(obj) -> str:
    if isinstance(obj, (bytes, bytearray)):
        return hashlib.sha256(bytes(obj)).hexdigest()
    if isinstance(obj, str):
        return hashlib.sha256(obj.encode()).hexdigest()
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()


# ─────────────────────────── the opportunity ───────────────────────────

def _part(v) -> str:
    """One key part, normalised exactly as canonical_intent.opportunity_key
    (intent stream) normalises it: None -> '', a number in its normal
    decimal form (3.5 == 3.50 == Decimal('3.5')), a '|' inside a part
    replaced so the key always has five parts."""
    if v is None:
        return ""
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        return format(Decimal(repr(v) if isinstance(v, float) else str(v))
                      .normalize(), "f")
    return str(v).replace("|", "/")


def opportunity_key(*, fixture, us_market_slug, holding_side, line,
                    scope) -> str:
    """THE STABLE OPPORTUNITY KEY: fixture | us_market_slug | holding_side |
    line | scope (the grading period). A re-evaluation of the same contract,
    side, line and scope is the SAME opportunity."""
    return "|".join(_part(x) for x in (fixture, us_market_slug, holding_side,
                                       line, scope))


def opportunity_id_from_key(key: str) -> str:
    if not isinstance(key, str) or key.count("|") != 4:
        raise ValueError("an opportunity key has exactly five parts")
    return "opp_" + hashlib.sha256(key.encode()).hexdigest()[:24]


def opportunity_id(*, fixture, us_market_slug, holding_side, line,
                   scope) -> str:
    return opportunity_id_from_key(opportunity_key(
        fixture=fixture, us_market_slug=us_market_slug,
        holding_side=holding_side, line=line, scope=scope))


def opportunity_id_of(value: str) -> str:
    """An id from either form: 'opp_<24 hex>' is returned as is; a raw
    five-part key (what the intent stream stores in the intent's
    `opportunity_id` field) is hashed."""
    if isinstance(value, str) and OPP_RE.match(value):
        return value
    return opportunity_id_from_key(value)


# ─────────────────────────── the decision clock ────────────────────────

def clock_us(v) -> int:
    """Microseconds since the epoch, computed the way Postgres stores a
    timestamptz written from this float (Python's datetime conversion,
    which the database driver uses), so the process clock and the stored
    stamp give the same integer."""
    if isinstance(v, _dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=_dt.timezone.utc)
        return (v - _EPOCH) // _US
    if isinstance(v, Decimal):
        v = float(v)
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise ValueError("a decision clock is a datetime or epoch seconds")
    d = _dt.datetime.fromtimestamp(float(v), tz=_dt.timezone.utc)
    return (d - _EPOCH) // _US


def epoch(v) -> float | None:
    """Epoch seconds (microsecond resolution) of a datetime / number, or
    None."""
    if v is None:
        return None
    try:
        return clock_us(v) / 1e6
    except (ValueError, OverflowError, OSError):
        return None


def decision_clock(v) -> str:
    us = clock_us(v)
    d = _EPOCH + _dt.timedelta(microseconds=us)
    return d.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def snapshot_id(*, opportunity_id: str, decided_at, code_sha: str) -> str:
    """'snap_' + sha256(opportunity_id | decision_clock | code_sha)[:24]:
    the opportunity AT the decision instant, frozen by one code."""
    if not OPP_RE.match(str(opportunity_id)):
        raise ValueError("snapshot_id needs an 'opp_' opportunity id")
    if not code_sha:
        raise ValueError("snapshot_id needs the code sha (or its "
                         "UNAVAILABLE reason)")
    raw = "%s|%s|%s" % (opportunity_id, decision_clock(decided_at), code_sha)
    return "snap_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


# ─────────────────────────── code and config ───────────────────────────

_CODE_SHA: list = []


def _git_head() -> tuple[str | None, str | None]:
    root = pathlib.Path(__file__).resolve().parents[3]
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root),
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "GIT_UNAVAILABLE_%s" % type(exc).__name__.upper()
    sha = (out.stdout or "").strip().lower()
    if out.returncode != 0 or not re.match(r"^[0-9a-f]{40}$", sha):
        return None, "GIT_HEAD_NOT_READ"
    return sha, None


def code_sha(*, refresh: bool = False) -> str:
    """THE RUNNING COMMIT. RENDER_GIT_COMMIT when it is a git object name
    (7..40 hex: a prefix is kept as the prefix it is, never padded), else
    this checkout's HEAD, else 'UNAVAILABLE:<why>'. Cached for the process:
    the online shadow hook never runs git."""
    if _CODE_SHA and not refresh:
        return _CODE_SHA[0]
    env = (os.environ.get("RENDER_GIT_COMMIT") or "").strip().lower()
    if env and SHA_RE.match(env):
        val = env
    else:
        sha, why = _git_head()
        val = sha if sha else "UNAVAILABLE:%s" % (
            why if not env else "RENDER_GIT_COMMIT_NOT_A_GIT_OBJECT_NAME")
    _CODE_SHA[:] = [val]
    return val


def config_sha(config) -> str:
    """sha256 of a configuration's canonical JSON."""
    return sha256(config if config is not None else {})

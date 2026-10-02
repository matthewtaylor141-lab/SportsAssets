"""TEAM LOGOS BY STABLE IDENTITY: venue team id + league, never a name guess.

The manifest (assets/agents/team-logos/manifest.json) lists every bundled
team logo with its provenance: the venue team id and league it belongs to,
the venue's own name for that team, the source URL, retrieval time, content
type and sha256. A logo is returned for a (team_id, league) pair only when
  * the manifest has that exact pair,
  * the bundled file exists and its sha256 matches the manifest (checked once
    at load; a mismatch withdraws the entry), and
  * the caller's venue name folds to the manifest's venue name (a re-used or
    re-assigned id shows a different name and gets no logo).
An abbreviation is never used to find a logo. Anything else returns None and
the UI shows the initials badge.

MLB entries reuse the 30 existing mlbstatic files (mlb-logo-<statsapi id>);
the manifest records the venue-id -> statsapi-id bridge, established by exact
full-name equality with the statsapi team list in mlb-logo-sources.json.

Read-only, in-process, no network: nothing here runs in a trading or decision
path; it decorates read payloads for the command centre.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

ASSET_DIR = Path(__file__).resolve().parent / "assets" / "agents"
LOGO_DIR = ASSET_DIR / "team-logos"
MANIFEST = LOGO_DIR / "manifest.json"
STATIC_PREFIX = "/api/command/agents/static/"
CONTENT_TYPES = {".svg": "image/svg+xml", ".png": "image/png",
                 ".webp": "image/webp", ".jpg": "image/jpeg"}


def fold(name) -> str:
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


@lru_cache(maxsize=1)
def table() -> dict:
    """{(league, team_id): entry} for verified entries only, plus the list of
    withdrawn ones (reason) for the coverage report."""
    try:
        raw = json.loads(MANIFEST.read_text())
    except (OSError, ValueError):
        return {"logos": {}, "withdrawn": [], "manifest": None}
    logos, withdrawn = {}, []
    for e in raw.get("teams", []):
        try:
            key = (str(e["league"]).lower(), int(e["venue_team_id"]))
            path = ASSET_DIR / e["file"]
        except (KeyError, TypeError, ValueError):
            withdrawn.append({"entry": e, "reason": "MALFORMED"})
            continue
        if path.suffix not in CONTENT_TYPES or path.parent not in (
                ASSET_DIR, LOGO_DIR):
            withdrawn.append({"entry": e, "reason": "FILE_NOT_ALLOWED"})
        elif _sha(path) != e.get("sha256"):
            withdrawn.append({"entry": e, "reason": "SHA256_MISMATCH"})
        elif key in logos:
            withdrawn.append({"entry": e, "reason": "DUPLICATE_KEY"})
        else:
            logos[key] = dict(e)
    return {"logos": logos, "withdrawn": withdrawn,
            "manifest": {k: raw.get(k) for k in ("version", "built_at",
                                                 "note")}}


def static_files() -> dict:
    """{published name: (relative path, content type)} for the static route's
    allowlist: exactly the verified manifest files."""
    out = {}
    for e in table()["logos"].values():
        rel = e["file"]
        out[Path(rel).name] = (rel, CONTENT_TYPES[Path(rel).suffix])
    return out


def logo(team_id, league, venue_name) -> dict | None:
    if team_id is None or not league:
        return None
    try:
        key = (str(league).strip().lower(), int(team_id))
    except (TypeError, ValueError):
        return None
    e = table()["logos"].get(key)
    if not e or fold(venue_name) != fold(e.get("venue_name")):
        return None
    kind = "flag" if "flag" in str(e.get("source_kind") or "") else "logo"
    return {"url": STATIC_PREFIX + Path(e["file"]).name, "kind": kind,
            "source": e.get("source"), "retrieved_at": e.get("retrieved_at")}


def initials(display: str | None, abbr: str | None = None) -> str:
    if abbr:
        return str(abbr).upper()[:3]
    parts = [p for p in re.split(r"[\s\-]+", str(display or "")) if p]
    if len(parts) > 1:
        return "".join(p[0] for p in parts[:2]).upper()
    return str(display or "?")[:3].upper()


MATCHUP_SQL = """
SELECT DISTINCT p.market_slug, p.event_slug, q.team_id, q.team_name,
       q.team_safe_name, q.team_abbr, q.team_league
  FROM us_premap p
  JOIN us_premap q ON q.event_slug = p.event_slug
 WHERE p.market_slug = ANY($1::text[]) AND q.team_id IS NOT NULL"""


def _order(event_slug: str, abbr: str | None) -> int:
    """Teams in the order the event slug names them (<league>-<a>-<b>-date);
    ordering only -- identity comes from the id."""
    codes = str(event_slug or "").split("-")[1:3]
    try:
        return codes.index(str(abbr or "").lower())
    except ValueError:
        return 9


async def matchups(conn, slugs) -> dict:
    """{market slug: [team, ...]} with both teams of the slug's event, each
    {name, team_id, league, initials, logo|None}. Unknown slugs are absent."""
    from . import market_labels as ML
    want = sorted({str(s).strip().lower() for s in slugs if s})
    if not want:
        return {}
    try:
        if not await conn.fetchval(
                "SELECT to_regclass('us_premap') IS NOT NULL"):
            return {}
        rows = await conn.fetch(MATCHUP_SQL, want)
    except Exception:                                           # noqa: BLE001
        return {}
    out: dict = {}
    seen = set()
    for r in rows:
        slug = str(r["market_slug"]).lower()
        if (slug, r["team_id"]) in seen:
            continue
        seen.add((slug, r["team_id"]))
        row = dict(r)
        name = ML._team_display(row) or (row.get("team_name") or "").title()
        out.setdefault(slug, []).append({
            "name": name, "team_id": row["team_id"],
            "league": row.get("team_league"),
            "initials": initials(name, row.get("team_abbr")),
            "logo": logo(row["team_id"], row.get("team_league"),
                         row.get("team_name")),
            "_o": _order(row.get("event_slug"), row.get("team_abbr"))})
    for slug, teams in out.items():
        teams.sort(key=lambda t: (t.pop("_o"), t["name"]))
        if len(teams) > 2:
            # more than two team ids on one event is not a matchup we can
            # draw honestly: keep names, drop logos
            for t in teams:
                t["logo"] = None
    return out


async def decorate(conn, rows, slug_key="us_market_slug", field="matchup"):
    """Attach `matchup` to each dict row that has a slug. Never raises."""
    try:
        rows = [r for r in (rows or []) if isinstance(r, dict)]
        got = await matchups(conn, [r.get(slug_key) for r in rows])
        for r in rows:
            m = got.get(str(r.get(slug_key) or "").lower())
            if m:
                r[field] = m
    except Exception:                                           # noqa: BLE001
        pass
    return rows


def coverage() -> dict:
    t = table()
    by = {}
    for (lg, _), e in t["logos"].items():
        by[lg] = by.get(lg, 0) + 1
    return {"verified_by_league": by, "withdrawn": [
        {"league": w["entry"].get("league") if isinstance(w["entry"], dict)
         else None, "reason": w["reason"]} for w in t["withdrawn"]]}

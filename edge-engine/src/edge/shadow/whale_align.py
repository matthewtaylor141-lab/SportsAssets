"""Whale-alignment tagging (owner-approved 2026-08-04).

The unique asset: swisstony's 5.35M-fill history plus RN1's live flow,
running NEXT TO an independent fair-value engine — but never joined. This
module joins them at fill time: every live/paper engine MONEYLINE entry
is tagged whale_aligned=True when a source whale holds an open BUY on the
same team in the same game. Settlement then grades the aligned cohort
against the rest from the ledger record alone.

Measurement first, policy later: nothing trades differently yet. If the
aligned cohort settles better, alignment earns a threshold discount; if
not, the tag cost nothing. Moneyline only in v1 — a whale's spread/total
side needs line-exact matching that this join can't prove cheaply, and a
wrong alignment tag poisons the very measurement it exists for.

Identity join: game key = sorted team/code tokens + date, from either
side's slug shape (platform: "mlb-stl-cin-2026-08-06"; engine:
"aec-mlb-stl-cin-2026-08-06"). Side = mapper-bar team match (>=0.95)
between the whale's outcome name and the engine's.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# Market-kind prefixes the engine's venue uses; the platform's whale slugs
# have no prefix. Either way the game identity is the tokens before the
# date, minus the prefix.
_KIND_PREFIXES = {"aec", "asc", "atc", "tsc", "astatc", "aoc"}


def game_key(slug: str) -> str | None:
    """'aec-mlb-stl-cin-2026-08-06(-anything)' -> 'cin-mlb-stl|2026-08-06'.

    Sorted tokens make the key order-insensitive, so home/away listing
    differences between the platform and the venue cannot split a game.
    """
    m = _DATE_RE.search(slug or "")
    if not m:
        return None
    date = m.group(1)
    head = (slug or "")[: m.start()].strip("-")
    toks = [t for t in head.split("-") if t and t not in _KIND_PREFIXES]
    if len(toks) < 2:
        return None
    return f"{'-'.join(sorted(toks))}|{date}"


def build_map(rows: list[dict]) -> dict[str, list[str]]:
    """Platform rows [{slug, outcome}] -> {game_key: [whale outcome names]}."""
    out: dict[str, list[str]] = {}
    for r in rows or []:
        key = game_key(r.get("slug") or "")
        name = (r.get("outcome") or "").strip()
        if key and name:
            out.setdefault(key, [])
            if name not in out[key]:
                out[key].append(name)
    return out


def aligned(pmus_slug: str, outcome_name: str,
            whale_map: dict[str, list[str]]) -> bool:
    """Is a source whale long the SAME team in the SAME game?"""
    if not whale_map:
        return False
    key = game_key(pmus_slug)
    if key is None:
        return False
    names = whale_map.get(key)
    if not names:
        return False
    from edge.venues.mapper import team_score

    return any(team_score(n, outcome_name) >= 0.95 for n in names)


def fetch(base_url: str, token: str = "", timeout: float = 30.0) -> list[dict]:
    """Pull the source whales' open moneyline positions from the platform."""
    import requests

    if not base_url:
        return []
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.get(f"{base_url.rstrip('/')}/api/whale-open-identities",
                     headers=headers, timeout=timeout)
    if r.status_code != 200:
        log.info("whale identities fetch: HTTP %s", r.status_code)
        return []
    return (r.json() or {}).get("identities") or []

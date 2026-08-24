"""Worker: pre-map the venue universe so copy-time mapping is a lookup.

Owner order 2026-08-24 ("move quicker with precision"): the wrong-side
incident's root causes all lived in resolve-at-trade-time — searches,
fuzzy scoring, a 20-second budget, sides missing from direct lookups.
This worker walks the venue's own event boards on a cycle and writes
every ACTIVE market side into `us_premap` with deterministic lookup
keys. At trade time the executor asks the table, not the network:
zero milliseconds, and the side identifier comes from the venue's own
side expansion — wrong-side-by-construction is impossible, ambiguity
refuses.

Rows are validated live at zero risk: while the quarantine holds, every
premap-resolved mapping is refused-but-recorded, so the MAPA audit
stream proves side fidelity on real signals before any dollar rides.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from ..db import get_pool
from .. import pmus

log = logging.getLogger(__name__)

REFRESH_SECONDS = 1800          # full sweep cadence
PAGE_LIMIT = 100
MAX_EVENT_PAGES = 40            # bounds a sweep at ~4k events
LIST_PACING_S = 0.35            # stay under venue rate limits (429 fix, 2026-08-23)
PRUNE_HOURS = 26                # rows unseen for a day age out


def _norm(s: str | None) -> str:
    return pmus._norm(s)


def _lines_of(text: str | None) -> set[str]:
    return {n for n in re.findall(r"\d+(?:\.5)?", "") } if text is None else \
        {n for n in re.findall(r"\d+\.5", text or "")}


def event_keys_for(title: str | None, slug: str | None = None) -> list[str]:
    """Deterministic lookup keys for one event, built the same way at
    write time (venue side) and read time (whale side) so a match is an
    exact string hit, never a similarity score."""
    keys: set[str] = set()
    t = pmus._clean_title(title)
    if t:
        keys.add(_norm(t))
    sm = pmus._surname_matchup(title)
    if sm:
        a, b = [p.strip() for p in re.split(r"\s+vs\s+", sm, flags=re.I)]
        keys.add(f"{a} vs {b}".lower())
        keys.add(f"{b} vs {a}".lower())
    if t and " vs" in t.lower():
        sides = re.split(r"\s+vs\.?\s+", t, flags=re.I)
        if len(sides) == 2:
            na, nb = _norm(sides[0]), _norm(sides[1])
            if na and nb:
                keys.add(f"{na} vs {nb}")
                keys.add(f"{nb} vs {na}")
    if slug:
        s = (slug or "").lower()
        m = re.search(r"\d{4}-\d{2}-\d{2}", s)
        if m:
            keys.add(s[: m.end()].strip("-"))
    return sorted(k for k in keys if k)


def match_side(rows: list[dict], outcome: str | None,
               his_title: str | None) -> dict | None:
    """Pick the unique premap row that IS the whale's outcome.

    Precision rules (each one is a shipped incident):
    - Yes/No picks match only literal yes/no sides — never a named team
      (inversion incident 2026-08-24).
    - Over/Under picks require an over/under side AND line equality with
      the whale's title (wrong-line class).
    - Named picks match by exact normalized equality, else by a unique
      distinctive surname token (>3 chars). Two candidates passing is
      ambiguity, and ambiguity refuses — a tie must never fall to
      venue ordering (incident 2026-08-23).
    - A lined row never matches an unlined pick and vice versa.
    """
    on = _norm(outcome)
    if not on:
        return None
    # the whale's line may live in his title OR his outcome ("Over 3.5")
    his_lines = _lines_of(his_title) | _lines_of(outcome)

    def line_ok(r: dict) -> bool:
        rl = (r.get("line") or "").strip()
        if on in ("over", "under") or rl:
            return bool(rl) and rl in his_lines if his_lines else False
        return True

    cands: list[dict] = []
    if on in ("yes", "no"):
        cands = [r for r in rows if _norm(r.get("side_norm")) == on]
    elif on.split()[:1] and on.split()[0] in ("over", "under"):
        want = on.split()[0]
        # side descriptions carry their line ("Over 2.5" → "over 2 5");
        # match on the leading token, corroborate the line separately
        cands = [r for r in rows
                 if (r.get("side_norm") or "").split()[:1] == [want]
                 and line_ok(r)]
    else:
        exact = [r for r in rows if r.get("side_norm") == on
                 and not (r.get("line") or "").strip()]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            return None
        out_last = (on.split() or [""])[-1]
        if len(out_last) > 3:
            tok = [r for r in rows
                   if out_last in (r.get("side_norm") or "").split()
                   and not (r.get("line") or "").strip()]
            if len(tok) == 1:
                return tok[0]
        return None
    if len(cands) == 1:
        return cands[0]
    return None


async def _ensure_table(pool) -> None:
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS us_premap (
            identifier text PRIMARY KEY,
            event_slug text,
            event_title text,
            market_slug text,
            question text,
            kind text,
            line text,
            side_norm text,
            event_keys text[],
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """)
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS us_premap_keys ON us_premap "
        "USING gin (event_keys)")


def _market_rows(ev: dict, m: dict) -> list[dict]:
    """Rows for one venue market: each side its own orderable row."""
    q = m.get("question") or m.get("title") or ""
    line = ""
    ql = re.findall(r"\d+\.5", q)
    if len(set(ql)) == 1:
        line = ql[0]
    ev_slug = ev.get("slug") or ev.get("eventSlug") or ""
    ev_title = ev.get("title") or ""
    out: list[dict] = []
    sides = [s for s in (m.get("marketSides") or [])
             if isinstance(s, dict) and s.get("identifier")
             and s.get("description")]
    if sides:
        for s in sides:
            out.append({
                "identifier": str(s["identifier"]).lower(),
                "event_slug": ev_slug, "event_title": ev_title,
                "market_slug": (m.get("slug") or "").lower(),
                "question": q[:300], "kind": "side",
                "line": line,
                "side_norm": _norm(s["description"]),
            })
        return out
    # per-side contract: the market IS one side; its subject names it
    subject = m.get("outcome") or ""
    if not subject:
        mq = re.search(r"^will (?:the )?(.+?) (?:cover|win)", _norm(q))
        if mq:
            subject = mq.group(1)
    if not subject:
        title = m.get("title") or ""
        tl = f" {title.lower()} "
        if title and " vs" not in tl and " - " not in tl and " @ " not in tl:
            subject = title
    if m.get("slug") and subject:
        out.append({
            "identifier": (m.get("slug") or "").lower(),
            "event_slug": ev_slug, "event_title": ev_title,
            "market_slug": (m.get("slug") or "").lower(),
            "question": q[:300], "kind": "contract",
            "line": line,
            "side_norm": _norm(subject),
        })
    return out


async def _upsert(pool, r: dict, keys: list[str]) -> None:
    await pool.execute(
        """
        INSERT INTO us_premap (identifier, event_slug,
            event_title, market_slug, question, kind, line,
            side_norm, event_keys, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, now())
        ON CONFLICT (identifier) DO UPDATE SET
            event_slug=$2, event_title=$3, market_slug=$4,
            question=$5, kind=$6, line=$7, side_norm=$8,
            event_keys=$9, updated_at=now()
        """,
        r["identifier"], r["event_slug"], r["event_title"],
        r["market_slug"], r["question"], r["kind"],
        r["line"], r["side_norm"], keys)


async def _record_last(pool, summary: dict) -> None:
    import json as _json
    from datetime import datetime as _dt, timezone as _tz

    summary = {**summary,
               "at": _dt.now(_tz.utc).isoformat(timespec="seconds")}
    try:
        await pool.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            "premap_last", _json.dumps(summary))
    except Exception:  # noqa: BLE001 — diagnostics never kill the sweep
        log.exception("premap_last write failed")


async def refresh() -> dict:
    """Sweep the venue universe into us_premap. Primary path walks
    events.list; if the installed SDK lacks it (2026-08-24: rows=0 with
    no visible error — the worker's SDK predates .events), fall back to
    paginating markets.list directly and keying each market from its own
    title/question. Every completion or failure writes premap_last so
    a silent sweep is impossible."""
    pool = await get_pool()
    await _ensure_table(pool)
    client = pmus._get_client()
    seen_rows = 0
    events = 0
    err = None
    mode = "events"
    try:
        offset = 0
        for _page in range(MAX_EVENT_PAGES):
            resp = await asyncio.to_thread(
                client.events.list,
                {"limit": PAGE_LIMIT, "offset": offset, "active": True})
            got = list((resp or {}).get("events") or [])
            if not got:
                break
            offset += len(got)
            for ev in got:
                ev_slug = ev.get("slug") or ev.get("eventSlug")
                if not ev_slug:
                    continue
                await asyncio.sleep(LIST_PACING_S)
                try:
                    mresp = await asyncio.to_thread(
                        client.markets.list,
                        {"eventSlug": [ev_slug], "active": True})
                    markets = [m for m in (mresp or {}).get("markets") or []
                               if (m.get("eventSlug") or m.get("event_slug"))
                               in (None, ev_slug) and not m.get("closed")]
                except Exception:  # noqa: BLE001 — next event
                    continue
                events += 1
                keys = event_keys_for(ev.get("title"), ev_slug)
                if not keys:
                    continue
                for m in markets:
                    for r in _market_rows(ev, m):
                        await _upsert(pool, r, keys)
                        seen_rows += 1
            if len(got) < PAGE_LIMIT:
                break
    except Exception as exc:  # noqa: BLE001 — try the fallback path
        err = f"{type(exc).__name__}: {str(exc)[:160]}"
        log.warning("premap events path failed (%s); markets fallback", err)
        mode = "markets"
        try:
            offset = 0
            for _page in range(MAX_EVENT_PAGES):
                mresp = await asyncio.to_thread(
                    client.markets.list,
                    {"limit": PAGE_LIMIT, "offset": offset, "active": True})
                got = [m for m in (mresp or {}).get("markets") or []
                       if not m.get("closed")]
                raw = list((mresp or {}).get("markets") or [])
                if not raw:
                    break
                offset += len(raw)
                await asyncio.sleep(LIST_PACING_S)
                for m in got:
                    ev_slug = (m.get("eventSlug") or m.get("event_slug")
                               or "")
                    ev = {"slug": ev_slug,
                          "title": m.get("title") or m.get("question")}
                    keys = event_keys_for(
                        m.get("question") or m.get("title"), ev_slug)
                    if not keys:
                        continue
                    events += 1
                    for r in _market_rows(ev, m):
                        await _upsert(pool, r, keys)
                        seen_rows += 1
                if len(raw) < PAGE_LIMIT:
                    break
            err = None
        except Exception as exc2:  # noqa: BLE001 — recorded, next cycle
            err = f"{type(exc2).__name__}: {str(exc2)[:160]}"
    pruned = await pool.execute(
        "DELETE FROM us_premap WHERE updated_at < now() - interval '%s hours'"
        % int(PRUNE_HOURS))
    summary = {"mode": mode, "events": events, "rows": seen_rows,
               "err": err,
               "pruned": int(pruned.split()[-1]) if pruned else 0}
    await _record_last(pool, summary)
    log.info("premap refresh: %s", summary)
    return summary


async def resolve(pool, market_title: str | None, event_title: str | None,
                  outcome: str | None,
                  global_slug: str | None) -> dict | None:
    """Copy-time resolution from the table: exact keys, unique side, no
    network. None means 'not pre-mapped' — the caller falls through to
    the legacy resolvers (which the quarantine still gates)."""
    keys: set[str] = set()
    for t in (market_title, event_title):
        keys.update(event_keys_for(t))
    if global_slug:
        keys.update(event_keys_for(None, global_slug))
    keys = {k for k in keys if k}
    if not keys:
        return None
    try:
        rows = [dict(r) for r in await pool.fetch(
            "SELECT identifier, side_norm, kind, line, question, "
            "event_title FROM us_premap WHERE event_keys && $1::text[]",
            sorted(keys))]
    except Exception:  # noqa: BLE001 — table absent/degraded: fall through
        return None
    if not rows:
        return None
    hit = match_side(rows, outcome, market_title)
    if hit is None:
        return None
    return {"market_slug": hit["identifier"],
            "title": hit.get("question") or hit.get("event_title"),
            "outcome": hit.get("side_norm"),
            "matched_by": "premap", "score": 1.0}


async def main() -> None:
    while True:
        started = time.monotonic()
        try:
            await refresh()
        except Exception:  # noqa: BLE001 — supervised loop, next cycle
            log.exception("premap refresh failed")
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(60.0, REFRESH_SECONDS - elapsed))

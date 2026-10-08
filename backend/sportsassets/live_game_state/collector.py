"""Held-first, fair, bounded background collection. No market/execution authority."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import time
from zoneinfo import ZoneInfo

from .core import Fixture, Aliases, ScoreError, match_fixture, bind_game, display
from .providers import ScoreClient, ProviderError


@dataclass
class Work:
    due: float = 0.0
    last_attempt: float = 0.0
    state: str = "UNKNOWN"
    source: str | None = None
    failures: int = 0


class Collector:
    def __init__(self, client: ScoreClient, store, *, clock=time.time, max_state=1000,
                 per_cycle=12, enrich_summaries=False):
        if not 1 <= per_cycle <= 32 or not 1 <= max_state <= 1000:
            raise ValueError("invalid collector bounds")
        self.client, self.store, self.clock = client, store, clock
        self.max_state, self.per_cycle = max_state, per_cycle
        self.enrich_summaries = enrich_summaries
        self.work: dict[tuple[str, str], Work] = {}
        self.aliases = Aliases()
        self._sem = asyncio.Semaphore(2)
        self._cycle_lock = asyncio.Lock()
        self._overflow_cursor = 0

    async def _provider(self, f: Fixture, provider: str):
        binding = await self.store.binding(f, provider)
        if provider == "ESPN":
            day = datetime.fromtimestamp(f.start_at, ZoneInfo("America/New_York")).strftime("%Y%m%d")
            rows = await self.client.espn_board(f.league, day)
        else:
            rows = await self.client.odds_scores(f.league)
        game, why, refs = match_fixture(f, rows, self.aliases, binding)
        if game is None:
            raise ScoreError(why)
        raw = bind_game(f, game, alias_refs=refs, aliases=self.aliases)
        state = display(raw, venue=f.venue, event_id=f.event_id, now=self.clock())
        if state["status"] != "CURRENT":
            raise ScoreError(state.get("why") or "SCORE_NOT_CURRENT")
        return game, raw, binding

    async def refresh(self, f: Fixture, *, focused=False):
        async with self._sem:
            now = self.clock()
            primary_issue = None
            try:
                game, raw, binding = await self._provider(f, "ESPN")
                # Optional richer detail, never needed for scores and not called per browser.
                if focused and self.enrich_summaries:
                    try:
                        rows = await self.client.espn_summary(f.league, game["provider_event_id"])
                        g, _, refs = match_fixture(f, rows, self.aliases, raw["identity"])
                        if g is not None:
                            enriched = bind_game(f, g, alias_refs=refs, aliases=self.aliases)
                            if display(enriched, venue=f.venue, event_id=f.event_id, now=self.clock())["status"] == "CURRENT":
                                raw = enriched
                    except ScoreError:
                        pass  # The complete scoreboard remains valid; no mixed fields.
            except ScoreError as exc:
                primary_issue = "ESPN:" + str(exc)
                try:
                    _, raw, _ = await self._provider(f, "THE_ODDS_API")
                except ScoreError as fallback:
                    why = primary_issue + ";THE_ODDS_API:" + str(fallback)
                    await self.store.issue(f, why, now=self.clock())
                    return {"key": f.key, "ok": False, "why": why, "state": "UNKNOWN", "source": None}
            try:
                oid = await self.store.write(f, raw, now=self.clock(), issue=primary_issue)
            except ScoreError as exc:
                await self.store.issue(f, str(exc), now=self.clock())
                return {"key": f.key, "ok": False, "why": str(exc), "state": "UNKNOWN", "source": None}
            return {"key": f.key, "ok": True, "observation_id": oid,
                    "state": raw["game_status"], "source": raw["source"],
                    "fallback": primary_issue is not None, "why": primary_issue}

    @staticmethod
    def interval(f: Fixture, w: Work, now: float) -> float:
        if w.failures:
            return min(120.0, 10.0 * 2 ** min(4, w.failures-1))
        if w.state in ("FINAL", "CANCELED"):
            return 300.0
        if w.state in ("SCHEDULED", "POSTPONED"):
            return 30.0 if abs(now-f.start_at) < 900 else 120.0
        return 30.0 if w.source == "THE_ODDS_API" else 10.0

    async def cycle(self, fixtures: list[Fixture], *, focused_ids=(), census=None):
        async with self._cycle_lock:
            now = self.clock()
            # Deduplicate every market/side for the same exact venue event.
            unique, collisions = {}, set()
            for f in fixtures:
                if f.key in unique and unique[f.key].fingerprint != f.fingerprint:
                    collisions.add(f.key)
                unique[f.key] = f
            for k in collisions:
                unique.pop(k, None)
            live_keys = set(unique)
            self.work = {k: w for k, w in self.work.items() if k in live_keys}
            # State size bounded. Retain the oldest waiting fixtures rather than silently truncating.
            candidates = sorted(unique.values(), key=lambda f: (
                self.work.get(f.key, Work()).last_attempt, f.venue, f.event_id))
            state_overflow = max(0, len(candidates)-self.max_state)
            if state_overflow:
                # Round-robin across the full bounded caller census; dropping state
                # must not repeatedly prioritize the same alphabetic prefix.
                all_sorted = sorted(unique.values(), key=lambda f: f.key)
                n = len(all_sorted)
                start = self._overflow_cursor % n
                candidates = (all_sorted[start:] + all_sorted[:start])[:self.max_state]
                self._overflow_cursor = (start + self.max_state) % n
            else:
                candidates = candidates[:self.max_state]
            for f in candidates:
                self.work.setdefault(f.key, Work())
            self.work = {f.key: self.work[f.key] for f in candidates}
            due = [f for f in candidates if self.work[f.key].due <= now]
            focus = set(focused_ids)
            # Age first prevents a permanently focused fixture from starving others.
            due.sort(key=lambda f: (self.work[f.key].due,
                                   0 if f.event_id in focus else 1, self.work[f.key].last_attempt, f.key))
            chosen = due[:self.per_cycle]
            async def one(f):
                try:
                    return await self.refresh(f, focused=f.event_id in focus)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # No arbitrary exception message, which might contain DSNs or provider URLs.
                    return {"key": f.key, "ok": False, "state": "UNKNOWN", "source": None,
                            "why": "SCORE_COLLECTOR_ERROR:" + type(exc).__name__}
            results = await asyncio.gather(*(one(f) for f in chosen))
            finished = self.clock()
            for f, result in zip(chosen, results):
                w = self.work[f.key]
                w.last_attempt = finished
                w.failures = 0 if result["ok"] else min(6, w.failures+1)
                w.state, w.source = result["state"], result["source"]
                w.due = finished + self.interval(f, w, finished)
            reasons = {}
            for r in results:
                if not r["ok"]:
                    reasons[r["why"]] = reasons.get(r["why"], 0)+1
            report = {"worker": "trader_live_scores", "authority": "DISPLAY_ONLY",
                      "execution_authority": False, "held_fixture_count": len(unique),
                      "attempted": len(chosen), "successful": sum(r["ok"] for r in results),
                      "deferred_due": max(0, len(due)-len(chosen)), "state_overflow": state_overflow,
                      "identity_collisions": len(collisions), "errors": reasons,
                      "source_counts": {p: sum(r["ok"] and r["source"] == p for r in results)
                                        for p in ("ESPN", "THE_ODDS_API")},
                      "provider": self.client.counters, "fixture_census": census or {},
                      "observation_window": {"started_at": now, "finished_at": finished},
                      "rates_scope": "THIS_CYCLE_ONLY; NOT_A_GLOBAL_FRESHNESS_CLAIM"}
            await self.store.heartbeat(report, now=finished)
            return report

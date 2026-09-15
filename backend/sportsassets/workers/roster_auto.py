"""Apply the roster rules on a schedule, and write down why.

Owner order 2026-09-01 (evening): roster decisions are made by the data.
This worker is the hand that moves them. Every hour it reads the two
numbers the rules run on -- the edge gate's on-chain verdict on each
whale's own book, and the proof cohort's interval on our settled copies
of him -- computes every whale's state, applies the result, and records
each decision with the numbers that made it in roster_decisions.

WHAT IT WRITES, AND WHERE THE MONEY PATH READS IT.
  live_verified_whales  ingestion_state  -- the entry roster the copy
                        path consults (beats the env; same key the
                        admin endpoint writes). Contains measuring and
                        promoted whales. Demoted whales leave it; their
                        exits keep mirroring via exitable_whales().
  live_clip_overrides   ingestion_state  -- {whale: usd}. per_fill_usd
                        reads it ahead of PER_FILL_BY_WHALE, so a
                        measuring whale trades at measuring_clip(whale)
                        -- the owner's per-whale clip if he named one,
                        else MEASURE_CLIP_USD -- and a
                        promoted one at PROMOTED_CLIP_USD.
  roster_state          ingestion_state  -- {whale: state}, the memory
                        the rules need (demotion sticks).

FAIL CLOSED. If either input is unreadable this pass writes nothing: a
roster moved on a missing number is a roster moved on a guess. If the
worker itself dies, the last written roster stands -- exactly as it
would if the owner had set it by hand.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from ..analytics import roster_rules as rules
from ..db import get_pool

log = logging.getLogger(__name__)

ROSTER_AUTO = os.environ.get("ROSTER_AUTO", "on").lower() in ("1", "on", "true", "yes")
EVERY_S = float(os.environ.get("ROSTER_AUTO_EVERY_S", "3600"))
BOOT_DELAY_S = 90.0

K_ROSTER = "live_verified_whales"
K_CLIPS = "live_clip_overrides"
K_STATE = "roster_state"
# The last pass, published for the API (a separate service whose copy
# of this module never runs a pass): when, what went wrong, and every
# decision with its numbers.
K_LAST = "roster_auto_last"

_last: dict[str, Any] = {"at": None, "decisions": [], "error": None}


async def _read_state(pool, key: str, default: Any) -> Any:
    raw = await pool.fetchval("SELECT value FROM ingestion_state WHERE key=$1", key)
    if raw is None:
        return default
    return json.loads(raw) if isinstance(raw, str) else raw


async def _write_state(pool, key: str, value: Any) -> None:
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        key, json.dumps(value))


def _hardcoded_clip(whale: str) -> float | None:
    """What the money path used before the rules wrote a clip."""
    try:
        from ..live_executor import PENNY_TRIAL_PER_FILL_USD, PER_FILL_BY_WHALE
        return float(PER_FILL_BY_WHALE.get(whale, PENNY_TRIAL_PER_FILL_USD))
    except Exception:  # noqa: BLE001
        return None


async def compute(pool, snapshot_fn, realized_fn) -> list[rules.Decision]:
    """Read both inputs and the memory; return every whale's decision."""
    snap = snapshot_fn() or {}
    funded_map = snap.get("whales") or {}
    if snap.get("err"):
        raise RuntimeError(f"edge gate unreadable: {snap['err']}")
    realized_map = (await realized_fn(pool)).get("by_whale") or {}
    state = await _read_state(pool, K_STATE, {})
    if not isinstance(state, dict):
        state = {}
    state = {str(w).lower(): s for w, s in state.items()}
    # FIRST-PASS SEEDING. A whale the owner already put on the stored
    # roster is trading today; he enters the rules as MEASURING, not as
    # a stranger, so a gate that does not fund him this hour HOLDS him
    # pending a realized verdict rather than dropping him on sight.
    current = await _read_state(pool, K_ROSTER, [])
    if isinstance(current, str):
        current = current.split(",")
    for w in (current if isinstance(current, list) else []):
        w = str(w).strip().lower()
        if w:
            state.setdefault(w, "measuring")
    # A WHALE THE OWNER CUT IN CODE STAYS CUT. COPY_CUT_WHALES is the
    # hardcoded owner decision (exits mirrored, entries refused); a
    # funded book must not re-admit him at the measurement clip.
    try:
        from ..live_executor import COPY_CUT_WHALES
        for w in COPY_CUT_WHALES:
            state.setdefault(str(w).lower(), "cut")
    except Exception:  # noqa: BLE001 — never block the pass on an import
        pass
    edge_lower = {w: (g.get("ci95") or [None])[0] for w, g in funded_map.items()
                  if isinstance(g.get("ci95"), (list, tuple)) and g["ci95"]}
    edge_lower = {w: float(v) for w, v in edge_lower.items() if v is not None}
    return rules.plan(funded_map, realized_map, state, edge_lower)


async def apply(pool, decisions: list[rules.Decision]) -> dict:
    """Write the roster, the clips, the memory, and the audit rows."""
    roster = sorted(d.whale for d in decisions if d.to_state in ("measuring", "promoted"))
    # EVERY HELD WHALE GETS A CLIP, AND A DEMOTED ONE GETS 0. The 0 is
    # load-bearing twice: per_fill_usd reads it as a block ahead of the
    # hardcoded clip, and exitable_whales() reads the key's presence as
    # "still ours to sell", so his open copies keep mirroring his exits
    # after he has left the entry roster.
    clips = {d.whale: (float(d.clip_usd or 0.0)
                       if d.to_state in ("measuring", "promoted") else 0.0)
             for d in decisions if d.to_state != "absent"}
    state = {d.whale: d.to_state for d in decisions if d.to_state != "absent"}
    # A CLIP CHANGE IS A CHANGE (round four). The first pass seeds a
    # rostered whale as measuring and then decides measuring, so the
    # state never moved -- while his clip went from the hardcoded $250
    # to the $50 measurement clip, a 5x cut in deployment that read as
    # "held". It fires in the raising direction too: the owner's per-
    # whale clip (2026-09-04) moves rn1 and homerunhazard from a stored
    # $50 to $250 on the first pass after that deploy, and that move is
    # named. Compare against what actually bound before this pass: the
    # previously stored clip, else the hardcoded one.
    prev = await _read_state(pool, K_CLIPS, {})
    prev = prev if isinstance(prev, dict) else {}
    for d in decisions:
        if d.to_state == "absent":
            continue
        before = prev.get(d.whale)
        if before is None:
            before = _hardcoded_clip(d.whale)
        try:
            before = float(before)
        except (TypeError, ValueError):
            continue
        if abs(clips[d.whale] - before) > 1e-9:
            d.changed = True
            d.reason = f"clip ${before:.0f} -> ${clips[d.whale]:.0f}; {d.reason}"
    await _write_state(pool, K_STATE, state)
    await _write_state(pool, K_CLIPS, clips)
    if roster:
        await _write_state(pool, K_ROSTER, roster)
    else:
        # AN EMPTY ROSTER IS NEVER WRITTEN. The rules can demote every
        # whale; the money path then has nobody to copy, which is the
        # correct outcome -- but it is expressed by leaving the last
        # non-empty roster in place with every clip at 0, not by
        # writing [] and falling through to the env/code default that
        # a stale key used to override. Zero clips block entries.
        log.warning("roster_auto: rules leave NO whale on the roster; "
                    "clips zeroed, roster key left as-is")
    changed = [d for d in decisions if d.changed]
    for d in decisions:
        try:
            await pool.execute(
                "INSERT INTO roster_decisions (whale, from_state, to_state, "
                "clip_usd, reason, n, roi, ci_lo, ci_hi, clusters, funded, "
                "changed) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
                d.whale, d.from_state, d.to_state, d.clip_usd, d.reason,
                d.n, d.roi, d.ci_lo, d.ci_hi, d.clusters, d.funded, d.changed)
        except Exception as exc:  # noqa: BLE001 — audit row is best-effort
            log.warning("roster_auto: could not record decision for %s (%s)",
                        d.whale, type(exc).__name__)
    for d in changed:
        log.warning("ROSTER %s: %s -> %s clip=%s — %s", d.whale, d.from_state,
                    d.to_state, d.clip_usd, d.reason)
    return {"roster": roster, "clips": clips, "state": state,
            "changed": [d.as_row() for d in changed]}


async def owner_set(pool, whales: list[str]) -> dict:
    """The owner's set-roster, reconciled with the rules' memory.

    The owner's word outranks the rules in BOTH directions and must
    survive the next pass: a whale he puts on the roster enters as
    MEASURING even if the rules had demoted him (a promoted one keeps
    his promotion); a whale he takes off is CUT -- sticky, clip 0, exits
    still mirrored -- so the next pass cannot quietly re-admit him
    because his book happens to be funded. Called by the admin endpoint
    that writes the roster; writes state and clips beside it.
    """
    want = {str(w).strip().lower() for w in whales if str(w).strip()}
    state = await _read_state(pool, K_STATE, {})
    state = {str(w).lower(): s for w, s in state.items()} if isinstance(state, dict) else {}
    clips = await _read_state(pool, K_CLIPS, {})
    clips = dict(clips) if isinstance(clips, dict) else {}
    changed: list[dict] = []
    for w in sorted(want):
        prev = state.get(w, "absent")
        if prev != "promoted":
            state[w] = "measuring"
            clips[w] = rules.measuring_clip(w)
        else:
            clips[w] = rules.PROMOTED_CLIP_USD
        if prev != state[w]:
            changed.append({"whale": w, "from": prev, "to": state[w]})
    for w, s in list(state.items()):
        if w not in want and s in ("measuring", "promoted"):
            state[w] = "cut"
            clips[w] = 0.0
            changed.append({"whale": w, "from": s, "to": "cut"})
    await _write_state(pool, K_STATE, state)
    await _write_state(pool, K_CLIPS, clips)
    for c in changed:
        try:
            await pool.execute(
                "INSERT INTO roster_decisions (whale, from_state, to_state, "
                "clip_usd, reason, changed) VALUES ($1,$2,$3,$4,$5,true)",
                c["whale"], c["from"], c["to"], clips.get(c["whale"]),
                "owner set-roster")
        except Exception:  # noqa: BLE001 — audit row is best-effort
            pass
        log.warning("ROSTER (owner) %s: %s -> %s", c["whale"], c["from"], c["to"])
    return {"state": state, "clips": clips, "changed": changed}


async def run_once(pool, snapshot_fn=None, realized_fn=None) -> dict | None:
    """One pass. Returns what was applied, or None if nothing was."""
    if snapshot_fn is None or realized_fn is None:
        from .. import edge_gate
        from ..analytics.proof import COHORT_START, cohort_assess

        snapshot_fn = snapshot_fn or edge_gate.snapshot

        async def _realized(p):
            return await cohort_assess(p, COHORT_START)
        realized_fn = realized_fn or _realized
    try:
        decisions = await compute(pool, snapshot_fn, realized_fn)
    except Exception as exc:  # noqa: BLE001 — FAIL CLOSED: write nothing
        _last.update(at=time.time(), error=f"{type(exc).__name__}: {str(exc)[:160]}")
        log.warning("roster_auto: inputs unreadable, roster untouched (%s)", _last["error"])
        await _publish_last(pool)
        return None
    applied = await apply(pool, decisions)
    _last.update(at=time.time(), error=None,
                 decisions=[d.as_row() for d in decisions])
    await _publish_last(pool)
    return applied


async def _publish_last(pool) -> None:
    """The last pass, for the API. Best-effort; never a roster write."""
    try:
        await _write_state(pool, K_LAST, {
            "at": _last["at"], "error": _last["error"],
            "decisions": _last["decisions"]})
    except Exception as exc:  # noqa: BLE001
        log.warning("roster_auto: could not publish the last pass (%s)",
                    type(exc).__name__)


async def status(pool) -> dict:
    """What the rules last decided and what they wrote, read from the
    database -- the only place the API and the worker agree."""
    out = {"enabled_here": ROSTER_AUTO, "every_s": EVERY_S,
           "rules": snapshot()["rules"]}
    for key, name, default in ((K_LAST, "last", None), (K_STATE, "state", {}),
                               (K_CLIPS, "clips", {})):
        try:
            out[name] = await _read_state(pool, key, default)
        except Exception as exc:  # noqa: BLE001
            out[name] = default
            out["error"] = f"{key} unreadable: {type(exc).__name__}"
    return out


def snapshot() -> dict:
    """What the last pass decided, for the gates endpoint and the probe."""
    return {"enabled": ROSTER_AUTO, "every_s": EVERY_S, "last_at": _last["at"],
            "error": _last["error"], "decisions": _last["decisions"],
            "rules": {"measure_clip_usd": rules.MEASURE_CLIP_USD,
                      "promoted_clip_usd": rules.PROMOTED_CLIP_USD,
                      "min_n_promote": rules.MIN_N_PROMOTE,
                      "min_clusters_promote": rules.MIN_CLUSTERS_PROMOTE,
                      "min_n_demote": rules.MIN_N_DEMOTE,
                      "max_measuring": rules.MAX_MEASURING}}


async def main() -> None:
    if not ROSTER_AUTO:
        log.info("roster_auto disabled (ROSTER_AUTO=off); the roster is manual")
        # PARK, do not return (2026-09-05, the first hour the flag was
        # off): workers/all.py's supervise() restarts a loop that exits
        # cleanly every RESTART_DELAY_SECONDS, so a return here wrote
        # three lines every five seconds -- "starting loop", this line,
        # "exited cleanly; restarting" -- for as long as the roster was
        # manual, drowning the log the operator reads during a switch.
        # An event nobody sets holds the loop out of service in silence;
        # the flag is read at import, so only a deploy can re-enable it.
        await asyncio.Event().wait()
    from .. import edge_gate

    pool = await get_pool()
    await asyncio.sleep(BOOT_DELAY_S)          # let the gate cache warm
    log.info("roster_auto up: every %ss, rules=%s", EVERY_S, snapshot()["rules"])
    while True:
        try:
            try:
                await edge_gate.refresh(pool)
            except Exception:  # noqa: BLE001 — snapshot reports err
                pass
            await run_once(pool)
        except Exception:  # noqa: BLE001 — never dies
            log.exception("roster_auto pass failed")
        await asyncio.sleep(EVERY_S)

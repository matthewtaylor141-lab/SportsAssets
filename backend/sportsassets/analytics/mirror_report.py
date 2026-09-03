"""What the position-mirroring shadow has read (phase P0, 2026-09-02):
the ratio per whale, the latest row per market, and the counts that
decide whether P1 may turn orders on -- how often the resting order the
mirror would have placed was already fillable against the book, how far
the fill-derived position drifts from the exit worker's snapshot, and
how much of his activity mapped to a venue market at all.

THE PHASE 0 INSTRUMENTS (to-a-tee program, owner order 2026-09-02 "I
want us to match everything ... mirror the whales to a tee"): every gate
the later phases read is computed here from the row's JSONB detail --
the unmapped share by family and by his dollars; the mapped share by
mapping SOURCE (the quarantine admits premap and exact; a ledger-sourced
map is refused at P1 admission, so "mapped" overstated what P1 could
open); the would-fill rate split into NON-LEGACY plans (the ones P1
would actually place) and plans against a legacy per-fill row, each
with a market-clustered interval; the parallel short reading's rate;
the snapshot census; the dead-band dollars; his short side's share of
markets and dollars; and a would-P&L settle of the plans that would
have filled, from the markets that have since resolved.

INTERVALS: every rate here is the cluster-robust standard of
proof.roi_with_ci applied to a proportion -- stake 1 per plan, pnl 1
when it filled, clustered by market (event_key = condition_id) -- so a
market read twenty times is one game's worth of evidence, not twenty,
and the shadow's reading cannot be narrower than the live cohort's.
"""
from __future__ import annotations

import json
from typing import Any

# The mapping classes P1 admission accepts while the mapping quarantine
# holds -- the same set the copy lane's QUARANTINE_RESUME_SRC names
# (live_executor). Restated here so the report stays importable without
# the executor's dependencies; a test pins the two sets equal.
ADMISSIBLE_SRC = frozenset({"premap", "exact"})
# The three legacy shapes the would-fill gate must not count (to-a-tee
# timing review, F2): a plan the long-only target refused by name, a
# plan against a negative ledger (a legacy per-fill BUY_SHORT of ours),
# and a plan against a positive ledger that a per-fill row holds.
LEGACY_REASON_PREFIX = "short side not admitted"
DEAD_BAND_REASON = "under the dollar dead band"


def _detail(r: dict) -> dict:
    d = r.get("detail")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError:
            d = {}
    return d if isinstance(d, dict) else {}


def _num(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def is_legacy_plan(r: dict, d: dict | None = None, short: bool = False) -> bool | None:
    """True when the plan is one of the three legacy shapes, False when
    it is a plan P1 could have placed, None when the ledger facts are
    unreadable (excluded from BOTH cohorts and counted, never guessed)."""
    d = _detail(r) if d is None else d
    reason = str((d.get("reason_short") if short else r.get("reason")) or "")
    if not short and reason.startswith(LEGACY_REASON_PREFIX):
        return True
    # THE LEDGER'S OWN VERDICT COMES FIRST (Phase 0 review, minor 3): a
    # per-fill row that is EXITING, or filled rows netting to zero, leave
    # ledger_net at 0 while P1's legacy_row referee still refuses the
    # slug -- the flat-ledger shortcut below must not read that plan as
    # one P1 could have placed.
    legacy = d.get("ledger_legacy")
    if legacy:
        return True
    ledger = _num(r.get("ledger_net"))
    if ledger is None:
        return None
    if ledger < 0:
        return True
    if ledger == 0:
        return False
    if legacy is None:
        return None
    return bool(legacy)


def rate_with_ci(rows: list[dict], filled_key: str = "would_fill",
                 cluster_key: str = "condition_id") -> dict:
    """A fill rate with the cluster-robust interval of proof.roi_with_ci:
    each resolved plan stakes 1 and earns 1 when it filled, clustered by
    market, so the interval is the one the live cohort would be judged
    by (MIN_PROOF_CLUSTERS applies to the verdict, not to the number)."""
    from .proof import roi_with_ci

    resolved = [r for r in rows if r.get(filled_key) is not None]
    fills = [r for r in resolved if r.get(filled_key)]
    out: dict[str, Any] = {"orders": len(rows), "resolved": len(resolved), "fills": len(fills),
                           "rate": (round(len(fills) / len(resolved), 4) if resolved else None),
                           "ci95": None, "clusters": len({r.get(cluster_key) for r in resolved})}
    if resolved:
        ci = roi_with_ci([{"stake": 1.0, "pnl": 1.0 if r.get(filled_key) else 0.0,
                           "event_key": str(r.get(cluster_key) or "")} for r in resolved])
        if ci.get("ci95"):
            lo, hi = ci["ci95"]
            out["ci95"] = [round(max(0.0, lo), 4), round(min(1.0, hi), 4)]
            out["se"] = ci.get("se")
    return out


def _p(sorted_vals: list[float], q: float) -> float | None:
    """Nearest-rank percentile (the same rule the drift p90 uses)."""
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    return sorted_vals[min(n - 1, max(0, -(-int(q * 100) * n // 100) - 1))]


def summarize(latest: list[dict], all_rows: list[dict], ratios: dict) -> dict:
    """latest: newest row per (whale, market); all_rows: every row in the
    window. Pure, so the reading is testable."""
    n = len(all_rows)
    mapped = [r for r in all_rows if r.get("us_market_slug")]
    orders = [r for r in mapped if r.get("would_side")]
    # a plan is judged against the NEXT reading of its market; a plan
    # with no later reading is unresolved and counted on neither side
    resolved = [r for r in orders if r.get("would_fill") is not None]
    fills = [r for r in resolved if r.get("would_fill")]
    # a market can be BOTH "short side not admitted" and frozen: the
    # reason carries the target's why first (first shadow hours: Shelton
    # v Hurkacz read "short side not admitted; frozen: venue and ledger
    # disagree" and was counted in neither list)
    frozen = [r for r in mapped if "frozen" in str(r.get("reason") or "")]
    # drift = |fills-derived - venue-read| over the larger of the two, so
    # "fills say he holds, the venue says he is out" (snap 0, his > 0)
    # counts as full drift instead of being dropped (review round two)
    drift = []
    for r in mapped:
        hl, sl = r.get("his_long"), r.get("snap_long")
        if hl is None or sl is None:
            continue
        big = max(float(hl), float(sl))
        if big > 0:
            drift.append(abs(float(hl) - float(sl)) / big)
    drift_sorted = sorted(drift)
    # nearest-rank 90th percentile: the smallest value at or above 90%
    # of the readings (two readings -> the larger one)
    p90 = (drift_sorted[min(len(drift_sorted) - 1,
                            max(0, -(-9 * len(drift_sorted) // 10) - 1))]
           if drift_sorted else None)
    games = {r.get("condition_id") for r in mapped}
    # a stale positions read is excluded from drift by the worker (its
    # snap_* columns are NULL) and counted here so the gap is visible
    stale = 0
    for r in mapped:
        if _detail(r).get("snap_stale"):
            stale += 1
    out: dict[str, Any] = {
        "rows": n, "mapped_rows": len(mapped), "unmapped_rows": n - len(mapped),
        "markets": len({r.get("condition_id") for r in all_rows}),
        "mapped_markets": len(games),
        "would_orders": len(orders), "would_resolved": len(resolved), "would_fill": len(fills),
        "would_fill_rate": round(len(fills) / len(resolved), 4) if resolved else None,
        "frozen_rows": len(frozen),
        "drift_n": len(drift), "drift_p90": round(p90, 4) if p90 is not None else None,
        "drift_over_5pct": sum(1 for d in drift if d > 0.05),
        "stale_snapshot_rows": stale,
        "ratios": ratios,
        "latest": [dict({k: r.get(k) for k in (
            "at", "whale", "condition_id", "us_market_slug", "his_long", "his_other",
            "his_net", "snap_long", "snap_other", "ratio", "target", "capped",
            "ledger_net", "venue_net", "bid", "ask", "mark", "his_last_px",
            "would_side", "would_qty", "would_px", "would_fill", "reason")},
            detail=_detail(r))
            for r in latest],
    }
    out.update(phase0_census(latest, all_rows))
    nl = out["would_fill_nonlegacy"]
    out["reading"] = (
        f"shadow: {len(games)} mapped markets of {out['markets']} in the window; "
        f"would have placed {len(orders)} orders; of {len(resolved)} judged against the next "
        f"reading, {len(fills)} would have filled ({out['would_fill_rate'] if resolved else 'n/a'}); "
        f"non-legacy {nl['fills']}/{nl['resolved']} = {nl['rate'] if nl['rate'] is not None else 'n/a'} "
        f"ci95={nl['ci95'] if nl['ci95'] else 'n/a'} over {nl['clusters']} markets; drift p90 "
        f"{out['drift_p90'] if p90 is not None else 'n/a'} on {len(drift)} snapshot reads; "
        f"{len(frozen)} frozen (venue/ledger disagree). NO ORDERS PLACED (P0)."
    )
    return out


def phase0_census(latest: list[dict], all_rows: list[dict]) -> dict:
    """The Phase 0 instruments, from the rows' JSONB detail. Pure."""
    latest_mapped = [r for r in latest if r.get("us_market_slug")]
    latest_unmapped = [r for r in latest if not r.get("us_market_slug")]
    out: dict[str, Any] = {}

    # ---- unmapped, by family and by his dollars in the window
    by_fam: dict[str, dict] = {}
    by_why: dict[str, dict] = {}
    usd = 0.0
    outcome_null = 0
    for r in latest_unmapped:
        d = _detail(r)
        fam = str(d.get("family") or "unknown")
        why = str(d.get("explain") or "unknown")
        u = _num(d.get("notional_6h")) or 0.0
        usd += u
        for table, key in ((by_fam, fam), (by_why, why)):
            cell = table.setdefault(key, {"markets": 0, "usd": 0.0})
            cell["markets"] += 1
            cell["usd"] = round(cell["usd"] + u, 2)
        if _num(d.get("outcome_null")):
            outcome_null += 1
    out["unmapped"] = {"markets": len(latest_unmapped), "usd": round(usd, 2),
                       "by_family": by_fam, "by_explain": by_why,
                       "outcome_null_markets": outcome_null,
                       "named": sum(1 for r in latest_unmapped if _detail(r).get("his_slug"))}

    # ---- mapped, by SOURCE and the ledger row's own class
    by_src: dict[str, dict] = {}
    ledger_classes: dict[str, int] = {}
    admissible = 0
    adm_usd = 0.0
    fam_cells: dict[str, dict] = {}
    for r in latest_mapped:
        d = _detail(r)
        src = str(d.get("map") or "unknown")
        gross = _num(d.get("his_gross_usd"))
        cell = by_src.setdefault(src, {"markets": 0, "usd": 0.0, "unmarked": 0})
        cell["markets"] += 1
        if gross is None:
            cell["unmarked"] += 1
        else:
            cell["usd"] = round(cell["usd"] + gross, 2)
        if src == "ledger":
            cls = str(d.get("map_class") or "unrecorded")
            ledger_classes[cls] = ledger_classes.get(cls, 0) + 1
        if src in ADMISSIBLE_SRC:
            admissible += 1
            adm_usd += gross or 0.0
        fam = str(d.get("family") or "unknown")
        fc = fam_cells.setdefault(fam, {"markets": 0, "neg": 0, "pos": 0, "usd": 0.0,
                                        "per_side": 0})
        fc["markets"] += 1
        net = _num(r.get("his_net"))
        if net is not None and net < 0:
            fc["neg"] += 1
        elif net is not None and net > 0:
            fc["pos"] += 1
        fc["usd"] = round(fc["usd"] + (gross or 0.0), 2)
        if d.get("per_side"):
            fc["per_side"] += 1
    out["mapped_by_source"] = {
        "markets": len(latest_mapped), "by_source": by_src, "ledger_classes": ledger_classes,
        "admissible": admissible, "admissible_usd": round(adm_usd, 2),
        "admissible_share": (round(admissible / len(latest_mapped), 4)
                             if latest_mapped else None),
        "admissible_src": sorted(ADMISSIBLE_SRC)}
    out["family"] = fam_cells

    # ---- his sign: negative-net share of markets, of |net| shares, of dollars
    neg_m = pos_m = flat_m = unmarked = 0
    neg_sh = pos_sh = neg_usd = pos_usd = 0.0
    for r in latest_mapped:
        net = _num(r.get("his_net"))
        mark = _num(r.get("mark"))
        if net is None or net == 0:
            flat_m += 1
            continue
        if net < 0:
            neg_m += 1
            neg_sh += -net
        else:
            pos_m += 1
            pos_sh += net
        if mark is None or not (0.0 < mark < 1.0):
            unmarked += 1
            continue
        # his dollars on the leg he actually holds: the long leg at the
        # mark, the other leg at one minus it
        if net < 0:
            neg_usd += -net * (1.0 - mark)
        else:
            pos_usd += net * mark
    tot_sh = neg_sh + pos_sh
    tot_usd = neg_usd + pos_usd
    out["sign"] = {"neg_markets": neg_m, "pos_markets": pos_m, "flat_markets": flat_m,
                   "neg_share_markets": (round(neg_m / (neg_m + pos_m), 4)
                                         if neg_m + pos_m else None),
                   "neg_sh": round(neg_sh, 2), "pos_sh": round(pos_sh, 2),
                   "neg_share_sh": round(neg_sh / tot_sh, 4) if tot_sh else None,
                   "neg_usd": round(neg_usd, 2), "pos_usd": round(pos_usd, 2),
                   "neg_share_usd": round(neg_usd / tot_usd, 4) if tot_usd else None,
                   "unmarked_markets": unmarked}

    # ---- would-fill: non-legacy vs legacy, each market-clustered
    mapped_all = [r for r in all_rows if r.get("us_market_slug")]
    plans = [r for r in mapped_all if r.get("would_side")]
    nonlegacy, legacy, unknown = [], [], 0
    for r in plans:
        v = is_legacy_plan(r)
        if v is None:
            unknown += 1
        elif v:
            legacy.append(r)
        else:
            nonlegacy.append(r)
    out["would_fill_nonlegacy"] = dict(rate_with_ci(nonlegacy), legacy_unknown=unknown)
    out["would_fill_legacy"] = rate_with_ci(legacy)

    # ---- the parallel short reading
    srows = []
    neg_target_markets = set()
    for r in mapped_all:
        d = _detail(r)
        if d.get("would_side_short"):
            srows.append({"condition_id": r.get("condition_id"),
                          "would_fill_short": d.get("would_fill_short"),
                          "legacy": is_legacy_plan(r, d, short=True)})
        t = _num(d.get("target_short"))
        if t is not None and t < 0:
            neg_target_markets.add(r.get("condition_id"))
    s_non = [r for r in srows if r["legacy"] is False]
    out["short"] = dict(rate_with_ci(srows, "would_fill_short"),
                        nonlegacy=rate_with_ci(s_non, "would_fill_short"),
                        neg_target_markets=len(neg_target_markets),
                        latest_neg_target=sum(
                            1 for r in latest_mapped
                            if (_num(_detail(r).get("target_short")) or 0) < 0))

    # ---- the snapshot census (RN1: fresh+partial on every probe)
    snap = {"markets": len(latest_mapped), "fresh_complete": 0, "fresh_partial": 0,
            "stale": 0, "none": 0, "token_na_markets": 0, "age_p50_s": None, "age_max_s": None,
            "fills_since_snap_p50": None}
    ages, since = [], []
    for r in latest_mapped:
        d = _detail(r)
        state = d.get("snap_state")
        if state is None:                       # rows written before Phase 0
            state = ("none" if d.get("snap_age_s") is None else
                     "stale" if d.get("snap_stale") else
                     "fresh_partial" if d.get("snap_partial") else "fresh_complete")
        snap[state] = snap.get(state, 0) + 1
        if state.startswith("fresh") and (r.get("snap_long") is None or r.get("snap_other") is None):
            snap["token_na_markets"] += 1
        a = _num(d.get("snap_age_s"))
        if a is not None:
            ages.append(a)
        f = _num(d.get("fills_since_snap"))
        if f is not None:
            since.append(f)
    ages.sort()
    since.sort()
    snap["age_p50_s"] = _p(ages, 0.5)
    snap["age_max_s"] = ages[-1] if ages else None
    snap["fills_since_snap_p50"] = _p(since, 0.5)
    snap["fresh_complete_share"] = (round(snap["fresh_complete"] / len(latest_mapped), 4)
                                    if latest_mapped else None)
    out["snapshot"] = snap

    # ---- the dead band: his moves the $5 / 2% band refuses, in dollars
    db_rows = 0
    db_usd = 0.0
    for r in latest_mapped:
        if str(r.get("reason") or "").endswith(DEAD_BAND_REASON):
            d = _detail(r)
            delta, mark = _num(d.get("delta")), _num(r.get("mark"))
            db_rows += 1
            if delta is not None and mark is not None:
                db_usd += abs(delta) * mark
    out["dead_band"] = {"markets": db_rows, "usd": round(db_usd, 2)}

    # ---- the touch: how long a judged plan waited, and the queue at the touch
    waits, depths = [], 0
    for r in mapped_all:
        d = _detail(r)
        t = _num(d.get("touched_s"))
        if t is not None:
            waits.append(t)
        if isinstance(d.get("touch_depth"), dict):
            depths += 1
    waits.sort()
    out["touch"] = {"n": len(waits), "touched_s_p50": _p(waits, 0.5),
                    "touched_s_p90": _p(waits, 0.9), "depth_n": depths}
    return out


def settle_would_pnl(rows: list[dict], short: bool = False) -> dict:
    """WOULD-P&L: the plans that would have filled, settled as lots at
    the venue's resolution -- pnl = (payout - would_px) x qty on a BUY,
    the sign flipped on a SELL, stake = the leg's cost -- ONE LOT PER
    (whale, market, side): the FIRST plan that would have filled, since
    every tick re-plans the same intent and a plan counted once per tick
    would settle the same lot twenty times. Legacy plans are dropped by
    the same filter the would-fill rate uses. Interval: proof.roi_with_ci
    clustered by game (the market's event, else the market)."""
    from .proof import roi_with_ci

    seen: set[tuple] = set()
    lots: list[dict] = []
    dropped_legacy = dropped_unsettled = 0
    for r in sorted(rows, key=lambda x: str(x.get("at") or "")):
        d = _detail(r)
        if short:
            side, px, qty, filled = (d.get("would_side_short"), _num(d.get("would_px_short")),
                                     _num(d.get("would_qty_short")), d.get("would_fill_short"))
        else:
            side, px, qty, filled = (r.get("would_side"), _num(r.get("would_px")),
                                     _num(r.get("would_qty")), r.get("would_fill"))
        if not filled or not side or px is None or not qty:
            continue
        payout = _num(r.get("payout"))
        if payout is None:
            dropped_unsettled += 1
            continue
        if is_legacy_plan(r, d, short=short) is not False:
            dropped_legacy += 1
            continue
        key = (r.get("whale"), r.get("condition_id"), side)
        if key in seen:
            continue
        seen.add(key)
        if side == "BUY_LONG":
            pnl, stake = (payout - px) * qty, px * qty
        else:
            pnl, stake = (px - payout) * qty, (1.0 - px) * qty
        lots.append({"stake": round(stake, 4), "pnl": round(pnl, 4),
                     "event_key": str(r.get("game_key") or r.get("condition_id") or "")})
    ci = roi_with_ci(lots)
    return {"lots": len(lots), "markets": len(seen), "staked": ci.get("staked"),
            "pnl": ci.get("pnl"), "roi": ci.get("roi"), "ci95": ci.get("ci95"),
            "clusters": ci.get("clusters"), "verdict": ci.get("verdict"),
            "dropped_legacy": dropped_legacy, "dropped_unsettled": dropped_unsettled}


async def would_pnl_rows(pool: Any, hours: float, whale: str | None) -> list[dict]:
    """The would-filled plans in the window whose market has resolved,
    with the long token's payout (market_tokens x resolved_prices, the
    settlement engine's own shape) and the game key for clustering."""
    args: list[Any] = [float(hours)]
    wf = ""
    if whale:
        args.append(whale.lower())
        wf = "AND s.whale = $2"
    rows = await pool.fetch(
        f"""
        SELECT s.whale, s.condition_id, s.at, s.would_side, s.would_px, s.would_qty,
               s.ledger_net, s.reason, s.detail::text AS detail,
               COALESCE(m.event_slug, s.condition_id) AS game_key,
               ((m.resolved_prices -> mt.outcome_index)::text)::float8 AS payout
          FROM mirror_shadow s
          JOIN market_tokens mt ON mt.token_id = s.long_asset
          JOIN markets m ON m.condition_id = mt.condition_id
         WHERE s.at >= now() - ($1::float8 * interval '1 hour') {wf}
           AND s.us_market_slug IS NOT NULL
           AND (s.would_fill OR (s.detail->>'would_fill_short') = 'true')
           AND m.resolved AND mt.outcome_index IS NOT NULL
           AND jsonb_array_length(m.resolved_prices) > mt.outcome_index
         ORDER BY s.at ASC /* would-pnl */
        """, *args)
    return [dict(r) for r in rows]


async def mirror_shadow_report(pool: Any, hours: float = 24.0,
                               whale: str | None = None) -> dict:
    args: list[Any] = [float(hours)]
    wf = ""
    if whale:
        args.append(whale.lower())
        wf = "AND whale = $2"
    # the resolved-market join is read first: it is the smaller read,
    # and a failure there is named inside the report, never allowed to
    # hide the window read the gates depend on
    pnl_rows: list[dict] | None = None
    pnl_error: str | None = None
    try:
        pnl_rows = await would_pnl_rows(pool, hours, whale)
    except Exception as exc:  # noqa: BLE001 — tables absent, or no resolution yet
        pnl_error = f"unavailable: {type(exc).__name__}"
    try:
        all_rows = [dict(r) for r in await pool.fetch(
            f"""
            SELECT at, whale, condition_id, us_market_slug, his_long, his_other, his_net,
                   snap_long, snap_other, ratio, target, capped, ledger_net, venue_net,
                   bid, ask, mark, his_last_px, would_side, would_qty, would_px,
                   would_fill, reason, detail::text AS detail
              FROM mirror_shadow
             WHERE at >= now() - ($1::float8 * interval '1 hour') {wf}
             ORDER BY at DESC
            """, *args)]
    except Exception as exc:  # noqa: BLE001 — table absent until 046
        return {"rows": 0, "error": f"unavailable: {type(exc).__name__}", "latest": []}
    latest: dict[tuple, dict] = {}
    for r in all_rows:                       # newest first
        key = (r.get("whale"), r.get("condition_id"))
        latest.setdefault(key, r)
    ratios: dict = {}
    try:
        raw = await pool.fetchval("SELECT value FROM ingestion_state WHERE key='mirror_ratio'")
        if raw:
            ratios = raw if isinstance(raw, dict) else json.loads(raw)
    except Exception:  # noqa: BLE001
        ratios = {}
    for r in all_rows:
        if hasattr(r.get("at"), "isoformat"):
            r["at"] = r["at"].isoformat()
    out = summarize(list(latest.values()), all_rows, ratios)
    out["hours"] = float(hours)
    out["whale"] = whale.lower() if whale else None
    if pnl_rows is None:
        out["would_pnl"] = {"error": pnl_error, "lots": 0}
        out["would_pnl_short"] = {"error": pnl_error, "lots": 0}
    else:
        for r in pnl_rows:
            if hasattr(r.get("at"), "isoformat"):
                r["at"] = r["at"].isoformat()
        out["would_pnl"] = settle_would_pnl(pnl_rows)
        out["would_pnl_short"] = settle_would_pnl(pnl_rows, short=True)
    out["frozen_detail"] = await frozen_detail(pool, list(latest.values()))
    return out


async def frozen_detail(pool: Any, latest: list[dict], limit: int = 5) -> list[dict]:
    """For each market the shadow froze (venue and ledger disagree), the
    live_orders rows on its slug, so the disagreement can be read for
    what it is: a sleeve the ledger read does not count, a row in a
    status it does not count, or a position the venue holds that no row
    explains (first shadow hour: Shelton v Hurkacz read ledger -604
    against venue +3,458). Best-effort; empty when the ledger is
    unreadable."""
    frozen = [r for r in latest
              if "frozen" in str(r.get("reason") or "") and r.get("us_market_slug")]
    if not frozen:
        return []
    try:
        from ..live_executor import ORDER_INTENT_SQL
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for r in frozen[:limit]:
        slug = str(r["us_market_slug"])
        try:
            rows = await pool.fetch(
                f"""
                SELECT id, status, lane, whale_username, filled_shares::float8 AS sh,
                       {ORDER_INTENT_SQL} AS intent, placed_at
                  FROM live_orders
                 WHERE us_market_slug = $1
                   AND status IN ('filled', 'exiting', 'settled', 'cashed_out', 'merged',
                                  'submitting', 'open')
                 ORDER BY placed_at DESC LIMIT 12
                """, slug)
        except Exception as exc:  # noqa: BLE001
            out.append({"slug": slug, "venue_net": r.get("venue_net"),
                        "ledger_net": r.get("ledger_net"), "error": type(exc).__name__})
            continue
        out.append({"slug": slug, "venue_net": r.get("venue_net"),
                    "ledger_net": r.get("ledger_net"),
                    "rows": [{"id": x["id"], "status": x["status"], "lane": x["lane"],
                              "whale": x["whale_username"], "sh": x["sh"],
                              "intent": (str(x["intent"]).replace("ORDER_INTENT_", "")
                                         if x["intent"] else None),
                              "placed_at": (x["placed_at"].isoformat()
                                            if hasattr(x["placed_at"], "isoformat")
                                            else x["placed_at"])}
                             for x in rows]})
    return out


# ------------------------------------------------------ coverage (MIRRORCOVER)

def derivative_candidates(his_slug: str | None) -> list[str]:
    """The tsc-/asc- slugs the exact derivative lane tries for a total
    or a spread, in its order (Phase 0 review, major 2: the candidate
    set carried none of them, so the runner tested the wrong slugs for
    every total and spread he traded). The grammar is the exact lane's
    own -- pmus.resolve_derivative_exact builds 'tsc-<base>-<line>' and
    the swapped-team form for a total, pmus._spread_exact the four asc-
    forms (his suffix verbatim, then without the team, without the
    side, and the bare line) for a spread -- restated here because that
    lane builds its list inline around a venue client and exposes no
    pure helper; a test pins these lists equal to the slugs the lane
    actually tries on a total, a spread and a swapped-order total. The
    feed parse is the lane's own (_feed_derivative), so a slug the lane
    would not parse yields nothing here either. A spread whose team
    token is not one of the base's two teams (a corners or segment
    handicap) is refused by the lane before any candidate is built and
    yields nothing here, for the same reason: it is a different market.
    The lane's text corroboration (his title's signed line against the
    venue's question) is a MAPPING question and does not decide whether
    a slug is listed, so it is not applied to the listing candidates."""
    try:
        from ..pmus import _feed_derivative
    except Exception:  # noqa: BLE001
        return []
    try:
        fd = _feed_derivative(str(his_slug or ""))
    except Exception:  # noqa: BLE001
        return []
    if not fd:
        return []
    base, line = str(fd.get("base") or ""), str(fd.get("line") or "")
    if not base or not line:
        return []
    if fd.get("kind") == "total":
        cands = [f"tsc-{base}-{line}"]
        bt = base.split("-")
        if len(bt) >= 4:
            swapped = "-".join([bt[0], bt[2], bt[1]] + bt[3:])
            cands.append(f"tsc-{swapped}-{line}")
        return list(dict.fromkeys(cands))
    if fd.get("kind") == "spread":
        team, side = fd.get("team"), fd.get("side")
        base_head = base.split("-")
        base_teams = set(base_head[1:3]) if len(base_head) >= 4 else set()
        if team and team not in base_teams:
            return []
        suffix_full = "-".join(p for p in (team, side, line) if p)
        cands = [f"asc-{base}-{suffix_full}"]
        if team:
            cands.append(f"asc-{base}-" + "-".join(p for p in (side, line) if p))
        if side:
            cands.append(f"asc-{base}-" + "-".join(p for p in (team, line) if p))
        if team and side:
            cands.append(f"asc-{base}-{line}")
        return list(dict.fromkeys(cands))
    return []


def candidate_slugs(title: str | None, his_slug: str | None, event_slug: str | None,
                    outcomes: list[str]) -> list[str]:
    """EVERY venue slug the mapping grammar would try for his market --
    the tennis abbreviations in both player orders and every tour code,
    the us-slug forms per outcome, the exact derivative lane's tsc-/asc-
    forms for a total or a spread, his own slug verbatim, and the aec-/
    atc- forms of his event slug. All of them, because the runner's
    listing test classes a market "not listed" only when every one is a
    404 (three of nine would misclass ITF men; every total and spread
    was tested on slugs the exact lane never tries). Order kept, no
    repeats."""
    try:
        from ..live_executor import _tennis_candidates, _us_slug_candidates
    except Exception:  # noqa: BLE001
        _tennis_candidates = _us_slug_candidates = None  # type: ignore[assignment]
    out: list[str] = []
    slug = str(his_slug or "").lower()
    if slug and _tennis_candidates is not None:
        try:
            out += _tennis_candidates(title, slug)
        except Exception:  # noqa: BLE001
            pass
    if slug and _us_slug_candidates is not None:
        for o in outcomes or [""]:
            try:
                out += _us_slug_candidates(slug, str(o or ""))
            except Exception:  # noqa: BLE001
                continue
    if slug:
        out += derivative_candidates(slug)
        out.append(slug)
    ev = str(event_slug or "").lower()
    if ev:
        out += [f"aec-{ev}", f"atc-{ev}"]
    seen: set[str] = set()
    uniq: list[str] = []
    for s in out:
        if s and s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


async def mirror_cover_report(pool: Any, whale: str = "rn1", hours: float = 24.0,
                              map_max: int = 150) -> dict:
    """His markets in the window with what the runner's MIRRORCOVER job
    needs to class each one against the venue board: the candidate
    slugs, his dollars (window BUY notional and gross shares at the
    shadow's mark), the shadow's current class and the current mapping
    source. Read-only, Postgres only: markets the shadow has not read are
    mapped here by the same table-only map_market, ranked by his dollars
    and bounded by `map_max` (one premap read per token)."""
    import time as _time

    from ..workers import mirror_shadow as ms
    from ..workers.premap import date_of

    now_ts = _time.time()
    out: dict[str, Any] = {"whale": whale, "hours": float(hours), "conditions": [],
                           "map_max": int(map_max), "map_calls": 0}
    try:
        conds = await ms.active_conditions(pool, whale, hours)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"unavailable: {type(exc).__name__}"
        return out
    try:
        out["null_condition_fills"] = int(await pool.fetchval(
            """
            SELECT count(*) FROM trades t JOIN whales w ON w.id = t.whale_id
             WHERE lower(w.username) = $1 AND t.condition_id IS NULL
               AND t.ts >= now() - ($2::float8 * interval '1 hour') /* cover-null-condition */
            """, whale, float(hours)) or 0)
    except Exception:  # noqa: BLE001
        out["null_condition_fills"] = None
    shadow: dict[str, dict] = {}
    try:
        for r in await pool.fetch(
                """
                SELECT DISTINCT ON (condition_id) condition_id, us_market_slug, reason,
                       mark, his_long, his_other, his_net, target, at, detail::text AS detail
                  FROM mirror_shadow
                 WHERE whale = $1 AND at >= now() - ($2::float8 * interval '1 hour')
                 ORDER BY condition_id, at DESC /* cover-shadow-latest */
                """, whale, float(hours)):
            shadow[str(r["condition_id"])] = dict(r)
    except Exception as exc:  # noqa: BLE001
        out["shadow_error"] = type(exc).__name__
    rows: list[dict] = []
    for cid in conds:
        try:
            fills = await ms.his_fills(pool, whale, cid)
        except Exception:  # noqa: BLE001
            continue
        if not fills:
            continue
        ctx = ms._first_context(fills)
        pos = ms.mi.net_positions(fills)
        outcomes = []
        for f in fills:
            o = str(f.get("outcome") or "")
            if o and o not in outcomes:
                outcomes.append(o)
        sh = sorted(pos.values(), reverse=True)
        s = shadow.get(cid)
        d = _detail(s) if s else {}
        row: dict[str, Any] = {
            "condition_id": cid, "his_slug": ctx["his_slug"], "event_slug": ctx["event_slug"],
            "title": ctx["title"], "event_title": ctx["event_title"], "outcomes": outcomes,
            "sport": ctx["sport"], "family": ms._family_of(ctx["his_slug"]),
            "date": date_of(ctx["his_slug"]) or None,
            "usd24h": ms.notional_in_window(fills, hours, now_ts), "n_fills": len(fills),
            "outcome_null": ms.outcome_null_count(fills),
            "gross_sh": round(sum(sh), 4), "paired_sh": round(sh[1], 4) if len(sh) > 1 else 0.0,
            "candidates": candidate_slugs(ctx["title"], ctx["his_slug"], ctx["event_slug"], outcomes),
            "shadow": None, "map": None, "explain": None,
        }
        if s:
            mark = _num(s.get("mark"))
            row["shadow"] = {
                "at": (s["at"].isoformat() if hasattr(s.get("at"), "isoformat") else s.get("at")),
                "us_slug": s.get("us_market_slug"), "reason": s.get("reason"), "mark": mark,
                "his_long": s.get("his_long"), "his_other": s.get("his_other"),
                "his_net": s.get("his_net"), "target": s.get("target"),
                "class": ("mapped" if s.get("us_market_slug") else "unmapped"),
                "source": d.get("map"), "map_class": d.get("map_class"),
                "explain": d.get("explain"), "gross_usd": d.get("his_gross_usd"),
                "per_side": d.get("per_side"), "ledger_legacy": d.get("ledger_legacy")}
            if s.get("us_market_slug"):
                row["map"] = {"source": d.get("map"), "us_slug": s.get("us_market_slug"),
                              "per_side": bool(d.get("per_side")), "map_class": d.get("map_class")}
            else:
                row["explain"] = d.get("explain")
        rows.append(row)
    # the markets the shadow has not classed: map them here, his biggest first
    rows.sort(key=lambda r: (-(r["usd24h"] or 0.0), r["condition_id"]))
    for row in rows:
        if row["map"] is not None or row["explain"] is not None:
            continue
        if out["map_calls"] >= int(map_max):
            row["explain"] = "unread:map_budget"
            continue
        out["map_calls"] += 1
        try:
            fills = await ms.his_fills(pool, whale, row["condition_id"])
            m = await ms.map_market(pool, fills)
        except Exception as exc:  # noqa: BLE001
            row["explain"] = f"map_raised:{type(exc).__name__}"
            continue
        if m:
            cls = m["source"]
            if m["source"] == "ledger":
                cls = ms.ledger_facts(await ms.ledger_rows(pool, m["us_slug"]))["map_class"]
            row["map"] = {"source": m["source"], "us_slug": m["us_slug"],
                          "per_side": bool(m.get("per_side")), "map_class": cls}
        else:
            row["explain"] = await ms.explain_unmapped(pool, ms._first_context(fills))
    out["conditions"] = rows
    out["markets"] = len(rows)
    out["usd24h"] = round(sum(r["usd24h"] or 0.0 for r in rows), 2)
    out["mapped"] = sum(1 for r in rows if r["map"])
    out["admissible"] = sum(1 for r in rows if r["map"] and r["map"]["source"] in ADMISSIBLE_SRC)
    out["admissible_src"] = sorted(ADMISSIBLE_SRC)
    return out


__all__ = ["summarize", "phase0_census", "mirror_shadow_report", "frozen_detail",
           "settle_would_pnl", "would_pnl_rows", "rate_with_ci", "is_legacy_plan",
           "candidate_slugs", "derivative_candidates", "mirror_cover_report", "ADMISSIBLE_SRC"]

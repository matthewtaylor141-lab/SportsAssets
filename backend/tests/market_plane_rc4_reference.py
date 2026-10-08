"""RC4 REFERENCE IMPLEMENTATIONS (release 7fd4574e == 9b94ef5c), VERBATIM.

The market plane's memory repair (RC5) rewrote populate, coverage_pass,
populate_kalshi, the run loop's assignment read and the certifier's refdata
read to hold one page at a time. These are the RC4 bodies, copied byte for
byte from `git show 9b94ef5c:backend/sportsassets/market_plane/populate.py`
and `.../workers/universal_market_plane.py` (only the module prefixes of
their helpers are spelled out), so tests/test_market_plane_memory_bound.py
can run the old and the new code on the same input and prove the outputs and
the writes identical. NOT a test module (no test_ prefix): loaded by path.
Never imported by production code.
"""
from __future__ import annotations

import json
import time

from sportsassets.market_plane import certification as CERT
from sportsassets.market_plane import populate as POP
from sportsassets.market_plane import registry as R
from sportsassets.market_plane import sharding as SH
from sportsassets.market_plane.populate import (
    AUTHORITY, CANDIDATE_REFUSAL_SQL, CATALOGUE_SQL, EVENT_SQL, KALSHI,
    O, P_CANDIDATE, P_HELD, PRICED_SQL, REST_BOOK_SQL, RULES_META_SQL,
    UPSERT_SQL, VALUATION_SQL, VALUATION_WINDOW_S, VENUE, ACTIVE_HORIZON_S,
    _epoch, _jsonish, _sha, apply_required, classify, contract_row,
    external_codes, kalshi_contract_row, league_of, required_sets_read)


async def populate(conn, *, since: float, now: float | None = None,
                   full: bool = False) -> dict:
    """Upsert every catalogue market changed since `since` (all of them when
    `full`), keep REQUIRED markets active, and (on a full pass) retire
    registry rows the catalogue no longer lists and nothing requires. Returns
    counts and the new watermark."""
    at = float(now if now is not None else time.time())
    held, cands, both_read = await required_sets_read(conn)
    rows = [dict(r) for r in await conn.fetch(
        CATALOGUE_SQL, 0.0 if full else float(since))]
    have = {r["contract_id"]: r["content_sha"] for r in await conn.fetch(
        "SELECT contract_id, content_sha FROM market_plane_registry")}
    out = {"read": len(rows), "upserted": 0, "changed": 0, "excluded": {},
           "required_added": 0, "retired": 0, "full": bool(full)}
    batch, events = [], []
    seen_slugs = set()
    watermark = float(since)
    for r in rows:
        watermark = max(watermark, _epoch(r.get("updated_at")) or watermark)
        c = contract_row(r, now=at, held=held, candidates=cands)
        if c is None:
            lg = league_of(r.get("event_slug"), r.get("team_league"))
            out["excluded"][lg] = out["excluded"].get(lg, 0) + 1
            continue
        seen_slugs.add(c["contract_id"])
        changed = have.get(c["contract_id"]) != c["content_sha"]
        out["changed"] += int(changed)
        batch.append((c["contract_id"], c["venue"], c["sport"],
                      c["competition"], c["event_id"], c["market_type"],
                      json.dumps(c["ontology"], default=str), c["active"],
                      c["desired_subscription"], at, c["priority"],
                      c["required_reason"], c["family"], c["period"],
                      c["event_start"], c["last_seen_at"], c["content_sha"],
                      AUTHORITY))
        if changed:
            events.append(("upsert:%s:%s" % (c["contract_id"],
                                             c["content_sha"][:16]),
                           c["contract_id"], "CONTRACT_UPSERT",
                           json.dumps({"priority": c["priority"],
                                       "reason": c["required_reason"],
                                       "family": c["family"],
                                       "sport": c["sport"]}), AUTHORITY))
    # REQUIRED but not (re)listed: kept active with the reason, never dropped
    missing_required = (held | cands) - seen_slugs - set(
        k for k in have if k in seen_slugs)
    for slug in sorted(missing_required):
        if slug in have and not full:
            continue
        reason = ("OPEN_PAPER_POSITION" if slug in held
                  else "EVALUATED_CANDIDATE")
        prio = P_HELD if slug in held else P_CANDIDATE
        content = {"required": reason}
        batch.append((slug, VENUE, None, None, None, None,
                      json.dumps({"gaps": ["NOT_IN_CURRENT_CATALOGUE"],
                                  "version": O.VERSION}), True, True, at,
                      prio, reason, None, None, None, at, _sha(content),
                      AUTHORITY))
        out["required_added"] += 1
    async with conn.transaction():
        for i in range(0, len(batch), 1000):
            await conn.executemany(UPSERT_SQL, batch[i:i + 1000])
        for i in range(0, len(events), 1000):
            await conn.executemany(EVENT_SQL, events[i:i + 1000])
        out["required_applied"] = await apply_required(
            conn, held, cands, at=at, both_read=both_read)
        if full:
            # retire: not listed within the horizon and not required
            tag = await conn.execute(
                "UPDATE market_plane_registry SET active = false, "
                "       updated_at = to_timestamp($1) "
                " WHERE active AND last_seen_at < to_timestamp($2) "
                "   AND NOT (contract_id = ANY($3::text[]))",
                at, at - ACTIVE_HORIZON_S, sorted(held | cands))
            try:
                out["retired"] = int(str(tag).split()[-1])
            except (ValueError, IndexError):
                out["retired"] = 0
    out["upserted"] = len(batch)
    out["watermark"] = watermark
    out["required"] = {"held": len(held), "candidates": len(cands)}
    return out



async def _fetch_chunked(conn, sql, ids, *args, key="slug"):
    out = {}
    for i in range(0, len(ids), 5000):
        chunk = ids[i:i + 5000]
        try:
            for r in await conn.fetch(sql, *args, chunk):
                out[r[key]] = dict(r)
        except Exception:                                       # noqa: BLE001
            pass
    return out


#: the bounded venue x sport x league x family settlement breakdown
BREAKDOWN_MAX_KEYS = 200


async def coverage_pass(conn, *, fresh_symbols=frozenset(), now=None,
                        rest_sla_s: float = 300.0, limit: int | None = None
                        ) -> dict:
    """Classify every ACTIVE registry contract and write the changed terminal
    states AND settlement states (market_plane.settlement, evidence only).
    Returns the matrix summary (counts by state, sport, family, why), the
    settlement-state counts and breakdown, and this pass's delta: how many
    contracts moved from MAPPED_BUT_SETTLEMENT_NOT_PROVEN to a PROVEN
    settlement state (and back)."""
    from sportsassets.market_plane.coverage import matrix
    from sportsassets.market_plane import settlement as S
    at = float(now if now is not None else time.time())
    rows = [dict(r) for r in await conn.fetch(
        "SELECT contract_id, venue, sport, competition, event_id, family, "
        "       period, ontology, coverage_state, coverage_why, priority, "
        "       settlement_state, settlement_why, settlement_basis, "
        "       settlement_evidence->>'rules_sha256' AS settlement_rules_sha "
        "  FROM market_plane_registry WHERE active "
        " ORDER BY priority, contract_id" + (" LIMIT %d" % int(limit)
                                             if limit else ""))]
    slugs = [r["contract_id"] for r in rows]
    vals = await _fetch_chunked(conn, VALUATION_SQL, slugs,
                                float(VALUATION_WINDOW_S))
    cands = await _fetch_chunked(conn, CANDIDATE_REFUSAL_SQL, slugs,
                                 float(VALUATION_WINDOW_S))
    rest = {k: _epoch(v["observed_at"]) for k, v in (await _fetch_chunked(
        conn, REST_BOOK_SQL, slugs, float(rest_sla_s))).items()}
    priced = {k: {"eligibility": _jsonish(v.get("eligibility")) or {},
                  "policy": _jsonish(v.get("policy")) or {}}
              for k, v in (await _fetch_chunked(
                  conn, PRICED_SQL, slugs, float(VALUATION_WINDOW_S))).items()}
    rules_ok = True
    try:
        await conn.fetchval("SELECT 1 FROM market_plane_rules LIMIT 1")
    except Exception:                                           # noqa: BLE001
        rules_ok = False
    rules = (await _fetch_chunked(conn, RULES_META_SQL, slugs,
                                  key="contract_id")) if rules_ok else {}
    for r in rules.values():
        r["evidence"] = _jsonish(r.get("evidence")) or {}
    # THE TEXT, ONLY WHERE A TERMS COMPARISON IS STILL TO BE MADE: a never-
    # attested full-event winner whose (fingerprint, family, league) is not
    # in this process's comparison cache
    need = []
    for r in rows:
        s = r["contract_id"]
        rr = rules.get(s)
        v = vals.get(s) or {}
        attested = bool(v.get("settlement_verdict")) or any(
            str(x).startswith("SETTLEMENT") for x in (v.get("refusals") or []))
        fam = S.h2h_family(r)
        if rr is None or attested or not rr.get("rules_published") or \
                rr.get("venue") != VENUE or fam is None:
            continue
        if (rr.get("rules_sha256"), fam, r.get("competition")) in \
                S._TERMS_CACHE:
            rr["rules_text"] = ""          # cached: the text is not re-read
        else:
            need.append(s)
    texts = await _fetch_chunked(
        conn, "SELECT contract_id, rules_text FROM market_plane_rules "
              " WHERE contract_id = ANY($1::text[])", need,
        key="contract_id") if need else {}
    for s, t in texts.items():
        rules[s]["rules_text"] = t.get("rules_text")
    ext = external_codes()
    results, changed, schanged = [], [], []
    delta = {"to_proven": 0, "from_proven": 0, "to_conflict": 0,
             "to_external": 0}
    src_counts = {"PMX_GRPC": 0, "RETAIL_PUSH": 0, "REST_RECOVERY": 0,
                  "NONE": 0}
    # THE TWO DENOMINATORS (owner, 2026-10-06): the PRIORITY universe (open
    # positions + evaluated candidates: the capital-required markets) and
    # the ENTIRE active universe are reported apart, never blended
    tiers = {"PRIORITY": {"PMX_GRPC": 0, "REST_RECOVERY": 0, "NONE": 0,
                          "EXTERNAL_DATA_UNAVAILABLE": 0, "total": 0},
             "ALL": {"PMX_GRPC": 0, "REST_RECOVERY": 0, "NONE": 0,
                     "EXTERNAL_DATA_UNAVAILABLE": 0, "total": 0}}
    for r in rows:
        s = r["contract_id"]
        if s in fresh_symbols:
            src, fresh = "PMX_GRPC", True
        elif s in rest:
            src, fresh = "REST_RECOVERY", True
        else:
            src, fresh = None, False
        src_counts[src or "NONE"] += 1
        st = S.state_for(r, valuation=vals.get(s), rules=rules.get(s),
                         priced=priced.get(s), rules_looked_up=rules_ok)
        t = classify(r, valuation=vals.get(s), candidate=cands.get(s),
                     fresh_book=fresh, book_source=src, external_codes=ext,
                     settlement=st)
        t["venue"] = r.get("venue")
        results.append(t)
        for tier in (("PRIORITY", "ALL") if (r.get("priority") is not None
                                            and int(r["priority"])
                                            <= P_CANDIDATE) else ("ALL",)):
            tiers[tier]["total"] += 1
            if t["state"] == "EXTERNAL_DATA_UNAVAILABLE":
                tiers[tier]["EXTERNAL_DATA_UNAVAILABLE"] += 1
            tiers[tier][src or "NONE"] += 1
        if (t["state"], t["why"]) != (r.get("coverage_state"),
                                     r.get("coverage_why")):
            changed.append((s, t["state"], t["why"], at))
        sha = st["evidence"].get("rules_sha256")
        if (st["state"], st["why"], st["basis"], sha) != (
                r.get("settlement_state"), r.get("settlement_why"),
                r.get("settlement_basis"), r.get("settlement_rules_sha")):
            schanged.append((s, st["state"], st["why"], st["basis"],
                             json.dumps(st["evidence"], default=str), at))
            prior_np = (r.get("settlement_state") == S.NOT_PROVEN or (
                r.get("settlement_state") is None and r.get("coverage_state")
                == "MAPPED_BUT_SETTLEMENT_NOT_PROVEN"))
            if st["proven"] and prior_np:
                delta["to_proven"] += 1
            if r.get("settlement_state") in S.PROVEN_STATES and \
                    not st["proven"]:
                delta["from_proven"] += 1
            if st["state"] == S.CONFLICT and \
                    r.get("settlement_state") != S.CONFLICT:
                delta["to_conflict"] += 1
            if st["state"] == S.EXTERNAL and \
                    r.get("settlement_state") != S.EXTERNAL:
                delta["to_external"] += 1
    if changed or schanged:
        async with conn.transaction():
            for i in range(0, len(changed), 1000):
                await conn.executemany(
                    "UPDATE market_plane_registry SET coverage_state = $2, "
                    "       coverage_why = $3, coverage_at = to_timestamp($4) "
                    " WHERE contract_id = $1", changed[i:i + 1000])
            for i in range(0, len(schanged), 1000):
                await conn.executemany(
                    "UPDATE market_plane_registry SET settlement_state = $2, "
                    "       settlement_why = $3, settlement_basis = $4, "
                    "       settlement_evidence = $5::jsonb, "
                    "       settlement_at = to_timestamp($6) "
                    " WHERE contract_id = $1", schanged[i:i + 1000])
    m = matrix([{"contract_id": t["contract_id"], **t["evidence"]}
                for t in results])
    by_sport, by_why, by_venue = {}, {}, {}
    s_by_state = {k: 0 for k in S.STATES}
    s_by_basis, s_by_why, s_by_venue, brk = {}, {}, {}, {}
    for t in results:
        k = t.get("sport") or "UNKNOWN"
        by_sport.setdefault(k, {}).setdefault(t["state"], 0)
        by_sport[k][t["state"]] += 1
        vn = t.get("venue") or "UNKNOWN"
        by_venue.setdefault(vn, {}).setdefault(t["state"], 0)
        by_venue[vn][t["state"]] += 1
        w = "%s:%s" % (t["state"], t["why"])
        by_why[w] = by_why.get(w, 0) + 1
        st = t["settlement"]
        s_by_state[st["state"]] += 1
        s_by_basis[st["basis"]] = s_by_basis.get(st["basis"], 0) + 1
        sw = "%s:%s" % (st["state"], (st["why"] or "")[:120])
        s_by_why[sw] = s_by_why.get(sw, 0) + 1
        s_by_venue.setdefault(vn, {}).setdefault(st["state"], 0)
        s_by_venue[vn][st["state"]] += 1
        bk = "%s|%s|%s|%s" % (vn, k, t.get("competition") or "UNKNOWN",
                              t.get("family") or "UNKNOWN")
        brk.setdefault(bk, {}).setdefault(st["state"], 0)
        brk[bk][st["state"]] += 1
    top = sorted(brk.items(), key=lambda kv: -sum(kv[1].values()))
    m.pop("rows", None)
    return dict(m, by_sport=by_sport, by_venue=by_venue,
                top_reasons=dict(sorted(by_why.items(),
                                        key=lambda kv: -kv[1])[:40]),
                source_counts=src_counts, freshness_tiers=tiers,
                changed=len(changed),
                active=len(rows), computed_at=at,
                settlement={
                    "by_state": s_by_state, "by_basis": s_by_basis,
                    "by_venue": s_by_venue,
                    "top_reasons": dict(sorted(s_by_why.items(),
                                               key=lambda kv: -kv[1])[:40]),
                    "breakdown_venue_sport_league_family": dict(
                        top[:BREAKDOWN_MAX_KEYS]),
                    "breakdown_keys_total": len(brk),
                    "breakdown_truncated": len(brk) > BREAKDOWN_MAX_KEYS,
                    "changed": len(schanged), "delta": delta,
                    "rules_table_read": rules_ok,
                    "terms_text_loaded": len(texts),
                    "authority_note": S.AUTHORITY_NOTE})


async def populate_kalshi(conn, result: dict, *, now: float | None = None
                          ) -> dict:
    """Upsert every market a kalshi_catalogue walk returned into the
    registry (CONTRACT_UPSERT only when content changed) and its rules into
    market_plane_rules. A TRUNCATED walk writes what it read and retires
    nothing (rows not re-seen age out by ACTIVE_HORIZON_S like any
    listing). No authority."""
    from sportsassets.market_plane import rules as RULES
    at = float(now if now is not None else time.time())
    ms = [m for m in (result or {}).get("markets") or []
          if isinstance(m, dict) and m.get("ticker")]
    have = {r["contract_id"]: r["content_sha"] for r in await conn.fetch(
        "SELECT contract_id, content_sha FROM market_plane_registry "
        " WHERE venue = $1", KALSHI)}
    batch, events = [], []
    for m in ms:
        c = kalshi_contract_row(m, now=at)
        if c is None:
            continue
        ch = have.get(c["contract_id"]) != c["content_sha"]
        batch.append((c["contract_id"], c["venue"], c["sport"],
                      c["competition"], c["event_id"], c["market_type"],
                      json.dumps(c["ontology"], default=str), c["active"],
                      c["desired_subscription"], at, c["priority"],
                      c["required_reason"], c["family"], c["period"],
                      c["event_start"], c["last_seen_at"], c["content_sha"],
                      AUTHORITY))
        if ch:
            events.append(("upsert:%s:%s" % (c["contract_id"],
                                             c["content_sha"][:16]),
                           c["contract_id"], "CONTRACT_UPSERT",
                           json.dumps({"venue": KALSHI,
                                       "series": c["competition"]}),
                           AUTHORITY))
    async with conn.transaction():
        for i in range(0, len(batch), 1000):
            await conn.executemany(UPSERT_SQL, batch[i:i + 1000])
        for i in range(0, len(events), 1000):
            await conn.executemany(EVENT_SQL, events[i:i + 1000])
    rr = await RULES.upsert(conn, [RULES.kalshi_row(m) for m in ms], now=at)
    return {"markets": len(ms), "upserted": len(batch),
            "changed": len(events), "rules": rr,
            "complete": bool((result or {}).get("complete")),
            "stopped": (result or {}).get("stopped")}


# ── workers/universal_market_plane.py (RC4) ──────────────────────────

async def certify(conn, mgr) -> dict:
    """DURABLE SAME-BOOK CERTIFICATION: the strict window evidence (30
    comparable samples at >= 95% agreement, same venue instant, exact
    identity -- unchanged) for each subscribed contract, persisted keyed by
    its identity fingerprint. A contradiction or a fall below the rule
    overwrites the certificate (not SUPPORTED); a changed fingerprint never
    reuses it."""
    from sportsassets import institutional_same_book as SB
    out = {"evaluated": 0, "supported": 0, "accumulating": 0,
           "contradicted": 0}
    rows = await conn.fetch(
        "SELECT contract_id, refdata FROM market_plane_registry "
        " WHERE venue='POLYMARKET_US' AND active AND refdata IS NOT NULL "
        "   AND coalesce(refdata->>'unlisted','false') <> 'true' "
        "   AND subscription_shard IS NOT NULL")
    recs = {}
    for r in rows:
        rd = POP._jsonish(r["refdata"]) or {}
        recs[r["contract_id"]] = rd
    syms = sorted(recs)
    for i in range(0, len(syms), 500):
        chunk = syms[i:i + 500]
        ev = await SB.same_book_by_symbol(conn, chunk)
        for s in chunk:
            e = ev.get(s) or {"status": "UNTESTED", "detail": {}}
            det = e.get("detail") or {}
            rd = recs[s]
            ident = CERT.identity_for(
                s, price_scale=rd.get("priceScale") or rd.get("price_scale"),
                qty_scale=rd.get("fractionalQtyScale") or rd.get("qty_scale"))
            comparable = int(det.get("comparable") or 0)
            agree = int(det.get("agree_top_n") or 0) + int(
                det.get("agree_touch_only") or 0)
            cert = CERT.verdict(comparable=comparable, agreeing=agree,
                                identity=ident)
            supported = e.get("status") == "SUPPORTED" and \
                cert["status"] == "SUPPORTED"
            cert["status"] = "SUPPORTED" if supported else "ACCUMULATING"
            cert["window_status"] = e.get("status")
            cert["identity"] = ident
            await R.save_certification(conn, s, cert)
            out["evaluated"] += 1
            out["supported"] += int(supported)
            out["accumulating"] += int(not supported)
            out["contradicted"] += int(e.get("status") == "CONTRADICTED")
    return out


async def run_loop_assignment_sync(conn, mgr) -> dict:
    """RC4 run() lines 476-485: the assignment pass's read and the
    manager's sync, as the loop ran them (assigned_rows, assignments and
    instruments were loop locals)."""
    assigned_rows = await R.assigned_contracts(conn)
    assignments = {r["contract_id"]: r["subscription_shard"]
                   for r in assigned_rows}
    instruments = {r["contract_id"]: POP._jsonish(
        r["refdata"]) for r in assigned_rows}
    return mgr.sync(assignments, instruments)


# ── market_plane/sharding.py (RC4) ───────────────────────────────────

def assign_stable(symbols, existing=None, *, max_per_stream=SH.DEFAULT_MAX_PER_STREAM,
                  max_streams=SH.DEFAULT_MAX_STREAMS, order=None) -> dict:
    """Preserve valid existing assignments; place only new symbols.

    No reshuffle on membership additions. That prevents healthy streams from
    reconnecting merely because the universe changed.
    """
    syms=sorted({str(s).strip() for s in symbols or () if str(s).strip()})
    existing={str(k):int(v) for k,v in (existing or {}).items()
              if str(k) in syms and 0 <= int(v) < int(max_streams)}
    loads={i:0 for i in range(int(max_streams))}; assigned={}
    # keep existing only while its shard still has room
    for s in syms:
        if s in existing and loads[existing[s]] < max_per_stream:
            assigned[s]=existing[s]; loads[existing[s]]+=1
    overflow=[]
    # (integration) NEW symbols are placed in the caller's priority order
    # (held positions, candidates, core families first), so when capacity
    # runs out the overflow is the LOWEST-priority tail -- named, never the
    # alphabetical tail. Existing assignments are never moved.
    rank={str(x):i for i,x in enumerate(order or ())}
    placing=sorted(syms,key=lambda x:(rank.get(x,len(rank)),x)) if order else syms
    for s in placing:
        if s in assigned: continue
        candidates=[i for i in range(int(max_streams)) if loads[i] < max_per_stream]
        if not candidates:
            overflow.append(s); continue
        i=min(candidates,key=lambda x:(loads[x],x))
        assigned[s]=i; loads[i]+=1
    shards=[]
    for i in range(int(max_streams)):
        members=sorted(s for s,v in assigned.items() if v==i)
        if members: shards.append({"shard":i,"symbols":members,"count":len(members)})
    return {"assignments":assigned,"shards":shards,"symbols":len(assigned),
            "capacity":int(max_per_stream)*int(max_streams),"overflow":overflow,
            "complete":not overflow,"version":SH.VERSION}

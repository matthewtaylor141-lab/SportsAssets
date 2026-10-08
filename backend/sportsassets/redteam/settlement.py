"""SETTLEMENT CERTIFICATE INVALIDATION (red_team.settlement_guard, bound to
the canonical claim layer).

Every canonical alias is CERTIFIED against the settlement-rules fingerprint
it was built from (market_plane_rules.rules_sha256 of its contract). A later
pass reading a DIFFERENT fingerprint appends INVALIDATED
(RULES_CHANGED_SINCE_CERTIFICATION): the alias loses eligibility at once --
no claim class, no route, no arbitrage leg, no new entry -- and is
re-certified only by a later full re-mapping of the NEW rules (the next
pass rebuilds the payoff vector from the re-parsed rules end to end; the
certificate is re-issued only if that succeeds). Unknown payoff states and
a missing fingerprint fail closed through the package gate.

  certify      the Kalshi market-data worker's pass: decide and APPEND
               (red_team_settlement_certificates, append-only)
  apply        every reader (the worker's routing, Adriana's claim scan):
               the latest certificate, read only -- an alias whose current
               fingerprint is not the certified one is refused
"""
from __future__ import annotations

import hashlib

from ..red_team import settlement_guard as SG

R_CHANGED = "RULES_CHANGED_SINCE_CERTIFICATION"
CERTIFIED, INVALIDATED = "CERTIFIED", "INVALIDATED"


def alias_key(i) -> str:
    return "%s|%s|%s" % (i.venue, i.market_id, i.side)


def decide(i, latest: dict | None) -> dict:
    """One alias against its latest certificate -> {action, eligible,
    blockers}. action: CERTIFY (append CERTIFIED), INVALIDATE (append
    INVALIDATED), KEEP, RECERTIFY (append CERTIFIED after an invalidation),
    REFUSE (no append; the gate's own blockers)."""
    unknown = [s for s, t in (i.vector or {}).items() if t is None]
    cur = i.rules_sha256
    if latest is None or latest.get("status") == INVALIDATED:
        g = SG.settlement_certificate_gate(
            decision_rules_fingerprint=cur, current_rules_fingerprint=cur,
            outcome_space_exhaustive=True, unknown_states=unknown,
            unequal_states=[], source_agreement=True)
        if not g["green"]:
            return {"action": "REFUSE", "eligible": False,
                    "blockers": list(g["blockers"])}
        return {"action": "CERTIFY" if latest is None else "RECERTIFY",
                "eligible": True, "blockers": []}
    g = SG.settlement_certificate_gate(
        decision_rules_fingerprint=latest.get("rules_sha256"),
        current_rules_fingerprint=cur, outcome_space_exhaustive=True,
        unknown_states=unknown, unequal_states=[], source_agreement=True)
    if R_CHANGED in g["blockers"]:
        return {"action": "INVALIDATE", "eligible": False,
                "blockers": list(g["blockers"])}
    return {"action": "KEEP" if g["green"] else "REFUSE",
            "eligible": g["green"], "blockers": list(g["blockers"])}


async def latest_certificates(conn, keys: list) -> dict:
    if not keys or not await conn.fetchval(
            "SELECT to_regclass('red_team_settlement_certificates') "
            "IS NOT NULL"):
        return {}
    return {r["alias_key"]: dict(r) for r in await conn.fetch(
        "SELECT DISTINCT ON (alias_key) alias_key, status, rules_sha256, "
        "       claim_fingerprint, at FROM red_team_settlement_certificates "
        " WHERE alias_key = ANY($1::text[]) ORDER BY alias_key, at DESC",
        sorted(set(keys)))}


def _strip(built: dict, refuse: dict) -> dict:
    """Remove refused aliases from their claim classes (they keep their
    reasons on the instrument and land in `refused`)."""
    if not refuse:
        return built
    classes = {}
    refused = list(built.get("refused") or [])
    for fp, members in built["classes"].items():
        keep = []
        for i in members:
            why = refuse.get(alias_key(i))
            if why:
                i.refusals = list(i.refusals or []) + why
                i.fingerprint = None
                refused.append(i)
            else:
                keep.append(i)
        if keep:
            classes[fp] = keep
    return dict(built, classes=classes, refused=refused)


async def apply(conn, built: dict) -> tuple:
    """READ ONLY: (built with every alias whose certificate does not hold
    removed, {alias_key: decision})."""
    members = [i for c in built["classes"].values() for i in c]
    latest = await latest_certificates(conn, [alias_key(i) for i in members])
    out, refuse = {}, {}
    for i in members:
        d = decide(i, latest.get(alias_key(i)))
        out[alias_key(i)] = d
        if d["action"] in ("INVALIDATE", "REFUSE"):
            refuse[alias_key(i)] = d["blockers"] or [R_CHANGED]
    return _strip(built, refuse), out


async def certify(conn, built: dict, *, now: float) -> dict:
    """The worker's pass: decide every alias, APPEND what changed, return
    the stripped claims and counts."""
    members = [i for c in built["classes"].values() for i in c]
    latest = await latest_certificates(conn, [alias_key(i) for i in members])
    counts: dict = {}
    refuse = {}
    has = await conn.fetchval(
        "SELECT to_regclass('red_team_settlement_certificates') IS NOT NULL")
    for i in members:
        k = alias_key(i)
        d = decide(i, latest.get(k))
        counts[d["action"]] = counts.get(d["action"], 0) + 1
        if d["action"] in ("INVALIDATE", "REFUSE"):
            refuse[k] = d["blockers"] or [R_CHANGED]
        if not has or d["action"] in ("KEEP", "REFUSE"):
            continue
        status = INVALIDATED if d["action"] == "INVALIDATE" else CERTIFIED
        cid = "cert:%s" % hashlib.sha256(("%s|%s|%s|%s" % (
            k, status, i.rules_sha256, int(now * 1000))).encode()
        ).hexdigest()[:32]
        await conn.execute(
            "INSERT INTO red_team_settlement_certificates (certificate_id, "
            " alias_key, venue, market_id, claim_fingerprint, rules_sha256, "
            " status, reason, at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8, "
            " to_timestamp($9)) ON CONFLICT (certificate_id) DO NOTHING",
            cid, k, i.venue, i.market_id, i.fingerprint,
            i.rules_sha256 if status == CERTIFIED else (
                (latest.get(k) or {}).get("rules_sha256")),
            status, None if status == CERTIFIED else ",".join(
                d["blockers"]) + " now=%s" % i.rules_sha256, now)
    return _strip(built, refuse), counts

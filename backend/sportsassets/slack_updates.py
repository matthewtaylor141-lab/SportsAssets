"""PROACTIVE MANAGEMENT UPDATES for the verified management Slack channel.

Produced from the account's own records, queued as READY deliveries in the
existing Slack bridge (agent_slack_delivery), so sending, leases, retries and
the OFF switch are the bridge's. Nothing here changes a position, a policy or
a control.

  * INITIAL briefing once per connection (source key update:initial:<rev>)
  * HOURLY summary, at most one per UTC hour (update:hourly:<YYYY-MM-DDTHH>)
  * DAILY performance and learning report after 22:00 UTC
    (update:daily:<YYYY-MM-DD>)
  * ALERTS for a service failure, a reconciliation discrepancy or a research
    blocker. An alert's source key is its condition fingerprint plus a 6-hour
    bucket, so an unchanged condition is not re-posted within 6 h, and an
    hourly summary consolidates everything routine.

Figures are separate and labelled: simulated cash, reserved cash, exposure
(open cost basis), realized P&L and unrealized P&L, with the ledger sequence
and timestamp they come from; training strategies are reported apart from
investment and benchmark strategies. Discussion is never labelled learning or
improved profitability: research items are reported by their recorded stage.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone

ACCOUNT = "paper_acct_main"
STATE_KEY = "agent.slack.updates"
CC = "https://command.bettortoken.com"
AGENT = "audrey"                 # updates speak as the auditor
ALERT_BUCKET_S = 6 * 3600
DAILY_AFTER_H = 22
STALE_CYCLE_S = 900.0
STALE_PASS_S = 600.0
TRAINING_KINDS = {"TRAINING"}


def _utc(at: float) -> datetime:
    return datetime.fromtimestamp(at, timezone.utc)


def _usd(v) -> str:
    try:
        return "${:,.2f}".format(float(v))
    except (TypeError, ValueError):
        return "not stated"


def _fp(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True,
                                     default=str).encode()).hexdigest()[:16]


async def _row(conn, key):
    v = await conn.fetchval("SELECT value FROM ingestion_state WHERE key=$1",
                            key)
    return json.loads(v) if isinstance(v, str) else (v or {})


async def snapshot(conn, now: float) -> dict:
    """One read of everything an update reports, from the same functions the
    pages use (account_section, pnl_by_strategy, paper_brief.reconcile)."""
    from . import bettor_paper_ops as OPS
    from .agents import paper_brief as PB
    a = await OPS.audrey_operations(conn, account_id=ACCOUNT, now=now)
    acct = (a.get("account") or {}).get("data") or {}
    pnl = (a.get("pnl_by_strategy") or {}).get("data") or []
    try:
        rc = await PB.reconcile(conn, now=now, account_id=ACCOUNT, entries=0)
    except Exception as exc:                                    # noqa: BLE001
        rc = {"reconciled": None, "error": type(exc).__name__}
    research = [dict(r) for r in await conn.fetch(
        "SELECT status, count(*) AS n, max(updated_at) AS latest "
        "  FROM agent_tasks WHERE kind=$2 "
        "   AND spec->>'account_id'=$1 GROUP BY 1", ACCOUNT, _kind())] \
        if await conn.fetchval("SELECT to_regclass('agent_tasks') IS NOT NULL") \
        else []
    cyc = await _row(conn, "ext_pinnacle_last_cycle")
    ps = await _row(conn, "paper_session_last_pass")
    return {"at": now, "account": acct, "pnl": pnl, "reconcile": rc,
            "research": research, "cycle_at": cyc.get("at"),
            "pass_at": ps.get("at")}


def _kind() -> str:
    from .agents import capability_work as W
    return W.KIND


def _pnl_lines(pnl: list) -> list:
    groups = {"Training": [], "Investment / benchmark": []}
    for r in pnl:
        g = "Training" if r.get("kind") in TRAINING_KINDS else \
            "Investment / benchmark"
        groups[g].append(r)
    out = []
    for g, rows in groups.items():
        if not rows:
            continue
        real = sum(float(r.get("realized_pnl_usd") or 0) for r in rows)
        unk = any(r.get("unrealized_pnl_usd") is None for r in rows)
        unr = sum(float(r.get("unrealized_marked_only_usd") or 0)
                  for r in rows)
        expo = sum(float(r.get("open_cost_basis_usd") or 0) for r in rows)
        n = sum(int(r.get("open_positions") or 0) for r in rows)
        out.append("%s: realized %s · unrealized %s · exposure %s · %d open"
                   % (g, _usd(real), ("%s (marked only; some unmarked)"
                                      % _usd(unr)) if unk else _usd(unr),
                      _usd(expo), n))
    return out


def account_block(s: dict) -> str:
    a = s["account"]
    rc = s["reconcile"]
    stamp = _utc(s["at"]).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "Simulated cash %s · reserved %s · available %s" % (
            _usd(a.get("cash_usd")), _usd(a.get("reserved_usd")),
            _usd(a.get("available_usd"))),
        "Realized P&L %s · unrealized P&L %s · %s open positions" % (
            _usd(a.get("realized_pnl_usd")),
            _usd(a.get("unrealized_pnl_usd")) if a.get("marks_complete")
            else _usd(a.get("unrealized_pnl_marked_only_usd"))
            + " (marked only)", a.get("open_positions")),
    ] + _pnl_lines(s["pnl"]) + [
        "Ledger %s · seq %s · %s" % (
            "reconciled" if rc.get("reconciled") else
            ("NOT reconciled" if rc.get("reconciled") is False
             else "reconciliation unavailable"),
            a.get("last_sequence"), stamp),
        "Records: %s/audrey · simulated paper account; no real money." % CC]
    return "\n".join(lines)


def research_line(s: dict) -> str:
    by = {r["status"]: int(r["n"]) for r in s["research"]}
    if not by:
        return "Research: no recorded research tasks."
    return "Research tasks by recorded status: " + ", ".join(
        "%s %d" % (k, v) for k, v in sorted(by.items())) + \
        ". Stages are reported as recorded; none is an activated change."


def alerts(s: dict, now: float) -> list:
    out = []
    rc = s["reconcile"]
    if rc.get("reconciled") is False:
        failed = sorted(c.get("check") or c.get("name") or "?"
                        for c in rc.get("failed_checks") or [])
        out.append(("reconciliation", _fp(failed),
                    "ALERT · ledger reconciliation discrepancy: "
                    + (", ".join(failed) or "unnamed check")))
    for key, at, limit, what in (("cycle", s.get("cycle_at"), STALE_CYCLE_S,
                                  "entry-lane collection cycle"),
                                 ("pass", s.get("pass_at"), STALE_PASS_S,
                                  "paper agent pass")):
        try:
            age = now - float(at)
        except (TypeError, ValueError):
            age = None
        if age is None or age > limit:
            out.append(("service:" + key, _fp(key),
                        "ALERT · %s has not completed for %s" % (
                            what, "an unknown time" if age is None
                            else "%d min" % (age // 60))))
    blocked = sum(int(r["n"]) for r in s["research"]
                  if r["status"] in ("REJECTED", "BLOCKED"))
    if blocked:
        out.append(("research", _fp(blocked),
                    "ALERT · %d research task(s) blocked or rejected; see "
                    "%s/audrey" % (blocked, CC)))
    return out


async def due(conn, now: float, *, revision) -> list:
    """[(source_key, text)] due now; caller queues them (idempotent keys)."""
    st = await _row(conn, STATE_KEY)
    t = _utc(now)
    hour, day = t.strftime("%Y-%m-%dT%H"), t.strftime("%Y-%m-%d")
    scheduled = (st.get("initial_rev") != revision or st.get("hourly") != hour
                 or (t.hour >= DAILY_AFTER_H and st.get("daily") != day))
    if not scheduled and now - float(st.get("alerts_checked_at") or 0) < 300:
        return []                       # nothing due: no read at all
    s = await snapshot(conn, now)
    st["alerts_checked_at"] = now
    out = []
    if st.get("initial_rev") != revision:
        out.append(("update:initial:%s" % revision,
                    "Status briefing · Derek, Xavier and Audrey are connected "
                    "to this channel.\n" + account_block(s) + "\n"
                    + research_line(s)))
        st["initial_rev"] = revision
    if st.get("hourly") != hour:
        out.append(("update:hourly:" + hour,
                    "Hourly summary · " + t.strftime("%H:00 UTC") + "\n"
                    + account_block(s) + "\n" + research_line(s)))
        st["hourly"] = hour
    if t.hour >= DAILY_AFTER_H and st.get("daily") != day:
        out.append(("update:daily:" + day,
                    "Daily performance and learning report · " + day + "\n"
                    + account_block(s) + "\n" + research_line(s) + "\n"
                    "Training-strategy results are research costs, reported "
                    "apart from investment results. Learning is reported only "
                    "where a forward evaluation is recorded."))
        st["daily"] = day
    bucket = int(now // ALERT_BUCKET_S)
    for kind, fp, text in alerts(s, now):
        out.append(("alert:%s:%s:%d" % (kind, fp, bucket), text))
    await conn.execute(
        "INSERT INTO ingestion_state(key,value) VALUES($1,$2::jsonb) "
        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
        STATE_KEY, json.dumps(st))
    return out


async def queue(conn, now: float | None = None) -> int:
    """Queue due updates for the management channel. Requires the bridge ON
    and a management channel inside the allowed channels. Returns count."""
    from . import slack_bridge as S
    now = time.time() if now is None else now
    control = S.decode(await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1", S.CONTROL))
    if control.get("enabled") is not True:
        return 0
    cfg = S.settings(AGENT)
    chan = os.getenv("SLACK_MANAGEMENT_CHANNEL_ID", "")
    if not cfg["token"] or not cfg["team"] or chan not in cfg["channels"]:
        return 0
    n = 0
    for key, text in await due(conn, now,
                               revision=control.get("changed_at")):
        r = await conn.execute(
            "INSERT INTO agent_slack_delivery(delivery_id,agent,team_id,"
            "channel_id,source_key,answer,state) VALUES($1,$2,$3,$4,$5,$6,"
            "'READY') ON CONFLICT DO NOTHING",
            S.delivery_id(AGENT, cfg["team"], key), AGENT, cfg["team"], chan,
            key, text[:3400])
        n += r.endswith(" 1")
    return n

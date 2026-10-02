"""PROACTIVE MANAGEMENT UPDATES for the verified management Slack channel.

Produced from the account's own records, queued as READY deliveries in the
existing Slack bridge (agent_slack_delivery), so sending, leases, retries and
the OFF switch are the bridge's. Nothing here changes a position, a policy or
a control.

  * INITIAL briefing once per connection (source key update:initial:<rev>)
  * PROGRESS every 30 minutes, 9 a.m.-6 p.m. Eastern
    (update:progress:<ET date>T<HH:MM>): what changed, why it matters, what
    happens next and whether management action is needed
  * Audrey's HOURLY reconciled account update (update:hourly:<UTC hour>),
    merged into the on-the-hour progress update during business hours
  * DAILY research and performance report after 6 p.m. Eastern
    (update:daily:<ET date>)
  * ESCALATIONS addressed to the verified managers for a genuine decision
    (no paper entry for 3 h; the live mirror underfunded), once a day each
  * ALERTS for a service failure, a reconciliation discrepancy or a research
    blocker. An alert's source key is its condition fingerprint plus a 6-hour
    bucket, so an unchanged condition is not re-posted within 6 h, and an
    hourly summary consolidates everything routine.

SIMULATED (paper) and ACTUAL (live 1:1,000 mirror account) figures are in
separate, labelled sections. Figures are separate and labelled: simulated cash, reserved cash, exposure
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


ET = "America/New_York"
BUSINESS_START_H, BUSINESS_END_H = 9, 18          # 9 a.m. - 6 p.m. Eastern
DAILY_AFTER_ET_H = 18
DECISION_BUCKET_S = 24 * 3600
NO_ENTRY_ESCALATE_S = 3 * 3600
UNANSWERED_AFTER_S = 600


def _et(at: float) -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(at, ZoneInfo(ET))


def progress_slot(now: float) -> str | None:
    """'YYYY-MM-DDTHH:MM' (Eastern, on :00 or :30) when a 30-minute progress
    update belongs to this moment's slot, inside 9:00-18:00 Eastern."""
    t = _et(now)
    if not (BUSINESS_START_H <= t.hour < BUSINESS_END_H or
            (t.hour == BUSINESS_END_H and t.minute < 30)):
        return None
    return t.strftime("%Y-%m-%dT%H:") + ("00" if t.minute < 30 else "30")


def managers_tag() -> str:
    ids = [x.strip() for x in os.getenv("SLACK_MANAGEMENT_USER_IDS", "").split(",")
           if x.strip()]
    return " ".join("{{@%s}}" % i for i in ids)


async def mirror_snapshot(conn) -> dict | None:
    """The ACTUAL (live account) figures of the 1:1,000 mirror, or None when
    the mirror schema is absent or it has never been enabled."""
    if not await conn.fetchval("SELECT to_regclass('execmirror_control') IS NOT NULL"):
        return None
    ctl = await conn.fetchrow("SELECT enabled, stopped, cutover_at FROM execmirror_control WHERE id=1")
    if not ctl or not ctl["cutover_at"]:
        return None
    from . import execmirror_view as V
    v = await V.view(conn, limit=50)
    return {"enabled": ctl["enabled"], "stopped": ctl["stopped"],
            "pnl": v["pnl"], "coverage": v["coverage"],
            "account": v["account"], "events": v["events"][:5]}


def mirror_block(m: dict | None) -> str:
    if not m:
        return "ACTUAL (live account, 1:1,000 mirror): not enabled."
    p, c = m["pnl"], m["coverage"]
    rec = (m["account"] or {}).get("reconciliation") or {}
    bal = ((m["account"] or {}).get("balances") or [{}])[0]
    return ("ACTUAL (live account, 1:1,000 mirror) · %s · live P&L %s vs paper/1,000 %s "
            "(difference %s, tracking %s) · %s of %s paper orders mirrored · "
            "buying power %s · venue reconciliation %s" % (
                "STOPPED" if m["stopped"] else ("ON" if m["enabled"] else "OFF"),
                _usd(p.get("live_total")), _usd(p.get("expected_live_total")),
                _usd(p.get("difference")), p.get("tracking_ratio") or "n/a",
                c.get("mirrored"), c.get("paper_orders_seen"),
                _usd(bal.get("buyingPower")),
                "OK" if rec.get("reconciled") else ("DIFFERENCE" if rec else "not yet read")))


async def activity(conn, since: float, now: float) -> dict:
    """What happened in the paper experiment since the last update."""
    r = await conn.fetchrow(
        """SELECT count(*) AS decisions,
                  count(*) FILTER (WHERE verdict <> 'REFUSE') AS approved
             FROM paper_decisions WHERE decided_at > to_timestamp($1)""", since)
    top = await conn.fetch(
        """SELECT refusal, count(*) AS n FROM paper_decisions
            WHERE decided_at > to_timestamp($1) AND refusal IS NOT NULL
            GROUP BY 1 ORDER BY 2 DESC LIMIT 3""", since)
    o = await conn.fetchrow(
        """SELECT count(*) FILTER (WHERE role='ENTRY') AS entries,
                  count(*) FILTER (WHERE role<>'ENTRY') AS other
             FROM paper_orders WHERE account_id=$1 AND decided_at > to_timestamp($2)""",
        ACCOUNT, since)
    last_entry = await conn.fetchval(
        "SELECT extract(epoch FROM max(decided_at)) FROM paper_orders "
        " WHERE account_id=$1 AND role='ENTRY'", ACCOUNT)
    unanswered = await conn.fetch(
        """SELECT delivery_id, agent, state, created_at FROM agent_slack_delivery
            WHERE question IS NOT NULL AND state NOT IN ('SENT')
              AND created_at < now() - make_interval(secs => $1)
            ORDER BY created_at LIMIT 10""", float(UNANSWERED_AFTER_S)) \
        if await conn.fetchval("SELECT to_regclass('agent_slack_delivery') IS NOT NULL") else []
    return {"decisions": int(r["decisions"] or 0), "approved": int(r["approved"] or 0),
            "refusals": [(x["refusal"], int(x["n"])) for x in top],
            "entries": int(o["entries"] or 0), "other_orders": int(o["other"] or 0),
            "last_entry_at": float(last_entry) if last_entry else None,
            "unanswered": [dict(x) for x in unanswered]}


def _delta(prev: dict, s: dict) -> list:
    a = s["account"]
    out = []
    for key, label in (("realized_pnl_usd", "realized P&L"),
                       ("unrealized_pnl_usd", "unrealized P&L"),
                       ("cash_usd", "simulated cash")):
        try:
            d = float(a.get(key) or 0) - float(prev.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if prev and abs(d) >= 0.005:
            out.append("%s %s%s" % (label, "+" if d > 0 else "-", _usd(abs(d))[0:]))
    if prev and a.get("open_positions") != prev.get("open_positions"):
        out.append("open positions %s -> %s" % (prev.get("open_positions"),
                                                a.get("open_positions")))
    return out


def progress_text(s: dict, act: dict, m: dict | None, prev: dict, *, label: str,
                  with_reconciliation: bool) -> str:
    changed = _delta(prev, s)
    changed.append("%d decisions (%d approved), %d new entries, %d other orders"
                   % (act["decisions"], act["approved"], act["entries"],
                      act["other_orders"]))
    why, nxt, action = [], [], "None."
    if act["entries"] == 0 and act["refusals"]:
        why.append("No new paper entries: every decision was refused, mostly "
                   + ", ".join("%s (%d)" % r for r in act["refusals"]) + ".")
        nxt.append("Derek keeps evaluating; the refusal reasons are under "
                   "research in #agent-workroom.")
    if s["reconcile"].get("reconciled") is False:
        why.append("The paper ledger does NOT reconcile; Audrey is escalating.")
        action = "Yes — see Audrey's escalation."
    if act["unanswered"]:
        why.append("%d management question(s) without a delivered answer "
                   "(oldest %s)." % (len(act["unanswered"]),
                                     act["unanswered"][0]["created_at"]))
        nxt.append("The bridge retries within its limits; failures are named, "
                   "not reposted.")
    if not why:
        why.append("Routine: accounts reconcile and services are current.")
    if not nxt:
        nxt.append("Next consolidated update in 30 minutes (business hours).")
    lines = [label,
             "What changed: " + "; ".join(changed) + ".",
             "Why it matters: " + " ".join(why),
             "Next: " + " ".join(nxt),
             "Management action needed: " + action,
             "SIMULATED (paper account):"]
    lines.append(account_block(s) if with_reconciliation else
                 "Realized %s · unrealized %s · %s open · ledger %s" % (
                     _usd(s["account"].get("realized_pnl_usd")),
                     _usd(s["account"].get("unrealized_pnl_usd")),
                     s["account"].get("open_positions"),
                     "reconciled" if s["reconcile"].get("reconciled") else "see Audrey"))
    lines.append(mirror_block(m))
    lines.append(research_line(s))
    return "\n".join(lines)


def decision_escalations(s: dict, act: dict, m: dict | None, now: float) -> list:
    """Genuine questions for management, each at most once a day."""
    out = []
    tag = managers_tag()
    if act["last_entry_at"] and now - act["last_entry_at"] > NO_ENTRY_ESCALATE_S \
            and act["refusals"]:
        top = act["refusals"][0]
        out.append(("no-entries", _fp(top[0]),
                    "%s Decision needed · no paper entry for %d h. The leading refusal is "
                    "%s (%d in the last period). The live 1:1,000 mirror can only trade "
                    "what paper trades. Do you want Derek to prioritise research on "
                    "this blocker (reply in thread), or keep the current policy?"
                    % (tag, (now - act["last_entry_at"]) // 3600, top[0], top[1])))
    if m and m["enabled"]:
        bal = ((m["account"] or {}).get("balances") or [{}])[0]
        try:
            bp = float(bal.get("buyingPower"))
        except (TypeError, ValueError):
            bp = None
        if bp is not None and bp < 500:
            out.append(("mirror-funding", _fp(round(bp)),
                        "%s Decision needed · the live mirror account has %s buying "
                        "power; mirroring the whole $500,000 paper account at 1:1,000 "
                        "needs about $500. Orders beyond the balance are recorded as "
                        "INSUFFICIENT_CASH, so live P&L will cover only part of paper. "
                        "Fund the account, or accept partial coverage?" % (tag, _usd(bp))))
    return out


async def due(conn, now: float, *, revision) -> list:
    """[(source_key, text)] due now; caller queues them (idempotent keys)."""
    st = await _row(conn, STATE_KEY)
    t = _utc(now)
    et = _et(now)
    hour, day_et = t.strftime("%Y-%m-%dT%H"), et.strftime("%Y-%m-%d")
    slot = progress_slot(now)
    scheduled = (st.get("initial_rev") != revision or st.get("hourly") != hour
                 or (slot and st.get("progress") != slot)
                 or (et.hour >= DAILY_AFTER_ET_H and st.get("daily") != day_et))
    if not scheduled and now - float(st.get("alerts_checked_at") or 0) < 300:
        return []                       # nothing due: no read at all
    s = await snapshot(conn, now)
    since = float(st.get("last_update_at") or now - 1800)
    act = await activity(conn, since, now)
    m = await mirror_snapshot(conn)
    prev = st.get("prev") or {}
    st["alerts_checked_at"] = now
    out = []
    posted = False
    if st.get("initial_rev") != revision:
        out.append(("update:initial:%s" % revision,
                    "Status briefing · Derek, Xavier and Audrey are connected to this "
                    "channel.\n"
                    "Derek (discovery & entry): evaluates every market against Pinnacle; "
                    "researching PinnAPI coverage, missed opportunities, freshness, "
                    "market matching and calibration.\n"
                    "Xavier (portfolio management): manages open positions and standing "
                    "orders; researching execution quality, exit alternatives and paper "
                    "vs live execution differences.\n"
                    "Audrey (audit & intelligence): reconciles the ledger, reviews Derek's "
                    "and Xavier's reasoning, reports hourly and daily.\n"
                    "Ask any of us with @Derek / @Xavier / @Audrey; reply in the thread "
                    "for follow-ups; '@Agent assign: <question>' opens a research task.\n"
                    + progress_text(s, act, m, prev, label="Current state",
                                    with_reconciliation=True)))
        st["initial_rev"] = revision
        posted = True
    if slot and st.get("progress") != slot:
        on_hour = slot.endswith(":00")
        out.append(("update:progress:" + slot,
                    progress_text(s, act, m, prev,
                                  label="Progress update · %s ET%s" % (
                                      slot[-5:], " · with Audrey's hourly reconciliation"
                                      if on_hour else ""),
                                  with_reconciliation=on_hour)))
        st["progress"] = slot
        if on_hour:
            st["hourly"] = hour          # merged: no separate hourly message
        posted = True
    if st.get("hourly") != hour:
        out.append(("update:hourly:" + hour,
                    "Audrey · hourly reconciled account update · "
                    + t.strftime("%H:00 UTC") + "\nSIMULATED (paper account):\n"
                    + account_block(s) + "\n" + mirror_block(m)))
        st["hourly"] = hour
        posted = True
    if et.hour >= DAILY_AFTER_ET_H and st.get("daily") != day_et:
        out.append(("update:daily:" + day_et,
                    "Audrey · daily research and performance report · " + day_et + "\n"
                    + progress_text(s, act, m, prev, label="Today",
                                    with_reconciliation=True) + "\n"
                    "Training-strategy results are research costs, reported apart from "
                    "investment results. Learning is reported only where a forward "
                    "evaluation is recorded."))
        st["daily"] = day_et
        posted = True
    bucket = int(now // ALERT_BUCKET_S)
    for kind, fp, text in alerts(s, now):
        out.append(("alert:%s:%s:%d" % (kind, fp, bucket),
                    (managers_tag() + " " + text) if kind in
                    ("reconciliation", "service:cycle", "service:pass") else text))
    dbucket = int(now // DECISION_BUCKET_S)
    for kind, fp, text in decision_escalations(s, act, m, now):
        out.append(("escalation:%s:%s:%d" % (kind, fp, dbucket), text))
    if posted:
        st["last_update_at"] = now
        st["prev"] = {k: s["account"].get(k) for k in
                      ("realized_pnl_usd", "unrealized_pnl_usd", "cash_usd",
                       "open_positions")}
    await conn.execute(
        "INSERT INTO ingestion_state(key,value) VALUES($1,$2::jsonb) "
        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
        STATE_KEY, json.dumps(st, default=str))
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

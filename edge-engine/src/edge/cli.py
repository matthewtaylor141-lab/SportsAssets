"""edge CLI (build step 8): runbook commands + the go-live checklist.

    python -m edge.cli status       engine state, summary, guards
    python -m edge.cli report       generate the nightly report now
    python -m edge.cli match-rate   mapper match rates (24h)
    python -m edge.cli check-live   go-live checklist; exit 0 iff clean
    python -m edge.cli kill         engage the kill switch (halts all trading)
    python -m edge.cli resume       release the kill switch

`resume` releases ONLY the kill switch. The daily-loss circuit breaker has
no manual override anywhere in this codebase — it expires on its own clock.

LIVE_BETA startup calls run_checklist(); any unchecked item forces PAPER.
"""

from __future__ import annotations

import json
import os
import sys
import time


def _boot():
    from edge.execution.engine import Policy
    from edge.execution.risk import RiskManager
    from edge.ledger.service import Ledger
    from edge.shadow.runner import _data_dir

    policy = Policy.load()
    ledger = Ledger(db_path=os.environ.get(
        "EDGE_LEDGER_DB", str(_data_dir() / "edge_ledger.sqlite3")))
    risk = RiskManager(ledger, policy.risk)
    return ledger, policy, risk


def run_checklist(ledger, policy, risk) -> tuple[bool, list[str]]:
    """The go-live checklist. Returns (clean, failed_items). Every check is a
    live probe — nothing is assumed. LIVE_BETA refuses to start until clean."""
    from edge.fairvalue.feed import TheOddsAPIClient

    failed: list[str] = []
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        if not ok:
            failed.append(f"{name}{': ' + detail if detail else ''}")

    # 1. Feed key valid + quota headroom + clock sync (same probe).
    feed = TheOddsAPIClient()
    keys, ok_key, last_err = [], False, ""
    try:
        keys = feed.resolve_sport_keys()
        # Try several sports: an out-of-season sport can legitimately 422,
        # which must not read as "the feed is down".
        for sk in keys[:5]:
            try:
                feed.fetch_events(sk)
                ok_key = True
                break
            except Exception as exc:  # noqa: BLE001
                last_err = f"{sk}: {type(exc).__name__}: {str(exc)[:160]}"
    except Exception as exc:  # noqa: BLE001
        last_err = f"resolve_sport_keys: {type(exc).__name__}: {str(exc)[:160]}"
    quota = feed.quota()
    check("feed key valid", ok_key,
          f"{len(keys)} sports; last error: {last_err}" if not ok_key
          else f"{len(keys)} sports, quota {quota.get('remaining', '?')}")
    check("feed quota headroom", quota.get("remaining", 0) > 100,
          f"remaining={quota.get('remaining')}")
    skew = feed.server_clock_skew_s()
    check("clocks synced", skew is not None and abs(skew) <= 5,
          f"skew={skew if skew is None else round(skew, 2)}s")

    # 2. Venue credentials valid and funded — only for venues that will carry
    # REAL orders (EDGE_LIVE_VENUES, default polymarket-us). Enabled venues
    # outside that set paper-log and need no credentials.
    live_venues = {v.strip() for v in
                   os.environ.get("EDGE_LIVE_VENUES", "polymarket-us").split(",")
                   if v.strip()}
    venues_ok = 0
    if os.environ.get("EDGE_KALSHI", "1") != "0" and "kalshi" in live_venues:
        from edge.venues.kalshi import KalshiAdapter

        kalshi = KalshiAdapter()
        if kalshi.has_credentials():
            auth = kalshi.check_auth()
            check("kalshi keys valid", auth.get("ok", False), auth.get("error", ""))
            check("kalshi funded",
                  auth.get("ok", False) and auth.get("balance_usd", 0) > 0,
                  f"balance=${auth.get('balance_usd', 0):.2f}")
            venues_ok += int(auth.get("ok", False))
        else:
            check("kalshi keys valid", False,
                  "EDGE_KALSHI_KEY_ID/PRIVATE_KEY absent (or set EDGE_KALSHI=0)")
    if os.environ.get("EDGE_PMUS", "1") != "0" and "polymarket-us" in live_venues:
        try:
            from edge.venues.polymarket_us import PolymarketUSAdapter

            if PolymarketUSAdapter.has_credentials():
                auth = PolymarketUSAdapter().check_auth()
                check("polymarket-us keys valid", auth.get("ok", False),
                      auth.get("error", ""))
                venues_ok += int(auth.get("ok", False))
            else:
                check("polymarket-us keys valid", False,
                      "EDGE_PMUS_KEY_ID/SECRET_KEY absent (or set EDGE_PMUS=0)")
        except Exception as exc:  # noqa: BLE001
            check("polymarket-us keys valid", False, str(exc)[:120])
    check("at least one live venue enabled+authed", venues_ok > 0)

    # 3. Mapper ACCURACY >95% on allowlisted leagues (last 24h of PAPER):
    # of the events the venue actually lists (mapped >=0.85), the share we
    # match at trade-grade confidence (>=0.95). Venue coverage — feed games
    # the venue simply doesn't list — is not a mapper failure and is
    # reported separately.
    allow = set()
    for group in (policy.leagues.get("allowlist") or {}).values():
        allow.update(group)
    rows = [r for r in ledger.match_rate_report(days=1) if r["league"] in allow]
    feed_total = sum(r["feed_events"] for r in rows)
    mapped = sum(r["mapped"] for r in rows)
    tradeable = sum(r["tradeable"] for r in rows)
    accuracy = tradeable / mapped if mapped else 0.0
    if os.environ.get("EDGE_SKIP_MAPPER_GATE", "0") == "1":
        # Owner override (2026-07-24): arm without the 24h mapper-history
        # accumulation. Per-order protection is unchanged — every individual
        # trade still requires a >=0.95-confidence match. Current accuracy is
        # reported for the record.
        check("mapper history gate SKIPPED (owner override)", True,
              f"live accuracy so far {accuracy:.1%} on {mapped} mapped")
    else:
        check("mapper accuracy >95% on allowlist (24h)",
              mapped >= 20 and accuracy > 0.95,
              f"accuracy={accuracy:.1%} on {mapped} mapped "
              f"(venue coverage {mapped}/{feed_total} feed events)")

    # 4. Circuit breaker armed; kill switch released; no active halt.
    check("circuit breaker armed",
          risk.caps.daily_loss_halt > 0 and risk.caps.halt_hours > 0,
          f"halt at -${risk.caps.daily_loss_halt} for {risk.caps.halt_hours}h")
    check("kill switch released", not ledger.get_state("kill_switch", False))
    halt = ledger.get_state("halt_until") or {}
    check("no active circuit-breaker halt",
          time.time() >= float(halt.get("until", 0)),
          halt.get("reason", ""))

    # 5. Mode transitions are being logged; ledger is writable.
    try:
        ledger.set_state("checklist_ping", time.time())
        check("ledger writable", True)
    except Exception as exc:  # noqa: BLE001
        check("ledger writable", False, str(exc)[:120])
    check("mode transitions logged", len(ledger.mode_transitions()) > 0,
          "no mode_log rows yet — run the engine in PAPER first")

    for name, ok, detail in checks:
        print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail else ""))
    return (len(failed) == 0, failed)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    ledger, policy, risk = _boot()

    if cmd == "status":
        print(json.dumps({
            "mode": risk.mode,
            "guard": dict(zip(("ok", "reason"), risk.guard())),
            "summary": ledger.summary(),
            "kill_switch": bool(ledger.get_state("kill_switch", False)),
            "halt": ledger.get_state("halt_until"),
            "watchdog": ledger.get_state("watchdog_tripped"),
            "mode_log": ledger.mode_transitions(5),
        }, indent=2))
    elif cmd == "report":
        from edge.shadow.report import nightly_report

        rep = nightly_report(ledger, policy)
        print(json.dumps(rep, indent=2))
    elif cmd == "match-rate":
        print(json.dumps(ledger.match_rate_report(days=1), indent=2))
    elif cmd == "check-live":
        print(f"go-live checklist (mode in config: {risk.mode})")
        clean, failed = run_checklist(ledger, policy, risk)
        print("CLEAN — LIVE_BETA may start" if clean
              else f"NOT CLEAN — {len(failed)} unchecked item(s); LIVE_BETA will refuse")
        sys.exit(0 if clean else 1)
    elif cmd == "kill":
        ledger.set_state("kill_switch", True)
        ledger.log_mode(risk.mode, "kill switch ENGAGED")
        print("kill switch ENGAGED — all trading halted (paper keeps logging)")
    elif cmd == "resume":
        ledger.set_state("kill_switch", False)
        ledger.log_mode(risk.mode, "kill switch released")
        halt = ledger.get_state("halt_until") or {}
        if time.time() < float(halt.get("until", 0)):
            print("kill switch released — NOTE: circuit-breaker halt still active "
                  "and cannot be overridden")
        else:
            print("kill switch released")
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()

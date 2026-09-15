"""edge CLI (build step 8): runbook commands + the go-live checklist.

    python -m edge.cli status       engine state, summary, guards
    python -m edge.cli census [N]   N-day venue market census + estimate
    python -m edge.cli report       generate the nightly report now
    python -m edge.cli methodology  render the methodology document
    python -m edge.cli figures      every measured figure, as JSON
    python -m edge.cli export       figures + positions.csv + the document

Reporting commands take `--days N` (rolling, default 7), `--since DATE`
(absolute UTC — what a re-baseline needs), `--all-modes` (include PAPER)
and `--out PATH`.
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
    # An ACTIVE halt is reported but does NOT fail the checklist. The halt
    # is an ORDER-TIME stop — risk.guard() blocks every engine order and
    # live_blocked() every side-channel sweep, scope-aware, until it
    # expires. Failing BOOT on it demoted the whole process to PAPER on
    # any redeploy inside a halt window, silently converting the copy
    # sleeve (its own breaker, owner directive 2026-08-05) to paper for
    # hours while the status read "halted" (observed 18:31Z 2026-08-05 →
    # 00:45Z 2026-08-06: every 'copied' counter that evening was dry-run).
    halt = ledger.get_state("halt_until") or {}
    halted = time.time() < float(halt.get("until", 0))
    check("circuit-breaker halt state (informational)", True,
          "ACTIVE — engine paths blocked at order time until "
          f"{halt.get('until')}" if halted else "none")

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


def _parse_window(argv: list[str]) -> tuple[int, float | None, bool]:
    """--days N | --since YYYY-MM-DD[THH:MM:SSZ] | --all-modes.

    `--since` is an absolute UTC instant; `--days` a rolling window ending
    now. A re-baseline needs the former and a dashboard wants the latter, so
    both exist and absolute wins.
    """
    from datetime import datetime, timezone

    days, since, live_only = 7, None, True
    for i, a in enumerate(argv):
        if a == "--days" and i + 1 < len(argv):
            days = int(argv[i + 1])
        elif a == "--since" and i + 1 < len(argv):
            raw = argv[i + 1]
            fmt = "%Y-%m-%dT%H:%M:%SZ" if "T" in raw else "%Y-%m-%d"
            since = datetime.strptime(raw, fmt).replace(
                tzinfo=timezone.utc).timestamp()
        elif a == "--all-modes":
            live_only = False
    return days, since, live_only


def _cmd_reporting(cmd: str, ledger, policy, argv: list[str]) -> None:
    from pathlib import Path

    from edge.reporting.export import write_bundle
    from edge.reporting.figures import compute_figures
    from edge.reporting.methodology import render
    from edge.shadow.runner import _data_dir

    days, since, live_only = _parse_window(argv)
    out = None
    for i, a in enumerate(argv):
        if a == "--out" and i + 1 < len(argv):
            out = Path(argv[i + 1])

    if cmd == "figures":
        print(json.dumps(compute_figures(ledger, policy, days=days,
                                         since=since, live_only=live_only),
                         indent=2))
    elif cmd == "methodology":
        doc = render(ledger, policy, days=days, since=since,
                     live_only=live_only)
        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(doc)
            print(f"wrote {out}")
        else:
            print(doc)
    else:                                    # export
        res = write_bundle(ledger, policy, out or (_data_dir() / "export"),
                           days=days, since=since, live_only=live_only)
        print(json.dumps(res, indent=2))


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
    elif cmd in ("methodology", "figures", "export"):
        _cmd_reporting(cmd, ledger, policy, sys.argv[2:])
    elif cmd == "census":
        _cmd_census(int(sys.argv[2]) if len(sys.argv) > 2 else 30)
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


def _cmd_census(days: int) -> None:
    """30-day venue census + measured-funnel fill estimate."""
    from polymarket_us import PolymarketUS

    from edge.analysis.venue_census import census, estimate_fills_per_day

    ledger, policy, risk = _boot()
    result = census(PolymarketUS(), days=days)

    # Measured conversion rates from our own live telemetry.
    rows = ledger.match_rate_report(days=min(days, 7))
    mapped = sum(r["mapped"] for r in rows)
    tradeable = sum(r["tradeable"] for r in rows)
    tradeable_rate = (tradeable / mapped) if mapped else 0.0
    result["estimate"] = estimate_fills_per_day(
        result, tradeable_rate=tradeable_rate or 0.0,
        clear_rate=float(os.environ.get("EDGE_MEASURED_CLEAR_RATE", "0") or 0),
    )
    print(json.dumps(result, indent=2, default=str))

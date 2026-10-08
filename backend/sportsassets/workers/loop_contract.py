"""THE SUPERVISED-LOOP RETURN CONTRACT (completion readiness, 2026-10-07).

A supervised loop that returns normally is restarted after
all.RESTART_DELAY_SECONDS -- that is how a loop polling a database control
row (bettor_live) notices it was started. But a loop switched off by its
ENVIRONMENT cannot change its answer until the process is redeployed (this
service re-reads its environment only on a deploy), so restarting it every
five seconds is pure churn: a log line, a loop-health write and an import
pass per loop, forever. Production showed exactly that for
UNIVERSAL_MARKET_PLANE=off.

A loop that is OFF BY CONFIGURATION returns LOOP_DISABLED instead of None.
The supervisor then records it once and stops supervising that loop for the
life of the process. Nothing else changes: a crash is still restarted, a
clean None return is still restarted, and a database-controlled loop never
returns LOOP_DISABLED for its database control, so that row is still
polled -- an operator flips it with no deploy (render-ops sql obs-run /
obs-stop). bettor_live's delegated incentive lane polls it by PARKING IN
PLACE (bettor_live_loop._park, re-read every IDLE_POLL_S, returning after
PARK_MAX_S) rather than by returning into the five-second restart; only that
lane's environment kill switch (BETTOR_LIVE_LOOP=off) returns LOOP_DISABLED.
"""
from __future__ import annotations

LOOP_DISABLED = "LOOP_DISABLED_BY_CONFIGURATION"


def is_disabled(result) -> bool:
    return result == LOOP_DISABLED

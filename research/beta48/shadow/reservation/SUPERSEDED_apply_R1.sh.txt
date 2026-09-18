#!/bin/sh
# RESERVATION R1 -- APPLY. Omits exactly two scheduled occurrences on
# 2026-09-18 so the frozen substantive capture can win one lock handoff.
#
#   run85-phase2-capture     omit nominal 2026-09-18 00:00Z
#   beta48-forward-capture   omit nominal 2026-09-18 02:00Z
#
# NOT AUTHORIZED UNTIL MANAGEMENT APPROVES. Cancels nothing, disables
# nothing, weakens no concurrency group, changes no collector.
set -eu
cd "$(git rev-parse --show-toplevel)"
test -z "$(git status --porcelain)" || { echo "tree not clean"; exit 1; }
grep -q 'cron: "0 \*/6 \* \* \*"' .github/workflows/run85-phase2-capture.yml
grep -q 'cron: "0 \*/2 \* \* \*"' .github/workflows/beta48-forward-capture.yml
sed -i 's|cron: "0 \*/6 \* \* \*"|cron: "0 6,12,18 * * *"|' \
    .github/workflows/run85-phase2-capture.yml
sed -i 's|cron: "0 \*/2 \* \* \*"|cron: "0 0,4,6,8,10,12,14,16,18,20,22 * * *"|' \
    .github/workflows/beta48-forward-capture.yml
git add .github/workflows/run85-phase2-capture.yml \
        .github/workflows/beta48-forward-capture.yml
git commit -q -m "RESERVATION R1: omit two scheduled occurrences 2026-09-18 [skip render]

run85-phase2-capture   omits nominal 00:00Z (one 5h segment)
beta48-forward-capture omits nominal 02:00Z (one 33m segment)

Both restored by reservation/restore_R1.sh the moment the substantive
capture's job is created. No run cancelled, no schedule disabled, no
concurrency group changed, no collector SHA or threshold touched.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VWCVhVqpQNcNptWpp25jDC"
git push -u origin claude/session-njaewf
echo "R1 APPLIED $(git rev-parse --short HEAD)"

#!/bin/sh
# RESERVATION R1 -- RESTORE. Puts both crons back exactly as they were.
# Run the moment the substantive capture's JOB IS CREATED (lock acquired),
# and unconditionally at the 2026-09-18T06:00Z backstop whatever happened.
# Idempotent: exits 0 if the crons are already restored.
set -eu
cd "$(git rev-parse --show-toplevel)"
changed=0
if grep -q 'cron: "0 6,12,18 \* \* \*"' .github/workflows/run85-phase2-capture.yml; then
  sed -i 's|cron: "0 6,12,18 \* \* \*"|cron: "0 */6 * * *"|' \
      .github/workflows/run85-phase2-capture.yml
  changed=1
fi
if grep -q 'cron: "0 0,4,6,8,10,12,14,16,18,20,22 \* \* \*"' .github/workflows/beta48-forward-capture.yml; then
  sed -i 's|cron: "0 0,4,6,8,10,12,14,16,18,20,22 \* \* \*"|cron: "0 */2 * * *"|' \
      .github/workflows/beta48-forward-capture.yml
  changed=1
fi
[ "$changed" = 1 ] || { echo "R1 ALREADY RESTORED"; exit 0; }
git add .github/workflows/run85-phase2-capture.yml \
        .github/workflows/beta48-forward-capture.yml
git commit -q -m "RESERVATION R1: restore both schedules [skip render]

run85-phase2-capture   back to 0 */6 * * *
beta48-forward-capture back to 0 */2 * * *

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VWCVhVqpQNcNptWpp25jDC"
git push -u origin claude/session-njaewf
echo "R1 RESTORED $(git rev-parse --short HEAD)"

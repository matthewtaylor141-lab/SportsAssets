#!/bin/bash
# RELEASE GATE of the FROZEN candidate a8bf09a (eef233b + Pinnacle-only paper benchmark + chat fixes) (verdict vs e348ebf AND dff544c).
# gate reporter + paper slices 1-2 + personas + agent-page paper contract).
# Replaces the b4e5578 pre-gate that the container restart killed at 90%.
# Same 3.12.3 venv (= requirements.lock), candidate run_gate.sh, fresh DB from
# mp2, and the STREAMING reporter so failures survive a kill.
export PATH=/tmp/claude-0/gatevenv312/bin:$PATH
unset PYTHONPATH
export GATE_TOOLS_DIR=/tmp/claude-0/gate_tools_v3
CO=/tmp/claude-0/gate_a8bf; DB=gate_a8bf_head; OUT=/tmp/claude-0/gate_a8bf_out/head
mkdir -p /tmp/claude-0/gate_a8bf_out
sudo -u postgres psql -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB TEMPLATE mp2"
(cd $CO/backend && DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/$DB python -m sportsassets.scripts.migrate) > $OUT.migrate.log 2>&1
echo "=== head $(date -u +%FT%TZ) start" >> /tmp/claude-0/gate_a8bf_out/timeline.txt
(cd $CO/backend && bash $CO/backend/tools/run_gate.sh $CO $DB $OUT --timeout=600) > $OUT.run.log 2>&1
echo "=== head $(date -u +%FT%TZ) exit=$?" >> /tmp/claude-0/gate_a8bf_out/timeline.txt

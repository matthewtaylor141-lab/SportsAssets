#!/bin/bash
# RELEASE GATE of the frozen candidate 70aec64 (a8bf09a + per-strategy chat attribution).
export PATH=/tmp/claude-0/gatevenv312/bin:$PATH
unset PYTHONPATH
export GATE_TOOLS_DIR=/tmp/claude-0/gate_tools_v3
CO=/tmp/claude-0/gate_lbl; DB=gate_lbl_head; OUT=/tmp/claude-0/gate_lbl_out/head
mkdir -p /tmp/claude-0/gate_lbl_out
sudo -u postgres psql -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB TEMPLATE mp2"
(cd $CO/backend && DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/$DB python -m sportsassets.scripts.migrate) > $OUT.migrate.log 2>&1
echo "=== head $(date -u +%FT%TZ) start" >> /tmp/claude-0/gate_lbl_out/timeline.txt
(cd $CO/backend && bash $CO/backend/tools/run_gate.sh $CO $DB $OUT --timeout=600) > $OUT.run.log 2>&1
echo "=== head $(date -u +%FT%TZ) pytest-exit=$?" >> /tmp/claude-0/gate_lbl_out/timeline.txt
cd $CO/backend
for B in gate_a8bf_out gate_e348_out gate_e4dc_out; do
  python tools/gate_verdict.py /tmp/claude-0/$B/$( [ $B = gate_e4dc_out ] && echo base || echo head)_report.json ${OUT}_report.json --critical tools/capital_critical_tests.txt > /tmp/claude-0/gate_lbl_out/verdict_vs_$B.txt 2>&1
  echo "=== verdict vs $B exit=$?" >> /tmp/claude-0/gate_lbl_out/timeline.txt
done

set -uo pipefail
say() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"; }
echo '```' >> "$GITHUB_STEP_SUMMARY"
A="${API:-https://sportsassets-api.onrender.com}"
H="X-Admin-Token: ${ADMIN_TOKEN}"
say "== F1 . DID ANY CAPACITY-PROBE RECORD REACH PRODUCTION =="
say "  ASKED OF PRODUCTION ITSELF, read-only. The runner holds no"
say "  database credential, so the service -- which does -- runs"
say "  the audit. A probe record is identified by the harness own"
say "  fingerprints: the condition id shape, the slug prefix and"
say "  the payout event. NEVER by an experiment id, because the"
say "  experiment id is what was wrong."
say ""
say "  AND THE POSITIONS ARE THE LOAD-BEARING COUNT. Positions and"
say "  fills feed exposure and per-lane P&L DIRECTLY -- neither"
say "  consumer reads external_valuations on the way to a"
say "  position -- so a zero valuation count would establish"
say "  nothing about either. The evidence is zero identified probe"
say "  positions AND zero of their dependent records."
AU=$(curl -sS -o /tmp/prodaudit.json -w '%{http_code}' -H "$H" \
       "$A/api/admin/capacity-probe-audit" || echo 000)
say "  audit                          HTTP $AU (expect 200)"
jq -r '"  verdict                        " + (.verdict // "-"),
       "  probe positions                " + ((.positions // 0) | tostring),
       "  of those in a strategy book    " + ((.in_strategy_book // 0) | tostring),
       "  probe orders                   " + ((.orders // 0) | tostring),
       "  probe fills                    " + ((.fills // 0) | tostring),
       "  probe decisions                " + ((.decisions // 0) | tostring),
       "  probe outcome rows             " + ((.outcomes // 0) | tostring),
       "  probe valuations (not the proof) "
         + ((.valuations // 0) | tostring),
       "  positions in this database     " + ((.all_positions // 0) | tostring),
       "  clean                          " + (.clean | tostring),
       "  unreadable                     " + (.unreadable // "no")' \
  /tmp/prodaudit.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
jq -r '.books // {} | to_entries[]?
       | "  BOOK " + (.key // "-") + "   positions " + (.value | tostring)' \
  /tmp/prodaudit.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
echo '```' >> "$GITHUB_STEP_SUMMARY"

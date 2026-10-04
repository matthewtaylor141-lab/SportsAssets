set -uo pipefail
say() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"; }
echo '```' >> "$GITHUB_STEP_SUMMARY"
A="${API:-https://sportsassets-api.onrender.com}"
say "## the UNFUNDED research-shadow lane"
say "It waives exactly MODEL_TRUST_DRIFT. Freshness, the settlement"
say "condition-to-payout comparison, the declared support, every"
say "exposure rail and positive net edge ALL still block, so this"
say "cannot make an entry happen -- only stop being the reason one"
say "did not. It has no path to capital."
say ""
C=$(curl -sS --max-time 45 -o /tmp/arm.json -w '%{http_code}' \
      -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
      -H 'Content-Type: application/json' \
      -d '{"confirm":"ARM"}' \
      "$A/api/admin/rn1x-arm-research-shadow" || echo 000)
say "arm HTTP $C"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  "  action                 " + n(.action),
  "  wrote                  " + n(.wrote),
  "  control_key            " + n(.control_key),
  "  authorised             " + n(.authorised.authorised),
  "  why                    " + n(.authorised.why),
  "  calibration_rows       " + n(.calibration_rows)
    + "   still_unmeasured " + n(.calibration_still_unmeasured),
  "  waivable               " + n(.describe.waivable),
  "  never_waivable         " + n(.describe.never_waivable)' \
  /tmp/arm.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || {
    say "ARM REPORT DID NOT RENDER -- raw:"
    jq -r 'tojson | .[0:1200]' /tmp/arm.json 2>&1 \
      | tee -a "$GITHUB_STEP_SUMMARY" || true; }
echo '```' >> "$GITHUB_STEP_SUMMARY"

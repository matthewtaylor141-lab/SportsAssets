set -uo pipefail
say() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"; }
echo '```' >> "$GITHUB_STEP_SUMMARY"
A="${API:-https://sportsassets-api.onrender.com}"
# THE SHADOW P&L, THROUGH THE SAME AUTHENTICATED READ THE COMMAND
# CENTRE USES. Not a second query: if this and the page disagree
# one of them is wrong and nobody could tell which.
C=$(curl -sS --max-time 60 -o /tmp/pnl.json -w '%{http_code}' \
      -H "X-Admin-Token: $ADMIN_TOKEN" \
      "$A/api/command/rn1x/statuses" || echo 000)
say "## reconciled shadow P&L (HTTP $C)"
# `.statuses.shadow_pnl`, AND THE MISSING PREFIX WAS THE WHOLE
# BUG. The route returns {scope, label, statuses:{...}} and
# `shadow_pnl` is a key of `statuses`, not of the response. The
# first version of this step indexed `.shadow_pnl` and so printed
# `mark_basis -` with no lane lines at all -- a null path, not an
# empty P&L. The raw dump is now the P&L OBJECT rather than a
# 7000-character prefix of the whole payload, which is what
# stopped the earlier run's evidence from reaching the log.
jq -r '.statuses.shadow_pnl | tojson | .[0:6000]' /tmp/pnl.json \
  2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "-- per lane --"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  .statuses.shadow_pnl as $P
  | $P.unrealised_basis as $b
  | "mark_basis " + n($b),
    ($P.lanes // {} | to_entries[]
      | "lane " + .key
        + "  settled " + n(.value.settled_positions)
        + "  open " + n(.value.open_positions)
        + "  realised " + n(.value.net_usd)
        + "  fees " + n(.value.fees_usd)
        + "  basis " + n(.value.open_inventory_at_cost_usd)
        + "  unrealised " + n(.value.unrealised.unrealised_usd)
        + "  total " + n(.value.total_pnl_usd)
        + " (" + n(.value.total_pnl_status) + ")",
      "    marked " + n(.value.unrealised.marked_positions)
        + "  unmarked " + n(.value.unrealised.unmarked_positions)
        + "  blockers " + n(.value.unrealised.unmarked_by_blocker),
      "    provenance " + n(.value.unrealised.by_provenance))' \
  /tmp/pnl.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
echo '```' >> "$GITHUB_STEP_SUMMARY"

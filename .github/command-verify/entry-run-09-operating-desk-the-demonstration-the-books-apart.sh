set -uo pipefail
say() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"; }
echo '```' >> "$GITHUB_STEP_SUMMARY"
A="${API:-https://sportsassets-api.onrender.com}"
H="X-Admin-Token: ${ADMIN_TOKEN}"

say "== D1 . THE PAGE IS SERVED, AND THE SHORT URL REDIRECTS =="
PC=$(curl -sS -o /tmp/deskpage.html -w '%{http_code}' \
       -H "$H" "$A/api/command/bettor/desk/page" || echo 000)
RC=$(curl -sS -o /dev/null -w '%{http_code}' \
       "$A/command/desk" || echo 000)
UC=$(curl -sS -o /dev/null -w '%{http_code}' \
       "$A/api/command/bettor/desk/page" || echo 000)
say "  page with the operator token   HTTP $PC (expect 200)"
say "  short URL /command/desk        HTTP $RC (expect 307)"
say "  page with NO credential       HTTP $UC (expect 401)"
say "  page bytes                    $(wc -c < /tmp/deskpage.html)"
for M in "Bettor EV Engine" "btn-halt" "optoken" \
         "/api/command/bettor/control/" "NO_TRADE"; do
  if grep -qF "$M" /tmp/deskpage.html; then
    say "  served page contains          $M"
  else
    say "  MISSING from the served page  $M"
  fi
done

say ""
say "== D2 . THE CONTROLLED DEMONSTRATION, WHOLE LIFECYCLE =="
curl -sS -X POST -H "$H" -H 'Content-Type: application/json' \
  -d '{"stage":"full"}' \
  "$A/api/admin/bettor-demonstration/run" > /tmp/demo.json \
  || say "  the demonstration call failed"
jq -r '"  experiment      " + (.experiment_id // "-"),
       "  ok              " + (.ok | tostring),
       "  position        " + (.position_id // "-"),
       "  cycles run      " + ((.lifecycle.cycles_run // 0) | tostring),
       "  flat            " + (.lifecycle.position_is_flat | tostring),
       "  residual        " + (.lifecycle.residual_settlement // "-"),
       "  excluded from strategy performance  "
         + (.excluded_from_strategy_performance | tostring)' \
  /tmp/demo.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
jq -r '.lifecycle.reconciliation.positions[]?
       | "  RECONCILED  bought " + (.bought_qty | tostring)
         + " @ " + (.avg_buy_price | tostring)
         + "  sold " + (.sold_qty | tostring)
         + " @ " + (.avg_sell_price | tostring)
         + "  held " + (.held_qty | tostring)
         + "  fees " + (.fees_usd | tostring)
         + "  realised net " + (.realised_net_of_fees_usd | tostring)
         + "  identity " + (.quantity_identity_holds | tostring)' \
  /tmp/demo.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
jq -r '.lifecycle.stages[]? | select(.ran == true)
       | "  cycle " + ((.cycle // 0) | tostring)
         + "  " + (.selected_action // "-")
         + "  qty " + ((.selected_qty // 0) | tostring)
         + "  prints " + ((.prints_applied // 0) | tostring)
         + "/" + ((.prints_offered // 0) | tostring)
         + "  residual after " + ((.inventory_after.residual // 0) | tostring)' \
  /tmp/demo.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true

say ""
say "== D3 . THE DESK, BOOK BY BOOK, NEVER SUMMED =="
curl -sS -H "$H" "$A/api/command/bettor/desk" > /tmp/desk.json \
  || say "  the desk read failed"
jq -r '"  as of           " + (.as_of // "-"),
       "  funded          " + (.funded_submission // "-"),
       "  cycle label     " + (.controls.cycle_label // "-"),
       "  build           " + ((.controls.build_identity.build // "-") | tostring),
       "  verdict         " + (.opportunities.verdict // "-"),
       "  admissible      " + ((.opportunities.admissible_count // 0) | tostring)' \
  /tmp/desk.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
jq -r '.positions.books | to_entries[]?
       | "  BOOK " + .key + "  positions " + ((.value.count // 0) | tostring)
         + "  strategy performance "
         + (.value.counts_toward_strategy_performance | tostring)' \
  /tmp/desk.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
jq -r '.opportunities.no_trade_reasons // {} | to_entries[]?
       | "  BLOCKED AT  " + .key + "   candidates " + (.value | tostring)' \
  /tmp/desk.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
jq -r '"  demonstration counts toward strategy performance  "
       + (.demonstration.counts_toward_strategy_performance | tostring)' \
  /tmp/desk.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true

say ""
say "== D4 . THE EXCLUSION, AT THE CONSUMING QUERY =="
curl -sS -H "$H" \
  "$A/api/command/rn1x/external/entry-evidence?hours=720&limit=50" \
  > /tmp/ee2.json || say "  the entry-evidence read failed"
jq -r '"  evidence is scoped to   " + (.experiment_id // "-"),
       "  inventory rows          " + ((.inventory // []) | length | tostring),
       "  rows from another book  "
         + (((.inventory // []) | map(select(.experiment_id != .experiment_id)) | length) | tostring)' \
  /tmp/ee2.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
jq -r '(.inventory // [])[]
       | "  INVENTORY  " + (.experiment_id // "-") + "  " + (.position_id // "-")' \
  /tmp/ee2.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
if grep -qF "DEMONSTRATION" /tmp/ee2.json; then
  say "  CHECK: the word DEMONSTRATION appears in this read"
else
  say "  the demonstration does NOT appear in the entry lane read"
fi

say ""
say "== D5 . THE CONTROLS, EACH READ BACK =="
curl -sS -H "$H" "$A/api/command/bettor/control" > /tmp/ctl0.json \
  || say "  the control read failed"
jq -r '"  research lane armed     " + ((.state.research_lane_armed.value) | tostring),
       "  funded executor paused  " + ((.state.funded_executor_paused.value) | tostring),
       "  working modelled orders " + ((.state.working_modelled_orders) | tostring),
       "  orders this lane submitted " + ((.state.funded_orders_this_lane_submitted) | tostring),
       "  live_orders rows, all time " + ((.state.live_orders_rows_all_time) | tostring),
       "  account recorded        " + ((.state.account_proposal.name // "-") | tostring),
       "  limits recorded         " + ((.state.limits_proposal.proposed // {}) | tostring)' \
  /tmp/ctl0.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true

say "  -- a pause, then a resume, each confirmed from the row --"
curl -sS -X POST -H "$H" -H 'Content-Type: application/json' \
  -d '{"by":"COMMAND_VERIFY"}' \
  "$A/api/command/bettor/control/pause" > /tmp/ctlp.json || true
jq -r '"  pause  ok " + (.ok | tostring)
       + "  armed_confirmed " + ((.armed_confirmed) | tostring)' \
  /tmp/ctlp.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
curl -sS -X POST -H "$H" -H 'Content-Type: application/json' \
  -d '{"by":"COMMAND_VERIFY","scope":"research"}' \
  "$A/api/command/bettor/control/resume" > /tmp/ctlr.json || true
jq -r '"  resume ok " + (.ok | tostring)
       + "  armed_confirmed " + ((.armed_confirmed) | tostring)
       + "  scope " + (.scope // "-")' \
  /tmp/ctlr.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true

say "  -- the two refusals that must stay refusals --"
FR=$(curl -sS -o /tmp/ctlf.json -w '%{http_code}' -X POST -H "$H" \
       -H 'Content-Type: application/json' \
       -d '{"scope":"funded"}' \
       "$A/api/command/bettor/control/resume" || echo 000)
say "  funded resume            HTTP $FR (expect 409)"
jq -r '"  refusal                  " + (.detail.reason // "-")' \
  /tmp/ctlf.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
# THE GUARD IS THE ROW, SO IT IS EXERCISED BY A REAL id. The
# registry is read first; a display name alone proves only that a
# name is not an identity, which is a different refusal.
curl -sS -H "$H" -o /tmp/reg.json \
  "$A/api/admin/funded-account-registry" || true
jq -r '"  registry accounts        " + ((.count // 0) | tostring),
       "  eligible by their rows   "
         + ((.activation_eligible_by_their_rows // [])
            | join(", ")),
       "  paused on their rows     "
         + ((.paused_accounts // []) | join(", ")),
       "  holds no balances        "
         + ((.holds_no_balances // false) | tostring)' \
  /tmp/reg.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
PAUSED_ID=$(jq -r '(.paused_accounts // [])[0] // ""' \
              /tmp/reg.json 2>/dev/null || echo "")
if [ -n "$PAUSED_ID" ]; then
  say "  submitting the PAUSED account by its canonical id,"
  say "  under a different display name: the row must refuse it."
  curl -sS -X POST -H "$H" -H 'Content-Type: application/json' \
    -d "{\"account\":{\"account_id\":\"$PAUSED_ID\",\"name\":\"Perfectly Fine Desk\",\"venue\":\"PMUS_TEST\"}}" \
    "$A/api/command/bettor/control/account" > /tmp/ctla.json || true
  jq -r '"  paused account by id  ok " + (.ok | tostring)
         + "  refusal " + (.refusal // "-")' \
    /tmp/ctla.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
else
  say "  no paused account in the registry to exercise the guard"
fi
# AND A DISPLAY NAME ALONE IS STILL NOT AN IDENTITY.
curl -sS -X POST -H "$H" -H 'Content-Type: application/json' \
  -d '{"account":{"name":"PMUS ACCOUNTING_UNCERTAIN","venue":"PMUS"}}' \
  "$A/api/command/bettor/control/account" > /tmp/ctlb.json || true
jq -r '"  a name with no id     ok " + (.ok | tostring)
       + "  refusal " + (.refusal // "-")' \
  /tmp/ctlb.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true

say ""
say "  A READ CREDENTIAL MUST NOT WRITE."
NW=$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
       -H 'Content-Type: application/json' -d '{}' \
       "$A/api/command/bettor/control/pause" || echo 000)
say "  control with no operator token   HTTP $NW (expect 401)"
echo '```' >> "$GITHUB_STEP_SUMMARY"

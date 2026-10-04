set -uo pipefail
# `pipefail`, AND IT MATTERS HERE. `jq ... | tee -a` takes tee's
# exit status, so when jq stopped on a type error after the first
# candidate the `||` branch never fired and the error was never
# printed: ONE candidate's evidence appeared out of twenty-five
# and the report looked complete.
# THE ADMIN ROUTES LIVE ON THE API ORIGIN, not on the proxied
# host: netlify.toml proxies only /api/command/*. Addressing the
# proxy returned 404 on every admin POST in run 39, and the tell
# was that the /api/command reads in the same step returned 200.
[ -n "${ADMIN_TOKEN:-}" ] || { echo "::error::ADMIN_TOKEN unset"; exit 1; }
H="X-Admin-Token: $ADMIN_TOKEN"
say() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"; }
echo "### AUTONOMOUS ENTRY LANE, AGAINST PRODUCTION MARKETS" \
  >> "$GITHUB_STEP_SUMMARY"
echo '```' >> "$GITHUB_STEP_SUMMARY"

# 1 · WHICH BUILD IS ANSWERING. Every conclusion below is about
# the deployed identity, so it is read first and printed.
C=$(curl -s -o /tmp/hz.json -w '%{http_code}' --max-time 45 \
    "$API/api/health" || echo 000)
say "health HTTP $C"
# NO ROUTE PUBLISHES THE DEPLOYED COMMIT, so none is claimed here
# -- /api/health returned 404 and the old line printed
# "commit ?", which is honest but pointless. The deployed SHA is
# read from render-ops; what is image-derived and printed below
# is the lane's own limits sha and versions.

# 2 · ARM the control row and let one cycle run.
# 90s, NOT 45. Run 75's arm POST timed out at 45s on the first
# request after a redeploy; `/tmp/ar.json` was then absent, the
# `head` that reports the body exited 1 under `set -uo pipefail`,
# and THE WHOLE STEP DIED -- taking the per-candidate stage
# census, the settlement coverage and the holdings block with
# it. A read that fails must report and continue: the cycle runs
# on the loop's own schedule and does not need this POST.
C=$(curl -s -o /tmp/ar.json -w '%{http_code}' --max-time 90 \
    -X POST -H "$H" "$API/api/admin/ext-pinnacle-shadow/on" \
    || echo 000)
say "arm HTTP $C"
jq -c . /tmp/ar.json 2>/dev/null | tee -a "$GITHUB_STEP_SUMMARY" \
  || head -c 200 /tmp/ar.json 2>/dev/null || true
# 2b · THE ARM IS CONFIRMED FROM THE CONTROL STATE, NOT FROM A
#      STATUS CODE. Making the report above non-fatal is right,
#      and on its own it introduces a worse failure: a timed-out
#      or retried arm would be passed over in silence and the
#      cycle read afterwards presented as the armed lane's
#      output. `armed_requested` is what was asked for;
#      `armed_confirmed` is what the table says afterwards; and
#      `effectively_armed` is the conjunction the loop itself
#      requires -- the control row AND the environment flag.
#      UNCONFIRMED IS NOT ARMED AND IT IS NOT UNARMED EITHER.
AC=$(curl -s -o /tmp/arm_state.json -w '%{http_code}' --max-time 60 \
       -H "$H" "$API/api/admin/ext-pinnacle-shadow" || echo 000)
say "arm-state HTTP $AC"
ARMED=UNCONFIRMED
if [ "$AC" = "200" ]; then
  ARMED=$(jq -r '
    if .effectively_armed == true then "ARMED_CONFIRMED"
    elif .armed_confirmed == null then "UNCONFIRMED"
    elif .armed_confirmed == false then "NOT_ARMED_CONTROL_ROW_FALSE"
    elif .env_flag_set == false then "NOT_ARMED_ENV_FLAG_UNSET"
    else "UNCONFIRMED" end' /tmp/arm_state.json 2>/dev/null \
    || echo UNCONFIRMED)
  jq -c '{control_row_present, control_row_raw, armed_confirmed,
          env_flag_set, effectively_armed}' /tmp/arm_state.json \
    2>/dev/null | tee -a "$GITHUB_STEP_SUMMARY" || true
fi
say "ARM VERDICT  $ARMED"
echo "ARM_VERDICT=$ARMED" >> "$GITHUB_ENV"
case "$ARMED" in
  ARMED_CONFIRMED) : ;;
  *) say "" ;
     say "  THE ARM IS NOT CONFIRMED ($ARMED). Everything below" ;
     say "  is still READ and still REPORTED -- the loop cycles" ;
     say "  on its own schedule -- but it must NOT be described" ;
     say "  as the armed lane's output, and an empty funnel here" ;
     say "  says nothing about the market." ;;
esac
sleep 180

# 3 · EVERY INPUT BEHIND EVERY CANDIDATE. Written to a file and
# run as a program: an interpolated one-liner failed once and
# fell through to a raw dump of the page header.
C=$(curl -s -o /tmp/ee.json -w '%{http_code}' --max-time 90 \
    -H "$H" "$API/api/command/rn1x/external/entry-evidence?hours=6&limit=25" \
    || echo 000)
say "entry-evidence HTTP $C"
if [ "$C" != "200" ]; then
  say "THE READ DID NOT RETURN 200 -- no conclusion follows"
  head -c 400 /tmp/ee.json 2>/dev/null \
    | tee -a "$GITHUB_STEP_SUMMARY" || true
  echo '```' >> "$GITHUB_STEP_SUMMARY"; exit 0
fi
cat > /tmp/ee.jq <<'JQ'
def n(x): if x == null then "-" else (x|tostring) end;
"experiment\t" + n(.experiment_id),
"policy\t" + n(.policy),
"candidates\t" + n(.candidates|length),
"inventory_rows\t" + n(.inventory|length),
"calibration_measured\t" + n(.source_calibration.measured),
"calibration_why\t" + n(.source_calibration.why // .source_calibration.metric),
"limits_sha\t" + n(.risk_declaration.limitsSha),
"",
"== CANDIDATES ==",
(.candidates[] |
  "-- " + n(.condition_id) + "  " + n(.us_market_slug),
  "   selection\t"    + n(.contract_selection) + "  pays_on " + n(.payout_event),
  "   probability\t"  + n(.probability) + "  age_s " + n(.age_s)
                       + "  books " + n(.outcome_books),
  "   price\t"        + n(.executable_price) + "  cost/contract " + n(.cost_per_contract)
                       + "  edge/contract " + n(.estimated_edge_per_contract),
  "   size\t"         + n(.proposed_size) + "  decision " + n(.decision)
                       + "  admissible " + n(.admissible),
  "   p_fill\t"       + n(.execution_estimate.p_fill)
                       + "  basis " + n(.execution_estimate.basis)
                       + "  forecast " + n(.execution_estimate.is_forecast),
  "   walk\t"         + "levels " + n(.execution_estimate.levels_consumed)
                       + "  vwap " + n(.execution_estimate.vwap)
                       + "  best " + n(.execution_estimate.best_acquisition_price)
                       + "  break_even " + n(.execution_estimate.break_even_limit),
  "   notional\t"     + "intended " + n(.execution_estimate.sizing.intendedNotionalUsd)
                       + "  executed " + n(.execution_estimate.sizing.executedEntryNotionalUsd)
                       + "  unfilled " + n(.execution_estimate.sizing.unfilledNotionalUsd)
                       + "  " + n(.execution_estimate.sizing.status),
  "   settlement\t"   + n(.settlement_comparison.compatibility)
                       + "  ctx " + n(.settlement_comparison.quote_context)
                       + "  phase " + n(.settlement_comparison.scope_phase)
                       + "  fmt " + n(.settlement_comparison.scope_game_format)
                       + "  rules_read " + n(.settlement_comparison.venue_rules_read),
  "   fixture\t"      + n(.settlement_comparison.fixture_source)
                       + "  gamePk " + n(.settlement_comparison.fixture_game_pk)
                       + "  at " + n(.settlement_comparison.fixture_retrieved_at),
  "   risk\t"         + "permitted " + n(.risk_verdict.permitted)
                       + "  rails_failed " + n(.risk_verdict.railsNotPassed)
                       + "  gates_failed " + n(.risk_verdict.gatesNotPassed),
  "   waiver\t"       + "authorised " + n(.risk_verdict.research_waiver.authorised)
                       + "  waived " + n(.risk_verdict.research_waiver.waived)
                       + "  why_not " + n(.risk_verdict.research_waiver.refusals),
  "   exposure\t"     + n(.exposure_observed.observed),
  "   REFUSALS\t"     + ((.refusals // []) | join(", ")),
  ""),
"== INVENTORY THIS LANE HOLDS ==",
(if (.inventory|length) == 0 then "  none" else
  (.inventory[] |
    "  " + n(.position_id),
    "    qty " + n(.qty) + " @ " + n(.price)
      + "  basis " + n(.cost_basis_usd) + "  fees " + n(.fees_usd)
      + "  filled " + n(.filled_qty),
    "    decisions " + n(.decisions) + "  orders " + n(.orders)
      + "  outcomes " + n(.outcomes)
      + "  provenance " + n(.provenance)) end)
JQ
sed -i 's/^          //' /tmp/ee.jq
jq -r -f /tmp/ee.jq /tmp/ee.json 2>/tmp/ee.err \
  | tee -a "$GITHUB_STEP_SUMMARY" \
  || { say "THE REPORT FAILED TO RENDER -- this is a reporting"
       say "fault, not a finding. jq said:"
       head -c 300 /tmp/ee.err | tee -a "$GITHUB_STEP_SUMMARY"; }

# 4 · THE CALIBRATION MEASUREMENT, RUN. The join reads back what
# happened; the evaluator scores the recorded point-in-time
# probabilities. A shortfall is the expected first answer and is
# NOT written -- the row is what the gate reads.
# THE JOIN IS PACED, SO THE CLIENT MUST WAIT FOR IT. Each
# unjoined valuation costs one paced venue read; at the default
# limit of sixty that can exceed any 180 s budget, and it did --
# curl gave up and the step printed `HTTP 000000` with no file to
# read. A smaller batch per run and a client timeout that matches
# what the work can take. Collection is incremental by design:
# the next cycle joins the next batch.
: > /tmp/cal.json
C=$(curl -s -o /tmp/cal.json -w '%{http_code}' --max-time 600 \
    -X POST -H "$H" -H 'Content-Type: application/json' \
    --data '{"days": 90, "join_limit": 12}' \
    "$API/api/admin/external-source-calibration" || echo 000)
say ""
say "calibration HTTP $C"
if [ "$C" = "200" ]; then
  cat > /tmp/cal.jq <<'JQ'
def n(x): if x == null then "-" else (x|tostring) end;
"verdict\t"          + n(.verdict),
"gate_opens\t"       + n(.gate_opens),
"-- the declaration, fixed before any data was read --",
"  evaluator\t"      + n(.measurement.version)
                      + "  supersedes " + n(.measurement.supersedes),
"  metric\t"         + n(.acceptance.metric)
                      + "  ceiling " + n(.acceptance.conditions.brier_at_or_below_ceiling)
                      + "  ceiling_is " + n(.acceptance.the_ceiling_is),
"  reference\t"      + n(.acceptance.reference_only.constant_half_brier)
                      + " = " + n(.acceptance.reference_only.what_it_is),
"  min_fixtures\t"   + n(.acceptance.conditions.min_independent_fixtures),
"  calib_bound\t"    + n(.acceptance.conditions.calibration_error_at_or_below),
"  baseline\t"       + n(.acceptance.baseline.fitted_on)
                      + "  uses_eval_outcomes "
                      + n(.acceptance.baseline.fitted_using_evaluation_outcomes),
"  scope\t"          + n(.scope.source_version) + " / "
                      + n(.scope.source_method) + " / "
                      + n(.scope.market) + " / "
                      + n(.scope.families)
                      + "  unit " + n(.scope.independent_unit),
"-- the outcome join --",
"  examined\t"       + n(.outcome_join.examined)
                      + "  resolved " + n(.outcome_join.resolved)
                      + "  void " + n(.outcome_join.void)
                      + "  pending " + n(.outcome_join.pending)
                      + "  unreadable " + n(.outcome_join.unreadable)
                      + "  unmatched " + n(.outcome_join.unmatched),
"  not_settled\t"    + "named_winner " + n(.outcome_join.named_winner)
                      + "  inferred " + n(.outcome_join.inferred)
                      + "  unparseable " + n(.outcome_join.unparseable)
                      + "  side_unknown " + n(.outcome_join.side_unknown),
"  by_status\t"      + n(.outcome_join.by_status),
"  by_class\t"       + n(.outcome_join.by_class),
"-- the measurement --",
"  rows_in_scope\t"  + n(.measurement.rows_in_scope)
                      + "  out_of_scope " + n(.measurement.rows_out_of_scope),
"  fixtures\t"       + n(.measurement.unique_fixtures)
                      + "  payout_statements " + n(.measurement.payout_statements_in_scope)
                      + "  collapsed " + n(.measurement.observations_collapsed),
"  counts\t"         + n(.measurement.outcome_counts),
"  resolved_fix\t"   + n(.measurement.resolved_fixtures),
"  split\t"          + "fit " + n(.measurement.baseline_split.fit_fixtures)
                      + "  eval " + n(.measurement.baseline_split.evaluation_fixtures)
                      + "  total_required "
                      + n(.measurement.baseline_split.total_resolved_fixtures_required),
"  baseline_p\t"     + n(.measurement.baseline.probability)
                      + "  baseline_brier "
                      + n(.measurement.baseline_comparison.baseline_brier)
                      + "  improvement "
                      + n(.measurement.baseline_comparison.paired_mean_improvement)
                      + "  ci [" + n(.measurement.baseline_comparison.ci_low)
                      + ", " + n(.measurement.baseline_comparison.ci_high) + "]",
"  scored\t"         + n(.measurement.scored_events),
"  score\t"          + n(.measurement.score)
                      + "  se " + n(.measurement.uncertainty.brier_standard_error)
                      + "  provisional " + n(.measurement.score_is_provisional),
"  calibration\t"    + "ece " + n(.measurement.calibration.expected_calibration_error)
                      + "  worst_bin " + n(.measurement.calibration.worst_bin_gap),
"  decomposition\t"  + n(.measurement.predictive_score.decomposition),
"  profitability\t"  + "measured_here "
                      + n(.measurement.trading_profitability.measured_here),
"  checks_failed\t"  + n(.measurement.conditions_failed),
"  shortfall\t"      + n(.measurement.shortfall_events)
                      + "  total_fixtures "
                      + n(.measurement.shortfall_resolved_fixtures_total),
"  written\t"        + n(.measurement.written),
"  inputs_sha\t"     + n(.measurement.inputs_sha),
"  why\t"            + n(.measurement.why)
JQ
  sed -i 's/^          //' /tmp/cal.jq
  jq -r -f /tmp/cal.jq /tmp/cal.json 2>/tmp/cal.err \
    | tee -a "$GITHUB_STEP_SUMMARY" \
    || { say "THE CALIBRATION REPORT FAILED TO RENDER -- a"
         say "reporting fault, not a finding:"
         head -c 300 /tmp/cal.err | tee -a "$GITHUB_STEP_SUMMARY"; }
else
  say "THE CALIBRATION RUN DID NOT RETURN 200 -- no conclusion"
  head -c 300 /tmp/cal.json | tee -a "$GITHUB_STEP_SUMMARY"
fi

# 4b · SETTLEMENT FROM THE VENUE'S OWN RESOLUTION.
# Every open shadow position -- the entry lane's AND the
# acceptance position -- is asked of the venue. The answer is
# either completed accounting or the EXACT pending/unreadable
# state, never "probably not settled yet". An MLB feed reporting
# "Final" is not part of this read, and no bookmaker quote is
# needed: a finished contract's value is the settlement price.
: > /tmp/st.json
C=$(curl -s -o /tmp/st.json -w '%{http_code}' --max-time 300 \
    -X POST -H "$H" -H 'Content-Type: application/json' \
    --data '{"limit": 25}' \
    "$API/api/admin/rn1x-settle-open-positions" || echo 000)
say ""
say "## settlement from the venue (HTTP $C)"
# THE RAW PAYLOAD, UNCONDITIONALLY, BEFORE ANY FORMATTING.
# THIS IS THE THIRD TIME a jq type mismatch has hidden a 200
# response from me -- `input_chain`, then `alternatives`, now
# this: `Cannot index string with string "why"`. Each time the
# sweep RAN and I could not say what it returned. A report that
# can fail must never be the only copy of the evidence.
# THE SETTLEMENT OBJECT AND EVERY RESULT, DUMPED COMPACT AND
# UNFORMATTED. The 1800-byte head landed inside `consumer` and
# never reached `settlement`, so the one thing I needed was the
# one thing not printed -- my fourth pass over this reporting
# path. These two lines index NOTHING, so no type can break
# them, and they are the authoritative copy of the evidence.
say "-- settlement, compact --"
jq -c '.settlement | del(.results)' /tmp/st.json 2>&1 \
  | head -c 1200 | tee -a "$GITHUB_STEP_SUMMARY"
say ""
say "-- each result object, one per line --"
jq -c '.settlement.results[]?' /tmp/st.json 2>&1 \
  | head -c 3000 | tee -a "$GITHUB_STEP_SUMMARY"
say ""
say "-- types, so a mismatch names itself --"
jq -r '"root " + (type)
       + "  settlement " + (.settlement|type)
       + "  results " + ((.settlement.results // null)|type)
       + "  results[0] " + (((.settlement.results // [])[0]
                              // null)|type)' \
  /tmp/st.json 2>&1 | head -3 | tee -a "$GITHUB_STEP_SUMMARY"
if [ "$C" = "200" ]; then
  cat > /tmp/st.jq <<'JQ'
def n(x): if x == null then "-" else (x|tostring) end;
"needs_odds\t"       + n(.consumer.requires_fresh_bookmaker_odds),
"submits\t"          + n(.consumer.submits_orders),
"examined\t"         + n(.settlement.examined)
                      + "  settled " + n(.settlement.settled)
                      + "  void " + n(.settlement.void)
                      + "  already " + n(.settlement.already)
                      + "  unresolved " + n(.settlement.unresolved)
                      + "  errors " + n(.settlement.errors),
"by_status\t"        + n(.settlement.by_status),
"experiments\t"      + n(.settlement.experiments),
"policies\t"         + n(.settlement.policies),
"-- one line per open position --",
# PARENTHESISED, AND THAT WAS THE WHOLE BUG. `|` binds LOOSER
# than `,` in jq, so without these parentheses the entire comma
# sequence above was piped into the formatter -- the literal
# string "needs_odds\t..." had `.position_id` applied to it, and
# jq reported the error against the last field it tried, `why`.
# Reproduced locally against the real payload before this edit.
((.settlement.results // [])[]
  | "  " + n(.position_id)
    + "\n     slug " + n(.us_market_slug)
    + "  venue " + n(.venue_status) + "/" + n(.venue_class)
    + "  read " + n(.settlement_read)
    + "\n     side " + n(.side_map)
    + "  own_decision " + n(.identity_is_the_own_decision)
    + "\n     STATUS " + n(.status) + "  written " + n(.written)
    + "\n     net " + n(.accounting.net_usd)
    + "  cash " + n(.accounting.realized_cash_usd)
    + "  residual " + n(.accounting.residual_qty)
    + "\n     why " + n(.why))
JQ
  sed -i 's/^          //' /tmp/st.jq
  jq -r -f /tmp/st.jq /tmp/st.json 2>/tmp/st.err \
    | tee -a "$GITHUB_STEP_SUMMARY" \
    || { say "THE SETTLEMENT REPORT FAILED TO RENDER -- a"
         say "reporting fault, not a finding:"
         head -c 300 /tmp/st.err | tee -a "$GITHUB_STEP_SUMMARY"; }
else
  say "THE SETTLEMENT SWEEP DID NOT RETURN 200 -- no conclusion"
  head -c 400 /tmp/st.json | tee -a "$GITHUB_STEP_SUMMARY"
fi

# 5 · THE CENSUS BESIDE IT, so a cycle in which nothing was even
# a candidate is distinguishable from one in which everything
# refused.
C=$(curl -s -o /tmp/cx.json -w '%{http_code}' --max-time 60 \
    -H "$H" "$API/api/command/rn1x/external/census?hours=6" || echo 000)
say ""
say "census HTTP $C"
# THE REFUSAL TOTALS, THEN WHERE EACH CANDIDATE ACTUALLY STOPPED.
#
# WHY THE SECOND BLOCK EXISTS, AND IT CORRECTS A CLAIM OF MINE. I
# reported that every candidate lacked profitable depth. Missing
# walk/VWAP fields do not establish that: a candidate refused at
# PROBABILITY, FRESHNESS, IDENTITY or SETTLEMENT SCOPE never reached
# execution estimation at all, so it has no walk because it was never
# priced -- not because the book was thin. `stages` attributes each
# refused candidate to its EARLIEST failing stage and counts the
# negative-edge cases with and without a walk separately.
jq -r '"evaluated\t"  + ((.summary.evaluated  // 0)|tostring),
       "admissible\t" + ((.summary.admissible // 0)|tostring),
       "refused\t"    + ((.summary.refused    // 0)|tostring),
       "-- refusals --",
       (.refusals // {} | to_entries[] | "  \(.key)\t\(.value)")' \
  /tmp/cx.json 2>/dev/null | tee -a "$GITHUB_STEP_SUMMARY" \
  || head -c 300 /tmp/cx.json
say ""
# THE WINDOW'S OWN TOTALS FIRST. `evaluated` above is ALL TIME and the
# stage counts are windowed; printing them adjacent with no label made
# 63 + 15 = 78 look like it had lost 308 rows against an all-time 386.
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
       "all_time_evaluated  " + n(.summary.evaluated)
         + "  (" + n(.summary_window) + ")",
       "in_window           " + n(.summary_in_window.evaluated)
         + " evaluated  " + n(.summary_in_window.admissible)
         + " admissible  " + n(.summary_in_window.refused) + " refused",
       "reconciles          " + n(.stages.reconciles_with_window)
         + "  every refused candidate in the window lands in one stage"' \
  /tmp/cx.json 2>/dev/null | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "-- stage attribution, raw --"
jq -c '.stages' /tmp/cx.json 2>&1 | head -c 1500 \
  | tee -a "$GITHUB_STEP_SUMMARY"
say ""
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
       if (.stages.computed == false) then
         "stages\tNOT COMPUTED: " + n(.stages.error)
       else
         "reached_execution_estimate\t"
           + n(.stages.reached_execution_estimate)
           + "  walk_took_levels " + n(.stages.walk_took_levels),
         "negative_edge_WITH_a_walk\t"
           + n(.stages.negative_edge_with_a_walk),
         "negative_edge_WITHOUT_a_walk\t"
           + n(.stages.negative_edge_without_a_walk),
         "-- first failing stage, per candidate --",
         ((.stages.by_first_stage // {}) | to_entries[]
           | "  " + .key + "\t" + n(.value))
       end' /tmp/cx.json 2>/dev/null \
  | tee -a "$GITHUB_STEP_SUMMARY" || true
echo '```' >> "$GITHUB_STEP_SUMMARY"

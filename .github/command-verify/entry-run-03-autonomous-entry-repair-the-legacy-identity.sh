set -uo pipefail
[ -n "${ADMIN_TOKEN:-}" ] || { echo "::error::ADMIN_TOKEN unset"; exit 1; }
H="X-Admin-Token: $ADMIN_TOKEN"
say() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"; }
echo '```' >> "$GITHUB_STEP_SUMMARY"
# 4a-bis · THE LEGACY POSITION'S IDENTITY, RE-ESTABLISHED FIRST.
#
# THE PRODUCTION REFUSAL THIS ADDRESSES, verbatim from the previous
# run: THE_ONLY_AVAILABLE_IDENTITY_DESCRIBES_A_DIFFERENT_OUTCOME --
# the acceptance position holds 'Arizona Diamondbacks', the two
# recorded identities for its condition describe ['Colorado Rockies',
# 'None'], and the consumer refused rather than borrow the other
# side's slug. That refusal is CORRECT and it occurs BEFORE the venue
# is asked anything, so venue settlement stayed untested for this
# position.
#
# The repair re-derives the binding from `premap.resolve` -- asked for
# the outcome the GLOBAL CATALOGUE lists at this position's own index
# -- cross-checks it five ways, and writes only NULL identity columns.
# It never reads `external_valuations`, never touches provenance and
# never reseeds: the position stays synthetic, modelled and unfunded.
# It runs HERE, immediately before the sweep, so the repair and the
# readback are one run against one build.
# WHICH POSITION, AND IT IS NO LONGER A LITERAL. THE DEFECT THIS
# FIXES: this step was pinned to the ARIZONA id while the gate and
# the settlement sweep act on whichever acceptance position was
# most recently adopted -- Houston today. So the audited repair ran
# against a position nobody was judging, and the one being judged
# kept the binding that refused. The subject is now RESOLVED from
# the newest row under the acceptance policy, and Arizona is
# repaired too rather than instead.
ARIZONA_PID="RN1X_SHADOW_CHALLENGER_HOLD_RANKED_V1:ACCEPTANCE_SHADOW_MANAGER_DEMO_V1:-105276210"
: > /tmp/accpos.json
CP=$(curl -s -o /tmp/accpos.json -w '%{http_code}' --max-time 60 \
    -H "$H" "$API/api/command/rn1x/positions?limit=50&policy=ACCEPTANCE_SHADOW_MANAGER_DEMO_V1" \
    || echo 000)
SUBJ=$(jq -r '(.positions // [])[0].position_id // ""' \
       /tmp/accpos.json 2>/dev/null || echo "")
say ""
say "## the acceptance positions, newest first (HTTP $CP)"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  (.positions // [])[] | "  " + n(.position_id)
    + "  policy " + n(.policy)
    + "  opened " + n(.opened_at // .created_at)
    + "  qty " + n(.qty) + "  filled " + n(.filled_qty)' \
  /tmp/accpos.json 2>&1 | head -20 \
  | tee -a "$GITHUB_STEP_SUMMARY" || true
say "  selected_subject\t${SUBJ:-<none>}"
say "  arizona\t$ARIZONA_PID"
say ""
say "## the audited repair, on the SELECTED SUBJECT then Arizona"
# A FUNCTION, NOT A LOOP INSIDE A YAML BLOCK. Two calls, two file
# pairs, so the recap can print both instead of whichever ran last.
repair () {   # $1 = position id, $2 = file suffix
  local P="$1" SFX="$2"
  [ -n "$P" ] || { say "  (no id for suffix '$SFX')"; return 0; }
  say ""
  say "  position\t$P"
  : > "/tmp/rep0$SFX.json"
  local D W
  D=$(curl -s -o "/tmp/rep0$SFX.json" -w '%{http_code}' --max-time 120 \
      -X POST -H "$H" -H 'Content-Type: application/json' \
      --data "$(jq -nc --arg p "$P" '{position_id:$p,dry_run:true}')" \
      "$API/api/admin/rn1x-repair-position-identity" || echo 000)
  say "  -- dry run (HTTP $D), raw --"
  head -c 2000 "/tmp/rep0$SFX.json" | tee -a "$GITHUB_STEP_SUMMARY"
  say ""
  : > "/tmp/rep$SFX.json"
  W=$(curl -s -o "/tmp/rep$SFX.json" -w '%{http_code}' --max-time 120 \
      -X POST -H "$H" -H 'Content-Type: application/json' \
      --data "$(jq -nc --arg p "$P" '{position_id:$p}')" \
      "$API/api/admin/rn1x-repair-position-identity" || echo 000)
  say "  -- write (HTTP $W), raw --"
  head -c 2500 "/tmp/rep$SFX.json" | tee -a "$GITHUB_STEP_SUMMARY"
  say ""
  # THE HELD EXPOSURE AND THE VENUE BINDING, VERIFIED INDEPENDENTLY
  # of the repair's own answer: the trace's own view of what this
  # position holds, read through a different route.
  : > "/tmp/tr$SFX.json"
  local T
  T=$(curl -s -o "/tmp/tr$SFX.json" -w '%{http_code}' --max-time 90 \
      -H "$H" "$API/api/command/rn1x/trace/$P" || echo 000)
  say "  -- trace (HTTP $T): the last two decisions and the holding --"
  jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
    "    qty " + n(.position.qty) + "  filled " + n(.position.filled_qty)
      + "  residual " + n(.position.residual_qty)
      + "  basis " + n(.position.cost_basis_usd),
    "    outcome_index " + n(.position.outcome_index)
      + "  payout_event " + n(.position.payout_event)
      + "  slug " + n(.position.venue_market_slug),
    "    decisions " + n((.decisions // []) | length)
      + "  orders " + n((.orders // []) | length)
      + "  fills " + n((.fills // []) | length)
      + "  outcomes " + n((.outcomes // []) | length),
    (((.decisions // []) | sort_by(.decision_ts) | reverse
       | .[0:2])[]
      | "    decision " + n(.decision_id // .id)
        + "  at " + n(.decision_ts)
        + "  action " + n(.action)
        + "  version " + n(.engine_version // .version)
        + "  policy " + n(.policy)
        + "  settlement " + n(.settlement_qualified
                              // .settlement_compatibility)
        + "  complete " + n(.evidence_complete // .complete)),
    "    outcome rows " + n([(.outcomes // [])[]
      | {status: (.status // .settlement_read), net: .net_usd}])' \
    "/tmp/tr$SFX.json" 2>&1 | head -30 \
    | tee -a "$GITHUB_STEP_SUMMARY" || true
}
repair "${SUBJ:-}" ""
[ "${SUBJ:-}" = "$ARIZONA_PID" ] || repair "$ARIZONA_PID" "_az"

if [ -s /tmp/rep.json ] || [ -s /tmp/rep_az.json ]; then
  cat > /tmp/rep.jq <<'JQ'
def n(x): if x == null then "-" else (x|tostring) end;
"ok\t"            + n(.repair.ok)
                    + "  written " + n(.repair.written)
                    + "  refusal " + n(.repair.refusal),
"why\t"           + n(.repair.why),
"held_outcome\t"  + n(.repair.held_outcome)
                    + "  token " + n(.repair.held_token_id)
                    + "  index " + n(.repair.outcome_index),
"catalogue\t"     + n(.repair.catalogue),
"resolver\t"      + n(.repair.resolver),
"identity\t"      + n(.repair.identity),
"provenance\t"    + n(.repair.provenance)
                    + "  reseeds " + n(.repair.reseeds)
                    + "  reads_valuations "
                    + n(.repair.reads_external_valuations),
"-- the cross-checks, each with its own verdict --",
((.repair.cross_checks // {}) | to_entries[]
  | "  " + .key + "\t" + n(.value.passed) + "\t" + n(.value.why))
JQ
  sed -i 's/^  //' /tmp/rep.jq
  for F in /tmp/rep.json /tmp/rep_az.json; do
    [ -s "$F" ] || continue
    say "-- formatted: $F --"
    jq -r -f /tmp/rep.jq "$F" 2>/tmp/rep.err \
      | tee -a "$GITHUB_STEP_SUMMARY" || true
  done
  # A jq FAULT IS ANNOUNCED AS ONE. The loop above swallows a
  # failure per file so one bad payload cannot hide the other, so
  # the error file is checked here instead.
  if [ -s /tmp/rep.err ]; then
    say "A REPAIR REPORT FAILED TO RENDER -- a reporting fault,"
    say "not a finding. The raw payloads are above. jq said:"
    head -c 300 /tmp/rep.err | tee -a "$GITHUB_STEP_SUMMARY"
  fi
else
  say "NEITHER REPAIR CALL LEFT A RESPONSE -- no identity was"
  say "written and the sweep below will refuse as it did before."
fi

echo '```' >> "$GITHUB_STEP_SUMMARY"

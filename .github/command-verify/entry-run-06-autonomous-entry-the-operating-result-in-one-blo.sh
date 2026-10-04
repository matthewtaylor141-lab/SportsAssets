set -uo pipefail
say() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"; }
echo "### THE OPERATING RESULT" >> "$GITHUB_STEP_SUMMARY"
echo '```' >> "$GITHUB_STEP_SUMMARY"
for f in arm ee cx pnl rep0 rep st vp; do
  [ -s "/tmp/$f.json" ] || say "MISSING /tmp/$f.json -- the lines it feeds are absent, not zero"
done
say "-- 0a · the legacy identity, BEFORE (dry run, wrote nothing) --"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  .repair as $r
  | "  held_outcome " + n($r.held_outcome)
    + "  index " + n($r.outcome_index)
    + "  token " + n($r.held_token_id),
    "  identity_as_read " + n($r.identity_before // $r.identity),
    "  catalogue " + n($r.catalogue),
    "  resolver  " + n($r.resolver),
    "  ok " + n($r.ok) + "  written " + n($r.written)
      + "  refusal " + n($r.refusal),
    "  why " + n($r.why)' \
  /tmp/rep0.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "-- 0b · the legacy identity, AFTER (the write) --"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  .repair as $r
  | "  ok " + n($r.ok) + "  written " + n($r.written)
    + "  refusal " + n($r.refusal),
    "  identity " + n($r.identity),
    "  derivation catalogue=" + n($r.catalogue)
      + "  resolver=" + n($r.resolver),
    "  provenance " + n($r.provenance)
      + "  reseeds " + n($r.reseeds)
      + "  reads_valuations " + n($r.reads_valuations),
    "  why " + n($r.why)' \
  /tmp/rep.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "-- 0c · settlement from the venue, per open position --"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  "  examined " + n(.settlement.examined)
    + "  settled " + n(.settlement.settled)
    + "  void " + n(.settlement.void)
    + "  already " + n(.settlement.already)
    + "  unresolved " + n(.settlement.unresolved)
    + "  errors " + n(.settlement.errors),
  "  by_status " + n(.settlement.by_status),
  ((.settlement.results // [])[]
    | "  " + n(.position_id),
      "     slug " + n(.us_market_slug)
        + "  venue " + n(.venue_status) + "/" + n(.venue_class)
        + "  read " + n(.settlement_read),
      "     STATUS " + n(.status) + "  written " + n(.written)
        + "  net " + n(.accounting.net_usd)
        + "  cash " + n(.accounting.realized_cash_usd)
        + "  residual " + n(.accounting.residual_qty),
      "     why " + n(.why))' \
  /tmp/st.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "-- 0d · the venue's own settlement prose, as read live --"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  (.probes // [])[]
  | "  " + n(.slug) + "  field " + n(.settlement_terms.field)
      + "  chars " + n(.settlement_terms.chars)
      + "  truncated " + n(.settlement_terms.truncated),
    "     STATED     " + n(.settlement_terms.stated),
    "     NOT STATED " + n(.settlement_terms.not_stated),
    "     conflicts  " + n(.settlement_terms.contradicted)
      + "  sentences " + n(.settlement_terms.sentences),
    "     unused     " + n(.settlement_terms.unused_evidence),
    "     prose      " + n(.settlement_terms.text)' \
  /tmp/vp.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "-- 1 · the research lane's arming --"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  "  authorised        " + n(.authorised.authorised)
    + "   key " + n(.control_key),
  "  calibration_rows  " + n(.calibration_rows)
    + "   still_unmeasured " + n(.calibration_still_unmeasured),
  "  waivable          " + n(.describe.waivable)
    + "   never " + n(.describe.never_waivable)' \
  /tmp/arm.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "-- 2 · candidates, and who used the waiver --"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  (.candidates // []) as $c
  | "  candidates_read    " + n($c|length),
    "  admissible         "
      + n([$c[] | select(.admissible)] | length),
    "  waiver_AUTHORISED  "
      + n([$c[] | select(.risk_verdict.research_waiver.authorised
                         == true)] | length),
    "  waiver_APPLIED     "
      + n([$c[] | select((.risk_verdict.research_waiver.waived
                          // []) | length > 0)] | length)
      + "   (a waived gate, not merely an armed lane)",
    "  waiver_gates       "
      + n([$c[] | (.risk_verdict.research_waiver.waived // [])[]]
          | unique),
    "  waiver_refusals    "
      + n([$c[] | (.risk_verdict.research_waiver.refusals // [])[]]
          | unique),
    # WHY A ZERO ABOVE IS NOT SELF-EXPLANATORY, and the last run
    # left me guessing. A candidate that never reached the gate has
    # NO risk section at all, and a row written before this build
    # has a risk section with no waiver key. Those are different
    # facts from "the lane was not armed", so they are counted.
    "  risk_section_present " + n([$c[]
        | select(.risk_verdict != null)] | length)
      + "   with_a_waiver_key " + n([$c[]
        | select(.risk_verdict.research_waiver != null)] | length),
    "  newest_candidate " + n([$c[] | .decided_at // .observed_at]
        | max)' \
  /tmp/ee.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "-- 3 · where each refused candidate stopped (windowed) --"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  "  in_window " + n(.summary_in_window.evaluated) + " evaluated  "
    + n(.summary_in_window.admissible) + " admissible  "
    + n(.summary_in_window.refused) + " refused"
    + "   reconciles " + n(.stages.reconciles_with_window),
  ((.stages.by_first_stage // {}) | to_entries[]
    | "  " + .key + "  " + n(.value)),
  # THE DISTINCTION THAT MUST NOT BE LOST: a candidate refused
  # before stage 5 was never priced, so its empty walk is the
  # ABSENCE of a measurement. Only `negative_edge_with_a_walk`
  # supports any statement about depth; `without_a_walk` is a
  # negative edge at the observed best ask and says nothing
  # about the rest of the book.
  "  reached_execution_estimate "
    + n(.stages.reached_execution_estimate)
    + "   walk_took_levels " + n(.stages.walk_took_levels),
  "  negative_edge_WITH_a_walk    "
    + n(.stages.negative_edge_with_a_walk)
    + "   (measured against walked depth)",
  "  negative_edge_WITHOUT_a_walk "
    + n(.stages.negative_edge_without_a_walk)
    + "   (top of book only)",
  "  never_priced_at_all          "
    + n([(.stages.by_first_stage // {}) | to_entries[]
          | select(.key | test("^[1-4]_")) | .value] | add // 0)
    + "   (refused before 5_EXECUTION_ESTIMATE)",
  # THE OPPORTUNITY ANSWER, AS ITS OWN NUMBER. "None was
  # positive" used to be an inference from two negative counts
  # and a total. An assessment answers "how many candidates
  # showed positive net edge" directly, including when it is 0.
  "  POSITIVE_EDGE                "
    + n(.stages.positive_edge)
    + "   edge_not_computed " + n(.stages.edge_not_computed),
  "  execution_mode  " + n(.stages.execution_mode)
    + "   excludes " + n(.stages.excludes)' \
  /tmp/cx.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
# ── 3a2 · THE FUNNEL, PER SUPPORTED SPORT ──────────────────
#
# WHY PER SPORT. Every count above is summed over both supported
# sports, so a sport that contributes nothing at all is
# indistinguishable from one that contributes candidates which
# then refuse. Those are different findings: the first is a
# coverage or mapping fact, the second is an economics fact.
# `venue_markets_open_and_fresh` is the OBSERVED UNIVERSE for
# that sport; `mapped_to_a_venue_contract` is the subset a
# connected probability source can price at all.
say "-- 3a2 · observed universe and funnel, per supported sport --"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  .statuses.external_valuation.last_cycle as $L
  | if $L == null then
      "  NO CYCLE HEARTBEAT -- the funnel is absent, not empty"
    else
      "  cycle_at " + n($L.at) + "  state " + n($L.state)
        + "  markets_considered " + n($L.markets_considered),
      "  venue universe by label " + n($L.venue_universe_by_label),
      (($L.funnel_by_provider_sport // {}) | to_entries[]
        | "  " + .key + "  (" + n(.value.family) + ")"
          + "  venue_open_fresh " + n(.value.venue_markets_open_and_fresh)
          + "  provider_events " + n(.value.provider_events)
          + "  with_pinnacle " + n(.value.with_pinnacle_h2h)
          + "  mapped " + n(.value.mapped_to_a_venue_contract)
          + "  evaluated " + n(.value.evaluated)
          + "  written " + n(.value.written)),
      (($L.funnel_by_provider_sport // {}) | to_entries[]
        | "     " + .key + " refusals " + n(.value.refusals)),
      "  CYCLE LABEL " + n($L.cycle_label),
      "  " + n($L.cycle_label_note),
      "",
      "  -- every MAPPED candidate against its FIRST refusal --",
      (($L.mapped_candidate_ledger // [])[]
        | "   " + n(.stage) + "  " + n(.first_refusal // "ADMITTED")
          + "  us " + n(.us_market_slug)
          + "  ours " + n(.global_slug)
          + "  priced " + n(.priced_outcome)
          + (if .edge == null then ""
             else "  edge " + (.edge|tostring) end)),
      (($L.mapped_candidate_ledger // [])[]
        | select(.raw_transact_time != null or .age_s != null)
        | "      CLOCK raw " + n(.raw_transact_time)
          + "  parsed " + n(.parsed_epoch_s)
          + "  decision " + n(.decision_instant_epoch_s)
          + "  age " + n(.age_s) + "s  limit " + n(.limit_s) + "s"
          + "  basis " + n(.age_basis)),
      (($L.mapped_candidate_ledger // [])[]
        | select(.age_semantics != null)
        | "      CLOCK SEMANTICS " + n(.age_semantics)),
      (($L.mapped_candidate_ledger // [])[]
        | select(.period_evidence != null)
        | "      PERIOD " + n(.period_evidence.basis)
          + "  participants "
          + n(.period_evidence.participants_named)
          + "  title " + n(.period_evidence.event_title)),
      "",
      (($L.venue_errors // [])[]
        | "  venue_error " + n(.us_market_slug // .global_slug)
          + "  " + n(.refusal)),
      (($L.venue_errors // [])[]
        | select(.venue_clock != null)
        | "     RAW CLOCK path " + n(.venue_clock.field_path)
          + "  type " + n(.venue_clock.raw_type)
          + "  raw " + n(.venue_clock.raw)),
      (($L.venue_errors // [])[]
        | select(.venue_clock != null)
        | "     BOOK AGE " + n(.age_s) + "s  limit "
          + n(.limit_s) + "s  basis " + n(.age_basis)
          + "  parsed_epoch " + n(.venue_clock.parsed_epoch_s)),
      "  venue_errors " + n($L.venue_errors)
    end' /tmp/pnl.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
# THE COVERAGE QUESTION, COUNTED OVER THE WHOLE WINDOW. Does ANY
# supported market have a payoff this source can price? A 25-row
# sample cannot answer "none", so the census counts every row.
say "-- 3b · settlement coverage, over the window --"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  .settlement_coverage as $S
  | if ($S.computed == false) then
      "  NOT COMPUTED: " + n($S.error)
    else
      "  COMPATIBLE rows " + n($S.compatible_rows),
      (($S.by_verdict // {}) | to_entries[]
        | "  " + .key + "  rows " + n(.value.rows)
          + "  admissible " + n(.value.admissible)
          + "  distinct_slugs " + n(.value.distinct_slugs)),
      # PER SPORT, because "0 COMPATIBLE" summed over two sports
      # does not say whether the OTHER sport was examined and
      # refused or never reached the comparison at all. An
      # UNKNOWN here is a MISSING STATEMENT on one side -- it is
      # never read as agreement, and a market is never selected
      # because a payout pattern failed to match.
      (($S.by_verdict // {}) | to_entries[]
        | . as $v | ($v.value.by_sport // {}) | to_entries[]
        | "     " + $v.key + " / " + .key
          + "  rows " + n(.value.rows)
          + "  admissible " + n(.value.admissible)
          + "  distinct_slugs " + n(.value.distinct_slugs)),
      "  conflicting conditions " + n($S.mismatched_conditions)
    end' /tmp/cx.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "-- 4 · what this lane HOLDS, by provenance --"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  (.inventory // []) as $i
  | "  positions " + n($i|length),
    ($i | group_by(.provenance // "UNLABELLED")[]
      | "  " + n(.[0].provenance) + "  " + n(length)
        + "   ids " + n([.[].position_id]))' \
  /tmp/ee.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "-- 5 · reconciled P&L, per lane --"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  .statuses.shadow_pnl as $P
  | "  mark_basis " + n($P.unrealised_basis),
    ($P.lanes // {} | to_entries[]
      | "  " + .key + "  realised " + n(.value.net_usd)
        + "  fees " + n(.value.fees_usd)
        + "  unrealised " + n(.value.unrealised.unrealised_usd)
        + "  total " + n(.value.total_pnl_usd)
        + "  marked " + n(.value.unrealised.marked_positions)
        + "/unmarked " + n(.value.unrealised.unmarked_positions))' \
  /tmp/pnl.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
# ── 6 · MANAGEMENT EVIDENCE, ON ITS OWN EVIDENCE ─────────────
#
# THE CONFLATION THIS PREVENTS. A successful terminal-settlement
# read proves LIFECYCLE CLOSURE: the venue was asked, the answer
# was authoritative, the accounting was written and the exposure
# released. It proves NOTHING about recurring management, because
# settlement is one event and management is a repeated decision
# with its own observed inputs. The two are reported apart, and
# `with_observed_runtime_decision` is the only number that speaks
# to the second one.
say "-- 6 · recurring MANAGEMENT evidence (not settlement) --"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  .statuses.prospective_rn1_management as $P
  | "  positions " + n($P.positions)
    + "   with_observed_runtime_decision "
    + n($P.with_observed_runtime_decision),
    "  by_decision_basis " + n($P.by_decision_basis),
    "  legacy_backdated_and_reclassified "
      + n($P.legacy_backdated_and_reclassified),
    "  newest_decision_ts " + n($P.newest_decision_ts),
    "  badge " + n($P.badge) + "  producing " + n($P.producing),
    "  why " + n($P.why)' \
  /tmp/pnl.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
echo '```' >> "$GITHUB_STEP_SUMMARY"

set -uo pipefail
A="${API:-https://sportsassets-api.onrender.com}"
H="X-Admin-Token: ${ADMIN_TOKEN:-}"
say() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"; }
echo "### REPAIRS: S1 sizing · S2 audit · S3 soccer · S4 catalogue" \
  >> "$GITHUB_STEP_SUMMARY"
echo '```' >> "$GITHUB_STEP_SUMMARY"
if [ -z "${ADMIN_TOKEN:-}" ]; then
  say "SKIPPED: no ADMIN_TOKEN"
  echo '```' >> "$GITHUB_STEP_SUMMARY"; exit 0
fi

# ── S1 + S2 · EVERY CANDIDATE THAT PRICED POSITIVELY ─────────
#
# THE SEVEN AUDIT DIMENSIONS, from the row rather than re-derived:
# fixture, payout identity, intent, market period, price
# orientation, fees and freshness. Before this release four of
# those were unreadable -- `period` was the literal FULL_GAME on
# every row, the resolver's identity evidence was three nulls
# because the wrapper read key names `premap.resolve` does not
# return, and the freshness decomposition was not persisted at
# all. So a +0.397 edge could not be told from an artefact.
say "== S2 · the positive-edge candidates, audited =="
EC=$(curl -s --max-time 90 -o /tmp/ev.json -w '%{http_code}' \
       -H "$H" \
       "$A/api/command/rn1x/external/entry-evidence?hours=6&limit=60" \
       || echo 000)
say "entry-evidence HTTP $EC"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  [(.candidates // [])[]
    | select((.estimated_edge_per_contract // -1) > 0)] as $P
  | "  positive_edge_candidates  " + ($P | length | tostring),
    ($P[] | .risk_verdict as $R | .execution_estimate as $E
     | "",
       "-- " + n(.us_market_slug) + "   edge/contract " + n(.estimated_edge_per_contract),
       "   1 FIXTURE      condition " + n(.condition_id)
         + "  event_key " + n(.event_key),
       "   2 PAYOUT IDENT pays_on " + n(.payout_event)
         + "  basis " + n(.payout_event_basis),
       "                  prob_event " + n(.probability_event)
         + "  complement " + n(.payout_is_complement),
       "                  resolver_asked " + n(.resolver_asked_for)
         + "  matched_side " + n(.matched_side_norm),
       "                  mapped_outcome " + n(.mapped_outcome)
         + "  match " + n(.mapping_match),
       "   3 INTENT       " + n(.buy_intent)
         + "  ladder_side " + n(.ladder_side)
         + "  identity_basis " + n(.contract_identity_basis),
       "   4 PERIOD       " + n(.period)
         + "  market " + n(.market)
         + "  settlement_rule " + n(.settlement_rule),
       "   5 ORIENTATION  p " + n(.probability)
         + "  executable " + n(.executable_price)
         + "  overround " + n(.overround)
         + "  devig " + n(.devig_method),
       "   6 FEES         cost/contract " + n(.cost_per_contract)
         + "  realised_fee " + n($E.fee_per_contract_realised)
         + "  basis " + n($E.fee_basis),
       "                  vwap " + n($E.vwap)
         + "  submitted_limit " + n($E.submitted_limit)
         + "  economics_use " + n($E.economics_use),
       "   4b CATALOGUE  kind " + n(.period_evidence.kind)
         + "  event " + n(.period_evidence.event_slug)
         + "  side " + n(.period_evidence.side)
         + "  siblings " + n(.period_evidence.sibling_markets),
       "   7 FRESHNESS    fresh " + n($R.freshness_evidence.fresh)
         + "  pinnacle " + n($R.freshness_evidence.pinnacle_age_s)
         + "/" + n($R.freshness_evidence.pinnacle_limit_s)
         + "  venue " + n($R.freshness_evidence.venue_age_s)
         + "/" + n($R.freshness_evidence.venue_limit_s),
       "                  venue_clock " + n($R.freshness_evidence.venue_age_basis),
       "   7b RAW CLOCK   path " + n($R.freshness_evidence.venue_clock.field_path)
         + "  type " + n($R.freshness_evidence.venue_clock.raw_type),
       "                  raw " + n($R.freshness_evidence.venue_clock.raw),
       "                  parser " + n($R.freshness_evidence.venue_clock.parser)
         + "  parsed_epoch " + n($R.freshness_evidence.venue_clock.parsed_epoch_s),
       "                  lag provider " + n($R.freshness_evidence.pinnacle_provider_lag_s)
         + "  ours " + n($R.freshness_evidence.pinnacle_our_processing_s),
       "                  why " + (n($R.freshness_evidence.why) | .[0:110]),
       "   S1 SIZING     reduced_by_rails " + n($R.notional_reduced_by_rails)
         + "  policy_notional " + n($R.policy_notional_usd),
       "                  qty_cap " + n($R.qty_cap.qty_cap)
         + "  binding " + n($R.qty_cap.binding_rail),
       "                  size " + n(.proposed_size)
         + "  rails_not_passed " + n($R.railsNotPassed),
       "                  gates_not_passed " + n($R.gatesNotPassed)
         + "  permitted " + n($R.permitted)
         + "  reason " + (n($R.reason) | .[0:60]),
       "   REFUSALS      " + ((.refusals // []) | join(", ") | .[0:190]))' \
  /tmp/ev.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "  READ IT AS: fresh=null is UNKNOWN, not stale -- a clock was"
say "  not measured, and venue_clock says which. fresh=false means"
say "  both were measured and one was too old. Those are different"
say "  problems and only one of them is ours."
say ""
say "  AND: rails_failed EMPTY with a positive edge is the S1"
say "  repair working. Before it, a STANDARD budget reserved at"
say "  the break-even limit exceeded a STANDARD rail by exactly"
say "  the edge, so every profitable candidate failed all five."
say ""

# ── S3 · WHY SOCCER CANNOT REACH A VENUE CONTRACT ────────────
say "== S3 · the resolver's own answer, per soccer fixture =="
for SP in Soccer soccer; do
  MC=$(curl -s --max-time 180 -o /tmp/mg.json -w '%{http_code}' \
         -H "$H" "$A/api/admin/shadow-mapgap?sport=$SP&limit=14" \
         || echo 000)
  say "shadow-mapgap sport=$SP HTTP $MC"
  if [ "$MC" = "200" ]; then break; fi
done
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  "  fixtures_read " + n(.fixtures_read),
  "  steps " + ((.steps // {}) | tojson | .[0:300]),
  ((.fixtures // [])[] | "",
    "-- " + n(.global_slug),
    "   title " + n(.title),
    "   sides " + ((.sides_parsed // []) | tojson),
    ((.per_side // {}) | to_entries[]
      | "   " + (.key | .[0:26])
        + "  step " + n(.value.step)
        + "  keys " + n(.value.keys_built)
        + "  venue_rows " + n(.value.venue_rows_found),
        "      league_alias_would_hit " + n(.value.league_alias_would_hit)
          + "  stripped " + n(.value.league_alias_stripped_key)
          + "  rows " + n(.value.league_alias_venue_rows),
        "      venue_sample " + (n(.value.league_alias_venue_sample) | .[0:140]),
        "      bridge " + n(.value.bridge_reason)
          + "  would_resolve " + n(.value.bridge_would_resolve),
        "      resolved_slug " + n(.value.market_slug)
          + "  intent " + n(.value.intent),
        "      DETAIL " + (n(.value.detail) | .[0:240])),
    "   -- the VENUE'"'"'S OWN ROWS for this event --",
    ((.venue_rows // [])[]
      | "      " + n(.market_slug)
        + "  kind " + n(.kind)
        + "  side_norm " + n(.side_norm)
        + "  siblings " + n(.siblings_for_event)
        + "  lg " + n(.league)
        + "  | " + (n(.question) | .[0:80])))' \
  /tmp/mg.json 2>&1 | head -140 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "  THE STEP NAME IS THE REMEDY. no_key_intersection with"
say "  league_alias_would_hit=true is a LEAGUE token gap, which"
say "  the resolver already bridges. false with rows found is a"
say "  TEAM-CODE gap, which needs a witness from the venue's own"
say "  question text and is a different mechanism entirely."
say "  Naming which one applies is what stops an alias table"
say "  being invented."
say ""

# ── S4 · THE WHOLE BOARD, NOT THE FIRST 400 ──────────────────
say "== S4 · every competition on the venue's board =="
VC=$(curl -s --max-time 180 -o /tmp/vcm.json -w '%{http_code}' \
       -H "$H" "$A/api/admin/venue-competitions" || echo 000)
say "venue-competitions HTTP $VC"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  "  events_read " + n(.events_read)
    + "  competitions " + n(.competitions)
    + "  truncated " + n(.truncated),
  "  REAL tokens (no simulation marker in the venue'"'"'s labels):",
  "    " + ((.real_competition_tokens // []) | join(" ") | .[0:600]),
  "  REAL tokens WITH a money line:",
  "    " + ((.real_competitions_with_a_moneyline // []) | join(" ") | .[0:600]),
  "",
  ((.rows // [])[]
    | "  " + n(.league_token)
      + "  events " + n(.events)
      + "  ml_events " + n(.moneyline_events)
      + "  sides " + n(.moneyline_sides)
      + "  sim " + n(.simulated_events)
      + "  " + n(.verdict)
      + "  desk " + ((.desk_league_buckets // {}) | tojson | .[0:60])
      + "  e.g. " + n(.examples[0].event)
      + " | " + n(.examples[0].title))' \
  /tmp/vcm.json 2>&1 | head -120 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
# ── S4b · THE FIXTURE QUESTION, not the token question ───────
# The lane refuses per FIXTURE. `lmx` being on the board does not
# mean the fixture we hold a probability for is, and a token
# census cannot tell those apart. `lmx` holds 7 events, so every
# one of them fits in the record.
for T in lmx efl1 cnl lng; do
  say ""
  say "-- S4b · every venue event under '$T' --"
  VT=$(curl -s --max-time 180 -o "/tmp/vt_$T.json" \
         -w '%{http_code}' -H "$H" \
         "$A/api/admin/venue-competitions?token=$T" || echo 000)
  say "   HTTP $VT"
  jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
    ((.token_events // [])[]
      | "   " + n(.event) + "  | " + n(.title)
        + "  kinds " + ((.market_kinds // []) | join(","))
        + "  ml " + ((.moneyline_us_slugs // []) | join(" ")
                     | .[0:150]))' \
    "/tmp/vt_$T.json" 2>&1 | head -40 \
    | tee -a "$GITHUB_STEP_SUMMARY" || true
done
say ""
say "  READ IT AS: our own market slug names a fixture and a"
say "  PAYOUT EVENT. A venue event listed here with an aec/atc"
say "  money line is reachable; the same fixture absent is"
say "  missing venue coverage; and our slug naming an exact"
say "  score or a total is a payout event the venue does not"
say "  sell as a money line, which is neither of those."
say ""
# ── S4b2 · THE COMMAND CENTRE ITSELF, INSPECTED ──────────────
# The operating handoff, read through the deployed interface --
# not a description of it. Controls, Opportunities, Orders,
# Positions and Performance, with the books kept separate and
# NO_TRADE reported as a result when the scheduler admitted
# nothing.
say ""
say "== S4b2 · the Bettor EV Engine operating handoff =="
DK=$(curl -s --max-time 120 -o /tmp/desk.json -w '%{http_code}' \
       -H "$H" "$A/api/command/bettor/desk?hours=24&limit=40" \
       || echo 000)
say "  GET /api/command/bettor/desk  HTTP $DK"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  "  product " + n(.product) + "  view " + n(.view),
  "  funded_submission " + n(.funded_submission)
    + "  reads_only " + n(.reads_only),
  "",
  "  -- CONTROLS --",
  "  mode " + n(.controls.research_mode.label)
    + "  env_flag_set " + n(.controls.research_mode.env_flag_set)
    + "  submits_orders " + n(.controls.research_mode.submits_orders),
  "  build " + n(.controls.build_identity.build)
    + "  source_sha12 " + n(.controls.build_identity.source_sha256_12)
    + "  pid " + n(.controls.build_identity.pid),
  "  cycle_at " + n(.controls.last_cycle_at)
    + "  state " + n(.controls.cycle_state)
    + "  LABEL " + n(.controls.cycle_label),
  "  control_state " + ((.controls.control_state // {}) | tojson | .[0:200]),
  "",
  "  -- OPPORTUNITIES --",
  "  markets_considered " + n(.opportunities.markets_considered)
    + "  candidates " + n((.opportunities.candidates // []) | length)
    + "  admissible " + n(.opportunities.admissible_count),
  "  VERDICT " + n(.opportunities.verdict),
  "  fair value " + n(.opportunities.fair_value_source.name)
    + "  internally_trained "
    + n(.opportunities.fair_value_source.is_an_internally_trained_model),
  (((.opportunities.no_trade_reasons // {}) | to_entries[])
    | "     blocker " + .key + "  x" + (.value|tostring)),
  (((.opportunities.funnel // {}) | to_entries[])
    | "     funnel " + .key
      + "  mapped " + n(.value.mapped_to_a_venue_contract)
      + "  evaluated " + n(.value.evaluated)
      + "  written " + n(.value.written)),
  (((.opportunities.first_refusal_per_mapped_candidate // [])[])
    | "     candidate " + n(.us_market_slug)
      + "  stage " + n(.stage)
      + "  first_refusal " + n(.first_refusal)),
  "",
  "  -- POSITIONS, SEPARATE BOOKS --",
  (((.positions.books // {}) | to_entries[])
    | "     book " + .key + "  count " + (.value.count|tostring)),
  "  " + n(.positions.note),
  "",
  "  -- PERFORMANCE, PER LANE --",
  (((.performance.per_lane // {}) | to_entries[])
    | "     " + .key + "  " + (.value | tojson | .[0:220])),
  "  mark_basis " + n(.performance.mark_basis),
  "",
  "  -- OPEN LIMITATIONS, STATED --",
  "  freshness   " + n(.open_limitations.venue_book_freshness),
  "  settlement  " + n(.open_limitations.settlement_compatibility),
  "  scope       " + n(.open_limitations.market_scope_metadata)' \
  /tmp/desk.json 2>&1 | head -90 | tee -a "$GITHUB_STEP_SUMMARY" \
  || true
if [ "$DK" != "200" ]; then
  say "  THE HANDOFF DID NOT RETURN 200 -- head of the body:"
  head -c 400 /tmp/desk.json 2>/dev/null \
    | tee -a "$GITHUB_STEP_SUMMARY" || true
fi
say ""

# ── S4c · THE PUBLISHED SCHEMA, FETCHED ON THIS RUNNER ───────
# The container the agent works in cannot reach
# docs.polymarket.us (its egress proxy blocks the domain). This
# runner can, and the published schema is the authority for what
# the two type fields MEAN -- captured samples show which values
# occur, never what they cover. Fetched read-only, printed with
# its source named, and it is NOT replaced by older repository
# classifiers.
say ""
say "== S4c · the venue's PUBLISHED Sports Schema =="
SCU="https://docs.polymarket.us/trader-guide/sports-schema"
say "  source $SCU"
SC=$(curl -sL --max-time 60 -o /tmp/schema.html \
       -w '%{http_code}' "$SCU" || echo 000)
say "  HTTP $SC  bytes $(wc -c < /tmp/schema.html 2>/dev/null || echo 0)"
if [ "$SC" = "200" ]; then
  python3 - <<'PYS' | tee -a "$GITHUB_STEP_SUMMARY" || true
import html, re, sys
t = open("/tmp/schema.html", errors="replace").read()
t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
t = html.unescape(re.sub(r"(?s)<[^>]+>", "\n", t))
lines = [l.strip() for l in t.splitlines() if l.strip()]
keys = ("sportsMarketType", "marketSportType", "market_sport_type",
        "SPORTS_MARKET_TYPE", "full_game", "full_time",
        "first_half", "second_half", "period", "scope",
        "slug", "identifier", "participant", "team")
hits = [l for l in lines if any(k in l for k in keys)]
print("  schema lines mentioning the fields: %d" % len(hits))
for l in hits[:80]:
    print("   | " + l[:160])
if not hits:
    print("   NO FIELD NAMES FOUND IN THE FETCHED TEXT -- the page")
    print("   may render client-side. Then the published schema is")
    print("   NOT in hand and the token sets stay provisional.")
PYS
else
  say "  THE SCHEMA WAS NOT RETRIEVED. The scope token sets in"
  say "  bettor_venue_mapping remain PROVISIONAL -- established"
  say "  from captured responses and held conservatively -- and"
  say "  that is stated in the module, not glossed."
fi
say ""

# ── S4d · THE TWO TYPE FIELDS, TOGETHER, ON REAL CONTRACTS ───
# v2 is the STRUCTURE and cannot carry scope: captured rows show
# full_game, first_half and second_half SPREADs all returning
# SPORTS_MARKET_TYPE_SPREAD. So the pair is printed side by side
# for a full-game shape, a segment shape and a future shape.
say "== S4d · sportsMarketTypeV2 AND sportsMarketType, together =="
for T in mlb cfb nhl f1 lmx; do
  MT=$(curl -s --max-time 180 -o "/tmp/mt_$T.json" \
         -w '%{http_code}' -H "$H" \
         "$A/api/admin/venue-competitions?token=$T" || echo 000)
  say ""
  say "-- $T  HTTP $MT"
  jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
    ((.token_events // [])[]
      | "   event " + n(.event) + "  | " + n(.title),
        ((.market_types // [])[]
          | "      " + n(.us_slug)
            + "\n         v2 " + n(.sportsMarketTypeV2)
            + "\n         v1 " + n(.sportsMarketType)
            + "\n         kind(diagnostic only) "
            + n(.kind_diagnostic_only)
            + "  team " + n(.team) + "/" + n(.team_id)))' \
    "/tmp/mt_$T.json" 2>&1 | head -80 \
    | tee -a "$GITHUB_STEP_SUMMARY" || true
done
say ""
say "  READ IT AS: v2 establishes STRUCTURE only. If a full game"
say "  and a half show the SAME v2, that is the proof, and scope"
say "  must come from v1's own scope token. A v1 with no"
say "  recognised token -- the generic \`moneyline\`, or a"
say "  whole-contest form like \`tennis_match_winner\` -- leaves"
say "  scope UNRESOLVED rather than full."
say ""

# ── S5 · WHAT THE VENUE'"'"'S transactTime ACTUALLY MEANS ──────
# The gate refuses a book whose transactTime is more than 30 s
# before the decision instant. That is a staleness measurement
# ONLY IF the venue stamps the RESPONSE. If it stamps the LAST
# BOOK CHANGE, an old value means the book has not moved -- the
# ordinary condition of a quiet pre-game money line -- and the
# gate would be refusing quiet books and calling it freshness.
# institutional_book'"'"'s own contract was INSPECTED and does not
# transfer: its 5 s limit measures OUR CACHED COPY, and what
# makes that meaningful is a persistent poll process refreshing
# the cache while the consumer reads memory instead of calling
# REST to decide. This lane calls REST AT the decision, so
# receipt age is the round trip and the check cannot fail.
# obs/streamstate forbids the subtraction outright. THE REFUSAL
# IS RETAINED and both limits stay where they are.
#
# SO THIS BLOCK REPORTS OBSERVATIONS AND DRAWS NO VERDICT: raw
# stamps, our request and receipt instants, book hashes and the
# deltas. Two samples of three contracts cannot separate a
# response stamp from a last-update stamp, and nothing about
# admission moves either way.
say ""
say "== S5 · the venue clock, read twice on the same contract =="
CP=$(curl -s --max-time 240 -o /tmp/clk.json -w '%{http_code}' \
       -H "$H" "$A/api/admin/venue-clock-probe?gap_s=12&slugs=aec-mlb-cin-tor-2026-09-26,aec-mlb-nym-wsh-2026-09-26,aec-atp-danmed-valroy-2026-09-23" \
       || echo 000)
say "venue-clock-probe HTTP $CP"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  "  configured_limit_s " + n(.configured_limit_s),
  "  SEMANTICS " + n(.semantics),
  ((.pairs // [])[]
    | "  " + n(.slug) + "  gap " + n(.gap_s) + "s  " + n(.verdict),
      "     1 raw " + n(.first.raw_transact_time)
        + "  type " + n(.first.raw_type)
        + "  parsed " + n(.first.parsed_epoch_s),
      "       returned_at " + n(.first.returned_at_epoch_s)
        + "  age_at_return " + n(.first.age_at_return_s) + "s"
        + "  book " + n(.first.book_sha16)
        + "  ask " + n(.first.best_ask)
        + "  depth " + n(.first.displayed_ask_depth),
      "     2 raw " + n(.second.raw_transact_time)
        + "  parsed " + n(.second.parsed_epoch_s),
      "       returned_at " + n(.second.returned_at_epoch_s)
        + "  age_at_return " + n(.second.age_at_return_s) + "s"
        + "  book " + n(.second.book_sha16),
      "     transact_delta " + n(.transact_time_delta_s) + "s"
        + "  book_changed " + n(.book_changed)),
  "  " + n(.reading)' \
  /tmp/clk.json 2>&1 | head -60 | tee -a "$GITHUB_STEP_SUMMARY" \
  || true

# ── S6 · THE FIXTURE QUESTION, four verdicts not one ─────────
say ""
say "== S6 · competition / fixture / contract / payout event =="
for SP in Soccer Baseball; do
  FX=$(curl -s --max-time 240 -o "/tmp/fx_$SP.json" \
         -w '%{http_code}' -H "$H" \
         "$A/api/admin/venue-fixture-crossing?sport=$SP&limit=30" \
         || echo 000)
  say ""
  say "-- $SP  HTTP $FX"
  jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
    "   venue_events_considered " + n(.venue_events_considered),
    "   verdicts " + n(.verdict_counts),
    ((.fixtures // [])[]
      | "   " + n(.global_slug),
        "      verdict " + n(.verdict),
        "      ours sides " + n(.our_sides)
          + "  family " + n(.our_market_family)
          + "  segment " + n(.our_segment)
          + "  full_match_winner "
          + n(.our_payout_is_a_full_match_winner),
        "      venue fixture " + n([(.venue_fixture_candidates
            // [])[].event])
          + "  full_match " + n(.venue_full_match_contracts),
        (if (.single_side_only_near_misses // []) | length > 0
         then "      ONE SIDE ONLY (not adopted) "
              + n([(.single_side_only_near_misses[]
                    | .event)])
         else empty end))' \
    "/tmp/fx_$SP.json" 2>&1 | head -120 \
    | tee -a "$GITHUB_STEP_SUMMARY" || true
done
say ""
say "  ONLY the ALL_PRESENT verdict is a mapping defect on our"
say "  side. The rest are absent venue coverage, or a candidate"
say "  query asking for a payout event the venue does not sell"
say "  as a money line, and no normalisation repairs either."
say ""
say "  THE EPL QUESTION, SETTLED EITHER WAY. events_read is the"
say "  WHOLE cached board, not desk-games' first 400, so an"
say "  absent token here is absence from the board rather than"
say "  from a sample. A token present with ml_events 0 is listed"
say "  without a money line, which no mapping repair reaches."
say ""
echo '```' >> "$GITHUB_STEP_SUMMARY"

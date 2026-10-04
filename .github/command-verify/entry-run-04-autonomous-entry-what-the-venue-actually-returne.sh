set -uo pipefail
[ -n "${ADMIN_TOKEN:-}" ] || { echo "::error::ADMIN_TOKEN unset"; exit 1; }
H="X-Admin-Token: $ADMIN_TOKEN"
say() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"; }
echo '```' >> "$GITHUB_STEP_SUMMARY"
# 4a-ter · WHAT THE VENUE ACTUALLY RETURNED, BEFORE WE INTERPRET IT.
#
# `CLOSED_BUT_NO_REPORTED_OR_CONVERGED_OUTCOME` and
# `NO_SETTLEMENT_PRICE_IN_RESPONSE` are OUR PARSER'S verdicts. I
# reported them as the venue's refusal, which is a different claim.
# This captures the raw redacted payload from both surfaces -- the
# settlement endpoint and the market listing -- with the FIELD TYPES,
# for Arizona and for a calibration fixture the outcome join itself
# recorded as unreadable. Read-only; nothing is written.
#
# The types are the half that decides it: `_converged_winner` requires
# `outcomePrices` to be a list, so a venue delivering it as a
# JSON-encoded STRING would produce exactly this symptom while the
# prices sat there unread. The probe names that case as OUR gap, and an
# absent field as the payload's. It infers no payout from `closed`,
# `resolved` or `settledAt`.
: > /tmp/vp.json
# THE SLUGS THE LANE ACTUALLY EVALUATED, PER SPORT -- not two
# hardcoded baseball literals.
#
# WHY THIS CHANGED. The prose read is the only affirmative
# establishment of the VENUE's side, and it was pinned to two MLB
# slugs. So the other supported sport's payout rules were never
# read at all, and "no compatible pair" rested on a comparison
# that had only ever been run on baseball. Up to two distinct
# slugs per sport_family are taken from the candidates the lane
# just evaluated; the baseball literals remain only as the
# fallback for a window with no candidates.
jq -r '[(.candidates // [])[]
        | {f: (.sport_family // "UNLABELLED"),
           s: .us_market_slug}
        | select(.s != null)]
       | group_by(.f) | map(.[0:2]) | flatten
       | map(.s) | unique | .[0:6] | @json' \
  /tmp/ee.json > /tmp/slugs.json 2>/dev/null || true
if ! jq -e 'length > 0' /tmp/slugs.json >/dev/null 2>&1; then
  printf '%s\n' '["aec-mlb-az-col-2026-09-24","aec-mlb-cle-kc-2026-09-25"]' \
    > /tmp/slugs.json
  say "NO CANDIDATE SLUGS IN THE WINDOW -- falling back to the two"
  say "baseball literals. The prose below is then evidence about"
  say "those two contracts only."
fi
say "prose slugs  $(cat /tmp/slugs.json)"
jq -n --slurpfile s /tmp/slugs.json \
   '{slugs: $s[0], calibration_unreadable: 1}' > /tmp/vpreq.json
VC=$(curl -s -o /tmp/vp.json -w '%{http_code}' --max-time 180 \
    -X POST -H "$H" -H 'Content-Type: application/json' \
    --data @/tmp/vpreq.json \
    "$API/api/admin/venue-settlement-probe" || echo 000)
say ""
say "## what the venue returned (HTTP $VC)"
# THE RAW PAYLOAD FIRST, UNCONDITIONALLY. This is the artifact the
# investigation turns on; no formatter gets to lose it.
# `head -c` KILLED THIS STEP AND I WROTE IT. Under `set -uo pipefail`,
# `head` closing the pipe early sends jq SIGPIPE, pipefail takes jq's
# non-zero status, and `-e` ends the step -- exit code 2, right after
# the raw dump, losing every formatted line that followed. The raw
# evidence survived only because it printed first, which is the one
# thing that went right. Truncation now happens INSIDE jq, so nothing
# closes a pipe on it.
say "-- raw, per slug --"
jq -r '.probes[]? | tojson | .[0:6000]' /tmp/vp.json 2>&1 \
  | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "-- which fixtures were picked --"
jq -r '.calibration_picks | tojson | .[0:600]' /tmp/vp.json 2>&1 \
  | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
if [ "$VC" = "200" ]; then
  jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
    (.probes // [])[]
      | "slug            " + n(.slug),
        "  retrieved_at  " + n(.retrieved_at),
        "  terminal      " + n(.terminal_reading)
          + "  authoritative_payout " + n(.authoritative_payout_present),
        "  why           " + n(.why),
        "  settlement    " + n(.settlement.endpoint)
          + "  err " + n(.settlement.error)
          + "  keys " + n(.settlement.top_level_keys),
        "  listing       matched " + n(.listing.matched)
          + "  err " + n(.listing.error),
        "  outcomePrices " + n(.listing.payout_candidates.outcomePrices.shape),
        "  outcomes      " + n(.listing.payout_candidates.outcomes.shape),
        "  marketSides   " + n(.listing.payout_candidates.marketSides.shape),
        "  status        " + n(.listing.payout_candidates.status.value)
          + "  closed " + n(.listing.payout_candidates.closed.value),
        "  void          " + n(.listing.void_evidence.declared)
          + "  field " + n(.listing.void_evidence.field),
        "  reader        " + n(.reader_verdict.status)
          + "  " + n(.reader_verdict.error),
        "  PARSER_GAP    " + n(.parser_gap.found)
          + "  " + n(.parser_gap.why),
        "  values        " + n(.listing.raw_subset),
        "  PROSE FIELD   " + n(.settlement_terms.field)
          + "  chars " + n(.settlement_terms.chars)
          + "  truncated " + n(.settlement_terms.truncated)
          + "  fields " + n(.settlement_terms.fields_present),
        "  prose VERBATIM " + n(.settlement_terms.text),
        "  STATED        " + n(.settlement_terms.stated),
        "  NOT STATED    " + n(.settlement_terms.not_stated),
        "  contradicted  " + n(.settlement_terms.contradicted)
          + "  sentences " + n(.settlement_terms.sentences),
        "  unused        " + n(.settlement_terms.unused_evidence)' \
    /tmp/vp.json 2>/tmp/vp.err | tee -a "$GITHUB_STEP_SUMMARY" \
    || { say "THE PROBE REPORT FAILED TO RENDER -- the raw payload is"
         say "above and is the authoritative copy:"
         head -c 300 /tmp/vp.err | tee -a "$GITHUB_STEP_SUMMARY"; }
else
  say "THE VENUE PROBE DID NOT RETURN 200 -- no conclusion about"
  say "whether the gap is ours or the payload's."
fi

echo '```' >> "$GITHUB_STEP_SUMMARY"

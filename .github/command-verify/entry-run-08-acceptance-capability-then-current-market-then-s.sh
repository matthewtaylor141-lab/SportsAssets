set -uo pipefail
A="${API:-https://sportsassets-api.onrender.com}"
H="X-Admin-Token: ${ADMIN_TOKEN:-}"
say() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"; }
echo "### ACCEPTANCE: A capability · B current market · C scope" \
  >> "$GITHUB_STEP_SUMMARY"
echo '```' >> "$GITHUB_STEP_SUMMARY"
if [ -z "${ADMIN_TOKEN:-}" ]; then
  say "SKIPPED: no ADMIN_TOKEN"
  echo '```' >> "$GITHUB_STEP_SUMMARY"; exit 0
fi

# ── A · THE CONTROLLED LIFECYCLE, FOUND NOT ASSUMED ──────────
#
# The position is SEARCHED FOR by the property that matters --
# orders AND fills AND a settled outcome row -- rather than
# hardcoded. A hardcoded id is how this report previously came to
# describe Arizona while the gate judged Houston.
say "== A · SOFTWARE CAPABILITY: one position, every stage =="
PC=$(curl -s --max-time 90 -o /tmp/apos.json -w '%{http_code}' \
       -H "$H" "$A/api/command/rn1x/positions?limit=200" || echo 000)
say "positions HTTP $PC"
jq -r '[(.positions // [])[] | .position_id] | .[0:60] | .[]' \
  /tmp/apos.json > /tmp/apids.txt 2>/dev/null || true
say "candidate positions scanned  $(wc -l < /tmp/apids.txt)"
FOUND=""
: > /tmp/atr.json
while read -r P; do
  [ -n "$P" ] || continue
  curl -s --max-time 60 -H "$H" \
    "$A/api/command/rn1x/trace/$P" -o /tmp/t1.json || continue
  # EVERY STAGE, OR IT IS NOT THE DEMONSTRATION.
  # `.outcome` is ONE OBJECT OR NULL, not an array -- the trace
  # route returns `outcome`, and rn1x_outcomes is keyed by
  # position_id so there is at most one row. Testing `.outcomes`
  # as a list would have matched nothing forever and reported a
  # missing demonstration that was actually a missing key.
  if jq -e '((.orders // []) | length) > 0
            and ((.fills // []) | length) > 0
            and (.outcome != null)' \
       /tmp/t1.json >/dev/null 2>&1; then
    FOUND="$P"; cp /tmp/t1.json /tmp/atr.json; break
  fi
done < /tmp/apids.txt
if [ -z "$FOUND" ]; then
  say "NO POSITION IN THE NEWEST 60 TRAVERSED EVERY STAGE."
  say "That is a gap in the demonstration, not a passing result."
else
  say "subject  $FOUND"
  say "trace    $A/api/command/rn1x/trace/$FOUND"
  jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
    .position as $p
    | "  1 ENTRY        policy " + n($p.policy)
        + "  provenance " + n($p.provenance)
        + "  entry_kind " + n($p.entry_kind),
      "                qty " + n($p.qty)
        + "  filled " + n($p.filled_qty)
        + "  residual " + n($p.residual_qty)
        + "  basis_usd " + n($p.cost_basis_usd),
      "  2 ORDERS       " + ((.orders // []) | length | tostring)
        + "   " + ((.orders // []) | map(
            n(.side) + "/" + n(.state) + " @" + n(.limit_price)
            + " x" + n(.qty) + " filled " + n(.filled_qty)
            + " basis " + n(.fill_basis)) | join("  ")),
      "  3 FILLS        " + ((.fills // []) | length | tostring)
        + "   " + ((.fills // []) | map(
            "@" + n(.price) + " x" + n(.qty)
            + " fee " + n(.fee_usd)
            + " queue_share " + n(.queue_share)
            + " basis " + n(.fill_basis)) | join("  ")),
      "  4 MANAGEMENT   decisions "
        + ((.decisions // []) | length | tostring)
        + "   distinct_instants "
        + (((.decisions // []) | map(.decision_ts) | unique
            | length) | tostring)
        + "   newest " + n((.decisions // []) | last | .decision_ts),
      # ONE OUTCOME ROW OR NONE. rn1x_outcomes is keyed by
      # position_id, and the route returns it as `outcome`.
      "  5 EXIT/SETTLE  settled_at " + n(.outcome.settled_at)
        + "   residual " + n(.outcome.residual_qty)
        + "   unpaired " + n(.outcome.unpaired_qty)
        + "   basis " + n(.outcome.outcome_basis),
      "  6 ACCOUNTING   realized_cash " + n(.outcome.realized_cash_usd)
        + "  fees " + n(.outcome.fees_usd)
        + "  net " + n(.outcome.net_usd)
        + "  residual_settled " + n(.outcome.residual_settled_usd)
        + "  turnover " + n(.outcome.turnover_usd)
        + "  committed_peak " + n(.outcome.committed_peak_usd),
      # THE ROUTE SAYS THIS ITSELF. Printing its own words beats
      # asserting a column that does not exist on the projection:
      # `is_modelled` is CHECKed true in the SCHEMA, and the
      # trace publishes the semantics as prose instead.
      "  LABELS        " + (n(.fill_semantics) | .[0:300])' \
    /tmp/atr.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
  say "                No order was submitted to any venue;"
  say "                migration 100 CHECKs is_modelled true on"
  say "                every rn1x order and fill."
  say "  PROVES        the lifecycle runs end to end."
  say "  DOES NOT      say anything about profitability, about"
  say "                current markets, or about capital."
fi
say ""

# ── A2 · DUPLICATE PROTECTION AND RESTART RECOVERY ───────────
#
# Both are demonstrated by REPEATING an operation that already
# happened and showing the refusal, which is stronger than a
# test asserting the same thing in isolation.
say "== A2 · duplicate protection, on a live repeat =="
SC=$(curl -s --max-time 60 -o /tmp/adup.json -w '%{http_code}' \
       -X POST -H "$H" -H 'Content-Type: application/json' \
       -d '{}' "$A/api/admin/rn1x-acceptance-position" || echo 000)
say "re-seed HTTP $SC"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  "  state " + n(.state) + "  wrote " + n(.wrote)
    + "  position_id " + n(.position_id),
  "  why   " + (n(.why) | .[0:200]),
  "  submits_orders " + n(.submits_orders)
    + "  funded " + n(.funded)' \
  /tmp/adup.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say "  A SECOND CALL MUST NOT CREATE A SECOND HOLDING. wrote=false"
say "  with an ADOPTED/existing state is the protection working;"
say "  migration 114's unique index is the backstop."
say ""
# WHAT THIS BLOCK DOES AND DOES NOT SHOW.
#
# It shows LIVENESS AFTER A PROCESS RESTART: the API was
# redeployed for this release, and the writer identity below
# carries THIS build's SHA, so the cycle it timestamps was run
# by the post-restart process. That is worth reading, and it is
# all it is.
#
# IT IS NOT RESTART RECOVERY OF HELD INVENTORY. Recovery means
# a position entered before the restart is re-read afterwards
# with the same identity, the same residual and no duplicate
# write. This lane holds ZERO positions (§4 of the operating
# recap), so there is nothing here for a restart to recover,
# and a completed cycle cannot evidence otherwise.
#
# NOR IS A CONNECTION RELOAD A PROCESS RESTART. Re-reading a
# position on a fresh DB connection in the same interpreter
# exercises the reload path, not process recovery; the two must
# not be reported as the same thing.
#
# Process recovery is verified separately, by two real OS
# processes: A enters and manages, then EXITS; B starts in a
# fresh interpreter and re-reads. See the acceptance record.
say "== A2b · post-restart liveness (NOT inventory recovery) =="
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  .statuses.external_valuation.last_cycle as $L
  | "  entry loop   writer " + n($L.writer)
      + "  state " + n($L.state)
      + "  at " + n($L.at),
    "  SHOWS      the process that ran this cycle carries this",
    "             SHA for this release, so the loop came back up",
    "             the redeploy and is cycling.",
    "  DOES NOT   show restart recovery of held inventory: this",
    "             lane holds no positions, so no identity, no",
    "             residual and no duplicate protection is under",
    "             test here. A connection reload is also NOT a",
    "             process restart. Process recovery is verified",
    "             by the two-process check in the acceptance",
    "             record, not by this line."' \
  /tmp/pnl.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""

# ── C · THE VENUE'S OWN BOARD, PER LEAGUE ────────────────────
#
# WHY THIS IS HERE. The entry lane's universe is OUR `markets`
# table. Measured in run 67: 230 open Soccer rows, 29 Pinnacle-
# priced soccer fixtures, and ZERO reaching a valuation -- three
# of them refused with the venue's own words, "`markets.slug` is
# the GLOBAL id and the venue does not accept it". That leaves one
# question open, and it decides the attribution: does the VENUE
# natively list a soccer MONEY LINE at all?
#
#   money lines exist on the venue  -> OUR ingestion is the defect
#   they do not                     -> missing venue coverage
#
# `aec`/`atc` are the venue's own slug-grammar prefixes for a
# money line (pmus.list_desk_events). The route below is the one
# the desk board already uses; the admin token satisfies it.
say "== C · the VENUE'S OWN board, per league =="
VC=$(curl -s --max-time 120 -o /tmp/vb.json -w '%{http_code}' \
       -H "$H" \
       "$A/api/admin/desk-games?venue=polymarket&league=everything" \
       || echo 000)
say "desk-games HTTP $VC"
jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
  (.games // []) as $g
  | "  events_returned " + ($g | length | tostring),
    "  counts " + ((.counts // {}) | tojson | .[0:400]),
    ($g | group_by(.league)[]
      | . as $rows
      | ($rows | map(.outcomes[]?.us_slug // "")
               | map(select(test("^(aec|atc)-"))) | length) as $ml
      | "  " + n($rows[0].league)
        + "  events " + ($rows | length | tostring)
        + "  moneyline_sides " + ($ml | tostring)
        + "  HAS_MONEYLINE " + (if $ml > 0 then "YES" else "NO" end)
        + "   e.g. " + n($rows[0].outcomes[0].us_slug))' \
  /tmp/vb.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "  READ IT AS: a league with HAS_MONEYLINE YES is a contract"
say "  the venue sells and our lane did not reach -- an"
say "  INGESTION/MAPPING defect on our side. A league with NO is"
say "  missing venue coverage, which no code of ours can repair."
say "  Neither verdict makes an UNKNOWN payout rule compatible."
say ""

# ── C2 · WHICH SOCCER CONTRACT, EXACTLY ──────────────────────
#
# NOT EVERY NATIVE SOCCER CONTRACT IS SUITABLE, and the previous
# report's `aec|atc` prefix test could not tell them apart: the
# example it printed was `atc-ebfpl-ars-che-2026-09-25-dh2-ars`,
# a DOUBLE CHANCE, and the MLB one was `...-i8-draw`, an INNING
# SEGMENT. Both classify as "moneyline" today because
# `copy_sports.market_type_of` returns on the KIND PREFIX alone
# for the venue grammar and never inspects the suffix -- the
# suffix analysis that catches segments and exact scores exists
# only on the kindless feed grammar.
#
# So the payout event has to be established from the slug's own
# shape before any probability is bound to it:
#   three-way money line   home / draw / away on the full match
#   binary team-win        one team, full match
#   double chance          two of three outcomes -- NOT the same
#                          payout event as a money line
#   exact score, segments  excluded outright
#
# This prints the FULL board of up to three soccer events through
# `/api/admin/desk-game`, which already applies the classifier,
# so the real suffix vocabulary and the classifier's verdict on
# each slug are visible together. It is a read; it decides
# nothing.
say "== C2 · the full soccer board, and how it classifies =="
jq -r '[(.games // [])[] | select(.league == "soccer") | .id]
       | .[0:3] | .[]' /tmp/vb.json > /tmp/socc.txt 2>/dev/null \
  || true
say "soccer events sampled  $(wc -l < /tmp/socc.txt)"
: > /tmp/soccslugs.txt
while read -r EV; do
  [ -n "$EV" ] || continue
  say ""
  say "-- event $EV"
  GC=$(curl -s --max-time 90 -o /tmp/g1.json -w '%{http_code}' \
         -H "$H" \
         "$A/api/admin/desk-game?venue=polymarket&id=$EV" \
         || echo 000)
  say "   desk-game HTTP $GC"
  # EVERY market, WITH the group label beside it. The payload's
  # exact nesting is not assumed: any object that carries a
  # `us_slug` is printed with whatever label sits next to it, and
  # the raw key list is printed too so a reader can see the shape
  # rather than trust this formatter.
  say "   raw keys: $(jq -r '[keys[]]|tostring' /tmp/g1.json 2>/dev/null | head -c 200)"
  jq -r 'def n(x): if x == null then "-" else (x|tostring) end;
    [.. | objects | select(has("us_slug"))]
    | .[] | "   " + n(.us_slug)
            + "   label=" + (n(.label) | .[0:52])
            + "   group=" + n(.group // .kind)' \
    /tmp/g1.json 2>/dev/null \
    | head -40 | tee -a "$GITHUB_STEP_SUMMARY" || true
  jq -r '[.. | objects | .us_slug? // empty] | unique | .[]' \
    /tmp/g1.json 2>/dev/null >> /tmp/soccslugs.txt || true
done < /tmp/socc.txt
say ""
say "-- every distinct soccer us_slug seen --"
sort -u /tmp/soccslugs.txt | head -60 \
  | sed 's/^/   /' | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "-- the SUFFIX VOCABULARY, after the date, with counts --"
# The suffix is what distinguishes a full-match money line from a
# double chance or a segment. Counted rather than eyeballed.
sed -E 's/^[a-z]+-//; s/^.*-[0-9]{4}-[0-9]{2}-[0-9]{2}//; s/^-//' \
  /tmp/soccslugs.txt 2>/dev/null \
  | sed 's/^$/<EMPTY: bare event slug>/' \
  | sort | uniq -c | sort -rn | head -40 \
  | sed 's/^/   /' | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "-- the venue's own soccer LEAGUE TOKENS (alias check) --"
sed -E 's/^[a-z]+-//; s/-.*$//' /tmp/soccslugs.txt 2>/dev/null \
  | sort | uniq -c | sort -rn | head -20 \
  | sed 's/^/   /' | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "  A suffix naming a half, a period, an inning, a correct or"
say "  exact score, or a player is NOT the full-match payout event."
say ""
say "  AND A CORRECTION TO MY OWN EARLIER READING: binary YES/NO"
say "  PACKAGING DOES NOT DISQUALIFY A CONTRACT. \"Team A wins the"
say "  match? Y/N\" IS one outcome of a three-way match, and a"
say "  de-vigged three-way probability prices it directly. Three"
say "  such binaries -- home, draw, away -- are a three-way money"
say "  line sold as three contracts. What disqualifies a contract"
say "  is the EVENT it pays on, not how it is packaged. I said"
say "  otherwise and it was wrong."
say ""

# ── C3 · REAL vs SIMULATED, OVER THE WHOLE SOCCER BOARD ──────
#
# The 3-event sample in C2 cannot answer the coverage question:
# whether the venue lists the competitions the SOURCE prices --
# the real English Premier League (`soccer_epl`) and Liga MX
# (`soccer_mexico_ligamx`). This histograms the league token of
# EVERY soccer event the board returned, which is already in hand
# from the call above, so it costs nothing more.
#
# A SIMULATED COMPETITION IS NOT ITS REAL NAMESAKE. `ebfpl` is
# eBattles FPL, whose own labels say "eBattles"; its fixtures
# carry real club names and are not real fixtures. Binding a
# real-league probability to one on a name match is a
# cross-competition category error, and the token is the only
# thing that separates them.
say "== C3 · every soccer league token on the board =="
jq -r '[(.games // [])[] | select(.league == "soccer")] as $s
       | "  soccer events on the board  " + ($s | length | tostring),
         ($s | group_by(.id | split("-")[0])[]
           | "  " + (.[0].id | split("-")[0])
             + "   events " + (length | tostring)
             + "   e.g. " + (.[0].id)
             + "   title " + ((.[0].title // "-") | .[0:56]))' \
  /tmp/vb.json 2>&1 | tee -a "$GITHUB_STEP_SUMMARY" || true
say ""
say "  THE SOURCE PRICES: soccer_epl (the REAL Premier League)"
say "  and soccer_mexico_ligamx (the REAL Liga MX). A token above"
say "  that is neither is a competition we hold no probability"
say "  for, however familiar the club names look."
echo '```' >> "$GITHUB_STEP_SUMMARY"

# THE BETTOR COMMAND CENTRE. What it shows, and what it refuses to.

Five views over one evidence read, behind the existing COMMAND session.
Built on `claude/command-center`. Nothing here is deployed.

## WHAT THE AUDIT FOUND FIRST

The existing command centre (`frontend/public/command/`, 2,100 lines of
JS; `api/command_snapshot.py`, 785 lines; `api/command_shadow.py`, 1,262
lines) has genuinely good discipline already: `RetrievalIncomplete` →
503 → "FEED UNAVAILABLE" rather than a page of zeros, a `_blank`/
NOT_IDENTIFIED convention in `bettor_command_view.py`, and a server-side
investor projection so a browser is never handed rows it must then strip.

What it does not have is any of the five views asked for. Specifically:

  * the incentive observation run is not surfaced at all — no probe or
    boot identity, no journal, no allowance-against-limits, no
    early-vs-window split;
  * there is no test-evidence view, so no suite's tested commit,
    environment or completion status is visible anywhere;
  * there is no capability-traceability view;
  * economic figures are not separated into buckets that cannot be
    summed.

So this is additive: a new read model, a new route, a new page. The
existing snapshot, shadow and investor paths are untouched, which is
also why none of their tests moved.

## THE SHAPE

    sportsassets/bettor_command_center.py      PURE. records in, views out.
    sportsassets/api/command_center.py         the I/O half. SELECT-only.
    sportsassets/api/app.py                    two GETs behind require_command
    frontend/public/command/center.{html,js,css}

The split is the point: every state the page can reach is reachable in a
test by handing `build()` a list of dicts. No database, no fixtures on
disk, no browser needed to assert that a quiet book is not called broken.

## THE REFUSALS, AND WHERE EACH ONE LIVES

**A true control flag is not collection.** `lifecycle()` reads terminal
states first, and when the control is true with zero ladder records it
returns `ARMED_NO_FRAMES` — a named state, not a footnote. The live run
this week is exactly why: the control was set true at 02:36:14Z, and at
no point did a frame exist. A dashboard keyed on the flag would have
shown a healthy green run for six minutes of nothing.

**A quiet book is not a broken feed.** `connection_health()` never
derives a verdict from silence. It reads the worker's OWN liveness
detector, which is journalled as GAP_OPENED / GAP_CLOSED. No open gap
means the worker still believes the socket; frame recency then only
distinguishes ACTIVE from QUIET, which describes the market, not the
health. `CONNECTED_QUIET` is styled as a good state, deliberately —
painting six quiet hours red teaches the reader to stop believing red.

**A gap that closed is not still open.** `gap_view()` pairs GAP_OPENED
against GAP_CLOSED in time order. GAP_OPENED carries no `to` field, so
anything that decides "open" by looking for a missing `to` finds every
gap the run ever recovered from and pins the page at INTERRUPTED for the
rest of the day. Pinned by
`test_a_gap_that_closed_is_not_still_open`.

**A missing measurement is not zero.** `cell()` raises if given no
source; `unknown()` is the only way to render an absent quantity, and it
renders the word UNKNOWN. `test_every_cell_in_a_built_payload_has_a_source`
walks the whole payload; the browser pass counts `.cc-broken` nodes and
found zero across 33 renders.

**An incomplete test run reports no counts.** `suite_row()` withholds
pass/fail/skip when `complete` is false, because the counts an aborted
run printed describe a prefix. A missing or truncated JUnit file is an
incomplete run, not a passing one.

**Unmatched failures are unattributed.** `baseline_compare()` returns
`comparable: False` when either side is incomplete, and labels every
difference `unattributed` when the two runs did not cover the same
selection. A regression claim needs a complete baseline over the same
selection or it is not a regression claim.

**The four economic buckets never sum.** Realized trading P&L, replay
P&L, hypothetical reward and venue-confirmed reward are returned as a
mapping, and no total is computed anywhere. Realized P&L is
NOT_APPLICABLE — not zero — because there is no order path at all, which
is a different fact from a trading history that netted nothing.

**Observation never reaches LIVE_EXECUTION_VALIDATED.**
`test_nothing_claims_live_execution_validation` asserts the top rung is
empty, and P_FILL sits at NOT_IMPLEMENTED with the reason stated on the
row: a public feed shows the book, never our order in it.

## VERIFICATION

Focused tests: 73 pass (`backend/tests/test_bettor_command_center.py`).

Browser pass through the real route, the real `require_command`
dependency and the real page — 33 screenshots at 1440x1000 and 390x844:

    authentication      401 without the cookie; page says
                        "COMMAND UNLOCK REQUIRED", not a page of zeros
    cookie              HttpOnly confirmed; document.cookie reads ""
    session body        carries no token
    leak scan           password absent from the DOM; no api_key,
                        secret, private_key, Authorization or
                        DATABASE_URL anywhere in the rendered page
    mobile              zero horizontal page overflow at 390px
    sourceless cells    0 across all 33 renders

Source-to-screen, all three sides independently derived — the fixture
records recounted from scratch, the payload the real route returned, and
the text a real browser rendered:

    stopped              COMPLETED        | 12 of 12 | CONNECTED_QUIET
    armed-no-frames      ARMED_NO_FRAMES  |  0 of 12 | CONNECTED_QUIET
    collecting           COLLECTING       | 12 of 12 | CONNECTED_ACTIVE
    quiet-healthy        COLLECTING       | 12 of 12 | CONNECTED_QUIET
    disconnected         INTERRUPTED      | 12 of 12 | DISCONNECTED
    restart              COLLECTING       | 12 of 12 | CONNECTED_ACTIVE
    exhausted-allowance  STOPPED          |  8 of 12 | CONNECTED_QUIET
    partial-coverage     COLLECTING       |  5 of 12 | CONNECTED_ACTIVE
    failed               FAILED           | 12 of 12 | CONNECTED_QUIET
    scheduled            SCHEDULED        |  0 of 12 | NOT_STARTED
    unavailable          HTTP 503 JOURNAL_UNREADABLE, page says UNAVAILABLE

All three agree on every row.

Two defects the browser pass caught that the unit tests could not: 4px
of horizontal scroll on a phone, from the synthetic banner's bleed being
hard-coded to 16px against a 12px mobile gutter; and market-coverage
rows six lines tall on a phone, from a 33-character slug being squeezed
rather than allowed to scroll. Both fixed; both re-verified.

## SYNTHETIC FIXTURES ANNOUNCE THEMSELVES

Every preview scenario renders a red banner at the top of the page:
"SYNTHETIC FIXTURE — NOT VENUE DATA, NOT A RUN RESULT", naming the
scenario. It is driven by a `preview` key that only the preview harness
sets, so a production snapshot shows nothing. Every fixture record also
carries `"synthetic": true` in its payload.

## RUNNING THE PREVIEW

    cd backend
    python tools/command_center_preview.py --port 8899 --list
    python tools/command_center_preview.py --port 8899 --scenario collecting
    # then http://127.0.0.1:8899/command/center.html
    # unlock with the --password value; switch states with
    #   curl http://127.0.0.1:8899/preview/use/<scenario>

    python tools/command_center_shots.py --base http://127.0.0.1:8899 --out /tmp/shots
    python tools/command_center_reconcile.py --shots /tmp/shots

The preview substitutes ONE thing: a stub pool in place of Postgres. The
route, the auth dependency, the reader, the read model and the page are
the production article. The stub answers exactly the three reads the
reader performs and raises on a fourth, so a new query cannot silently
render as "no data".

## THE REMAINING LIMITATIONS, NAMED

**1. Views 2 and 3 will read UNKNOWN in production.** `backend/Dockerfile`
copies `research/beta48/shadow`, the top-level JSON, and exactly one
acceptance file — `incentive_manifest.json`. The suite XML,
`evaluation.json` and `incentive_opportunity.json` are NOT in the
production image, on purpose: that directory is 4.9 MB of patches and
evidence artifacts that do not belong in a production image. So in the
deployed API the test-evidence and replay-economics figures render
UNKNOWN with "artifact not shipped in this image" as the stated reason.
They render correctly in the preview, where the repository is present.
Fixing it means either a narrow COPY of the specific artifacts or
serving them from a store — both are Dockerfile or schema changes, and
both are deployments. Neither is done here.

**2. The deployed SHA reads UNKNOWN unless the platform stamps it.**
`deployed_identity()` reads RENDER_GIT_COMMIT / GIT_COMMIT /
SOURCE_COMMIT / BETTOR_DEPLOYED_SHA and reports UNKNOWN when none is
set. It deliberately does NOT fall back to the working tree, which is
not what is deployed.

**3. This is verified against synthetic fixtures, not against the live
run.** The observation run has not collected a single frame — see
OBSERVATION_BLOCKER_20260923.md. So the live-operation view has been
exercised against eleven hand-built states and against zero real ones.
When frames exist, the same reconciliation should be re-run against the
real journal before any figure on that view is quoted.

**4. Nothing is deployed.** Publishing the page would redeploy
sportsassets-api. That is held.

# render-ops.yml — the prose

GitHub caps a workflow file at 512,000 bytes. render-ops.yml reached
511,808 of them, and an eight-line comment added on 2026-09-21 pushed
it to 512,388 — over the ceiling. Every dispatch then returned
startup_failure, which is how this file came to exist: the lever that
operates Render, reads the database and holds the kill switch was
disabled by a comment.

Nothing was deleted. Each block below is the verbatim text that stood
above the code it documents, keyed by the anchor left behind in its
place. The workflow now sits far enough under the ceiling that a note
can be added without taking the lever down again.

## [R01]

Documents:

```
name: render-ops
```

Render operations from the Actions tab (2026-09-05, the disk-full outage).

The recovery ran on screenshots: this session cannot reach Render's API
or dashboard (egress policy), and the owner has no terminal. Every read
of a service's events, deploys and logs, and every resume / redeploy /
database maintenance step, went through a person. This job is the
bridge: one dispatch = one read or one guarded action, printed in full.

THE KEY. Prefer the RENDER_API_KEY repository secret. Until it exists,
the key may be passed as the `render_key` input; it is masked before
anything else runs, so it never appears in the log. Rotate the key in
Render (Account Settings -> API Keys) when this incident is over.

FAIL CLOSED. Reads print what came back or say the read failed; nothing
is inferred from silence. Every state-changing action (resume, suspend,
restart, deploy, the two maintenance statements) requires confirm=DO and
prints the resolved service id and name BEFORE it acts. The `sql` action
runs ONLY the statements named here -- there is no free-text SQL.

## [R02]

Documents:

```
ARG="$(jq -r '.inputs.arg // empty' "$GITHUB_EVENT_PATH")"
```

arg and render_key are read from the event payload on disk, not
mapped through `env:`: the runner prints a step's env block in the
log header BEFORE any ::add-mask:: in the script runs, so a value
mapped there (an env-set secret, a pasted key) would be exposed.

## [R03]

Documents:

```
ID=$(svc_id "$SERVICE"); [ -z "$ID" ] && { echo "no service named $SERVICE"; exit 1; }
```

READ-ONLY. What the LIVE service is actually building
from, which is not necessarily what render.yaml says:
rootDir is Blueprint-managed and a plain code deploy
does not re-apply it.

## [R04]

Documents:

```
need_confirm
```

confirm=DO. Reconcile the LIVE service to what
render.yaml ALREADY DECLARES. The values are fixed here
rather than taken from `arg` on purpose: render.yaml is
the source of truth, so this action can only move the
service toward it and can never set an arbitrary root.
A later Blueprint sync produces the same result.

## [R05]

Documents:

```
ID=$(svc_id "$SERVICE"); [ -z "$ID" ] && { echo "no service named $SERVICE"; exit 1; }
```

Render's metrics API, one point per step: memory in bytes
and CPU in cores per instance. Added 2026-09-05 when the
workers were OOM-killed thirteen times in four hours and
the log lines before each kill were only the steady-state
mix -- the log could not say whether RSS ramps or spikes.

## [R06]

Documents:

```
MIN=30; TEXT=""; START=""; END=""; DIR=backward
```

arg: minutes back (default 30) | a text filter over the last 3 h |
a UTC window today "HH:MM:SS/HH:MM:SS" (printed oldest first, so
the lines around a crash read in order), optionally followed by
a space and a text filter.

## [R07]

Documents:

```
mirror-on)  need_confirm; SQL="INSERT INTO ingestion_state (key, value) VALUES ('mirror_live', 'true
```

THE MIRROR'S INCREASE SWITCH (owner order 2026-09-05, "switch on
the mirror system 100% and turn off the entry system"). The P1
reconciler (workers/mirror_live.py, step 0) increases a book
only when ingestion_state 'mirror_live' reads EXACTLY JSON true;
false, absent, malformed or unreadable is exits-only. The env
PMUS_MIRROR=on plus PMUS_MIRROR_WHALES decide the mode and the
allowlist (env-set above); this row is the second key. No admin
endpoint writes it yet (P1 step 10), so it is an auditable
confirm=DO action here: every flip is a dated run in the
Actions log. mirror-state prints every mirror key so the flip
is read back from the row the worker reads, not assumed.

## [R08]

Documents:

```
pause-on)   need_confirm; SQL="INSERT INTO ingestion_state (key, value) VALUES ('live_trading_paused
```

THE ADMIN KILL SWITCH, WRITTEN WHERE THE CODE READS IT
(R5, 2026-09-21). live_executor._is_paused reads exactly
one thing: ingestion_state where key = PAUSE_KEY =
'live_trading_paused', json.loads'd and passed through
bool(). api/live_executor_state.set_paused writes that
same row as jsonb via the same upsert. This is that
write, not a second mechanism -- the statement below is
byte-for-byte what set_paused(True) produces, and the
worker cannot tell which one wrote it.

WHY IT IS NEEDED AT ALL: the row was ABSENT. _is_paused
returns False on None, so the kill switch documented as
"no further orders" had never been armed -- it failed
open on absence exactly as WHALE_EXIT_ENABLED did. The
function is otherwise strict (unreadable and malformed
both count as PAUSED); only missing was permissive.

POST /api/admin/live/pause is the ordinary lever and
stays the ordinary lever. This one exists because that
endpoint needs a reachable API and an operator session,
and an audited Actions run is the path this environment
has. Both write the same row. pause-state reads it back
from the row the worker reads rather than assuming.

## [R09]

Documents:

```
mirror-preflight) SQL="SELECT what, value FROM (SELECT key AS what, left(value::text, 240) AS value 
```

THE PRE-FLIGHT READ (2026-09-05). The pre-flight review of
the switch named the rows the first ON tick reads before a
dollar moves: the mapping quarantine, the premap gate, the
side-echo counter behind first_fill_gate, the loss stop,
the pause and overspend halts, the roster and clips -- and
the counts that show whether any lane is still placing.
One statement, one row per fact, so the flip is decided
from what the worker will read and not from memory.

## [R10]

Documents:

```
mirror-rearm) need_confirm; SQL="WITH gone AS (DELETE FROM ingestion_state WHERE key = 'mirror_loss_
```

The loss stop's only re-arm: nothing in the worker deletes
'mirror_loss_stop' once it trips (pre-flight 2026-09-05),
so the operator does it here, on the record. THE RE-ARM
RESTARTS THE WINDOW (L1, 2026-09-07): the 16:51Z re-arm
re-tripped at 17:07:45Z on the morning's losses, still
inside the worker's trailing 24 h. ONE statement now deletes
the stop AND writes 'mirror_loss_rearm' = {at: now(), by:
render-ops, prior: the deleted stop or null}; the worker's
loss sum counts only what happened after that instant
(mirror_live._loss_window_start) and its next receipt says
`since` / `rearmed_at`. The limit does not move. The SELECT
reads both rows back.

## [R11]

Documents:

```
mirror-post-only-rearm) need_confirm; SQL="DELETE FROM ingestion_state WHERE key = 'mirror_post_only
```

THE POST-ONLY BLOCK'S RE-ARM (E31, FILL lane 31), the loss stop's own
pattern: the venue filled a post-only rest AT CREATE and its own
`aggressor` did not read as a maker, so the worker wrote
`mirror_post_only_block` and every ADD is refused by that name until a
human clears it (the exits keep running; the flag never left the send).
This DELETES the key and prints every mirror_ state key so the operator
sees what stands. There is no automatic re-probe by design: a human
reads the block's receipt (book, order, filled, aggressor) first.

## [R12]

Documents:

```
loss-breaker) SQL="WITH r AS (SELECT x.at FROM (SELECT (value->>'at')::timestamptz AS at FROM ingest
```

THE SLEEVE'S BREAKER, READ AS THE MIRROR READS IT (L2, 2026-09-07).
live_executor._loss_breaker_tripped sums EVERY settled live_orders
row of the last 24 h (the mirror's standing rows settle there too)
against PMUS_LOSS_BREAKER_USD; since L2 the mirror reads that sum
from its re-arm window (mirror_loss_rearm's at, else 24 h back).
Both windows, by lane / whale / status, then the newest settled
rows -- so a `loss_breaker` census is read off the rows themselves.

## [R13]

Documents:

```
mirror-tick) SQL="SELECT h.service AS what, e.key, left(e.value::text, 8000) AS value FROM service_h
```

THE LAST TICK, WHOLE (2026-09-06). The worker logs its mode
line with the full stats dict, but the logs action above
keeps 400 characters of a message and the census that says
WHY a tick placed nothing sits past that cut. The same dict
is the mirror_live heartbeat's detail, so this prints it one
key per row, plus the shadow's, plus the live books and the
newest orders -- the tick as the worker recorded it.
(2026-09-09 23:4xZ, after FILL lane 26 went live at 22:48Z) plus the
chain listener's heartbeat -- its `sweep` block {runs, found, handled,
failed, skipped_throttled, last_span, last_at} is the only record of
the logs the socket did not hand over (the worker logs a sweep only
when it finds one or fails, so a silent log is 'nothing found' OR
'never ran') -- and the poller's (page_overflow, new, detect_lag_s).
The heartbeat value column is cut at 8,000 characters (2,400 until
2026-09-10 02:1xZ): the mirror's census JSON runs past 4,000 with 246
names and the newest lanes' names sit at its tail (E28's walk_row_* /
ledger_stale_*, E29's hand_*), unreadable under the old cut.

## [R14]

Documents:

```
tick-ring) SQL="WITH r AS (SELECT e FROM ingestion_state s, jsonb_array_elements(CASE WHEN jsonb_typ
```

THE TICK RING (FILL lane 14, 2026-09-09; docs section 58). mirror-tick
prints ONE tick -- tick_s 38.0 at 15 books (tick_2245 331), 76.9 s at
54-64 books (hourly_2030 426) -- and the distribution the owner's "as
fast as his" line is judged on was in no file. The worker keeps the
last 240 full ticks' timing (about 2 h at POLL_S 30 s) under
ingestion_state 'mirror_tick_ring' = {"ticks": [[at, tick_s, walk,
orders, books, candidates, books_live, venue_calls, cand_cap,
cand_reads, yielded, fast_n, fast_wait, fast_work, fast_prelude], ...],
"at"}, written at most once a minute; this prints it per HOUR: ticks,
tick_s median / p90 / max, candidates median / max, books median,
fast_wait p90, yields (lane 15's, 0 before it), fast_prelude median.
Every field is read by its position (e->>1 tick_s, e->>4 books, e->>5
candidates, e->>10 yielded, e->>12 fast_wait, e->>14 fast_prelude)
behind jsonb guards, so a NULL, scalar or malformed value -- an entry
that is not an array of 15 numbers, or a number past float8's range
in a read position (a jsonb number is a numeric: each read position
is range-checked as a numeric before the float8 cast) -- prints
NOTHING for that row and never an error that stops the hourly bundle
(ON_ERROR_STOP). Rows are the hour buckets by their instant, newest
first (across midnight 00:00 prints above 23:00).
A deploy starts the ring empty: no rows for the hours before it.
Read-only, LIMITed (48 hour buckets; the ring holds about two).

## [R15]

Documents:

```
mirror-refusals) SQL="SELECT o.id, o.placed_at, o.side, o.intent, o.wire, o.qty, o.reason, left(o.re
```

THE REFUSAL RECEIPTS (2026-09-06). A place_refused row keeps
the adapter's raw (expected cost, the venue's preview order)
in receipt; this prints the newest refused rows whole so a
money guard is changed on the venue's numbers, never a guess.

## [R16]

Documents:

```
mirror-pnl) SQL="SELECT b.id, b.us_market_slug AS slug, b.state, b.intent, b.ratio, b.ledger_net AS 
```

THE MIRROR'S P&L TODAY (2026-09-06, owner question). Realized
is every sale booked on a mirror book (partials included,
mirror_books.realized_pnl), settled is the standing row's
settlement copied onto the book, open is the cost basis still
held (ledger_net x avg_cost on a long; |ledger_net| x (1 - avg_cost),
the collateral, on a short: avg_cost is the CONTRACT price). By book, then the day's totals;
UTC day boundaries, and every figure is what the tables hold.
E3 (2026-09-06 23:10Z): settled is the venue's WHOLE-position figure and already holds the
realized part, so realized + settled counts a settled book's sales twice. The totals row's
day_pnl is the one true number, the loss stop's rule: settled over the closed-settled books,
realized over every other book (open, and closes cashed out / cancelled with settled NULL).

## [R17]

Documents:

```
his-recent) SQL="WITH his AS (SELECT t.condition_id, max(t.ts) AS last_ts, min(t.ts) AS first_ts, co
```

HIS LAST TWO HOURS AGAINST OURS (2026-09-06, owner: "I don't
see trades firing"). One row per market he filled in the
last 2 h, newest first: his fills, shares and dollars, the
shadow's latest verdict on it (reason + the resolver's
explain when unmapped, the US slug when mapped), and the
live book on that condition with its state and what it
holds. Every miss is named from the tables, never guessed.

## [R18]

Documents:

```
mirror-why) SQL="WITH his AS (SELECT DISTINCT t.condition_id FROM trades t JOIN whales w ON w.id = t
```

WHY A MAPPED MARKET HAS NO BOOK (2026-09-06, owner: "we need
more volume"). For every market he filled in the last 2 h that
the shadow MAPPED to a US slug and that has no live book: the
shadow's newest verdict with its whole detail (venue vs ledger
on a freeze, the drift, the snapshot state), so the refusal is
read from the row and not inferred from a truncated census.

## [R19]

Documents:

```
copy-lane) SQL="SELECT o.placed_at::time(0) AS at, o.whale_username AS whale, o.side, o.status, roun
```

THE COPY LANE TODAY (2026-09-06): every live_orders row of the
last 8 h -- the lane that competes with the mirror for a slug
(admission refuses `venue_already_holds` / `legacy_row` on a
slug another lane holds). Newest first, then by status per hour.

## [R20]

Documents:

```
c3-rows) SQL="SELECT split_part(identifier, '-', 1) AS prefix, count(*) AS rows, count(DISTINCT spli
```

THE VENUE ROWS THE MAPPER SAW (2026-09-06): every us_premap row
on two of tonight's soccer events he is trading (Juventus/Milan,
Espanyol/Sevilla), so the per-team yes/no refusal can be
reproduced offline against the rows themselves.
C3 (2026-09-06 20:35Z): the venue's rows, ANY prefix, for the
events where his unmapped dollars sit tonight (spreads,
draws, btts, totals lines, exact scores), his rows for
those slugs, and the venue's prefix census for the day.

## [R21]

Documents:

```
c4-rows) SQL="SELECT split_part(identifier, '-', 1) AS prefix, count(*) AS rows, min(identifier) AS 
```

THE MASCOT SPREADS HE FILLED, AND THE VENUE'S OWN ROWS (2026-09-07 02:5xZ).
His cfb-lou-miss / cfb-wisc-nd spread flow (18 markets, $15.8k in the 2 h to
02:38Z) read spread:subject-uncertified, spread:pair-unwitnessed,
spread:title-unreadable and spread:code-unmatched (C4). The shadow keeps only
the refusal NAME (mirror_shadow.detail.explain), never the trace, so the chain
is reproduced offline from the rows themselves. c4-rows: the venue's prefix
census on the two events; every non-asc row (the aec moneyline sides, the atc
rows the grammar class reads for its per-side contract); the asc spread rows,
one line per identifier with both sides; his fills per market AND outcome with
his title, the event title and his outcome_index. c4-shadow: the FIRST fill per
spread market (the one mirror_shadow._first_context judges); the shadow's newest
row per market with its whole detail (venue vs ledger on the frozen moneyline);
every mirror_books row on the two events, any state; the grammar echo's
certified keys; the copy lane's live_orders on the venue's cfb slugs.

## [R22]

Documents:

```
cfb-his) SQL="WITH his AS (SELECT t.condition_id, t.market_slug, t.outcome, t.size * t.price AS usd,
```

THE COLLEGE FOOTBALL BUCKET (2026-09-07 13:5xZ, coverage-gap: cfb moneylines
'no mark: book unreadable' $132k, spreads $131k across six refusal names, totals
line-absent $9k). cfb-his: every cfb market he filled in the last 30 h per
(slug, outcome) with his title, the event title, his outcome_index, the shadow's
latest verdict and the book; the FIRST fill per market (the one _first_context
judges); the family x verdict census with the slugs. cfb-venue: the venue's aec
rows for the dates (sides, event_title, question); per event he traded, the prefix
census with every FULL-GAME tsc/asc tail the venue lists (line-absent is read off
this); per event, the aec sides, the venue's event_title, one full-game asc
question (mascots), one 1h asc question, and the winner-1h atc rows' code=question
(the school beside the code); the grammar echo's certified keys. Reads only.

## [R23]

Documents:

```
nfl-rows) SQL="WITH his AS (SELECT t.condition_id, t.market_slug AS his_slug, max(t.market_title) AS
```

THE NFL BOARD (2026-09-10 00:4xZ, owner: "he has a ton of NFL trades and we have
zero of them mirrored"; his-board at 00:34Z: nfl 10 markets / 96 fills / $26,628
in 24 h, every one 'unmapped (no US contract bound)'). Four reads: (1) his NFL
rows of 30 h (slug, title, the stored event title, outcomes, dollars) with the
shadow's newest verdict per market (reason, family, explain, the venue slug it
bound if any); (2) the venue's league-code census on the Sep 10-15 dates, so
the code the venue files football under is read from the table, not assumed;
(3) every SUFFIX shape the venue lists under '-nfl-' (full game, halves,
quarters, totals, spreads) with sides and one question; (4) for each of his
NFL events, the venue's rows carrying his two team codes in either order under
ANY prefix and league code -- the identity witness a mapping lane needs;
(00:4xZ) the suffix census is cut to FULL-GAME tails (lines folded to N) and (5)
the Patriots/Seahawks full-game aec/asc/tsc rows verbatim (the venue dates
that event 2026-09-09 against his 2026-09-10). Read-only, LIMITed.

## [R24]

Documents:

```
nfl-team) SQL="SELECT identifier, side_norm, team_abbr, team_name, team_safe_name, team_id, team_lea
```

THE NFL TEAM DICT AS THE VENUE STATES IT (C10, 2026-09-10; read-only): the C6
team columns of migration 055 on every aec-nfl row (abbreviation / mascot /
safeName / id), the asc 3.5 rows beside them, the venue's distinct NFL team
records, his NFL spread slugs over 14 days, and the ne-sea atc rows.

## [R25]

Documents:

```
mirror-by-league) SQL="SELECT split_part(b.us_market_slug, '-', 2) AS lg, count(*) AS books, count(*
```

THE STUDY'S TWO METRICS ON THE DESK'S OWN DATA (2026-09-10; read-only; docs/rn1-book-anatomy.md):
mirror-by-league -- our books' settled P&L, stake, ROI and won/lost by league (7 d), by contract
prefix (7 d) and in total (24 h / 7 d / life); his-matched -- his matched share of cost, pair
cost and residual by league and by sport over 7 days from the fills we hold, and his sells.

## [R26]

Documents:

```
two-legged) SQL="WITH f AS (SELECT 0.000::float8 AS fee), s AS (SELECT DISTINCT ON (condition_id) co
```

THE TWO-LEGGED SHADOW'S ANSWER (E38, 2026-09-10; read-only; docs/mirror-coverage.md
section 77). NOTHING IN THIS LANE SENDS AN ORDER and nothing the live mirror sends has
changed: mirror_shadow computes and RECORDS the two-legged target; going live is a later
lane the owner decides after reading these rows. This preset REPLACES pair-when, which asked
the same question against his fills alone; this one asks it against OUR achievable prices.
The objective is HIS MATCHED PAIRS WHERE THE PAIR CLEARS -- not his net (which erases the
matched book: 58.6% of his cost buys BOTH outcomes) and not his gross (which would buy his
losing tennis pairs at 1.0136 beside his winning soccer at 0.9325). His residual is
recorded, never targeted.
READ rest_* AND NOT take_*. The two outcomes share ONE book here, so the other leg's ask
is the complement of this leg's bid and CROSSING BOTH TOUCHES COSTS EXACTLY 1 + THE SPREAD:
take_admissible is 0 by arithmetic, not by market conditions, and is printed only so the
identity is visible rather than assumed. The pair that can clear is the POST-ONLY one --
rest on BOTH bids, cost 1 - the spread -- obtainable only if BOTH rests fill (statement 2).
THE FEE IS ONE LITERAL: 0.000::float8 AS fee, per contract. A pair is TWO contracts, so
admissibility is `pair cost + 2 x fee < 1.00` and the break-even fee per contract is the
margin HALVED. The settlement rows put the taking fee near 6% of shares x p x (1-p) while a
position built from rests alone paid nothing, so 0 is the right default for a rested pair
and breakeven_fee_pc is the column that says what we could afford if that is ever wrong.
Statement 1: by sport with an ALL row over the newest row per condition in 24 h -- his
coverage in his own fill dollars, rest_pair_cost vs his_pair_cost, rest_edge_avg,
breakeven_fee_pc. Statement 2: whether both rests actually fill. Statements 3-4: the same
arithmetic per market, dollar-ranked. Nothing is written; four SELECTs.

## [R27]

Documents:

```
outcome-census) SQL="WITH his AS (SELECT t.condition_id, COALESCE(NULLIF(m.sport, 'unclassified'), N
```

IS HIS BOOK ACTUALLY BINARY? (E38, 2026-09-10; read-only.) The his-matched arithmetic
pairs outcome_index 0 against outcome_index 1 and filters `outcome_index IN (0, 1)`.
That is a COMPLETE hedge only if every condition is binary, so that 0 and 1 are exact
complements paying 1.00 between them. If any condition carried a third outcome -- a
three-way soccer game with the draw as outcome 2 on the same condition -- then
LEAST(b0, b1) is not a hedge at all, both legs can pay zero, and the 0.9325 that
carries this whole finding would be meaningless.
The STRUCTURE says binary: market_tokens must hold exactly two tokens for a
condition (mirror_shadow.py:1011) at complementary indices (map_lane.py:596, :603), a
soccer 1X2 is three separate markets -- a home slug, an away slug and a `-draw` slug
(the league-rows-por preset above reads exactly that shape) -- and even an exact score
is one market per score line. This preset MEASURES it instead of arguing it.
Statement 1: his 7-day conditions by sport -- how many carry any outcome_index >= 2
and what dollars sit on them, how many carry more than two distinct assets, and the
one-sided / two-sided split, with an ALL row. Statement 2: the token catalogue's own
count for the conditions he traded -- tokens per condition and the outcome names, so a
three-token condition would print as its own row. Statement 3: the offending
conditions themselves, named and dollar-ranked, so an exception is a slug and not a
count. A clean run is `oi_ge_2` 0 and `over_2_tokens` 0 on every sport, and one row of
`tokens = 2` in statement 2. Nothing is written; three SELECTs.

## [R28]

Documents:

```
epl-rows) SQL="SELECT split_part(identifier, '-', 2) AS league, split_part(identifier, '-', 1) AS pr
```

VERIFY THE DAY (owner 2026-09-06 ~22:55Z: every book we opened or closed today against RN1's own
deduped fills on the same market and the venue's resolution: our side and size vs his net at
our first/last fill, our realized+settled vs his settled P&L on the market, his first fill vs ours.
THE VENUE'S OWN ROWS FOR A LEAGUE THE MAPPER MISSED (2026-09-07 00:05Z):
his epl-eve-mun / epl-ars-che flow ($90k + $118k in 6 h) read
no_side_match; the guessed atc-epl-<a>-<b>-<date>-<side> slugs
404 on the venue while its index holds two Everton/Man United
events (MIRRORCOVER search:2ev). Print every us_premap row whose
identifier or question names those games, the venue's soccer
league codes for the date (with counts), and the -fh- rows'
shape, so the venue's naming is READ, never guessed.

## [R29]

Documents:

```
gap-soccer-his) SQL="WITH his AS (SELECT t.condition_id, max(t.market_slug) AS his_slug, max(t.marke
```

THE SOCCER GAP, HIS SIDE (2026-09-07 13:5xZ, coverage program bucket 1: soccer
moneylines / totals / spreads / btts refusing no_side_match in leagues the mapper
knows, ~550k USD in 24 h). One row per market he filled in the last 24 h in those
league codes with no mirror book: his verbatim slug, title, the stored event title,
every outcome he filled, fills, dollars, first/last fill, the shadow's latest
verdict and WHEN it was written (a verdict older than the rule that maps it is
stale, epl_unmapped.md section 1.4), whether the market settled. Then dollars by league.

## [R30]

Documents:

```
gap-soccer-venue) SQL="WITH ev AS (SELECT DISTINCT split_part(t.market_slug, '-', 1) AS lg, split_pa
```

THE SOCCER GAP, THE VENUE'S SIDE (same read). For every event he traded in those
league codes (his <lg>-<a>-<b>-<date>), every venue event on that date sharing at
least one of his team codes: share = (va=a)(vb=b)(va=b)(vb=a) as four digits (1100 =
both codes his, identity / C2 alias; 1000 or 0100 = one code differs, the C5 case;
0011 = his order reversed), the venue's stored event title (the lookup key C5 needs),
the full-game per-team atc rows count, the tsc lines, the full-game asc rows with the
side_norm the sweep wrote (digits = the pre-C3 rows), whether a btts row exists, and
the two per-team questions verbatim (the P4 witness). Then his events the venue
lists under NO code sharing either team code on that date.

## [R31]

Documents:

```
planned-unopened) SQL="WITH his AS (SELECT t.condition_id, max(t.market_slug) AS his_slug, count(*) 
```

THE 'NO MARK: BOOK UNREADABLE' BUCKET (2026-09-07 13:5xZ, the coverage census:
$418k / 61 mapped markets in 24 h whose latest shadow verdict is 'no mark').
A no-mark row is one of three venue readings (mirror_shadow.py:1929-1979):
an empty book (a terminal state, or OPEN with no makers), a one-sided
book (a bid and no ask: the mark reads the ask only), or a read that
raised (bbo_error). The census keys the LATEST verdict, written ~20 min
after his last fill, so it says nothing about the market while he traded.
Since W1 / R1 the shadow names the reading itself ('no mark: venue state
<STATE>', 'no mark: bid only <px>', 'no mark: empty open book', 'no mark:
read failed <Exc>', 'no mark: no state, empty'): every read here keys the
PREFIX 'no mark:' so the older 'book unreadable' rows still match.
1: the bucket by the venue's own state / sides / error on that latest row;
2: per market, every shadow row in 30 h classified (two-sided OPEN reads,
one-sided, empty-open, terminal, halted-not-terminal, raised, no state) with
the first two-sided OPEN read vs his first/last fill and the game start;
3: the verbatim rows of the six largest; 4: his fills by minute on those six;
5: the live heartbeat's whole census (the mirror-tick preset cuts it at
2400 chars) and the loss-stop key. Reads only, LIMITed.
PLANNED-UNOPENED (2026-09-07, the 'planned_unopened' investigator): the
markets the shadow planned a leg on (would_side set, two-sided OPEN book)
while he traded, on which the LIVE lane opened no book of any state. The
lane's per-candidate refusal is not persisted, so this reads the nearest
evidence. 1: per market -- his window and dollars, which of his two
tokens the shadow named long/other, WHEN his first fill on the venue's
long-side token landed (before it the fills sit only on the short-side
token and map_market hands the live lane long_asset=None: the silent
`if not la: return` at mirror_live._tick_candidate), the count of during-
window shadow rows with long_asset NULL vs set, the reasons over the
window, the venue's us_premap sides/intents for the identifier, the token
catalogue, our live_orders on his tokens (the ledger lane and the
slug_recent_copy/legacy_row referees), and how many of his conditions
were active (6 h lookback) at his first fill (the newest-first walk's
MAX_MARKETS_PER_TICK=40 candidate cap). 2: every loss-stop trip the
order rows remember (reason mirror_loss_stop, by half hour) beside the
books opened per hour (when the lane was opening books at all). 3: the
shadow's rows through his window on the six largest, one in ten plus the
first and last, with long_asset, his_long/his_other, target, would_side.
Reads only, LIMITed.

## [R32]

Documents:

```
coverage-gap) SQL="WITH his AS (SELECT t.condition_id, split_part(COALESCE(t.market_slug, ''), '-', 
```

THE COVERAGE CENSUS KEYS THE VERDICT AT HIS LAST FILL (W1 / R3, 2026-09-07).
`sh` was the NEWEST shadow row per market, written 44-83 min after his
last fill on a market the venue had expired, so $385k of two-sided OPEN
markets printed under 'no mark' (gap_book_unreadable.md section 0). Now:
per market, the newest row at or before his last fill in the window whose
venue state is not terminal (ms.STATE_TERMINAL's four strings), falling
back to the newest row; `judged` prints when that row was written. Reads only.
Since W2 / P2 (migration 054) the live lane persists the candidate's own
refusal: for a market with NO book `why` prefers the newest
mirror_candidate_refusals row at or before his last fill (else the newest),
printed `live:<refusal>`, over the shadow's verdict; `judged` is that row's
time when it decided. A database without 054 fails this preset until
migrate runs on the next boot (start.sh runs it before the API).

## [R33]

Documents:

```
cand-refusals) SQL="WITH his AS (SELECT t.condition_id, max(t.market_slug) AS his_slug, count(*) AS 
```

THE CANDIDATE'S OWN REFUSAL, LAST 24 H (W2 / P2, 2026-09-07; the table
mirror_candidate_refusals, migration 054: one row per (whale, market)
transition of the live lane's refusal name, re-stamped every 900 s).
1: by the LATEST refusal per market, the markets and his dollars under
it, and the no-book part of both (a market that later opened a book
is counted, and told apart); 2: every name's rows and markets, with
the tick's wall time, candidate reads and his active conditions when
the rows were written (the walk's cap made visible); 3: the 40 largest
no-book markets by his dollars with their refusal path through the
day (name@HH:MM and the signed target where the row carries one).
Reads only, LIMITed.

## [R34]

Documents:

```
mirror-hand-release) need_confirm
```

THE HAND'S RELEASE (E29, 2026-09-10, FILL lane 29; docs 71). A hand
reduce E24 adopts marks the book HAND-EXITED (last_plan->'hand_exit'
and the ingestion_state key 'mirror_hand_exit', a dict keyed
'<whale>:<condition_id>'): the book never increases again and the
market opens no new book for the same whale while the memo holds.
This preset lets the mirror back into the play by BOOK ID: ONE
statement (its own transaction under psql -c) removes the memo entry
built from the book row's whale and condition_id (jsonb minus on the
key) and the book row's last_plan minus 'hand_exit'; it prints the
entries it removed, or 'no hold on book N' when neither carried one,
and touches nothing else (updated_at is not named: the fills-missed
census reads the newest-UPDATED book on the condition). The worker
reads both on its next tick; a tick that read the row before the
release does not carry the record back (mirror_live._write_plan).

## [R35]

Documents:

```
mirror-register) need_confirm
```

THE OPERATOR REGISTER (E5 / P1, 2026-09-07; migration 056). Book 77
(Shelton/Tsitsipas) read venue 1,128 / ledger 0 / manual 0 since
2026-09-06 -- a lost placement response whose fill the venue reports
and the ledger never booked -- and froze on every tick. This dispatch
is the ONLY writer of mirror_registered_positions: the owner's dated,
named statement that the shares are outside the book. arg is
`<book_id>=<shares> <note>` (shares signed like the ledger, negative =
the short side; a note is required). The slug, asset and condition
come from the book's OWN row -- never typed -- and ONE statement
prints the venue's reading the worker last wrote (venue_net), the
ledger, the desk's manual shares and the register so far, then
INSERTs only when the figure equals venue - ledger - manual EXACTLY,
the book is frozen and was read inside 10 minutes, the figure carries
the book's own leg's sign (never a negative on a long book: the live
plan would then sell shares the venue does not hold), and no row
stands on the slug yet. The verdict column says registered or REFUSED
(a book id with no row is REFUSED too: E5 review L2).
The worker reads the signed sum per slug beside the manual shares
(mirror_live._SQL_REGISTERED_SHARES) and never writes here.

## [R36]

Documents:

```
mirror-frozen) SQL="SELECT b.id, left(b.us_market_slug, 40) AS slug, b.intent, b.frozen_reason, b.fr
```

THE FROZEN BOOKS (E5 / P2, read-only): every frozen book with the
venue's reading the worker last wrote, the ledger, the desk's manual
shares, the register, his net and the target, when it froze, the
frozen exit's verdict off the last plan (frozen_exit: venue_own /
target / held-by-name or the act's word) and the last venue-sized
reduce order (reason frozen_reduce); then the register rows.
E24 (2026-09-09, FILL lane 24): `hand` beside manual / registered --
the desk's hand fills on the slug as the worker last judged them off
the plan (last_plan->'hand': net / adds / reduces / fills / verdict
/ adopted / orders), the fourth explained term; no migration.

## [R37]

Documents:

```
frozen-detail) SQL="SELECT b.id, left(b.us_market_slug, 40) AS slug, b.state, b.frozen_reason, b.las
```

THE FROZEN BOOK IN FULL (2026-09-08): every frozen book's
own row (its last plan, its last reason, the freeze tick
count, the flow reference) with its market's closed /
resolved flags, then every order of a frozen book and every
non-terminal order anywhere -- for a book that stays frozen
after its order should have settled (book 455, order 2475)

## [R38]

Documents:

```
resolution-lag) SQL="SELECT count(DISTINCT t.condition_id) AS unresolved_traded FROM trades t LEFT J
```

THE CATALOGUE LAG (2026-09-08): a mirror book ends only when its
market's gamma row reads closed / resolved (step M) or the venue
settles its row; book 43's row (a 09-06 match) still read
closed=f resolved=f two days on. The resolution sweep chases
unresolved traded conditions 500 at a time with no order -- this
counts that backlog, shows the analytics beat, and lists every
open or frozen book with its row's flags and refresh time

## [R39]

Documents:

```
book=*) if [[ ! "$ARG" =~ ^book=([0-9]+)$ ]]; then echo "book: arg must be book=<id> (got '$ARG')"; 
```

ONE BOOK IN FULL (2026-09-08): `book=<id>` -- the row with its
last plan, every order of it, our booked fills on its standing
row, and HIS fills on its condition (time, side, size, price)
-- for reading why a paired book landed on the other sign;
then (2026-09-08 14:1xZ, Martinez 529/534) the candidate
refusal rows on the book's own condition, newest first, so
a market that will not REOPEN after a close is read from
the live lane's own verdict (venue_already_holds on a
sub-share venue residual) and not inferred

## [R40]

Documents:

```
his-board) SQL="WITH his AS (SELECT t.condition_id, t.side, t.size, t.price, t.ts, t.market_slug, (m
```

HIS LEDGER BY BOARD (2026-09-09 ~13:55Z, owner: "tell me how much money he is
down today compared to us, and how much he made from sports we can't
copy (global vs US Markets)"): his fills of 24 h under the D1 collapse
(his-day's rows, chain-first, never a chain+poll double), each market
bucketed by what the live lane did with it -- MIRRORED (a book of
ours), `unmapped` (the mapper bound no US contract: the venue does not
list it, or a mapping gap the coverage program owns), every other live
refusal by its name (no_mark, venue_already_holds, game_unreadable,
cand_unread_capped ...), or the shadow's word when the live lane never
judged it -- with his dollars, his settled dollars and his settled P&L
at the resolved price; then the same by league for MIRRORED and
unmapped. Read-only; coverage-gap prints the undeduped rows (its dollars
run about a third over these), his-day the two-bucket version.

## [R41]

Documents:

```
league-census) SQL="SELECT split_part(identifier, '-', 2) AS lg, count(DISTINCT split_part(identifie
```

THE LEAGUES THE MAPPER DOES NOT RECOGNISE (2026-09-07 14:xxZ, the
coverage program's league-code bucket, ~$170k/24 h): por, itsb,
col1, nor, gre1, bel1, ere, den, tur2, bra3, sui, cs2 all read
no_key_intersection. Two reads, each under the 300-line cap.
league-census: every venue league code listed for Sep 5-7 with
its game count, the league NAME as the venue's own per-team
question states it, and one verbatim question -- so an alias is
read from the venue's words, never from a table.

## [R42]

Documents:

```
league-rows) SQL="WITH his AS (SELECT t.condition_id, t.market_slug, t.market_title, t.outcome, t.si
```

league-rows: (1) his EVENTS in those leagues over 30 h -- markets,
dollars, families, his moneyline title (the C2/C5 title witness),
markets.event_title (the only matchup key a moneyline has), the
shadow's latest verdicts; (2) every venue row whose identifier
carries his slug minus its league code, under ANY venue code and
prefix (the identity that C2's alias arm and C3's _c3_code need);
(3) the venue's full-game atc rows for his team-code pair under
any code; (4) the venue's atc rows on his date whose question
names one of HIS clubs (his event title's sides, his moneyline
title's anchor; diacritics folded on both sides for the LOOKUP
only) -- the witness that certifies a league/club translation.

## [R43]

Documents:

```
league-rows-por) SQL="SELECT m.slug, m.event_title, left(m.title, 44) AS title FROM markets m WHERE 
```

league-rows-por (2026-09-07 14:0xZ, the second read of the league
bucket): the 13:51Z league-rows read cut its name join at LIMIT 90
inside 'itsb', so por/nor/sui/tur2/gre1/bel1/ere/bra3 were unread;
and max(event_title) over an event hid whether the MONEYLINE's own
markets.event_title carries the '- More Markets' suffix. (1) the
moneyline's own event_title per event; (2) every venue atc row
under ligpor / tsl / els / swsl on Sep 5-7 with its event title
(the Liga Portugal listing verbatim); (3) the name join for the
leagues the first read did not reach.

## [R44]

Documents:

```
prefix-rows) SQL="WITH his AS (SELECT t.condition_id, max(t.market_slug) AS his_slug, max(t.market_t
```

THE PREFIX-FILTER BUCKET (2026-09-07 14:xxZ, coverage program bucket
type_prefix_filter_emptied, ~$83k/24 h: bra/arg/pol/scop/itsb/den/sui
totals and the bra spreads). premap.resolve_explain reaches that step
only when the event IS on the board and no row carries the family
prefix (tsc-/asc-/astatc-). Four reads: (1) his 24 h totals/spreads in
those league codes with the shadow's latest verdict; (2) for each of
his events, the venue's prefix census on that game under ANY venue
league code (identifier carrying his <a>-<b>-<date>, aec rows included
-- the dated census above hides them); (3) the venue's all-dates
family census per league code, so a league the venue NEVER lists a
tsc-/asc- row for is read from the table; (4) dollars by league x family.

## [R45]

Documents:

```
paired-day) SQL="WITH bk AS (SELECT b.condition_id, min(b.id) AS book, string_agg(DISTINCT left(b.us
```

THE PAIRED DAY (owner 2026-09-07 21:4xZ, "results (p and l) lining up
proportionately"; 22:2xZ "proportional, directional ... within 1c"): on every
SETTLED market we both hold (a mirror book opened or closed inside 24 h, his
fills inside 48 h), his settled P&L and ROI beside ours, OUR STAKE AS A
FRACTION OF HIS DOLLARS beside the target fraction (10% or the $2,500 cap),
his average BUY price beside our average cost (cents_over: entries at his
cent read 0), the median seconds from his latest fill to our entry order,
whether the signs agree, and the book's last reason. Then the totals with
the fraction's median / min / max, cents_over's median and the latency
median. FILL lane 0b (2026-09-08), the target line: a market PAIRS ON ANY
SETTLED EPISODE (our stake and settled figure summed over the closed books
with settled_pnl; `episodes` / `settled_eps` beside them -- before, a market
with one live or unsettled episode was excluded whole, 58 mixed markets
holding +4,276.04 of our settled winners); the totals' dollar sums and ratios
run over the STAKED rows (our_staked > 0), the zero-stake rows counted as
zero_stake_mkts with their settled dollars beside (book 253: our_staked 0.00 /
settled +735.41 credited to the set); sign_agree still counts every row. The
pattern arm paired-day=<ISO> is this SQL with `b.opened_at >= that clock` on
the books window -- the set restricted to books opened after a deploy (the
bare label and the hourly stay the whole set). Read-only, LIMITed.

## [R46]

Documents:

```
paired-ratio) SQL="WITH bk AS (SELECT b.condition_id, min(b.id) AS book, string_agg(DISTINCT left(b.
```

THE PAIRED RATIO (PNL program lane M, 2026-09-08; owner ~11:50Z "I want to know
when he makes money we make money"): the paired-day set (the same bk / his
CTEs, verbatim) with his NET cost beside his gross. On the book's token axis
(long_asset / other_asset; a BUY_SHORT book mirrors) his running net is +BUY
long +SELL other -SELL long -BUY other; his_net_peak = max |running net| over
the window; his_vwap_adds = the size-weighted price of the ADDING fills on the
side the peak was reached on (the sign of the running net at the peak; a fill
adds what it moves |running| further out on that side, a reduce of the other
side adds nothing -- the fold of the review's HIGH-2, 2026-09-08: on a market
whose book flipped the first side's vwap priced the other side's peak) (mi.vwap_of,
E12b: a BUY of the long at p, a SELL of the other at 1 - p; the mirror image on
a short, printed as the short leg's price so peak x vwap is dollars on both);
his_cost_net = his_net_peak x his_vwap_adds; his_cost (gross, two-sided: 278
bought 21,896 USD gross for a 21,196 net) stays beside it, never instead.
share_frac = our peak shares / his_net_peak, our peak shares = the largest
book's peak_exposure_usd / its leg price (avg_cost on a long, 1 - avg_cost on a
BUY_SHORT: rules.book_buy peaks a short leg at leg x (1 - avg) -- HIGH-1; the
episodes of one market read as a peak like his_net_peak, never a sum; NULL
when a staked book's avg is unreadable, then our staked / the leg-priced
fill-weighted avg_px of our adding order rows); stake_frac_net = our_staked /
his_cost_net; roi_ratio = our_roi / his_roi (NULL when his_roi is 0) with
his_roi_net = his_pnl / his_cost_net and roi_ratio_net beside them (MEDIUM-3);
pnl_ratio = our_settled / his_pnl. A market with his_net_peak 0 or vwap
NULL prints NULL fractions, never 0, never a guess. The totals row names the
OPPOSITE books with their last reason and ends in the ONE line the hourly
reads: `PAIRED <n> mkts his_roi .. our_roi .. roi_ratio ..x same a/b opposite
<books> share_frac_med ..% stake_frac_net_med ..% cents_over ..c lat_med ..s`.
Frozen books pair only when closed with settled_pnl (as paired-day). FILL lane
0b (2026-09-08; owner decision D3): the line LEADS WITH THE NET RATIO --
`PAIRED <n> mkts (<staked> staked, <k> zero-stake dropped) his_roi_net .. our_roi
.. roi_ratio_net ..x gross his_roi .. roi_ratio ..x same a/b ...` (1.81x net
against 4.54x gross on 2026-09-08's 114) -- pairs on any settled episode, drops
the zero-stake rows from the sums and ratios, and takes the pattern arm
paired-ratio=<ISO> (books opened at or after that clock), exactly as paired-day
above. Read-only.

## [R47]

Documents:

```
latency-census) SQL="WITH fp AS (SELECT x.id, x.fast FROM xmltable('/table/row' PASSING (CASE WHEN N
```

THE LATENCY CENSUS (owner 2026-09-07 22:4xZ, "latency must be flawless and
exceptional"): every mirror order of the last 6 h with the LATEST fill of his
on the same market at or before the order -- his fill to our order (median
and p90) and our order to our fill (median), by hour and kind; then the last
40 orders one by one. Read-only, LIMITed. Nothing here is the wake's own
clock: the stamp is his fill's venue/block time.
FILL lane 9 (2026-09-09; migration 061): every line splits by `path` -- the
order row's `fast` (true: a FAST tick placed it, false: a FULL one,
'unrecorded' before 061), read through a CTE `fp` only when
information_schema says the column exists, so the hourly bundle survives a
deploy ahead of the boot that applies 061 -- and two per-FILL statements
beside the by-row one: (a) one row per (book, his_fill_id) -- 059's fill the
order answered -- with his_to_first_order median / p90 over the FIRST order's
placed_at minus the trades row's ts -- the row found by its PRIMARY KEY
(t.id = CASE WHEN his_fill_id is all digits THEN his_fill_id::bigint END: a
stamp-keyed or junk id reads NULL and drops to the by-row line, never a
sequential scan of trades per order row) -- so a replace chain counts ONCE
against the fill it first answered (4688 / 4693 / 4697 / 4704, h2225 883 /
878 / 874 / 868: one fill at 32 s where the by-row line read four at 32 / 53 /
192 / 287 s); (b) keep_to_replace_s per fill the record names behind a rest
(`rest_id`, guarded the same way): the rest's successor on the book by LEAD(id)
OVER (PARTITION BY book_id ORDER BY placed_at), its placed_at minus the fill's
stamp, by CAUSE alone (at most one row per cause word: the hourly runs under
the runner's 1,500-line cap, h2225 at 1,270 lines before this lane, so no hour
bucket here). Rows without his_fill_id (pre-059) stay in the by-row line.

## [R48]

Documents:

```
fills-answered) SQL="WITH r AS (SELECT t.id, t.condition_id, t.ts, t.detected_at, upper(t.side) AS s
```

THE FILLS ANSWERED (E9, 2026-09-07; owner 22:4xZ "latency must be flawless
and exceptional"): every fill of his of the last 6 h on a market that has
(or had) a mirror book, joined to the book's last_plan->'his_fills_seen'
entry for it -- ONE row per fill, written by the tick that first planned
with it: our order's row id, or the refusal name (mirror_live, the
paragraph over FAST_TICK_MAX). By name (answered = an order went out that
tick; unseen = no plan has held the fill yet), with the count, his dollars
and the seconds from his fill to the tick that answered or named it
(median, p90); then the top 30 unanswered by his dollars, one by one.
His rows are collapsed chain-first the way the mirror's own read
collapses them (D1: a per-match poll row whose (tx, asset, side) has
a chain / s1 row is that row's split, never a second fill) -- the plan
lists the net-leg row's id, so an uncollapsed census would file every
poll duplicate as 'unseen' with its dollars. The window runs over his
6 h of rows on mirrored markets only. Read-only, LIMITed.

## [R49]

Documents:

```
fills-missed) SQL="WITH fa AS (SELECT x.fill_id, x.name, x.order_id, x.book_id FROM xmltable('/table
```

THE MIRROR'S OWN FILLED-VS-MISSED (lane M, 2026-09-08). The FVM and PRICEFID
lines of the probe exclude lane='mirror' (api/app.py:2671, price_fidelity.py
:181), so none of the mirror's entries were scored. 1: his ADDING fills on our
markets (24 h, chain/s1 collapsed as fills-answered; adding on the token axis
of the book whose window holds the fill -- opened_at less the 120 s a fill is
answered within, to closed_at; the later book when two hold it, else the
latest (the fold of MEDIUM-2, 2026-09-08: the latest book's axis dropped the
fill the first book of a flipped market filled) -- a short mirrored) joined to
our first entry order within 120 s -- by the plan's his_fills_seen[].order when
the entry carries one, else the first increase/take placed in [ts, ts + 120 s)
(FILL lane 4, 2026-09-08: the per-fill record mirror_fill_answers, migration 060,
is read FIRST -- its order_id, then the plan's, then the window; its name, then the
plan's -- through a CTE `fa` that reads the table only when to_regclass finds it, so
the hourly bundle survives the minutes between a deploy and the boot that applies
060; a fill the list rolled off keeps its `refused:<name>` from the table)
-- classed filled | partial |
missed_expired_ioc (IOC, 0 filled) | missed_replace (cancelled, reason replace)
| missed_open | refused:<his_fills_seen name> | unseen (no plan held the fill)
| order_row_unread (the plan names an order id no row carries); every fill
graded at HIS price on the book's leg against markets.resolved_prices (payoff
= the long token's resolved price, 1 - it on a short): n, his_usd, n_resolved,
roi = sum((payoff - px) x size) / sum(usd) over the resolved, ci95 = 1.96 x
stddev of the per-fill roi / sqrt(n_resolved) (a normal approximation, enough
for a preset), lag_med_s, with an ALL row; 2 (FILL lane 0b, 2026-09-08): the
classes per STATE of the market at the fill -- in_book (a book's window,
opened_at - 120 s to closed_at, holds it), before_open (older than the earliest
book's window), after_close (at or after the newest close with no book live),
no_book (between two books) -- so `unseen` splits by a GROUP BY instead of an
argument (R4's (i) / (ii) / (iii) on $517,203.44); 3 (lane 0b): filled /
partial / missed_expired_ioc per the answering row's 059 `decision` (rest /
take / take_in_band once the band lands; 'unrecorded' before 059) with the
median band_c of the rows -- brief question 3's read per fill; FILL lane 8
(2026-09-09) adds missed_replace to that WHERE word -- one word, here AND in
the hourly's copy -- so the largest class of the day (745 fills / $381,750.75
at 22:25Z, h2225 996) prints its cause rows, missed_replace x {replace_cent,
replace_qty, replace_side, ttl, replace_unread}, instead of the 77-row proxy
the filled / partial rows carried; the word printed is whatever the row
carries (COALESCE(decision, 'unrecorded')), so a word a later lane writes
prints the day it is written; 4: the same per
book (LIMIT 60);
5: band_c over every entry order row of 24 h = (ask_at_place - floor-cent of
his_level) x 100 on a BUY_LONG, (ceil-cent of his_level - bid_at_place) x 100
on a SELL_LONG (the columns mirror_live writes at placement; his cent rounded
in numeric before the floor / ceil -- HIGH-3: float8 floor(0.29 x 100) is 28,
so a rest AT his cent read a cent wide), the share at or
through, inside +1c and inside +2c -- the band's reachable share BEFORE lane 8
-- and the fill rate inside vs outside 1c; 6: the entry fill rate by hour and
kind (latency-census's rows, entries only, plus replaced and IOC misses).
Nothing here is a guess: a fill no plan recorded reads `unseen`. Read-only.
FILL lane 9 (2026-09-09; migration 061): a SEVENTH statement -- the same chain
plus a CTE `fc` reading the record's `cause` and `fast` columns -- groups
refused:open_order_pending by COALESCE(cause, 'unrecorded') x path (n, his_usd,
n_resolved, roi, ci95, lag): WHY the rest stood (same / min_life / take_capped /
replace_capped / frozen / flow_grew) and which tick named it. `fc` reads the
two columns only when information_schema says they exist (the same
query_to_xml idiom as `fa`), so the hourly bundle survives the minutes
between a deploy and the boot that applies 061 -- 'unrecorded' until then.

## [R50]

Documents:

```
on-target-why) SQL="WITH r AS (SELECT t.id, t.condition_id, t.ts, upper(t.side) AS side, t.asset, t.
```

WHY `on_target` HAD NO ORDER (lane M, 2026-09-08): 50,696 USD of his 126,704
on our markets in 24 h read on_target in fills-answered and no order went out.
Every fill of his of 24 h on a mirrored market whose his_fills_seen entry is
named on_target with no order, attributed by cause off the ROW that holds the
entry: `block` (the fill is older than the book's first sight, opened_at - 60 s
= FIRST_SIGHT_S, and the book carries a non-zero flow_base: E12 never buys
it), `game_cap` (the plan names a game_cap, or its game_room reads <= 0),
`flow_hold` (the plan holds a flow_hold), `stale_snapshot` (|target_raw| <
ratio x |the flow the target is sized on| by more than half a share -- the
plan's own flow_net, else his_net - flow_base (mirror_live _tick_book: a flow
book's target is ratio x flow, never ratio x his_net; the fold of HIGH-4,
2026-09-08, before which every on_target fill on a flow-only book read stale
by the block's dollars): the target was sized on a reading behind the fills),
`block_flow` (a flow-only book, flow_base <> 0, whose target is below ratio x
|his_net| by the block alone: on target for the flow, the block refused), else
`unattributed`. The plan is the row's LATEST,
not the one at the fill -- an older cause may have cleared since; a cause
that cannot be read is `unattributed`, never guessed. By cause with an ALL
row (its his_usd is fills-answered's on_target on the same window), then the
30 largest fills with the row's numbers. Read-only, LIMITed.

## [R51]

Documents:

```
traded-day) SQL="SELECT 'today' AS what, count(*) AS filled_orders, count(DISTINCT o.book_id) AS boo
```

THE DAY'S TURNOVER (2026-09-08, owner question "How many dollars have we traded today in our
mirror trades"): every FILLED order row of the UTC day (done_at, else placed_at), the
dollars at the venue's returned average (the wire when none returned), split by the long
token bought (BUY_LONG: a long's open / add / a short's cover) and sold (SELL_LONG: a
long's reduce / flatten / a short's open), then by kind x side and by hour. Read-only.

## [R52]

Documents:

```
tennis-witness=*) if [[ ! "$ARG" =~ ^tennis-witness=([0-9]{4}-[0-9]{2}-[0-9]{2})(:([a-z]+(,[a-z]+)*)
```

THE TENNIS WITNESS READ (2026-09-08, PNL program lane 7 item 1;
PNL_lane3_coverage §3): `tennis-witness=<YYYY-MM-DD>[:<surname>,
<surname>...]` -- the venue's own aec- rows on that date whose
event_title names any of the surnames (lower-case letters only,
the regex refuses anything else), plus the date's aec row count
per tour so 'no rows' can be told from 'no table rows that day'.
Rows -> the title witness (item 2) is built on them; no rows ->
'venue does not list', the question closes. The date is his
slug's own (atp-gea-zandsch-2026-09-07); the surnames his event
title's. Read-only, LIMITed; no name reaches SQL unchecked.

## [R53]

Documents:

```
esports-chi-rows) SQL="WITH sh AS (SELECT DISTINCT ON (condition_id) condition_id, at, left(detail->
```

THE DRIFT ON EVERY LIVE BOOK (2026-09-06, owner: "more volume"):
the shadow's newest reading per live book -- his fills-derived
long/other against the venue snapshot's long/other, the drift
the rule computed, and the snapshot's state and age -- so a
refused increase is read from the numbers.
THE ESPORTS / CHILE-CHINA / COLOMBIA BUCKET (2026-09-07 14:xxZ, the coverage
program's esports_chi bucket, ~$120k/24 h: chi1 yn:no-row + unparsed, col1
yn:no-row + no_key_intersection, cs2 no_key_intersection + unparsed, bra/arg
yn:no-row). His rows per MARKET with his title, the stored event title (the
key the resolver builds from -- max() over an event hides the ' - Exact Score'
suffix that breaks the key), outcome and the shadow's newest verdict; the
venue's per-team rows under csl (CSL) AND pdc (Primera Chile) AND lco (Liga
Colombia) for Sep 5-7 with their questions verbatim (which league his chi1
is, and which club each code names, is READ off the question, never guessed);
the venue rows on his date whose question names the club of his own title
(the C2 witness) for every yn:no-row event of the bucket; the venue's whole
CS2 moneyline board for Sep 6-7 (aec rows, sides, questions, event titles)
and its map / handicap / total rows on his CS2 events, so a map-winner or
map-handicap lane can be proposed on the venue's own wording.

## [R54]

Documents:

```
drift-16) SQL="SELECT t.source, t.side, t.outcome, count(*) AS n, round(sum(t.size)::numeric, 1) AS 
```

THE DRIFT ON BOOK 16 (2026-09-06, owner: "more volume"): his fills
on the Kostyuk condition by source and side, the rows that share a
tx hash across sources (a fill counted twice), and the shadow's
history of fills-derived vs snapshot long/other on that condition.

## [R55]

Documents:

```
fills-vs-venue) SQL="WITH bk AS (SELECT DISTINCT ON (b.condition_id) b.condition_id, b.id AS book, b
```

HIS FILLS AGAINST THE VENUE, PER MARKET (E19, PNL lane 8, 2026-09-08;
Martinez 529/534: the D1 collapse dropped four real poll rows, 18,374.5
sh, under an s1 row's tx, his net read 11,974.6 against the venue's
25,104 and every reopen was refused drift). Per market he traded in
the last 24 h that has a mirror book: the raw rows and shares by
source (chain / s1 / poll+backfill), the collapsed long / other / net
under the OLD key (every per-match row under a net-leg row dropped)
and the NEW key (dropped only when the per-match rows sum to ONE source's
net leg or repeat one of its rows, one each; the review's fold) -- SQL-side so
the difference is read -- and the venue's per-market figures from the
newest book's last plan (mkt_long, mkt_other, snap_net, flow_net,
drift, drift_src, its clock); the fills counted are those at or before
that clock (later_sh beside them), ordered by the NEW key's gap to the
venue's net. Then the 24 h totals. Read-only; unrun on the branch.

## [R56]

Documents:

```
books-new) SQL="SELECT b.id, b.us_market_slug AS slug, b.intent, b.map_source AS src, b.ratio, b.tar
```

THE NEWEST BOOKS, SIDE-CHECKED (2026-09-06 19:42Z: four books opened
on the first tick after C1 booted). For every book with id >= the
arg-less floor 23: the book's slug, intent, his token, map source
and target; his fills on the condition (outcome, side, shares, avg
price, last fill); and the venue's own rows for that identifier
from us_premap (side_norm, intent, line, question) -- so the side
the book took is read against the venue's wording, never inferred.

## [R57]

Documents:

```
flow-books) SQL="SELECT b.id, left(b.us_market_slug, 44) AS slug, b.intent, b.ratio, b.target, b.led
```

THE FLOW BOOKS (E12, 2026-09-08): every book opened in the last 6 h with its
block (flow_base), the fills' net it was read at (flow_last_net), the open's
catch-up verdict and the ratchet's reading verdict off the plan row; then a
census by verdict. A NULL flow_base on a post-057 book is the old rule (the
column absent when it opened); the second statement counts them apart.
catchup_side (lane M) reads last_plan->'catchup': since the lane M fold
(2026-09-08, review HIGH-5) _write_plan carries the open's verdict from the
prior plan onto every plan it writes, so a book opened from that commit on
reads its side on every READ row; the quiet skip's plan (_SKIP_CARRIED, E6's
pinned contract, not widened by that fold) does not carry it, so a book
quiet-skipped before its next read loses the verdict and reads `unread` from
there; a book opened before the commit holds it on its FIRST plan only --
the per-side dollars sum the books that carry it, never a guess.

## [R58]

Documents:

```
nf-his) SQL="WITH his AS (SELECT t.condition_id, t.market_slug AS his_slug, max(t.market_title) AS h
```

THE FAMILIES THE MAPPER HAS NO LANE FOR (2026-09-07 13:5xZ, coverage-gap census:
exact_score ~$112k, prop ~$88k, unknown ~$41k in RN1's last 24 h). nf-his: his
rows (30 h) on those families -- slug, HIS title, the stored event title (the
key the resolver builds from), outcome, dollars -- and the shadow's newest
verdict per market; then the digit-code slugs the parser refuses ('cu1',
'riv1', 'cor1', 'her1' read unknown_market_type:unparsed). nf-venue: the
venue's rows for the same games -- every distinct SUFFIX shape the venue lists
on one Premier League event (does it list corners, player goals, team totals,
halves?), the first-half per-team rows with their questions, the exact-score
rows for the La Liga game he traded (val-bar vs the venue's val-fcb), and the
CS2 board (the astatc map rows' sides and questions, the codes it lists) --
so every lane below is proposed on the venue's own words, never a guess.

## [R59]

Documents:

```
exits-paired) SQL="WITH bk AS (SELECT b.condition_id, min(b.id) AS book, string_agg(DISTINCT left(b.
```

THE EXITS, PAIRED (E8 part 2 step 1, 2026-09-08; owner "we go down when he
loses ... sells (or exits) within 1c of his price"): on every market whose
mirror book CLOSED in the last 24 h, did we leave when he left, at his
price? HIS SIDE from his 48 h rows collapsed chain-first (the paired-day
read, verbatim), each row a signed LEG ON THE BOOK'S TOKEN AXIS: size x
(BUY +1 / SELL -1) x (long_asset +1 / other_asset -1 -- the mirror's own
his_net, long minus other: on this venue he leaves a Yes as often by
BUYING the No as by selling the Yes, and a per-condition BUY-minus-SELL
read 175 of 200 books "held to settlement" on 2026-09-09) x (-1 on a
BUY_SHORT book, whose leg is his net-negative side). Over their running
sum: the peak (its max; must be > 0, else his net never went the book's
way), his first reducing fill after it (his_exit_from: a leg < 0), the
fill at which the sum reads <= 10 % of the peak (his_exit_ts; NULL = he
held to settlement), the size-weighted price and the dollars of the
reducing fills from the peak to that fill -- every price on the long
axis (a No at p is a Yes at 1 - p; the dollars are that price on a long,
its complement on a short), his settled P&L as paired-day. OUR SIDE from
the closed books' order rows: the peak of the running filled sum (a row
that grows the leg adds, one that shrinks it subtracts -- keyed on the
book's intent and the row's plan side, so a short's cover is the exit it
is); then over the reducing rows placed from his_exit_from on (10 s of
clock skew allowed): the first one's placed_at, the fill-weighted avg_px
(contract space, the same long axis), the shares filled over our peak,
lag_s = ours - his_exit_from, cents_vs_his = (his - ours) x 100 on a long,
reversed on a short (positive = our price was worse), held_to_settlement.
THE VERDICT, in order: no_position (we never held a share) and
his_net_unseen (no row of his inside 48 h, or his net never on the book's
axis) are unmeasured; then no_exit_by_him, exited_with_him (>= 80 % of
our peak filled), partial_exit, exit_placed_unfilled, no_exit_order;
stuck_after_his_exit_usd = our dollars settled NEGATIVE on the last
three, worst first. FILL lane 0b (2026-09-08; book 266: he cut 30 % and we
held 1,955 sh to -977.65 under `no_exit_by_him`, which the 10 % rule hides):
`trough` = his lowest running net after the peak, his_reduced_pct = 1 -
trough / peak, and the verdict he_reduced_we_held -- no exit by the 10 % rule,
his_reduced_pct >= 0.25 (chosen) and under half our peak filled after his
first reducing fill (0.5 chosen) -- read BEFORE no_exit_by_him (a CASE reads in
order; the arm is that verdict's own sub-case), counted in the stuck dollars
and in the totals (reduced_we_held, reduced_we_held_stuck_usd). FILL lane 8
(2026-09-09; C5's read of exitspaired_2304 rows 532-575: six of the twenty
stuck he_reduced_we_held books had our filled fraction within five points of
his cut -- 313 0.24 / 0.26, 332 0.37 / 0.37, 364 0.31 / 0.27, 525 0.34 /
0.34, 320 0.31 / 0.31, 699 0.38 / 0.37, $1,088.27 of the $2,900.48 -- the
E12 pro-rata ratchet following him, not a hold): the arm we_matched_his_cut
-- no exit by the 10 % rule, his_reduced_pct >= 0.25 and |our filled
fraction - his_reduced_pct| <= 0.05 (0.05 chosen) -- read BEFORE
he_reduced_we_held, NOT in stuck_after_his_exit_usd, counted in the totals
as matched_cut_n; 683 (0.35 against his 0.43, eight points) stays
he_reduced_we_held. Then ONE totals row. Read-only, LIMITed.

## [R60]

Documents:

```
take-band) SQL="WITH e AS (SELECT o.id, CASE WHEN o.decision IN ('replace_cent', 'replace_qty', 'rep
```

REST VS TAKE, BY DECISION AND BAND (FILL lane 0b item 1, 2026-09-08; brief
question 3: is the adverse selection at the rest?). Every entry order row of
the last 24 h on a LONG book (kind increase / take, side BUY_LONG) graded at
OUR price against the long token's resolved price: per (decision, band bucket)
with decision the 059 word that placed the row (rest / take; take_in_band once
the entry band lands; 'unrecorded' on a row older than 059) and band_c =
(ask_at_place - floor-cent of his_level) x 100 at placement (his cent rounded
in numeric before the floor, HIGH-3's lesson) bucketed <=0 / 1 / 2 / 3+ /
unread: n, filled_n, fill_pct, our_usd = sum(filled x wire), n_resolved (filled
rows on resolved markets), roi = sum(filled x (payoff - wire)) / sum(filled x
wire) over them, ci95 = 1.96 x stddev of the per-row roi / sqrt(n_resolved),
paid_med_c = the median of (wire - floor-cent of his_level) x 100 over the
filled rows (the cents paid over his cent: 0 today by construction, 1 and never
2 once the band lands), send_ask_over_wire_med_c = the median of (ask_at_send
- wire) x 100 over IOC rows (the re-read's ask against the wire). ROLLUP rows
read ALL. The per-fill classes split by decision are fills-missed's third
statement, beside this in the hourly. Read-only.
FILL lane 8 (2026-09-09; h2225 1212: the rest row read 278 / 111 filled /
140 replaced, one fill_pct over both). (a) THE SPLIT: a replaced rest prints
under its own word `rest_replaced` (the cause words fold to it, not to
'rest'), so n / filled_n / fill_pct / roi read per side; `replaced_n` STAYS
on the `rest` row -- a window sum over the base word and bucket, so the rest
row and its rest_replaced sibling both read the bucket's replaced count and
ALL reads the total, as before. (b) A SECOND STATEMENT, the replaced entry
rows of 24 h (both intents: BUY_LONG on a long book, SELL_LONG on a short --
the exits-paired reducing clause's mirror image; kind increase / take) each
paired with the NEXT entry row of the SAME book (LEAD over PARTITION BY
book_id ORDER BY placed_at, id): touch_moved = the same his cent (floor-cent
of his_level, numeric-rounded) and a different wire (the bid stepped under a
standing his cent -- the rest chased it); same_quote = the same cent, wire
and qty (the clock's shape: 4731 / 4732 at 22:44Z, tick_2245 393-394);
qty_regrow = the same cent and wire, LEAD(qty) > qty with the growth under
10 % of the leaves (qty - filled); other = his cent moved or a resize outside
that (816's 1,403 -> 752 after a 631.85 fill, h2225 872-882); unread = no
next row, or his_level / wire NULL on either side -- never a guess. Beside
them future_clock = decision 'replace_unread' AND done_at - placed_at < 45 s
AND filled = 0 (a proxy for the clock until the row can say more; it
overlaps the pair words), and the replaced rows' done_at - placed_at median.
By side (long / short, from b.intent) and hour, ROLLUP (side, hour): the
side's ALL row is the long-book touch_moved share lane 12 is gated on. Rows
bounded at 2 x 24 + 3 in the hourly. Read-only.
E27 (FILL lane 27, 2026-09-09): a THIRD STATEMENT, the SHORT add's own (decision, bucket)
table -- the first grades LONG adds alone (BUY_LONG on a BUY_LONG book) and the short's band
take (a SELL_LONG IOC on a BUY_SHORT book, decision take_in_band, wire a cent or two UNDER
ceil(his_level)) had no read: band_c = ceil-cent(his_level) - bid_at_place (the bid under his
SELL cent at placement, the mirror image of the long's ask over his cent), given_c = ceil-cent
(his_level) - wire (the cents given up under him; 0 on a take at his cent), our_usd the
collateral filled x (1 - wire), roi = (wire - payoff) / (1 - wire) over the filled rows on
resolved markets (a short's stake is its collateral), the same buckets and ROLLUP, side 'short'.
Read-only; both places (this case and the hourly's copy).

## [R61]

Documents:

```
maker-rests) SQL="WITH e AS (SELECT o.id, o.book_id, round(o.wire::numeric, 4) AS wire, o.qty, o.fil
```

THE MAKER'S RESTS (E31, 2026-09-10, FILL lane 31; owner order ~03:3xZ
"become a maker not taker ... mirror him to a tee"). Every order the
mirror sends is now a post-only rest at his price or better that never
crosses the touch, and nothing on the tip read a REST'S OWN LIFE -- from
placement to fill, replace or rejection -- with the touch beside it.
1: every post-only GTC/GTD row of the last 24 h on his books by `side`
(long / short from b.intent), `leg` (add / reduce from o.kind) and
`clause` -- 'his_cent' when the wire IS his cent (floor-cent(his_level)
on a BUY_LONG row, ceil-cent on a SELL_LONG one), 'touch' when the
clamp moved it, 'unpriced' when the row carries no level of his -- with
rests, standing (state open at read), filled_n, filled_whole, fill_pct,
filled_usd (the collateral: wire a share on a long, 1 - wire on a short
add), maker_fill_pct (filled rows the venue did NOT fill at create over
filled rows), crossed_at_create (post-only rows with
taker_at_placement -- the venue ignoring the flag, which writes the
durable block), from_his_c_med (median cents from his own cent: 0 at
his cent, negative inside), from_touch_c_med (median cents inside the
far touch: how far from crossing the rest sat), ttf_med_s (placed to
done on filled rows), unfilled_usd, and n_resolved / roi / ci95 on the
filled ADD rows graded as take-band grades them (the long token's
resolved price; a short's stake its collateral). ROLLUP reads ALL.
2: WHAT MOVED THEM, per (side, hour): replaced with take-band statement
2's own pair rule (touch_moved / same_quote / qty_regrow / other /
unread), rejected (state rejected, reason post_only_rejected:%),
rejected_cross (the reason's ':cross' word), repriced (a rejected row
followed within 90 s by the same book's row at a DIFFERENT wire -- this
lane's re-price off a proven cross) and held_backoff (books whose last
plan holds post_only_backoff). Read-only, its own TO; both places (this
case and the hourly's copy).

## [R62]

Documents:

```
exits-band) SQL="WITH x AS (SELECT o.id, o.book_id, CASE WHEN o.decision IN ('replace_cent', 'replac
```

THE EXIT BAND (FILL lane 0b item 2, 2026-09-08; R5's unread A8: was the bid
within 1c or 2c of his price at the held exit ticks?). Every REDUCING order
row of the last 24 h (the exits-paired clause: BUY_LONG on a BUY_SHORT book,
SELL_LONG on a long) whose 059 decision is exit_rest / take / cover (and
exit_take_in_band / cover_in_band once lane 3 lands), with band_c = (his_level
- bid_at_place) x 100 on a SELL_LONG and (ask_at_place - his_level) x 100 on a
cover -- how many cents past his price the touch sat when the row went out --
bucketed <=0 / 1 / 2 / 3-5 / >5 / unread. 1: per (decision, bucket) n,
filled_any, filled_whole, shares_unfilled = sum(qty - filled), usd_unfilled =
that at his_level, done_med_s = the median seconds from placed_at to done_at;
2: per (decision, bucket) the BOOKS whose rows in it filled anything against
the books whose rows never did, with mirror_books.settled_pnl summed on each
side (settled_when_filled / settled_when_unfilled: the fill-when-right read on
the exit side). ROLLUP rows read ALL. The row records the re-read's ask only
(059 ask_at_send); a SELL IOC's send-time bid lives on the plan. Read-only.
FILL lane 8 (2026-09-09). (a) THE SPLIT, as take-band's: a replaced exit
rest prints under `exit_rest_replaced` (a short book's under
`cover_replaced`), never a cause word of its own; `replaced_n` stays on the
exit_rest / cover row through the same window sum (the rest row and its
replaced sibling both read the bucket's replaced count). Both statements.
(b) THE PER-IOC SAME-TICK REST LINE lane 0b promised (docs section 46's
live proof; not in exitsband_2304): a THIRD statement over every exit IOC
of 24 h on a LONG book (SELL_LONG, tif IOC, whatever its word) by how it
filled -- whole / partial / zero -- and, for each, the exit_rest row on the
SAME book placed within 15 s after it (its decision 'exit_rest', or a cause
word once a replace overwrote it -- the HIGH-1 lesson; a row with no word is
not counted): iocs, ioc_shares, ioc_filled_sh, rested_15s, rest_filled_any,
rest_shares, rest_filled_sh, rest_lag_med_s, with ALL. Before lane 1 that
count was 0 by construction; it is the one read that prices lane 1's
$246.20 bound. Read-only.

## [R63]

Documents:

```
closed-while-he-traded) SQL="WITH cb AS (SELECT b.id AS book, b.condition_id, left(b.us_market_slug,
```

CLOSED WHILE HE TRADED (FILL lane 0b item 5, 2026-09-08; R4's $73,475.58 on
books 309 / 338 / 630, whose closes were sign flips -- post_booksnew_1707 385,
verify_1750 359). Per book closed in the last 24 h by the MIRROR'S OWN close
(the plan's close reads cashed_out / cancelled, or last_reason reads
closed_cashed_out / closed_cancelled -- the plan write of the same tick can
overwrite last_reason with the plan's reason, so both are read; never the
settle's `closed: standing row *` nor venue_market_ended): opened / closed,
last_plan close / sign_flip / turn / reopen_refused (the last two written by
lane 5, NULL before it), his net at the close (last_plan net), his fills AFTER
closed_at on the condition (48 h rows collapsed chain-first: n, usd, shares,
his P&L on them at his price where the market resolved, his last fill), his
net now on the book's axis (long minus other over the 48 h rows), the next
book on the condition (id, opened, lag_s, state, settled), and the reopen path
after the close from mirror_candidate_refusals (refusals, the first name,
name@HH:MM ... left 200). Then the totals (books, with_later_fills, later_usd,
later_sh, later_pnl_at_his_px, reopened_n, refused_only_n, no_verdict_n,
sign_flips) and the books by FIRST refusal name. Read-only, LIMITed.

## [R64]

Documents:

```
fill-answers) SQL="WITH w AS (SELECT condition_id, name, count(*) AS n, count(order_id) AS with_orde
```

THE PER-FILL RECORD'S HEALTH LINE (FILL lane 4, 2026-09-08; migration 060,
mirror_fill_answers: one durable row per fill of his the mirror held, the name
the tick gave it and the order it placed, written once, never lost to the plan
list's 20-entry bound or the book's close). Per market with a book: the newest
book (id, slug, state), his fills held in 24 h (chain/s1 collapsed as fills-missed
reads them), the rows written in 24 h, how many carry an order, the names (name=n,
left 120), the oldest fill written vs the oldest the plan's list still keeps and
the plan's fills_hwm; then the table's totals. Dispatched with service
sportsassets-db. Against a database without the table it fails by name (this
preset IS the table's measurement; fills-missed's own read is guarded so the
hourly survives). Read-only, LIMITed.
FILL lane 9 (2026-09-09; migration 061): `causes` (cause=n per market, left 60)
beside `names` -- the record's `cause` column read through a CTE `c` only
when information_schema says the column exists (query_to_xml, the `fa`
idiom), so this preset still runs against a database with 060 and not 061;
the table itself is still read plainly (absent: fails by name, as before).

## [R65]

Documents:

```
sleeve-48h) SQL="WITH k AS (SELECT b.*, COALESCE(NULLIF(m.sport, 'unclassified'), '(unmapped)') AS s
```

THE HOURLY, IN ONE RUN (2026-09-08; eight since lane M, nine since FILL lane 0b's
take-band): the read-only presets the
hourly status reads -- mirror-tick, mirror-pnl, paired-day, paired-ratio (lane M),
latency-census, fills-answered, fills-missed, take-band (FILL lane 0b, beside
fills-missed so the rest-vs-take read rides the first hourly after the deploy),
on-target-why (lane M) -- run one after the other in ONE job, each under a
'== name' section marker, so the hour's numbers come from one log instead
of nine dispatches. The text below is the nine presets' own SQL joined;
test_render_ops_hourly pins that it equals them, so a preset edit that
forgets this line fails there. Still nine since FILL lane 8 (2026-09-09):
fills-missed's (class, decision) WHERE word carries missed_replace here as
in the standalone case, and take-band carries its two statements (the
replaced-rows pairs table is bounded at 2 x 24 + 3 rows). TEN since FILL
lane 14 (2026-09-09): tick-ring right after mirror-tick -- the same
statement as the standalone case, bounded at 48 hour-bucket rows (the
ring holds about two hours), so the 13:35Z hourly's 1,075-line log gains
at most a few dozen under the runner's HEAD 1500. Read-only, LIMITed.

## [R66]

Documents:

```
data-audit) SQL="SELECT 'trades (whale fills, GLOBAL venue)' AS src, count(*) AS n_rows, min(ts)::da
```

THE DATA-AVAILABILITY AUDIT (2026-09-10; read-only; five SELECTs, writes nothing).
Answers the coverage half of a research brief: for every table that could carry a price
observation, how many rows, over what exact dates, across how many markets -- and, for the
US quote series specifically, how many observations carry BOTH a bid and an ask, since a
one-sided quote cannot price a spread. Statement 4's monthly histogram is the missingness
check: `days_with_data` against the days in the month names every gap without interpolating
across one. Statement 2 is the one that decides whether an execution study is possible at
all -- `obs_both_sides` on mirror_shadow is the ONLY executable US bid/ask we hold, and
statement 3 says how many markets carry enough of it to reconstruct a path.

## [R67]

Documents:

```
ID=$(svc_id "$SERVICE"); [ -z "$ID" ] && { echo "no service named $SERVICE"; exit 1; }
```

VALUES of the operator FLAGS only -- never a key, token, URL or
credential. The switches that decide whether money moves
(LIVE_COPY_HALT: 'on'/'1'/'true'/'yes'/'halt'/'stop' halt) must
be readable without a screenshot; everything else stays masked.

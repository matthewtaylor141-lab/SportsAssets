# Copy-chain program — RN1 and HomeRunHazard

Working document for the copy lane's accuracy programme. It lives in the repository
deliberately: the session container has been restored from stale snapshots three times in
one day, and only pushed commits survive. Anything worth keeping goes here.

Provenance note. The original programme (seven analysis lenses with refuters, 2026-09-03)
and its probe logs were lost to the second rollback. The FIX sections below are code-level
and re-verifiable against this repository. Production figures quoted in earlier drafts are
NOT reproduced here: re-measure before quoting any of them to the owner.

Line numbers cited in prose drift between commits. Re-locate by grep, never trust them.

---

## The metric

Dollars of the whale's flow per day that a fix brings inside our mirrored share — not our
own P&L. A fix that makes us copy more of what he actually does ranks above a fix that
makes our existing copies cheaper.

## Standing constraints

- Fail closed. An unreadable input refuses or trips; it never proceeds on a default.
- No behaviour change on the money path beyond what a brief names.
- Every new census is bounded; every new database read has a LIMIT and a timeout.
- Nothing on the money path ships without adversarial review. Every round so far has found
  a real defect, including in fixes that had already passed one round.

---

## FIX 1 — Chain-lane coverage (rank 1)

The listener is how we learn a whale traded within a second. When it misses, the poller
finds the fill minutes later and the copy gates refuse it as stale — so a miss here is a
trade we simply do not copy.

### What the investigation established (2026-09-03, read-only, against this tree)

1. **The seen-set is keyed on the transaction alone** while the matched-wallet set is
   computed per log. The venue batches several fills — including several of one wallet's —
   into one settlement transaction; the code's own comment says so. The first frame is
   handled, every later fill in that transaction is dropped, and the drop is UNCOUNTED.
   This is structurally biased against whales who trade in batches, which is exactly
   HomeRunHazard's symptom (chain 0 of his last 10 fills, all poller).
2. **The v2 and v3 topic constants are byte-identical.** `_handle_log` catches that topic
   first and returns, so the v2 branch is unreachable and `decode_order_filled_v2` is dead
   code exercised only by tests. Every fill therefore takes the receipt-reconstruction path
   and pays an `eth_getTransactionReceipt` instead of the single-log decode that needs no
   receipt, no USDC leg and no share-match selector.
3. A wallet absent from topics 1..3 of the fill event (proxy vs EOA) is also uncounted.
4. The bundle-decoder theory is NOT supported: at least one HomeRunHazard poll-only
   transaction carried a single event that would have passed the selector, and a decode
   that would have succeeded cannot be a decode refusal. Do not write a summed fill on that
   evidence — instrument first, decide after.

### The build

- Counters on the healthy beat, which has none today: abandoned claims, failed receipts,
  second-wallet-in-transaction, skipped backfills, throttle flaps, endpoints configured and
  in use, failure streak, the reorg-removed flag, and the cause of writer conflicts.
- `try/finally` after the claim so a failure finishes the claim "refused" and pops the seen
  entry; check the HTTP status before parsing a receipt body (a 429 body parses as an empty
  receipt and reads as "no shares"); key the seen set on (transaction, wallet).
- Quiet timeout 60s → 10s; support a second websocket endpoint and prove it on the beat.
- A per-whale chain gauge keyed by address, with matched bumped before every refusal, so
  "no row for him" unambiguously means his log never arrived.

### Two majors found in review — fix before this ships

- **The fail-closed block-timestamp raise has no boundary.** Refusing a fill whose block
  time cannot be read is correct; letting the exception unwind into the connection loop is
  not. It tears down a healthy subscription, publishes a false "down" beat, forces a
  reconnect, and on the backfill path jumps the cursor to the tip — forfeiting the whole
  gap to the poller. The degraded-node answers this fix exists for (`200` with a null
  result, an RPC error body, a missing timestamp) are precisely the newly fatal ones.
  Reproduced against the real loop. Fix: catch it at the per-fill loop and `continue` —
  every fail-closed property is preserved by the existing `finally`, and the other wallets
  of a batched transaction still decode.
- **The roster freezes.** Skipping the roster read on a quiet-driven reconnect assumed the
  inner loop refreshes every 60s, but that refresh sits after the receive that timed out,
  and the clock resets on every reconnect. On a lane with any gap over 10s per minute the
  roster is read once, at boot, and never again — so a whale added by the operator is never
  picked up, his fills book as "no owner", and the per-whale gauge shows no row for him:
  the exact wrong diagnosis the gauge was added to prevent. Both chain paths go blind at
  once. Reproduced: 9 reconnect cycles, 1 roster read. Fix: refresh on a quiet cycle when
  the roster is older than 60s, or move the clock onto the listener so it survives a
  reconnect. Note that a test currently pins the broken behaviour and must be changed.

Gate: RN1 chain share ≥ 0.95 on days without a provider outage; HomeRunHazard's provenance
line ≥ 8 of 10 by chain; the healthy beat prints at least two endpoints and every counter.

---

## FIX 2 — An owner clip lever, and a breaker that moves with it

Today no admin path can set any clip other than $50 for this roster. The measuring clip is
a code constant, the hourly pass rewrites the stored clips from it, and the one documented
"lever" — the reset flag on the roster endpoint — DELETES the stored roster as well as the
clips, after which the executor falls back to a five-whale default at hardcoded clips of
$250, $250 and $100. That is the opposite of the two-whale roster the owner ordered.
**It must not be used.**

### The storage contract (both halves implement exactly this)

1. A stored key `live_owner_clips` — `{whale: usd}` — in the same store and helpers the
   existing clip overrides use. Written ONLY by the roster endpoint from an optional
   `clips` field. A body without it leaves the key untouched. The reset flag neither writes
   nor deletes it, and its note is amended to say the reset restores the five-whale
   default.
2. The rules take the owner clips: a measuring whale trades at `min(owner_clip, max_clip)`,
   otherwise the measuring constant. Demotion still writes zero and still wins; promotion
   still writes the promoted clip.
3. A stored `live_loss_breaker_usd`, written beside the clips on every pass:
   `max(env_floor, 2 × 0.2822 × settled_stake_per_day_at_this_clip)`, where 0.2822 is the
   daily copy-ROI standard deviation over the 23-day RN1 series and the window is the
   breaker's own settled window. Never below the floor.
4. The breaker reads that value when readable, else the floor; an **unreadable ledger
   trips** it. Today it fails OPEN — an unreadable ledger reads as zero loss and trading
   continues. Three tests pin that old behaviour and must be updated deliberately.
5. After writing the clips the endpoint runs the same pass the hourly worker runs, so the
   effective clips reflect the change on the next read rather than after the next hour.
6. The gates line prints the owner clips, the breaker in force, and its source.

Gate: one POST with `{"clips": {"rn1": 250}}` yields an effective clip of 250 for RN1 on
the next read and after the next hourly pass, with every other whale still at zero.

---

## FIX 5 — The dollar instrument (do first by the clock)

Recovers nothing by itself; every other number is re-cut on it. The per-refusal stake
already exists for the row stage and the probe simply drops the field and asks
roster-wide. The pre-row census counts refusals as integers with no dollars and re-counts
the same trade every sweep pass.

Build: key the rejection census by gate rather than by a per-row message (per-row texts
carrying a number can never aggregate, which is why the large majority of refusals are
unnamed on the probe); sum his notional per gate per whale; give the pre-row stop his
dollars, a fresh-vs-sweep key, and a bounded per-boot set so a trade's first refusal counts
once; print every latent whole-roster stop on one line; stop labelling a settled-only
figure as latency.

Gate: every trade of the day lands in exactly one of filled, row-refused by gate,
pre-row-refused by reason, or never offered — and the four sum to the day's trades and
notional within 1%.

---

## FIX 10 — Poll only the whales being copied

Five of the seven fast-lane whales have had a zero clip since the roster was cut to two and
are still polled every 2.5 seconds; demanded request rate exceeds the configured ceiling.
The exposure is a rate-limit storm that removes the reconciler and the exit lane for the
two whales we actually copy. Both lanes follow the effective clip map, read through the
executor's own reader; an unreadable map keeps the current set (never widens to the
default, never empties) and says so on the beat.

---

## FIX 3, 4, 6, 7, 8, 9 — the rest of the ranked programme

- **3 Unmapped markets.** Split "the venue does not list it" from "our key grammar", per
  whale, in dollars. The named failures cluster on esports, Turkish second-division totals,
  and per-team yes/no rows whose side never resolves.
- **4 The volume governor.** The rules write $50 and the lane spends $36–38, because the
  envelope counts fills while the flow crosses it in dollars per row. Define the envelope in
  the unit the governor spends, and print it.
- **6 HomeRunHazard's cell gates.** Two refusal classes are unscored by design and write no
  row, so their cost is unmeasurable. Write a refusal row, score it as the row-stage
  refusals are scored, and stop the sweep re-offering what cannot pass.
- **7 Adds and one-per-game.** The "other outcome of a market we hold" refusals are mostly
  not his reductions; the real defect is that we hold legs he is flat or short on, at an
  uncounted size.
- **8 Exits.** Stamp his complement-fill time, our send and fill time, and the bid at send;
  give an unfilled exit on the trade lane the retry record the position lane already keeps;
  price our exit where he prices his.
- **9 Fees and rebates.** The venue's own commission values sit unread in the stored order
  receipts; our taker fee is charged as zero in the P&L. Read them, charge the fee, and read
  his rebate ledger before claiming any rebate figure.

---

## Owner decisions

1. **The clip.** $50 today; $250 through the lever above roughly triples the share of his
   dollars we mirror. Supporting evidence measured 2026-09-03: RN1's ≥$250 band is the only
   size band of his that is positive at 95% confidence, and it carries 87% of his stake —
   our $50 clip mirrors the bands that are not demonstrated. This is a risk-appetite call,
   not one the data settles.
2. **HomeRunHazard's cell gates and entry band.** Keep until FIX 6 prices them, then lift
   flat-or-better.
3. **A second websocket endpoint.** One provider outage cost a full day at poller latency.
4. **The add and per-game caps** — decide on the quoter census, not before.
5. **Mirroring his exits** — on for RN1, off for HomeRunHazard, on the graded evidence.

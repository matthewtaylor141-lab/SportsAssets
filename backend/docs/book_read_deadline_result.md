# Book-read deadline: result (2026-10-01)

Branch `claude/rel-reads`, based on `a8bf09a` (release candidate `claude/rel-cand3`).

## Defect
Source: research/derek_entry_lane_investigation_2026-10-01.md, Q2, on `claude/command-center`.

In `workers/ext_pinnacle_loop.py`, `venue_quote` read each candidate's venue book like this:

```
asyncio.wait_for(asyncio.to_thread(_read_book_blocking, slug), timeout=VENUE_TIMEOUT_S)
```

It passed no deadline. Two things followed:

- **The gate could out-wait the timeout.** Because the read had no deadline, the request gate could hold it for up to `MAX_UNDEADLINED_WAIT_S` (20 s). That is longer than the 10 s outer bound, so a gate hold showed up as an anonymous `TimeoutError`.
- **Each timeout delayed everything after it.** Every timeout added about 10 s to every later candidate in the batch. On 2026-09-30, 257 of the 448 `QUOTE_STALE_ON_ARRIVAL` refusals were inside the 30 s limit when the provider delivered them. Our own processing delay pushed them past it.

On a timeout, the thread's result was thrown away, and with it the per-read accounting (`grt.read_state`) that shows whether the time went to our gate or to the venue.

## Change (no threshold, evidence rule, P5 rule or funded path touched)
**`venue_quote(..., freshness_deadline_epoch_s=None)`**

The scheduled cycle now passes `provider_epoch + PINNACLE_MAX_AGE_S` (still 30 s). The read's deadline is that value minus `BOOK_READ_DEADLINE_MARGIN_S` (1.0 s).
- **Budget already used up:** if no time is left, the candidate is refused as `BOOK_READ_DEADLINE_WOULD_EXCEED_FRESHNESS` and no read is issued. The refusal carries `read_issued: False`, and `venue_requests` does not count it.
- **Deadline reaches the gate:** the deadline goes to `_read_book_blocking`, and from there to `grt.begin_read`. The gate then refuses by name instead of sleeping past it. A gate refusal of `DECISION_DEADLINE_PASSED_BEFORE_DISPATCH` or `VENUE_COOLDOWN_EXCEEDS_THE_DECISION_DEADLINE` is reported as `BOOK_READ_DEADLINE_WOULD_EXCEED_FRESHNESS`, with the gate's own code in `gate_refusal`.
- **Await bounded by the budget:** the await is bounded by `min(VENUE_TIMEOUT_S, time left)`. If it times out on the budget, the refusal is the deadline code. If it times out on the 10 s bound, it stays `VENUE_BOOK_READ_FAILED`.
- **Callers without a deadline are unchanged:** the hedge and observation paths pass no deadline and behave exactly as before.

**Timing record kept**

`venue_quote` gives the read a context dict (`_BOOK_READ_BUDGET`, a ContextVar that `asyncio.to_thread` carries into the thread). The thread writes its gate `read_id` and final counters into that dict. Every failed read returns `read_timing` with these fields:
- `gate_wait_s`: completed holds plus any hold still in progress;
- `venue_s`: elapsed time minus gate wait, which covers pacing, the SDK and the network;
- `deadline_s`, `timeout_s`, `elapsed_s`, `dispatched`, `responses`, `gate_refusals`.

The record appears in the refusal's `diagnostic`, which reaches the heartbeat's `venue_errors`, and in the candidate's refusal-ledger entry.

**`venue_request_gate.check_before_dispatch`**

The gate now writes `gate_wait_started_at` and `gate_waiting_until` into the read state for the length of a hold. A caller that gives up mid-hold therefore does not count the hold as venue time.

**`bettor_external_shadow`**

The new code is in stage `2_FRESHNESS` and classed `DECIDED`, the same treatment as `QUOTE_STALE_ON_ARRIVAL`.

**Test stubs**

Three test files stub `venue_quote` with an explicit signature. Each stub gained `freshness_deadline_epoch_s=None` and nothing else.

## Tests
New file: `backend/tests/test_book_read_deadline.py`, 5 tests.
1. **An 11 s read costs later candidates only its own budget.** Candidate A has 2 s of budget after the margin and hits a stubbed 11 s read. It is refused by name in ≤ 2 s + slack (not 10 s), and the read received its deadline. Candidate B, priced 20 s earlier, is reached at an age below 29 s and actually read.
2. **Under the margin, the candidate is refused by name with no read issued.**
3. **No deadline means unchanged behaviour:** the bound stays 10 s and the code stays `VENUE_BOOK_READ_FAILED`, now with `read_timing`.
4. **The timing record survives a timeout.** This runs the real chain (`_read_book_blocking` → pmus → SDK → our gate → mock socket). The gate holds the read 1 s, the venue then takes 11 s, and the budget is 4 s. The result is a refusal at about 4 s with `gate_wait_s≈1`, `venue_s≈3`, `deadline_s≈4` and `dispatched=1`, also present in `diagnostic`.
5. **A gate hold longer than the budget is refused at once,** with zero requests and `gate_refusal=VENUE_COOLDOWN_EXCEEDS_THE_DECISION_DEADLINE`.

## Commands and counts
Scratch databases `rel_reads` and `rel_reads_base` were created from template `mp2`, then migrated with `python -m sportsassets.scripts.migrate` through 182. All runs used `nice -n 15` and `/tmp/claude-0/gatevenv312/bin/python`.

```
cd backend
F=$(grep -l ext_pinnacle_loop tests/*.py)
RN1X_TEST_DSN=postgresql://postgres:postgres@127.0.0.1:5432/<db> python -m pytest -q -p no:cacheprovider $F
```

| run | files | result |
|---|---|---|
| base a8bf09a (existing ext_pinnacle_loop tests) | 99 | 1520 passed |
| this branch (the same 99 files plus the new file) | 100 | **1525 passed**, 0 failed, 0 skipped |
| this branch: gate/classification tests outside that set (`venue_request_gate`, `bettor_external_shadow`, `EVALUABILITY_OF`) | 15 | 190 passed |
| `tests/test_book_read_deadline.py` alone | 1 | 5 passed (about 7 s) |

## Not changed / limits
- **Delay still possible:** a slow read still costs later candidates up to `min(10 s, this candidate's remaining budget)`. The fix bounds that cost by the read's own freshness budget. It does not shorten reads that have time left.
- **Root cause still open:** why any individual venue read is slow is still unproven. The kept `read_timing` is what will now answer that question in production.

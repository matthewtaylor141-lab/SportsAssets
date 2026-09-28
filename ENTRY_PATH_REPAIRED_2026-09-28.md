# The entry path: what blocked it, what I repaired, what remains

Branch `claude/command-center`, commit `79a7c9c`. Production API unchanged, still
serving `c3d0cfc`. Nothing deployed. No funded activation. Account
`acct_fc2d773a2afa4851` remains paused.

---

## 1 · Engineering completed and tested

### The blocker was not the resolver, and the obvious repair was the dangerous one

I reported the blocker as *"`resolve_venue_identity` maps 0 of 20 EPL provider
events onto 217 available Soccer venue contracts."* That statement of the
symptom was right. My diagnosis of the cause was wrong, and I was one commit
from shipping a repair that would have lost money.

Through the authorized `research-sql` route (runs 258 and 259, both `psql
exit=0`):

| our leading token | our open markets | us_premap rows carrying it |
|---|---|---|
| lal | 468 | **0** |
| **epl** | **387** | **0** |
| sea | 321 | 1812 |
| mls | 316 | 460 |

**The US venue lists no English Premier League contracts at all.** What it does
list, when its own titles are searched for English top-flight club names, is 408
rows across 68 events:

```
atc-ebfpl-ars-che-2026-09-27-dh3-ars    "eBattles: Arsenal vs. Chelsea"
atc-ebfpl-ars-liv-2026-09-27-dh2-ars    "eBattles: Arsenal vs. Liverpool"
sports_type = efootball_team_full_time_winner    538 events
```

These are video game matches between real club names, kicking off every few
minutes. `ebfpl` is eBattles Football Premier League, not the Premier League.

The repair I was preparing was a league alias `epl → ebfpl`. It would have bound
a Pinnacle probability for the real Arsenal–Chelsea fixture to a contract
settling on a simulation of it — and **every other check the lane makes would
have passed**: the clubs match, the date matches, the market type matches, the
money line exists, the rails are satisfied. A losing position with a
correct-looking audit trail.

### Repair 1 — a realism guard where the lane actually decides

`bettor_venue_realism` establishes whether a venue contract is the real fixture
from the **venue's own words**: its `sports_type` classification, and its title,
question and slug prose. Never from a league token — `ebfpl` and the real
`ebfsa`/`ebfwca` share a prefix, and inferring from that is the same class of
guess. UNKNOWN is not REAL: an absent or unreadable catalogue row refuses.

`resolve_venue_identity` now settles this **before the intent check**, in the
same round trip that already read the event key. One read, not two, because
`venue_pace` is a process-wide serial gate and a second read per candidate is
paid by every candidate queued behind it.

The concept already existed in `api/app.py` — for a **diagnostic route**, and
nowhere in the entry lane. A guard that lives only in the route that reports the
problem does not prevent it. There is now one definition and both consume it.

### Repair 2 — stop paying for a competition that cannot be reached

`soccer_epl` cost roughly 20 metered credits a cycle, about 2,000 a day, to
reach a refusal. It is out of the requested set and cannot return through the
catalogue-read-failure path.

### Repair 3 — and my replacement set was wrong too

I ranked seven replacement competitions by `us_premap` rows matching
`'%-<token>-%'`. Run 259 asked for the same counts with the token in the slug's
**league position**:

| token | anywhere in slug | in the league position | what it actually matched |
|---|---|---|---|
| col | 1244 | **0** | `aec-mlb-col-cws` — Colorado Rockies |
| sea | 1080 | **0** | `aec-mlb-laa-sea` — Seattle |
| por | 712 | **0** | `asc-unl-den-por` — Portugal, Nations League |
| tur | 570 | **0** | `asc-unl-bel-tur` — Turkey, same |
| arg | 378 | **0** | `atc-ebfwca-arg-bra` — **eBattles** |
| lmx | 576 | 576 | real Liga MX |
| mls | 460 | 460 | real MLS |

Six of seven had zero coverage. A team abbreviation and a league token share a
slug and the pattern cannot tell them apart. I was about to point a metered
credit budget at Serie A on the strength of Seattle Mariners rows.

The venue's real soccer board, by its own league token and event count, is
`unl 39, intf 15, engnl 12, cnl 10, uwcl 9`, then club competitions in single
figures (`arg2 8, lco 7, brb 6, uslc 5, irl1 5, par2 4, mls 3, lmx 3`). **Late
September is an international window.** A frozen list of club competitions would
have been wrong within a fortnight even if I had measured it correctly.

So the candidate set is **derived from the venue's own board at cycle time**
(`venue_soccer_competitions`), with simulated competitions excluded in SQL by
the venue's own words, and a token map saying only which provider key
corresponds to a venue league token. International friendlies are excluded with
a stated reason — their settlement and team-selection conventions are not
established for this lane, so a candidate would reach a rule refusal, not a
trade.

**The provider's key names remain mine to guess.** This container has no
provider egress and no key, so I could not read `/v4/sports`. `/v4/sports` is
unmetered, so the cycle confirms each candidate against the provider's own
catalogue before making a metered call on it. A key I guessed wrong becomes
`PROVIDER_DOES_NOT_LIST_THIS_COMPETITION` in the report instead of a bill.

### A defect I introduced, found, and fixed

`fetch_sport_catalogue` had no caller inside the cycle, so its unwrapped `httpx`
error was harmless. Putting it on the cycle's path made a provider transport
failure kill the whole cycle — including the funded servicing that does not use
this provider at all. Three cycle tests failed with `ProxyError`. It now returns
the same refusal a non-200 does, and a test exercises the real function against
an unreachable host.

### Tests

70 pass across the two new files, the loop suite, and the audit-findings set.
The A9 event-namespace test asserted a literal SQL string that my refactor
relocated; it now chases the query to its new home and asserts **both** halves —
the resolver taking its key from that row, and that query reading `event_slug`
from `us_premap` — rather than being weakened to the half that still passed.

---

## 2 · Deployed and observed behaviour

Nothing from this work is deployed. Production serves `c3d0cfc`, deployed
2026-09-28T13:03:48Z, migrations 125–128 only.

The live cycle I diagnosed (`ext_pinnacle_last_cycle`, build `c3d0cfc`) reports
`evaluated 0, written 0, markets_considered 233, elapsed 48.62s`,
`cycle_label ZERO_EVALUATED__INPUT_PATH_BLOCKED`. Its own note is correct and I
repeat it rather than soften it: *with `evaluated == 0` no candidate reached
execution estimation, so this cycle establishes NOTHING about available edge. It
is an input-path fact.*

The funded book is empty: 0 intents, 0 fills, 0 economic events, $0.00. Funded
authorization keys: zero rows.

**This repair is not yet observed in production.** It is engineering that passes
tests. Whether the Nations League path reaches a candidate depends on the
provider listing `soccer_uefa_nations_league` — which I could not check from
here, by design of the confirm-before-spend step.

---

## 3 · Available market and model evidence

Unchanged by this work, and I am not claiming otherwise. All 1,126 valuation
rows this lane has ever written are `sport_family = baseball`, `market = h2h`,
**0 admissible**. No soccer candidate has ever reached valuation, so nothing in
that cohort speaks to soccer economics either way.

Codex's corrected settlement counts stand: INCOMPATIBLE 527 (37 with positive
edge), UNKNOWN 353 (15), NOT_RECORDED 246 (15). This establishes no recorded
compatible result in this historical cohort. It does not establish that
settlement evidence was never captured, or that no supported market can qualify.

---

## 4 · Account authorization and limits

The account stays paused. `accounting_status = ACCOUNTING_UNCERTAIN`, incident
`DESK_ACCOUNTING_RECOVERY_2026_09_23`, `pnl_reliability =
UNRELIABLE_DO_NOT_QUOTE`, measured identity drift **$2,367.72**,
`last_verified_at` empty. No funded activation is requested or authorized here.

### What `daily_loss_stop_usd` actually enforces — owed, and now exact

The field name says daily. **There is no daily window, no calendar boundary and
no reset.** It binds the `MAX_DRAWDOWN` rail, which `exposure_from_rows` sums as
**three** terms over the lane's whole book — the previous statement named two,
which understated it:

1. for each **settled** position, `max(0, −realised_net)`. Realised losses in
   full; realised **gains do not offset them**;
2. for each unsettled position **with** a mark, `max(0, cost − mark)` — a marked
   position contributes as soon as its mark falls below cost, not only at
   settlement. This is the term the old description omitted;
3. for each unsettled position **without** a mark, the entire cost basis,
   counted as a total loss. The proposed position is unsettled and unmarked by
   construction, so its full cost is added too.

**Reset behaviour.** `OPEN_BOOK_SQL` is `WHERE p.experiment_id = $1` with no
time bound, so every position the lane has ever taken stays in the row set.
Terms 2 and 3 fall as positions mark better or settle. **Term 1 never falls.**
The rail is monotonically non-decreasing in realised losses, and the only thing
that clears accumulated realised loss is **changing the experiment id** — which
starts a new book and is an operator act, not a reset.

So a $20 approval means: refuse the next entry once worst-case exposed loss
reaches $20, and keep refusing until positions mark better or settle. It does
not lift overnight. `cumulative_loss_stop_usd` is accepted as the accurate
synonym; the old key is kept because recorded approvals use it.

There is also still **no continuous funded loss breaker**. `MAX_DRAWDOWN` is
evaluated at submission time, so it cannot halt mid-session independently of a
submission attempt. I am stating the gap rather than implying coverage.

---

## 5 · Real-venue execution still requiring verification

- The provider must list `soccer_uefa_nations_league` and the other mapped keys.
  Unconfirmed from here; the cycle confirms before spending.
- Whether a Nations League fixture resolves through `premap.resolve` once the
  competition is requested. The resolver was never the defect for EPL, and that
  does not establish it bridges UNL.
- The two book-currency refusals in the live cycle remain: one candidate at
  `age_s 950.556` on `M3_ORIGIN_GENERATION_INSTANT` against a `venue_clock`
  reading of 32.776 s — two clocks disagreeing by 30×, and 32.776 exceeds the
  30 s bound regardless. The other is `NO_MECHANISM_AVAILABLE`. Not repaired.
- No order has been sent. Order authority has not been tested and will not be
  tested by sending an unauthorized order.

---

## What I am not claiming

That the trader now trades. One input-path gate is repaired and a
capital-losing mis-repair is prevented; the funded pair work (items 3–5 of your
directive) is untouched by this commit. Nothing here supports a revenue or
completion claim, and the milestone of real-money autonomous operation is not
met today.

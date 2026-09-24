# Timestamp integrity, the recurring path, and four corrected claims

2026-09-24. Shadow only. No funded orders, the damaged account stays
paused, the collector and every request budget are untouched, and
management's frozen policy is not consulted by anything here.

---

## 1 · The odds credential, and what was actually required

`EDGE_ODDS_API_KEY` is now provisioned on **sportsassets-api**.

Run **35941026587**, 2026-09-24T01:01:04Z. The env-var key list *before*
the write did not contain it; the list *after* does, and the Render PUT
returned **HTTP 200**. Both listings print **key names only**.

**The mechanism, and why it is shaped this way.** The value is not a
workflow input. `workflow_dispatch` inputs are stored verbatim in the
run's event payload, which is readable from the API — and `::add-mask::`
suppresses a value in *log output* and does nothing about that payload. A
key typed into a dispatch form is already disclosed. So the step reads
`secrets.EDGE_ODDS_API_KEY`, builds the request body with python from
`os.environ` (never `argv`, so it cannot appear in a process list),
`shred`s the body, and verifies by listing names. Secret store to secret
store, with no third copy anywhere.

This is deliberately **not** the generic `render-ops env-set`, which
takes `KEY=VALUE` as an input and would therefore put the key in the
event payload.

**Provisioning is not proof of use.** A wrong value, a revoked key or an
egress rule all look identical from outside, so
`GET /api/command/rn1x/external/probe` makes one real request **from the
API process** and reports the HTTP status, the event count, Pinnacle
coverage, quote ages and the provider's quota headers. It returns
`key_value_returned: false` and no provider error body, because those
bodies can echo the query string.

## 2 · The recurring production path

`workers/ext_pinnacle_loop` is the runtime caller that did not exist.
Before it, `bettor_external_shadow.evaluate` was reached by a test and a
research script only — so the entry inputs it forwards into
`bettor_entry_gate.admit` were supplied by fixtures and nothing else.

One cycle, failing closed at every step: control row (`ingestion_state`,
the same table every other loop's stop lives in) → tables against the
live catalog → credential by presence → fresh odds → **exact** venue
contract → **contemporaneous** ask and displayed depth → production fees
→ the real gate → persist.

Two findings worth more than the plumbing.

**The existing normaliser collides two clubs.**
`edge/venues/mapper.norm_team` strips `city`, `town`, `united` and `utd`
as noise. Measured, not supposed:

```
norm_team("Manchester City")   -> 'manchester'
norm_team("Manchester United") -> 'manchester'      COLLIDE
norm_team("Leeds United")      -> 'leeds'
norm_team("Leeds City")        -> 'leeds'           COLLIDE
```

Both Manchester clubs were on the slate this source priced (Liverpool v
Manchester City **and** Manchester United v Tottenham, run 35935500538).
Reusing that normaliser would have been licence to value one derby and
buy the other. `bettor_venue_mapping` therefore deaccents, lowercases and
drops punctuation and **nothing else**, keeps token order, requires both
team names fully contained in exactly one open market, and refuses a
collision by name.

**The venue's settlement rule is not in our data.** Pinnacle's is known
per sport and the two differ — soccer full-time 1X2 is regulation 90 plus
stoppage, the MLB moneyline includes extra innings. The `markets` table
carries condition_id, title, slug, event_title, sport, tags, closed,
resolved and resolved_prices, and none of those states a settlement rule.
"Will Fulham beat Hull City?" is consistent with regulation-only *and*
with including extra time. So `bettor_venue_settlement.ATTESTED` is
**empty**, `agrees()` returns `None`, and the loop refuses by name and
counts it.

That refusal is recorded **on the row**, not used to skip the row — the
persisted record still carries the raw odds, both timestamps, the
mapping, the de-vig method, the probability, the executable price, the
cost and the estimated edge, so management can see exactly what the
engine was looking at when it declined. A test asserts a caller refusal
vetoes admission, and a paired test asserts the same inputs clear without
it, so the veto is not a function that refuses everything.

To close it: capture the venue's per-market rules text, or measure the
rule from `resolved_prices` on fixtures decided after 90 minutes. Neither
is done, and the module says so rather than implying coverage.

## 3 · Timestamp integrity

**What was wrong.** `bettor_rn1x_run` wrote

```python
detected_ts = max(src_ts, det_raw)     # a DERIVED value in an OBSERVED column
decision_ts = detected_ts              # "so the CHECK passes"
```

Two separate losses. The chain lane's `detected_at` legitimately precedes
the fill's own `ts` (median −0.6 s over 77,712 RN1 fills), so `max()`
replaced an observed receipt instant with a derived one and the raw value
was never persisted anywhere. And the decision instant was **backdated**
to that availability instant — which is why the traced prospective
position showed `source_ts = detected_ts = decision_ts =
2026-09-23T22:14:47Z`. That is not three observations. It is one instant
copied twice, and the cycle that wrote it ran later.

**The repair.** Four clocks, and which are observed is part of the record:

| | |
|---|---|
| `source_ts` | the venue's own instant for the fill — OBSERVED |
| `detected_ts` | when our pipeline recorded seeing it — OBSERVED, preserved verbatim |
| `available_at` | `max(source_ts, detected_ts)` — DERIVED, stored separately |
| `decision_ts` | when the policy actually decided — OBSERVED |

The CHECK now constrains the derived value against the observed ones and
requires a decision to follow availability. It no longer requires
`detected_ts >= source_ts`, because measurement contradicts that ordering
and a constraint contradicted by the data is what wedged the prospective
lane in the first place.

The prospective lane reads `time.time()` in the worker and passes
`BASIS_RUNTIME`; the historical lane keeps the availability basis and is
labelled `REPLAY_AT_AVAILABILITY`, because stamping a replay of a settled
market with today's clock would be a worse claim than the one being
fixed. A runtime basis with no clock supplied is **refused**, not silently
fallen back — that silent fallback is the whole bug.

**Orders cannot fill against the past.** The old loop filtered prints on
receipt time only, against a backdated `decision_ts`, so on a delayed
cycle every print between the evidence arriving and the cycle running was
consumable by an order that did not exist. There are now two gates: a
prospective decision more than `MAX_PROSPECTIVE_DECISION_LAG_S` (300 s)
after availability is refused outright, and within that window a print
whose own execution instant precedes the order's creation is skipped and
counted. Independently, a `BEFORE INSERT` trigger on `rn1x_fills` refuses
any fill earlier than its order's `created_at_runtime` — a trigger and not
a CHECK, because a CHECK cannot read another table.

**The audit, and the labelling.** Migration 104 marks every pre-existing
row `BACKDATED_TO_AVAILABILITY_UNAUDITED` with a `clock_integrity` string
stating that its receipt instant was overwritten and is unrecoverable,
and that **an unresolved outcome on it does not establish a prospective
decision**. Those rows are labelled, not rewritten: inventing a creation
time for them would be the original defect again.
`GET /api/command/rn1x/clock-audit` and the `rn1x_clock_audit` view report
the split; the trace route now carries `clock_semantics` saying in words
what that row's timestamps are and are not.

**Verified by mutation.** 13 tests; reverting `detected_ts = det_raw` to
`max(...)` fails one, restoring the backdated `decision_ts` fails four,
and removing the order-creation floor fails one.

## 4 · Four claims corrected

**The Pinnacle run.** It was 42 valuations with **opportunities not
assessed, because venue prices were unavailable** — not a search that
found no opportunities. Nothing was compared; the comparison's other half
did not exist in that run.

**Coverage.** NBA 0/41 and NHL 0/33 means **Pinnacle was absent from
those events in that response, at that time** — one bulk request per
sport at 23:28:5xZ. It does not establish that Pinnacle never quotes
those sports. The loop still omits them, because absent-in-the-response is
the only thing we can act on and asking anyway spends credits to be
refused; that is a budget decision, not a claim about the book.

**The `order_submitted` CHECK.** I offered it as evidence that no order
could be submitted. It is not. It constrains what a row may *say*; an
order could be placed and never recorded and the CHECK would hold. Worse,
the loop's own import closure reaches `pmus`, which also defines
`submit_fok` — so "no submit appears in this module" is weaker than it
sounds.

What actually stops a funded order is the **venue boundary**:
`pmus.submit_fok`'s first executable statement is
`_gate.authorize("submit", lane=_lane(), slug=...)`, read at the moment of
submission and never carried in by a caller, and denial **raises**. An
undeclared lane is treated as `unknown` and still gets the copy controls.
With no pool bound the gate builds an empty snapshot and decides on it
rather than short-circuiting to permission. `tests/
test_ext_shadow_cannot_fund.py` checks the first-statement property and
the attribute set taken off `pmus` **with the AST**, not by string search,
because a comment would fool a grep in either direction. The CHECK
remains as a backstop against a lying writer, which is all it ever was.

**Duplicate accounting.** There was none, and a test proved it: two
cycles over an unchanged quote took the row count 1 → 2. Migration 105
adds one row per provider observation, keyed on the **book's**
`observed_at` rather than our receipt time — keying on ours would have
made every cycle "new data" by definition. It deduplicates before
indexing, keeping the earliest row and never preferring an unsettled copy
over a settled one, and `persist` returns `None` on a conflict so the
caller does not count a skip as a write.

## 5 · What is still not established

- **No opportunity has been found, and none has been ruled out.** With the
  venue settlement rule unattested, every evaluation refuses before
  admission on that ground alone.
- **No calibration, no net shadow return, no execution sensitivity.** All
  three need outcomes joined to predictions recorded beforehand.
- **`P_FILL` remains `NOT_IDENTIFIED`.** Every fill anywhere in this stack
  is modelled, and the depth this loop reads is *displayed* depth, which
  the snapshot module itself says is not a queue position.
- **This source has never been measured against the venue price.** The
  internal challenger was measured *worse* (Δ log loss −0.00926); nothing
  here revisits or supersedes that.
- **render-ops.yml has 422 bytes of headroom**, already below this
  repository's own 32 KiB rule. It is the lever that operates Render and
  holds the trading kill switch. That is a pre-existing condition, not
  caused by this work, and it is why the control row is armed by a route
  rather than by a statement added to that file.

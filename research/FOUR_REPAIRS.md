# The four authorized repairs

Engineering record for the changes released in this commit. It is not the
management record and it does not carry a verdict on the shadow system;
those stay in `FINAL_ACCEPTANCE_RECORD.md` until the readback below is in.

Funded submission remains disabled, the uncertain account stays paused,
the worker service stays pinned at `f5d1c05`, and nothing here can send an
order to a venue.

---

## S1 · Shadow sizing now fits the rails it is judged against

**The defect.** Sizing asked for a `STANDARD`-dollar notional. The rails
then measured the *reservation* — quantity times the break-even limit,
which is the right conservative basis. But the dollar rails are themselves
`STANDARD × 1`. So the comparison was

```
qty × break_even_limit      ≤  STANDARD
(STANDARD / vwap) × limit   ≤  STANDARD
limit / vwap                ≤  1
```

and `limit > vwap` is precisely what having an edge *means*. Every
candidate with any edge at all breached `MAX_MARKET_EXPOSURE`,
`MAX_EVENT_EXPOSURE`, `MAX_CORRELATED_EXPOSURE` and `MAX_DRAWDOWN`
simultaneously — on a book holding nothing. Run 71 recorded exactly that:
five positive-edge candidates, all five with the same five rails failed,
reserving $1042.05 and $2275.86 against a $1000 rail.

`MAX_RESIDUAL_INVENTORY` was unreachable for a second, independent
reason: a `STANDARD` budget buys `STANDARD / price` contracts, which
exceeds the 2000-contract rail at any price under $0.50 — that is, on
every underdog.

**The repair.** `headroom_from_rows` asks the *same* measurement function
what each rail still allows with nothing proposed; `qty_cap_from_headroom`
converts that to the largest quantity the binding rail permits at the
reservation price; `estimate(..., headroom=...)` reduces the budget to
that before it walks the ladder. The standard trade stays the intent
ceiling — the rails only ever reduce it, never raise it. **No limit
moved**, and `LIMITS_SHA` is asserted unchanged.

Rails are classified once, from what `exposure_from_rows` actually adds:
five scale in dollars, `MAX_RESIDUAL_INVENTORY` scales in contracts, and
`MAX_CAPITAL_HOURS` does not scale at all — so a breach of that one is a
refusal, not a smaller trade. A test asserts the three sets partition the
rail list, so an added rail cannot be silently ignored by the cap.

**Evidence.** `test_shadow_sizing_fits_the_rails.py`, 221 tests. It proves
the *old* arithmetic unsatisfiable across the price range rather than
narrating it, and closes the rounding class by sweeping all 98 prices from
$0.01 to $0.98 through the engine's own `verdict()` — the cap is computed
from the rounded reservation price the rails see, with a one-microdollar
shave, because `round(qty × price, 6)` can otherwise push a position sized
to land exactly on a limit past it.

---

## S2 · The two positive-edge candidates, audited

Seven dimensions were asked for. Three were already sound; four could not
be answered from the row at all, which is itself the finding.

| dimension | state | what was wrong |
|---|---|---|
| fixture | sound | condition id and event key on the row |
| payout identity | **broken, repaired** | see below |
| intent | sound | `ORDER_INTENT_BUY_*` → ladder side, and the payout event is *not* read off the intent (repaired earlier) |
| market period | **broken, repaired** | see below |
| price orientation | sound | `payout_is_complement` is set only from demonstrated complementarity |
| fees | sound | taker fee at the best acquisition price for the limit; realised per-level fees for the cost |
| freshness | **unreadable, now persisted** | see below |

**Payout identity — the row's evidence was three nulls.**
`resolve_venue_identity` recorded `matched_side_norm`, `matched_identifier`
and `matched_question` from keys `premap.resolve` does not return. It
returns the side under `outcome`, the identifier under `market_slug` and
the question under `title`. So every candidate in production carried three
nulls under a comment promising the resolver's evidence was "PRESERVED
rather than discarded". It was discarded. Nothing downstream read them, so
nothing broke and nothing complained — which is exactly why payout
identity could not be audited from the row meant to make it auditable.

The test fixture could not have caught it either: `fake_resolve` returned
only `market_slug` and `intent`. It now mirrors the real return shape.

**Market period — it was a literal on both sides of the comparison.**
`contract["period"]` was the string `"FULL_GAME"`, and the same literal was
passed to the valuation, so two assertions of `FULL_GAME` always agreed.
`is_segment()` reads only the title, while its sibling `is_line_market()`
reads the title *and* the slug — and the venue puts the period in the slug.
Run 71's board read proved the exposure real: `atc-mlb-atl-mia-2026-09-25-i6-draw`
(inning six) and `atc-ebfcwc-bjo-paris-2026-09-26-dh1-bjo` both came back
inside the money-line family, because `copy_sports.market_type_of`
classifies the venue grammar on the kind prefix alone.

`period_of_venue_slug` now establishes it affirmatively and without a
vocabulary: everything after the trailing `YYYY-MM-DD` must be empty (the
`aec` family, where both sides share one slug and the side is the intent)
or exactly the side the resolver matched. Anything else is
`VENUE_CONTRACT_PERIOD_NOT_ESTABLISHED`, attributed to `3_IDENTITY` —
because which period a contract pays on is part of *which contract it is*.
A whitelist of `i6`/`dh1`/`h1` would only refuse the segments someone had
already thought of; a test sweeps invented tokens to show this does not.
Double chance is excluded by the same rule, incidentally but genuinely.

**Freshness — `fresh: null` is UNKNOWN, not stale.** Both candidates
failed the `STALE_DATA` gate with Pinnacle quotes 15.98 s and 21.65 s old
against a 30 s rule, which looks like a contradiction and is not one:
`_entry_freshness` returns `fresh: None` whenever *either* clock is
unmeasured, and the venue supplies no `transactTime` on some reads. Those
two states have completely different remedies — one is our latency, the
other is a field the venue did not send — and neither was on the row. Both
ages, both limits, the venue's clock basis, and the provider-lag / our-
processing split now travel on `risk_verdict.freshness_evidence`, beside
the waiver record, for the same reason the waiver is there.

**The remaining blockers on those two candidates are unchanged and real:**
`STALE_DATA` (a venue clock, not ours to invent) and
`UNRESOLVED_SETTLEMENT_SEMANTICS` (the payout-rule conflict). Neither is
waivable by the research waiver, and neither was loosened.

---

## S3 · Soccer: the mapping question, and the rules

**The rules are now captured on the book side.** Pinnacle's soccer section
was fetched verbatim in run 67 and had sat unread in a fixture, because the
terms extractor's keyword vocabulary was baseball-only. `BOOK_TERMS` now
carries `("soccer", "h2h", PRE_GAME|IN_PLAY)` with every payout class tied
to a quoted line — line 382 (the 90-minute basis, which *excludes* extra
time and penalties, the opposite of baseball's extra innings), line 383
(abandoned → void), the general 12-hour rule. A test asserts each quote is
character-identical to the capture, and that the three conditions the prose
is *silent* on stay **absent** from the map: silence must stay silence,
because a filled-in condition reads as agreement.

Two carve-outs are enforced rather than noted: a World Cup fixture gets 72
hours instead of 12, and a knockout tie decidable by extra time is a
format where the 90-minute basis and a venue "winner of the match"
contract disagree about the same match. Two in-play void branches — a VAR
decision, and incorrect score/corner/red-card information — are recorded
in `SOCCER_IN_PLAY_EXTRA_VOID_BRANCHES` and deliberately *not* mapped, so
a COMPATIBLE verdict on the seven modelled conditions does not claim to
cover them.

**Measured consequence, run before release.** With the venue's own draw
sibling present — which the venue does list for soccer — the draw rule
that has blocked soccer since it was written now **establishes** from
venue data. The two rules still unmet, `overtime` and `void`, both depend
on one thing: the venue's published soccer prose, which `read_rules_text`
already fetches at evaluation time and which has never been read because
no soccer candidate ever got that far. So the soccer chain is complete
except for the mapping.

**The mapping itself is not repaired blind.** The venue lists
`lmx-aft-cmf-2026-09-25` "Atlante FC vs. CF Monterrey" — the same fixture
the lane refused as the global slug `mex-atla-mon1-2026-09-25-mon1`. The
slug shows *two* candidate gaps at once, and they have different remedies:

```
league token   ours `mex`,  the venue's `lmx`
team codes     ours `atla`/`mon1`,  the venue's `aft`/`cmf`
```

A league alias is a one-token bridge the resolver already has
(`_yn_alias_pick`, `matched_by: premap_alias`). Team codes are a different
mechanism entirely (`code_translated`, `code_pair`, `club_by_exclusion`)
and need a witness from the venue's own question text. Guessing which
applies is how an alias table gets invented, so `/api/admin/shadow-mapgap`
runs the existing read-only `resolve_explain` over the lane's own soccer
universe and reports the resolver's own step name, plus the league-alias
probe's answer for each miss. **The repair follows the probe, in the next
commit, once the step is named.**

---

## S4 · The whole venue board, not the first 400

`desk-games?league=everything` reports `counts.everything = 1400` and then
returns `cards[:400]`. The census in run 71 was therefore a 400-event
sample sorted by the tail of the event id, and "no `epl` token appears" was
unsound in one direction: absence from a sample is not absence from the
board.

No second venue sweep is needed — `_desk_sweep` already pages 14 × 100 and
caches the lot. `/api/admin/venue-competitions` reads that same cache
untruncated and reports, per league token: event count, money-line events
and sides, the desk classifier's own bucket (run 71 found non-soccer tokens
inside its `soccer` bucket), and whether the **venue's own titles and
labels** carry a simulation marker. Real and simulated are separated by
the publisher's words, never by a token guess: `ebfcwc` fixtures carry
real club names — "Man City vs Boca Juniors" — and say "eBattles" in the
label. A token where only *some* events carry a marker is reported as
`MIXED` rather than collapsed either way.

`NO_SIMULATION_MARKER` is stated as what it is: the publisher did not mark
it otherwise. It is not a claim that a competition is real.

---

## Found while doing S1, named and NOT fixed here

`MAX_EVENT_EXPOSURE` cannot see held positions on the same event.
`OPEN_BOOK_SQL` selects `condition_id`, `cost_usd`, `qty`, `opened_at` and
`realized_net_usd` — but not an event key — so `exposure_from_rows`
receives `event_key: None` on every held row and the event rail only ever
counts the position being proposed. The per-*market* rail works, because
`condition_id` is selected; the per-*event* rail, which exists precisely
because "two markets on the same game are one bet on that game", is blind
to exactly the case it was written for.

This is pre-existing and orthogonal to the sizing repair, and it only
binds once this lane holds two positions on one fixture — it holds zero
today. Fixing it means carrying an event key onto `rn1x_positions`, which
is a migration, so it is named here rather than bundled into an authorized
sizing repair. **Next action:** add the event key to the positions row and
to `OPEN_BOOK_SQL`, then assert the rail aggregates two markets on one
fixture.

The open book is correctly scoped to `experiment_id =
EXT_PINNACLE_DEVIG_V1_SHADOW`, so the historical lane's −$3,033 of
realised losses does not consume this lane's drawdown headroom. Were it
ever to, the refusal would now be the named
`NO_RAIL_HEADROOM_FOR_ANY_POSITION` rather than five silently failed
rails.

---

## What is pre-existing and not caused by these changes

- `tests/test_bettor_sport_mapping.py::test_wiring_the_mapping_did_not_move_the_policy_code_sha`
  fails. Verified by computing `semantic_code_sha()` on the stashed
  pre-change tree: `5a4b1c52…` before and after, against a pin expecting
  `84c80e7c…`. The pin was already stale; it is **not** updated here,
  because updating a pin over code I did not move would defeat it.
- `031_us_premap_signed.sql` and `055_us_premap_team.sql` fail on a fresh
  database with `relation "us_premap" does not exist`.
- `test_workflow_size_guard.py` fails on both trees: **`render-ops.yml` is
  511,578 bytes with 422 bytes of headroom** against GitHub's 512,000-byte
  per-file ceiling. That file is the lever that operates Render and holds
  the trading kill switch, and it was already disabled once by an
  eight-line comment — which is why the guard exists. My change adds
  10,598 bytes to `command-verify.yml` (248,993 → 259,591), a different
  file with ample headroom, so it is not the cause; but this is an
  operational hazard on the kill switch and it is one comment from
  recurring. Outside the authorized scope here, so flagged rather than
  touched. **Next action:** split `render-ops.yml`, or move its prose to
  `RENDER_OPS_NOTES.md`, before anything else is added to it.
- The `ent2` "32 failed" discrepancy stays open, as recorded.

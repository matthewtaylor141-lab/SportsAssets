# THE FULL-METHODOLOGY PROGRAM — buys, sells, shorts, exits, re-entries, live, shadow-first

Companion to `docs/mirror-to-a-tee-program.md` (revision 2, "the mirror programme") and
`docs/copy-chain-program.md` (the per-fill lane's accuracy programme). Written to be committed beside them.
Where this document says §0, §1, §3b, §4 M<n>, §5 decision <n>, §6 without a name it means the MIRROR
PROGRAMME's section. Nothing here contradicts its §1 (dropped and amended) or §6 (what to-a-tee cannot
mean); where it extends a decision it says so and gives the evidence.

Abbreviations as in the mirror programme: `le` = live_executor.py, `ml` = workers/mirror_live.py,
`rules` = analytics/mirror_live_rules.py, `mi` = analytics/mirror.py, `ms` = workers/mirror_shadow.py,
`mr` = analytics/mirror_report.py, `wx` = workers/whale_exits.py.

**PROVENANCE OF EVERY LINE NUMBER IN THIS DOCUMENT.** Repo HEAD 35748fd, branch claude/session-njaewf,
read-only. Another wave is building in this tree right now. At the time of writing `git status --porcelain`
shows modified: `.github/workflows/set-roster.yml`, `analytics/gate_edge.py`, `analytics/lane_exec.py`,
`analytics/roster_rules.py`, `api/app.py`, `api/copies_record.py`, `api/track_record.py`, `config.py`,
`ingestion/chain.py`, `ingestion/claim_registry.py`, `ingestion/poller.py`, `ingestion/s1_emitter.py`,
`live_executor.py`, `workers/roster_auto.py`, `workers/whale_exits.py` and their tests.

* Line numbers for **ml, ms, rules, mi, mr, pmus.py, analytics/engine.py, analytics/proof.py,
  analytics/decompose.py and every migration** are WORKING-TREE numbers and those files are CLEAN, so they
  are also HEAD numbers. I read them this session.
* For **le, wx, app.py, track_record.py, copies_record.py** — dirty — this document cites SYMBOLS, never a
  working-tree line, except where it says "(HEAD)", which means I read it with `git show HEAD:<path>`.
  A builder must re-anchor on the symbol, not on a number: the buys lens's own line numbers for
  live_executor.py were already ~200 lines stale by the time its refuter ran.
* No production figure is quoted anywhere in this document. Where a number is needed it is (a) a constant or
  a query in a repository file, cited; (b) a figure recorded in the committed mirror programme's §0/§4/§6,
  cited as such and marked as a BASELINE recorded there, not as a reading taken now; or (c) a threshold this
  programme sets. Everything else is written **unmeasured**. The probe logs behind the programmes' numbers
  are gone and are not re-derivable here (no DB, no network in the session that wrote this).

---

## 0. WHERE THE SYSTEM STANDS TODAY, with evidence

| fact | evidence (read this session unless marked) |
|---|---|
| We copy his BUYS only, per fill, at a stored clip, for two whales; the position mirror is CANCEL-ONLY | owner statement; `ml:2657-2662` MODE_SAFE runs `_reconcile_orders` + `_instruments` and RETURNS |
| **The mirror's stop is a strand, not a stop.** SAFE returns BEFORE `_global_guards`, and the admin flatten lever is read inside it | `ml:2657-2662` (the SAFE return) vs `ml:697 _mirror_stop("mirror_flatten")` inside `_global_guards`; `_STATE_FLATTEN = "mirror_flatten"` at `ml:132` |
| **A wrong BUY can leave today.** `ms.account_positions` drops a position row it cannot parse and still reports the walk COMPLETE, while raising on the page cap | `ms:365-366 except (TypeError, ValueError): continue` inside the page loop; `ms:372` the comment "A WALK THAT HIT THE PAGE CAP IS NOT A READING OF THE ACCOUNT" and the raise below it |
| **Three unreadable reads abandon the tick above the reconcile,** so live rests stand unbooked and un-TTL'd | `ml:2674 / :2678 / :2682` `_abandon(...)` + `return`, all above `ml:2685 await _reconcile_orders(t)`; the SAFE and cancel_all branches at `:2660 / :2665` DO reconcile first |
| **The whole-slug close is decided by a truncating comparison** and is the one order the worker sends with no clamp to its own book | `ml:2390 sole = ledger >= int(held)`; `ml:2427 t.pmus.close_position(...)`; contrast the rest path's clamp at `ml:2100` |
| **One shell lever can loosen a money bound.** Every MIRROR_* dollar cap is `capped_env` (downward-only, `rules:162`), but the freshness gate on HIS position is a raw env read, as are six other MIRROR_* knobs | `ms:84 SNAP_MAX_AGE_S = float(os.environ.get("MIRROR_SNAP_MAX_AGE_S", "300"))`; `ms:74,75,76,79,89,104`; consumed at `ml:1556` |
| **Post-only can be turned off from a shell, and latches off by itself** | `ml:272 os.environ.get("PMUS_MIRROR_POST_ONLY", "on")`; `ml:185 _POST_ONLY_OK = True`; `ml:2288 _POST_ONLY_OK = False` on a fill-at-create |
| **Phase 1 is unwired and it is the single reason the mirror opens no book.** The reader, the admission fact and the net-drift rule all exist and nothing calls them | `wx:524 async def market_positions(...)` — no caller anywhere in `sportsassets/`; `rules:430 snap_market_fresh` and its only consumer `rules:501`; `rules:1212 drift_net_rule` called by nothing |
| `last_fresh_agreed` is a hard-coded literal at both worker call sites, and `rules` says the worker must ASSERT it, default False | `ml:1826` and `ml:2496 last_fresh_agreed=True`; `rules:1188` and `rules:1204` |
| **The mirror day cap literally filters one side spelling,** and the order table's CHECK excludes every short spelling | `ml:355 WHERE side = 'BUY_LONG' ... /* ml-mirror-day */`; `migrations/047_mirror_live.sql:84 CHECK (side IN ('BUY_LONG','SELL_LONG'))` |
| **The pre-trade money bound is computed in the wrong space on a short,** and there is no venue cross-check at all on a sell | `pmus.py:2381 expected_cost = limit_price * quantity`; `pmus.py:2383 if not sell:` guards the whole preview block; `PREVIEW_COST_TOLERANCE = 1.02` at `pmus.py:36` |
| **`_confirm_gone` answers "gone" about a token whose complement is in the response it just read** | wx (working tree) `_confirm_gone`: the loop matches `str(p.get("asset") ...) == asset`, and after the loop `return True` with the comment "the leg is not among them: gone" |
| **A fail-closed exit refusal is discarded, not pinned:** `mx_exit_dedup_unreadable` and `mx_exit_recently_applied` are absent from the allowlist that its twin `mx_exit_ledger_unreadable` is in | HEAD `le` `EXIT_PENDING_REASONS` frozenset — I listed every member at HEAD; neither name is present |
| **Migration 049's fidelity columns have no writer** | `migrations/049_mirror_fidelity.sql:32,37,42` add `trigger_trade_id`, `his_fill_ts`, `first_fill_at`; grep across `sportsassets/` finds them only in the DDL and `tests/test_mirror_live_migration.py:464-467` |
| **A book that EXITS before resolution has no graded P&L forever** | `ml:1703 settled_pnl = own = None`, written only under `ml:1706 if status == "settled":`; the other close path writes `state='closed'` with no `settled_pnl` |
| **There is no proof instrument for the mirror and no data source for the promotion gate** | `analytics/proof.py:305 AND COALESCE(lo.lane,'') <> 'mirror'`; `MIRRORGRADE` appears once repo-wide, in a docstring at `rules:1393`, with no producer; `rules:1399 p2_verdict` and `rules:1375 demotion_due` have no caller outside tests; only `/api/admin/mirror-shadow` (`app.py:5697`) and `/api/admin/mirror-cover` (`:5709`) exist |
| **No money path reads buying power.** Three lanes place into one account | grep for balances/buying power finds `api/pmus_account.py` and `api/app.py` only; `rules` `AdmissionFacts` has no cash field |
| `SHORT_PROBATION_N` ships at 3 and the gate stops serialising once that many verdicts exist | HEAD `le:94 SHORT_PROBATION_N = int(os.environ.get("LIVE_SHORT_PROBATION_N", "3"))`; HEAD `le:5602 if done >= SHORT_PROBATION_N:` |
| Migrations present: … 045, 046, 047, 049. 048, 050, 051 are unused and RESERVED by §2 rule 3 | `ls backend/migrations` |
| `api/app.py` has no router: 147 bare `@app.get`/`@app.post` decorators, zero `APIRouter` | grep counts, this session |
| The diagnostic workflow already fetches `/api/health/services` unauthenticated | `.github/workflows/engine-diagnostic.yml:865` |
| BASELINES recorded in the committed mirror programme §0 (not re-measured here): per-market snapshot fresh-complete share 0; net drift p90 0.4531 whole-book per token; ledger-sourced mapping on 69.4% of readings; 55% of mapped markets carry a negative net; 42.9% of mapped markets on a half-cent grid; M19 needs 31,052 games (±46.6 ROI points at 30 games) | `docs/mirror-to-a-tee-program.md` §0, §4, §6 |

**The one-sentence statement.** The buy lane works and is instrumented only after the row exists; the sell
lane sizes every sale from a denominator that is not this episode's; the short lane is refused everywhere
except two unguarded desk doors; the mirror lane cannot open a book, cannot be stopped without stranding
whatever it holds, and cannot be graded. Nothing in the list above is a theory: each row is a file and a
line in this repository.

---

## 1. THE SYNTHESIS — which plan this is, what was grafted, what was removed, and how the judges' disagreement was decided

Three plans were written and three judges scored them on different criteria. The judges disagreed:
safety ranked *risk-first* 9 / *shortest-safe* 7 / *proof-first* 4; honesty ranked *proof-first* 9 /
*risk-first* 8 / *shortest-safe* 5; buildability ranked *shortest-safe* 8 / *risk-first* 7 /
*proof-first* 5. **This programme takes risk-first's ORDERING, shortest-safe's UNIT SIZING and DELIVERY, and
proof-first's GATES.** The reasons, decided here and not deferred:

**D-A. The base is risk-first's ordering — containment before sight before a new side.** It is the only
ordering derived from the goal rather than checked against it, and two of the three fatal findings in the
field are things the other two plans arm money over: `ms.account_positions`' parse-drop (a *wrong BUY*, not a
missed one) and the abandon path that leaves live rests standing. Both are verified above. Honesty and
buildability are properties a plan can be *given* by grafting; an ordering that places an order before its
guard cannot be repaired by grafting.

**D-B. Unit sizing follows shortest-safe, not risk-first.** Risk-first's 30 units contend on
`live_executor.py` 13 ways and on `ml` 12 ways, and several of its units ("Programme Phase 4 verbatim") are
not one commit or one review. Every unit below is one commit with its tests, and §2 fixes the landing order
inside the two files that several units must touch. Where risk-first and shortest-safe describe the same
change, the brief here is shortest-safe's (it names the file, the conditional, and the two traps that would
make the change wrong) with risk-first's **unreadable contract** clause added — see D-E.

**D-C. Gates follow proof-first, and M19 is never a gate.** §3b of the mirror programme fixes an estimator,
a clustering unit and a minimum n for every metric; two of the three plans quietly widened one. Shortest-safe
authorised a 10× cap widening on "the books closed so far" where §3b requires 30 book-clustered books, and
lowered Phase 5's live short gate to 10 books. Risk-first put M11 (min n = 30 books) on a 1→3→5-book rung.
**Both are removed here.** §3b's table is restated in §3b below with the LINE that supplies each number, and
no live widening step is authorised by a metric below its minimum n. The economic gate M19 is reported with
its interval and n_needed and is **never** an authorising reading — see owner decision 2.

**D-D. The take is disarmed for the whole rollout (graft from shortest-safe).** `MIRROR_TAKE_AFTER_S` is
`min_wait_env` (`rules:190, :259`) and `take_allowed` floors at `max(wait_s, MIRROR_TAKE_AFTER_S)`
(`rules:830, :835`), so a large value is honoured and no caller can shorten it. Setting it to 86400 for the
rollout deletes the entire "unproven venue refusal shape" class from first money for the cost of one
environment variable. Risk-first did not do this; it costs nothing and it is adopted.

**D-E. Every unit carries an "unreadable contract" (graft from risk-first).** One line per unit saying what
the change does when its read fails. It is the question a builder always has to ask, and it is the seam every
defect in §0 lives in. Two contract rules are programme-wide: *a per-market fact may refuse that market and
must never abandon the tick*, and *an instrument that can stop trading is worse than no instrument* — every
census/counter write is wrapped and counted, never in the path of a refusal.

**D-F. The reversal lever is the allowlist; `exits` is the stop; unset is neither.** Verified at HEAD:
`mirror_allowlist()`/`mirror_mode()` read the environment only, so emptying `PMUS_MIRROR_WHALES` resumes the
per-fill lane for that whale in the same deploy **while the book still ticks, reduces and flattens**. That is
strictly better than `PMUS_MIRROR=exits` (which leaves the whale copied by nobody) and infinitely better than
unsetting the variable (which is a strand — §0 row 2). But the reversal re-arms the per-fill lane onto slugs a
mirror book holds, which freezes those books (`ml:441-445` counts only `whale_username='manual'`), so **R7
must land before the first live book or the reversal lever creates unmanaged positions.**

**D-G. `SHORT_PROBATION_N` stays at its shipped 3.** Risk-first's step 9 lowered it to 1. HEAD `le:5602`
stops taking the serialising lock once `done >= N`, so N=1 means the per-fill short lane runs unserialised
across the whole roster after a single verdict — while risk-first's own next step asks for three verdicts.
Shortest-safe's discipline is adopted instead: three 1-share BUY_SHORTs on a slug the account holds nothing
else on, each exited to zero, with **`SELL_SHORT sent = 0`** as an explicit gate clause.

**D-H. The exit-fraction gate is a replay, not a simulation (graft from proof-first).** Risk-first gated E1
on a 20-cycle simulation, which is an assertion about a model. The replay over rows already held is a reading
and it establishes the pre-change baseline the post-change reading is judged against.

**D-I. MIRROREXIT is built before the exit work, not after.** §5 decision 18 says M14's threshold is set from
that reading, and no such reading exists (`grep MIRROREXIT` finds nothing in `mr` or the workflow). It is
zero money and one day, and it is the only thing that can price the exits we do take.

**Fatal flaws removed by construction, each with the unit that owns the fix:**

| removed flaw | where it came from | owned here by |
|---|---|---|
| A short order placed ahead of the unit that guards it (rung 5's resting SELL_SHORT before the collateral preview and the exit denomination) | proof-first's C6/D0 circular dependency | SH1 before V1; V1's short rungs are gated on SH1+SH2 landing |
| Migration 050 assigned to two different units against a register that reserves it for shorts | proof-first B1 vs D1 | §2 numbering register; the census takes 053 |
| Arming live money with `ms.account_positions`' parse-drop open | shortest-safe (no unit owns it) | **R4**, in the first wave |
| An abandon path that leaves live rests standing while money is on | shortest-safe (read as a strength) | **R1**, in the first wave |
| Two money-moving bounds held by a rollout note ("it is not set", "touched by nobody") | shortest-safe | **R2**, in the first wave |
| A live cap widening authorised below §3b's minimum n | shortest-safe S3, risk-first step 8 | §3b + switch-on S6/S7 |
| `SHORT_PROBATION_N=1` | risk-first step 9 | D-G |
| Steps authorised by a venue-rung harness no unit builds; a grade with no delivery path | risk-first | **V1** owns the harness; **A2** owns the delivery |
| An M19 built on `settled_pnl` alone (selection bias published as a 95% interval) | the naive reading of §4 M19 | **A2**, which unions both close paths and labels each row |

---

## 2. COLLISION REGISTER (extends §2 of the mirror programme)

1. **Migration numbers.** §2 rule 3 reserves 048 = Phase 0b, 050 = Phase 5 (shorts), 051 = Phase 6 (game
   claims). 049 exists. This programme claims **052** (mirror integrity + track + the book's first-sight
   columns), **053** (copy census), **054** (exit retry), **055** (the settlement claim by lane). Each is
   claimed by exactly ONE unit — A1, B2, E4, X2 respectively — and no unit may claim 048/050/051 for anything
   but the phase they are reserved for. (One source plan assigned 050 to two different units against this
   register; that is the class of collision this rule exists to stop.)
   `047` stays CREATE-only (`tests/test_mirror_live_migration.py`); 049 stays an ALTER.
2. **Files the in-flight wave owns** — `live_executor.py`, `api/app.py`, `api/track_record.py`,
   `api/copies_record.py`, `workers/whale_exits.py`, `workers/roster_auto.py`, `analytics/roster_rules.py`,
   `ingestion/*`. Every unit that touches one is marked **WAVE-BLOCKED** and must be built on the wave's tree,
   never on HEAD. If the wave slips, ask it to carry E1's three-field payload change rather than opening a
   second editor on the same file.
3. **Landing order inside `workers/mirror_live.py`** (six units touch it; each owns a disjoint region and its
   own NEW test file, but they must land in this order to avoid a rebase chain):
   **R1** (`_tick`'s three returns, the SAFE branch) → **R7** (`_SQL_MANUAL_SHARES`, `_read_market`) →
   **R3** (`_flatten_vanished`) → **R5** (`_book_delta`, the trip sites, `_post_only_enabled`) →
   **R6** (the per-tick cash read) → **A1** (`_SQL_ORDER_INSERT`, `_SQL_ORDER_STATE`, `_trip_live_off`) →
   **P1** (`_read_market`'s per-market call, the two `drift_rule` call sites).
4. **Landing order inside `workers/mirror_shadow.py`**: **R4** (`account_positions`) → **R2** (the env block)
   → **A4** (the exit-leg summary) → **P2** (`map_market`) → **P3** (`compute_ratio`).
5. **New test files** (all verified absent today, so they are free to claim, and they remove the five-way
   collision on `tests/test_mirror_live_worker.py`): `test_mirror_live_safe_mode.py` (R1),
   `test_mirror_env_bounds.py` (R2), `test_mirror_live_flatten.py` (R3), `test_mirror_account_positions.py`
   (R4), `test_mirror_live_overspend.py` (R5), `test_mirror_cash.py` (R6), `test_mirror_live_freeze.py` (R7),
   `test_mirror_grade.py` (A2), `test_mirror_admin.py` (X3), `test_mirror_probe.py` (V1).
6. **`parallel_safe_with_step9`** as defined in §2 rule 1 still governs: additive kwargs with defaults are
   safe; arity or return-shape changes are not. P1 is FALSE by that rule (the worker's step B changes what it
   reads); every other mirror-lane unit here is TRUE.

---

## 3. THE UNITS, IN BUILD ORDER

Each unit is **one commit with its tests**. "Can start now" means no file it owns is in the dirty set above.

**THE FIRST WAVE — start these eight today, in this order** (§2 rule 3 fixes the order inside
`workers/mirror_live.py`; §2 rule 4 inside `workers/mirror_shadow.py`; §2 rule 5 gives each its own new test
file so they do not collide on `test_mirror_live_worker.py`):

> **R1** (stop-is-not-strand) → **R7** (explain every non-mirror share) → **R3** (bound the whole-slug close)
> → **R5** (the three fail-open seams) → **R6** (cash on the money path), with **R4** (an unreadable row makes
> the walk unreadable) and **R2** (no shell lever loosens a money bound) landing in parallel on
> `mirror_shadow.py`, plus **A1** (the storage whose window cannot be recovered) and **SH1** (the collateral
> preview and the market tick, in `pmus.py`, which blocks every short anywhere).

Every one of the eight is a mirror-lane or venue-module file, every one is independently landable, and none
of them changes what the system does at the venue: after all eight, the mirror is still SAFE and the per-fill
lane still copies both whales exactly as it does today. **A2, A3, P1, P2, P3 and X2 are also unblocked** and
follow immediately behind on the same files; they are not in the first wave only because §2's landing order
puts them after a rebase.

### WAVE R — CONTAINMENT. Every wrong-order and strand path closed and named before anything arms.

#### R1 — the mirror's stop is not a strand
**Files:** `workers/mirror_live.py` (`_tick`'s three abandon returns, the MODE_SAFE branch);
`backend/tests/test_mirror_live_safe_mode.py` (NEW). **Depends on:** nothing. **Can start now: YES.**

**Builds.** Three changes. (a) `_tick` returns on `positions_unreadable`, `open_orders_unreadable` and
`protected_ids_unreadable` at `ml:2674 / :2678 / :2682`, all ABOVE `await _reconcile_orders(t)` at `ml:2685`,
and `_abandon` then sets a process backoff — so on any of those, no fill is booked, no terminal state is
written, no TTL expiry is cancelled, and nothing is cancelled, while resting orders stay live at the venue.
Move `await _reconcile_orders(t, count=False)` above each of the three returns. The precedent is in the same
function: the MODE_SAFE branch (`ml:2660`) and the tripped-guard branch (`ml:2665`) already reconcile first.
Where the open-orders read itself is the unreadable one, cancel by the protected-id set and name
`reconcile_skipped`. (b) Hoist the `mirror_flatten` lever (`_STATE_FLATTEN`, `ml:132`, read at `ml:697` inside
`_global_guards`) so it is read ABOVE the SAFE return — today the one mode in which nothing else can reduce a
book is also the one mode the operator's flatten cannot reach. (c) In the SAFE branch, count books with
`state <> 'closed'` and non-zero `ledger_net`; a non-zero count emits `_mirror_stop("books_open_in_safe")`,
degrades the heartbeat and writes a durable receipt in the `_STATE_LOSS_STOP` shape. **Never widen SAFE to
sell on its own** — SAFE reduces only on the operator's explicit flatten.
**Unreadable contract.** Books count unreadable ⇒ treat as books open (alarm on). Flatten key unreadable ⇒
no flatten. Open-orders unreadable ⇒ cancel the protected set, name `reconcile_skipped`, still abandon.
**Tests.** `test_an_unreadable_positions_walk_still_cancels_and_books`;
`test_an_unreadable_open_orders_read_names_reconcile_skipped`; `test_safe_mode_alarms_when_a_book_is_open`;
`test_safe_mode_reads_the_flatten_lever`; `test_an_unreadable_flatten_key_is_not_a_flatten`;
`test_safe_mode_places_no_buy`; the existing 429-storm abandon invariant stays green.
**Gate.** Fault-injection tick with the positions walk raising: `orders_left_standing = 0` and
`orders_reconciled` equals the count of non-terminal mirror orders, asserted as counters in the test; the
abandon still happens. On the beat, the line `MIRRORLIVE` prints `books_open_in_safe`; **passes at 0**, and
any non-zero is a reversing reading in the switch-on sequence.

#### R2 — no money bound can be loosened from a shell
**Files:** `workers/mirror_shadow.py` (the env block, `ms:74-109`); `workers/mirror_live.py`
(`_SQL_REPLACES` at `ml:367-370`, `_post_only_enabled` at `ml:271-274`);
`backend/tests/test_mirror_env_bounds.py` (NEW); `backend/tests/test_mirror_shadow.py`.
**Depends on:** nothing. **Can start now: YES.**

**Builds.** (a) Seven MIRROR_* knobs are raw `os.environ.get` while every dollar cap goes through
`capped_env` (`rules:162`, downward-only). The dangerous one is `MIRROR_SNAP_MAX_AGE_S` (`ms:84`), the
freshness gate on HIS position: it is consumed at `ml:1556` into `fresh_read` and from there into
`AdmissionFacts.snap_fresh` (admission's `snapshot_stale`, `rules:501`), into `drift_rule`'s `increase_ok`,
and into `_snap_of` returning a number instead of None — so raising it from a shell opens new books and grows
live ones against an arbitrarily old picture of the whale, and it can loosen the very gate the rollout is
steered by. Route it through `capped_env("MIRROR_SNAP_MAX_AGE_S", 300.0, floor=30.0)` and route
`MIRROR_SHADOW_POLL_S`, `MIRROR_LOOKBACK_H`, `MIRROR_RATIO_DAYS`, `MIRROR_MAX_MARKETS`, `MIRROR_JUDGE_TTL_S`
through the same discipline. (b) `PMUS_MIRROR_POST_ONLY` (`ml:272`) becomes one-way: `on` is the only accepted
value from the environment. With post-only off every mirror rest is a plain crossable GTD limit that can fill
at placement in a locked book, which defeats `at_or_through`, `TAKE_ARM_STALE_WAITS`, the 120 s take floor and
`P2_MAKER_SHARE_MIN` in one variable. (c) `_SQL_REPLACES` counts `reason = 'replace'` only, while a TTL expiry
is cancelled under the name `ttl` and re-placed next tick — so *tightening* `MIRROR_REST_TTL_S` raises the
venue write rate. Count `reason IN ('replace','ttl')` and floor the TTL at
`3600 / MIRROR_MAX_REPLACES_PER_HOUR` so the two knobs cannot contradict each other.
**Unreadable contract.** An unparseable env value is the code default, logged once, never the env value.
**Tests.** `test_every_mirror_env_knob_is_downward_only` (table-driven, each name at a huge value and a tiny
one); `test_snap_max_age_has_a_floor`; `test_post_only_cannot_be_turned_off_from_the_environment`;
`test_the_replace_budget_counts_ttl_cancels`; `test_the_ttl_floor_follows_the_replace_budget`.
**Gate.** Deterministic: the sweep passes for all seven names — for each, the resolved value with a large env
value equals the code default. On the beat, `MIRRORLIVE` prints `replaces_used` and `ttl_cancels`; **passes
when `replaces_used + ttl_cancels <= MIRROR_MAX_REPLACES_PER_HOUR` per book per hour at every legal TTL.**

#### R3 — the whole-slug close is bounded
**Files:** `workers/mirror_live.py` (`_flatten_vanished`, `ml:2385-2429`);
`backend/tests/test_mirror_live_flatten.py` (NEW). **Depends on:** R7 (for the third clause).
**Can start now: YES** (the `ceil` and two-reads halves land alone; the third clause rebases on R7).

**Builds.** `ml:2390 sole = ledger >= int(held)` decides, from ONE `le._pm_held` read, whether to call
`t.pmus.close_position(slug, slippage_bips=...)` at `ml:2427` — a whole-slug close, and the only order the
worker sends with no clamp to its own book (every rest path clamps at `ml:2100`). `int()` truncates, so a
foreign holding of a fraction under one share defeats the test **deterministically, with no race**. Three
changes: use `math.ceil(held)`; require TWO agreeing `_pm_held` reads before taking the sole branch (the
discipline `_cancel_and_settle` already applies to cancels, `ml:1450-1470`); and refuse the sole branch while
any NON-MIRROR `live_orders` row on that slug is filled or in flight — R7's widened query answers it.
**Unreadable contract.** Either read failing, the two disagreeing, or the non-mirror query failing ⇒
`no_bid_for_flatten` (the existing name), retry next tick, never `close_position`. `EXIT_SLIPPAGE_BIPS` is not
touched; the paired-rest path is not touched.
**Tests.** `test_a_fractional_foreign_share_is_not_sole`; `test_two_disagreeing_held_reads_refuse`;
`test_a_live_per_fill_row_on_the_slug_refuses_the_sole_branch`; the existing `select_flatten` truth table
unchanged.
**Gate.** Deterministic on fixtures, then live: the census counter `close_position_with_foreign_shares`
**passes at exactly 0** over every closed book.

#### R4 — an unreadable row makes the walk unreadable
**Files:** `workers/mirror_shadow.py` (`account_positions`, `ms:346-377`);
`backend/tests/test_mirror_account_positions.py` (NEW). **Depends on:** R1 (so the abandon it triggers has
already been made safe). **Can start now: YES.**

**Builds.** This is the one seam in the mirror's otherwise fail-closed census that lets a **wrong order** out
today. `account_positions` promises that a market absent from a successful walk is simply not held, and
upholds that magnificently for a truncated page (`ms:372`, "A WALK THAT HIT THE PAGE CAP IS NOT A READING OF
THE ACCOUNT", then a raise) — but the loop body at `ms:365-366` does `except (TypeError, ValueError):
continue`, so one unparseable `netPosition` drops the slug from a dict the caller is told is complete.
Downstream, in order: `_read_market` reads `venue = (t.positions or {}).get(slug.lower(), 0.0)` → 0.0
(`ml:1569`); admission's `if vn != 0.0: return "venue_already_holds"` (`rules:490-491`) passes; the plan sees
venue 0 against ledger 0, they agree; and `_place` sends a BUY into a slug the account already holds. The next
readable tick freezes the book, but the order is at the venue. Count parse failures and rows with a missing or
empty slug; any count above zero raises the same `RuntimeError` the page cap raises. Also emit
`positions_walk_at_cap` on the beat when the walk reaches `POSITIONS_PAGES_MAX` — a page ceiling on an account
shared with the per-fill lane and the desk. The correct stance is one branch away in the same function.
**Unreadable contract.** Any row we cannot read makes the whole walk unreadable ⇒ None ⇒ abandon, which after
R1 has already cancelled and booked.
**Tests.** `test_an_unparseable_netposition_makes_the_walk_unreadable`;
`test_a_slugless_row_makes_the_walk_unreadable`; `test_the_page_cap_still_raises`;
`test_a_clean_walk_is_still_complete`.
**Gate.** Fault injection: one bad row in a multi-page walk yields `positions_unreadable` on the census and
**0 orders placed that tick**. On the beat, `positions_walk_at_cap` **passes at 0**; any non-zero blocks
raising the book count.

#### R5 — the mirror's three fail-open seams
**Files:** `workers/mirror_live.py` (`_book_delta` at `ml:1291-1310`, the `_POST_ONLY_OK` latch at `ml:185 /
:2288`, `_instruments`' `reaper_touched_mirror`, `_read_market`'s desk read); `analytics/mirror_live_rules.py`
(`book_buy`'s contract only); `backend/tests/test_mirror_live_overspend.py` (NEW).
**Depends on:** R1. **Can start now: YES.**

**Builds.** (a) **Mirror overspend.** The per-fill lane's hardest-won breaker halts the sleeve when the venue
charges more than we asked; the mirror READS that halt and never WRITES one — `rules.book_buy` accepts any
finite price in (0,1) and is never handed the order's wire, so a rest that fills above its cent inflates
`avg_cost`, `gross_buy_usd` and the day spend with no detector, and §4 M10's `at_or_better = 1.00` invariant
has no instrument at all. Add the comparison in `_book_delta`, which holds `o["wire"]` and is the single
booking entry point for all three fill paths: for a BUY, `avg_px > wire + tick/2 + 1e-4` ⇒ `_freeze` and
`_trip_live_off("mirror_overspend")`, the same call `overfill` and `wrong_sign_trip` already make. **Two cases
or the change is worse than nothing:** `_place`'s fill-at-create path calls `_book_delta` BEFORE the post-only
latch runs (`ml:2281-2283` then `:2288`), and a post-only order the venue crossed anyway is precisely the fill
most likely to be above the wire — so the check must run on that call; and a CLOSE row's wire is deliberately
`0.0`, not None, so `avg_px > wire` is true for EVERY vanish flatten — exempt `tif == "CLOSE"` by name, with
the reason in the code. (b) **The post-only latch** (`ml:185`, flipped at `ml:2288`) is a one-way
process-global reached with no operator involved: one venue fill-at-create turns post-only off for every
subsequent order in the process, and because no post-only rejection can then fire, `take_arms` never arms and
the bounded-take construction goes dark with it. Make `post_only_ignored` a durable increase block written the
way `_STATE_LOSS_STOP` already is (`ml:749-753`) — reduce-and-flatten only until an operator clears it.
(c) **`reaper_touched_mirror`** (a copy-lane reaper touching a MIRROR order — the thing migration 047 was
structured to make impossible, and §4 M15's target 0) today only degrades the beat; make it
`_trip_live_off("reaper_touched_mirror")`. (d) An unreadable desk-shares read is swallowed as `manual = 0.0`
where every other unreadable fact in the file is a named refusal — freeze `venue_ledger_unreadable` by name
(the query itself is R7's).
**Unreadable contract.** `avg_px` or `wire` unreadable ⇒ book at the wire (today's fallback) and count
`overspend_uncheckable`; do not trip on an absent number and do not hide it.
**Tests.** `test_a_fill_above_the_wire_trips_and_freezes`;
`test_a_fill_at_create_above_the_wire_trips_before_the_post_only_latch`;
`test_a_close_row_never_trips_overspend`; `test_an_unreadable_avg_px_counts_but_does_not_trip`;
`test_post_only_off_blocks_increases_and_survives_a_restart`; `test_reaper_touched_mirror_trips`;
`test_an_unreadable_desk_read_freezes_by_name`; `book_buy`'s existing refusal list unchanged.
**Gate.** `at_or_better` becomes COMPUTABLE and is printed on `MIRRORGRADE`; **passes at exactly 1.00** on
every booked mirror fill. `mirror_overspend`, `post_only_ignored`, `reaper_touched_mirror` on
`MIRRORINTEG` **pass at 0** over the first 7 live days; any non-zero is a stop, not a warning.

#### R6 — buying power on the money path
**Files:** `api/pmus_account.py` (a pure `cash_room(bal, reserved)` beside the existing read);
`analytics/mirror_live_rules.py` (`AdmissionFacts.cash_room`, the `insufficient_cash` clause, `room_scale`
taking one more room); `workers/mirror_live.py` (the per-tick read, the within-tick decrement);
`backend/tests/test_mirror_cash.py` (NEW); `backend/tests/test_mirror_live_rules.py`.
**Depends on:** R1. **Can start now: YES.**

**Builds.** The only enumerated failure mode with no bound at all: buying power is read by a display snapshot
and by no money path, `AdmissionFacts` has no cash field, and three lanes place into one account. Build a pure
`cash_room(bal: dict | None, reserved: float) -> float | None` — None on anything not finite, else
`max(0.0, buyingPower - reserved)`. The worker reads balances once per tick behind the pacer into
`t.cash_room = cash_room(bal, le._REST_RESERVED_USD)` and passes it to `rules.room_scale` as one more room:
that function's existing contract already returns 0 shares on any room it cannot read as a finite number
(`rules:656-661`), so an unreadable balance becomes NO room rather than unlimited room — one argument to a
pure function with an existing fail-closed contract. Add the admission clause `insufficient_cash` and its
census key, because today an unfundable order returns `place_refused:<status>` and the tick census folds it
under the bare family `place_refused` (`ml:167-168`), so "no funds" and "bad tick size" are the same operator
signal and the distinguishing detail lives only in a bounded in-memory dict. Decrement `t.cash_room` by each
placement's estimate in the same block that already decrements `day_room`/`total_room`/`mirror_day`
(`ml:2273-2278`). **This unit READS a bound; it does not set `MIRROR_BANKROLL_USD`** (owner decision 1, P4).
**Unreadable contract.** An unreadable balances read ⇒ `cash_room = None` ⇒ `room_scale` returns 0 ⇒ no
increase, refused `insufficient_cash`, counted `cash_unreadable`. Never "unlimited". A raising read never
abandons the tick.
**Tests.** `test_cash_room_is_buying_power_minus_reservations`;
`test_an_unreadable_balance_is_no_room_by_name`; `test_insufficient_cash_is_an_admission_clause`;
`test_room_scale_takes_cash_as_one_more_room`; `test_a_raising_balances_read_does_not_abandon_the_tick`.
**Gate.** `MIRRORCASH` prints `cash_room` and `cash_unreadable` every tick. **Passes when venue funds
rejections over the first 7 live days = 0 with `insufficient_cash` naming them first (§4 M27), and
`cash_unreadable <= 1% of ticks.**

#### R7 — every non-mirror share on the slug is explained
**Files:** `workers/mirror_live.py` (`_SQL_MANUAL_SHARES` at `ml:441-445`, its `_read_market` caller);
`backend/tests/test_mirror_live_freeze.py` (NEW). **Depends on:** nothing. **Can start now: YES.**

**Builds.** `_SQL_MANUAL_SHARES` counts only rows with `COALESCE(whale_username,'') = 'manual'` (read this
session at `ml:443`), so a per-fill copy row from ANY whale on a market a mirror book holds is *unexplained*
and the venue-vs-ledger freeze fires. A frozen book cancels its rest each tick and writes `kind:'frozen'`; it
does not reduce and does not flatten; `_thaw` is automatic-only and requires the other lane to sell; there is
no admin unfreeze. **This is the reversal path, not an edge case:** per D-F the rollback lever resumes the
per-fill lane on the mirrored whale in the same deploy, and its next copy on a market a mirror book holds
would freeze that book into an unmanaged position. Widen the query to every non-mirror `live_orders` row on
the slug (`COALESCE(lane,'') <> 'mirror'`, `status IN ('filled','submitting','exiting')`) so those shares are
EXPLAINED rather than freezing; keep the freeze for genuinely unexplained divergence; and feed the same query
to R3's sole-holder clause.
**Unreadable contract.** The query unreadable ⇒ `manual = None` ⇒ freeze by the name
`venue_ledger_unreadable` (today it is swallowed as 0.0).
**Tests.** `test_a_per_fill_row_on_the_slug_explains_its_shares`;
`test_an_unexplained_venue_share_still_freezes`; `test_an_unreadable_desk_read_freezes_by_name`;
`test_a_frozen_book_reduces_again_once_the_explained_shares_clear`.
**Gate.** Deterministic: in the cross-lane fixture, 0 freezes from an explained share. Live: `MIRRORINTEG`
`frozen_ticks / book_ticks` **passes below 0.01** (§4 M15) and `venue_ledger_unreadable` **passes at 0**.

### WAVE A — THE INSTRUMENTS. What cannot be measured retroactively lands before the first live book.

#### A1 — fidelity storage and durable counters
**Files:** `workers/mirror_live.py` (`_SQL_ORDER_INSERT`, `_SQL_ORDER_STATE`, `_trip_live_off`,
`_instruments`); `backend/migrations/052_mirror_integrity.sql` (NEW);
`backend/tests/test_mirror_live_migration.py`; `backend/tests/test_mirror_live_worker.py`.
**Depends on:** nothing. **Can start now: YES. MUST land before the first live book.**

**Builds.** Four windows close forever the moment books run. (a) Migration 049 already adds
`mirror_orders.trigger_trade_id`, `.his_fill_ts`, `.first_fill_at` (`049:32,37,42`) and grep finds them ONLY
in the DDL and its migration test — `_SQL_ORDER_INSERT` does not name them and `_SQL_ORDER_STATE` never sets
`first_fill_at`. So §4 M11, the headline Phase 4 gate in §3b, has no data even once live. Write all three:
the trigger trade id and his fill timestamp at insert from the wake that produced the plan (NULL, with
`wake_source` in {poll, cursor, none}, when there was none), and `first_fill_at = COALESCE(first_fill_at,
now())` on the first transition to a non-zero filled quantity. (b) M15's counters have no durable cumulative
store: `_new_stats()` is rebuilt per tick, `heartbeat()` overwrites its row, the census is a bounded
per-process dict, and `_trip_live_off` writes ONE ingestion_state key so a second trip erases the first.
052 creates `mirror_integrity(name TEXT PRIMARY KEY, n BIGINT NOT NULL DEFAULT 0, first_at, last_at,
last_detail JSONB)` and every M15 name is UPSERTed cumulatively: `wrong_sign_trip`, `order_lost`, `overfill`,
`reaper_touched_mirror`, `book_settle_disagree`, `shadow_live_disagree`, `frozen_ticks`,
`post_only_ignored`, `books_open_in_safe`, `mirror_overspend`, `settle_uncheckable`,
`close_position_with_foreign_shares`, `insufficient_cash`. (c) M6 and M28 need per-(book, tick) history that
047 does not create: 052 adds `mirror_track(book_id, at, ledger_net, target, target_flow, frozen, dead_band)`
written once per book per tick, with a 30-day retention delete in the same statement so it is bounded.
(d) Add `settle_uncheckable` beside `book_settle_disagree`: `_close_settled` leaves `settle_disagree` NULL
when the market, the payout or the average cost could not be read (`ml:1703-1721`), and **NULL is not False**
— a cohort in which no book could be cross-checked must not score like a cohort that agreed to the cent.
**Unreadable contract.** A failed instrument write must never fail a tick: each in its own try/except,
counted `instrument_write_failed`, continue.
**Tests.** `test_052_is_create_only_and_047_049_unchanged`;
`test_order_insert_names_the_trigger_columns`; `test_first_fill_at_is_set_once_and_never_moved`;
`test_a_second_trip_increments_rather_than_overwrites`; `test_track_is_written_per_book_per_tick`;
`test_an_unreadable_payout_counts_settle_uncheckable_and_leaves_disagree_null`;
`test_an_instrument_write_failure_does_not_stop_the_tick`.
**Gate.** Deterministic on a synthetic 100-tick run, then on the first 30 mirror orders:
`MIRRORINTEG` prints every M15 name (present at n=0 before any book); `MIRRORFIDELITY` prints
`trigger_trade_id_share` — **passes at >= 0.85**, the wake-source target §4 M11/Phase 4 sets — and
`first_fill_at_share`, **passes at 1.00 of filled orders**; `mirror_track` rows = books × ticks.

#### A2 — the grade, both close paths, and its delivery
**Files:** `backend/sportsassets/analytics/mirror_grade.py` (NEW — the whole computation);
`workers/mirror_live.py` (the heartbeat fold only: `stats["grade"]`);
`.github/workflows/engine-diagnostic.yml` (new `MIRRORLIVE` / `MIRRORGRADE` / `MIRRORINTEG` jq lines);
`backend/tests/test_mirror_grade.py` (NEW). **Depends on:** A1. **Can start now: YES.**

**Builds.** There is no proof instrument for the mirror at all (§0). Build a pure module over
`mirror_books` × `mirror_orders` × `mirror_integrity` × `mirror_track` computing: books live and opened
today; ledger vs venue; §4 M10 (`filled_rest/placed_rest`, `at_or_better`, `maker_share`); M11 (react p50/p90
and `by_usd` from A1's columns); M15 from `mirror_integrity`; M16 (settlement gap by class) **with
`settle_uncheckable` printed as its denominator**; and M19 = `proof.roi_with_ci` on `peak_exposure_usd`,
game-clustered, over closed books.
**THE CLAUSE THAT MUST NOT BE GOT WRONG.** `settled_pnl` is written only inside `if status == "settled":`
(`ml:1703-1706`); the other close path (`_maybe_close_episode` → `_close_mirror_episode` → `_SQL_BOOK_STATE`)
sets `state='closed'` and writes no `settled_pnl`. So **every book that EXITS BEFORE RESOLUTION — the
methodology's own exit, the cohort the owner is asking to copy — has `settled_pnl` NULL forever.** An M19
built on that column alone silently drops that cohort and publishes a selection bias as a 95% interval, which
is worse than having no M19. UNION both paths (`realized_pnl` for exited books, `settled_pnl` for resolved),
print `n_exited` and `n_resolved` separately, and label per row which column was used. A book with neither is
`grade_unreadable`, excluded and counted. `peak_exposure_usd` is already selected and already read by
`rules.book_buy` — what is missing is a grading reader, not a SELECT.
**Delivery without touching `api/app.py`.** The worker folds the result into its heartbeat detail; the
workflow's new jq lines read `/api/health/services`, which the workflow already fetches unauthenticated
(`engine-diagnostic.yml:865`). No route change, so this unit is not wave-blocked.
**Unreadable contract.** A metric whose inputs are absent prints `unreadable:<name>`, never 0 — the shape
`p2_verdict` already returns.
**Tests.** `test_m19_unions_both_close_paths_and_labels_the_source`;
`test_an_exited_book_is_in_the_cohort`; `test_settle_uncheckable_is_m16s_denominator`;
`test_an_empty_cohort_prints_n_zero_and_never_an_interval`;
`test_grade_unreadable_is_counted_not_dropped`; `test_every_jq_line_parses_on_an_absent_payload`.
**Gate.** Deterministic on a seeded fixture of 30 closed books (half exited, half resolved): `MIRRORGRADE`
prints `n_books`, `n_games`, `n_exited`, `n_resolved`, `ci_lo`, `n_needed_at_target`, and M16 with its
checkable denominator. **Passes when every line prints and the cohort's `n_exited + n_resolved +
grade_unreadable = n_books`.** M19's lower bound is REPORTED and is never an authorising reading (§3b, owner
decision 2).

#### A3 — MIRROREXIT: the shadow's exit leg
**Files:** `analytics/mirror_report.py`; `workers/mirror_shadow.py` (the SELL-side judge);
`.github/workflows/engine-diagnostic.yml`; `backend/tests/test_mirror_report.py`;
`backend/tests/test_mirror_shadow.py`. **Depends on:** nothing (land after R4/R2 per §2 rule 4).
**Can start now: YES.**

**Builds.** Phase 0c's `exit_leg` block, verified absent (`grep MIRROREXIT` and `grep exit_leg` over
`sportsassets/` and the workflow return nothing). For every 046 shadow row whose plan is a SELL (a reduction
or a flatten), judge would-fill against the SELL side over the same TTL the BUY judge uses, and record
time-to-touch. `summarize` gains an `exit_leg` block: would-fill by family and phase with a MARKET-clustered
rate and 95% lower bound, `reduce_time_to_touch` p50/p90, and the dollar-weighted share of SELL plans that
would not fill inside one TTL. **This is first among the exit work because §5 decision 18 sets §4 M14's
threshold from this reading and no such reading exists** — M14 gates every reduction the mirror will ever
place and sets the interim threshold for the per-fill exit price.
**Unreadable contract.** The shadow never touches an order; a row it cannot judge is counted
`exit_unjudged`, never counted as a fill or a miss.
**Tests.** `test_exit_leg_judges_the_sell_side_over_the_same_ttl`;
`test_exit_leg_clusters_by_market_not_by_plan`; `test_exit_unjudged_is_counted`;
`test_the_shadow_never_touches_an_order` stays green; every new jq line parses on an empty endpoint.
**Gate.** 24 h shadow: `MIRROREXIT` prints SELL would-fill with a market-clustered 95% lower bound over
**>= 30 markets** and time-to-touch p50/p90 by family. **The gate is that the line prints at n >= 30** — the
VALUE is an input to owner decision 18, not a threshold this programme sets.

### WAVE P — THE READING. The mirror's own view of him, still zero money.

#### P1 — the per-market position read (mirror programme Phase 1, wired)
**Files:** `workers/mirror_live.py` (`_read_market`, the two `drift_rule` call sites at `ml:1826` and
`ml:2496`); `analytics/mirror_live_rules.py` (admission's `snapshot_stale` clause only, `rules:501`);
`backend/tests/test_mirror_live_worker.py`; `backend/tests/test_mirror_live_rules.py`.
**READS BUT DOES NOT EDIT** `wx.market_positions` — the function exists (`wx:524`), returns both tokens, and
is already tested, so this unit needs no edit in the wave's file. **Depends on:** R1, R2, R4.
**Can start now: YES.**

**Builds.** The single reason the mirror opens no book. `_read_market` makes ONE paced `market_positions`
call per book and per candidate per tick, charged to the venue read budget. It sets
`AdmissionFacts.snap_market_fresh = True` only when BOTH tokens came back for THAT market and the read is
fresh; anything else leaves it None, and `rules:501` (`if f.snap_fresh is not True and f.snap_market_fresh is
not True`) then refuses `snapshot_stale` exactly as today — the fail-closed default is already written.
`AdmissionFacts.drift` comes from `drift_net_rule(his_long, his_other, snap_long, snap_other)` (`rules:1212`,
called by nothing today) with per-token `drift_rule` as the fallback and `drift_src` in {market, book}
recorded: §0 of the mirror programme records that merged pair legs are a large share of his shares, and a
merged pair reads per-token drift 1.0 — which locks increases out of that market for the life of the book —
against a net reading of 0.
**THE CLAUSE THAT MUST LAND IN THE SAME CHANGE.** `last_fresh_agreed=True` is a hard-coded literal at BOTH
call sites (`ml:1826`, `ml:2496`), while `rules:1188` says the WORKER must assert it and `rules:1204` makes
the default the SMALLER of two disagreeing readings. It is inert today only because `_net_for` consults
`drift.reduce_from` when a snapshot exists — and this unit is exactly what makes a snapshot exist. Make it a
real read: True only when the per-market net and the fills-derived net agree within one share, else False.
Wiring Phase 1 without this silently turns "sell down to the smaller of two disagreeing readings" into
"keep the larger".
**Unreadable contract.** A None or raising read refuses THAT MARKET by the new census name
`snap_market_unreadable` and NEVER abandons the tick; `snap_market_fresh` stays None, `snap_net` stays None,
`last_fresh_agreed` is False.
**Tests.** `test_read_market_calls_market_positions_once_per_book_per_tick`;
`test_snap_market_fresh_is_set_only_on_a_fresh_complete_read`;
`test_an_unreadable_market_read_refuses_that_market_and_not_the_tick`;
`test_drift_is_the_net_rule_so_a_merged_pair_reads_zero`;
`test_last_fresh_agreed_is_never_a_literal` (an `ast` walk asserting no `Constant(True)` at either call site
— the shape that let this survive); `test_a_book_past_the_read_budget_refuses_rather_than_reads`; the
existing `snapshot_stale` pins stay green.
**Gate.** 24 h shadow over **>= 30 planned markets** (§3b M4/M5: share and nearest-rank p90, cluster =
market, min n = 30 markets AND 24 h): `MIRRORSNAP` field `fresh_complete_share` **passes at >= 0.95**
(§0 baseline records 0); `MIRRORSNAP` field `net_drift_p90` **passes at <= 0.05** (§0 baseline records
0.4531 whole-book per token); `snapshot_stale` on candidates **< 5% of ticks**; `snap_market_unreadable`
**<= 5% of market-ticks**; tick abandons attributable to this read **= 0**. **This gate is the precondition
of every arming step below.**

#### P2 — the mapping class the quarantine admits (Phase 2)
**Files:** `workers/mirror_shadow.py` (`map_market`); `workers/premap.py`; `copy_sports.py`;
`backend/tests/test_mirror_shadow.py`; `backend/tests/test_premap*.py`;
`backend/tests/test_copy_sports*.py`; `backend/tests/test_tennis_league_gate.py`.
**Depends on:** nothing structurally; land after A3 per §2 rule 4. **Can start now: YES.**

**Builds.** Phase 2 as the mirror programme writes it (the phantom-line fix, the yes/no identity branch, the
exact lane inside `map_market` behind its own TTL and `MIRROR_MAP_READS_PER_TICK=12`, the grammar moved to
`copy_sports`, `market_type_of`), with ONE framing correction the programme's prose does not carry:
**`map_market` PREFERS the class the quarantine refuses.** `if cands: return _choose_long(assets, cands, pos,
'ledger')` sits BEFORE the premap block, and the ledger query is the newest `live_orders` row per token with
no status or error filter — so under the quarantine the mapper chooses the refusal on exactly the markets we
have traded before and never asks premap for a second opinion on them. The fix is therefore **prefer the
admissible source**, not merely filter the ledger: try the exact lane, then premap, then a filtered ledger
(`status <> 'rejected' AND error NOT LIKE 'quarantined%'`) carrying the row's class. The exact lane must call
the SAME `pmus.resolve_market_exact` the copy lane labels `exact`, pinned by an `inspect.getsource` test —
without that pin the label is class laundering. Mapping reads count against the key-wide venue budget, never
against the tick's market slots; unmapped markets cost no slot. `map_market(pool, fills, pmus=None)` keeps its
two-argument form so the in-flight worker contract is untouched (§2 rule 1).
**BEFORE STARTING, READ TWO SWITCHES.** Whether the quarantine binds at all is a function of the
`LIVE_MAPPING_QUARANTINE` env value and the `ingestion_state.mapping_quarantine` row. Both are **unmeasured**
here. If the quarantine is off, this unit's payoff is smaller and it moves behind P3.
**Unreadable contract.** exact → premap → filtered ledger → no map, counted by step. An unreadable quarantine
key means quarantine ON.
**Tests.** `test_map_market_exact_hits_come_only_from_resolve_market_exact` (source inspection);
`test_the_admissible_source_is_preferred_over_the_ledger`;
`test_a_rejected_or_quarantined_ledger_row_is_not_a_candidate`;
`test_unmapped_markets_cost_no_slot_and_no_read`;
`test_admissible_src_equals_le_quarantine_resume_src`; the two-arg form byte-identical.
**Gate.** 24 h shadow over **>= 30 mapped markets** (§3b M3, dollar-weighted share, cluster = market):
`MIRRORSRC` field `admissible_share` **passes at >= 0.90** (§0 baseline records ledger-sourced on 69.4% of
readings); `MIRRORCOVER-CELL` field `listed_unmapped_share` **passes at <= 0.20 by markets AND by usd**;
`map_reads_per_tick` **<= 12**.

#### P3 — the bankroll ratio measured, with Rule LE's flow target beside it (Phase 3a)
**Files:** `analytics/mirror.py` (`ratio_bankroll`, `flow_net`, `pre_existing_ratchet`, `vwap_since`);
`workers/mirror_shadow.py` (`compute_ratio`'s second query, `MIRROR_SHADOW_RATIO_MODE`, `target_flow`);
`analytics/mirror_report.py`; `backend/tests/test_mirror.py`; `backend/tests/test_mirror_shadow.py`.
**Depends on:** P2 (the admitted set). **Can start now: YES.**

**Builds.** Phase 3a as written: `compute_ratio` gains a second query (30-day open cost on unresolved
markets, `deployed_usd_30d`) and passes `deployed_usd`/`bankroll_usd` into `mi.mirror_ratio`, which today is
called with neither, so `ratio_bankroll` always returns `why='deployed_unreadable'`, the live ratio is the
median-burst ratio which clamps to 1.0, and the per-market cap is the only sizer. `MIRROR_SHADOW_RATIO_MODE`
selects the mode and **both denominators are printed, never substituted** (owner decision 1). Beside the
BYTE-IDENTICAL target, compute `target_flow` under Rule LE — pure `flow_net`, `pre_existing_ratchet`
**PRO-RATA** on reductions (§1 D25 killed the flow-first form: he trims a small fraction and it sells half
our book), `vwap_since` — keyed on the first-sight stamp. Print `MIRRORRATIO` (both denominators),
`MIRRORSHAPE` (M7), and dead-band dollars per r.
**Unreadable contract.** `deployed_usd_30d <= 0` or unreadable ⇒ `ratio_bankroll = None` with `why` named,
never a substituted denominator.
**Tests.** `test_ratio_bankroll_is_bankroll_over_deployed_and_none_on_zero`;
`test_flow_since_first_sight_ratchets_pro_rata_on_reductions`; `test_a_crossing_zeroes_the_block`;
the existing `int()` pins in `test_mirror.py` unchanged.
**Gate.** 24 h shadow in bankroll mode over **>= 30 admitted markets** (§3b Phase 3a row, dollar-weighted
share, cluster = market): `MIRRORSHAPE` field `within_band_share` (ratio in [0.9, 1.1]) **passes at >= 0.95
of admitted dollars** (§4 M7; §0 baseline records 0.34×–52.63×); `MIRRORSHAPE` field `cap_bound_share`
**passes at <= 0.05** (§0 baseline records 68.7–85.1%); `deployed_usd_30d` readable on every hourly refresh.

#### P4 — live sizing: cost-basis cap, reservation, cash room, Rule LE, whale_cut (Phase 3b)
**Files:** `analytics/mirror_live_rules.py`; `workers/mirror_live.py`; `api/pmus_account.py` (the first-sight and
pre-existing-net columns are A1's, in migration 052 — this unit adds NO migration);
**`live_executor.py`** (the mirror reservation lock and the book-INSERT columns) — **WAVE-BLOCKED**. **Depends on:** R6, P3's gate, owner decisions 1, 13, 15, 20.
**Can start now: NO.**

**Builds.** Phase 3b as the mirror programme writes it: `ratio_eff = ratio_bankroll × min(1, clip/50)`;
`cap_usd = min(MIRROR_NET_CAP_USD, bankroll_room)` on a COST basis; a mirror reservation lock and counter
modelled on the copy lane's; `MIRROR_DAY_USD` as a downward handle; Rule LE's `target = ratio_eff × flow_net`
with the pro-rata ratchet; `first_sight_unreadable` (no plan); `late_entry` at NEW-book admission only, never
consuming `opened_today`; and the shadow target switching to flow in the SAME change (§1 D25, else
`shadow_live_disagree` trips on every pre-built book). Two clauses the programme's table does not spell out as
code. **(i) The cross-tick reservation hole:** `_place` correctly decrements this tick's own rooms at
placement (`ml:2273-2278`, with the reason in the code), but the next tick re-reads `_SQL_MIRROR_DAY`, which
sums `cash_usd`, which is written only when a fill BOOKS — so a resting order live at the venue consumes no
room across ticks. Write an `est_usd` at placement and sum `cash_usd + COALESCE(est_usd,0) FILTER (WHERE state
IN ('placing','open'))`. **(ii) `whale_cut` as an admission clause:** the programme names it and `rules` has
none, so today only the env allowlist stops a cut whale on the mirror side while a stored clip map can
override a zero clip.
**Unreadable contract.** Four separate names, none a default: `bankroll_room` unreadable ⇒ 0 room;
`cash_room` unreadable ⇒ 0 room (R6); `pre_existing_net` unreadable ⇒ `first_sight_unreadable`, no plan;
`deployed_usd_30d` unreadable ⇒ no ratio ⇒ no increase.
**CLIPS DO NOT MOVE:** `min(1, clip/50)` saturates at 1.0, so a $50 clip and a $250 clip produce the same
mirror target (owner decision 4).
**Tests.** Phase 3b's list, plus `test_a_resting_order_consumes_day_room_across_ticks`;
`test_a_cut_whale_never_opens_or_increases_a_mirror_book_by_name`.
**Gate.** Live, at the >= 30-book rung: `MIRRORGRADE` field `shape_within_band_share` **passes at >= 0.95 of
his dollars on books held**; the bankroll cap never binds while the sum of books is under the bankroll;
`MIRRORCASH` **0 funds rejections over 24 h** with `insufficient_cash` naming them first;
`mirror_loss_stop` false trips **= 0 over 7 days** at the stop the owner signs.

### WAVE SH — A SIDE. Nothing here places a short until its own guard has landed.
*(Units are SH1-SH5 so they can never be confused with the switch-on steps S0-S10 in §4.)*

#### SH1 — the pre-trade bound in collateral space, and the market's own tick
**Files:** `backend/sportsassets/pmus.py` (`submit_fok`'s `expected_cost`, `_amount(price, tick=None)`);
`backend/tests/test_pmus.py`; `backend/tests/test_pmus_post_only.py`.
**Depends on:** nothing. **Can start now: YES. BLOCKS EVERY SHORT ANYWHERE.**

**Builds.** (a) `pmus.py:2381 expected_cost = limit_price * quantity`, compared against the venue's preview at
`:2402` with `PREVIEW_COST_TOLERANCE = 1.02` (`:36`). This is THE pre-trade money bound, added after the
overspend incident. On a BUY_SHORT the wire is the complement while the venue's cost is the collateral, so the
guard is **void in the loose direction** on shorts of longshots and **inverts above 0.50**, refusing a
correctly priced short as `preview_mismatch` — a status that reads as the venue disagreeing with us. The
programme names this void for the MIRROR only (Phase 5: "the preview guard bounds nothing on a short so the
mirror bounds pay itself"); the per-fill lane has no compensating bound named anywhere, and the post-fill
overspend breaker only catches it after the money has moved and then halts the whole sleeve. Make
`expected_cost` intent-aware: `(1 - limit_price) * quantity` for a short buy, byte-identical for every
existing long caller. (b) The preview runs only `if not sell:` (`pmus.py:2383`) — there is **no venue
cross-check of any kind on the sell side**, which is the one instrument that would notice a price in the wrong
space. Compute the collateral-space expectation on a SELL_SHORT too, and when the preview cannot be run,
**refuse rather than send**, contained: return a result dict, never raise into a worker. (c) Land the tick
work in the same file so `pmus.py` takes one review, not two: `_amount(price, tick=None)` takes its decimals
from the market's own tick and **REFUSES an off-tick price by name (`off_tick`) instead of rounding** — §0 of
the mirror programme records that a large share of his mapped markets sit on a half-cent grid while every wire
function hardcodes a cent and `_amount` formats to two decimals, so a half-cent price ROUNDS UP, producing a
BUY above the computed wire that the preview passes because it compares against the unrounded float.
**THE DEFAULT KEEPS EVERY EXISTING CALLER'S BYTES IDENTICAL** — that property is what makes this safe to land
in parallel with the wave, which also calls `pmus.py`.
**Unreadable contract.** Intent unreadable ⇒ refuse to send, never "assume long". Preview unreadable ⇒ the
existing `preview_unreadable` refusal.
**Tests.** `test_expected_cost_is_collateral_on_a_short` (a price table spanning longshot to favourite);
`test_a_correctly_priced_short_of_a_favourite_is_not_a_preview_mismatch`;
`test_a_sell_side_short_with_no_preview_refuses_by_name`; `test_an_unreadable_intent_refuses`;
`test_amount_carries_the_market_tick_and_refuses_off_tick`; `test_default_tick_keeps_every_cent_rule`
(existing full-precision sweeps unchanged); the post-only fixtures unchanged.
**Gate.** Deterministic: the price table passes at every rung and every existing long assertion is
byte-identical. Shadow: `off_tick` **passes at 0** over 24 h. The live half of the tick is V1 rung 11.

#### SH2 — a short must be exitable before one is placed
**Files:** `live_executor.py` (`sell_limit_price`'s SELL_SHORT call sites, `_exit_intent`, the
`short_reduce_unproven` refusal) — **WAVE-BLOCKED**; `backend/tests/test_side_intent.py`.
**Depends on:** SH1, R3. **Can start now: NO.**

**Builds.** The plausible failure of a short exit is not a bad price but a short that **cannot be exited**,
and this file already records that class as a past incident. Two amplifiers: there is no venue preview on the
sell side at all (SH1 (b)); and a short ENTRY converts to contract space via `wire_limit` while the short EXIT
applies no conversion — it reads the short-leg bid, subtracts two cents and sends it verbatim with intent
SELL_SHORT. Under the venue model `wire_limit` itself states, closing a short means BUYING the contract back,
so that limit does not overpay — it never fills. Therefore Phase 5's clause *"a short REDUCE only via
`close_position` when sole holder until a rung reads a resting SELL_SHORT back, else `short_reduce_unproven`"*
is **not a later rung; it is a hard precondition of placing the first proof short**. Build it as such: the
short exit path refuses `short_reduce_unproven` by name, and the only permitted short reduction is
`close_position` under R3's hardened sole-holder test.
**Unreadable contract.** An unproven short reduce refuses by a name that IS in `EXIT_PENDING_REASONS`, so the
position lane pins and retries; never send a SELL_SHORT limit whose denomination is unproven.
**Tests.** `test_a_short_reduce_is_refused_until_the_rung_prints`;
`test_the_sole_holder_close_is_the_only_short_reduction`;
`test_short_reduce_unproven_is_in_exit_pending_reasons`.
**Gate.** Deterministic. `SELL_SHORT sent` **passes at exactly 0** until V1 rung 5 prints a resting
SELL_SHORT read back from the venue in a stated denomination.

#### SH3 — the desk and the pair path are sign-safe
**Files:** `live_executor.py` (both `_execute_manual_limit` submit paths, the desk cash-out,
`_pair_completion_context`, the reaper's `fill_cash` call) — **WAVE-BLOCKED**; their tests.
**Depends on:** SH1. **Can start now: NO.**

**Builds.** "The short branch is off" is true of ONE lane only. Four live sign holes. (a) **The manual desk
can send a BUY_SHORT today**, with `LIVE_ALLOW_SHORT` off: both desk paths pass `mapping["intent"]` straight
into `submit_fok`, `resolve_market_exact` stamps the short intent via `order_intent_for` → `premap.side_intent`,
and `wire_limit`'s call sites do not include any desk path — so the desk's limit goes on the wire in outcome
space on an order the venue reads as a sell of the contract, the exact shape `wire_limit` was written to fix.
The desk also never calls `_spawn_echo`, so a desk short earns no side echo, no position-sign verdict, no
`short_side_proof`, no probation, no side band and no ask tolerance. Route the desk through `wire_limit` and
the existing `_short_gate`, or refuse a non-long desk intent by name. (b) **The desk cash-out prices one leg
and sells another** by two independent derivations: it reads `_pm_long_leg`, prices with the bid, then calls
`submit_fok` with NO intent argument so the venue side is derived a second time — the exact defect
`mirror_exit`'s own comment names ("two derivations can disagree, and disagreeing here means pricing one leg
and selling the other") and fixed next door. Pass the derived intent, and book with intent-aware
`realized_pnl`. (c) **Pair completion is sign-blind:** `_pair_completion_context` asks `_pm_held`, which
returns an absolute net position; the file documents that this `abs()` erases the side and that reasoning "a
short reads negative so a positive held must be long" is a bug it ALREADY SHIPPED ONCE — the entire reason
`_pm_long_leg` exists — yet the pair path never calls it. If we are short the sibling we are already long the
slug, so "completing the pair" doubles a position while the log prints a lock message indistinguishable from a
real arbitrage, and it is gated on HOLDING a short, not on `LIVE_ALLOW_SHORT`. **The carve-out itself stays**
(a deliberate owner-ordered rule with locked economics, capped at shares already held); this unit makes it
refuse a short sibling. (d) The reaper's long default is STRUCTURAL, not incidental: the intent SQL reads only
the response's execution intent or `raw.preview.intent`, and `raw.preview.intent` is never written on the
ordinary IOC copy — so on precisely the rows the reaper reconciles (response lost) it returns NULL, and a
short books the wrong cash into the day cap and the sleeve's room.
**Unreadable contract.** Intent unreadable anywhere on the money path ⇒ refuse and name `intent_unreadable`,
never "assume long". Sibling side unreadable ⇒ no pair completion.
**Tests.** `test_the_desk_refuses_or_wires_a_short`; `test_the_desk_cash_out_passes_its_intent`;
`test_pair_completion_refuses_a_short_sibling`; `test_the_reaper_names_an_unreadable_intent`.
**Gate.** Deterministic and **must pass before `LIVE_ALLOW_SHORT` is ever set**: `intent_unreadable` prints
(**passes at 0** on long-only flow) and **0 desk orders reach `submit_fok` without an intent**.

#### SH4 — the short vocabulary, the day cap, and the claim
**Files:** `backend/migrations/050_mirror_shorts.sql` (NEW — the number §2 rule 3 reserves for Phase 5);
`backend/migrations/051_mirror_game_claims.sql` (NEW — reserved for Phase 6, landed here because the claim it
adds is what shorts open); `workers/mirror_live.py` (`_SQL_MIRROR_DAY` at `ml:355`);
`analytics/mirror_live_rules.py`; their tests. **Depends on:** SH1. **Can start now: YES.**

**Builds.** **The claim that binds today is migration 045's, not 047's.** The mirror's standing row is
inserted with `asset = long_asset` and status `filled`, and 045's `live_orders_one_fill_per_asset` is a
partial unique index on `asset`; `_open_mirror_book`'s own docstring says "THE INSERT IS THE CLAIM", it
catches the constraint and rolls back, and the worker names `asset_claimed`. So under P1's long-only rule no
mirror-vs-mirror co-hold is reachable at all, and the correct, narrow residual is exactly what shorts and
soccer open: **045 claims the LONG asset only**, so a short book and a sibling-condition book are different
assets and collide with nothing. 047 already ships the columns and indexes a referee would read
(`mirror_books_assets_idx`, `mirror_books_game_idx`, `game_key` stored on every book) and `game_key` is read
by NO refusal — so the SELECT half needs no migration; only the UNIQUE claim does. 051 adds one partial
unique index on the existing `game_key` column plus a game-level cap column. **In the same change:** 050
widens `mirror_orders`' `CHECK (side IN ('BUY_LONG','SELL_LONG'))` (`047:84`) to admit the short spellings and
carries the book intent, and `_SQL_MIRROR_DAY` (`ml:355`, `WHERE side = 'BUY_LONG'`) becomes `side LIKE
'BUY%'` (or sums by intent). It is the only thing bounding the mirror's gross buys per day: the moment a short
leg is written under another spelling the INSERT fails the CHECK, and if the CHECK is widened without the SQL
the day ceiling silently doubles.
**Unreadable contract.** The claim is a database constraint and cannot be unreadable; the referee that reads
`game_key` fails closed (unreadable ⇒ refuse the book).
**Tests.** `test_a_buy_short_row_moves_the_day_cap`; `test_the_side_check_admits_the_short_spellings`;
`test_two_books_on_one_game_are_claimed_in_the_database`;
`test_a_short_book_and_a_long_book_on_one_condition_collide`.
**Gate.** Deterministic: the day-cap arithmetic is identical for a long and a short leg of equal collateral,
and the game claim refuses the second book on one `game_key` beyond the cap.

#### SH5 — the probation, and the mirror's short leg
**Files:** `analytics/mirror_live_rules.py` (`_state_nums` widened, `OpenOrder.intent`, `mirror_target`'s sign
door); `workers/mirror_live.py`; `workers/mirror_shadow.py`; `live_executor.py` (the `_open_mirror_book`
intent door, the probation lock, the reaper echo site) — **WAVE-BLOCKED**; their tests.
**Depends on:** SH1, SH2, SH3, SH4, P1, P4, A2, X1's open-short clause, V1 rung 5. **Can start now: NO.**

**Builds.** **THREE OF PHASE 5's FIVE NAMED CLAUSES ARE ALREADY BUILT, NAMED, COUNTED AND TESTED, and
re-implementing them is the main way this unit goes wrong.** `wrong_sign_trip` trips live off, cancels the
book's opens and freezes it, and is in the census key list and `rules.P2_INTEGRITY_COUNTERS`;
`book_settle_disagree` is built with its census key; `gross_buy_usd` is a `BookState` field accumulated from
`le.fill_cash(inc, px, ORDER_INTENT)`, and `fill_cash` is ALREADY intent-aware — so Phase 5's
`gross_buy_usd += (1-px)*q` clause is **one constant becoming the book's intent, not a build**. `_state_nums`
exists and needs widening, not writing. Genuinely absent: migration 050 (SH4) and `OpenOrder`, which carries a
bare `side: str` and no intent field. So: fix the book intent at open; widen `_state_nums`; give `OpenOrder`
an intent and compare it; carry the book's intent into `fill_cash`; size in collateral space `(1 - wire) × q`
in `room_scale`, `wire_usd` and the day cap. **Route BOTH short doors through the ONE `_short_gate` the
executor already has** — `_open_mirror_book`'s ledger door and `mirror_target`'s negative-target refusal are
separate today, and doing it in two places is this codebase's recurring class-dilution defect. A sign flip
closes the episode, which is what the mirror already does (a negative target falls through to the paired
flatten), so Phase 5's `sign_flip` is existing behaviour named, not new machinery.
**ALSO, THE PROBATION HOLE.** `_spawn_echo` receives the serialising lock only when the result is ok and the
filled quantity is positive; every other exit releases it in the `finally` with NO verdict, and
`_rest_after_ioc` can return `rest_unknown`, leaving the row for the reaper — which has no `_spawn_echo` call
site. So such a short releases the lock immediately, freeing the next short, and if it later fills it earns no
side verdict at all, so `SHORT_PROBATION_N` never advances. Bounded today only because the rest lane defaults
off — and that is the flag the proof run wants on. Hold the lock until a verdict exists or a bounded timeout
releases it with a named counter, or give the reaper an echo call site.
**ONE FIGURE THAT MUST NOT AUTHORISE ANYTHING.** `mirror_shadow.py` carries a comment claiming a large number
of sign-verified BUY_SHORT fills with zero mismatch, while `live_executor` says in its own words that no short
has been placed since the cost model was corrected. **Unmeasured and self-contradicted inside this
repository.** Delete or qualify the comment in this unit; the authorising reading for shorts is V1 rung 5 and
the position-sign check, never that line.
**Unreadable contract.** `short_model_confirmed()` False ⇒ `short_model_disarmed`, no short book; a sign that
cannot be read ⇒ the existing `wrong_sign_trip` freeze.
**Tests.** `test_a_buy_short_row_moves_the_day_cap` (SH4's, re-run here);
`test_both_short_doors_call_one_short_gate`; `test_open_order_intent_is_compared`;
`test_gross_buy_usd_is_collateral_on_a_short_book`;
`test_a_rest_unknown_short_holds_the_lock_or_names_the_counter`;
`test_short_probation_n_default_is_three`; every long path byte-identical.
**Gate.** Shadow, 7 days per Phase 5: `MIRRORSHORT` field `would_fill_short_lo` (market-clustered 95% lower
bound) **passes at >= 0.50 over >= 30 markets**. Live: **>= 30 CLOSED short books** (§3b Phase 5 row, cluster
= book, min n = 30 — this programme does NOT lower it) with `at_or_better = 1.0` on the short leg,
`wrong_sign_trip = 0`, `book_settle_disagree = 0`, `overfill = 0`; §4 M13 short-side dollar coverage **>=
0.95** of his negative-net dollars on mapped admitted markets.

### WAVE B — THE BUY LANE. All wave-blocked; none can start now.

#### B1 — the clip in force is readable and cannot become the code default silently
**Files:** `live_executor.py` (`per_fill_usd`, `_refresh_clips`); `analytics/roster_rules.py`;
`workers/roster_auto.py`; `backend/tests/test_roster_reset.py`;
`backend/tests/test_clips_follow_the_rules.py` — **ALL WAVE-BLOCKED. Can start now: NO.**

**Builds.** The owner decided that clips stay at $50. Nothing in the code enforces that, and three paths raise
it. (1) `_refresh_clips` sets the override map to None whenever the `ingestion_state` row is ABSENT, with no
log line and no retry — the retry loop guards exceptions and a clean read of a missing row is not one — and
`per_fill_usd` then falls through to the hardcoded per-whale defaults, which are higher and cover more whales
than the decided roster. **The headline "$50 clip" and its several-times-larger opposite are the same code
selected by a database row nobody has read.** (2) The roster endpoint's reset flag deletes the stored clip
map, producing exactly that state; the repository already carries this as an xfailing marker in
`test_roster_reset.py`. (3) `roster_rules.decide` promotes a whale to the promoted clip unattended on the
hourly pass, and the in-flight owner lever binds the MEASURING clip only — so the owner's decision has an
expiry administered by a cron. Build copy-chain FIX 2's storage contract exactly as written; make an absent
clip row a NAMED, LOGGED, COUNTED state (`clip_map_absent`) that keeps the last-known map rather than falling
to the code default, and refuse with `no_clip` if none has ever been read. **This unit changes no default —
it makes the decided clip the thing actually in force.** It also fixes the ORDER of the type multiplier:
`per_fill_usd` applies `TYPE_MULT` AFTER the stored clip, so the decided clip is exceeded on two whole market
types; a STORED clip becomes a ceiling the multiplier may not raise, while a whale with no stored clip keeps
today's arithmetic exactly.
**Unreadable contract.** Absent map ⇒ last-known map, named and counted; never the code default, never a
whale outside the decided roster.
**Tests.** `test_an_absent_clip_row_does_not_fall_to_the_code_default`;
`test_a_stored_clip_is_a_ceiling_the_type_multiplier_cannot_raise`;
`test_the_reset_does_not_delete_the_owner_clips`; `test_promotion_is_bounded_by_the_owner_clip`;
`test_the_code_default_is_unchanged_for_a_whale_with_no_stored_clip`; the existing xfail flipped to a pass.
**Gate.** Copy-chain FIX 2's own gate, plus: with the decided map stored, B2's census line
`copies_above_stored_clip` **passes at 0** over 24 h on every market type; with the row deleted in a test,
`clip_map_absent` counts and no whale outside the decided roster is copied.

#### B2 — the pre-INSERT dollar census (copy-chain FIX 5)
**Files:** `live_executor.py` (`_copy_stop`, `_gate_census`, three INSERT columns);
`backend/migrations/053_copy_census.sql` (NEW); `api/app.py` (the census line) — **WAVE-BLOCKED.
Can start now: NO.**

**Builds.** Twenty-two returns run before the first `live_orders` INSERT and each writes only an in-memory
integer in a bounded dict, so the question *"which of his dollars are we not copying, and to which gate"* has
no answerable form. Build FIX 5 as written: key by GATE, never by a per-row message carrying a number (a
per-row text can never aggregate, which is why most refusals are unnamed today); sum HIS NOTIONAL per gate per
whale; a fresh-vs-sweep key plus a bounded per-boot seen set so the sweep's re-offer of one trade counts once;
every latent whole-roster stop on one line; durable storage in 053 so a restart does not erase the day.
**Narrower and correct against the buys assessment:** his notional IS durably recorded (the placed row joins
`trades`); what is unrecoverable is the MEDIAN AT DECISION TIME, which lives in an in-memory cache that is
never persisted. Add three INSERT columns — `his_notional`, `conviction_median`, `governed_clip` — so a placed
clip can be reconstructed. That is three columns, not FIX 5.
**Unreadable contract.** A census write that fails must never fail a copy: try/except, count, continue.
**Tests.** FIX 5's gate as a property test over a synthetic day; `test_a_trade_counts_once_across_sweep_passes`;
`test_the_census_survives_a_restart`; `test_a_raising_census_does_not_change_maybe_execute`.
**Gate.** FIX 5's own: every trade of the day lands in exactly one of {filled, row-refused by gate,
pre-row-refused by reason, never offered}, and the four **sum to the day's trades AND notional within 1%**.

#### B3 — the conviction floor and the denominator: measure, then change, one at a time
**Files:** `live_executor.py` (`per_fill_usd`, `whale_average_notional`, the conviction arm list);
`backend/tests/test_conviction_sizing.py` — **WAVE-BLOCKED and gated on B2. Can start now: NO.**

**Builds.** **STEP 1, ZERO MONEY, LANDS FIRST.** `test_conviction_sizing.py::_clip` REIMPLEMENTS the arm list
in the test file and omits both the positive-average guard and the integer-share step — its own docstring says
a harness that computes the intended answer instead of the shipped one can never catch a divergence. Re-point
it at the production sizing path and add the owner's own worked case as an assertion AGAINST PRODUCTION at the
clip in force. Merely re-pinning the helper's governed constant would not work: the assertion would still be
about the helper. **STEP 2, MONEY PATH, SHIPS ALONE AND ONLY AFTER THE READING.** At the decided clip the
conviction anchor is small enough that a copy clears the dust floor only when his trade is a large fraction of
his own median notional; below that it is refused `below_min_clip` BEFORE any row is written — not sized down,
dropped, with no row, no dollars and no durable name — and the owner's own quoted example computes to a few
dollars and is discarded. At the higher clip the cliff falls below `CONVICTION_MIN` and the floor is
structurally unreachable. Same code, silent drop of half his entry flow at one clip and never binding at the
other, selected by a database row. The fix is one of: lower the dust floor, raise the anchor fraction, or
floor the conviction arm at the dust floor rather than dropping. **SEPARATELY AND NOT IN THE SAME LANDING:**
the conviction DENOMINATOR — its docstring says exits are excluded, its SQL does not exclude them, and its
test asserts a third thing, while `classify_exit`'s own comment records that most classifiable buys are exits.
Changing the floor and the denominator in one wave makes neither measurable (copy-chain's standing
constraint). Also name the thin-whale branch: the cliff does not apply at all below the minimum sample, so an
identical trade sizes very differently by roster tenure and the rule flips silently on a whale's Nth priced
buy — count `conviction_unreadable` and `conviction_thin` separately so they stop being the same state.
**Unreadable contract.** An unreadable median ⇒ today's full-clip behaviour, named `conviction_unreadable`
and counted.
**Tests.** `test_the_helper_calls_production`; `test_the_cliff_at_the_clip_in_force`;
`test_the_denominator_excludes_exits`; `test_a_thin_whale_is_named_not_silent`.
**Gate.** Step 1 deterministic. **Step 2's gate is a READING, not a threshold:** B2's census field
`below_min_clip_usd` per whale per day over **>= 7 days** with the day-to-day spread, and the owner signs
(owner decision 5). After the change the same census line **falls by the predicted amount ± 10%**.

### WAVE E — SELLS AND EXITS. The largest live money error in the system. All wave-blocked.

#### E1 — the exit fraction is an episode stock, on all three producers
**Files:** `live_executor.py` (`classify_exit`, `mirror_exit`); `workers/whale_exits.py` (`_cycle`'s payload
only); `backend/tests/test_exit_fraction_is_a_stock.py`;
`backend/tests/test_exit_target_survives_the_rewrite.py` — **WAVE-BLOCKED. Can start now: NO.**

**Builds.** One change to the one formula that sizes every sale, covering ALL THREE producers that disagree
with the target form consuming them. (1) **The trade lane's denominator is his ALL-TIME gross on the leg** —
the `bought` SQL has no time bound — so the cumulative fraction is inflated the moment he has completed a
prior round trip on that token, and `if closed_frac >= FULL_EXIT_FRAC: qty = ours` PROMOTES it: because the
inflation is `1 - remaining/lifetime_gross`, **the smaller his trim the worse the multiple**, and after enough
completed round trips on one token a small trim is a full flatten through `close_position` with no limit
price. Round trips only accumulate, and both whales trade the same league moneylines through a season.
(2) **The sell-routed ledger branch is armed and untested:** `execute_copy` routes any ingested SELL straight
into `mirror_exit` where the supplied fraction is None, so the ledger branch `min(sold/bought, 1.0)` runs,
gated on nothing but `exitable_whales()` — a lifetime ratio by construction. Every Pool stub in both exit
suites supplies the fraction through the payload, so no test enters this branch with a non-zero sold quantity.
(3) **The position lane feeds a per-cycle FLOW fraction into a CUMULATIVE-target form:** `whale_exits` sends
only `{whale, asset, side, closed_frac}`, so `mirror_exit`'s existing cumulative rewrite is skipped and the
raw flow fraction is applied against our ORIGINAL shares; a successful sale advances the baseline, so the
divergence is **unbounded, not bounded**, and every stalled cycle returns `mx_exit_rounds_to_zero`, which IS
in the pending allowlist, so the census reads "held for a retry" while the retry can never progress.
**THE FIX IS ONE DENOMINATOR: HIS EPISODE ENTRY.** `analytics/merge_pnl._replay_stepper` already computes the
honest per-episode denominator and Phase 9 names it as the M18 builder — reuse it rather than writing a
second. `whale_exits` sends the pinned pre-exit baseline and the delta as `his_open_shares`/`his_exit_shares`
so the rewrite runs. Also the epsilon: an exactly-at-the-floor trim evaluates just under the floor in float
and is refused `mx_below_floor` — cosmetic on the position lane, DROPPED OUTRIGHT on the trade lane; compare
with a tolerance.
**Unreadable contract.** An unreadable episode boundary refuses with a name that IS in `EXIT_PENDING_REASONS`
so the position lane pins it, and (after E4) with a trade-lane retry record; **never fall back to the lifetime
denominator.**
**Tests.** The existing suite CANNOT see this class: its `Row` is a bare dict subclass with no `orig_qty`, so
the legacy base path is taken, and each trim is handed a freshly rebuilt Pool — verbatim the instrument defect
the source names as the reason the base bug survived. Drive the stateful pool from
`test_exit_target_survives_the_rewrite.py`, and add fixtures where `bought != open_sh` **for the first time**:
`test_a_repeated_trimmer_tracks_his_remaining_fraction`;
`test_a_small_trim_after_many_round_trips_is_not_a_flatten`;
`test_the_sell_routed_ledger_branch_uses_the_episode_denominator`;
`test_an_exact_floor_trim_is_not_below_the_floor`.
**Gate.** **A REPLAY, because the lane is already live and cannot be shadowed.** Over the last 30 days of
`live_orders × trades`, replay every exit the system actually made and report per exit
`|our sold fraction − his episode fraction|`. **PASSES at p90 <= 0.05 AND zero exits where we sold >= 2× his
fraction.** Computable from rows already held; run BEFORE the change for the baseline and after it for the
verdict, then re-run on the following 7 days as confirmation.

#### E2 — a fail-closed exit refusal never advances the snapshot
**Files:** `live_executor.py` (`EXIT_PENDING_REASONS`, gates 8 and 12, `classify_exit`'s holding query);
`workers/whale_exits.py` (the pending-vs-no_action branch); their tests — **WAVE-BLOCKED. Depends on E1.
Can start now: NO.**

**Builds.** Four defects, all of which DESTROY an observation rather than pin it. (1)
`mx_exit_dedup_unreadable` is ABSENT from `EXIT_PENDING_REASONS` although its own comment promises a retry;
its trade-lane twin `mx_exit_ledger_unreadable` IS in the list (I listed every member of the frozenset at
HEAD). An unlisted reason falls to `exits_no_action`, the snapshot advances, and the exit is discarded
permanently — on the fail-closed branch of a money path. **One line.** (2) `mx_exit_recently_applied` is
likewise unlisted, and the dedup window is several of the position lane's own cycles — and the lane WRITES the
record it then blocks on, so it refuses ITSELF after every sale it makes, each blocked cycle permanently
advancing the baseline. (3) Gate 12 is guarded `if _xtid is None`, i.e. the position lane only, while the
source comment states the hazard symmetrically; position-sells-first then trade-lane-follows passes gate 8
(trade id) and never reaches gate 12, and because the two lanes compute DIFFERENT fractions the follow-up
sells the difference between two disagreeing formulas. (4) **No twin guard on the exit path:** the ENTRY path
documents key-divergent twins as observed and defends by transaction hash with a fail-closed branch, and
`trades.dedupe_key` puts the timestamp and the quantised price INSIDE the key, so a shifted timestamp produces
a second row with a different id — while `classify_exit` excludes only the current row BY ID, so the twin
stays inside his holding and understates what he still held. Reuse `_tx_hash_of`, already in the file, failing
closed on an unreadable hash — the same rule the entry path applies.
**Unreadable contract.** The invariant this unit establishes IS the contract: a fail-closed exit refusal must
never advance the snapshot.
**Tests.** `test_every_fail_closed_exit_refusal_is_pending` (an `ast` walk collecting every `_exit_done`
name on a fail-closed branch and asserting membership of `EXIT_PENDING_REASONS`);
`test_the_dedup_does_not_block_the_lane_that_wrote_the_record`; `test_gate_12_runs_for_both_callers`;
`test_a_tx_twin_does_not_double_the_exit_fraction`.
**Gate.** E1's replay re-run with dedup and a twin injected into the fixture set: **0 discarded observations,
0 double sales**, and the replay's p90 still **<= 0.05**.

#### E3 — the positions walk is complete or it is unreadable
**Files:** `workers/whale_exits.py` (`_fetch_positions`, `diff_exits`, `_cycle`, `_confirm_gone`);
`workers/mirror_live.py` (the `_confirm_gone` call sites); their tests — **whale_exits.py WAVE-BLOCKED.
Can start now: NO.**

**Builds.** `_fetch_positions` breaks the page walk on an empty page and on any short page; only a still-full
page at the ceiling raises `TruncatedPositions`. So a venue hiccup returning an empty page mid-walk yields a
snapshot the caller believes COMPLETE, missing every asset past the cut; those assets are absent, `diff_exits`
books each as a **full exit**, and on the non-truncated branch `_confirm_gone` is NEVER CALLED — it is wired
only inside the partial branch. The bound is not the per-cycle exit ceiling: the fairness rotation plus the
deferred pin marches the whole vanished set through at that ceiling per cycle, so the real bound is every
position we hold past the cut. The module's own comment names the class: the consequence is not a coverage
gap, it is wrong sell orders. Two changes, the second subsuming the first: treat an empty page before the
ceiling as `TruncatedPositions`; and extend `_confirm_gone` to EVERY vanish we hold on BOTH branches.
**Same unit, same function's contract:** `_confirm_gone` narrows positions to the CONDITION (both tokens) then
loops for a row whose asset equals the single asset passed — a complement row fails that test, the loop ends,
and it returns True ("gone") (read this session in the working tree). `mirror_live` calls it with the token WE
labelled long, so a whale who has left the long leg and sits in the complement is reported gone and the book
takes the vanish flatten, the only path that accepts slippage, while he still holds. The fix already exists
unused: `wx.market_positions` reads both tokens. **This is Phase 5's "`_confirm_gone` on the token carrying
his net" clause, and it is a PRECONDITION of shorts, not a consequence.**
**Unreadable contract.** A short or empty page ⇒ `TruncatedPositions` ⇒ partial ⇒ no unconfirmed exit.
`_confirm_gone` unreadable ⇒ NOT gone (today's stance, kept).
**Tests.** `test_an_empty_page_mid_walk_is_truncated`; `test_confirm_gone_runs_on_the_complete_branch_too`;
`test_confirm_gone_reads_both_tokens_of_the_condition`; `test_a_whale_in_the_complement_is_not_gone`.
**Gate.** Fault injection: an empty page at any offset produces **0 exits** and a partial snapshot;
`_confirm_gone` consulted on **100%** of vanishes. Live: `unconfirmed_full_exits` **passes at 0 over 7 days**,
with the count of walks classified truncated printed beside the count classified complete.

#### E4 — the trade lane gets a retry record, and his price (copy-chain FIX 8)
**Files:** `live_executor.py` (`maybe_execute`'s exit dispatch, the `sell_limit_price` call sites);
`backend/migrations/054_exit_retry.sql` (NEW); their tests — **WAVE-BLOCKED. Depends on E1, E2, A3.
Can start now: NO.**

**Builds.** The lane carrying the volume drops every liquidity refusal: `maybe_execute` only increments a
`tradelane_dropped_*` counter and returns, so `mx_venue_unfilled`, `mx_no_bid_for_partial`,
`mx_partial_full_exit`, `mx_exit_rounds_to_zero` and `mx_below_floor` are lost and retried only by his next
fill on the same market — which may never come. The source states the gap itself ("Those need a real retry
record"). The position lane pins and retries; the trade lane does not. Build the retry record in 054,
bounded and consumed. Second half: **price our exit where he prices his.** `sell_limit_price` is two cents
THROUGH the bid, and the full-and-sole branch sends `close_position` with no limit at all. On the trade lane
we already know where he exited — he bought the complement, so his exit price on our leg is its complement,
and `classify_exit` has that fill in hand. The mirror lane already does this correctly and never markets a
paired flatten. §5 decision 18 recommends holding rather than a bounded take, so **this is about the price of
the exits we DO take, not about taking more of them.**
**Unreadable contract.** His complement price unreadable ⇒ today's bid-based limit, counted
`exit_price_from_bid`; never an unpriced send on a partial.
**Tests.** `test_a_liquidity_refusal_writes_a_retry_record`; `test_the_retry_record_is_consumed_and_bounded`;
`test_the_exit_is_priced_at_one_minus_his_complement`; `test_an_unreadable_complement_falls_back_by_name`.
**Gate.** `trade_lane_exit_refusals_without_a_retry_record` **passes at 0**;
`exits_priced_from_his_complement` **passes at >= 0.90** of trade-lane exits. **§4 M14's threshold is left
UNSET** — it is owner decision 18 and it needs A3's MIRROREXIT reading.

#### E5 — a per-whale exit switch, so the owner's decision is expressible
**Files:** `live_executor.py` (`exitable_whales`); `api/copies_record.py` (the whale set);
`workers/whale_exits.py` (the wanted set); their tests — **WAVE-BLOCKED. Can start now: NO.**

**Builds.** Copy-chain owner decision 5 — exits on for one whale, off for the other — is UNEXPRESSIBLE today:
`exitable_whales()` is the union of four sets, deliberately wider than the entry roster so that cutting a
whale cannot strand his positions, and the second whale sits in two of them, so both his exit lanes are on and
the only way to honour the decision also removes his entry clip. Add ONE stored map `live_exit_whales` in the
same store and helpers as the clip overrides, consulted by both lanes, **defaulting to today's behaviour
(everyone exitable)** so nothing changes on deploy; a whale set false is refused by name `exit_off_for_whale`
and counted, and his positions are still reducible by an explicit admin flatten.
**Unreadable contract.** An unreadable map keeps the CURRENT set — never widens, never empties. **This is the
one place fail-open is correct: refusing to exit strands a position.**
**Tests.** `test_the_default_is_every_exitable_whale`;
`test_a_whale_switched_off_is_not_exited_on_either_lane`;
`test_an_unreadable_map_keeps_every_whale_exitable`;
`test_switching_exits_off_does_not_change_his_entry_clip`.
**Gate.** Deterministic; then setting a whale off produces **0 exit attempts on either lane** for him over
24 h, with no change to the other whale's exit count and no change to either whale's clip.

### WAVE X — ACCOUNTING AND THE OPERATOR.

#### X1 — the served surfaces see a trim and an exit
**Files:** `api/track_record.py`; `api/copies_record.py`; `api/app.py` (`_category_breakdown`,
the day-detail reconciliation); their tests — **ALL WAVE-BLOCKED. Depends on E1. Can start now: NO.**

**Builds.** Six defects on one theme: a methodology copy's realized dollars have nowhere dated to live.
(1) `track_record.build` reads `realized` from the venue and then discards it on any unsettled row, so a
partial reduction's realized dollars are zero in the site headline — the same shape as the defect the owner
reported and which was fixed in `copies_record` and never here — and the copies ROI takes only terminal
statuses, so trim dollars never enter it either. Rule LE's pro-rata ratchet makes trimming the MODAL event of
a full-methodology mirror. (2) Every full exit writes `cashed_out`, which the daily breakdown and the
day-detail reconciliation both omit (`WHERE status = 'settled'`), so every exit becomes unexplained residual
on the surface built to eliminate residual; `/api/admin/copy-grade` already adds `cashed_out` with the comment
explaining why, and the served surfaces did not get it. (3) **The silent case is worse:** on a PARTIAL trim
the venue slug stays open so side A skips it and the standing row stays `filled` so side B skips it — real
realized money moves, the residual is zero, and the reconciliation reports CLEAN. (4) A trim carries **no
date** anywhere: the partials list hardcodes a null day, and the standing row logs every BUY as a dated
addition line and logs NOTHING for a sale, so the realized side cannot be reconstructed, aged, or
double-book-checked from the ledger every served surface reads — itemise sales the way buys are itemised.
(5) The episode close stamps the settlement timestamp at close time, so a multi-day book files all of its P&L
on the close date across four dated surfaces including the loss breaker — date on the realizing event.
(6) **Before shorts:** an OPEN short is classified settled (net position at or below zero) and its opening
proceeds are booked as profit in the venue-basis figure; the undatable guard that would catch it is disarmed
by one dated sibling row on the same slug. Also: the display cap must not delete a row's STAKE with its P&L
(the sleeve's ROI denominator disappears with its numerator), and the open-exposure query needs a positive-
shares predicate (a book sold to zero stays `filled` by design to hold the asset claim).
**Unreadable contract.** A surface that cannot read a figure prints it unreadable, never zero.
**Tests.** `test_a_trim_is_dated_and_visible_in_the_headline`;
`test_cashed_out_is_in_the_daily_breakdown`;
`test_the_day_detail_does_not_agree_while_both_sides_are_blind`;
`test_a_sale_writes_an_itemised_ledger_line`; `test_a_fully_exited_book_is_not_open_exposure`;
`test_the_display_cap_keeps_the_stake`; `test_an_open_short_is_not_settled_profit`.
**Gate.** On the seeded fixture, the day-detail residual is **exactly $0.00 with trims and exits present** and
**non-zero when a trim is deliberately hidden** (the test must be able to fail); the headline net P&L equals
the venue-basis figure to the cent on a book that has trimmed. **The open-short clause must pass before
`LIVE_ALLOW_SHORT` is set.**

#### X2 — one venue P&L is not two lanes' P&L
**Files:** `analytics/engine.py` (`_settle_pmus_from_venue`, `allocate_venue_pnl`);
`analytics/decompose.py` (`payout_of`); `backend/migrations/055_asset_claim_by_lane.sql` (NEW);
`backend/tests/test_engine_settlement.py`. **Depends on:** nothing — `engine.py` is CLEAN.
**Can start now: YES.**

**Builds.** `_settle_pmus_from_venue` groups `live_orders` by lower-cased slug with NO whale and NO lane
predicate and splits pro-rata by filled cost. Migration 045's claim is per ASSET, not per slug, and exempts
manual/underdog — so **today, with no mirror and no shorts, a human desk ticket on the same slug takes a
pro-rata share of the copy row's P&L**, and both loss breakers read exactly that number. Once a mirror book
and a per-fill row hold opposite tokens of one slug (legal under 045), the same machinery books a losing row
as a winner. Fix: group by (slug, asset) and allocate within the asset group only; where a slug carries rows
on both tokens, settle each token against its own payout. Separately, `payout_of` returns any value in [0,1],
which is §1 D17's exact shape (a closed-but-pending market carrying its last mids) — wrap it in a
resolved-gated reader that can never read a mid as a payout, and RAISE (not merely record)
`book_settle_disagree` when a settle came from an allocation across more than one row. Fix the restatement
audit in the same change: the old-P&L term is forced to zero for a `filled` row carrying realized trim P&L,
so the delta reports the whole figure as new money.
**Unreadable contract.** An unreadable payout, or a market closed-but-not-resolved, leaves the row unsettled
this pass, counted `settle_deferred`, retried. Never a mid booked as a payout.
**Tests.** `test_two_lanes_on_opposite_tokens_settle_independently`;
`test_a_manual_ticket_takes_no_share_of_a_copy_row`;
`test_a_closed_unresolved_mid_is_never_a_payout`; `test_an_allocation_across_rows_raises_disagree`;
the existing restatement-delta pins updated deliberately.
**Gate.** On the seeded two-lane fixture, allocated P&L per row **equals truth to the cent**;
`settle_deferred` printed. **Must land before shorts and before the first mirror book on a slug the per-fill
lane has ever touched.**

#### X3 — the operator surface and the promotion gate's data source
**Files:** `backend/sportsassets/api/mirror_admin.py` (NEW — every handler and its SQL);
`api/app.py` (**TWO LINES**: an import and a `register(app)` call — `app.py` is a monolith of 147 bare route
decorators with zero `APIRouter`, verified this session, so a `register(app)` shim really is the minimum
footprint in a wave-owned file); `.github/workflows/engine-diagnostic.yml`;
`backend/tests/test_mirror_admin.py` (NEW). **Depends on:** A2; the wave landing `app.py`.
**Can start now: NO.**

**Builds.** `GET /api/admin/mirror` returning exactly the payload `rules.p2_verdict` (`rules:1399`) reads —
today it, `demotion_due` (`rules:1375`) and `capture_short` have no caller and no data source, and only
`/api/admin/mirror-shadow` and `/api/admin/mirror-cover` exist. `POST /api/admin/mirror-arm` writing
`mirror_live`, `mirror_live_whales`, `mirror_live_demoted` and `mirror_flatten` into `ingestion_state` — the
four keys NO code writes today, so arming, narrowing, demoting and force-flattening are hand-written database
rows. **The payload's key set must be EXACTLY the key set `p2_verdict` reads, pinned by a set-equality test,**
so the verdict can never come back `unreadable:*` because a key was renamed.
**Unreadable contract.** An unparseable body writes nothing; `mirror_live` accepts only the literal boolean
true; a whale absent from the env allowlist is refused by name (the DB may only NARROW).
**Tests.** `test_the_payload_key_set_equals_p2_verdicts_reads` (by inspection);
`test_a_malformed_body_writes_nothing`; `test_the_db_cannot_widen_the_env_allowlist`;
`test_require_admin_is_enforced`; `test_the_flatten_key_written_here_is_read_by_the_worker`.
**Gate.** `p2_verdict` returns **0 `unreadable:` keys** on the live payload; one POST arms and one POST
flattens, each read back on the next tick. **NOT a precondition of the first live book** (the environment is
the arm and the allowlist is the reversal); it IS the precondition of widening past one whale and of any
graded promotion.

### WAVE V — THE VENUE.

#### V1 — the 1-share rung harness (Phase 7)
**Files:** `.github/workflows/engine-diagnostic.yml` (a `mirror-probe` step);
`backend/sportsassets/api/mirror_admin.py` (a `require_admin` probe endpoint, guarded by a separate
`PROBE_PLACE` flag for the placing rungs — same file as X3, one review);
`backend/sportsassets/pmus.py` (`take_arms`' second refusal shape, the commission fields into the execution
record, a settlement reader, a balances reader); `analytics/mirror_live_rules.py` (`take_arms` accepts both
shapes); `backend/tests/test_mirror_probe.py` (NEW). **Depends on:** X3, R6, SH1 (rungs 1-4, 7-8, 11, 14, 16-17);
**SH1 + SH2 + SH3 landed for rung 5.** **Can start now: NO.**

**Builds.** The rungs are the only way to answer what only the venue can answer, and **no unit in any of the
three source plans owned this harness — three switch-on steps were authorised by lines nothing produced.**
Under one live book at the smallest cap: (1-4) post-only's BOTH refusal shapes, a GTD raw read-back, a cancel
read-back, a partial booked once; (5) **THE SHORT READS** — a resting SELL_SHORT, a BUY_LONG against a
negative position, and the net-position SIGN against a sent BUY_SHORT; (6) the per-side game key; (7)
settlement to the cent; (8) reaper isolation; (9) the wrong-sign trip; (10) commission fields on every
execution; (11) the half-cent tick read back; (12) queue priority under modify vs cancel-and-replace; (13)
transport; (14) cash — two consistent balance reads; (15) the settlement price; (16) the market state; (17)
the first-fill echo, so Phase 4's first-fill re-key cannot deadlock the first book.
**RUNG 5 IS GATED:** it is the only rung that sends a short, and it may not run until SH1 (the collateral
preview and the sell-side refusal), SH2 (`short_reduce_unproven` and `close_position`-only) and SH3 (the desk
doors) have all landed. That ordering is the single most important thing in this unit.
**Unreadable contract.** A rung that cannot read its answer prints `rung_unreadable:<n>` and authorises
nothing; the probe endpoint refuses to place without its own flag.
**Tests.** `test_both_post_only_refusal_shapes_arm_the_take`; `test_commission_fields_are_parsed`;
`test_the_settlement_reader_parses_a_non_binary_price`;
`test_the_probe_step_jq_parses_on_absent_lines`; `test_the_probe_endpoint_refuses_to_place_without_the_flag`.
**Gate.** Every `MIRRORPROBE` line prints as specified; **rung 7 gap <= $0.05**; rung 11 reads the half-cent
back; rung 14 two consistent balance reads; rung 5 yields a **SIGN VERDICT**. Deterministic — one probe run
each, but **this is the first real money and each rung is one order.**

### WAVE L — LIFECYCLE AND FAMILIES.

#### L1 — the mirror's own wake, level freshness, re-entries (Phase 4)
**Files:** `ingestion/pipeline.py` (the mirror wake fan-out); `analytics/mirror_live_rules.py`
(`level_stale`, the tick parameters, venue state/phase admission facts, the reopen exemption,
`take_arms(status, state)`); `workers/mirror_shadow.py` (his level, candidate ranking);
`workers/mirror_live.py`; `live_executor.py` (the notify site hoisted above the gate block) —
**partly WAVE-BLOCKED. Depends on:** P1, SH1, A1. **Can start now: NO.**

**Builds.** Phase 4 as the mirror programme writes it, with two clauses emphasised. **(i) The independent
wake:** the reconciler's wake comes from an ingestion fan-out of fresh fills for the mirrored whales keyed by
condition, plus a trades-cursor fallback, with `wake_source` in {fill, poll, cursor, none} as a census field.
Today the notify rides INSIDE the per-fill lane's hand-off, so the probe switch and the halt both silently
degrade the mirror to polling — and **disabling the per-fill lane for the mirrored whale is exactly what
arming the mirror does.** **(ii) REOPEN IS EXEMPT FROM the daily book count — this is the RE-ENTRY rule and it
is not in the code:** admission refuses on the day cap with no reopen branch, so a whale who exits and
re-enters the same market inside a day spends two of the day's book slots on one market, and a re-entry after
a PARTIAL exit is additionally refused `venue_already_holds` before it reaches the day cap at all. Plus the
rest of Phase 4: `level_stale`; the market's own tick on the wire (the `pmus` half is SH1); venue state and
phase as NEW-book admission facts with the state named in the refusal; a live book reading suspended cancels
its rests and holds; the take never arms on a refusal while the state is not open; ranking by his dollars;
fidelity per his fill; the modify question after rung 12.
**Unreadable contract.** Game start unread ⇒ fall back to the slug date, named `game_start_unread`. Venue
state unread ⇒ refuse a NEW book by name; never open on an unread state.
**Tests.** Phase 4's list in full, plus `test_the_mirror_wakes_with_the_copy_lane_disabled`;
`test_reopen_is_exempt_from_the_day_cap`; `test_a_refusal_from_a_non_open_state_never_arms_the_take`.
**Gate.** Shadow: plans resting more than a cent under the bid with a stale trigger **= 0**; the markets read
per tick carry **>= 80%** of his recent gross dollars. Live: `MIRRORFIDELITY` field `wake_source_fill_share`
**passes at >= 0.85 with the copy lane DISABLED**; reduction wake-to-plan p50 **<= 10 s**;
`by_usd` **>= 0.5** with react p50 **<= 10 s** and p90 **<= 30 s**, over **>= 30 books** (§3b M11,
cluster = book); `off_tick = 0`; `replace_capped = 0` on in-play books over 24 h.

#### L2 — soccer: per-condition books and the price floor by name (Phase 6)
**Files:** `analytics/mirror_live_rules.py`; `workers/mirror_shadow.py`;
`backend/migrations/051` (shared with SH4); `live_executor.py` (the one-per-game read and the never-add prior,
for the mirror's sibling conditions only) — **partly WAVE-BLOCKED**; `copy_sports.py` **UNTOUCHED** — the
floor stays where the owner made it and the worker carries `MIRROR_SOCCER_FLOOR=inherit|open_only|off`,
default `inherit`. **Depends on:** SH5, L1, SH4's migration 051, owner decisions 8 and 9.
**Can start now: NO.**

**Builds.** Phase 6 verbatim: a three-way contract is one condition whose two sides share the identifier; up
to three books per game under one GAME-LEVEL cap; the referee claims per condition for mirror books; a Draw
book is not "the game already copied" for the mirror's sibling conditions while per-fill copies on the game
stay refused. **One added fact that reinforces the sequencing and is not in the programme's Phase 6 text:**
the soccer price floor is denominated on HIS fill price and is not intent-aware, so a short of a favourite is
a low-priced fill for him and is refused by construction — the floor materially suppresses the SHORT class on
soccer even after SH5 opens it.
**Unreadable contract.** The floor switch unreadable ⇒ `inherit` (fail closed).
**Tests.** Phase 6's list: three books on one game key admitted for the mirror and a fourth refused
`game_cap`; a per-fill copy on the game still refused by name; the game-key equality pins;
`test_mirror_floor_switch_defaults_to_inherit`;
`test_open_only_judges_the_floor_at_the_books_entry_level`;
`test_a_draw_condition_book_is_refused_by_the_floor_under_inherit`.
**Gate.** Shadow: his soccer games with more than one mapped condition read a target on every condition;
`floor_refused_usd` printed by class for 24 h before option (b) may be chosen. Live: **>= 30 closed soccer
condition-books**, `book_settle_disagree = 0`, `game_cap` breaches **= 0**.

---

## 3b. GATE STATISTICS — which line supplies each number, and what passes

Restates §3b of the mirror programme, with the LINE that supplies the reading added. **No live widening step
in §4 is authorised by a metric below its minimum n.** Two of the three source plans broke that rule; it is
the single most common way a rollout claims a proof it does not have.

| gate | unit | line and field | estimator / cluster / min n | passes at |
|---|---|---|---|---|
| Snapshot usability (M4) | P1 | `MIRRORSNAP.fresh_complete_share` | share / market / >= 30 markets AND 24 h | >= 0.95 |
| Net drift (M5) | P1 | `MIRRORSNAP.net_drift_p90` | nearest-rank p90 / market / >= 30 AND 24 h | <= 0.05 |
| Admissible mapping (M3) | P2 | `MIRRORSRC.admissible_share` | dollar-weighted share / market / >= 30 mapped | >= 0.90 |
| Listed-unmapped (M2) | P2 | `MIRRORCOVER-CELL.listed_unmapped_share` | share / market / >= 30 | <= 0.20 by markets AND usd |
| Shape (M7) | P3 | `MIRRORSHAPE.within_band_share`, `.cap_bound_share` | dollar share / market / >= 30 admitted | >= 0.95 in [0.9,1.1]; cap-bound <= 0.05 |
| Exit-leg would-fill | A3 | `MIRROREXIT.sell_fill_lo`, `.time_to_touch_p50/p90` | proportion / market / >= 30 | **the line prints at n >= 30**; the value feeds decision 8 |
| Short shadow | SH5 | `MIRRORSHORT.would_fill_short_lo` | proportion / market / >= 30 markets, 7 days | >= 0.50 |
| Live rest fill, at_or_better, maker (M10) | R5, A2 | `MIRRORGRADE.rest_fill_lo`, `.at_or_better`, `.maker_share` | proportion / **book** / **>= 30 books** | >= 0.40; **1.00 exact**; >= 0.5 |
| Live fidelity (M11) | A1, L1 | `MIRRORFIDELITY.by_usd`, `.react_p50`, `.react_p90` | dollar fraction, nearest-rank / **book** / **>= 30 books** | >= 0.5; <= 10 s; <= 30 s |
| Tracking error (M6) | A1, A2 | `MIRRORTRACK.p50`, `.p90` | nearest-rank / market / >= 30 AND 24 h | p90 <= 0.05, p50 <= 0.02 |
| Integrity (M15) | A1, R1, R5, R7 | `MIRRORINTEG.<name>` | counts / — / all rows | **every counter 0**; `frozen_ticks` < 1% |
| Settlement (M16) | A2 | `MIRRORGRADE.settle_gap`, `.settle_uncheckable` | per book / — / every closed book | <= $0.05 on clean books; **`settle_uncheckable` printed as the denominator** |
| Cash (M27) | R6 | `MIRRORCASH.funds_rejections`, `.cash_unreadable` | counts / — / 24 h | 0; <= 1% of ticks |
| Short live | SH5 | `MIRRORGRADE` short split | as M10 / **book** / **>= 30 closed short books** | at_or_better 1.0; the three trips at 0 |
| Exit fidelity (per-fill lane) | E1 | the 30-day replay: `abs_fraction_gap_p90`, `overshoot_2x_count` | per exit / — / every exit in 30 days | p90 <= 0.05 AND overshoot count = 0 |
| Copy funnel | B2 | the census four-way sum | totals / — / one day | the four sum to the day's trades AND notional **within 1%** |
| **M19, the economic gate** | A2 | `MIRRORGRADE.ci_lo`, `.n_needed_at_target`, `.n_exited`, `.n_resolved` | `proof.roi_with_ci` on peak exposure / **game** / 30 books AND 30 games | **REPORTED, NEVER GATED.** §3b records that its own interval is not reachable inside any phase window (31,052 games at his edge). See owner decision 2. |

---

## 4. THE SWITCH-ON SEQUENCE — what each step sets, the reading that authorises it, the reading that reverses it

**The levers, stated once.** `PMUS_MIRROR_WHALES=` (empty) is the **REVERSAL**: verified at HEAD,
`mirror_allowlist()`/`mirror_mode()` read the environment only, so emptying it resumes the whale's per-fill
copies in the same deploy **while the book still ticks, reduces and flattens**. `PMUS_MIRROR=exits` is the
**STOP**: books manage down, no new exposure, and the whale is copied by nobody. **`PMUS_MIRROR` unset is
NEITHER** — after R1 it alarms and honours a flatten, but it is still the mode in which nothing plans; the
runbook must never say "unset the flag to stop it".

**S0 — READ THE STATE WE ARE ACTUALLY IN. Authorises everything below; nothing is enabled.**
Three reads, all **unmeasured** here: `SELECT value FROM ingestion_state WHERE key='live_clip_overrides'`;
the two mapping-quarantine switches (`LIVE_MAPPING_QUARANTINE` and `ingestion_state.mapping_quarantine`); and
one `/positions` read for open legacy short inventory. **REVERSES:** if the clip row is ABSENT the buy lane is
running more whales at a higher clip than was decided, and **B1 becomes the first unit ahead of everything
else** — every gate below is denominated in dollars at a clip. The legacy-short read decides whether SH3's pair
and desk defects are live today or latent.

**S1 — LAND THE FIRST WAVE (R1, R2, R4, R5, R6, R7, A1, SH1).** No behaviour change visible to the venue; the
mirror is still SAFE, the per-fill lane still copies both whales.
**AUTHORISED BY:** the eight deterministic gates in §3 (orders reconciled on abandon with none left standing;
the env sweep downward-only for all seven names; one bad position row makes a walk unreadable and places zero
orders; `at_or_better` computable; `insufficient_cash` named with `MIRRORCASH` printing; cross-lane shares
explained with zero spurious freezes; `MIRRORINTEG` present with every M15 name at 0; the collateral price
table).
**REVERSES:** revert the commits — nothing is enabled, so a revert costs nothing.

**S2 — `PMUS_MIRROR=exits` WITH `PMUS_MIRROR_WHALES=` (empty), together.** A state change, not an enablement:
it moves the mirror out of SAFE so it can manage anything it holds, while the empty allowlist means no whale's
per-fill copies stop. **Beware the allowlist trap:** naming a whale in `PMUS_MIRROR_WHALES` who is not also in
`MIRROR_WHALES` yields an EMPTY intersection with no refusal name for the dropped whale, while the boot log
still reads armed.
**AUTHORISED BY:** R1's gate — the SAFE alarm prints, the flatten lever is reachable, abandons reconcile
first. **REVERSES:** back to unset, acceptable ONLY while `books_open_in_safe = 0`, which R1 now prints.

**S3 — LAND AND ENABLE THE SHADOW INSTRUMENTS (A2, A3, P1, P2, P3, X2).** Zero money.
**AUTHORISED BY:** `test_the_shadow_never_touches_an_order` green; A2's fixture gate; X2's two-lane fixture
matching truth to the cent. **REVERSES:** `MIRROR_SHADOW=off`; revert X2.

**S4 — THE SHADOW GATES PASS.** No build; a reading window.
**AUTHORISED BY:** P1's gate (`MIRRORSNAP.fresh_complete_share >= 0.95` and `net_drift_p90 <= 0.05` over
>= 30 planned markets in 24 h), P2's (`admissible_share >= 0.90`), P3's (`within_band_share >= 0.95`,
`cap_bound_share <= 0.05`), and `MIRROREXIT` printing at n >= 30.
**REVERSES:** any of those falling back over any 24 h window — return to S3. **No arming step below is
legitimate without P1's gate.** If drift p90 lands between 0.05 and 0.15, **the honest move is to take the
number to the owner, not to widen the gate.**

**S5 — LAND THE WAVE-BLOCKED CORRECTIONS: B1, B2, then E1, E2, E3, E4, E5, then X1, X3, SH2, SH3.** The buy
lane's and exit lane's fixes and the accounting go live BEFORE the mirror does, because the mirror's reversal
path returns the whale to the per-fill lane and it must be the fixed one.
**AUTHORISED BY:** B2's four-way funnel summing within 1%; B1's clip gate; **E1's 30-day replay at p90 <= 0.05
with zero 2× overshoots**; E2's mechanical allowlist test; E3's empty-page fault injection producing zero
exits; X1's reconciliation residual exactly zero with trims and exits present AND non-zero when a trim is
hidden; X3's `p2_verdict` returning zero unreadable keys.
**REVERSES:** revert. **B3 step 2 is deliberately NOT in this step** — it is measure-then-decide and waits on
7 days of B2's reading plus owner decision 5.

**S6 — THE VENUE RUNGS (V1) AT ONE BOOK AND THE SMALLEST CAP, ON RN1 — FIRST MONEY, AND A CUTOVER.**
Set `PMUS_MIRROR=on`, `PMUS_MIRROR_WHALES=rn1`, `MIRROR_WHALES=rn1`, `ingestion_state.mirror_live=true`,
`MIRROR_MAX_LIVE_BOOKS=1`, `MIRROR_MAX_BOOKS_PER_DAY=1`, `MIRROR_NET_CAP_USD` at the smallest rung,
**`MIRROR_TAKE_AFTER_S=86400`** (D-D: honoured because it is `min_wait_env`, and no caller can shorten it —
this disarms the take for the whole rollout so every order is a post-only maker rest),
`PMUS_MIRROR_POST_ONLY` untouched (R2 has made it one-way), `MIRROR_FLATTEN_REST_S` at its default (R3 has
fixed the sole-holder hazard on the one path that accepts slippage). **THIS IS THE STEP AT WHICH RN1'S
PER-FILL COPIES STOP, WHOLE-WHALE, FROM THE DEPLOY ALONE** (owner decision 1). Run rungs 1-4, 7-8, 11, 14,
16-17 only; **rung 5 does not run here.**
**AUTHORISED BY:** S1-S5 green, plus the stop lever rehearsed once before money (set `PMUS_MIRROR=exits`, read
the beat, set it back).
**REVERSES — `PMUS_MIRROR_WHALES=` (empty), one deploy, per-fill copies resume while the book still reduces —
on ANY of:** a `MIRRORINTEG` counter above 0 (`wrong_sign_trip`, `overfill`, `order_lost`,
`reaper_touched_mirror`, `post_only_ignored`, `mirror_overspend`, `books_open_in_safe`,
`close_position_with_foreign_shares`); a frozen book; `insufficient_cash` or a `place_refused:*` naming funds;
rung 7's settlement gap above $0.05; `at_or_better` below 1.00.

**S7 — WIDEN BUYS, REDUCTIONS AND EXITS ON THE SAME ONE WHALE.** On the mirror lane a sell IS a reduction of
the target and an exit is a reduction to zero, so all three go live together at S6 and this step only widens
the ladder: books 1 → 3 → 5 and the cap and day limit in step with them, **one rung per reading, never per
clock.**
**AUTHORISED BY, AND THIS IS THE CORRECTION OF THE FIELD'S MOST COMMON ERROR:** the first widening requires
only S6's reversing list clean over 48 h; **every widening beyond the second requires the >= 30-book
readings** — `MIRRORGRADE.rest_fill_lo >= 0.40` and `.maker_share >= 0.5` and `.at_or_better = 1.00`
book-clustered over >= 30 closed books, `MIRRORFIDELITY.by_usd >= 0.5` with react p50/p90 over >= 30 books,
`MIRRORTRACK` p90 <= 0.05, `MIRRORINTEG` all zero. That is 6+ days at five books a day plus a settlement
tail. **A metric below its minimum n authorises nothing.**
**REVERSES:** S6's list, plus `replace_capped > 0`, plus frozen ticks >= 1% — **step back one rung, never to
zero**, because stepping to zero means the reversal lever, not the strand.

**S8 — SHORTS ON THE PER-FILL LANE, ONE PROBATION SHORT AT A TIME.** Run V1 rung 5 (gated on SH1's collateral
preview, SH2's `short_reduce_unproven`, SH3's desk doors and X1's open-short clause), then set
`LIVE_ALLOW_SHORT=on` with **`SHORT_PROBATION_N` at its shipped 3** (D-G). Three 1-share BUY_SHORTs on a slug
the account holds nothing else on — the position-sign check abstains unless we are the sole leg — each exited
to a zero net position **by `close_position` only.**
**AUTHORISED BY:** rung 5's three lines from the venue; SH1's price table; SH3's deterministic gate (zero desk
orders reaching the venue without an intent, pair completion refusing a short sibling); X1's open-short clause
green; E3 landed so `_confirm_gone` reads both tokens.
**REVERSES — `LIVE_ALLOW_SHORT=off`, one deploy — on:** a position-sign read that disagrees with the sent
intent, **even once** (a stop, not a warning); a `preview_mismatch` on a correctly priced short; **a proof
short that `close_position` could not flatten**. Gate clause: **`SELL_SHORT sent = 0`.**
**The comment in `mirror_shadow.py` claiming a large number of sign-verified short fills must not authorise
this step** — it is unmeasured and contradicted inside this repository (SH5 deletes or qualifies it).

**S9 — THE MIRROR'S SHORT LEG (SH4, SH5).** Only after S8 has produced three sign-verified fills and one proven
reduce. The short leg opens at the SAME cap the long leg has reached; **shorts do not get their own ladder.**
**AUTHORISED BY:** `MIRRORSHORT.would_fill_short_lo >= 0.50` over >= 30 markets across 7 days; SH4's day-cap
arithmetic identical for a long and a short leg of equal collateral; then **>= 30 CLOSED short books** with
`at_or_better = 1.0` on the short leg and the three trips at zero.
**REVERSES:** the mirror short door alone (`short_side_refused` restored) — the long books keep running — on
any `wrong_sign_trip`, any `book_settle_disagree` on a clean book, any `overfill`, or any SELL_SHORT refusal
that is not `short_reduce_unproven`.

**S10 — LIFECYCLE AND RE-ENTRIES (L1), THEN SOCCER (L2), THEN THE SECOND WHALE.** Each its own deploy.
**AUTHORISED BY:** L1's gate (`wake_source_fill_share >= 0.85` with the copy lane disabled, wake-to-plan p50
<= 10 s, `off_tick = 0`, `replace_capped = 0`) and L2's (>= 30 closed soccer condition-books,
`book_settle_disagree = 0`, `game_cap` breaches 0).
**REVERSES:** narrow the allowlist, or `MIRROR_SOCCER_FLOOR=inherit`, on any clause failing over 24 h.
The second whale requires X3's operator surface and `p2_verdict` returning a verdict, not a build.

---

## 5. WHAT ONLY THE OWNER CAN DECIDE (each with a recommendation)

1. **THE COVERAGE HANDOVER AT S6.** `mirror_mode` reads the environment only and returns above every entry
   gate, so arming the mirror on rn1 stops his per-fill copies for ALL his markets in the same second. For the
   rollout window those copies are replaced by ONE book at the smallest cap. **This is a cutover, not an
   addition, and it is the largest single behaviour change in this programme.** Only he can accept it and only
   he can say how long the window may run before S7. *Recommendation: accept, with S7's first widening
   scheduled at 48 h on S6's reversing list — the alternative (a shadow-with-live-books mode) does not exist
   and would need building.*
2. **WHAT "100% WITH CONFIDENCE" IS MEASURED BY.** §3b's honest answer is that M19 — the only metric that can
   say a methodology copy WORKED — needs 30 books AND 30 games, and its own interval is not reachable inside
   any phase window (31,052 games at his edge; ±46.6 ROI points at 30 games, both recorded in §3b/§6).
   The confidence this programme can deliver is **FIDELITY** (M4-M15: are we holding what he holds, at his
   price, at his size, when he holds it, and can we get out), **not PROVEN EDGE**. The substitute has three
   parts: (a) invariants at n=1 — a violation is a stop, not a datum; (b) fidelity at n >= 30 books, roughly
   6 days plus a settlement tail; (c) M18 capture and M19 **reported with their intervals and never gated**.
   *Recommendation: he signs (c) explicitly, BEFORE the first live book, not after thirty of them. Widening
   M19's threshold to make it passable would be dishonest; so would shipping with no answer to "did it work".*
3. **THE ROLLOUT LADDER ITSELF** — book count, cap and day limit, and the rule that a widening beyond the
   second requires >= 30 book-clustered readings (§3b, S7). *Recommendation: sign the ladder and the
   minimum-n rule together; they are the same decision.*
4. **THE CLIP — three things he has not been told.** (a) The decided clip is exceeded by half on two whole
   market types, because the type multiplier is applied AFTER the stored clip (bounded by the max clip, so not
   a breach, but not what was decided). (b) If the clip row is ABSENT the lane runs more whales at the code
   default, silently, with no log line, and the roster reset lever produces exactly that state. (c) The rules
   can promote a whale to the promoted clip **unattended on the hourly pass** — an owner decision currently
   administered by a cron. **And the decision does not bound the mirror at all:** `mirror_target` clamps the
   clip scale at 1.0, so a $50 clip and a $250 clip produce the SAME mirror target; the mirror is sized by
   `MIRROR_NET_CAP_USD`, which needs its own signature (decision 3). *Recommendation: B1 makes (b) and (c)
   impossible; (a) is his call and my recommendation is that a stored clip is a ceiling.*
5. **WHETHER TO FIX THE CONVICTION DUST CLIFF, AND IN WHICH ORDER.** At the decided clip every buy below
   roughly half his own median notional is refused before any row is written — no row, no dollars, no durable
   name — and his own worked example, quoted in the source, is among the dropped. *Recommendation: measure
   with B2 for 7 days, fix the DENOMINATOR alone (its SQL includes his complement-buy exits while its
   docstring says it excludes them), re-read, then decide the floor. Changing both in one wave makes neither
   measurable.*
6. **THE BANKROLL, AND THE SPLIT OF ONE ACCOUNT BETWEEN THREE LANES** (§5 decisions 1 and 20). Blocks P4.
   Until it exists the per-market cap is the only sizer and "to a tee" cannot be claimed on size.
   *Recommendation: name it at S4, so P4 can land before S6.*
7. **THE LOSS STOP DURING THE ROLLOUT** (§5 decision 15). At the rollout's day ceiling the mirror stop will
   effectively never trip, so the protection is books × cap, not the stop. Note also that the mirror's stop
   sums LIFETIME realized P&L for any book touched in 24 h and adds settled P&L for books closed in 24 h, and
   a settled book satisfies both legs — it over-counts, which is the safe direction, **but it is not a P&L and
   must never be published as one.** *Recommendation: a tighter per-book stop for the window.*
8. **THE EXIT LEG** (§5 decision 18): hold to resolution versus a bounded take after N TTLs. §4 M14's
   threshold cannot be set until A3's `MIRROREXIT` prints. *Recommendation: ship hold-to-resolution (the
   paired flatten rests and is never marketed, and the take is disarmed for the whole rollout); revisit with
   the reading.*
9. **THE SOCCER PRICE FLOOR ON MIRROR BOOKS** (§5 decision 10): inherit / open_only / off-for-mirror. Added
   fact: the floor is denominated on HIS fill price and is not intent-aware, so it suppresses the SHORT class
   on soccer by construction even after S9. *Recommendation: `open_only` for P1, judged at the book's entry
   level and never re-judged on increases.*
10. **PER-WHALE EXITS** (copy-chain decision 5, "on for RN1, off for HomeRunHazard"). Unexpressible today
    without also removing the second whale's entry clip. E5 makes it expressible for the first time.
    *Recommendation: confirm the decision still stands; E5 defaults to today's behaviour so nothing changes
    on deploy.*
11. **WHETHER ANY LEGACY SHORT IS OPEN ON THE ACCOUNT RIGHT NOW.** **Unmeasured** here. It decides whether
    SH3's pair-completion sign blindness and the desk cash-out's long-math P&L are LIVE defects today or
    latent ones. One `/positions` read, and it belongs at S0.
12. **WHETHER THE PAIR-COMPLETION CARVE-OUT SURVIVES SHORTS.** It is a deliberate owner-ordered rule with
    locked economics, capped at shares already held, and it is the one place the entry lane knowingly takes a
    position outside his book. *Recommendation: the carve-out stays and SH3 makes it refuse a short sibling.*
13. **DECISION 13, THE SWITCH-ON STATE** (late entry). Deferring Rule LE means the first books buy into his
    pre-existing position at his newest level — option (B), which §5's own recommendation says never. Bounded
    by cap × books at S6. *Recommendation: accept for the rollout window at the smallest cap, or fund P4
    before S6 and move the first live order out by that much.*
14. **RESOLUTION-RULE MISMATCH POLICY** (§5 decision 17). *Recommendation unchanged: refuse basis and retire
    mismatches, flag walkover/cancel/window.*

---

## 6. HONEST RESIDUALS — what will still not be mirrored, and why

Everything in §6 of the mirror programme still stands and is not repeated. What this programme ADDS or
SHARPENS:

1. **The economic question is not answered by this programme and cannot be.** §3b records M19's horizon;
   every gate here is a fidelity gate. If his edge is not what the copy thesis assumes, every gate in §4 can
   pass and the sleeve can still lose money. Owner decision 2 is where that is faced, not engineered around.
2. **The per-fill lane's exit defects stay live through the handover.** E1-E4 fix them, but until S5 lands
   they spend money on rn1's legacy tail and on the second whale throughout — and after S6 they will be
   misread as mirror damage. They cannot touch a mirror book (`mirror_exit`'s row query excludes the mirror
   lane), which is why they are deferred rather than blocking, not because they are small.
3. **A one-book mirror does not replace a whole-whale per-fill lane.** S6's real cost is coverage, not risk:
   arming rn1 stops his copies whole-whale from the deploy alone and one book at the smallest cap does not
   replace them. This is the programme's biggest honest weakness and it is owner decision 1.
4. **The half-cent grid costs fill rate until V1 rung 11.** A cent wire rests half a tick under the bid on a
   large share of his mapped markets and simply does not fill. That is a **coverage loss and never an
   overpay** — the mirror's buy wire is the minimum of his price and the bid, floored to a cent, so the wire
   is at or below the bid by construction. **The answer to a low fill rate is the market's own tick, NEVER
   loosening post-only** (which R2 has now made impossible from a shell, and which would turn every rest into
   a crossable limit filling at placement in a locked book).
5. **A short that cannot be exited is the one sticky failure in this programme.** SH2's and step S8's ordering exist
   entirely for it: `close_position` only, on a sole-leg slug, no priced SELL_SHORT until a rung reads a
   resting one back. If that ordering is broken the failure is a position, not a number.
6. **His long-to-short flip is copied as flat, never as short, until S9** — the exit fraction caps at one and
   discards the surplus by design, the residual entry is suppressed, and a fresh short is refused. It books
   as a clean exit and is invisible in the exit census.
7. **Queue position, ladders, in-play cadence, void-class settlement and his pair-capture economics** are §6's
   and are unchanged: one rest per book against clustered ascending-cent fills, a replace budget below his
   cent-change rate, and a closed-lot blend a long-only net mirror cannot earn.
8. **Fees and rebates are charged nowhere.** Every P&L this system publishes is gross; the venue's commission
   fields are parsed and read by nothing outside the venue module, and the thesis meter subtracts a hardcoded
   zero for this venue against an unread fee coefficient. §4 M17 as defined measures PRESENCE, not
   reconciliation — it can read 100% while every published dollar stays gross. V1 rung 10 collects the
   values; **nothing in this programme charges them**, and the owner should be told the published figures are
   gross until it does.
9. **Three database facts this programme is authorised by were not read.** The clip map, the two mapping
   quarantine switches, and open legacy short inventory. S0 exists to settle them; each changes which unit is
   urgent rather than latent, and the clip map changes the first unit in the whole programme.
10. **What the reversal lever does not undo.** Emptying the allowlist resumes the per-fill lane and leaves the
    book reducing — but a book already frozen by a cross-lane collision (R7's class, before R7 lands) is not
    reduced by anything and has no admin unfreeze. R7 is therefore a precondition of the reversal being real,
    not a nicety, and there is still no unfreeze endpoint after it; that is a residual X3 could close and this
    programme does not.

---

## A. WHAT I VERIFIED IN THIS SESSION (read-only)

Every claim in §0 was read this session at the file and line given there. Specifically confirmed against the
CLEAN working tree (== HEAD for these files): `ml:2657-2662` (the SAFE return above `_global_guards`),
`ml:2674/2678/2682` above `ml:2685` (the abandon-before-reconcile ordering), `ml:697` (the flatten lever
inside `_global_guards`), `ml:2390` (`sole = ledger >= int(held)`) and `ml:2427` (`close_position`),
`ml:355` (`WHERE side = 'BUY_LONG'`), `ml:441-445` (`_SQL_MANUAL_SHARES` matching only `'manual'`),
`ml:185/272/2288` (the post-only latch and its raw env read), `ml:1703-1721` (`settled_pnl` written only on
the resolved path), `ml:1826/2496` (`last_fresh_agreed=True` literals), `ms:346-377`
(`account_positions`' parse-drop beside its truncation raise), `ms:74-109` (seven raw env reads),
`rules:162/190/259/430/501/1188/1204/1212/1375/1393/1399` , `pmus.py:36/2381/2383/2402`,
`migrations/047:84`, `migrations/049:32,37,42`, `analytics/proof.py:305`,
`api/app.py:5697/5709` and its 147 route decorators with zero `APIRouter`,
`.github/workflows/engine-diagnostic.yml:865`. Against HEAD via `git show HEAD:` (the file is dirty):
`live_executor.py` `SHORT_PROBATION_N` default 3 and the probation release at `done >= N`, and the full
membership of `EXIT_PENDING_REASONS` (neither `mx_exit_dedup_unreadable` nor `mx_exit_recently_applied` is in
it). Against the dirty working tree: `whale_exits._confirm_gone` returning True after the loop when the asked
token is absent although a complement row for the same condition was in the response.

No test suite was run for this document and no database or network was reachable. Every figure attributed to
production is marked **unmeasured** or cited to `docs/mirror-to-a-tee-program.md`.

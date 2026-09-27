# THE FAILING TESTS — controlled comparison, attribution, and effect

**Instrument:** pytest JUnit XML, parsed. Not grepped log lines.
**Dependency manifests captured with each run** (`pip freeze`, python, pytest).

---

## 1 · I called the new failures order-dependent. That was not established.

Isolated passes and a changing failure set do not establish causation, and a new
fixture can introduce cross-test interference. So a controlled comparison was run:
**same installed dependency versions** (`pycryptodome` present in both, verified by
diffing the captured manifests), same environment, same instrument.

| run | SHA | identities | source |
|---|---|---|---|
| baseline | `6d75275` | **152** | JUNIT_XML |
| release | `ea13389` | **155** | JUNIT_XML |

**3 new, 0 fixed — and all three were caused by my own credential fix:**

| identity | cause | repair |
|---|---|---|
| `test_desk_auth.py::test_unlock_mints_a_working_token` | signed in with `settings().desk_password`, which worked only because the shipped default was a password published in this repository | the file configures its own credential through a fixture — **not** by restoring a default |
| `test_wall.py::test_wall_unlock_mints_a_wall_token` | same | same |
| `test_healthz_boot_marker.py::TestTheBootMarker::test_the_old_fields_are_still_there` | asserted the `/healthz` payload by **equality**, so adding the two auth-posture fields failed a test whose stated purpose is that the OLD fields survive | asserts a subset; the new fields get their own assertions, so they are checked rather than tolerated |

**Confirming gate at `ea13389` after the repairs: 152 identities. Zero new, zero
fixed, against the same baseline.**

### And the two failures I had been discussing were never release-caused

`test_mirror_live_worker::test_t1...` and `test_s4_review_pins::test_after_a_cover_fill...`
appear in **both** paired runs. The original 177-identity baseline ran **without
`pycryptodome`**, which left 25 tests erroring at import and changed what ran
before them.

> **The valid baseline is 152, measured with the same dependencies. The 177 figure
> should not be quoted again**, and neither should the "25 fixed / 2 new" summary
> derived from it.

### A confound that cannot be equalised, recorded rather than hidden

The two SHAs differ in schema — **110 tables at baseline, 115 at release** —
because the release adds migrations. That is inherent to comparing two commits.

---

## 2 · The 152 failures by ACTUAL EFFECT

> **"Environment valid" and "safe application" are different conclusions.** The
> environment held for every run — `pg_down_samples=0`, 35 samples, VALID. That is
> a statement about PostgreSQL staying up, and it is not a statement about the
> application being verified.

| n | error class | what it actually means |
|---|---|---|
| **56** | `sportsassets.execution_gate.Denied: authorization_unavailable` | **the safety gate refusing** — "execution gate is not bound to a loop and pool, so the kill switch cannot be read from this process" |
| 48 | `AssertionError` | real behavioural assertions; need individual reading |
| 34 | `RuntimeError` | `There is no current event loop in thread 'MainThread'` — a toolchain/asyncio issue, not application logic |
| 9 | `ValueError` | mostly `substring not found` in the render-ops text fixtures |
| 4 | `TypeError`, `CalledProcessError`, `ForeignKeyViolationError`, bare asserts | individual |

### The 56 are a COVERAGE GAP, not a safety demonstration

The gate is doing exactly what it is designed to do: it **fails closed** when it
cannot read the kill switch. But the consequence for verification is the opposite
of reassuring:

> **56 tests that exercise the submission path cannot reach their subject in this
> environment.** The submission behaviour they were written to check is therefore
> **UNTESTED here** — the gate refuses before the code under test runs.

So "152 failures, none new" does **not** mean the capital path is verified. It
means 56 of its tests never got to run their subject. **The named repair** is to
bind the gate to a loop and pool in the test harness — `execution_gate.bind(loop,
pool)` exists for exactly this and is called once per process by whatever owns the
loop. That is real work and it is **not done**; it is recorded as the repair rather
than performed hastily, because a harness that binds a gate wrongly would make
the suite assert against a control that is not the production one.

---

## 3 · The capital-relevant ring, recomputed on the valid baseline

| measure | identities | files |
|---|---|---|
| **A** import closure (any depth) | 97 | 32 |
| **B** coupling symbols by name | **0** | 0 |
| **C** first ring (entry points + direct imports) | **92** | 29 |
| outside the closure | 60 | — |

Earlier figures (113 / 107) were measured against the invalid 177 baseline and are
superseded. The conclusion does not change: **my claim that none of the standing
failures affects the capital path is withdrawn**, and 92 identities sit where a
defect is at most one call from money.

**Crossing §2 with §3 is the point:** the largest single class in the ring is the
gate denying, which means the capital path's own tests are the ones not running.
That is a worse finding than a behavioural failure would be, because a failing
assertion at least tells you what is wrong.

---

## 4 · THE GATE HAS A NOISE FLOOR, AND I HAD NEVER MEASURED IT

Every "N new identities" claim in this project — including all of mine — assumes
the gate is a deterministic instrument. **It is not**, and the experiment that
shows it is the obvious one nobody had run: **the same SHA, twice.**

| run | SHA | identities |
|---|---|---|
| C2 | `118c8e6` | 154 |
| C3 | `118c8e6` | 154 |

Same count, **different set**:

| | identity |
|---|---|
| only in run 1 | `test_review_q7_a_call_cut_short_in_the_wait_is_all_wait_and_confirm_gone_is_byte_identical` |
| only in run 2 | `test_two_concurrent_submissions_cannot_both_reach_the_venue` |

Both names describe **timing and concurrency**, which is where the nondeterminism
would be expected to live.

> **The instrument's noise floor is at least ±1 identity per run.** So a reported
> delta of one or two new identities is *within the noise* and cannot by itself be
> attributed to a code change. Every such delta I have quoted — and I have quoted
> several — needed a same-SHA control to mean anything, and did not have one.

### What this changes about the release discipline

"No new failure identities" is only regression evidence **above the noise floor**.
The gate should be run twice on the release SHA, and a delta treated as real only
when it reproduces. That is now the standard this evidence file holds itself to.

## 5 · The one candidate that DOES reproduce — left explicitly unresolved

`tests/test_s4_review_pins.py::test_after_a_cover_fill_the_ledger_moves_toward_zero_never_past_it_realized_is_the_short_formula_and_the_shadow_agrees`

* **reproduces in BOTH runs of `118c8e6`**, so it is above the noise floor;
* absent from the `152` baseline and from the `ea13389` run;
* the failure is `AssertionError: assert 'closed' == 'live'` — a position whose
  state is `closed` where the test expects `live`. That is **cross-test database
  state**, not arithmetic: the gate gives each run a fresh database, so the
  pollution is *within* the run and depends on collection order.

**I cannot attribute it to the code delta at this SHA.** That delta is confined to
the session-epoch signing change in `api/app.py` and `config.py`, which has no
coupling to position state — the test file contains zero references to tokens,
`admin_token` or the epoch.

> **So it is recorded as UNRESOLVED rather than explained.** The candidate
> explanation — that three tests appended to a file collecting earlier shifted
> which fixtures ran before it — is a hypothesis I have not tested, and the
> earlier lesson here is precisely that a plausible ordering story is not
> attribution.

**The repair path** is the same one §2 names for the 56 gate denials: the suite
needs per-test isolation of position state. Until that exists, this class of
failure will keep moving around, and the honest reporting is a noise floor plus a
named exception rather than a clean number.

### A parser trap worth recording, because it nearly inverted this finding

My first diagnostic read `c.find("failure") or c.find("error")`. **An
`ElementTree` element with no children is FALSY**, so an empty `<failure>` falls
through to `error`, returns `None`, and the test is reported as **PASSED**. It told
me this failure did not exist. The gate's own parser uses
`case.find(t) is not None` and was never wrong; the diagnostic I wrote to check the
gate was. Corrected to an explicit `is not None`.

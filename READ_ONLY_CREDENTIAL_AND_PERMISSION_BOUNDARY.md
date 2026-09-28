# The actual credential, process and database boundary

**Directive answered:** *"Close the `_mod` leak and document the actual credential and permission boundary."* · *"Fix `_mod`, but do not replace it with another Python wrapper and call that isolation… Enforce the intended separation through the relevant credential, process and database permissions."* · *"Do not describe an in-process object as security isolation."*

Generated from `bettor_read_only_venue.authority_boundary()`, which probes what it can and marks the rest NOT_ESTABLISHED. Every "OPEN" below is measured live by `reflection_paths_still_open()`, not asserted in prose.

---

## 1 · The `_mod` leak is closed. That did not create a boundary.

`ReadOnlyVenue` held the venue module in a `_mod` slot. `__slots__` makes that a **real attribute**, so ordinary lookup found it, `__getattr__` was never consulted, and `v._mod.submit_fok` handed back every mutation. The allowlist was one underscore deep. My own tests missed it because they checked `hasattr` and `setattr` — never the escape hatch the design itself created.

It now holds only the allowlisted **bound read functions**, resolved once at construction. `v._mod` raises `AttributeError`.

**Four in-process paths still reach `pmus.submit_fok` while holding only a `ReadOnlyVenue`:**

| Path | State |
|---|---|
| `getattr(v, "submit_fok")` | CLOSED |
| `v._mod.submit_fok` *(the removed slot)* | CLOSED |
| `object.__getattribute__(v, "_reads")[…].__globals__["submit_fok"]` | **OPEN** |
| `v.slug_bid.__globals__["submit_fok"]` | **OPEN** |
| `from sportsassets import pmus` | **OPEN** |
| `sys.modules["sportsassets.pmus"]` | **OPEN** |

Each is one line of ordinary Python. **In-process Python affords no boundary against code running in the same interpreter.** Calling this object isolation would be the same category error as the `_mod` slot it replaced.

`tests/test_the_read_only_interface_cannot_mutate.py` now asserts these four are **OPEN** — so if one closes, the test fails and forces a re-measurement instead of letting a stale reassurance sit there.

**What the object is:** an ergonomic guard. It stops the diagnostic call site that never intended to submit — the failure mode this repository has actually shipped twice — and it stops nothing that intends to.

---

## 2 · Credential — the only real boundary, and its granularity is unknown

Order authority comes from `PMUS_KEY_ID` / `PMUS_SECRET_KEY` (`submission_surface.CREDENTIAL_SETTINGS`).

**Enforced by absence.** A process without these cannot sign a venue request whatever object it holds. This is the strongest control in the system, and the only one that does not depend on our own code being correct.

**Granularity: NOT_ESTABLISHED.** No read-only venue credential has been inspected. Whether the venue issues a key that reads quotes and holdings but cannot submit is **unknown**, and per standing instruction this repository does not assume such a capability exists. **Until a key is issued and its refusals observed, treat any present credential as able to trade.**

---

## 3 · Process — ten gates, all of which depend on our source being right

`submission_surface.GATES` enumerates ten:

| # | Gate | Kind |
|---|---|---|
| 1 | `FUNDED_SUBMISSION_ENABLED` | CODE_CONSTANT |
| 2 | `REAL_ORDER_SUBMISSION_ENABLED` | CODE_CONSTANT |
| 3 | `FUNDED_EXIT_SUBMISSION_ENABLED` | CODE_CONSTANT |
| 4 | `POLICY_ADMISSION_ENABLED` | CODE_CONSTANT |
| 5 | `execution_gate` | PROCESS_BOUND_GATE |
| 6 | a recorded submission authorization | DATABASE_ROW |
| 7 | an ELIGIBLE, reconciled account | DATABASE_STATE |
| 8 | a readable exposure total inside its cap | MEASUREMENT |
| 9 | the venue credential | CREDENTIAL |
| 10 | the funded schema | SCHEMA |

Gate 5 is the structurally strongest of the nine non-credential gates: `execution_gate` is invoked **inside** `pmus.submit_fok` and `pmus.close_position` rather than at the call sites, so it covers callers that pass the function as a callable to `asyncio.to_thread` — which no call-site check caught. Its denial **raises** rather than returning. Its kill switch is `live_trading_paused` in `ingestion_state`, read at submission time, fail-closed on an unreadable row.

**Process separation is a convention, not a permission.** The learning loops, the API and the workers are **not separate security principals**. They differ by which code they run, not by what they are permitted to do. A worker that imported `pmus` and held the credential settings would submit; nothing outside our own source prevents it.

---

## 4 · Database — no separation at all

**One credential: `DATABASE_URL`.** Verified by walking every `.py` in the package for `*DATABASE_URL*` — exactly one setting name, and a test pins that count so a second DSN cannot appear silently.

**There is no read-only database role.** Every process that reaches the database holds the same credential, so every process can write every `ingestion_state` key — **including `live_trading_paused` and `mirror_loss_stop`, the rows the kill switch and the loss breaker are read from.** A learning loop whose legitimate job is to upsert its own heartbeat row is, at the permission level, able to clear the kill switch.

### Worked example, and a correction to my own first version of it

`workers/rn1x_model_loop._heartbeat` issues `INSERT INTO ingestion_state (key, value) … ON CONFLICT DO UPDATE` with the key as a **bound parameter**, so the statement will write any key.

I first wrote here that **no caller passes another key**. **That was false.** One caller does: `key=STANDBY_KEY`, to keep a standby out of the writer's row. A single-line grep for `_heartbeat(.*key=` missed it because the `key=` sat on the following line; the AST test that replaced the grep found it immediately.

The conclusion is unchanged but the stated reason was wrong. The correct reason: both keys any caller names are **module-level literal constants holding that loop's own two rows** (`rn1x_model_last_cycle`, `rn1x_model_last_cycle_standby`); no call site computes a key or takes one from input. So the loop does not write a control row — because of the two constants its callers happen to name, **not because of a grant**. The same connection would accept the switch's key.

A test now walks the call nodes by AST: every `key=` must be a bare module-level `Name`, its constant must be one of the loop's own rows, and it must not be a control key.

### The one place this was deliberately narrowed

The research-shadow arm/disarm route in `api/app.py` accepts **no key parameter**, expressly so it cannot become a general `ingestion_state` writer and put `live_trading_paused` one admin request away. That is the right instinct applied at one endpoint. It is not the database's doing.

### What would make it a permission — NOT IMPLEMENTED

A second role with `SELECT` on everything and `INSERT`/`UPDATE` restricted away from the control keys — a separate table owned by the admin role, or column-level grants plus a trigger — handed to the loops through a second DSN setting. **Stated as the remedy, not as a control.**

---

## 5 · Honest summary

One real boundary (the credential's absence), one lane of in-code gates that depend on our source being right, and a database with no separation at all. The read-only interface is none of these; it is an accident guard.

**This section is not a completion claim.** The database remedy is unimplemented, the credential's granularity is unestablished, and nine of the ten gates are only as good as this repository's source.

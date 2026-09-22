# Probe 2 — preflight record

Candidate `ba870764f847e31b2704c5315ba53f7907b5c687`, deployed
2026-09-22.

## Preconditions, each measured before the next step

| check | method | result |
|---|---|---|
| candidate SHA | `git rev-parse HEAD` | `ba87076…` matches the approved string exactly |
| working tree | `git status --short` | clean |
| ancestry | `git merge-base --is-ancestor e8f616a HEAD` | **YES** — fast-forward, 17 commits, no force |
| observation stopped | `render-ops sql obs-state`, run 35735369492 | `bettor_live_observation = false` |
| trading controls | `render-ops sql pause-state`, run 35735390684 | `live_trading_paused = true` — **unchanged** |
| prior probe preserved | `render-ops sql obs-budget`, run 35735379839 | saved to `../live_probe_3c413436/BUDGET_ROW_AT_PRESERVATION.json` |
| deregistration prepared | `scripts/bettor_deregister.py` | `64befcd`, 1 ahead / 0 behind, AST-verified, pushed untracked |

## Deployment

```
git push origin ba870764f847e31b2704c5315ba53f7907b5c687:refs/heads/claude/session-njaewf
   e8f616a..ba87076   (fast-forward, no force)
```

| service | deploy window | status | commit |
|---|---|---|---|
| sportsassets-api | 13:44:59 → 13:46:04 | **live** | `ba87076` |
| sportsassets-workers | 13:44:59 → 13:45:49 | **live** | `ba87076` |

Worker idle and DB-controlled throughout:
`not observing (STOPPED_BY_CONTROL: bare boolean false)`.

## A FINDING: `env-set` did NOT trigger a redeploy

The `render-ops` workflow prints, after every `env-set`:

> `(Render redeploys the service on an env change; read 'deploys' to
> follow it)`

**It did not.** Both `env-set` calls returned HTTP 200 and the values
were stored:

```
13:48:48  env-set BETTOR_PROBE_MAX_RPS      HTTP 200   (4 chars, masked)
13:48:5x  env-set BETTOR_PROBE_SAMPLE_SEED  HTTP 200
```

but the deploy list at 13:52:43 still showed the 13:44:59 `new_commit`
deploy as newest, and the worker's own log at 13:51:22 still reported:

```
effective config {... "max_rps": 0.25 ...}
```

Three minutes after the variable was stored, the running process had
never seen it.

**Why this mattered.** The authorization specifies 0.10 req/s shared
across all acquisition. Arming on the strength of the HTTP 200 would
have run the probe at **0.25 req/s** — two and a half times the
approved rate — and spent the single authorized probe identity on a
run that violated its own limits. The stored value and the running
value are different facts, and only the second one is the venue's
experience.

Resolved with an explicit `render-ops restart confirm=DO`, and the
effective config is re-read from the worker's own log before arming.

**The general rule this is an instance of**: a configuration is what
the process reports, never what the API accepted.

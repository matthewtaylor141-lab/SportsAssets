# `render-ops.yml` is against a hard 512,000-byte ceiling

**Measured, not inferred, on 2026-09-23.**

| commit | file size | dispatch |
|---|---:|---|
| `39ff4f8` | 511,805 B | ran normally |
| `3329590` | **513,127 B** | **`startup_failure`** |

The only change between them was one added `sql` case (+1,322 B). The
run was created and immediately failed *before any step executed*, so
no log, no annotation in the job output, and nothing wrong with the
YAML: `yaml.safe_load` parsed it and `bash -n` accepted the rendered
`run:` script at both sizes.

**The ceiling is 512,000 bytes — 512 KB, decimal, not 512 KiB.**
513,127 is over 512,000 and under 524,288, and it failed.

## Why this is easy to misdiagnose

`startup_failure` looks exactly like a syntax error, and every local
check passes, so the natural next move is to hunt for a quoting bug in
the statement that was just added. There is no quoting bug. The file
simply got too big, and the statement that "broke" it is only the one
that happened to cross the line.

**The first thing to check after a `startup_failure` on this workflow
is `wc -c .github/workflows/render-ops.yml`.**

## Adding a case

Every new `sql` case costs its own length plus its entry in the `action`
description comment. Before pushing one:

    wc -c .github/workflows/render-ops.yml     # must stay < 512000

Current headroom is roughly 1 KB, which is **less than one typical
case**. So a new case now requires removing an old one, and one-shot
diagnostics should be removed once their finding is captured somewhere
durable.

`neg-qty` was removed on 2026-09-23 for exactly that reason. Its
finding — `mirror_candidate_refusals.target` is signed, id 42451 carried
−25, and one such row refused the whole COMMAND snapshot — lives in run
`35868626142`'s log, in the commit that repaired `decision_row`, and in
ten regression tests that carry the record shape. **The case was the
instrument, not the evidence.**

## The real fix, when there is time

Four single statements exceed 20 KB and one is 96 KB. Those belong in
files the job reads, not in the dispatch matrix. That is a refactor
with its own risk and it is not being done under a deadline; this note
exists so the next person hitting `startup_failure` loses minutes
rather than hours.

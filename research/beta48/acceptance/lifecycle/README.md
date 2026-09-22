# Lifecycle runs — one directory per run

Every run writes into `run_<sha7>_<utc>/` and finishes by writing
`manifest.json` **last**.

**A directory without a manifest is an interrupted run.** That is the
whole point of writing it last, and it is why results can no longer be
mixed.

## What went wrong before

Runs shared this directory flat and overwrote phase files one at a
time. Mid-run — or after an interruption — it held `L1..L6` from the
current run beside `L8..L11` from the previous one, with nothing in
the files saying which was which. File timestamps were the only clue
and they do not survive a checkout. A commit taken at the wrong moment
would have recorded a mixture as a single result.

## Reading a run

`manifest.json` binds the run to its code and its image:

```
run_id, sha, working_tree_modified_paths,
image, image_id, dsn_database,
checks_passed, checks_failed, phases_failed,
phases: { "L1": {"pass": n, "fail": n}, ... }
```

`working_tree_modified_paths` is non-zero when the run was taken
against a tree that did not match its SHA — read the result
accordingly.

Every result is **SIMULATED TRANSPORT**. It is not a live venue
connection and must never be reported as one.

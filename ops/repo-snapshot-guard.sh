#!/bin/bash
# PreToolUse guard: never let an edit land on a reverted snapshot checkout.
#
# WHY. On 2026-08-30/31 this CCR container restored an Aug-24 snapshot SEVEN
# times in one session. Each restore rewinds the working tree to f7034b1
# (live_executor.py: 5,964 lines -> 1,776), wipes /tmp, resets git's
# remote-tracking refs and drops stashes. Editing afterwards applies changes
# to week-old code; reading `git show origin/<branch>:file` afterwards reads
# the STALE ref and reports pushed work as missing. Both happened, and both
# produced wrong statements to the owner.
#
# WHERE IT LIVES, AND WHY THAT MATTERS. The first version of this lived in
# ~/.claude/ because the hooks there appeared to survive. They do not: the
# launcher RE-PROVISIONS its own files on every container start, and their
# mtimes move with it. That looked identical to persistence and was not —
# the guard written there was destroyed by the next restore, along with the
# settings.json wiring it. The only storage that outlives a restore is the
# git remote, so the guard is committed to the repo it protects.
#
# STATELESS BY DESIGN. Nothing local persists, so this cannot rely on a
# recorded high-water SHA — the previous version did, and the restore ate
# it. The freshness marker lives in /tmp instead, and /tmp being wiped is
# the FEATURE: a missing marker is exactly the signal to re-verify against
# the remote. Steady state is one stat() and no network; a new commit or a
# restore costs one fetch.
#
# SAFETY. Fast-forward only — never reset --hard, never checkout --. A dirty
# tree is stashed (labelled, recoverable) before the fast-forward, so nothing
# is discarded. Anything it cannot resolve cleanly BLOCKS the edit rather
# than letting it land on stale code.
#
# KNOWN GAP, stated rather than papered over: immediately after a restore the
# working tree sits at a commit that predates this file, so the hook is
# unwired until the first resync. It self-heals the moment the branch is
# fast-forwarded. In that window the launcher's own Stop hook — which IS
# re-provisioned, so it always exists — is what reports the dirty tree, and
# it caught all seven restores.

set -uo pipefail
payload=$(cat 2>/dev/null || true)
fpath=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' 2>/dev/null)
if [[ -n "$fpath" ]]; then dir=$(dirname "$fpath"); else dir="$PWD"; fi
[[ -d "$dir" ]] || dir="$PWD"

root=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || exit 0
branch=$(git -C "$root" rev-parse --abbrev-ref HEAD 2>/dev/null) || exit 0
[[ "$branch" == "HEAD" ]] && exit 0
head=$(git -C "$root" rev-parse HEAD 2>/dev/null) || exit 0

slug=$(printf '%s@%s' "$root" "$branch" | tr '/ ' '__')
marker="/tmp/.repo-guard-${slug}-${head}"
[[ -f "$marker" ]] && exit 0

git -C "$root" fetch origin "$branch" --quiet 2>/dev/null
remote=$(git -C "$root" rev-parse "origin/$branch" 2>/dev/null)
if [[ -z "$remote" ]]; then : > "$marker"; exit 0; fi

# At or ahead of origin — normal, including unpushed local commits. The Stop
# hook nags about those; this guard does not.
if [[ "$head" == "$remote" ]] || git -C "$root" merge-base --is-ancestor "$remote" "$head" 2>/dev/null; then
  : > "$marker"; exit 0
fi

if ! git -C "$root" merge-base --is-ancestor "$head" "$remote" 2>/dev/null; then
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"repo-guard: HEAD (%s) and origin/%s (%s) have diverged - neither is an ancestor of the other. Not auto-resolving under an edit. Inspect: git -C %s log --oneline -3"}}\n' "${head:0:7}" "$branch" "${remote:0:7}" "$root"
  exit 0
fi

note="repo-guard: HEAD was ${head:0:7}, behind origin/$branch (${remote:0:7}) - the container reverted the checkout."
stashed=""
if [[ -n "$(git -C "$root" status --porcelain 2>/dev/null)" ]]; then
  if git -C "$root" stash push -u -m "repo-guard auto-stash $(date -u +%FT%TZ)" --quiet 2>/dev/null; then
    stashed=" Uncommitted files were stashed (git stash list) - nothing discarded."
  else
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s Could not stash the dirty tree, so the resync was not attempted and this edit would land on stale code. Resolve by hand: git -C %s status"}}\n' "$note" "$root"
    exit 0
  fi
fi

git -C "$root" merge --ff-only "origin/$branch" --quiet 2>/dev/null
now=$(git -C "$root" rev-parse HEAD 2>/dev/null)
if [[ "$now" == "$remote" ]]; then
  : > "/tmp/.repo-guard-${slug}-${now}"
  printf '{"systemMessage":"%s Auto-resynced to %s.%s"}\n' "$note" "${now:0:7}" "$stashed"
  exit 0
fi
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s The fast-forward did not restore it (still at %s). This edit would apply to a stale checkout, so it is blocked. Recover: git -C %s fetch origin %s && git -C %s merge --ff-only origin/%s"}}\n' "$note" "${now:0:7}" "$root" "$branch" "$root" "$branch"
exit 0

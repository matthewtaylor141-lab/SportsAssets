#!/bin/sh
# Point git at the tracked hooks directory. Run once per clone.
#
# core.hooksPath is LOCAL CONFIG, not tracked, so a fresh clone has no hooks
# until this runs -- which is the one weakness of hook-based enforcement and the
# reason .github/workflows/commit-guard.yml exists as a backstop.
set -e
root=$(git rev-parse --show-toplevel)
git -C "$root" config core.hooksPath .githooks
echo "core.hooksPath = .githooks"
echo "guard active: a push of any commit without [skip render] or [deploy-approved] is refused"

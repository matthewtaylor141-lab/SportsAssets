# BUILT FROM THE REPOSITORY ROOT, NOT FROM backend/.
#
# The image must contain the tested EV research modules, because
# `sportsassets.bettor_ev_bridge` imports them rather than carrying a
# copy -- and a copy drifts. They live outside backend/, so the build
# context has to be the repository root:
#
#     rootDir: .
#     dockerfilePath: ./backend/Dockerfile
#
# Every COPY below is therefore relative to the REPOSITORY ROOT. A path
# that reads `sportsassets` rather than `backend/sportsassets` is a
# leftover from the old context and will fail the build rather than
# silently ship the wrong tree.

FROM python:3.12-slim

WORKDIR /app

COPY backend/pyproject.toml ./
COPY backend/sportsassets ./sportsassets
COPY backend/migrations ./migrations
COPY backend/start.sh ./

# THE WHOLE shadow/ DIRECTORY, DELIBERATELY. Ten modules are needed
# transitively today (action_ev, action_ev_mc, ev_core, inventory_state,
# maker_fill, position_state, prior_registry, bettor_dataset,
# capture_quality, tick_semantics) and the directory is ~7 MB. Copying
# only those ten would mean a new transitive import passes every local
# test and fails in production -- the exact split between what is
# tested and what is shipped that importing-instead-of-copying exists
# to prevent.
COPY research/beta48/shadow ./research/beta48/shadow

# THE FROZEN WHALE PRIORS, which live one level up from shadow/.
# WHALE_REFERENCE_PRIORS_V1.json carries COMPLETION_HAZARD_BY_ACCOUNT_
# AND_INTERVAL and -- in the same file, on every row -- the
# restrictions that govern it: IS_NOT = BETTOR_P_FILL,
# MAY_SEED_BETTOR_P_FILL = false, the selection condition and the CI95.
# The data and its limits travel together, so the file is copied whole
# rather than having its numbers extracted into code where the
# restrictions would not follow them.
COPY research/beta48/*.json ./research/beta48/

# THE CAPTURED INCENTIVE MANIFEST, AND ONLY IT.
#
# THE DEFECT THIS FIXES. The line above globs `research/beta48/*.json`,
# which is TOP-LEVEL ONLY -- it does not descend into `acceptance/`.
# The activation sequence declared
# `BETTOR_INCENTIVE_MANIFEST=research/beta48/acceptance/incentive_manifest.json`,
# and against WORKDIR /app that path simply does not exist in the
# image. The worker would have refused to start with MANIFEST_ABSENT on
# the first boot after deployment. A repository-file check could never
# have caught it, because the file IS in the repository.
#
# COPIED NARROWLY, not by copying `acceptance/` whole: that directory
# is 4.9 MB of patches, suite XML and result JSON that the runtime has
# no use for, and shipping it would put evidence artifacts inside a
# production image.
#
# AND THE BUILD FAILS IF IT IS ABSENT. `COPY` of a missing literal path
# is a build error, so "the manifest is not in the image" becomes a
# failed build rather than a worker that starts and refuses. That is
# the direction this release wants the failure to point.
COPY research/beta48/acceptance/incentive_manifest.json \
     ./research/beta48/acceptance/

# THE INCENTIVE SCORER, for the same reason and with the same narrowness.
#
# The manifest above is the programme's TERMS. Scoring an observed ladder
# against those terms needs `bettor_incentive_score`, which sits beside it
# at research/beta48 -- and the *.json glob above does not bring code.
#
# WHY THIS IS SHIPPED RATHER THAN REIMPLEMENTED. The command centre has to
# run the opportunity analysis on the ladders the collector actually
# observed, and the only alternative to shipping this file is writing a
# second scorer inside `sportsassets/`. Two scorers disagreeing about a
# reward share is worse than one file in an image, and the repository's own
# rule -- reuse the engine, never build a parallel one -- says which to
# choose. The driver is NOT copied: it is a CLI, and the endpoint calls
# `measure` directly.
COPY research/beta48/bettor_incentive_score.py \
     ./research/beta48/
COPY research/beta48/bettor_incentive_opportunity.py \
     ./research/beta48/

# The bridge resolves this FIRST, before any parent-directory guess.
# Source-tree parent arithmetic has no reason to hold inside an image
# whose layout a Dockerfile chose.
ENV BETTOR_RESEARCH_ROOT=/app/research/beta48/shadow

RUN pip install --no-cache-dir . && chmod +x start.sh

CMD ["sh", "start.sh"]

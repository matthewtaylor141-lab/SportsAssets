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

# The bridge resolves this FIRST, before any parent-directory guess.
# Source-tree parent arithmetic has no reason to hold inside an image
# whose layout a Dockerfile chose.
ENV BETTOR_RESEARCH_ROOT=/app/research/beta48/shadow

RUN pip install --no-cache-dir . && chmod +x start.sh

CMD ["sh", "start.sh"]

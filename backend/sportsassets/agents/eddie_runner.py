"""HISTORICAL IMPORT PATH (migration 266): the execution agent EDDIE was
renamed ARCHER. A thin shim -- `from .agents import eddie_runner` returns the
very `sportsassets.agents.archer_runner` module object. New code imports
`archer_runner`."""
import sys

from . import archer_runner as _archer_runner

sys.modules[__name__] = _archer_runner

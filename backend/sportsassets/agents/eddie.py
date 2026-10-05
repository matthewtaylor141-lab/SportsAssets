"""HISTORICAL IMPORT PATH (migration 266): the execution agent EDDIE was
renamed ARCHER. This module is a thin shim -- `from .agents import eddie`
returns the very `sportsassets.agents.archer` module object (same functions,
same constants, same state), so nothing can diverge between the two names.
New code imports `archer`."""
import sys

from . import archer as _archer

sys.modules[__name__] = _archer

"""Pure SHADOW reference functions for edge-decay analysis."""
from __future__ import annotations

def retention(initial_edge: float, edge: float):
    if initial_edge is None or initial_edge <= 0 or edge is None:
        return None
    return edge / initial_edge

def first_crossing(samples, fraction):
    """samples = [(seconds_since_detection, executable_edge)]."""
    if not samples:
        return None
    s=sorted(samples)
    e0=s[0][1]
    if e0 is None or e0 <= 0:
        return None
    target=e0*fraction
    for t,e in s:
        if e is not None and e <= target:
            return t
    return None

def edge_half_life(samples):
    return first_crossing(samples,0.5)

def time_to_zero(samples):
    if not samples:
        return None
    for t,e in sorted(samples):
        if e is not None and e <= 0:
            return t
    return None

def pipeline_loss(initial_ev_usd, final_ev_usd):
    if initial_ev_usd is None or final_ev_usd is None:
        return None
    return initial_ev_usd-final_ev_usd

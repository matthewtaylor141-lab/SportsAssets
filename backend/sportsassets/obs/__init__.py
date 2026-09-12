"""RN1 forward observability -- run 83.

PASSIVE BY CONSTRUCTION. Nothing in this package imports an order client, an
executor, or any module that can reach one. That is not a convention: it is
asserted statically by tests/test_obs_safety.py, which walks this package's
transitive imports and fails the run if an order egress module appears, and
again dynamically by a test double that fails if an order API is ever called.

The package exists because run 82 established that physical actionable latency
is NOT IDENTIFIABLE from retained data -- every source-anchored boundary crosses
an unmeasured clock offset -- and that this can only be fixed forward.
"""

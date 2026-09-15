"""Reporting: figures computed from the ledger, and the document they back.

The problem this package exists to solve is that a methodology document with
hand-typed numbers goes wrong quietly. PHILOSOPHY.md carried a moneyline
sample count of 95 in one section, 126 in a config comment and 100 in the
live engine; a draw surcharge of 2.71c against a measured 2.46c; a
retention n of 201 in one table and 196 in another. None of those were
careless — each was correct on the day it was written, and the document has
no way to notice when it stops being.

So: figures.py computes every measured number from the ledger, each carrying
its own sample count and a reason when it cannot be computed, and
methodology.py renders the document around them. The prose is authored; the
numbers never are.
"""

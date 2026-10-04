"""THE BETTOR INDEPENDENT PROFITABILITY LAB -- additive research modules.

Every module here is SHADOW / READ-ONLY / RESEARCH FIRST (the lab brief): it
imports nothing from an order, venue, execution or funded module, no decision
path imports it, and nothing it writes is read by a decision path. Pure
functions live in their own files; database readers and writers are separate;
read-only endpoints are under GET /api/command/lab/<track>.
"""

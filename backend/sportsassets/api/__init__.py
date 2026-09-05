"""The API package. Its log handler lives in api/app.py, NOT here.

It lived here for a few hours on 2026-09-05 and silenced the WORKERS.
This package is not the API process's alone: the workers import
sportsassets.api.copies_record (whale_exits, analytics, the poller)
and sportsassets.api.pmus_account / track_record (analytics) at run
time, and a package __init__ runs on the first of those imports in
WHATEVER process makes it. The API's handler set httpx and httpcore to
WARNING -- right for a process whose board sweeps make dozens of venue
calls a minute -- and the workers, whose per-call httpx lines are how
venue outages are read, went quiet from their first whale_exits cycle
after the deploy: the mirror's first ON ticks were abandoned on three
empty quotes and the log carried no HTTP line to say what the venue
had answered. Process-wide logging configuration belongs in the
process's entry module (api/app.py for uvicorn, workers/__init__.py
for the workers), never in a package that both processes import.
"""

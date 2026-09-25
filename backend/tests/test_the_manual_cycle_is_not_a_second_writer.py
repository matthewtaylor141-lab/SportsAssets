"""AN ADMIN ROUTE THAT DRIVES A CYCLE MUST NOT BECOME A SECOND WRITER.

THE DEFECT THESE TESTS CLOSE, AND IT WAS MINE. I added
`POST /api/admin/rn1x-run-management` so a verification run could produce
management decisions without waiting out the 900 s cadence, and argued it
was safe because it calls `run_continuing_management` -- the scheduler's own
function. That argument is wrong.

`rn1x_shadow.run` holds `pg_advisory_lock(LOCK_KEY)`. The lock is
SESSION-scoped, held on one dedicated connection for the process's whole
life, and -- by that module's own docstring -- that loop is HOSTED IN THE
SAME API. So a route that takes a pool connection and calls the function
directly is a second writer on one book: two `manage_open_positions` runs
can select the same position concurrently and both place an order. Invoking
the scheduler's function is not the same as holding the scheduler's
ownership, and calling one "the deployed recurring loop" does not make it so.

WHAT IS PINNED HERE:

  * the route contends for the SAME key the writer loop uses, never its own;
  * it uses `pg_try_advisory_lock`, so it never blocks behind the writer;
  * failing to acquire is a REFUSAL that writes nothing and names the holder;
  * an unreadable probe also refuses -- unknown ownership is not permission;
  * the lock is released on the SAME connection that took it, because a
    session lock leaked onto a pooled connection would make the scheduled
    writer a permanent standby on its next restart;
  * every cycle it does drive is LABELLED as manually triggered, so a
    decision produced here is never read as one the schedule produced.
"""

from __future__ import annotations

import inspect

from sportsassets.api import app as APP
from sportsassets.workers import rn1x_shadow as W

SRC = inspect.getsource(APP.admin_rn1x_run_management)


def test_it_contends_for_the_writer_loops_own_key():
    """Its own key would be no lock at all -- two locks, two writers."""
    assert "W.LOCK_KEY" in SRC
    # AND THE KEY IS THE WRITER'S, read from the module rather than copied.
    assert isinstance(W.LOCK_KEY, int)
    assert "pg_try_advisory_lock" in SRC


def test_it_never_blocks_behind_the_writer():
    """`pg_advisory_lock` would wait, holding a request open until the
    scheduled loop exited -- which is never, by design."""
    assert "pg_try_advisory_lock" in SRC
    assert "SELECT pg_advisory_lock($1)" not in SRC


def test_not_acquiring_the_lock_is_a_refusal_that_names_the_holder():
    assert "SCHEDULED_WRITER_HOLDS_THE_LOCK" in SRC
    assert "SECOND writer" in SRC
    # AND IT SAYS WHAT TO DO INSTEAD, rather than only that it refused.
    assert "what_to_do" in SRC
    assert "cadence" in SRC


def test_an_unreadable_lock_probe_also_refuses():
    """Unknown ownership is not permission. This is the same failure mode
    the acceptance seeder already had to fix: a failed lookup was being
    read as an absence."""
    assert "WRITER_LOCK_UNREADABLE" in SRC
    assert "ownership is unknown" in SRC


def test_the_lock_is_released_on_the_connection_that_took_it():
    """A session lock left on a pooled connection would make the scheduled
    writer a permanent standby the next time it restarted."""
    assert "pg_advisory_unlock" in SRC
    assert "writer_lock_released" in SRC
    # Acquired and released against one explicitly held connection, not two
    # separate `async with pool.acquire()` blocks.
    assert "conn = await pool.acquire()" in SRC
    assert "await pool.release(conn)" in SRC


def test_manually_driven_cycles_are_labelled_as_manual():
    """So a decision this route produced is never reported as evidence that
    the schedule produced it."""
    assert "MANUAL_ADMIN_ROUTE_NOT_THE_SCHEDULE" in SRC
    assert "held_writer_lock" in SRC


def test_it_still_drives_the_schedulers_function_and_not_a_copy():
    assert "W.run_continuing_management" in SRC
    assert "A_SECOND_ENGINE" in SRC


def test_the_writer_lock_key_is_the_one_the_loop_actually_takes():
    """Read off `run` itself, so a renamed or re-keyed lock fails here
    rather than silently giving the route a lock nobody else wants."""
    run_src = inspect.getsource(W.run)
    assert "pg_try_advisory_lock" in run_src
    assert "LOCK_KEY" in run_src

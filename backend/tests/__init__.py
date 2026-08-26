# A REAL package on purpose (2026-08-26).
#
# Two test modules import shared stubs as `from tests.test_live_executor_
# ladder import ...`. Without this file, `tests` is a PEP 420 namespace
# portion, and namespace resolution scans ALL of sys.path before
# committing: any dependency that ships a regular `tests` package into
# site-packages wins the name outright, and the import dies with
# ModuleNotFoundError -- which is exactly what happened in CI while the
# identical suite passed locally. A regular package at the front of
# sys.path terminates the scan immediately, so the name cannot be
# hijacked by whatever a dependency happens to install.

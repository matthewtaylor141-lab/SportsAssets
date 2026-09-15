-- Pin the 2026-08-10 copy-source promotion (owner approval): the vetted
-- wallet 0x2c33…0563 joins the live copy sleeve, keyed by its current
-- auto-generated username. The weekly roster refresh renames and
-- deactivates UNPINNED whales (roster.py) — either would silently sever
-- every config reference and stop both ingestion and copies, so the row
-- is pinned before any code keys on it. Username is left as-is: only the
-- owner names whales; a rename (owner-supplied) stays a one-line UPDATE.
UPDATE whales
   SET pinned = TRUE, active = TRUE
 WHERE lower(address) = '0x2c335066fe58fe9237c3d3dc7b275c2a034a0563';

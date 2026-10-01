# d7bbecd — completed-game paper policy V2 (owner's 0.5 pp threshold)

Matched gate against the serving 475d7e4 on a fresh database migrated to 188,
critical list incl. tests/test_completed_game_v2_half_point_threshold.py.

VERDICT: ACCEPTED — 0 new failures, 0 critical failures; the 70 pre-existing
baseline failures remain (NOT all-green).

Rule (paper only): p_pinnacle - simulated acquisition price >= 0.005 at every
level used AND conditional expected profit after fees > 0; levels whose fee
per contract consumes the edge are never bought.

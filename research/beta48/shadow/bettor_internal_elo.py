"""Section 8. BETTOR_INTERNAL_ELO -- our own rating, computed causally.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.

WHY OUR OWN
-----------
The repository Elo has ASOF_PROVENANCE = NOT_PROVEN, and it cannot be rescued:
its `date` column is the date the rating applies TO, not the date it was
computed, so a series regenerated in one pass over completed history embeds
later results in earlier rows and nothing in the file distinguishes that from a
genuine contemporaneous rating.

That is a reason to stop USING it. It is not a reason to do without a strength
rating, because a rating is a deterministic function of prior results, and
prior results ARE archivally provable. So we compute our own.

THE PROPERTY THAT MAKES IT PROVEN_DERIVED
-----------------------------------------
    Adding a future result cannot change any earlier Elo state.

That is not a comment; it is the test, and it is what separates a causal rating
from a regenerated one. A whole-history regeneration fails it silently -- the
numbers still look like Elo, still predict well in backtest, and still contain
tomorrow. So the state is persisted sequentially, every update reads the state
BEFORE the match it is about to consume, and `assert_causal` replays the series
with a future match appended and checks that every earlier snapshot is
byte-identical.

If that passes, and every input result is PROVEN_NATIVE or PROVEN_ARCHIVAL,
then the rating inherits the status of its inputs:

    INTERNAL_ELO_STATUS = PROVEN_DERIVED
"""

import hashlib
import json
from collections import defaultdict

NOT_IDENTIFIED = "NOT_IDENTIFIED"

ELO_START = 1500.0
ELO_K = 20.0
ELO_HOME_ADVANTAGE = 60.0
ELO_SCALE = 400.0

GD_ELO_K = 20.0
GD_ELO_MAX_MULTIPLIER = 3.0

EWMA_ALPHA = 0.25

THE_CAUSAL_PROPERTY = (
    "adding a future result cannot change any earlier Elo state")

WHY_NOT_THE_REPOSITORY_ELO = (
    "its date column is the date the rating applies TO, not the date it was "
    "computed; a regenerated series embeds later results in earlier rows and "
    "the file cannot show it")

INHERITANCE_RULE = (
    "a deterministic causal transformation of PROVEN inputs is PROVEN_DERIVED. "
    "The algorithm must be frozen and must read state strictly before the "
    "match it consumes; otherwise it is not a transformation of the inputs, it "
    "is a new estimate that happens to use them")


def _expected(ra, rb, home_adv=ELO_HOME_ADVANTAGE):
    return 1.0 / (1.0 + 10.0 ** ((rb - (ra + home_adv)) / ELO_SCALE))


def _result(gh, ga):
    return 1.0 if gh > ga else (0.5 if gh == ga else 0.0)


class InternalElo:
    """Sequential, read-before-update, persistable.

    The state is a plain dict of club -> rating, and `snapshot()` hashes it, so
    a replay can be compared exactly rather than approximately.
    """

    def __init__(self, k=ELO_K, home_adv=ELO_HOME_ADVANTAGE, start=ELO_START,
                 goal_difference=False):
        self.k = k
        self.home_adv = home_adv
        self.start = start
        self.goal_difference = goal_difference
        self.rating = {}
        self.played = defaultdict(int)
        self.consumed = 0
        self.last_date = None

    def get(self, club):
        return self.rating.get(club, self.start)

    def read_before(self, home, away):
        """The pre-match state. This is the ONLY thing a feature may use."""
        rh, ra = self.get(home), self.get(away)
        return {
            "INT_ELO_HOME": rh,
            "INT_ELO_AWAY": ra,
            "INT_ELO_DIFF": rh - ra,
            "INT_ELO_EXPECTED_HOME": _expected(rh, ra, self.home_adv),
            "INT_ELO_HOME_MATCHES": self.played[home],
            "INT_ELO_AWAY_MATCHES": self.played[away],
        }

    def update(self, home, away, gh, ga, date=None):
        """Consume one result. Refuses to go backwards in time."""
        if date is not None and self.last_date is not None and date < self.last_date:
            raise ValueError("out-of-order update: %s after %s"
                             % (date, self.last_date))
        rh, ra = self.get(home), self.get(away)
        exp = _expected(rh, ra, self.home_adv)
        s = _result(gh, ga)
        k = self.k
        if self.goal_difference:
            gd = abs(gh - ga)
            mult = 1.0 if gd <= 1 else min(GD_ELO_MAX_MULTIPLIER,
                                           1.0 + 0.5 * (gd - 1))
            k = k * mult
        delta = k * (s - exp)
        self.rating[home] = rh + delta
        self.rating[away] = ra - delta
        self.played[home] += 1
        self.played[away] += 1
        self.consumed += 1
        if date is not None:
            self.last_date = date

    def snapshot(self):
        return {c: round(r, 9) for c, r in sorted(self.rating.items())}

    def digest(self):
        return hashlib.sha256(
            json.dumps(self.snapshot(), sort_keys=True).encode()).hexdigest()


class EwmaForm:
    """Goals for and against, exponentially weighted, read before update."""

    def __init__(self, alpha=EWMA_ALPHA):
        self.alpha = alpha
        self.gf = {}
        self.ga = {}

    def read_before(self, home, away):
        return {
            "INT_EWMA_GF_HOME": self.gf.get(home),
            "INT_EWMA_GA_HOME": self.ga.get(home),
            "INT_EWMA_GF_AWAY": self.gf.get(away),
            "INT_EWMA_GA_AWAY": self.ga.get(away),
        }

    def update(self, home, away, gh, ga):
        a = self.alpha
        for club, f, g in ((home, gh, ga), (away, ga, gh)):
            self.gf[club] = f if club not in self.gf else \
                a * f + (1 - a) * self.gf[club]
            self.ga[club] = g if club not in self.ga else \
                a * g + (1 - a) * self.ga[club]


def run_series(matches, k=ELO_K, goal_difference=False, snapshot_every=None):
    """Feed matches in date order, emitting the pre-match read for each.

    `matches` are dicts with DATE, HOME, AWAY, FT_HOME, FT_AWAY, already
    filtered to what is archivally admissible. Returns the per-match features
    and, optionally, periodic state snapshots for the causality test.
    """
    ms = sorted(matches, key=lambda m: (m["DATE"], m["HOME"], m["AWAY"]))
    elo = InternalElo(k=k, goal_difference=goal_difference)
    ewma = EwmaForm()
    rows, snaps = [], {}
    for i, m in enumerate(ms):
        feat = elo.read_before(m["HOME"], m["AWAY"])
        feat.update(ewma.read_before(m["HOME"], m["AWAY"]))
        feat["DATE"] = m["DATE"]
        feat["HOME"] = m["HOME"]
        feat["AWAY"] = m["AWAY"]
        rows.append(feat)
        if snapshot_every and i % snapshot_every == 0:
            snaps[i] = elo.digest()
        elo.update(m["HOME"], m["AWAY"], m["FT_HOME"], m["FT_AWAY"], m["DATE"])
        ewma.update(m["HOME"], m["AWAY"], m["FT_HOME"], m["FT_AWAY"])
    return rows, elo, ewma, snaps


def assert_causal(matches, future_match, snapshot_every=25):
    """THE test. Replay with a future result appended; nothing earlier moves.

    Returns a verdict dict rather than raising, so a caller can report the
    failure rather than crash on it -- but a False here means the rating is NOT
    PROVEN_DERIVED and must not be used in the high-integrity lane.
    """
    base_rows, _, _, base_snaps = run_series(matches,
                                             snapshot_every=snapshot_every)
    extended = list(matches) + [future_match]
    ext_rows, _, _, ext_snaps = run_series(extended,
                                           snapshot_every=snapshot_every)
    n = len(base_rows)
    rows_identical = all(base_rows[i] == ext_rows[i] for i in range(n))
    snaps_identical = all(base_snaps.get(i) == ext_snaps.get(i)
                          for i in base_snaps)
    return {
        "THE_CAUSAL_PROPERTY": THE_CAUSAL_PROPERTY,
        "BASE_MATCHES": len(matches),
        "EXTENDED_MATCHES": len(extended),
        "EARLIER_FEATURE_ROWS_IDENTICAL": rows_identical,
        "EARLIER_STATE_SNAPSHOTS_IDENTICAL": snaps_identical,
        "CAUSAL": bool(rows_identical and snaps_identical),
    }


def state_as_of(matches, cutoff_date):
    """Elo and EWMA state using only matches strictly before `cutoff_date`."""
    prior = [m for m in matches if m["DATE"] < cutoff_date]
    _, elo, ewma, _ = run_series(prior)
    return elo, ewma, len(prior)


def describe():
    return {
        "THE_CAUSAL_PROPERTY": THE_CAUSAL_PROPERTY,
        "WHY_NOT_THE_REPOSITORY_ELO": WHY_NOT_THE_REPOSITORY_ELO,
        "INHERITANCE_RULE": INHERITANCE_RULE,
        "PARAMETERS": {"ELO_START": ELO_START, "ELO_K": ELO_K,
                       "ELO_HOME_ADVANTAGE": ELO_HOME_ADVANTAGE,
                       "ELO_SCALE": ELO_SCALE, "EWMA_ALPHA": EWMA_ALPHA},
        "PARAMETERS_ARE_FROZEN": True,
        "NO_WHOLE_HISTORY_REGENERATION": True,
    }

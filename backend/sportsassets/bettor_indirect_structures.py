"""INDIRECT STRUCTURES ACROSS MARKETS ON ONE FIXTURE, PROVED NOT PATTERN-MATCHED.

Owner directive, "COMPLETE THE AUTONOMOUS TRADING SYSTEM" §5:

    "Implement indirect-pair reasoning: verify fixture, participants,
     orientation, date, period, line, overtime, ties, pushes, voids,
     postponements, settlement treatment; payoff table across all
     outcomes including exceptional settlement states; distinguish
     direct complementary / indirect with both-win region / both-lose
     region / partial or unequal-quantity hedges / unestablishable
     relationships. For Bears ML plus Panthers +4.5, prove the
     both-win region under the actual contracts. 'Cannot lose both
     legs' does not establish a profitable portfolio. Never allocate
     the same inventory to multiple hedges."

────────────────────────────────────────────────────────────────────
WHY THIS IS NOT A NAME MATCHER, WHICH IS THE ONLY INTERESTING DESIGN
DECISION IN THE FILE.

The obvious implementation reads two market titles, notices that one
says "Bears" and the other says "Panthers +4.5", and concludes
"middle". That implementation is wrong for a reason that is not
stylistic: the titles do not carry the facts the classification
depends on. Two legs form a middle only if they are graded against THE
SAME underlying random variable. A full-game spread and a first-half
spread both say "Bears -4.5" and are graded against different
variables. A moneyline that counts overtime and a spread that does not
are graded against different variables. In both cases the names match
and the structure does not exist.

So this module never classifies from names. It builds each leg's
PAYOUT FUNCTION over an explicitly enumerated partition of the
fixture's outcome space, sums them region by region, and reads the
taxonomy off the resulting joint payoff table. A structure is whatever
the arithmetic says it is. If the facts needed to build a leg's payout
function are missing, the pair is UNESTABLISHABLE and the missing fact
is named -- never defaulted, never inferred from the title.

────────────────────────────────────────────────────────────────────
WHAT "NEVER BOTH LOSE" DOES AND DOES NOT BUY, STATED BEFORE ANY
CALLER CAN QUOTE THE TAXONOMY AT A RISK GATE.

The case studies are unambiguous and they disagree with the intuition:

    RN1     MIDDLE  21,190,205 units @ $1.2024   ->  +$1.6M realized
            GAP      6,410,095 units @ $0.6755   ->  -$170K realized
    Ferrari MIDDLE  14,484,617 units @ $1.1711   ->  +$997K realized
            GAP      8,677,930 units @ $0.8003   ->  -$683K realized

Both accounts' MIDDLE books cost MORE than $1 per unit on average --
$1.20 and $1.17 against a guaranteed minimum of $1.00. A structure
that cannot lose both legs and cost $1.17 has a GUARANTEED LOSS of
$0.17 per unit unless the middle lands often enough to pay for it. It
is not a hedge; it is a bet on the middle wearing a hedge's clothes.
Ferrari's own above-$1 MIDDLE book realized $34K on $14.1M of cost.

Only the below-$1 MIDDLE locks a gross surplus, and both studies show
it is the small part of the book:

    RN1     below-$1 MIDDLE  5,327,083 units @ $0.7940  -> +$1.5M
    Ferrari below-$1 MIDDLE  3,163,615 units @ $0.8590  -> +$936K

So `classify` returns a taxonomy label AND a `locks_gross_surplus`
verdict computed against actual cost, and the two are different
questions. `MIDDLE` alone never authorises anything.

────────────────────────────────────────────────────────────────────
POSTPONEMENT IS NOT A PAYOUT AND IS NOT IN THE MINIMUM.

A cancelled fixture voids at 50-50 and that IS a payout, so it enters
the payoff table as $0.50 per leg. A POSTPONED fixture does not: the
market stays open, nothing is paid, and the capital stays committed
for an unbounded time. Folding that into `min_payout` would report a
guaranteed $1.00 on a structure that may return nothing for weeks, so
postponement is carried as its own field -- `unresolved_states` --
and `min_payout` is explicitly the minimum CONDITIONAL ON RESOLUTION.
Section 9's capital-usage accounting reads the first; a risk gate
reading only the second would be reading the wrong number.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict, replace
from fractions import Fraction

from . import bettor_settlement_clauses as SETTLE

# ── the outcome variable a leg is graded against ─────────────────────
#
# Two legs can only form a structure if these agree EXACTLY. This is
# the check the name matcher cannot do.

VAR_MARGIN = "MARGIN"      # signed integer: score(team_a) - score(team_b)
VAR_TOTAL = "TOTAL"        # non-negative integer: combined score
VAR_WIN3 = "WIN3"          # categorical: A_WINS / DRAW / B_WINS

# ── periods. Never combined across a boundary (§5). ──────────────────

PERIOD_FULL = "FULL_GAME"
PERIOD_H1 = "FIRST_HALF"
PERIOD_H2 = "SECOND_HALF"
PERIOD_Q1 = "FIRST_QUARTER"
PERIOD_MAP = "MAP_OR_GAME"          # esports maps, tennis sets
PERIOD_SERIES = "SERIES"

# ── overtime treatment. A leg that includes OT and one that excludes
#    it are graded against different variables even at the same line.

OT_INCLUDED = "OT_INCLUDED"
OT_EXCLUDED = "OT_EXCLUDED"
OT_NOT_APPLICABLE = "OT_NOT_APPLICABLE"   # no OT exists in this sport/period
OT_UNKNOWN = "OT_UNKNOWN"                 # rule text not captured -> refuse

# ── exceptional settlement states, enumerated not assumed ────────────

STATE_REGULAR = "REGULAR"
STATE_TIE = "TIE"                  # regulation tie, sport permits it
STATE_PUSH = "PUSH"                # margin/total lands exactly on an integer line
STATE_VOID = "VOID"                # fixture cancelled / abandoned
STATE_POSTPONED = "POSTPONED"      # market stays open; NOT a payout

# ── market kinds ─────────────────────────────────────────────────────

KIND_MONEYLINE = "MONEYLINE"
KIND_SPREAD = "SPREAD"
KIND_TOTAL = "TOTAL"
KIND_THREE_WAY = "THREE_WAY"       # soccer "Will T1 win?" style condition

# ── taxonomy (§5) ────────────────────────────────────────────────────

DIRECT_COMPLEMENT = "DIRECT_COMPLEMENT"
MIDDLE = "MIDDLE"                          # never both lose; $1..$2
GAP = "GAP"                                # never both win; $0..$1
INDEPENDENT_OVERLAP = "INDEPENDENT_OVERLAP"  # both-win AND both-lose reachable
PARTIAL = "PARTIAL_UNEQUAL_QUANTITY"
UNESTABLISHABLE = "UNESTABLISHABLE"

TAXONOMY = (DIRECT_COMPLEMENT, MIDDLE, GAP, INDEPENDENT_OVERLAP,
            PARTIAL, UNESTABLISHABLE)

# A structure label is a statement about PAYOFF SHAPE only.
LABEL_IS = "A_STATEMENT_ABOUT_PAYOFF_SHAPE_ACROSS_OUTCOMES"
LABEL_IS_NOT = "AN_AUTHORISATION_A_PROFIT_OR_AN_ESTABLISHED_EV"

CENTS = 100          # payouts are exact in cents; $0, $0.50, $1, $2


# ═════════════════════════════════════════════════════════════════════
# LEGS
# ═════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Leg:
    """One held outcome token, described by the facts grading needs.

    Every field is required to be stated. `None` on a field this leg's
    kind needs is a refusal, not a default: an unstated overtime rule
    or an unstated orientation makes the payout function unknowable,
    and a structure built on an unknowable leg is a fabricated hedge.
    """
    condition_id: str
    fixture_id: str
    kind: str
    period: str
    overtime: str
    # Orientation: which of the fixture's two listed participants this
    # leg's chosen outcome backs. Team A is the fixture's FIRST listed
    # participant -- the same A that VAR_MARGIN is signed towards.
    backs: str | None = None          # "A" | "B" | "DRAW" | None for totals
    # Handicap in the backed team's favour, expressed against team A's
    # margin. Bears -4.5 backing Bears is line=-4.5 backs="A";
    # Panthers +4.5 backing Panthers is the SAME contract from B's
    # side, so line=-4.5 backs="B". Always half-integer or an integer
    # with a stated push rule.
    line: Fraction | None = None
    # For totals: OVER or UNDER at `line`.
    over_under: str | None = None
    quantity: int = 0                 # contracts, integer
    cost_cents_per_unit: int | None = None
    # Settlement rule text captured from the venue for THIS condition.
    # Absent text is a refusal for any exceptional state it would have
    # governed -- we do not supply the venue's rules from memory.
    #
    # ONE FIELD PER OUTCOME, AND EACH HOLDS ONLY THE CLAUSE THAT
    # ESTABLISHES IT. The reported defect was that `build_leg` put the
    # WHOLE captured prose in both of these, so "A tie resolves 50-50"
    # -- a sentence about a played, drawn fixture -- established a
    # 50-cent payout for a fixture that never happened. The fields are
    # now per-outcome by contract, and `_established_payout` below
    # ENFORCES it rather than trusting the caller: a string in
    # `void_rule` that does not name a cancellation trigger establishes
    # nothing, whoever put it there.
    tie_rule: str | None = None
    void_rule: str | None = None
    settlement_text_captured: bool = False
    #: The structured five-outcome reading from `bettor_settlement_clauses`,
    #: keyed by outcome, each entry carrying its own source clause, payout
    #: class and refusal. Present when the leg was built from captured prose;
    #: `None` on the hand-written fixtures below, which state their clauses
    #: directly in the two fields above. `compare=False` keeps `Leg`
    #: hashable and keeps two legs differing only in provenance equal.
    settlement_rules: dict | None = field(default=None, compare=False)
    #: The whole-document provenance (raw text, source, retrieval time,
    #: content hash, interpretation version) for the reading above.
    settlement_provenance: dict | None = field(default=None, compare=False)

    def rule_for(self, outcome: str) -> dict | None:
        """The structured reading for one outcome, or None if unread."""
        return (self.settlement_rules or {}).get(outcome)

    def missing_facts(self) -> list[str]:
        """Facts this leg needs before its payout function can be built."""
        gaps: list[str] = []
        if self.kind not in (KIND_MONEYLINE, KIND_SPREAD, KIND_TOTAL,
                             KIND_THREE_WAY):
            gaps.append("kind %r is not a graded market kind" % (self.kind,))
        if self.period not in (PERIOD_FULL, PERIOD_H1, PERIOD_H2, PERIOD_Q1,
                               PERIOD_MAP, PERIOD_SERIES):
            gaps.append("period %r is not stated as a known period"
                        % (self.period,))
        if self.overtime == OT_UNKNOWN:
            gaps.append("overtime treatment is not captured for %s; a leg "
                        "that may or may not count overtime is not graded "
                        "against a known variable" % self.condition_id)
        if self.kind in (KIND_MONEYLINE, KIND_SPREAD, KIND_THREE_WAY):
            if self.backs not in ("A", "B", "DRAW"):
                gaps.append("orientation is not established for %s: which "
                            "listed participant the held outcome backs is "
                            "not stated" % self.condition_id)
        if self.kind == KIND_SPREAD and self.line is None:
            gaps.append("spread %s has no line" % self.condition_id)
        if self.kind == KIND_TOTAL:
            if self.line is None:
                gaps.append("total %s has no line" % self.condition_id)
            if self.over_under not in ("OVER", "UNDER"):
                gaps.append("total %s does not state OVER or UNDER"
                            % self.condition_id)
        if self.line is not None and self.line.denominator != 2:
            # An integer line can PUSH. A push is a real settlement
            # state with a real payout, and we do not know it unless
            # the venue text was captured.
            if not self.settlement_text_captured:
                gaps.append("line %s on %s is an integer, so the outcome can "
                            "land exactly on it; the venue's push rule was "
                            "not captured, so that region has no known "
                            "payout" % (self.line, self.condition_id))
            elif _established_payout(self, SETTLE.PUSH) is None:
                # CAPTURING PROSE IS NOT A PARSED PAYOUT INSTRUCTION. This
                # used to say the classifier could not represent a push at
                # all; it now can, so the gap is the narrower and truer one:
                # THIS leg's text does not establish a push payout, and the
                # clause reader says why.
                rec = self.rule_for(SETTLE.PUSH) or {}
                gaps.append("integer line %s on %s can push -- the outcome can "
                            "land exactly on the line -- and no captured clause "
                            "establishes that push's payout (%s)"
                            % (self.line, self.condition_id,
                               rec.get("refusal") or SETTLE.R_NOT_STATED))
        if self.quantity < 0:
            gaps.append("quantity is negative on %s" % self.condition_id)
        return gaps

    @property
    def variable(self) -> str:
        if self.kind == KIND_TOTAL:
            return VAR_TOTAL
        if self.kind == KIND_THREE_WAY:
            return VAR_WIN3
        return VAR_MARGIN

    def grading_key(self) -> tuple:
        """What must match between two legs for one variable to grade both.

        Fixture, period, the variable itself and the overtime treatment.
        Two legs differing on ANY of these are graded against different
        random variables however similar their titles look.
        """
        return (self.fixture_id, self.period, self.variable, self.overtime)


# ═════════════════════════════════════════════════════════════════════
# THE OUTCOME PARTITION AND THE PAYOUT FUNCTION
# ═════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Region:
    """One cell of the fixture's outcome space, with its own payouts.

    `label` is human-readable; `state` is the settlement state; the
    predicate is already applied -- a Region is a decided cell, not a
    test. Regions for one classification always partition the space
    exhaustively, which is what makes min/max a proof rather than a
    sample.
    """
    label: str
    state: str = STATE_REGULAR
    # The margin or total values this cell covers, as an inclusive
    # integer interval; None on either end means unbounded. Absent
    # entirely for WIN3 and for non-regular states.
    lo: int | None = None
    hi: int | None = None
    win3: str | None = None


def _margin_regions(breaks: list[int]) -> list[Region]:
    """Partition the integer margin line at the given integer breaks.

    `breaks` are the integers at which some leg's payout changes. The
    partition is the open intervals between them plus the singleton
    breaks themselves, so a payout that changes exactly at an integer
    (an integer line, a tie at margin 0) gets its own cell and cannot
    be averaged away.
    """
    pts = sorted(set(breaks))
    out: list[Region] = []
    prev: int | None = None
    for p in pts:
        lo = None if prev is None else prev + 1
        hi = p - 1
        if lo is None or lo <= hi:
            out.append(Region("margin in (%s, %d)" % (
                "-inf" if prev is None else prev, p), lo=lo, hi=hi))
        out.append(Region("margin = %d" % p, lo=p, hi=p))
        prev = p
    out.append(Region("margin > %d" % pts[-1], lo=pts[-1] + 1, hi=None))
    return out


#: Which `Leg` string field is contractually about which outcome. A string
#: found in one of these is evidence about THAT outcome and no other, and
#: `_established_payout` checks the text actually names the outcome before
#: reading a payout out of it.
_LEGACY_FIELD_FOR = {
    SETTLE.TIE: "tie_rule",
    SETTLE.CANCELLED: "void_rule",
}


def _established_payout(leg: Leg, outcome: str) -> int | None:
    """Per-unit cents THIS SIDE of this leg pays in `outcome`, or None.

    THE RULE THIS ENFORCES. A payout is established for an outcome only by text
    that NAMES THAT OUTCOME and, by a construction the clause reader recognises,
    states a resolution for it. Nothing else counts -- not a resolution stated
    for a different outcome, not the presence of a string in a field, not a
    default.

    AND A RESOLUTION IS NOT A PAYOUT. The prose says what the MARKET does; what
    a held contract RECEIVES also needs the account's own order intent, because
    a market resolving NO pays $1.00 to the NO holder and $0.00 to the YES
    holder. v1 read "this market resolves NO" as a class it had named
    RESOLVES_NO_FOR_THE_HELD_SIDE and returned 0 cents for BOTH sides.

    THE SIDE COMES FROM THE IDENTITY, NEVER FROM `backs`. `condition_id` is
    `slug#SIDE`, so the order intent travels with the leg. Which participant a
    leg backs is a different fact: a leg backing team B can hold either outcome
    token of a market about team B, and inferring polarity from orientation is
    the conflation this function must not make. A leg whose identity carries no
    side cannot be paid a YES/NO resolution, and refuses.

    A REFUND IS NOT FIFTY CENTS. Where the clause says the basis is refunded the
    payout is this contract's own cost -- 30c on one bought at 30c -- and is
    undetermined when the cost is not stated. Returning 50 would invent a gain
    on the cheap side and a loss on the dear one, and would contradict
    `reconcile_settlement`, which books a void as the remaining basis.
    """
    side = held_side_of_leg(leg)
    rec = leg.rule_for(outcome)
    if rec and rec.get("established"):
        return _cents_for(rec.get("resolution") or rec.get("payout_class"),
                          side, leg)
    if rec is not None:
        # READ AND REFUSED. The structured reading is authoritative for this
        # outcome once it exists; falling back to the string field here would
        # let a blob answer a question the clause reader just declined.
        return None

    field_name = _LEGACY_FIELD_FOR.get(outcome)
    if field_name is None:
        return None
    text = getattr(leg, field_name, None)
    if not leg.settlement_text_captured or not text:
        return None
    # THE GUARD. Does this text actually speak about this outcome at all?
    if not SETTLE._mentions(text, outcome):
        return None
    read = SETTLE.read_outcome(text, outcome)
    if not read["established"]:
        return None
    return _cents_for(read.get("resolution") or read.get("payout_class"),
                      side, leg)


def held_side_of_leg(leg: Leg) -> str | None:
    """The account's order intent for this leg, from its side-aware identity.

    Deliberately NOT `leg.backs`. Orientation says which participant the leg's
    outcome is about; the side says which outcome token is held. A market on
    team B resolving NO pays the holder of B's NO token, and orientation cannot
    tell you whether that is this leg.
    """
    cid = str(getattr(leg, "condition_id", "") or "")
    if "#" not in cid:
        return None
    side = cid.rsplit("#", 1)[1].strip().upper()
    return side if side in SETTLE.SIDES else None


def _cents_for(resolution, side, leg: Leg) -> int | None:
    """One resolution plus one side plus one basis, in cents, or None."""
    got = SETTLE.side_payout_cents(
        resolution, side, basis_cents=leg.cost_cents_per_unit)
    return got.get("cents")


def void_suppression_hides(legs, *, fixture_can_void: bool) -> tuple[str, ...]:
    """Legs whose cancellation payout is unknown and would be hidden.

    DECLARING CANCELLATION IMPOSSIBLE IS NOT READING THE CANCELLATION RULE.
    `payoff_table(fixture_can_void=False)` removes the VOID cell entirely, so
    a leg whose cancellation payout is UNDETERMINED stops contributing `None`
    to the joint column and the minimum becomes determinate. The floor then
    looks proved when what actually happened is that the unknown was deleted.

    A caller may legitimately know a fixture cannot be cancelled. What it may
    not do is use that claim to stand in for a settlement rule it never read.
    So the two are separated: suppression is allowed, and suppression that
    hides an unread cancellation rule is named here and refused by `classify`.

    Returns one string per affected leg; empty when nothing is hidden --
    including when `fixture_can_void` is True, since then nothing is removed.
    """
    if fixture_can_void:
        return ()
    out = []
    for leg in legs:
        if _established_payout(leg, SETTLE.CANCELLED) is None:
            rec = leg.rule_for(SETTLE.CANCELLED) or {}
            out.append(
                "cancellation was declared impossible for this fixture, and %s "
                "has no established cancellation payout (%s), so suppressing "
                "the VOID region removes an UNDETERMINED cell rather than an "
                "inapplicable one -- the resulting floor would be an artefact "
                "of the suppression"
                % (leg.condition_id,
                   rec.get("refusal") or SETTLE.R_NOT_STATED))
    return tuple(out)


def _leg_payout_cents(leg: Leg, region: Region) -> int | None:
    """What one unit of `leg` pays in this region. None = not determined.

    This is the whole classification. Everything else reads its output.
    """
    # ── exceptional states first: they override the variable ─────────
    if region.state == STATE_POSTPONED:
        # A postponement is not a payout: the market stays open until the
        # fixture is played or abandoned. Prose saying so is recorded (it is
        # how POSTPONED becomes established rather than unknown) but there is
        # still no money to book in this cell.
        return None
    if region.state == STATE_VOID:
        return _established_payout(leg, SETTLE.CANCELLED)
    if region.state == STATE_PUSH:
        return _established_payout(leg, SETTLE.PUSH)
    if region.state == STATE_TIE:
        # A regulation tie in a two-outcome US moneyline. A SPREAD at a .5
        # line has no tie region at all (margin 0 is simply a loss for the
        # favourite), so a tie only reaches a moneyline leg.
        if leg.kind == KIND_MONEYLINE:
            return _established_payout(leg, SETTLE.TIE)
        # fall through: spreads and totals grade a tie by the variable

    # ── regular grading by the variable ──────────────────────────────
    if leg.variable == VAR_WIN3:
        if region.win3 is None:
            return None
        want = {"A": "A_WINS", "B": "B_WINS", "DRAW": "DRAW"}[leg.backs]
        return CENTS if region.win3 == want else 0

    if leg.variable == VAR_TOTAL:
        if region.lo is None and region.hi is None:
            return None
        # A .5 line splits the integers cleanly; the region is entirely
        # on one side of it because `line` was a break point.
        probe = region.lo if region.lo is not None else region.hi
        if probe is None:
            return None
        if Fraction(probe) == leg.line:
            # The total landed exactly on the line: this cell IS the push,
            # whatever `region.state` says, because the state is a property
            # of the fixture and the push is a property of THIS leg's line.
            return _established_payout(leg, SETTLE.PUSH)
        over = Fraction(probe) > leg.line
        if leg.over_under == "OVER":
            return CENTS if over else 0
        return 0 if over else CENTS

    # VAR_MARGIN: moneyline and spread.
    probe = region.lo if region.lo is not None else region.hi
    if probe is None:
        return None
    m = Fraction(probe)                    # team A's margin
    if leg.kind == KIND_MONEYLINE:
        # No handicap. A wins iff margin > 0.
        a_wins = m > 0
        return CENTS if (a_wins == (leg.backs == "A")) else 0
    # Spread. `line` is the handicap applied to team A's margin, so the
    # A-side leg wins iff margin + line > 0, and the B-side leg is the
    # complement of that same test on the same condition.
    if m + leg.line == 0:
        # A PUSH IS NOT A B-SIDE WIN. Both sides of an integer spread land
        # here together, and the cell pays what the venue's push clause says
        # -- which is `None` until a clause naming a push states one.
        return _established_payout(leg, SETTLE.PUSH)
    a_side_wins = (m + leg.line) > 0
    return CENTS if (a_side_wins == (leg.backs == "A")) else 0


def _break_points(legs: tuple[Leg, ...]) -> list[int]:
    """Integers where some leg's payout changes, so each gets its own cell."""
    pts: set[int] = {0}                    # margin 0 is always a boundary
    for leg in legs:
        if leg.line is None:
            continue
        # A .5 line changes payout between floor and ceil; the two
        # neighbouring integers are what matter, and both are covered
        # by putting the floor in as a break.
        f = leg.line.numerator // leg.line.denominator
        pts.add(f)
        pts.add(f + 1)
        pts.add(-f)
        pts.add(-f - 1)
    return sorted(pts)


#: AN INTEGER TOTAL LINE IS A PUSH THE PARTITION DOES NOT ISOLATE.
#:
#: THE DEFECT, MEASURED (map of aca3564, §1). The total partition below breaks
#: only at floor(line), and `_leg_payout_cents` recognises a push only when a
#: cell's lower bound IS the line. A half-integer line is exactly right under
#: that rule. An integer line is not: OVER 20 + UNDER 23 with an established
#: refund-push clause gives "total in [0, 20]" = [0, 100] and "total in
#: [21, 23]" = [100, 100] -- totals 20 and 23 should push and are graded as a
#: loss and a win instead. Integer SPREADS are isolated correctly (every
#: +/-floor(line) is a margin break point, so the push has its own cell).
#:
#: So a total leg with an integer line is REFUSED by name wherever a table is
#: built from it, rather than priced on a partition that folds its push into a
#: neighbouring cell. Repairing the partition is a separate, tested change.
R_INTEGER_TOTAL_PUSH_NOT_ISOLATED = (
    "AN_INTEGER_TOTAL_LINE_CAN_PUSH_AND_THE_PARTITION_DOES_NOT_ISOLATE_IT")


def integer_total_push_gaps(legs) -> list[str]:
    """One named gap per total leg whose line is an integer. Pure."""
    out = []
    for leg in legs:
        line = getattr(leg, "line", None)
        if (getattr(leg, "kind", None) == KIND_TOTAL and line is not None
                and Fraction(line).denominator == 1):
            out.append(
                "%s: total %s has the integer line %s, so the total can land "
                "exactly on it, and the total partition breaks only at "
                "floor(line) -- the push would be folded into a neighbouring "
                "cell and priced as a win or a loss"
                % (R_INTEGER_TOTAL_PUSH_NOT_ISOLATED, leg.condition_id, line))
    return out


def leg_grading(legs) -> tuple[dict, ...]:
    """THE GRADING FACTS OF EACH LEG, recorded on the structure.

    The table's region labels do not carry the lines -- "total in [0, 20]" is
    the same label for an OVER 20.5 and an OVER 20 -- so a consumer that must
    refuse the integer-total defect above cannot tell the two apart from the
    table alone. This is what it reads instead. Index order is the leg order
    the table's `per_leg_cents` uses.
    """
    out = []
    for leg in legs:
        line = getattr(leg, "line", None)
        out.append({"condition_id": getattr(leg, "condition_id", None),
                    "fixture_id": getattr(leg, "fixture_id", None),
                    "kind": getattr(leg, "kind", None),
                    "variable": getattr(leg, "variable", None),
                    "period": getattr(leg, "period", None),
                    "overtime": getattr(leg, "overtime", None),
                    "backs": getattr(leg, "backs", None),
                    "over_under": getattr(leg, "over_under", None),
                    "line": None if line is None else str(Fraction(line))})
    return tuple(out)


def payoff_table(legs: tuple[Leg, ...],
                 *, sport_permits_tie: bool,
                 fixture_can_void: bool = True,
                 fixture_can_postpone: bool = True) -> list[dict]:
    """Every outcome region, each leg's payout in it, and the joint total.

    Exhaustive by construction: the regular cells partition the whole
    integer line (or the three WIN3 categories) and the exceptional
    states are appended explicitly. `joint_cents` is None wherever any
    leg's payout is not determined -- an undetermined cell never
    silently contributes 0 to a minimum.
    """
    if not legs:
        return []
    variable = legs[0].variable
    regions: list[Region] = []
    if variable == VAR_WIN3:
        regions = [Region("A wins", win3="A_WINS"),
                   Region("draw", win3="DRAW"),
                   Region("B wins", win3="B_WINS")]
    elif variable == VAR_TOTAL:
        breaks = sorted({int(l.line.numerator // l.line.denominator)
                         for l in legs if l.line is not None})
        pts = breaks or [0]
        regions = []
        prev: int | None = None
        for p in pts:
            lo = 0 if prev is None else prev + 1
            if lo <= p:
                regions.append(Region("total in [%d, %d]" % (lo, p),
                                      lo=lo, hi=p))
            prev = p
        regions.append(Region("total > %d" % pts[-1], lo=pts[-1] + 1))
    else:
        regions = _margin_regions(_break_points(legs))
        if sport_permits_tie:
            regions = [r for r in regions if not (r.lo == 0 and r.hi == 0)]
            regions.append(Region("regulation tie", state=STATE_TIE,
                                  lo=0, hi=0))
    if fixture_can_void:
        regions.append(Region("fixture cancelled or abandoned",
                              state=STATE_VOID))
    # `fixture_can_void=False` DELETES the cell, and deleting a cell whose
    # payout was undetermined turns an unknown floor into a determinate one.
    # That bypass is reported by `void_suppression_hides` and refused in
    # `classify`; it is not silently corrected here, because whether this
    # fixture can be cancelled is a fact about the fixture that this function
    # has no way to establish.
    if fixture_can_postpone:
        regions.append(Region("fixture postponed; market stays open",
                              state=STATE_POSTPONED))

    rows: list[dict] = []
    for r in regions:
        per_leg = [_leg_payout_cents(leg, r) for leg in legs]
        joint = (None if any(p is None for p in per_leg)
                 else sum(p * leg.quantity
                          for p, leg in zip(per_leg, legs)))
        rows.append({
            "region": r.label,
            "state": r.state,
            "per_leg_cents": per_leg,
            "joint_cents": joint,
            "determined": joint is not None,
        })
    return rows


# ═════════════════════════════════════════════════════════════════════
# CLASSIFICATION
# ═════════════════════════════════════════════════════════════════════

@dataclass
class Structure:
    """The classified relationship between two legs, with its evidence."""
    taxonomy: str
    legs: tuple[str, ...] = ()
    units: int = 0                        # equal-quantity units matched
    min_payout_cents: int | None = None   # CONDITIONAL ON RESOLUTION
    max_payout_cents: int | None = None
    both_win_regions: tuple[str, ...] = ()
    both_lose_regions: tuple[str, ...] = ()
    undetermined_regions: tuple[str, ...] = ()
    unresolved_states: tuple[str, ...] = ()
    cost_cents: int | None = None
    locks_gross_surplus: bool | None = None
    guaranteed_gross_result_cents: int | None = None
    missing_facts: tuple[str, ...] = ()
    table: tuple[dict, ...] = ()
    #: Each leg's grading facts, in `per_leg_cents` order (see `leg_grading`).
    leg_grading: tuple[dict, ...] = ()
    label_is: str = LABEL_IS
    label_is_not: str = LABEL_IS_NOT
    why: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def classify(leg_a: Leg, leg_b: Leg, *,
             sport_permits_tie: bool,
             fixture_can_void: bool = True,
             fixture_can_postpone: bool = True) -> Structure:
    """Classify two legs by their joint payoff, or refuse and say why.

    The refusals come first and they are not warnings: a structure
    reported on legs whose grading variable was never established is a
    fabricated hedge, and every downstream consumer would treat it as
    real risk reduction.
    """
    gaps = leg_a.missing_facts() + leg_b.missing_facts()
    if leg_a.condition_id == leg_b.condition_id:
        # Same condition: this is a DIRECT pair, handled by the direct
        # pair engine, not here. Saying so is not a refusal.
        pass
    if leg_a.fixture_id != leg_b.fixture_id:
        gaps.append("different fixtures (%s vs %s): no shared outcome "
                    "variable exists" % (leg_a.fixture_id, leg_b.fixture_id))
    if leg_a.period != leg_b.period:
        gaps.append("different periods (%s vs %s): a full-game leg and a "
                    "part-game leg are graded against different variables "
                    "and are never combined"
                    % (leg_a.period, leg_b.period))
    if leg_a.variable != leg_b.variable:
        gaps.append("different outcome variables (%s vs %s): a margin leg "
                    "and a total leg do not partition one space, so no "
                    "both-win region can be proved"
                    % (leg_a.variable, leg_b.variable))
    if leg_a.overtime != leg_b.overtime:
        gaps.append("different overtime treatment (%s vs %s): the same "
                    "line on the two legs is not the same threshold, so "
                    "the structure is not established"
                    % (leg_a.overtime, leg_b.overtime))
    # AN UNREAD CANCELLATION RULE CANNOT BE REPAIRED BY DECLARING
    # CANCELLATION IMPOSSIBLE. Both are facts about settlement, only one of
    # them was read, and the table would otherwise report a floor that exists
    # because the unknown cell was deleted.
    gaps.extend(void_suppression_hides((leg_a, leg_b),
                                       fixture_can_void=fixture_can_void))
    # AN INTEGER TOTAL LINE'S PUSH IS NOT ISOLATED by the total partition (see
    # R_INTEGER_TOTAL_PUSH_NOT_ISOLATED), so its table would grade the push as
    # a win or a loss. Refused here, before any floor is computed from it.
    gaps.extend(integer_total_push_gaps((leg_a, leg_b)))
    grading = leg_grading((leg_a, leg_b))
    if gaps:
        return Structure(taxonomy=UNESTABLISHABLE,
                         legs=(leg_a.condition_id, leg_b.condition_id),
                         leg_grading=grading,
                         missing_facts=tuple(gaps),
                         why=("classification refused: %d fact(s) needed to "
                              "build a payout function are not established"
                              % len(gaps)))

    units = min(leg_a.quantity, leg_b.quantity)
    unequal = leg_a.quantity != leg_b.quantity

    # Classify on ONE unit of each leg so the taxonomy is a property of
    # the contracts, not of how much happens to be held.
    unit_a = _with_quantity(leg_a, 1)
    unit_b = _with_quantity(leg_b, 1)
    table = payoff_table((unit_a, unit_b),
                         sport_permits_tie=sport_permits_tie,
                         fixture_can_void=fixture_can_void,
                         fixture_can_postpone=fixture_can_postpone)

    determined = [r for r in table if r["determined"]]
    undet = tuple(r["region"] for r in table if not r["determined"]
                  and r["state"] != STATE_POSTPONED)
    unresolved = tuple(r["region"] for r in table
                       if r["state"] == STATE_POSTPONED)
    if not determined:
        return Structure(taxonomy=UNESTABLISHABLE,
                         legs=(leg_a.condition_id, leg_b.condition_id),
                         table=tuple(table), leg_grading=grading,
                         undetermined_regions=undet,
                         unresolved_states=unresolved,
                         missing_facts=("no region has a determined joint "
                                        "payout",),
                         why="every outcome region is undetermined")

    lo = min(r["joint_cents"] for r in determined)
    hi = max(r["joint_cents"] for r in determined)
    both_win = tuple(r["region"] for r in determined
                     if r["joint_cents"] >= 2 * CENTS)
    both_lose = tuple(r["region"] for r in determined
                      if r["joint_cents"] == 0)

    if undet:
        tax = UNESTABLISHABLE
        why = ("%d outcome region(s) have no determined payout, so neither "
               "a both-win nor a both-lose region can be ruled out"
               % len(undet))
    elif lo == hi == CENTS:
        tax = DIRECT_COMPLEMENT
        why = "every outcome pays exactly $1.00 across the two legs"
    elif lo >= CENTS and hi > CENTS:
        tax = MIDDLE
        why = ("no outcome region loses both legs; the middle pays $%.2f"
               % (hi / CENTS))
    elif lo == 0 and hi <= CENTS:
        tax = GAP
        why = ("no outcome region wins both legs; %d region(s) pay nothing"
               % len(both_lose))
    else:
        tax = INDEPENDENT_OVERLAP
        why = ("both a both-win and a both-lose region are reachable "
               "($%.2f..$%.2f), so these legs do not hedge each other"
               % (lo / CENTS, hi / CENTS))

    if unequal and tax in (MIDDLE, GAP, DIRECT_COMPLEMENT):
        why = ("%s on %d matched unit(s); the %d unmatched contract(s) on "
               "the longer leg remain unhedged directional inventory. %s"
               % (PARTIAL, units,
                  max(leg_a.quantity, leg_b.quantity) - units, why))
        tax = PARTIAL

    cost = None
    locks = None
    guaranteed = None
    if (leg_a.cost_cents_per_unit is not None
            and leg_b.cost_cents_per_unit is not None):
        cost = leg_a.cost_cents_per_unit + leg_b.cost_cents_per_unit
        if tax in (MIDDLE, DIRECT_COMPLEMENT, PARTIAL) and not undet:
            guaranteed = lo - cost
            locks = guaranteed > 0
        elif tax == GAP:
            # min payout is $0: nothing is locked, whatever it cost.
            locks = False
            guaranteed = lo - cost

    return Structure(
        taxonomy=tax,
        legs=(leg_a.condition_id, leg_b.condition_id),
        units=units,
        min_payout_cents=lo,
        max_payout_cents=hi,
        both_win_regions=both_win,
        both_lose_regions=both_lose,
        undetermined_regions=undet,
        unresolved_states=unresolved,
        cost_cents=cost,
        locks_gross_surplus=locks,
        guaranteed_gross_result_cents=guaranteed,
        table=tuple(table),
        leg_grading=grading,
        why=why,
    )


def _with_quantity(leg: Leg, q: int) -> Leg:
    # `replace` RATHER THAN A FIELD LIST. The hand-written constructor call
    # this replaces enumerated ten fields, so every field added to `Leg`
    # afterwards was silently dropped on requantification -- which for the
    # settlement reading would have turned an established rule back into
    # "nothing established" in the middle of a sizing loop, with no error.
    return replace(leg, quantity=q)


# ═════════════════════════════════════════════════════════════════════
# ALLOCATION: NEVER THE SAME INVENTORY TWICE (§5)
# ═════════════════════════════════════════════════════════════════════

@dataclass
class Allocation:
    structures: list[Structure] = field(default_factory=list)
    unallocated: dict[str, int] = field(default_factory=dict)
    ambiguity: dict[str, int] = field(default_factory=dict)
    refused: list[Structure] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"structures": [s.to_dict() for s in self.structures],
                "unallocated": dict(self.unallocated),
                "ambiguity": dict(self.ambiguity),
                "refused": [s.to_dict() for s in self.refused]}


def allocate(legs: list[Leg], *, sport_permits_tie: bool,
             fixture_can_void: bool = True,
             fixture_can_postpone: bool = True,
             prefer: tuple[str, ...] = (MIDDLE, DIRECT_COMPLEMENT, GAP),
             ) -> Allocation:
    """Assign each contract to at most one structure. Report the ambiguity.

    The case studies record 44,330 lots (RN1) with two or more
    compatible counterparts, one with 923 alternatives. A greedy pass
    that took each in turn would report a hedged book that double-counts
    the same shares, so remaining quantity is decremented as structures
    are formed and the number of alternatives each lot HAD is reported
    separately rather than hidden by the choice.

    Preference orders payoff shapes on matched units, independent of lot
    sizes. This attributes inventory already held; it is not an order
    selector or a claim that a middle has the best expected return.
    """
    remaining = {l.condition_id: l.quantity for l in legs}
    by_id = {l.condition_id: l for l in legs}
    alloc = Allocation(ambiguity={l.condition_id: 0 for l in legs})

    pairs: list[tuple[Structure, str, str]] = []
    ids = [l.condition_id for l in legs]
    for i, a_id in enumerate(ids):
        for b_id in ids[i + 1:]:
            s = classify(by_id[a_id], by_id[b_id],
                         sport_permits_tie=sport_permits_tie,
                         fixture_can_void=fixture_can_void,
                         fixture_can_postpone=fixture_can_postpone)
            if s.taxonomy == UNESTABLISHABLE:
                alloc.refused.append(s)
                continue
            if s.taxonomy == PARTIAL:
                # Unequal quantities describe the unmatched remainder,
                # not the matched units' payoff shape. Sorting PARTIAL
                # last used to choose a full direct pair ahead of a
                # smaller middle, despite the explicit MIDDLE preference.
                # Validate the original legs above before normalising so
                # invalid inventory cannot be hidden by setting q=1.
                s = classify(_with_quantity(by_id[a_id], 1),
                             _with_quantity(by_id[b_id], 1),
                             sport_permits_tie=sport_permits_tie,
                             fixture_can_void=fixture_can_void,
                             fixture_can_postpone=fixture_can_postpone)
            pairs.append((s, a_id, b_id))
            alloc.ambiguity[a_id] += 1
            alloc.ambiguity[b_id] += 1

    rank = {t: i for i, t in enumerate(prefer)}
    pairs.sort(key=lambda p: (rank.get(p[0].taxonomy, len(prefer)),
                              p[1], p[2]))

    for s, a_id, b_id in pairs:
        q = min(remaining[a_id], remaining[b_id])
        if q <= 0:
            continue
        formed = classify(_with_quantity(by_id[a_id], q),
                          _with_quantity(by_id[b_id], q),
                          sport_permits_tie=sport_permits_tie,
                          fixture_can_void=fixture_can_void,
                          fixture_can_postpone=fixture_can_postpone)
        formed.units = q
        alloc.structures.append(formed)
        remaining[a_id] -= q
        remaining[b_id] -= q

    alloc.unallocated = {k: v for k, v in remaining.items() if v > 0}
    return alloc


# ═════════════════════════════════════════════════════════════════════
# THE BEARS / PANTHERS CONTRACTS, AS LOCATED IN THE CASE STUDY
# ═════════════════════════════════════════════════════════════════════
#
# §5: "For Bears ML plus Panthers +4.5, prove the both-win region under
# the actual contracts." These are the identifiers and rules the study
# recorded from Gamma for fixture nfl-chi-car-2026-09-13, not values
# recalled from anywhere else. They are the study's OBSERVATIONS, which
# is why `settlement_text_captured` is True: the rule text was pulled.

BEARS_PANTHERS_FIXTURE = "nfl-chi-car-2026-09-13"

BEARS_MONEYLINE = Leg(
    condition_id="0x06eea9e1...c8e22",
    fixture_id=BEARS_PANTHERS_FIXTURE,
    kind=KIND_MONEYLINE, period=PERIOD_FULL, overtime=OT_INCLUDED,
    backs="A",                        # A = Bears, the first listed team
    quantity=1, cost_cents_per_unit=None,
    tie_rule="tie resolves 50-50", void_rule="void: 50-50",
    settlement_text_captured=True)

PANTHERS_PLUS_4_5 = Leg(
    # The SAME contract as 'Spread: Bears (-4.5)' seen from the other
    # side: Panthers +4.5 wins exactly when Bears -4.5 loses. The study
    # located both conditionIds; the payoff is identical either way.
    condition_id="0x952c4a42...5ee49",
    fixture_id=BEARS_PANTHERS_FIXTURE,
    kind=KIND_SPREAD, period=PERIOD_FULL, overtime=OT_INCLUDED,
    backs="B",                        # backing the Panthers side
    line=Fraction(-9, 2),             # -4.5 applied to team A's margin
    quantity=1, cost_cents_per_unit=None,
    tie_rule="spread at .5 has no tie region",
    void_rule="void: 50-50", settlement_text_captured=True)

# What actually happened, as the study verified it. Kept beside the
# contracts so the example cannot be read as a realised profit: the
# both-win region is a REGION, and in this fixture it did not occur.
BEARS_PANTHERS_ACTUAL = {
    "resolved": "Bears",
    "moneyline_paid": True,
    "panthers_spread_paid": False,
    "both_win_occurred": False,
    "why": ("the Bears won by five or more, which is outside the 1..4 "
            "both-win region; only the moneyline leg paid"),
    "this_wallet_held_it": False,
    "scope": ("verified against the contracts and their resolution only; "
              "neither case-study account held a combinable cross-market "
              "residual on this fixture, so the example is illustrative of "
              "the STRUCTURE and is not an observed position"),
}


def bears_panthers_proof() -> dict:
    """Prove the both-win region from the contracts, not from the title."""
    s = classify(BEARS_MONEYLINE, PANTHERS_PLUS_4_5,
                 sport_permits_tie=True,
                 fixture_can_void=True, fixture_can_postpone=True)
    return {
        "structure": s.to_dict(),
        "both_win_region_is": s.both_win_regions,
        "actual": dict(BEARS_PANTHERS_ACTUAL),
        "not_established": (
            "that the structure was profitable. min payout $%s against a "
            "cost this example does not state; a MIDDLE bought above $1 "
            "has a guaranteed shortfall unless the middle lands often "
            "enough to pay for it"
            % ("%.2f" % (s.min_payout_cents / CENTS)
               if s.min_payout_cents is not None else "NOT_DETERMINED")),
    }


def describe() -> dict:
    return {
        "taxonomy": TAXONOMY,
        "label_is": LABEL_IS,
        "label_is_not": LABEL_IS_NOT,
        "grading_variables": (VAR_MARGIN, VAR_TOTAL, VAR_WIN3),
        "exceptional_states": (STATE_REGULAR, STATE_TIE, STATE_PUSH,
                               STATE_VOID, STATE_POSTPONED),
        "postponement_is_not_a_payout": (
            "a postponed fixture pays nothing and keeps the capital "
            "committed; min_payout_cents is the minimum CONDITIONAL ON "
            "RESOLUTION and unresolved_states carries the rest"),
        "case_study_evidence": {
            "RN1": {"middle_units": 21190205, "middle_avg_cost": 1.2024,
                    "middle_realized": 1.6e6,
                    "gap_units": 6410095, "gap_avg_cost": 0.6755,
                    "gap_realized": -170e3,
                    "below_1_middle_units": 5327083,
                    "below_1_middle_avg_cost": 0.7940,
                    "below_1_middle_realized": 1.5e6},
            "Ferrari": {"middle_units": 14484617, "middle_avg_cost": 1.1711,
                        "middle_realized": 997e3,
                        "gap_units": 8677930, "gap_avg_cost": 0.8003,
                        "gap_realized": -683e3,
                        "below_1_middle_units": 3163615,
                        "below_1_middle_avg_cost": 0.8590,
                        "below_1_middle_realized": 936e3},
            "kept_separate": ("RN1 and Ferrari are not pooled; the two "
                              "accounts are separate observations and "
                              "their middle books differ in both average "
                              "cost and realised result"),
        },
    }

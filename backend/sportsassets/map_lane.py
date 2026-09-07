"""THE COPY LANE'S EXACT MAPPING SEQUENCE, AS ONE FUNCTION (C1, owner
order 2026-09-06 "let's remove those caps so we start copying his actual
book").

The per-fill copy lane (live_executor's copy block) maps a whale fill
through FIVE resolvers in order: premap.resolve; the US slug grammar
(_tennis_candidates + _us_slug_candidates) through
pmus.resolve_market_exact for a moneyline or pmus.resolve_derivative_exact
for a spread/total; his own slug verbatim through resolve_market_exact;
pmus.resolve_team_yesno_exact; and the fuzzy resolve_market. The mirror
(workers/mirror_shadow.map_market, which mirror_live opens books from)
ran only the first, so 1,217 of his markets in six hours -- $2.15M of
his flow -- read "unmapped" while the copy lane had been trading them.

`exact_lane` is steps two to four, in the copy lane's order and behind
the copy lane's own gates (moneyline-only for the grammar and the yes/no
lane, spread/total-only for the derivative lane, the doubleheader
sibling guards, the 20 s box per resolver call). NEVER the fuzzy step:
a wrong market is a wrong trade, so every step here is grammar-to-grammar
and refuses by name.

WHY THE COPY LANE DOES NOT CALL THIS FUNCTION. Its inline block is
pinned, byte for byte, by its own tests (test_exact_resolver_diagnostics
counts three `_ex_diag.append("timeout")` inside the copy block,
test_identity_slug_is_exact pins `[src_slug], ctx.get("outcome")` there,
test_live_executor_mapping pins the tennis call within 600 characters of
`mtype == "moneyline"` in the file). Those tests are the proof the money
path did not move, and they stay byte-identical, so the block stays
where it is. The two lanes are held together by
tests/test_mirror_maps_the_copy_lane.py instead, which drives BOTH the
copy lane's block and this function with the same fake resolvers and
asserts the same resolver calls, in the same order, on the same
candidates -- the drift a shared body would prevent is caught by the
suite the moment either side moves.

THE VENUE READ IS THE CALLER'S. `read(fn, *args)` is awaited for every
resolver call, so the mirror paces it through venue_pace, counts it,
and bounds it per tick (a caller past its bound raises ReadsCapped and
the lane stops without a verdict). resolve_market_exact is called ONE
candidate at a time so each read is one venue call and each candidate's
verdict is the caller's to remember; the resolver's own loop returns on
the first hit, so the sequence and the diag codes are the copy lane's.

THE aec CODE SIDE ('grammar', C1 round 2). The copy lane's own side rule
on an aec- market is NAME similarity: resolve_market_exact scores each
side's description against his outcome and takes the unique side over
MATCH_FLOOR, intent from the venue's own long/short marker. It has NO
team-code or side-order rule -- on mascots ('Bears' for Baylor) it
refuses outright -- so the 2,012 side echoes on side_echo_last certify
name+marker, not order. The grammar step below is therefore a MIRROR
class with its own certification (mirror_live: the venue's per-side
contract must name the same side before a book opens; the first fill's
echo must hold the side the rule chose), never a copy-lane class.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Awaitable, Callable

# the copy lane's box per resolver call (live_executor._EXACT_BOX_S)
EXACT_BOX_S = 20.0
# the mapping sources this lane can answer with, in certification order:
# 'exact' is QUARANTINE_RESUME_SRC's own class; 'yesno' is the copy
# lane's yesno_exact (refused-but-recorded while the quarantine holds,
# certified under its own counter); 'grammar' is the aec code side
# below, certified by the mirror's own venue-truth checks (mirror_live)
SRC_EXACT, SRC_YESNO, SRC_GRAMMAR = "exact", "yesno", "grammar"
# refusal names the grammar step answers with (fail closed, by name)
REFUSE_CODE_UNMATCHED = "side_code_unmatched"
REFUSE_CODE_AMBIGUOUS = "side_code_ambiguous"
REFUSE_CODE_CONFLICT = "side_code_conflict"
REFUSE_CODE_SHAPE = "side_code_shape"
REFUSE_CODE_NOINTENT = "side_code_nointent"
REFUSE_CODE_NO_INDEX = "side_code_no_index"
REFUSE_CODE_PAIR = "side_code_pair"
# a token-level refusal that the pair rule may resolve (his other
# token's outcome names no code, which is the sibling's normal shape)
# versus one that refuses the whole market (review E)
PAIR_RESOLVABLE = frozenset({REFUSE_CODE_UNMATCHED})
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_WORD_RE = re.compile(r"[a-z0-9]+")


class ReadsCapped(Exception):
    """The caller's per-tick venue budget is spent: no verdict."""


Read = Callable[..., Awaitable[Any]]


def _diag(diag: list | None, code: str) -> None:
    if diag is not None and len(diag) < 24:
        diag.append(code)


async def _boxed(read: Read, diag: list | None, tag: str, fn, *args):
    """One resolver call through the caller's read, inside the copy
    lane's box. A timeout is recorded and falls through (never a
    verdict); a spent budget propagates so the caller can name it."""
    try:
        return await asyncio.wait_for(read(fn, *args), timeout=EXACT_BOX_S)
    except asyncio.TimeoutError:
        _diag(diag, f"{tag}timeout" if tag else "timeout")
        return None


def slug_head(global_slug: str | None) -> tuple[str, str, str, str, list[str]] | None:
    """(league, a, b, date, tail) of a kindless feed slug, or None when
    the head is not exactly three tokens before an ISO date -- the
    parse _us_slug_candidates and resolve_team_yesno_exact both use."""
    s = (global_slug or "").lower()
    m = _DATE_RE.search(s)
    if not m:
        return None
    head = [t for t in s[:m.start()].strip("-").split("-") if t]
    if len(head) != 3:
        return None
    tail = [t for t in s[m.end():].strip("-").split("-") if t]
    return head[0], head[1], head[2], m.group(0), tail


def _norm(s: str | None) -> str:
    import unicodedata as _ud

    folded = _ud.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(_WORD_RE.findall(folded))


def code_hits(code: str, outcome: str | None) -> bool:
    """Does his outcome name this slug team code? THE WHOLE-WORD /
    FIRST-WORD RULE (C1 round 2, review finding A): the code is a whole
    word of the outcome, or the outcome's FIRST word starts with the
    code, or the code starts with that first word (three letters or
    more). No substring, no subsequence: 'Michigan State' does not hit
    'mich' by its second word, 'Texas A&M' does not hit 'aubrn' by its
    'a', and 'aubrn' does not reach Auburn by letters in order -- an
    abbreviation the feed spells that way names its team only through
    the outcome index and the pair rule below. Codes under three
    letters never hit (the yes/no lane's own code-short rule)."""
    code = (code or "").lower()
    if len(code) < 3:
        return False
    words = _norm(outcome).split()
    if not words:
        return False
    if code in words:
        return True
    first = words[0]
    return first.startswith(code) or (len(first) >= 3 and code.startswith(first))


def shared_stem(a: str, b: str) -> str:
    """The SHARED STEM of a slug's two team codes (C3, 2026-09-06): when
    one code is a proper prefix of the other ('wash' of 'washst',
    'mich' of 'michst', 'ohio' of 'ohiost') a first-word hit on the
    SHORTER code names either team -- 'Washington' and 'Washington
    State' both start with 'wash' -- so code_hits alone cannot decide a
    side and its answer must not ground a conflict. Returns the shorter
    code when the pair shares a stem, else ''."""
    a, b = (a or "").lower(), (b or "").lower()
    if len(a) >= 3 and len(b) > len(a) and b.startswith(a):
        return a
    if len(b) >= 3 and len(a) > len(b) and a.startswith(b):
        return b
    return ""


def code_names(code: str, outcome: str | None, sibling: str) -> bool:
    """code_hits, read against the SIBLING code (C3). Without a shared
    stem this is code_hits exactly. With one, the LONGER code is named
    when the outcome's first word starts with the whole code, or starts
    with the stem and the outcome's SECOND word starts with the rest of
    the code ('Washington State' -> 'wash' + 'st'); the SHORTER code is
    named when the first word starts with it and the longer code is NOT
    named ('Washington' -> 'wash', no second word). Nothing here is a
    subsequence or a similarity: the residual of the longer code must
    begin the outcome's own next word, so 'Washington Huskies' names
    'wash' and never 'washst'. A stem hit that decides nothing is
    neither a hit nor a conflict."""
    code = (code or "").lower()
    stem = shared_stem(code, sibling)
    if not stem:
        return code_hits(code, outcome)
    words = _norm(outcome).split()
    if not words:
        return False
    longer = code if len(code) > len(stem) else (sibling or "").lower()
    rest = longer[len(stem):]
    first = words[0]
    longer_named = first.startswith(longer) or (
        first.startswith(stem) and len(words) >= 2 and len(rest) >= 2
        and words[1].startswith(rest))
    if code == longer:
        return longer_named
    return (stem in words or first.startswith(stem)) and not longer_named


def _code_words_of(outcome: str | None) -> list[str]:
    """The outcome's words for the split rule: NFKD-folded, lower, with
    an ampersand INSIDE a word dropped ('A&M' -> 'am', the venue's own
    'flam' for Florida A&M) -- nothing else is folded."""
    import unicodedata as _ud

    folded = _ud.normalize("NFKD", str(outcome or "")).encode("ascii", "ignore").decode().lower()
    folded = re.sub(r"(?<=[a-z0-9])&(?=[a-z0-9])", "", folded)
    return _WORD_RE.findall(folded)


def code_words(code: str, outcome: str | None) -> bool:
    """THE MULTI-WORD SCHOOL CODE (C4, 2026-09-06): 'scarst' for South
    Carolina State, 'flam' for Florida A&M, 'washst' for Washington
    State. The code is the concatenation of NON-EMPTY PREFIXES of
    CONSECUTIVE words of the outcome, starting at its first word, EVERY
    word used in order, and the split is UNIQUE: 'scarst' = s|car|st
    over South|Carolina|State, 'flam' = fl|am over Florida|A&M. Refused:
    a code with more than one split, a split that would skip a word
    ('tex' over Texas|Tech leaves Tech unused -- the whole-word rule
    owns single-word claims), an outcome of one word (the first-word
    rule owns it), a code under three letters. No subsequence, no
    similarity: every letter of the code is accounted for, in order, by
    the front of the outcome's own words."""
    code = (code or "").lower()
    words = _code_words_of(outcome)
    if len(code) < 3 or len(words) < 2 or len(words) > len(code):
        return False

    def splits(pos: int, w: int) -> int:
        if w == len(words):
            return 1 if pos == len(code) else 0
        n = 0
        word = words[w]
        for k in range(1, min(len(word), len(code) - pos) + 1):
            if code[pos:pos + k] != word[:k]:
                break
            n += splits(pos + k, w + 1)
            if n > 1:
                break
        return n

    return splits(0, 0) == 1


def code_reads(code: str, outcome: str | None, sibling: str) -> bool:
    """code_names, and ONLY where the C1/C3 rules name NEITHER of the
    slug's two codes, the multi-word split (code_words) for this one.
    Every verdict code_names gives -- a hit, an ambiguity, a conflict --
    stands; the split rule turns a 'names neither' into a hit and
    nothing else ('South Carolina State' -> scarst beside flam)."""
    if code_names(code, outcome, sibling) or code_names(sibling, outcome, code):
        return code_names(code, outcome, sibling)
    return code_words(code, outcome)


# ---------------------------------------------------------------- C6
# THE VENUE'S OWN TEAM FIELD AND ITS CODE WITNESS (2026-09-07; the C6
# block comment in workers/premap.py). Two readings, both venue<->venue:
# the segment-winner rows `atc-<lg>-<a>-<b>-<date>-winner-<seg>-<code>`
# state the SCHOOL beside the CODE in their own question ('Will Notre
# Dame win the first half?' under `-nd`), and the aec side's `team` dict
# states abbreviation (the code) and safeName (the school) beside the
# mascot. Names meet by token-set equality (pmus._yn_name_match), never
# a prefix; `name`/`alias` (the mascot) is never the school; `ordering`
# is never read.
_WINNER_Q_RE = re.compile(
    r"^will (?P<school>.+?) win the (?:first|second|third|fourth) (?:half|quarter)\?$")


def same_name(a: str | None, b: str | None) -> bool:
    """Two folded names naming the same team: the yes/no lane's token-set
    equality (pmus._yn_name_match), and nothing looser."""
    from . import pmus

    a, b = _norm(a), _norm(b)
    return bool(a and b) and pmus._yn_name_match(a, b)


def winner_school(question: str | None) -> str | None:
    """The school a venue segment-winner row names in its own question
    ('Will Notre Dame win the first half?' -> 'notre dame'), folded; None
    for any other wording -- the draw rows ('Will the first half end
    tied?') and the prop rows never parse."""
    q = " ".join(str(question or "").split()).lower()
    m = _WINNER_Q_RE.match(q)
    return (_norm(m.group("school")) or None) if m else None


def code_school(board: list[dict], lg: str, a: str, b: str, date: str) -> tuple[dict | None, str | None]:
    """THE CODE WITNESS (C6a): code -> school from the event's own
    `winner-<seg>-<code>` rows on `board` (the event's rows as the sweep
    stored them). Every parsed row for a code must name the same school,
    both codes must be witnessed, and the two schools must differ.
    Returns ({code: school}, None); (None, refusal) when the venue's rows
    contradict each other (spread:code-witness-disagree: one code, two
    schools; spread:code-witness-same: both codes one school); (None,
    None) when the witness is absent, and today's code rules stand. A
    row whose question is not this grammar states nothing here."""
    base = f"atc-{lg}-{a}-{b}-{date}-winner-"
    named: dict[str, list[str]] = {a: [], b: []}
    for r in board:
        ident = str(r.get("identifier") or "").lower()
        if not ident.startswith(base):
            continue
        tail = ident[len(base):].split("-")
        if len(tail) != 2 or tail[1] not in (a, b):
            continue                    # the draw row, or not this grammar
        school = winner_school(r.get("question"))
        if school:
            named[tail[1]].append(school)
    if not named[a] or not named[b]:
        return None, None
    out: dict[str, str] = {}
    for c in (a, b):
        if any(not same_name(named[c][0], s) for s in named[c][1:]):
            return None, "spread:code-witness-disagree"
        out[c] = named[c][0]
    if a == b or same_name(out[a], out[b]):
        return None, "spread:code-witness-same"
    return out, None


def side_team(side: dict | None) -> tuple[str, str]:
    """(abbreviation, safeName) of a venue side's own `team` dict, folded
    -- the slug code and the school as the venue states them; ('', '')
    when absent. Never `name`/`alias` (the mascot), never `ordering`."""
    t = side.get("team") if isinstance(side, dict) and isinstance(side.get("team"), dict) else {}
    return str(t.get("abbreviation") or "").strip().lower(), _norm(t.get("safeName"))


def _identity_on() -> bool:
    """The owner's identity switch (premap.yn_identity_on): every C6
    moneyline read below is dark without it (M5 review HIGH-1), exactly
    as the C6 spread chain is -- OFF is byte-identical."""
    from .workers.premap import yn_identity_on

    return yn_identity_on()


def aec_head(market: dict | None) -> tuple[str, str, str, str] | None:
    """(lg, a, b, date) of the venue's OWN aec market slug
    (aec-<lg>-<a>-<b>-<date>) -- the head every C6 moneyline read is
    bounded by (M5 review LOW-1 / LOW-2 / MEDIUM-2); None for any other
    shape."""
    s = str((market or {}).get("slug") or "").lower() if isinstance(market, dict) else ""
    if not s.startswith("aec-"):
        return None
    h = slug_head(s[4:])
    return (h[0], h[1], h[2], h[3]) if h and not h[4] else None


def _team_hits(a: str, b: str, outcome: str | None, market: dict) -> list[int] | None:
    """C6 on the moneyline: the positions the venue's OWN team field
    certifies for his outcome -- a side whose abbreviation is one of the
    slug codes and whose safeName names his outcome puts him at that
    code's position. BOTH sides must state an abbreviation (M5 review
    MEDIUM-1: half a binding is none, the bar the spread reader holds).
    None when the venue states a code at the OTHER side index (its side
    order is not the slug's: a contradiction the positional pick below
    could not survive, refused as a conflict)."""
    m = market if isinstance(market, dict) else {}
    sides = [s for s in (m.get("marketSides") or []) if isinstance(s, dict)]
    if len(sides) != 2:
        return []
    teams = [side_team(s) for s in sides]
    # both halves of BOTH bindings: an abbreviation AND a school on each
    # side (M5 v2 review LOW-1: the bar the spread reader holds)
    if not all(abbr and school for abbr, school in teams):
        return []
    hits: list[int] = []
    for j, (abbr, school) in enumerate(teams):
        if abbr not in (a, b):
            continue
        i = (a, b).index(abbr)
        if i != j:
            return None
        if same_name(school, outcome):
            hits.append(i)
    return hits


def _team_code_clash(sides: list[dict], i: int, code: str, a: str, b: str) -> str | None:
    """M5 review MEDIUM-2: the aec side's OWN abbreviation against the
    code the plain contract certified it as -- the side stating the
    OTHER slug code, or the other side stating this one, is the venue
    contradicting itself (the detail, a mismatch); an abbreviation that
    is neither code is not evidence and changes nothing."""
    abbr, _own = side_team(sides[i])
    oabbr, _other = side_team(sides[1 - i])
    if abbr in (a, b) and abbr != code:
        return f"the aec side at position {i} states abbreviation {abbr!r}, not {code!r}"
    if oabbr == code:
        return f"the aec side at position {1 - i} states abbreviation {code!r} too"
    return None


def _team_truth(contract: dict, head: tuple[str, str, str, str], sides: list[dict], i: int,
                code: str, school: str | None) -> tuple[str, str] | None:
    """C6 venue-only truth for a SEGMENT-WINNER contract: the contract's
    slug is exactly atc-<lg>-<a>-<b>-<date>-winner-<seg>-<code> under the
    aec market's own head (never a suffix read: M5 review LOW-1), its
    question names a school, and the aec side at position i states
    abbreviation == code and safeName naming that school (and his
    outcome, when the caller carries it). None when the contract is not
    that grammar (the name reading above is the whole verdict);
    unverified when either side states no team (MEDIUM-1); ok when every
    field agrees; MISMATCH on any disagreement, or when the other side
    claims the same code or school."""
    q_school = winner_school(contract.get("question"))
    if q_school is None:
        return None
    lg, a, b, date = head
    slug = str(contract.get("slug") or "").lower()
    prefix = f"atc-{lg}-{a}-{b}-{date}-winner-"
    tail = slug[len(prefix):].split("-") if slug.startswith(prefix) else []
    if code not in (a, b) or len(tail) != 2 or not tail[0] or tail[1] != code:
        return "unverified", f"contract {slug} is not {prefix}<seg>-{code}, the {code!r} winner row"
    abbr, own = side_team(sides[i])
    oabbr, other = side_team(sides[1 - i])
    if not abbr and not own:
        return "unverified", f"the aec side at position {i} states no team"
    if not oabbr or not other:
        # both halves, or none of it (M5 v2 review LOW-1): the bar the
        # spread reader holds; his own side's half is read below, where a
        # school without the code is a mismatch
        return "unverified", (f"the other aec side (position {1 - i}) states no team "
                              f"(abbreviation {oabbr!r}, school {other!r}): half a binding")
    stated = f"the aec side at position {i} states abbreviation {abbr!r}, school {own!r}"
    if abbr == code and same_name(own, q_school) and (school is None or same_name(own, school)):
        if oabbr == code or same_name(other, q_school):
            return "mismatch", (f"contract {slug} names {q_school!r}; both aec sides claim it "
                                f"({abbr!r}/{own!r} and {oabbr!r}/{other!r})")
        return "ok", f"contract {slug} names {q_school!r}; {stated}"
    return "mismatch", (f"contract {slug} names {q_school!r} for {code!r}; {stated}"
                        + (f", his outcome {school!r}" if school else ""))


def contract_slug(global_slug: str | None, i: int) -> str | None:
    """The venue's per-side contract for team i of his slug --
    atc-<lg>-<a>-<b>-<date>-<code_i> -- the copy lane's own atc-
    candidate shape (_us_slug_candidates); None when his slug does not
    parse."""
    parsed = slug_head(global_slug)
    if parsed is None or i not in (0, 1):
        return None
    lg, a, b, date, _tail = parsed
    return f"atc-{lg}-{a}-{b}-{date}-{(a, b)[i]}"


def _title_sides(text: str | None) -> list[str]:
    t = _norm(text)
    parts = [p.strip() for p in re.split(r"\s+vs\s+", t) if p.strip()]
    return parts if len(parts) == 2 else []


def aec_code_side(global_slug: str | None, outcome: str | None, market: dict,
                  outcome_index: int | None = None, *, intent_of=None) -> tuple[dict | None, str | None]:
    """THE TEAM-CODE SIDE (C1 build step 4, round-2 rules). The venue
    names college mascots ('Bears' for Baylor) where his feed names
    schools, so the exact resolver finds the aec- market by slug grammar
    and refuses the side by name -- correctly, the mascot is not the
    school. The side is chosen here from HIS OWN signal, never from the
    mascot, and every fact must agree:

      his outcome names exactly one of his slug's two codes (code_hits,
      the whole-word/first-word rule);
      his feed's outcome_index is PRESENT and equals that code's position
      (mandatory, review A: a unique hit on the wrong team -- 'Michigan
      State' hitting 'mich' -- is caught by the index, so the index may
      never be absent);
      the venue slug is his under aec-, exactly two sides share it (the
      aec shape), the side at that position carries the venue's own
      long/short marker.

    Everything that could contradict it refuses, by name:
      side_code_unmatched   his outcome names neither code ('Sooners')
      side_code_ambiguous   his outcome names both (a derby rendering)
      side_code_no_index    his feed carries no outcome index
      side_code_shape       not his slug echoed under aec-, not exactly
                            two named sides sharing the market slug,
                            closed, or a slug tail that is not a team code
      side_code_conflict    the index disagrees with the code's position;
                            his slug's own side token names the other
                            team; a venue side's description names the
                            OTHER position's code and not its own, or
                            names BOTH codes (review B); the venue title
                            is a matchup naming the codes in the
                            opposite order
      side_code_nointent    the venue marked no long/short on the side
    Returns (mapping, refusal): exactly one is set."""
    from .workers.premap import side_intent as _side_intent

    parsed = slug_head(global_slug)
    if parsed is None:
        return None, REFUSE_CODE_SHAPE
    lg, a, b, date, tail = parsed
    if a == b or len(tail) > 1 or (tail and tail[0] not in (a, b)):
        return None, REFUSE_CODE_SHAPE
    # each code read against its sibling: a shared stem (wash/washst)
    # decides by the outcome's own words, never by the stem alone (C3);
    # a multi-word school code (scarst) only where neither code is
    # named by those rules (C4, code_reads)
    hits = [i for i, c in enumerate((a, b)) if code_reads(c, outcome, (b, a)[i])]
    if not hits and lg == "cfb" and _identity_on():
        # C6 (2026-09-07): where no code rule reads his outcome (the
        # two-letter `nd`; 'Notre Dame'), the venue's OWN team field on
        # the aec side -- abbreviation the code, safeName the school --
        # names his position; a side stating a code at the other index
        # is the venue's contradiction, refused. College football only
        # for now (the soccer builder lands its own reader of the same
        # columns) and dark without the identity switch (M5 review).
        hits = _team_hits(a, b, outcome, market)
        if hits is None:
            return None, REFUSE_CODE_CONFLICT
    if not hits:
        return None, REFUSE_CODE_UNMATCHED
    if len(hits) != 1:
        return None, REFUSE_CODE_AMBIGUOUS
    i = hits[0]
    code = (a, b)[i]
    try:
        oi = int(outcome_index) if outcome_index is not None else None
    except (TypeError, ValueError):
        oi = None
    if oi not in (0, 1):
        return None, REFUSE_CODE_NO_INDEX
    if oi != i:
        return None, REFUSE_CODE_CONFLICT
    if tail and tail[0] != code:
        # his slug's own side token disagrees with his outcome (the
        # yes/no lane's yn:suffix-side rule): metadata shear, refuse
        return None, REFUSE_CODE_CONFLICT
    cand = f"aec-{lg}-{a}-{b}-{date}"
    m = market if isinstance(market, dict) else {}
    if str(m.get("slug") or "").lower() != cand or m.get("closed"):
        return None, REFUSE_CODE_SHAPE
    sides = [s for s in (m.get("marketSides") or []) if isinstance(s, dict)]
    if len(sides) != 2 or any(not (s.get("identifier") and s.get("description")) for s in sides):
        return None, REFUSE_CODE_SHAPE
    if any(str(s.get("identifier") or "").lower() != cand for s in sides):
        # distinct per-side identifiers are the atc- shape: the exact
        # resolver's own side selection owns that family, by name
        return None, REFUSE_CODE_SHAPE
    for j, s in enumerate(sides):
        desc = str(s.get("description") or "")
        own = code_reads((a, b)[j], desc, (b, a)[j])
        other = code_reads((b, a)[j], desc, (a, b)[j])
        if other and not own:
            return None, REFUSE_CODE_CONFLICT
        if own and other:
            return None, REFUSE_CODE_CONFLICT       # a description naming both codes (B)
    # the venue's own title, when it is a matchup that names the codes,
    # must name them in his slug's order (the live payload names schools:
    # 'Baylor vs. Auburn' for aec-cfb-bayl-aubrn); a matchup naming them
    # the other way round is a contradiction, a mascot title says nothing
    for text in (m.get("title"), m.get("question")):
        ts = _title_sides(text)
        if ts and code_reads(b, ts[0], a) and code_reads(a, ts[1], b) \
                and not (code_reads(a, ts[0], b) or code_reads(b, ts[1], a)):
            return None, REFUSE_CODE_CONFLICT
    side = sides[i]
    # the venue's own marker on the side: pmus.order_intent_for when the
    # caller's venue module has it, else premap.side_intent (the same
    # reading -- order_intent_for defers to it); None means REFUSE
    intent = intent_of(m, side) if intent_of is not None else _side_intent(side, sides)
    if not intent:
        return None, REFUSE_CODE_NOINTENT
    return {"market_slug": str(side["identifier"]), "title": m.get("question") or m.get("title"),
            "outcome": side["description"], "intent": intent, "side_index": i,
            "matched_by": "aec_code_order", "score": 1.0}, None


def pair_agrees(global_slug: str | None, i: int, other_outcome: str | None,
                other_index: int | None) -> str | None:
    """BOTH-TOKEN AGREEMENT (review (3)): when his other token is in the
    fills it is the other side of the same two-sided contract by
    construction, and its own facts must say so -- its outcome index
    must be the complementary position and its outcome must not name
    the code the mapped token chose (it may name its own; the feed's
    abbreviations often name neither, which is not a contradiction).
    Returns a refusal name (side_code_pair, or side_code_conflict when
    the sibling names the mapped code) or None when the pair agrees."""
    parsed = slug_head(global_slug)
    if parsed is None or i not in (0, 1):
        return REFUSE_CODE_PAIR
    _lg, a, b, _date, _tail = parsed
    try:
        oi = int(other_index) if other_index is not None else None
    except (TypeError, ValueError):
        oi = None
    if oi != 1 - i:
        return REFUSE_CODE_PAIR
    # THE SHARED STEM (C3): 'Washington State' beside a mapped 'wash'
    # names 'washst' by its own two words, not the mapped code -- no
    # conflict; a sibling naming the mapped code and not its own, with
    # no stem to share ('Baylor' beside a mapped 'bayl'), still refuses
    mapped, own = (a, b)[i], (a, b)[1 - i]
    if code_reads(mapped, other_outcome, own) and not code_reads(own, other_outcome, mapped):
        return REFUSE_CODE_CONFLICT
    return None


def _equal_names(contract: dict, description: str) -> bool:
    want = _norm(description)
    if not want:
        return False
    team = contract.get("team") if isinstance(contract.get("team"), dict) else {}
    fields = [contract.get("outcome"), contract.get("title"), team.get("name"),
              team.get("alias"), team.get("safeName")]
    return any(_norm(f) == want for f in fields if f)


def grammar_truth(contract: dict | None, market: dict | None, i: int, *,
                  code: str | None = None, school: str | None = None) -> tuple[str, str]:
    """VENUE-ONLY TRUTH BEFORE A GRAMMAR BOOK OPENS (review (2)): the
    venue's per-side contract for the code the rule chose
    (atc-<lg>-<a>-<b>-<date>-<code_i>) names its team in its own
    outcome/title/team fields; that name must EXACT-EQUAL the aec
    market's sides[i].description. Both strings are the venue's own, so
    an agreement pins side i to the code with no order assumption and a
    disagreement is a wrong side caught before a dollar moves.
    ('ok' | 'mismatch' | 'unverified', detail): unverified when the
    contract is not listed or either payload is unreadable (refuse the
    open, nothing tripped); mismatch when the contract is listed and
    names something else (refuse and trip). C6 (2026-09-07): with
    `code` (the slug code at i), a segment-winner contract whose question
    names the school is read against the aec side's own team field
    (_team_truth); `school` is his outcome, checked too when carried."""
    m = market if isinstance(market, dict) else {}
    sides = [s for s in (m.get("marketSides") or []) if isinstance(s, dict)]
    if len(sides) != 2 or i not in (0, 1) or not sides[i].get("description") \
            or not sides[1 - i].get("description"):
        return "unverified", "aec sides unreadable"
    c = contract if isinstance(contract, dict) else {}
    if not c.get("slug"):
        return "unverified", "per-side contract not listed"
    desc = str(sides[i]["description"])
    head = aec_head(m) if code and _identity_on() else None
    if _equal_names(c, desc):
        if head is not None:
            # M5 review MEDIUM-2: the plain contract's name never
            # overrides the side's own code field
            clash = _team_code_clash(sides, i, str(code).lower(), head[1], head[2])
            if clash:
                return "mismatch", f"contract {c.get('slug')} names {desc!r} but {clash}"
        return "ok", f"contract {c.get('slug')} names {desc!r}"
    # MISMATCH ONLY WHEN THE CONTRACT NAMES THE OTHER SIDE (round 3, review
    # 2): a contract that names sides[1-i] is a wrong side, and trips; any
    # other shape -- a yes/no-shaped contract (question + Yes/No, no
    # outcome), a title that names neither, no name at all -- is a shape
    # this check cannot read, unverified: no book, no trip
    other = str(sides[1 - i]["description"])
    if _equal_names(c, other):
        return "mismatch", (f"contract {c.get('slug')} names {other!r}, the OTHER side; the aec "
                            f"side at position {i} is {desc!r}")
    if head is not None and head[0] == "cfb":
        # C6-ML: college football only for now, under the identity switch
        c6 = _team_truth(c, head, sides, i, str(code).lower(), school)
        if c6 is not None:
            return c6
    named = [str(x) for x in (c.get("outcome"), c.get("title")) if x]
    return "unverified", f"contract {c.get('slug')} names {named!r}, neither side by name"


def grammar_fill_echo(echo: dict | None, expected_outcome: str | None, intent: str | None,
                      filled: float | None) -> tuple[str, str]:
    """THE FIRST FILL'S ECHO on a grammar book (owner order 2026-09-06,
    round 2): the venue's positions payload names the side we hold
    (marketMetadata.outcome) and its signed size. The echoed outcome
    must equal the side description the rule chose and the sign must be
    the book's intent -- the copy lane's own position-sign check, with
    its sole-leg attribution: |net| must match our booked fill within
    5% + 1 share, else the echo abstains (unverified). ('ok' |
    'mismatch' | 'unverified', detail)."""
    e = echo if isinstance(echo, dict) else None
    if not expected_outcome:
        # the side the rule chose is not on record (round 3, review 4): a
        # verdict needs both strings, so this is unverified, never a trip
        return "unverified", "the chosen side is not on record"
    if e is None:
        return "unverified", "positions unreadable"
    try:
        net = float(e.get("net")) if e.get("net") is not None else None
    except (TypeError, ValueError):
        net = None
    if net is None or net == 0:
        return "unverified", "no position held yet"
    try:
        my = float(filled or 0.0)
    except (TypeError, ValueError):
        my = 0.0
    if my <= 0 or abs(abs(net) - my) > my * 0.05 + 1:
        return "unverified", f"net={net} not attributable to our {my:g} filled shares"
    want_long = intent == "ORDER_INTENT_BUY_LONG"
    if want_long != (net > 0):
        return "mismatch", f"POSITION SIDE WRONG: sent {intent} but hold netPosition={net}"
    got = e.get("outcome")
    if not got:
        return "unverified", "venue echoed no outcome"
    if _norm(got) != _norm(expected_outcome):
        return "mismatch", f"venue echoes side {got!r}, the rule chose {expected_outcome!r}"
    return "ok", f"venue echoes {got!r} with netPosition={net}"


async def exact_lane(pool, pmus, ctx: dict, read: Read, *, diag: list | None = None,
                     cands_out: list | None = None, grammar: bool = False,
                     retrieve: Read | None = None) -> tuple[dict | None, str | None, str | None]:
    """The copy lane's exact steps after premap said no, for ONE token
    (`ctx`: market_slug, event_slug, market_title, event_title, outcome,
    outcome_index -- the copy lane's _market_context shape). Returns
    (mapping, source, refusal): a mapping with its source ('exact' |
    'yesno' | 'grammar'), or None with a refusal NAME when a step found
    the market and refused the side (the grammar step's names above),
    or (None, None, None) when nothing answered -- the caller keeps
    premap's own explain for that.

    `grammar=True` appends the mirror-only aec code side after the copy
    lane's steps; it runs only when the exact resolver reported the
    aec- candidate LISTED and refused its side (a 404 never earns a
    second read). `retrieve(slug)` is the caller's paced venue read for
    that step (defaults to `read` over pmus._get_client().markets)."""
    from .copy_sports import _tennis_candidates, _us_slug_candidates, market_type_of

    src_slug = str(ctx.get("market_slug") or ctx.get("event_slug") or "")
    outcome = ctx.get("outcome")
    title = ctx.get("market_title")
    mtype = market_type_of(src_slug)
    mapping = None
    source = None
    aec_listed = False
    if mtype == "moneyline":
        # tennis first, then the us-slug forms (the copy lane's order)
        cands = (_tennis_candidates(title, src_slug)
                 + _us_slug_candidates(src_slug, outcome or ""))
        if cands_out is not None:
            cands_out.extend(cands)
        for cand in cands:
            n0 = len(diag) if diag is not None else 0
            mapping = await _boxed(read, diag, "", pmus.resolve_market_exact, [cand], outcome, diag)
            if mapping is not None:
                break
            if cand.startswith("aec-") and diag is not None and len(diag) > n0:
                note = str(diag[n0])
                aec_listed = aec_listed or note.startswith(("low:", "amb:", "noint", "parent"))
        if mapping is not None:
            source = SRC_EXACT
    elif mtype in ("spread", "total"):
        mapping = await _boxed(read, diag, "", pmus.resolve_derivative_exact,
                               src_slug, outcome, title)
        if mapping is not None:
            from .live_executor import _dh_sibling_guard
            if await _dh_sibling_guard(pool, src_slug):
                _diag(diag, "dh-siblings")
                mapping = None
            else:
                source = SRC_EXACT
    if mapping is None and src_slug and mtype == "moneyline":
        # his own slug verbatim, the strongest candidate there is
        if cands_out is not None:
            cands_out.append(src_slug)
        mapping = await _boxed(read, diag, "", pmus.resolve_market_exact, [src_slug], outcome, diag)
        if mapping is not None:
            source = SRC_EXACT
    if mapping is None and mtype == "moneyline":
        mapping = await _boxed(read, diag, "yn:", pmus.resolve_team_yesno_exact,
                               src_slug, outcome, title, ctx.get("event_title"), diag)
        if mapping is not None:
            from .live_executor import _dh_sibling_guard_ml
            if await _dh_sibling_guard_ml(pool, src_slug):
                _diag(diag, "yn:dh-siblings")
                mapping = None
            else:
                source = SRC_YESNO
    if mapping is None and grammar and mtype == "moneyline" and aec_listed:
        parsed = slug_head(src_slug)
        if parsed is not None:
            lg, a, b, date, _tail = parsed
            cand = f"aec-{lg}-{a}-{b}-{date}"

            def _fetch(slug: str) -> dict:
                return (pmus._get_client().markets.retrieve_by_slug(slug) or {}).get("market") or {}

            try:
                if retrieve is not None:
                    market = await asyncio.wait_for(retrieve(cand), timeout=EXACT_BOX_S)
                else:
                    market = await asyncio.wait_for(read(_fetch, cand), timeout=EXACT_BOX_S)
            except ReadsCapped:
                raise
            except asyncio.TimeoutError:
                _diag(diag, "grammar:timeout")
                market = None
            except Exception as exc:  # noqa: BLE001 — an unreadable market is no side
                _diag(diag, f"grammar:{type(exc).__name__}")
                market = None
            if market:
                hit, refusal = aec_code_side(src_slug, outcome, market, ctx.get("outcome_index"),
                                             intent_of=getattr(pmus, "order_intent_for", None))
                if hit is not None:
                    _diag(diag, "grammar:ok")
                    return hit, SRC_GRAMMAR, None
                _diag(diag, f"grammar:{refusal}")
                return None, None, refusal
    return mapping, source, None


__all__ = ["exact_lane", "aec_code_side", "pair_agrees", "grammar_truth", "grammar_fill_echo",
           "contract_slug", "code_hits", "code_names", "shared_stem", "slug_head", "ReadsCapped",
           "EXACT_BOX_S", "SRC_EXACT", "SRC_YESNO", "SRC_GRAMMAR", "PAIR_RESOLVABLE",
           "REFUSE_CODE_UNMATCHED", "REFUSE_CODE_AMBIGUOUS", "REFUSE_CODE_CONFLICT",
           "REFUSE_CODE_SHAPE", "REFUSE_CODE_NOINTENT", "REFUSE_CODE_NO_INDEX", "REFUSE_CODE_PAIR"]

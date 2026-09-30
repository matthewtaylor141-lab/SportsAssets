"""ONE MANAGEMENT-FRIENDLY LABEL FOR A POSITION, FROM VERIFIED METADATA.

Every Command Centre page (Derek's history, Xavier's position cards,
standing orders and recommendations, Audrey's audit and chat rendering)
labels a market through THIS module, so the three pages can never disagree
about what a position is:

    Yankees Moneyline
    Yankees vs. Red Sox · MLB · Full Game · Oct 2, 2026
    $1,000 invested · 50¢ average price · 2,000 contracts

WHERE THE FACTS COME FROM. The venue's own catalogue, `us_premap`: one row per
venue side, carrying the side's order intent (which intent buys THIS side),
its normalised description, the signed line, the venue's team dict
(`team_name`, `team_safe_name`, `team_abbr`, `team_league`), `game_start` and
`sportsMarketType`. The sport comes from `bettor_sport_mapping` (the venue's
market type first, the attested league second). Nothing is fuzzy-matched and
nothing is looked up from memory.

THE SIDE IS THE ONE THAT IS HELD. The label is built from the catalogue row
whose `intent` equals the position's order intent. On a two-sided market the
SHORT intent buys the other side, whose own row names its team; on a yes/no
market the SHORT intent is the NO side and the label says NO -- a NO position
is never rendered as the YES side.

WHEN THE METADATA IS INCOMPLETE the label is the most readable venue text
that exists (the catalogue question, else the event title, else the market
code read as codes -- never as invented team names) with a NAMED warning such
as `METADATA_INCOMPLETE: team not resolved`. An open position is never
hidden because its label is incomplete.

LOGOS. No licensed team logo files are bundled. `badge` is a neutral team
badge -- initials and a colour derived from the venue's team id or code --
with `logo: null` and the note saying official logos are not bundled.

RAW IDENTIFIERS stay available, never as the primary label: `technical`
carries the market slug, the side's identifier and intent for the page's
"Technical details" disclosure and for search.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
from typing import Any, Iterable

VERSION = "MARKET_LABELS_V1"
LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"

W_NO_ROW = "METADATA_INCOMPLETE: no catalogue row for this market"
W_NO_SIDE = "METADATA_INCOMPLETE: the catalogue does not say which side this order intent buys"
W_NO_TEAM = "METADATA_INCOMPLETE: team not resolved"
W_NO_OPPONENT = "METADATA_INCOMPLETE: opponent not resolved"
W_NO_KIND = "METADATA_INCOMPLETE: market type not resolved"
W_NO_PERIOD = "METADATA_INCOMPLETE: period not stated"
W_NO_LINE = "METADATA_INCOMPLETE: line not stated"
LOGO_NOTE = ("official team logos are not bundled (no licensed files); a "
             "neutral badge is shown")

#: Leagues whose venue team dict names the club by its nickname in
#: `team_name` ('yankees'); on college rows `team_name` is the mascot and the
#: school is `team_safe_name` (migration 055), so those read safe_name.
_NICKNAME_LEAGUES = {"mlb", "nba", "nfl", "nhl", "wnba", "mls"}
_COLLEGE = re.compile(r"^(cfb|cbb|ncaa|ncaaf|ncaab|wcbb)")
_UNITS = {"baseball": ("Run", "Runs"), "football": ("Point", "Points"),
          "basketball": ("Point", "Points"), "hockey": ("Goal", "Goals"),
          "soccer": ("Goal", "Goals"), "esports": ("Map", "Maps"),
          "tennis": ("Game", "Games"), "table_tennis": ("Point", "Points"),
          "cricket": ("Run", "Runs")}
_SMALL = {"of", "and", "the", "vs", "fc", "de"}
_KEEP_UPPER = {"fc", "sc", "afc", "cf", "ac", "lafc", "nycfc", "psg", "usa",
               "g2", "t1", "nip", "mibr", "big", "og"}


def _title(s: str | None) -> str:
    """A folded venue string for display: 'red sox' -> 'Red Sox'."""
    words = []
    for i, w in enumerate(str(s or "").strip().split()):
        lw = w.lower()
        if lw in _KEEP_UPPER:
            words.append(lw.upper())
        elif i and lw in _SMALL:
            words.append(lw)
        elif re.match(r"^\d", lw):
            words.append(lw)
        else:
            words.append(lw[:1].upper() + lw[1:])
    return " ".join(words)


def _num(line) -> str | None:
    if line is None or str(line).strip() == "":
        return None
    m = re.search(r"([+-])?\s*(\d+(?:\.\d+)?)", str(line))
    if not m:
        return None
    mag = m.group(2).rstrip("0").rstrip(".") if "." in m.group(2) else m.group(2)
    return (m.group(1) or "") + mag


def period_of(*texts) -> tuple[str | None, str | None]:
    """(label, code) of the period named in the venue's strings, or
    (None, None) when none is stated. Map, first five, half, quarter and
    period markets are told apart; nothing is assumed."""
    blob = " ".join(str(t or "") for t in texts).lower()
    m = re.search(r"(?:^|[^a-z])map[\s_\-]?(\d{1,2})(?![\d])", blob)
    if m:
        return "Map %s" % int(m.group(1)), "MAP_%s" % int(m.group(1))
    if re.search(r"first[\s_\-]?(?:five|5)|1st[\s_\-]?(?:five|5)|(?:^|[^a-z0-9])f5(?:[^a-z0-9]|$)|first_5_innings", blob):
        return "First 5 Innings", "FIRST_FIVE"
    if re.search(r"first[\s_\-]?half|1st[\s_\-]?half|(?:^|[^a-z0-9])1h(?:[^a-z0-9]|$)", blob):
        return "1st Half", "FIRST_HALF"
    if re.search(r"second[\s_\-]?half|2nd[\s_\-]?half|(?:^|[^a-z0-9])2h(?:[^a-z0-9]|$)", blob):
        return "2nd Half", "SECOND_HALF"
    m = re.search(r"(first|second|third|fourth|1st|2nd|3rd|4th)[\s_\-]?quarter", blob)
    if m:
        n = {"first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th"}.get(m.group(1), m.group(1))
        return "%s Quarter" % n, "QUARTER_%s" % n[0]
    m = re.search(r"(first|second|third|1st|2nd|3rd)[\s_\-]?period", blob)
    if m:
        n = {"first": "1st", "second": "2nd", "third": "3rd"}.get(m.group(1), m.group(1))
        return "%s Period" % n, "PERIOD_%s" % n[0]
    if re.search(r"full[\s_\-]?game|full[\s_\-]?match|full[\s_\-]?time|regulation[\s_\-]?and", blob):
        return "Full Game", "FULL_GAME"
    return None, None


def kind_of(*texts) -> str | None:
    blob = " ".join(str(t or "") for t in texts).lower()
    if re.search(r"spread|handicap|run[\s_\-]?line|puck[\s_\-]?line", blob):
        return "SPREAD"
    if re.search(r"(?:^|[^a-z])total|over[\s_/\-]?under|(?:^|[^a-z])o/u", blob):
        return "TOTAL"
    if re.search(r"moneyline|money[\s_\-]?line|(?:^|[^a-z])ml(?:[^a-z]|$)|winner|to[\s_]win|(?:^|[^a-z])win(?:[^a-z]|$)", blob):
        return "MONEYLINE"
    return None


def _team_display(row: dict) -> str | None:
    lg = str(row.get("team_league") or "").strip().lower()
    name, safe = row.get("team_name"), row.get("team_safe_name")
    if _COLLEGE.match(lg):
        pick = safe or name
    elif lg in _NICKNAME_LEAGUES:
        pick = name or safe
    else:
        pick = safe or name
    if pick:
        return _title(pick)
    return None


def _side_display(row: dict) -> str | None:
    """The side's name as the venue states it: the team dict first, then the
    side's own description when it is a name rather than yes/no/over/under."""
    t = _team_display(row)
    if t:
        return t
    sn = str(row.get("side_norm") or "").strip().lower()
    if sn and sn not in ("yes", "no", "over", "under", "draw", "tie") and \
            not re.search(r"\d", sn):
        return _title(sn)
    return None


def badge(team: str | None, key: Any = None, abbr: str | None = None) -> dict | None:
    if not team:
        return None
    parts = [p for p in re.split(r"[\s\-]+", team) if p]
    initials = (str(abbr).upper()[:4] if abbr else
                ("".join(p[0] for p in parts[:2]) if len(parts) > 1 else team[:3]).upper())
    h = hashlib.sha256(str(key if key is not None else team).encode()).digest()
    hue = int.from_bytes(h[:2], "big") % 360
    return {"initials": initials, "hue": hue, "logo": None, "note": LOGO_NOTE}


def _date(v) -> str | None:
    if v is None:
        return None
    try:
        if hasattr(v, "strftime"):
            d = v
        elif isinstance(v, (int, float)):
            d = _dt.datetime.fromtimestamp(float(v), _dt.timezone.utc)
        else:
            s = str(v).replace("Z", "+00:00")
            d = _dt.datetime.fromisoformat(s) if "T" in s or " " in s else \
                _dt.datetime.strptime(s[:10], "%Y-%m-%d")
        return d.strftime("%b %-d, %Y")
    except (TypeError, ValueError):
        return None


def _money(v: float) -> str:
    return "$" + ("{:,.0f}".format(v) if abs(v - round(v)) < 0.005 else "{:,.2f}".format(v))


def size_line(*, invested=None, avg_price=None, contracts=None) -> str | None:
    bits = []
    if invested is not None:
        bits.append("%s invested" % _money(float(invested)))
    if avg_price is not None:
        c = float(avg_price) * 100.0
        bits.append(("%d¢" % round(c) if abs(c - round(c)) < 0.05 else "%.1f¢" % c)
                    + " average price")
    if contracts is not None:
        q = float(contracts)
        bits.append(("{:,.0f}".format(q) if abs(q - round(q)) < 1e-9 else "{:,.2f}".format(q))
                    + (" contract" if q == 1 else " contracts"))
    return " · ".join(bits) or None


def _readable_slug(slug: str | None) -> str | None:
    """A market code made readable AS CODES (never as invented names)."""
    s = str(slug or "").strip().lower()
    if not s:
        return None
    parts = s.split("-")
    date = re.search(r"\d{4}-\d{2}-\d{2}", s)
    head = [p for p in parts if not re.match(r"^\d{4}$|^\d{2}$", p)]
    codes = [p.upper() for p in head[1:] if p]
    txt = " ".join(codes[:5])
    return (txt + (" · " + date.group(0) if date else "")).strip() or s


def resolve(rows: Iterable[dict] | None, *, market_slug: str | None,
            intent: str | None, invested=None, avg_price=None,
            contracts=None, title_hint: str | None = None) -> dict:
    """THE LABEL. Pure: `rows` are this market's catalogue rows (every side),
    `intent` the position's order intent. Returns primary, secondary, size,
    side, warnings, badge and the technical identifiers."""
    rows = [dict(r) for r in (rows or []) if r]
    slug = (market_slug or "").strip()
    warnings: list[str] = []
    tech = {"market_slug": slug or None, "intent": intent,
            "identifier": None, "resolver": VERSION}
    size = size_line(invested=invested, avg_price=avg_price,
                     contracts=contracts)
    if not rows:
        readable = title_hint or _readable_slug(slug) or "unlabelled market"
        warnings.append(W_NO_ROW)
        return {"primary": readable, "secondary": None, "size": size,
                "side": {"intent": intent, "held": None,
                         "is_no": intent == SHORT and None},
                "complete": False, "warnings": warnings, "badge": None,
                "technical": tech, "source": "NO_CATALOGUE_ROW"}
    held = next((r for r in rows if r.get("intent") == intent), None)
    complement_no = False
    if held is None and len(rows) == 1:
        if intent == LONG:
            held = rows[0]
        elif intent == SHORT and str(rows[0].get("side_norm") or "").lower() \
                not in ("no",) and rows[0].get("intent") == LONG:
            # a one-row contract bought SHORT: the NO side of its proposition
            complement_no = True
    other = next((r for r in rows if r is not held and r.get("intent")
                  and r.get("intent") != intent), None)
    base = held or rows[0]
    tech["identifier"] = base.get("identifier")
    q = base.get("question") or ""
    st = base.get("sports_type") or ""
    ev_title = base.get("event_title") or ""
    try:
        from . import bettor_sport_mapping as SM
        cls = SM.classify(sports_type=st or None,
                          team_league=base.get("team_league") or None)
        sport = cls.get("SPORT") if cls.get("SPORT") != SM.NOT_IDENTIFIED else None
        league = cls.get("LEAGUE") if cls.get("LEAGUE") != SM.NOT_IDENTIFIED else None
    except Exception:                                           # noqa: BLE001
        sport, league = None, (base.get("team_league") or None)
    if held is None and not complement_no:
        warnings.append(W_NO_SIDE)
    sn = str((held or {}).get("side_norm") or "").strip().lower()
    is_no = complement_no or sn == "no"
    is_yes = sn == "yes"
    team = _side_display(held) if (held and not is_no and not is_yes) else None
    if (is_no or is_yes) and held is not None:
        team = _team_display(held)          # a team dict on a yes/no row, if any
    opp = _side_display(other) if (other and not is_no and not is_yes) else None
    kind = kind_of(st, base.get("kind") if base.get("kind") not in ("side", "contract") else "", q)
    per_label, per_code = period_of(st, q, slug, base.get("event_slug"))
    line = _num((held or base).get("signed")) or _num((held or base).get("line"))
    if is_no and complement_no:
        line = None
    unit = _UNITS.get(sport or "", (None, None))
    market = None
    if kind == "MONEYLINE":
        market = "Map Winner" if (per_code or "").startswith("MAP_") else "Moneyline"
    elif kind == "SPREAD":
        if line is None:
            warnings.append(W_NO_LINE)
            market = "Spread"
        else:
            u = unit[1] if abs(float(line)) != 1 else unit[0]
            market = ("%s%s" % ("" if line.startswith(("+", "-")) else "+", line)) + \
                (" " + u if u else "")
    elif kind == "TOTAL":
        ou = "Over" if sn.startswith("over") else "Under" if sn.startswith("under") else None
        market = re.sub(r"\s+", " ", "%s %s%s" % (ou or "Total", line or "",
                                                   (" " + unit[1]) if unit[1] else "")).strip()
        if line is None:
            warnings.append(W_NO_LINE)
    else:
        warnings.append(W_NO_KIND)
    side_word = "NO" if is_no else ("YES" if is_yes else None)
    primary = None
    if kind == "TOTAL" and not (is_no or is_yes):
        subject = (_title(ev_title) if ev_title.islower() else ev_title) or None
        primary = market if not subject else "%s — %s" % (subject, market)
    elif team:
        primary = team + (" " + market if market else "")
        if is_no:
            primary = "NO — " + primary
    if primary is None:
        if not team:
            warnings.append(W_NO_TEAM)
        text = (q.strip() or ((_title(ev_title) if ev_title.islower() else ev_title)
                              if ev_title else None)
                or title_hint or _readable_slug(slug) or "unlabelled market")
        primary = ("NO — " + text) if is_no else (("YES — " + text) if is_yes else text)
    if held is not None and kind in ("MONEYLINE", "SPREAD") and not opp \
            and not is_no and not is_yes:
        warnings.append(W_NO_OPPONENT)
    if per_label is None:
        warnings.append(W_NO_PERIOD)
    comp = (league or "").upper() or (_title(sport) if sport else None)
    matchup = None
    if team and opp:
        matchup = "%s vs. %s" % (team, opp)
    elif ev_title:
        matchup = _title(ev_title) if ev_title.islower() else ev_title
    secondary = " · ".join(x for x in (matchup, comp, per_label,
                                       _date(base.get("game_start"))) if x) or None
    return {"primary": primary, "secondary": secondary, "size": size,
            "side": {"intent": intent, "held": side_word or team,
                     "is_no": bool(is_no)},
            "market": market, "kind": kind, "period": per_code,
            "team": team, "opponent": opp, "competition": comp,
            "sport": sport, "complete": not warnings, "warnings": warnings,
            "badge": badge(team, (held or {}).get("team_id") or
                           (held or {}).get("team_abbr") or team,
                           (held or {}).get("team_abbr")),
            "technical": tech, "source": "us_premap"}


CATALOGUE_SQL = (
    "SELECT identifier, event_slug, event_title, market_slug, question, kind, "
    "       line, side_norm, intent, signed, %s "
    "  FROM us_premap WHERE market_slug = ANY($1::text[])")
TEAM_COLS = ("team_abbr", "team_name", "team_safe_name", "team_id",
             "team_league", "game_start", "sports_type")


async def catalogue(conn, slugs: Iterable[str]) -> dict[str, list]:
    """Every catalogue row for these market slugs (both sides), by slug.
    Absent table or columns are read as absent metadata, never as an error
    that hides a position."""
    want = sorted({str(s).strip().lower() for s in slugs if s})
    if not want:
        return {}
    if not await conn.fetchval("SELECT to_regclass('us_premap') IS NOT NULL"):
        return {}
    have = {r["column_name"] for r in await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        " WHERE table_name = 'us_premap'")}
    cols = ", ".join(c if c in have else "NULL AS %s" % c for c in TEAM_COLS)
    out: dict[str, list] = {}
    for r in await conn.fetch(CATALOGUE_SQL % cols, want):
        out.setdefault(str(r["market_slug"]).lower(), []).append(dict(r))
    return out


async def items_for_intents(conn, intent_ids: Iterable[str]) -> dict[str, dict]:
    """The label inputs of funded orders, by intent id: the market, the order
    intent that was bought, and the size actually filled (or ordered)."""
    ids = sorted({str(i) for i in intent_ids if i})
    if not ids or not await conn.fetchval(
            "SELECT to_regclass('bettor_funded_intents') IS NOT NULL"):
        return {}
    has_f = await conn.fetchval(
        "SELECT to_regclass('bettor_funded_fills') IS NOT NULL")
    fills = ("(SELECT sum(qty)::float8 FROM bettor_funded_fills f "
             "  WHERE f.intent_id = i.intent_id) AS fq, "
             "(SELECT sum(qty * price)::float8 FROM bettor_funded_fills f "
             "  WHERE f.intent_id = i.intent_id) AS fn"
             if has_f else "NULL::float8 AS fq, NULL::float8 AS fn")
    out = {}
    for r in await conn.fetch(
            "SELECT i.intent_id, i.us_market_slug, i.order_intent, "
            "       i.quantity::float8 AS q, i.limit_price::float8 AS p, "
            "       i.collateral_usd::float8 AS c, %s "
            "  FROM bettor_funded_intents i WHERE i.intent_id = ANY($1::text[])"
            % fills, ids):
        fq = r["fq"] or 0.0
        out[r["intent_id"]] = {
            "market_slug": r["us_market_slug"], "intent": r["order_intent"],
            "contracts": fq if fq else r["q"],
            "avg_price": (r["fn"] / fq) if fq else r["p"],
            "invested": r["fn"] if fq else r["c"]}
    return out


async def resolve_many(conn, items: Iterable[dict]) -> list[dict]:
    """[{market_slug, intent, invested?, avg_price?, contracts?}] -> labels."""
    items = list(items)
    cat = await catalogue(conn, (i.get("market_slug") for i in items))
    return [resolve(cat.get(str(i.get("market_slug") or "").lower()),
                    market_slug=i.get("market_slug"), intent=i.get("intent"),
                    invested=i.get("invested"), avg_price=i.get("avg_price"),
                    contracts=i.get("contracts"),
                    title_hint=i.get("title_hint")) for i in items]


def text_label(lbl: dict) -> str:
    """One line for chat, audit and notification text."""
    s = lbl.get("primary") or ""
    if lbl.get("secondary"):
        s += " (" + lbl["secondary"] + ")"
    if lbl.get("warnings"):
        s += " [" + lbl["warnings"][0] + "]"
    return s


def describe() -> dict:
    return {"version": VERSION, "source": "us_premap (the venue catalogue) + "
            "bettor_sport_mapping", "logos": LOGO_NOTE,
            "warnings": [W_NO_ROW, W_NO_SIDE, W_NO_TEAM, W_NO_OPPONENT,
                         W_NO_KIND, W_NO_PERIOD, W_NO_LINE]}

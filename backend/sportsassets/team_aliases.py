"""Team-name aliasing for the trade desk search (owner report
2026-08-07: searching 'Braves' found nothing — Kalshi titles games by
CITY). A query term that is a team nickname also matches the team's
location word(s), so 'braves', 'atlanta' and 'ATL fans typing either'
all find the same game on either venue. Ambiguous nicknames (cardinals,
giants, rangers...) map to every location that uses them — a search is
a filter, not an oracle, and showing both Cardinals games beats hiding
one."""

from __future__ import annotations

# nickname -> location word(s) the venues title games with.
ALIASES: dict[str, tuple[str, ...]] = {
    # MLB
    "braves": ("atlanta",), "orioles": ("baltimore",),
    "red sox": ("boston",), "white sox": ("chicago",),
    "cubs": ("chicago",), "reds": ("cincinnati",),
    "guardians": ("cleveland",), "rockies": ("colorado",),
    "tigers": ("detroit",), "astros": ("houston",),
    "royals": ("kansas city",), "angels": ("los angeles",),
    "dodgers": ("los angeles",), "marlins": ("miami",),
    "brewers": ("milwaukee",), "twins": ("minnesota",),
    "mets": ("new york",), "yankees": ("new york",),
    "phillies": ("philadelphia",), "pirates": ("pittsburgh",),
    "padres": ("san diego",), "mariners": ("seattle",),
    "cardinals": ("st. louis", "arizona"),
    "rays": ("tampa bay",), "blue jays": ("toronto",),
    "nationals": ("washington",),
    "giants": ("san francisco", "new york"),
    "rangers": ("texas", "new york"),
    # WNBA
    "aces": ("las vegas",), "liberty": ("new york",),
    "sky": ("chicago",), "fever": ("indiana",),
    "wings": ("dallas",), "storm": ("seattle",),
    "lynx": ("minnesota",), "mercury": ("phoenix",),
    "sparks": ("los angeles",), "dream": ("atlanta",),
    "sun": ("connecticut",), "mystics": ("washington",),
    "valkyries": ("golden state",),
    # NBA
    "hawks": ("atlanta",), "celtics": ("boston",), "nets": ("brooklyn",),
    "hornets": ("charlotte",), "bulls": ("chicago",),
    "cavaliers": ("cleveland",), "mavericks": ("dallas",),
    "nuggets": ("denver",), "pistons": ("detroit",),
    "warriors": ("golden state",), "rockets": ("houston",),
    "pacers": ("indiana",), "clippers": ("los angeles",),
    "lakers": ("los angeles",), "grizzlies": ("memphis",),
    "heat": ("miami",), "bucks": ("milwaukee",),
    "timberwolves": ("minnesota",), "pelicans": ("new orleans",),
    "knicks": ("new york",), "thunder": ("oklahoma city",),
    "magic": ("orlando",), "76ers": ("philadelphia",),
    "sixers": ("philadelphia",), "suns": ("phoenix",),
    "trail blazers": ("portland",), "blazers": ("portland",),
    "spurs": ("san antonio",), "raptors": ("toronto",),
    "jazz": ("utah",), "wizards": ("washington",),
    "kings": ("sacramento", "los angeles"),
    # NFL
    "falcons": ("atlanta",), "ravens": ("baltimore",),
    "bills": ("buffalo",), "bears": ("chicago",),
    "bengals": ("cincinnati",), "browns": ("cleveland",),
    "cowboys": ("dallas",), "broncos": ("denver",),
    "lions": ("detroit",), "packers": ("green bay",),
    "texans": ("houston",), "colts": ("indianapolis",),
    "jaguars": ("jacksonville",), "chiefs": ("kansas city",),
    "raiders": ("las vegas",), "chargers": ("los angeles",),
    "rams": ("los angeles",), "dolphins": ("miami",),
    "vikings": ("minnesota",), "patriots": ("new england",),
    "saints": ("new orleans",), "eagles": ("philadelphia",),
    "steelers": ("pittsburgh",), "49ers": ("san francisco",),
    "niners": ("san francisco",), "seahawks": ("seattle",),
    "buccaneers": ("tampa bay",), "titans": ("tennessee",),
    "commanders": ("washington",),
    "jets": ("new york", "winnipeg"),
    "panthers": ("carolina", "florida"),
    # NHL
    "ducks": ("anaheim",), "bruins": ("boston",), "sabres": ("buffalo",),
    "flames": ("calgary",), "hurricanes": ("carolina",),
    "blackhawks": ("chicago",), "avalanche": ("colorado",),
    "blue jackets": ("columbus",), "stars": ("dallas",),
    "red wings": ("detroit",), "oilers": ("edmonton",),
    "wild": ("minnesota",), "canadiens": ("montreal",),
    "predators": ("nashville",), "devils": ("new jersey",),
    "islanders": ("new york",), "senators": ("ottawa",),
    "flyers": ("philadelphia",), "penguins": ("pittsburgh",),
    "sharks": ("san jose",), "kraken": ("seattle",),
    "blues": ("st. louis",), "lightning": ("tampa bay",),
    "maple leafs": ("toronto",), "canucks": ("vancouver",),
    "golden knights": ("vegas",), "capitals": ("washington",),
    "mammoth": ("utah",),
}

_MULTIWORD = sorted((n for n in ALIASES if " " in n),
                    key=len, reverse=True)

# Bet-type words carry no team information — a Kalshi game row matches
# "braves ml" on "braves" alone.
NOISE = {"ml", "moneyline", "line", "bet", "winner", "game", "match"}


def terms_of(query: str) -> list[set[str]]:
    """Query -> one alias-set per meaningful term. Multi-word nicknames
    collapse to a single term BEFORE splitting ('red sox' is one term,
    not a 'red' and a 'sox')."""
    q = (query or "").lower().strip()
    found: list[set[str]] = []
    for nick in _MULTIWORD:
        if nick in q:
            found.append({nick, *ALIASES[nick]})
            q = q.replace(nick, " ")
    for tok in q.split():
        if tok in NOISE:
            continue
        found.append({tok, *ALIASES.get(tok, ())})
    return found


def matches(query: str, fields: list[str | None]) -> bool:
    """Every meaningful query term (or one of its aliases) must appear
    somewhere in the row's fields."""
    hay = " | ".join(f.lower() for f in fields if f)
    return all(any(a in hay for a in aliases)
               for aliases in terms_of(query))

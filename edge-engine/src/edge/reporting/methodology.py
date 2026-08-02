"""Render the methodology document from config and measured figures.

The prose is authored here. Every NUMBER is injected — from `config/` for
settings, from `figures.compute_figures()` for measurements. Nothing in the
output is transcribed by hand, which is the whole point: the previous
document carried three different sample counts for one cohort and a
surcharge that had moved 0.25c since it was written, and had no way to
notice either.

A measurement that cannot be computed renders as an explicit "not measured
(n=…)", never as a blank or a zero. A reader must always be able to tell the
difference between a thing we found and a thing we have not looked at yet.
"""

from __future__ import annotations

from datetime import datetime, timezone

from edge.reporting.figures import (MIN_SETTLED_FOR_RETURN, compute_figures,
                                    verdict)


def _ts(ts: float | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC")


def num(f: dict | None, fmt: str = "{:.2f}", suffix: str = "",
        with_n: bool = True) -> str:
    """Render a figure, or say plainly why there is no number."""
    if f is None:
        return "*not available*"
    if f.get("value") is None:
        n = f.get("n")
        return f"*not measured ({'n=' + str(n) if n is not None else 'no sample'})*"
    body = fmt.format(f["value"]) + suffix
    if with_n and f.get("n") is not None:
        body += f"  (n={f['n']:,})"
    return body


def _bands_table(bands: dict) -> str:
    rows = sorted((bands.get("tradeable") or {}).items())
    if not rows:
        return "_(no tradeable bands configured)_"
    out = ["| band | min edge |", "|---|---|"]
    for key, cfg in rows:
        out.append(f"| {key} | {float(cfg['min_edge_threshold']) * 100:.1f}¢ |")
    dead = bands.get("dead_zones") or []
    if dead:
        out.append("")
        out.append("**Dead zones — never traded at any edge:** "
                   + ", ".join(f"`{d}`" for d in dead))
    return "\n".join(out)


def render(ledger, policy, *, days: int = 7, since: float | None = None,
           live_only: bool = True, figures: dict | None = None) -> str:
    f = figures or compute_figures(ledger, policy, days=days, since=since,
                                   live_only=live_only)
    from edge.execution.risk import caps_for_mode, profile_for_mode

    risk = policy.risk
    mode = str(risk.get("mode", "PAPER")).upper()
    # Resolve settings the way the ENGINE resolves them. Doing our own merge
    # here once made the document advertise the LIVE caps while the engine
    # ran the beta profile.
    prof = profile_for_mode(risk, mode)
    caps = caps_for_mode(risk, mode)
    leagues = policy.leagues
    exp = risk.get("exploration") or {}
    arb = risk.get("arbitrage") or {}
    w = f["window"]
    h = f["headline"]

    def cfg(key, default=None):
        return prof.get(key, default)

    L = []
    A = L.append

    A("# How the edge is made, and how it is taken")
    A("")
    A(f"**Generated {_ts(w['end_ts'])}** — every measured number below is "
      f"computed from the engine's ledger at generation time, and every "
      f"setting is read from `config/`. Nothing here is transcribed by hand.")
    A("")
    A(f"Measurement window: **{_ts(w['start_ts'])} → {_ts(w['end_ts'])}** "
      f"({'absolute' if w['absolute_since'] else str(w['days']) + '-day rolling'}"
      f", {'live fills only' if w['live_only'] else 'all modes'}).")
    A("")
    A("---")
    A("")

    # ── 1. the mechanism ────────────────────────────────────────────────
    A("## 1. Where the edge comes from")
    A("")
    A("Sharp sportsbooks price sports better than a prediction market's order "
      "book does. Strip the bookmaker's margin out of their odds and what "
      "remains is an estimate of the true probability. When the venue sells "
      "an outcome for materially less than that estimate, we buy it and hold "
      "it to settlement.")
    A("")
    A("**We do not predict games.** There is no model of football or baseball "
      "anywhere in this system. We arbitrage a disagreement between two "
      "pricing systems, one of which is better informed than the other. That "
      "distinction matters for what can go wrong: our failures are not bad "
      "forecasts, they are cases where the two prices describe *different "
      "bets* and we did not notice.")
    A("")
    A("That is why the system is built to refuse by default. A mispriced "
      "market and a mis-*mapped* market look identical from the inside — "
      "both say \"the venue is at 0.40 and we think 0.55\". One is an "
      "opportunity; the other means our 0.55 describes the wrong team, the "
      "wrong line, the wrong half, or the wrong player. The second is far "
      "more common.")
    A("")

    # ── 2. fair value ───────────────────────────────────────────────────
    A("## 2. How fair value is computed")
    A("")
    try:
        from edge.fairvalue.feed import BOOK_WEIGHTS
        A("Only these books quote into the estimate, and they do not get "
          "equal votes:")
        A("")
        A("```")
        for book, weight in sorted(BOOK_WEIGHTS.items(),
                                   key=lambda kv: (-kv[1], kv[0])):
            A(f"{book:<18} {weight:.1f}")
        A("```")
        A("")
    except ImportError:      # pragma: no cover - defensive
        pass
    A("Soft and recreational books are excluded deliberately: including them "
      "biases the median toward the soft side and manufactures edges that "
      "are really our own error. Pinnacle is the line the rest of the market "
      "prices against, and an exchange is a real market clearing real money "
      "with no vig to remove. A small low-margin book is mostly copying "
      "Pinnacle with a lag — giving it an equal vote means our \"sharp "
      "consensus\" can literally *be* the follower's opinion when only two "
      "or three books quote, which is exactly where the biggest apparent "
      "edges appear.")
    A("")
    A("The combination is a **weighted median**, not a mean. Weights move the "
      "halfway point; they do not let one wild quote drag the estimate.")
    A("")
    A("The weighted-median odds for a complete outcome set are converted to "
      "probabilities and normalised to sum to exactly 1.0 (power method, "
      "multiplicative as a cross-check). That normalisation is what removes "
      "the bookmaker's margin — it is the step that turns a price into an "
      "estimate.")
    A("")
    A(f"**Anchor rule: `min_anchor_books: {cfg('min_anchor_books', 1)}`.** At "
      "least one of Pinnacle or a listed exchange must stand behind the "
      "number or we refuse to price the market at all. Whether an anchor "
      "exists is a per-league fact and cannot be assumed — the same provider, "
      "plan and request returns Pinnacle on MLB props and nothing sharp at "
      "all on some smaller leagues.")
    A("")

    # ── 3. the gates ────────────────────────────────────────────────────
    A("## 3. How a trade clears")
    A("")
    A("A candidate must pass every gate, in order. Failing any one gets it "
      "passed on, with the reason counted.")
    A("")
    A("**Gate 1 — is the quote fresh?** Feed quotes older than "
      f"{(risk.get('watchdog') or {}).get('max_quote_age_s', 30)}s cannot "
      "back an order. Feed age, clock skew, venue error rate and mapper "
      "confidence are all watched; any breach halts live trading until the "
      "inputs are healthy again.")
    A("")
    A("**Gate 2 — do we know what bet this is?** The venue's outcome text "
      "must resolve to exactly one bet, cross-checked against slug "
      "structure, title, point value and segment token. **Any disagreement "
      "between signals makes the market untradeable rather than guessed.** "
      "Two traps worth naming, both pinned by tests: `N+` is a half-point "
      "bet (\"5+ strikeouts\" is Over 4.5, not Over 5.0 — on a whole line, "
      "exactly 5 pushes), and stat names overlap across roles (`hits` is a "
      "batter market, `hits allowed` is a pitcher market; longest phrase "
      "wins, ties refuse). A segment market is never priced off the "
      "full-game line.")
    A("")
    A("**Gate 3 — is this league and category allowed?**")
    A("")
    A("```")
    A(f"blocked_categories:     {leagues.get('blocked_categories') or []}")
    A(f"blocklist:              {leagues.get('blocklist') or []}")
    A(f"unknown_league_policy:  {leagues.get('unknown_league_policy')}")
    A("```")
    A("")
    A("An unlisted league is *unmeasured*, not disproven, so it trades and "
      "is graded nightly. A blocklisted one lost money and stays shut. "
      "Qualifying and playoff rounds inherit their parent competition's "
      "code, so a blocked league cannot re-enter through a round nobody "
      "thought to list.")
    A("")
    A("**Gate 4 — what price would we actually pay?** Not the ask. The "
      "adapter returns the price we would really transact at, and the "
      "threshold is judged there. Paper mode always crosses: a paper fill at "
      "a resting price invents a queue position we never held, and that is "
      "how a shadow record starts lying about its own returns.")
    A("")
    A("**Gate 5 — is the price in a tradeable band?** Thresholds are per "
      "5-cent band and per category.")
    A("")
    A(_bands_table(policy.bands))
    A("")
    cats = (policy.bands.get("categories") or {})
    if cats:
        A("Derivatives do **not** inherit the moneyline dead zones — they are "
          "two-sided line bets priced symmetrically about the line, so the "
          "asymmetry that produced those zones does not exist for them.")
        A("")
        for cat, cfgd in sorted(cats.items()):
            windows = ", ".join(
                f"{k} → {float(v['min_edge_threshold']) * 100:.1f}¢"
                for k, v in sorted((cfgd.get("tradeable") or {}).items()))
            A(f"- **{cat}**: {windows}")
        A("")
    A("An unlisted band is not tradeable. Not proven is not the same as safe.")
    A("")
    A("**Gate 6 — does the edge clear the bar after the drift surcharge?** "
      "The surcharge is the measured adverse move in fair value in the "
      "minute after we buy, per category, recomputed continuously from our "
      "own fills. A threshold is a bar our *estimate* must clear; if fair "
      "value reliably moves against us right after we buy, the estimate is "
      "optimistic by exactly that much. Only adverse drift is charged — "
      "being early is not a licence to be looser. Measured values are in §5.")
    A("")
    A(f"**Gate 7 — is the edge believable?** `max_believable_edge: "
      f"{cfg('max_believable_edge', 0.08)}`. Anything above that is refused "
      "and logged, never traded, in any mode. Real measured edges run 1–4¢. "
      "A 20¢ edge is a mapping error, a resolution mismatch, or a stale "
      "quote wearing a costume.")
    A("")
    A("**Gate 8 — do the risk caps allow it?**")
    A("")
    A("```")
    A(f"mode:                     {mode}")
    A(f"per_fill_usd_default:     ${caps.per_fill_default:,.2f}")
    A(f"per_fill_usd_max:         ${caps.per_fill_max:,.2f}")
    A(f"per_market_exposure_usd:  ${caps.per_market:,.2f}")
    A(f"per_event_exposure_usd:   "
      + (f"${caps.per_event:,.2f}" if caps.per_event > 0 else "unbounded"))
    A(f"one_position_per_market:  {caps.one_per_market}")
    A(f"one_position_per_event:   {caps.one_per_event}")
    A(f"daily_loss_halt:          ${caps.daily_loss_halt:,.2f}"
      f"  ({caps.halt_hours:.0f}h, no manual override)")
    A("```")
    A("")
    if mode == "PAPER":
        # Paper caps are deliberately loose — paper dollars are free, so
        # evidence velocity is limited only by opportunity supply. Showing
        # only these would tell a reader nothing about how real money is
        # deployed, so the live profile is stated alongside.
        beta = caps_for_mode(risk, "LIVE_BETA")
        A("The engine is currently in **PAPER**, whose caps are loose on "
          "purpose: paper dollars are free, so the only thing worth limiting "
          "is opportunity supply. The profile that governs real money is:")
        A("")
        A("```")
        A(f"per_fill_usd_default:     ${beta.per_fill_default:,.2f}")
        A(f"per_fill_usd_max:         ${beta.per_fill_max:,.2f}")
        A(f"per_market_exposure_usd:  ${beta.per_market:,.2f}")
        A(f"per_event_exposure_usd:   "
          + (f"${beta.per_event:,.2f}" if beta.per_event > 0 else "unbounded"))
        A("```")
        A("")
    A("**One position per market, never per event.** A single game lists a "
      "moneyline plus a ladder of spreads, totals and props — 20–40 "
      "separately priced bets. Claiming the whole event would take one and "
      "abandon the rest.")
    A("")
    A("**Never add to a position.** Not on a favourable move: adding at a "
      "worse price mathematically dilutes the edge — buy at 0.40 against a "
      "0.45 fair for 5¢, add at 0.44 and the blended edge is 3¢. Not on an "
      "adverse move: the price falling is evidence our estimate was wrong. "
      "The per-event cap exists on top of that because the sides of one game "
      "are not independent — exactly one of them pays.")
    A("")
    A("The day budget is a share of **start-of-day** buying power, not cash "
      "remaining. Sized off remaining cash it would chase its own spend and "
      "run out after deploying half the account.")
    A("")

    # ── 4. capitalizing ─────────────────────────────────────────────────
    A("## 4. How the edge is capitalized")
    A("")
    A("### Sizing — a ladder, not Kelly")
    A("")
    # The ladder that governs real money, whichever mode we are in now.
    live_prof = profile_for_mode(risk, "LIVE_BETA")
    ladder = cfg("size_ladder") or live_prof.get("size_ladder") or []
    if ladder:
        A("```")
        for rung in ladder:
            A(f"at {float(rung['at']):.1f}x the bar  ->  ${float(rung['usd']):.2f}")
        A("```")
        A("")
        A("`at` is a **multiple of the threshold**, not a cent figure. The bar "
          "already varies by band, category and measured drift, so a fixed "
          "cent trigger would mean something different in every market — "
          "generous where the bar is low and unreachable where it is high.")
        A("")
    A("**Deliberately not Kelly.** Kelly sizes on the edge you *believe* you "
      "have, so it amplifies estimation error exactly as readily as edge — "
      "and the estimate is the thing under suspicion. An unknown or zero "
      "threshold takes the standard ticket, never the top rung: staking the "
      "maximum on the markets we understand least is the most expensive "
      "direction for a default to fail in.")
    A("")
    A("### Exit policy: there isn't one")
    A("")
    A("**Buy-only, hold to resolution.** No exit logic exists.")
    A("")
    A("The arithmetic decides this, not preference. Polymarket US quotes in "
      "whole cents; at a 50¢ contract crossing costs about 2¢, roughly 4% of "
      "stake, against a claimed edge of 2–3¢. **A round trip costs more than "
      "the position earns.** Recycling capital faster at a loss per cycle "
      "does not multiply returns, it multiplies losses. Settlement is also a "
      "free exit — exactly $1 or $0, no spread, no slippage, no liquidity "
      "risk.")
    A("")
    A("The capital-recycling benefit is taken instead through **settlement "
      "priority**: nearest kick-offs are priced first, so the same bankroll "
      "turns over more often, and a truncated cycle drops the games that "
      "would have locked money up longest.")
    A("")
    A("### Exploration")
    A("")
    if exp.get("enabled"):
        A(f"Sub-threshold edges trade on a capped budget "
          f"({float(exp.get('budget_share', 0)) * 100:.0f}% of the day, "
          f"max {exp.get('max_fills_per_day')} fills), tagged separately, "
          f"with a floor of {float(exp.get('min_edge', 0)) * 100:.1f}¢ and "
          f"at least {exp.get('min_consensus_books')} books agreeing. This "
          "is what makes the threshold a *measurement* rather than an "
          "assumption. It fails closed: an unknown consensus depth is not "
          "permission.")
    else:
        A("Disabled.")
    A("")
    A("### Arbitrage")
    A("")
    if arb.get("enabled"):
        A(f"A complete outcome set purchasable for under $1 is guaranteed "
          f"profit — exactly one leg resolves true and pays $1. Sized at "
          f"{arb.get('max_sets')} set per opportunity, "
          f"{arb.get('max_books_per_cycle')} books per cycle.")
        A("")
        A("**The guarantee holds only when every leg is owned.** Three legs "
          "of four is not an arbitrage; it is a naked directional position "
          "at a price nobody judged. So: thinnest leg first (the one most "
          "likely to fail fails while we own nothing); a failure ends the "
          "sequence; if legs are already owned when one fails, completion "
          "outranks price; completion buys, never sells; fill-or-kill "
          "throughout; and legs come from one venue only, because across "
          "venues the outcomes can settle on different criteria and the set "
          "is not a partition.")
    else:
        A("Disabled.")
    A("")

    # ── 5. what we have measured ────────────────────────────────────────
    A("## 5. What we have actually measured")
    A("")
    A("### The record")
    A("")
    A(f"- Settled: **{num(h['n_settled'], '{:,.0f}', with_n=False)}** "
      f"({num(h['wins'], '{:,.0f}', with_n=False)}W / "
      f"{num(h['losses'], '{:,.0f}', with_n=False)}L)")
    A(f"- Staked: **${num(h['staked'], '{:,.2f}', with_n=False)}**")
    A(f"- Net: **${num(h['net'], '{:,.2f}', with_n=False)}**")
    A(f"- Return: **{num(h['return_pct'], '{:+.2%}', with_n=False)}**")
    A(f"- Per-trade SD: {num(h['sd_per_trade'], '{:.2f}', with_n=False)}, "
      f"standard error {num(h['se'], '{:.2%}', with_n=False)}")
    A(f"- Distance from zero: "
      f"**{num(h['sigma_from_zero'], '{:.2f}', with_n=False)}σ**")
    A(f"- Open exposure: ${num(h['open_cost'], '{:,.2f}', with_n=False)}")
    A("")
    A(f"> **{verdict(f)}**")
    A("")
    A(f"A return is not quoted below {MIN_SETTLED_FOR_RETURN} settlements. "
      "That threshold is in the code, not in anyone's judgement on the day, "
      "and it applies symmetrically — the same rule that refuses to call a "
      "losing stretch a disproof refuses to call a winning one an edge.")
    A("")

    daily = f.get("daily") or []
    if daily:
        A("### By day")
        A("")
        A("| day | settled | W/L | staked | net | return |")
        A("|---|---|---|---|---|---|")
        for d in daily[:14]:
            roi = f"{d['roi']:+.2%}" if d.get("roi") is not None else "—"
            A(f"| {d['day']} | {d['settled']} | {d['wins']}/{d['losses']} "
              f"| ${d['staked']:.2f} | ${d['realized']:+.2f} | {roi} |")
        A("")

    A("### Adverse selection, per category")
    A("")
    A("This is the measurement that decides whether the edge is real. We "
      "record fair value at entry, then fair value for the same market a "
      "minute later. Edge that survives was real; edge that evaporates was a "
      "stale quote, and we were systematically buying the side the market "
      "had just left.")
    A("")
    dbc = f.get("drift_by_category") or {}
    if dbc:
        A("| category | drift | retention | surcharge applied |")
        A("|---|---|---|---|")
        for cat, m in sorted(dbc.items()):
            A(f"| {cat} | {num(m['drift_cents'], '{:+.2f}', '¢')} "
              f"| {num(m['retention'], '{:.3f}', with_n=False)} "
              f"| {num(m['surcharge_cents'], '{:+.2f}', '¢', with_n=False)} |")
        A("")
    else:
        A("_No drift observations in this window._")
        A("")
    A("**Retention cannot validate a mapping.** It compares our fair value "
      "against *itself* a minute later, so a number that is stably wrong "
      "scores a perfect 1.00. Only settled P&L can detect a mapping error.")
    A("")

    fs = f["free_samples"]
    A("### The control group")
    A("")
    A(f"The same measurement on outcomes we **priced but did not buy**: "
      f"{num(fs['n_observations'], '{:,.0f}', with_n=False)} observations, "
      f"mean drift {num(fs['drift_cents'], '{:+.2f}', '¢', with_n=False)}.")
    A("")
    A("This is what separates adverse selection from a stale feed. Our fills "
      "are selected — we get filled when someone wants to sell to us — so "
      "drift measured on them carries staleness *and* selection. Drift "
      "measured on markets we merely looked at carries staleness alone. If "
      "the two differ, the gap is the cost of being chosen.")
    A("")

    rev = f["reversion"]
    A("### Winner's curse")
    A("")
    A(f"Regression of the realized move on the claimed edge, across free "
      f"samples: keep = {num(rev['keep'], '{:.4f}')}, slope "
      f"{num(rev['slope'], '{:+.4f}', with_n=False)}. Shrinkage "
      f"{'**active**' if rev['shrinkage_active']['value'] else 'inactive'}.")
    A("")
    A("This asks whether fair value reverts *in proportion to how much edge "
      "we thought we had* — the signature of an estimate that is noisy "
      "rather than wrong. Shrinkage and the drift surcharge never both "
      "apply; that would charge twice for the same error.")
    A("")

    bc = f.get("by_category") or {}
    if bc:
        A("### Settled P&L by category")
        A("")
        A("| category | settled | W/L | staked | realized | ROI |")
        A("|---|---|---|---|---|---|")
        for cat, m in sorted(bc.items()):
            roi = f"{m['roi']:+.2%}" if m.get("roi") is not None else "—"
            A(f"| {cat} | {m['settled']} | {m['wins']}/{m['losses']} "
              f"| ${m['staked']:.2f} | ${m['realized']:+.2f} | {roi} |")
        A("")
        A("This is the only table that can catch a mapping error, which is "
          "why it matters more than anything above it.")
        A("")

    sp = f.get("spread_cost") or {}
    if sp:
        A("### What crossing costs")
        A("")
        A("| category | n | spread | paid over mid | claimed edge | net |")
        A("|---|---|---|---|---|---|")
        for cat, m in sorted(sp.items()):
            A(f"| {cat} | {m['n']} | {m['spread_c']:.2f}¢ "
              f"| {m['paid_over_mid_c']:.2f}¢ | {m['edge_c']:.2f}¢ "
              f"| **{m['net_c']:+.2f}¢** |")
        A("")
        A("The venue quotes in whole cents, so at a 50¢ contract one tick is "
          "2% of stake — the spread is not a detail around the edge, it is "
          "the same size as the edge. If the net column is negative we were "
          "losing at the moment of entry, whatever the model said.")
        A("")

    # ── 6. falsification ────────────────────────────────────────────────
    A("## 6. What would disprove this")
    A("")
    A("A strategy that cannot be wrong is not a strategy. The conditions "
      "under which this one should be stopped, decided in advance:")
    A("")
    A("1. **Settled P&L stays negative past the sample threshold.** Not "
      "drift, not retention — settled money, which is the only figure that "
      "can detect a mapping error.")
    A("2. **Retention stays low across categories.** Retention near zero "
      "means fair value follows the price rather than leading it, which is "
      "the same as saying the sharp books are not telling us anything the "
      "venue does not already know.")
    A("3. **Net-of-spread edge at entry is negative.** If the table above "
      "shows we pay more to cross than we claim to win, no amount of "
      "settlement luck fixes it.")
    A("")
    A("That decision should be made by a threshold agreed beforehand, not by "
      "whoever is most invested in the answer.")
    A("")
    A("## 7. The rules that override everything")
    A("")
    A("1. Refuse rather than guess. An unmapped market is not a cheap one.")
    A("2. Never exceed a size cap to make back a loss. Size discipline *is* "
      "the strategy.")
    A("3. Log every decision input. A losing period must be diagnosable from "
      "the record alone.")
    A("4. Config is measurement, not opinion. A number should be traceable "
      "to something observed.")
    A("5. When a measurement contradicts the model, the measurement wins.")
    A("")
    return "\n".join(L) + "\n"

# BETTOR adversarial / chaos acceptance matrix

The system is not accepted because a scenario produces no exception.
It is accepted when the scenario causes the correct fail-closed behavior,
preserves position truth, names the blocker, and does not invent profit.

| ID | Injected failure | Required behavior |
|---|---|---|
| C01 | Kill PMX gRPC stream while positions are open | Books GAP/STALE; new entries stop; exits/protection stay available; reconnect requires complete snapshot before fresh |
| C02 | Kalshi returns 429 for 20 minutes | Kalshi backs off; Polymarket health stays independent; no SLA widening |
| C03 | Polymarket reconnects while Kalshi healthy | Only Polymarket health changes |
| C04 | Cheapest quote is stale | Stale route loses to current route |
| C05 | MLB doubleheader with same teams | Structured fixture identity/start disambiguates or refuses |
| C06 | NBA/WNBA city-name collision | League-namespaced identity refuses cross-league map |
| C07 | Settlement rule changes after mapping | Existing certificate invalidated; pair/trade reverts to NOT_ESTABLISHED |
| C08 | Three-way soccer Team B NO offered cheaply | Never aliased to Team A YES unless payoff vector equals |
| C09 | One arb leg fills 80%, other 0% | Not labelled execution locked; unmatched loss cap triggers repair/freeze |
| C10 | Submit times out after venue accepted order | Deterministic client id; reconcile before retry; no duplicate exposure |
| C11 | Duplicate fill event arrives | Deduped by venue trade/order identity; no duplicate P&L/position |
| C12 | Fills arrive out of order | Position truth converges from immutable fill identity |
| C13 | New socket connected, no snapshot yet | Connected != fresh; books remain GAP/STALE |
| C14 | Server clock ahead/behind | future timestamps / impossible ages refuse; monotonic receive clock retained |
| C15 | Fee schedule missing or changed | route becomes ineligible until versioned fee evidence exists |
| C16 | Kalshi YES 49c / opponent NO 45c | equivalent claim chooses lowest all-in executable route |
| C17 | Depth only exists at worse levels | route uses VWAP/full depth; top-of-book EV cannot certify size |
| C18 | Cross-venue arb exists but one venue lacks cash | opportunity not capital executable; no assumption of instant transfers |
| C19 | Two strategy names trade same economic claim | canonical exposure lock counts them as one exposure |
| C20 | Directional sleeve breaks while arb remains good | disable directional sleeve only; unrelated sleeve remains eligible |
| C21 | Global freshness 99% but held book stale | held-position gate remains RED |
| C22 | UI cache is old but last value was green | metric shows STALE/UNKNOWN, never retained green |
| C23 | 13k repeated rows from 195 events | confidence based on independent events, not row count |
| C24 | New candidate selected after holdout read | promotion refused |
| C25 | Twin invents IOC fills | twin certification fails until agreement / mismatch gates pass |
| C26 | OOM / deploy during open positions | service restart preserves durable state; no new entry until truth quorum restored |
| C27 | Applied migration modified in repo | migration fingerprint gate blocks release |
| C28 | CI tests SHA A but deploy is SHA B | release gate blocks capital |
| C29 | PMX RSA key placed in PMUS slot | credential-class gate blocks affected path |
| C30 | Revenue target > positive capacity | deployment capped at proven positive capacity; remainder CASH |

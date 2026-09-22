"""Stream ONE public Time & Sales CSV, filter it, reconcile it.

Run by .github/workflows/fetch-docs.yml when the dispatched url carries
the `#stream` fragment. One file per dispatch, which is the bound.

NOTHING IS HELD. The file is read in 1 MiB chunks, hashed and counted
as it passes, and only matching rows are written out. Peak source disk
is zero and peak memory is one chunk, so a 223 MB file costs the same
as a small one.

COVERAGE IS CONFIRMED FROM THE ROWS, NOT THE FILENAME. The file named
20260913 was observed to OPEN with rows stamped 2026-09-12T17:00 ET, so
the filename is a session label rather than a statement about content.
This records the first and last timestamp actually seen, the distinct
calendar dates present, and per-market print counts and quantities --
which is what a later join has to be reconciled against.
"""
import csv, gzip, hashlib, io, json, os, sys, collections, urllib.request

URL = os.environ["URL_NOFRAG"]
UA = "sportsassets-timesales-reader"
MAX_BYTES = int(os.environ.get("TS_MAX_BYTES") or 400_000_000)

# Our captured universe, fixed here so no input is needed.
SYMBOLS = {
    "aec-boxing-canalv-chrmbi-2026-10-31-canalv-chrmbi",
    "aec-cfb-coast-del-2026-09-19",
    "aec-cfb-kentst-ohiost-2026-09-19",
    "aec-cfb-portst-ore-2026-09-18",
    "aec-cfb-uwg-etnst-2026-09-19",
    "aec-nfl-atl-pit-2026-09-13",
    "aec-nfl-bal-ind-2026-09-13",
    "aec-nfl-chi-car-2026-09-13",
    "aec-nfl-cle-jax-2026-09-13",
    "asc-epl-mnu-mnc-2026-09-13-fh-neg-1pt5",
    "atc-lmx-ame-tij-2026-09-05-tij",
    "atc-lmx-pue-tol-2026-09-04-draw",
}


class Counting(io.RawIOBase):
    def __init__(self, fh, cap):
        self._fh, self.cap = fh, cap
        self.sha = hashlib.sha256()
        self.n = 0
        self.truncated = False

    def readable(self):
        return True

    def readinto(self, b):
        if self.n >= self.cap:
            self.truncated = True
            return 0
        chunk = self._fh.read(min(len(b), self.cap - self.n))
        if not chunk:
            return 0
        self.sha.update(chunk)
        self.n += len(chunk)
        b[:len(chunk)] = chunk
        return len(chunk)


name = URL.split("/")[-1].split("?")[0]
req = urllib.request.Request(URL, headers={"User-Agent": UA})
resp = urllib.request.urlopen(req, timeout=900)
declared = resp.headers.get("Content-Length")
print("== %s declared Content-Length=%s cap=%d ==" % (name, declared,
                                                      MAX_BYTES))
raw = Counting(resp, MAX_BYTES)
buf = io.TextIOWrapper(io.BufferedReader(raw, 1 << 20), encoding="utf-8",
                       errors="replace", newline="")

out = gzip.open("out/%s.filtered.csv.gz" % name, "wt", newline="")
w = csv.writer(out)
w.writerow(["source_file", "transaction_time", "symbol", "last_price",
            "last_quantity"])

hdr = None
rows = kept = bad = 0
per_symbol = collections.defaultdict(lambda: [0, 0.0])
dates = collections.Counter()
offsets = collections.Counter()
first_ts = last_ts = None
price_widths = collections.Counter()

for i, r in enumerate(csv.reader(buf)):
    if i == 0:
        hdr = r
        continue
    rows += 1
    if len(r) < 4:
        bad += 1
        continue
    tt, sym, px, qty = r[0], r[1], r[2], r[3]
    if first_ts is None:
        first_ts = tt
    last_ts = tt
    dates[tt[:10]] += 1
    if len(tt) >= 6:
        offsets[tt[-6:]] += 1
    price_widths[len(px.split(".")[-1]) if "." in px else 0] += 1
    try:
        q = float(qty)
    except ValueError:
        q = 0.0
        bad += 1
    a = per_symbol[sym]
    a[0] += 1
    a[1] += q
    if sym in SYMBOLS:
        w.writerow([name, tt, sym, px, qty])
        kept += 1
out.close()

rep = {
    "url": URL, "file": name, "header": hdr,
    "declared_content_length": declared,
    "bytes_read": raw.n, "truncated_at_cap": raw.truncated,
    "sha256_of_bytes_read": raw.sha.hexdigest(),
    "rows_total": rows, "rows_malformed": bad,
    "rows_kept_for_our_markets": kept,
    "distinct_symbols_in_file": len(per_symbol),
    "first_transaction_time": first_ts,
    "last_transaction_time": last_ts,
    "calendar_dates_present": dict(dates),
    "utc_offsets_present": dict(offsets),
    "price_decimal_widths": dict(price_widths),
    "our_markets": {s: {"prints": per_symbol[s][0],
                        "quantity": round(per_symbol[s][1], 6)}
                    for s in sorted(SYMBOLS)},
    "joined_any": sorted(s for s in SYMBOLS if per_symbol[s][0] > 0),
    "top_symbols": sorted(
        ({"symbol": k, "prints": v[0], "quantity": round(v[1], 4)}
         for k, v in per_symbol.items()), key=lambda d: -d["quantity"])[:10],
}
with open("out/%s.report.json" % name, "w") as fh:
    json.dump(rep, fh, indent=2, sort_keys=True)

print(json.dumps({k: rep[k] for k in (
    "file", "header", "bytes_read", "truncated_at_cap", "rows_total",
    "rows_malformed", "rows_kept_for_our_markets",
    "distinct_symbols_in_file", "first_transaction_time",
    "last_transaction_time", "calendar_dates_present",
    "utc_offsets_present", "price_decimal_widths", "joined_any")},
    indent=2))
print("== OUR MARKETS ==")
print(json.dumps(rep["our_markets"], indent=2, sort_keys=True))

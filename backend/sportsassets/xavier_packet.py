"""XAVIER'S MANAGEMENT PACKET: NO MANAGEMENT ACTION WITHOUT CURRENT EVIDENCE.

Xavier may emit a management action -- HOLD, EXIT, REDUCE or a HEDGE
(netting / indirect hedge) -- for a position ONLY when its packet carries
every element below. Otherwise the review is a RECORDED REFUSAL naming each
missing element, never a silent HOLD on stale data:

  NO_RECONCILED_POSITION_QTY  the position's open quantity (from its fills)
                              does not agree with the ledger's FILL - SALE -
                              SETTLEMENT entries, or could not be read
  NO_FRESH_PROBABILITY        the probability is not FRESH_CURRENT_PROBABILITY
                              by its own freshness rule (the Pinnacle /
                              PinnAPI 30 s rule, paper_xavier.
                              probability_evidence) -- an entry-time or older
                              probability is never management evidence
  NO_CURRENT_EXECUTABLE_BOOK  the held market's mark is not FRESH or
                              QUIET_VALID (bettor_paper_freshness, the 300 s
                              SLA): no current executable bid / ask
  NO_EXECUTABLE_EXIT_DEPTH    the current book shows no executable depth on
                              the side a close consumes
  NO_SETTLEMENT_IDENTITY      the contract's settlement identity (payout
                              event + complement flag of the entry valuation)
                              is not established
  NO_PROTECTION_STATE         the standing protective order's state could not
                              be read

The probability and book rules are the existing ones; nothing here defines a
threshold. What a refusal still permits is unchanged: the cost-recovery
protection (priced from quantity, basis and fees only) is maintained, and no
discretionary sale is possible. Pure: stdlib only.
"""
from __future__ import annotations

VERSION = "XAVIER_MANAGEMENT_PACKET_V1"

R_XAVIER_PACKET_INCOMPLETE = "XAVIER_MANAGEMENT_PACKET_INCOMPLETE"

P_QTY = "NO_RECONCILED_POSITION_QTY"
P_PROBABILITY = "NO_FRESH_PROBABILITY"
P_BOOK = "NO_CURRENT_EXECUTABLE_BOOK"
P_DEPTH = "NO_EXECUTABLE_EXIT_DEPTH"
P_SETTLEMENT = "NO_SETTLEMENT_IDENTITY"
P_PROTECTION = "NO_PROTECTION_STATE"
ELEMENTS = (P_QTY, P_PROBABILITY, P_BOOK, P_DEPTH, P_SETTLEMENT,
            P_PROTECTION)

#: the probability's evidence state that alone is management evidence
#: (= paper_xavier.E_FRESH / xavier_freshness.E_FRESH)
E_FRESH = "FRESH_CURRENT_PROBABILITY"
#: the mark classes that are a current executable book
#: (= bettor_paper_freshness.FRESHLY_MANAGEABLE, pinned by a test)
CURRENT_BOOK_CLASSES = ("FRESH", "QUIET_VALID")
#: the management actions the packet gates
MANAGEMENT_ACTIONS = ("HOLD", "EXIT", "REDUCE", "NETTING",
                      "ACQUIRE_INDIRECT_HEDGE", "HEDGE", "DIRECT_HEDGE",
                      "INDIRECT_HEDGE", "SAME_VENUE_NETTING")


def build(*, residual: dict | None, evidence_state, probability_source=None,
          valuation_id=None, mark: dict | None, mark_class,
          settlement: dict | None, protection: dict | None) -> dict:
    """THE PACKET (pure): every element with its value and whether it is
    present."""
    res = residual or {}
    mk = mark or {}
    st = settlement or {}
    pr = protection or {}
    depth = mk.get("exit_depth_at_mark")
    return {
        "version": VERSION,
        "residual": {"open_qty": res.get("open_qty"),
                     "ledger_open_qty": res.get("ledger_open_qty"),
                     "present": bool(res.get("reconciled"))},
        "probability": {"evidence_state": evidence_state,
                        "source": probability_source,
                        "valuation_id": valuation_id,
                        "present": evidence_state == E_FRESH},
        "book": {"mark_class": mark_class,
                 "obs_id": mk.get("obs_id"),
                 "observed_at": mk.get("observed_at"),
                 "age_s": mk.get("age_s"), "bid": mk.get("bid"),
                 "ask": mk.get("ask"),
                 "present": mark_class in CURRENT_BOOK_CLASSES},
        "exit_depth": {"at_mark": depth,
                       "total": mk.get("exit_depth_total"),
                       "present": (mark_class in CURRENT_BOOK_CLASSES
                                   and depth is not None
                                   and float(depth) > 0)},
        "settlement": {"fingerprint": st.get("fingerprint"),
                       "payout_event": st.get("payout_event"),
                       "payout_is_complement": st.get(
                           "payout_is_complement"),
                       "present": bool(st.get("fingerprint"))},
        "protection": {"state": pr.get("state"),
                       "order_id": pr.get("order_id"),
                       "present": bool(pr.get("known"))
                       and pr.get("state") is not None},
    }


_KEYS = (("residual", P_QTY), ("probability", P_PROBABILITY),
         ("book", P_BOOK), ("exit_depth", P_DEPTH),
         ("settlement", P_SETTLEMENT), ("protection", P_PROTECTION))


def gate(packet: dict | None) -> dict:
    """{complete, missing[...], refusal} (pure). A packet that is absent is
    missing every element."""
    p = packet or {}
    missing = [code for key, code in _KEYS
               if not (p.get(key) or {}).get("present")]
    return {"complete": not missing, "missing": missing,
            "refusal": None if not missing else R_XAVIER_PACKET_INCOMPLETE}


def permits(action, packet: dict | None) -> bool:
    """Whether `action` may be emitted on this packet (pure): a management
    action only on a complete packet; anything else (a non-action such as
    WAITING_FOR_FRESH_EVIDENCE) is not gated here."""
    if action not in MANAGEMENT_ACTIONS:
        return True
    return gate(packet)["complete"]

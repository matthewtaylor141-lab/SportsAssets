from __future__ import annotations

def stream_currency_gate(*, connected:bool, complete_snapshot_received:bool,
                         gap:bool, book_age_s:float|None, max_book_age_s:float,
                         source:str)->dict:
    blockers=[]
    if not connected: blockers.append("STREAM_NOT_CONNECTED")
    if not complete_snapshot_received: blockers.append("NO_AUTHORITATIVE_SNAPSHOT_AFTER_CONNECT")
    if gap: blockers.append("STREAM_GAP")
    if book_age_s is None or book_age_s>max_book_age_s: blockers.append("BOOK_STALE")
    return {"green":not blockers,"source":source,"blockers":tuple(blockers)}

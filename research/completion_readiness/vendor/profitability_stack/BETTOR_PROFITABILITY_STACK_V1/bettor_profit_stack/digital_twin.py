from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Callable, Iterable, Any
import heapq
import itertools

class EventType(str, Enum):
    BOOK = "BOOK"
    TRADE = "TRADE"
    DECISION = "DECISION"
    ORDER_ACK = "ORDER_ACK"
    CANCEL_ACK = "CANCEL_ACK"
    SETTLEMENT = "SETTLEMENT"

@dataclass(order=True, frozen=True)
class MarketEvent:
    ts: float
    seq: int
    kind: EventType = field(compare=False)
    market_id: str = field(compare=False)
    payload: dict = field(compare=False, default_factory=dict)

@dataclass
class BookState:
    bid: float | None = None
    bid_qty: float = 0.0
    ask: float | None = None
    ask_qty: float = 0.0
    observed_at: float | None = None

@dataclass
class SimOrder:
    order_id: str
    market_id: str
    side: str
    price: float
    qty: float
    remaining: float
    placed_at: float
    active_at: float
    queue_ahead: float = 0.0
    state: str = "PENDING_ACK"
    filled_qty: float = 0.0
    avg_fill_price: float | None = None

@dataclass(frozen=True)
class OrderIntent:
    action: str
    market_id: str
    side: str
    price: float | None = None
    qty: float = 0.0
    order_id: str | None = None

@dataclass
class TwinConfig:
    submit_latency_s: float = 0.050
    cancel_latency_s: float = 0.050
    queue_fraction_ahead: float = 1.0
    taker_crosses_immediately: bool = True
    partial_fills: bool = True

@dataclass
class ReplayReceipt:
    decisions: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    fills: list[dict] = field(default_factory=list)
    settlements: list[dict] = field(default_factory=list)
    no_lookahead_violations: int = 0

class TwinView:
    """Read-only strategy view. It contains only state observed at or before now."""
    def __init__(self, now: float, books: dict[str, BookState], positions: dict[str, float]):
        self.now = now
        self._books = {k: BookState(**asdict(v)) for k,v in books.items()}
        self._positions = dict(positions)

    def book(self, market_id: str) -> BookState | None:
        b = self._books.get(market_id)
        return None if b is None else BookState(**asdict(b))

    def position(self, market_id: str) -> float:
        return self._positions.get(market_id, 0.0)

    @property
    def positions(self):
        return dict(self._positions)

class DigitalTwin:
    """Deterministic event-by-event exchange simulator.

    The strategy callback is invoked only after the current event has been applied
    and receives a TwinView that cannot access future events.
    """
    def __init__(self, config: TwinConfig = TwinConfig()):
        self.config = config
        self.books: dict[str, BookState] = {}
        self.orders: dict[str, SimOrder] = {}
        self.positions: dict[str, float] = {}
        self.cash: float = 0.0
        self.receipt = ReplayReceipt()
        self._counter = itertools.count(1)
        self._internal: list[MarketEvent] = []

    def _book(self, market_id: str) -> BookState:
        return self.books.setdefault(market_id, BookState())

    def _schedule(self, ts: float, kind: EventType, market_id: str, payload: dict):
        heapq.heappush(self._internal, MarketEvent(ts, 10_000_000 + next(self._counter), kind, market_id, payload))

    def _fill(self, o: SimOrder, qty: float, price: float, ts: float, reason: str):
        qty = max(0.0, min(o.remaining, qty))
        if qty <= 0:
            return
        prev_notional = (o.avg_fill_price or 0.0) * o.filled_qty
        o.filled_qty += qty
        o.remaining -= qty
        o.avg_fill_price = (prev_notional + qty * price) / o.filled_qty
        o.state = "FILLED" if o.remaining <= 1e-9 else "PARTIALLY_FILLED"
        signed = qty if o.side.upper() == "BUY" else -qty
        self.positions[o.market_id] = self.positions.get(o.market_id, 0.0) + signed
        self.cash += -qty * price if o.side.upper() == "BUY" else qty * price
        self.receipt.fills.append({
            "ts": ts, "order_id": o.order_id, "market_id": o.market_id,
            "side": o.side, "qty": qty, "price": price, "reason": reason
        })

    def _ack_order(self, e: MarketEvent):
        oid = e.payload["order_id"]
        o = self.orders.get(oid)
        if not o or o.state != "PENDING_ACK":
            return
        o.state = "RESTING"
        b = self._book(o.market_id)
        # Marketable at activation -> taker-like immediate fill.
        if self.config.taker_crosses_immediately:
            if o.side == "BUY" and b.ask is not None and o.price >= b.ask:
                self._fill(o, o.remaining, b.ask, e.ts, "CROSSED_AT_ACK")
                return
            if o.side == "SELL" and b.bid is not None and o.price <= b.bid:
                self._fill(o, o.remaining, b.bid, e.ts, "CROSSED_AT_ACK")
                return
        touch_qty = b.bid_qty if o.side == "BUY" and b.bid == o.price else b.ask_qty if o.side == "SELL" and b.ask == o.price else 0.0
        o.queue_ahead = max(0.0, touch_qty * self.config.queue_fraction_ahead)

    def _ack_cancel(self, e: MarketEvent):
        o = self.orders.get(e.payload["order_id"])
        if o and o.state in {"RESTING","PARTIALLY_FILLED"}:
            o.state = "CANCELLED"

    def _apply_book(self, e: MarketEvent):
        p=e.payload
        b=self._book(e.market_id)
        b.bid=p.get("bid",b.bid); b.bid_qty=float(p.get("bid_qty",b.bid_qty) or 0.0)
        b.ask=p.get("ask",b.ask); b.ask_qty=float(p.get("ask_qty",b.ask_qty) or 0.0)
        b.observed_at=e.ts

        # If a resting order becomes marketable, fill at displayed opposite touch.
        for o in list(self.orders.values()):
            if o.market_id != e.market_id or o.state not in {"RESTING","PARTIALLY_FILLED"}:
                continue
            if o.side=="BUY" and b.ask is not None and o.price>=b.ask:
                self._fill(o,o.remaining,b.ask,e.ts,"BOOK_CROSSED_RESTING_ORDER")
            elif o.side=="SELL" and b.bid is not None and o.price<=b.bid:
                self._fill(o,o.remaining,b.bid,e.ts,"BOOK_CROSSED_RESTING_ORDER")

    def _apply_trade(self, e: MarketEvent):
        price=float(e.payload["price"]); qty=float(e.payload["qty"])
        aggressor=str(e.payload.get("aggressor","")).upper()
        for o in list(self.orders.values()):
            if o.market_id != e.market_id or o.state not in {"RESTING","PARTIALLY_FILLED"}:
                continue
            hit = (o.side=="BUY" and aggressor=="SELL" and price<=o.price) or (o.side=="SELL" and aggressor=="BUY" and price>=o.price)
            if not hit:
                continue
            consumed=max(0.0,qty)
            if o.queue_ahead>0:
                used=min(o.queue_ahead,consumed); o.queue_ahead-=used; consumed-=used
            if consumed>0:
                fillqty=min(o.remaining,consumed) if self.config.partial_fills else o.remaining
                self._fill(o,fillqty,o.price,e.ts,"QUEUE_TRADE")

    def _apply_settlement(self,e:MarketEvent):
        payout=float(e.payload["payout"])
        pos=self.positions.get(e.market_id,0.0)
        settlement_cash=pos*payout
        self.cash += settlement_cash
        self.positions[e.market_id]=0.0
        self.receipt.settlements.append({"ts":e.ts,"market_id":e.market_id,"position":pos,"payout":payout,"cash":settlement_cash})

    def _apply_internal(self,e:MarketEvent):
        if e.kind==EventType.ORDER_ACK: self._ack_order(e)
        elif e.kind==EventType.CANCEL_ACK: self._ack_cancel(e)

    def _submit_intent(self,intent:OrderIntent,now:float):
        action=intent.action.upper()
        if action=="PLACE":
            oid=intent.order_id or f"sim-{next(self._counter)}"
            if intent.price is None or intent.qty<=0:
                return
            o=SimOrder(oid,intent.market_id,intent.side.upper(),float(intent.price),float(intent.qty),float(intent.qty),now,now+self.config.submit_latency_s)
            self.orders[oid]=o
            self.receipt.orders.append({"ts":now,"action":"PLACE","order_id":oid,"market_id":intent.market_id,"side":o.side,"price":o.price,"qty":o.qty})
            self._schedule(o.active_at,EventType.ORDER_ACK,o.market_id,{"order_id":oid})
        elif action=="CANCEL" and intent.order_id:
            self.receipt.orders.append({"ts":now,"action":"CANCEL","order_id":intent.order_id,"market_id":intent.market_id})
            self._schedule(now+self.config.cancel_latency_s,EventType.CANCEL_ACK,intent.market_id,{"order_id":intent.order_id})

    def replay(self, events: Iterable[MarketEvent], strategy: Callable[[TwinView, MarketEvent], list[OrderIntent] | None]) -> ReplayReceipt:
        external=sorted(list(events))
        i=0
        while i<len(external) or self._internal:
            next_ext=external[i] if i<len(external) else None
            next_int=self._internal[0] if self._internal else None
            if next_int is not None and (next_ext is None or next_int.ts<=next_ext.ts):
                e=heapq.heappop(self._internal)
                self._apply_internal(e)
                continue
            e=next_ext; i+=1
            if e.kind==EventType.BOOK:self._apply_book(e)
            elif e.kind==EventType.TRADE:self._apply_trade(e)
            elif e.kind==EventType.SETTLEMENT:self._apply_settlement(e)

            view=TwinView(e.ts,self.books,self.positions)
            intents=strategy(view,e) or []
            self.receipt.decisions.append({"ts":e.ts,"event_kind":e.kind.value,"market_id":e.market_id,"intent_count":len(intents)})
            for intent in intents:self._submit_intent(intent,e.ts)
        return self.receipt

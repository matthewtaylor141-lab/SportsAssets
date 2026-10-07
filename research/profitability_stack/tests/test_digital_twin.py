from bettor_profit_stack.digital_twin import (
    DigitalTwin,TwinConfig,MarketEvent,EventType,OrderIntent
)

def ev(ts,seq,kind,payload=None,market="m1"):
    return MarketEvent(ts,seq,kind,market,payload or {})

def test_no_future_book_visible_to_strategy():
    seen=[]
    events=[
      ev(1,1,EventType.BOOK,{"bid":.40,"bid_qty":10,"ask":.42,"ask_qty":10}),
      ev(2,2,EventType.BOOK,{"bid":.50,"bid_qty":10,"ask":.52,"ask_qty":10}),
    ]
    def strat(view,event):
        seen.append((event.ts,view.book("m1").bid))
        return []
    DigitalTwin().replay(events,strat)
    assert seen==[(1,.40),(2,.50)]

def test_submit_latency_prevents_instant_resting():
    twin=DigitalTwin(TwinConfig(submit_latency_s=.1))
    events=[ev(1,1,EventType.BOOK,{"bid":.40,"bid_qty":10,"ask":.45,"ask_qty":10})]
    fired=[False]
    def strat(view,event):
        if not fired[0]:
            fired[0]=True
            return [OrderIntent("PLACE","m1","BUY",.40,2,"o1")]
        return []
    twin.replay(events,strat)
    assert twin.orders["o1"].state=="RESTING"
    assert twin.orders["o1"].active_at==1.1

def test_marketable_at_ack_fills_opposite_touch():
    twin=DigitalTwin(TwinConfig(submit_latency_s=.1))
    events=[ev(1,1,EventType.BOOK,{"bid":.40,"bid_qty":10,"ask":.42,"ask_qty":10})]
    fired=[False]
    def strat(view,event):
        if not fired[0]:
            fired[0]=True
            return [OrderIntent("PLACE","m1","BUY",.45,2,"o1")]
        return []
    twin.replay(events,strat)
    assert twin.orders["o1"].state=="FILLED"
    assert twin.orders["o1"].avg_fill_price==.42
    assert twin.positions["m1"]==2

def test_queue_ahead_consumed_before_fill():
    twin=DigitalTwin(TwinConfig(submit_latency_s=0,queue_fraction_ahead=1))
    events=[
      ev(1,1,EventType.BOOK,{"bid":.40,"bid_qty":5,"ask":.45,"ask_qty":10}),
      ev(2,2,EventType.TRADE,{"price":.40,"qty":3,"aggressor":"SELL"}),
      ev(3,3,EventType.TRADE,{"price":.40,"qty":4,"aggressor":"SELL"}),
    ]
    fired=[False]
    def strat(view,event):
        if event.ts==1 and not fired[0]:
            fired[0]=True
            return [OrderIntent("PLACE","m1","BUY",.40,2,"o1")]
        return []
    twin.replay(events,strat)
    assert twin.orders["o1"].filled_qty==2

def test_cancel_latency_allows_fill_before_cancel_ack():
    twin=DigitalTwin(TwinConfig(submit_latency_s=0,cancel_latency_s=.5,queue_fraction_ahead=0))
    events=[
      ev(1,1,EventType.BOOK,{"bid":.40,"bid_qty":0,"ask":.45,"ask_qty":10}),
      ev(2,2,EventType.BOOK,{"bid":.40,"bid_qty":0,"ask":.45,"ask_qty":10}),
      ev(2.2,3,EventType.TRADE,{"price":.40,"qty":1,"aggressor":"SELL"}),
    ]
    def strat(view,event):
        if event.ts==1:return [OrderIntent("PLACE","m1","BUY",.40,1,"o1")]
        if event.ts==2:return [OrderIntent("CANCEL","m1","BUY",order_id="o1")]
        return []
    twin.replay(events,strat)
    assert twin.orders["o1"].state=="FILLED"

def test_settlement_reconciles_cash_and_position():
    twin=DigitalTwin(TwinConfig(submit_latency_s=0))
    events=[
      ev(1,1,EventType.BOOK,{"bid":.40,"bid_qty":0,"ask":.42,"ask_qty":10}),
      ev(2,2,EventType.SETTLEMENT,{"payout":1.0}),
    ]
    def strat(view,event):
        if event.ts==1:return [OrderIntent("PLACE","m1","BUY",.45,1,"o1")]
        return []
    twin.replay(events,strat)
    assert twin.positions["m1"]==0
    assert abs(twin.cash-(1-.42))<1e-12

from sportsassets import kalshi_public_rules as K

class Resp:
    def __init__(self,status_code,body): self.status_code=status_code; self._body=body
    def json(self): return self._body
class Tx:
    def __init__(self,response=None,raises=None): self.response=response; self.raises=raises; self.calls=[]
    def get(self,url,*,timeout):
        self.calls.append((url,timeout))
        if self.raises: raise self.raises
        return self.response

def test_public_get_reads_rules_only():
    tx=Tx(Resp(200,{"market":{"ticker":"KXTEST","rules_primary":"If cancelled, resolves to a fair price.","rules_secondary":""}}))
    got=K.read("KXTEST",transport=tx)
    assert got.ok and got.evidence["settlement"]["void_rule"]=="LAST_FAIR_PRICE"
    assert tx.calls[0][0].endswith("/markets/KXTEST")

def test_non_200_named(): assert K.read("KX",transport=Tx(Resp(404,{}))).error=="HTTP_404"
def test_empty_ticker_sends_nothing():
    tx=Tx(); got=K.read("",transport=tx); assert not got.ok and got.sent is False and tx.calls==[]
def test_transport_failure_named(): assert K.read("KX",transport=Tx(raises=TimeoutError())).error=="TimeoutError"
def test_reader_declares_no_mutations():
    d=K.describe(); assert d["method"]=="GET" and d["credentials"]=="NONE" and d["orders"] is False and d["mutations"] is False

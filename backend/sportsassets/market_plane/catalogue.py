"""Completeness-preserving catalogue enumeration by recursive partition."""
from __future__ import annotations

VERSION="CATALOGUE_COMPLETENESS_V1"


def split_window(start:float,end:float):
    mid=(float(start)+float(end))/2.0
    return (float(start),mid),(mid,float(end))


async def enumerate_complete(fetch_page, *, sports:list[str], start:float, end:float,
                             max_depth:int=16, min_window_s:float=900.0) -> dict:
    """fetch_page(sport,start,end) -> {items:[...], truncated:bool, cursor_complete:bool}.

    A truncated bucket is recursively divided until each bucket proves complete.
    No bucket that remains truncated is silently accepted.
    """
    rows=[]; failures=[]; buckets=[]
    async def walk(sport,a,b,depth):
        got=await fetch_page(sport,a,b)
        trunc=bool(got.get("truncated")) or got.get("cursor_complete") is False
        buckets.append({"sport":sport,"start":a,"end":b,"truncated":trunc,
                        "count":len(got.get("items") or [])})
        if not trunc:
            rows.extend(got.get("items") or []); return
        if depth>=max_depth or b-a<=min_window_s:
            failures.append({"sport":sport,"start":a,"end":b,
                             "why":"PROVIDER_BUCKET_REMAINS_TRUNCATED"}); return
        left,right=split_window(a,b)
        await walk(sport,*left,depth+1); await walk(sport,*right,depth+1)
    for sport in sports:
        await walk(sport,float(start),float(end),0)
    return {"complete":not failures,"items":rows,"failures":failures,
            "buckets":buckets,"version":VERSION}

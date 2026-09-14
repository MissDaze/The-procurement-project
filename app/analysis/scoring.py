from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo


def prospect_score(*, contract_count:int, disclosed_value:Decimal|float, agency_count:int, latest_award:date|None, live_market_count:int, opportunity_count:int, name:str="") -> tuple[int,dict]:
    value=float(disclosed_value or 0); days=(date.today()-latest_award).days if latest_award else 9999
    giant=any(x in name.lower() for x in ("deloitte","accenture","microsoft","amazon","ibm","kpmg","pwc","boeing","lockheed"))
    parts={
        "Government procurement activity":min(20,contract_count*3),
        "Government contract value":min(20, int(value/2_500_000)+5 if value else 0),
        "Number of agencies served":min(15,agency_count*3),
        "Recent government activity":15 if days<=180 else 10 if days<=365 else 5 if days<=730 else 0,
        "Active live-tender market":min(15,live_market_count*3),
        "SME/mid-market suitability":0 if giant else 10 if value<=50_000_000 else 6 if value<=150_000_000 else 2,
        "Current opportunity volume":min(5,opportunity_count),
    }
    return sum(parts.values()),parts


def opportunity_score(*, capability:int, category:int, agency:int, historical:int, location:int, closes_at:datetime|None, competitive:int) -> tuple[int,str,dict]:
    if closes_at and closes_at.tzinfo is None:
        closes_at=closes_at.replace(tzinfo=ZoneInfo("Australia/Sydney"))
    now=datetime.now(timezone.utc); days=(closes_at-now).days if closes_at else 999
    timing=10 if 7<=days<=45 else 7 if days>45 else 4 if days>=2 else 0
    parts={"Capability Match":min(30,capability),"Category Match":min(15,category),"Agency Relevance":min(15,agency),"Historical Fit":min(15,historical),"Location":min(10,location),"Timing":timing,"Competitive Context":min(5,competitive)}
    total=sum(parts.values()); classification="HIGH" if total>=80 else "GOOD" if total>=60 else "WATCH" if total>=40 else "LOW"
    return total,classification,parts


def recompete_score(*, end_date:date|None, amendment_count:int, agency_frequency:int, relationship_years:float, incumbent:bool=False, relevance:int=0) -> tuple[int,dict]:
    """Score an expiry watch without manufacturing incumbency evidence.

    `relevance` is supplied by deterministic category/capability matching. Incumbent
    points are only awarded when the expiring contract is actually attributed to
    the prospect supplier.
    """
    days=(end_date-date.today()).days if end_date else 9999
    parts={
        "Expiry proximity":35 if 0<=days<=180 else 25 if 180<days<=365 else 10 if 365<days<=540 else 0,
        "Capability/category relevance":min(20,max(0,relevance)),
        "Amendment history":min(10,max(0,amendment_count)*2),
        "Agency relationship":min(15,max(0,agency_frequency)*5),
        "Incumbent position":15 if incumbent else 0,
        "Relationship duration":min(5,max(0,int(relationship_years))),
    }
    return sum(parts.values()),parts

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agency, CollectionRun, Contract, ContractVersion, RejectedRecord, Supplier

API = "https://api.tenders.gov.au/ocds/findByDates/contractPublished/{start}/{end}"
SOURCE = "AusTender OCDS Contract Notices"


def _dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _party(release, role):
    ids = {x["id"] for x in release.get("parties", []) if role in x.get("roles", [])}
    return next((x for x in release.get("parties", []) if x.get("id") in ids), None)


def parse_release(release: dict) -> dict:
    tender = release.get("tender", {}); contracts = release.get("contracts", [])
    contract = contracts[0] if contracts else {}
    supplier = _party(release, "supplier") or {}
    buyer = _party(release, "buyer") or _party(release, "procuringEntity") or release.get("buyer", {})
    period = contract.get("period", {})
    value = contract.get("value", {}).get("amount")
    abn = next((x.get("id") for x in supplier.get("additionalIdentifiers", []) if x.get("scheme") == "AU-ABN"), None)
    return {
        "source_id": contract.get("id") or tender.get("id") or release.get("ocid"), "title": contract.get("title") or tender.get("title") or "Untitled contract",
        "description": contract.get("description") or tender.get("description"), "supplier_name": supplier.get("name"), "supplier_abn": abn,
        "agency_name": buyer.get("name"), "current_value": Decimal(str(value)) if value is not None else None,
        "publication_date": (_dt(release.get("date")) or datetime.now(timezone.utc)).date(),
        "start_date": _dt(period.get("startDate")).date() if period.get("startDate") else None,
        "end_date": _dt(period.get("endDate")).date() if period.get("endDate") else None,
        "procurement_method": tender.get("procurementMethodDetails") or tender.get("procurementMethod"),
        "category": ((contract.get("items") or [{}])[0].get("classification") or {}).get("id") or tender.get("mainProcurementCategory"), "source_url": f"https://www.tenders.gov.au/Cn/Show/{contract.get('id','')}",
    }


def collect(db: Session, day: date | None = None) -> CollectionRun:
    day = day or (date.today() - timedelta(days=1)); nxt = day + timedelta(days=1)
    run = CollectionRun(source=SOURCE, collection_type="awarded_contracts"); db.add(run); db.commit()
    try:
        url = API.format(start=f"{day.isoformat()}T00:00:00Z", end=f"{nxt.isoformat()}T00:00:00Z")
        response = httpx.get(url, headers={"Accept":"application/json","User-Agent":"NixSecProcurementIntelligence/1.0"}, timeout=60)
        response.raise_for_status(); releases = response.json().get("releases", []); run.checked = len(releases)
        for release in releases:
            try:
                row = parse_release(release)
                if not row["source_id"] or not row["supplier_name"] or not row["agency_name"]: raise ValueError("Missing contract identity")
                supplier = db.scalar(select(Supplier).where(Supplier.canonical_name == row["supplier_name"]))
                if not supplier: supplier=Supplier(canonical_name=row["supplier_name"],legal_name=row["supplier_name"],abn=row["supplier_abn"]); db.add(supplier); db.flush()
                agency = db.scalar(select(Agency).where(Agency.canonical_name == row["agency_name"]))
                if not agency: agency=Agency(canonical_name=row["agency_name"]); db.add(agency); db.flush()
                raw_hash=hashlib.sha256(json.dumps(release,sort_keys=True).encode()).hexdigest()
                item=db.scalar(select(Contract).where(Contract.source==SOURCE,Contract.source_id==row["source_id"]))
                if not item:
                    item=Contract(source=SOURCE,raw_hash=raw_hash,supplier_id=supplier.id,agency_id=agency.id,original_value=row["current_value"],**{k:v for k,v in row.items() if k in {"source_id","title","description","current_value","publication_date","start_date","end_date","category","procurement_method","source_url"}}); db.add(item); run.created+=1
                elif item.raw_hash != raw_hash:
                    db.add(ContractVersion(contract_id=item.id,raw_hash=item.raw_hash,previous_value=item.current_value,previous_end_date=item.end_date,snapshot={"value":str(item.current_value),"end_date":str(item.end_date)}))
                    for key in ("title","description","current_value","publication_date","start_date","end_date","category","procurement_method","source_url"): setattr(item,key,row[key])
                    item.raw_hash=raw_hash; run.updated+=1
                db.commit()
            except Exception as exc:
                db.rollback(); run.errors+=1; db.add(RejectedRecord(source=SOURCE,reason=str(exc),payload={"ocid":release.get("ocid")})); db.commit()
        run.checked=len(releases); run.status="healthy" if run.errors==0 else "partial"
    except Exception as exc: run.status="failed"; run.errors+=1; run.latest_error=str(exc)
    run.finished_at=datetime.now(timezone.utc); db.add(run); db.commit(); return run

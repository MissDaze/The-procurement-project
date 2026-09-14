from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urljoin

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agency, CollectionRun, Contract, ContractVersion, RejectedRecord, Supplier

API = "https://api.tenders.gov.au/ocds/findByDates/contractPublished/{start}/{end}"
SOURCE = "AusTender OCDS Contract Notices"
HEADERS = {"Accept": "application/json", "User-Agent": "NixSecProcurementIntelligence/1.0"}


def _dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _party_by_id(release: dict, party_id: str | None) -> dict | None:
    if not party_id:
        return None
    return next((p for p in release.get("parties", []) if p.get("id") == party_id), None)


def _party_with_role(release: dict, role: str) -> dict | None:
    return next((p for p in release.get("parties", []) if role in p.get("roles", [])), None)


def _supplier_for_contract(release: dict, contract: dict) -> tuple[dict | None, str | None]:
    """Resolve the supplier through contract -> award -> supplier.

    A release can contain more than one supplier party. Selecting the first party
    with the supplier role can join the wrong company name to the wrong ABN. We
    only attribute a contract to a supplier when the award linkage is unambiguous.
    """
    awards = release.get("awards", []) or []
    award_id = contract.get("awardID")
    award = next((a for a in awards if a.get("id") == award_id), None) if award_id else None
    if award is None and len(awards) == 1:
        award = awards[0]

    refs = (award or {}).get("suppliers", []) or []
    if len(refs) == 1:
        ref = refs[0]
        party = _party_by_id(release, ref.get("id")) or {}
        merged = dict(party)
        merged.update({k: v for k, v in ref.items() if v not in (None, "", [])})
        return merged, None

    if len(refs) > 1:
        return None, "Multiple suppliers are linked to the award; supplier attribution is ambiguous"

    fallback = [p for p in release.get("parties", []) if "supplier" in p.get("roles", [])]
    if len(fallback) == 1:
        return fallback[0], None
    if len(fallback) > 1:
        return None, "Multiple supplier parties exist and no unambiguous award linkage was found"
    return None, "No supplier party could be resolved for this contract"


def _valid_abn(value: str | None) -> bool:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) != 11:
        return False
    numbers = [int(x) for x in digits]
    numbers[0] -= 1
    weights = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19]
    return sum(n * w for n, w in zip(numbers, weights)) % 89 == 0


def _supplier_abn(supplier: dict | None) -> str | None:
    if not supplier:
        return None
    identifiers = supplier.get("additionalIdentifiers", []) or []
    identifier = supplier.get("identifier") or {}
    if identifier:
        identifiers = [identifier, *identifiers]
    raw = next(
        (
            item.get("id")
            for item in identifiers
            if str(item.get("scheme", "")).upper() in {"AU-ABN", "ABN"}
        ),
        None,
    )
    digits = re.sub(r"\D", "", raw or "")
    return digits if _valid_abn(digits) else None


def _normalise_name(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _resolve_supplier(db: Session, name: str | None, abn: str | None) -> tuple[Supplier | None, str | None]:
    """Resolve a supplier without silently merging conflicting identities."""
    if not name:
        return None, "Supplier name is missing"

    by_name = db.scalar(select(Supplier).where(Supplier.canonical_name == name))
    by_abn = db.scalar(select(Supplier).where(Supplier.abn == abn)) if abn else None

    if by_name and by_abn and by_name.id != by_abn.id:
        return None, f"Supplier identity conflict: name and ABN resolve to different stored suppliers ({name} / {abn})"

    if by_name:
        if abn and by_name.abn and by_name.abn != abn:
            return None, f"Supplier identity conflict: {name} already has ABN {by_name.abn}, source supplied {abn}"
        if abn and not by_name.abn:
            by_name.abn = abn
        return by_name, None

    if by_abn:
        if _normalise_name(by_abn.canonical_name) != _normalise_name(name):
            return None, f"Supplier identity conflict: ABN {abn} is already attached to {by_abn.canonical_name}, source supplied {name}"
        return by_abn, None

    supplier = Supplier(canonical_name=name, legal_name=name, abn=abn)
    db.add(supplier)
    db.flush()
    return supplier, None


def parse_release(release: dict) -> dict:
    tender = release.get("tender", {}) or {}
    contracts = release.get("contracts", []) or []
    contract = contracts[0] if contracts else {}
    supplier, supplier_issue = _supplier_for_contract(release, contract)

    procuring = tender.get("procuringEntity") or {}
    buyer = _party_by_id(release, procuring.get("id")) or _party_with_role(release, "buyer") or _party_with_role(release, "procuringEntity") or release.get("buyer", {}) or {}

    period = contract.get("period", {}) or {}
    value = (contract.get("value") or {}).get("amount")
    classification = (((contract.get("items") or [{}])[0].get("classification")) or {})
    classification_id = str(classification.get("id") or "").strip() or None
    classification_description = str(classification.get("description") or "").strip() or None
    unspsc = classification_id if classification_id and re.fullmatch(r"\d{8}", classification_id) else None

    return {
        "source_id": contract.get("id") or tender.get("id") or release.get("ocid"),
        "title": contract.get("title") or tender.get("title") or "Untitled contract",
        "description": contract.get("description") or tender.get("description"),
        "supplier_name": (supplier or {}).get("name"),
        "supplier_abn": _supplier_abn(supplier),
        "supplier_issue": supplier_issue,
        "agency_name": buyer.get("name"),
        "current_value": Decimal(str(value)) if value is not None else None,
        "publication_date": (_dt(release.get("date")) or datetime.now(timezone.utc)).date(),
        "start_date": _dt(period.get("startDate")).date() if period.get("startDate") else None,
        "end_date": _dt(period.get("endDate")).date() if period.get("endDate") else None,
        "procurement_method": tender.get("procurementMethodDetails") or tender.get("procurementMethod"),
        "category": classification_description or classification_id or tender.get("mainProcurementCategory"),
        "unspsc": unspsc,
        "source_url": f"https://www.tenders.gov.au/Cn/Show/{contract.get('id', '')}",
    }


def _fetch_releases(client: httpx.Client, start_day: date, end_day: date) -> list[dict]:
    """Fetch every page for a published-date window.

    AusTender OCDS responses can be paginated. Ignoring `links.next` silently
    undercounts contract notices, which then corrupts supplier totals and prospect
    discovery. The official ecosystem commonly collects this API in short date
    windows; NixSec uses seven-day windows for historical backfill.
    """
    url = API.format(start=f"{start_day.isoformat()}T00:00:00Z", end=f"{end_day.isoformat()}T00:00:00Z")
    releases: list[dict] = []
    seen_urls: set[str] = set()

    while url:
        if url in seen_urls:
            raise RuntimeError("AusTender OCDS pagination loop detected")
        if len(seen_urls) >= 1000:
            raise RuntimeError("AusTender OCDS pagination exceeded safety limit")
        seen_urls.add(url)

        response = client.get(url)
        response.raise_for_status()
        payload = response.json()
        releases.extend(payload.get("releases", []) or [])
        next_url = (payload.get("links") or {}).get("next")
        url = urljoin(url, next_url) if next_url else ""

    return releases


def _store_release(db: Session, release: dict, run: CollectionRun) -> None:
    row = parse_release(release)
    if not row["source_id"] or not row["agency_name"]:
        raise ValueError("Missing contract identity")

    agency = db.scalar(select(Agency).where(Agency.canonical_name == row["agency_name"]))
    if not agency:
        agency = Agency(canonical_name=row["agency_name"])
        db.add(agency)
        db.flush()

    supplier = None
    identity_issue = row.get("supplier_issue")
    if not identity_issue:
        supplier, identity_issue = _resolve_supplier(db, row.get("supplier_name"), row.get("supplier_abn"))

    raw_hash = hashlib.sha256(json.dumps(release, sort_keys=True).encode()).hexdigest()
    item = db.scalar(select(Contract).where(Contract.source == SOURCE, Contract.source_id == row["source_id"]))
    fields = {
        k: row[k]
        for k in (
            "source_id", "title", "description", "current_value", "publication_date",
            "start_date", "end_date", "category", "unspsc", "procurement_method", "source_url"
        )
    }

    if not item:
        item = Contract(
            source=SOURCE,
            raw_hash=raw_hash,
            supplier_id=supplier.id if supplier else None,
            agency_id=agency.id,
            original_value=row["current_value"],
            **fields,
        )
        db.add(item)
        run.created += 1
    elif item.raw_hash != raw_hash or item.supplier_id != (supplier.id if supplier else None):
        db.add(
            ContractVersion(
                contract_id=item.id,
                raw_hash=item.raw_hash,
                previous_value=item.current_value,
                previous_end_date=item.end_date,
                snapshot={
                    "value": str(item.current_value),
                    "end_date": str(item.end_date),
                    "supplier_id": item.supplier_id,
                },
            )
        )
        for key, value in fields.items():
            setattr(item, key, value)
        item.supplier_id = supplier.id if supplier else None
        item.agency_id = agency.id
        item.raw_hash = raw_hash
        run.updated += 1

    if identity_issue:
        db.add(
            RejectedRecord(
                source=SOURCE,
                reason=f"Supplier attribution withheld: {identity_issue}",
                payload={
                    "ocid": release.get("ocid"),
                    "contract_id": row["source_id"],
                    "supplier_name": row.get("supplier_name"),
                    "supplier_abn": row.get("supplier_abn"),
                },
            )
        )


def collect(
    db: Session,
    day: date | None = None,
    end_day: date | None = None,
    collection_type: str = "awarded_contracts",
) -> CollectionRun:
    start_day = day or (date.today() - timedelta(days=1))
    end_day = end_day or (start_day + timedelta(days=1))
    if end_day <= start_day:
        raise ValueError("end_day must be after day")

    run = CollectionRun(source=SOURCE, collection_type=collection_type)
    db.add(run)
    db.commit()

    try:
        with httpx.Client(headers=HEADERS, timeout=60, follow_redirects=True) as client:
            releases = _fetch_releases(client, start_day, end_day)
        run.checked = len(releases)

        for release in releases:
            try:
                _store_release(db, release, run)
                db.commit()
            except Exception as exc:
                db.rollback()
                run.errors += 1
                db.add(RejectedRecord(source=SOURCE, reason=str(exc), payload={"ocid": release.get("ocid")}))
                db.commit()

        run.status = "healthy" if run.errors == 0 else "partial"
    except Exception as exc:
        db.rollback()
        run.status = "failed"
        run.errors += 1
        run.latest_error = str(exc)

    run.finished_at = datetime.now(timezone.utc)
    db.add(run)
    db.commit()
    return run


def backfill(db: Session, days: int = 365, chunk_days: int = 7) -> list[CollectionRun]:
    """Backfill historical published Contract Notices in bounded weekly windows."""
    days = max(1, min(int(days), 3650))
    chunk_days = max(1, min(int(chunk_days), 31))
    end = date.today()
    cursor = end - timedelta(days=days)
    runs: list[CollectionRun] = []

    while cursor < end:
        nxt = min(cursor + timedelta(days=chunk_days), end)
        run = collect(db, day=cursor, end_day=nxt, collection_type="awarded_contracts_backfill")
        runs.append(run)
        if run.status == "failed":
            break
        cursor = nxt

    return runs

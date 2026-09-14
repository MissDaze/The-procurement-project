from __future__ import annotations

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agency, CollectionRun, RejectedRecord, Tender, TenderVersion

RSS_URL = "https://www.tenders.gov.au/public_data/rss/rss.xml"
SOURCE = "AusTender Current ATM"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NixSecProcurementIntelligence/1.0; +https://nixsec.com.au)",
    "Accept": "application/rss+xml,application/xml,text/xml,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
    "Cache-Control": "no-cache",
}

NON_BID_TYPE_TERMS = (
    "notice",
    "industry briefing",
    "industry update",
    "request for information",
    "request for comment",
    "market sounding",
    "forecast",
)

NON_BID_TITLE_PATTERNS = (
    r"^\s*notice\b",
    r"\bindustry\s+update\b",
    r"\bindustry\s+briefing\b",
    r"\btender\s+forecast\b",
    r"\bprocurement\s+forecast\b",
    r"\brequest\s+for\s+information\b",
    r"\bmarket\s+sounding\b",
)


def _field(soup: BeautifulSoup, name: str) -> str | None:
    label = soup.find("label", attrs={"for": name})
    if not label:
        return None
    box = label.find_parent(class_="list-desc")
    value = box.select_one(".list-desc-inner") if box else None
    return " ".join(value.get_text(" ", strip=True).split()) if value else None


def _close_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    match = re.match(r"(\d{1,2}-[A-Za-z]{3}-\d{4} \d{1,2}:\d{2} [ap]m)", value)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%d-%b-%Y %I:%M %p").replace(tzinfo=ZoneInfo("Australia/Sydney"))


def _is_non_bid_notice(title: str | None, tender_type: str | None) -> bool:
    type_text = (tender_type or "").lower()
    if any(term in type_text for term in NON_BID_TYPE_TERMS):
        return True
    title_text = title or ""
    return any(re.search(pattern, title_text, flags=re.IGNORECASE) for pattern in NON_BID_TITLE_PATTERNS)


def _status(title: str | None, tender_type: str | None, close: datetime | None) -> str:
    now_local = datetime.now(ZoneInfo("Australia/Sydney"))
    if _is_non_bid_notice(title, tender_type):
        return "NOTICE" if not close or close > now_local else "CLOSED"
    if not close:
        return "UNVERIFIED"
    if close <= now_local:
        return "CLOSED"
    if close - now_local < __import__("datetime").timedelta(days=7):
        return "CLOSING SOON"
    return "LIVE"


def parse_rss(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    rows = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link.startswith("https://www.tenders.gov.au/Atm/Show/"):
            continue
        atm_hint, _, name = title.partition(":")
        pub = item.findtext("pubDate")
        rows.append(
            {
                "atm_id_hint": atm_hint.strip() if name else None,
                "title": name.strip() if name else title,
                "description": BeautifulSoup(item.findtext("description") or "", "html.parser").get_text(" ", strip=True),
                "published_at": parsedate_to_datetime(pub) if pub else None,
                "source_url": link,
            }
        )
    return rows


def parse_detail(html: str, base: dict) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    lead = soup.select_one("p.lead")
    close = _close_datetime(_field(soup, "CloseDate"))
    category = _field(soup, "Category")
    unspsc = category.split(" - ", 1)[0] if category and re.match(r"^\d{8}\b", category) else None
    tender_type = _field(soup, "Type")
    title = lead.get_text(" ", strip=True) if lead else base["title"]
    return {
        **base,
        "source_id": _field(soup, "AtmId") or base.get("atm_id_hint"),
        "title": title,
        "agency_name": _field(soup, "Agency"),
        "category": category,
        "unspsc": unspsc,
        "closes_at": close,
        "tender_type": tender_type,
        "status": _status(title, tender_type, close),
    }


def _mark_unverified(tender: Tender, db: Session, reason: str) -> None:
    if tender.status == "UNVERIFIED":
        return
    db.add(
        TenderVersion(
            tender_id=tender.id,
            raw_hash=tender.raw_hash,
            snapshot={
                "status": tender.status,
                "closes_at": tender.closes_at.isoformat() if tender.closes_at else None,
                "reason": reason,
            },
        )
    )
    tender.status = "UNVERIFIED"


def collect(db: Session, max_details: int | None = None, delay_seconds: float = 0.15) -> CollectionRun:
    run = CollectionRun(source=SOURCE, collection_type="live_tenders")
    db.add(run)
    db.commit()
    seen_ids: set[str] = set()

    try:
        with httpx.Client(headers=HEADERS, timeout=45, follow_redirects=True) as client:
            response = client.get(RSS_URL)
            response.raise_for_status()
            feed = parse_rss(response.content)
            run.checked = len(feed)

            for base in feed[:max_details]:
                try:
                    detail_response = client.get(base["source_url"])
                    detail_response.raise_for_status()
                    row = parse_detail(detail_response.text, base)
                    if not row.get("source_id") or not row.get("agency_name"):
                        raise ValueError("Required ATM detail field missing")
                    if row["status"] in {"LIVE", "CLOSING SOON"} and not row.get("closes_at"):
                        raise ValueError("Bid opportunity has no verifiable closing date")

                    seen_ids.add(row["source_id"])
                    agency = db.scalar(select(Agency).where(Agency.canonical_name == row["agency_name"]))
                    if not agency:
                        agency = Agency(canonical_name=row["agency_name"])
                        db.add(agency)
                        db.flush()

                    serial = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items() if k != "atm_id_hint"}
                    raw_hash = hashlib.sha256(json.dumps(serial, sort_keys=True).encode()).hexdigest()
                    tender = db.scalar(select(Tender).where(Tender.source == SOURCE, Tender.source_id == row["source_id"]))

                    fields = {
                        k: v
                        for k, v in row.items()
                        if k in {
                            "source_id", "title", "description", "agency_name", "published_at",
                            "closes_at", "category", "unspsc", "tender_type", "source_url", "status"
                        }
                    }
                    if not tender:
                        tender = Tender(source=SOURCE, raw_hash=raw_hash, agency_id=agency.id, **fields)
                        db.add(tender)
                        db.flush()
                        run.created += 1
                    elif tender.raw_hash != raw_hash:
                        db.add(
                            TenderVersion(
                                tender_id=tender.id,
                                raw_hash=tender.raw_hash,
                                snapshot={
                                    "status": tender.status,
                                    "closes_at": tender.closes_at.isoformat() if tender.closes_at else None,
                                },
                            )
                        )
                        for key, value in fields.items():
                            setattr(tender, key, value)
                        tender.agency_id = agency.id
                        tender.raw_hash = raw_hash
                        run.updated += 1

                    tender.last_seen_at = datetime.now(timezone.utc)
                    db.commit()
                    time.sleep(delay_seconds)

                except Exception as exc:
                    db.rollback()
                    run.errors += 1
                    hint = base.get("atm_id_hint")
                    if hint:
                        existing = db.scalar(select(Tender).where(Tender.source == SOURCE, Tender.source_id == hint))
                        if existing and existing.status in {"LIVE", "CLOSING SOON"}:
                            _mark_unverified(existing, db, "Official detail page could not be refreshed")
                    db.add(RejectedRecord(source=SOURCE, reason=str(exc), payload={"source_url": base.get("source_url")}))
                    db.commit()

            # Only a complete successful feed traversal can be used to invalidate
            # records that disappeared from the current ATM feed. Never do this for
            # deliberately limited test/preview collections.
            if max_details is None:
                current = db.scalars(
                    select(Tender).where(Tender.source == SOURCE, Tender.status.in_(["LIVE", "CLOSING SOON"]))
                ).all()
                for tender in current:
                    if tender.source_id not in seen_ids:
                        _mark_unverified(tender, db, "No longer present in the latest complete current-ATM feed")
                        run.updated += 1
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

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from weasyprint import HTML

from app.ai.tender_recommendation import analyse_tenders_for_company, build_company_profile
from app.config import ROOT, settings
from app.models import Contract, OpportunityScore, Prospect, ReportRun, RecompeteScore, Tender

REPORT_TITLES = {
    "Two-Page Prospect Preview": ("GOVERNMENT GROWTH", "Procurement Opportunity Brief"),
    "Full Procurement Intelligence Report": ("STRATEGIC INTELLIGENCE", "Procurement Intelligence Report"),
    "Live Tender Report": ("LIVE OPPORTUNITIES", "Tender Intelligence Report"),
    "Contract Expiry Report": ("FORWARD PIPELINE", "Contract Expiry Intelligence"),
    "Competitor Intelligence Report": ("MARKET POSITION", "Procurement Position Brief"),
    "Agency Intelligence Report": ("BUYER INSIGHT", "Agency Opportunity Brief"),
    "Weekly Opportunity Brief": ("THIS WEEK", "Weekly Opportunity Brief"),
}

LIVE_STATUSES = ("LIVE", "CLOSING SOON")
LIVE_FRESHNESS_HOURS = 30


def _verified(value):
    return value if value not in (None, "", []) else "Not Available"


def _iso(value):
    return value.isoformat() if value else None


def _tender_fact(tender: Tender, score: OpportunityScore | None = None) -> dict:
    return {
        "source_id": tender.source_id,
        "title": tender.title,
        "agency": tender.agency_name,
        "published_at": _iso(tender.published_at),
        "closes_at": _iso(tender.closes_at),
        "tender_type": tender.tender_type,
        "category": tender.category,
        "unspsc": tender.unspsc,
        "description": (tender.description or "")[:1800],
        "location": tender.location,
        "procurement_method": tender.procurement_method,
        "contact_details": tender.contact_details or {},
        "document_links": tender.document_links or [],
        "official_source": tender.source_url,
        "internal_deterministic_score": score.score if score else None,
        "internal_score_breakdown": score.breakdown if score else None,
    }


def _expiry_fact(score: RecompeteScore, contract: Contract, supplier_id: int) -> dict:
    return {
        "source_id": contract.source_id,
        "title": contract.title,
        "end_date": _iso(contract.end_date),
        "category": contract.category,
        "unspsc": contract.unspsc,
        "confidence": score.confidence,
        "classification": score.classification,
        "evidence": score.evidence or {},
        "recipient_is_incumbent": contract.supplier_id == supplier_id,
        "official_source": contract.source_url,
    }


def _live_tenders(db: Session, limit: int = 60) -> list[Tender]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LIVE_FRESHNESS_HOURS)
    return db.scalars(
        select(Tender)
        .where(
            Tender.status.in_(LIVE_STATUSES),
            Tender.last_seen_at >= cutoff,
            Tender.closes_at > datetime.now(timezone.utc),
        )
        .order_by(Tender.closes_at.asc())
        .limit(limit)
    ).all()


def _company_contract_facts(db: Session, supplier_id: int) -> list[dict]:
    rows = db.scalars(
        select(Contract)
        .where(Contract.supplier_id == supplier_id)
        .order_by(Contract.publication_date.desc())
        .limit(30)
    ).all()
    return [
        {
            "source_id": c.source_id,
            "title": c.title,
            "description": (c.description or "")[:1200],
            "category": c.category,
            "unspsc": c.unspsc,
            "value": float(c.current_value or 0),
            "publication_date": _iso(c.publication_date),
            "start_date": _iso(c.start_date),
            "end_date": _iso(c.end_date),
            "agency": c.agency.canonical_name if c.agency else None,
            "official_source": c.source_url,
        }
        for c in rows
    ]


def generate(db: Session, prospect_id: int, report_type: str = "Two-Page Prospect Preview") -> ReportRun:
    prospect = db.get(Prospect, prospect_id)
    if not prospect:
        raise ValueError("Prospect not found")

    supplier = prospect.supplier
    report_id = f"NS-{date.today():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"
    kicker, title = REPORT_TITLES.get(report_type, ("PROCUREMENT INTELLIGENCE", report_type))

    live_tenders = _live_tenders(db)
    score_rows = db.scalars(select(OpportunityScore).where(OpportunityScore.prospect_id == prospect.id)).all()
    score_by_tender = {row.tender_id: row for row in score_rows}

    company_contracts = _company_contract_facts(db, supplier.id)
    company_profile = build_company_profile(
        supplier.canonical_name,
        supplier.abn,
        prospect.inferred_capabilities or [],
        prospect.main_categories or [],
        company_contracts,
    )

    tender_facts = [_tender_fact(t, score_by_tender.get(t.id)) for t in live_tenders]
    recommendations = analyse_tenders_for_company(company_profile, tender_facts)
    tender_by_source = {t.source_id: t for t in live_tenders}

    rows = []
    for rec in recommendations:
        tender = tender_by_source.get(rec.tender_source_id)
        if tender:
            rows.append({"tender": tender, "recommendation": rec})

    apply_rows = sorted([r for r in rows if r["recommendation"].recommendation == "APPLY"], key=lambda x: x["recommendation"].confidence, reverse=True)
    review_rows = sorted([r for r in rows if r["recommendation"].recommendation == "REVIEW"], key=lambda x: x["recommendation"].confidence, reverse=True)

    recompetes = db.execute(
        select(RecompeteScore, Contract)
        .join(Contract, Contract.id == RecompeteScore.contract_id)
        .where(RecompeteScore.prospect_id == prospect.id)
        .order_by(RecompeteScore.confidence.desc())
        .limit(8)
    ).all()
    expiry_rows = [{"score": score, "contract": contract} for score, contract in recompetes]

    coverage_from, coverage_to = db.execute(select(func.min(Contract.publication_date), func.max(Contract.publication_date))).one()

    if apply_rows:
        decision_signal = "APPLY"
        immediate_action = f"Review the official ATM documents for {apply_rows[0]['tender'].title} first and confirm every mandatory requirement before committing bid resources."
    elif review_rows:
        decision_signal = "REVIEW"
        immediate_action = "Review the shortlisted tenders against the official ATM documents before making a bid/no-bid decision."
    else:
        decision_signal = "MONITOR"
        immediate_action = "No live tender has enough evidence for an APPLY recommendation at report time; continue monitoring."

    executive_summary = (
        f"NixSec analysed {len(live_tenders)} current source-verified live tenders against the recorded procurement history of {supplier.canonical_name}. "
        f"The AI recommendation layer identified {len(apply_rows)} tender{'s' if len(apply_rows) != 1 else ''} to seriously consider applying for and {len(review_rows)} additional review candidate{'s' if len(review_rows) != 1 else ''}. "
        f"{immediate_action}"
    )

    env = Environment(loader=FileSystemLoader(str(ROOT / "app" / "reports" / "templates")), undefined=StrictUndefined, autoescape=True)
    html = env.get_template("report.html").render(
        report_kicker=kicker,
        report_title=title,
        requested_report_type=report_type,
        company_name=_verified(supplier.canonical_name),
        company_abn=_verified(supplier.abn),
        company_category=_verified((prospect.main_categories or [None])[0]),
        identity_note="Company name and ABN are shown as recorded in collected procurement data. Independent ABR identity verification has not been completed unless explicitly stated.",
        client_logo_url=None,
        report_date=date.today().strftime("%d %B %Y"),
        report_type=report_type,
        report_id=report_id,
        confidentiality_text="CONFIDENTIAL — PREPARED EXCLUSIVELY FOR THE NAMED RECIPIENT",
        nixsec_website=settings.nixsec_website,
        prospect=prospect,
        supplier=supplier,
        company_profile=company_profile,
        analysed_tender_count=len(live_tenders),
        apply_rows=apply_rows,
        review_rows=review_rows,
        expiry_rows=expiry_rows,
        decision_signal=decision_signal,
        executive_summary=executive_summary,
        immediate_action=immediate_action,
        coverage_from=coverage_from,
        coverage_to=coverage_to,
    )

    settings.report_dir.mkdir(parents=True, exist_ok=True)
    path = settings.report_dir / f"{report_id}.pdf"
    HTML(string=html, base_url=str(ROOT)).write_pdf(path)
    run = ReportRun(
        report_id=report_id,
        prospect_id=prospect.id,
        report_type=report_type,
        file_path=str(path),
        parameters={
            "cover_first": True,
            "identity_fields_independently_verified": False,
            "contract_coverage_from": _iso(coverage_from),
            "contract_coverage_to": _iso(coverage_to),
            "external_report_hides_internal_prospect_score": True,
            "ai_decision_support": True,
            "ai_is_final_recommendation_layer": True,
            "live_tenders_analysed": len(live_tenders),
            "apply_recommendations": len(apply_rows),
            "review_recommendations": len(review_rows),
        },
    )
    db.add(run)
    db.commit()
    return run


def cover_is_page_one(path: Path) -> bool:
    data = path.read_bytes()
    return data.startswith(b"%PDF") and len(re.findall(rb"/Type\s*/Page\b", data)) >= 2

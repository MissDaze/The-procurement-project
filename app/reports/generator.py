from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from weasyprint import HTML

from app.ai.openrouter import grounded_report_analysis
from app.config import ROOT, settings
from app.models import Contract, OpportunityScore, Prospect, ReportRun, RecompeteScore, Tender

REPORT_TITLES = {
    "Two-Page Prospect Preview": ("GOVERNMENT GROWTH", "Procurement Opportunity Brief"),
    "Full Procurement Intelligence Report": ("STRATEGIC INTELLIGENCE", "Procurement Intelligence Report"),
    "Live Tender Report": ("LIVE OPPORTUNITIES", "Tender Intelligence Report"),
    "Contract Expiry Report": ("FORWARD PIPELINE", "Contract Expiry Intelligence"),
    # Competitor benchmarking is not yet implemented. Keep the requested report
    # type for workflow compatibility but do not falsely label the output as a
    # competitor report.
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


def _opportunity_fact(score: OpportunityScore, tender: Tender) -> dict:
    return {
        "source_id": tender.source_id,
        "title": tender.title,
        "agency": tender.agency_name,
        "published_at": _iso(tender.published_at),
        "closes_at": _iso(tender.closes_at),
        "tender_type": tender.tender_type,
        "category": tender.category,
        "unspsc": tender.unspsc,
        "score": score.score,
        "classification": score.classification,
        "score_evidence": score.breakdown or {},
        "official_source": tender.source_url,
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


def generate(db: Session, prospect_id: int, report_type: str = "Two-Page Prospect Preview") -> ReportRun:
    prospect = db.get(Prospect, prospect_id)
    if not prospect:
        raise ValueError("Prospect not found")

    supplier = prospect.supplier
    report_id = f"NS-{date.today():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"
    kicker, title = REPORT_TITLES.get(report_type, ("PROCUREMENT INTELLIGENCE", report_type))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LIVE_FRESHNESS_HOURS)

    matches = db.execute(
        select(OpportunityScore, Tender)
        .join(Tender, Tender.id == OpportunityScore.tender_id)
        .where(
            OpportunityScore.prospect_id == prospect.id,
            Tender.status.in_(LIVE_STATUSES),
            Tender.last_seen_at >= cutoff,
            Tender.closes_at > datetime.now(timezone.utc),
        )
        .order_by(OpportunityScore.score.desc())
        .limit(10)
    ).all()

    recompetes = db.execute(
        select(RecompeteScore, Contract)
        .join(Contract, Contract.id == RecompeteScore.contract_id)
        .where(RecompeteScore.prospect_id == prospect.id)
        .order_by(RecompeteScore.confidence.desc())
        .limit(10)
    ).all()

    coverage_from, coverage_to = db.execute(
        select(func.min(Contract.publication_date), func.max(Contract.publication_date))
    ).one()

    opportunity_facts = [_opportunity_fact(score, tender) for score, tender in matches]
    expiry_facts = [_expiry_fact(score, contract, supplier.id) for score, contract in recompetes]
    facts = {
        "company_name_as_recorded": supplier.canonical_name,
        "abn_as_recorded": supplier.abn,
        "identity_independently_verified": False,
        "procurement_categories": prospect.main_categories or [],
        "inferred_capabilities": prospect.inferred_capabilities or [],
        "recorded_contracts": supplier.contract_count,
        "recorded_disclosed_value": str(supplier.disclosed_value),
        "recorded_agencies": supplier.agency_count,
        "historical_coverage": {
            "from": _iso(coverage_from),
            "to": _iso(coverage_to),
            "meaning": "Date range of contract notices currently stored by NixSec; not represented as lifetime procurement history.",
        },
        "opportunities": opportunity_facts,
        "expiry_watches": expiry_facts,
        "score_note": "NixSec scores are decision-support rankings, not government evaluation scores or predictions of award.",
    }
    analysis = grounded_report_analysis(facts)
    opportunity_insights = analysis.get("opportunity_insights") or {}

    match_rows = []
    for score, tender in matches:
        insight = opportunity_insights.get(tender.source_id) or opportunity_insights.get(tender.title) or {}
        match_rows.append(
            {
                "score": score,
                "tender": tender,
                "insight": {
                    "why_fit": insight.get("why_fit") or "The opportunity passed NixSec's deterministic relevance threshold; review the official tender documents before treating it as a bid target.",
                    "action": insight.get("action") or "Open the official source and make a bid/no-bid decision against the mandatory requirements.",
                    "risk": insight.get("risk") or "Eligibility and delivery capability have not been independently confirmed by NixSec.",
                },
            }
        )

    expiry_rows = [
        {"score": score, "contract": contract}
        for score, contract in recompetes
    ]

    env = Environment(
        loader=FileSystemLoader(str(ROOT / "app" / "reports" / "templates")),
        undefined=StrictUndefined,
        autoescape=True,
    )
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
        matches=matches,
        match_rows=match_rows,
        recompetes=recompetes,
        expiry_rows=expiry_rows,
        analysis=analysis,
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
        },
    )
    db.add(run)
    db.commit()
    return run


def cover_is_page_one(path: Path) -> bool:
    data = path.read_bytes()
    return data.startswith(b"%PDF") and len(re.findall(rb"/Type\s*/Page\b", data)) >= 2

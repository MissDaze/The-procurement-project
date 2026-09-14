from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import select
from sqlalchemy.orm import Session
from weasyprint import HTML

from app.ai.openrouter import grounded_text
from app.config import ROOT, settings
from app.models import Contract, OpportunityScore, Prospect, ReportRun, RecompeteScore, Tender

REPORT_TITLES = {
    "Two-Page Prospect Preview": ("GOVERNMENT GROWTH", "Opportunity Preview"),
    "Full Procurement Intelligence Report": ("STRATEGIC INTELLIGENCE", "Procurement Intelligence Report"),
    "Live Tender Report": ("LIVE OPPORTUNITIES", "Tender Intelligence Report"),
    "Contract Expiry Report": ("FORWARD PIPELINE", "Contract Expiry Report"),
    "Competitor Intelligence Report": ("MARKET POSITION", "Competitor Intelligence Report"),
    "Agency Intelligence Report": ("BUYER INSIGHT", "Agency Intelligence Report"),
    "Weekly Opportunity Brief": ("THIS WEEK", "Weekly Opportunity Brief"),
}

LIVE_STATUSES = ("LIVE", "CLOSING SOON")
LIVE_FRESHNESS_HOURS = 30


def _verified(value):
    return value if value not in (None, "", []) else "Not Available"


def generate(db: Session, prospect_id: int, report_type: str = "Two-Page Prospect Preview") -> ReportRun:
    prospect = db.get(Prospect, prospect_id)
    if not prospect:
        raise ValueError("Prospect not found")

    supplier = prospect.supplier
    report_id = f"NS-{date.today():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"
    kicker, title = REPORT_TITLES.get(report_type, ("PROCUREMENT INTELLIGENCE", report_type))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LIVE_FRESHNESS_HOURS)

    # Reports must never rely on an old OpportunityScore alone. The underlying
    # tender must still be a current, recently observed bid opportunity.
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

    facts = {
        "company_name_as_recorded": supplier.canonical_name,
        "abn_as_recorded": supplier.abn,
        "recorded_contracts": supplier.contract_count,
        "recorded_disclosed_value": str(supplier.disclosed_value),
        "recorded_agencies": supplier.agency_count,
        "current_live_matches": len(matches),
        "prospect_score": prospect.score,
    }
    narrative = grounded_text(
        "Using only these recorded procurement facts, explain in two sentences why monitoring may be useful. Do not describe the entity identity as independently verified.",
        facts,
        "The recorded procurement footprint may benefit from systematic tender, expiry and agency monitoring. Entity identity fields are shown as recorded in source procurement data and should be independently verified before external use.",
    )

    env = Environment(
        loader=FileSystemLoader(str(ROOT / "app" / "reports" / "templates")),
        undefined=StrictUndefined,
        autoescape=True,
    )
    html = env.get_template("report.html").render(
        report_kicker=kicker,
        report_title=title,
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
        recompetes=recompetes,
        narrative=narrative,
    )

    settings.report_dir.mkdir(parents=True, exist_ok=True)
    path = settings.report_dir / f"{report_id}.pdf"
    HTML(string=html, base_url=str(ROOT)).write_pdf(path)
    run = ReportRun(
        report_id=report_id,
        prospect_id=prospect.id,
        report_type=report_type,
        file_path=str(path),
        parameters={"cover_first": True, "identity_fields_independently_verified": False},
    )
    db.add(run)
    db.commit()
    return run


def cover_is_page_one(path: Path) -> bool:
    data = path.read_bytes()
    return data.startswith(b"%PDF") and len(re.findall(rb"/Type\s*/Page\b", data)) >= 2

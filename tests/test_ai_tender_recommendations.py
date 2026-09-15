from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.ai.tender_recommendation import CompanyProfile, TenderRecommendation, analyse_tenders_for_company
from app.models import Contract, OpportunityScore, Prospect, Supplier, Tender


def _profile():
    return CompanyProfile(
        company_name="Example Pty Ltd",
        abn="51824753556",
        what_company_demonstrably_does="Recorded secretariat and meeting administration work.",
        key_capability_themes=["Secretariat services"],
        procurement_categories=["Business administration services"],
        historical_evidence={"contract_count": 1, "contracts": []},
        inference_notes="Capabilities inferred from procurement history.",
    )


def test_ai_disabled_fallback_never_claims_apply(monkeypatch):
    import app.ai.tender_recommendation as recmod
    monkeypatch.setattr(recmod, "settings", replace(recmod.settings, ai_enabled=False, openrouter_api_key=""))
    results = analyse_tenders_for_company(_profile(), [{"source_id":"ATM-1","title":"Secretariat Services"}])
    assert results[0].recommendation == "REVIEW"
    assert "AI recommendation analysis is unavailable" in results[0].suitability_rationale
    assert "Not established from currently collected source material" in results[0].application_steps
    assert "Not established from currently collected source material" in results[0].required_documentation


def test_report_can_recommend_tender_without_opportunity_score(db, tmp_path, monkeypatch):
    import app.reports.generator as generator
    monkeypatch.setattr(generator, "settings", replace(generator.settings, report_dir=tmp_path))

    supplier = Supplier(canonical_name="Example Pty Ltd", abn="51824753556", contract_count=1, agency_count=1, disclosed_value=Decimal("100000"))
    db.add(supplier); db.flush()
    db.add(Contract(source="test", source_id="CN-1", title="Secretariat services", description="Meeting administration and executive secretariat", supplier_id=supplier.id, current_value=Decimal("100000"), publication_date=date.today(), start_date=date.today(), end_date=date.today()+timedelta(days=200), category="Business administration services", unspsc="80160000", source_url="https://example.test/cn", raw_hash="c"*64))
    prospect = Prospect(supplier_id=supplier.id, score=10, score_breakdown={}, main_categories=["Business administration services"], inferred_capabilities=["Secretariat services"])
    db.add(prospect); db.flush()
    tender = Tender(source="test", source_id="ATM-AI", title="Forum Secretariat Services", description="Provide executive secretariat and meeting administration services", agency_name="Example Agency", category="Business administration services", unspsc="80160000", tender_type="Request for Tender", source_url="https://example.test/atm", status="LIVE", raw_hash="t"*64, published_at=datetime.now(timezone.utc), closes_at=datetime.now(timezone.utc)+timedelta(days=20), last_seen_at=datetime.now(timezone.utc))
    db.add(tender); db.commit()

    assert db.scalar(select(OpportunityScore).where(OpportunityScore.prospect_id == prospect.id)) is None

    def fake_ai(profile, tenders):
        assert any(t["source_id"] == "ATM-AI" for t in tenders)
        return [TenderRecommendation("ATM-AI", "Forum Secretariat Services", "APPLY", "Direct fit with demonstrated secretariat delivery.", "Recorded Secretariat services contract.", "Strong alignment.", "Confirm mandatory eligibility.", "The scope aligns with recorded delivery experience.", "Review ATM documents and submit through the stated channel.", "Review the official ATM schedules and provide all documents they specify.", 88)]

    monkeypatch.setattr(generator, "analyse_tenders_for_company", fake_ai)
    run = generator.generate(db, prospect.id)
    assert run.parameters["ai_is_final_recommendation_layer"] is True
    assert run.parameters["apply_recommendations"] == 1
    assert Path(run.file_path).read_bytes().startswith(b"%PDF")


def test_deterministic_score_cannot_force_apply(monkeypatch):
    import app.ai.tender_recommendation as recmod
    monkeypatch.setattr(recmod, "settings", replace(recmod.settings, ai_enabled=False, openrouter_api_key=""))
    tender = {"source_id":"ATM-HIGH","title":"High score but unanalysed","internal_deterministic_score":99}
    result = analyse_tenders_for_company(_profile(), [tender])[0]
    assert result.recommendation != "APPLY"


def test_report_template_contains_actionable_sections():
    template = Path("app/reports/templates/report.html").read_text(encoding="utf-8")
    for phrase in (
        "These are the current tenders NixSec recommends your company consider applying for",
        "Why this is suited to your business",
        "Evidence from your business/history",
        "Why you should apply",
        "Application steps",
        "Documentation you will need",
        "What to verify before committing bid effort",
    ):
        assert phrase in template

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from app.analysis.scoring import opportunity_score, prospect_score, recompete_score
from app.collectors.austender_contracts import parse_release
from app.collectors.austender_live import parse_detail, parse_rss
from app.models import Agency, Prospect, Supplier, Tender
from app.reports.generator import generate

RSS = b'''<rss><channel><item><title>ATM-1: Security Services</title><link>https://www.tenders.gov.au/Atm/Show/abc</link><description><![CDATA[<p>Cyber security work</p>]]></description><pubDate>Mon, 14 Sep 2026 00:00:00 GMT</pubDate></item></channel></rss>'''
DETAIL = '''<p class="lead">Security Services</p><div class="list-desc"><label for="AtmId">ATM ID</label>:<div class="list-desc-inner">ATM-1</div></div><div class="list-desc"><label for="Agency">Agency</label>:<div class="list-desc-inner">Test Agency</div></div><div class="list-desc"><label for="Category">Category</label>:<div class="list-desc-inner">81111800 - System services</div></div><div class="list-desc"><label for="CloseDate">Close Date &amp; Time</label>:<div class="list-desc-inner">14-Sep-2099 5:00 pm <span>(ACT Local Time)</span></div></div><div class="list-desc"><label for="Type">ATM Type</label>:<div class="list-desc-inner">Request for Tender</div></div>'''
NOTICE_DETAIL = '''<p class="lead">Notice - Estate Works Program Defence Industry Update</p><div class="list-desc"><label for="AtmId">ATM ID</label>:<div class="list-desc-inner">NOTICE-1</div></div><div class="list-desc"><label for="Agency">Agency</label>:<div class="list-desc-inner">Department of Defence</div></div><div class="list-desc"><label for="Category">Category</label>:<div class="list-desc-inner">80160000 - Business administration services</div></div><div class="list-desc"><label for="CloseDate">Close Date &amp; Time</label>:<div class="list-desc-inner">14-Sep-2099 5:00 pm <span>(ACT Local Time)</span></div></div><div class="list-desc"><label for="Type">ATM Type</label>:<div class="list-desc-inner">Notice</div></div>'''


def test_rss_and_detail_parser():
    rows = parse_rss(RSS)
    assert len(rows) == 1 and rows[0]["atm_id_hint"] == "ATM-1"
    result = parse_detail(DETAIL, rows[0])
    assert result["agency_name"] == "Test Agency"
    assert result["unspsc"] == "81111800"
    assert result["status"] == "LIVE"


def test_non_bid_notice_is_not_live_tender():
    base = {
        "atm_id_hint": "NOTICE-1",
        "title": "Notice - Estate Works Program Defence Industry Update",
        "description": "Industry information only",
        "published_at": datetime.now(timezone.utc),
        "source_url": "https://www.tenders.gov.au/Atm/Show/notice-1",
    }
    result = parse_detail(NOTICE_DETAIL, base)
    assert result["tender_type"] == "Notice"
    assert result["status"] == "NOTICE"


def test_contract_supplier_resolves_through_award_not_first_supplier_party():
    release = {
        "ocid": "ocds-test-1",
        "date": "2026-09-14T00:00:00Z",
        "parties": [
            {"id": "buyer-1", "name": "Test Agency", "roles": ["buyer"]},
            {
                "id": "supplier-wrong",
                "name": "ZESTUCCINE PTY LTD",
                "roles": ["supplier"],
                "additionalIdentifiers": [{"scheme": "AU-ABN", "id": "52 111 057 446"}],
            },
            {
                "id": "supplier-right",
                "name": "ANDREW H WEST & ASSOCIATES",
                "roles": ["supplier"],
                "additionalIdentifiers": [{"scheme": "AU-ABN", "id": "48 237 467 157"}],
            },
        ],
        "awards": [
            {
                "id": "award-1",
                "suppliers": [{"id": "supplier-right", "name": "ANDREW H WEST & ASSOCIATES"}],
            }
        ],
        "contracts": [
            {
                "id": "CN-TEST-1",
                "awardID": "award-1",
                "title": "Secretariat Services",
                "value": {"amount": 26100},
                "period": {"startDate": "2026-09-01T00:00:00Z", "endDate": "2027-09-01T00:00:00Z"},
                "items": [
                    {
                        "classification": {
                            "scheme": "UNSPSC",
                            "id": "80160000",
                            "description": "Business administration services",
                        }
                    }
                ],
            }
        ],
        "tender": {},
    }
    result = parse_release(release)
    assert result["supplier_name"] == "ANDREW H WEST & ASSOCIATES"
    assert result["supplier_abn"] == "48237467157"
    assert result["supplier_issue"] is None
    assert result["unspsc"] == "80160000"
    assert result["category"] == "Business administration services"


def test_scores_deterministic_and_explainable():
    total, parts = prospect_score(contract_count=8, disclosed_value=Decimal("6000000"), agency_count=4, latest_award=date.today(), live_market_count=5, opportunity_count=5, name="Example Pty Ltd")
    assert total == sum(parts.values()) and 0 <= total <= 100
    score, label, breakdown = opportunity_score(capability=29, category=14, agency=13, historical=12, location=10, closes_at=datetime.now(timezone.utc) + timedelta(days=12), competitive=4)
    assert score == sum(breakdown.values()) and label == "HIGH"
    confidence, evidence = recompete_score(end_date=date.today() + timedelta(days=100), amendment_count=2, agency_frequency=3, relationship_years=2)
    assert confidence == sum(evidence.values())


def test_duplicate_tender_constraint(db):
    db.add(Agency(canonical_name="A")); db.flush()
    kwargs = dict(source="s", source_id="1", title="x", source_url="u", status="LIVE", raw_hash="h")
    db.add(Tender(**kwargs)); db.commit(); db.add(Tender(**kwargs))
    try:
        db.commit()
        assert False
    except IntegrityError:
        db.rollback()


def test_report_cover_first_and_missing_data(db, tmp_path, monkeypatch):
    import app.reports.generator as generator
    from dataclasses import replace
    monkeypatch.setattr(generator, "settings", replace(generator.settings, report_dir=tmp_path))
    supplier = Supplier(canonical_name="Example Pty Ltd", contract_count=2, agency_count=1, disclosed_value=Decimal("100000"))
    db.add(supplier); db.flush()
    prospect = Prospect(supplier_id=supplier.id, score=55, score_breakdown={}, main_categories=[], inferred_capabilities=[])
    db.add(prospect); db.commit()
    run = generate(db, prospect.id)
    data = Path(run.file_path).read_bytes()
    assert data.startswith(b"%PDF") and run.parameters["cover_first"] is True
    assert run.parameters["identity_fields_independently_verified"] is False


def test_prospect_history_identity(db):
    supplier = Supplier(canonical_name="Keep Me Pty Ltd", abn="12345678901")
    db.add(supplier); db.flush()
    prospect = Prospect(supplier_id=supplier.id, score=70, score_breakdown={})
    db.add(prospect); db.commit()
    assert prospect.supplier.canonical_name == "Keep Me Pty Ltd"

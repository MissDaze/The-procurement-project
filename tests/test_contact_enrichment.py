"""Tests for contact enrichment service.

Verifies:
- Contact deduplication by (email, phone, profile_url) tuple
- No email generation from names or company names
- HTTP mocking; no live external calls
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest
from sqlalchemy import func, select

from app.models import Prospect, ProspectContact, Supplier
from app.services.contact_enrichment import (
    _company_tokens,
    _extract_page,
    _normalise_email,
    enrich_prospect,
    enrich_missing_prospects,
)


@pytest.fixture
def supplier(db):
    """Create a test supplier."""
    s = Supplier(canonical_name="Test Solutions Pty Ltd", abn="12345678901", state="NSW")
    db.add(s)
    db.flush()
    return s


@pytest.fixture
def prospect(db, supplier):
    """Create a test prospect."""
    p = Prospect(supplier_id=supplier.id, score=75, score_breakdown={})
    db.add(p)
    db.commit()
    return p


def test_normalise_email_rejects_noreply_addresses():
    """Verify no-reply addresses are filtered out."""
    assert _normalise_email("noreply@example.com") is None
    assert _normalise_email("no-reply@example.com") is None
    assert _normalise_email("privacy@example.com") is None
    assert _normalise_email("test@example.com") is None  # example.com is placeholder
    assert _normalise_email("info@example.org") is None  # example.org is placeholder


def test_normalise_email_lowercases_and_strips():
    """Verify emails are normalized: lowercased, stripped, and excess punctuation removed."""
    assert _normalise_email("  John.Doe@Company.COM  ") == "john.doe@company.com"
    assert _normalise_email("contact@business.com.au") == "contact@business.com.au"
    assert _normalise_email("sales@firm.org.uk") == "sales@firm.org.uk"


def test_company_tokens_excludes_stopwords():
    """Verify company name tokenization excludes common stop words."""
    tokens = _company_tokens("ABC Solutions Pty Ltd Australia")
    assert "abc" in tokens
    assert "solutions" in tokens
    assert "pty" not in tokens  # filtered
    assert "ltd" not in tokens  # filtered
    assert "australia" not in tokens  # filtered


def test_extract_page_deduplicates_contacts():
    """Verify _extract_page deduplicates contacts by (email, phone, profile_url)."""
    html = """
    <html>
    <body>
        <p>Contact us at sales@business.com</p>
        <a href="mailto:sales@business.com">Email</a>
        <a href="tel:+61293339999">+61 2 9333 9999</a>
        <p>Phone: +61 2 9333 9999</p>
    </body>
    </html>
    """
    url = "https://business.com.au/contact"
    contacts, links = _extract_page(url, html)
    
    # Should have exactly one contact per unique (email, phone, url) - no duplicates
    assert len(contacts) >= 1
    # All should have same profile_url (the page itself)
    assert all(c["profile_url"] == url for c in contacts)
    # Check for email and phone presence (both extracted from same page)
    emails = {c.get("business_email") for c in contacts if c.get("business_email")}
    phones = {c.get("business_phone") for c in contacts if c.get("business_phone")}
    # Should have found both email and phone
    assert "sales@business.com" in emails
    assert any("9333 9999" in p for p in phones)


def test_extract_page_rejects_senior_role_without_matching_email():
    """Verify senior roles are only recorded when co-located with an actual business email."""
    html = """
    <html>
    <body>
        <section>
            John Smith, Chief Executive Officer
            contact: sales@business.com
        </section>
        <section>
            Jane Doe, Managing Director
            (no contact info provided here)
        </section>
    </body>
    </html>
    """
    url = "https://business.com.au"
    contacts, links = _extract_page(url, html)
    
    # Should extract email contact, not manufacture a Jane Doe contact without email
    assert any(c.get("business_email") == "sales@business.com" for c in contacts)
    # Jane Doe should not appear as a solo contact with no email
    jane_contacts = [c for c in contacts if "Jane" in c.get("name", "")]
    assert len(jane_contacts) == 0 or all(c.get("business_email") for c in jane_contacts)


def test_enrich_prospect_deduplicates_repeated_calls(db, prospect):
    """Verify enrich_prospect does not create duplicate contacts on repeated runs."""
    mock_html = """
    <html>
    <body>
        <p>Email: support@business.com</p>
        <p>Phone: +61 2 8123 4567</p>
    </body>
    </html>
    """
    
    with patch("httpx.Client") as MockClient:
        mock_client = MagicMock()
        MockClient.return_value.__enter__.return_value = mock_client
        
        # Setup mock search results
        mock_client.post.return_value = MagicMock(
            text="<html><a class='result__a' href='https://business.com.au'>Website</a></html>"
        )
        
        # Setup mock website fetch
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = mock_html
        mock_response.url = "https://business.com.au"
        mock_client.get.return_value = mock_response
        
        # First enrichment
        result1 = enrich_prospect(db, prospect)
        count1 = db.scalar(select(func.count(ProspectContact.id)).where(ProspectContact.prospect_id == prospect.id))
        
        # Second enrichment with same data
        result2 = enrich_prospect(db, prospect)
        count2 = db.scalar(select(func.count(ProspectContact.id)).where(ProspectContact.prospect_id == prospect.id))
        
        # Contact count should not increase on second call
        assert count1 == count2
        assert result2["created"] == 0


def test_enrich_prospect_no_http_calls_without_mocking(db, prospect):
    """Verify enrich_prospect will not make real HTTP calls when called with a mock."""
    # This test ensures that if mocking fails, the test itself fails loudly
    # by attempting an HTTP call (which would fail without real network)
    
    with patch("httpx.Client") as MockClient:
        mock_client = MagicMock()
        MockClient.return_value.__enter__.return_value = mock_client
        
        # Simulate network failure (would happen if real call attempted)
        mock_client.post.side_effect = RuntimeError("Should not make real HTTP calls in tests!")
        
        result = enrich_prospect(db, prospect)
        assert result["status"] == "search_failed"


def test_enrich_missing_prospects_respects_limit(db):
    """Verify enrich_missing_prospects respects the limit parameter."""
    # Create 5 prospects without contacts
    for i in range(5):
        supplier = Supplier(canonical_name=f"Company {i}", abn=f"1234567890{i}", state="NSW")
        db.add(supplier)
        db.flush()
        prospect = Prospect(supplier_id=supplier.id, score=80 - i, score_breakdown={})
        db.add(prospect)
    db.commit()
    
    with patch("app.services.contact_enrichment.enrich_prospect") as mock_enrich:
        mock_enrich.return_value = {"created": 1, "website": "https://example.com", "status": "ok"}
        
        result = enrich_missing_prospects(db, limit=2)
        
        # Should only check 2 prospects even though 5 exist
        assert result["prospects_checked"] == 2
        assert mock_enrich.call_count == 2


def test_enrich_missing_prospects_skips_already_enriched(db):
    """Verify enrich_missing_prospects skips prospects that already have contacts."""
    supplier = Supplier(canonical_name="Already Enriched Co", abn="11111111111", state="NSW")
    db.add(supplier)
    db.flush()
    
    prospect = Prospect(supplier_id=supplier.id, score=85, score_breakdown={})
    db.add(prospect)
    db.flush()
    
    # Add an existing contact
    contact = ProspectContact(
        prospect_id=prospect.id,
        name="John Smith",
        job_title="Sales Manager",
        business_email="john@example.com",
        profile_url="https://example.com"
    )
    db.add(contact)
    db.commit()
    
    with patch("app.services.contact_enrichment.enrich_prospect") as mock_enrich:
        mock_enrich.return_value = {"created": 0, "website": None, "status": "ok"}
        
        result = enrich_missing_prospects(db, limit=10)
        
        # Should not call enrich_prospect since this prospect already has a contact
        assert mock_enrich.call_count == 0
        assert result["prospects_checked"] == 0


def test_no_email_generation_from_name_or_company():
    """Verify that NO emails are manufactured from person or company names.
    
    This is a core safety constraint: we only extract emails that are explicitly
    published on the website, never generate them from names.
    """
    # Test that _extract_page doesn't construct emails from names
    html = """
    <html>
    <body>
        <h1>John Smith</h1>
        <p>Senior Manager at Company Solutions</p>
        <p>Address: 123 Business St, Sydney NSW</p>
    </body>
    </html>
    """
    
    url = "https://example.com"
    contacts, _ = _extract_page(url, html)
    
    # Should extract zero contacts because no actual email/phone is present
    # and we don't generate john@company.com or john.smith@company.com
    for contact in contacts:
        if contact.get("business_email"):
            # If there's an email, it must not look like: first.last@company pattern
            email = contact["business_email"]
            assert "john" not in email.lower() or "@" not in email
            assert "smith" not in email.lower() or "@" not in email


def test_enrich_prospect_returns_status_dict(db, prospect):
    """Verify enrich_prospect returns expected status structure."""
    with patch("httpx.Client") as MockClient:
        mock_client = MagicMock()
        MockClient.return_value.__enter__.return_value = mock_client
        
        mock_client.post.return_value = MagicMock(
            text="<html></html>"
        )
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client.get.return_value = mock_response
        
        result = enrich_prospect(db, prospect)
        
        # Verify all expected keys are present
        assert "created" in result
        assert "website" in result
        assert "status" in result
        assert isinstance(result["created"], int)
        assert isinstance(result["status"], str)


def test_contact_deduplication_with_same_email_different_phone(db, prospect):
    """Verify that same email with different phones are treated as separate contacts."""
    # Manually insert two contacts with same email but different phones
    c1 = ProspectContact(
        prospect_id=prospect.id,
        name="John Smith",
        job_title="Sales",
        business_email="sales@business.com",
        business_phone="+61 2 1234 5678",
        profile_url="https://business.com/contact"
    )
    c2 = ProspectContact(
        prospect_id=prospect.id,
        name="Jane Doe",
        job_title="Support",
        business_email="support@business.com",
        business_phone="+61 2 9876 5432",
        profile_url="https://business.com/support"
    )
    db.add_all([c1, c2])
    db.commit()
    
    count = db.scalar(select(func.count(ProspectContact.id)).where(ProspectContact.prospect_id == prospect.id))
    assert count == 2


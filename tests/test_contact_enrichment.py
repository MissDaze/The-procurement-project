from app.services.contact_enrichment import _extract_decision_makers, _normalise_email


def test_generic_inboxes_are_rejected():
    assert _normalise_email("info@company.com.au") is None
    assert _normalise_email("sales@company.com.au") is None
    assert _normalise_email("admin@company.com.au") is None
    assert _normalise_email("contact@company.com.au") is None


def test_named_ceo_with_published_email_is_accepted():
    html = """
    <html><body><section>
      <h2>Jane Smith</h2>
      <p>Chief Executive Officer</p>
      <p>Email: jane.smith@company.com.au</p>
    </section></body></html>
    """
    contacts, _ = _extract_decision_makers("https://company.com.au/team", html)
    assert any(
        c["name"] == "Jane Smith"
        and c["business_email"] == "jane.smith@company.com.au"
        and "executive" in c["job_title"].lower()
        for c in contacts
    )


def test_no_email_is_invented_when_page_has_none():
    html = """
    <html><body><section>
      <h2>Jane Smith</h2>
      <p>Managing Director</p>
      <p>Please contact the company through our enquiry form.</p>
    </section></body></html>
    """
    contacts, _ = _extract_decision_makers("https://company.com.au/team", html)
    assert contacts == []


def test_generic_email_beside_ceo_is_not_promoted():
    html = """
    <html><body><section>
      <h2>Jane Smith</h2>
      <p>CEO</p>
      <p>Email: info@company.com.au</p>
    </section></body></html>
    """
    contacts, _ = _extract_decision_makers("https://company.com.au/team", html)
    assert contacts == []

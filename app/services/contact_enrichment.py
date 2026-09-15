from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Prospect, ProspectContact

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NixSecProcurementIntelligence/1.0; +https://nixsec.pro)",
    "Accept-Language": "en-AU,en;q=0.9",
}
SEARCH_URL = "https://html.duckduckgo.com/html/"
BLOCKED_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com",
    "abr.business.gov.au", "asic.gov.au", "australiacheck.com", "dnb.com",
    "zoominfo.com", "rocketreach.co", "signalhire.com", "apollo.io",
    "yellowpages.com.au", "truelocal.com.au", "hotfrog.com.au",
}
CONTACT_PATH_TERMS = ("contact", "about", "team", "people", "leadership", "company")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE_RE = re.compile(r"(?:(?:\+?61\s?[2-478])|(?:0[2-478]))(?:[\s().-]*\d){8}|(?:1300|1800)[\s.-]*\d{3}[\s.-]*\d{3}")
ROLE_RE = re.compile(r"\b(?:chief executive officer|ceo|managing director|director|head of (?:sales|business development|government|public sector)|business development manager|sales director|general manager|bid manager|tender manager)\b", re.I)


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _blocked(url: str) -> bool:
    host = _host(url)
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


def _clean_result_url(href: str) -> str | None:
    if not href:
        return None
    if href.startswith("//duckduckgo.com/l/"):
        query = parse_qs(urlparse("https:" + href).query)
        raw = query.get("uddg", [None])[0]
        return unquote(raw) if raw else None
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return None


def _search_candidates(company_name: str, client: httpx.Client) -> list[str]:
    response = client.post(SEARCH_URL, data={"q": f'"{company_name}" Australia contact'}, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    urls: list[str] = []
    for a in soup.select("a.result__a"):
        url = _clean_result_url(a.get("href", ""))
        if not url or _blocked(url):
            continue
        host = _host(url)
        if not host or host.endswith("duckduckgo.com"):
            continue
        if url not in urls:
            urls.append(url)
        if len(urls) >= 5:
            break
    return urls


def _company_tokens(name: str) -> set[str]:
    stop = {"pty", "ltd", "limited", "the", "and", "associates", "group", "australia", "services"}
    return {w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 3 and w not in stop}


def _looks_official(url: str, company_name: str, html: str) -> bool:
    tokens = _company_tokens(company_name)
    host_words = set(re.findall(r"[a-z0-9]+", _host(url)))
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()[:12000]
    host_match = bool(tokens & host_words)
    text_hits = sum(1 for token in tokens if token in text)
    return host_match or text_hits >= min(2, max(1, len(tokens)))


def _normalise_email(email: str) -> str | None:
    value = email.strip().strip(".,;:()[]<>").lower()
    if value.endswith(("@example.com", "@example.org")):
        return None
    local = value.split("@", 1)[0]
    if local in {"noreply", "no-reply", "privacy"}:
        return None
    return value


def _extract_page(url: str, html: str) -> tuple[list[dict], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    contacts: list[dict] = []
    seen: set[tuple[str | None, str | None]] = set()

    emails = {_normalise_email(x) for x in EMAIL_RE.findall(soup.get_text(" ", strip=True))}
    for a in soup.select('a[href^="mailto:"]'):
        emails.add(_normalise_email(a.get("href", "").split(":", 1)[1].split("?", 1)[0]))
    emails.discard(None)

    phones = {re.sub(r"\s+", " ", x).strip() for x in PHONE_RE.findall(soup.get_text(" ", strip=True))}
    for a in soup.select('a[href^="tel:"]'):
        raw = a.get("href", "").split(":", 1)[1].strip()
        if raw:
            phones.add(raw)

    for email in sorted(emails):
        key = (email, None)
        if key not in seen:
            contacts.append({"name": "Company contact", "job_title": "Public business contact", "business_email": email, "business_phone": None, "profile_url": url})
            seen.add(key)
    for phone in sorted(phones):
        key = (None, phone)
        if key not in seen:
            contacts.append({"name": "Company contact", "job_title": "Public business contact", "business_email": None, "business_phone": phone, "profile_url": url})
            seen.add(key)

    # If a public business email appears in the same content block as a senior role,
    # preserve the displayed name/title only when the official site itself states it.
    for block in soup.find_all(["p", "li", "div", "section", "article"]):
        text = " ".join(block.get_text(" ", strip=True).split())
        role = ROLE_RE.search(text)
        block_emails = [_normalise_email(x) for x in EMAIL_RE.findall(text)]
        block_emails = [x for x in block_emails if x]
        if not role or not block_emails or len(text) > 500:
            continue
        name_match = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z'’-]+){1,3})\b", text)
        if not name_match:
            continue
        for email in block_emails:
            key = (email, None)
            contacts.append({"name": name_match.group(1), "job_title": role.group(0).strip(), "business_email": email, "business_phone": None, "profile_url": url})
            seen.add(key)

    links: list[str] = []
    for a in soup.find_all("a", href=True):
        label = (a.get_text(" ", strip=True) + " " + a["href"]).lower()
        if any(term in label for term in CONTACT_PATH_TERMS):
            candidate = urljoin(url, a["href"])
            if _host(candidate) == _host(url) and candidate not in links:
                links.append(candidate)
        if len(links) >= 6:
            break
    return contacts, links


def enrich_prospect(db: Session, prospect: Prospect) -> dict:
    existing = db.scalars(select(ProspectContact).where(ProspectContact.prospect_id == prospect.id)).all()
    existing_keys = {(c.business_email or "", c.business_phone or "", c.profile_url or "") for c in existing}
    created = 0
    website = None
    errors: list[str] = []

    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=20) as client:
        try:
            candidates = _search_candidates(prospect.supplier.canonical_name, client)
        except Exception as exc:
            return {"created": 0, "website": None, "status": "search_failed", "error": str(exc)}

        for candidate in candidates:
            try:
                response = client.get(candidate)
                response.raise_for_status()
                if "text/html" not in response.headers.get("content-type", ""):
                    continue
                if not _looks_official(str(response.url), prospect.supplier.canonical_name, response.text):
                    continue
                website = str(response.url)
                page_contacts, links = _extract_page(website, response.text)
                for link in links[:4]:
                    try:
                        r = client.get(link)
                        if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
                            more, _ = _extract_page(str(r.url), r.text)
                            page_contacts.extend(more)
                    except Exception:
                        pass

                for row in page_contacts:
                    key = (row.get("business_email") or "", row.get("business_phone") or "", row.get("profile_url") or "")
                    if key in existing_keys:
                        continue
                    db.add(ProspectContact(prospect_id=prospect.id, **row))
                    existing_keys.add(key)
                    created += 1
                db.commit()
                break
            except Exception as exc:
                errors.append(str(exc))

    return {"created": created, "website": website, "status": "ok" if website else "not_found", "errors": errors[:3]}


def enrich_missing_prospects(db: Session, limit: int = 20) -> dict:
    prospects = db.scalars(select(Prospect).order_by(Prospect.score.desc()).limit(250)).all()
    checked = 0
    created = 0
    for prospect in prospects:
        has_contact = db.scalar(select(ProspectContact.id).where(ProspectContact.prospect_id == prospect.id).limit(1))
        if has_contact:
            continue
        result = enrich_prospect(db, prospect)
        checked += 1
        created += int(result.get("created", 0))
        if checked >= limit:
            break
    return {"prospects_checked": checked, "contacts_created": created}

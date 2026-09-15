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
    "facebook.com", "instagram.com", "x.com", "twitter.com",
    "abr.business.gov.au", "asic.gov.au", "dnb.com", "zoominfo.com",
    "rocketreach.co", "signalhire.com", "apollo.io", "yellowpages.com.au",
}
CONTACT_PATH_TERMS = ("contact", "about", "team", "people", "leadership", "management", "company")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
ROLE_RE = re.compile(
    r"\b(?:chief executive officer|ceo|managing director|founder|co-founder|"
    r"executive director|sales director|business development director|"
    r"business development manager|bid manager|tender manager|"
    r"head of (?:sales|business development|government|public sector)|"
    r"government (?:sales|business development) lead|public sector lead|director)\b",
    re.I,
)
GENERIC_LOCALS = {"info", "sales", "admin", "contact", "hello", "office", "support", "enquiries", "reception"}
NAME_RE = re.compile(r"\b([A-Z][a-zA-Z'’-]+(?:\s+[A-Z][a-zA-Z'’-]+){1,2})\b")


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
    return href if href.startswith(("http://", "https://")) else None


def _company_tokens(name: str) -> set[str]:
    stop = {"pty", "ltd", "limited", "the", "and", "associates", "group", "australia", "services"}
    return {w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 3 and w not in stop}


def _search_candidates(company_name: str, client: httpx.Client) -> list[str]:
    queries = [
        f'"{company_name}" CEO email Australia',
        f'"{company_name}" "Managing Director" email',
        f'"{company_name}" "Business Development" email',
        f'"{company_name}" "Public Sector" email',
    ]
    urls: list[str] = []
    for query in queries:
        response = client.post(SEARCH_URL, data={"q": query}, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for a in soup.select("a.result__a"):
            url = _clean_result_url(a.get("href", ""))
            if not url or _blocked(url) or _host(url).endswith("duckduckgo.com"):
                continue
            if url not in urls:
                urls.append(url)
            if len(urls) >= 8:
                return urls
    return urls


def _looks_official(url: str, company_name: str, html: str) -> bool:
    tokens = _company_tokens(company_name)
    host_words = set(re.findall(r"[a-z0-9]+", _host(url)))
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()[:12000]
    return bool(tokens & host_words) or sum(1 for token in tokens if token in text) >= min(2, max(1, len(tokens)))


def _normalise_email(email: str) -> str | None:
    value = email.strip().strip(".,;:()[]<>").lower()
    if "@" not in value or value.endswith(("@example.com", "@example.org")):
        return None
    local = value.split("@", 1)[0]
    if local in GENERIC_LOCALS or local in {"noreply", "no-reply", "privacy"}:
        return None
    return value


def _name_from_block(text: str, role_match: re.Match) -> str | None:
    before = text[:role_match.start()].strip(" -|,:;()")
    before_matches = NAME_RE.findall(before)
    if before_matches:
        return before_matches[-1]
    after = text[role_match.end():].strip(" -|,:;()")
    after_matches = NAME_RE.findall(after)
    return after_matches[0] if after_matches else None


def _extract_decision_makers(url: str, html: str) -> tuple[list[dict], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    contacts: list[dict] = []
    seen: set[str] = set()

    for block in soup.find_all(["p", "li", "div", "section", "article", "td"]):
        text = " ".join(block.get_text(" ", strip=True).split())
        if not text or len(text) > 700:
            continue
        role = ROLE_RE.search(text)
        if not role:
            continue
        emails = {_normalise_email(x) for x in EMAIL_RE.findall(text)}
        for a in block.select('a[href^="mailto:"]'):
            emails.add(_normalise_email(a.get("href", "").split(":", 1)[1].split("?", 1)[0]))
        emails.discard(None)
        if not emails:
            continue
        name = _name_from_block(text, role)
        if not name:
            continue
        for email in sorted(emails):
            if email in seen:
                continue
            contacts.append({
                "name": name,
                "job_title": role.group(0).strip(),
                "business_email": email,
                "business_phone": None,
                "profile_url": url,
            })
            seen.add(email)

    links: list[str] = []
    for a in soup.find_all("a", href=True):
        label = (a.get_text(" ", strip=True) + " " + a["href"]).lower()
        if any(term in label for term in CONTACT_PATH_TERMS):
            candidate = urljoin(url, a["href"])
            if _host(candidate) == _host(url) and candidate not in links:
                links.append(candidate)
        if len(links) >= 8:
            break
    return contacts, links


def enrich_prospect(db: Session, prospect: Prospect) -> dict:
    existing = db.scalars(select(ProspectContact).where(ProspectContact.prospect_id == prospect.id)).all()
    existing_emails = {c.business_email.lower() for c in existing if c.business_email}
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
                found, links = _extract_decision_makers(website, response.text)
                for link in links[:6]:
                    try:
                        r = client.get(link)
                        if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
                            more, _ = _extract_decision_makers(str(r.url), r.text)
                            found.extend(more)
                    except Exception:
                        pass

                for row in found:
                    email = row["business_email"].lower()
                    if email in existing_emails:
                        continue
                    db.add(ProspectContact(prospect_id=prospect.id, **row))
                    existing_emails.add(email)
                    created += 1
                db.commit()
                if created:
                    break
            except Exception as exc:
                errors.append(str(exc))

    return {"created": created, "website": website, "status": "ok" if created else "not_found", "errors": errors[:3]}


def enrich_missing_prospects(db: Session, limit: int = 20) -> dict:
    prospects = db.scalars(select(Prospect).order_by(Prospect.score.desc()).limit(250)).all()
    checked = 0
    created = 0
    for prospect in prospects:
        has_named_email = db.scalar(
            select(ProspectContact.id).where(
                ProspectContact.prospect_id == prospect.id,
                ProspectContact.business_email.is_not(None),
            ).limit(1)
        )
        if has_named_email:
            continue
        result = enrich_prospect(db, prospect)
        checked += 1
        created += int(result.get("created", 0))
        if checked >= limit:
            break
    return {"prospects_checked": checked, "decision_maker_emails_created": created}

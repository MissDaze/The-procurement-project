from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.scoring import opportunity_score, prospect_score, recompete_score
from app.models import Contract, ContractVersion, OpportunityScore, Prospect, ProspectCapability, RecompeteScore, Supplier, Tender


LIVE_STATUSES = ("LIVE", "CLOSING SOON")
# The official current ATM feed is refreshed regularly. A tender that has not
# been observed for longer than this is withheld from matching/reports until it
# is seen again, rather than being assumed to still be live.
LIVE_FRESHNESS_HOURS = 30

# AusTender contract classifications are commonly UNSPSC codes. These codes are
# useful for matching, but they are not meaningful capability labels for users.
UNSPSC_EXACT_LABELS = {
    "80160000": "Business administration services",
    "80101500": "Business and corporate management consultation services",
    "80111600": "Temporary personnel services",
    "80111700": "Personnel recruitment",
    "81110000": "Computer services",
    "81111500": "Software or hardware engineering",
    "81111800": "System and system component administration services",
    "81112000": "Data services",
    "81112100": "Internet services",
    "81112200": "Software maintenance and support",
    "81160000": "Information technology service delivery",
}

UNSPSC_SEGMENT_LABELS = {
    "43": "Information technology and telecommunications",
    "44": "Office equipment and administrative supplies",
    "46": "Security and safety services and equipment",
    "70": "Agriculture and environmental services",
    "71": "Mining and resource services",
    "72": "Building, construction and maintenance services",
    "73": "Industrial production and manufacturing services",
    "76": "Industrial cleaning services",
    "77": "Environmental services",
    "78": "Transport, storage and logistics services",
    "80": "Management and business administration services",
    "81": "Engineering, research and technology services",
    "82": "Editorial, design and creative services",
    "83": "Utilities and public services",
    "84": "Financial and insurance services",
    "85": "Healthcare services",
    "86": "Education and training services",
    "90": "Travel, hospitality and entertainment services",
    "91": "Personal and domestic services",
    "92": "Public safety, defence and security services",
    "93": "Public administration and civic services",
}

CAPABILITY_PATTERNS = (
    (r"\bsecretariat\b", "Secretariat services"),
    (r"\badministrat(?:ion|ive)\b", "Business administration services"),
    (r"\bcyber(?:security| security)?\b", "Cybersecurity services"),
    (r"\bessential\s+eight\b", "Essential Eight cybersecurity"),
    (r"\bpenetration\s+test(?:ing)?\b", "Penetration testing"),
    (r"\bsecurity\s+assessment\b", "Security assessment services"),
    (r"\bgovernance[, /-]*(?:risk[, /-]*)?(?:and )?compliance\b|\bGRC\b", "Governance, risk and compliance"),
    (r"\bcloud\b", "Cloud services"),
    (r"\bsoftware\b", "Software services"),
    (r"\bICT\b|\binformation technology\b", "ICT services"),
    (r"\bconsult(?:ing|ancy|ant)\b", "Consulting services"),
    (r"\bproject management\b", "Project management"),
    (r"\bprogram(?:me)? management\b", "Program management"),
    (r"\brecruit(?:ment|ing)\b", "Recruitment services"),
    (r"\btraining\b|\blearning and development\b", "Training services"),
    (r"\bengineering\b", "Engineering services"),
    (r"\bmaintenance\b", "Maintenance services"),
    (r"\blogistics\b", "Logistics services"),
    (r"\bcleaning\b", "Cleaning services"),
)

STOPWORDS = {
    "about", "after", "again", "against", "being", "between", "contract",
    "contracts", "department", "government", "services", "service", "supply",
    "australian", "australia", "commonwealth", "procurement", "provision",
    "support", "works", "other", "their", "there", "which", "with", "from",
}


def _current_live_tenders(db: Session) -> list[Tender]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LIVE_FRESHNESS_HOURS)
    return db.scalars(
        select(Tender).where(
            Tender.status.in_(LIVE_STATUSES),
            Tender.last_seen_at >= cutoff,
            Tender.closes_at > datetime.now(timezone.utc),
        )
    ).all()


def _is_unspsc_code(value: str | None) -> bool:
    return bool(value and re.fullmatch(r"\d{8}", value.strip()))


def _humanise_category(value: str | None) -> str | None:
    if not value:
        return None
    text = " ".join(value.split()).strip()
    match = re.match(r"^(\d{8})\s*(?:[-–—:]\s*(.+))?$", text)
    if match:
        code, supplied_label = match.group(1), match.group(2)
        if supplied_label:
            return supplied_label.strip()
        return UNSPSC_EXACT_LABELS.get(code) or UNSPSC_SEGMENT_LABELS.get(code[:2])
    return text


def _infer_capabilities(contracts: list[Contract]) -> list[str]:
    scores: Counter[str] = Counter()
    for contract in contracts:
        category = _humanise_category(contract.category)
        if category:
            scores[category] += 3

        code = (contract.unspsc or "").strip()
        if _is_unspsc_code(code):
            label = UNSPSC_EXACT_LABELS.get(code) or UNSPSC_SEGMENT_LABELS.get(code[:2])
            if label:
                scores[label] += 2

        corpus = " ".join(filter(None, [contract.title, contract.description, category]))
        for pattern, label in CAPABILITY_PATTERNS:
            if re.search(pattern, corpus, flags=re.IGNORECASE):
                scores[label] += 2

    return [name for name, _ in scores.most_common() if name and not _is_unspsc_code(name)][:6]


def _matching_keywords(contracts: list[Contract], capabilities: list[str]) -> set[str]:
    text = " ".join(
        capabilities
        + [
            value
            for c in contracts
            for value in (c.title or "", c.description or "", _humanise_category(c.category) or "")
        ]
    ).lower()
    return {
        word
        for word in re.findall(r"[a-z][a-z0-9-]{3,}", text)
        if word not in STOPWORDS and not word.isdigit()
    }


def rebuild_supplier_metrics(db: Session) -> None:
    for supplier in db.scalars(select(Supplier)).all():
        rows = db.scalars(select(Contract).where(Contract.supplier_id == supplier.id)).all()
        supplier.contract_count = len(rows)
        supplier.disclosed_value = sum((r.current_value or 0) for r in rows)
        supplier.agency_count = len({r.agency_id for r in rows if r.agency_id})
        dates = [r.publication_date for r in rows if r.publication_date]
        supplier.first_award = min(dates) if dates else None
        supplier.latest_award = max(dates) if dates else None
    db.commit()


def discover_prospects(db: Session) -> int:
    rebuild_supplier_metrics(db)
    created = 0
    tenders = _current_live_tenders(db)

    for supplier in db.scalars(select(Supplier).where(Supplier.contract_count > 0)).all():
        if not supplier.abn or len("".join(filter(str.isdigit, supplier.abn))) != 11 or "withheld" in supplier.canonical_name.lower():
            continue

        contracts = db.scalars(select(Contract).where(Contract.supplier_id == supplier.id)).all()
        capabilities = _infer_capabilities(contracts)
        keywords = _matching_keywords(contracts, capabilities)
        live = sum(
            1
            for tender in tenders
            if keywords
            & set(re.findall(r"[a-z][a-z0-9-]{3,}", ((tender.title or "") + " " + (tender.category or "")).lower()))
        )

        score, breakdown = prospect_score(
            contract_count=supplier.contract_count,
            disclosed_value=supplier.disclosed_value,
            agency_count=supplier.agency_count,
            latest_award=supplier.latest_award,
            live_market_count=live,
            opportunity_count=live,
            name=supplier.canonical_name,
        )
        prospect = db.scalar(select(Prospect).where(Prospect.supplier_id == supplier.id))
        if not prospect:
            prospect = Prospect(supplier_id=supplier.id)
            db.add(prospect)
            db.flush()
            created += 1

        categories = [_humanise_category(c.category) for c in contracts]
        top_categories = [name for name, _ in Counter(x for x in categories if x).most_common(5)]
        prospect.score = score
        prospect.score_breakdown = breakdown
        prospect.main_categories = top_categories
        prospect.inferred_capabilities = capabilities or top_categories
        prospect.next_action = "Generate a tailored sales preview" if score >= 70 else "Review procurement footprint"

        for old in db.scalars(select(ProspectCapability).where(ProspectCapability.prospect_id == prospect.id)).all():
            if not old.confirmed and _is_unspsc_code(old.name):
                db.delete(old)

        for capability in prospect.inferred_capabilities:
            if not capability or _is_unspsc_code(capability):
                continue
            if not db.scalar(
                select(ProspectCapability).where(
                    ProspectCapability.prospect_id == prospect.id,
                    ProspectCapability.name == capability,
                )
            ):
                db.add(
                    ProspectCapability(
                        prospect_id=prospect.id,
                        name=capability,
                        evidence="Inferred from official awarded-contract category/title/description",
                        confirmed=False,
                    )
                )

    db.commit()
    return created


def calculate_matches(db: Session) -> int:
    count = 0
    live_tenders = _current_live_tenders(db)
    valid_tender_ids = {t.id for t in live_tenders}

    # Remove opportunity scores that refer to notices, closed, stale or otherwise
    # unverified tenders. This prevents an old score from leaking into a new report.
    for existing in db.scalars(select(OpportunityScore)).all():
        if existing.tender_id not in valid_tender_ids:
            db.delete(existing)
    db.flush()

    for prospect in db.scalars(select(Prospect)).all():
        supplier = prospect.supplier
        contracts = db.scalars(select(Contract).where(Contract.supplier_id == supplier.id)).all()
        capabilities = prospect.inferred_capabilities or _infer_capabilities(contracts)
        keywords = _matching_keywords(contracts, capabilities)
        agencies = {c.agency_id for c in contracts}
        readable_categories = " ".join(_humanise_category(c.category) or "" for c in contracts).lower()

        for tender in live_tenders:
            corpus = ((tender.title or "") + " " + (tender.description or "") + " " + (tender.category or "")).lower()
            tender_words = set(re.findall(r"[a-z][a-z0-9-]{3,}", corpus))
            overlap = len(keywords & tender_words)
            human_tender_category = _humanise_category(tender.category)
            category_match = bool(
                human_tender_category and human_tender_category.lower() in readable_categories
            )
            total, label, parts = opportunity_score(
                capability=min(30, overlap * 10),
                category=15 if category_match else min(15, overlap * 5),
                agency=15 if tender.agency_id in agencies else 5,
                historical=min(15, len(contracts) * 2),
                location=10,
                closes_at=tender.closes_at,
                competitive=3,
            )
            if total < 25:
                continue

            item = db.scalar(
                select(OpportunityScore).where(
                    OpportunityScore.prospect_id == prospect.id,
                    OpportunityScore.tender_id == tender.id,
                )
            )
            if not item:
                item = OpportunityScore(
                    prospect_id=prospect.id,
                    tender_id=tender.id,
                    score=total,
                    classification=label,
                    breakdown=parts,
                )
                db.add(item)
                count += 1
            else:
                item.score = total
                item.classification = label
                item.breakdown = parts

        for contract in db.scalars(select(Contract).where(Contract.end_date >= date.today())).all():
            contract_text = " ".join(filter(None, [contract.title, contract.description, _humanise_category(contract.category)])).lower()
            if keywords and not any(keyword in contract_text for keyword in keywords):
                continue
            amendments = db.scalar(
                select(func.count()).select_from(ContractVersion).where(ContractVersion.contract_id == contract.id)
            ) or 0
            conf, evidence = recompete_score(
                end_date=contract.end_date,
                amendment_count=amendments,
                agency_frequency=len([c for c in contracts if c.agency_id == contract.agency_id]),
                relationship_years=2,
            )
            if conf < 35:
                continue
            rec = db.scalar(
                select(RecompeteScore).where(
                    RecompeteScore.prospect_id == prospect.id,
                    RecompeteScore.contract_id == contract.id,
                )
            )
            if not rec:
                db.add(
                    RecompeteScore(
                        prospect_id=prospect.id,
                        contract_id=contract.id,
                        confidence=conf,
                        evidence=evidence,
                    )
                )

    db.commit()
    return count

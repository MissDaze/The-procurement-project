from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.scoring import opportunity_score, prospect_score, recompete_score
from app.models import Contract, ContractVersion, OpportunityScore, Prospect, ProspectCapability, RecompeteScore, Supplier, Tender


LIVE_STATUSES = ("LIVE", "CLOSING SOON")
LIVE_FRESHNESS_HOURS = 30

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


def _words(*values: str | None) -> set[str]:
    text = " ".join(v or "" for v in values).lower()
    return {
        word
        for word in re.findall(r"[a-z][a-z0-9-]{3,}", text)
        if word not in STOPWORDS and not word.isdigit()
    }


def _contract_category_labels(contracts: list[Contract]) -> set[str]:
    return {label.lower() for label in (_humanise_category(c.category) for c in contracts) if label}


def _contract_unspsc(contracts: list[Contract]) -> set[str]:
    return {(c.unspsc or "").strip() for c in contracts if _is_unspsc_code((c.unspsc or "").strip())}


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
        categories = _contract_category_labels(contracts)
        unspsc = _contract_unspsc(contracts)

        live = 0
        for tender in tenders:
            tender_words = _words(tender.title, tender.description, tender.category)
            overlap = len(keywords & tender_words)
            tender_category = (_humanise_category(tender.category) or "").lower()
            exact_unspsc = bool(tender.unspsc and tender.unspsc in unspsc)
            category_match = bool(tender_category and tender_category in categories)
            if exact_unspsc or category_match or overlap >= 2:
                live += 1

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

        readable_categories = [_humanise_category(c.category) for c in contracts]
        top_categories = [name for name, _ in Counter(x for x in readable_categories if x).most_common(5)]
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

    # Scores are cheap to rebuild and this prevents stale scores created by an
    # older algorithm from appearing in a new report.
    for existing in db.scalars(select(OpportunityScore)).all():
        db.delete(existing)
    for existing in db.scalars(select(RecompeteScore)).all():
        db.delete(existing)
    db.flush()

    for prospect in db.scalars(select(Prospect)).all():
        supplier = prospect.supplier
        contracts = db.scalars(select(Contract).where(Contract.supplier_id == supplier.id)).all()
        capabilities = prospect.inferred_capabilities or _infer_capabilities(contracts)
        keywords = _matching_keywords(contracts, capabilities)
        agencies = {c.agency_id for c in contracts if c.agency_id}
        category_labels = _contract_category_labels(contracts)
        prospect_unspsc = _contract_unspsc(contracts)

        for tender in live_tenders:
            tender_words = _words(tender.title, tender.description, tender.category)
            overlap_terms = keywords & tender_words
            overlap = len(overlap_terms)
            human_tender_category = (_humanise_category(tender.category) or "").lower()
            exact_unspsc = bool(tender.unspsc and tender.unspsc in prospect_unspsc)
            category_match = bool(human_tender_category and human_tender_category in category_labels)

            # Hard relevance gate: timing, location or a familiar agency may
            # strengthen a real match, but can never create one by themselves.
            if not (exact_unspsc or category_match or overlap >= 2):
                continue

            matching_contracts = 0
            for contract in contracts:
                contract_words = _words(contract.title, contract.description, contract.category)
                same_unspsc = bool(tender.unspsc and contract.unspsc and tender.unspsc == contract.unspsc)
                same_category = bool(
                    human_tender_category
                    and (_humanise_category(contract.category) or "").lower() == human_tender_category
                )
                if same_unspsc or same_category or len(contract_words & tender_words) >= 2:
                    matching_contracts += 1

            location_score = 0
            supplier_state = (supplier.state or "").strip().upper()
            tender_location = (tender.location or "").upper()
            if supplier_state and tender_location:
                if supplier_state in tender_location or "NATIONAL" in tender_location or "AUSTRALIA" in tender_location:
                    location_score = 10

            capability_score = min(30, overlap * 10 + (10 if exact_unspsc else 0))
            category_score = 15 if (exact_unspsc or category_match) else 5 if overlap >= 2 else 0
            agency_score = 10 if tender.agency_id and tender.agency_id in agencies else 0
            historical_score = min(15, matching_contracts * 4)

            total, label, parts = opportunity_score(
                capability=capability_score,
                category=category_score,
                agency=agency_score,
                historical=historical_score,
                location=location_score,
                closes_at=tender.closes_at,
                competitive=0,
            )
            if total < 40:
                continue

            db.add(
                OpportunityScore(
                    prospect_id=prospect.id,
                    tender_id=tender.id,
                    score=total,
                    classification=label,
                    breakdown={
                        **parts,
                        "Matched keywords": sorted(overlap_terms)[:8],
                        "Exact UNSPSC match": exact_unspsc,
                        "Exact category match": category_match,
                    },
                )
            )
            count += 1

        # Expiry intelligence is a watchlist, not a claim that a future tender
        # exists. Own contracts and market contracts are treated differently.
        for contract in db.scalars(select(Contract).where(Contract.end_date >= date.today())).all():
            contract_words = _words(contract.title, contract.description, contract.category)
            overlap_terms = keywords & contract_words
            human_category = (_humanise_category(contract.category) or "").lower()
            exact_unspsc = bool(contract.unspsc and contract.unspsc in prospect_unspsc)
            category_match = bool(human_category and human_category in category_labels)
            incumbent = contract.supplier_id == supplier.id

            if not incumbent and not (exact_unspsc or (category_match and overlap_terms) or len(overlap_terms) >= 3):
                continue

            agency_history = [c for c in contracts if c.agency_id and c.agency_id == contract.agency_id]
            relationship_dates = [c.start_date or c.publication_date for c in agency_history if (c.start_date or c.publication_date)]
            if relationship_dates:
                relationship_years = max(0.0, (date.today() - min(relationship_dates)).days / 365.25)
            else:
                relationship_years = 0.0

            amendments = db.scalar(
                select(func.count()).select_from(ContractVersion).where(ContractVersion.contract_id == contract.id)
            ) or 0
            relevance = 20 if exact_unspsc else 15 if category_match else min(15, len(overlap_terms) * 5)
            conf, evidence = recompete_score(
                end_date=contract.end_date,
                amendment_count=amendments,
                agency_frequency=len(agency_history),
                relationship_years=relationship_years,
                incumbent=incumbent,
                relevance=relevance,
            )
            threshold = 35 if incumbent else 45
            if conf < threshold:
                continue

            db.add(
                RecompeteScore(
                    prospect_id=prospect.id,
                    contract_id=contract.id,
                    confidence=conf,
                    evidence={
                        **evidence,
                        "Matched keywords": sorted(overlap_terms)[:8],
                        "Exact UNSPSC match": exact_unspsc,
                        "Exact category match": category_match,
                    },
                    classification="INCUMBENT RENEWAL WATCH" if incumbent else "MARKET EXPIRY WATCH — INFERRED",
                )
            )

    db.commit()
    return count

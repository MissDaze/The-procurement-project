from __future__ import annotations

from collections import Counter
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.scoring import opportunity_score, prospect_score, recompete_score
from app.models import Contract, ContractVersion, OpportunityScore, Prospect, ProspectCapability, RecompeteScore, Supplier, Tender


def rebuild_supplier_metrics(db: Session) -> None:
    for supplier in db.scalars(select(Supplier)).all():
        rows=db.scalars(select(Contract).where(Contract.supplier_id==supplier.id)).all()
        supplier.contract_count=len(rows); supplier.disclosed_value=sum((r.current_value or 0) for r in rows)
        supplier.agency_count=len({r.agency_id for r in rows if r.agency_id}); dates=[r.publication_date for r in rows if r.publication_date]
        supplier.first_award=min(dates) if dates else None; supplier.latest_award=max(dates) if dates else None
    db.commit()


def discover_prospects(db: Session) -> int:
    rebuild_supplier_metrics(db); created=0; tenders=db.scalars(select(Tender).where(Tender.status.in_(["LIVE","CLOSING SOON"]))).all()
    for supplier in db.scalars(select(Supplier).where(Supplier.contract_count>0)).all():
        if not supplier.abn or len("".join(filter(str.isdigit, supplier.abn))) != 11 or "withheld" in supplier.canonical_name.lower():
            continue
        contracts=db.scalars(select(Contract).where(Contract.supplier_id==supplier.id)).all()
        cats=[c.category for c in contracts if c.category]; cat_words={w.lower() for c in cats for w in c.split() if len(w)>4}
        live=sum(1 for t in tenders if cat_words & {w.lower() for w in ((t.title or "")+" "+(t.category or "")).split()})
        score,breakdown=prospect_score(contract_count=supplier.contract_count,disclosed_value=supplier.disclosed_value,agency_count=supplier.agency_count,latest_award=supplier.latest_award,live_market_count=live,opportunity_count=live,name=supplier.canonical_name)
        prospect=db.scalar(select(Prospect).where(Prospect.supplier_id==supplier.id))
        topcats=[x for x,_ in Counter(cats).most_common(5)]
        if not prospect:
            prospect=Prospect(supplier_id=supplier.id); db.add(prospect); db.flush(); created+=1
        prospect.score=score; prospect.score_breakdown=breakdown; prospect.main_categories=topcats; prospect.inferred_capabilities=topcats; prospect.next_action="Generate a tailored sales preview" if score>=70 else "Review procurement footprint"
        for category in topcats:
            if not db.scalar(select(ProspectCapability).where(ProspectCapability.prospect_id==prospect.id,ProspectCapability.name==category)):
                db.add(ProspectCapability(prospect_id=prospect.id,name=category,evidence="Observed in official awarded-contract category",confirmed=False))
    db.commit(); return created


def calculate_matches(db: Session) -> int:
    count=0
    for prospect in db.scalars(select(Prospect)).all():
        supplier=prospect.supplier; contracts=db.scalars(select(Contract).where(Contract.supplier_id==supplier.id)).all()
        categories=" ".join(c.category or "" for c in contracts).lower(); agencies={c.agency_id for c in contracts}
        keywords={w for w in categories.split() if len(w)>4}
        for tender in db.scalars(select(Tender).where(Tender.status.in_(["LIVE","CLOSING SOON"]))).all():
            corpus=((tender.title or "")+" "+(tender.description or "")+" "+(tender.category or "")).lower(); overlap=len(keywords & set(corpus.split()))
            total,label,parts=opportunity_score(capability=min(30,overlap*10),category=15 if tender.category and tender.category.lower() in categories else min(15,overlap*5),agency=15 if tender.agency_id in agencies else 5,historical=min(15,len(contracts)*2),location=10,closes_at=tender.closes_at,competitive=3)
            if total<25: continue
            item=db.scalar(select(OpportunityScore).where(OpportunityScore.prospect_id==prospect.id,OpportunityScore.tender_id==tender.id))
            if not item: item=OpportunityScore(prospect_id=prospect.id,tender_id=tender.id,score=total,classification=label,breakdown=parts); db.add(item); count+=1
            else: item.score=total; item.classification=label; item.breakdown=parts
        for contract in db.scalars(select(Contract).where(Contract.end_date>=date.today())).all():
            if not (contract.category and any(k in contract.category.lower() for k in keywords)): continue
            amendments=db.scalar(select(func.count()).select_from(ContractVersion).where(ContractVersion.contract_id==contract.id)) or 0
            conf,evidence=recompete_score(end_date=contract.end_date,amendment_count=amendments,agency_frequency=len([c for c in contracts if c.agency_id==contract.agency_id]),relationship_years=2)
            if conf<35: continue
            rec=db.scalar(select(RecompeteScore).where(RecompeteScore.prospect_id==prospect.id,RecompeteScore.contract_id==contract.id))
            if not rec: db.add(RecompeteScore(prospect_id=prospect.id,contract_id=contract.id,confidence=conf,evidence=evidence))
    db.commit(); return count

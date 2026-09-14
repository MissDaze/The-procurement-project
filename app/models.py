from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class AdminUser(Base, TimestampMixin):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Agency(Base, TimestampMixin):
    __tablename__ = "agencies"
    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    contract_count: Mapped[int] = mapped_column(Integer, default=0)
    supplier_count: Mapped[int] = mapped_column(Integer, default=0)
    disclosed_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    legal_name: Mapped[str | None] = mapped_column(String(300))
    abn: Mapped[str | None] = mapped_column(String(11), index=True)
    state: Mapped[str | None] = mapped_column(String(30))
    entity_type: Mapped[str | None] = mapped_column(String(100))
    contract_count: Mapped[int] = mapped_column(Integer, default=0)
    agency_count: Mapped[int] = mapped_column(Integer, default=0)
    disclosed_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    first_award: Mapped[date | None] = mapped_column(Date)
    latest_award: Mapped[date | None] = mapped_column(Date)


class Category(Base, TimestampMixin):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(300))


class Tender(Base, TimestampMixin):
    __tablename__ = "tenders"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_tender_source_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(100), index=True)
    source_id: Mapped[str] = mapped_column(String(150), index=True)
    title: Mapped[str] = mapped_column(String(800))
    description: Mapped[str | None] = mapped_column(Text)
    agency_id: Mapped[int | None] = mapped_column(ForeignKey("agencies.id"))
    agency_name: Mapped[str | None] = mapped_column(String(300), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    category: Mapped[str | None] = mapped_column(String(500))
    unspsc: Mapped[str | None] = mapped_column(String(30), index=True)
    location: Mapped[str | None] = mapped_column(String(100))
    tender_type: Mapped[str | None] = mapped_column(String(120))
    procurement_method: Mapped[str | None] = mapped_column(String(120))
    contact_details: Mapped[dict | None] = mapped_column(JSON)
    document_links: Mapped[list | None] = mapped_column(JSON)
    source_url: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(30), index=True)
    raw_hash: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    agency: Mapped[Agency | None] = relationship()


class TenderVersion(Base):
    __tablename__ = "tender_versions"
    __table_args__ = (UniqueConstraint("tender_id", "raw_hash", name="uq_tender_version_hash"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"))
    raw_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Contract(Base, TimestampMixin):
    __tablename__ = "contracts"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_contract_source_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(100))
    source_id: Mapped[str] = mapped_column(String(150))
    title: Mapped[str] = mapped_column(String(800))
    description: Mapped[str | None] = mapped_column(Text)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    agency_id: Mapped[int | None] = mapped_column(ForeignKey("agencies.id"))
    original_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    current_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    publication_date: Mapped[date | None] = mapped_column(Date)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, index=True)
    category: Mapped[str | None] = mapped_column(String(500))
    unspsc: Mapped[str | None] = mapped_column(String(30))
    procurement_method: Mapped[str | None] = mapped_column(String(120))
    panel_information: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(1000))
    raw_hash: Mapped[str] = mapped_column(String(64))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    supplier: Mapped[Supplier | None] = relationship()
    agency: Mapped[Agency | None] = relationship()


class ContractVersion(Base):
    __tablename__ = "contract_versions"
    __table_args__ = (UniqueConstraint("contract_id", "raw_hash", name="uq_contract_version_hash"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"))
    raw_hash: Mapped[str] = mapped_column(String(64))
    previous_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    previous_end_date: Mapped[date | None] = mapped_column(Date)
    snapshot: Mapped[dict] = mapped_column(JSON)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Prospect(Base, TimestampMixin):
    __tablename__ = "prospects"
    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), unique=True)
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    lifecycle_stage: Mapped[str] = mapped_column(String(40), default="New Prospect", index=True)
    main_categories: Mapped[list] = mapped_column(JSON, default=list)
    inferred_capabilities: Mapped[list] = mapped_column(JSON, default=list)
    last_contact: Mapped[date | None] = mapped_column(Date)
    follow_up_date: Mapped[date | None] = mapped_column(Date)
    next_action: Mapped[str | None] = mapped_column(String(500))
    supplier: Mapped[Supplier] = relationship()


class ProspectCapability(Base, TimestampMixin):
    __tablename__ = "prospect_capabilities"
    __table_args__ = (UniqueConstraint("prospect_id", "name", name="uq_prospect_capability"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    evidence: Mapped[str | None] = mapped_column(Text)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


class ProspectContact(Base, TimestampMixin):
    __tablename__ = "prospect_contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"))
    name: Mapped[str | None] = mapped_column(String(200))
    job_title: Mapped[str | None] = mapped_column(String(200))
    business_email: Mapped[str | None] = mapped_column(String(320))
    business_phone: Mapped[str | None] = mapped_column(String(100))
    profile_url: Mapped[str | None] = mapped_column(String(1000))


class ProspectNote(Base, TimestampMixin):
    __tablename__ = "prospect_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"))
    note: Mapped[str] = mapped_column(Text)


class OutreachActivity(Base, TimestampMixin):
    __tablename__ = "outreach_activity"
    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"))
    contact_date: Mapped[date] = mapped_column(Date, default=date.today)
    method: Mapped[str] = mapped_column(String(100))
    outcome: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    follow_up_date: Mapped[date | None] = mapped_column(Date)


class ClientProfile(Base, TimestampMixin):
    __tablename__ = "client_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"), unique=True)
    company_name: Mapped[str] = mapped_column(String(300))
    website: Mapped[str | None] = mapped_column(String(1000))
    abn: Mapped[str | None] = mapped_column(String(11))
    industry: Mapped[str | None] = mapped_column(String(200))
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    excluded_keywords: Mapped[list] = mapped_column(JSON, default=list)
    preferred_agencies: Mapped[list] = mapped_column(JSON, default=list)
    preferred_locations: Mapped[list] = mapped_column(JSON, default=list)
    min_contract_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    max_contract_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    known_competitors: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    logo_path: Mapped[str | None] = mapped_column(String(1000))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Competitor(Base, TimestampMixin):
    __tablename__ = "competitors"
    __table_args__ = (UniqueConstraint("prospect_id", "supplier_id", name="uq_competitor_pair"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"))
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    overlap_score: Mapped[int] = mapped_column(Integer, default=0)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class OpportunityScore(Base, TimestampMixin):
    __tablename__ = "opportunity_scores"
    __table_args__ = (UniqueConstraint("prospect_id", "tender_id", name="uq_opportunity_score"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"))
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"))
    score: Mapped[int] = mapped_column(Integer)
    classification: Mapped[str] = mapped_column(String(20))
    breakdown: Mapped[dict] = mapped_column(JSON)


class RecompeteScore(Base, TimestampMixin):
    __tablename__ = "recompete_scores"
    __table_args__ = (UniqueConstraint("prospect_id", "contract_id", name="uq_recompete_score"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"))
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id"))
    confidence: Mapped[int] = mapped_column(Integer)
    evidence: Mapped[dict] = mapped_column(JSON)
    classification: Mapped[str] = mapped_column(String(50), default="LIKELY RECOMPETE — INFERRED")


class ReportRun(Base, TimestampMixin):
    __tablename__ = "report_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[str] = mapped_column(String(80), unique=True)
    prospect_id: Mapped[int | None] = mapped_column(ForeignKey("prospects.id"))
    client_id: Mapped[int | None] = mapped_column(ForeignKey("client_profiles.id"))
    report_type: Mapped[str] = mapped_column(String(100))
    file_path: Mapped[str] = mapped_column(String(1000))
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="completed")


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(100), index=True)
    collection_type: Mapped[str] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="running")
    checked: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    latest_error: Mapped[str | None] = mapped_column(Text)


class SavedSearch(Base, TimestampMixin):
    __tablename__ = "saved_searches"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    entity_type: Mapped[str] = mapped_column(String(50))
    criteria: Mapped[dict] = mapped_column(JSON)


class SystemSetting(Base, TimestampMixin):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(150), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class RejectedRecord(Base):
    __tablename__ = "rejected_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

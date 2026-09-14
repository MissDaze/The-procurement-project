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
    job_title: Mapped[str | None] = mappeÛN-¢G§²ÚîÆ­yÙØÛÛ[[Šš[X\WÚÙ^OUYJBˆ›ÜÜXİÚYˆX\YÚ[HHX\YØÛÛ[[Š›Ü™ZYÛ’Ù^Jœ›ÜÜXİËšY‹Û™[]OHĞTĞĞQHŠJBˆÛÛXİÙ]NˆX\YÙ]WHHX\YØÛÛ[[Š]KY˜][Y]KÙ^JBˆY]ÙˆX\YÜİ—HHX\YØÛÛ[[Šİš[™ÊL
JBˆİ]ÛÛYNˆX\YÜİˆ›Û™WHHX\YØÛÛ[[Šİš[™ÊÌ
JBˆ›İ\ÎˆX\YÜİˆ›Û™WHHX\YØÛÛ[[Š^
Bˆ›Ûİ×İ\Ù]NˆX\YÙ]H›Û™WHHX\YØÛÛ[[Š]JB‚‚˜Û\ÜÈÛY[›Ùš[J˜\ÙK[Y\İ[\Z^[ŠN‚ˆ×İX›[˜[YW×ÈH˜ÛY[Ü›Ùš[\È‚ˆYˆX\YÚ[HHX\YØÛÛ[[Šš[X\WÚÙ^OUYJBˆ›ÜÜXİÚYˆX\YÚ[HHX\YØÛÛ[[Š›Ü™ZYÛ’Ù^Jœ›ÜÜXİËšYŠK[š\]YOUYJBˆÛÛ\[WÛ˜[YNˆX\YÜİ—HHX\YØÛÛ[[Šİš[™ÊÌ
JBˆÙXœÚ]NˆX\YÜİˆ›Û™WHHX\YØÛÛ[[Šİš[™ÊL
JBˆX›ˆX\YÜİˆ›Û™WHHX\YØÛÛ[[Šİš[™ÊLJJBˆ[™\İNˆX\YÜİˆ›Û™WHHX\YØÛÛ[[Šİš[™ÊŒ
JBˆØ\Xš[]Y\ÎˆX\YÛ\İHHX\YØÛÛ[[Š”ÓÓ‹Y˜][[\İ
BˆÙ^]ÛÜ™ÎˆX\YÛ\İHHX\YØÛÛ[[Š”ÓÓ‹Y˜][[\İ
Bˆ^ÛYYÚÙ^]ÛÜ™ÎˆX\YÛ\İHHX\YØÛÛ[[Š”ÓÓ‹Y˜][[\İ
Bˆ™Y™\œ™YØYÙ[˜ÚY\ÎˆX\YÛ\İHHX\YØÛÛ[[Š”ÓÓ‹Y˜][[\İ
Bˆ™Y™\œ™YÛØØ][ÛœÎˆX\YÛ\İHHX\YØÛÛ[[Š”ÓÓ‹Y˜][[\İ
BˆZ[—ØÛÛ˜Xİİ˜[YNˆX\YÑXÚ[X[›Û™WHHX\YØÛÛ[[Š[Y\šXÊNŠJBˆX^ØÛÛ˜Xİİ˜[YNˆX\YÑXÚ[X[›Û™WHHX\YØÛÛ[[Š[Y\šXÊNŠJBˆÛ›İÛ—ØÛÛ\]]ÜœÎˆX\YÛ\İHHX\YØÛÛ[[Š”ÓÓ‹Y˜][[\İ
Bˆ›İ\ÎˆX\YÜİˆ›Û™WHHX\YØÛÛ[[Š^
BˆÙÛ×Ü]ˆX\YÜİˆ›Û™WHHX\YØÛÛ[[Šİš[™ÊL
JBˆXİ]™NˆX\YØ›ÛÛHHX\YØÛÛ[[Š›ÛÛX[‹Y˜][UYJB‚‚˜Û\ÜÈÛÛ\]]ÜŠ˜\ÙK[Y\İ[\Z^[ŠN‚ˆ×İX›[˜[YW×ÈH˜ÛÛ\]]ÜœÈ‚ˆ×İX›WØ\™Ü××ÈH
[š\]YPÛÛœİ˜Z[
œ›ÜÜXİÚY‹œİ\Y\—ÚY‹˜[YOH\WØÛÛ\]]Ü—ÜZ\ˆŠK
BˆYˆX\YÚ[HHX\YØÛÛ[[Šš[X\WÚÙ^OUYJBˆ›ÜÜXİÚYˆX\YÚ[HHX\YØÛÛ[[Š›Ü™ZYÛ’Ù^Jœ›ÜÜXİËšYŠJBˆİ\Y\—ÚYˆX\YÚ[HHX\YØÛÛ[[Š›Ü™ZYÛ’Ù^Jœİ\Y\œËšYŠJBˆİ™\›\ÜØÛÜ™NˆX\YÚ[HHX\YØÛÛ[[Š[YÙ\‹Y˜][L
Bˆ]šY[˜ÙNˆX\YÙXİHHX\YØÛÛ[[Š”ÓÓ‹Y˜][YXİ
B‚‚˜Û\ÜÈÜÜ[š]TØÛÜ™J˜\ÙK[Y\İ[\Z^[ŠN‚ˆ×İX›[˜[YW×ÈH›ÜÜ[š]WÜØÛÜ™\È‚ˆ×İX›WØ\™Ü××ÈH
[š\]YPÛÛœİ˜Z[
œ›ÜÜXİÚY‹[™\—ÚY‹˜[YOH\WÛÜÜ[š]WÜØÛÜ™HŠK
BˆYˆX\YÚ[HHX\YØÛÛ[[Šš[X\WÚÙ^OUYJBˆ›ÜÜXİÚYˆX\YÚ[HHX\YØÛÛ[[Š›Ü™ZYÛ’Ù^Jœ›ÜÜXİËšYŠJBˆ[™\—ÚYˆX\YÚ[HHX\YØÛÛ[[Š›Ü™ZYÛ’Ù^J[™\œËšYŠJBˆØÛÜ™NˆX\YÚ[HHX\YØÛÛ[[Š[YÙ\ŠBˆÛ\ÜÚYšXØ][ÛˆX\YÜİ—HHX\YØÛÛ[[Šİš[™ÊŒ
JBˆœ™XZÙİÛˆX\YÙXİHHX\YØÛÛ[[Š”ÓÓŠB‚‚˜Û\ÜÈ™XÛÛ\]TØÛÜ™J˜\ÙK[Y\İ[\Z^[ŠN‚ˆ×İX›[˜[YW×ÈHœ™XÛÛ\]WÜØÛÜ™\È‚ˆ×İX›WØ\™Ü××ÈH
[š\]YPÛÛœİ˜Z[
œ›ÜÜXİÚY‹˜ÛÛ˜XİÚY‹˜[YOH\WÜ™XÛÛ\]WÜØÛÜ™HŠK
BˆYˆX\YÚ[HHX\YØÛÛ[[Šš[X\WÚÙ^OUYJBˆ›ÜÜXİÚYˆX\YÚ[HHX\YØÛÛ[[Š›Ü™ZYÛ’Ù^Jœ›ÜÜXİËšYŠJBˆÛÛ˜XİÚYˆX\YÚ[HHX\YØÛÛ[[Š›Ü™ZYÛ’Ù^J˜ÛÛ˜XİËšYŠJBˆÛÛ™šY[˜ÙNˆX\YÚ[HHX\YØÛÛ[[Š[YÙ\ŠBˆ]šY[˜ÙNˆX\YÙXİHHX\YØÛÛ[[Š”ÓÓŠBˆÛ\ÜÚYšXØ][ÛˆX\YÜİ—HHX\YØÛÛ[[Šİš[™ÊL
KY˜][H“RÑSH‘PÓÓTUH8 %S‘‘T”‘QŠB‚‚˜Û\ÜÈ™\Ü[Š˜\ÙK[Y\İ[\Z^[ŠN‚ˆ×İX›[˜[YW×ÈHœ™\ÜÜ[œÈ‚ˆYˆX\YÚ[HHX\YØÛÛ[[Šš[X\WÚÙ^OUYJBˆ™\ÜÚYˆX\YÜİ—HHX\YØÛÛ[[Šİš[™Ê
K[š\]YOUYJBˆ›ÜÜXİÚYˆX\YÚ[›Û™WHHX\YØÛÛ[[Š›Ü™ZYÛ’Ù^Jœ›ÜÜXİËšYŠJBˆÛY[ÚYˆX\YÚ[›Û™WHHX\YØÛÛ[[Š›Ü™ZYÛ’Ù^J˜ÛY[Ü›Ùš[\ËšYŠJBˆ™\Üİ\NˆX\YÜİ—HHX\YØÛÛ[[Šİš[™ÊL
JBˆš[WÜ]ˆX\YÜİ—HHX\YØÛÛ[[Šİš[™ÊL
JBˆ\˜[Y]\œÎˆX\YÙXİHHX\YØÛÛ[[Š”ÓÓ‹Y˜][YXİ
Bˆİ]\ÎˆX\YÜİ—HHX\YØÛÛ[[Šİš[™ÊÌ
KY˜][H˜ÛÛ\]YŠB‚‚˜Û\ÜÈÛÛXİ[Û”[Š˜\ÙJN‚ˆ×İX›[˜[YW×ÈH˜ÛÛXİ[Û—Ü[œÈ‚ˆYˆX\YÚ[HHX\YØÛÛ[[Šš[X\WÚÙ^OUYJBˆÛİ\˜ÙNˆX\YÜİ—HHX\YØÛÛ[[Šİš[™ÊL
K[™^UYJBˆÛÛXİ[Û—İ\NˆX\YÜİ—HHX\YØÛÛ[[Šİš[™Ê
JBˆİ\YØ]ˆX\YÙ]][YWHHX\YØÛÛ[[Š]U[YJ[Y^›Û™OUYJKY˜][[›İÊBˆš[š\ÚYØ]ˆX\YÙ]][YH›Û™WHHX\YØÛÛ[[Š]U[YJ[Y^›Û™OUYJJBˆİ]\ÎˆX\YÜİ—HHX\YØÛÛ[[Šİš[™ÊÌ
KY˜][Hœ[›š[™ÈŠBˆÚXÚÙYˆX\YÚ[HHX\YØÛÛ[[Š[YÙ\‹Y˜][L
BˆÜ™X]YˆX\YÚ[HHX\YØÛÛ[[Š[YÙ\‹Y˜][L
Bˆ\]YˆX\YÚ[HHX\YØÛÛ[[Š[YÙ\‹Y˜][L
Bˆ\œ›ÜœÎˆX\YÚ[HHX\YØÛÛ[[Š[YÙ\‹Y˜][L
Bˆ]\İÙ\œ›ÜˆX\YÜİˆ›Û™WHHX\YØÛÛ[[Š^
B‚‚˜Û\ÜÈØ]™YÙX\˜Ú
˜\ÙK[Y\İ[\Z^[ŠN‚ˆ×İX›[˜[YW×ÈHœØ]™YÜÙX\˜Ú\È‚ˆYˆX\YÚ[HHX\YØÛÛ[[Šš[X\WÚÙ^OUYJBˆ˜[YNˆX\YÜİ—HHX\YØÛÛ[[Šİš[™ÊŒ
JBˆ[]Wİ\NˆX\YÜİ—HHX\YØÛÛ[[Šİš[™ÊL
JBˆÜš]\šXNˆX\YÙXİHHX\YØÛÛ[[Š”ÓÓŠB‚‚˜Û\ÜÈŞ\İ[TÙ][™Ê˜\ÙK[Y\İ[\Z^[ŠN‚ˆ×İX›[˜[YW×ÈHœŞ\İ[WÜÙ][™ÜÈ‚ˆÙ^NˆX\YÜİ—HHX\YØÛÛ[[Šİš[™ÊML
Kš[X\WÚÙ^OUYJBˆ˜[YNˆX\YÙXİHHX\YØÛÛ[[Š”ÓÓŠB‚‚˜Û\ÜÈ™Z™XİY™XÛÜ™
˜\ÙJN‚ˆ×İX›[˜[YW×ÈHœ™Z™XİYÜ™XÛÜ™È‚ˆYˆX\YÚ[HHX\YØÛÛ[[Šš[X\WÚÙ^OUYJBˆÛİ\˜ÙNˆX\YÜİ—HHX\YØÛÛ[[Šİš[™ÊL
JBˆ™X\ÛÛˆX\YÜİ—HHX\YØÛÛ[[Š^
Bˆ^[ØYˆX\YÙXİHHX\YØÛÛ[[Š”ÓÓŠBˆÜ™X]YØ]ˆX\YÙ]][YWHHX\YØÛÛ[[Š]U[YJ[Y^›Û™OUYJKY˜][[›İÊB
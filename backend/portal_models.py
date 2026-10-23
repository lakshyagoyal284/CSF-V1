"""SQLAlchemy models for the AgriTrust AI dual-portal workflow.

The reviewed PostgreSQL DDL lives in ``db/agritrust_schema.sql`` and is the
source of truth for a deployment. These models mirror it closely enough to run
on both PostgreSQL and SQLite, which keeps the hackathon demo zero-config while
still shipping a production schema.

Portability choices:

* UUID primary keys are stored as ``VARCHAR(36)`` and generated in Python. This
  avoids dialect-specific UUID types and makes the same rows portable.
* Enum-like columns are ``VARCHAR`` + ``CHECK`` constraints in PostgreSQL. The
  ORM uses plain strings, and the API validates the allowed values with
  Literal types, so an invalid state cannot be written by the service.
* JSONB is used on PostgreSQL and JSON elsewhere, selected by dialect.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

#: JSONB on PostgreSQL, plain JSON on SQLite.
JsonColumn = JSON().with_variant(JSONB(), "postgresql")


def new_id() -> str:
    """Opaque public identifier for every portal record."""
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """Timezone-aware UTC timestamp (SQLite stores it without an offset)."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base shared by the portal tables."""


class User(Base):
    """``users`` - a borrower or a bank officer."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # borrower | banker
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    applications: Mapped[list["Application"]] = relationship(
        back_populates="borrower",
        cascade="all, delete-orphan",
        foreign_keys="Application.borrower_id",
    )

    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        Index("idx_users_role", "role"),
    )

    @property
    def is_banker(self) -> bool:
        return self.role == "banker"


class Application(Base):
    """``applications`` - the underwriting file and its workflow state."""

    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    borrower_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)

    # Applicant identity snapshot (see the DDL comment: point-in-time record).
    applicant_name: Mapped[str] = mapped_column(String(160), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    applicant_category: Mapped[str] = mapped_column(String(24), nullable=False)
    region_tier: Mapped[str] = mapped_column(String(24), default="rural", nullable=False)
    age: Mapped[int | None] = mapped_column()
    dependents: Mapped[int | None] = mapped_column()
    years_in_operation: Mapped[int | None] = mapped_column()
    crop_type: Mapped[str | None] = mapped_column(String(24), default="not_applicable")

    requested_loan_amount: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    loan_tenure_months: Mapped[int] = mapped_column(default=36, nullable=False)
    outstanding_debt: Mapped[float] = mapped_column(Numeric(16, 2), default=0, nullable=False)
    monthly_emi_obligation: Mapped[float] = mapped_column(
        Numeric(16, 2), default=0, nullable=False
    )

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    borrower: Mapped[User] = relationship(
        back_populates="applications", foreign_keys=[borrower_id]
    )
    assets: Mapped["AssetDeclaration | None"] = relationship(
        back_populates="application", cascade="all, delete-orphan", uselist=False
    )
    documents: Mapped[list["DocumentProof"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )
    scores: Mapped[list["CreditScoreResult"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="CreditScoreResult.generated_at.desc()",
    )

    __table_args__ = (
        Index("idx_applications_status_submitted", "status", "submitted_at"),
        Index("idx_applications_borrower", "borrower_id", "submitted_at"),
    )

    @property
    def latest_score(self) -> "CreditScoreResult | None":
        return self.scores[0] if self.scores else None


class AssetDeclaration(Base):
    """``asset_declarations`` - declared collateral and cash-flow proxies.

    Product-facing names (``land_acres``, ``shop_inventory_val`` ...) are kept
    here; the mapping to the model's internal feature names happens in exactly
    one place, :func:`backend.portal_service.to_score_request`.
    """

    __tablename__ = "asset_declarations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # farming collateral
    land_acres: Mapped[float | None] = mapped_column(Numeric(10, 2))
    soil_quality: Mapped[int | None] = mapped_column()
    crop_yield_val: Mapped[float | None] = mapped_column(Numeric(16, 2))
    land_market_value: Mapped[float | None] = mapped_column(Numeric(16, 2))
    irrigation_score: Mapped[int | None] = mapped_column()
    livestock_val: Mapped[float | None] = mapped_column(Numeric(16, 2))

    # business collateral
    shop_inventory_val: Mapped[float | None] = mapped_column(Numeric(16, 2))
    shop_area_sqft: Mapped[float | None] = mapped_column(Numeric(10, 2))
    shop_ownership: Mapped[str] = mapped_column(
        String(24), default="not_applicable", nullable=False
    )

    # shared collateral
    property_val: Mapped[float | None] = mapped_column(Numeric(16, 2))
    equipment_val: Mapped[float | None] = mapped_column(Numeric(16, 2))

    # alternative cash-flow proxies
    utility_score: Mapped[int | None] = mapped_column()
    utility_delays_12m: Mapped[int | None] = mapped_column()
    upi_volume: Mapped[float | None] = mapped_column(Numeric(16, 2))
    avg_bank_balance: Mapped[float | None] = mapped_column(Numeric(16, 2))
    annual_income_proxy: Mapped[float | None] = mapped_column(Numeric(16, 2))

    verified_fields: Mapped[list[str]] = mapped_column(
        JsonColumn, default=list, nullable=False
    )
    verified_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    application: Mapped[Application] = relationship(back_populates="assets")

    @property
    def declared_total_asset_value(self) -> float:
        """Database-generated in PostgreSQL; computed here for SQLite parity."""
        values = (
            self.land_market_value,
            self.property_val,
            self.equipment_val,
            self.livestock_val,
            self.shop_inventory_val,
        )
        return float(sum(float(value) for value in values if value is not None))


class DocumentProof(Base):
    """``document_proofs`` - metadata for a proof held in object storage."""

    __tablename__ = "document_proofs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    doc_type: Mapped[str] = mapped_column(String(40), nullable=False)
    file_url: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(160), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    verified_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    verified_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    application: Mapped[Application] = relationship(back_populates="documents")

    __table_args__ = (
        Index("idx_document_proofs_app", "application_id", "uploaded_at"),
    )


class CreditScoreResult(Base):
    """``credit_score_results`` - append-only model decision with SHAP reasons."""

    __tablename__ = "credit_score_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    credit_score: Mapped[int] = mapped_column(nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(8), nullable=False)
    recommended_limit: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    shap_explanations_json: Mapped[dict[str, Any]] = mapped_column(
        JsonColumn, nullable=False
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    probability_of_default: Mapped[float | None] = mapped_column(Numeric(7, 6))
    decision: Mapped[str] = mapped_column(String(32), default="MANUAL_REVIEW", nullable=False)
    score_band: Mapped[dict[str, Any]] = mapped_column(
        JsonColumn, default=dict, nullable=False
    )
    data_quality: Mapped[dict[str, Any]] = mapped_column(
        JsonColumn, default=dict, nullable=False
    )
    policy_breakdown: Mapped[dict[str, Any]] = mapped_column(
        JsonColumn, default=dict, nullable=False
    )
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    officer_notes: Mapped[str | None] = mapped_column(Text)
    decline_option: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decline_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    application: Mapped[Application] = relationship(back_populates="scores")

    __table_args__ = (
        Index("idx_credit_score_results_app", "application_id", "generated_at"),
        Index("idx_credit_score_results_tier", "risk_tier"),
    )


class AuditLog(Base):
    """``underwriting_audit_log`` - immutable workflow trail."""

    __tablename__ = "underwriting_audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("applications.id", ondelete="CASCADE")
    )
    actor_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    actor_role: Mapped[str | None] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JsonColumn, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        Index("idx_audit_application", "application_id", "created_at"),
    )

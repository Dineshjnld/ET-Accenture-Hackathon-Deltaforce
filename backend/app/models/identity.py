"""Identity and organisational hierarchy."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.core import TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from app.db import Base


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    entity_type: Mapped[str] = mapped_column(
        String(80), nullable=False
    )  # e.g. scheduled_commercial_bank, small_finance_bank, nbfc
    jurisdiction: Mapped[str] = mapped_column(String(80), default="IN")
    total_assets_inr_crore: Mapped[int | None] = mapped_column(Integer, nullable=True)
    banking_outlets_in_india: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_internal_ombudsman: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    rbi_registration_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    business_units: Mapped[list["BusinessUnit"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class BusinessUnit(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "business_units"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="business_units")

    __table_args__ = (UniqueConstraint("organization_id", "code", name="uq_business_unit_code"),)


class Role(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    permissions: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list

    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role_id: Mapped[str | None] = mapped_column(ForeignKey("roles.id", ondelete="SET NULL"), nullable=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    role: Mapped[Role | None] = relationship(back_populates="users")


__all__ = ["Organization", "BusinessUnit", "Role", "User"]

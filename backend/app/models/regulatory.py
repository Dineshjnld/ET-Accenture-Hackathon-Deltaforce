"""Regulatory catalogue, documents, versions and text chunks."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, PortableVector
from app.models.core import (
    DocumentSourceKind,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    utcnow,
)


class RegulatorySource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "regulatory_sources"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    authority: Mapped[str] = mapped_column(String(160), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(80), default="IN")
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_type: Mapped[str] = mapped_column(String(60), default="master_direction")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieval_method: Mapped[str | None] = mapped_column(String(120), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    regulations: Mapped[list["Regulation"]] = relationship(back_populates="source")


class Regulation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "regulations"

    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("regulatory_sources.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    regulator: Mapped[str] = mapped_column(String(160), default="Reserve Bank of India")
    jurisdiction: Mapped[str] = mapped_column(String(80), default="IN")
    applicability_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active")

    source: Mapped[RegulatorySource] = relationship(back_populates="regulations")
    versions: Mapped[list["RegulationVersion"]] = relationship(
        back_populates="regulation", cascade="all, delete-orphan", order_by="RegulationVersion.published_at"
    )


class RegulationVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "regulation_versions"

    regulation_id: Mapped[str] = mapped_column(ForeignKey("regulations.id", ondelete="CASCADE"), index=True)
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    supersedes_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("regulation_versions.id", ondelete="SET NULL"), nullable=True
    )
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)

    regulation: Mapped[Regulation] = relationship(back_populates="versions")
    document: Mapped["Document | None"] = relationship(
        back_populates="regulation_versions", foreign_keys=[document_id]
    )

    __table_args__ = (
        UniqueConstraint("regulation_id", "version_label", name="uq_regulation_version_label"),
        Index("ix_regulation_versions_effective", "effective_from", "effective_to"),
    )


class Document(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "documents"

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source_kind: Mapped[DocumentSourceKind] = mapped_column(String(30), default=DocumentSourceKind.INTERNAL)
    source_kind_label: Mapped[str] = mapped_column(String(160), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    jurisdiction: Mapped[str | None] = mapped_column(String(80), nullable=True)
    document_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reference_number: Mapped[str | None] = mapped_column(String(160), nullable=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)
    business_unit_id: Mapped[str | None] = mapped_column(ForeignKey("business_units.id", ondelete="SET NULL"), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_length: Mapped[int] = mapped_column(Integer, default=0)
    extraction_status: Mapped[str] = mapped_column(String(40), default="pending")
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False)
    parse_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentVersion.created_at"
    )
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentChunk.chunk_index"
    )
    regulation_versions: Mapped[list[RegulationVersion]] = relationship(
        back_populates="document", foreign_keys=[RegulationVersion.document_id]
    )

    __table_args__ = (
        Index("ix_documents_source_kind", "source_kind"),
        Index("ix_documents_published", "published_at"),
    )


class DocumentVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "document_versions"

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    text_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    text_length: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(160), nullable=True)

    document: Mapped[Document] = relationship(back_populates="versions")
    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="document_version")

    __table_args__ = (UniqueConstraint("document_id", "version_label", name="uq_document_version_label"),)


class DocumentChunk(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "document_chunks"

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    document_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    heading: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(PortableVector, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(160), nullable=True)

    document: Mapped[Document] = relationship(back_populates="chunks")
    document_version: Mapped[DocumentVersion | None] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunk_index"),
        Index("ix_chunks_section", "section"),
    )


__all__ = [
    "RegulatorySource",
    "Regulation",
    "RegulationVersion",
    "Document",
    "DocumentVersion",
    "DocumentChunk",
]

"""Tables of the cabinets (PRD section 10): the teacher's overrides and comments, the
administrator's notifications and backups."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Notification kinds.
NOTIFY_SERVICE_DOWN = "service_down"
NOTIFY_PROVIDER_UNAVAILABLE = "provider_unavailable"
NOTIFY_BACKUP_FAILED = "backup_failed"

# Backup lifecycle: requested by the API → picked up by the backup service → done / failed.
BACKUP_REQUESTED = "requested"
BACKUP_RUNNING = "running"
BACKUP_DONE = "done"
BACKUP_FAILED = "failed"
BACKUP_MANUAL = "manual"
BACKUP_SCHEDULED = "scheduled"


class EvaluationOverride(Base):
    __tablename__ = "evaluation_overrides"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attempts.id", ondelete="CASCADE"), nullable=False
    )
    old_total: Mapped[float] = mapped_column(Float, nullable=False)
    new_total: Mapped[float] = mapped_column(Float, nullable=False)
    old_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    new_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    teacher_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attempts.id", ondelete="CASCADE")
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("training_sessions.id", ondelete="CASCADE")
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AdminNotification(Base):
    __tablename__ = "admin_notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # What the alert is about (service name, provider name): one open alert per source.
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class Backup(Base):
    __tablename__ = "backups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default=BACKUP_MANUAL)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=BACKUP_REQUESTED)
    file_name: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

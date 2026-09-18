"""Skill ratings (PRD 9.7): one row per trainee × incident group × mode, updated after
every evaluation by the Elo-like rule in ``app.domain.analytics.rating``."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SkillRating(Base):
    __tablename__ = "skill_ratings"

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Code of ``incident_groups`` (first segment of the incident type code).
    incident_group: Mapped[str] = mapped_column(String(8), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), primary_key=True)
    rating: Mapped[float] = mapped_column(Float, nullable=False)
    # Evaluated attempts that went into the rating.
    n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

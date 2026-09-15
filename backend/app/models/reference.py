"""Reference tables filled from the organizers' dataset (wave 1).

Codes are natural primary keys: the evaluation engines and scenario bodies refer to them,
and the import is idempotent (upsert by code).
"""

import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Service(Base):
    __tablename__ = "services"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    short_title: Mapped[str] = mapped_column(String(64), nullable=False)
    # Service 103 never sets «Не принята» / «Отказ»: «Работы завершены: без бригады» instead.
    no_reject: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Receives cards on АРМ-112 (the trainer's audience) rather than via an integrated system.
    via_arm112: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class IncidentGroup(Base):
    __tablename__ = "incident_groups"

    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_row: Mapped[int | None] = mapped_column(Integer)


class IncidentType(Base):
    __tablename__ = "incident_types"

    code: Mapped[str] = mapped_column(String(24), primary_key=True)
    group_code: Mapped[str] = mapped_column(
        String(8), ForeignKey("incident_groups.code", ondelete="CASCADE"), nullable=False
    )
    stat_group: Mapped[str | None] = mapped_column(String(200))
    sign1: Mapped[str] = mapped_column(String(200), nullable=False)
    sign2: Mapped[str | None] = mapped_column(String(200))
    sign3: Mapped[str | None] = mapped_column(String(200))
    hints: Mapped[str | None] = mapped_column(Text)
    final_title: Mapped[str] = mapped_column(String(300), nullable=False)
    ekp_title: Mapped[str | None] = mapped_column(String(300))
    main_service: Mapped[str | None] = mapped_column(String(32))
    main_service_raw: Mapped[str | None] = mapped_column(String(64))
    service_rules: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    flag_codes: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False, default=list)
    required_topics: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, default=list
    )
    source_row: Mapped[int | None] = mapped_column(Integer)


class IncidentFlag(Base):
    __tablename__ = "incident_flags"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    column_hint: Mapped[str | None] = mapped_column(String(200))
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ResponseStatus(Base):
    __tablename__ = "response_statuses"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_comment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_order_number: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allowed_next: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False, default=list)
    description: Mapped[str | None] = mapped_column(Text)
    memo_page: Mapped[int | None] = mapped_column(Integer)


class CardStatus(Base):
    __tablename__ = "card_statuses"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    is_alert: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)


class RejectReason(Base):
    __tablename__ = "reject_reasons"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)


class TypicalError(Base):
    __tablename__ = "typical_errors"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    penalty: Mapped[int] = mapped_column(Integer, nullable=False)
    memo_ref: Mapped[str | None] = mapped_column(String(200))
    example: Mapped[str | None] = mapped_column(Text)


class CallerTopic(Base):
    __tablename__ = "caller_topics"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False, default=list)
    order: Mapped[int] = mapped_column(Integer, nullable=False)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_no: Mapped[int] = mapped_column(Integer, nullable=False)
    item_no: Mapped[int] = mapped_column(Integer, nullable=False)
    situation: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    ocr_confident: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    traps: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False, default=list)


class Street(Base):
    __tablename__ = "streets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Lower-case, «ё» → «е», single spaces: what the prefix search compares against.
    name_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    okrug: Mapped[str] = mapped_column(String(16), nullable=False)
    district: Mapped[str] = mapped_column(String(100), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="osm")

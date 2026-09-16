"""Training tables (PRD section 10): groups, scenarios with versions, sessions, attempts,
evaluations and the per-session event log that feeds the WebSocket.

Attempt state and card status are stored as short strings rather than enums so later waves
can add values without a migration; the allowed values are the constants below.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Scenario / session modes (PRD section 4).
MODE_CARD_RESPONSE = "card_response"
MODE_CALL_INTAKE = "call_intake"

# Scenario review status.
SCENARIO_DRAFT = "draft"
SCENARIO_REVIEW = "review"
SCENARIO_APPROVED = "approved"

# Session lifecycle.
SESSION_DRAFT = "draft"
SESSION_RUNNING = "running"
SESSION_FINISHED = "finished"

# АРМ-112 marks a card «Не завершено» 48 hours after the service accepted it (memo page 27).
UNFINISHED_SECONDS_DEFAULT = 48 * 3600

# Attempt lifecycle for the card-response mode.
ATTEMPT_ISSUED = "issued"  # card is in the journal, «Добавлена»
ATTEMPT_RECEIVED = "received"  # opened by the trainee, «Получена службой»
ATTEMPT_IN_PROGRESS = "in_progress"  # at least one manual status set
ATTEMPT_FINISHED = "finished"  # final status or «Завершить работу с карточкой»
ATTEMPT_EVALUATED = "evaluated"  # evaluation stored (wave 2 engine)
ACTIVE_ATTEMPT_STATES = (ATTEMPT_ISSUED, ATTEMPT_RECEIVED, ATTEMPT_IN_PROGRESS)

# Call of a call-intake attempt (PRD 9.5).
CALL_IDLE = "idle"  # nothing dialled yet
CALL_RINGING = "ringing"  # the softphone rings
CALL_ANSWERED = "answered"  # conversation in progress
CALL_ENDED = "ended"  # see call_end_reason
# call_end_reason values.
CALL_END_HANGUP = "hangup"  # the operator hung up
CALL_END_CALLER_HANGUP = "caller_hangup"  # the caller dropped the call (scenario)
CALL_END_NO_ANSWER = "no_answer"  # the softphone did not answer in time
CALL_END_NO_CONTACT = "no_contact"  # «нет контакта» pressed
CALL_END_CALL_DROPPED = "call_dropped"  # «срыв звонка» pressed
CALL_END_FAILED = "failed"  # telephony error


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    teacher_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL")
    )
    # «2-1» = ticket 2, item 1: how the seed files refer to tickets.
    ticket_ref: Mapped[str | None] = mapped_column(String(16))
    incident_type_code: Mapped[str | None] = mapped_column(String(24))
    service_code: Mapped[str | None] = mapped_column(String(32))
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=SCENARIO_DRAFT)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # Stable key of a seed file so re-running the seed updates instead of duplicating.
    seed_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ScenarioVersion(Base):
    __tablename__ = "scenario_versions"
    __table_args__ = (UniqueConstraint("scenario_id", "version", name="uq_scenario_versions"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    revision_comment: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TrainingSession(Base):
    __tablename__ = "training_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    teacher_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="SET NULL")
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    card_source: Mapped[str] = mapped_column(String(16), nullable=False, default="scenarios")
    # Explicit ordered queue of scenarios; empty = every approved scenario of the mode that
    # matches incident_groups and service_profile.
    scenario_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )
    incident_groups: Mapped[list[str]] = mapped_column(
        ARRAY(String(8)), nullable=False, default=list
    )
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    service_profile: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, default=list
    )
    norm_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    pass_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=70)
    hints_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Cards each trainee gets before the queue is considered done; 0 = the whole queue.
    cards_per_student: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Seconds after the primary status before an open card becomes «Не завершено».
    unfinished_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=UNFINISHED_SECONDS_DEFAULT
    )
    voice_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dialog_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="select")
    weights: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=SESSION_DRAFT)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seed_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("training_sessions.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="RESTRICT"), nullable=False
    )
    scenario_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    # Number shown in the journal («Происшествие №»), unique within the session.
    card_number: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    primary_status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    draft: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB)
    # [{status, order_number, comment, reject_reason, at, by, action_id}] in order.
    status_log: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    dialog: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    recording_path: Mapped[str | None] = mapped_column(String(500))
    call_state: Mapped[str] = mapped_column(String(16), nullable=False, default=CALL_IDLE)
    call_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    call_end_reason: Mapped[str | None] = mapped_column(String(16))
    call_dropped_marked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    no_contact_marked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    client_submission_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=ATTEMPT_ISSUED)
    # Current status of the service (last entry of status_log) and of the card as a whole.
    response_status: Mapped[str] = mapped_column(String(32), nullable=False, default="added")
    card_status: Mapped[str] = mapped_column(String(32), nullable=False, default="registered")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Setting(Base):
    """Operational settings edited by the administrator, one JSON document per section
    (``telephony``, later ``logging``…). Defaults come from the environment."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Evaluation(Base):
    __tablename__ = "evaluations"

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attempts.id", ondelete="CASCADE"), primary_key=True
    )
    total: Mapped[float] = mapped_column(Float, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    components: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    errors: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    ai_comment: Mapped[str | None] = mapped_column(Text)
    methods: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SessionEvent(Base):
    __tablename__ = "session_events"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_session_events_seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("training_sessions.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    # Null = visible to everyone in the session; otherwise only to this student and the teacher.
    student_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

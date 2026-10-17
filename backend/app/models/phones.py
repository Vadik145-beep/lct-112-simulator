"""The phone directory of the dispatch services (issue #263, customer's recommendations of
16.10.2026, item 8: «перечень специалистов и номера условных телефонов должен формироваться
преподавателем не более 10 номеров для одной ДДС»).

One directory for all teachers (decision of 17.10.2026). Each row is a person of the service's
own organization the trainee can dial from the virtual phone: a role from the customer's four,
a name and a short number of four digits, unique within the service.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Customer's limit: not more than ten numbers for one dispatch service.
MAX_PHONE_ENTRIES = 10
PHONE_NUMBER_DIGITS = 4


class PhoneEntry(Base):
    __tablename__ = "phone_directory"
    __table_args__ = (UniqueConstraint("service_code", "number", name="uq_phone_directory_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # The organizers' import drops services that left the classifier: their numbers go too.
    service_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("services.code", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    number: Mapped[str] = mapped_column(String(PHONE_NUMBER_DIGITS), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

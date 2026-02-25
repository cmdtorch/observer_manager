import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TelegramGroup(Base):
    __tablename__ = "telegram_groups"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Legacy back-ref via TelegramGroup.org_id (maintained by webhook auto-create)
    org: Mapped["Organization | None"] = relationship(  # noqa: F821
        "Organization",
        foreign_keys=[org_id],
        primaryjoin="TelegramGroup.org_id == Organization.id",
    )

    # Canonical back-ref via Organization.telegram_group_id
    organization: Mapped["Organization | None"] = relationship(  # noqa: F821
        "Organization",
        foreign_keys="[Organization.telegram_group_id]",
        primaryjoin="Organization.telegram_group_id == TelegramGroup.id",
        back_populates="telegram_group",
        uselist=False,
    )

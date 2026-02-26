import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.associations import user_org_association


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    grafana_org_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    glitchtip_org_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    glitchtip_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_groups.id"),
        nullable=True,
        unique=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    users: Mapped[list["User"]] = relationship(  # noqa: F821
        "User",
        secondary=user_org_association,
        back_populates="orgs",
        lazy="select",
    )
    api_keys: Mapped[list["ApiKey"]] = relationship(  # noqa: F821
        "ApiKey", back_populates="organization", lazy="select"
    )
    applications: Mapped[list["Application"]] = relationship(  # noqa: F821
        "Application", back_populates="organization", lazy="select"
    )
    telegram_group: Mapped["TelegramGroup | None"] = relationship(  # noqa: F821
        "TelegramGroup",
        foreign_keys=[telegram_group_id],
        back_populates="organization",
        lazy="select",
    )
    invited_users: Mapped[list["InvitedUser"]] = relationship(  # noqa: F821
        "InvitedUser", back_populates="organization", lazy="select"
    )

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.associations import user_org_association


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    grafana_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grafana_invite_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    glitchtip_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    glitchtip_invite_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    orgs: Mapped[list["Organization"]] = relationship(  # noqa: F821
        "Organization",
        secondary=user_org_association,
        back_populates="users",
        lazy="select",
    )

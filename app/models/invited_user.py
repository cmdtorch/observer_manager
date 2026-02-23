from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class InvitedUser(Base):
    __tablename__ = "invited_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    grafana_invited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    grafana_invite_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    glitchtip_invited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    glitchtip_invite_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    organization: Mapped["Organization"] = relationship(  # noqa: F821
        "Organization", back_populates="invited_users"
    )

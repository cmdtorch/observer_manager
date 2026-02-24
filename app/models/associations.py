import uuid

from sqlalchemy import Column, ForeignKey, Table

from app.db.base import Base

user_org_association = Table(
    "user_org_association",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("org_id", ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True),
)

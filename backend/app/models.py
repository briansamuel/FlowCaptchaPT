"""Database models."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Integer, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from .database import Base


def gen_id():
    return str(uuid.uuid4())


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String(100), nullable=False)
    key_hash = Column(String(128), nullable=False, unique=True)
    key_prefix = Column(String(12), nullable=False)
    is_active = Column(Boolean, default=True)
    rate_limit = Column(Integer, default=60)  # requests per minute
    allowed_actions = Column(String(20), default="ALL")  # ALL, VIDEO, IMAGE
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)

    logs = relationship("UsageLog", back_populates="api_key", lazy="dynamic")


class UsageLog(Base):
    __tablename__ = "usage_logs"

    id = Column(String, primary_key=True, default=gen_id)
    api_key_id = Column(String, ForeignKey("api_keys.id"), nullable=True)
    action = Column(String(30), nullable=False)
    success = Column(Boolean, default=False)
    error = Column(Text, nullable=True)
    token_preview = Column(String(30), nullable=True)
    ip_address = Column(String(45), nullable=True)
    response_time_ms = Column(Integer, nullable=True)
    callback_result = Column(String(20), nullable=True)
    callback_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    api_key = relationship("ApiKey", back_populates="logs")

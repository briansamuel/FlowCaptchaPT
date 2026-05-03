"""Database setup - SQLite with async support."""
import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

from .config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def _migrate_usage_logs_nullable(conn):
    """Migrate usage_logs.api_key_id to nullable (SQLite can't ALTER COLUMN)."""
    try:
        result = await conn.execute(text("PRAGMA table_info(usage_logs)"))
        rows = result.fetchall()
        for row in rows:
            if row[1] == "api_key_id" and row[3] == 1:  # notnull=1
                logger.info("Migrating usage_logs.api_key_id to nullable...")
                await conn.execute(text("ALTER TABLE usage_logs RENAME TO usage_logs_old"))
                await conn.execute(text("""
                    CREATE TABLE usage_logs (
                        id VARCHAR PRIMARY KEY,
                        api_key_id VARCHAR REFERENCES api_keys(id),
                        action VARCHAR(30) NOT NULL,
                        success BOOLEAN DEFAULT 0,
                        error TEXT,
                        token_preview VARCHAR(30),
                        ip_address VARCHAR(50),
                        response_time_ms INTEGER,
                        callback_result VARCHAR(20),
                        callback_error TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                await conn.execute(text(
                    "INSERT INTO usage_logs SELECT * FROM usage_logs_old"
                ))
                await conn.execute(text("DROP TABLE usage_logs_old"))
                logger.info("Migration complete.")
                return
    except Exception as e:
        logger.warning(f"Migration check skipped: {e}")


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_usage_logs_nullable(conn)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

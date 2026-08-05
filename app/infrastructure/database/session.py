from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import settings

_pool_options: dict[str, int] = {}
if not settings.DATABASE_URL.startswith("sqlite"):
    _pool_options = {
        "pool_size": 10,
        "max_overflow": 20,
    }

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.ENVIRONMENT == "development",
    future=True,
    **_pool_options,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def unit_of_work() -> AsyncIterator[AsyncSession]:
    """Provide one atomic transaction for a single application operation."""
    async with AsyncSessionLocal.begin() as session:
        yield session


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency backed by the same application Unit of Work."""
    async with unit_of_work() as session:
        yield session

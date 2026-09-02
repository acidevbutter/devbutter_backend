import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db_session
from app.main import app
from app.settings.config import settings

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def _disable_conversation_rate_limit():
    """The whole suite shares one ASGI client IP ("testclient"), so the
    per-IP concierge rate limiter (ADR-0004) would trip after a few dozen
    conversation calls. Off by default; the dedicated test re-enables it."""
    original = settings.conversation_rate_limit_per_minute
    settings.conversation_rate_limit_per_minute = 0
    yield
    settings.conversation_rate_limit_per_minute = original


@pytest.fixture
async def db_session():
    # StaticPool + check_same_thread=False: a real single shared in-memory
    # SQLite connection, so multiple *independent* AsyncSession objects
    # (one per HTTP request, matching production's one-session-per-request
    # via app.db.session.get_db_session) all see the same database --
    # required for tests that fire genuinely concurrent requests
    # (test_payment_method_router.py's asyncio.gather cases). A single
    # shared AsyncSession instance driven from two coroutines concurrently
    # is not safe (SQLAlchemy sessions aren't concurrency-safe), so the
    # `client` fixture below opens a fresh session per request instead of
    # reusing this one across concurrent calls.
    engine = create_async_engine(
        TEST_DATABASE_URL,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def client(db_session: AsyncSession):
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)

    async def _override_get_db_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_get_db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def signup(client: AsyncClient, email: str = "a@example.com", **overrides) -> dict:
    payload = {"email": email, "password": "supersecret", "name": "Test Account", **overrides}
    response = await client.post("/accounts/signup", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def auth_headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}

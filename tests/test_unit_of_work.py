from unittest.mock import MagicMock

import pytest

from app.infrastructure.database.session import unit_of_work


class BeginContext:
    def __init__(self, session):
        self.session = session
        self.exited_with = None

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        self.exited_with = exc_type


@pytest.mark.asyncio
async def test_unit_of_work_uses_sessionmaker_begin(monkeypatch):
    session = object()
    context = BeginContext(session)
    factory = MagicMock()
    factory.begin.return_value = context
    monkeypatch.setattr(
        "app.infrastructure.database.session.AsyncSessionLocal",
        factory,
    )

    async with unit_of_work() as active_session:
        assert active_session is session

    factory.begin.assert_called_once_with()
    assert context.exited_with is None


@pytest.mark.asyncio
async def test_unit_of_work_propagates_error_for_rollback(monkeypatch):
    context = BeginContext(object())
    factory = MagicMock()
    factory.begin.return_value = context
    monkeypatch.setattr(
        "app.infrastructure.database.session.AsyncSessionLocal",
        factory,
    )

    with pytest.raises(RuntimeError):
        async with unit_of_work():
            raise RuntimeError("boom")

    assert context.exited_with is RuntimeError

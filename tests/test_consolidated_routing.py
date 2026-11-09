import pytest
from sqlalchemy import select

from app.domains.memory.models import PendingConfirmation
from app.domains.planning.models import Task
from app.orchestration.orchestrator import MainOrchestrator
from app.tools.planner_tools import PlannerTools
from tests.test_shared_memory import _memory_database, _seed_family


@pytest.mark.asyncio
async def test_purchase_with_currency_creates_expense_without_completing_matching_task() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    try:
        async with factory.begin() as session:
            await PlannerTools.create_task(
                session,
                creator_id=user_id,
                owner_type="household",
                owner_id=household_id,
                assignee_id=user_id,
                title="Купить продукты",
            )
        message = "Купил продукты за 100 грн"
        assert MainOrchestrator.domain_for_message(message) == "finance"
        async with factory.begin() as session:
            response = await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Test",
                message_text=message,
                timezone_name="Europe/Kyiv",
                telegram_chat_id=123456789,
                telegram_message_id=100,
                pending_actions_enabled=True,
            )
        async with factory() as session:
            task = (await session.execute(select(Task))).scalar_one()
            confirmation = (await session.execute(select(PendingConfirmation))).scalar_one()
        assert task.is_completed is False
        assert confirmation.action_type == "finance_log"
        assert confirmation.payload["expenses"][0]["amount"] == 100
        assert "Выберите категорию" in response
    finally:
        await engine.dispose()

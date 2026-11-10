import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agents.memory.agent import SharedMemoryAgent, format_summary
from app.config.settings import settings
from app.domains.finance.models import FinancialTransaction
from app.domains.identity.models import Household, User
from app.domains.memory.models import (
    PendingConfirmation,
    PendingSharedAction,
    SharedConversationMessage,
    SharedConversationMessageRetry,
    SharedConversationSummary,
    SharedMemoryItem,
)
from app.domains.planning.models import Reminder, Task
from app.infrastructure.database.base import Base
from app.infrastructure.integrations.conversation_summary_worker import (
    create_daily_summaries,
    deliver_due_shared_summaries,
    retry_due_shared_context_messages,
)
from app.orchestration.orchestrator import MainOrchestrator
from app.tools.memory_tools import SharedMemoryTools

MEMORY_TABLES = [
    Household.__table__,
    User.__table__,
    SharedConversationMessage.__table__,
    SharedConversationMessageRetry.__table__,
    SharedMemoryItem.__table__,
    SharedConversationSummary.__table__,
    PendingSharedAction.__table__,
    PendingConfirmation.__table__,
    Reminder.__table__,
    Task.__table__,
    FinancialTransaction.__table__,
]


async def _memory_database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=MEMORY_TABLES)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_family(factory):
    household_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with factory.begin() as session:
        session.add(
            Household(
                id=household_id,
                name="Family",
                timezone="Europe/Kyiv",
            )
        )
        session.add(
            User(
                id=user_id,
                household_id=household_id,
                telegram_id=123,
                first_name="Саша",
            )
        )
    return household_id, user_id


@pytest.mark.asyncio
async def test_shared_context_is_retrieved_from_postgres_for_general_reply() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    chat_id = -100123

    async with factory.begin() as session:
        await SharedMemoryTools.record_message(
            session,
            household_id=household_id,
            author_user_id=user_id,
            telegram_chat_id=chat_id,
            telegram_message_id=1,
            role="user",
            author_name="Саша",
            message_type="text",
            content="Мы решили поехать в горы первого августа.",
        )

    with patch(
        "app.integrations.llm.provider.TerraReasoningProvider.generate_text",
        new=AsyncMock(return_value="Вы решили поехать в горы первого августа."),
    ) as generate:
        async with factory.begin() as session:
            response = await MainOrchestrator.process_user_message(
                session=session,
                user_id=user_id,
                household_id=household_id,
                user_name="Саша",
                message_text="Когда мы решили ехать в горы?",
                telegram_chat_id=chat_id,
                shared_context_enabled=True,
            )

    assert response == "Вы решили поехать в горы первого августа."
    prompt = generate.await_args.kwargs["prompt"]
    assert "Мы решили поехать в горы первого августа." in prompt
    assert "<recent_shared_chat>" in prompt
    await engine.dispose()


@pytest.mark.asyncio
async def test_explicit_shared_memory_is_persisted() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)

    async with factory.begin() as session:
        response = await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_id,
            household_id=household_id,
            user_name="Саша",
            message_text="Запомни: корм лучше покупать заранее",
            telegram_chat_id=-100123,
            shared_context_enabled=True,
        )

    async with factory() as session:
        memory = (await session.execute(select(SharedMemoryItem))).scalar_one()
    assert memory.kind == "fact"
    assert memory.content == "корм лучше покупать заранее"
    assert "Запомнил для общего семейного контекста" in response

    async with factory.begin() as session:
        forget_response = await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_id,
            household_id=household_id,
            user_name="Саша",
            message_text="Не учитывай больше корм заранее",
            telegram_chat_id=-100123,
            shared_context_enabled=True,
        )
    async with factory() as session:
        memory = (await session.execute(select(SharedMemoryItem))).scalar_one()
        confirmation = (await session.execute(select(PendingConfirmation))).scalar_one()
    assert memory.status == "active"
    assert "Подтвердите удаление" in forget_response

    async with factory.begin() as session:
        confirmed_response = await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_id,
            household_id=household_id,
            user_name="Саша",
            message_text=f"подтвердить {confirmation.confirmation_code}",
            telegram_chat_id=-100123,
            shared_context_enabled=True,
        )
    async with factory() as session:
        memory = (await session.execute(select(SharedMemoryItem))).scalar_one()
        confirmation = (await session.execute(select(PendingConfirmation))).scalar_one()
    assert memory.status == "dismissed"
    assert confirmation.status == "completed"
    assert "Убрал из общей памяти" in confirmed_response
    await engine.dispose()


@pytest.mark.asyncio
async def test_followup_time_completes_pending_reminder_with_original_title() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    chat_id = -100123

    async with factory.begin() as session:
        first_response = await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_id,
            household_id=household_id,
            user_name="Саша",
            message_text="Напомни, чтоб я уточнил у Сани, что они решили по поводу брони",
            telegram_chat_id=chat_id,
            shared_context_enabled=True,
        )
    async with factory.begin() as session:
        second_response = await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_id,
            household_id=household_id,
            user_name="Саша",
            message_text="Через 30 минут",
            telegram_chat_id=chat_id,
            shared_context_enabled=True,
        )

    async with factory() as session:
        reminder = (await session.execute(select(Reminder))).scalar_one()
        action = (await session.execute(select(PendingSharedAction))).scalar_one()
        confirmations = list((await session.execute(select(PendingConfirmation))).scalars())
    assert "Напишите следующим сообщением, когда" in first_response
    assert reminder.title == "чтоб я уточнил у Сани, что они решили по поводу брони"
    assert reminder.telegram_chat_id == chat_id
    assert action.status == "completed"
    assert confirmations == []
    assert "Напоминание создано" in second_response
    await engine.dispose()


@pytest.mark.asyncio
async def test_tasks_command_reads_active_household_tasks() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    async with factory.begin() as session:
        session.add(
            Task(
                owner_type="household",
                owner_id=household_id,
                creator_id=user_id,
                assignee_id=user_id,
                title="Уточнить бронь",
            )
        )

    async with factory.begin() as session:
        response = await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_id,
            household_id=household_id,
            user_name="Саша",
            message_text="/tasks",
        )

    assert "Активные семейные задачи" in response
    assert "Уточнить бронь" in response
    await engine.dispose()


@pytest.mark.asyncio
async def test_natural_task_command_creates_real_household_task() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)

    async with factory.begin() as session:
        response = await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_id,
            household_id=household_id,
            user_name="Саша",
            message_text="Создай задачу купить корм",
        )

    async with factory() as session:
        task = (await session.execute(select(Task))).scalar_one()
    assert task.owner_id == household_id
    assert task.title == "купить корм"
    assert "Создал семейную задачу" in response
    await engine.dispose()


@pytest.mark.asyncio
async def test_shared_food_photo_is_analyzed_without_personal_meal_write() -> None:
    analysis = {
        "dish_name": "Ньокки",
        "calories_est": 600,
        "proteins_g": 20,
        "fats_g": 18,
        "carbs_g": 80,
        "coaching_tip": "Добавьте овощи.",
    }
    with (
        patch(
            "app.integrations.gemini.client.GeminiVisionClient.analyze_food_photo",
            new=AsyncMock(return_value=analysis),
        ) as analyze,
        patch(
            "app.orchestration.orchestrator.HealthTools.log_meal_photo",
            new=AsyncMock(),
        ) as log_personal_meal,
        patch(
            "app.orchestration.orchestrator.SharedMemoryTools.get_pending_action",
            new=AsyncMock(return_value=None),
        ),
    ):
        response = await MainOrchestrator.process_user_message(
            session=object(),
            user_id=uuid.uuid4(),
            household_id=uuid.uuid4(),
            user_name="Саша",
            message_text="",
            photo_bytes=b"photo",
            telegram_chat_id=-100123,
            shared_context_enabled=True,
        )

    analyze.assert_awaited_once_with(b"photo")
    log_personal_meal.assert_not_awaited()
    assert "Ньокки" in response


@pytest.mark.asyncio
async def test_shared_message_recording_is_idempotent_for_repeated_telegram_updates() -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    async with factory.begin() as session:
        first = await SharedMemoryTools.record_message(
            session,
            household_id=household_id,
            author_user_id=user_id,
            telegram_chat_id=-100123,
            telegram_message_id=777,
            role="user",
            author_name="Alex",
            message_type="text",
            content="same update",
        )
        second = await SharedMemoryTools.record_message(
            session,
            household_id=household_id,
            author_user_id=user_id,
            telegram_chat_id=-100123,
            telegram_message_id=777,
            role="user",
            author_name="Alex",
            message_type="text",
            content="same update again",
        )
        assert second.id == first.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_idle_chat_does_not_create_or_deliver_conversation_summary(monkeypatch) -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    chat_id = -100123
    # Keep this scenario before the Kyiv evening cutoff so it isolates the
    # inactivity recap from the separate daily-digest path.
    old = datetime(2026, 8, 5, 10, tzinfo=timezone.utc)
    async with factory.begin() as session:
        first = await SharedMemoryTools.record_message(
            session,
            household_id=household_id,
            author_user_id=user_id,
            telegram_chat_id=chat_id,
            telegram_message_id=1,
            role="user",
            author_name="Саша",
            message_type="text",
            content="Нужно уточнить бронь.",
        )
        second = await SharedMemoryTools.record_message(
            session,
            household_id=household_id,
            author_user_id=None,
            telegram_chat_id=chat_id,
            telegram_message_id=None,
            role="assistant",
            author_name="Family",
            message_type="text",
            content="Когда напомнить?",
        )
        first.created_at = old
        second.created_at = old + timedelta(minutes=1)

    agent = SharedMemoryAgent()
    agent.summarize_messages = AsyncMock()
    bot = AsyncMock()
    monkeypatch.setattr(
        "app.infrastructure.integrations.conversation_summary_worker.AsyncSessionLocal",
        factory,
    )
    monkeypatch.setattr(settings, "FAMILY_GROUP_CHAT_ID", chat_id)

    delivered = await deliver_due_shared_summaries(
        bot,
        now=old + timedelta(hours=2),
        agent=agent,
    )

    assert delivered == 0
    agent.summarize_messages.assert_not_awaited()
    bot.send_message.assert_not_awaited()
    async with factory() as session:
        summaries = (await session.execute(select(SharedConversationSummary))).scalars().all()
        memories = (await session.execute(select(SharedMemoryItem))).scalars().all()
    assert summaries == []
    assert memories == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_idle_finance_exchange_does_not_create_or_deliver_conversation_summary(monkeypatch) -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    chat_id = -100123
    old = datetime(2026, 8, 5, 10, tzinfo=timezone.utc)
    async with factory.begin() as session:
        user_message = await SharedMemoryTools.record_message(
            session,
            household_id=household_id,
            author_user_id=user_id,
            telegram_chat_id=chat_id,
            telegram_message_id=1,
            role="user",
            author_name="Саша",
            message_type="text",
            content="85 грн отдых",
        )
        assistant_message = await SharedMemoryTools.record_message(
            session,
            household_id=household_id,
            author_user_id=None,
            telegram_chat_id=chat_id,
            telegram_message_id=2,
            role="assistant",
            author_name="Family",
            message_type="text",
            content="Расход сохранён.",
        )
        user_message.created_at = old
        assistant_message.created_at = old + timedelta(minutes=1)

    agent = SharedMemoryAgent()
    agent.summarize_messages = AsyncMock(
        return_value={
            "decisions": [],
            "actions": ["Подтвердить GQxamlvp"],
            "money": [],
            "open_questions": [],
            "facts": ["Отмечен отдых по высшей математике за 85 грн"],
            "suggestions": [],
        }
    )
    bot = AsyncMock()
    monkeypatch.setattr(
        "app.infrastructure.integrations.conversation_summary_worker.AsyncSessionLocal",
        factory,
    )
    monkeypatch.setattr(settings, "FAMILY_GROUP_CHAT_ID", None)

    delivered = await deliver_due_shared_summaries(
        bot,
        now=old + timedelta(hours=2),
        agent=agent,
    )

    assert delivered == 0
    agent.summarize_messages.assert_not_awaited()
    bot.send_message.assert_not_awaited()
    async with factory() as session:
        summaries = (await session.execute(select(SharedConversationSummary))).scalars().all()
    assert summaries == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_retry_worker_persists_delivered_assistant_reply_once(monkeypatch) -> None:
    engine, factory = await _memory_database()
    household_id, _ = await _seed_family(factory)
    chat_id = -100123
    now = datetime.now(timezone.utc)

    async with factory.begin() as session:
        await SharedMemoryTools.queue_message_for_retry(
            session,
            household_id=household_id,
            telegram_chat_id=chat_id,
            telegram_message_id=42,
            author_name="Family",
            message_type="text",
            content="Завтра без встреч.",
            now=now,
        )

    monkeypatch.setattr(
        "app.infrastructure.integrations.conversation_summary_worker.AsyncSessionLocal",
        factory,
    )
    monkeypatch.setattr(settings, "SHARED_CHAT_MEMORY_ENABLED", True)

    delivered = await retry_due_shared_context_messages(now=now)
    delivered_again = await retry_due_shared_context_messages(now=now)

    assert delivered == 1
    assert delivered_again == 0
    async with factory() as session:
        message = (await session.execute(select(SharedConversationMessage))).scalar_one()
        retry = (await session.execute(select(SharedConversationMessageRetry))).scalar_one()
    assert message.telegram_message_id == 42
    assert message.content == "Завтра без встреч."
    assert retry.status == "delivered"
    await engine.dispose()


@pytest.mark.asyncio
async def test_persisted_summary_preserves_visual_sections() -> None:
    engine, factory = await _memory_database()
    household_id, _ = await _seed_family(factory)
    structured_data = {
        "decisions": ["Кофе относится к кафе"],
        "actions": ["Проверить расход 95 грн"],
        "money": ["95 грн — кофе"],
        "open_questions": [],
        "facts": [],
        "suggestions": [],
    }
    expected = format_summary(structured_data)

    async with factory.begin() as session:
        await SharedMemoryTools.save_summary(
            session,
            household_id=household_id,
            telegram_chat_id=-100123,
            summary_kind="conversation",
            period_key="pretty-summary",
            summary_text=expected,
            structured_data=structured_data,
            window_started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            window_ended_at=datetime.now(timezone.utc),
        )

    async with factory() as session:
        summary = (await session.execute(select(SharedConversationSummary))).scalar_one()
    assert summary.summary_text == expected
    assert "\n\n✅ Решили\n• Кофе относится к кафе" in summary.summary_text
    assert "\n\n💳 Деньги\n• 95 грн — кофе" in summary.summary_text
    await engine.dispose()


@pytest.mark.asyncio
async def test_daily_summary_uses_full_transcript_and_recovers_previous_day(monkeypatch) -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    chat_id = -100123
    day = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
    async with factory.begin() as session:
        user_message = await SharedMemoryTools.record_message(
            session,
            household_id=household_id,
            author_user_id=user_id,
            telegram_chat_id=chat_id,
            telegram_message_id=20,
            role="user",
            author_name="Саша",
            message_type="text",
            content="Нужно уточнить билеты.",
        )
        assistant_message = await SharedMemoryTools.record_message(
            session,
            household_id=household_id,
            author_user_id=None,
            telegram_chat_id=chat_id,
            telegram_message_id=21,
            role="assistant",
            author_name="Family",
            message_type="text",
            content="Напомню вечером.",
        )
        user_message.created_at = day
        assistant_message.created_at = day + timedelta(minutes=1)

    agent = SharedMemoryAgent()
    agent.summarize_messages = AsyncMock(
        return_value={
            "decisions": [],
            "actions": ["Уточнить билеты"],
            "money": [],
            "open_questions": [],
            "facts": [],
            "suggestions": [],
        }
    )
    monkeypatch.setattr("app.infrastructure.integrations.conversation_summary_worker.AsyncSessionLocal", factory)
    monkeypatch.setattr(settings, "FAMILY_GROUP_CHAT_ID", chat_id)
    monkeypatch.setattr(settings, "CHAT_SUMMARY_DAILY_HOUR", 21)
    monkeypatch.setattr(settings, "CHAT_SUMMARY_MAX_MESSAGES", 1)

    summaries = await create_daily_summaries(
        now=datetime(2026, 8, 5, 8, tzinfo=timezone.utc),
        agent=agent,
    )

    assert len(summaries) == 1
    assert [call.args[0] for call in agent.summarize_messages.await_args_list] == [
        [{"author": "Саша", "content": "Нужно уточнить билеты."}],
        [{"author": "Family", "content": "Напомню вечером."}],
    ]
    assert await create_daily_summaries(now=datetime(2026, 8, 5, 8, tzinfo=timezone.utc), agent=agent) == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_summary_extraction_does_not_reprocess_bot_reports(monkeypatch) -> None:
    engine, factory = await _memory_database()
    household_id, user_id = await _seed_family(factory)
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    async with factory.begin() as session:
        user_message = await SharedMemoryTools.record_message(
            session,
            household_id=household_id,
            author_user_id=user_id,
            telegram_chat_id=-100123,
            telegram_message_id=10,
            role="user",
            author_name="Саша",
            message_type="text",
            content="Кофе 95 грн.",
        )
        bot_report = await SharedMemoryTools.record_message(
            session,
            household_id=household_id,
            author_user_id=None,
            telegram_chat_id=-100123,
            telegram_message_id=11,
            role="assistant",
            author_name="Family",
            message_type="text",
            content="Длинный отчёт за месяц со всеми категориями и прошлыми операциями.",
        )
        user_message.created_at = old
        bot_report.created_at = old + timedelta(minutes=1)

    agent = SharedMemoryAgent()
    agent.summarize_messages = AsyncMock(
        return_value={
            "decisions": [],
            "actions": [],
            "money": ["95 грн — кофе"],
            "open_questions": [],
            "facts": [],
            "suggestions": [],
        }
    )
    monkeypatch.setattr(
        "app.infrastructure.integrations.conversation_summary_worker.AsyncSessionLocal",
        factory,
    )

    from app.infrastructure.integrations.conversation_summary_worker import (
        create_idle_conversation_summaries,
    )

    await create_idle_conversation_summaries(
        now=old + timedelta(hours=2),
        agent=agent,
    )

    assert agent.summarize_messages.await_args.args[0] == [
        {
            "author": "Саша",
            "content": "Кофе 95 грн.",
        }
    ]
    await engine.dispose()

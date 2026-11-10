import re
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.memory.models import PendingConfirmation
from app.integrations.google.sheets import MONTHLY_BUDGET_HEADERS
from app.tools.confirmation_tools import ConfirmationTools
from app.tools.finance_tools import FinanceTools


def expense_timestamp(text: str, timezone_name: str, *, now: datetime | None = None) -> datetime:
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(timezone_name))
    # Remove monetary tokens before looking for dates (7.50 грн is an amount).
    date_text = re.sub(r"\d+(?:[.,]\d{1,2})?\s*(?:грн\.?|uah|₴|грив\w*)", "", text, flags=re.I)
    match = re.search(r"(?<![\d.])(\d{1,2})\.(\d{2})(?:\.(\d{4}))?(?!\d)(?!\.\d)", date_text)
    if match:
        day, month, year = match.groups()
        target = local.date().replace(day=int(day), month=int(month), year=int(year or local.year))
    elif re.search(r"\b(?:позавчера|позавчора)\b", text, re.I):
        target = local.date() - timedelta(days=2)
    elif re.search(r"\b(?:вчера|вчора)\b", text, re.I):
        target = local.date() - timedelta(days=1)
    else:
        return local.astimezone(timezone.utc)
    return datetime.combine(target, time(12), tzinfo=local.tzinfo).astimezone(timezone.utc)


def selection_prompt(confirmation: PendingConfirmation) -> str:
    if not confirmation.payload.get("category_selection"):
        return "Отправьте расход заново, чтобы выбрать категорию и проверить дату."
    index = int(confirmation.payload.get("next_expense", 0))
    expenses = confirmation.payload["expenses"]
    expense = expenses[index]
    stamp = datetime.fromisoformat(expense["occurred_at"]).astimezone(ZoneInfo(confirmation.payload["timezone"]))
    return (
        f"💳 {expense['amount']:g} грн · {stamp:%d.%m.%Y} · расход {index + 1} из {len(expenses)}\n"
        "Выберите категорию кнопкой — нажатие сохранит расход.\n"
        f"`категория {confirmation.confirmation_code} {index}`"
    )


async def select_expense_category(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    household_id: uuid.UUID,
    telegram_chat_id: int,
    code: str,
    index: int,
    category_index: int,
) -> str:
    confirmation = await ConfirmationTools.find_for_reply(
        session,
        household_id=household_id,
        telegram_chat_id=telegram_chat_id,
        initiated_by_user_id=user_id,
        confirmation_code=code,
    )
    if confirmation is None or confirmation.action_type != "finance_log":
        return "Не нашёл расход для этого пользователя и чата."
    if confirmation.status != "pending":
        return "Этот расход уже обработан или срок выбора истёк."
    if not confirmation.payload.get("category_selection"):
        return selection_prompt(confirmation)
    if index != confirmation.payload.get("next_expense", 0):
        return "Эта кнопка уже использована. Выберите категорию следующего расхода."
    if category_index < 0 or category_index >= len(MONTHLY_BUDGET_HEADERS):
        return "Неизвестная категория."
    if not await ConfirmationTools.claim(session, confirmation):
        return "Срок выбора истёк. Отправьте расход заново."
    expense = confirmation.payload["expenses"][index]
    category = MONTHLY_BUDGET_HEADERS[category_index]
    result = await FinanceTools.log_transaction(
        session=session,
        owner_type="household",
        owner_id=household_id,
        amount=expense["amount"],
        merchant=expense["merchant"],
        category=category,
        description=expense["description"],
        external_id=expense["external_id"],
        occurred_at=datetime.fromisoformat(expense["occurred_at"]),
        telegram_chat_id=telegram_chat_id,
    )
    if result.get("status") not in {"SUCCESS", "DUPLICATE"}:
        raise RuntimeError("Expense was not persisted.")
    stamp = datetime.fromisoformat(expense["occurred_at"]).astimezone(ZoneInfo(confirmation.payload["timezone"]))
    response = f"💳 Расход сохранён: {expense['amount']:g} грн · {category} · {stamp:%d.%m.%Y}."
    response += "\nСинхронизация с Google Sheets поставлена в очередь."
    if index + 1 < len(confirmation.payload["expenses"]):
        confirmation.payload = {**confirmation.payload, "next_expense": index + 1}
        confirmation.status = "pending"
        response += "\n\n" + selection_prompt(confirmation)
    else:
        await ConfirmationTools.complete(confirmation)
    await session.flush()
    return response

import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode

from app.config.settings import settings
from app.infrastructure.database.session import AsyncSessionLocal
from app.orchestration.orchestrator import MainOrchestrator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "🌿 **Добро пожаловать в Family AI Life OS!**\n\n"
        "Я — ваш семейный ассистент. Я помогаю следить за здоровьем, сном (Oura), "
        "питанием по фото, семейным бюджетом, расходами и задачами.\n\n"
        "Просто напишите мне свой запрос или отправьте фото еды/чека!"
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📖 **Доступные возможности:**\n"
        "• 🥗 **Фото еды**: отправьте фото, и я оценю калории и БЖУ.\n"
        "• 💍 **Oura Ring**: подключайте Oura для отслеживания сна и готовности.\n"
        "• 💳 **Финансы**: отправляйте чеки или расходы для учета бюджета.\n"
        "• 🛒 **Покупки**: говорите 'добавь молоко в список покупок'."
    )


@dp.message()
async def handle_user_message(message: types.Message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name or "Пользователь"
    
    photo_bytes = None
    if message.photo:
        # Get highest resolution photo
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        photo_bytes = downloaded_file.read()

    async with AsyncSessionLocal() as session:
        # Placeholder UUIDs for Denys & Oleksandra single-household setup
        user_uuid = settings.DENYS_TELEGRAM_ID
        household_uuid = user_uuid

        response_text = await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_uuid,
            household_id=household_uuid,
            user_name=user_name,
            message_text=message.text or message.caption or "",
            photo_bytes=photo_bytes,
        )

    await message.answer(response_text, parse_mode=ParseMode.MARKDOWN)


async def start_bot():
    logger.info("Starting Family AI Life OS Telegram Bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(start_bot())

import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app.config.settings import settings
from app.infrastructure.database.session import AsyncSessionLocal
from app.integrations.oura.client import OuraClient
from app.orchestration.orchestrator import MainOrchestrator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()


async def setup_bot_commands(bot_instance: Bot):
    """Registers official Telegram slash commands menu."""
    commands = [
        BotCommand(command="start", description="🌿 Запустить семейного ассистента"),
        BotCommand(command="help", description="📖 Справка и список возможностей"),
        BotCommand(command="oura", description="💍 Подключить или проверить Oura Ring"),
        BotCommand(command="tasks", description="📋 Посмотреть текущие задачи"),
        BotCommand(command="shopping", description="🛒 Семейный список покупок"),
        BotCommand(command="budget", description="💳 Расходы и бюджет месяца"),
        BotCommand(command="health", description="🥗 Сводка здоровья и питания"),
    ]
    await bot_instance.set_my_commands(commands)
    logger.info("Telegram Bot slash commands registered successfully.")


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "🌿 **Добро пожаловать в Family AI Life OS!**\n\n"
        "Я — ваш семейный ассистент. Я помогаю следить за здоровьем, сном (Oura), "
        "питанием по фото, семейным бюджетом, расходами и задачами.\n\n"
        "🔗 Для подключения Oura Ring введите команду: **/oura**\n"
        "🥗 Или просто отправьте мне фото еды / чека!"
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📖 **Доступные возможности:**\n"
        "• **/oura** — подключение Oura Ring\n"
        "• **/shopping** — посмотреть семейный список покупок\n"
        "• **/tasks** — показать список задач\n"
        "• **/budget** — сводка трат за месяц\n"
        "• 🥗 **Фото еды**: отправьте фото, и я оценю калории и БЖУ.\n"
        "• 💳 **Чеки**: отправьте фото чека для учёта в бюджете."
    )


@dp.message(Command("oura"))
async def cmd_oura_setup(message: types.Message):
    user_id = message.from_user.id
    auth_url = OuraClient.get_authorization_url(state=str(user_id))
    
    await message.answer(
        "💍 **Подключение Oura Ring:**\n\n"
        "1. Перейдите по ссылке ниже и войдите под вашим аккаунтом Oura:\n"
        f"🔗 [Авторизоваться в Oura Ring]({auth_url})\n\n"
        "2. Нажмите кнопку **Approve / Разрешить**.\n"
        "3. Браузер перенаправит вас на страницу. Скопируйте ссылку из адресной строки (или полученный код `code=...`) и **отправьте её мне ответом в этот чат**!",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )


@dp.message()
async def handle_user_message(message: types.Message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name or "Пользователь"
    text = message.text or message.caption or ""

    # OAuth Code Capturing from User input
    if "code=" in text or (len(text.strip()) > 20 and not text.startswith("/") and not message.photo):
        code = text.split("code=")[-1].split("&")[0].strip()
        try:
            tokens = await OuraClient.exchange_code_for_tokens(code)
            await message.answer(
                "✅ **Oura Ring успешно подключено!**\n\n"
                "Теперь я автоматически отслеживаю ваш сон, готовность (Readiness) и активность.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        except Exception as e:
            logger.warning(f"Oura OAuth exchange error: {e}")

    photo_bytes = None
    if message.photo:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        photo_bytes = downloaded_file.read()

    async with AsyncSessionLocal() as session:
        user_uuid = settings.DENYS_TELEGRAM_ID if user_id == settings.DENYS_TELEGRAM_ID else settings.OLEKSANDRA_TELEGRAM_ID
        household_uuid = user_uuid

        response_text = await MainOrchestrator.process_user_message(
            session=session,
            user_id=user_uuid,
            household_id=household_uuid,
            user_name=user_name,
            message_text=text,
            photo_bytes=photo_bytes,
        )

    await message.answer(response_text, parse_mode=ParseMode.MARKDOWN)


async def start_bot():
    logger.info("Starting Family AI Life OS Telegram Bot...")
    await setup_bot_commands(bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(start_bot())

"""Public application information; no account data or provider calls."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

STYLE = """
:root{color-scheme:light;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;
color:#183c35;background:#f5f4ef}*{box-sizing:border-box}body{margin:0}
a{color:#24594c;text-underline-offset:4px}a:focus-visible{outline:3px solid #a76522;outline-offset:5px}
header,main,footer{width:min(860px,calc(100% - 40px));margin:auto}
header{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:28px 0;
border-bottom:1px solid #cbd4cb}header a{font-weight:650}nav a{font-size:16px}
main{padding:64px 0}h1{font-size:clamp(32px,6vw,58px);line-height:1.12;letter-spacing:-.035em;
margin:16px 0 24px;overflow-wrap:anywhere}h2{font-size:24px;line-height:1.3;margin:32px 0 12px}
p,li{font-size:17px;line-height:1.75}p{margin:12px 0}.eyebrow{font-size:13px;
letter-spacing:.13em;text-transform:uppercase;font-weight:700}.intro{font-size:21px;max-width:680px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin:36px 0}
.card{padding:24px;border:1px solid #cbd4cb;border-radius:18px;background:#fffdf8}
.card h2{margin:0 0 10px}.note{border-left:3px solid #527666;padding-left:20px;margin-top:32px}
footer{padding:24px 0 36px;border-top:1px solid #cbd4cb;display:flex;gap:18px;flex-wrap:wrap}
footer a{overflow-wrap:anywhere}.muted{color:#52645e}ul{padding-left:24px}
@media(max-width:560px){main{padding:36px 0}.grid{grid-template-columns:1fr}
header{align-items:flex-start;flex-direction:column;gap:12px}.card{padding:20px}}
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f'<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title} · FamilyBotapp</title><style>{STYLE}</style></head>"
        '<body><header><a href="/">FamilyBotapp</a><nav aria-label="Основная навигация">'
        '<a href="/privacy">Конфиденциальность</a></nav></header>'
        f"<main>{body}</main><footer><span>FamilyBotapp</span>"
        '<a href="/privacy">Политика конфиденциальности</a>'
        '<a href="mailto:ssobachkovna@gmail.com">ssobachkovna@gmail.com</a></footer></body></html>'
    )


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home() -> HTMLResponse:
    return _page(
        "Личный семейный помощник",
        """<p class="eyebrow">Личный помощник в Telegram</p>
        <h1>Семейные дела.<br>В одном диалоге.</h1>
        <p class="intro">FamilyBotapp помогает заранее разрешённым пользователям
        планировать дела, вести семейные списки и работать со своим Google-аккаунтом.</p>
        <div class="grid"><section class="card"><h2>Google Calendar</h2>
        <p>Просмотр ближайших событий, создание встреч и удаление событий по командам пользователя.
        Приложению нужен доступ к событиям подключённого календаря.</p></section>
        <section class="card"><h2>Gmail</h2><p>Просмотр важных писем: отправитель, тема,
        дата и краткий фрагмент. При подключении также запрашивается разрешение Gmail
        на черновики и отправку; автоматическая отправка писем этой страницей не выполняется.</p></section></div>
        <h2>Доступ под вашим контролем</h2><p>Это личный семейный проект, без открытой регистрации.
        Подключение Google начинается командой <strong>/google</strong> в личном чате с ботом
        и подтверждается на странице Google. Пароль Google боту не передаётся.</p>
        <p>Для повторного подключения используйте <strong>/google reconnect</strong>.
        Отозвать доступ можно в <a href="https://myaccount.google.com/connections">настройках Google Account</a>.</p>
        <p class="note">Перед подключением прочитайте <a href="/privacy">политику конфиденциальности</a>:
        какие данные обрабатываются, какие сервисы используются и как запросить удаление.</p>""",
    )


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy() -> HTMLResponse:
    return _page(
        "Политика конфиденциальности",
        """<p class="eyebrow">Данные и доступ</p><h1>Политика конфиденциальности</h1>
        <p class="muted">FamilyBotapp · Обновлено 14 сентября 2026 года</p>
        <p>FamilyBotapp — личный Telegram-помощник для ограниченного круга семейных пользователей.
        Контакт оператора по вопросам данных: <a href="mailto:ssobachkovna@gmail.com">ssobachkovna@gmail.com</a>.</p>
        <h2>Какие данные обрабатываются</h2><ul>
        <li>Идентификаторы пользователя и чата Telegram, присланные команды, сообщения и вложения,
        необходимые для выбранной функции.</li>
        <li>Google: разрешённые сведения об аккаунте, OAuth-токены и предоставленные разрешения.</li>
        <li>Gmail: идентификаторы писем, отправители, темы, даты, метки и краткие фрагменты
        для показа и определения важных писем.</li>
        <li>Google Calendar: сведения о событиях, включая названия и время, для просмотра,
        создания и удаления событий по запросам пользователя.</li>
        <li>Созданные пользователем задачи, напоминания и другие записи; при включённой памяти
        общего чата — сообщения и краткие сводки для продолжения разговора.</li></ul>
        <h2>Для чего нужны данные</h2><p>Данные используются для выполнения команд,
        отображения результатов, напоминаний и работы подключённых функций. Токен обновления
        позволяет продлевать доступ без повторного входа, пока Google разрешает его использование.
        При авторизации запрашиваются также права Gmail на черновики и отправку писем;
        наличие разрешения само по себе не означает отправку письма.</p>
        <h2>Сервисы и AI-обработка</h2><p>Google предоставляет Gmail и Calendar API.
        Telegram доставляет сообщения и результаты. Railway используется для размещения сервиса;
        записи приложения хранятся в его базе данных.</p>
        <p>В зависимости от функции и конфигурации OpenAI и Google Gemini обрабатывают
        текст запросов, контекст разговора, изображения или аудио для распознавания и подготовки ответа.
        Текст, который вы включили в запрос, может содержать сведения о встрече или письме.
        Команды /mail и /calendar получают данные Google и формируют ответы без отдельного
        AI-вызова в этих обработчиках. Это не означает, что скопированные вами в обычный диалог
        данные никогда не попадут в AI-обработку.</p>
        <p>FamilyBotapp не продаёт данные Google и не использует их для рекламы.
        Использование и передача информации, полученной через Google API, должны соответствовать
        <a href="https://developers.google.com/terms/api-services-user-data-policy">
        Google API Services User Data Policy</a>,
        включая требования Limited Use. Данные Google не предназначены для обучения
        общих моделей AI оператором приложения.</p>
        <h2>Хранение и защита</h2><p>OAuth-токены хранятся в зашифрованном виде с привязкой
        к пользователю. Пароль Google приложение не получает. Это не означает, что все сообщения
        и записи базы данных зашифрованы тем же способом. Доступ к функциям бота ограничен
        разрешёнными Telegram-пользователями и правилами личных и семейных чатов.</p>
        <p>Единый автоматический срок удаления всех записей не установлен. Сохранённые данные
        остаются до удаления доступными функциями приложения или обработки запроса оператором;
        резервные копии и данные внешних сервисов могут иметь отдельные сроки хранения.</p>
        <h2>Отзыв доступа и удаление</h2><p>Отозвать разрешения можно на странице
        <a href="https://myaccount.google.com/connections">подключений Google Account</a>.
        Отзыв останавливает дальнейший доступ, но сам по себе не удаляет ранее сохранённые записи.</p>
        <p>Чтобы запросить удаление токенов и сохранённых данных, напишите на
        <a href="mailto:ssobachkovna@gmail.com">ssobachkovna@gmail.com</a> и укажите, что нужен
        запрос на удаление данных FamilyBotapp. Не присылайте пароль, токены или коды авторизации.
        Оператор может попросить подтвердить, что аккаунт принадлежит вам.</p>
        <h2>Изменения политики</h2><p>При изменении работы с данными эта страница обновляется.
        Дата актуальной версии указана в начале документа.</p>""",
    )

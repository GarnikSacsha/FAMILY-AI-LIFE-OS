import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_CLOCK = re.compile(
    r"(?<!\d)([01]?\d|2[0-3])(?:[:.]([0-5]\d)|\s+час(?:а|ов)?)(?!\d)",
    re.IGNORECASE,
)
_TIME_PHRASE = re.compile(
    r"(?i)\b(?:в|на)\s*(?:[01]?\d|2[0-3])"
    r"(?:[:.]([0-5]\d)|\s+час(?:а|ов)?)?(?:\s+(?:утра|дня|вечера|ночи))?\b"
)
_NUMERIC_END = re.compile(
    r"(?i)\b(?:по|до)\s+(0?[1-9]|[12]\d|3[01])[./-](0?[1-9]|1[0-2])"
    r"(?:[./-](\d{2}|\d{4}))?\b"
)
_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
_MONTH_END = re.compile(r"(?i)\b(?:по|до)\s+(0?[1-9]|[12]\d|3[01])\s+(" + "|".join(_MONTHS) + r")(?:\s+(\d{4}))?\b")
_WEEKDAYS = {
    "понедельник": 0,
    "понедельника": 0,
    "вторник": 1,
    "вторника": 1,
    "среду": 2,
    "среды": 2,
    "четверг": 3,
    "четверга": 3,
    "пятницу": 4,
    "пятницы": 4,
    "субботу": 5,
    "субботы": 5,
    "воскресенье": 6,
    "воскресенья": 6,
}
_WEEKDAY_END = re.compile(r"(?i)\b(?:по|до)\s+(" + "|".join(_WEEKDAYS) + r")\b")
_FOREVER = re.compile(r"(?i)\b(?:бессроч\w*|навсегда|без\s+конца)\b")
_QUOTED_TITLE = re.compile(r'"([^"\r\n]{1,255})"|«([^»\r\n]{1,255})»|“([^”\r\n]{1,255})”')


@dataclass(frozen=True)
class CalendarRequestParts:
    """Scheduling fields extracted without interpreting the task subject."""

    title: str
    clock: time | None
    recurrence_end_date: date | None
    recurring_forever: bool


def calendar_clock(message_text: str) -> time | None:
    match = _CLOCK.search(message_text or "")
    if match is None:
        return None
    return time(int(match.group(1)), int(match.group(2) or 0))


def _candidate_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def recurrence_end_date(message_text: str, *, start_date: date) -> date | None:
    normalized = (message_text or "").lower().replace("ё", "е")
    if re.search(r"\bдо\s+конца\s+(?:этой\s+)?недели\b", normalized):
        return start_date + timedelta(days=6 - start_date.weekday())
    if re.search(r"\bна\s+(?:одну\s+)?неделю\b", normalized):
        return start_date + timedelta(days=6)

    weekday_match = _WEEKDAY_END.search(normalized)
    if weekday_match is not None:
        target_weekday = _WEEKDAYS[weekday_match.group(1)]
        return start_date + timedelta(days=(target_weekday - start_date.weekday()) % 7)

    numeric_match = _NUMERIC_END.search(normalized)
    if numeric_match is not None:
        year_text = numeric_match.group(3)
        year = int(year_text) if year_text else start_date.year
        if year < 100:
            year += 2000
        candidate = _candidate_date(year, int(numeric_match.group(2)), int(numeric_match.group(1)))
        if candidate is not None and year_text is None and candidate < start_date:
            candidate = _candidate_date(year + 1, candidate.month, candidate.day)
        return candidate

    month_match = _MONTH_END.search(normalized)
    if month_match is not None:
        year_text = month_match.group(3)
        year = int(year_text) if year_text else start_date.year
        candidate = _candidate_date(year, _MONTHS[month_match.group(2)], int(month_match.group(1)))
        if candidate is not None and year_text is None and candidate < start_date:
            candidate = _candidate_date(year + 1, candidate.month, candidate.day)
        return candidate

    return None


def daily_recurrence(
    message_text: str,
    *,
    start_at: datetime,
    timezone_name: str,
    stored_end_date: date | None = None,
) -> list[str] | None:
    if _FOREVER.search(message_text or ""):
        return ["RRULE:FREQ=DAILY"]

    zone = ZoneInfo(timezone_name)
    local_start = start_at.astimezone(zone)
    end_date = recurrence_end_date(message_text, start_date=local_start.date()) or stored_end_date
    if end_date is None or end_date < local_start.date():
        return None
    final_occurrence = datetime.combine(end_date, local_start.timetz().replace(tzinfo=None), tzinfo=zone)
    until_utc = final_occurrence.astimezone(timezone.utc)
    return [f"RRULE:FREQ=DAILY;UNTIL={until_utc:%Y%m%dT%H%M%SZ}"]


def _clean_title_sentence(sentence: str) -> str:
    title = re.sub(r"\s+", " ", sentence).strip()
    title = re.sub(
        r"(?i)^\s*(?:(?:так|в\s+общем|короче|вот)\s*,?\s*)*"
        r"(?:а\s+)?(?:можешь\s+)?(?:мне\s*,?\s*)?(?:пожалуйста\s*,?\s*)?",
        "",
        title,
    )
    title = re.sub(
        r"(?i)^\s*(?:запиши|записать|добавь|добавить|создай|создать|поставь|поставить)\b"
        r"[\s,:—-]*(?:меня|мне|нам|нас)?[\s,:—-]*(?:пожалуйста)?[\s,:—-]*",
        "",
        title,
    )
    title = re.sub(r"(?i)\bв\s+календар\w*\b", " ", title)
    title = re.sub(r"(?i)\b(?:напомин\w*|задач\w*)\b", " ", title)
    title = re.sub(r"(?i)\b(?:на\s+)?(?:сегодня|завтра|послезавтра)\b", " ", title)
    title = re.sub(
        r"(?i)\b(?:на\s+)?каждый\s+день\b|\bежедневн\w*\b|"
        r"\bначиная\s+с\s+сегодня(?:шнего)?\s+дня\b|\bс\s+сегодня(?:шнего)?\s+дня\b|"
        r"\bвключая\s+сегодня(?:шний)?\s+день\b",
        " ",
        title,
    )
    title = re.sub(
        r"(?i)\b(?:по|до)\s+(?:конца\s+(?:этой\s+)?недели|"
        + "|".join(_WEEKDAYS)
        + r"|\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|\d{1,2}\s+(?:"
        + "|".join(_MONTHS)
        + r"))\b",
        " ",
        title,
    )
    title = re.sub(r"(?i)\bна\s+(?:одну\s+)?неделю\b", " ", title)
    title = _TIME_PHRASE.sub(" ", title)
    title = _CLOCK.sub(" ", title)
    title = _FOREVER.sub(" ", title)
    title = re.sub(
        r"(?i)\b(?:вот\s+)?чтобы\s+(?:типа\s+)?|"
        r"\bчтобы\s+я\s+не\s+забыл\b|\bдавай\b|\bвремя\b|"
        r"\bу\s+меня\s+было(?:\s+в\s+календар\w*)?\s+написано\b|"
        r"\bчто\s+мне\s+нужно\s+(?:проходить|изучать)\b",
        " ",
        title,
    )
    title = re.sub(r"(?i)\b(?:запиши|поставь|поставить)\b", " ", title)
    title = re.sub(r"(?i)^\s*на\s+", "", title)
    title = re.sub(r"\s*[,;:—-]+\s*", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" \t\n,.;:—-«»\"'")
    if title.lower() == "стрижку":
        return "стрижка"
    return title


def extract_quoted_title(message_text: str) -> str | None:
    matches = list(_QUOTED_TITLE.finditer(message_text or ""))
    for match in reversed(matches):
        raw_candidate = next((group for group in match.groups() if group is not None), "")
        candidate = re.sub(r"\s+", " ", raw_candidate).strip(" \t\n,.;:—-'«»")
        if candidate and re.search(r"[A-Za-zА-Яа-яЁёІіЇїЄє]", candidate):
            return candidate[:255]
    return None


def extract_calendar_title(message_text: str) -> str:
    quoted_title = extract_quoted_title(message_text)
    if quoted_title is not None:
        return quoted_title

    candidates: list[str] = []
    for sentence in re.split(r"[.!?]+", message_text or ""):
        candidate = _clean_title_sentence(sentence)
        if not candidate or not re.search(r"[A-Za-zА-Яа-яЁёІіЇїЄє]", candidate):
            continue
        if candidate.casefold() not in {item.casefold() for item in candidates}:
            candidates.append(candidate)
    return (candidates[0] if candidates else "Событие")[:255]


def parse_calendar_request(
    message_text: str,
    *,
    start_date: date | None = None,
) -> CalendarRequestParts:
    return CalendarRequestParts(
        title=extract_calendar_title(message_text),
        clock=calendar_clock(message_text),
        recurrence_end_date=(
            recurrence_end_date(message_text, start_date=start_date) if start_date is not None else None
        ),
        recurring_forever=_FOREVER.search(message_text or "") is not None,
    )

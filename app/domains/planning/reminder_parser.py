import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ParsedReminder:
    title: str
    trigger_times: tuple[datetime, ...]


_REMINDER_VERB = re.compile(r"\bнапом\w*\b", re.IGNORECASE)
_CLOCK = re.compile(r"\bв\s+([01]?\d|2[0-3])(?::([0-5]\d))?\b", re.IGNORECASE)
_RELATIVE = re.compile(
    r"\bчерез\s+(\d+)\s*"
    r"(минут(?:у|ы)?|мин|час(?:а|ов)?|день|дня|дней|недел(?:ю|и|ь))\b",
    re.IGNORECASE,
)
_NUMERIC_DATE = re.compile(
    r"(?<![\d./-])(0?[1-9]|[12]\d|3[01])[./-](0?[1-9]|1[0-2])"
    r"(?:[./-](\d{2}|\d{4}))?(?![\d./-])"
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
_MONTH_DATE = re.compile(
    r"\b(0?[1-9]|[12]\d|3[01])\s+(" + "|".join(_MONTHS) + r")(?:\s+(\d{4}))?\b",
    re.IGNORECASE,
)
_WEEKDAYS = {
    "понедельник": 0,
    "вторник": 1,
    "среду": 2,
    "четверг": 3,
    "пятницу": 4,
    "субботу": 5,
    "воскресенье": 6,
}
_WEEKDAY = re.compile(r"\bв\s+(" + "|".join(_WEEKDAYS) + r")\b", re.IGNORECASE)


def is_reminder_request(message_text: str) -> bool:
    return _REMINDER_VERB.search(message_text or "") is not None


def _clock(message_text: str, default_hour: int = 10) -> time:
    match = _CLOCK.search(message_text)
    if match is None:
        return time(default_hour, 0)
    return time(int(match.group(1)), int(match.group(2) or 0))


def _future_local_datetime(
    target_date: date,
    target_time: time,
    *,
    local_now: datetime,
    allow_next_year: bool = False,
) -> datetime:
    candidate = datetime.combine(target_date, target_time, tzinfo=local_now.tzinfo)
    if allow_next_year and candidate <= local_now:
        candidate = candidate.replace(year=candidate.year + 1)
    return candidate


def _clean_title_fragment(value: str) -> str:
    title = value
    title = re.sub(r"(?i)^\s*(?:мне|нам|пожалуйста)\b", "", title)
    title = re.sub(r"(?i)\b(?:мне|нам|тоже)\b", "", title)
    title = re.sub(r"(?i)\b(?:в\s+течени[еи]\s+недели|каждый\s+день\s+(?:в\s+течение\s+)?недели)\b", "", title)
    title = _RELATIVE.sub("", title)
    title = re.sub(r"(?i)\bчерез\s+(?:полчаса|час)\b", "", title)
    title = re.sub(r"(?i)\b(?:сегодня|завтра|послезавтра)\b", "", title)
    title = _WEEKDAY.sub("", title)
    title = _CLOCK.sub("", title)
    title = _NUMERIC_DATE.sub("", title)
    title = _MONTH_DATE.sub("", title)
    title = re.sub(r"(?i)^\s*(?:о\s+том,\s*что|что|чтобы|об)\s+", "", title)
    title = re.sub(r"\s+", " ", title).strip(" \t\n,.;:—-")
    return title


def _clean_title(message_text: str) -> str:
    verb = _REMINDER_VERB.search(message_text)
    if verb is None:
        title = _clean_title_fragment(message_text)
        return (title or "Семейное напоминание")[:255]

    marker = verb.group(0).lower().replace("ё", "е")
    suffix = _clean_title_fragment(message_text[verb.end() :])
    if re.fullmatch(r"напомин(?:ание|ания|анию|ании|анием)", marker):
        suffix = re.sub(r"(?i)^\s*(?:мне|нам|пожалуйста)\b[\s,:—-]*", "", suffix)
        suffix = re.sub(r"(?i)^(?:о|об|про)\s+", "", suffix)
        return (suffix or "Семейное напоминание")[:255]
    deictic_suffix = re.sub(
        r"(?i)^(?:(?:об|про)\s+)?(?:этом|это)$",
        "",
        suffix,
    ).strip(" \t\n,.;:—-")
    if deictic_suffix:
        return suffix[:255]

    prefix = message_text[: verb.start()]
    prefix = re.sub(r"(?i)\b(?:так\s+(?:шо|что)|и)\s*$", "", prefix)
    prefix = re.sub(r"(?i)^\s*(?:во[- ]вторых|во[- ]первых)[,.:\s]*", "", prefix)
    prefix = re.sub(r"\s+", " ", prefix).strip(" \t\n,.;:—-")
    title = prefix or suffix
    return (title or "Семейное напоминание")[:255]


def reminder_title(message_text: str) -> str:
    """Return the task portion without treating conversational filler as the title."""
    return _clean_title(message_text)


def _as_utc(values: list[datetime]) -> tuple[datetime, ...]:
    return tuple(value.astimezone(timezone.utc) for value in values)


def parse_reminder_request(
    message_text: str,
    *,
    timezone_name: str,
    now: datetime | None = None,
) -> ParsedReminder | None:
    """Parse supported Russian reminder phrases into deterministic UTC timestamps."""
    if not is_reminder_request(message_text):
        return None

    zone = ZoneInfo(timezone_name)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware.")
    local_now = current.astimezone(zone)
    normalized = message_text.lower().replace("ё", "е")
    title = _clean_title(message_text)
    target_clock = _clock(message_text)

    if re.search(r"\bкаждый\s+день\b.*\bнедел", normalized):
        values = [
            _future_local_datetime(local_now.date() + timedelta(days=offset), target_clock, local_now=local_now)
            for offset in range(1, 8)
        ]
        return ParsedReminder(title=title, trigger_times=_as_utc(values))

    if re.search(r"\bв\s+течени[еи]\s+недели\b", normalized):
        values = [
            _future_local_datetime(local_now.date() + timedelta(days=offset), target_clock, local_now=local_now)
            for offset in (1, 3, 6)
        ]
        return ParsedReminder(title=title, trigger_times=_as_utc(values))

    if re.search(r"\bчерез\s+полчаса\b", normalized):
        return ParsedReminder(
            title=title,
            trigger_times=(current.astimezone(timezone.utc) + timedelta(minutes=30),),
        )
    if re.search(r"\bчерез\s+час\b", normalized):
        return ParsedReminder(
            title=title,
            trigger_times=(current.astimezone(timezone.utc) + timedelta(hours=1),),
        )

    relative = _RELATIVE.search(normalized)
    if relative is not None:
        amount = int(relative.group(1))
        unit = relative.group(2)
        if unit.startswith(("мин",)):
            delta = timedelta(minutes=amount)
        elif unit.startswith("час"):
            delta = timedelta(hours=amount)
        elif unit.startswith(("день", "дня", "дней")):
            delta = timedelta(days=amount)
        else:
            delta = timedelta(weeks=amount)
        return ParsedReminder(title=title, trigger_times=(current.astimezone(timezone.utc) + delta,))

    day_offset = None
    if "послезавтра" in normalized:
        day_offset = 2
    elif "завтра" in normalized:
        day_offset = 1
    elif "сегодня" in normalized:
        day_offset = 0
    if day_offset is not None:
        value = _future_local_datetime(
            local_now.date() + timedelta(days=day_offset),
            target_clock,
            local_now=local_now,
        )
        if value <= local_now:
            return None
        return ParsedReminder(title=title, trigger_times=_as_utc([value]))

    numeric_date = _NUMERIC_DATE.search(normalized)
    if numeric_date is not None:
        year_text = numeric_date.group(3)
        year = int(year_text) if year_text else local_now.year
        if year < 100:
            year += 2000
        try:
            target_date = date(year, int(numeric_date.group(2)), int(numeric_date.group(1)))
        except ValueError:
            return None
        value = _future_local_datetime(
            target_date,
            target_clock,
            local_now=local_now,
            allow_next_year=year_text is None,
        )
        if value <= local_now:
            return None
        return ParsedReminder(title=title, trigger_times=_as_utc([value]))

    month_date = _MONTH_DATE.search(normalized)
    if month_date is not None:
        explicit_year = month_date.group(3)
        year = int(explicit_year) if explicit_year else local_now.year
        try:
            target_date = date(year, _MONTHS[month_date.group(2)], int(month_date.group(1)))
        except ValueError:
            return None
        value = _future_local_datetime(
            target_date,
            target_clock,
            local_now=local_now,
            allow_next_year=explicit_year is None,
        )
        if value <= local_now:
            return None
        return ParsedReminder(title=title, trigger_times=_as_utc([value]))

    weekday = _WEEKDAY.search(normalized)
    if weekday is not None:
        target_weekday = _WEEKDAYS[weekday.group(1)]
        days_ahead = (target_weekday - local_now.weekday()) % 7
        value = _future_local_datetime(
            local_now.date() + timedelta(days=days_ahead),
            target_clock,
            local_now=local_now,
        )
        if value <= local_now:
            value += timedelta(days=7)
        return ParsedReminder(title=title, trigger_times=_as_utc([value]))

    return None

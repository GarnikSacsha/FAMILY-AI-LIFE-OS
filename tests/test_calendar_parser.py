from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.orchestration.orchestrator import MainOrchestrator


@pytest.mark.parametrize(
    ("message_text", "expected"),
    [
        (
            "Добавь в календарь на 11.08 15:00 посмотреть видео",
            datetime(2026, 8, 11, 15, 0, tzinfo=ZoneInfo("Europe/Kyiv")),
        ),
        (
            "Добавь в календарь 11.08.2026 15.00 посмотреть видео",
            datetime(2026, 8, 11, 15, 0, tzinfo=ZoneInfo("Europe/Kyiv")),
        ),
        (
            "Добавь в календарь 11-08-2026 15-00 посмотреть видео",
            datetime(2026, 8, 11, 15, 0, tzinfo=ZoneInfo("Europe/Kyiv")),
        ),
    ],
)
def test_calendar_parser_accepts_explicit_day_month_and_time(
    message_text: str,
    expected: datetime,
) -> None:
    parsed = MainOrchestrator._parse_calendar_datetime(
        message_text,
        timezone_name="Europe/Kyiv",
        now=datetime(2026, 8, 2, 12, tzinfo=ZoneInfo("Europe/Kyiv")),
    )

    assert parsed == expected

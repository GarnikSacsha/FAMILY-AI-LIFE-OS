import pytest

from app.orchestration.orchestrator import MainOrchestrator
from app.telegram.bot import TELEGRAM_COMMAND_NAMES, normalize_telegram_command


def test_plain_tasks_command_routes_to_planner() -> None:
    assert MainOrchestrator.domain_for_message("/tasks") == "planner"


def test_tasks_command_mention_routes_to_planner() -> None:
    normalized, addressed_to_other_bot = normalize_telegram_command(
        "/tasks@familyhealtheee_bot",
        bot_username="familyhealtheee_bot",
    )

    assert not addressed_to_other_bot
    assert normalized == "/tasks"
    assert MainOrchestrator.domain_for_message(normalized) == "planner"


@pytest.mark.parametrize("command_name", sorted(TELEGRAM_COMMAND_NAMES))
def test_all_registered_command_mentions_are_normalized(command_name: str) -> None:
    normalized, addressed_to_other_bot = normalize_telegram_command(
        f"/{command_name}@familyhealtheee_bot",
        bot_username="familyhealtheee_bot",
    )

    assert not addressed_to_other_bot
    assert normalized == f"/{command_name}"


def test_tasks_command_for_another_bot_is_not_handled_as_tasks() -> None:
    message = "/tasks@other_bot"

    normalized, addressed_to_other_bot = normalize_telegram_command(
        message,
        bot_username="familyhealtheee_bot",
    )

    assert normalized == message
    assert addressed_to_other_bot
    assert MainOrchestrator._routing_text(message) == message
    assert MainOrchestrator.domain_for_message(message) != "planner"


def test_plain_text_with_at_sign_is_not_changed() -> None:
    message = "Позови меня @familyhealtheee_bot"

    assert MainOrchestrator._routing_text(message) == message

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.tools.google_tools import GoogleWorkspaceTools
from app.tools.mail_filter import classify_mail


@pytest.mark.parametrize(
    "headers",
    [
        {"list-id": "Weekly publication <weekly.example>"},
        {"list-unsubscribe": "<mailto:unsubscribe@example.org>"},
        {"precedence": "bulk"},
        {"auto-submitted": "auto-generated"},
    ],
)
@pytest.mark.parametrize("labels", [[], ["IMPORTANT"]])
def test_mass_mail_metadata_is_not_overridden_by_gmail_importance(headers, labels):
    message = {
        "subject": "How to Become a Graph Engineer",
        "from": "Author <author@example.org>",
        "snippet": "Learn about payment systems and career opportunities",
        "mail_headers": headers,
        "label_ids": labels,
    }
    assert classify_mail(message) is None


@pytest.mark.parametrize(
    "subject,sender",
    [
        ("Обновления приложений для Вашего устройства", "Apple <no_reply@email.apple.com>"),
        ("TehBeZ made updates in Cryptomann Academy", "Notion <notify@mail.notion.so>"),
    ],
)
def test_routine_automated_updates_do_not_inherit_importance(subject, sender):
    assert classify_mail({"subject": subject, "from": sender, "label_ids": ["IMPORTANT"]}) is None


@pytest.mark.parametrize(
    "subject",
    [
        "$24.95 payment to Example was unsuccessful again",
        "Your receipt for order 123",
        "Сповіщення системи безпеки",
        "Action Required! Some recordings will be deleted in 14 days",
        "Доставка Bolt Food",
        "Recruiter wants to schedule an interview",
    ],
)
def test_actionable_notices_survive_unsubscribe_headers(subject):
    result = classify_mail(
        {
            "subject": subject,
            "from": "Service <no_reply@service.example>",
            "mail_headers": {"list-unsubscribe": "<mailto:leave@example.org>"},
            "label_ids": ["IMPORTANT"],
        }
    )
    assert result is not None


def test_data_loss_warning_without_important_label_is_kept():
    result = classify_mail(
        {
            "subject": "Your recordings will be deleted in 14 days",
            "from": "Service <no-reply@service.example>",
            "mail_headers": {"auto-submitted": "auto-generated"},
        }
    )
    assert result is not None


def test_starred_newsletter_and_human_reply_are_preserved():
    assert (
        classify_mail(
            {
                "subject": "Weekly publication",
                "from": "Author <author@example.org>",
                "mail_headers": {"list-id": "publication"},
                "label_ids": ["STARRED"],
            }
        )
        is not None
    )
    assert (
        classify_mail(
            {"subject": "Lunch tomorrow?", "from": "Anna <anna@example.org>", "mail_headers": {"auto-submitted": "no"}}
        )
        is not None
    )


@pytest.mark.asyncio
async def test_gmail_metadata_reaches_filter_without_exposing_headers_in_results():
    def detail(subject, headers):
        return {
            "payload": {
                "headers": [
                    {"name": "Subject", "value": subject},
                    {"name": "From", "value": "Author <author@example.org>"},
                    *headers,
                ]
            },
            "labelIds": ["IMPORTANT"],
        }

    responses = [
        {"messages": [{"id": "newsletter"}, {"id": "personal"}]},
        detail("Linn's Leverage #108", [{"name": "LiSt-UnSuBsCrIbE", "value": "<mailto:leave@example.org>"}]),
        detail("Lunch tomorrow?", [{"name": "Auto-Submitted", "value": "no"}]),
    ]
    with (
        patch.object(GoogleWorkspaceTools, "get_valid_access_token", AsyncMock(return_value="test")),
        patch.object(GoogleWorkspaceTools, "_get_json", AsyncMock(side_effect=responses)),
    ):
        result = await GoogleWorkspaceTools.list_important_mail(object(), user_id=uuid.uuid4())
    assert [message["id"] for message in result] == ["personal"]
    assert "mail_headers" not in result[0]

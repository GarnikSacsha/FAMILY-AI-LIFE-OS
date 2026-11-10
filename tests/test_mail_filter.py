from typing import Any

import pytest

from app.tools.mail_filter import classify_mail


@pytest.mark.parametrize(
    "message",
    [
        {
            "subject": "5 new shows and movies to watch",
            "from": "IMDb <do-not-reply@imdb.com>",
            "snippet": "Your weekly recommendations",
        },
        {
            "subject": "50% OFF + 100% Profit Split & MORE",
            "from": "FundingTraders <help@fundingtraders.com>",
            "snippet": "Limited time offer",
        },
        {
            "subject": "Новые акции на Kasta",
            "from": "Kasta <order@kasta.ua>",
            "snippet": "Скидки до 50%",
        },
        {
            "subject": "Your bestsellers selected",
            "from": "AliExpress <ae-ug-ut-interest26@mail.aliexpress.com>",
            "snippet": "Recommended products for you",
        },
    ],
)
def test_mail_filter_rejects_obvious_promotional_noise(message: dict[str, str]) -> None:
    assert classify_mail(message) is None


@pytest.mark.parametrize(
    ("message", "category"),
    [
        (
            {
                "subject": "Payment received for your invoice",
                "from": "Billing <billing@service.example>",
                "snippet": "Your card was charged 49.00 USD",
            },
            "Деньги",
        ),
        (
            {
                "subject": "Recruiter wants to schedule an interview",
                "from": "Anna <anna@company.example>",
                "snippet": "Your application is moving forward",
            },
            "Карьера",
        ),
        (
            {
                "subject": "You have a new message from a recruiter",
                "from": "LinkedIn <messages-noreply@linkedin.com>",
                "snippet": "A recruiter sent you an opportunity",
            },
            "Карьера",
        ),
    ],
)
def test_mail_filter_keeps_finance_and_career_messages(
    message: dict[str, str],
    category: str,
) -> None:
    result = classify_mail(message)

    assert result is not None
    assert result.category == category


# Subjects visible in the supplied screenshot; senders are synthetic and the
# unavailable snippets/labels are not inferred from the real mailbox.
@pytest.mark.parametrize(
    "subject",
    [
        "Осінній фестиваль знижок до -50%",
        "Довгоочікувані гаджети від Apple у продажу з 25 вересня",
        "Don't miss this 7-day saving plan",
        "DOU Daily: повернення з Каліфорнії в український deftech",
    ],
    ids=["ukrainian-discount", "ukrainian-product-launch", "saving-plan-promo", "daily-newsletter"],
)
def test_screenshot_promotions_are_not_personal_mail(subject: str) -> None:
    assert classify_mail({"subject": subject, "from": "Alex <alex@shop.example>", "snippet": ""}) is None


@pytest.mark.parametrize(
    "message",
    [
        {
            "subject": "Маркетинг є. Стратегії нема. Знайомо?",
            "from": "Learning <info@learning.example>",
            "label_ids": ["INBOX", "IMPORTANT", "CATEGORY_PROMOTIONS"],
        },
        {
            "subject": "Просмотрите тенденции найма в компании Example",
            "from": "LinkedIn <messages-noreply@linkedin.com>",
            "label_ids": ["INBOX", "IMPORTANT"],
        },
    ],
    ids=["gmail-promotion-with-important-label", "hiring-trends-not-recruiter-contact"],
)
def test_important_label_does_not_promote_marketing_or_digest(message: dict[str, Any]) -> None:
    assert classify_mail(message) is None


@pytest.mark.parametrize(
    "sender",
    ["Updates <updates@service.example>", "Offers <bonus@shop.example>", "Service <do-not-reply@service.example>"],
    ids=["updates-address", "bonus-address", "do-not-reply-address"],
)
def test_automated_sender_is_not_evidence_of_personal_correspondence(sender: str) -> None:
    assert classify_mail({"subject": "What's new this week", "from": sender}) is None


@pytest.mark.parametrize(
    ("message", "category"),
    [
        (
            {"subject": "Обед завтра", "from": "Анна <anna@personal.example>", "snippet": "Встретимся в час?"},
            "Личное",
        ),
        (
            {"subject": "Project review tomorrow", "from": "Sam <sam@company.example>", "label_ids": ["IMPORTANT"]},
            "Важное",
        ),
        (
            {
                "subject": "Your payment receipt",
                "from": "Shop <updates@shop.example>",
                "snippet": "Payment received: 34 UAH. Discount applied.",
            },
            "Деньги",
        ),
        (
            {
                "subject": "New sign-in",
                "from": "Security <no-reply@accounts.example>",
                "label_ids": ["CATEGORY_PROMOTIONS"],
            },
            "Безопасность",
        ),
        (
            {"subject": "Recruiter wants to schedule an interview", "from": "Anna <anna@company.example>"},
            "Карьера",
        ),
        (
            {
                "subject": "50% discount",
                "from": "Shop <bonus@shop.example>",
                "label_ids": ["STARRED", "CATEGORY_PROMOTIONS"],
            },
            "Важное",
        ),
    ],
    ids=[
        "personal-reply",
        "important-work-mail",
        "receipt-with-discount",
        "security-mislabelled",
        "direct-recruiter",
        "explicit-user-star",
    ],
)
def test_filter_preserves_useful_mail_while_removing_promotions(message: dict[str, Any], category: str) -> None:
    result = classify_mail(message)
    assert result is not None
    assert result.category == category


@pytest.mark.parametrize(
    "message",
    [
        {"subject": "Насолоджуйтесь APR до 7%!", "from": "Binance <do_not_reply@mailer2.binance.com>"},
        {"subject": "Enjoy APR up to 7%!", "from": "Offers <info@exchange.example>", "label_ids": ["IMPORTANT"]},
        {
            "subject": "Маркетинг є. Стратегії нема. Знайомо? 🤔",
            "from": "Genius.Space <info@genius.space>",
            "label_ids": ["IMPORTANT"],
        },
        {"subject": "What's new this week", "from": "Service <do_not_reply@service.example>"},
        {
            "subject": "Python Engineer, New Grad в компании Jobright.ai",
            "from": "Оповещения о вакансиях LinkedIn <jobalerts-noreply@linkedin.com>",
            "label_ids": ["IMPORTANT"],
        },
        {
            "subject": "Senior Business Analyst в компании Avenga",
            "from": "Оповещения о вакансиях LinkedIn <jobalerts-noreply@linkedin.com>",
        },
        {
            "subject": "New job matches",
            "from": "LinkedIn <jobalerts-noreply@linkedin.com>",
            "snippet": "Paid leave and competitive compensation",
            "label_ids": ["IMPORTANT"],
        },
    ],
    ids=[
        "apr-ukrainian",
        "apr-important",
        "marketing-without-promotion-label",
        "underscore-no-reply",
        "linkedin-important-job-alert",
        "linkedin-job-alert",
        "job-alert-with-financial-word",
    ],
)
def test_followup_screenshot_noise_is_excluded_without_assumed_promotion_labels(message: dict[str, Any]) -> None:
    assert classify_mail(message) is None


@pytest.mark.parametrize(
    ("message", "category"),
    [
        (
            {
                "subject": "Action Required! Some recordings will be deleted in 14 days",
                "from": "Service <no-reply@recordings.example>",
                "label_ids": ["IMPORTANT"],
            },
            "Важное",
        ),
        (
            {
                "subject": "Сповіщення системи безпеки",
                "from": "Google <no-reply@accounts.google.com>",
                "label_ids": ["IMPORTANT"],
            },
            "Важное",
        ),
        ({"subject": "Payment received", "from": "Exchange <do_not_reply@exchange.example>"}, "Деньги"),
        (
            {
                "subject": "Your account APR changed to 7%",
                "from": "Bank <no_reply@bank.example>",
                "label_ids": ["IMPORTANT"],
            },
            "Важное",
        ),
        ({"subject": "A recruiter sent you a message", "from": "LinkedIn <messages-noreply@linkedin.com>"}, "Карьера"),
        ({"subject": "Interview invitation", "from": "Anna <anna@genius.space>"}, "Карьера"),
        (
            {
                "subject": "Новые вакансии",
                "from": "LinkedIn <jobalerts-noreply@linkedin.com>",
                "label_ids": ["STARRED", "IMPORTANT"],
            },
            "Важное",
        ),
    ],
    ids=[
        "deletion-warning",
        "security-warning",
        "automated-receipt",
        "apr-account-notice",
        "direct-linkedin-contact",
        "no-domain-blacklist",
        "starred-job-alert",
    ],
)
def test_followup_fix_preserves_direct_contacts_and_service_notices(message: dict[str, Any], category: str) -> None:
    result = classify_mail(message)
    assert result is not None
    assert result.category == category

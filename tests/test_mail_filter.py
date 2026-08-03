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

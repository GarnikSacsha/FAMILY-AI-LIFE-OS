import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MailImportance:
    category: str
    reason: str


_FINANCIAL_TRANSACTION_TERMS = (
    "charged",
    "charge",
    "payment",
    "paid",
    "receipt",
    "invoice",
    "billing",
    "statement",
    "transaction",
    "withdrawal",
    "deposit",
    "refund",
    "card ending",
    "списан",
    "списание",
    "оплат",
    "платеж",
    "платёж",
    "квитанц",
    "счет",
    "счёт",
    "транзакц",
    "карта",
    "банк",
    "возврат",
)
_CAREER_TERMS = (
    "recruiter",
    "recruiting",
    "hiring",
    "interview",
    "application",
    "applied",
    "candidate",
    "job offer",
    "offer letter",
    "screening",
    "assessment",
    "ваканс",
    "рекрутер",
    "рекрутинг",
    "собесед",
    "отклик",
    "кандидат",
    "найм",
    "интервью",
)
_CAREER_MESSAGE_TERMS = ("message", "месседж", "сообщени", "opportunity", "position", "роль", "позици")
_SECURITY_TERMS = (
    "security alert",
    "new sign-in",
    "login attempt",
    "password",
    "verification code",
    "подозрительн",
    "вход в аккаунт",
    "парол",
    "код подтвержд",
)
_PROMO_TERMS = (
    "% off",
    "discount",
    "sale",
    "profit split",
    "promo",
    "deal",
    "coupon",
    "скидк",
    "распродаж",
    "акци",
    "выгодн",
    "бесплатно",
    "бестселлер",
    "bestseller",
)
_NOISE_TERMS = (
    "new shows",
    "movies to watch",
    "top picks",
    "recommended for you",
    "recommendations",
    "newsletter",
    "digest",
    "top hires",
    "топ-найм",
    "подборка",
    "рекомендац",
    "новинки",
)
_TRUSTED_SERVICE_DOMAINS = ("openai.com", "linkedin.com", "linkedinmail.com")
_AUTOMATED_PREFIXES = ("no-reply", "noreply", "donotreply", "notifications", "notification", "mailer-daemon")


def _normalize(value: str) -> str:
    return " ".join((value or "").lower().replace("ё", "е").split())


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _sender_email(sender: str) -> str:
    match = re.search(r"<([^>]+)>", sender or "")
    return _normalize(match.group(1) if match else sender)


def _sender_domain(sender: str) -> str:
    email = _sender_email(sender)
    return email.rsplit("@", 1)[-1] if "@" in email else ""


def _looks_automated(sender: str) -> bool:
    email = _sender_email(sender)
    local_part = email.split("@", 1)[0]
    return local_part.startswith(_AUTOMATED_PREFIXES)


def classify_mail(message: dict[str, Any]) -> MailImportance | None:
    subject = _normalize(str(message.get("subject", "")))
    sender = str(message.get("from", ""))
    snippet = _normalize(str(message.get("snippet", "")))
    text = f"{subject} {sender.lower()} {snippet}"
    labels = {str(label).upper() for label in message.get("label_ids", []) or []}
    promo = _contains_any(text, _PROMO_TERMS)
    noise = _contains_any(text, _NOISE_TERMS)
    financial = _contains_any(text, _FINANCIAL_TRANSACTION_TERMS)
    career = _contains_any(text, _CAREER_TERMS)
    career_message = _contains_any(text, _CAREER_MESSAGE_TERMS)
    security = _contains_any(text, _SECURITY_TERMS)
    domain = _sender_domain(sender)

    if "STARRED" in labels:
        return MailImportance("Важное", "Письмо отмечено звездой в Gmail")
    if "IMPORTANT" in labels and not (promo or noise):
        return MailImportance("Важное", "Gmail пометил письмо как важное")
    if financial and not (promo and not _contains_any(text, ("receipt", "invoice", "payment", "оплат", "списан", "транзакц"))):
        return MailImportance("Деньги", "Платёж, списание, счёт или банковская операция")
    if career and not (promo or noise):
        return MailImportance("Карьера", "Вакансия, рекрутер, отклик или собеседование")
    if domain in _TRUSTED_SERVICE_DOMAINS and career_message and not (promo or noise):
        return MailImportance("Карьера", "Сообщение или контакт по карьерной теме")
    if security:
        return MailImportance("Безопасность", "Вход, пароль или подтверждение аккаунта")
    if domain in _TRUSTED_SERVICE_DOMAINS and _contains_any(text, _SECURITY_TERMS + _FINANCIAL_TRANSACTION_TERMS):
        return MailImportance("Сервисы", "Важное уведомление от подключённого сервиса")
    if not promo and not noise and not _looks_automated(sender) and sender.strip():
        return MailImportance("Личное", "Письмо от живого отправителя без рекламных признаков")
    return None

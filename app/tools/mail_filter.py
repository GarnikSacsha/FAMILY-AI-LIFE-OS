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
    "сповіщення системи безпеки",
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
    "зниж",
    "розпродаж",
    "у продажу",
    "don't miss this",
    "маркетинг є. стратегії нема. знайомо",
)
_APR_PROMO_RATE = re.compile(r"\bapr\b[^.!?\n]{0,40}\d+(?:[.,]\d+)?\s*%")
_APR_PROMO_CALL = re.compile(r"\b(?:enjoy|earn)\b|насолодж|заробля|отримайте|получите")
_LINKEDIN_JOB_ALERT = re.compile(r"^job[-_]?alerts?(?:[-_.+]|$)")
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
    "daily:",
    "hiring trends",
    "тенденции найма",
    "тенденції найму",
)
_TRUSTED_SERVICE_DOMAINS = ("openai.com", "linkedin.com", "linkedinmail.com")
_AUTOMATED_PREFIXES = (
    "no-reply",
    "noreply",
    "donotreply",
    "do-not-reply",
    "notifications",
    "notification",
    "mailer-daemon",
)
# Match these mailbox names exactly (including optional plus-addressing), so
# a person whose address merely starts with one of them is not excluded.
_BULK_MAILBOXES = {"updates", "bonus", "newsletter", "newsletters", "notify"}
_ACTIONABLE_MAIL_TERMS = (
    "receipt",
    "invoice",
    "payment received",
    "payment failed",
    "payment declined",
    "was charged",
    "refund",
    "квитанц",
    "списан",
    "оплата",
    "платеж отклонен",
    "security alert",
    "new sign-in",
    "login attempt",
    "password reset",
    "verification code",
    "сповіщення системи безпеки",
    "вход в аккаунт",
    "код подтвержд",
    "interview",
    "recruiter",
    "собесед",
    "рекрутер",
    "delivery",
    "доставк",
    "order confirmation",
    "замовлен",
    "apr changed",
    "rate changed",
)
_FAILED_PAYMENT = re.compile(r"\bpayment\b.{0,120}\b(?:unsuccessful|failed|declined)\b")
_DATA_LOSS_TERMS = ("will be deleted", "будут удалены", "буде видалено", "будуть видалені")


def _has_distribution_headers(message: dict[str, Any]) -> bool:
    raw = message.get("mail_headers")
    if not isinstance(raw, dict):
        return False
    headers = {_normalize(str(key)): _normalize(str(value)) for key, value in raw.items() if value}
    return bool(
        headers.get("list-id")
        or headers.get("list-unsubscribe")
        or headers.get("precedence", "").split(";", 1)[0].strip() in {"bulk", "list", "junk"}
        or headers.get("auto-submitted", "").split(";", 1)[0].strip() in {"auto-generated", "auto-replied"}
    )


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
    local_part = email.split("@", 1)[0].replace("_", "-")
    return local_part.startswith(_AUTOMATED_PREFIXES) or local_part.split("+", 1)[0] in _BULK_MAILBOXES


def classify_mail(message: dict[str, Any]) -> MailImportance | None:
    subject = _normalize(str(message.get("subject", "")))
    sender = str(message.get("from", ""))
    snippet = _normalize(str(message.get("snippet", "")))
    text = f"{subject} {sender.lower()} {snippet}"
    labels = {str(label).upper() for label in message.get("label_ids", []) or []}
    promo = (
        "CATEGORY_PROMOTIONS" in labels
        or _contains_any(text, _PROMO_TERMS)
        or bool(_APR_PROMO_RATE.search(subject) and _APR_PROMO_CALL.search(subject))
    )
    noise = _contains_any(text, _NOISE_TERMS)
    financial = _contains_any(text, _FINANCIAL_TRANSACTION_TERMS)
    career = _contains_any(text, _CAREER_TERMS)
    career_message = _contains_any(text, _CAREER_MESSAGE_TERMS)
    security = _contains_any(text, _SECURITY_TERMS)
    domain = _sender_domain(sender)
    data_loss_warning = _contains_any(subject, _DATA_LOSS_TERMS)
    actionable_notice = (
        _contains_any(subject, _ACTIONABLE_MAIL_TERMS) or bool(_FAILED_PAYMENT.search(subject)) or data_loss_warning
    )

    if "STARRED" in labels:
        return MailImportance("Важное", "Письмо отмечено звездой в Gmail")
    # Job matching broadcasts are not direct recruiter contacts, even if Gmail
    # marks them IMPORTANT or their snippet mentions paid leave or benefits.
    if domain in {"linkedin.com", "linkedinmail.com"} and _LINKEDIN_JOB_ALERT.match(_sender_email(sender)):
        return None
    # An unsubscribe link is a distribution signal, not proof of advertising.
    # Keep actionable notices based on the subject; generic words in a digest's
    # snippet must not promote that digest into finance or career mail.
    if (_has_distribution_headers(message) or _looks_automated(sender)) and not actionable_notice:
        return None
    if "IMPORTANT" in labels and not (promo or noise):
        return MailImportance("Важное", "Gmail пометил письмо как важное")
    if data_loss_warning:
        return MailImportance("Сервисы", "Предупреждение об удалении данных")
    if financial and not (
        promo and not _contains_any(text, ("receipt", "invoice", "payment", "оплат", "списан", "транзакц"))
    ):
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
        return MailImportance("Личное", "Не обнаружены признаки рекламы или автоматической рассылки")
    return None

import logging
import re
from typing import Any

REDACTED = "[REDACTED]"
_SENSITIVE_PAIR = re.compile(
    r"(?i)\b(token|secret|authorization|code|password|api[_-]?key)\b"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: REDACTED if _is_sensitive_key(str(key)) else redact(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        text = _BEARER.sub(f"Bearer {REDACTED}", value)
        return _SENSITIVE_PAIR.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text)
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in ("token", "secret", "authorization", "code", "password", "api_key"))


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.msg)
        if record.args:
            record.args = redact(record.args)
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)

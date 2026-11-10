import logging
from unittest.mock import patch

import main
from app.security.logging import RedactingFilter, redact


def test_token_parameters_in_text_are_redacted():
    message = "access_token=synthetic-access refresh_token=synthetic-refresh password=synthetic-password"
    result = redact(message)
    for value in ("synthetic-access", "synthetic-refresh", "synthetic-password"):
        assert value not in result


def test_startup_installs_redaction_on_application_and_access_logs():
    root = logging.getLogger()
    access = logging.getLogger("uvicorn.access")
    error = logging.getLogger("uvicorn.error")
    old_handlers, old_level = root.handlers[:], root.level
    old_access_filters, old_error_filters = access.filters[:], error.filters[:]
    try:
        with patch.object(main.uvicorn, "run"):
            main.main()
        assert any(isinstance(f, RedactingFilter) for h in root.handlers for f in h.filters)
        assert any(isinstance(f, RedactingFilter) for f in access.filters)
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            "%s %s",
            ("GET", "/oauth/oura/callback?code=synthetic-code&state=synthetic-state"),
            None,
        )
        for log_filter in access.filters:
            log_filter.filter(record)
        assert "synthetic-code" not in record.getMessage()
        assert "synthetic-state" not in record.getMessage()
    finally:
        root.handlers, root.level = old_handlers, old_level
        access.filters, error.filters = old_access_filters, old_error_filters

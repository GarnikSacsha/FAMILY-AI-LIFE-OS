import logging

import uvicorn

from app.api.application import app
from app.config.settings import settings


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    uvicorn.run(
        app,
        host=getattr(settings, "HTTP_HOST", "0.0.0.0"),  # noqa: S104 - configured container listener
        port=getattr(settings, "HTTP_PORT", 8000),
        log_level=settings.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()

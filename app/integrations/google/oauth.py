import logging
from typing import Any
from urllib.parse import urlencode

import aiohttp

from app.config.settings import settings

logger = logging.getLogger(__name__)

GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105  # nosec B105
GOOGLE_USER_SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
)


class GoogleOAuthError(Exception):
    """Safe Google OAuth error that never contains credentials or provider bodies."""


class GoogleOAuthClient:
    @classmethod
    def is_configured(cls) -> bool:
        return bool(
            settings.GOOGLE_OAUTH_CLIENT_ID
            and settings.GOOGLE_OAUTH_CLIENT_SECRET
            and settings.GOOGLE_OAUTH_REDIRECT_URI
        )

    @classmethod
    def _client_credentials(cls) -> tuple[str, str]:
        client_id = (settings.GOOGLE_OAUTH_CLIENT_ID or "").strip()
        client_secret_setting = settings.GOOGLE_OAUTH_CLIENT_SECRET
        client_secret = client_secret_setting.get_secret_value().strip() if client_secret_setting is not None else ""
        if not client_id or not client_secret:
            raise GoogleOAuthError("Google OAuth is not configured.")
        return client_id, client_secret

    @classmethod
    def get_authorization_url(cls, *, state: str, login_hint: str | None = None) -> str:
        client_id, _ = cls._client_credentials()
        if not state or len(state) < 32:
            raise GoogleOAuthError("A valid OAuth state is required.")

        parameters = {
            "client_id": client_id,
            "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(GOOGLE_USER_SCOPES),
            "state": state,
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
        }
        if login_hint:
            parameters["login_hint"] = login_hint
        return f"{GOOGLE_AUTHORIZATION_URL}?{urlencode(parameters)}"

    @classmethod
    async def _token_request(cls, payload: dict[str, str]) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=20)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(GOOGLE_TOKEN_URL, data=payload) as response:
                    if response.status != 200:
                        raise GoogleOAuthError("Google authorization is unavailable.")
                    data = await response.json()
        except GoogleOAuthError:
            raise
        except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
            logger.warning("Google token request failed (%s).", type(exc).__name__)
            raise GoogleOAuthError("Google authorization is unavailable.") from exc

        access_token = data.get("access_token")
        expires_in = data.get("expires_in")
        if not isinstance(access_token, str) or not access_token.strip():
            raise GoogleOAuthError("Google returned an invalid token response.")
        if not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in <= 0:
            raise GoogleOAuthError("Google returned an invalid token response.")
        return data

    @classmethod
    async def exchange_code_for_tokens(cls, code: str) -> dict[str, Any]:
        client_id, client_secret = cls._client_credentials()
        if not code or len(code) > 4096:
            raise GoogleOAuthError("Invalid Google authorization code.")
        return await cls._token_request(
            {
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
                "grant_type": "authorization_code",
            }
        )

    @classmethod
    async def refresh_access_token(cls, refresh_token: str) -> dict[str, Any]:
        client_id, client_secret = cls._client_credentials()
        if not refresh_token or len(refresh_token) > 4096:
            raise GoogleOAuthError("Invalid Google refresh token.")
        return await cls._token_request(
            {
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
            }
        )

import base64
import urllib.parse
from datetime import date
from typing import Any

import aiohttp

from app.config.settings import settings


class OuraOAuthError(Exception):
    """Safe OAuth error that never embeds provider response bodies or secrets."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class OuraClient:
    BASE_URL = "https://api.ouraring.com/v2/usercollection"
    # This is the provider endpoint, not a credential.
    TOKEN_URL = "https://api.ouraring.com/oauth/token"  # noqa: S105  # nosec B105
    AUTH_URL = "https://cloud.ouraring.com/oauth/authorize"
    DATE_COLLECTIONS = frozenset(
        {
            "daily_sleep",
            "daily_readiness",
            "daily_activity",
            "daily_spo2",
            "daily_stress",
            "sleep",
        }
    )

    @classmethod
    def get_authorization_url(cls, state: str) -> str:
        """Returns the OAuth2 authorization URL for Oura Ring."""
        client_id = (settings.OURA_CLIENT_ID or "").strip()
        redirect_uri = (settings.OURA_REDIRECT_URI or "").strip()
        if not client_id or not redirect_uri:
            raise OuraOAuthError("Oura OAuth is not configured.")
        if not state or not state.strip() or len(state) > 256:
            raise OuraOAuthError("A valid OAuth state is required.")

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": settings.OURA_SCOPES,
            "state": state,
        }
        return f"{cls.AUTH_URL}?{urllib.parse.urlencode(params)}"

    @classmethod
    def _client_authorization(cls) -> str:
        client_id = (settings.OURA_CLIENT_ID or "").strip()
        client_secret_setting = settings.OURA_CLIENT_SECRET
        if not client_id or client_secret_setting is None:
            raise OuraOAuthError("Oura OAuth is not configured.")
        client_secret = client_secret_setting.get_secret_value().strip()
        if not client_secret:
            raise OuraOAuthError("Oura OAuth is not configured.")
        credentials = f"{client_id}:{client_secret}".encode()
        return f"Basic {base64.b64encode(credentials).decode('ascii')}"

    @classmethod
    async def _token_request(cls, payload: dict[str, str]) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=10, connect=3)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    cls.TOKEN_URL,
                    data=payload,
                    headers={"Authorization": cls._client_authorization()},
                ) as response:
                    if response.status != 200:
                        raise OuraOAuthError(
                            "Oura token request was rejected.",
                            status_code=response.status,
                        )
                    try:
                        token_data = await response.json()
                    except (aiohttp.ContentTypeError, ValueError) as exc:
                        raise OuraOAuthError("Oura returned an invalid token response.") from exc
        except OuraOAuthError:
            raise
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise OuraOAuthError("Oura token exchange is unavailable.") from exc

        if not isinstance(token_data, dict):
            raise OuraOAuthError("Oura returned an invalid token response.")
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in")
        if not isinstance(access_token, str) or not access_token.strip():
            raise OuraOAuthError("Oura returned an invalid token response.")
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            raise OuraOAuthError("Oura returned an invalid token response.")
        if isinstance(expires_in, int) and not isinstance(expires_in, bool):
            expires_in_value = expires_in
        elif isinstance(expires_in, str) and expires_in.isdigit():
            expires_in_value = int(expires_in)
        else:
            raise OuraOAuthError("Oura returned an invalid token response.")
        if expires_in_value <= 0:
            raise OuraOAuthError("Oura returned an invalid token response.")

        token_data["expires_in"] = expires_in_value
        return token_data

    @classmethod
    async def exchange_code_for_tokens(cls, code: str) -> dict[str, Any]:
        """Exchange an authorization code for access and rotating refresh tokens."""
        if not code or not code.strip() or len(code) > 2048:
            raise OuraOAuthError("Invalid OAuth authorization code.")
        redirect_uri = (settings.OURA_REDIRECT_URI or "").strip()
        if not redirect_uri:
            raise OuraOAuthError("Oura OAuth is not configured.")
        return await cls._token_request(
            {
                "grant_type": "authorization_code",
                "code": code.strip(),
                "redirect_uri": redirect_uri,
            }
        )

    @classmethod
    async def refresh_access_token(cls, refresh_token: str) -> dict[str, Any]:
        """Use Oura's single-use refresh token and return its replacement."""
        if not refresh_token or not refresh_token.strip() or len(refresh_token) > 4096:
            raise OuraOAuthError("Invalid OAuth refresh token.")
        return await cls._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token.strip(),
            }
        )

    @classmethod
    async def get_daily_collection(
        cls,
        collection: str,
        *,
        access_token: str,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        """Read one allowlisted date-based Oura V2 collection without leaking responses."""
        if collection not in cls.DATE_COLLECTIONS:
            raise ValueError("Unsupported Oura date collection.")
        if not access_token or not access_token.strip():
            raise OuraOAuthError("Oura access token is unavailable.")
        if end_date < start_date:
            raise ValueError("Oura end_date must not precede start_date.")

        timeout = aiohttp.ClientTimeout(total=15, connect=3)
        url = f"{cls.BASE_URL}/{collection}"
        params = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        headers = {"Authorization": f"Bearer {access_token.strip()}"}
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url,
                    params=params,
                    headers=headers,
                ) as response:
                    if response.status != 200:
                        raise OuraOAuthError(
                            "Oura data request failed.",
                            status_code=response.status,
                        )
                    try:
                        payload = await response.json()
                    except (aiohttp.ContentTypeError, ValueError) as exc:
                        raise OuraOAuthError("Oura returned an invalid data response.") from exc
        except OuraOAuthError:
            raise
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise OuraOAuthError("Oura data request is unavailable.") from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise OuraOAuthError("Oura returned an invalid data response.")
        return payload

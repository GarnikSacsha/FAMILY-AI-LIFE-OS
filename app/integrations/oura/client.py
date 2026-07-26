import base64
import urllib.parse
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
    async def exchange_code_for_tokens(cls, code: str) -> dict[str, Any]:
        """Exchanges authorization code for access and refresh tokens."""
        if not code or not code.strip() or len(code) > 2048:
            raise OuraOAuthError("Invalid OAuth authorization code.")

        client_id = (settings.OURA_CLIENT_ID or "").strip()
        client_secret_setting = settings.OURA_CLIENT_SECRET
        redirect_uri = (settings.OURA_REDIRECT_URI or "").strip()
        if (
            not client_id
            or client_secret_setting is None
            or not client_secret_setting.get_secret_value().strip()
            or not redirect_uri
        ):
            raise OuraOAuthError("Oura OAuth is not configured.")

        payload = {
            "grant_type": "authorization_code",
            "code": code.strip(),
            "redirect_uri": redirect_uri,
        }
        timeout = aiohttp.ClientTimeout(total=10, connect=3)
        credentials = f"{client_id}:{client_secret_setting.get_secret_value()}".encode()
        authorization = f"Basic {base64.b64encode(credentials).decode('ascii')}"

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    cls.TOKEN_URL,
                    data=payload,
                    headers={"Authorization": authorization},
                ) as response:
                    if response.status != 200:
                        raise OuraOAuthError(
                            "Oura token exchange was rejected.",
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

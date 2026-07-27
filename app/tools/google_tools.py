import asyncio
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import OAuthToken
from app.integrations.google.oauth import GoogleOAuthClient, GoogleOAuthError
from app.security.token_cipher import TokenCipher, get_token_cipher

logger = logging.getLogger(__name__)


class GoogleWorkspaceError(Exception):
    """Safe integration failure suitable for user-facing fallback handling."""

    def __init__(self, message: str, *, error_code: str = "GOOGLE_WORKSPACE_ERROR") -> None:
        super().__init__(message)
        self.error_code = error_code


class GoogleWorkspaceTools:
    @staticmethod
    async def save_google_tokens(
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        tokens: Mapping[str, Any],
        cipher: TokenCipher | None = None,
        require_refresh_token: bool = True,
    ) -> None:
        if not isinstance(user_id, uuid.UUID):
            raise ValueError("Google tokens require a valid internal user_id.")

        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        expires_in = tokens.get("expires_in")
        if not isinstance(access_token, str) or not access_token.strip():
            raise ValueError("Google access token is missing.")
        if require_refresh_token and (not isinstance(refresh_token, str) or not refresh_token.strip()):
            raise ValueError("Google refresh token is missing; reconnect with consent.")
        if not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in <= 0:
            raise ValueError("Google expires_in must be a positive integer.")

        scope_value = tokens.get("scope")
        scope = str(scope_value).strip() if scope_value else None
        token_cipher = cipher or get_token_cipher()
        encrypted_access = token_cipher.encrypt(
            access_token.strip(),
            user_id=user_id,
            provider="google",
            token_type="access_token",  # noqa: S106  # nosec B106
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        result = await session.execute(
            select(OAuthToken)
            .where(
                OAuthToken.user_id == user_id,
                OAuthToken.provider == "google",
            )
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None:
            if not isinstance(refresh_token, str) or not refresh_token.strip():
                raise ValueError("Google refresh token is required for a new connection.")
            record = OAuthToken(
                user_id=user_id,
                provider="google",
                access_token_encrypted=encrypted_access,
                refresh_token_encrypted=token_cipher.encrypt(
                    refresh_token.strip(),
                    user_id=user_id,
                    provider="google",
                    token_type="refresh_token",  # noqa: S106  # nosec B106
                ),
                expires_at=expires_at,
                scope=scope,
            )
            session.add(record)
        else:
            record.access_token_encrypted = encrypted_access
            if isinstance(refresh_token, str) and refresh_token.strip():
                record.refresh_token_encrypted = token_cipher.encrypt(
                    refresh_token.strip(),
                    user_id=user_id,
                    provider="google",
                    token_type="refresh_token",  # noqa: S106  # nosec B106
                )
            record.expires_at = expires_at
            if scope:
                record.scope = scope
        await session.flush()

    @staticmethod
    async def get_valid_access_token(
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        cipher: TokenCipher | None = None,
    ) -> str:
        token_cipher = cipher or get_token_cipher()
        result = await session.execute(
            select(OAuthToken)
            .where(
                OAuthToken.user_id == user_id,
                OAuthToken.provider == "google",
            )
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise GoogleWorkspaceError("Google account is not connected.")

        now = datetime.now(timezone.utc)
        expires_at = record.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at is not None and expires_at > now + timedelta(seconds=60):
            return token_cipher.decrypt(
                record.access_token_encrypted,
                user_id=user_id,
                provider="google",
                token_type="access_token",  # noqa: S106  # nosec B106
            )

        if not record.refresh_token_encrypted:
            raise GoogleWorkspaceError("Google account must be reconnected.")
        refresh_token = token_cipher.decrypt(
            record.refresh_token_encrypted,
            user_id=user_id,
            provider="google",
            token_type="refresh_token",  # noqa: S106  # nosec B106
        )
        try:
            refreshed = await GoogleOAuthClient.refresh_access_token(refresh_token)
        except GoogleOAuthError as exc:
            raise GoogleWorkspaceError("Google account must be reconnected.") from exc
        await GoogleWorkspaceTools.save_google_tokens(
            session,
            user_id=user_id,
            tokens=refreshed,
            cipher=token_cipher,
            require_refresh_token=False,
        )
        return str(refreshed["access_token"])

    @staticmethod
    async def _get_json(
        url: str,
        *,
        access_token: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=15)
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            async with aiohttp.ClientSession(timeout=timeout) as client:
                async with client.get(url, headers=headers, params=params) as response:
                    if response.status != 200:
                        error_code = {
                            401: "GOOGLE_TOKEN_INVALID",
                            403: "GOOGLE_PERMISSION_OR_API_DISABLED",
                        }.get(response.status, f"GOOGLE_HTTP_{response.status}")
                        raise GoogleWorkspaceError(
                            "Google Workspace request failed.",
                            error_code=error_code,
                        )
                    payload = await response.json()
        except GoogleWorkspaceError:
            raise
        except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
            logger.warning("Google Workspace request failed (%s).", type(exc).__name__)
            raise GoogleWorkspaceError("Google Workspace request failed.") from exc
        if not isinstance(payload, dict):
            raise GoogleWorkspaceError("Google Workspace returned an invalid response.")
        return payload

    @staticmethod
    async def _require_calendar_scope(
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
    ) -> None:
        result = await session.execute(
            select(OAuthToken.scope).where(
                OAuthToken.user_id == user_id,
                OAuthToken.provider == "google",
            )
        )
        scope_value = result.scalar_one_or_none()
        if scope_value is None:
            raise GoogleWorkspaceError(
                "Google account is not connected.",
                error_code="GOOGLE_NOT_CONNECTED",
            )
        scopes = set(scope_value.split())
        calendar_scopes = {
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/calendar.readonly",
        }
        if scopes.isdisjoint(calendar_scopes):
            raise GoogleWorkspaceError(
                "Google Calendar permission is missing.",
                error_code="GOOGLE_CALENDAR_SCOPE_MISSING",
            )

    @staticmethod
    async def list_recent_mail(
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        limit: int = 5,
    ) -> list[dict[str, str]]:
        access_token = await GoogleWorkspaceTools.get_valid_access_token(
            session,
            user_id=user_id,
        )
        listing = await GoogleWorkspaceTools._get_json(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            access_token=access_token,
            params={"maxResults": str(max(1, min(limit, 10))), "q": "in:inbox"},
        )
        messages = listing.get("messages")
        if not isinstance(messages, list):
            return []

        async def load_message(message_id: str) -> dict[str, str]:
            payload = await GoogleWorkspaceTools._get_json(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                access_token=access_token,
                params={"format": "metadata"},
            )
            headers = payload.get("payload", {}).get("headers", [])
            values = {
                str(item.get("name", "")).lower(): str(item.get("value", ""))
                for item in headers
                if isinstance(item, dict)
            }
            return {
                "id": message_id,
                "subject": values.get("subject", "(без темы)"),
                "from": values.get("from", "неизвестный отправитель"),
                "date": values.get("date", ""),
                "snippet": str(payload.get("snippet", "")),
            }

        ids = [str(item["id"]) for item in messages if isinstance(item, dict) and item.get("id")]
        return list(await asyncio.gather(*(load_message(message_id) for message_id in ids)))

    @staticmethod
    async def list_upcoming_events(
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        limit: int = 5,
    ) -> list[dict[str, str]]:
        await GoogleWorkspaceTools._require_calendar_scope(
            session,
            user_id=user_id,
        )
        access_token = await GoogleWorkspaceTools.get_valid_access_token(
            session,
            user_id=user_id,
        )
        payload = await GoogleWorkspaceTools._get_json(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            access_token=access_token,
            params={
                "maxResults": str(max(1, min(limit, 10))),
                "singleEvents": "true",
                "orderBy": "startTime",
                "timeMin": datetime.now(timezone.utc).isoformat(),
            },
        )
        items = payload.get("items")
        if not isinstance(items, list):
            return []
        return [
            {
                "id": str(item.get("id", "")),
                "summary": str(item.get("summary", "(без названия)")),
                "start": str(item.get("start", {}).get("dateTime") or item.get("start", {}).get("date") or ""),
            }
            for item in items
            if isinstance(item, dict)
        ]

    @staticmethod
    async def _calendar_request(
        method: str,
        url: str,
        *,
        access_token: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=15)
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            async with aiohttp.ClientSession(timeout=timeout) as client:
                async with client.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                ) as response:
                    if response.status not in {200, 204}:
                        error_code = {
                            401: "GOOGLE_TOKEN_INVALID",
                            403: "GOOGLE_PERMISSION_OR_API_DISABLED",
                            404: "GOOGLE_CALENDAR_EVENT_NOT_FOUND",
                        }.get(response.status, f"GOOGLE_HTTP_{response.status}")
                        raise GoogleWorkspaceError(
                            "Google Calendar request failed.",
                            error_code=error_code,
                        )
                    if response.status == 204:
                        return {}
                    payload = await response.json()
        except GoogleWorkspaceError:
            raise
        except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
            raise GoogleWorkspaceError(
                "Google Calendar request failed.",
                error_code="GOOGLE_CALENDAR_UNAVAILABLE",
            ) from exc
        if not isinstance(payload, dict):
            raise GoogleWorkspaceError(
                "Google Calendar returned an invalid response.",
                error_code="GOOGLE_CALENDAR_INVALID_RESPONSE",
            )
        return payload

    @staticmethod
    async def create_calendar_event(
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        summary: str,
        start_at: datetime,
        end_at: datetime,
        timezone_name: str,
    ) -> dict[str, str]:
        await GoogleWorkspaceTools._require_calendar_scope(session, user_id=user_id)
        access_token = await GoogleWorkspaceTools.get_valid_access_token(session, user_id=user_id)
        payload = await GoogleWorkspaceTools._calendar_request(
            "POST",
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            access_token=access_token,
            json_body={
                "summary": summary,
                "start": {"dateTime": start_at.isoformat(), "timeZone": timezone_name},
                "end": {"dateTime": end_at.isoformat(), "timeZone": timezone_name},
            },
        )
        return {
            "id": str(payload.get("id", "")),
            "summary": str(payload.get("summary", summary)),
            "start": str(payload.get("start", {}).get("dateTime", start_at.isoformat())),
        }

    @staticmethod
    async def delete_calendar_event(
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        event_id: str,
    ) -> None:
        await GoogleWorkspaceTools._require_calendar_scope(session, user_id=user_id)
        access_token = await GoogleWorkspaceTools.get_valid_access_token(session, user_id=user_id)
        await GoogleWorkspaceTools._calendar_request(
            "DELETE",
            f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
            access_token=access_token,
        )

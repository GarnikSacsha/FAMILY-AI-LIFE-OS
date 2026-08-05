import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from app.config.settings import settings

logger = logging.getLogger(__name__)

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


class GoogleSheetsError(Exception):
    """Safe Sheets error that never contains credentials or provider bodies."""


class GoogleSheetsClient:
    @classmethod
    def is_configured(cls) -> bool:
        return bool(
            settings.GOOGLE_SHEETS_SPREADSHEET_ID
            and (settings.GOOGLE_CREDENTIALS_JSON or settings.GOOGLE_CREDENTIALS_JSON_PATH)
        )

    @classmethod
    def _credentials_info(cls) -> dict[str, Any]:
        raw_credentials = settings.GOOGLE_CREDENTIALS_JSON
        if raw_credentials is not None:
            raw_value = raw_credentials.get_secret_value()
        elif settings.GOOGLE_CREDENTIALS_JSON_PATH:
            path = Path(settings.GOOGLE_CREDENTIALS_JSON_PATH).expanduser()
            try:
                raw_value = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise GoogleSheetsError("Google Sheets credentials are unavailable.") from exc
        else:
            raise GoogleSheetsError("Google Sheets credentials are not configured.")

        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise GoogleSheetsError("Google Sheets credentials are invalid.") from exc
        if not isinstance(value, dict) or value.get("type") != "service_account":
            raise GoogleSheetsError("Google Sheets requires service-account credentials.")
        return value

    @classmethod
    async def _access_token(cls) -> str:
        try:
            credentials = service_account.Credentials.from_service_account_info(
                cls._credentials_info(),
                scopes=[SHEETS_SCOPE],
            )
            await asyncio.to_thread(credentials.refresh, Request())
        except GoogleSheetsError:
            raise
        except Exception as exc:
            logger.warning("Google Sheets authentication failed (%s).", type(exc).__name__)
            raise GoogleSheetsError("Google Sheets authentication failed.") from exc
        if not credentials.token:
            raise GoogleSheetsError("Google Sheets authentication returned no token.")
        return credentials.token

    @classmethod
    async def _request(
        cls,
        method: str,
        url: str,
        *,
        access_token: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=20)
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            async with aiohttp.ClientSession(timeout=timeout) as client:
                async with client.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                ) as response:
                    if response.status not in (200, 201):
                        raise GoogleSheetsError("Google Sheets request failed.")
                    payload = await response.json()
        except GoogleSheetsError:
            raise
        except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
            logger.warning("Google Sheets request failed (%s).", type(exc).__name__)
            raise GoogleSheetsError("Google Sheets request failed.") from exc
        if not isinstance(payload, dict):
            raise GoogleSheetsError("Google Sheets returned an invalid response.")
        return payload

    @classmethod
    async def append_transaction(cls, values: list[str]) -> str:
        try:
            async with asyncio.timeout(settings.GOOGLE_SHEETS_OPERATION_TIMEOUT_SECONDS):
                return await cls._append_transaction(values)
        except TimeoutError as exc:
            raise GoogleSheetsError("Google Sheets operation timed out.") from exc

    @classmethod
    async def _append_transaction(cls, values: list[str]) -> str:
        if len(values) != 9:
            raise ValueError("Google Sheets transaction rows require exactly 9 values.")
        spreadsheet_id = (settings.GOOGLE_SHEETS_SPREADSHEET_ID or "").strip()
        range_name = settings.GOOGLE_SHEETS_RANGE.strip()
        if not spreadsheet_id or not range_name:
            raise GoogleSheetsError("Google Sheets is not configured.")

        access_token = await cls._access_token()
        encoded_sheet = quote(spreadsheet_id, safe="")
        encoded_range = quote(range_name, safe="")
        values_url = f"https://sheets.googleapis.com/v4/spreadsheets/{encoded_sheet}/values/{encoded_range}"

        existing = await cls._request("GET", values_url, access_token=access_token)
        rows = existing.get("values", [])
        transaction_id = values[-1]
        if isinstance(rows, list) and any(
            isinstance(row, list) and len(row) >= 9 and str(row[8]) == transaction_id for row in rows
        ):
            verified_range = existing.get("range")
            return str(verified_range) if verified_range else range_name

        append_url = f"{values_url}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
        appended = await cls._request(
            "POST",
            append_url,
            access_token=access_token,
            json_body={"majorDimension": "ROWS", "values": [values]},
        )
        updates = appended.get("updates")
        if (
            not isinstance(updates, dict)
            or int(updates.get("updatedRows", 0)) < 1
            or not isinstance(updates.get("updatedRange"), str)
        ):
            raise GoogleSheetsError("Google Sheets did not confirm an inserted row.")

        verified = await cls._request("GET", values_url, access_token=access_token)
        verified_rows = verified.get("values", [])
        if not isinstance(verified_rows, list) or not any(
            isinstance(row, list) and len(row) >= 9 and str(row[8]) == transaction_id for row in verified_rows
        ):
            raise GoogleSheetsError("Google Sheets row verification failed.")
        return str(updates["updatedRange"])

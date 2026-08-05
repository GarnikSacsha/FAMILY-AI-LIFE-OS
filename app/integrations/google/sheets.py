import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from app.config.settings import settings

logger = logging.getLogger(__name__)

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
MONTHLY_RECEIPTS_SHEET = "__family_ai_sync_receipts"
MONTHLY_TEMPLATE_VERSION = "monthly_budget/v1"
KYIV_TIMEZONE = ZoneInfo("Europe/Kyiv")
MONTHLY_BUDGET_HEADERS = (
    "Продукты",
    "Квартира / ежемесячные платежи",
    "Медицина",
    "Одежда",
    "Отдых",
    "Уход / спорт",
    "Транспорт",
    "Быт",
    "Булка / Долли",
    "Другое",
)
MONTHLY_CATEGORY_HEADERS = {
    "Groceries": "Продукты",
    "Restaurants": "Продукты",
    "Utilities": "Квартира / ежемесячные платежи",
    "Health": "Медицина",
    "Shopping": "Одежда",
    "Entertainment": "Отдых",
    "Sports": "Уход / спорт",
    "Transport": "Транспорт",
    "Pets": "Булка / Долли",
}


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
    def projection_key(cls) -> str:
        spreadsheet_id = (settings.GOOGLE_SHEETS_SPREADSHEET_ID or "").strip()
        if settings.GOOGLE_SHEETS_LAYOUT == "monthly_budget":
            period = settings.GOOGLE_SHEETS_PERIOD or ""
            return f"monthly_budget:{spreadsheet_id}:{settings.GOOGLE_SHEETS_MONTHLY_SHEET_ID}:{period}"
        return f"ledger:{spreadsheet_id}:{settings.GOOGLE_SHEETS_RANGE.strip()}"

    @classmethod
    def monthly_budget_category(cls, category: str) -> str:
        return MONTHLY_CATEGORY_HEADERS.get(category.strip(), "Другое")

    @classmethod
    async def project_monthly_budget_expense(
        cls,
        *,
        transaction_id: str,
        occurred_at: datetime,
        category: str,
        amount: str,
    ) -> str:
        """Append one immutable receipt row that the visible month grid sums.

        The stable transaction ID is stored in a hidden worksheet. A retry first
        finds that ID, so a lost HTTP response cannot add the expense twice.
        """
        try:
            async with asyncio.timeout(settings.GOOGLE_SHEETS_OPERATION_TIMEOUT_SECONDS):
                return await cls._project_monthly_budget_expense(
                    transaction_id=transaction_id,
                    occurred_at=occurred_at,
                    category=category,
                    amount=amount,
                )
        except TimeoutError as exc:
            raise GoogleSheetsError("Google Sheets operation timed out.") from exc

    @classmethod
    async def _project_monthly_budget_expense(
        cls,
        *,
        transaction_id: str,
        occurred_at: datetime,
        category: str,
        amount: str,
    ) -> str:
        spreadsheet_id = (settings.GOOGLE_SHEETS_SPREADSHEET_ID or "").strip()
        period = settings.GOOGLE_SHEETS_PERIOD
        if not spreadsheet_id or period is None:
            raise GoogleSheetsError("Google Sheets monthly budget is not configured.")
        try:
            normalized_amount = Decimal(amount)
        except (InvalidOperation, ValueError) as exc:
            raise GoogleSheetsError("Google Sheets received an invalid expense amount.") from exc
        if normalized_amount <= 0:
            raise GoogleSheetsError("Google Sheets received an invalid expense amount.")

        access_token = await cls._access_token()
        encoded_sheet = quote(spreadsheet_id, safe="")
        main_sheet_title = await cls._monthly_main_sheet_title(
            spreadsheet_id=encoded_sheet,
            access_token=access_token,
        )
        await cls._ensure_monthly_budget_template(
            spreadsheet_id=encoded_sheet,
            access_token=access_token,
            main_sheet_title=main_sheet_title,
            period=period,
        )

        receipts_range = cls._a1_range(MONTHLY_RECEIPTS_SHEET, "A:E")
        receipts_url = cls._values_url(encoded_sheet, receipts_range)
        existing = await cls._request("GET", receipts_url, access_token=access_token)
        rows = existing.get("values", [])
        if isinstance(rows, list) and any(
            isinstance(row, list) and row and str(row[0]) == transaction_id for row in rows[1:]
        ):
            return f"{MONTHLY_RECEIPTS_SHEET}!A:A"

        day = occurred_at.astimezone(KYIV_TIMEZONE).day
        values = [
            transaction_id,
            str(day),
            cls.monthly_budget_category(category),
            format(normalized_amount, "f"),
            period,
        ]
        append_url = f"{receipts_url}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
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
            raise GoogleSheetsError("Google Sheets did not confirm an inserted expense.")

        verified = await cls._request("GET", receipts_url, access_token=access_token)
        verified_rows = verified.get("values", [])
        if not isinstance(verified_rows, list) or not any(
            isinstance(row, list) and row and str(row[0]) == transaction_id for row in verified_rows[1:]
        ):
            raise GoogleSheetsError("Google Sheets expense verification failed.")
        return str(updates["updatedRange"])

    @classmethod
    def _values_url(cls, encoded_spreadsheet_id: str, range_name: str) -> str:
        return (
            f"https://sheets.googleapis.com/v4/spreadsheets/{encoded_spreadsheet_id}/values/"
            f"{quote(range_name, safe='')}"
        )

    @staticmethod
    def _a1_range(sheet_title: str, cell_range: str) -> str:
        escaped_title = sheet_title.replace("'", "''")
        return f"'{escaped_title}'!{cell_range}"

    @classmethod
    async def _monthly_main_sheet_title(cls, *, spreadsheet_id: str, access_token: str) -> str:
        metadata_url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
            "?fields=sheets(properties(sheetId,title,hidden,index))"
        )
        metadata = await cls._request("GET", metadata_url, access_token=access_token)
        sheets = metadata.get("sheets", [])
        if not isinstance(sheets, list):
            raise GoogleSheetsError("Google Sheets monthly template is invalid.")
        for sheet in sheets:
            properties = sheet.get("properties") if isinstance(sheet, dict) else None
            if (
                isinstance(properties, dict)
                and properties.get("sheetId") == settings.GOOGLE_SHEETS_MONTHLY_SHEET_ID
                and isinstance(properties.get("title"), str)
                and not properties.get("hidden", False)
            ):
                return properties["title"]
        raise GoogleSheetsError("Google Sheets monthly template sheet was not found.")

    @classmethod
    async def _ensure_monthly_budget_template(
        cls,
        *,
        spreadsheet_id: str,
        access_token: str,
        main_sheet_title: str,
        period: str,
    ) -> None:
        metadata_url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
            "?fields=sheets(properties(sheetId,title,hidden,index))"
        )
        metadata = await cls._request("GET", metadata_url, access_token=access_token)
        sheets = metadata.get("sheets", [])
        receipts_exists = any(
            isinstance(sheet, dict)
            and isinstance(sheet.get("properties"), dict)
            and sheet["properties"].get("title") == MONTHLY_RECEIPTS_SHEET
            for sheet in sheets
        )
        if not receipts_exists:
            batch_update_url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate"
            await cls._request(
                "POST",
                batch_update_url,
                access_token=access_token,
                json_body={
                    "requests": [
                        {
                            "addSheet": {
                                "properties": {
                                    "title": MONTHLY_RECEIPTS_SHEET,
                                    "hidden": True,
                                }
                            }
                        }
                    ]
                },
            )

        marker_range = cls._a1_range(MONTHLY_RECEIPTS_SHEET, "F1:G1")
        marker_url = cls._values_url(spreadsheet_id, marker_range)
        marker = await cls._request("GET", marker_url, access_token=access_token)
        marker_values = marker.get("values", [])
        marker_row = marker_values[0] if isinstance(marker_values, list) and marker_values else []
        if isinstance(marker_row, list) and marker_row[:2] == [MONTHLY_TEMPLATE_VERSION, period]:
            return

        header_range = cls._a1_range(main_sheet_title, "B2:K2")
        headers = await cls._request(
            "GET",
            cls._values_url(spreadsheet_id, header_range),
            access_token=access_token,
        )
        header_values = headers.get("values", [])
        if not isinstance(header_values, list) or header_values != [list(MONTHLY_BUDGET_HEADERS)]:
            raise GoogleSheetsError("Google Sheets monthly expense headers do not match the configured template.")

        formulas = cls._monthly_grid_formulas(period)
        update_url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values:batchUpdate"
        updated = await cls._request(
            "POST",
            update_url,
            access_token=access_token,
            json_body={
                "valueInputOption": "USER_ENTERED",
                "data": [
                    {
                        "range": cls._a1_range(main_sheet_title, "B3:K33"),
                        "majorDimension": "ROWS",
                        "values": formulas,
                    },
                    {
                        "range": cls._a1_range(MONTHLY_RECEIPTS_SHEET, "A1:G1"),
                        "majorDimension": "ROWS",
                        "values": [
                            [
                                "transaction_id",
                                "day",
                                "category",
                                "amount",
                                "period",
                                MONTHLY_TEMPLATE_VERSION,
                                period,
                            ]
                        ],
                    },
                ],
            },
        )
        if int(updated.get("totalUpdatedCells", 0)) < 317:
            raise GoogleSheetsError("Google Sheets did not configure the monthly budget template.")

    @classmethod
    def _monthly_grid_formulas(cls, period: str) -> list[list[str]]:
        receipts = cls._a1_range(MONTHLY_RECEIPTS_SHEET, "$A:$E").split("!", maxsplit=1)[0]
        formulas = []
        for _day in range(31):
            row = []
            for column in "BCDEFGHIJK":
                row.append(
                    f'=IFERROR(SUMIFS({receipts}!$D:$D,{receipts}!$B:$B,ROW()-2,'
                    f'{receipts}!$C:$C,{column}$2,{receipts}!$E:$E,"{period}"),0)'
                )
            formulas.append(row)
        return formulas

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

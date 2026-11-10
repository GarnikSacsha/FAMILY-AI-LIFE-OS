from unittest.mock import AsyncMock, patch

import pytest

from app.config.settings import settings
from app.integrations.google.sheets import GoogleSheetsClient


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", " \t=", "\r\n@", "\u00a0+"])
@pytest.mark.parametrize("column", [1, 2, 4, 5, 6, 7, 8])
@pytest.mark.asyncio
async def test_untrusted_ledger_text_is_literal(prefix, column, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_SPREADSHEET_ID", "synthetic-demo")
    row = [
        "2026-10-16T12:00:00+00:00",
        "groceries",
        "Продукты",
        "1350.50",
        "UAH",
        "expense",
        "manual",
        "household",
        "synthetic-id",
    ]
    row[column] = prefix + "1+1"
    original = row.copy()
    request = AsyncMock(
        side_effect=[
            {"values": []},
            {"updates": {"updatedRows": 1, "updatedRange": "A2:I2"}},
            {"values": [row]},
        ]
    )
    with (
        patch.object(GoogleSheetsClient, "_access_token", AsyncMock(return_value="synthetic")),
        patch.object(GoogleSheetsClient, "_request", request),
    ):
        await GoogleSheetsClient.append_transaction(row)
    call = request.await_args_list[1]
    written = call.kwargs["json_body"]["values"][0]
    assert written[column] == "'" + original[column]
    assert written[0] == original[0]
    assert written[3] == "1350.50"
    assert "USER_ENTERED" in call.args[1]
    assert row == original


@pytest.mark.parametrize("text", ["Продукты", "groceries", "'=already literal"])
@pytest.mark.asyncio
async def test_ordinary_ledger_labels_stay_unchanged(text, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_SPREADSHEET_ID", "synthetic-demo")
    row = ["2026-10-16", text, "Groceries", "350.00", "UAH", "expense", "manual", "household", "id"]
    request = AsyncMock(
        side_effect=[
            {"values": []},
            {"updates": {"updatedRows": 1, "updatedRange": "A2:I2"}},
            {"values": [row]},
        ]
    )
    with (
        patch.object(GoogleSheetsClient, "_access_token", AsyncMock(return_value="synthetic")),
        patch.object(GoogleSheetsClient, "_request", request),
    ):
        await GoogleSheetsClient.append_transaction(row)
    assert request.await_args_list[1].kwargs["json_body"]["values"] == [row]

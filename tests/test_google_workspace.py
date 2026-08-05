import base64
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr

from app.api.oauth import google_oauth_callback
from app.config.settings import settings
from app.infrastructure.integrations.google_sheets_worker import TransactionSyncItem
from app.integrations.google.oauth import GoogleOAuthClient
from app.integrations.google.sheets import MONTHLY_BUDGET_HEADERS, GoogleSheetsClient
from app.security.token_cipher import TokenCipher
from app.tools.google_tools import GoogleWorkspaceTools


def _session_returning(record=None):
    result = Mock()
    result.scalar_one_or_none.return_value = record
    session = Mock()
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    session.add = Mock()
    return session


def test_google_authorization_url_requests_offline_user_scopes(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", SecretStr("client-secret"))
    monkeypatch.setattr(
        settings,
        "GOOGLE_OAUTH_REDIRECT_URI",
        "https://family.example.com/oauth/google/callback",
    )

    url = GoogleOAuthClient.get_authorization_url(state="s" * 48)
    query = parse_qs(urlparse(url).query)

    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["redirect_uri"] == ["https://family.example.com/oauth/google/callback"]
    scopes = set(query["scope"][0].split())
    assert "https://www.googleapis.com/auth/gmail.readonly" in scopes
    assert "https://www.googleapis.com/auth/gmail.compose" in scopes
    assert "https://www.googleapis.com/auth/calendar.events" in scopes


@pytest.mark.asyncio
async def test_google_callback_saves_tokens_for_state_bound_user():
    session = AsyncMock()
    user_id = uuid.uuid4()
    tokens = {
        "access_token": "access",
        "refresh_token": "refresh",
        "expires_in": 3600,
        "scope": "openid email",
    }
    with (
        patch(
            "app.api.oauth.OAuthStateManager.validate_and_consume_state",
            new=AsyncMock(return_value=user_id),
        ),
        patch(
            "app.api.oauth.GoogleOAuthClient.exchange_code_for_tokens",
            new=AsyncMock(return_value=tokens),
        ),
        patch(
            "app.api.oauth.GoogleWorkspaceTools.save_google_tokens",
            new=AsyncMock(),
        ) as save_tokens,
    ):
        response = await google_oauth_callback(
            code="valid-code",
            state="valid-state",
            error=None,
            session=session,
        )

    assert response.status_code == 200
    save_tokens.assert_awaited_once_with(
        session,
        user_id=user_id,
        tokens=tokens,
    )


@pytest.mark.asyncio
async def test_google_tokens_are_encrypted_per_user():
    session = _session_returning()
    user_id = uuid.uuid4()
    cipher = TokenCipher(base64.b64encode(b"g" * 32).decode("ascii"))

    await GoogleWorkspaceTools.save_google_tokens(
        session,
        user_id=user_id,
        tokens={
            "access_token": "plain-google-access",
            "refresh_token": "plain-google-refresh",
            "expires_in": 3600,
            "scope": "openid email",
        },
        cipher=cipher,
    )

    record = session.add.call_args.args[0]
    assert "plain-google-access" not in record.access_token_encrypted
    assert "plain-google-refresh" not in record.refresh_token_encrypted
    assert (
        cipher.decrypt(
            record.refresh_token_encrypted,
            user_id=user_id,
            provider="google",
            token_type="refresh_token",
        )
        == "plain-google-refresh"
    )


@pytest.mark.asyncio
async def test_sheets_append_skips_existing_transaction(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_SPREADSHEET_ID", "spreadsheet")
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_RANGE", "A:I")
    transaction_id = str(uuid.uuid4())
    request = AsyncMock(return_value={"values": [["", "", "", "", "", "", "", "", transaction_id]]})

    with (
        patch.object(GoogleSheetsClient, "_access_token", new=AsyncMock(return_value="token")),
        patch.object(GoogleSheetsClient, "_request", new=request),
    ):
        await GoogleSheetsClient.append_transaction([""] * 8 + [transaction_id])

    request.assert_awaited_once()
    assert request.await_args.args[0] == "GET"


@pytest.mark.asyncio
async def test_sheets_append_adds_new_transaction(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_SPREADSHEET_ID", "spreadsheet")
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_RANGE", "A:I")
    row = ["2026-07-26", "базар", "Groceries", "1900.00", "UAH", "expense", "manual", "household", "tx"]
    request = AsyncMock(
        side_effect=[
            {"values": []},
            {"updates": {"updatedRows": 1, "updatedRange": "Расходы!A27:I27"}},
            {"values": [row]},
        ]
    )

    with (
        patch.object(GoogleSheetsClient, "_access_token", new=AsyncMock(return_value="token")),
        patch.object(GoogleSheetsClient, "_request", new=request),
    ):
        updated_range = await GoogleSheetsClient.append_transaction(row)

    assert updated_range == "Расходы!A27:I27"
    assert request.await_count == 3
    assert request.await_args_list[1].args[0] == "POST"
    assert request.await_args_list[1].kwargs["json_body"]["values"] == [row]


def test_monthly_budget_category_mapping_and_projection_key(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_SPREADSHEET_ID", "monthly-sheet")
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_LAYOUT", "monthly_budget")
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_PERIOD", "2026-08")
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_MONTHLY_SHEET_ID", 0)

    assert GoogleSheetsClient.monthly_budget_category("Pets") == "Булка / Долли"
    assert GoogleSheetsClient.monthly_budget_category("Restaurants") == "Продукты"
    assert GoogleSheetsClient.monthly_budget_category("Uncategorized") == "Другое"
    assert GoogleSheetsClient.projection_key() == "monthly_budget:monthly-sheet:0:2026-08"


@pytest.mark.asyncio
async def test_monthly_budget_projection_sets_formulas_and_verifies_receipt(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_SPREADSHEET_ID", "monthly-sheet")
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_LAYOUT", "monthly_budget")
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_PERIOD", "2026-08")
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_MONTHLY_SHEET_ID", 0)
    transaction_id = str(uuid.uuid4())
    metadata = {
        "sheets": [
            {"properties": {"sheetId": 0, "title": "Расходы", "hidden": False}},
            {"properties": {"sheetId": 9, "title": "__family_ai_sync_receipts", "hidden": True}},
        ]
    }
    request = AsyncMock(
        side_effect=[
            metadata,
            metadata,
            {"values": []},
            {"values": [list(MONTHLY_BUDGET_HEADERS)]},
            {"totalUpdatedCells": 317},
            {"values": [["transaction_id", "day", "category", "amount", "period"]]},
            {"updates": {"updatedRows": 1, "updatedRange": "__family_ai_sync_receipts!A2:E2"}},
            {
                "values": [
                    ["transaction_id", "day", "category", "amount", "period"],
                    [transaction_id, "5", "Продукты", "120.50", "2026-08"],
                ]
            },
        ]
    )

    with (
        patch.object(GoogleSheetsClient, "_access_token", new=AsyncMock(return_value="token")),
        patch.object(GoogleSheetsClient, "_request", new=request),
    ):
        updated_range = await GoogleSheetsClient.project_monthly_budget_expense(
            transaction_id=transaction_id,
            occurred_at=datetime(2026, 8, 5, 12, tzinfo=timezone.utc),
            category="Restaurants",
            amount="120.50",
        )

    assert updated_range == "__family_ai_sync_receipts!A2:E2"
    template_update = request.await_args_list[4].kwargs["json_body"]
    assert template_update["data"][0]["range"] == "'Расходы'!B3:K33"
    assert len(template_update["data"][0]["values"]) == 31
    assert "SUMIFS" in template_update["data"][0]["values"][0][0]
    assert "K$2" in template_update["data"][0]["values"][0][-1]
    append = request.await_args_list[6].kwargs["json_body"]
    assert append["values"] == [[transaction_id, "5", "Продукты", "120.50", "2026-08"]]


@pytest.mark.asyncio
async def test_monthly_budget_projection_replay_does_not_append_twice(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_SPREADSHEET_ID", "monthly-sheet")
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_LAYOUT", "monthly_budget")
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_PERIOD", "2026-08")
    transaction_id = str(uuid.uuid4())
    metadata = {
        "sheets": [
            {"properties": {"sheetId": 0, "title": "Расходы", "hidden": False}},
            {"properties": {"sheetId": 9, "title": "__family_ai_sync_receipts", "hidden": True}},
        ]
    }
    request = AsyncMock(
        side_effect=[
            metadata,
            metadata,
            {"values": [["monthly_budget/v1", "2026-08"]]},
            {"values": [["transaction_id"], [transaction_id]]},
        ]
    )

    with (
        patch.object(GoogleSheetsClient, "_access_token", new=AsyncMock(return_value="token")),
        patch.object(GoogleSheetsClient, "_request", new=request),
    ):
        updated_range = await GoogleSheetsClient.project_monthly_budget_expense(
            transaction_id=transaction_id,
            occurred_at=datetime(2026, 8, 5, 12, tzinfo=timezone.utc),
            category="Groceries",
            amount="10",
        )

    assert updated_range == "__family_ai_sync_receipts!A:A"
    assert all(call.args[0] == "GET" for call in request.await_args_list)


def test_sheet_worker_row_has_stable_transaction_identity():
    transaction_id = uuid.uuid4()
    item = TransactionSyncItem(
        id=transaction_id,
        occurred_at=datetime(2026, 7, 26, 12, tzinfo=timezone.utc),
        merchant="базар",
        category="Groceries",
        amount="1900.00",
        currency="UAH",
        direction="expense",
        source="manual",
        owner_type="household",
    )

    assert item.as_sheet_row()[-1] == str(transaction_id)
    assert len(item.as_sheet_row()) == 9

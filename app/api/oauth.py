import inspect
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.session import get_db_session
from app.integrations.google.oauth import GoogleOAuthClient
from app.integrations.oura.client import OuraClient
from app.security.oauth import OAuthStateError, OAuthStateManager
from app.tools.google_tools import GoogleWorkspaceTools
from app.tools.health_tools import HealthTools

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/oauth", tags=["OAuth"])


async def _rollback_if_supported(session: AsyncSession) -> None:
    rollback = getattr(session, "rollback", None)
    if callable(rollback):
        result = rollback()
        if inspect.isawaitable(result):
            await result


@router.get("/oura/callback", response_class=HTMLResponse)
async def oura_oauth_callback(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
) -> HTMLResponse:
    """Validate an Oura callback and persist tokens for the state-bound user."""
    if error:
        logger.warning("Oura authorization was cancelled or rejected.")
        return HTMLResponse(
            content=(
                "<html><body><h2>Authorization cancelled</h2>"
                "<p>You may close this window and return to Telegram.</p>"
                "</body></html>"
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required OAuth parameters.",
        )

    try:
        user_id = await OAuthStateManager.validate_and_consume_state(
            session,
            raw_state=state,
            provider="oura",
        )
        tokens = await OuraClient.exchange_code_for_tokens(code)
        await HealthTools.save_oura_tokens(
            session,
            user_id=user_id,
            tokens=tokens,
        )
    except OAuthStateError:
        await _rollback_if_supported(session)
        logger.warning("Rejected invalid, expired, or replayed OAuth state.")
        return HTMLResponse(
            content=(
                "<html><body><h2>Authorization error</h2>"
                "<p>This authorization request is invalid or expired. "
                "Start a new connection from Telegram.</p></body></html>"
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception:
        await _rollback_if_supported(session)
        logger.error("Oura callback failed; transaction rolled back.")
        return HTMLResponse(
            content=(
                "<html><body><h2>Connection error</h2>"
                "<p>Failed to complete authorization. Please try again.</p>"
                "</body></html>"
            ),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return HTMLResponse(
        content=(
            "<html><body style='font-family: sans-serif; text-align: center; "
            "padding-top: 50px;'>"
            "<h2>Oura Ring connected</h2>"
            "<p>Your account has been securely linked.</p>"
            "<p>You can close this tab and return to Telegram.</p>"
            "</body></html>"
        ),
        status_code=status.HTTP_200_OK,
    )


@router.get("/google/callback", response_class=HTMLResponse)
async def google_oauth_callback(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
) -> HTMLResponse:
    """Connect the state-bound user to personal Gmail and Google Calendar."""
    if error:
        logger.warning("Google authorization was cancelled or rejected.")
        return HTMLResponse(
            content=(
                "<html><body><h2>Google authorization cancelled</h2>"
                "<p>You may close this window and return to Telegram.</p>"
                "</body></html>"
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required OAuth parameters.",
        )

    try:
        user_id = await OAuthStateManager.validate_and_consume_state(
            session,
            raw_state=state,
            provider="google",
        )
        tokens = await GoogleOAuthClient.exchange_code_for_tokens(code)
        await GoogleWorkspaceTools.save_google_tokens(
            session,
            user_id=user_id,
            tokens=tokens,
        )
    except OAuthStateError:
        await _rollback_if_supported(session)
        logger.warning("Rejected invalid, expired, or replayed Google OAuth state.")
        return HTMLResponse(
            content=(
                "<html><body><h2>Google authorization error</h2>"
                "<p>This request is invalid or expired. Start a new connection "
                "from Telegram.</p></body></html>"
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as error:
        await _rollback_if_supported(session)
        logger.error("Google callback failed (%s); transaction rolled back.", type(error).__name__)
        return HTMLResponse(
            content=(
                "<html><body><h2>Google connection error</h2>"
                "<p>Failed to complete authorization. Please try again.</p>"
                "</body></html>"
            ),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return HTMLResponse(
        content=(
            "<html><body style='font-family: sans-serif; text-align: center; "
            "padding-top: 50px;'>"
            "<h2>Google account connected</h2>"
            "<p>Gmail and Calendar are securely linked.</p>"
            "<p>You can close this tab and return to Telegram.</p>"
            "</body></html>"
        ),
        status_code=status.HTTP_200_OK,
    )

import logging
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.session import AsyncSessionLocal
from app.integrations.oura.client import OuraClient
from app.security.oauth import OAuthStateManager, OAuthStateError
from app.tools.health_tools import HealthTools

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/oauth", tags=["OAuth"])


async def get_session():
    async with AsyncSessionLocal.begin() as session:
        yield session


@router.get("/oura/callback", response_class=HTMLResponse)
async def oura_oauth_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Secure OAuth2 Callback Endpoint for Oura Ring."""
    if error:
        logger.warning(f"Oura OAuth returned error: {error}")
        return HTMLResponse(
            content="<html><body><h2>❌ Authorization Cancelled or Failed</h2><p>You may close this window and return to Telegram.</p></body></html>",
            status_code=400,
        )

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required OAuth parameters ('code' or 'state').",
        )

    try:
        # Validate state, check TTL and single-use consumption, resolve user_id
        user_id = await OAuthStateManager.validate_and_consume_state(session, raw_state=state, provider="oura")
        
        # Exchange authorization code for tokens server-side (10s timeout)
        tokens = await OuraClient.exchange_code_for_tokens(code)
        
        # Save encrypted tokens
        await HealthTools.save_oura_tokens(session, telegram_id=0, tokens=tokens)
        
        return HTMLResponse(
            content=(
                "<html><body style='font-family: sans-serif; text-align: center; padding-top: 50px;'>"
                "<h2>✅ Oura Ring Successfully Connected!</h2>"
                "<p>Your Oura account has been securely linked to Family AI Life OS.</p>"
                "<p>You can close this tab and return to Telegram.</p>"
                "</body></html>"
            ),
            status_code=200,
        )

    except OAuthStateError as e:
        logger.warning(f"OAuth State Validation Error: {e}")
        return HTMLResponse(
            content=f"<html><body><h2>❌ Authorization Error</h2><p>{str(e)}</p></body></html>",
            status_code=400,
        )
    except Exception as e:
        logger.exception("Unexpected error during Oura token exchange")
        return HTMLResponse(
            content="<html><body><h2>❌ Connection Error</h2><p>Failed to complete authorization. Please try again.</p></body></html>",
            status_code=500,
        )

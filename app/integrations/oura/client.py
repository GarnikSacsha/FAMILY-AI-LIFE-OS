import urllib.parse
import aiohttp
from typing import Dict, Any, Optional
from datetime import date
from app.config.settings import settings


class OuraClient:
    BASE_URL = "https://api.ouraring.com/v2/usercollection"
    TOKEN_URL = "https://api.ouraring.com/oauth/token"
    AUTH_URL = "https://cloud.ouraring.com/oauth/authorize"

    @classmethod
    def get_authorization_url(cls, state: str = "default") -> str:
        """Returns the OAuth2 authorization URL for Oura Ring."""
        client_id = (settings.OURA_CLIENT_ID or "").strip()
        redirect_uri = (settings.OURA_REDIRECT_URI or "").strip()
        
        # Standard Oura API V2 scope list
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "email personal daily heartrate tag workout session spo2 stress",
            "state": state,
        }
        return f"{cls.AUTH_URL}?{urllib.parse.urlencode(params)}"

    @classmethod
    async def exchange_code_for_tokens(cls, code: str) -> Dict[str, Any]:
        """Exchanges authorization code for access and refresh tokens."""
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.OURA_REDIRECT_URI,
            "client_id": settings.OURA_CLIENT_ID,
            "client_secret": settings.OURA_CLIENT_SECRET,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(cls.TOKEN_URL, data=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ValueError(f"Oura token exchange failed ({resp.status}): {text}")
                return await resp.json()

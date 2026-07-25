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
        params = {
            "client_id": settings.OURA_CLIENT_ID or "",
            "redirect_uri": settings.OURA_REDIRECT_URI,
            "response_type": "code",
            "scope": "email personal daily heartrate tag workout session spo2 stress heart_health ring_configuration",
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

    @classmethod
    async def refresh_access_token(cls, refresh_token: str) -> Dict[str, Any]:
        """Refreshes an expired access token."""
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.OURA_CLIENT_ID,
            "client_secret": settings.OURA_CLIENT_SECRET,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(cls.TOKEN_URL, data=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ValueError(f"Oura token refresh failed ({resp.status}): {text}")
                return await resp.json()

    @classmethod
    async def fetch_daily_readiness(
        cls, access_token: str, start_date: str, end_date: str
    ) -> Dict[str, Any]:
        """Fetches daily readiness records from Oura API V2."""
        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"{cls.BASE_URL}/daily_readiness?start_date={start_date}&end_date={end_date}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ValueError(f"Failed to fetch Oura readiness ({resp.status}): {text}")
                return await resp.json()

    @classmethod
    async def fetch_daily_sleep(
        cls, access_token: str, start_date: str, end_date: str
    ) -> Dict[str, Any]:
        """Fetches daily sleep records from Oura API V2."""
        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"{cls.BASE_URL}/daily_sleep?start_date={start_date}&end_date={end_date}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ValueError(f"Failed to fetch Oura sleep ({resp.status}): {text}")
                return await resp.json()

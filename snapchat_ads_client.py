"""
Snapchat Ads - läsklient mot Snapchat Marketing API.

Endast läsning: listar kampanjer och hämtar statistik. Inga
skrivoperationer stöds för den här plattformen.

Autentisering sker via OAuth2 refresh token-flödet - access token hämtas
och förnyas automatiskt av den här modulen (cachad i minnet tills den
snart går ut). Access token loggas ALDRIG, bara att en förnyelse skett.

OBS: Verifiera fält-/endpointnamn mot Snapchats egen dokumentation
(marketingapi.snapchat.com) om ett anrop avvisas.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

import config
import fake_data
from config import SnapchatAdsSettings
from http_utils import HttpError, request_json

logger = logging.getLogger("google_ads_mcp.snapchat")

TOKEN_URL = "https://accounts.snapchat.com/login/oauth2/access_token"
API_BASE = "https://adsapi.snapchat.com/v1"
MAX_PAGES = 50  # säkerhetsgräns mot oändliga pagination-loopar

_client: Optional[httpx.Client] = None
_settings: Optional[SnapchatAdsSettings] = None
_access_token: Optional[str] = None
_token_expires_at: float = 0.0


class SnapchatAdsToolError(RuntimeError):
    """Fel som ska visas för MCP-klienten (LLM:en) på ett begripligt sätt."""


def init_client(settings: SnapchatAdsSettings, timeout: float = 30.0) -> httpx.Client:
    """Initierar den globala Snapchat Ads-klienten. Anropas en gång vid uppstart."""
    global _client, _settings, _access_token, _token_expires_at
    _settings = settings
    _client = httpx.Client(timeout=timeout)
    _access_token = None
    _token_expires_at = 0.0
    logger.info("Snapchat Ads-klient initierad.")
    return _client


def _get_client() -> tuple[httpx.Client, SnapchatAdsSettings]:
    if _client is None or _settings is None:
        raise RuntimeError(
            "Snapchat Ads-klienten är inte initierad. Kontrollera SNAPCHAT_CLIENT_ID/"
            "SNAPCHAT_CLIENT_SECRET/SNAPCHAT_REFRESH_TOKEN i miljön/.env och starta om servern."
        )
    return _client, _settings


def _get_access_token() -> str:
    """Hämtar en cachad access token, eller förnyar den via refresh_token om den
    saknas eller snart går ut. Token-värdet loggas aldrig."""
    global _access_token, _token_expires_at
    client, settings = _get_client()

    if _access_token and time.monotonic() < _token_expires_at - 60:
        return _access_token

    try:
        response = client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
                "refresh_token": settings.refresh_token,
            },
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise SnapchatAdsToolError(
            f"Kunde inte förnya Snapchat access token (HTTP {exc.response.status_code})."
        ) from exc
    except httpx.RequestError as exc:
        raise SnapchatAdsToolError(f"Nätverksfel vid Snapchat token-förnyelse: {exc}") from exc

    _access_token = payload["access_token"]
    _token_expires_at = time.monotonic() + float(payload.get("expires_in", 1800))
    logger.info("Snapchat access token förnyad.")
    return _access_token


def _authed_get(url: str, params: Optional[dict]) -> dict:
    """
    OBS: params=None (inte {}) måste användas när url redan har en query-sträng
    (t.ex. Snapchats next_link) - httpx tolkar params={} som "ersätt query-
    strängen med tom" och nollställer då cursorn i next_link, vilket ger en
    oändlig pagination-loop på samma sida.
    """
    client, _ = _get_client()
    token = _get_access_token()
    try:
        return request_json(
            client, "GET", url, params=params, headers={"Authorization": f"Bearer {token}"}
        )
    except HttpError as exc:
        raise SnapchatAdsToolError(f"Snapchat Marketing API-fel: {exc}") from exc


def list_campaigns(ad_account_id: str) -> list[dict[str, Any]]:
    """Listar kampanjer för ett Snapchat ad account."""
    ad_account_id = str(ad_account_id).strip()
    if not ad_account_id:
        raise SnapchatAdsToolError("ad_account_id saknas.")
    if config.MOCK_MODE:
        return fake_data.snapchat_list_campaigns(ad_account_id)

    campaigns: list[dict[str, Any]] = []
    url = f"{API_BASE}/adaccounts/{ad_account_id}/campaigns"
    params: Optional[dict] = {}
    for _ in range(MAX_PAGES):
        data = _authed_get(url, params)
        for item in data.get("campaigns", []):
            # Snapchat kan wrappa varje post som {"campaign": {...}} - packa upp om så.
            campaigns.append(item.get("campaign", item))
        next_link = data.get("paging", {}).get("next_link")
        if not next_link:
            break
        url = next_link  # redan en fullständig, absolut URL - se OBS ovan
        params = None
    return campaigns


def get_stats(
    ad_account_id: str,
    fields: list[str],
    start_time: str,
    end_time: str,
    granularity: str = "DAY",
) -> dict[str, Any]:
    """
    Hämtar statistik (rapportering) för ett Snapchat ad account.

    Args:
        ad_account_id: Snapchat ad account-id.
        fields: t.ex. ["spend", "impressions", "swipes"].
        start_time / end_time: ISO 8601-tidsstämplar, t.ex. "2024-01-01T00:00:00.000-07:00".
        granularity: "DAY" | "HOUR" | "LIFETIME" | "TOTAL".
    """
    ad_account_id = str(ad_account_id).strip()
    if not ad_account_id:
        raise SnapchatAdsToolError("ad_account_id saknas.")
    if not fields:
        raise SnapchatAdsToolError("fields får inte vara tomt.")
    if config.MOCK_MODE:
        return fake_data.snapchat_get_stats(
            ad_account_id, fields, start_time, end_time, granularity=granularity
        )

    params = {
        "fields": ",".join(fields),
        "granularity": granularity,
        "start_time": start_time,
        "end_time": end_time,
    }
    return _authed_get(f"{API_BASE}/adaccounts/{ad_account_id}/stats", params)

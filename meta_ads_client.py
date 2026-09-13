"""
Meta (Facebook/Instagram) Ads - läsklient mot Graph API Marketing-endpoints.

Endast läsning: listar kampanjer och kör insights-queries. Inga
skrivoperationer stöds för den här plattformen (kunden har bara bett om
läsning för Meta/TikTok/Snapchat - skrivoperationer finns bara för Google
Ads, se google_ads_client.py).

OBS: Graph API-versionen (META_API_VERSION) och fältnamn kan ändras/
sunsettas över tid enligt Metas egen versioneringspolicy. Verifiera mot
https://developers.facebook.com/docs/marketing-api om ett fält avvisas.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

from config import MetaAdsSettings
from http_utils import HttpError, request_json

logger = logging.getLogger("google_ads_mcp.meta")

MAX_PAGES = 50  # säkerhetsgräns mot oändliga pagination-loopar

_client: Optional[httpx.Client] = None
_settings: Optional[MetaAdsSettings] = None


class MetaAdsToolError(RuntimeError):
    """Fel som ska visas för MCP-klienten (LLM:en) på ett begripligt sätt."""


def init_client(settings: MetaAdsSettings, timeout: float = 30.0) -> httpx.Client:
    """Initierar den globala Meta Ads-klienten. Anropas en gång vid uppstart."""
    global _client, _settings
    _settings = settings
    _client = httpx.Client(
        base_url=f"https://graph.facebook.com/{settings.api_version}",
        timeout=timeout,
    )
    logger.info("Meta Ads-klient initierad (api_version=%s).", settings.api_version)
    return _client


def _get_client() -> tuple[httpx.Client, MetaAdsSettings]:
    if _client is None or _settings is None:
        raise RuntimeError(
            "Meta Ads-klienten är inte initierad. Kontrollera att META_ACCESS_TOKEN "
            "är satt i miljön/.env och starta om servern."
        )
    return _client, _settings


def normalize_ad_account_id(ad_account_id: str) -> str:
    """Meta kräver att konto-id är prefixat med 'act_'. Lägger till prefixet om det saknas."""
    ad_account_id = str(ad_account_id).strip()
    if not ad_account_id:
        raise MetaAdsToolError("ad_account_id saknas.")
    return ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"


def _paginated_get(client: httpx.Client, path: str, params: dict) -> list[dict[str, Any]]:
    """Följer Graph API:ns paging.next-länkar tills de tar slut (eller MAX_PAGES nås)."""
    results: list[dict[str, Any]] = []
    url: str = path
    request_params: Optional[dict] = params
    for _ in range(MAX_PAGES):
        try:
            data = request_json(client, "GET", url, params=request_params)
        except HttpError as exc:
            raise MetaAdsToolError(f"Meta Graph API-fel: {exc}") from exc
        results.extend(data.get("data", []))
        next_url = data.get("paging", {}).get("next")
        if not next_url:
            break
        url = next_url  # 'next' är redan en fullständig, absolut URL med token+cursor
        request_params = None
    return results


def list_campaigns(ad_account_id: str) -> list[dict[str, Any]]:
    """Listar kampanjer för ett Meta ad account: id, namn, status, objective, budget."""
    client, settings = _get_client()
    ad_account_id = normalize_ad_account_id(ad_account_id)
    params = {
        "access_token": settings.access_token,
        "fields": "id,name,status,effective_status,objective,daily_budget,lifetime_budget",
        "limit": 100,
    }
    return _paginated_get(client, f"/{ad_account_id}/campaigns", params)


def run_insights_query(
    ad_account_id: str,
    fields: list[str],
    level: str = "campaign",
    date_preset: Optional[str] = "last_30d",
    time_range: Optional[dict] = None,
    time_increment: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Kör en Insights-query (rapportering) mot Meta Marketing API.

    Args:
        ad_account_id: t.ex. "act_1234567890" eller bara "1234567890".
        fields: t.ex. ["campaign_name", "impressions", "clicks", "spend"].
        level: "account" | "campaign" | "adset" | "ad".
        date_preset: t.ex. "today", "yesterday", "last_7d", "last_30d".
            Ignoreras om time_range anges.
        time_range: {"since": "YYYY-MM-DD", "until": "YYYY-MM-DD"} - tar över date_preset.
        time_increment: t.ex. "1" för en rad per dag, annars aggregerat över perioden.
    """
    client, settings = _get_client()
    ad_account_id = normalize_ad_account_id(ad_account_id)
    if not fields:
        raise MetaAdsToolError("fields får inte vara tomt.")

    params: dict[str, Any] = {
        "access_token": settings.access_token,
        "fields": ",".join(fields),
        "level": level,
        "limit": 500,
    }
    if time_range:
        params["time_range"] = json.dumps(time_range)
    elif date_preset:
        params["date_preset"] = date_preset
    if time_increment:
        params["time_increment"] = time_increment

    return _paginated_get(client, f"/{ad_account_id}/insights", params)

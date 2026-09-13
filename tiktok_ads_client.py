"""
TikTok Ads - läsklient mot TikTok Marketing API.

Endast läsning: listar kampanjer och kör integrated reports (rapportering).
Inga skrivoperationer stöds för den här plattformen.

OBS: Verifiera fält-/dimensionsnamn mot TikToks egen dokumentation
(business-api.tiktok.com) om ett anrop avvisas - Marketing API:et
versioneras och fält kan tillkomma/utgå.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

import config
import fake_data
from config import TikTokAdsSettings
from http_utils import HttpError, request_json

logger = logging.getLogger("google_ads_mcp.tiktok")

MAX_PAGES = 50  # säkerhetsgräns mot oändliga pagination-loopar

_client: Optional[httpx.Client] = None


class TikTokAdsToolError(RuntimeError):
    """Fel som ska visas för MCP-klienten (LLM:en) på ett begripligt sätt."""


def init_client(settings: TikTokAdsSettings, timeout: float = 30.0) -> httpx.Client:
    """Initierar den globala TikTok Ads-klienten. Anropas en gång vid uppstart."""
    global _client
    _client = httpx.Client(
        base_url=f"https://business-api.tiktok.com/open_api/{settings.api_version}",
        headers={"Access-Token": settings.access_token},
        timeout=timeout,
    )
    logger.info("TikTok Ads-klient initierad (api_version=%s).", settings.api_version)
    return _client


def _get_client() -> httpx.Client:
    if _client is None:
        raise RuntimeError(
            "TikTok Ads-klienten är inte initierad. Kontrollera att TIKTOK_ACCESS_TOKEN "
            "är satt i miljön/.env och starta om servern."
        )
    return _client


def _call(path: str, params: dict) -> dict:
    """Gör ett GET-anrop mot TikTok Marketing API och tolkar dess svarskuvert
    ({"code": 0, "message": "OK", "data": {...}}) - code != 0 är ett API-fel."""
    client = _get_client()
    try:
        data = request_json(client, "GET", path, params=params)
    except HttpError as exc:
        raise TikTokAdsToolError(f"TikTok Marketing API-fel: {exc}") from exc
    code = data.get("code")
    if code not in (0, None):
        raise TikTokAdsToolError(
            f"TikTok Marketing API-fel: code={code} message={data.get('message')}"
        )
    return data.get("data", {})


def list_campaigns(advertiser_id: str) -> list[dict[str, Any]]:
    """Listar kampanjer för ett TikTok advertiser-konto."""
    advertiser_id = str(advertiser_id).strip()
    if not advertiser_id:
        raise TikTokAdsToolError("advertiser_id saknas.")
    if config.MOCK_MODE:
        return fake_data.tiktok_list_campaigns(advertiser_id)

    campaigns: list[dict[str, Any]] = []
    page = 1
    for _ in range(MAX_PAGES):
        data = _call(
            "/campaign/get/",
            {"advertiser_id": advertiser_id, "page": page, "page_size": 100},
        )
        campaigns.extend(data.get("list", []))
        total_pages = data.get("page_info", {}).get("total_page", page)
        if page >= total_pages:
            break
        page += 1
    return campaigns


def run_report(
    advertiser_id: str,
    metrics: list[str],
    start_date: str,
    end_date: str,
    dimensions: Optional[list[str]] = None,
    data_level: str = "AUCTION_CAMPAIGN",
    report_type: str = "BASIC",
) -> list[dict[str, Any]]:
    """
    Kör en integrated report (rapportering) mot TikTok Marketing API.

    Args:
        advertiser_id: TikTok advertiser-id.
        metrics: t.ex. ["spend", "impressions", "clicks", "conversion"].
        start_date / end_date: "YYYY-MM-DD".
        dimensions: t.ex. ["campaign_id", "stat_time_day"]. Default ["campaign_id"].
        data_level: t.ex. "AUCTION_CAMPAIGN", "AUCTION_ADGROUP", "AUCTION_AD".
        report_type: default "BASIC".
    """
    advertiser_id = str(advertiser_id).strip()
    if not advertiser_id:
        raise TikTokAdsToolError("advertiser_id saknas.")
    if not metrics:
        raise TikTokAdsToolError("metrics får inte vara tomt.")
    dimensions = dimensions or ["campaign_id"]
    if config.MOCK_MODE:
        return fake_data.tiktok_run_report(
            advertiser_id,
            metrics,
            start_date,
            end_date,
            dimensions=dimensions,
            data_level=data_level,
        )

    rows: list[dict[str, Any]] = []
    page = 1
    for _ in range(MAX_PAGES):
        data = _call(
            "/report/integrated/get/",
            {
                "advertiser_id": advertiser_id,
                "report_type": report_type,
                "dimensions": json.dumps(dimensions),
                "metrics": json.dumps(metrics),
                "data_level": data_level,
                "start_date": start_date,
                "end_date": end_date,
                "page": page,
                "page_size": 100,
            },
        )
        rows.extend(data.get("list", []))
        total_pages = data.get("page_info", {}).get("total_page", page)
        if page >= total_pages:
            break
        page += 1
    return rows

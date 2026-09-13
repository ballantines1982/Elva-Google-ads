"""
Klient-initiering och alla anrop mot Google Ads API:
- run_query(): kör valfri GAQL-query via GoogleAdsService.search() (med pagination)
- list_campaigns(): fördefinierad GAQL-query för kampanjöversikt
- create_search_ad(): skapar en Responsive Search Ad (alltid PAUSED)
- pause_ad() / enable_ad(): ändrar AdGroupAd.status via mutate

All felhantering av GoogleAdsException samlas i _handle_google_ads_exception()
så att error.code och message alltid loggas tydligt, utan att någonsin logga
credentials/nycklar.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.api_core import protobuf_helpers
from google.protobuf.json_format import MessageToDict

from config import GoogleAdsSettings, normalize_customer_id

logger = logging.getLogger("google_ads_mcp.client")
audit_logger = logging.getLogger("google_ads_mcp.audit")

# --- Valideringsgränser för Responsive Search Ads (Google Ads-krav) ---
MAX_HEADLINES = 15
MIN_HEADLINES = 3  # Google Ads kräver minst 3 rubriker per RSA
MAX_HEADLINE_LENGTH = 30
MAX_DESCRIPTIONS = 4
MIN_DESCRIPTIONS = 2  # Google Ads kräver minst 2 beskrivningar per RSA
MAX_DESCRIPTION_LENGTH = 90

LIST_CAMPAIGNS_QUERY = """
    SELECT
        campaign.id,
        campaign.name,
        campaign.status,
        campaign.advertising_channel_type,
        campaign_budget.id,
        campaign_budget.amount_micros
    FROM campaign
    ORDER BY campaign.id
"""

_client: Optional[GoogleAdsClient] = None


class GoogleAdsToolError(RuntimeError):
    """Fel som ska visas för MCP-klienten (LLM:en) på ett begripligt sätt."""


def init_client(settings: GoogleAdsSettings) -> GoogleAdsClient:
    """Initierar den globala Google Ads-klienten. Anropas en gång vid uppstart."""
    global _client
    config_dict = settings.to_client_config()
    _client = GoogleAdsClient.load_from_dict(config_dict)
    logger.info("Google Ads-klient initierad (auth_mode=%s).", settings.auth_mode())
    return _client


def get_client() -> GoogleAdsClient:
    if _client is None:
        raise RuntimeError(
            "Google Ads-klienten är inte initierad. Anropa init_client(settings) vid uppstart."
        )
    return _client


def _handle_google_ads_exception(exc: GoogleAdsException, context: str) -> None:
    """Loggar en GoogleAdsException tydligt (code + message per fel) och kastar vidare."""
    request_id = exc.request_id
    logger.error("GoogleAdsException i %s (request_id=%s)", context, request_id)
    error_summaries = []
    for error in exc.failure.errors:
        code = error.error_code
        logger.error("  -> error.code=%s message=%s", code, error.message)
        error_summaries.append(f"{code}: {error.message}")
    raise GoogleAdsToolError(
        f"Google Ads API-fel i {context} (request_id={request_id}): "
        + "; ".join(error_summaries)
    ) from exc


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Konverterar en GoogleAdsRow (proto-plus) till en ren, JSON-serialiserbar dict."""
    return MessageToDict(row._pb, preserving_proto_field_name=True)


def _log_write_operation(action: str, customer_id: str, actor: str, detail: str) -> None:
    """
    Enkel spårbarhetslogg för alla skrivoperationer (create/pause/enable).
    Loggas till stdout (och därmed till fil/plattformslogg om man omdirigerar
    stdout, t.ex. på Railway/Render). Innehåller ALDRIG credentials.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    audit_logger.info(
        "AUDIT ts=%s actor=%s action=%s customer_id=%s detail=%s",
        timestamp,
        actor,
        action,
        customer_id,
        detail,
    )


def run_query(customer_id: str, query: str, timeout: float = 30.0) -> list[dict[str, Any]]:
    """
    Kör en GAQL-query mot GoogleAdsService.search() och returnerar alla rader
    som en lista av dicts. search() itererar automatiskt över alla sidor, så
    hela resultatet samlas in oavsett hur många rader svaret innehåller.
    """
    client = get_client()
    customer_id = normalize_customer_id(customer_id)
    ga_service = client.get_service("GoogleAdsService")

    request = client.get_type("SearchGoogleAdsRequest")
    request.customer_id = customer_id
    request.query = query
    request.page_size = 10_000

    rows: list[dict[str, Any]] = []
    try:
        response = ga_service.search(request=request, timeout=timeout)
        for row in response:  # Pager: hämtar automatiskt nästa sida vid behov.
            rows.append(_row_to_dict(row))
    except GoogleAdsException as exc:
        _handle_google_ads_exception(exc, context=f"run_query(customer_id={customer_id})")
    return rows


def _micros_to_amount(micros: Any) -> Optional[float]:
    if micros is None:
        return None
    try:
        return int(micros) / 1_000_000
    except (TypeError, ValueError):
        return None


def list_campaigns(customer_id: str, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Bekvämlighetsverktyg: listar kampanjer med id, namn, status och budget."""
    rows = run_query(customer_id, LIST_CAMPAIGNS_QUERY, timeout=timeout)
    campaigns = []
    for row in rows:
        campaign = row.get("campaign", {})
        budget = row.get("campaignBudget", {})
        campaigns.append(
            {
                "id": campaign.get("id"),
                "name": campaign.get("name"),
                "status": campaign.get("status"),
                "advertising_channel_type": campaign.get("advertisingChannelType"),
                "budget_id": budget.get("id"),
                "budget_micros": budget.get("amountMicros"),
                "budget_amount": _micros_to_amount(budget.get("amountMicros")),
            }
        )
    return campaigns


def _validate_rsa_assets(headlines: list[str], descriptions: list[str]) -> None:
    if not isinstance(headlines, list) or not (MIN_HEADLINES <= len(headlines) <= MAX_HEADLINES):
        raise GoogleAdsToolError(
            f"headlines måste innehålla mellan {MIN_HEADLINES} och {MAX_HEADLINES} rubriker, "
            f"fick {len(headlines) if isinstance(headlines, list) else 'ogiltig typ'}."
        )
    for h in headlines:
        if not isinstance(h, str) or not h.strip():
            raise GoogleAdsToolError("Alla headlines måste vara icke-tomma strängar.")
        if len(h) > MAX_HEADLINE_LENGTH:
            raise GoogleAdsToolError(
                f"Headline {h!r} är {len(h)} tecken långt, max tillåtet är {MAX_HEADLINE_LENGTH}."
            )

    if not isinstance(descriptions, list) or not (
        MIN_DESCRIPTIONS <= len(descriptions) <= MAX_DESCRIPTIONS
    ):
        raise GoogleAdsToolError(
            f"descriptions måste innehålla mellan {MIN_DESCRIPTIONS} och {MAX_DESCRIPTIONS} "
            f"beskrivningar, fick {len(descriptions) if isinstance(descriptions, list) else 'ogiltig typ'}."
        )
    for d in descriptions:
        if not isinstance(d, str) or not d.strip():
            raise GoogleAdsToolError("Alla descriptions måste vara icke-tomma strängar.")
        if len(d) > MAX_DESCRIPTION_LENGTH:
            raise GoogleAdsToolError(
                f"Description {d!r} är {len(d)} tecken lång, max tillåtet är {MAX_DESCRIPTION_LENGTH}."
            )


def create_search_ad(
    customer_id: str,
    ad_group_id: str,
    headlines: list[str],
    descriptions: list[str],
    final_url: str,
    actor: str = "unknown",
    timeout: float = 30.0,
) -> dict[str, Any]:
    """
    Skapar en Responsive Search Ad. Skapas ALLTID med status PAUSED - annonsen
    måste aktiveras explicit via enable_ad() efter granskning.
    """
    _validate_rsa_assets(headlines, descriptions)
    if not final_url or not final_url.strip():
        raise GoogleAdsToolError("final_url får inte vara tomt.")

    client = get_client()
    customer_id = normalize_customer_id(customer_id)
    ad_group_ad_service = client.get_service("AdGroupAdService")
    ad_group_service = client.get_service("AdGroupService")

    operation = client.get_type("AdGroupAdOperation")
    ad_group_ad = operation.create
    ad_group_ad.ad_group = ad_group_service.ad_group_path(customer_id, ad_group_id)
    # Säkerhetskrav: skapas ALDRIG som ENABLED.
    ad_group_ad.status = client.enums.AdGroupAdStatusEnum.PAUSED

    ad = ad_group_ad.ad
    ad.final_urls.append(final_url)

    for headline_text in headlines:
        asset = client.get_type("AdTextAsset")
        asset.text = headline_text
        ad.responsive_search_ad.headlines.append(asset)

    for description_text in descriptions:
        asset = client.get_type("AdTextAsset")
        asset.text = description_text
        ad.responsive_search_ad.descriptions.append(asset)

    try:
        response = ad_group_ad_service.mutate_ad_group_ads(
            customer_id=customer_id, operations=[operation], timeout=timeout
        )
    except GoogleAdsException as exc:
        _handle_google_ads_exception(
            exc,
            context=f"create_search_ad(customer_id={customer_id}, ad_group_id={ad_group_id})",
        )

    resource_name = response.results[0].resource_name
    ad_id = resource_name.split("~")[-1] if resource_name else None

    _log_write_operation(
        action="create_search_ad",
        customer_id=customer_id,
        actor=actor,
        detail=f"ad_group_id={ad_group_id} resource_name={resource_name} status=PAUSED",
    )

    return {
        "resource_name": resource_name,
        "ad_group_id": str(ad_group_id),
        "ad_id": ad_id,
        "status": "PAUSED",
        "message": (
            f"Responsive Search Ad skapad med status PAUSED (annons-id: {ad_id}). "
            "Annonsen är INTE aktiv och visas inte förrän den aktiveras uttryckligen "
            "via enable_ad()."
        ),
    }


def _set_ad_group_ad_status(
    customer_id: str,
    ad_group_id: str,
    ad_id: str,
    status_enum_name: str,
    actor: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    client = get_client()
    customer_id = normalize_customer_id(customer_id)
    ad_group_ad_service = client.get_service("AdGroupAdService")

    operation = client.get_type("AdGroupAdOperation")
    ad_group_ad = operation.update
    ad_group_ad.resource_name = ad_group_ad_service.ad_group_ad_path(
        customer_id, ad_group_id, ad_id
    )
    ad_group_ad.status = getattr(client.enums.AdGroupAdStatusEnum, status_enum_name)
    operation.update_mask.CopyFrom(protobuf_helpers.field_mask(None, ad_group_ad._pb))

    try:
        response = ad_group_ad_service.mutate_ad_group_ads(
            customer_id=customer_id, operations=[operation], timeout=timeout
        )
    except GoogleAdsException as exc:
        _handle_google_ads_exception(
            exc,
            context=(
                f"set_ad_group_ad_status(customer_id={customer_id}, "
                f"ad_group_id={ad_group_id}, ad_id={ad_id}, status={status_enum_name})"
            ),
        )

    resource_name = response.results[0].resource_name
    _log_write_operation(
        action=f"status_change:{status_enum_name}",
        customer_id=customer_id,
        actor=actor,
        detail=f"ad_group_id={ad_group_id} ad_id={ad_id} resource_name={resource_name}",
    )
    return {"resource_name": resource_name, "status": status_enum_name}


def pause_ad(
    customer_id: str, ad_group_id: str, ad_id: str, actor: str = "unknown", timeout: float = 30.0
) -> dict[str, Any]:
    """Pausar en annons (AdGroupAd.status -> PAUSED)."""
    result = _set_ad_group_ad_status(
        customer_id, ad_group_id, ad_id, "PAUSED", actor=actor, timeout=timeout
    )
    result["message"] = f"Annons {ad_id} i annonsgrupp {ad_group_id} är nu PAUSAD."
    return result


def enable_ad(
    customer_id: str, ad_group_id: str, ad_id: str, actor: str = "unknown", timeout: float = 30.0
) -> dict[str, Any]:
    """Aktiverar en annons (AdGroupAd.status -> ENABLED)."""
    result = _set_ad_group_ad_status(
        customer_id, ad_group_id, ad_id, "ENABLED", actor=actor, timeout=timeout
    )
    result["message"] = f"Annons {ad_id} i annonsgrupp {ad_group_id} är nu AKTIVERAD (ENABLED)."
    return result

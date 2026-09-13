"""
Google Ads MCP-server (+ läsning från Meta, TikTok och Snapchat Ads).

Exponerar följande verktyg för LLM-klienter (ChatGPT, Claude m.fl.) via
MCP:s streamable-http-transport på endpointen /mcp:

Google Ads (läsning + begränsad, kontrollerad skrivning):
- run_gaql_query   : kör valfri GAQL-query (huvudverktyget för läsning)
- list_campaigns   : bekvämlighetsverktyg, listar kampanjer
- create_search_ad : skapar en Responsive Search Ad (skapas ALLTID PAUSED)
- pause_ad         : pausar en annons
- enable_ad        : aktiverar en annons

Meta (Facebook/Instagram) Ads, TikTok Ads, Snapchat Ads (endast läsning):
- list_meta_campaigns / run_meta_insights_query
- list_tiktok_campaigns / run_tiktok_report
- list_snapchat_campaigns / get_snapchat_stats

Meta/TikTok/Snapchat är additiva - saknas deras miljövariabler startar
servern ändå (bara Google Ads är obligatoriskt), men respektive verktyg
ger då ett tydligt konfigurationsfel om det anropas. Inga skrivoperationer
finns för dessa tre plattformar.

Kör lokalt över stdio (t.ex. för Claude Desktop-konfiguration):
    python server.py --transport stdio

Kör som HTTP-server (Railway/Render, ChatGPT Developer Mode, fjärranslutning):
    python server.py
    Lyssnar på http://<HOST>:<PORT>/mcp. Om MCP_AUTH_TOKEN är satt krävs
    headern "Authorization: Bearer <MCP_AUTH_TOKEN>" på alla anrop.
"""

from __future__ import annotations

import argparse
import contextvars
import logging
import secrets
import sys

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

import config
import google_ads_client as gac
import meta_ads_client as mac
import snapchat_ads_client as sac
import tiktok_ads_client as tac

# --------------------------------------------------------------------------
# Loggning. OBS: logga ALDRIG credentials, tokens eller service account-
# nycklar här eller i google_ads_client.py - bara maskerade identifierare.
# --------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("google_ads_mcp.server")

# Maskerad identifierare för den som anropar, satt av auth-middlewaren från
# bearer-token. Används enbart för spårbarhet i audit-loggen (google_ads_client.py).
_actor_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("actor", default="unknown")


def _mask_token(token: str) -> str:
    """Returnerar en maskerad version av en token, säker att logga."""
    if len(token) <= 6:
        return "***"
    return f"...{token[-6:]}"


mcp = FastMCP(
    name="google-ads-mcp",
    instructions=(
        "Verktyg för att läsa (via GAQL) och i begränsad omfattning skriva "
        "Google Ads-data. Alla skrivoperationer loggas för spårbarhet, och "
        "nya annonser skapas alltid med status PAUSED - de måste aktiveras "
        "explicit med enable_ad() efter mänsklig granskning. Utöver Google "
        "Ads finns verktyg för att LÄSA data (kampanjer, rapportering) från "
        "Meta (Facebook/Instagram) Ads, TikTok Ads och Snapchat Ads - inga "
        "skrivoperationer stöds för dessa tre."
    ),
)


@mcp.tool()
def run_gaql_query(customer_id: str, query: str) -> dict:
    """
    Kör en valfri GAQL-query mot Google Ads API (GoogleAdsService.search) och
    returnerar samtliga resultatrader. Detta är huvudverktyget för all
    rapportering och läsning av Google Ads-data.

    Args:
        customer_id: Google Ads customer-id, med eller utan bindestreck
            (t.ex. "123-456-7890" eller "1234567890").
        query: En giltig GAQL-query, t.ex.
            "SELECT campaign.id, campaign.name, metrics.clicks FROM campaign
             WHERE segments.date DURING LAST_7_DAYS".

    Returns:
        dict: {"row_count": int, "rows": list[dict]} - en dict per resultatrad.
    """
    settings = config.load_server_settings()
    rows = gac.run_query(customer_id, query, timeout=settings.request_timeout_seconds)
    return {"row_count": len(rows), "rows": rows}


@mcp.tool()
def list_campaigns(customer_id: str) -> dict:
    """
    Bekvämlighetsverktyg: listar kampanjer i ett konto med id, namn, status,
    kanaltyp och daglig budget. Implementerat som en fördefinierad GAQL-query.

    Args:
        customer_id: Google Ads customer-id, med eller utan bindestreck.

    Returns:
        dict: {"campaign_count": int, "campaigns": list[dict]}.
    """
    settings = config.load_server_settings()
    campaigns = gac.list_campaigns(customer_id, timeout=settings.request_timeout_seconds)
    return {"campaign_count": len(campaigns), "campaigns": campaigns}


@mcp.tool()
def create_search_ad(
    customer_id: str,
    ad_group_id: str,
    headlines: list[str],
    descriptions: list[str],
    final_url: str,
) -> dict:
    """
    Skapar en Responsive Search Ad i angiven annonsgrupp. Annonsen skapas
    ALLTID med status PAUSED, oavsett indata - den syns aldrig för användare
    förrän den aktiveras explicit via enable_ad() efter granskning.

    Args:
        customer_id: Google Ads customer-id, med eller utan bindestreck.
        ad_group_id: Id för annonsgruppen annonsen ska skapas i.
        headlines: 3-15 rubriker, max 30 tecken var.
        descriptions: 2-4 beskrivningar, max 90 tecken var.
        final_url: URL till landningssidan annonsen ska länka till.

    Returns:
        dict med "resource_name", "ad_id", "status" (alltid "PAUSED") och
        ett tydligt bekräftelsemeddelande.
    """
    settings = config.load_server_settings()
    actor = _actor_ctx.get()
    return gac.create_search_ad(
        customer_id,
        ad_group_id,
        headlines,
        descriptions,
        final_url,
        actor=actor,
        timeout=settings.request_timeout_seconds,
    )


@mcp.tool()
def pause_ad(customer_id: str, ad_group_id: str, ad_id: str) -> dict:
    """
    Pausar en annons genom att sätta AdGroupAd.status till PAUSED.

    Args:
        customer_id: Google Ads customer-id, med eller utan bindestreck.
        ad_group_id: Id för annonsgruppen annonsen tillhör.
        ad_id: Id för annonsen som ska pausas.
    """
    settings = config.load_server_settings()
    actor = _actor_ctx.get()
    return gac.pause_ad(
        customer_id, ad_group_id, ad_id, actor=actor, timeout=settings.request_timeout_seconds
    )


@mcp.tool()
def enable_ad(customer_id: str, ad_group_id: str, ad_id: str) -> dict:
    """
    Aktiverar en annons genom att sätta AdGroupAd.status till ENABLED.
    OBS: detta gör annonsen live - använd med försiktighet.

    Args:
        customer_id: Google Ads customer-id, med eller utan bindestreck.
        ad_group_id: Id för annonsgruppen annonsen tillhör.
        ad_id: Id för annonsen som ska aktiveras.
    """
    settings = config.load_server_settings()
    actor = _actor_ctx.get()
    return gac.enable_ad(
        customer_id, ad_group_id, ad_id, actor=actor, timeout=settings.request_timeout_seconds
    )


@mcp.tool()
def list_meta_campaigns(ad_account_id: str) -> dict:
    """
    Listar kampanjer i ett Meta (Facebook/Instagram) ad account: id, namn,
    status och budget. Endast läsning.

    Args:
        ad_account_id: Meta ad account-id, med eller utan "act_"-prefix
            (t.ex. "act_1234567890" eller "1234567890").

    Returns:
        dict: {"campaign_count": int, "campaigns": list[dict]}.
    """
    campaigns = mac.list_campaigns(ad_account_id)
    return {"campaign_count": len(campaigns), "campaigns": campaigns}


@mcp.tool()
def run_meta_insights_query(
    ad_account_id: str,
    fields: list[str],
    level: str = "campaign",
    date_preset: str | None = "last_30d",
    time_range: dict | None = None,
    time_increment: str | None = None,
) -> dict:
    """
    Kör en Insights-rapport (rapportering) mot Meta Marketing API. Endast läsning.

    Args:
        ad_account_id: Meta ad account-id, med eller utan "act_"-prefix.
        fields: t.ex. ["campaign_name", "impressions", "clicks", "spend"].
        level: "account" | "campaign" | "adset" | "ad".
        date_preset: t.ex. "today", "last_7d", "last_30d". Ignoreras om time_range anges.
        time_range: {"since": "YYYY-MM-DD", "until": "YYYY-MM-DD"} - tar över date_preset.
        time_increment: t.ex. "1" för en rad per dag, annars aggregerat över perioden.

    Returns:
        dict: {"row_count": int, "rows": list[dict]}.
    """
    rows = mac.run_insights_query(
        ad_account_id,
        fields,
        level=level,
        date_preset=date_preset,
        time_range=time_range,
        time_increment=time_increment,
    )
    return {"row_count": len(rows), "rows": rows}


@mcp.tool()
def list_tiktok_campaigns(advertiser_id: str) -> dict:
    """
    Listar kampanjer för ett TikTok advertiser-konto. Endast läsning.

    Args:
        advertiser_id: TikTok advertiser-id.

    Returns:
        dict: {"campaign_count": int, "campaigns": list[dict]}.
    """
    campaigns = tac.list_campaigns(advertiser_id)
    return {"campaign_count": len(campaigns), "campaigns": campaigns}


@mcp.tool()
def run_tiktok_report(
    advertiser_id: str,
    metrics: list[str],
    start_date: str,
    end_date: str,
    dimensions: list[str] | None = None,
    data_level: str = "AUCTION_CAMPAIGN",
) -> dict:
    """
    Kör en integrated report (rapportering) mot TikTok Marketing API. Endast läsning.

    Args:
        advertiser_id: TikTok advertiser-id.
        metrics: t.ex. ["spend", "impressions", "clicks", "conversion"].
        start_date / end_date: "YYYY-MM-DD".
        dimensions: t.ex. ["campaign_id", "stat_time_day"]. Default ["campaign_id"].
        data_level: t.ex. "AUCTION_CAMPAIGN", "AUCTION_ADGROUP", "AUCTION_AD".

    Returns:
        dict: {"row_count": int, "rows": list[dict]}.
    """
    rows = tac.run_report(
        advertiser_id,
        metrics,
        start_date,
        end_date,
        dimensions=dimensions,
        data_level=data_level,
    )
    return {"row_count": len(rows), "rows": rows}


@mcp.tool()
def list_snapchat_campaigns(ad_account_id: str) -> dict:
    """
    Listar kampanjer för ett Snapchat ad account. Endast läsning.

    Args:
        ad_account_id: Snapchat ad account-id.

    Returns:
        dict: {"campaign_count": int, "campaigns": list[dict]}.
    """
    campaigns = sac.list_campaigns(ad_account_id)
    return {"campaign_count": len(campaigns), "campaigns": campaigns}


@mcp.tool()
def get_snapchat_stats(
    ad_account_id: str,
    fields: list[str],
    start_time: str,
    end_time: str,
    granularity: str = "DAY",
) -> dict:
    """
    Hämtar statistik (rapportering) för ett Snapchat ad account. Endast läsning.

    Args:
        ad_account_id: Snapchat ad account-id.
        fields: t.ex. ["spend", "impressions", "swipes"].
        start_time / end_time: ISO 8601-tidsstämplar, t.ex. "2024-01-01T00:00:00.000-07:00".
        granularity: "DAY" | "HOUR" | "LIFETIME" | "TOTAL".

    Returns:
        dict: rått svar från Snapchats stats-endpoint.
    """
    return sac.get_stats(ad_account_id, fields, start_time, end_time, granularity=granularity)


class BearerTokenAuthMiddleware:
    """
    Enkel bearer-token-autentisering för HTTP-transporten (ren ASGI-middleware).

    Kräver headern "Authorization: Bearer <MCP_AUTH_TOKEN>" på alla HTTP-
    anrop när MCP_AUTH_TOKEN är satt. Jämförelsen görs med secrets.compare_digest
    för att undvika timing-attacker. Loggar aldrig själva token - bara en
    maskerad svans, sparad som spårbarhets-"actor" för skrivoperationer.
    """

    def __init__(self, app, expected_token: str | None):
        self.app = app
        self.expected_token = expected_token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not self.expected_token:
            # Ingen token konfigurerad (redan varnat vid uppstart) - släpp igenom.
            _actor_ctx.set("no-auth-configured")
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        provided = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else None

        if not provided or not secrets.compare_digest(provided, self.expected_token):
            response = JSONResponse(
                {
                    "error": "unauthorized",
                    "message": "Saknar eller ogiltig 'Authorization: Bearer <token>'-header.",
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return

        _actor_ctx.set(_mask_token(provided))
        await self.app(scope, receive, send)


def build_http_app():
    """Bygger ASGI-appen som körs via uvicorn: FastMCP:s streamable-http-app
    inlindad i bearer-token-middlewaren. Exponeras på /mcp (FastMCP default)."""
    server_settings = config.load_server_settings()
    inner_app = mcp.streamable_http_app()
    return BearerTokenAuthMiddleware(inner_app, server_settings.auth_token)


def _init_optional_platform(name: str, load_settings, init_client, timeout: float) -> None:
    """
    Initierar en tilläggsplattform (Meta/TikTok/Snapchat) om dess miljövariabler
    är satta. Till skillnad från Google Ads (obligatoriskt) är dessa additiva -
    saknad konfiguration bara loggas som en varning, servern startar ändå.
    Anropas ett sådant plattforms verktyg innan det initierats ges ett tydligt
    felmeddelande (se respektive klients _get_client()).
    """
    try:
        settings = load_settings()
    except config.ConfigError as exc:
        logger.warning(
            "%s är inte konfigurerad, hoppar över (dess verktyg ger ett tydligt "
            "fel om de anropas): %s",
            name,
            exc,
        )
        return
    init_client(settings, timeout=timeout)


def main() -> None:
    parser = argparse.ArgumentParser(description="Google Ads MCP-server")
    parser.add_argument(
        "--transport",
        choices=["streamable-http", "stdio"],
        default="streamable-http",
        help="Transport att köra servern med (default: streamable-http).",
    )
    args = parser.parse_args()

    if config.MOCK_MODE:
        # MOCK_MODE=true: hoppa över all riktig klient-initiering (Google Ads är
        # annars obligatoriskt) - inga credentials behövs, se fake_data.py.
        logger.warning(
            "MOCK_MODE aktiverat - hoppar över init av riktiga API-klienter. "
            "Alla verktyg (Google/Meta/TikTok/Snapchat) svarar med fejkad testdata."
        )
    else:
        # Initiera Google Ads-klienten vid uppstart så konfigurationsfel upptäcks
        # direkt, innan servern börjar ta emot anrop. Google Ads är obligatoriskt.
        ads_settings = config.load_google_ads_settings()
        gac.init_client(ads_settings)

        # Meta/TikTok/Snapchat är additiva tilläggsplattformar för läsning -
        # saknas deras miljövariabler startar servern ändå.
        http_timeout = config.load_server_settings().http_timeout_seconds
        _init_optional_platform(
            "Meta Ads", config.load_meta_ads_settings, mac.init_client, http_timeout
        )
        _init_optional_platform(
            "TikTok Ads", config.load_tiktok_ads_settings, tac.init_client, http_timeout
        )
        _init_optional_platform(
            "Snapchat Ads", config.load_snapchat_ads_settings, sac.init_client, http_timeout
        )

    if args.transport == "stdio":
        # Lokal körning, t.ex. från Claude Desktops mcpServers-konfiguration.
        # MCP-klienten startar processen direkt - HTTP-auth är inte relevant.
        logger.info("Startar Google Ads MCP-server över stdio.")
        mcp.run(transport="stdio")
        return

    server_settings = config.load_server_settings()
    mcp.settings.host = server_settings.host
    mcp.settings.port = server_settings.port
    app = build_http_app()
    logger.info(
        "Startar Google Ads MCP-server på http://%s:%s%s (bearer-auth: %s)",
        server_settings.host,
        server_settings.port,
        mcp.settings.streamable_http_path,
        "PÅ" if server_settings.auth_token else "AV (!)",
    )
    uvicorn.run(app, host=server_settings.host, port=server_settings.port)


if __name__ == "__main__":
    main()

"""
Google Ads MCP-server.

Exponerar följande verktyg för LLM-klienter (ChatGPT, Claude m.fl.) via
MCP:s streamable-http-transport på endpointen /mcp:

- run_gaql_query   : kör valfri GAQL-query (huvudverktyget för läsning)
- list_campaigns   : bekvämlighetsverktyg, listar kampanjer
- create_search_ad : skapar en Responsive Search Ad (skapas ALLTID PAUSED)
- pause_ad         : pausar en annons
- enable_ad        : aktiverar en annons

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
        "explicit med enable_ad() efter mänsklig granskning."
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Google Ads MCP-server")
    parser.add_argument(
        "--transport",
        choices=["streamable-http", "stdio"],
        default="streamable-http",
        help="Transport att köra servern med (default: streamable-http).",
    )
    args = parser.parse_args()

    # Initiera Google Ads-klienten vid uppstart så konfigurationsfel upptäcks
    # direkt, innan servern börjar ta emot anrop.
    ads_settings = config.load_google_ads_settings()
    gac.init_client(ads_settings)

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

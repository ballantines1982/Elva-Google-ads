"""
Konfiguration för Google Ads MCP-servern.

Läser ALL konfiguration från miljövariabler (via python-dotenv för lokal
utveckling med en .env-fil). Ingen service account-nyckel eller annan
hemlighet skrivs någonsin till loggar härifrån - se google_ads_client.py
och server.py för hur loggning görs säkert.

Två autentiseringssätt mot Google Ads API stöds:

1. OAuth2 med refresh token (rekommenderas, och det vanliga sättet att
   ansluta till Google Ads API):
   GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET, GOOGLE_ADS_REFRESH_TOKEN

2. Service account + domain-wide delegation (endast relevant om kontot
   administreras under en Google Workspace-domän som gett service accounten
   domain-wide delegation till Google Ads-scopet):
   GOOGLE_ADS_JSON_KEY_FILE_PATH, GOOGLE_ADS_IMPERSONATED_EMAIL

Utöver Google Ads läses här även (valfri, additiv) konfiguration för
läsning från Meta (Facebook/Instagram) Ads, TikTok Ads och Snapchat Ads:

- Meta:      META_ACCESS_TOKEN (+ valfritt META_APP_SECRET, META_API_VERSION)
- TikTok:    TIKTOK_ACCESS_TOKEN (+ valfritt TIKTOK_API_VERSION)
- Snapchat:  SNAPCHAT_CLIENT_ID, SNAPCHAT_CLIENT_SECRET, SNAPCHAT_REFRESH_TOKEN

Dessa tre är additiva - saknas de startar servern ändå (bara Google Ads
är obligatoriskt), men respektive plattforms verktyg ger då ett tydligt
konfigurationsfel om de anropas.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv

# Laddar .env om den finns (no-op i produktion där miljövariabler sätts direkt
# av värdplattformen, t.ex. Railway/Render).
load_dotenv()

logger = logging.getLogger("google_ads_mcp.config")


class ConfigError(RuntimeError):
    """Fel i konfigurationen - saknad eller ogiltig miljövariabel."""


def _get_env(name: str, required: bool = False, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name, default)
    if value is not None:
        value = value.strip()
    if required and not value:
        raise ConfigError(f"Miljövariabeln {name} saknas eller är tom.")
    return value or None


def normalize_customer_id(customer_id: str) -> str:
    """
    Normaliserar ett Google Ads customer-id.

    Google Ads API vill ha customer-id utan bindestreck ("1234567890"), men
    användare klistrar ofta in det som det visas i gränssnittet
    ("123-456-7890"). Den här funktionen tar bort bindestreck/whitespace och
    validerar att resultatet bara innehåller siffror.
    """
    if not customer_id or not str(customer_id).strip():
        raise ConfigError("customer_id saknas eller är tomt.")
    normalized = str(customer_id).replace("-", "").replace(" ", "").strip()
    if not normalized.isdigit():
        raise ConfigError(
            f"Ogiltigt customer_id: {customer_id!r}. Förväntade enbart siffror "
            "(ev. formaterat med '-', t.ex. '123-456-7890')."
        )
    return normalized


@dataclass(frozen=True)
class GoogleAdsSettings:
    developer_token: str
    login_customer_id: Optional[str] = None
    use_proto_plus: bool = True

    # OAuth2-flöde (rekommenderas)
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    refresh_token: Optional[str] = None

    # Service account-flöde (domain-wide delegation)
    json_key_file_path: Optional[str] = None
    impersonated_email: Optional[str] = None

    def auth_mode(self) -> str:
        has_oauth = bool(self.client_id and self.client_secret and self.refresh_token)
        has_service_account = bool(self.json_key_file_path and self.impersonated_email)

        if has_oauth and has_service_account:
            raise ConfigError(
                "Både OAuth-uppgifter (GOOGLE_ADS_CLIENT_ID/SECRET/REFRESH_TOKEN) och "
                "service account-uppgifter (GOOGLE_ADS_JSON_KEY_FILE_PATH/"
                "GOOGLE_ADS_IMPERSONATED_EMAIL) är satta. Välj EN autentiseringsmetod."
            )
        if has_oauth:
            return "oauth"
        if has_service_account:
            return "service_account"
        raise ConfigError(
            "Ingen giltig autentiseringsmetod hittades. Sätt antingen "
            "GOOGLE_ADS_CLIENT_ID + GOOGLE_ADS_CLIENT_SECRET + GOOGLE_ADS_REFRESH_TOKEN "
            "(OAuth, rekommenderas) eller GOOGLE_ADS_JSON_KEY_FILE_PATH + "
            "GOOGLE_ADS_IMPERSONATED_EMAIL (service account med domain-wide delegation)."
        )

    def to_client_config(self) -> dict:
        """Bygger den dict som GoogleAdsClient.load_from_dict() förväntar sig."""
        config: dict = {
            "developer_token": self.developer_token,
            "use_proto_plus": self.use_proto_plus,
        }
        if self.login_customer_id:
            config["login_customer_id"] = self.login_customer_id

        mode = self.auth_mode()
        if mode == "oauth":
            config.update(
                client_id=self.client_id,
                client_secret=self.client_secret,
                refresh_token=self.refresh_token,
            )
        else:
            config.update(
                json_key_file_path=self.json_key_file_path,
                impersonated_email=self.impersonated_email,
            )
        return config


@lru_cache(maxsize=1)
def load_google_ads_settings() -> GoogleAdsSettings:
    """Läser och validerar Google Ads-konfigurationen från miljövariabler."""
    developer_token = _get_env("GOOGLE_ADS_DEVELOPER_TOKEN", required=True)

    login_customer_id = _get_env("GOOGLE_ADS_LOGIN_CUSTOMER_ID")
    if login_customer_id:
        login_customer_id = normalize_customer_id(login_customer_id)

    settings = GoogleAdsSettings(
        developer_token=developer_token,  # type: ignore[arg-type]
        login_customer_id=login_customer_id,
        client_id=_get_env("GOOGLE_ADS_CLIENT_ID"),
        client_secret=_get_env("GOOGLE_ADS_CLIENT_SECRET"),
        refresh_token=_get_env("GOOGLE_ADS_REFRESH_TOKEN"),
        json_key_file_path=_get_env("GOOGLE_ADS_JSON_KEY_FILE_PATH"),
        impersonated_email=_get_env("GOOGLE_ADS_IMPERSONATED_EMAIL"),
    )
    # Validera direkt så att servern failar snabbt vid uppstart med ett tydligt
    # felmeddelande, hellre än vid första verktygsanropet.
    mode = settings.auth_mode()
    logger.info("Google Ads-konfiguration laddad (auth_mode=%s).", mode)
    return settings


@dataclass(frozen=True)
class ServerSettings:
    auth_token: Optional[str]
    host: str
    port: int
    request_timeout_seconds: float
    http_timeout_seconds: float


@lru_cache(maxsize=1)
def load_server_settings() -> ServerSettings:
    """Läser MCP-serverns egna inställningar (auth, host/port, timeout)."""
    auth_token = _get_env("MCP_AUTH_TOKEN")
    if not auth_token:
        logger.warning(
            "MCP_AUTH_TOKEN är INTE satt - servern körs UTAN bearer-token-"
            "autentisering. Sätt MCP_AUTH_TOKEN innan servern exponeras publikt!"
        )
    port = int(_get_env("PORT", default="8000"))  # Railway/Render sätter PORT åt oss
    host = _get_env("HOST", default="0.0.0.0")
    timeout = float(_get_env("GOOGLE_ADS_TIMEOUT_SECONDS", default="30"))
    http_timeout = float(_get_env("ADS_HTTP_TIMEOUT_SECONDS", default="30"))
    return ServerSettings(
        auth_token=auth_token,
        host=host,  # type: ignore[arg-type]
        port=port,
        request_timeout_seconds=timeout,
        http_timeout_seconds=http_timeout,
    )


# --------------------------------------------------------------------------
# Meta (Facebook/Instagram) Ads - läsning via Graph API Marketing-endpoints.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class MetaAdsSettings:
    access_token: str
    app_secret: Optional[str] = None
    api_version: str = "v21.0"


@lru_cache(maxsize=1)
def load_meta_ads_settings() -> MetaAdsSettings:
    """Läser Meta Ads-konfiguration. Kastar ConfigError om token saknas."""
    access_token = _get_env("META_ACCESS_TOKEN", required=True)
    return MetaAdsSettings(
        access_token=access_token,  # type: ignore[arg-type]
        app_secret=_get_env("META_APP_SECRET"),
        api_version=_get_env("META_API_VERSION", default="v21.0"),  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------
# TikTok Ads - läsning via TikTok Marketing API.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class TikTokAdsSettings:
    access_token: str
    api_version: str = "v1.3"


@lru_cache(maxsize=1)
def load_tiktok_ads_settings() -> TikTokAdsSettings:
    """Läser TikTok Ads-konfiguration. Kastar ConfigError om token saknas."""
    access_token = _get_env("TIKTOK_ACCESS_TOKEN", required=True)
    return TikTokAdsSettings(
        access_token=access_token,  # type: ignore[arg-type]
        api_version=_get_env("TIKTOK_API_VERSION", default="v1.3"),  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------
# Snapchat Ads - läsning via Snapchat Marketing API (OAuth2 refresh token).
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SnapchatAdsSettings:
    client_id: str
    client_secret: str
    refresh_token: str


@lru_cache(maxsize=1)
def load_snapchat_ads_settings() -> SnapchatAdsSettings:
    """Läser Snapchat Ads-konfiguration. Kastar ConfigError om något saknas."""
    return SnapchatAdsSettings(
        client_id=_get_env("SNAPCHAT_CLIENT_ID", required=True),  # type: ignore[arg-type]
        client_secret=_get_env("SNAPCHAT_CLIENT_SECRET", required=True),  # type: ignore[arg-type]
        refresh_token=_get_env("SNAPCHAT_REFRESH_TOKEN", required=True),  # type: ignore[arg-type]
    )

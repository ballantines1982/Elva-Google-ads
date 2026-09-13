"""
Delad HTTP-hjälpfunktion för REST-baserade annonsplattformar (Meta, TikTok,
Snapchat) som inte har ett officiellt, tungt Python-SDK likt google-ads.

Centraliserar timeout-hantering och tydlig felloggning så varje klientmodul
slipper duplicera det. Loggar ALDRIG request-headers eller query-parametrar
i sin helhet (där access tokens ofta ligger) - bara metod, URL och (vid fel)
svarskroppen från motparten, som normalt inte innehåller hemligheter.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("google_ads_mcp.http")


class HttpError(RuntimeError):
    """Fel vid ett HTTP-anrop mot en annonsplattforms REST-API."""


def _safe_error_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:500]


def request_json(client: httpx.Client, method: str, url: str, **kwargs: Any) -> dict:
    """
    Gör ett HTTP-anrop och returnerar svaret som dict.

    Fångar timeouts, nätverksfel och HTTP-felstatusar och kastar ett tydligt
    HttpError med motpartens felmeddelande (om sådant finns i svarskroppen).
    """
    try:
        response = client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException as exc:
        logger.error("Timeout vid %s %s", method, url)
        raise HttpError(f"Timeout vid anrop mot {url}") from exc
    except httpx.HTTPStatusError as exc:
        body = _safe_error_body(exc.response)
        logger.error("HTTP %s vid %s %s: %s", exc.response.status_code, method, url, body)
        raise HttpError(f"HTTP {exc.response.status_code} vid anrop mot {url}: {body}") from exc
    except httpx.RequestError as exc:
        logger.error("Nätverksfel vid %s %s: %s", method, url, exc)
        raise HttpError(f"Nätverksfel vid anrop mot {url}: {exc}") from exc

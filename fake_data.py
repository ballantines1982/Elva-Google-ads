"""
Fejkad testdata för alla fyra plattformarna (Google Ads, Meta, TikTok, Snapchat).

Aktiveras genom att sätta miljövariabeln MOCK_MODE=true. Då startar servern
UTAN några riktiga credentials (Google Ads-developer token m.m. behövs inte
alls) och samtliga verktyg returnerar påhittad men realistisk data istället
för att anropa de riktiga API:erna. Användbart för att testa/chatta mot
MCP-serverns verktyg innan man har riktiga kontouppgifter för alla, eller
några, plattformar.

All data är deterministisk - seedad på id/datum/fältnamn via SHA-256 - så
samma anrop alltid ger samma resultat mellan omstarter, men olika
customer_id/ad_account_id ger olika (men alltid rimligt formad) data.
Inga nätverksanrop görs någonsin i mock-läge.

OBS: run_gaql_query() i mock-läge tolkar INTE GAQL på riktigt - den läser
bara av vilka fält-grupper (t.ex. "metrics.", "segments.date", "ad_group.")
som nämns i query-strängen och lägger till motsvarande fejkade fält. Det
räcker för att testa verktygsanrop och chattflöden, men validerar inte att
en GAQL-query faktiskt är syntaktiskt korrekt.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


def _rng(*parts: Any) -> random.Random:
    """Deterministisk slumpgenerator seedad på godtyckliga delar (id, datum, ...)."""
    seed = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return random.Random(seed)


def _today_utc() -> datetime:
    return datetime.now(timezone.utc)


# ==============================================================================
# Google Ads
# ==============================================================================

_GADS_CAMPAIGNS = [
    ("Sommarrea 2026", "SEARCH", "ENABLED"),
    ("Black Friday", "SEARCH", "PAUSED"),
    ("Brand Awareness - Sverige", "DISPLAY", "ENABLED"),
    ("Leadgenerering B2B", "SEARCH", "ENABLED"),
    ("Retargeting - Kundvagn", "DISPLAY", "ENABLED"),
    ("Video - YouTube Prospecting", "VIDEO", "PAUSED"),
]


def gads_run_query(customer_id: str, query: str) -> list[dict[str, Any]]:
    """Fejkar GoogleAdsService.search() - se modulens docstring för hur query
    tolkas (enkel substrängs-heuristik, inte riktig GAQL-parsning)."""
    q = query.lower()
    wants_metrics = "metrics." in q
    wants_segments_date = "segments.date" in q
    wants_ad_group = "ad_group." in q or "ad_group\n" in q or "from ad_group" in q
    wants_ad_group_ad = "ad_group_ad." in q
    wants_keyword = "keyword" in q

    rows: list[dict[str, Any]] = []
    for i, (name, channel_type, status) in enumerate(_GADS_CAMPAIGNS, start=1):
        campaign_id = 1_000_000_000 + i
        budget_id = 2_000_000_000 + i
        crng = _rng("gads", customer_id, campaign_id)
        budget_micros = crng.choice([50_000_000, 100_000_000, 200_000_000, 500_000_000])

        base_row: dict[str, Any] = {
            "campaign": {
                "id": str(campaign_id),
                "name": name,
                "status": status,
                "advertisingChannelType": channel_type,
            },
            "campaignBudget": {"id": str(budget_id), "amountMicros": str(budget_micros)},
        }
        if wants_ad_group:
            base_row["adGroup"] = {
                "id": str(campaign_id * 10 + 1),
                "name": f"{name} - Annonsgrupp 1",
                "status": "ENABLED",
            }
        if wants_ad_group_ad:
            base_row["adGroupAd"] = {
                "resourceName": (
                    f"customers/{customer_id}/adGroupAds/"
                    f"{campaign_id * 10 + 1}~{campaign_id * 100 + 1}"
                ),
                "status": "PAUSED",
            }
        if wants_keyword:
            krng = _rng("gads", customer_id, campaign_id, "keyword")
            base_row["adGroupCriterion"] = {
                "keyword": {
                    "text": krng.choice(
                        ["skor online", "köp skor rea", "bästa sneakers", "sneakers stockholm"]
                    ),
                    "matchType": "BROAD",
                }
            }

        if wants_metrics or wants_segments_date:
            for d in range(7):  # senaste 7 dagarna räcker för att kunna chatta om trender
                date = (_today_utc() - timedelta(days=d)).strftime("%Y-%m-%d")
                drng = _rng("gads", customer_id, campaign_id, date)
                impressions = drng.randint(500, 20_000)
                clicks = max(1, int(impressions * drng.uniform(0.01, 0.08)))
                cost_micros = clicks * drng.randint(3_000_000, 15_000_000)
                conversions = round(clicks * drng.uniform(0.02, 0.15), 2)

                row = dict(base_row)
                if wants_segments_date:
                    row["segments"] = {"date": date}
                if wants_metrics:
                    row["metrics"] = {
                        "impressions": str(impressions),
                        "clicks": str(clicks),
                        "costMicros": str(cost_micros),
                        "conversions": str(conversions),
                        "ctr": round(clicks / impressions, 4) if impressions else 0,
                        "averageCpc": str(int(cost_micros / clicks)) if clicks else "0",
                    }
                rows.append(row)
        else:
            rows.append(base_row)
    return rows


# ==============================================================================
# Meta (Facebook/Instagram) Ads
# ==============================================================================

_META_CAMPAIGNS = [
    ("Sommarrea 2026", "OUTCOME_SALES", "ACTIVE"),
    ("Retargeting - Kundvagn", "OUTCOME_SALES", "ACTIVE"),
    ("Brand Awareness", "OUTCOME_AWARENESS", "PAUSED"),
    ("Leadgen - Kontaktformulär", "OUTCOME_LEADS", "ACTIVE"),
]


def meta_list_campaigns(ad_account_id: str) -> list[dict[str, Any]]:
    rng = _rng("meta", ad_account_id)
    campaigns = []
    for i, (name, objective, status) in enumerate(_META_CAMPAIGNS, start=1):
        campaigns.append(
            {
                "id": str(1_200_000_000_000 + i),
                "name": name,
                "status": status,
                "effective_status": status,
                "objective": objective,
                "daily_budget": str(rng.choice([300, 500, 1000, 2000])),
            }
        )
    return campaigns


def meta_run_insights_query(
    ad_account_id: str,
    fields: list[str],
    level: str = "campaign",
    date_preset: Optional[str] = "last_30d",
    time_range: Optional[dict] = None,
    time_increment: Optional[str] = None,
) -> list[dict[str, Any]]:
    n_days = 7 if time_increment else 1
    rows: list[dict[str, Any]] = []
    for i, (name, objective, status) in enumerate(_META_CAMPAIGNS, start=1):
        campaign_id = str(1_200_000_000_000 + i)
        for d in range(n_days):
            date = (_today_utc() - timedelta(days=d)).strftime("%Y-%m-%d")
            drng = _rng("meta", ad_account_id, campaign_id, date)
            impressions = drng.randint(1000, 50_000)
            clicks = max(1, int(impressions * drng.uniform(0.01, 0.06)))
            spend = round(clicks * drng.uniform(3, 12), 2)
            reach = int(impressions * drng.uniform(0.6, 0.9))

            row: dict[str, Any] = {}
            for f in fields:
                if f == "campaign_name":
                    row[f] = name
                elif f == "campaign_id":
                    row[f] = campaign_id
                elif f == "impressions":
                    row[f] = str(impressions)
                elif f == "clicks":
                    row[f] = str(clicks)
                elif f == "spend":
                    row[f] = str(spend)
                elif f == "reach":
                    row[f] = str(reach)
                elif f == "ctr":
                    row[f] = str(round(clicks / impressions * 100, 2)) if impressions else "0"
                elif f == "cpc":
                    row[f] = str(round(spend / clicks, 2)) if clicks else "0"
                else:
                    row[f] = None  # okänt fält i fejkdata - fälten valideras inte mot Graph API
            if time_increment:
                row["date_start"] = date
                row["date_stop"] = date
            rows.append(row)
    return rows


# ==============================================================================
# TikTok Ads
# ==============================================================================

_TIKTOK_CAMPAIGNS = [
    ("Sommarrea 2026", "PRODUCT_SALES", "CAMPAIGN_STATUS_ENABLE"),
    ("Video Views - Prospecting", "REACH", "CAMPAIGN_STATUS_ENABLE"),
    ("Konvertering - App Install", "APP_PROMOTION", "CAMPAIGN_STATUS_DISABLE"),
]


def tiktok_list_campaigns(advertiser_id: str) -> list[dict[str, Any]]:
    rng = _rng("tiktok", advertiser_id)
    out = []
    for i, (name, objective, status) in enumerate(_TIKTOK_CAMPAIGNS, start=1):
        out.append(
            {
                "campaign_id": str(1_700_000_000_000_000_000 + i),
                "campaign_name": name,
                "objective_type": objective,
                "status": status,
                "budget": rng.choice([300, 500, 1000]),
                "budget_mode": "BUDGET_MODE_DAY",
            }
        )
    return out


def tiktok_run_report(
    advertiser_id: str,
    metrics: list[str],
    start_date: str,
    end_date: str,
    dimensions: Optional[list[str]] = None,
    data_level: str = "AUCTION_CAMPAIGN",
) -> list[dict[str, Any]]:
    dimensions = dimensions or ["campaign_id"]
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    n_days = max((end - start).days + 1, 1)
    per_day = "stat_time_day" in dimensions

    rows: list[dict[str, Any]] = []
    for i, (name, objective, status) in enumerate(_TIKTOK_CAMPAIGNS, start=1):
        campaign_id = str(1_700_000_000_000_000_000 + i)
        for d in range(n_days if per_day else 1):
            date = (start + timedelta(days=d)).strftime("%Y-%m-%d")
            drng = _rng("tiktok", advertiser_id, campaign_id, date)
            impressions = drng.randint(1000, 40_000)
            clicks = max(1, int(impressions * drng.uniform(0.01, 0.05)))
            spend = round(clicks * drng.uniform(2, 8), 2)

            dims: dict[str, Any] = {}
            for dm in dimensions:
                if dm == "campaign_id":
                    dims[dm] = campaign_id
                elif dm == "stat_time_day":
                    dims[dm] = date
                else:
                    dims[dm] = None

            mets: dict[str, Any] = {}
            for m in metrics:
                if m == "spend":
                    mets[m] = str(spend)
                elif m == "impressions":
                    mets[m] = str(impressions)
                elif m == "clicks":
                    mets[m] = str(clicks)
                elif m == "conversion":
                    mets[m] = str(int(clicks * drng.uniform(0.03, 0.1)))
                else:
                    mets[m] = None  # okänt fält i fejkdata

            rows.append({"dimensions": dims, "metrics": mets})
    return rows


# ==============================================================================
# Snapchat Ads
# ==============================================================================

_SNAPCHAT_CAMPAIGNS = [
    ("Sommarrea 2026", "ACTIVE"),
    ("Story Ads - Prospecting", "ACTIVE"),
    ("Retargeting", "PAUSED"),
]


def snapchat_list_campaigns(ad_account_id: str) -> list[dict[str, Any]]:
    rng = _rng("snap", ad_account_id)
    out = []
    for i, (name, status) in enumerate(_SNAPCHAT_CAMPAIGNS, start=1):
        out.append(
            {
                "id": f"11111111-1111-1111-1111-{i:012d}",
                "ad_account_id": ad_account_id,
                "name": name,
                "status": status,
                "daily_budget_micro": rng.choice([300_000_000, 500_000_000, 1_000_000_000]),
            }
        )
    return out


def snapchat_get_stats(
    ad_account_id: str,
    fields: list[str],
    start_time: str,
    end_time: str,
    granularity: str = "DAY",
) -> dict[str, Any]:
    n = 7 if granularity == "DAY" else 1
    timeseries = []
    for d in range(n):
        date = (_today_utc() - timedelta(days=d)).strftime("%Y-%m-%dT00:00:00.000Z")
        drng = _rng("snap", ad_account_id, date)
        stat: dict[str, Any] = {}
        for f in fields:
            if f == "impressions":
                stat[f] = drng.randint(1000, 30_000)
            elif f == "swipes":
                stat[f] = drng.randint(20, 800)
            elif f == "spend":
                stat[f] = drng.randint(50_000_000, 900_000_000)  # mikro-valuta, som riktiga API:et
            else:
                stat[f] = 0  # okänt fält i fejkdata
        timeseries.append({"start_time": date, "end_time": date, "stats": stat})

    return {
        "request_status": "SUCCESS",
        "total_stats": [
            {
                "id": ad_account_id,
                "type": "ad_account",
                "granularity": granularity,
                "timeseries": timeseries,
            }
        ],
    }

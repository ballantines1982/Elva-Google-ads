# Google Ads MCP-server

En HTTPS-baserad MCP-server (streamable-http transport) som kopplar
ChatGPT/Claude till Google Ads API. Exponerar verktyg för att läsa
Google Ads-data via GAQL och för att utföra ett fåtal kontrollerade
skrivoperationer (skapa/pausa/aktivera annonser).

## Projektstruktur

```
server.py             - MCP-server, tool-definitioner (FastMCP)
google_ads_client.py  - klient-init, GAQL-exekvering, mutate-hjälpfunktioner
config.py             - miljövariabler, autentiseringskonfiguration
requirements.txt
Dockerfile             - för deploy på Railway/Render
Procfile                - alternativ för Render/Heroku-liknande deploy
.env.example
```

## Verktyg som exponeras

| Verktyg | Beskrivning |
|---|---|
| `run_gaql_query(customer_id, query)` | Kör valfri GAQL-query mot `GoogleAdsService.search()`. Huvudverktyget för all rapportering/läsning. Hanterar pagination automatiskt. |
| `list_campaigns(customer_id)` | Bekvämlighetsverktyg: listar kampanjer (id, namn, status, kanaltyp, budget) via en fördefinierad GAQL-query. |
| `create_search_ad(customer_id, ad_group_id, headlines, descriptions, final_url)` | Skapar en Responsive Search Ad. Skapas **alltid** med status `PAUSED`. Validerar max 15 headlines (≤30 tecken) och max 4 descriptions (≤90 tecken), samt Google Ads minimikrav (3 headlines / 2 descriptions). |
| `pause_ad(customer_id, ad_group_id, ad_id)` | Sätter `AdGroupAd.status = PAUSED`. |
| `enable_ad(customer_id, ad_group_id, ad_id)` | Sätter `AdGroupAd.status = ENABLED`. **Gör annonsen live** - använd med försiktighet. |

Alla `customer_id` normaliseras automatiskt (bindestreck/mellanslag tas bort),
så både `"123-456-7890"` och `"1234567890"` fungerar.

## 1. Skaffa Google Ads API-uppgifter

Den här servern läser **all** konfiguration från miljövariabler - ingen
`google-ads.yaml`-fil behövs. Du behöver fyra saker:

1. **Developer token** - hämtas från ditt Google Ads Manager-konto under
   `Verktyg och inställningar > API Center`
   (https://ads.google.com/aw/apicenter). Ett nytt konto får ofta en token
   med "test account access" till en början, vilket räcker gott för att
   testa mot ett Google Ads-testkonto.
2. **OAuth-klient** (client ID + client secret) - skapas i
   [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
   som en OAuth-klient av typen **Desktop app**.
3. **Refresh token** - genereras EN gång lokalt via Google Ads-teamets
   officiella hjälpskript:

   ```bash
   pip install google-ads
   curl -O https://raw.githubusercontent.com/googleads/google-ads-python/main/examples/authentication/generate_user_credentials.py
   python generate_user_credentials.py --client_id <DITT_CLIENT_ID> --client_secret <DIN_CLIENT_SECRET>
   ```

   Skriptet öppnar en webbläsare för Google-inloggning/samtycke och skriver
   ut en `refresh_token`. Spara den i din `.env`.

4. **Customer-id** för det (test-)konto du vill anropa, samt ev.
   **login-customer-id** (MCC-kontots id) om kontot nås via ett manager-konto.

> **Om du redan har en `google-ads.yaml`:** den här versionen av servern
> använder inte filen (efter beslut i projektet att bygga en ren
> env-var-baserad `config.py`, som passar bättre för deploy på
> Railway/Render). Om du redan har kört `generate_user_credentials.py`
> tidigare kan du bara kopiera `client_id`, `client_secret`,
> `refresh_token` och `developer_token`-värdena från din befintliga
> `google-ads.yaml` in i `.env`.

### Alternativ: service account + domain-wide delegation

`config.py` stödjer även autentisering via en service account-nyckel
(`GOOGLE_ADS_JSON_KEY_FILE_PATH` + `GOOGLE_ADS_IMPERSONATED_EMAIL`), men
detta **fungerar bara** om Google Ads-kontot administreras under en Google
Workspace-domän som explicit gett service accounten domain-wide delegation
till Google Ads-scopet. För de flesta konton (inklusive testkonton skapade
som privatperson) är **OAuth-flödet ovan det som faktiskt fungerar** - använd
service account-alternativet bara om du vet att din organisation har satt
upp domain-wide delegation.

## 2. Lokal installation

```bash
python3.11 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# öppna .env och fyll i:
#   GOOGLE_ADS_DEVELOPER_TOKEN
#   GOOGLE_ADS_CLIENT_ID / GOOGLE_ADS_CLIENT_SECRET / GOOGLE_ADS_REFRESH_TOKEN
#   MCP_AUTH_TOKEN (valfri lokalt, men generera en riktig hemlighet innan deploy)
```

## 3. Testa lokalt mot ett Google Ads-testkonto

Starta servern över stdio (enklast för snabb felsökning, ingen HTTP/auth
inblandad):

```bash
python server.py --transport stdio
```

Eller starta som HTTP-server (samma sätt som i produktion):

```bash
python server.py
# Startar på http://0.0.0.0:8000/mcp
```

### Testa `run_gaql_query` och `list_campaigns` manuellt

Enklaste sättet att testa mot ett riktigt testkonto utan att koppla in
ChatGPT/Claude är att skriva ett litet Python-skript som pratar MCP direkt:

```python
import asyncio
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

async def main():
    async with streamablehttp_client(
        "http://127.0.0.1:8000/mcp",
        headers={"Authorization": "Bearer <ditt MCP_AUTH_TOKEN>"},
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool(
                "list_campaigns", {"customer_id": "123-456-7890"}
            )
            print(result)

            result = await session.call_tool(
                "run_gaql_query",
                {
                    "customer_id": "123-456-7890",
                    "query": "SELECT campaign.id, campaign.name, metrics.clicks "
                             "FROM campaign WHERE segments.date DURING LAST_7_DAYS",
                },
            )
            print(result)

asyncio.run(main())
```

Byt ut `123-456-7890` mot ditt testkontos customer-id (finns i toppen av
Google Ads-gränssnittet).

## 4. Koppla till Claude / ChatGPT

### Claude Desktop (stdio, lokalt)

Lägg till i din Claude Desktop-konfiguration (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "google-ads": {
      "command": "/absolut/sökväg/till/venv/bin/python",
      "args": ["/absolut/sökväg/till/server.py", "--transport", "stdio"],
      "env": {
        "GOOGLE_ADS_DEVELOPER_TOKEN": "...",
        "GOOGLE_ADS_CLIENT_ID": "...",
        "GOOGLE_ADS_CLIENT_SECRET": "...",
        "GOOGLE_ADS_REFRESH_TOKEN": "..."
      }
    }
  }
}
```

### Claude / ChatGPT Developer Mode (streamable-http, fjärransluten)

När servern är deployad (se nedan) pekar du en "Custom Connector" /
"Developer Mode MCP server" mot:

```
https://<din-deploy-url>/mcp
```

med en HTTP-header:

```
Authorization: Bearer <ditt MCP_AUTH_TOKEN>
```

Exakt hur du lägger till en anpassad connector skiljer sig mellan
ChatGPT (Inställningar → Connectors → Developer mode) och Claude
(Inställningar → Connectors → Add custom connector) - båda ber om en
URL och stödjer att skicka med en bearer-token/API-nyckel.

## 5. Deployment (Railway / Render)

Repot innehåller både en `Dockerfile` och en `Procfile` - använd det som
passar din valda plattform.

**Railway:**
1. Skapa ett nytt projekt från detta repo.
2. Railway upptäcker `Dockerfile` automatiskt och bygger imagen.
3. Sätt miljövariablerna från `.env.example` under **Variables**
   (`GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CLIENT_ID`,
   `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN`,
   `GOOGLE_ADS_LOGIN_CUSTOMER_ID` vid behov, `MCP_AUTH_TOKEN`).
   Railway sätter `PORT` automatiskt - servern läser den i `config.py`.
4. Efter deploy nås servern på `https://<ditt-projekt>.up.railway.app/mcp`.

**Render:**
1. Skapa en ny **Web Service** från detta repo.
2. Render kan antingen bygga från `Dockerfile` eller använda `Procfile`
   (Environment: Python, Build Command: `pip install -r requirements.txt`,
   Start Command: `python server.py`).
3. Sätt samma miljövariabler som ovan under **Environment**.
4. Efter deploy nås servern på `https://<din-tjänst>.onrender.com/mcp`.

I båda fallen: **sätt ett starkt `MCP_AUTH_TOKEN` innan du delar URL:en** -
generera ett med t.ex.:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Säkerhet

- **Bearer-token-auth**: alla HTTP-anrop till `/mcp` kräver headern
  `Authorization: Bearer <MCP_AUTH_TOKEN>` om variabeln är satt. Är den
  inte satt loggas en tydlig varning vid uppstart och servern körs helt
  utan auth (bra för lokal utveckling, **farligt i produktion**).
- **Inga hemligheter i loggar**: varken developer token, client secret,
  refresh token eller service account-nyckel skrivs någonsin till loggar.
  Skrivoperationer loggas till stdout med tidsstämpel, en maskerad
  identifierare för anroparen (sista 6 tecknen av bearer-token) och vilken
  åtgärd som utfördes - för spårbarhet, inte för att exponera hemligheter.
- **Skrivoperationer är begränsade**: enda skrivvägarna är att skapa en
  RSA (alltid `PAUSED`), pausa en annons eller aktivera en annons. Inget
  annat kan muteras via den här servern.
- **`.env`, `google-ads.yaml` och alla `*.json`-nycklar är gitignorade** -
  committa aldrig riktiga hemligheter till repot.

## Felhantering

- Alla anrop mot Google Ads API är omslutna i try/except för
  `GoogleAdsException`. Vid fel loggas `request_id` samt `error.code` och
  `message` för varje enskilt fel i svaret, och ett tydligt
  sammanfattande felmeddelande skickas tillbaka till MCP-klienten.
- `customer_id` normaliseras automatiskt (bindestreck/mellanslag tas bort)
  och valideras (måste vara enbart siffror efter normalisering).
- Timeout för GAQL-queries och mutate-anrop styrs av
  `GOOGLE_ADS_TIMEOUT_SECONDS` (default 30 sekunder).

## Vanliga fel

| Fel | Trolig orsak |
|---|---|
| `RefreshError: invalid_client` | Fel `GOOGLE_ADS_CLIENT_ID`/`GOOGLE_ADS_CLIENT_SECRET`, eller OAuth-klienten är borttagen i Google Cloud Console. |
| `PERMISSION_DENIED` / `USER_PERMISSION_DENIED` | Kontot du anropar (`customer_id`) är inte kopplat till det Google-konto refresh-token genererades för, eller så saknas `GOOGLE_ADS_LOGIN_CUSTOMER_ID` när du går via ett manager-konto. |
| `DEVELOPER_TOKEN_NOT_APPROVED` | Din developer token har bara test-access men du anropar ett riktigt (icke-test-)konto. |
| `401 Unauthorized` från MCP-servern | Fel eller saknad `Authorization: Bearer <token>`-header mot din egen server (inte Google Ads API). |

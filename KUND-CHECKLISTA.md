# Checklista: vad behövs från kunden?

Den här filen är till för överlämningen mellan dig och kunden - vad **kunden
måste göra själv** (för att de äger kontona) och vad **du behöver få av dem**
för att kunna fylla i `.env` och driftsätta MCP-servern.

Generellt gäller för alla fyra plattformar: kunden äger sina annonskonton,
så vissa steg (skapa appar, godkänna åtkomst, generera tokens) måste göras
av någon med admin-rättigheter på deras konto - antingen kunden själv, eller
dig om de ger dig tillfällig admin-åtkomst.

---

## Snabb sammanfattning - minimum för att komma igång

| Plattform | Obligatoriskt för drift? | Kundens jobb tar ungefär |
|---|---|---|
| **Google Ads** | Ja - servern startar inte utan detta | 15-30 min (om Manager-konto redan finns) |
| **Meta Ads** | Nej, additivt | 15-30 min |
| **TikTok Ads** | Nej, additivt | 30-60+ min (kan kräva godkännande från TikTok) |
| **Snapchat Ads** | Nej, additivt | 20-30 min |

Ni behöver inte ha allt klart samtidigt - servern startar fint med bara
Google Ads konfigurerat, och ni lägger till de andra plattformarna när
kunden hunnit ordna dem.

---

## 1. Google Ads (obligatoriskt)

### Vad kunden behöver göra
1. **Ge dig åtkomst till deras Google Ads-konto.** I Google Ads-gränssnittet:
   `Verktyg och inställningar` → `Åtkomst och säkerhet` → bjud in dig med
   din Google-mailadress (räcker med "Standard"-åtkomst för läsning +
   skrivning av annonser).
2. **Bekräfta om de har ett Google Ads Manager-konto (MCC).** Om de har
   flera annonskonton eller sköts av en byrå finns oftast redan ett. Om
   inte - de (eller du, om ni skapar det ihop) behöver skapa ett för att
   kunna hämta en developer token.
3. **Hämta en developer token** i Manager-kontot: `Verktyg och
   inställningar` → `API Center` (https://ads.google.com/aw/apicenter).
   Ett nytt konto får oftast bara "test account access" till en början -
   fullt godkännande för produktionskonton kräver en ansökan till Google
   (kan ta några dagar).
4. **Bestämma vem som skapar OAuth-klienten** (Client ID + Secret) i
   Google Cloud Console. **Rekommendation:** kunden skapar den i sitt
   eget Google Cloud-projekt, så äger de åtkomsten och kan återkalla den
   närsomhelst. Alternativt kan du göra det i ett projekt du kontrollerar.
5. **Generera en refresh token** - görs EN gång genom att köra ett litet
   skript (se README) som öppnar en webbläsare där kunden loggar in med
   det Google-konto som ska ha åtkomst till Ads-kontot och godkänner
   åtkomsten. Antingen kör kunden skriptet själv och skickar dig
   `refresh_token`, eller så gör ni det tillsammans (skärmdelning).

### Vad du behöver få av kunden
- [ ] **Developer token**
- [ ] **Customer ID** för det/de konto(n) som ska läsas/skrivas till
      (t.ex. `123-456-7890`)
- [ ] **Login customer ID** (MCC-kontots id) - endast om de har flera
      underkonton under ett manager-konto
- [ ] **OAuth Client ID + Client Secret**
- [ ] **Refresh token**
- [ ] Besked om kontot är ett **testkonto eller ett skarpt/live-konto**
      (påverkar vilken developer token-nivå som krävs)

---

## 2. Meta (Facebook/Instagram) Ads (valfritt/additivt)

### Vad kunden behöver göra
1. **Ge dig åtkomst till deras Business Manager**, antingen som
   Admin/Employee-användare eller genom att lägga till dig som Partner
   (Business Settings → Users/Partners).
2. **Skapa en app** i Meta for Developers kopplad till deras Business
   Manager, med produkten **Marketing API** aktiverad (eller ge dig
   åtkomst att göra det).
3. **Skapa ett System User** i Business Manager och koppla det till
   deras ad account med minst `ads_read`-behörighet.
4. **Generera ett long-lived access token** för System User-kontot
   (Business Settings → System Users → Generate New Token, scope
   `ads_read`).
5. Om kontot ni ska läsa från ligger **utanför** kundens egen Business
   Manager (t.ex. ett annat företags konto): då krävs det att Meta
   godkänner appen via **App Review** - detta tar längre tid och bör
   flaggas till kunden i god tid.

### Vad du behöver få av kunden
- [ ] **META_ACCESS_TOKEN** (System User long-lived token)
- [ ] **Ad account-id** (t.ex. `act_1234567890`)
- [ ] Ev. **App Secret** (endast om ni vill använda appsecret_proof)

---

## 3. TikTok Ads (valfritt/additivt)

### Vad kunden behöver göra
1. **Ge dig åtkomst till deras TikTok for Business-konto / Ads Manager.**
2. **Skapa en Developer-app** i TikTok Business API-portalen
   (business-api.tiktok.com/portal) och ansöka om åtkomst till
   **Marketing API**. TikTok granskar ansökningar manuellt - räkna med
   att detta kan ta tid, flagga det till kunden tidigt i processen.
3. **Auktorisera appen mot sitt annonsörskonto** - en person med
   admin-rättigheter på kontot (kunden, eller du om ni gett dig
   tillfällig åtkomst) loggar in och godkänner åtkomsten i TikToks
   OAuth-flöde, vilket ger en access token.

### Vad du behöver få av kunden
- [ ] **TIKTOK_ACCESS_TOKEN**
- [ ] **Advertiser ID** (finns i TikTok Ads Manager → kontoinställningar)

---

## 4. Snapchat Ads (valfritt/additivt)

### Vad kunden behöver göra
1. **Ge dig åtkomst till deras Snapchat Business Manager.**
2. **Skapa en OAuth2-app** under Business Settings → API Access (eller
   ge dig åtkomst att göra det) - ger `client_id` och `client_secret`.
3. **Genomföra OAuth-auktoriseringsflödet en gång** (webbläsarbaserat -
   kunden, eller den som administrerar kontot, loggar in och godkänner
   åtkomsten) för att generera en `refresh_token`. Servern förnyar sedan
   access token automatiskt - ingen mer manuell hantering behövs efter det.

### Vad du behöver få av kunden
- [ ] **SNAPCHAT_CLIENT_ID**
- [ ] **SNAPCHAT_CLIENT_SECRET**
- [ ] **SNAPCHAT_REFRESH_TOKEN**
- [ ] **Ad account-id**

---

## 5. Drift/deploy - saker att stämma av med kunden

- [ ] **Vem äger deploy-plattformen?** (Railway/Render) - ska tjänsten
      ligga i ditt eller kundens konto/faktureringsansvar?
- [ ] **Vem äger secrets på lång sikt?** Rekommendation: kunden bör
      kunna återkalla/rotera sina egna tokens (developer token, access
      tokens, refresh tokens) utan att vara beroende av dig.
- [ ] Bestäm **MCP_AUTH_TOKEN** - den genererar du själv (ett slumpat
      värde), och delar sedan säkert med den/de som ska ansluta
      ChatGPT/Claude till servern.

## Säkerhet vid överlämning av uppgifter

Alla ovanstående är hemligheter (tokens/secrets) som ger åtkomst till
kundens annonskonton - hantera dem därefter:

- Be kunden **aldrig** skicka dem i vanlig e-post eller Slack i klartext
  om ni kan undvika det - använd en lösenordshanterare med delningsfunktion
  (1Password, Bitwarden m.fl.) eller en tillfällig, krypterad delning.
- Lagra dem bara i `.env`-filen lokalt eller som miljövariabler i
  deploy-plattformens hemlighetslager (Railway/Render Variables) - aldrig
  i git (se `.gitignore` i projektet).
- Där det går: föredra att **kunden bjuder in dig som användare** på sina
  konton (Google Ads, Business Manager) framför att de skickar
  färdiga tokens - då kan de återkalla din åtkomst separat utan att rotera
  hela integrationen.

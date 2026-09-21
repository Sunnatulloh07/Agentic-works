# O'zbekiston CRM Ekotizimi, Ko'p Kanalli Aloqa va AI Agentlar Arxitekturasi

> ## ⚠️ AUDIO BO'YICHA QAROR O'ZGARGAN — USHBU HUJJATNING 2.1 VA 3-BO'LIMLARINI O'QISHDA
>
> Bu hujjat **mijozga ko'rsatilgan tarixiy dizayn** (v0.4 davri). Uning 2.1-bo'limi
> qo'ng'iroq audiosini **S3'ga saqlashni** va 3-bo'limi CRM'ga **`audio_url`**
> yozishni tasvirlaydi.
>
> **v0.5 qarori buni bekor qildi.** Amaldagi qoida:
>
> * **Audio platformaga hech qachon yetib kelmaydi va saqlanmaydi.** Bu
>   optimizatsiya emas, **strukturaviy** chegaradir.
> * P14 (telephony) ning uchta bosqichida ham invariant: audio yo'q, dialer
>   yozilmagan, ikkinchi xabar yo'li yo'q.
> * `roadmap_v05.decision`: *"Video frames never reach the platform…"* — xuddi shu
>   mantiq audio uchun ham.
>
> Kelajakdagi ishtirokchi faqat shu hujjatni o'qib, audio yo'lini qayta qursa —
> bu regressiya. Amaldagi manbalar: `docs/prd-v05/*`, `api-python/platform_runtime/telephony.py`,
> `scripts/probe_telephony_consent.py`.

Ushbu hujjat Agent Platform loyihasining O'zbekiston va Markaziy Osiyo bozoridagi barcha real CRM tizimlari bilan integratsiyalashuvi, ko'p kanalli (Call-markaz, Telegram, WhatsApp, Instagram) ma'lumotlarni CRM'ga yig'ish, audio va matnli suhbatlarni qayd etish hamda AI agentlarning ushbu lidlar ustida uzluksiz avtonom ishlash (qayta yozish, qayta qo'ng'iroq qilish) siklini belgilaydi.

---

## 1. O'zbekistonda Amalda Qo'llaniladigan CRM Tizimlari Ro'yxati

O'zbekiston bizneslari faqat Bitrix24 yoki amoCRM bilan cheklanmaydi. Har bir sohaning o'z ixtisoslashgan va ommalashgan CRM tizimlari mavjud:

```
                            ┌──────────────────────────────────────────────────┐
                            │          UNIVERSAL AGENT PLATFORM                │
                            └────────────────────────┬─────────────────────────┘
                                                     │
         ┌───────────────────┬───────────────────────┼────────────────────────┬───────────────────┐
         ▼                   ▼                       ▼                        ▼                   ▼
  [Universal CRM]     [Ta'lim / Kurslar]      [Retail & Fashion]       [HoReCa / Taom]     [Xizmatlar / Go'zallik]
  • Bitrix24          • MODME CRM             • BILLZ POS & CRM        • Jowi              • YClients / Altegio
  • Kommo (amoCRM)    • Alfa CRM              • MoySklad               • Poster POS        • MedElement / MedCRM
  • 1C:CRM / 1C:ERP                           • 1C:Roznitsa            • iiko / R-Keeper
  • Zoho / HubSpot                                                     • Restik
```

### 1.1. Universal va Savdo Boshqaruvi CRM'lari
1. **Bitrix24 (Cloud va On-premise):**
   * Yirik korxonalar, distribyutorlar va banklar. Ko'p funksiyali (vazifalar, CRM, xodimlar boshqaruvi).
2. **Kommo (amoCRM):**
   * B2B va B2C savdo bo'limlari, savdo voronkasi (sales funnel) bo'yicha eng ommabop.
3. **1C:CRM / 1C:ERP / 1C:УТ (Управление торговлей):**
   * O'zbekiston buxgalteriya va ulgurji savdo korxonalarining 70% dan ortig'i foydalanadigan asosiy hisob tizimi.
4. **Zoho CRM / HubSpot:**
   * IT kompaniyalar, eksportchilar va xalqaro servislar.

### 1.2. Vertikal va Sohaviy CRM Tizimlari (O'zbekistonda yetakchilar)
1. **MODME CRM ([modme.uz](https://modme.uz)):**
   * **Soha:** O'quv markazlari, xususiy maktablar, IT akademiyalar, til kurslari (Najot Ta'lim, PDP, Cambridge va 1000+ markazlar).
   * **Xususiyati:** O'quvchilar balansi, davomat, sinov darsiga yozilish, ota-onalarga xabar, pedagoglar oyligi.
2. **BILLZ POS & CRM ([billz.uz](https://billz.uz)):**
   * **Soha:** Kiyim-kechak (fashion retail), poyafzal, kosmetika, aksessuarlar, do'konlar tarmoqlari (Selfie, Terra Pro, Vicco va hk.).
   * **Xususiyati:** Do'kon kassa tizimi, mijozlar sodiqlik dasturi (loyalty cashback/ballar), kiyim o'lchamlari va qoldiqlari, SMS xabarnomalar.
3. **MoySklad ([moysklad.ru](https://moysklad.ru)):**
   * **Soha:** Kichik va o'rta ulgurji/chakana savdo, internet do'konlar, ishlab chiqarish sexlari.
   * **Xususiyati:** Ombor qoldiqlari, mahsulot tannarxi, buyurtmalar boshqaruvi.
4. **YClients / Altegio ([yclients.com](https://yclients.com)):**
   * **Soha:** Go'zallik salonlari, barbershoplar, SPA, stomatologiya, xususiy tibbiy klinikalar.
   * **Xususiyati:** Mutaxassislar vaqti bo'yicha online bron qilish, mijoz kartochkasi, tashriflar tarixi.
5. **Jowi / Poster POS / iiko / Restik:**
   * **Soha:** Restoranlar, kafelar, fast-food, yetkazib berish (delivery) xizmatlari.
   * **Xususiyati:** Stol bron qilish, yetkazib berish buyurtmalari, kuryerlar boshqaruvi.
6. **Leadbox / SalesDoc:**
   * **Soha:** Distribyutsiya, furgon savdosi (van selling), savdo agentlari monitoringi.

---

## 2. Ko'p Kanalli Qabul Qilish Arxitekturasi (Multi-Channel Ingestion)

Mijoz qayerdan murojaat qilmasin, tizim ma'lumotni to'liq qamrab oladi:

```
  [Kiruvchi Kanallar]
  ├── 1. AI Call-Markaz (SIP/WebRTC audio) ──┐
  ├── 2. Telegram (Bot & Shaxsiy akkaunt) ───┤
  ├── 3. WhatsApp Business API ──────────────┼──► [Agent Platform Router] ──► [Unified CRM Adapter]
  ├── 4. Instagram Direct & Comments ────────┤              │                          │
  └── 5. Veb-sayt / Lead Ads / Webhooklar ───┘              ▼                          ▼
                                                    [Customer 360 DB]         [Target CRM Card]
                                                    (Audio + Text log)        • Audio fayl link
                                                                              • To'liq Transkript
                                                                              • Sentiment & Xulosa
                                                                              • Voronka bosqichi
```

### 2.1. Call-Markaz (AI Ovozli Aloqa) Ingestion:
* **Kiruvchi qo'ng'iroq:** Virtual ATS (Uztelecom, Beeline Business, Asterisk, Zadarma) orqali SIP trunk bilan ulanadi.
* **AI Operator javobi:** O'zbek/rus tilida AI xodim suhbatlashadi.
* **Audio yozuv:** ⚠️ **BEKOR QILINGAN (v0.5).** Bu band tarixiy dizaynni tasvirlaydi. Amalda audio platformaga hech qachon yetib kelmaydi va saqlanmaydi. Quyidagi matn **o'qilmasin**: Butun suhbat audio fayli (.mp3 / .wav) shifrlangan saqlagichga (S3) yuklanadi.
* **Transkripsiya va Xulosa:**
  * To'liq matn (STT - Speech-to-Text).
  * **Summary:** Suhbatning 2-3 jumlali mazmuni (Mijoz nima so'radi, nima sotib olmoqchi).
  * **Sentiment:** Mijoz kayfiyati (`ijobiy`, `neytral`, `salbiy`, `shoshilinch`).
  * **Action items:** Qilinishi kerak bo'lgan ishlar (masalan, "Katalog yuborish", "Ertaga soat 14:00 da qayta telefon qilish").

### 2.2. Messenjerlar va Ijtimoiy Tarmoqlar Ingestion:
* **Telegram:** Suhbat tarixi, jo'natilgan rasm va lokatsiyalar.
* **WhatsApp Business:** Xalqaro va korporativ mijozlar chatlari.
* **Instagram Direct:** Mahsulot rasmiga kelgan so'rovlar, narx so'raganlar.

---

## 3. CRMga Ma'lumotlarni Yuklash va Biriktirish Qoidalari

Har bir aloqa uchun CRM'da quyidagi tuzilma yaratiladi:

```json
{
  "lead_title": "Telegram / Ovozli Qo'ng'iroq: Anvar (+998901234567)",
  "contact": {
    "name": "Anvar Karimov",
    "phone": "+998901234567",
    "channels": {
      "telegram": "@anvar_k",
      "instagram": "anvar.fashion"
    }
  },
  "deal": {
    "price": 1200000,
    "currency": "UZS",
    "stage": "lead_qualified"
  },
  "activity_call": {
    "direction": "inbound",
    "duration_seconds": 145,
    "audio_url": "SET_NONE",  // ⚠️ BEKOR QILINGAN (v0.5): audio maydoni CRMga yozilmaydi
    "transcript": "[00:01] AI: Assalomu alaykum! Erkaklar kostyumlari bo'yicha qiziqyapsizmi?\n[00:05] Mijoz: Ha, to'y uchun qora smoking bormi sizlarda?...",
    "summary": "Mijoz 50-razmer qora smoking qidirmoqda. Narxi 1.2 mln so'mga rozi, to'lov usulini so'radi.",
    "sentiment": "positive",
    "tags": ["smoking", "to'y", "hot_lead"]
  }
}
```

* **Bitrix24:** `crm.timeline.comment.add` va `crm.activity.add` orqali lid kartasiga audio fayl pleeri va matnli sharh qadab qo'yiladi.
* **Kommo (amoCRM):** `/api/v4/leads/{id}/notes` orqali maxsus `call_in` turidagi note qo'shiladi (audiosi bilan birga CRM ichida tinglash mumkin).
* **Modme:** O'quvchi profiliga sinov darsiga yozilish kartochkasi ochiladi va audio xulosa biriktiriladi.
* **BILLZ:** Mijoz profiliga qiziqqan tovar kodi (SKU), o'lchami va aloqa audio xulosasi saqlanadi.

---

## 4. AI Agentlarning CRM Ustida Qayta Ishlash Sikli (Continuous Loop)

CRM faqat ma'lumot saqlaydigan passiv baza emas, balki **AI agentlarning ish maydoni** bo'ladi:

```
                    ┌────────────────────────────────────────────────────┐
                    │      CRM'dagi Lidlar va Bitimlar Voronkasi        │
                    └─────────────────────────┬──────────────────────────┘
                                              │
                     Davriy Reja (Har 15 daqiqada Cron Scanner)
                                              │
                    ┌─────────────────────────▼──────────────────────────┐
                    │     AI Skaner va Qayta Ishlash Agentlari          │
                    └─────────────────────────┬──────────────────────────┘
                                              │
         ┌────────────────────────────────────┼────────────────────────────────────┐
         ▼                                    ▼                                    ▼
[1. Javobsiz Qolgan Lid]            [2. Qayta Qo'ng'iroq / Eslatma]       [3. Savdoni Yopish / Chegirma]
Mijozga 2 soatdan beri javob        Mijoz: "Ertaga soat 10 da            Mijoz savatni to'ldirib,
berilmagan.                         telefon qiling" degan.               to'lov qilmadi.
──► Telegram/WhatsApp orqali        ──► Belgilangan vaqtda AI             ──► Shaxsiy promokod bilan
    qayta yozadi:                       Outbound Voice Call orqali           qayta taklif yuboradi:
    "Anvar aka, smoking o'lchami        telefon qilib suhbatlashadi.         "Faqat bugun 10% chegirma!"
    bo'yicha ma'lumot tayyor!"
```

### 4.1. Avtonom Qayta Aloqa Ssenariylari (Re-engagement Scenarios):
1. **Chiquvchi Avtomatlashtirilgan Ovozli Qo'ng'iroq (Outbound AI Callback):**
   * Mijoz veb-saytda yoki botda "Menga qo'ng'iroq qiling" tugmasini bosgan.
   * AI agent 60 soniya ichida mijozga telefon qiladi, audio suhbat o'tkazadi va natijani CRM'da "muvaffaqiyatli bog'lanildi" holatiga o'tkazadi.
2. **Messenjerda Qayta Faollashtirish (Abandoned Lead Follow-up):**
   * Mijoz narxni so'rab, keyin jim bo'lib qolgan.
   * AI agent 24 soatdan so'ng xushmuomalalik bilan xabar yozadi: *"Assalomu alaykum! Kecha so'ragan modelingiz bo'yicha yana savollaringiz bormi? Sizga menejerimiz yordam berishini xohlaysizmi?"*
3. **Mijozni Tirik Operatorga Topshirish (Operator Escalation):**
   * Agar mijoz suhbatda norozi ohangda gapirsa, narx bo'yicha individual chegirma so'rasa yoki shartnoma talab qilsa:
     * Agent CRM'dagi bitimga `operator_call_needed` tegini qo'yadi.
     * Mas'ul xodimning Telegramiga tezkor xabar (Hot Lead Alert) yuboradi: *"Diqqat! Anvar aka katta buyurtma bo'yicha tirik menejer bilan gaplashmoqchi. Telefon: +998901234567"*.

---

## 5. Kengaytirilgan Universal CRM Ulagich Interfeysi

Har qanday yangi CRM (Modme, Billz, MoySklad va hk.) qo'shilganda quyidagi yagona interfeysga bo'ysunadi:

```python
class UniversalCRMAdapter(Protocol):
    # 1. Lidlar va Kontaktlar
    def create_lead(self, tenant: str, config: dict, request: CRMLeadRequest) -> dict: ...
    def get_lead(self, tenant: str, config: dict, lead_id: str) -> dict: ...
    def find_leads(self, tenant: str, config: dict, query: dict) -> list[dict]: ...
    
    # 2. Multi-channel Timeline Biriktirish
    def attach_call_record(self, tenant: str, config: dict, lead_id: str, call: CRMCallRecord) -> dict: ...
    def attach_message(self, tenant: str, config: dict, lead_id: str, message: CRMChatMessage) -> dict: ...
    
    # 3. Voronka va Bitimlar
    def update_stage(self, tenant: str, config: dict, lead_id: str, stage: str, reason: str = '') -> dict: ...
    def create_deal(self, tenant: str, config: dict, request: CRMDealRequest) -> dict: ...
    
    # 4. Qayta ishlash uchun so'rovlar (Agent Loop uchun)
    def fetch_stalled_leads(self, tenant: str, config: dict, inactive_minutes: int = 120) -> list[dict]: ...
    def fetch_scheduled_callbacks(self, tenant: str, config: dict, due_before_timestamp: float) -> list[dict]: ...
```

### 5.1 Amaldagi driver holati (v0.3.9)

Yagona interfeysdan faqat **adapteri bor** driverlar foydalanadi. Qolganlari operatorga
ko'rinadi, lekin `adapter_required` bo'lib qoladi va tool chaqiruvida aniq nom bilan
fail-closed qiladi — "unsupported driver" bilan aralashmaydi.

| Driver | Holat | Izoh |
|---|---|---|
| `bitrix24` | Bajarilgan | REST, `crm.timeline.comment.add`, phone/email equality qidiruvi |
| `kommo` / `amocrm` | Bajarilgan | API v4, complex lead create, notes |
| `onec` | Bajarilgan | Operator e'lon qilgan HTTP service; Basic/Bearer; `response_map` |
| `custom_webhook` | Bajarilgan | Operator deklaratsiyasi: method/path/body allowlist |
| `modme`, `billz`, `moysklad`, `retailcrm`, `yclients`, `jowi`, `poster` | `adapter_required` | Custom HTTP orqali hozir ulanadi; native adapter keyingi bloklar |

**Custom HTTP driver — 1C va o'z ichki CRM'ni ulashning asosiy yo'li.** Operator
`config/integrations.json`da quyidagini e'lon qiladi:

```json
{
  "driver": "custom_webhook",
  "host": "crm.example.uz",
  "allowed_hosts": ["crm.example.uz"],
  "base_path": "/api/v1",
  "credential_env": "SET_CUSTOM_CRM_TOKEN",
  "headers": {"Authorization": {"env": "SET_CUSTOM_CRM_TOKEN", "prefix": "Bearer "}},
  "operations": {
    "find_leads": {"method": "GET", "path": "/leads?q={query}&limit={limit}"},
    "create_lead": {"method": "POST", "path": "/leads",
                    "body": {"full_name": "name", "tel": "phone"}}
  },
  "response_map": {"items": "data", "id": "id", "title": "title",
                   "phone": "contacts.phone"},
  "capabilities": ["read", "plan_write", "execute_write", "reconcile"],
  "agent_ids": ["sales.responder"]
}
```

Uch chegara saqlanadi: (1) manzilni faqat operator belgilaydi — agent, pack yoki model
URL/host/port bera olmaydi; (2) har bir placeholder `quote(value, safe='')` bilan
kodlanadi; (3) javob faqat `response_map`dagi pointerlar orqali o'qiladi, topilmagan
maydon uydirilmaydi. Method `GET/POST/PUT/PATCH` bilan cheklangan, `attach_message`
yo'li `{lead_id}`ni o'z ichiga olishi shart.

To'liq ishlaydigan namuna: `config/crm-1c-custom.example.json`.
Batafsil: `docs/development/V039-IMPLEMENTATION-UZ.md`.

# PRD v0.4 — Yagona biznes miyasi (unified business brain)

Holat: **ANALYSIS / IN_PROGRESS / PRODUCTION NO_GO**
Sana: 2026-09-19
Manba: real mijoz feedbacki (O'zbekiston SMB, sotuv + ombor + moliya + HR + buxgalteriya)

Bu hujjat mijoz aytgan muammolarni **arxitektura qarorlariga** aylantiradi. Har bir band
`BACKLOG.json`dagi aniq blokka bog'lanadi. Bu tayyor capability ro'yxati emas.

---

## 1. Mijoz nima dedi — xom holda tasnif

Mijozning tizimlari bugun shunday tarqoq:

| Soha | Tizim | Kim ishlatadi |
|---|---|---|
| Sotuv / lidlar | **AmoCRM yoki Bitrix24** + IP telefoniya (call-markaz) | sotuv bo'limi |
| Reklama kirishi | **Google Ads / Facebook Ads** → sayt → CRM | marketolog |
| Tovar hisobi | **MoySklad** | ombor, sotuvchi |
| Moliya | **MoySklad ham, Google Sheets ham** | moliyachi |
| HR | Google Sheets + "Google Sheets'ga o'xshagan" alohida platforma (suhbatlar, davomat) | HR |
| Buxgalteriya / soliq | alohida tizim | buxgalter |
| Ishlab chiqarish | alohida (yoki hech narsa) | sex |

Mijozning aniq gaplari va ularning ma'nosi:

| Mijoz gapi | Biznes talabi |
|---|---|
| "solishtirishning imkoniyati juda qiyinlanib qolgan" | Bir tovarning **ombor + narx + sotuv + moliya** holatini bir joyda ko'rish |
| "Uning narxlarini olib yurish ham muammo" | Sotuvchiga **real vaqt narx/qoldiq** javobi (Excel/PDF ko'chirmasdan) |
| "Hamma narsani umumlashtiradigan tizim juda kam" | **Kross-tizim yagona ko'rinish** |
| "Odonni o'rgatish muammo" | Yangi tizim **o'rgatishni talab qilmasligi** kerak |
| "SAPni SAPga qilaman desa, SAP shunaqa muammo" | Og'ir ERP **o'rnini bosmaymiz** |
| "xodimlarga o'rgatish muammo" | **O'qitish = chat**, o'zbek tilida, mavjud ekranlarda |
| "xodimlari vaqtiga ma'lumotlarni qilib berolmaydi" | **Vaqt tanqisligi** — avtomatik yig'ish |
| "ba'zilari puldan qizg'onadi" | Narx **past**, qiymat **ko'rinadigan** bo'lishi shart |
| "ishlab chiqarish siklidagi muammolar juda ko'p" | Ishlab chiqarish **keyingi faza**, lekin arxitektura ochiq |
| "avvalroq telefonlashgan ovozli yozib olinganlar ustida ishlab" | **Ovozli yozuvlarni qayta ishlash** (call-markaz) |
| "call center yoki marketologlarga datalarni olib chiqish" | Tuzilgan ma'lumot **eksport**i |
| "hodim so'rasa va so'ramasa ham avtomatlashtirib yig'ib yuborishi" | **Proaktiv brifing / alert** |
| "to'liq tizimni o'z qo'liga oladigan agentic" | **Agentic nazorat** (ladder + approval) |

## 2. Asosiy xulosa: biz yangi tizim emas, aqlli qatlam qurmoqdamiz

Mijozning eng qimmat gapi: **"Odonni o'rgatish muammo"**. Bu butun loyihaning
pozitsiyasini belgilaydi.

Odoo/SAP yiqilgan joyi — ular **yangi tizim**: yangi ekran, yangi atama, yangi jarayon.
Xodim uni o'rganishi kerak, vaqt yo'q, pul qizg'anadi. Shuning uchun:

> **Bizning platforma mavjud tizimlarni almashtirmaydi. U ularning ustida turadi.**

Xodim uchun hech narsa o'zgarmaydi: u AmoCRM'da lidni ko'radi, MoySklad'da qoldiqni
ko'radi, Google Sheets'da hisobotni yozadi. Farq shundaki, endi u **o'zbekcha so'raydi**
va javobni **hamma tizimdan bir vaqtda** oladi — ko'chirmasdan, Excel ochmasdan.

Bu PRD'ning 1-tamoyili bilan aynan mos: *core bir marta yoziladi, pack config'da yashaydi*.

## 3. Nima uchun bu bizning mavjud arxitekturaga sig'adi

| Mijoz talabi | Mavjud mexanizm | Holat |
|---|---|---|
| AmoCRM/Bitrix24 sotuv | `crm/kommo_adapter.py`, `crm/bitrix24_adapter.py` | Bajarilgan |
| 1C buxgalteriya | `crm/onec_adapter.py` | Bajarilgan |
| Boshqa har qanday CRM/ERP | `crm/custom_http_adapter.py` (operator deklaratsiyasi) | Bajarilgan |
| Mijoz bazasi (SQL/NoSQL) | `database/` gateway (8 backend) | Bajarilgan (contract) |
| Gmail/Drive/Calendar | `google_adapters.py`, `google_sync.py` | Bajarilgan (metadata) |
| Lid qayta ishlash sikli | `reengagement.py` + `agent_loop.py` | Bajarilgan |
| Ovoz chiqishi (TTS) | `speech.py` (Aisha) | Qisman |
| Bilim/RAG | `knowledge.py` (BM25 + vektor) | Qisman |
| Lokal qurilma (printer, ekran, 1C klient) | `apps/runner` | Faqat Linux |
| **Google Sheets moliya/HR** | faqat bitta qattiq `sheets.append` | **YETISHMAYDI** |
| **MoySklad tovar/qoldiq** | yo'q | **YETISHMAYDI** |
| **Telefoniya / ovoz yozuvlari** | yo'q | **YETISHMAYDI** |
| **Kross-tizim yagona ko'rinish** | yo'q | **YETISHMAYDI** |
| **Proaktiv brifing** | yo'q | **YETISHMAYDI** |

Ya'ni **asos bor, lekin mijoz aytgan to'rtta eng og'riqli joy yo'q**.

---

## 4. Gap analysis: mijoz talabi ↔ bugungi kod

### 4.1 To'liq yecha olamizmi? — Halol javob

**Qisqa javob: ha, lekin bugungi holatda emas.** Quyidagi jadval "nima tayyor / nima
kerak / qancha ish" ni ko'rsatadi. Bu taxmin emas, har bir qator kodga bog'langan.

| Mijoz talabi | Kerak bo'ladigan source | Bugun bor | Baho |
|---|---|---|---|
| AmoCRM/Bitrix24 lidlar, voronka, timeline | — | Bor | Tayyor |
| 1C buxgalteriya o'qish/yozish | — | Bor | Tayyor |
| MoySklad tovar, qoldiq, narx | yangi typed adapter | Yo'q | **1 blok** |
| Google Sheets moliya/HR (ko'p jadval, ko'p varaq) | ko'p-manzilli Sheets adapter | Faqat 1 ta append | **1 blok** |
| Holat o'zgarishini kuzatish (qoldiq, narx, lid) | webhook/polling + dedup | Reengagement bor | **1 blok** |
| Ovoz yozuvlarini matnga aylantirish (STT) | Aisha STT typed tool | Faqat TTS | **1 blok** |
| Ovozli yozuvdan lid, e'tiroz, sifat, sentiment | STT + tahlil + CRM timeline | Yo'q | **1 blok** |
| Vaqtida qilish kerak bo'lgan ishlar nazorati | scheduler + eskalatsiya | Qisman | **1 blok** |
| Kross-tizim yagona ko'rinish (Customer/Product 360) | yagona read-model + query | Qisman (C360 bor) | **1 blok** |
| Proaktiv brifing (so'ramasa ham) | scheduler + alert + digest | Yo'q | **1 blok** |
| Xodim o'qitmasdan ishlatishi | UI + o'zbekcha chat + pack persona | Qisman | **1 blok** |
| Ishlab chiqarish sikli | alohida adapter + BOM | Yo'q | **keyingi faza** |
| IP telefoniya (qo'ng'iroq qilish) | telephony adapter | Yo'q | **keyingi faza** |

**Xulosa:** 11 blokdan **10 tasi bizning mavjud arxitekturamiz ichida bajariladi** —
chunki hammasi "typed adapter + approval + reconcile + audit" naqshiga tushadi. Yangi
paradigma kerak emas. Bu eng muhim topilma.

### 4.2 Lekin uchta narsa **haqiqatan qiyin** — va buni yashirmayman

| Qiyinlik | Nega | Yechim |
|---|---|---|
| **Ovoz** | O'zbek shevasi, ruscha aralash matn, shovqin, 3 kishilik suhbat. Diarizatsiya (kim gapirdi) aniqligi past bo'lishi mumkin | Fazali: avval STT + inson tekshiruvi; ishonch past bo'lsa odamga eskalatsiya |
| **Aqlli tizimlarning sxemasi o'zgaradi** | MoySklad/1C/AmoCRM o'z maydonlarini o'zgartiradi. Adapter buziladi | `response_map` operator konfiguratsiyasi + `health` probe + drift aniqlash |
| **"Narxni olib yurish"** | Narx qaysi tizimda "haqiqiy"? MoySklad, 1C va Sheets uch xil raqam berishi mumkin | **Manba ustuvorligi (source of truth)** siyosati pack'da; ziddiyat aniqlansa ziddiyat sifatida ko'rsatiladi, jim tanlanmaydi |

Bu uchtasi PRD'da **ochiq risklar** sifatida qoladi. Ularni "yechilgan" deb e'lon qilish
noto'g'ri bo'lardi.

### 4.3 Mijozning "hamma narsani umumlashtirish" talabiga aniq javob

Mijoz aslida **ikkita narsa** so'ramoqda, va ular ajratilishi shart:

1. **Read-side birlashtirish (yagona ko'rinish).** Bir savol → hamma tizimdan javob.
   Bu **mumkin va arzon**: adapterlar faqat o'qiydi, hech narsa buzilmaydi, migratsiya yo'q.
   Mijozning "solishtirish qiyin" og'rig'i aynan shu.
2. **Write-side birlashtirish (bitta tizimdan hammasini boshqarish).** Bir joydan
   yozib hamma tizimni yangilash. Bu **xavfli**: double-entry, narx ziddiyati, ombor
   nomuvofiqligi. Shuning uchun **har doim approval + reconcile**, va default **o'chirilgan**.

> **Qaror:** v0.4 read-side birlashtirishdan boshlanadi. Write-side faqat bitta tizimga
> (manba egalari) yozadi, boshqalari read yoki derived.

## 5. Ustuvorlik: nima birinchi

Mijoz "pul to'lashga rozi, lekin vaqt yo'q" dedi. Demak **birinchi bo'lib vaqt
tejaydigan ish** qilinishi kerak, eng ko'p integratsiya emas.

| Faza | Nima | Nega birinchi | Mijozga ko'rinadigan natija |
|---|---|---|---|
| **P1** | Kross-tizim o'qish + yagona ko'rinish | Migratsiya yo'q, risk past, og'riq eng katta | "Anvar aka buyurtmasi bo'yicha: MoySkladda qoldiq 3, AmoCRM'da lid 'kelishuv', narx 450k" |
| **P2** | Proaktiv brifing | "so'ramasa ham" talabi | Har kuni 9:00'da sotuvchiga 8 ta harakat kerak bo'lgan lid |
| **P3** | Google Sheets moliya/HR (ko'p jadval) | Moliyachi/HR allaqachon Sheets'da; hech narsa o'rgatish kerak emas | Hisobotni so'z bilan so'raydi, avtomatik yig'iladi |
| **P4** | MoySklad tovar/qoldiq/narx | Sotuvchi narxni olib yurmasin | Telegram'da qoldiq va narx javobi |
| **P5** | Ovoz yozuvlarini qayta ishlash | Call-markaz + sotuv sifati | Yozuvdan: e'tiroz, kelishuv, keyingi qadam |
| **P6** | Nazorat va eskalatsiya | "vaqtida qilinmadi" og'rig'i | Muddati o'tgan ish menejerga Telegram'da |
| **P7** | Ishlab chiqarish / telefoniya | Eng og'ir, eng kech | keyingi faza |

Har faza **alohida qabul qilinadi** va oldingisi ishlagandan keyin boshlanadi.

---

## 6. Arxitektura: Business Graph (yagona o'qish qatlami)

P1 ni bajarish uchun yangi tushuncha kerak: **Business Graph** — mijozning barcha
tizimlaridan yig'ilgan, faqat o'qish uchun, tenant-scoped yagona ko'rinish.

### 6.1 Nima uchun bu to'g'ri yechim

Mijozning har bir tizimi **o'z haqiqatiga ega** va biz ularni birlashtirmaymiz — biz
ularga **ishora qilamiz**. Bu "master data management" loyihalari yiqilgan joyi:
ular mijozni bitta tizimga ko'chirishni talab qiladi. Biz ko'chirmaymiz.

```
              ┌──────────────────────────────────────────────┐
              │            BUSINESS GRAPH (read-only)        │
              │  entity: customer | product | order | deal   │
              │          invoice | employee | call | task    │
              │  har bir atribut: qiymat + MANBA + ishonch   │
              └────────────────────┬─────────────────────────┘
                                   │  faqat o'qish, har bir soha o'z adapteri orqali
      ┌────────────┬───────────────┼───────────────┬──────────────┬────────────┐
      ▼            ▼               ▼               ▼              ▼            ▼
  AmoCRM/       MoySklad      1C buxgalteriya  Google Sheets  Telefoniya   Ichki DB
  Bitrix24      (tovar,       (hujjat,         (moliya,        (call        (SQL/
  (lid,         qoldiq,       soliq)           HR, davomat)    yozuvlar)    NoSQL)
   voronka)      narx)
```

### 6.2 Eng muhim qoida: har bir atribut manbasini biladi

Mijoz "narxni olib yurish muammo" dedi. Muammo shundaki, uch tizim uch xil narx
ko'rsatadi. Biz **bitta raqamni tanlab jim qo'ymaymiz** — biz **qaysi tizimdan
kelganini va ishonchni** ko'rsatamiz:

```json
{
  "entity": "product",
  "id": "SKU-1042",
  "attributes": {
    "price":           {"value": 450000, "source": "moysklad", "observed": 1726745000},
    "cost":            {"value": 310000, "source": "moysklad", "observed": 1726745000},
    "stock_quantity":  {"value": 3,      "source": "moysklad", "observed": 1726745000},
    "price_1c":        {"value": 462000, "source": "onec",     "observed": 1726741000},
    "margin_sheet":    {"value": 140000, "source": "sheets:finance", "observed": 1726744000}
  },
  "conflicts": [
    {"attribute": "price", "values": [450000, 462000],
     "sources": ["moysklad", "onec"], "rule": "source_priority"}
  ]
}
```

Ya'ni mijoz **ziddiyatni ko'radi**, biz uni yashirmaymiz. Bu ishonch masalasi: buxgalter
1C raqamini bilishi kerak, sotuvchi MoySklad raqamini. Ikkalasi ham to'g'ri — kontekst
boshqa.

### 6.3 Manba ustuvorligi (source of truth) — pack'da

Ziddiyat qaysi tizimda hal qilinishini **mijoz o'zi** pack'da belgilaydi:

```yaml
business_graph:
  entities:
    product:
      price:        { primary: moysklad, fallback: [onec, sheets.finance] }
      stock:        { primary: moysklad }        # ombor faqat MoySkladda
      tax_category: { primary: onec }            # soliq faqat 1C'da
    customer:
      deal_stage:   { primary: amocrm }
      balance:      { primary: onec }
      phone:        { primary: amocrm, fallback: [sheets.hr] }
  conflict_policy: report            # report | primary_wins | newest_wins
```

`conflict_policy: report` — default va tavsiya etilgan. Jim tanlash (silent pick)
noto'g'ri buxgalteriya qarorlariga olib keladi.

### 6.4 Yangi tool'lar (P1)

| Tool | Xavf | Vazifa |
|---|---|---|
| `graph.entity` | read | Bitta entity'ning barcha manbalardan yig'ilgan ko'rinishi |
| `graph.search` | read | Nom/telefon/SKU bo'yicha entity topish |
| `graph.timeline` | read | Bitta mijoz/buyurtma bo'ylab xronologik voqealar |
| `graph.conflicts` | read | Hal qilinmagan ziddiyatlar ro'yxati |
| `graph.explain` | read | "Bu raqam qayerdan keldi?" — manba zanjiri |

Hammasi **faqat o'qish**. Hech biri yozmaydi, shuning uchun approval talab qilmaydi va
mijozning mavjud tizimlariga **hech qanday xavf tug'dirmaydi**. Bu P1 ni eng xavfsiz
birinchi qadam qiladi.

### 6.5 Yangilanish: surish emas, tortish

Business Graph **provider'ga yozmaydi**. U ikki usulda yangilanadi:

- **Polling** — rejalashtirilgan, chegaralangan o'qish (`google_sync.py` naqshi allaqachon bor).
- **Webhook** — provider o'zi xabar beradi (AmoCRM, Bitrix24, MoySklad qo'llab-quvvatlaydi).

Ikkalasida ham **dedup** majburiy: bir voqea ikki marta kelsa, ikkinchi marta
qayta ishlanmaydi. Bu `engine`dagi idempotency naqshining aynan o'zi.

---

## 7. "So'ramasa ham" — proaktiv yetkazish (P2)

Mijoz: *"hodim so'rasa va so'ramasa ham avtomatlashtirib yig'ib yuborishi"*.

Bu **re-engagement loop**ning umumlashmasi: o'sha koordinator naqshi (schedule → chegaralangan
o'qish → dedup → approval-gated yozuv) boshqa sohalarga ham qo'llanadi.

| Brifing | Kimga | Qachon | Manba |
|---|---|---|---|
| "Bugun harakat kerak bo'lgan lidlar" | sotuvchi | 09:00 | amocrm + graph |
| "Qoldig'i kam, narxi o'zgargan tovarlar" | omborchi | 09:00 | moysklad |
| "Muddati o'tgan to'lov" | moliyachi | 09:00 | onec + sheets.finance |
| "Bugun kelgan nomzodlar / davomat" | HR | 09:00 | sheets.hr |
| "Kechagi qo'ng'iroqlar sifati" | call-markaz rahbari | 10:00 | voice |

**Muhim qoida:** brifing **faqat o'qiydi va xabar yuboradi**. Hech qanday biznes
yozuvi bo'lmaydi. Shu sababli u **approval talab qilmaydi** (xabar yuborish — mavjud
`allowed_recipients` siyosati bilan cheklangan).

Koordinator: `platform_runtime/briefing.py` — `reengagement.py` bilan bir xil tuzilish,
lekin `graph` va adapter read'lar ustida ishlaydi.

## 8. Ovoz: yozib olingan qo'ng'iroqlar (P5)

Mijoz: *"avvalroq telefonlashgan ovozli yozib olinganlar ustida ishlab"* — ya'ni
**yangi qo'ng'iroq qilish emas**, **mavjud yozuvlarni qayta ishlash**.

### 8.1 Quvur (pipeline)

```
[IP telefoniya / call-markaz] ── yozuv (mp3/ogg) + metadata
        │
        ▼
  1. INGEST   — yozuvni olish (URL/env reference orqali, operator konfiguratsiyasi)
  2. STT      — matnga aylantirish (Aisha STT; o'zbek + rus aralash)
  3. DIARIZE  — kim gapirdi (operator / mijoz); ishonch past bo'lsa belgilanadi
  4. EXTRACT  — tuzilgan natija:
                { e'tirozlar[], kelishuv, keyingi_qadam, sentiment, sifat_bali }
  5. LINK     — CRM'dagi lid/buyurtma bilan bog'lash (telefon raqami bo'yicha)
  6. WRITE    — CRM timeline'ga izoh (approval-gated) + lokal tahlil saqlanadi
```

### 8.2 Bu nima uchun qimmatli

- **Sotuv sifati nazorati:** menejer 100 qo'ng'iroqni tinglay olmaydi; agent 100 tasini
  o'qib, 5 tasini "e'tibor bering" deb chiqaradi.
- **O'qitish (mijozning og'rig'i!):** yaxshi qo'ng'iroq skriptlari va yomon e'tirozlar
  to'planadi → yangi xodim **o'z kompaniyasining** yozuvlaridan o'rganadi. Bu
  "xodimlarni o'rgatish muammo" talabiga to'g'ridan-to'g'ri javob.
- **Yo'qolgan lidlar:** yozuvda "qayta qo'ng'iroq qiling" deyilgan, lekin hech kim
  qilmagan → aniqlanadi va `reengagement`ga uzatiladi.

### 8.3 Chegaralar (halol)

| Chegara | Sabab |
|---|---|
| **Ruxsat (consent) majburiy** | Ovoz yozuvi shaxsiy ma'lumot. Operator rozilik asosini hujjatlashtirishi shart; platforma faqat texnik imkoniyat beradi |
| Diarizatsiya aniqligi cheklangan | 3+ kishi va shovqinli kanalda kim gapirgani xato bo'lishi mumkin |
| Sheva va ruscha aralash | STT sifati past bo'lishi mumkin; past ishonchda odamga eskalatsiya |
| Audio saqlash muddati | Retention siyosati va shifrlash talab qilinadi |

**Muhim:** audio fayl **hech qachon modelga to'g'ridan-to'g'ri berilmaydi** — faqat
transkript. Transkript ham **ishonchsiz ma'lumot** deb belgilanadi (mijoz "ignore all
rules" deb aytishi mumkin).

## 9. "O'rgatish muammo" — xodim tajribasi (P1/P2/P3 kesimi)

Bu talabni qondirish uchun **hech qanday yangi ekran** talab qilinmaydi:

| Xodim | Bugun | Platforma bilan |
|---|---|---|
| Sotuvchi | AmoCRM'da lid ochadi, MoySklad'da qoldiq qidiradi, narxni Excel'dan oladi | Telegram'da o'zbekcha so'raydi: *"Anvar aka uchun 1042 tovar bor mi?"* → javob |
| Moliyachi | 4 ta Sheets'ni qo'lda birlashtiradi | *"Kechagi tushum"* → avtomatik yig'ilgan raqam + manba |
| HR | Suhbat yozuvlarini qo'lda yozadi | Yozuv → tuzilgan xulosa → Sheets'ga |
| Rahbar | Hech kim umumiy holatni bilmaydi | Ertalabki brifing bitta xabarda |

**Prinsip:** xodim **o'z tizimini tashlamaydi**. U faqat yangi odat oladi — *so'rash*.
Bu Odoo/SAP bilan eng katta farqimiz.

## 10. Metrikalar (qabul mezoni)

| Metrika | Maqsad | Nega bu |
|---|---|---|
| Xodim savoliga javob berish vaqti | ≤ 30 s | Excel ochishdan tez bo'lishi kerak |
| Kross-tizim so'rovda to'g'ri manba ko'rsatilishi | 100% | Ziddiyat yashirilmasin |
| Brifing yetkazilishi (09:00) | ≥ 99% | "so'ramasa ham" talabi |
| Xodim o'qitish vaqti | ≤ 1 kun | Mijozning asosiy og'rig'i |
| Yozuvdan tuzilgan natija aniqligi | ≥ 85% (inson tasdiqi bilan) | Ovoz ishonchliligi |
| CRM'ga noto'g'ri yozuv | 0 | Approval + reconcile tufayli |
| Mijoz tizimiga uzilish ta'siri | 0 | Biz read-only qatlamdammiz |

## 11. Ochiq risklar (yashirilmaydi)

1. **Narx/manba ziddiyati** — uch tizim uch xil raqam. Yechim: `conflict_policy: report`,
   lekin mijoz baribir qaysi raqam "to'g'ri" ekanini aytishi kerak.
2. **Ovoz sifati** — sheva + shovqin. Diarizatsiya xato qilishi mumkin.
3. **Provider sxemasi o'zgaradi** — adapter buziladi. `response_map` + health probe bor,
   lekin drift aniqlash hali yozilmagan.
4. **Huquqiy** — ovoz yozuvi roziligi, shaxsiy ma'lumot, saqlash muddati. Operator
   zimmasida; platforma faqat texnik imkoniyat beradi.
5. **Mijoz "hammasini bitta tizimga" ko'chirishni kutsa** — biz buni **qilmaymiz**.
   Bu kutishni boshidanoq to'g'rilash kerak.
6. **Raqobat** — Odoo/Bitrix24 o'z ekotizimini kengaytirmoqda. Bizning farqimiz: **o'zbek
   tili + o'rgatish talab qilmaslik + mavjud tizimlarga tegilmaslik**.

## 12. Bu PRD `BACKLOG.json`ga qanday tushadi

| Blok id | Faza | Holat |
|---|---|---|
| `business_graph` | P1 | TODO |
| `briefing` | P2 | TODO |
| `google_sheets_multi` | P3 | TODO |
| `inventory_moysklad` | P4 | TODO |
| `voice_call_processing` | P5 | TODO |
| `task_oversight_escalation` | P6 | TODO |
| `manufacturing_bom` | P7 | TODO |
| `telephony_outbound` | P7 | TODO |

## 13. Birinchi konkret qadam

P1 va P3 ni **birga** boshlash eng katta foyda beradi, chunki:

- Sheets adapteri (P3) — Business Graph (P1) uchun **manba** bo'ladi
- Ikkalasi ham **read-only** — mijozning tizimlariga tegmaydi
- Moliyachi va HR bugun allaqachon Sheets'da — **hech narsa o'rgatish kerak emas**

Shu sababli keyingi blok: **`google_sheets_multi`** — ko'p-jadval, ko'p-varaq Sheets
adapteri va `sheets.read` / `sheets.rows` / `sheets.append` typed tool'lar.

## 14. Bu PRD nima qilmaydi (scope chegarasi)

- Odoo/SAP o'rnini bosmaydi, mijozni migratsiya qilishga majburlamaydi
- Yangi ERP moduli (ombor, HR, buxgalteriya) yozmaydi
- Provider'ga read-only bo'lmagan ommaviy yozishni avtomatik qilmaydi
- Ovoz yozuvi uchun huquqiy rozilik asosini **yaratmaydi** (operator zimmasida)
- Ishlab chiqarish va outbound telefoniya — keyingi faza, bu PRD faqat arxitektura
  o'rnini ochiq qoldiradi
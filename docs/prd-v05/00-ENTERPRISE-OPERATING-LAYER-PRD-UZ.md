# PRD v0.5 — Korxona operatsion qatlami (enterprise operating layer)

Holat: **ANALYSIS / IN_PROGRESS / PRODUCTION NO_GO**
Sana: 2026-09-19
Manba: mijozning ikkinchi feedbacki + 2026 bozor tadqiqoti

Bu hujjat v0.4 PRD'ni **almashtirmaydi** — uni kengaytiradi. v0.4 platformani
"mavjud tizimlar ustidagi aqlli qatlam" deb belgilagan edi. v0.5 shu qatlamni
**korxona miqyosiga** chiqaradi: bo'limlar boshqaruvi, xodimlar **va** agentlar
nazorati, kamera ma'lumotlari, ishlab chiqarish va hujjatlar.

Har bir band `BACKLOG.json`dagi aniq blokka bog'lanadi.

---

## 1. Mijoz nima dedi (ikkinchi feedback, xom holda)

> "Mijozlarning biznes-jarayonlariga mos AI-agentlarni o'rnatish va sozlash.
> Agentlarni CRM, ERP, Telegram, WhatsApp, ma'lumotlar bazalari, API va
> **hujjatlar** bilan integratsiya qilish."

> "…bu faqat xodimlarga emas, **umumiy boshqaruv bo'limlariga** — xodimlarni ham,
> **agentlarni ham** boshqarish agentlarini qilishimiz kerak. Hattoki
> **kameralarni** ham ulash, kamera ma'lumotlari ustida ham ishlash va boshqa shu
> kabi **ishlab chiqarish zavodlarini har bir qism, hodim va detallar qismigacha**
> agentlarni qilishga moslashtirishimiz kerak."

Bu gapda **to'rt yangi talab** bor, va ular v0.4 dagi uchta fazadan ancha og'ir:

| # | Talab | Ma'nosi |
|---|---|---|
| T1 | CRM, ERP, Telegram, **WhatsApp**, DB, API, **hujjat** integratsiyasi | Kanallar va manbalar soni ikki baravar oshadi |
| T2 | **Boshqaruv bo'limlari** uchun agentlar | Endi sotuvchi emas, **rahbar** ham foydalanuvchi |
| T3 | Xodimlarni **va** agentlarni boshqarish agentlari | Platforma o'z agentlarini ham "xodim" sifatida boshqaradi |
| T4 | **Kamera** ma'lumotlari + ishlab chiqarish zavodining **har bir detaligacha** | Fizik dunyo (OT) qatlami qo'shiladi |

T4 — bu sifat jihatidan yangi narsa. v0.4 gacha platforma faqat **IT tizimlar**
bilan ishlagan: HTTP JSON, jadval, SQL. Kamera va sex — bu **OT** (operational
technology): real vaqt, uzluksiz oqim, koordinatalar, xodim harakati. Bu qatlam
o'z arxitekturasini talab qiladi va **huquqiy jihatdan eng nozik** qism.

---

## 2. 2026 bozorida nima ishlaydi (tadqiqot)

Quyidagi bandlar haqiqiy ochilgan manbalardan olingan. Har birida **bizga
tegishli xulosa** bor.

### 2.1 Agentic arxitekturaning qatlamlari

Manba: *Enterprise AI Agent Architecture: A 2026 Engineering Guide* (Akoode,
2026-08-22).

Manba aytadigan asosiy narsa: **"Model tanlash endi qiyin qism emas."** Qiyin
qism — *"which tools it's allowed to call, what data it can see, how its actions
get authorized, how a bad decision gets caught before it causes damage, and how
the whole system gets monitored once it's live."*

Ya'ni 2026'da bozor **governance**ni mahsulot deb sotmoqda, **model**ni emas.
Bizning arxitektura (`ladder` + `approval` + `reconcile` + `audit` + typed
adapter) aynan shu tomonda — bu tasodif emas, to'g'ri tanlov.

Manba ajratadigan to'rt toifa va bizning holatimiz:

| Toifa | Ta'rif | Bizda |
|---|---|---|
| Automation | qat'iy qoida, reasoning yo'q | `p_reengagement` sikli |
| Chatbot | bir savol-javob, tizimga tegmaydi | `knowledge.search`, `reports.summary` |
| Copilot | AI qoralama qiladi, odam qaror qiladi | `sheets.append` approval bilan |
| **Agent** | maqsad → reja → tool chaqiruv → kuzatish → takror | `agent_loop` + `plan` |

**Xulosa:** bizda to'rt toifa ham bor, lekin **agent** toifasi eng zaif
qatlamlangan: plan majburan bitta run ichida, supervisor yo'q.

### 2.2 Orkestratsiya naqshlari: supervisor / pipeline / swarm

Manba: *Multi-Agent Orchestration Patterns* (Traversaal.ai, 2026-08-10).

Manba asosiy qoidasi: **"Start with a pipeline … Move to a supervisor only when
routing genuinely requires dynamic judgment at runtime. Add swarm bursts only
when a subtask is truly parallelizable."**

Va ikki ogohlantirish, ikkalasi ham bizga tegishli:

1. **"Context fragmentation is the silent killer"** — agentlar markazlashgan
   holatdan emas, o'z suhbat tarixidan o'qisa, qarama-qarshi natija beradi.
   Bizning reengagement loop aynan shundan qochadi: feed **oddiy tool handler**
   orqali o'qiladi, model o'z tarixidan emas.
2. **Swarm'da xarajat portlashi** — har hop'da to'liq kontekst qayta yuborilsa.
   Yechim: **delta kontekst**.

**Xulosa:** bizga supervisor kerak, lekin **swarm emas**. Supervisor —
`boshqaruv agenti` (T2/T3) uchun aynan mos naqsh: rahbar so'raydi → supervisor
qaysi bo'lim agentini chaqirishni hal qiladi. Swarm (parallel agentlar umumiy
holat ustida) zavod uchun jozibali ko'rinadi, lekin kontekst fragmentatsiyasi
xavfi bizning audit talabimiz bilan ziddiyatga tushadi.
### 2.3 Unified Namespace (UNS) — OT va IT ni ulash naqshi

Manba: *Unified Namespace (UNS) Architecture: The Definitive 2026 Guide for
Industrial Data* (Anexee Engineering, 2026-07-20).

Bu **mijozning T4 talabiga to'g'ridan-to'g'ri javob**. UNS — mahsulot emas,
naqsh. To'rt xususiyati:

| Xususiyat | Ma'nosi |
|---|---|
| Bitta haqiqat manbasi | Barcha operatsion data bitta namespace orqali oqadi |
| Ierarxik tuzilma | Korxona → zavod → sex → liniya → katak → uskuna → teg |
| Event-driven pub/sub | Ishlab chiqaruvchi e'lon qiladi, iste'molchi obuna bo'ladi — **polling yo'q** |
| Kontekstli data | Teg PLC manzili emas, **aktiv konteksti** bilan belgilanadi |

Manba aytadigan uchta raqam va bitta ogohlantirish:

- "typical multi-plant enterprise has **30 to 100+ point-to-point
  integrations**" — har yangi iste'molchi N yangi integratsiya qo'shadi.
  UNS buni **N + M** ga tushiradi.
- UNS "**is not** a replacement for SCADA, a physical database, or a fixed
  vendor product".
- Manzillash: `Enterprise/PlantIndia/Assembly/Line3/RoboticCell1/WeldRobot/CurrentkW`.
  "stable across SCADA changes" — ya'ni SCADA o'zgarsa ham manzil qoladi.
- "**start with the asset model, not the tags**" — eng muhim maslahat.

**Bizga xulosa (va nima qilmaymiz):**

Biz MQTT broker yoki SCADA konnektor **qurmaymiz** — bu OT vendorlar ishi va
bizning kuchimiz emas. Lekin biz **aktiv modeli**ni (asset model) o'z shaklimizga
olishimiz kerak, chunki:

1. Business Graph'ning `product` entity'si zavodda **`asset`** bilan bog'lanadi:
   bir detal = bir SKU = bir uskuna pozitsiyasi.
2. UNS'ning ierarxik manzili — bizning `graph.entity` uchun **tayyor id tizimi**.
   Ya'ni `asset` entity'sining `identity`si `path` bo'lishi mumkin:
   `zavod-1/sex-2/liniya-3/stanok-7`.
3. Chiqish yo'li: biz **faqat o'qiymiz**. Zavod o'z UNS'ini qurgan bo'lsa,
   biz unga obuna bo'lamiz (yoki uni mavjud DB/HTTP orqali o'qiymiz). Biz
   zavodning UNS'ini boshqarmaymiz.

Bu muhim chegara: **biz OT infratuzilmasi emas, OT o'quvchisimiz.**

### 2.4 Kamera: retrofit — eng arzon va eng ko'p tarqalgan yo'l

Manba: *Retrofit Architecture for AI Physical Security* (IntelliSee, 2026-05-22)
va *CCTV Analytics for Manufacturing* (Jidoka Technologies, 2026-06-01).

Ikki manba mustaqil ravishda bir xil xulosaga keladi:

- IntelliSee: "**Retrofit, not rip-and-replace, is the dominant deployment
  pattern** for AI physical security in 2026."
- Jidoka: "The average manufacturing facility has cameras installed on **70 to
  80% of its production floor**. Almost all of them are recording footage that
  nobody watches."

Jidoka ochib beradigan **aniq texnik bo'shliq** — va bu bizning kirish nuqtamiz:

> "The infrastructure gap is **not the camera**. It is the **compute layer
> between the camera and a structured data output**."

Ya'ni: kamera **bor**, AI model **bor**, lekin ular orasida "kameradan tuzilgan
ma'lumot chiqaradigan" qatlam **yo'q**. Aynan shu qatlamni biz qurishimiz mumkin.

Jidoka qanday hodisalarni sanaydi (muhim — bu bizning `vision.*` tool
ro'yxatiga aylanadi):

| Hodisa | Manba |
|---|---|
| Sikl boshlanishi / tugashi | stanok kamerasi |
| Asbob olish, moment qo'llash | moment stansiyasi |
| **Qadam tartibi buzilishi** (SOP deviation) | montaj stansiyasi |
| Sikl vaqti (standartga nisbatan) | liniya |
| **Bekor turish** (idle) va davomiyligi | stansiya |
| Nuqson (defect) bayrog'i | sifat nazorati |
| Xavfsizlik buzilishi | umumiy |

Va **ERP'ga nima uzatiladi**: "OEE availability and performance metrics per line
and shift, SOP deviation events with timestamp and station ID, cycle time per
unit, quality alert flags, and safety compliance incidents."

Ikki muhim cheklov (biz ularni yashirmasligimiz kerak):

- **Aniqlik**: Jidoka o'zining vizual tekshiruvda "99.8%" deydi, lekin
  **SOP qadam tekshiruvi** uchun "depends on camera angle and lighting" deb
  qo'shadi va ochiq ogohlantiradi: *"Accuracy figures from vendor marketing
  materials should be verified against your specific product and camera
  configuration."* Biz ham xuddi shunday qilamiz.
- **Maxfiylik**: kamera **odamlarni** ko'radi. Bu shaxsiy ma'lumot va
  ko'p mamlakatda **biometrik** toifaga tushadi (quyidagi 2.6 ga qarang).

**Arxitektura qarori (muhim):** Jidoka "**edge** compute unit … processes each
frame at sub-10ms latency … **without storing or transmitting footage**" deb
ta'riflaydi. Biz aynan shu modelni qabul qilamiz:

> **Video hech qachon platformaga kelmaydi.** Platformaga faqat **hodisa**
> keladi: `{station, event, timestamp, confidence}`. Bu bir vaqtning o'zida
> ham arzon (tarmoqli kengligi), ham huquqiy jihatdan eng xavfsiz tanlov.

Bu ovoz (P5) bilan bir xil qoida: v0.4 da "audio hech qachon planner'ga
yuborilmaydi, faqat transkript" deb belgilangan edi. Kamera uchun
"**frame** hech qachon platformaga kelmaydi, faqat hodisa".
### 2.5 Hujjatlar va hisob-faktura: nazorat zanjiri

Manba: *invoice-to-pay-agent* (ochiq kod loyihasi, 2026).

Bu manba **bizning approval arxitekturasiga deyarli bir xil** oqimni tasvirlaydi:

```
upload -> parse -> normalize -> validate schema and business rules -> duplicate check
-> PO / delivery matching -> exception and fraud controls -> approval routing
-> payment and ERP sync planning -> mock ERP post -> audit log
```

Diqqat qilinadigan joylar:

| Bosqich | Nega muhim |
|---|---|
| **duplicate check** | Bizning `(tenant, connection, lead_id)` dedup naqshining hujjat varianti |
| **PO / delivery matching** (3-way) | Bu **reconcile** naqshining aynan o'zi |
| **exception and fraud controls** | Bizda yo'q — yangi ochilgan talab |
| **approval routing** | Bizning `approval` + `approver_role` |
| **audit log** | Bizning `p_audit` |
| **ERP posting mocked, no live connector** | Ularning ochiq cheklovi — bizda esa haqiqiy 1C/HTTP adapterlar bor |

Va ularning ochiq cheklovi: *"Payment execution is represented as a control
plan; this project does **not move money**."* Biz ham xuddi shu chegarani
qo'yamiz: **platforma pul harakatlantirmaydi**, u faqat hujjatni tayyorlaydi va
tasdiqqa qo'yadi.

**Xulosa:** hujjat oqimi bizning mavjud arxitekturamiz ichida **butunlay**
bajariladi, faqat `document.*` tool oilasi qo'shiladi. `fraud_controls` —
yangi, lekin u ham `conflict`/`report` naqshining davomi.

### 2.6 Huquqiy: O'zbekiston 2026 — bu **arxitekturani belgilaydi**

Manba: Law No. 1125, 2026-03-26 (O'zbekiston "Shaxsiy ma'lumotlar to'g'risida"gi
qonunga o'zgartirish), tahlil: Nurilla Abdushukurov, 2026-03-27.

Bu **eng muhim topilma**, chunki u kod yozishdan oldin qarorni belgilaydi:

| 2019 qonun | 2026 tuzatish |
|---|---|
| **Barcha** shaxsiy ma'lumot O'zbekistondagi serverda saqlanishi shart | Faqat **uch toifa**: **biometrik**, **genetik**, **telekom foydalanuvchi** ma'lumotlari |
| Barcha shaxsiy ma'lumot bazalari davlat reyestriga kiritiladi | Faqat uch toifa bazalari |
| Chetdan saqlash aniq mexanizmsiz | **Uch qonuniy asos**: adekvatlik, shartnomaviy kafolatlar, xalqaro standartlar |

Va muhim **amaliy** ogohlantirish: uchta ikkinchi darajali hujjat hali **yo'q** —
"the cross-border pathways exist in law but **cannot be relied upon in
practice**".

**Xulosa — kodga ta'siri:**

1. **Kamera + yuzni aniqlash = biometrik ma'lumot = majburiy lokal saqlash +
   davlat reyestri.** Ya'ni: agar mijoz zavodga **yuzni taniydigan** kamera
   qo'ysa, biz uni **bulutga chiqara olmaymiz**. Bu bizning "edge'da qoladigan
   hodisa" qarorini huquqiy jihatdan ham **majburiy** qiladi, shunchaki arzon
   tanlov emas.
2. **Shaxsni tanimaydigan** hodisalar (nuqson, sikl vaqti, bekordonlik, SOP
   buzilishi) biometrik emas — ular uchun chegara yumshoqroq.
3. Shuning uchun `vision.*` tool'lari **ikki sinfga** bo'linishi shart:
   `vision.station_event` (shaxssiz, chegara yumshoq) va
   `vision.person_event` (**biometrik**, `human_led` majburiy).
4. Buni **konfiguratsiya bilan** hal qilish kerak: operator
   `personal_data_class` e'lon qiladi va platforma qaysi rejimda ishlashini
   biladi. Bu — paketning **hujjatlashtirilgan majburiyati**, texnik yechim
   emas.
5. Buxgalteriya/hujjat ma'lumotlari uch toifaga **kirmaydi** — demak 2026
   qonuni bo'yicha chetda saqlash mumkin, lekin uchta asosdan biri
   tanlanguncha **ishonib bo'lmaydi**. Shuning uchun data residency'ni operator
   sozlamasi qilib qo'yamiz: `residency: uz | any`.

Bu band **mahsulot emas, shart**. Uni chetlab o'tadigan arxitektura qurish
mijozni huquqiy xavfga soladi.
### 2.7 Governance kernel: UPA

Manba: *A Unified Policy Architecture (UPA): The Governance Kernel for
Enterprise AI Operating Systems* (arXiv 2609.06543, 2026-09-06).

Maqola aytadigan muammo: "existing authorization, security, guardrails, and
compliance mechanisms are **fragmented** and are not designed to govern
autonomous AI **as a unified system**."

Va taklif: bitta siyosat modeli agentlar, **tool'lar**, workflow'lar, **xotira**,
korxona resurslari, **agent-to-agent** muloqot va biznes qoidalarini qamraydi.
U **authorization'dan tashqari** runtime obligations, **human approvals**,
compliance, **audit evidence** va governance evaluation'ni ham o'z ichiga oladi.

**Bizga xulosa:** bizning `policies` + `p_approvals` + `p_audit` + `ladder`
aslida UPA'ning kichik, ishlaydigan qismi. UPA'dan **uchta** narsa olib
kelishimiz kerak, chunki bizda yo'q:

1. **Runtime obligations** — "ruxsat" bilan "bajarish" orasidagi farq.
   Masalan: "yozishing mumkin, lekin **avval** manba tizimdan muvofiqlik
   tekshiruvi o'qilsin". Bizda hozir faqat `approval`.
2. **Agent-to-agent** siyosat — T3 (agentlarni boshqarish) uchun **shart**.
   Supervisor boshqa agentni chaqirsa, ikkinchi agentning vakolati
   **supervisor'ning vakolatidan mustaqil** tekshirilishi kerak. Bu bizning
   `reengagement` dagi "har siklda authority qayta tekshiriladi" qoidasining
   agentlararo kengaytmasi.
3. **Provenance-aware policy** — siyosat manba ishonchiga qarab o'zgaradi.
   Bu **Business Graph'ga to'g'ridan-to'g'ri** tegishli: agar narx `sheets`dan
   kelgan bo'lsa (qo'lda kiritilgan), `moysklad`dan kelgan narxdan **kamroq
   ishonchli**. Ya'ni provenance = siyosat kirishi.

### 2.8 WhatsApp: qoidalar bizning approval naqshimizni tasdiqlaydi

Manba: *WhatsApp Business API for AI Agents* (Bollard AI, 2026-06-23).

Manba bergan aniq raqamlar va qoidalar:

| Qoida | Ma'nosi |
|---|---|
| **24 soatlik oyna** | Mijoz yozsa ochiladi; ichida erkin matn mumkin, har yangi xabar bilan **qayta tiklanadi** |
| Oynadan tashqarida | Faqat **Meta tasdiqlagan shablon** (template) yuborilishi mumkin |
| Oyna yopilgach erkin matn | **Xato 131047** |
| Shablon shartlari | Meta tasdiqlashi kerak; toifa: marketing / utility / authentication; soatiga 100 shablon |
| Limitlar | Tier: 250 → 1 000 → 10 000 → 100 000 → cheksiz |
| O'tkazuvchanlik | Standart **80 xabar/sekund**/raqam; yuqori tierda 1 000 |
| Narx | 2025-07-01 dan **xabar boshiga**; xizmat oynasi ichidagi erkin javob **bepul** |
| On-Premises API | **Sunset** (2025-10-23), faqat Cloud API |
| Ruxsatlar | `whatsapp_business_messaging` (yuborish) va `whatsapp_business_management` (akkaunt) — **ajratilgan** |

**Bizga xulosa — bu juda muhim:**

WhatsApp bizning `approval` naqshimizni **qonuniy majburiyat**ga aylantiradi.
Agent "mijozga yozdim" deb o'ylaydi, lekin oyna yopiq bo'lsa xabar **hech
qachon** yetib bormaydi va xato qaytadi. Ya'ni:

1. `whatsapp.send` **write** risk va **approval** talab qiladi — kanalning
   o'z huquqiy chegarasi bor.
2. Oyna holati **o'qilishi** kerak: `whatsapp.window` (read) — qaysi raqamda
   oyna ochiq va qachon yopiladi. Bu **Business Graph'ning `customer`
   entity'siga** yangi atribut bo'ladi: `wa_window_until`.
3. Shablon **operator konfiguratsiyasi** bo'lishi kerak (nom + til), model
   shablon **yozmasligi** kerak — aks holda model marketing toifasidagi
   shablonni utility deb yuborib, akkaunt sifat reytingini tushirishi mumkin.
4. `whatsapp_business_messaging` va `whatsapp_business_management` **ikki
   alohida token** bo'lishi kerak. Bizning `secret(cfg, 'token_env')` modeli
   bunga mos.

Telegram uchun bunday oyna **yo'q** — bu farqni paketda hujjatlashtirish kerak,
chunki xodim "Telegram'da ishladi, WhatsApp'da nega ishlamadi?" deb so'raydi.

### 2.9 HR va moliya agentlari: bozor katalogi

Manba: Oracle Fusion Agentic Applications for HR e'loni (2026-04-09) — bozor
yo'nalishini ko'rsatuvchi ishonchli signal (sahifaning o'zi 403 qaytardi, shuning
uchun bu yerda faqat **kategoriya** sifatida ishlatiladi, raqam sifatida emas).

Bozor HR agentlarini quyidagi ishlarga bog'laydi: ishga qabul, onboarding,
maosh, yo'qlik/davomat, samaradorlik, ishchi kuchi rejalashtirish. Har biri
**turli ma'lumot**ga tegadi va har xil **tasdiq** talab qiladi.

**Bizga xulosa:** mijozning HR ma'lumoti **Google Sheets'da** (suhbatlar,
davomat) — ya'ni P3 allaqachon bu yo'lda. Lekin maosh va shaxsiy ma'lumot
**eng nozik** toifa: ular uchun `human_led` majburiy va audit **to'liq** bo'lishi
kerak. Bu HR agentini "oddiy chat" deb qarash xatosini oldini oladi.
---

## 3. Yangi talablar ↔ bugungi platforma: gap tahlili

Bu jadval tadqiqotdan keyingi **halol** holat. Har bir qator kodga bog'langan.

| Talab (T) | Kerak | Bugun bor | Baho |
|---|---|---|---|
| T1 Telegram | `telegram.send` | Bor (write, approval) | **Tayyor** |
| T1 WhatsApp | yangi kanal adapteri + 24h oyna | **Yo'q** | **1 blok** |
| T1 CRM/ERP | Bitrix24, Kommo, 1C, custom HTTP | Bor | **Tayyor** |
| T1 DB/API | 8 backend, MCP, custom HTTP | Bor | **Tayyor** |
| T1 **Hujjat** | `document.*`: parse, extract, match | **Yo'q** | **1 blok** |
| T2 Boshqaruv bo'limlari | rol bo'yicha brifing + supervisor | **Yo'q** | **2 blok** |
| T3 Xodimlarni boshqarish | `workforce.*` (davomat, smena, yuklama) | Qisman (Sheets) | **1 blok** |
| T3 **Agentlarni boshqarish** | agent reyestri, sog'liq, xarajat, natija | **Yo'q** | **1 blok** |
| T4 Kamera | `vision.*` hodisa oqimi + `asset` entity | **Yo'q** | **2 blok** |
| T4 Ishlab chiqarish | `asset` + BOM + sikl + OEE | **Yo'q** | **2 blok** |
| — | Kross-tizim o'qish | `business_graph` | **Shu blok** |
| — | Proaktiv yetkazish | `briefing` | TODO |
| — | Dedup + approval naqshi | `reengagement` | **Tayyor** |

**Xulosa:** mavjud naqsh (typed adapter + approval + reconcile + audit +
`business_graph` read model) **o'nta** yangi blokning **to'qqiztasini**
o'zgarmagan holda qabul qiladi. Faqat **bittasi** haqiqatan yangi: kamera/OT
oqimi, chunki u **uzluksiz** va **fizik**.

### 3.1 Uchta narsa haqiqatan qiyin — yashirmayman

| Qiyinlik | Nega | Yechim |
|---|---|---|
| **Kamera = biometrik risk** | Yuzni aniqlash O'zbekiston qonuni bo'yicha majburiy lokal saqlash + reyestr | Faqat **hodisa** platformaga keladi; shaxs hodisalari `human_led`; operator rejimni e'lon qiladi |
| **Agent-to-agent vakolat** | Supervisor boshqa agentni chaqirsa, vakolat "sizib" ketishi mumkin | Har agent **o'z** siyosati bilan tekshiriladi; supervisor vakolati **meros qilinmaydi** (UPA qoidasi) |
| **Ishlab chiqarish detali = haqiqat manbasi** | MoySklad, 1C, MES va qog'oz bir detal uchun har xil raqam beradi | `business_graph` `report` siyosati: ziddiyat **ko'rsatiladi**, tanlanmaydi |

---

## 4. Arxitektura: yetti qatlam

v0.4 da Business Graph bitta qatlam edi. v0.5 uni **yetti qatlamga** yoyadi.
Har bir qatlam **alohida** o'chirilishi mumkin va pastdagi qatlam yuqoridagidan
**mustaqil** ishlaydi.

```
┌─────────────────────────────────────────────────────────────────────────┐
│ L6  INTERFEYS       Telegram · WhatsApp · Web · ovoz · brifing xabari   │
├─────────────────────────────────────────────────────────────────────────┤
│ L5  NAZORAT         xodimlar boshqaruvi · AGENTLAR boshqaruvi · KPI     │
│                     supervisor · eskalyatsiya · xarajat byudjeti       │
├─────────────────────────────────────────────────────────────────────────┤
│ L4  AGENTLAR        sotuv · moliya · HR · PM · sifat · logistika        │
│                     (har biri pack'dagi o'z policy bilan)              │
├─────────────────────────────────────────────────────────────────────────┤
│ L3  BILIM/MIYA      knowledge (BM25+vektor) · memory · qaror jurnali    │
├─────────────────────────────────────────────────────────────────────────┤
│ L2  BUSINESS GRAPH  entity · attribute(value+source+observed) ·         │
│                     conflict · timeline  — hammasi READ-ONLY           │
├─────────────────────────────────────────────────────────────────────────┤
│ L1  KONEKTORLAR     CRM · ERP · DB · Sheets · HTTP · MCP · HUJJAT ·     │
│                     KAMERA HODISASI · OT teg  (typed adapter+approval) │
├─────────────────────────────────────────────────────────────────────────┤
│ L0  CHEGARA         huquq (residency, biometrik) · audit · approval ·   │
│                     reconcile · dedup · kill switch · byudjet          │
└─────────────────────────────────────────────────────────────────────────┘
```

**Eng muhim tuzilma qoidasi:** L0 **pastda** turadi, chunki u hamma narsani
qamraydi. Lekin u **oxirgi** tekshiriladi emas — u **birinchi** tekshiriladi.
Ya'ni: vakolat yo'q bo'lsa, L1'ga **hech qachon** yetib borilmaydi.
### 4.1 L1: kamera hodisasi qanday ulanadi (yangi)

Bu — eng katta yangi arxitektura qarori. Uchta variant bor edi:

| Variant | Tavsif | Qaror |
|---|---|---|
| A. Platforma RTSP oqimni o'zi tortadi | Har kamera uchun dekodlash + model | **RAD ETILDI** |
| B. Platforma VMS'ga ulanadi (ONVIF) | Kamera boshqaruvi bizga o'tadi | **RAD ETILDI** |
| C. Edge hodisa e'lon qiladi, biz o'qiymiz | Kamera/VMS o'z ishini qiladi, biz faqat hodisani olamiz | **QABUL QILINDI** |

Nega A va B rad etildi:

1. **A** — video oqim platformaga kelsa, biz **barcha yuzlarni** saqlagan
   bo'lamiz. Bu O'zbekiston qonuni bo'yicha **biometrik ma'lumot** va majburiy
   lokal saqlash + davlat reyestri. Bundan tashqari tarmoqli kengligi va xarajat
   chiziqli o'smaydi — **eksponensial** o'sadi.
2. **B** — kamera boshqaruvi bizga o'tsa, biz **OT infratuzilma vendori**
   bo'lib qolamiz. Bu bizning kuchimiz emas va mijozni **yagona vendorga**
   bog'laydi — bu esa v0.4 dagi asosiy va'damizga zid: "biz almashtirmaymiz".
3. **C** — biz **mavjud** edge/VMS hisoblash qatlamining iste'molchisi
   bo'lamiz. Mijoz o'z kamerasi va modelini saqlaydi; biz **hodisani**
   tuzilgan ma'lumotga aylantiramiz va boshqa tizimlarga ulaymiz.

**Shakl:** `vision.events` (read) — kamera/VMS'ning webhook yoki DB jadvali
orqali kelgan hodisalar. Har hodisa `business_graph`ning `asset` entity'siga
`station` kaliti bilan bog'lanadi. Ya'ni kamera hodisasi **yangi dunyo emas**,
u shunchaki **yana bir manba**.

```
kamera/VMS (edge AI)  ->  hodisa jadvali/webhook  ->  vision.events (read)
                                                  ->  graph.timeline (asset)
                                                  ->  briefing / eskalatsiya
```

Bu juda muhim: kamera uchun **yangi orkestratsiya naqshi kerak emas**. U
o'zimizning read-only manba + dedup + approval naqshimizga tushadi. Tadqiqotda
topilgan yagona istisno — **uzluksizlik**: hodisa oqimi to'xtovsiz, shuning
uchun `vision.events` **oyna** (window) va **limit** bilan o'qilishi shart.

### 4.2 L2: Business Graph — implementatsiya qilingan (shu blok)

`platform_runtime/business_graph.py`. Olti tool, **hammasi read**:

| Tool | Vazifa |
|---|---|
| `graph.entities` | E'lon qilingan entity/atribut/manba shakli (connection, range, ustun **qaytarilmaydi**) |
| `graph.entity` | Bitta entity: har atribut `values` + `selected` + `conflict` |
| `graph.search` | Atribut qiymati bo'yicha id qidirish (bounded) |
| `graph.timeline` | Bir entity uchun kross-tizim faoliyati (manba tartibida) |
| `graph.conflicts` | Ikki tizim kelishmaydigan joylar ro'yxati |
| `graph.explain` | Bitta atribut ortidagi **hamma** kuzatuv va nima uchun tanlangan/tanlanmagan |

**Manba = mavjud read tool.** `connectors.read`, `sheets.rows`, `sheets.read`,
`database.read`. Boshqa hech narsa manba bo'la olmaydi — `telegram.send` yoki
`database.write` **rad etiladi**, aks holda read model yashirin yozuv yo'liga
aylanadi.

**Vakolat meros qilinmaydi.** Graph tool'i chaqirilganda har bir manba uchun:
1. manba tool'i agentda **bor**mi,
2. `connectors.read`/`database.read` bo'lsa connection `allowed_connections`
   da **bormi**,

— tekshiriladi, **har qanday provider I/O dan oldin**. Ya'ni ruxsat yo'q bo'lsa,
**hech bir manba o'qilmaydi**: qisman o'qish (bir manba ishlab, boshqasi ruxsat
bermay) **mumkin emas**.

**Ziddiyat hech qachon jimgina hal qilinmaydi.**

| `conflict_policy` | Xatti-harakat |
|---|---|
| `report` (default) | Barcha kuzatuvlar qaytariladi, **hech biri tanlanmaydi** (`selected: null`) |
| `primary_wins` | Operator e'lon qilgan tartib bo'yicha bittasi tanlanadi, **lekin ziddiyat baribir qaytariladi** |
| `newest_wins` | **RAD ETILADI** — `observed` bu **bizning o'qish vaqtimiz**, provider'ning yangilanish vaqti emas. Uni "eng yangi" deb ko'rsatish yolg'on bo'lardi |

**Manba xatosi "ma'lumot yo'q" degani emas.** Xato qilgan manba
`source_errors`da **nomi bilan** qayd etiladi va `complete: false` bo'ladi.
Xato **matni** hech qachon qaytarilmaydi (u range, spreadsheet id yoki ichki URL
echo qilishi mumkin) — faqat **xato sinfi nomi**. Va **vakolat xatosi
(`Forbidden`) qayta ko'tariladi**, chunki u provayder nosozligi emas, siyosat
javobi.

**Sonli matn ziddiyat emas.** DB `450000` beradi, jadval `'450000'` beradi —
bu narx ziddiyati **emas**. Lekin `'Ali Valiyev'` va `'ali valiyev'` bir xil,
`'Ali'` va `'Vali'` — ziddiyat.

### 4.3 L5: agentlarni boshqarish (T3) — yangi, lekin kichik

Bu qism **platformaning o'z agentlarini** boshqaradi. Uch savolga javob beradi:

| Savol | Tool | Manba |
|---|---|---|
| Agent nima qildi? | `agent.activity` | `p_audit`, `p_agent_runs` |
| Agent qancha sarfladi? | `agent.cost` | `usage_budget` |
| Agent sog'lommi? | `agent.health` | oxirgi run holati, xato darajasi, `drift` |

**Muhim chegara:** bu tool'lar **read** va **owner/operator** uchun. Agent
o'zini yoki boshqa agentni **o'zgartira olmaydi** — "o'z-o'zini boshqarish"
naqshi bu yerda ataylab rad etilgan, chunki u audit zanjirini buzadi: kim
o'zgargan bo'lsa, u o'zgarishni yozgan bo'lardi.

**Supervisor qoidasi (UPA'dan):** supervisor boshqa agentni chaqirsa, ikkinchi
agent **o'z** `policy`si bilan ishlaydi. Supervisor vakolati **meros
qilinmaydi**. Bu `preflight` naqshining aynan kengaytmasi va u allaqachon
`agent_loop` + `dispatch_allowed` orqali majburlanadi.
---

## 5. Yo'l xaritasi: P8 .. P14

v0.4 ning P1–P7 fazalari **saqlanadi** (`business_graph` = P1 hozir bajarildi).
Quyida faqat **yangi** fazalar.

| Faza | Blok | Qiymat | Nega bu tartibda |
|---|---|---|---|
| **P8** | `whatsapp_channel` | Sotuv mijoz bilan WhatsApp'da, 24h oyna hurmat qilinadi | Eng ko'p so'ralgan kanal; mavjud approval naqshiga **to'liq** tushadi |
| **P8b** | `document_intake` | Hisob-faktura/shartnoma → tuzilgan ma'lumot | AP oqimi; `reconcile` naqshi tayyor |
| **P9** | `agent_oversight` | Agentlar sog'ligi, xarajati, natijasi (L5) | T3 ning birinchi yarmi; kam xarajat, katta ishonch |
| **P9b** | `workforce_oversight` | Davomat, smena, yuklama, muddati o'tgan ish | T3 ning ikkinchi yarmi; HR Sheets'da allaqachon bor |
| **P10** | `briefing` | Rol bo'yicha ertalabki dayjest | T2 ning kirish nuqtasi; `reengagement` bilan bir xil koordinator |
| **P10b** | `supervisor_router` | Rahbar savoli → to'g'ri bo'lim agenti | T2 ning yuragi; UPA agent-to-agent qoidasi |
| **P11** | `asset_model` | `asset` entity: zavod → uskuna → detal | T4 poydevori; UNS "asset model first" qoidasi |
| **P11b** | `vision_events` | Kamera hodisasi oqimi (`vision.events`) | T4 ning ko'zi; faqat hodisa, hech qachon frame |
| **P12** | `manufacturing_bom` | Mahsulot tarkibi, sikl, chiqim | T4 ning miyasi; `asset` modeliga tayanadi |
| **P13** | `oee_and_andon` | OEE, bekordinlik, SOP buzilishi → eskalyatsiya | T4 ning natijasi; Jidoka ro'yxatidagi hodisalar |
| **P14** | `telephony_outbound` | Chiquvchi qo'ng'iroq | Eng og'ir, eng kech |

**Nega P8 va P9 P10'dan oldin:** brifing (P10) **manbasiz** bo'sh bo'ladi.
Avval o'qish (graph) va kanal (WhatsApp), keyin yetkazish (briefing). Bu
v0.4 dagi "Sheets avval, chunki Graph'ga manba bo'ladi" mantiqining davomi.

**Nega P11 (asset) P11b (vision) dan oldin:** Jidoka ham, Anexee ham bir xil
aytadi — **"start with the asset model, not the tags"**. Agar biz avval
kamera hodisasini olsak, uni **nimaga** bog'lashni bilmaymiz va hodisa
egasiz qoladi.

---

## 6. Qabul mezonlari (o'lchanadigan)

| Metrika | Maqsad | Nega bu |
|---|---|---|
| Kross-tizim so'rovda **manba ko'rsatilishi** | 100% | Ziddiyat yashirilmasin (v0.4 talabi, `business_graph` bajaradi) |
| Ziddiyat **jimgina tanlanmasligi** | 100% (`report`da `selected: null`) | Ishonch: buxgalter 1C, sotuvchi MoySklad raqamini bilishi kerak |
| Ruxsatsiz manba **o'qilmasligi** | 100% (preflight) | Qisman o'qish "ruxsat bor" degan yolg'on taassurot bermasin |
| Manba xatosi **"ma'lumot yo'q"** deb ko'rinmasligi | 100% (`source_errors` + `complete`) | Bo'sh natija bilan o'qilmagan natija bir xil ko'rinmasin |
| Brifing yetkazilishi (09:00) | ≥ 99% | "so'ramasa ham" talabi |
| WhatsApp xabari **oyna** holatiga mos bo'lishi | 100% (131047 bo'lmasin) | Oyna tashqarisida erkin matn **hech qachon** yetib bormaydi |
| Kamera **frame** platformaga tushmasligi | 0 frame | Biometrik huquq + tarmoqli kengligi |
| Shaxs hodisasi uchun `human_led` | 100% | Biometrik toifa |
| Agent boshqa agent vakolatini **meros qilmasligi** | 100% | UPA agent-to-agent qoidasi |
| Muddati o'tgan ish eskalatsiyasi | ≥ 95% | P6 talabi |

---

## 7. Ochiq risklar (yashirilmaydi)

1. **Biometrik huquq** — yuzni aniqlash O'zbekistonda mandatli lokal saqlash +
   davlat reyestri. Agar mijoz buni xohlasa va biz buni bulutda qilsak, mijoz
   qonunni buzadi. Platforma **texnik imkoniyat** beradi; **huquqiy asos**
   operator zimmasida.
2. **Uchta ikkinchi darajali hujjat hali yo'q** — chetga uzatish yo'llari
   qonunda bor, amalda **ishonib bo'lmaydi**. Shuning uchun default
   `residency: uz`.
3. **Kamera aniqligi** — Jidoka o'zi ogohlantiradi: SOP qadam tekshiruvi
   "camera angle and lighting" ga bog'liq. Biz ham brend raqamlarini
   takrorlamaymiz.
4. **OT uzluksizligi** — hodisa oqimi to'xtovsiz. `vision.events` **oyna**
   bilan o'qilmasa, bir kunda o'n millionlab hodisa plannerga oqib keladi.
5. **Agent-to-agent cheksiz zanjir** — supervisor → agent → agent → … Bu
   `max_steps` bilan to'silishi kerak, aks holda xarajat portlaydi
   (swarm'dagi "context rehydration" muammosi).
6. **Raqobat** — Odoo/Bitrix24 o'z ekotizimini kengaytirmoqda. Farqimiz:
   **o'zbek tili + o'rgatish talab qilmaslik + mavjud tizimlarga tegilmaslik**
   + **kross-tizim ziddiyatni ochiq ko'rsatish**.

---

## 8. Bu PRD nima qilmaydi (scope chegarasi)

- **MQTT broker, SCADA konnektor, ONVIF kamera boshqaruvi qurmaydi.** Biz OT
  o'quvchisimiz, OT infratuzilma vendori emas.
- **Video oqimni qabul qilmaydi va saqlamaydi.** Faqat hodisa.
- **Pul harakatlantirmaydi.** Hujjatni tayyorlaydi, to'lovni tasdiqqa qo'yadi.
- **Yuzni taniydigan model o'qitmaydi.** Mijozning mavjud modeli hodisa beradi.
- **Xodimni baholamaydi yoki jazolash qarori qabul qilmaydi.** U faktlarni
  ko'rsatadi va menejerga eskalatsiya qiladi.
- **Agentni o'zi o'zgartirmaydi.** Agentlar boshqaruvi **read**.
- **Mijozni migratsiya qilmaydi, ERP moduli yozmaydi.**


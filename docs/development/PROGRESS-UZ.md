# Avtonom development reyestri

Joriy source **v0.5**. Ish davom etmoqda, **IN_PROGRESS**, production **NO_GO**. To‘liq PRD
100% tugamagan.

**Holat bloki (2026-09-22 chuqur ko‘rib chiqishi):**

- API bu mashinada **hech qachon ishga tushirilmagan** — `api-python/.env` yo‘q,
  `config/integrations.json` yo‘q, `api-python/data/app.db` da faqat migratsiya qatori.
  Shuning uchun **hech bir** integratsiya `live_verified` emas; eng yuqorisi
  `LOCAL_CONTRACT_TESTED`.
- `runtime_tests`: **3 239 sinov**, Windows’da `failures=1, errors=12` — **13 tasi
  Windows-only** (`os.O_NOFOLLOW`, `mkfifo`, `fcntl`, symlink privilegiyasi, POSIX fayl
  rejimlari). `integration_tests`: **95/95 PASS**
  (`ENV=test ALLOW_INSECURE_DEV=true PIPELINE_MODE=platform IDENTITY_DIRECTORY=false`).
- `api-python/tests/` (legacy): **nafaqaga chiqarildi (2026-09-22)**. U 33 qizil / 172 pass
  edi va hech bir gate uni yurgizmasdi; tirik kodni sinaydigan 5 fayl
  `integration_tests/` ga ko‘chirildi (`test_packs_contract`, `test_pack_persona`,
  `test_lang`, `test_config`, `test_security_helpers`), qolgani o‘chirildi — sabablar
  `api-python/integration_tests/LEGACY-RETIRED.md` da.
- CI: `http` job endi **yig‘iladi** (`integration_tests/conftest.py`);
  `ui_dependency_security` **hamon FAIL** (`next@14.2.35`: 1 critical + 1 high; Next 15 +
  React 19 kerak).
- Yetib borish: registry’dagi **89** tool’dan **12 tasi** yetkazilgan pack’lardan
  chaqiriladi; `platform_runtime` kodining **81%** yetib bo‘lmaydi. 17 modul
  (`erp`, `documents`, `inventory`, `business_graph`, `whatsapp`, `whatsapp_inbound`,
  `telephony`, `assets`, `vision`, `manufacturing`, `oee`, `workforce`, `supervisor`,
  `reengagement`, `escalation`, `briefing`, `oversight`) — **muzlatilgan preview**,
  mahsulot funksiyasi emas.
- First-run: eski `setup_local.py` → `owner_login.py` yo‘li **o‘lik** (410 + 403, UI’da
  token maydoni yo‘q). Yagona ishlaydigan yo‘l — `scripts/provision_identity.py`; qarang
  `docs/ONBOARDING-UZ.md`.

Quyidagi sessiya yozuvlari xronologik, eng yangisi tepada emas — har biri o‘z sanasidagi
o‘lchovni saqlaydi.

## Mahsulotlashtirish sessiyasi: UI qatlami, chegara auditi §151–§152, DR

Bu sessiya **kod emas, yetkazib berish** bo‘shlig‘ini yopishga qaratildi. Har bir da’vo
o‘lchov bilan; o‘lchanmagan narsa «o‘lchanmagan» deb yozilgan.

### 1. UI qatlami — 27 route endi interfeysga ega

`ui_full_product: PARTIAL` ning sababi shu edi: backend route bor, UI yo‘q. Endi:

| Panel | Route’lar | Fayl |
|---|---|---|
| Qayta aloqa | `GET/PUT /reengagement`, `/ledger`, `/sync` | `OperationsPanels.tsx` |
| Brifing | `GET/PUT /briefing`, `/ledger` | shu |
| Eskalatsiya | `GET/PUT /escalation`, `/ledger`, `/disable` | shu |
| Supervisor | `GET/PUT /supervisor`, `/route`, `/history` | shu |
| Takrorlanuvchi jadval | `POST /schedules` | shu |
| Holat/metrika | `GET /health` + mavjud ro‘yxat route’lari | shu |
| Mijoz sub-resurslari | `/contacts`, `/channel-identities`, `/orders` | shu |
| Hisob/a’zolik | `logout-all`, `sessions`, `workspaces`, `select`, `invitations`, `accept`, `revoke` | `AdminPanel.tsx` |
| Bootstrap | `POST /identity/bootstrap` | shu |

**Eng muhim tuzatish:** `page.tsx` noaniq qadam uchun «reconcile API orqali dalil bilan
yakunlaydi» deb **buyurar** edi, lekin tugma yo‘q edi — ya’ni yagona hujjatlashtirilgan
tiklash yo‘li hujjatlashtirilgan, qurilmagan edi. `ReconcileControl` shu bo‘shliqni
yopadi: owner-only, dalil majburiy, natija audit jurnaliga tushadi.

**Supervisor `/route` uchun `Idempotency-Key` majburiy edi va uni yuborish yo‘li yo‘q
edi.** `SessionClient` ga allowlist asosidagi header qo‘shildi: nom `[A-Za-z0-9-]{1,64}`,
qiymat 256 belgidan qisqa, `Authorization`/`Content-Type`/`Host`/`Cookie`/`Origin`/
`Referer` **qayta yozilmaydi**. 15 ta node testi (4 tasi yangi).

**UI gate’lari endi RUN, oldin NOT_RUN edi:**

| Gate | Natija |
|---|---|
| `tsc --noEmit` | **PASS**, 0 xato |
| `next build` | **PASS**, 7 sahifa |
| `npm audit --audit-level=high` | **FAIL** — pastga qarang |

**Ochiq risk (yangi topilgan):** `next@14.2.5` da **1 critical + 1 high**. `14.2.35` ga
ko‘tarildi (11 advisory yopiladi, patch darajasi, typecheck+build qayta tasdiqlandi),
lekin qolgan advisory’lar **`>=15.5.24`** talab qiladi. Ya’ni bu gate’ni **14.x
liniyasida yopib bo‘lmaydi** — Next 15 + React 19 ga rejalashtirilgan major upgrade
kerak. `ui_dependency_security` CI job shu sababdan **qizil**.

### 2. Chegara auditi §151 — tashqi provayder transporti

`google_oauth.py`, `model_transport.py`, `model_response.py`, `mcp.py`. 24 mutatsiya.

**Natija: 23 RED, 1 RECORDED, 0 o‘lchanmagan.** Matritsa **bitta haqiqiy nuqson**
topdi va u o‘qishdan ko‘rinmasdi:

> `client id suffix` mutatsiyasi **YASHIL** chiqdi. Ya’ni «client id Google client id
> bo‘lishi shart» degan qoidani **butunlay o‘chirib tashlash mumkin edi** va to‘plam
> jim qolardi. Sabab: mavjud chegara testi faqat **to‘g‘ri** client id berardi — u
> uzunlikni qadadi, **shaklni** emas. `test_client_id_must_be_a_google_client_id`
> qo‘shildi; qayta yurishda **RED**.

`MAX_PORT = 65535` (model_transport) **RECORDED**: `urlsplit` 65535 dan katta portni
taqqoslashdan **oldin** rad etadi, shuning uchun konstanta kengaytirilsa ham natija
o‘zgarmaydi va hech bir test buni ajrata olmaydi. Bu — chegara emas, **o‘lchanish**
masalasi.

### 3. Chegara auditi §152 — planner, speech, baza o‘qish

`agent_planner.py`, `speech.py`, `postgres_connector.py`, `crm/crm_reconcile.py`.
28 mutatsiya.

**Natija: 28 RED, 0 RECORDED, 0 o‘lchanmagan.** Loyihada birinchi marta **bitta ham
yashil yo‘q**.

### 4. `revert_matrix` — asbob tuzatildi (muhim)

§152 da kontrol **yashil emas**: `test_foundation_v02` ichida Windows bajarolmaydigan
POSIX mount testi bor (11 ma’lum error’dan biri). «Kontrol yashil bo‘lishi shart» qoidasi
shu sababdan **umuman o‘lchashdan bosh tortdi**.

Asbob endi **imzo (signature)** bilan ishlaydi: kontrol **yozilgan bazani aynan
takrorlashi**, har bir mutatsiya esa **uni o‘zgartirishi** kerak. Bu yashildan
**qat’iyroq**: oldindan mavjud xatoni **yo‘q qilgan** mutatsiya ham ushlanadi.
`AUTO_BASELINE` — imzo platformada o‘lchanadi, shuning uchun skript Linux’da ham,
Windows’da ham to‘g‘ri.

### 5. `retry.py` — 429/5xx uchun chegaralangan qayta urinish (yangi)

Inventar buni «umuman yo‘q» deb yozgan edi. To‘rt qaror:

- **O‘qish takrorlanadi, yozuv takrorlanmaydi.** `run` `idempotent=True` bo‘lmasa
  **bir marta** chaqiradi. Sabab: takroriy POST — bu **ikkinchi ta’sir**, va platforma
  buni allaqachon `uncertain` deb qaraydi.
- **Faqat 429 va 5xx.** 400/401/403/404 abadiy bir xil javob beradi; 401 ni qayta
  urinish operator ko‘rishi kerak bo‘lgan xatoni **yashiradi**.
- **`Retry-After` hurmat qilinadi, lekin cheklanadi** (RFC 9110 ning ikkala shakli).
- **Devor-soat shifti** — provayder «bir soatdan keyin» desa, worker bir soat
  uxlamaydi.

17 test, barcha chegara o‘z **raqami** bilan qadalgan.

### 6. Reliz va infratuzilma gigiyenasi

| Ish | Oldin | Endi |
|---|---|---|
| `MANIFEST.sha256` | FAIL — 445 tekshirildi, 51 hash mos emas, 162 ro‘yxatda yo‘q | **PASS — 483 fayl, 0 xato** |
| Generator | repo ichida **yo‘q** | `scripts/generate_manifest.py` (`--check` bilan) |
| `.gitignore` hisobi | `verify_manifest` uni o‘qimasdi → dev daraxtida hech qachon yashil emas | o‘qiladi (git top-level qo‘riqchisi bilan) |
| `test_symlink_denied` | **FAIL** (baseline `failures=1`) | tuzatildi: `symlink_to` muvaffaqiyatini **tekshiradi** |
| `api-python/tmp*` | 4 ta chala papka | o‘chirildi |
| DR mashqi | **yo‘q** | `scripts/dr_drill.py` + `DR-RUNBOOK-UZ.md` |

**DR drill o‘lchovi:** 42 jadval, 16 satr, **0.295 s**; 7 qadam ham `ok`, shu jumladan
**tenant izolyatsiyasi tiklangan nusxada**. Qamrab olinmaganlari hujjatda: shifrlash,
offsite, retention, PITR, tarmoq bazalari, HA.

---

## v0.5 blok: Business Graph (P1)

Real mijozning **ikkinchi** feedbackidan chiqqan yo‘nalish. Chuqur tadqiqot va
yangi yo‘l xaritasi: `docs/prd-v05/00-ENTERPRISE-OPERATING-LAYER-PRD-UZ.md`.
Implementatsiya hisoboti: `V05-BLOCKS-CORE-UZ.md`.

Mijozning yangi talablari **to‘rtta**: (T1) WhatsApp + hujjat integratsiyasi,
(T2) **boshqaruv bo‘limlari** uchun agentlar, (T3) xodimlarni **va agentlarni**
boshqarish, (T4) **kamera** ma’lumotlari va zavodning **har bir detaligacha**
qamrab olinishi.

- `platform_runtime/business_graph.py` — operator e’lon qilgan **kross-tizim
  read model**. Olti tool, hammasi **read**: `graph.entities`, `graph.entity`,
  `graph.search`, `graph.timeline`, `graph.conflicts`, `graph.explain`.
- **Manba — mavjud read tool** (`connectors.read`, `sheets.rows`, `sheets.read`,
  `database.read`) va u **oddiy handler** orqali chaqiriladi. Ya’ni graph
  **o‘z vakolatini bermaydi**; `telegram.send` yoki `database.write` manba
  **bo‘la olmaydi**.
- **Preflight**: har bir manba vakolati **har qanday provider I/O dan oldin**
  tekshiriladi. Ruxsat yo‘q bo‘lsa **hech bir manba o‘qilmaydi** — qisman o‘qish
  imkonsiz, shuning uchun “nosozlik” va “vakolat xatosi” aralashmaydi.
- **Har atribut `source` + `observed` bilan.** Ziddiyat **jimgina hal
  qilinmaydi**: `report` (default) hech narsa **tanlamaydi**; `primary_wins`
  tanlaydi va **baribir** xabar beradi; `newest_wins` **rad etiladi**, chunki
  `observed` — bizning o‘qish vaqtimiz, provider yangilanish vaqti emas.
- **Manba xatosi “ma’lumot yo‘q” degani emas**: `source_errors` + `complete:
  false`; faqat **xato sinfi nomi**, provider matni emas. **Vakolat xatosi**
  esa qayta ko‘tariladi.
- Sonli matn (`'450000'`) va son (`450000`) **ziddiyat emas** — `canonical()`.

**Tadqiqotning asosiy qarorlari (rad etilganlar ham):**

| Qaror | Sabab |
|---|---|
| Platforma RTSP oqimni **tortmaydi** | Biometrik video + eksponensial xarajat |
| Platforma kameralarni ONVIF bilan **boshqarmaydi** | OT infratuzilma vendori bo‘lib qolardik, mijoz bir vendorga bog‘lanardi |
| **Edge hodisa e’lon qiladi, biz o‘qiymiz** | Kamera/VMS o‘z ishini qiladi; **frame hech qachon platformaga kelmaydi** |
| Law No. 1125 (2026-03-26) | Yuzni aniqlash = **biometrik** ma’lumot = majburiy lokal saqlash + davlat reyestri → default `residency: uz` |
| WhatsApp **24 soatlik oyna** | Oyna tashqarisida erkin matn **hech qachon** yetib bormaydi (xato 131047) → approval + `whatsapp.window` read |
| **Supervisor vakolati meros qilinmaydi** (UPA) | Chaqirilgan agent **o‘z** policy’si bilan tekshiriladi; zanjirga `max_steps` kerak |
| Platforma **pul harakatlantirmaydi** | Hujjat oqimi nazorat rejasini tayyorlaydi, to‘lovni tasdiqqa qo‘yadi |

**Yangi yo‘l xaritasi:** P8 WhatsApp → P8b hujjat → P9 agentlar nazorati → P9b
xodimlar nazorati → P10 brifing → P10b supervisor → P11 asset modeli → P11b
kamera hodisasi → P12 BOM → P13 OEE/andon → P14 chiquvchi telefoniya.
Tartib sababi: **brifing manbasiz bo‘sh bo‘ladi**, shuning uchun avval o‘qish va
kanal. Va **“tags emas, asset model”** — shuning uchun P11 P11b dan oldin.

**38 yangi test.** `runtime_tests` 1051 → **1089 test**; error/failure soni
o‘zgarmadi.

**Audit topilmasi (N+1 amplifikatsiya):** `graph.search` va `graph.conflicts`
har bir id uchun `resolve()` chaqirardi, `resolve()` esa **har safar**
`preflight()` + manbani **qaytadan to‘liq o‘qiydi**. Ya’ni 8 id uchun
`graph.conflicts` — **9 provider GET**. O‘lchandi:
`scripts/probes/probe_graph_amplification.py`. Tuzatildi: yangi `_collect_all()` har
manbani **aynan bir marta** o‘qib, kuzatuvlarni id bo‘yicha savatlarga ajratadi;
`search`/`conflicts`/`identifiers` endi shu savatdan foydalanadi. Natija:
**9 GET → 1 GET**, tartib va javob shakli o‘zgarmagan. 6 yangi regressiya testi
buni qulflaydi (`test_business_graph.py` 38 → **44**).


## v0.5 blok: WhatsApp oynasini kiruvchi hodisalardan o‘qish (P8d)

`platform_runtime/whatsapp.py` ga `_window_from_events` va `window_sources`
qo‘shildi. Auditning 1-topilmasi — `whatsapp_inbound` yozgan `window_until` ni
**hech narsa o‘qimasligi** — shu bilan yopildi.

**Pretsedent qoidasi:** tasdiqlangan kiruvchi hodisa operator jadvalidan
**ustun** — «yangiroq bo‘lgani» emas, balki **hodisa**. Sabab: qo‘lda
tahrirlangan katak Meta o‘zi bergan timestamp yopgan oynani **qayta
ochmasligi** kerak, aks holda platforma **o‘zi tekshirmagan** qiymatdan o‘ziga
ruxsat beradi. `window()` chiqishiga `source` (`event`/`register`) maydoni
qo‘shildi.

**Blokning eng nozik nuqsoni — va uni testlar emas, probe tutdi.**
`whatsapp_inbound` `window_until` ni **tugash vaqti** deb yozadi (Meta timestamp
+ 24 soat), `window_state` esa **oxirgi kiruvchi vaqt** ni olib 24 soatni
**o‘zi** qo‘shadi. To‘g‘ridan-to‘g‘ri ulash 24 soatni **ikki marta** qo‘shardi:
**25 soat oldin** yozgan mijoz oyna **ochiq** deb o‘qilardi — bu aynan modul
oldini olish uchun qurilgan **131047** holati. 119 + 70 test yashil edi, chunki
har biri **o‘z** tasviriga nisbatan izchil edi; ularni bog‘laydigan
**konversiya** hech qayerda tekshirilmagan edi. Tuzatish:
`float(stamp) - WINDOW_SECONDS` + regressiya testi.

**Ataylab qilinmagan ish:** `whatsapp` `engine.OUTBOUND_CHANNELS` ga
**qo‘shilmadi**. (a) oyna va manzil **ikki xil savol**; (b) qo‘shish
`whatsapp.send` ni allowlist shoxobchasidan hodisa-bog‘lash shoxobchasiga
**ko‘chiradi** — bu ruxsat **qo‘shish** emas, **qoida almashtirish**;
(c) oynadan tashqarida qonuniy yuboriladigan **shablonlar** bog‘lanadigan
hodisasiz qolardi. Hisobot: `V05-BLOCKS-MESSAGING-UZ.md`.

## v0.5 blok: `wa_window_until` grafiya atributi — tekshirildi va rad etildi (P8L)

PRD v0.5 §2.8 oyna holatini Business Graph’ning `customer` entity’siga
`wa_window_until` atributi sifatida qo‘shishni taklif qiladi. Blok **qurilmadi**;
**rad etildi**, va sabab **mexanik**:

- grafda **atribut — provider qatorining ustuni**. `_source` `map` ni shunday
  o‘qiydi: `clean_map[attribute] = _text(field, ...)`, va `_collect` uni
  `row.get(field)` bilan oladi. Ya’ni atribut **hisoblanmaydi** — **ko‘chiriladi**;
- demak `wa_window_until` ni deklaratsiya qilish «oyna **inson qo‘lda yozadigan**
  katakda yashaydi» degani bo‘lardi, va katak **mijoz yozganda o‘zi
  yangilanmaydi**. Bu **P8c nuqsonining aynan o‘zi**, bir qatlam pastda;
- `observed` dan hisoblash ham rad etildi: `observed` — **bizning o‘qish
  vaqtimiz**, provider’ning yangilanish vaqti emas. Qoralama va yuborish
  **ikki xil surat** o‘qib, bitta mijoz haqida **kelishib qolmasdi**;
- `whatsapp.window` ni graf manbasi qilish ham rad etildi: `SAFE_SOURCE_TOOLS`
  to‘rtta **qator qaytaruvchi** tool bilan cheklangan; `whatsapp.window`
  `entity_id` dan emas, **tenant konfiguratsiyasidan** javob beradi; chiqishi
  qator ro‘yxati emas; va bitta savol uchun **uchta ruxsat** talab qilardi.

**PRD ning maqsadi bajarilgan:** `whatsapp.window` `open`, `closes_at` va
**`source`** qaytaradi — va bu **o‘sha** resolver, `send` darvozasi provayder
I/O sidan **oldin** chaqiradigani. Ya’ni **talab emas, usul** rad etildi.

Atribut o‘rniga **qo‘riqchi testlar** (`WaLifecycleGuardTests`, 6 metod → 52
to‘plam testi) va
`scripts/probes/probe_wa_window_graph_attribute.py` (`PROVEN`) yozildi — shunda bu xato
kelajakda **jimgina** qaytolmaydi. **Yangi tool yo‘q**
(`build_registry()=69`). Hisobot: `V05-BLOCKS-MESSAGING-UZ.md`.

## v0.5 blok: ERP posting — moliyaviy tizimga birinchi yozish (P8e)

`document.posting_plan` **taklif**da tugardi. Bu blok — undan keyingi qadam, va u
repozitoriydagi **eng xavfli kod**, chunki CRM izohidan farqli o‘laroq posting —
**boshqa tizim harakat qiladigan moliyaviy yozuv**. Uch qoida, va har biri
**majburlangan**:

1. **Pul harakatlanmaydi.** Posting ERP’da **buxgalteriya hujjati — qarz**
   yaratadi; to‘lash **inson ishi** bo‘lib qoladi. Bu `documents` blokining
   da’vosidan **torroq**, shuning uchun **alohida** o‘lchanadi: modulda
   `pay`/`transfer`/`settle`/`remit`/`refund` nomli **birorta** ommaviy funksiya
   yo‘q (strukturaviy), va `posting_body()` — yuborilishi mumkin bo‘lgan **yagona
   to‘plam**.
2. **To‘liq bo‘lmagan hujjat — rad etiladi.** Yetti maydon talab qilinadi;
   beshtasi hujjatdan, **ikkitasi** (`account`, `counterparty`) operator
   registridan **alias** sifatida. Ular hujjatdan **hech qachon** o‘qilmaydi:
   o‘z hisob raqamini o‘zi aytadigan yetkazib beruvchi — o‘z qarzini qayerga
   qo‘yishni **o‘zi tanlayotgan** yetkazib beruvchi. **Yetishmayotgan** va
   **buzuq** ajratilgan: biri `missing` ro‘yxatiga qo‘shiladi, ikkinchisi darhol
   o‘z sababi bilan ko‘tariladi.
3. **Ko‘pi bilan bir marta yoziladi**, **to‘rt qavat**: ERP qidiruvi
   (`find_posted` — **fail-closed**: so‘ray olmaslik "yo‘q" degani emas), I/O dan
   **oldin** tekshiriladigan lokal ledger, poygani yutadigan `UNIQUE` indeks, va
   **muvaffaqiyatsiz urinish qaytariladigan** qoladi (faqat `posted` /
   `skipped_existing` indeksni egallaydi). 2xx lekin identifikatorsiz javob —
   `unconfirmed`, `posted` emas.

**Ikkala driver** mashq qilindi: `custom_http` va `onec_http` **faqat javobni
qanday o‘qishda** farq qiladi (flat `id` vs `result.Ref_Key`), va bu farq
**o‘lchandi** — driver hech narsani o‘zgartirmasa, custom ERP **o‘zida bor**
hujjatni ko‘rmay qolardi, ya’ni ikki marta yozish sharti.

`erp.posting_submit` **ataylab** `engine.OUTBOUND_TOOLS` da emas: u gate
«agent bu qabul qiluvchiga murojaat qila oladimi» deb so‘raydi, postingning esa
**qabul qiluvchisi yo‘q** — qo‘riqchisi hujjat identifikatsiyasi.

54 test (`test_erp.py`), `config/erp_posting.example.json` +
`scripts/check_erp_example.py`, `scripts/probes/probe_erp_posting_boundary.py`
(`PROVEN`, 5 bo‘lim). Registr **69 → 72**. Hisobot:
`V05-BLOCKS-OPS-UZ.md`.

## v0.5 blok: Optimallashtirish — dvigatel narxi, shartnoma va hujjat hajmi

Yangi blok qurilmadi. Mavjud 2800 testli to'plam, 12 670 satrli hujjat va 394 satrli
skill **o'lchov bilan** qisqartirildi. Uchta haqiqiy nomuvofiqlik topildi.

**1. Ro'yxatga olish shartnomasi.** 17 ta `register_*` dan **beshtasi**
(`business_graph`, `google_adapters`, `oversight`, `sheets`, `workforce`) mavjud
tool'ni o'tkazib yubormasdi, ya'ni qayta ro'yxatga olish `Invalid tool registration`
berardi — sababni aytmaydigan xabar. **Va ikkita test aynan shu ko'tarilishni
qadalgan edi**, ya'ni chetlanish "ataylab" ko'rinardi. Endi bitta
`tools.register_once()` — shartnomaning yagona bayoni — va **13 nusxa test bitta
kuchliroq testga** birlashtirildi (`test_registration_contract.py`), u butun runtime
bo'ylab har bir `register_*` ni aylanadi.

**2. Dvigatel narxi.** `cProfile`: `Engine.__init__` — `test_supervisor.py` ning
**38%** i, shundan `executescript` 10.1 s; bitta faylda **144 ta `Engine()`** → 1728
`executescript`. Ya'ni vaqt modul mantig'ida emas, **har qurilishda sxemani qaytadan
yozishda**. Uch o'lchangan tuzatish:

| Tuzatish | O'lchov |
|---|---|
| 12 sxema skripti → **1** | 183 → 100 ms |
| **Barmoq-izi** (`PRAGMA user_version`, sxemalar `sha256` idan) | bir marta baza uchun |
| `synchronous=NORMAL` faqat sxema yozuvi uchun | 100 → **45 ms** |

**Sovuq qurilish 183 → 44 ms (4×)**, issiq 13 ms. To'liq to'plam **515.7 → 443.0 s
(−14%)**, imzo **aynan o'zgarmadi**.

**Nima qilinmadi:** `scrypt` KDF (204 ms/chaqiruv) ayblanmadi — o'lchandi, u butun
to'plamning **6%** i, va uni arzonlashtirish **95 testni yiqitadi**. Ya'ni u
optimallashtirish emas.

**3. Hujjat va skill.** 20 ta `V05*-IMPLEMENTATION-UZ.md` → **3 mavzuli fayl**
(yo'qotish nol, skript satrlarni solishtiradi), `§147.9` dagi takroriy darslar olib
tashlandi, `boundary-audit` skill **394 → 73 satr** (har safar yuklanadigan qism),
darslar 5 mavzuli `references/` faylida.

**Mening xatom:** mundarija skripti I fazani (§1–§10, 242 satr) o'chirib yubordi;
repo git emas edi. Memory yozuvlaridan qayta tiklandi, hujjatga ochiq izoh qo'yildi.
Saboq skill'ga yozildi: **oraliq o'chiruvchi skript ikkala uchni ham tasdiqlashi kerak.**

## v0.5 blok: Agent oversight (P9, T3)

Mijozning T3 talabi — **“xodimlarni va agentlarni boshqarish”**. Bu blok uning
agent qismi: P9. Yangi modul — `platform_runtime/oversight.py`.

Uchta tool, **hammasi `read`**: `agent.activity`, `agent.cost`, `agent.health`.

- **Yozuv yo‘li umuman yo‘q.** Modulda biron write funksiyasi mavjud emas:
  agentni **o‘zgartirish** — o‘z vakolatini o‘zgartirish demak, bu audit
  zanjirini buzadi. Shuning uchun “read-only” bu yerda siyosat emas, **tuzilma**.
- **Hodisa birlashtirish `p_tasks.agent` orqali**, `p_audit.data` ustida
  `LIKE '%agent%'` emas. Sabab: `sales` bo‘lagi `sales.order_taker` hodisalarini
  **meros qilib olardi** — bu jimgina noto‘g‘ri raqam bo‘lardi.
- **Xarajat halol atributsiya qilinadi.** `p_budget_reservations.request_key`
  agent nomini **saqlamaydi** (tekshirildi: kalit `model:<uuid>` yoki chaqiruvchi
  kaliti). Shuning uchun `attribution: 'agent_run_window'` deb **ochiq** yoziladi
  va yolg‘on per-agent `spent_micro` **qaytarilmaydi** — o‘rniga tenant
  darajasidagi `tenant_spent_micro`, `tenant_ledger_by_status`,
  `tenant_inflight_micro` va `budget_configured` beriladi.
- **O‘qib bo‘lmaydigan metrika “nol” emas.** `health()` da `not_recorded`
  ro‘yxati bor, chunki `0` ko‘rsatish **yolg‘on tasalli** bo‘lardi.
- **`failure_rate` tugagan run bo‘lmasa `None`**, `0.0` emas.
- **E’lon qilinmagan agentni rad etadi**; kelajakdagi `agents`/`agent_catalog`
  hook’i hisobga olingan, u yo‘q bo‘lsa faqat id shakli tekshiriladi.

`config/agent-capabilities.example.yaml` ga `mgmt.agent_supervisor` misoli
qo‘shildi — chegara operator uchun hujjatlashtirilgan.

**39 yangi test** (graph 38 → 44, oversight 33 yangi). `runtime_tests` 1089 →
**1128 test**; error/failure soni o‘zgarmadi.


## v0.5 blok: Briefing (P10)

Mijozning T2/T3 talablari: **boshqaruv bo‘limlari** uchun agentlar va
**xodimlarni/agentlarni boshqarish**. P9 agent qismini berdi; P10 — kunlik
brifing. Yangi modul — `platform_runtime/briefing.py`.

`reengagement` bilan **bir xil koordinator shakli** (jadval → cheklangan o‘qish →
dedup → yetkazish), lekin uchta ataylab qilingan farq:

- **Manba — Business Graph, bitta feed emas.** Brifing kross-tizim: buyurtma
  CRM’dan, tovar ERP’dan, marja Sheets’dan. Shuning uchun `graph.search` /
  `graph.conflicts` orqali o‘qiladi — har manba vakolati provider I/O dan oldin
  tekshiriladi, har qiymat manbasi bilan ko‘rinadi, kelishmovchilik e’lon
  qilinadi.
- **Yetkazish read + notify, approval kerak emas.** Yagona yozuv —
  `telegram.send`, u mijozga emas **operatorga** aytadi. Brifingni approval
  ortiga yashirish operatorni approvalni **refleks bilan tasdiqlashga**
  o‘rgatardi.
- **Model ishtirok etmaydi.** Brifing — fakt hisoboti; uni modeldan o‘tkazish
  “tovar kam” degan gapni qayta ifodalash qadami bo‘lib, faqat aniqlik
  yo‘qotadi. Matn modulda yig‘iladi.

**Ushlandan nuqsonlar (hammasi o‘z kodimda, test yozilayotganda):**

1. **`(0)` yolg‘oni** — o‘qib bo‘lmagan bo‘lim “0 yozuv” deb ko‘rsatilardi, bu
   menejerga “hech narsa bo‘lmagan” degani. Endi `_section_rows` **uch holat**
   qaytaradi (`ok`/`partial`/`failed`) va `failed` bo‘lsa **hech qanday son**
   chop etilmaydi.
2. **`tick` yolg‘on `True`** — bir kunda ikkinchi tick “ish qildim” derdi va
   `next_due` ni **oldinga surardi**; ya’ni har restart ertangi brifingni
   kechiktiraverardi. Endi `False` va `next_due` tegilmaydi.
3. **`_render_row` crash** — ziddiyat qiymati `dict` bo‘lmasa `AttributeError`.

`engine.py` ga `BRIEFING_SCHEMA` qo‘shildi; `worker.py` har tenant uchun
`briefing.tick()` chaqiradi; API’da `GET/PUT /{tenant}/briefing` + ledger
endpointi (`PUT` **owner-only**, chunki haqiqiy chat’ga yetkazishni
rejalashtiradi).

**44 yangi test.** `runtime_tests` 1128 → **1172 test**; error/failure soni
o‘zgarmadi.


## v0.5 blok: Workforce oversight (P9b)

Yangi modul — `platform_runtime/workforce.py`. Uchta **read-only** tool:
`workforce.attendance`, `workforce.shifts`, `workforce.workload`. Menejer
agentlar haqida so‘ragan savollarni xodimlar haqida ham so‘raydi: **kim ishda**,
**kim ortiqcha yuklangan**, **nima muddatidan o‘tgan**.

**Yangi adapter yo‘q — Sheets registri qayta ishlatiladi.** Davomat, smena va
vazifalar bugun ham kotib yuritadigan jadval, shuning uchun modul o‘z provider
yo‘lini ochmaydi: u `sheets.rows` tool’ini **oddiy handler orqali** chaqiradi.
Bitta qatordan o‘tish registr deklaratsiyasi, A1 allowlist, agent tool ruxsati va
connection allowlist’ini **o‘zgarishsiz** qo‘llaydi — ya’ni bu modul **yangi
ma’lumot yo‘li qo‘shmaydi**.

**Asosiy chegara — xodim baholanmaydi.** Faylda ball, reyting, indeks,
samaradorlik foizi yoki xodimlar orasidagi taqqoslash **yo‘q**. Bu PRD v0.5 §8 ga
yozilgan ataylab rad etish: odamlarni saralaydigan raqam ertami-kechmi jazolash
uchun ishlatiladi, platforma esa kim nima uchun kechikkanini bilmaydi. Shuning
uchun modul **son va o‘sha son ortidagi ismlarni** qaytaradi, hukmni menejerga
beradi. `overdue` har doim qatorlari bilan birga keladi — menejer indeksga
ishonmasdan **sababni tekshira oladi**.

**Ustun nomi modeldan kelmaydi.** Model faqat view va limit aytadi;
spreadsheet, range va ustun nomlarini **operator** e’lon qiladi
(`config/workforce.example.json`).

**Uchta “nol emas, `None`” qoidasi:** bo‘sh soat katagi `None` (nol emas, aks
holda smena kamayib ko‘rinardi); `10.01.2026` kabi noaniq sana
`unparsable_due` (taxmin qilish noto‘g‘ri ishni kechikkan qilardi); status ustuni
o‘qilmasa `attendance` **rad etadi** — bo‘sh ro‘yxat “hammasi ishda” bo‘lib
ko‘rinardi.

**Ushlandan nuqson:** modul `MAX_ROWS = 500` deb e’lon qilardi, `sheets` tool’i
esa 200 da to‘xtaydi — ya’ni modul o‘zi hech qachon olmaydigan chegarani va’da
qilardi. 200 ga tenglashtirildi. Qolgan uchta yiqilish **testning** xatosi edi:
connection nomi (`google`), bo‘sh `status_column` (`ValueError` emas,
`Forbidden`), va deterministik soat `1_770_000_000` = **2026-02-02** bo‘lgani
uchun `2026-03-20` fixture’lari kelajakda qolgan edi.

**32 yangi test.** `runtime_tests` 1172 → **1204 test**; error/failure soni
o‘zgarmadi. Registr 49 tool.


## v0.5 blok: Supervisor router (P10b)

Yangi modul — `platform_runtime/supervisor.py`. Rahbar savoli to‘g‘ri bo‘lim
agentiga yo‘naltiriladi. Muammo: menejer kechikkan yetkazib berish savoli
logistika agentiga, to‘xtab qolgan lid savoli sotuv agentiga tegishli ekanini
bilmaydi. U **bitta** savolni o‘z so‘zlari bilan beradi.

**Uchta ataylab qilingan qaror:**

**1. Yo‘naltirish — ruxsat berish emas, marshrut qarori.** Router maqsad uchun
oddiy agent run yaratadi (`AgentLoop.create`), maqsadning **o‘z** policy’si
qaytadan o‘qiladi. Supervisor’ning `tools`, `allowed_connections`, `ladder` va
`allowed_recipients` maydonlari **hech qachon** ko‘chirilmaydi.

**2. Yo‘naltirish — tool emas.** Run yaratish **control plane**da
(`POST /{tenant}/supervisor/route`). Agar tool bo‘lganda, undagi har qanday agent
o‘zi run yasay olardi va hop chegarasi faqat promptga tayanardi. Ro‘yxatda faqat
ikkita **read** tool: `supervisor.route` (xaritani ko‘rsatadi, run yaratmaydi) va
`supervisor.sections`.

**3. Zanjir uch qavat chegaralangan:** `max_hops` (standart 1) **ledger’dan**
sanaladi; router bo‘lgan agentga yo‘naltirish standart holatda **rad etiladi**;
run yaratish tool emas, ya’ni chegarani **kod** majburlaydi.

**Asosiy xavfsizlik xossasi — o‘lchangan, da’vo qilinmagan.**
`scripts/probes/probe_supervisor_authority.py`: hech qanday ma’lumot tool’i tutmagan
supervisor `connectors.read` tutgan agentga yo‘naltiradi → supervisor policy’si
**bayt-bayt bir xil** qoladi, va maqsad **o‘zida yo‘q** tool’ni ishlatmoqchi
bo‘lsa **rad etiladi**. Ya’ni cheklov **bajaruvchiga** bog‘lanadi: *vakolat
bajaruvchiga o‘tadi, so‘rovchiga emas.*

**Mos kelmasa — nom bilan rad.** Default agentga yuborilmaydi:
`status='unrouted', reason='no_section_matched'`. Oylik savoliga sotuv agenti
javob bergani javob bermaslikdan yomonroq.

**Ushlandan nuqsonlar:** eng muhimi — **`max_hops` bezak edi**. Qabul qilinardi,
tekshirilardi, javobda ko‘rinardi, lekin **hech narsani cheklamasdi** (kod har
doim bitta run yaratardi). Endi `_depth()` ledger’dan sanaydi va `hop_cap_reached`
bilan rad etadi. Bu eng yomon nuqson turi: **ishlayotgandek ko‘rinadi**. Yana:
`SUPERVISOR_SCHEMA` `engine.py` da ro‘yxatga olinmagan edi (43 test yiqildi);
test `freeze()` ni ichki `tx()` ichida chaqirgan edi (`database is locked`).

**53 yangi test.** `runtime_tests` 1204 → **1257 test**; error/failure soni
o‘zgarmadi. Registr 51 tool.


## v0.5 blok: Vision events (P11b)

Yangi modul — `platform_runtime/vision.py`. Kamera hodisalarini o‘qiydi. PRD
bo‘shliqni aniq nomlaydi: *«The infrastructure gap is **not the camera**. It is
the **compute layer between the camera and a structured data output**.»* Ya’ni
kamera ham, model ham **bor** — yetishmayotgani ular orasidagi qatlam. Shuning
uchun bu modul **video tizimi emas, hodisa o‘quvchisi**. Uchta **read** tool:
`vision.station_event` / `vision.person_event` / `vision.summary`. Hodisalar
operatorning mavjud reestridan `sheets.rows` orqali o‘qiladi — **yangi transport
yo‘q**.

**Ikki qoida — modul shu ikki qoida uchun mavjud:**

**1. Frame hech qachon platformaga kelmaydi.** Faqat
`{stansiya, hodisa, sana, ishonch}`. Bu ovoz (P5) qoidasi bilan bir xil, lekin
kamera uchun **arzonlik emas, huquqiy majburiyat**: O‘zbekiston qonuni
**O‘RQ-1125** (26.03.2026) bo‘yicha biometrik ma’lumot lokal saqlanishi va
davlat reestriga kiritilishi shart — ya’ni kadrni bulutga chiqaradigan quvur
mijozni **qonunbuzarga aylantiradi**. Bu **strukturaviy** qulflangan: har bir
tool sxemasida `frame`/`image`/`stream`/`rtsp`/`base64` **yo‘qligi**, va modul
manba matnida `cv2`/`torch`/`base64`/`write_bytes` **yo‘qligi** test bilan
tekshiriladi.

**2. Shaxsni aniqlaydigan hodisa — biometrik, ya’ni `human_led` majburiy.**
PRD tool qatlamini ikki sinfga bo‘lishni talab qiladi: `vision.station_event`
(shaxssiz: nuqson, sikl, bekor, SOP — chegara yumshoq) va `vision.person_event`
(**yuzni aniqlash — biometrik**). Bu **qattiq darvoza**: faqat agentning
`ladder`’i `human_led` bo‘lganda va operator `biometric_ack` berganda o‘qish
mumkin, va darvoza **har qanday provayder I/O sidan oldin** ishlaydi.


**Asosiy xavfsizlik xossasi — o‘lchangan, da’vo qilinmagan.**
`scripts/probes/probe_vision_biometric_gate.py` uch `ladder` darajasida bir xil
chaqiruvni yurgizib, har biri **nechta provayder so‘rovi** yaratganini sanaydi:

```
autonomous         refused: Person-identifying ...    0 GET
human_assisted     refused: Person-identifying ...    0 GET
human_led          read 1 event(s)                    1 GET
```

Muhim raqam — rad etilish emas, **nol GET** bilan rad etilishi: ma’lumotni
o‘qib bo‘lgach rad etadigan darvoza darvoza emas.

**Ushlandan nuqsonlar** (ikkita haqiqiy modul nuqsoni):

1. **`sensitivity: station` qaysi sinf shaxsiy ekanini aytmasdi** — `person_classes`
   reestr ichida edi, shuning uchun `station` reestrida u bo‘sh bo‘lardi va `yuz`
   qatori `nuqson` dan farq qilmasdi. Tuzatish: **sinflar lug‘ati endi tenant
   bo‘yicha**, reestr bo‘yicha emas. Sabab: **sinf — HODISANING xossasi, u yotgan
   jadvalning xossasi emas**. Reestr bo‘yicha cheklanganda noto‘g‘ri jadvalga
   tushgan holda biometrik qator **shaxssiz yo‘ldan o‘qilardi** — bu aynan
   qonuniy chegara oldini olish uchun qo‘yilgan xato.
2. **`_bind` intern tugunni aktiv deb qabul qilardi** (`prefix_path` har qanday
   chuqurlikni oladi), shuning uchun `zavod-1/sex-1` «bog‘landi». Endi `parse_path`
   — PRD hodisani `graph.timeline (asset)` ga bog‘laydi, intern tugunning
   timeline’i yo‘q.

**Ochiq topilma — tuzatilmagan, lekin yashirilmagan:**
`sheets.py` bo‘sh `allowed_connections` ni **«hammasi ruxsat»** deb o‘qiydi
(`if allowed and ...`), kod bazasining qolgan qismi esa (`engine.py`,
`connectors.py`, `google_adapters.py`, `business_graph.py`) **«hech narsa
ruxsat»** deb o‘qiydi. Ya’ni bir xil policy qiymati bir yo‘lda «taqiqlangan»,
boshqasida «ruxsat». Odatdagi dispatch’da `engine.py` oldinroq tekshirgani uchun
amalda ko‘rinmaydi. Ataylab tegilmadi (boshqa blok doirasi, `config/` da hech bir
deklaratsiya bo‘sh ro‘yxatga tayanmaydi) — **lekin alohida hal qilinishi kerak**.

**48 yangi test** (`test_vision.py`) + `probe_vision_biometric_gate.py` +
`check_vision_example.py`. `runtime_tests` 1315 → **1363 test**. To‘liq to‘plam:
`Ran 1363 tests` — **failures=1, errors=149**, ya’ni baseline **aynan
o‘zgarmadi** (1363 − 1315 = 48 yangi test, error soniga qo‘shilmadi).
Registr **60 tool** (`known_tool_names()`). Hodisa **aktivga** bog‘lanadi (segment
bo‘yicha, prefiks bo‘yicha emas), bog‘lanmagani **hisobotda** ko‘rinadi, o‘qish
**oyna + limit** bilan cheklangan.

> **Hujjat tuzatishi (P8 da topildi):** yuqoridagi bloklar registr sonini «59
> tool» deb yozgan edi. Haqiqiy son `known_tool_names()` bo‘yicha **60**, katalogsiz
> `build_registry()` bo‘yicha esa 59 edi. Ya’ni oldingi hisobotda son bir
> birlikka xato yozilgan va keyingi hisobotlarga o‘tib ketgan. Tekshirilmagan
> son — tekshirilmagan da’vo, shuning uchun bu yerda ochiq tuzatiladi.


## v0.5 blok: WhatsApp kanali (P8, T1)

Mijozning T1 talabi — **WhatsApp integratsiyasi**. Bu uning kanal qismi.
Yangi modul — `platform_runtime/whatsapp.py`. Uchta tool: `whatsapp.window`
(**read**), `whatsapp.templates` (**read**), `whatsapp.send` (**write**, approval).

**Blok funksiya emas, chegara.** PRD §2.8 bir gapni beradi: oyna yopilganda
erkin matn **hech qachon** yetib bormaydi — Meta **xato 131047** qaytaradi, mijoz
esa javob kutib qoladi va CRM’da «javob berilgan» bo‘lib ko‘rinadi. Shuning uchun
butun tuzilish bitta o‘lchanadigan mezonga bo‘ysunadi:

> **131047 ga yetib bo‘lmasligi kerak — dizayn bo‘yicha.**

Bu «tutamiz va retry qilamiz» emas, bu **provider I/O dan oldin rad etish**.

- **Oyna — o‘qiladi, taxmin qilinmaydi.** `whatsapp.window` yopilish vaqtini
  **bizning soat** va **oxirgi inbound** vaqtidan hisoblaydi, ya’ni javob
  operatorga **ko‘rsatilishi mumkin**; providerdan qayta so‘ralmaydi.
- **Yopiq oynada erkin matn rad etiladi — 0 POST bilan** va **shablonga jimgina
  tushirilmaydi**. Sabab: toifani modelga tanlatish akkaunt sifat reytingini
  tushirishning aynan o‘zi.
- **Shablon — operator konfiguratsiyasi.** Model uni **nomi bo‘yicha** tanlaydi;
  `language` ham, `category` ham **argument emas** (schema’da yo‘q). Shablon
  yaratish/topshirish tooli umuman yo‘q: bu Meta ko‘rigi va operatorning huquqiy
  javobgarligi.
- **Kontakt — operator e’lon qilgan.** Model bergan raqam qabul qilinmaydi:
  bu **cheksiz send yuzasi**. Har chaqiruvda bitta kontakt; **broadcast yo‘li
  yo‘q**.
- **Ikkita token, hech qachon bitta.** `whatsapp_business_messaging` (yuborish)
  va `whatsapp_business_management` (akkaunt) — Meta’ning **alohida scope’lari**,
  shuning uchun alohida konfiguratsiya kalitlari. Send faqat **o‘z** kalitini
  o‘qiydi.

**Probe bilan o‘lchandi** (`scripts/probes/probe_whatsapp_window.py`, haqiqiy chiqish):

```
open window + text                     sent                                                1 POST
CLOSED window + text                   refused (names 131047)                              0 POST
CLOSED window + declared template      sent                                                1 POST
open window + undeclared template      refused                                             0 POST
```

Muhim raqam — **nol**, rad etilish emas. Va shablon yopiq oynada **baribir
ketadi**: blok WhatsApp’ni o‘chirmadi, Meta **ruxsat bergan yagona yo‘lni**
ishlatdi.

**Ushlandan nuqsonlar** (uchta):

1. **`engine.py` chiquvchi manzil tekshiruvi qattiq kodlangan edi** — haqiqiy
   nuqson, P8 dan **oldin** ham bor edi. `engine._submit` toollar ro‘yxatini
   `{'telegram.send','instagram.send'}` deb sanardi, shuning uchun **har qanday
   yangi chiquvchi tool** tekshiruvsiz qolardi. `whatsapp.send` aynan shunday
   qoldi: modulning kontakt deklaratsiyasi himoya qilardi, lekin engine
   darajasidagi `allowed_recipients` tekshiruvi **umuman qo‘llanmasdi**. Endi
   nomlangan konstanta + kanalga bog‘liq qoida. 2 regressiya testi qulflaydi.
2. **Naive vaqt tamg‘asi UTC deb o‘qilardi** — `sheets` sanani
   `FORMATTED_STRING` bilan beradi, ya’ni mintaqasiz: `2026-09-19 10:00:00`.
   Buni UTC deb o‘qish **5 soatlik** xato beradi (O‘zbekiston UTC+5) va
   yuborishni oynadan tashqariga qo‘yadi — modul oldini olish uchun qurilgan
   xatoning o‘zi. Endi mintaqasiz vaqt tamg‘asi **rad etiladi**.
3. **Registry soni hujjatda xato edi** — P11b hisoboti «59 tool» deb yozgan,
   haqiqiy son 60 edi (yuqoridagi tuzatishga qarang).

**52 yangi test** (`test_whatsapp.py`) + probe + `check_whatsapp_example.py` +
`config/whatsapp.example.json` + 2 agent misoli. `runtime_tests` 1363 → **1415
test**. To‘liq to‘plam: `Ran 1415 tests` — **failures=1, errors=149**, ya’ni
baseline **aynan o‘zgarmadi** (1415 − 1363 = 52 yangi test, error soniga
qo‘shilmadi). Registr `known_tool_names()` bo‘yicha **63 tool**.


## v0.5 blok: Hujjat qabul qilish va hisob-kitob nazorati (P8b, T2)

PRD §2.5 bitta zanjirni beradi: `upload → parse → normalize → validate → duplicate
check → PO/delivery matching (3-way) → exception and fraud controls → approval
routing → payment and ERP sync planning → mock ERP post → audit log`. Bu blok
aynan shu zanjir — yangi modul `platform_runtime/documents.py`, beshta tool:
`document.parse` (**write**), `document.match` (**read**), `document.duplicates`
(**read**), `document.fraud_signals` (**read**), `document.posting_plan`
(**write**, approval).

Blokning **butun ma’nosi** — bitta chegara, va u PRD §8 ning o‘z ko‘lami:

> **Pul harakatlantirmaydi. Hujjatni tayyorlaydi, to‘lovni tasdiqqa qo‘yadi.**

Ya’ni bu «to‘lashdan oldin so‘raymiz» emas — **to‘laydigan qadam umuman yo‘q**.
Kuchli da’vo uchta mustaqil yo‘nalishdan sinovdan o‘tkaziladi:

- **Sintaktik:** modulning 16 ommaviy funksiyasi/klassi pul lug‘ati bo‘yicha
  tekshiriladi. `pay_invoice` qo‘shgan kontribyutor shu yerda yiqiladi.
- **Registry orqali:** 5 tool × 3 ladder = **15 katak**, haqiqiy `Engine` +
  haqiqiy SQLite, tarmoq o‘rnida hisoblagich → **hammasida 0 provider chaqiruvi**,
  `autonomous` ham kiradi.
- **Chiqishda:** reja to‘lov qadamini **tushirib qoldirmaydi**, balki
  `executes_payment: False` deb **ochiq aytadi**.

**Pul — butun son (minor units), hech qachon float.** Ikkilik suzuvchi nuqta har
bir o‘nlik summani aniq saqlay olmaydi, va **bir tiyin xato qiladigan tekshiruv —
ishonib bo‘lmaydigan tekshiruv**. Yaxlitlash emas, **kesish** ishlatiladi (yuqoriga
yaxlitlash keyin solishtiriladigan sonni shishirardi). Hujjat **o‘zi bilan
kelishmasa** rad etiladi: satr elementlari yig‘indisi ko‘rsatilgan jami summaga
teng bo‘lishi shart. Noaniq sana (`10.01.2026`) ham rad etiladi — taxmin dublikat
oynasini **oylarga** suradi.

**Dublikat kaliti `(kind, supplier, number)` — summani ataylab chiqarib
tashlaydi.** Bir xil raqam, **boshqa summa** — bu qayta chiqarilgan hisob-faktura,
ya’ni aynan shu tekshiruv ushlaydigan firibgarlik. Qayta yuborilgan raqam asl
nusxani **qayta yozmaydi**: asl nusxa — birinchi marta ko‘rilgan narsaning dalili.

**Uch tomonlama: `incomplete` ≠ `mismatch`.** Dalilning **yo‘qligi** va **zid
dalil** — ikki xil fakt; ularni jipslashtirish tasdiqlovchini yo‘q narsani
qidirishga yuborardi. Tolerantlik e’lon qilinmasa, kelishuv **aniq** (nol), chunki
nolga teng bo‘lmagan default operator rozi bo‘lmagan nomuvofiqliklarni jimgina
kechirardi. Ikki xil valyuta kursisiz **hech qachon** solishtirilmaydi.

**Firibgarlik — belgi, hukm emas.** Har bir belgi **o‘z dalilini** nomlaydi:
tasdiqlovchi tekshira olmaydigan bayroqni o‘qimasdan bosishga o‘rganadi. Belgilar
ikki guruhga bo‘linadi — **material** (`duplicate_altered`, `amount_outlier`,
`bank_account_changed`) rejani ushlab turadi; **kuchsiz** (`round_number`,
`weekend_date`, `no_history`) faqat xabar qilinadi.

**Ushlandan nuqsonlar** (uchtasi bu blokda tug‘ilgan):

1. **Tool’lar engine darvozasidan umuman o‘ta olmasdi** — haqiqiy nuqson va u
   **jimgina** edi. Sxemada `fields` bare `{'type': 'object'}` deb e’lon qilingan
   edi; registry validatorida `properties` e’lon qilinmagan bare object uchun
   **har bir** nested dict rad etiladi (`Schema fields mismatch`). Natijada beshta
   tool’ning **hammasi** har bir chaqiruvni rad etardi: tool ro‘yxatda bor,
   capability pack’da havola qilingan, `risk` to‘g‘ri — lekin **uni submit qilib
   bo‘lmaydi**. Registratsiya testi buni **hech qachon** tutmaydi. **Probe** topdi,
   chunki u `submit` ni haqiqiy `Engine` orqali o‘tkazadi. Tuzatish: tuzilgan
   argument **JSON satri** sifatida, izchil dekoder (`_payload`) bilan. 3 regressiya
   testi qulflaydi.
2. **Kuchsiz belgilar rejani ushlab turardi** — toza, mos kelgan, **yumaloq**
   hisob-faktura `review_fraud_signals` olardi. Bu o‘z kodimdagi **dizayn
   ziddiyati**: haqiqiy hisob-fakturalarning ko‘pchiligi yumaloq, shuning uchun
   bunga tayanish tasdiqlovchini ogohlantirishni o‘qimasdan bosishga **o‘rgatadi**.
   Material/kuchsiz ajratmasi shundan tug‘ildi.
3. **`weekend_date` yolg‘on da’vo qilardi** — «bu yetkazib beruvchi odatda
   chiqarmaydi» deb, **tarixni tekshirmasdan**. Endi faqat **o‘z tarixida** dam
   olish kuni yo‘q bo‘lganda ko‘tariladi, va tekshirganimizni **da’vo qilmaymiz**.

**84 yangi test** (`test_documents.py`) + probe + `check_documents_example.py` +
`config/documents.example.json` + 2 agent misoli (`finance.ap_clerk`,
`finance.ap_reviewer`). `runtime_tests` 1415 → **1499 test**. To‘liq to‘plam:
`Ran 1499 tests` — **failures=1, errors=149**, ya’ni baseline **aynan o‘zgarmadi**
(1499 − 1415 = 84 yangi test, error soniga qo‘shilmadi). Registr
`build_registry()` bo‘yicha **67 tool**; capability pack **15 agent, 37 tool
havolasi, 0 noma’lum**.

> **Migratsiya eslatmasi:** `engine.py` `p_migrations` **8-versiya** oldi
> (documents sxemasi). `test_v036_upgrade.py` va `test_v037_migration.py`
> `[1..7]` ni qattiq tekshirardi va **to‘g‘ri** yiqildi — ular `[1..8]` ga
> yangilandi, jimgina o‘chirilmadi.


## v0.5 blok: Asset model (P11)

Yangi modul — `platform_runtime/assets.py`. Platformada hozirgacha **identitet**
yo‘q edi: savol berish mumkin, lekin «P-100 pressning sikli vaqti» deb so‘ralganda
`P-100` hech narsaga bog‘lanmagan edi. Har bir yangi modul o‘z identitetini
o‘ylab topishi kerak bo‘lardi va ular bir-biriga mos kelmasdi. P11 shu
identitetni **bir marta, operatorning o‘z reestrida** o‘rnatadi.

**Uchta ataylab qilingan qaror:**

**1. Yo‘l — identitetning o‘zi.** `zavod-1/sex-2/liniya-3/stanok-7` — manzil
emas, **identitet**. Reestrdagi `id` ustuni aynan shu. Alohida `asset_id`
yasalmaydi, chunki ikkita raqamli id vaqt o‘tib ajralib ketadi.

**2. Ierarxiyani operator e’lon qiladi, kod uni *tekshiradi*.** `levels` — odam
uchun yorliq, kod mos keladigan lug‘at emas. Kod faqat **uzunlikni** va nomning
qonuniyligini tekshiradi, shuning uchun boshqa mijoz
`('uchastka', 'kran')` deb e’lon qilishi mumkin va kod o‘zgarmaydi.

**3. Yozish yo‘li yo‘q.** Faylda bitta ham `write` tool yo‘q. Sabab: modelni
tahrirlay oladigan tizim har bir hodisa va hisobot tayanadigan **identitetning
muallifiga** aylanadi. Platforma — OT **o‘quvchisi**, OT sotuvchisi emas.
Beshta tool ham `read`: `asset.levels/tree/children/descendants/resolve`.

**Asosiy xavfsizlik xossasi — segment, satr prefiksi emas.**
`'zavod-10'.startswith('zavod-1')` **`True`** — sodda satr tekshiruvi bitta
zavod so‘roviga boshqa zavodning aktivlarini qaytaradi. `is_descendant()`
**segment bo‘yicha** solishtiradi. Bu xato jimgina bo‘ladi: daraxt chiziladi,
aktivlar sanaladi, xato chiqmaydi — shunchaki begona zavod uskunasi ro‘yxatda
paydo bo‘ladi. Fixture’da `zavod-1` bilan `zavod-10` **ikkalasi ham** bor.

**Ikkita yo‘l qoidasi — ataylab ikkita funksiya.** `parse_path()` **aynan** to‘liq
chuqurlikni talab qiladi (identitet: `resolve`, reestrdagi `id`). `prefix_path()`
**ko‘pi bilan** to‘liq chuqurlikni qabul qiladi (navigatsiya: `children`,
`descendants`, `tree(path=...)`). Flag emas, **ikki nom** — chunki noto‘g‘ri
chaqiruv flag bilan jimgina o‘tib ketadi va identitet chaqiruvi bir kuni prefix
qoidasiga tushib qoladi.

**Ushlandan nuqsonlar** (uchta haqiqiy modul nuqsoni):

1. `children`/`descendants` intern tugunni **umuman qabul qilmasdi** — bitta
   qat’iy `parse_path` tufayli `children('zavod-1')` `ValueError` berardi
   (30 test shu bitta sababdan qizil edi). `prefix_path()` ajratildi.
2. `children` **noto‘g‘ri `level`** qaytarardi — ota-onaning darajasini, bir
   pog‘ona sayoz. `level` (so‘ralgan) va yangi `child_level` (qaytgan) ajratildi.
3. **Graf preflight Sheets manbalar uchun `allowed_connections` ni umuman
   tekshirmasdi** — shart faqat `connectors.read`/`database.read` uchun edi, lekin
   `sheets.rows` ulanishni **reestrdan** oladi. Ya’ni P1 dan beri:
   `allowed_connections: ['erp']` bo‘lgan agent Sheets manbasini baribir o‘qiy
   olardi. Tuzatildi (`_register_connection`), va `test_business_graph.py` ga
   **ikkita** regressiya testi yozildi. Ahamiyatli: grafikning o‘z fixture’ida bu
   yo‘l hech qachon sinalmagan edi — Sheets manbasi doim ikkinchi bo‘lib, `erp`
   birinchi javob berardi.

**56 yangi test** (`test_assets.py`) + **2 regressiya** (`test_business_graph.py`).
`runtime_tests` 1257 → **1315 test**. To‘liq to‘plam: `Ran 1315 tests` —
**failures=1, errors=149**, ya’ni oldingi baseline **aynan o‘zgarmadi**
(1315 − 1257 = 58 yangi test, error soniga qo‘shilmadi). Registr **56 tool**.


## v0.4 blok: Business Graph PRD va Sheets registers

Real mijoz feedbackidan chiqqan yangi yo‘nalish. Tafsilot:
`docs/prd-v04/00-UNIFIED-BUSINESS-BRAIN-PRD-UZ.md` va `V04-IMPLEMENTATION-UZ.md`.

**Asosiy qaror:** platforma mavjud tizimlarni **almashtirmaydi** (AmoCRM/Bitrix24,
MoySklad, 1C, Google Sheets, telefoniya) — ularning **ustida** turadi. Sabab: mijozning
eng katta og‘rig‘i “Odoo/SAP’ni o‘rgatish muammo”. Xodim o‘z ekranlarida ishlashda davom
etadi, faqat endi o‘zbekcha **so‘raydi**.

- `platform_runtime/sheets.py` — operator e’lon qilgan **sheets registrlari**. Yangi
  tool’lar: `sheets.registers`, `sheets.read`, `sheets.rows` (hammasi **read**).
  Moliya va HR bugun allaqachon Google Sheets’da — bu eng arzon birinchi qadam.
- Manzil faqat operator konfiguratsiyasi; A1 notation qat’iy tekshiriladi; kirillcha
  varaq nomlari qo‘llanadi; e’lon qilinmagan registr bo‘sh varaq emas, **rad etiladi**.
- Ko‘p-range write **qo‘shilmadi** — undan zaif ikkinchi write yo‘li yo‘qligidan yomonroq.
- PRD’da 8 fazali yo‘l xaritasi: P1 Business Graph, P2 brifing, P3 Sheets, P4 MoySklad,
  P5 ovoz, P6 nazorat/eskalatsiya, P7 ishlab chiqarish va telefoniya.

**25 yangi test.** `runtime_tests` 1026 → **1051 test**; error/failure soni o‘zgarmadi.

## v0.3.10 blok: re-engagement loop

Tafsilot: `V0310-IMPLEMENTATION-UZ.md`.

- `reengagement.py` — owner-only scheduled koordinator: due policy, authority qayta
  tekshiruvi, read-only feed, lid claim, per-lid AgentLoop run.
- **Feed oddiy tool handler orqali o‘qiladi** (`crm.lead.stalled`).
- **Provider xatosi ≠ “lid yo‘q”**: `Conflict` bo‘lsa sikl qayta rejalashtiriladi + audit.
- **Dedup kaliti `(tenant, connection, lead_id)`** — policy kirmaydi, chunki bir CRM’ga
  ikki policy qarasa bitta mijozga ikki marta yozilishi mumkin edi.
- **Outreach approval-gated qoladi**: loop yozmaydi, yozuv `risk=write` orqali.

**36 yangi test.**

## v0.3.9 blok: 1C va custom HTTP CRM adapterlari

Tafsilot: `V039-IMPLEMENTATION-UZ.md`.

- `crm/onec_adapter.py` — 1C:CRM / 1C:УТ HTTP service, Basic/Bearer auth, kirillcha
  `response_map`, `created_id` envelope, identifikatorsiz yozuvni `Conflict` qilish.
- `crm/custom_http_adapter.py` — operator-deklaratsiya qilgan HTTP CRM uchun method/path/
  body allowlist, percent-encoded placeholder, header env reference, `Host` override taqiqi.
- `crm.lead.stalled` — re-engagement uchun read-only feed; provayder xatosi `Conflict`,
  bo‘sh ro‘yxat emas.
- `search_mode` / `find_leads_by_mode` routing contract’ga ko‘chirildi; reconciler ham
  shu qoidani ishlatadi.
- `describe_crm` endi haqiqiy: `transport_implemented` adapteri bo‘lmagan driverda `False`.

**78 yangi test.**

## Qayta tekshirish

Original Google Drive ZIP SHA-256: `c4fd4437f2b05386e41386989a9ad8bd5cad2fe9b18e583be6d4ea044b61c473`. Original manifestdagi 418 fayl mos; ZIPda 419 entry.

Joriy yakuniy lokal dalil: `docs/verification/development-v038-final/summary.json` (v0.3.8) va
`docs/development/LOCAL-VERIFICATION-UZ.md` (Windows portability tuzatishlari).

| Gurup | Test |
|---|---:|
| Python runtime | 1172 |
| Release helpers | 3 |
| Manifest helpers | 6 |
| Node runner | 24 (POSIX-only) |
| Browser session | 12 |
| Browser OAuth | 12 |
| Browser Google data boundary | 16 |
| HTTP integration (dependency-backed) | 80 passed |
| Jami | **1325** |

SQLite demolari va Python/JS/TS sintaksisi test soniga qo‘shilmaydi. HTTP, React
typecheck/build, live provider, native qurilmalar va production acceptance o‘tkazilmagan.

## Bajarilgan source

GoogleSync Gmail va Drive uchun snapshotdan oldin boshlang‘ich cursorni saqlaydi; snapshotdan
keyin o‘sha nuqtadan delta yuradi. Calendar initial full va incremental tokenlarini to‘g‘ri
ajratadi. Pagination tugamaguncha boshlang‘ich token almashtirilmaydi. O‘chirilgan yozuvlar
body saqlamaydigan tombstonega aylanadi. Token muddati tugasa avtomatik destructive reset yo‘q.

GoogleReconciler faqat GET bilan provider dalilini o‘qiydi. Yetarli dalil bo‘lmasa uncertain
saqlanadi. Musbat dalilda engine step va dispatch journal bitta transactionda yangilanadi.
Qayta send/create yo‘q.

CRM qatlami: Bitrix24, Kommo, 1C va operator-deklaratsiya qilgan custom HTTP typed
adapterlari; plan fingerprinting, approval-gated write, faqat musbat dalil bilan settle
qiladigan reconcile.

## Chegaralar

Bu metadata sync: xat matni, attachment va Drive fayl kontenti olinmaydi. Gmail sahifasidagi
o‘zgargan yozuvlar 100 tadan oshsa cursor siljimaydi, bounded staging buffer hali kerak.
Snapshot Gmail sahifasi 20, Drive/Calendar 100 item bilan cheklangan. Shared-drive maxsus
enumeration hali yo‘q. Record version lokal observation sequence, provider versiyasi emas.

Computer’da FastAPI/pytest/Pydantic/HTTPX/PyJWT va Next/React dependency’lari mavjud emas
(cryptography ham yo‘q), shuning uchun 138 vault testi va 15 HTTP testi lokal yurmaydi.
`runtime_tests` to‘liq yurganida jami **151 failure**: 138 `cryptography` sababli
(vault, OAuth, Google sync/reconcile/adapters) va **13 Windows-POSIX-only**
(`test_portable_fs` + `Node runner` + `test_macos_bundle`), ular ataylab yiqiladi.
Workspace tarmoq allowlisti bo‘sh; live provider credentials ishlatilmadi. Node runner va
`test_portable_fs` POSIX-only xavfsizlik kontraktini talab qiladi, shuning uchun Windows
hostda yiqiladi — bu ataylab, README filesystem ijrosini faqat Linux uchun yoqqan.

**v0.5h da qayta yurgizildi:** `Ran 1415 tests` — **failures=1, errors=149**, ya’ni
oldingi baseline **aynan o‘zgarmadi** (1257 → 1415 test qo‘shilganiga qaramay).
Jami yangi test (P1 auditi 6 + P9 33 + P10 44 + P9b 32 + P10b 53 +
P11 asset 56 + graf regressiyasi 2 + P11b vision 48 + P8 whatsapp 52 = **326**)
**hammasi yashil**, mavjud yiqilishlarga hech biri sabab bo‘lmadi. Yangi 158 test
error soniga **qo‘shilmadi** — bu `business_graph`, `oversight`, `briefing`,
`workforce`, `supervisor`, `assets`, `vision` va `whatsapp` modullarining
regressiya keltirmaganini tasdiqlaydi. (Sonni da’vo qilishdan **oldin**
tekshirildi: `V05E` da aynan shu tekshiruv yolg‘on «regressiya yo‘q» hisobotini
ushlagan edi.) P8 da bu tekshiruv yana o‘zini oqladi: `engine.py` dagi chiquvchi
manzil darvozasi o‘zgargandan keyin ham error soni 149 da qoldi.

**v0.5i da qayta yurgizildi:** `Ran 1499 tests` — **failures=1, errors=149**,
ya’ni baseline **yana aynan o‘zgarmadi** (1415 → 1499). P8b `document_intake`
**84 yangi test** qo‘shdi va ularning hech biri mavjud yiqilishlarga sabab
bo‘lmadi. Jami v0.5 yangi testi: **326 + 84 = 410**.

**v0.5j da qayta yurgizildi:** `Ran 1571 tests` — **failures=1, errors=149**,
ya’ni baseline **uchinchi marta aynan o‘zgarmadi** (1499 → 1571 = +72 yangi
test). Bu safar bir marta **chalg‘idim**: birinchi yurgizish `failures=2,
errors=150` berdi va yiqilishni **o‘zimning o‘zgarishlarim** deb o‘yladim.
Tekshiruv uni **test durotkasi** ekanini ko‘rsatdi: `ISO_OFFSET =
2026-09-19T10:00+05:00` + 24 soat = `2026-09-20T10:00+05:00`, va to‘plam aynan
**o‘sha kuni 10:24 da** yurgizildi — ya’ni test **mening o‘zgarishlarimdan
qat’i nazar** 10:00 dan boshlab yiqilardi. Bu **haqiqiy nuqson** edi
(chirigan to‘plam), va u tuzatildi: parsing fixture’lari o‘zgarmas qoldi, oyna
fixture’lari real soatga **nisbiy** bo‘ldi.

**v0.5k da qayta yurgizildi:** `Ran 1636 tests` — **failures=1, errors=149,
skipped=1**, ya’ni baseline **to‘rtinchi marta aynan o‘zgarmadi** (1571 → 1636 =
+65 yangi test, P8d). Yagona `failures` — Windows-only `test_all_files_private`
(`AssertionError: 0 != 63`), u P8 dan oldin ham bor edi.

**v0.5l da qayta yurgizildi:** `Ran 1688 tests` — **failures=1, errors=149,
skipped=1**, ya’ni baseline **beshinchi marta aynan o‘zgarmadi** (1636 → 1688 =
+52 test, P8L). Qo‘riqchi **6 ta metod** sifatida yozilgan, lekin `GraphTests`
dan meros olgani uchun to‘plamda **52** test bo‘lib ishlaydi — ya’ni qo‘riqchi
**ataylab** har bir mavjud graf sozlamasiga ham qo‘llanadi.

**v0.5m da qayta yurgizildi:** `Ran 1742 tests` — **failures=1, errors=149,
skipped=1**, ya’ni baseline **oltinchi marta aynan o‘zgarmadi** (1688 → 1742 =
+54 test, P8e). `test_erp.py` da 54 test, va ulardan 7 tasi aynan shu
yurgizishdan **keyin** qo‘shilgan `custom_http` qamrovi uchun — ya’ni
foydalanuvchining "ikkalasi ham" talabidagi ikkinchi adapter **bo‘shliq** edi va
u yopildi.

**v0.5n da qayta yurgizildi (2026-09-21):** `Ran 2784 tests` — **failures=1,
errors=11, skipped=1**, ya’ni baseline **aynan o‘zgarmadi**. Son 2800 → 2784 =
**−20 + 4**: 13 ta takroriy "registratsiya idempotent" testi olib tashlandi (ba’zilari
meros orqali ikki marta sanalardi, shuning uchun 20), va ularning o‘rniga **4 ta
kuchliroq** shartnoma testi qo‘shildi.

**Muhim o‘lchov uslubi:** imzo **faqat boshqariladigan venv bilan** to‘g‘ri
(`cryptography` bor). Yalang‘och interpretatorda 138 test `VaultError` beradi va
imzo `errors=153` bo‘lib o‘qiladi — bu **muhit fakti**, kod fakti emas. Shu sababli
`scripts/measure_suite.py` endi qaysi interpretatorni ishlatganini **chop etadi**,
va KDF-tekshiruv rejimi olib tashlandi (u imzoni buzardi).

P8e **audit tufayli** ushlangan **oltita** haqiqiy nuqson: `_date_text`
`2026-13-01` ni qabul qilardi; `failed` qator o‘z retry’sini to‘sardi;
`posted()` `failed` qatorni qaytarardi; o‘z `PATH_RE` im `/a/../b` va
`//evil.example` ni qabul qilardi; yetishmayotgan credential yutilardi; va
buzuq maydon "yetishmayotgan" deb xabar berilardi. Eng o‘rgatuvchisi — **o‘z
yo‘l-gate’ini yozish**: u allaqachon mavjud va testdan o‘tgan
`safe_relative_path` ni takrorladi va takrorlash jarayonida **boshqacha** qaror
qabul qildi.

P8d **probe tufayli** ushlangan nuqson: `_window_from_events` saqlangan
`window_until` ni (ya’ni **tugash vaqtini**) `window_state` ga uzatardi, u esa
unga yana 24 soat qo‘shadi. Natijada **25 soat oldin** yozgan mijoz oyna
**ochiq** deb o‘qilardi — bu aynan modul oldini olish uchun qurilgan **131047**
holati. Testlar buni tutmadi, **probe tutdi**; tuzatish `float(stamp) -
WINDOW_SECONDS` + bitta regressiya testi.

**v0.5n da qayta yurgizildi:** `Ran 1791 tests` — **failures=1, errors=149,
skipped=1**, ya’ni baseline **yettinchi marta aynan o‘zgarmadi** (1742 → 1791 =
+49 test, P6). `test_escalation.py` da 45 test, `escalation.preview` va
`escalation.schedules` registrda (`build_registry()` = 74), va
`probe_escalation_boundary.py` oltita xavfsizlik xususiyatini o‘lchaydi.

P6 **probe tufayli** ushlangan **haqiqiy nuqson** — va u modulning o‘z
docstring’iga zid edi: `_claim` da `sent` qator **faqat cooldown ichida**
qayta urinilmasdi, ya’ni cooldown o‘tishi bilan **o‘sha kechikkan vazifa yana
eskalatsiya qilinardi** — har kuni, vazifa kechikkan ekan. Docstring esa
aksiyatni va’da qilardi ("A sent digest is a decision already taken"), kod esa
teskarisini qilardi. Ya’ni menejer **"Faktura #12 kechikdi"** xabarini har kuni
olardi — kanalni o‘chirishga olib keladigan aynan shu naqsh. Tuzatish: `sent` —
**umrbod terminal** (bu kalit uchun), faqat `queued` (crash) va `failed`
(cooldown o‘tgach) qayta uriniladi. Bitta regressiya testi qo‘shildi
(`test_a_delivered_item_is_never_re_sent_however_long_it_stays_late`), va probe
ham shu kuchliroq xususiyatni o‘lchaydi. Bu **P8d bilan bir sinf**: testlar
tutmadi, probe tutdi.

P6 da yana bir audit topilmasi: unread/reestr xatosi
`except (Conflict, ValueError, LookupError)` bilan tutilardi, lekin Sheets
transport nosozligi **`SheetsError`** (ya’ni `RuntimeError`) ko‘taradi — ya’ni
provider uzilishi sikl ichida **tutilmagan istisno** bo‘lib chiqardi va
`escalation.cycle_failed` audit yozilmasdan `tick()` yiqilardi. Endi o‘qish
ataylab `except Exception` bilan o‘raladi (klass nomigina yoziladi), chunki
har qanday "o‘qish bo‘lmadi" holatida javob bir xil bo‘lishi kerak: xabar
yuborilmaydi, sikl qayta rejalashtiriladi.

Bu blokda tekshiruv o‘zini **ikki marta** oqladi:

- Birinchi yurgizish `failures=3` berdi. Bittasi kutilgan
  (`test_all_files_private`), ikkitasi **mening** blokimdan:
  `test_v036_upgrade.py` va `test_v037_migration.py` `p_migrations` ro‘yxatini
  `[1..7]` deb qattiq tekshirardi. Bu **to‘g‘ri** testlar edi — ular sxema
  versiyasi o‘zgarganini aynan ko‘rsatdi. Ular `[1..8]` ga **yangilandi**,
  o‘chirilmadi. Ya’ni `failures=3` ni «baseline» deb yozib qo‘yish oson edi;
  tekshiruv buni ushladi.

## Keyingi ish

1. `agent_oversight` (P9): **bajarildi** — `oversight.py`, uchta **read** tool
   (`agent.activity`, `agent.cost`, `agent.health`). Agentni **o‘zgartirmaydi**,
   chunki modulda yozuv yo‘li umuman yo‘q. 33 yangi test.
2. `briefing` (P10): **bajarildi** — `briefing.py`, operator e’lon qilgan
   kunlik jadval. Manba endi `business_graph` orqali, yagona yozuv
   `telegram.send`. 44 yangi test. UI va live acceptance qoldi.
3. `workforce_oversight` (P9b): **bajarildi** — `workforce.py`, uchta **read**
   tool. HR allaqachon Sheets’da, ya’ni yangi adapter kerak bo‘lmadi. Xodimni
   **baholamaydi**: fakt va ism ko‘rsatadi, hukmni menejerga beradi. 32 yangi test.
4. `supervisor_router` (P10b): **bajarildi** — `supervisor.py`. Rahbar savoli
   bo‘lim agentiga yo‘naltiriladi; maqsad **o‘z** policy’si bilan ishlaydi,
   supervisor vakolati **meros qilinmaydi** (probe bilan o‘lchandi). Zanjir
   `max_hops` + router-rad + "tool emas" qoidalari bilan chegaralangan. 53 test.
   Ko‘p hop zanjiri, statistika endpointi va UI qoldi.
5. `asset_model` (P11): **bajarildi** — `assets.py`, beshta **read** tool
   (`asset.levels/tree/children/descendants/resolve`). Yo‘l = identitet;
   ierarxiyani operator e’lon qiladi, kod uni tekshiradi; **yozish yo‘li yo‘q**.
   Eng nozik qismi — segment bo‘yicha solishtirish (`zavod-1` avlodi `zavod-10`
   emas). Yo‘lning ikki qoidasi ikki funksiyaga ajratildi
   (`parse_path` identitet / `prefix_path` navigatsiya). Graf preflight’idagi
   Sheets ulanish nuqsoni tuzatildi. 56 test + 2 regressiya.
6. `vision_events` (P11b): **bajarildi** — `vision.py`, uchta **read** tool
   (`vision.station_event` / `person_event` / `summary`). **Frame hech qachon
   platformaga kelmaydi** (faqat hodisa) — va bu **huquqiy majburiyat**
   (O‘RQ-1125: biometrik ma’lumot lokal + davlat reestri). Tool qatlami ikki
   sinfga bo‘lingan, va `person_event` **biometrik, `human_led` majburiy**;
   darvoza provayder I/O sidan **oldin** ishlaydi (probe bilan o‘lchandi: 0 GET).
   Sinflar lug‘ati **tenant bo‘yicha**, reestr bo‘yicha emas. 48 test + probe.
   Ochiq: webhook qabul nuqtasi, hodisa dedup’i, UI. **Ochiq topilma:**
   `sheets.py` bo‘sh `allowed_connections` ni boshqacha o‘qiydi (V05G §4.2).
7. `document_intake` (P8b): **bajarildi** — `documents.py`, beshta tool
   (`document.parse` write, `document.match` / `duplicates` / `fraud_signals`
   read, `document.posting_plan` write). **Platforma pul harakatlantirmaydi** —
   to‘lov qadami umuman yo‘q, eng uzoq nuqta buxgalterga ko‘rsatiladigan reja.
   Uchta nuqson ushlandi, eng muhimi: tool’lar sxemadagi bare
   `{'type': 'object'}` tufayli engine darvozasidan **umuman o‘ta olmasdi**
   (jimgina — registratsiya testi tutmaydi, probe tutdi). 84 test + probe.
   Hisobot: `V05-BLOCKS-MESSAGING-UZ.md`.
8. `whatsapp_inbound_ingest` (P8c): **bajarildi** — `whatsapp_inbound.py`,
   ikkita **read** tool (`whatsapp.webhook` / `whatsapp.verify`). Imzo **RAW
   baytlar** ustida, **parse qilinishidan oldin**, **constant-time**; status
   callback va notanish raqam **sababi bilan tashlanadi** (Meta non-2xx ni qayta
   yuboradi). **Kiruvchi qabul tool emas** — model webhook qabul qila olsa,
   yozmagan mijoz uchun 24 soatlik oyna ochardi. 70 test + probe.
   Hisobot: `V05-BLOCKS-MESSAGING-UZ.md`.
   **Shu blokda adversarial audit o‘tkazildi** (o‘sha hisobot §2): uchta jiddiy
   nuqson topildi va tuzatildi — (a) `_parse_timestamp` **standart kasr soniyali
   ISO formatni rad etardi**, ya’ni oyna yopiq o‘qilib, hozir yozgan mijozga
   javob berish rad etilardi; (b) P8 test fixture’lari **durotka o‘rnatilgan**
   edi, shuning uchun to‘plam 24 soatdan keyin **chirib** yiqilardi; (c)
   `window_until` yoziladi lekin **o‘qilmaydi** — `OUTBOUND_CHANNELS` da
   `whatsapp` yo‘q, ya’ni kiruvchi oqim chiqish darvozasiga **ulanmagan**
   (qayd etildi, ochiq qoldirildi: pretsedent qarori talab qiladi).
9. `wa_window_until` grafiya atributi (P8L, PRD §2.8): **tekshirildi va RAD
    ETILDI** — hech narsa qurilmadi, va bu **ataylab**. Sabab **mexanik**, didnan
    emas: grafda **atribut — bu provider qatorining ustuni**, chunki `_collect`
    har bir `map` elementi uchun `row.get(field)` o‘qiydi. Ya’ni `customer`
    entity’siga `wa_window_until` qo‘shish «oyna **inson qo‘lda yozadigan**
    katakda yashaydi» degani bo‘lardi — va katak mijoz yozganda o‘zi
    yangilanmaydi. Bu P8c da chiqqan va P8d da yopilgan nuqsonning **aynan o‘zi**,
    bir qatlam pastda: platforma **o‘zi tasdiqlagan** faktni (Meta imzolagan
    xabar) **qo‘lda yuritilgan** fakt bilan **almashtirardi**.
    `observed` dan hisoblash ham rad etildi: `observed` — **bizning o‘qish
    vaqtimiz**, provider’ning yangilanish vaqti emas; qoralama va yuborish
    **ikki xil surat** o‘qib, bitta mijoz haqida **kelishib qolmasdi**.
    `whatsapp.window` ni graf manbasi qilish ham rad etildi: `SAFE_SOURCE_TOOLS`
    to‘rtta **qator qaytaruvchi** tool bilan cheklangan, `whatsapp.window` esa
    `entity_id` dan emas, **tenant konfiguratsiyasidan** javob beradi — id ni
    kontaktga bog‘lash **ikkinchi deklaratsiyani** talab qilardi; bundan tashqari
    uning chiqishi qator ro‘yxati emas, va bitta savol uchun **uchta ruxsat**
    kerak bo‘lardi.
    **PRD ning maqsadi bajarilgan:** `whatsapp.window` `open`, `closes_at` va
    **`source`** (`event`/`register`) qaytaradi — va bu **o‘sha** resolver,
    `send` darvozasi provayder I/O sidan **oldin** chaqiradigani.
    Atribut o‘rniga **qo‘riqchi testlar** yozildi (6 metod → 52 to‘plam testi): ular kelajakda kimdir uni
    qo‘shsa **yiqiladi**, shunda xato **jimgina** qaytolmaydi.
    Hisobot: `V05-BLOCKS-MESSAGING-UZ.md`. Probe:
    `probe_wa_window_graph_attribute.py` (`PROVEN`). **Yangi tool yo‘q.**
10. **ERP sinxronizatsiyasi** (P8b ning davomi, P8e): **bajarildi** —
    `platform_runtime/erp.py`. `mock ERP post` endi haqiqiy: uchta tool
    (`erp.posting_prepare` read, `erp.posting_submit` write,
    `erp.posting_status` read), ikkala driver (`onec_http` **va** `custom_http`),
    va **pul harakatlanmaydi** — posting **qarz** yozadi, to‘lash ERP ichida
    **inson ishi** bo‘lib qoladi. To‘liq bo‘lmagan hujjat **nomi bilan** rad
    etiladi, **hech qachon** to‘ldirilmaydi; `account` va `counterparty`
    operator registridan **alias** sifatida keladi va hujjatdan **o‘qilmaydi**.
    Ko‘pi bilan bir marta yoziladi — **to‘rt qavat** (ERP qidiruvi, I/O dan
    oldingi lokal ledger, `UNIQUE` `claim_key` indeksi, va qaytariladigan
    muvaffaqiyatsizlik). 54 test (`test_erp.py`),
    `config/erp_posting.example.json`, `scripts/check_erp_example.py`, va
    `probe_erp_posting_boundary.py` (`PROVEN`). Registr **69 → 72**.
    Hisobot: `V05-BLOCKS-OPS-UZ.md`.
    Qolgani: **live ERP acceptance** (shuning uchun `production_release` hali
    ham `NO_GO`); OCR/ajratish qatlami hamon bu blokdan **tashqarida** va
    ataylab shunday — bu blok PDF/OCR kutubxonasini o‘z ichiga olmaydi, maydonlar
    allaqachon ajratilgan holda keladi.
11. **WhatsApp oynasini kiruvchi hodisalardan o‘qish** (P8c ning davomi, P8d):
    **bajarildi** — `whatsapp.py` da `_window_from_events` va `window_sources`.
    Auditning 1-topilmasi shu bilan **yopildi**: endi `whatsapp_inbound` yozgan
    `window_until` **haqiqatan o‘qiladi**. Pretsedent qarori **aniq** qabul
    qilindi: **tasdiqlangan kiruvchi hodisa operator jadvalidan ustun** — yangi
    emasi emas, balki **hodisa**. `window()` chiqishiga `source`
    (`event`/`register`) maydoni qo‘shildi, ya’ni javob **qayerdan** kelganini
    ko‘rsatadi. `whatsapp` **ataylab** `OUTBOUND_CHANNELS` ga qo‘shilmadi:
    (a) oyna va manzil **ikki xil savol**; (b) qo‘shish `whatsapp.send` ni
    allowlist shoxobchasidan hodisa-bog‘lash shoxobchasiga **ko‘chiradi** —
    bu ruxsat qo‘shish emas, **qoida almashtirish**; (c) oynadan tashqarida
    qonuniy yuboriladigan **shablonlar** bog‘lanadigan hodisasiz qolardi.
    65 yangi test (test_whatsapp.py 54 → **119**) + `probe_whatsapp_window_sources.py`
    (`PROVEN`). Hisobot: `V05-BLOCKS-MESSAGING-UZ.md`.
    Qolgani: PRD §2.8 talab qilgan `wa_window_until` atributi Business Graph’ning
    `customer` entity’siga **hali qo‘shilmagan** — va P8L uni **ataylab** rad
    etdi (sababi: `V05-BLOCKS-MESSAGING-UZ.md`).
11. `task_oversight_escalation` (P6): `workload` ning `overdue` chiqishini
   menejerga xabar qilish — hozir fakt bor, eskalatsiya yo‘q.
12. `reengagement_ui_and_live_acceptance`: dashboard ekrani (policy forma + ledger
   jadvali), hot-lead eskalatsiya (menejerga Telegram alert), ledger retention.
13. Bitrix24/Kommo uchun stalled feed; Kommo'da `crm.deal.create` modelini tekshirish.
14. Google katta history batchlarini durable staging orqali bo‘lib ishlash, scheduler,
    webhook/dedup va quota backoff.
15. Identity MFA/recovery/OIDC, knowledge parser/embedding/grounding.
16. **`sheets.py` `allowed_connections` nomuvofiqligini yopish** (V05G §4.2):
    bo‘sh ro‘yxat bir yo‘lda «hammasi ruxsat», boshqasida «hech narsa» —
    yagona qoidaga keltirilishi kerak.17. Billing/voice, Mac/Windows native runner, UI dependency upgrade/build, production
    operations. Windows uchun alohida ownership modeli kerak: hozirgi local-executor
    xavfsizlik kontrakti POSIX-only (`O_NOFOLLOW`, `mkfifo`, 0600 single-owner).

**v0.5o da qayta yurgizildi:** `Ran 1855 tests` — **failures=1, errors=149,
skipped=1**, ya’ni baseline **sakkizinchi marta aynan o‘zgarmadi** (1791 → 1855 =
+64 test, P12). `test_manufacturing.py` da 64 test, uchta read tool registrda
(`manufacturing.bom` / `manufacturing.cycle` / `manufacturing.yield`,
`build_registry()` = 77), va `probe_manufacturing_boundary.py` **22 o‘lchangan
xossa** bo‘yicha oltita xavfsizlik xususiyatini tekshiradi.

P12 ning butun mavzusi — **rad etish**, va u **kodda** majburlangan. PRD §8
Jidoka kameralarining ERP‘ga uzatadigan to‘rt narsasini sanaydi. Uchtasi sondan
yasaladi (cycle time, yield, SOP deviation hodisalari soni); **to‘rtinchisi —
OEE — ataylab rad etiladi**: availability va performance uchun rejalashtirilgan
ish vaqti va ideal sikl vaqti kerak, ular esa bu registrlarda **yo‘q**.
`vision.summary` buni allaqachon rad etgan edi ("bu P13 ishi") va bu blok
**o‘sha rad etishni saqlaydi** — mavjud bo‘lgan narsadan availability raqamini
o‘ylab topish o‘rniga. Ya’ni `manufacturing` OEE egasi **emas**; P13 egasi.

P12 da **probe ikkita haqiqiy nuqson tutdi**, va ikkalasi ham modulning **o‘z
docstring‘iga zid** edi — P8d va P6 bilan aynan bir sinf:

- `yield_report` da `setdefault` **o‘qiladiganlik tekshiruvidan oldin** ishlardi.
  Bo‘sh chiqim katagi `''` → `_number('')` = `None`, lekin guard `''.strip()` ni
  bo‘sh deb **ishlamaydi**, natijada **uydirma `output: 0.0` stansiya**
  yaratilardi. Menejer buni "liniya hech narsa ishlab chiqarmadi" deb o‘qirdi —
  aynan modul rad etish uchun qurilgan uydirma. Tuzatish: `output is None` bo‘lsa
  yozuv **umuman yaratilmaydi**.
- Nuqson tarmog‘ida `continue` **yozuv yaratilgandan keyin** turardi, ya’ni bir
  katakdagi matn butun qatorning **o‘qilgan chiqimini** summasidan chiqarib
  tashlardi. Tuzatish: o‘qilmaydigan nuqson katagi chiqimni **yo‘qotmaydi**, u
  stansiyani `defect_unreadable` deb belgilab **butun stansiyani hisoblanmaydigan**
  qiladi — chunki o‘qiladigan kataklarni yig‘ib, o‘qilmaydiganini e‘tiborsiz
  qoldirish nuqsonni **kam** ko‘rsatib, chiqimni **shishirardi**.

Uchtasi qo‘shimcha ravishda test bilan qo‘yilgan: bo‘sh chiqim qatori stansiya
yaratmaydi, lekin **deklaratsiya qilingan 0 haqiqiy 0 bo‘lib qoladi** — aks holda
tuzatish teskari nuqsonga aylanardi.

**v0.5p da qayta yurgizildi:** `Ran 1934 tests` — **failures=1, errors=149,
skipped=1**, ya’ni baseline **to‘qqizinchi marta aynan o‘zgarmadi** (1855 → 1934 =
+63 test, P13). `test_oee.py` da 63 test, ikkita read tool registrda
(`oee.report` / `oee.andon`, `build_registry()` = 79), va
`probe_oee_boundary.py` **78 o‘lchangan xossa** bo‘yicha yettita bo‘limni
tekshiradi.

P13 — **P12 topshirig‘ining bajarilishi**. PRD §8 Jidoka kamerasining birinchi
elementi: *"OEE availability and performance metrics per line and shift"*. P12
buni **nomlab** rad etgan edi ("availability va performance uchun rejalashtirilgan
ish vaqti va ideal sikl vaqti kerak, ular bu registrlarda yo‘q"), va bu blok
o‘sha topshiriqni bajaradi — lekin **faqat mijoz deklaratsiya qilgan me’yorlar
bo‘yicha**:

    availability = ish / reja
    performance  = (ideal * chiqim) / ish
    quality      = yaroqli / chiqim
    OEE          = availability * performance * quality

uchunchi muhim qoida: **qisman OEE raqam sifatida ko‘rsatilmaydi**. Agar birorta
faktor hisoblanmasa — `oee: null`, va `not_computable` aynan qaysi kirish
**yo‘qligini** (`absent`) yoki **ishlatib bo‘lmasligini** (`unusable`) nomlaydi.
Yarmi o‘lchangan, yarmi standart qiymat bilan to‘ldirilgan indeks — bu P12
sarlavhasi ogohlantirgan "ertami-kechmi xato bo‘ladigan va hech qachon
so‘ralmaydigan son", va u **to‘liq ko‘rinadi**, shuning uchun yo‘qligidan
**yomonroq**.

Ikki narsa ataylab **qisqartirilmaydi**: `run > plan` bo‘lganda availability
100% ga **qisqartirilmaydi** (bu rejalashtirish nuqsoni), va liniya me’yordan
sekin ishlaganda performance 1.0 ga **ko‘tarilmaydi** (performance — nisbat emas,
**tezlik**; 1.0 dan past bo‘lish normal holat).

**Andon — faqat aniqlash yarmi.** `oee.andon` qaysi stansiya qaysi deklaratsiya
qilingan chegarani kesib o‘tganini **o‘lchangan qiymat** va **chegara** bilan
qaytaradi — **fakt, hukm emas**; yetkazish **P6 eskalatsiya koordinatoridan
qayta ishlatiladi**, ikkinchi xabar yo‘li o‘stirilmaydi. Chegara — operator
konfiguratsiyasi, shuning uchun platforma 85% ni "yomon" deb **hal qilmaydi**;
chegara deklaratsiya qilinmasa `andon` **nomlab rad etadi**. OEE‘si
hisoblanmaydigan stansiya **signal ko‘tarmaydi** — `not_evaluated` bo‘ladi, chunki
yo‘q ustun nomi menejerni ishlamayotgan liniya haqida ogohlantirmasligi kerak.

P13 da **uchta nuqson** topildi, va bu safar yangi shaklda — **bittasi kodda
emas, hujjatda** edi:

- `_REGISTER_KEYS` da `planned_run_seconds` **yo‘q** edi — ya’ni konstanta
  funksiyasi umuman **yetib bo‘mas** edi. Qo‘shildi.
- `good > produced` → `quality = 4.1662`, OEE **3.4719** ya’ni **347%**.
  `_factor_quality` endi buni **rad etadi**, va `good_exceeds_produced`
  hisoblagichi orqali **ko‘rinadigan** qiladi.
- **Hujjat kodga zid edi.** Modul sarlavhasi ikkala reja manbasi (ustun +
  konstanta) deklaratsiya qilinsa **rad etilishini** da’vo qilardi. O‘lchov
  buning **aksini** ko‘rsatdi: **ustun ustunlik qiladi**, konstanta esa **bo‘sh
  katak uchun zaxira**; matnli katak esa hech qaysi manbani ishlatmaydi. Bu
  mantiqiy dizayn ("har satr qiymati + stansiya standarti") — lekin hujjat
  uning **aksini** aytardi, va men **o‘zim yozgan** config misoli, checker va
  testlar ham o‘sha noto‘g‘ri da’voni kodlagan edi. Kodni "tuzatish" noto‘g‘ri
  bo‘lardi; **hujjat** tuzatildi, checker endi **qabul qilinishini** o‘lchaydi,
  va uchta yangi test qo‘shildi. Rad etish chegarasi **aniq**: **ikkita registr**
  bir o‘qish uchun deklaratsiya qilinganda.

Ya’ni "docstring — dalil emas" qoidasi **ikki tomonlama** ishlaydi: kodni
hujjatga ishonib qabul qilish ham, hujjatni kodga ishonib qabul qilish ham xato.
Faqat **o‘lchov** haqiqatni aytadi. **T4 shu bilan tugadi**; keyingi blok —
**P14 `telephony_outbound`**.

To‘liq holat `BACKLOG.json` da. Joriy paketni tayyor mahsulot deb nomlamaslik kerak.

## V05Q — Qo'ng'iroq hodisalari va rozilik darvozasi (P14 `telephony_outbound`, A bosqich)

**Sana:** 2026-09-20. **Holat:** QURILDI VA O'LCHANDI (lokal kontrakt).

P14 — PRD yuzasining **oxirgi** qismi, va u ataylab oxirgi: og'ir, huquqiy
jihatdan ochiq (yozib olishga rozilik va saqlash — **operator** majburiyati,
PRD-04 VO-07) va sifatga sezgir (o'zbek shevasi + ruscha aralash STT ni buzadi).
Shuning uchun bu bosqichda **dialer yozilmadi** — keyingi har qanday dialer
chetlab o'tolmaydigan **qism** yozildi: **hodisa o'quvchi + rozilik darvozasi**.

**Uchta yangi read tool** (`telephony.consent`, `telephony.call_events`,
`telephony.summary`); `build_registry()` **79 → 82**.

- **Audio hech qachon platformaga yetib kelmaydi** — **strukturaviy**, va'da
  emas: hech qanday tool argumenti / config kaliti / chiqish maydoni yozuvni,
  havolani, transkriptni yoki buferni tashimaydi; modul **audio yuzasini import
  ham qilmaydi**.
- **Qo'ng'iroq qilish — tool emas, ataylab.** Rozilik darvozasi kodda,
  provayderga yeta oladigan **yagona** yo'lda majburlanishi kerak — promptda
  emas. Shuning uchun `telephony.consent` — **savol** beradigan tool: noma'lum
  raqam **istisno emas, sabab bilan `consented: false`**; **buzuq** yozuv esa
  configure vaqtida **raise** qiladi (xato "granted" deb o'qilmasligi kerak).
- **Maqsad yopiq lug'at va registr bo'yicha** (`service/delivery/payment/
  support/marketing`), ustundan **o'qilmaydi**: o'zini qayta belgilay oladigan
  qator marketingni service qilib darvozadan o'tib ketardi.
- **Raqam — bitta raqam**: `+998 90 123 45 67` = `998-90-123-45-67`, lekin
  `901234567` ≠ `998901234567` — **prefiks mos emas** (P11 ning segment qoidasi,
  bu yerda raqamga).
- **Yozib olish — alohida rozilik**: yo'qligi `recording_allowed: false`, va u
  **"yozuv yo'q" degani emas** — platforma yozuv borligini bilmaydi.

**Probe bir nuqson topdi va u kodda tuzatildi.** Modul `outcome_column` ni
"shaffof matn" deb o'qirdi — bu bilan "audio yetib kelmaydi" chegarasini
**operator ustun nomini tanlashiga** bog'lab qo'ygan edi. Registr media ustunini
tashisa, **yozuv URL i `outcome` sifatida qayta chop etilardi**. Tuzatildi:
**lokator shaklidagi katak** (sxemali URL yoki media kengaytmasi) **ushlanadi**
va `withheld_outcomes` da **sanaladi**; filtr **tor**, oddiy natija so'zi
o'zgarmaydi. Bu — *"docstring — dalil emas"* qoidasining **beshinchi**
tasdiqlanishi, va eng qimmat joyda: **huquqiy** chegarada.

**Regressiya:** avvalgi P12/P13 ultra-auditida **5 nuqson** topilib tuzatildi
(ikki `_number` non-finite teshigi, kritik `andon` ko'r nuqtasi, yopiq
`not_evaluated`, latent BOM off-by-one) — probe'lar **78** (OEE) va **32**
(ishlab chiqarish) xossaga chiqdi, test_oee **73**, test_manufacturing **70**.

**O'lchangan:** `test_telephony.py` — **54 test**;
`probe_telephony_consent.py` — **7 bo'lim, 63 o'lchangan xossa, hammasi PASS**;
`config/telephony.example.json` + `check_telephony_example.py` (12 PASS);
`ops.telephony` agenti (`registry tools: 82`, `unknown: []`).

**Baseline (o'n birinchi marta aynan):** **1988 test**,
`failures=1, errors=149, skipped=1` — **o'zgarmadi** (yagona failure —
Windows-only `test_all_files_private`).

**Hujjatlar:** `V05-BLOCKS-OPS-UZ.md` (yangi, 10 bo'lim),
`AUDIT-CORRECTIONS-UZ.md` (P14 A auditi), `BACKLOG.json`
(`telephony_outbound` → `STAGE_A_LOCAL_CONTRACT_TESTED`, `offline_tests` **1988**,
claim "P1-P13 + P14 stage A", `next_source_block`: B bosqich),
`README.md` (1988), shu fayl.

**Keyingi:** **B bosqich** — chiqish navbati + throughput, saqlash muddati
siyohati va rad etish chegarasi. **C bosqich** — eskalatsiya yetkazish **P6
koordinatori orqali** (ikkinchi bildirishnoma yo'li **ochilmaydi**). Audio
saqlash **hech qachon**, hech qanday bosqichda va hech qanday sozlamada.
`production_release` **NO_GO** bo'lib qoladi (live acceptance yo'q).

## V05R — Chiqish navbati, tezlik shifti va saqlash oynasi (P14 `telephony_outbound`, B bosqich)

**Sana:** 2026-09-20. **Holat:** QURILDI VA O'LCHANDI (lokal kontrakt).

A bosqich uchta savolga javob berdi: **kimga**, **qaysi maqsad uchun**,
**yozib olish mumkinmi**. B bosqich kampaniya uchun qolgan uchtasini qo'shadi:
**kim qo'ng'iroq qilinishi mumkin** (navbat), **qanchalik tez** (sur'at) va
**yozuv qancha yashaydi** (saqlash). Va **hech narsa ochmaydi**: yangi
ma'lumot yo'li yo'q, transport yo'q, dialer yo'q.

**Ikkita yangi read tool** (`telephony.queue`, `telephony.retention`);
`build_registry()` **82 → 84**.

- **Navbat — "nima qilish mumkin", "nima qilindi" emas.** Faqat `outbound`
  qatorlar. **Kiruvchi qo'ng'iroq hech qachon navbat elementi emas**: biz uni
  qilishni tanlamaganmiz, demak rejalashtiradigan narsa ham, hurmat qilinadigan
  sur'at ham yo'q. **Roziliksiz qator ko'rsatiladi** (`consented: false` +
  sabab) — yashirish operatorga "ro'yxat toza" degan **yolg'on ishonch**
  berardi.
- **Tezlik shifti — IJRO ETILADI, e'lon qilinmaydi.** Sur'atdan ortiq
  chaqiriladigan qatorlar `over_capacity` da **sanaladi** va chaqiriladigan deb
  **ko'rsatilmaydi**. Sabab: *haddan ortiq ko'rsatilgan navbat qisqasidan
  yomonroq* — qisqasining davosi **kutish**, haddan ortiqning davosi **yo'q**.
- **Sur'at e'lon qilinmasa — sig'im NOL.** Sur'atini hal qilmagan operator
  qo'ng'iroq qilishni ham hal qilmagan. `limit` **elementni** cheklaydi,
  **baholashni emas** — `matched` va `over_capacity` **butun oynani**
  tasvirlaydi, aks holda chaqiruvchi **noto'g'ri raqamga** qarab sur'at sozlardi.
- **Saqlash — hisobot, harakat emas.** Platforma qo'ng'iroq yozuvini **hech
  qachon o'zi o'chirmaydi** (`deleted` doim `0`): qaror ham, amal ham huquqiy
  asosni ko'rsata oladigan **operator** bilan qoladi. Har qator **aynan bitta**
  holatda: `in_window` / `past_window` / `unaged` — va **`unaged` uchinchi
  holat**, ikkisiga qo'shilmaydi, chunki aks holda **sana xato yozilgani uchun**
  yozuv yo'q qilinardi.
- **Saqlash majburiy (VO-07).** Consent registri e'lon qilgan tenant demak
  shaxsiy ma'lumot saqlaydi; oynasiz — VO-07 taqiqlagan tanlov **jimgina**
  qilingan. Shu sababli `retention_days` **configure vaqtida** rad etiladi va
  **cheklangan** bo'lishi shart ("abadiy" — siyosat emas, siyosat **yo'qligi**).

**Audit bitta haqiqiy nuqson topdi — yashirin O(N) kuchaytirish.** `queue()`
har bir chiqish qatori uchun `consent()` chaqirardi, `consent()` esa rozilik
registrini **qaytadan** o'qirdi: 50 qatorli navbat = **50 marta** provayder GET.
U **to'g'ri natija berardi va testdan o'tardi**; probe **hop sonini** o'lchagani
uchun ushladi. Tuzatish: **ichki `_index` tikuv** — bir marta o'qilgan indeks
`consent()` ga uzatiladi, tool yuzasida **yo'q**, shuning uchun bitta raqam
haqidagi bitta savol avvalgidek yangi o'qish oladi. Probe endi **12 qatorli ham,
60 qatorli ham 2 hop** sarflashini o'lchaydi. Bu — *"docstring — dalil emas"*
qoidasining **oltinchi** tasdiqlanishi, va bu safar **unumdorlik** chegarasida.

**O'lchangan:** `test_telephony.py` — **137 test** (A ning 57 tasi meros +
24 yangi; meros **ataylab**, chunki B A ning invariantini buzsaydi, A testi
yiqiladi); `probe_telephony_consent.py` — **8 bo'lim, 98 o'lchangan xossa**
(69 → 98, +29), hammasi PASS; `check_telephony_example.py` **22 PASS** (12 → 22);
`ops.telephony` agenti 5 tool bilan (`registry tools: 84`, `unknown: []`).

**Baseline (o'n uchinchi marta aynan):** **2071 test**,
`failures=1, errors=149, skipped=1` — **o'zgarmadi** (yagona failure —
Windows-only `test_all_files_private`).

**Hujjatlar:** `V05-BLOCKS-OPS-UZ.md` (yangi, 8 bo'lim),
`AUDIT-CORRECTIONS-UZ.md` (P14 B auditi), `BACKLOG.json`
(`telephony_outbound` → `STAGE_B_LOCAL_CONTRACT_TESTED`, `offline_tests` **2071**,
claim "P1-P13 + P14 stages A and B", `next_source_block`: C bosqich),
`README.md` (2071), shu fayl.

**Keyingi:** **C bosqich** — eskalatsiya yetkazish **P6 koordinatori orqali**
(ikkinchi bildirishnoma yo'li **ochilmaydi**). B da ataylab **ochilmagan**:
haqiqiy dialer, SIP, codec, concurrent slot boshqaruvi, provayder rate-limit
retry, yozuvni haqiqatda o'chirish (operator amali) va VO-06 budget
reservation'ni `usage_budget` ga ulash. Audio saqlash **hech qachon**, hech
qanday bosqichda va hech qanday sozlamada. `production_release` **NO_GO** bo'lib
qoladi (live acceptance yo'q).

## V05S — Eskalatsiya yetkazish P6 koordinatori orqali (P14 `telephony_outbound`, C bosqich)

**Sana:** 2026-09-20. **Holat:** QURILDI VA O'LCHANDI (lokal kontrakt).

C bosqichning butun maqsadi — **ikkinchi bildirishnoma yo'lini ochmaslik**.
B navbatni berdi; tabiiy keyingi qadam "navbat tayyor bo'lganda menejerga xabar
berish" edi, va eng oson (va eng xato) yo'l — telephony moduliga o'z
`telegram.send` chaqiruvini qo'shish. Platformada **allaqachon** koordinator bor
(P6), u bitta qiyin savolga javob beradi: *"bu allaqachon xabar qilinganmi?"* —
ledger, dedup, cooldown va compare-and-set bilan. Ikkinchi yuboruvchi — bu
**o'sha savolga ikkinchi javob**, ya'ni **o'chirishni unutib qoldiradigan
ikkinchi narsa**.

**Yangi tool YO'Q, yangi yuboruvchi YO'Q.** Koordinator **manba tanlovchi**
oldi: `source` = `workforce` (default) yoki `telephony`. Ikkalasi ham **o'sha**
`telegram.send`, **o'sha** `p_escalation` ledger, **o'sha** dedup va **o'sha**
cooldown bilan ishlaydi. `DELIVERY_TOOLS` **o'zgarmadi** — hamon faqat
`{telegram.send}`.

**Bu strukturaviy o'lchanadi, aytilmaydi:** probe `DELIVERY_TOOLS` ni tekshiradi
va modul **hech qanday** `notify/deliver/send/post/publish/push/alert` oshkora
yuzani eksport qilmasligini o'lchaydi — ikkinchi yuboruvchi **yo'q**.

- **Digest haddan ortiq da'vo qilmaydi.** Navbat `blocked_count` va
  `over_capacity` ni aytadi; eskalatsiya ham ularni aytishi shart, aks holda
  menejer "12 ta tayyor" deb o'qib, "10 tasi rad etilgan" ni bilmaydi. Faqat
  rozilik darvozasidan **o'tgan** qatorlar element bo'ladi.
- **Staleness manbaga bog'liq.** `max_age_days` kechikkan **ishni** chiqaradi.
  **Raqamga qo'llanilsa** — qonuniy qo'ng'iroqni jimgina bekor qilardi, va
  "eski" qo'ng'iroqning qonuniyligini to'xtatmaydi. Shuning uchun qoida **faqat
  `workforce`** uchun; probe **ikki tomonni ham** o'lchaydi (sizib o'tmagan).
- **Dedup identifikatori — raqam va maqsad, hech qachon shaxs.** Navbat raqam
  tashiydi; element qatorida operator nomlansa, eskalatsiya **ko'rsatkich
  hisobotiga** aylanardi.

**O'lchangan:** `test_escalation.py` **45 → 106** (61 meros + 16 yangi;
`EscalationTelephonySourceTests` **ataylab** ota-sinfdan meros oladi — parallel
yuboruvchi kiritilsa **ota-sinfning** yetkazish testlari yiqiladi);
`probe_escalation_boundary.py` **21 → 36** o'lchangan xossa (7–9-bo'limlar),
hammasi PASS; `check_escalation_example.py` ikkala manbani o'rgatadi va
qamrovni tekshiradi; `ops.telephony` agenti `telegram.send` +
`allowed_recipients` oldi.

**Baseline (o'n to'rtinchi marta aynan):** **2132 test**,
`failures=1, errors=149, skipped=1` — **o'zgarmadi** (yagona failure —
Windows-only `test_all_files_private`).

**P14 YAKUNI:** A (hodisalar + rozilik), B (navbat + sur'at + saqlash),
C (eskalatsiya P6 orqali) — **uchalasi ham LOCAL_CONTRACT_TESTED**. Uch
bosqichda ham **audio saqlanmadi, dialer yozilmadi va ikkinchi bildirishnoma
yo'li ochilmadi**.

**Hujjatlar:** `V05-BLOCKS-OPS-UZ.md` (yangi, 9 bo'lim),
`AUDIT-CORRECTIONS-UZ.md`, `BACKLOG.json` (`STAGE_C_LOCAL_CONTRACT_TESTED`,
`offline_tests` **2132**, claim "P1-P13 + P14 stages A, B and C"),
`README.md` (2132), `config/escalation.example.yaml`, shu fayl.

**Keyingi (P14 ochiq qolgan, ataylab):** haqiqiy dialer, SIP, codec, concurrent
slot boshqaruvi, provayder rate-limit retry, yozuvni haqiqatda o'chirish
(operator amali), VO-06 budget reservation'ni `usage_budget` ga ulash, STT/TTS
live sifat (**NOT_RUN** — real provayder audiosi kerak) va live provayder
acceptance. Audio saqlash **hech qachon**. `production_release` **NO_GO**.

## V05T — OAuth chegaralari: **beshta rad etish bitta xabarga aylangan** (§148)

**Sana:** 2026-09-21. **Holat:** QURILDI VA O'LCHANDI (lokal kontrakt).

Auditning qirq uchinchi fazasi. **Yangi qobiliyat yo'q** — mavjud `oauth.py`
(378 satr, 49 test) chegaralari o'lchandi va bitta haqiqiy nuqson tuzatildi.

**Nega bu modul tanlandi:** skan paytida e'tibor tortdi — modulda **birorta ham
nomlangan konstanta yo'q**, lekin **to'qqizta** sonli chegara bor, hammasi
**inline literal**. Ya'ni "konstanta yo'q" degani "chegara yo'q" degani emas;
ularni qo'lda sanash kerak bo'ldi.

### O'lchangan nuqson — beshta **butunlay boshqa** rad etish bitta umumiy xabarga aylanardi

`complete()` da `_tokens()` **keng `except Exception`** ichida chaqiriladi va
**hamma narsani** `OAuthError('Authorization outcome unavailable; authorize
again')` ga o'raydi. `OAuthError` — `RuntimeError` avlodi, ya'ni **modulning
o'z xatosi ham o'sha handler'ga tushadi**.

| Provayder javobi | Operator ko'rgan xabar |
|---|---|
| `expires_in = 59` | Authorization outcome **unavailable** |
| `expires_in = 86401` | Authorization outcome **unavailable** |
| `expires_in = "3600"` | Authorization outcome **unavailable** |
| `scope` = 10 001 belgi | Authorization outcome **unavailable** |
| `access_token` = 16 001 belgi | Authorization outcome **unavailable** |

Ya'ni **javob to'liq o'qilgan va tushunilgan**, lekin operator **tarmoq
nosozligini** qidirishga yuboriladi va auditga `uncertain` yoziladi.

**Tuzatish:** (1) `_tokens` ichida `credential()` yordamchisi — `bounded` ning
`ValueError` ini `OAuthError` ga o'giradi, ya'ni metod shartnomasi bir xil
bo'ladi; (2) `complete`/`access` da `except (OAuthError, Forbidden)` shoxi **keng
handler'dan oldin** — sabab saqlanadi. `Forbidden` bu yerda **provayder
tomonidagi** rad etish, shuning uchun chaqiruvchi shartnomasiga (`OAuthError`)
o'tkaziladi, lekin **matni saqlanadi**.

**Holat mashinasi o'zgarmadi:** `_failure(..., 'uncertain')` va
`_cleanup_issued(...)` baribir ishlaydi — **ishlatib bo'lmaydigan credential
baribir bekor qilinishi kerak**. Faqat **sabab** tiklandi. Tuzatishdan keyin
**5 xil xabar** (7 rad etishdan; uchta expiry varianti bitta to'g'ri xabarni
bo'lishadi).

### Qaytarish matritsasi — **9/9 yashil → 9/9 qizil**

Yangi qayta ishlatiladigan asbob: `scripts/probes/revert_matrix.py`; faza skripti
`scripts/probes/audit_oauth_bounds.py`. Nazorat yashil, keyin **to'qqizta mutatsiya
hammasi yashil** — ya'ni **49 test butun oqimni uchidan-uchiga yurardi va
bittasi ham chegarani o'lchamagan edi**. Oqim testi oqim ishlashini isbotlaydi;
u shift **256** ekanini **257** dan ajratmaydi. Tuzatishdan keyin **9/9 qizil**,
tiklash tasdiqlangan.

**Qo'shilgan:** `test_oauth.py` da `DeclaredBoundTests` — **8 sinov** (49 → 57),
har biri literalni **aniq satr** bilan qadaydi **va** chegarani **ikki tomondan**
yuradi. Sinf **`OAuthTests` dan meros olmaydi** — pastga qarang.
`scripts/probes/probe_oauth_boundaries.py` — **48 xossa, 48 pass**, 4 bo'lim.

### O'z xatolarim (yashirilmadi)

1. **Probe'da darvozalarga son berdim** (`bounded(256)`) — ular **satr** oladi.
   6 ta "qizil" chiqdi, ular **mening** xatolarim edi, modulning emas.
2. **Matritsani SIGTERM bilan o'ldirdim va bitta mutatsiya faylda qoldi**
   (`PKCE floor 43` → `42`). Keyingi yurish **mutatsiyani baseline deb o'qidi**
   va nazorat qizil bo'ldi — bu aynan skill yozgan tuzoq. Tuzatildi:
   `revert_matrix.py` endi **sidecar** saqlaydi, uzilgan yurishni **aniqlaydi**,
   va `atexit` + `SIGINT`/`SIGTERM` bilan tiklaydi. Mexanizm **sinovdan
   o'tkazildi** (soxta uzilish → aniqlandi → tiklandi).
3. **Meros — qayta ishlash emas, qayta yuritish.** Sinfni dastlab `OAuthTests`
   dan meros qildirdim: **43 ta oqim sinovi behuda ikkinchi marta** yurdi
   (49 → 101 test, +8 s), chunki sinf **fixture'ni o'zgartirmaydi**. Repo'dagi
   naqsh (`SupervisorBoundaryTests`, `SheetsBoundaryTests`) **fixture'ni
   o'zgartiradi**, shuning uchun meros o'sha yerda oqlanadi. Meros olib
   tashlandi: **57 test, 10.7 s**.

**Baseline (o'n beshinchi marta aynan):** **2792 test**,
`failures=1, errors=11, skipped=1`, **413 s** — imzo **o'zgarmadi** (yagona
failure — Windows-only `test_all_files_private`; 11 error — POSIX-only
xavfsizlik kontrakti). Test soni 2784 → 2792: **+8** (`DeclaredBoundTests`),
boshqa hech narsa qo'shilmadi yoki olib tashlanmadi.

**Hujjatlar:** `ULTRA-AUDIT-ASCII-CELL-UZ.md` §148 (yangi faza),
`BACKLOG.json` (`offline_tests` **2792**, claim'ga oauth chegara hukmi
qo'shildi), shu fayl.

**Keyingi nomzodlar (o'lchandi, modul × probe):** `tools.py` (303 satr,
**13 katta literal, 0 nomlangan konstanta**), `secret_vault.py` (99 satr,
6 literal), `app/` qatlami (40 modul / 4 479 satr), `agent_planner.py`,
`google_oauth.py`, `postgres_connector.py`, `speech.py`, `mcp.py`,
`model_transport.py`, `model_response.py`, `crm/crm_reconcile.py`.
`production_release` **NO_GO** bo'lib qoladi.

## V05U — `tools.py` chegaralari: **ikki sabab bitta xabar** va **yiqilgan qo'riqchi** (§149)

**Sana:** 2026-09-21. **Holat:** QURILDI VA O'LCHANDI (lokal kontrakt).

Auditning qirq to'rtinchi fazasi. **Yangi qobiliyat yo'q** — har bir tool
argumenti o'tadigan darvoza (`tools.py`) o'lchandi va **ikki haqiqiy nuqson**
tuzatildi.

**Nega bu modul:** skan mexanik edi — **13 ta katta sonli literal** va
**bitta ham nomlangan konstanta yo'q**, ya'ni chegaralar konstanta bo'yicha
qidiruvga **ko'rinmas**. Hammasi qo'lda sanaldi.

### Birinchi matritsa: **15 mutatsiyadan 10 tasi YASHIL**

Ya'ni **besh chegara** qadalgan, **o'ntasi yo'q**. Eng qimmati — `string()`
yordamchisi: bu **har bir tool maydonining** sukut chegarasi, va uni
kengaytirish **hamma** maydonni jimgina kengaytiradi — **hech narsa sezmadi**.

### Nuqson A — **ikki sabab, bitta xabar**

| Holat | Oldin | Keyin |
|---|---|---|
| nom takrorlangan | `Invalid tool registration` | `Tool already registered: x.y` |
| risk noma'lum | `Invalid tool registration` | `Unknown tool risk level: bogus` |

Bu — §148 dagi "beshta rad etish bitta xabar" naqshining **aynan o'zi**, bir
qatlam yuqorida. Va eng muhimi: **bu xabarni repo o'zi allaqachon shikoyat
qilgan** — `register_once` docstring'i (P15) aynan shunday deb yozadi:
*"a duplicate-name error whose message says nothing about the cause"*. Ya'ni
**da'vo to'g'ri edi va kod hali ham uni bajarayotgan edi**; `register_once`
xatoni **chetlab o'tgan**, **nomlamagan**. Rad etish o'zgarmadi (baribir
`ValueError`) — faqat **sabab** nomlandi.

### Nuqson B — **qo'riqchi rad etish o'rniga yiqilardi**

`validate_schema` sxema bo'ylab har daraja uchun bir marta rekursiya qiladi;
sxema — **operator konfiguratsiyasi**, ya'ni chuqurligi bu modul nazoratida emas.
O'lchandi: **1500 daraja → `RecursionError`** (`ValueError` shartnomasi emas).
**Yetib boriladi:** `mcp.call` chaqiruvchi argumentlarini operator sxemasi
bo'yicha tekshiradi, `arguments_json` esa **12 000 belgi** oladi — 1500 daraja
uchun ~10 500 belgi yetarli. Tuzatish: `MAX_SCHEMA_DEPTH = 64` — **o'lchangan**
raqam, o'ylab topilmagan: 88 tool'ning eng chuqur sxemasi **3 daraja** (21× keng).

**Ikkinchi shift o'lchandi va RAD ETILDI:** `encode()` butun qiymat bo'yicha
chaqiriladi va JSON enkoderi rekursiv — **2998 darajada ishlaydi, 2999 da
yiqiladi**; 12 000 belgi esa ko'pi bilan **1999** daraja tashiydi. `1999 < 2998`
— shift **yetib borilmaydi**, "tuzatish" kerak emas. Probe buni har yurishda
qayta o'lchaydi.

### Qaytarish matritsasi — **17/17 qizil**, restore tasdiqlangan

Ikki rejim **konstanta emas** — shu fazada tuzatilgan ikki nuqson, chunki
qadalgan bo'lmasa tuzatish **jimgina qaytarilishi** mumkin edi.

**Asbob yaxshilandi:** `scripts/probes/revert_matrix.py` endi **bir nechta** test
faylini bitta yurishda o'lchaydi (dotted spec). Bitta fayl bilan chegaralansak,
**qo'shni fayldagi** sinov qadaydigan chegara **yolg'on YASHIL** chiqardi.
Eski `*.py` rejimi o'zgarmadi.

### Qo'shilgan

- `test_adapters.py`: `DeclaredBoundTests` — **11 sinov** (18 → **29**), har biri
  **literalni** qadaydi (konstantani moduldan **qayta o'qimaydi** — §142.5 da
  oltita chegara aynan shu sababdan yashil chiqqan) va chegarani **ikki tomondan**
  yuradi.
- `scripts/probes/probe_tools_boundaries.py` — **66 xossa, 66 pass**, 6 bo'lim.
- `tools.py`: nomlangan konstanta **0 → 11**; 303 → **343 satr**.
- `test_registration_contract.py` docstring'i tuzatildi — u endi **eski** xabarni
  tasvirlab qolgan edi (hujjat kodga zid bo'lsa, foydalanuvchi hujjatga yuradi).

### O'z xatolarim

Probe'da **argumentlar tartibini almashtirdim** (`validate_schema(*nested(n))` —
`(schema, value)`), **`staticmethod` bilan `self` ni yo'qotdim** (timeout `None`
bo'lib chiqdi), `_object_of_bytes` **bir baytga xato** edi (`encode` ixcham JSON
yozadi — `{"a":""}` 9 emas, **8** bayt), va **bisection predikati boolean
qaytargan, lekin `deepest` istisno kutgan** edi. To'rttasi ham **mening** xatoim;
`_object_of_bytes` darhol ko'rindi, chunki test **o'z shartini assert qiladi**.

**Baseline (o'n oltinchi marta aynan):** **2803 test**,
`failures=1, errors=11, skipped=1`, **270.9 s** — imzo **o'zgarmadi**.
2792 → 2803 = **+11**, boshqa o'zgarish yo'q.

**Hujjatlar:** `ULTRA-AUDIT-ASCII-CELL-UZ.md` §149,
`BACKLOG.json` (`offline_tests` **2803**, claim'ga tools chegara hukmi),
`README.md` (2803), shu fayl.

**Keyingi nomzodlar:** `secret_vault.py` (99 satr, 6 literal — **diqqat: unda ham
"rad etishlar bitta xabarga aylanadi" naqshi bor**, lekin u yerda *ochiq* va
*ataylab* bo'lishi mumkin, o'lchash kerak), `app/` qatlami (40 modul),
`agent_planner.py`, `google_oauth.py`, `postgres_connector.py`, `speech.py`,
`mcp.py`, `model_transport.py`, `model_response.py`, `crm/crm_reconcile.py`.
`production_release` **NO_GO** bo'lib qoladi.

## V05V — `secret_vault.py` chegaralari: **chaqiruvchining xatosi kripto xatosi bo'lib qolgan** (§150)

**Sana:** 2026-09-21. **Holat:** QURILDI VA O'LCHANDI (lokal kontrakt).

Auditning qirq beshinchi fazasi. **Yangi qobiliyat yo'q** — credential
saqlanadigan modul (`secret_vault.py`) o'lchandi, **bitta haqiqiy nuqson**
tuzatildi, ikki chegara esa **yetib borilmaydigan** deb **rad etildi**.

**Nega bu modul:** skan mexanik edi — **6 ta katta sonli literal** va **bitta ham
nomlangan konstanta yo'q** (§148 va §149 bilan bir xil sabab, uchinchi nusxa).
Farqi shundaki, bu yerda **rad etish xabarlarining o'zi ham chegara**: `seal` va
`open` `except Exception` bilan hamma sababni bitta matnga aylantiradi — ya'ni
"ikki sabab bitta xabar" bu yerda **ataylab** bo'lishi ham mumkin.

### Birinchi matritsa: **13 mutatsiyadan 8 tasi YASHIL**

Sakkizta yashil **uch guruhga** bo'lindi, va guruhni aniqlash uchun **o'lchash**
kerak bo'ldi:

| Guruh | Chegaralar | Nima uchun yashil |
|---|---|---|
| **Qadalmagan** | halqa 8, kalit id 64, kontekst 4096, kontekst bo'sh emas, konvert maydonlari | Kod bajaradi, lekin **hech bir test qadamaydi** |
| **Qo'riqchi ko'rinmas** | konvert 90 000 | Test bor, lekin u **boshqa sababdan** rad etiladi |
| **Yetib borilmaydigan** | kodlangan maydon 150 000, ochilgan yuk 64 000 | **Hech bir yo'l** yetib bormaydi |

Eng nozik joyi — **konvert 90 000**: `test_invalid_envelope_shapes` da `'x'*90001`
**bor**, ya'ni test **qadagandek ko'rinadi**. Lekin shift olib tashlanganda ham
o'sha satr `json.loads` da yiqilib **baribir** `VaultError` beradi — ya'ni test
**rad etishni** o'lchagan, **sababini** emas.

### Nuqson — **chaqiruvchining o'z xatosi kripto xatosi bo'lib xabar qilinardi**

| Holat | Oldin | Keyin |
|---|---|---|
| `seal` + kontekst 4097 bayt | `Secret encryption failed` | `Bounded encryption context required` |
| `seal` + bo'sh kontekst `{}` | `Secret encryption failed` | `Bounded encryption context required` |
| `seal` + kontekst `None` | `Secret encryption failed` | `Bounded encryption context required` |

`seal` ning `except Exception` i `_aad` ning **o'z** `VaultError` ini ham yutardi.
Sabab esa **chaqiruvchining o'z argumenti** — ya'ni uni **tuzatish mumkin**, va
kripto shaklidagi xabar chaqiruvchini noto'g'ri joydan qidirtiradi. Tuzatish:
`except VaultError: raise` — tor istisno keng istisnodan **oldin**.

### `open` da bu **ataylab TESKARI** — va endi qadalgan

`open` da `_aad` **kalit id tekshiruvidan keyin** chaqiriladi. Agar kontekst xatosi
alohida nomlansa, u "kalit id noma'lum" dan **ajralib qolardi** — ya'ni hujumchi
**qaysi kalit id lar sozlanganini** bilib olardi (**oracle**). Shuning uchun
to'rttasi ham **bir xil** xabar beradi:

```
open(konvert, {})              -> Secret authentication failed
open(konvert, {boshqa ctx})    -> Secret authentication failed
open(konvert, juda katta ctx)  -> Secret authentication failed
open(konvert, kid='nope')      -> Secret authentication failed
```

Bu asimmetriya endi **test bilan qadalgan** — aks holda keyingi dasturchi uni
"izchillik uchun" tuzatib, oracle'ni **qaytarib qo'yadi**.

### Ikki chegara **yetib borilmaydi** — o'lchandi, tuzatilmadi

- **Konvert 90 000:** eng katta **to'g'ri shaklli** konvert **85 479 belgi**
  (maksimal yuk 64 000 bayt + eng uzun kalit id 64 belgi) — ya'ni
  **85 479 < 90 000**. Uning vazifasi `json.loads` ni katta satrdan uzoq tutish;
  shuning uchun **natija emas, mexanizm** qadaldi: `json.loads` umuman
  chaqirilmasligi assert qilinadi.
- **Kodlangan maydon 150 000:** `open` ichida konvert shifti birinchi uradi
  (90 000 < 150 000); `from_environment` orqali ham yetib bo'lmaydi — probe **shu
  hostning** muhit o'zgaruvchisi shiftini bisection bilan o'lchadi: **32 747
  belgi** (Linux'da 131 072). Ya'ni **hech qayerdan** yetib borilmaydi.

### Uch chegarani **xulq-atvor qaday olmaydi**

`NONCE_BYTES`, `ENVELOPE_VERSION` va `AAD_FORMAT` **ham yozuvchi, ham o'quvchi**
tomonidan o'qiladi — kengaytirilsa ikkala tomon birga suriladi va round-trip
**baribir o'tadi**. Probe buni o'lchadi. Yagona pin — **literal assertion**, va bu
nazariy emas: ikkinchi matritsa **1/17 yashil** chiqdi va aynan `AAD_FORMAT` edi
(uni literal tekshiruviga qo'shishni unutgan edim). `AAD_FORMAT` productionda
o'zgarsa — **saqlangan har bir credential ochilmaydigan** bo'ladi.

### Qaytarish matritsasi — **17/17 qizil**, restore tasdiqlangan

Ikki mutatsiya **konstanta emas** — shu fazada tuzatilgan ikki xulq (`seal`
nomlaydi, `open` nomlamaydi). Qadalmasa, ikkala tuzatish ham jimgina
qaytarilardi.

**Asbob yaxshilandi:** `revert_matrix.py` da `verify()` va fazza skriptlarida
`--check` rejimi. Matritsa ~2 daqiqa oladi, naqsh tekshiruvi millisekund — naqsh
xatosi endi kutishdan **oldin** topiladi. (Bu xato **ikki marta** to'langan edi.)

### Qo'shilgan

- `test_secret_vault.py`: `DeclaredBoundTests` — **11 sinov** (11 → **22**), har
  biri chegarani **ikki tomondan** yuradi va **literalni** qadaydi.
- `scripts/probes/probe_vault_boundaries.py` — **99 xossa, 99 pass**, 9 bo'lim.
- `scripts/probes/audit_vault_bounds.py` — **17 mutatsiya**, 17/17 qizil, 0 yashil.
- `secret_vault.py`: nomlangan konstanta **0 → 12**; 99 → **147 satr**.

### O'z xatolarim

**`AAD_FORMAT` ni literal tekshiruviga qo'shmadim** — ikkinchi matritsa shu sababdan
**1/17 yashil** chiqdi, ya'ni "qadadim" degan ishonchni **matritsa ushlab qoldi**.
**Muhit o'zgaruvchisi shiftini bilmasdim** — 150 001 belgili qiymat `os.environ` da
`ValueError` berdi, ya'ni chegara yetib borilmaydigan ekanini **xato orqali**
bildim. **`AESGCM.encrypt` ga `str` berdim** (`.encode('utf-8')` tushib qolgan).
**Eng katta konvertni bir belgi xato hisobladim** — 85 478 dedim, **85 479** chiqdi;
probe o'lchadi.

**Baseline (o'n yettinchi marta aynan):** **2814 test**,
`failures=1, errors=11, skipped=1` — imzo **o'zgarmadi**.
2803 → 2814 = **+11**, boshqa o'zgarish yo'q.

**Hujjatlar:** `ULTRA-AUDIT-ASCII-CELL-UZ.md` §150,
`BACKLOG.json` (`offline_tests` **2814**), `README.md` (2814), shu fayl.

**Keyingi nomzodlar:** `app/` qatlami (40 modul / 4 479 satr),
`agent_planner.py`, `google_oauth.py`, `postgres_connector.py`, `speech.py`,
`mcp.py`, `model_transport.py`, `model_response.py`, `crm/crm_reconcile.py`.
`production_release` **NO_GO** bo'lib qoladi.

## V05W — Boshqaruv tekisligi chegaralari: **61 mutatsiya, 61 qizil** (§153)

`app/platform_api.py` — har bir tenant yozuvi o'tadigan yagona eshik: **717 satr,
88 `Field(...)`**. Inventar uni "eng katta audit qilinmagan modul" deb belgilagan
edi, va sabab aniq edi: **barcha chegaralar inline literal** edi (`le=10`,
`max_length=500`). Inline literalni test **manzillay olmaydi** — bu §151–§152 ning
butun mantiqi.

Birinchi o'lchov savolni shunday qo'ydi: *avtomatlashtirish mijozga qanchalik
qattiq tegishi mumkinligi chegarasini **bitta ham test qizarmasdan** kengaytirish
mumkinmi?* Javob **ha** edi, chunki hech bir test umuman raqam aytmasdi:

```
grep -l 'le=10' runtime_tests/*.py   →  0 fayl
grep -l 'max_length=20' runtime_tests/*.py   →  0 fayl
```

### Nima qilindi

**48 shakl**, **81 chaqiruv joyi** nomlangan konstantaga aylantirildi
(`MAX_IDENTIFIER_CHARS`, `MAX_REENGAGEMENT_PER_CYCLE`, `DEFAULT_HOUR`, …).
Konstantalar bloki fayl boshida, uch bo'limga ajratilgan: **shift** (matn, vektor,
pul, qadam, vaqt), **avtonomiya shiftlari** (qayta aloqa, eskalatsiya, brifing) va
**standart qiymatlar**.

**Invariant endi mutlaq:** modulda **bitta ham** `Field(...)` ichida raqamli
literal qolmadi. Buni test tekshiradi (`NoInlineBoundTests`), ya'ni yangi maydon
`le=500` bilan qo'shilsa, to'plam **qizil** bo'ladi — nomlanishi shart. Busiz
yuqoridagi ikki qatlam "vakillik" bo'lib qolardi, "to'liqlik" emas.

### Strukturaviy test **uchta haqiqiy xatoni ushlab qoldi**

Umumiy naqsh almashtirishlari bir xil *qiymatga* ega, lekin **boshqa ma'noli**
maydonlarni birlashtirib qo'ygan edi. `test_every_named_bound_is_referenced_by_the_module`
(belgilangan-u ishlatilmagan konstanta) ularni topdi:

| Konstanta | Nima bo'lgan edi | Tuzatish |
|---|---|---|
| `MAX_DISPLAY_NAME_CHARS` | `display_name` `MAX_KEY_CHARS` ga tushgan | o'z nomi |
| `MAX_EXTERNAL_ID_CHARS` | `external_ref`, `external_id` `MAX_KEY_CHARS` / `MAX_MODEL_CHARS` ga tushgan | o'z nomi |
| `MAX_QUERY_CHARS` | bilim `query` si `MAX_EVIDENCE_CHARS` ga tushgan | o'z nomi |

Qiymat bir xil (500, 256) bo'lgani uchun **hech bir xulq o'zgarmagan** — lekin
kelajakda bittasi o'zgarsa, ikkinchisi ham **jimgina** o'zgarardi. Bu aynan
audit qidirayotgan nuqson sinfi.

### Blast radius **o'lchandi, taxmin qilinmadi**

Yigirma daqiqalik naqsh bilan **uchta eng xavfli chegara** kengaytirildi:

| Mutatsiya | `test_reengagement` + `test_escalation` + `test_briefing` |
|---|---|
| `MAX_REENGAGEMENT_PER_CYCLE` 20 → **200** | **YASHIL** |
| `MAX_ESCALATION_PER_CYCLE` 50 → **500** | **YASHIL** |
| `MAX_COOLDOWN_SECONDS` 2 592 000 → **25 920 000** | **YASHIL** |

Ya'ni: bir siklda **10 barobar ko'proq** mijozga tegish, va anti-spam kutish
muddatini **10 barobar** qisqartirish — bu uch modul uchun **butunlay ko'rinmas**.
Ular bu endpoint'lar **orqasidagi** siklni o'lchaydi, **oldidagi** shartnomani emas.

Shu sabab matritsa naqshi ataylab **bitta fayl**. To'rt modulni qo'shish 2 daqiqalik
matritsani 70 daqiqaga aylantirardi va **hech narsani isbotlamasdi** — natija
baribir qizil bo'lardi, chunki qadovchi test bitta.

### Qaytarish matritsasi — **61/61 qizil**, restore tasdiqlangan

**0 yashil, 0 o'lchanmagan.** Birinchi marta **barcha standart qiymatlar ham
qadalgan**. `DEFAULT_*` — bu chegara emas, **siyosat**: operator tanlamaganda tizim
**o'zi** nima qiladi. Shuning uchun ular ko'rib chiqilishi shart bo'lgan eng xavfli
guruh, va ular endi 16 mutatsiya bilan qadalgan.

### Qo'shilgan

- `runtime_tests/test_control_plane_bounds.py` — **50 sinov**, uch qatlam
  (literal, xulq, struktura).
- `scripts/probes/audit_control_plane_bounds.py` — **61 mutatsiya**, 61/61 qizil.
- `platform_api.py`: nomlangan konstanta **0 → 90**; 717 → **913 satr**.

### O'z xatolarim

**`min_length=1` ni tashlab ketdim** — keyin ko'rib chiqib nomladim
(`MIN_NON_EMPTY`), chunki u ham chegara ("bo'sh bo'lmasin"), va nomlanmasa
struktura invariant **mutlaq** bo'lmasdi, ya'ni istisno qolardi.
**`MAX_EXTERNAL_ID_CHARS` va `MAX_QUERY_CHARS` ni e'lon qildim, lekin
ishlatmadim** — strukturaviy test ushladi. **Umumiy naqsh almashtirishga haddan
tashqari ishondim** — uchtasi birlashib ketdi, yuqoridagi jadvalga qarang.

**Baseline:** **2949 test**, `failures=1, errors=11, skipped=1` — imzo
**o'zgarmadi** (§150 bilan aynan).

---

## V05X — Windows uchun bloklangan yuza: **11 emas, 12** (§154)

Inventar shunday yozgan edi:

| | |
|---|---|
| Inventar da'vosi | `failures=0, errors=11, skipped=2` |
| **O'lchangan** | `failures=1, errors=11, skipped=1` |

Uch maydondan **bittasi** to'g'ri edi. Farqning sababi mazmunli:
`test_macos_bundle.test_all_files_private` — bu **failure**, **error** emas: u
yetishmayotgan primitivni chaqirmaydi, **ruxsat bitlarini** tekshiradi
(`st_mode & 0o077 == 0`), shuning uchun boshqacha yiqiladi va "error" sanovidan
tushib qolgan. `skipped=2` esa umuman yo'q: to'plamdagi **yagona** `skipTest` —
`test_whatsapp_inbound` da, va u platforma uchun emas, **runtime** uchun
o'tkazib yuboradi.

Ya'ni **o'zini yozuv deb e'lon qilgan hujjat ichida** raqam surilib ketgan.

### Bitta yetishmayotgan primitiv yettitasini bloklaydi

| Sabab | Sinovlar |
|---|---|
| `os.O_NOFOLLOW` yo'q | **7** |
| `os.mkfifo` yo'q | 2 |
| `fcntl` moduli yo'q | 1 |
| POSIX mount semantikasi (`test_foundation_v02`) | 1 |
| POSIX ruxsat bitlari (`test_macos_bundle`) | 1 |

`O_NOFOLLOW` — to'plamning **yarmidan ko'pini** bloklayotgan yagona sabab, chunki
u `open()` ning simlinkni kuzatmasligini ta'minlaydi va `portable_fs` butun
descriptor-identifikatsiya tekshiruvini shunga qaraydi.

### Yozilgani

`runtime_tests/test_platform_baseline.py` — **7 sinov**. U ro'yxatni **sabab
bo'yicha** qadaydi, sinovlarni **qayta yurgizmasdan**: Windows'da har bir sabab
**hamon** amal qilishi shart, POSIX'da esa **hech biri** amal qilmasligi shart.
Ya'ni kim `os.O_NOFOLLOW` ni shim qilsa yoki loyiha Linux runner'ga o'tsa, bu
modul **qizil** bo'ladi va "yozuv eskirgan" deydi — yozuv haqiqiy qolishining
yagona yo'li shu. Ro'yxatdagi har bir ID **import qilinib tekshiriladi**, ya'ni
xato yozilgan ID 12 gacha sanab, hech narsani hujjatlashtirmasligi mumkin emas.

**O'tkazib yuborish — bu o'tish emas.** O'n ikkisi Windows'da **tekshirilmagan**,
yashil emas. Modul aynan shuni CI jurnalini o'qib bilmaslikka majbur qiladi.

---

## V05Y — `integration_tests` **yig'ilmay** turgan edi (tuzatildi)

Bu "test infratuzilmasi" bandi edi va u taxmin qilinganidan **oddiyroq** chiqdi.

```
pytest integration_tests --collect-only
→ 86 tests collected, 1 error
→ ERROR integration_tests/test_connector_authority_http.py - app.config.ConfigError
```

Sabab: sakkiz moduldan **yettitasi** `ENV` ni o'zi o'rnatadi, bittasi yo'q.
`test_connector_authority_http.py` ning 4-qatori `app.platform_api` ni import
qiladi, `ENV` ni o'rnatadigan modulga esa 6-qatorda yetadi — pytest esa uni
alifbo bo'yicha **birinchi** yig'adi. `app.platform_api` → `app.auth` esa
import vaqtida konfiguratsiyani tekshiradi va **fail-closed** yopiladi.

Tuzatish: `integration_tests/conftest.py` — pytest har qanday test modulidan
**oldin** `conftest` ni import qiladi, ya'ni tartibga bog'liqlik yo'qoladi.
Har moduldagi `setdefault` **qoldirildi** (ular zararsiz, va olib tashlansa
fayllar yakka holda yurmay qolardi).

Natija: **86 + 1 xato → 95 sinov, 95 pass, 50 s.**

`integration_tests` **asosiy offline gate'ga qo'shilmadi**: u FastAPI/HTTPX talab
qiladi va offline Computer'da yurmaydi (modulning o'z hujjati shuni aytadi:
"NOT RUN in the offline Computer"). U endi **yig'iladi va yashil**, lekin
`verify_offline.py` dan tashqarida qoladi — bu ataylab.
## V05Z — `app/` qatlami: **o'qilmaydigan to'plamdagi qadam — qadam emas** (§155)

Bu fazada ikkita topilma bor va ikkinchisi muhimroq.

### Nima qilindi

`app/` qatlami — oxirgi audit qilinmagan qatlam edi. U runtime modullaridan
**boshqa turdagi** bo'shliqni saqlagan: ular **nomsiz** edi, bular esa **nomli,
lekin o'qilmaydigan joyda qadalgan**.

`MAX_PERSONA_CHARS` (`packs.py`) va `MAX_QUEUE` (`runner_ws.py`) `tests/` da
qadalgan edi. Lekin `.github/workflows/verify.yml` faqat ikki narsani yurgizadi:
15-qatorda `runtime_tests`, 33-qatorda `integration_tests`. **`api-python/tests/`
hech qayerda yo'q.** Ya'ni bu qadam emas — **da'vo**.

Nomlangan chegaralar: `auth.py` (3), `identity_store.py` (19), `limits.py` (4),
`packs.py`, `telegram.py`, `trace.py`, `runner_ws.py` (2). Jami **31 mutatsiya,
31 qizil, 0 GREEN, 0 o'lchanmagan**; har modulda `CONTROL GREEN` va
`restore verified: YES`.

Eng qimmatli qator — **scrypt parametrlari**: `SCRYPT_N` ni `16384` → `1024`
qilish har bir saqlangan parolni buzish narxini **16 barobar** arzonlashtiradi,
va buni **hech narsa o'qimasdi**.

### Instrument o'z sidecar'ini o'chira olmagani uchun o'lchov o'ldi

Matritsa birinchi moduldan keyin to'xtadi — 31 dan **3 tasi** o'lchandi, qolgan
oltisi haqida **birorta satr yo'q**, chiqish kodi esa `0`.

Sabab: `revert_matrix.main` oxirida `os.remove(sidecar)`, host esa ommaviy
o'chirishni to'sadi:

```
[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]
  {"count":220,"threshold":50,"scope":"turn","targets":["...auth.py.matrix-baseline"]}
```

Istisno `main` dan chiqib faza skriptining `for` siklini uzadi. Ya'ni **asbob
o'zini tozalay olmagani uchun o'ldi va bu haqda hech narsa demadi** — §148 dagi
"ikki o'qilmagan rejim yashil deb o'qilgan" nuqsonining boshqa ko'rinishi.

Tuzatish: `revert_matrix.drop_sidecar()` — `OSError` yutiladi, xabar bosiladi,
o'lchov davom etadi. Sidecar eskirib qolishi xavfsiz (keyingi yurish uni jonli
fayl bilan solishtiradi).

### Ikkinchi topilma: §154 ning **o'z yozuvi** ikki marta xato edi

`test_platform_baseline.py` aynan shu surilishni to'xtatish uchun yozilgan edi.
U **o'zi surilib ketdi**:

| | §154 yozuvi | §155 o'lchovi |
|---|---|---|
| Bloklangan sinovlar | 12 | **13** |
| `O_NOFOLLOW` | 7 | **6** |
| `symlink_privilege` | — | **2** |
| Imzo | `errors=11` | `errors=12` |

1. `test_root_symlink_replacement_denied` ro'yxatda **umuman yo'q edi**.
2. `test_escape_symlink_denied` `O_NOFOLLOW` deb yozilgan, lekin traceback uning
   **o'z `setUp` ida**, `os.symlink` da o'lganini ko'rsatadi — o'sha kodga yetib
   ham bormaydi.

Ikkisi ham **nomi yo'q** boshqa fakt bilan o'ladi: Windows simlink imtiyozi,
`OSError` `WinError 1314`. Endi u `symlink_privilege`.

**Saboq:** birinchi versiya shartning **rost** bo'lishini tekshirdi, shartning
**aynan sabab** ekanini hech qachon tekshirmadi. `not hasattr(os, 'O_NOFOLLOW')`
bu hostda rost — sinovni o'ldirgan narsa u bo'lsa ham, bo'lmasa ham. Ya'ni
noto'g'ri qator predikatni qanoatlantirib **o'tib ketadi**.

Yechim: `test_each_recorded_reason_is_the_actual_cause` — har bir bloklangan
sinovni **yurgizadi** va ko'tarilgan istisnoni qatordagi sababga solishtiradi.
Qo'riqchi tekshirildi: sababni qayta mutatsiya qilinsa **3 sinov qizil**.

O'sha tuzoqning ikkinchi nusxasi: `TestResult.errors` **istisno obyektini emas,
formatlangan traceback satrini** saqlaydi, ya'ni `errors[0][1][1]` — satrning
ikkinchi belgisi. Tekshiruvchi sinov shu xatoni qilib 11 qizil berdi; `_Capture`
endi obyektni `addError` dan oladi.

### O'lchov sodiqligi: `tzdata`

`tests/` ni o'lchaganda ma'lum bo'ldi: **35 emas, 33 qizil**. Ikki sinov
`tzdata` bilan yashilga o'tdi. `requirements.txt:9` da `tzdata>=2024.1`
**e'lon qilingan**, lekin lokal venv'da o'rnatilmagan edi — ya'ni mening
baseline'im CI muhitiga mos kelmasdi. O'rnatildi: `35 → 33 qizil`,
`170 → 172 yashil`.

### `tests/` — 33 qizil, uch sabab

| Sabab | Sinovlar |
|---|---|
| Bekor qilingan marshrut → `410 Gone` | **10** |
| Eskirgan javob shakli (`KeyError`) | **15** |
| Legacy runner WebSocket (`4401`) | **4** |
| Kontrakt/mazmun surilishi | **3** |
| Auth statusi (`401` vs `403`) | **1** |

Ya'ni uning qizilligi **tasodifiy emas** — u ataylab bekor qilingan API ni
sinaydi. Lekin u hamon **o'qilmaydi**, va shu holicha turishi noto'g'ri: uni yo
tuzatish, yo nafaqaga chiqarish kerak.

### Yozilgani

| Fayl | Nima |
|---|---|
| `api-python/runtime_tests/test_app_layer_bounds.py` | **48 sinov** (qadamlar + struktura qo'riqchisi) |
| `scripts/probes/audit_app_layer_bounds.py` | 31 mutatsiya, 7 modul |
| `scripts/append_doc_section.py` | marker bilan **bir marta** qo'shuvchi (ikki marta yozishni to'sadi) |
| `scripts/probes/revert_matrix.py` | `drop_sidecar()` — o'lchov endi o'zini tozalay olmagani uchun o'lmaydi |
| `api-python/runtime_tests/test_platform_baseline.py` | 12 → **13**, `symlink_privilege`, **sababni tekshiruvchi** sinov |

### Natija

```
runtime_tests.test_app_layer_bounds runtime_tests.test_identity_store
→ 54 sinov, OK

test_platform_baseline
→ 9 sinov, OK
```

To'plam: **3 000 sinov** (`2949 + 51`), imzo
`failures=1, errors=12, skipped=1` — `errors` birga oshdi, chunki **13-sinov
haqiqatan ham o'lchanmagan edi** va endi sanaladi.

---
## V060 — `verify_offline.py` tugata olmaydigan gate edi, va **socket'siz to'plamga solgan socket** (§155 davomi)

Bu ish §155 ni tekshirish paytida chiqdi va **ikki nuqson** berdi: biri asbobda,
biri **mening testlarimda**.

### Asbob: gate o'zi tekshirayotgan platformada tugata olmaydi

```
UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97 in position 26494
TypeError: data must be str, not NoneType   (verify_offline.py:141)
```

`0x97` — `[WinError 1314] Клиент не обладает требуемыми правами` matnidagi
kirill bayti. O'quvchi thread `UnicodeDecodeError` ko'taradi, `result.stdout`
`None` bo'lib qoladi, `write_text(None)` `TypeError` beradi.

Sabab: `child_env` `LANG=C.UTF-8` o'rnatadi, lekin `PYTHONIOENCODING` ni emas —
**bola bilan ota-ona kodlash haqida kelishmagan**. Tuzatish: `errors='replace'`
va `result.stdout or ''`.

### Mening testlarim: to'plam socket'siz **bo'lgani uchun** emas edi

Tuzatishdan keyin gate **yurgizildi** va uchta yangi xato bilan qizil bo'ldi:

```
RuntimeError: Offline verification: network disabled
```

Uchtasi ham mening `TelegramBodyCeilingTests` im — ular
`starlette.testclient.TestClient` ishlatgan edi, va `runtime_tests` da
`TestClient` **hech qayerda yo'q**: uni faqat `integration_tests` ishlatadi va u
aynan shu sabab bilan offline gate'dan chiqarilgan.

Ya'ni to'plam socket'siz **bo'lgani uchun** emas — **hech kimga socket kerak
bo'lmagani uchun** socket'siz edi.

Tuzatish **ikki qadam** bo'ldi, chunki birinchisi yetmadi:

1. `TestClient` olib tashlandi → korutina to'g'ridan-to'g'ri chaqiriladi.
2. `asyncio.run` ham ishlamadi: **Windows'da event loop'ning o'zi socket** —
   `ProactorEventLoop` ham, `SelectorEventLoop` ham self-pipe'ini
   `socket.socketpair()` dan quradi, hook uni `socket.connect` deb ko'radi.
   Shuning uchun korutina **qo'lda** `send(None)` bilan yuritiladi. Bu xavfsiz,
   chunki handler hech qachon to'xtamaydi (yagona `await` — stub `body()`).

**Tekshirildi:** modul **48 sinov** — audit hook **bilan ham**, usiz ham **OK**.

**Yangi qo'riqchi:** `OfflineSuiteTests` `runtime_tests` da `TestClient`/`httpx`
importini taqiqlaydi. `urllib.request` **ataylab** ochiq: `test_custom_http_adapter`
va `test_onec_adapter` uni faqat `OpenerDirector.open` ni soxta `HTTPError` bilan
almashtirish uchun import qiladi — ulanish yo'q. Mutatsiya bilan tekshirildi:
`test_retry.py` ga `TestClient` qo'shilsa, test **fayl nomini aytib** yiqiladi.

### Yana bir o'lchov: Node runner ham **o'sha sinfda** bloklangan

`node --test apps/runner/test.js` Windows'da **24 dan 11 tasi** yiqiladi:

```
Error: Private single-owner file required
  apps/runner/runner.js:92  privateFile()
```

Sabab Python tomonidagi bilan **bir xil**: `privateFile` `(meta.mode & 0o077) === 0`
ni talab qiladi, Windows esa POSIX ruxsat bitlarini modellashtirmaydi.

| Til | Bloklangan | Qadalganmi |
|---|---|---|
| Python (`runtime_tests`) | **13 / 3000** | ha — `test_platform_baseline.py` |
| Node (`apps/runner`) | **11 / 24** | **yo'q** |

Node tomonidagini qadash — keyingi ish.

### Yakuniy o'lchov

```
Ran 3000 tests in 172.327s

FAILED (failures=1, errors=12, skipped=1)
```

13 yomon sinov — **hammasi** ma'lum bloklangan yuza, **bittasi ham** shu fazada
yozilgan moduldan emas.

---
## V061 — Tasdiq navbati va avtonomiya zinapoyasi: **38 mutatsiya, 38 qizil** (§156)

### Nima qilindi

§155 "o'qilmaydigan to'plamdagi qadam — qadam emas" dedi. Bu fazada o'sha gapning
**manzili** aniqlandi: `LadderStore` va `FileApprovalStore` ning **gate ichida
birorta ham iste'molchisi yo'q**. Ular faqat `tests/` da (qizil, gatesiz) qadalgan
edi, va `runtime_tests`/`integration_tests` bo'ylab qidiruv boshqa iste'molchi
topmaydi.

Nomlangan chegaralar: `ladder.py` da to'rtta siyosat soni + oyna poli + oyna
ifodasi; `approvals.py` da navbat shifti, sabab shifti, id entropiyasi,
`DECISIONS`, telefon maskasi. Jami **38 mutatsiya, 38 qizil, 0 GREEN,
0 o'lchanmagan**; ikki modulda ham `CONTROL GREEN`, `restore verified: YES`.
Yangi to'plam: `runtime_tests/test_approval_ladder_bounds.py` — **76 sinov**.

### `MIN_WINDOW` — o'lik qoida, va u hech narsa ko'tarmaydi

`min_tasks` default'i ostida **ikkinchi yalang'och `30`** turgan edi: mos kelishi
shart bo'lgan ikki literal, ularni mos qiladigan hech narsa yo'q.

```
record:  entry["outcomes"] = (... + [bool(ok)])[-self.window:]
record:  if total >= self.min_tasks:
```

Tarix `[-window:]` gacha qisqaradi, ko'tarilish `total >= min_tasks` talab qiladi.
Ya'ni `window < min_tasks` bo'lsa `total` bu songa **hech qachon yetmaydi** — agent
hech qachon ko'tarilmaydi. Bu o'lik qoida, va u **hech qanday istisno
ko'tarmaydi**. `max(MIN_WINDOW, min_tasks)` savolni olib tashladi; o'lchov poli
ikki tomonida ham o'tkazildi: `{1,5,29} → 30`, `{31,100} → 31,100`.

### Siyosat uchligi konstruktor default'i edi

`min_tasks`, `max_err`, `demote_err` — "agent qachon odamsiz ishlashni boshlaydi"
degan savolning javobi — konstruktor default'i edi. Ya'ni
`LadderStore(min_tasks=1)` bitta muvaffaqiyatli vazifadan keyin ko'tarardi va
**birorta testning rangi o'zgarmasdi**; yagona signal agentlarning tezroq avtonom
bo'lib qolgani. `auto_cap` default'i ham shu yerda: uni `False` qilish agentga o'z
write-harakatlarini o'zi tasdiqlash imkonini beradi (audit S29).

### `DECISIONS` — uchinchi nusxa ortiqcha edi

Qaror lug'ati ikki joyda yozilgan edi (store `ValueError`, marshrut `422`), va
uchinchi nusxa — `"approved" if decision == "approved" else "rejected"` — yuqoridagi
qo'riqchidan keyin shunchaki `decision`. Uchta yozuv o'rniga bitta manba qoldi.

### Telefon maskasi ikki tomondan ham sizadi

| Tana belgilari | Natija |
|---|---|
| 8 | **maskalanmagan** |
| 9–16 | `+998***` |
| 17 | `+998***6` — **dumi qolgan** |

Ikkisi ham testda **sizib chiqish sifatida** yozildi, tuzatish sifatida emas: ular
allaqachon shu holatda edi, va chegara endi nomlangan.

### O'lchov xatosi — `storage.reset()` faylni **o'chirmaydi**

Modulning dastlabki probe'i bir xil `APP_DB` da o'nlab ssenariy yurgizib,
**oldingi ssenariyning qoldig'ini siyosat deb** o'qidi (`errs=1` "ko'taradi",
29 muvaffaqiyatdan keyin `human_assisted` — holbuki 29 < 30). Sabab: `reset()`
faqat ulanishni yopadi, faylni qoldiradi. Har bir testga alohida `APP_DB`
berilgach raqamlar izchil bo'ldi:

```
29 -> human_led | 30 -> human_assisted
errs=1 (0.0333) -> human_assisted | errs=2 (0.0667) -> human_led
6/30 (==0.20) -> tushmadi | 7/30 (0.2333) -> bir pog'ona pastga
```

Bu §155.6 bilan **bir sinf**: o'lchov to'g'ri ko'rinadi, lekin o'lchanayotgan narsa
boshqa. Farqi shundaki, u yerda sabab test to'plamida, bu yerda **probe'ning
o'zida** edi.

### Qadamni aynan chegarada o'lchash

`min_tasks=30` da 5% **hech qachon butun songa tushmaydi** (1.5), shuning uchun
`<=` ni `<` dan ajratib bo'lmasdi. `min_tasks=20` tushiradi — bitta xato yigirmada
**aynan** 5%. `demote_err` uchun ayni shu 6/30 = 0.20 bilan bajariladi. Ikkisi ham
mutatsiya matritsasida qizil.

### Halol cheklov: **mustaqil iste'molchi yo'q**

§155 da o'lchov spetsifikatsiyasi ikki fayl edi — qadam
(`test_app_layer_bounds`) va iste'molchi (`test_identity_store`). Bu fazada
ikkinchisi **yo'q**: qadamlar xatti-harakat bilan qadalgan (haqiqiy SQLite orqali
haqiqiy store), lekin **mustaqil chaqiruvchi** kengaytirilgan chegarani sezmaydi.
Bu §155 dan kuchsizroq, va yashirmasdan shunday yozildi.
## V062 — Gate o'lchagan narsani aytmaydi: **164 xato, 12 emas** (§156 davomi)

### Nima bo'ldi

`verify_offline.py` Python ishlarini `sys.executable` bilan yurgizadi — uni kim
ishga tushirgan bo'lsa, o'sha interpreter bilan. Bu ataylab (muhit ham
o'lchanayotgan narsaning bir qismi), lekin natijada **noto'g'ri ishga tushirish
haqiqiy nuqsondan farq qilmaydi**.

Men gate'ni boshqariladigan venv o'rniga yalang'och `python` bilan yurgizdim:

| | To'g'ri venv | Yalang'och `python` |
|---|---|---|
| Signature | `failures=1, errors=12, skipped=1` | **`failures=2, errors=164, skipped=1`** |
| `python_runtime` | `FAIL` | `FAIL` |
| Chiqish kodi | `1` | `1` |

164 xatoning sababi bitta: `ModuleNotFoundError: No module named 'fastapi'`.
`fastapi` import qiladigan har bir modul **umuman import bo'lmaydi**, shuning uchun
`test_app_layer_bounds`, `test_control_plane_bounds` va
`test_approval_ladder_bounds` `unittest.loader._FailedTest` ga aylanadi. Qolgan
~150 tasi `cryptography` yo'qligidan.

**Ro'yxat allaqachon bor edi** — lekin eng oxirida, `summary.json` ichida
`http_dependencies_missing` deb: uch daqiqalik traceback'dan keyin, o'quvchi bilib
izlashi kerak bo'lgan maydonda.

### Tuzatish

Ishlar boshlanishidan **oldin** rad etish — interpreter nomini aytib, va dalil
papkasi yaratilishidan **oldin**, shunda rad etish tugallangan yurishga o'xshab
qoladigan bo'sh papka qoldirmaydi:

```
REFUSED: ...python\versions\3.13.12\python.exe cannot import pytest, fastapi, httpx,
pydantic, jwt, yaml, cryptography.
```

**Ikki ro'yxat, va nega torrog'i to'g'ri.** Birinchi urinishda `psycopg` va
`redis` ham bloklandi — va gate **shu repozitoriyning bazaviy o'lchovi olingan
venv**ni rad etdi. Ular baza drayverlari: yo'qligi bir nechta kontrakt testini
skip qiladi, import bo'lishni to'xtatmaydi. Shuning uchun:

* `REQUIRED_DEPENDENCIES` — import uchun zarur, **bloklaydi**;
* `HTTP_DEPENDENCIES` — yuqoridagilar + ixtiyoriy drayverlar, **xabar qilinadi**.

Va rad etish ochib bergan bo'shliq: **`cryptography` ikkala ro'yxatning hech
birida yo'q edi** — uning yo'qligi 164 xatoning ko'p qismini berdi, summary esa
bu paketni bir marta ham nomlamagan bo'lardi. Endi u bloklovchi ro'yxatda.

### Ikkinchi xato — va u §155.6 bilan bir sinf

Tuzatishdan keyin men `docs/verification/` ostidagi **bo'sh** papkani "rad
etilgan yurishning qoldig'i" deb o'qib o'chirdim. U qoldiq emas edi — u **hozir
yurgizilgan** gate'ning **tirik** dalil papkasi edi.

Gate papkani birinchi yaratadi, `*.log` larni esa har bir ish **tugagach** yozadi;
ya'ni `python_runtime` (uch daqiqa) ishlayotganda papka **bo'sh bo'lishi kerak**.
Yurish birinchi log'ni yozmoqchi bo'lganda `FileNotFoundError` bilan o'ldi.

Bo'sh papka "rad etilgan" bilan ham, "ishlayotgan" bilan ham mos keladi; ikkisini
faqat **jarayonlar jadvali** ajratadi. Xatoni qilinishiga sabab bo'lgan narsa ham
bor edi: oldingi sessiyadan haqiqiy bo'sh qoldiq qolgan edi. Endi qo'riqchi
`mkdir` dan oldin ishlagani uchun rad etish umuman papka yaratmaydi va bu
noaniqlik yo'q.

## V063 — Customer 360: **bir son ikki joyda** va **60 mutatsiyadan 4 tasi yashil** (§157)

### Nima bo'ldi

§156 halol cheklov bilan tugadi: `LadderStore` va `FileApprovalStore` ni gate ichida hech
kim ishlatmaydi, qadamlar **mustaqil iste'molchisiz** edi. §157 o'sha bo'shliqni
**o'lchadi**: `app/` qatlamining eng ko'p chegarali fayli `customer360.py` olindi — va
uning **uchta mustaqil iste'molchisi** bor (`test_customer360`, `test_identity_hardening`,
`test_control_plane_authority`).

Uchta asosiy topilma:

1. **`{1,128}` va `maximum=128` — bir son, ikki joy.** `_id` avval `_text`, keyin `_ID_RE`
   ni tekshiradi; regex kvantifikatori o'sha sonni ikkinchi marta yozgan edi. `{1,12}`
   qilish hech qanday xatolik ko'tarmaydi — qaysi biri tor bo'lsa **jimgina o'sha yutadi**.
   Endi regex konstantadan quriladi — ikkinchi literal **yo'q** (§156 dagi
   `max(MIN_WINDOW, min_tasks)` bilan bir harakat).

2. **Dominat shiftlar.** `kind`, `channel`, `status`, `currency` uzunlikka, keyin lug'atga
   tekshiriladi — shuning uchun ularning shiftlari **to'plamni** emas, ish hajmini
   chegaralaydi. Ular **sabab** bilan qadalandi: 33 belgili `kind` uzunlik sababidan, 10
   belgilisi lug'at sababidan rad etiladi — ikki xil sabab, bir xil istisno turi.

3. **`status!='deleted'` — hech bir test yeta olmaydigan qo'riqchi.** `create_customer`
   `deleted` ni rad etadi, shuning uchun API orqali bu qatorni yarata olmaydigan test
   **yo'q**. To'rt predikat, nol qoplama. Qator SQL bilan ekildi.

Va uchta yangi qadam turi: `LIMIT 100` **to'rt marta**; yozuv rollari `{'owner','operator'}`
`identity_store.ROLES` dan bexabar **inline** edi (endi `WRITE_ROLES` + subset testi);
sahifalash oynasi xabar matnida **uchinchi marta** yozilgan edi (`"limit 1..100"` — endi
f-string).

### Matritsa — ikki yurish

`customer360.py` ning **mustaqil iste'molchisi bor**, shuning uchun matritsa **ikki marta**
yurgizildi: **pass A** — faqat yangi modul (52 mutatsiya), **pass B** — faqat uchta
iste'molchi (8 mutatsiya). Bu §156 ning "qadam — lekin mustaqil chaqiruvchi sezmaydi"
gapini **o'lchangan** da'voga aylantiradi.

**Birinchi yurish: 60 dan 4 tasi GREEN.** To'rtdan uchtasi — bitta oila, va u bu fazaning
eng qimmatli darsi:

> **Istisno turi — rad etishning isboti emas.** Har bir mutator oxirida `get_customer` ni
> qaytaradi; qo'riqchi o'chirilganda ham chaqiruv "rad etiladi" — faqat **bir qator
> keyinroq**, yozuv allaqachon bo'lgach. `assertRaises(X)` buni ajratmaydi.

* `_ensure_customer` predikati — jetim qator yozilardi; endi `count == 0` bilan qadalgan.
* `_ensure_customer` o'chirilishi — yo'q customerga kontakt yozilardi; `count == 0`.
* actor tekshiruvi — keyingi qator rad etardi, lekin **xabar** o'zgarardi; endi xabar bilan.
* `verified is not True` — noto'g'ri yurishga yozilgan edi; pass B ga ko'chirildi.

**Ikkinchi yurish: 60/60 RED, 0 GREEN, 0 o'lchanmagan**, ikkala `restore verified: YES`.

### Natija

- `app/customer360.py` — 17 konstanta + `WRITE_ROLES`; `{1,128}`/`maximum=128` juftligi
  birlashtirildi; `LIMIT 100` × 4 → konstanta; xabar f-string.
- `runtime_tests/test_customer360_bounds.py` — **72 sinov** (77 subtest), yashil.
- `scripts/probes/audit_customer360_bounds.py` — ikki yurishli matritsa, 60 mutatsiya.
- Gate: `Ran 3148 tests, FAILED (failures=1, errors=12, skipped=1)` — 13 tasi platforma
  sababli bloklangan (§154), **modulimdan nol**; MANIFEST 510 fayl, PASS.

### Keyingi qadam

`app/` qatlamida qolgan modullar: `storage.py` (260 satr), `pipeline.py` (167), `main.py`
(137) — ularning chegaralari boshqa sinfda; `apps/runner/portable_fs.py`. Keyin:
`api-python/tests/` (33 qizil, gatesiz) ning taqdiri — tiklash yoki o'chirish.

## §158 — Hujjatlarda "yo'q / qadalmagan" deb yozilgan joylarni to'ldirish (2026-09-25)

Uch ish, uch o'lchov; har biri oldingi sessiya qoldirgan aniq qatordan olindi. Qatorlardan
biri allaqachon yopilgan edi: `api-python/tests/` nafaqaga chiqarilgan (papka yo'q,
`LEGACY-RETIRED.md` yozilgan) — §157 dagi "taqdiri: tiklash yoki o'chirish" shu bilan yopildi.

### 1. WhatsApp inbound HTTP route — modul bor edi, route yo'q edi

`app/whatsapp_api.py` yozildi (`engine.py` izohi va `whatsapp_inbound` docstring'i aynan shu
nomni kutgan edi) va `app/main.py`ga ulandi. Avvaldan yozilgan 16 test 404 edi (route yo'q),
uchta yangi chegara testi qo'shildi — **19/19 PASS**.

Qarorlar kodda yozilgan sabablar bilan: imzo **RAW baytlar** ustida, hech narsa parse
qilinishidan oldin; tenant **tasdiqlanmagan** `phone_number_id`dan faqat imzoni tekshiruvchi
sirni tanlash uchun o'qiladi; sir yo'q bo'lsa **500**, dev-bypass yo'q (tasdiqlanmagan delivery
24 soatlik oynani ochib, `whatsapp.send`li har bir agentni mijoz yozdi deb ishontira oladi);
noma'lum biznes raqami **200 + `ignored`** (Meta non-2xx ni qayta yuboradi, retry bo'roni
tuzatish emas); **bir raqamni ikki tenant e'lon qilsa 409** — taxmin bir do'kon mijozini
boshqasiga beradi.

Yo'l-yo'lakay topilgan **ikki nuqson** (ikkisi ham oldingi sessiyadan, ikkisi ham test
yashil bo'lmagani uchun yashiringan edi):

1. **Test helper'i imzoning uch holatini bittaga qo'shib qo'ygan**: `signature or sign(raw)`
   tufayli `''` (bo'sh header) **to'g'ri imzo** bilan almashtirilardi — ya'ni bo'sh header
   hech qachon sinalmagan. Endi `None` / `False` / `''` ajratilgan.
2. **`test_security_helpers.py::test_auth_token_admin_gate` o'z muhitini e'lon qilmasdi**:
   `IDENTITY_DIRECTORY=false` ni yurgizuvchi eksport qilishini kutardi, CI esa faqat
   `ENV` va `PIPELINE_MODE` beradi → test **CI'da doim qizil** bo'lardi (410 ≠ 403). Endi
   test o'zi tayinlaydi.

`whatsapp` `OUTBOUND_CHANNELS`ga qo'shilgani (oldingi sessiya) bilan eskirgan ikki kutish
tuzatildi: runtime testi "whatsapp — chat hech qachon yozmagan" uchun `NotFound`ni qadaydi;
integratsiya testi esa whatsapp reply'ning **ijobiy** yo'lini (`api.agents` + engine policy
tikilgan, task `queued`, `approval_status=approved`, adapter chaqirilmagan) va notanish chat
uchun **404**ni qo'shdi.

O'lchov: `integration_tests` **351 passed** (o'sha daraxtda avval 350: 1 qizil).

### 2. `app/pipeline.py` chegara auditi — 36 test, 17/17 RED matritsa

Modulning **runtime test fayli umuman yo'q edi**; chegaralar inline: `4000` ikki joyda,
`1..99` uchinchi nusxasi (Order modeli + approval re-check bilan birga **uch joyda**), `200`,
`500`, `8`, `10**9`, pattern ichida yalang'och `998`. Endi oraliq `app/orders.py`da **bir
marta** e'lon qilinadi va `pipeline` import qiladi; qolgani nomlangan konstanta
(`MAX_TEXT_CHARS`, `MAX_CUSTOMER_CHARS`, `MAX_LEAD_CHARS`, `STABLE_ID_HEX_CHARS`,
`STABLE_ID_MODULUS`, `UZ_COUNTRY_CODE`, `UZ_NATIONAL_DIGITS`, `UZ_PHONE`, `BUY_COMMAND`,
`STOP_COMMAND`).

Yangi `runtime_tests/test_pipeline_bounds.py` (**36 test**) ularni ham literal, ham xulq
bilan qadaydi: platform ko'prigi (`ui→web`, truncation, conversation default) haqiqiy
funksiyada, engine seam'lari patchlangan holda; `remember` haqiqiy SQLite faylda; legacy
production refusal `is_prod` patchlangan holda.

`scripts/probes/audit_pipeline_bounds.py`: **17 mutatsiya, 17 RED, 0 GREEN, 0 o'lchanmagan,
restore YES**. Bitta qator ataylab halol yozildi: `STOP_COMMAND` nomi **konstantaning literal
pin'i** bilan tutildi, **legacy xulqi bilan emas** — qator izohi shuni aytadi. Legacy happy
path bu specda ataylab yo'q (pack papkasi, approvals store, quota jadvali kerak) va ochiq
qoladi.

### 3. Node runner Windows bloklangan yuzasi — endi qadalgan

Hujjat "11/24, **qadalmagan**" derdi. Qayta o'lchov (node v24.18.1): **31 test, 20 pass,
11 fail, 0 skip** — to'plam o'sgan, bloklangan yuza o'sha 11. Uch sabab: `execute()` Linux
bo'lmagan platformada rad etadi (5), `symlink` EPERM (2), POSIX ruxsat bitlari (4).

`apps/runner/windows-baseline.test.js` o'n bittasini sabablari bilan yozadi va **har bir
sababni haqiqatda yurgizib** tasdiqlaydi (`test_platform_baseline.py` darsi: shartning rostligi
sabab ekanini isbotlamaydi). U qo'shimcha ravishda: har bir nom `test.js`da borligini qadaydi
(qayta nomlash baseline'ni yangilashga majbur qiladi), yozilgan imzo ichki izchilligini
tekshiradi, va **POSIX'da o'sha uch amal muvaffaqiyatli bo'lishini** talab qiladi — ya'ni bu
Windows o'lchovi, umumiy bahona emas. `verify.yml` va `verify_offline.py`ga ulandi.

### Yo'l-yo'lakay: CI `core` job yig'ilmasdi

`verify.yml` `core` job'i faqat `requirements-offline.txt` o'rnatadi, unda esa `fastapi`
yo'q edi — holbuki `test_app_layer_bounds.py` va (yangi) `test_pipeline_bounds.py` modul
darajasida `fastapi` import qiladi. Import xatosi discovery'ni yiqitadi, ya'ni job **hech
qachon yashil bo'lmagan**. Tuzatish: `requirements-offline.txt`ga `fastapi>=0.115` qo'shildi
(sababi faylning o'zida yozilgan).

### Ochiq qolgan, aniq nomlangan

- `whatsapp.*` tool'ini birorta yetkazilgan pack e'lon qilmaydi → route bor, **reachability
  o'zgarmadi**; UI va live Meta acceptance yo'q.
- `app/storage.py`, `app/main.py`, `apps/runner/portable_fs.py` chegaralari hamon audit
  qilinmagan (`pipeline.py` shu sessiyada yopildi).
- Yuqoridagi `fastapi` tuzatishi **CI'da yurgizilmagan** — lokal, import zanjirini kuzatish
  bilan topilgan; CI dalili yo'q.

## §159 — Commit qilinmagan ishni tugatish: uch chegara auditi va WhatsApp reachability (2026-09-25, ikkinchi o'tirish)

### 0. Claude'ning commit qilinmagan ishi — review natijasi

`conversation_api.py` + `operator_reply.py` + `ConversationPanels.tsx` +
`conversation-client.mjs`, `erp_api.py`, `version.py`, `turkish-baby` pack, `e2e_smoke.py`,
`import_catalog.py`, `test_runner_close_codes.py`, `demo-retail` pack/persona o'zgarishlari.
Chala yoki bog'lanmagan **hech narsa topilmadi**: `ConversationPanels` `page.tsx`da
`conversations`/`handoffs` tab sifatida render qilinadi, `HandoffsPanel` va
`ReconcileControl` ham ulangan. `scripts/e2e_smoke.py` **jonli yurgizildi**: haqiqiy API +
worker + soxta model/Bot API, **15 passed, 0 failed** — conversation turn, operator
takeover, buyurtma tasdiqlash, handoff, `/orders`, 401 imzo, token sizib chiqmasligi.
Butun conversation qatlami shu bilan lokal ravishda tasdiqlandi.

### 1. `app/storage.py` — 18 test, 9/9 RED

Inline raqamlar nomlandi: `CONNECT_TIMEOUT_SECONDS = 30.0`, `DELIVERY_LEASE_SECONDS = 60`,
`MAX_DELIVERY_ERROR_CHARS = 500`. Yangi `runtime_tests/test_storage_bounds.py` (18 test)
haqiqiy SQLite faylda claim eksklyuzivligi va lease tugashini, 1 sekundlik clamp'ni,
tenant scoping'ni, attempt sanashni, xato qirqilishini va `record_inbound` idempotentligini
qadaydi. `scripts/probes/audit_storage_bounds.py`: **9/9 RED**, restore YES.

### 2. `app/main.py` — 15 test, 11/11 RED

Front-door yuzalari nomlandi: CORS origin/method/header, `MAX_TENANT_CHARS=64`, `ROLES`,
`MUTATION_ROLES`, `OWNER_ONLY_PREFIX`, `LEGACY_EXEMPT_PREFIXES`, `NO_STORE_PREFIXES`,
`LEGACY_RUNNER_PREFIX`. Yangi `runtime_tests/test_main_bounds.py` (15 test) middleware
korutinasini **qo'lda** yuritadi (Windows'da event loop — socket; offline suite taqiqlaydi),
shuning uchun 410/401/403 va no-store qarorlari offline o'lchanadi. Probe: **11/11 RED**
(uchtasi faqat xulq bilan tutadi).

Topilma: **role tekshiruvi faqat legacy rejimda yetib boriladi** — platform rejimida 410
undan oldin javob beradi. Bu testlarda yozib qo'yildi, chunki aks holda "tekshiruv bor"
degan noto'g'ri taassurot qolardi.

Ikkinchi topilma: refaktor **mavjud guard tomonidan tutildi**. `test_security_surface_v037`
`main.py`ni AST bilan o'qib, `allow_methods` qiymatini `ast.literal_eval` qilardi — qiymat
endi `CORS_METHODS` nomi bo'lgani uchun yiqildi va to'plam signaturasi `errors=13` bo'ldi.
Guard nomni ham hal qiladigan qilib tuzatildi (literal **yoki** modul konstantasi), bu esa
uni kuchaytirdi: endi u konstantaning qiymatini o'qiydi, shaklini emas.

### 3. `apps/runner/portable_fs.py` — 28 test, 11/11 RED

Validatsiya chegaralari inline edi (root 1..32, deny 100×128, yo'l 2000, stdin 64000),
nomlanganlari esa (MAX_BYTES/MAX_ENTRIES/MAX_VISITED) hech qayerda qadalmagan edi.
`test_portable_fs.py`ga `DeclaredBoundTests` (12 test, hammasi platforma-mustaqil).
Probe: **11/11 RED**.

**Birinchi o'lchovda bir qator GREEN chiqdi** ("path length 2000→20000"): uzun yo'l hamon
ValueError berardi, lekin **boshqa sababdan** (commonpath). Test aynan chegarani
o'lchaydigan qilib qayta yozildi — endi RED. §154/§155 sabog'ining yana bir uchrashuvi:
sababni emas, natijani tekshirish yetarli emas.

### 4. WhatsApp reachability — 12/88 → 17/88

`packs/turkish-baby`ga **`sales.wa_assistant`** qo'shildi: trigger `source: whatsapp`,
`ladder: human_assisted` (birinchi WhatsApp agenti — har bir erkin javob operator
tasdig'ini kutadi, oyna qoidasi jonli Meta'da sinalmaguncha), tools
`[whatsapp.send, whatsapp.window, whatsapp.templates, products.search, shop.info, orders.draft]`,
`order_agent: sales.order_taker`. Persona (`prompts/sales/whatsapp.md`) oyna qoidasini
yozadi: `whatsapp.window` → ochiq bo'lsa matn, yopiq bo'lsa `whatsapp.templates`dan shablon;
`ungrounded_numbers(prompt, "") == []` bilan qadalgan. 3 yangi pack testi — jami
`test_pack_turkish_baby` + contract **54 passed**.

O'lchov (2026-09-25): registry **88**, yetib boriladigan **17**; yetib bo'lmaydigan LOC
**10 758 / 19 182 = 56%**. Eski 81% **boshqa metodika** bilan o'lchangan (yadro modullari
hisobdan chiqarilgan) — raqamlar solishtirilmasligi hujjatlarda yozib qo'yildi.
`whatsapp.verify`/`whatsapp.webhook` ataylab yetib borilmaydi: ular ingest/operator yuzasi.

### Yakuniy o'lchov (2026-09-25)

| To'plam | Natija |
|---|---|
| `runtime_tests` | **3 569 test**, `failures=1, errors=12, skipped=1` — 13 bloklangan aynan baseline ro'yxati |
| `integration_tests` | **354/354 PASS** |
| Yangi probelar | storage 9/9, main 11/11, portable_fs 11/11, pipeline 17/17 — **hammasi RED, restore YES** |
| `e2e_smoke.py` | **15 passed, 0 failed** |

### Ochiq qolgan

- Reachability: 71 tool hamon yetib bo'lmaydi (`erp`, `documents`, `inventory`, `telephony`,
  `vision`, `manufacturing`, `oee`, `workforce`, `supervisor`, `assets`, `graph`, `crm`,
  `sheets`, `database`, `agent.*`, `voice.tts`, ...) — har biri pack qatori + konfiguratsiya.
- Live acceptance hamon yo'q; `production_release: NO_GO`.

## §160 — Oxirgi UI bo'shlig'i va yangi API yuzalari (2026-09-25, uchinchi o'tirish)

### 1. Tool chaqirish yuzasi — §10 dagi **oxirgi UI bo'shlig'i** yopildi

Inventar shunday derdi: `workforce.workload` kabi tool'lar HTTP route emas, shuning uchun
umumiy tool chaqiruvchi yuza kerak. Raw JSON reja maydoni bor edi — **qoldirildi**
(ishlaydigan yo'lni o'chirish OCP emas), endi uning yonida **katalog sxemasidan qurilgan
forma** bor:

- `apps/ui/lib/tools-client.mjs` — sxemadan maydonlar (`text` / `integer` / `boolean` /
  string-ro'yxat / JSON; `enum` → select), majburiy maydon va chegara tekshiruvi
  **tarmoqdan oldin**, va `POST /platform/{tenant}/tasks` kutgan bir qadamli body.
- Tanlov agentning **o'z siyosatidan** (`/catalog`) keladi — qattiq yozilgan ro'yxat yo'q;
  yozuv tool'i tanlansa UI "tasdiq navbatiga tushadi" deb aytadi.
- 8 node testi (`tools-client.test.mjs`); `verify.yml` va `verify_offline.py`ga ulandi.
- Python tomoni lug'atni qadaydi: `runtime_tests/test_schema_vocabulary.py` (8 test) —
  besh tur, `enum` faqat stringda, **manfiy integer minimum yo'q** (shu sababli forma
  minus belgisini xato deb rad etadi), har chegara `int`, object maydonlar o'z
  `properties`ini nomlaydi. Yangi shakl qo'shilsa, test builder'ni o'rgatishni talab qiladi.

### 2. `app/erp_api.py` — 11 test, probe **6/6 RED**

`POSTING_LIMIT=100`, `MAX_EVIDENCE_CHARS=1000`, `MAX_EXTERNAL_ID_CHARS=128` nomlandi.
`test_erp_api_bounds.py` so'rov modelini **serversiz** sinaydi (bo'sh/haddan uzun evidence,
haddan uzun external_id, yopiq outcome to'plami, ortiqcha maydon rad etiladi) va ikki
vakolat shaklini chaqiruv joyida qadaydi: o'qish — owner/operator, reconcile — faqat owner.

Guard yozayotganda topilgan nuqson: modul **docstring'i** `LIMIT 100` ni takrorlagani uchun
manba tekshiruvi **kod qismiga** cheklandi (docstring raqamni takrorlashi mumkin, kod — yo'q).

### 3. `app/shop_api.py` — 13 test, probe **9/9 RED**, bitta haqiqiy inline literal

Konstantalar (satr/matn chegaralari, kanal ro'yxati va config-blok xaritasi, ikki rol
to'plami, credential nom shakli) hech qayerda qadalmagan edi. **Manba guard'i
`pack.products[:1000]` ni topdi** — endi `MAX_PRODUCTS` (katalog sahifasi va xabar satri
boshqa savolga javob beradi, shuning uchun `MAX_SHOP_ROWS` emas, o'z chegarasi).

Uch sof helper xulqi qadaldi: `_text` (qirqish chegarasi; string bo'lmagan → bo'sh),
`_json` (tur mos kelmasa default — oqib ketmaydi), `_credential_refs` (faqat `*_env`
kalitlari; scoped token xaritasi — hujjatlashtirilgan istisno; **nom shakliga mos kelmagan
qiymat hech qachon qaytmaydi**).

### Yakuniy o'lchov (2026-09-25)

| To'plam | Natija |
|---|---|
| `runtime_tests` | **3 601 test**, `failures=1, errors=12, skipped=1` — 13 bloklangan aynan baseline ro'yxati |
| `integration_tests` | **354/354 PASS** |
| UI | typecheck **PASS**, build **PASS**, `npm audit` 0 |
| Yangi probelar | erp 6/6, shop 9/9 — RED, restore YES |

### Ochiq qolgan

- Tool yuzasi **brauzerda bosilmagan** (typecheck + unit test bor, E2E yo'q).
- Reachability: 71 tool hamon pack'siz; live acceptance yo'q; `production_release: NO_GO`.

## §161 — `agent.*` reachable + oversight catalog hook (2026-09-25, to'rtinchi o'tirish)

### 1. `agent.*` endi yetib boriladi

`turkish-baby`ning `ops.assistant`iga platformaning **o'z read-only oversight**'i qo'shildi:
`agent.activity`, `agent.cost`, `agent.health`. Boshqa frozen modullardan farqli o'laroq bu
modul **hech qanday konfiguratsiya talab qilmaydi** (connection va register yo'q — platforma
o'zi yozgan jadvallarni o'qiydi), shuning uchun pack e'loni hollow claim emas:
`integration_tests/test_tool_surface_contract.py` haqiqiy HTTP orqali read'ni uchi-uchiga
yurgizadi (`engine.tick` → step `succeeded`, `result['agent'] == 'sales.assistant'`), va
`agent.activity` chegaralari server tomonda sinaladi (chegarada 200, chegaradan oshsa 422).

### 2. Yo'l-yo'lakay **haqiqiy nuqson**: oversight'ning "noma'lum agent" himoyasi o'lik edi

Modul hujjati aytadi: *"an unknown agent is a 404-style refusal rather than an empty
activity list"*. Ammo `_known` buni faqat `engine.agent_catalog` hook'i mavjud bo'lganda
qiladi (aks holda "cannot enumerate" fallback — faqat id **shakli** tekshiriladi), va
**`app/`dagi hech narsa bu hook'ni o'rnatmagan**: `api.engine()` qo'ymasdi, ya'ni
productionda har bir noma'lum agent **nol ko'rsatkichli hisobot** bo'lib qaytardi.
Runtime testlari esa o'z Engine'lariga hook'ni **qo'lda qo'yib**, himoyani "bor" deb
ko'rsatardi — guard'ning o'zi hech qachon o'tkazib yuborilmagan edi.

Tuzatish: `api.engine()` endi `e.agent_catalog = agents` (pack ro'yxati) qo'yadi; pack
yuklanmasa runtime ichidagi `None` fallback ishlaydi (crash emas). **Qadash:**
`test_the_shipped_engine_wires_the_oversight_catalog` **haqiqiy** `api.engine()`ni chaqirib
hook va ro'yxatni tekshiradi; `test_an_unknown_target_agent_is_refused_by_the_read_itself`
typo uchun step `failed`/`Forbidden` bo'lishini talab qiladi (engine xabarni emas, istisno
**sinfini** yozadi — bu ham testda izohlangan).

### 3. O'lchov metodidagi xato tuzatildi: **12/89 va 17/88 noto'g'ri edi**

Yetib borish raqamlari **bare** `build_registry()` bilan o'lchangan edi — u esa xost bergan
3 shop tool'ini (`products.search`, `shop.info`, `orders.draft`) ro'yxatga olmaydi, ya'ni
ular **reachable bo'la turib** hisobga olinmagan va **maxraj ham xato** edi. To'g'ri metod —
engine o'zi ishlatadigan registry: `build_registry(catalog, shop_data)` = **91** tool
(`known_tool_names()` ham 91). Shu bilan (2026-09-25):

- **reachable: 20 / 91**
- yetib bo'lmaydigan LOC: **10 447 / 19 182 = 54%**, **14** modul (`oversight` ro'yxatdan chiqdi)

### Yakuniy o'lchov

| To'plam | Natija |
|---|---|
| `runtime_tests` | **3 601** — app qatlamidagi hook tahriri runtime to'plamiga tegmadi |
| `integration_tests` | **362/362 PASS** (354 + 8 yangi) |

### Ochiq qolgan

- Qolgan frozen modullar hamon **konfiguratsiya talab qiladi** (`inventory` ikki qavatli:
  graph + connection; `crm`/`sheets`/`database` connection; `telephony`/`workforce`/`oee`
  register) — ularni pack'ga qo'yish "e'lon bor, konfiguratsiya yo'q" holatini beradi.
- Live acceptance yo'q; `production_release: NO_GO`.

## §162 — Offline gate: "qizil" emas, **qayd etilgan platforma yuzasi** (2026-09-25, beshinchi o'tirish)

`verify_offline.py` Windows'da **har safar** FAIL berardi: `python_runtime` va `node_runner`
platforma sababli qizil, gate esa faqat `exit 0` ni biladi. **Har doim qizil gate hech qanday
signal bermaydi.** Endi bar — **qayd etilgan yuza**: daraxtda allaqachon ikki rekord bor
(`test_platform_baseline.BLOCKED` — 13 test sababi bilan; `windows-baseline.test.js` — 11),
gate ularni **o'qiydi** (nusxa saqlamaydi) va qizil to'plam rekord ichida bo'lsa
`PASS_WITH_RECORDED_BLOCKED` deb yozadi; ro'yxatdan tashqari **har qanday yangi qizil = FAIL**.
Bloklangan testning o'tib ketishi mumkin (rekordning o'z qo'riqchisi sabablarni qayta o'lchaydi).

### Yo'l-yo'lakay topilgan **uchta haqiqiy nuqson** — uchtasi ham "gate o'zini o'lchamayapti" sinfidan

1. **Rad etish (refusal) noto'g'ri muhitni o'lchagan.** Parent `find_spec` bilan
   `fastapi/pydantic/yaml/jwt/cryptography` bor deb tasdiqlardi, izolyatsiyalangan child esa
   ularning **hech birini ko'rmasdi** — bu hostda paketlar **user site**'da (`%APPDATA%`),
   gate esa `APPDATA`ni child'ga uzatmasdi. Natija: `python_runtime` 3601 o'rniga **3339 test,
   errors=172** — import xatolari **test qizilligi** sifatida dalilga yozilardi. Tuzatish:
   `APPDATA` passthrough, `PYTHONIOENCODING=utf-8` va **`child_imports` probe** — rad etish
   endi ishlar yuguradigan muhitda o'lchanadi va dalil papkasi yaratilishidan **oldin**.
2. **Node logi mojibake edi.** Parent cp1251 bilan dekodlagani uchun Node'ning ✖ belgisi
   `вњ–` bo'lib yozilardi → yuza tekshiruvi uni ko'rmasdi. Tuzatish: child UTF-8 yozadi,
   parent `encoding='utf-8'` bilan o'qiydi.
3. **Ikki parser nuqsoni**: (a) bir qatorli `ERROR: short (to'liq.id)` shakli o'qilmasdi
   (faqat ko'chirilgan qator o'qilardi); (b) `✖ failing tests:` **sarlavhasi** test nomi
   deb o'qilardi. (c) qo'shimcha: ish `discover -s runtime_tests` bilan yurgani uchun id'lar
   `runtime_tests.` prefiksisiz keladi, rekord esa prefiks bilan — taqqoslash normallashtirildi
   (`without_package_prefix`).

**Muhim halollik qaydi:** birinchi yurish loglari (`local-20260926T060200214320Z`,
`local-20260926T060743536197Z`) **saqlanadi** — ular nuqsonlarni topgan yurishlar. Gate
o'sha loglarga nisbatan **FAIL** berishda davom etadi (tekshirildi): 172 import xatosi
rekorddan tashqarida.

### Yangi testlar

`scripts/test_verify_offline.py` — **22 test**: rekordlar daraxtdan o'qiladi (13/11), qayd
etilgan yuza qabul qilinadi, yangi qizil / o'qilmaydigan hisobot / yo'q rekord **rad etiladi**,
ikki unittest shakli va node sarlavhasi, child muhiti (`PYTHONIOENCODING`, `APPDATA`) va
**child probe** (haqiqiy o'lchov + yo'q modul rad etilishi). CI'ga yangi qadam qo'shildi.

### Natija — dalil: `docs/verification/local-20260926T061554347843Z/`

**`EXIT 0`** — gate bu hostda birinchi marta **o'zi haqidagi da'voni o'lchadi**:

| Job | Natija |
|---|---|
| `python_runtime` | **PASS_WITH_RECORDED_BLOCKED** — 3 601 test, **13 qayd etilgan** |
| `node_runner` | **PASS_WITH_RECORDED_BLOCKED** — 36 test, **11 qayd etilgan** |
| `release_tools` · `manifest_tools` · `sqlite_demo` · `managed_database_demo` · 4 brauzer klienti · `python_syntax` | **PASS** |
| `javascript_typescript_syntax` | BLOCKED (bun o'rnatilmagan — ixtiyoriy) |

Ya'ni: **yangi qizil = FAIL**, qayd etilgan platforma yuzasi = PASS.



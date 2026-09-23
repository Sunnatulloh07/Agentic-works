# QOLGAN ISHLAR — to'liq inventar (2026-09-20)

> Bu hujjat **o'lchovga** asoslangan, taxminga emas. Har bir da'vo registry,
> fayl tizimi yoki test to'plamidan o'qildi. Raqamlar sana bilan belgilangan.
>
> **Yangilandi: 2026-09-21 (kechki sessiya)** — UI qatlami, chegara auditi §151–§154,
> `retry.py`, DR drill, manifest generator, `integration_tests` tuzatildi.
> Batafsil: `PROGRESS-UZ.md`.
>
> **Yangilandi: 2026-09-22** — chegara auditi §155–§157 (`app/` qatlami, tasdiq
> navbati va avtonomiya zinapoyasi, Customer 360), `MANIFEST` portativligi,
> `verify_offline.py` qo'riqchisi. Batafsil: `PROGRESS-UZ.md` V05Z–V063.
>
> **Yangilandi: 2026-09-22 (chuqur ko'rib chiqish)** — **yetib borish (reachability)**
> o'lchandi, first-run yo'li **o'lik** ekani aniqlandi, `tests/` nafaqaga chiqarish
> qaroriga o'tdi. Quyidagi §0 va §11 jadvallari shunga ko'ra tuzatildi.

## 0. Hozirgi holat — bir qarashda

| Ko'rsatkich | Qiymat | Manba |
|---|---|---|
| Runtime modullari | **43** fayl (retry.py qo'shildi) | `api-python/platform_runtime/` |
| Registry tool'lari | **88** (known **89**) | `build_registry()` |
| **Pack'dan yetib boradigan tool'lar** | **12** — 89 dan | yetkazilgan pack'lar: `demo-retail`, `marketing`, `_template` |
| **Yetib bo'lmaydigan `platform_runtime` LOC** | **81%** | shu uch pack'dan chaqirib bo'lmaydi |
| Backend API route'lari | **54** (platform) + **13** (identity) + **7** (oauth) + **4** (google) | `grep -c '^@router\.'` |
| `app/` modullari | **40** fayl | `api-python/app/` |
| Offline testlar | **3 148** | `Ran 3148 tests` |
| Test signature | `failures=1, errors=12, skipped=1` | **boshqariladigan venv bilan** (`cryptography` + `tzdata`) |
| Windows uchun **bloklangan** sinovlar | **13** (12 error + 1 failure) | `runtime_tests/test_platform_baseline.py` |
| Ultra-audit fazalari | **§125–§157** — 33 ta raqamlangan, uzluksiz | `ULTRA-AUDIT-ASCII-CELL-UZ.md` |
| UI gate'lari | typecheck **PASS**, build **PASS** (`next@14.2.35`), `npm audit` **FAIL** | `apps/ui` |
| `MANIFEST.sha256` | **PASS** — 510 fayl, 0 xato; **va endi toza LF eksportda ham PASS** (§155.12) | `scripts/verify_manifest.py` |
| **API bu mashinada ishga tushirilganmi** | **YO'Q** | `.env` yo'q, `config/integrations.json` yo'q, `data/app.db` da faqat migratsiya qatori |
| **README first-run yo'li** | **O'LIK edi, tuzatildi** | `setup_local.py` → `owner_login.py` = 410 + 403; yagona yo'l `provision_identity.py` |
| `production_release` | **NO_GO** | `BACKLOG.json` |

**Muhim:** `production_release: NO_GO` — bu **ataylab**. Tizim hozir
"mijozga topshiriladigan mahsulot" emas, "tekshirilgan platforma yadrosi".

**Ikkinchi muhim gap — yetib borish.** Registry'dagi 89 tool'dan **12 tasigina**
yetkazib berilayotgan pack'lardan chaqirilishi mumkin. Quyidagi modullarni
ishlatadigan **birorta pack yo'q**: `erp`, `documents`, `inventory`,
`business_graph`, `whatsapp`, `whatsapp_inbound`, `telephony`, `assets`, `vision`,
`manufacturing`, `oee`, `workforce`, `supervisor`, `reengagement`, `escalation`,
`briefing`, `oversight`. WhatsApp inbound uchun **umuman HTTP route yo'q**.

Bular **muzlatilgan (frozen) preview modullar** — mahsulot funksiyasi emas. Ular test
va chegara auditi bilan qoplangan, lekin hech bir mijoz konfiguratsiyasi ularga yetib
bormaydi. Bu hujjatda ular "qurilgan" deb sanaladi, va bu to'g'ri — lekin "qurilgan"
"yetkazilgan" degani emas.


---

## 1. BACKEND — holat

### Qurilgan (LOCAL_CONTRACT_TESTED)

**Yadro:**
- `engine.py` — tranzaksiya, tenant izolyatsiyasi, approval gate, audit
- `tools.py` — registry, schema validatsiya, 88 tool
- `cells.py` — ASCII-cell invarianti (I faza tuzatishi)
- `agent_loop.py`, `agent_planner.py` — agent bajarish sikli

**Biznes modullari (hammasi test + probe bilan; hammasi ham `FROZEN_PREVIEW` — §0 ga
qarang: birorta yetkazilgan pack ularni chaqirmaydi):**
- `documents.py` — invoice intake, match, fraud signals, posting plan
- `erp.py` — posting prepare/status/submit
- `inventory.py` — MoySklad narx/qoldiq/marja
- `manufacturing.py`, `oee.py` — BOM, tsikl, yield, andon
- `assets.py` — asset modeli (namuna modul)
- `business_graph.py` — entity/grafik qidiruv
- `workforce.py` — smena, davomat, yuklama
- `oversight.py` — agent faoliyati, audit o'qish
- `supervisor.py` — router va sections
- `escalation.py`, `reengagement.py`, `briefing.py`

**Connector'lar:**
- `connectors.py`, `connector_contract.py`, `connector_authority.py`
- `postgres_connector.py`
- `google_adapters/sync/reconcile/oauth.py`
- `sheets.py`, `whatsapp.py`, `whatsapp_inbound.py`, `telephony.py`

**Agent/LLM:**
- `llm.py`, `model_transport.py`, `model_response.py`
- `usage_budget.py`, `secret_vault.py`, `speech.py`, `vision.py`

### ⚠️ Ochiq (backend)

| ID | Holat | Qolgan ish |
|---|---|---|
| `voice_call_processing` | **TODO** | STT, diarization, natijani ajratish, CRM timeline |
| `production_operations` | **TODO** | HA, observability, egress, DR, real acceptance |
| `knowledge_full_rag` | **PARTIAL** | Parser, embedding, evals (matn/BM25 bor) |
| `identity_mfa_recovery` | **PARTIAL** | Lifecycle, MFA, recovery, OIDC |
| `billing` | **TODO** | Usage budget ≠ obuna/hisob-faktura |
| `voice` | **PARTIAL** | Public pipeline yo'q |
| `crm_reengagement_feed` | **SOURCE_ONLY** | Rejalashtirilgan AgentLoop'ga ulash yo'q |
| `google_sync_walkers` | **LOCAL_CONTRACT_TESTED_BOUNDED** | Chegaralangan |

### Yo'q (backend)

- **HA / klaster** — bitta instance uchun yozilgan
- **Observability** — metrika, trace eksport, alerting
- **DR (disaster recovery)** — backup/restore mashqi yo'q
- **Rate-limit retry** — provayder 429/5xx uchun backoff yo'q
- **Real provayder acceptance** — MoySklad, 1C, Meta, Google: `live_verified: False`.
  Bu **hammasiga** taalluqli: API bu mashinada hech qachon ishga tushirilmagan
  (`.env` yo'q, `config/integrations.json` yo'q), shuning uchun **hech bir**
  integratsiyani `live_verified` deb yozib bo'lmaydi — eng yuqorisi
  `LOCAL_CONTRACT_TESTED`
- **WhatsApp inbound HTTP route** — modul bor, **route yo'q**

---

## 2. AGENTING — holat

### Qurilgan

- **Agent loop**: `agent_loop.py` + `agent_planner.py` — reja → qadam → approval → bajarish
- **Ladder tizimi**: `autonomous` / `human_assisted` / `human_led`
- **Approval gate**: har bir `write` tool engine tomonidan ushlanadi. **2026-09-22 dan
  bitta istisno:** `autonomous` agent outbound write'ni per-message tasdiqsiz yuborishi
  mumkin, faqat qabul qiluvchi pack'ning `allowed_recipients` ro'yxatida bo'lsa yoki task
  javob berayotgan tasdiqlangan inbound suhbat bo'lsa (`engine.py::_preauthorized`).
  `destructive` va `physical` **har doim** kutadi; `human_led` / `human_assisted`
  o'zgarmadi
- **Brifing va eskalatsiya** endi o'z transportiga ega emas: ikkalasi ham
  `engine.submit` orqali oddiy engine task'i sifatida yuboriladi va approval gate'ini
  **chetlab o'tmaydi**; jurnaldagi `submitted` qatori engine verdikti bilan yopiladi
- **Pack tizimi**: `app/packs.py` — YAML'dan agent yuklash, load-time tool tekshiruvi
- **Supervisor router**: `supervisor.route` — bo'limga yo'naltirish
- **Usage budget**: `usage_budget.py` — LLM xarajat byudjeti + reservation

### Paketlar (2 ta)

| Paket | Agentlar | Holat |
|---|---|---|
| `marketing` | 4 ta (content_editor, ad_creative, researcher, script_doctor) | YAML bor, ishlaydi |
| `demo-retail` | 6+ ta (responder, order_taker, stock_answerer, branch_router, hot_lead, daily_stats) | YAML + prompt bor |
| `_template` | — | Boshlang'ich shablon |

### ⚠️ Ochiq (agenting)

- **Veb-qidiruv adapteri yo'q** — `marketing.researcher` faqat yuklangan bilim bazasidan ishlaydi (pack'da izohlangan)
- **Agentlar orasidagi delegatsiya** — supervisor bor, lekin agent→agent topshiriq uzatish cheklangan
- **Agent xotirasi** — `memory.put` / `memory.search` bor, lekin uzoq muddatli xotira strategiyasi yo'q
- **Retry/self-correction** — qadam yiqilganda avtomatik qayta rejalashtirish yo'q
- **Real acceptance** — agentlar demo ma'lumotda sinalgan, haqiqiy mijozda emas

---

## 3. CONNECTORS — holat

### Qurilgan (driver'lar)

| Driver | Modul | Holat |
|---|---|---|
| `sqlite_readonly` | `connectors.py` | LOCAL_TESTED |
| `postgres` | `postgres_connector.py` | LOCAL_TESTED |
| `custom_http` (1C) | `connectors.py` | LOCAL_CONTRACT_TESTED |
| Google (Gmail/Drive/Calendar) | `google_*.py` | LOCAL_CONTRACT_TESTED |
| Google Sheets | `sheets.py` | LOCAL_CONTRACT_TESTED |
| MoySklad | `inventory.py` | LOCAL_CONTRACT_TESTED |

**Xavfsizlik:** `connector_authority.py` + `connector_contract.py` — har bir
connector uchun ruxsat oq ro'yxati; fail-closed.

### ⚠️ Ochiq (connectors)

- **Schema drift detection** — `response_map` bor, lekin provayder sxemasi
  o'zgarganda avtomatik aniqlash **yo'q** (ochiq risk #2)
- **Health probe** — bor, lekin davriy emas
- **Rate-limit handling** — provayder 429 uchun backoff yo'q
- **Live acceptance** — hech bir provayderda `live_verified: True` emas
- **Write-back** — ERP'ga yozish mock'dan tashqariga chiqmagan
  (`erp_sync_beyond_mock_post`)

---

## 4. ADMIN PANEL — holat

### ⚠️ QISMAN QURILDI (2026-09-21)

**Mavjud:** `apps/ui/components/AdminPanel.tsx` — hisob va a’zolik boshqaruvi:
sessiyalar ro‘yxati, `logout-all`, workspace ro‘yxati va almashtirish, taklifnoma
yaratish (rol tanlash bilan), taklifnomani qabul qilish, a’zolikni bekor qilish.
`BootstrapPanel` — birinchi ownerni `X-Admin-Token` bilan yaratish.

**Hali yo‘q:**
- Foydalanuvchi CRUD (ro‘yxat, tahrirlash, o‘chirish)
- Rol/ruxsat tahrirlash ekrani
- Tenant yaratish/o‘chirish UI (`POST /identity/workspaces` bor, UI yo‘q)
- Tizim sozlamalari ekrani
- Byudjet/kvota boshqaruvi (qisman — `BudgetPanel` bor)
- Provayder kalitlarini kiritish UI (ataylab yo‘q — kalit env/vault’da)
- Metrika/health dashboard — **qisman**: `MetricsPanel` health va navbat hisoblarini
  ko‘rsatadi, lekin metrika eksporti/trace/alerting **yo‘q** va panel buni aytadi

**Izoh:** a’zolar ro‘yxati route’i yo‘q, shuning uchun «a’zoni bekor qilish» maydoni
erkin matn (foydalanuvchi ID). Bu ma’lum cheklov, panelda yozilgan.

---

## 5. TENANT PANEL — holat

### ⚠️ QISMAN — operator paneli ichida

**Bor:** platform page ichida tenant-scoped ko‘rinishlar:
- **Mijozlar 360** — mijoz, kontakt, kanal identity, buyurtmalar (**endi yozish ham bor**)
- **Connections** — connector ro‘yxati, verify, lifecycle
- **Audit** — tenant audit oqimi
- **Usage budget** — `/usage-budget` route bor
- **Qayta aloqa / Brifing / Eskalatsiya / Supervisor** — **yangi**, to‘liq CRUD + jurnal
- **Takrorlanuvchi jadval** — yangi

**Nima yo‘q:**
- Tenant **o‘z-o‘zini** boshqarish (self-service onboarding)
- Tenant sozlamalari ekrani (valyuta, til, ish vaqti, ES lat)
- Tenant hisoboti / eksport
- Tenant foydalanuvchilarini boshqarish (a’zolar ro‘yxati route’i yo‘q)
- Ko‘p-tenant almashtirish — **endi bor** (Admin panelda)

---

## 6. XODIMLAR PANELI — holat

### ⚠️ YO‘Q (backend qismi bor)

**Backend TAYYOR:**
- `workforce.py` — 3 tool: `workforce.shifts`, `workforce.attendance`,
  `workforce.workload`
- Google Sheets'dan smena register'ini o'qish
- `oversight.activity` — xodim/agent faoliyati

**UI YO‘Q** — bu sessiyada **qo‘shilmadi**; qolgan ish ro‘yxatida qoladi.


---

## 7. Boshqa panellar / qurilmalar

| Element | Holat | Qolgan |
|---|---|---|
| `ui_full_product` | **PARTIAL (yaxshilandi)** | 27 route ulandi; xodimlar paneli, self-service, eksport yo‘q |
| `oauth_api_ui` | **LOCAL_TESTED** | typecheck **PASS**, build **PASS**, node testlari **PASS**; browser E2E hali **NOT_RUN** |
| `mac_desktop` | **PARTIAL** | Read-only preview; native installer yo'q |
| `windows_desktop` | **TODO** | Runner + native adapterlar |
| `telephony_outbound` | **STAGE_C_LOCAL_CONTRACT_TESTED** | Dialer, SIP, codec, concurrent slot |
| `wa_window_until_graph_attribute` | **INVESTIGATED_REFUSED** | Ataylab rad etilgan |
| `ui_dependency_security` | **FAIL (yangi)** | `next@14.2.35` da 1 critical + 1 high; tuzatish **Next ≥15.5.24 + React 19** talab qiladi |


**`windows_desktop` muhim izoh:** hozirgi lokal-executor xavfsizlik shartnomasi
**POSIX-only** (`O_NOFOLLOW`, `mkfifo`, `0600` single-owner). Windows uchun
**alohida ownership modeli** kerak — shunchaki port qilish xavfsizlik teshigi
ochadi.

---

## 8. Offline testlar — nima **NOT_RUN**

`NOT_RUN` ro'yxati (ataylab ochiq qoldirilgan):

- React typecheck / build / browser E2E
- Haqiqiy provayder acceptance (MoySklad, 1C, Meta, Google, Telegram)
- STT/TTS live sifat
- Vision live (rasm hech qachon platformaga tushmaydi — strukturaviy)
- Audio live (hech qachon — strukturaviy)
- Ko'p-instansiyali HA sinovi
- Yuklama (load) sinovi
- Xavfsizlik penetratsiya sinovi

---

## 9. Ultra-audit — bajarilgan fazalar

Raqamlangan fazalar **§125–§156** — **32 ta**, uzluksiz, bo'shliqsiz
(`grep -c '^## §'`). Har bir fazaning usuli bir xil: chegarani **o'lchash**, uni
**mutatsiya** qilib ko'rish, yashil qolganini **qadash**. To'liq ro'yxat va
o'lchovlar hujjatda; eng ko'p uchraydigan naqshlar quyida.

> Ilgari bu yerda "45 faza (§1–§154)" va "47 faza" yozilgan edi — §0 va §9
> **bir-biriga zid** raqamlar berardi va ikkisi ham o'lchanmagan edi. Endi
> raqam hujjatdan sanaladi, taxmin qilinmaydi.

| Faza | Mavzu | Topilgan nuqson |
|---|---|---|
| I | ASCII-cell invarianti | 13 modulda `\d` Unicode raqamlarni o'qigan |
| II | `truncated` hukmi | 7 joyda `len >= limit` — kesilgan va to'liq bir xil |
| III | `count` vs sahifa | `erp.posting_status` sahifa hajmini jami deb bergan |
| IV–VII | `fraud_signals` arifmetikasi | mediana median emas; birlik xatosi; o'lik akkumulyator |
| VIII–IX | sukut chegaralari va bayroq hosilasi | bayroqning hosilasi uni o'lchaydigan testni yashirgan |
| X–XVII | istisno turlari, mapper, throttle, OAuth muddatlari | kontraktdan tashqari istisno turi |
| XVIII–XXIV | qurilma kaskadi, `generation`, oynalar | jamlanuvchi hisob yozuvlar orasida oqib ketgan |
| XXV–XXXIII | ERP, ifodalab bo'lmaydigan sonlar, navbat shifti | `math.isfinite(10**400)` qo'riqchini o'ldirgan |
| XXXIV–XLII | connector, knowledge, vision, aktivlar, sheets, CRM, qayta aloqa, supervisor | `truncated` **yo'qotishni** inkor qilgan; to'rt adapter bir chegarani to'rt xil yozgan; o'n birga surilgan shift |
| §148–§150 | OAuth, registry (`tools.py`), vault | chaqiruvchi xatosi kripto xatosi bo'lib qolgan |
| §151 | tashqi provayder transporti (4 modul) | **client id shakli hech narsa bilan qadalmagan** — qoidani o'chirish mumkin edi |
| §152 | planner, speech, baza o'qish, CRM reconcile | 28/28 RED — bitta ham yashil yo'q |
| §153 | boshqaruv tekisligi (`platform_api.py`, 88 `Field`) | **avtonomiya shiftlari 10× kengaytirilsa ham birorta test qizarmasdi**; uchta konstanta birlashib ketgan |
| §154 | Windows uchun bloklangan yuza | yozuv **11** der edi, o'lchandi — **12** |
| §155 | `app/` qatlami, o'qilmaydigan to'plam, va **ikki gate** | `tests/` da qadalgan chegara qadalgan emas (uni **hech bir gate yurgizmaydi**); §154 ning o'z yozuvi **13** va **6** bo'lishi kerak edi; `MANIFEST` **toza eksportda 403/498 mismatch** — gate o'zi aytgan joyda qizil edi; `verify_offline.py` **tugata olmaydi** edi, va men unga socket soldim |
| §156 | tasdiq navbati va avtonomiya zinapoyasi | `LadderStore` va `FileApprovalStore` ning **gate ichida birorta iste'molchisi yo'q** — yagona qoplama gatesiz, 33 qizil `tests/`; `MIN_WINDOW` — ikkinchi yalang'och `30`, va undan past oyna **ko'tarilishni imkonsiz** qiladi (o'lik qoida, hech narsa ko'tarmaydi); siyosat uchligi konstruktor default'i edi (`min_tasks=1` — bitta vazifadan keyin avtonomiya); telefon maskasi **ikki tomondan** sizadi; `verify_offline.py` noto'g'ri interpreter bilan **164 xato** beradi va buni **aytmaydi** |
| §157 | Customer 360, **ikki yurishli** matritsa | `{1,128}` va `maximum=128` — **bir son ikki joyda**, qaysi biri tor bo'lsa jimgina o'sha yutadi (regex endi konstantadan); dominat shiftlar **to'plam emas, sabab bilan** qadalandi; `status!='deleted'` — **API orqali yetib bo'lmaydigan** to'rt qo'riqchi (qator SQL bilan ekildi); `WRITE_ROLES` — `ROLES` dan bexabar inline subset edi; **birinchi yurishda 60 dan 4 tasi GREEN** — istisno turi rad etishning isboti emas (mutatorlar `get_customer` qaytaradi), ikkinchi yurishda **60/60 RED** |

**Eng muhim o'lchov (IV faza):** eski kodda **600 000 – 899 999 so'm**
(haqiqiy 3x–4.5x mediana) invoice'lar **`ready_for_approval`** qaytarardi —
tekshiruv butun oraliqda o'tkazib yuborilgan.


---

## 10. Xulosa — uchta savolga aniq javob

**"Nima qoldi?"** — Backend yadrosi tugallangan va testlangan. Qolgan ish
**mahsulotlashtirish** va **qattiqlashtirish**.

**"Nimalar ochiq?"** — 8 ta ochiq risk (`BACKLOG.json`), 11 ta TODO/PARTIAL item.

**"Nima qurilmagan?"** — UI qatlami **asosan qurildi** (§151–§152 sessiyasida):

| Yuza | Holat |
|---|---|
| Admin panel (sessiyalar, workspace, taklifnomalar) | **bor** — `AdminPanel.tsx` |
| Birinchi owner bootstrap | **bor** — `BootstrapPanel` |
| Tenant panellari (qayta aloqa, brifing, eskalatsiya, supervisor, jadval, metrika) | **bor** — `OperationsPanels.tsx` |
| Mijoz resurslari va reconcile boshqaruvi | **bor** — `CustomerResourcesPanel`, `ReconcileControl` |
| OAuth / Google / agent-run yuzalari | **bor** |
| **Xodimlar (`workforce`)** | **yo'q** — bu HTTP route emas, **tool** (`workforce.workload`), shuning uchun tool chaqiruvchi umumiy yuza kerak |
| **Windows desktop** | **yo'q** — POSIX shartnomasi to'sqinlik qiladi (`O_NOFOLLOW`), §154 |

Ya'ni qolgan UI ishi — bitta **tool chaqirish yuzasi**, alohida panel emas.

**Eng muhim xulosa:** backend **yadrosi** va agenting yozilgan va testlangan, lekin
`production_release: NO_GO`. Ikki sabab, ikkalasi ham kod sifati emas:

1. **Operatsion qattiqlashtirish yo'q** — HA, observability, DR, live acceptance.
2. **Yetib borish bo'shlig'i** — 89 tool'dan 12 tasi, `platform_runtime` kodining
   19% qismi yetkazilgan pack'lardan chaqiriladi. Qolgan 17 modul **muzlatilgan
   preview**, mahsulot funksiyasi emas (§0).

Bunga qo'shimcha, **birinchi ishga tushirish yo'li o'lik edi**: README'dagi
`setup_local.py` → `owner_login.py` ketma-ketligi 410 va 403 bilan to'xtardi, UI'da
token maydoni esa umuman yo'q. Yagona ishlaydigan yo'l — `scripts/provision_identity.py`.
README va `docs/ONBOARDING-UZ.md` shunga ko'ra qayta yozildi. Ya'ni «kod tayyor, faqat
yetkazib berish qoldi» degan eski xulosa **to'liq emas** edi: mahsulotga kirish eshigi
ham yopiq turgan.

---

## 11. Test infratuzilmasi — o'lchangan holat (§154–§157)

| Savol | Javob |
|---|---|
| Offline to'plam (`runtime_tests`) | **3 148 sinov**, `failures=1, errors=12, skipped=1` |
| Windows uchun bloklangan | **13** (12 error + 1 failure) — `test_platform_baseline.py` qadaydi |
| Eng katta **yagona** sabab | `os.O_NOFOLLOW` — **6 tasi** |
| Ikkinchi sabab (nomi yo'q edi) | `symlink_privilege` (`OSError`, `WinError 1314`) — **2 tasi** |
| Sabab **haqiqatan sabab**mi? | ha — `test_each_recorded_reason_is_the_actual_cause` har bir bloklangan sinovni **yurgizib**, ko'tarilgan istisnoni qatordagi sababga solishtiradi |
| `integration_tests` | **95 sinov, 95 pass, 50 s** — ilgari **yig'ilmasdi** (`conftest.py` qo'shildi) |
| `integration_tests` gate'da | **yo'q** — FastAPI/HTTPX talab qiladi, offline Computer'da yurmaydi |
| **`api-python/tests/`** | **33 qizil, 172 pass, 4 skip** — va uni **hech bir gate yurgizmaydi**. Qaror qabul qilindi: **nafaqaga chiqarish jarayonda** |
| CI `http` job | endi **yig'iladi** — `integration_tests/conftest.py` `ENV` va `ALLOW_INSECURE_DEV` ni qo'yib, collection ordering bog'liqligini yo'q qildi |
| CI `ui_dependency_security` job | **hamon FAIL** — `next@14.2.35` da 1 critical + 1 high; Next 15 + React 19 kerak |
| POSIX'da yurishimi | CI (`ubuntu-latest`) shu 13 sinovni **yurgizadi**; lokal POSIX o'lchovi olinmagan |
| **Node runner** (`apps/runner/test.js`) | **11 / 24 qizil** Windows'da — `privateFile()` `(mode & 0o077) === 0` ni talab qiladi, Windows POSIX ruxsat bitlarini modellashtirmaydi. **Qadalmagan** |
| `verify_offline.py` | endi **tugatadi** (§155) va noto'g'ri interpreter bilan ishga tushirilsa **rad etadi** (§156.10); Windows'da `python_runtime` va `node_runner` FAIL — **platforma**, kod emas |
| `test_customer360_bounds.py` (§157) | **72 sinov** (77 subtest), yashil; `scripts/probes/audit_customer360_bounds.py` — **ikki yurishli** matritsa (52 o'z moduli + 8 mustaqil iste'molchi), **60/60 RED** |
| Umumiy baseline fixture (deyarli 3 daqiqalik to'plam uchun) | **hali yo'q** |

**`tests/` — alohida gap.** U `tests/` nomi bilan yuradi, lekin
`.github/workflows/verify.yml` faqat `runtime_tests` va `integration_tests` ni
yurgizadi. Ya'ni 33 qizil sinov hech kimga ko'rinmaydi, va **o'sha to'plamdagi
qadam o'qilmaydi** (§155). Uning qizilligi tasodifiy emas: 10 tasi ataylab
bekor qilingan marshrut (`410 Gone`), 15 tasi eskirgan javob shakli, 4 tasi
legacy runner WebSocket, 3 tasi kontrakt surilishi, 1 tasi auth statusi.
Qaror qabul qilindi: **nafaqaga chiqarish** (*retirement in progress*). Bu to'plamning
natijasi acceptance sifatida ishlatilmaydi va uning qizilligi yangi regressiya deb
hisoblanmaydi.


# QOLGAN ISHLAR — to'liq inventar (2026-09-20)

> Bu hujjat **o'lchovga** asoslangan, taxminga emas. Har bir da'vo registry,
> fayl tizimi yoki test to'plamidan o'qildi. Raqamlar sana bilan belgilangan.
>
> **Yangilandi: 2026-09-21** — optimallashtirish sessiyasidan keyin.

## 0. Hozirgi holat — bir qarashda

| Ko'rsatkich | Qiymat | Manba |
|---|---|---|
| Runtime modullari | **42** fayl, **16 226** satr | `api-python/platform_runtime/` |
| Registry tool'lari | **88** (known **89**) | `build_registry()` |
| Backend API route'lari | **54** (platform) + **14** (identity) + **9** (oauth) + **4** (google) | `grep -c '^@router\.'` |
| `app/` modullari | **40** fayl, **4 483** satr | `api-python/app/` |
| Offline testlar | **2 784** | `Ran 2784 tests` |
| Test signature | `failures=1, errors=11, skipped=1` | **boshqariladigan venv bilan** o'lchanadi (`cryptography` bor); yalang'och interpretatorda 138 `VaultError` qo'shiladi |
| `production_release` | **NO_GO** | `BACKLOG.json` |

**Muhim:** `production_release: NO_GO` — bu **ataylab**. Tizim hozir
"mijozga topshiriladigan mahsulot" emas, "tekshirilgan platforma yadrosi".

---

## 1. BACKEND — holat

### Qurilgan (LOCAL_CONTRACT_TESTED)

**Yadro:**
- `engine.py` — tranzaksiya, tenant izolyatsiyasi, approval gate, audit
- `tools.py` — registry, schema validatsiya, 88 tool
- `cells.py` — ASCII-cell invarianti (I faza tuzatishi)
- `agent_loop.py`, `agent_planner.py` — agent bajarish sikli

**Biznes modullari (hammasi test + probe bilan):**
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
- **Real provayder acceptance** — MoySklad, 1C, Meta, Google: `live_verified: False`

---

## 2. AGENTING — holat

### Qurilgan

- **Agent loop**: `agent_loop.py` + `agent_planner.py` — reja → qadam → approval → bajarish
- **Ladder tizimi**: `autonomous` / `human_assisted` / `human_led`
- **Approval gate**: har bir `write` tool engine tomonidan ushlanadi
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

### ⚠️ ASOSAN YO'Q

**Mavjud bo'lgan narsa:** `apps/ui/app/platform/page.tsx` (166 satr) — bu
**operator** paneli, admin paneli emas. 8 ta tab:

`tasks`, `agents`, `inbox`, `audit`, `connections`, `customers`, `customer`,
`leads`

**Nima yo'q:**
- Foydalanuvchi boshqaruvi (CRUD)
- Rol/ruxsat tahrirlash
- Tenant yaratish/o'chirish UI
- Tizim sozlamalari ekrani
- Byudjet/kvota boshqaruvi UI
- Provayder kalitlarini kiritish UI
- Metrika/health dashboard

**Backend tomoni TAYYOR:** `identity_api.py` da 14 ta route bor
(`/bootstrap`, `/register`, `/login`, `/workspaces`, `/invitations`, ...),
lekin ularga **UI yo'q**. Bu `ui_full_product: PARTIAL` ning aynan ma'nosi.

---

## 5. TENANT PANEL — holat

### ⚠️ QISMAN — operator paneli ichida

**Bor:** platform page ichida tenant-scoped ko'rinishlar:
- **Mijozlar 360** — mijoz, kontakt, kanal identity, buyurtmalar
- **Connections** — connector ro'yxati, verify, lifecycle
- **Audit** — tenant audit oqimi
- **Usage budget** — `/usage-budget` route bor

**Nima yo'q:**
- Tenant **o'z-o'zini** boshqarish (self-service onboarding)
- Tenant sozlamalari ekrani (valyuta, til, ish vaqti, ES lat)
- Tenant hisoboti / eksport
- Tenant foydalanuvchilarini boshqarish
- Ko'p-tenant almashtirish UI (`/workspaces` route bor, UI yo'q)

---

## 6. XODIMLAR PANELI — holat

### ⚠️ YO'Q (backend qismi bor)

**Backend TAYYOR:**
- `workforce.py` — 3 tool: `workforce.shifts`, `workforce.attendance`,
  `workforce.workload`
- Google Sheets'dan smena register'ini o'qish
- `oversight.activity` — xodim/agent faoliyati

**UI YO'Q:**
- Smena jadvali ekrani
- Davomat ko'rinishi
- Yuklama taqsimoti
- Xodim profili

**Izoh:** `workforce` moduli Sheets register'iga bog'langan, ya'ni ma'lumot
manbai bor; faqat **ko'rsatish qatlami** yozilmagan.

---

## 7. Boshqa panellar / qurilmalar

| Element | Holat | Qolgan |
|---|---|---|
| `ui_full_product` | **PARTIAL** | Mahsulot oqimlari + build hardening |
| `oauth_api_ui` | **SOURCE_ONLY** | React typecheck/build/browser E2E **NOT_RUN** |
| `mac_desktop` | **PARTIAL** | Read-only preview; native installer yo'q |
| `windows_desktop` | **TODO** | Runner + native adapterlar |
| `telephony_outbound` | **STAGE_C_LOCAL_CONTRACT_TESTED** | Dialer, SIP, codec, concurrent slot |
| `wa_window_until_graph_attribute` | **INVESTIGATED_REFUSED** | Ataylab rad etilgan |

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

**42 faza** bajarildi (§1–§147, `ULTRA-AUDIT-ASCII-CELL-UZ.md`). Har bir fazaning
usuli bir xil: chegarani **o'lchash**, uni **mutatsiya** qilib ko'rish, yashil
qolganini **qadash**. To'liq ro'yxat va o'lchovlar hujjatda; eng ko'p uchraydigan
naqshlar quyida.

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

**Eng muhim o'lchov (IV faza):** eski kodda **600 000 – 899 999 so'm**
(haqiqiy 3x–4.5x mediana) invoice'lar **`ready_for_approval`** qaytarardi —
tekshiruv butun oraliqda o'tkazib yuborilgan.

---

## 10. Xulosa — uchta savolga aniq javob

**"Nima qoldi?"** — Backend yadrosi tugallangan va testlangan. Qolgan ish
**mahsulotlashtirish** va **qattiqlashtirish**.

**"Nimalar ochiq?"** — 8 ta ochiq risk (`BACKLOG.json`), 11 ta TODO/PARTIAL item.

**"Nima qurilmagan?"** — Asosan **UI qatlami**:
- Admin panel — yo'q
- Tenant self-service — qisman
- Xodimlar paneli — UI yo'q (backend bor)
- Windows desktop — yo'q (POSIX contract to'sqinlik qiladi)

**Eng muhim xulosa:** backend va agenting **tayyor**, lekin
`production_release: NO_GO`. Sabab — **UI qatlami va operatsion qattiqlashtirish**
(HA, observability, DR, live acceptance) yo'q. Bu **kod** muammosi emas,
**yetkazib berish** muammosi.

# Implementation status — joriy holat (2026-09-22)

**v0.5 · IN_PROGRESS · production NO_GO.** Bu fayl bitta joriy status blokidan va oxirida
yig‘ilgan **Tarixiy holat** bo‘limidan iborat. Oldingi versiyalarning bannerlari o‘chirilmadi,
faqat pastga ko‘chirildi.

## Bir qarashda

| Savol | Javob |
|---|---|
| API bu mashinada ishga tushirilganmi? | **Yo‘q.** `api-python/.env` yo‘q, `config/integrations.json` yo‘q, `api-python/data/app.db` da faqat migratsiya qatori bor. |
| Biror provider `live_verified`mi? | **Yo‘q.** Eng yuqori daraja — `LOCAL_CONTRACT_TESTED`. |
| `runtime_tests` | **3 601 sinov** (2026-09-25); Windows’da `failures=1, errors=12` — **13 tasi Windows-only** sabab bilan va ro‘yxat `test_platform_baseline.py` da qadalgan. |
| `integration_tests` | **351/351 PASS** (2026-09-25; `ENV=test ALLOW_INSECURE_DEV=true PIPELINE_MODE=platform IDENTITY_DIRECTORY=false`; birinchi ikkitasini `conftest.py` o‘zi qo‘yadi; `cryptography` o‘rnatilgan bo‘lishi shart). Legacy to‘plamdan ko‘chirilgan 5 fayl shu songa kiradi. |
| `api-python/tests/` (legacy) | **Nafaqaga chiqarildi (2026-09-22).** U 33 qizil / 172 pass edi va hech bir gate uni yurgizmasdi. Sabablar: `api-python/integration_tests/LEGACY-RETIRED.md`. |
| CI `http` job | endi **yig‘iladi** — `integration_tests/conftest.py` ordering bog‘liqligini yo‘q qildi. |
| CI `ui_dependency_security` job | **PASS** — `npm audit --audit-level=high` → 0 vulnerabilities (2026-09-25; `next@16.3.6` + `react@19.3.0`). |
| First-run yo‘li | Faqat `scripts/provision_identity.py`. Eski `setup_local.py` → `owner_login.py` ketma-ketligi **o‘lik** (410 / 403, UI’da token maydoni yo‘q). |

## Qamrov: registry va pack’lar orasidagi bo‘shliq

Registry’da **88** tool bor. Yetkazib berilayotgan pack’lardan (`demo-retail`, `marketing`,
`turkish-baby`, `_template`) **17 tasi** chaqirilishi mumkin (2026-09-25 o‘lchovi;
`whatsapp.*` endi `turkish-baby`da). Qolgan 15 modulning **10 758 satri**
(19 182 dan, 56%) hamon chaqirilmaydi.

Quyidagi modullarni ishlatadigan **birorta pack yo‘q**:

`erp` · `documents` · `inventory` · `business_graph` ·
`telephony` · `assets` · `vision` · `manufacturing` · `oee` · `workforce` · `supervisor` ·
`reengagement` · `escalation` · `briefing` · `oversight`

WhatsApp inbound HTTP route **bor** (`app/whatsapp_api.py`; 19 HTTP testi
`integration_tests/test_whatsapp_webhook_http.py`) va `whatsapp.*` tool’lari
`turkish-baby` pack’ida e’lon qilingan — live Meta acceptance hamon yo‘q.

Bular **muzlatilgan (frozen) preview modullar** — mahsulot funksiyasi emas. Ular test va
chegara auditi bilan qoplangan, lekin hech bir mijoz konfiguratsiyasi ularga yetib
bormaydi. Hujjatlarda backend «tayyor» deb yozilgan joyda gap **yadro** haqida, bu
modullar haqida emas.

## Approval siyosati — joriy shakl

Approval har bir write uchun **sukut**. 2026-09-22 dan bitta istisno bor
(`platform_runtime/engine.py::_preauthorized`): **`autonomous`** ladderdagi agent outbound
write’ni per-message tasdiqsiz yuborishi mumkin, **faqat** qabul qiluvchi

- pack’ning `allowed_recipients` ro‘yxatida bo‘lsa; yoki
- task javob berayotgan **tasdiqlangan inbound suhbat** bo‘lsa (o‘sha kanal, o‘sha
  `conversation_id`).

`human_led` hamma narsani ushlaydi, `human_assisted` o‘zgarmadi, `approval:` ro‘yxatidagi
tool nomlari har doim kutadi, **`destructive` va `physical` risk hech qachon nazoratsiz
bajarilmaydi**.

Brifing va eskalatsiya endi o‘z transportiga ega emas: ikkalasi ham `engine.submit`
orqali oddiy engine task’i sifatida yuboriladi va approval gate’ini **chetlab
o‘tmaydi**. Jurnaldagi `submitted` qatori engine verdikti bilan yopiladi.

## Model qatlami

Planner asli **OpenAI `/chat/completions` shaklida** yozilgan
(`platform_runtime/llm.py`, `agent_planner.py`). Gemini OpenAI-mos endpoint bergani
uchun shu yo‘l bilan ulanadi.

**Claude uchun native Messages API adapteri endi bor** (`model_transport.py`,
`provider: anthropic`): `POST /v1/messages`, `x-api-key`, majburiy
`anthropic-version: 2023-06-01`, top-level `system`, `temperature` va `response_format`
**yuborilmaydi**, javob `parse_anthropic_decision` bilan o‘qiladi. Holati —
**`LOCAL_CONTRACT_TESTED`**: haqiqiy Anthropic endpointiga chaqiruv qilinmagan.

Claude Haiku 3 va Haiku 3.5 **nafaqaga chiqarilgan**; joriy kichik model —
**`claude-haiku-4-5`**. `docs/agent-platform-PRD-TZ.md` hali «Haiku 3.5» deb yozadi, bu
**eskirgan** raqam.

## Hali qurilmagan yoki to‘liq emas

| Talab | Holat |
|---|---|
| To‘liq agentic planning loop, oldingi tool natijasiga qarab keyingi rejani tuzish | Qisman. `agent_loop.py` + `agent_planner.py` bor; live acceptance yo‘q |
| Faktik lookupdan so‘ng avtomatik grounded LLM javob | Yo‘q. Read natija UI’da; uydirma avtomatik javob yuborilmaydi |
| Embedding RAG, document chunking/indexing | Yo‘q. Matn/BM25 darajasida; buni RAG deb atamang |
| PostgreSQL, distributed execution, HA/klaster | Qisman. `postgres_connector.py` read-only; ijro bitta instance uchun |
| Observability (metrika eksporti, trace, alerting) | Yo‘q. `MetricsPanel` health va navbat hisobini ko‘rsatadi, ko‘proq emas |
| DR (disaster recovery) mashqi | Yo‘q real mashq; `scripts/dr_drill.py` bor |
| Provayder 429/5xx uchun backoff | Yo‘q |
| Integrator onboarding, user directory, refresh/revoke user tokens | Qisman |
| Billing, subscription freeze, token/usage cost budget | Qisman. `usage_budget.py` obuna/hisob-faktura emas |
| Gmail, Drive, Calendar, alohida CRM adapter | Qisman, `LOCAL_CONTRACT_TESTED`. MCP generic call’ni tasdiqlangan CRM adapter deb atamang |
| OAuth refresh, token vault, provider rate-limit lifecycle | Qisman. `cryptography` o‘rnatilmagan bo‘lsa OAuth qismi ishlamaydi |
| Windows/macOS runner, Excel/Word/print/1C/screen/browser/IoT | Yo‘q yoki disabled. Lokal-executor shartnomasi **POSIX-only** |
| Signed runner update, tray, rollback, screenshots/redaction | Yo‘q |
| STT/TTS/voice production pipeline | Yo‘q |
| Xodimlar (`workforce`) UI | Yo‘q. Backend tool sifatida bor, HTTP route emas |
| Tenant self-service onboarding va sozlamalar ekrani | Yo‘q |
| Backup encryption/offsite rotation, migration rollback framework | Yo‘q. Online SQLite backup utility va smoke test bor |
| Security/load/browser E2E/failure/recovery to‘liq acceptance | Qisman unit coverage, to‘liq emas |
| Uch kunlik haqiqiy staging pilot va production deployment | Bajarilmagan |
| UI dependency xavfsizligi | **FAIL** — Next 15 + React 19 migratsiyasi kerak |

## Bilinadigan chegaralar

Freeze in-flight external amalni fizik to‘xtatishini kafolatlamaydi; holat `uncertain` ga
o‘tkaziladi. Expired lease avtomatik qayta ijroga ochilmaydi. Har external write
transactiondan tashqarida bajariladi — exactly-once umumiy kafolat yo‘q.

MCP transporti cheklangan JSON/finite-SSE variant; uzluksiz SSE, OAuth negotiation, stdio
server va murakkab reconnect yo‘q. Remote MCP tool riski operator konfiguratsiyasiga
bog‘liq; barcha MCP call write-risk sifatida approval’dan o‘tadi.

Runnerda POSIX descriptor tekshiruvi bor, ammo u butun OS sandbox o‘rnini bosmaydi.
Dedicated non-privileged OS user, ACL va faqat ajratilgan kataloglar talab qilinadi.
Journalda o‘qilgan fayl mazmuni saqlanishi mumkin, disk shifrlash va retention kerak.

Yangi UI avtomatik polling qilmaydi, **Yangilash** ishlatiladi. Tarixiy `/legacy` UI
platforma rejimida mos emas; eski mutation endpointlar default o‘chirilgan. Production’da
`PIPELINE_MODE=legacy` ishlatmang.

Internetga chiqadigan production’dan oldin dependency/security review majburiy — hozir u
**qizil**.

### 2026-09-23 da yopilgan nuqsonlar (lokal testlar bilan, live emas)

| Nuqson | Endi |
|---|---|
| ERP «post once» kafolat emas edi: ledger qatori POST’dan **keyin** yozilardi, POST va ledger orasidagi crash retry’da ikkinchi posting berardi | POST’dan **oldin** claim qiluvchi `posting` qatori deterministik idempotency kaliti bilan yoziladi va ERP’ga `Idempotency-Key` header’ida yuboriladi. Javobi yo‘qolgan POST (timeout, 5xx, crash; 180 s dan eski rezerv) `uncertain` bo‘ladi va owner `erp.reconcile_posting` qilmaguncha qayta yuborilmaydi. Faqat aniq rad javobi (408/409/425/429 dan boshqa 4xx) retry’ga ochiq. `unconfirmed` ham endi retry’ni to‘sadi. Endi HTTP route ham bor: `GET /platform/{tenant}/erp/postings?status=...` (owner/operator o‘qiydi) va `POST /platform/{tenant}/erp/postings/{id}/reconcile` (owner only) |
| Business Graph / inventory: source 50 qatorli chegarada (where-filtersiz) to‘xtasa natija jim kesilardi | Har source status’ida `truncated`; javoblarda `truncated` va `sources_truncated`. Bitta entity uchun faqat id topilmagan kesilgan source hisoblanadi |
| Login throttle proxy ortida bitta IP bucket (global lockout) | `TRUSTED_PROXIES` (IP/CIDR); faqat ishonchli peer’da XFF’ning o‘ngdan birinchi ishonchsiz manzili. Akkaunt bo‘yicha throttle avvaldan bor edi, test bilan qadaldi |
| Runner har `4403` da butunlay chiqardi | Server endi alohida kodlar yuboradi: `4401` token, `4403` revoke/rotatsiya, `4400` protokol, `1011` server xatosi (`integration_tests/test_runner_close_codes.py`). Runner: `4403` → darhol chiqadi, `4401` → token yo‘li (fayldan kutadi, 3 raddan keyin chiqadi), qolganlari → backoff bilan qayta ulanadi |
| MySQL: `ssl_ca` yo‘q, PyMySQL CA’siz hostname tekshiruvini o‘chirardi | Aniq `SSLContext` (hostname + zanjir, TLS ≥ 1.2); ixtiyoriy `ssl_ca_env` (CA fayl yo‘li env’da) yoki absolyut `ssl_ca`; inline PEM va `tls_verify: false` rad etiladi |

Qolgan: live ERP/MySQL tekshiruvi.

---

<details>
<summary><strong>Tarixiy holat</strong> — oldingi bannerlar va 2026-09-13 hisoboti (joriy status emas)</summary>

Quyidagi matnlar o‘z sanasida yozilgan. Ularning PASS raqamlari joriy checkpointni
tasdiqlamaydi.

### v0.3.8 banner

> **Joriy: v0.3.8, IN_PROGRESS, production NO_GO.** Google metadata sync/reconciliation va
> HTTP/UI source qo‘shildi. Joriy dalil: [development reyestri](development/PROGRESS-UZ.md).
> Quyidagi raqam va holatlar tarixiy snapshotlar.

### v0.3.6 banner

> **Joriy development: v0.3.6, oraliq checkpoint.** Yangi source, audit va 709 ta lokal test
> dalili: [development reyestri](development/PROGRESS-UZ.md) va
> [implementatsiya hisoboti](development/V036-IMPLEMENTATION-UZ.md). To‘liq mahsulot emas.

### v0.3.5 eslatma

> 486 offline test PASS; SQL Server/Oracle transportlari source/contract bosqichida.
> [Joriy checkpoint](CHECKPOINT-V035-UZ.md) va
> [enterprise cheklovlari](ENTERPRISE-SQL-PREVIEW-UZ.md). Live tekshiruv yo‘q, production
> NO-GO.

### v0.3.4 eslatma

> Bu fayldagi avvalgi bosqichlar tarixiy. O‘sha sanadagi tasdiqli SQL/NoSQL qatlami va
> chegaralari [CHECKPOINT-V034-UZ.md](CHECKPOINT-V034-UZ.md) hamda
> [MANAGED-DATABASES-UZ.md](MANAGED-DATABASES-UZ.md) da. Production NO-GO.

### Holat: v0.3.2 agent loop source preview

**SOURCE_ONLY / NOT_RUN / PRODUCTION NO-GO.** Persisted, budget-cheklangan result-fed agent
loop, model adapter, HTTP/worker ulanishi va dashboard source’i yozildi. v0.3.1 xavfsizlik
tuzatishlari saqlandi. Feature standart holatda o‘chiq.

Tafsilot: `CHECKPOINT-V032-UZ.md`. Hech qanday test/build/typecheck/lint/compile/demo yoki
live acceptance bajarilmadi. Model javobiga source IDs tekshiruvi qo‘shildi, lekin semantik
fakt tekshiruvi va haqiqiy pul budjeti hali yo‘q. Eski workerga rollback yangi parent-run
fence’ni olib tashlashi mumkin.

### Holat: v0.3.1 code-review preview

**SOURCE_ONLY / NOT_RUN / PRODUCTION NO-GO.** `CHECKPOINT-V031-UZ.md` va
`verification/v031/` o‘sha checkpoint manbasi. Ish tartibi: `WORKING-AGREEMENT-UZ.md`.

Kod o‘zgarishlari va yangi regression ssenariylari yozildi. Test/build/typecheck/lint/
compile/demo bajarilmadi. To‘liq PRD implementatsiyasi yakunlanmagan.

### Holat: v0.3 hardening preview

**Production NO-GO.** PRD: `prd-v03/00-HARDENING-PRD-UZ.md`; audit: `AUDIT-V03-UZ.md`; test
dalillari: `verification/v03/`.

Yangi kod identity/session, UI, worker va connector hardeningni qamrab oladi. Barcha CRM/ERP
tayyor emas. HTTP va React build mahalliy bajarilmagan.

### Amaldagi holat: v0.2 engineering preview

Dalillar: `verification/v02-summary.json`, `AUDIT-V02-UZ.md`,
`prd-v02/06-RELEASE-ACCEPTANCE.md`.

114 Python runtime test va 15 Node test lokal bajarildi. Aisha adapterlari faqat mock
contract; haqiqiy ovoz yaratilmagan. SQLite eksport connectori lokal haqiqiy DB bilan
tekshirildi. HTTP integration, React typecheck/build, live providerlar va production deploy
tasdiqlanmagan.

---

### Implementation status, 2026-09-13

#### Release qarori

**Engineering preview. Full PRD va production acceptance bajarilmagan.** Yetishmayotgan
qismlarni mock natija bilan “tayyor” deb ko‘rsatish o‘rniga xavfli eski yo‘llar o‘chirildi.
Tayyor tashqi hosting URL yaratilmagan.

#### 2026-09-14 davomiy bosqich: Universal connector contract

Connector contract v1.0, capability/lifecycle metadata va `postgres_readonly` guarded read
adapteri qo'shildi. Declarative Bitrix24, amoCRM, 1C, MCP va custom HTTP entries endi
`adapter_required` sifatida fail-closed ko'rinadi. Bu barcha providerlar tayyor degani emas.
4 ta connector contract testi bilan jami **129 Python runtime test** o‘tdi.

Haqiqiy PostgreSQL staging, CRM OAuth/write/reconcile, provider rate limit va secret vault
acceptance hali production gate emas.

#### 2026-09-14 davomiy bosqich: Identity va workspace foundation

User, workspace, membership, one-time invitation, revoke, password scrypt hash va
refresh-token rotation qo'shildi. `IDENTITY_DIRECTORY=true` rejimida `/platform`
authorization active membershipni har requestda tekshiradi va JWTdagi eskirgan role'ni manba
deb olmaydi. Yangi 6 ta identity regression testi bilan jami **125 Python runtime test**
o‘tdi.

OIDC/JWKS, MFA, email verification, password reset, KMS vault va production identity provider
hali production gate emas.

#### 2026-09-14 davomiy bosqich: Customer 360

`p_customers`, `p_customer_contacts`, `p_channel_identities` va `p_customer_orders`
tenant-scope jadval/API foundationi qo‘shildi. Customer detail contacts, channel identities,
conversations va orders bilan qaytadi. Dashboardga `Mijozlar 360` paneli qo‘shildi. Kanal
identity avtomatik merge qilinmaydi; faqat explicit verified link ruxsat etiladi.

Yangi 5 ta dependency-free Customer 360 testi bilan jami **119 Python runtime test** o‘tdi.
FastAPI HTTP, React typecheck/build va real CRM sync hali release gate emas.

#### Bajarilgan va lokal testlangan (2026-09-13 holati)

| Qism | Dalil |
| --- | --- |
| Generic task + tartibli steps + persisted natija | SQLite bilan real unit va demo test |
| Web/Telegram/Instagram/cron envelope uchun bitta engine | Kanal almashishi testlari |
| Idempotent task/event acceptance, conflicting replay rejection | Transaction va replay testlari |
| Atomic multi-process claim, fencing, lease expiry | To‘rtta alohida OS process testi |
| Har write uchun approval, approver actor, owner-only policy | Negative va positive testlar |
| Plan/argument/policy o‘zgarsa claim rad etilishi | Tamper va policy-change testlari |
| Tenant isolation, agent-scoped memory va TTL | Tenant/agent negative testlari |
| Persistent business records va hisoblagich report | Haqiqiy SQLite yozuvi |
| Interval schedule va dedup | Deterministik clock testi |
| Freeze/revoke in-flight holatni uncertain qilishi | Fencing va device testlari |
| Device generation rotation, eski queued taskni bekor qilish | Rotation regression test |
| Durable inbox quota | Replayni ikki marta hisoblamaslik testi |
| Linux read-only runner, allowlist, secret path deny, journal | Node filesystem testlari |
| MCP/LLM/provider request kontraktlari | Mock transport testlari, real provider testi EMAS |
| SQLite backup/restore | Backup integrity va succeeded taskni tiklash smoke testi |

**O‘sha sanadagi natija:** 71 ta Python unittest va 13 ta Node test o‘tdi. Python test
muhiti: 3.14.6; Node: 24.16.0; Linux. CI’da Python 3.11 va Node 22 uchun alohida qayta
tekshiruv yozilgan, ammo CI ishga tushirilmagan.

69 ta Python fayl AST syntax tekshiruvdan o‘tdi. To‘rtta TSX fayli TypeScript 7.0.2
`--noCheck` bilan sintaktik emitdan o‘tdi. **Bu React typecheck yoki Next build o‘tdi degani
emas.** Test loglari `docs/verification/` ichida.

#### Kod yozilgan, ammo integratsion tekshiruv bajarilmagan (2026-09-13 holati)

FastAPI route/middleware va device WebSocket; owner/operator/integrator/viewer JWT; Next
control plane; Telegram, Instagram Login API, Google Sheets HTTP outbound; OpenAI-compatible
JSON planner; MCP JSON va finite-SSE transport; Docker Compose; CI configuration.

Computer’da FastAPI, PyJWT, Pydantic, PyYAML, pytest, HTTPX, React/Next dependency’lari
mavjud emas va package o‘rnatish ruxsati yo‘q. Shu sababli `integration_tests/`, tarixiy
pytest suite, to‘liq React typecheck/build, Docker build va haqiqiy WebSocket e2e
bajarilmadi. Ular muvaffaqiyatli deb hisoblanmaydi.

Google, Meta, Telegram, LLM, MCP hisoblarining haqiqiy kalitlari ishlatilmadi. Tashqi xabar,
CRM/Sheets yozuvi yoki qurilma ijrosi foydalanuvchining real tizimiga yuborilmadi.
Providerlar sonini koddagi adapter mavjudligidan real integratsiya soni sifatida hisoblamang.

#### 2026-09-13 dagi «bilinadigan chegaralar» matni

> All write tool’lar har safar approval talab qiladi, bu PRD’dagi ayrim autonomous write
> variantlaridan qat’iyroq.

**Bu jumla endi to‘g‘ri emas** — 2026-09-22 dan boshlab pre-authorised destination istisnosi
bor; yuqoridagi «Approval siyosati» bo‘limiga qarang.

> Eski Next/React package lock va Python keng version ranges saqlangan. Internet advisory
> audit va lockfile modernization bajarilmagan. Eski testlar yangi auth/runner
> kontraktlariga to‘liq moslashtirilmagan, shuning uchun “oldingi 177 passed saqlanib qoldi”
> da’vosi yo‘q.

</details>

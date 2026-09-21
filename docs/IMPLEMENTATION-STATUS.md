> **Joriy: v0.3.8, IN_PROGRESS, production NO_GO.** Google metadata sync/reconciliation va HTTP/UI source qo‘shildi. Joriy dalil: [development reyestri](development/PROGRESS-UZ.md). Quyidagi raqam va holatlar tarixiy snapshotlar.

> **Joriy development: v0.3.6, oraliq checkpoint.** Yangi source, audit va 709 ta lokal test dalili: [development reyestri](development/PROGRESS-UZ.md) va [implementatsiya hisoboti](development/V036-IMPLEMENTATION-UZ.md). To‘liq mahsulot emas. Quyidagi oldingi bo‘limlar v0.3.5 va avvalgi holat kontekstini saqlaydi.

> **v0.3.5 eslatma:** 486 offline test PASS; SQL Server/Oracle transportlari source/contract bosqichida. [Joriy checkpoint](CHECKPOINT-V035-UZ.md) va [enterprise cheklovlari](ENTERPRISE-SQL-PREVIEW-UZ.md). Live tekshiruv yo‘q, production NO-GO. Quyidagi qaydlar tarixiy.

> **v0.3.4 eslatma:** bu fayldagi avvalgi bosqichlar tarixiy. Joriy tasdiqli SQL/NoSQL qatlami va chegaralari [CHECKPOINT-V034-UZ.md](CHECKPOINT-V034-UZ.md) hamda [MANAGED-DATABASES-UZ.md](MANAGED-DATABASES-UZ.md) da. Production NO-GO.

# Joriy holat: v0.3.2 agent loop source preview

**SOURCE_ONLY / NOT_RUN / PRODUCTION NO-GO.** Persisted, budget-cheklangan result-fed agent loop, model adapter, HTTP/worker ulanishi va dashboard source’i yozildi. v0.3.1 xavfsizlik tuzatishlari saqlandi. Feature standart holatda o‘chiq.

Joriy tafsilot: `CHECKPOINT-V032-UZ.md`. Hech qanday test/build/typecheck/lint/compile/demo yoki live acceptance bajarilmadi. Model javobiga source IDs tekshiruvi qo‘shildi, lekin semantik fakt tekshiruvi va haqiqiy pul budjeti hali yo‘q. Eski workerga rollback yangi parent-run fence’ni olib tashlashi mumkin.

Quyidagi v0.3.1/v0.3 statuslari tarixiy snapshotlar, yangi versiya uchun PASS emas.

---

# Joriy holat: v0.3.1 code-review preview

**SOURCE_ONLY / NOT_RUN / PRODUCTION NO-GO.** `CHECKPOINT-V031-UZ.md` va `verification/v031/` joriy checkpoint manbasi. Ish tartibi: `WORKING-AGREEMENT-UZ.md`.

Kod o‘zgarishlari va yangi regression ssenariylari yozildi. Test/build/typecheck/lint/compile/demo bajarilmadi. To‘liq PRD implementatsiyasi yakunlanmagan. Quyidagi statuslar tarixiy snapshotlar, v0.3.1 PASS dalili emas.

---

# Joriy holat: v0.3 hardening preview

**Production NO-GO.** Joriy PRD: `prd-v03/00-HARDENING-PRD-UZ.md`; audit: `AUDIT-V03-UZ.md`; test dalillari: `verification/v03/`.

Yangi kod identity/session, UI, worker va connector hardeningni qamrab oladi. Barcha CRM/ERP tayyor emas. HTTP va React build mahalliy bajarilmagan. Quyidagi oldingi statuslar tarixiy snapshot bo‘lib, joriy tasdiq o‘rnida ishlatilmasin.

---

# Amaldagi holat: v0.2 engineering preview

Joriy dalillar: `verification/v02-summary.json`, `AUDIT-V02-UZ.md`, `prd-v02/06-RELEASE-ACCEPTANCE.md`.

114 Python runtime test va 15 Node test lokal bajarildi. Aisha adapterlari faqat mock contract; haqiqiy ovoz yaratilmagan. SQLite eksport connectori lokal haqiqiy DB bilan tekshirildi. HTTP integration, React typecheck/build, live providerlar va production deploy tasdiqlanmagan. Quyidagi v0.1 matn tarixiy baseline sifatida saqlangan, yangi kodning test holatini ifodalamaydi.

---

# Implementation status, 2026-09-13

## Release qarori

**Engineering preview. Full PRD va production acceptance bajarilmagan.** Yetishmayotgan qismlarni mock natija bilan “tayyor” deb ko‘rsatish o‘rniga xavfli eski yo‘llar o‘chirildi. Tayyor tashqi hosting URL yaratilmagan.

## 2026-09-14 davomiy bosqich: Universal connector contract

Connector contract v1.0, capability/lifecycle metadata va `postgres_readonly` guarded read adapteri qo'shildi. Declarative Bitrix24, amoCRM, 1C, MCP va custom HTTP entries endi `adapter_required` sifatida fail-closed ko'rinadi. Bu barcha providerlar tayyor degani emas. 4 ta connector contract testi bilan jami **129 Python runtime test** o‘tdi.

Haqiqiy PostgreSQL staging, CRM OAuth/write/reconcile, provider rate limit va secret vault acceptance hali production gate emas.

## 2026-09-14 davomiy bosqich: Identity va workspace foundation

User, workspace, membership, one-time invitation, revoke, password scrypt hash va refresh-token rotation qo'shildi. `IDENTITY_DIRECTORY=true` rejimida `/platform` authorization active membershipni har requestda tekshiradi va JWTdagi eskirgan role'ni manba deb olmaydi. Yangi 6 ta identity regression testi bilan jami **125 Python runtime test** o‘tdi.

OIDC/JWKS, MFA, email verification, password reset, KMS vault va production identity provider hali production gate emas.

## 2026-09-14 davomiy bosqich: Customer 360

`p_customers`, `p_customer_contacts`, `p_channel_identities` va `p_customer_orders` tenant-scope jadval/API foundationi qo‘shildi. Customer detail contacts, channel identities, conversations va orders bilan qaytadi. Dashboardga `Mijozlar 360` paneli qo‘shildi. Kanal identity avtomatik merge qilinmaydi; faqat explicit verified link ruxsat etiladi.

Yangi 5 ta dependency-free Customer 360 testi bilan jami **119 Python runtime test** o‘tdi. FastAPI HTTP, React typecheck/build va real CRM sync hali release gate emas.

## Bajarilgan va lokal testlangan

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

**Joriy natija:** 71 ta Python unittest va 13 ta Node test o‘tdi. Python test muhiti: 3.14.6; Node: 24.16.0; Linux. CI’da Python 3.11 va Node 22 uchun alohida qayta tekshiruv yozilgan, ammo CI ishga tushirilmagan.

69 ta Python fayl AST syntax tekshiruvdan o‘tdi. To‘rtta TSX fayli TypeScript 7.0.2 `--noCheck` bilan sintaktik emitdan o‘tdi. **Bu React typecheck yoki Next build o‘tdi degani emas.** Test loglari `docs/verification/` ichida.

## Kod yozilgan, ammo integratsion tekshiruv bajarilmagan

FastAPI route/middleware va device WebSocket; owner/operator/integrator/viewer JWT; Next control plane; Telegram, Instagram Login API, Google Sheets HTTP outbound; OpenAI-compatible JSON planner; MCP JSON va finite-SSE transport; Docker Compose; CI configuration.

Computer’da FastAPI, PyJWT, Pydantic, PyYAML, pytest, HTTPX, React/Next dependency’lari mavjud emas va package o‘rnatish ruxsati yo‘q. Shu sababli `integration_tests/`, tarixiy pytest suite, to‘liq React typecheck/build, Docker build va haqiqiy WebSocket e2e bajarilmadi. Ular muvaffaqiyatli deb hisoblanmaydi.

Google, Meta, Telegram, LLM, MCP hisoblarining haqiqiy kalitlari ishlatilmadi. Tashqi xabar, CRM/Sheets yozuvi yoki qurilma ijrosi foydalanuvchining real tizimiga yuborilmadi. Providerlar sonini koddagi adapter mavjudligidan real integratsiya soni sifatida hisoblamang.

## Hali qurilmagan yoki to‘liq emas

| Talab | Holat |
| --- | --- |
| To‘liq agentic planning loop, oldingi tool natijasiga qarab keyingi rejani tuzish | Yo‘q. Hozir bounded literal-argument plan; natija control plane’da ko‘rinadi |
| Faktik lookupdan so‘ng avtomatik grounded LLM javob | Yo‘q. Read natija UI’da; uydirma avtomatik javob yuborilmaydi |
| Embedding RAG, document chunking/indexing | Yo‘q. Faqat agent-scoped keyword memory, buni RAG deb atamang |
| PostgreSQL, pgvector, distributed execution | Yo‘q. SQLite lokal persistent volume |
| Integrator onboarding, user directory, refresh/revoke user tokens | Qisman. Bootstrap admin user token beradi; device rotation bor |
| Billing, subscription freeze, token/usage cost budget | Yo‘q. Inbox count quota billing emas |
| Gmail, Drive, Calendar, alohida CRM adapter | Yo‘q. MCP generic call o‘rnini to‘liq tasdiqlangan CRM adapter deb atamang |
| OAuth refresh, token vault, provider rate-limit lifecycle | Yo‘q. Environment reference va manual rotation |
| Windows/macOS runner, Excel/Word/print/1C/screen/browser/IoT | Yo‘q yoki disabled. Eski simulyatsiya ijroga ulanmagan |
| Signed runner update, tray, rollback, screenshots/redaction | Yo‘q |
| STT/TTS/voice production | Yo‘q |
| Automatic safe read retry, provider dead-letter operator retry | Qisman. Failed inbox retry bor; external uncertain write avtomatik qaytarilmaydi |
| UI barcha PRD ekranlari, diagram agent map, mobile accessibility | Qisman control plane, full UI emas |
| Backup encryption/offsite rotation, migration rollback framework | Yo‘q. Online SQLite backup utility va smoke test bor |
| Security/load/browser/failure/recovery to‘liq acceptance | Qisman unit coverage, to‘liq emas |
| Uch kunlik haqiqiy staging pilot va production deployment | Bajarilmagan |

## Bilinadigan chegaralar

All write tool’lar har safar approval talab qiladi, bu PRD’dagi ayrim autonomous write variantlaridan qat’iyroq. Freeze in-flight external amalni fizik to‘xtatishini kafolatlamaydi; holat uncertain ga o‘tkaziladi. Expired lease avtomatik qayta ijroga ochilmaydi.

MCP transporti cheklangan JSON/finite-SSE variant; uzluksiz SSE, OAuth negotiation, stdio server va murakkab reconnect yo‘q. Server protocol negotiation va real tools/call deploymentda tekshirilishi shart. Remote MCP tool riski operator konfiguratsiyasiga bog‘liq; barcha MCP call write-risk sifatida approval’dan o‘tadi.

Runnerda Linux descriptor tekshiruvi bor, ammo u butun OS sandbox o‘rnini bosmaydi. Dedicated non-privileged OS user, rootlarda ACL va faqat ajratilgan kataloglar talab qilinadi. Journalda o‘qilgan fayl mazmuni saqlanishi mumkin, disk shifrlash va retention kerak.

Yangi UI avtomatik polling qilmaydi, Yangilash ishlatiladi. Tarixiy `/legacy` UI platforma rejimida mos emas; eski mutation endpointlar default o‘chirilgan. Production’da PIPELINE_MODE=legacy ishlatmang.

Eski Next/React package lock va Python keng version ranges saqlangan. Internet advisory audit va lockfile modernization bajarilmagan. Internetga chiqadigan production’dan oldin dependency/security review majburiy. Eski testlar yangi auth/runner kontraktlariga to‘liq moslashtirilmagan, shuning uchun “oldingi 177 passed saqlanib qoldi” da’vosi yo‘q.

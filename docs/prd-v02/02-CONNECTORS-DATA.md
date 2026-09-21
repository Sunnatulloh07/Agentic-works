# PRD-02: CRM, ERP va mijoz ma’lumotlari

## Mahsulot va xavfsizlik chegarasi

“Barcha connectorlar” bitta universal API emas. Platforma umumiy connector framework, versionlangan capability katalogi va providerga xos adapterlardan tashkil topadi. Auth, field mapping, delta-sync, write semantics va limitlar providerga qarab farqlanadi. Hali yozilmagan adapter UI’da ishlaydigan connector sifatida ko‘rsatilmaydi.

## Connector lifecycle

`draft → authorizing → configured → verifying → healthy/degraded → revoked`. Configure holati real connection testdan o‘tgan degani emas. `last_verified_at`, contract version, sync cursor, scopes, health, sanitized error code va usage saqlanadi.

OAuth authorization-code + PKCE/state, encrypted secret vault, KMS envelope encryption, refresh rotation va secret version zarur. Secretlar prompt, browser localStorage, pack YAML, audit body yoki job resultga chiqmaydi. OAuth revoke provider va platformdagi ijroni to‘xtatadi.

| ID | Talab | Acceptance |
|---|---|---|
| CN-01 | Stable connector contract | discover, validate, read, plan_write, execute_write, reconcile, revoke imkoniyatlari deklaratsiyasi |
| CN-02 | Tenant + agent scopes | Boshqa tenant connection ID’si va agentga berilmagan connection executiondan oldin rad etiladi |
| CN-03 | Customer DB xavfsiz o‘qish | Read-only DB credential, TLS, schema/table/column allowlist, bound parameter, row/timeout limit |
| CN-04 | Network xavfsizligi | SSRF private/link-local blok, aniq host policy, DNS rebindingdan himoya, redirect va credential destination binding |
| CN-05 | CRM/ERP write | Dry-run diff, obyekt revisioni, kerakli approval, stable idempotency, provider receipt |
| CN-06 | Sync va conflict | Cursor durable; duplicate webhook dedup; source-of-truth va conflict resolution aniq |
| CN-07 | PII minimalizatsiya | Maqsadga kerakli ustunlar; redaction, retention va tenant delete oqimi |
| CN-08 | SQL / prompt injection | LLM matni raw SQLga aylanmaydi; data tool yoki role siyosatini o‘zgartirmaydi |

## v0.2 dagi haqiqiy kod

`platform_runtime/connectors.py` SQLite eksportlari uchun real read-only adapter beradi. `connectors.read` runtime tooliga `connection`, `table`, `columns`, ixtiyoriy `limit` va exact-match `where` beriladi. SQL matni, DSN, path va URL foydalanuvchidan qabul qilinmaydi. `PLATFORM_DB_ROOTS` explicit absolut mount allowlist; `mode=ro`, `query_only`, SQL authorizer, VM/time limit, 100 row va 80 KB result limit mavjud. Views va virtual tables yo‘q. Secret ustunlar allowlistda bo‘lmasa qaytmaydi. Platformaning o‘z DB fayli connector qilib ulanishi rad etiladi.

Bu PostgreSQL, MySQL yoki CRM SaaSga live ulanish emas. SQLite mijoz eksportini serverga read-only mount qilish operator vazifasi. Pathni boshqa process almashtirishi, hostile mounted database va native SQLite parseri alohida OS/container boundary talab qiladi. Config fayli trusted deployment input, user-upload emas.

`allowed_connections` pack policy’da agent uchun belgilanadi. Metadata endpoint owner/integratorga path va secretsiz qaytadi. `config/customer-sqlite.example.json` faqat merge qilinadigan namuna, ishlayotgan tenant konfiguratsiyasi emas.

## Keyingi adapterlar

Avval foydalanuvchi ishlatadigan aniq CRM va DB tanlanadi. PostgreSQL read-only adapter alohida cheklangan DB user, statement_timeout, pool limiti va tenant data filtering bilan quriladi. CRM yoki ERP yozuvlari native API orqali boshlanadi; 1C, Bitrix24, amoCRM va boshqalarning har biri alohida contract/integration test oladi. Direct SQL write avtomatik yoqilmaydi.

MCP yordamchi adapter, “hamma CRM tayyor” emas. v0.2 har allowlisted MCP tool uchun explicit `tool_schemas` talab qiladi va unknown argumentlarni rad etadi. MCP endpointning private-network/DNS xavfsizligi hali release blocker.

## 2026-09-14 universal connector contract foundation

`platform_runtime/connector_contract.py` version 1.0 connector descriptorini qo'shadi. Har connection driver, lifecycle, capability, scope va contract version bilan e'lon qilinadi. Noma'lum driver `unsupported_driver`, Bitrix24/amoCRM/1C/MCP/custom HTTP kabi deklarativ entry'lar `adapter_required` holatida ko'rinadi; ular healthy deb ko'rsatilmaydi.

`postgres_readonly` adapteri operator allowlist qilgan host, TLS, password environment reference, table/column allowlist, optional tenant column, parametrli equality filter, statement timeout va read-only transaction bilan ishlaydi. SQL, DSN, password va host user requestidan olinmaydi. Psycopg dependency production image'ga qo'shildi, real database acceptance hali bajarilmagan.

Bu universal framework barcha providerlar avtomatik ishlaydi degani emas. Har real CRM/ERP uchun alohida OAuth/token, API version, field mapping, rate limit, write receipt, reconcile va contract test adapteri kerak.


## 2026-09-14 v0.3.3 authority checkpointi

O‘qish adapterlarida lifecycle, capability, contract va agent_ids runtime tekshiruvlari qo‘shildi. Pack allowed_connections va connection agent_ids birgalikda bajariladi. Bo‘sh agent_ids pack ruxsatidan tashqari qo‘shimcha cheklov bermaydi; bo‘sh bo‘lmagan ro‘yxat aniq agent kontekstini talab qiladi. Configured/healthy/degraded faqat ishga ruxsat holati, live health dalili emas. Draft/authorizing/verifying/revoked odatiy read/probe yo‘lida yopiq; alohida persisted authorizing/verifying lifecycle servisi hali yo‘q.

Owner/integrator single-table probe scoped connection uchun explicit agent va pack ruxsatini talab qiladi, sample satrlarini APIga qaytarmaydi. 37 yangi offline regressiya mavjud. HTTP/UI source kengaytirildi, lekin dependency-backed acceptance bajarilmadi. Batafsil: `docs/CHECKPOINT-V033-UZ.md`. CRM/ERP provider adapterlari, MCP network hardening va qo‘shimcha DB driverlari bu checkpointda yozilmagan.

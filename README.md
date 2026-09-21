# v0.5 avtonom development checkpoint

**IN_PROGRESS; production NO_GO; yakuniy mahsulot emas.** Joriy dalil: `docs/development/PROGRESS-UZ.md`, `docs/development/V05-BLOCKS-CORE-UZ.md`, `docs/prd-v05/00-ENTERPRISE-OPERATING-LAYER-PRD-UZ.md`.

Kross-tizim **Business Graph** qo‘shildi: operator e’lon qilgan entity’lar, har bir atributda `source` va `observed`, ziddiyat **jimgina hal qilinmaydi**. Manba — mavjud read tool, shuning uchun graph **o‘z vakolatini bermaydi**; ruxsatsiz manba bo‘lsa hech bir manba o‘qilmaydi. Kamera/OT qatlami uchun arxitektura qarori yozildi (faqat **hodisa**, hech qachon frame) va O‘zbekiston Law No. 1125 biometrik talabi hisobga olindi. 38 yangi lokal test. Real provider acceptance o‘tkazilmagan.

Quyidagi oldingi README bo‘limlari tarixiy holatni tasvirlaydi; joriy reyestr ustuvor.

> **v0.3.8 checkpoint.** Gmail metadata/history, Drive metadata/changes va Calendar sync-token walkerlar; resumable cursor, atomik commit, owner session/credential fencing; read-only Google write reconciliation; Google sync UI va HTTP source qo‘shildi. Test natijalari fake Google transport bilan, real providerga ulanish tasdiqlanmagan.

> **Joriy development: v0.3.6, oraliq checkpoint.** Yangi source, audit va 709 ta lokal test dalili: [development reyestri](docs/development/PROGRESS-UZ.md) va [implementatsiya hisoboti](docs/development/V036-IMPLEMENTATION-UZ.md). To‘liq mahsulot emas. Quyidagi oldingi bo‘limlar v0.3.5 va avvalgi holat kontekstini saqlaydi.

# Agent Platform v0.3.5, enterprise SQL preview

**486 offline test PASS / PRODUCTION NO-GO.** Joriy holat: [v0.3.5 checkpoint va audit](docs/CHECKPOINT-V035-UZ.md), [SQL/NoSQL shartnomasi va haqiqiy moslik](docs/MANAGED-DATABASES-UZ.md). Aniq test dalillari `docs/verification/v035/summary.json` da. Bu davom etayotgan loyiha, barcha DB va CRM integratsiyalari tayyor emas. [Oldingi v0.3.3 holati](docs/CHECKPOINT-V033-UZ.md) tarixiy dalil sifatida saqlanadi.

Connector revoke, agent scope, capability va config validation tuzatildi. Scope-aware read probe API/UI source’i qo‘shildi. 293 Python runtime, 15 runner, 8 browser-session va 3 release-tool testi o‘tdi. Python hamda JS/TS sintaksisi tekshirildi; bu HTTP/React typecheck/Next build o‘rnini bosmaydi. Next.js 14.2.5 xavfsizlik yangilanishi, provider adapterlari va live acceptance ochiq.

Qayta tekshirish: `python scripts/verify_offline.py`. Dalillar: `docs/verification/v033/`. Original v0.3.2 fayllarining tarixiy statuslari quyida saqlandi; eng yangi checkpoint ustuvor.

---

# Agent Platform v0.3.2, agent loop source checkpointi

**SOURCE_ONLY / NOT_RUN / PRODUCTION NO-GO.** Persisted, budget-cheklangan result-fed agent loop, model adapter, HTTP/worker ulanishi va dashboard source’i yozildi. v0.3.1 xavfsizlik tuzatishlari saqlandi. Feature standart holatda o‘chiq.

Joriy tafsilot: `docs/CHECKPOINT-V032-UZ.md`. Hech qanday test/build/typecheck/lint/compile/demo yoki live acceptance bajarilmadi. Model javobiga source IDs tekshiruvi qo‘shildi, lekin semantik fakt tekshiruvi va haqiqiy pul budjeti hali yo‘q. Eski workerga rollback yangi parent-run fence’ni olib tashlashi mumkin.

Quyidagi v0.3.1/v0.3 statuslari tarixiy snapshotlar, yangi versiya uchun PASS emas.

---

# Agent Platform v0.3.1, kod va review checkpointi

**SOURCE_ONLY / NOT_RUN / PRODUCTION NO-GO.** Joriy o‘zgarishlar: `docs/CHECKPOINT-V031-UZ.md`. Ishlash kelishuvi: `docs/WORKING-AGREEMENT-UZ.md`.

Control-plane mutation authorization, atomik device audit/fencing, replay authority, Customer 360 actor talabi va qat’iy HTTP input uchun source o‘zgarishlari yozildi. Yangi regression test fayllari tayyorlandi, ammo foydalanuvchi tanloviga ko‘ra **hech qanday loyiha testi, build, typecheck, lint, compile yoki demo bajarilmadi**. Joriy dalil: `docs/verification/v031/`.

Quyidagi v0.3 va undan eski qo‘llanmalar tarixiy. Ularning PASS raqamlari, login yo‘riqlari yoki cheklovlari yangi checkpointni avtomatik tasdiqlamaydi.

---

# Agent Platform v0.3 hardening preview

**Production: NO-GO.** Eng yangi holat: `docs/prd-v03/00-HARDENING-PRD-UZ.md`, `docs/AUDIT-V03-UZ.md` va `docs/RUNBOOK-V03-UZ.md`.

Joriy source: session-bound login/workspace UI, gated provisioning, replay-safe refresh families, worker authority checks, Customer 360 integrity va PostgreSQL read-only contract. Mahalliy dalillar `docs/verification/v03/`da. HTTP, React build, provider live va production rollout hali tasdiqlanmagan.

Quyidagi v0.2 yozuvlari tarixiy kontekst. Joriy test holati va cheklovlar uchun v0.3 evidence asosiy manba.

# Agent Platform 0.2, tekshirilgan foundation preview

**Productionga tayyor emas.** Source asosida xavfsizlik tuzatishlari, real SQLite-export read-only connectori, Aisha REST TTS/STT adapterlari va rolga mos dashboard o‘zgarishlari qo‘shilgan. Universal CRM/ERP, haqiqiy signup/workspaces, billing va streaming hali tayyor emas.

Birinchi o‘qing: [Audit](docs/AUDIT-V02-UZ.md), [PRDlar](docs/prd-v02/00-INDEX.md), [Ishga tushirish](docs/V02-RUNBOOK-UZ.md), [test dalili](docs/verification/v02-summary.json).

Lokal runtime: `cd api-python && python -m unittest discover -s runtime_tests -t runtime_tests`. `-t` berilmaganda discovery `Start directory is not importable: 'runtime_tests'` bilan yiqiladi, chunki `runtime_tests` package emas. Runner: `node --test apps/runner/test.js`. Hozirgi offline to‘plam: **2784 test** (`failures=1, errors=11, skipped=1` — hammasi oldindan mavjud, qarang: `docs/development/LOCAL-VERIFICATION-UZ.md`). To‘liq HTTP/UI sinovlari bloklangan, mahalliy muhitda deps yo‘q.

Xavfsizlik o‘zgarishi: `ENV=dev`ning o‘zi anonim adminni ochmaydi. `ALLOW_INSECURE_DEV=true` faqat izolyatsiyalangan test/demo uchun. Oddiy deploy unique secret talab qiladi. MCP chaqiruvida explicit `tool_schemas` majburiy.

SQL Server/Oracle chegaralari: [enterprise SQL preview](docs/ENTERPRISE-SQL-PREVIEW-UZ.md). Yangi arxiv yaxlitligi: `python scripts/verify_manifest.py`.

## Oldingi v0.1 qo‘llanma, tarixiy kontekst

# Agent Platform 0.1.0, engineering preview

Bu paket mavjud loyihani davom ettiradi: umumiy core + tenant pack + nazorat qilinadigan tool ijrosi. Telegram mahsulotning o‘zi emas, faqat kirish kanallaridan biri.

**Bu PRD to‘liq bajarilgan production mahsulot EMAS.** Yangi runtime kodining bajarilgan testlari bor, lekin FastAPI integratsiya testlari, Next production build, haqiqiy providerlar va staging ushbu muhitda tekshirilmagan. To‘liq holat: `docs/IMPLEMENTATION-STATUS.md`. Eski baseline fayllaridagi test raqamlari bu versiya uchun dalil emas.

## Eng tez ishlaydigan tekshiruv

Python 3.11+ bilan repository ildizidan, tashqi package va kalitsiz:

````sh
python scripts/demo_runtime.py
cd api-python
python -m unittest discover -s runtime_tests -t runtime_tests
````

Demo haqiqiy vaqtinchalik SQLite bazasida write vazifani yaratadi, approval oldidan to‘xtatadi, tasdiqlaydi, yozuvni saqlaydi va replay bir xil taskni qaytarishini tekshiradi. Bu LLM demo emas.

## Lokal UI + API + worker

Lokal Docker va internetga chiqish talab qilinadi. Ushbu buyruqlar foydalanuvchi kompyuterida bajariladi:

````sh
python scripts/setup_local.py
docker compose up --build -d
python scripts/owner_login.py
````

`http://localhost:3000` ni oching. Tenant: `demo-retail`. `owner-token.local.txt` ichidagi lokal JWT ni UI token maydoniga kiriting. Tokenni boshqalarga bermang. API OpenAPI: `http://localhost:8000/docs`. Bu manzillar lokal xizmatlar uchun, tayyor hosting URL emas.

UI’da `ops.assistant` tanlab standart `reports.summary` planini yuboring. Alohida worker ishni bajaradi, **Yangilash** orqali natijani oching. Keyin `records.create` yozuvini yuboring: u task detail ichida tasdiq kutadi. Barcha write tool’lar, hatto autonomous agent uchun ham, ushbu preview’da per-action approval talab qiladi.

Matnli `/report`, `/memory savol`, `/record kind|title|body` deterministik demo yo‘llari. Boshqa matn uchun sozlangan LLM kerak. LLM bo‘lmasa input yo‘qolmaydi, inbox `failed` bo‘ladi, konfiguratsiya to‘g‘rilangach operator retry qiladi.

## Identity va workspace foundation

`/identity/bootstrap` first-run user + workspace yaratadi. `/identity/login`, `/identity/refresh`, invitation va membership revoke endpointlari mavjud. Pilot/stagingdan oldin `IDENTITY_DIRECTORY=true` qilib, platform authorizationni active membershipga bog‘lang. Bu previewda OIDC/JWKS va MFA hali qo‘shilmagan, shuning uchun production login sifatida qabul qilmang.

## Tool va kanal sozlash

`config/integrations.example.json` namunani o‘rganing. Kerakli bo‘limlarni `config/integrations.json` ga ko‘chiring. Haqiqiy kalitlar faqat `api-python/.env` ichida, JSON esa environment variable nomlarini saqlaydi. `.env` va haqiqiy integration JSON gitignore qilingan.

Provider ruxsatlari, model ID, Meta account va API versiyasi sizning hisobingizda haqiqatan mavjud bo‘lishi kerak. Namunadagi `SET_...` qiymatlar ishlaydigan konfiguratsiya emas. Secrets rotation va OAuth token renewal avtomatlashtirilmagan.

Telegram: webhook secretni sozlang, HTTPS endpointga provider orqali ulang. Instagram: production’da account ID aynan bitta tenantga moslanadi va raw body HMAC tekshiriladi. Faqat Instagram Login API outbound varianti kiritilgan; Facebook Login varianti uchun alohida adapter kerak.

`telegram.send`, `instagram.send`, `sheets.append`, `mcp.call` haqiqiy HTTP transport kodiga ega, ammo haqiqiy servis bilan sinov o‘tkazilmagan. MCP uchun server URL va allowed_tools operator tomonidan belgilanadi, agent pack ham `mcp.call` ga ruxsat berishi kerak. Telegram/Instagram inbound javobi faqat shu suhbatga yuborilishi mumkin. Web/cron’dan bevosita yuborish uchun agent pack ichidagi `allowed_recipients` ro‘yxati talab qilinadi.

## Runner

Yangi runner Node 22.4+ ishlatadi, npm dependency talab qilmaydi. Bu preview’da filesystem tool ijrosi **faqat Linux** uchun yoqilgan. Windows/macOS, printer, office, app launch, screen automation, imzolangan update hali tayyor emas. Eski simulated runner faqat `docs/legacy/runner.js.txt` tarixiy ma’lumot sifatida saqlangan.

UI’da Owner qurilma qo‘shadi va 24-soatlik device JWT oladi. `apps/runner/allow.example.json` dan `allow.json` yarating, faqat maxsus ajratilgan xavfsiz kataloglarni ko‘rsating. Linux yo‘llarini ishlating, masalan `/home/user/agent-shared`.

````sh
cd apps/runner
node --test test.js
# RUNNER_TOKEN: UI'da yaratilgan device JWT, xavfsiz environment orqali bering.
# RUNNER_SERVER: ws://127.0.0.1:8000/platform/runner/ws yoki masofada wss://...
node runner.js
````

Pack agenti `ops.device_observer`. Plan misoli:

````json
[{"tool":"fs.list","args":{"dir":"/home/user/agent-shared"},"device":"office-1"}]
````

Journalda task ID ko‘rilgan bo‘lsa qayta bajarilmaydi. Lease eskirsa yoki kill/freeze sodir bo‘lsa in-flight task `uncertain` bo‘ladi. Bu fizik jarayonni ortga qaytarishni yoki tashqi tizimda exactly-once ni kafolatlamaydi. Masofaviy runner WSS talab qiladi.

## Muhim fayllar

`api-python/platform_runtime/` executable engine, typed tools, LLM va MCP. `api-python/app/platform_api.py` control plane, `worker.py` persistent execution. `apps/ui/app/platform/` boshqaruv UI. `runtime_tests/` dependency-free testlar; `integration_tests/` bajarilishi kerak bo‘lgan FastAPI testlar. `api-python/tests/` tarixiy legacy kontraktlar, hammasi bu yangi runtime acceptance’i sifatida o‘tishi kutilmaydi.

`docs/RUNBOOK.md` operatsion tartiblarni, `docs/IMPLEMENTATION-STATUS.md` bajarilmagan ishlarni aniq belgilaydi.

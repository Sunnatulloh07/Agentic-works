# Agent Platform — v0.5 development checkpoint

**IN_PROGRESS · production NO_GO · mahsulot emas, tekshirilgan platforma yadrosi.**

Bu repo umumiy core + tenant pack + nazorat qilinadigan tool ijrosidan iborat. Telegram
mahsulotning o‘zi emas, faqat kirish kanallaridan biri.

| Ko‘rsatkich | Holat (2026-09-22 da o‘lchangan) |
|---|---|
| `runtime_tests` (Windows, `cryptography` o‘rnatilgan) | **3 239 sinov**, `failures=1, errors=12` — **13 tasi Windows-only** (`os.O_NOFOLLOW`, `mkfifo`, `fcntl`, symlink privilegiyasi, POSIX fayl rejimlari) |
| `integration_tests` | **192/192 PASS** (2026-09-23; `ENV=test ALLOW_INSECURE_DEV=true PIPELINE_MODE=platform IDENTITY_DIRECTORY=false`, `cryptography` o‘rnatilgan). Legacy to‘plamdan ko‘chirilgan 5 fayl shu songa kiradi |
| `api-python/tests/` (legacy) | **nafaqaga chiqarildi (2026-09-22)** — 33 qizil / 172 pass edi va hech bir gate uni yurgizmasdi; qarang `api-python/integration_tests/LEGACY-RETIRED.md` |
| CI `http` job | endi **yig‘iladi** (`integration_tests/conftest.py` qo‘shilgani uchun) |
| CI `ui_dependency_security` job | **FAIL** — `next@14.2.35` da 1 critical + 1 high; tuzatish Next 15 + React 19 talab qiladi |
| Live provider acceptance | **hech biri** — eng yaxshisi `LOCAL_CONTRACT_TESTED` |
| API bu mashinada ishga tushirilganmi | **yo‘q** — `api-python/.env` yo‘q, `config/integrations.json` yo‘q, `api-python/data/app.db` da faqat migratsiya qatori bor |

Joriy dalil: [`docs/development/PROGRESS-UZ.md`](docs/development/PROGRESS-UZ.md),
[`docs/development/QOLGAN-ISHLAR-INVENTAR-UZ.md`](docs/development/QOLGAN-ISHLAR-INVENTAR-UZ.md),
[`docs/IMPLEMENTATION-STATUS.md`](docs/IMPLEMENTATION-STATUS.md).
Birinchi marta ishga tushirayotgan bo‘lsangiz: [`docs/ONBOARDING-UZ.md`](docs/ONBOARDING-UZ.md).

Tarixiy versiya bannerlari hujjat oxirida, **Tarixiy holat** bo‘limida saqlangan.

---

## Eng tez ishlaydigan tekshiruv

Python 3.11+ bilan repository ildizidan, tashqi package va kalitsiz:

````sh
python scripts/demo_runtime.py
cd api-python
python -m unittest discover -s runtime_tests -t runtime_tests
````

Demo haqiqiy vaqtinchalik SQLite bazasida write vazifani yaratadi, approval oldidan
to‘xtatadi, tasdiqlaydi, yozuvni saqlaydi va replay bir xil taskni qaytarishini
tekshiradi. Bu LLM demo emas.

`-t` berilmaganda discovery `Start directory is not importable: 'runtime_tests'` bilan
yiqiladi, chunki `runtime_tests` package emas.

## Birinchi ishga tushirish (lokal UI + API + worker)

> **Eski `setup_local.py` → `owner_login.py` ketma-ketligi ishlamaydi.** `setup_local.py`
> `.env` ichiga `IDENTITY_DIRECTORY=true` va `IDENTITY_BOOTSTRAP_ENABLED=false` yozadi;
> shundan keyin `owner_login.py` chaqiradigan `POST /auth/token` **410 «Use session-bound
> identity login»** qaytaradi, `POST /identity/bootstrap` esa **403 «Bootstrap disabled»**
> qaytaradi. Ustiga-ustak UI’da token qo‘yish maydoni **yo‘q** — `SessionGate.tsx`
> email/parol formasi. Yagona ishlaydigan first-run yo‘li — `provision_identity.py`.

````sh
# 1. Muhit. cryptography OAuth/Google funksiyalari va to'liq test to'plami uchun SHART.
cd api-python
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt  # cryptography shu ro'yxatda
cd ..

# 2. Lokal config. Random secret yaratadi, hech nima chop etmaydi.
python scripts/setup_local.py

# 3. Birinchi owner hisobi. APP_DB va PACKS_DIR API bilan AYNAN bir xil bo'lishi shart.
#    Berilmasa: APP_DB=api-python/data/app.db, PACKS_DIR=packs (repo ildizi).
#    Skript email, ism va parolni interaktiv so'raydi; hech qanday token chop etmaydi.
python scripts/provision_identity.py --workspace demo-retail --workspace-name "Demo"

# 4. Xizmatlar. Docker varianti:
docker compose up --build -d
#    yoki lokal venv varianti (uchta alohida terminal):
#      cd api-python && uvicorn app.main:app --port 8000
#      cd api-python && python -m app.worker
#      cd apps/ui && npm ci && npm run dev

# 5. http://localhost:3000 — 3-qadamda kiritgan email/parol bilan kiring.
#    API OpenAPI: http://localhost:8000/docs
````

Uchinchi qadam `app.packs` va `app.identity_store` ni import qiladi, ya’ni
**FastAPI, PyYAML va pydantic o‘rnatilgan bo‘lishi kerak** (1-qadam buni beradi).
`--workspace template` ataylab rad etiladi.

**Docker bilan muhim chegara:** compose `APP_DB=/srv/data/app.db` ni `apidata` nomli
volumega bog‘laydi va `api-python/Dockerfile` `scripts/` katalogini image ichiga
**ko‘chirmaydi**. Demak host’da yaratilgan identity compose bazasiga **yetib bormaydi**.
Docker yo‘lini tanlasangiz, provisioning konteyner ichida, `scripts/` mount qilingan va
`PYTHONPATH` `/srv` ga qo‘yilgan holda bajarilishi kerak. Bu variant bu mashinada
**NOT_RUN** — tekshirilmagan. Eng qisqa tasdiqlangan yo‘l — yuqoridagi lokal venv
varianti.

UI’da `ops.assistant` tanlab standart `reports.summary` planini yuboring. Alohida worker
ishni bajaradi, **Yangilash** orqali natijani oching (UI avtomatik polling qilmaydi).
Keyin `records.create` yozuvini yuboring: u task detail ichida tasdiq kutadi.

Matnli `/report`, `/memory savol`, `/record kind|title|body` deterministik demo yo‘llari.
Boshqa matn uchun sozlangan LLM kerak. LLM bo‘lmasa input yo‘qolmaydi, inbox `failed`
bo‘ladi, konfiguratsiya to‘g‘rilangach operator retry qiladi.

## Approval va avtonomiya

Approval **har bir write uchun sukut**. Bitta istisno qo‘shildi (2026-09-22,
`engine.py::_preauthorized`): **`autonomous`** ladderdagi agent outbound write’ni
per-message tasdiqsiz yuborishi mumkin, **faqat** qabul qiluvchi ikkidan biri bo‘lsa:

- pack’dagi `allowed_recipients` ro‘yxatida bor; yoki
- task javob berayotgan **tasdiqlangan inbound suhbat** (o‘sha kanal, o‘sha
  `conversation_id`).

Qolgan hamma narsa o‘zgarmadi: `human_led` hamma narsani ushlaydi, `human_assisted`
o‘zgarmadi, `approval:` ro‘yxatidagi tool nomlari har doim kutadi va **`destructive`
hamda `physical` risk hech qachon nazoratsiz bajarilmaydi**.

Brifing (`briefing.py`) va eskalatsiya (`escalation.py`) endi o‘z transportiga ega emas:
ikkalasi ham `engine.submit` orqali oddiy engine task’i sifatida yuboriladi. Ya’ni ular
approval gate’ini **chetlab o‘tmaydi** — rejalashtirilgan xabar ham xuddi oddiy plan
qadami kabi engine siyosatidan, dispatch paytidagi qabul qiluvchi qayta tekshiruvidan va
`uncertain` holatidan o‘tadi. Jurnal `submitted` qatori engine verdikti bilan yopiladi.

## Qamrov: nima haqiqatan yetib boradi

Registry’da **89** tool bor, lekin **12 tasigina** yetkazib berilayotgan pack’lardan
(`demo-retail`, `marketing`, `_template`) chaqirilishi mumkin. `platform_runtime`
kodining **81%** shu uch pack’dan **yetib bo‘lmaydi**.

Quyidagi modullarni ishlatadigan **birorta pack yo‘q**:

`erp` · `documents` · `inventory` · `business_graph` · `whatsapp` · `whatsapp_inbound` ·
`telephony` · `assets` · `vision` · `manufacturing` · `oee` · `workforce` · `supervisor` ·
`reengagement` · `escalation` · `briefing` · `oversight`

WhatsApp inbound uchun **umuman HTTP route yo‘q**.

Bular **muzlatilgan preview modullar**, mahsulot funksiyasi emas. Ular test va chegara
auditi bilan qoplangan, ammo hech bir mijoz konfiguratsiyasi ularga yetib bormaydi.
Hujjatning boshqa joyida backend «tayyor» deb yozilgan bo‘lsa, u **yadro** haqida —
bu modullar haqida emas.

## Identity va workspace foundation

`/identity/login`, `/identity/refresh`, invitation va membership revoke endpointlari
mavjud. `/identity/bootstrap` first-run user + workspace yaratadi, ammo u faqat
`IDENTITY_BOOTSTRAP_ENABLED=true` bo‘lganda ochiq va `X-Admin-Token` talab qiladi;
`setup_local.py` uni ataylab `false` qiladi. Pilot/stagingda `IDENTITY_DIRECTORY=true`
platform authorizationni active membershipga bog‘laydi.

OIDC/JWKS va MFA hali qo‘shilmagan, shuning uchun buni production login sifatida qabul
qilmang.

## Tool va kanal sozlash

Konfiguratsiya ikki qatlamdan o‘qiladi. **Ustuvorlik: pack-local avval, operator JSON
keyin** (`platform_runtime/tools.py::config`):

1. `packs/<tenant>/integrations.yaml` — tenant’ning o‘z integratsiya ta’rifi. Namuna:
   `packs/_template/integrations.example.yaml`.
2. `config/integrations.json` (`PLATFORM_INTEGRATIONS_FILE`) — operator darajasidagi
   fallback.

Pack-local fayl **mavjud bo‘lsa, u yakuniy** — parse xatosi bo‘lsa ham operator JSON’ga
jimgina qaytilmaydi, xato ochiq beriladi. Tenant nomi pack charset’iga solishtiriladi va
yo‘l `PACKS_DIR` ildizidan chiqolmaydi.

Ikkala fayl ham **faqat environment variable nomlarini** saqlaydi. **Hech qachon secret
qiymatini** ularga yozmang. Haqiqiy kalitlar faqat `api-python/.env` yoki vault ichida.
`.env` va haqiqiy integration JSON gitignore qilingan. Namuna:
`config/integrations.example.json`.

Provider ruxsatlari, model ID, Meta account va API versiyasi sizning hisobingizda
haqiqatan mavjud bo‘lishi kerak. Namunadagi `SET_...` qiymatlar ishlaydigan
konfiguratsiya emas. Secrets rotation va OAuth token renewal avtomatlashtirilmagan.

Telegram: webhook secretni sozlang, keyin uni Telegram’da **aynan shu secret bilan**
ro‘yxatdan o‘tkazing, aks holda API har real update’ni 401 bilan rad etadi (header
faqat `secret_token` bilan ro‘yxatdan o‘tgan webhook’ga keladi):

````sh
python scripts/telegram_set_webhook.py --url https://SIZNING-DOMEN/webhooks/telegram?tenant=demo-retail \
    --token-env DEMO_TELEGRAM_TOKEN --secret-env TELEGRAM_WEBHOOK_SECRET --dry-run
````

`--dry-run` so‘rovni token yashirilgan holda ko‘rsatadi; olib tashlasangiz, haqiqiy
`setWebhook` chaqiriladi. Skript token ham, secret ham chop etmaydi. Instagram:
production’da account ID aynan bitta tenantga moslanadi va raw body HMAC tekshiriladi.
Faqat Instagram Login API outbound varianti kiritilgan; Facebook Login varianti uchun
alohida adapter kerak.

`telegram.send`, `instagram.send`, `sheets.append`, `mcp.call` haqiqiy HTTP transport
kodiga ega, ammo **haqiqiy servis bilan sinov o‘tkazilmagan**. MCP uchun server URL va
allowed_tools operator tomonidan belgilanadi, agent pack ham `mcp.call` ga ruxsat berishi
kerak. Telegram/Instagram inbound javobi faqat shu suhbatga yuborilishi mumkin.
Web/cron’dan bevosita yuborish uchun agent pack ichidagi `allowed_recipients` ro‘yxati
talab qilinadi.

## Model tanlash

Planner **OpenAI `/chat/completions` shaklida** yozilgan (`platform_runtime/llm.py`,
`base_url` + `/chat/completions`). Buning amaliy natijalari:

| Provider | Holat |
|---|---|
| OpenAI va OpenAI-mos endpointlar | to‘g‘ridan-to‘g‘ri ishlaydi (`base_url` almashtiriladi) |
| Gemini | OpenAI-mos endpoint beradi, shuning uchun shu yo‘l bilan ulanadi |
| Claude (Anthropic) | **native Messages API adapteri yozildi** (`model_transport.py`, `provider: anthropic`) — `LOCAL_CONTRACT_TESTED`, live chaqiruv yo‘q |

Anthropic dialekti `/chat/completions` emas: `POST /v1/messages`, `x-api-key` (Bearer
emas), majburiy `anthropic-version: 2023-06-01`, `system` **top-level maydon**, va
`temperature` / `response_format` **yuborilmaydi**. Javob `parse_anthropic_decision`
bilan o‘qiladi. Bu qatlam faqat lokal kontrakt testlari bilan qoplangan — **haqiqiy
Anthropic endpointiga chaqiruv qilinmagan**.

**Model nomlari haqida:** Claude Haiku 3 va Haiku 3.5 Anthropic tomonidan **nafaqaga
chiqarilgan (retired)**. Joriy kichik model — **`claude-haiku-4-5`**. `docs/`
ichidagi eski PRD matnlari (`agent-platform-PRD-TZ.md`) hali «Haiku 3.5» deb yozadi —
bu **eskirgan**, rejalashtirishda ishlatmang.

`model` konfiguratsiyasi majburiy va explicit: `llm.py` bo‘sh yoki 256 belgidan uzun
model nomini rad etadi.

## Pack maydonlari: e’lon qilingan va o‘qiladigan

`pack.yaml` ichidagi ba’zi maydonlar schema’da bor, lekin hali **hech bir kod ularni
o‘qimaydi**. To‘liq jadval: [`CONTEXT.md`](CONTEXT.md) → «Pack maydonlari». Qisqacha:
`persona:` **ishlaydi** (2026-09-15 dan agent system prompt’iga yuklanadi), `triggers`,
`language`, `memory.scope`, `tool_policy`, `layout`, `theme` esa **e’lon qilingan, hali
iste’mol qilinmaydi**.

## Runner

Runner Node 22.4+ ishlatadi, npm dependency talab qilmaydi. Bu preview’da filesystem
tool ijrosi **faqat Linux** uchun yoqilgan. Windows/macOS, printer, office, app launch,
screen automation, imzolangan update hali tayyor emas. Eski simulated runner faqat
`docs/legacy/runner.js.txt` tarixiy ma’lumot sifatida saqlangan.

UI’da Owner qurilma qo‘shadi va 24-soatlik device JWT oladi. `apps/runner/allow.example.json`
dan `allow.json` yarating, faqat maxsus ajratilgan xavfsiz kataloglarni ko‘rsating. Linux
yo‘llarini ishlating, masalan `/home/user/agent-shared`.

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

Journalda task ID ko‘rilgan bo‘lsa qayta bajarilmaydi. Lease eskirsa yoki kill/freeze
sodir bo‘lsa in-flight task `uncertain` bo‘ladi. Bu fizik jarayonni ortga qaytarishni
yoki tashqi tizimda exactly-once ni kafolatlamaydi. Masofaviy runner WSS talab qiladi.

**Windows eslatma:** runner testlari Windows’da qizil (`privateFile()` POSIX ruxsat
bitlarini talab qiladi). Bu platforma cheklovi, kod nuqsoni emas.

## Test holati

````sh
cd api-python
python -m unittest discover -s runtime_tests -t runtime_tests

# HTTP to'plami — dependency'lar o'rnatilgan muhitda:
#   conftest.py ENV va ALLOW_INSECURE_DEV ni o'zi qo'yadi; qolgan ikkitasini bering.
PIPELINE_MODE=platform IDENTITY_DIRECTORY=false python -m pytest integration_tests -q
````

- `runtime_tests` — 3 239 sinov. Windows’da `failures=1, errors=12`; **hammasi 13 tasi
  Windows-only sabab bilan** (`os.O_NOFOLLOW`, `mkfifo`, `fcntl`, symlink privilegiyasi,
  POSIX fayl rejimlari) va `test_platform_baseline.py` har bir sababni **yurgizib**
  tasdiqlaydi.
- `integration_tests` — **95/95 PASS**. Ilgari umuman yig‘ilmas edi; `conftest.py`
  ordering bog‘liqligini yo‘q qildi. **Eslatma:** bu o‘lchovdan keyin nafaqaga
  chiqarilgan `tests/` dan 5 fayl shu papkaga ko‘chirildi, shuning uchun jami son
  o‘zgargan — qayta o‘lchang.
- `api-python/tests/` — **nafaqaga chiqarildi (2026-09-22)**. U 33 qizil / 172 pass edi va
  **hech bir gate uni yurgizmasdi**. Tirik kodni sinaydigan 5 fayl `integration_tests/`
  ichiga ko‘chirildi, qolgani o‘chirildi. Sabablar: `api-python/integration_tests/LEGACY-RETIRED.md`.
- CI: `core`, `http`, `ui` va `ui_dependency_security` joblari bor.
  `ui_dependency_security` **qizil** va 14.x liniyasida yopilmaydi.

## Muhim fayllar

`api-python/platform_runtime/` executable engine, typed tools, LLM va MCP.
`api-python/app/platform_api.py` control plane, `worker.py` persistent execution.
`apps/ui/app/platform/` boshqaruv UI. `runtime_tests/` dependency-free testlar;
`integration_tests/` FastAPI testlari (va nafaqaga chiqarilgan legacy to‘plamdan
saqlangan 5 fayl — `integration_tests/LEGACY-RETIRED.md`). `api-python/tests/` endi
**yo‘q**.

`docs/ONBOARDING-UZ.md` birinchi ishga tushirish, `docs/RUNBOOK.md` operatsion
tartiblar, `docs/IMPLEMENTATION-STATUS.md` bajarilmagan ishlar.

---

<details>
<summary><strong>Tarixiy holat</strong> — oldingi versiya bannerlari (joriy status emas)</summary>

Quyidagi matnlar o‘z sanasida yozilgan. Ularning PASS raqamlari, login yo‘riqlari yoki
cheklovlari **joriy checkpointni tasdiqlamaydi**. Yuqoridagi status bloki ustuvor.

### v0.5 avtonom development checkpoint

**IN_PROGRESS; production NO_GO; yakuniy mahsulot emas.** Joriy dalil:
`docs/development/PROGRESS-UZ.md`, `docs/development/V05-BLOCKS-CORE-UZ.md`,
`docs/prd-v05/00-ENTERPRISE-OPERATING-LAYER-PRD-UZ.md`.

Kross-tizim **Business Graph** qo‘shildi: operator e’lon qilgan entity’lar, har bir
atributda `source` va `observed`, ziddiyat **jimgina hal qilinmaydi**. Manba — mavjud
read tool, shuning uchun graph **o‘z vakolatini bermaydi**; ruxsatsiz manba bo‘lsa hech
bir manba o‘qilmaydi. Kamera/OT qatlami uchun arxitektura qarori yozildi (faqat
**hodisa**, hech qachon frame) va O‘zbekiston Law No. 1125 biometrik talabi hisobga
olindi. 38 yangi lokal test. Real provider acceptance o‘tkazilmagan.

### v0.3.8 checkpoint

Gmail metadata/history, Drive metadata/changes va Calendar sync-token walkerlar;
resumable cursor, atomik commit, owner session/credential fencing; read-only Google write
reconciliation; Google sync UI va HTTP source qo‘shildi. Test natijalari fake Google
transport bilan, real providerga ulanish tasdiqlanmagan.

### v0.3.6 oraliq checkpoint

Yangi source, audit va 709 ta lokal test dalili: [development reyestri](docs/development/PROGRESS-UZ.md)
va [implementatsiya hisoboti](docs/development/V036-IMPLEMENTATION-UZ.md). To‘liq mahsulot
emas.

### Agent Platform v0.3.5, enterprise SQL preview

**486 offline test PASS / PRODUCTION NO-GO.** Holat: [v0.3.5 checkpoint va audit](docs/CHECKPOINT-V035-UZ.md),
[SQL/NoSQL shartnomasi va haqiqiy moslik](docs/MANAGED-DATABASES-UZ.md). Test dalillari
`docs/verification/v035/summary.json` da. Barcha DB va CRM integratsiyalari tayyor emas.
[Oldingi v0.3.3 holati](docs/CHECKPOINT-V033-UZ.md) tarixiy dalil sifatida saqlanadi.

Connector revoke, agent scope, capability va config validation tuzatildi. Scope-aware read
probe API/UI source’i qo‘shildi. 293 Python runtime, 15 runner, 8 browser-session va 3
release-tool testi o‘tdi. Python hamda JS/TS sintaksisi tekshirildi; bu HTTP/React
typecheck/Next build o‘rnini bosmaydi. Next.js 14.2.5 xavfsizlik yangilanishi, provider
adapterlari va live acceptance ochiq.

Qayta tekshirish: `python scripts/verify_offline.py`. Dalillar: `docs/verification/v033/`.

### Agent Platform v0.3.2, agent loop source checkpointi

**SOURCE_ONLY / NOT_RUN / PRODUCTION NO-GO.** Persisted, budget-cheklangan result-fed
agent loop, model adapter, HTTP/worker ulanishi va dashboard source’i yozildi. v0.3.1
xavfsizlik tuzatishlari saqlandi. Feature standart holatda o‘chiq.

Tafsilot: `docs/CHECKPOINT-V032-UZ.md`. Hech qanday test/build/typecheck/lint/compile/demo
yoki live acceptance bajarilmadi. Model javobiga source IDs tekshiruvi qo‘shildi, lekin
semantik fakt tekshiruvi va haqiqiy pul budjeti hali yo‘q. Eski workerga rollback yangi
parent-run fence’ni olib tashlashi mumkin.

### Agent Platform v0.3.1, kod va review checkpointi

**SOURCE_ONLY / NOT_RUN / PRODUCTION NO-GO.** O‘zgarishlar:
`docs/CHECKPOINT-V031-UZ.md`. Ishlash kelishuvi: `docs/WORKING-AGREEMENT-UZ.md`.

Control-plane mutation authorization, atomik device audit/fencing, replay authority,
Customer 360 actor talabi va qat’iy HTTP input uchun source o‘zgarishlari yozildi. Yangi
regression test fayllari tayyorlandi, ammo foydalanuvchi tanloviga ko‘ra **hech qanday
loyiha testi, build, typecheck, lint, compile yoki demo bajarilmadi**. Dalil:
`docs/verification/v031/`.

### Agent Platform v0.3 hardening preview

**Production: NO-GO.** Holat: `docs/prd-v03/00-HARDENING-PRD-UZ.md`,
`docs/AUDIT-V03-UZ.md` va `docs/RUNBOOK-V03-UZ.md`.

Source: session-bound login/workspace UI, gated provisioning, replay-safe refresh
families, worker authority checks, Customer 360 integrity va PostgreSQL read-only
contract. Mahalliy dalillar `docs/verification/v03/`da. HTTP, React build, provider live
va production rollout tasdiqlanmagan.

### Agent Platform 0.2, tekshirilgan foundation preview

**Productionga tayyor emas.** Source asosida xavfsizlik tuzatishlari, real SQLite-export
read-only connectori, Aisha REST TTS/STT adapterlari va rolga mos dashboard
o‘zgarishlari qo‘shilgan. Universal CRM/ERP, haqiqiy signup/workspaces, billing va
streaming tayyor emas.

Birinchi o‘qing: [Audit](docs/AUDIT-V02-UZ.md), [PRDlar](docs/prd-v02/00-INDEX.md),
[Ishga tushirish](docs/V02-RUNBOOK-UZ.md), [test dalili](docs/verification/v02-summary.json).

O‘sha sanadagi offline to‘plam: **2814 test** (`failures=1, errors=11, skipped=1`), qarang:
`docs/development/LOCAL-VERIFICATION-UZ.md`.

Xavfsizlik o‘zgarishi: `ENV=dev`ning o‘zi anonim adminni ochmaydi. `ALLOW_INSECURE_DEV=true`
faqat izolyatsiyalangan test/demo uchun. Oddiy deploy unique secret talab qiladi. MCP
chaqiruvida explicit `tool_schemas` majburiy.

SQL Server/Oracle chegaralari: [enterprise SQL preview](docs/ENTERPRISE-SQL-PREVIEW-UZ.md).
Arxiv yaxlitligi: `python scripts/verify_manifest.py`.

### Agent Platform 0.1.0, engineering preview

Bu paket mavjud loyihani davom ettiradi: umumiy core + tenant pack + nazorat qilinadigan
tool ijrosi.

**Bu PRD to‘liq bajarilgan production mahsulot EMAS.** Yangi runtime kodining bajarilgan
testlari bor, lekin FastAPI integratsiya testlari, Next production build, haqiqiy
providerlar va staging o‘sha muhitda tekshirilmagan. Eski baseline fayllaridagi test
raqamlari bu versiya uchun dalil emas.

**Eskirgan first-run yo‘riqnomasi (ishlamaydi, tarix uchun saqlangan):**
`python scripts/setup_local.py` → `docker compose up --build -d` →
`python scripts/owner_login.py` → `owner-token.local.txt` ichidagi JWT ni UI token
maydoniga kiritish. Bu ketma-ketlik 410/403 bilan to‘xtaydi va UI’da token maydoni yo‘q;
yuqoridagi **Birinchi ishga tushirish** bo‘limiga qarang.

</details>

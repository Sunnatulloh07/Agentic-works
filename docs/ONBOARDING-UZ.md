# Birinchi ishga tushirish (onboarding)

**Holat (2026-09-23): 3–6-qadamlar Windows 11 / Python 3.14 da live tasdiqlangan**,
repo’dan tashqaridagi sandbox katalogda (`--env-file` orqali): `setup_local` →
`run_local.py --check` → `provision_identity.py --password-stdin` → `run_local.py` →
`/health` → `/identity/login` → Telegram webhook → tasdiq → `telegram.send` soxta
Telegram serverga (`telegram.base_url`, proxy’siz) yetib bordi. **Tasdiqlanmagan:** UI
(`npm run dev`), haqiqiy Telegram’da `setWebhook`, haqiqiy LLM provayder, Docker varianti.
Xato bilan uchrashsangiz, pastdagi **Tez-tez uchraydigan xatolar** bo‘limiga qarang.

Qisqa yo‘l:

````sh
python scripts/setup_local.py                      # .env + config/integrations.json
#   api-python/.env ni oching: DEMO_TELEGRAM_TOKEN, PLATFORM_LLM_KEY ni to'ldiring
python scripts/provision_identity.py --workspace demo-retail --workspace-name "Demo"
python scripts/run_local.py --check                # faqat PASS/FAIL, qiymat chop etilmaydi
python scripts/run_local.py                        # API :8000 + worker
cd apps/ui && npm ci && npm run dev                # boshqa terminalda
python scripts/telegram_set_webhook.py --url https://PUBLIC-HOST/webhooks/telegram?tenant=demo-retail \
    --token-env DEMO_TELEGRAM_TOKEN --secret-env TELEGRAM_WEBHOOK_SECRET
````

## 0. Nimaga eski yo‘riqnoma ishlamaydi

README’ning eski versiyasi shuni taklif qilardi:

````sh
python scripts/setup_local.py
docker compose up --build -d
python scripts/owner_login.py     # <- bu yerda to'xtaydi
````

Sabab zanjiri:

1. `scripts/setup_local.py` `.env` ichiga `IDENTITY_DIRECTORY=true` va
   `IDENTITY_BOOTSTRAP_ENABLED=false` yozadi.
2. `IDENTITY_DIRECTORY` yoqilgan bo‘lsa, `POST /auth/token`
   **`410 Use session-bound identity login`** qaytaradi (`app/main.py`).
   `owner_login.py` aynan shu endpointni chaqiradi.
3. Muqobil sifatida `POST /identity/bootstrap` ham yopiq:
   `IDENTITY_BOOTSTRAP_ENABLED` `true` emas, shuning uchun
   **`403 Bootstrap disabled`** (`app/identity_api.py`).
4. Hatto token olingan taqdirda ham, UI’da uni qo‘yadigan maydon **yo‘q**:
   `apps/ui/components/SessionGate.tsx` — bu email/parol formasi, token paste emas.

Ya’ni eski yo‘l uch joyda uziladi. Yagona ishlaydigan first-run yo‘li —
`scripts/provision_identity.py`.

## 1. Talablar

- **Python 3.11+**
- **Node 22+** (UI va runner uchun)
- **Docker** — faqat compose variantini tanlasangiz
- `api-python/requirements.txt` dagi paketlar: FastAPI, uvicorn, pydantic, PyJWT, PyYAML,
  httpx, `cryptography` va boshqalar

**`cryptography` haqida alohida gap.** U `requirements.txt` da bor va **kerak**:
OAuth/Google yuzalari hamda to‘liq test to‘plami usiz ishlamaydi.
`requirements-offline.txt` uni ataylab **ajratib** qo‘yadi (izohda yozilganidek, real
AES-GCM testlari uchun kerak, lekin offline verification’dan tashqarida o‘rnatiladi).
Ya’ni offline gate uchun `requirements-offline.txt` yetarli, **haqiqiy ishlatish uchun
esa `requirements.txt`**.

## 2. Muhit

````sh
cd api-python
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cd ..
````

## 3. Lokal konfiguratsiya

````sh
python scripts/setup_local.py
````

Bu ikkita fayl yaratadi, hech qanday secret ekranga chop etilmaydi:

- `api-python/.env` (`0600` rejimda): random `JWT_SECRET`, `ADMIN_TOKEN`, webhook
  secretlar; yo‘l qatorlari `APP_DB=api-python/data/app.db`, `PACKS_DIR=packs`,
  `PLATFORM_INTEGRATIONS_FILE=config/integrations.json` (nisbiy yo‘l repo ildiziga
  nisbatan o‘qiladi); va **bo‘sh** `DEMO_TELEGRAM_TOKEN=`, `PLATFORM_LLM_KEY=` qatorlari.
- `config/integrations.json` — faqat **mavjud bo‘lmasa**: `demo-retail` uchun `telegram`
  va `llm` bloklari, ichida faqat env **nomlari** (`token_env`, `key_env`), qiymat yo‘q.

`.env` allaqachon mavjud bo‘lsa skript uni **saqlab qoladi** (`Existing .env preserved`) —
bu ataylab.

### 3a. `.env` ni to‘ldirish

`api-python/.env` ni oching va ikki qatorni to‘ldiring:

- `DEMO_TELEGRAM_TOKEN=` — @BotFather bergan bot token;
- `PLATFORM_LLM_KEY=` — LLM provayder kaliti.

`config/integrations.json` dagi `llm` bloki sukut bo‘yicha Anthropic Messages API:
`"provider": "anthropic"`, `"model": "claude-opus-5"`, `"effort": "low"`,
`"agent_loop_enabled": true`. Demak `PLATFORM_LLM_KEY` — Anthropic API kaliti. Arzonroq
variant kerak bo‘lsa `model` ni `claude-sonnet-5` yoki `claude-haiku-4-5` ga almashtiring;
**Haiku 4.5 `effort` ni qabul qilmaydi** — u holda `"effort"` qatorini o‘chiring.
`agent_loop_enabled: true` bo‘lmasa sotuv agenti har mijozga faqat "operatorga uzatdim"
matnini yuboradi — `run_local.py --check` buni `FAIL  conversation ...` deb ko‘rsatadi.
Kalitlarni **faqat** `.env` ga yozing, JSON’ga emas.

Tekshirish (faqat qaysi tekshiruv o‘tgani/yiqilgani chiqadi, qiymatlar hech qachon):

````sh
python scripts/run_local.py --check
````

`FAIL  integrations demo-retail: empty or unset: PLATFORM_LLM_KEY` kabi qator qaysi env
nomi bo‘shligini aytadi.

## 4. Birinchi owner hisobi

Interaktiv (email, ism va parolni so‘raydi, parol ikki marta):

````sh
python scripts/provision_identity.py --workspace demo-retail --workspace-name "Demo"
````

Pipe/skript uchun — parol stdin’dan **bitta qator** bo‘lib o‘qiladi (Windows’da `getpass`
pipe’ni emas, konsolni o‘qiydi, shuning uchun interaktiv variant pipe ostida qotib qoladi):

````sh
echo "$OWNER_PASSWORD" | python scripts/provision_identity.py --workspace demo-retail \
    --workspace-name "Demo" --email owner@example.com --display-name "Ega" --password-stdin
````

Parol buyruq qatoriga tushmaydi va hech qanday token chop etilmaydi.

**Uch shart:**

1. Skript `api-python/.env` ni `run_local.py` bilan **bir xil** o‘qiydi, shuning uchun
   `APP_DB` va `PACKS_DIR` API bilan mos keladi. Boshqa `.env` uchun: `--env-file`.
2. FastAPI, PyYAML va pydantic o‘rnatilgan bo‘lishi kerak: skript `app.packs` va
   `app.identity_store` ni import qiladi (2-qadam buni beradi).
3. `--workspace` mavjud pack nomi bo‘lishi kerak: `demo-retail` yoki `marketing`.
   `template` ataylab rad etiladi.

## 5. Xizmatlarni ko‘tarish

### 5a. Lokal venv varianti (tasdiqlangan yo‘l)

````sh
python scripts/run_local.py                 # API http://127.0.0.1:8000 + worker
````

`run_local.py` `api-python/.env` ni yuklaydi (process env ustun turadi), keyin uvicorn va
`python -m app.worker` ni `api-python` katalogida bola jarayon sifatida ishga tushiradi.
Ctrl+C ikkalasini ham to‘xtatadi; bittasi o‘zi yiqilsa, ikkinchisi ham to‘xtatiladi va
skript nol bo‘lmagan kod bilan chiqadi. Bayroqlar: `--port 8010`, `--no-worker`,
`--check`, `--env-file`.

`uvicorn app.main:app` ni to‘g‘ridan-to‘g‘ri ishlatmang: `.env` ni hech narsa yuklamaydi va
API `ConfigError: ENV sozlanmagan` bilan yiqiladi.

UI — boshqa terminalda (**bu mashinada live tekshirilmagan**):

````sh
cd apps/ui && npm ci && npm run dev
````

### 5b. Docker compose varianti

````sh
docker compose up --build -d
````

**Diqqat — bu variantda 4-qadam boshqacha ishlaydi.** `docker-compose.yml`
`APP_DB=/srv/data/app.db` ni `apidata` nomli volumega bog‘laydi, `api-python/Dockerfile`
esa `scripts/` katalogini image ichiga **ko‘chirmaydi**. Demak:

- host’da yaratilgan identity compose bazasiga **yetib bormaydi**;
- provisioning konteyner ichida, `scripts/` mount qilingan va `PYTHONPATH` `/srv` ga
  qo‘yilgan holda bajarilishi kerak.

Bu variant bu mashinada **NOT_RUN** — tekshirilmagan. Birinchi marta ishga tushirayotgan
bo‘lsangiz 5a ni tanlang.

## 6. Kirish

`http://localhost:3000` ni oching va **4-qadamda kiritgan email/parol** bilan kiring.
Keyin workspace’ni tanlang. API OpenAPI: `http://localhost:8000/docs`.

Tokenlar faqat sahifa xotirasida saqlanadi — sahifa yopilsa qayta kirish kerak.

## 7. Birinchi vazifa

UI’da `ops.assistant` agentini tanlab standart `reports.summary` planini yuboring.
Worker alohida jarayon bo‘lgani uchun natija darhol chiqmaydi: **Yangilash** tugmasini
bosing (UI avtomatik polling qilmaydi).

Keyin `records.create` yozuvini yuboring — u task detail ichida **tasdiq kutadi**.
Approval har bir write uchun sukut.

Matnli `/report`, `/memory savol`, `/record kind|title|body` deterministik demo yo‘llari.
Boshqa matn uchun sozlangan LLM kerak. LLM bo‘lmasa input yo‘qolmaydi — inbox `failed`
bo‘ladi, konfiguratsiya to‘g‘rilangach operator retry qiladi.

## 8. Integratsiyalarni sozlash

Konfiguratsiya ikki qatlamdan o‘qiladi, **pack-local avval**:

1. `packs/<tenant>/integrations.yaml`
2. `config/integrations.json` (`PLATFORM_INTEGRATIONS_FILE`) — operator fallback

Ikkala fayl ham **faqat environment variable nomlarini** saqlaydi. **Hech qachon secret
qiymatini** ularga yozmang — haqiqiy kalitlar faqat `api-python/.env` yoki vault ichida.
Namuna: `config/integrations.example.json`.

Model tanlash haqida: planner asli OpenAI `/chat/completions` shaklida, Gemini esa
OpenAI-mos endpoint bergani uchun shu yo‘l bilan ulanadi. **Claude uchun native Messages
API adapteri bor** (`provider: anthropic` — `POST /v1/messages`, `x-api-key`,
`anthropic-version: 2023-06-01`, top-level `system`), lekin u faqat lokal kontrakt
testlari bilan qoplangan: **haqiqiy Anthropic endpointiga chaqiruv qilinmagan**.

Claude Haiku 3 / 3.5 **nafaqaga chiqarilgan** — joriy kichik model
`claude-haiku-4-5`. `docs/agent-platform-PRD-TZ.md` hali «Haiku 3.5» deb yozadi, bu
eskirgan.

## 9. Telegram webhook

Telegram faqat **ochiq https** manzilga yuboradi, `127.0.0.1:8000` unga ko‘rinmaydi. Lokal
ishlaganda API’ni tunnel orqali chiqaring (masalan, `cloudflared tunnel --url
http://127.0.0.1:8000` yoki `ngrok http 8000`) va bergan https manzilni ishlating:

````sh
python scripts/telegram_set_webhook.py --url https://PUBLIC-HOST/webhooks/telegram?tenant=demo-retail \
    --token-env DEMO_TELEGRAM_TOKEN --secret-env TELEGRAM_WEBHOOK_SECRET --dry-run
````

Skript env **nomlarini** oladi, qiymatlar `.env` dan emas, process env’dan o‘qiladi —
avval ularni shell’ga yuklang. `--dry-run` so‘rovni token yashirilgan holda ko‘rsatadi,
olib tashlasangiz haqiqiy `setWebhook` chaqiriladi. Ikkala holatda ham ro‘yxatga
olinadigan **aniq URL** `Webhook URL: ...` qatorida chiqadi.

**`?tenant=<nom>` shart.** Core’da sukut tenant yo‘q: tenant URL’dagi `?tenant=` dan,
`TELEGRAM_DEFAULT_TENANT` env’dan yoki `TENANT_SECRETS` dagi per-tenant secret’dan
aniqlanadi. Hech biri bo‘lmasa API `400 tenant kerak` qaytaradi; URL’da `tenant`
bo‘lmasa skript ogohlantiradi.

Media xabarlarda matn `caption` maydonida keladi va shunday o‘qiladi. Izohsiz rasm, video,
fayl yoki ovozli xabar `[rasm]`, `[video]`, `[fayl]`, `[ovozli xabar]` kabi belgi bilan
qabul qilinadi — agent mijozdan matn bilan yozishni so‘rashi yoki operatorga uzatishi
mumkin; faylning o‘zi yuklab olinmaydi. Stiker va servis xabarlar (masalan,
`new_chat_members`) `{"ok": true, "ignored": true}` bilan jim o‘tkazib yuboriladi.

Bu qadam haqiqiy Telegram bilan **tekshirilmagan** (faqat `--dry-run` va unit testlar).

Test yoki lokal fake server uchun Bot API manzilini tenant konfigida almashtirish mumkin:
`telegram.base_url`. Faqat https; `http://127.0.0.1:<port>` (raqamli loopback, port ≥ 1024)
faqat LLM bilan **aynan bir xil** aniq ruxsat bo‘lganda: `"provider_mode": "local_loopback"`.
`localhost` nomi rad etiladi.

## Tez-tez uchraydigan xatolar

| Belgi | Sabab | Yechim |
|---|---|---|
| `410 Use session-bound identity login` | `owner_login.py` yoki `/auth/token` ishlatildi, `IDENTITY_DIRECTORY=true` | `owner_login.py` ni ishlatmang; `provision_identity.py` + UI login |
| `403 Bootstrap disabled` | `IDENTITY_BOOTSTRAP_ENABLED=false` (`setup_local.py` shunday yozadi) | `provision_identity.py` ishlating |
| `Existing .env preserved. Edit it manually.` | `.env` allaqachon bor | Ataylab. Kerak bo‘lsa qo‘lda tahrirlang |
| `ConfigError: ENV sozlanmagan` | `uvicorn` to‘g‘ridan-to‘g‘ri ishga tushirildi, `.env` yuklanmadi | `python scripts/run_local.py` |
| `FAIL  integrations: no tenant has integration config` | `config/integrations.json` ham, `packs/<tenant>/integrations.yaml` ham yo‘q | `setup_local.py` ni ishga tushiring yoki fayllardan birini yarating |
| Webhook `400 tenant kerak` | URL’da `?tenant=` yo‘q | `?tenant=demo-retail` qo‘shing yoki `TELEGRAM_DEFAULT_TENANT` ni bering |
| `provision_identity.py` pipe ostida qotib qoldi | Windows’da `getpass` konsolni o‘qiydi | `--email`, `--display-name`, `--password-stdin` |
| `Template cannot be provisioned` | `--workspace template` berildi | `demo-retail` yoki `marketing` |
| `ModuleNotFoundError: fastapi` / `yaml` / `pydantic` | `requirements.txt` o‘rnatilmagan yoki venv faollashmagan | 2-qadamni qayta bajaring |
| OAuth/Google yuzalari ishlamaydi, AES-GCM testlari yiqiladi | `cryptography` o‘rnatilmagan | `pip install -r api-python/requirements.txt` |
| UI’da login qilib bo‘ldi, lekin workspace ro‘yxati bo‘sh | Identity boshqa `APP_DB` ga yozilgan (masalan, host vs compose volume) | `APP_DB` ni moslang va 4-qadamni to‘g‘ri baza bilan takrorlang |
| Task `queued` da qotib qoldi | Worker ishlamayapti | `run_local.py` ni `--no-worker` siz ishga tushiring |
| Natija yangilanmayapti | UI avtomatik polling qilmaydi | **Yangilash** tugmasi |
| `Start directory is not importable: 'runtime_tests'` | `-t` bayrog‘i berilmagan | `python -m unittest discover -s runtime_tests -t runtime_tests` |

## Nimani kutmaslik kerak

- **Live provider integratsiyasi** — hech biri `live_verified` emas. Telegram, Instagram,
  Sheets, Google, MoySklad, 1C: eng yuqorisi `LOCAL_CONTRACT_TESTED`.
- **Mahsulot funksiyalarining to‘liq to‘plami** — registry’dagi 89 tool’dan **12 tasi**
  yetkazilgan pack’lardan chaqiriladi. ERP, hujjatlar, ombor, WhatsApp, telefoniya,
  vision, ishlab chiqarish, OEE, xodimlar, supervisor, brifing, eskalatsiya va boshqa
  modullar **muzlatilgan preview** — birorta pack ularni ishlatmaydi. WhatsApp inbound
  uchun HTTP route umuman yo‘q.
- **Windows runner** — lokal-executor shartnomasi POSIX-only (`O_NOFOLLOW`, `mkfifo`,
  `0600`). Windows’da runner testlari qizil, bu platforma cheklovi.
- **Production** — `production_release: NO_GO`. HA, observability, DR va live acceptance
  yo‘q; UI dependency auditi qizil.

To‘liq ro‘yxat: [`development/QOLGAN-ISHLAR-INVENTAR-UZ.md`](development/QOLGAN-ISHLAR-INVENTAR-UZ.md)
va [`IMPLEMENTATION-STATUS.md`](IMPLEMENTATION-STATUS.md). Operatsion tartiblar:
[`RUNBOOK.md`](RUNBOOK.md).

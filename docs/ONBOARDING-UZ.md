# Birinchi ishga tushirish (onboarding)

**Holat: bu tartib kod o‘qib tekshirilgan, lekin live run bilan tasdiqlanmagan.**
API bu repo saqlanayotgan mashinada hech qachon ishga tushirilmagan — `api-python/.env`
yo‘q, `config/integrations.json` yo‘q, `api-python/data/app.db` da faqat migratsiya qatori
bor. Xato bilan uchrashsangiz, bu kutilgan holat; pastdagi **Tez-tez uchraydigan xatolar**
bo‘limiga qarang.

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

Bu `api-python/.env` (random `JWT_SECRET`, `ADMIN_TOKEN`, webhook secretlar, `0600`
rejimda) va bo‘sh `config/integrations.json` yaratadi. Hech qanday secret ekranga chop
etilmaydi. `.env` allaqachon mavjud bo‘lsa skript uni **saqlab qoladi** va to‘xtaydi —
bu ataylab.

## 4. Birinchi owner hisobi

````sh
python scripts/provision_identity.py --workspace demo-retail --workspace-name "Demo"
````

Skript email, ism va parolni (kamida 12 belgi) **interaktiv** so‘raydi. Parol buyruq
qatoriga tushmaydi va hech qanday token chop etilmaydi.

**Uch shart:**

1. `APP_DB` va `PACKS_DIR` API ishlatadigan qiymatlar bilan **aynan bir xil** bo‘lishi
   kerak. Berilmasa, sukut qiymatlar: `APP_DB=api-python/data/app.db`,
   `PACKS_DIR=<repo>/packs` — lokal (Docker’siz) variant uchun shu to‘g‘ri keladi.
2. FastAPI, PyYAML va pydantic o‘rnatilgan bo‘lishi kerak: skript `app.packs` va
   `app.identity_store` ni import qiladi (2-qadam buni beradi).
3. `--workspace` mavjud pack nomi bo‘lishi kerak: `demo-retail` yoki `marketing`.
   `template` ataylab rad etiladi.

## 5. Xizmatlarni ko‘tarish

### 5a. Lokal venv varianti (eng qisqa tasdiqlangan yo‘l)

Uchta alohida terminal:

````sh
cd api-python && uvicorn app.main:app --port 8000
cd api-python && python -m app.worker
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

## Tez-tez uchraydigan xatolar

| Belgi | Sabab | Yechim |
|---|---|---|
| `410 Use session-bound identity login` | `owner_login.py` yoki `/auth/token` ishlatildi, `IDENTITY_DIRECTORY=true` | `owner_login.py` ni ishlatmang; `provision_identity.py` + UI login |
| `403 Bootstrap disabled` | `IDENTITY_BOOTSTRAP_ENABLED=false` (`setup_local.py` shunday yozadi) | `provision_identity.py` ishlating |
| `Existing .env preserved. Edit it manually.` | `.env` allaqachon bor | Ataylab. Kerak bo‘lsa qo‘lda tahrirlang |
| `Template cannot be provisioned` | `--workspace template` berildi | `demo-retail` yoki `marketing` |
| `ModuleNotFoundError: fastapi` / `yaml` / `pydantic` | `requirements.txt` o‘rnatilmagan yoki venv faollashmagan | 2-qadamni qayta bajaring |
| OAuth/Google yuzalari ishlamaydi, AES-GCM testlari yiqiladi | `cryptography` o‘rnatilmagan | `pip install -r api-python/requirements.txt` |
| UI’da login qilib bo‘ldi, lekin workspace ro‘yxati bo‘sh | Identity boshqa `APP_DB` ga yozilgan (masalan, host vs compose volume) | `APP_DB` ni moslang va 4-qadamni to‘g‘ri baza bilan takrorlang |
| Task `queued` da qotib qoldi | Worker ishlamayapti | `python -m app.worker` ni ishga tushiring |
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

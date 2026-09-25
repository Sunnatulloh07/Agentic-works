# Operatsion runbook

## Lokal va production chegarasi

`setup_local.py` faqat lokal preview uchun random secretlar yaratadi. Compose portlari loopbackga bog‘langan. Internetga ochishdan oldin production ENV, HTTPS reverse proxy, origin allowlist, provider secrets, ingress rate limits, OS ACL, dependency audit va integratsiya testlarini yakunlang. Compose production certification emas.

`ENV=production`, kuchli `JWT_SECRET`, `ADMIN_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` talab qilinadi. `TENANT_SECRETS=tenant:secret,...` Telegram tenant secret mapping uchun ishlatiladi. Instagram account ID faqat bitta tenantga bog‘lanishi shart. Super-admin bearer role minting yo‘q, bootstrap `ADMIN_TOKEN` alohida server siri.

## Ishga tushirish

**Holat: bu repo saqlanayotgan mashinada API hech qachon ishga tushirilmagan.** `api-python/.env` yo‘q, `config/integrations.json` yo‘q, `api-python/data/app.db` da faqat migratsiya qatori bor. Quyidagi tartib **o‘qib tekshirilgan**, lekin live run bilan tasdiqlanmagan.

Birinchi ishga tushirish uchun [`docs/ONBOARDING-UZ.md`](ONBOARDING-UZ.md) yoki README’ning «Birinchi ishga tushirish» bo‘limini bajaring. Qisqasi:

1. `pip install -r api-python/requirements.txt` — `cryptography` shu yerda; OAuth/Google yuzalari va to‘liq test to‘plami usiz ishlamaydi.
2. `python scripts/setup_local.py` — lokal `.env` va bo‘sh integration mapping.
3. `python scripts/provision_identity.py --workspace demo-retail --workspace-name "Demo"` — **yagona ishlaydigan first-run yo‘li**.
4. `docker compose up --build -d`, yoki lokal `uvicorn app.main:app` + `python -m app.worker` (+ `npm run dev` UI uchun).
5. `http://localhost:3000` — 3-qadamda kiritilgan email/parol bilan kiring.

**Eski `scripts/owner_login.py` yo‘li ishlamaydi va uni ishlatmang.** `setup_local.py` `.env` ichiga `IDENTITY_DIRECTORY=true` va `IDENTITY_BOOTSTRAP_ENABLED=false` yozadi; shundan keyin `POST /auth/token` **410 «Use session-bound identity login»**, `POST /identity/bootstrap` esa **403 «Bootstrap disabled»** qaytaradi. UI’da token qo‘yish maydoni ham yo‘q — `SessionGate.tsx` email/parol formasi.

API va worker bir xil `APP_DB`, pack directory va integrations configuration ishlatishi shart. `provision_identity.py` ham **aynan o‘sha** `APP_DB` va `PACKS_DIR` bilan chaqirilishi kerak, aks holda identity boshqa bazaga yoziladi. Diqqat: compose `APP_DB=/srv/data/app.db` ni `apidata` nomli volumega bog‘laydi va image ichiga `scripts/` ko‘chirilmaydi — ya’ni host’da provisioning qilingan identity compose bazasida **ko‘rinmaydi**.

Integratsiya konfiguratsiyasi ikki qatlamdan o‘qiladi, **pack-local avval**: `packs/<tenant>/integrations.yaml`, keyin `config/integrations.json` (`PLATFORM_INTEGRATIONS_FILE`). Ikkalasi ham faqat environment variable **nomlarini** saqlaydi, hech qachon qiymatni.

LLM va provider kalitlari kerak bo‘lmasa config `{}` bilan `/report` va typed local tasklar ishlaydi.

Eski `.env`, database, WAL, approval log, trace yoki runner journalni yangi paketga avtomatik ko‘chirmang. Avval old deployni to‘xtating va shifrlangan backup oling. Eski JWT yangi issuer/audience/role formatiga mos emas; yangidan token bering. Oldin oshkor bo‘lgan API kalitlar haqiqiy bo‘lsa providerda bekor qiling va yangilang.

## Vazifa holatlari

`queued` worker kutadi. `waiting_approval` task detaildan operator yoki owner aniq argumentlarni tasdiqlaydi. `running` lease bor. `succeeded` tool natijasi saqlangan. `failed` aniq lokal/planning xato. `uncertain` side effect sodir bo‘lgan-bo‘lmaganini avtomatik bilib bo‘lmaydi. `cancelled` keyingi ish to‘xtatilgan.

`uncertain` uchun yangi key bilan takror task yaratmang. Avval provider receipt, record, local journal va auditni tekshiring. Owner `/platform/{tenant}/steps/{step}/reconcile` orqali `outcome=succeeded|failed` va haqiqiy `evidence` beradi. Bu endpoint side effectni qayta bajarmaydi. Hali tirik worker/device jarayonini to‘xtatib, keyin reconciliation qiling.

Har external write transactiondan tashqarida bajariladi. Exactly-once umumiy kafolat yo‘q. Telegram sendMessage va Sheets append yo‘qolgan javobdan keyin takrorlansa duplicate bo‘lishi mumkin, shuning uchun avtomatik retry yo‘q.

## Approval va avtonomiya

Approval har bir write uchun sukut. 2026-09-22 dan bitta istisno bor: **`autonomous`** ladderdagi agent outbound write’ni per-message tasdiqsiz yuborishi mumkin, **faqat** qabul qiluvchi pack’ning `allowed_recipients` ro‘yxatida bo‘lsa, yoki task javob berayotgan **tasdiqlangan inbound suhbat** bo‘lsa (o‘sha kanal, o‘sha `conversation_id`). `human_led` hamma narsani ushlaydi, `human_assisted` o‘zgarmadi, `approval:` ro‘yxatidagi tool nomlari har doim kutadi, **`destructive` va `physical` risk hech qachon nazoratsiz bajarilmaydi**.

Operator uchun amaliy xulosa: `allowed_recipients` ro‘yxati endi **xavfsizlik chegarasi** — unga qo‘shilgan har bir manzil avtonom agentga tasdiqsiz yozish huquqini beradi. Ro‘yxatni pack review’sisiz kengaytirmang.

Brifing va eskalatsiya endi o‘z transportiga ega emas: ikkalasi ham `engine.submit` orqali oddiy engine task’i sifatida yuboriladi. Rejalashtirilgan xabar ham approval gate’idan, dispatch paytidagi qabul qiluvchi qayta tekshiruvidan va `uncertain` holatidan o‘tadi; jurnaldagi `submitted` qatori engine verdikti bilan yopiladi.

## Freeze va device lifecycle

Owner freeze yangi claimlarni to‘xtatadi; oldingi running step uncertain bo‘ladi. Bu OS processni majburan kill qilish emas. Runner STOP fayli yoki SIGTERM lokal to‘xtatadi. Device token 24 soat, har WebSocket xabarda expiry va generation tekshiriladi. Re-enrollment eski queued tasklarni bekor qiladi, running tasklarni uncertain qiladi. User token refresh/revoke lifecycle hali yo‘q.

## Backup va restore

`python scripts/db_backup.py PATH_TO_DB PATH_TO_NEW_BACKUP` online SQLite backup qiladi va integrity tekshiradi. Backup tenant ma’lumotlarini o‘z ichiga oladi, sir sifatida saqlang. Restore oldidan API va worker to‘xtatilishi, barcha eski SQLite ulanishlar yopilishi kerak. DB hamda unga tegishli `-wal` va `-shm` holatini administrator nazoratida almashtiring; tirik bazaga oddiy copy qilmang. Bu paketda offsite encrypted retention avtomatlashtirilmagan.

## Test buyruqlari

````sh
cd api-python
python -m unittest discover -s runtime_tests -t runtime_tests -v

# Dependency'lar o‘rnatilgan haqiqiy development muhitida.
# conftest.py ENV=test va ALLOW_INSECURE_DEV=true ni o‘zi qo‘yadi; qolgan ikkitasi sizdan:
PIPELINE_MODE=platform IDENTITY_DIRECTORY=false python -m pytest integration_tests -q

cd ../apps/runner
node --test test.js
cd ../ui
npm ci
npm run typecheck
npm run build
````

O‘lchangan natijalar (2026-09-22, Windows, `cryptography` o‘rnatilgan venv):

| To‘plam | Natija |
|---|---|
| `runtime_tests` | **3 239 sinov**, `failures=1, errors=12` — **13 tasi Windows-only** (`os.O_NOFOLLOW`, `mkfifo`, `fcntl`, symlink privilegiyasi, POSIX fayl rejimlari). `test_platform_baseline.py` har bir sababni yurgizib tasdiqlaydi. |
| `integration_tests` | **95/95 PASS**, yuqoridagi env bilan |
| `apps/runner` node testlari | Windows’da 31 dan **11 tasi** qizil — sabablar endi `windows-baseline.test.js` da **qadalgan** (5 execute off-Linux, 2 symlink EPERM, 4 POSIX ruxsat bitlari) va har bir sabab haqiqatda yurgizib tasdiqlanadi. Platforma cheklovi, kod nuqsoni emas |
| `api-python/tests/` (legacy) | **33 qizil / 172 pass**, va **hech bir gate uni yurgizmaydi** |

Tarixiy `api-python/tests/` suite hozir **nafaqaga chiqarilmoqda** (alohida ish). Uning natijasini acceptance sifatida ishlatmang va uning qizilligini yangi regressiya deb hisoblamang.

CI (`.github/workflows/verify.yml`) to‘rt jobdan iborat: `core`, `http`, `ui`, `ui_dependency_security`.

- `http` job endi **yig‘iladi** — `integration_tests/conftest.py` collection ordering bog‘liqligini yo‘q qildi.
- `ui_dependency_security` **FAIL** — `next@14.2.35` da `npm audit` 1 critical + 1 high beradi. Tuzatish **Next 15 + React 19** migratsiyasini talab qiladi, 14.x liniyasida yopilmaydi.

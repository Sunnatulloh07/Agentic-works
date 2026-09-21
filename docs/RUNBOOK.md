# Operatsion runbook

## Lokal va production chegarasi

`setup_local.py` faqat lokal preview uchun random secretlar yaratadi. Compose portlari loopbackga bog‘langan. Internetga ochishdan oldin production ENV, HTTPS reverse proxy, origin allowlist, provider secrets, ingress rate limits, OS ACL, dependency audit va integratsiya testlarini yakunlang. Compose production certification emas.

`ENV=production`, kuchli `JWT_SECRET`, `ADMIN_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` talab qilinadi. `TENANT_SECRETS=tenant:secret,...` Telegram tenant secret mapping uchun ishlatiladi. Instagram account ID faqat bitta tenantga bog‘lanishi shart. Super-admin bearer role minting yo‘q, bootstrap `ADMIN_TOKEN` alohida server siri.

## Ishga tushirish

README lokal ketma-ketligini bajaring. API va worker bir xil `APP_DB`, pack directory va integrations configuration ishlatishi shart. LLM va provider kalitlari kerak bo‘lmasa config `{}` bilan `/report` va typed local tasklar ishlaydi.

Eski `.env`, database, WAL, approval log, trace yoki runner journalni yangi paketga avtomatik ko‘chirmang. Avval old deployni to‘xtating va shifrlangan backup oling. Eski JWT yangi issuer/audience/role formatiga mos emas; yangidan token bering. Oldin oshkor bo‘lgan API kalitlar haqiqiy bo‘lsa providerda bekor qiling va yangilang.

## Vazifa holatlari

`queued` worker kutadi. `waiting_approval` task detaildan operator yoki owner aniq argumentlarni tasdiqlaydi. `running` lease bor. `succeeded` tool natijasi saqlangan. `failed` aniq lokal/planning xato. `uncertain` side effect sodir bo‘lgan-bo‘lmaganini avtomatik bilib bo‘lmaydi. `cancelled` keyingi ish to‘xtatilgan.

`uncertain` uchun yangi key bilan takror task yaratmang. Avval provider receipt, record, local journal va auditni tekshiring. Owner `/platform/{tenant}/steps/{step}/reconcile` orqali `outcome=succeeded|failed` va haqiqiy `evidence` beradi. Bu endpoint side effectni qayta bajarmaydi. Hali tirik worker/device jarayonini to‘xtatib, keyin reconciliation qiling.

Har external write transactiondan tashqarida bajariladi. Exactly-once umumiy kafolat yo‘q. Telegram sendMessage va Sheets append yo‘qolgan javobdan keyin takrorlansa duplicate bo‘lishi mumkin, shuning uchun avtomatik retry yo‘q.

## Freeze va device lifecycle

Owner freeze yangi claimlarni to‘xtatadi; oldingi running step uncertain bo‘ladi. Bu OS processni majburan kill qilish emas. Runner STOP fayli yoki SIGTERM lokal to‘xtatadi. Device token 24 soat, har WebSocket xabarda expiry va generation tekshiriladi. Re-enrollment eski queued tasklarni bekor qiladi, running tasklarni uncertain qiladi. User token refresh/revoke lifecycle hali yo‘q.

## Backup va restore

`python scripts/db_backup.py PATH_TO_DB PATH_TO_NEW_BACKUP` online SQLite backup qiladi va integrity tekshiradi. Backup tenant ma’lumotlarini o‘z ichiga oladi, sir sifatida saqlang. Restore oldidan API va worker to‘xtatilishi, barcha eski SQLite ulanishlar yopilishi kerak. DB hamda unga tegishli `-wal` va `-shm` holatini administrator nazoratida almashtiring; tirik bazaga oddiy copy qilmang. Bu paketda offsite encrypted retention avtomatlashtirilmagan.

## Test buyruqlari

````sh
cd api-python
python -m unittest discover -s runtime_tests -v
# Dependency'lar o‘rnatilgan haqiqiy development muhitida:
python -m pytest integration_tests -q
cd ../apps/runner
node --test test.js
cd ../ui
npm ci
npm run typecheck
npm run build
````

Tarixiy `api-python/tests/` suite alohida migratsiya ishidir. Oldingi runner/JWT kontraktlarining testlari yangilanishi kerak. Ularni e’tiborsiz qoldirib production pass deyish mumkin emas. CI bu paketda yozilgan, lekin push yoki real CI run qilinmagan.

# v0.3 staging runbook

Production release emas. Avval alohida staging bazasi va test provider hisobi ishlatilsin.

## Muhit

`api-python/.env`ni `python scripts/setup_local.py` bilan lokal yarating. Script secretlarni konsolga chiqarmaydi. `IDENTITY_DIRECTORY=true`, `IDENTITY_BOOTSTRAP_ENABLED=false` default. Config environmentga supervisor/Docker env_file orqali beriladi. Python scriptlar `.env`ni o‘zicha yuklamaydi.

API va worker aynan bir xil `APP_DB`, `PACKS_DIR` va `PLATFORM_INTEGRATIONS_FILE` ko‘rishi kerak. Haqiqiy credentiallarni chatga, sourcega yoki test logga yozmang. Birinchi owner account uchun API runtime environmentida repo ildizidan:

````sh
python scripts/provision_identity.py --workspace demo-retail --workspace-name "Test biznes"
````

CLI email/ism/parolni interaktiv oladi, parol terminalda aks etmaydi. HTTP bootstrap faqat admin credential va explicit flag bilan ishlaydi; public browser login uchun uni yoqmang. Existing userlar bo‘lsa bootstrap rad etiladi. Eski user bazasi bor operator workspace/membership mappingni qo‘lda review qilishi kerak.

Dashboardda email/parol bilan kiring, workspace’ni tanlang. Tokenlar browser memory’da, sahifa reload bo‘lsa qayta login kerak. Tokenlarni clipboard/localStoragega ko‘chirish talab qilinmaydi. Taklif tokenini owner faqat tegishli shaxsga xavfsiz kanal bilan yetkazadi.

## Test buyruqlari

Quyidagi buyruqlar kerakli dependencylar mavjud CI/development muhitida bajariladi. Computer’da paket o‘rnatilmadi.

````sh
cd api-python
python -m unittest discover -s runtime_tests -v
ENV=test ALLOW_INSECURE_DEV=true python -m pytest integration_tests -q
cd ..
node --test apps/runner/test.js apps/ui/lib/session-client.test.mjs
python -m unittest discover -s scripts -p 'test_release_tools.py' -v
cd apps/ui
npm ci
npm run typecheck
npm run build
````

CI workflow HTTP va UI gate’larni saqlaydi. Joriy unit testlar o‘tishi sabab HTTP/UI gate skip qilinmasin. CI execution bu suhbatdan ishga tushirilmagan.

## Migratsiya va backup

Oldingi arxiv va bazani saqlang. API/worker’ni to‘xtatib staging snapshot oling yoki faqat snapshot uchun SQLite backup API’dan foydalaning. Quyidagi yo‘llar o‘zingizning environmentga mos bo‘lishi kerak:

````sh
python scripts/sqlite_backup.py /srv/data/app.db /secure-backups/pre-v03.db
python scripts/sqlite_backup.py /secure-backups/pre-v03.db /srv/staging/restored.db
````

Destination mavjud bo‘lsa overwrite rad qilinadi. Artifact permission 0600, lekin u shifrlanmagan; encryption va backup serverga yetkazish operator zimmasida. Restore faol DB ustiga yozilmaydi. Snapshotning checksumini va restore biznes invariantlarini alohida tekshiring.

v0.3 `p_identity_migrations` v1, session family/version ustunlari, audit/rate-limit jadvallari va `p_schedule_owners`ni qo‘shadi. Eski sessionlar bekor qilinadi, eski JWTlar directory mode’da rad etiladi. Foydalanuvchilar qayta login qiladi. Owneri yo‘q eski schedulelar re-authorization talab qiladi. Schema eski kodga qaytish uchun oldin review qilingan backup bilan tiklanadi, auth zaifligini qayta ochish tavsiya etilmaydi.

## PostgreSQL va provider acceptance

`config/customer-sqlite.example.json`dagi PostgreSQL entry live emas. Alohida read-only DB user, server TLS verify-full, schema/table/column allowlist, tenant_column yoki dedicated_database isolationni haqiqiy stagingda sinang. Egress ACL/DNS, system role va RLS audit kerak. MCP/CRM credential uchun oddiy `.env` staging reference production vault o‘rnini bosa olmaydi.

Live yozishlarni production hisobida sinamang. Provider write receipt, idempotency, rate-limit va reconcile dalili bo‘lmaguncha tegishli adapter release qilinmaydi.

## Release gate

````sh
python scripts/check_release.py docs/verification/v03/release-evidence.json
````

Hozir kutiladigan natija `NO_GO`, exit code 1. Bu test xatosi emas, bajarilmagan production mezonlarini yashirmaydigan tekshiruv. Manifest evidence mavjudligini tekshiradi, hujjatdagi da’volarning haqqoniyligini avtomatik isbotlamaydi. Yakuniy release uchun mustaqil review va staging dalili zarur.

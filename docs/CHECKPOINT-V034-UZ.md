# v0.3.4 oraliq nusxa: tasdiqli SQL/NoSQL operatsiyalari

**OFFLINE PREVIEW / PRODUCTION NO-GO. Loyiha davom etmoqda.** Asl Google Drive ZIP nusxasi o‘zgartirilmadi. v0.3.3 manbalari asosida alohida ishchi nusxada kod davom ettirildi. Foydalanuvchining avtonom dasturlash talabi mijoz DB’sida oldin tanlangan tasdiqli yozish chegarasini bekor qilish deb talqin qilinmadi.

## Qo‘shilgan kod

`platform_runtime/database/` ichida yagona operatsiya shartnomasi, capability katalogi, olti SQL dialekt kompilyatori, SQLite/PostgreSQL/MySQL/MariaDB/MongoDB/Redis transport manbalari va Engine gateway yaratildi. SQL Server va Oracle faqat kompilyator bosqichida. Qolgan katalog backendlarining ijrosi rad etiladi.

Engine’ga konfiguratsiyaga bog‘langan step fingerprint va bir martalik DB dispatch jurnali qo‘shildi. Mavjud tasdiq muddati, mustaqil approver, actor/tenant/agent scope, freeze, lease, audit va no-retry siyosati saqlandi. Eski read-only connectorlar yozishga o‘tkazilmadi. API katalog versiyasi `0.3.4-managed-database-preview`.

Barcha connectionlari o‘chirilgan konfiguratsiya namunalari, alohida agent pack namunasi, optional dependency deklaratsiyasi va real vaqtinchalik SQLite’da plan, tasdiq, UPDATE, o‘qish ketma-ketligini bajaradigan demo qo‘shildi. [To‘liq DB shartnomasi va chegaralari](MANAGED-DATABASES-UZ.md) alohida hujjatda.

## Test dalillari

**405 test PASS:** Python runtime 379 (shundan 86 yangi DB testi), release yordamchilari 3, Node runner 15, browser session client 8. Yangi 86 test umumiy 405 ichida, alohida ustiga qo‘shib sanalmaydi. Ikki SQLite demo ham PASS. Python sintaksisi 114 fayl, JS/TS/TSX sintaksisi 14 fayl tekshirildi. JS/TS sintaksisi typecheck yoki production build degani emas.

Aniq so‘nggi raqamlar `docs/verification/v034/summary.json` va shu katalogdagi haqiqiy loglarda saqlanadi. v0.3.3 boshlang‘ich nusxasidagi 319 test ushbu Computer’da qayta o‘tdi. Yangi to‘plam SQL/NoSQL kontrakti, parametrli so‘rovlar, tenant chegarasi, tasdiq, replay, parallel worker CAS, lifecycle revoke, xatodagi rollback/no-retry hamda driver yopilishini qamrab oladi.

SQLite testlari real vaqtinchalik SQLite fayllarida bajarildi. PostgreSQL, MySQL, MariaDB, MongoDB va Redis testlarida tarmoq drayverlari fake obyektlar bilan almashtirildi. Redis Lua skripti haqiqiy Redis’da, SQL dialektlari esa haqiqiy tarmoq DB serverlarida bajarilmadi. Fake testning o‘tishi TLS, server versiyasi, native driver compatibility yoki production to‘g‘riligini tasdiqlamaydi.

HTTP/FastAPI testlari, React typecheck, Next production build, dependency audit va haqiqiy CRM/LLM/tarmoq DB acceptance bajarilmadi. Paketlar o‘rnatilmadi, tashqi mijoz bazasiga yozilmadi va deployment amalga oshirilmadi. Offline runner tarmoq chaqiruvlarini bloklaydi, subprocesslarga haqiqiy muhit secretlarini uzatmaydi.

## Ochiq to‘siqlar

| Holat | To‘siq |
| --- | --- |
| BLOCKED | Bu Computer’da FastAPI/pytest/httpx/pydantic hamda tarmoq DB driverlari yo‘q; paket o‘rnatish mavjud emas |
| OPEN | Barcha tarmoq adapterlari uchun haqiqiy TLS/server/role va fault acceptance kerak |
| OPEN | SQL Server va Oracle transportlari, qolgan NoSQL adapterlari hali yozilmagan |
| OPEN | Receipt bilan provider holatini avtomatik reconcile qilish yo‘q; distributed commit yoki exactly-once da’vosi yo‘q |
| OPEN | Native endpoint host allowlist’i DNS rebinding va tarmoq izolatsiyasi o‘rnini egallamaydi; deployment network policy kerak |
| OPEN | All-worker durable revocation fence, in-flight cancellation va DDL/config o‘zgarishlari bo‘yicha live sinovlar kerak |
| OPEN | Umumiy qism flat scalar operatsiyalar bilan cheklangan; nested document, JOIN, graph/search, bulk va DELETE yo‘q |
| OPEN | CRM/ERP provider OAuth, rasmiy API write receipt/sync/reconcile hali alohida ish |
| OPEN | Oldingi Next.js dependency xavfi, identity/vault, billing, spend ledger, retention va production load/fault to‘siqlari saqlanadi |

Ruxsat berilgan jadvalning o‘zi ham zararli default/check funksiyalar yoki tashqi biznes qoidalariga ega bo‘lishi mumkin. Bu qatlam arbitrary raw so‘rovlarni bermaydi, lekin mijoz sxemasining to‘liq biznes auditi o‘rnini bosmaydi. DB schema/DDL huquqlari agent rolida bo‘lmasligi kerak. Ishlab chiqarish muhiti uchun ruxsat etilgan resurslar alohida tekshiriladi.

## Davom ettirish nuqtasi

Birinchi tayanch qatlam bor, lekin barcha DB’lar bilan tayyor ishlaydigan mahsulot hali yo‘q. Navbatdagi kod ishlari SQL Server/Oracle transportlari va qolgan NoSQL oilalari. Bunga parallel ravishda foydalanuvchining staging/CI muhitida haqiqiy adapter tekshiruvlari rejalashtiriladi. Asl v0.3.3 va uning tarixiy dalillari saqlanadi.

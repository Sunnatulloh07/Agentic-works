# v0.3.5 oraliq nusxa: SQL Server va Oracle transportlari

**486 offline test PASS. OFFLINE PREVIEW / PRODUCTION NO-GO. Loyiha davom etmoqda.**

## Ushbu bosqich

Foydalanuvchi Google Drive’dagi v0.3.4 ZIP’dan davom ettirishni va keyingi qadam sifatida kod yozishni tanladi. Asl arxiv o‘zgartirilmadi. Boshlang‘ich 405 offline test ushbu Computer’da qayta o‘tdi. SQL Server va Oracle transportlari, qat’iy endpoint/schema nazorati, disabled konfiguratsiya namunalari hamda native dependency deklaratsiyalari qo‘shildi. API katalog versiyasi `0.3.5-enterprise-sql-preview`.

Transportlar mavjud gateway, konfiguratsiyaga bog‘langan fingerprint, mustaqil approval, tenant/key/version nazorati va bir martalik dispatch orqali ishlaydi. Provider yoki commit xatosida avtomatik qayta urinilmaydi. Driver xato tafsilotlari yangi transportlar javobida yashiriladi. Native serverlarda ishlashi tasdiqlanmagan.

## Haqiqiy bajarilgan tekshiruvlar

| To‘plam | Natija |
| --- | --- |
| Python runtime | 454 PASS |
| Release helper | 3 PASS |
| Manifest checker helper | 6 PASS |
| Node runner | 15 PASS |
| Browser session client | 8 PASS |
| Jami | 486 PASS |
| SQLite demo | 2 demo PASS, yuqoridagi test soniga qo‘shilmagan |
| Python syntax | 120 fayl PASS |
| JS/TS/TSX syntax | 14 fayl PASS; typecheck/build emas |

v0.3.4 ustiga 75 ta enterprise kontrakt/authority testi va 6 ta manifest helper testi qo‘shildi. Ular jami 486 ichida. Driver testlari fake; faqat mahalliy SQLite sinovlari real vaqtinchalik bazalarda bajarildi. Loglar va aniq buyruqlar `docs/verification/v035/` da.

## Asl arxiv nazorat summalari

Asl `MANIFEST.sha256` 245 entry ko‘rsatadi. 8 faylning hash’i mos kelmadi va manifestda ko‘rsatilmagan 36 fayl bor. Qo‘shimcha tekshiruvda arxiv ichidagi `docs/verification/v034/manifest-v034.sha256` boshqa barcha 281 faylga to‘liq mos keldi. Demak, root manifest arxivning joriy holati bilan moslashtirilmagan; ichki versiyalangan manifest esa mos. Tarixiy sabab va publisher autentikligi mustaqil tasdiqlanmagan. Asl root manifest, kutilgan/haqiqiy hashlar va ichki manifest tekshiruvi `docs/verification/source-v034/` da saqlandi.

Yangi checkpoint uchun barcha release fayllarini qamrab oladigan yangi `MANIFEST.sha256` tuzildi. Bu yangi nusxaning yaxlitligini tekshirish uchun, original publisher imzosi yoki tarixiy nusxaning autentikligi isboti emas. `python scripts/verify_manifest.py` tekshiruv vositasi qo‘shildi. CI unga va uning unit testlariga moslandi; CI’ning o‘zi bu Computer’dan ishga tushirilmadi.

## Muhim cheklovlar

SQL Server/Oracle schema va type cheklovlari [ENTERPRISE-SQL-PREVIEW-UZ.md](ENTERPRISE-SQL-PREVIEW-UZ.md) da. Oracle bo‘sh satr va boolean payloadini rad etadi. SQL Server matnli identity uchun nonnullable nvarchar BIN2 va version uchun bigint talab qiladi. Oracle quoted nomlarda case saqlanadi, version uchun NUMBER(16..38,0) ishlatiladi. Barcha namunaviy connectionlar o‘chirilgan.

Haqiqiy native driver/TLS/least-privilege, parallel DDL, lock/collation, connection loss va commit-ack sinovlari ochiq. Paketlar o‘rnatilmadi, mijoz bazasiga ulanilmadi va deployment qilinmadi. FastAPI/pytest/httpx/pydantic va network DB dependencylari bu muhitda yo‘q. HTTP tests, React typecheck, Next production build, dependency audit va live acceptance bajarilmadi.

Default/check funksiyalar, indexed views, server/session triggerlari, RLS/VPD, credential rotation va tarmoq routing/redirect oqibatlari to‘liq audit qilinmagan. DNS va network policy, durable revocation, avtomatik reconcile, identity/vault, billing va load/fault to‘siqlari saqlanadi.

## Davom ettirish nuqtasi

DynamoDB, Cassandra, Neo4j va Elasticsearch katalogda qolmoqda, ularning transportlari hali yo‘q. Keyingi adapterni foydalanuvchi bilan tanlash kerak. Haqiqiy serverlar uchun Docker/CI/VPS yo‘nalishi hali tanlanmagan. Bu loyiha yakuni emas.

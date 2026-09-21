# SQL Server va Oracle adapterlari, v0.3.5 oraliq nusxa

**OFFLINE PREVIEW. Haqiqiy server acceptance bajarilmagan. PRODUCTION NO-GO.**

Bu bosqich SQL Server va Oracle uchun parametrli kompilyator yoniga ijro transportini qo‘shadi. `read`, `insert` va versiyali `update` yagona 1.1 shartnomasi, mavjud approval, tenant binding va bir martalik dispatch orqali ishlaydi. Yangi transportlar `network_transport_unverified`; `live_verified=false` saqlanadi. Boshqa NoSQL adapterlari avtomatik tayyor bo‘lib qolmaydi.

## SQL Server chegaralari

Manba kodi SQL Server 2019+ va Microsoft ODBC Driver 18 uchun yozildi; bu versiyalarda live tekshiruv hali yo‘q. Python `pyodbc` hamda OS darajasidagi ODBC driver kerak. `Encrypt=yes`, `TrustServerCertificate=no`, `ConnectRetryCount=0`, login timeout 5 soniya, query timeout 2 soniya va lock timeout 1 soniya qat’iy beriladi. Arbitrary DSN/driver berilmaydi; connection stringdagi parol qiymati ODBC qavs qoidasi bilan escape qilinadi va logga chiqarilmaydi.

Session pooling modulning birinchi connectionidan oldin o‘chirilishi kerak. Adapter buni o‘rnatadi, ammo process avval boshqa pyodbc connection ochgan bo‘lsa oldingi initialization qaytarilgan deb da’vo qilinmaydi. Alohida worker va OS ODBC konfiguratsiyasi live qabulda tekshiriladi.

Oddiy jadval, object bo‘yicha `VIEW DEFINITION` va configured maydonlarning to‘liq metadatasi talab qilinadi. Temporal, memory-optimized, FileTable va graph jadvallari rad etiladi. Configured computed, identity, generated va user-defined type ustunlari rad etiladi. Qo‘llab-quvvatlangan scalar ustunlar: cheklangan `nvarchar`/`varchar`, `bigint`, `int`, `smallint`, `tinyint`, `bit`. Text ustun uzunligi 1..8000 byte; `MAX`, LOB va boshqa turlar qabul qilinmaydi.

Tenant va matnli record key uchun NOT NULL `nvarchar` va `_BIN2` bilan tugaydigan binary collation kerak; case-insensitive tenant comparison qabul qilinmaydi. Numeric key turi so‘rovdagi integer key bilan mos kelishi kerak. Version ustuni NOT NULL `bigint`; SQL Server native `rowversion` turi bu shartnoma uchun mos emas.

Yozishdan oldin konservativ `TABLOCKX,HOLDLOCK` olinadi va metadata qayta tekshiriladi. Bu jadvalga parallel yozish tezligini kamaytiradi; load/DDL sinovi bajarilmagan. Enabled triggerlar, ko‘rinadigan inbound/outbound foreign keylar va haqiqiy unique key yo‘qligi rad etiladi. Filtered, disabled va hypothetical indekslar uniqueness dalili sifatida olinmaydi; INCLUDE ustunlar unique key tarkibiga qo‘shilmaydi.

SQL Server’da `SET TRANSACTION READ ONLY` ekvivalenti yo‘q. `read_only=true` natija operatsiyaning o‘qish ekanini bildiradi, DB loginining yozish huquqi yo‘qligini emas. Alohida read-only login kerak bo‘lsa administrator alohida connection provision qiladi. `ApplicationIntent=ReadOnly` xavfsizlik chegarasi sifatida ishlatilmaydi.

## Oracle chegaralari

Manba kodi Oracle 19c+ va `python-oracledb` Thin uchun yozildi; live tekshiruv yo‘q. Thick mode va Oracle Client initialization bu preview’ga kirmaydi. Explicit host, port, `service_name`, `protocol=tcps`, `ssl_server_dn_match=True`, `retry_count=0` va 5 soniyali TCP connection timeout ishlatiladi. CA ishonchi tizim trust store orqali; sertifikatni tekshirishni o‘chiradigan yo‘l berilmaydi. Custom wallet/mTLS, RAC/SCAN redirect va boshqa tarmoq topologiyalari qo‘llab-quvvatlangan deb hisoblanmaydi.

`call_timeout=2000` bitta round trip uchun, butun operatsiya uchun qat’iy 2 soniyali deadline emas. Oracle listener boshqa endpointga yo‘naltirishi mumkin. Host allowlist va retry_count=0 tarmoq egress policy yoki redirect bloklash kafolati emas.

Schema, jadval va ustun nomlari aynan konfiguratsiyadagi case bilan quote qilinadi. Odatdagi unquoted Oracle sxemalari uppercase bo‘ladi; misolda `SALES`, `CUSTOMERS`, `WORKSPACE_ID` ishlatilgan. `database` konfiguratsiya yorlig‘i bo‘lib qoladi; haqiqiy Oracle manzili explicit `service_name` orqali tanlanadi. Administrator ikkalasini tenant binding bilan mos provision qilishi kerak.

Oddiy nonpartitioned heap jadval talab qilinadi; temporary, nested, IOT, secondary, external, materialized-view va object table rad etiladi. Configured virtual, hidden, identity va LOB ustunlar yo‘q. Cheklangan `VARCHAR2`/`NVARCHAR2` (1..4000 byte) va scale=0 `NUMBER` mavjud. Version NOT NULL `NUMBER(16..38,0)`; matnli tenant/key NOT NULL hamda `BINARY` yoki `USING_NLS_COMP` collation bo‘lishi kerak. Session’da `NLS_COMP=BINARY`, `NLS_SORT=BINARY` o‘rnatiladi.

Oracle bo‘sh satrni NULL sifatida talqin qiladi, oldingi SQL versiyalarda BOOLEAN ustuni esa umumiy emas. Shu sabab bu preview bo‘sh satr va boolean payloadlarini reja bosqichidayoq rad etadi. `null` alohida null sifatida qoladi. Bu cheklovlarni universal scalar moslik sifatida yashirmaslik kerak.

O‘qish `SET TRANSACTION READ ONLY` bilan, yozish `LOCK TABLE ... IN ROW EXCLUSIVE MODE NOWAIT` bilan ishlaydi. Yozishda enabled trigger, resursning o‘z foreign key’i yoki enabled/validated/nondeferrable unique/primary constraint yo‘qligi rad etiladi. Oddiy unique indexning o‘zi ushbu preview’da yetarli emas.

## Xatolar, ruxsatlar va ochiq xavflar

Har ikkala adapter parametrli SQL, tenant/key/version predikatlari, aniq bitta affected row, commit yoki rollback va cursor/connection cleanup ishlatadi. Connection, provider va cleanup xatolari javobda driver tafsilotlarini chiqarmaydi. Commit javobi yo‘qolishi, cursor cleanup xatosi yoki platform receipt’iga yetmaslik muvaffaqiyat sifatida yashirilmaydi. Engine natijani ehtiyotkorlik bilan `uncertain` deb belgilaydi; avtomatik retry yo‘q.

Metadata tekshiruvi to‘liq biznes/sxema xavfsizlik auditi emas. Default/check funksiyalar, indexed-view ta’siri, server/session triggerlari, RLS/VPD, yashirin permission semantikasi va concurrent DDL alohida audit talab qiladi. SQL Server metadata visibility huquqi ham barcha cross-resource ta’sirlarni to‘liq ko‘rsatishini live tekshiruvsiz aytib bo‘lmaydi. Administrator ruxsatli sxemani tekshirishi, runtime loginidan DDL/admin huquqlarini olib tashlashi va tarmoqni izolyatsiyalashi kerak. Exactly-once yoki production tayyorligi da’vosi yo‘q.

## Sinov dalili va qayta bajarish

Yangi testlar fake DB-API obyektlarida ishlaydi. Ular SQL matni va bindings, metadata fail-closed, TLS parametrlarini driverga uzatish, rowcount conflict, rollback/close, no-retry, approval/replay va Engine `uncertain` yo‘lini tekshiradi. Native driver, haqiqiy TLS handshake, SQL katalog so‘rovlarining real serverdagi ishlashi, transaction/lock/collation semantikasi, haqiqiy fault injection va least-privilege huquqlar tekshirilmagan.

````bash
python scripts/verify_manifest.py
python scripts/verify_offline.py
````

Barcha connection namunalari `enabled=false`. `requirements-databases.txt` compatibility diapazonlari, audit qilingan lock emas. Ushbu Computer’da yangi paketlar o‘rnatilmadi. Parol/kalitlarni chatga yoki JSON’ga yozmang; native DB credentiali uchun deployment secret tizimi kerak. HTTPS-only `DSEC_*` placeholderlar native DB paroli sifatida rad etiladi.

## Implementatsiya uchun foydalanilgan rasmiy manbalar

- [Microsoft ODBC connection keywords, TLS va retry sozlamalari](https://learn.microsoft.com/en-us/sql/connect/odbc/dsn-connection-string-attribute?view=sql-server-ver17)
- [Microsoft ODBC connection resiliency](https://learn.microsoft.com/en-us/sql/connect/odbc/connection-resiliency?view=sql-server-ver17)
- [python-oracledb ConnectParams](https://python-oracledb.readthedocs.io/en/stable/api_manual/connect_params.html)
- [python-oracledb Connection, call_timeout va transaction lifecycle](https://python-oracledb.readthedocs.io/en/stable/api_manual/connection.html)
- [Oracle 19c ALL_TRIGGERS metadata visibility](https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/ALL_TRIGGERS.html)

Manbalar API tanlashga yordam berdi; ular ushbu loyihaning live sinov dalili emas.

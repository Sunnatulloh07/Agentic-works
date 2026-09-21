# SQL va NoSQL uchun yagona DB qatlami, v0.3.5 oraliq nusxa

Bu loyiha yakuni yoki barcha bazalar tayyor degan e’lon emas. Maqsad SQL, hujjat va kalit-qiymat bazalarini bitta qat’iy operatsiya shartnomasi orqali ketma-ket ulash. Backendning haqiqiy semantikasi saqlanadi, ishlamaydigan adapter ishlayotgandek ko‘rsatilmaydi.

## Joriy moslik

| Backend | Hozirgi amalga oshirish | Tekshiruv darajasi |
| --- | --- | --- |
| SQLite | O‘qish, INSERT, versiyali UPDATE | Haqiqiy vaqtinchalik SQLite bazasi, ikki tenant, parallel ishchilar |
| PostgreSQL | TLS transporti, cheklangan SELECT/INSERT/UPDATE | Fake driver kontrakti; haqiqiy server tekshirilmagan |
| MySQL | InnoDB, TLS transporti, SELECT/INSERT/UPDATE | Fake driver kontrakti; haqiqiy server tekshirilmagan |
| MariaDB | InnoDB, TLS transporti, SELECT/INSERT/UPDATE | Fake driver kontrakti; haqiqiy server tekshirilmagan |
| MongoDB | Proyeksiyali o‘qish, insert_one, versiyali update_one | Fake driver kontrakti; haqiqiy server tekshirilmagan |
| Redis | Aniq kalitli hash o‘qish, qat’iy Lua orqali atomar INSERT/CAS | Fake driver kontrakti; Lua haqiqiy Redis’da bajarilmagan |
| SQL Server | ODBC 18 orqali cheklangan SELECT/INSERT/UPDATE transporti | Fake driver kontrakti; haqiqiy server tekshirilmagan |
| Oracle | Thin TCPS orqali cheklangan SELECT/INSERT/UPDATE transporti | Fake driver kontrakti; haqiqiy server tekshirilmagan |
| DynamoDB, Cassandra, Neo4j, Elasticsearch | Adapter katalogi va holat modeli | Adapter hali yo‘q, ijro rad etiladi |

Ro‘yxat dunyodagi barcha DB mahsulotlari ro‘yxati emas. Yangi backend server kodidagi registr va transport orqali qo‘shiladi. Model modul, driver, URL, DSN yoki raw SQL/NoSQL buyruqlarini yuklay olmaydi. `database.catalog` natijasi `universal_native_support=false` va `live_verified=false` qiymatlarini saqlaydi.

SQL Server/Oracle schema, scalar va transaction cheklovlari [enterprise preview hujjati](ENTERPRISE-SQL-PREVIEW-UZ.md) da. Oracle bo‘sh satr va boolean payloadini rad etadi; umumiy scalar ro‘yxati har backend uchun bir xil moslik kafolati emas.

## Bitta operatsiya shartnomasi

`database.read`, `database.plan_write`, `database.write` agent vositalari mavjud Engine/AgentLoop orqali ishlaydi. Yangi shartnoma `1.1`, eski read-only connectorlar esa `1.0` holida saqlanadi. Mavjud read-only connectionlar avtomatik yozish vakolatiga ega bo‘lmaydi.

Argumentdagi `connection` oldindan berilgan connection ID hisoblanadi. `request_json` ichida faqat operatsiya, resurs, maydonlar, aniq kalit va kerak bo‘lganda kutilgan versiya bo‘ladi. Model tenant, credential, scope yoki SQL bera olmaydi.

````json
{
  "connection": "customer-postgres",
  "request_json": "{\"operation\":\"update\",\"resource\":\"customers\",\"key\":\"customer-001\",\"expected_version\":1,\"values\":{\"status\":\"contacted\"}}"
}
````

Bu argument avval `database.plan_write` ga beriladi. Natijadagi `plan_fingerprint` yuqoridagi argumentga qo‘shilib, `database.write` taski yaratiladi. Amaldagi approval API orqali tegishli owner tasdiqlagandan keyingina Engine uni ijro etadi. Misoldagi agent siyosati mustaqil tasdiqlovchini talab qiladi.

`read` uchun `fields` va 1..100 oraliqda `limit` ishlatiladi. O‘qishda kalit ixtiyoriy, lekin Redis doim aniq `key` talab qiladi va SCAN qilmaydi. `insert` uchun `key` hamda `values`, `update` uchun qo‘shimcha musbat `expected_version` kerak. Bir yozish operatsiyasi ko‘pi bilan bitta mantiqiy yozuvga mo‘ljallangan.

Joriy ko‘chma qiymatlar: chegaralangan satr, aniq butun son, boolean va null. Pul/decimal qiymatlari aniqlikni yo‘qotmaslik uchun satr bilan uzatiladi. Ichma-ich hujjatlar, BSON ObjectId, binary yozish, erkin filter/operatorlar, JOIN, aggregate, bulk, DELETE, upsert va DDL bu shartnomaga kirmaydi.

## Ruxsat va yaxlitlik

Connection uchun agent bog‘lanishi, capabilities, lifecycle, generation, jadval/collection va maydonlar ro‘yxati majburiy. Tenant ustuni server tomonidan qo‘shiladi; alohida DB rejimi bo‘lsa `bound_tenant` aniq mos kelishi kerak. Kalit, tenant va versiya maydonlarini model o‘zgartira olmaydi.

Plan hash tenant, agent, connection, normalizatsiyalangan operatsiya va provision qilingan konfiguratsiyaga bog‘lanadi. Konfiguratsiya yoki payload o‘zgarsa qayta plan va qayta tasdiq talab qilinadi. Task qabul qilishda, claim paytida va yozish handlerida ruxsatlar takroran tekshiriladi. `ladder: autonomous` yozuv tasdig‘ini o‘chirib qo‘ymaydi.

SQL adapterlari UPDATE uchun kalit va versiya shartini qo‘llaydi, DB darajasidagi unique key’ni talab qiladi va muvaffaqiyatsiz tranzaksiyani rollback qiladi. SQLite, PostgreSQL hamda MySQL/MariaDB’da joriy boshqariladigan yozuvlar triggerli jadvallarni rad etadi. SQLite foreign key’li, MySQL/MariaDB esa InnoDB bo‘lmagan resurslarga yozmaydi. PostgreSQL’da faqat oddiy jadval qo‘llanadi, partition/view yo‘q.

MongoDB selectorida tenant, kalit va versiya uchun scalar type hamda literal equality qo‘shimcha tekshiruvi bor. Bu MongoDB oddiy equality’sining array elementlariga ham mos tushish semantikasini cheklaydi. Redis kaliti namespace, resurs, tenant hash’i va kalit hash’idan tuziladi, integer `1` va string `"1"` farqlanadi.

## Audit va noma’lum natija

`p_database_dispatch` bir Engine step’ining providerga ikkinchi marta yuborilishini rad etadi. Provider chaqiruvidan oldin `dispatching`, javobdan so‘ng `committed` yoki xatoda `uncertain` qayd etiladi. Maxfiy DB maydon qiymatlari yangi DB audit hodisalariga ko‘chirilmaydi. Tasdiq uchun zarur task argumentlari va o‘qish natijalari mavjud platform saqlash siyosatiga bo‘ysunadi; to‘liq redaction/retention hali alohida ish.

Bu global exactly-once kafolati emas. Mijoz DB commit’i va platform receipt’i ikki tizimda, ular orasida crash yuz berishi mumkin. Bir xil amal boshqa task bilan qayta tasdiqlansa bu yangi dispatch hisoblanadi; unique key va versiya qo‘shimcha himoya beradi. Avtomatik retry yo‘q. `dispatching` holatida qolgan yoki `uncertain` yozuvni tashqi DB dalillari bilan reconcile qilish mexanizmi keyingi bosqichda; joriy holatda operator tekshiruvi kerak. Hatto oldindan aniqlangan ayrim xatolar ham mavjud Engine siyosati sabab ehtiyotkorlik bilan `uncertain` bo‘ladi.

## Yoqish va takrorlash

`config/customer-managed.example.json` dagi barcha connectionlar ataylab `enabled=false`. Mavjud mijoz konfiguratsiyasi yoki pack avtomatik almashtirilmaydi. `config/managed-agent.example.yaml` namunasini mijoz pack’iga moslashtirish, ruxsatli mount, haqiqiy sxema, unique key, versiya ustuni, eng kam huquqli DB user va TLS’ni administrator tayyorlaydi. Birinchi tekshiruvlar production emas, alohida staging bazalarida bajariladi.

````bash
python scripts/demo_managed_database.py
python scripts/verify_offline.py
````

Optional tarmoq dependency deklaratsiyasi `api-python/requirements-databases.txt` da. Bu audit qilingan lock emas, paketlar bu Computer’da o‘rnatilmadi. Native DB protokollariga HTTPS-only `DSEC_*` placeholder uzatish rad etiladi. Haqiqiy credentiallar chatga yoki konfiguratsiya fayliga yozilmaydi, tegishli deployment secret tizimida beriladi.

## Keyingi integratsiyalar

SQL Server va Oracle ijro transportlari manba darajasida qo‘shildi, live acceptance ochiq. Keyingi DynamoDB/Cassandra hamda graph/search adapterlari o‘z semantikasiga mos yoziladi. Keyin barcha tarmoq adapterlari haqiqiy TLS, least-privilege role, ikki tenant, timeout, DDL/role o‘zgarishi, uzilgan aloqa va commit-ack uzilishi bilan tekshirilishi kerak.

Bitrix24, Kommo/amoCRM va 1C talabda qoladi. Umumiy DB adapteri ularning rasmiy API integratsiyasi o‘rnini egallamaydi. Vendor DB’siga to‘g‘ridan-to‘g‘ri yozish biznes qoidalarini buzishi mumkin; har bir mijoz uchun ruxsat etilgan sxema va amallar alohida kelishiladi.

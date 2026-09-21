# v0.3.6 development checkpoint

Bu **oraliq source checkpoint**, to‘liq PRD yoki 100% tayyor mahsulot emas. Mahalliy testlar development davomida bajarildi. Haqiqiy provider, DB, Mac/Windows va deployment acceptance keyinga qoldirilgan.

## Yozilgan va tizimga ulangan qismlar

### To‘rtta yangi managed DB transporti

`dynamodb_backend.py`: explicit AWS account/region, tenant HASH + record RANGE, table ARN/schema tekshiruvi, strongly-consistent exact-key GetItem, conditional PutItem/UpdateItem, cheklangan scalar serializatsiyasi. Global tables, scan/query, xom PartiQL, credential auto-discovery va write retry yo‘q.

`cassandra_backend.py`: explicit TLS host va keyspace, tenant partition + record clustering, oddiy scalar columnlar, IF NOT EXISTS va versiyali LWT, QUORUM/SERIAL. Counter, static column, TTL, filtering va avtomatik retry rad etiladi.

`neo4j_backend.py`: to‘g‘ridan-to‘g‘ri bolt+s, unique constraint tekshiruvi, oddiy property-node CRUDning read/insert/update qismi, parametrli Cypher va explicit transaction. Update expected-version tekshiruvidan oldin property read/write dependency orqali write lock talab qiladi. Native concurrency va plugin/trigger xatti-harakati hali alohida acceptance talab qiladi.

`elasticsearch_backend.py`: tenant/resource/key asosida hash ID, aniq concrete index, mapping va pipeline siyosati, create-only insert, `_seq_no`/`_primary_term` CAS update va exact get. Script, search DSL, bulk, automatic index yaratish, upsert va ingest pipeline yo‘q. Receipt va shard xatolari tekshiriladi.

Barcha to‘rttasi gateway/catalog, plan fingerprint, mustaqil approval va durable dispatch/replay himoyasiga ulangan. DB katalogidagi 12 backend uchun cheklangan transport source’i mavjud. Bu barcha DB imkoniyatlari yoki live tasdiq degani emas. `config/nosql-managed.example.json` misollari default o‘chiq.

### Atomic usage budget

`usage_budget.py`: integer microunit ledger, UTC oylik limit, parallel chaqiruv limiti, rezerv, bir martalik dispatch, settlement, uncertain va owner reconciliation. SQLite transactionlari bir paytdagi rezervlar limitdan oshishining oldini oladi. Eski oy rezervlari parallel limitdan yashirilmaydi. Policy pasaytirilsa oldingi rezerv dispatchda yana tekshiriladi.

Noaniq natija rezervni ushlab qoladi, avtomatik refund yoki re-dispatch yo‘q. Provider real sarfi rezervdan ko‘p bo‘lsa ham kesib tashlanmaydi, to‘liq hisobga olinadi. Admin dalil bilan reconcile qiladi. Bu subscription, invoice, Click/Payme/Stripe yoki narxni avtomatik aniqlash moduli emas.

Literal Planner va ResultPlanner konfiguratsiyada `usage_budget` mavjud bo‘lsa ledger’dan foydalanadi. `usage_budget_required: true` majburiy konfiguratsiyani talab qiladi. Eski konfiguratsiyalar uchun opt-in o‘chirilmagan: flag/rates sozlanmagan cloud callerlar eski rejimda qoladi. Barcha tool, voice va tarmoq DB to‘lovlari hali umumiy meterga ulanmagan.

### Bilim bazasi

`knowledge.py`: text ingestion, 512-belgilik/64-overlap chunklar, immutable manba IDlari, expected-version CAS, tenant va agent collection ACL, delete tombstone, lokal BM25 va optional oldindan hisoblangan pinned-model vectorlari uchun cosine/RRF. Bo‘lakdagi original matn/offsetlar qaytariladi. Uzbek apostroflari qidiruvda normallashtiriladi.

`knowledge.search` registry’ga ulangan. Operator agent pack’iga tool ruxsatini, owner esa collection ACL’ni alohida beradi. HTTP source endpointlari create/grant/ingest/list/delete/search uchun mavjud. UI matn ingestion va BM25 qidiruvini ishlatadi. Hybrid vectors service/API orqali qabul qilinadi, ammo bu snapshot avtomatik embedding providerini chaqirmaydi.

Binary PDF/DOCX/OCR ingestion, crawler, katta tashqi vector DB, query rewriting va semantik fakt tekshiruvi hali qilinmagan. Bir collection 4000 bo‘lak, matn hujjat 100000 belgi, query natijasi 1–5 bo‘lak bilan cheklangan. Matn instruction emas, untrusted content sifatida belgilangan.

### Runner va Mac uchun source

Linux read-only runner’da FIFO orqali kutib qolish, cheksiz file read, katta katalog allocation, hard-link, noto‘g‘ri UTF-8, jurnal symlink/perms va task-envelope tekshiruvlari kuchaytirildi. Tokenni private 0600 file’dan olish va qayta ulanishda qayta o‘qish qo‘shildi. Token env va token file birga berilsa rad etiladi.

`apps/runner/portable_fs.py`: Linux descriptor yo‘li hamda macOS `fcntl(F_GETPATH)` orqali pinned root, descriptor/inode va read limit tekshiruvi. Linux’da haqiqiy vaqtinchalik fayllar bilan ishladi; Darwin syscall faqat mock kontrakt bilan tekshirildi.

`scripts/prepare_runner_macos.py`: yangi papkaga app/config/state va token qiymatisiz user LaunchAgent plist tayyorlaydi. Hech narsa install/start qilmaydi, runtime yuklamaydi. Node 22.4+ va Python 3.10+ foydalanuvchi tomonidan o‘rnatilishi kerak. Bu `.pkg`/`.dmg`, signed tray app, notarization, Windows service, auto-update yoki full desktop automation emas.

### Lokal model

`model_transport.py`: `provider_mode=local_loopback` explicit bo‘lgandagina `http://127.0.0.1:PORT` yoki IPv6 `::1` modelga kalitsiz murojaat source’i. DNS localhost, boshqa host, privileged port, URL credentials va redirect rad etiladi. Cloud default HTTPS va API credential talabini saqlaydi.

`config/local-llm.example.json` Ollama/LM Studio’ning OpenAI-compatible serveriga moslashtirish uchun misol. O‘rnatilgan model nomini operator kiritadi. Zero token rate faqat lokal inference provider billingini hisoblamaslik misoli, Mac elektr/compute/support xarajati nol degani emas. Model inference, tool-call sifati va Uzbek javoblar bu muhitda tekshirilmagan. Google NotebookLM bu model serveri emas. Loopback URL API/worker ishlayotgan mashinani anglatadi: core cloud’da bo‘lsa bu cloud serverning o‘zi, mijoz Mac’i emas. Runner o‘rnatishning o‘zi Mac modelini cloud’ga avtomatik ochmaydi.

### UI va model-response review

Budjet hamda bilim bazasi panellari control-plane’ga qo‘shildi. Session client explicit PUT/PATCH/DELETEni qo‘llaydi, redirect va write retry’ni rad etadi. Inputlar so‘rov davomida o‘chiriladi, UI permission’lari serverda qayta tekshiriladi.

Ikkala planner shared strict response parserdan foydalanadi: bir choice, finish_reason=stop, duplicate-key/NaN/Infinity rad etilishi, byte, depth va node limit. UI uchun sintaksis tekshiruvi bajarildi; React/Next typecheck, production build va brauzer UX acceptance bajarildi deb ko‘rsatilmaydi.

## Reviewda topilib tuzatilgan muammolar

| Topilma | Tuzatish |
| --- | --- |
| JSON depth cheklovi Python versiyasiga bog‘liq edi | Explicit iterative depth≤32 va nodes≤5000 |
| Literal Planner duplicate JSON key va truncated outputni qabul qilishi mumkin edi | Shared qat’iy parser |
| SDK ValueError tafsilotlari exception orqali chiqishi mumkin edi | Provider call chegarasida sanitizatsiya |
| Budjet limiti pasaygach eski rezerv ishlashi mumkin edi | Dispatchda joriy limit/parallel policy qayta tekshiriladi |
| Oy almashganda eski rezerv parallel limitdan yashirinishi mumkin edi | Barcha oy pendinglari hisoblanadi |
| Delete/recreate hujjat versionini 1ga qaytarishi mumkin edi | Versionli tombstone, stale CAS rad |
| FIFO ochish runnerni bloklashi mumkin edi | O_NONBLOCK va regular-file tekshiruvi |
| Katta fayl o‘qilishi yoki katalog ro‘yxati cheksiz xotira olishi mumkin edi | Bounded read va iterativ bounded directory scan |
| Hard-link, jurnal symlink va ochiq permission holatlari | Single-link/private owner/mode va directory identity |
| Task lease yo‘q yoki NaN bo‘lsa taqqoslashdan o‘tishi mumkin edi | Qat’iy finite lease va envelope validator |
| Elasticsearch update o‘qib bo‘lmaydigan katta hujjatga olib kelishi mumkin edi | To‘liq merged document byte-limit write’dan oldin |

## Dalil darajasi

- Real local: vaqtinchalik SQLite, filesystem, Python/Node unit/integration-as-service testlari.
- Fake contract: AWS/Cassandra/Neo4j/Elasticsearch SDK, model HTTP responses, Darwin F_GETPATH.
- Source/syntax: yangi HTTP routerlar, UI panellari, LaunchAgent konfiguratsiyasi.
- Bajarilmagan: real SDK/cluster/Mac/local model, HTTP dependency-backed testlar, React/Next build/typecheck, vendor CVE audit va deployment.

ECC paketi bu muhitda topilmadi/o‘rnatilmadi; tashqi mustaqil reviewer agent ishlatilmagan. Reviewni shu agent kod va regression dalillari asosida bajardi. Kompyuterga yangi paketlar o‘rnatish ruxsat etilmagan. Bu holatlar yashirilmagan va 100% readiness deb talqin qilinmaydi.

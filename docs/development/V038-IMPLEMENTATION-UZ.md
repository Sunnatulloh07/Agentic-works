# v0.3.8 implementation va review

## Scope va dalil

Bu development Google sync/reconciliation blokini davom ettiradi, butun Agent Platform PRDni yakunlamaydi. Oldingi agent hisobotidagi claimlar avtomatik qabul qilinmadi: ZIP checksum/CRC/path/manifest va 821 original offline test qayta tekshirildi.

## Source arxitekturasi

`platform_runtime/google_sync.py`: Gmail profile -> snapshot -> history; Drive startPageToken -> files snapshot -> changes; Calendar full list -> incremental syncToken. Har `page` chaqiruvi bitta bounded provider sahifasini ishlaydi, Gmaildagi message fan-out readlari shunga kiradi. Stream ID kind+calendar asosida serverda hisoblanadi. User URL yoki cursor bermaydi. Bo‘sh inbox va bo‘sh delta ham ishlaydi. Intermediary pageToken ishlatilayotganda boshlang‘ich historyId/syncToken saqlanadi. Drive tokenlari taqqoslanmaydi. Provider tokeni eskirsa `ResetRequired` beriladi, lokal nusxa avtomatik o‘chirilmaydi.

Record version `revision*1000+index` lokal ketma-ket kuzatish identifikatori. Uni Gmail historyId, Calendar sequence/updated yoki Drive version deb ko‘rsatish xato bo‘ladi. Bitta sahifadagi bir xil ID uchun oxirgi kuzatuv saqlanadi. Provider fieldlar untrusted. Message full body yoki Drive file bytes olinmaydi.

`platform_runtime/google_reconcile.py`: owner musbat provider dalili bilan uncertain write’ni settle qiladi. Asl agent policy, approved fingerprint, OAuth config/resource digest va credential generation qayta tekshiriladi. Providerga faqat GET yuboriladi. Calendar response boshqa event, o‘zgargan body, correlation mismatch yoki yangi attendees bo‘lsa unresolved. Gmail qidiruvda bittadan ko‘p natija/pagination/mos bo‘lmagan MIME yoki SENT bo‘lmasa unresolved. Message-ID exactly-once kafolati emas. SENT orqali send action qabul qilingani tekshiriladi, xat recipient inboxiga yetgani kafolatlanmaydi. No-match yoki 404 remote amal bajarilmadi degani emas.

`sync_store.py`: commit/reset uchun transaction guard, resetni session/config bilan atomik fence qilish va lokal keyset pagination qo‘shildi. Mavjud jadval sxemasi o‘zgarmadi, v0.3.7 migration talab qilinmaydi. Existing migration regressiyasi saqlanadi.

`google_adapters.py`: pinned host va path endi birgalikda tekshiriladi; Gmail history/profile va Drive changes endpointlari GET uchun ishlatiladi. HTTP status sanitized `GoogleHTTPError`ga aylanadi, provider error body/token logga chiqmaydi. Redirect rad qilish saqlandi.

`app/google_data_api.py`: owner session-family, current membership, current configuration va generation bilan himoyalangan `/sync/page`, `/sync/records`, `/sync/reset`, `/google/steps/{step}/reconcile` endpointlari. Reset explicit `confirm_reset: true` talab qiladi; Google’dagi ma’lumotlarni o‘chirmaydi. API yangi query/body orqali arbitrary destination yoki cursor qabul qilmaydi.

`apps/ui/components/GoogleData.tsx`: metadata sahifa ishlash, lokal yozuvlarni ko‘rish, explicit local reset va provider readback UI. Provider matni React text/pre sifatida chiqariladi, HTML bajarilmaydi. Kontekst o‘zgarganda eski response ko‘rsatilmaydi. SessionClient automatic write retry qilmaydi. `google-data-client.mjs` input turlarini va app route allowlistini tekshiradi.

## Reviewda topilgan va tuzatilgan holatlar

| Topilma | Tuzatish va regression |
|---|---|
| OAuth helper bounded() metadata newline’ni rad qilgan | Metadata uchun alohida bounded text validatsiya; multiline Calendar testi |
| Reset/commitdan oldingi session check transactiondan tashqarida qolishi | Transaction guard, role/session/config race testlari |
| Record o‘qish davomida reauthorization generation o‘zgarishi | API read oldi/ketidagi generation compare |
| Calendar extendedProperties noto‘g‘ri tur bo‘lsa parse xatosi | Malformed objectni unresolved qoldirish |
| Gmail labelIds string bo‘lsa substring SENT qabul qilinishi | Faqat bounded array; negative test |
| Calendar keyin qo‘shilgan attendees bilan noto‘g‘ri tasdiqlanishi | Unexpected attendees unresolved |
| JS RegExp.test(undefined) string coercion va trailing newline | Qat’iy type/length/character guard; 4 yangi browser regression |
| Google host/path mustaqil allowlist bo‘lgan | Host-path pairing; no-network negative test |
| Provider 429/404 statusi yo‘qolgan | Sanitized status turi; stream.closed/error redaction regression |
| Browser OAuth/helper yo‘q bo‘lsa verifier exit code qat’iy bo‘lmagan | OAuth va Google data guruhlari required checksga qo‘shildi |

Review o‘z-o‘zini tekshirish va executable regressionlardan iborat. Mustaqil reviewer agenti yoki ECC ishlatilgani da’vo qilinmaydi.

## Ochiq cheklovlar

Katta Gmail history event (>100 changed record), juda katta metadata payload va uzoq fan-out bounded cheklovda cursorni oldinga surmaydi. Silent truncation yo‘q, ammo bunday sahifani davom ettirish uchun staging/subpage source’i qolgan. Cursor cycle’da bir xil pageToken qaytishi aniqlanadi; ixtiyoriy uzun provider token cycle’i uchun tarixiy detection hali yo‘q. Scheduler/backoff/push webhook receiver, shared-drive enumeration, RAG ingestion, permission granularligi va retention quota keyingi bloklar.

HTTP integration testlari source sifatida yozildi, NOT_RUN. React typecheck/build va browser E2E NOT_RUN. Local tests real SQLite/vault va scripted Google transport bilan bajarildi; live Google test emas. CI configuration yozildi, lekin bu sessiyada GitHub CI ishga tushirilmagan.

## Rasmiy protocol manbalari

- https://developers.google.com/workspace/gmail/api/guides/sync
- https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list
- https://developers.google.com/workspace/drive/api/guides/manage-changes
- https://developers.google.com/workspace/calendar/api/guides/sync

Bu hujjatlar pagination/cursor semantikasini aniqlash uchun o‘qildi. Live API acceptance o‘rnini bosmaydi.

# Agent Platform v0.3.3: connector vakolatlari va offline tekshiruv

**OFFLINE VERIFIED PREVIEW / PRODUCTION NO-GO.** Foydalanuvchi sanasi: 2026-09-14, GMT+5. Loglardagi vaqt UTC formatida bo‘lishi mumkin. Bu loyiha yakuni emas, davom etayotgan ishning saqlangan checkpointidir.

Foydalanuvchi ushbu sessiyada test, audit va kod review’ga ruxsat berdi. Oldingi v0.3.1/v0.3.2 hujjatlaridagi faqat kod/review tartibi tarixiy holatdir. Asl v0.3.2 arxiv o‘zgartirilmadi. Bitrix24, amoCRM/Kommo, 1C, MCP va mijoz DB integratsiyalarining barchasi talabda qoladi, ammo bu checkpoint ularning hammasini tayyor deb e’lon qilmaydi.

## Bajarilgan tekshiruvlar

Testlar haqiqiy mijoz hisoblari yoki bazalarisiz bajarildi. Test jarayonlariga haqiqiy environment kalitlari berilmadi. Python socket chaqiruvlari va Node HTTP/fetch/WebSocket yo‘llari offline guard bilan yopildi. PostgreSQL va tashqi provider transportlari mavjud testlarda almashtirilgan test drayverlari orqali tekshirildi.

| Tekshiruv | Natija | Dalil |
| --- | --- | --- |
| Python runtime, joriy kod | 293 test PASS | `docs/verification/v033/python_runtime.log` |
| Yangi connector regressiyalari | 37 test, yuqoridagi 293 ichida | `runtime_tests/test_connector_authority.py` |
| Node runner | 15 test PASS | `docs/verification/v033/node_runner.log` |
| Browser session client | 8 test PASS | `docs/verification/v033/browser_session_client.log` |
| Release/backup yordamchilari | 3 test PASS | `docs/verification/v033/release_tools.log` |
| Offline SQLite demo | PASS | `docs/verification/v033/sqlite_demo.log` |
| Python sintaksisi | 102 fayl PASS | `docs/verification/v033/python_syntax.json` |
| JS/TS/TSX sintaksisi | 14 fayl PASS, Bun transform | `docs/verification/v033/javascript_typescript_syntax.log` |

Jami **319 test PASS**. 37 yangi test alohida qo‘shib sanalmaydi. Boshlang‘ich runtime holatida 256 test o‘tgan. Birinchi 25 yangi regressiya eski kodda muvaffaqiyatsiz bo‘ldi; red logdagi 34 failure va 9 error subtest natijalarini ham hisoblaydi, ular 43 ta alohida zaiflik degani emas. Keyingi testlar bilan yangi faylda jami 37 test bor.

Haqiqiy vaqtinchalik SQLite bazasidan `Ali` satri o‘qilib, engine task natijasi orqali keyingi agent-loop qaroriga uzatilishi ham tekshirildi. Bu testda rejalashtiruvchi model o‘rnida deterministik funksiya bor; haqiqiy LLM testi yoki semantik fakt tasdig‘i emas.

**TypeScript sintaksisi typecheck yoki Next.js production build degani emas.** FastAPI/pytest/httpx/pydantic/psycopg va boshqa kerakli runtime paketlari Computer muhitida mavjud emas. HTTP testlar, legacy pytest to‘plami, React typecheck va Next build bajarilmadi. Yangi HTTP regression fayli faqat manba/sintaksis bosqichida. Paket o‘rnatish, live provider so‘rovi, CI triggeri va deployment amalga oshirilmadi.

## Koddagi tuzatishlar

`connector_authority.py` o‘qish vakolatini yagona tekshiruvga yig‘adi. `draft`, `authorizing`, `verifying` va `revoked` holatlari odatiy o‘qish ijrosiga ruxsat bermaydi. `configured`, `healthy`, `degraded` o‘qishga ruxsat berishi mumkin, lekin hech biri real health isboti emas. `read` capability, contract version va qo‘llab-quvvatlangan adapter talabi bajariladi.

`agent_ids` endi faqat metama’lumot emas. Bo‘sh bo‘lmagan ro‘yxat aniq mos agentni talab qiladi; bo‘sh ro‘yxat pack `allowed_connections` siyosatiga qo‘shimcha cheklov qo‘ymaydi. Runtime’da pack va connection ruxsatlari birgalikda bajarilishi kerak. Bevosita DB yordamchisiga agent konteksti berilmasa, agentga bog‘langan connection rad etiladi.

Engine topshiriqni qabul qilishda va claim paytida connection vakolatini tekshiradi. Handler DB o‘qishidan oldin yana tekshiradi. Navbatga qo‘yilgandan yoki model qaroridan keyin revoke/scope o‘zgarishi regressionlar bilan qoplandi. Bu allaqachon boshlangan SQL/tarmoq so‘rovini fizik bekor qilish kafolati emas.

Connector descriptor `enabled` uchun faqat haqiqiy boolean qabul qiladi. Lifecycle va capability qiymatlari, duplicate/bounded agent/scope ro‘yxatlari qat’iy tekshiriladi. Driver nomidagi ortiqcha bo‘shliq yashirin tuzatilmaydi. Bekor qilingan connection katalogda `revoked` statusida ko‘rinadi. SQLite platform DB blokirovkasi endi boshqa nomdagi hardlink aliasni ham rad etadi.

## Connection verify API va UI

`POST /platform/{tenant}/connections/{connection_id}/verify` avvalgi bo‘sh so‘rovni agentga bog‘lanmagan connection uchun qabul qiladi. Agentga bog‘langan connection uchun endi `{ "agent": "ops.assistant" }` talab qilinadi. Agentning pack’i ham `connectors.read` va kerakli connection’ga ruxsat berishi kerak. Noto‘g‘ri qo‘shimcha body maydonlari rad etiladi.

Dependency-free `probe_read_connection` owner/integrator actor, workspace freeze, lifecycle, agent va pack ruxsatlarini o‘qishdan oldin qayta tekshiradi. Natija mijoz satrlarini yoki DB manzilini bermaydi, faqat bitta jadvaldan cheklangan o‘qish o‘tganini bildiradi. `persisted=false`: health/lifecycle holati doimiy saqlanmaydi. Mavjud bo‘lmagan yoki ruxsatsiz connection probe’lari umumiy 403 bilan yopiladi.

Dashboard source’iga agent tanlash va probe tugmasi qo‘shildi. Probe natijasi doimiy health o‘rniga alohida vaqtli natija sifatida ko‘rsatiladi. UI source sintaksisi tekshirildi, lekin brauzer E2E yoki React/Next build bajarilmadi. API request serialization va yangi HTTP regressiyalar ham to‘liq dependency muhitida qayta bajarilishi shart.

## Ochiq production to‘siqlari

| Holat | To‘siq | Chegara |
| --- | --- | --- |
| OPEN, yuqori | Next.js 14.2.5 | Rasmiy App Router DoS tuzatishlaridan eski; paket/lock yangilanmadi |
| BLOCKED | HTTP, React typecheck, Next build | Kerakli paketlar bu Computer’da yo‘q, muvaffaqiyat taxmin qilinmadi |
| OPEN, yuqori | MCP/umumiy HTTP egress | Aniq destination policy, private-network chegarasi va DNS rebinding himoyasi hali yo‘q |
| OPEN | CRM/ERP adapterlari | Bitrix24, amoCRM/Kommo, 1C provider OAuth, sync, write receipt va reconcile yozilmagan |
| OPEN | PostgreSQL live acceptance | Haqiqiy TLS, read-only role, ikki tenant va timeout sinovlari bajarilmadi |
| OPEN | Identity/vault | Session-level transaction fencing, OIDC/MFA, secret vault va rotation to‘liq emas |
| OPEN | Runner resurs limitlari | Statik review: `readFileSync(fd)` o‘suvchi faylni katta hajmda o‘qishi, `readdirSync` katta katalogni to‘liq yig‘ishi mumkin; bu checkpointda tuzatilmadi |
| OPEN | Global revoke | Konfiguratsiya barcha workerlarda mos bo‘lishi kerak; durable generation fence va in-flight cancellation yo‘q |
| OPEN | Qolgan PRD | LLM spend ledger, data retention/redaction, billing, load/fault, offsite restore va staging pilot |

Bu yo‘naltirilgan kod auditi va regression tekshiruvi, to‘liq penetration test yoki barcha zaifliklar topilgani haqidagi xulosa emas. Avval o‘qilgan task natijalarining retention siyosati connection revoke bilan avtomatik o‘chmaydi.

## Dependency xavfsizligi bo‘yicha dalil

`apps/ui/package.json` va lock fayl Next.js `14.2.5` ni ko‘rsatadi. Rasmiy 2025-12-11 advisory, keyingi addendum bilan, Next 14 App Router uchun DoS masalasini va o‘sha reliz qatoridagi `14.2.35` tuzatishini qayd etadi. Bu `14.2.35` bugungi kunda yetarli yoki eng yangi degani emas. Joriy support policy Next 14 ni unsupported deb ko‘rsatadi; yangi supported LTS va joriy advisorylar asosida to‘liq migration/lock/build tekshiruvi kerak.

Manbalar, 2026-09-14 foydalanuvchi sanasida ochib ko‘rildi:

- https://nextjs.org/blog/security-update-2025-12-11
- https://nextjs.org/support-policy
- https://nextjs.org/blog/july-2026-security-release

Paketlar yoki lock integrity qiymatlari qo‘lda uydirilmadi. `.github/workflows/verify.yml` ichiga UI `npm audit --audit-level=high` job’i qo‘shildi. Bu CI job hali ishga tushirilmagan; lockdagi xavf bartaraf etilgani haqida da’vo yo‘q. Python dependencylar hamon keng `>=` oraliqlarida, to‘liq reproducible lock va dependency audit ochiq qoladi.

## Takrorlash va keyingi ish

Repository ildizidan `python scripts/verify_offline.py` bajariladi. Skript yangi timestampli evidence katalogini yaratadi, mavjud dalilni ustidan yozmaydi. HTTP/UI build hamda live acceptance bu skriptga kirmaydi. Qolgan tekshiruvlar tayyor dependency muhitida mavjud CI workflow orqali bajarilishi kerak.

PRDdagi gibrid model saqlanadi: core cloud’da, local runner mijozda; premium on-prem alohida yo‘l. Keyingi qaror mijozning o‘z DB’siga yozish chegarasi: DB faqat o‘qish va CRM/ERP API orqali tasdiqli yozuvmi, yoki DB uchun ham alohida oldindan belgilangan, tasdiqli write amallari kerakmi? LLMga ixtiyoriy raw SQL berish rejalashtirilmaydi.

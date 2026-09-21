# Source audit va v0.2 o‘zgarishlar hisoboti

## Hukm

Google Drive’dagi ZIP muvaffaqiyatli yuklab olindi. Original SHA256: `5fff3925655b410fc0b8c103280fc58ac860224924ad52de9b832c0201f5a88f`. Original 132 fayl, 506,616 byte unpacked, 69 Python fayl. Arxivda path traversal yoki symlink topilmadi. Original alohida saqlangan.

Stack: Python FastAPI, SQLite durable runtime, Next.js 14.2.5/React18 dashboard, Node read-only Linux runner va YAML agent packlar. Bu allaqachon engineering preview ekanini original README/status ham bildirgan. “Hamma CRM/ERP, to‘liq admin, o‘zbekcha stream tayyor” degan xulosa kodga mos kelmaydi.

## Audit qamrovi

Barcha original fayllar inventarizatsiya qilindi, Python source AST bilan parse qilindi. Chuqur qo‘lda/source review: asosiy PRD/design/status hujjatlari, runtime engine/tools/LLM/MCP, platform API, auth/config/security, UI, runner, voice seam va deploy/test konfiguratsiyasi. Webhook/pipeline integration nuqtalari qo‘shimcha ko‘rildi. Bu har legacy modulning har bir qatoriga mustaqil penetration test o‘tkazildi degani emas. Dependency SCA, browser E2E va live provider tekshiruvlari bajarilmadi.

Mustaqil read-only review oqimlari ishlatildi; ularning topilmalari asosiy kod bilan qayta tekshirildi. Misol: eski runner transportidagi claim/lease kamchiligi faol APIga tegishli deb qabul qilinmadi, chunki `main.py` eski router’ni include qilmaydi va `/runner/*` mutation 410 bilan yopilgan. Yangi `/platform/runner/ws` boshqa, fencingli yo‘l. Ikkinchi reviewer yo‘q degan `/events` va `/connections` route’lari AST orqali borligi tasdiqlandi. Review xulosasi kod dalilisiz haqiqat deb olinmadi.

## Talab va real holat

| Soha | Original dalil | v0.2 holati |
|---|---|---|
| Yagona profil/workspace | JWT tenant+role, token paste | Server identity/role UI yaxshilandi; membership/login yo‘q |
| CRM/ERP/customer DB | Telegram/Instagram/Sheets/MCP adapterlari, local records | SQLite export read-only connectori haqiqiy lokal DB bilan ishlaydi; universal/live CRM yo‘q |
| Agent execution | Durable task/steps/approval | Freeze, independent approval va agent connection scope kuchaytirildi; bounded replanning qolgan |
| Ovoz | Legacy endpointlar 501 | Aisha REST TTS/STT adapterlari mock-contract test; stream/upload/playback yo‘q |
| Platform admin | Bootstrap ADMIN_TOKEN va tenant control | To‘liq admin lifecycle, billing, secrets manager hali yo‘q |
| Deploy | Local Docker va CI fayli | Production tasdiqlanmagan, Next security upgrade blocker |

Original PRD’dagi 146 test/19 runner/UI build da’volari joriy baseline statusdagi 71/13 bilan mos emas. Biz originalni qayta bajardik: 71 Python runtime, 13 Node. Hujjatdagi tarixiy raqam bilan yangi muhitdagi dalil aralashtirilmadi. PRD Postgres/pgvectorni ko‘zlaydi, real runtime SQLite. Yangi design “butun platforma” scope’i bilan eski phased MVP scope’i ziddiyatli; yangi PRDlar yakuniy katta maqsadni kichraytirmasdan, release gate’larini ajratadi.

## Tasdiqlangan va tuzatilgan masalalar

**AUTH-01, yuqori:** `app/security.py::dev_open` unset/dev/test muhitni avtomatik ochiq hisoblagan. `auth.py` importidagi startup validation unset ENV’ni rad qilgani sabab “unset ENV bilan real app anonim owner chiqaradi” deb da’vo qilinmaydi. Ammo explicit dev/test oldindan ma’lum secretlar va admin yo‘qligida ochiq edi. Endi alohida `ALLOW_INSECURE_DEV=true` kerak; oddiy dev/prod unique secretlarsiz boshlanmaydi. Ma’lum demo JWT secret normal rejimda rad etiladi.

**EXEC-01, yuqori:** freeze faqat claim/event processingni to‘xtatgan, yangi task/event/approval/schedule qabul qilish davom etgan. Endi ular transaction ichida rad etiladi; idempotent qayta yuborilgan oldingi task/event xavfsiz eski natijani qaytaradi. Dispatchdan oldingi fence qo‘shildi. Network side effectni mutlaq bekor qilish kafolati berilmaydi.

**EXEC-02, policy hardening:** ikki kishilik approval siyosati yo‘q edi. Endi `approval.independent` opt-in policy bilan creator approve qilolmaydi. Hamma write’ni ikki kishi tasdiqlaydi degan da’vo emas; default compatibility saqlangan.

**MCP-01, o‘rta/yuqori:** MCP tool name allowlist bo‘lib, per-tool argument schema yo‘q edi. `tool_schemas` majburiy, unknown/invalid argument tarmoqqacha rad etiladi. URL/DNS egress hardening alohida ochiq gap.

**RUN-01, o‘rta:** allowlisted directory listing child `.env`, `password`, `.ssh` yoki tashqi symlink nomlarini ko‘rsatishi mumkin edi. Har child `permitted`dan o‘tadi; denied entry yashiriladi. File o‘qilgandan so‘ng o‘sib ketgan size qayta tekshiriladi. OS sandbox va parent-directory race production xavfi hali mavjud.

**UI-01, o‘rta:** logout audit/inbox/devices kabi state’ni tozalamas edi; keyingi tenantda eski state qolishi mumkin edi. Endi barcha tegishli state tozalanadi va busy paytida logout bloklanadi. Server rolidan owner/operator/viewer/integrator control holati olinadi; packdagi haqiqiy birinchi agent tanlanadi. Catalog tenantga biriktirilgan tool’largacha cheklanadi.

## Yangi implementatsiya

`platform_runtime/connectors.py` local customer SQLite exportdan approved base table/columnsni read-only o‘qiydi. Arbitrary SQL, URL, DSN, user path qabul qilmaydi. Mount root, agent connection allowlist, table/column policy, parametrli equality filter, 100 row, timeout/VM va result hajm cheklovi bor. Boshqa tenant connection ID’si rad etiladi. Disabled connector catalogni buzmaydi, metadata’dan chiqariladi.

`platform_runtime/speech.py` Aisha rasmiy REST request/response kontraktiga mos TTS/STT adapteri. `voice.tts` runtime’da approval talab qiluvchi tashqi pullik tool. Uzbek TTS text va mood; model Gulnoza. STT lokal adapter metodi mavjud, public uploadga ulanmagan. Download URL o‘zboshimchalik bilan fetch qilinmaydi. Provider billing/quality/live success tasdiqlanmagan.

Dashboard connector tab faqat sanitized metadata ko‘rsatadi. OAuth self-service onboarding yoki bazaga istalgan credential bilan live ulanish deb taqdim etilmaydi. Yangi capabilitylar mavjud packlarga avtomatik keng vakolat bermaydi; `config/agent-capabilities.example.yaml` orqali operator explicit tanlaydi.

## Ochiq P0/P1 release blockerlar

1. Supported Next.js versiyasiga intentional upgrade, regenerated lockfile, dependency audit va clean build. Eski 14.2.5 o‘zgartirilmay qoldirildi, chunki o‘rnatish/buildsiz lockfile va yangi versiya ishlashini tasdiqlab bo‘lmaydi.
2. Haqiqiy login, user/workspace membership, revoke/refresh va platform-admin isolation.
3. KMS/vault/OAuth refresh, aniq provider host va DNS rebinding/SSRF egress policy. Hozir LLM/MCP manzili operator configga tayanadi.
4. Budget ledger, spend reservation va billing. Faqat inbox count quota bor, pul limitini almashtirmaydi.
5. Customer CRM/ERP adapterlari, sync/conflict model, agent result-based loop.
6. Production DB/PostgreSQL migratsiyasi, backup/restore, retention/PII delete va staging pilot.
7. Voice streaming gateway, real Uzbek benchmark, telephony codecs/barge-in va commercial/API tariff shartlari.

Rasmiy dependency/advisory va voice manbalari `prd-v02/04-VOICE-COST-QUALITY.md` hamda `06-RELEASE-ACCEPTANCE.md`da.

## Test dalili

Original: **71 Python + 13 Node**. v0.2: **114 Python + 15 Node**, lokal qayta bajarilgan. Yangi HTTP integration testlar yozildi, ammo bu muhitda FastAPI/Pydantic/PyJWT/pytest/httpx va frontend dependencylar yo‘q. UI uchun faqat TypeScript7 `--noCheck` syntax-only tekshiruvi o‘tdi, bu React typecheck yoki Next build emas. Unit testlar live audio va provider sifatini o‘lchamaydi.

Loglar `docs/verification/v02-*`da, o‘zgargan fayllar `v02-changes.json`da. “Tayyor” degan status faqat tekshirilgan doira uchun ishlatiladi.

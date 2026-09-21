# Avtonom development reyestri

Joriy source: **v0.3.6 development checkpoint**. Holat: **IN_PROGRESS**. To‘liq PRD yoki 100% tayyor mahsulot deb e’lon qilinmagan.

Foydalanuvchi tartibi: qismlar ketma-ket yoziladi, lokal test va logic review qilinadi, xatolar tuzatiladi. Real server/qurilma/provider acceptance’ni foydalanuvchi development oxirida o‘zi bajaradi. Modullar o‘rtasida ruxsat so‘ralmaydi. Aniqliklar kechiktiriladi, faqat boshqa foydali ishni ham to‘xtatadigan savol beriladi.

## Ushbu checkpoint dalillari

`docs/verification/development-v036-final/summary.json` va yonidagi loglar:

| Guruh | PASS |
| --- | ---: |
| Python runtime | 664 |
| Release helpers | 3 |
| Manifest helpers | 6 |
| Node runner | 24 |
| Browser session client | 12 |
| Jami | **709** |

v0.3.5 bazasi 486 edi. 223 ta yangi test qo‘shildi. SQLite demo, managed DB demo, Python hamda JS/TS/TSX syntax alohida PASS, test soniga qo‘shilmagan. Round2’da model JSON chuqurligi bo‘yicha regression topildi va explicit limit bilan tuzatildi. Yakuniy log o‘sha fixni ham tekshirgan.

## Development holati

| Qism | Source holati | Dalil / qolgan chegara |
| --- | --- | --- |
| Original ZIP | Saqlangan | 302 manifest entry mos |
| DynamoDB | Cheklangan transport ulandi | fake SDK + approval/replay, native yo‘q |
| Cassandra | Cheklangan transport ulandi | fake SDK/LWT, native yo‘q |
| Neo4j | Cheklangan property-node transport ulandi | fake SDK, real locking/plugin acceptance yo‘q |
| Elasticsearch | Exact-document transport ulandi | fake SDK/native CAS argumentlari, cluster acceptance yo‘q |
| Usage budget | Lokal ledger + planner + API/UI ulandi | real SQLite; subscription invoice/voice/tool sarfi qolgan |
| Knowledge ingestion/retrieval | Text/BM25/ACL/version/tombstone + API/UI ulandi | real SQLite; synthetic vectors; binary/embedding provider qolgan |
| Lokal model | Explicit numeric-loopback source ulandi | mocked HTTP; real inference/quality yo‘q |
| Linux read-only runner | Xavfsizlik fixlari | haqiqiy fayllarda lokal test |
| macOS runner | F_GETPATH helper + unsigned LaunchAgent generator | Darwin syscall mocked; real Mac, tray, .pkg/signing qolgan |
| Windows runner | Qilinmagan | native descriptor/service source qolgan |
| Desktop automation | Qilinmagan | screen, click/type, browser, Office, printer, 1C, IoT |
| OAuth/token lifecycle | Qolgan | safe state/PKCE/refresh/revoke va provider credentials |
| Google/CRM adapterlari | Qolgan | Gmail/Drive/Calendar, Bitrix24/Kommo, 1C |
| Identity/MFA/recovery | Qolgan | TOTP, recovery, email verification, OIDC/JWKS |
| Voice | Foundation eski source’da | consent/stream/cancel va to‘liq STT/TTS qolgan |
| UI product completeness | Qisman | yangi panellar source/syntax; qolgan UX/CRUD sozlamalar, typecheck/build |
| Production operations | Qolgan | vault/KMS, billing, recovery, egress, observability va deploymentni foydalanuvchi keyin qiladi |

## Keyingi avtonom development paketi

1. OAuth state, PKCE, callback binding, authorization generation, refresh single-flight va revoke uchun dependency-light core yozish, testlash.
2. Gmail/Drive/Calendar uchun typed, tenant/agent scoped API transportlari va approval/replay integratsiyasi.
3. Bitrix24/Kommo typed connectorlari va webhook deduplication.
4. So‘ng knowledge document parsers/embedding adapteri, voice, identity, qolgan desktop/UI qismlari.

Bu ustuvorlik live testni talab qilmaydi. Credential, haqiqiy app ID yoki server bo‘lmasa konfiguratsiya orqali ajratiladi; boshqa source development to‘xtatilmaydi.

## Keyinga qoldirilgan savollar va cheklovlar

- ECC’ning aniq repo/paket/versiyasi yo‘q. Mavjud toolsetda ECC yoki alohida code-review agent topilmadi. O‘rnatildi/mustaqil audit qilindi deb aytilmaydi.
- Computer’da yangi paketlar o‘rnatish ruxsat etilmagan. Native SDK, FastAPI/Next to‘liq dependency-backed sinovlari bajarilmagan. Bu source ishining to‘xtashi emas.
- Model nomlari, haqiqiy narxlar, OAuth IDs, signing identifikatorlari keyin operator tomonidan kiritiladi. Sirlar chatdan so‘ralmaydi.
- Lokal LLM loopback API/worker hostiga tegishli; cloud core mijoz Mac modeliga avtomatik ulanmaydi.
- Mac/Windows real acceptance, tashqi provider va deployment natijalari hozir tasdiqlanmaydi.

## Qayta ochish

Persistent checkpoint: `agent-platform-v0.3.6-development.zip`. ZIP ichida `agent-platform/` ildizi bor. Arxivni xavfsiz ochib, `python scripts/verify_manifest.py` bilan entrylarni tekshirish mumkin. Original v0.3.5 ZIP alohida saqlangan. `.git` tarixi ishlab chiqilmadi va remote tizimlarda hech narsa joylashtirilmadi.

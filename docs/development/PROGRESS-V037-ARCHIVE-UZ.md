# Avtonom development reyestri

Joriy source: **v0.3.7 development checkpoint**, holat: **IN_PROGRESS**. Umumiy PRD 100% tugadi deb aytilmaydi. Foydalanuvchi modullar oralig‘ida ruxsat so‘ramasdan source yozish, lokal test, mantiqiy review va fixni davom ettirishni so‘ragan. Credential, real provider, native qurilma va deployment acceptance keyinga qoldiriladi. Faqat barcha foydali ishni to‘xtatadigan savol beriladi.

## Mustaqil lokal dalil

`docs/verification/development-v037-migration-final/summary.json`:

| Guruh | PASS |
|---|---:|
| Python runtime, vault, OAuth, Google, sync, migration | 764 |
| Release helpers | 3 |
| Manifest helpers | 6 |
| Node runner | 24 |
| Browser session client | 12 |
| Browser OAuth boundary | 12 |
| Jami | **821** |

v0.3.6 bazasi 709 edi; **112 ta yangi lokal test**. Demos va syntax PASS, ular test soniga qo‘shilmagan. v0.3.6 original frozen SQL schemadan migration ham haqiqiy SQLite’da tekshirildi. Python 3.14.6, cryptography 50.0.1 ishlatilgan; deployment Python 3.11 bu muhitda alohida tekshirilmagan.

## Ushbu blokda qilingan ish

CORS PUT nomuvofiqligi tuzatildi. Legacy runner exposed deb aytilgan oldingi audit xatosi to‘g‘rilandi. AES-GCM secret vault, OAuth state/PKCE/owner/session-family/generation binding, single-flight refresh, encrypted revoke outbox, issued-token cleanup, owner OAuth HTTP API va popup UI yozildi. 7 typed Google tool engine approval va durable dispatch journal bilan ulandi. Atomik sync cursor/dedup/tombstone/lease foundation yozildi. Ikki mustaqil reviewer topgan kamchiliklar fixlandi, qo‘shimcha regressiyalar bilan qayta tekshirildi.

Batafsil source yo‘llari: `V037-IMPLEMENTATION-UZ.md`. Bu checkpoint OAuth/Google blokini sezilarli rivojlantiradi, barcha product scope’ni tugatmaydi.

## Keyingi ketma-ket source bloklar

1. Gmail history, Drive changes va Calendar incremental sync walkerlar. Generic sync foundationni providerga xos cursor/version/event mapping bilan ulash.
2. Google provider write reconciliation: Calendar receipt lookup, Gmail Message-ID yordamida read-only evidence lookup; uncertain natijani faqat matching provider dalili bilan yakunlash.
3. Bitrix24 va Kommo typed OAuth connectorlari, webhook auth/dedup, field mapping va incremental sync; generic CRUD deb noto‘g‘ri ko‘rsatmaslik.
4. 1C va custom HTTP allowlisted typed adapters.
5. Identity lifecycle: password change/admin disable/session inventory/revoke, MFA/recovery/OIDC key rotation.
6. Knowledge parser/embedding/grounding pipeline.
7. Unified usage meter va subscription/invoice/payment adapterlari.
8. Public voice consent/STT/TTS/stream/cancel/storage.
9. Mac runner product source: Keychain, tray, installer/update/rollback; native acceptance alohida.
10. Windows runner va desktop adapters; xavfli tool’lar approval bilan.
11. UI product completeness, supported Next upgrade va dependency lock; haqiqiy build/typecheck faqat mavjud dependency muhiti bilan.
12. Versioned migrations, HA storage/queue, observability, backup/restore, egress/vault deployment source.

## Muhit cheklovlari

Computer’da paket o‘rnatish ruxsat etilmagan, workspace outbound allowlist bo‘sh. FastAPI/Pydantic/PyJWT/httpx/pytest va Next/React dependency-backed testlar hozir NOT_RUN. Bu boshqa source ishini to‘xtatmaydi. Tashqi credentials chatda so‘ralmaydi. Hostlangan KMS, native Mac/Windows, real provider, database cluster, real LLM, staging va production acceptance hali tasdiqlanmagan.

ECC aniq skill/tool sifatida mavjud emas; o‘rnatildi deb aytilmaydi. Buning o‘rniga ikkita real independent source review va kod regressionlari bajarildi. Oldingi v0.3.6 reyestr arxivda saqlandi.

## Qayta tiklash ma’lumoti

Original v0.3.6 ZIP SHA-256: `14cf92659612062a8555d3f6e74c6f9a2a47079de4d2d5e6c130b096f9258e1e`. Yangi development nusxa `agent-platform-v0.3.7-development.zip` bo‘ladi. ZIP ichida `agent-platform/` ildizi va yangilangan `MANIFEST.sha256` bor. Source `/tmp/ap37/agent-platform` ichida ham bor, lekin faqat persistent ZIP ishonchli davom etish nuqtasi. Production qarori hali NO_GO.

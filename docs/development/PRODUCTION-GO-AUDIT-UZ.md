# PRODUCTION GO AUDIT — nima to'sib turibdi (2026-09-29)

> **Maqsad:** bitta savolga javob — *production GO holatiga kelishga nima to'sqinlik
> qilyapti, nima ochiq/tugatilmagan qolyapti?* Har bir da'vo **o'lchovga** asoslangan
> (registry, fayl tizimi, test to'plami, `measure_reachability.py`). Taxmin yo'q.
>
> Bu fayl — **yagona joriy NO_GO hisoboti**. Versiya hujjatlari (`V036`–`V04`),
> checkpoint'lar (`CHECKPOINT-V031`–`V035`) va `ULTRA-AUDIT-*` — **audit trail**; ular
> `PROGRESS-UZ.md`, `README.md` va `IMPLEMENTATION-STATUS.md` dan
> havola qilingan, shuning uchun **o'chirilmaydi**. (`docs/verification/` 2026-09-27..29
> tozalashda o'chirilgan, git tarixida; `verify_offline.py` dalillari endi `.verify-offline/`.) Joriy holat uchun faqat shu fayl va `IMPLEMENTATION-STATUS.md`
> o'qiladi.

## Verdikt

**v0.5 · IN_PROGRESS · production NO_GO.** Yadro (engine, identity, approval, scheduling,
tenant isolation) qurilgan va offline test bilan qoplangan. Lekin **mahsulot hech qachon
real muhitda ishga tushirilmagan**: API bu mashinada start olmagan, hech bir provider
`live_verified` emas, va tool-owning kodining **83.5%** ni hech bir pack e'lon qilmaydi
(bu «o'lik kod» emas, faqat pack e'lon qilmagan tool modullari o'lchovi). Bular
**release blocker**.

## Bir qarashda (2026-09-29 o'lchovi)

| Ko'rsatkich | Qiymat | Manba |
|---|---|---|
| Registry tool'lari | **91** | `build_registry(catalog, shop_data)` |
| Pack'dan yetib boradigan tool | **20 / 91** | `scripts/probes/measure_reachability.py` |
| Hech bir pack e'lon qilmagan tool-owning LOC | **83.5%** (11 345 / 13 591; **17 / 22** modul) | `measure_reachability.py` |
| Butun paket bo'ylab (xuddi shu qoida) | **84.1%** (11 890 / 14 136; 19 / 24) | `measure_reachability.py` |
| Pack e'lon qiladigan tool-owning modul | **5**: `knowledge`, `oversight`, `shop_tools`, `tools`, `whatsapp` | `measure_reachability.py` |
| Yetkazilgan pack'lar | `demo-retail`, `marketing`, `turkish-baby` (+ `_template`) | `packs/` |
| `runtime_tests` (offline) | **3 718**; Windows: `failures=1, errors=12, skipped=1` | `api-python/runtime_tests` |
| Windows-only **bloklangan** | **13** (`test_portable_fs` ×11, `test_macos_bundle` ×1, `test_foundation_v02` mount_scope ×1; sabab bilan qadalgan) | `test_platform_baseline.py` |
| `integration_tests` | **386 / 386 PASS** (`ENV=test ALLOW_INSECURE_DEV=true PIPELINE_MODE=platform IDENTITY_DIRECTORY=false python -m pytest integration_tests -q`, `cryptography` kerak) | `api-python/integration_tests/` |
| `scripts` testlari; `e2e_smoke.py` | OK; **15 / 15** (haqiqiy API + worker, soxta model + soxta Telegram, loopback) | `scripts/` |
| Node runner (`apps/runner/test.js`) | **21 / 32** Windows'da (11 tasi POSIX-only, qadalgan) | `windows-baseline.test.js` |
| UI gate'lari | typecheck + build toza, node `lib` testlari **101 / 101**, `npm audit` **0 vuln** (`next@16.3.6`, `react@19.3.0`) | `apps/ui` |
| CI (`verify.yml`) | aynan **4 job**: `core`, `http`, `ui`, `ui_dependency_security` (`manifest_integrity` / `measure_reachability` — `verify_offline.py` job nomlari, CI emas) | `.github/workflows/verify.yml` |
| `MANIFEST.sha256` | 2026-09-27 da PASS (573 fayl, 0 xato); 2026-09-29 da qayta o'lchanmagan | `scripts/verify_manifest.py` |
| **API bu mashinada ishga tushganmi?** | **Yo'q** — `.env` yo'q, `integrations.json` yo'q, `app.db` 74 jadval **hammasi 0 qator** | fayl tizimi |
| Biror provider `live_verified`mi? | **Yo'q** — eng yuqori `LOCAL_CONTRACT_TESTED`; haqiqiy Telegram va haqiqiy model bilan jonli sinov o'tkazilmagan | test kodi |

## Production GO'ni to'sib turgan blockerlar

Har biri **release gate** — yopilmaguncha GO yo'q.

### B1. Mahsulot hech qachon real muhitda start olmagan
- `api-python/.env` **yo'q**, `config/integrations.json` **yo'q**.
- `api-python/data/app.db` da **74 jadval** bor (to'liq migratsiya), lekin **hammasi 0 qator**
  — schema mavjud, operational ma'lumot yo'q.
- First-run yo'li faqat `scripts/provision_identity.py`. Eski `setup_local.py` →
  `owner_login.py` ketma-ketligi **o'lik** (410 / 403; UI'da token maydoni yo'q;
  `owner_login.py` o'chirilgan).
- `scripts/e2e_smoke.py` (15/15) vaqtinchalik muhitda haqiqiy API + workerni soxta model va
  soxta Telegram bilan yurgizadi — bu real deploy emas.
- **Nima qilish kerak:** real muhitda bir marta to'liq start → bitta haqiqiy inbound →
  bitta haqiqiy outbound yozuv → `app.db` da operational qatorlar. Bu **end-to-end smoke**.

### B2. Hech bir provider `live_verified` emas
- Telegram, Instagram, WhatsApp, Google Sheets, LLM (OpenAI-compatible), MCP — barchasi
  faqat **mock/contract transport** bilan test qilingan (`LOCAL_CONTRACT_TESTED`).
- Real kalitlar ishlatilmagan; tashqi xabar, CRM/Sheets yozuvi yoki qurilma ijrosi real
  tizimga yuborilmagan.
- **Nima qilish kerak:** kamida bitta kanal (mas. Telegram) uchun real token bilan
  `live_verified` darajasiga chiqish; qolganlarini keyin.

### B3. WhatsApp live Meta acceptance yo'q
- HTTP route **bor** (`app/whatsapp_api.py`; HTTP testlari
  `integration_tests/test_whatsapp_webhook_http.py`), `whatsapp.*` tool'lari
  `turkish-baby` pack'ida e'lon qilingan.
- Lekin **real Meta acceptance** (haqiqiy webhook verification + outbound) bajarilmagan.
- **Nima qilish kerak:** Meta sandbox/business account bilan real webhook handshake va
  bitta template xabar.

### B4. Tool-owning kodining 83.5% hech bir pack tomonidan e'lon qilinmagan
- Bu **o'lik kod o'lchovi emas**: u faqat pack e'lon qilmagan tool modullarini sanaydi
  (`whatsapp_inbound` webhook route orqali, `escalation` worker orqali baribir yetib boriladi).
- **17** muzlatilgan (frozen) tool-owning modul: `assets`, `business_graph`, `connectors`,
  `documents`, `erp`, `escalation`, `google_adapters`, `inventory`, `manufacturing`, `oee`,
  `sheets`, `speech`, `supervisor`, `telephony`, `vision`, `whatsapp_inbound`, `workforce`.
- Ular test va chegara auditi bilan qoplangan, lekin **hech bir mijoz konfiguratsiyasi**
  ularning tool'larini e'lon qilmaydi. Hujjatlarda "backend tayyor" deb yozilgan joyda gap **yadro**
  haqida, bu modullar haqida emas.
- **Nima qilish kerak:** GO uchun bu modullarni yo (a) mahsulot funksiyasi sifatida pack'ga
  ulash, yoki (b) "preview/frozen" deb aniq belgilab, release scope'dan chiqarish. Hozir
  ular "tayyor" taassurotini beradi-yu, lekin pack ularni e'lon qilmaydi — bu **hollow risk**.

### B5. Platforma yuzasi (Windows) qizil, POSIX o'lchovi olinmagan
- Windows'da **13** runtime test bloklangan (`test_portable_fs` ×11, `test_macos_bundle` ×1,
  `test_foundation_v02` mount_scope ×1) va Node runner **21/32** (11 tasi POSIX-only).
  Barchasi sabab bilan qadalgan (`test_platform_baseline.py`, `windows-baseline.test.js`) —
  yangi qizil = FAIL.
- CI (`ubuntu-latest`) bu 13 sinovni yurgizadi, lekin **lokal POSIX o'lchovi olinmagan**.
- **Nima qilish kerak:** POSIX'da bir marta to'liq `verify_offline.py` + Node runner
  yashil o'lchovini olish (CI natijasi bilan tasdiqlash).

### B6. Legacy `api-python/tests/` — YOPILGAN
- `api-python/tests/` **2026-09-22 da nafaqaga chiqarilgan va papka mavjud emas**. Tirik
  kodni sinaydigan 5 fayl `api-python/integration_tests/` ichiga ko'chirilgan, qolgani
  o'chirilgan. Ilgari yozilgan «33 qizil / 172 pass» endi amal qilmaydi.
- Qaror va sabablar: `api-python/integration_tests/LEGACY-RETIRED.md`.
- **Nima qilish kerak:** hech narsa (blocker emas).

### B7. Release gate bo'lmagan integratsion tekshiruvlar
- FastAPI HTTP, real WebSocket e2e, Docker build, to'liq React typecheck/build (CI'da bor,
  lekin lokal Computer'da dependency yo'q), real CRM/Sheets yozuvi — bular **acceptance
  sifatida ishlatilmagan**.
- **Nima qilish kerak:** GO dan oldin kamida Docker build + bitta real HTTP e2e + bitta
  real Sheets/CRM yozuvini bajarish.

## Ochiq / tugatilmagan ishlar (blocker emas, lekin ochiq)

| # | Ochiq ish | Holat | Manba |
|---|---|---|---|
| O1 | Umumiy baseline fixture (3 daqiqalik to'plam uchun) | **hali yo'q** | `QOLGAN-ISHLAR-INVENTAR-UZ.md` §11 |
| O2 | `api-python/tests/` nafaqaga chiqarish | **bajarilgan** (2026-09-22), papka yo'q | B6 |
| O3 | 17 frozen modulni release scope'dan chiqarish yoki pack'ga ulash | **qaror kutilmoqda** | B4 |
| O8 | Native Claude `tool_use` planner va sotuv-bot eval to'plami | **boshlangan, to'xtatilgan** | — |
| O9 | Web kanalidagi bir martalik planner worker drain'ni to'sishi mumkin | **ochiq** | — |
| O10 | Tenant integratsiya yozmasligi kerak (konfig istalgan env nomini ko'rsata oladi) | **ochiq** | — |
| O11 | SQLite faqat bitta host | **ochiq** | — |
| O4 | Lokal POSIX `verify_offline.py` + Node runner yashil o'lchovi | **olinmagan** | B5 |
| O5 | Real provider `live_verified` (kamida 1 kanal) | **yo'q** | B2 |
| O6 | WhatsApp real Meta acceptance | **yo'q** | B3 |
| O7 | End-to-end smoke (real start + 1 inbound + 1 outbound) | **bajarilmagan** | B1 |

## Hujjat tozaligi — nima qilindi, nima saqlandi

**Saqlandi (audit trail — o'chirish havolalarni buzadi):**
- `docs/development/V036-*.md` … `V04-IMPLEMENTATION-UZ.md` — har bir versiya o'zgarishi;
  `PROGRESS-UZ.md` dan havola qilingan (`docs/verification/*/changes.json` 2026-09-27..29
  tozalashda o'chirilgan, git tarixida).
- `docs/CHECKPOINT-V031-UZ.md` … `CHECKPOINT-V035-UZ.md` — checkpoint'lar;
  `PROGRESS-UZ.md` dan havola qilingan.
- `docs/development/ULTRA-AUDIT-ASCII-CELL-UZ.md` — chegara auditi fazalari (§125–§157).

**Tuzatildi (divergensiya bartaraf etildi):**
- 2026-09-27: `IMPLEMENTATION-STATUS.md` — runtime_tests 3 601→3 651, integration 351→357,
  reachability "10 447 / 19 182, 54%, 14 modul" → 83.8% / 11 283 / 13 460, 17 modul.
- 2026-09-29: barcha joriy hujjatlar **3 718 / 386 / 21 of 32 / e2e 15 of 15** ga, reachability
  **83.5% / 11 345 / 13 591** ga yangilandi; «83.8% unreachable» «hech bir pack e'lon qilmagan»
  deb qayta so'zlandi; CI job ro'yxati 4 ta (`core`, `http`, `ui`, `ui_dependency_security`) ga
  tuzatildi; `api-python/tests/` «33 qizil» da'vosi olib tashlandi.
- `QOLGAN-ISHLAR-INVENTAR-UZ.md` §11 — "3 148 sinov" → 3 651, "95 integration" →
  357, "ui FAIL next@14.2.35" → PASS next@16.3.6 (2026-09-27 raqamlari; joriy raqamlar yuqorida).

**O'chirildi (havolasiz aniq dublikatlar — `docs/reference/` papkasi):**
- `docs/reference/agent-platform-PRD-TZ.md` — `docs/agent-platform-PRD-TZ.md` bilan bir xil
  (kanonik nusxa README/IMPLEMENTATION-STATUS/ONBOARDING dan havola qilingan).
- `docs/reference/2026-09-13-v1-sales-platform-implementation.md` —
  `docs/superpowers/plans/...` bilan bir xil.
- `docs/reference/2026-09-13-v1-sales-platform-design.md` — `docs/superpowers/specs/...`
  bilan bir xil (kanonik nusxa implementation faylidan havola qilingan).
- Hech bir `.md` `docs/reference/` ni havola qilmagan → o'chirish havolalarni buzmadi.

## Manbalar (qayta hisoblanadigan)

- `scripts/probes/measure_reachability.py --json` — registry/pack e'lon qilgan/LOC
  (`scripts/probes/` da faqat shu skript qolgan).
- `scripts/verify_offline.py` — runtime_tests, manifest, reachability job'lari (bu CI job'lari
  emas; CI faqat `core`, `http`, `ui`, `ui_dependency_security`).
- `api-python/integration_tests/` — 386 HTTP/integration testi.
- `python scripts/e2e_smoke.py` — 15 tekshiruv (haqiqiy API + worker, soxta model/Telegram).
- `apps/runner/test.js` + `windows-baseline.test.js` — Node runner platforma yuzasi.
- `docs/IMPLEMENTATION-STATUS.md` — status bloki + tarixiy holat.
- `docs/development/QOLGAN-ISHLAR-INVENTAR-UZ.md` — to'liq inventar (§0–§11).

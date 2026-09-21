# v0.3.9 implementation: 1C va custom HTTP CRM adapterlari

## Scope

`BACKLOG.json`dagi navbatdagi blok — `1c_custom_http` — bajarildi. Bu blok PRD'ning
o‘zgarmas tamoyilini (#1: core bir marta yoziladi, pack config‘da yashaydi) amalda
sinaydi: O‘zbekiston bozoridagi CRM tizimlarining aksari (Modme, Billz, MoySklad,
YClients, Jowi, Poster, mijozning o‘z ichki tizimi) uchun **alohida Python yozilmaydi** —
operator HTTP shartnomasini deklaratsiya qiladi, core uni bajaradi.

Production qarori o‘zgarmaydi: **NO_GO**. Live provider acceptance bajarilmagan.

## Nima qo‘shildi

### 1. `platform_runtime/crm/onec_adapter.py` — 1C:Enterprise

1C buxgalteriya va ulgurji savdoning 70%+ qismini egallaydi, lekin uning HTTP servisi
**saytga xos** shakl qaytaradi: maydon nomlari kirillcha (`Статус`, `Наименование`),
javob `result.rows` yoki yalang‘och massiv bo‘lishi mumkin, yozuv `Ref_Key` bilan
identifikatsiya qilinadi.

| Xususiyat | Qaror |
|---|---|
| Auth | `basic` (default, `basic_auth_env` = `user:password`) yoki `bearer`; `credential_env` umumiy nom sifatida qabul qilinadi |
| Destination | `https://{host}{base_path}` — host `allowed_hosts`da, `base_path` `safe_relative_path` bilan tekshiriladi |
| Javob shakli | `response_map` orqali; `created_id` alohida pointer, chunki yaratish javobi o‘ralgan (`result.Ref_Key`), ro‘yxat qatori esa tekis (`Ref_Key`) |
| Status | `ONEC_STATUS_MAP` faqat tasdiqlangan holatlarni kanoniklashtiradi; noma'lum holat `''` + `provider_status`da saqlanadi |
| Identifikatorsiz yozuv | `Conflict` — 2xx identifikatorsiz qaysi yozuv yaratilganini isbotlamaydi |

### 2. `platform_runtime/crm/custom_http_adapter.py` — operator-deklaratsiya qilgan HTTP CRM

Bu adapter agentga ochiq bo‘lgani uchun **umumiy request primitivi bo‘lib qolmasligi**
shart. Shuning uchun uchta chegara qo‘yildi:

1. **Destination faqat operator konfiguratsiyasi.** Na tool argumenti, na pack qiymati, na
   model javobi scheme/host/port/absolute URL bera oladi. `safe_relative_path` `//`,
   `..`, `\`, `:`, `#`, bo‘shliq va control belgilarni rad etadi.
2. **Placeholder har doim percent-encoded.** `build_path` `quote(text, safe='')` ishlatadi,
   ya'ni `x&limit=999&path=/../admin?` kabi qiymat URL tuzilishini o‘zgartira olmaydi.
   Faqat allowlist'dagi nomlar (`query, phone, email, lead_id, contact_id, limit,
   minutes, since`) qabul qilinadi.
3. **Method va body allowlist.** `GET/POST/PUT/PATCH`; `DELETE/TRACE/CONNECT/OPTIONS`
   rad etiladi. Body faqat deklaratsiya qilingan kalitlardan iborat bo‘ladi va har bir
   qiymat validatsiya qilingan maydonga ishora qilishi shart.

Credential `headers` ichida environment reference sifatida beriladi:
`{"Authorization": {"env": "SET_CUSTOM_CRM_TOKEN", "prefix": "Bearer "}}`. `Host`,
`Content-Length`, `Connection`, `Transfer-Encoding` sarlavhalarini override qilish rad
etiladi.

### 3. `crm.lead.stalled` — re-engagement feed

CRM ekotizimi hujjatidagi (§4.1) ikkita avtonom ssenariy — javobsiz qolgan lid va muddati
kelgan qayta qo‘ng‘iroq — uchun **read-only** feed. Feed hech narsa yozmaydi: qaytarilgan
lid oddiy, approval-gated step'ga aylanadi.

Muhim qaror: provayder xatosi `Conflict` bo‘ladi, bo‘sh natija emas. Aks holda
"provayder ishlamayapti" va "javobsiz lid yo‘q" bir xil ko‘rinardi va follow-up sikli
jimgina to‘xtab qolardi.

### 4. Umumiy routing va reconcile

- `IMPLEMENTED_CRM_DRIVERS` — adapteri bor driverlar. `modme/billz/moysklad/retailcrm/
  yclients/jowi/poster` hali ham `adapter_required` va **named error** beradi
  (`CRM driver X has no executable adapter yet`), "unsupported" bilan aralashmaydi.
- `search_mode` / `find_leads_by_mode` / `find_contacts_by_mode` — contract'da. Ilgari
  gateway va reconciler har biri o‘z `if driver == ...` shoxobchasini yozardi; endi bitta
  qoida.
- `describe_crm` endi **rostini** aytadi: `transport_implemented` faqat haqiqiy adapter
  uchun `True`, `live_verified` doim `False`.

## Test topgan haqiqiy nuqson

`find_leads_by_mode`ni contract'ga ko‘chirish paytida **reconciler**dagi eski shoxobcha
qolib ketdi. Test buni ushladi: `onec` va `custom_webhook` uchun `matches` doim `[]`
bo‘lardi, ya'ni **yozish mumkin, lekin reconcile qilib bo‘lmaydigan** driverlar paydo
bo‘lardi — `uncertain` step abadiy osilib qolardi. Tuzatildi va 5 test bilan qoplandi
(`test_onec_positive_readback_settles_step` va hk).

Ikkinchi topilma: `dig()` faqat ASCII segmentlarni qabul qilardi, 1C'ning kirillcha
maydon nomlari (`Статус`) esa rad etilardi. `POINTER_SEGMENT_RE = [\w-]{1,64}` bilan
tuzatildi — struktura belgilari (`. [ ] *` quote, bo‘shliq) baribir taqiqlangan.

## Dalil

| Guruh | Test |
|---|---:|
| `test_onec_adapter.py` | 31 |
| `test_custom_http_adapter.py` | 30 |
| `test_new_crm_drivers.py` | 17 |
| Jami yangi | **78** |

`python -m unittest discover -s runtime_tests`:

| | Oldin | Keyin |
|---|---:|---:|
| Topilgan test | 919 | **997** |
| Error | 150 | **150** |
| Failure | 1 | **1** |

Ya'ni 78 yangi test qo‘shildi va mavjud holat **o‘zgarmadi**. Qolgan 151 muammoning
hammasi platforma-intrinsik (138 `cryptography` yo‘q, qolgani POSIX-only xavfsizlik
kontrakti) — `docs/development/LOCAL-VERIFICATION-UZ.md`da batafsil.

Mavjud CRM, connector va pack-contract guruhlari regressiyasiz: `test_crm_contract`,
`test_crm_gateway`, `test_crm_reconcile`, `test_bitrix24_adapter`, `test_kommo_adapter`,
`test_pack_contract`, `test_connector_contract`, `test_connector_authority` — 91/91 PASS.
Dependency-backed HTTP: `test_cors_and_retirement_http` + `test_http` — 14/14 PASS.

To‘liq offline harness (`scripts/verify_offline.py`): `release_tools` PASS (3),
`manifest_tools` PASS (6), `sqlite_demo` PASS, `managed_database_demo` PASS, uchta browser
suite PASS (40), `python_syntax` PASS.

## Konfiguratsiya namunasi

`config/crm-1c-custom.example.json` — ikkala driver uchun to‘liq ishlaydigan shakl,
`SET_...` qiymatlar bilan. Credential'lar faqat env nomi sifatida.

## Chegaralar (yashirilmaydi)

1. **Live acceptance yo‘q.** 1C'ning haqiqiy HTTP servisi, custom endpoint, OAuth token
   yangilanishi, rate-limit xatti-harakati tekshirilmagan. Adapterlar
   `configured_not_live_verified`.
2. **1C OData varianti qo‘shilmagan.** Faqat operator e'lon qilgan HTTP service. Avtomatik
   `$metadata` discovery yo‘q — bu ataylab, chunki metadata o‘qish ham tarmoq siyosati.
3. **Re-engagement loop hali ulanmagan.** Feed bor, lekin uni scheduler + AgentLoop +
   approval bilan bog‘lash keyingi blok (`crm_reengagement_loop`).
4. **`crm.lead.stalled` Bitrix24 va Kommo‘da yo‘q** va bu aniq `Forbidden` beradi
   ("declares no stalled-lead feed") — jimgina bo‘sh ro‘yxat emas.
5. **Kirillcha status mapping to‘liq emas.** Faqat e'lon qilingan holatlar; mijozning
   o‘z holat nomlari `provider_status`da saqlanadi va kanonik `''` bo‘lib qoladi.
6. **Body shabloni faqat tekis kalitlar.** `create_lead.body` ichma-ich struktura
   yasay olmaydi (masalan massiv ichida obyekt). Murakkab shakl kerak bo‘lsa, provider
   uchun kichik adapter yozish to‘g‘riroq yo‘l.

## Keyingi qadam

`crm_reengagement_loop`: `crm.lead.stalled` natijasini scheduled AgentLoop run'ga ulash,
outreach'ni approval-gated qilish, dedup (bir lidga ikki marta yozmaslik) va
`p_schedule_owners` orqali owner bilan bog‘lash.
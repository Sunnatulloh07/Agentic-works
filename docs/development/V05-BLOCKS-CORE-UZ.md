# v0.5 blok hisobotlari — yadro (graf, nazorat, aktivlar)

Business Graph, agent nazorati, qoralama, ishchi kuchi, router va aktiv modeli. Har bir blok o'z davrida yozilgan; matni **o'zgartirilmagan**, faqat sarlavha darajalari birlashtirildi.

## Mundarija

- [V05 — v0.5 implementatsiyasi — Business Graph (P1)](#v05)
- [V05B — v0.5b implementatsiyasi — Business Graph auditi va Agent oversight (P9)](#v05b)
- [V05C — v0.5c implementatsiyasi — Briefing (P10)](#v05c)
- [V05D — v0.5d implementatsiyasi — Workforce oversight (P9b)](#v05d)
- [V05E — v0.5e implementatsiyasi — Supervisor router (P10b)](#v05e)
- [V05F — v0.5f implementatsiyasi — Asset model (P11)](#v05f)
- [V05G — v0.5g implementatsiyasi — Vision events (P11b)](#v05g)

---

<a id="v05"></a>

## v0.5 implementatsiyasi — Business Graph (P1)

Blok: `business_graph`. Holat: **LOCAL_CONTRACT_TESTED**.
Tadqiqot va yo'l xarita: `docs/prd-v05/00-ENTERPRISE-OPERATING-LAYER-PRD-UZ.md`.

Bu hujjat nima **yozildi**, qaysi **chegaralar** qo'yildi va qaysi **nuqsonlar**
ushlandi — o'shani yozadi. Reja emas, hisobot.

---

### 1. Nima qo'shildi

`api-python/platform_runtime/business_graph.py` — mijozning barcha tizimlaridan
yig'ilgan, **faqat o'qish uchun**, tenant-scoped yagona ko'rinish.

Olti tool, **hammasi `read`**:

| Tool | Vazifa |
|---|---|
| `graph.entities` | E'lon qilingan entity/atribut/manba shakli |
| `graph.entity` | Bitta entity: har atribut `values` + `selected` + `conflict` |
| `graph.search` | Atribut qiymati bo'yicha id qidirish |
| `graph.timeline` | Bir entity uchun kross-tizim faoliyati |
| `graph.conflicts` | Ikki tizim kelishmaydigan joylar |
| `graph.explain` | Bitta atribut ortidagi hamma kuzatuv |

Konfiguratsiya operator tomonida (`config/business-graph.example.json`):

```json
"business_graph": {
  "conflict_policy": "report",
  "entities": {
    "product": {
      "identity": "sku",
      "priority": ["erp", "finance"],
      "sources": {
        "erp": {
          "tool": "connectors.read", "key": "sku",
          "args": {"connection": "erp", "table": "products",
                   "columns": ["sku", "price", "stock"]},
          "map": {"price": "price", "stock": "stock"}
        },
        "finance": {
          "tool": "sheets.rows", "key": "SKU",
          "args": {"register": "finance", "range": "margins"},
          "map": {"price": "Narx", "margin": "Foyda"}
        }
      }
    }
  }
}
```

---

### 2. To'rtta chegara (nima uchun bu xavfsiz)

#### 2.1 Manba — faqat mavjud read tool

`SAFE_SOURCE_TOOLS = {connectors.read, sheets.rows, sheets.read, database.read}`.
Boshqa har qanday tool **rad etiladi**. Sabab: agar `telegram.send` yoki
`database.write` manba bo'la olsa, "o'qish modeli" **yashirin yozuv yo'liga**
aylanadi va bizning butun approval zanjiri chetlab o'tiladi.

Bu `test_write_tool_cannot_back_a_source` bilan qulflangan (3 ta yozuv tool'i
sinovdan o'tkaziladi).

#### 2.2 Vakolat meros qilinmaydi

`preflight()` **har bir** manba uchun, **har qanday provider I/O dan oldin**:

1. manba tool'i agentning `tools` ro'yxatida bormi;
2. `connectors.read` / `database.read` bo'lsa, `connection`
   `allowed_connections` da bormi;
3. operator argumentlari tool sxemasidan o'tadimi.

Muhim oqibat: **qisman o'qish imkonsiz**. Ya'ni "ERP o'qildi, Sheets ruxsat
bermadi" holati bo'lmaydi — chunki ruxsat bo'lmasa **hech bir manba** o'qilmaydi.
Busiz chaqiruvchi **nosozlikni** **vakolat xatosidan** ajrata olmasdi.

Uch test buni qo'riqlaydi:
`test_missing_source_tool_denies_before_any_provider_read`,
`test_disallowed_connection_denies_before_any_provider_read`,
`test_graph_tool_must_be_held_by_the_agent`.

#### 2.3 Ziddiyat jimgina hal qilinmaydi

```json
"attributes": {
  "price": {
    "values": [
      {"value": 450000, "source": "erp", "observed": 1726745000},
      {"value": 462000, "source": "finance", "observed": 1726745000}
    ],
    "conflict": true,
    "selected": null
  }
}
```

| `conflict_policy` | Xatti-harakat |
|---|---|
| `report` (default) | Barcha kuzatuvlar; `selected: null` |
| `primary_wins` | `priority` tartibi bo'yicha tanlanadi; **ziddiyat baribir qaytariladi** |
| `newest_wins` | **rastratsiya rad etiladi** |

`newest_wins` nega rad etiladi: `observed` — bu **bizning o'qish vaqtimiz**,
provider'ning yangilanish vaqti emas. Uni "eng yangi" deb ko'rsatish **yolg'on
bo'lardi**, shuning uchun modul uni ochiq xato bilan rad etadi va sababini
yozadi (`test_newest_wins_is_refused_rather_than_faked`).

#### 2.4 Manba xatosi "ma'lumot yo'q" degani emas

| Holat | Javob |
|---|---|
| Manba ishladi | `sources[].read = true`, qiymatlar bor |
| Manba ishlamadi | `source_errors = [{source, error}]`, `complete = false` |
| **Vakolat** yo'q | **`Forbidden` qayta ko'tariladi** (`source_errors`ga yozilmaydi) |

`error` maydonida **faqat xato sinfi nomi** bor (`'OSError'`, `'SheetsError'`),
**matni emas**: provider xabari range, spreadsheet id yoki ichki URL echo
qilishi mumkin.

---

### 3. Ushlandan haqiqiy nuqsonlar

| # | Nuqson | Qanday topildi | Nima o'zgardi |
|---|---|---|---|
| 1 | Sonli matn (`'450000'`) va son (`450000`) **ziddiyat** deb ko'rsatilardi | Test yozilganda: bir manba Sheets, bir manba SQL — har biri o'z tipida qaytaradi | `canonical()` qo'shildi: sonli matn songa aylantiriladi; **boshqa** matn case-fold qilinadi. Ya'ni `'Ali Valiyev' == 'ali valiyev'`, lekin `450000 != 462000` |
| 2 | Boshlang'ich `_require` funksiyasi `preflight`dan tushib qoldi → `graph.entities` `NameError` berardi | Registrni `build_registry()` bilan yig'ib ko'rilganda | `_require` ajratildi va tool qatlami uchun yagona vakolat tekshiruvi qilib qo'yildi |
| 3 | Test kutgan xato sinfi `SheetsError`, aslida `OSError` chiqdi | `test_a_failing_source_...` birinchi yurishida yiqildi | Kod **to'g'ri** edi (xato sinfi halol ko'rsatilgan); test tuzatildi va **xato matni oshkor bo'lmasligi** ham alohida tekshirildi |
| 4 | `graph.entities` manba `args`ini (connection, table, ustunlar) qaytarib yuborardi | `test_entities_returns_shape_without_connection_...` | Faqat `{source, tool}` qoladi; spreadsheet id va jadval nomi model javobida **umuman yo'q** |
| 5 | Atributlar tartibi tasodifiy edi (dict tartibi) | `test_entities_...` yiqildi | `attributes` **saralanadi**, shuning uchun model javobi barqaror |
---

### 4. Testlar

`api-python/runtime_tests/test_business_graph.py` — **38 test**, 15 subtest.
Haqiqiy `Engine`, haqiqiy `sqlite_readonly` konnektor, haqiqiy Sheets registri;
faqat **provider GET** skript bilan almashtirilgan (`sheets._http_get`).
Ya'ni HTTP va tarmoq **ishlatilmadi**, lekin SQLite haqiqiy fayl.

| Guruh | Soni | Nima tekshiriladi |
|---|---:|---|
| Ro'yxatga olish | 4 | Barcha tool `read`; `graph.*` registri dublikat qo'riqchisi; e'lon qilinmagan graph bo'sh; nomlar saralangan |
| Konfiguratsiya | 8 | Noma'lum kalitlar; `newest_wins`; yozuv tool'i manba bo'lmasligi; `priority` to'liqligi; ustun allowlist'idan tashqari maydon; atribut identifikatori; operator args majburiyligi; e'lon qilinmagan entity |
| Vakolat | 4 | Graph tool'i agentda; manba tool'i yetishmasligi; connection ruxsati; e'lon qilinmagan atribut |
| Yechish | 11 | `value`+`source`+`observed`; `report` tanlamaydi; `primary_wins` tanlaydi va baribir xabar beradi; sonli matn; `canonical`; `'1042'` vs `1042.5`; kalitsiz qator; manba statistikasi; id tipi `str` |
| Halollik | 4 | Manba xatosi nomlanadi; transport xatosi qiymatga aylanmaydi; **vakolat xatosi ko'tariladi**; xato sinfi |
| Qidiruv/ziddiyat/izoh | 10 | `graph.search` moslik/tartib/limit; `graph.conflicts`; `graph.timeline` provider tartibi (xronologiya **da'vo qilmaydi**); `graph.explain` `declared_by`/`observed_by`/`missing_from`; `graph.entities` shakli sizib chiqmasligi |

Eng muhim uchta test:

1. `test_missing_source_tool_denies_before_any_provider_read` —
   `transport.calls == []` bilan **hech bir manba o'qilmaganini** isbotlaydi.
2. `test_conflict_is_reported_and_nothing_is_selected_under_report` —
   `selected is None` va ziddiyat ro'yxatining **aniq** shakli.
3. `test_timeline_preserves_provider_order_and_does_not_claim_chronology` —
   `order == 'provider'`; matnli sanani saralab "xronologiya" deb ko'rsatish
   **yolg'on** bo'lardi (`10.01.2026` < `09.02.2026`).

### 5. Chegaralar (halol ro'yxat)

| Chegara | Sabab |
|---|---|
| `graph.timeline` xronologik **emas** | Provider matnli sanani parse qilmasdan saralash noto'g'ri tartib beradi |
| Id mosligi **satr bo'yicha** | `1042` (butun son) va `'1042'` (matn) bir id; haqiqiy moslashtirish operator `map`i ishi |
| Maksimal 200 qator/skan, 100 moslik | Bounded read; undan kattasi staging talab qiladi |
| Faqat `connectors.read` + Sheets + `database.read` sinxron o'qish | Provider pagination hali ishlatilmaydi |
| Ziddiyat **aniqlanadi**, **hal qilinmaydi** | `report` — ataylab. `primary_wins` — operator qarori |
| Yozuv yo'li **yo'q** | Kross-tizim yozuv per-source ownership + approval + reconcile talab qiladi |
| Live provider acceptance **NOT_RUN** | Google Sheets, DB, 1C, MoySklad jonli sinovdan o'tkazilmagan |
| React UI **yo'q** | Faqat tool qatlami |

### 6. Keyingi qadamlar (tartib bilan)

1. `briefing` (P10) — `reengagement` bilan bir xil koordinator shakli:
   jadval → cheklangan o'qish (graph orqali) → dedup → approval-gated yozuv.
   Brifing uchun **yozuv** faqat xabar, ya'ni `telegram.send`.
2. `agent_oversight` (P9) — `p_audit`, `p_agent_runs` va `usage_budget` ustidan
   uchta **read** tool. Agentni **o'zgartirmaydi**.
3. `asset_model` (P11) — `business_graph`ning `asset` entity'si: UNS
   ierarxiyasi (`zavod/sex/liniya/stanok`) `identity` bo'ladi. Kamera hodisasi
   (P11b) shunga bog'lanadi.
4. `whatsapp_channel` (P8) — 24 soatlik oyna **o'qilishi** (`whatsapp.window`),
   yuborish **approval** bilan; shablon operator konfiguratsiyasi.

Tadqiqot tafsilotlari va yetti qatlamli arxitektura:
`docs/prd-v05/00-ENTERPRISE-OPERATING-LAYER-PRD-UZ.md`.


---

<a id="v05b"></a>

## v0.5b implementatsiyasi — Business Graph auditi va Agent oversight (P9)

Bloklar: `business_graph` auditi (P1) va `agent_oversight` (P9).
Holat: **LOCAL_CONTRACT_TESTED**. Yo‘l xarita:
`docs/prd-v05/00-ENTERPRISE-OPERATING-LAYER-PRD-UZ.md`.

Bu hujjat reja emas, hisobot: nima **yozildi**, qanday **nuqsonlar** ushlandi
va qaysi **chegaralar** ochiq qoldi.

---

### 1. Business Graph auditi — bitta haqiqiy nuqson

`business_graph.py` ning o‘zi (38 test, oltita read tool, to‘rtta chegara)
**to‘g‘ri** chiqdi. Lekin kodni **o‘qib** chiqqanimda bitta ishlash nuqsoni
topildi — u mavjud testlar **ushlay olmagan**, chunki testlar bitta id bilan
yurardi.

#### Nuqson: N+1 provider amplifikatsiyasi

`search()` va `conflicts()` har bir topilgan id uchun `resolve()` chaqirardi:

```python
for entity_id in ids:
    view = resolve(engine, tenant, agent, entry, entity_id, step)   # <-- har safar
```

`resolve()` esa ichida **`preflight()` + manbani to‘liq o‘qishni** qaytadan
bajaradi. Ya’ni o‘qish **id soniga chiziqli** o‘sardi.

**O‘lchandi** — `scripts/probes/probe_graph_amplification.py`, haqiqiy `Engine` va
haqiqiy test harness (`test_business_graph.RecordingTransport`) ustida:

| Chaqiruv | Id | Provider GET |
|---|---:|---:|
| `graph.entity` | 1 | 1 |
| `graph.conflicts` | 8 | **9** |
| `graph.search` (1 moslik) | — | 1 |

`9 = 1 skan + 8 per-id re-read`. 100 id × 4 manba bo‘lsa **~400 GET** — bu
kvota va xarajat nuqtai nazaridan jiddiy.

#### Tuzatish

Uchta yangi yordamchi qo‘shildi:

- `_collect_all(engine, tenant, agent, entry, step)` — **har manbani aynan bir
  marta** o‘qib, kuzatuvlarni `by_id` savatlariga ajratadi; `status`, `errors`,
  `now` ni ham qaytaradi.
- `_view_from(entry, entity_id, observations, conflict_policy, ...)` — savatdan
  bitta entity ko‘rinishini yasaydi (avval `resolve()` qilgan ishning sof,
  I/O siz qismi).
- `identifiers(..., collected=None)` — oldindan hisoblangan `_collect_all`
  natijasini qabul qiladi va **ikkinchi o‘qishni** oldini oladi.

`search()` va `conflicts()` endi `_collect_all` + `_view_from` ustida ishlaydi.

#### Natija

| Chaqiruv | Oldin | Keyin |
|---|---:|---:|
| `graph.entity` | 1 GET | 1 GET |
| `graph.conflicts` (8 id) | 9 GET | **1 GET** |
| `graph.search` | 1 GET | 1 GET |

Chiqish **tartibi va shakli o‘zgarmadi** — bu `search`/`conflicts` natijasini
per-id yo‘l bilan **solishtiruvchi** test bilan qulflandi.

#### 6 yangi regressiya testi (`test_business_graph.py` 38 → 44)

| Test | Nima qulflanadi |
|---|---|
| `test_scanning_tools_read_each_source_once_regardless_of_id_count` | 8 id uchun GET soni id soniga bog‘liq **emas** |
| `test_a_single_entity_read_still_issues_one_provider_get` | Bitta entity yo‘li saqlanib qoldi |
| `test_search_and_conflicts_agree_with_a_per_id_read` | Natija per-id yo‘l bilan **bir xil** |
| `test_conflicts_still_reports_the_same_disagreement_after_batching` | Batching ziddiyat semantikasini buzmaydi |
| `test_a_failing_source_is_reported_once_after_batching` | Xato manba **bir marta** nomlanadi, qolgani o‘qiladi |
| `test_batched_ids_keep_the_declared_source_priority_order` | `priority` tartibi saqlanadi |

---

### 2. Agent oversight (P9) — yangi modul

`platform_runtime/oversight.py`. Uchta tool, **hammasi `read`**:

| Tool | Vazifa |
|---|---|
| `agent.activity` | Vazifa/run soni holat bo‘yicha + `p_tasks` orqali bog‘langan hodisalar |
| `agent.cost` | Tenant budjet oynasi, ledger, in-flight; **halol** atributsiya |
| `agent.health` | Oxirgi run, `runs_by_status`, `failure_rate`, kutilayotgan ish, kill switch |

#### 2.1 Yozuv yo‘li — yo‘q (siyosat emas, tuzilma)

Modulda `write` funksiyasi **umuman mavjud emas**. Sabab: agent o‘z vakolatini
o‘zgartira olsa, **audit zanjiri ma’nosiz bo‘ladi** — kim o‘zgarganini
isbotlashning yo‘li qolmaydi. Shuning uchun “read-only” bu yerda hujjatdagi
va’da emas, kodning tuzilishidan kelib chiqadigan fakt.

Ro‘yxatga olishda uchala tool ham `read` risk sinfi bilan qo‘shiladi va bu
test bilan tekshiriladi.

#### 2.2 Hodisalarni birlashtirish — `p_tasks.agent`, `LIKE` emas

Birinchi variantim `p_audit.data` ustida `LIKE '%agent%'` edi. **Rad etildi:**
`sales` bo‘lagi `sales.order_taker` hodisalarini **meros qilib olardi** — bu
jimgina noto‘g‘ri raqam. To‘g‘ri yo‘l — `p_tasks` jadvalidagi `agent` ustuni
orqali **aniq** bog‘lash.

#### 2.3 Xarajat — yolg‘on aniq raqamdan halol indisga

`p_budget_reservations.request_key` **agent nomini saqlamaydi** (tekshirildi:
kalit `model:<uuid>` yoki chaqiruvchi kaliti). Shuning uchun:

- javobda `attribution: 'agent_run_window'` deb **ochiq** yoziladi;
- per-agent `spent_micro` **qaytarilmaydi** (u to‘qilgan raqam bo‘lardi);
- o‘rniga `tenant_spent_micro`, `tenant_ledger_by_status`,
  `tenant_inflight_micro`, `budget_configured`.

#### 2.4 “O‘qilmadi” ≠ “nol”

- `health()` da `not_recorded` ro‘yxati bor (masalan `'budget'`) — ko‘rsatib
  bo‘lmaydigan metrika uchun `0` berish **yolg‘on tasalli**.
- `failure_rate` **tugagan run bo‘lmasa `None`**, `0.0` emas.
- `agent.cost` va `agent.health` e’lon qilinmagan agentni **rad etadi**.

#### 2.5 Konfiguratsiya

`config/agent-capabilities.example.yaml` ga `mgmt.agent_supervisor` misoli
qo‘shildi: uchta oversight tool + chegara izohi. Bu operator uchun
**hujjatlashtirilgan qaror** — “nazoratchi agent agentni o‘zgartira olmaydi”.

---

### 3. Testlar

| Fayl | Oldin | Keyin |
|---|---:|---:|
| `runtime_tests/test_business_graph.py` | 38 | **44** |
| `runtime_tests/test_oversight.py` | — | **33** |
| `runtime_tests` jami | 1089 | **1128** |

`test_oversight.py` haqiqiy `Engine` + haqiqiy SQLite qatorlari ustida yur:
ro‘yxatga olish va read-only, e’lon qilinmagan agentni rad etish, id shakli,
tarix yo‘qligi, vazifa/run sonini **ajratish**, hodisa join’ining to‘g‘riligi,
oyna va limit chegarasi, truncation, xarajat atributsiyasi halolligi,
in-flight/spent ajratmasi, `failure_rate`, `not_recorded`, kill switch,
credential sizib chiqmasligi.

`scripts/probes/probe_graph_amplification.py` — N+1 ni **o‘lchaydigan** diagnostika
skripti (tuzatishdan keyin ham regressiyani tutish uchun qoldi).

`config/agent-capabilities.example.yaml` — 5 agent, **noma’lum tool yo‘q**.

---

### 4. Halol chegaralar

| Chegara | Sabab |
|---|---|
| Per-agent xarajat **yo‘q** | `request_key` agentni saqlamaydi; to‘qish yolg‘on bo‘lardi |
| Oversight **UI yo‘q** | Faqat tool qatlami |
| Live acceptance **NOT_RUN** | Jonli provayder/DB sinovi o‘tkazilmagan |
| Windows hostda `cryptography` yo‘q | 138 vault/OAuth testi lokal yurmaydi (loyiha tan olgan cheklov) |
| 13 POSIX-only test yiqiladi | `test_portable_fs` + Node runner + `test_macos_bundle` — ataylab |

---

### 5. Keyingi qadam

1. `briefing` (P10) — `reengagement` bilan bir xil koordinator shakli: jadval →
   cheklangan o‘qish (**endi `business_graph` orqali**) → dedup →
   approval-gated yetkazish (yozuv faqat xabar). Brifing manbasiz bo‘sh bo‘ladi,
   shuning uchun P1 va P9 oldin kerak edi.
2. `workforce_oversight` (P9b) — davomat, smena, kechikkan ish; HR allaqachon
   Sheets’da. Xodimni **baholamaydi**, fakt ko‘rsatadi va menejerga eskalatsiya
   qiladi.
3. `asset_model` (P11) → `vision_events` (P11b) — **“tags emas, asset model”**:
   UNS ierarxiyasi (`zavod/sex/liniya/stanok`) `identity` bo‘ladi, kamera
   hodisasi shunga bog‘lanadi. **Frame platformaga kelmaydi.**


---

<a id="v05c"></a>

## v0.5c implementatsiyasi — Briefing (P10)

Blok: `briefing`. Holat: **LOCAL_CONTRACT_TESTED**.
Yo‘l xarita: `docs/prd-v05/00-ENTERPRISE-OPERATING-LAYER-PRD-UZ.md`.
Oldingi bloklar: `V05-BLOCKS-CORE-UZ.md` (P1), `V05-BLOCKS-CORE-UZ.md` (P1 audit + P9).

Bu hujjat nima **yozildi**, qanday **nuqsonlar** ushlandi va qaysi **chegaralar**
ochiq qoldi — o‘shani yozadi. Reja emas, hisobot.

---

### 1. Nima qo‘shildi

`api-python/platform_runtime/briefing.py` — jadval bo‘yicha ishlaydigan kunlik
brifing koordinatori. `reengagement` bilan **bir xil shakl**: jadval → cheklangan
o‘qish → dedup → yetkazish. Uchta ataylab qilingan farq:

#### 1.1 Manba — Business Graph, bitta feed emas

Brifing tabiatan **kross-tizim**: bugungi buyurtma CRM’dan, kam qolgan tovar
ERP’dan, marja kotib kiritgan Sheets’dan. Shuning uchun o‘qish `graph.search` va
`graph.conflicts` orqali ketadi. Bu avtomatik ravishda quyidagilarni beradi:

- har manba vakolati **har qanday provider I/O dan oldin** tekshiriladi;
- har qiymat **manbasi bilan** ko‘rinadi;
- ikki tizim kelishmasa — **e’lon qilinadi**, o‘rtacha hisoblanmaydi.

#### 1.2 Yetkazish — read + notify, approval **kerak emas**

Brifing deterministik ravishda **shu modulda** yig‘iladi, yagona yozuv esa
`telegram.send`. U mijozga emas, **operatorga** aytadi. Shuning uchun approval
qo‘yilmadi: brifingni approval ortiga yashirish operatorni approval navbatini
**refleks bilan tasdiqlashga** o‘rgatardi — bu esa approvalning butun ma’nosini
yo‘qotadi.

#### 1.3 Model ishtirok etmaydi

Brifing — **fakt hisoboti**. Uni modeldan o‘tkazish “tovar kam” degan gapni
qayta ifodalash qadamini qo‘shadi, u esa faqat aniqlikni yo‘qotishi mumkin.
Shuning uchun matn modulda yig‘iladi va testlar menejer **aynan oladigan
matnni** tekshiradi.

Konfiguratsiya operator tomonida:

```json
{
  "sections": [
    {"entity": "order", "attribute": "status", "equals": "new", "label": "Yangi buyurtma"},
    {"entity": "product", "conflicts": true, "label": "Tizimlar kelishmayapti"}
  ],
  "hour": 8, "timezone_offset_minutes": 300
}
```

---

### 2. Beshta chegara (nima uchun bu xavfsiz)

| Chegara | Nima qulflandi |
|---|---|
| Qabul qiluvchi — **operator konfiguratsiyasi** | Ma’lumot brifingni boshqa chat’ga **yo‘naltira olmaydi** |
| **O‘qilmagan manba e’lon qilinadi** | `complete: false` + bo‘lim nomi; “yozuv yo‘q” bilan aralashmaydi |
| Ziddiyat **fakt sifatida** | `conflict_policy` qarori graph qatlamida qoladi, brifing uni chetlab o‘tmaydi |
| Yozuv manbasi **mumkin emas** | `business_graph` faqat read tool’ni manba qilib oladi |
| Vakolat **har sikl** qayta tekshiriladi | Egasini revoke qilish yoki tenantni muzlatish yetkazishni to‘xtatadi |

Dedup kaliti — `(tenant, schedule, day)`, ya’ni **kun**. Brifing — kunlik
artefakt: worker qayta ishga tushsa ham bir kunda **bitta** brifing ketadi.

---

### 3. Ushlandan haqiqiy nuqsonlar

Bularning hammasi shu sessiyada, **testlar yozilayotganda** topildi va tuzatildi.
Har biri mening o‘z kodimdagi xato edi.

| # | Nuqson | Qanday topildi | Nima o‘zgardi |
|---|---|---|---|
| 1 | **`(0)` yolg‘oni**: o‘qib bo‘lmagan bo‘lim “0 yozuv” deb ko‘rsatilardi | Provider outage testi: `read_rows` yiqildi, lekin digest baribir `(0)` bilan keldi | `_section_rows` endi **uch holat** qaytaradi: `ok` / `partial` / `failed`. `failed` bo‘lsa **hech qanday son** chop etilmaydi — “bu bo‘lim to‘liq emas, «yozuv yo‘q» degani EMAS” |
| 2 | **`_render_row` crash**: ziddiyat qiymati `dict` bo‘lmasa `AttributeError` | `test_a_conflict_...` — graph `values` ichida `float` keldi | Har element `isinstance(item, dict)` bilan qo‘riqlanadi, aks holda `repr()` |
| 3 | **`tick` yolg‘on `True`**: bir kunda ikkinchi marta chaqirilganda ham “ish qildim” derdi va `next_due` ni **oldinga surardi** | `test_a_second_tick_...` — `assertFalse` yiqildi | Takroriy kun endi `False` qaytaradi va `next_due` **tegilmaydi**. Aks holda har restart ertangi brifingni kechiktirib boraverardi |
| 4 | Bo‘lim validatsiyasi graph deklaratsiyasiga bog‘lanmagan holda qolgan edi (o‘lik shart) | Kod o‘qilganda | Keraksiz shart olib tashlandi |
| 5 | `SPREADSHEET_ID_RE` 20 belgidan kam id’ni rad etadi; test konfiguratsiyasi juda qisqa edi | `sheets.rows` `ValueError` berdi | Test konfiguratsiyasi haqiqiy shaklga keltirildi |

**#1 va #3 eng muhimlari.** #1 — menejerga “0 yangi buyurtma” deb aytib, aslida
tizimga ulanmagan bo‘lish; #3 — brifingning har restart’da bir daqiqa kechikishi.

---

### 4. Testlar

`api-python/runtime_tests/test_briefing.py` — **44 test**, haqiqiy `Engine` va
haqiqiy SQLite ustida. ERP manbasi — **haqiqiy SQLite fayl**; faqat Sheets HTTP
hop’i va Telegram yuborish skript bilan almashtirilgan. Ya’ni graph preflight,
registr sxema tekshiruvi va ledger — hammasi **real** ishlaydi.

| Guruh | Soni | Nima tekshiriladi |
|---|---:|---|
| Ro‘yxatga olish | 2 | Yagona yozuv tool’i — notifier; jadval faqat owner uchun |
| Konfiguratsiya | 10 | Schedule id shakli; agent policy; noma’lum kalitlar; ro‘yxat emas; section soni cap; har knob chegarasi; `enabled` boolean; timezone; conflicts-section; attribute/equals majburiy |
| Sikl | 7 | Due bo‘lmaguncha yetkazmaslik; bitta digest; bo‘lim nomi+soni; bo‘sh bo‘lim **fakt**; ikkinchi tick takrorlamaydi; **worker restart** takrorlamaydi; keyingi kun yangi digest |
| Ledger | 3 | Natija yoziladi; faqat `queued` takrorlanadi; `queued` qayta claim qilinadi |
| Nosozlik | 6 | Outage “yozuv yo‘q” bo‘lmaydi; failed sikl **retire qilinmaydi**; partial nomlanadi; **o‘qilmagan bo‘lim son chop etmaydi**; denied → disable+sabab; muzlatilgan tenant |
| Halollik | 6 | Qabul qiluvchi konfiguratsiya; prompt injection fakt bo‘lib qoladi; ziddiyat ko‘rinadi; digest bounded; credential chiqmaydi; bo‘lim o‘qishlari ko‘paymaydi |
| Kun kaliti | 2 | Schedule timezone ishlatiladi; bir kun ichida barqaror |
| Dispatch | 4 | `enabled=False`; audit (delivered/configured); schedule’da credential yo‘q |

Eng muhim to‘rtta test:

1. `test_an_unreadable_section_never_prints_a_row_count` — `(0)` **yo‘q**.
2. `test_a_second_tick_on_the_same_day_does_not_deliver_again` — `False` va
   `next_due` tegilmagan.
3. `test_a_worker_restart_does_not_produce_a_second_digest` — dedup kaliti
   jarayonlar aro ishlaydi.
4. `test_the_recipient_is_configuration_and_never_data` — provider matni
   qabul qiluvchini o‘zgartira olmaydi.

`runtime_tests` 1128 → **1172 test**.

---

### 5. Integratsiya

| Joy | Nima o‘zgardi |
|---|---|
| `platform_runtime/engine.py` | `BRIEFING_SCHEMA` ro‘yxatga olindi (`reengagement` bilan bir xil yo‘l) |
| `app/worker.py` | Har tenant uchun `briefing.tick(tenant)` — `reengagement.tick` yonida |
| `app/platform_api.py` | `GET /{tenant}/briefing`, `PUT /{tenant}/briefing/{schedule}`, `GET /{tenant}/briefing/{schedule}/ledger` |
| `config/agent-capabilities.example.yaml` | `mgmt.briefer` misoli + chegara izohi |
| `scripts/check_capabilities_example.py` | Konfiguratsiyadagi tool nomlarini registrga solishtiruvchi yordamchi |

**`PUT` owner-only**, chunki bu **haqiqiy chat’ga** avtomatik yetkazishni
rejalashtiradi.

---

### 6. Halol chegaralar

| Chegara | Sabab |
|---|---|
| Brifing **model ishlatmaydi** | Fakt hisoboti; qayta ifodalash faqat aniqlik yo‘qotadi |
| Manba faqat **graph** orqali | Boshqa yo‘l vakolat tekshiruvini chetlab o‘tardi |
| Yetkazish faqat `telegram.send` | Boshqa kanal hali yo‘q (`whatsapp_channel` P8 da) |
| **UI yo‘q** | Faqat tool + API qatlami |
| Live provider acceptance **NOT_RUN** | Telegram jonli sinovdan o‘tgani yo‘q |
| Bo‘lim **soni 12**, qator 50 bilan cheklangan | Bounded o‘qish; undan kattasi staging talab qiladi |

---

### 7. Keyingi qadam

1. `workforce_oversight` (P9b) — davomat, smena, kechikkan ish. HR allaqachon
   Sheets’da, ya’ni yangi adapter kerak emas. Xodimni **baholamaydi**: fakt
   ko‘rsatadi va menejerga eskalatsiya qiladi.
2. `supervisor_router` (P10b) — menejer savoli to‘g‘ri bo‘lim agentiga
   yo‘naltiriladi. Chaqirilgan agent **o‘z** policy’si bilan tekshiriladi;
   supervisor vakolati **meros qilinmaydi** (UPA), zanjirga `max_steps` kerak.
3. `asset_model` (P11) → `vision_events` (P11b) — **“tags emas, asset model”**:
   UNS ierarxiyasi (`zavod/sex/liniya/stanok`) `identity` bo‘ladi. **Frame
   platformaga kelmaydi.**
4. `whatsapp_channel` (P8) — 24 soatlik oyna **o‘qiladi**, yuborish approval
   bilan.


---

<a id="v05d"></a>

## v0.5d implementatsiyasi — Workforce oversight (P9b)

Blok: `workforce_oversight`. Holat: **LOCAL_CONTRACT_TESTED**.
Yo‘l xarita: `docs/prd-v05/00-ENTERPRISE-OPERATING-LAYER-PRD-UZ.md`.
Oldingi bloklar: `V05-BLOCKS-CORE-UZ.md` (P1), `V05-BLOCKS-CORE-UZ.md`
(P1 audit + P9), `V05-BLOCKS-CORE-UZ.md` (P10).

Bu hujjat nima **yozildi**, qanday **nuqsonlar** ushlandi va qaysi **chegaralar**
ochiq qoldi — o‘shani yozadi. Reja emas, hisobot.

---

### 1. Nima qo‘shildi

`api-python/platform_runtime/workforce.py` — uchta **read-only** tool:
`workforce.attendance`, `workforce.shifts`, `workforce.workload`.

Menejer agentlar haqida so‘ragan uch savolni xodimlar haqida ham so‘raydi:
**kim ishda**, **kim ortiqcha yuklangan** va **nima muddatidan o‘tgan**. Javob
mijoz allaqachon yuritadigan HR ma’lumotidan olinadi.

#### 1.1 Yangi adapter **yo‘q** — Sheets registri qayta ishlatiladi

Davomat, smena va vazifalar bugun ham kotib yuritadigan jadval. Shuning uchun
`workforce` moduli o‘zining provider yo‘lini **ochmaydi**: u `sheets.rows`
tool’ini **oddiy handler orqali** chaqiradi:

```python
tool = engine.registry.get('sheets.rows')
tool.validate(args)
result = tool.handler(engine, tenant, agent, args, 'workforce')
```

Bu bitta qatordan o‘tish avtomatik ravishda quyidagilarni beradi — **hech biri
qayta yozilmagan**:

- registr **operator tomonidan e’lon qilingan** bo‘lishi shart;
- A1 oralig‘i **allowlist** ichida bo‘lishi shart;
- agent **o‘z** policy’sida `sheets.rows` ga ega bo‘lishi shart;
- connection allowlist tekshiriladi;
- qator va katak **chegaralangan** (`MAX_ROWS = 200`).

Ya’ni bu modul **yangi ma’lumot yo‘li qo‘shmaydi**. U faqat mavjud yo‘lni
ma’noli nomlar bilan o‘raydi.

#### 1.2 Ustunlar — operator konfiguratsiyasi, model emas

Model **view** nomini va chegara raqamini aytadi. Model **spreadsheet_id**,
**range** yoki **ustun nomini** ayta olmaydi. Ular operator tomonida e’lon
qilinadi:

```json
{
  "workforce": {
    "registers": {
      "attendance": {
        "register": "hr", "range": "attendance",
        "id_column": "id", "name_column": "Ism",
        "status_column": "Holat", "date_column": "Sana",
        "absent_statuses": ["yo'q", "ta'til"],
        "done_statuses": ["keldi"]
      }
    }
  }
}
```

To‘liq misol — `config/workforce.example.json`.

---

### 2. Asosiy chegara: **xodim baholanmaydi**

Bu modulning eng muhim qarori — **nima qilinmagani**. Faylda **ball, reyting,
indeks, samaradorlik foizi yoki xodimlar orasidagi taqqoslash yo‘q**. Bu
e’tiborsizlik emas, mahsulotning o‘z qamroviga yozilgan ataylab rad etish:

> "Xodimni baholamaydi yoki jazolash qarori qabul qilmaydi. U faktlarni
> ko‘rsatadi va menejerga eskalatsiya qiladi." — PRD v0.5 §8

Sabab odob emas, **xavfsizlik**: odamlarni saralaydigan raqam ertami-kechmi
jazolash uchun ishlatiladi, platforma esa buni hal qiladigan tomon emas. U kim
nima uchun kechikkanini, smena norasmiy yopilganini yoki shartnomada nima
yozilganini bilmaydi. Shuning uchun modul **son va o‘sha son ortidagi ismlarni**
qaytaradi va hukmni menejerga beradi.

Amalda bu shuni bildiradi:

| Qoida | Nima uchun |
|---|---|
| **`overdue` har doim qatorlari bilan birga** | Menejer indeksga ishonmasdan **sababni tekshira oladi** |
| `attendance` faqat **`absent` ro‘yxatini** qaytaradi, foiz emas | Foiz — baholash; ro‘yxat — fakt |
| `shifts` **yig‘indi hisoblamaydi** | “Samaradorlik” faqat platforma asossiz baholashi mumkin |
| `note` maydoni har javobda | Chegara **javobning ichida** turadi, hujjatda emas |

---

### 3. Olti chegara (nima uchun bu xavfsiz)

| Chegara | Nima qulflandi |
|---|---|
| **Yangi ma’lumot yo‘li yo‘q** | Har o‘qish `sheets.rows` orqali: registr, A1, tool va connection ruxsatlari **o‘zgarmagan holda** amal qiladi |
| **Yozuv yo‘li umuman yo‘q** | Modulda hech bir write tool yo‘q, uchta ham `read` |
| **Ustun nomi modeldan kelmaydi** | Model view va limit aytadi; spreadsheet, range va ustunni **faqat operator** e’lon qiladi |
| **O‘qilmagan registr e’lon qilinadi** | `complete: false` + `source_errors` (faqat exception klassi). “Hech kim yo‘q” bilan aralashmaydi |
| **Noma’lum status taxmin qilinmaydi** | `unknown_status` ro‘yxatida chiqadi — operator mapping’ni kengaytiradi |
| **Buzuq qator yashirilmaydi** | Identity ustuni bo‘sh qator `skipped` da sanaladi |

#### 3.1 Uchta “nol emas, `None`” qoidasi

Fakt hisobotida eng xavfli xato — **o‘qilmaganni nol deb ko‘rsatish**. Uch joyda
ataylab qaytarilgan:

1. **`_hours`** — bo‘sh yoki o‘qilmaydigan soat katagi `None` qaytaradi, `0`
   emas. Nol soat bilan “katak bo‘sh” boshqa-boshqa fakt, ikkinchisini
   birinchisi deb ko‘rsatish smenani **kamaytirib** ko‘rsatardi. `None`
   bo‘lganlar `unreadable_hours` da sanaladi.
2. **`_iso_day_text`** — `10.01.2026` kabi sana **o‘qilmaydigan** hisoblanadi va
   `unparsable_due` ga tushadi. O‘zbekistonda kun-birinchi, boshqa joyda
   oy-birinchi; taxmin qilish **noto‘g‘ri ishni muddati o‘tgan** deb belgilardi.
3. **`absent_statuses` bo‘sh bo‘lsa** — `attendance` **rad etadi**
   (`Forbidden`), “hech kim yo‘q” demaydi. Status ustuni o‘qilmasa, bo‘sh
   ro‘yxat va “hammasi ishda” bir xil ko‘rinardi.

---

### 4. Ushlandan haqiqiy nuqsonlar

Bular shu sessiyada, **testlar yozilayotganda** topildi. Biri mening kodimdagi
haqiqiy nomuvofiqlik, qolganlari testlarning o‘z xatosi edi.

| # | Topilgan | Nima o‘zgardi |
|---|---|---|
| 1 | **`MAX_ROWS = 500`** modulda, lekin `sheets` tool’ining shifti **200**. Ya’ni modul o‘zi hech qachon olmaydigan chegarani e’lon qilardi | `MAX_ROWS = 200` ga tenglashtirildi (izoh bilan): kattaroq chegara rad etishni **noaniqroq joyga** ko‘chirardi. Test konfiguratsiyasi ham 500→200 |
| 2 | `test_attendance_requires_a_status_column` `ValueError` kutardi, aslida **`Forbidden`** to‘g‘ri | Bo‘sh `status_column` — qonuniy (“majburiy emas”) belgi; **view** rad etadi, registr emas. Test `test_attendance_refuses_when_no_status_column_is_declared` ga o‘zgartirildi va `Forbidden` tekshiradi |
| 3 | Test connection nomi `hr`, registr esa `google` | Policy’ga `'google'` qo‘shildi — registr qaysi connection bilan e’lon qilinsa, **o‘sha** ruxsat talab qiladi |
| 4 | 3 workload testi yiqildi (`IndexError`, noto‘g‘ri son) | **Kod to‘g‘ri edi, fixture xato edi**: deterministik soat `1_770_000_000` = **2026-02-02**, mening sanalarim `2026-03-20` esa undan **keyin**. Muddati o‘tgan fixture’lar `2026-01-20` / `2026-01-05` ga tuzatildi |
| 5 | **`config/workforce.example.json` validator’dan o‘tmadi**: registr ichida `_comment` kaliti bor edi | `_register` noma’lum kalitni **rad etadi** (ataylab: ustun nomidagi xato bo‘sh ro‘yxatga o‘xshab ketardi). Izohlar registrdan **tashqariga** (`_note_*`) ko‘chirildi. `scripts/check_workforce_example.py` qo‘shildi — misol endi **modulning o‘z validator’idan** o‘tishi tekshiriladi |
| 6 | **`scripts/check_capabilities_example.py` faqat bitta katalogdan ishlardi**: `sys.path.insert(0, '.')` + `'../config/...'` | Root’dan yurganda `ModuleNotFoundError` berardi. Yo‘l `__file__` asosida hisoblanadigan qilindi — endi **root’dan ham, `api-python` dan ham** ishlaydi va noma’lum tool bo‘lsa `exit=non-zero` qaytaradi. **Ishlab bo‘lmaydigan yordamchi — tekshiruv emas.** |

**#1, #5 va #6 muhimlari.** #1 — modulning o‘z ichidagi nomuvofiqlik: e’lon
qilingan chegara amalda ishlaydigan chegaradan katta bo‘lsa, foydalanuvchi “200
tagacha o‘qiy olaman” deb o‘ylab, aslida **hech qachon** olmaydi. #5 — hujjat
misoli copy-paste qilinadigan narsa; validator rad etadigan misol operatorga
**ishlamaydigan shakl** o‘rgatardi. #6 — bu sessiyadan oldingi ishning nuqsoni
edi va faqat **ishga tushirib ko‘rilganda** ko‘rindi: “yozilgan” skript
“ishlaydigan” skript degani emas.

---

### 5. Testlar

`api-python/runtime_tests/test_workforce.py` — **32 test**, haqiqiy `Engine` va
haqiqiy SQLite ustida. Faqat `sheets.rows` handler’i skript bilan almashtirilgan;
registr deklaratsiyasi, policy tekshiruvi va registry — **hammasi real**.

| Guruh | Soni | Testlar |
|---|---:|---|
| Chegara (kod bilan) | 4 | `every_workforce_tool_is_read_only`, `the_module_exposes_no_write_path`, `the_output_contains_no_score_or_ranking`, `the_count_never_ships_without_the_rows_behind_it` |
| Attendance | 6 | E’lon qilingan statusdan yo‘qlik; noma’lum status **taxmin qilinmaydi**; identity’siz qator `skipped`; registr e’lon qilinmagan; **status ustuni bo‘lmasa rad etiladi**; o‘qilmagan registr bo‘sh ro‘yxat emas |
| Shifts | 3 | Soatlar satr bo‘yicha; **bo‘sh katak nol soat emas**; **yig‘indi/samaradorlik yo‘q** |
| Workload | 7 | Ochiq va kechikkan sanaladi; **noaniq sana `10.01.2026` taxmin qilinmaydi**; `done` hech qachon kechikkan emas; ochiq ish ham kechikkan emas; **kechikkan qatorlar “nima kechikkani”ni ko‘rsatadi**; `task_column` majburiy |
| Konfiguratsiya | 5 | Noma’lum kalit rad etiladi; registr nomi shakli; chegarasiz status ro‘yxati; `workforce` bloki yo‘qligi **xato emas** |
| Vakolat | 4 | Agent `sheets.rows` ga ega bo‘lishi shart; connection ruxsat etilishi shart; e’lon qilinmagan range rad etiladi; **chaqiruvchi range yoki spreadsheet ayta olmaydi** |
| Bound | 3 | `limit` chegaralangan; `absent_only` boolean; kesish (`truncation`) e’lon qilinadi |
| Halollik | 2 | Credential yoki provider URL chiqmaydi; **registr bir marta skanlanadi** |

Eng muhim ikkitasi:

1. `test_the_output_contains_no_score_or_ranking` — chegara **kod bilan**
   qulflangan, faqat izoh bilan emas.
2. `test_an_unread_register_is_not_an_empty_roster` — uzilish “hech kim
   yo‘q”ga aylanmaydi.

`runtime_tests` 1172 → **1204 test**.

---

### 6. Integratsiya

| Joy | Nima o‘zgardi |
|---|---|
| `platform_runtime/tools.py` | `register_workforce_tools(r)` — registr endi **49 tool** |
| `config/workforce.example.json` | **Yangi**: `attendance` / `shifts` / `workload` registr mappingi misoli + `_note_*` izohlari |
| `config/agent-capabilities.example.yaml` | `mgmt.hr_officer` misoli + “xodim baholanmaydi” chegarasi izohi |
| `scripts/check_workforce_example.py` | **Yangi**: misolni **modulning o‘z validator’idan** o‘tkazadi va bo‘sh blok xato emasligini tasdiqlaydi |
| `scripts/check_capabilities_example.py` | **Tuzatildi**: katalogdan qat’i nazar ishlaydi (`__file__` asosidagi yo‘l); noma’lum tool bo‘lsa non-zero exit |
| `README.md` | Lokal test buyrug‘i `-t runtime_tests` bilan tuzatildi (`-s` yolg‘iz yiqiladi) va test soni 1204 ga yangilandi |

---

### 7. Halol chegaralar

| Chegara | Sabab |
|---|---|
| **UI yo‘q** | Faqat tool qatlami; React ekrani yo‘q |
| **Eskalatsiya yo‘q** | Modul fakt beradi; menejerga **xabar yuborish** hali ulanmagan (`task_oversight_escalation` P6) |
| Live Google Sheets acceptance **NOT_RUN** | Jonli provider sinovi o‘tkazilmagan |
| Registr **200 qator** bilan cheklangan | Undan kattasi staging talab qiladi (sheets bilan bir xil chegara) |
| Ism 50, status 40 belgi | Bounded javob |
| Sana **faqat ISO** | Noaniq format taxmin qilinmaydi |

---

### 8. Keyingi qadam

1. `supervisor_router` (P10b) — menejer savoli to‘g‘ri bo‘lim agentiga
   yo‘naltiriladi. Chaqirilgan agent **o‘z** policy’si bilan tekshiriladi;
   supervisor vakolati **meros qilinmaydi** (UPA); zanjirga `max_steps` kerak.
2. `asset_model` (P11) → `vision_events` (P11b) — **“tags emas, asset model”**:
   UNS ierarxiyasi (`zavod/sex/liniya/stanok`) `identity` bo‘ladi. **Frame
   platformaga kelmaydi**; shaxsni aniqlaydigan hodisa — biometrik, `human_led`.
3. `whatsapp_channel` (P8) — 24 soatlik oyna **o‘qiladi**, yuborish approval
   bilan.
4. `task_oversight_escalation` (P6) — `workload` ning `overdue` chiqishini
   menejerga xabar qilish.


---

<a id="v05e"></a>

## v0.5e implementatsiyasi — Supervisor router (P10b)

Blok: `supervisor_router`. Holat: **LOCAL_CONTRACT_TESTED**.
Yo‘l xarita: `docs/prd-v05/00-ENTERPRISE-OPERATING-LAYER-PRD-UZ.md`.
Oldingi bloklar: `V05-BLOCKS-CORE-UZ.md` (P1), `V05-BLOCKS-CORE-UZ.md`
(P1 audit + P9), `V05-BLOCKS-CORE-UZ.md` (P10),
`V05-BLOCKS-CORE-UZ.md` (P9b).

Bu hujjat nima **yozildi**, qanday **nuqsonlar** ushlandi va qaysi **chegaralar**
ochiq qoldi — o‘shani yozadi. Reja emas, hisobot.

---

### 1. Nima qo‘shildi

`api-python/platform_runtime/supervisor.py` — rahbar savolini to‘g‘ri bo‘lim
agentiga yo‘naltiruvchi router.

Muammo: menejer kechikkan yetkazib berish savoli logistika agentiga, to‘xtab
qolgan lid savoli sotuv agentiga tegishli ekanini bilmaydi. U **bitta** savolni
o‘z so‘zlari bilan beradi. Modul shu savolni qaysi bo‘lim agenti javob berishini
hal qiladi va savolni topshiradi.

#### 1.1 Uchta ataylab qilingan qaror

**1. Yo‘naltirish — ruxsat berish emas, marshrut qarori.** Router maqsadni
tanlaganda faqat **bitta** ish qiladi: o‘sha maqsad uchun oddiy agent run yaratadi
(`AgentLoop.create` orqali). Maqsadning **o‘z** policy’si qaytadan o‘qiladi —
xuddi menejer unga to‘g‘ridan-to‘g‘ri murojaat qilgandek. Supervisor’ning
`tools`, `allowed_connections`, `ladder` va `allowed_recipients` maydonlari
**hech qachon** ko‘chirilmaydi, birlashtirilmaydi, kengaytirilmaydi.

**2. Yo‘naltirish — tool emas.** Run yaratish **control plane**da
(`POST /{tenant}/supervisor/route`), model tool’ida emas. Agar tool bo‘lganda,
`supervisor.route` ga ega har qanday agent o‘zi run yasay olardi va hop chegarasi
faqat promptga tayanib qolardi. Ro‘yxatda faqat ikkita **read** tool bor:
`supervisor.route` (xaritani ko‘rsatadi, run yaratmaydi) va `supervisor.sections`.

**3. Zanjir uch tomonlama chegaralangan** (quyida).

#### 1.2 Zanjir chegarasi — uch qavat

PRD v0.5 §7 (ochiq xatar 5): *"Agent-to-agent cheksiz zanjir — supervisor → agent
→ agent → … Bu `max_steps` bilan to‘silishi kerak."*

| Qavat | Nima qulflandi |
|---|---|
| **`max_hops` (standart 1)** | Bir maqsadga nechta delegatsiya bo‘lishi mumkin. **Ledger’dan sanaladi**, ya’ni jarayon qayta ishga tushsa ham tiklanadi va chaqiruvchi uni nolga qaytara olmaydi |
| **Router’ga yo‘naltirish rad etiladi** | O‘zi `supervisor.route` tutgan agentga yo‘naltirish standart holatda **`Forbidden`**. Aks holda `supervisor → supervisor → …` shunchaki boshqa supervisor nomini aytish bilan ochilardi |
| **Run yaratish tool emas** | Yuqorida: chegarani **kod** majburlaydi, prompt emas |

---

### 2. Asosiy xavfsizlik xossasi

Modul docstring’ida shunday yozilgan:

> *CRM o‘qiy olmaydigan supervisor, CRM o‘qiy oladigan agentga yo‘naltirib, o‘zi
> o‘sha ma’lumotni qo‘lga kiritolmaydi.*

Bu **docstring dalil emas**, shuning uchun `scripts/probes/probe_supervisor_authority.py`
buni o‘lchaydi. Probe eng keskin holatni yasaydi: hech qanday ma’lumot tool’i
tutmagan supervisor, `connectors.read` tutgan bo‘lim agentiga yo‘naltiradi.

```
supervisor tools      : ['supervisor.route', 'supervisor.sections']
routed to agent       : sales.360
target tools          : ['connectors.read', 'agent.activity']
supervisor tools after: ['supervisor.route', 'supervisor.sections']
supervisor connections: [] -> []
target using a tool it lacks -> refused: True

PROVEN: authority flows to the executor, not to the asker
```

Ikki tomonlama isbotlangan:

1. **Yo‘naltirish supervisorni kengaytirmaydi** — policy’si bayt-bayt bir xil
   qoladi (`tools` ham, `allowed_connections` ham).
2. **Teskari yo‘nalish ham to‘g‘ri** — maqsad **o‘zida yo‘q** tool’ni
   ishlatmoqchi bo‘lsa **rad etiladi**, supervisor nima tutganidan qat’i nazar.
   Ya’ni cheklov **bajaruvchiga** bog‘lanadi.

---

### 3. Yo‘naltirish qoidalari

**Keyword xaritasi — operator konfiguratsiyasi.** Model faqat **operator yozgan
ro‘yxatdan** tanlaydi; agent id, tool, connection yoki recipient **ayta olmaydi**
(`supervisor.route` sxemasi faqat `question` qabul qiladi — test bilan qulflangan).

Mos kelish qoidasi **deterministik**: eng uzun keyword yutadi, keyin e’lon
tartibi. Ataylab regex **emas** — operator so‘z yozadi, va xato kengaytirib
yubora olmaydi.

**Mos kelmasa — nom bilan rad.** Default agentga **yuborilmaydi**:
`status='unrouted', reason='no_section_matched'` ledgerga yoziladi va `Forbidden`
ko‘tariladi. Sabab: oylik haqidagi savolga sotuv agenti javob bergani javob
bermaslikdan **yomonroq**.

---

### 4. Ushlandan haqiqiy nuqsonlar

Bular shu sessiyada topildi va tuzatildi.

| # | Nuqson | Qanday topildi | Nima o‘zgardi |
|---|---|---|---|
| 1 | **`max_hops` bezak edi**: qabul qilinardi, tekshirilardi va hisobotda ko‘rinardi, lekin **hech narsani cheklamasdi** — kod har doim bitta run yaratardi | Kod qayta o‘qilganda: parametr *hujjatlashtirilgan, lekin amalga oshirilmagan* | `_depth()` qo‘shildi — ledger’dan maqsadga qilingan delegatsiyalar sonini sanaydi. `depth + 1 > max_hops` bo‘lsa `hop_cap_reached` bilan rad etiladi. Endi parametr **haqiqiy** |
| 2 | **Registrni ikki marta qurib bo‘lmaydi**: `build_registry()` `supervisor.*` ni allaqachon qo‘shadi, `register_supervisor_tools()` ni yana chaqirish `ValueError: Invalid tool registration` beradi | To‘liq to‘plam yurgizilganda error soni **149 → 201** bo‘ldi (aynan +52, ya’ni mening testlarim soni). Izolyatsiyada qayta ishlab ko‘rildi | `register_supervisor_tools` **idempotent** qilindi: mavjud tool qayta qo‘shilmaydi, xato bermaydi. `test_registration_is_idempotent` qo‘shildi |
| 3 | Modul `p_supervisor_section` jadvalini yaratardi, lekin `engine.py` da **ro‘yxatga olinmagan** edi | 43 test `sqlite3.OperationalError: no such table` bilan yiqildi | `SUPERVISOR_SCHEMA` `engine.py` ga qo‘shildi (`briefing` bilan bir xil yo‘l) |
| 4 | Test `with self.engine.tx()` ichida `freeze()` chaqirdi — `freeze` **o‘zi** transaction ochadi | `sqlite3.OperationalError: database is locked` | Ichki `tx()` olib tashlandi, izoh bilan: `BEGIN IMMEDIATE` ichida `BEGIN IMMEDIATE` qulf beradi |
| 5 | `probe_supervisor_authority.py` katalogdan qat’i nazar ishlamasdi | Ishga tushirilganda `ModuleNotFoundError` | Yo‘l `__file__` asosida; **uchinchi marta** shu xato — endi barcha yangi skriptlar shu naqshda yoziladi |

**#1 va #2 eng muhimlari.**

**#1** — bu “hujjatlashtirilgan, lekin bajarilmagan” nuqsoni: parametr mavjud,
validatsiya o‘tadi, javobda ko‘rinadi — va **hech nima qilmaydi**. Bu eng yomon
turdagi nuqson, chunki u ishlayotgandek ko‘rinadi. Testlar ushlamasdi, chunki
`max_hops` ni hech kim haqiqiy cheklov sifatida sinamagan edi. Shundan keyin
`max_hops` uchun **to‘rtta** test yozildi.

**#2** — va bu sessiyaning eng ibratli nuqsoni, chunki **men deyarli noto‘g‘ri
hisobot berdim**. To‘liq to‘plam `failures=1, errors=201` qaytardi. Oldingi
baseline 149 edi. Farq **aynan 52** — ya’ni mening yangi testlarim soni. Bu
“regressiya yo‘q” deb yozishdan oldin **to‘xtash** kerak bo‘lgan signal edi.

Sabab: `build_registry()` endi `supervisor.*` tool’larini o‘zi qo‘shadi
(`tools.py` ga ulangan), mening testim esa ustiga yana
`register_supervisor_tools(registry)` chaqirardi — va `Registry.add`
takroriy nomni **ataylab** rad etadi. Ya’ni xato registr qurishda emas, **ikki
marta qo‘shishda** edi.

Tuzatish ikkita qatlamda:

- **Test darajasida** shart qo‘yildi (mavjud bo‘lsa qayta qo‘shmaslik).
- **Modul darajasida** esa `register_supervisor_tools` **idempotent** qilindi.
  Bu muhimroq: `build_registry()` ni chaqirib keyin tool qo‘shish **normal
  naqsh**, va u `Invalid tool registration` bilan yiqilsa, xabar **haqiqiy
  sabab haqida hech nima demaydi**. Endi qayta qo‘shish — no-op.

Sabab izolyatsiyada tasdiqlandi: `test_supervisor` + `test_sheets` birga
yurgizilganda 52 error `Invalid tool registration` berardi; tuzatishdan keyin
ikkalasi ham yashil.

**Uslubiy saboq:** to‘liq to‘plam soni **o‘zgarmaganini da’vo qilishdan oldin
tekshirish** kerak. Bu sessiyada aynan shu tekshiruv yolg‘on hisobotni ushladi.

---

### 5. Testlar

`api-python/runtime_tests/test_supervisor.py` — **53 test**, haqiqiy `Engine`,
haqiqiy SQLite va haqiqiy `AgentLoop`. Run yaratilganda u **haqiqiy** run bo‘ladi,
ya’ni engine’ning o‘z validatsiyasi, rate limiting va audit yo‘li ishlaydi.

| Guruh | Soni | Nima tekshiriladi |
|---|---:|---|
| Ro‘yxatga olish | 13 | Ikkala tool ham `read`; **qayta qo‘shish no-op**; e’lon owner-only; noma’lum agent; bo‘sh/katta harfli section id; keyword chegaralari; disable/re-enable |
| Yo‘naltirish | 8 | E’lon qilingan section oladi; run **maqsad** uchun yaratiladi; mos kelmasa nom bilan rad; default agent yo‘q; aniq section keyword’ni chetlab o‘tadi |
| **Vakolat meros emas** | 6 | Maqsad o‘z policy’si bilan; supervisor hech nima olmaydi; maqsad **o‘z cheklovi** bilan cheklanadi; policy’si o‘chirilgan maqsadga yo‘naltirish rad |
| Zanjir o‘chiq | 5 | Router section standart holatda rad; sabab yoziladi; `is_router` belgisi; faqat aniq yoqilganda; boolean tekshiruvi |
| **Hop cap** | 6 | Standart 1 rad etadi; ledger’dan sanaladi; kengroq chegara; chegara chegaralangan; **restart’dan omon qoladi**; muzlatish/revoke |
| Replay | 3 | Bir kalit ikkinchi hop sarflamaydi; boshqa savol → conflict; run kaliti **hosil qilinadi** |
| Bound | 6 | Savol; request key; step budjeti; sekund budjeti; budjet runga yetadi |
| Model hech nima ayta olmaydi | 5 | Sxema faqat `question`; sections tool argument olmaydi; `supervisor.route` **run yaratmaydi**; faqat e’lon qilingan sectionlar |
| Ledger | 3 | `routed` va `unrouted` yoziladi; credential/URL yo‘q; policy materiali chiqmaydi |

Eng muhim to‘rtta test:

1. `test_a_supervisor_gains_nothing_from_routing` — policy bayt-bayt bir xil.
2. `test_a_target_cannot_use_a_tool_its_own_policy_forbids` — cheklov
   **bajaruvchiga** bog‘lanadi.
3. `test_the_hop_cap_is_counted_from_the_ledger` + `test_the_hop_count_survives_a_restart`.
4. `test_the_route_tool_reports_the_map_without_routing` — read tool haqiqatan
   read: 0 run yaratilganini o‘lchaydi.

`runtime_tests` 1204 → **1257 test**.

---

### 6. Integratsiya

| Joy | Nima o‘zgardi |
|---|---|
| `platform_runtime/engine.py` | `SUPERVISOR_SCHEMA` ro‘yxatga olindi |
| `platform_runtime/tools.py` | `register_supervisor_tools(r)` — registr endi **51 tool** |
| `app/platform_api.py` | `GET /{tenant}/supervisor`, `PUT /{tenant}/supervisor/{section}` (**owner-only**), `POST /{tenant}/supervisor/route`, `GET /{tenant}/supervisor/history` |
| `app/worker.py` | **Tick qo‘shilmadi** — ataylab: yo‘naltirish jadval emas, on-demand amal |
| `config/supervisor.example.json` | **Yangi**: section xaritasi misoli + chegara izohlari |
| `config/agent-capabilities.example.yaml` | `mgmt.router` misoli + UPA izohi |
| `scripts/probes/probe_supervisor_authority.py` | **Yangi**: vakolat meros qilinmasligini **o‘lchaydi** |

`PUT .../{section}` **owner-only**, chunki u qaysi agent rahbarga javob berishini
hal qiladi — bu **marshrut vakolati** o‘zgarishi, shaxsiy sozlama emas.
`POST .../route` esa owner/operator, chunki u oddiy operatsion amal (haqiqiy run
sarflaydi) va hop chegarasi **kodda** majburlanadi. `Idempotency-Key` header
**majburiy**: usiz takroriy so‘rov ikkinchi delegatsiya bo‘lardi.

---

### 7. Halol chegaralar

| Chegara | Sabab |
|---|---|
| **Yo‘naltirishni model qilmaydi** | Qaror deterministik keyword xaritasidan; model faqat xaritani **ko‘radi**. Sabab: tushuntirib bo‘lmaydigan marshrutni audit qilib bo‘lmaydi |
| **`route` endpointdan chaqiriladi**, agent tool’idan emas | Xavfsizlik uchun; kelajakda agent-o‘zaro yo‘naltirish kerak bo‘lsa, hop cap allaqachon tayyor |
| **Ko‘p hop amalda ishlatilmaydi** | `max_hops > 1` test bilan ishlaydi, lekin haqiqiy zanjir (A → B → C) hali yo‘q: B ni C ga yo‘naltiradigan yo‘l yozilmagan |
| **UI yo‘q** | Faqat tool + API qatlami |
| **Baho/latency o‘lchovi yo‘q** | Qaysi section qancha savol oldi — ledger’da bor, lekin endpoint yo‘q |
| Jonli model provayderi **yo‘q** | Yo‘naltirilgan run yaratiladi, lekin uni model bajarishi lokal muhitda sinalmagan |

---

### 8. Keyingi qadam

1. `asset_model` (P11) → `vision_events` (P11b) — **“tags emas, asset model”**:
   UNS ierarxiyasi (`zavod/sex/liniya/stanok`) `identity` bo‘ladi va kamera
   hodisasi shunga bog‘lanadi. **Frame platformaga kelmaydi.**
2. `supervisor` bo‘yicha qolgan ish: ko‘p hop zanjirini haqiqiy yozish, section
   statistikasi endpointi, UI.
3. `whatsapp_channel` (P8) — 24 soatlik oyna **o‘qiladi**, yuborish approval bilan.
4. `task_oversight_escalation` (P6): `workload` ning `overdue` chiqishini
   menejerga xabar qilish.


---

<a id="v05f"></a>

## v0.5f implementatsiyasi — Asset model (P11)

Blok: `asset_model`. Holat: **LOCAL_CONTRACT_TESTED**.
Yo‘l xarita: `docs/prd-v05/00-ENTERPRISE-OPERATING-LAYER-PRD-UZ.md`.
Oldingi bloklar: `V05-BLOCKS-CORE-UZ.md` (P1), `V05-BLOCKS-CORE-UZ.md`
(P1 audit + P9), `V05-BLOCKS-CORE-UZ.md` (P10), `V05-BLOCKS-CORE-UZ.md`
(P9b), `V05-BLOCKS-CORE-UZ.md` (P10b).

Bu hujjat nima **yozildi**, qanday **nuqsonlar** ushlandi va qaysi **chegaralar**
ochiq qoldi — o‘shani yozadi. Reja emas, hisobot.

---

### 1. Nima qo‘shildi

`api-python/platform_runtime/assets.py` — uskuna ierarxiyasi modeli.

Muammo: platformada hozirgacha **identitet** yo‘q edi. Savol berish mumkin
(«uskunalar holati qanday?»), lekin «P-100 pressning sikli vaqti» deb
so‘ralganda, `P-100` hech narsaga bog‘lanmagan edi. Har bir yangi modul
(texnik xizmat, OEE, kamera hodisasi, brak) o‘z identitetini o‘ylab topishi
kerak bo‘lardi va ular bir-biriga mos kelmasdi.

P11 shu identitetni **bir marta, operatorning o‘z reestrida** o‘rnatadi.

#### 1.1 Uchta ataylab qilingan qaror

**1. Yo‘l — identitetning o‘zi.** `zavod-1/sex-2/liniya-3/stanok-7` — bu shunchaki
manzil emas, **identitet**. Reestrdagi `id` ustuni aynan shu. Alohida
`asset_id` yasalmaydi, chunki ikkita raqamli id (biri reestrdagi, biri
platformadagi) vaqt o‘tib ajralib ketadi, va qaysi biri haqiqiy ekanini hech kim
bilmaydi.

**2. Ierarxiyani operator e’lon qiladi, kod uni *tekshiradi*.** `levels` —
`('zavod', 'sex', 'liniya', 'stanok')` — bu odam uchun yorliq, kod mos keladigan
lug‘at emas. Kod faqat **uzunlikni** va **nomning qonuniyligini** tekshiradi.
Ya’ni boshqa mijoz `('zavod', 'korpus', 'liniya', 'stanok')` yoki
`('uchastka', 'kran')` deb e’lon qilishi mumkin — kod o‘zgarmaydi.

**3. Yozish yo‘li yo‘q.** Faylda bitta ham `write` tool yo‘q. Sabab: modelni
tahrirlay oladigan tizim har bir hodisa, o‘lchov va hisobot tayanadigan
**identitetning muallifiga** aylanadi. UNS operatorniki; platforma uni **o‘qiydi**.
Bu OT sotuvchisi emas, OT *o‘quvchisi* ekanining kodda ifodasi.

#### 1.2 Beshta read tool

| Tool | Nima qaytaradi | `write` yo‘li |
|---|---|---|
| `asset.levels` | E’lon qilingan ierarxiya va chuqurlik | yo‘q |
| `asset.tree` | Butun daraxt (yoki `path`/`depth` bilan kesilgan) | yo‘q |
| `asset.children` | Bitta tugunning **bevosita** bolalari | yo‘q |
| `asset.descendants` | Bitta tugun ostidagi **barcha** aktivlar | yo‘q |
| `asset.resolve` | Bitta aktivning to‘liq grafik ko‘rinishi | yo‘q |

Beshtasi ham `read`. Bu tasodif emas: **identitetni tahrirlash — bu yozish
amali**, va modul oxirigacha faqat o‘qiydi.

---

### 2. Asosiy xavfsizlik xossasi: segment, satr prefiksi emas

Bu blokning eng nozik qismi.

`zavod-1` ning avlodlari `zavod-10` ni **o‘z ichiga olmasligi** kerak. Nega?
Chunki `'zavod-10'.startswith('zavod-1')` — **`True`**. Sodda satr tekshiruvi
bitta zavod so‘roviga boshqa zavodning aktivlarini qaytaradi.

```python
def is_descendant(parent_segments, candidate_segments):
    if len(candidate_segments) <= len(parent_segments):
        return False
    return candidate_segments[:len(parent_segments)] == list(parent_segments)
```

Yechim: **segment bo‘yicha** solishtirish, satr bo‘yicha emas. `['zavod-1']` va
`['zavod-10', ...]` — segmentlari mos kelmaydi, demak avlod emas.

Bu ataylab shunday yozilgan, chunki bu xato **jimgina** bo‘ladi: daraxt
chiziladi, aktivlar sanaladi, hech qanday xato chiqmaydi — shunchaki boshqa
zavodning uskunasi ro‘yxatda paydo bo‘ladi.

`test_a_sibling_plant_is_not_a_descendant` va
`test_the_tree_puts_the_sibling_plant_in_its_own_place` buni to‘g‘ridan-to‘g‘ri
o‘lchaydi, va fixture'da `zavod-1` bilan `zavod-10` **ikkalasi ham** bor —
chunki bu xatoni faqat shunday ma’lumot ushlaydi.

---

### 3. Ikkita yo‘l qoidasi — ataylab ikkita funksiya

Bu sessiyaning eng muhim dizayn tuzatishi.

Dastlab bitta `parse_path()` bor edi va u **qat’iy** edi: yo‘l to‘liq chuqurlikda
bo‘lishi shart. Bu identitet uchun to‘g‘ri: `zavod-1/sex-1` va
`zavod-1/sex-1/liniya-1/stanok-1` — bir xil fan emas, va bo‘shliqqa yo‘l qo‘ygan
parser ikkalasini bir xil deb o‘qib, ikki aktivning tarixini **jimgina
qo‘shib yuboradi**.

Lekin **navigatsiya** boshqa narsa. `asset.children('zavod-1')` — «shu zavodning
sexlari» — bu **filtr**, identitet emas. Shu bilan birga kodning bitta
funksiyasi ikkalasini ham bajarganda, `asset.children` umuman ishlamadi:
`zavod-1` bir segment, ierarxiya to‘rt — `ValueError: path must have 4 levels`.

Tuzatish **ikkita funksiya**, bitta emas:

| Funksiya | Chuqurlik qoidasi | Kim ishlatadi |
|---|---|---|
| `parse_path()` | **Aynan** to‘liq chuqurlik | Identitet: `resolve`, reestrdagi `id` |
| `prefix_path()` | **Ko‘pi bilan** to‘liq chuqurlik (1..N) | Navigatsiya: `children`, `descendants`, `tree(path=...)` |

Nega ikkita funksiya, nega bitta funksiyada `strict=True` flag emas? Chunki
flag bilan **noto‘g‘ri** chaqiruv (ya’ni avtomatik to‘g‘ri bo‘lgan) bo‘lmasdan
o‘tib ketadi, va identitet chaqiruvi bir kuni jimgina prefix qoidasiga tushib
qoladi. Ikkita nom — bu ikki xil **ma’no**, va kodda ham ikki xil bo‘lishi kerak.

---

### 4. Ushlandan haqiqiy nuqsonlar

Bular shu sessiyada topildi va tuzatildi. Uchta **haqiqiy modul** nuqsoni va
beshta **mening testingiz** nuqsoni bor — va bu almashinuvni yashirmaslik muhim.

#### 4.1 Modul nuqsonlari

| # | Nuqson | Qanday topildi | Nima o‘zgardi |
|---|---|---|---|
| 1 | **`asset.children` va `asset.descendants` intern tugunni umuman qabul qilmasdi**: bitta `parse_path` qat’iy chuqurlikni talab qilardi, shuning uchun `children('zavod-1')` `ValueError` berardi | Testlar `Ran 52 … failures=1, errors=30` — 30 xato aynan shu bitta sababdan | `prefix_path()` ajratildi (3-bo‘lim). Navigatsiya endi istalgan chuqurlikdagi tugunni qabul qiladi, identitet qoidasi esa o‘zgarmadi |
| 2 | **`children` noto‘g‘ri `level` qaytarardi**: `declared[len(parent) - 1]` — ya’ni **ota-onaning** darajasini, bir pog‘ona sayoz | `test_children_of_a_plant_are_its_shops`: `'sex' != 'zavod'` | `'level'` (so‘ralgan tugun) va **yangi `'child_level'`** (qaytgan natija) ajratildi. «zavod-1 ning sexlari» ni chizayotgan chaqiruvchiga ikkalasi ham kerak |
| 3 | **Graf preflight Sheets manbalar uchun `allowed_connections` ni umuman tekshirmasdi**: shart faqat `connectors.read`/`database.read` uchun edi. `sheets.rows` manba ulanishni **reestrdan** oladi, shuning uchun hech qachon tekshirilmasdi | Test yozilganda `Forbidden` ko‘tarilmadi — va **`test_business_graph.py` da ham** bu yo‘l sinalmagan edi (u yerda faqat `erp`/`connectors.read` holati bor) | `business_graph.preflight` endi ulanishni **ikkala yo‘l** bilan topadi: argumentda nomlangan, yoki (Sheets uchun) reestrdan o‘qilgan. `_register_connection()` qo‘shildi |

**#3 eng jiddiysi**, chunki u **umumiy modulda** va **grafikning o‘zida** edi —
ya’ni P1 dan beri mavjud. Ta’siri: `allowed_connections: ['erp']` bo‘lgan agent
Sheets manbasini baribir o‘qiy olardi, agar o‘sha manba preflight’dan o‘tgan
boshqa manba bilan birga e’lon qilingan bo‘lsa. Bu «hujjatlashtirilgan, lekin
bajarilmagan» nuqsonining yana bir ko‘rinishi: docstring *«har bir manba uchun
vakolat provayder I/O sidan oldin isbotlanadi»* deb yozgan, lekin kod buni faqat
manbaning argumenti ulanishni **eslatib o‘tganda** bajarardi.

Nega o‘z testlarim ushlamadi? Chunki **grafikning test fixture’ida**
Sheets manbasi (`finance`, ulanish `google`) doim **ikkinchi** manba edi va
`erp` birinchi bo‘lib javob berardi. Ya’ni xato bor edi, lekin fixture uni
ko‘rsatmasdi. Shuning uchun tuzatish bilan birga `test_business_graph.py` ga
**ikkita** regressiya testi yozildi:

- `test_a_sheets_source_connection_is_gated_too` — manba nomi bilan rad etiladi,
  va **provayder umuman chaqirilmaydi** (`transport.calls == []`).
- `test_an_unresolvable_register_is_not_a_way_past_the_tool_check` — reestr
  o‘qilmasa, manba o‘z tool’i bilan cheklanib qoladi (ya’ni «ulanish topilmadi»
  hech qachon «ruxsat berildi» ma’nosini bermaydi).

#### 4.2 Mening testingiz nuqsonlari

Bularni yozib qo‘yaman, chunki ular **uslubiy** saboq.

| # | Nuqson | Nima o‘rgatdi |
|---|---|---|
| 4 | `call()` yordamchisi `step` argumentini uzatmasdi — modulning barcha funksiyalari uni talab qiladi | 30 xatoning **hammasi** bitta sababdan edi. Bitta yordamchi xatosi butun faylni qizil qiladi — va bu **haqiqiy nuqsonni ko‘rishga xalaqit beradi** (#1, #2 shu 30 xato ichida yashiringan edi) |
| 5 | `write_config(assets=None)` `null` yozardi, kalitni **tashlab ketmasdi**. Test «e’lon qilinmagan ierarxiya» ni sinamoqchi edi, aslida «`null` ierarxiya» ni sinardi | `None` — bu JSON qiymati, yo‘q kalit emas. Ikkisi modul uchun **boshqa ma’no**. `_MISSING` sentineli qo‘shildi |
| 6 | `test_a_duplicate_level_name_is_refused` — `assertRaises` ni konfiguratsiya yozilishidan **oldin** chaqirardi | Mening testim modulni emas, **o‘zini** sinardi. Tuzatilganda haqiqiy xatti-harakat aniq bo‘ldi |
| 7 | `test_no_declared_hierarchy_is_a_declared_absence` ichida `view = levels(TENANT) if False else None` qatori bor edi | O‘lik kod testda — bu test **hech nima** o‘lchamayotganining belgisi. Qayta yozildi |
| 8 | `test_a_register_id_that_is_not_a_path_is_skipped` daraxtdagi **har bir** yo‘l 4 segment deb da’vo qilardi | Daraxtda intern tugunlar (zavod, sex, liniya) **bo‘lishi kerak** — bu daraxtning maqsadi. Da’vo ierarxiya chegarasiga o‘zgartirildi |

**Asosiy saboq:** 30 xatodan 11 tasi (va keyin 5 ta failure) — hammasi **mening**
kodimda. Agar shu bosqichda «modul buzilgan» deb xulosa qilsam, #1 va #2
nuqsonlarini ham, **#3 graf nuqsonini ham** topmagan bo‘lardim. Xatoni **avval
o‘z testingda** izlash — bu tezlik masalasi emas, **aniqlik** masalasi.

---

### 5. Testlar

`api-python/runtime_tests/test_assets.py` — **56 test**, haqiqiy `Engine`,
haqiqiy SQLite va haqiqiy Business Graph. Faqat Sheets HTTP hop qasddan
skriptlangan.

| Guruh | Soni | Nima tekshiriladi |
|---|---:|---|
| Ro‘yxatga olish | 4 | Beshtasi ham `read`; **qayta qo‘shish no-op**; yozish tool’i yo‘q; hech bir tool `spreadsheet_id`/`range`/`register`/`connection` qabul qilmaydi |
| Darajalar | 4 | E’lon qilingan ierarxiya; **yo‘q blok — bo‘sh ierarxiya, xato emas**; bo‘sh ro‘yxat rad; takroriy nom rad; noma’lum kalit rad; chegaradan chuqur rad |
| Yo‘l shakli | 7 | To‘g‘ri chuqurlik o‘tadi; **qisqa yo‘l rad** (identitet); chuqur yo‘l rad; chekka slashlar; bo‘shliq rad; foiz-kodlash rad; bo‘sh segment rad; ierarxiyasiz tenant — `Forbidden` |
| **Segment, prefiks emas** | 4 | `zavod-1` ↔ `zavod-10`; daraxtda alohida shox; o‘zi o‘zining avlodi emas |
| Avlodlar | 7 | Butun zavod; bitta sex; barg — bo‘sh, xato emas; `level`/`is_asset`; **truncation hisobotda**; `limit` chegarasi |
| Bolalar | 5 | Sexlar; liniyalar; har bir boladagi aktivlar soni; barg — xato; nevaralar qaytmaydi |
| Daraxt | 6 | Barcha darajalar; `depth` chegarasi; `path` bilan kichik daraxt; `depth` validatsiyasi; **aka-uka zavod o‘z joyida** |
| `resolve` | 4 | Bitta aktiv manbasi bilan; qiymat **tizim nomi** bilan yonma-yon; ro‘yxatda yo‘q yo‘l — `registered: false`, xato emas; shakl tekshiruvi |
| **Halollik** | 6 | O‘qilmagan manba **nom bilan** ko‘rinadi (bo‘sh daraxt emas); yo‘l bo‘lmagan id **tashlanadi**, qayta shakllantirilmaydi; buzuq qator butun daraxtni buzmaydi; token/URL/`Bearer` chiqmaydi; **vakolat javobda** |
| Vakolat | 4 | Manba tool’i talab qilinadi; **ulanish ruxsat etilishi shart** (regressiya); e’lon qilinmagan entity rad; **bir manba — bir o‘qish** (N+1 emas) |
| Butunlik | 3 | `format_path` aylanadi; sxema ortiqcha kalitni rad etadi; yo‘l talab qiladigan uchta tool `required: ['path']` |

`test_business_graph.py` — 44 → **46 test** (ikkita regressiya qo‘shildi, 4.1 #3).

`runtime_tests` 1257 → **1315 test**.

#### 5.1 To‘liq to‘plam — baseline bilan solishtirilgan

```
Ran 1315 tests in 202.681s
FAILED (failures=1, errors=149)
```

Oldingi baseline **aynan**: `failures=1, errors=149`. Ya’ni:

- **error soni o‘zgarmadi** (149) — yangi testlar soni (+58) unga **qo‘shilmadi**.
  1257 + 58 = 1315. Bu `V05E` dagi saboqning bevosita qo‘llanishi: to‘liq to‘plam
  soni o‘zgarmaganini **da’vo qilishdan oldin tekshirish**.
- **failure soni o‘zgarmadi** (1) — o‘sha eski `test_all_files_private` (Windows’da
  fayl ruxsat biti boshqacha; macOS bundle testi, bu blokka aloqasi yo‘q).

1 failure + 149 error — **oldindan mavjud**, mahalliy muhitda platforma
ko‘tarilmagani sababli (`cryptography`, `fastapi`, `.env`, `config/integrations.json`
yo‘q). Bu blok ularga **bittasini ham** qo‘shmadi.

---

### 6. Integratsiya

| Joy | Nima o‘zgardi |
|---|---|
| `platform_runtime/assets.py` | **Yangi**: beshta read tool, `parse_path`/`prefix_path` ikkiligi, `is_descendant` |
| `platform_runtime/tools.py` | `register_asset_tools(r)` — registr endi **56 tool** |
| `platform_runtime/business_graph.py` | **Nuqson #3 tuzatildi**: `preflight` endi Sheets manba ulanishini reestrdan ham topadi (`_register_connection`). Bu blokka 46 test (2 yangi) |
| `config/assets.example.json` | **Yangi**: ierarxiya misoli + oltita chegara izohi |
| `config/agent-capabilities.example.yaml` | `mgmt.plant_ops` misoli (5 asset tool + `sheets.rows`, `allowed_connections: [plant]`) |
| `scripts/check_assets_example.py` | **Yangi**: misolni **modulning o‘z validatoridan** o‘tkazadi, prefix qoidasini ham o‘lchaydi |
| `engine.py` | **O‘zgarmadi** — bu blok jadval yaratmaydi (o‘qish moduli) |
| `app/worker.py`, `app/platform_api.py` | **O‘zgarmadi** — tick ham, endpoint ham yo‘q (7-bo‘limga qarang) |

Registr: **56 tool**, `check_capabilities_example.py` — 26 tool havolasi,
0 noma’lum, 9 agent.

---

### 7. Halol chegaralar

| Chegara | Sabab |
|---|---|
| **API endpoint yo‘q** | Faqat tool qatlami. Aktiv modelini REST orqali boshqarish kerak emas: u **o‘qish** modeli |
| **Worker tick yo‘q** | Ataylab. Aktiv modeli **jadval emas** — u so‘ralganda o‘qiladi. Tick qo‘shilsa, har daqiqada o‘zgarmagan daraxtni o‘qib, provayderga behuda so‘rov yuborardi |
| **UI yo‘q** | Daraxtni ko‘rsatadigan ekran hali yozilmagan. `asset.tree` shu ekran uchun tayyor |
| **O‘lchovlarni o‘qimaydi** | `measurements` **e’lon qilinadi va tekshiriladi**, lekin P11 ularni o‘qimaydi. Sabab: o‘lchov vaqt qatori (time series) — bu P12/P13 ishi. P11 faqat **identitet** o‘rnatadi |
| **Kamerani boshqarmaydi** | Bu **P11b** (`vision_events`). P11 aktiv mavjud bo‘lishini ta’minlaydi — kamera hodisasi bog‘lanadigan narsa bo‘lishi uchun |
| **Reestrni yozmaydi** | Mijoz o‘z Google Sheets’ida yozadi. Platforma hech qachon aktiv yaratmaydi, tahrirlamaydi, o‘chirmaydi |
| **`MAX_CHILDREN = 200`, `MAX_DESCENDANTS = 200`** | Hujayra va qator chegaralari bilan bir xil intizom: bitta so‘rov butun zavodni emas, **chegaralangan** bo‘lakni qaytaradi, va `truncated` bayrog‘i **har doim** javobda bo‘ladi |
| **Jonli provayder qabuli — NOT_RUN** | Lokal muhitda platforma ko‘tarilmaydi. Barcha testlar haqiqiy `Engine` + skriptlangan Sheets hop |

---

### 8. Keyingi qadam

1. **`vision_events` (P11b)** — navbatdagi blok, va sabab PRD’da yozilgan:
   *«Agar biz avval kamera hodisasini olsak, uni nimaga bog‘lashni bilmaymiz va
   hodisa egasiz qoladi.»* Endi bog‘lanadigan narsa **bor**: `zavod-1/sex-2/...`
   shaklidagi identitet. **Frame platformaga kelmaydi** — faqat hodisa.
2. `whatsapp_channel` (P8) — 24 soatlik oyna **o‘qiladi**, yuborish approval bilan.
3. `task_oversight_escalation` (P6): `workload` ning `overdue` chiqishini menejerga
   xabar qilish.
4. Aktiv modeli bo‘yicha qolgan ish: daraxt UI, `measurements` uchun vaqt qatori,
   aktiv bo‘yicha texnik xizmat tarixi.


---

<a id="v05g"></a>

## v0.5g implementatsiyasi — Vision events (P11b)

Blok: `vision_events`. Holat: **LOCAL_CONTRACT_TESTED**.
Yo‘l xarita: `docs/prd-v05/00-ENTERPRISE-OPERATING-LAYER-PRD-UZ.md`.
Oldingi bloklar: `V05-BLOCKS-CORE-UZ.md` (P1), `V05-BLOCKS-CORE-UZ.md`
(P1 audit + P9), `V05-BLOCKS-CORE-UZ.md` (P10), `V05-BLOCKS-CORE-UZ.md`
(P9b), `V05-BLOCKS-CORE-UZ.md` (P10b), `V05-BLOCKS-CORE-UZ.md` (P11).

Bu hujjat nima **yozildi**, qanday **nuqsonlar** ushlandi va qaysi **chegaralar**
ochiq qoldi — o‘shani yozadi. Reja emas, hisobot.

---

### 1. Nima qo‘shildi

`api-python/platform_runtime/vision.py` — kamera hodisalarini o‘qiydigan modul.

PRD bo‘shliqni aniq nomlaydi:

> *"The infrastructure gap is **not the camera**. It is the **compute layer
> between the camera and a structured data output**."*

Ya’ni mijozda kamera ham, model ham **bor**. Yetishmayotgan qism — ular orasidagi
qatlam. Shuning uchun bu modul **video tizimi emas, hodisa o‘quvchisi**.

Uchta **read** tool: `vision.station_event`, `vision.person_event`,
`vision.summary`. Hodisalar operatorning mavjud reestridan (VMS jadvali yoki
webhook log) `sheets.rows` orqali o‘qiladi — **yangi transport yo‘q**.

---

### 2. Ikki qoida — modul shu ikki qoida uchun mavjud

#### 2.1 Frame hech qachon platformaga kelmaydi

Platformaga faqat `{stansiya, hodisa, sana, ishonch}` keladi. Bu v0.4 dagi ovoz
qoidasi bilan bir xil (*«audio hech qachon planner’ga yuborilmaydi, faqat
transkript»*), lekin kamera uchun bu **arzonlik emas, huquqiy majburiyat**:

> O‘zbekiston qonuni **O‘RQ-1125** (26.03.2026) bo‘yicha biometrik ma’lumot
> **lokal saqlanishi** va **davlat reestriga** kiritilishi shart.

Ya’ni kadrni bulutga chiqaradigan quvur mijozni **qonunbuzarga aylantiradi**.
Frame’ni edge’da qoldirish — **optimizatsiya emas, talab**.

Bu **strukturaviy** qulflangan, docstring bilan emas: `test_no_tool_accepts_a_frame_or_a_stream`
har bir tool sxemasida `frame`, `image`, `video`, `stream`, `rtsp`, `url`,
`snapshot`, `base64`, `photo`, `clip` kalitlarini **yo‘qligini** tekshiradi.
`test_the_module_contains_no_model_or_frame_vocabulary` esa **manba matnida**
`cv2`, `opencv`, `torch`, `tensorflow`, `base64`, `write_bytes`, `open(` kabi
so‘zlarni qidiradi — kelajakdagi tahrir jimgina kadr buferi yoki model o‘qitish
yo‘lini qo‘sha olmaydi.

#### 2.2 Shaxsni aniqlaydigan hodisa — biometrik, ya’ni `human_led`

PRD tool qatlamini **ikki sinfga** bo‘lishni talab qiladi:

| Sinf | Misol | Chegara |
|---|---|---|
| `vision.station_event` | nuqson, sikl vaqti, bekor turish, SOP buzilishi | yumshoq |
| `vision.person_event` | **yuzni aniqlash** | **biometrik, `human_led` majburiy** |

Bu **qattiq darvoza** sifatida amalga oshirildi. Shaxsni aniqlaydigan hodisani
faqat agentning `ladder`’i `human_led` **bo‘lganda** va operator biometrik
majburiyatni **tan olganda** (`biometric_ack`) o‘qish mumkin.

Darvoza **har qanday provayder I/O sidan oldin** ishlaydi — ya’ni noto‘g‘ri
`ladder` bilan agent biometrik ma’lumotga **umuman murojaat qila olmaydi**.

---

### 3. Asosiy xavfsizlik xossasi — da’vo emas, **o‘lchov**

`scripts/probes/probe_vision_biometric_gate.py` buni o‘lchaydi: bir xil chaqiruv uch
`ladder` darajasida yurgiziladi va har biri **nechta provayder so‘rovi**
yaratgani sanaladi. Muhim raqam — birinchi ikkitasining **rad etilishi** emas,
**nol GET** bilan rad etilishi: ma’lumotni o‘qib bo‘lgach rad etadigan darvoza
darvoza emas.

```
vision tools        : ['vision.station_event', 'vision.person_event', 'vision.summary']

ladder             person_event outcome               provider GETs
----------------------------------------------------------------------
autonomous         refused: Person-identifying ...    0
human_assisted     refused: Person-identifying ...    0
human_led          read 1 event(s)                    1

ladder             station_event outcome              provider GETs
----------------------------------------------------------------------
autonomous         read 2 event(s)                    1
human_led          read 2 event(s)                    1

PROVEN: person events are refused before any provider I/O unless the
        agent is human_led, and the impersonal read never surfaces a
        person-identified row
```

Diqqat qilinadigan ikkita raqam: `station_event` **2** hodisa qaytaradi (4 emas),
chunki fixture’da 4 qator bor va ulardan biri `yuz` — u **shaxssiz o‘qishdan
tushib qolgan**.

---

### 4. Ushlandan haqiqiy nuqsonlar

Bular shu sessiyada topildi va tuzatildi.

#### 4.1 Modul nuqsonlari

| # | Nuqson | Qanday topildi | Nima o‘zgardi |
|---|---|---|---|
| 1 | **`sensitivity: station` qaysi sinf shaxsiy ekanini aytmasdi.** `person_classes` reestr ichida edi, shuning uchun `station` reestrida u **bo‘sh** bo‘lardi — va `yuz` qatori `nuqson` dan farq qilmasdi. Filtr esa butun oqimni tashlab yuborardi | `test_a_person_class_row_is_not_returned_by_the_station_read` yiqildi: `yuz` **qaytgan** edi | **Sinflar lug‘ati endi tenant bo‘yicha**, reestr bo‘yicha emas. `person_classes(tenant)` qo‘shildi. Sabab pastda |
| 2 | **`_bind` intern tugunni aktiv deb qabul qilardi** — `prefix_path` har qanday chuqurlikni oladi, shuning uchun `zavod-1/sex-1` «bog‘landi» va `not-a-path` ham | `test_an_unbound_station_is_reported_not_dropped` va `test_a_station_of_the_wrong_depth_is_unbound` yiqildi: `unbound` 0 edi | `_bind` endi `parse_path` (aynan **aktiv** darajasi) ishlatadi. PRD hodisani `graph.timeline (asset)` ga bog‘laydi — intern tugunning timeline’i yo‘q |

**#1 eng muhimi va u dizayn xatosi edi, kod xatosi emas.**

Mantiq shunday edi: «har reestr o‘zi qaysi sinflar shaxsiy ekanini e’lon qiladi».
Bu **noto‘g‘ri**, chunki **sinf — bu HODISANING xossasi, u yotgan jadvalning
xossasi emas**. Agar `yuz` kirish reestrida biometrik bo‘lsa, u sex reestrida
ham biometrik. Reestr bo‘yicha cheklanganda, **noto‘g‘ri jadvalga tushgan
yuzni aniqlash qatori shaxssiz yo‘ldan o‘qilardi** — bu esa aynan huquqiy chegara
oldini olish uchun qo‘yilgan xato.

Tuzatish: bitta tenant bo‘yicha lug‘at, va filtr **`_is_person(class, identifying)
is not wants_person`** — ya’ni shaxssiz o‘qish **har qanday** shaxsiy sinfni
tashlaydi, qaysi reestrda uchraganidan qat’i nazar.

#### 4.2 Muhim topilma — tuzatilmagan, lekin yozib qo‘yilgan

**`sheets.py` bo‘sh `allowed_connections` ni "hammasi ruxsat" deb o‘qiydi, kod
bazasining qolgan qismi esa "hech narsa ruxsat" deb o‘qiydi.**

```python
## sheets.py _authorize()
allowed = policy.get('allowed_connections') or []
if allowed and connection not in allowed:      # <-- bo'sh ro'yxat = RUXSAT
    raise Forbidden('Agent connection access denied')
```

Lekin `engine.py`, `connectors.py`, `google_adapters.py` va endi
`business_graph.py` **qat’iy** o‘qiydi:

```python
if args['connection'] not in policy.get('allowed_connections', []):   # bo'sh = RAD
```

Ya’ni **bir xil policy qiymati bir yo‘lda "hammasi taqiqlangan", boshqasida
"hammasi ruxsat"** ma’nosini beradi. Bu xavfsizlik modelidagi haqiqiy
nomuvofiqlik.

Nega bu test bilan ushlanmadi: odatdagi dispatch yo‘lida `engine.py` tool’ni
chaqirishdan **oldin** tekshiradi, shuning uchun `sheets._authorize` ga navbat
yetmaydi. Nomuvofiqlik faqat `sheets.rows` handler **to‘g‘ridan-to‘g‘ri**
chaqirilganda ko‘rinadi.

**Tuzatilmadi**, va sabab ataylab: (a) bu P11b blokining doirasi emas; (b)
`config/` dagi **hech bir** deklaratsiya bo‘sh ro‘yxatga tayanmaydi (hammasi aniq
ulanish yozadi), ya’ni amaliy ta’sir hozircha yo‘q; (c) o‘zgartirish 149 ta
oldindan mavjud yiqilish bilan aralashib ketadigan mavjud testlarga tegadi.
**Lekin bu TOPILMA, yashirilgan emas** — alohida hal qilinishi kerak.

#### 4.3 Mening testingiz nuqsonlari

| # | Nuqson | Nima o‘rgatdi |
|---|---|---|
| 3 | `test_the_module_exposes_no_write_path` `dir(module)` ni skanerlardi va **`assets`** importini "yozish yuzasi" deb belgiladi (`set` qism-satri uchun) | Import qilingan modul — qo‘ng‘iroq yuzasi emas. Endi faqat **modulning o‘z** funksiya/klasslari (`__module__` bo‘yicha) tekshiriladi |
| 4 | `test_summary_has_no_oee_or_performance_figure` butun JSON’da `'oee'` qidirardi — va **o‘zi qo‘shgan** izohda topdi: *«bu OEE emas, bu P13 ishi»* | Rad etishni **halol qiladigan** ogohlantirishni qidirish testni yolg‘on qizil qiladi. Endi **kalitlar** tekshiriladi va izohda `P13` borligi **talab** qilinadi |
| 5 | `test_confidence_is_reported_verbatim…` `event` sinfi bo‘yicha dict yasardi, lekin fixture’da **ikki** `nuqson` qatori bor (ikki xil zavod) — biri ikkinchisini bosib ketdi | Kalit sifatida `(stansiya, hodisa)` kerak, `hodisa` yolg‘iz yetarli emas |
| 6 | `test_the_connection_must_be_permitted` `allowed_connections=[]` bilan `Forbidden` kutardi | Aynan 4.2 topilmasiga olib keldi: kutilgan xatti-harakat yo‘q edi. Test haqiqiy xatti-harakatni o‘lchaydigan qilib qayta yozildi va nomuvofiqlik hujjatlashtirildi |

---

### 5. Hodisani aktivga bog‘lash

Har hodisa P11 modelidagi **aktiv** (barg yo‘l) ga bog‘lanadi — PRD uni
`graph.timeline (asset)` da ko‘rsatish uchun.

Qoida **segment bo‘yicha**, satr prefiksi bo‘yicha emas: `'zavod-10'.startswith('zavod-1')`
**`True`**, shuning uchun sodda prefiks bir zavod hodisalarini boshqasiga
bog‘lardi va buni **jimgina** qilardi. Bu P11 ning asosiy xossasi, endi unga
tayanadigan oqimda ham o‘lchangan.

**Bog‘lanmagan hodisa — xato emas, hisobot.** `zavod-1/sex-1` (intern tugun) yoki
`not-a-path` **`unbound`** hisoblagichida ko‘rinadi, lekin:
- **tashlab yuborilmaydi** (keyin tekshirish uchun son qoladi),
- va **hech qachon taxmin qilinmaydi** — noto‘g‘ri zavodga bog‘langan hodisa
  menejer ko‘rib turgan bog‘lanmagan hodisadan **yomonroq**.

---

### 6. Oyna va chegara

Oqim aslida **to‘xtovsiz**, shuning uchun chegarasiz o‘qish bir kunda millionlab
qatorni planner’ga quyardi. Har o‘qish **oyna** (`since`/`until`) va **limit**
bilan cheklangan.

- Oyna **ISO sana** (`YYYY-MM-DD`) bo‘yicha **matn sifatida** solishtiriladi.
  `19.09.2026` kabi noaniq format **taxmin qilinmaydi** — `undated` da sanaladi.
  Sabab: taxmin noto‘g‘ri kunni **qo‘shadi yoki chiqarib tashlaydi**.
- `truncated` bayrog‘i **har doim** javobda, va reestrning o‘zi kesilgan bo‘lsa
  ham ko‘rinadi — cheklangan javob to‘liq ko‘rinmasligi kerak.
- `summary` **faqat shaxssiz** hodisalarni sanaydi. Aniqlangan odamlar **soni** ham
  biometrik ishlovdir, shuning uchun u darvoza ortida qoladi.

`summary` **OEE emas**: unumdorlik va mavjudlik uchun sikl vaqti va
rejalashtirilgan ish vaqti kerak — bu **P13** ishi. Kod shuni **nomlaydi**
(`note` da), chunki jimgina yaqin son berish mijoz tekshira olmaydigan raqam
bo‘lardi.

---

### 7. Ishonch ko‘rsatkichi

`confidence` ustuni ixtiyoriy va qiymati **so‘zma-so‘z matn sifatida** qaytariladi.
U **hech qachon** to‘g‘rilik da’vosiga aylantirilmaydi — PRD ning o‘z
ogohlantirishi amal qiladi: vendor aniqligi *«camera angle and lighting»* ga
bog‘liq.

Bo‘sh katak **`0` emas, ko‘rsatilmaydi**: bo‘sh katak va ishonchli nol — ikki xil
fakt.

---

### 8. Testlar

`api-python/runtime_tests/test_vision.py` — **48 test**, haqiqiy `Engine`,
haqiqiy SQLite. Faqat Sheets HTTP hop skriptlangan.

| Guruh | Soni | Nima tekshiriladi |
|---|---:|---|
| Ro‘yxatga olish | 5 | Uchtasi ham `read`; qayta qo‘shish no-op; yozish yuzasi yo‘q; **tool frame/stream qabul qilmaydi**; register/range/column qabul qilmaydi |
| **O‘qitish taqiqi** | 1 | Manba matnida `cv2`/`torch`/`base64`/`write_bytes` yo‘q |
| Sinf ajratmasi | 3 | Shaxssiz yo‘l ishlaydi; **`yuz` shaxssiz o‘qishdan tushadi**; shaxssiz qator shaxsiy o‘qishdan tushadi; har hodisa sinfi bilan keladi |
| **Biometrik darvoza** | 7 | `autonomous` rad; `human_assisted` rad; `human_led` o‘tadi; **provayder I/O sidan oldin rad**; `biometric_ack` siz rad; sinfsiz shaxsiy reestr rad; noma’lum `sensitivity` rad |
| **`summary` chegarasi** | 2 | Shaxsiy hodisani **sanAmaydi**; OEE/unumdorlik **kaliti** yo‘q, izoh `P13` ni nomlaydi |
| Aktivga bog‘lash | 5 | Aktiv darajasiga bog‘lanadi; **aka-uka zavod o‘ziniki**; bog‘lanmagan **hisobotda**; noto‘g‘ri chuqurlik bog‘lanmagan; ierarxiyasiz `Forbidden` |
| Oyna | 6 | Quyi chegara; **yuqori chegara**; teskari oyna rad; ISO bo‘lmagan oyna rad; sanasiz qator `undated`; oynasiz hamma kun |
| Chegara | 3 | `limit` qo‘llanadi va hisobotda; `limit` chegaralangan; `truncated` har doim bor |
| Halollik | 7 | Ishonch **so‘zma-so‘z**; bo‘sh ishonch **ko‘rsatilmaydi**; izoh ogohlantirishni saqlaydi; buzuq qator buzmaydi; **o‘qilmagan reestr "hodisa yo‘q" emas**; token/URL chiqmaydi; vakolat javobda |
| Vakolat | 6 | Manba tool’i talab; ulanish talab; e’lon qilinmagan blok rad; **bir o‘qish — bir GET**; faqat shaxsiy reestr bilan shaxssiz o‘qish rad |
| Butunlik | 3 | Sxema ortiqcha kalitni rad; noma’lum reestr kaliti rad; `station_column` siz reestr rad |

`runtime_tests` 1315 → **1363 test**.

---

### 9. Halol chegaralar

| Chegara | Sabab |
|---|---|
| **Tool’lar jonli oqimdan o‘qimaydi** | Faqat operator yozib turgan reestrdan. Webhook qabul qilish nuqtasi, navbat va dedup hali **yozilmagan** — hozir «VMS jadvalga yozadi, biz o‘qiymiz» |
| **Hodisa dedup’i yo‘q** | Takroriy hodisa ikki marta sanaladi. Dedup kaliti (`(tenant, register, stansiya, hodisa, vaqt)`) keyingi ish |
| **OEE yo‘q** | Ataylab: sikl vaqti va rejalashtirilgan ish vaqti kerak. Bu **P13** |
| **Xodim bilan bog‘lash yo‘q** | `person_event` kim ekanini aytmaydi va aytmasligi kerak: platforma yuzni taniy olmaydi va xodimni baholamaydi (P9b chegarasi bilan bir xil) |
| **UI yo‘q** | Faqat tool + konfiguratsiya |
| **`sheets.py` nomuvofiqligi ochiq** | 4.2 ga qarang — topilgan, hujjatlashtirilgan, ataylab tegilmagan |
| **Jonli provayder qabuli — NOT_RUN** | Lokal muhitda platforma ko‘tarilmaydi; barcha testlar haqiqiy `Engine` + skriptlangan Sheets hop. Haqiqiy VMS bilan sinalmagan |

---

### 10. Keyingi qadam

1. `whatsapp_channel` (P8) — 24 soatlik oyna **o‘qiladi**, yuborish approval bilan.
2. `document_intake` (P8b).
3. Hodisa oqimi uchun: webhook qabul nuqtasi + **dedup kaliti** + navbat.
4. `task_oversight_escalation` (P6): `workload` ning `overdue` chiqishini
   menejerga xabar qilish.
5. `oee_and_andon` (P13) — sikl vaqti va rejalashtirilgan ish vaqti bilan haqiqiy
   OEE; `summary` o‘shanda ham o‘z chegarasini saqlab qoladi.

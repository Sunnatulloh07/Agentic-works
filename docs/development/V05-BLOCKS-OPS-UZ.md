# v0.5 blok hisobotlari — operatsiyalar (ERP, ishlab chiqarish, aloqa, ombor)

ERP posting, kechikkan ish eskalatsiyasi, ishlab chiqarish o'qishlari, OEE, telefoniya va ombor.

## Mundarija

- [V05M — V05M — ERP posting (`erp_sync_beyond_mock_post`)](#v05m)
- [V05N — V05N — Kechikkan ish eskalatsiyasi (`task_oversight_escalation`)](#v05n)
- [V05O — V05O — Ishlab chiqarish o'qishlari (`manufacturing_bom`)](#v05o)
- [V05P — V05P — OEE va andon (`oee_and_andon`)](#v05p)
- [V05Q — V05Q — Qo'ng'iroq hodisalari va rozilik darvozasi (`telephony_outbound`, A bosqich)](#v05q)
- [V05R — V05R — Chiqish navbati, tezlik shifti va saqlash oynasi (`telephony_outbound`, B bosqich)](#v05r)
- [V05S — V05S — Eskalatsiya yetkazish P6 koordinatori orqali (`telephony_outbound`, C bosqich)](#v05s)
- [V05T — V05T — Inventory read bloki (`inventory_moysklad`), P4](#v05t)

---

<a id="v05m"></a>

## V05M — ERP posting (`erp_sync_beyond_mock_post`)

**Sana:** 2026-09-20
**Blok:** `erp_sync_beyond_mock_post` (P8e)
**Holat:** **QURILDI VA O'LCHANDI** — lokal kontrakt darajasida. Live ERP acceptance
yo'q, shuning uchun `production_release` **NO_GO** bo'lib qoladi.
**Yangi tool:** 3 ta — `erp.posting_prepare` (read), `erp.posting_submit` (write),
`erp.posting_status` (read). `build_registry()` **69 → 72**,
`known_tool_names()` **70 → 73**.
**Yangi test:** `runtime_tests/test_erp.py`, **54** test.
**Yangi probe:** `scripts/probe_erp_posting_boundary.py` — 5 bo'lim, **PROVEN**.

---

### 1. Vazifa va undan oldingi holat

Roadmap elementi `erp_sync_beyond_mock_post` shunday ta'riflangan edi: bugungi
`document.posting_plan` faqat **taklif** qaytaradi (`executes_payment: False`) va
hech qachon yozmaydi. Ya'ni "buxgalteriyaga yozish" qadami umuman yo'q edi.

Foydalanuvchi to'rtta savolga shunday javob berdi:

1. **Qaysi adapter?** — "Ikkalasi ham" (`onec_http` **va** `custom_http`).
2. **Chegara qayerda?** — "faqat bu yozishlarda aniq buyruqlar bo'lganda ... 
   gallyutsinatsiya bilan bo'lmaslik kerak".
3. **Buni kodda qanday majburlash?** — "Hujjat to'liq bo'lmasa — rad etish".
4. **Ikki marta to'lashning oldini?** — "1 va 2 ham qilish kerak" (ERP qidiruvi
   **va** lokal ledger).

Bu to'rtta javob modulning butun tuzilishini belgiladi. Har biri quyida alohida
o'lchov bilan ko'rsatilgan.

### 2. Uch qoida, va har biri **majburlangan**

#### 2.1. Pul harakatlanmaydi — bu **torroq** da'vo

`documents` bloki "platforma pul o'tkazmaydi" deb da'vo qilardi va bu to'g'ri edi,
chunki u yerda pul yo'li umuman yo'q edi. Bu blok **boshqa** da'vo qiladi, shuning
uchun **alohida** o'lchanadi:

* modul **ERP'da buxgalteriya hujjati** yaratadi — ya'ni **qarz (payable)** yozadi;
* to'lash **inson ishi** bo'lib qoladi, mijoz allaqachon ishonadigan ERP ichida;
* modulda `pay`, `transfer`, `settle`, `remit`, `refund`, `payout` nomli **birorta**
  ommaviy funksiya yo'q — bu **strukturaviy** tekshiriladi
  (`test_the_module_exposes_no_payment_callable` `dir(module)` ni aylanadi);
* `posting_body()` — modul **yuborishi mumkin bo'lgan yagona to'plam**; probe uni
  ko'rib chiqadi va pul maydoni yo'qligini tasdiqlaydi.

Ya'ni "pul harakatlanmaydi" endi **kengaytirilgan** ma'noda: platforma qarz
yozadi, lekin uni **to'lamaydi**.

#### 2.2. To'liq bo'lmagan hujjat — **rad etiladi**, to'ldirilmaydi

`REQUIRED_FIELDS = (supplier, number, doc_date, currency, total_minor, account,
counterparty)`. Ulardan ikkitasi — `account` va `counterparty` — **hujjatda umuman
yo'q**, ular operatorning registridan olinadi.

Bu yerda **uchta** qaror bor va uchtasi ham muhim:

**(a) Yetishmayotgan va buzuq ajratilgan.** `resolve_posting` `missing` ro'yxatini
yig'adi, lekin **buzuq** qiymatni darhol o'z sababi bilan ko'taradi:

```
total_minor: 1250000.5  ->  'total_minor must be an integer count of minor units'
total_minor: yo'q       ->  missing ro'yxatiga qo'shiladi
```

Nega? Chunki "summani o'qiy olmadik" va "yuborgan summangiz son emas" operatorni
**ikki xil joyga** yuboradi. Ikkalasini bitta "incomplete" xabariga yig'ish
xatoni bilyard ostiga yashirishdir.

**(b) Alias, xom kod emas.** Model `purchase` deydi, operator registri uni `6010`
ga aylantiradi. Model **hech qachon** xom hisob kodini yozmaydi, chunki koddagi bir
raqam xatosi — audit paytigacha hech kim sezmaydigan noto'g'ri o'tkazma.

**(c) Hisob va kontragent hujjatdan **o'qilmaydi**.** Bu ataylab: hujjat —
**tashqaridan ta'sirlanadigan** kirish. O'z hisob raqamini o'zi aytadigan yetkazib
beruvchi — o'z qarzini qayerga qo'yishni **o'zi tanlayotgan** yetkazib beruvchi.
Shuning uchun imzo ham shunday: `resolve_posting(tenant, document, *, account='',
counterparty='')` — ular **keyword-only** va hujjat dict'idan **hech qachon**
o'qilmaydi. Probe buni `resolve_posting(..., **{field: 'injected'})` bilan sinab
ko'radi va `TypeError` oladi.

#### 2.3. Ikki marta yozish — **uch qavat** qo'riqchi

`identity(posting)` — `(driver, 'invoice', supplier, number)`. Summa **ataylab**
kirmaydi (xuddi `documents` blokidagi kabi): bir xil raqam **boshqa** summa bilan —
takror emas, **shubhaliroq**.

| Qavat | Nima qiladi | Nimani ushlaydi |
|---|---|---|
| 1. ERP qidiruvi (`find_posted`) | Provayderdan so'raydi | Boshqa tizim yozganini |
| 2. Lokal ledger (`posted`) | O'z yozuvimizni **I/O dan oldin** tekshiradi | O'z takrorimizni |
| 3. `UNIQUE` indeks (`claim_key`) | Poygani yutadi | Bir vaqtdagi ikki submitni |

Uchtasi ham kerak, chunki: (1) boshqa tizim yozganini ko'rmaydi, (2) provayder
qidiruvi POST bilan **atomik** bo'lishiga ishonib bo'lmaydi, (3) indeks esa
**check emas, kafolat**.

**Va to'rtinchi qavat — muvaffaqiyatsizlik qaytariladigan bo'lib qoladi.** Bu
birinchi versiyadagi **haqiqiy xato** edi va uni audit tutdi: `UNIQUE` indeks
`(tenant,driver,kind,supplier,number)` ustida turgan edi, shuning uchun bitta
transport uzilishi qonuniy schyot-fakturani **abadiy** yozilmas qilib qo'yardi va
yagona chora — bazani qo'lda tahrirlash edi. Tuzatish: `claim_key` ustuni faqat
`CLAIMING_STATUSES = {'posted','skipped_existing'}` uchun to'ldiriladi; `failed`
va `unconfirmed` qatorlar indeksni **egallamaydi** (SQLite'da `NULL` lar bir-biriga
teng emas, shuning uchun bir nechtasi yonma-yon yashay oladi).

### 3. Audit tutgan haqiqiy nuqsonlar

Bu blokda **oltita** haqiqiy xato topildi. Har biri kodga emas, **auditga** tegishli
edi — ya'ni ular yozilgan paytda "to'g'ri" ko'rinardi.

| # | Nuqson | Nega xavfli | Tuzatish |
|---|---|---|---|
| 1 | `_date_text('2026-13-01')` **qabul qilardi** | ISO shakl regex'i oy oralig'ini tekshirmasdi; `2026-13-01` — sana emas | `_real_date()` `calendar.monthrange` bilan |
| 2 | `failed` qator o'z retry'sini **to'sardi** | Transport uzilishi schyot-fakturani abadiy yozilmas qilardi | `claim_key` faqat claiming statuslarda |
| 3 | `posted()` `failed` qatorni qaytarardi | Retry baribir rad etilardi | `AND status IN (CLAIMING_STATUSES)` |
| 4 | `PATH_RE` `/a/../b` va `//evil.example` ni **qabul qilardi** | O'z regex'im nuqta ruxsat bergan edi | O'chirildi; `safe_relative_path` ga topshirildi |
| 5 | Yetishmayotgan credential **yutilardi** | Operator ERP'ni tekshirishga ketardi, aslida env var kerak | `RuntimeError`/`ValueError` **aynan** qayta ko'tariladi |
| 6 | Buzuq maydon "incomplete" ga **qo'shilardi** | `total_minor: 1250000.5` — *yetishmayotgan* deb xabar berardi | Absent → `missing`; malformed → darhol raise |

**4-nuqson** eng o'rgatuvchisi. Men o'z yo'l-gate'imni yozdim, u allaqachon
mavjud va **allaqachon testdan o'tgan** funksiyani takrorladi — va takrorlash
jarayonida **boshqacha** qaror qabul qildi. Dars: *bir yo'l-gate'ining ikkinchi
implementatsiyasi — uni xato qilishning ikkinchi imkoniyati.* Kod endi
`safe_relative_path` ga topshiradi va sabab docstring'da **yozilgan**, chunki
kelajakda kimdir "o'zim yozaman" deb o'ylamasligi kerak.

### 4. Config: `connections` dan **ataylab** ajratilgan

`erp_config()` `connections` dan emas, `erp_posting` degan **alohida** kalitdan
o'qiydi. Sabab: CRM ulanish — ko'p o'qiladigan integratsiya; posting maqsadi —
**yozganingizga qarab harakat qiladigan** tizim. Alohida deklaratsiya bo'lmasa,
operator "faqat shu sozlangan" deb **CRM sandbox**'iga yozib qo'yishi mumkin.

`config/erp_posting.example.json` — `_note_*` kalitlari bilan, va
`scripts/check_erp_example.py` uni **validatordan o'tkazadi**. Checker shuningdek
o'lchaydi: noma'lum kalit rad etiladi; `host` da sxema yo'l rad etiladi;
`../admin` va `//evil.example` rad etiladi; to'liq bo'lmagan hujjat **nomi bilan**
rad etiladi; e'lon qilinmagan alias rad etiladi; `10.01.2026` **noaniq** deb rad
etiladi; `2026-02-30` **haqiqiy emas** deb rad etiladi.

### 5. Ikkala driver — **ikkalasi ham** o'lchandi

Foydalanuvchi "ikkalasi ham" dedi. Birinchi versiyada **faqat `onec_http`**
mashq qilingan edi — bu bo'shliq edi va u yopildi.

Driver'ning **yagona** xulq-atvor farqi — javobni **qanday o'qish**:

```
custom_http:  {'id': 'C-77'}                  response_map: {'existing_id': 'id'}
onec_http:    {'result': {'Ref_Key': 'C-77'}} response_map: {'existing_id': 'result.Ref_Key'}
```

Bu **o'lchandi** (`test_custom_http_reads_a_flat_answer_while_onec_reads_a_nested_one`):
custom flat javobni o'qiydi, onec uni **ko'rmaydi** (va aksincha). Agar driver
hech narsani o'zgartirmasa, bittasi **tasodifan** ishlagan bo'lardi — va custom
ERP **o'zida bor** hujjatni ko'rmay qolardi, ya'ni aynan ikki marta yozish sharti.

Qo'shimcha ravishda o'lchandi: `basic` va `bearer` auth (1C on-premise odatda
`user:password` ishlatadi, faqat bearer yuboradigan blok o'sha mijozlarda
**joylashtirilmaydi**); buzuq `user:password` — **I/O dan oldin** rad etiladi;
va **driver identifikatsiyaning bir qismi**, shuning uchun bir xil raqam har bir
ERP'ga **bir martadan** yozilishi mumkin (migratsiya uchun zarur), lekin bitta
ERP ichida qo'riqchi **kuchsizlanmaydi** (buni alohida test tekshiradi — aks
holda oldingi test identifikatsiya umuman majburlanmasa ham o'tib ketardi).

### 6. Nima uchun `erp.posting_submit` — **outbound gate'da emas**

`engine.OUTBOUND_TOOLS` "bu agent shu qabul qiluvchiga murojaat qila oladimi"
savoliga javob beradi. Posting'ning **qabul qiluvchisi yo'q**: uning qo'riqchisi —
**hujjat identifikatsiyasi**, va uni ledger hamda ERP qidiruvi **I/O dan oldin**
majburlamoqda. Uni allowlist shoxobchasiga qo'yish tekshiruvni **bo'sh** qilardi,
chunki bog'lanadigan suhbat yo'q va shart har doim bajarilardi. Sabab
`engine.py` da **izoh sifatida** yozilgan, chunki kelajakda kimdir uni "unutib
qoldirilgan" deb qo'shib qo'ymasligi kerak.

### 7. Tekshiruv natijasi

| Tekshiruv | Natija |
|---|---|
| `test_erp.py` | `Ran 54 tests ... OK` |
| `probe_erp_posting_boundary.py` | **PROVEN** (5 bo'lim) |
| `check_erp_example.py` | `OK` |
| Registr | `build_registry() = 72`, `known_tool_names() = 73` |
| `test_business_graph.py` | 98 OK |
| `test_whatsapp.py` | 119 OK |
| `test_whatsapp_inbound.py` | 70 OK (skipped=1) |
| To'liq to'plam | `Ran 1742 tests` — **failures=1, errors=149, skipped=1** |

Baseline **oltinchi marta aynan o'zgarmadi**: 1688 → 1742 = +54 yangi test, va
`failures=1, errors=149` — o'sha Windows-only `test_all_files_private`
(`AssertionError: 0 != 63`), boshqa hech narsa.

### 8. Ochiq qolgan

* **Live ERP acceptance** — yo'q. `production_release` **NO_GO**.
* `allow_auto_submit` — config'da bor, lekin engine'ning approval darvozasini
  **o'chirmaydi**; u faqat operatorning "bu shakldagi oddiy schyotlar ikkinchi
  qarashni talab qilmaydi" niyatini yozadi. Live acceptance'gacha `false`.
* `erp.posting_prepare` `in_erp` ni **tekshirmaydi** ("use `erp.posting_submit`
  to check" deb qaytaradi) — ataylab, chunki prepare **read** va u I/O qilmasligi
  kerak; lekin operator buni bilib turishi kerak.

### 9. Xulosa

Platforma endi **birinchi marta** moliyaviy tizimga yozadi. Uchta xossa, har biri
**o'lchangan**: pul harakatlanmaydi (strukturaviy + body + chiqish); to'liq
bo'lmagan hujjat **nomi bilan** rad etiladi va **hech qachon** to'ldirilmaydi
(yetishmayotgan va buzuq **ajratilgan**); hujjat **ko'pi bilan bir marta**
yoziladi, va bu **to'rt qavat** bilan himoyalangan (ERP qidiruvi, I/O dan oldingi
lokal ledger, poygani yutadigan `UNIQUE` indeks, va muvaffaqiyatsizlikni
**qaytariladigan** qoldiradigan status siyosati).

Eng muhim dars **auditda** chiqdi: oltita haqiqiy nuqsondan biri — o'z
yo'l-gate'imni yozish — mavjud, testdan o'tgan funksiyani takrorlash edi va
takror **boshqacha** qaror qabul qildi. Ishlaydigan kodni qayta yozish — uni
xato qilish uchun yangi imkoniyat.


---

<a id="v05n"></a>

## V05N — Kechikkan ish eskalatsiyasi (`task_oversight_escalation`)

**Sana:** 2026-09-20
**Blok:** `task_oversight_escalation` (P6)
**Holat:** **QURILDI VA O'LCHANDI** — lokal kontrakt darajasida. Live Telegram
acceptance yo'q, shuning uchun `production_release` **NO_GO** bo'lib qoladi.
**Yangi tool:** 2 ta, **ikkalasi ham read** — `escalation.preview`,
`escalation.schedules`. `build_registry()` **72 → 74**.
**Yangi test:** `runtime_tests/test_escalation.py`, **45** test.
**Yangi probe:** `scripts/probe_escalation_boundary.py` — 6 bo'lim, **PROVEN**.

---

### 1. Vazifa va undan oldingi holat

PRD v0.5 §6 va §8 ikki jumlani birga yozadi:

    "Muddati o'tgan ish eskalatsiyasi | ≥ 95% | P6 talabi"
    "Muddati o'tgan ish menejerga eskalatsiya qilinadi."

`workforce.workload` (P9b) birinchi yarmini bajargan edi: u kechikkan ishni
**aniqlaydi** va ortidagi qatorlarni qaytaradi. Lekin uning natijasi — o'qish
natijasi, chaqiruvchining qo'lida qoladi. So'ramagan menejer hech qachon
bilmaydi. Ya'ni P6 — **yetkazib berish** yarmi edi.

Avval [V05M](V05-BLOCKS-OPS-UZ.md#v05m) (ERP posting) va undan oldingi bloklar
`reengagement` / `briefing` da o'rnatilgan **koordinator naqshini** bergan edi:
operator sozlaydi, `tick` chegaralangan manbani o'qiydi, har bir dedup kalitiga
bitta yetkazish ketadi. Bu blok o'sha naqshni **takrorlaydi**, lekin mavzu
**odamlar** bo'lgani uchun chegaralar boshqacha.

### 2. Ikki qat'iy chegara, va har biri **kodda majburlangan**

#### 2.1. Platforma xodimni **baholamaydi**

Bu **siyosat emas, struktura**. Modulda ball, reyting, rank, samaradorlik
indeksi yoki xodimlarni taqqoslash **yo'q**:

* `dir(module)` ni aylanib, `score|rank|rate|productivity|efficiency|kpi` nomli
  ommaviy funksiya **yo'qligi** tekshiriladi
  (`test_the_module_exposes_no_way_to_act_on_the_work`);
* yuborilgan **matn** ichida o'sha so'zlar (va `samaradorlik`, `reyting`)
  yo'qligi tekshiriladi (`test_the_escalation_never_evaluates_the_person`) —
  ya'ni hujjat emas, **natija** o'lchanadi;
* har bir xabar oxirida ochiq yoziladi: *"Bu faktlar ro'yxati — platforma
  xodimni baholamaydi va jazolash qarori qabul qilmaydi. Qaror menejerniki."*

Sabab PRD v0.5 §8 da yozilgan va bu yerda takrorlanadi: platforma **nima uchun**
vazifa kechikkanini bilmaydi. Odamni tartiblaydigan raqam — uni jazolash uchun
ishlatiladigan raqam. Shuning uchun modul **faktlarni** uzatadi (kim, qanday
vazifa, qaysi muddat, qanday holat) va hukmni menejerga qoldiradi.

#### 2.2. Faqat **o'qish + xabar** — ishga tegmaydi

`DELIVERY_TOOLS = frozenset({'telegram.send'})` — modul yeta oladigan **yagona**
yozuv. Ya'ni:

* vazifani **qayta tayinlamaydi**, **yopmaydi**, holatini **o'zgartirmaydi**;
* mijozga **yozmaydi** — qabul qiluvchi operator konfiguratsiyasi;
* shuning uchun **approval talab qilinmaydi**: `briefing` bilan bir xil
  "menejerga ayt" kanali. Fakt hisobotini approval ortiga yashirish
  operatorlarni refleks bilan tasdiqlashga o'rgatardi.

Va **muhim tuzilish qarori**: xabar yuborish **tool emas**. `escalation.preview`
va `escalation.schedules` — read; yetkazish `EscalationLoop.tick` ishi. Aks holda
har qanday `telegram.send` ga ega agent o'zi xabar ishlab chiqarardi va
dedup/cooldown chegarasi faqat **prompt** bilan ushlanardi. Hozir uni **kod**
majburlaydi.

### 3. Audit va probe tutgan haqiqiy nuqsonlar

Ikki nuqson topildi. Ikkalasi ham **testlar emas, o'lchov** tutgan — bu blokning
eng o'rgatuvchi qismi.

#### 3.1. `sent` qator cooldown o'tgach **qayta yuborilardi** (probe tutdi)

`_claim` `sent` va `failed` ni **bir xil** ko'rib chiqardi: cooldown o'tishi
bilan ikkalasi ham qayta urinilardi. Natijada **o'sha kechikkan vazifa har kuni
qayta eskalatsiya qilinardi** — vazifa kechikkan ekan. Menejer "Faktura #12
kechikdi" xabarini **har kuni** olardi, va bu kanalni o'chirishga olib keladigan
aynan shu naqsh.

Eng yomoni: modulning **o'z docstring'i** aksiyatni va'da qilardi. Buni probe
o'lchadi (`a delivered item is never re-sent after the cooldown` → FAIL), testlar
tutmadi. Tuzatish: `sent` — **umrbod terminal**; faqat `queued` (worker crash
qilgan, hech narsa yetkazilmagan) va `failed` (cooldown o'tgach) qayta uriniladi.
Bitta regressiya testi qo'shildi.

Bu **P8d bilan bir sinf**: testlar tutmadi, probe tutdi.

#### 3.2. Provider uzilishi **tutilmagan istisno** edi (audit tutdi)

`_read_overdue` `except (Conflict, ValueError, LookupError)` bilan o'ralgan edi.
Lekin Sheets transport nosozligi **`SheetsError`** ko'taradi — u `RuntimeError`
avlodidir. Ya'ni **provider uzilishi sikl ichida tutilmagan istisno** bo'lib
chiqardi: `escalation.cycle_failed` audit **yozilmasdan**, `tick()` yiqilardi.

Tuzatish: o'qish ataylab `except Exception` bilan o'raladi. Sabab — har qanday
"o'qish bo'lmadi" holatida javob **bir xil** bo'lishi kerak: xabar yuborilmaydi,
audit yoziladi, sikl qayta rejalashtiriladi. **Istisnoning faqat klass nomi**
yoziladi (`type(error).__name__`) — provider xabari range, spreadsheet id yoki
token aks ettirishi mumkin, va bu loyihaning qoidasi: provider matni — **ma'lumot**,
hech qachon provenance emas.

### 4. Dedup: kalit — **ish ob'ekti**, xodim emas

Kalit: `(tenant, schedule, person, task, due)`. Ya'ni bitta kechikkan vazifa
**bir marta** eskalatsiya qilinadi — menejer "bu faktura kechikdi" deb eshitadi,
"bu odam kechikdi" deb emas.

* `sent` — bu kalit uchun **umrbod terminal** (3.1 ga qarang);
* muddati **ko'chirilgan** vazifa — **yangi** kalit (yangi `due`), shuning uchun
  o'z asosida qayta eskalatsiya qilinadi: bu **boshqa** va'da buzilishi;
* juda eski (`max_age_days` dan oshgan) yozuv — **alohida yuborilmaydi**, lekin
  **sanaladi** va keyingi haqiqiy eskalatsiya hisobotiga qo'shiladi. Ya'ni
  doimiy kechikkan vazifa **iz qoldiradi**, lekin "1 ta yozuv eski" degan soatlik
  xabarga aylanmaydi — bu kanalni o'qilmaydigan qilardi.

### 5. Fail-closed: o'qilmagan reestr — "kechikkan yo'q" **emas**

`workforce` (P9b) bilan bir xil qoida, va bu blokda **hayotiy**:

* o'qish `workforce.workload` **oddiy handler** orqali ketadi — ya'ni reestr
  deklaratsiyasi, A1 allowlist, agent tool ruxsati va connection allowlist
  **o'zgarishsiz** qo'llanadi. Modul **yangi ma'lumot yo'li qo'shmaydi**;
* provider uzilganda sikl **xabar yubormaydi**, audit yozadi va
  **qayta rejalashtiradi** — jadvalni **o'chirmaydi**. Blipni doimiy sukunatga
  aylantirish noto'g'ri bo'lardi;
* ruxsat **bekor qilingan** bo'lsa (owner revoked) — bu boshqa holat: jadval
  **sabab bilan o'chiriladi** (`authority_revoked`), chunki u aks holda har
  siklda behuda aylanardi.

### 6. Hokimiyat har siklda **qayta tekshiriladi**

`configure` — owner-only (`require_authority(c, tenant, 'cron', actor, ('owner',))`).
Lekin bu **yetarli emas**: `tick` har safar `require_active` va owner rolini
**qaytadan** tekshiradi. Sabab — sozlash vaqtida ruxsat bor edi degani, **hozir**
bor degani emas. Freeze ham har siklda tekshiriladi.

Bu — `briefing` da o'rnatilgan qoida, va bu yerda **test bilan** qoplanadi:
`test_revoking_the_owner_stops_delivery_and_disables`.

### 7. Tekshiruv natijasi

| Tekshiruv | Natija |
|---|---|
| `test_escalation.py` | `Ran 45 tests ... OK` |
| `probe_escalation_boundary.py` | **PROVEN** (6 bo'lim) |
| `check_escalation_example.py` | `OK` |
| `check_capabilities_example.py` | `OK`, `registry tools: 74` |
| Registr | `build_registry() = 74` |
| `test_workforce.py` | 32 OK |
| `test_briefing.py` + `test_reengagement.py` + `test_oversight.py` + `test_supervisor.py` | 191 OK |
| To'liq to'plam | `Ran 1791 tests` — **failures=1, errors=149, skipped=1** |

Baseline **yettinchi marta aynan o'zgarmadi**: 1742 → 1791 = +49 yangi test, va
`failures=1, errors=149` — o'sha Windows-only `test_all_files_private`
(`AssertionError: 0 != 63`), boshqa hech narsa.

### 8. Ochiq qolgan

* **Live Telegram acceptance** — yo'q. `production_release` **NO_GO**.
* **UI** — yo'q. `escalation.schedules` va `escalation.preview` mavjud, lekin
  operator uchun boshqaruv paneli yozilmagan.
* **Yozuvni tasdiqlash (acknowledgement)** — yo'q. Menejer xabarni "ko'rdim" deb
  belgilay olmaydi; hozircha bu Telegram'ning o'zida qoladi.
* **`max_age_days`** — ataylab sukut bo'yicha 30 kun. Bu **sanaladi**, lekin
  alohida yuborilmaydi (4-bo'limga qarang) — kelajakda operatorga "eski
  yozuvlar" uchun alohida, kam chastotali hisobot kerak bo'lishi mumkin.

### 9. Xulosa

Platforma endi **faktni menejerga yetkazadi**, va bu uning **birinchi** marta
odamlar haqidagi ma'lumotni tashabbus bilan uzatishi. Ikki xossa, har biri
**o'lchangan**: platforma xodimni **baholamaydi** (strukturaviy + matn + ochiq
bayonot), va ishga **tegmaydi** (yagona yozuv — menejerga xabar, approval'siz).

Eng muhim dars yana **o'lchovda** chiqdi: `sent` qatorni cooldown o'tgach qayta
yuborish — modulning **o'z docstring'iga zid** edi, testlar tutmadi, probe
tutdi. Ya'ni docstring — dalil emas; xossani **o'lchash** kerak. Bu loyihaning
takrorlanuvchi darsi, va bu blokda u yana bir bor tasdiqlandi.


---

<a id="v05o"></a>

## V05O — Ishlab chiqarish o'qishlari (`manufacturing_bom`)

**Sana:** 2026-09-20
**Blok:** `manufacturing_bom` (P12 / T4)
**Holat:** **QURILDI VA O'LCHANDI** — lokal kontrakt darajasida. Live ERP
acceptance yo'q, shuning uchun `production_release` **NO_GO** bo'lib qoladi.
**Yangi tool:** 3 ta, **uchalasi ham read** — `manufacturing.bom`,
`manufacturing.cycle`, `manufacturing.yield`. `build_registry()` **74 → 77**.
**Yangi test:** `runtime_tests/test_manufacturing.py`, **64** test.
**Yangi probe:** `scripts/probe_manufacturing_boundary.py` — 6 bo'lim,
**22 o'lchangan xossa**, hammasi PASS.

---

### 1. Vazifa va undan oldingi holat

PRD v0.5 §8 Jidoka kameralarining ERP'ga uzatadigan to'rt narsasini sanaydi:

    "OEE availability and performance metrics per line and shift, SOP deviation
     events with timestamp and station ID, cycle time per unit, quality alert
     flags"

P11 (`assets`) har bir mashinaga **joy** bergan edi. P11b (`vision`) har bir
hodisaga **voqea** bergan edi — va `vision.summary` da OEE'ni ataylab rad etib,
"bu P13 ishi" deb yozgan edi. Lekin dushanba kuni ishlab chiqarish menejeri
so'raydigan savolga hali javob yo'q edi:

> **Biz nima ishlab chiqardik, nimadan, va qanchasi yaroqli chiqdi?**

Bu blok — o'sha javob, va u ataylab mijozning **o'z registrlari** ko'tara oladigan
**eng kichik** javob.

### 2. Uchta qat'iy rad etish, har biri **kodda majburlangan**

#### 2.1. OEE **hisoblanmaydi** — P13 egasi

To'rt narsadan faqat **uchtasi** sondan yasaladi:

| PRD elementi | Bu blokda | Nega |
|---|---|---|
| cycle time per unit | ✅ `manufacturing.cycle` | standart deklaratsiya qilinsa |
| yield | ✅ `manufacturing.yield` | chiqim **va** nuqson o'qilganda |
| SOP deviation / idle | ⚠️ soni sanaladi | bu **hodisa**, P11b uni sinfi bo'yicha o'qiydi |
| **OEE availability/performance** | ❌ **rad etiladi** | rejalashtirilgan ish vaqti va ideal sikl vaqti bu registrlarda **yo'q** |

`vision.summary` OEE'ni allaqachon rad etgan edi. Bu modul **o'sha rad etishni
saqlaydi** — mavjud bo'lgan narsadan availability raqamini o'ylab topish o'rniga.
Probe buni **o'lchaydi**: har bir view'ning har bir kaliti va har bir yozuvi OEE
va baholovchi nomlar uchun tekshiriladi, va natija **0**.

Diqqat: test **kalitlarni** tekshiradi, `note` matnini emas. Sabab — `note`
ataylab OEE so'zini **rad etish uchun** ishlatadi ("Bu OEE emas — OEE uchun
rejalashtirilgan ish vaqti kerak (P13)"), shuning uchun butun payload'ni qidirish
rad etishni oqlaydigan izohning o'zida yiqilardi. Bu P11b da o'rnatilgan naqsh.

#### 2.2. Chiqim **bitta sondan** hisoblanmaydi

Agar chiqim o'qilsa-yu nuqson o'qilmasa (ustun deklaratsiya qilinmagan, yoki
hamma katak bo'sh) — stansiya `yield: None` va `yield_computable: false` bilan
qaytadi. **Nol nuqson deb faraz qilish** yarmini brakka chiqarayotgan liniya uchun
**100%** chop etardi.

Uchta holat, uchalasi ham probe bilan o'lchangan:

* nuqson ustuni **yo'q** → `yield: None`;
* nuqson kataklari **bo'sh** → `yield: None`;
* chiqim **0** → `yield: None` (nolga bo'lishning ma'nosi yo'q; 0% ham, 100% ham
  uydirma son);
* nuqson **chiqimdan katta** → `yield: None` **va** `defect_exceeds_output` sanaladi.

Oxirgisi muhim: nuqson > chiqim — bu **ma'lumot xatosi**, va uni jimgina 0% ga
aylantirish xatoni yashirardi. Menejer ko'rishi kerak.

#### 2.3. Xodim bo'yicha ko'rsatkich **mavjud emas**

Bu **siyosat emas, struktura**: `operator_column`, `shift_column`, `team_column`
yoki shunga o'xshash **birorta konfiguratsiya kaliti yo'q**. Agar mijozning
jadvalida operator ustuni bo'lsa ham, u **hech qachon o'qilmaydi** — modul faqat
o'ziga aytilgan ustunlarni o'qiydi.

Probe buni **qiyinlashtiradi**: u registrga ataylab `operator` va `brigada`
ustunlarini soladi (Ali Valiyev, Husan Rahimov, Zilola Karimova) va moduldan
odam raqami chiqarishga **urinadi**. Natija: kalit ham, qiymat ham **sızmadi**.

Sabab o'zgarmaydi: odamga bog'langan chiqim raqami — **sababi platformada
bo'lmagan jazo**. Platforma partiya nega brak bo'lganini bilmaydi.

### 3. Audit va probe tutgan haqiqiy nuqsonlar

**Ikki nuqson** topildi, va ikkalasi ham **probe tutdi, testlar tutmadi** — bu
loyihaning takrorlanuvchi darsi yana tasdiqlandi.

#### 3.1. Bo'sh chiqim katagi **uydirma 0.0 stansiya** yaratardi (probe tutdi)

`yield_report` da `setdefault` **o'qiladiganlik tekshiruvidan oldin** ishlardi:

```python
output = _number(output_raw)
if _text(output_raw, 32).strip() and output is None:   # bo'sh '' bu yerga tushmaydi
    unreadable_out += 1
    continue
record = by_station.setdefault(name, {...'output': 0.0...})   # <-- yaratilardi
if output is not None:
    record['output'] += output
```

Bo'sh katak `''` → `_number('')` = `None` → lekin `''.strip()` **bo'sh**, ya'ni
guard ishlamaydi → `setdefault` **0.0 bilan yozuv yaratadi** → natijada
"liniya hech narsa ishlab chiqarmadi" degan **uydirma** raqam chiqadi.

Bu modulning **o'z docstring'iga zid**: "a blank cell that becomes zero would
understate output and inflate a yield". Probe buni o'lchadi
(`a register of blanks produced no zero-output station` → FAIL, 2 ta uydirma),
testlar tutmadi.

**Tuzatish:** `output is None` bo'lsa **umuman yozuv yaratilmaydi** — na bo'sh
katak, na o'qilmaydigan matn. Bo'sh va matnli katak **alohida** sanaladi
("yozilmagan" va "o'qilmagan" menejer uchun boshqa gap), lekin hech biri nol
bo'lmaydi.

#### 3.2. O'qilmaydigan nuqson katagi **chiqimni summasidan yo'qotardi** (probe tutdi)

Nuqson tarmog'ida `continue` **yozuv yaratilgandan keyin** turardi:

```python
defect = _number(defect_raw)
if defect is None and _text(defect_raw, 32).strip():
    unreadable_defect += 1
    continue        # <-- shu qatorning o'qilgan chiqimi RAQQ ORASIDA tashlanadi
```

Ya'ni bir katakdagi matn butun qatorning **o'qilgan chiqimini** summasidan
chiqarib tashlardi — stansiya o'z ishlab chiqarishini jimgina **yo'qotardi**.

**Tuzatish:** o'qilmaydigan nuqson katagi chiqimni **yo'qotmaydi**; u stansiyani
`defect_unreadable` deb belgilaydi va bu **butun stansiyani hisoblanmaydigan**
qiladi. Sabab: o'qiladigan kataklarni yig'ib, o'qilmaydiganini e'tiborsiz
qoldirish nuqsonni **kam ko'rsatib**, chiqimni **shishirardi**.

#### 3.3. Probe'ning o'z xatosi (bittasi)

Probe'ning 5-bo'limi "zavod-1 ning o'z stansiyasi 100 ko'rsatadi" deb kutgan edi,
lekin fixture'da zavod-1 ning **ikkita** stansiyasi bor (100 va 200). Bu **probe
xatosi**, modul xatosi emas — tuzatildi va kutilmagan holat ham qo'shildi
(zavod-10 ning 50'si zavod-1 totallarida **yo'qligi** alohida o'lchanadi).

### 4. Bo'sh katak ≠ 0, va bu **o'lchangan**

Loyihaning eng ko'p takrorlanadigan qoidasi, bu blokda **ikki tomondan**
tekshiriladi:

* bo'sh chiqim kataklari **stansiya yaratmaydi** — 0 uydirilmaydi;
* bo'sh katak **"o'qilmagan" deb ham sanalmaydi** — bu menejer uchun boshqa gap;
* **deklaratsiya qilingan 0 esa haqiqiy 0 bo'lib qoladi** — bu ham alohida test.

Uchinchisi muhim: agar tuzatish "0 ni ham tashlab yuborish" ga borsa, u **haqiqiy
nolni** yo'qotardi va bu teskari nuqson bo'lardi. Ikkalasi ham test bilan
qo'yilgan (`test_a_row_with_no_readable_output_creates_no_station`,
`test_a_declared_zero_output_still_creates_a_station`).

Qo'shimcha: **o'nlik vergul** (`12,5`) o'nlik nuqta sifatida o'qiladi. O'zbek
jadvalidagi haqiqiy ma'lumot shunday, va uni rad etish **xato** bo'lardi.

### 5. Moslashish — **segment bo'yicha**, string prefiks emas

P11 qoidasi **qayta ishlatiladi**, qayta yozilmaydi:

```python
def _bind(tenant, station):
    try:
        segments = assets.parse_path(tenant, station)
    except (ValueError, Forbidden):
        return None
    return assets.format_path(segments)
```

Ya'ni `zavod-1` hech qachon `zavod-10` ning chiqimini o'zlashtira olmaydi —
`'zavod-10'.startswith('zavod-1')` **True** bo'lsa ham.

Va muhim **chiqish shakli qarori**: `path` **faqat haqiqatan bog'langanda**
qo'shiladi. Ilgari u `'path': _bind(...) or ''` edi — ya'ni bog'lanmagan stansiya
`""` ko'rsatardi, va bo'sh satrni "bu stansiyaning **joyi yo'q**" deb o'qish
mumkin edi. Hozir bog'lanmagan stansiyada `path` kaliti **umuman yo'q**, va
`unbound` soni buni **ko'rinadigan** qiladi — jimgina emas.

Bu P11b bilan bir xil qoida: "an event bound to the wrong plant is worse than an
unbound one a manager can see".

### 6. Fail-closed: o'qilmagan registr — "ishlab chiqarish yo'q" **emas**

* o'qish `sheets.rows` **oddiy handler'i** orqali ketadi — registr deklaratsiyasi,
  A1 allowlist, agent tool ruxsati va connection allowlist **o'zgarishsiz**
  qo'llanadi. Modul **yangi ma'lumot yo'li va yangi vakolat qo'shmaydi**;
* provider uzilganda istisno **yuqoriga chiqadi** — `complete: True` bilan bo'sh
  chiqim **qaytmaydi**. Sabab: "zavod to'xtagan" va "biz o'qiy olmadik" bir xil
  ko'rinmasligi kerak;
* `sheets.rows` agent ro'yxatida bo'lmasa — **Forbidden**;
* registr deklaratsiya qilinmagan bo'lsa — **Forbidden** (bo'sh zavod emas);
* bir view uchun **ikkita registr** bo'lsa — **Forbidden** (noaniq);
* faqat `yield` registri bor tenant `cycle` o'qiy olmaydi — **Forbidden**.

### 7. Tekshiruv natijasi

| Tekshiruv | Natija |
|---|---|
| `test_manufacturing.py` | `Ran 64 tests ... OK` |
| `probe_manufacturing_boundary.py` | **22 o'lchangan xossa, hammasi PASS** (6 bo'lim) |
| `check_manufacturing_example.py` | `OK` — modulning **o'z** validator'i ishlatiladi |
| `check_capabilities_example.py` | `OK`, `registry tools: 77`, `unknown: []` |
| Registr | `build_registry() = 77` |
| To'liq to'plam | `Ran 1855 tests` — **failures=1, errors=149, skipped=1** |

Baseline **sakkizinchi marta aynan o'zgarmadi**: 1791 → 1855 = +64 yangi test, va
`failures=1, errors=149` — o'sha Windows-only `test_all_files_private`,
boshqa hech narsa.

### 8. Ochiq qolgan

* **OEE (P13)** — bu blok **ataylab** hisoblamaydi. `oee_and_andon` bloki
  rejalashtirilgan ish vaqti va ideal sikl vaqtini talab qiladi; ular hozircha
  hech qaysi registrda deklaratsiya qilinmagan.
* **SOP deviation / idle span** — P11b hodisa sifatida o'qiydi; bu blok ularni
  **qayta yasamaydi**, faqat deklaratsiya qilingan sinfni sanaydi.
* **Ko'p bosqichli BOM portlashi** — yo'q, ataylab: bu komponentning qaysi
  qatlamdan kelganini yashirardi.
* **Tannarx** — yo'q, ataylab: narx manbasi deklaratsiya qilinmagan.
* **Live ERP acceptance** — yo'q. `production_release` **NO_GO**.
* **UI** — yo'q. Uchta tool mavjud, operator uchun panel yozilmagan.

### 9. Xulosa

Platforma endi dushanba savoliga javob beradi — **nima yasaldik, nimadan, va
qanchasi yaroqli chiqdi** — va javobning har bir raqami **o'zi bilan kelgan
kirishlarni** olib yuradi: namuna soni, o'qilmagan soni, taqqoslangan/taqqoslanmagan
holati. Denominatori ko'rinmaydigan foiz — bu **ertami-kechmi xato bo'ladigan va
hech qachon so'ralmaydigan** son.

Va yana bir bor **o'lchov** hikoyani yozdi: ikkita nuqson topildi, ikkalasi ham
modulning **o'z docstring'iga zid** edi (bo'sh katak nolga aylanishi, o'qilmaydigan
katak chiqimni yeb qo'yishi), ikkalasini ham **probe** tutdi, testlar tutmadi.
Ya'ni docstring — dalil emas; xossani **o'lchash** kerak. Bu loyihaning
takrorlanuvchi darsi, va bu blokda u **yana bir bor** tasdiqlandi.


---

<a id="v05p"></a>

## V05P — OEE va andon (`oee_and_andon`)

**Sana:** 2026-09-20
**Blok:** `oee_and_andon` (P13 / T4) — **T4 ning oxirgi bo'lagi**
**Holat:** **QURILDI VA O'LCHANDI** — lokal kontrakt darajasida. Live ERP
acceptance yo'q, shuning uchun `production_release` **NO_GO** bo'lib qoladi.
**Yangi tool:** 2 ta, **ikkalasi ham read** — `oee.report`, `oee.andon`.
`build_registry()` **77 → 79**.
**Yangi test:** `runtime_tests/test_oee.py`, **63** test.
**Yangi probe:** `scripts/probe_oee_boundary.py` — 6 bo'lim,
**78 o'lchangan xossa**, hammasi PASS.

---

### 1. Vazifa va undan oldingi holat

P12 bloki OEE'ni **nomlab rad etgan** edi:

> "Availability and performance need planned run time and ideal cycle time,
>  which are not in these registers. P13 owns OEE."

Va `vision.summary` ham xuddi shunday rad etgan edi ("bu P13 ishi"). Bu blok —
o'sha topshiriqning **bajarilishi**. PRD v0.5 §8 Jidoka kamerasining birinchi
elementi aynan shu:

    "OEE availability and performance metrics per line and shift"

### 2. Nega P12 hisoblay olmadi — va bu blok nima qiladi

OEE — uchta nisbatning ko'paytmasi:

    availability = ish_vaqti / rejaviy_ish_vaqti
    performance  = (ideal_sikl * ishlab_chiqarilgan) / ish_vaqti
    quality      = yaroqli / ishlab_chiqarilgan
    OEE          = availability * performance * quality

P12 `ishlab_chiqarilgan` va `yaroqli` ni o'qiy oladi (bu uning **yield**'i).
Lekin `rejaviy_ish_vaqti` va `ideal_sikl` — **o'lchov emas, STANDART**. Standart
esa mijoz **deklaratsiya qiladigan** narsa, platforma kuzatuvlardan **yasab
oladigan** son emas. O'z maxrajini o'zi o'ylab topgan platforma — zavodni
**o'zi ixtiro qilgan me'yor** bilan baholaydi.

Shuning uchun qoida "OEE hisobla" emas:

> **Uchta faktordan har biri faqat deklaratsiya qilingan kirishlardan
> hisoblanadi, va kirishi yo'q bo'lsa — nomlab rad etiladi, hech qachon
> standart qiymat olmaydi.**

### 3. To'rtta halol natija, bitta mo'rt son emas

| Holat | Natija |
|---|---|
| uchala faktor hisoblanadi | `oee` — son, yonida uchala faktor va **oltita kirish** |
| birorta faktor hisoblanmaydi | `oee: None`, hisoblanadigan faktorlar **saqlanadi**, `not_computable` aynan qaysi kirish yo'qligini **nomlaydi** |
| reja yo'q, ideal bor | availability `None`, performance esa **hisoblanadi** (quyida) |
| hech narsa deklaratsiya qilinmagan | modul **baribir o'qiydi** va har bir yo'q kirishni nomlaydi |

#### 3.1. performance rejani **talab qilmaydi** — bu ataylab

    performance = ideal * chiqim / ish        ← uchta O'LCHANGAN qiymat

Shuning uchun ideal sikl vaqtini deklaratsiya qilib, smena rejasini
deklaratsiya qilmagan mijoz **haqiqiy** performance va quality raqamini oladi,
OEE esa rad etiladi. Bu butun o'qishni rad etishdan **qat'iy ko'proq ma'lumot**.

Bu blokda **tuzatilgan hujjat xatosi**: modul sarlavhasi ham, test ham dastlab
performance rejaga bog'liq deb yozgan edi. Kod to'g'ri edi, **hujjat xato** edi.
Test `test_no_plan_declared_refuses_availability_but_not_performance` deb
qayta nomlandi.

#### 3.2. Qisman OEE **raqam sifatida ko'rsatilmaydi**

Yarmi o'lchangan, yarmi standart qiymat bilan to'ldirilgan indeks — bu P12
sarlavhasi ogohlantirgan "ertami-kechmi xato bo'ladigan va hech qachon
so'ralmaydigan son", va u **to'liq ko'rinadi**, shuning uchun yo'qligidan
**yomonroq**.

`not_computable` ikki xil sababni **ajratadi**, chunki tuzatish har xil amal:

* **`absent`** — kiritma deklaratsiya qilinmagan, bo'sh, yoki o'qilmaydigan.
  Tuzatish: **uni deklaratsiya qilish**;
* **`unusable`** — kiritma **bor**, lekin ishlatib bo'lmaydi. Masalan `produced: 0`
  (nolga bo'lish), yoki `good > produced` (ma'lumot xatosi). Tuzatish: **ma'lumotni
  tuzatish**, va kiritmani "yo'q" deb nomlash operatorni **mavjud** ustunni
  qidirishga yuborardi.

### 4. Rad etishlar, har biri **kodda majburlangan**

#### 4.1. `good > produced` **347% OEE** berardi (probe tutdi)

Dastlab `_factor_quality` faqat `produced <= 0` ni rad etardi. `good = 9999`,
`produced = 2400` bo'lsa `quality = 4.1662` chiqardi, va OEE **3.4719** — ya'ni
**347%**.

Bu modulning **o'z docstring'iga zid** edi, va xuddi P12 dagi nuqson naqshi:
o'lchov emas, bayonot ishlatilgan edi.

**Tuzatish:** `_factor_quality` endi `good > produced` ni **rad etadi**
(`None` qaytaradi), va `report` buni `good_exceeds_produced` hisoblagichi orqali
**ko'rinadigan** qiladi — xuddi P12 nuqson sonini chiqimdan katta bo'lganda
qisqartirmasdan ko'rsatgani kabi.

#### 4.2. `produced == 0` — "yo'q" emas, **ishlatib bo'lmaydigan**

Nolga bo'lishning ma'nosi yo'q; 0% ham, 100% ham **uydirma**. Lekin kiritma
**bor** — shuning uchun `unusable: ['produced']`, `absent` emas.

#### 4.3. `run > plan` **1.0 ga qisqartirilmaydi**

Rejadan uzun ishlangan vaqt — bu **rejalashtirish nuqsoni** (reja 8 soat deydi,
registr 9 soat ishlangan deydi). Uni 100% ga qisqartirish nuqsonni **mukammal
son ortiga yashirardi**. Probe buni o'lchaydi: `availability > 1.0` bo'lib
qoladi (fixture'da **5.0**).

#### 4.4. performance **1.0 ga ko'tarilmaydi**

performance — bu **nisbat emas, tezlik**. Liniya me'yor ko'rsatgan tezlikdan
sekin ishlaganda u 1.0 dan **past** bo'ladi, va bu **normal holat**, xato emas.
Probe buni alohida o'lchaydi (fixture'da `produced=100`, `run=26000`,
`ideal=10` → `performance = 0.0385`, va u **aynan shunday** hisobotlanadi).

### 5. Andon — faqat **aniqlash** yarmi

"Poka-yoke" PRD atamalarida — **andon shnuri**: menejerga **so'ralmasdan**
yetib boradigan chegara buzilishi. Bu modul **aniqlash** yarmini beradi va
**yetkazish** yarmini **P6 eskalatsiya koordinatoridan qayta ishlatadi**,
ikkinchi xabar yo'lini o'stirish o'rniga.

* `oee.andon` deklaratsiya qilingan chegaralarni o'qiydi va qaysi stansiyalar
  buzganini **o'lchangan qiymat** va **kesib o'tilgan chegara** bilan qaytaradi
  — **fakt, hukm emas**. Alerting — koordinator amali, tool emas, shuning uchun
  model **na signal yasay oladi, na uni bosa oladi**;
* chegara — **operator konfiguratsiyasi**. Platforma 85% ni "yomon OEE" deb
  **hal qilmaydi**; mijoz nima haqida xabardor bo'lishni **o'zi** hal qiladi;
* chegara umuman deklaratsiya qilinmagan bo'lsa — `andon` **nomlab rad etadi**.
  U taqqoslaydigan chegara **o'ylab topmaydi**, va hech kim so'ramagan
  taqqoslashni **hisobot qilmaydi**.

#### 5.1. Hisoblanmaydigan stansiya — **signal emas**

Agar stansiyaning OEE'si hisoblanmasa, u **`not_evaluated`** bo'lib sanaladi va
**hech qanday buzilish ko'tarmaydi**. Sabab: yo'q ustun nomi menejerni
**ishlamayotgan liniya** haqida ogohlantirmasligi kerak. Probe buni o'lchaydi.

#### 5.2. Bitta deklaratsiya — bitta buzilish

Probe buni **izolyatsiyada** o'lchaydi: bitta deklaratsiya qilingan metrika bir
marta kesilsa — **bitta** buzilish; ikkitasi kesilsa — **ikkita**. Ya'ni qo'riqchi
"jimgina o'chirilgan" ham emas, va bitta metrika boshqasini **shishirmaydi** ham.

### 6. Audit va probe tutgan haqiqiy nuqsonlar

#### 6.1. Hujjat **kodga zid** edi: reja ustuni vs konstantasi (probe + checker tutdi)

Modul sarlavhasi va `planned_run_seconds` hujjati shunday da'vo qilardi:

> "Declaring both is a **refusal**, not a precedence rule: two sources for one
>  denominator is an ambiguity, and the platform will not pick one silently."

**Kod buni umuman qilmasdi.** O'lchangan haqiqiy xatti-harakat:

| Deklaratsiya | Katak | Natija |
|---|---|---|
| ustun + konstanta | `28800` | `planned = 28800` (ustun) |
| ustun + konstanta | `10000` | `planned = 10000` (ustun), availability `2.6` |
| ustun + konstanta | **bo'sh** | `planned = 28800` (konstantadan **fallback**) |
| faqat konstanta | `''` | `planned = 28800` |

Ya'ni haqiqiy qoida: **ustun ustunlik qiladi, konstanta bo'sh katak uchun
zaxira**. Bu **mantiqiy va yaxshi dizayn** ("har satr qiymati + stansiya
standarti" — kichik zavodlar rejani aynan shunday biladi). Lekin **hujjat
buning aksini** aytardi, va men **o'zim yozgan** config misoli hamda checker ham
o'sha noto'g'ri da'voni kodlagan edi.

Bu loyihaning **haqiqiy nuqson sinfi**: *hujjat dalil emas*. Va bu safar nuqson
kodda emas — **hujjatda** edi, ya'ni kodni "tuzatish" noto'g'ri bo'lardi.

**Tuzatish:** modul sarlavhasiga aniq **"Planned run time: column and constant,
and which one wins"** bo'limi qo'shildi (o'lchangan qoida bilan), kod izohi va
config misoli tuzatildi, checker endi **rad etishni emas, qabul qilinishini**
o'lchaydi, va probe **uchta** holatni (ustun ustunligi, bo'sh katak fallback,
o'qilmaydigan katak na-na) o'lchaydi. Uchta yangi test qo'shildi.

**Refusal chegarasi o'zgarmadi va u aniq:** rad etish — **ikkita registr** bir
o'qish uchun deklaratsiya qilinganda (`test_two_registers_for_one_read_are_ambiguous_and_refused`).

#### 6.2. `_REGISTER_KEYS` da `planned_run_seconds` yo'q edi

Validator `ValueError: oee.registers line has unsupported keys:
['planned_run_seconds']` berardi — ya'ni **konstanta funksiyasi umuman
yetib bo'lmas** edi. Qo'shildi.

#### 6.3. Bo'sh reja katagi konstantaga **tushmasdi**

Konstanta fallback faqat `elif` tarmog'ida ishlardi, shuning uchun ustun
deklaratsiya qilinib katak **bo'sh** bo'lsa — fallback ishlamasdi. Tuzatildi:
bo'sh katak standartga **tushadi**, lekin **matnli** katak baribir sanaladi.

#### 6.4. Probe'ning o'z ikki xatosi

P12 dagi kabı, probe ham o'z xatosini qildi, va ikkalasi ham **modul xatosi
emas** edi:

* **2-bo'lim performance tekshiruvi** — probe `performance <= (ideal*produced)/run`
  deb chegarani noto'g'ri o'qigan edi. Aslida `produced=100` uchun
  `10*100/26000 = 0.0384615`, ya'ni modulning **0.0385** qiymati **to'g'ri**.
  Probe tenglikni (aniqlik doirasida) o'lchaydigan qilib tuzatildi, va ustiga
  "past tezlik 1.0 ga ko'tarilmagani" ham qo'shildi;
* **4-bo'lim buzilish soni** — probe fixture'da `oee` **va** `availability`
  chegarasini deklaratsiya qilgan edi, shuning uchun **ikkita** buzilish chiqdi
  (availability 0.9028 < 0.95). Probe "1 kutilgan" deb o'lchardi — bu **probe
  xatosi**. Tuzatildi: endi bitta metrika izolyatsiyada **bitta**, ikkitasi
  **ikkita** buzilish chiqarishi alohida o'lchanadi.

### 7. Bo'sh katak ≠ 0, va bu **o'lchangan**

Loyihaning eng ko'p takrorlanadigan qoidasi, bu blokda ham ikki tomondan:

* bo'sh reja katagi + konstanta → konstantaga **tushadi** (rad etilmaydi);
* bo'sh reja katagi + konstanta **yo'q** → `planned: None`, availability rad
  etiladi, `unreadable` sanaladi;
* **matnli** katak → hech qaysi manba ishlatilmaydi, `unreadable` sanaladi;
* deklaratsiya qilingan **haqiqiy 0** esa — `unusable` (uydirma emas).

O'nlik vergul (`12,5`) o'nlik nuqta sifatida o'qiladi — o'zbek jadvalidagi
haqiqiy ma'lumot shunday.

### 8. Xodim bo'yicha ko'rsatkich **mavjud emas**

Bu **siyosat emas, struktura**: `operator_column`, `shift_column`, `team_column`
yoki shunga o'xshash **birorta kalit yo'q**. Probe registrga ataylab `operator`
va `brigada` ustunlarini soladi va moduldan odam raqami chiqarishga **urinadi**
— natija: kalit ham, qiymat ham **sızmadi**, andon payload'ida ham.

Sabab: **stanokga** bog'langan OEE — texnik xizmat signali; **o'sha raqam
smenadagi operatorga** bog'lansa — **jazo**. Platforma partiya nega brak
bo'lganini bilmaydi.

### 9. Fail-closed

* o'qish `sheets.rows` **oddiy handler'i** orqali ketadi — registr deklaratsiyasi,
  A1 allowlist, agent tool ruxsati va connection allowlist **o'zgarishsiz**.
  Modul **yangi ma'lumot yo'li va yangi vakolat qo'shmaydi**;
* provider uzilganda istisno **yuqoriga chiqadi** — "zavod to'xtagan" va
  "biz o'qiy olmadik" bir xil ko'rinmasligi kerak;
* registr deklaratsiya qilinmagan → **Forbidden**;
* bir o'qish uchun **ikkita registr** → **Forbidden** (noaniq);
* chegara deklaratsiya qilinmagan → `andon` **Forbidden** (o'ylab topilgan
  chegara emas).

### 10. Tekshiruv natijasi

| Tekshiruv | Natija |
|---|---|
| `test_oee.py` | `Ran 63 tests ... OK` |
| `probe_oee_boundary.py` | **78 o'lchangan xossa, hammasi PASS** (7 bo'lim) |
| `check_oee_example.py` | `OK` — modulning **o'z** validator'i ishlatiladi |
| `check_capabilities_example.py` | `OK`, `registry tools: 79`, `unknown: []` |
| Registr | `build_registry() = 79` |
| To'liq to'plam | `Ran 1934 tests` — **failures=1, errors=149, skipped=1** |

Baseline **to'qqizinchi marta aynan o'zgarmadi**: 1855 → 1934 = +79 yangi test, va
`failures=1, errors=149` — o'sha Windows-only `test_all_files_private`,
boshqa hech narsa.

### 11. Ochiq qolgan

* **Live ERP acceptance** — yo'q. `production_release` **NO_GO**;
* **Smena kalendari** — modul uni **tanlamaydi**: rejaviy ish vaqti stansiya
  bo'yicha konstanta yoki deklaratsiya qilingan ustun. Kalendar arifmetikasi
  zavod nechta smena ishlashini platforma **hal qilishini** bildirardi;
* **Idle vaqtni o'zi klassifikatsiya qilmaydi** — deklaratsiya qilingan sinfni
  sanaydi;
* **Alerting delivery** — P6 koordinatori; bu modul faqat **aniqlaydi**;
* **UI** — yo'q. Ikki tool mavjud, operator uchun panel yozilmagan;
* **T4 tugadi** — keyingi blok **P14 `telephony_outbound`**.

### 12. Xulosa

P12 OEE'ni "hisoblash mumkin emas" deb topshirgan edi; bu blok uni **halol
hisoblaydi** — lekin faqat mijoz **deklaratsiya qilgan** me'yorlar bo'yicha, va
har bir raqam o'zi bilan **kim ishlab chiqargan kiritmalarni** olib yuradi.
Maxraji ko'rinmaydigan foiz yo'q; qisman indeks yo'q; xodim bo'yicha raqam yo'q.

Va yana bir bor **o'lchov** hikoyani yozdi — lekin bu safar yangi shaklda:
uchta nuqson topildi, va ulardan bittasi **kodda emas, hujjatda** edi. Hujjat
kodning aksini aytardi. Ya'ni "docstring — dalil emas" qoidasi ikki tomonlama
ishlaydi: **kodni hujjatga ishonib qabul qilish ham, hujjatni kodga ishonib
qabul qilish ham** xato. Faqat **o'lchov** haqiqatni aytadi.


---

<a id="v05q"></a>

## V05Q — Qo'ng'iroq hodisalari va rozilik darvozasi (`telephony_outbound`, A bosqich)

**Sana:** 2026-09-20
**Blok:** `telephony_outbound` (P14 / T4) — **A bosqich: hodisalar + rozilik darvozasi**
**Holat:** **QURILDI VA O'LCHANDI** — lokal kontrakt darajasida. Live provayder
acceptance yo'q, `production_release` **NO_GO** bo'lib qoladi.
**Yangi tool:** 3 ta, **hammasi read** — `telephony.consent`,
`telephony.call_events`, `telephony.summary`. `build_registry()` **79 → 82**.
**Yangi test:** `runtime_tests/test_telephony.py`, **57** test (54 + audit
tuzatishlarining 3 regressiya testi).
**Yangi probe:** `scripts/probe_telephony_consent.py` — 7 bo'lim,
**69 o'lchangan xossa**, hammasi PASS.
**Yangi config:** `config/telephony.example.json` + `scripts/check_telephony_example.py`.

---

### 1. Nega bu blok eng oxirgi

P14 — PRD yuzasining **oxirgi qismi**, va u ataylab oxirgi. BACKLOG buni uch
sabab bilan yozgan:

1. **Og'ir** — real vaqtli STT/TTS, codec, SIP, concurrency limitlari;
2. **Huquqiy jihatdan ochiq** — yozib olishga rozilik va saqlash muddati
   *operatorning* majburiyati (PRD-04 VO-07), mahsulot funksiyasi emas;
3. **Sifatga sezgir** — o'zbek shevasi va ruscha aralash STT ni buzadi
   (PRD-04: WER ≤15%, ism/raqam ≥98%, live p95 <2.5s — bular **maqsad**, o'lchangan
   natija emas).

Shuning uchun bu bosqichda **dialer yozilmaydi**. Yoziladi — hech qanday keyingi
dialer chetlab o'tolmaydigan **qism**: **qo'ng'iroq hodisalarini o'qish** va
**rozilik darvozasi**. Sabab oddiy: rozilik darvozasini **keyin qo'shib
bo'lmaydi** — u kodda bo'lishi kerak, chunki roziliksiz qo'ng'iroq sifat emas,
**huquq** masalasi.

### 2. Ikki qoida, bajariladigan shaklda

#### 2.1 Audio hech qachon platformaga yetib kelmaydi

Bu — vizyon (P11b) va v0.4 qoidasining aynan o'zi ("audio hech qachon plannerga
yetib kelmaydi, faqat transkript"), lekin bu yerda u **strukturaviy**:

* hech qanday tool argumenti, config kaliti yoki chiqish maydoni yozuvni,
  yozuv havolasini, transkriptni yoki PCM buferini tashimaydi;
* modul **media o'qish yuzasini import ham qilmaydi** (`AishaREST`, `synthesize`,
  `transcribe`, `urllib` — hech biri yo'q; test buni o'lchaydi);
* registr — operator yozadigan **QO'NG'IROQ JURNALI**, media ombori emas.

**Probe topgan nuqson (kodda tuzatildi).** Dastlab modul `outcome_column` ni
"shaffof matn" deb o'qirdi. Probe ko'rsatdiki, ustunni media ustuniga
yo'naltirsa, **yozuv URL i `outcome` sifatida qayta chop etiladi**:

```json
{"outcome": "https://media.example/rec-9.wav"}
```

Ya'ni "audio hech qachon yetib kelmaydi" degan chegara **operator ustun nomini
tanlashiga bog'liq** edi. Tuzatildi: **lokator shaklidagi katak ushlanadi** —
sxemali URL (`scheme://`, `//`, `data:`, `blob:`) yoki media kengaytmali katak
(`.wav/.mp3/.opus/.webm/...`) — va `outcome` **bo'sh** qaytadi, katak esa
`withheld_outcomes` da **sanaladi**. Filtr **tor**: oddiy natija so'zi
(`answered`, `no_answer`) o'zgarmaydi.

#### 2.2 Deklaratsiya qilinmagan raqamga qo'ng'iroq qilish — kodda rad etiladi

PRD rozilikni operator majburiyati qiladi (VO-07). Platformada bu — vizyonning
biometrik darvozasi va WhatsApp oynasining aynan o'sha **deklaratsiya + rad
etish** naqshi:

* operator rozilikni **deklaratsiya qiladi** (tenant siyosati + raqam bo'yicha
  yozuv);
* **deklaratsiyasiz raqamga qo'ng'iroq — kodda, nom bilan rad etiladi**;
* **yozib olish** — **alohida** rozilik talab qiladi, va u yo'q bo'lsa
  `recording_allowed: false`, "yozuv yo'q" emas — platforma yozuv borligini
  **bilmaydi** va yo'qlikni yozuv haqidagi dalil qilib o'qimaydi.

**Nega prompt emas, kod.** Roziliksiz odamga qo'ng'iroq — sifat muammosi emas,
**huquqiy** muammo, va prompt majburlash mexanizmi emas. Aynan shu argument
eskalatsiya koordinatori yetkazish uchun aytadi: faqat model ko'rsatmalarida
yashaydigan qoidani yetarlicha ishonchli kirish **olib tashlaydi**.

`telephony.consent` — savolga javob beradigan **read** tool: *"bu raqamga
qo'ng'iroq qilsa bo'ladimi, va yozib olsa bo'ladimi"*. Noma'lum raqam
**istisno emas, sabab bilan `consented: false`** qaytaradi, chunki "qo'ng'iroq
qilsa bo'ladimi" deb so'rayotgan chaqiruvchiga **"yo'q"** javobi ishlatiladigan
bo'lishi kerak.

**Bir tomonlama qattiqroq.** Deklaratsiya **yo'q** bo'lishi mumkin (noma'lum
raqam → `consented: false` + sabab). **Buzuq** deklaratsiya bo'lishi mumkin
**emas**: o'qib bo'lmaydigan status, noma'lum maqsad, o'qib bo'lmaydigan muddat —
konfiguratsiya vaqtida **raise** qiladi, chunki rozilik yozuvidagi xato
**"granted" deb o'qilmasligi** kerak. *"Yozuv topmadik"* va *"yozuvni o'qiy
olmadik"* — ikki xil fakt, va faqat bittasiga tayanish xavfsiz.

### 3. Raqam — bitta raqam

Har bir taqqoslash `normalise()` dan o'tadi:

| Katak | Natija |
|---|---|
| `+998 90 123 45 67` | `998901234567` |
| `998-90-123-45-67` | `998901234567` |
| `(998) 90 123 45 67` | `998901234567` |
| `''`, `None` | `''` (raqam emas) |
| `Ali Valiyev` | `''` |
| `1234567890123456` | `''` (juda uzun) |
| `True` | `''` (boolean ham emas) |
| `901234567` | `901234567` ≠ `998901234567` |

Oxirgi qator **muhim**: P11 asset yo'lida **segment, prefiks emas** qoidasi bor
edi; bu yerda ham xuddi shunday — `startswith` qisqa raqam roziligi bilan
**notanish odamga qo'ng'iroq qilardi**.

### 4. Maqsad — yopiq lug'at, va registr bo'yicha

Maqsad **registr bo'yicha** deklaratsiya qilinadi, ustundan **o'qilmaydi**:

    PURPOSES = ('service', 'delivery', 'payment', 'support', 'marketing')

Sabab: o'zini qayta belgilay oladigan qator marketing qo'ng'iroqni service deb
belgilab, darvozadan **o'tib ketardi**. Yetkazib berish haqida rozilik —
aksiya haqida rozilik **emas**; ikkalasini bitta bayroqqa yig'ish qonuniy
ro'yxatni noqonuniy qilishning usuli.

### 5. Nima **yo'q**, ataylab

* **Dialer yo'q.** Hech qanday tool provayderga qo'ng'iroq qila olmaydi. Test
  modul yuzasida `dial`/`place`/`call`/`connect`/`send` nomlarini **rad etadi**.
* **Audio yuzasi yo'q** (2.1 ga qarang).
* **Shaxs bo'yicha ko'rsatkich yo'q.** `agent_column`, `operator_column`,
  `by_agent`, `rank`, `rating`, `score`, `leaderboard` — hech biri yo'q; test va
  probe buni **o'lchaydi**. Qo'ng'iroq **soni** — telefon liniyasi haqidagi fakt;
  agent bo'yicha qo'ng'iroq soni — ko'rib chiqishda ishlatiladigan raqam, va
  platforma qo'ng'iroq nima uchun uzun yoki qisqa bo'lganini **bilmaydi**.
* **Yozish yo'li yo'q.** Uchala tool ham `read` ro'yxatida.

### 6. O'lchangan xossalar (`scripts/probe_telephony_consent.py`, 7 bo'lim)

| Bo'lim | Nima o'lchanadi |
|---|---|
| 1 | Raqam normalizatsiyasi; prefiks mos emas |
| 2 | Rozilik fakti + sababi; rad etish **nol** ortiqcha xarajat; maqsad kesishmaydi |
| 3 | Roziliksiz chiqish qo'ng'iroq **ko'rsatiladi**, yashirilmaydi va sanaladi |
| 4 | Audio hech qachon yetib kelmaydi; lokator ushlanadi; so'z o'tadi |
| 5 | Shaxs baholanmaydi; davomiylik yig'indisi `timed_count` bilan birga |
| 6 | Darvoza strukturaviy: uzilish — tinch kun **emas** |
| 7 | Konfiguratsiya xatoni rad etadi: har xil mutatsiya sinovdan o'tadi |

O'lchangan xossa soni: **69**, hammasi PASS.

### 7. Testlar (`runtime_tests/test_telephony.py`, 57 test)

| Guruh | Nima tekshiriladi |
|---|---|
| Ro'yxatga olish | 3 tool read; idempotent; yozish/dial yuzasi yo'q; sxema qo'shimcha kalitni rad etadi |
| Raqam | Ikki imlo teng; bo'sh/ism/uzun/boolean raqam emas; prefiks mos emas |
| Rozilik darvozasi | Grant/yo'q/chiqarib olingan/muddati o'tgan; maqsad kesishmaydi; allowlist |
| Yozib olish | Alohida rozilik; yo'qligi "yozuv yo'q" emas |
| Hodisa o'quvchi | Roziliksiz chiqish belgilanadi; kiruvchi darvozadan o'tmaydi; raqamsiz qator `skipped` |
| Audio chegarasi | Media yo'li chiqmaydi; `withheld_outcomes`; so'z saqlanadi; import yuzasi toza |
| Shaxs yo'q | Hech qanday ko'rinish shaxs kalitini chiqarmaydi; `by_agent` yo'q |
| Oyna/chegara | Oyna, limit, `truncated`, `null` davomiylik |
| Konfiguratsiya | Xato ustun nomi, noma'lum kalit/maqsad, o'qib bo'lmaydigan status/muddat rad etiladi |
| Rad etishlar | Tool agentda yo'q; ulanish ruxsat etilmagan; uzilish — tinch kun emas; sir/URL chiqmaydi |

### 8. Konfiguratsiya

`config/telephony.example.json` — **ikki registr** (service + delivery) va bitta
rozilik registri. `scripts/check_telephony_example.py` misolni **modulning o'z
validatori** bilan tekshiradi va uchta mutatsiyani rad etishini o'lchaydi:
noma'lum maqsad, ishlatib bo'lmaydigan allowlist raqami, shaxs-shaklidagi kalit.

`config/agent-capabilities.example.yaml` ga `ops.telephony` agenti qo'shildi
(tool'lar: `telephony.consent`, `telephony.call_events`, `telephony.summary`,
`sheets.rows`; `ladder: human_assisted`). `check_capabilities_example.py`:
`registry tools: 82`, `unknown: []`.

### 9. Baseline

**`Ran 1991 tests` → `failures=1, errors=149, skipped=1`** — baza **o'n ikkinchi
marta aynan o'zgarmadi** (1934 → 1988 → 1991 = **+57** yangi test). Yagona yiqilish —
Windows-only `test_all_files_private`.

#### 9.1 A bosqichning o'z auditida topilgan nuqsonlar

Audit probe orqali **ikki nuqson** topdi — ikkisi ham normalizatsiya
chegarasida, va ikkisi ham shu bosqichda tuzatildi:

1. **Raqamlar bir-biriga qulab tushishi.** `normalise()` raqamdan boshqa hamma
   narsani olib tashlar edi, ya'ni `'https://a/rec-9.wav'` → `'9'`. Aloqasiz
   olti xil satr bitta `'9'` ga aylanardi. **Tuzatish:** `MIN_NUMBER_DIGITS = 7`
   — pastdan chegara.
2. **Fayl nomidagi raqam raqamga aylanishi.** `'https://cdn.example/rec-998901234567.wav'`
   → `'998901234567'` — to'liq yaroqli raqam, lekin u media fayl nomi, qarshi
   tomon emas. **Tuzatish:** `_LOCATOR_RE` mos kelsa umuman rad etiladi; `tel:`
   esa (raqamning qonuniy imlosi) olib tashlanadi, rad etilmaydi.

Sabab bir xil: **matndan raqamdan boshqa hamma narsani olib tashlash xavfli
tomonga yo'qotadi.** Bu ikkisi `test_telephony.py` da regressiya testi va
`probe_telephony_consent.py` 1-bo'limida o'lchangan xossa sifatida qulflandi.

### 10. Ochiq qolgan (ataylab)

**B bosqich:** chiqish navbati, throughput (provayderning soniyalik chegarasi),
saqlash muddati siyosati va rad etish chegarasi.
**C bosqich:** eskalatsiya yetkazish P6 koordinatori orqali — **ikkinchi
bildirishnoma yo'li ochilmaydi** — plus probe va hujjat.

Quyidagilar **bu bosqichda ham, umuman platformada ham** qilinmaydi:

* audio saqlash — **hech qachon** (bu sozlama emas, qoida);
* qo'ng'iroq qilish — tool emas, koordinator ishi (C bosqichda ham);
* STT/TTS live sifat o'lchovi — real audio kerak, shuning uchun `NOT_RUN`;
* yozuvni saqlash muddati — operator majburiyati, platforma uni **yuritmaydi**.

`production_release` **NO_GO** bo'lib qoladi: live provayder acceptance yo'q.


---

<a id="v05r"></a>

## V05R — Chiqish navbati, tezlik shifti va saqlash oynasi (`telephony_outbound`, B bosqich)

**Sana:** 2026-09-20
**Blok:** `telephony_outbound` (P14 / T4) — **B bosqich: navbat + tezlik + saqlash**
**Holat:** **QURILDI VA O'LCHANDI** — lokal kontrakt darajasida. Live provayder
acceptance yo'q, `production_release` **NO_GO** bo'lib qoladi.
**Yangi tool:** 2 ta, **hammasi read** — `telephony.queue`, `telephony.retention`.
`build_registry()` **82 → 84**.
**Yangi test:** `TelephonyStageBTests` — **24** yangi test metodi (ota-sinfdan
57 tasini meros oladi; `test_telephony.py` jami **137** test).
**Probe:** `scripts/probe_telephony_consent.py` 8-bo'lim — **29** yangi o'lchangan
xossa (jami **69 → 98**), hammasi PASS.
**Config:** `config/telephony.example.json` ga `throughput` + `retention_days`;
`check_telephony_example.py` **12 → 22** PASS.

---

### 1. B bosqich nimani qo'shadi va nimani qo'shmaydi

A bosqich uchta savolga javob berdi: **kimga qo'ng'iroq qilish mumkin**, **qaysi
maqsad uchun**, **yozib olish mumkinmi**. Lekin haqiqiy chiqish kampaniyasi uchun
yana uchta fakt kerak:

1. **Kim qo'ng'iroq qilinishi mumkin** — navbat. A bosqichda bu
   `call_events` ning yon mahsuloti edi; B da u **o'z ko'rinishi** bo'ldi, chunki
   keyingi dialer **aynan shuni** o'qiydi.
2. **Qanchalik tez** — tezlik shifti. Bu havaskorlik savoli emas: cheklanmagan
   chiqish ro'yxati — bu soniyada yuzlab qo'ng'iroq, va hech bir inson uni
   kuzata olmaydi.
3. **Yozuv qancha yashaydi** — saqlash oynasi. VO-07 saqlashni **operatorning
   majburiyati** deb yozgan, mahsulot funksiyasi emas.

**Va B bosqich hech narsani qo'shmaydi:** yangi ma'lumot yo'li yo'q, yangi
transport yo'q, dialer yo'q. U A bosqich o'qigan **o'sha registrni** o'qiydi,
o'sha rozilik darvozasidan o'tadi va **hech qachon qo'ng'iroq qilmaydi**.

### 2. Uchta qoida, uchtasi ham o'lchangan

#### 2.1 Navbat — bu "nima qilish mumkin", "nima qilindi" emas

`telephony.queue` faqat `direction == 'outbound'` qatorlarni ko'rsatadi. Kiruvchi
qo'ng'iroq navbat elementi **emas**: biz uni qilishni tanlamaganmiz, demak
rejalashtiradigan narsa yo'q va hurmat qilinadigan sur'at ham yo'q.

**Roziliksiz qator ham ko'rsatiladi** — `consented: false` va sababi bilan. Uni
yashirish operatorga ro'yxat toza degan yolg'on ishonch berardi; ko'rsatish esa
uni tuzatishga yo'naltiradi. `blocked_count` shu qatorlarni sanaydi.

#### 2.2 Tezlik shifti — **ijro etiladi**, e'lon qilinmaydi

`throughput.per_window` / `window_seconds` e'lon qilinadi. Shift **kodda
qo'llaniladi**: agar o'qish sur'at ko'tara oladiganidan ko'p chaqiriladigan qator
topsa, ortig'i `over_capacity` da **sanaladi** va **chaqiriladigan deb
ko'rsatilmaydi**.

Nega bu muhim: **haddan ortiq ko'rsatilgan navbat qisqasidan yomonroq.** Qisqa
ro'yxatning davosi — kutish. Haddan ortiq ro'yxatning davosi — yo'q.

**Shift e'lon qilinmasa — sig'im nolga teng.** Sur'atini hal qilmagan operator
hech kimga qo'ng'iroq qilishni ham hal qilmagan. Bu xavfsiz standart: yo'q
ruxsatni "cheksiz ruxsat" deb o'qish mumkin emas.

**`limit` faqat ko'rsatiladigan elementlarni cheklaydi, baholashni emas.** Har bir
qator baribir tekshiriladi, shuning uchun `matched` va `over_capacity` **butun
oynani** tasvirlaydi. "900 ta chaqiriladigan raqam bor" va "mana ulardan 50 tasi"
— ikki xil fakt, va faqat ikkinchisini olgan chaqiruvchi **noto'g'ri raqamga**
qarab sur'atni sozlaydi.

#### 2.3 Saqlash oynasi — hisobot, harakat emas

`telephony.retention` **hech narsani o'chirmaydi**. `deleted` doim `0`. Platforma
qo'ng'iroq yozuvini **hech qachon o'zi yo'q qilmaydi**: yo'q qilish qarori ham,
amali ham **huquqiy asosni ko'rsata oladigan yagona tomon — operator** bilan
qoladi.

Har bir qator **aynan bitta** holatda bo'ladi:

| Holat | Ma'nosi |
|---|---|
| `in_window` | e'lon qilingan oyna o'tmagan — yozuv turadi |
| `past_window` | oyna o'tgan — operator yo'q qilishi kerak |
| `unaged` | qator sanasini o'qib bo'lmaydi — oyna qo'llanmaydi |

**`unaged` ataylab uchinchi holat**, ikkisidan biriga qo'shilmaydi. Aks holda
sana **xato yozilgani uchun** yozuv "o'z-o'zidan muddati o'tgan" bo'lib
yo'q qilinardi. Bu modul **xato yozuv asosida hech narsani yo'q qilmaydi**.

### 3. Audit topgan nuqson: N ta raqam N marta o'qish

B bosqichning o'z auditi **haqiqiy nuqson** topdi. Dastlab `queue()` har bir
chiqish qatori uchun `consent()` ni chaqirardi, va `consent()` har safar rozilik
registrini **qaytadan o'qirdi**. Ya'ni 50 qatorli navbat — 50 marta provider
GET.

**Nima uchun bu bug:** u to'g'ri natija berardi va testdan o'tardi. Lekin u
`limit` bilan birga **yashirin O(N) kuchaytirish** edi: navbat o'sgan sari
xarajat ham o'sardi, va provayderning rate limiti shu yerda buzilardi.

**Tuzatish:** `_index` — ichki tikuv. `consent()` ga **bir marta o'qilgan**
indeks uzatiladi; u tool yuzasining bir qismi emas (tool handler hech qachon
uzatmaydi), shuning uchun bitta raqam haqidagi bitta savol avvalgidek yangi
o'qish oladi. `_scan()` (ya'ni `call_events`/`summary`) ham xuddi shunday
tuzatildi.

Probe buni **o'lchaydi**: 12 qatorli navbat ham, 60 qatorli ham — **2 hop**
(bitta qo'ng'iroq o'qishi + bitta rozilik o'qishi).

### 4. Saqlash oynasi majburiy — VO-07

Consent registri e'lon qilingan tenant **demak odamlar haqida shaxsiy ma'lumot
saqlaydi**, va oynasiz ma'lumot egasi VO-07 taqiqlagan tanlovni **jimgina**
qilgan. Shuning uchun `retention_days` **configure vaqtida** rad etiladi — standart
qiymat berilmaydi.

Oyna **cheklangan va musbat**: `1..3650`. "Abadiy saqlash" — bu saqlash siyosati
emas, bu **siyosatning yo'qligi**.

### 5. O'lchangan xossalar (probe, 8-bo'lim)

| Guruh | Nima o'lchanadi |
|---|---|
| Navbat | Faqat chiqish; kiruvchi navbatga tushmaydi (raqam bir marta ko'rinadi); roziliksiz qator ko'rinadi va sanaladi |
| Shift | Sur'at sig'im sifatida qaytadi; 5 chaqiriladigan qator 2'lik sur'atda 3 ta `over_capacity`; `limit` elementni cheklaydi, baholashni emas |
| Sur'atsizlik | E'lon qilinmagan sur'atda sig'im 0 va har bir chaqiriladigan qator ortiqcha |
| Saqlash | Uch holat ajratiladi; `deleted == 0`; o'qib bo'lmaydigan sana uzoq kelajakda ham `unaged`; oyna kun bilan o'lchanadi (30 kun o'tadi, 29 kun o'tmaydi) |
| Kuchaytirish | 60 qatorli navbat 12 qatorli bilan **bir xil hop** sarflaydi (≤2) |
| Dial yo'q | Navbat elementida dial/sip/audio/.wav/transcript yo'q |
| Config | Nolga teng sur'at, bir daqiqadan qisqa oyna, noma'lum kalit, cheksiz/o'nol saqlash rad etiladi |

O'lchangan xossa soni: **98** (69 + 29), hammasi PASS.

### 6. Testlar (`TelephonyStageBTests`)

`TelephonyStageBTests(TelephonyTests)` — **meros ataylab**: bu testlar **o'sha**
engine, fixture va skriptlangan hop ustida ishlaydi, shuning uchun B bosqich
A bosqichning invariantini buzsaydi, **ota-sinf testi yiqiladi**.

24 yangi test metodi: navbat faqat chiqish; kiruvchi hech qachon element emas;
roziliksiz qator ko'rinadi; navbat dial qilmaydi; sur'at qaytadi; sur'atsizlikda
sig'im 0; ortiqcha sanaladi; `limit` baholashni cheklamaydi; chegara ortidagi
limit rad etiladi; **N qator N marta o'qimaydi**; audio yuzasi yo'q; shaxs
baholanmaydi; oyna hurmat qilinadi; saqlash uch holati; **saqlash hech qachon
o'chirmaydi**; o'qib bo'lmaydigan sana tasodifan muddati o'tgan bo'lmaydi; oyna
chegarasi kunlarda; oyna majburiy; sur'at/saqlash rad etishlari; ikki yangi tool
read-only va ro'yxatga olingan.

### 7. Baseline

**`Ran 2071 tests` → `failures=1, errors=149, skipped=1`** — baza **o'n uchinchi
marta aynan o'zgarmadi** (1991 → 2071 = **+80**). Yagona yiqilish — Windows-only
`test_all_files_private`.

### 8. Ochiq qolgan (ataylab)

**C bosqich:** eskalatsiya yetkazish P6 koordinatori orqali — **ikkinchi
bildirishnoma yo'li ochilmaydi** — plus probe va hujjat.

**B bosqichda ochilmagan:** haqiqiy dialer, SIP, codec, concurrent slot
boshqaruvi, provayder rate-limit retry, yozuvni haqiqatda o'chirish (bu operator
amali), budget reservation bilan bog'lash (VO-06 — `usage_budget` moduli
mavjud va keyingi bosqichda ulanadi).


---

<a id="v05s"></a>

## V05S — Eskalatsiya yetkazish P6 koordinatori orqali (`telephony_outbound`, C bosqich)

**Sana:** 2026-09-20
**Blok:** `telephony_outbound` (P14 / T4) — **C bosqich: eskalatsiya yetkazish**
**Holat:** **QURILDI VA O'LCHANDI** — lokal kontrakt darajasida. Live provayder
acceptance yo'q, `production_release` **NO_GO** bo'lib qoladi.
**Yangi tool:** **yo'q** — ataylab. Mavjud `escalation.preview` kengaytirildi
(`source` argumenti).
**Modul o'zgarishi:** `escalation.py` — `source` tanlovchi (`workforce` |
`telephony`), `SOURCE_TOOLS`, `_telephony_items`, digestga ikki yangi fakt.
**Yangi test:** `test_escalation.py` **45 → 106** (61 meros + 16 yangi metod).
**Probe:** `scripts/probe_escalation_boundary.py` — **21 → 36** o'lchangan xossa
(9-bo'limga chiqdi), hammasi PASS.
**Config:** `config/escalation.example.yaml` + `check_escalation_example.py`
ikkala manbani o'rgatadi.

---

### 1. C bosqichning butun maqsadi — **ikkinchi yo'l ochmaslik**

B bosqich chiqish navbatini berdi. Tabiiy keyingi qadam: *"navbatda
qo'ng'iroqlar tayyor bo'lganda menejerga xabar berish"*. Va eng oson — va eng
xato — yo'l: telephony moduliga o'z `telegram.send` chaqiruvini qo'shish.

**Nega bu xato:** platformada **allaqachon** koordinator bor (`escalation.py`,
P6) — va u bitta qiyin savolga javob beradi: **"bu allaqachon xabar qilinganmi?"**
U buni ledger, dedup kaliti, cooldown va compare-and-set bilan qiladi. Ikkinchi
yuboruvchi — bu **o'sha savolga ikkinchi javob**, ya'ni ikkinchi dedup, ikkinchi
cooldown va **o'chirishni unutib qoldiradigan ikkinchi narsa**.

Shuning uchun C bosqich **yangi yuboruvchi qo'shmaydi**. U koordinatorga
**manba tanlovchi** qo'shadi:

| Manba | O'quvchi | Yuboruvchi |
|---|---|---|
| `workforce` (default) | `workforce.workload` | `telegram.send` |
| `telephony` | `telephony.queue` | **`telegram.send`** |

Ikkalasi ham **o'sha** ledger, **o'sha** dedup va **o'sha** cooldown bilan
ishlaydi. `DELIVERY_TOOLS` o'zgarmadi — u hamon **faqat** `{telegram.send}`.

### 2. Bu strukturaviy o'lchanadi, aytilmaydi

Probe 7-bo'limda da'voni **o'lchaydi**, docstringni o'qimaydi:

- `DELIVERY_TOOLS == {telegram.send}` — yangi manba uni **o'zgartirmadi**;
- modul `notify`/`deliver`/`send`/`post`/`publish`/`push`/`alert` nomli
  **hech qanday** oshkora yuzani eksport qilmaydi → ikkinchi yuboruvchi **yo'q**;
- telefoniya eskalatsiyasi **o'sha** `telegram.send` orqali **o'sha** qabul
  qiluvchiga yetib boradi;
- va uni **o'sha** `p_escalation` ledger ushlab turadi.

### 3. Telefoniya digesti — haddan ortiq da'vo qilmaydi

Navbatning o'zi `over_capacity` va `blocked_count` ni aytadi (B bosqich).
Eskalatsiya digesti ham ularni **aytishi shart**, aks holda menejer
*"12 ta qo'ng'iroq tayyor"* deb o'qib, **ulardan 10 tasi rad etilganini** va
faqat 2 tasi qonuniy bajarilishini **bilmaydi** — bu navbat rad etadigan
haddan ortiq da'vonning **o'sha o'zi**, faqat xabar ichida.

Shuning uchun digest qo'shadi:

```
Rozilik darvozasi rad etgan raqamlar: 2 — qo'ng'iroq qilinmaydi
E'lon qilingan sur'atdan ortiq (bajarilmaydi): 1 — sig'im 0
Ro'yxat to'liq emas (truncated) — bu qism, hammasi emas
```

Va **faqat rozilik darvozasidan o'tgan** qatorlar element bo'ladi. Rad etilgan
raqam **hech qachon** "chaqiriladigan" sifatida ro'yxatga tushmaydi.

### 4. Muddati o'tganlik (staleness) qoidasi manbaga bog'liq

`max_age_days` **kechikkan ishni** chiqarib tashlaydi: bir yil kechikkan vazifa
endi eskalatsiya emas, bu ma'lumot sifati muammosi.

**Raqamga qo'llanilsa — bu bug bo'lardi.** Raqamni yoshi bo'yicha chiqarib
tashlash **qonuniy qo'ng'iroqni jimgina bekor qilardi**, va "eski" —
qo'ng'iroqning qonuniyligini to'xtatadigan sabab emas.

Shuning uchun qoida **faqat `workforce`** uchun ishlaydi, va probe buni **ikki
tomondan** o'lchaydi:

- `telephony` + `max_age_days=1` + 400 kunlik sana → **baribir yuboriladi**;
- `workforce` + o'sha shart → **yuborilmaydi** (qoida sizib o'tmagan).

### 5. Dedup kaliti — raqam, hech qachon xodim

`workforce` da kalit — **ish ob'ekti** (`person, task, due`), xodim emas.
`telephony` da "ish ob'ekti" — **raqam va maqsad**. Bir raqam bir marta
eskalatsiya qilinadi, keyin **terminal**: kunlik sikl uni qayta yubormaydi.

Va **hech qanday shaxs nomlanmaydi**: navbat raqam va maqsadni tashiydi, hech
qachon operatorni emas. Probe element qatorlarini tekshiradi (digest ostidagi
"platforma xodimni baholamaydi" **disclaimer'i** — bu **kafolat**, qoidabuzarlik
emas, va probe shuni **ataylab** ajratadi).

### 6. O'lchangan xossalar (probe 7–9-bo'lim)

| Guruh | Nima o'lchanadi |
|---|---|
| Bitta yuboruvchi | `DELIVERY_TOOLS` o'zgarmagan; ikkinchi yuboruvchi eksport qilinmagan; telefoniya o'sha qabul qiluvchi va o'sha ledgerga boradi |
| Haddan ortiq da'vo yo'q | Rad etilgan raqamlar soni aytiladi; rad etilgan raqam chaqiriladigan deb ro'yxatlanmaydi; sur'at ortig'i aytiladi |
| Yoshi bo'yicha chiqarish | Eski sana qonuniy raqamni chiqarib tashlamaydi; `workforce` da staleness **saqlanadi** |

O'lchangan xossa soni: **36** (21 + 15), hammasi PASS.

### 7. Testlar

`EscalationTelephonySourceTests(EscalationTests)` — **meros ataylab**: bu
testlar **o'sha** engine, **o'sha** skriptlangan `telegram.send`, **o'sha**
ledger va **o'sha** dedup ustida ishlaydi. C bosqich parallel yuboruvchi
kiritgan bo'lsa, **ota-sinfning** yetkazish testlari yiqilardi.

16 yangi metod: manba qabul qilinadi va eslab qolinadi; `workforce` default;
noma'lum manba rad etiladi; manba o'quvchisi yo'q agent rad etiladi; yuboruvchi
yo'q agent rad etiladi; eskalatsiya **o'sha** yuboruvchi orqali boradi;
roziliksiz raqam chaqiriladigan deb eskalatsiya qilinmaydi; digest rad etilgan
sonni aytadi; digest sur'at ortig'ini aytadi; bir raqam ikki marta
eskalatsiya qilinmaydi; **shaxs nomlanmaydi**; **raqam yoshi bo'yicha
chiqarilmaydi**; **`workforce` staleness sizib o'tmagan**; uzilish tinch kun
emas; preview manbani aytadi; preview noma'lum manbani rad etadi.

### 8. Baseline

**`Ran 2132 tests` → `failures=1, errors=149, skipped=1`** — baza **o'n
to'rtinchi marta aynan o'zgarmadi** (2071 → 2132 = **+61**). Yagona yiqilish —
Windows-only `test_all_files_private`.

### 9. P14 yakuni

Uch bosqich ham **lokal kontrakt darajasida** tugadi:

| Bosqich | Nima | Holat |
|---|---|---|
| A | Hodisalar + rozilik darvozasi | **LOCAL_CONTRACT_TESTED** |
| B | Navbat + sur'at + saqlash | **LOCAL_CONTRACT_TESTED** |
| C | Eskalatsiya P6 orqali | **LOCAL_CONTRACT_TESTED** |

**Uch bosqichda ham hech qachon audio saqlanmadi, dialer yozilmadi va ikkinchi
bildirishnoma yo'li ochilmadi.**

**Ochiq qolgan (ataylab):** haqiqiy dialer, SIP, codec, concurrent slot
boshqaruvi, provayder rate-limit retry, yozuvni haqiqatda o'chirish (operator
amali), VO-06 budget reservation'ni `usage_budget` ga ulash, STT/TTS live sifat
(`NOT_RUN` — real provayder audiosi kerak) va live provayder acceptance.
`production_release` **NO_GO** bo'lib qoladi.


---

<a id="v05t"></a>

## V05T — Inventory read bloki (`inventory_moysklad`), P4

Sana: 2026-09-20. Source **v0.5**. Holat: **LOCAL_CONTRACT_TESTED**.
`production_release` — **NO_GO** (o'zgarmadi).

---

### 1. Nima uchun bu blok, va nima uchun tracker'ning o'z javobidan **boshqa** javob

`BACKLOG.json` bu blokni shunday yozgan edi:

```json
{ "id": "inventory_moysklad", "status": "TODO",
  "remaining": "Typed MoySklad adapter: product, stock, price, cost.
                Customer named this twice for the seller price problem" }
```

Ya'ni reja **typed MoySklad adapteri** yozish edi. Blok boshqa yo'l bilan hal qilindi va
sabab muhim:

`business_graph` **allaqachon** MoySklad'ni `connectors.read` orqali o'qiydi va
**allaqachon** qaysi tizim kelishmasligini hal qiladi. MoySklad uchun alohida typed
adapter yozish `crm_gateway` yo'lini ochib, **o'sha savolga ikkinchi javob** bergan
bo'lardi: ikkinchi manba ro'yxati, ikkinchi ustuvorlik qoidasi, ikkinchi ziddiyat
formati. Platformaning o'z intizomi shunga qarshi — `business_graph` docstring'i:
*"The graph does not merge them"*.

Shuning uchun blok **yangi transport, yangi ulanish va yangi registr qo'shmaydi**.

---

### 2. Nima qurildi

`api-python/platform_runtime/inventory.py` — 4 ta tool, hammasi **read**:

| Tool | Vazifa |
|---|---|
| `inventory.product` | Bitta mahsulotning to'liq ko'rinishi: nom, narx, tannarx, qoldiq, birlik, valyuta |
| `inventory.stock` | Qoldiqlar — bitta mahsulot yoki katalog bo'ylab |
| `inventory.price` | Narxlar, **har biri manbasi bilan** |
| `inventory.margin` | Narx − tannarx, yoki **sababi nomi bilan** rad |

Har bir o'qish `business_graph.resolve()` orqali o'tadi. Natijada registr
deklaratsiyasi, A1 allowlist, agent tool ruxsati va connection allowlist
**o'zgarishsiz** amal qiladi.

Konfiguratsiya **rol → grafik atributi** ko'rinishida (`config/inventory.example.json`):

```json
"inventory": {
  "entity": "product",
  "identity": "sku",
  "attributes": { "price": "price", "cost": "cost", "stock": "stock_quantity" }
}
```

Rol — **ma'no** ("birlik narxi"), atribut — grafikdagi nom. Grafik atributini tool
argumentida to'g'ridan-to'g'ri nomlash modelga qaysi raqamni "narx" deb atashni
tanlashga ruxsat bergan bo'lardi.

---

### 3. Yetti chegara (hammasi **o'lchangan**, tasvirlanmagan)

`scripts/probe_inventory_boundary.py` — **68 xossa, 6 bo'lim, 68 pass, 0 fail**.

1. **Narx manbasi bilan keladi.** Manbasiz raqam — sotuvchi himoya qila olmaydigan raqam.
2. **Ziddiyat ko'rsatiladi.** `report` (default) da **hech narsa tanlanmaydi**; `primary_wins`
   da operator tartibi tanlaydi va ziddiyat **baribir** ko'rsatiladi. Bu PRD ning o'z qoidasi:
   *"buxgalter 1C raqamini bilishi kerak, sotuvchi MoySklad raqamini"* — ikkalasi to'g'ri.
3. **Blank / nol / o'qib bo'lmaydigan — uch xil fakt.** `nan`/`inf` rad etiladi (haqiqiy
   Python float'lari, aks holda "o'qilgan" bo'lib o'tib ketardi).
4. **Foyda faqat ikkala o'qilgan qiymatdan.** Aks holda `None` **va sabab nomi**.
5. **Ombor soni "sotuvga tayyor" degani emas.** Strukturaviy: chiqishda `available`
   kaliti yo'q. Platforma rezerv, karantin va hisobga olinmagan omborni bilmaydi.
6. **Odam bo'yicha ko'rsatkich yo'q** (strukturaviy).
7. **Yozish yo'li umuman yo'q** — manba matnida o'lchangan, docstring'da emas.

---

### 4. Ultra-audit: o'zimning kodimda topilgan 2 ta haqiqiy nuqson

#### Nuqson A — "hisoblab bo'lmadi" va "tanlashdan bosh tortdim" bir xil ko'rinardi

`report` siyosatida narx ziddiyatli bo'lsa `business_graph` `selected: null` qaytaradi —
to'g'ri. Lekin `margin` undan `None` bo'lib qolardi va bu **"foyda hisoblab bo'lmadi"**
bo'lib ko'rinardi. Aslida:

* narx **o'qilgan** (ikki xil, ikkalasi ham to'g'ri),
* tannarx **o'qilgan**,
* platforma shunchaki narx **tanlashdan bosh tortgan**.

Ikkalasi operatorni **ikki xil joyga** yuboradi: biri registrlarni solishtirishga, ikkinchisi
**bitta konfiguratsiya qatorini** yozishga.

**Tuzatish:** `_reason()` 4 xil sababni ajratadi (`not_declared` / `blank` / `unread` /
`conflict`) va chiqishda ikkita **alohida** ro'yxat:

```json
{ "not_computable": ["not_declared"],
  "margin_blocked_by_conflict": ["conflict"] }
```

Test buni qulflaydi: `test_a_conflict_is_not_reported_as_missing_data` va
`test_declaring_a_priority_computes_the_margin_that_report_refused` — ya'ni tuzatish
**konfiguratsiya**, ko'proq ma'lumot emas, va u **ishlaydi**.

#### Nuqson B — mos kelmaydigan kalit

Bitta mahsulot yo'li `margin_reasons`, katalog yo'li `not_computable` qaytarardi. Ikkalasi
`not_computable` ga keltirildi.

---

### 5. `source_priority` jimgina yo'qolishi tuzatildi

Blokni ochishda topilgan nuqson: `business_graph.graph_config()` da

```python
unknown = set(raw) - {'conflict_policy', 'entities', 'source_priority'}
```

`'source_priority'` **butun repoda faqat shu bitta qatorda** uchraydi. Ya'ni kalit
whitelist'da — operator uning borligini biladi (noma'lum kalit `raise` qiladi) — va
**hech kim o'qimaydi**: `graph_config` faqat `conflict_policy` va `entities` qaytarardi,
shuning uchun `resolve`/`search`/`conflicts`/`explain`/`timeline` unga **ko'r** edi.

Bu o'sha modulning o'z docstring'iga zid: *"Configuration errors raise instead of being
skipped"* — lekin **jimgina yo'qolgan kalit xato emas, undan yomoni**.

**Tuzatish:**
* `graph_config()` endi `source_priority` ni natijaga soladi;
* PRD §6.3 dagi **per-attribute** shakl ishlaydi (`{primary, fallback}`, yoki bare string);
* top-level va entity-darajali jadvallar birlashadi — **entity ustun**;
* **jimgina no-op sinfi rad etiladi:** deklaratsiyalangan manba entity'da yo'q bo'lsa yoki
  attribute hech bir manbada map qilinmagan bo'lsa → `ValueError`;
* `explain()` endi `attribute_priority` + `priority_source` qaytaradi — *"bu raqam qayerdan
  keldi?"* savoliga **qaysi qoida hal qilgani** bilan javob beradi;
* latent hazard yopildi: `priority` e'lon qilinmasa tartib endi **deterministik**
  (`sorted`), **JSON kalit tartibiga bog'liq emas** — JSON'da tartib semantik emas.

---

### 6. O'lchovlar

| Ko'rsatkich | Oldin | Keyin |
|---|---|---|
| Offline to'plam | 2132 | **2229** (+97) |
| `failures / errors / skipped` | 1 / 149 / 1 | **1 / 149 / 1** (o'zgarmadi) |
| `test_business_graph.py` | 98 | **157** |
| `test_inventory.py` | — | **38** |
| Probe xossalari | 26 | **68** (0 fail) |
| Registry tool'lari | 84 | **88** |
| `known_tool_names` | 85 | **89** |

Baseline **o'zgarmadi**: yagona `failure` — Windows-only `test_all_files_private`
(`0 != 63`, POSIX mode bitlari Windows'da mavjud emas). +97 = 38 + 59.

---

### 7. Ochiq qolgan (ataylab)

* **MoySklad live acceptance** — yo'q. `describe_crm.live_verified` **False** bo'lib qoladi.
  Jonli token, haqiqiy `api.moysklad.ru`, sahifalash va rate-limit tekshirilmagan.
* **UI** — yo'q.
* Tracker'ning o'z `next_source_block`i (`telephony_live_acceptance`) **o'zgarmadi** — u
  jonli provayder ishi.

### 8. Bu blok NIMA qilmaydi

* Narxni **belgilamaydi**. Bu tijoriy qaror.
* Qoldiqni **"sotuvga tayyor"** ga aylantirmaydi.
* Rezerv qilmaydi, kotirovka bermaydi, buyurtma bermaydi, chegirma hisoblamaydi.
* Odam bo'yicha ko'rsatkich chiqarmaydi.
